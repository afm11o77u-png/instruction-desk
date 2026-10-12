"""Source/DOM-markup assertions for 初期在庫 取扱 search + inline create."""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
PICKERS_JS = (ROOT / 'static' / 'opening_pickers.js').read_text(encoding='utf-8')
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


class OpeningPickersUiTest(unittest.TestCase):
    def test_index_loads_opening_pickers_script(self):
        self.assertIn('src="opening_pickers.js"', INDEX)
        self.assertLess(INDEX.index('opening_pickers.js'), INDEX.index('app.js'))

    def test_opening_uses_handle_search_not_customer_then_product(self):
        body = _fn(APP_JS, 'mountOpeningRegisterForm')
        self.assertIn("searchPickerMarkup({label:'取扱'", body)
        self.assertIn("inputId:'handleQuery'", body)
        self.assertIn('filterOpeningHandles', body)
        self.assertIn('取扱を新しく登録', body)
        self.assertIn('openOpeningHandleCreate', body)
        self.assertIn('該当する取扱がありません', body)
        self.assertNotIn("searchPickerMarkup({label:'顧客'", body)
        self.assertNotIn("searchPickerMarkup({label:'商品と加工仕様'", body)
        self.assertNotIn('<select id="customerId"', body)
        self.assertNotIn('<select id="handleId"', body)
        self.assertIn('bindOpeningSearchPicker', body)
        self.assertIn('ArrowDown', _fn(APP_JS, 'bindOpeningSearchPicker'))
        self.assertIn("e.key==='Enter'", _fn(APP_JS, 'bindOpeningSearchPicker'))

    def test_handle_create_form_fields_and_no_stock_side_effect_copy(self):
        body = _fn(APP_JS, 'mountOpeningHandleCreateForm')
        self.assertIn("searchPickerMarkup({label:'顧客'", body)
        self.assertIn("searchPickerMarkup({label:'商品'", body)
        self.assertIn('handleEmbroidery', body)
        self.assertIn('品番・商品名・色・仕入先で検索', body)
        self.assertIn('/api/customer-products', body)
        self.assertIn('在庫はまだ増やしていません', _fn(APP_JS, 'mountOpeningHandleCreateForm'))
        self.assertIn('cancelOpeningHandleCreate', APP_JS)
        self.assertIn('selectOpeningHandle', body)

    def test_confirm_requires_confirmed_handle_id(self):
        body = _fn(APP_JS, 'confirmOpening')
        self.assertIn('resolveConfirmedOpeningHandle', body)
        self.assertIn('入力した文字だけでは保存できません', body)
        self.assertIn('customerProductId:selected.id', body)
        self.assertNotIn('resolveConfirmedOpeningCustomer', body)
        self.assertNotIn('customers[0]', body)

    def test_post_save_stays_on_opening_without_keeping_handle(self):
        body = _fn(APP_JS, 'confirmOpening')
        self.assertIn('openingStateAfterSuccessfulSave(state.opening)', body)
        self.assertIn("applySectionState('setup','opening')", body)
        self.assertIn('openOpeningRegister()', body)
        self.assertIn('loadOpeningEntries()', body)
        self.assertNotIn("state.tab='list'", body)
        keep = _fn(PICKERS_JS, 'openingStateAfterSuccessfulSave')
        self.assertNotIn('customerId', keep)
        self.assertNotIn('handleId', keep)

    def test_dropdown_capped_scrollable(self):
        self.assertRegex(CSS, r'\.search-picker-list\{[^}]*max-height:\s*220px')
        self.assertRegex(CSS, r'\.search-picker-list\{[^}]*overflow-y:\s*auto')
        self.assertIn('.opening-handle-tools', CSS)

    def test_helpers_search_handles_and_idempotent_api(self):
        self.assertIn('filterOpeningHandles', PICKERS_JS)
        self.assertIn('handlePickerLabel', PICKERS_JS)
        self.assertIn('selectOpeningHandle', PICKERS_JS)
        self.assertIn('reconcileOpeningLinesForProduct', PICKERS_JS)
        self.assertIn('def ensure_customer_product', STOCK_PY)
        self.assertIn("UNIQUE(customer_id, product_id, embroidery)", STOCK_PY)
        self.assertIn("'/api/customer-products'", SERVER_PY)
        self.assertIn('stock.ensure_customer_product', SERVER_PY)
        self.assertIn('自動では再開しません', STOCK_PY)
        self.assertIn('名前だけでは自動作成しません', STOCK_PY)

    def test_node_unit_suite(self):
        script = ROOT / 'tests' / 'test_opening_pickers.js'
        proc = subprocess.run(['node', str(script)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('All opening picker unit tests passed', proc.stdout)


if __name__ == '__main__':
    unittest.main()
