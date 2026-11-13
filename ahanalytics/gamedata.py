"""Quests (Belohnungen) und Item-Sets aus der Battle.net Static API für WoW: Forever.

Quelle: Namespace ``static-forever-{region}``
  /data/wow/quest/area/index, /data/wow/quest/category/index → Quest-IDs je Zone/Kategorie
  /data/wow/quest/{id}            → Titel, Zone, Typ, Fraktion, Klassen, Belohnungen
  /data/wow/item-set/index        → Sets
  /data/wow/item-set/{id}         → Teile und Set-Boni

Wie bei den Rezepten: lokal in der Datenbank, höchstens wöchentlich bzw. bei neuem
Namespace-Build neu geladen; ein unvollständiger Abruf ersetzt den Bestand nicht.

Der Quest-Abruf (~7000 Anfragen) läuft in Etappen: bereits geladene Quests landen in
``gamedata_cache`` (je Build), ein Lauf hört nach ``TIME_BUDGET`` Sekunden auf und der
nächste macht dort weiter. So kann ein langsamer oder hängender Abruf den stündlichen
Lauf nicht blockieren, und kein Fortschritt geht verloren.

Nicht in der API: welche Items eine Quest *verlangt* (Questziele), Beute von Gegnern,
Händlerangebote.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import db
from .recipes import MAX_ERROR_RATIO, MAX_ERRORS, MAX_AGE_DAYS, MIN_KEEP_RATIO, _ns_build, item_table, price_table

SCHEMA = """
CREATE TABLE IF NOT EXISTS quests (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    zone       TEXT,                     -- Gebiet oder Kategorie (z. B. „Kräuterkunde“, „Saisonbedingt“)
    type       TEXT,                     -- Dungeon | Schlachtzug | Elite | PvP | …
    faction    TEXT,                     -- Allianz | Horde | NULL (beide)
    classes    TEXT,                     -- „Krieger, Paladin“ oder NULL
    repeatable INTEGER,                  -- 0 | 1 wiederholbar | 2 täglich | 3 wöchentlich
    xp         INTEGER,
    money      INTEGER
);
CREATE TABLE IF NOT EXISTS quest_rewards (
    quest_id  INTEGER NOT NULL,
    item_id   INTEGER NOT NULL,
    item_name TEXT,
    choice    INTEGER NOT NULL DEFAULT 0,  -- 1 = eine von mehreren zur Wahl
    PRIMARY KEY (quest_id, item_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS quest_rewards_item ON quest_rewards(item_id);
CREATE TABLE IF NOT EXISTS item_sets (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    effects TEXT                         -- JSON [[benötigte Teile, Text], …]
);
CREATE TABLE IF NOT EXISTS item_set_items (
    set_id    INTEGER NOT NULL,
    item_id   INTEGER NOT NULL,
    item_name TEXT,
    PRIMARY KEY (set_id, item_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS item_set_items_item ON item_set_items(item_id);
CREATE TABLE IF NOT EXISTS gamedata_cache (  -- Zwischenstand eines Abrufs über mehrere Läufe
    kind  TEXT NOT NULL,
    id    INTEGER NOT NULL,
    build TEXT NOT NULL,
    data  TEXT,                          -- JSON des geparsten Eintrags, NULL = ohne Titel/verworfen
    PRIMARY KEY (kind, id)
) WITHOUT ROWID;
"""

TIME_BUDGET = 360      # Sekunden je Lauf für Quest-Details, danach Fortsetzung im nächsten Lauf
CHUNK = 250            # Quests je Block (nach jedem Block wird der Zwischenstand gespeichert)
WORKERS = 16
MAX_RPS = 80           # Battle.net erlaubt 100 Anfragen/s

FACTIONS = {"ALLIANCE": "Allianz", "HORDE": "Horde"}


def ensure_schema(con):
    con.executescript(SCHEMA)


class _RateLimit:
    """Höchstens ``rps`` Anfragen pro Sekunde über alle Threads."""

    def __init__(self, rps):
        self.gap, self.next, self.lock = 1.0 / rps, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        if t > now:
            time.sleep(t - now)


_LIMIT = _RateLimit(MAX_RPS)


def _get(client, path):
    _LIMIT.wait()
    try:
        payload, _ = client.get(path, "static")
        return payload, None
    except Exception as exc:  # noqa: BLE001 – einzelne Fehler zählen, nicht abbrechen
        return None, f"{path}: {exc}"


def _pmap(client, paths, workers=WORKERS):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda p: _get(client, p), paths))


# ------------------------------------------------------------------ Quests

def parse_quest(payload: dict, zone_hint: str | None = None) -> dict | None:
    if not payload or not payload.get("id") or not payload.get("title"):
        return None
    req = payload.get("requirements") or {}
    rew = payload.get("rewards") or {}
    fac = (req.get("faction") or {}).get("type")
    classes = [c.get("name") for c in req.get("classes") or [] if c.get("name")]
    rep = 3 if payload.get("is_weekly") else 2 if payload.get("is_daily") else 1 if payload.get("is_repeatable") else 0
    rewards, seen = [], set()
    items = rew.get("items") or {}
    for key, choice in (("items", 0), ("choice_of", 1)):
        for x in items.get(key) or []:
            it = x.get("item") or {}
            if it.get("id") and int(it["id"]) not in seen:
                seen.add(int(it["id"]))
                rewards.append({"item_id": int(it["id"]), "item_name": it.get("name"), "choice": choice})
    zone = (payload.get("area") or {}).get("name") or (payload.get("category") or {}).get("name") or zone_hint
    return {"id": int(payload["id"]), "title": payload["title"], "zone": zone,
            "type": (payload.get("type") or {}).get("name"), "faction": FACTIONS.get(fac),
            "classes": ", ".join(classes) or None, "repeatable": rep,
            "xp": rew.get("experience"), "money": (rew.get("money") or {}).get("value") if isinstance(rew.get("money"), dict) else rew.get("money"),
            "rewards": rewards}


def fetch_quests(client, log=print, con=None, deadline=None):
    """Alle Quests laden. Mit ``con`` wird der Fortschritt zwischengespeichert; ist ``deadline``
    erreicht, bevor alles da ist, kommt ``None`` statt der Liste zurück (Fortsetzung beim nächsten Mal)."""
    idx = []
    build = ""
    for kind, path, key in (("area", "/data/wow/quest/area/index", "areas"),
                            ("category", "/data/wow/quest/category/index", "categories")):
        d, err = _get(client, path)
        if err:
            raise RuntimeError(err)
        build = build or _ns_build(d)
        idx += [(kind, a["id"], a.get("name")) for a in d.get(key) or []]
    lists = _pmap(client, [f"/data/wow/quest/{k}/{i}" for k, i, _ in idx])
    hint, errors = {}, [e for _, e in lists if e]
    for (kind, _, name), (d, _) in zip(idx, lists):
        for q in (d or {}).get("quests") or []:
            hint.setdefault(int(q["id"]), name)
    ids = sorted(hint)
    done = {}
    if con is not None:
        ensure_schema(con)
        con.execute("DELETE FROM gamedata_cache WHERE kind='quest' AND build<>?", (build,))
        done = {i: (json.loads(d) if d else None)
                for i, d in con.execute("SELECT id, data FROM gamedata_cache WHERE kind='quest'")}
    todo = [q for q in ids if q not in done]
    log(f"Quests: {len(idx)} Gebiete/Kategorien, {len(ids)} Quests – {len(done)} schon geladen, "
        f"lade {len(todo)} …")
    t0 = time.time()
    for k in range(0, len(todo), CHUNK):
        if deadline and time.time() > deadline:
            log(f"Quests: Zeitbudget erreicht – {len(done)} von {len(ids)} geladen, "
                f"Fortsetzung im nächsten Lauf.")
            return None, build, len(ids) + len(idx), len(errors)
        part = todo[k:k + CHUNK]
        rows = []
        for i, (d, e) in zip(part, _pmap(client, [f"/data/wow/quest/{q}" for q in part])):
            if e:
                errors.append(e)
                continue
            done[i] = parse_quest(d, hint[i])
            rows.append(("quest", i, build, json.dumps(done[i], ensure_ascii=False) if done[i] else None))
        if con is not None and rows:
            with db.transaction(con):
                con.executemany("INSERT OR REPLACE INTO gamedata_cache(kind, id, build, data) VALUES (?,?,?,?)", rows)
        n = k + len(part)
        log(f"Quests: {n}/{len(todo)} ({n / max(time.time() - t0, 0.001):.0f}/s, {len(errors)} Fehler)")
    quests = [done[i] for i in ids if done.get(i)]
    if errors:
        log(f"Quests: {len(errors)} Abrufe fehlgeschlagen (z. B. {errors[0]})")
    return quests, build, len(ids) + len(idx), len(errors)


def store_quests(con, quests, build="", log=print) -> bool:
    ensure_schema(con)
    old = con.execute("SELECT COUNT(*) FROM quests").fetchone()[0]
    if not quests or len(quests) < old * MIN_KEEP_RATIO:
        log(f"Quests: Abruf lieferte nur {len(quests)} statt bisher {old} – Bestand bleibt unverändert.")
        return False
    with db.transaction(con):
        con.execute("DELETE FROM quest_rewards")
        con.execute("DELETE FROM quests")
        con.executemany("""INSERT INTO quests(id, title, zone, type, faction, classes, repeatable, xp, money)
                           VALUES (:id, :title, :zone, :type, :faction, :classes, :repeatable, :xp, :money)""", quests)
        con.executemany("INSERT INTO quest_rewards(quest_id, item_id, item_name, choice) VALUES (?,?,?,?)",
                        [(q["id"], r["item_id"], r["item_name"], r["choice"]) for q in quests for r in q["rewards"]])
        _ensure_items(con, {r["item_id"]: r["item_name"] for q in quests for r in q["rewards"]})
        db.set_meta(con, "quests_fetched", int(time.time()))
        db.set_meta(con, "quests_build", build)
        con.execute("DELETE FROM gamedata_cache WHERE kind='quest'")
    n = sum(1 for q in quests if q["rewards"])
    log(f"Quests gespeichert: {len(quests)} Quests, davon {n} mit Item-Belohnung")
    return True


# -------------------------------------------------------------------- Sets

def parse_set(payload: dict) -> dict | None:
    if not payload or not payload.get("id") or not payload.get("name"):
        return None
    items = [{"item_id": int(x["id"]), "item_name": x.get("name")} for x in payload.get("items") or [] if x.get("id")]
    if not items:
        return None
    effects = [[e.get("required_count"), e.get("display_string")] for e in payload.get("effects") or [] if e.get("display_string")]
    return {"id": int(payload["id"]), "name": payload["name"], "effects": json.dumps(effects, ensure_ascii=False), "items": items}


def fetch_sets(client, log=print, con=None, deadline=None):  # noqa: ARG001 – klein, ohne Etappen
    d, err = _get(client, "/data/wow/item-set/index")
    if err:
        raise RuntimeError(err)
    ids = sorted({int(s["id"]) for s in d.get("item_sets") or []})
    res = _pmap(client, [f"/data/wow/item-set/{i}" for i in ids])
    errors = [e for _, e in res if e]
    sets = [s for s in (parse_set(p) for p, _ in res) if s]
    log(f"Sets: {len(ids)} Sets geladen" + (f", {len(errors)} Fehler" if errors else ""))
    return sets, _ns_build(d), len(ids), len(errors)


def store_sets(con, sets, build="", log=print) -> bool:
    ensure_schema(con)
    old = con.execute("SELECT COUNT(*) FROM item_sets").fetchone()[0]
    if not sets or len(sets) < old * MIN_KEEP_RATIO:
        log(f"Sets: Abruf lieferte nur {len(sets)} statt bisher {old} – Bestand bleibt unverändert.")
        return False
    with db.transaction(con):
        con.execute("DELETE FROM item_set_items")
        con.execute("DELETE FROM item_sets")
        con.executemany("INSERT INTO item_sets(id, name, effects) VALUES (:id, :name, :effects)", sets)
        con.executemany("INSERT OR IGNORE INTO item_set_items(set_id, item_id, item_name) VALUES (?,?,?)",
                        [(s["id"], x["item_id"], x["item_name"]) for s in sets for x in s["items"]])
        _ensure_items(con, {x["item_id"]: x["item_name"] for s in sets for x in s["items"]})
        db.set_meta(con, "sets_fetched", int(time.time()))
        db.set_meta(con, "sets_build", build)
    log(f"Sets gespeichert: {len(sets)} Sets")
    return True


def _ensure_items(con, names: dict):
    con.executemany("INSERT OR IGNORE INTO items(id, name, meta_status) VALUES (?, ?, 'pending')", names.items())
    con.executemany("UPDATE items SET name=? WHERE id=? AND name IS NULL AND ? IS NOT NULL",
                    [(n, i, n) for i, n in names.items()])


# ------------------------------------------------------------ Aktualisieren

def _stale(con, kind, table, client, max_age_days):
    ensure_schema(con)
    if not con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
        return True, "noch keine Daten"
    if time.time() - int(db.get_meta(con, f"{kind}_fetched", "0") or 0) > max_age_days * 86400:
        return True, f"älter als {max_age_days} Tage"
    if client is not None:
        d, err = _get(client, "/data/wow/item-set/index" if kind == "sets" else "/data/wow/quest/category/index")
        build = _ns_build(d) if d else ""
        if build and build != db.get_meta(con, f"{kind}_build", ""):
            return True, f"neue Spielversion ({build})"
    return False, "aktuell"


def update(con, client, force=False, max_age_days=MAX_AGE_DAYS, log=print, budget=None) -> dict:
    """Quests und Sets bei Bedarf neu laden. Gibt {quests: bool, sets: bool} zurück.

    ``budget``: Sekunden für die Quest-Details in diesem Lauf (Standard ``TIME_BUDGET``)."""
    out = {}
    deadline = time.time() + (TIME_BUDGET if budget is None else budget)
    for kind, table, fetch, store, label in (("sets", "item_sets", fetch_sets, store_sets, "Sets"),
                                             ("quests", "quests", fetch_quests, store_quests, "Quests")):
        stale, why = (True, "erzwungen") if force else _stale(con, kind, table, client, max_age_days)
        if not stale:
            log(f"{label}: {why} – kein Abruf nötig.")
            out[kind] = False
            continue
        t = time.time()
        log(f"{label}: lade neu ({why}) …")
        data, build, total, errors = fetch(client, log=log, con=con, deadline=deadline)
        if data is None:
            out[kind] = False
            continue
        if errors > max(MAX_ERRORS, total * MAX_ERROR_RATIO):
            log(f"{label}: {errors} von {total} Abrufen fehlgeschlagen – Bestand bleibt unverändert.")
            out[kind] = False
            continue
        out[kind] = store(con, data, build, log=log)
        log(f"{label}: fertig in {time.time() - t:.0f} s")
    return out


# ------------------------------------------------------------------ Export

def export_data(con, group_houses: dict[str, int] | None = None) -> dict:
    """Struktur für die Website (Export und lokaler Server).

    quests.rows: [id, Titel, Zone, Typ, Fraktion, Klassen, wiederholbar, EP, Geld, [[Item-ID, zur Wahl], …]]
                 (nur Quests mit Item-Belohnung)
    sets.rows:   [id, Name, [Item-IDs], [[Teile, Bonus], …]]
    """
    ensure_schema(con)
    rewards = {}
    names = {}
    for r in con.execute("SELECT quest_id, item_id, item_name, choice FROM quest_rewards ORDER BY quest_id, choice, item_id"):
        rewards.setdefault(r[0], []).append([r[1], r[3]])
        names.setdefault(r[1], r[2])
    qrows = []
    for q in con.execute("SELECT * FROM quests ORDER BY id"):
        if q["id"] in rewards:
            qrows.append([q["id"], q["title"], q["zone"], q["type"], q["faction"], q["classes"], q["repeatable"],
                          q["xp"], q["money"], rewards[q["id"]]])
    members = {}
    for r in con.execute("SELECT set_id, item_id, item_name FROM item_set_items ORDER BY set_id, item_id"):
        members.setdefault(r[0], []).append(r[1])
        names.setdefault(r[1], r[2])
    srows = []
    for s in con.execute("SELECT * FROM item_sets ORDER BY id"):
        if s["id"] in members:
            try:
                eff = json.loads(s["effects"] or "[]")
            except ValueError:
                eff = []
            srows.append([s["id"], s["name"], members[s["id"]], eff])
    ids = set(names)
    return {
        "fetched": {"quests": int(db.get_meta(con, "quests_fetched", "0") or 0) or None,
                    "sets": int(db.get_meta(con, "sets_fetched", "0") or 0) or None},
        "quests": {"cols": ["id", "title", "zone", "type", "faction", "classes", "repeatable", "xp", "money", "rewards"],
                   "rows": qrows},
        "sets": {"cols": ["id", "name", "items", "effects"], "rows": srows},
        "items": item_table(con, ids, names),
        "prices": price_table(con, group_houses, ids),
    }
