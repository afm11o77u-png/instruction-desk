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
        self.assertEqual(cols, ['No.', 'カテゴリ', '商品', '取扱', 'サイズ', '開く'])

    def test_table_body_cells_match_header_order(self):
        body = _fn(APP_JS, 'products')
        # Class markers keep header/body alignment for width CSS.
        for cls in ('prod-id', 'prod-cat', 'prod-name', 'prod-handles', 'prod-sizes', 'prod-act'):
            self.assertIn(f'class="{cls}"', body)
        # 取扱 (links) cell must appear before サイズ (sizes) in each row template.
        self.assertLess(body.index('prod-handles'), body.index('prod-sizes'))
        # カテゴリは商品名のすぐ左
        self.assertLess(body.index('class="prod-cat"'), body.index('class="prod-name"'))
        self.assertLess(body.index('r.links.map'), body.index('r.sizes.map(esc).join'))
        self.assertIn('class="products-list"', body)
        self.assertIn('function productListTitle(r)', APP_JS)
        self.assertIn("if(r.code&&code)return `${code} / ${name}`", APP_JS)
        self.assertIn('${esc(productListTitle(r))}', body)
        self.assertNotIn('${codeShow} ${esc(r.name)}', body)

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
        # 商品ID: 仕入先 .sid 見た目＋見出し/4桁の最小（3.75rem）。5.25/5.5 は広すぎ、1% は重なり
        start = STYLE_CSS.index('/* 商品一覧:')
        end = STYLE_CSS.index('.check,.inactive-toggle', start)
        products_css = STYLE_CSS[start:end]
        self.assertNotIn('width:5.5rem', products_css)
        self.assertNotIn('width:5.25rem', products_css)
        self.assertNotIn('width:1%', products_css)
        self.assertIn('width:3.75rem', products_css)
        self.assertIn('min-width:3.75rem', products_css)
        self.assertIn('padding-left:6px!important', products_css)
        self.assertIn('#8aa094', products_css)  # same muted ID color as table.directory .sid
        self.assertIn('.prod-id', products_css)
        self.assertIn('white-space:nowrap', products_css)
        self.assertIn('overflow:hidden', products_css)
        self.assertIn('.prod-handles{width:30%}', products_css)
        self.assertIn('.prod-cat{width:10%', products_css)
        self.assertIn('.prod-act{width:6.75rem', products_css)
        self.assertIn('.prod-sizes{width:18%', products_css)
        # directory reference still documents the 2em sid slot for in-cell IDs
        self.assertIn('table.directory .sid{display:inline-block;width:2em', STYLE_CSS)
        dm = (ROOT / 'static' / 'display-mode.css').read_text(encoding='utf-8')
        self.assertIn('table.products-list td.prod-id{font-size:11px}', dm)

    def test_list_title_separates_saved_code_only(self):
        import subprocess
        fn = _fn(APP_JS, 'productCodeShow') + '\n' + _fn(APP_JS, 'productListTitle')
        script = fn + r'''
const cases = [
  [{code:'3M9910', codeDisplay:'3M9910', name:'反射テープ 10mm', color:'', category:'制服'}, '3M9910 / 反射テープ 10mm'],
  [{code:'CD633', codeDisplay:'CD633', name:'パンツ', color:'白', category:'制服'}, 'CD633 / パンツ 白'],
  [{code:'', codeDisplay:'', name:'生地', color:'', category:'部品・資材'}, '生地'],
  [{code:'', codeDisplay:'', name:'裾上げ', color:'', category:'加工サービス'}, '裾上げ'],
  [{code:'', codeDisplay:'品番なし（要確認）', name:'白衣', color:'', category:'制服'}, '品番なし（要確認） 白衣'],
  [{code:'', name:'白衣', color:'白', category:''}, '品番なし 白衣 白'],
]
for (const [row, want] of cases) {
  const got = productListTitle(row)
  if (got !== want) {
    console.error(JSON.stringify({got, want}))
    process.exit(1)
  }
  if (!row.code && got.includes(' / ')) {
    console.error('slash without code '+got)
    process.exit(1)
  }
}
'''
        proc = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        body = _fn(APP_JS, 'products')
        self.assertIn('${r.code}${r.codeDisplay', body)
        self.assertNotIn('productListTitle', body[body.index('const text='):body.index('return text.includes')])


if __name__ == '__main__':
    unittest.main()
