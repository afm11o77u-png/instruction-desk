"""鱗幸1段化・モリタン白専用削除・フレッシュ36・タケダ重複・あすかベーカリー赤青。"""
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
        'INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)'
        ' VALUES(?,?,?,?,?,?,0)',
        (key, 1, name, '', name, '対応'),
    ).lastrowid


def _add_format(c, customer_id, identity, format_name, **kwargs):
    return c.execute(
        'INSERT INTO name_formats('
        'identity, customer_id, source_kind, format_name, placement, family, heading, note,'
        'layout, reading, small, width_mm, height_mm, fixed_color, color_caption,'
        'groups_json, colors_json, swatches_json, maximum_font, heading_font, spec_json, retired'
        ') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
        (
            identity, customer_id, 1, format_name,
            kwargs.get('placement', ''), kwargs.get('family', 'HG丸ゴシック'),
            kwargs.get('heading', ''), kwargs.get('note', ''),
            kwargs.get('layout', 'Name'), kwargs.get('reading', 'フルネーム'),
            kwargs.get('small', 1), kwargs.get('width', 50), kwargs.get('height', 24),
            kwargs.get('fixed', '白'), kwargs.get('color_caption', ''),
            json.dumps(kwargs.get('groups') or [], ensure_ascii=False),
            json.dumps(kwargs.get('colors') or [], ensure_ascii=False),
            json.dumps(kwargs.get('swatches') or [], ensure_ascii=False),
            kwargs.get('maximum_font', 26),
            kwargs.get('heading_font', 0),
            json.dumps(kwargs.get('spec') or {}, ensure_ascii=False),
        ),
    ).lastrowid


def _add_sample(c, format_id, *, key='standard', paper='白', path='', caption=''):
    return c.execute(
        'INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)'
        ' VALUES(?,?,?,?,?,?)',
        (format_id, key, '', paper, path, caption),
    ).lastrowid


class RinkoLargeOneBandTest(unittest.TestCase):
    def test_large_has_no_oo_placeholder(self):
        bands = names._rinko_large_bands()
        self.assertEqual(len(bands), 1)
        self.assertEqual(bands[0]['text'], '山田 太郎')
        self.assertNotIn('○○', bands[0]['text'])
        self.assertEqual(names.RINKO_LARGE_HEADING, 'フルネーム（大汎用）')
        self.assertNotIn('○○', names.RINKO_LARGE_SAMPLE_CAPTION)
        self.assertNotIn('上段', names.RINKO_LARGE_SAMPLE_CAPTION)
        spec = names._rinko_spec('large')
        self.assertNotIn('sampleUpper', spec)
        self.assertEqual(len(spec['bandHeightsMm']), 1)


class MoritanSoloWhiteTest(unittest.TestCase):
    def test_retires_solo_white_keeps_role_colors(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / 'images' / 'names').mkdir(parents=True)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.DATA_DIR = None
                names.schema(c)
                names.bind(data_dir)
                names.ensure_six_customers_name_formats(c, data_dir)
                # ensure_six で正規形式が既にある前提。白専用残骸だけ足す
                moritan = c.execute(
                    'SELECT id FROM name_customers WHERE source_name=? AND retired=0',
                    ('モリタン紋別',),
                ).fetchone()
                self.assertIsNotNone(moritan)
                keep = c.execute(
                    'SELECT id FROM name_formats WHERE identity=? AND retired=0',
                    ('moritan:large-fullname',),
                ).fetchone()
                self.assertIsNotNone(keep)
                solo = _add_format(
                    c, moritan['id'], 'old-white', '大ネーム',
                    small=0, width=80, height=50, fixed='白',
                    color_caption='白', maximum_font=48,
                )
                _add_sample(c, solo, paper='白', path='images/names/solo-white.png')
                (data_dir / 'images' / 'names' / 'solo-white.png').write_bytes(b'\x89PNG solo')

                other = _add_customer(c, 'other', '他社サンプル')
                other_fmt = _add_format(c, other, 'other-white', '大ネーム', fixed='白')
                _add_sample(c, other_fmt, paper='白')

                report = names.correct_moritan_solo_white(c, data_dir)
                self.assertEqual(report['removedFormats'], 1)
                self.assertEqual(report['policy'], names.MORITAN_SOLO_WHITE_POLICY)

                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (solo,)).fetchone()['retired'],
                    1,
                )
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (keep['id'],)).fetchone()['retired'],
                    0,
                )
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (other_fmt,)).fetchone()['retired'],
                    0,
                )
                again = names.correct_moritan_solo_white(c, data_dir)
                self.assertEqual(again['removedFormats'], 0)


