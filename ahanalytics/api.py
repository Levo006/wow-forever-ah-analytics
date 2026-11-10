"""JSON-API: reine Funktionen (con, params) -> dict. Vom HTTP-Server aufgerufen."""
from __future__ import annotations

import json
import os

from . import db, metrics
from .itemmeta import CLASS_NAMES, QUALITY_NAMES

RANGES = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400, "all": None}
PRICE_METRICS = ("min", "p10", "median", "mean")


class BadRequest(ValueError):
    pass


class NotFound(LookupError):
    pass


# ---------------------------------------------------------------- Helfer

def _int(params, key, default=None, lo=None, hi=None):
    v = params.get(key)
    if v in (None, ""):
        return default
    try:
        v = int(float(v))
    except ValueError as exc:
        raise BadRequest(f"Parameter {key} muss eine Zahl sein") from exc
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _float(params, key, default=None):
    v = params.get(key)
    if v in (None, ""):
        return default
    try:
        return float(v)
    except ValueError as exc:
        raise BadRequest(f"Parameter {key} muss eine Zahl sein") from exc


def _ids(params, key):
    v = params.get(key)
    if not v:
        return []
    try:
        return [int(x) for x in str(v).split(",") if x.strip()]
    except ValueError as exc:
        raise BadRequest(f"Parameter {key}: kommagetrennte IDs erwartet") from exc


def _house_filter(params, alias="s"):
    """WHERE-Teil für Häuser: house=ID[,ID] oder region/faction/source."""
    where, args = [], []
    hids = _ids(params, "house")
    if hids:
        where.append(f"{alias}.house_id IN ({','.join('?' * len(hids))})")
        args += hids
    for key in ("region", "faction", "source"):
        if params.get(key):
            where.append(f"{alias}.house_id IN (SELECT id FROM houses WHERE {key} = ?)")
            args.append(params[key])
    return where, args


def _item_row(r) -> dict:
    d = dict(r)
    if d.get("name") is None and "item_id" in d:
        d["name"] = f"Item #{d['item_id']}"
    return d


def _pct(a, b):
    if a is None or b in (None, 0):
        return None
    return (a / b - 1) * 100


def house_latest(con, house_id):
    r = con.execute("SELECT MAX(ts) FROM snapshots WHERE house_id=?", (house_id,)).fetchone()
    return r[0] if r else None


def _default_house(con):
    r = con.execute("""SELECT h.id FROM houses h JOIN snapshots s ON s.house_id=h.id
                       GROUP BY h.id ORDER BY COUNT(*) DESC, MAX(s.ts) DESC LIMIT 1""").fetchone()
    return r[0] if r else None


def _resolve_house(con, params):
    hid = _int(params, "house")
    if hid is None:
        hid = _default_house(con)
    if hid is None:
        raise NotFound("Keine Daten vorhanden – bitte zuerst Daten importieren.")
    if not con.execute("SELECT 1 FROM houses WHERE id=?", (hid,)).fetchone():
        raise NotFound(f"Auktionshaus {hid} unbekannt")
    return hid


SUMMARY_COLS = """
  s.house_id, s.item_id, s.first_ts, s.last_ts, s.points, s.last_min, s.last_median, s.last_qty,
  s.last_auctions, s.prev24_min, s.prev24_median, s.prev7d_min, s.prev7d_median,
  s.avg7_min, s.avg30_min, s.avg7_median, s.avg30_median, s.avg7_qty, s.cv7_min,
  s.low30_min, s.high30_min,
  i.name, i.quality, i.class_id, i.class_name, i.subclass_id, i.subclass_name, i.icon,
  i.item_level, i.is_commodity, i.meta_status,
  h.name AS house_name, h.region, h.faction, h.granularity,
  CASE WHEN s.prev24_min > 0 THEN (1.0*s.last_min/s.prev24_min - 1)*100 END AS chg24_min,
  CASE WHEN s.prev7d_min > 0 THEN (1.0*s.last_min/s.prev7d_min - 1)*100 END AS chg7d_min,
  CASE WHEN s.prev24_median > 0 THEN (1.0*s.last_median/s.prev24_median - 1)*100 END AS chg24_median,
  CASE WHEN s.prev7d_median > 0 THEN (1.0*s.last_median/s.prev7d_median - 1)*100 END AS chg7d_median,
  COALESCE(s.last_min, 0) * COALESCE(s.last_qty, 0) AS market_value
"""
SUMMARY_FROM = """
  FROM item_summary s JOIN items i ON i.id = s.item_id JOIN houses h ON h.id = s.house_id
"""


