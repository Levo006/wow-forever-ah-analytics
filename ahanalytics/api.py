"""JSON-API: reine Funktionen (con, params) -> dict. Vom HTTP-Server aufgerufen."""
from __future__ import annotations

import json
import os
import time

from . import db, metrics, itemnames
from . import liquidity as liquidity_
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
    d = itemnames.enrich(r)
    if "market_ts" in d:
        d["price_basis"] = "robust_reference" if db.house_kind(d.get("market_source", ""), d.get("market_ext_id", "")) in db.GROUPS else "lowest_ask"
        if d.get("last_lowest") is None and d["price_basis"] == "lowest_ask" and d.get("granularity") != "day":
            d["last_lowest"] = d.get("last_min")
        d["is_current"] = bool(d.get("last_ts") and d["last_ts"] == d["market_ts"])
        d["current_quantity"] = d.get("last_qty") if d["is_current"] else (0 if d.get("market_complete") else None)
        d["availability"] = "observed" if d["is_current"] else ("not_observed" if d.get("market_complete") else "unknown")
        if not d["is_current"]:
            d["market_value"] = 0
    return d


def _pct(a, b):
    if a is None or b in (None, 0):
        return None
    return (a / b - 1) * 100


def house_latest(con, house_id):
    r = con.execute("SELECT MAX(ts) FROM snapshots WHERE house_id=?", (house_id,)).fetchone()
    return r[0] if r else None


def _default_house(con):
    # bevorzugt: Sammelmarkt Allianz mit aktuellen Daten
    for (hid, src, ext) in con.execute(
            "SELECT h.id, h.source, h.ext_id FROM houses h WHERE EXISTS "
            "(SELECT 1 FROM snapshots s WHERE s.house_id=h.id) ORDER BY h.id"):
        if db.house_kind(src, ext) == "allianz":
            return hid
    return _default_house_fallback(con)


def _default_house_fallback(con):
    # bevorzugt: aktuelle Daten (letzte 2 Tage), dann die meisten Items, dann die meisten Snapshots
    r = con.execute("""SELECT h.id FROM houses h JOIN snapshots s ON s.house_id=h.id
                       GROUP BY h.id
                       ORDER BY MAX(s.ts) > (SELECT MAX(ts) FROM snapshots) - 2*86400 DESC,
                                (SELECT COUNT(*) FROM item_summary x WHERE x.house_id = h.id) DESC,
                                COUNT(*) DESC LIMIT 1""").fetchone()
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
  s.last_lowest, s.prev24_ts, s.prev7d_ts, h.source AS market_source, h.ext_id AS market_ext_id,
  s.house_id, s.item_id, s.first_ts, s.last_ts, s.points, s.last_min, s.last_median, s.last_qty,
  s.last_auctions, s.prev24_min, s.prev24_median, s.prev7d_min, s.prev7d_median,
  s.avg7_min, s.avg30_min, s.avg7_median, s.avg30_median, s.avg7_qty, s.cv7_min,
  s.low30_min, s.high30_min,
  i.name, i.name_en, i.quality, i.class_id, i.class_name, i.subclass_id, i.subclass_name, i.icon,
  i.item_level, i.required_level, i.is_commodity, i.meta_status,
  i.catalog_build, i.catalog_state, i.catalog_test, i.binding, i.catalog_aliases,
  va.alias AS verified_alias,
  lq.presence AS liq_presence, lq.events AS liq_events, lq.vanish AS liq_vanish,
  lq.units AS liq_units, lq.rate AS liq_rate, lq.score AS liq_score, lq.computed_at AS liq_ts,
  h.name AS house_name, h.region, h.faction, h.granularity,
  (SELECT MAX(ts) FROM snapshots z WHERE z.house_id=s.house_id) AS market_ts,
  COALESCE((SELECT complete FROM snapshots z WHERE z.house_id=s.house_id ORDER BY ts DESC LIMIT 1),0) AS market_complete,
  CASE WHEN s.prev24_min > 0 THEN (1.0*s.last_min/s.prev24_min - 1)*100 END AS chg24_min,
  CASE WHEN s.prev7d_min > 0 THEN (1.0*s.last_min/s.prev7d_min - 1)*100 END AS chg7d_min,
  CASE WHEN s.prev24_median > 0 THEN (1.0*s.last_median/s.prev24_median - 1)*100 END AS chg24_median,
  CASE WHEN s.prev7d_median > 0 THEN (1.0*s.last_median/s.prev7d_median - 1)*100 END AS chg7d_median,
  CASE WHEN s.last_ts=(SELECT MAX(ts) FROM snapshots WHERE house_id=s.house_id) THEN COALESCE(s.last_min, 0) * COALESCE(s.last_qty, 0) ELSE 0 END AS market_value
