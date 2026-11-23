"""Statische Item-Seiten: Template, Preisformat, Sitemaps, Vollabdeckung."""
import json
import os
import re
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET

from ahanalytics import itempages
from ahanalytics.export import STATIC_SRC, _stamp_index, ASSETS

ITEM_2770 = {
    "id": 2770, "name": "Kupfererz", "name_en": "Copper Ore", "quality": 1,
    "class_name": "Handwerkswaren", "subclass_name": "Metall & Stein",
    "icon": "inv_ore_copper_01", "item_level": 10, "required_level": 0,
    "slot": None, "binding": None,
    "details": json.dumps({"lines": [{"t": "Ein deutsches Detail"}], "stackable": True, "equippable": False}),
    "details_en": json.dumps({"lines": [{"t": "An English detail"}], "stackable": True, "equippable": False}),
}
PRICES = {
    "normal:Allianz": {"last_min": 110, "last_median": 144, "last_qty": 17528, "last_ts": 1795429320},
    "normal:Horde": {"last_min": 259, "last_median": 260, "last_qty": 12196, "last_ts": 1795429320},
}


def _shell():
    with open(os.path.join(STATIC_SRC, "index.html"), encoding="utf-8") as fh:
        return fh.read()


def _render(lang="en", it=None, prices=None):
    it = it or ITEM_2770
    prices = PRICES if prices is None else prices
    maps = itempages._en_maps(STATIC_SRC)
    tpl = itempages._template(_shell())
    page, _, _ = itempages.render_page(tpl, it, prices, lang, maps)
    return page


class MoneyTextTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(itempages.money_text(59), "59c")
        self.assertEqual(itempages.money_text(1234), "12s 34c")
        self.assertEqual(itempages.money_text(14400), "1g 44s")
        self.assertEqual(itempages.money_text(1234567), "123g")  # >= 100 g: Silber fällt weg (wie moneyText in app.js)
        self.assertEqual(itempages.money_text(123456789), "12.345g")  # >= 100 g: Tausenderpunkt
        self.assertIsNone(itempages.money_text(None))


class ItemPageRenderTest(unittest.TestCase):
    def test_en_page(self):
        html = _render("en")
        self.assertIn('<html lang="en"', html)
        self.assertIn("<title>Copper Ore – Prices by Ruleset | Forever AH-Analytics</title>", html)
        self.assertIn('<link rel="canonical" href="https://forever-analytics.eu/item/2770/">', html)
        self.assertIn('hreflang="en"', html)
        self.assertIn('hreflang="de" href="https://forever-analytics.eu/de/item/2770/"', html)
        self.assertIn('hreflang="x-default" href="https://forever-analytics.eu/item/2770/"', html)
        self.assertIn('content="en_US"', html)
        self.assertIn("Copper Ore", html)
        self.assertIn("Metal &amp; Stone", html)  # Unterklasse übersetzt
        self.assertIn("Trade Goods", html)
        self.assertIn("An English detail", html)  # EN-Tooltip, nicht der deutsche
        self.assertNotIn("Ein deutsches Detail", html)
        self.assertIn("1s 10c", html)  # Min 110 Kupfer
        self.assertIn("Alliance", html)
        self.assertIn("../../#/item/2770", html)  # interaktive Ansicht
        self.assertIn("../../de/item/2770/", html)  # Sprachwechsel
        self.assertIn('src="../../app.js', html)  # Assets mit Tiefenpräfix
        self.assertIn("ah.bootlang", html)

    def test_de_page(self):
        html = _render("de")
        self.assertIn('<html lang="de"', html)
        self.assertIn("<title>Kupfererz – Preise je Regelsatz | Forever AH-Analytics</title>", html)
        self.assertIn('<link rel="canonical" href="https://forever-analytics.eu/de/item/2770/">', html)
        self.assertIn('hreflang="en" href="https://forever-analytics.eu/item/2770/"', html)
        self.assertIn("Kupfererz", html)
        self.assertIn("Metall &amp; Stein", html)
        self.assertIn("Ein deutsches Detail", html)
        self.assertIn("Allianz", html)
        self.assertIn("../../../item/2770/", html)  # Sprachwechsel zur EN-Haupt-URL
        self.assertIn('src="../../../app.js', html)

    def test_no_prices_and_escaping(self):
        it = dict(ITEM_2770, name='Gift<zahn> & "Rächer"', details=None)
        html = _render("de", it=it, prices={})
        self.assertIn("Noch nicht im Auktionshaus gesehen.", html)
        self.assertIn("Gift&lt;zahn&gt; &amp; &quot;Rächer&quot;", html)
        self.assertNotIn("<zahn>", html)  # kein unescaped HTML

    def test_missing_names_fall_back(self):
        it = dict(ITEM_2770, name=None, name_en=None)
        html = _render("en", it=it)
        self.assertIn("Item #2770", html)

    def test_boot_hint_script_present(self):
        html = _render("en")
        self.assertIn(r"\/(de\/)?item\/(\d+)\/", html)


