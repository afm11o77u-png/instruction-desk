"""鱗幸食品の先方から指定：黄色は色一覧のみ。黄色専用見本は削除し再追加しない。"""
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
import zlib
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


def _add_format(c, customer_id, identity, format_name, *, fixed_color='', note='', color_caption='', groups=None, colors=None, spec=None):
    groups = groups or []
    colors = colors if colors is not None else (['赤'] * len(groups))
    return c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, note, fixed_color, color_caption,
            groups_json, colors_json, swatches_json, spec_json, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)''',
        (
            identity, customer_id, 1, format_name, '胸', note, fixed_color, color_caption,
            json.dumps(groups, ensure_ascii=False),
            json.dumps(colors, ensure_ascii=False),
            json.dumps(['#FFFFFF'] * len(groups), ensure_ascii=False),
            json.dumps(spec or {}, ensure_ascii=False),
        ),
    ).lastrowid


def _add_sample(c, format_id, *, key='standard', condition='', paper='', path='', caption=''):
    return c.execute(
        '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
           VALUES(?,?,?,?,?,?)''',
        (format_id, key, condition, paper, path, caption),
    ).lastrowid


def _png_chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)


def _draw_name(path, text, fill=(0, 0, 0), bg=(255, 255, 255)):
    """テスト用の小さな PNG（Pillow 不要）。text は区別用にファイルへ埋め込む。"""
    del fill  # 色分けは bg のみ
    width, height = 8, 4
    raw = b''.join(
        b'\x00' + bytes(bg) * width
        for _ in range(height)
    )
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    png = (
        b'\x89PNG\r\n\x1a\n'
        + _png_chunk(b'IHDR', ihdr)
        + _png_chunk(b'IDAT', zlib.compress(raw, 9))
        + _png_chunk(b'tEXt', b'title\x00' + str(text or '').encode('utf-8', 'replace'))
        + _png_chunk(b'IEND', b'')
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _yellow_sample_count(c, format_id):
    return c.execute(
        "SELECT COUNT(*) n FROM name_samples WHERE format_id=? AND paper_color IN ('黄色','黄')",
        (format_id,),
    ).fetchone()['n']


def _sample_papers(c, format_id):
    return [
        row['paper_color']
        for row in c.execute(
            'SELECT paper_color FROM name_samples WHERE format_id=? ORDER BY id',
            (format_id,),
        )
    ]


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

    def test_adds_mix_yellow_without_solo_sample(self):
        """新規適用：色一覧へ黄色を足すが、黄色専用見本は作らない。"""
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

                c.execute(
                    'INSERT INTO name_reviews(format_id, assignee_id, confirmed_digest, confirmed_at, confirmed_name) VALUES(?,?,?,?,?)',
                    (rinko_mix, 1, 'old-digest', '2026-01-01T00:00:00', '担当'),
                )
                review_before = c.execute(
                    'SELECT assignee_id FROM name_reviews WHERE format_id=?',
                    (rinko_mix,),
                ).fetchone()

                report = names.correct_rinko_yellow(c, data_dir)
                self.assertEqual(report['changed'], 1)
                self.assertEqual(report['policy'], names.RINKO_YELLOW_POLICY)
                self.assertEqual(report['items'][0]['formatId'], rinko_mix)
                self.assertEqual(report['items'][0]['mixColorsAfter'], names.RINKO_MIX_COLORS)
                self.assertEqual(report['items'][0]['removedYellowSamples'], [])

                fmt = c.execute('SELECT * FROM name_formats WHERE id=?', (rinko_mix,)).fetchone()
                self.assertEqual(names.mix_colors_for(fmt), names.RINKO_MIX_COLORS)
                self.assertIn('黄色', fmt['note'])
                self.assertIn('5色', fmt['note'])

                # 大・小とも「先方から指定」見本だけ（黄色専用は無し）
                self.assertEqual(_sample_papers(c, rinko_mix), ['先方から指定'])
                self.assertEqual(_yellow_sample_count(c, rinko_mix), 0)
                self.assertEqual(
                    c.execute('SELECT COUNT(*) n FROM name_samples WHERE format_id=?', (rinko_mix,)).fetchone()['n'],
                    1,
                )
                self.assertEqual(
                    c.execute('SELECT paper_color FROM name_samples WHERE id=?', (rinko_sample,)).fetchone()['paper_color'],
                    '先方から指定',
                )
                self.assertFalse((data_dir / 'images' / 'names' / 'rinko-mix-yellow.png').exists())

                self.assertEqual(_yellow_sample_count(c, rinko_red), 0)
                other_fmt = c.execute('SELECT * FROM name_formats WHERE id=?', (other_mix,)).fetchone()
                self.assertEqual(names.mix_colors_for(other_fmt), names.DEFAULT_MIX_COLORS)
                self.assertEqual(_yellow_sample_count(c, other_mix), 0)
                self.assertEqual(
                    c.execute('SELECT relative_path FROM name_samples WHERE id=?', (other_sample,)).fetchone()['relative_path'],
                    'images/names/other-mix.png',
                )

                review = c.execute(
                    'SELECT assignee_id, confirmed_digest FROM name_reviews WHERE format_id=?',
                    (rinko_mix,),
                ).fetchone()
                self.assertEqual(review['assignee_id'], review_before['assignee_id'])
                self.assertIsNone(review['confirmed_digest'])

                backup = data_dir / report['backupDir']
                self.assertTrue(backup.is_dir())
                self.assertTrue((backup / 'report.json').is_file())
                self.assertTrue((data_dir / names.RINKO_YELLOW_REPORT).is_file())

                again = names.correct_rinko_yellow(c, data_dir)
                self.assertEqual(again['changed'], 0)
                self.assertEqual(_sample_papers(c, rinko_mix), ['先方から指定'])

    def test_removes_existing_solo_yellow_samples(self):
        """#99 で追加済みの黄色専用見本を大・小から削除し、5色見本だけ残す。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            db = data_dir / 'desk.sqlite3'
            img_dir = data_dir / 'images' / 'names'
            large_img = img_dir / 'rinko-large.png'
            small_img = img_dir / 'rinko-small.png'
            large_yellow = img_dir / 'rinko-large-yellow.png'
            small_yellow = img_dir / 'rinko-small-yellow.png'
            _draw_name(large_img, '上段 氏名')
            _draw_name(small_img, 'フルネーム')
            _draw_name(large_yellow, '上段 氏名', bg=(255, 255, 224))
            _draw_name(small_yellow, 'フルネーム', bg=(255, 255, 224))

            with _connect(db) as c:
                names.bind(data_dir)
                names.schema(c)
                rinko = _add_customer(c, 'rinko', '鱗幸食品')
                large = _add_format(
                    c, rinko, 'id-large', '大ネーム（上段自由入力・氏名）',
                    fixed_color='先方から指定',
                    note='色は先方指定（赤・青・緑・白・黄色）。5色',
                    spec={'mixColors': list(names.RINKO_MIX_COLORS)},
                )
                small = _add_format(
                    c, rinko, 'id-small', '小ネーム（フルネーム）',
                    fixed_color='先方から指定',
                    note='色は先方指定（赤・青・緑・白・黄色）。5色',
                    spec={'mixColors': list(names.RINKO_MIX_COLORS)},
                )
                # 設定キーだけ旧ポリシー（黄色見本あり）のまま
                c.execute(
                    "INSERT OR REPLACE INTO settings VALUES(?,?)",
                    (names.RINKO_YELLOW_SETTINGS_KEY, json.dumps({
                        'at': 'old', 'changed': 2, 'mixColors': list(names.RINKO_MIX_COLORS),
                    })),
                )

                _add_sample(
                    c, large, key='mix', paper='先方から指定',
                    path='images/names/rinko-large.png', caption='見本',
                )
                yellow_large = _add_sample(
                    c, large, key='yellow', paper='黄色',
                    path='images/names/rinko-large-yellow.png', caption='見本',
                )
                _add_sample(
                    c, small, key='mix', paper='先方から指定',
                    path='images/names/rinko-small.png', caption='NM-000155',
                )
                yellow_small = _add_sample(
                    c, small, key='yellow', paper='黄色',
                    path='images/names/rinko-small-yellow.png', caption='NM-000155',
                )
                c.execute(
                    'INSERT INTO name_reviews(format_id, assignee_id, confirmed_digest, confirmed_at, confirmed_name) VALUES(?,?,?,?,?)',
                    (large, 7, 'digest-large', '2026-01-01T00:00:00', '担当A'),
                )
                c.execute(
                    'INSERT INTO name_reviews(format_id, assignee_id, confirmed_digest, confirmed_at, confirmed_name) VALUES(?,?,?,?,?)',
                    (small, 8, 'digest-small', '2026-01-01T00:00:00', '担当B'),
                )

                self.assertEqual(_yellow_sample_count(c, large), 1)
                self.assertEqual(_yellow_sample_count(c, small), 1)

                report = names.correct_rinko_yellow(c, data_dir)
                self.assertEqual(report['changed'], 2)
                self.assertEqual(report['policy'], names.RINKO_YELLOW_POLICY)

                self.assertEqual(_sample_papers(c, large), ['先方から指定'])
                self.assertEqual(_sample_papers(c, small), ['先方から指定'])
                self.assertEqual(_yellow_sample_count(c, large), 0)
                self.assertEqual(_yellow_sample_count(c, small), 0)
                self.assertIsNone(
                    c.execute('SELECT 1 FROM name_samples WHERE id=?', (yellow_large,)).fetchone()
                )
                self.assertIsNone(
                    c.execute('SELECT 1 FROM name_samples WHERE id=?', (yellow_small,)).fetchone()
                )

                # mixColors・説明の黄色は残る
                for fid in (large, small):
                    fmt = c.execute('SELECT * FROM name_formats WHERE id=?', (fid,)).fetchone()
                    self.assertEqual(names.mix_colors_for(fmt), names.RINKO_MIX_COLORS)
                    self.assertIn('黄色', fmt['note'])

                self.assertFalse(large_yellow.exists())
                self.assertFalse(small_yellow.exists())
                self.assertTrue(large_img.is_file())
                self.assertTrue(small_img.is_file())

                backup = data_dir / report['backupDir']
                self.assertTrue((backup / 'yellow-samples.json').is_file())
                self.assertTrue((backup / 'images' / 'rinko-large-yellow.png').is_file())
                self.assertTrue((backup / 'images' / 'rinko-small-yellow.png').is_file())

                for fid, assignee in ((large, 7), (small, 8)):
                    review = c.execute(
                        'SELECT assignee_id, confirmed_digest FROM name_reviews WHERE format_id=?',
                        (fid,),
                    ).fetchone()
                    self.assertEqual(review['assignee_id'], assignee)
                    self.assertIsNone(review['confirmed_digest'])

                setting = json.loads(
                    c.execute(
                        "SELECT value FROM settings WHERE key=?",
                        (names.RINKO_YELLOW_SETTINGS_KEY,),
                    ).fetchone()['value']
                )
                self.assertEqual(setting['policy'], names.RINKO_YELLOW_POLICY)

                again = names.correct_rinko_yellow(c, data_dir)
                self.assertEqual(again['changed'], 0)
                self.assertEqual(_sample_papers(c, large), ['先方から指定'])
                self.assertEqual(_sample_papers(c, small), ['先方から指定'])

    def test_catalog_import_keeps_mix_without_solo_yellow(self):
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
                        'samples': [
                            {
                                'key': 'mix',
                                'condition': '',
                                'paperColor': '先方から指定',
                                'relativePath': 'images/names/rinko.png',
                                'caption': '山田太郎',
                            },
                            {
                                'key': 'yellow',
                                'condition': '',
                                'paperColor': '黄色',
                                'relativePath': 'images/names/rinko-yellow.png',
                                'caption': '山田太郎',
                            },
                        ],
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
                self.assertEqual(_sample_papers(c, rinko_fmt['id']), ['先方から指定'])
                self.assertEqual(_yellow_sample_count(c, rinko_fmt['id']), 0)
                other_fmt = c.execute(
                    '''SELECT f.* FROM name_formats f
                       JOIN name_customers cu ON cu.id=f.customer_id
                       WHERE cu.source_name=?''',
                    ('別会社',),
                ).fetchone()
                self.assertEqual(names.mix_colors_for(other_fmt), names.DEFAULT_MIX_COLORS)
                self.assertEqual(_yellow_sample_count(c, other_fmt['id']), 0)
            data = json.loads((import_dir / 'catalog.json').read_text(encoding='utf-8'))
            rinko_cat = next(f for f in data['formats'] if f['identity'] == 'fmt-rinko')
            self.assertEqual(rinko_cat['mixColors'], names.RINKO_MIX_COLORS)
            self.assertFalse(any(names._is_yellow_color(s.get('paperColor')) for s in rinko_cat['samples']))
            self.assertTrue(any(s.get('paperColor') == '先方から指定' for s in rinko_cat['samples']))
            self.assertIn('黄色', rinko_cat.get('note') or '')
            other_cat = next(f for f in data['formats'] if f['identity'] == 'fmt-other')
            self.assertNotEqual(other_cat.get('mixColors'), names.RINKO_MIX_COLORS)


if __name__ == '__main__':
    unittest.main()
