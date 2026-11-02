"""メニュー別（分割）バックアップ・復元のテスト（一時フォルダのみ使用）。"""
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import migration
import split_backup as sb

SCHEMA = '''
CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, manage INTEGER NOT NULL DEFAULT 0, overview INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE login_gates (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE auth_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
CREATE TABLE audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor_id INTEGER, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE history (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL, actor_id INTEGER, actor TEXT, changes TEXT);
CREATE TABLE templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, folder TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL);
CREATE TABLE placements (id INTEGER PRIMARY KEY AUTOINCREMENT, customer TEXT NOT NULL DEFAULT '', created_by_id INTEGER, updated_by_id INTEGER);
CREATE TABLE kintai_people (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE mystery_table (id INTEGER PRIMARY KEY, v TEXT);
'''


def make_pc(root, users, *, orders=(), templates=(), placements=(), kintai=(), settings=()):
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(root / 'instructions.sqlite3'))
    c.executescript(SCHEMA)
    for uid, login, name, role in users:
        c.execute('INSERT INTO users(id,login,name,password,role) VALUES(?,?,?,?,?)', (uid, login, name, 'hash-' + login, role))
    c.execute("INSERT INTO sessions VALUES('tok',1,9e9)")
    for oid, payload in orders:
        c.execute('INSERT INTO orders(id,version,payload) VALUES(?,1,?)', (oid, json.dumps(payload, ensure_ascii=False)))
    for tid, uid, name in templates:
        c.execute("INSERT INTO templates VALUES(?,?,'f',?,1,'{}')", (tid, uid, name))
    for pid, cust, by in placements:
        c.execute('INSERT INTO placements(id,customer,created_by_id,updated_by_id) VALUES(?,?,?,?)', (pid, cust, by, by))
    for kid, name in kintai:
        c.execute('INSERT INTO kintai_people VALUES(?,?)', (kid, name))
    for k, v in settings:
        c.execute('INSERT INTO settings VALUES(?,?)', (k, v))
    c.commit(); c.close()
    s = sqlite3.connect(str(root / 'stock.sqlite3'))
    s.executescript("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL); INSERT INTO products(name) VALUES('home-item');")
    s.commit(); s.close()


def q(root, sql, db='instructions.sqlite3'):
    c = sqlite3.connect(str(root / db))
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


OWNER = {'id': 1, 'name': '主', 'login': 'owner@example.jp'}


class SplitBackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # 会社PC: owner=1, sato=2, kato=3
        self.company = self.tmp / 'company'
        make_pc(self.company,
                [(1, 'owner@example.jp', '主', 'owner'), (2, 'sato@example.jp', '佐藤', 'staff'), (3, 'kato@example.jp', '加藤', 'staff')],
                orders=[(10, {'number': 'A-1', 'assigneeId': 3, 'owner': '加藤(会社)', 'authorId': 2})],
                templates=[(5, 3, '会社ひな形')],
                placements=[(7, '会社顧客', 2)],
                kintai=[(1, '会社の人')],
                settings=[('seeded', 'company'), ('placement_import', 'company'), ('recovery', 'company')])
        (self.company / 'images' / 'placement').mkdir(parents=True)
        (self.company / 'images' / 'placement' / 'a.png').write_bytes(b'company')
        s = sqlite3.connect(str(self.company / 'stock.sqlite3'))
        s.execute("UPDATE products SET name='company-item'"); s.commit(); s.close()
        # 自宅PC: 同じ人が別ID（kato=2, sato=3）, 会社にいない guest=4
        self.home = self.tmp / 'home'
        make_pc(self.home,
                [(1, 'owner@example.jp', '主', 'owner'), (2, 'kato@example.jp', '加藤', 'staff'), (3, 'sato@example.jp', '佐藤', 'staff'), (4, 'guest@example.jp', 'ゲスト', 'staff')],
                orders=[(99, {'number': 'H-1'})],
                templates=[(8, 4, '自宅ひな形')],
                placements=[(9, '自宅顧客', 4)],
                kintai=[(1, '自宅の人')],
                settings=[('seeded', 'home'), ('placement_import', 'home'), ('recovery', 'home')])
        (self.home / 'images' / 'placement').mkdir(parents=True)
        (self.home / 'images' / 'placement' / 'b.png').write_bytes(b'home')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def roundtrip(self, cat):
        z, manifest = sb.export_category(cat, self.company, actor=OWNER)
        self.assertEqual(manifest['category'], cat)
        return sb.restore_category(z, self.home, actor=OWNER, expect_category=cat), z

    def test_classification(self):
        c = sqlite3.connect(str(self.home / 'instructions.sqlite3'))
        self.assertEqual(sb.category_tables(c, 'mark'), ['kintai_people'])
        self.assertIn('mystery_table', sb.category_tables(c, 'desk'))
        self.assertEqual(sb.unplaced_tables(c), ['mystery_table'])
        self.assertNotIn('sessions', sum((sb.category_tables(c, k) for k in sb.ORDER), []))
        c.close()

    def test_desk_restore_only_desk_and_remaps_users(self):
        r, z = self.roundtrip('desk')
        with zipfile.ZipFile(z) as zf:
            self.assertIn('data.sqlite3', zf.namelist())
        self.assertTrue((self.home / 'backups' / r['safetyBackup'][0]).exists())
        payload = json.loads(q(self.home, 'SELECT payload FROM orders')[0][0])
        self.assertEqual(q(self.home, 'SELECT id FROM orders'), [(10,)])
        self.assertEqual(payload['assigneeId'], 2)  # kato: 会社3 → 自宅2
        self.assertEqual(payload['owner'], '加藤')
        self.assertEqual(payload['authorId'], 3)  # sato: 会社2 → 自宅3
        self.assertEqual(q(self.home, 'SELECT id,user_id FROM templates'), [(5, 2)])
        # 他区分は変わらない
        self.assertEqual(q(self.home, 'SELECT customer,created_by_id FROM placements'), [('自宅顧客', 4)])
        self.assertEqual(q(self.home, 'SELECT name FROM kintai_people'), [('自宅の人',)])
        self.assertEqual(dict(q(self.home, 'SELECT key,value FROM settings')),
                         {'seeded': 'company', 'placement_import': 'home', 'recovery': 'home'})
        self.assertEqual(q(self.home, 'SELECT login FROM users ORDER BY id')[3], ('guest@example.jp',))

    def test_customer_restore_replaces_files(self):
        r, _ = self.roundtrip('customer')
        self.assertEqual(q(self.home, 'SELECT customer,created_by_id FROM placements'), [('会社顧客', 3)])
        self.assertTrue((self.home / 'images' / 'placement' / 'a.png').exists())
        self.assertFalse((self.home / 'images' / 'placement' / 'b.png').exists())
        self.assertTrue(any('files' in x for x in r['safetyBackup']))
        self.assertEqual(dict(q(self.home, 'SELECT key,value FROM settings'))['placement_import'], 'company')
        self.assertEqual(q(self.home, 'SELECT payload FROM orders')[0][0], json.dumps({'number': 'H-1'}))

    def test_stock_and_mark(self):
        self.roundtrip('stock')
        self.assertEqual(q(self.home, 'SELECT name FROM products', 'stock.sqlite3'), [('company-item',)])
        self.roundtrip('mark')
        self.assertEqual(q(self.home, 'SELECT name FROM kintai_people'), [('会社の人',)])

    def test_users_restore_remaps_other_categories(self):
        self.roundtrip('users')
        self.assertEqual(q(self.home, 'SELECT id,login FROM users ORDER BY id'),
                         [(1, 'owner@example.jp'), (2, 'sato@example.jp'), (3, 'kato@example.jp')])
        # 自宅ひな形の所有者 guest(4) は会社に無い → 復元した主管理者(1)へ
        self.assertEqual(q(self.home, 'SELECT user_id FROM templates'), [(1,)])
        self.assertEqual(q(self.home, 'SELECT created_by_id FROM placements'), [(1,)])
        self.assertEqual(q(self.home, 'SELECT count(*) FROM sessions'), [(0,)])
        self.assertEqual(dict(q(self.home, 'SELECT key,value FROM settings'))['recovery'], 'company')

    def test_schema_mismatch_aborts_without_change(self):
        c = sqlite3.connect(str(self.company / 'instructions.sqlite3'))
        c.execute('ALTER TABLE templates ADD COLUMN newer TEXT'); c.commit(); c.close()
        z, _ = sb.export_category('desk', self.company, actor=OWNER)
        with self.assertRaises(sb.SplitBackupError) as e:
            sb.restore_category(z, self.home, actor=OWNER)
        self.assertIn('newer', str(e.exception))
        self.assertEqual(q(self.home, 'SELECT id FROM orders'), [(99,)])
        self.assertFalse(migration.is_busy())

    def test_wrong_category_rejected(self):
        z, _ = sb.export_category('mark', self.company, actor=OWNER)
        with self.assertRaises(sb.SplitBackupError):
            sb.restore_category(z, self.home, actor=OWNER, expect_category='desk')
        self.assertEqual(q(self.home, 'SELECT name FROM kintai_people'), [('自宅の人',)])

    def test_users_without_owner_rolls_back(self):
        c = sqlite3.connect(str(self.company / 'instructions.sqlite3'))
        c.execute("UPDATE users SET active=0 WHERE role='owner'"); c.commit(); c.close()
        z, _ = sb.export_category('users', self.company, actor=OWNER)
        with self.assertRaises(sb.SplitBackupError):
            sb.restore_category(z, self.home, actor=OWNER)
        self.assertEqual(len(q(self.home, 'SELECT id FROM users')), 4)


if __name__ == '__main__':
    unittest.main()
