"""Tests für Kennzahlen und die API-Endpunkte (mit kleinem Demo-Datensatz)."""
import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahanalytics import api, db, metrics  # noqa: E402
from ahanalytics.sources import demo  # noqa: E402

H = 3600
T0 = 1_790_000_000 - 1_790_000_000 % 86400


class MetricsTest(unittest.TestCase):
    def test_trend_exact(self):
        pts = [(T0 + d * 86400, 100 * 1.02 ** d) for d in range(30)]
        t = metrics.trend(pts)
        self.assertAlmostEqual(t["pct_per_day"], 2.0, places=6)
        self.assertAlmostEqual(t["r2"], 1.0, places=6)
        self.assertEqual(t["direction"], "steigend")

    def test_trend_flat(self):
        rng = random.Random(1)
        pts = [(T0 + h * H, 100 + rng.uniform(-1, 1)) for h in range(200)]
        self.assertEqual(metrics.trend(pts)["direction"], "seitwärts")

    def test_volatility(self):
        const = [(T0 + i * H, 50) for i in range(48)]
        self.assertEqual(metrics.volatility(const)["log_return_std"], 0)
        alt = [(T0 + i * H, 100 if i % 2 else 110) for i in range(48)]
        v = metrics.volatility(alt)
        self.assertAlmostEqual(v["log_return_std"], math.log(1.1), places=3)
        self.assertAlmostEqual(v["daily"], math.log(1.1) * math.sqrt(24), places=2)
        self.assertGreater(v["cv"], 0)

    def test_outliers(self):
        pts = [(T0 + i * H, 100 + (i % 3)) for i in range(100)]
        pts[50] = (pts[50][0], 300)
        pts[70] = (pts[70][0], 30)
        out = metrics.outliers(pts)
        self.assertEqual([o["ts"] for o in out], [pts[50][0], pts[70][0]])
        self.assertEqual(out[0]["kind"], "Spitze")
        self.assertEqual(out[1]["kind"], "Einbruch")

    def test_no_outliers_in_smooth_series(self):
        pts = [(T0 + i * H, 100 * (1 + 0.05 * math.sin(i / 5))) for i in range(200)]
        self.assertEqual(metrics.outliers(pts), [])

    def test_seasonality_hourly_and_weekday(self):
        pts = []
        for i in range(24 * 28):
            ts = T0 + i * H
            hour = (i % 24)
            wday = ((T0 // 86400 + 3) + i // 24) % 7  # 1.1.1970 war Donnerstag (3)
            v = 100 * (1.10 if hour == 19 else 1.0) * (1.2 if wday == 2 else 1.0)
            pts.append((ts, v))
        s = metrics.seasonality(pts, tz_offset_min=0)
        hourly = {x["hour"]: x["pct"] for x in s["hourly"]}
        self.assertEqual(max(hourly, key=hourly.get), 19)
        weekday = {x["day"]: x["pct"] for x in s["weekday"]}
        self.assertEqual(max(weekday, key=weekday.get), "Mi")

    def test_seasonality_needs_data(self):
        self.assertEqual(metrics.seasonality([(T0, 1)] * 3), {"hourly": None, "weekday": None})

    def test_bucket(self):
        rows = [{"ts": T0 + i * H, "min": i + 1, "max": i + 2, "median": i, "mean": i, "p10": i,
                 "quantity": 10, "auctions": 1} for i in range(1000)]
        b = metrics.bucket(rows, 100)
        self.assertLessEqual(len(b), 101)
        self.assertEqual(b[0]["low"], 1)
        self.assertEqual(sum(x["n"] for x in b), 1000)
        self.assertEqual(metrics.bucket(rows[:10], 100), rows[:10])


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = db.connect(":memory:")
        end = T0 + 40 * 86400
        demo.HOUSES_BACKUP = demo.HOUSES
        demo.HOUSES = demo.HOUSES[:2]
        try:
            demo.generate(cls.con, days=20, seed=7, end_ts=end, log=lambda *a: None)
        finally:
            demo.HOUSES = demo.HOUSES_BACKUP
        cls.house = api._default_house(cls.con)

    def test_houses_and_overview(self):
        hs = api.houses(self.con, {})
        self.assertEqual(len(hs["houses"]), 2)
        ov = api.overview(self.con, {"house": str(self.house)})
        self.assertEqual(ov["kpis"]["items"], len(demo.ITEMS))
        self.assertTrue(ov["categories"])

    def test_search_filters(self):
        r = api.search(self.con, {"q": "erz", "house": str(self.house)})
        self.assertTrue(all("erz" in x["name"].lower() for x in r["items"]))
        r = api.search(self.con, {"class": "7", "subclass": "5", "house": str(self.house)})
        self.assertTrue(r["total"] > 0 and all(x["subclass_name"] == "Stoff" for x in r["items"]))
        r = api.search(self.con, {"quality": "4", "house": str(self.house)})
        self.assertTrue(all(x["quality"] == 4 for x in r["items"]))
        r = api.search(self.con, {"min_price": "100000", "house": str(self.house), "sort": "price", "dir": "asc"})
        prices = [x["last_min"] for x in r["items"]]
        self.assertTrue(all(p >= 100000 for p in prices) and prices == sorted(prices))
        r = api.search(self.con, {"faction": "Horde"})
        self.assertTrue(all(x["faction"] == "Horde" for x in r["items"]))
        r = api.search(self.con, {"q": "2770", "spark": "1"})
        self.assertTrue(r["items"][0]["spark"])
        with self.assertRaises(api.BadRequest):
            api.search(self.con, {"min_price": "abc"})

    def test_item_history_analysis(self):
        h = api.history(self.con, 2770, {"house": str(self.house), "range": "7d"})
        self.assertEqual(h["raw_points"], 7 * 24 + 1)
        h = api.history(self.con, 2770, {"house": str(self.house), "range": "all", "max_points": "50"})
        self.assertLessEqual(len(h["points"]), 51)
        a = api.analysis(self.con, 2770, {"house": str(self.house), "range": "30d", "metric": "median"})
        self.assertEqual(a["metric"], "median")
        self.assertIsNotNone(a["seasonality"]["hourly"])
        self.assertIsNotNone(a["seasonality"]["weekday"])
        with self.assertRaises(api.BadRequest):
            api.history(self.con, 2770, {"range": "5y"})
        with self.assertRaises(api.NotFound):
            api.item(self.con, 999999, {})

    def test_snapshot_drilldown(self):
        snaps = api.item_snapshots(self.con, 2770, {"house": str(self.house), "page_size": "5"})
        self.assertEqual(len(snaps["rows"]), 5)
        latest = snaps["rows"][0]
        self.assertEqual(latest["has_ladder"], 1)
        s = api.snapshot(self.con, self.house, latest["ts"], {"item": "2770"})
        self.assertTrue(s["focus"]["ladder"])
        self.assertEqual(s["focus"]["stats"]["min"], latest["min"])
        self.assertIsNotNone(s["prev_ts"])
        self.assertTrue(any(x["chg"] is not None for x in s["items"]))

    def test_compare(self):
        c = api.compare(self.con, {"items": "2770,2771,2772", "house": str(self.house), "range": "7d"})
        self.assertEqual(len(c["series"]), 3)
        self.assertAlmostEqual(c["correlation"][0][0], 1.0, places=6)

    def test_movers_and_deals(self):
        m = api.movers(self.con, {"house": str(self.house), "window": "24h"})
        self.assertTrue(all(x["chg24_min"] > 0 for x in m["gainers"]))
        self.assertTrue(all(x["chg24_min"] < 0 for x in m["losers"]))
        g = [x["chg24_min"] for x in m["gainers"]]
        self.assertEqual(g, sorted(g, reverse=True))
        d = api.deals(self.con, {"house": str(self.house), "threshold": "10"})
        for x in d["deals"]:
            self.assertLessEqual(x["last_min"], x["avg7_min"] * 0.9 + 1)

    def test_classes(self):
        c = api.classes(self.con, {"house": str(self.house)})["classes"]
        total = sum(x["items"] for x in c)
        self.assertEqual(total, self.con.execute(
            "SELECT COUNT(*) FROM item_summary WHERE house_id=?", (self.house,)).fetchone()[0])


class CompactTest(unittest.TestCase):
    def test_compact_whole_days(self):
        con = db.connect(":memory:")
        hid = db.upsert_house(con, "t", "x", "Haus", granularity="hour")
        day0 = 1_790_000_000 // 86400 * 86400
        for h in range(10 * 24):  # 10 Tage stündlich
            ts = day0 + h * 3600
            db.write_snapshot(con, hid, ts, "t", {1: {"min": 100 + (h % 24), "max": 200, "quantity": 10}})
        db.refresh_summary(con)
        res = db.compact(con, hourly_days=7, house_hourly_days=7, log=lambda *a: None)
        rows = con.execute("SELECT ts, min, max FROM prices ORDER BY ts").fetchall()
        daily = [r for r in rows if db.is_daily_ts(r[0])]
        hourly = [r for r in rows if not db.is_daily_ts(r[0])]
        self.assertEqual(len(daily), 2)                  # Tage 0 und 1 vollständig verdichtet
        self.assertEqual(daily[0][1], round(sum(100 + i for i in range(24)) / 24))
        self.assertEqual(daily[0][2], 200)
        self.assertEqual(len(hourly), 8 * 24)
        self.assertEqual(res["days"], 2)
        again = db.compact(con, hourly_days=7, house_hourly_days=7, log=lambda *a: None)  # idempotent
        self.assertEqual(again["days"], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM snapshots WHERE source='tagesmittel'").fetchone()[0], 2)

    def test_retention_markets_vs_single_houses(self):
        con = db.connect(":memory:")
        single = db.upsert_house(con, "battlenet", "eu-110", "Haus", faction="Allianz")
        market = db.upsert_group_house(con, "battlenet", "eu", "allianz")
        day0 = 1_790_000_000 // 86400 * 86400
        for h in range(20 * 24):  # 20 Tage stündlich
            for hid in (single, market):
                db.write_snapshot(con, hid, day0 + h * 3600, "t", {1: {"min": 100, "quantity": 1}})
        db.compact(con, log=lambda *a: None)   # Standard: Märkte 7 T stündlich/180 T, Häuser 2 T/14 T

        def counts(hid):
            ts = [r[0] for r in con.execute("SELECT ts FROM prices WHERE house_id=?", (hid,))]
            return sum(not db.is_daily_ts(t) for t in ts), sum(db.is_daily_ts(t) for t in ts)
        self.assertEqual(counts(market), (8 * 24, 12))   # 7 ganze Tage + laufender Tag stündlich
        hourly, daily = counts(single)
        self.assertEqual(hourly, 3 * 24)                  # 2 ganze Tage + laufender Tag
        self.assertLessEqual(daily, 12)                   # älter als 14 Tage gelöscht
        oldest = con.execute("SELECT MIN(ts) FROM prices WHERE house_id=?", (single,)).fetchone()[0]
        self.assertGreaterEqual(oldest, day0 + 19 * 86400 - 15 * 86400)


if __name__ == "__main__":
    unittest.main()
