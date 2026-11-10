"""Statischer Export für GitHub Pages (oder jeden anderen statischen Webspace).

Schreibt das Frontend plus vorberechnete JSON-Dateien in einen Ordner
(Standard ``docs/``). Das Frontend erkennt den Statikmodus an
``data/meta.json`` und rechnet Suche, Filter, Top-Movers, Deals und
Kategorien im Browser auf Basis der Zusammenfassungen.

Layout::

    docs/
      index.html, app.css, app.js, static-api.js, vendor/…
      data/meta.json                      Häuser, Stand, Export-Infos
      data/house/<id>/summary.json        alle Items des Hauses (spaltenweise)
      data/house/<id>/snapshots.json      Snapshot-Liste (Marktwert-Verlauf)
      data/house/<id>/snap/<ts>.json      Items eines Snapshots (+ Preisstaffeln)
      data/item/<id>.json                 Metadaten, Historie und Analysen je Haus

Lokale Pfade (z. B. der WoW-Account-Ordner) werden NICHT exportiert.
"""
from __future__ import annotations

import json
import os
import shutil
import time

from . import api, db, metrics

STATIC_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

SUMMARY_FIELDS = [
    "item_id", "first_ts", "last_ts", "points", "last_min", "last_median", "last_qty", "last_auctions",
    "prev24_min", "prev24_median", "prev7d_min", "prev7d_median", "avg7_min", "avg30_min",
    "avg7_median", "avg30_median", "avg7_qty", "cv7_min", "low30_min", "high30_min",
    "name", "name_en", "quality", "class_id", "class_name", "subclass_id", "subclass_name", "icon",
    "item_level", "is_commodity", "chg24_min", "chg7d_min", "chg24_median", "chg7d_median",
    "market_value", "spark",
]
PRICE_FIELDS = ["ts", "min", "p10", "median", "mean", "max", "quantity", "auctions"]
SNAP_FIELDS = ["item_id", "min", "p10", "median", "mean", "max", "quantity", "auctions", "prev_min"]


def _round(v):
    if isinstance(v, float):
        return round(v, 4) if abs(v) < 1000 else round(v, 1)
    return v


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"), default=_round)


def _columnar(fields, rows):
    return {"cols": fields, "rows": [[_round(r.get(f)) for f in fields] for r in rows]}


def _ranges_for(granularity):
    return ["7d", "30d", "90d", "all"] if granularity == "day" else ["7d", "30d", "all"]