# ------------------------------------------------------------ Endpunkte

def status(con, params, cfg=None, collector=None):
    cfg = cfg or {}
    bn = cfg.get("battlenet", {})
    from .sources import auctionator
    av = cfg.get("auctionator", {})
    paths = [av["path"]] if av.get("path") else auctionator.find_savedvariables(
        av.get("wow_root") or r"G:\World of Warcraft\_forever_")
    db_path = cfg.get("server", {}).get("db_path")
    return {
        "data_version": db.data_version(con),
        "battlenet_configured": bool(bn.get("client_id") and bn.get("client_secret")),
        "battlenet_regions": bn.get("regions"),
        "auctionator_paths": [{"path": p, "exists": os.path.exists(p),
                               "mtime": int(os.path.getmtime(p)) if os.path.exists(p) else None}
                              for p in paths],
        "db_size": os.path.getsize(db_path) if db_path and os.path.exists(db_path) else None,
        "counts": dict(con.execute(
            """SELECT (SELECT COUNT(*) FROM houses) houses, (SELECT COUNT(*) FROM items) items,
                      (SELECT COUNT(*) FROM snapshots) snapshots,
                      (SELECT COUNT(*) FROM item_summary) series""").fetchone()),
        "collector": collector.state() if collector else None,
    }


def houses(con, params):
    rows = con.execute(
        """SELECT h.*, COUNT(s.id) snapshots, MIN(s.ts) first_ts, MAX(s.ts) last_ts,
                  (SELECT COUNT(*) FROM item_summary x WHERE x.house_id = h.id) items
           FROM houses h LEFT JOIN snapshots s ON s.house_id = h.id
           GROUP BY h.id ORDER BY h.region, h.faction, h.name""").fetchall()
    return {"houses": [dict(r) for r in rows], "default": _default_house(con)}


def overview(con, params):
    hid = _resolve_house(con, params)
    k = con.execute(
        f"""SELECT COUNT(*) items, SUM(COALESCE(last_min,0)*COALESCE(last_qty,0)) market_value,
                   SUM(last_qty) quantity, SUM(last_auctions) auctions,
                   AVG(CASE WHEN prev24_min>0 THEN 1.0*last_min/prev24_min-1 END)*100 avg_chg24,
                   AVG(CASE WHEN prev7d_min>0 THEN 1.0*last_min/prev7d_min-1 END)*100 avg_chg7d
            FROM item_summary WHERE house_id=?""", (hid,)).fetchone()
    snaps = con.execute(
        """SELECT ts, item_count, auction_count, total_value FROM snapshots
           WHERE house_id=? ORDER BY ts DESC LIMIT 400""", (hid,)).fetchall()
    snaps = [dict(r) for r in reversed(snaps)]
    house = dict(con.execute("SELECT * FROM houses WHERE id=?", (hid,)).fetchone())
    p = {"house": str(hid), "limit": "6"}
    return {
        "house": house,
        "kpis": dict(k),
        "last_ts": snaps[-1]["ts"] if snaps else None,
        "snapshot_count": con.execute("SELECT COUNT(*) FROM snapshots WHERE house_id=?",
                                      (hid,)).fetchone()[0],
        "market_series": snaps,
        "categories": classes(con, {"house": str(hid)})["classes"],
        "movers": movers(con, {**p, "window": "24h"}),
        "deals": deals(con, {**p, "threshold": "15"}),
        "most_volatile": [_item_row(r) for r in con.execute(
            f"SELECT {SUMMARY_COLS} {SUMMARY_FROM} WHERE s.house_id=? AND s.avg7_qty >= 1 "
            f"ORDER BY s.cv7_min DESC LIMIT 6", (hid,))],
    }


