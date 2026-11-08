"""新しい在庫QR。合成データと一時DBのみ。発行と読取は数量を変えない。"""
from __future__ import annotations

import io
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import qr_image  # noqa: E402
import qr_link_import  # noqa: E402
import server as stock_server  # noqa: E402
import stock  # noqa: E402
import stock_qr  # noqa: E402

APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')

try:
    from PIL import Image
    from pyzbar.pyzbar import decode as zbar_decode
except Exception:
    Image = None
    zbar_decode = None


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def qty_state(c):
    row = c.execute(
        'SELECT COUNT(*) n, COALESCE(SUM(qty_remaining),0) q FROM lots'
    ).fetchone()
    return row['n'], row['q']


def seed(c):
    cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
    su = stock.save_supplier(c, {'name': 'メーカーA', 'active': True})
    pr = stock.save_product(c, {
        'code': 'U-1', 'name': '白衣', 'color': '白', 'category': stock.CATEGORY_UNIFORM,
        'supplierId': su['id'], 'active': True, 'sizes': ['M', 'L'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '倉庫1', 'active': True})
    loc2 = stock.save_location(c, {'siteName': '倉庫2', 'active': True})
    handle = pr['links'][0]['id']
    other = stock.ensure_customer_product(c, {
        'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '胸刺繍',
    })
    cu2 = stock.save_customer(c, {'name': '顧客B', 'active': True})
    other_customer = stock.ensure_customer_product(c, {
        'customerId': cu2['id'], 'productId': pr['id'], 'embroidery': '',
    })
    return {
        'customer': cu, 'product': pr, 'loc': loc, 'loc2': loc2,
        'handle': handle, 'embroidered': other['id'], 'other_customer': other_customer['id'],
    }


def inbound(c, handle, loc, size='M', qty=2, cost=1000, day='2026-04-01', condition='new', token='inbound-token'):
    return stock.register_inbound(c, {
        'token': token, 'customerProductId': handle, 'size': size,
        'locationId': loc, 'qty': qty, 'unitCost': cost, 'receivedOn': day,
        'condition': condition,
    })


def show(c, handle, size='M', condition='new'):
    if c.in_transaction:
        c.commit()
    c.execute('BEGIN IMMEDIATE')
    try:
        body = stock_qr.show(c, {
            'customerProductId': handle, 'size': size, 'condition': condition, '_canWrite': True,
        })
    except Exception:
        c.rollback()
        raise
    c.commit()
    return body


class StockQrTests(unittest.TestCase):
    def test_same_class_reuses_id_across_place_cost_and_receipt(self):
        folder, _path, c = newdb()
        try:
            fx = seed(c)
            inbound(c, fx['handle'], fx['loc']['id'], qty=2, cost=1000, day='2026-04-01', token='inbound-a-token')
            inbound(c, fx['handle'], fx['loc']['id'], qty=3, cost=1800, day='2026-05-01', token='inbound-b-token')
            inbound(c, fx['handle'], fx['loc2']['id'], qty=1, cost=1000, day='2026-06-01', token='inbound-c-token')
            c.commit()
            before = qty_state(c)
            first = show(c, fx['handle'])
            second = show(c, fx['handle'])
            self.assertTrue(stock_qr.PUBLIC_RE.fullmatch(first['publicId']))
            self.assertTrue(first['publicId'].startswith('nqr1.'))
            self.assertNotIn('白衣', first['publicId'])
            self.assertNotIn('顧客A', first['publicId'])
            self.assertEqual(first['publicId'], second['publicId'])
            self.assertTrue(first['created'])
            self.assertFalse(second['created'])
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM stock_qr_codes').fetchone()['n'], 1)
            self.assertEqual(first['lotCount'], 3)
            self.assertIsNone(first['pickedLotId'])
            self.assertFalse(first['qtyChanged'])
            self.assertEqual(qty_state(c), before)
            self.assertEqual(first['customerName'], '顧客A')
            self.assertEqual(first['productName'], '白衣')
            self.assertEqual(first['color'], '白')
            self.assertEqual(first['size'], 'M')
            self.assertEqual(first['conditionLabel'], '新品')
            self.assertEqual(first['embroideryLabel'], '加工なし')
        finally:
            c.close()
            folder.cleanup()

    def test_handle_customer_size_and_condition_are_distinct(self):
        folder, _path, c = newdb()
        try:
            fx = seed(c)
            plain = show(c, fx['handle'], 'M', 'new')
            embroidered = show(c, fx['embroidered'], 'M', 'new')
            other = show(c, fx['other_customer'], 'M', 'new')
            large = show(c, fx['handle'], 'L', 'new')
            used = show(c, fx['handle'], 'M', 'used')
            unset = show(c, fx['handle'], 'M', '')
            ids = {
                plain['publicId'], embroidered['publicId'], other['publicId'],
                large['publicId'], used['publicId'], unset['publicId'],
            }
            self.assertEqual(len(ids), 6)
            self.assertEqual(embroidered['embroideryLabel'], '胸刺繍')
            self.assertEqual(used['condition'], 'used')
            self.assertEqual(unset['condition'], '')
            self.assertEqual(unset['conditionLabel'], '未設定')
            self.assertEqual(unset['lotCount'], 0)
            self.assertIsNone(unset['pickedLotId'])
        finally:
            c.close()
            folder.cleanup()

    def test_rename_keeps_id_and_shows_current_names(self):
        folder, _path, c = newdb()
        try:
            fx = seed(c)
            first = show(c, fx['handle'])
            c.execute('UPDATE customers SET name=? WHERE id=?', ('顧客A改', fx['customer']['id']))
            c.execute(
                'UPDATE products SET name=?, color=? WHERE id=?',
                ('白衣改', '紺', fx['product']['id']),
            )
            c.commit()
            again = show(c, fx['handle'])
            self.assertEqual(again['publicId'], first['publicId'])
            self.assertEqual(again['customerName'], '顧客A改')
            self.assertEqual(again['productName'], '白衣改')
            self.assertEqual(again['color'], '紺')
            found = stock_qr.lookup(c, first['publicId'])
            self.assertTrue(found['found'])
            self.assertEqual(found['kind'], 'stock')
            self.assertEqual(found['productName'], '白衣改')
            self.assertFalse(found['qtyChanged'])
        finally:
            c.close()
            folder.cleanup()

    def test_new_form_does_not_fall_through_to_legacy(self):
        folder, _path, c = newdb()
        try:
            fx = seed(c)
            inbound(c, fx['handle'], fx['loc']['id'], token='legacy-lot-token')
            c.commit()
            legacy_id = '20230829160854438nGM1b58'
            t = stock.stamp()
            c.execute(
                '''INSERT INTO qr_stock_links(
                     legacy_stock_id, legacy_customer_id, legacy_maker_id, customer_name,
                     item_num, item_name, item_size, is_used, customer_id, product_id,
                     customer_product_id, size_label, condition, location_id, match_kind,
                     created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    legacy_id, 'cust', 'maker', '顧客A', 'U-1', '白衣', 'M', '新品',
                    fx['customer']['id'], fx['product']['id'], fx['handle'], 'M', 'new',
                    fx['loc']['id'], 'exact', t, t,
                ),
            )
            c.commit()
            before = qty_state(c)
            found = stock_qr.lookup(c, {'id': legacy_id, 'customer_id': 'cust', 'maker_id': 'maker'})
            self.assertTrue(found['found'])
            self.assertEqual(found['kind'], 'legacy')
            self.assertGreaterEqual(found['lotCount'], 1)
            self.assertIsNone(found['pickedLotId'])
            self.assertFalse(found['qtyChanged'])
            missing = 'nqr1.' + 'ab' * 16
            ghost = stock_qr.lookup(c, missing)
            self.assertFalse(ghost['found'])
            self.assertEqual(ghost['kind'], 'stock')
            self.assertEqual(qty_state(c), before)
            self.assertEqual(qr_link_import.lookup_qr(c, legacy_id)['found'], True)
        finally:
            c.close()
            folder.cleanup()

    def test_parallel_issue_keeps_one_row(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            c.commit()
            ids = []
            errors = []

            def work():
                cc = stock.connect(path)
                try:
                    cc.execute('BEGIN IMMEDIATE')
                    body = stock_qr.show(cc, {
                        'customerProductId': fx['handle'], 'size': 'M', 'condition': 'new',
                        '_canWrite': True,
                    })
                    cc.commit()
                    ids.append(body['publicId'])
                except Exception as e:
                    errors.append(e)
                    cc.rollback()
                finally:
                    cc.close()

            threads = [threading.Thread(target=work) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(set(ids)), 1)
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM stock_qr_codes').fetchone()['n'], 1)
        finally:
            c.close()
            folder.cleanup()

    def test_viewer_cannot_issue_but_can_read(self):
        folder, path, c = newdb()
        try:
            fx = seed(c)
            c.commit()
            code, denied = stock_server.api_post(
                str(path), 'desk', '/api/stock-qr',
                {'customerProductId': fx['handle'], 'size': 'M', 'condition': 'new'},
                can_write=False,
            )
            self.assertEqual(code, 403)
            self.assertIn('閲覧専用', denied['error'])
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM stock_qr_codes').fetchone()['n'], 0)
            code, made = stock_server.api_post(
                str(path), 'desk', '/api/stock-qr',
                {'customerProductId': fx['handle'], 'size': 'M', 'condition': '新品'},
                can_write=True,
            )
            self.assertEqual(code, 200)
            self.assertTrue(made['created'])
            code, seen = stock_server.api_post(
                str(path), 'desk', '/api/stock-qr',
                {'customerProductId': fx['handle'], 'size': 'M', 'condition': 'new'},
                can_write=False,
            )
            self.assertEqual(code, 200)
            self.assertEqual(seen['publicId'], made['publicId'])
            self.assertFalse(seen['created'])
            code, looked = stock_server.api_get(
                str(path), 'desk', '/api/stock-qr/lookup', {'qr': [made['publicId']]},
                can_write=False,
            )
            self.assertEqual(code, 200)
            self.assertTrue(looked['found'])
            self.assertIsNone(looked['pickedLotId'])
            code, bad = stock_server.api_post(
                str(path), 'desk', '/api/stock-qr',
                {'customerProductId': fx['handle'], 'size': 'M'},
                can_write=True,
            )
            self.assertEqual(code, 400)
        finally:
            c.close()
            folder.cleanup()

    def test_parts_are_not_excluded(self):
        folder, _path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
            su = stock.save_supplier(c, {'name': 'メーカーA', 'active': True})
            pr = stock.save_product(c, {
                'code': 'P-1', 'name': '襟上げテープ', 'color': '',
                'category': stock.CATEGORY_PARTS,
                'supplierId': su['id'], 'active': True, 'sizes': ['F'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            body = show(c, pr['links'][0]['id'], 'F', 'new')
            self.assertTrue(body['created'])
            self.assertEqual(body['productName'], '襟上げテープ')
        finally:
            c.close()
            folder.cleanup()

    def test_png_round_trip_is_the_id_only(self):
        if Image is None or zbar_decode is None:
            self.skipTest('画像の解読ライブラリが無いため往復確認を飛ばします')
        text = 'nqr1.3f2a9c1e7b804d6a91c0e5f4a2b8d714'
        png = qr_image.png_bytes(text)
        self.assertTrue(png.startswith(b'\x89PNG'))
        image = Image.open(io.BytesIO(png))
        image.load()
        found = zbar_decode(image)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].data.decode('utf-8'), text)
        folder, _path, c = newdb()
        try:
            fx = seed(c)
            body = show(c, fx['handle'])
            raw = __import__('base64').b64decode(body['pngBase64'])
            image = Image.open(io.BytesIO(raw))
            image.load()
            found = zbar_decode(image)
            self.assertEqual(found[0].data.decode('utf-8'), body['publicId'])
            self.assertNotIn('白衣'.encode(), found[0].data)
        finally:
            c.close()
            folder.cleanup()

    def test_missing_table_is_a_migration(self):
        folder, _path, c = newdb()
        try:
            self.assertFalse(stock.needs_migration(c))
            c.execute('DROP TABLE stock_qr_codes')
            self.assertTrue(stock.needs_migration(c))
            stock.initialize(c)
            self.assertFalse(stock.needs_migration(c))
        finally:
            c.close()
            folder.cleanup()

    def test_stock_list_menu_keeps_order_button(self):
        menu = re.search(r'function stockOpMenuItemsHtml\(.*?return `([^`]+)`', APP_JS, re.S).group(1)
        row = re.search(r'function stockActionButtonsHtml\(.*?return `([^`]+)`', APP_JS, re.S).group(1)
        self.assertIn('>操作</button>', row)
        self.assertIn('data-stock-order=', row)
        self.assertIn('>発注</button>', row)
        self.assertNotIn('>QR表示</button>', row)
        self.assertNotIn('>入庫</button>', row)
        self.assertEqual(menu.count('<button'), 3)
        self.assertIn('openInboundDialog(target)', APP_JS)
        self.assertIn('openOutboundDialog(target)', APP_JS)
        self.assertIn("openStockMoveFromRow(row,'入庫')", APP_JS)
        self.assertIn("openStockMoveFromRow(row,'出庫')", APP_JS)
        self.assertIn('function closeStockOpMenu()', APP_JS)
        self.assertIn('function clearStockOpTarget()', APP_JS)
        close = re.search(r'function closeStockOpMenu\(\)\{.*?\n}\n', APP_JS, re.S).group(0)
        self.assertIn('clearStockOpTarget()', close)
        opened = re.search(r'function openStockOpMenu\(btn,row\)\{.*?\n}\n', APP_JS, re.S).group(0)
        self.assertLess(opened.find('closeStockOpMenu()'), opened.find("classList.add('is-op-target')"))
        self.assertIn("closest('tr, article.dm-card')", APP_JS)
        product = re.search(r'function renderProductStockList\(groups\)\{.*?\n}\n', APP_JS, re.S).group(0)
        self.assertIn('productId:r.productId??g.productId', product)
        self.assertIn("e.key==='Escape'", APP_JS)
        self.assertIn("addEventListener('scroll'", APP_JS)
        self.assertIn('pointerdown', APP_JS)
        self.assertIn('r.left-mw-gap', APP_JS)
        self.assertEqual(APP_JS.count("menu.id='stockOpMenu'"), 1)
        self.assertIn("if(!stockCanWrite())return '—'", APP_JS[APP_JS.index('function stockActionButtonsHtml'):])
        order = re.search(r'async function openOrderFromStockRow\(row\)\{.*?\n}\n', APP_JS, re.S).group(0)
        self.assertIn('PurchaseOrders.openEditor(null,{customerId:row.customerId', order)
        # 操作メニューを結ぶのは在庫一覧だけ
        self.assertEqual(APP_JS.count('bindStockRowActions('), 3)
        self.assertIn('function stockList()', APP_JS)
        history = APP_JS[APP_JS.index('function historyPage()'):APP_JS.index('function historyPage()') + 500]
        self.assertNotIn('stockOpMenu', history)
        self.assertNotIn('bindStockRowActions', history)


if __name__ == '__main__':
    unittest.main()
