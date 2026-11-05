"""月末締めの保存・上書き・棚卸連携（テスト用DBのみ）。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock
import stock_month_close as mc
import stock_stocktake as st

W = {'_canWrite': True, '_actor': {'name': 'テスト担当'}}


def setup():
    folder = tempfile.TemporaryDirectory()
    c = stock.connect(Path(folder.name) / 'stock.sqlite3')
    stock.initialize(c)
    cu = stock.save_customer(c, {'name': '締め病院', 'active': True, 'officeCode': 'C01'})
    su = stock.save_supplier(c, {'name': '仕入先', 'active': True})
    pr = stock.save_product(c, {'code': 'MC-1', 'name': '白衣', 'color': '白', 'supplierId': su['id'], 'active': True,
                                'sizes': ['M'],
                                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}]})
    loc = stock.save_location(c, {'siteName': '締め倉庫', 'active': True})
    h = pr['links'][0]['id']
    stock.register_inbound(c, {'token': 'mc-seed-in-1', 'customerProductId': h, 'size': 'M', 'locationId': loc['id'],
                               'qty': 10, 'unitCost': 1000, 'receivedOn': '2026-09-01'})
    return folder, c, cu, h, loc


class MonthCloseTest(unittest.TestCase):
    def test_close_overwrite_and_export(self):
        folder, c, cu, h, loc = setup()
        with folder, c:
            s = mc.close_month(c, {**W, 'month': '2026-10'})
            self.assertTrue(s['closed'])
            self.assertEqual(s['totalQty'], 10)
            self.assertEqual(s['rows'][0]['productCode'], 'MC-1')
            self.assertEqual(s['rows'][0]['qty'], 10)
            self.assertEqual(s['header']['closedBy'], 'テスト担当')
            with self.assertRaises(ValueError) as ar:
                mc.close_month(c, {**W, 'month': '2026-10'})
            self.assertIn('上書き', str(ar.exception))
            stock.register_inbound(c, {'token': 'mc-seed-in-2', 'customerProductId': h, 'size': 'M',
                                       'locationId': loc['id'], 'qty': 5, 'unitCost': 1100, 'receivedOn': '2026-10-05'})
            s = mc.close_month(c, {**W, 'month': '2026-10', 'confirm': True})
            self.assertEqual(s['totalQty'], 15)
            data, name = mc.export_xlsx(c, '2026-10')
            self.assertTrue(name.endswith('.xlsx'))
            self.assertGreater(len(data), 100)
            months = mc.list_closes(c)['months']
            self.assertEqual(months[0]['month'], '2026-10')
            js = (Path(__file__).resolve().parents[1] / 'static' / 'month_close.js').read_text(encoding='utf-8')
            self.assertIn('mc-flow', js)
            self.assertIn('実棚 − 締め時在庫数', js)

    def test_example_flow_close10_actual9_out3_to6(self):
        folder, c, cu, h, loc = setup()
        with folder, c:
            mc.close_month(c, {**W, 'month': '2026-10'})
            s = st.add_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
            r = s['rows'][0]
            self.assertEqual(r['bookQty'], 10)
            st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': r['id'], 'actualQty': 9}]})
            stock.register_outbound(c, {**W, 'token': 'mc-out-after', 'customerProductId': h, 'size': 'M',
                                        'locationId': loc['id'], 'qty': 3, 'businessOn': '2026-10-20'})
            self.assertEqual(stock.current_qty_for_target(c, h, 'M', loc['id']), 7)
            st.apply(c, {**W, 'month': '2026-10', 'confirm': True})
            self.assertEqual(stock.current_qty_for_target(c, h, 'M', loc['id']), 6)


if __name__ == '__main__':
    unittest.main()