def classes(con, params):
    hid = _resolve_house(con, params)
    rows = con.execute(
        """SELECT i.class_id, i.class_name, i.subclass_id, i.subclass_name,
                  COUNT(*) items,
                  SUM(COALESCE(s.last_min,0)*COALESCE(s.last_qty,0)) market_value,
                  SUM(s.last_qty) quantity,
                  AVG(CASE WHEN s.prev24_min>0 THEN 1.0*s.last_min/s.prev24_min-1 END)*100 chg24,
                  AVG(CASE WHEN s.prev7d_min>0 THEN 1.0*s.last_min/s.prev7d_min-1 END)*100 chg7d
           FROM item_summary s JOIN items i ON i.id = s.item_id
           WHERE s.house_id = ?
           GROUP BY i.class_id, i.subclass_id ORDER BY i.class_id, i.subclass_id""",
        (hid,)).fetchall()
    out: dict = {}
    for r in rows:
        cid = r["class_id"] if r["class_id"] is not None else -1
        c = out.setdefault(cid, {
            "class_id": cid,
            "class_name": r["class_name"] or CLASS_NAMES.get(cid, "Ohne Metadaten"),
            "items": 0, "market_value": 0, "quantity": 0, "subclasses": [], "_w24": [], "_w7": []})
        c["items"] += r["items"]
        c["market_value"] += r["market_value"] or 0
        c["quantity"] += r["quantity"] or 0
        if r["chg24"] is not None:
            c["_w24"].append((r["chg24"], r["items"]))
        if r["chg7d"] is not None:
            c["_w7"].append((r["chg7d"], r["items"]))
        c["subclasses"].append({
            "subclass_id": r["subclass_id"],
            "subclass_name": r["subclass_name"] or ("Unbekannt" if r["subclass_id"] is None
                                                   else f"Unterklasse {r['subclass_id']}"),
            "items": r["items"], "market_value": r["market_value"], "quantity": r["quantity"],
            "chg24": r["chg24"], "chg7d": r["chg7d"]})
    result = []
    for c in out.values():
        for key, src in (("chg24", "_w24"), ("chg7d", "_w7")):
            w = c.pop(src)
            n = sum(x[1] for x in w)
            c[key] = sum(a * b for a, b in w) / n if n else None
        result.append(c)
    result.sort(key=lambda c: -(c["market_value"] or 0))
    return {"house_id": hid, "classes": result}


SEARCH_SORTS = {
    "name": "i.name COLLATE NOCASE", "price": "s.last_min", "median": "s.last_median",
    "qty": "s.last_qty", "avg_qty": "s.avg7_qty", "chg24": "chg24_min", "chg7d": "chg7d_min",
    "vol": "s.cv7_min", "value": "market_value", "quality": "i.quality", "id": "s.item_id",
    "updated": "s.last_ts", "avg30": "s.avg30_min",
}


