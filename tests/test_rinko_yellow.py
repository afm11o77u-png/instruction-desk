"""鱗幸食品の先方から指定へ黄色を追加（他顧客は変更しない）。"""
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


def _add_format(c, customer_id, identity, format_name, *, fixed_color='', note='', color_caption='', groups=None, colors=None):
    groups = groups or []
    colors = colors if colors is not None else (['赤'] * len(groups))
    return c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, note, fixed_color, color_caption,
            groups_json, colors_json, swatches_json, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)''',
        (
            identity, customer_id, 1, format_name, '胸', note, fixed_color, color_caption,
            json.dumps(groups, ensure_ascii=False),
            json.dumps(colors, ensure_ascii=False),
            json.dumps(['#FFFFFF'] * len(groups), ensure_ascii=False),
        ),
    ).lastrowid


def _add_sample(c, format_id, *, key='standard', condition='', paper='', path='', caption=''):
    return c.execute(
        '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
           VALUES(?,?,?,?,?,?)''',
        (format_id, key, condition, paper, path, caption),
    ).lastrowid


def _draw_name(path, text, fill=(0, 0, 0), bg=(255, 255, 255)):
    font = ImageFont.truetype(str(FONT), 28)
    im = Image.new('RGB', (240, 64), bg)
    ImageDraw.Draw(im).text((16, 14), text, font=font, fill=fill)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


