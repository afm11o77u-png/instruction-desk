"""あすかHOUSE東苗穂：Excel 行11–13 の部署・役職リストを詳細画面用に登録。"""
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


def _connect(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, payload TEXT NOT NULL DEFAULT "{}")')
    return c


def _add_customer(c, key, name, *, retired=0, deleted_at=None):
    cid = c.execute(
        'INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)'
        ' VALUES(?,?,?,?,?,?,?)',
        (key, 1, name, '', name, '対応', retired),
    ).lastrowid
    if deleted_at is not None:
        c.execute(
            'UPDATE name_customers SET deleted_at=?, deleted_by_name=? WHERE id=?',
            (deleted_at, 'test', cid),
        )
    return cid


def _add_format(c, customer_id, identity, format_name, **kwargs):
    return c.execute(
        'INSERT INTO name_formats('
        'identity, customer_id, source_kind, format_name, placement, family, heading, note,'
        'layout, reading, small, width_mm, height_mm, fixed_color, color_caption,'
        'groups_json, colors_json, swatches_json, departments_json, maximum_font, heading_font,'
        'spec_json, retired'
        ') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
        (
            identity, customer_id, 1, format_name,
            kwargs.get('placement', 'スクラブ：左胸　ケーシー：左胸　長白衣：左胸'),
            kwargs.get('family', 'HG丸ゴシック'),
            kwargs.get('heading', '上段：部署名　中段：ふりがな　下段：苗字'),
            kwargs.get('note', '上段のみ左にインデント'),
            kwargs.get('layout', 'DeptFurigana'), kwargs.get('reading', '苗字のみ'),
            kwargs.get('small', 0), kwargs.get('width', 80), kwargs.get('height', 50),
            kwargs.get('fixed', '白'), kwargs.get('color_caption', ''),
            json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps(kwargs.get('departments') or [], ensure_ascii=False),
            kwargs.get('maximum_font', 50),
            kwargs.get('heading_font', 0),
            json.dumps(kwargs.get('spec') or {}, ensure_ascii=False),
        ),
    ).lastrowid


def _add_sample(c, format_id, *, key='standard', paper='白', path='images/names/keep-me.png', caption='既存見本'):
    return c.execute(
        'INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)'
        ' VALUES(?,?,?,?,?,?)',
        (format_id, key, '', paper, path, caption),
    ).lastrowid