def search(con, params):
    where, args = _house_filter(params)
    q = (params.get("q") or "").strip()
    if q:
        if q.isdigit():
            where.append("(s.item_id = ? OR i.name LIKE ?)")
            args += [int(q), f"%{q}%"]
        else:
            for word in q.split():
                where.append("i.name LIKE ?")
                args.append(f"%{word}%")
    for key, col in (("class", "i.class_id"), ("subclass", "i.subclass_id")):
        v = params.get(key)
        if v not in (None, ""):
            if v == "-1":
                where.append(f"{col} IS NULL")
            else:
                where.append(f"{col} = ?")
                args.append(_int(params, key))
    quals = _ids(params, "quality")
    if quals:
        where.append(f"i.quality IN ({','.join('?' * len(quals))})")
        args += quals
    for key, expr, op in (("min_price", "s.last_min", ">="), ("max_price", "s.last_min", "<="),
                          ("min_qty", "s.last_qty", ">="), ("max_qty", "s.last_qty", "<="),
                          ("min_avg_qty", "s.avg7_qty", ">="),
                          ("min_chg", "chg24_min", ">="), ("max_chg", "chg24_min", "<=")):
        v = _float(params, key)
        if v is not None:
            where.append(f"{expr} {op} ?")
            args.append(v)
    if params.get("commodity") in ("0", "1"):
        where.append("COALESCE(i.is_commodity,0) = ?")
        args.append(int(params["commodity"]))
    sort = SEARCH_SORTS.get(params.get("sort") or "value", "market_value")
    direction = "ASC" if params.get("dir") == "asc" else "DESC"
    page = _int(params, "page", 1, lo=1)
    size = _int(params, "page_size", 50, lo=1, hi=500)
    wsql = ("WHERE " + " AND ".join(where)) if where else ""
    base = f"{SUMMARY_FROM} {wsql}"
    total = con.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    order = f"{sort} IS NULL, {sort} {direction}, s.item_id"
    order_args: list = []
    if params.get("sort") == "relevance" and q:
        # Treffer am Namensanfang zuerst, dann Wortanfang, dann beliebig; innerhalb nach Marktwert
        order = ("CASE WHEN i.name LIKE ? THEN 0 WHEN i.name LIKE ? THEN 1 ELSE 2 END, "
                 "market_value DESC, s.item_id")
        order_args = [f"{q}%", f"% {q}%"]
    rows = con.execute(
        f"SELECT {SUMMARY_COLS} {base} ORDER BY {order} LIMIT ? OFFSET ?",
        args + order_args + [size, (page - 1) * size]).fetchall()
    items = [_item_row(r) for r in rows]
    if params.get("spark") == "1" and items:
        _attach_sparklines(con, items)
    return {"total": total, "page": page, "page_size": size, "items": items}


def _attach_sparklines(con, items, days=14):
    """Hängt je Zeile eine Tages-Sparkline des Mindestpreises an (1 Query pro Haus)."""
    by_house: dict[int, list] = {}
    for it in items:
        by_house.setdefault(it["house_id"], []).append(it)
    for hid, its in by_house.items():
        latest = house_latest(con, hid) or 0
        ids = [it["item_id"] for it in its]
        rows = con.execute(
            f"""SELECT item_id, CAST((ts - ?) / 86400 AS INT) d, AVG(min) v FROM prices
                WHERE house_id = ? AND item_id IN ({','.join('?' * len(ids))}) AND ts > ?
                GROUP BY item_id, d ORDER BY item_id, d""",
            [latest - days * 86400, hid] + ids + [latest - days * 86400]).fetchall()
        sp: dict[int, list] = {}
        for r in rows:
            sp.setdefault(r["item_id"], []).append(round(r["v"]) if r["v"] is not None else None)
        for it in its:
            it["spark"] = sp.get(it["item_id"], [])


def item(con, item_id, params):
    meta = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not meta:
        raise NotFound(f"Item {item_id} unbekannt")
    rows = con.execute(f"SELECT {SUMMARY_COLS} {SUMMARY_FROM} WHERE s.item_id=? "
                       f"ORDER BY h.region, h.faction, h.name", (item_id,)).fetchall()
    meta = dict(meta)
    meta["name"] = meta["name"] or f"Item #{item_id}"
    meta["quality_name"] = QUALITY_NAMES.get(meta["quality"])
    return {"item": meta, "houses": [_item_row(r) for r in rows]}


def _range_bounds(con, hid, rng):
    if rng not in RANGES:
        raise BadRequest(f"Zeitraum {rng!r} ungültig (24h, 7d, 30d, 90d, all)")
    latest = house_latest(con, hid)
    span = RANGES[rng]
    lo = (latest - span) if (latest is not None and span) else 0
    return lo, latest


def _history_rows(con, hid, item_id, lo, hi=None):
    q = """SELECT ts, min, p10, median, mean, max, quantity, auctions FROM prices
           WHERE house_id=? AND item_id=? AND ts >= ?"""
    args = [hid, item_id, lo]
    if hi is not None:
        q += " AND ts <= ?"
        args.append(hi)
    return [dict(r) for r in con.execute(q + " ORDER BY ts", args)]


