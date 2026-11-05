"""月末締めの記録と在庫更新の停止。合成データと一時DBのみ。"""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server as stock_server  # noqa: E402
import stock  # noqa: E402
import stock_close  # noqa: E402
import stock_stocktake as st  # noqa: E402

W = {'_canWrite': True}


def db():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def actor(role, name='担当'):
    return {'id': 1, 'name': name, 'role': role}


def seed(c):
    cu = stock.save_customer(c, {'name': '締め病院', 'active': True})
    su = stock.save_supplier(c, {'name': '仕入', 'active': True})
    pr = stock.save_product(c, {
        'code': 'CL-1', 'name': '白衣', 'color': '白', 'supplierId': su['id'],
        'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '本社', 'shelfName': 'A', 'active': True})
    h = pr['links'][0]['id']
    stock.register_inbound(c, {
        'token': 'cl-in-m1', 'customerProductId': h, 'size': 'M', 'locationId': loc['id'],
        'qty': 3, 'unitCost': 1000, 'receivedOn': '2026-10-01', **W,
    })
    stock.register_inbound(c, {
        'token': 'cl-in-l1', 'customerProductId': h, 'size': 'L', 'locationId': loc['id'],
        'qty': 2, 'unitCost': 1500, 'receivedOn': '2026-10-02', **W,
        '_actor': actor('owner'),
    })
    c.commit()
    return cu, pr, loc, h


def qty(c):
    row = c.execute(
        'SELECT COUNT(*) n, COALESCE(SUM(qty_remaining),0) q, COALESCE(SUM(CASE WHEN COALESCE(cost_pending,0)=0 THEN qty_remaining*unit_cost END),0) a FROM lots'
    ).fetchone()
    return row['n'], row['q'], row['a']


class StockCloseTest(unittest.TestCase):
    def test_snapshot_keeps_every_lot_and_ignores_later_stock(self):
        folder, path, c = db()
        try:
            seed(c)
            before = qty(c)
            rec = stock_close.save_record(c, {'month': '2026-10', '_canWrite': True, '_actor': actor('owner', '主管理者')})
            c.commit()
            self.assertEqual(rec['version'], 1)
            self.assertEqual(rec['lotCount'], before[0])
            self.assertEqual(rec['qtyTotal'], before[1])
            self.assertEqual(rec['amountTotal'], before[2])
            self.assertEqual({line['size'] for line in rec['lines']}, {'M', 'L'})
            stock.register_inbound(c, {
                'token': 'cl-later', 'customerProductId': rec['lines'][0]['customerProductId'],
                'size': 'M', 'locationId': rec['lines'][0]['locationId'],
                'qty': 9, 'unitCost': 1000, 'receivedOn': '2026-10-20', **W,
            })
            c.commit()
            again = stock_close.record_detail(c, rec['id'])
            self.assertEqual(again['qtyTotal'], before[1])
            self.assertEqual(again['amountTotal'], before[2])
            self.assertEqual(len(again['lines']), before[0])
            second = stock_close.save_record(c, {'month': '2026-10', '_canWrite': True, '_actor': actor('owner')})
            c.commit()
            self.assertEqual(second['version'], 2)
            self.assertEqual(stock_close.record_detail(c, rec['id'])['qtyTotal'], before[1])
            self.assertFalse(stock_close.hold_state(c)['held'])
        finally:
            c.close()
            folder.cleanup()

    def test_hold_roles_and_freeze_blocks_updates_not_orders_or_stocktake(self):
        folder, path, c = db()
        try:
            cu, pr, loc, h = seed(c)
            before = qty(c)
            for role in ('sales', 'floor', 'floor2', 'floor3'):
                with self.assertRaises(PermissionError):
                    stock_close.set_hold(c, {'_actor': actor(role), '_canWrite': True}, held=True)
            c.rollback()
            code, denied = stock_server.api_post(
                str(path), 'desk', '/api/stock-close/hold', {},
                can_write=True, actor=actor('sales', '営業管理者'),
            )
            self.assertEqual(code, 403)
            code, floor_denied = stock_server.api_post(
                str(path), 'desk', '/api/stock-close/hold', {},
                can_write=True, actor=actor('floor', '現場'),
            )
            self.assertEqual(code, 403)
            self.assertFalse(stock_close.hold_state(c)['held'])
            code, ok = stock_server.api_post(
                str(path), 'desk', '/api/stock-close/hold', {},
                can_write=False, actor=actor('warehouse', '倉庫担当'),
            )
            self.assertEqual(code, 200)
            self.assertTrue(ok['held'])
            code, owner_again = stock_server.api_post(
                str(path), 'desk', '/api/stock-close/hold', {},
                can_write=True, actor=actor('owner'),
            )
            self.assertEqual(code, 200)
            self.assertTrue(owner_again['already'])

            with self.assertRaises(stock_close.StockUpdateHeld):
                stock.register_inbound(c, {
                    'token': 'cl-blocked', 'customerProductId': h, 'size': 'M',
                    'locationId': loc['id'], 'qty': 1, 'unitCost': 1000,
                    'receivedOn': '2026-10-21', **W, '_actor': actor('owner'),
                })
            c.rollback()
            with self.assertRaises(stock_close.StockUpdateHeld):
                stock.register_outbound(c, {
                    'token': 'cl-out-blocked', 'customerProductId': h, 'size': 'M',
                    'locationId': loc['id'], 'qty': 1, 'businessOn': '2026-10-21',
                    **W, '_actor': actor('floor'),
                })
            c.rollback()
            with self.assertRaises(stock_close.StockUpdateHeld):
                stock.register_opening(c, {
                    'token': 'cl-open-blocked', 'customerProductId': h, 'locationId': loc['id'],
                    'basisDate': '2026-10-21', 'receivedUnknown': True, 'payoutOrder': 1,
                    'lines': [{'size': 'M', 'qty': 1, 'unitCost': 1000}], **W,
                })
            c.rollback()
            with self.assertRaises(stock_close.StockUpdateHeld):
                stock.register_stocktake(c, {
                    'token': 'cl-fake-st', 'customerProductId': h, 'size': 'M',
                    'locationId': loc['id'], 'actualQty': 99, 'businessOn': '2026-10-21',
                    '_stocktake_apply': True, **W,
                })
            c.rollback()
            self.assertEqual(qty(c), before)

            code, saved = stock_server.api_post(
                str(path), 'desk', '/api/purchase-orders', {
                    'token': 'cl-po-token1', 'supplierId': pr['supplierId'], 'customerId': cu['id'],
                    'orderDate': '2026-10-01',
                    'lines': [{'customerProductId': h, 'sizes': {'M': 1}, 'note': ''}],
                }, can_write=True, actor=actor('sales'),
            )
            self.assertEqual(code, 200, saved)
            listed = stock_server.api_get(str(path), 'desk', '/api/stock-close', {})
            self.assertEqual(listed[0], 200)

            st.add_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id'], '_actor': actor('owner')})
            sheet = st.get(c, '2026-10')
            row = next(r for r in sheet['rows'] if r['size'] == 'M')
            st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': row['id'], 'actualQty': row['bookQty'] + 1}]})
            c.commit()
            applied = st.apply(c, {**W, 'month': '2026-10', 'confirm': True, '_actor': actor('owner')})
            c.commit()
            self.assertGreaterEqual(applied['adjusted'], 1)
            self.assertGreater(qty(c)[1], before[1])
            frozen_record = stock_close.save_record(c, {'month': '2026-11', '_canWrite': True, '_actor': actor('owner')})
            c.commit()
            after_apply_qty = qty(c)
            st.apply(c, {**W, 'month': '2026-10', 'confirm': True, '_actor': actor('owner')})
            c.commit()
            self.assertEqual(stock_close.record_detail(c, frozen_record['id'])['qtyTotal'], frozen_record['qtyTotal'])

            code, released = stock_server.api_post(
                str(path), 'desk', '/api/stock-close/release', {},
                can_write=True, actor=actor('owner'),
            )
            self.assertEqual(code, 200)
            self.assertFalse(released['held'])
            stock.register_inbound(c, {
                'token': 'cl-after', 'customerProductId': h, 'size': 'M', 'locationId': loc['id'],
                'qty': 1, 'unitCost': 1000, 'receivedOn': '2026-11-01', **W,
            })
            c.commit()
            self.assertGreater(qty(c)[1], after_apply_qty[1])
            code, no = stock_server.api_post(
                str(path), 'desk', '/api/stock-close/save', {'month': '2026-11'},
                can_write=False, actor=actor('warehouse'),
            )
            self.assertEqual(code, 403)
        finally:
            c.close()
            folder.cleanup()

    def test_hold_waits_for_open_writer(self):
        folder, path, c = db()
        try:
            seed(c)
            c.execute('BEGIN IMMEDIATE')
            started = threading.Event()
            done = {}

            def other():
                c2 = stock.connect(path)
                started.set()
                stock_close.set_hold(c2, {'_actor': actor('owner')}, held=True)
                c2.commit()
                done['held'] = stock_close.hold_state(c2)['held']
                c2.close()

            th = threading.Thread(target=other)
            th.start()
            self.assertTrue(started.wait(2))
            th.join(0.3)
            self.assertTrue(th.is_alive())
            c.commit()
            th.join(2)
            self.assertFalse(th.is_alive())
            self.assertTrue(done['held'])
        finally:
            c.close()
            folder.cleanup()

    def test_stock_gate_and_role_assignment(self):
        sys.path.insert(0, str(ROOT.parent))
        import auth
        self.assertTrue(auth.can_open_stock({'role': 'warehouse', 'manage': 0, 'overview': 0}))
        self.assertFalse(auth.stock_can_write({'role': 'warehouse', 'manage': 0}))
        self.assertTrue(auth.can_open_stock({'role': 'owner', 'manage': 0, 'overview': 0}))
        self.assertFalse(auth.can_open_stock({'role': 'floor', 'manage': 0, 'overview': 0}))
        self.assertTrue(auth.can_open_stock({'role': 'floor', 'manage': 1, 'overview': 0}))
        self.assertTrue(auth.can_open_stock({'role': 'sales', 'manage': 0, 'overview': 1}))
        self.assertFalse(auth.stock_can_write({'role': 'sales', 'manage': 0, 'overview': 1}))
        # 既存利用者を倉庫担当へ書き換えるSQLは置かない
        src = (ROOT.parent / 'auth.py').read_text(encoding='utf-8')
        self.assertNotIn("SET role='warehouse'", src)
        self.assertNotIn('SET role="warehouse"', src)
        self.assertIn("'warehouse'", src)
        account = (ROOT.parent / 'account.js').read_text(encoding='utf-8')
        self.assertIn('value="warehouse">倉庫担当', account)
        self.assertIn("role==='warehouse'", account)

    def test_ui_copy(self):
        js = (ROOT / 'static' / 'stock_close.js').read_text(encoding='utf-8')
        nav = (ROOT.parent / 'index.html').read_text(encoding='utf-8')
        self.assertIn('現物の入出庫も止める必要があります', js)
        self.assertIn('棚卸の反映は続けられます', js)
        self.assertIn('棚卸で自動では直しません', js)
        self.assertIn('data-stock-section="close"', nav)
        self.assertNotIn('先に締めてください', js)
        self.assertNotIn('差分', js)


if __name__ == '__main__':
    unittest.main()
