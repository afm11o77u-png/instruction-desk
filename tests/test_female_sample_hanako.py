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


def _add_format(
    c, customer_id, identity, format_name, *, groups=None, color_caption='', note='', fixed_color='',
    layout='Name', family='HG丸ゴシックM-PRO', width_mm=80, height_mm=50, maximum_font=28, blank=0,
):
    return c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, family, note, layout,
            fixed_color, color_caption, groups_json, colors_json, swatches_json,
            width_mm, height_mm, maximum_font, blank, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
        (
            identity, customer_id, 1, format_name, '胸', family, note, layout,
            fixed_color, color_caption,
            json.dumps(groups or [], ensure_ascii=False),
            json.dumps(['赤'] * len(groups or []), ensure_ascii=False),
            json.dumps(['#F08080'] * len(groups or []), ensure_ascii=False),
            width_mm, height_mm, maximum_font, blank,
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


def _draw_dept_name(path, dept, name, *, size=(320, 140), bg=(255, 220, 220), dept_size=22, name_size=28):
    """部署＋氏名の複合見本フィクスチャ。"""
    im = Image.new('RGB', size, bg)
    draw = ImageDraw.Draw(im)
    dept_font = ImageFont.truetype(str(FONT), dept_size)
    name_font = ImageFont.truetype(str(FONT), name_size)
    draw.text((24, 18), dept, font=dept_font, fill=(0, 0, 0))
    draw.text((24, 70), name, font=name_font, fill=(0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def _draw_heading_name(path, heading, name, *, size=(320, 140), bg=(255, 220, 220)):
    im = Image.new('RGB', size, bg)
    draw = ImageDraw.Draw(im)
    h_font = ImageFont.truetype(str(FONT), 20)
    n_font = ImageFont.truetype(str(FONT), 28)
    draw.text((20, 16), heading, font=h_font, fill=(0, 0, 0))
    draw.text((20, 72), name, font=n_font, fill=(0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def _draw_split_number(path, left, name, right, *, size=(360, 100), bg=(255, 220, 220)):
    im = Image.new('RGB', size, bg)
    draw = ImageDraw.Draw(im)
    side = ImageFont.truetype(str(FONT), 22)
    mid = ImageFont.truetype(str(FONT), 28)
    draw.text((12, 34), left, font=side, fill=(0, 0, 0))
    draw.text((110, 30), name, font=mid, fill=(0, 0, 0))
    draw.text((300, 34), right, font=side, fill=(0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def _region_bytes(path, box):
    im = Image.open(path).convert('RGB')
    return im.crop(box).tobytes()


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
        self.assertTrue(names._sample_is_explicit_female(
            {'condition_label': '女性／と畜場', 'paper_color': '赤', 'variant_key': 'f'},
            {'format_name': '部署付き', 'groups_json': '["女性／と畜場","女性／加工場"]'},
        ))
        self.assertTrue(names._sample_is_explicit_female(
            {'condition_label': '女性／加工場', 'paper_color': '赤', 'variant_key': 'f'},
            {'format_name': '部署付き', 'groups_json': '["女性／と畜場","女性／加工場"]'},
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
                self.assertEqual(report['ruleVersion'], names.FEMALE_HANAKO_RULE_VERSION)

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
                failed = report.get('failedItems') or []
                self.assertTrue(any('再生成' in (item.get('reason') or '') or '印字' in (item.get('reason') or '') for item in failed))
                row = c.execute('SELECT caption, relative_path FROM name_samples WHERE id=?', (sid,)).fetchone()
                self.assertEqual(row['caption'], '山田 太郎')
                self.assertEqual(row['relative_path'], 'images/names/blank.png')

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_deptname_regenerates_name_keeps_department(self):
        """DeptName は部署名を残して氏名だけ山田花子へ。氏名のみ画像に作り直さない。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img = data_dir / 'images' / 'names' / 'dept.png'
            _draw_dept_name(img, 'と畜場', '山田 太郎', bg=(240, 160, 160))
            before_dept = _region_bytes(img, (0, 0, 320, 55))
            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'c1', '試験')
                fid = _add_format(
                    c, cust, 'id-dept', '部署付き', groups=['女性'],
                    layout='DeptName', maximum_font=28, width_mm=80, height_mm=35,
                )
                sid = _add_sample(
                    c, fid, key='f', condition='女性', paper='赤',
                    path='images/names/dept.png', caption='山田 太郎',
                )
                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report['changed'], 1, report)
                item = report['items'][0]
                self.assertEqual(item['sampleId'], sid)
                self.assertIn('regenerat', item.get('imageAction', ''))
                self.assertIn('DeptName', item.get('imageNote', ''))
                row = c.execute('SELECT caption, relative_path FROM name_samples WHERE id=?', (sid,)).fetchone()
                self.assertEqual(row['caption'], '山田 花子')
                self.assertIn(names.FEMALE_IMAGE_SUFFIX, row['relative_path'])
                forked = data_dir / row['relative_path']
                self.assertTrue(forked.is_file())
                self.assertNotEqual(img.read_bytes(), forked.read_bytes())
                self.assertEqual(_region_bytes(forked, (0, 0, 320, 55)), before_dept)

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_unsupported_layout_held_without_name_only_rewrite(self):
        """未対応 layout（例: QR）は保留。閾値緩和や氏名のみ作り直しはしない。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img = data_dir / 'images' / 'names' / 'qr.png'
            _draw_name(img, '山田 太郎', bg=(240, 160, 160))
            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'c1', '試験')
                fid = _add_format(
                    c, cust, 'id-qr', 'QR付き', groups=['女性'],
                    layout='QR',
                )
                sid = _add_sample(
                    c, fid, key='f', condition='女性', paper='赤',
                    path='images/names/qr.png', caption='山田 太郎',
                )
                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report['changed'], 0)
                self.assertGreaterEqual(report.get('failed', 0), 1)
                reason = (report.get('failedItems') or [{}])[0].get('reason') or ''
                self.assertIn('QR', reason)
                row = c.execute('SELECT caption, relative_path FROM name_samples WHERE id=?', (sid,)).fetchone()
                self.assertEqual(row['caption'], '山田 太郎')
                self.assertEqual(row['relative_path'], 'images/names/qr.png')
                self.assertFalse((data_dir / 'images' / 'names' / f'qr{names.FEMALE_IMAGE_SUFFIX}.png').is_file())

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_surname_layout_excluded_not_failed(self):
        """Surname / 苗字のみは対象外。失敗件数に含めず「山田」のまま。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img = data_dir / 'images' / 'names' / 'sur.png'
            _draw_name(img, '山田', bg=(255, 220, 220))
            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'c1', '試験')
                fid = _add_format(
                    c, cust, 'id-sur', '苗字のみ', groups=['女性'],
                    layout='Surname',
                )
                sid = _add_sample(
                    c, fid, key='f', condition='女性', paper='赤',
                    path='images/names/sur.png', caption='山田',
                )
                # caption に太郎が無くても layout=Surname なら excluded
                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report.get('changed', 0), 0)
                self.assertEqual(report.get('failed', 0), 0)
                self.assertGreaterEqual(report.get('excludedSurname', 0), 1)
                kinds = {item.get('excludedKind') for item in (report.get('surnameExcludedItems') or report.get('excludedItems') or [])}
                self.assertIn('surname', kinds)
                row = c.execute('SELECT caption, relative_path FROM name_samples WHERE id=?', (sid,)).fetchone()
                self.assertEqual(row['caption'], '山田')
                self.assertEqual(row['relative_path'], 'images/names/sur.png')

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
                    'layout': 'Name',
                    'family': 'HG丸ゴシックM-PRO',
                    'widthMm': 80,
                    'heightMm': 50,
                    'maximumFont': 28,
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

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_match_template_finds_yamada_and_rejects_blank(self):
        """山田太郎検出を維持し、空画像は ink 比で誤検出しない。"""
        import time

        font = ImageFont.truetype(str(FONT), 28)
        bg = (255, 220, 220)
        hay = Image.new('RGB', (400, 160), bg)
        ImageDraw.Draw(hay).text((40, 50), '山田 太郎', font=font, fill=(0, 0, 0))
        needle = Image.new('RGB', (1, 1), bg)
        box = ImageDraw.Draw(Image.new('L', (1, 1))).textbbox((0, 0), '山田 太郎', font=font)
        tw, th = max(1, box[2] - box[0] + 4), max(1, box[3] - box[1] + 4)
        needle = Image.new('RGB', (tw, th), bg)
        ImageDraw.Draw(needle).text((2, 2), '山田 太郎', font=font, fill=(0, 0, 0))
        ink = names._ink_mass(needle, bg)
        self.assertGreaterEqual(ink, 400)

        t0 = time.perf_counter()
        match = names._match_template(hay, needle, step=2)
        elapsed = time.perf_counter() - t0
        self.assertIsNotNone(match)
        x, y, score = match
        self.assertLess(score, ink * 0.55)
        self.assertLess(abs(x - 38), 8)
        self.assertLess(abs(y - 48), 8)
        self.assertLess(elapsed, 2.0, f'match too slow: {elapsed:.3f}s')

        blank = Image.new('RGB', (400, 160), bg)
        blank_match = names._match_template(blank, needle, step=2)
        self.assertIsNotNone(blank_match)
        self.assertGreaterEqual(blank_match[2], ink * 0.55)

        # 純Python経路でも検出できること（numpy 無しフォールバック）
        prev = names._HAS_NUMPY
        try:
            names._HAS_NUMPY = False
            py_match = names._match_template(hay, needle, step=2)
            self.assertIsNotNone(py_match)
            self.assertLess(py_match[2], ink * 0.55)
        finally:
            names._HAS_NUMPY = prev

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_regenerate_emits_progress_and_changes_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src.png'
            dst = Path(tmp) / 'dst.png'
            _draw_name(src, '山田 太郎', bg=(240, 128, 128))
            events = []
            ok, note = names._regenerate_name_sample(
                src, dst,
                name_text='山田 花子',
                family_hint='HG丸ゴシックM-PRO',
                maximum_font=28,
                width_mm=80,
                height_mm=50,
                progress_callback=events.append,
            )
            self.assertTrue(ok, note)
            self.assertTrue(dst.is_file())
            self.assertNotEqual(src.read_bytes(), dst.read_bytes())
            tries = [e for e in events if e.get('phase') == 'image-try']
            self.assertGreaterEqual(len(tries), 1)
            self.assertEqual(tries[0].get('method'), 'regenerate')
            self.assertIn('size', tries[0])

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_nm000115_kobayashi_female_red_regenerates(self):
        """小林製麺相当: NM-000115 大ネーム(フルネーム) 女性/赤を山田花子へ再生成。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            shared = img_dir / 'nm000115.png'
            # 80×50 mm 相当キャンバス・赤用紙・氏名のみ
            font = ImageFont.truetype(str(FONT), 42)
            im = Image.new('RGB', (480, 300), (240, 128, 128))
            draw = ImageDraw.Draw(im)
            box = draw.textbbox((0, 0), '山田 太郎', font=font)
            tw, th = box[2] - box[0], box[3] - box[1]
            draw.text(((480 - tw) / 2 - box[0], (300 - th) / 2 - box[1]), '山田 太郎', font=font, fill=(0, 0, 0))
            shared.parent.mkdir(parents=True, exist_ok=True)
            im.save(shared)
            before = shared.read_bytes()

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'kobayashi', '小林製麺')
                fid = _add_format(
                    c, cust, 'nm-000115', '大ネーム(フルネーム)',
                    groups=['男性', '女性'],
                    layout='Name',
                    family='HG丸ゴシックM-PRO',
                    width_mm=80,
                    height_mm=50,
                    maximum_font=28,
                )
                male_id = _add_sample(
                    c, fid, key='male', condition='男性', paper='青',
                    path='images/names/nm000115.png', caption='山田 太郎',
                )
                female_id = _add_sample(
                    c, fid, key='female', condition='女性', paper='赤',
                    path='images/names/nm000115.png', caption='山田 太郎',
                )
                # 了承済みでも更新トリガで digest クリアされること（勝手に了承維持しない）
                c.execute(
                    'INSERT INTO name_reviews(format_id, assignee_id, confirmed_by, confirmed_name, confirmed_at, confirmed_digest)'
                    ' VALUES(?,?,?,?,?,?)',
                    (fid, 1, 1, '田中', '2026-01-01T00:00:00', 'old-digest'),
                )

                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report['changed'], 1, report)
                self.assertEqual(report['items'][0]['sampleId'], female_id)
                self.assertIn('regenerat', report['items'][0].get('imageAction', ''))

                female = c.execute('SELECT * FROM name_samples WHERE id=?', (female_id,)).fetchone()
                male = c.execute('SELECT * FROM name_samples WHERE id=?', (male_id,)).fetchone()
                self.assertEqual(female['caption'], '山田 花子')
                self.assertIn(names.FEMALE_IMAGE_SUFFIX, female['relative_path'])
                self.assertEqual(male['caption'], '山田 太郎')
                self.assertEqual(male['relative_path'], 'images/names/nm000115.png')
                self.assertEqual(shared.read_bytes(), before)
                forked = data_dir / female['relative_path']
                self.assertTrue(forked.is_file())
                self.assertNotEqual(forked.read_bytes(), before)

                review = c.execute(
                    'SELECT confirmed_digest FROM name_reviews WHERE format_id=?', (fid,),
                ).fetchone()
                self.assertIsNone(review['confirmed_digest'])

                again = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(again['changed'], 0)

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_nm000026_female_dept_conditions_regenerate(self):
        """NM-000026: 女性／と畜場・女性／加工場は部署名を残して山田花子。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            toshi = img_dir / 'nm000026-toshi.png'
            kako = img_dir / 'nm000026-kako.png'
            _draw_dept_name(toshi, 'と畜場', '山田 太郎', bg=(240, 128, 128))
            _draw_dept_name(kako, '加工場', '山田 太郎', bg=(240, 128, 128))
            before_toshi_dept = _region_bytes(toshi, (0, 0, 320, 55))
            before_kako_dept = _region_bytes(kako, (0, 0, 320, 55))

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'nm26', 'NM000026顧客')
                fid = _add_format(
                    c, cust, 'NM-000026', '部署と氏名',
                    groups=['女性／と畜場', '女性／加工場'],
                    layout='DeptName',
                    family='HG丸ゴシックM-PRO',
                    width_mm=80,
                    height_mm=35,
                    maximum_font=28,
                )
                id_toshi = _add_sample(
                    c, fid, key='toshi', condition='女性／と畜場', paper='赤',
                    path='images/names/nm000026-toshi.png', caption='山田 太郎',
                )
                id_kako = _add_sample(
                    c, fid, key='kako', condition='女性／加工場', paper='赤',
                    path='images/names/nm000026-kako.png', caption='山田 太郎',
                )
                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report['changed'], 2, report)
                changed_ids = {item['sampleId'] for item in report['items']}
                self.assertEqual(changed_ids, {id_toshi, id_kako})

                row_t = c.execute('SELECT * FROM name_samples WHERE id=?', (id_toshi,)).fetchone()
                row_k = c.execute('SELECT * FROM name_samples WHERE id=?', (id_kako,)).fetchone()
                self.assertEqual(row_t['caption'], '山田 花子')
                self.assertEqual(row_k['caption'], '山田 花子')
                self.assertIn(names.FEMALE_IMAGE_SUFFIX, row_t['relative_path'])
                self.assertIn(names.FEMALE_IMAGE_SUFFIX, row_k['relative_path'])
                forked_t = data_dir / row_t['relative_path']
                forked_k = data_dir / row_k['relative_path']
                self.assertTrue(forked_t.is_file())
                self.assertTrue(forked_k.is_file())
                self.assertEqual(_region_bytes(forked_t, (0, 0, 320, 55)), before_toshi_dept)
                self.assertEqual(_region_bytes(forked_k, (0, 0, 320, 55)), before_kako_dept)
                # 元画像は残す
                self.assertTrue(toshi.is_file())
                self.assertTrue(kako.is_file())

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_nm000115_keeps_done_female_and_male_taro(self):
        """NM-000115: 変更済み女性花子を維持し、男性太郎は変更しない。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            male_img = img_dir / 'nm000115.png'
            female_img = img_dir / f'nm000115{names.FEMALE_IMAGE_SUFFIX}.png'
            _draw_name(male_img, '山田 太郎', bg=(160, 180, 240))
            _draw_name(female_img, '山田 花子', bg=(240, 128, 128))
            male_before = male_img.read_bytes()
            female_before = female_img.read_bytes()

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'kobayashi', '小林製麺')
                fid = _add_format(
                    c, cust, 'NM-000115', '大ネーム(フルネーム)',
                    groups=['男性', '女性'],
                    layout='Name',
                )
                male_id = _add_sample(
                    c, fid, key='male', condition='男性', paper='青',
                    path='images/names/nm000115.png', caption='山田 太郎',
                )
                female_id = _add_sample(
                    c, fid, key='female', condition='女性', paper='赤',
                    path=f'images/names/nm000115{names.FEMALE_IMAGE_SUFFIX}.png',
                    caption='山田 花子',
                )
                c.execute(
                    "INSERT OR REPLACE INTO settings VALUES(?,?)",
                    (
                        names.FEMALE_HANAKO_SETTINGS_KEY,
                        json.dumps({
                            'at': '20261014-000000',
                            'changed': 1,
                            'ruleVersion': names.FEMALE_HANAKO_RULE_VERSION,
                            'completedSampleIds': [female_id],
                        }, ensure_ascii=False),
                    ),
                )
                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report.get('changed', 0), 0, report)
                female = c.execute('SELECT * FROM name_samples WHERE id=?', (female_id,)).fetchone()
                male = c.execute('SELECT * FROM name_samples WHERE id=?', (male_id,)).fetchone()
                self.assertEqual(female['caption'], '山田 花子')
                self.assertEqual(female['relative_path'], f'images/names/nm000115{names.FEMALE_IMAGE_SUFFIX}.png')
                self.assertEqual(male['caption'], '山田 太郎')
                self.assertEqual(male['relative_path'], 'images/names/nm000115.png')
                self.assertEqual(male_img.read_bytes(), male_before)
                self.assertEqual(female_img.read_bytes(), female_before)

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_headingname_and_splitnumber_regenerate(self):
        """HeadingName / SplitNumber も氏名だけ差し替え、他要素を保持。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            heading = img_dir / 'heading.png'
            split = img_dir / 'split.png'
            _draw_heading_name(heading, '小ネーム見本', '山田 太郎')
            _draw_split_number(split, '12', '山田 太郎', '34')
            before_heading = _region_bytes(heading, (0, 0, 320, 50))
            before_left = _region_bytes(split, (0, 0, 90, 100))
            before_right = _region_bytes(split, (290, 0, 360, 100))

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                cust = _add_customer(c, 'c1', '試験')
                hid = _add_format(
                    c, cust, 'id-h', '見出し付き', groups=['女性'],
                    layout='HeadingName', maximum_font=28, width_mm=80, height_mm=35,
                )
                sid_h = _add_sample(
                    c, hid, key='f', condition='女性用', paper='赤',
                    path='images/names/heading.png', caption='山田 太郎',
                )
                nid = _add_format(
                    c, cust, 'id-n', '番号左右', groups=['女性'],
                    layout='SplitNumber', maximum_font=28, width_mm=90, height_mm=25,
                )
                sid_n = _add_sample(
                    c, nid, key='f', condition='女性専用', paper='赤',
                    path='images/names/split.png', caption='山田 太郎',
                )
                report = names.correct_female_sample_names(c, data_dir, force=True)
                self.assertEqual(report['changed'], 2, report)
                rows = {
                    row['id']: row
                    for row in c.execute('SELECT id, caption, relative_path FROM name_samples')
                }
                self.assertEqual(rows[sid_h]['caption'], '山田 花子')
                self.assertEqual(rows[sid_n]['caption'], '山田 花子')
                forked_h = data_dir / rows[sid_h]['relative_path']
                forked_n = data_dir / rows[sid_n]['relative_path']
                self.assertEqual(_region_bytes(forked_h, (0, 0, 320, 50)), before_heading)
                self.assertEqual(_region_bytes(forked_n, (0, 0, 90, 100)), before_left)
                self.assertEqual(_region_bytes(forked_n, (290, 0, 360, 100)), before_right)


if __name__ == '__main__':
    unittest.main()
