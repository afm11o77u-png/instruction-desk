"""Source/DOM-markup assertions for stock-desk header nav + 初期在庫 popup list."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
STOCK_PY = (ROOT / 'stock.py').read_text(encoding='utf-8')
SERVER_PY = (ROOT / 'server.py').read_text(encoding='utf-8')


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\nfunction ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class StockNavLayoutUiTest(unittest.TestCase):
    def test_tabs_sit_in_header_with_brand(self):
        self.assertIn('class="stockhead"', INDEX)
        self.assertIn('class="stockhead-brand"', INDEX)
        self.assertIn('id="tabs"', INDEX)
        # tabs are inside header, not a sibling below
        header = INDEX[INDEX.index('<header'):INDEX.index('</header>')]
        self.assertIn('id="tabs"', header)
        self.assertNotIn('<nav class="tabs"', INDEX[INDEX.index('</header>'):])

    def test_paint_always_renders_six_tabs(self):
        self.assertIn("STOCK_TABS=[['customers','顧客'],['suppliers','仕入先'],['products','商品'],['locations','保管場所'],['opening','初期在庫'],['list','在庫一覧']]", APP_JS)
        paint = _fn(APP_JS, 'paint')
        self.assertIn('STOCK_TABS.map', paint)
        self.assertNotIn("state.mode==='masters'", paint)
        self.assertNotIn('embedded\n', paint)
        for label in ('顧客', '仕入先', '商品', '保管場所', '初期在庫', '在庫一覧'):
            self.assertIn(label, APP_JS[APP_JS.index('STOCK_TABS='):APP_JS.index('MASTER_TABS=')])

    def test_header_tabs_flex_wrap_no_overflow_css(self):
        self.assertRegex(CSS, r'header,\s*\.stockhead\{[^}]*flex-wrap:\s*wrap')
        self.assertRegex(CSS, r'header,\s*\.stockhead\{[^}]*min-width:\s*0')
        self.assertRegex(CSS, r'\.tabs\{[^}]*flex-wrap:\s*wrap')
        self.assertRegex(CSS, r'\.tabs\{[^}]*min-width:\s*0')
        self.assertRegex(CSS, r'\.tabs\{[^}]*max-width:\s*100%')
        self.assertRegex(CSS, r'\.tabs button\.active\{[^}]*box-shadow:\s*inset 0 -3px 0')


class OpeningPopupUiTest(unittest.TestCase):
    def test_opening_page_is_list_with_register_button(self):
        body = _fn(APP_JS, 'opening')
        self.assertIn('初期在庫を登録', body)
        self.assertIn('id="openOpeningRegister"', body)
        self.assertIn('state.openings', body)
        self.assertIn('openOpeningRegister()', body)
        self.assertNotIn('searchPickerMarkup', body)
        self.assertNotIn('id="confirmOpen"', body)
        for col in ('基準日', '顧客', '商品', '加工仕様', 'サイズ', '数量', '仕入単価', '保管場所'):
            self.assertIn(col, body)

    def test_register_form_lives_in_large_opening_dialog(self):
        self.assertIn('id="openingDialog"', INDEX)
        self.assertIn('class="opening-dialog"', INDEX)
        form = _fn(APP_JS, 'mountOpeningRegisterForm')
        self.assertIn('openingDialogEl()', _fn(APP_JS, 'openOpeningRegister'))
        self.assertIn('showModal()', _fn(APP_JS, 'openOpeningRegister'))
        self.assertIn('id="confirmOpen"', form)
        self.assertIn("searchPickerMarkup({label:'顧客'", form)
        self.assertRegex(CSS, r'dialog\.opening-dialog\{[^}]*width:\s*min\(1100px,\s*96vw\)')
        self.assertRegex(CSS, r'dialog\[open\]\{[^}]*display:\s*flex')
        self.assertRegex(CSS, r'\.dialogbody\{[^}]*overflow:\s*auto')

    def test_dirty_close_confirms_discard(self):
        self.assertIn('touchOpeningDirty()', APP_JS)
        self.assertIn("window.confirm('入力中の内容を破棄しますか？')", APP_JS)
        leave = _fn(APP_JS, 'ensureLeaveOpeningForm')
        self.assertIn('openingDialogIsOpen()', leave)
        self.assertIn('state.openingDirty', leave)
        self.assertIn('closeOpeningDialog()', leave)

    def test_post_save_refreshes_list_keeps_customer_reopens_form(self):
        body = _fn(APP_JS, 'confirmOpening')
        self.assertIn('openingStateAfterSuccessfulSave(state.opening)', body)
        self.assertIn('loadOpeningEntries()', body)
        self.assertIn('openOpeningRegister()', body)
        self.assertIn("state.openingDirty=false", body)

    def test_opening_entries_api_reads_existing_lots(self):
        self.assertIn('def opening_entries(c):', STOCK_PY)
        self.assertIn("m.kind='opening'", STOCK_PY)
        self.assertIn('qty_received', STOCK_PY[STOCK_PY.index('def opening_entries'):])
        self.assertIn("'/api/opening/entries'", SERVER_PY)
        self.assertIn('stock.opening_entries(c)', SERVER_PY)
        # must not invent a parallel store
        self.assertNotIn('CREATE TABLE IF NOT EXISTS opening_entries', STOCK_PY)


if __name__ == '__main__':
    unittest.main()
