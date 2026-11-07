"""登録した定型書式の保存・権限・既存データの保持。実DBは使わない。"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

import fixed_label_formats as forms
import split_backup


def _connect(path):
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    return c


def _user(role='floor', uid=1, must_change=0):
    return {
        'id': uid,
        'name': f'{role}太郎',
        'login': f'{role}@example.co.jp',
        'role': role,
        'must_change': must_change,
    }


def _handler(user):
    return SimpleNamespace(session_user=lambda c: user)


def _tiers(name='長尾様行'):
    return {
        'name': name,
        'kind': 'tiers',
        'payload': {'lines': ['石屋商事', '長尾様行', '納品日　月　日']},
    }


def _depts(name='銭函チェック', departments=None):
    return {
        'name': name,
        'kind': 'departments',
        'payload': {
            'customer': 'だるま食品銭函工場',
            'departments': departments or ['製造１課', '衛生'],
        },
    }


class FixedLabelFormatStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='fixed-label-formats-')
        self.db = Path(self._tmp.name) / 'instructions.sqlite3'
        self.c = _connect(self.db)
        self.c.execute(
            'CREATE TABLE audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor_id INTEGER, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL)'
        )
        self.c.execute('CREATE TABLE orders (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL)')
        self.c.execute(
            'CREATE TABLE label_sets (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL)'
        )
        self.c.execute("INSERT INTO orders(version,payload) VALUES(1,?)", ('{"customer":"残す顧客"}',))
        self.c.execute(
            "INSERT INTO label_sets(user_id,name,version,payload) VALUES(1,'既存宛名',1,?)",
            ('[{"customer":"既存"}]',),
        )
        forms.initialize(self.c)
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self._tmp.cleanup()

    def _reopen(self):
        self.c.commit()
        self.c.close()
        self.c = _connect(self.db)

    def _save(self, data, role='floor', uid=1):
        return forms.handle(_handler(_user(role, uid)), self.c, '/api/fixed-label-formats/save', data)

    def test_initialize_keeps_existing_rows_and_is_idempotent(self):
        status, saved = self._save(_tiers())
        self.assertEqual(status, 200)
        forms.initialize(self.c)
        self.c.commit()
        self._reopen()
        forms.initialize(self.c)
        orders = self.c.execute('SELECT payload FROM orders').fetchone()['payload']
        labels = self.c.execute('SELECT name,payload FROM label_sets').fetchone()
        formats = self.c.execute('SELECT name,payload FROM fixed_label_formats').fetchall()
        self.assertEqual(orders, '{"customer":"残す顧客"}')
        self.assertEqual(labels['name'], '既存宛名')
        self.assertEqual(labels['payload'], '[{"customer":"既存"}]')
        self.assertEqual(len(formats), 1)
        self.assertEqual(formats[0]['name'], '長尾様行')
        self.assertIn('納品日　月　日', formats[0]['payload'])

    def test_save_list_reopen_and_update(self):
        status, saved = self._save(_depts())
        self.assertEqual(status, 200)
        self.assertEqual(saved['kind'], 'departments')
        self.assertEqual(saved['version'], 1)
        self.assertEqual(saved['payload']['customer'], 'だるま食品銭函工場')
        self.assertEqual(saved['updatedBy'], 'floor太郎')
        self._reopen()
        status, rows = forms.handle(_handler(_user('warehouse', 9)), self.c, '/api/fixed-label-formats', None)
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], saved['id'])
        self.assertEqual(rows[0]['payload']['departments'], ['製造１課', '衛生'])

        status, updated = self._save({
            'id': saved['id'],
            'version': saved['version'],
            'name': '銭函チェック改',
            'kind': 'departments',
            'payload': {'customer': 'だるま食品銭函工場', 'departments': ['製造１課', '営業部', '工場']},
        }, role='floor2', uid=2)
        self.assertEqual(status, 200)
        self.assertEqual(updated['version'], 2)
        self.assertEqual(updated['name'], '銭函チェック改')
        self.assertEqual(updated['updatedBy'], 'floor2太郎')
        self.assertEqual(updated['payload']['departments'], ['製造１課', '営業部', '工場'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM fixed_label_formats').fetchone()[0], 1)
        actions = [r['action'] for r in self.c.execute('SELECT action FROM audit ORDER BY id')]
        self.assertEqual(actions, ['定型書式を登録', '定型書式を修正'])

    def test_version_conflict_does_not_change_the_row(self):
        _, saved = self._save(_tiers())
        before = self.c.execute('SELECT name,version,payload FROM fixed_label_formats WHERE id=?', (saved['id'],)).fetchone()
        status, body = self._save({
            'id': saved['id'],
            'version': 99,
            'name': '壊す',
            'kind': 'tiers',
            'payload': {'lines': ['あ', 'い', 'う']},
        })
        self.assertEqual(status, 409)
        self.assertIn('読み込み直してください', body['error'])
        after = self.c.execute('SELECT name,version,payload FROM fixed_label_formats WHERE id=?', (saved['id'],)).fetchone()
        self.assertEqual(after['name'], before['name'])
        self.assertEqual(after['version'], before['version'])
        self.assertEqual(after['payload'], before['payload'])

    def test_duplicate_and_builtin_names_do_not_insert(self):
        self._save(_tiers('長尾様行'))
        status, body = self._save(_tiers('長尾様行'), role='owner', uid=3)
        self.assertEqual(status, 409)
        self.assertIn('同じ名前', body['error'])
        with self.assertRaises(ValueError):
            self._save(_tiers('石屋商事'))
        with self.assertRaises(ValueError):
            self._save(_tiers('  ひまわり1  '))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM fixed_label_formats').fetchone()[0], 1)

    def test_safe_text_and_department_limits(self):
        nasty = "石屋'); DROP TABLE orders;--"
        status, saved = self._save({
            'name': nasty,
            'kind': 'tiers',
            'payload': {'lines': [' <script>alert(1)</script> ', 'A&B', '上\n段\u0001']},
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved['name'], nasty)
        self.assertEqual(saved['payload']['lines'][0], '<script>alert(1)</script>')
        self.assertEqual(saved['payload']['lines'][1], 'A&B')
        self.assertEqual(saved['payload']['lines'][2], '上段')
        self.assertEqual(self.c.execute('SELECT payload FROM orders').fetchone()['payload'], '{"customer":"残す顧客"}')
        with self.assertRaises(ValueError):
            self._save(_depts('空', ['製造', '  ']))
        with self.assertRaises(ValueError):
            self._save(_depts('多すぎ', [f'課{i}' for i in range(25)]))
        status, full = self._save(_depts('ちょうど', [f'課{i}' for i in range(24)]))
        self.assertEqual(status, 200)
        self.assertEqual(len(full['payload']['departments']), 24)
        with self.assertRaises(ValueError):
            self._save({'name': '欠け', 'kind': 'tiers', 'payload': {'lines': ['あ', 'い', '']}})

    def test_no_delete_and_missing_id(self):
        _, saved = self._save(_tiers())
        status, body = forms.handle(
            _handler(_user()), self.c, '/api/fixed-label-formats/delete',
            {'id': saved['id'], 'version': saved['version']},
        )
        self.assertEqual(status, 404)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM fixed_label_formats').fetchone()[0], 1)
        status, body = self._save({
            'id': 999,
            'version': 1,
            'name': '無い',
            'kind': 'tiers',
            'payload': {'lines': ['あ', 'い', 'う']},
        })
        self.assertEqual(status, 404)

    def test_permissions(self):
        for role in ('owner', 'floor', 'floor2', 'floor3'):
            status, saved = self._save(_tiers(f'{role}の3段'), role=role, uid={'owner': 1, 'floor': 2, 'floor2': 3, 'floor3': 4}[role])
            self.assertEqual(status, 200, role)
            self.assertEqual(saved['name'], f'{role}の3段')
        _, saved = self._save(_depts())
        for role in ('owner', 'floor', 'floor2', 'floor3', 'warehouse'):
            status, rows = forms.handle(_handler(_user(role, 8)), self.c, '/api/fixed-label-formats', None)
            self.assertEqual(status, 200, role)
            self.assertTrue(any(r['id'] == saved['id'] for r in rows))
        for role in ('sales', 'warehouse'):
            status, body = self._save(_tiers(f'{role}は登録できない'), role=role, uid=9)
            self.assertEqual(status, 403, role)
            self.assertIn('現場・現場２・現場３と主管理者', body['error'])
        status, body = forms.handle(_handler(_user('sales')), self.c, '/api/fixed-label-formats', None)
        self.assertEqual(status, 403)
        self.assertIn('倉庫担当', body['error'])
        status, body = forms.handle(_handler(None), self.c, '/api/fixed-label-formats', None)
        self.assertEqual(status, 401)
        status, body = forms.handle(_handler(_user('owner', must_change=1)), self.c, '/api/fixed-label-formats', None)
        self.assertEqual(status, 401)
        status, body = self._save(_tiers('停止前'), role='owner', uid=1)
        # 営業の登録失敗のあと、既存行は残る
        self.assertGreaterEqual(self.c.execute('SELECT COUNT(*) FROM fixed_label_formats').fetchone()[0], 1)
        self.assertEqual(self.c.execute('SELECT payload FROM orders').fetchone()['payload'], '{"customer":"残す顧客"}')
        self.assertEqual(self.c.execute('SELECT name FROM label_sets').fetchone()['name'], '既存宛名')

    def test_source_has_no_destructive_sql(self):
        src = (ROOT / 'fixed_label_formats.py').read_text(encoding='utf-8')
        self.assertIn('CREATE TABLE IF NOT EXISTS fixed_label_formats', src)
        self.assertNotIn('DROP TABLE', src)
        self.assertNotIn('DELETE FROM', src)


class FixedLabelFormatWiringTest(unittest.TestCase):
    def test_builtin_names_match_renderer(self):
        completed = subprocess.run(
            ['node', '-e', "process.stdout.write(JSON.stringify(require('./fixed_label.js').fixedLabelBuiltinNames()))"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout), list(forms.BUILTIN_NAMES))

    def test_node_preview_unit(self):
        completed = subprocess.run(
            ['node', str(ROOT / 'tests' / 'test_fixed_label_formats.js')],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('ok', completed.stdout)

    def test_screen_and_routes(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        sheet = (ROOT / 'sheet.js').read_text(encoding='utf-8')
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        style = (ROOT / 'style.css').read_text(encoding='utf-8')
        for token in (
            'id="fixedlabelregister"',
            'id="fixedregname"',
            'id="fixedregline1"',
            'id="fixedregline2"',
            'id="fixedregline3"',
            'id="fixedregcustomer"',
            'id="fixedregdeptadd"',
            'id="fixedregsave"',
            'id="fixedregupdate"',
            'id="fixedregload"',
            '登録した書式も、この書式一覧から選べます。',
        ):
            self.assertIn(token, index)
        self.assertIn('この行を外す', sheet)
        self.assertNotIn('id="fixedregdelete"', index)
        self.assertNotIn('fixed-label-formats/delete', sheet)
        self.assertIn("['owner','floor','floor2','floor3']", sheet)
        self.assertIn("currentUser?.role==='warehouse'", sheet)
        self.assertIn('departmentRowChange', sheet)
        self.assertIn('/api/fixed-label-formats', sheet)
        self.assertIn("path=='/api/fixed-label-formats'", server)
        self.assertIn("route.startswith('/api/fixed-label-formats/')", server)
        self.assertIn('fixed_label_formats.initialize(c)', server)
        self.assertIn('#labeldesk .labelfixedregister', style)
        self.assertIn('fixed_label_formats', split_backup.CATEGORIES['desk']['tables'])
        self.assertEqual(split_backup.table_category('fixed_label_formats'), 'desk')
        start = sheet.index('function printFixedLabel')
        end = sheet.index('fillFixedLabelFormatSelect();', start)
        body = sheet[start:end]
        self.assertNotIn('/api/fixed-label-formats', body)
        self.assertNotIn('fetch(', body)


if __name__ == '__main__':
    unittest.main()
