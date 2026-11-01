"""旧CSV（商品を登録）の段階分け（テストDBのみ）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv, newdb  # noqa: E402

ROWS = [
    '顧客A,仕入先X,ABC-100,ポロシャツ,L',
    '顧客A,仕入先X,ABC-200,ポロシャツ（刺繍）,L',
    '顧客A,仕入先X,,ボタン,F',
    '顧客A,仕入先X,,シャツ（刺繍）,F',
]


def body(phase):
    return {'importMode': 'products', 'productPhase': phase, 'name': 'a.csv',
            'file': b64(ledger_csv(ROWS)), '_canWrite': True, '_actor': {'name': 't'}}


def run(c, path, phase, tok):
    b = body(phase)
    p = pli.build_preview(c, b)
    with mock.patch.object(stock, 'backup_sqlite', return_value=''):
        pli.commit_preview(c, {**b, 'commitToken': tok, 'previewFingerprint': p['previewFingerprint']}, db_path=path)
    return p


def products(c):
    return sorted((r['code'], r['name'], r['category']) for r in c.execute('SELECT code,name,category FROM products'))


class ImportPhaseTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_parts_then_goods(self):
        p = run(self.c, self.path, 'parts', 'tok-ph-1')
        self.assertEqual(sorted(r['rowNo'] for r in p['rows']), [3, 4, 5])
        self.assertEqual(p['phaseHiddenRows'], 1)
        got = products(self.c)
        self.assertNotIn('ABC-200', [x[0] for x in got])
        self.assertEqual([x[1] for x in got if x[2] == stock.CATEGORY_PROCESS_SERVICE], ['刺繍加工'])

        p2 = run(self.c, self.path, 'goods', 'tok-ph-2')
        self.assertEqual(sorted(r['rowNo'] for r in p2['rows']), [2, 3])
        got = products(self.c)
        self.assertEqual([x[1] for x in got if x[2] == stock.CATEGORY_PROCESS_SERVICE], ['刺繍加工'])
        self.assertIn('ABC-200', [x[0] for x in got])
        link = self.c.execute(
            "SELECT p.name FROM customer_product_processes x JOIN products p ON p.id=x.process_product_id").fetchall()
        self.assertEqual([r[0] for r in link], ['刺繍加工'])
        # 原文→商品マップ: ABC-200 の行は最終的に衣服商品
        m = self.c.execute(
            "SELECT p.code FROM product_ledger_row_maps m JOIN products p ON p.id=m.product_id "
            "WHERE m.source_row_no=3 ORDER BY m.id DESC LIMIT 1").fetchone()
        self.assertEqual(m[0], 'ABC-200')

        before = products(self.c)
        run(self.c, self.path, 'goods', 'tok-ph-3')
        run(self.c, self.path, 'parts', 'tok-ph-4')
        self.assertEqual(products(self.c), before)

    def test_goods_alone_still_creates_service(self):
        run(self.c, self.path, 'goods', 'tok-ph-5')
        names = [x[1] for x in products(self.c) if x[2] == stock.CATEGORY_PROCESS_SERVICE]
        self.assertEqual(names, ['刺繍加工'])


if __name__ == '__main__':
    unittest.main()
