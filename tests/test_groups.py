"""Tests für Marktgruppen (Alle AHs / Neutral), Hausbezeichnungen und Item-Tooltips."""
import json
import os
import sys
import unittest
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import api, db  # noqa: E402
from ahanalytics.sources import battlenet  # noqa: E402
from test_ingest import FakeResponse  # noqa: E402

T0 = 1_790_000_000 - 1_790_000_000 % 3600
QUIET = lambda *a: None  # noqa: E731


def _house(con, num, faction):
    return db.upsert_house(con, "battlenet", f"eu-{num}", f"Haus {num}", region="eu", faction=faction)


class TypicalMinTest(unittest.TestCase):
    def test_lockangebot_wird_ignoriert(self):
        stats = {1: {"min": 5}}
        # großes Haus 100c, mittleres 110c, kleines Haus mit Lockangebot 5c
        db.apply_typical_min(stats, [{1: (100, 500)}, {1: (110, 300)}, {1: (5, 10)}])
        self.assertEqual(stats[1]["min"], 100)

    def test_item_nur_in_einem_haus(self):
        stats = {2: {"min": 7}}
        db.apply_typical_min(stats, [{1: (100, 5)}, {2: (7, 1)}])
        self.assertEqual(stats[2]["min"], 7)


class GroupBackfillTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        a, h, n = _house(self.con, 110, "Allianz"), _house(self.con, 113, "Horde"), _house(self.con, 116, "Neutral")
        for k in range(3):
            ts = T0 + k * 3600
            db.write_snapshot(self.con, a, ts, "t", {1: {"min": 100, "median": 120, "mean": 125, "max": 200, "quantity": 300, "auctions": 3}})
            db.write_snapshot(self.con, h, ts + 60, "t", {1: {"min": 80, "median": 90, "mean": 95, "max": 150, "quantity": 100, "auctions": 2}})
            db.write_snapshot(self.con, n, ts, "t", {1: {"min": 400, "median": 400, "mean": 400, "max": 400, "quantity": 5, "auctions": 1}})
        self.alle = db.upsert_group_house(self.con, "battlenet", "eu", "alle")
        self.neutral = db.upsert_group_house(self.con, "battlenet", "eu", "neutral")

    def test_members(self):
        self.assertEqual(len(db.group_members(self.con, self.alle)), 2)
        self.assertEqual(len(db.group_members(self.con, self.neutral)), 1)

    def test_backfill(self):
        self.assertEqual(db.build_group_snapshots(self.con, self.alle, log=QUIET), 3)
        r = self.con.execute("SELECT * FROM prices WHERE house_id=? ORDER BY ts", (self.alle,)).fetchall()
        self.assertEqual(len(r), 3)
        self.assertEqual((r[0]["quantity"], r[0]["auctions"], r[0]["max"]), (400, 5, 200))
        self.assertEqual(r[0]["min"], 100)                       # gewichteter Median der Tiefstpreise
        self.assertEqual(r[0]["mean"], round((125 * 300 + 95 * 100) / 400))
        self.assertEqual(r[0]["ts"], T0 + 60)                    # jüngster Zeitpunkt der Stunde
        self.assertEqual(db.build_group_snapshots(self.con, self.alle, log=QUIET), 0)  # idempotent
        n = self.con.execute("SELECT min FROM prices WHERE house_id=?", (self.neutral,)).fetchall()
        self.assertEqual(n, [])                                  # Neutral noch nicht gebaut
        db.build_group_snapshots(self.con, self.neutral, log=QUIET)
        self.assertEqual(self.con.execute("SELECT MAX(min) FROM prices WHERE house_id=?", (self.neutral,)).fetchone()[0], 400)

    def test_houses_labels_and_default(self):
        db.build_group_snapshots(self.con, self.alle, log=QUIET)
        db.build_group_snapshots(self.con, self.neutral, log=QUIET)
        db.refresh_summary(self.con)
        hs = api.houses(self.con, {})
        kinds = [h["kind"] for h in hs["houses"]]
        self.assertEqual(kinds[:2], ["alle", "neutral"])
        self.assertEqual(hs["default"], self.alle)
        by = {h["ext_id"]: h for h in hs["houses"]}
        self.assertEqual(by["eu-110"]["verbund"], "A")
        self.assertEqual(by["eu-113"]["verbund"], "A")
        self.assertEqual(by["eu-110"]["short"], "Allianz A")
        info = api.item(self.con, 1, {})
        self.assertEqual({h["kind"] for h in info["houses"]}, {"alle", "neutral", "haus"})


class CollectGroupsTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        lm = "Tue, 10 Nov 2026 11:00:00 GMT"
        houses = [{"id": 110, "name": "Auktionshaus der Allianz"}, {"id": 113, "name": "Auktionshaus der Horde"},
                  {"id": 116, "name": "Auktionshaus der Schwarzmeerräuber"}]
        comm = {
            110: [{"id": 1, "item": {"id": 2770}, "quantity": 500, "unit_price": 100},
                  {"id": 2, "item": {"id": 2770}, "quantity": 1, "unit_price": 5}],      # Lockangebot
            113: [{"id": 3, "item": {"id": 2770}, "quantity": 300, "unit_price": 110}],
            116: [{"id": 4, "item": {"id": 2770}, "quantity": 20, "unit_price": 400}],
        }
        routes = {"/token": {"access_token": "T", "expires_in": 3600},
                  "/data/wow/ruleset/index": {"rulesets": [{"id": 1}]},
                  "/data/wow/ruleset/1/auctions/": {"auctions": houses}}
        for h in houses:
            routes[f"/data/wow/ruleset/1/auctions/{h['id']}"] = {"auctions": []}
            routes[f"/data/wow/ruleset/1/auctions/{h['id']}/commodities"] = {"auctions": comm[h["id"]]}

        def opener(req, timeout=None):
            path = urllib.parse.urlparse(req.full_url).path
            if path in routes:
                return FakeResponse(routes[path], {"Last-Modified": lm})
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

        self.client = battlenet.BattleNetClient("id", "secret", region="eu", opener=opener)

    def test_pools(self):
        battlenet.collect(self.con, self.client, fetch_metadata=False, log=QUIET)
        g = {r["ext_id"]: r["id"] for r in self.con.execute("SELECT * FROM houses")}
        alle = self.con.execute("SELECT * FROM prices WHERE house_id=?", (g["eu-alle"],)).fetchone()
        self.assertEqual(alle["quantity"], 801)
        self.assertEqual(alle["min"], 100)      # typischer Tiefstpreis, nicht das 5c-Lockangebot
        self.assertEqual(alle["median"], 100)   # exakt aus allen Einzelauktionen
        neu = self.con.execute("SELECT * FROM prices WHERE house_id=?", (g["eu-neutral"],)).fetchone()
        self.assertEqual((neu["min"], neu["quantity"]), (400, 20))
        res = battlenet.collect(self.con, self.client, fetch_metadata=False, log=QUIET)
        self.assertEqual(res.snapshots, 0)      # gleicher Stand: nichts doppelt
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM snapshots WHERE house_id=?",
                                          (g["eu-alle"],)).fetchone()[0], 1)


class TooltipTest(unittest.TestCase):
    def test_lines(self):
        pi = {
            "binding": {"name": {"de_DE": "Wird beim Anlegen gebunden"}},
            "inventory_type": {"type": "HEAD", "name": "Kopf"},
            "item_subclass": {"name": "Platte"},
            "armor": {"display": {"display_string": "565 Rüstung"}},
            "stats": [{"display": {"display_string": "+18 Stärke"}},
                      {"is_equip_bonus": True, "display": {"display_string": "+20 Trefferwert"}}],
            "requirements": {"level": {"display_string": "Benötigt Stufe 56"}},
            "spells": [{"description": "Benutzen: Etwas."}],
        }
        lines = battlenet.tooltip_lines(pi)
        self.assertEqual(lines[0], {"t": "Wird beim Anlegen gebunden", "c": "w"})
        self.assertEqual(lines[1], {"t": "Kopf", "c": "w", "r": "Platte"})
        self.assertIn({"t": "+20 Trefferwert", "c": "g"}, lines)
        self.assertEqual(lines[-1], {"t": "Benutzen: Etwas.", "c": "g"})

    def test_parse_item_details(self):
        out = battlenet.parse_item({"name": "Helm", "required_level": 56, "is_stackable": False,
                                    "inventory_type": {"type": "HEAD", "name": "Kopf"},
                                    "preview_item": {"inventory_type": {"type": "HEAD", "name": "Kopf"}}})
        self.assertEqual((out["required_level"], out["slot"]), (56, "Kopf"))
        self.assertEqual(json.loads(out["details"])["lines"][0]["t"], "Kopf")


if __name__ == "__main__":
    unittest.main()
