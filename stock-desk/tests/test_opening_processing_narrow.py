"""初期在庫: 括弧加工で登録先取扱を絞る（白衣→加工なし、白衣(DHC刺繍あり)→DHC刺繍、＊白衣→加工なし）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import legacy_csv_bulk_import as L  # noqa: E402


def _row(mid, cpid, name, processing=''):
    return {
        'id': mid, 'product_id': 10, 'customer_product_id': cpid, 'customer_id': 1,
        'csv_customer': 'ＤＨＣ夕張長沼工場', 'csv_supplier': '', 'csv_product_name': name,
        'csv_size': 'M', 'csv_product_code': 'NPC71082', 'is_main_candidate': 1,
        'file_sha256': '', 'run_id': 1, 'source_row_no': mid, '_proc': processing,
    }


def _spec(row):
    return {'color': '', 'processing': row['_proc'], 'material': '', 'usageKind': '',
            'rowKind': '', 'processingProductId': None}


ROWS = [
    _row(1, 100, '白衣'),
    _row(2, 200, '白衣(DHC刺繍あり)', 'DHC刺繍'),
    _row(3, 100, '＊白衣'),
]


class T(unittest.TestCase):
    def resolve(self, name, rows=ROWS):
        c = mock.MagicMock()
        c.execute.return_value.fetchone.return_value = None
        with mock.patch.object(L, '_map_row_spec_full', _spec):
            return L._resolve_from_map_rows(c, rows, product_name=name, customer='ＤＨＣ夕張長沼工場',
                                            code_t='NPC71082', prefer_map_id=None)

    def dests(self, res):
        return {m['customerProductId'] for m in res['matches']}

    def test_plain_goes_to_no_processing(self):
        r = self.resolve('白衣')
        self.assertNotEqual(r['status'], 'ambiguous')
        self.assertEqual(self.dests(r), {100})

    def test_star_goes_to_no_processing(self):
        r = self.resolve('＊白衣')
        self.assertNotEqual(r['status'], 'ambiguous')
        self.assertEqual(self.dests(r), {100})

    def test_embroidery_goes_to_dhc(self):
        r = self.resolve('白衣(DHC刺繍あり)')
        self.assertNotEqual(r['status'], 'ambiguous')
        self.assertEqual(self.dests(r), {200})

    def test_embroidery_via_map_name_only(self):
        rows = [_row(1, 100, '白衣'), _row(2, 200, '白衣(DHC刺繍あり)')]
        self.assertEqual(self.dests(self.resolve('白衣(DHC刺繍あり)', rows)), {200})
        self.assertEqual(self.dests(self.resolve('白衣', rows)), {100})

    def test_two_processed_destinations_stay_ambiguous(self):
        rows = [_row(1, 100, '白衣(A刺繍)', 'A刺繍'), _row(2, 200, '白衣(B刺繍)', 'B刺繍')]
        self.assertEqual(self.resolve('白衣', rows)['status'], 'ambiguous')

    def test_names_equivalent_requires_same_processing(self):
        self.assertTrue(L._names_equivalent_core('白衣', '＊白衣'))
        self.assertFalse(L._names_equivalent_core('白衣', '白衣(DHC刺繍あり)'))
        self.assertTrue(L._names_equivalent_core('白衣(DHC刺繍あり)', '白衣(DHC刺繍あり)'))

    def test_shape_word_not_processing(self):
        self.assertEqual(L._name_processing_key('白衣(七分袖)'), '')


if __name__ == '__main__':
    unittest.main()