class AsukaHouseHigashiNaeboDeptsTest(unittest.TestCase):
    def test_excel_lists_are_independent(self):
        self.assertEqual(
            names.ASUKA_HOUSE_HIGASHI_NAEBO_DEPARTMENTS,
            ['介護', 'デイケア', '看護', 'リハビリ', '栄養課', '医師'],
        )
        self.assertEqual(
            names.ASUKA_HOUSE_HIGASHI_NAEBO_ROLES,
            ['リーダー', '主任', '課長', '施設長'],
        )
        self.assertEqual(names.ASUKA_HOUSE_HIGASHI_NAEBO_POLICY, 'excel-gojuon2-r11-13-depts-roles-v1')

    def test_updates_target_only_and_keeps_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / 'images' / 'names').mkdir(parents=True)
            img = data_dir / 'images' / 'names' / 'keep-me.png'
            img.write_bytes(b'PNG-FAKE')
            db = data_dir / 'instructions.sqlite3'
            c = _connect(db)
            try:
                names.bind(data_dir)
                # schema の ensure 群は走らせず、表だけ用意して対象 ensure を直接呼ぶ
                c.execute('''CREATE TABLE IF NOT EXISTS name_customers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL UNIQUE,
                    source_index INTEGER NOT NULL DEFAULT 0,
                    source_name TEXT NOT NULL,
                    reading TEXT NOT NULL DEFAULT '',
                    linked_name TEXT NOT NULL DEFAULT '',
                    link_status TEXT NOT NULL DEFAULT '未対応',
                    manual INTEGER NOT NULL DEFAULT 0,
                    retired INTEGER NOT NULL DEFAULT 0
                )''')
                c.execute('''CREATE TABLE IF NOT EXISTS name_formats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity TEXT NOT NULL UNIQUE,
                    customer_id INTEGER NOT NULL,
                    source_kind INTEGER NOT NULL,
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
                    retired INTEGER NOT NULL DEFAULT 0
                )''')
                c.execute('''CREATE TABLE IF NOT EXISTS name_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    format_id INTEGER NOT NULL,
                    variant_key TEXT NOT NULL,
                    condition_label TEXT NOT NULL DEFAULT '',
                    paper_color TEXT NOT NULL DEFAULT '',
                    relative_path TEXT NOT NULL DEFAULT '',
                    caption TEXT NOT NULL DEFAULT '',
                    UNIQUE(format_id, variant_key)
                )''')
                names._ensure_soft_delete_columns(c)
                names._column(c, 'name_formats', 'roles_json', "roles_json TEXT NOT NULL DEFAULT 'null'")

                target = _add_customer(c, 'excel:asuka-hn', names.ASUKA_HOUSE_HIGASHI_NAEBO_CUSTOMER_NAME)
                house = _add_customer(c, 'excel:asuka-house', 'あすかHOUSE', deleted_at='2026-01-01T00:00:00')
                bakery = _add_customer(c, 'excel:asuka-bakery', 'あすかベーカリー')
                ashuka = _add_customer(c, 'excel:ashuka', '札幌/桜台/白石/岩見沢/宮の沢明日佳')

                fid = _add_format(c, target, 'asuka-hn:large', '大ネーム（部署・ふりがな・苗字）')
                _add_sample(c, fid, path='images/names/keep-me.png', caption='既存見本を残す')
                house_fid = _add_format(
                    c, house, 'asuka-house:large', '大ネーム',
                    departments=['触らない部署'],
                )
                _add_sample(c, house_fid, path='', caption='HOUSE見本')
                bakery_fid = _add_format(c, bakery, 'asuka-bakery:small', '小ネーム')
                ashuka_fid = _add_format(
                    c, ashuka, 'ashuka:three', '大ネーム',
                    departments=list(names.ASHUKA_DEPARTMENTS),
                )

                before_house = c.execute(
                    'SELECT departments_json, roles_json, placement, family, note FROM name_formats WHERE id=?',
                    (house_fid,),
                ).fetchone()
                before_bakery = c.execute(
                    'SELECT departments_json, roles_json FROM name_formats WHERE id=?',
                    (bakery_fid,),
                ).fetchone()
                before_ashuka = c.execute(
                    'SELECT departments_json, roles_json FROM name_formats WHERE id=?',
                    (ashuka_fid,),
                ).fetchone()
                before_sample = c.execute(
                    'SELECT relative_path, caption, paper_color FROM name_samples WHERE format_id=?',
                    (fid,),
                ).fetchone()
                before_meta = c.execute(
                    'SELECT placement, family, heading, note, maximum_font, fixed_color FROM name_formats WHERE id=?',
                    (fid,),
                ).fetchone()

                report = names.ensure_asuka_house_higashi_naebo_depts(c, data_dir)
                self.assertGreaterEqual(report['changed'], 1)
                self.assertTrue(report['exclusionsUntouched'])
                self.assertEqual(report['displayMode'], 'separate-lists')
                self.assertEqual(report['departments'], names.ASUKA_HOUSE_HIGASHI_NAEBO_DEPARTMENTS)
                self.assertEqual(report['roles'], names.ASUKA_HOUSE_HIGASHI_NAEBO_ROLES)
                self.assertTrue(report.get('backupDir'))
                backup = data_dir / report['backupDir']
                self.assertTrue(backup.is_dir())
                self.assertTrue((backup / f'format-{fid}.json').is_file())

                row = c.execute(
                    'SELECT departments_json, roles_json, placement, family, heading, note,'
                    ' maximum_font, fixed_color FROM name_formats WHERE id=?',
                    (fid,),
                ).fetchone()
                self.assertEqual(json.loads(row['departments_json']), names.ASUKA_HOUSE_HIGASHI_NAEBO_DEPARTMENTS)
                self.assertEqual(json.loads(row['roles_json']), names.ASUKA_HOUSE_HIGASHI_NAEBO_ROLES)
                self.assertEqual(row['placement'], before_meta['placement'])
                self.assertEqual(row['family'], before_meta['family'])
                self.assertEqual(row['heading'], before_meta['heading'])
                self.assertEqual(row['note'], before_meta['note'])
                self.assertEqual(row['maximum_font'], before_meta['maximum_font'])
                self.assertEqual(row['fixed_color'], before_meta['fixed_color'])

                sample = c.execute(
                    'SELECT relative_path, caption, paper_color FROM name_samples WHERE format_id=?',
                    (fid,),
                ).fetchone()
                self.assertEqual(dict(sample), dict(before_sample))
                self.assertTrue(img.is_file())
                self.assertEqual(img.read_bytes(), b'PNG-FAKE')

                after_house = c.execute(
                    'SELECT departments_json, roles_json, placement, family, note FROM name_formats WHERE id=?',
                    (house_fid,),
                ).fetchone()
                self.assertEqual(dict(after_house), dict(before_house))
                after_bakery = c.execute(
                    'SELECT departments_json, roles_json FROM name_formats WHERE id=?',
                    (bakery_fid,),
                ).fetchone()
                self.assertEqual(dict(after_bakery), dict(before_bakery))
                after_ashuka = c.execute(
                    'SELECT departments_json, roles_json FROM name_formats WHERE id=?',
                    (ashuka_fid,),
                ).fetchone()
                self.assertEqual(dict(after_ashuka), dict(before_ashuka))

                again = names.ensure_asuka_house_higashi_naebo_depts(c, data_dir)
                self.assertEqual(again['changed'], 0)
                self.assertTrue(again.get('skipped'))
            finally:
                c.close()

    def test_skips_when_customer_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'instructions.sqlite3'
            c = _connect(db)
            try:
                names.bind(data_dir)
                c.execute('''CREATE TABLE IF NOT EXISTS name_customers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL UNIQUE,
                    source_index INTEGER NOT NULL DEFAULT 0,
                    source_name TEXT NOT NULL,
                    reading TEXT NOT NULL DEFAULT '',
                    linked_name TEXT NOT NULL DEFAULT '',
                    link_status TEXT NOT NULL DEFAULT '未対応',
                    manual INTEGER NOT NULL DEFAULT 0,
                    retired INTEGER NOT NULL DEFAULT 0
                )''')
                c.execute('''CREATE TABLE IF NOT EXISTS name_formats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity TEXT NOT NULL UNIQUE,
                    customer_id INTEGER NOT NULL,
                    source_kind INTEGER NOT NULL,
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
                    retired INTEGER NOT NULL DEFAULT 0
                )''')
                report = names.ensure_asuka_house_higashi_naebo_depts(c, data_dir)
                self.assertTrue(report.get('skipped'))
                self.assertEqual(report.get('reason'), 'あすかHOUSE東苗穂顧客なし')
            finally:
                c.close()


if __name__ == '__main__':
    unittest.main()
