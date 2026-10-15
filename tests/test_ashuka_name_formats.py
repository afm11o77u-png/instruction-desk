"""明日佳：訂正版 Excel 78–80 行に合わせた3段更新と実習生用2段追加。"""
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


def _add_customer(c, key, name, reading=''):
    return c.execute(
        '''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)
           VALUES(?,?,?,?,?,?,0)''',
        (key, 1, name, reading, name, '対応'),
    ).lastrowid


def _add_format(
    c, customer_id, identity, format_name, *, fid=None, placement='', heading='', note='',
    layout='', reading='', family='HG丸ゴシック', width=80, height=50, fixed='白',
    departments=None, maximum_font=0, heading_font=0, spec=None,
):
    departments = departments if departments is not None else []
    if fid is None:
        return c.execute(
            '''INSERT INTO name_formats(
                identity, customer_id, source_kind, format_name, placement, family, heading, note,
                layout, reading, small, width_mm, height_mm, fixed_color, departments_json,
                maximum_font, heading_font, spec_json, retired
               ) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,0)''',
            (
                identity, customer_id, 1, format_name, placement, family, heading, note,
                layout, reading, width, height, fixed,
                json.dumps(departments, ensure_ascii=False),
                maximum_font, heading_font, json.dumps(spec or {}, ensure_ascii=False),
            ),
        ).lastrowid
    c.execute(
        '''INSERT INTO name_formats(
            id, identity, customer_id, source_kind, format_name, placement, family, heading, note,
            layout, reading, small, width_mm, height_mm, fixed_color, departments_json,
            maximum_font, heading_font, spec_json, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
        (
            fid, identity, customer_id, 1, format_name, placement, family, heading, note,
            layout, reading, 0, width, height, fixed,
            json.dumps(departments, ensure_ascii=False),
            maximum_font, heading_font, json.dumps(spec or {}, ensure_ascii=False),
        ),
    )
    # AUTOINCREMENT の続きが明示 id より前に戻らないよう調整
    max_id = c.execute('SELECT MAX(id) AS m FROM name_formats').fetchone()['m'] or fid
    try:
        c.execute("UPDATE sqlite_sequence SET seq=? WHERE name='name_formats'", (max_id,))
        if c.total_changes == 0:
            c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES('name_formats', ?)", (max_id,))
    except sqlite3.OperationalError:
        pass
    return fid


def _add_sample(c, format_id, *, key='standard', paper='白', path='', caption=''):
    return c.execute(
        '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
           VALUES(?,?,?,?,?,?)''',
        (format_id, key, '', paper, path, caption),
    ).lastrowid


