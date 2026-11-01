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


GROUP_ROWS = [
    '顧客A,フジ総合安全㈱,May-93,長袖シャツ,L',
    '顧客B,フジ総合安全㈱,May-93,ポロシャツ（刺繍）,L',
    '顧客A,住商モンブラン㈱,SD8711-2,白衣ポケット加工(住商モンブラン),L',
    '顧客B,㈱札幌白衣,SD8711-2,白衣,L',
    '顧客C,住商モンブラン㈱,SD8711-2,白衣(住商モンブラン),M',
    '顧客A,仕入先X,,ボタン,F',
]


class PartsPhaseGroupTest(unittest.TestCase):
    """部品・加工段階では衣服の品番グループ（一括承認候補など）を出さない。"""

    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def _body(self, phase):
        return {'importMode': 'products', 'productPhase': phase, 'name': 'g.csv',
                'file': b64(ledger_csv(GROUP_ROWS)), '_canWrite': True, '_actor': {'name': 't'}}

    def test_parts_phase_has_no_garment_groups(self):
        b = self._body('parts')
        p = pli.build_preview(self.c, b)
        self.assertEqual(p['skuGroups'], [])
        self.assertFalse(any(r.get('skuDecisionNeeded') for r in p['rows']))
        self.assertFalse(any((r.get('csv') or {}).get('code') == 'May-93' and not r.get('phasePartsOnly')
                             for r in p['rows']))
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(self.c, {**b, 'commitToken': 'tok-ph-g1',
                                        'previewFingerprint': p['previewFingerprint']}, db_path=self.path)
        got = products(self.c)
        codes = [x[0] for x in got]
        self.assertNotIn('May-93', codes)
        self.assertNotIn('SD8711-2', codes)
        svc = sorted(x[1] for x in got if x[2] == stock.CATEGORY_PROCESS_SERVICE)
        self.assertEqual(svc, ['ポケット加工', '刺繍加工'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM product_ledger_row_maps m JOIN products p '
                                        "ON p.id=m.product_id WHERE p.code IN ('May-93','SD8711-2')").fetchone()[0], 0)
        # 再実行しても増えない
        p2 = pli.build_preview(self.c, b)
        self.assertEqual(p2['skuGroups'], [])
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(self.c, {**b, 'commitToken': 'tok-ph-g2',
                                        'previewFingerprint': p2['previewFingerprint']}, db_path=self.path)
        self.assertEqual(products(self.c), got)

    def test_goods_phase_still_has_garment_groups(self):
        p = pli.build_preview(self.c, self._body('goods'))
        self.assertEqual(sorted(g['code'] for g in p['skuGroups']), ['May-93', 'SD8711-2'])
