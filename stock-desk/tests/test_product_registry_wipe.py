"""商品登録全削除（検証用）— 一時DBのみ。本番在庫DBには触れない。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import product_registry_wipe as wipe  # noqa: E402
import sample_data  # noqa: E402
import server as stock_server  # noqa: E402
import stock  # noqa: E402
import stock_orders  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def owner_body(**extra):
    body = {
        '_canWrite': True,
        '_actor': {'id': 1, 'name': '主管理テスト', 'role': 'owner'},
        'confirmText': wipe.CONFIRM_TEXT,
    }
    body.update(extra)
    return body


def csv_b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8-sig')).decode('ascii')


def tiny_ledger(rows):
    return '\n'.join(['顧客,仕入先,商品コード,商品名,サイズ'] + rows)


class UiWiringTest(unittest.TestCase):
    def test_products_page_has_verify_zone(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        js = (ROOT / 'static' / 'product_registry_wipe.js').read_text(encoding='utf-8')
        self.assertIn('商品登録を全削除（検証用）', app)
        self.assertIn('verify-zone', app)
        self.assertIn('stockIsOwner', app)
        self.assertIn('productRegistryWipeOpen', app)
        self.assertIn('product_registry_wipe.js', html)
        self.assertIn('ProductRegistryWipe', js)
        self.assertIn(wipe.CONFIRM_TEXT, js)
        self.assertIn('/api/products/registry-wipe', js)
        self.assertIn('disabled', js)


class OwnerAuthTest(unittest.TestCase):
    def test_non_owner_preview_and_wipe_rejected(self):
        folder, path, c = newdb()
        try:
            stock.save_customer(c, {'name': '顧客A', 'active': True})
            stock.save_supplier(c, {'name': '仕入A', 'active': True})
            sid = c.execute('SELECT id FROM suppliers').fetchone()['id']
            cid = c.execute('SELECT id FROM customers').fetchone()['id']
            stock.save_product(c, {
                'code': 'W-1', 'name': 'ウェア', 'color': '白', 'supplierId': sid, 'active': True,
                'sizes': ['M'],
                'links': [{'customerId': cid, 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            for role in ('sales', 'floor', 'manage', ''):
                body = {
                    '_canWrite': True,
                    '_actor': {'id': 2, 'name': '一般', 'role': role},
                    'confirmText': wipe.CONFIRM_TEXT,
                }
                with self.assertRaises(PermissionError):
                    wipe.preview_wipe(c, body)
                with self.assertRaises(PermissionError):
                    wipe.execute_wipe(c, body, db_path=path)
            # manage 相当: can_write でも role が owner でなければ不可
            code, resp = stock_server.api_post(
                str(path), 'desk', '/api/products/registry-wipe',
                {'_actor': {'name': '管理者', 'role': 'sales'}, 'confirmText': wipe.CONFIRM_TEXT},
                can_write=True,
                actor={'id': 2, 'name': '管理者', 'role': 'sales'},
            )
            self.assertEqual(code, 403)
            self.assertFalse(resp.get('deleted'))
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 1)
        finally:
            c.close()
            folder.cleanup()

    def test_root_proxy_gates_owner(self):
        root = (ROOT.parent / 'server.py').read_text(encoding='utf-8')
        self.assertIn("api_path in ('/api/products/registry-wipe'", root)
        self.assertIn("u['role']!='owner'", root)
        self.assertIn("'role': u['role']", root)


class WipeSuccessTest(unittest.TestCase):
    def test_products_and_handles_only_wiped(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入A', 'active': True})
            su2 = stock.save_supplier(c, {'name': '仕入B', 'active': True})
            loc = stock.save_location(c, {'siteName': '倉庫X', 'active': True})
            p = stock.save_product(c, {
                'code': 'SCR-1', 'name': 'スクラブ', 'color': '白',
                'supplierId': su['id'], 'subSupplierIds': [su2['id']], 'active': True,
                'sizes': ['M', 'L'],
                'links': [
                    {'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                    {'customerId': cu['id'], 'embroidery': 'ネーム', 'isStandard': False, 'active': False},
                ],
            })
            # 台帳マップ（再取込阻害）を模擬
            c.execute(
                '''INSERT INTO product_ledger_row_maps(
                     product_map_key,row_map_key,match_group_key,file_sha256,file_name,source_row_no,
                     csv_customer,csv_supplier,csv_product_code,csv_product_name,csv_size,
                     customer_id,product_id,customer_product_id,run_id,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    'k', 'rk', '', 'sha', 't.csv', 1,
                    '顧客A', '仕入A', 'SCR-1', 'スクラブ', 'M',
                    cu['id'], p['id'], p['links'][0]['id'], None, stock.now(), stock.now(),
                ),
            )
            c.commit()
            kept_before = wipe.kept_snapshot(c)
            self.assertEqual(kept_before['customers'], 1)
            self.assertEqual(kept_before['suppliers'], 2)
            self.assertEqual(kept_before['locations'], 2)  # 未確認 + 倉庫X

            preview = wipe.preview_wipe(c, owner_body())
            self.assertTrue(preview['canWipe'])
            self.assertEqual(preview['counts']['products'], 1)
            self.assertEqual(preview['counts']['customerProducts'], 2)
            self.assertEqual(preview['counts']['productLedgerRowMaps'], 1)

            code, result = stock_server.api_post(
                str(path), 'desk', '/api/products/registry-wipe',
                owner_body(),
                can_write=True,
                actor={'id': 1, 'name': '主管理テスト', 'role': 'owner'},
            )
            self.assertEqual(code, 200, result)
            self.assertTrue(result.get('ok'))
            self.assertTrue(Path(result['backupPath']).is_file())

            with stock.connect(path) as c2:
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 0)
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM product_sizes').fetchone()['n'], 0)
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM product_suppliers').fetchone()['n'], 0)
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'], 0)
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM product_ledger_row_maps').fetchone()['n'], 0)
                after = wipe.kept_snapshot(c2)
                self.assertEqual(after['customers'], kept_before['customers'])
                self.assertEqual(after['suppliers'], kept_before['suppliers'])
                self.assertEqual(after['locations'], kept_before['locations'])
                self.assertEqual(after.get('lots', 0), 0)
                # 拠点名が残っている
                names = {r['name'] for r in c2.execute('SELECT name FROM locations')}
                self.assertIn('倉庫X', names)
                self.assertIn(loc['name'] if 'name' in loc else '倉庫X', names)
        finally:
            c.close()
            folder.cleanup()


class InUseAbortTest(unittest.TestCase):
    def test_stock_reference_aborts_nothing_deleted(self):
        folder, path, c = newdb()
        try:
            sample_data.seed(c)
            c.commit()
            before_p = c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n']
            before_cp = c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n']
            before_lots = c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n']
            self.assertGreater(before_lots, 0)

            preview = wipe.preview_wipe(c, owner_body())
            self.assertFalse(preview['canWipe'])
            self.assertTrue(preview['blockers'])

            code, resp = stock_server.api_post(
                str(path), 'desk', '/api/products/registry-wipe',
                owner_body(),
                can_write=True,
                actor={'id': 1, 'name': '主管理テスト', 'role': 'owner'},
            )
            self.assertEqual(code, 409)
            self.assertEqual(resp.get('code'), 'in_use')
            self.assertFalse(resp.get('deleted'))
            self.assertTrue(resp.get('blockers'))
            for b in resp['blockers']:
                self.assertIn('productId', b)
                self.assertTrue(b.get('reasons'))

            with stock.connect(path) as c2:
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], before_p)
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'], before_cp)
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], before_lots)
        finally:
            c.close()
            folder.cleanup()

    def test_zero_qty_lot_still_blocks(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客Z', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入Z', 'active': True})
            site = stock.save_location(c, {'siteName': '拠点Z', 'active': True})
            p = stock.save_product(c, {
                'code': 'Z-1', 'name': 'ゼロ在庫', 'color': '', 'supplierId': su['id'], 'active': True,
                'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            cp_id = p['links'][0]['id']
            stock.register_opening(c, {
                '_canWrite': True, '_actor': {'name': 't'},
                'token': 'zero-qty-open', 'customerProductId': cp_id, 'locationId': site['id'],
                'basisDate': '2026-01-01', 'receivedUnknown': True, 'payoutOrder': 1,
                'lines': [{'size': 'M', 'qty': 1, 'unitCost': 100}],
            })
            plan = stock.plan_outbound(c, {
                'customerProductId': cp_id, 'locationId': site['id'], 'size': 'M', 'qty': 1,
            })
            # 全量出庫して数量0に（ロット行は残る）
            stock.register_outbound(c, {
                '_canWrite': True, '_actor': {'name': 't'},
                'token': 'zero-qty-out', 'customerProductId': cp_id, 'locationId': site['id'],
                'size': 'M', 'qty': 1, 'businessOn': '2026-01-02',
                'fingerprint': plan['fingerprint'],
            })
            c.commit()
            lot = c.execute('SELECT qty_remaining FROM lots').fetchone()
            self.assertEqual(lot['qty_remaining'], 0)

            preview = wipe.preview_wipe(c, owner_body())
            self.assertFalse(preview['canWipe'])
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 1)
        finally:
            c.close()
            folder.cleanup()

    def test_purchase_order_blocks(self):
        folder, path, c = newdb()
        try:
            stock_orders.migrate_purchase_orders(c)
            cu = stock.save_customer(c, {'name': '顧客PO', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入PO', 'active': True})
            p = stock.save_product(c, {
                'code': 'PO-1', 'name': '発注商品', 'color': '紺', 'supplierId': su['id'], 'active': True,
                'sizes': ['L'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            stock_orders.save_purchase_order(c, {
                'token': 'po-wipe-1',
                'supplierId': su['id'],
                'customerId': cu['id'],
                'lines': [{
                    'customerProductId': p['links'][0]['id'],
                    'sizes': {'L': 2},
                }],
                '_canWrite': True,
                '_actor': {'name': '発注者'},
            })
            c.commit()
            preview = wipe.preview_wipe(c, owner_body())
            self.assertFalse(preview['canWipe'])
            reasons = ' '.join(
                r for b in preview['blockers'] for r in b.get('reasons') or []
            )
            self.assertIn('発注', reasons)
        finally:
            c.close()
            folder.cleanup()


class BackupAndRollbackTest(unittest.TestCase):
    def test_backup_failure_deletes_nothing(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客B', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入B', 'active': True})
            stock.save_product(c, {
                'code': 'B-1', 'name': 'バックアップ失敗', 'color': '', 'supplierId': su['id'], 'active': True,
                'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            with mock.patch.object(stock, 'backup_sqlite', side_effect=stock.BackupError()):
                code, resp = stock_server.api_post(
                    str(path), 'desk', '/api/products/registry-wipe',
                    owner_body(),
                    can_write=True,
                    actor={'id': 1, 'name': '主管理テスト', 'role': 'owner'},
                )
            self.assertEqual(code, 400)
            self.assertEqual(resp.get('code'), 'backup_failed')
            self.assertFalse(resp.get('deleted'))
            with stock.connect(path) as c2:
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 1)
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'], 1)
        finally:
            c.close()
            folder.cleanup()

    def test_mid_failure_rolls_back(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客R', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入R', 'active': True})
            stock.save_product(c, {
                'code': 'R-1', 'name': '途中失敗', 'color': '', 'supplierId': su['id'], 'active': True,
                'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            real_delete = wipe._delete_registry

            def boom(conn):
                real_delete(conn)
                raise RuntimeError('simulated mid-failure')

            with mock.patch.object(wipe, '_delete_registry', side_effect=boom):
                code, resp = stock_server.api_post(
                    str(path), 'desk', '/api/products/registry-wipe',
                    owner_body(),
                    can_write=True,
                    actor={'id': 1, 'name': '主管理テスト', 'role': 'owner'},
                )
            self.assertEqual(code, 400)
            self.assertFalse(resp.get('deleted'))
            with stock.connect(path) as c2:
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 1)
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'], 1)
        finally:
            c.close()
            folder.cleanup()


class ReimportAfterWipeTest(unittest.TestCase):
    def test_ledger_csv_can_reregister(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger([
                '顧客A,仕入A,RW-1,再取込スクラブ,M',
                '顧客A,仕入A,RW-1,再取込スクラブ,L',
                '顧客B,仕入A,RW-1,再取込スクラブ,M',
            ])
            body = {
                'file': csv_b64(text), 'name': 'reimport.csv',
                'token': 'wipe-reimport-1',
                '_canWrite': True, '_actor': {'name': '取込', 'role': 'owner'},
            }
            preview = pli.build_preview(c, body)
            c.commit()
            self.assertGreater(preview['countsRaw'].get('create', 0), 0)
            first = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'wipe-reimport-1',
            }, db_path=path)
            c.commit()
            self.assertFalse(first.get('already'))
            self.assertGreater(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 0)
            self.assertGreater(c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'], 0)
            stock_before = pli.stock_snapshot(c)

            wiped = wipe.execute_wipe(c, owner_body(), db_path=path)
            c.commit()
            self.assertTrue(wiped['ok'])
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM product_ledger_row_maps').fetchone()['n'], 0)

            preview2 = pli.build_preview(c, body)
            self.assertGreater(preview2['countsRaw'].get('create', 0), 0)
            second = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'wipe-reimport-2',
            }, db_path=path)
            c.commit()
            self.assertFalse(second.get('already'))
            self.assertGreater(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 0)
            self.assertGreater(c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'], 0)
            self.assertEqual(pli.stock_snapshot(c), stock_before)
        finally:
            c.close()
            folder.cleanup()


class ConfirmTextTest(unittest.TestCase):
    def test_wrong_confirm_rejected(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客C', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入C', 'active': True})
            stock.save_product(c, {
                'code': 'C-1', 'name': '確認文言', 'color': '', 'supplierId': su['id'], 'active': True,
                'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            code, resp = stock_server.api_post(
                str(path), 'desk', '/api/products/registry-wipe',
                owner_body(confirmText='全削除'),
                can_write=True,
                actor={'id': 1, 'name': '主管理テスト', 'role': 'owner'},
            )
            self.assertEqual(code, 400)
            self.assertFalse(resp.get('deleted'))
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 1)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
