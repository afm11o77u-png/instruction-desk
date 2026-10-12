"""§7 scenarios for stock-desk 発注 (temp DB / fixtures only)."""
from __future__ import annotations

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
import stock_orders
import stock_period


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def seed(c):
    cu = stock.save_customer(c, {'name': '発注テスト病院', 'address': '札幌市', 'active': True})
    su = stock.save_supplier(c, {
        'name': '発注テスト仕入先', 'address': '石狩', 'phone': '011-000-0000',
        'fax': '011-000-0001', 'contact': '山田', 'active': True,
    })
    su2 = stock.save_supplier(c, {'name': '別仕入先', 'active': True})
    pr1 = stock.save_product(c, {
        'code': 'PO-A', 'name': 'スクラブA', 'color': '白',
        'supplierId': su['id'], 'active': True, 'sizes': ['S', 'M', 'L', 'LL'],
        'links': [{'customerId': cu['id'], 'embroidery': '病院名', 'isStandard': True, 'active': True}],
    })
    pr2 = stock.save_product(c, {
        'code': 'PO-B', 'name': 'スクラブB', 'color': '紺',
        'supplierId': su['id'], 'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    # Usual supplier is su2 — must NOT appear for PO supplier su
    pr_other = stock.save_product(c, {
        'code': 'PO-X', 'name': '別仕入商品', 'color': '灰',
        'supplierId': su2['id'], 'active': True, 'sizes': ['M'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '発注テスト倉庫', 'active': True})
    stock_orders.save_company_profile(c, {
        'companyName': 'テスト商事', 'companyBranch': '石狩支店',
        'companyAddress': '北海道石狩市1-1', 'companyTel': '0123-45-6789',
        'companyFax': '0123-45-6790', '_canWrite': True,
    })
    return {
        'customer': cu, 'supplier': su, 'supplier2': su2,
        'p1': pr1, 'p2': pr2, 'p_other': pr_other, 'loc': loc,
        'h1': pr1['links'][0]['id'], 'h2': pr2['links'][0]['id'],
        'h_other': pr_other['links'][0]['id'],
    }


def po_body(fx, **extra):
    # Header fields like orderDate / ordererName / supplierContact / supplierFax are
    # ignored for create (handwritten on paper). Actor becomes internal 作成利用者.
    body = {
        'token': extra.pop('token', 'po-create-token-01'),
        'supplierId': fx['supplier']['id'],
        'customerId': fx['customer']['id'],
        'remarks': '全体備考',
        'lines': [
            {
                'customerProductId': fx['h1'],
                'sizes': {'S': 6, 'M': 3, 'L': 3, 'LL': 6, '2L': 0},
                'note': '左胸プリント依頼',
            },
            {
                'customerProductId': fx['h2'],
                'sizes': {'M': 10, 'L': 5},
                'note': '',
            },
        ],
        '_canWrite': True,
        '_actor': {'id': 1, 'name': '担当太郎'},
    }
    body.update(extra)
    return body


class PurchaseOrdersTest(unittest.TestCase):
    def test_multi_product_size_matrix_and_candidates(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            cands = stock_orders.product_candidates(c, {
                'customerId': fx['customer']['id'], 'supplierId': fx['supplier']['id'],
            })
            ids = {p['customerProductId'] for p in cands['products']}
            self.assertIn(fx['h1'], ids)
            self.assertIn(fx['h2'], ids)
            self.assertNotIn(fx['h_other'], ids)

            po = stock_orders.save_purchase_order(c, po_body(fx))
            c.commit()
            self.assertEqual(po['status'], 'unordered')
            self.assertEqual(po['statusLabel'], '未発注')
            from datetime import date
            self.assertTrue(po['number'].startswith(f'PO-{date.today().isoformat().replace("-", "")}-'))
            self.assertTrue(po['createdAt'])
            self.assertEqual(po['createdBy'], '担当太郎')
            self.assertEqual(po['orderDate'], '')  # handwritten blank, not system-filled
            self.assertEqual(po['supplierContact'], '')
            self.assertEqual(po['supplierFax'], '')
            # blank/0 excluded; S/M/L/LL from p1 + M/L from p2 = 6 lines
            self.assertEqual(len(po['lines']), 6)
            self.assertEqual(po['totalOrdered'], 6 + 3 + 3 + 6 + 10 + 5)
            # embroidery kept internally, not forced into line note
            emb_line = next(l for l in po['lines'] if l['customerProductId'] == fx['h1'])
            self.assertEqual(emb_line['embroidery'], '病院名')
            self.assertEqual(emb_line['lineNote'], '左胸プリント依頼')
        finally:
            c.close()
            folder.cleanup()

    def test_supplier_change_does_not_rewrite_product_usual_supplier(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            before = c.execute('SELECT supplier_id FROM products WHERE id=?', (fx['p1']['id'],)).fetchone()['supplier_id']
            stock_orders.save_purchase_order(c, po_body(fx, token='po-sup-token-01'))
            c.commit()
            after = c.execute('SELECT supplier_id FROM products WHERE id=?', (fx['p1']['id'],)).fetchone()['supplier_id']
            self.assertEqual(before, after)
            self.assertEqual(after, fx['supplier']['id'])
            with self.assertRaises(ValueError):
                stock_orders.save_purchase_order(c, po_body(
                    fx, token='po-wrong-sup',
                    supplierId=fx['supplier2']['id'],
                ))
        finally:
            c.close()
            folder.cleanup()

    def test_presave_preview_and_pdf_html_no_amounts(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            # Client may still send legacy header fields — print must ignore them.
            html = stock_orders.preview_purchase_order_html(c, po_body(
                fx, token='po-prev-token',
                orderDate='2026-10-09', ordererName='担当太郎',
                supplierContact='山田', supplierFax='011-000-0001',
            ))
            self.assertIn('発注書', html)
            self.assertIn('テスト商事', html)
            self.assertIn('発注テスト病院様分', html)
            self.assertIn('発注テスト仕入先', html)
            self.assertIn('左胸プリント依頼', html)
            self.assertIn('サイズ', html)
            self.assertNotIn('単価', html)
            self.assertNotIn('金額', html)
            # Handwritten blanks only — do not auto-fill 担当/FAX/発注者/発注日
            self.assertIn('po-blank', html)
            self.assertNotIn('担当太郎', html)
            self.assertNotIn('山田', html)
            self.assertNotIn('011-000-0001', html)
            self.assertNotIn('2026-10-09', html)
            # company from settings store (always print same five fields)
            self.assertIn('北海道石狩市1-1', html)
            self.assertIn('支店', html)
            self.assertIn('石狩支店', html)
            self.assertIn('TEL', html)
            self.assertIn('0123-45-6789', html)
            po = stock_orders.save_purchase_order(c, po_body(fx, token='po-save-for-html'))
            c.commit()
            saved_html = stock_orders.purchase_order_html(c, po['id'])
            self.assertIn(po['number'], saved_html)
            self.assertIn('発注番号', saved_html)
            self.assertNotIn('担当太郎', saved_html)
            self.assertEqual(po['createdBy'], '担当太郎')
        finally:
            c.close()
            folder.cleanup()

    def test_company_profile_seeds_defaults_when_empty(self):
        folder, path, c = newdb()
        try:
            # Fresh DB has no company keys — first read seeds brand default into settings.
            co = stock_orders.company_profile(c)
            c.commit()
            self.assertEqual(co['companyName'], 'エンパイアー')
            again = stock_orders.company_profile(c, ensure=False)
            self.assertEqual(again['companyName'], 'エンパイアー')
            row = c.execute("SELECT value FROM settings WHERE key='company_name'").fetchone()
            self.assertEqual(row['value'], 'エンパイアー')
            html = stock_orders.render_po_html({
                'number': 'PO-TEST', 'orderDate': '', 'supplierName': '仕入',
                'customerName': '顧客', 'ordererName': '', 'remarks': '',
                'lines': [], 'company': again,
            })
            self.assertIn('エンパイアー', html)
            self.assertIn('支店', html)
            self.assertIn('住所', html)
        finally:
            c.close()
            folder.cleanup()

    def test_save_vs_mark_ordered_and_master_rename_snapshot(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            po = stock_orders.save_purchase_order(c, po_body(fx, token='po-status-token'))
            c.commit()
            self.assertEqual(po['status'], 'unordered')
            # preview/save alone did not order
            again = stock_orders.save_purchase_order(c, po_body(fx, token='po-status-token'))
            self.assertTrue(again['already'])
            self.assertEqual(again['status'], 'unordered')

            marked = stock_orders.mark_ordered(c, {'id': po['id'], '_canWrite': True, '_actor': {'name': '担当太郎'}})
            c.commit()
            self.assertEqual(marked['status'], 'ordered')
            self.assertFalse(marked['already'])

            stock.save_customer(c, {
                'id': fx['customer']['id'], 'name': '改名後病院', 'address': '別住所', 'active': True,
            })
            stock.save_product(c, {
                'id': fx['p1']['id'], 'code': 'PO-A', 'name': '改名商品', 'color': '赤',
                'supplierId': fx['supplier']['id'], 'active': True, 'sizes': ['S', 'M', 'L', 'LL'],
                'links': [{'customerId': fx['customer']['id'], 'embroidery': '病院名', 'isStandard': True, 'active': True}],
            })
            c.commit()
            view = stock_orders.get_purchase_order(c, po['id'])
            self.assertEqual(view['customerName'], '発注テスト病院')
            self.assertEqual(view['lines'][0]['productName'], 'スクラブA')
            self.assertEqual(view['lines'][0]['color'], '白')
        finally:
            c.close()
            folder.cleanup()

    def test_partial_split_complete_receive_and_period(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            po = stock_orders.save_purchase_order(c, {
                'token': 'po-recv-token-01',
                'supplierId': fx['supplier']['id'],
                'customerId': fx['customer']['id'],
                'orderDate': '2026-10-01',
                'ordererName': '担当太郎',
                'lines': [{'customerProductId': fx['h2'], 'sizes': {'M': 10}, 'note': ''}],
                '_canWrite': True,
                '_actor': {'name': '担当太郎'},
            })
            stock_orders.mark_ordered(c, {'id': po['id'], '_canWrite': True, '_actor': {'name': '担当太郎'}})
            line_id = po['lines'][0]['id']

            # partial 6 of 10 @ 1000
            r1 = stock_orders.receive_purchase_order(c, {
                'id': po['id'], 'token': 'po-recv-batch-a1',
                'receivedOn': '2026-10-05', 'locationId': fx['loc']['id'],
                'items': [{'lineId': line_id, 'qty': 6, 'unitCost': 1000}],
                '_canWrite': True, '_actor': {'name': '担当太郎'},
            })
            c.commit()
            self.assertEqual(r1['status'], 'partial')
            self.assertEqual(r1['lines'][0]['qtyReceived'], 6)
            self.assertEqual(r1['lines'][0]['qtyRemaining'], 4)

            # split remainder @ different unit cost
            r2 = stock_orders.receive_purchase_order(c, {
                'id': po['id'], 'token': 'po-recv-batch-b2',
                'receivedOn': '2026-10-08', 'locationId': fx['loc']['id'],
                'items': [{'lineId': line_id, 'qty': 4, 'unitCost': 1200}],
                '_canWrite': True, '_actor': {'name': '担当太郎'},
            })
            c.commit()
            self.assertEqual(r2['status'], 'complete')
            self.assertEqual(r2['lines'][0]['qtyRemaining'], 0)
            self.assertEqual(len(r2['receipts']), 2)

            lots = list(c.execute(
                'SELECT unit_cost, qty_remaining FROM lots WHERE customer_product_id=? AND size_label=? ORDER BY id',
                (fx['h2'], 'M'),
            ))
            self.assertEqual([(r['unit_cost'], r['qty_remaining']) for r in lots], [(1000, 6), (1200, 4)])

            # over-receive blocked
            with self.assertRaises(ValueError):
                stock_orders.receive_purchase_order(c, {
                    'id': po['id'], 'token': 'po-recv-over',
                    'receivedOn': '2026-10-09', 'locationId': fx['loc']['id'],
                    'items': [{'lineId': line_id, 'qty': 1, 'unitCost': 1000}],
                    '_canWrite': True,
                })

            period = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            self.assertEqual(period['totals']['inboundQty'], 10)
            self.assertEqual(period['totals']['inboundAmount'], 6 * 1000 + 4 * 1200)
        finally:
            c.close()
            folder.cleanup()

    def test_idempotent_save_and_receive_double_submit(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            a = stock_orders.save_purchase_order(c, po_body(fx, token='po-idem-save-01'))
            b = stock_orders.save_purchase_order(c, po_body(fx, token='po-idem-save-01'))
            self.assertEqual(a['id'], b['id'])
            self.assertTrue(b['already'])
            stock_orders.mark_ordered(c, {'id': a['id'], '_canWrite': True})
            line_id = a['lines'][0]['id']
            r1 = stock_orders.receive_purchase_order(c, {
                'id': a['id'], 'token': 'po-idem-recv-99',
                'receivedOn': '2026-10-09', 'locationId': fx['loc']['id'],
                'items': [{'lineId': line_id, 'qty': 2, 'unitCost': 900}],
                '_canWrite': True,
            })
            r2 = stock_orders.receive_purchase_order(c, {
                'id': a['id'], 'token': 'po-idem-recv-99',
                'receivedOn': '2026-10-09', 'locationId': fx['loc']['id'],
                'items': [{'lineId': line_id, 'qty': 2, 'unitCost': 900}],
                '_canWrite': True,
            })
            c.commit()
            self.assertTrue(r2['already'])
            self.assertEqual(r1['lines'][0]['qtyReceived'], r2['lines'][0]['qtyReceived'])
            qty = c.execute(
                'SELECT COALESCE(SUM(qty_remaining),0) AS q FROM lots WHERE customer_product_id=? AND size_label=?',
                (a['lines'][0]['customerProductId'], a['lines'][0]['size']),
            ).fetchone()['q']
            self.assertEqual(qty, 2)
        finally:
            c.close()
            folder.cleanup()

    def test_concurrent_receive_consistent(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            po = stock_orders.save_purchase_order(c, {
                'token': 'po-conc-token',
                'supplierId': fx['supplier']['id'],
                'customerId': fx['customer']['id'],
                'orderDate': '2026-10-01',
                'ordererName': '担当太郎',
                'lines': [{'customerProductId': fx['h2'], 'sizes': {'M': 5}, 'note': ''}],
                '_canWrite': True,
            })
            stock_orders.mark_ordered(c, {'id': po['id'], '_canWrite': True})
            c.commit()
            line_id = po['lines'][0]['id']
            errors = []
            ok = []

            def worker(token, qty):
                try:
                    with stock.connect(path) as c2:
                        c2.execute('BEGIN IMMEDIATE')
                        stock_orders.receive_purchase_order(c2, {
                            'id': po['id'], 'token': token,
                            'receivedOn': '2026-10-09', 'locationId': fx['loc']['id'],
                            'items': [{'lineId': line_id, 'qty': qty, 'unitCost': 800}],
                            '_canWrite': True,
                        })
                        c2.commit()
                        ok.append(token)
                except Exception as e:
                    errors.append(str(e))

            t1 = threading.Thread(target=worker, args=('po-conc-a-token', 3))
            t2 = threading.Thread(target=worker, args=('po-conc-b-token', 3))
            t1.start(); t2.start(); t1.join(); t2.join()
            with stock.connect(path) as c3:
                view = stock_orders.get_purchase_order(c3, po['id'])
                received = view['lines'][0]['qtyReceived']
                stock_qty = c3.execute(
                    'SELECT COALESCE(SUM(qty_remaining),0) AS q FROM lots WHERE customer_product_id=? AND size_label=?',
                    (fx['h2'], 'M'),
                ).fetchone()['q']
            self.assertEqual(received, stock_qty)
            self.assertLessEqual(received, 5)
            self.assertGreaterEqual(received, 3)
            # at least one of the overlapping receives must fail or together stay <= 5
            self.assertTrue(len(ok) == 1 or received == 5)
        finally:
            c.close()
            folder.cleanup()

    def test_cancel_keeps_received_stock(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            po = stock_orders.save_purchase_order(c, {
                'token': 'po-cancel-token',
                'supplierId': fx['supplier']['id'],
                'customerId': fx['customer']['id'],
                'orderDate': '2026-10-01',
                'ordererName': '担当太郎',
                'lines': [{'customerProductId': fx['h2'], 'sizes': {'M': 4}, 'note': ''}],
                '_canWrite': True,
            })
            stock_orders.mark_ordered(c, {'id': po['id'], '_canWrite': True})
            stock_orders.receive_purchase_order(c, {
                'id': po['id'], 'token': 'po-cancel-recv',
                'receivedOn': '2026-10-09', 'locationId': fx['loc']['id'],
                'items': [{'lineId': po['lines'][0]['id'], 'qty': 2, 'unitCost': 700}],
                '_canWrite': True,
            })
            cancelled = stock_orders.cancel_purchase_order(c, {'id': po['id'], '_canWrite': True})
            c.commit()
            self.assertEqual(cancelled['status'], 'cancelled')
            qty = c.execute(
                'SELECT COALESCE(SUM(qty_remaining),0) AS q FROM lots WHERE customer_product_id=? AND size_label=?',
                (fx['h2'], 'M'),
            ).fetchone()['q']
            self.assertEqual(qty, 2)
            self.assertTrue(any(e['kind'] == 'cancelled' for e in cancelled['events']))
        finally:
            c.close()
            folder.cleanup()

    def test_edit_after_ordered_sets_needs_resend(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            po = stock_orders.save_purchase_order(c, po_body(fx, token='po-edit-token'))
            stock_orders.mark_ordered(c, {'id': po['id'], '_canWrite': True})
            edited = stock_orders.save_purchase_order(c, {
                **po_body(fx, token='po-edit-token-2'),
                'id': po['id'],
                'remarks': '修正後備考',
            })
            c.commit()
            self.assertTrue(edited['needsResend'])
            self.assertEqual(edited['remarks'], '修正後備考')
            self.assertTrue(any(e['kind'] == 'updated' for e in edited['events']))
        finally:
            c.close()
            folder.cleanup()

    def test_readonly_denied(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            with self.assertRaises(ValueError):
                stock_orders.save_purchase_order(c, {**po_body(fx), '_canWrite': False})
        finally:
            c.close()
            folder.cleanup()

    def test_http_idempotency_and_preview(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            c.commit()
            c.close()

            st, denied = server.api_post(
                str(path), 'live', '/api/purchase-orders',
                po_body(fx, token='http-po-ro'), can_write=False, actor={'name': 'HTTP'},
            )
            self.assertEqual(st, 403)

            body = {
                'token': 'http-po-token-01',
                'supplierId': fx['supplier']['id'],
                'customerId': fx['customer']['id'],
                'orderDate': '2026-10-09',
                'ordererName': 'HTTP',
                'lines': [{'customerProductId': fx['h2'], 'sizes': {'M': 3}, 'note': 'x'}],
            }
            st1, first = server.api_post(str(path), 'live', '/api/purchase-orders', body, can_write=True, actor={'id': 1, 'name': 'HTTP'})
            st2, second = server.api_post(str(path), 'live', '/api/purchase-orders', body, can_write=True, actor={'id': 1, 'name': 'HTTP'})
            self.assertEqual(st1, 200)
            self.assertEqual(st2, 200)
            self.assertEqual(first['id'], second['id'])
            self.assertTrue(second.get('already'))

            preview = server.api_post(
                str(path), 'live', '/api/purchase-orders/preview', body,
                can_write=True, actor={'name': 'HTTP'},
            )
            self.assertEqual(preview[0], 200)
            self.assertEqual(len(preview), 3)
            self.assertIn('発注書'.encode('utf-8'), preview[1])
            self.assertIn('text/html', preview[2])
        finally:
            folder.cleanup()

    def test_migration_creates_po_tables(self):
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            c = stock.connect(path)
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
            c.commit()
            c.close()
            self.assertTrue(stock.needs_migration(stock.connect(path)))
            stock.backup_sqlite(path)
            with stock.connect(path) as c2:
                stock.initialize(c2)
                tables = {r['name'] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn('purchase_orders', tables)
                self.assertIn('purchase_order_lines', tables)
                self.assertIn('purchase_order_receipts', tables)
                cols = {r['name'] for r in c2.execute('PRAGMA table_info(suppliers)')}
                self.assertIn('fax', cols)
            backups = list(Path(folder.name).glob('stock.pre-inout-*.sqlite3'))
            self.assertTrue(backups)
        finally:
            folder.cleanup()

    def test_unordered_cannot_receive(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            po = stock_orders.save_purchase_order(c, {
                'token': 'po-unord-recv',
                'supplierId': fx['supplier']['id'],
                'customerId': fx['customer']['id'],
                'orderDate': '2026-10-01',
                'ordererName': '担当太郎',
                'lines': [{'customerProductId': fx['h2'], 'sizes': {'M': 2}, 'note': ''}],
                '_canWrite': True,
            })
            with self.assertRaises(ValueError):
                stock_orders.receive_purchase_order(c, {
                    'id': po['id'], 'token': 'po-unord-recv-x',
                    'receivedOn': '2026-10-09', 'locationId': fx['loc']['id'],
                    'items': [{'lineId': po['lines'][0]['id'], 'qty': 1, 'unitCost': 500}],
                    '_canWrite': True,
                })
        finally:
            c.close()
            folder.cleanup()


class PurchaseOrderNavUiTest(unittest.TestCase):
    def test_nav_orders_at_bottom(self):
        repo = Path(__file__).resolve().parents[2]
        desk = (repo / 'index.html').read_text(encoding='utf-8')
        aside = desk[desk.index('<aside>'):desk.index('</aside>')]
        stocknav = aside[aside.index('id="stocknav"'):aside.index('<div class="asidebottom"')]
        self.assertIn('data-stock-section="orders"', stocknav)
        self.assertIn('発注', stocknav)
        self.assertLess(stocknav.index('stocknav-period'), stocknav.index('stocknav-orders'))
        self.assertLess(stocknav.index('stocknav-move'), stocknav.index('stocknav-history'))
        self.assertLess(stocknav.index('stocknav-history'), stocknav.index('stocknav-period'))
        app = (repo / 'stock-desk' / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("'orders'", app)
        self.assertIn('purchase_orders.js', (repo / 'stock-desk' / 'static' / 'index.html').read_text(encoding='utf-8'))
        placement = (repo / 'placement.js').read_text(encoding='utf-8')
        self.assertIn("section==='orders'", placement)

    def test_create_ui_only_supplier_customer_and_company_settings(self):
        js = (Path(__file__).resolve().parents[1] / 'static' / 'purchase_orders.js').read_text(encoding='utf-8')
        # Create screen: pickers only — no typed header fields for paper blanks
        self.assertIn("label:'仕入先'", js)
        self.assertIn("label:'対象顧客'", js)
        self.assertNotIn('id="poSuContact"', js)
        self.assertNotIn('id="poSuFax"', js)
        self.assertNotIn('id="poDate"', js)
        self.assertNotIn('id="poOrderer"', js)
        self.assertIn('作成日時', js)
        self.assertIn('作成者', js)
        self.assertIn('openCompanySettings', js)
        self.assertIn('自社情報', js)
        self.assertIn("api('/api/company-profile'", js)


if __name__ == '__main__':
    unittest.main()
