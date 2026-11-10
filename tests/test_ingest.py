"""Tests für Ingest: Aggregation, Auctionator-Import, Battle.net-Verarbeitung, Summary."""
import io
import json
import urllib.error
import urllib.parse
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import cbor, db  # noqa: E402
from ahanalytics.aggregate import aggregate_listings, stats_from_levels, weighted_percentile  # noqa: E402
from ahanalytics.sources import auctionator, battlenet  # noqa: E402


def lua_bytes_literal(b: bytes) -> bytes:
    """Kodiert Bytes wie WoW als Lua-String (Steuerzeichen als \\ddd)."""
    out = bytearray(b'"')
    for c in b:
        if c in (ord('"'), ord("\\")) or c < 32 or c == 127:
            out += b"\\" + str(c).encode()
        else:
            out.append(c)
    out += b'"'
    return bytes(out)


def make_auctionator_file(realms: dict) -> bytes:
    parts = [b'AUCTIONATOR_CONFIG = {\n["debug"] = false,\n}\n',
             b'AUCTIONATOR_PRICE_DATABASE = {\n["__dbversion"] = 8,\n']
    for realm, content in realms.items():
        parts.append(b'["' + realm.encode() + b'"] = ' + lua_bytes_literal(cbor.dumps(content)) + b",\n")
    parts.append(b"}\n")
    return b"".join(parts)


class AggregateTest(unittest.TestCase):
    def test_weighted_percentile(self):
        levels = [(10, 1), (20, 8), (30, 1)]
        self.assertEqual(weighted_percentile(levels, 0.1), 10)
        self.assertEqual(weighted_percentile(levels, 0.5), 20)
        self.assertEqual(weighted_percentile(levels, 1.0), 30)
        self.assertIsNone(weighted_percentile([], 0.5))

    def test_stats(self):
        s = stats_from_levels([(100, 2), (300, 2), (100, 1)])
        self.assertEqual(s["min"], 100)
        self.assertEqual(s["max"], 300)
        self.assertEqual(s["quantity"], 5)
        self.assertEqual(s["median"], 100)  # 3 von 5 Stück kosten 100
        self.assertEqual(s["mean"], 180)    # (300 + 600) / 5
        self.assertEqual(s["auctions"], 3)
        self.assertEqual(s["_levels"], [[100, 3], [300, 2]])

    def test_aggregate_listings_ignores_invalid(self):
        stats, ladders = aggregate_listings([(1, 50, 1), (1, 40, 3), (2, None, 1), (2, 0, 1), (3, 10, 0)])
        self.assertEqual(set(stats), {1})
        self.assertEqual(stats[1]["min"], 40)
        self.assertEqual(ladders[1], [[40, 3], [50, 1]])


class AuctionatorImportTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")

    def test_import(self):
        d0 = 2500
        content = {
            "2770": {"m": 20, "h": {str(d0): 25, str(d0 + 1): 22}, "l": {str(d0): 18},
                     "a": {str(d0): 100, str(d0 + 1): 80}},
            "g:12640:63": {"m": 5000, "h": {str(d0): 6000}, "l": [], "a": {str(d0): 1}},
            "g:12640:66": {"m": 4000, "h": {str(d0): 7000}, "l": {str(d0): 4000}, "a": {str(d0): 2}},
            "p:39:1:1": {"m": 1, "h": {str(d0): 1}, "l": [], "a": {}},
            "version": 2,
        }
        data = make_auctionator_file({"PvE": content, "PvP": {"version": 2}})
        res = auctionator.import_bytes(self.con, data, region="eu")
        self.assertEqual(res.houses, 1)
        self.assertEqual(res.items, 2)          # Pet übersprungen, Varianten zusammengeführt
        self.assertEqual(res.snapshots, 2)
        ts0 = auctionator.SCAN_DAY_0 + d0 * 86400
        r = self.con.execute("SELECT * FROM prices WHERE item_id=2770 AND ts=?", (ts0,)).fetchone()
        self.assertEqual((r["min"], r["max"], r["quantity"]), (18, 25, 100))
        g = self.con.execute("SELECT * FROM prices WHERE item_id=12640").fetchone()
        self.assertEqual((g["min"], g["max"], g["quantity"]), (4000, 7000, 3))
        s = self.con.execute("SELECT * FROM item_summary WHERE item_id=2770").fetchone()
        self.assertEqual(s["last_min"], 22)
        self.assertEqual(s["prev24_min"], 18)
        h = self.con.execute("SELECT * FROM houses").fetchone()
        self.assertEqual((h["source"], h["granularity"], h["ext_id"]), ("auctionator", "day", "PvE"))

    def test_reimport_is_idempotent(self):
        data = make_auctionator_file({"PvE": {"2770": {"m": 1, "h": {"2500": 5}, "l": [], "a": {"2500": 3}}}})
        auctionator.import_bytes(self.con, data)
        auctionator.import_bytes(self.con, data)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM prices").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)

    def test_rejects_other_file(self):
        with self.assertRaises(ValueError):
            auctionator.import_bytes(self.con, b'FOO = { ["x"] = 1 }')

    def test_parse_key(self):
        self.assertEqual(auctionator.parse_key("123"), 123)
        self.assertEqual(auctionator.parse_key("g:456:70"), 456)
        self.assertEqual(auctionator.parse_key("gr:789:1:2"), 789)
        self.assertIsNone(auctionator.parse_key("p:39:1:1"))

    def test_real_file_if_present(self):
        paths = auctionator.find_savedvariables()
        if not paths:
            self.skipTest("Keine echte Auctionator.lua auf diesem Rechner")
        realms = auctionator.decode_realms(open(paths[0], "rb").read())
        self.assertIsInstance(realms, dict)


class FakeResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class BattleNetTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        self.calls = []
        lm = "Tue, 10 Nov 2026 11:00:00 GMT"
        routes = {
            "/token": {"access_token": "T", "expires_in": 3600},
            "/data/wow/ruleset/index": {"rulesets": [{"id": 1}, {"id": 2}]},
            "/data/wow/ruleset/1/auctions/": {"auctions": [
                {"id": 110, "name": "Alliance Auction House", "key": {}},
                {"id": 112, "name": "Blackwater Auction House", "key": {}}]},
            "/data/wow/ruleset/1/auctions/110": {"id": 110, "auctions": [
                {"id": 1, "item": {"id": 12640, "bonus_lists": [1]}, "buyout": 5000000, "quantity": 1},
                {"id": 2, "item": {"id": 12640}, "buyout": 4000000, "quantity": 1},
                {"id": 3, "item": {"id": 12640}, "bid": 100, "quantity": 1}]},
            "/data/wow/ruleset/1/auctions/110/commodities": {"auctions": [
                {"id": 9, "item": {"id": 2770}, "quantity": 200, "unit_price": 20},
                {"id": 10, "item": {"id": 2770}, "quantity": 50, "unit_price": 35}]},
            "/data/wow/ruleset/1/auctions/112": {"id": 112},  # ohne "auctions"-Feld
            "/data/wow/ruleset/1/auctions/112/commodities": {"auctions": []},
            "/data/wow/item/2770": {"name": "Kupfererz", "quality": {"type": "COMMON"},
                                    "item_class": {"id": 7, "name": "Handwerkswaren"},
                                    "item_subclass": {"id": 7, "name": "Metall & Stein"},
                                    "level": 1, "sell_price": 5},
            "/data/wow/media/item/2770": {"assets": [{"key": "icon", "value": "https://x/icon.jpg"}]},
        }

        def opener(req, timeout=None):
            url = req.full_url
            path = urllib.parse.urlparse(url).path
            self.calls.append(url)
            if path in routes:
                return FakeResponse(routes[path], {"Last-Modified": lm})
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)

        self.client = battlenet.BattleNetClient("id", "secret", region="eu", opener=opener)

    def test_collect(self):
        res = battlenet.collect(self.con, self.client, log=lambda *a: None)
        self.assertEqual(res.houses, 2)
        self.assertTrue(any("namespace=dynamic-forever-eu" in c for c in self.calls))
        houses = {r["ext_id"]: dict(r) for r in self.con.execute("SELECT * FROM houses")}
        self.assertEqual(houses["eu-110"]["faction"], "Allianz")
        self.assertEqual(houses["eu-112"]["faction"], "Neutral")
        hid = houses["eu-110"]["id"]
        gear = self.con.execute("SELECT * FROM prices WHERE house_id=? AND item_id=12640", (hid,)).fetchone()
        self.assertEqual((gear["min"], gear["max"], gear["auctions"]), (4000000, 5000000, 2))
        ore = self.con.execute("SELECT * FROM prices WHERE house_id=? AND item_id=2770", (hid,)).fetchone()
        self.assertEqual((ore["min"], ore["median"], ore["quantity"]), (20, 20, 250))
        self.assertEqual(ore["mean"], round((200 * 20 + 50 * 35) / 250))
        item = self.con.execute("SELECT * FROM items WHERE id=2770").fetchone()
        self.assertEqual((item["name"], item["quality"], item["is_commodity"]), ("Kupfererz", 1, 1))
        self.assertEqual(item["icon"], "https://x/icon.jpg")
        missing = self.con.execute("SELECT meta_status FROM items WHERE id=12640").fetchone()[0]
        self.assertEqual(missing, "missing")
        lad = self.con.execute("SELECT data FROM ladders WHERE item_id=2770").fetchone()[0]
        self.assertEqual(json.loads(lad), [[20, 200], [35, 50]])

    def test_same_snapshot_skipped(self):
        battlenet.collect(self.con, self.client, fetch_metadata=False, log=lambda *a: None)
        res = battlenet.collect(self.con, self.client, fetch_metadata=False, log=lambda *a: None)
        self.assertEqual(res.snapshots, 0)
        self.assertEqual(res.skipped, 2)

    def test_listing_unit_price(self):
        rows = list(battlenet.listings_from_payload(
            {"auctions": [{"item": {"id": 5}, "buyout": 300, "quantity": 3}]}, False))
        self.assertEqual(rows, [(5, 100, 3)])


if __name__ == "__main__":
    unittest.main()
