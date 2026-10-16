"""API tests for idempotent 取扱 ensure (/api/customer-products)."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = str(Path(folder.name) / 'stock.db')
    c = stock.connect(path)
    stock.initialize(c)
    c.commit()
    return folder, path, c


def seed_masters(c):
    cu = stock.save_customer(c, {'name': '取扱顧客', 'active': True})
    su = stock.save_supplier(c, {'name': '取扱仕入先', 'active': True})
    pr = stock.save_product(c, {
        'code': 'H-1', 'name': '取扱商品', 'color': '白', 'supplierId': su['id'],
        'active': True, 'sizes': ['M', 'L'], 'links': [],
    })
    c.commit()
    return cu, su, pr


class EnsureCustomerProductTest(unittest.TestCase):
    def test_create_idempotent_and_no_lot_side_effect(self):
        folder, path, c = newdb()
        try:
            cu, _su, pr = seed_masters(c)
            first = stock.ensure_customer_product(c, {
                'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '左胸',
            })
            c.commit()
            self.assertFalse(first['already'])
            self.assertEqual(first['customerId'], cu['id'])
            self.assertEqual(first['productId'], pr['id'])
            self.assertEqual(first['embroidery'], '左胸')
            self.assertTrue(first['active'])
            self.assertTrue(first['isStandard'])  # first link for customer+product
            second = stock.ensure_customer_product(c, {
                'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '左胸',
            })
            c.commit()
            self.assertTrue(second['already'])
            self.assertEqual(first['id'], second['id'])
            n = c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n']
            self.assertEqual(n, 1)
            lots = c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n']
            self.assertEqual(lots, 0)
            # empty embroidery = 加工なし; second style does not rewrite first standard
            other = stock.ensure_customer_product(c, {
                'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '',
            })
            c.commit()
            self.assertFalse(other['already'])
            self.assertFalse(other['isStandard'])
            std = c.execute(
                'SELECT is_standard FROM customer_products WHERE id=?', (first['id'],)
            ).fetchone()['is_standard']
            self.assertEqual(std, 1)
        finally:
            c.close()
            folder.cleanup()

    def test_inactive_not_reactivated(self):
        folder, path, c = newdb()
        try:
            cu, _su, pr = seed_masters(c)
            created = stock.ensure_customer_product(c, {
                'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '',
            })
            c.execute('UPDATE customer_products SET active=0 WHERE id=?', (created['id'],))
            c.commit()
            with self.assertRaises(ValueError) as ctx:
                stock.ensure_customer_product(c, {
                    'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '',
                })
            self.assertIn('使用停止', str(ctx.exception))
            self.assertIn('自動では再開しません', str(ctx.exception))
            active = c.execute(
                'SELECT active FROM customer_products WHERE id=?', (created['id'],)
            ).fetchone()['active']
            self.assertEqual(active, 0)
        finally:
            c.close()
            folder.cleanup()

    def test_missing_customer_or_product_not_auto_created(self):
        folder, path, c = newdb()
        try:
            cu, _su, pr = seed_masters(c)
            with self.assertRaises(ValueError) as ctx:
                stock.ensure_customer_product(c, {
                    'customerId': 99999, 'productId': pr['id'], 'embroidery': '',
                })
            self.assertIn('顧客が未登録', str(ctx.exception))
            with self.assertRaises(ValueError) as ctx2:
                stock.ensure_customer_product(c, {
                    'customerId': cu['id'], 'productId': 99999, 'embroidery': '',
                })
            self.assertIn('商品が未登録', str(ctx2.exception))
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM customers').fetchone()['n'], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 1)
        finally:
            c.close()
            folder.cleanup()

    def test_http_idempotent_post(self):
        folder, path, c = newdb()
        cu, _su, pr = seed_masters(c)
        c.close()
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(path, 'live'))
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        def post(body):
            req = urllib.request.Request(
                f'http://127.0.0.1:{port}/api/customer-products',
                data=json.dumps(body).encode(),
                method='POST',
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
            body = {'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '袖口'}
            st1, first = post(body)
            st2, second = post(body)
            self.assertEqual(st1, 200)
            self.assertEqual(st2, 200)
            self.assertFalse(first['already'])
            self.assertTrue(second['already'])
            self.assertEqual(first['id'], second['id'])
            with stock.connect(path) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], 0)
                self.assertEqual(
                    conn.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'], 1
                )
        finally:
            httpd.shutdown()
            httpd.server_close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
