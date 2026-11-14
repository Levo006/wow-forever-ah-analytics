"""Item-Metadaten (Name, Qualität, Klasse, Unterklasse, Icon) von Wowhead.

Wowhead führt eine eigene Datenbank für WoW: Forever:
``https://www.wowhead.com/forever/de/item=<id>&xml`` liefert u. a.

    <name>, <quality id>, <class id>, <subclass id>, <icon>, <level>

Das ist die Ersatzquelle, solange kein Battle.net-API-Zugang konfiguriert
ist. Abfragen sind gedrosselt (wenige parallel, kurze Pause) und das
Ergebnis wird dauerhaft in ``items`` gespeichert – jedes Item wird nur
einmal abgefragt; nicht gefundene Items nach 7 Tagen erneut.
"""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .. import db

URL = "https://www.wowhead.com/forever/{lang}item={id}&xml"  # lang = "de/" oder "" (Englisch)
USER_AGENT = "Mozilla/5.0 (compatible; wow-ah-analytics/1.0)"
_lock = threading.Lock()


def parse_xml(text: str) -> dict | None:
    root = ET.fromstring(text)
    item = root.find("item")
    if item is None:
        return None

    def txt(tag):
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else None

    def attr_id(tag):
        el = item.find(tag)
        try:
            return int(el.get("id")) if el is not None and el.get("id") is not None else None
        except ValueError:
            return None

    level = txt("level")
    return {
        "name": txt("name"),
        "quality": attr_id("quality"),
        "class_id": attr_id("class"),
        "class_name": txt("class"),
        "subclass_id": attr_id("subclass"),
        "subclass_name": txt("subclass"),
        "icon": txt("icon"),
        "item_level": int(level) if level and level.isdigit() else None,
    }


def fetch_one(item_id: int, lang="de", opener=urllib.request.urlopen, timeout=20, retries=3):
    """Gibt (status, felder) zurück: status ∈ ok | missing | error."""
    url = URL.format(lang="" if lang == "en" else f"{lang}/", id=item_id)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            data = parse_xml(body)
            return ("ok", data) if data and data.get("name") else ("missing", None)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return "missing", None
            if exc.code in (403, 429):
                return "blocked", None  # Wowhead drosselt: Lauf abbrechen, später erneut
            if exc.code == 503 and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return "error", None
        except (urllib.error.URLError, TimeoutError, ET.ParseError):
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return "error", None
    return "error", None


def fetch_item_metadata(con, limit=10000, pause=2.0, recheck_days=7, lang="de",
                        log=print, opener=urllib.request.urlopen) -> int:
    """Lädt Metadaten für Items ohne Namen – nacheinander, ca. 1 Anfrage/Sekunde.

    Antwortet Wowhead mit 403/429 (Drosselung), wird der Lauf beendet; offene
    Items bleiben „pending“ und werden beim nächsten Import/Lauf nachgeholt.
    Gibt die Anzahl gefundener Items zurück.
    """
    if not _lock.acquire(blocking=False):
        log("Metadaten-Abruf läuft bereits.")
        return 0
    try:
        cutoff = int(time.time()) - recheck_days * 86400
        ids = [r[0] for r in con.execute(
            """SELECT id FROM items WHERE meta_status='pending'
                 OR (meta_status='missing' AND COALESCE(meta_checked,0) < ?)
               ORDER BY id NOT IN (SELECT item_id FROM item_summary), id LIMIT ?""", (cutoff, limit))]  # AH-Items zuerst
        if not ids:
            return 0
        log(f"Lade Item-Namen und Icons von Wowhead für {len(ids)} Items (ca. 1 alle 2 s) …")
        found, checked, blocked = 0, 0, False
        batch = []

        def flush():
            nonlocal batch
            if not batch:
                return
            with db.transaction(con):
                for bid, st, d in batch:
                    if st == "ok":
                        db.upsert_item(con, bid, "ok", **d)
                    elif st == "missing":
                        db.upsert_item(con, bid, "missing")
                db.bump_data_version(con)
            batch = []

        for iid in ids:
            status, data = fetch_one(iid, lang=lang, opener=opener)
            if status == "blocked":
                blocked = True
                break
            checked += 1
            if status == "ok":
                found += 1
            batch.append((iid, status, data))
            if len(batch) >= 100:
                flush()
                log(f"  {checked}/{len(ids)} geprüft, {found} gefunden")
            time.sleep(pause)
        flush()
        msg = f"Metadaten: {found} von {checked} geprüften Items geladen"
        if blocked:
            msg += f" – Wowhead drosselt gerade, {len(ids) - checked} Items folgen beim nächsten Lauf"
        log(msg)
        with db.transaction(con):
            db.log_import(con, "wowhead", "ok", msg)
        return found
    finally:
        _lock.release()


def fetch_english_names(con, limit=10000, pause=2.0, recheck_days=7, log=print,
                        opener=urllib.request.urlopen) -> int:
    """Lädt die englischen Item-Namen (für zweisprachige Suche), ca. 1 Anfrage/Sekunde."""
    if not _lock_en.acquire(blocking=False):
        log("Abruf englischer Namen läuft bereits.")
        return 0
    try:
        cutoff = int(time.time()) - recheck_days * 86400
        ids = [r[0] for r in con.execute(
            """SELECT id FROM items WHERE name_en IS NULL AND meta_status != 'missing'
                 AND COALESCE(en_checked, 0) < ? ORDER BY id LIMIT ?""", (cutoff, limit))]
        if not ids:
            return 0
        log(f"Lade englische Item-Namen von Wowhead für {len(ids)} Items (ca. 1 alle 2 s) …")
        found, checked, blocked, batch = 0, 0, False, []

        def flush():
            nonlocal batch
            if not batch:
                return
            now = int(time.time())
            with db.transaction(con):
                for bid, name in batch:
                    con.execute("UPDATE items SET name_en=?, en_checked=? WHERE id=?", (name, now, bid))
                db.bump_data_version(con)
            batch = []

        for iid in ids:
            status, data = fetch_one(iid, lang="en", opener=opener)
            if status == "blocked":
                blocked = True
                break
            checked += 1
            if status == "ok":
                found += 1
                batch.append((iid, data["name"]))
            elif status == "missing":
                batch.append((iid, None))
            if len(batch) >= 100:
                flush()
                log(f"  {checked}/{len(ids)} geprüft, {found} englische Namen")
            time.sleep(pause)
        flush()
        msg = f"Englische Namen: {found} von {checked} geprüften Items geladen"
        if blocked:
            msg += f" – Wowhead drosselt gerade, {len(ids) - checked} folgen beim nächsten Lauf"
        log(msg)
        with db.transaction(con):
            db.log_import(con, "wowhead", "ok", msg)
        return found
    finally:
        _lock_en.release()


_lock_en = threading.Lock()
