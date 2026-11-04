"""棚卸表（顧客×月）の作成・保存・上書きの検証（テスト用DBのみ）。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock
import stock_stocktake as st

W = {'_canWrite': True, '_actor': {'name': 'テスト担当'}}


def setup():
    folder = tempfile.TemporaryDirectory()
    c = stock.connect(Path(folder.name) / 'stock.sqlite3')
    stock.initialize(c)
    cu = stock.save_customer(c, {'name': '棚卸病院', 'active': True, 'officeCode': 'K01'})
    other = stock.save_customer(c, {'name': '対象外病院', 'active': True})
    su = stock.save_supplier(c, {'name': '仕入先', 'active': True})
    pr = stock.save_product(c, {'code': 'TK-1', 'name': '白衣', 'color': '白', 'supplierId': su['id'], 'active': True,
                                'sizes': ['M', 'L'],
                                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}]})
    loc = stock.save_location(c, {'siteName': '棚卸テスト倉庫', 'active': True})
    h = pr['links'][0]['id']
    stock.register_inbound(c, {'token': 'tk-seed-in-a1', 'customerProductId': h, 'size': 'M', 'locationId': loc['id'],
                               'qty': 3, 'unitCost': 1000, 'receivedOn': '2026-09-01'})
    stock.register_inbound(c, {'token': 'tk-seed-in-b1', 'customerProductId': h, 'size': 'M', 'locationId': loc['id'],
                               'qty': 2, 'unitCost': 1200, 'receivedOn': '2026-10-01'})
    stock.register_stocktake(c, {**W, 'token': 'tk-seed-pend1', 'customerProductId': h, 'size': 'L', 'locationId': loc['id'],
                                 'actualQty': 4, 'businessOn': '2026-10-02'})
    return folder, c, cu, other, h, loc


class StocktakeSheetTest(unittest.TestCase):
    def test_any_customer_selectable_per_month(self):
        folder, c, cu, other, *_ = setup()
        with folder, c:
            self.assertNotIn('stocktakeTarget', cu)
            ids = {x['id'] for x in st.list_stocktakes(c)['customers']}
            self.assertEqual(ids, {cu['id'], other['id']})
            a = st.create(c, {**W, 'customerId': cu['id'], 'month': '2026-10'})
            b = st.create(c, {**W, 'customerId': other['id'], 'month': '2026-10'})
            self.assertNotEqual(a['id'], b['id'])
            self.assertEqual(b['rows'], [])
            self.assertEqual(len(st.list_stocktakes(c)['stocktakes']), 2)
            with self.assertRaises(PermissionError if hasattr(stock, 'PermissionError') else Exception):
                st.create(c, {'_canWrite': False, 'customerId': cu['id'], 'month': '2026-10'})

    def test_create_save_apply(self):
        folder, c, cu, _, h, loc = setup()
        with folder, c:
            s = st.create(c, {**W, 'customerId': cu['id'], 'month': '2026-10'})
            rows = {r['size']: r for r in s['rows']}
            m, l = rows['M'], rows['L']
            self.assertEqual((m['officeCode'], m['productCode'], m['bookQty'], m['bookAmount']), ('K01', 'TK-1', 5, 5400))
            self.assertTrue(m['unitMixed'])
            self.assertIsNone(l['bookAmount'])  # 原価未確定
            again = st.create(c, {**W, 'customerId': cu['id'], 'month': '2026-10'})
            self.assertEqual((again['id'], again.get('already')), (s['id'], True))
            s = st.save(c, {**W, 'id': s['id'], 'rows': [{'id': m['id'], 'actualQty': 2}, {'id': l['id'], 'actualQty': ''}]})
            m = {r['size']: r for r in s['rows']}['M']
            # FIFO: 古い1000円から3点払出し → 残 1200×2
            self.assertEqual((m['diffQty'], m['actualAmount'], m['diffAmount']), (-3, 2400, -3000))
            with self.assertRaises(ValueError):
                st.apply(c, {**W, 'id': s['id']})
            s = st.apply(c, {**W, 'id': s['id'], 'confirm': True})
            self.assertEqual(s['adjusted'], 1)
            self.assertTrue(s['applied'])
            self.assertEqual(s['appliedBy'], 'テスト担当')
            self.assertEqual(stock.current_qty_for_target(c, h, 'M', loc['id']), 2)
            self.assertEqual(stock.current_qty_for_target(c, h, 'L', loc['id']), 4)
            note = c.execute("SELECT note FROM move_batches WHERE kind='stocktake' ORDER BY id DESC").fetchone()['note']
            self.assertIn(f"棚卸#{s['id']}", note)
            with self.assertRaises(ValueError):
                st.save(c, {**W, 'id': s['id'], 'rows': [{'id': m['id'], 'actualQty': 9}]})

    def test_increase_uses_lot_price(self):
        folder, c, cu, _, h, loc = setup()
        with folder, c:
            s = st.create(c, {**W, 'customerId': cu['id'], 'month': '2026-11'})
            m = {r['size']: r for r in s['rows']}['M']
            self.assertEqual(m['incPrice'], 1200)
            s = st.save(c, {**W, 'id': s['id'], 'rows': [{'id': m['id'], 'actualQty': 6}]})
            m = {r['size']: r for r in s['rows']}['M']
            self.assertEqual((m['actualAmount'], m['diffAmount']), (6600, 1200))
            st.apply(c, {**W, 'id': s['id'], 'confirm': True})
            self.assertEqual(stock.current_qty_for_target(c, h, 'M', loc['id']), 6)


if __name__ == '__main__':
    unittest.main()
