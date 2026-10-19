"""対象3社：Excel 行25–27 / 122–123 / 158 のネーム追加。"""
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
            layout, reading, small, width_mm, height_mm, fixed_color, maximum_font, spec_json, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''',
        (
            identity, customer_id, 1, format_name,
            kwargs.get('placement', ''), kwargs.get('family', ''),
            kwargs.get('heading', ''), kwargs.get('note', ''),
            kwargs.get('layout', 'Name'), kwargs.get('reading', 'フルネーム'),
            kwargs.get('small', 0), kwargs.get('width', 80), kwargs.get('height', 50),
            kwargs.get('fixed', '白'), kwargs.get('maximum_font', 0),
            json.dumps(kwargs.get('spec') or {}, ensure_ascii=False),
        ),
    ).lastrowid


class ThreeCustomerNameFormatsTest(unittest.TestCase):
    def test_generic_and_tokachi_sizes(self):
        self.assertEqual(names._three_generic_pt('large', '山田 太郎'), 48)
        self.assertEqual(names._three_generic_pt('small', '山田 太郎'), 26)
        self.assertEqual(names._three_tokachi_surname_pt('山田'), 80)
        self.assertEqual(names._three_tokachi_surname_pt('山田太'), 64)
        self.assertEqual(names._three_tokachi_surname_pt('山田太郎'), 56)

    def test_creates_three_customers_and_formats(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow未導入')

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                # schema 内の ensure より先に他社を置き、対象外が変わらないことを見る
                names.schema(c)
                # schema が空DBへ3社を載せる前に他社だけ入れたいので、ここでは明示 ensure
                # （schema 呼び出し時点で既に ensure 済みなら changed=0 でも実体は揃う）
                other = _add_customer(c, 'other', '他社サンプル')
                other_fmt = _add_format(
                    c, other, 'id-other', '大ネーム',
                    placement='他社位置', family='HG丸ゴシック', note='他社の備考',
                )
                other_note = c.execute(
                    'SELECT note FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone()['note']

                report = names.ensure_three_customer_name_formats(c, data_dir)
                self.assertEqual(report['policy'], names.THREE_NAMES_POLICY)
                # schema で既に適用済みなら 0、未適用なら作成数が入る
                self.assertIn(report.get('reason'), (None, 'already-applied', '', None))
                if report['changed'] == 0:
                    self.assertEqual(report.get('reason'), 'already-applied')
                else:
                    self.assertGreaterEqual(report['changed'], 6)
                    self.assertTrue(report['backupDir'])
                    self.assertTrue((data_dir / report['backupDir']).is_dir())

                for name in (
                    'エア・ウォーター・アグリ',
                    '十勝ミートセンター',
                    'ベル・クール研究所',
                ):
                    row = c.execute(
                        'SELECT id FROM name_customers WHERE retired=0 AND source_name=?',
                        (name,),
                    ).fetchone()
                    self.assertIsNotNone(row, name)

                identities = [
                    'airwater-agri:large-fullname',
                    'airwater-agri:small-pants-fullname',
                    'airwater-agri:small-hat-surname',
                    'tokachi-meat:large-surname',
                    'tokachi-meat:small-surname',
                    'bellcool:small-fullname',
                ]
                for identity in identities:
                    fmt = c.execute(
                        'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                        (identity,),
                    ).fetchone()
                    self.assertIsNotNone(fmt, identity)
                    samples = c.execute(
                        'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                        (fmt['id'],),
                    ).fetchall()
                    self.assertGreaterEqual(len(samples), 1, identity)
                    for sample in samples:
                        rel = sample['relative_path']
                        self.assertTrue(rel.startswith('images/names/'))
                        path = data_dir / rel
                        self.assertTrue(path.is_file(), rel)
                        with Image.open(path) as im:
                            self.assertEqual(
                                im.size,
                                (fmt['width_mm'] * 10, fmt['height_mm'] * 10),
                            )

                # 十勝は白・緑の2見本
                tokachi = c.execute(
                    "SELECT id FROM name_formats WHERE identity='tokachi-meat:large-surname'"
                ).fetchone()
                papers = [
                    r['paper_color']
                    for r in c.execute(
                        'SELECT paper_color FROM name_samples WHERE format_id=? ORDER BY id',
                        (tokachi['id'],),
                    )
                ]
                self.assertEqual(papers, ['白', '緑'])

                # 他社は不変
                after = c.execute(
                    'SELECT note FROM name_formats WHERE id=?', (other_fmt,),
                ).fetchone()['note']
                self.assertEqual(after, other_note)

                # 再実行で重複しない
                report2 = names.ensure_three_customer_name_formats(c, data_dir)
                self.assertEqual(report2['changed'], 0)
                n = c.execute(
                    '''SELECT COUNT(*) n FROM name_formats WHERE identity IN ({})'''.format(
                        ','.join('?' * len(identities))
                    ),
                    identities,
                ).fetchone()['n']
                self.assertEqual(n, 6)

    def test_reuses_existing_customer_without_duplicate(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest('Pillow未導入')

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with _connect(data_dir / 'desk.sqlite3') as c:
                names.bind(data_dir)
                # schema の自動 ensure を避け、改行付き既存顧客だけ先に置く
                names.DATA_DIR = None
                names.schema(c)
                names.bind(data_dir)
                _add_customer(c, 'legacy-air', 'エア・ウォーター・アグリ\n更別工場/札幌工場')
                report = names.ensure_three_customer_name_formats(c, data_dir)
                self.assertGreaterEqual(report['changed'], 1)
                n = c.execute(
                    '''SELECT COUNT(*) n FROM name_customers
                       WHERE retired=0 AND (
                         source_name LIKE 'エア・ウォーター・アグリ%'
                         OR source_key='excel:airwater-agri'
                       )'''
                ).fetchone()['n']
                self.assertEqual(n, 1)
                # 形式は既存顧客へ紐づく
                cust = c.execute(
                    "SELECT id FROM name_customers WHERE source_key='legacy-air'"
                ).fetchone()
                linked = c.execute(
                    "SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND identity LIKE 'airwater-agri:%'",
                    (cust['id'],),
                ).fetchone()['n']
                self.assertEqual(linked, 3)


if __name__ == '__main__':
    unittest.main()
