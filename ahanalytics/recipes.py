"""Berufe und Rezepte (Herstellung) aus der Battle.net Static API für WoW: Forever.

Quelle: Namespace ``static-forever-{region}``
  /data/wow/profession/index                       → Berufe
  /data/wow/profession/{id}/skill-tier/{tier}      → Kategorien mit Rezept-IDs
  /data/wow/recipe/{id}                            → Ergebnis, Menge, Reagenzien

Die Daten ändern sich nur mit neuen Spielversionen. Sie werden deshalb lokal in
der Datenbank gehalten und höchstens wöchentlich bzw. bei einem neuen
Namespace-Build (z. B. ``static-1.60.1_70399-forever-eu``) neu geladen.

Nicht in der API enthalten: benötigte Fertigkeitsstufe, Lehrer/Fundort,
Entzauberungs-Ergebnisse.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS professions (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    type  TEXT,                          -- PRIMARY | SECONDARY
    sort  INTEGER
);
CREATE TABLE IF NOT EXISTS recipes (
    id            INTEGER PRIMARY KEY,
    profession_id INTEGER NOT NULL,
    category      TEXT,
    sort          INTEGER,               -- Reihenfolge innerhalb des Berufs (wie im Spiel)
    name          TEXT NOT NULL,
    description   TEXT,
    item_id       INTEGER,               -- hergestelltes Item (NULL z. B. bei Verzauberungen)
    item_name     TEXT,
    qty_min       REAL NOT NULL DEFAULT 1,
    qty_max       REAL NOT NULL DEFAULT 1,
    rank          INTEGER
);
CREATE INDEX IF NOT EXISTS recipes_item ON recipes(item_id);
CREATE TABLE IF NOT EXISTS recipe_reagents (
    recipe_id INTEGER NOT NULL,
    item_id   INTEGER NOT NULL,
    item_name TEXT,
    quantity  INTEGER NOT NULL,
    PRIMARY KEY (recipe_id, item_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS reagents_item ON recipe_reagents(item_id);
"""

MAX_AGE_DAYS = 7
MIN_KEEP_RATIO = 0.5   # neuer Abruf muss mindestens 50 % des Bestands liefern
MAX_ERRORS, MAX_ERROR_RATIO = 5, 0.02  # mehr fehlgeschlagene Abrufe → nichts ersetzen
PRICE_MAX_AGE = 2 * 86400  # ältere letzte Preise gelten als „derzeit nicht im AH“


def ensure_schema(con):
    con.executescript(SCHEMA)


def _ns_build(payload) -> str:
    href = ((payload or {}).get("_links") or {}).get("self", {}).get("href", "")
    m = re.search(r"namespace=([^&]+)", href)
    return m.group(1) if m else ""


def parse_quantity(q) -> tuple[float, float]:
    q = q or {}
    if q.get("value") is not None:
        v = float(q["value"])
        return v, v
    lo = float(q.get("minimum") or 1)
    hi = float(q.get("maximum") or lo)
    return lo, max(lo, hi)


def parse_recipe(payload: dict, profession_id: int, category: str | None, sort: int) -> dict | None:
    """Rezept aus /data/wow/recipe/{id} → dict, oder None wenn unbrauchbar."""
    if not payload or not payload.get("id") or not payload.get("name"):
        return None
    reagents = {}
    for r in payload.get("reagents") or []:
        it = r.get("reagent") or {}
        iid, q = it.get("id"), r.get("quantity")
        if not iid or not q or int(q) <= 0:
            continue
        prev = reagents.get(int(iid))
        reagents[int(iid)] = {"item_id": int(iid), "item_name": it.get("name"),
                              "quantity": int(q) + (prev["quantity"] if prev else 0)}
    if not reagents:
        return None
    crafted = payload.get("crafted_item") or {}
    lo, hi = parse_quantity(payload.get("crafted_quantity"))
    return {
        "id": int(payload["id"]), "profession_id": profession_id, "category": category, "sort": sort,
        "name": payload["name"], "description": payload.get("description"),
        "item_id": int(crafted["id"]) if crafted.get("id") else None, "item_name": crafted.get("name"),
        "qty_min": lo, "qty_max": hi, "rank": payload.get("rank"),
        "reagents": sorted(reagents.values(), key=lambda r: r["item_id"]),
    }


def _signature(r):
    return (r["item_id"], r["name"], tuple((x["item_id"], x["quantity"]) for x in r["reagents"]))


