"""Source/DOM-markup assertions for 入庫・出庫 list display modes."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
PO_JS = (ROOT / 'static' / 'purchase_orders.js').read_text(encoding='utf-8')


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\s*\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\n\s*function ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class MoveDisplayModesUiTest(unittest.TestCase):
    def test_title_slot_covers_list_period_orders(self):
        mount = _fn(APP_JS, 'mountStockDisplay')
        sync = _fn(APP_JS, 'syncStockListDisplaySlot')
        for section in ('list', 'period', 'orders', 'setup'):
            self.assertIn(f"section==='{section}'", sync)
        self.assertNotIn("section==='move'", sync)
        for tab in ('list', 'period', 'orders'):
            self.assertIn(f"tab==='{tab}'", mount)
        self.assertNotIn("tab==='move'", mount)
        self.assertIn('SETUP_TAB_IDS.has(tab)', mount)
        self.assertLess(
            mount.index("#stocklistdisplay"),
            mount.index("#main .bar"),
        )

    def test_move_page_redirects_without_own_display(self):
        body = _fn(APP_JS, 'movePage')
        self.assertIn('stockList()', body)
        self.assertNotIn("mountStockDisplay(stage,'move'", body)
        list_page = _fn(APP_JS, 'stockList')
        self.assertIn("mountStockDisplay(stage,'list'", list_page)
        self.assertIn('stock-list-filters', list_page)

    def test_orders_page_mounts_display_on_title_slot(self):
        self.assertIn("mountStockDisplay(stage,'orders'", PO_JS)
        self.assertIn("grid.hidden=displayMode!=='grid'", PO_JS)
        self.assertIn('class="dm-card"', PO_JS)


if __name__ == '__main__':
    unittest.main()
