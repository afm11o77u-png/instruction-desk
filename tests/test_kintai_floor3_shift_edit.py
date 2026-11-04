"""現場３: マーク管理のシフト表は閲覧・編集・保存できる。他の機能はマーク権限が必要。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import kintai

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'kintai-shift.js').read_text(encoding='utf-8')


class _H:
    def __init__(self, user, path):
        self.user = user
        self.path = path
        self.headers = {}


FLOOR3 = {'id': 3, 'name': '現', 'login': 'f3', 'role': 'floor3', 'manage': 0, 'overview': 0, 'mark_kintai': 0}


class Floor3ShiftEditTest(unittest.TestCase):
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

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _get(self, path, q=''):
        return kintai.handle_get(_H(FLOOR3, path + q), self.c, path)

    def test_floor3_can_read_and_save_shift(self):
        code, sheet = self._get('/api/kintai/shift', '?year=2026&month=11')
        self.assertEqual(code, 200)
        payload = {'year': 2026, 'month': 11, 'version': sheet.get('version', 0),
                   'people': sheet.get('people') or [], 'cells': sheet.get('cells') or {}}
        res = kintai.handle_post(_H(FLOOR3, '/api/kintai/shift'), self.c, '/api/kintai/shift', payload)
        self.assertEqual(res[0], 200, res)

    def test_floor3_still_denied_outside_shift(self):
        self.assertEqual(self._get('/api/kintai', '?year=2026')[0], 403)
        self.assertEqual(self._get('/api/kintai/bihin', '?year=2026&month=11')[0], 403)
        self.assertEqual(kintai.handle_post(_H(FLOOR3, '/api/kintai/import'), self.c, '/api/kintai/import', {})[0], 403)
        self.assertEqual(kintai.handle_post(_H(FLOOR3, '/api/kintai/bihin'), self.c, '/api/kintai/bihin', {})[0], 403)

    def test_other_roles_still_denied_shift(self):
        sales = dict(FLOOR3, role='sales')
        self.assertEqual(kintai.handle_post(_H(sales, '/api/kintai/shift'), self.c, '/api/kintai/shift', {})[0], 403)

    def test_js_has_no_view_only_guard(self):
        self.assertNotIn('shiftReadOnly', JS)
        self.assertNotIn('applyReadOnlyChrome', JS)


if __name__ == '__main__':
    unittest.main()
