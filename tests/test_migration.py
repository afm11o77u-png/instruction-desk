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


def _init_instructions(
    db_path,
    *,
    login='owner@example.co.jp',
    password='Password1234!!',
    name='主管理者',
    role='owner',
    manage=1,
    extra_users=None,
    order_assignee_id=None,
    with_history=False,
):
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
            CREATE TABLE shipments (id INTEGER PRIMARY KEY, created_by_id INTEGER, updated_by_id INTEGER);
            CREATE TABLE name_customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL UNIQUE,
                source_index INTEGER NOT NULL DEFAULT 0,
                source_name TEXT NOT NULL,
                reading TEXT NOT NULL DEFAULT '',
                linked_name TEXT NOT NULL DEFAULT '',
                link_status TEXT NOT NULL DEFAULT '未対応',
                manual INTEGER NOT NULL DEFAULT 0,
                retired INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE name_formats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identity TEXT NOT NULL UNIQUE,
                customer_id INTEGER NOT NULL,
                source_kind INTEGER NOT NULL DEFAULT 0,
                format_name TEXT NOT NULL,
                placement TEXT NOT NULL DEFAULT '',
                family TEXT NOT NULL DEFAULT '',
                header_family TEXT NOT NULL DEFAULT '',
                heading TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                layout TEXT NOT NULL DEFAULT '',
                reading TEXT NOT NULL DEFAULT '',
                small INTEGER NOT NULL DEFAULT 0,
                width_mm INTEGER NOT NULL DEFAULT 0,
                height_mm INTEGER NOT NULL DEFAULT 0,
                unknown_size INTEGER NOT NULL DEFAULT 0,
                fixed_color TEXT NOT NULL DEFAULT '',
                color_caption TEXT NOT NULL DEFAULT '',
                groups_json TEXT NOT NULL DEFAULT '[]',
                colors_json TEXT NOT NULL DEFAULT '[]',
                swatches_json TEXT NOT NULL DEFAULT '[]',
                departments_json TEXT NOT NULL DEFAULT 'null',
                pending TEXT NOT NULL DEFAULT '',
                maximum_font REAL NOT NULL DEFAULT 0,
                heading_font REAL NOT NULL DEFAULT 0,
                number_length INTEGER NOT NULL DEFAULT 0,
                source_row INTEGER,
                previous_source_row INTEGER,
                spec_json TEXT NOT NULL DEFAULT '{}',
                needs_review INTEGER NOT NULL DEFAULT 0,
                review_json TEXT NOT NULL DEFAULT '[]',
                blank INTEGER NOT NULL DEFAULT 0,
                manual INTEGER NOT NULL DEFAULT 0,
                retired INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE name_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                format_id INTEGER NOT NULL,
                variant_key TEXT NOT NULL,
                condition_label TEXT NOT NULL DEFAULT '',
                paper_color TEXT NOT NULL DEFAULT '',
                relative_path TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                UNIQUE(format_id, variant_key)
            );
            CREATE TABLE name_reviews (
                format_id INTEGER PRIMARY KEY,
                assignee_id INTEGER,
                source_label TEXT NOT NULL DEFAULT '',
                confirmed_by INTEGER,
                confirmed_name TEXT,
                confirmed_at TEXT,
                confirmed_digest TEXT
            );
            CREATE TABLE name_review_history (
                id INTEGER PRIMARY KEY,
                format_id INTEGER,
                actor_id INTEGER,
                actor_name TEXT,
                action TEXT,
                at TEXT,
                digest TEXT
            );
            CREATE TABLE label_sets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE user_display_modes (user_id INTEGER NOT NULL, screen_id TEXT NOT NULL, mode TEXT NOT NULL, PRIMARY KEY (user_id, screen_id));
        ''')
        hashed = password_hash(password)
        uid = c.execute(
            "INSERT INTO users(login,name,password,role,manage) VALUES(?,?,?,?,?)",
            (login, name, hashed, role, manage),
        ).lastrowid
        assignee = order_assignee_id if order_assignee_id is not None else uid
        c.execute(
            "INSERT INTO orders(version,payload) VALUES(1,?)",
            (json.dumps({
                'customer': '移行テスト顧客',
                'status': '未着手',
                'owner': name,
                'assigneeId': assignee,
                'authorId': uid,
                'authorName': name,
            }, ensure_ascii=False),),
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
            "INSERT INTO placements(original_name,relative_path,thumb_path,customer,part,created_at,updated_at,created_by_id,updated_by_id,version) VALUES(?,?,?,?,?,?,?,?,?,1)",
            ('sample.png', 'images/placement/sample.png', 'images/placement/thumbs/sample.jpg', '顧客A', '胸', '2026-01-01', '2026-01-01', uid, uid),
        )
        c.execute(
            "INSERT INTO label_sets(user_id,name,version,payload) VALUES(?,?,1,?)",
            (uid, '宛名A', json.dumps({'lines': ['A']}, ensure_ascii=False)),
        )
        c.execute(
            "INSERT INTO user_display_modes(user_id,screen_id,mode) VALUES(?,?,?)",
            (uid, 'orders', 'list'),
        )
        c.execute("INSERT INTO sessions VALUES('tok', ?, 9999999999)", (uid,))
        c.execute("INSERT INTO settings VALUES('seeded','1')")
        c.execute("INSERT INTO settings VALUES('recovery','dest-or-src-recovery-hash')")
        if with_history:
            c.execute(
                "INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(1,'2026-01-01','作成',?,?,'{}')",
                (uid, f'{name} ({login})'),
            )
        if extra_users:
            for eu in extra_users:
                euid = c.execute(
                    "INSERT INTO users(login,name,password,role,manage,active) VALUES(?,?,?,?,?,?)",
                    (
                        eu['login'], eu['name'], password_hash(eu.get('password', 'Password1234!!')),
                        eu.get('role', 'sales'), eu.get('manage', 0), eu.get('active', 1),
                    ),
                ).lastrowid
                eu['id'] = euid
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


def _dest_user_id(data_dir, login):
    c = sqlite3.connect(str(Path(data_dir) / 'instructions.sqlite3'))
    try:
        return c.execute('SELECT id FROM users WHERE login=?', (login,)).fetchone()[0]
    finally:
        c.close()


def _add_name_format(
    db_path,
    *,
    identity,
    customer_name='顧客A',
    customer_key=None,
    format_name='形式A',
    note='',
    sample_path='images/names/a.png',
    sample_caption='見本',
    assignee_id=None,
    confirmed=False,
    confirmed_at='2026-03-01T10:00:00',
    history=None,
    force_format_id=None,
):
    """Insert a name format (+ optional approval) into a throwaway instructions DB."""
    import name_review

    c = sqlite3.connect(str(db_path))
    try:
        c.row_factory = sqlite3.Row
        key = customer_key or f'cust:{customer_name}'
        row = c.execute('SELECT id FROM name_customers WHERE source_key=?', (key,)).fetchone()
        if row:
            customer_id = row['id']
        else:
            customer_id = c.execute(
                '''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)
                   VALUES(?,?,?,?,?,?,0)''',
                (key, 1, customer_name, '', customer_name, '対応'),
            ).lastrowid
        if force_format_id is not None:
            c.execute(
                '''INSERT INTO name_formats(
                    id, identity, customer_id, source_kind, format_name, placement, note, retired
                   ) VALUES(?,?,?,?,?,?,?,0)''',
                (force_format_id, identity, customer_id, 1, format_name, '胸', note),
            )
            format_id = force_format_id
        else:
            format_id = c.execute(
                '''INSERT INTO name_formats(
                    identity, customer_id, source_kind, format_name, placement, note, retired
                   ) VALUES(?,?,?,?,?,?,0)''',
                (identity, customer_id, 1, format_name, '胸', note),
            ).lastrowid
        c.execute(
            '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
               VALUES(?,?,?,?,?,?)''',
            (format_id, 'standard', '', '', sample_path, sample_caption),
        )
        token = name_review.digest(c, format_id)
        if assignee_id is not None or confirmed:
            conf_by = assignee_id if confirmed else None
            conf_name = None
            conf_at = None
            conf_digest = None
            if confirmed:
                conf_name = c.execute('SELECT name FROM users WHERE id=?', (assignee_id,)).fetchone()[0]
                conf_at = confirmed_at
                conf_digest = token
            c.execute(
                '''INSERT INTO name_reviews(
                    format_id, assignee_id, source_label, confirmed_by, confirmed_name, confirmed_at, confirmed_digest
                   ) VALUES(?,?,?,?,?,?,?)''',
                (format_id, assignee_id, '', conf_by, conf_name, conf_at, conf_digest),
            )
        for item in history or []:
            c.execute(
                '''INSERT INTO name_review_history(format_id, actor_id, actor_name, action, at, digest)
                   VALUES(?,?,?,?,?,?)''',
                (
                    format_id,
                    item.get('actor_id'),
                    item.get('actor_name', ''),
                    item.get('action', '内容を確認'),
                    item.get('at', confirmed_at),
                    item.get('digest', token),
                ),
            )
        c.commit()
        return format_id, token
    finally:
        c.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='migtest-')
        self.root = Path(self._tmp.name)
        self.src = self.root / 'home-pc'
        self.dst = self.root / 'company-pc'
        self.src.mkdir()
        self.dst.mkdir()
        self.password = 'Password1234!!'
        self.uid, self.hash = _init_instructions(
            self.src / 'instructions.sqlite3',
            password=self.password,
            with_history=True,
        )
        _init_stock(self.src / 'stock.sqlite3')
        _seed_files(self.src)
        # Destination has different business data + different auth.
        self.dst_password = 'OldPassword1234!!'
        self.dst_uid, self.dst_hash = _init_instructions(
            self.dst / 'instructions.sqlite3',
            login='old@example.co.jp',
            password=self.dst_password,
            name='旧管理者',
        )
        _init_stock(self.dst / 'stock.sqlite3')
        _seed_files(self.dst)
        (self.dst / 'images' / 'placement' / 'only-on-company.png').write_bytes(b'company-only')
        # Distinct company display mode + recovery
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            c.execute("UPDATE settings SET value='company-recovery' WHERE key='recovery'")
            c.execute(
                "UPDATE user_display_modes SET mode='grid' WHERE user_id=? AND screen_id='orders'",
                (self.dst_uid,),
            )
            c.commit()
        finally:
            c.close()
        self.pc_config = self.root / 'company-network.json'
        self.pc_config.write_text(json.dumps({'port': 8765, 'bind': '127.0.0.1', 'addresses': ['10.0.0.8']}), encoding='utf-8')

    def tearDown(self):
        self._tmp.cleanup()

    def _map_src_to_dst(self):
        return {self.uid: self.dst_uid}

    def test_export_scrubs_password_hashes(self):
        zip_path, manifest = migration.build_export_zip(self.src, self.root / 'usb' / 'desk-migration-test.zip')
        self.assertTrue(zip_path.is_file())
        self.assertEqual(manifest['formatVersion'], migration.FORMAT_VERSION)
        self.assertTrue(manifest['authSecretsExcluded'])
        self.assertEqual(len(manifest['sourceUsers']), 1)
        self.assertNotIn('password', manifest['sourceUsers'][0])
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        self.assertIn('instructions.sqlite3', names)
        # Staged DB inside ZIP must not carry verifiable password material.
        with zipfile.ZipFile(zip_path) as zf:
            zf.extract('instructions.sqlite3', self.root / 'extracted')
        c = sqlite3.connect(str(self.root / 'extracted' / 'instructions.sqlite3'))
        try:
            pw = c.execute('SELECT password FROM users').fetchone()[0]
            self.assertEqual(pw, migration.EXCLUDED_PASSWORD)
            gate = c.execute('SELECT gate FROM users').fetchone()[0]
            self.assertIsNone(gate)
            recovery = c.execute("SELECT value FROM settings WHERE key='recovery'").fetchone()
            self.assertIsNone(recovery)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
        finally:
            c.close()

    def test_export_restore_preserves_dest_auth_and_remaps(self):
        zip_path, manifest = migration.build_export_zip(self.src, self.root / 'usb' / 'desk-migration-test.zip')
        self.assertEqual(manifest['keyCounts']['orders'], 1)
        self.assertIn('image files', manifest['excludes'])

        before_company_extra = (self.dst / 'images' / 'placement' / 'only-on-company.png').is_file()
        self.assertTrue(before_company_extra)
        pc_before = self.pc_config.read_text(encoding='utf-8')

        summary, _ = migration.inspect_zip(zip_path, self.dst)
        self.assertEqual(summary['keyCounts']['orders'], 1)
        self.assertTrue(summary['preservesDestinationUsers'])
        self.assertEqual(len(summary['sourceUsers']), 1)
        self.assertEqual(len(summary['destinationUsers']), 1)
        self.assertIn('利用者アカウント', summary['warning'])

        result = migration.restore_from_zip(
            self.dst, zip_path,
            actor={'id': self.dst_uid, 'name': '旧管理者', 'login': 'old@example.co.jp'},
            auto_backup_dir=self.root / 'auto',
            user_map=self._map_src_to_dst(),
        )
        self.assertTrue(result['ok'])
        self.assertTrue(result['preservesDestinationUsers'])
        self.assertTrue(any(p.name.startswith('pre-restore-') for p in (self.root / 'auto').glob('*.zip')))

        self.assertEqual(self.pc_config.read_text(encoding='utf-8'), pc_before)

        counts = migration.summarize_data_dir(self.dst)['counts']
        self.assertEqual(counts['orders'], 1)
        self.assertEqual(counts['users'], 1)
        self.assertEqual(counts['stockLots'], 1)
        self.assertTrue((self.dst / 'images' / 'placement' / 'only-on-company.png').is_file())
        self.assertEqual(
            (self.dst / 'name-import' / 'catalog.json').read_text(encoding='utf-8'),
            '{"customerCount":1}',
        )

        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            row = c.execute("SELECT id, login, password, name, role FROM users WHERE role='owner'").fetchone()
            self.assertEqual(row[0], self.dst_uid)
            self.assertEqual(row[1], 'old@example.co.jp')
            self.assertTrue(verify(self.dst_password, row[2]))
            self.assertEqual(row[3], '旧管理者')
            # Source login must NOT appear
            self.assertIsNone(c.execute("SELECT id FROM users WHERE login='owner@example.co.jp'").fetchone())
            sessions = c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
            self.assertEqual(sessions, 0)
            recovery = c.execute("SELECT value FROM settings WHERE key='recovery'").fetchone()[0]
            self.assertEqual(recovery, 'company-recovery')
            mode = c.execute(
                "SELECT mode FROM user_display_modes WHERE user_id=? AND screen_id='orders'",
                (self.dst_uid,),
            ).fetchone()[0]
            self.assertEqual(mode, 'grid')
            payload = json.loads(c.execute('SELECT payload FROM orders').fetchone()[0])
            self.assertEqual(payload['assigneeId'], self.dst_uid)
            self.assertEqual(payload['authorId'], self.dst_uid)
            self.assertEqual(payload['owner'], '旧管理者')
            tmpl_uid = c.execute('SELECT user_id FROM templates').fetchone()[0]
            self.assertEqual(tmpl_uid, self.dst_uid)
            creator = c.execute('SELECT creator FROM picking_lists').fetchone()[0]
            self.assertEqual(creator, self.dst_uid)
            hist = c.execute('SELECT actor_id, actor FROM history').fetchone()
            self.assertIsNone(hist[0])
            self.assertTrue(str(hist[1]).startswith('移行元の記録'))
            audit = c.execute("SELECT action FROM audit ORDER BY id DESC LIMIT 1").fetchone()[0]
            self.assertIn('利用者保持', audit)
            self.assertIn('ネーム情報', audit)
        finally:
            c.close()

    def test_same_numeric_id_not_auto_bound(self):
        """Source id=1 and dest id=1 are different people; mapping is required and applied."""
        # Rebuild dest with a sales user id that will collide numerically if we blindly kept source ids.
        shutil.rmtree(self.dst)
        self.dst.mkdir()
        sales = {'login': 'sales@example.co.jp', 'name': '会社営業', 'role': 'sales'}
        self.dst_uid, self.dst_hash = _init_instructions(
            self.dst / 'instructions.sqlite3',
            login='company@example.co.jp',
            password=self.dst_password,
            name='会社管理者',
            extra_users=[sales],
        )
        _init_stock(self.dst / 'stock.sqlite3')
        _seed_files(self.dst)
        sales_id = sales['id']
        self.assertEqual(self.uid, 1)
        self.assertEqual(self.dst_uid, 1)
        self.assertEqual(sales_id, 2)

        zip_path, _ = migration.build_export_zip(self.src, self.root / 'map.zip')
        # Explicitly map source owner(1) → company sales(2), not dest owner(1).
        migration.restore_from_zip(
            self.dst, zip_path,
            auto_backup_dir=self.root / 'auto-map',
            user_map={1: sales_id},
        )
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            logins = {r[0] for r in c.execute('SELECT login FROM users')}
            self.assertEqual(logins, {'company@example.co.jp', 'sales@example.co.jp'})
            payload = json.loads(c.execute('SELECT payload FROM orders').fetchone()[0])
            self.assertEqual(payload['assigneeId'], sales_id)
            self.assertEqual(payload['owner'], '会社営業')
            self.assertEqual(c.execute('SELECT user_id FROM templates').fetchone()[0], sales_id)
            owner_pw = c.execute("SELECT password FROM users WHERE login='company@example.co.jp'").fetchone()[0]
            self.assertTrue(verify(self.dst_password, owner_pw))
        finally:
            c.close()

    def test_restore_requires_user_map(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'need-map.zip')
        before = hashlib.sha256((self.dst / 'instructions.sqlite3').read_bytes()).hexdigest()
        with self.assertRaises(migration.MigrationError) as ctx:
            migration.restore_from_zip(self.dst, zip_path, auto_backup_dir=self.root / 'auto-nomap')
        self.assertIn('割り当て', str(ctx.exception))
        self.assertEqual(hashlib.sha256((self.dst / 'instructions.sqlite3').read_bytes()).hexdigest(), before)

    def test_path_independence_relative_images(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'usb2.zip')
        other = self.root / 'C_instruction_desk_data'
        other.mkdir()
        other_uid, _ = _init_instructions(other / 'instructions.sqlite3', login='x@example.co.jp', password='Password1234!!')
        migration.restore_from_zip(
            other, zip_path,
            auto_backup_dir=self.root / 'auto2',
            user_map={self.uid: other_uid},
        )
        c = sqlite3.connect(str(other / 'instructions.sqlite3'))
        try:
            rel = c.execute('SELECT relative_path FROM placements').fetchone()[0]
            self.assertTrue(rel.startswith('images/'))
            self.assertFalse(rel.startswith('/'))
            self.assertNotIn(':', rel.split('/')[0])
            self.assertFalse((other / rel).exists())
            login = c.execute('SELECT login FROM users').fetchone()[0]
            self.assertEqual(login, 'x@example.co.jp')
        finally:
            c.close()

    def test_corrupt_zip_rejected_and_destination_intact(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'good.zip')
        bad = self.root / 'bad.zip'
        data = bytearray(zip_path.read_bytes())
        mid = len(data) // 2
        data[mid] ^= 0xFF
        data[mid + 1] ^= 0xAA
        bad.write_bytes(data)
        marker = self.dst / 'images' / 'placement' / 'only-on-company.png'
        self.assertTrue(marker.is_file())
        before_hash = hashlib.sha256((self.dst / 'instructions.sqlite3').read_bytes()).hexdigest()
        with self.assertRaises(migration.MigrationError):
            migration.inspect_zip(bad, self.dst)
        self.assertEqual(hashlib.sha256((self.dst / 'instructions.sqlite3').read_bytes()).hexdigest(), before_hash)
        self.assertTrue(marker.is_file())

    def test_failed_restore_rolls_back(self):
        evil = self.root / 'evil.zip'
        with zipfile.ZipFile(evil, 'w') as zf:
            zf.writestr('../outside.txt', 'nope')
            zf.writestr('manifest.json', json.dumps({'formatVersion': 2}))
        marker = self.dst / 'images' / 'placement' / 'only-on-company.png'
        before = marker.read_bytes()
        with self.assertRaises(migration.MigrationError):
            migration.restore_from_zip(
                self.dst, evil,
                auto_backup_dir=self.root / 'auto3',
                user_map=self._map_src_to_dst(),
            )
        self.assertEqual(marker.read_bytes(), before)

    def test_restore_failure_after_park_rolls_back(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'ok2.zip')
        marker = self.dst / 'images' / 'placement' / 'only-on-company.png'
        before_orders = migration.summarize_data_dir(self.dst)['counts']['orders']
        self.assertTrue(marker.is_file())

        real_replace = os.replace

        def flaky_replace(src, dst):
            src_p, dst_p = Path(src), Path(dst)
            if dst_p.name == 'instructions.sqlite3' and 'incoming-' in str(src_p):
                raise OSError('simulated install failure')
            return real_replace(src, dst)

        os.replace = flaky_replace
        try:
            with self.assertRaises(migration.MigrationError):
                migration.restore_from_zip(
                    self.dst, zip_path,
                    auto_backup_dir=self.root / 'auto4',
                    user_map=self._map_src_to_dst(),
                )
        finally:
            os.replace = real_replace

        self.assertEqual(migration.summarize_data_dir(self.dst)['counts']['orders'], before_orders)
        self.assertTrue(marker.is_file())
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            login = c.execute("SELECT login FROM users WHERE role='owner'").fetchone()[0]
            self.assertEqual(login, 'old@example.co.jp')
            self.assertTrue(verify(self.dst_password, c.execute("SELECT password FROM users WHERE role='owner'").fetchone()[0]))
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

        result = migration.restore_from_zip(
            self.dst, zip_path,
            auto_backup_dir=self.root / 'auto5',
            user_map=self._map_src_to_dst(),
        )
        self.assertTrue(result['ok'])
        self.assertEqual(marker.read_bytes(), marker_bytes)
        self.assertEqual(dest_photo.read_bytes(), dest_photo_bytes)
        self.assertFalse((self.dst / 'images' / 'placement' / 'injected.png').exists())
        self.assertFalse((self.dst / 'name-import' / 'photo.jpg').exists())
        self.assertEqual(
            (self.dst / 'name-import' / 'catalog.json').read_text(encoding='utf-8'),
            '{"customerCount":1}',
        )
        summary, _ = migration.inspect_zip(zip_path, self.dst)
        self.assertIn('画像', summary['warning'])
        self.assertNotIn('画像も含みます', summary['warning'])

    def test_legacy_format_v1_accepted_without_applying_passwords(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'legacy-base.zip', scrub_auth=False)
        # Downgrade manifest to v1 and keep password hashes (simulates old USB ZIP).
        work = self.root / 'legacy-work'
        work.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work)
        manifest = json.loads((work / 'manifest.json').read_text(encoding='utf-8'))
        manifest['formatVersion'] = 1
        manifest.pop('authSecretsExcluded', None)
        # Recompute sha after we leave passwords in DB (already unscrubbed).
        manifest['databases']['instructions']['sha256'] = migration._sha256_file(work / 'instructions.sqlite3')
        body = json.dumps({k: v for k, v in manifest.items() if k != 'integrity'}, ensure_ascii=False, indent=2, sort_keys=True).encode()
        manifest['integrity'] = {
            'manifestBodySha256': migration._sha256_bytes(body),
            'instructionsSha256': manifest['databases']['instructions']['sha256'],
            'stockSha256': manifest['databases']['stock']['sha256'],
            'filesSha256': migration._sha256_bytes(
                '\n'.join(f"{e['sha256']}  {e['path']}" for e in manifest['files']).encode()
            ),
        }
        (work / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        legacy = self.root / 'legacy-v1.zip'
        with zipfile.ZipFile(legacy, 'w') as zf:
            for path in work.rglob('*'):
                if path.is_file():
                    zf.write(path, path.relative_to(work).as_posix())

        summary, _ = migration.inspect_zip(legacy, self.dst)
        self.assertTrue(summary['legacyFormat'] or summary['legacyAuthPresent'])
        self.assertIn('旧形式', summary['warning'])

        migration.restore_from_zip(
            self.dst, legacy,
            auto_backup_dir=self.root / 'auto-legacy',
            user_map=self._map_src_to_dst(),
        )
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            login, pw = c.execute("SELECT login, password FROM users WHERE role='owner'").fetchone()
            self.assertEqual(login, 'old@example.co.jp')
            self.assertTrue(verify(self.dst_password, pw))
            self.assertIsNone(c.execute("SELECT id FROM users WHERE login='owner@example.co.jp'").fetchone())
        finally:
            c.close()

    def test_unsupported_format_rejected_before_changes(self):
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'base.zip')
        work = self.root / 'badver'
        work.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work)
        manifest = json.loads((work / 'manifest.json').read_text(encoding='utf-8'))
        manifest['formatVersion'] = 99
        (work / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        bad = self.root / 'badver.zip'
        with zipfile.ZipFile(bad, 'w') as zf:
            for path in work.rglob('*'):
                if path.is_file():
                    zf.write(path, path.relative_to(work).as_posix())
        before = hashlib.sha256((self.dst / 'instructions.sqlite3').read_bytes()).hexdigest()
        with self.assertRaises(migration.MigrationError) as ctx:
            migration.inspect_zip(bad, self.dst)
        self.assertIn('形式', str(ctx.exception))
        self.assertEqual(hashlib.sha256((self.dst / 'instructions.sqlite3').read_bytes()).hexdigest(), before)

    def test_name_data_protected_when_destination_has_names(self):
        """Destination with any name rows keeps full name domain; ZIP names are ignored."""
        import name_review

        src_identity = 'fmt:only-src'
        dst_identity = 'fmt:company-only'
        _add_name_format(
            self.src / 'instructions.sqlite3',
            identity=src_identity,
            customer_name='開発顧客',
            format_name='開発形式',
            note='ZIP側',
            sample_path='images/names/src.png',
            assignee_id=self.uid,
            confirmed=True,
            force_format_id=10,
            history=[{'actor_id': self.uid, 'actor_name': '主管理者', 'at': '2026-01-01T09:00:00'}],
        )
        _add_name_format(
            self.dst / 'instructions.sqlite3',
            identity=dst_identity,
            customer_name='会社顧客',
            format_name='会社形式',
            note='会社側の原文',
            sample_path='images/names/dst.png',
            sample_caption='会社見本',
            assignee_id=self.dst_uid,
            confirmed=True,
            confirmed_at='2026-02-15T12:00:00',
            force_format_id=99,
            history=[
                {'actor_id': self.dst_uid, 'actor_name': '旧管理者', 'action': '内容を確認', 'at': '2026-02-15T12:00:00'},
            ],
        )
        # Destination-only name_import setting should also survive.
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            c.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES('name_import',?)",
                (json.dumps({'customerCount': 1, 'source': 'company'}, ensure_ascii=False),),
            )
            c.commit()
        finally:
            c.close()

        zip_path, _ = migration.build_export_zip(self.src, self.root / 'protect.zip')
        summary, _ = migration.inspect_zip(zip_path, self.dst, user_map=self._map_src_to_dst())
        self.assertTrue(summary['preservesDestinationNames'])
        self.assertTrue(summary['nameProtection']['protected'])
        self.assertIn('ネーム情報', summary['warning'])
        self.assertIn('そのまま残し', summary['warning'])
        self.assertTrue(any('ネーム情報' in x for x in summary['preserves']))
        self.assertFalse(any(x.startswith('ネーム情報（このパソコンに未登録') for x in summary['replaces']))

        result = migration.restore_from_zip(
            self.dst, zip_path,
            auto_backup_dir=self.root / 'auto-protect',
            user_map=self._map_src_to_dst(),
        )
        self.assertTrue(result['preservesDestinationNames'])
        self.assertTrue(result['nameProtection']['protected'])
        self.assertTrue(any(p.name.startswith('pre-restore-') for p in (self.root / 'auto-protect').glob('*.zip')))

        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            c.row_factory = sqlite3.Row
            identities = [r[0] for r in c.execute('SELECT identity FROM name_formats ORDER BY id')]
            self.assertEqual(identities, [dst_identity])
            self.assertIsNone(
                c.execute("SELECT id FROM name_formats WHERE identity=?", (src_identity,)).fetchone()
            )
            fid = c.execute(
                "SELECT id FROM name_formats WHERE identity=?", (dst_identity,)
            ).fetchone()['id']
            self.assertEqual(fid, 99)
            fmt = c.execute('SELECT format_name, note FROM name_formats WHERE id=?', (fid,)).fetchone()
            self.assertEqual(fmt['format_name'], '会社形式')
            self.assertEqual(fmt['note'], '会社側の原文')
            cust = c.execute(
                'SELECT source_name FROM name_customers WHERE id=('
                'SELECT customer_id FROM name_formats WHERE id=?)',
                (fid,),
            ).fetchone()['source_name']
            self.assertEqual(cust, '会社顧客')
            sample = c.execute(
                'SELECT relative_path, caption FROM name_samples WHERE format_id=?', (fid,)
            ).fetchone()
            self.assertEqual(sample['relative_path'], 'images/names/dst.png')
            self.assertEqual(sample['caption'], '会社見本')
            review = c.execute('SELECT * FROM name_reviews WHERE format_id=?', (fid,)).fetchone()
            self.assertEqual(review['assignee_id'], self.dst_uid)
            self.assertEqual(review['confirmed_at'], '2026-02-15T12:00:00')
            self.assertEqual(review['confirmed_by'], self.dst_uid)
            token = name_review.digest(c, fid)
            self.assertEqual(review['confirmed_digest'], token)
            status = name_review.state(c, fid, {'id': self.dst_uid, 'active': 1, 'must_change': 0})
            self.assertEqual(status['status'], '了承')
            hist = c.execute(
                'SELECT actor_id, actor_name, at FROM name_review_history WHERE format_id=? ORDER BY id',
                (fid,),
            ).fetchall()
            self.assertEqual(len(hist), 1)
            self.assertEqual(hist[0]['actor_id'], self.dst_uid)
            self.assertEqual(hist[0]['at'], '2026-02-15T12:00:00')
            name_import = json.loads(
                c.execute("SELECT value FROM settings WHERE key='name_import'").fetchone()[0]
            )
            self.assertEqual(name_import['source'], 'company')
            # Orders/business data still came from ZIP.
            self.assertEqual(c.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 1)
            login, pw = c.execute("SELECT login, password FROM users WHERE role='owner'").fetchone()
            self.assertEqual(login, 'old@example.co.jp')
            self.assertTrue(verify(self.dst_password, pw))
        finally:
            c.close()

    def test_name_data_from_zip_when_destination_empty(self):
        """Empty destination receives ZIP name tables; assignees remapped; approvals kept if digest valid."""
        import name_review

        identity = 'fmt:from-zip'
        _add_name_format(
            self.src / 'instructions.sqlite3',
            identity=identity,
            customer_name='ZIP顧客',
            format_name='ZIP形式',
            note='空先への取り込み',
            sample_path='images/names/zip.png',
            assignee_id=self.uid,
            confirmed=True,
            confirmed_at='2026-01-10T10:00:00',
            force_format_id=22,
            history=[{'actor_id': self.uid, 'actor_name': '主管理者', 'at': '2026-01-10T10:00:00'}],
        )
        # Destination name tables remain empty (setUp default).
        self.assertFalse(migration.destination_has_name_data(self.dst / 'instructions.sqlite3'))

        zip_path, _ = migration.build_export_zip(self.src, self.root / 'empty-dest.zip')
        summary, _ = migration.inspect_zip(zip_path, self.dst, user_map=self._map_src_to_dst())
        self.assertFalse(summary['nameProtection']['protected'])
        self.assertIn('ネーム情報がない', summary['warning'])
        self.assertTrue(any('ネーム情報（このパソコンに未登録' in x for x in summary['replaces']))

        result = migration.restore_from_zip(
            self.dst, zip_path,
            auto_backup_dir=self.root / 'auto-empty',
            user_map=self._map_src_to_dst(),
        )
        self.assertFalse(result['nameProtection']['protected'])
        self.assertEqual(result['nameProtection']['mode'], 'applyZip')

        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            c.row_factory = sqlite3.Row
            fid = c.execute(
                "SELECT id FROM name_formats WHERE identity=?", (identity,)
            ).fetchone()['id']
            self.assertEqual(fid, 22)
            review = c.execute('SELECT * FROM name_reviews WHERE format_id=?', (fid,)).fetchone()
            self.assertEqual(review['assignee_id'], self.dst_uid)
            self.assertEqual(review['confirmed_by'], self.dst_uid)
            self.assertEqual(review['confirmed_at'], '2026-01-10T10:00:00')
            token = name_review.digest(c, fid)
            self.assertEqual(review['confirmed_digest'], token)
            status = name_review.state(c, fid, {'id': self.dst_uid, 'active': 1, 'must_change': 0})
            self.assertEqual(status['status'], '了承')
            hist = c.execute(
                'SELECT actor_id, actor_name FROM name_review_history WHERE format_id=?',
                (fid,),
            ).fetchone()
            # ZIP history actors are unbound and annotated as 移行元の記録.
            self.assertIsNone(hist['actor_id'])
            self.assertIn('移行元の記録', hist['actor_name'])
        finally:
            c.close()

    def test_name_protection_ignores_conflicting_zip_content(self):
        """Same identity in ZIP with different content must not overwrite destination."""
        identity = 'fmt:same-id'
        _add_name_format(
            self.src / 'instructions.sqlite3',
            identity=identity,
            format_name='開発の改訂版',
            note='ZIPの新しい文面',
            sample_path='images/names/new.png',
            assignee_id=self.uid,
            confirmed=True,
            force_format_id=11,
        )
        _add_name_format(
            self.dst / 'instructions.sqlite3',
            identity=identity,
            format_name='会社の原文',
            note='会社側の文面',
            sample_path='images/names/old.png',
            assignee_id=self.dst_uid,
            confirmed=True,
            confirmed_at='2026-02-20T08:00:00',
            force_format_id=55,
            history=[{'actor_id': self.dst_uid, 'actor_name': '旧管理者', 'at': '2026-02-20T08:00:00'}],
        )
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'conflict.zip')
        migration.restore_from_zip(
            self.dst, zip_path,
            auto_backup_dir=self.root / 'auto-conflict',
            user_map=self._map_src_to_dst(),
        )
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT id, format_name, note FROM name_formats WHERE identity=?",
                (identity,),
            ).fetchone()
            self.assertEqual(row['id'], 55)
            self.assertEqual(row['format_name'], '会社の原文')
            self.assertEqual(row['note'], '会社側の文面')
            sample = c.execute(
                'SELECT relative_path FROM name_samples WHERE format_id=?', (row['id'],)
            ).fetchone()['relative_path']
            self.assertEqual(sample, 'images/names/old.png')
        finally:
            c.close()

    def test_name_protection_legacy_zip_same_rule(self):
        identity = 'fmt:legacy-keep'
        _add_name_format(
            self.src / 'instructions.sqlite3',
            identity=identity,
            format_name='旧ZIP形式',
            note='ZIP文面',
            assignee_id=self.uid,
            confirmed=True,
            force_format_id=30,
        )
        _add_name_format(
            self.dst / 'instructions.sqlite3',
            identity=identity,
            format_name='会社原文',
            note='会社文面',
            assignee_id=self.dst_uid,
            confirmed=True,
            confirmed_at='2026-02-25T08:00:00',
            force_format_id=80,
        )
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'legacy-name-base.zip', scrub_auth=False)
        work = self.root / 'legacy-name-work'
        work.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work)
        manifest = json.loads((work / 'manifest.json').read_text(encoding='utf-8'))
        manifest['formatVersion'] = 1
        manifest.pop('authSecretsExcluded', None)
        manifest['databases']['instructions']['sha256'] = migration._sha256_file(work / 'instructions.sqlite3')
        body = json.dumps({k: v for k, v in manifest.items() if k != 'integrity'}, ensure_ascii=False, indent=2, sort_keys=True).encode()
        manifest['integrity'] = {
            'manifestBodySha256': migration._sha256_bytes(body),
            'instructionsSha256': manifest['databases']['instructions']['sha256'],
            'stockSha256': manifest['databases']['stock']['sha256'],
            'filesSha256': migration._sha256_bytes(
                '\n'.join(f"{e['sha256']}  {e['path']}" for e in manifest['files']).encode()
            ),
        }
        (work / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        legacy = self.root / 'legacy-name-v1.zip'
        with zipfile.ZipFile(legacy, 'w') as zf:
            for path in work.rglob('*'):
                if path.is_file():
                    zf.write(path, path.relative_to(work).as_posix())

        summary, _ = migration.inspect_zip(legacy, self.dst, user_map=self._map_src_to_dst())
        self.assertIn('ネーム情報', summary['warning'])
        self.assertTrue(summary['nameProtection']['protected'])

        migration.restore_from_zip(
            self.dst, legacy,
            auto_backup_dir=self.root / 'auto-legacy-name',
            user_map=self._map_src_to_dst(),
        )
        import name_review
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT id, format_name, note FROM name_formats WHERE identity=?",
                (identity,),
            ).fetchone()
            self.assertEqual(row['id'], 80)
            self.assertEqual(row['format_name'], '会社原文')
            review = c.execute('SELECT * FROM name_reviews WHERE format_id=?', (row['id'],)).fetchone()
            self.assertEqual(review['assignee_id'], self.dst_uid)
            self.assertEqual(review['confirmed_at'], '2026-02-25T08:00:00')
            status = name_review.state(c, row['id'], {'id': self.dst_uid, 'active': 1, 'must_change': 0})
            self.assertEqual(status['status'], '了承')
        finally:
            c.close()

    def test_name_protection_uses_latest_dest_snapshot_at_apply(self):
        """Name edits after inspect preview must still be preserved at restore."""
        identity = 'fmt:revalidate'
        _add_name_format(
            self.src / 'instructions.sqlite3',
            identity=identity,
            format_name='ZIP版',
            note='ZIP',
            assignee_id=self.uid,
            confirmed=True,
            force_format_id=31,
        )
        _add_name_format(
            self.dst / 'instructions.sqlite3',
            identity=identity,
            format_name='会社版',
            note='プレビュー時点',
            assignee_id=self.dst_uid,
            confirmed=False,
            force_format_id=81,
        )
        zip_path, _ = migration.build_export_zip(self.src, self.root / 'reval.zip')
        summary, _ = migration.inspect_zip(zip_path, self.dst, user_map=self._map_src_to_dst())
        self.assertTrue(summary['nameProtection']['protected'])

        # Company edits / confirms after preview.
        import name_review
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            c.row_factory = sqlite3.Row
            fid = 81
            c.execute("UPDATE name_formats SET note=? WHERE id=?", ('プレビュー後の修正', fid))
            token = name_review.digest(c, fid)
            c.execute(
                'UPDATE name_reviews SET confirmed_by=?, confirmed_name=?, confirmed_at=?, confirmed_digest=? '
                'WHERE format_id=?',
                (self.dst_uid, '旧管理者', '2026-03-01T18:00:00', token, fid),
            )
            c.execute(
                'INSERT INTO name_review_history(format_id, actor_id, actor_name, action, at, digest) '
                'VALUES(?,?,?,?,?,?)',
                (fid, self.dst_uid, '旧管理者', '内容を確認', '2026-03-01T18:00:00', token),
            )
            c.commit()
        finally:
            c.close()

        result = migration.restore_from_zip(
            self.dst, zip_path,
            auto_backup_dir=self.root / 'auto-reval',
            user_map=self._map_src_to_dst(),
        )
        self.assertTrue(result['nameProtection']['protected'])
        c = sqlite3.connect(str(self.dst / 'instructions.sqlite3'))
        try:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT id, note FROM name_formats WHERE identity=?", (identity,)
            ).fetchone()
            self.assertEqual(row['id'], 81)
            self.assertEqual(row['note'], 'プレビュー後の修正')
            review = c.execute('SELECT * FROM name_reviews WHERE format_id=?', (row['id'],)).fetchone()
            self.assertEqual(review['confirmed_at'], '2026-03-01T18:00:00')
            status = name_review.state(c, row['id'], {'id': self.dst_uid, 'active': 1, 'must_change': 0})
            self.assertEqual(status['status'], '了承')
        finally:
            c.close()


