"""Source assertions: 商品登録・編集の「この商品を扱う顧客」は検索付き選択。"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\nfunction ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class ProductLinkCustomerPickerUiTest(unittest.TestCase):
    def test_product_editor_uses_search_picker_not_select(self):
        body = _fn(APP_JS, 'bindProductEditor')
        self.assertIn('searchPickerMarkup', body)
        self.assertIn("label:'顧客'", body)
        self.assertIn('bindOpeningSearchPicker', body)
        self.assertIn('filterOpeningCustomers', body)
        self.assertIn('該当する顧客がありません', body)
        self.assertIn('顧客名またはカナで検索', body)
        self.assertNotIn('<select data-i=', body)
        self.assertNotIn('data-k="customerId"', body)

    def test_each_link_row_has_independent_picker_ids(self):
        body = _fn(APP_JS, 'bindProductEditor')
        self.assertIn('linkCustomerQ${i}', body)
        self.assertIn('linkCustomerList${i}', body)
        self.assertIn('linkCustomerClear${i}', body)
        self.assertIn('linkCustomerHint${i}', body)

    def test_typed_text_does_not_become_customer_id_on_save(self):
        read = _fn(APP_JS, 'readForm')
        self.assertIn('resolveProductLinkCustomer', read)
        self.assertIn('入力した文字だけでは保存できません', read)
        self.assertIn('候補から選んでください', read)
        # payload only maps confirmed numeric customerId — never customerQuery
        self.assertNotIn('customerQuery', read)
        editor = _fn(APP_JS, 'bindProductEditor')
        self.assertIn('l.customerId=null', editor)
        self.assertIn('l.customerId=Number(item.id)', editor)

    def test_edit_keeps_existing_customer_display(self):
        body = _fn(APP_JS, 'bindProductEditor')
        self.assertIn('resolveProductLinkCustomer', body)
        self.assertIn("cust?cust.name:(l.customerQuery||'')", body)
        self.assertIn('value:display', body)
        self.assertIn('isConfirmed:()=>!!resolveProductLinkCustomer', body)

    def test_reuses_opening_picker_keyboard_and_empty_copy(self):
        binder = _fn(APP_JS, 'bindOpeningSearchPicker')
        self.assertIn('ArrowDown', binder)
        self.assertIn('ArrowUp', binder)
        self.assertIn("e.key==='Enter'", binder)
        self.assertIn('dialogbody', binder)
        self.assertIn('scrollTop', binder)
        product = _fn(APP_JS, 'bindProductEditor')
        self.assertEqual(product.count("emptyText:'該当する顧客がありません'"), 1)

    def test_linkrow_dropdown_not_clipped_css(self):
        self.assertIn('.linkrow:focus-within{z-index:8}', CSS)
        self.assertIn('.linkrow .search-picker-list{z-index:9}', CSS)
        self.assertRegex(CSS, r'\.links\{[^}]*overflow:\s*visible')
        self.assertRegex(CSS, r'\.linkrow\{[^}]*overflow:\s*visible')
        self.assertRegex(CSS, r'\.search-picker-list\{[^}]*max-height:\s*220px')

    def test_opening_picker_unit_suite_still_passes(self):
        script = ROOT / 'tests' / 'test_opening_pickers.js'
        proc = subprocess.run(['node', str(script)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == '__main__':
    unittest.main()
