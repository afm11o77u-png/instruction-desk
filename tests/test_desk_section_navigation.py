"""Regression: mode switches to 顧客・商品情報 / 在庫管理 must be served from instruction-desk."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / 'server.py').read_text(encoding='utf-8')
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
PLACEMENT = (ROOT / 'placement.js').read_text(encoding='utf-8')


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\nfunction ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class DeskSectionNavigationTest(unittest.TestCase):
    def test_index_loads_catalog_scripts(self):
        for src in ('/display-mode.js', '/shipping.js', '/placement.js', '/names.js'):
            self.assertIn(f'src="{src}"', INDEX)

    def test_mode_buttons_present(self):
        aside = INDEX[INDEX.index('<aside>'):INDEX.index('</aside>')]
        self.assertIn('data-catalog="desk"', aside)
        self.assertIn('data-catalog="info"', aside)
        self.assertIn('data-catalog="stock"', aside)
        self.assertIn('顧客・商品情報', aside)
        self.assertIn('在庫管理', aside)
        self.assertIn('id="stocknav"', aside)
        self.assertIn('data-stock-section="list"', aside)
        self.assertIn('data-stock-section="move"', aside)
        self.assertIn('data-stock-section="setup"', aside)
        self.assertIn('入庫・出庫', aside)
        self.assertIn('class="navrow stocknav-top"', aside)
        self.assertIn('class="nav stocknav-move"', aside)
        stocknav = aside[aside.index('id="stocknav"'):]
        self.assertLess(stocknav.index('stocknav-top'), stocknav.index('stocknav-move'))
        self.assertRegex(
            stocknav,
            r'stocknav-top[\s\S]*data-stock-section="list"[\s\S]*data-stock-section="setup"',
        )

    def test_show_catalog_switches_info_and_stock(self):
        show = _fn(PLACEMENT, 'showCatalog')
        self.assertIn("which==='info'", show)
        self.assertIn("which==='stock'", show)
        self.assertIn("want='/inventory/?embed=1'", show)
        self.assertIn("$('#infonav')", show)
        self.assertIn("syncStockNav('list')", show)
        self.assertIn("#modes .mode", PLACEMENT)

    def test_server_serves_catalog_static_assets(self):
        # Commit 0211846 accidentally dropped these from the static map;
        # without them placement.js never loads and mode clicks do nothing.
        for path in (
            '/placement.js',
            '/shipping.js',
            '/names.js',
            '/display-mode.js',
            '/display-mode.css',
        ):
            self.assertIn(f"'{path}'", SERVER)

    def test_server_embeds_inventory(self):
        self.assertIn('def _stock_root()', SERVER)
        self.assertIn('def serve_inventory(self, method):', SERVER)
        self.assertIn("path=='/inventory' or path.startswith('/inventory/')", SERVER)
        self.assertIn("route.startswith('/inventory/')", SERVER)
        nested = "nested = ROOT / 'stock-desk'"
        self.assertIn(nested, SERVER)

    def test_server_keeps_info_apis(self):
        self.assertIn('import placement', SERVER)
        self.assertIn('import shipping', SERVER)
        self.assertIn('import names', SERVER)
        self.assertIn('import display_modes', SERVER)
        self.assertIn("'/api/placements'", SERVER)
        self.assertIn("'/api/shipments'", SERVER)
        self.assertIn("'/api/name-labels'", SERVER)

    def test_migration_user_map_wiring_preserved(self):
        self.assertIn('migration.inspect_zip(upload, DATA)', SERVER)
        self.assertIn("user_map=d.get('userMap')", SERVER)


if __name__ == '__main__':
    unittest.main()
