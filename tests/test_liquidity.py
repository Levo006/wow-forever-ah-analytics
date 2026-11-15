"""Umsatzsignale: Präsenz, Ausverkäufe, Teilsnapshot-Ausklammerung, Score, Historie."""
import unittest

from ahanalytics import api, db, liquidity

H = 3600
T0 = 1_794_600_000  # beliebiger Start, ganze Stunde


def _snap(con, hid, ts, items, complete=None):
    stats = {i: {"min": 100 * (n + 1), "median": 100 * (n + 1), "quantity": q, "auctions": 1}
             for n, (i, q) in enumerate(items.items())}
    sid = db.write_snapshot(con, hid, ts, "test", stats)
    if complete is not None:
        con.execute("UPDATE snapshots SET complete=? WHERE id=?", (complete, sid))


class LiquidityTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        self.hid = db.upsert_house(self.con, "battlenet", "eu-110", "AH #110",
                                   region="eu", faction="Allianz")

    def tearDown(self):
        self.con.close()

    def _series(self, hours):
        """hours: Liste von dicts {item_id: qty} oder None (= kein Snapshot)."""
        for n, items in enumerate(hours):
            if items is not None:
                _snap(self.con, self.hid, T0 + n * H, items)

    def test_vanish_and_drops_counted(self):
        # Item 1: 3x komplett ausverkauft; Item 2: nur Mengeneinbruch; Item 3: liegt still.
        hours = [{1: 5, 2: 10, 3: 1}, {2: 10, 3: 1}, {1: 5, 2: 4, 3: 1}, {2: 4, 3: 1},
                 {1: 5, 2: 4, 3: 1}, {1: 5, 2: 4, 3: 1}]
        self._series(hours)
        stats = liquidity.compute_house(self.con, self.hid)
        self.assertEqual(stats[1]["vanish"], 2)          # Stunde 0->1 und 2->3
        self.assertEqual(stats[1]["events"], 2)
        self.assertEqual(stats[1]["units"], 10)
        self.assertEqual(stats[2]["vanish"], 0)
        self.assertEqual(stats[2]["events"], 1)          # 10 -> 4
        self.assertEqual(stats[3]["events"], 0)          # nie bewegt
        self.assertEqual(stats[3]["presence"], 1.0)
        self.assertGreater(stats[1]["score"], stats[3]["score"])

    def test_partial_snapshots_excluded(self):
        # Teilsnapshot (complete=0) in Stunde 1: das dortige "Verschwinden" zählt nicht,
        # und die Nachbarn werden nicht über die Lücke verglichen.
        hours = [{1: 5}, {1: 5}, {1: 5}]
        self._series(hours)
        con = self.con
        sid = con.execute("SELECT id FROM snapshots ORDER BY ts LIMIT 1 OFFSET 1").fetchone()[0]
        con.execute("UPDATE snapshots SET complete=0 WHERE id=?", (sid,))
        # Item ist Stunde 0 und 2 gelistet, Stunde 1 (Teilsnapshot) fehlt der Eintrag:
        con.execute("DELETE FROM prices WHERE house_id=? AND ts=?", (self.hid, T0 + H))
        stats = liquidity.compute_house(con, self.hid)
        self.assertEqual(stats[1]["vanish"], 0)
        self.assertEqual(stats[1]["events"], 0)
        # Die Teilstunde zählt weder als gelistet noch als erwartete Stunde:
        self.assertEqual(stats[1]["expected"], 2)
        self.assertEqual(stats[1]["present"], 2)
        self.assertEqual(stats[1]["presence"], 1.0)

    def test_daily_aggregates_ignored(self):
        self._series([{1: 5}, {1: 5}, {1: 3}])
        day_ts = (T0 // 86400) * 86400 + db.DAILY_OFFSET
        _snap(self.con, self.hid, day_ts, {1: 99})  # verdichteter Tageswert
        stats = liquidity.compute_house(self.con, self.hid)
        self.assertEqual(stats[1]["expected"], 3)  # Tagesmittel ist kein Stunden-Snapshot
        self.assertEqual(stats[1]["events"], 1)    # nur 5 -> 3

    def test_update_writes_table_and_is_idempotent(self):
        self._series([{1: 5}, {1: 5}, {1: 5}])
        liquidity.update_liquidity(self.con, log=lambda *a: None)
        n1 = self.con.execute("SELECT COUNT(*) FROM item_liquidity").fetchone()[0]
        self.assertEqual(n1, 1)
        liquidity.update_liquidity(self.con, log=lambda *a: None)
        n2 = self.con.execute("SELECT COUNT(*) FROM item_liquidity").fetchone()[0]
        self.assertEqual(n1, n2)
        row = self.con.execute("SELECT presence, events, score FROM item_liquidity").fetchone()
        self.assertEqual(row[0], 1.0)
        self.assertEqual(row[1], 0)
        self.assertIsNotNone(row[2])

    def test_daily_backfill_only_for_group_houses(self):
        gid = db.upsert_group_house(self.con, "battlenet", "eu", "neutral")
        day0 = (T0 // 86400) - 1  # abgeschlossener Vortag
        for n in range(6):
            _snap(self.con, gid, day0 * 86400 + n * H, {1: 5, 2: 2})
            _snap(self.con, self.hid, day0 * 86400 + n * H, {1: 5})
        today = T0 // 86400 + 1
        liquidity.backfill_daily(self.con, gid, today=today, log=lambda *a: None)
        rows = self.con.execute(
            "SELECT day, expected, present FROM liquidity_daily WHERE house_id=? AND item_id=1",
            (gid,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], day0)
        self.assertEqual(rows[0][1], 6)
        self.assertEqual(rows[0][2], 6)
        liquidity.backfill_daily(self.con, gid, today=today, log=lambda *a: None)  # idempotent
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM liquidity_daily WHERE house_id=?", (gid,)).fetchone()[0], 2)  # 2 Items
        # Einzelhaus: keine Tageshistorie
        liquidity.backfill_daily(self.con, self.hid, today=today, log=lambda *a: None)
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM liquidity_daily WHERE house_id=?", (self.hid,)).fetchone()[0], 0)

    def test_api_endpoint_and_item_history(self):
        hours = [{1: 5, 2: 10}, {2: 10}, {1: 5, 2: 9}, {2: 9}, {1: 5, 2: 9}]
        self._series(hours)
        db.refresh_summary(self.con)
        liquidity.update_liquidity(self.con, log=lambda *a: None)
        out = api.liquidity(self.con, {"house": str(self.hid)})
        self.assertEqual(out["window_days"], 7)
        self.assertEqual(out["hot"][0]["item_id"], 1)   # meiste Ausverkäufe zuerst
        self.assertEqual(out["hot"][0]["liq_vanish"], 2)
        # Item 2: dauerhaft gelistet, nur ein Mengeneinbruch, kein Ausverkauf → "cold"
        self.assertEqual([x["item_id"] for x in out["cold"]], [2])
        info = api.item(self.con, 1, {})
        self.assertIn("liquidity", info)
        row = next(h for h in info["houses"] if h["house_id"] == self.hid)
        self.assertIsNotNone(row["liq_score"])


if __name__ == "__main__":
    unittest.main()