def dedupe(professions: list[dict], recipes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Entfernt Rezepte unbenannter Berufe, die es unter einem benannten Beruf schon gibt
    (Forever liefert z. B. „Kupfer verhütten“ zusätzlich unter einem Beruf ohne Namen),
    und vergibt verbleibenden unbenannten Berufen den Namen „Sonstiges“."""
    named = {p["id"] for p in professions if p.get("name")}
    seen = {_signature(r) for r in recipes if r["profession_id"] in named}
    out, ids = [], set()
    for r in recipes:
        if r["id"] in ids:
            continue
        if r["profession_id"] not in named and _signature(r) in seen:
            continue
        ids.add(r["id"])
        out.append(r)
    used = {r["profession_id"] for r in out}
    profs = [{**p, "name": p.get("name") or "Sonstiges"} for p in professions if p["id"] in used]
    return profs, out


def fetch_all(client, workers=8, log=print) -> tuple[list[dict], list[dict], str, int, int]:
    """Lädt alle Berufe und Rezepte.

    Gibt (Berufe, Rezepte, Namespace-Build, Anzahl Rezept-IDs, Anzahl Fehler) zurück."""
    idx, _ = client.get("/data/wow/profession/index", "static")
    build = _ns_build(idx)
    professions, todo = [], []
    for n, p in enumerate(idx.get("professions") or []):
        pd, _ = client.get(f"/data/wow/profession/{p['id']}", "static")
        professions.append({"id": int(p["id"]), "name": p.get("name"),
                            "type": (pd.get("type") or {}).get("type"), "sort": n})
        for t in pd.get("skill_tiers") or []:
            td, _ = client.get(f"/data/wow/profession/{p['id']}/skill-tier/{t['id']}", "static")
            for cat in td.get("categories") or []:
                for r in cat.get("recipes") or []:
                    todo.append((int(r["id"]), int(p["id"]), cat.get("name"), len(todo)))
    log(f"Rezepte: {len(professions)} Berufe, {len(todo)} Rezepte – lade Details …")

    def one(job):
        rid, pid, cat, sort = job
        try:
            payload, _ = client.get(f"/data/wow/recipe/{rid}", "static")
        except Exception as exc:  # noqa: BLE001 – einzelne Fehler zählen, nicht abbrechen
            return None, f"{rid}: {exc}"
        return parse_recipe(payload, pid, cat, sort), None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, todo))
    errors = [e for _, e in results if e]
    if errors:
        log(f"Rezepte: {len(errors)} Abrufe fehlgeschlagen (z. B. {errors[0]})")
    recipes = [r for r, _ in results if r]
    professions, recipes = dedupe(professions, recipes)
    return professions, recipes, build, len(todo), len(errors)


def store(con, professions, recipes, build="", log=print) -> bool:
    """Ersetzt den Rezeptbestand atomar. Liefert False (ohne Änderung), wenn der neue
    Abruf verdächtig klein ist – dann bleibt der alte Bestand erhalten."""
    ensure_schema(con)
    old = con.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    if not recipes or len(recipes) < old * MIN_KEEP_RATIO:
        log(f"Rezepte: Abruf lieferte nur {len(recipes)} statt bisher {old} – Bestand bleibt unverändert.")
        return False
    with db.transaction(con):
        con.execute("DELETE FROM recipe_reagents")
        con.execute("DELETE FROM recipes")
        con.execute("DELETE FROM professions")
        con.executemany("INSERT INTO professions(id, name, type, sort) VALUES (:id, :name, :type, :sort)",
                        professions)
        con.executemany(
            """INSERT INTO recipes(id, profession_id, category, sort, name, description, item_id, item_name,
                                   qty_min, qty_max, rank)
               VALUES (:id, :profession_id, :category, :sort, :name, :description, :item_id, :item_name,
                       :qty_min, :qty_max, :rank)""", recipes)
        con.executemany(
            "INSERT INTO recipe_reagents(recipe_id, item_id, item_name, quantity) VALUES (?,?,?,?)",
            [(r["id"], x["item_id"], x["item_name"], x["quantity"]) for r in recipes for x in r["reagents"]])
        # Items der Rezepte bekannt machen: Name sofort, Icon/Qualität über den normalen Metadaten-Abruf
        names = {}
        for r in recipes:
            if r["item_id"]:
                names[r["item_id"]] = r["item_name"]
            for x in r["reagents"]:
                names.setdefault(x["item_id"], x["item_name"])
        con.executemany("INSERT OR IGNORE INTO items(id, name, meta_status) VALUES (?, ?, 'pending')",
                        names.items())
        con.executemany("UPDATE items SET name=? WHERE id=? AND name IS NULL AND ? IS NOT NULL",
                        [(n, i, n) for i, n in names.items()])
        db.set_meta(con, "recipes_fetched", int(time.time()))
        db.set_meta(con, "recipes_build", build)
    log(f"Rezepte gespeichert: {len(recipes)} Rezepte in {len(professions)} Berufen, {len(names)} Items"
        + (f" ({build})" if build else ""))
    return True


def is_stale(con, client=None, max_age_days=MAX_AGE_DAYS) -> tuple[bool, str]:
    ensure_schema(con)
    if not con.execute("SELECT 1 FROM recipes LIMIT 1").fetchone():
        return True, "noch keine Rezepte"
    fetched = int(db.get_meta(con, "recipes_fetched", "0") or 0)
    if time.time() - fetched > max_age_days * 86400:
        return True, f"älter als {max_age_days} Tage"
    if client is not None:
        try:
            idx, _ = client.get("/data/wow/profession/index", "static")
            build = _ns_build(idx)
            if build and build != db.get_meta(con, "recipes_build", ""):
                return True, f"neue Spielversion ({build})"
        except Exception:  # noqa: BLE001
            pass
    return False, "aktuell"


def update(con, client, force=False, max_age_days=MAX_AGE_DAYS, log=print) -> bool:
    stale, why = (True, "erzwungen") if force else is_stale(con, client, max_age_days)
    if not stale:
        log(f"Rezepte: {why} – kein Abruf nötig.")
        return False
    log(f"Rezepte: lade neu ({why}) …")
    t = time.time()
    professions, recipes, build, total, errors = fetch_all(client, log=log)
    if errors > max(MAX_ERRORS, total * MAX_ERROR_RATIO):
        log(f"Rezepte: {errors} von {total} Abrufen fehlgeschlagen – Bestand bleibt unverändert.")
        return False
    ok = store(con, professions, recipes, build, log=log)
    log(f"Rezepte: fertig in {time.time() - t:.0f} s")
    return ok


# ------------------------------------------------------------------ Export

def export_data(con, group_houses: dict[str, int] | None = None) -> dict:
    """Kompakte Struktur für die Website (identisch für Export und lokalen Server).

    recipes.rows: [id, Beruf-Index, Kategorie-Index, Name, Item-ID|null, Menge min, Menge max,
                   [[Reagenz-ID, Menge], …]]
    items: {id: [Name, Qualität, Icon, im AH (0/1)]}
    prices: {allianz|horde|neutral: {id: [Tiefstpreis, Median, Stück]}}
    """
    ensure_schema(con)
    profs = [dict(r) for r in con.execute("SELECT id, name, type FROM professions ORDER BY sort, id")]
    pidx = {p["id"]: i for i, p in enumerate(profs)}
    rows = con.execute("SELECT * FROM recipes ORDER BY profession_id, sort, id").fetchall()
    reag = {}
    for r in con.execute("SELECT recipe_id, item_id, quantity FROM recipe_reagents ORDER BY recipe_id, item_id"):
        reag.setdefault(r[0], []).append([r[1], r[2]])
    cats, cidx = [], {}
    out_rows, item_ids = [], set()
    for r in rows:
        if r["profession_id"] not in pidx or r["id"] not in reag:
            continue
        c = r["category"] or ""
        if c not in cidx:
            cidx[c] = len(cats)
            cats.append(c)
        q1 = r["qty_min"]
        q2 = r["qty_max"]
        out_rows.append([r["id"], pidx[r["profession_id"]], cidx[c], r["name"], r["item_id"],
                         int(q1) if q1 == int(q1) else q1, int(q2) if q2 == int(q2) else q2, reag[r["id"]]])
        if r["item_id"]:
            item_ids.add(r["item_id"])
        item_ids.update(x[0] for x in reag[r["id"]])
    in_ah = {r[0] for r in con.execute("SELECT DISTINCT item_id FROM item_summary")}
    items = {}
    fallback = {}
    for r in rows:
        if r["item_id"] and r["item_name"]:
            fallback[r["item_id"]] = r["item_name"]
    for r in con.execute("SELECT item_id, item_name FROM recipe_reagents"):
        fallback.setdefault(r[0], r[1])
    meta = {}
    ids = sorted(item_ids)
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        for r in con.execute(f"SELECT id, name, quality, icon FROM items WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            meta[r[0]] = r
    for iid in ids:
        m = meta.get(iid)
        name = (m["name"] if m and m["name"] else None) or fallback.get(iid) or f"Item #{iid}"
        items[str(iid)] = [name, m["quality"] if m else None, m["icon"] if m else None, 1 if iid in in_ah else 0]
    prices = {}
    for key, hid in (group_houses or {}).items():
        p = {}
        latest = con.execute("SELECT MAX(ts) FROM snapshots WHERE house_id=?", (hid,)).fetchone()[0] or 0
        for r in con.execute("""SELECT item_id, last_min, last_median, last_qty FROM item_summary
                                WHERE house_id=? AND last_min IS NOT NULL AND last_ts >= ?""",
                             (hid, latest - PRICE_MAX_AGE)):
            if r[0] in item_ids:
                p[str(r[0])] = [r[1], r[2], r[3]]
        prices[key] = {"house_id": hid, "items": p}
    return {
        "build": db.get_meta(con, "recipes_build", ""),
        "fetched": int(db.get_meta(con, "recipes_fetched", "0") or 0) or None,
        "professions": profs, "categories": cats,
        "recipes": {"cols": ["id", "prof", "cat", "name", "item", "qmin", "qmax", "reagents"], "rows": out_rows},
        "items": items, "prices": prices,
    }


def group_houses(con) -> dict[str, int]:
    """Fraktions-Sammelhäuser (allianz/horde/neutral) → Haus-ID."""
    out = {}
    for r in con.execute("SELECT id, source, ext_id FROM houses"):
        k = db.house_kind(r["source"], r["ext_id"])
        if k in db.GROUPS and k not in out:
            out[k] = r["id"]
    return out