class RinkoYellowTest(unittest.TestCase):
    def test_append_yellow_keeps_existing(self):
        self.assertEqual(
            names._append_yellow_to_text('色は先方指定（赤・青・緑・白）'),
            '色は先方指定（赤・青・緑・白・黄色）',
        )
        self.assertEqual(
            names._append_yellow_to_text('赤・青・緑・白・黄色'),
            '赤・青・緑・白・黄色',
        )
        self.assertEqual(names._append_yellow_to_text('特記なし'), '特記なし')

    def test_mix_colors_default_and_spec(self):
        self.assertEqual(names.mix_colors_for({'spec_json': '{}'}), ['赤', '青', '緑', '白'])
        self.assertEqual(
            names.mix_colors_for({'spec_json': json.dumps({'mixColors': names.RINKO_MIX_COLORS})}),
            names.RINKO_MIX_COLORS,
        )

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_only_rinko_mix_gets_yellow(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            rinko_img = img_dir / 'rinko-mix.png'
            other_img = img_dir / 'other-mix.png'
            _draw_name(rinko_img, '山田 太郎')
            _draw_name(other_img, '山田 太郎', bg=(255, 240, 240))

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                rinko = _add_customer(c, 'rinko', '鱗幸食品')
                other = _add_customer(c, 'other', '他社サンプル')
                rinko_mix = _add_format(
                    c, rinko, 'id-rinko-mix', '大ネーム',
                    fixed_color='先方から指定',
                    note='色は先方指定（赤・青・緑・白）。4色',
                )
                rinko_red = _add_format(c, rinko, 'id-rinko-red', '赤固定', fixed_color='赤')
                other_mix = _add_format(c, other, 'id-other-mix', '先方指定', fixed_color='先方から指定')

                rinko_sample = _add_sample(
                    c, rinko_mix, key='mix', paper='先方から指定',
                    path='images/names/rinko-mix.png', caption='見本 山田 太郎',
                )
                _add_sample(c, rinko_red, key='red', paper='赤', path='', caption='赤のみ')
                other_sample = _add_sample(
                    c, other_mix, key='mix', paper='先方から指定',
                    path='images/names/other-mix.png', caption='他社見本',
                )

                # 了承行があっても勝手に了承し直さない（digest クリアのみ）
                c.execute(
                    'INSERT INTO name_reviews(format_id, assignee_id, confirmed_digest, confirmed_at, confirmed_name) VALUES(?,?,?,?,?)',
                    (rinko_mix, 1, 'old-digest', '2026-01-01T00:00:00', '担当'),
                )

                report = names.correct_rinko_yellow(c, data_dir)
                self.assertEqual(report['changed'], 1)
                self.assertEqual(report['items'][0]['formatId'], rinko_mix)
                self.assertEqual(report['items'][0]['mixColorsAfter'], names.RINKO_MIX_COLORS)

                fmt = c.execute('SELECT * FROM name_formats WHERE id=?', (rinko_mix,)).fetchone()
                self.assertEqual(names.mix_colors_for(fmt), names.RINKO_MIX_COLORS)
                self.assertIn('黄色', fmt['note'])
                self.assertIn('5色', fmt['note'])

                yellow = c.execute(
                    "SELECT * FROM name_samples WHERE format_id=? AND paper_color='黄色'",
                    (rinko_mix,),
                ).fetchone()
                self.assertIsNotNone(yellow)
                self.assertEqual(yellow['condition_label'], '')
                self.assertNotIn('女性', yellow['condition_label'])
                self.assertTrue(yellow['relative_path'])
                self.assertIn(names.RINKO_YELLOW_IMAGE_SUFFIX, yellow['relative_path'])
                self.assertTrue((data_dir / yellow['relative_path']).is_file())
                self.assertTrue(rinko_img.is_file())
                self.assertNotEqual(rinko_img.read_bytes(), (data_dir / yellow['relative_path']).read_bytes())

                # 既存の先方指定見本は残る
                self.assertEqual(
                    c.execute('SELECT COUNT(*) n FROM name_samples WHERE format_id=?', (rinko_mix,)).fetchone()['n'],
                    2,
                )
                self.assertEqual(
                    c.execute('SELECT paper_color FROM name_samples WHERE id=?', (rinko_sample,)).fetchone()['paper_color'],
                    '先方から指定',
                )

                # 赤固定・他顧客は変更なし
                self.assertIsNone(
                    c.execute(
                        "SELECT 1 FROM name_samples WHERE format_id=? AND paper_color='黄色'",
                        (rinko_red,),
                    ).fetchone()
                )
                other_fmt = c.execute('SELECT * FROM name_formats WHERE id=?', (other_mix,)).fetchone()
                self.assertEqual(names.mix_colors_for(other_fmt), names.DEFAULT_MIX_COLORS)
                self.assertIsNone(
                    c.execute(
                        "SELECT 1 FROM name_samples WHERE format_id=? AND paper_color='黄色'",
                        (other_mix,),
                    ).fetchone()
                )
                self.assertEqual(
                    c.execute('SELECT relative_path FROM name_samples WHERE id=?', (other_sample,)).fetchone()['relative_path'],
                    'images/names/other-mix.png',
                )

                review = c.execute('SELECT confirmed_digest FROM name_reviews WHERE format_id=?', (rinko_mix,)).fetchone()
                self.assertIsNone(review['confirmed_digest'])

                backup = data_dir / report['backupDir']
                self.assertTrue(backup.is_dir())
                self.assertTrue((backup / 'report.json').is_file())
                self.assertTrue((data_dir / names.RINKO_YELLOW_REPORT).is_file())

                again = names.correct_rinko_yellow(c, data_dir)
                self.assertEqual(again['changed'], 0)

    @unittest.skipUnless(HAS_PIL and FONT.is_file(), 'PillowとCJKフォントが必要')
    def test_catalog_import_then_yellow(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            import_dir = data_dir / 'name-import'
            import_dir.mkdir()
            img = data_dir / 'images' / 'names' / 'rinko.png'
            _draw_name(img, '山田太郎')
            catalog = {
                'customerCount': 2,
                'formatCount': 2,
                'imageCount': 1,
                'customers': [
                    {'sourceKey': 'rinko', 'sourceIndex': 1, 'name': '鱗幸食品', 'reading': ''},
                    {'sourceKey': 'other', 'sourceIndex': 2, 'name': '別会社', 'reading': ''},
                ],
                'formats': [
                    {
                        'identity': 'fmt-rinko',
                        'customerKey': 'rinko',
                        'sourceKind': 1,
                        'format': '大',
                        'fixedColor': '先方から指定',
                        'note': '（赤・青・緑・白）',
                        'groups': [],
                        'colors': [],
                        'colorSwatches': [],
                        'samples': [{
                            'key': 'mix',
                            'condition': '',
                            'paperColor': '先方から指定',
                            'relativePath': 'images/names/rinko.png',
                            'caption': '山田太郎',
                        }],
                    },
                    {
                        'identity': 'fmt-other',
                        'customerKey': 'other',
                        'sourceKind': 1,
                        'format': '大',
                        'fixedColor': '先方から指定',
                        'groups': [],
                        'colors': [],
                        'colorSwatches': [],
                        'samples': [{
                            'key': 'mix',
                            'condition': '',
                            'paperColor': '先方から指定',
                            'relativePath': '',
                            'caption': '別会社',
                        }],
                    },
                ],
            }
            (import_dir / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
            db = data_dir / 'desk.sqlite3'

            def connect():
                return _connect(db)

            names.import_catalog(connect, data_dir)
            with connect() as c:
                rinko_fmt = c.execute(
                    '''SELECT f.* FROM name_formats f
                       JOIN name_customers cu ON cu.id=f.customer_id
                       WHERE cu.source_name=?''',
                    ('鱗幸食品',),
                ).fetchone()
                self.assertEqual(names.mix_colors_for(rinko_fmt), names.RINKO_MIX_COLORS)
                yellow = c.execute(
                    "SELECT * FROM name_samples WHERE format_id=? AND paper_color='黄色'",
                    (rinko_fmt['id'],),
                ).fetchone()
                self.assertIsNotNone(yellow)
                other_fmt = c.execute(
                    '''SELECT f.* FROM name_formats f
                       JOIN name_customers cu ON cu.id=f.customer_id
                       WHERE cu.source_name=?''',
                    ('別会社',),
                ).fetchone()
                self.assertEqual(names.mix_colors_for(other_fmt), names.DEFAULT_MIX_COLORS)
                self.assertIsNone(
                    c.execute(
                        "SELECT 1 FROM name_samples WHERE format_id=? AND paper_color='黄色'",
                        (other_fmt['id'],),
                    ).fetchone()
                )
            data = json.loads((import_dir / 'catalog.json').read_text(encoding='utf-8'))
            rinko_cat = next(f for f in data['formats'] if f['identity'] == 'fmt-rinko')
            self.assertEqual(rinko_cat['mixColors'], names.RINKO_MIX_COLORS)
            self.assertTrue(any(s.get('paperColor') == '黄色' for s in rinko_cat['samples']))
            other_cat = next(f for f in data['formats'] if f['identity'] == 'fmt-other')
            self.assertNotEqual(other_cat.get('mixColors'), names.RINKO_MIX_COLORS)


if __name__ == '__main__':
    unittest.main()