def history(con, item_id, params):
    hid = _resolve_house(con, params)
    lo, hi = _range_bounds(con, hid, params.get("range", "7d"))
    rows = _history_rows(con, hid, item_id, lo)
    max_points = _int(params, "max_points", 720, lo=20, hi=5000)
    return {"house_id": hid, "item_id": item_id, "range": params.get("range", "7d"),
            "from": lo, "to": hi, "raw_points": len(rows),
            "points": metrics.bucket(rows, max_points)}


def analysis(con, item_id, params):
    hid = _resolve_house(con, params)
    metric = params.get("metric", "min")
    if metric not in PRICE_METRICS:
        raise BadRequest("metric muss min, p10, median oder mean sein")
    lo, _ = _range_bounds(con, hid, params.get("range", "30d"))
    rows = _history_rows(con, hid, item_id, lo)
    series = [(r["ts"], r[metric]) for r in rows]
    if metric != "min" and not any(v for _, v in series):
        metric = "min"
        series = [(r["ts"], r["min"]) for r in rows]
    tz = _int(params, "tz")
    res = metrics.full_analysis(series, tz_offset_min=tz)
    qty = [(r["ts"], r["quantity"]) for r in rows]
    res["quantity"] = metrics.describe(qty)
    res["metric"] = metric
    res["points"] = len(series)
    return res