"""
# Handelsrelevante Items: Handelswaren oder Qualitaet ab ungewoehnlich (kein Grau-/Weissschrott).
RELEVANT_WHERE = "(COALESCE(i.quality, 1) >= 2 OR (i.is_commodity = 1 AND COALESCE(i.quality, 1) >= 1))"

SUMMARY_FROM = """
  FROM item_summary s JOIN items i ON i.id = s.item_id JOIN houses h ON h.id = s.house_id
  LEFT JOIN verified_aliases va ON va.item_id = s.item_id AND va.status = 'ok'
  LEFT JOIN item_liquidity lq ON lq.house_id = s.house_id AND lq.item_id = s.item_id
"""


# ------------------------------------------------------------ Endpunkte

def status(con, params, cfg=None, collector=None):
    cfg = cfg or {}
    bn = cfg.get("battlenet", {})
    from .sources import auctionator
    from . import catalog
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
        "catalog_coverage": catalog.coverage(con),
        "market_runs": market_runs(con),
    }


def market_runs(con, limit=3):
    return [dict(json.loads(r["manifest"]), id=r["id"]) for r in con.execute(
        "SELECT id,manifest FROM market_runs ORDER BY id DESC LIMIT ?", (limit,))]


def market_quality(con, hid):
    row = con.execute("SELECT ts,complete,run_id,source_min_ts,source_max_ts FROM snapshots WHERE house_id=? ORDER BY ts DESC LIMIT 1", (hid,)).fetchone()
    result = dict(row) if row else {"complete":None, "ts":None}
    recent = con.execute("SELECT r.id,r.status FROM market_runs r JOIN houses h ON h.region=r.region WHERE h.id=? ORDER BY r.id DESC LIMIT 1",(hid,)).fetchone()
    if recent: result.update(latest_run_id=recent["id"],latest_run_status=recent["status"])
    return result


def houses(con, params):
    rows = con.execute(
        """SELECT h.*, COUNT(s.id) snapshots, MIN(s.ts) first_ts, MAX(s.ts) last_ts,
                  (SELECT COUNT(*) FROM item_summary x WHERE x.house_id = h.id) items
           FROM houses h LEFT JOIN snapshots s ON s.house_id = h.id
           GROUP BY h.id ORDER BY h.region, h.faction, h.name""").fetchall()
    return {"houses": decorate_houses([dict(r) for r in rows]), "default": _default_house(con)}


_HOUSE_CACHE: dict = {}


def _house_info(con) -> dict:
    ver = db.data_version(con)
    if _HOUSE_CACHE.get("ver") != ver or _HOUSE_CACHE.get("con") is not con:
        _HOUSE_CACHE.update(ver=ver, con=con, info={h["id"]: h for h in houses(con, {})["houses"]})
    return _HOUSE_CACHE["info"]


def decorate_houses(hs: list[dict]) -> list[dict]:
    """Ergänzt kind (allianz|horde|neutral|haus|scan), verbund (A, B, …), group (Sammelmarkt
    der eigenen Fraktion) und lesbare Bezeichnungen.

    Die Battle.net-Häuser von Forever kommen in Dreiergruppen (Allianz/Horde/Neutral
    eines Server-Verbunds): 110/113/116, 111/114/117, 112/115/118, danach 119–121,
    123–125 usw. Die Buchstaben werden nur unter den Verbünden mit Daten vergeben.
    """
    def vkey(h):
        try:
            n = int(str(h["ext_id"]).rsplit("-", 1)[-1])
        except ValueError:
            return None
        if 110 <= n <= 118:
            return (n - 110) % 3
        if n >= 119:
            return 3 + (n - 119) // 4
        return None

    for h in hs:
        h["kind"] = db.house_kind(h.get("source", ""), h.get("ext_id", ""))
    keys = sorted({vkey(h) for h in hs if h["kind"] == "haus" and h.get("snapshots") and vkey(h) is not None})
    letters = {k: chr(65 + i) for i, k in enumerate(keys)}
    for h in hs:
        k = h["kind"]
        if k in ("allianz", "horde"):
            h["label"], h["short"] = f"{h['faction']} · alle Verbünde", h["faction"]
        elif k == "neutral":
            h["label"], h["short"] = "Neutral · Schwarzmeerräuber", "Neutral"
        elif k == "legacy":
            h["label"] = h["short"] = h["name"]
        elif k == "scan":
            h["label"], h["short"] = f"Eigene Scans · {h['name']}", "Eigene Scans"
        else:
            v = letters.get(vkey(h))
            h["verbund"] = v
            num = str(h["ext_id"]).rsplit("-", 1)[-1]
            fac = h.get("faction") or "?"
            h["label"] = f"{fac} · Verbund {v} (#{num})" if v else h["name"]
            h["short"] = f"{fac} {v}" if v else h["name"]
            h["group"] = db.group_for_faction(fac) if h.get("source") == "battlenet" else None
    order = {"allianz": 0, "horde": 1, "neutral": 2, "haus": 3, "scan": 4, "legacy": 5}
    fo = {"Allianz": 0, "Horde": 1, "Neutral": 2}
    hs.sort(key=lambda h: (order[h["kind"]], h.get("verbund") or "", fo.get(h.get("faction"), 9), h["name"]))
    return hs


def overview(con, params):
    hid = _resolve_house(con, params)
    k = con.execute(
        f"""SELECT COUNT(*) items, SUM(COALESCE(last_min,0)*COALESCE(last_qty,0)) market_value,
                   SUM(last_qty) quantity, SUM(last_auctions) auctions,
                   AVG(CASE WHEN prev24_min>0 THEN 1.0*last_min/prev24_min-1 END)*100 avg_chg24,
                   AVG(CASE WHEN prev7d_min>0 THEN 1.0*last_min/prev7d_min-1 END)*100 avg_chg7d
            FROM item_summary WHERE house_id=? AND last_ts=(SELECT MAX(ts) FROM snapshots WHERE house_id=?)""", (hid, hid)).fetchone()
    snaps = con.execute(
        """SELECT ts, item_count, auction_count, total_value FROM snapshots
           WHERE house_id=? ORDER BY ts DESC LIMIT 400""", (hid,)).fetchall()
    snaps = [dict(r) for r in reversed(snaps)]
    house = dict(con.execute("SELECT * FROM houses WHERE id=?", (hid,)).fetchone())
    p = {"house": str(hid), "limit": "6"}
    comparisons = [dict(r) for r in con.execute("SELECT last_min,prev24_min,prev7d_min FROM item_summary WHERE house_id=? AND last_ts=?", (hid,house_latest(con,hid)))]
    k = dict(k)
    import statistics
    for label,col in (("24","prev24_min"),("7d","prev7d_min")):
        vals = [(r["last_min"]/r[col]-1)*100 for r in comparisons if r.get(col) and r.get("last_min")]
        k[f"median_chg{label}"] = statistics.median(vals) if vals else None
        k[f"comparable{label}"] = len(vals)
        k[f"up{label}"] = sum(v>0 for v in vals)
        k[f"down{label}"] = sum(v<0 for v in vals)
    quality = market_quality(con,hid)
    k["comparisons_reliable"] = bool(quality.get("complete")) and bool(quality.get("ts") and quality["ts"] >= time.time()-10800)
    return {
        "house": house,
        "kpis": k,
        "quality": quality,
        "last_ts": snaps[-1]["ts"] if snaps else None,
        "snapshot_count": con.execute("SELECT COUNT(*) FROM snapshots WHERE house_id=?",
                                      (hid,)).fetchone()[0],
        "market_series": snaps,
        "categories": classes(con, {"house": str(hid)})["classes"],
        "movers": movers(con, {**p, "window": "24h", "relevant": "1"}),
        "deals": deals(con, {**p, "threshold": "15", "relevant": "1", "liq_min": "30"}),
    }


def classes(con, params):
    import statistics
    hid = _resolve_house(con,params)
    out = {}
    reliable = market_quality(con,hid)
    reliable = bool(reliable.get("complete")) and bool(reliable.get("ts") and reliable["ts"]>=time.time()-10800)
    for raw in con.execute(f"SELECT {SUMMARY_COLS} {SUMMARY_FROM} WHERE s.house_id=?", (hid,)):
        row = _item_row(raw)
        cid = row["class_id"] if row["class_id"] is not None else -1
        c = out.setdefault(cid,{"class_id":cid,"class_name":row["class_name"] or CLASS_NAMES.get(cid,"Ohne Metadaten"),"items":0,"market_value":0,"quantity":0,"_subs":{},"_24":[],"_7":[]})
        sid = row["subclass_id"]
        sub = c["_subs"].setdefault(sid,{"subclass_id":sid,"subclass_name":row["subclass_name"] or ("Unbekannt" if sid is None else f"Unterklasse {sid}"),"items":0,"market_value":0,"quantity":0,"_24":[],"_7":[]})
        for target in (c,sub):
            target["items"] += 1
            target["market_value"] += row["market_value"] or 0
            target["quantity"] += row.get("current_quantity") or 0
            for suffix,key in (("24","chg24_min"),("7","chg7d_min")):
                if reliable and row["is_current"] and row.get(key) is not None:target["_"+suffix].append(row[key])
    def finish(target):
        for suffix,name in (("24","chg24"),("7","chg7d")):
            vals=target.pop("_"+suffix)
            target[name]=statistics.median(vals) if vals else None
            target[name+"_n"]=len(vals)
        return target
    result=[]
    for c in out.values():
        c["subclasses"]=[finish(sub) for _,sub in sorted(c.pop("_subs").items(),key=lambda kv:kv[0] if kv[0] is not None else -1)]
        result.append(finish(c))
    result.sort(key=lambda c:-c["market_value"])
    return {"house_id":hid,"classes":result}


SEARCH_SORTS = {
    "name": "i.name COLLATE NOCASE", "price": "s.last_min", "median": "s.last_median",
    "qty": "s.last_qty", "avg_qty": "s.avg7_qty", "chg24": "chg24_min", "chg7d": "chg7d_min",
    "vol": "s.cv7_min", "value": "market_value", "quality": "i.quality", "id": "s.item_id",
    "updated": "s.last_ts", "avg30": "s.avg30_min",
}


def search(con, params):
    """Globaler Itemindex, ausgewählte Märkte liefern nur Preisbeobachtungen."""
    where, args = _house_filter(params)
    base = SUMMARY_FROM + (" WHERE " + " AND ".join(where) if where else "")
    rows = [_item_row(r) for r in con.execute(f"SELECT {SUMMARY_COLS} {base}", args)]
    seen = {r["item_id"] for r in rows}
    global_ids = {r[0] for r in con.execute("SELECT DISTINCT item_id FROM item_summary")}
    for raw in con.execute("""SELECT i.id AS item_id, i.*, va.alias AS verified_alias FROM items i
                              LEFT JOIN verified_aliases va ON va.item_id = i.id AND va.status = 'ok'"""):
        if raw["item_id"] in seen:
            continue
        r = _item_row(raw)
        r.update({"house_id": None, "no_ah": True, "has_history": r["item_id"] in global_ids,
                  "is_current": False, "availability": "no_local_data", "market_value": 0})
        for key in ("last_min", "last_median", "last_qty", "avg7_qty", "avg30_min", "chg24_min", "chg7d_min", "cv7_min", "last_ts"):
            r[key] = None
        # Keep the selected scope on catalog rows without inventing market data.
        for key in ("region", "faction", "source"):
            r[key] = params.get(key)
        rows.append(r)
    q = db.fold((params.get("q") or "").strip()) or ""
    if q:
        def matches(r):
            text = db.fold(" ".join(str(r.get(k) or "") for k in ("name", "name_en")) + " " + " ".join(r.get("aliases", [])))
            return (q.isdigit() and r["item_id"] == int(q)) or all(w in text for w in q.split())
        rows = [r for r in rows if matches(r)]
    for key, col in (("class", "class_id"), ("subclass", "subclass_id")):
        if params.get(key) not in (None, ""):
            v = _int(params, key)
            rows = [r for r in rows if r.get(col) == (None if v == -1 else v)]
    quals = _ids(params, "quality")
    if quals:
        rows = [r for r in rows if r.get("quality") in quals]
    # --- Kategorie-Filter (Spiegel in static-api.js; beide Seiten müssen übereinstimmen) ---
    if params.get("listed") == "1":
        rows = [r for r in rows if r.get("is_current")]
    min_lvl, max_lvl = _int(params, "min_lvl", lo=1), _int(params, "max_lvl", lo=1)
    # Ohne bekannte Stufen im Datenbestand (älterer Export) wirkt der Filter nicht, statt alles auszublenden.
    if (min_lvl is not None or max_lvl is not None) and any(r.get("required_level") is not None for r in rows):
        rows = [r for r in rows if r.get("required_level") is not None
                and (min_lvl is None or r["required_level"] >= min_lvl)
                and (max_lvl is None or r["required_level"] <= max_lvl)]
    prof_ids = set(_ids(params, "prof"))
    if prof_ids or params.get("margin") == "1":
        from . import recipes as rc
        if prof_ids:
            pmap = rc.profession_items(con)
            rows = [r for r in rows if prof_ids & pmap.get(r["item_id"], set())]
        if params.get("margin") == "1":
            factions = {str(r.get("faction") or "").lower() for r in rows if r.get("faction")}
            if not factions:
                factions = {"allianz", "horde", "neutral"}
            keep = rc.profitable_recipe_items(rc.export_data(con, rc.group_houses(con)), factions)
            rows = [r for r in rows if r["item_id"] in keep]
    for key, col, lower in (("min_price", "last_min", True), ("max_price", "last_min", False),
                             ("min_qty", "last_qty", True), ("max_qty", "last_qty", False),
                             ("min_avg_qty", "avg7_qty", True), ("min_chg", "chg24_min", True), ("max_chg", "chg24_min", False)):
        v = _float(params, key)
        if v is not None:
            rows = [r for r in rows if r.get("is_current") and r.get(col) is not None and (r[col] >= v if lower else r[col] <= v)]
    if params.get("commodity") in ("0", "1"):
        rows = [r for r in rows if (r.get("is_commodity") or 0) == int(params["commodity"])]
    cols = {"name": "name", "price": "last_min", "median": "last_median", "qty": "last_qty", "avg_qty": "avg7_qty",
            "chg24": "chg24_min", "chg7d": "chg7d_min", "vol": "cv7_min", "value": "market_value", "quality": "quality",
            "id": "item_id", "updated": "last_ts", "avg30": "avg30_min"}
    col = cols.get(params.get("sort"), "market_value")
    rows.sort(key=lambda r: r["item_id"])
    if params.get("sort") == "relevance" and q:
        def rank(r):
            names = [db.fold(r.get("name") or ""), db.fold(r.get("name_en") or ""), *[db.fold(x) for x in r.get("aliases", [])]]
            return (0 if any(n.startswith(q) for n in names) else 1 if any(" " + q in n for n in names) else 2, -(r.get("market_value") or 0))
        rows.sort(key=rank)
    else:
        values = [r for r in rows if r.get(col) is not None]
        values.sort(key=lambda r: r[col].casefold() if isinstance(r[col], str) else r[col], reverse=params.get("dir") != "asc")
        rows = values + [r for r in rows if r.get(col) is None]
    total = len(rows)
    page = _int(params, "page", 1, lo=1)
    size = _int(params, "page_size", 50, lo=1, hi=500)
    rows = rows[(page - 1) * size:page * size]
    if params.get("spark") == "1":
        _attach_sparklines(con, [r for r in rows if r.get("house_id")])
    return {"total": total, "page": page, "page_size": size, "items": rows}


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
    meta = con.execute("""SELECT i.*, va.alias AS verified_alias FROM items i
                          LEFT JOIN verified_aliases va ON va.item_id = i.id AND va.status = 'ok'
                          WHERE i.id=?""", (item_id,)).fetchone()
    if not meta:
        raise NotFound(f"Item {item_id} unbekannt")
    rows = con.execute(f"SELECT {SUMMARY_COLS} {SUMMARY_FROM} WHERE s.item_id=? "
                       f"ORDER BY h.region, h.faction, h.name", (item_id,)).fetchall()
    meta = itemnames.enrich(meta)
    meta["quality_name"] = QUALITY_NAMES.get(meta["quality"])
    if meta.get("details"):
        try:
            meta["details"] = json.loads(meta["details"])
        except ValueError:
            meta["details"] = None
    meta.pop("details_v", None)
    hs = [_item_row(r) for r in rows]
    info = _house_info(con)
    for h in hs:
        d = info.get(h["house_id"], {})
        for k in ("kind", "label", "short", "verbund", "group"):
            h[k] = d.get(k)
    liq: dict = {}
    for r in con.execute(
            """SELECT d.house_id, d.day, d.expected, d.present, d.events, d.vanish, d.units
               FROM liquidity_daily d JOIN houses h ON h.id = d.house_id
               WHERE d.item_id=? ORDER BY d.house_id, d.day""", (item_id,)):
        liq.setdefault(str(r[0]), []).append(
            {"day": r[1], "expected": r[2], "present": r[3],
             "events": r[4], "vanish": r[5], "units": r[6]})
    return {"item": meta, "houses": hs, "liquidity": liq}


def _range_bounds(con, hid, rng):
    if rng not in RANGES:
        raise BadRequest(f"Zeitraum {rng!r} ungültig (24h, 7d, 30d, 90d, all)")
    latest = house_latest(con, hid)
    span = RANGES[rng]
    lo = (latest - span) if (latest is not None and span) else 0
    return lo, latest


def _history_rows(con, hid, item_id, lo, hi=None):
    q = """SELECT ts, min, p10, median, mean, max, quantity, auctions, lowest, sample_n, reference_low, reference_high FROM prices
           WHERE house_id=? AND item_id=? AND ts >= ?"""
    args = [hid, item_id, lo]
    if hi is not None:
        q += " AND ts <= ?"
        args.append(hi)
    rows = [dict(r) for r in con.execute(q + " ORDER BY ts", args)]
    for row in rows:
        row["n"] = row["sample_n"] if db.is_daily_ts(row["ts"]) else row["sample_n"] or 1
        row["aggregated"] = db.is_daily_ts(row["ts"])
    return rows


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
        where.append("(fold(i.name) LIKE ? OR fold(i.name_en) LIKE ? OR CAST(p.item_id AS TEXT) = ?)")
        args += [f"%{db.fold(q)}%", f"%{db.fold(q)}%", q]
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
        f"""SELECT p.*, i.name, i.name_en, i.quality, i.icon, i.class_name, i.subclass_name,
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
    where = ["s.house_id = ?", f"{col} IS NOT NULL", "s.last_ts=(SELECT MAX(ts) FROM snapshots WHERE house_id=s.house_id)"]
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
    if params.get("relevant") == "1":
        where.append(RELEVANT_WHERE)
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
             f"s.last_min <= {ref} * (1 - ?)", "s.points >= 3", "s.last_ts=(SELECT MAX(ts) FROM snapshots WHERE house_id=s.house_id)", "s.last_ts >= ?"]
    args: list = [hid, threshold, int(time.time()) - 3 * 3600]
    min_qty = _float(params, "min_qty", 0)
    if min_qty:
        where.append("COALESCE(s.avg7_qty, 0) >= ?")
        args.append(min_qty)
    if params.get("class") not in (None, ""):
        where.append("i.class_id = ?")
        args.append(_int(params, "class"))
    if params.get("relevant") == "1":
        where.append(RELEVANT_WHERE)
    liq_min = _float(params, "liq_min", 0)
    if liq_min:
        where.append("COALESCE(lq.score, 0) >= ?")
        args.append(liq_min)
    limit = _int(params, "limit", 50, lo=1, hi=500)
    rows = con.execute(
        f"""SELECT {SUMMARY_COLS}, {ref} AS ref_price,
                   (1 - 1.0*s.last_min / {ref}) * 100 AS discount,
                   ({ref} - s.last_min) AS saving
            {SUMMARY_FROM} WHERE {' AND '.join(where)}
            ORDER BY discount DESC LIMIT ?""", args + [limit]).fetchall()
    return {"house_id": hid, "basis": basis, "threshold": threshold * 100,
            "deals": [_item_row(r) for r in rows]}


