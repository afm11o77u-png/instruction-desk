"""通常入庫（一覧にない商品）: multi-size lines, kind=inbound, idempotency (temp DB)."""
import json
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
    cu = stock.save_customer(c, {'name': '通常入庫病院', 'active': True})
    su = stock.save_supplier(c, {'name': '通常入庫仕入', 'active': True})
    pr = stock.save_product(c, {
        'code': 'NIN-1', 'name': '通常入庫スクラブ', 'color': '紺',
        'supplierId': su['id'], 'active': True, 'sizes': ['S', 'M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '通常入庫倉庫', 'active': True})
    handle = pr['links'][0]['id']
    return cu, pr, loc, handle


class NormalInboundMultiTest(unittest.TestCase):
    def test_multi_size_lines_one_batch_all_inbound_kind(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            result = stock.register_inbound(c, {
                'token': 'multi-in-1',
                'customerProductId': handle,
                'locationId': loc['id'],
                'condition': 'new',
                'receivedOn': '2026-10-10',
                'note': 'まとめて入庫',
                'lines': [
                    {'size': 'S', 'qty': 2, 'unitCost': 1000},
                    {'size': 'M', 'qty': 3, 'unitCost': 1100},
                    {'size': 'L', 'qty': 1, 'unitCost': 1200},
                ],
            })
            c.commit()
            self.assertFalse(result['already'])
            self.assertEqual(result['kind'], 'inbound')
            self.assertEqual(result['totalQty'], 6)
            self.assertEqual(result['totalAmount'], 2 * 1000 + 3 * 1100 + 1200)
            self.assertEqual(len(result['lines']), 3)
            kinds = [r['kind'] for r in c.execute('SELECT kind FROM moves WHERE move_batch_id=?', (result['batchId'],))]
            self.assertEqual(kinds, ['inbound', 'inbound', 'inbound'])
            self.assertEqual(c.execute("SELECT COUNT(*) AS n FROM moves WHERE kind='opening'").fetchone()['n'], 0)
            # Same unit price still creates separate lots per line; sizes differ here.
            lots = list(c.execute(
                'SELECT size_label, qty_remaining, unit_cost, received_unknown, payout_order, basis_date, received_on '
                'FROM lots ORDER BY size_label'
            ))
            self.assertEqual([(r['size_label'], r['qty_remaining'], r['unit_cost']) for r in lots],
                             [('L', 1, 1200), ('M', 3, 1100), ('S', 2, 1000)])
            for r in lots:
                self.assertEqual(r['received_unknown'], 0)
                self.assertEqual(r['payout_order'], 0)
                self.assertEqual(r['received_on'], '2026-10-10')
                self.assertEqual(r['basis_date'], '2026-10-10')
        finally:
            c.close()
            folder.cleanup()

    def test_multi_size_validation_rejects_before_any_insert(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            with self.assertRaises(ValueError) as ctx:
                stock.register_inbound(c, {
                    'token': 'multi-bad',
                    'customerProductId': handle,
                    'locationId': loc['id'],
                    'receivedOn': '2026-10-10',
                    'lines': [
                        {'size': 'M', 'qty': 2, 'unitCost': 1000},
                        {'size': 'M', 'qty': 1, 'unitCost': 1000},  # duplicate size+cost
                    ],
                })
            self.assertIn('同じサイズ', str(ctx.exception))
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM move_batches').fetchone()['n'], 0)
            with self.assertRaises(ValueError):
                stock.register_inbound(c, {
                    'token': 'multi-nodate',
                    'customerProductId': handle,
                    'locationId': loc['id'],
                    'lines': [{'size': 'M', 'qty': 1, 'unitCost': 1000}],
                })
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_idempotent_token_does_not_double_stock(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            body = {
                'token': 'multi-idem',
                'customerProductId': handle,
                'locationId': loc['id'],
                'receivedOn': '2026-10-11',
                'lines': [
                    {'size': 'M', 'qty': 4, 'unitCost': 1500},
                    {'size': 'L', 'qty': 2, 'unitCost': 1600},
                ],
            }
            a = stock.register_inbound(c, body)
            b = stock.register_inbound(c, body)
            c.commit()
            self.assertFalse(a['already'])
            self.assertTrue(b['already'])
            self.assertEqual(a['batchId'], b['batchId'])
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], 2)
            self.assertEqual(c.execute('SELECT SUM(qty_remaining) AS n FROM lots').fetchone()['n'], 6)
        finally:
            c.close()
            folder.cleanup()

    def test_legacy_single_size_still_works(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            r = stock.register_inbound(c, {
                'token': 'legacy-one',
                'customerProductId': handle,
                'size': 'M',
                'locationId': loc['id'],
                'qty': 5,
                'unitCost': 2000,
                'receivedOn': '2026-10-12',
            })
            c.commit()
            self.assertEqual(r['lotId'], 1)
            self.assertEqual(r['size'], 'M')
            self.assertEqual(r['currentQtyAfter'], 5)
            self.assertEqual(r['kind'], 'inbound')
            self.assertEqual(len(r['lines']), 1)
        finally:
            c.close()
            folder.cleanup()

    def test_period_summary_counts_as_inbound_not_opening(self):
        folder, path, c = newdb()
        try:
            _, _, loc, handle = seed_masters(c)
            stock.register_inbound(c, {
                'token': 'period-multi',
                'customerProductId': handle,
                'locationId': loc['id'],
                'receivedOn': '2026-10-05',
                'lines': [
                    {'size': 'M', 'qty': 3, 'unitCost': 1000},
                    {'size': 'L', 'qty': 2, 'unitCost': 1000},
                ],
            })
            stock.register_opening(c, {
                'token': 'period-open',
                'customerProductId': handle,
                'locationId': loc['id'],
                'basisDate': '2026-09-01',
                'receivedUnknown': True,
                'payoutOrder': 1,
                'lines': [{'size': 'M', 'qty': 7, 'unitCost': 500}],
            })
            c.commit()
            import stock_period
            data = stock_period.summarize_period(c, {'from': '2026-10-01', 'to': '2026-10-31'})
            m = next(r for r in data['rows'] if r.get('size') == 'M')
            self.assertEqual(m['inboundQty'], 3)
            self.assertEqual(m['openingQty'], 7)
            l = next(r for r in data['rows'] if r.get('size') == 'L')
            self.assertEqual(l['inboundQty'], 2)
            self.assertEqual(l['openingQty'], 0)
            self.assertEqual(data['totals']['inboundQty'], 5)
        finally:
            c.close()
            folder.cleanup()

    def test_http_multi_inbound_idempotency(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = Path(folder.name) / 'stock.sqlite3'
        server.prepare(path, 'live')
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.make_handler(path, 'live'))
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        import urllib.request

        def post(url_path, body):
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
            _, customer = post('/api/customers', {'name': 'HTTP通常', 'active': True})
            _, supplier = post('/api/suppliers', {'name': 'HTTP仕入2', 'active': True})
            _, product = post('/api/products', {
                'code': 'HN-1', 'name': 'HTTP通常商品', 'color': '白', 'supplierId': supplier['id'],
                'active': True, 'sizes': ['M', 'L'],
                'links': [{'customerId': customer['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            _, place = post('/api/locations', {'siteName': 'HTTP通常拠点', 'active': True})
            handle = product['links'][0]['id']
            body = {
                'token': 'http-multi-in',
                'customerProductId': handle,
                'locationId': place['id'],
                'receivedOn': '2026-10-13',
                'lines': [
                    {'size': 'M', 'qty': 2, 'unitCost': 900},
                    {'size': 'L', 'qty': 1, 'unitCost': 950},
                ],
            }
            st1, first = post('/api/inbound', body)
            st2, second = post('/api/inbound', body)
            self.assertEqual(st1, 200)
            self.assertEqual(st2, 200)
            self.assertFalse(first['already'])
            self.assertTrue(second['already'])
            self.assertEqual(first['totalQty'], 3)
            self.assertEqual(first['kind'], 'inbound')
        finally:
            httpd.shutdown()
            httpd.server_close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
