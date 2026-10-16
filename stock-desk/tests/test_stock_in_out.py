"""§8 scenarios for daily 入庫・出庫 (temp DB / fixtures only)."""
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
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
        'code': 'SCR-IN', 'name': '入出庫スクラブ', 'color': '白',
        'supplierId': su['id'], 'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '石狩倉庫テスト', 'active': True})
    handle = pr['links'][0]['id']
    return cu, pr, loc, handle


class StockInOutTest(unittest.TestCase):
    def test_same_price_inbound_creates_separate_lots(self):
        folder, path, c = newdb()
        try:
            cu, pr, loc, handle = seed_masters(c)
            a = stock.register_inbound(c, {
                'token': 'in-same-1-token', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 1800, 'receivedOn': '2026-10-01',
            })
            b = stock.register_inbound(c, {
                'token': 'in-same-2-token', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 1800, 'receivedOn': '2026-10-02',
            })
            c.commit()
            lots = list(c.execute(
                'SELECT id,qty_received,qty_remaining,unit_cost FROM lots WHERE customer_product_id=? AND size_label=? ORDER BY id',
                (handle, 'M'),
            ))
            self.assertEqual(len(lots), 2)
            self.assertNotEqual(a['lotId'], b['lotId'])
            self.assertEqual([r['unit_cost'] for r in lots], [1800, 1800])
            self.assertEqual(sum(r['qty_remaining'] for r in lots), 8)
            targets = stock.move_targets(c, {})
            self.assertEqual(len(targets['targets']), 1)
            self.assertEqual(targets['targets'][0]['qty'], 8)
            self.assertEqual(targets['targets'][0]['amount'], 8 * 1800)
        finally:
            c.close()
            folder.cleanup()

    def test_new_price_inbound_keeps_old_costs(self):
        folder, path, c = newdb()
        try:
            cu, pr, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'in-old-price', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 10, 'unitCost': 1000, 'receivedOn': '2026-09-01',
            })
            stock.register_inbound(c, {
                'token': 'in-new-price', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 1200, 'receivedOn': '2026-10-01',
            })
            c.commit()
            view = stock.stock_query(c, {'group': 'product'})
            row = view['groups'][0]['rows'][0]
            self.assertEqual(row['qty'], 14)
            self.assertEqual(row['amount'], 10 * 1000 + 4 * 1200)
            costs = sorted(p['unitCost'] for p in row['prices'])
            self.assertEqual(costs, [1000, 1200])
            # product master update must not rewrite past lot costs
            stock.save_product(c, {
                'id': pr['id'], 'code': pr['code'], 'name': '名前変更', 'color': pr['color'],
                'supplierId': pr['supplierId'], 'active': True, 'sizes': pr['sizes'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            again = stock.stock_query(c, {'group': 'lot'})
            self.assertEqual(sorted(l['unitCost'] for l in again['lots']), [1000, 1200])
        finally:
            c.close()
            folder.cleanup()

    def test_fifo_oldest_date_not_cheapest(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'fifo-lot-1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 9000, 'receivedOn': '2026-08-01',
            })
            stock.register_inbound(c, {
                'token': 'fifo-lot-2', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 100, 'receivedOn': '2026-09-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 6,
            })
            self.assertTrue(plan['ok'])
            self.assertEqual([a['unitCost'] for a in plan['allocations']], [9000, 100])
            self.assertEqual([a['qty'] for a in plan['allocations']], [5, 1])
            out = stock.register_outbound(c, {
                'token': 'out-fifo-1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 6, 'fingerprint': plan['fingerprint'],
            })
            c.commit()
            self.assertEqual(out['totalAmount'], 5 * 9000 + 100)
            remain = stock.stock_query(c, {})
            self.assertEqual(remain['totalQty'], 4)
            self.assertEqual(remain['totalAmount'], 4 * 100)
        finally:
            c.close()
            folder.cleanup()

    def test_multi_lot_outbound_and_unknown_date_payout_order(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_opening(c, {
                'token': 'open-unknown-2', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-07-01', 'receivedUnknown': True, 'payoutOrder': 2,
                'lines': [{'size': 'M', 'qty': 3, 'unitCost': 500}],
            })
            stock.register_opening(c, {
                'token': 'open-unknown-1', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-07-01', 'receivedUnknown': True, 'payoutOrder': 1,
                'lines': [{'size': 'M', 'qty': 2, 'unitCost': 800}],
            })
            stock.register_inbound(c, {
                'token': 'dated-inbound', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 4, 'unitCost': 1000, 'receivedOn': '2026-08-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 6,
            })
            # unknown payout 1, then payout 2, then dated
            self.assertEqual(
                [(a['receivedUnknown'], a['payoutOrder'] if a['receivedUnknown'] else a['receivedOn'], a['qty']) for a in plan['allocations']],
                [(True, 1, 2), (True, 2, 3), (False, '2026-08-01', 1)],
            )
            stock.register_outbound(c, {
                'token': 'out-multi-1', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 6, 'fingerprint': plan['fingerprint'],
            })
            c.commit()
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), 3)
        finally:
            c.close()
            folder.cleanup()

    def test_shortage_blocks_and_idempotent_double_submit(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'short-inbound', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 5,
            })
            self.assertFalse(plan['ok'])
            self.assertEqual(plan['shortfall'], 3)
            with self.assertRaises(ValueError):
                stock.register_outbound(c, {
                    'token': 'short-outbound', 'customerProductId': handle, 'size': 'M',
                    'locationId': loc['id'], 'qty': 5, 'fingerprint': plan['fingerprint'],
                })
            ok_plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 2,
            })
            first = stock.register_outbound(c, {
                'token': 'once-outbound', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'fingerprint': ok_plan['fingerprint'],
            })
            second = stock.register_outbound(c, {
                'token': 'once-outbound', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'fingerprint': ok_plan['fingerprint'],
            })
            c.commit()
            self.assertFalse(first['already'])
            self.assertTrue(second['already'])
            self.assertEqual(stock.current_qty_for_target(c, handle, 'M', loc['id']), 0)
            in1 = stock.register_inbound(c, {
                'token': 'inbound-once', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 700, 'receivedOn': '2026-10-03',
            })
            in2 = stock.register_inbound(c, {
                'token': 'inbound-once', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 700, 'receivedOn': '2026-10-03',
            })
            self.assertFalse(in1['already'])
            self.assertTrue(in2['already'])
            self.assertEqual(stock.current_qty_for_target(c, handle, 'L', loc['id']), 1)
        finally:
            c.close()
            folder.cleanup()

    def test_fingerprint_conflict_requires_reconfirm(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'fingerprint-a', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 5, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 2,
            })
            stock.register_inbound(c, {
                'token': 'fingerprint-b', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 1, 'unitCost': 1100, 'receivedOn': '2026-10-02',
            })
            with self.assertRaises(stock.StockConflict) as ctx:
                stock.register_outbound(c, {
                    'token': 'fingerprint-out', 'customerProductId': handle, 'size': 'M',
                    'locationId': loc['id'], 'qty': 2, 'fingerprint': plan['fingerprint'],
                })
            self.assertTrue(ctx.exception.payload.get('needsReconfirm'))
            fresh = ctx.exception.payload['plan']
            ok = stock.register_outbound(c, {
                'token': 'fingerprint-out2', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 2, 'fingerprint': fresh['fingerprint'],
                'confirmChanged': True,
            })
            self.assertEqual(ok['totalQty'], 2)
        finally:
            c.close()
            folder.cleanup()

    def test_concurrent_outbound_no_negative(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'concurrent-in', 'customerProductId': handle, 'size': 'M',
                'locationId': loc['id'], 'qty': 3, 'unitCost': 1000, 'receivedOn': '2026-10-01',
            })
            c.commit()
            c.close()

            errors = []
            results = []

            def worker(token):
                conn = stock.connect(path)
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    plan = stock.plan_outbound(conn, {
                        'customerProductId': handle, 'size': 'M', 'locationId': loc['id'], 'qty': 3,
                    })
                    out = stock.register_outbound(conn, {
                        'token': token, 'customerProductId': handle, 'size': 'M',
                        'locationId': loc['id'], 'qty': 3, 'fingerprint': plan['fingerprint'],
                    })
                    conn.commit()
                    results.append(out)
                except Exception as e:
                    conn.rollback()
                    errors.append(str(e))
                finally:
                    conn.close()

            t1 = threading.Thread(target=worker, args=('concurrent-a',))
            t2 = threading.Thread(target=worker, args=('concurrent-b',))
            t1.start(); t2.start(); t1.join(); t2.join()
            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)
            c2 = stock.connect(path)
            qty = stock.current_qty_for_target(c2, handle, 'M', loc['id'])
            self.assertEqual(qty, 0)
            neg = c2.execute('SELECT COUNT(*) AS n FROM lots WHERE qty_remaining<0').fetchone()['n']
            self.assertEqual(neg, 0)
            c2.close()
        finally:
            folder.cleanup()

    def test_readonly_blocks_and_opening_still_works(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            with self.assertRaises(ValueError):
                stock.register_inbound(c, {
                    'token': 'readonly-in', 'customerProductId': handle, 'size': 'M',
                    'locationId': loc['id'], 'qty': 1, 'unitCost': 1000, 'receivedOn': '2026-10-01',
                    '_canWrite': False,
                })
            opening = stock.register_opening(c, {
                'token': 'opening-ok1', 'customerProductId': handle, 'locationId': loc['id'],
                'basisDate': '2026-09-01', 'receivedUnknown': True, 'payoutOrder': 1,
                'lines': [{'size': 'M', 'qty': 2, 'unitCost': 1000}],
            })
            self.assertEqual(opening['totalQty'], 2)
            entries = stock.opening_entries(c)
            self.assertTrue(any(e['qty'] == 2 for e in entries['entries']))
            # consumed inbound cannot be corrected via inbound_correct; opening correct blocked after outbound
            stock.register_inbound(c, {
                'token': 'correct-in1', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 2, 'unitCost': 900, 'receivedOn': '2026-10-01',
            })
            lot = c.execute("SELECT id FROM lots WHERE size_label='L'").fetchone()['id']
            plan = stock.plan_outbound(c, {
                'customerProductId': handle, 'size': 'L', 'locationId': loc['id'], 'qty': 1,
            })
            stock.register_outbound(c, {
                'token': 'correct-out1', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 1, 'fingerprint': plan['fingerprint'],
            })
            with self.assertRaises(ValueError):
                stock.correct_inbound(c, {'lotId': lot, 'qty': 1, 'unitCost': 900, 'reason': '試し'})
            hist = stock.lot_history(c, lot)
            kinds = [e['kind'] for e in hist]
            self.assertIn('inbound', kinds)
            self.assertIn('outbound', kinds)
        finally:
            c.close()
            folder.cleanup()

    def test_zero_stock_inbound_path_and_list_breakdown(self):
        folder, path, c = newdb()
        try:
            _, pr, loc, handle = seed_masters(c)
            # no prior stock for L
            self.assertEqual(stock.current_qty_for_target(c, handle, 'L', loc['id']), 0)
            stock.register_inbound(c, {
                'token': 'zero-stock-in', 'customerProductId': handle, 'size': 'L',
                'locationId': loc['id'], 'qty': 7, 'unitCost': 1500, 'receivedOn': '2026-10-05',
            })
            c.commit()
            self.assertEqual(stock.current_qty_for_target(c, handle, 'L', loc['id']), 7)
            view = stock.stock_query(c, {'group': 'product'})
            prices = view['groups'][0]['rows'][0]['prices']
            self.assertEqual(prices[0]['amount'], 7 * 1500)
            self.assertTrue(prices[0]['receipts'])
            lot_view = stock.stock_query(c, {'group': 'lot'})
            self.assertEqual(lot_view['lots'][0]['qty'], 7)
        finally:
            c.close()
            folder.cleanup()

    def test_http_inbound_outbound_idempotency_and_readonly(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = Path(folder.name) / 'stock.sqlite3'
        server.prepare(path, 'live')
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(path, 'live'))
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        import urllib.request

        def post(url_path, body, *, can_write=True):
            # exercise api_post permission flag via direct call for readonly;
            # HTTP handler in standalone always can_write=True.
            if not can_write:
                return server.api_post(path, 'live', url_path, body, can_write=False)
            req = urllib.request.Request(
                f'http://127.0.0.1:{port}{url_path}',
                data=json.dumps(body).encode(), method='POST',
            )
            req.add_header('Content-Type', 'application/json')
            req.add_header('X-Stock-Request', '1')
            req.add_header('Origin', 'http://127.0.0.1')
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read().decode())

        try:
            st, customer = post('/api/customers', {'name': 'HTTP顧客', 'active': True})
            self.assertEqual(st, 200)
            st, supplier = post('/api/suppliers', {'name': 'HTTP仕入', 'active': True})
            st, product = post('/api/products', {
                'code': 'H-1', 'name': 'HTTP商品', 'color': '白', 'supplierId': supplier['id'],
                'active': True, 'sizes': ['M'], 'links': [
                    {'customerId': customer['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                ],
            })
            st, place = post('/api/locations', {'siteName': 'HTTP拠点', 'active': True})
            handle = product['links'][0]['id']
            body = {
                'token': 'http-inbound', 'customerProductId': handle, 'size': 'M',
                'locationId': place['id'], 'qty': 4, 'unitCost': 2000, 'receivedOn': '2026-10-01',
            }
            st1, first = post('/api/inbound', body)
            st2, second = post('/api/inbound', body)
            self.assertEqual(st1, 200)
            self.assertFalse(first['already'])
            self.assertTrue(second['already'])
            st, preview = post('/api/outbound/preview', {
                'customerProductId': handle, 'size': 'M', 'locationId': place['id'], 'qty': 2,
            })
            self.assertTrue(preview['ok'])
            out_body = {
                'token': 'http-outbound', 'customerProductId': handle, 'size': 'M',
                'locationId': place['id'], 'qty': 2, 'fingerprint': preview['fingerprint'],
            }
            st, out1 = post('/api/outbound', out_body)
            st, out2 = post('/api/outbound', out_body)
            self.assertFalse(out1['already'])
            self.assertTrue(out2['already'])
            st, denied = post('/api/inbound', dict(body, token='ro-token'), can_write=False)
            self.assertEqual(st, 403)
            # opening register still works
            st, opening = post('/api/opening', {
                'token': 'http-opening', 'customerProductId': handle, 'locationId': place['id'],
                'basisDate': '2026-09-01', 'receivedUnknown': True, 'payoutOrder': 1,
                'lines': [{'size': 'M', 'qty': 1, 'unitCost': 500}],
            })
            self.assertEqual(st, 200)
            self.assertEqual(opening['totalQty'], 1)
        finally:
            httpd.shutdown()
            httpd.server_close()
            folder.cleanup()

    def test_migration_backup_preserves_existing_lots(self):
        folder = tempfile.TemporaryDirectory()
        path = Path(folder.name) / 'stock.sqlite3'
        c = sqlite3.connect(path)
        c.executescript('''
            CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kana TEXT NOT NULL DEFAULT '',
              address TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', contact TEXT NOT NULL DEFAULT '',
              active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE suppliers(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, address TEXT NOT NULL DEFAULT '',
              phone TEXT NOT NULL DEFAULT '', contact TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE products(id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '',
              supplier_id INTEGER, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE product_sizes(id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, label TEXT NOT NULL, sort_order INTEGER NOT NULL, UNIQUE(product_id, label));
            CREATE TABLE customer_products(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
              embroidery TEXT NOT NULL DEFAULT '', is_standard INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(customer_id, product_id, embroidery));
            CREATE TABLE locations(id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER, active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE opening_batches(id INTEGER PRIMARY KEY, client_token TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
            CREATE TABLE lots(id INTEGER PRIMARY KEY, batch_id INTEGER, customer_product_id INTEGER NOT NULL, size_label TEXT NOT NULL,
              location_id INTEGER NOT NULL, unit_cost INTEGER NOT NULL, qty_received INTEGER NOT NULL, qty_remaining INTEGER NOT NULL,
              basis_date TEXT NOT NULL, received_on TEXT, received_unknown INTEGER NOT NULL DEFAULT 0, payout_order INTEGER NOT NULL DEFAULT 0,
              condition TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE moves(id INTEGER PRIMARY KEY, lot_id INTEGER NOT NULL, kind TEXT NOT NULL, qty_delta INTEGER NOT NULL,
              unit_cost INTEGER NOT NULL, qty_before INTEGER, qty_after INTEGER, cost_before INTEGER, cost_after INTEGER,
              location_id INTEGER, related_lot_id INTEGER, loan_id INTEGER, note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
            CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
        ''')
        t = '2026-01-01T00:00:00'
        c.execute("INSERT INTO customers(id,name,created_at,updated_at) VALUES(1,'旧顧客',?,?)", (t, t))
        c.execute("INSERT INTO products(id,code,name,created_at,updated_at) VALUES(1,'OLD','旧商品',?,?)", (t, t))
        c.execute("INSERT INTO product_sizes(product_id,label,sort_order) VALUES(1,'M',0)")
        c.execute("INSERT INTO customer_products(id,customer_id,product_id,created_at,updated_at) VALUES(1,1,1,?,?)", (t, t))
        c.execute("INSERT INTO locations(id,name,created_at,updated_at) VALUES(1,'旧拠点',?,?)", (t, t))
        c.execute("INSERT INTO lots(id,customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,basis_date,received_on,created_at,updated_at) VALUES(1,1,'M',1,1000,5,5,'2026-01-01','2026-01-01',?,?)", (t, t))
        c.execute("INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,note,at) VALUES(1,'opening',5,1000,0,5,'初期在庫',?)", (t,))
        c.commit()
        c.close()
        server.prepare(path, 'live')
        backups = list(Path(folder.name).glob('stock.pre-*.sqlite3'))
        self.assertTrue(backups)
        c2 = stock.connect(path)
        self.assertEqual(c2.execute('SELECT qty_remaining FROM lots WHERE id=1').fetchone()['qty_remaining'], 5)
        cols = {r['name'] for r in c2.execute('PRAGMA table_info(moves)')}
        self.assertIn('actor_name', cols)
        self.assertIn('move_batch_id', cols)
        c2.close()
        folder.cleanup()


if __name__ == '__main__':
    unittest.main()
