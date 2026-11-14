"""Belegte deutsche Aliase aus Wowhead Classic (verified_aliases).

Deckt ab: Nur Items ohne bestätigten Namen werden geprüft, eingeklammerte
Classic-Namen gelten nicht als Beleg, Namen gleich dem englischen erzeugen
kein Alias, Drosselung bricht sauber ab, und die Suche findet Items über
den belegten Alias – ohne translation_missing zu verstecken.
"""
import io
import os
import sys
import time
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import api, db, itemnames  # noqa: E402
from ahanalytics.sources import wowhead  # noqa: E402


def xml(item_id, name):
    return (f'<?xml version="1.0" encoding="UTF-8"?><wowhead><item id="{item_id}">'
            f"<name><![CDATA[{name}]]></name><level>38</level><quality id=\"3\">Selten</quality>"
            f"<class id=\"4\"><![CDATA[Rüstung]]></class><subclass id=\"3\"><![CDATA[Kettenrüstung]]></subclass>"
            f"<icon displayId=\"0\">inv_gauntlets_19</icon></item></wowhead>")


NOT_FOUND = '<?xml version="1.0" encoding="UTF-8"?><wowhead><error>Item not found!</error></wowhead>'


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def seed(con, rows):
    for iid, name, en in rows:
        db.upsert_item(con, iid, name=name, name_en=en, quality=3, class_id=4)


class ClassicAliasTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")

    def tearDown(self):
        self.con.close()

    def rows(self):
        return {r[0]: r for r in self.con.execute(
            "SELECT item_id, status, alias, source, source_url FROM verified_aliases")}

    def test_verified_name_becomes_search_alias_only(self):
        seed(self.con, [(10331, "[Scarlet Gauntlets]", "Scarlet Gauntlets")])

        def opener(req, timeout=None):
            self.assertIn("/classic/de/item=10331", req.full_url)
            return Resp(xml(10331, "Scharlachrote Stulpen").encode())

        n = wowhead.fetch_classic_aliases(self.con, pause=0, opener=opener, log=lambda *a: None)
        self.assertEqual(n, 1)
        r = self.rows()[10331]
        self.assertEqual((r[1], r[2], r[3]), ("ok", "Scharlachrote Stulpen", "wowhead_classic"))
        self.assertEqual(r[4], "https://www.wowhead.com/classic/de/item=10331")
        row = itemnames.enrich({"item_id": 10331, "name": "[Scarlet Gauntlets]", "name_en": "Scarlet Gauntlets",
                                "verified_alias": "Scharlachrote Stulpen", "catalog_aliases": None})
        self.assertEqual(row["aliases"], ["Scharlachrote Stulpen"])
        self.assertTrue(row["translation_missing"])  # Alias hebt den Hinweis nicht auf
        self.assertEqual(row["name"], "Scarlet Gauntlets")  # kein erfundener bestätigter Name
        # Zweiter Lauf: bereits geprüfte Items werden nicht erneut abgefragt.
        calls = []
        wowhead.fetch_classic_aliases(self.con, pause=0, opener=lambda req, timeout=None: calls.append(req) or Resp(b""), log=lambda *a: None)
        self.assertEqual(calls, [])

    def test_bracketed_and_unknown_classic_names_are_not_evidence(self):
        seed(self.con, [(647, "[Destiny]", "Destiny"), (276398, "[Scarlet Malice]", "Scarlet Malice")])

        def opener(req, timeout=None):
            if "item=647" in req.full_url:
                return Resp(xml(647, "[Destiny]").encode())  # auch Classic ohne Übersetzung
            return Resp(NOT_FOUND.encode())

        n = wowhead.fetch_classic_aliases(self.con, pause=0, opener=opener, log=lambda *a: None)
        self.assertEqual(n, 0)
        self.assertEqual(self.rows()[647][:2], (647, "missing"))
        self.assertEqual(self.rows()[276398][:2], (276398, "missing"))
        row = itemnames.enrich({"item_id": 647, "name": "[Destiny]", "name_en": "Destiny", "verified_alias": None})
        self.assertEqual(row["aliases"], [])

    def test_name_equal_to_english_is_acknowledged_without_alias(self):
        seed(self.con, [(999, "[Hammer]", "Hammer")])
        n = wowhead.fetch_classic_aliases(self.con, pause=0,
                                          opener=lambda req, timeout=None: Resp(xml(999, "Hammer").encode()),
                                          log=lambda *a: None)
        self.assertEqual(n, 1)
        self.assertEqual(self.rows()[999][:3], (999, "ok", None))

    def test_items_with_confirmed_names_are_not_queried(self):
        seed(self.con, [(3304, "Brackwasserstulpen", "Brackwater Gauntlets")])
        calls = []
        n = wowhead.fetch_classic_aliases(self.con, pause=0,
                                          opener=lambda req, timeout=None: calls.append(req) or Resp(b""),
                                          log=lambda *a: None)
        self.assertEqual((n, calls), (0, []))

    def test_network_error_stays_unmarked_and_block_stops(self):
        seed(self.con, [(1, "[One]", "One"), (2, "[Two]", "Two"), (3, "[Three]", "Three")])

        def opener(req, timeout=None):
            if "item=1" in req.full_url:
                raise urllib.error.URLError("offline")
            raise urllib.error.HTTPError(req.full_url, 429, "throttled", {}, None)

        n = wowhead.fetch_classic_aliases(self.con, pause=0, opener=opener, log=lambda *a: None)
        self.assertEqual(n, 0)
        self.assertEqual(self.rows(), {})  # Fehler: nicht markiert, später erneut; Block: Abbruch

    def test_search_finds_item_by_verified_alias(self):
        con = db.connect(":memory:")
        try:
            hid = db.upsert_house(con, "battlenet", "eu-110", "Allianz", faction="Allianz")
            seed(con, [(10331, "[Scarlet Gauntlets]", "Scarlet Gauntlets"),
                       (3304, "Brackwasserstulpen", "Brackwater Gauntlets")])
            ts = int(time.time()) // 60 * 60
            db.write_snapshot(con, hid, ts, "test", {10331: {"min": 100, "quantity": 2}})
            db.refresh_summary(con)
            con.execute("""INSERT INTO verified_aliases(item_id,status,alias,source,source_url,checked_at)
                           VALUES (10331,'ok','Scharlachrote Stulpen','wowhead_classic',
                                   'https://www.wowhead.com/classic/de/item=10331', 1)""")
            for q in ("Scharlachrote Stulpen", "scharlachrote"):
                r = api.search(con, {"q": q, "house": str(hid)})
                self.assertEqual([x["item_id"] for x in r["items"]], [10331], q)
            # Teilwort-Suche trifft weiterhin auch andere Items mit demselben Wortbestandteil.
            r = api.search(con, {"q": "Stulpen", "house": str(hid)})
            self.assertEqual({x["item_id"] for x in r["items"]}, {10331, 3304})
            item = api.item(con, 10331, {"house": str(hid)})
            self.assertIn("Scharlachrote Stulpen", item["item"]["aliases"])
            # Englische Suche und bestätigte deutsche Namen bleiben unverändert.
            self.assertEqual(api.search(con, {"q": "Scarlet", "house": str(hid)})["items"][0]["item_id"], 10331)
            self.assertEqual(api.search(con, {"q": "Brackwasser", "house": str(hid)})["items"][0]["item_id"], 3304)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
