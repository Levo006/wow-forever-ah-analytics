"""Liquiditäts-/Umsatzsignale pro Item und Auktionshaus.

Verkäufe sind in den Auktionsdaten nie direkt sichtbar – nur Angebote.
Deshalb kombiniert diese Auswertung mehrere beobachtbare Signale:

* ``presence``: Anteil der geprüften Stunden-Snapshots mit Angebot.
* ``events``: Umsatzsignale = Ausverkäufe (Item verschwindet komplett)
  plus Mengeneinbrüche zwischen zwei aufeinanderfolgenden Snapshots.
* ``vanish``: nur die kompletten Ausverkäufe.
* ``units``: über diese Ereignisse verschwundene Stückzahl (obere
  Schätzung – Abläufe und Zurücknahmen sind nicht von Verkäufen
  unterscheidbar).

Snapshots mit ``complete = 0`` (Teilstände, z. B. nach einem Abruffehler)
fließen nicht ein; ihre Zeitnachbarn werden nicht über die Lücke hinweg
mit Normalabstand verglichen, damit ein Scan-Ausfall nicht wie ein
Massenverkauf aussieht. Verdichtete Tagesmittel fließen ebenfalls nicht
ein. Es wird nie behauptet, dass ein tatsächlicher Verkauf stattfand –
die Kennzahlen heißen bewusst „Umsatzsignale".

Der Score (0–100) gewichtet Präsenz und das hausinterne Perzentil der
Ereignisrate, damit Neutral fair gegen Neutral bewertet wird.
"""
from __future__ import annotations

import time
from bisect import bisect_right

from . import db

WINDOW = 7 * 86400          # Auswertungsfenster für die aktuellen Kennzahlen
MAX_PAIR_GAP = 3 * 3600     # längere Lücke = kein Vergleich (z. B. Teilsnapshot dazwischen)
DAILY_KEEP_DAYS = 90        # Aufbewahrung der täglichen Liquiditäts-Historie


def _valid_hours(con, hid, lo, hi):
    """Geprüfte Stunden-Snapshots eines Hauses im Fenster [lo, hi)."""
    rows = [r[0] for r in con.execute(
        """SELECT ts FROM snapshots WHERE house_id=? AND ts>=? AND ts<?
           AND COALESCE(complete,1)=1 ORDER BY ts""", (hid, lo, hi))]
    return [t for t in rows if not db.is_daily_ts(t)]


def _series(con, hid, lo, hi, valid_set):
    """item_id -> {ts: quantity}, nur zu geprüften Zeitpunkten."""
    series = {}
    for item, ts, qty in con.execute(
            "SELECT item_id, ts, quantity FROM prices WHERE house_id=? AND ts>=? AND ts<?",
            (hid, lo, hi)):
        if ts in valid_set:
            series.setdefault(item, {})[ts] = qty or 0
    return series


def _signals(series, valid, pairs, pair_hours):
    """Kernauswertung: Präsenz und Umsatzsignale je Item."""
    expected = len(valid)
    out = {}
    for item, qs in series.items():
        vanish = drops = units = 0
        for a, b in pairs:
            qa = qs.get(a)
            if qa is None:
                continue
            qb = qs.get(b)
            if qb is None:
                vanish += 1
                units += qa
            elif qb < qa:
                drops += 1
                units += qa - qb
        events = vanish + drops
        out[item] = {
            "expected": expected,
            "present": len(qs),
            "presence": len(qs) / expected if expected else 0.0,
            "events": events,
            "vanish": vanish,
            "units": units,
            "rate": events / pair_hours * 24 if pair_hours else 0.0,
        }
    return out


