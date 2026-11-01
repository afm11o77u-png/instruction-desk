"""括弧の「未加工品」等は加工なし（テストDBのみ）。"""
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

ROWS = ['よつ葉乳業,株サーヴォ,DCP515,通行服上(未加工品),L',
        'よつ葉乳業,株サーヴォ,DCP516,通行服下（加工なし）,L']


class NoProcessingParenTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def run_phase(self, phase, tok):
        b = {'importMode': 'products', 'productPhase': phase, 'name': 'n.csv',
             'file': b64(ledger_csv(ROWS)), '_canWrite': True, '_actor': {'name': 't'}}
        p = pli.build_preview(self.c, b)
        if p['rows']:
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(self.c, {**b, 'commitToken': tok,
                                            'previewFingerprint': p['previewFingerprint']}, db_path=self.path)
        return p

    def test_unprocessed_is_plain_product(self):
        p = self.run_phase('parts', 'tok-np-1')
        self.assertEqual(p.get('processingServicePlans') or [], [])
        p = self.run_phase('goods', 'tok-np-2')
        for r in p['rows']:
            self.assertEqual(r['status'], pli.STATUS_CREATE, r.get('reasons'))
            self.assertFalse(r.get('reasons'))
        names = sorted(r['name'] for r in self.c.execute('SELECT name FROM products'))
        self.assertEqual(names, ['通行服上', '通行服下'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM customer_product_processes').fetchone()[0], 0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM products WHERE category=?',
                                        (stock.CATEGORY_PROCESS_SERVICE,)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()


class NoProcessingNameTest(unittest.TestCase):
    def test_not_a_service_name(self):
        import product_ledger_processing_service as ps
        for n in ('未加工', '未加工品', '加工なし', '無加工'):
            self.assertEqual(ps.unify_processing_service_name(n), '', n)
        self.assertEqual(ps.unify_processing_service_name('刺繍加工'), '刺繍加工')
