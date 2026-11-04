"""棚卸表（顧客×月）の作成・保存・上書きの検証（テスト用DBのみ）。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock
import stock_stocktake as st

W = {'_canWrite': True, '_actor': {'name': 'テスト担当'}}


def seed():
    folder = tempfile.TemporaryDirectory()
    c = stock.connect(Path(folder.name) / 'stock.sqlite3')
    stock.initialize(c)
    cu = stock.save_customer(c, {'name': '並び病院', 'active': True})
    loc = stock.save_location(c, {'siteName': '並び倉庫', 'active': True})
    n = [0]
    sups = {}

    def item(sup, code, sizes):
        su = sups.get(sup) or sups.setdefault(sup, stock.save_supplier(c, {'name': sup, 'active': True}))
        pr = stock.save_product(c, {'code': code, 'name': code, 'supplierId': su['id'], 'active': True, 'sizes': sizes,
                                    'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}]})
        for z in sizes:
            n[0] += 1
            stock.register_inbound(c, {'token': f'row-order-token-{n[0]:04d}', 'customerProductId': pr['links'][0]['id'], 'size': z,
                                       'locationId': loc['id'], 'qty': 1, 'unitCost': 100, 'receivedOn': '2026-09-01'})
        return pr
    return folder, c, cu, item


def seq(s):
    return [(r['productCode'], r['size']) for r in s['rows']]


class StocktakeRowOrderTest(unittest.TestCase):
    def test_size_key(self):
        got = sorted(['F', '3L', 'LL', 'M', 'S', 'L', '4L', 'SS', '100', '90', '5L'], key=st.size_key)
        self.assertEqual(got, ['90', '100', 'SS', 'S', 'M', 'L', 'LL', '3L', '4L', '5L', 'F'])

    def test_default_order_supplier_sjis_code_size(self):
        folder, c, cu, item = seed()
        with folder, c:
            item('住商モンブラン㈱', 'A-1', ['M'])
            item('㈱サーヴォ', 'Z-9', ['L', 'S'])
            item('㈱サーヴォ', 'B-2', ['3L', 'LL'])
            item('アイトス㈱', 'Q-1', ['F'])
            s = st.add_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
            self.assertEqual(seq(s), [('Q-1', 'F'), ('B-2', 'LL'), ('B-2', '3L'), ('Z-9', 'S'), ('Z-9', 'L'), ('A-1', 'M')])

    def test_saved_order_survives_readd_and_next_month(self):
        folder, c, cu, item = seed()
        with folder, c:
            item('A社', 'A-1', ['M'])
            item('B社', 'B-1', ['M'])
            s = st.add_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
            ids = [r['id'] for r in s['rows']][::-1]
            s = st.save_order(c, {**W, 'month': '2026-10', 'customerId': cu['id'], 'rowIds': ids})
            self.assertEqual(seq(s), [('B-1', 'M'), ('A-1', 'M')])
            st.remove_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
            item('A社', 'A-0', ['M'])   # 新規品は既定順の位置（直前の行の後ろ）へ
            s = st.add_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
            self.assertEqual(seq(s), [('B-1', 'M'), ('A-0', 'M'), ('A-1', 'M')])
            s = st.add_customer(c, {**W, 'month': '2026-11', 'customerId': cu['id']})
            self.assertEqual(seq(s), [('B-1', 'M'), ('A-0', 'M'), ('A-1', 'M')])
            with self.assertRaises(ValueError):
                st.save_order(c, {**W, 'month': '2026-11', 'customerId': cu['id'], 'rowIds': ids[:1]})


if __name__ == '__main__':
    unittest.main()
