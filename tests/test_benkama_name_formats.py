"""弁釜：Excel 行159–160 のネーム形式（社員／パート色・期生色表）。"""
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


def _add_customer(c, key, name):
    return c.execute(
        '''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)
           VALUES(?,?,?,?,?,?,0)''',
        (key, 1, name, '', name, '対応'),
    ).lastrowid


def _add_format(c, customer_id, identity, format_name, **kwargs):
    return c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, family, heading, note,
            layout, reading, small, width_mm, height_mm, fixed_color, color_caption,
            groups_json, colors_json, swatches_json, maximum_font, heading_font, number_length,
            source_row, spec_json, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
        (
            identity, customer_id, 1, format_name,
            kwargs.get('placement', ''), kwargs.get('family', ''),
            kwargs.get('heading', ''), kwargs.get('note', ''),
            kwargs.get('layout', 'Name'), kwargs.get('reading', 'フルネーム'),
            kwargs.get('small', 0), kwargs.get('width', 80), kwargs.get('height', 50),
            kwargs.get('fixed', '白'), kwargs.get('color_caption', '白'),
            json.dumps(kwargs.get('groups') or [], ensure_ascii=False),
            json.dumps(kwargs.get('colors') or [], ensure_ascii=False),
            json.dumps(kwargs.get('swatches') or [], ensure_ascii=False),
            kwargs.get('maximum_font', 0),
            kwargs.get('heading_font', 0),
            kwargs.get('number_length', 0),
            kwargs.get('source_row', 0),
            json.dumps(kwargs.get('spec') or {}, ensure_ascii=False),
        ),
    ).lastrowid


