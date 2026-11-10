"""Tests für die Wowhead-Metadatenquelle und die Relevanz-Sortierung der Suche."""
import io
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import api, db  # noqa: E402
from ahanalytics.sources import wowhead  # noqa: E402

XML = """<?xml version="1.0" encoding="UTF-8"?><wowhead><item id="279960"><name><![CDATA[Leitstein]]></name>
<level>4</level><quality id="1">Gewöhnlich</quality><class id="0"><![CDATA[Verbrauchbar]]></class>
<subclass id="8"><![CDATA[Andere (Verbrauchbar)]]></subclass><icon displayId="0">inv_misc_rune_06</icon></item></wowhead>"""
NOT_FOUND = '<?xml version="1.0" encoding="UTF-8"?><wowhead><error>Item not found!</error></wowhead>'


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class WowheadTest(unittest.TestCase):
    def test_parse(self):
        d = wowhead.parse_xml(XML)
        self.assertEqual(d["name"], "Leitstein")
        self.assertEqual((d["quality"], d["class_id"], d["subclass_id"], d["item_level"]), (1, 0, 8, 4))
        self.assertEqual(d["class_name"], "Verbrauchbar")
        self.assertEqual(d["icon"], "inv_misc_rune_06")
        self.assertIsNone(wowhead.parse_xml(NOT_FOUND))

    def test_fetch_metadata(self):
        con = db.connect(":memory:")
        db.ensure_items(con, [279960, 1, 2])
        urls = []

        def opener(req, timeout=None):
            urls.append(req.full_url)
            if "item=279960" in req.full_url:
                return Resp(XML.encode())
            if "item=1&" in req.full_url:
                return Resp(NOT_FOUND.encode())
            raise urllib.error.URLError("offline")

        n = wowhead.fetch_item_metadata(con, pause=0, opener=opener, log=lambda *a: None)
        self.assertEqual(n, 1)
        self.assertTrue(all("/forever/de/item=" in u for u in urls))
        st = dict(con.execute("SELECT id, meta_status FROM items").fetchall())
        self.assertEqual(st, {279960: "ok", 1: "missing", 2: "pending"})  # Netzfehler -> später erneut
        self.assertEqual(con.execute("SELECT name FROM items WHERE id=279960").fetchone()[0], "Leitstein")

    def test_stops_when_blocked(self):
        con = db.connect(":memory:")
        db.ensure_items(con, [1, 2, 3])
        calls = []

        def opener(req, timeout=None):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 403, "blocked", {}, None)

        self.assertEqual(wowhead.fetch_item_metadata(con, pause=0, opener=opener, log=lambda *a: None), 0)
        self.assertEqual(len(calls), 1)  # sofort aufgehört
        self.assertEqual(con.execute("SELECT COUNT(*) FROM items WHERE meta_status='pending'").fetchone()[0], 3)


class RelevanceTest(unittest.TestCase):
    def test_prefix_first(self):
        con = db.connect(":memory:")
        hid = db.upsert_house(con, "t", "x", "Haus")
        names = {1: "Großes Kupferrohr", 2: "Kupfererz", 3: "Schwerer Kupferbarren"}
        for iid, name in names.items():
            db.upsert_item(con, iid, "ok", name=name)
        db.write_snapshot(con, hid, 1000, "t", {i: {"min": 10 * i, "quantity": 1000 // i} for i in names})
        db.refresh_summary(con)
        r = api.search(con, {"q": "kupfer", "sort": "relevance"})
        self.assertEqual(r["items"][0]["name"], "Kupfererz")


if __name__ == "__main__":
    unittest.main()


class BilingualSearchTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        hid = db.upsert_house(self.con, "t", "x", "Haus")
        db.upsert_item(self.con, 765, "ok", name="Silberblatt", name_en="Silverleaf")
        db.upsert_item(self.con, 3356, "ok", name="Königsblut", name_en="Kingsblood")
        db.upsert_item(self.con, 8836, "ok", name="Arthas' Tränen", name_en="Arthas' Tears")
        db.write_snapshot(self.con, hid, 1000, "t", {i: {"min": 10, "quantity": 5} for i in (765, 3356, 8836)})
        db.refresh_summary(self.con)

    def names(self, q):
        return [x["name"] for x in api.search(self.con, {"q": q, "sort": "relevance"})["items"]]

    def test_german_and_english(self):
        self.assertEqual(self.names("Silberblatt"), ["Silberblatt"])
        self.assertEqual(self.names("silverleaf"), ["Silberblatt"])
        self.assertEqual(self.names("Silver"), ["Silberblatt"])
        self.assertEqual(self.names("kingsblood"), ["Königsblut"])

    def test_umlauts_and_apostrophes(self):
        self.assertEqual(self.names("konigsblut"), ["Königsblut"])
        self.assertEqual(self.names("KÖNIGS"), ["Königsblut"])
        self.assertEqual(self.names("arthas tranen"), ["Arthas' Tränen"])
        self.assertEqual(self.names("arthas tears"), ["Arthas' Tränen"])

    def test_fold(self):
        self.assertEqual(db.fold("Größe Äxte"), "grosse axte")
        self.assertIsNone(db.fold(None))