def item_snapshots(con, item_id, params):
    hid = _resolve_house(con, params)
    page = _int(params, "page", 1, lo=1)
    size = _int(params, "page_size", 50, lo=1, hi=500)
    total = con.execute("SELECT COUNT(*) FROM prices WHERE house_id=? AND item_id=?",
                        (hid, item_id)).fetchone()[0]
    rows = con.execute(
        """SELECT p.*, s.id snapshot_id,
                  EXISTS(SELECT 1 FROM ladders l WHERE l.snapshot_id = s.id AND l.item_id = p.item_id) has_ladder,
                  LAG(p.min) OVER (ORDER BY p.ts) prev_min
           FROM prices p LEFT JOIN snapshots s ON s.house_id = p.house_id AND s.ts = p.ts
           WHERE p.house_id=? AND p.item_id=? ORDER BY p.ts DESC LIMIT ? OFFSET ?""",
        (hid, item_id, size, (page - 1) * size)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["chg_min"] = _pct(d["min"], d["prev_min"])
        out.append(d)
    # LAG über die Seite hinaus korrigieren: letzter Eintrag hat sonst kein Vorgänger
    if out:
        last = out[-1]
        prev = con.execute("SELECT min FROM prices WHERE house_id=? AND item_id=? AND ts<? "
                           "ORDER BY ts DESC LIMIT 1", (hid, item_id, last["ts"])).fetchone()
        last["prev_min"] = prev[0] if prev else None
        last["chg_min"] = _pct(last["min"], last["prev_min"])
    return {"total": total, "page": page, "page_size": size, "rows": out}


def snapshot(con, house_id, ts, params):
    snap = con.execute("SELECT * FROM snapshots WHERE house_id=? AND ts=?", (house_id, ts)).fetchone()
    if not snap:
        raise NotFound("Snapshot nicht gefunden")
    snap = dict(snap)
    house = dict(con.execute("SELECT * FROM houses WHERE id=?", (house_id,)).fetchone())
    prev = con.execute("SELECT ts FROM snapshots WHERE house_id=? AND ts<? ORDER BY ts DESC LIMIT 1",
                       (house_id, ts)).fetchone()
    nxt = con.execute("SELECT ts FROM snapshots WHERE house_id=? AND ts>? ORDER BY ts LIMIT 1",
                      (house_id, ts)).fetchone()
    prev_ts = prev[0] if prev else None
    where = ["p.house_id = ?", "p.ts = ?"]
    args: list = [prev_ts, house_id, ts]
    q = (params.get("q") or "").strip()
    if q:
        where.append("(i.name LIKE ? OR CAST(p.item_id AS TEXT) = ?)")
        args += [f"%{q}%", q]
    if params.get("class") not in (None, ""):
        where.append("i.class_id = ?")
        args.append(_int(params, "class"))
    sorts = {"value": "value", "price": "p.min", "qty": "p.quantity", "chg": "chg",
             "name": "i.name COLLATE NOCASE", "auctions": "p.auctions"}
    sort = sorts.get(params.get("sort") or "value", "value")
    direction = "ASC" if params.get("dir") == "asc" else "DESC"
    page = _int(params, "page", 1, lo=1)
    size = _int(params, "page_size", 50, lo=1, hi=500)
    base = f"""FROM prices p JOIN items i ON i.id = p.item_id
               LEFT JOIN prices pp ON pp.house_id = p.house_id AND pp.item_id = p.item_id AND pp.ts = ?
               WHERE {' AND '.join(where)}"""
    total = con.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    rows = con.execute(
        f"""SELECT p.*, i.name, i.quality, i.icon, i.class_name, i.subclass_name,
                   COALESCE(p.min,0)*COALESCE(p.quantity,0) value, pp.min prev_min,
                   CASE WHEN pp.min > 0 THEN (1.0*p.min/pp.min - 1)*100 END chg
            {base} ORDER BY {sort} IS NULL, {sort} {direction} LIMIT ? OFFSET ?""",
        args + [size, (page - 1) * size]).fetchall()
    result = {"snapshot": snap, "house": house, "prev_ts": prev_ts, "next_ts": nxt[0] if nxt else None,
              "total": total, "page": page, "page_size": size,
              "items": [_item_row(r) for r in rows]}
    focus = _int(params, "item")
    if focus is not None:
        lad = con.execute("SELECT data FROM ladders WHERE snapshot_id=? AND item_id=?",
                          (snap["id"], focus)).fetchone()
        stats = con.execute("SELECT * FROM prices WHERE house_id=? AND item_id=? AND ts=?",
                            (house_id, focus, ts)).fetchone()
        meta = con.execute("SELECT * FROM items WHERE id=?", (focus,)).fetchone()
        result["focus"] = {
            "item": _item_row({**dict(meta), "item_id": focus}) if meta else {"item_id": focus},
            "stats": dict(stats) if stats else None,
            "ladder": json.loads(lad[0]) if lad else None,
        }
    return result


def compare(con, params):
    ids = _ids(params, "items")[:8]
    if not ids:
        raise BadRequest("Parameter items fehlt (z. B. items=2770,2771)")
    hid = _resolve_house(con, params)
    metric = params.get("metric", "min")
    if metric not in PRICE_METRICS + ("quantity",):
        raise BadRequest("Ungültige Kennzahl")
    lo, _ = _range_bounds(con, hid, params.get("range", "7d"))
    max_points = _int(params, "max_points", 400, lo=20, hi=3000)
    series = []
    for iid in ids:
        rows = _history_rows(con, hid, iid, lo)
        pts = metrics.bucket(rows, max_points)
        meta = con.execute("SELECT name, quality, icon FROM items WHERE id=?", (iid,)).fetchone()
        summ = con.execute(f"SELECT {SUMMARY_COLS} {SUMMARY_FROM} WHERE s.house_id=? AND s.item_id=?",
                           (hid, iid)).fetchone()
        vals = [(r["ts"], r.get(metric)) for r in rows]
        series.append({
            "item_id": iid,
            "name": (meta["name"] if meta and meta["name"] else f"Item #{iid}"),
            "quality": meta["quality"] if meta else None,
            "icon": meta["icon"] if meta else None,
            "points": [{"ts": p["ts"], "v": p.get(metric)} for p in pts],
            "summary": _item_row(summ) if summ else None,
            "trend": metrics.trend(vals),
            "volatility": metrics.volatility(vals),
            "describe": metrics.describe(vals),
        })
    # Korrelation der Log-Renditen (auf gemeinsamen Zeitstempeln)
    corr = []
    for a in series:
        row = []
        for b in series:
            row.append(_correlation(a["points"], b["points"]))
        corr.append(row)
    return {"house_id": hid, "metric": metric, "series": series, "correlation": corr}


def _correlation(pa, pb):
    da = {p["ts"]: p["v"] for p in pa if p["v"]}
    db_ = {p["ts"]: p["v"] for p in pb if p["v"]}
    common = sorted(set(da) & set(db_))
    if len(common) < 5:
        return None
    import math
    ra = [math.log(da[t2] / da[t1]) for t1, t2 in zip(common, common[1:])]
    rb = [math.log(db_[t2] / db_[t1]) for t1, t2 in zip(common, common[1:])]
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in ra))
    sb = math.sqrt(sum((x - mb) ** 2 for x in rb))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / (sa * sb)