def compute_house(con, hid, latest=None):
    """Aktuelle 7-Tage-Kennzahlen eines Hauses: {item_id: {…, score}}."""
    if latest is None:
        row = con.execute(
            "SELECT MAX(ts) FROM snapshots WHERE house_id=? AND COALESCE(complete,1)=1",
            (hid,)).fetchone()
        latest = row[0] if row else None
    if latest is None:
        return {}
    lo, hi = latest - WINDOW, latest + 1
    valid = _valid_hours(con, hid, lo, hi)
    if not valid:
        return {}
    pairs = [(a, b) for a, b in zip(valid, valid[1:]) if b - a <= MAX_PAIR_GAP]
    pair_hours = sum(b - a for a, b in pairs) / 3600.0
    out = _signals(_series(con, hid, lo, hi, set(valid)), valid, pairs, pair_hours)
    rates = sorted(v["rate"] for v in out.values())
    n = len(rates)
    for v in out.values():
        pct = bisect_right(rates, v["rate"]) / n if n else 0.0
        v["score"] = round(100 * (0.4 * v["presence"] + 0.6 * pct))
    return out


def update_liquidity(con, house_ids=None, log=print):
    """Schreibt ``item_liquidity`` neu und ergänzt fehlende Tageszeilen."""
    if house_ids is None:
        house_ids = [r[0] for r in con.execute(
            "SELECT id FROM houses WHERE granularity='hour'")]
    t0 = time.time()
    now = int(time.time())
    total = 0
    for hid in house_ids:
        stats = compute_house(con, hid)
        con.execute("DELETE FROM item_liquidity WHERE house_id=?", (hid,))
        con.executemany(
            """INSERT INTO item_liquidity(house_id,item_id,presence,events,vanish,units,rate,score,computed_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ((hid, i, round(v["presence"], 4), v["events"], v["vanish"], v["units"],
              round(v["rate"], 3), v["score"], now) for i, v in stats.items()))
        total += len(stats)
        backfill_daily(con, hid)
    if total:
        log(f"Liquidität aktualisiert: {total} Einträge ({time.time() - t0:.1f} s)")
    return total


def _daily_rows(con, hid, day):
    """Umsatzsignale eines abgeschlossenen UTC-Kalendertags aus Stundenwerten."""
    lo, hi = day * 86400, (day + 1) * 86400
    valid = _valid_hours(con, hid, lo, hi)
    if not valid:
        return {}
    pairs = [(a, b) for a, b in zip(valid, valid[1:]) if b - a <= MAX_PAIR_GAP]
    pair_hours = sum(b - a for a, b in pairs) / 3600.0
    return _signals(_series(con, hid, lo, hi, set(valid)), valid, pairs, pair_hours)


def backfill_daily(con, hid, today=None, log=print):
    """Ergänzt fehlende Tageszeilen der Sammelmärkte aus den Stundendaten.

    Nur die Fraktions-Sammelmärkte bekommen eine lange Tageshistorie;
    Einzelhäuser behalten ihre Stundendaten ohnehin nur 14 Tage. Läuft
    idempotent bei jedem Sammler-Durchlauf, so steht die Historie auch für
    Tage zur Verfügung, die vor der Einführung dieser Auswertung liegen.
    """
    row = con.execute("SELECT source, ext_id FROM houses WHERE id=?", (hid,)).fetchone()
    if not row or db.house_kind(row[0], row[1]) not in db.GROUPS:
        return 0
    today = today if today is not None else int(time.time()) // 86400
    have = {r[0] for r in con.execute(
        "SELECT day FROM liquidity_daily WHERE house_id=?", (hid,))}
    days = {r[0] for r in con.execute(
        f"SELECT DISTINCT ts/86400 FROM prices WHERE house_id=? AND ts%86400 != {db.DAILY_OFFSET}",
        (hid,))}
    written = 0
    for day in sorted(d for d in days - have if d < today):
        stats = _daily_rows(con, hid, day)
        if not stats:
            continue
        con.executemany(
            """INSERT OR REPLACE INTO liquidity_daily
               (house_id,item_id,day,expected,present,events,vanish,units)
               VALUES (?,?,?,?,?,?,?,?)""",
            ((hid, i, day, v["expected"], v["present"], v["events"], v["vanish"], v["units"])
             for i, v in stats.items()))
        written += 1
    con.execute("DELETE FROM liquidity_daily WHERE house_id=? AND day < ?",
                (hid, today - DAILY_KEEP_DAYS))
    if written:
        log(f"Liquiditäts-Tageshistorie Haus {hid}: {written} Tage ergänzt")
    return written
