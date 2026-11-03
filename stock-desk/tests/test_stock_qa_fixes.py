"""QA指摘の修正（取消制限・原価未確定の扱い・不足出庫の登録単価など）。一時DBのみ。"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock
import stock_history
import stock_orders
import stock_period
import stock_prices
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_purchase_orders import newdb, seed, set_price

TODAY = date.today().isoformat()
W = {'_canWrite': True}


def ordered_po(c, fx, sizes, token):
    po = stock_orders.save_purchase_order(c, {
        'token': 'qa-token-' + token, 'supplierId': fx['supplier']['id'], 'customerId': fx['customer']['id'],
        'lines': [{'customerProductId': fx['h1'], 'sizes': sizes, 'note': ''}], **W,
    })
    stock_orders.mark_ordered(c, {'id': po['id'], **W})
    return po


class QaFixesTest(unittest.TestCase):
    def setUp(self):
        self.folder, _, self.c = newdb()
        self.fx = seed(self.c)

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def receive(self, po, qty, on=TODAY, token='r1', line=0):
        return stock_orders.receive_purchase_order(self.c, {
            'id': po['id'], 'token': 'qa-token-' + token, 'receivedOn': on, 'locationId': self.fx['loc']['id'],
            'items': [{'lineId': po['lines'][line]['id'], 'qty': qty}], **W,
        })

    def test_cancel_unreceived_ok_and_resend_flag_hidden(self):
        po = ordered_po(self.c, self.fx, {'M': 2}, 'p1')
        stock_orders.save_purchase_order(self.c, {
            'id': po['id'], 'token': 'qa-token-' + 'p1e', 'supplierId': self.fx['supplier']['id'],
            'customerId': self.fx['customer']['id'],
            'lines': [{'customerProductId': self.fx['h1'], 'sizes': {'M': 3}, 'note': ''}], **W,
        })
        self.assertTrue(stock_orders.get_purchase_order(self.c, po['id'])['needsResend'])
        done = stock_orders.cancel_purchase_order(self.c, {'id': po['id'], **W})
        self.assertEqual(done['status'], 'cancelled')
        self.assertFalse(done['needsResend'])

    def test_complete_po_cannot_be_cancelled(self):
        po = ordered_po(self.c, self.fx, {'M': 1}, 'p2')
        self.receive(po, 1)
        with self.assertRaisesRegex(ValueError, '取消できません'):
            stock_orders.cancel_purchase_order(self.c, {'id': po['id'], **W})

    def test_future_receipt_date_rejected(self):
        po = ordered_po(self.c, self.fx, {'M': 1}, 'p3')
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError, '未来の日付'):
            self.receive(po, 1, on=tomorrow)

    def test_pending_receipt_shown_as_pending(self):
        # LL の価格を消す → 原価未確定で入荷
        self.c.execute('DELETE FROM price_base WHERE product_id=? AND size_label=?', (self.fx['p1']['id'], 'LL'))
        po = ordered_po(self.c, self.fx, {'LL': 2, 'M': 1}, 'p4')
        lines = {l['size']: l for l in po['lines']}
        stock_orders.receive_purchase_order(self.c, {
            'id': po['id'], 'token': 'qa-token-' + 'r4', 'receivedOn': TODAY, 'locationId': self.fx['loc']['id'],
            'items': [{'lineId': lines['LL']['id'], 'qty': 2}, {'lineId': lines['M']['id'], 'qty': 1}], **W,
        })
        period = stock_period.summarize_period(self.c, {'from': TODAY, 'to': TODAY})
        ll = next(r for r in period['rows'] if r['size'] == 'LL')
        self.assertIsNone(ll['inboundAmount'])
        self.assertEqual(ll['costPendingInboundQty'], 2)
        self.assertTrue(all(h['costPending'] and h['unitCost'] is None for h in ll['history']))
        self.assertEqual(period['totals']['inboundAmount'], 1000)
        self.assertEqual(period['totals']['costPendingInboundQty'], 2)
        items = stock_history.list_history(self.c, {})['events']
        ll_items = [h for h in items if h['size'] == 'LL']
        self.assertTrue(ll_items and all(h['costPending'] and h['unitCost'] is None for h in ll_items))
        lot_id = ll_items[0]['lotId']
        self.assertTrue(stock.lot_history(self.c, lot_id)[0]['costPending'])
        # 在庫一覧の原価未確定数に入る
        self.assertEqual(stock.stock_query(self.c, {})['costPendingQty'], 2)
        self.assertEqual(stock.stock_lot_page(self.c, {}, 200)['costPendingQty'], 2)

    def test_zero_lots_hidden(self):
        h = self.fx['h1']
        stock.register_inbound(self.c, {'token': 'qa-token-' + 'i1', 'customerProductId': h, 'size': 'M', 'qty': 2,
                                        'unitCost': 900, 'locationId': self.fx['loc']['id'],
                                        'condition': 'new', 'receivedOn': TODAY, **W})
        stock.register_outbound(self.c, {'token': 'qa-token-' + 'o1', 'customerProductId': h, 'size': 'M', 'qty': 2,
                                         'locationId': self.fx['loc']['id'], 'condition': 'new',
                                         'businessOn': TODAY, **W})
        self.assertEqual(stock.stock_query(self.c, {})['lots'], [])
        self.assertEqual(stock.stock_lot_page(self.c, {}, 200)['lots'], [])

    def _short_out(self, token, qty=3):
        h = self.fx['h1']
        stock.register_inbound(self.c, {'token': 'qa-token-' + token + 'i', 'customerProductId': h, 'size': 'M', 'qty': 1,
                                        'unitCost': 900, 'locationId': self.fx['loc']['id'],
                                        'condition': 'new', 'receivedOn': TODAY, **W})
        return stock.register_outbound(self.c, {'token': 'qa-token-' + token, 'customerProductId': h, 'size': 'M', 'qty': qty,
                                                'locationId': self.fx['loc']['id'], 'condition': 'new',
                                                'businessOn': TODAY, 'confirmShortage': True, **W})

    def _period_m(self):
        p = stock_period.summarize_period(self.c, {'from': TODAY, 'to': TODAY})
        return next(r for r in p['rows'] if r['size'] == 'M')

    def test_shortage_uses_special_price(self):
        self.c.execute('''INSERT INTO price_special(customer_id,supplier_id,product_id,size_label,price,note,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,?)''',
                       (self.fx['customer']['id'], self.fx['supplier']['id'], self.fx['p1']['id'], 'M', 800, '', 't', 't'))
        out = self._short_out('s1')
        short = next(l for l in out['lines'] if l['kind'] == 'outbound_shortage')
        self.assertEqual((short['unitCost'], short['amount'], short['costPending']), (800, 1600, False))
        # 後から単価を変えても過去の出庫金額は変わらない
        self.c.execute('UPDATE price_special SET price=1 ')
        row = self._period_m()
        self.assertEqual(row['outboundAmount'], 900 + 2 * 800)
        self.assertEqual(row['costPendingOutboundQty'], 0)

    def test_shortage_uses_base_price(self):
        self._short_out('s2')
        self.assertEqual(self._period_m()['outboundAmount'], 900 + 2 * 1000)

    def test_shortage_without_price_is_pending(self):
        self.c.execute('DELETE FROM price_base')
        self._short_out('s3')
        row = self._period_m()
        self.assertEqual(row['outboundAmount'], 900)
        self.assertEqual(row['costPendingOutboundQty'], 2)


if __name__ == '__main__':
    unittest.main()
