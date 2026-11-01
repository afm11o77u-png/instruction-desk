"""括弧のない名前でも色候補が複数なら品番単位で色を決める（一時DB・合成データのみ）。"""
from __future__ import annotations

import unittest

import test_ledger_review_five_fixes as base

ROWS = ['顧客A,仕入Z,BF3100,ズキン ツバ無 白 ゴールド,F',
        '顧客A,仕入Z,BF3100,ズキン ツバ無 白 ゴールド,S',
        '顧客A,仕入Z,BF3100,ズキン ツバ無 白 ゴールド,XL']


class SkuColorCandidatesTest(unittest.TestCase):
    run_case = base.ReviewFiveFixesTest.run_case

    def test_rows_become_one_sku_item(self):
        pv, items = self.run_case(ROWS)
        g = next(x for x in pv['skuGroups'] if x['code'] == 'BF3100')
        self.assertTrue(g['needsDecision'])
        self.assertEqual([(i['kind'], i['code']) for i in items], [('sku', 'BF3100')])
        self.assertEqual(items[0]['fields']['color'], '選択が必要')

    def test_merge_with_one_color_applies_to_all_rows(self):
        pv, items = self.run_case(ROWS, {'BF3100': {'decision': 'merge', 'color': 'ホワイト'}})
        self.assertEqual(items, [])
        self.assertEqual([r['status'] for r in pv['rows']], ['create'] * 3)
        self.assertEqual({(r.get('product') or {}).get('officialColor') for r in pv['rows']}, {'ホワイト'})


if __name__ == '__main__':
    unittest.main()
