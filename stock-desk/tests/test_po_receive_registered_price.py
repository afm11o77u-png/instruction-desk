"""納品確認: 商品登録の単価（特値→基準単価）を初期値に出し、送られた単価をロットに記録する。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock
import stock_orders
import stock_prices
from tests.test_purchase_orders import newdb, seed

ROOT = Path(__file__).resolve().parents[1]
A = {'_canWrite': True, '_actor': {'name': '担当太郎'}}


def ordered_po(c, fx, token):
    po = stock_orders.save_purchase_order(c, {
        'token': token, 'supplierId': fx['supplier']['id'], 'customerId': fx['customer']['id'],
        'lines': [{'customerProductId': fx['h2'], 'sizes': {'M': 5, 'L': 3}, 'note': ''}], **A,
    })
    stock_orders.mark_ordered(c, {'id': po['id'], **A})
    return stock_orders._po_dict(c, po['id'])


def receive(c, fx, po, token, cost=None):
    items = [{'lineId': l['id'], 'qty': l['qtyRemaining'], **({'unitCost': cost} if cost is not None else {})}
             for l in po['lines']]
    return stock_orders.receive_purchase_order(c, {
        'id': po['id'], 'token': token, 'locationId': fx['loc']['id'], 'items': items, **A})


def lot_cost(c, lot_id):
    return c.execute('SELECT unit_cost FROM lots WHERE id=?', (lot_id,)).fetchone()[0]


class RegisteredPriceReceiveTest(unittest.TestCase):
    def setUp(self):
        self.folder, _p, self.c = newdb()
        c = self.c
        c.executescript(stock_prices.SCHEMA)
        self.fx = seed(c)
        sid, pid = self.fx['supplier']['id'], self.fx['p2']['id']
        t = stock.stamp()
        c.execute("INSERT INTO price_base(supplier_id,product_id,size_label,price,status,updated_at) VALUES(?,?,'M',1200,'ok',?) ON CONFLICT(supplier_id,product_id,size_label) DO UPDATE SET price=excluded.price", (sid, pid, t))
        c.execute("INSERT INTO price_base(supplier_id,product_id,size_label,price,status,updated_at) VALUES(?,?,'L',1300,'ok',?) ON CONFLICT(supplier_id,product_id,size_label) DO UPDATE SET price=excluded.price", (sid, pid, t))

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_registered_price_recorded_and_sent_cost_ignored(self):
        po = ordered_po(self.c, self.fx, 'po-rp-01')
        self.assertEqual([l['registeredPrice'] for l in po['lines']], [1200, 1300])
        r = receive(self.c, self.fx, po, 'po-rp-r1', cost=999)
        lots = [x['lotId'] for x in r['receiveResults']]
        self.assertEqual([lot_cost(self.c, x) for x in lots], [1200, 1300])
        self.assertEqual([x['unitCost'] for x in r['receiveResults']], [1200, 1300])
        moves = [m[0] for m in self.c.execute(
            "SELECT unit_cost FROM moves WHERE lot_id IN (?,?) ORDER BY lot_id", lots)]
        self.assertEqual(moves, [1200, 1300])
        recs = [m[0] for m in self.c.execute(
            'SELECT unit_cost FROM purchase_order_receipts WHERE purchase_order_id=? ORDER BY id', (po['id'],))]
        self.assertEqual(recs, [1200, 1300])

    def test_special_price_wins(self):
        sid, pid = self.fx['supplier']['id'], self.fx['p2']['id']
        t = stock.stamp()
        self.c.execute("INSERT INTO price_special(customer_id,supplier_id,product_id,size_label,price,created_at,updated_at) VALUES(?,?,?,NULL,800,?,?)",
                       (self.fx['customer']['id'], sid, pid, t, t))
        po = ordered_po(self.c, self.fx, 'po-rp-02')
        r = receive(self.c, self.fx, po, 'po-rp-r2', cost=5000)
        self.assertEqual([lot_cost(self.c, x['lotId']) for x in r['receiveResults']], [800, 800])

    def test_unset_price_refused_even_with_cost(self):
        self.c.execute("DELETE FROM price_base WHERE size_label='L' AND product_id=?", (self.fx['p2']['id'],))
        po = ordered_po(self.c, self.fx, 'po-rp-03')
        self.assertEqual([l['registeredPrice'] for l in po['lines']], [1200, None])
        with self.assertRaisesRegex(ValueError, '単価が未設定の商品があります'):
            receive(self.c, self.fx, po, 'po-rp-r3', cost=1500)

    def test_ui_wording(self):
        js = (ROOT / 'static' / 'purchase_orders.js').read_text(encoding='utf-8')
        self.assertNotIn('入荷を登録', js)
        self.assertIn('>納品確認<', js)
        self.assertIn('>納品確定<', js)
        self.assertIn('registeredPrice', js)
        self.assertNotIn('登録単価と違う単価', js)
        self.assertNotIn('data-cost', js)
        self.assertIn('単価が未設定の商品があります。', js)
        self.assertIn('商品登録を開く', js)
        self.assertIn("setStockSection('setup',{setupTab:'products'})", js)
        self.assertIn('id="poOpenReg"', js)


if __name__ == '__main__':
    unittest.main()
