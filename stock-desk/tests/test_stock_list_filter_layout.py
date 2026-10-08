"""Source/DOM-markup assertions for 在庫一覧 filter row + 表示 outside frame."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')


def _fn(name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\{{', APP_JS)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\nfunction ', APP_JS[m.end():])
    end = m.end() + nxt.start() if nxt else len(APP_JS)
    return APP_JS[start:end]


class StockListFilterLayoutTest(unittest.TestCase):
    def test_stock_list_filters_are_five_selects_in_dedicated_bar(self):
        body = _fn('stockList')
        self.assertIn('class="panel stock-list-panel"', body)
        self.assertIn('class="stock-list-chrome"', body)
        self.assertIn('id="stocklistdisplay"', body)
        self.assertIn('class="bar stock-list-filters"', body)
        for fid, label in (
            ('fCustomer', '顧客'),
            ('fProduct', '商品'),
            ('fSize', 'サイズ'),
            ('fSpec', '加工仕様'),
            ('fLocation', '保管場所'),
        ):
            self.assertIn(f'id="{fid}"', body)
            self.assertIn(label, body)
        # 表示 must not be hard-coded inside the filter bar markup
        filters = body[body.index('stock-list-filters'):body.index('id="groups"')]
        self.assertNotIn('表示', filters)
        self.assertNotIn('display-mode', filters)

    def test_list_display_mounts_outside_filter_frame(self):
        body = _fn('mountStockDisplay')
        self.assertIn("tab==='list'", body)
        self.assertIn("#stocklistdisplay", body)
        self.assertIn("#main .bar", body)
        # list path must prefer the outside slot before the shared .bar
        list_branch = body[body.index("tab==='list'"):]
        self.assertLess(
            list_branch.index('#stocklistdisplay'),
            list_branch.index('#main .bar'),
        )

    def test_filter_change_behavior_unchanged(self):
        body = _fn('stockList')
        self.assertIn(
            "['fCustomer','fProduct','fSize','fSpec','fLocation'].forEach(id=>$('#'+id).onchange=apply)",
            body,
        )
        self.assertIn('f.customerId=$(\'#fCustomer\').value', body)
        self.assertIn('await loadStock()', body)

    def test_stock_list_filter_css_one_row_and_display_outside(self):
        self.assertRegex(CSS, r'\.stock-list-chrome\{[^}]*justify-content:\s*flex-end')
        self.assertRegex(CSS, r'\.stock-list-filters\{[^}]*flex-wrap:\s*nowrap')
        self.assertRegex(CSS, r'\.stock-list-filters>label\{[^}]*flex:\s*1 1 0')
        self.assertRegex(CSS, r'\.stock-list-filters select\{[^}]*min-width:\s*0')
        self.assertRegex(
            CSS,
            r'\.stock-list-display \.display-mode-bar(?:-inline)?,\s*\n\.stock-list-display \.display-mode-bar-inline\{[^}]*width:\s*auto',
        )
        self.assertIn(
            '.stock-list-display .display-mode-bar-inline .display-mode-control{width:auto}',
            CSS,
        )
        # narrow: wrap filters only; keep 表示 auto-width outside
        self.assertIn('@media(max-width:900px)', CSS)
        self.assertRegex(CSS, r'@media\(max-width:900px\)\{[^}]*\.stock-list-filters\{[^}]*flex-wrap:\s*wrap')
        media640 = CSS[CSS.index('@media(max-width:640px)'):]
        self.assertIn('.stock-list-display .display-mode-bar-inline{width:auto', media640)
        self.assertIn(
            '.stock-list-display .display-mode-bar-inline .display-mode-control{width:auto}',
            media640,
        )
        self.assertIn('.stock-list-filters>label{flex:1 1 calc(50% - 8px)', media640)


if __name__ == '__main__':
    unittest.main()
