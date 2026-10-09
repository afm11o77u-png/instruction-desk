"""Tests for 貼付け位置「修正依頼」簡易掲示板 (post / persist / link / status / permissions)."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import placement
import migration


def _connect(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _user(uid=1, name='現場太郎', login='floor@example.co.jp', role='floor', must_change=0):
    return {'id': uid, 'name': name, 'login': login, 'role': role, 'must_change': must_change}


def _handler(user, path='/api/placement-corrections'):
    return SimpleNamespace(user=user, path=path)


class PlacementCorrectionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='pcorr-')
        self.data = Path(self._tmp.name)
        self.db = self.data / 'instructions.sqlite3'
        placement.bind(self.data)
        with _connect(self.db) as c:
            placement.schema(c)
            c.execute(
                '''INSERT INTO placements(original_name,relative_path,thumb_path,customer,part,created_at,updated_at,version)
                   VALUES('a.png','images/placement/a.png','images/placement/thumbs/a.jpg','宮の沢病院','胸ポケット','2026-01-01T10:00:00','2026-01-01T10:00:00',1)'''
            )
            self.placement_id = c.execute('SELECT id FROM placements').fetchone()[0]
            c.commit()

    def tearDown(self):
        self._tmp.cleanup()

    def test_post_persist_and_list(self):
        viewer = _user(role='sales', name='営業花子', login='sales@example.co.jp')
        with _connect(self.db) as c:
            status, created = placement.create_correction(
                _handler(viewer), c,
                {'body': '胸の位置がずれています', 'placementId': self.placement_id},
            )
            self.assertEqual(status, 200)
            self.assertEqual(created['body'], '胸の位置がずれています')
            self.assertEqual(created['status'], '未対応')
            self.assertEqual(created['authorName'], '営業花子')
            self.assertEqual(created['placementId'], self.placement_id)
            self.assertEqual(created['customer'], '宮の沢病院')
            self.assertEqual(created['part'], '胸ポケット')
            c.commit()

        # Persist across reconnect (restart simulation).
        with _connect(self.db) as c:
            status, data = placement.list_corrections(
                _handler(viewer, '/api/placement-corrections'), c,
            )
            self.assertEqual(status, 200)
            self.assertEqual(data['total'], 1)
            self.assertEqual(data['counts']['未対応'], 1)
            item = data['items'][0]
            self.assertEqual(item['id'], created['id'])
            self.assertEqual(item['customer'], '宮の沢病院')
            self.assertTrue(item['hasPlacement'])

    def test_page_wide_request_without_image(self):
        viewer = _user(role='floor')
        with _connect(self.db) as c:
            status, created = placement.create_correction(
                _handler(viewer), c, {'body': 'フォルダ分けのルールを教えてください'},
            )
            self.assertEqual(status, 200)
            self.assertIsNone(created['placementId'])
            self.assertFalse(created['hasPlacement'])
            self.assertEqual(created['customer'], '')
            c.commit()

    def test_link_requires_existing_placement(self):
        viewer = _user(role='floor')
        with _connect(self.db) as c:
            status, body = placement.create_correction(
                _handler(viewer), c, {'body': '無い画像', 'placementId': 99999},
            )
            self.assertEqual(status, 404)
            self.assertIn('貼付け位置', body['error'])

    def test_status_update_records_actor(self):
        poster = _user(uid=2, role='sales', name='営業')
        editor = _user(uid=1, role='floor2', name='現場二', login='floor2@example.co.jp')
        with _connect(self.db) as c:
            _, created = placement.create_correction(
                _handler(poster), c, {'body': '色が違う', 'placementId': self.placement_id},
            )
            status, updated = placement.update_correction(
                _handler(editor), c,
                {'id': created['id'], 'version': created['version'], 'status': '対応中', 'response': '確認します'},
            )
            self.assertEqual(status, 200)
            self.assertEqual(updated['status'], '対応中')
            self.assertEqual(updated['response'], '確認します')
            self.assertEqual(updated['statusByName'], '現場二')
            self.assertEqual(updated['statusById'], 1)
            self.assertTrue(updated['statusAt'])
            c.commit()

        with _connect(self.db) as c:
            status, data = placement.list_corrections(
                _handler(editor, '/api/placement-corrections?status=対応中'), c,
            )
            self.assertEqual(status, 200)
            self.assertEqual(data['total'], 1)
            self.assertEqual(data['items'][0]['status'], '対応中')

    def test_view_permission_can_post_but_not_status(self):
        sales = _user(role='sales')
        with _connect(self.db) as c:
            status, created = placement.create_correction(
                _handler(sales), c, {'body': '依頼だけ'},
            )
            self.assertEqual(status, 200)
            status, body = placement.update_correction(
                _handler(sales), c,
                {'id': created['id'], 'version': created['version'], 'status': '対応済み', 'response': 'x'},
            )
            self.assertEqual(status, 403)
            self.assertIn('主管理者', body['error'])

    def test_edit_roles_can_change_status(self):
        for role in ('owner', 'floor2'):
            with self.subTest(role=role):
                with _connect(self.db) as c:
                    _, created = placement.create_correction(
                        _handler(_user(role='floor')), c, {'body': f'role-{role}'},
                    )
                    status, updated = placement.update_correction(
                        _handler(_user(uid=9, role=role, name=role)), c,
                        {'id': created['id'], 'version': created['version'], 'status': '対応済み', 'response': '完了'},
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(updated['status'], '対応済み')

    def test_forbidden_roles(self):
        outsider = _user(role='admin')  # outside placement view roles
        with _connect(self.db) as c:
            status, body = placement.list_corrections(_handler(outsider), c)
            self.assertEqual(status, 403)
            status, body = placement.create_correction(
                _handler(outsider), c, {'body': '不可'},
            )
            self.assertEqual(status, 403)

    def test_get_placement_for_board_link(self):
        viewer = _user(role='floor')
        with _connect(self.db) as c:
            status, row = placement.get(_handler(viewer), c, self.placement_id)
            self.assertEqual(status, 200)
            self.assertEqual(row['customer'], '宮の沢病院')
            self.assertEqual(row['part'], '胸ポケット')

    def test_filter_open_status_first(self):
        viewer = _user(role='owner')
        with _connect(self.db) as c:
            _, a = placement.create_correction(_handler(viewer), c, {'body': '先に未対応'})
            _, b = placement.create_correction(_handler(viewer), c, {'body': '対応済みにする'})
            placement.update_correction(
                _handler(viewer), c,
                {'id': b['id'], 'version': b['version'], 'status': '対応済み', 'response': '済'},
            )
            _, data = placement.list_corrections(_handler(viewer, '/api/placement-corrections'), c)
            self.assertEqual(data['items'][0]['status'], '未対応')
            self.assertEqual(data['items'][0]['body'], '先に未対応')

    def test_backup_zip_includes_corrections_table(self):
        viewer = _user(role='floor')
        with _connect(self.db) as c:
            placement.create_correction(_handler(viewer), c, {'body': 'バックアップ対象'})
            c.commit()

        def connect():
            return _connect(self.db)

        dest = placement.make_backup(connect, self.data)
        self.assertTrue(dest.is_file())
        import zipfile
        with zipfile.ZipFile(dest) as zf:
            zf.extract('instructions.sqlite3', self.data / 'restored')
        with _connect(self.data / 'restored' / 'instructions.sqlite3') as c:
            n = c.execute('SELECT COUNT(*) FROM placement_corrections').fetchone()[0]
            self.assertEqual(n, 1)
            body = c.execute('SELECT body FROM placement_corrections').fetchone()[0]
            self.assertEqual(body, 'バックアップ対象')


class PlacementCorrectionMigrationTest(unittest.TestCase):
    def test_user_map_remaps_author_and_status_names(self):
        with tempfile.TemporaryDirectory(prefix='pcorr-mig-') as tmp:
            root = Path(tmp)
            db = root / 'instructions.sqlite3'
            with _connect(db) as c:
                placement.schema(c)
                c.execute(
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, version INTEGER)"
                )
                c.execute(
                    "INSERT INTO users(id,login,name,password,role,manage,overview,active,must_change,version) VALUES(10,'src@x','移行元投稿者','h','sales',0,0,1,0,1)"
                )
                c.execute(
                    "INSERT INTO users(id,login,name,password,role,manage,overview,active,must_change,version) VALUES(11,'src2@x','移行元対応者','h','floor2',0,0,1,0,1)"
                )
                c.execute(
                    '''INSERT INTO placement_corrections(placement_id,body,author_id,author_name,created_at,status,status_by_id,status_by_name,status_at,response,updated_at,version)
                       VALUES(NULL,'移行テスト',10,'移行元投稿者','2026-01-01','対応中',11,'移行元対応者','2026-01-02','確認中','2026-01-02',1)'''
                )
                c.commit()

            auth_snapshot = {
                'users': [
                    {'id': 100, 'login': 'dst@x', 'name': '会社投稿者', 'password': 'h', 'role': 'sales', 'manage': 0, 'overview': 0, 'active': 1, 'must_change': 0, 'version': 1},
                    {'id': 101, 'login': 'dst2@x', 'name': '会社対応者', 'password': 'h', 'role': 'floor2', 'manage': 0, 'overview': 0, 'active': 1, 'must_change': 0, 'version': 1},
                ],
                'settings': {},
            }
            user_map = {10: 100, 11: 101}
            migration._apply_dest_users_and_remap(
                db, auth_snapshot, user_map,
                actor={'id': 100, 'name': '会社投稿者', 'login': 'dst@x'},
                manifest={'createdAt': '2026-01-01'},
                auto_zip=None,
            )
            with _connect(db) as c:
                row = c.execute('SELECT * FROM placement_corrections').fetchone()
                self.assertEqual(row['author_id'], 100)
                self.assertEqual(row['author_name'], '会社投稿者')
                self.assertEqual(row['status_by_id'], 101)
                self.assertEqual(row['status_by_name'], '会社対応者')
                users = [r['id'] for r in c.execute('SELECT id FROM users ORDER BY id')]
                self.assertEqual(users, [100, 101])

    def test_key_counts_include_corrections(self):
        with tempfile.TemporaryDirectory(prefix='pcorr-kc-') as tmp:
            root = Path(tmp)
            instr = root / 'instructions.sqlite3'
            stock = root / 'stock.sqlite3'
            with _connect(instr) as c:
                placement.schema(c)
                c.execute(
                    '''INSERT INTO placement_corrections(body,author_name,created_at,updated_at)
                       VALUES('a','x','2026-01-01','2026-01-01')'''
                )
                c.commit()
            counts = migration._key_counts(instr, stock)
            self.assertEqual(counts.get('placementCorrections'), 1)


class PlacementCorrectionUiSmokeTest(unittest.TestCase):
    def test_ui_hooks_present(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        js = (ROOT / 'placement.js').read_text(encoding='utf-8')
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        for needle in (
            'id="placementboardopen"',
            'id="placementboard"',
            'id="placementrequest"',
            'id="placementrespond"',
            'id="placementrequestfromview"',
            '修正を依頼',
        ):
            self.assertIn(needle, index)
        for needle in (
            'openPlacementBoard',
            '/api/placement-corrections',
            'placementrequestfromview',
            'canEditPlacement',
            '画像を開く',
        ):
            self.assertIn(needle, js)
        self.assertIn('placementboardcard', css)
        self.assertIn('is-open', css)
        # Zoom bar range from #54 must stay untouched.
        self.assertIn('id="placementzoom" type="range" min="30" max="170"', index)


if __name__ == '__main__':
    unittest.main()