def movers(con, params):
    hid = _resolve_house(con, params)
    window = params.get("window", "24h")
    metric = params.get("metric", "min")
    if window not in ("24h", "7d") or metric not in ("min", "median"):
        raise BadRequest("window ∈ {24h, 7d}, metric ∈ {min, median}")
    col = f"chg{'24' if window == '24h' else '7d'}_{metric}"
    where = ["s.house_id = ?", f"{col} IS NOT NULL"]
    args: list = [hid]
    min_qty = _float(params, "min_qty", 0)
    if min_qty:
        where.append("COALESCE(s.avg7_qty, s.last_qty) >= ?")
        args.append(min_qty)
    min_price = _float(params, "min_price")
    if min_price:
        where.append("s.last_min >= ?")
        args.append(min_price)
    if params.get("class") not in (None, ""):
        where.append("i.class_id = ?")
        args.append(_int(params, "class"))
    limit = _int(params, "limit", 25, lo=1, hi=200)
    base = f"SELECT {SUMMARY_COLS} {SUMMARY_FROM} WHERE {' AND '.join(where)}"
    gain = con.execute(f"SELECT * FROM ({base}) WHERE {col} > 0 ORDER BY {col} DESC LIMIT ?",
                       args + [limit]).fetchall()
    lose = con.execute(f"SELECT * FROM ({base}) WHERE {col} < 0 ORDER BY {col} ASC LIMIT ?",
                       args + [limit]).fetchall()
    return {"house_id": hid, "window": window, "metric": metric, "column": col,
            "gainers": [_item_row(r) for r in gain], "losers": [_item_row(r) for r in lose]}


def deals(con, params):
    hid = _resolve_house(con, params)
    basis = params.get("basis", "avg7")
    if basis not in ("avg7", "avg30"):
        raise BadRequest("basis ∈ {avg7, avg30}")
    ref = f"s.{basis}_median" if params.get("ref") == "median" else f"s.{basis}_min"
    threshold = _float(params, "threshold", 20.0) / 100
    where = ["s.house_id = ?", f"{ref} > 0", "s.last_min IS NOT NULL",
             f"s.last_min <= {ref} * (1 - ?)", "s.points >= 3"]
    args: list = [hid, threshold]
    min_qty = _float(params, "min_qty", 0)
    if min_qty:
        where.append("COALESCE(s.avg7_qty, 0) >= ?")
        args.append(min_qty)
    if params.get("class") not in (None, ""):
        where.append("i.class_id = ?")
        args.append(_int(params, "class"))
    limit = _int(params, "limit", 50, lo=1, hi=500)
    rows = con.execute(
        f"""SELECT {SUMMARY_COLS}, {ref} AS ref_price,
                   (1 - 1.0*s.last_min / {ref}) * 100 AS discount,
                   ({ref} - s.last_min) AS saving
            {SUMMARY_FROM} WHERE {' AND '.join(where)}
            ORDER BY discount DESC LIMIT ?""", args + [limit]).fetchall()
    return {"house_id": hid, "basis": basis, "threshold": threshold * 100,
            "deals": [_item_row(r) for r in rows]}


def import_log(con, params):
    rows = con.execute("SELECT * FROM import_log ORDER BY id DESC LIMIT 50").fetchall()
    return {"log": [dict(r) for r in rows]}
