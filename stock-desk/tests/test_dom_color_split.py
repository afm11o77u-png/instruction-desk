import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import code_suffix_attributes as suffix
import product_ledger_import as pli
import stock

class TestDomColorSplit(unittest.TestCase):
    def test_classify_dom_colors(self):
        cases = [
            ('DOM0020 C-50', 'DOM0020', 'ダークネイビー(C50)'),
            ('DOM0020 C-24', 'DOM0020', 'ヌードピンク(C24)'),
            ('DOM0022 C-50', 'DOM0022', 'ダークネイビー(C50)'),
            ('DOM0022 C-24', 'DOM0022', 'ヌードピンク(C24)'),
            ('DOM0023 C-92', 'DOM0023', 'ダークグレー(C92)'),
            ('DOM0023 C-50', 'DOM0023', 'ダークネイビー(C50)'),
            ('DOM0023 C-24', 'DOM0023', 'ヌードピンク(C24)'),
        ]
        for raw, expected_base, expected_color in cases:
            res = suffix.classify_suffix(raw)
            self.assertEqual(res['baseCode'], expected_base, f"Failed baseCode for {raw}")
            self.assertEqual(res['color'], expected_color, f"Failed color for {raw}")

    def test_dom_auto_color_split_in_preview(self):
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            conn = stock.connect(path)
            stock.initialize(conn)
            pli.ensure_schema(conn)

            csv_text = (
                "顧客,仕入先,商品コード,商品名,サイズ\n"
                "関歯科医院,チトセ㈱,DOM0022 C-50,スクラブ ダークネイビー,S\n"
                "関歯科医院,チトセ㈱,DOM0022 C-50,スクラブ ダークネイビー,M\n"
                "関歯科医院,チトセ㈱,DOM0022 C-24,スクラブ ヌードピンク,S\n"
                "関歯科医院,チトセ㈱,DOM0022 C-24,スクラブ ヌードピンク,M\n"
                "関歯科医院,チトセ㈱,DOM0023 C-92,ズボン ダークグレー,S\n"
                "関歯科医院,チトセ㈱,DOM0023 C-50,ズボン ダークネイビー,S\n"
                "関歯科医院,チトセ㈱,DOM0023 C-24,ズボン ヌードピンク,S\n"
            )
            body = {
                'importMode': 'products',
                'name': 'dom_test.csv',
                'file': base64.b64encode(csv_text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True,
                '_actor': {'name': 'テスト'},
            }
            preview = pli.build_preview(conn, body)
            by_code = {g['code']: g for g in preview.get('skuGroups') or []}
            
            # DOM0022 should be colorSplit
            g22 = by_code.get('DOM0022')
            self.assertIsNotNone(g22)
            self.assertTrue(g22.get('colorSplit'), "DOM0022 should be automatically colorSplit")
            plans22 = (g22.get('colorSplitPlans') or {}).get('plans') or []
            self.assertEqual(len(plans22), 2)
            colors22 = {p['color'] for p in plans22}
            self.assertIn('ダークネイビー(C50)', colors22)
            self.assertIn('ヌードピンク(C24)', colors22)

            # DOM0023 should be colorSplit with 3 colors
            g23 = by_code.get('DOM0023')
            self.assertIsNotNone(g23)
            self.assertTrue(g23.get('colorSplit'), "DOM0023 should be automatically colorSplit")
            plans23 = (g23.get('colorSplitPlans') or {}).get('plans') or []
            self.assertEqual(len(plans23), 3)
            colors23 = {p['color'] for p in plans23}
            self.assertIn('ダークグレー(C92)', colors23)
            self.assertIn('ダークネイビー(C50)', colors23)
            self.assertIn('ヌードピンク(C24)', colors23)

            # Rows should be resolved (canCommit == True, not review)
            for row in preview['rows']:
                self.assertTrue(row.get('canCommit'), f"Row {row['rowNo']} should be ready to commit")
                self.assertNotEqual(row.get('status'), 'review', f"Row {row['rowNo']} should not be review")
        finally:
            conn.close()
            folder.cleanup()

if __name__ == '__main__':
    unittest.main()
