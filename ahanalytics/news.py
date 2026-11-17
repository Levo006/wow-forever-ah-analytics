"""News-Feeds (WoW-Forever-News, Blue Posts) abrufen und mit kuratierten Gold-Guides exportieren.

Die Quellen sind öffentliche RSS-/Atom-Feeds. Der Abruf ist fehlertolerant:
schlägt ein Feed fehl, bleibt der bisherige Bestand in der Datenbank erhalten.
``python run.py news`` füllt die Tabelle ``news_items``; der Website-Export
schreibt daraus ``data/news.json`` fürs statische Frontend. Kuratierte
Gold-Guides stehen fest in ``GUIDES`` und erscheinen auch ohne Feed-Abruf.
"""
from __future__ import annotations

import calendar
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

# (Quelle, Feed-URL, Art, Sprache) – Art "news" (Redaktion) oder "blue" (Blue Posts).
FEEDS = (
    ("Wowhead Forever", "https://www.wowhead.com/news/rss/forever", "news", "en"),
    ("Blue Tracker", "https://www.bluetracker.gg/rss/wow/", "blue", "en"),
)

# Kuratierte, redaktionell gepflegte Gold-Guides (Qualität geprüft, Stand 17.11.2026):
# etablierte Guide-Redaktionen, aktuell gepflegt; Boosting-Shops bewusst ausgeschlossen.
GUIDES = (
    {"url": "https://www.icy-veins.com/wow-classic/goldmaking-guide-the-best-ways-to-make-gold",
     "title": "WoW Classic Goldmaking Guide: The Best Ways to Make Gold",
     "source": "Icy Veins", "kind": "guide", "lang": "en"},
    {"url": "https://www.wowhead.com/classic/guide/gold-making-auction-house",
     "title": "Gold Making Guide for WoW Classic",
     "source": "Wowhead", "kind": "guide", "lang": "en"},
    {"url": "https://www.wowhead.com/classic/guide/sm-scarlet-monastery-gold-farming-classic-wow",
     "title": "Gold Farming Scarlet Monastery in Classic WoW",
     "source": "Wowhead", "kind": "guide", "lang": "en"},
    {"url": "https://www.wowhead.com/classic/guide/classic-mining-best-farming-basics-early-ores",
     "title": "Best Mining and Ore Farming Routes in WoW Classic",
     "source": "Wowhead", "kind": "guide", "lang": "en"},
    {"url": "https://www.warcrafttavern.com/wow-classic/guides/sinyous-comprehensive-classic-wow-gold-guide/",
     "title": "Sinyou's Comprehensive Classic WoW Gold Guide",
     "source": "Warcraft Tavern", "kind": "guide", "lang": "en"},
)

MAX_ITEMS = 300      # Ringpuffer in der Datenbank
FEED_TIMEOUT = 20    # Sekunden je Feed


def _create_table(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS news_items (
        url TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        source TEXT NOT NULL,
        kind TEXT NOT NULL,
        lang TEXT NOT NULL DEFAULT 'en',
        published_ts INTEGER,
        fetched_ts INTEGER NOT NULL
    )""")


def _parse_date(value) -> int | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:  # RFC 822 (RSS 2.0)
        return int(parsedate_to_datetime(value).timestamp())
    except (TypeError, ValueError):
        pass
    try:  # ISO 8601 (Atom), Zeitzone ggf. abgeschnitten (UTC angenommen)
        return int(calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return None


def parse_feed(xml_bytes: bytes, source: str, kind: str, lang: str) -> list[dict]:
    """RSS-2.0-/Atom-Feed -> [{url, title, source, kind, lang, published_ts}]."""
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter("item"):  # RSS 2.0
        text = lambda tag: (it.findtext(tag) or "").strip()  # noqa: E731
        url, title = text("link"), text("title")
        if not url or not title:
            continue
        items.append({"url": url, "title": title, "source": source, "kind": kind,
                      "lang": lang, "published_ts": _parse_date(text("pubDate"))})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for it in root.findall("a:entry", ns):  # Atom
        title = (it.findtext("a:title", "", ns) or "").strip()
        link = ""
        for l in it.findall("a:link", ns):
            if l.get("rel") in (None, "alternate") and l.get("href"):
                link = l.get("href")
                break
        if not link or not title:
            continue
        items.append({"url": link, "title": title, "source": source, "kind": kind,
                      "lang": lang,
                      "published_ts": _parse_date(it.findtext("a:published", "", ns)
                                                  or it.findtext("a:updated", "", ns))})
    return items


def _store(con: sqlite3.Connection, items: list[dict]) -> int:
    now = int(time.time())
    n = 0
    for it in items:
        con.execute("""INSERT INTO news_items (url, title, source, kind, lang, published_ts, fetched_ts)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(url) DO UPDATE SET title=excluded.title,
                           published_ts=COALESCE(excluded.published_ts, news_items.published_ts),
                           fetched_ts=excluded.fetched_ts""",
                    (it["url"], it["title"], it["source"], it["kind"], it["lang"],
                     it["published_ts"], now))
        n += 1
    con.execute("""DELETE FROM news_items WHERE url NOT IN (
                       SELECT url FROM news_items
                       ORDER BY COALESCE(published_ts, fetched_ts) DESC LIMIT ?)""",
                (MAX_ITEMS,))
    return n


def fetch_and_store(con: sqlite3.Connection, log=print) -> dict:
    """Alle Feeds abrufen und speichern. Fehler einzelner Feeds stoppen den Lauf nicht."""
    from . import db
    summary = {}
    for source, url, kind, lang in FEEDS:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; ForeverAHAnalytics/1.0; +https://levo006.github.io/wow-forever-ah-analytics/)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
            with urllib.request.urlopen(req, timeout=FEED_TIMEOUT) as resp:
                items = parse_feed(resp.read(), source, kind, lang)
            with db.transaction(con):
                _create_table(con)
                n = _store(con, items)
            summary[source] = n
            log(f"News: {source}: {n} Einträge")
        except Exception as exc:  # noqa: BLE001 – alter Bestand bleibt gültig
            summary[source] = f"Fehler: {exc}"
            log(f"News: {source}: Abruf fehlgeschlagen ({exc}) – bisheriger Bestand bleibt")
    return summary


def payload(con: sqlite3.Connection, limit: int = 8) -> dict:
    """``data/news.json``-Inhalt: kuratierte Guides plus jüngste Feed-Einträge."""
    _create_table(con)
    rows = con.execute("""SELECT url, title, source, kind, lang, published_ts
                          FROM news_items
                          ORDER BY COALESCE(published_ts, fetched_ts) DESC LIMIT ?""",
                       (limit,)).fetchall()
    return {"generated": int(time.time()),
            "guides": [dict(g) for g in GUIDES],
            "items": [{"url": r[0], "title": r[1], "source": r[2], "kind": r[3],
                       "lang": r[4], "published_ts": r[5]} for r in rows]}
