"""鱗幸食品：Excel 230–231 行に合わせた大・小ネーム更新。"""
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


def _add_format(
    c, customer_id, identity, format_name, *, fixed_color='先方から指定', note='',
    placement='', family='', heading='', layout='', reading='', small=0,
    width=80, height=50, maximum_font=0, heading_font=0, spec=None,
    groups=None, colors=None, color_caption='',
):
    return c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, family, heading, note,
            layout, reading, small, width_mm, height_mm, fixed_color, color_caption,
            groups_json, colors_json, swatches_json, maximum_font, heading_font, spec_json, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
        (
            identity, customer_id, 1, format_name, placement, family, heading, note,
            layout, reading, small, width, height, fixed_color, color_caption,
            json.dumps(groups or [], ensure_ascii=False),
            json.dumps(colors or [], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            maximum_font, heading_font, json.dumps(spec or {'mixColors': list(names.RINKO_MIX_COLORS)}, ensure_ascii=False),
        ),
    ).lastrowid


def _add_sample(c, format_id, *, key='standard', paper='先方から指定', path='', caption=''):
    return c.execute(
        '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
           VALUES(?,?,?,?,?,?)''',
        (format_id, key, '', paper, path, caption),
    ).lastrowid


class RinkoNameFormatsTest(unittest.TestCase):
    def test_b2_generic_sizes(self):
        self.assertEqual(names._rinko_generic_font_pt('large', '山田太郎'), 48)
        self.assertEqual(names._rinko_generic_font_pt('large', '山田 太郎'), 48)
        self.assertEqual(names._rinko_generic_font_pt('large', 'あいうえお'), 38)
        self.assertEqual(names._rinko_generic_font_pt('large', 'あいうえおか'), 30)
        self.assertEqual(names._rinko_generic_font_pt('small', '山田太郎'), 26)
        self.assertEqual(names._rinko_generic_font_pt('small', 'あいうえお'), 24)
        self.assertEqual(names._rinko_generic_font_pt('small', 'あいうえおか'), 20)

    def test_band_heights_sum(self):
        large = names._rinko_large_bands()
        self.assertEqual(len(large), 2)
        self.assertAlmostEqual(sum(b['heightMm'] for b in large), 50.0, places=2)
        self.assertEqual(large[0]['fontPt'], 26)
        self.assertEqual(large[0]['alignH'], 'left')
        self.assertEqual(large[0]['alignV'], 'middle')
        self.assertEqual(large[0]['text'], '○○')
        self.assertNotIn('工場長', large[0]['text'])
        self.assertEqual(large[1]['alignH'], 'center')
        self.assertEqual(large[1]['alignV'], 'top')
        self.assertEqual(large[1]['fontPt'], 48)
        small = names._rinko_small_bands()
        self.assertEqual(len(small), 1)
        self.assertAlmostEqual(small[0]['heightMm'], 24.0, places=2)
        self.assertEqual(small[0]['fontPt'], 26)

    def test_mix_color_preserves_question_mark(self):
        self.assertTrue(names._is_mix_color('先方から指定？'))
        self.assertEqual(names._mix_color_label('先方から指定？'), '先方から指定？')
        self.assertEqual(names._mix_color_label('先方から指定'), '先方から指定')
        self.assertEqual(names._mix_color_label(''), '先方から指定')

    def test_updates_large_and_small_without_yellow_solo(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow未導入')

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            img_dir = data_dir / 'images' / 'names'
            old_large = img_dir / 'old-large.png'
            old_small = img_dir / 'old-small.png'
            old_yellow = img_dir / 'old-large-yellow.png'
            img_dir.mkdir(parents=True)
            old_large.write_bytes(b'\x89PNG\r\n\x1a\nold-large')
            old_small.write_bytes(b'\x89PNG\r\n\x1a\nold-small')
            old_yellow.write_bytes(b'\x89PNG\r\n\x1a\nold-yellow')

            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.schema(c)
                rinko = _add_customer(c, 'rinko', '鱗幸食品')
                other = _add_customer(c, 'other', '他社サンプル')
                large = _add_format(
                    c, rinko, 'id-large', '大ネーム',
                    fixed_color='先方から指定',
                    note='旧備考', placement='旧位置', family='MSPゴシック',
                    heading='旧', layout='Name', reading='苗字のみ',
                    width=80, height=50, maximum_font=36,
                    # Excel 段数欄が1でも、配置は2段へ直す前提の旧データ
                    spec={'mixColors': ['赤', '青', '緑', '白']},
                )
                small = _add_format(
                    c, rinko, 'id-small', '小ネーム',
                    fixed_color='先方から指定？',
                    note='旧小', placement='旧小位置', family='MSPゴシック',
                    heading='旧', layout='Name', reading='苗字のみ',
                    small=1, width=50, height=24, maximum_font=20,
                    spec={'mixColors': ['赤', '青', '緑', '白']},
                )
                other_fmt = _add_format(
                    c, other, 'id-other', '大ネーム',
                    fixed_color='先方から指定',
                    note='他社の備考', placement='他社位置', family='HG丸ゴシック',
                    heading='他社', width=80, height=50,
                )
                _add_sample(c, large, path='images/names/old-large.png', caption='旧大')
                _add_sample(
                    c, large, key='yellow', paper='黄色',
                    path='images/names/old-large-yellow.png', caption='黄色専用',
                )
                _add_sample(c, small, paper='先方から指定？', path='images/names/old-small.png', caption='旧小')
                _add_sample(c, other_fmt, path='', caption='他社見本')

                c.execute(
                    'INSERT INTO name_reviews(format_id, assignee_id, source_label, confirmed_digest, confirmed_at, confirmed_name) VALUES(?,?,?,?,?,?)',
                    (large, 3, '田中', 'old-digest', '2026-01-01T00:00:00', '田中'),
                )
                c.execute(
                    'INSERT INTO name_reviews(format_id, assignee_id, source_label) VALUES(?,?,?)',
                    (small, 3, '田中'),
                )

                report = names.correct_rinko_name_formats(c, data_dir)
                self.assertEqual(report['changed'], 2)
                self.assertEqual(report['policy'], names.RINKO_NAMES_POLICY)
                self.assertEqual(report['b2']['largeGenericPt'][4], 48)
                self.assertEqual(report['b2']['smallGenericPt'][4], 26)

                fmt_large = c.execute('SELECT * FROM name_formats WHERE id=?', (large,)).fetchone()
                self.assertEqual(fmt_large['format_name'], names.RINKO_LARGE_FORMAT_NAME)
                self.assertEqual(fmt_large['placement'], names.RINKO_LARGE_PLACEMENT)
                self.assertEqual(fmt_large['family'], names.RINKO_FAMILY)
                self.assertEqual(fmt_large['layout'], 'HeadingName')
                self.assertEqual(fmt_large['reading'], 'フルネーム')
                self.assertEqual(fmt_large['heading'], names.RINKO_LARGE_HEADING)
                self.assertEqual(fmt_large['note'], names.RINKO_LARGE_NOTE)
                self.assertIn('上下2段', fmt_large['note'])
                self.assertNotIn('3段', fmt_large['note'])
                self.assertEqual(fmt_large['width_mm'], 80)
                self.assertEqual(fmt_large['height_mm'], 50)
                self.assertEqual(fmt_large['heading_font'], 26)
                self.assertEqual(fmt_large['maximum_font'], 48)
                self.assertEqual(fmt_large['source_row'], 230)
                self.assertEqual(fmt_large['fixed_color'], '先方から指定')
                self.assertEqual(fmt_large['color_caption'], names.RINKO_COLOR_CAPTION)
                self.assertEqual(json.loads(fmt_large['groups_json']), names.RINKO_COLOR_GROUPS)
                self.assertEqual(json.loads(fmt_large['colors_json']), names.RINKO_COLOR_VALUES)
                self.assertEqual(names.mix_colors_for(fmt_large), names.RINKO_MIX_COLORS)
                self.assertIn('工場長', fmt_large['note'])
                self.assertIn('黄色', fmt_large['note'])

                fmt_small = c.execute('SELECT * FROM name_formats WHERE id=?', (small,)).fetchone()
                self.assertEqual(fmt_small['format_name'], names.RINKO_SMALL_FORMAT_NAME)
                self.assertEqual(fmt_small['placement'], names.RINKO_SMALL_PLACEMENT)
                self.assertEqual(fmt_small['family'], names.RINKO_FAMILY)
                self.assertEqual(fmt_small['layout'], 'Name')
                self.assertEqual(fmt_small['small'], 1)
                self.assertEqual(fmt_small['width_mm'], 50)
                self.assertEqual(fmt_small['height_mm'], 24)
                self.assertEqual(fmt_small['maximum_font'], 26)
                self.assertEqual(fmt_small['heading_font'], 0)
                self.assertEqual(fmt_small['source_row'], 231)
                # 疑問符付き原文は維持
                self.assertEqual(fmt_small['fixed_color'], '先方から指定？')
                self.assertNotIn('上段帯', fmt_small['note'])

                # 見本は先方指定1件のみ。黄色専用は削除
                large_samples = list(c.execute(
                    'SELECT * FROM name_samples WHERE format_id=? ORDER BY id', (large,),
                ))
                self.assertEqual(len(large_samples), 1)
                self.assertEqual(large_samples[0]['paper_color'], '先方から指定')
                self.assertEqual(large_samples[0]['caption'], names.RINKO_LARGE_SAMPLE_CAPTION)
                self.assertIn('rinko-large', large_samples[0]['relative_path'])
                self.assertFalse(old_yellow.exists())

                small_samples = list(c.execute(
                    'SELECT * FROM name_samples WHERE format_id=? ORDER BY id', (small,),
                ))
                self.assertEqual(len(small_samples), 1)
                self.assertEqual(small_samples[0]['paper_color'], '先方から指定？')
                self.assertIn('rinko-small', small_samples[0]['relative_path'])

                with Image.open(data_dir / large_samples[0]['relative_path']) as im:
                    self.assertEqual(im.size, (800, 500))
                with Image.open(data_dir / small_samples[0]['relative_path']) as im2:
                    self.assertEqual(im2.size, (500, 240))

                # 他社は不変
                other_row = c.execute('SELECT * FROM name_formats WHERE id=?', (other_fmt,)).fetchone()
                self.assertEqual(other_row['note'], '他社の備考')
                self.assertEqual(other_row['placement'], '他社位置')

                # 担当者は維持。了承 digest は内容変更で無効化のみ
                review = c.execute(
                    'SELECT assignee_id, source_label, confirmed_digest FROM name_reviews WHERE format_id=?',
                    (large,),
                ).fetchone()
                self.assertEqual(review['assignee_id'], 3)
                self.assertEqual(review['source_label'], '田中')
                self.assertIsNone(review['confirmed_digest'])

                backup = data_dir / report['backupDir']
                self.assertTrue((backup / f'format-{large}.json').is_file())
                self.assertTrue((backup / 'images' / f'{large}-old-large.png').is_file())

                # 再実行で戻らない・重複追加しない
                again = names.correct_rinko_name_formats(c, data_dir)
                self.assertEqual(again['changed'], 0)
                count = c.execute(
                    'SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND retired=0',
                    (rinko,),
                ).fetchone()['n']
                self.assertEqual(count, 2)
                yellow_n = c.execute(
                    "SELECT COUNT(*) n FROM name_samples WHERE format_id IN (?,?) AND paper_color IN ('黄色','黄')",
                    (large, small),
                ).fetchone()['n']
                self.assertEqual(yellow_n, 0)

    def test_does_not_add_duplicate_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.schema(c)
                rinko = _add_customer(c, 'rinko', '鱗幸食品')
                # 小だけある
                small = _add_format(
                    c, rinko, 'id-small', '小ネーム（フルネーム）',
                    small=1, width=50, height=24,
                    spec={'mixColors': list(names.RINKO_MIX_COLORS)},
                )
                _add_sample(c, small, path='')
                report = names.correct_rinko_name_formats(c, data_dir)
                self.assertEqual(report['changed'], 1)
                actions = {i['kind']: i['action'] for i in report['items']}
                self.assertEqual(actions.get('large'), 'missing')
                self.assertEqual(actions.get('small'), 'update')
                n = c.execute(
                    'SELECT COUNT(*) n FROM name_formats WHERE customer_id=?', (rinko,),
                ).fetchone()['n']
                self.assertEqual(n, 1)

    def test_catalog_removes_yellow_solo_and_applies_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            catalog_dir = data_dir / 'name-import'
            catalog_dir.mkdir(parents=True)
            catalog = {
                'customers': [{'sourceKey': 'rinko', 'name': '鱗幸食品'}],
                'formats': [{
                    'dbId': 1,
                    'customerKey': 'rinko',
                    'format': '大ネーム（上段自由入力・氏名）',
                    'fixedColor': '先方から指定',
                    'mixColors': ['赤', '青', '緑', '白'],
                    'note': '旧',
                    'samples': [
                        {'key': 'mix', 'paperColor': '先方から指定', 'relativePath': 'images/names/a.png', 'caption': 'a'},
                        {'key': 'yellow', 'paperColor': '黄色', 'relativePath': 'images/names/a-yellow.png', 'caption': 'y'},
                    ],
                }],
            }
            (catalog_dir / 'catalog.json').write_text(
                json.dumps(catalog, ensure_ascii=False), encoding='utf-8',
            )
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.schema(c)
                rinko = _add_customer(c, 'rinko', '鱗幸食品')
                # dbId と揃えるため id を明示せず、patch は identity/name でも探す
                large = _add_format(
                    c, rinko, 'id-large', '大ネーム（上段自由入力・氏名）',
                    width=80, height=50,
                )
                # catalog dbId を実 id に合わせる
                data = json.loads((catalog_dir / 'catalog.json').read_text(encoding='utf-8'))
                data['formats'][0]['dbId'] = large
                (catalog_dir / 'catalog.json').write_text(
                    json.dumps(data, ensure_ascii=False), encoding='utf-8',
                )
                _add_sample(c, large, path='images/names/a.png')
                _add_sample(c, large, key='yellow', paper='黄色', path='images/names/a-yellow.png')

                report = names.correct_rinko_name_formats(c, data_dir)
                self.assertGreaterEqual(report['changed'], 1)
                patched = json.loads((catalog_dir / 'catalog.json').read_text(encoding='utf-8'))
                fmt = patched['formats'][0]
                self.assertEqual(fmt['placement'], names.RINKO_LARGE_PLACEMENT)
                self.assertEqual(fmt['mixColors'], names.RINKO_MIX_COLORS)
                self.assertEqual(fmt['groups'], names.RINKO_COLOR_GROUPS)
                papers = [s.get('paperColor') for s in fmt['samples']]
                self.assertEqual(papers, ['先方から指定'])
                self.assertTrue(all(not names._is_yellow_color(p) for p in papers))


if __name__ == '__main__':
    unittest.main()
