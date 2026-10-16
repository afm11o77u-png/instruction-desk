"""マイナス在庫・不足出庫・原価未確定・棚卸・FIFO境界の検証（テスト用DBのみ）。"""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def seed_masters(c):
    cu = stock.save_customer(c, {'name': 'テスト病院', 'active': True})
    su = stock.save_supplier(c, {'name': 'テスト仕入先', 'active': True})
    pr = stock.save_product(c, {
        'code': 'SCR-SH', 'name': '不足スクラブ', 'color': '白',
        'supplierId': su['id'], 'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '石狩倉庫不足テスト', 'active': True})
    handle = pr['links'][0]['id']
    return cu, pr, loc, handle


class ShortageStockTest(unittest.TestCase):
    def test_outbound_from_positive_zero_and_negative(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'sh-in-pos-01', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            # 2→-1
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 3,
            })
            self.assertEqual(plan['shortfall'], 1)
            self.assertTrue(plan['needsShortageConfirm'])
            with self.assertRaises(ValueError):
                stock.register_outbound(c, {
                    'token': 'sh-out-need-confirm', 'customerProductId': handle, 'size': 'M',
                    'locationId': loc['id'], 'qty': 3, 'fingerprint': plan['fingerprint'],
                })
            stock.register_outbound(c, {
                'token': 'sh-out-2-to-neg', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'fingerprint': plan['fingerprint'],
                'confirmShortage': True,
            })
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), -1)
            # 0在庫からの出庫（別サイズ）
            plan0 = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'L', 'locationId': loc['id'], 'qty': 3,
            })
            self.assertEqual(plan0['shortfall'], 3)
            stock.register_outbound(c, {
                'token': 'sh-out-from-zero', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 3, 'fingerprint': plan0['fingerprint'],
                'confirmShortage': True,
            })
            self.assertEqual(stock.current_qty_for_target(c, handle, 'L', loc['id']), -3)
            # -3 → -5
            plan_n = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'L', 'locationId': loc['id'], 'qty': 2,
            })
            stock.register_outbound(c, {
                'token': 'sh-out-more-neg', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 2, 'fingerprint': plan_n['fingerprint'],
                'confirmShortage': True,
            })
            self.assertEqual(stock.current_qty_for_target(c, handle, 'L', loc['id']), -5)
            view = stock.stock_query(c, {'stockFilter': 'shortage'})
            self.assertTrue(all(l['qty'] < 0 for l in view['lots']))
            self.assertTrue(any(l['costPending'] for l in view['lots']))
            # 金額に原価未確定を混ぜない
            self.assertEqual(view['totalAmount'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_inbound_cover_partial_exact_excess(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 5,
            })
            stock.register_outbound(c, {
                'token': 'sh-cover-out', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'fingerprint': plan['fingerprint'],
                'confirmShortage': True,
            })
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), -5)
            # 一部補充 -5+2=-3
            stock.register_inbound(c, {
                'token': 'sh-cover-part', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 1500, 'receivedOn': '2026-10-10',
            })
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), -3)
            # ちょうど解消 -3+3=0
            stock.register_inbound(c, {
                'token': 'sh-cover-exact', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 1600, 'receivedOn': '2026-10-11',
            })
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), 0)
            # 超過補充 0+4=4（二重差し引きしない）
            stock.register_inbound(c, {
                'token': 'sh-cover-over', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 1700, 'receivedOn': '2026-10-12',
            })
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), 4)
            view = stock.stock_query(c, {})
            self.assertEqual(view['totalQty'], 4)
            self.assertEqual(view['totalAmount'], 4 * 1700)
            kinds = {r['kind'] for r in c.execute('SELECT kind FROM moves')}
            self.assertIn('outbound_shortage', kinds)
            self.assertIn('shortage_cover', kinds)
            self.assertIn('inbound', kinds)
        finally:
            c.close()
            folder.cleanup()

    def test_fifo_multi_price_and_no_cross_bucket(self):
        folder, path, c = newdb()
        try:
            cu, pr, loc, handle = seed_masters(c)
            # 別顧客
            cu2 = stock.save_customer(c, {'name': '別病院', 'active': True})
            stock.save_product(c, {
                'id': pr['id'], 'code': pr['code'], 'name': pr['name'], 'color': pr['color'],
                'supplierId': pr['supplierId'], 'active': True, 'sizes': ['M', 'L'],
                'links': [
                    {'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                    {'customerId': cu2['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                ],
            })
            handle2 = c.execute(
                'SELECT id FROM customer_products WHERE customer_id=? AND product_id=?',
                (cu2['id'], pr['id']),
            ).fetchone()['id']
            stock.register_inbound(c, {
                'token': 'fifo-a-1000', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 1000, 'receivedOn': '2026-09-01',
            })
            stock.register_inbound(c, {
                'token': 'fifo-b-2000', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 2000, 'receivedOn': '2026-09-15',
            })
            stock.register_inbound(c, {
                'token': 'other-cust', 'customerProductId': handle2, 'size': 'M',
                'locationId': loc['id'], 'qty': 50, 'unitCost': 100, 'receivedOn': '2026-08-01',
            })
            stock.register_inbound(c, {
                'token': 'used-stock', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 20, 'unitCost': 50, 'receivedOn': '2026-08-01',
                'condition': 'used',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 8,
                'condition': 'new',
            })
            # 3+3=6 FIFO, shortfall 2 — 別顧客・中古は使わない
            self.assertEqual(plan['shortfall'], 2)
            self.assertEqual([a['unitCost'] for a in plan['allocations']], [1000, 2000])
            self.assertEqual([a['qty'] for a in plan['allocations']], [3, 3])
            stock.register_outbound(c, {
                'token': 'fifo-short-out', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 8, 'fingerprint': plan['fingerprint'],
                'confirmShortage': True, 'condition': 'new',
            })
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id'], 'new'), -2)
            self.assertEqual(stock.current_qty_for_target(c, handle2, 'M', loc['id']), 50)
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id'], 'used'), 20)
        finally:
            c.close()
            folder.cleanup()

    def test_idempotent_and_concurrent_with_shortage_confirm(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'idem-in-token', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 900, 'receivedOn': '2026-10-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 1,
            })
            a = stock.register_outbound(c, {
                'token': 'idem-out-once', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'fingerprint': plan['fingerprint'],
            })
            b = stock.register_outbound(c, {
                'token': 'idem-out-once', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'fingerprint': plan['fingerprint'],
            })
            self.assertFalse(a['already'])
            self.assertTrue(b['already'])
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), 0)
            c.commit()
            c.close()

            errors = []
            results = []

            def worker(token):
                conn = stock.connect(path)
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    p = stock.plan_outbound(conn, {
                        'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 2,
                    })
                    out = stock.register_outbound(conn, {
                        'token': token, 'customerProductId': handle, 'size': 'M',
                        'locationId': loc['id'], 'qty': 2, 'fingerprint': p['fingerprint'],
                        'confirmShortage': True,
                    })
                    conn.commit()
                    results.append(out)
                except Exception as e:
                    conn.rollback()
                    errors.append(str(e))
                finally:
                    conn.close()

            t1 = threading.Thread(target=worker, args=('concurrent-sh-a',))
            t2 = threading.Thread(target=worker, args=('concurrent-sh-b',))
            t1.start(); t2.start(); t1.join(); t2.join()
            self.assertEqual(len(results) + len(errors), 2)
            self.assertGreaterEqual(len(results), 1)
            c2 = stock.connect(path)
            qty = stock.current_qty_for_target(c2, handle, 'M', loc['id'])
            # 成功した出庫分だけマイナスになる（二重成功なら -4、片方なら -2）
            self.assertIn(qty, (-2, -4))
            if len(results) == 2:
                self.assertEqual(qty, -4)
            else:
                self.assertEqual(qty, -2)
            c2.close()
        finally:
            folder.cleanup()

    def test_stocktake_and_history_correct(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'st-in-token1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            # 棚卸で 5→3（減）
            st = stock.register_stocktake(c, {
                'token': 'st-down-token', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'actualQty': 3,
            })
            self.assertEqual(st['delta'], -2)
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), 3)
            # 棚卸で 3→7（増・原価未確定）
            st2 = stock.register_stocktake(c, {
                'token': 'st-up-pending-tok', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'actualQty': 7,
            })
            self.assertEqual(st2['delta'], 4)
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), 7)
            view = stock.stock_query(c, {})
            # 確定金額は元の3枚分のみ（増分は原価未確定）
            self.assertEqual(view['totalAmount'], 3 * 1000)
            move = c.execute(
                "SELECT id FROM moves WHERE kind='stocktake' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            corr = stock.correct_history_move(c, {
                'moveId': move['id'], 'reason': '棚卸の取消テスト',
            })
            self.assertEqual(corr['mode'], 'void')
            kinds = {r['kind'] for r in c.execute('SELECT kind FROM moves')}
            self.assertIn('move_void', kinds)
            self.assertIn('stocktake', kinds)
        finally:
            c.close()
            folder.cleanup()

    def test_list_keeps_zero_negative_no_auto_combo(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'list-in-m', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 800, 'receivedOn': '2026-10-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 1,
            })
            stock.register_outbound(c, {
                'token': 'list-out-m', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'fingerprint': plan['fingerprint'],
            })
            targets = stock.move_targets(c, {'mode': 'out'})
            sizes = {t['size'] for t in targets['targets'] if t['customerProductId'] == handle}
            self.assertIn('M', sizes)
            self.assertNotIn('L', sizes)  # 未登録組合せは自動生成しない
            # 0行が残る
            m = next(t for t in targets['targets'] if t['size'] == 'M')
            self.assertEqual(m['qty'], 0)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
