"""加工行だけの品番（本体行なし）は加工サービスとして登録する（テストDBのみ）。"""
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

ROWS = [f'ヨシツネ食品,オリゾン(有),CJ2401-2,クリーンウェア インナー加工,{s}'
        for s in ('S', 'M', 'L', 'LL', '3L', '4L')]


class ProcessingOnlySkuTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def body(self, phase):
        return {'importMode': 'products', 'productPhase': phase, 'name': 'p.csv',
                'file': b64(ledger_csv(ROWS)), '_canWrite': True, '_actor': {'name': 't'}}

    def run_phase(self, phase, tok):
        b = self.body(phase)
        p = pli.build_preview(self.c, b)
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(self.c, {**b, 'commitToken': tok,
                                        'previewFingerprint': p['previewFingerprint']}, db_path=self.path)
        return p

    def prods(self):
        return sorted((r['code'] or '', r['name'], r['category'])
                      for r in self.c.execute('SELECT code,name,category FROM products'))

    def test_parts_phase_registers_service_without_review(self):
        p = pli.build_preview(self.c, self.body('parts'))
        g = [g for g in p['skuGroups'] if g['code'] == 'CJ2401-2'][0]
        self.assertFalse(g['needsDecision'])
        self.assertNotIn('garment_body_unknown', [r.get('code') for r in g.get('reviewReasons') or []])
        self.run_phase('parts', 'tok-po-1')
        got = self.prods()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], '')  # 品番は加工サービスに付けない
        self.assertEqual(got[0][2], stock.CATEGORY_PROCESS_SERVICE)
        # 商品段階: 本体を求めず何も作らない。再実行で増えない
        p2 = pli.build_preview(self.c, self.body('goods'))
        self.assertEqual(p2['skuGroups'], [])
        self.assertEqual(p2['rows'], [])
        self.run_phase('parts', 'tok-po-3')
        self.assertEqual(self.prods(), got)


if __name__ == '__main__':
    unittest.main()
