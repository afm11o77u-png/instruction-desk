"""ネーム情報：見本画像1件ごとの論理削除・復元（主管理者のみ）。"""
import unittest
from types import SimpleNamespace

import names
import test_name_soft_delete_restore as base
from test_name_soft_delete_restore import _handler


class NameSampleDeleteTest(unittest.TestCase):
    setUp = base.NameSoftDeleteRestoreTest.setUp
    tearDown = base.NameSoftDeleteRestoreTest.tearDown

    def _second(self):
        sid = self.c.execute(
            'INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)'
            ' VALUES(?,?,?,?,?,?)', (self.f1, 'yellow', '黄色用紙', '黄', 'images/names/demo.png', '見本2'),
        ).lastrowid
        self.c.commit()
        return sid

    def _visible(self, fid):
        st, body = names.detail(_handler(), self.c, fid)
        self.assertEqual(st, 200)
        return [s['id'] for s in body['samples']]

    def test_columns_added(self):
        cols = {r['name'] for r in self.c.execute('PRAGMA table_info(name_samples)')}
        for col in ('deleted_at', 'deleted_by', 'deleted_by_name'):
            self.assertIn(col, cols)

    def test_owner_deletes_only_target(self):
        s1b = self._second()
        other_before = self._visible(self.of)
        f2_before = self._visible(self.f2)
        st, body = names.soft_delete(_handler(), self.c, {'kind': 'sample', 'id': s1b})
        self.assertEqual(st, 200, body)
        self.assertEqual(body['label'], '黄色用紙　用紙色：黄')
        self.assertEqual(self._visible(self.f1), [self.s1])
        self.assertEqual(self._visible(self.f2), f2_before)
        self.assertEqual(self._visible(self.of), other_before)
        self.assertEqual(names.send_sample(_handler(), self.c, s1b)[0], 404)
        self.assertEqual(names.send_sample(_handler(), self.c, self.s1)[0], 200)
        self.assertTrue((self.root / 'images' / 'names' / 'demo.png').is_file())
        row = self.c.execute('SELECT deleted_by_name FROM name_samples WHERE id=?', (s1b,)).fetchone()
        self.assertEqual(row['deleted_by_name'], '主管理者')
        st, fmt = names.detail(_handler(), self.c, self.f1)
        self.assertEqual(st, 200)
        self.assertNotIn(self.f1, [f['id'] for f in names.list_deleted(_handler(), self.c)[1]['formats']])

    def test_non_owner_denied(self):
        s1b = self._second()
        for role in ('sales', 'floor', 'floor2', 'floor3'):
            st, _ = names.soft_delete(_handler(role, 9, role), self.c, {'kind': 'sample', 'id': s1b})
            self.assertEqual(st, 403, role)
        st, _ = names.soft_delete(SimpleNamespace(user=None, path='/'), self.c, {'kind': 'sample', 'id': s1b})
        self.assertIn(st, (401, 403))
        self.assertEqual(sorted(self._visible(self.f1)), sorted([self.s1, s1b]))
        names.soft_delete(_handler(), self.c, {'kind': 'sample', 'id': s1b})
        for role in ('sales', 'floor', 'floor2', 'floor3'):
            self.assertEqual(names.restore(_handler(role, 9, role), self.c, {'kind': 'sample', 'id': s1b})[0], 403)

    def test_last_sample_blocked(self):
        st, body = names.soft_delete(_handler(), self.c, {'kind': 'sample', 'id': self.s2})
        self.assertEqual(st, 409)
        self.assertIn('この形式を削除', body['error'])
        self.assertEqual(self._visible(self.f2), [self.s2])

    def test_restore(self):
        s1b = self._second()
        names.soft_delete(_handler(), self.c, {'kind': 'sample', 'id': s1b})
        st, deleted = names.list_deleted(_handler(), self.c)
        self.assertEqual([s['id'] for s in deleted['samples']], [s1b])
        st, body = names.restore(_handler(), self.c, {'kind': 'sample', 'id': s1b})
        self.assertEqual(st, 200, body)
        self.assertFalse(body['parentDeleted'])
        self.assertEqual(sorted(self._visible(self.f1)), sorted([self.s1, s1b]))
        self.assertEqual(names.list_deleted(_handler(), self.c)[1]['samples'], [])
        self.assertEqual(names.restore(_handler(), self.c, {'kind': 'sample', 'id': s1b})[0], 404)
        self.assertEqual(names.soft_delete(_handler(), self.c, {'kind': 'sample', 'id': 'x'})[0], 400)


if __name__ == '__main__':
    unittest.main()
