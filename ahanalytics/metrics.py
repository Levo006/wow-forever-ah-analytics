"""Kennzahlen für Preis-Zeitreihen (reines Python, gut testbar).

Alle Funktionen erwarten Listen von (ts, wert) mit ts in Unix-Sekunden,
aufsteigend sortiert; ``None``-Werte werden ignoriert.
"""
from __future__ import annotations

import math
import statistics
import time
from bisect import bisect_left, bisect_right

DAY = 86400.0
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _clean(points):
    return [(int(t), float(v)) for t, v in points if v is not None and v > 0]


def volatility(points) -> dict:
    """Volatilität als Standardabweichung der Log-Renditen und als CV.

    * ``log_return_std``: Std. der Log-Änderung von Punkt zu Punkt.
    * ``daily``: auf einen Tag skaliert (sqrt(Punkte pro Tag)).
    * ``cv``: Variationskoeffizient Std/Mittel der Preise.
    """
    p = _clean(points)
    if len(p) < 3:
        return {"log_return_std": None, "daily": None, "cv": None}
    rets = [math.log(b[1] / a[1]) for a, b in zip(p, p[1:])]
    sd = statistics.pstdev(rets)
    span_days = max((p[-1][0] - p[0][0]) / DAY, 1e-9)
    per_day = (len(p) - 1) / span_days
    vals = [v for _, v in p]
    mean = statistics.fmean(vals)
    return {
        "log_return_std": sd,
        "daily": sd * math.sqrt(per_day) if per_day > 0 else None,
        "cv": statistics.pstdev(vals) / mean if mean else None,
    }


def trend(points) -> dict:
    """Lineare Regression auf ln(preis) über die Zeit.

    ``pct_per_day``: geschätzte prozentuale Änderung pro Tag,
    ``r2``: Bestimmtheitsmaß (0 = kein Trend, 1 = perfekter Trend).
    """
    p = _clean(points)
    if len(p) < 3:
        return {"pct_per_day": None, "r2": None, "direction": "unbekannt"}
    t0 = p[0][0]
    xs = [(t - t0) / DAY for t, _ in p]
    ys = [math.log(v) for _, v in p]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return {"pct_per_day": None, "r2": None, "direction": "unbekannt"}
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    syy = sum((y - my) ** 2 for y in ys)
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    pct = (math.exp(slope) - 1) * 100
    if r2 < 0.1 or abs(pct) < 0.25:
        direction = "seitwärts"
    else:
        direction = "steigend" if pct > 0 else "fallend"
    return {"pct_per_day": pct, "r2": r2, "direction": direction, "slope_log": slope}


def rolling_median(points, window_s: float):
    """Zentrierter gleitender Median mit Zeitfenster ``window_s``."""
    p = _clean(points)
    ts = [t for t, _ in p]
    out = []
    half = window_s / 2
    for t, _ in p:
        lo = bisect_left(ts, t - half)
        hi = bisect_right(ts, t + half)
        out.append((t, statistics.median(v for _, v in p[lo:hi])))
    return out


def outliers(points, window_s: float = 2 * DAY, k: float = 4.0, min_rel: float = 0.15):
    """Ausreißer über gleitenden Median ± k · MAD (robust).

    Ein Punkt gilt nur dann als Ausreißer, wenn er zusätzlich mindestens
    ``min_rel`` (15 %) vom lokalen Median abweicht – sonst würden bei sehr
    ruhigen Reihen Kleinstschwankungen markiert.
    """
    p = _clean(points)
    if len(p) < 5:
        return []
    ts = [t for t, _ in p]
    half = window_s / 2
    result = []
    for i, (t, v) in enumerate(p):
        lo = bisect_left(ts, t - half)
        hi = bisect_right(ts, t + half)
        neigh = [x for j, (_, x) in enumerate(p[lo:hi], start=lo) if j != i]
        if len(neigh) < 3:
            continue
        med = statistics.median(neigh)
        mad = statistics.median(abs(x - med) for x in neigh) * 1.4826
        dev = v - med
        if med > 0 and abs(dev) / med >= min_rel and abs(dev) > k * max(mad, med * 0.01):
            result.append({"ts": t, "value": v, "expected": med,
                           "deviation_pct": dev / med * 100,
                           "kind": "Spitze" if dev > 0 else "Einbruch"})
    return result


