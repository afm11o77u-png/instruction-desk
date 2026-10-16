"""Source assertions for 商品一覧 column order (取扱 before サイズ)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
STYLE_CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\s*\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\n\s*function ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class ProductsListColumnsUiTest(unittest.TestCase):
    def test_table_header_order_handling_before_size(self):
        body = _fn(APP_JS, 'products')
        header = re.search(
            r'<thead><tr>(.*?)</tr></thead>',
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(header)
        cols = re.findall(r'<th>([^<]*)</th>', header.group(1))
        self.assertEqual(cols, ['商品ID', '商品', '取扱', 'サイズ', '開く'])

    def test_table_body_cells_match_header_order(self):
        body = _fn(APP_JS, 'products')
        # Class markers keep header/body alignment for width CSS.
        for cls in ('prod-id', 'prod-name', 'prod-handles', 'prod-sizes', 'prod-act'):
            self.assertIn(f'class="{cls}"', body)
        # 取扱 (links) cell must appear before サイズ (sizes) in each row template.
        self.assertLess(body.index('prod-handles'), body.index('prod-sizes'))
        self.assertLess(body.index('r.links.map'), body.index('r.sizes.map(esc).join'))
        self.assertIn('class="products-list"', body)

    def test_grid_card_order_handling_before_size(self):
        body = _fn(APP_JS, 'products')
        grid = body[body.index('list-grid'):]
        self.assertLess(
            grid.index('r.links.map'),
            grid.index('r.sizes.map'),
        )
        self.assertIn('取扱未設定', grid)
        self.assertIn('サイズ未設定', grid)

    def test_products_list_width_css(self):
        self.assertIn('table.products-list', STYLE_CSS)
        self.assertIn('.prod-handles', STYLE_CSS)
        self.assertIn('.prod-sizes', STYLE_CSS)
        self.assertIn('.prod-act', STYLE_CSS)
        self.assertIn('table-layout:fixed', STYLE_CSS)


if __name__ == '__main__':
    unittest.main()