class AshukaNameFormatsTest(unittest.TestCase):
    def test_band_heights_sum_to_50(self):
        self.assertAlmostEqual(sum(b['heightMm'] for b in names.ASHUKA_THREE_BANDS), 50.0, places=2)
        self.assertAlmostEqual(sum(b['heightMm'] for b in names.ASHUKA_TRAINEE_BANDS), 50.0, places=2)

    def test_render_sample_images(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow未導入')
        with tempfile.TemporaryDirectory() as tmp:
            three = Path(tmp) / 'three.png'
            trainee = Path(tmp) / 'trainee.png'
            ok, note = names._render_ashuka_banded_sample(three, names.ASHUKA_THREE_BANDS)
            self.assertTrue(ok, note)
            ok2, note2 = names._render_ashuka_banded_sample(trainee, names.ASHUKA_TRAINEE_BANDS)
            self.assertTrue(ok2, note2)
            with Image.open(three) as im:
                self.assertEqual(im.size, (800, 500))
                self.assertEqual(im.getpixel((0, 0)), (255, 255, 255))
            with Image.open(trainee) as im2:
                self.assertEqual(im2.size, (800, 500))

    def test_updates_nm150_and_reuses_empty_nm229(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img = data_dir / 'images' / 'names' / 'old-150.png'
            img.parent.mkdir(parents=True, exist_ok=True)
            img.write_bytes(b'\x89PNG\r\n\x1a\n' + b'old')

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                ashuka = _add_customer(c, 'ashuka', names.ASHUKA_CUSTOMER_NAME, names.ASHUKA_READING_RIGHT)
                other = _add_customer(c, 'other', '他社サンプル')
                three = _add_format(
                    c, ashuka, 'id-150', '大ネーム',
                    fid=150, placement='旧位置', heading='旧', note='旧備考',
                    layout='Name', reading='', width=80, height=50, fixed='白',
                )
                empty = _add_format(
                    c, ashuka, 'id-229', '大ネーム',
                    fid=229, placement='', heading='', note='',
                )
                other_fmt = _add_format(c, other, 'id-other', '大ネーム', heading='他社の内容')
                _add_sample(c, three, path='images/names/old-150.png', caption='旧見本')
                c.execute(
                    'INSERT INTO name_reviews(format_id, assignee_id, source_label, confirmed_digest, confirmed_at, confirmed_name) VALUES(?,?,?,?,?,?)',
                    (three, 11, '田中', 'old-digest', '2026-01-01T00:00:00', '田中'),
                )
                c.execute(
                    'INSERT INTO name_reviews(format_id, assignee_id, source_label) VALUES(?,?,?)',
                    (empty, 11, '田中'),
                )

                report = names.correct_ashuka_name_formats(c, data_dir)
                self.assertGreaterEqual(report['changed'], 2)
                self.assertEqual(report['policy'], names.ASHUKA_NAMES_POLICY)

                fmt150 = c.execute('SELECT * FROM name_formats WHERE id=?', (150,)).fetchone()
                self.assertEqual(fmt150['format_name'], names.ASHUKA_THREE_FORMAT_NAME)
                self.assertEqual(fmt150['placement'], names.ASHUKA_PLACEMENT)
                self.assertEqual(fmt150['layout'], 'DeptFurigana')
                self.assertEqual(fmt150['reading'], '苗字のみ')
                self.assertEqual(fmt150['heading'], names.ASHUKA_THREE_HEADING)
                self.assertEqual(fmt150['note'], names.ASHUKA_THREE_NOTE)
                self.assertEqual(fmt150['family'], names.ASHUKA_FAMILY)
                self.assertEqual(fmt150['width_mm'], 80)
                self.assertEqual(fmt150['height_mm'], 50)
                self.assertEqual(fmt150['fixed_color'], '白')
                self.assertEqual(float(fmt150['maximum_font']), 50)
                self.assertEqual(float(fmt150['heading_font']), 24)
                self.assertEqual(json.loads(fmt150['departments_json']), names.ASHUKA_DEPARTMENTS)
                spec = json.loads(fmt150['spec_json'])
                self.assertEqual(spec['ashukaKind'], 'three')
                self.assertEqual(spec['bandHeightsMm'], [12.8, 12.5, 24.7])
                self.assertEqual(spec['bandFontsPt'], [24, 28, 50])

                sample150 = c.execute(
                    'SELECT * FROM name_samples WHERE format_id=? ORDER BY id', (150,),
                ).fetchone()
                self.assertIsNotNone(sample150)
                self.assertEqual(sample150['paper_color'], '白')
                self.assertEqual(sample150['caption'], names.ASHUKA_THREE_SAMPLE_CAPTION)
                self.assertTrue((data_dir / sample150['relative_path']).is_file())

                fmt229 = c.execute('SELECT * FROM name_formats WHERE id=?', (229,)).fetchone()
                self.assertEqual(fmt229['format_name'], names.ASHUKA_TRAINEE_FORMAT_NAME)
                self.assertEqual(fmt229['identity'], names.ASHUKA_TRAINEE_IDENTITY)
                self.assertEqual(fmt229['layout'], 'DeptName')
                self.assertIn('ターシャー', fmt229['heading'])
                self.assertIn('ふりがななし', fmt229['note'])
                self.assertEqual(json.loads(fmt229['departments_json']), names.ASHUKA_DEPARTMENTS)
                sample229 = c.execute(
                    'SELECT * FROM name_samples WHERE format_id=?', (229,),
                ).fetchone()
                self.assertTrue((data_dir / sample229['relative_path']).is_file())
                self.assertIn('ターシャー', sample229['caption'])

                # 担当は維持。了承digestは内容変更で無効化（自動了承しない）
                review150 = c.execute(
                    'SELECT assignee_id, source_label, confirmed_digest FROM name_reviews WHERE format_id=?',
                    (150,),
                ).fetchone()
                self.assertEqual(review150['assignee_id'], 11)
                self.assertEqual(review150['source_label'], '田中')
                self.assertIsNone(review150['confirmed_digest'])
                review229 = c.execute(
                    'SELECT assignee_id, source_label FROM name_reviews WHERE format_id=?',
                    (229,),
                ).fetchone()
                self.assertEqual(review229['assignee_id'], 11)
                self.assertEqual(review229['source_label'], '田中')

                other_row = c.execute('SELECT * FROM name_formats WHERE id=?', (other_fmt,)).fetchone()
                self.assertEqual(other_row['heading'], '他社の内容')
                self.assertEqual(other_row['format_name'], '大ネーム')

                backup = data_dir / report['backupDir']
                self.assertTrue(backup.is_dir())
                self.assertTrue((backup / 'format-150.json').is_file())
                self.assertTrue((backup / 'format-229.json').is_file())
                self.assertTrue((data_dir / names.ASHUKA_NAMES_REPORT).is_file())

                again = names.correct_ashuka_name_formats(c, data_dir)
                self.assertEqual(again['changed'], 0)
                self.assertEqual(again.get('reason'), '適用済み')
                # 再実行で実習生用が二重追加されない
                count = c.execute(
                    'SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND retired=0 AND (format_name=? OR identity=?)',
                    (ashuka, names.ASHUKA_TRAINEE_FORMAT_NAME, names.ASHUKA_TRAINEE_IDENTITY),
                ).fetchone()['n']
                self.assertEqual(count, 1)

    def test_does_not_overwrite_filled_nm229_creates_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.schema(c)
                ashuka = _add_customer(c, 'ashuka', names.ASHUKA_CUSTOMER_NAME)
                _add_format(
                    c, ashuka, 'id-150', '大ネーム（旧）',
                    fid=150, placement='旧', heading='旧見出し', note='旧',
                    layout='DeptFurigana',
                )
                _add_format(
                    c, ashuka, 'id-229', '大ネーム（別用途）',
                    fid=229, placement='別位置', heading='別見出し', note='中身あり',
                    layout='Name', departments=['別部署'],
                )
                _add_sample(c, 229, path='images/names/keep.png', caption='残す')

                report = names.correct_ashuka_name_formats(c, data_dir)
                actions = {item['action'] for item in report['items']}
                self.assertIn('update-three', actions)
                self.assertIn('create-trainee', actions)
                fmt229 = c.execute('SELECT * FROM name_formats WHERE id=?', (229,)).fetchone()
                self.assertEqual(fmt229['format_name'], '大ネーム（別用途）')
                self.assertEqual(fmt229['heading'], '別見出し')
                trainee = c.execute(
                    'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                    (names.ASHUKA_TRAINEE_IDENTITY,),
                ).fetchone()
                self.assertIsNotNone(trainee)
                self.assertNotEqual(trainee['id'], 229)
                self.assertEqual(trainee['format_name'], names.ASHUKA_TRAINEE_FORMAT_NAME)

                # 再実行しても追加しない
                before = c.execute(
                    'SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND retired=0',
                    (ashuka,),
                ).fetchone()['n']
                again = names.correct_ashuka_name_formats(c, data_dir)
                self.assertEqual(again['changed'], 0)
                after = c.execute(
                    'SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND retired=0',
                    (ashuka,),
                ).fetchone()['n']
                self.assertEqual(before, after)

    def test_skips_other_customers_and_missing_ashuka(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.schema(c)
                other = _add_customer(c, 'other', '札幌健診センター')
                _add_format(c, other, 'x', '大ネーム', fid=150, heading='他社')
                report = names.correct_ashuka_name_formats(c, data_dir)
                self.assertTrue(report.get('skipped'))
                self.assertEqual(report.get('reason'), '明日佳顧客なし')
                row = c.execute('SELECT heading FROM name_formats WHERE id=150').fetchone()
                self.assertEqual(row['heading'], '他社')


if __name__ == '__main__':
    unittest.main()
