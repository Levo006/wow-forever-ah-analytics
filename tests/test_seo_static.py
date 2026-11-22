"""SEO-Grundlagen: robots.txt, sitemap.xml und Meta-Tags sind vorhanden.

Der statische Export kopiert jeden Eintrag aus ``static/`` unveraendert in
den Seiten-Ordner (siehe ``export.export_site``); darum genuegt hier der
Nachweis, dass die Dateien im Frontend-Ordner korrekt vorliegen.
"""
import os
import unittest
import xml.etree.ElementTree as ET

from ahanalytics.export import STATIC_SRC

SITE_URL = "https://forever-analytics.eu/"


class SeoStaticTest(unittest.TestCase):
    def test_robots_txt_allows_crawling_and_names_sitemap(self):
        with open(os.path.join(STATIC_SRC, "robots.txt"), encoding="utf-8") as fh:
            robots = fh.read()
        self.assertIn("Allow: /", robots)
        self.assertIn(f"Sitemap: {SITE_URL}sitemap.xml", robots)

    def test_sitemap_is_valid_and_canonical(self):
        tree = ET.parse(os.path.join(STATIC_SRC, "sitemap.xml"))
        locs = [e.text for e in tree.getroot().iter() if e.tag.endswith("loc")]
        self.assertEqual(locs, [SITE_URL])

    def test_index_has_search_and_social_meta(self):
        with open(os.path.join(STATIC_SRC, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('<meta name="description"', html)
        self.assertIn(f'<link rel="canonical" href="{SITE_URL}">', html)
        self.assertIn('<meta property="og:title"', html)
        self.assertIn('<meta property="og:description"', html)
        self.assertIn(f'<meta property="og:url" content="{SITE_URL}">', html)
        self.assertIn(f'content="{SITE_URL}assets/og-image.jpg"', html)
        # Das OG-Bild muss wirklich im Repo liegen, sonst zeigt die Vorschau ins Leere.
        self.assertTrue(os.path.exists(os.path.join(STATIC_SRC, "assets", "og-image.jpg")))


if __name__ == "__main__":
    unittest.main()
