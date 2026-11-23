"""Tests für die Kategorie-Filter in der Server-Suche (Spiegel: tests/category_filters.test.js)."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import api, db, recipes  # noqa: E402


class CategoryFiltersTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        recipes.ensure_schema(self.con)
        self.h = db.upsert_house(self.con, "battlenet", "eu-111", "Normal Allianz", faction="Allianz")
        self.ts = int(time.time()) // 3600 * 3600
        for iid, name, en, q, cid, sub, lvl in (
                (2770, "Kupfererz", "Copper Ore", 1, 7, 7, None),
                (2840, "Kupferbarren", "Copper Bar", 1, 7, 7, None),
                (2589, "Leinenstoff", "Linen Cloth", 1, 7, 5, None),
                (7000, "Rezeptlose Ware", "Recipeless Goods", 2, 7, 11, None),
                (8001, "Pläne: Testdolch", "Plans: Test Dagger", 3, 9, 4, None),
                (8002, "Rezept: Minusmacher", "Recipe: Loss Maker", 3, 9, 6, None),
                (9001, "Testdolch", "Test Dagger", 3, 2, 15, 20),
                (9002, "Minusmacher", "Loss Maker", 1, 0, 0, None)):
            db.upsert_item(self.con, iid, name=name, name_en=en, quality=q,
                           class_id=cid, subclass_id=sub, required_level=lvl)
        db.write_snapshot(self.con, self.h, self.ts, "test", {
            2770: {"min": 100, "quantity": 50},
            2840: {"min": 300, "quantity": 20},
            2589: {"min": 40, "quantity": 100},
            8001: {"min": 50000, "quantity": 1},
            9001: {"min": 10000, "quantity": 3},
            9002: {"min": 50, "quantity": 5},
        })
        self.con.execute("UPDATE snapshots SET complete=1 WHERE house_id=?", (self.h,))
        db.refresh_summary(self.con)
        self.con.executemany("INSERT INTO professions(id, name, type, sort) VALUES (?, ?, 'PRIMARY', ?)",
                             [(164, "Schmiedekunst", 1), (171, "Alchimie", 2), (186, "Bergbau", 3)])
        self.con.executemany(
            "INSERT INTO recipes(id, profession_id, category, sort, name, item_id, item_name, qty_min, qty_max)"
            " VALUES (?, ?, '', ?, ?, ?, ?, 1, 1)",
            [(1, 164, 1, "Testdolch", 9001, "Testdolch"),
             (2, 171, 1, "Minusmacher", 9002, "Minusmacher"),
             (3, 186, 1, "Kupfer verhütten", 2840, "Kupferbarren")])
        self.con.executemany("INSERT INTO recipe_reagents(recipe_id, item_id, item_name, quantity) VALUES (?, ?, ?, 1)",
                             [(1, 2840, "Kupferbarren"), (2, 2770, "Kupfererz"), (3, 2770, "Kupfererz")])

    def tearDown(self):
        self.con.close()

    def ids(self, params):
        return sorted(x["item_id"] for x in api.search(self.con, params)["items"])

    def test_prof_filter(self):
        # Beruf = Item ist Zutat oder Ergebnis eines Rezepts des Berufs
        self.assertEqual(self.ids({"house": str(self.h), "class": "7", "prof": "171"}), [2770])
        self.assertEqual(self.ids({"house": str(self.h), "class": "7", "prof": "164"}), [2840])
        self.assertEqual(self.ids({"house": str(self.h), "class": "7", "prof": "186"}), [2770, 2840])
        # ODER über mehrere Berufe
        self.assertEqual(self.ids({"house": str(self.h), "class": "7", "prof": "164,171"}), [2770, 2840])
        # Items ohne Rezeptbezug (7000) und unbeteiligte (2589) fallen weg
        self.assertNotIn(7000, self.ids({"house": str(self.h), "prof": "164"}))
        # Berufs-Filter über Klassen hinweg (Rezept-Produkte eingeschlossen)
        self.assertEqual(self.ids({"house": str(self.h), "prof": "171"}), [2770, 9002])

    def test_listed_filter(self):
        self.assertEqual(self.ids({"house": str(self.h), "class": "7", "listed": "1"}), [2589, 2770, 2840])
        self.assertEqual(self.ids({"house": str(self.h), "class": "7"}), [2589, 2770, 2840, 7000])

    def test_level_filter(self):
        self.assertEqual(self.ids({"house": str(self.h), "class": "2", "min_lvl": "10"}), [9001])
        self.assertEqual(self.ids({"house": str(self.h), "class": "2", "min_lvl": "30"}), [])
        self.assertEqual(self.ids({"house": str(self.h), "class": "2", "max_lvl": "19"}), [])
        self.assertEqual(self.ids({"house": str(self.h), "class": "2", "min_lvl": "19", "max_lvl": "20"}), [9001])
        # Ohne bekannte Stufen im Datenbestand wirkt der Filter nicht (alter Export)
        self.assertEqual(self.ids({"house": str(self.h), "class": "7", "min_lvl": "10"}), [2589, 2770, 2840, 7000])

    def test_margin_filter(self):
        # 8001 lehrt "Testdolch": Verkauf 10000 * 0,95 - 300 = 9200 > 0
        # 8002 lehrt "Minusmacher": Verkauf 50 * 0,95 - 100 < 0
        self.assertEqual(self.ids({"house": str(self.h), "margin": "1"}), [8001])
        self.assertEqual(self.ids({"house": str(self.h), "class": "9", "margin": "1"}), [8001])
        self.assertEqual(self.ids({"house": str(self.h), "class": "9"}), [8001, 8002])

    def test_combined_and_bad_params(self):
        self.assertEqual(self.ids({"house": str(self.h), "class": "7", "prof": "186", "quality": "1"}), [2770, 2840])
        self.assertEqual(self.ids({"house": str(self.h), "class": "7", "prof": "186", "quality": "2"}), [])
        with self.assertRaises(api.BadRequest):
            api.search(self.con, {"prof": "abc"})

    def test_profession_items_helper(self):
        pmap = recipes.profession_items(self.con)
        self.assertEqual(pmap[2770], {171, 186})
        self.assertEqual(pmap[2840], {164, 186})
        self.assertEqual(pmap[9001], {164})
        self.assertNotIn(7000, pmap)


if __name__ == "__main__":
    unittest.main()
