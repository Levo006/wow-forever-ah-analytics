"""SQLite-Speicher: Schema, Upserts und die vorberechnete Item-Zusammenfassung.

Designentscheidungen
--------------------
* Preise in Kupfer (Integer). 1 Gold = 10 000 Kupfer.
* ``prices`` ist die Zeitreihe: eine Zeile pro (Auktionshaus, Item, Zeitpunkt).
  WITHOUT ROWID + Primärschlüssel (house_id, item_id, ts) => Zeitreihen eines
  Items liegen physisch zusammen; Range-Scans sind sehr schnell.
* ``item_summary`` ist eine materialisierte Sicht (nach jedem Import neu
  berechnet). Suche, Top-Movers, Deals und Kategorien lesen nur daraus.
* ``ladders`` hält die Preisstaffel (Preisstufe -> Menge) pro Snapshot, aber
  nur für die jüngsten N Snapshots je Haus (Speicherplatz).
"""
from __future__ import annotations

import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS houses (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source   TEXT NOT NULL,              -- battlenet | auctionator | demo | ...
    ext_id   TEXT NOT NULL,              -- ID in der Quelle (z. B. Haus-ID, Realm-Name)
    region   TEXT NOT NULL DEFAULT '',
    name     TEXT NOT NULL,
    faction  TEXT NOT NULL DEFAULT '',   -- Allianz | Horde | Neutral | ''
    ruleset  TEXT NOT NULL DEFAULT '',
    granularity TEXT NOT NULL DEFAULT 'hour',  -- hour | day
    UNIQUE (source, ext_id)
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY,
    name          TEXT,
    quality       INTEGER,               -- 0 grau … 5 legendär
    class_id      INTEGER,
    class_name    TEXT,
    subclass_id   INTEGER,
    subclass_name TEXT,
    icon          TEXT,                  -- Icon-Name oder volle URL
    item_level    INTEGER,
    vendor_sell   INTEGER,
    is_commodity  INTEGER,
    meta_status   TEXT DEFAULT 'pending',-- ok | missing | pending
    meta_checked  INTEGER
);
CREATE INDEX IF NOT EXISTS items_class ON items(class_id, subclass_id);
CREATE INDEX IF NOT EXISTS items_name ON items(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    house_id      INTEGER NOT NULL REFERENCES houses(id),
    ts            INTEGER NOT NULL,      -- Unix-Sekunden (UTC)
    source        TEXT NOT NULL,
    item_count    INTEGER,
    auction_count INTEGER,
    total_value   INTEGER,               -- Summe min*Menge (Marktvolumen-Schätzung)
    imported_at   INTEGER,
    UNIQUE (house_id, ts)
);