class SitemapTest(unittest.TestCase):
    def test_sitemaps_chunked_valid_and_en_first(self):
        with tempfile.TemporaryDirectory() as d:
            ids = list(range(1, itempages.SITEMAP_CHUNK + 3))  # 2 EN-Chunks + 2 DE-Chunks
            n = itempages.write_sitemaps(d, ids)
            self.assertEqual(n, 6)  # pages + 2 EN + 2 DE + Index
            tree = ET.parse(os.path.join(d, "sitemap.xml"))
            locs = [e.text for e in tree.getroot().iter() if e.tag.endswith("loc")]
            self.assertEqual(locs[0], "https://forever-analytics.eu/sitemap-pages.xml")
            self.assertIn("sitemap-items-en-1.xml", locs[1])  # EN vor DE
            en1 = ET.parse(os.path.join(d, "sitemap-items-en-1.xml"))
            urls = [e.text for e in en1.getroot().iter() if e.tag.endswith("loc")]
            self.assertEqual(len(urls), itempages.SITEMAP_CHUNK)
            self.assertEqual(urls[0], "https://forever-analytics.eu/item/1/")
            de2 = ET.parse(os.path.join(d, "sitemap-items-de-2.xml"))
            urls_de2 = [e.text for e in de2.getroot().iter() if e.tag.endswith("loc")]
            self.assertEqual(len(urls_de2), 2)
            self.assertTrue(urls_de2[0].startswith("https://forever-analytics.eu/de/item/"))


def _mini_db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE houses (id INTEGER PRIMARY KEY, source TEXT, ext_id TEXT, faction TEXT DEFAULT '')")
    con.execute("""CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, name_en TEXT, quality INTEGER,
                   class_name TEXT, subclass_name TEXT, icon TEXT, item_level INTEGER,
                   required_level INTEGER, slot TEXT, details TEXT, details_en TEXT, binding INTEGER)""")
    con.execute("""CREATE TABLE item_summary (house_id INTEGER, item_id INTEGER, last_min INTEGER,
                   last_median INTEGER, last_qty INTEGER, last_ts INTEGER)""")
    con.execute("INSERT INTO houses VALUES (797, 'battlenet', 'eu-111', 'Allianz')")
    con.execute("INSERT INTO houses VALUES (798, 'battlenet', 'eu-114', 'Horde')")
    con.execute("INSERT INTO items VALUES (1, 'Kupfererz', 'Copper Ore', 1, 'Handwerkswaren', 'Metall & Stein', 'inv_x', 10, 0, NULL, NULL, NULL, NULL)")
    con.execute("INSERT INTO items VALUES (2, 'Teufelseisenerz', 'Fel Iron Ore', 1, 'Handwerkswaren', 'Metall & Stein', 'inv_y', 60, 0, NULL, NULL, NULL, NULL)")
    con.execute("INSERT INTO item_summary VALUES (797, 1, 110, 144, 100, 1795429320)")
    con.execute("INSERT INTO item_summary VALUES (798, 1, 259, 260, 50, 1795429320)")
    con.execute("CREATE TABLE snapshots (house_id INTEGER, ts INTEGER)")
    con.executemany("INSERT INTO snapshots VALUES (?,1795429320)", [(797,),(798,)])
    con.commit()
    return con


class ExportItemPagesTest(unittest.TestCase):
    def test_full_export_writes_all_items_both_languages(self):
        with tempfile.TemporaryDirectory() as d:
            con = _mini_db(os.path.join(d, "t.sqlite"))
            # Shell wie im echten Export: statische Dateien + Versionsstempel
            for name in os.listdir(STATIC_SRC):
                src = os.path.join(STATIC_SRC, name)
                if os.path.isfile(src):
                    with open(src, "rb") as fsrc, open(os.path.join(d, name), "wb") as fdst:
                        fdst.write(fsrc.read())
            _stamp_index(d, "test123")
            stats = itempages.export_item_pages(con, d, STATIC_SRC, log=lambda *_: None)
            self.assertEqual(stats["items"], 2)
            self.assertEqual(stats["item_pages"], 4)
            self.assertEqual(stats["with_prices"], 1)
            for sub, iid in (("", 1), ("", 2), ("de/", 1), ("de/", 2)):
                p = os.path.join(d, sub, "item", str(iid), "index.html")
                self.assertTrue(os.path.exists(p), p)
            en1 = open(os.path.join(d, "item", "1", "index.html"), encoding="utf-8").read()
            self.assertIn("app.js?v=test123", en1)  # Versionsstempel greift
            self.assertIn("1s 10c", en1)
            self.assertIn("Copper Ore", en1)
            de2 = open(os.path.join(d, "de", "item", "2", "index.html"), encoding="utf-8").read()
            self.assertIn("Teufelseisenerz", de2)
            self.assertIn("Noch nicht im Auktionshaus gesehen.", de2)
            # Sitemap: alle Items in beiden Sprachen
            tree = ET.parse(os.path.join(d, "sitemap-items-en-1.xml"))
            urls = [e.text for e in tree.getroot().iter() if e.tag.endswith("loc")]
            self.assertEqual(urls, ["https://forever-analytics.eu/item/1/",
                                    "https://forever-analytics.eu/item/2/"])
            con.close()


class ParseDetailsLocaleTest(unittest.TestCase):
    """parse_details (Battle.net): EN- und DE-Tooltips aus derselben all_locales-Antwort."""

    PAYLOAD = {
        "name": {"de_DE": "Kupfererz", "en_US": "Copper Ore"},
        "is_stackable": True, "is_equippable": False, "max_count": 1,
        "preview_item": {
            "binding": {"name": {"de_DE": "Wird beim Aufheben gebunden", "en_US": "Binds when picked up"}},
            "description": {"de_DE": "Glänzt.", "en_US": "Shiny."},
        },
    }

    def test_locales(self):
        from ahanalytics.sources.battlenet import parse_details
        de = parse_details(self.PAYLOAD, "de_DE")
        en = parse_details(self.PAYLOAD, "en_US")
        self.assertIn("Wird beim Aufheben gebunden", de)
        self.assertIn('"t":"Einzigartig"', de)
        self.assertIn("Binds when picked up", en)
        self.assertIn('"t":"Unique"', en)
        self.assertNotIn("Wird beim", en)
        data = json.loads(en)
        self.assertTrue(data["stackable"])
        self.assertFalse(data["equippable"])


if __name__ == "__main__":
    unittest.main()
