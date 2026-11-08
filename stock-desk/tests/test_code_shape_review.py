"""品番の形の確認。合成CSVと一時DBだけ。実ファイルは使わない。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import base64
import tempfile

import code_shape_review as shape  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64_from_text(text: str) -> str:
    return base64.b64encode(text.encode('utf-8-sig')).decode('ascii')


def tiny_ledger_rows(rows):
    return '\n'.join(['顧客,仕入先,商品コード,商品名,サイズ'] + rows)


def _row(row_no, code, supplier, category, name, size):
    return {
        'rowNo': row_no,
        'status': 'create',
        'csv': {
            'sourceCode': code,
            'code': code,
            'supplier': supplier,
            'productName': name,
            'size': size,
            'color': '',
        },
        'product': {'category': category, 'name': name, 'size': size},
    }


def _body(text):
    return {'file': csv_b64_from_text(text), 'name': 'shape.csv', '_canWrite': True, '_actor': {'name': '確認'}}


class CodeShapeDetectTest(unittest.TestCase):
    def test_groups_do_not_strip_formal_suffix_or_guess(self):
        folder, _path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,7-522,作業ズボン,M',
                '顧客A,仕入A,7-522下,作業ズボン,LL',
                '顧客A,仕入A,AZ-8618,白衣,S',
                '顧客A,仕入A,AZ-8618-003,白衣,M',
                '顧客A,仕入A,漢字品番,作業着,F',
                '顧客A,ミドリ安全北海道㈱,CD1,白衣,S',
                '顧客A,ミドリ安全㈱ 室蘭,CD1上,白衣上,M',
                '顧客A,㈱ミドリ安全,CD1下,白衣下,L',
            ])
            preview = pli.build_preview(c, _body(text))
            groups = {g['id']: g for g in preview['codeShapeGroups']}
            self.assertIn('extend:7-522', groups)
            self.assertNotIn('extend:AZ-8618', groups)
            self.assertIn('unknown:other:漢字品番', groups)
            midori = groups['midori:CD1']
            codes = {m['code'] for m in midori['members']}
            self.assertEqual(codes, {'CD1', 'CD1上', 'CD1下'})
            self.assertIn(shape.JOINT_IMPORT_NOTE, midori['jointImport'])
            self.assertIn('LL', groups['extend:7-522']['sizeUnion'])
            self.assertIn('M', groups['extend:7-522']['sizeUnion'])
            with self.assertRaises(ValueError) as caught:
                pli.commit_preview(c, {
                    **_body(text),
                    'previewFingerprint': preview['previewFingerprint'],
                    'previewAt': preview['previewAt'],
                    'token': 'shape-undecided-1',
                }, db_path=str(_path))
            self.assertIn('未決定', str(caught.exception))
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_merge_keeps_size_union_and_reimport_stays_one_product(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,7-522,作業ズボン,M',
                '顧客A,仕入A,7-522下,作業ズボン,LL',
                '顧客A,仕入A,7-522下,作業ズボン,3L',
            ])
            body = _body(text)
            preview = pli.build_preview(c, body)
            decisions = {'extend:7-522': {'action': 'merge_to_base'}}
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'shape-merge-1',
                'codeShapeDecisions': decisions,
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertFalse(result['codeShapeJointImport'])
            codes = [r['code'] for r in c.execute('SELECT code FROM products ORDER BY code')]
            self.assertEqual(codes, ['7-522'])
            sizes = {
                r['label'] for r in c.execute(
                    'SELECT label FROM product_sizes ps JOIN products p ON p.id=ps.product_id WHERE p.code=?',
                    ('7-522',),
                )
            }
            self.assertEqual(sizes, {'M', 'LL', '3L'})
            link = c.execute('SELECT target_code FROM code_shape_links WHERE source_code=?', ('7-522下',)).fetchone()
            self.assertEqual(link['target_code'], '7-522')
            preview2 = pli.build_preview(c, body)
            self.assertIn('extend:7-522', {g['id'] for g in preview2['codeShapeGroups']})
            with self.assertRaises(ValueError) as again:
                pli.commit_preview(c, {
                    **body,
                    'previewFingerprint': preview2['previewFingerprint'],
                    'previewAt': preview2['previewAt'],
                    'token': 'shape-merge-2',
                    'codeShapeDecisions': decisions,
                }, db_path=str(path))
            self.assertIn('変更なし', str(again.exception))
            self.assertEqual(c.execute("SELECT COUNT(*) n FROM products WHERE code='7-522'").fetchone()['n'], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) n FROM products WHERE code='7-522下'").fetchone()['n'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_midori_upper_and_lower_stay_apart_until_chosen(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,ミドリ安全北海道㈱,CD1,白衣,S',
                '顧客A,ミドリ安全北海道㈱,CD1上,白衣上,M',
                '顧客A,ミドリ安全北海道㈱,CD1下,白衣下,L',
            ])
            body = _body(text)
            preview = pli.build_preview(c, body)
            decisions = {
                'midori:CD1': {
                    'action': 'choose',
                    'targets': {'CD1': 'CD1', 'CD1上': 'CD1上', 'CD1下': 'CD1下'},
                },
            }
            pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'shape-midori-1',
                'codeShapeDecisions': decisions,
            }, db_path=str(path))
            c.commit()
            codes = sorted(r['code'] for r in c.execute('SELECT code FROM products'))
            self.assertEqual(codes, ['CD1', 'CD1上', 'CD1下'])
        finally:
            c.close()
            folder.cleanup()

    def test_non_midori_uniform_stays_and_embedded_mark_is_not_cut(self):
        folder, path, c = newdb()
        try:
            rows = [
                _row(2, '7-522', '一般衣料㈱', '制服', '作業ズボン', 'M'),
                _row(3, '7-522青', '一般衣料㈱', '制服', '作業ズボン', 'LL'),
                _row(4, 'AZ-8618', '一般衣料㈱', '制服', '白衣', 'S'),
                _row(5, 'AZ-8618-003', '一般衣料㈱', '制服', '白衣', 'M'),
                _row(6, 'AB上12', 'ミドリ安全北海道㈱', '制服', '反射ベスト', 'F'),
                _row(7, 'CD1', 'ミドリ安全北海道㈱', '制服', '白衣', 'S'),
                _row(8, 'CD1上', '㈱ミドリ安全', '制服', '白衣', 'M'),
                _row(9, '7-522下', 'ミドリ安全㈱', '部品・資材', '部材', 'F'),
            ]
            groups = {g['id']: g for g in shape.build_groups(c, rows)}
            extend = groups['extend:7-522']
            self.assertEqual({m['code'] for m in extend['members']}, {'7-522', '7-522青', '7-522下'})
            self.assertNotIn('extend:AZ-8618', groups)
            embedded = groups['unknown:midori:AB上12']
            self.assertIn('途中に上または下', embedded['note'])
            self.assertNotIn('AB12', {m['code'] for m in embedded['members']})
            midori = groups['midori:CD1']
            self.assertEqual({m['code'] for m in midori['members']}, {'CD1', 'CD1上'})
            text = tiny_ledger_rows([
                '顧客A,ミドリ安全北海道㈱,AB上12,反射ベスト,F',
                '顧客A,一般衣料㈱,AZ-8618,白衣,S',
                '顧客A,一般衣料㈱,AZ-8618-003,白衣,M',
            ])
            body = _body(text)
            preview = pli.build_preview(c, body)
            ids = {g['id'] for g in preview['codeShapeGroups']}
            self.assertIn('unknown:midori:AB上12', ids)
            self.assertNotIn('extend:AZ-8618', ids)
            decisions = {
                g['id']: {'action': 'keep_separate'}
                for g in preview['codeShapeGroups']
            }
            pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'shape-embedded-1',
                'codeShapeDecisions': decisions,
            }, db_path=str(path))
            c.commit()
            codes = sorted(r['code'] for r in c.execute('SELECT code FROM products'))
            self.assertIn('AB上12', codes)
            self.assertNotIn('AB12', codes)
            self.assertIn('AZ-8618', codes)
            self.assertIn('AZ-8618-003', codes)
        finally:
            c.close()
            folder.cleanup()

    def test_server_rejects_target_outside_the_group(self):
        groups = [{
            'id': 'midori:CD1',
            'kind': 'midori_vertical',
            'baseCode': 'CD1',
            'supplierScope': 'midori',
            'members': [{'code': 'CD1上'}, {'code': 'CD1下'}],
        }]
        assessed = shape.assess(groups, {
            'midori:CD1': {'action': 'choose', 'targets': {'CD1上': 'ZZ-9', 'CD1下': 'CD1下'}},
        })
        self.assertFalse(assessed[0]['resolved'])


class OpeningWipeUiSourceTest(unittest.TestCase):
    def test_button_matches_product_wipe_zone(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="openingWipeOpen"', app)
        self.assertIn('初期在庫だけが対象です', app)
        self.assertLess(app.index('product-wipe-scope'), app.index('openingWipeOpen'))
        self.assertIn('opening_stock_wipe.js', html)
        js = (ROOT / 'static' / 'opening_stock_wipe.js').read_text(encoding='utf-8')
        self.assertIn('初期在庫を全削除', js)
        self.assertIn('/api/opening-stock/wipe', js)
        ledger = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('codeShapeDecisions', ledger)
        self.assertIn('初期在庫と旧QRの一体取り込みは今回ありません', ledger)


if __name__ == '__main__':
    unittest.main()
