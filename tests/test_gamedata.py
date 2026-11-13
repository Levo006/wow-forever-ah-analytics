"""Tests für Quests (Belohnungen) und Item-Sets: Parsing, Abruf, Speicherung, Export."""
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import api, db, export, gamedata  # noqa: E402
from ahanalytics.sources import battlenet  # noqa: E402
from test_ingest import FakeResponse  # noqa: E402

QUIET = lambda *a: None  # noqa: E731
NS = "https://eu.api.blizzard.com/x?namespace=static-1.60.1_70399-forever-eu"


def _item(i, n):
    return {"item": {"id": i, "name": n}}


QUESTS = {
    33: {"id": 33, "title": "Wölfe an der Grenze", "area": {"name": "Nordhaintal"}, "requirements": {},
         "rewards": {"experience": 170, "items": {"choice_of": [_item(80, "Weiche Schuhe"), _item(6070, "Wolfhaut-Armschienen")]}}},
    40: {"id": 40, "title": "Hordequest", "category": {"name": "Saisonbedingt"}, "type": {"name": "Dungeon"},
         "is_daily": True, "requirements": {"faction": {"type": "HORDE"}, "classes": [{"name": "Krieger"}]},
         "rewards": {"money": {"value": 500}, "items": {"items": [_item(80, "Weiche Schuhe"), _item(80, "Weiche Schuhe")]}}},
    41: {"id": 41, "title": "Ohne Belohnung", "requirements": {}, "rewards": {"experience": 10}},
    42: {"id": 42, "title": None, "rewards": {"items": {"items": [_item(1, "x")]}}},          # Platzhalter
}
SETS = {
    144: {"id": 144, "name": "Eisenfederrüstung", "items": [{"id": 15066, "name": "Brust"}, {"id": 15067, "name": "Schultern"}],
          "effects": [{"display_string": "Set: +20 Schaden", "required_count": 2}]},
    1: {"id": 1, "name": "leer", "items": []},
}


def fake_client(fail=()):
    routes = {"/token": {"access_token": "T", "expires_in": 3600},
              "/data/wow/quest/area/index": {"_links": {"self": {"href": NS}}, "areas": [{"id": 9, "name": "Nordhaintal"}]},
              "/data/wow/quest/category/index": {"_links": {"self": {"href": NS}}, "categories": [{"id": 22, "name": "Saisonbedingt"}]},
              "/data/wow/quest/area/9": {"quests": [{"id": 33}, {"id": 41}, {"id": 42}]},
              "/data/wow/quest/category/22": {"quests": [{"id": 40}, {"id": 33}]},
              "/data/wow/item-set/index": {"_links": {"self": {"href": NS}}, "item_sets": [{"id": 144}, {"id": 1}]}}
    for q, p in QUESTS.items():
        if q not in fail:
            routes[f"/data/wow/quest/{q}"] = p
    for s, p in SETS.items():
        routes[f"/data/wow/item-set/{s}"] = p

    def opener(req, timeout=None):
        path = urllib.parse.urlparse(req.full_url).path
        if path in routes:
            return FakeResponse(routes[path], {})
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
    return battlenet.BattleNetClient("id", "secret", region="eu", opener=opener)


class ParseTest(unittest.TestCase):
    def test_quest(self):
        q = gamedata.parse_quest(QUESTS[40], "Hinweis")
        self.assertEqual((q["zone"], q["type"], q["faction"], q["classes"], q["repeatable"], q["money"]),
                         ("Saisonbedingt", "Dungeon", "Horde", "Krieger", 2, 500))
        self.assertEqual(q["rewards"], [{"item_id": 80, "item_name": "Weiche Schuhe", "choice": 0}])  # doppelt → einmal
        q = gamedata.parse_quest(QUESTS[33])
        self.assertEqual([r["choice"] for r in q["rewards"]], [1, 1])
        self.assertIsNone(gamedata.parse_quest(QUESTS[42]))
        self.assertEqual(gamedata.parse_quest({"id": 5, "title": "x"}, "Zone")["zone"], "Zone")

    def test_set(self):
        s = gamedata.parse_set(SETS[144])
        self.assertEqual(json.loads(s["effects"]), [[2, "Set: +20 Schaden"]])
        self.assertIsNone(gamedata.parse_set(SETS[1]))


class StoreExportTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        res = gamedata.update(self.con, fake_client(), log=QUIET)
        self.assertEqual(res, {"quests": True, "sets": True})

    def test_store(self):
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM quests").fetchone()[0], 3)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM quest_rewards").fetchone()[0], 3)
        self.assertEqual(self.con.execute("SELECT name FROM items WHERE id=15067").fetchone()[0], "Schultern")
        self.assertEqual(gamedata.update(self.con, fake_client(), log=QUIET), {"quests": False, "sets": False})
        db.set_meta(self.con, "quests_build", "alt")
        self.assertEqual(gamedata.update(self.con, fake_client(), log=QUIET), {"quests": True, "sets": False})

    def test_failed_fetch_keeps_data(self):
        old = gamedata.MAX_ERRORS
        gamedata.MAX_ERRORS = 0
        try:
            res = gamedata.update(self.con, fake_client(fail=(33, 40)), force=True, log=QUIET)
        finally:
            gamedata.MAX_ERRORS = old
        self.assertFalse(res["quests"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM quests").fetchone()[0], 3)

    def test_resume_after_time_budget(self):
        class After:                        # Frist, die nach n Prüfungen abläuft
            def __init__(self, n):
                self.n = n

            def __lt__(self, other):
                self.n -= 1
                return self.n < 0

        old = gamedata.CHUNK
        gamedata.CHUNK = 1
        try:
            con = db.connect(":memory:")
            q, _, _, err = gamedata.fetch_quests(fake_client(), QUIET, con=con, deadline=After(2))
            self.assertIsNone(q)                                     # abgebrochen …
            self.assertEqual(con.execute("SELECT COUNT(*) FROM gamedata_cache").fetchone()[0], 2)  # … Fortschritt bleibt
            # Fortsetzung: 33 und 40 kommen aus dem Zwischenstand, obwohl ihr Abruf jetzt fehlschlägt
            q, _, _, err = gamedata.fetch_quests(fake_client(fail=(33, 40)), QUIET, con=con)
            self.assertEqual((sorted(x["id"] for x in q), err), ([33, 40, 41], 0))
            # Zeitbudget 0: Sets werden trotzdem gespeichert, Quests im nächsten Lauf
            con = db.connect(":memory:")
            self.assertEqual(gamedata.update(con, fake_client(), log=QUIET, budget=0), {"sets": True, "quests": False})
            self.assertEqual(gamedata.update(con, fake_client(), log=QUIET), {"sets": False, "quests": True})
            self.assertEqual(con.execute("SELECT COUNT(*) FROM gamedata_cache").fetchone()[0], 0)
        finally:
            gamedata.CHUNK = old

    def test_export(self):
        al = db.upsert_group_house(self.con, "battlenet", "eu", "allianz")
        db.write_snapshot(self.con, al, 1_790_000_000, "t", {6070: {"min": 250, "quantity": 2}})
        db.refresh_summary(self.con)
        d = gamedata.export_data(self.con, {"allianz": al})
        rows = {r[0]: r for r in d["quests"]["rows"]}
        self.assertEqual(sorted(rows), [33, 40])                 # nur Quests mit Item-Belohnung
        self.assertEqual(rows[33][9], [[80, 1], [6070, 1]])
        self.assertEqual(d["sets"]["rows"][0][2], [15066, 15067])
        self.assertEqual(d["items"]["6070"][3], 1)
        self.assertEqual(d["prices"]["allianz"]["items"]["6070"][0], 250)
        self.assertEqual(api.quests(self.con, {}), gamedata.export_data(self.con, {"allianz": al}))
        out = tempfile.mkdtemp()
        try:
            export.export_site(self.con, out, log=QUIET)
            with open(os.path.join(out, "data", "quests.json"), encoding="utf-8") as fh:
                self.assertEqual(len(json.load(fh)["quests"]["rows"]), 2)
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
