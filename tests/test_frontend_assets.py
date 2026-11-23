"""Frontend additions are copied and included in cache-version invalidation."""
import tempfile
import unittest
from pathlib import Path
from ahanalytics.export import ASSETS, frontend_version, _stamp_index

class FrontendAssetTest(unittest.TestCase):
    def test_tooltip_and_theme_invalidate_version(self):
        for name in ('design-exchange.css','item-tooltip.js'):
            self.assertIn(name, ASSETS)
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/name;p.write_text('v1')
                before=frontend_version(d);p.write_text('v2')
                self.assertNotEqual(before, frontend_version(d))
    def test_new_assets_stamped(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'index.html'
            p.write_text('<head><link href="design-exchange.css"><script src="item-tooltip.js"></script></head>')
            _stamp_index(d,'abc123')
            self.assertIn('design-exchange.css?v=abc123',p.read_text())
            self.assertIn('item-tooltip.js?v=abc123',p.read_text())

class DirectItemNavigationTest(unittest.TestCase):
    def test_inline_tooltips_do_not_intercept_item_links(self):
        root=Path(__file__).resolve().parents[1]/'static'
        script=(root/'item-tooltip.js').read_text()
        self.assertNotIn('preventDefault',script)
        self.assertNotIn('showModal',script)
        self.assertNotIn('item-inspector',(root/'design-exchange.css').read_text())
        self.assertIn('data-original-tooltip',script)
        self.assertIn('sanitize',script)
