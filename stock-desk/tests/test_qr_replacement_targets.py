import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli

class TestQrReplacementTargets(unittest.TestCase):
    def test_collect_qr_replacement_targets(self):
        sku_groups = [
            {
                'code': 'DOM0022',
                'colorSplit': True,
                'colorSplitPlans': {
                    'plans': [
                        {'internalCode': 'DOM0022-ダークネイビー', 'productName': 'スクラブ ダークネイビー', 'color': 'ダークネイビー'},
                        {'internalCode': 'DOM0022-ピンク', 'productName': 'スクラブ ヌードピンク', 'color': 'ピンク'},
                    ]
                }
            },
            {
                'code': 'AZ-1730-006',
                'colorSplit': False,
            }
        ]
        targets = pli.collect_qr_replacement_targets(sku_groups)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]['originalCode'], 'DOM0022')
        self.assertEqual(targets[0]['splitType'], 'color')
        self.assertEqual(len(targets[0]['splitItems']), 2)
        self.assertEqual(targets[0]['splitItems'][0]['code'], 'DOM0022-ダークネイビー')
        self.assertEqual(targets[0]['splitItems'][1]['code'], 'DOM0022-ピンク')

if __name__ == '__main__':
    unittest.main()
