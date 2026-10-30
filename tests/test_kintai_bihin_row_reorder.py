"""備品棚卸: 行の並べ替え（↑↓・ドラッグ）と保存順の永続化。"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import kintai
import kintai_bihin

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'kintai-bihin.js').read_text(encoding='utf-8')


def _extract(name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\s*\{{', JS)
    if not m:
        raise AssertionError(f'missing function {name}')
    i = JS.find('{', m.start())
    depth = 0
    for j in range(i, len(JS)):
        if JS[j] == '{':
            depth += 1
        elif JS[j] == '}':
            depth -= 1
            if depth == 0:
                return JS[m.start():j + 1]
    raise AssertionError(name)


class RowReorderUiContractTest(unittest.TestCase):
    def test_controls_and_unsaved(self):
        self.assertIn('data-move-dir="-1"', JS)
        self.assertIn('data-move-dir="1"', JS)
        self.assertIn('draggable="true"', JS)
        body = _extract('moveRowByKey')
        self.assertIn('markDirty()', body)
        self.assertIn('captureQtyInputs', body)
        self.assertIn('restoreQtyInputs', body)
        self.assertNotIn('api(', body)  # 並べ替えだけでは保存しない

    def test_move_array_item_node(self):
        script = _extract('moveArrayItem') + r'''
const a = ['A', 'B', 'C', 'D'];
console.log(JSON.stringify({
  up: moveArrayItem(a, 2, 1), down: moveArrayItem(a, 0, 1), dragFirstToLast: moveArrayItem(a, 0, 3),
  dragLastToFirst: moveArrayItem(a, 3, 0), outOfRange: moveArrayItem(a, 0, -1), same: moveArrayItem(a, 1, 1), orig: a,
}));
'''
        r = subprocess.run(['node', '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        self.assertEqual(o['up'], ['A', 'C', 'B', 'D'])
        self.assertEqual(o['down'], ['B', 'A', 'C', 'D'])
        self.assertEqual(o['dragFirstToLast'], ['B', 'C', 'D', 'A'])
        self.assertEqual(o['dragLastToFirst'], ['D', 'A', 'B', 'C'])
        self.assertEqual(o['outOfRange'], ['A', 'B', 'C', 'D'])
        self.assertEqual(o['same'], ['A', 'B', 'C', 'D'])
        self.assertEqual(o['orig'], ['A', 'B', 'C', 'D'])


class RowReorderStoreTest(unittest.TestCase):
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
        for n, p in (('A', 1), ('B', 2), ('C', 3)):
            kintai_bihin.save_item(self.c, self.user, {'category': '', 'name': n, 'unit': '個', 'unitPrice': p})

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_saved_array_order_persists_per_month(self):
        sep = kintai_bihin.get_sheet(self.c, 2026, 9)
        kintai_bihin.save_sheet(self.c, self.user, {'year': 2026, 'month': 9, 'version': 0, 'rows': sep['rows']})
        sep_before = tuple(self.c.execute('SELECT * FROM kintai_bihin_sheets WHERE month=9').fetchone())
        items_before = [tuple(r) for r in self.c.execute('SELECT * FROM kintai_bihin_items ORDER BY id')]
        oct_ = kintai_bihin.get_sheet(self.c, 2026, 10)
        rows = list(reversed(oct_['rows']))
        rows[0] = dict(rows[0], closingQty=4)
        # クライアントの古い sortOrder が残っていても配列順が優先される
        code, saved = kintai_bihin.save_sheet(self.c, self.user, {'year': 2026, 'month': 10, 'version': 0, 'rows': rows})
        self.assertEqual(code, 200)
        again = kintai_bihin.get_sheet(self.c, 2026, 10)
        self.assertEqual([r['name'] for r in again['rows']], ['C', 'B', 'A'])
        self.assertEqual(again['rows'][0]['closingQty'], 4)
        self.assertEqual([r['name'] for r in kintai_bihin.get_sheet(self.c, 2026, 9)['rows']], ['A', 'B', 'C'])
        self.assertEqual(tuple(self.c.execute('SELECT * FROM kintai_bihin_sheets WHERE month=9').fetchone()), sep_before)
        self.assertEqual([tuple(r) for r in self.c.execute('SELECT * FROM kintai_bihin_items ORDER BY id')], items_before)
        # 未作成月は従来どおりマスタ順
        self.assertEqual([r['name'] for r in kintai_bihin.get_sheet(self.c, 2026, 11)['rows']], ['A', 'B', 'C'])


if __name__ == '__main__':
    unittest.main()
