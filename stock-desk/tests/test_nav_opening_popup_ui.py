"""Source/DOM-markup assertions for stock-desk list/setup nav + 初期在庫 popup list."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
STOCK_PY = (ROOT / 'stock.py').read_text(encoding='utf-8')
SERVER_PY = (ROOT / 'server.py').read_text(encoding='utf-8')
DESK_INDEX = (REPO / 'index.html').read_text(encoding='utf-8')
PLACEMENT_JS = (REPO / 'placement.js').read_text(encoding='utf-8')


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\nfunction ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class StockListSetupNavUiTest(unittest.TestCase):
    def test_parent_stocknav_has_list_and_setup(self):
        self.assertIn('id="stocknav"', DESK_INDEX)
        aside = DESK_INDEX[DESK_INDEX.index('<aside>'):DESK_INDEX.index('</aside>')]
        self.assertIn('data-stock-section="list"', aside)
        self.assertIn('data-stock-section="setup"', aside)
        self.assertIn('在庫一覧', aside)
        self.assertIn('登録・設定', aside)
        self.assertIn('data-catalog="desk"', aside)
        self.assertIn('data-catalog="info"', aside)
        self.assertIn('data-catalog="stock"', aside)

    def test_parent_show_catalog_wires_stocknav(self):
        show = _fn(PLACEMENT_JS, 'showCatalog')
        self.assertIn("$('#stocknav')", show)
        self.assertIn("syncStockNav('list')", show)
        self.assertIn('tellStockFrame', PLACEMENT_JS)
        self.assertIn("type:'stock-section'", PLACEMENT_JS)
        self.assertIn("e.data.type==='stock-section-changed'", PLACEMENT_JS)

    def test_tabs_sit_in_header_with_brand(self):
        self.assertIn('class="stockhead"', INDEX)
        self.assertIn('class="stockhead-brand"', INDEX)
        self.assertIn('id="tabs"', INDEX)
        self.assertIn('id="stocktitle"', INDEX)
        header = INDEX[INDEX.index('<header'):INDEX.index('</header>')]
        self.assertIn('id="tabs"', header)
        self.assertNotIn('<nav class="tabs"', INDEX[INDEX.index('</header>'):])

    def test_setup_renders_five_register_tabs_not_list(self):
        self.assertIn(
            "SETUP_TABS=[['customers','顧客'],['suppliers','仕入先'],['products','商品'],['locations','保管場所'],['opening','初期在庫']]",
            APP_JS,
        )
        self.assertNotIn("['list','在庫一覧']", APP_JS[APP_JS.index('SETUP_TABS='):APP_JS.index('SETUP_TAB_IDS=')])
        paint = _fn(APP_JS, 'paint')
        self.assertIn('SETUP_TABS.map', paint)
        self.assertIn("state.section==='setup'", paint)
        self.assertIn('tabs.hidden=true', paint)
        for label in ('顧客', '仕入先', '商品', '保管場所', '初期在庫'):
            self.assertIn(label, APP_JS[APP_JS.index('SETUP_TABS='):APP_JS.index('SETUP_TAB_IDS=')])

    def test_default_entry_is_stock_list(self):
        self.assertIn("section:'list'", APP_JS)
        self.assertIn("tab:'list'", APP_JS)
        boot = _fn(APP_JS, 'boot')
        self.assertIn("applySectionState(sectionParam==='setup'?'setup':'list'", boot)
        show = _fn(PLACEMENT_JS, 'showCatalog')
        self.assertIn("syncStockNav('list')", show)

    def test_setup_remembers_last_register_tab(self):
        self.assertIn("SETUP_TAB_KEY='stock-desk:setup-tab'", APP_JS)
        self.assertIn('function loadSetupTab(', APP_JS)
        self.assertIn('function saveSetupTab(', APP_JS)
        apply = _fn(APP_JS, 'applySectionState')
        self.assertIn('saveSetupTab(next)', apply)
        self.assertIn("state.setupTab=next", apply)

    def test_heading_shows_current_setup_target(self):
        paint = _fn(APP_JS, 'paint')
        self.assertIn("SETUP_TAB_LABEL[state.tab]", paint)
        self.assertIn("'在庫一覧'", paint)
        self.assertIn("id=\"stocktitle\"", INDEX)

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
        for col in ('顧客', '商品', '加工仕様', 'サイズ', '数量', '仕入単価', '保管場所'):
            self.assertIn(col, body)
        self.assertNotIn('<th>基準日</th>', body)

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
        self.assertIn('touchEditDirty()', APP_JS)
        self.assertIn("window.confirm('入力中の内容を破棄しますか？')", APP_JS)
        leave = _fn(APP_JS, 'ensureLeaveOpeningForm')
        self.assertIn('openingDialogIsOpen()', leave)
        self.assertIn('state.openingDirty', leave)
        self.assertIn('closeOpeningDialog()', leave)
        edit_leave = _fn(APP_JS, 'ensureLeaveEditDialog')
        self.assertIn('editDialogIsOpen()', edit_leave)
        self.assertIn('state.editDirty', edit_leave)
        nav = _fn(APP_JS, 'ensureCanNavigate')
        self.assertIn('ensureLeaveOpeningForm()', nav)
        self.assertIn('ensureLeaveEditDialog()', nav)

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

    def test_stock_pages_have_no_backup_create_footer(self):
        footer = INDEX[INDEX.index('<footer>'):INDEX.index('</footer>')]
        self.assertNotIn('id="backup"', footer)
        self.assertNotIn('バックアップを作る', footer)
        self.assertNotIn('バックアップを作る', INDEX)
        self.assertNotIn("#backup", APP_JS)
        self.assertNotIn("/api/backup", APP_JS)
        self.assertNotIn("'/api/backup'", SERVER_PY)
        self.assertNotIn('def backup(', SERVER_PY)
        self.assertIn('id="foot"', footer)


if __name__ == '__main__':
    unittest.main()
