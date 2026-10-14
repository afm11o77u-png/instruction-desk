"""女性用ネーム見本の架空氏名（山田太郎→山田花子）。"""
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
        self.assertFalse(names._sample_is_explicit_female(
            {'condition_label': '', 'paper_color': '赤', 'variant_key': 'red'},
            {'format_name': '大', 'groups_json': '[]', 'fixed_color': '赤'},
        ))
        self.assertFalse(names._sample_is_explicit_female(
            {'condition_label': '男性用', 'paper_color': '青', 'variant_key': 'm'},
            {'format_name': '大', 'groups_json': '["男性用","女性用"]'},
        ))
        self.assertFalse(names._sample_is_explicit_female(
            {'condition_label': '', 'paper_color': '', 'variant_key': 'x'},
            {'format_name': '大', 'groups_json': '["男性用","女性用"]'},
        ))
        self.assertTrue(names._sample_is_explicit_female(
            {'condition_label': '', 'paper_color': '', 'variant_key': 'x'},
            {'format_name': '女性用ネーム', 'groups_json': '[]', 'note': ''},
        ))

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
                # schema may no-op without samples; clear auto report noise
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

                report = names.correct_female_sample_names(c, data_dir)
                self.assertEqual(report['changed'], 1)
                self.assertEqual(report['items'][0]['sampleId'], female_id)

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

                # 了承は勝手に付けない（digestクリアはトリガ任せ。confirmed行が無ければ何もしない）
                self.assertIsNone(c.execute('SELECT confirmed_digest FROM name_reviews WHERE format_id=?', (fid,)).fetchone())

                # 再実行は idempotent
                again = names.correct_female_sample_names(c, data_dir)
                self.assertEqual(again['changed'], 0)

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_catalog_json_caption_updated(self):
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
                self.assertEqual(row['caption'], '表示は山田花子')
            data = json.loads((import_dir / 'catalog.json').read_text(encoding='utf-8'))
            sample = data['formats'][0]['samples'][0]
            self.assertEqual(sample['caption'], '表示は山田花子')


if __name__ == '__main__':
    unittest.main()
