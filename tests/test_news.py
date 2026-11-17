"""News-Modul: Feed-Parsing, Speicherung und Export-Payload."""
import sqlite3
import time
import unittest

from ahanalytics import news

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Wowhead News</title>
<item><title>100+ Gold Per Hour Farm Nerfed in WoW: Forever</title>
<link>https://www.wowhead.com/forever/news/a</link>
<pubDate>Tue, 17 Nov 2026 12:00:00 +0000</pubDate></item>
<item><title>Scheduled Maintenance</title>
<link>https://www.wowhead.com/forever/news/b</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Hotfixes: November 16, 2026</title>
<link rel="alternate" href="https://www.bluetracker.gg/wow/topic/1"/>
<published>2026-11-16T20:00:00Z</published></entry>
</feed>"""


class ParseFeedTest(unittest.TestCase):
    def test_rss_items_and_dates(self):
        items = news.parse_feed(RSS, "Wowhead Forever", "news", "en")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "100+ Gold Per Hour Farm Nerfed in WoW: Forever")
        self.assertEqual(items[0]["kind"], "news")
        self.assertIsInstance(items[0]["published_ts"], int)
        self.assertIsNone(items[1]["published_ts"])

    def test_atom_items(self):
        items = news.parse_feed(ATOM, "Blue Tracker", "blue", "en")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "blue")
        self.assertIsInstance(items[0]["published_ts"], int)


class PayloadTest(unittest.TestCase):
    def test_store_and_payload(self):
        con = sqlite3.connect(":memory:")
        news._create_table(con)
        news._store(con, news.parse_feed(RSS, "Wowhead Forever", "news", "en"))
        news._store(con, news.parse_feed(ATOM, "Blue Tracker", "blue", "en"))
        p = news.payload(con)
        self.assertEqual(len(p["guides"]), len(news.GUIDES))
        self.assertTrue(all(g["kind"] == "guide" and g["url"].startswith("https://") for g in p["guides"]))
        self.assertEqual(len(p["items"]), 3)
        # jüngster Eintrag zuerst; ohne Datum zählt der Abrufzeitpunkt
        self.assertEqual(p["items"][0]["title"], "Scheduled Maintenance")
        self.assertEqual(p["items"][1]["title"], "100+ Gold Per Hour Farm Nerfed in WoW: Forever")
        self.assertEqual(p["items"][-1]["title"], "Hotfixes: November 16, 2026")
        # Duplikate aktualisieren statt zu doppeln
        news._store(con, news.parse_feed(ATOM, "Blue Tracker", "blue", "en"))
        self.assertEqual(len(news.payload(con)["items"]), 3)
        con.close()

    def test_payload_ohne_tabelle_ist_exporttauglich(self):
        con = sqlite3.connect(":memory:")
        p = news.payload(con)  # legt die Tabelle selbst an
        self.assertEqual(p["items"], [])
        self.assertTrue(p["guides"])
        self.assertIsInstance(p["generated"], int)
        self.assertLessEqual(p["generated"], int(time.time()))
        con.close()


if __name__ == "__main__":
    unittest.main()
