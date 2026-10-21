"""6社ネーム更新：健診/タケダ/フレッシュ/モリタン + FK検証 + 鱗幸段サイズ。"""
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
        'groups_json, colors_json, swatches_json, maximum_font, spec_json, retired'
        ') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
        (
            identity, customer_id, 1, format_name,
            kwargs.get('placement', ''), kwargs.get('family', ''),
            kwargs.get('heading', ''), kwargs.get('note', ''),
            kwargs.get('layout', 'Name'), kwargs.get('reading', 'フルネーム'),
            kwargs.get('small', 1), kwargs.get('width', 50), kwargs.get('height', 24),
            kwargs.get('fixed', '白'), kwargs.get('color_caption', '白'),
            json.dumps(kwargs.get('groups') or [], ensure_ascii=False),
            json.dumps(kwargs.get('colors') or [], ensure_ascii=False),
            json.dumps(kwargs.get('swatches') or [], ensure_ascii=False),
            kwargs.get('maximum_font', 0),
            json.dumps(kwargs.get('spec') or {}, ensure_ascii=False),
        ),
    ).lastrowid


def _add_sample(c, format_id, *, key='standard', paper='白', path='', caption=''):
    return c.execute(
        'INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)'
        ' VALUES(?,?,?,?,?,?)',
        (format_id, key, '', paper, path, caption),
    ).lastrowid


def _sample_ok(path, *, expect_w, expect_h, paper_rgb=(255, 255, 255)):
    """見本が存在し、寸法一致・非空・四隅が用紙色・文字インクあり。"""
    from PIL import Image
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 32:
        return False, 'missing-or-empty'
    with Image.open(p) as im:
        im = im.convert('RGB')
        if im.size != (expect_w, expect_h):
            return False, f'size={im.size}'
        corners = [
            im.getpixel((0, 0)),
            im.getpixel((im.size[0] - 1, 0)),
            im.getpixel((0, im.size[1] - 1)),
            im.getpixel((im.size[0] - 1, im.size[1] - 1)),
        ]
        # はみ出しで黒縁がない（用紙色から大きく外れない）
        for px in corners:
            if any(abs(px[i] - paper_rgb[i]) > 8 for i in range(3)):
                return False, f'corner-mismatch={corners} paper={paper_rgb}'
        # 文字インク（用紙色と異なる画素）があること
        ink = 0
        for y in range(0, im.size[1], 4):
            for x in range(0, im.size[0], 4):
                px = im.getpixel((x, y))
                if any(abs(px[i] - paper_rgb[i]) > 30 for i in range(3)):
                    ink += 1
        if ink < 3:
            return False, 'no-ink'
    return True, 'ok'


