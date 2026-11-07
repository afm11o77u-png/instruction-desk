"""圧着カウンター: 保存・差分・月合計・権限・移行の冪等性。"""
from __future__ import annotations

import json
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
        self.c.executemany("INSERT INTO users(id,login,name,role,active) VALUES(?,?,?,?,?)", [
            (1, 'a', '現場花子', 'floor', 1), (2, 'b', '主任次郎', 'owner', 1),
            (3, 'c', '退職三郎', 'floor', 0), (4, 'd', '営業四郎', 'sales', 1)])
        kintai.bind(Path(self.tmp.name))
        kintai.schema(self.c)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def post(self, user, d, path='/api/kintai/crimp', worker=1):
        if path == '/api/kintai/crimp' and worker is not None:
            d = {'workerId': worker, **d}
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
        self.assertFalse(res['canAdmin'])
        self.assertNotIn('userName', res['rows'][0])
        self.assertNotIn('loginName', res['rows'][0])
        stored = self.c.execute(
            "SELECT user_name, worker_name FROM kintai_crimp_readings WHERE day='2026-11-02'"
        ).fetchone()
        self.assertEqual((stored['worker_name'], stored['user_name']), ('現場花子', 'floor3太郎'))
        self.assertIsNone(self.get(OWNER, '2026-10')[1]['rows'][0]['diff'])
        self.assertEqual(self.get(OWNER, '2026-10')[1]['rows'][0]['userName'], '現場花子')

    def test_same_day_reentry_updates(self):
        self.post(FLOOR3, {'day': '2026-11-02', 'reading': 10})
        self.post(OWNER, {'day': '2026-11-02', 'reading': 12}, worker=2)
        rows = self.get(OWNER, '2026-11')[1]['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['reading'], rows[0]['userName']), (12, '主任次郎'))

    def test_inputter_name_only_for_owner_or_manage_flag(self):
        """入力者名は主管理者か管理者フラグ（manage）だけ。一般のAPIにも出さない。"""
        self.assertEqual(self.post(FLOOR3, {'day': '2026-10-31', 'reading': 1000})[0], 200)
        code, saved = self.post(FLOOR3, {'day': '2026-11-02', 'reading': 1010})
        self.assertEqual(code, 200)
        self.assertNotIn('現場花子', json.dumps(saved, ensure_ascii=False))
        self.assertNotIn('floor3太郎', json.dumps(saved, ensure_ascii=False))
        stored = self.c.execute(
            'SELECT user_id, user_name, worker_id, worker_name, reading FROM kintai_crimp_readings WHERE day=?',
            ('2026-11-02',),
        ).fetchone()
        self.assertEqual(stored['reading'], 1010)
        self.assertEqual(stored['worker_id'], 1)
        self.assertEqual(stored['worker_name'], '現場花子')
        self.assertEqual(stored['user_id'], FLOOR3['id'])
        self.assertEqual(stored['user_name'], 'floor3太郎')

        def blob(user):
            status, res = self.get(user, '2026-11')
            return status, res, json.dumps(res, ensure_ascii=False)

        def assert_hidden(user):
            status, res, text = blob(user)
            self.assertEqual(status, 200, text)
            row = res['rows'][0]
            self.assertEqual(row['reading'], 1010)
            self.assertEqual(row['diff'], 10)
            self.assertEqual(res['total'], 10)
            self.assertFalse(res['canAdmin'])
            for key in ('userName', 'loginName', 'workerName', 'userId', 'workerId', 'user_name', 'worker_name'):
                self.assertNotIn(key, row)
                self.assertNotIn(key, res)
            self.assertNotIn('現場花子', text)
            self.assertNotIn('floor3太郎', text)
            self.assertNotIn('主任次郎', text)

        def assert_visible(user):
            status, res, text = blob(user)
            self.assertEqual(status, 200, text)
            row = res['rows'][0]
            self.assertEqual(row['reading'], 1010)
            self.assertEqual(row['diff'], 10)
            self.assertTrue(res['canAdmin'])
            self.assertEqual(row['userName'], '現場花子')
            self.assertEqual(row['loginName'], 'floor3太郎')
            self.assertNotIn('workerId', row)
            self.assertNotIn('userId', row)

        # 主管理者（manage が無くても可）。sqlite の行でも同じ。
        assert_visible(OWNER)
        assert_visible(U('owner', manage=0, overview=0, mark_kintai=0))
        owner_row = self.c.execute("SELECT * FROM users WHERE role='owner'").fetchone()
        assert_visible(owner_row)

        # 管理者フラグは manage。overview が 0 でも見える。チェックは両方立つが、判定は manage。
        assert_visible(U('sales', manage=1, overview=0, mark_kintai=0))
        assert_visible(U('floor', manage=1, overview=1, mark_kintai=0))
        self.c.execute('UPDATE users SET manage=1, overview=0, mark_kintai=0 WHERE id=4')
        assert_visible(self.c.execute('SELECT * FROM users WHERE id=4').fetchone())

        # 一般。マーク管理や overview だけでは見えない。数量と保存は維持。
        assert_hidden(FLOOR3)
        assert_hidden(U('floor3', manage=0, overview=0, mark_kintai=1))
        assert_hidden(U('sales', manage=0, overview=0, mark_kintai=1))
        assert_hidden(U('floor', manage=0, overview=0, mark_kintai=1))
        assert_hidden(U('sales', manage=0, overview=1, mark_kintai=0))
        self.c.execute(
            "INSERT INTO users(id,login,name,role,manage,overview,active,mark_kintai) VALUES(?,?,?,?,?,?,?,?)",
            (11, 'g', '一般五郎', 'floor3', 0, 0, 1, 1),
        )
        assert_hidden(self.c.execute('SELECT * FROM users WHERE id=11').fetchone())

        # 権限の無い人は 403。本文に入力者名は無い。
        status, res = self.get(U('floor'), '2026-11')
        self.assertEqual(status, 403)
        self.assertNotIn('現場花子', json.dumps(res, ensure_ascii=False))

        # 修正・削除の範囲は変えない（overview のみは不可、manage は可）。
        self.assertEqual(self.post(U('sales', overview=1, manage=0), {'day': '2026-11-02', 'reading': 9, 'edit': True})[0], 403)
        self.assertEqual(self.post(U('sales', mark_kintai=1), {'day': '2026-11-02'}, '/api/kintai/crimp/delete')[0], 403)
        self.assertEqual(self.c.execute('SELECT reading FROM kintai_crimp_readings WHERE day=?', ('2026-11-02',)).fetchone()['reading'], 1010)
        # 入力候補の一覧は記録の閲覧ではなく、保存に必要なので一般にも残す。過去の紐づけは含めない。
        p = '/api/kintai/crimp/workers'
        status, workers = kintai.handle_get(_H(FLOOR3, p), self.c, p)
        self.assertEqual(status, 200)
        self.assertEqual(sorted(w['name'] for w in workers), ['主任次郎', '現場花子'])
        self.assertTrue(all(set(w) == {'id', 'name'} for w in workers))

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

    def test_worker_required_and_same_list_as_shipping(self):
        p = '/api/kintai/crimp/workers'
        code, rows = kintai.handle_get(_H(FLOOR3, p), self.c, p)
        self.assertEqual(code, 200)
        self.assertEqual([r['name'] for r in rows], [r['name'] for r in kintai_crimp.workers(self.c)])
        self.assertEqual(sorted(r['id'] for r in rows), [1, 2])
        self.assertEqual(kintai.handle_get(_H(U('floor'), p), self.c, p)[0], 403)
        for w in (None, 3, 4, 'x'):
            with self.assertRaises(ValueError):
                self.post(FLOOR3, {'day': '2026-11-02', 'reading': 1}, worker=w)
        self.assertIn('kintai_crimp.workers(c)', (ROOT / 'server.py').read_text(encoding='utf-8'))

    def test_ui_wired(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        nav = html[html.index('id="kintainav"'):html.index('id="sidebarfooter"')]
        self.assertLess(nav.index('data-kintai-section="bihin"'), nav.index('data-kintai-section="crimp">圧着カウンター<'))
        shift = html[html.index('id="kintaishift"'):html.index('id="kintaicrimp"')]
        self.assertNotIn('crimpcounter', shift)
        self.assertLess(html.index('id="kintaicrimp" hidden'), html.index('id="crimpcounter"'))
        pl = (ROOT / 'placement.js').read_text(encoding='utf-8')
        self.assertIn("if(section==='crimp')return 'crimp';", pl)
        self.assertIn("['shift','crimp'].includes(b.dataset.kintaiSection)", pl)
        self.assertNotIn('loadKintaiCrimp', (ROOT / 'kintai-shift.js').read_text(encoding='utf-8'))
        self.assertIn('/kintai-crimp.js', (ROOT / 'server.py').read_text(encoding='utf-8'))
        js = (ROOT / 'kintai-crimp.js').read_text(encoding='utf-8')
        self.assertIn('confirm(', js)
        self.assertIn('id="crimpworker" required', html)
        self.assertIn('<th class="crimp-inputter" hidden>入力者</th>', html)
        self.assertIn('const showInputter = !!data.canAdmin;', js)
        self.assertIn("const name = showInputter ? esc(r.userName || '') : '';", js)
        self.assertNotIn('loginName', js)
        self.assertNotIn('${esc(r.userName)}', js)


if __name__ == '__main__':
    unittest.main()
