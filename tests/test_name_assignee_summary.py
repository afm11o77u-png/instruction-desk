"""ネーム情報の担当別了承件数。合成データと一時DBのみ。値は変えない。"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import name_review
import names


def _connect(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, payload TEXT NOT NULL DEFAULT "{}")')
    c.execute(
        '''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, login TEXT, name TEXT, role TEXT,
            active INTEGER DEFAULT 1, must_change INTEGER DEFAULT 0
        )'''
    )
    return c


def _handler(path='/api/name-labels'):
    user = {
        'id': 1, 'login': 'owner@test', 'name': '主管理者', 'role': 'owner',
        'active': 1, 'must_change': 0,
    }
    return SimpleNamespace(user=user, path=path)


def _customer(c, key, name):
    return c.execute(
        'INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)'
        ' VALUES(?,?,?,?,?,?,0)',
        (key, 1, name, '', name, '対応'),
    ).lastrowid


def _format(c, customer_id, identity, format_name):
    return c.execute(
        'INSERT INTO name_formats('
        'identity, customer_id, source_kind, format_name, placement, family, heading, note,'
        'layout, reading, small, width_mm, height_mm, fixed_color, color_caption,'
        'groups_json, colors_json, swatches_json, maximum_font, heading_font, spec_json, retired'
        ') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
        (
            identity, customer_id, 1, format_name, '', 'HG丸ゴシック', '', '',
            'Name', 'フルネーム', 1, 50, 24, '白', '',
            '[]', '[]', '[]', 26, 0, '{}',
        ),
    ).lastrowid


def _confirm(c, format_id, user_id, name, *, stale=False):
    token = 'stale' if stale else name_review.digest(c, format_id)
    c.execute(
        '''INSERT INTO name_reviews(
             format_id, assignee_id, confirmed_by, confirmed_name, confirmed_at, confirmed_digest
           ) VALUES(?,?,?,?,?,?)''',
        (format_id, user_id, user_id, name, '2026-04-01T00:00:00', token),
    )


class NameAssigneeSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = _connect(self.root / 'instructions.sqlite3')
        names.DATA_DIR = None
        names.schema(self.c)
        name_review.schema(self.c)
        self.c.execute("INSERT INTO users(id, login, name, role, active) VALUES(2, 'a', '山田', 'sales', 1)")
        self.c.execute("INSERT INTO users(id, login, name, role, active) VALUES(3, 'b', '佐藤', 'sales', 1)")
        self.c.execute("INSERT INTO users(id, login, name, role, active) VALUES(4, 'c', '停止太郎', 'sales', 0)")
        self.alpha = _customer(self.c, 'alpha', 'あ顧客')
        self.beta = _customer(self.c, 'beta', 'い顧客')
        self.a1 = _format(self.c, self.alpha, 'a1', '形式A1')
        self.a2 = _format(self.c, self.alpha, 'a2', '形式A2')
        self.a3 = _format(self.c, self.alpha, 'a3', '形式A3')
        self.b1 = _format(self.c, self.beta, 'b1', '形式B1')
        self.blank = _format(self.c, self.beta, 'b2', '形式B2')
        _confirm(self.c, self.a1, 2, '山田')
        _confirm(self.c, self.a2, 2, '山田', stale=True)
        c_id = _format(self.c, self.alpha, 'a4', '形式A4')
        _confirm(self.c, c_id, 3, '佐藤')
        _confirm(self.c, self.b1, 3, '佐藤')
        self.c.execute(
            'INSERT INTO name_reviews(format_id, assignee_id, source_label) VALUES(?,?,?)',
            (self.a3, None, '旧ラベル'),
        )
        self.c.execute(
            'INSERT INTO name_reviews(format_id, assignee_id) VALUES(?,?)',
            (self.blank, 4),
        )
        self.retired = _format(self.c, self.alpha, 'retired', '退役')
        self.c.execute('UPDATE name_formats SET retired=1 WHERE id=?', (self.retired,))
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _summary(self, path='/api/name-labels'):
        code, body = names.search(_handler(path), self.c)
        self.assertEqual(code, 200)
        return body

    def test_counts_each_format_once_by_assignee(self):
        body = self._summary()
        rows = {row['name']: row for row in body['assigneeSummary']['rows']}
        self.assertEqual(rows['山田']['approved'], 1)
        self.assertEqual(rows['山田']['unapproved'], 1)
        self.assertEqual(rows['佐藤']['approved'], 2)
        self.assertEqual(rows['佐藤']['unapproved'], 0)
        self.assertEqual(rows['担当未設定']['approved'], 0)
        self.assertEqual(rows['担当未設定']['unapproved'], 1)
        self.assertEqual(rows['停止太郎（停止中）']['approved'], 0)
        self.assertEqual(rows['停止太郎（停止中）']['unapproved'], 1)
        self.assertNotIn('旧ラベル', rows)
        self.assertEqual(body['assigneeSummary']['scope'], 'all')
        self.assertEqual(body['assigneeSummary']['formatCount'], 6)
        self.assertEqual(
            body['assigneeSummary']['approved'] + body['assigneeSummary']['unapproved'],
            sum(item['approvedCount'] + item['unapprovedCount'] for item in body['items']),
        )
        stored = [
            tuple(row) for row in self.c.execute(
                'SELECT format_id, assignee_id, confirmed_by, confirmed_digest FROM name_reviews ORDER BY format_id'
            )
        ]
        self._summary()
        again = [
            tuple(row) for row in self.c.execute(
                'SELECT format_id, assignee_id, confirmed_by, confirmed_digest FROM name_reviews ORDER BY format_id'
            )
        ]
        self.assertEqual(again, stored)

    def test_search_limits_the_same_formats(self):
        body = self._summary('/api/name-labels?q=' + 'あ顧客')
        self.assertEqual(body['assigneeSummary']['scope'], 'filtered')
        self.assertIn('検索で表示中', body['assigneeSummary']['scopeLabel'])
        rows = {row['name']: row for row in body['assigneeSummary']['rows']}
        self.assertEqual(rows['佐藤']['approved'], 1)
        self.assertNotIn('停止太郎（停止中）', rows)
        self.assertEqual(body['total'], 1)

    def test_missing_user_is_visible_and_not_approved(self):
        missing = _format(self.c, self.alpha, 'gone', '形式不明')
        token = name_review.digest(self.c, missing)
        self.c.execute(
            '''INSERT INTO name_reviews(
                 format_id, assignee_id, confirmed_by, confirmed_name, confirmed_at, confirmed_digest
               ) VALUES(?,?,?,?,?,?)''',
            (missing, 99, 99, '消えた人', '2026-04-01T00:00:00', token),
        )
        self.c.commit()
        body = self._summary()
        rows = {row['name']: row for row in body['assigneeSummary']['rows']}
        self.assertEqual(rows['担当者不明']['approved'], 0)
        self.assertEqual(rows['担当者不明']['unapproved'], 1)
        self.assertEqual(body['assigneeSummary']['rows'][0]['kind'] in ('unset', 'missing'), True)


if __name__ == '__main__':
    unittest.main()
