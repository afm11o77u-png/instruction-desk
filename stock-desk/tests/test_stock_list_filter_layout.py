"""Source/DOM-markup assertions for 在庫一覧 filter row + 表示 on title-row right end."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')


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
        self.assertNotIn('stock-list-chrome', body)
        self.assertNotIn('id="stocklistdisplay"', body)
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

    def test_display_slot_lives_on_title_row_right_end(self):
        self.assertIn('class="stockhead-end"', INDEX)
        self.assertIn('id="stocklistdisplay"', INDEX)
        self.assertIn('id="stocktitle"', INDEX)
        # Slot is in the header end cluster (after switch), not in main markup
        head = INDEX[INDEX.index('class="stockhead"'):INDEX.index('<main')]
        self.assertIn('id="stocklistdisplay"', head)
        self.assertLess(head.index('class="switch"'), head.index('id="stocklistdisplay"'))
        self.assertIn('hidden', head[head.index('id="stocklistdisplay"') - 80:head.index('id="stocklistdisplay"') + 40])

    def test_list_display_mounts_outside_filter_frame(self):
        body = _fn('mountStockDisplay')
        self.assertIn("tab==='list'", body)
        self.assertIn("tab==='move'", body)
        self.assertIn('SETUP_TAB_IDS.has(tab)', body)
        self.assertIn("#stocklistdisplay", body)
        self.assertIn("#main .bar", body)
        # list / move / period / orders / 登録・設定 prefer the title-row slot
        slot_branch = body[body.index('useTitleSlot'):]
        self.assertLess(
            slot_branch.index('#stocklistdisplay'),
            slot_branch.index('#main .bar'),
        )

    def test_paint_toggles_title_row_display_slot(self):
        body = _fn('paint')
        self.assertIn('syncStockListDisplaySlot(state.section)', body)
        sync = _fn('syncStockListDisplaySlot')
        self.assertIn("section==='list'", sync)
        self.assertIn("section==='setup'", sync)
        self.assertIn('slot.hidden=false', sync)
        self.assertIn('slot.hidden=true', sync)
        self.assertIn("slot.innerHTML=''", sync)

    def test_filter_change_behavior_unchanged(self):
        body = _fn('stockList')
        self.assertIn(
            "['fCustomer','fProduct','fSize','fSpec','fLocation','fCondition'].forEach(id=>$('#'+id).onchange=apply)",
            body,
        )
        self.assertIn('f.customerId=$(\'#fCustomer\').value', body)
        self.assertIn('f.condition=$(\'#fCondition\').value', body)
        self.assertIn('await loadStock()', body)

    def test_stock_list_filter_css_one_row_and_display_on_title_row(self):
        self.assertIn('.stockhead-end{', CSS)
        self.assertRegex(CSS, r'\.stockhead-end\{[^}]*margin-left:\s*auto')
        self.assertRegex(CSS, r'\.stockhead \.stock-list-display\{[^}]*justify-content:\s*flex-end')
        self.assertNotIn('.stock-list-chrome', CSS)
        self.assertRegex(CSS, r'\.stock-list-filters\{[^}]*flex-wrap:\s*nowrap')
        self.assertRegex(CSS, r'\.stock-list-filters>label\{[^}]*flex:\s*1 1 0')
        self.assertRegex(CSS, r'\.stock-list-filters select\{[^}]*min-width:\s*0')
        self.assertIn(
            '.stockhead .stock-list-display .display-mode-bar-inline .display-mode-control{width:auto}',
            CSS,
        )
        # narrow: wrap filters only; keep title-row 表示 auto-width
        self.assertIn('@media(max-width:900px)', CSS)
        self.assertRegex(CSS, r'@media\(max-width:900px\)\{[^}]*\.stock-list-filters\{[^}]*flex-wrap:\s*wrap')
        media640 = CSS[CSS.index('@media(max-width:640px)'):]
        self.assertIn('.stockhead .stock-list-display .display-mode-bar-inline{width:auto', media640)
        self.assertIn(
            '.stockhead .stock-list-display .display-mode-bar-inline .display-mode-control{width:auto}',
            media640,
        )
        self.assertIn('.stock-list-filters>label{flex:1 1 calc(50% - 8px)', media640)


if __name__ == '__main__':
    unittest.main()