class FreshLegacyAnd36Test(unittest.TestCase):
    def test_retires_legacy_and_spec_is_36_exact(self):
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

                fresh = _add_customer(c, 'fresh', 'フレッシュフーズ')
                # ensure_six で新形式を作ったあと、別の旧形式残骸を足す
                names.ensure_six_customers_name_formats(c, data_dir)
                legacy = _add_format(
                    c, fresh, 'old-mix', '小ネーム（フルネーム）',
                    placement='白下：左前',
                    heading='フルネーム（小汎用）', reading='フルネーム',
                    fixed='先方から指定', color_caption='先方から指定',
                    maximum_font=26,
                )
                _add_sample(c, legacy, paper='先方から指定', path='')

                report = names.correct_fresh_legacy_formats(c, data_dir)
                self.assertGreaterEqual(report['removedFormats'], 1)

                keep = c.execute(
                    'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                    ('fresh:small-surname',),
                ).fetchone()
                self.assertIsNotNone(keep)
                self.assertEqual(keep['maximum_font'], 36)
                self.assertEqual(keep['heading'], '苗字のみ（36pt）')
                self.assertEqual(keep['reading'], '苗字のみ')
                spec = json.loads(keep['spec_json'] or '{}')
                self.assertEqual(spec.get('sampleFit'), 'exact')
                papers = [
                    s['paper_color']
                    for s in c.execute(
                        'SELECT paper_color FROM name_samples WHERE format_id=? ORDER BY id',
                        (keep['id'],),
                    )
                ]
                self.assertEqual(papers, ['緑', '青'])
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (legacy,)).fetchone()['retired'],
                    1,
                )
                again = names.correct_fresh_legacy_formats(c, data_dir)
                self.assertEqual(again['removedFormats'], 0)


class TakedaDuplicateSmallTest(unittest.TestCase):
    def test_retires_duplicate_small_keeps_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / 'images' / 'names').mkdir(parents=True)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.DATA_DIR = None
                names.schema(c)
                names.bind(data_dir)
                names.ensure_six_customers_name_formats(c, data_dir)
                takeda = c.execute(
                    'SELECT id FROM name_customers WHERE source_name=? AND retired=0',
                    ('タケダ海産',),
                ).fetchone()
                self.assertIsNotNone(takeda)
                keep = c.execute(
                    'SELECT id FROM name_formats WHERE identity=? AND retired=0',
                    ('takeda:small-fullname',),
                ).fetchone()
                large = c.execute(
                    'SELECT id FROM name_formats WHERE identity=? AND retired=0',
                    ('takeda:large-fullname',),
                ).fetchone()
                self.assertIsNotNone(keep)
                self.assertIsNotNone(large)
                dup = _add_format(
                    c, takeda['id'], 'old-small', '小ネーム',
                    placement='白下：左前　帽子：左耳上',
                    family='HG丸ゴシック', reading='フルネーム',
                    fixed='白', color_caption='白', maximum_font=26,
                )
                _add_sample(c, dup, paper='白', path='images/names/dup-small.png')
                (data_dir / 'images' / 'names' / 'dup-small.png').write_bytes(b'\x89PNG dup')

                report = names.correct_takeda_duplicate_formats(c, data_dir)
                self.assertEqual(report['smallRetired'], 1)
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (dup,)).fetchone()['retired'],
                    1,
                )
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (keep['id'],)).fetchone()['retired'],
                    0,
                )
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (large['id'],)).fetchone()['retired'],
                    0,
                )
                again = names.correct_takeda_duplicate_formats(c, data_dir)
                self.assertEqual(again['smallRetired'], 0)


