"""エフケー：Excel 行29 のネーム形式（色・36pt・苗字のみ）。"""
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
            groups_json, colors_json, swatches_json, maximum_font, spec_json, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
        (
            identity, customer_id, 1, format_name,
            kwargs.get('placement', ''), kwargs.get('family', ''),
            kwargs.get('heading', ''), kwargs.get('note', ''),
            kwargs.get('layout', 'Name'), kwargs.get('reading', 'フルネーム'),
            kwargs.get('small', 1), kwargs.get('width', 50), kwargs.get('height', 24),
            kwargs.get('fixed', '先方から指定'), kwargs.get('color_caption', '先方から指定'),
            json.dumps(kwargs.get('groups') or [], ensure_ascii=False),
            json.dumps(kwargs.get('colors') or [], ensure_ascii=False),
            json.dumps(kwargs.get('swatches') or [], ensure_ascii=False),
            kwargs.get('maximum_font', 0),
            json.dumps(kwargs.get('spec') or {}, ensure_ascii=False),
        ),
    ).lastrowid


class FkNameFormatsTest(unittest.TestCase):
    def test_format_def_matches_excel_row29(self):
        fmt = names._fk_format_def()
        self.assertEqual(fmt['identity'], 'fk:small-surname')
        self.assertEqual(fmt['sourceRow'], 29)
        self.assertEqual(fmt['placement'], '白衣：左袖25cm　白下：左前　白帽子：右耳上')
        self.assertEqual(fmt['formatName'], '小ネーム（苗字のみ）')
        self.assertEqual(fmt['reading'], '苗字のみ')
        self.assertEqual(fmt['layout'], 'Surname')
        self.assertTrue(fmt['small'])
        self.assertEqual(fmt['widthMm'], 50)
        self.assertEqual(fmt['heightMm'], 24)
        self.assertEqual(fmt['maximumFont'], 36)
        self.assertEqual(fmt['fontPt'], 36)
        self.assertEqual(fmt['sampleText'], '山田')
        self.assertEqual(fmt['colorCaption'], '社員：緑　パート：青')
        self.assertEqual(fmt['groups'], ['社員', 'パート'])
        self.assertEqual(fmt['colors'], ['緑', '青'])
        self.assertEqual(fmt['swatches'], ['#90EE90', '#87CEFA'])
        self.assertEqual(fmt['papers'], ('緑', '青'))
        spec = names._fk_spec(fmt)
        self.assertIs(spec['surnameOnly'], True)
        self.assertEqual(spec['fkPolicy'], names.FK_NAMES_POLICY)

    def test_creates_fk_and_leaves_others(self):
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

                report = names.ensure_fk_name_formats(c, data_dir)
                self.assertEqual(report['policy'], names.FK_NAMES_POLICY)
                self.assertGreaterEqual(report['changed'], 1)
                self.assertTrue(report['backupDir'])
                self.assertTrue((data_dir / report['backupDir']).is_dir())

                cust = c.execute(
                    "SELECT * FROM name_customers WHERE retired=0 AND source_name=?",
                    (names.FK_CUSTOMER_NAME,),
                ).fetchone()
                self.assertIsNotNone(cust)

                fmt = c.execute(
                    'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                    (names.FK_IDENTITY,),
                ).fetchone()
                self.assertIsNotNone(fmt)
                self.assertEqual(fmt['customer_id'], cust['id'])
                self.assertEqual(fmt['format_name'], names.FK_FORMAT_NAME)
                self.assertEqual(fmt['placement'], names.FK_PLACEMENT)
                self.assertEqual(fmt['family'], names.FK_FAMILY)
                self.assertEqual(fmt['reading'], '苗字のみ')
                self.assertEqual(fmt['layout'], 'Surname')
                self.assertEqual(int(fmt['small']), 1)
                self.assertEqual(int(fmt['width_mm']), 50)
                self.assertEqual(int(fmt['height_mm']), 24)
                self.assertEqual(float(fmt['maximum_font']), 36)
                self.assertEqual(fmt['color_caption'], names.FK_COLOR_CAPTION)
                self.assertEqual(json.loads(fmt['groups_json']), names.FK_GROUPS)
                self.assertEqual(json.loads(fmt['colors_json']), names.FK_COLORS)
                self.assertEqual(json.loads(fmt['swatches_json']), names.FK_SWATCHES)
                spec = json.loads(fmt['spec_json'])
                self.assertIs(spec['surnameOnly'], True)
                self.assertEqual(spec['fkPolicy'], names.FK_NAMES_POLICY)

                samples = c.execute(
                    'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                    (fmt['id'],),
                ).fetchall()
                self.assertEqual(len(samples), 2)
                papers = [s['paper_color'] for s in samples]
                self.assertEqual(papers, ['緑', '青'])
                for sample in samples:
                    rel = sample['relative_path']
                    self.assertTrue(rel.startswith('images/names/'))
                    path = data_dir / rel
                    self.assertTrue(path.is_file(), rel)
                    with Image.open(path) as im:
                        self.assertEqual(im.size, (500, 240))

                after = c.execute(
                    'SELECT note FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone()['note']
                self.assertEqual(after, other_note)

                report2 = names.ensure_fk_name_formats(c, data_dir)
                self.assertEqual(report2['changed'], 0)
                self.assertEqual(report2.get('reason'), 'already-applied')
                n = c.execute(
                    'SELECT COUNT(*) n FROM name_formats WHERE identity=?',
                    (names.FK_IDENTITY,),
                ).fetchone()['n']
                self.assertEqual(n, 1)

    def test_updates_legacy_full_name_format(self):
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

                cid = _add_customer(c, 'legacy-fk', 'エフケー')
                legacy = _add_format(
                    c, cid, 'legacy-fk-old', '小ネーム',
                    placement=names.FK_PLACEMENT,
                    family='HG丸ゴシック',
                    reading='フルネーム',
                    layout='Name',
                    small=1,
                    width=50,
                    height=24,
                    fixed='先方から指定',
                    color_caption='先方から指定',
                    maximum_font=26,
                )
                other = _add_customer(c, 'other2', '別顧客')
                other_fmt = _add_format(
                    c, other, 'id-other2', '小ネーム',
                    placement='別位置', note='触らない',
                )
                other_snap = dict(c.execute(
                    'SELECT * FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone())

                report = names.ensure_fk_name_formats(c, data_dir)
                self.assertGreaterEqual(report['changed'], 1)

                fmt = c.execute(
                    'SELECT * FROM name_formats WHERE id=?', (legacy,),
                ).fetchone()
                self.assertEqual(fmt['identity'], names.FK_IDENTITY)
                self.assertEqual(fmt['reading'], '苗字のみ')
                self.assertEqual(fmt['layout'], 'Surname')
                self.assertEqual(float(fmt['maximum_font']), 36)
                self.assertEqual(fmt['color_caption'], names.FK_COLOR_CAPTION)
                self.assertEqual(json.loads(fmt['groups_json']), names.FK_GROUPS)

                # 他顧客不変
                after = dict(c.execute(
                    'SELECT * FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone())
                for key in (
                    'format_name', 'placement', 'note', 'reading', 'fixed_color',
                    'color_caption', 'maximum_font', 'identity',
                ):
                    self.assertEqual(after[key], other_snap[key], key)

                n = c.execute(
                    "SELECT COUNT(*) n FROM name_customers WHERE retired=0 AND source_name='エフケー'"
                ).fetchone()['n']
                self.assertEqual(n, 1)


if __name__ == '__main__':
    unittest.main()