CREATE TABLE IF NOT EXISTS prices (
    house_id  INTEGER NOT NULL,
    item_id   INTEGER NOT NULL,
    ts        INTEGER NOT NULL,
    min       INTEGER,                   -- günstigster Stückpreis
    p10       INTEGER,                   -- 10%-Perzentil (mengengewichtet)
    median    INTEGER,                   -- Median (mengengewichtet)
    mean      INTEGER,                   -- Mittelwert (mengengewichtet)
    max       INTEGER,
    quantity  INTEGER,                   -- angebotene Stückzahl
    auctions  INTEGER,                   -- Anzahl Auktionen
    PRIMARY KEY (house_id, item_id, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS prices_ts ON prices(house_id, ts);

CREATE TABLE IF NOT EXISTS ladders (
    snapshot_id INTEGER NOT NULL,
    item_id     INTEGER NOT NULL,
    data        TEXT NOT NULL,           -- JSON [[preis, menge], ...] aufsteigend
    PRIMARY KEY (snapshot_id, item_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS item_summary (
    house_id     INTEGER NOT NULL,
    item_id      INTEGER NOT NULL,
    first_ts     INTEGER,
    last_ts      INTEGER,
    points       INTEGER,
    last_min     INTEGER,
    last_median  INTEGER,
    last_qty     INTEGER,
    last_auctions INTEGER,
    prev24_min   INTEGER,
    prev24_median INTEGER,
    prev7d_min   INTEGER,
    prev7d_median INTEGER,
    avg7_min     REAL,
    avg30_min    REAL,
    avg7_median  REAL,
    avg30_median REAL,
    avg7_qty     REAL,
    cv7_min      REAL,                   -- Variationskoeffizient (Std/Mittel) 7 Tage
    low30_min    INTEGER,
    high30_min   INTEGER,
    PRIMARY KEY (house_id, item_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS summary_item ON item_summary(item_id);

CREATE TABLE IF NOT EXISTS import_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,
    source   TEXT NOT NULL,
    status   TEXT NOT NULL,              -- ok | error | skipped
    message  TEXT
);
"""

DAY = 86400


def default_db_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "ah.sqlite")


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or default_db_path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # isolation_level=None: Autocommit; Transaktionen explizit über transaction()
    con = sqlite3.connect(path, timeout=30, check_same_thread=False, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-65536")
    con.create_function("sqrt", 1, lambda x: math.sqrt(x) if x is not None and x >= 0 else None,
                        deterministic=True)
    con.create_function("fold", 1, fold, deterministic=True)
    con.executescript(SCHEMA)
    _migrate(con)
    return con


_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a",
                       "â": "a", "ê": "e", "î": "i", "ô": "o", "û": "u", "ñ": "n", "'": "", "’": ""})


def fold(text):
    """Suchschlüssel: klein, Umlaute → Grundbuchstabe, Apostrophe weg (Königsblut → konigsblut)."""
    if text is None:
        return None
    return str(text).lower().translate(_FOLD)


def _migrate(con):
    """Ergänzt Spalten älterer Datenbanken."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(items)")}
    if "name_en" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN name_en TEXT")
    if "en_checked" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN en_checked INTEGER")
    if "bn_checked" not in cols:
        con.execute("ALTER TABLE items ADD COLUMN bn_checked INTEGER")
    for col, typ in (("required_level", "INTEGER"), ("slot", "TEXT"), ("details", "TEXT"),
                     ("details_v", "INTEGER")):
        if col not in cols:
            con.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")


@contextmanager
def transaction(con: sqlite3.Connection):
    con.execute("BEGIN")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


# ---------------------------------------------------------------- Metadaten

def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key, value):
    con.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def bump_data_version(con):
    v = int(get_meta(con, "data_version", "0")) + 1
    set_meta(con, "data_version", v)
    return v


def data_version(con) -> int:
    return int(get_meta(con, "data_version", "0"))


# ------------------------------------------------------------------ Häuser

def upsert_house(con, source, ext_id, name, region="", faction="", ruleset="",
                 granularity="hour") -> int:
    con.execute(
        """INSERT INTO houses(source, ext_id, region, name, faction, ruleset, granularity)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(source, ext_id) DO UPDATE SET
             region=excluded.region, name=excluded.name, faction=excluded.faction,
             ruleset=excluded.ruleset, granularity=excluded.granularity""",
        (source, str(ext_id), region, name, faction, str(ruleset), granularity))
    return con.execute("SELECT id FROM houses WHERE source=? AND ext_id=?",
                       (source, str(ext_id))).fetchone()[0]


# ------------------------------------------------------------------- Items

ITEM_FIELDS = ("name", "name_en", "quality", "class_id", "class_name", "subclass_id",
               "subclass_name", "icon", "item_level", "vendor_sell", "is_commodity",
               "required_level", "slot", "details")


def ensure_items(con, item_ids):
    con.executemany("INSERT OR IGNORE INTO items(id, meta_status) VALUES (?, 'pending')",
                    ((int(i),) for i in item_ids))


def upsert_item(con, item_id: int, status="ok", **fields):
    if fields.get("class_id") is not None and not fields.get("class_name"):
        from .itemmeta import CLASS_NAMES
        fields["class_name"] = CLASS_NAMES.get(int(fields["class_id"]), f"Klasse {fields['class_id']}")
    cols = [f for f in ITEM_FIELDS if f in fields]
    con.execute("INSERT OR IGNORE INTO items(id) VALUES (?)", (item_id,))
    sets = ", ".join(f"{c}=?" for c in cols)
    sets = (sets + ", " if sets else "") + "meta_status=?, meta_checked=?"
    con.execute(f"UPDATE items SET {sets} WHERE id=?",
                [fields[c] for c in cols] + [status, int(time.time()), item_id])


# ------------------------------------------------------------ Marktgruppen
#
# Neben den einzelnen Auktionshäusern gibt es zwei Sammel-"Häuser":
#   <region>-alle     alle Fraktions-Auktionshäuser (Allianz + Horde) zusammen
#   <region>-neutral  alle neutralen Auktionshäuser (Schwarzmeerräuber) zusammen
# Sie verhalten sich wie normale Häuser (eigene Zeitreihe, Summary, Export).

GROUPS = {
    "alle": ("Alle Auktionshäuser", "Alle", ("Allianz", "Horde")),
    "neutral": ("Neutral · Schwarzmeerräuber", "Neutral", ("Neutral",)),
}


def house_kind(source: str, ext_id: str) -> str:
    """alle | neutral | haus | scan"""
    ext = str(ext_id)
    for key in GROUPS:
        if ext.endswith("-" + key):
            return key
    return "scan" if source == "auctionator" else "haus"


def group_for_faction(faction: str) -> str:
    return "neutral" if faction == "Neutral" else "alle"


def upsert_group_house(con, source: str, region: str, key: str) -> int:
    name, faction, _ = GROUPS[key]
    return upsert_house(con, source, f"{region}-{key}", name, region=region, faction=faction,
                        granularity="hour")


def apply_typical_min(stats: dict, house_mins: list[dict]) -> None:
    """Setzt in Sammelgruppen "min" auf den typischen Tiefstpreis.

    Der absolute Tiefstpreis über alle Häuser ist oft ein einzelnes Lockangebot
    irgendwo. Stattdessen: je Haus die günstige Preisstufe (10 %-Perzentil der
    Menge), davon der mengengewichtete Median über die Häuser (große Märkte
    zählen mehr als kleine). ``house_mins``: [{item: (preis, menge)}, …] je Haus.
    """
    from .aggregate import weighted_percentile
    for iid, st in stats.items():
        vals = sorted((m[iid][0], m[iid][1]) for m in house_mins if iid in m)
        if vals:
            st["min"] = weighted_percentile(vals, 0.5)


def group_members(con, group_id: int) -> list[int]:
    g = con.execute("SELECT source, ext_id, region FROM houses WHERE id=?", (group_id,)).fetchone()
    key = house_kind(g[0], g[1])
    factions = GROUPS[key][2]
    rows = con.execute(
        f"""SELECT id, source, ext_id FROM houses WHERE source=? AND region=?
            AND faction IN ({','.join('?' * len(factions))})""", (g[0], g[2], *factions)).fetchall()
    return [r[0] for r in rows if house_kind(r[1], r[2]) == "haus"]


def build_group_snapshots(con, group_id: int, log=print) -> int:
    """Ergänzt fehlende Stunden der Sammelgruppe aus den Kennzahlen der Einzelhäuser.

    "min" ist wie beim Sammler der typische Tiefstpreis (siehe apply_typical_min).
    Exakt sind Max, Menge, Auktionen und Mittelwert; Median und 10 %-Perzentil
    werden als mengengewichteter Median der Einzelwerte angenähert. (Neue Snapshots
    berechnet der Sammler exakt aus allen Einzelauktionen.)
    """
    from .aggregate import weighted_percentile
    members = group_members(con, group_id)
    if not members:
        return 0
    have = {ts // 3600 for (ts,) in con.execute(
        "SELECT ts FROM snapshots WHERE house_id=?", (group_id,))}
    buckets: dict[int, dict[int, int]] = {}
    for hid, ts in con.execute(
            f"SELECT house_id, ts FROM snapshots WHERE house_id IN ({','.join('?' * len(members))})",
            members):
        b = buckets.setdefault(ts // 3600, {})
        b[hid] = max(b.get(hid, 0), ts)
    # nur die Vorgeschichte (vor dem ersten Gruppen-Snapshot) – danach rechnet der Sammler exakt
    first = min(have) if have else None
    written = 0
    for bucket in sorted(b for b in buckets if b not in have and (first is None or b < first)):
        acc: dict[int, dict] = {}
        for hid, ts in buckets[bucket].items():
            for r in con.execute("""SELECT item_id, min, p10, median, mean, max, quantity, auctions
                                    FROM prices WHERE house_id=? AND ts=?""", (hid, ts)):
                q = r[6] or 0
                if not r[1] or q <= 0:
                    continue
                a = acc.setdefault(r[0], {"max": r[5] or r[1], "quantity": 0,
                                          "auctions": 0, "_sum": 0, "_med": [], "_p10": [], "_min": []})
                a["_min"].append(((r[2] or r[1]), q))
                a["max"] = max(a["max"], r[5] or r[1])
                a["quantity"] += q
                a["auctions"] += r[7] or 0
                a["_sum"] += (r[4] or r[3] or r[1]) * q
                a["_med"].append(((r[3] or r[1]), q))
                a["_p10"].append(((r[2] or r[1]), q))
        stats = {}
        for iid, a in acc.items():
            stats[iid] = {"min": weighted_percentile(sorted(a["_min"]), 0.5),
                          "max": a["max"], "quantity": a["quantity"],
                          "auctions": a["auctions"] or None,
                          "mean": int(round(a["_sum"] / a["quantity"])),
                          "median": weighted_percentile(sorted(a["_med"]), 0.5),
                          "p10": weighted_percentile(sorted(a["_p10"]), 0.5)}
        if stats:
            write_snapshot(con, group_id, max(buckets[bucket].values()), "sammel", stats)
            written += 1
    if written:
        log(f"Sammelgruppe {group_id}: {written} Zeitpunkte aus Einzelhäusern ergänzt")
    return written


# --------------------------------------------------------------- Snapshots

def snapshot_exists(con, house_id, ts) -> bool:
    return con.execute("SELECT 1 FROM snapshots WHERE house_id=? AND ts=?",
                       (house_id, ts)).fetchone() is not None


def write_snapshot(con, house_id: int, ts: int, source: str, stats: dict,
                   ladders: dict | None = None, replace=True) -> int:
    """Schreibt einen kompletten Snapshot.

    ``stats``: {item_id: dict(min, p10, median, mean, max, quantity, auctions)}
    ``ladders``: optional {item_id: [[preis, menge], ...]}
    """
    import json
    ensure_items(con, stats.keys())
    auction_count = sum((s.get("auctions") or 0) for s in stats.values())
    total_value = sum((s.get("min") or 0) * (s.get("quantity") or 0) for s in stats.values())
    con.execute(
        """INSERT INTO snapshots(house_id, ts, source, item_count, auction_count, total_value, imported_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(house_id, ts) DO UPDATE SET source=excluded.source,
             item_count=excluded.item_count, auction_count=excluded.auction_count,
             total_value=excluded.total_value, imported_at=excluded.imported_at""",
        (house_id, ts, source, len(stats), auction_count, total_value, int(time.time())))
    snap_id = con.execute("SELECT id FROM snapshots WHERE house_id=? AND ts=?",
                          (house_id, ts)).fetchone()[0]
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    con.executemany(
        f"""{verb} INTO prices(house_id, item_id, ts, min, p10, median, mean, max, quantity, auctions)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ((house_id, int(iid), ts, s.get("min"), s.get("p10"), s.get("median"), s.get("mean"),
          s.get("max"), s.get("quantity"), s.get("auctions")) for iid, s in stats.items()))
    if ladders:
        con.executemany("INSERT OR REPLACE INTO ladders(snapshot_id, item_id, data) VALUES (?,?,?)",
                        ((snap_id, int(iid), json.dumps(lad, separators=(",", ":")))
                         for iid, lad in ladders.items()))
    return snap_id


def prune_ladders(con, keep_per_house: int = 48):
    con.execute(
        """DELETE FROM ladders WHERE snapshot_id IN (
             SELECT id FROM (
               SELECT id, ROW_NUMBER() OVER (PARTITION BY house_id ORDER BY ts DESC) rn
               FROM snapshots) WHERE rn > ?)""", (keep_per_house,))


def log_import(con, source, status, message):
    con.execute("INSERT INTO import_log(ts, source, status, message) VALUES (?,?,?,?)",
                (int(time.time()), source, status, message[:2000]))


# --------------------------------------------------------- Zusammenfassung

def refresh_summary(con, house_ids=None):
    """Berechnet ``item_summary`` neu (für alle oder bestimmte Häuser).

    Fenster (24 h / 7 d / 30 d) beziehen sich auf den jüngsten Snapshot des
    jeweiligen Hauses, nicht auf die Uhrzeit – so bleiben Auswertungen auch für
    ältere Importe sinnvoll.
    """
    if house_ids is None:
        house_ids = [r[0] for r in con.execute("SELECT id FROM houses")]
    for hid in house_ids:
        row = con.execute("SELECT MAX(ts) FROM prices WHERE house_id=?", (hid,)).fetchone()
        con.execute("DELETE FROM item_summary WHERE house_id=?", (hid,))
        if not row or row[0] is None:
            continue
        hmax = row[0]
        con.execute(
            """
            INSERT INTO item_summary
            WITH agg AS (
              SELECT item_id,
                     MIN(ts) first_ts, MAX(ts) last_ts, COUNT(*) points,
                     AVG(CASE WHEN ts > :h - 7*86400 THEN min END)     avg7_min,
                     AVG(CASE WHEN ts > :h - 30*86400 THEN min END)    avg30_min,
                     AVG(CASE WHEN ts > :h - 7*86400 THEN median END)  avg7_median,
                     AVG(CASE WHEN ts > :h - 30*86400 THEN median END) avg30_median,
                     AVG(CASE WHEN ts > :h - 7*86400 THEN quantity END) avg7_qty,
                     AVG(CASE WHEN ts > :h - 7*86400 THEN 1.0*min*min END) sq7,
                     MIN(CASE WHEN ts > :h - 30*86400 THEN min END) low30,
                     MAX(CASE WHEN ts > :h - 30*86400 THEN min END) high30
              FROM prices WHERE house_id = :hid GROUP BY item_id
            )
            SELECT :hid, a.item_id, a.first_ts, a.last_ts, a.points,
                   l.min, l.median, l.quantity, l.auctions,
                   p24.min, p24.median, p7.min, p7.median,
                   a.avg7_min, a.avg30_min, a.avg7_median, a.avg30_median, a.avg7_qty,
                   CASE WHEN a.avg7_min > 0 AND a.sq7 - a.avg7_min*a.avg7_min > 0
                        THEN sqrt(a.sq7 - a.avg7_min*a.avg7_min) / a.avg7_min ELSE 0 END,
                   a.low30, a.high30
            FROM agg a
            JOIN prices l ON l.house_id = :hid AND l.item_id = a.item_id AND l.ts = a.last_ts
            LEFT JOIN prices p24 ON p24.house_id = :hid AND p24.item_id = a.item_id AND p24.ts = (
                 SELECT MAX(ts) FROM prices WHERE house_id = :hid AND item_id = a.item_id
                   AND ts <= a.last_ts - 86400 + 600)
            LEFT JOIN prices p7 ON p7.house_id = :hid AND p7.item_id = a.item_id AND p7.ts = (
                 SELECT MAX(ts) FROM prices WHERE house_id = :hid AND item_id = a.item_id
                   AND ts <= a.last_ts - 7*86400 + 600)
            """, {"hid": hid, "h": hmax})


DAILY_OFFSET = 43199  # Tageswerte liegen auf 11:59:59 UTC – kollidiert nie mit einem Snapshot


def is_daily_ts(ts: int) -> bool:
    return ts % 86400 == DAILY_OFFSET


def compact(con, hourly_days: int = 7, keep_days: int = 180, house_hourly_days: int = 2,
            house_keep_days: int = 14, log=print) -> dict:
    """Verdichtet stündliche Daten älter als ``hourly_days`` zu einem Wert pro Tag.

    Nur vollständige Tage werden verdichtet (jeder Tag genau einmal). Preise werden
    gemittelt, ``max`` als Maximum. Daten älter als ``keep_days`` werden gelöscht.
    Einzelne Auktionshäuser (nur Filter/Detail) behalten kürzer:
    ``house_hourly_days`` stündlich, ``house_keep_days`` insgesamt – die beiden
    Sammelmärkte tragen die lange Historie. Ohne das wüchse die Datenbank auf ~1 GB.
    """
    stats = {"days": 0, "deleted": 0}
    stats["rows_before"] = con.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    d = DAILY_OFFSET
    for hid, latest, source, ext_id in con.execute(
            "SELECT h.id, MAX(s.ts), h.source, h.ext_id FROM houses h JOIN snapshots s ON s.house_id=h.id "
            "WHERE h.granularity='hour' GROUP BY h.id").fetchall():
        single = house_kind(source, ext_id) == "haus"
        h_days = house_hourly_days if single else hourly_days
        k_days = house_keep_days if single else keep_days
        cut_day = (latest - h_days * 86400) // 86400 * 86400
        with transaction(con):
            days = con.execute(
                f"SELECT DISTINCT ts / 86400 FROM prices WHERE house_id=? AND ts < ? AND ts % 86400 != {d}",
                (hid, cut_day)).fetchall()
            if days:
                con.execute(
                    f"""INSERT OR REPLACE INTO prices(house_id, item_id, ts, min, p10, median, mean, max, quantity, auctions)
                        SELECT house_id, item_id, (ts / 86400) * 86400 + {d},
                               CAST(ROUND(AVG(min)) AS INTEGER), CAST(ROUND(AVG(p10)) AS INTEGER),
                               CAST(ROUND(AVG(median)) AS INTEGER), CAST(ROUND(AVG(mean)) AS INTEGER),
                               MAX(max), CAST(ROUND(AVG(quantity)) AS INTEGER), CAST(ROUND(AVG(auctions)) AS INTEGER)
                        FROM prices WHERE house_id=? AND ts < ? AND ts % 86400 != {d}
                        GROUP BY item_id, ts / 86400""", (hid, cut_day))
                con.execute(f"DELETE FROM prices WHERE house_id=? AND ts < ? AND ts % 86400 != {d}", (hid, cut_day))
                con.execute(
                    f"""INSERT OR IGNORE INTO snapshots(house_id, ts, source, item_count, auction_count, total_value, imported_at)
                        SELECT house_id, (ts / 86400) * 86400 + {d}, 'tagesmittel',
                               CAST(ROUND(AVG(item_count)) AS INTEGER), CAST(ROUND(AVG(auction_count)) AS INTEGER),
                               CAST(ROUND(AVG(total_value)) AS INTEGER), MAX(imported_at)
                        FROM snapshots WHERE house_id=? AND ts < ? AND ts % 86400 != {d}
                        GROUP BY ts / 86400""", (hid, cut_day))
                con.execute(f"""DELETE FROM ladders WHERE snapshot_id IN (SELECT id FROM snapshots
                                WHERE house_id=? AND ts < ? AND ts % 86400 != {d})""", (hid, cut_day))
                con.execute(f"DELETE FROM snapshots WHERE house_id=? AND ts < ? AND ts % 86400 != {d}",
                            (hid, cut_day))
                stats["days"] += len(days)
            old = latest - k_days * 86400
            stats["deleted"] += con.execute("DELETE FROM prices WHERE house_id=? AND ts < ?", (hid, old)).rowcount
            con.execute("DELETE FROM snapshots WHERE house_id=? AND ts < ?", (hid, old))
    if stats["days"] or stats["deleted"]:
        with transaction(con):
            refresh_summary(con)
            bump_data_version(con)
    stats["rows_after"] = con.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    log(f"Verdichtet: {stats['days']} Haus-Tage, Preiszeilen {stats['rows_before']} -> {stats['rows_after']}")
    return stats


class ConnectionPool:
    """Eine Verbindung pro Thread (SQLite + WAL erlaubt parallele Leser)."""

    def __init__(self, path):
        self.path = path
        self.local = threading.local()

    def get(self) -> sqlite3.Connection:
        con = getattr(self.local, "con", None)
        if con is None:
            con = connect(self.path)
            self.local.con = con
        return con
