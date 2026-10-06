"""Throwaway-dir tests for owner-only business data migration.

Never touches the live workspace data/ directory used by a running agent.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import migration
from auth import password_hash, verify


def _init_instructions(db_path, *, login='owner@example.co.jp', password='Password1234!!', name='主管理者'):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db_path))
    try:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
            CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE history (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL, actor_id INTEGER, actor TEXT, changes TEXT);
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, manage INTEGER NOT NULL DEFAULT 0, overview INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, gate TEXT);
            CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
            CREATE TABLE login_gates (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
            CREATE TABLE auth_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
            CREATE TABLE audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor_id INTEGER, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);
            CREATE TABLE templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, folder TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE picking_lists (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, creator INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE placements (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER UNIQUE, original_name TEXT NOT NULL DEFAULT '', relative_path TEXT NOT NULL DEFAULT '', thumb_path TEXT NOT NULL DEFAULT '', customer TEXT NOT NULL DEFAULT '', part TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by_id INTEGER, updated_by_id INTEGER, version INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE shipments (id INTEGER PRIMARY KEY);
            CREATE TABLE name_formats (id INTEGER PRIMARY KEY);
            CREATE TABLE name_samples (id INTEGER PRIMARY KEY, format_id INTEGER, relative_path TEXT NOT NULL DEFAULT '');
            CREATE TABLE label_sets (id INTEGER PRIMARY KEY);
        ''')
        hashed = password_hash(password)
        uid = c.execute(
            "INSERT INTO users(login,name,password,role,manage) VALUES(?,?,?,'owner',1)",
            (login, name, hashed),
        ).lastrowid
        c.execute(
            "INSERT INTO orders(version,payload) VALUES(1,?)",
            (json.dumps({'customer': '移行テスト顧客', 'status': '未着手', 'owner': name}, ensure_ascii=False),),
        )
        c.execute(
            "INSERT INTO templates(user_id,folder,name,version,payload) VALUES(?,?,?,1,?)",
            (uid, '', 'ひな形A', json.dumps({'customer': 'ひな形顧客'}, ensure_ascii=False)),
        )
        c.execute(
            "INSERT INTO picking_lists(id,version,creator,payload) VALUES(1,1,?,?)",
            (uid, json.dumps({'number': 'PK-000001', 'status': '確定'}, ensure_ascii=False)),
        )
        c.execute(
            "INSERT INTO placements(original_name,relative_path,thumb_path,customer,part,created_at,updated_at,version) VALUES(?,?,?,?,?,?,?,1)",
            ('sample.png', 'images/placement/sample.png', 'images/placement/thumbs/sample.jpg', '顧客A', '胸', '2026-01-01', '2026-01-01'),
        )
        c.execute("INSERT INTO sessions VALUES('tok', ?, 9999999999)", (uid,))
        c.execute("INSERT INTO settings VALUES('seeded','1')")
        c.commit()
        return uid, hashed
    finally:
        c.close()


def _init_stock(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(db_path))
    try:
        c.executescript('''
            CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
            CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
            CREATE TABLE products (id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL);
            CREATE TABLE lots (id INTEGER PRIMARY KEY, qty_remaining INTEGER NOT NULL);
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        ''')
        c.execute("INSERT INTO customers(name) VALUES('在庫顧客')")
        c.execute("INSERT INTO suppliers(name) VALUES('仕入先A')")
        c.execute("INSERT INTO products(code,name) VALUES('P1','商品1')")
        c.execute("INSERT INTO lots(qty_remaining) VALUES(3)")
        c.commit()
    finally:
        c.close()


def _seed_files(data_dir):
    place = data_dir / 'images' / 'placement'
    thumbs = place / 'thumbs'
    names = data_dir / 'images' / 'names'
    imported = data_dir / 'name-import'
    for folder in (place, thumbs, names, imported):
        folder.mkdir(parents=True, exist_ok=True)
    (place / 'sample.png').write_bytes(b'\x89PNG\r\n\x1a\n-fake-')
    (thumbs / 'sample.jpg').write_bytes(b'fake-thumb')
    (names / 'upload-000001.png').write_bytes(b'\x89PNG\r\n\x1a\n-name-')
    (imported / 'catalog.json').write_text('{"customerCount":1}', encoding='utf-8')
    (imported / 'skip-me.png').write_bytes(b'\x89PNG\r\n\x1a\n-skip-')
    # Excluded noise
    (data_dir / 'mail-out').mkdir(exist_ok=True)
    (data_dir / 'mail-out' / 'draft.eml').write_text('x', encoding='utf-8')
    (data_dir / 'backups').mkdir(exist_ok=True)
    (data_dir / 'backups' / 'old.zip').write_bytes(b'PK\x03\x04old')
    (data_dir / 'network-should-not-be-here.json').write_text('{"port":1}', encoding='utf-8')


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='migtest-')
        self.root = Path(self._tmp.name)
        self.src = self.root / 'home-pc'
        self.dst = self.root / 'company-pc'
        self.src.mkdir()
        self.dst.mkdir()
        self.password = 'Password1234!!'
        self.uid, self.hash = _init_instructions(self.src / 'instructions.sqlite3', password=self.password)
        _init_stock(self.src / 'stock.sqlite3')
        _seed_files(self.src)
        # Destination has different business data + PC-specific config outside DATA.
        _init_instructions(self.dst / 'instructions.sqlite3', login='old@example.co.jp', password='OldPassword1234!!', name='旧管理者')
        _init_stock(self.dst / 'stock.sqlite3')
        _seed_files(self.dst)
        (self.dst / 'images' / 'placement' / 'only-on-company.png').write_bytes(b'company-only')
        self.pc_config = self.root / 'company-network.json'
        self.pc_config.write_text(json.dumps({'port': 8765, 'bind': '127.0.0.1', 'addresses': ['10.0.0.8']}), encoding='utf-8')

    def tearDown(self):
        self._tmp.cleanup()

    def test_export_restore_roundtrip_counts_and_password(self):
        zip_path, manifest = migration.build_export_zip(self.src, self.root / 'usb' / 'desk-migration-test.zip')
        self.assertTrue(zip_path.is_file())
        self.assertIn('desk-migration', zip_path.name)
        self.assertEqual(manifest['formatVersion'], migration.FORMAT_VERSION)
        self.assertEqual(manifest['keyCounts']['orders'], 1)
        self.assertEqual(manifest['keyCounts']['users'], 1)
        self.assertEqual(manifest['keyCounts']['stockCustomers'], 1)
        # Excludes
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        self.assertIn('instructions.sqlite3', names)
        self.assertIn('stock.sqlite3', names)
        self.assertIn('name-import/catalog.json', names)
        self.assertTrue(any(n.startswith('manifest.json') for n in names))
        self.assertFalse(any(n.startswith('images/') for n in names))
        self.assertNotIn('name-import/skip-me.png', names)
        self.assertFalse(any(n.lower().endswith(ext) for n in names for ext in ('.png', '.jpg', '.jpeg', '.webp')))
        self.assertFalse(any('mail-out' in n for n in names))
        self.assertFalse(any(n.startswith('backups/') for n in names))
        self.assertIn('image files', manifest['excludes'])

        before_company_extra = (self.dst / 'images' / 'placement' / 'only-on-company.png').is_file()
        self.assertTrue(before_company_extra)
        pc_before = self.pc_config.read_text(encoding='utf-8')

        summary, _ = migration.inspect_zip(zip_path)
        self.assertEqual(summary['keyCounts']['orders'], 1)
        self.assertIn('置き換', summary['warning'])

        result = migration.restore_from_zip(
            self.dst, zip_path,
            actor={'id': 1, 'name': '旧管理者', 'login': 'old@example.co.jp'},
            auto_backup_dir=self.root / 'auto',
        )
        self.assertTrue(result['ok'])
        self.assertTrue((self.root / 'auto').exists())
        self.assertTrue(any(p.name.startswith('pre-restore-') for p in (self.root / 'auto').glob('*.zip')))

        # PC-specific config untouched
        self.assertEqual(self.pc_config.read_text(encoding='utf-8'), pc_before)

        # Destination DB/counts match source; destination images are left as-is
        counts = migration.summarize_data_dir(self.dst)['counts']
        self.assertEqual(counts['orders'], 1)
        self.assertEqual(counts['users'], 1)
        self.assertEqual(counts['stockLots'], 1)
        self.assertTrue((self.dst / 'images' / 'placement' / 'only-on-company.png').is_file())
        self.assertTrue((self.dst / 'images' / 'placement' / 'sample.png').is_file())
        self.assertEqual(
            (self.dst / 'name-import' / 'catalog.json').read_text(encoding='utf-8'),
            '{"customerCount":1}',
        )

        # Password hash still verifies migrated account
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            row = c.execute("SELECT id, login, password FROM users WHERE role='owner'").fetchone()
            self.assertEqual(row[1], 'owner@example.co.jp')
            self.assertTrue(verify(self.password, row[2]))
            sessions = c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
            self.assertEqual(sessions, 0)
            audit = c.execute("SELECT action FROM audit ORDER BY id DESC LIMIT 1").fetchone()[0]
            self.assertIn('復元', audit)
        finally:
            c.close()

    def test_path_independence_relative_images(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'usb2.zip')
        # Restore into a differently named data directory (simulates company path).
        other = self.root / 'C_instruction-desk_data'
        other.mkdir()
        _init_instructions(other / 'instructions.sqlite3', login='x@example.co.jp', password='Password1234!!')
        migration.restore_from_zip(other, zip_path, auto_backup_dir=self.root / 'auto2')
        c = sqlite3.connect(str(other / 'instructions.sqlite3'))
        try:
            rel = c.execute('SELECT relative_path FROM placements').fetchone()[0]
            self.assertTrue(rel.startswith('images/'))
            self.assertFalse(rel.startswith('/'))
            self.assertNotIn(':', rel.split('/')[0])
            # Images are omitted from the ZIP; a fresh dest may not have the file.
            self.assertFalse((other / rel).exists())
        finally:
            c.close()

    def test_corrupt_zip_rejected_and_destination_intact(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'good.zip')
        bad = self.root / 'bad.zip'
        data = bytearray(zip_path.read_bytes())
        # Flip bytes in the middle to break CRC / content.
        mid = len(data) // 2
        data[mid] ^= 0xFF
        data[mid + 1] ^= 0xAA
        bad.write_bytes(data)
        marker = self.dst / 'images' / 'placement' / 'only-on-company.png'
        self.assertTrue(marker.is_file())
        before_hash = hashlib.sha256((self.dst / 'instructions.sqlite3').read_bytes()).hexdigest()
        with self.assertRaises(migration.MigrationError):
            migration.inspect_zip(bad)
        # Destination untouched by inspect failure
        self.assertEqual(hashlib.sha256((self.dst / 'instructions.sqlite3').read_bytes()).hexdigest(), before_hash)
        self.assertTrue(marker.is_file())

    def test_failed_restore_rolls_back(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'ok.zip')
        # Build a ZIP that passes extract but fails late: remove integrity by swapping DB after packing is hard.
        # Instead, patch validate to pass then force failure during install by making instructions unreadable mid-way
        # via a ZIP whose stock sha is wrong in manifest while file is present — inspect catches that.
        # Use a ZIP with traversal path which extract rejects; destination must stay intact.
        evil = self.root / 'evil.zip'
        with zipfile.ZipFile(evil, 'w') as zf:
            zf.writestr('../outside.txt', 'nope')
            zf.writestr('manifest.json', json.dumps({'formatVersion': 1}))
        marker = self.dst / 'images' / 'placement' / 'only-on-company.png'
        before = marker.read_bytes()
        with self.assertRaises(migration.MigrationError):
            migration.restore_from_zip(self.dst, evil, auto_backup_dir=self.root / 'auto3')
        self.assertEqual(marker.read_bytes(), before)

    def test_restore_failure_after_park_rolls_back(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'ok2.zip')
        marker = self.dst / 'images' / 'placement' / 'only-on-company.png'
        before_orders = migration.summarize_data_dir(self.dst)['counts']['orders']
        self.assertTrue(marker.is_file())

        real_replace = os.replace

        def flaky_replace(src, dst):
            src_p, dst_p = Path(src), Path(dst)
            # Fail while installing the incoming instructions DB (after parking live data).
            if dst_p.name == 'instructions.sqlite3' and 'incoming-' in str(src_p):
                raise OSError('simulated install failure')
            return real_replace(src, dst)

        os.replace = flaky_replace
        try:
            with self.assertRaises(migration.MigrationError):
                migration.restore_from_zip(self.dst, zip_path, auto_backup_dir=self.root / 'auto4')
        finally:
            os.replace = real_replace

        self.assertEqual(migration.summarize_data_dir(self.dst)['counts']['orders'], before_orders)
        self.assertTrue(marker.is_file())
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            login = c.execute("SELECT login FROM users WHERE role='owner'").fetchone()[0]
            self.assertEqual(login, 'old@example.co.jp')
        finally:
            c.close()

    def test_owner_guard(self):
        with self.assertRaises(migration.MigrationError):
            migration.owner_guard(None)
        with self.assertRaises(migration.MigrationError):
            migration.owner_guard({'role': 'sales'})
        with self.assertRaises(migration.MigrationError):
            migration.owner_guard({'role': 'floor', 'manage': 1})
        migration.owner_guard({'role': 'owner'})

    def test_export_closes_zip_before_testzip(self):
        events = []
        Real = zipfile.ZipFile

        class TrackingZip(Real):
            def __init__(self, file, mode='r', *a, **k):
                super().__init__(file, mode, *a, **k)
                events.append(('open', mode))

            def close(self):
                events.append(('close', self.mode))
                return super().close()

            def testzip(self):
                events.append(('testzip', self.mode))
                return super().testzip()

        dest = self.root / 'closed-then-tested.zip'
        with mock.patch.object(migration.zipfile, 'ZipFile', TrackingZip):
            zip_path, _ = migration.build_export_zip(self.src, dest)
        self.assertTrue(zip_path.is_file())
        self.assertFalse(Path(str(dest) + '.partial').exists())
        self.assertIn(('open', 'w'), events)
        self.assertIn(('close', 'w'), events)
        self.assertIn(('open', 'r'), events)
        self.assertIn(('testzip', 'r'), events)
        self.assertLess(events.index(('close', 'w')), events.index(('testzip', 'r')))
        with zipfile.ZipFile(zip_path, 'r') as zf:
            self.assertIsNone(zf.testzip())

    def test_export_testzip_failure_includes_member_name(self):
        Real = zipfile.ZipFile
        bad_member = 'instructions.sqlite3'

        class BadTestZip(Real):
            def testzip(self):
                if self.mode == 'r':
                    return bad_member
                return super().testzip()

        dest = self.root / 'bad-member.zip'
        with mock.patch.object(migration.zipfile, 'ZipFile', BadTestZip):
            with self.assertRaises(migration.MigrationError) as ctx:
                migration.build_export_zip(self.src, dest)
        msg = str(ctx.exception)
        self.assertIn('ZIPの作成に失敗しました', msg)
        self.assertIn(bad_member, msg)
        self.assertFalse(dest.exists())
        self.assertFalse(Path(str(dest) + '.partial').exists())

    def test_restore_preserves_dest_images_and_skips_legacy_zip_images(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'no-images.zip')
        with zipfile.ZipFile(zip_path, 'a') as zf:
            zf.writestr('images/placement/injected.png', b'injected-from-zip')
            zf.writestr('name-import/photo.jpg', b'jpeg-bytes')
        (self.dst / 'name-import' / 'catalog.json').write_text('{"old":true}', encoding='utf-8')
        dest_photo = self.dst / 'name-import' / 'skip-me.png'
        dest_photo_bytes = dest_photo.read_bytes()
        marker = self.dst / 'images' / 'placement' / 'only-on-company.png'
        marker_bytes = marker.read_bytes()

        result = migration.restore_from_zip(self.dst, zip_path, auto_backup_dir=self.root / 'auto5')
        self.assertTrue(result['ok'])
        self.assertEqual(marker.read_bytes(), marker_bytes)
        self.assertEqual(dest_photo.read_bytes(), dest_photo_bytes)
        self.assertFalse((self.dst / 'images' / 'placement' / 'injected.png').exists())
        self.assertFalse((self.dst / 'name-import' / 'photo.jpg').exists())
        self.assertEqual(
            (self.dst / 'name-import' / 'catalog.json').read_text(encoding='utf-8'),
            '{"customerCount":1}',
        )
        summary, _ = migration.inspect_zip(zip_path)
        self.assertIn('画像', summary['warning'])
        self.assertNotIn('画像も含みます', summary['warning'])


if __name__ == '__main__':
    unittest.main()
