"""女性用ネーム見本の架空氏名（山田太郎→山田花子）。起動時変換なし・保守CLI。"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import names

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

FONT = Path('/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf')


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


def _add_format(c, customer_id, identity, format_name, *, groups=None, color_caption='', note='', fixed_color=''):
    return c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, note, fixed_color, color_caption,
            groups_json, colors_json, swatches_json, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)''',
        (
            identity, customer_id, 1, format_name, '胸', note, fixed_color, color_caption,
            json.dumps(groups or [], ensure_ascii=False),
            json.dumps(['赤'] * len(groups or []), ensure_ascii=False),
            json.dumps(['#F08080'] * len(groups or []), ensure_ascii=False),
        ),
    ).lastrowid


def _add_sample(c, format_id, *, key='standard', condition='', paper='', path='', caption=''):
    return c.execute(
        '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
           VALUES(?,?,?,?,?,?)''',
        (format_id, key, condition, paper, path, caption),
    ).lastrowid


def _draw_name(path, text, fill=(0, 0, 0), bg=(255, 220, 220)):
    font = ImageFont.truetype(str(FONT), 28)
    im = Image.new('RGB', (240, 64), bg)
    ImageDraw.Draw(im).text((16, 14), text, font=font, fill=fill)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


class FemaleSampleHanakoTest(unittest.TestCase):
    def test_names_py_compiles_without_stray_markdown(self):
        """Markdown 箇条書きが docstring/コメント外に混入すると SyntaxError になる。"""
        import py_compile
        import tokenize
        import io

        path = ROOT / 'names.py'
        py_compile.compile(str(path), doraise=True)
        src = path.read_bytes()
        covered = set()
        for tok in tokenize.tokenize(io.BytesIO(src).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                for line_no in range(tok.start[0], tok.end[0] + 1):
                    covered.add(line_no)
        stray = []
        for line_no, line in enumerate(src.decode('utf-8').splitlines(), 1):
            stripped = line.lstrip()
            if line_no in covered:
                continue
            if stripped.startswith(('- ', '* ')) or '「' in stripped:
                stray.append(f'{line_no}:{stripped[:80]}')
        self.assertEqual(stray, [], 'stray markdown/code outside strings:\n' + '\n'.join(stray))

    def test_maintenance_cmd_is_ascii_crlf(self):
        """cmd.exe は UTF-8 日本語行を CP932 誤読し、行が割れることがある。"""
        path = ROOT / '女性見本花子保守.cmd'
        raw = path.read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'), 'BOM 無し')
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''), 'CRLF only')
        self.assertTrue(all(b < 0x80 for b in raw), 'ASCII only')
        self.assertIn(b'set "DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1"', raw)
        self.assertIn(b'maintain_female_sample_hanako.py', raw)

    def test_replace_preserves_spacing(self):
        self.assertEqual(names.replace_yamada_taro('山田太郎'), '山田花子')
        self.assertEqual(names.replace_yamada_taro('見本：山田 太郎'), '見本：山田 花子')
        self.assertEqual(names.replace_yamada_taro('見本：山田　太郎'), '見本：山田　花子')
        self.assertEqual(names.replace_yamada_taro('山田'), '山田')
        self.assertEqual(names.replace_yamada_taro('佐藤 太郎'), '佐藤 太郎')

    def test_female_mark_not_by_color_alone(self):
        self.assertTrue(names._sample_is_explicit_female(
            {'condition_label': '女性用', 'paper_color': '赤', 'variant_key': 'f'},
            {'format_name': '大', 'groups_json': '[]'},
        ))
        self.assertTrue(names._sample_is_explicit_female(
            {'condition_label': '女性', 'paper_color': '赤', 'variant_key': 'f'},
            {'format_name': '大', 'groups_json': '["男性","女性"]'},
        ))
        self.assertFalse(names._sample_is_explicit_female(
            {'condition_label': '', 'paper_color': '赤', 'variant_key': 'red'},
            {'format_name': '大', 'groups_json': '[]', 'fixed_color': '赤'},
        ))
        self.assertFalse(names._sample_is_explicit_female(
            {'condition_label': '男性用', 'paper_color': '青', 'variant_key': 'm'},
            {'format_name': '大', 'groups_json': '["男性用","女性用"]'},
        ))
        self.assertFalse(names._sample_is_explicit_female(
            {'condition_label': '男性', 'paper_color': '青', 'variant_key': 'm'},
            {'format_name': '大', 'groups_json': '["男性","女性"]'},
        ))
        self.assertFalse(names._sample_is_explicit_female(
            {'condition_label': '', 'paper_color': '', 'variant_key': 'x'},
            {'format_name': '大', 'groups_json': '["男性用","女性用"]'},
        ))
        self.assertTrue(names._sample_is_explicit_female(
            {'condition_label': '', 'paper_color': '', 'variant_key': 'x'},
            {'format_name': '女性用ネーム', 'groups_json': '[]', 'note': ''},
        ))
        self.assertTrue(names._sample_is_explicit_female(
            {'condition_label': '女性専用', 'paper_color': '', 'variant_key': 'f'},
            {'format_name': '大', 'groups_json': '[]'},
        ))

    def test_schema_and_default_correct_skip_without_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            with _connect(db) as c:
                names.bind(data_dir)
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop(names.FEMALE_MAINTENANCE_ENV, None)
                    names.schema(c)
                    skipped = names.correct_female_sample_names(c, data_dir)
                self.assertTrue(skipped.get('skipped'))
                self.assertIn(names.FEMALE_MAINTENANCE_ENV, skipped.get('reason', ''))

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_correct_only_female_and_forks_shared_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            shared = img_dir / 'shared.png'
            _draw_name(shared, '山田 太郎')

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'c1', '試験顧客')
                fid = _add_format(c, cust, 'id-mix', '混在形式', groups=['男性用', '女性用'])
                male_id = _add_sample(
                    c, fid, key='male', condition='男性用', paper='青',
                    path='images/names/shared.png', caption='男性見本 山田 太郎',
                )
                female_id = _add_sample(
                    c, fid, key='female', condition='女性用', paper='赤',
                    path='images/names/shared.png', caption='女性見本 山田 太郎',
                )
                red_only = _add_format(c, cust, 'id-red', '赤のみ', fixed_color='赤')
                red_id = _add_sample(
                    c, red_only, key='red', condition='', paper='赤',
                    path='', caption='色だけ 山田 太郎',
                )
                surname = _add_format(c, cust, 'id-sur', '女性用苗字', groups=['女性用'])
                sur_id = _add_sample(
                    c, surname, key='sur', condition='女性用', paper='',
                    path='', caption='苗字のみ 山田',
                )

                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report['changed'], 1)
                self.assertEqual(report['items'][0]['sampleId'], female_id)
                self.assertEqual(report.get('ruleVersion'), names.FEMALE_HANAKO_RULE_VERSION)

                female = c.execute('SELECT * FROM name_samples WHERE id=?', (female_id,)).fetchone()
                male = c.execute('SELECT * FROM name_samples WHERE id=?', (male_id,)).fetchone()
                red = c.execute('SELECT * FROM name_samples WHERE id=?', (red_id,)).fetchone()
                sur = c.execute('SELECT * FROM name_samples WHERE id=?', (sur_id,)).fetchone()

                self.assertEqual(female['caption'], '女性見本 山田 花子')
                self.assertIn(names.FEMALE_IMAGE_SUFFIX, female['relative_path'])
                self.assertEqual(male['caption'], '男性見本 山田 太郎')
                self.assertEqual(male['relative_path'], 'images/names/shared.png')
                self.assertEqual(red['caption'], '色だけ 山田 太郎')
                self.assertEqual(sur['caption'], '苗字のみ 山田')

                self.assertTrue(shared.is_file())
                forked = data_dir / female['relative_path']
                self.assertTrue(forked.is_file())
                self.assertNotEqual(shared.read_bytes(), forked.read_bytes())

                backup = data_dir / report['backupDir']
                self.assertTrue(backup.is_dir())
                self.assertTrue((backup / 'report.json').is_file())
                self.assertTrue((data_dir / names.FEMALE_HANAKO_REPORT).is_file())

                self.assertIsNone(c.execute('SELECT confirmed_digest FROM name_reviews WHERE format_id=?', (fid,)).fetchone())

                again = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(again['changed'], 0)

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_bare_josei_after_v1_prior_rescans(self):
        """#97(v1) 適用済みでも条件「女性」だけの見本は caption・共有画像を直す。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            shared = img_dir / 'nm000115.png'
            _draw_name(shared, '山田 太郎', bg=(240, 160, 160))

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'c-nm', '試験NM')
                fid = _add_format(
                    c, cust, 'NM-000115', '大ネーム(フルネーム)',
                    groups=['男性', '女性'],
                )
                male_id = _add_sample(
                    c, fid, key='male', condition='男性', paper='青',
                    path='images/names/nm000115.png', caption='山田 太郎',
                )
                female_id = _add_sample(
                    c, fid, key='female', condition='女性', paper='赤',
                    path='images/names/nm000115.png', caption='山田 太郎',
                )
                c.execute(
                    "INSERT OR REPLACE INTO settings VALUES(?,?)",
                    (
                        names.FEMALE_HANAKO_SETTINGS_KEY,
                        json.dumps({
                            'at': '20260101-000000',
                            'changed': 0,
                            'report': names.FEMALE_HANAKO_REPORT,
                            'ruleVersion': 1,
                        }, ensure_ascii=False),
                    ),
                )

                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report['changed'], 1)
                self.assertEqual(report['items'][0]['sampleId'], female_id)
                self.assertEqual(report['ruleVersion'], 3)

                female = c.execute('SELECT * FROM name_samples WHERE id=?', (female_id,)).fetchone()
                male = c.execute('SELECT * FROM name_samples WHERE id=?', (male_id,)).fetchone()
                self.assertEqual(female['caption'], '山田 花子')
                self.assertIn(names.FEMALE_IMAGE_SUFFIX, female['relative_path'])
                self.assertEqual(male['caption'], '山田 太郎')
                self.assertEqual(male['relative_path'], 'images/names/nm000115.png')
                self.assertTrue(shared.is_file())
                self.assertTrue((data_dir / female['relative_path']).is_file())

                prior, ver = names._female_hanako_prior(c)
                self.assertEqual(ver, names.FEMALE_HANAKO_RULE_VERSION)
                self.assertEqual(prior.get('changed'), 1)

                again = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(again['changed'], 0)

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_maintenance_connect_short_tx_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            shared = img_dir / 'shared.png'
            _draw_name(shared, '山田 太郎')

            def connect():
                return _connect(db)

            with connect() as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'c1', '試験顧客')
                fid = _add_format(c, cust, 'id-mix', '混在', groups=['男性', '女性'])
                _add_sample(
                    c, fid, key='male', condition='男性', paper='青',
                    path='images/names/shared.png', caption='山田 太郎',
                )
                female_id = _add_sample(
                    c, fid, key='female', condition='女性', paper='赤',
                    path='images/names/shared.png', caption='山田 太郎',
                )

            with mock.patch.dict(os.environ, {names.FEMALE_MAINTENANCE_ENV: '1'}):
                ran = names.run_female_sample_hanako_maintenance(
                    connect, data_dir, require_env=True,
                )
                self.assertEqual(ran['changed'], 1)

            female = None
            with connect() as c:
                female = dict(c.execute('SELECT * FROM name_samples WHERE id=?', (female_id,)).fetchone())
            self.assertEqual(female['caption'], '山田 花子')
            self.assertIn(names.FEMALE_IMAGE_SUFFIX, female['relative_path'])

            # 利用者編集を想定して caption を戻し、計画と不一致なら conflict
            with connect() as c:
                c.execute(
                    'UPDATE name_samples SET caption=?, relative_path=? WHERE id=?',
                    ('利用者が直した', 'images/names/shared.png', female_id),
                )
                # completed を消して再検査させる
                c.execute("DELETE FROM settings WHERE key=?", (names.FEMALE_HANAKO_SETTINGS_KEY,))

            with mock.patch.dict(os.environ, {names.FEMALE_MAINTENANCE_ENV: '1'}):
                # 候補読み込み時の caption が「利用者が直した」なので山田太郎系ではなく pending にならない
                report2 = names.run_female_sample_hanako_maintenance(
                    connect, data_dir, require_env=True,
                )
            # 太郎が無いので変換対象外（利用者編集を花子にしない）
            self.assertEqual(report2.get('changed', 0), 0)
            with connect() as c:
                row = c.execute('SELECT caption FROM name_samples WHERE id=?', (female_id,)).fetchone()
                self.assertEqual(row['caption'], '利用者が直した')

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_image_detect_failure_leaves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            blank = img_dir / 'blank.png'
            blank.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (80, 40), (255, 200, 200)).save(blank)

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'c1', '試験')
                fid = _add_format(c, cust, 'id-f', '女性用', groups=['女性用'])
                sid = _add_sample(
                    c, fid, key='f', condition='女性用', paper='赤',
                    path='images/names/blank.png', caption='山田 太郎',
                )
                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report['changed'], 0)
                self.assertGreaterEqual(report.get('failed', 0), 1)
                row = c.execute('SELECT caption, relative_path FROM name_samples WHERE id=?', (sid,)).fetchone()
                self.assertEqual(row['caption'], '山田 太郎')
                self.assertEqual(row['relative_path'], 'images/names/blank.png')

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_catalog_json_caption_updated_via_maintenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            import_dir = data_dir / 'name-import'
            import_dir.mkdir()
            img = data_dir / 'images' / 'names' / 'only-female.png'
            _draw_name(img, '山田太郎', bg=(255, 255, 255))
            catalog = {
                'customerCount': 1,
                'formatCount': 1,
                'imageCount': 1,
                'customers': [{'sourceKey': 'c', 'sourceIndex': 1, 'name': '顧客Z', 'reading': ''}],
                'formats': [{
                    'identity': 'fmt-z',
                    'customerKey': 'c',
                    'sourceKind': 1,
                    'format': '女性用ラベル',
                    'groups': ['女性用'],
                    'colors': ['赤'],
                    'colorSwatches': ['#F08080'],
                    'samples': [{
                        'key': 'f',
                        'condition': '女性用',
                        'paperColor': '赤',
                        'relativePath': 'images/names/only-female.png',
                        'caption': '表示は山田太郎',
                    }],
                }],
            }
            (import_dir / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
            db = data_dir / 'desk.sqlite3'

            def connect():
                return _connect(db)

            names.import_catalog(connect, data_dir)
            with connect() as c:
                row = c.execute('SELECT caption, relative_path FROM name_samples').fetchone()
                # import alone must NOT convert（起動・取込を軽く）
                self.assertEqual(row['caption'], '表示は山田太郎')

            with mock.patch.dict(os.environ, {names.FEMALE_MAINTENANCE_ENV: '1'}):
                report = names.run_female_sample_hanako_maintenance(connect, data_dir)
            self.assertEqual(report['changed'], 1)
            with connect() as c:
                row = c.execute('SELECT caption, relative_path FROM name_samples').fetchone()
                self.assertEqual(row['caption'], '表示は山田花子')
            data = json.loads((import_dir / 'catalog.json').read_text(encoding='utf-8'))
            sample = data['formats'][0]['samples'][0]
            self.assertEqual(sample['caption'], '表示は山田花子')

    def test_maintenance_requires_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'

            def connect():
                return _connect(db)

            with connect() as c:
                names.bind(data_dir)
                names.schema(c)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(names.FEMALE_MAINTENANCE_ENV, None)
                report = names.run_female_sample_hanako_maintenance(connect, data_dir, require_env=True)
            self.assertTrue(report.get('skipped'))
            self.assertIn(names.FEMALE_MAINTENANCE_ENV, report.get('reason', ''))


if __name__ == '__main__':
    unittest.main()