class AsukaBakeryRedBlueTest(unittest.TestCase):
    def test_sets_red_blue_and_leaves_house_alone(self):
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

                house = _add_customer(c, 'house', 'あすかHOUSE東苗穂')
                house_fmt = _add_format(
                    c, house, 'house-large', '大ネーム',
                    placement='スクラブ：左胸', note='ハウスの備考は触るな',
                    small=0, width=80, height=50, fixed='白',
                )
                ashuka = _add_customer(c, 'ashuka', '札幌/桜台/白石/岩見沢/宮の沢明日佳')
                ashuka_fmt = _add_format(
                    c, ashuka, 'ashuka-large', '大ネーム',
                    note='明日佳の備考は触るな', small=0, width=80, height=50,
                )

                bakery = _add_customer(c, 'bakery-old', 'あすかベーカリー')
                report = names.ensure_asuka_bakery_name_formats(c, data_dir)
                self.assertGreaterEqual(report['changed'], 1)
                self.assertTrue(report['exclusionsUntouched'])
                self.assertEqual(report['colors'], ['赤', '青'])
                self.assertEqual(report.get('activeFormatCount'), 1)
                self.assertEqual(report['policy'], names.ASUKA_BAKERY_POLICY)

                keep = c.execute(
                    'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                    (names.ASUKA_BAKERY_IDENTITY,),
                ).fetchone()
                self.assertIsNotNone(keep)
                self.assertEqual(keep['color_caption'], '赤 or 青')
                self.assertEqual(keep['maximum_font'], 36)
                self.assertEqual(keep['family'], 'MSPゴシック')
                self.assertEqual(json.loads(keep['colors_json']), ['赤', '青'])
                spec = json.loads(keep['spec_json'] or '{}')
                self.assertEqual(spec.get('sampleFit'), 'exact')
                self.assertEqual(spec.get('asukaBakeryPolicy'), names.ASUKA_BAKERY_POLICY)
                papers = [
                    s['paper_color']
                    for s in c.execute(
                        'SELECT paper_color FROM name_samples WHERE format_id=? ORDER BY id',
                        (keep['id'],),
                    )
                ]
                self.assertEqual(papers, ['赤', '青'])
                self.assertEqual(
                    c.execute(
                        'SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND retired=0',
                        (bakery,),
                    ).fetchone()['n'],
                    1,
                )

                # 正規のあとで単色残骸を足し、再実行で退職されること
                solo = _add_format(
                    c, bakery, 'old-red', '小ネーム',
                    placement='シャツ：メーカータグ下', reading='苗字のみ',
                    layout='Surname', fixed='赤？', color_caption='赤？',
                    maximum_font=36, family='MSPゴシック',
                )
                _add_sample(c, solo, paper='赤？', path='')
                cleanup = names.ensure_asuka_bakery_name_formats(c, data_dir)
                self.assertGreaterEqual(cleanup.get('retiredExtraFormats', 0), 1)
                self.assertEqual(cleanup.get('activeFormatCount'), 1)
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (solo,)).fetchone()['retired'],
                    1,
                )

                self.assertEqual(
                    c.execute('SELECT note FROM name_formats WHERE id=?', (house_fmt,)).fetchone()['note'],
                    'ハウスの備考は触るな',
                )
                self.assertEqual(
                    c.execute('SELECT note FROM name_formats WHERE id=?', (ashuka_fmt,)).fetchone()['note'],
                    '明日佳の備考は触るな',
                )
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (house_fmt,)).fetchone()['retired'],
                    0,
                )
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (ashuka_fmt,)).fetchone()['retired'],
                    0,
                )

                again = names.ensure_asuka_bakery_name_formats(c, data_dir)
                self.assertEqual(again.get('retiredExtraFormats', 0), 0)
                self.assertEqual(again.get('activeFormatCount'), 1)

    def test_merges_duplicate_red_blue_formats_keeps_images(self):
        """#171 後に赤青付きが2件残った場合も1件に統合し、退職側の画像は消さない。"""
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest('Pillow未導入')

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            images = data_dir / 'images' / 'names'
            images.mkdir(parents=True)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                names.DATA_DIR = None
                names.schema(c)
                names.bind(data_dir)

                house = _add_customer(c, 'house', 'あすかHOUSE')
                house_fmt = _add_format(
                    c, house, 'house-keep', '大ネーム',
                    note='HOUSE本体も触るな', small=0, width=80, height=50, fixed='白',
                )
                bakery = _add_customer(c, 'bakery', 'あすかベーカリー')
                first = _add_format(
                    c, bakery, names.ASUKA_BAKERY_IDENTITY, '小ネーム（苗字のみ）',
                    placement=names.ASUKA_BAKERY_PLACEMENT,
                    reading='苗字のみ', layout='Surname',
                    color_caption='赤 or 青',
                    colors=['赤', '青'], swatches=['#F08080', '#87CEFA'],
                    maximum_font=36, family='MSPゴシック',
                    spec={'asukaBakeryPolicy': 'excel-gojuon2-r14-red-blue-v1', 'sampleFit': 'exact'},
                )
                second = _add_format(
                    c, bakery, 'asuka-bakery:small-surname-dup', '小ネーム（苗字のみ）',
                    placement=names.ASUKA_BAKERY_PLACEMENT,
                    reading='苗字のみ', layout='Surname',
                    color_caption='赤 or 青',
                    colors=['赤', '青'], swatches=['#F08080', '#87CEFA'],
                    maximum_font=36, family='MSPゴシック',
                )
                kept_paths = []
                for fid in (first, second):
                    for paper in ('赤', '青'):
                        rel = f'images/names/pre-{paper}-{fid:06}.png'
                        path = data_dir / rel
                        path.write_bytes(b'PNG-KEEP')
                        _add_sample(c, fid, key=f'paper-{paper}', paper=paper, path=rel)
                        if fid == second:
                            kept_paths.append(path)

                report = names.ensure_asuka_bakery_name_formats(c, data_dir)
                self.assertGreaterEqual(report.get('retiredExtraFormats', 0), 1)
                self.assertEqual(report.get('activeFormatCount'), 1)
                self.assertFalse(report.get('imagesUnlinked'))

                active = list(c.execute(
                    'SELECT * FROM name_formats WHERE customer_id=? AND retired=0',
                    (bakery,),
                ))
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0]['identity'], names.ASUKA_BAKERY_IDENTITY)
                self.assertEqual(json.loads(active[0]['colors_json']), ['赤', '青'])
                papers = [
                    s['paper_color']
                    for s in c.execute(
                        'SELECT paper_color FROM name_samples WHERE format_id=? ORDER BY id',
                        (active[0]['id'],),
                    )
                ]
                self.assertEqual(papers, ['赤', '青'])

                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (second,)).fetchone()['retired'],
                    1,
                )
                # 退職側の見本行・画像ファイルは残る
                self.assertEqual(
                    c.execute(
                        'SELECT COUNT(*) n FROM name_samples WHERE format_id=?', (second,),
                    ).fetchone()['n'],
                    2,
                )
                for path in kept_paths:
                    self.assertTrue(path.is_file(), path)

                self.assertEqual(
                    c.execute('SELECT note FROM name_formats WHERE id=?', (house_fmt,)).fetchone()['note'],
                    'HOUSE本体も触るな',
                )
                self.assertEqual(
                    c.execute('SELECT retired FROM name_formats WHERE id=?', (house_fmt,)).fetchone()['retired'],
                    0,
                )

                again = names.ensure_asuka_bakery_name_formats(c, data_dir)
                self.assertEqual(again.get('retiredExtraFormats', 0), 0)
                self.assertEqual(again.get('activeFormatCount'), 1)


if __name__ == '__main__':
    unittest.main()
