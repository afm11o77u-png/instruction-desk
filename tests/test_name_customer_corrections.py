"""ネーム情報の顧客名表記ゆれ（日綜中垣→日総、明日香→明日佳 など）。"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import names

ASHUKA_WRONG = '札幌、白石、岩見沢、宮の森明日香'
ASHUKA_SLASH_WRONG = '札幌/白石/岩見沢/宮の森明日佳'
ASHUKA_RIGHT = '札幌/桜台/白石/岩見沢/宮の沢明日佳'
ASHUKA_READING_WRONG = 'さっぽろしろいいわみざわみやのもりあすか'
ASHUKA_READING_RIGHT = 'さっぽろさくらだいしろいいわみざわみやのさわあすか'


def _connect(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, payload TEXT NOT NULL DEFAULT "{}")')
    return c


class NameCustomerCorrectionsTest(unittest.TestCase):
    def test_correct_maps_nissou_typo(self):
        self.assertEqual(names._correct_customer_name('日綜中垣'), '日総')
        self.assertEqual(names._correct_customer_name('日総'), '日総')
        self.assertEqual(names._correct_customer_name('他社'), '他社')

    def test_correct_maps_ashuka_name(self):
        self.assertEqual(names._correct_customer_name(ASHUKA_WRONG), ASHUKA_RIGHT)
        self.assertEqual(names._correct_customer_name(ASHUKA_SLASH_WRONG), ASHUKA_RIGHT)
        self.assertEqual(names._correct_customer_name(ASHUKA_RIGHT), ASHUKA_RIGHT)

    def test_correct_maps_ashuka_reading(self):
        self.assertEqual(
            names._correct_customer_reading(ASHUKA_SLASH_WRONG, ASHUKA_READING_WRONG),
            ASHUKA_READING_RIGHT,
        )
        self.assertEqual(
            names._correct_customer_reading(ASHUKA_WRONG, 'あすか'),
            ASHUKA_READING_RIGHT,
        )
        self.assertEqual(names._correct_customer_reading('他社', 'たしゃ'), 'たしゃ')

    def test_schema_rewrites_existing_name_customer(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'desk.sqlite3'
            with _connect(db) as c:
                names.bind(tmp)
                names.schema(c)
                c.execute(
                    '''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)
                       VALUES(?,?,?,?,?,?,0)''',
                    ('key-1', 1, '日綜中垣', 'にっそう', '日綜中垣', '対応'),
                )
                names.schema(c)
                row = c.execute('SELECT source_name, linked_name FROM name_customers WHERE source_key=?', ('key-1',)).fetchone()
                self.assertEqual(row['source_name'], '日総')
                self.assertEqual(row['linked_name'], '日総')

    def test_schema_rewrites_existing_ashuka_customer(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'desk.sqlite3'
            with _connect(db) as c:
                names.bind(tmp)
                names.schema(c)
                c.execute(
                    '''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)
                       VALUES(?,?,?,?,?,?,0)''',
                    ('key-ashuka', 2, ASHUKA_WRONG, 'あすか', ASHUKA_WRONG, '対応'),
                )
                names.schema(c)
                row = c.execute(
                    'SELECT source_name, linked_name, reading FROM name_customers WHERE source_key=?',
                    ('key-ashuka',),
                ).fetchone()
                self.assertEqual(row['source_name'], ASHUKA_RIGHT)
                self.assertEqual(row['linked_name'], ASHUKA_RIGHT)
                self.assertEqual(row['reading'], ASHUKA_READING_RIGHT)

    def test_schema_rewrites_slash_ashuka_customer(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'desk.sqlite3'
            with _connect(db) as c:
                names.bind(tmp)
                names.schema(c)
                c.execute(
                    '''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)
                       VALUES(?,?,?,?,?,?,0)''',
                    ('key-ashuka-slash', 3, ASHUKA_SLASH_WRONG, ASHUKA_READING_WRONG, ASHUKA_SLASH_WRONG, '対応'),
                )
                names.schema(c)
                row = c.execute(
                    'SELECT source_name, linked_name, reading FROM name_customers WHERE source_key=?',
                    ('key-ashuka-slash',),
                ).fetchone()
                self.assertEqual(row['source_name'], ASHUKA_RIGHT)
                self.assertEqual(row['linked_name'], ASHUKA_RIGHT)
                self.assertEqual(row['reading'], ASHUKA_READING_RIGHT)

    def test_import_catalog_applies_correction(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            import_dir = data_dir / 'name-import'
            import_dir.mkdir()
            catalog = {
                'customerCount': 1,
                'formatCount': 0,
                'imageCount': 0,
                'reviewCount': 0,
                'pendingCount': 0,
                'blankCount': 0,
                'customers': [{
                    'sourceKey': 'cust-a',
                    'sourceIndex': 1,
                    'name': '日綜中垣',
                    'reading': 'にっそう',
                }],
                'formats': [],
            }
            (import_dir / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
            db = data_dir / 'desk.sqlite3'

            def connect():
                return _connect(db)

            names.import_catalog(connect, data_dir)
            with connect() as c:
                row = c.execute('SELECT source_name FROM name_customers WHERE source_key=?', ('cust-a',)).fetchone()
                self.assertEqual(row['source_name'], '日総')

    def test_import_catalog_applies_ashuka_correction(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            import_dir = data_dir / 'name-import'
            import_dir.mkdir()
            catalog = {
                'customerCount': 1,
                'formatCount': 0,
                'imageCount': 0,
                'reviewCount': 0,
                'pendingCount': 0,
                'blankCount': 0,
                'customers': [{
                    'sourceKey': 'cust-ashuka',
                    'sourceIndex': 2,
                    'name': ASHUKA_WRONG,
                    'reading': 'あすか',
                }],
                'formats': [],
            }
            (import_dir / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
            db = data_dir / 'desk.sqlite3'

            def connect():
                return _connect(db)

            names.import_catalog(connect, data_dir)
            with connect() as c:
                row = c.execute(
                    'SELECT source_name, reading FROM name_customers WHERE source_key=?',
                    ('cust-ashuka',),
                ).fetchone()
                self.assertEqual(row['source_name'], ASHUKA_RIGHT)
                self.assertEqual(row['reading'], ASHUKA_READING_RIGHT)

    def test_import_catalog_applies_slash_ashuka_correction(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            import_dir = data_dir / 'name-import'
            import_dir.mkdir()
            catalog = {
                'customerCount': 1,
                'formatCount': 0,
                'imageCount': 0,
                'reviewCount': 0,
                'pendingCount': 0,
                'blankCount': 0,
                'customers': [{
                    'sourceKey': 'cust-ashuka-slash',
                    'sourceIndex': 3,
                    'name': ASHUKA_SLASH_WRONG,
                    'reading': ASHUKA_READING_WRONG,
                }],
                'formats': [],
            }
            (import_dir / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
            db = data_dir / 'desk.sqlite3'

            def connect():
                return _connect(db)

            names.import_catalog(connect, data_dir)
            with connect() as c:
                row = c.execute(
                    'SELECT source_name, reading FROM name_customers WHERE source_key=?',
                    ('cust-ashuka-slash',),
                ).fetchone()
                self.assertEqual(row['source_name'], ASHUKA_RIGHT)
                self.assertEqual(row['reading'], ASHUKA_READING_RIGHT)


if __name__ == '__main__':
    unittest.main()