def export_site(con, out_dir: str, history_days: int = 90, snapshot_files: int = 72,
                repo_url: str = "", log=print) -> dict:
    t0 = time.time()
    out_dir = os.path.abspath(out_dir)
    data_dir = os.path.join(out_dir, "data")
    # Frontend kopieren, alte Daten entfernen
    if os.path.isdir(data_dir):
        shutil.rmtree(data_dir)
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(STATIC_SRC):
        src = os.path.join(STATIC_SRC, name)
        dst = os.path.join(out_dir, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    open(os.path.join(out_dir, ".nojekyll"), "w").close()

    houses = [h for h in api.houses(con, {})["houses"] if h["snapshots"]]
    stats = {"houses": len(houses), "items": 0, "snap_files": 0}
    item_ids: set[int] = set()

    for h in houses:
        hid = h["id"]
        hdir = os.path.join(data_dir, "house", str(hid))
        latest = h["last_ts"]
        # Zusammenfassung inkl. 14-Tage-Sparkline
        rows = [dict(r) for r in con.execute(
            f"SELECT {api.SUMMARY_COLS} {api.SUMMARY_FROM} WHERE s.house_id=?", (hid,))]
        for i in range(0, len(rows), 500):
            api._attach_sparklines(con, rows[i:i + 500])
        item_ids.update(r["item_id"] for r in rows)
        _write(os.path.join(hdir, "summary.json"), _columnar(SUMMARY_FIELDS, rows))

        # Snapshots
        snaps = [dict(r) for r in con.execute(
            """SELECT s.id, s.ts, s.source, s.item_count, s.auction_count, s.total_value, s.imported_at,
                      EXISTS(SELECT 1 FROM ladders l WHERE l.snapshot_id = s.id) has_ladder
               FROM snapshots s WHERE s.house_id=? ORDER BY s.ts""", (hid,))]
        with_file = {s["ts"] for s in snaps[-snapshot_files:]}
        for s in snaps:
            s["file"] = 1 if s["ts"] in with_file else 0
        _write(os.path.join(hdir, "snapshots.json"), snaps)
        prev_ts = None
        for s in snaps:
            if s["file"]:
                prices = [dict(r) for r in con.execute(
                    """SELECT p.*, pp.min prev_min FROM prices p
                       LEFT JOIN prices pp ON pp.house_id=p.house_id AND pp.item_id=p.item_id AND pp.ts=?
                       WHERE p.house_id=? AND p.ts=?""", (prev_ts, hid, s["ts"]))]
                ladders = {str(r[0]): json.loads(r[1]) for r in con.execute(
                    "SELECT item_id, data FROM ladders WHERE snapshot_id=?", (s["id"],))}
                _write(os.path.join(hdir, "snap", f"{s['ts']}.json"),
                       {**_columnar(SNAP_FIELDS, prices), "ladders": ladders})
                stats["snap_files"] += 1
            prev_ts = s["ts"]
        h["latest"] = latest

    # Items
    house_by_id = {h["id"]: h for h in houses}
    for n, iid in enumerate(sorted(item_ids)):
        info = api.item(con, iid, {})
        hist, analysis = {}, {}
        for hs in info["houses"]:
            hid = hs["house_id"]
            house = house_by_id.get(hid)
            if not house:
                continue
            lo = (house["latest"] or 0) - history_days * 86400
            rows = api._history_rows(con, hid, iid, lo)
            hist[str(hid)] = _columnar(PRICE_FIELDS, rows)
            has_median = any(r["median"] is not None for r in rows)
            per_range = {}
            for rng in _ranges_for(house["granularity"]):
                span = api.RANGES[rng]
                rlo = (house["latest"] - span) if span else 0
                sub = [r for r in rows if r["ts"] >= rlo]
                per_metric = {}
                for metric in (("min", "median") if has_median else ("min",)):
                    series = [(r["ts"], r[metric]) for r in sub]
                    res = metrics.full_analysis(series)  # Saisonalität in Ortszeit des Exports
                    res["quantity"] = metrics.describe([(r["ts"], r["quantity"]) for r in sub])
                    res["metric"] = metric
                    res["points"] = len(series)
                    per_metric[metric] = res
                per_range[rng] = per_metric
            analysis[str(hid)] = per_range
        _write(os.path.join(data_dir, "item", f"{iid}.json"),
               {"item": info["item"], "houses": info["houses"], "history": hist, "analysis": analysis})
        if n and n % 1000 == 0:
            log(f"  {n} Items exportiert …")
    stats["items"] = len(item_ids)

    meta = {
        "generated_at": int(time.time()),
        "tz_offset_min": int(time.localtime().tm_gmtoff // 60),
        "history_days": history_days,
        "repo_url": repo_url,
        "houses": [{k: v for k, v in h.items() if k != "latest"} for h in houses],
        "default": api._default_house(con),
        "counts": {"houses": len(houses), "items": len(item_ids),
                   "snapshots": sum(h["snapshots"] for h in houses)},
        "import_log": [dict(r) for r in con.execute(
            "SELECT ts, source, status, message FROM import_log ORDER BY id DESC LIMIT 20")],
    }
    _write(os.path.join(data_dir, "meta.json"), meta)
    stats["seconds"] = round(time.time() - t0, 1)
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(data_dir) for f in fs)
    stats["data_mb"] = round(size / 1048576, 1)
    log(f"Export fertig: {stats['houses']} Häuser, {stats['items']} Items, "
        f"{stats['snap_files']} Snapshot-Dateien, {stats['data_mb']} MB in {stats['seconds']} s → {out_dir}")
    return stats