class BenkamaNameFormatsTest(unittest.TestCase):
    def test_format_defs_match_excel_rows_159_160(self):
        defs = {d['identity']: d for d in names._benkama_format_defs()}
        staff = defs['benkama:large-staff']
        period = defs['benkama:large-period']

        self.assertEqual(staff['sourceRow'], 159)
        self.assertEqual(staff['placement'], '白衣：左袖25cm')
        self.assertEqual(staff['formatName'], '大ネーム（社員・パート色）')
        self.assertEqual(staff['colorCaption'], '社員：青　パート：白')
        self.assertEqual(staff['groups'], ['社員', 'パート'])
        self.assertEqual(staff['colors'], ['青', '白'])
        self.assertEqual(staff['swatches'], ['#87CEFA', '#FFFFFF'])
        self.assertEqual(staff['papers'], ('青', '白'))
        self.assertEqual(staff['reading'], 'フルネーム')
        self.assertEqual(staff['layout'], 'HeadingName')
        self.assertEqual(staff['heading'], '上段：社員番号：〇〇〇〇〇〇〇')
        self.assertEqual(staff['headingFont'], 20)
        self.assertEqual(staff['numberLength'], 7)
        self.assertEqual(staff['widthMm'], 80)
        self.assertEqual(staff['heightMm'], 50)
        self.assertEqual(staff['sampleText'], '山田 太郎')
        self.assertIn('社員番号7ケタ', staff['note'])

        self.assertEqual(period['sourceRow'], 160)
        self.assertEqual(period['formatName'], '大ネーム（期生色表）')
        self.assertIn('札幌　1期生：赤', period['colorCaption'])
        self.assertIn('旭川　1期生：赤', period['colorCaption'])
        self.assertIn('10期生：白', period['colorCaption'])
        self.assertIn('函館　1～4期生：無し', period['colorCaption'])
        self.assertIn('八軒　1期生：赤', period['colorCaption'])
        self.assertIn('（札幌のみ、他は規則性なし？）', period['note'])
        self.assertIn('カタカナ', period['note'])
        self.assertEqual(period['sampleText'], '1 ﾃｷ ｼﾎｳ')
        self.assertEqual(period['papers'], ('赤', '緑', '青', '白'))
        self.assertIn('札幌1期生', period['groups'])
        self.assertIn('旭川10期生', period['groups'])
        self.assertIn('函館5期生', period['groups'])
        self.assertIn('八軒1期生', period['groups'])
        self.assertNotIn('函館1期生', period['groups'])  # 無しは除外

        for fmt in (staff, period):
            spec = names._benkama_spec(fmt)
            self.assertEqual(spec['benkamaPolicy'], names.BENKAMA_NAMES_POLICY)
            self.assertEqual(spec['columns'], 2)
            self.assertEqual(spec['numberLength'], 7)

    def test_creates_benkama_and_leaves_others(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow未導入')

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.DATA_DIR = None
                names.schema(c)
                names.bind(data_dir)

                other = _add_customer(c, 'other', '他社サンプル')
                other_fmt = _add_format(
                    c, other, 'id-other', '大ネーム',
                    placement='他社位置', family='HG丸ゴシック', note='他社の備考',
                    small=0, width=80, height=50, fixed='白',
                )
                other_note = c.execute(
                    'SELECT note FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone()['note']
                other_snap = dict(c.execute(
                    'SELECT * FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone())

                report = names.ensure_benkama_name_formats(c, data_dir)
                self.assertEqual(report['policy'], names.BENKAMA_NAMES_POLICY)
                self.assertGreaterEqual(report['changed'], 2)
                self.assertTrue(report['backupDir'])
                self.assertTrue((data_dir / report['backupDir']).is_dir())

                cust = c.execute(
                    "SELECT * FROM name_customers WHERE retired=0 AND source_name=?",
                    (names.BENKAMA_CUSTOMER_NAME,),
                ).fetchone()
                self.assertIsNotNone(cust)

                for identity, papers, color_caption in (
                    ('benkama:large-staff', ['青', '白'], names.BENKAMA_STAFF_COLOR_CAPTION),
                    ('benkama:large-period', ['赤', '緑', '青', '白'], None),
                ):
                    fmt = c.execute(
                        'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                        (identity,),
                    ).fetchone()
                    self.assertIsNotNone(fmt, identity)
                    self.assertEqual(fmt['customer_id'], cust['id'])
                    self.assertEqual(fmt['family'], names.BENKAMA_FAMILY)
                    self.assertEqual(fmt['placement'], names.BENKAMA_PLACEMENT)
                    self.assertEqual(fmt['reading'], 'フルネーム')
                    self.assertEqual(fmt['layout'], 'HeadingName')
                    self.assertEqual(int(fmt['small']), 0)
                    self.assertEqual(int(fmt['width_mm']), 80)
                    self.assertEqual(int(fmt['height_mm']), 50)
                    self.assertEqual(float(fmt['heading_font']), 20)
                    self.assertEqual(int(fmt['number_length']), 7)
                    if color_caption is not None:
                        self.assertEqual(fmt['color_caption'], color_caption)
                    else:
                        self.assertIn('八軒　1期生：赤', fmt['color_caption'])
                        self.assertIn('（札幌のみ、他は規則性なし？）', fmt['note'])
                    spec = json.loads(fmt['spec_json'])
                    self.assertEqual(spec['benkamaPolicy'], names.BENKAMA_NAMES_POLICY)
                    self.assertEqual(spec['columns'], 2)

                    samples = c.execute(
                        'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                        (fmt['id'],),
                    ).fetchall()
                    self.assertEqual([s['paper_color'] for s in samples], papers)
                    for sample in samples:
                        rel = sample['relative_path']
                        self.assertTrue(rel.startswith('images/names/'))
                        path = data_dir / rel
                        self.assertTrue(path.is_file(), rel)
                        with Image.open(path) as im:
                            self.assertEqual(im.size, (800, 500))

                after = dict(c.execute(
                    'SELECT * FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone())
                self.assertEqual(after['note'], other_note)
                for key in (
                    'format_name', 'placement', 'note', 'reading', 'fixed_color',
                    'color_caption', 'maximum_font', 'identity', 'family',
                ):
                    self.assertEqual(after[key], other_snap[key], key)

                report2 = names.ensure_benkama_name_formats(c, data_dir)
                self.assertEqual(report2['changed'], 0)
                self.assertEqual(report2.get('reason'), 'already-applied')
                n = c.execute(
                    "SELECT COUNT(*) n FROM name_formats WHERE identity LIKE 'benkama:%'"
                ).fetchone()['n']
                self.assertEqual(n, 2)

    def test_renames_alias_and_updates_legacy_white(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest('Pillow未導入')

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.DATA_DIR = None
                names.schema(c)
                names.bind(data_dir)

                cid = _add_customer(c, 'legacy-benkama', '弁釜札幌')
                legacy = _add_format(
                    c, cid, 'legacy-benkama-old', '大ネーム',
                    placement=names.BENKAMA_PLACEMENT,
                    family='MSPゴシック',
                    reading='フルネーム',
                    layout='HeadingName',
                    heading='上段：社員番号：〇〇〇〇〇〇〇',
                    note='社員番号7ケタ',
                    small=0,
                    width=80,
                    height=50,
                    fixed='白',
                    color_caption='白',
                    maximum_font=48,
                )
                other = _add_customer(c, 'other2', '別顧客')
                other_fmt = _add_format(
                    c, other, 'id-other2', '大ネーム',
                    placement='別位置', note='触らない',
                )
                other_snap = dict(c.execute(
                    'SELECT * FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone())

                report = names.ensure_benkama_name_formats(c, data_dir)
                self.assertGreaterEqual(report['changed'], 1)

                cust = c.execute(
                    'SELECT * FROM name_customers WHERE id=?', (cid,),
                ).fetchone()
                self.assertEqual(cust['source_name'], names.BENKAMA_CUSTOMER_NAME)
                self.assertEqual(cust['source_key'], names.BENKAMA_CUSTOMER_KEY)

                staff = c.execute(
                    'SELECT * FROM name_formats WHERE id=?', (legacy,),
                ).fetchone()
                self.assertEqual(staff['identity'], 'benkama:large-staff')
                self.assertEqual(staff['color_caption'], names.BENKAMA_STAFF_COLOR_CAPTION)
                self.assertEqual(json.loads(staff['groups_json']), names.BENKAMA_STAFF_GROUPS)

                period = c.execute(
                    "SELECT * FROM name_formats WHERE identity='benkama:large-period' AND retired=0"
                ).fetchone()
                self.assertIsNotNone(period)
                self.assertEqual(period['customer_id'], cid)

                n_cust = c.execute(
                    "SELECT COUNT(*) n FROM name_customers WHERE retired=0 AND ("
                    "source_name=? OR source_name LIKE '弁釜%')"
                    ,
                    (names.BENKAMA_CUSTOMER_NAME,),
                ).fetchone()['n']
                self.assertEqual(n_cust, 1)

                after = dict(c.execute(
                    'SELECT * FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone())
                for key in (
                    'format_name', 'placement', 'note', 'reading', 'fixed_color',
                    'color_caption', 'maximum_font', 'identity',
                ):
                    self.assertEqual(after[key], other_snap[key], key)


if __name__ == '__main__':
    unittest.main()
