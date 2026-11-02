import unittest
from unittest import mock
import legacy_csv_bulk_import as bulk
import legacy_csv_import as legacy


def plan(row, qty, cost, cond='new', status=legacy.STATUS_CREATE, size='M', pid=1):
    return {'rowNo': row, 'status': status, 'qty': qty, 'unitCost': cost, 'condition': cond,
            'reasons': [], 'csv': {'customer': 'A社', 'size': size},
            'target': {'productId': pid, 'embroidery': '刺繍なし', 'handleId': None, 'size': size},
            'place': {'siteName': '未確認', 'shelfName': ''}}


class MergeTest(unittest.TestCase):
    def test_same_target_summed(self):
        ps = [plan(2, 3, 1000), plan(3, 2, 1000), plan(4, 1, 1000, size='L')]
        n = bulk.merge_duplicate_opening_plans(None, ps)
        self.assertEqual(n, 1)
        self.assertEqual(ps[0]['qty'], 5)
        self.assertEqual(ps[1]['status'], bulk.MERGED_STATUS)
        self.assertEqual(ps[1]['mergedInto'], 2)
        self.assertEqual(ps[2]['status'], legacy.STATUS_CREATE)

    def test_weighted_cost_and_note(self):
        ps = [plan(2, 3, 1000), plan(3, 1, 2000)]
        bulk.merge_duplicate_opening_plans(None, ps)
        self.assertEqual(ps[0]['unitCost'], 1250)
        self.assertIn('加重平均', ps[0]['reasons'][-1])

    def test_zero_total_uses_first_price(self):
        ps = [plan(2, 0, 9550), plan(3, 0, 3150)]
        bulk.merge_duplicate_opening_plans(None, ps)
        self.assertEqual(ps[0]['unitCost'], 9550)
        self.assertIn('先頭行の単価', ps[0]['reasons'][-1])
        self.assertNotIn('加重平均', ps[0]['reasons'][-1])

    def test_negative_uses_first_price(self):
        ps = [plan(2, 5, 1000), plan(3, -2, 3000)]
        bulk.merge_duplicate_opening_plans(None, ps)
        self.assertEqual(ps[0]['unitCost'], 1000)
        self.assertEqual(ps[0]['qty'], 3)

    def test_codeless_size_correction_in_key(self):
        ps = [plan(2, 5, 2850, size='96-76'), plan(3, 6, 2850, size='100-92')]
        with mock.patch.object(bulk, '_codeless_stock_size', return_value='F'):
            fake = mock.MagicMock()
            n = bulk.merge_duplicate_opening_plans(fake, ps)
        self.assertEqual(n, 1)
        self.assertEqual(ps[0]['qty'], 11)

    def test_new_and_used_kept_apart(self):
        ps = [plan(2, 3, 1000, cond='new'), plan(3, 1, 1000, cond='used')]
        self.assertEqual(bulk.merge_duplicate_opening_plans(None, ps), 0)

    def test_review_rows_not_merged(self):
        ps = [plan(2, 3, 1000), plan(3, 1, 1000, status=legacy.STATUS_REVIEW)]
        self.assertEqual(bulk.merge_duplicate_opening_plans(None, ps), 0)

    def test_summary_count(self):
        s = bulk.summarize_opening_for_preview({'enabled': True, 'counts': {bulk.MERGED_STATUS: 2}})
        self.assertEqual(s['merged'], 2)


if __name__ == '__main__':
    unittest.main()
