"""Herkunft von Items aus der QuestieDB: Beute von Gegnern, Fundorte (Erzadern, Kräuter, Truhen),
Händler, Behälter (Item in Item) und Quests, die ein Item verlangen.

Quelle: GitHub-Release von https://github.com/Questie/QuestieDB (Datei ``QuestieDB-<v>-Forever.zip``).
Geprüft wird höchstens wöchentlich (eine Anfrage an die GitHub-API); neu geladen nur bei einem
neuen Release. Die Inhalte gelten laut LICENSE.md der QuestieDB „im Zweifel als all rights reserved“
(Code: GPLv3) – eingebaut auf ausdrücklichen Wunsch, mit Quellenangabe auf der Website.

NPC- und Objektnamen sind englisch (die QuestieDB-Datei enthält keine Übersetzungen); Gebiete und
Quest-Titel werden – wo bekannt – aus den deutschen Battle.net-Daten genommen.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.request

from . import db
from .recipes import MIN_KEEP_RATIO, item_table
from .sources import questie as qreader

RELEASE_API = "https://api.github.com/repos/Questie/QuestieDB/releases/latest"
ASSET = re.compile(r"^QuestieDB-.+-Forever\.zip$")
USER_AGENT = "wow-ah-analytics (Daten-Import)"
CHECK_DAYS = 7
SHARDS = 64            # data/sources/<id % 64>.json
MAX_NPCS = 40          # je Item exportierte Gegner (Rest nur gezählt)
MAX_OBJECTS = 30
MAX_VENDORS = 40
MIN_NPCS = 1000        # Plausibilität eines Imports
MIN_SOURCES = 10000

# Art der Herkunft
NPC_DROP, OBJECT, CONTAINER, VENDOR = 1, 2, 3, 4
# Art des Quest-Bezugs
Q_OBJECTIVE, Q_SOURCE, Q_STARTS = 1, 2, 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS qdb_npcs (
    id INTEGER PRIMARY KEY, name TEXT, minl INTEGER, maxl INTEGER,
    rank INTEGER,                         -- 0 normal | 1 Elite | 2 seltene Elite | 3 Boss | 4 selten
    zone INTEGER, fac TEXT,               -- A | H | AH (freundlich zu)
    sub TEXT                              -- Untertitel, z. B. „Waffenhändler“
);
CREATE TABLE IF NOT EXISTS qdb_objects (id INTEGER PRIMARY KEY, name TEXT, zone INTEGER);
CREATE TABLE IF NOT EXISTS qdb_sources (
    item_id INTEGER NOT NULL, kind INTEGER NOT NULL, src_id INTEGER NOT NULL,
    chance  REAL,                         -- Dropchance in % (nur wo bekannt)
    PRIMARY KEY (item_id, kind, src_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS qdb_sources_src ON qdb_sources(kind, src_id);
CREATE TABLE IF NOT EXISTS qdb_quests (
    id INTEGER PRIMARY KEY, name TEXT, level INTEGER, minlevel INTEGER, zone INTEGER, faction TEXT
);
CREATE TABLE IF NOT EXISTS qdb_quest_items (
    quest_id INTEGER NOT NULL, item_id INTEGER NOT NULL, kind INTEGER NOT NULL,
    PRIMARY KEY (item_id, quest_id, kind)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS qdb_item_names (id INTEGER PRIMARY KEY, name TEXT);  -- englisch, nur Behälter/Inhalt
CREATE TABLE IF NOT EXISTS zones (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
"""

ALLIANCE_RACES, HORDE_RACES = 1 | 4 | 8 | 64, 2 | 16 | 32 | 128


def ensure_schema(con):
    con.executescript(SCHEMA)


