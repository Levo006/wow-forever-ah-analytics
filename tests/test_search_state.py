"""Regression: globaler Katalog, lokaler Markt, historische Angebote und sicherer Restore."""
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from ahanalytics import api, db, export

ROOT = Path(__file__).resolve().parents[1]


class SearchStateTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        self.a = db.upsert_house(self.con, "battlenet", "eu-110", "Allianz", faction="Allianz")
        self.h = db.upsert_house(self.con, "battlenet", "eu-113", "Horde", faction="Horde")
        self.ts = int(time.time()) // 60 * 60
        for iid, name, en in ((7753, "[Bloodspiller]", "Bloodspiller"), (8226, "[The Butcher]", "The Butcher"),
                               (85, "Schmutzige Lederweste", "Dirty Leather Vest"), (1, "Historisch", "Historical"), (2, "Aktuell", "Current")):
            db.upsert_item(self.con, iid, name=name, name_en=en, quality=3, class_id=2)
        for k, price in enumerate((1000, 1000, 100)):
            db.write_snapshot(self.con, self.a, self.ts - (3 - k) * 3600, "test", {
                1: {"min": price, "quantity": 5}, 7753: {"min": 100, "quantity": 2}})
        db.write_snapshot(self.con, self.a, self.ts, "test", {2: {"min": 500, "quantity": 7}, 7753: {"min": 100, "quantity": 2}})
        db.write_snapshot(self.con, self.h, self.ts, "test", {85: {"min": 300, "quantity": 1}})
        db.set_meta(self.con, f"complete:{self.a}", "1")
        db.refresh_summary(self.con)

    def tearDown(self):
        self.con.close()

    def test_aliases_and_cross_market_catalog(self):
        for q, iid in (("Blutvergießer", 7753), ("Bloodspiller", 7753), ("Der Schlächter", 8226), ("85", 85)):
            r = api.search(self.con, {"q": q, "house": str(self.a)})
            self.assertEqual([x["item_id"] for x in r["items"]], [iid])
        self.assertTrue(api.search(self.con, {"q": "Blutvergießer", "house": str(self.a)})["items"][0]["translation_missing"])
        self.assertEqual(api.search(self.con, {"house": str(self.a), "class": "2"})["total"], 5)
        r = api.search(self.con, {"q": "85", "house": str(self.a)})["items"][0]
        self.assertTrue(r["has_history"])
        self.assertIsNone(r["last_min"])
        self.assertEqual(api.search(self.con, {"q": "85", "house": str(self.a), "min_qty": "1"})["total"], 0)

    def test_absent_offer_has_history_but_no_current_quantity_or_deal(self):
        r = api.search(self.con, {"q": "1", "house": str(self.a)})["items"][0]
        self.assertFalse(r["is_current"])
        self.assertEqual(r["availability"], "not_observed")
        self.assertEqual(r["current_quantity"], 0)
        self.assertEqual(r["last_min"], 100)
        self.assertEqual(r["market_value"], 0)
        self.assertNotIn(1, [r["item_id"] for r in api.deals(self.con, {"house": str(self.a)})["deals"]])
        self.assertEqual(api.overview(self.con, {"house": str(self.a)})["kpis"]["quantity"], 9)
        self.assertEqual(len(api.history(self.con, 1, {"house": str(self.a), "range": "all"})["points"]), 3)
        db.set_meta(self.con, f"complete:{self.a}", "0")
        r = api.search(self.con, {"q": "1", "house": str(self.a)})["items"][0]
        self.assertEqual(r["availability"], "unknown")
        self.assertIsNone(r["current_quantity"])

    def test_replace_snapshot_removes_disappeared_rows(self):
        db.write_snapshot(self.con, self.a, self.ts, "test", {2: {"min": 500, "quantity": 7}})
        db.refresh_summary(self.con)
        r = api.search(self.con, {"q": "7753", "house": str(self.a)})["items"][0]
        self.assertFalse(r["is_current"])

    @unittest.skipUnless(shutil.which("node"), "Node.js nicht installiert")
    def test_static_export_parity(self):
        with tempfile.TemporaryDirectory() as tmp:
            export.export_site(self.con, tmp, log=lambda *a: None)
            result = subprocess.run(["node", str(ROOT / "tests/search_state.test.js"), tmp, str(self.a)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class RestoreSafetyTest(unittest.TestCase):
    def test_restore_is_fail_closed(self):
        text = (ROOT / ".github/workflows/update.yml").read_text()
        block = text.split("      - name: Datenbank laden\n", 1)[1].split("      - name:", 1)[0].split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] if line.startswith("          ") else line for line in block.splitlines())
        # Interpolated GitHub run ID is a non-secret deterministic test value.
        script = script.replace("${{ github.run_id }}", "123")
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bins = root / "bin"
                bins.mkdir()
                gh = bins / "gh"
                gh.write_text("#!/bin/sh\n" + ("printf not-gzip > data/ah.sqlite.gz\nexit 0\n" if corrupt else "exit 1\n"))
                gh.chmod(0o755)
                sleep = bins / "sleep"
                sleep.write_text("#!/bin/sh\nexit 0\n")
                sleep.chmod(0o755)
                env = dict(os.environ, PATH=str(bins) + os.pathsep + os.environ["PATH"])
                r = subprocess.run(["bash", "-c", script], cwd=tmp, env=env, capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse((root / "data/ah.sqlite").exists())
                self.assertFalse((root / "ah.sqlite.gz").exists())


if __name__ == "__main__":
    unittest.main()
