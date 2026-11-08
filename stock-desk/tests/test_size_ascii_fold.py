"""サイズの全角ASCII英数字だけを半角に揃える。LLと2Lは別。合成DBのみ。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import qr_link_import as ql  # noqa: E402
import stock  # noqa: E402
import stock_prices  # noqa: E402
import stock_qr  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    conn = stock.connect(path)
    stock.initialize(conn)
    pli.ensure_schema(conn)
    return folder, path, conn


class FoldTest(unittest.TestCase):
    def test_only_fullwidth_ascii_alnum(self):
        self.assertEqual(stock.fold_size_ascii('Ｓ'), 'S')
        self.assertEqual(stock.fold_size_ascii('Ｌ'), 'L')
        self.assertEqual(stock.fold_size_ascii('２L'), '2L')
        self.assertEqual(stock.fold_size_ascii('２Ｌ'), '2L')
        self.assertEqual(stock.fold_size_ascii('７６'), '76')
        self.assertEqual(stock.fold_size_ascii('LL'), 'LL')
        self.assertEqual(stock.fold_size_ascii('2L'), '2L')
        self.assertEqual(stock.fold_size_ascii('XL'), 'XL')
        self.assertEqual(stock.fold_size_ascii('ｌ'), 'l')
        self.assertEqual(stock.fold_size_ascii('２ L'), '2 L')
        self.assertEqual(stock.fold_size_ascii('㎜'), '㎜')
        self.assertEqual(stock.fold_size_ascii('①'), '①')
        self.assertNotEqual(stock.fold_size_ascii('LL'), stock.fold_size_ascii('2L'))
        self.assertNotEqual(stock.fold_size_ascii('LL'), stock.fold_size_ascii('２L'))


class ImportAndStockTest(unittest.TestCase):
    def test_product_import_collapses_width_but_not_names(self):
        folder, path, conn = newdb()
        try:
            text = '\n'.join([
                '顧客,仕入先,商品コード,商品名,サイズ',
                '顧客A,仕入A,SZ-1,作業着,Ｓ',
                '顧客A,仕入A,SZ-1,作業着,S',
                '顧客A,仕入A,SZ-1,作業着,LL',
                '顧客A,仕入A,SZ-1,作業着,2L',
                '顧客A,仕入A,SZ-1,作業着,２Ｌ',
                '顧客A,仕入A,SZ-1,作業着,７６',
                '顧客A,仕入A,SZ-1,作業着,㎜',
            ])
            body = {
                'importMode': 'products', 'name': 'sz.csv',
                'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True, '_actor': {'name': '確認'},
            }
            preview = pli.build_preview(conn, body)
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'size-ascii-fold-1',
            }, db_path=str(path))
            conn.commit()
            pid = conn.execute("SELECT id FROM products WHERE code='SZ-1'").fetchone()['id']
            labels = {
                row['label'] for row in conn.execute(
                    'SELECT label FROM product_sizes WHERE product_id=?', (pid,)
                )
            }
            self.assertEqual(labels, {'S', 'LL', '2L', '76', '㎜'})
            self.assertNotIn('２L', labels)
            self.assertNotIn('①', labels)
        finally:
            conn.close()
            folder.cleanup()

    def _product_with_size(self, conn, label='2L'):
        customer = stock.save_customer(conn, {'name': '顧客A', 'active': True})
        supplier = stock.save_supplier(conn, {'name': '仕入A', 'active': True})
        product = stock.save_product(conn, {
            'code': 'SZ-2', 'name': '作業着', 'color': '', 'supplierId': supplier['id'],
            'active': True, 'sizes': [label],
            'links': [{'customerId': customer['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
        })
        conn.commit()
        handle = conn.execute(
            'SELECT id FROM customer_products WHERE product_id=?', (product['id'],)
        ).fetchone()
        now = stock.stamp()
        conn.execute(
            'INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,NULL,1,?,?)',
            ('本社', now, now),
        )
        site = conn.execute('SELECT id FROM locations WHERE parent_id IS NULL').fetchone()['id']
        conn.execute(
            'INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,1,?,?)',
            ('棚A', site, now, now),
        )
        conn.execute(
            'INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,1,?,?)',
            ('棚B', site, now, now),
        )
        conn.commit()
        shelves = {
            row['name']: row['id']
            for row in conn.execute('SELECT id,name FROM locations WHERE parent_id IS NOT NULL')
        }
        return product, handle['id'], shelves

    def _open(self, conn, handle_id, location_id, size, qty, cost, token):
        return stock.register_opening(conn, {
            'token': token,
            'customerProductId': handle_id,
            'locationId': location_id,
            'basisDate': '2026-01-01',
            'receivedUnknown': True,
            'payoutOrder': 1,
            'condition': 'new',
            'allowZero': True,
            '_canWrite': True,
            '_actor': {'name': '確認'},
            'lines': [{'size': size, 'qty': qty, 'unitCost': cost}],
        })

    def test_opening_and_qr_follow_halfwidth_without_summing_dual_labels(self):
        folder, _path, conn = newdb()
        try:
            product, handle_id, shelves = self._product_with_size(conn, '2L')
            self._open(conn, handle_id, shelves['棚A'], '２L', 3, 1000, 'size-open-1')
            conn.commit()
            lots = list(conn.execute(
                'SELECT size_label, qty_remaining, unit_cost, location_id FROM lots ORDER BY id'
            ))
            self.assertEqual(len(lots), 1)
            self.assertEqual(lots[0]['size_label'], '2L')
            self.assertEqual(lots[0]['qty_remaining'], 3)
            again = self._open(conn, handle_id, shelves['棚A'], '2L', 9, 1000, 'size-open-1')
            self.assertTrue(again.get('already'))
            self.assertEqual(conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'], 3)
            self._open(conn, handle_id, shelves['棚A'], '2L', 1, 1500, 'size-open-cost')
            self._open(conn, handle_id, shelves['棚B'], '２L', 2, 1000, 'size-open-place')
            conn.commit()
            rows = list(conn.execute(
                'SELECT size_label, qty_remaining, unit_cost, location_id FROM lots ORDER BY id'
            ))
            self.assertEqual(len(rows), 3)
            self.assertEqual({row['size_label'] for row in rows}, {'2L'})
            self.assertEqual(sorted(row['unit_cost'] for row in rows), [1000, 1000, 1500])
            self.assertEqual(len({row['location_id'] for row in rows}), 2)

            conn.execute(
                'INSERT INTO price_base(supplier_id,product_id,size_label,price,status,reason,source,updated_at) '
                'VALUES(?,?,?,?,?,?,?,?)',
                (product['supplierId'], product['id'], '2L', 1000, 'ok', '', 'test', stock.stamp()),
            )
            conn.commit()
            looked = stock_prices.lookup(
                conn, customer_id=None, product_id=product['id'], size_label='２L',
                supplier_id=product['supplierId'],
            )
            self.assertEqual(looked['price'], 1000)

            cp = conn.execute(
                'SELECT id FROM customer_products WHERE product_id=?', (product['id'],)
            ).fetchone()['id']
            first = stock_qr.show(conn, {
                'customerProductId': cp, 'size': '2L', 'condition': 'new',
                '_canWrite': True,
            })
            second = stock_qr.show(conn, {
                'customerProductId': cp, 'size': '２L', 'condition': 'new',
                '_canWrite': True,
            })
            self.assertEqual(first['publicId'], second['publicId'])
            self.assertFalse(second['created'])
            self.assertEqual(conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'], 6)

            conn.execute(
                'INSERT INTO product_sizes(product_id,label,sort_order) VALUES(?,?,?)',
                (product['id'], '２L', 9),
            )
            conn.commit()
            before = conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q']
            with self.assertRaises(ValueError) as caught:
                self._open(conn, handle_id, shelves['棚A'], '２Ｌ', 8, 1000, 'size-open-both')
            self.assertIn('合算しません', str(caught.exception))
            self.assertEqual(conn.execute('SELECT SUM(qty_remaining) q FROM lots').fetchone()['q'], before)
        finally:
            conn.close()
            folder.cleanup()

    def test_old_qr_wide_f_matches_halfwidth(self):
        folder, _path, conn = newdb()
        try:
            customer = stock.save_customer(conn, {'name': '顧客A', 'active': True})
            supplier = stock.save_supplier(conn, {'name': '仕入A', 'active': True})
            stock.save_product(conn, {
                'code': 'AZ-100', 'name': '白衣', 'color': '', 'supplierId': supplier['id'],
                'active': True, 'sizes': ['F'],
                'links': [{'customerId': customer['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            conn.commit()
            kind, labels = ql._size_hits('Ｆ', ['F'])
            self.assertEqual(kind, 'normalized')
            self.assertEqual(labels, ['F'])
            kind_ll, _ = ql._size_hits('LL', ['2L', 'LL'])
            self.assertEqual(kind_ll, 'exact')
            self.assertEqual(ql._size_hits('㎜', ['mm'])[0], 'missing')
        finally:
            conn.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
