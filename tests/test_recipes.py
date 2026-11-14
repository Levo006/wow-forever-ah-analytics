"""Tests für Berufe/Rezepte: Parsing, Duplikate, Speicherung, Abruf, Export (+ JS-Logik über Node)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import api, db, export, recipes  # noqa: E402
from ahanalytics.sources import battlenet  # noqa: E402
from test_ingest import FakeResponse  # noqa: E402

QUIET = lambda *a: None  # noqa: E731
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NS = "https://eu.api.blizzard.com/x?namespace=static-1.60.1_70399-forever-eu"


def _recipe(rid, name, item, reagents, qty=None):
    p = {"_links": {"self": {"href": NS}}, "id": rid, "name": name,
         "reagents": [{"reagent": {"id": i, "name": n}, "quantity": q} for i, n, q in reagents],
         "crafted_quantity": qty or {"value": 1.0}}
    if item:
        p["crafted_item"] = {"id": item[0], "name": item[1]}
    return p


RECIPES = {
    3324: _recipe(3324, "Kupfer verhütten", (2840, "Kupferbarren"), [(2770, "Kupfererz", 1)]),
    1600: _recipe(1600, "Bronze verhütten", (2841, "Bronzebarren"),
                  [(2840, "Kupferbarren", 1), (3576, "Zinnbarren", 1)], {"value": 2.0}),
    2662: _recipe(2662, "Kupferner Kettengürtel", (2851, "Kupferner Kettengürtel"), [(2840, "Kupferbarren", 6)]),
    4289: _recipe(4289, "Armschiene - Ausdauer", None, [(10940, "Seltsamer Staub", 2)]),
    53851: _recipe(53851, "Kupfer verhütten", (2840, "Kupferbarren"), [(2770, "Kupfererz", 1)]),  # Duplikat
    99: {"id": 99, "name": None, "reagents": [{"reagent": {"id": 1}, "quantity": 1}]},            # ohne Namen
}


def fake_client(recipes_map=RECIPES, fail=()):
    tiers = {
        186: [("Verhüttete Barren", [3324, 1600, 99])],
        164: [("Gürtel", [2662])],
        333: [("Armschienen", [4289])],
        2933: [(None, [53851])],
    }
    routes = {"/token": {"access_token": "T", "expires_in": 3600},
              "/data/wow/profession/index": {"_links": {"self": {"href": NS}}, "professions": [
                  {"id": 186, "name": "Bergbau"}, {"id": 164, "name": "Schmiedekunst"},
                  {"id": 333, "name": "Verzauberkunst"}, {"id": 2933, "name": None}]}}
    for pid, cats in tiers.items():
        routes[f"/data/wow/profession/{pid}"] = {"type": {"type": "PRIMARY"}, "skill_tiers": [{"id": pid * 10}]}
        routes[f"/data/wow/profession/{pid}/skill-tier/{pid * 10}"] = {
            "categories": [{"name": c, "recipes": [{"id": r} for r in rs]} for c, rs in cats]}
    for rid, p in recipes_map.items():
        if rid not in fail:
            routes[f"/data/wow/recipe/{rid}"] = p

    def opener(req, timeout=None):
        path = urllib.parse.urlparse(req.full_url).path
        if path in routes:
            return FakeResponse(routes[path], {})
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
    return battlenet.BattleNetClient("id", "secret", region="eu", opener=opener)


class ParseTest(unittest.TestCase):
    def test_parse(self):
        r = recipes.parse_recipe(RECIPES[1600], 186, "Barren", 3)
        self.assertEqual((r["item_id"], r["qty_min"], r["qty_max"]), (2841, 2.0, 2.0))
        self.assertEqual([x["item_id"] for x in r["reagents"]], [2840, 3576])

    def test_quantity_range_and_bad_entries(self):
        self.assertEqual(recipes.parse_quantity({"minimum": 1.0, "maximum": 4.0}), (1.0, 4.0))
        self.assertEqual(recipes.parse_quantity(None), (1.0, 1.0))
        self.assertIsNone(recipes.parse_recipe(RECIPES[99], 1, None, 0))
        self.assertIsNone(recipes.parse_recipe({"id": 5, "name": "x", "reagents": []}, 1, None, 0))
        # doppelte Zutat wird zusammengezählt, ungültige Mengen ignoriert
        p = _recipe(7, "x", (1, "a"), [(5, "b", 1), (5, "b", 2), (6, "c", 0)])
        r = recipes.parse_recipe(p, 1, None, 0)
        self.assertEqual([(x["item_id"], x["quantity"]) for x in r["reagents"]], [(5, 3)])

    def test_enchant_without_item(self):
        r = recipes.parse_recipe(RECIPES[4289], 333, "Armschienen", 0)
        self.assertIsNone(r["item_id"])

    def test_dedupe(self):
        profs = [{"id": 186, "name": "Bergbau"}, {"id": 2933, "name": None}, {"id": 5, "name": None}]
        rs = [recipes.parse_recipe(RECIPES[3324], 186, None, 0), recipes.parse_recipe(RECIPES[53851], 2933, None, 1),
              recipes.parse_recipe(RECIPES[2662], 5, None, 2), recipes.parse_recipe(RECIPES[3324], 186, None, 3)]
        p, r = recipes.dedupe(profs, rs)
        self.assertEqual([x["id"] for x in r], [3324, 2662])
        self.assertEqual({x["id"]: x["name"] for x in p}, {186: "Bergbau", 5: "Sonstiges"})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")

    def test_update_and_staleness(self):
        self.assertTrue(recipes.update(self.con, fake_client(), log=QUIET))
        n = self.con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
        self.assertEqual(n, 4)                                     # ohne Namen + Duplikat entfernt
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM professions").fetchone()[0], 3)
        self.assertEqual(self.con.execute("SELECT quantity FROM recipe_reagents WHERE recipe_id=2662").fetchone()[0], 6)
        # Rezept-Items sind als Items bekannt (Name sofort, Metadaten folgen)
        row = self.con.execute("SELECT name, meta_status FROM items WHERE id=2851").fetchone()
        self.assertEqual(tuple(row), ("Kupferner Kettengürtel", "pending"))
        self.assertEqual(db.get_meta(self.con, "recipes_build"), "static-1.60.1_70399-forever-eu")
        # zweiter Lauf: aktuell, kein Abruf
        self.assertFalse(recipes.update(self.con, fake_client(), log=QUIET))
        # neuer Build → neu laden
        db.set_meta(self.con, "recipes_build", "static-alt")
        self.assertTrue(recipes.is_stale(self.con, fake_client())[0])
        # zu alt → neu laden
        db.set_meta(self.con, "recipes_build", "static-1.60.1_70399-forever-eu")
        db.set_meta(self.con, "recipes_fetched", 1)
        self.assertTrue(recipes.is_stale(self.con)[0])

    def test_existing_item_names_kept(self):
        db.upsert_item(self.con, 2840, name="Kupferbarren (alt)", quality=1)
        recipes.update(self.con, fake_client(), log=QUIET)
        row = self.con.execute("SELECT name, meta_status FROM items WHERE id=2840").fetchone()
        self.assertEqual(tuple(row), ("Kupferbarren (alt)", "ok"))

    def test_partial_fetch_keeps_old_data(self):
        recipes.update(self.con, fake_client(), log=QUIET)
        # die Hälfte der Rezept-Abrufe schlägt fehl → alter Bestand bleibt
        old = recipes.MAX_ERRORS
        recipes.MAX_ERRORS = 0        # Testdaten sind klein; real: mind. 5 Fehler bzw. 2 %
        try:
            ok = recipes.update(self.con, fake_client(fail=(3324, 1600, 2662)), force=True, log=QUIET)
        finally:
            recipes.MAX_ERRORS = old
        self.assertFalse(ok)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0], 4)

    def test_suspiciously_small_result_keeps_old_data(self):
        recipes.update(self.con, fake_client(), log=QUIET)
        profs, rs, *_ = recipes.fetch_all(fake_client(), log=QUIET)
        self.assertFalse(recipes.store(self.con, profs, rs[:1], log=QUIET))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0], 4)


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        recipes.update(self.con, fake_client(), log=QUIET)
        self.al = db.upsert_group_house(self.con, "battlenet", "eu", "allianz")
        T = 1_790_000_000
        db.write_snapshot(self.con, self.al, T - 5 * 86400, "t", {3576: {"min": 50, "quantity": 1}})
        db.write_snapshot(self.con, self.al, T, "t", {2770: {"min": 100, "median": 110, "quantity": 50},
                                                       2840: {"min": 300, "quantity": 9}})
        db.refresh_summary(self.con)

    def test_export_data(self):
        d = recipes.export_data(self.con, recipes.group_houses(self.con))
        self.assertEqual([p["name"] for p in d["professions"]], ["Bergbau", "Schmiedekunst", "Verzauberkunst"])
        rows = {r[0]: r for r in d["recipes"]["rows"]}
        self.assertEqual(rows[1600][4:], [2841, 2, 2, [[2840, 1], [3576, 1]]])
        self.assertIsNone(rows[4289][4])
        self.assertEqual(d["items"]["2770"][0], "Kupfererz")
        self.assertEqual(d["items"]["2770"][3], 1)                 # im AH
        self.assertEqual(d["items"]["2851"][3], 0)                 # nie im AH
        p = d["prices"]["allianz"]["items"]
        self.assertEqual(p["2770"], [100, 110, 50])
        self.assertNotIn("3576", p)                                # letzter Preis zu alt
        self.assertEqual(api.recipes(self.con, {}), d)             # lokaler Server liefert dasselbe

    def test_teaching_items(self):
        ins = "INSERT INTO items(id, name, class_id, subclass_name, meta_status) VALUES (?,?,9,?,'ok')"
        self.con.execute(ins, (2881, "Pläne: Kupferner Kettengürtel", "Schmiedekunst"))
        self.con.execute(ins, (6000, "Formel: Armschiene - Ausdauer", "Verzauberkunst"))
        self.con.execute(ins, (6001, "Muster: Gibt es nicht", "Schneiderei"))
        self.con.execute("INSERT INTO items(id, name, class_id, meta_status) VALUES (6002, 'Kupferner Kettengürtel', 4, 'ok')")
        d = recipes.export_data(self.con, recipes.group_houses(self.con))
        self.assertEqual(d["teaches"], {"2881": [2662], "6000": [4289]})
        self.assertIn("2881", d["items"])                          # Rezept-Item mit Name/Icon/Preis dabei
        # gleichnamige Rezepte: Beruf (Unterklasse) entscheidet
        t = recipes.teaching_items(self.con, [(1, "Kupferner Kettengürtel", "Schmiedekunst"), (2, "Kupferner Kettengürtel", "Schneiderei")])
        self.assertEqual(t[2881], [1])

    def test_site_export_writes_file(self):
        out = tempfile.mkdtemp()
        try:
            export.export_site(self.con, out, log=QUIET)
            with open(os.path.join(out, "data", "recipes.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            self.assertEqual(len(d["recipes"]["rows"]), 4)
            with open(os.path.join(out, "data", "meta.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["counts"]["recipes"], 4)
            self.assertTrue(os.path.exists(os.path.join(out, "recipes-core.js")))
            # Cache-Busting: Skripte/Styles mit Version, dieselbe Version in meta.json
            v = export.frontend_version()
            with open(os.path.join(out, "index.html"), encoding="utf-8") as fh:
                html = fh.read()
            for name in export.ASSETS:
                self.assertIn(f'{name}?v={v}"', html)
            self.assertIn(f'<meta name="app-version" content="{v}">', html)
            with open(os.path.join(out, "data", "meta.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["app_version"], v)
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_empty_db(self):
        con = db.connect(":memory:")
        d = recipes.export_data(con, {})
        self.assertEqual(d["recipes"]["rows"], [])


@unittest.skipUnless(shutil.which("node"), "Node.js nicht installiert")
class RecipeCoreJsTest(unittest.TestCase):
    def test_js(self):
        res = subprocess.run(["node", os.path.join(ROOT, "tests", "recipes_core.test.js")],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