def _local_dt(ts, tz_offset_min=None):
    if tz_offset_min is None:
        return time.localtime(ts)
    return time.gmtime(ts + tz_offset_min * 60)


def seasonality(points, tz_offset_min=None) -> dict:
    """Tageszeit- und Wochentagsmuster.

    Jeder Punkt wird durch den zentrierten 24-h- bzw. 7-Tage-Median geteilt
    (entfernt Trend), dann werden die Verhältnisse je Stunde bzw. Wochentag
    gemittelt. Ergebnis in Prozent Abweichung vom „normalen“ Preis.
    """
    p = _clean(points)
    out = {"hourly": None, "weekday": None}
    if len(p) < 24:
        return out
    span = p[-1][0] - p[0][0]
    # Stündliches Muster braucht unterstündliche Auflösung
    step = span / max(len(p) - 1, 1)
    if step <= 3 * 3600 and span >= 2 * DAY:
        base = dict(rolling_median(p, DAY))
        buckets = [[] for _ in range(24)]
        for t, v in p:
            b = base.get(t)
            if b:
                buckets[_local_dt(t, tz_offset_min).tm_hour].append(v / b - 1)
        out["hourly"] = [
            {"hour": h, "pct": statistics.fmean(b) * 100 if b else None, "n": len(b)}
            for h, b in enumerate(buckets)]
    if span >= 14 * DAY:
        base = dict(rolling_median(p, 7 * DAY))
        buckets = [[] for _ in range(7)]
        for t, v in p:
            b = base.get(t)
            if b:
                buckets[_local_dt(t, tz_offset_min).tm_wday].append(v / b - 1)
        out["weekday"] = [
            {"day": WEEKDAYS[d], "pct": statistics.fmean(b) * 100 if b else None, "n": len(b)}
            for d, b in enumerate(buckets)]
    return out


def describe(points) -> dict:
    p = _clean(points)
    if not p:
        return {}
    vals = [v for _, v in p]
    return {
        "count": len(vals),
        "min": min(vals),
        "max": max(vals),
        "mean": statistics.fmean(vals),
        "median": statistics.median(vals),
        "first": vals[0],
        "last": vals[-1],
        "change_pct": (vals[-1] / vals[0] - 1) * 100 if vals[0] else None,
    }


def full_analysis(points, tz_offset_min=None) -> dict:
    return {
        "describe": describe(points),
        "volatility": volatility(points),
        "trend": trend(points),
        "outliers": outliers(points),
        "seasonality": seasonality(points, tz_offset_min),
    }


def bucket(points_rows, max_points: int):
    """Reduziert Zeilen (dicts mit 'ts') auf höchstens ``max_points`` Buckets.

    Preise werden gemittelt, ``min`` als Minimum, ``max`` als Maximum,
    Mengen gemittelt. Gibt die Zeilen unverändert zurück, wenn sie klein genug sind.
    """
    n = len(points_rows)
    if n <= max_points or n == 0:
        return points_rows
    t0, t1 = points_rows[0]["ts"], points_rows[-1]["ts"]
    width = max((t1 - t0) / max_points, 1)
    out, cur, cur_key = [], [], None
    for r in points_rows:
        k = int((r["ts"] - t0) // width)
        if cur_key is not None and k != cur_key:
            out.append(_merge(cur))
            cur = []
        cur_key = k
        cur.append(r)
    if cur:
        out.append(_merge(cur))
    return out


def _merge(rows):
    def avg(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return int(round(statistics.fmean(vals))) if vals else None

    mins = [r["min"] for r in rows if r.get("min") is not None]
    maxs = [r["max"] for r in rows if r.get("max") is not None]
    return {
        "ts": rows[len(rows) // 2]["ts"],
        "min": avg("min"),
        "low": min(mins) if mins else None,
        "p10": avg("p10"), "median": avg("median"), "mean": avg("mean"),
        "max": max(maxs) if maxs else None,
        "quantity": avg("quantity"), "auctions": avg("auctions"),
        "n": len(rows),
    }
