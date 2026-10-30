"""備品棚卸: 品目マスタから表示中の月の棚卸表へ追加する機能。"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import kintai
import kintai_bihin

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'kintai-bihin.js').read_text(encoding='utf-8')
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')


def _extract(name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\s*\{{', JS)
    if not m:
        raise AssertionError(f'missing function {name}')
    i = JS.find('{', m.start())
    depth = 0
    j = i
    while j < len(JS):
        if JS[j] == '{':
            depth += 1
        elif JS[j] == '}':
            depth -= 1
            if depth == 0:
                return JS[m.start():j + 1]
        j += 1
    raise AssertionError(name)


class AddFromMasterUiContractTest(unittest.TestCase):
    def test_button_target_and_added_label(self):
        self.assertIn('この月の棚卸に追加', JS)
        self.assertIn('data-add-item', JS)
        self.assertIn('追加済み', JS)
        self.assertIn('id="bihinmastertarget"', INDEX)
        # 追加先は画面の year/month（todayYm ではない）
        body = _extract('addMasterItemToSheet')
        self.assertNotIn('todayYm', body)
        self.assertIn('markDirty()', body)
        self.assertNotIn("api(", body)  # 追加だけでは保存しない
        self.assertIn('captureQtyInputs', body)
        self.assertIn('active === false', body)

    def test_pure_helpers_in_node(self):
        src = '\n'.join(_extract(n) for n in ('sheetHasItemId', 'blankQty', 'buildRowFromMasterItem'))
        script = src + r'''
const out = {};
const rows = [{itemId: 1, name: 'OSKラベル', unitPrice: 38000}];
out.dupById = sheetHasItemId(rows, 1);
out.sameNameOtherId = sheetHasItemId(rows, 2);
out.emptySheet = sheetHasItemId([], 1);
const osk2 = {id: 2, category: '消耗', name: 'OSKラベル', unit: '巻', unitPrice: 30000};
const fresh = buildRowFromMasterItem({id: 3, category: '資材', name: 'ユニフォーム宅配袋', unit: '枚', unitPrice: 32}, null);
out.fresh = fresh;
out.retained = buildRowFromMasterItem({id: 3, category: '資材', name: 'ユニフォーム宅配袋', unit: '枚', unitPrice: 32},
  {itemId: 3, rowKey: 'k3', openingQty: 100, purchaseQty: null, closingQty: 0});
out.otherRetainedIgnored = buildRowFromMasterItem(osk2, {itemId: 9, openingQty: 5});
console.log(JSON.stringify(out));
'''
        r = subprocess.run(['node', '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out['dupById'])
        self.assertFalse(out['sameNameOtherId'])
        self.assertFalse(out['emptySheet'])
        f = out['fresh']
        self.assertEqual((f['itemId'], f['category'], f['name'], f['unit'], f['unitPrice']),
                         (3, '資材', 'ユニフォーム宅配袋', '枚', 32))
        self.assertIsNone(f['openingQty'])
        self.assertIsNone(f['purchaseQty'])
        self.assertIsNone(f['closingQty'])
        rt = out['retained']
        self.assertEqual((rt['rowKey'], rt['openingQty'], rt['purchaseQty'], rt['closingQty']), ('k3', 100, None, 0))
        self.assertIsNone(out['otherRetainedIgnored']['openingQty'])


class AddFromMasterStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = sqlite3.connect(Path(self.tmp.name) / 't.sqlite3')
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, "
            "version INTEGER, mark_kintai INTEGER DEFAULT 0)"
        )
        kintai.bind(Path(self.tmp.name))
        kintai.schema(self.c)
        self.user = {'id': 1, 'name': '主', 'login': 'o@ex.com', 'role': 'owner', 'mark_kintai': 1}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _item(self, name, price, unit='個'):
        return kintai_bihin.save_item(self.c, self.user, {'category': '消耗', 'name': name, 'unit': unit, 'unitPrice': price})

    def test_added_row_with_blank_qty_round_trips_and_other_month_untouched(self):
        osk = self._item('OSKラベル', 38000, '巻')
        bag = self._item('ユニフォーム宅配袋', 32, '枚')
        # 9月: 両方保存
        sep = kintai_bihin.get_sheet(self.c, 2026, 9)
        sep_rows = [dict(r, openingQty=1, purchaseQty=0, closingQty=1) for r in sep['rows']]
        self.assertEqual(kintai_bihin.save_sheet(self.c, self.user, {'year': 2026, 'month': 9, 'version': 0, 'rows': sep_rows})[0], 200)
        sep_before = self.c.execute('SELECT rows_json, version FROM kintai_bihin_sheets WHERE year=2026 AND month=9').fetchone()
        # 10月: 0行で保存（全削除した状態）
        self.assertEqual(kintai_bihin.save_sheet(self.c, self.user, {'year': 2026, 'month': 10, 'version': 0, 'rows': []})[0], 200)
        self.assertEqual(kintai_bihin.get_sheet(self.c, 2026, 10)['rows'], [])
        # 画面で追加した行（数量空欄）を保存
        added = [
            {'rowKey': 'a', 'itemId': osk['id'], 'category': '消耗', 'name': 'OSKラベル', 'unit': '巻', 'unitPrice': 38000,
             'openingQty': None, 'purchaseQty': None, 'closingQty': None},
            {'rowKey': 'b', 'itemId': bag['id'], 'category': '消耗', 'name': 'ユニフォーム宅配袋', 'unit': '枚', 'unitPrice': 32,
             'openingQty': None, 'purchaseQty': None, 'closingQty': 5},
        ]
        code, saved = kintai_bihin.save_sheet(self.c, self.user, {'year': 2026, 'month': 10, 'version': 1, 'rows': added})
        self.assertEqual(code, 200)
        again = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual([r['itemId'] for r in again['rows']], [osk['id'], bag['id']])
        self.assertIsNone(again['rows'][0]['openingQty'])  # 空は0にならない
        self.assertIsNone(again['rows'][0]['closingQty'])
        self.assertEqual(again['rows'][1]['closingQty'], 5)
        self.assertEqual(again['rows'][0]['unitPrice'], 38000)
        sep_after = self.c.execute('SELECT rows_json, version FROM kintai_bihin_sheets WHERE year=2026 AND month=9').fetchone()
        self.assertEqual(tuple(sep_before), tuple(sep_after))


if __name__ == '__main__':
    unittest.main()