class MatchUsersByLoginTests(unittest.TestCase):
    """Unit tests for restore-screen bulk login matching (no ZIP restore)."""

    def test_login_match_case_insensitive_unique(self):
        source = [
            {'id': 1, 'login': 'Alice@Example.co.jp', 'name': '源アリス', 'active': 1},
            {'id': 2, 'login': 'bob@example.co.jp', 'name': '源ボブ', 'active': 1},
        ]
        dest = [
            {'id': 101, 'login': 'alice@example.co.jp', 'name': '先アリス', 'active': 1},
            {'id': 102, 'login': 'bob@example.co.jp', 'name': '先ボブ', 'active': 1},
            {'id': 1, 'login': 'other@example.co.jp', 'name': '同番号別人', 'active': 1},
        ]
        report = migration.match_users_by_login(source, dest)
        self.assertEqual(report['matches'], {1: 101, 2: 102})
        self.assertEqual(report['matchedCount'], 2)
        self.assertEqual(report['unmatchedCount'], 0)

    def test_name_only_or_blank_login_requires_manual(self):
        source = [
            {'id': 10, 'login': '', 'name': '現場太郎', 'nameOnly': True, 'active': 1},
            {'id': 11, 'login': '', 'name': '空白花子', 'active': 1},
        ]
        dest = [
            {'id': 201, 'login': '', 'name': '現場太郎', 'nameOnly': True, 'active': 1},
            {'id': 202, 'login': 'hana@example.co.jp', 'name': '空白花子', 'active': 1},
        ]
        report = migration.match_users_by_login(source, dest)
        self.assertEqual(report['matches'], {})
        self.assertEqual(report['unmatchedCount'], 2)
        reasons = {item['sourceId']: item['reason'] for item in report['unmatched']}
        self.assertEqual(reasons[10], 'ログインIDが空欄です')
        self.assertEqual(reasons[11], 'ログインIDが空欄です')

    def test_name_same_but_login_different_does_not_match(self):
        source = [{'id': 3, 'login': 'old@example.co.jp', 'name': '同一氏名', 'active': 1}]
        dest = [{'id': 303, 'login': 'new@example.co.jp', 'name': '同一氏名', 'active': 1}]
        report = migration.match_users_by_login(source, dest)
        self.assertEqual(report['matches'], {})
        self.assertEqual(report['unmatched'][0]['reason'], 'ログインIDが一致する利用者がいません')

    def test_same_numeric_id_alone_does_not_match(self):
        source = [{'id': 7, 'login': 'zip@example.co.jp', 'name': 'ZIP側', 'active': 1}]
        dest = [{'id': 7, 'login': 'pc@example.co.jp', 'name': 'PC側', 'active': 1}]
        report = migration.match_users_by_login(source, dest)
        self.assertEqual(report['matches'], {})
        self.assertEqual(report['unmatched'][0]['reason'], 'ログインIDが一致する利用者がいません')

    def test_inactive_destination_not_auto_selected(self):
        source = [{'id': 4, 'login': 'stop@example.co.jp', 'name': '停止元', 'active': 1}]
        dest = [{'id': 404, 'login': 'stop@example.co.jp', 'name': '停止先', 'active': 0}]
        report = migration.match_users_by_login(source, dest)
        self.assertEqual(report['matches'], {})
        self.assertIn('停止中', report['unmatched'][0]['reason'])

    def test_no_login_match_requires_manual(self):
        source = [{'id': 5, 'login': 'missing@example.co.jp', 'name': '欠席', 'active': 1}]
        dest = [{'id': 505, 'login': 'present@example.co.jp', 'name': '出席', 'active': 1}]
        report = migration.match_users_by_login(source, dest)
        self.assertEqual(report['matchedCount'], 0)
        self.assertEqual(report['unmatched'][0]['reason'], 'ログインIDが一致する利用者がいません')

    def test_mixed_match_and_unmatched_counts(self):
        source = [
            {'id': 1, 'login': 'ok@example.co.jp', 'name': '一致', 'active': 1},
            {'id': 2, 'login': '', 'name': '氏名のみ', 'nameOnly': True, 'active': 1},
            {'id': 3, 'login': 'gone@example.co.jp', 'name': '不一致', 'active': 1},
        ]
        dest = [
            {'id': 100, 'login': 'OK@example.co.jp', 'name': 'PC一致', 'active': 1},
            {'id': 101, 'login': 'other@example.co.jp', 'name': '氏名のみ', 'active': 1},
        ]
        report = migration.match_users_by_login(source, dest)
        self.assertEqual(report['matches'], {1: 100})
        self.assertEqual(report['matchedCount'], 1)
        self.assertEqual(report['unmatchedCount'], 2)
        by_id = {item['sourceId']: item['reason'] for item in report['unmatched']}
        self.assertEqual(by_id[2], 'ログインIDが空欄です')
        self.assertEqual(by_id[3], 'ログインIDが一致する利用者がいません')

    def test_repair_ui_exposes_bulk_same_user_controls(self):
        repair_js = (ROOT / 'repair.js').read_text(encoding='utf-8')
        repair_css = (ROOT / 'repair.css').read_text(encoding='utf-8')
        self.assertIn('同じ利用者に一括で割り当てる', repair_js)
        self.assertIn('mig-match-login', repair_js)
        self.assertIn('migrationApplyLoginMatch', repair_js)
        self.assertIn('ZIP内の利用者', repair_js)
        self.assertIn('このPCの利用者', repair_js)
        self.assertIn('選んだ1人に全員の割り当て先を変更', repair_js)
        self.assertIn('loginMatch', (ROOT / 'migration.py').read_text(encoding='utf-8'))
        self.assertIn('mig-map-unmatched', repair_css)


if __name__ == '__main__':
    unittest.main()
