"""Source/DOM-markup assertions for 初期在庫 searchable customer/product pickers."""
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

    def test_opening_uses_search_pickers_not_selects(self):
        body = _fn(APP_JS, 'mountOpeningRegisterForm')
        self.assertIn("searchPickerMarkup({label:'顧客'", body)
        self.assertIn("searchPickerMarkup({label:'商品と加工仕様'", body)
        self.assertIn("inputId:'customerQuery'", body)
        self.assertIn("inputId:'handleQuery'", body)
        self.assertNotIn('<select id="customerId"', body)
        self.assertNotIn('<select id="handleId"', body)
        self.assertIn('該当する顧客がありません', body)
        self.assertIn('bindOpeningSearchPicker', body)
        self.assertIn('ArrowDown', _fn(APP_JS, 'bindOpeningSearchPicker'))
        self.assertIn("e.key==='Enter'", _fn(APP_JS, 'bindOpeningSearchPicker'))

    def test_customer_change_clears_product_fields(self):
        body = _fn(APP_JS, 'mountOpeningRegisterForm')
        self.assertIn('clearOpeningProductFields(state.opening)', body)
        self.assertIn('clearOpeningCustomerSelection(state.opening)', body)

    def test_confirm_requires_confirmed_ids_not_typed_text(self):
        body = _fn(APP_JS, 'confirmOpening')
        self.assertIn('resolveConfirmedOpeningCustomer', body)
        self.assertIn('resolveConfirmedOpeningOffering', body)
        self.assertIn('入力した文字だけでは保存できません', body)
        self.assertNotIn('customers[0]', body)

    def test_post_save_keeps_customer_and_stays_on_opening(self):
        body = _fn(APP_JS, 'confirmOpening')
        self.assertIn('openingStateAfterSuccessfulSave(state.opening)', body)
        self.assertIn("applySectionState('setup','opening')", body)
        self.assertIn('openOpeningRegister()', body)
        self.assertIn('loadOpeningEntries()', body)
        self.assertNotIn("state.tab='list'", body)

    def test_dropdown_capped_scrollable(self):
        self.assertRegex(CSS, r'\.search-picker-list\{[^}]*max-height:\s*220px')
        self.assertRegex(CSS, r'\.search-picker-list\{[^}]*overflow-y:\s*auto')

    def test_helpers_only_active_and_scoped(self):
        self.assertIn('c&&c.active', PICKERS_JS)
        self.assertIn('filterOpeningCustomers', PICKERS_JS)
        self.assertIn('filterOpeningOfferings', PICKERS_JS)
        self.assertIn('openingStateAfterSuccessfulSave', PICKERS_JS)

    def test_node_unit_suite(self):
        script = ROOT / 'tests' / 'test_opening_pickers.js'
        proc = subprocess.run(['node', str(script)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('All opening picker unit tests passed', proc.stdout)


if __name__ == '__main__':
    unittest.main()
