"""色別: 全角スペース後の色を自動割当／色なし行は登録しない（合成データ・一時DB）。"""
from __future__ import annotations

import unittest

import test_ledger_review_five_fixes as base
from test_ledger_review_five_fixes import pli

ROWS = ['顧客A,仕入Z,BF9,帽子,F',
        '顧客A,仕入Z,BF9,帽子,S',
        '顧客A,仕入Z,BF9,サラドラツバ付きズキン　ホワイト,M',
        '顧客A,仕入Z,BF9,＊　サラドラツバ付きズキン　ホワイト,L',
        '顧客A,仕入Z,BF9,ツバ付きズキン　ブラック,L']


class ColorSplitNoColorTest(unittest.TestCase):
    run_case = base.ReviewFiveFixesTest.run_case

    def test_zukin_kin_is_not_gold(self):
        r = pli._row_color_extraction({'csv': {'productName': 'サラドラツバ付きズキン　ホワイト'}})
        self.assertEqual((r['color'], r['colorCandidates']), ('ホワイト', ['ホワイト']))
        r = pli._row_color_extraction({'csv': {'productName': 'シャツ　キン'}})
        self.assertEqual(r['color'], 'ゴールド')

    def test_split_auto_assigns_and_drops_no_color_rows(self):
        pv, items = self.run_case(ROWS, {'BF9': {'decision': 'color_split'}})
        split = pv['skuGroups'][0]['colorSplitPlans']
        self.assertEqual(sorted((p['color'], tuple(p['rowNos'])) for p in split['plans']),
                         [('ブラック', (6,)), ('ホワイト', (4, 5))])
        self.assertEqual(split['unassignedRows'], [])
        self.assertEqual(split['noColorRows'], [2, 3])
        st = {r['rowNo']: (r['status'], r['canCommit']) for r in pv['rows']}
        self.assertEqual(st[2], ('hold', False))
        self.assertEqual(st[4], ('create', True))
        self.assertEqual(items, [])


if __name__ == '__main__':
    unittest.main()