def liquidity(con, params):
    """Liquiditäts-Ranking: meiste Umsatzsignale bzw. aktuelle Angebote ohne Umsatz."""
    hid = _resolve_house(con, params)
    limit = _int(params, "limit", 30, lo=1, hi=200)
    where = ["s.house_id = ?", "lq.score IS NOT NULL",
             "s.last_ts=(SELECT MAX(ts) FROM snapshots WHERE house_id=s.house_id)"]
    args: list = [hid]
    if params.get("class") not in (None, ""):
        where.append("i.class_id = ?")
        args.append(_int(params, "class"))
    base = f"SELECT {SUMMARY_COLS} {SUMMARY_FROM} WHERE {' AND '.join(where)}"
    hot = con.execute(base + " ORDER BY lq.score DESC, lq.events DESC, s.item_id LIMIT ?",
                      args + [limit]).fetchall()
    cold = con.execute(base + " AND lq.events <= 1"
                              " ORDER BY market_value DESC, s.item_id LIMIT ?",
                       args + [limit]).fetchall()
    ts = con.execute("SELECT MAX(computed_at) FROM item_liquidity WHERE house_id=?",
                     (hid,)).fetchone()[0]
    return {"house_id": hid, "window_days": liquidity_.WINDOW // 86400, "computed_at": ts,
            "hot": [_item_row(r) for r in hot], "cold": [_item_row(r) for r in cold]}


def recipes(con, params):
    from . import recipes as rc
    return rc.export_data(con, rc.group_houses(con))


def quests(con, params):
    from . import gamedata
    from . import recipes as rc
    return gamedata.export_data(con, rc.group_houses(con))


def sources(con, item_id):
    from . import origins
    return origins.item_sources(con, int(item_id))


def import_log(con, params):
    rows = con.execute("SELECT * FROM import_log ORDER BY id DESC LIMIT 50").fetchall()
    return {"log": [dict(r) for r in rows]}
