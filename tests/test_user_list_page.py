"""利用者一覧ページ: 配線・権限（/api/users を再利用）・読み取り専用。"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

import auth

ROOT = Path(__file__).resolve().parents[1]


class _H(auth.AuthMixin):
    def __init__(self, user):
        self._u = user

    def session_user(self, c):
        return self._u


def U(role, **kw):
    u = {'id': 1, 'login': 'x@example.com', 'name': 'x', 'role': role, 'manage': 0, 'overview': 0,
         'active': 1, 'must_change': 0, 'version': 1, 'mark_kintai': 0}
    u.update(kw)
    return u


class UserListPageTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, role TEXT, manage INTEGER, '
                       'overview INTEGER, active INTEGER, must_change INTEGER, version INTEGER, mark_kintai INTEGER, password TEXT)')
        self.c.execute("INSERT INTO users VALUES (1,'a@example.com','甲','owner',0,0,1,0,1,0,'secret-hash')")
        self.c.execute("INSERT INTO users VALUES (2,'b@example.com','乙','floor3',0,0,1,1,1,1,'secret-hash')")

    def test_same_access_as_account_manager(self):
        code, rows = _H(U('owner')).auth_get('/api/users', self.c)
        self.assertEqual((code, len(rows)), (200, 2))
        self.assertNotIn('password', rows[0])
        self.assertEqual(_H(U('sales', manage=1)).auth_get('/api/users', self.c)[0], 200)
        for role in ('sales', 'floor', 'floor2', 'floor3'):
            self.assertEqual(_H(U(role)).auth_get('/api/users', self.c)[0], 403)
        self.assertEqual(_H(None).auth_get('/api/users', self.c)[0], 401)

    def test_wiring_and_read_only(self):
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        for k in ("'/users':'users.html'", "'/users.js':'users.js'", "'/users.css':'users.css'"):
            self.assertIn(k, server)
        self.assertIn('href="/users" target="_blank"', (ROOT / 'account.js').read_text(encoding='utf-8'))
        html = (ROOT / 'users.html').read_text(encoding='utf-8')
        for h in ('ログインID', '氏名', '業務権限', '管理者', 'マーク管理', '利用状態', '仮パスワード'):
            self.assertIn(f'>{h}</th>', html)
        self.assertNotIn('<input', html)
        js = (ROOT / 'users.js').read_text(encoding='utf-8')
        self.assertIn("fetch('/api/users')", js)
        self.assertNotIn('method', js)  # 読むだけ
        self.assertIn('aria-sort', js)


if __name__ == '__main__':
    unittest.main()
