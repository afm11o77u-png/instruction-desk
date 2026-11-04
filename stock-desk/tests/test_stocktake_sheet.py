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
    def test_monthly_sheet_add_remove(self):
        folder, c, cu, other, *_ = setup()
        with folder, c:
            ids = {x['id'] for x in st.list_stocktakes(c)['customers']}
            self.assertEqual(ids, {cu['id'], other['id']})
            empty = st.get(c, '2026-10')
            self.assertEqual((empty['id'], empty['rows']), (None, []))
            s = st.add_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
            self.assertEqual(s['added'], 2)
            s = st.add_customer(c, {**W, 'month': '2026-10', 'customerId': other['id']})
            self.assertEqual([b['customerId'] for b in s['blocks']], [cu['id'], other['id']])
            again = st.add_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
            self.assertTrue(again['already'])
            self.assertEqual(len(again['rows']), 2)
            s = st.remove_customer(c, {**W, 'month': '2026-10', 'customerId': other['id']})
            self.assertEqual(len(s['blocks']), 1)
            months = st.list_stocktakes(c)['months']
            self.assertEqual([(m['month'], m['customerCount']) for m in months], [('2026-10', 1)])
            with self.assertRaises(Exception):
                st.add_customer(c, {'_canWrite': False, 'month': '2026-10', 'customerId': other['id']})

    def test_save_apply_per_customer(self):
        folder, c, cu, other, h, loc = setup()
        with folder, c:
            st.add_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
            s = st.get(c, '2026-10')
            rows = {r['size']: r for r in s['rows']}
            m, l = rows['M'], rows['L']
            self.assertEqual((m['officeCode'], m['productCode'], m['bookQty'], m['bookAmount']), ('K01', 'TK-1', 5, 5400))
            self.assertTrue(m['unitMixed'])
            self.assertIsNone(l['bookAmount'])
            s = st.save(c, {**W, 'month': '2026-10', 'rows': [
                {'id': m['id'], 'actualQty': 2, 'note': '破損1'}, {'id': l['id'], 'actualQty': ''}]})
            rows = {r['size']: r for r in s['rows']}
            self.assertEqual((rows['M']['diffQty'], rows['M']['actualAmount'], rows['M']['diffAmount'], rows['M']['note']),
                             (-3, 2400, -3000, '破損1'))
            self.assertIsNone(rows['L']['actualQty'])  # 空欄＝未入力（0ではない）
            s = st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': l['id'], 'actualQty': 0}]})
            self.assertEqual({r['size']: r for r in s['rows']}['L']['actualQty'], 0)
            s = st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': l['id'], 'actualQty': ''}]})
            with self.assertRaises(ValueError):
                st.apply(c, {**W, 'month': '2026-10'})
            s = st.apply(c, {**W, 'month': '2026-10', 'confirm': True})
            self.assertEqual((s['adjusted'], s['appliedCustomers']), (1, 1))
            self.assertEqual(s['blocks'][0]['appliedBy'], 'テスト担当')
            self.assertEqual(stock.current_qty_for_target(c, h, 'M', loc['id']), 2)
            self.assertEqual(stock.current_qty_for_target(c, h, 'L', loc['id']), 4)
            with self.assertRaises(ValueError):
                st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': m['id'], 'actualQty': 9}]})
            with self.assertRaises(ValueError):
                st.remove_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
            # 後から追加した顧客は後で反映できる
            s = st.add_customer(c, {**W, 'month': '2026-10', 'customerId': other['id']})
            self.assertFalse(s['blocks'][1]['applied'])

    def test_increase_uses_lot_price(self):
        folder, c, cu, _, h, loc = setup()
        with folder, c:
            s = st.add_customer(c, {**W, 'month': '2026-11', 'customerId': cu['id']})
            m = {r['size']: r for r in s['rows']}['M']
            self.assertEqual(m['incPrice'], 1200)
            s = st.save(c, {**W, 'month': '2026-11', 'rows': [{'id': m['id'], 'actualQty': 6}]})
            m = {r['size']: r for r in s['rows']}['M']
            self.assertEqual((m['actualAmount'], m['diffAmount']), (6600, 1200))
            st.apply(c, {**W, 'month': '2026-11', 'confirm': True})
            self.assertEqual(stock.current_qty_for_target(c, h, 'M', loc['id']), 6)

    def test_old_per_customer_tables_dropped_moves_kept(self):
        folder = tempfile.TemporaryDirectory()
        with folder:
            path = Path(folder.name) / 'stock.sqlite3'
            c = stock.connect(path)
            stock.initialize(c)
            c.execute('DROP TABLE stocktake_sheet_rows')
            c.execute('DROP TABLE stocktake_blocks')
            c.execute('DROP TABLE stocktake_sheets')
            c.execute('CREATE TABLE stocktakes (id INTEGER PRIMARY KEY, customer_id INTEGER, month TEXT)')
            c.execute('CREATE TABLE stocktake_rows (id INTEGER PRIMARY KEY, stocktake_id INTEGER, applied_batch_id INTEGER)')
            c.execute("INSERT INTO stocktakes VALUES(1, 1, '2026-10')")
            c.execute("INSERT INTO move_batches(client_token,kind,note,created_at) VALUES('old-st-batch-1','stocktake','棚卸調整（2026-10 棚卸#1）','2026-10-31')")
            c.commit()
            c.close()
            c = stock.connect(path)
            stock.initialize(c)
            names = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn('stocktakes', names)
            self.assertNotIn('stocktake_rows', names)
            self.assertIn('stocktake_sheets', names)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM move_batches WHERE client_token='old-st-batch-1'").fetchone()[0], 1)
            c.close()


if __name__ == '__main__':
    unittest.main()
