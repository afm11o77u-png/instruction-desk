"""ネーム情報の利用者論理削除・復元（大元／子情報）。retired（カタログ退役）とは別。"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def _user(role='owner', uid=1, name='主管理者'):
    return {
        'id': uid, 'login': role + '@test', 'name': name, 'role': role,
        'active': 1, 'must_change': 0, 'manage': 0, 'overview': 0,
    }


def _handler(role='owner', uid=1, name='主管理者'):
    return SimpleNamespace(user=_user(role, uid, name), path='/api/name-labels')


def _add_customer(c, key, name):
    return c.execute(
        'INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)'
        ' VALUES(?,?,?,?,?,?,0)',
        (key, 1, name, '', name, '対応'),
    ).lastrowid


def _add_format(c, customer_id, identity, format_name):
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


def _add_sample(c, format_id, path='images/names/demo.png'):
    return c.execute(
        'INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)'
        ' VALUES(?,?,?,?,?,?)',
        (format_id, 'standard', '', '白', path, '見本'),
    ).lastrowid


class NameSoftDeleteRestoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'images' / 'names').mkdir(parents=True)
        (self.root / 'images' / 'names' / 'demo.png').write_bytes(b'\x89PNG\r\n\x1a\ndemo')
        self.db = self.root / 'instructions.sqlite3'
        self.c = _connect(self.db)
        names.bind(self.root)
        # schema 内の ensure_* を避けるため DATA_DIR を一時的に外す
        names.DATA_DIR = None
        names.schema(self.c)
        names.bind(self.root)
        self.cid = _add_customer(self.c, 'cust-a', 'テスト顧客A')
        self.f1 = _add_format(self.c, self.cid, 'fmt:a1', '形式1')
        self.f2 = _add_format(self.c, self.cid, 'fmt:a2', '形式2')
        self.s1 = _add_sample(self.c, self.f1)
        self.s2 = _add_sample(self.c, self.f2)
        self.other = _add_customer(self.c, 'cust-b', '別顧客B')
        self.of = _add_format(self.c, self.other, 'fmt:b1', '別形式')
        _add_sample(self.c, self.of)
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_schema_adds_soft_delete_columns_keeps_retired(self):
        cust = {r['name'] for r in self.c.execute('PRAGMA table_info(name_customers)')}
        fmt = {r['name'] for r in self.c.execute('PRAGMA table_info(name_formats)')}
        for col in ('deleted_at', 'deleted_by', 'deleted_by_name'):
            self.assertIn(col, cust)
            self.assertIn(col, fmt)
        self.assertIn('deleted_with_parent', fmt)
        self.assertIn('retired', cust)
        self.assertIn('retired', fmt)
        row = self.c.execute('SELECT deleted_at, retired FROM name_customers WHERE id=?', (self.cid,)).fetchone()
        self.assertIsNone(row['deleted_at'])
        self.assertEqual(row['retired'], 0)

    def test_child_only_delete_hides_format_and_sample_keeps_parent(self):
        status, body = names.soft_delete(_handler(), self.c, {'kind': 'format', 'id': self.f1})
        self.assertEqual(status, 200)
        self.assertEqual(body['format'], '形式1')
        img = (self.root / 'images' / 'names' / 'demo.png')
        self.assertTrue(img.is_file())

        st, search = names.search(_handler(), self.c)
        self.assertEqual(st, 200)
        ids = {item['id'] for item in search['items']}
        self.assertIn(self.cid, ids)
        self.assertIn(self.other, ids)

        st, cust = names.customer(_handler(), self.c, self.cid)
        self.assertEqual(st, 200)
        format_ids = {f['id'] for f in cust['formats']}
        self.assertNotIn(self.f1, format_ids)
        self.assertIn(self.f2, format_ids)

        self.assertEqual(names.detail(_handler(), self.c, self.f1)[0], 404)
        self.assertEqual(names.send_sample(_handler(), self.c, self.s1)[0], 404)
        self.assertEqual(names.detail(_handler(), self.c, self.f2)[0], 200)
        self.assertEqual(names.send_sample(_handler(), self.c, self.s2)[0], 200)

        row = self.c.execute('SELECT deleted_with_parent, deleted_by_name FROM name_formats WHERE id=?', (self.f1,)).fetchone()
        self.assertEqual(row['deleted_with_parent'], 0)
        self.assertEqual(row['deleted_by_name'], '主管理者')

    def test_parent_delete_hides_children_together(self):
        status, body = names.soft_delete(_handler(), self.c, {'kind': 'customer', 'id': self.cid})
        self.assertEqual(status, 200)
        self.assertEqual(body['sourceName'], 'テスト顧客A')
        self.assertEqual(body['hiddenFormats'], 2)
        self.assertTrue((self.root / 'images' / 'names' / 'demo.png').is_file())

        st, search = names.search(_handler(), self.c)
        ids = {item['id'] for item in search['items']}
        self.assertNotIn(self.cid, ids)
        self.assertIn(self.other, ids)

        self.assertEqual(names.customer(_handler(), self.c, self.cid)[0], 404)
        self.assertEqual(names.detail(_handler(), self.c, self.f1)[0], 404)
        self.assertEqual(names.detail(_handler(), self.c, self.f2)[0], 404)
        self.assertEqual(names.send_sample(_handler(), self.c, self.s1)[0], 404)

        for fid in (self.f1, self.f2):
            row = self.c.execute('SELECT deleted_with_parent FROM name_formats WHERE id=?', (fid,)).fetchone()
            self.assertEqual(row['deleted_with_parent'], 1)

        # 無関係な顧客は残る
        self.assertEqual(names.customer(_handler(), self.c, self.other)[0], 200)

    def test_restore_parent_restores_only_with_parent_children(self):
        # 形式1を個別削除 → 親削除 → 親復元で形式2だけ戻る
        names.soft_delete(_handler(), self.c, {'kind': 'format', 'id': self.f1})
        names.soft_delete(_handler(), self.c, {'kind': 'customer', 'id': self.cid})
        f1 = self.c.execute('SELECT deleted_with_parent, deleted_at FROM name_formats WHERE id=?', (self.f1,)).fetchone()
        f2 = self.c.execute('SELECT deleted_with_parent, deleted_at FROM name_formats WHERE id=?', (self.f2,)).fetchone()
        self.assertEqual(f1['deleted_with_parent'], 0)
        self.assertTrue(f1['deleted_at'])
        self.assertEqual(f2['deleted_with_parent'], 1)
        self.assertTrue(f2['deleted_at'])

        status, body = names.restore(_handler(), self.c, {'kind': 'customer', 'id': self.cid})
        self.assertEqual(status, 200)
        self.assertEqual(body['restoredFormats'], 1)

        st, cust = names.customer(_handler(), self.c, self.cid)
        self.assertEqual(st, 200)
        format_ids = {f['id'] for f in cust['formats']}
        self.assertNotIn(self.f1, format_ids)
        self.assertIn(self.f2, format_ids)

        # 個別削除の子を復元
        names.restore(_handler(), self.c, {'kind': 'format', 'id': self.f1})
        st, cust = names.customer(_handler(), self.c, self.cid)
        format_ids = {f['id'] for f in cust['formats']}
        self.assertEqual(format_ids, {self.f1, self.f2})

    def test_restore_child_only(self):
        names.soft_delete(_handler(), self.c, {'kind': 'format', 'id': self.f1})
        names.restore(_handler(), self.c, {'kind': 'format', 'id': self.f1})
        self.assertEqual(names.detail(_handler(), self.c, self.f1)[0], 200)
        self.assertEqual(names.send_sample(_handler(), self.c, self.s1)[0], 200)

    def test_non_owner_rejected(self):
        for role in ('sales', 'floor', 'floor2'):
            st, body = names.soft_delete(_handler(role), self.c, {'kind': 'format', 'id': self.f1})
            self.assertEqual(st, 403, role)
            self.assertIn('主管理者', body['error'])
            st, body = names.soft_delete(_handler(role), self.c, {'kind': 'customer', 'id': self.cid})
            self.assertEqual(st, 403, role)
            st, body = names.list_deleted(_handler(role), self.c)
            self.assertEqual(st, 403, role)
            st, body = names.restore(_handler(role), self.c, {'kind': 'format', 'id': self.f1})
            self.assertEqual(st, 403, role)

        # 削除されていないこと
        self.assertEqual(names.detail(_handler(), self.c, self.f1)[0], 200)
        self.assertEqual(names.customer(_handler(), self.c, self.cid)[0], 200)

    def test_deleted_list_separates_parent_and_individual_child(self):
        names.soft_delete(_handler(), self.c, {'kind': 'format', 'id': self.f1})
        names.soft_delete(_handler(), self.c, {'kind': 'customer', 'id': self.cid})
        st, data = names.list_deleted(_handler(), self.c)
        self.assertEqual(st, 200)
        cust_ids = {x['id'] for x in data['customers']}
        fmt_ids = {x['id'] for x in data['formats']}
        self.assertIn(self.cid, cust_ids)
        self.assertIn(self.f1, fmt_ids)
        self.assertNotIn(self.f2, fmt_ids)  # 親削除に巻き込まれた子は個別一覧に出ない
        parent = next(x for x in data['customers'] if x['id'] == self.cid)
        self.assertEqual(parent['hiddenWithParent'], 1)
        self.assertEqual(parent['individuallyDeletedFormats'], 1)

    def test_cancel_means_no_api_call_data_unchanged(self):
        # UI のキャンセルは API を呼ばない想定。呼ばなければ変更なしを確認。
        before = self.c.execute(
            'SELECT deleted_at FROM name_customers WHERE id=?', (self.cid,)
        ).fetchone()['deleted_at']
        self.assertIsNone(before)
        # 何も呼ばない
        after = self.c.execute(
            'SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND deleted_at IS NOT NULL',
            (self.cid,),
        ).fetchone()['n']
        self.assertEqual(after, 0)

    def test_retired_untouched_by_soft_delete(self):
        names.soft_delete(_handler(), self.c, {'kind': 'customer', 'id': self.cid})
        row = self.c.execute('SELECT retired, deleted_at FROM name_customers WHERE id=?', (self.cid,)).fetchone()
        self.assertEqual(row['retired'], 0)
        self.assertTrue(row['deleted_at'])
        for fid in (self.f1, self.f2):
            fmt = self.c.execute('SELECT retired, deleted_at FROM name_formats WHERE id=?', (fid,)).fetchone()
            self.assertEqual(fmt['retired'], 0)
            self.assertTrue(fmt['deleted_at'])


if __name__ == '__main__':
    unittest.main()