class SixCustomersNameFormatsTest(unittest.TestCase):
    def test_format_specs_match_excel(self):
        by_name = {c['customerName']: c for c in names._six_format_specs()}
        k = by_name[names.KENSHIN_CUSTOMER_NAME]['formats'][0]
        self.assertEqual(k['identity'], 'kenshin:small-size')
        self.assertEqual(k['sourceRow'], 82)
        self.assertEqual(k['placement'], '健診衣上下')
        self.assertEqual(k['family'], 'MSPゴシック')
        self.assertEqual(k['reading'], 'サイズ表記')
        self.assertEqual(k['maximumFont'], 36)
        self.assertEqual(k['sampleText'], 'L')

        t_large, t_small = by_name[names.TAKEDA_CUSTOMER_NAME]['formats']
        self.assertEqual(t_large['heading'], 'フルネーム（大汎用）')
        self.assertNotIn('役職', t_large['heading'])
        self.assertNotIn('上段：役職', t_large['note'])
        self.assertEqual(t_large['layout'], 'Name')
        self.assertEqual(t_small['placement'], '白下：ベルト後左　白帽子：右耳上')

        f = by_name[names.FRESH_CUSTOMER_NAME]['formats'][0]
        self.assertEqual(f['colorCaption'], '社員：緑　パート：青')
        self.assertEqual(f['reading'], '苗字のみ')
        self.assertEqual(f['sampleText'], '山田')
        self.assertEqual(f['papers'], ('緑', '青'))
        self.assertEqual(f['maximumFont'], 36)
        self.assertEqual(f['fontPt'], 36)
        self.assertEqual(f['sampleFit'], 'exact')
        self.assertEqual(f['heading'], '苗字のみ（36pt）')
        self.assertNotIn('小汎用', f['heading'])

        m_large, m_small = by_name[names.MORITAN_CUSTOMER_NAME]['formats']
        self.assertEqual(m_large['colorCaption'], '社員：白　派遣：赤　工員：青　研修生：緑')
        self.assertEqual(m_large['papers'], ('白', '赤', '青', '緑'))
        self.assertEqual(m_small['papers'], ('白', '赤', '青', '緑'))

    def test_creates_four_and_leaves_exclusions(self):
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

                # 除外顧客（変更禁止）
                ashuka = _add_customer(c, 'ashuka', '札幌/桜台/白石/岩見沢/宮の沢明日佳')
                ashuka_fmt = _add_format(
                    c, ashuka, 'id-ashuka', '大ネーム（部署・ふりがな・苗字）',
                    placement='スクラブ：左胸', family='HG丸ゴシック',
                    note='明日佳の備考は触るな', small=0, width=80, height=50,
                )
                asuka = _add_customer(c, 'asuka-east', 'あすかHOUSE東苗穂')
                asuka_fmt = _add_format(
                    c, asuka, 'id-asuka', '大ネーム',
                    placement='スクラブ：左胸', note='あすかの備考',
                    small=0, width=80, height=50,
                )
                other = _add_customer(c, 'other', '他社サンプル')
                other_fmt = _add_format(
                    c, other, 'id-other', '大ネーム',
                    placement='他社位置', note='他社の備考',
                    small=0, width=80, height=50,
                )

                # 旧健診（リネーム対象）
                old_kenshin = _add_customer(c, 'old-kenshin', '札幌健診センター')
                _add_format(
                    c, old_kenshin, 'id-kenshin-old', '小ネーム',
                    placement='健診衣上下', family='MSPゴシック',
                    reading='サイズ表記', small=1, width=50, height=24,
                    maximum_font=36,
                )

                # 旧タケダ（役職2段）
                takeda = _add_customer(c, 'takeda', 'タケダ海産')
                takeda_large = _add_format(
                    c, takeda, 'id-takeda-large', '大ネーム（役職＋氏名）',
                    placement='白衣：左袖25cm', family='HG丸ゴシック',
                    heading='上段：役職　下段：大汎用', note='役職ない場合は１列の大汎用',
                    layout='HeadingName', reading='フルネーム',
                    small=0, width=80, height=50, maximum_font=48,
                )
                _add_sample(c, takeda_large, caption='旧役職見本', path='')
                _add_format(
                    c, takeda, 'id-takeda-small', '小ネーム（フルネーム）',
                    placement='白下：ベルト後左　白帽子：右耳上', family='HG丸ゴシック',
                    reading='フルネーム', small=1, width=50, height=24, maximum_font=26,
                )

                report = names.ensure_six_customers_name_formats(c, data_dir)
                self.assertGreaterEqual(report['changed'], 1)
                self.assertTrue(report['exclusionsUntouched'])
                self.assertEqual(report['policy'], names.SIX_NAMES_POLICY)

                # 健診リネーム
                k_row = c.execute(
                    'SELECT * FROM name_customers WHERE source_name=?',
                    (names.KENSHIN_CUSTOMER_NAME,),
                ).fetchone()
                self.assertIsNotNone(k_row)
                self.assertIsNone(c.execute(
                    'SELECT 1 FROM name_customers WHERE source_name=? AND retired=0',
                    ('札幌健診センター',),
                ).fetchone())

                # タケダ大：役職なし
                t_large = c.execute(
                    'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                    ('takeda:large-fullname',),
                ).fetchone()
                self.assertIsNotNone(t_large)
                self.assertNotIn('役職', t_large['heading'])
                self.assertEqual(t_large['layout'], 'Name')
                t_samples = list(c.execute(
                    'SELECT * FROM name_samples WHERE format_id=?', (t_large['id'],),
                ))
                self.assertEqual(len(t_samples), 1)
                self.assertIn('フルネーム', t_samples[0]['caption'])
                self.assertNotIn('役職', t_samples[0]['caption'])

                # フレッシュ・モリタン作成
                fresh = c.execute(
                    'SELECT * FROM name_formats WHERE identity=?', ('fresh:small-surname',),
                ).fetchone()
                self.assertEqual(fresh['reading'], '苗字のみ')
                self.assertEqual(fresh['color_caption'], '社員：緑　パート：青')
                fresh_samples = list(c.execute(
                    'SELECT paper_color FROM name_samples WHERE format_id=? ORDER BY id',
                    (fresh['id'],),
                ))
                self.assertEqual([s['paper_color'] for s in fresh_samples], ['緑', '青'])

                m_large = c.execute(
                    'SELECT * FROM name_formats WHERE identity=?', ('moritan:large-fullname',),
                ).fetchone()
                self.assertEqual(
                    json.loads(m_large['groups_json']),
                    ['社員', '派遣', '工員', '研修生'],
                )
                m_samples = list(c.execute(
                    'SELECT paper_color FROM name_samples WHERE format_id=? ORDER BY id',
                    (m_large['id'],),
                ))
                self.assertEqual([s['paper_color'] for s in m_samples], ['白', '赤', '青', '緑'])

                # 除外・他社不変
                ashuka_row = c.execute(
                    'SELECT note FROM name_formats WHERE id=?', (ashuka_fmt,),
                ).fetchone()
                self.assertEqual(ashuka_row['note'], '明日佳の備考は触るな')
                asuka_row = c.execute(
                    'SELECT note FROM name_formats WHERE id=?', (asuka_fmt,),
                ).fetchone()
                self.assertEqual(asuka_row['note'], 'あすかの備考')
                other_row = c.execute(
                    'SELECT note FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone()
                self.assertEqual(other_row['note'], '他社の備考')

                # 見本 list+zoom 相当：欠損・寸法・はみ出し
                for fmt in c.execute(
                    'SELECT f.* FROM name_formats f '
                    'JOIN name_customers cu ON cu.id=f.customer_id '
                    'WHERE f.retired=0 AND cu.source_name IN (?,?,?,?)',
                    (
                        names.KENSHIN_CUSTOMER_NAME,
                        names.TAKEDA_CUSTOMER_NAME,
                        names.FRESH_CUSTOMER_NAME,
                        names.MORITAN_CUSTOMER_NAME,
                    ),
                ):
                    for sample in c.execute(
                        'SELECT * FROM name_samples WHERE format_id=?', (fmt['id'],),
                    ):
                        rel = sample['relative_path']
                        self.assertTrue(rel, msg=f'missing path fmt={fmt["identity"]}')
                        w = int(round(float(fmt['width_mm']) * 10))
                        h = int(round(float(fmt['height_mm']) * 10))
                        paper = str(sample['paper_color'] or '白')
                        rgb = names.SIX_PAPER_RGB.get(paper, (255, 255, 255))
                        ok, note = _sample_ok(
                            data_dir / rel, expect_w=w, expect_h=h, paper_rgb=rgb,
                        )
                        self.assertTrue(ok, msg=f'{fmt["identity"]} {rel}: {note}')

                # バックアップあり
                self.assertTrue(report.get('backupDir'))
                self.assertTrue((data_dir / report['backupDir']).is_dir())

                # 冪等
                again = names.ensure_six_customers_name_formats(c, data_dir)
                self.assertEqual(again['changed'], 0)
                self.assertEqual(again.get('reason'), 'already-applied')

                # identity 重複なし
                for identity in (
                    'kenshin:small-size', 'takeda:large-fullname', 'takeda:small-fullname',
                    'fresh:small-surname', 'moritan:large-fullname', 'moritan:small-fullname',
                ):
                    n = c.execute(
                        'SELECT COUNT(*) n FROM name_formats WHERE identity=? AND retired=0',
                        (identity,),
                    ).fetchone()['n']
                    self.assertEqual(n, 1, msg=identity)

    def test_fk_verify_noop_when_current(self):
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
                # FK を先に正しく入れる
                fk = names.ensure_fk_name_formats(c, data_dir)
                self.assertGreaterEqual(fk['changed'], 1)
                # six 実行（他4社も作成される）
                report = names.ensure_six_customers_name_formats(c, data_dir)
                self.assertIsNotNone(report.get('fkVerify'))
                # 再実行で FK も six も no-op
                again = names.ensure_six_customers_name_formats(c, data_dir)
                self.assertEqual(again['changed'], 0)
                self.assertEqual(again['fkVerify']['changed'], 0)

    def test_rinko_band_size_cleared_in_note(self):
        self.assertNotIn('上段帯', names.RINKO_LARGE_NOTE)
        self.assertNotIn('25mm', names.RINKO_LARGE_NOTE)
        self.assertNotIn('25mm', names.RINKO_LARGE_SAMPLE_CAPTION)
        self.assertEqual(names.RINKO_NAMES_POLICY, 'excel-gojuon2-r239-large-1band-v1')
        self.assertEqual(names.RINKO_LARGE_HEADING, 'フルネーム（大汎用）')
        self.assertNotIn('○○', names.RINKO_LARGE_SAMPLE_CAPTION)
        self.assertEqual(names.RINKO_LARGE_SOURCE_ROW, 239)
        self.assertEqual(names.RINKO_SMALL_SOURCE_ROW, 240)


if __name__ == '__main__':
    unittest.main()
