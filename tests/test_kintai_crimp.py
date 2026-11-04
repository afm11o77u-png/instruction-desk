"""圧着カウンター: 保存・差分・月合計・権限・移行の冪等性。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import kintai
import kintai_crimp

ROOT = Path(__file__).resolve().parents[1]


class _H:
    def __init__(self, user, path):
        self.user = user
        self.path = path


def U(role, **kw):
    u = {'id': 9, 'name': role + '太郎', 'login': role, 'role': role, 'manage': 0, 'overview': 0, 'mark_kintai': 0}
    u.update(kw)
    return u


OWNER = U('owner')
FLOOR3 = U('floor3')


class CrimpTest(unittest.TestCase):
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

    def post(self, user, d, path='/api/kintai/crimp'):
        return kintai.handle_post(_H(user, path), self.c, path, d)

    def get(self, user, month):
        p = '/api/kintai/crimp'
        return kintai.handle_get(_H(user, p + '?month=' + month), self.c, p)

    def test_schema_idempotent(self):
        kintai.schema(self.c)
        kintai_crimp.schema(self.c)
        self.assertTrue(self.c.execute("SELECT 1 FROM sqlite_master WHERE name='kintai_crimp_readings'").fetchone())

    def test_diff_total_and_month_boundary(self):
        for day, n in (('2026-10-31', 1000), ('2026-11-02', 1300), ('2026-11-05', 1700)):
            self.assertEqual(self.post(FLOOR3, {'day': day, 'reading': n})[0], 200)
        code, res = self.get(FLOOR3, '2026-11')
        self.assertEqual(code, 200)
        self.assertEqual([r['diff'] for r in res['rows']], [300, 400])
        self.assertEqual(res['total'], 700)
        self.assertEqual(res['rows'][0]['userName'], 'floor3太郎')
        self.assertFalse(res['canAdmin'])
        self.assertIsNone(self.get(OWNER, '2026-10')[1]['rows'][0]['diff'])

    def test_same_day_reentry_updates(self):
        self.post(FLOOR3, {'day': '2026-11-02', 'reading': 10})
        self.post(OWNER, {'day': '2026-11-02', 'reading': 12})
        rows = self.get(OWNER, '2026-11')[1]['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['reading'], rows[0]['userName']), (12, 'owner太郎'))

    def test_admin_only_edit_delete(self):
        self.post(FLOOR3, {'day': '2026-11-02', 'reading': 10})
        self.assertEqual(self.post(FLOOR3, {'day': '2026-11-02', 'reading': 9, 'edit': True})[0], 403)
        self.assertEqual(self.post(FLOOR3, {'day': '2026-11-02'}, '/api/kintai/crimp/delete')[0], 403)
        self.assertEqual(self.post(U('sales', manage=1), {'day': '2026-11-02', 'reading': 9, 'edit': True})[0], 200)
        self.assertEqual(self.post(OWNER, {'day': '2026-11-02'}, '/api/kintai/crimp/delete')[0], 200)
        self.assertEqual(self.get(OWNER, '2026-11')[1]['rows'], [])

    def test_permissions_and_validation(self):
        self.assertEqual(self.post(U('sales'), {'day': '2026-11-02', 'reading': 1})[0], 403)
        self.assertEqual(self.post(U('sales', mark_kintai=1), {'day': '2026-11-02', 'reading': 1})[0], 200)
        self.assertEqual(self.get(U('floor'), '2026-11')[0], 403)
        for bad in ({'day': '2026-13-01', 'reading': 1}, {'day': '2026-11-02', 'reading': -1}, {'day': '2026-11-02', 'reading': 'x'}):
            with self.assertRaises(ValueError):
                self.post(OWNER, bad)

    def test_ui_wired(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('id="shiftgrid"'), html.index('id="crimpcounter"'))
        self.assertLess(html.index('id="crimpcounter"'), html.index('id="kintaibihin"'))
        self.assertIn('/kintai-crimp.js', (ROOT / 'server.py').read_text(encoding='utf-8'))
        js = (ROOT / 'kintai-crimp.js').read_text(encoding='utf-8')
        self.assertIn('confirm(', js)


if __name__ == '__main__':
    unittest.main()
