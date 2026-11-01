"""初期在庫の要確認を理由別にまとめ、一括で登録しない／候補で確定（テストDBのみ）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import legacy_csv_bulk_import as bulk  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv, newdb, opening_csv  # noqa: E402


class OpeningReviewGroupsTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()
        ledger = ledger_csv([
            '顧客A,仕入先X,ABC-100,ポロシャツ,L',
            '顧客A,仕入先X,ABC-100,ポロシャツ,M',
        ])
        s1 = {'importMode': 'products', 'name': '台帳.csv', 'file': b64(ledger),
              '_canWrite': True, '_actor': {'name': 't'}}
        p = pli.build_preview(self.c, s1)
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(self.c, {**s1, 'commitToken': 'tok-review-groups-1',
                                        'previewFingerprint': p['previewFingerprint']}, db_path=self.path)
        self.stage = {
            'importMode': 'opening_stock', 'auxName': '初期在庫.csv',
            # 新古が不正 → 入力値の要確認
            'auxFile': b64(opening_csv([
                '??,仕入先X,顧客A,ポロシャツ,ABC-100,L,1000,0,0,3,本社,棚1,0',
                '??,仕入先X,顧客A,ポロシャツ,ABC-100,M,1000,0,0,2,本社,棚1,0',
            ])),
            'basisDate': '2026-01-15', 'defaultSiteName': '未確認',
            '_canWrite': True, '_actor': {'name': 't'},
        }

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def plans(self, sel=None):
        prev = pli.build_preview(self.c, {**self.stage, 'openingSelections': sel or {}})
        return (prev.get('openingStock') or {}).get('plans') or []

    def test_review_rows_get_group_and_skip_excludes(self):
        plans = self.plans()
        rev = [p for p in plans if p['status'] == 'needs_review']
        self.assertEqual(len(rev), 2, [(p['status'], p.get('reasons')) for p in plans])
        self.assertTrue(all(p.get('reviewGroup') and p.get('reviewGroupLabel') for p in rev))
        sel = {str(p['rowNo']): {'skip': True} for p in rev}
        after = self.plans(sel)
        self.assertTrue(all(p['status'] == 'excluded' and p.get('userSkipped') for p in after))
        self.assertIn(bulk.OPENING_SKIP_REASON, after[0]['reasons'])

    def test_prefer_map_id_narrows_only_to_known_candidate(self):
        maps = [dict(r) for r in self.c.execute('SELECT * FROM product_ledger_row_maps')]
        self.assertTrue(maps)
        base = dict(customer='顧客A', code='ABC-100', product_name='ポロシャツ', size='L', supplier='仕入先X')
        a = bulk.lookup_ledger_destinations_for_aux(self.c, **base)
        b = bulk.lookup_ledger_destinations_for_aux(self.c, **base, prefer_map_id='999999')
        self.assertEqual(a['status'], b['status'])

    def test_group_classifier(self):
        self.assertEqual(bulk._opening_review_group({'target': {'mapStatus': 'ambiguous'}}), 'ambiguous')
        self.assertEqual(bulk._opening_review_group({'target': {'mapStatus': 'deleted'}}), 'deleted')
        self.assertEqual(bulk._opening_review_group({'target': {'mapStatus': 'missing'},
                                                     'productRegistrationExcluded': True}), 'product_excluded')
        self.assertEqual(bulk._opening_review_group({'target': {'mapStatus': 'ok', 'usageConflict': True}}), 'usage')


if __name__ == '__main__':
    unittest.main()
