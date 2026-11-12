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

from . import api, db

STATIC_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

SUMMARY_FIELDS = [
    "item_id", "first_ts", "last_ts", "points", "last_min", "last_median", "last_qty", "last_auctions",
    "prev24_min", "prev24_median", "prev7d_min", "prev7d_median", "avg7_min", "avg30_min",
    "avg7_median", "avg30_median", "avg7_qty", "cv7_min", "low30_min", "high30_min",
    "name", "name_en", "quality", "class_id", "class_name", "subclass_id", "subclass_name", "icon",
    "item_level", "is_commodity", "chg24_min", "chg7d_min", "chg24_median", "chg7d_median",
    "market_value", "spark",
]
ITEM_HOUSE_FIELDS = [
    "house_id", "first_ts", "last_ts", "points", "last_min", "last_median", "last_qty", "last_auctions",
    "prev24_min", "prev7d_min", "avg7_min", "avg30_min", "avg7_qty", "cv7_min", "low30_min", "high30_min",
    "chg24_min", "chg7d_min", "chg24_median", "chg7d_median", "market_value",
]
PRICE_FIELDS = ["ts", "min", "p10", "median", "mean", "max", "quantity", "auctions", "n"]
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


H = 3600
# Auflösung der Historie in den statischen Dateien: (bis zu diesem Alter, Bucket-Größe; 0 = Original)
TIERS_MARKET = [(2 * 86400, 0), (7 * 86400, 4 * H), (None, 86400)]   # Alle AHs / Neutral
TIERS_HOUSE = [(2 * 86400, 0), (7 * 86400, 6 * H)]                 # Einzelhäuser: 48 h stündlich, bis 7 Tage 6-h-Mittel
HOUSE_FIELDS = ["ts", "min", "median", "quantity", "n"]


def _bucket_key(ts, size):
    if size >= 86400:  # Kalendertag in Ortszeit, Zeitpunkt 12 Uhr
        return int(time.mktime(time.localtime(ts)[:3] + (12, 0, 0, 0, 0, -1)))
    return ts // size * size + size // 2


def _merge(ts, rs):
    def avg(k):
        v = [r.get(k) for r in rs if r.get(k) is not None]
        return int(round(sum(v) / len(v))) if v else None
    maxs = [r["max"] for r in rs if r.get("max") is not None]
    return {"ts": ts, "min": avg("min"), "p10": avg("p10"), "median": avg("median"),
            "mean": avg("mean"), "max": max(maxs) if maxs else None,
            "quantity": avg("quantity"), "auctions": avg("auctions"),
            "n": sum(r.get("n", 1) for r in rs)}


def compact_history(rows, latest, hourly_days=7, tiers=None):
    """Verdichtet die Historie nach Alter: jüngste Punkte unverändert, ältere gemittelt.

    ``tiers``: [(max. Alter in s oder None, Bucket-Größe in s; 0 = Original), …] –
    Punkte älter als die letzte Stufe fallen weg. Ohne ``tiers``: die letzten
    ``hourly_days`` Tage unverändert, davor Tagesmittel. Spalte ``n`` = Anzahl
    zusammengefasster Snapshots (1 = Original, Tageswerte aus der DB zählen 24).
    """
    if tiers is None:
        tiers = [(hourly_days * 86400, 0), (None, 86400)]
    rows = [{**r, "n": 24 if db.is_daily_ts(r["ts"]) else 1} for r in rows]
    out, groups = [], {}
    for r in rows:
        age = latest - r["ts"]
        for limit, size in tiers:
            if limit is None or age <= limit:
                break
        else:
            continue  # zu alt
        if size == 0 or (r["n"] > 1 and size == 86400):
            out.append(r)  # Original bzw. bereits Tageswert
            continue
        groups.setdefault((size, _bucket_key(r["ts"], size)), []).append(r)
    for (size, key), rs in groups.items():
        out.append(rs[0] if len(rs) == 1 else _merge(key, rs))
    return sorted(out, key=lambda r: r["ts"])


def export_site(con, out_dir: str, history_days: int = 180, snapshot_files: int = 6,
                repo_url: str = "", ladder_files: int = 3, house_snapshot_files: int = 1,
                log=print) -> dict:
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
        n_files = house_snapshot_files if h.get("kind") == "haus" else snapshot_files
        with_file = {s["ts"] for s in snaps[-n_files:]} if n_files else set()
        with_ladder = {s["ts"] for s in snaps[-min(ladder_files, n_files):]} if n_files else set()
        for s in snaps:
            s["file"] = 1 if s["ts"] in with_file else 0
            s["has_ladder"] = 1 if s["has_ladder"] and s["ts"] in with_ladder else 0
        _write(os.path.join(hdir, "snapshots.json"), snaps)
        prev_ts = None
        for s in snaps:
            if s["file"]:
                prices = [dict(r) for r in con.execute(
                    """SELECT p.*, pp.min prev_min FROM prices p
                       LEFT JOIN prices pp ON pp.house_id=p.house_id AND pp.item_id=p.item_id AND pp.ts=?
                       WHERE p.house_id=? AND p.ts=?""", (prev_ts, hid, s["ts"]))]
                ladders = {str(r[0]): json.loads(r[1]) for r in con.execute(
                    "SELECT item_id, data FROM ladders WHERE snapshot_id=?", (s["id"],))}                     if s["has_ladder"] else {}
                _write(os.path.join(hdir, "snap", f"{s['ts']}.json"),
                       {**_columnar(SNAP_FIELDS, prices), "ladders": ladders})
                stats["snap_files"] += 1
            prev_ts = s["ts"]
        h["latest"] = latest

    # Items: Metadaten + verdichtete Historie je Haus (Analysen rechnet der Browser)
    house_by_id = {h["id"]: h for h in houses}
    for n, iid in enumerate(sorted(item_ids)):
        info = api.item(con, iid, {})
        hist = {}
        for hs in info["houses"]:
            hid = hs["house_id"]
            house = house_by_id.get(hid)
            if not house:
                continue
            latest = house["latest"] or 0
            if house.get("kind") == "haus":
                rows = compact_history(api._history_rows(con, hid, iid, latest - 7 * 86400), latest,
                                       tiers=TIERS_HOUSE)
                hist[str(hid)] = _columnar(HOUSE_FIELDS, rows)
            elif house.get("granularity") == "day":
                hist[str(hid)] = _columnar(PRICE_FIELDS, compact_history(
                    api._history_rows(con, hid, iid, latest - history_days * 86400), latest,
                    tiers=[(None, 0)]))
            else:
                rows = compact_history(api._history_rows(con, hid, iid, latest - history_days * 86400),
                                       latest, tiers=TIERS_MARKET)
                hist[str(hid)] = _columnar(PRICE_FIELDS, rows)
        info["item"].pop("meta_checked", None)
        for k in ("en_checked", "bn_checked", "meta_status"):
            info["item"].pop(k, None)
        hs_rows = info["houses"]
        _write(os.path.join(data_dir, "item", f"{iid}.json"),
               {"item": info["item"], "houses": _columnar(ITEM_HOUSE_FIELDS, hs_rows), "history": hist})
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