def _s(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v if isinstance(v, str) else None


def _i(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _ids(v):
    return [int(x) for x in qreader.lua_list(v) if isinstance(x, (int, float)) and x > 0]


def _faction(races):
    r = (_i(races) or 0) & 255
    if r and r & ALLIANCE_RACES == r:
        return "Allianz"
    if r and r & HORDE_RACES == r:
        return "Horde"
    return None


# ------------------------------------------------------------------ Einlesen

def parse(qdb: qreader.QuestieDB) -> dict:
    """Alles, was die Website braucht, aus einer QuestieDB lesen."""
    chances = qdb.drop_chances()
    sources, quest_items, names, boxed = [], set(), {}, set()
    npc_ids, obj_ids = set(), set()
    for iid in qdb.ids("Item"):
        r = qdb.row("Item", iid, qreader.ITEM) or {}
        names[iid] = _s(r.get("name"))
        for kind, key, ids in ((NPC_DROP, "npcDrops", npc_ids), (OBJECT, "objectDrops", obj_ids),
                               (CONTAINER, "itemDrops", None), (VENDOR, "vendors", npc_ids)):
            for sid in _ids(r.get(key)):
                ch = chances.get(iid, {}).get(sid) if kind == NPC_DROP else None
                sources.append((iid, kind, sid, ch))
                if ids is not None:
                    ids.add(sid)
                elif kind == CONTAINER:
                    boxed.update((iid, sid))
        sq = _i(r.get("startQuest"))
        if sq:
            quest_items.add((sq, iid, Q_STARTS))
    quests = []
    for qid in qdb.ids("Quest"):
        r = qdb.row("Quest", qid, qreader.QUEST) or {}
        obj = r.get("objectives") or {}
        if isinstance(obj, list):
            obj = {i + 1: v for i, v in enumerate(obj)}
        for x in qreader.lua_list(obj.get(3)):           # Gruppe 3 = Items
            x = qreader.lua_list(x)
            if x and _i(x[0]):
                quest_items.add((qid, int(x[0]), Q_OBJECTIVE))
        for iid in _ids(r.get("requiredSourceItems")):
            if (qid, iid, Q_OBJECTIVE) not in quest_items:
                quest_items.add((qid, iid, Q_SOURCE))
        zone = _i(r.get("zoneOrSort"))
        quests.append((qid, _s(r.get("name")), _i(r.get("questLevel")), _i(r.get("requiredLevel")),
                       zone if zone and zone > 0 else None, _faction(r.get("requiredRaces"))))
    npcs = []
    for nid in sorted(npc_ids):
        r = qdb.row("Npc", nid, qreader.NPC)
        if r is None:
            continue
        npcs.append((nid, _s(r.get("name")), _i(r.get("minLevel")), _i(r.get("maxLevel")), _i(r.get("rank")) or 0,
                     _i(r.get("zoneID")), _s(r.get("friendlyToFaction")), _s(r.get("subName"))))
    objects = []
    for oid in sorted(obj_ids):
        r = qdb.row("Object", oid, qreader.OBJECT)
        if r is not None:
            objects.append((oid, _s(r.get("name")), _i(r.get("zoneID"))))
    return {"npcs": npcs, "objects": objects, "sources": sources, "quests": quests,
            "quest_items": sorted(quest_items), "version": qdb.version,
            "item_names": sorted((i, names[i]) for i in boxed if names.get(i))}


def store(con, data: dict, tag: str, log=print) -> bool:
    ensure_schema(con)
    old = con.execute("SELECT COUNT(*) FROM qdb_sources").fetchone()[0]
    n = len(data["sources"])
    if len(data["npcs"]) < MIN_NPCS or n < MIN_SOURCES or n < old * MIN_KEEP_RATIO:
        log(f"QuestieDB: unplausibel ({len(data['npcs'])} NPCs, {n} Herkünfte, bisher {old}) – Bestand bleibt.")
        return False
    with db.transaction(con):
        for t in ("qdb_npcs", "qdb_objects", "qdb_sources", "qdb_quests", "qdb_quest_items", "qdb_item_names"):
            con.execute(f"DELETE FROM {t}")
        con.executemany("INSERT INTO qdb_npcs VALUES (?,?,?,?,?,?,?,?)", data["npcs"])
        con.executemany("INSERT INTO qdb_objects VALUES (?,?,?)", data["objects"])
        con.executemany("INSERT OR IGNORE INTO qdb_sources VALUES (?,?,?,?)", data["sources"])
        con.executemany("INSERT INTO qdb_quests VALUES (?,?,?,?,?,?)", data["quests"])
        con.executemany("INSERT INTO qdb_item_names VALUES (?,?)", data.get("item_names", []))
        con.executemany("INSERT OR IGNORE INTO qdb_quest_items(quest_id, item_id, kind) VALUES (?,?,?)", data["quest_items"])
        db.set_meta(con, "questie_tag", tag)
        db.set_meta(con, "questie_version", data.get("version", ""))
        db.set_meta(con, "questie_fetched", int(time.time()))
    log(f"QuestieDB {tag} gespeichert: {len(data['npcs'])} NPCs, {len(data['objects'])} Objekte, "
        f"{n} Herkünfte, {len(data['quest_items'])} Quest-Bezüge")
    return True


def store_zones(con, client, log=print):
    """Deutsche Gebietsnamen (Battle.net Quest-Gebiete = AreaTable-IDs, auch Dungeons)."""
    ensure_schema(con)
    try:
        d, _ = client.get("/data/wow/quest/area/index", "static")
    except Exception as exc:  # noqa: BLE001
        log(f"Gebietsnamen nicht geladen: {exc}")
        return
    rows = [(int(a["id"]), a["name"]) for a in d.get("areas") or [] if a.get("id") and a.get("name")]
    if rows:
        with db.transaction(con):
            con.executemany("INSERT OR REPLACE INTO zones(id, name) VALUES (?,?)", rows)


# ------------------------------------------------------------------ Aktualisieren

def _http_json(url, opener):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with opener(req, timeout=30) as resp:
        return json.loads(resp.read())


def _download(url, dest, opener):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener(req, timeout=120) as resp, open(dest, "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)


def update(con, client=None, force=False, path=None, log=print, opener=None) -> bool:
    """QuestieDB bei Bedarf neu einlesen. ``path``: lokale Zip-Datei/Addon-Ordner statt Download."""
    ensure_schema(con)
    opener = opener or urllib.request.urlopen
    if client is not None and (force or not con.execute("SELECT 1 FROM zones LIMIT 1").fetchone()):
        store_zones(con, client, log=log)
    have = con.execute("SELECT 1 FROM qdb_sources LIMIT 1").fetchone()
    if path:
        tag = "lokal:" + os.path.basename(os.path.normpath(path))
        data = parse(qreader.QuestieDB.from_file(path))
        return store(con, data, tag, log=log)
    checked = int(db.get_meta(con, "questie_checked", "0") or 0)
    if have and not force and time.time() - checked < CHECK_DAYS * 86400:
        log("QuestieDB: zuletzt vor weniger als 7 Tagen geprüft – kein Abruf nötig.")
        return False
    rel = _http_json(RELEASE_API, opener)
    tag = rel.get("tag_name") or ""
    asset = next((a for a in rel.get("assets") or [] if ASSET.match(a.get("name", ""))), None)
    if not asset:
        raise RuntimeError(f"QuestieDB-Release {tag} ohne Forever-Datei")
    if have and not force and tag == db.get_meta(con, "questie_tag", ""):
        db.set_meta(con, "questie_checked", int(time.time()))
        log(f"QuestieDB: {tag} ist aktuell.")
        return False
    log(f"QuestieDB: lade {asset['name']} ({asset.get('size', 0) / 1e6:.1f} MB) …")
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        _download(asset["browser_download_url"], tmp, opener)
        data = parse(qreader.QuestieDB.from_file(tmp))
    finally:
        os.unlink(tmp)
    ok = store(con, data, tag, log=log)
    if ok:
        db.set_meta(con, "questie_checked", int(time.time()))
    return ok


# ------------------------------------------------------------------ Export

def _faction_label(fac):
    return {"A": "Allianz", "H": "Horde"}.get(fac or "")


def export_items(con, item_ids) -> dict:
    """Herkunft für die angegebenen Items: {items: {id: {...}}, npcs, objects, quests, names}.

    Je Item: d = [[npc, Chance]] (Gegner, max. MAX_NPCS, sortiert nach Chance, Rang, Stufe), dn = Anzahl,
    o = [[objekt, Chance]], on, c = Behälter, in denen das Item steckt, k = Inhalt (falls selbst Behälter),
    v = Händler, q = [[quest, art]] (1 Questziel, 2 benötigt, 3 startet Quest).
    """
    ensure_schema(con)
    ids = sorted({int(i) for i in item_ids})
    out, npcs, objs, quests, refs = {}, set(), set(), set(), set()
    if not ids or not con.execute("SELECT 1 FROM qdb_sources LIMIT 1").fetchone():
        return {"items": {}, "npcs": {}, "objects": {}, "quests": {}, "names": {}}
    con.execute("CREATE TEMP TABLE IF NOT EXISTS _want(id INTEGER PRIMARY KEY)")
    con.execute("DELETE FROM _want")
    con.executemany("INSERT INTO _want VALUES (?)", [(i,) for i in ids])
    rank = {r[0]: (r[1] or 0, r[2] or 0) for r in con.execute(
        "SELECT n.id, n.rank, n.minl FROM qdb_npcs n WHERE n.id IN (SELECT src_id FROM qdb_sources s JOIN _want w ON w.id=s.item_id WHERE s.kind=1)")}
    grouped = {}
    for iid, kind, sid, ch in con.execute(
            "SELECT s.item_id, s.kind, s.src_id, s.chance FROM qdb_sources s JOIN _want w ON w.id=s.item_id"):
        grouped.setdefault(iid, {}).setdefault(kind, []).append((sid, ch))
    contains = {}
    for cont, iid in con.execute(
            "SELECT s.src_id, s.item_id FROM qdb_sources s JOIN _want w ON w.id=s.src_id WHERE s.kind=3"):
        contains.setdefault(cont, []).append(iid)
    qrefs = {}
    for iid, qid, kind in con.execute(
            "SELECT q.item_id, q.quest_id, q.kind FROM qdb_quest_items q JOIN _want w ON w.id=q.item_id ORDER BY q.quest_id"):
        qrefs.setdefault(iid, []).append([qid, kind])
    for iid in ids:
        g, e = grouped.get(iid, {}), {}
        drops = g.get(NPC_DROP, [])
        if drops:
            drops.sort(key=lambda x: (-(x[1] or -1), -(rank.get(x[0], (0, 0))[0] in (1, 2, 3)), rank.get(x[0], (0, 0))[1], x[0]))
            e["d"] = [[s, round(c, 2) if c else None] for s, c in drops[:MAX_NPCS]]
            e["dn"] = len(drops)
            npcs.update(s for s, _ in drops[:MAX_NPCS])
        ob = g.get(OBJECT, [])
        if ob:
            e["o"] = [[s, None] for s, _ in sorted(ob)[:MAX_OBJECTS]]
            e["on"] = len(ob)
            objs.update(s for s, _ in e["o"])
        if g.get(CONTAINER):
            e["c"] = sorted(s for s, _ in g[CONTAINER])
            refs.update(e["c"])
        if contains.get(iid):
            e["k"] = sorted(contains[iid])
            refs.update(e["k"])
        if g.get(VENDOR):
            e["v"] = sorted(s for s, _ in g[VENDOR])[:MAX_VENDORS]
            e["vn"] = len(g[VENDOR])
            npcs.update(e["v"])
        if qrefs.get(iid):
            e["q"] = qrefs[iid]
            quests.update(q for q, _ in qrefs[iid])
        if e:
            out[str(iid)] = e
    zones = dict(con.execute("SELECT id, name FROM zones"))
    bn_quests = {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT id, title, zone, faction FROM quests")} \
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='quests'").fetchone() else {}

    def rows(sql, keys):
        res = []
        keys = sorted(keys)
        for k in range(0, len(keys), 900):
            part = keys[k:k + 900]
            res += con.execute(sql.format(",".join("?" * len(part))), part).fetchall()
        return res

    npc_out = {str(r[0]): [r[1], r[2], r[3], r[4], zones.get(r[5]), _faction_label(r[6]), r[7]]
               for r in rows("SELECT id, name, minl, maxl, rank, zone, fac, sub FROM qdb_npcs WHERE id IN ({})", npcs)}
    obj_out = {str(r[0]): [r[1], zones.get(r[2])]
               for r in rows("SELECT id, name, zone FROM qdb_objects WHERE id IN ({})", objs)}
    q_out = {}
    for qid, name, level, minl, zone, fac in rows("SELECT id, name, level, minlevel, zone, faction FROM qdb_quests WHERE id IN ({})", quests):
        bn = bn_quests.get(qid)
        q_out[str(qid)] = [(bn and bn[0]) or name, level, minl, (bn and bn[1]) or zones.get(zone), (bn and bn[2]) or fac]
    names = item_table(con, refs, dict(rows("SELECT id, name FROM qdb_item_names WHERE id IN ({})", refs))) if refs else {}
    known = {r[0] for r in rows("SELECT id FROM items WHERE id IN ({})", refs)} if refs else set()
    for k, v in names.items():
        v.append(1 if int(k) in known else 0)        # 5. Feld: hat eine Item-Seite
    return {"items": out, "npcs": npc_out, "objects": obj_out, "quests": q_out, "names": names}


def export_shards(con, data_dir, log=print) -> int:
    """data/sources/<id % SHARDS>.json für alle Items der Datenbank. Gibt die Zahl der Items mit Herkunft zurück."""
    ensure_schema(con)
    if not con.execute("SELECT 1 FROM qdb_sources LIMIT 1").fetchone():
        return 0
    ids = [r[0] for r in con.execute("SELECT id FROM items")]
    out_dir = os.path.join(data_dir, "sources")
    os.makedirs(out_dir, exist_ok=True)
    total = 0
    for s in range(SHARDS):
        d = export_items(con, [i for i in ids if i % SHARDS == s])
        d["shards"] = SHARDS
        d["source"] = {"name": "QuestieDB", "tag": db.get_meta(con, "questie_tag", ""),
                       "url": "https://github.com/Questie/QuestieDB"}
        total += len(d["items"])
        with open(os.path.join(out_dir, f"{s}.json"), "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, separators=(",", ":"))
    return total


def item_sources(con, item_id: int) -> dict:
    """Wie ein Shard, aber nur für ein Item (lokaler Server)."""
    d = export_items(con, [item_id])
    d["shards"] = SHARDS
    d["source"] = {"name": "QuestieDB", "tag": db.get_meta(con, "questie_tag", ""), "url": "https://github.com/Questie/QuestieDB"}
    return d
