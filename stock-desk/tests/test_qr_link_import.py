"""旧QR対応Excel。合成データと一時DBのみ。在庫数量は変えない。"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import excel_book  # noqa: E402
import qr_link_import as ql  # noqa: E402
import server as stock_server  # noqa: E402
import stock  # noqa: E402

OLD = '20230829160854438nGM1b58'
CUST = '201908301105469388iv0hnc'
MAKER = '20190830110339xd4rzr4zon'
QR = json.dumps({'id': OLD, 'customer_id': CUST, 'maker_id': MAKER}, ensure_ascii=False)


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def snap(c):
    return ql.stock_snapshot(c)


def xlsx(rows):
    headers = [label for label, _key, _req in ql.COLUMNS]
    blob = excel_book.workbook([(ql.SHEET_NAME, headers, rows)])
    return blob


def b64(blob):
    return base64.b64encode(blob).decode('ascii')


def blank_row():
    return [''] * len(ql.COLUMNS)


def put(row, key, value):
    for i, (_label, col, _req) in enumerate(ql.COLUMNS):
        if col == key:
            row[i] = value
            return row
    raise KeyError(key)


def filled(**kwargs):
    row = blank_row()
    base = {
        'legacyStockId': OLD,
        'legacyCustomerId': CUST,
        'legacyMakerId': MAKER,
        'customerName': '顧客A',
        'makerName': 'メーカーA',
        'itemNum': 'AZ-8618-003',
        'itemName': 'ワークキャップ',
        'itemSize': 'F',
        'isUsed': '新品',
        'qty': '2',
        'price': '1200',
        'factoryName': '本社',
        'shelfName': 'A-1',
    }
    base.update(kwargs)
    for key, value in base.items():
        put(row, key, value)
    return row


def seed_cap(c, *, code='AZ-8618-003', customer='顧客A', sizes=None, name='ワークキャップ'):
    cu = stock.save_customer(c, {'name': customer, 'active': True})
    su = stock.save_supplier(c, {'name': 'メーカーA', 'active': True})
    pr = stock.save_product(c, {
        'code': code, 'name': name, 'color': '', 'supplierId': su['id'],
        'active': True, 'sizes': sizes or ['F', 'M'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc = stock.save_location(c, {'siteName': '本社', 'shelfName': 'A-1', 'active': True})
    cp = pr['links'][0]['id']
    stock.register_opening(c, {
        'token': 'open-qr-' + code + '-new',
        'customerProductId': cp, 'locationId': loc['id'], 'condition': 'new',
        'basisDate': '2026-01-01', 'receivedUnknown': True, 'payoutOrder': 1,
        'allowZero': True, 'lines': [{'size': 'F', 'qty': 2, 'unitCost': 1200}],
    })
    stock.register_opening(c, {
        'token': 'open-qr-' + code + '-used',
        'customerProductId': cp, 'locationId': loc['id'], 'condition': 'used',
        'basisDate': '2026-01-01', 'receivedUnknown': True, 'payoutOrder': 2,
        'allowZero': True, 'lines': [{'size': 'F', 'qty': 1, 'unitCost': 800}],
    })
    c.commit()
    return {'customer': cu, 'product': pr, 'location': loc, 'supplier': su}


def body_of(blob, **extra):
    return {'file': b64(blob), 'name': 'qr.xlsx', '_canWrite': True, '_actor': {'name': 'テスト'}, **extra}


class QrLinkTemplateTest(unittest.TestCase):
    def test_template_roundtrip_keeps_string_ids(self):
        blob = ql.template_bytes()
        self.assertTrue(blob.startswith(b'PK'))
        grid = ql._read_sheet_rows(blob)
        headers = [cell['text'] for cell in grid[0]]
        self.assertEqual(headers, [label for label, _k, _r in ql.COLUMNS])
        notes = excel_book.read_sheets(blob)['説明']
        flat = '\n'.join(cell for row in notes for cell in row)
        self.assertIn('在庫数量・金額・入出庫履歴は変えません', flat)
        self.assertIn('ハイフン', flat)
        row = filled(legacyStockId='00123nGM', legacyCustomerId=CUST, legacyMakerId=MAKER)
        parsed = ql.parse_workbook(xlsx([row]))
        cells = parsed[0][1]
        self.assertEqual(cells['legacyStockId']['text'], '00123nGM')
        self.assertEqual(cells['legacyStockId']['kind'], 'inline')
        self.assertNotEqual(cells['legacyStockId']['kind'], 'n')

    def test_template_download_route(self):
        folder, path, c = newdb()
        try:
            status, data, ctype, headers = stock_server.api_get(
                str(path), 'desk', '/api/qr-link/template', {},
            )
            self.assertEqual(status, 200)
            self.assertTrue(data.startswith(b'PK'))
            self.assertIn('sheet', ctype)
            self.assertIn('qr-link-template.xlsx', headers['Content-Disposition'])
        finally:
            c.close()
            folder.cleanup()


class QrLinkMatchTest(unittest.TestCase):
    def test_one_exact_commit_does_not_change_stock(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            before = snap(c)
            blob = xlsx([filled()])
            preview = ql.build_preview(c, body_of(blob))
            self.assertEqual(preview['countsRaw']['exact'], 1)
            self.assertTrue(preview['rows'][0]['includeDefault'])
            self.assertEqual(preview['rows'][0]['legacy']['condition'], 'new')
            result = ql.commit_preview(c, body_of(
                blob, previewFingerprint=preview['previewFingerprint'],
                previewAt=preview['previewAt'], token='qr-exact-token-1',
            ), db_path=str(path))
            c.commit()
            self.assertEqual(result['created'], 1)
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(snap(c), before)
            link = c.execute('SELECT * FROM qr_stock_links').fetchone()
            self.assertEqual(link['legacy_stock_id'], OLD)
            self.assertEqual(link['legacy_customer_id'], CUST)
            self.assertEqual(link['legacy_maker_id'], MAKER)
            self.assertEqual(link['item_num'], 'AZ-8618-003')
            self.assertEqual(link['customer_name'], '顧客A')
            self.assertEqual(link['size_label'], 'F')
            self.assertEqual(link['condition'], 'new')
            self.assertEqual(link['price_text'], '1200')
            self.assertEqual(link['qty_text'], '2')
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 1)
            found = ql.lookup_qr(c, {'id': OLD, 'customer_id': CUST, 'maker_id': MAKER})
            self.assertTrue(found['found'])
            self.assertIsNone(found['pickedLotId'])
            self.assertEqual(found['lotCount'], 1)
            self.assertEqual(found['lots'][0]['qtyRemaining'], 2)
        finally:
            c.close()
            folder.cleanup()

    def test_hyphen_only_is_not_exact_and_both_codes_are_ambiguous(self):
        folder, path, c = newdb()
        try:
            seed_cap(c, code='AZ8618003')
            blob = xlsx([filled(itemNum='AZ-8618-003')])
            preview = ql.build_preview(c, body_of(blob))
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'review')
            self.assertFalse(row['includeDefault'])
            self.assertEqual(row['candidates'][0]['codeMatch'], 'normalized')
            self.assertNotEqual(row['status'], 'exact')
            before = snap(c)
            with self.assertRaises(ValueError):
                ql.commit_preview(c, body_of(
                    blob, previewFingerprint=preview['previewFingerprint'],
                    token='qr-norm-skip-01',
                ), db_path=str(path))
            c.rollback()
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
            self.assertEqual(snap(c), before)
            key = row['candidates'][0]['key']
            preview2 = ql.build_preview(c, body_of(blob))
            result = ql.commit_preview(c, body_of(
                blob, previewFingerprint=preview2['previewFingerprint'],
                token='qr-norm-pick-01',
                selections={'2': {'include': True, 'candidateKey': key}},
            ), db_path=str(path))
            c.commit()
            self.assertEqual(result['created'], 1)
            self.assertEqual(snap(c), before)
            self.assertEqual(
                c.execute('SELECT match_kind FROM qr_stock_links').fetchone()['match_kind'],
                'manual',
            )
        finally:
            c.close()
            folder.cleanup()

    def test_exact_and_hyphen_variant_are_not_auto(self):
        folder, path, c = newdb()
        try:
            seed_cap(c, code='AZ-8618-003')
            cu = c.execute("SELECT id FROM customers WHERE name='顧客A'").fetchone()['id']
            su = c.execute("SELECT id FROM suppliers WHERE name='メーカーA'").fetchone()['id']
            stock.save_product(c, {
                'code': 'AZ8618003', 'name': 'ワークキャップ', 'color': '', 'supplierId': su,
                'active': True, 'sizes': ['F'],
                'links': [{'customerId': cu, 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            before = snap(c)
            products_before = before['products']
            preview = ql.build_preview(c, body_of(xlsx([filled()])))
            self.assertEqual(preview['rows'][0]['status'], 'ambiguous')
            self.assertFalse(preview['rows'][0]['includeDefault'])
            self.assertGreaterEqual(len(preview['rows'][0]['candidates']), 2)
            kinds = {item['codeMatch'] for item in preview['rows'][0]['candidates']}
            self.assertEqual(kinds, {'exact', 'normalized'})
            with self.assertRaises(ValueError):
                ql.commit_preview(c, body_of(
                    xlsx([filled()]), previewFingerprint=preview['previewFingerprint'],
                    token='qr-ambig-skip01',
                ), db_path=str(path))
            c.rollback()
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
            self.assertEqual(snap(c)['products'], products_before)
            self.assertEqual(snap(c)['qty'], before['qty'])
        finally:
            c.close()
            folder.cleanup()

    def test_unmatched_does_not_create_product(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            before = snap(c)
            blob = xlsx([filled(itemNum='NO-SUCH', itemName='無い商品')])
            preview = ql.build_preview(c, body_of(blob))
            self.assertEqual(preview['rows'][0]['status'], 'unmatched')
            self.assertFalse(preview['rows'][0]['candidates'])
            with self.assertRaises(ValueError):
                ql.commit_preview(c, body_of(
                    blob, previewFingerprint=preview['previewFingerprint'],
                    token='qr-miss-token01',
                    selections={'2': {'include': True}},
                ), db_path=str(path))
            c.rollback()
            self.assertEqual(snap(c), before)
        finally:
            c.close()
            folder.cleanup()

    def test_null_condition_is_not_new(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            before = snap(c)
            blob = xlsx([filled(isUsed='null')])
            preview = ql.build_preview(c, body_of(blob))
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'exact')
            self.assertEqual(row['legacy']['condition'], '')
            self.assertNotEqual(row['legacy']['condition'], 'new')
            ql.commit_preview(c, body_of(
                blob, previewFingerprint=preview['previewFingerprint'],
                token='qr-null-cond-01',
            ), db_path=str(path))
            c.commit()
            link = c.execute('SELECT condition FROM qr_stock_links').fetchone()
            self.assertEqual(link['condition'], '')
            found = ql.lookup_qr(c, OLD)
            self.assertIsNone(found['pickedLotId'])
            self.assertEqual(found['lotCount'], 2)
            self.assertEqual(snap(c), before)
        finally:
            c.close()
            folder.cleanup()

    def test_same_identity_customers_are_not_auto(self):
        folder, path, c = newdb()
        try:
            a = stock.save_customer(c, {'name': '青葉病院', 'active': True})
            b = stock.save_customer(c, {'name': '青葉 病院', 'active': True})
            su = stock.save_supplier(c, {'name': 'メーカーA', 'active': True})
            stock.save_product(c, {
                'code': 'AZ-8618-003', 'name': 'ワークキャップ', 'color': '',
                'supplierId': su['id'], 'active': True, 'sizes': ['F'],
                'links': [
                    {'customerId': a['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                    {'customerId': b['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                ],
            })
            stock.save_location(c, {'siteName': '本社', 'shelfName': 'A-1', 'active': True})
            c.commit()
            preview = ql.build_preview(c, body_of(xlsx([filled(customerName='青葉病院')])))
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'ambiguous')
            names = {item['customerName'] for item in row['candidates']}
            self.assertEqual(names, {'青葉病院', '青葉 病院'})
            self.assertFalse(row['includeDefault'])
        finally:
            c.close()
            folder.cleanup()

    def test_company_form_is_not_exact(self):
        folder, path, c = newdb()
        try:
            seed_cap(c, customer='株式会社北光')
            preview = ql.build_preview(c, body_of(xlsx([
                filled(customerName='㈱北光'),
            ])))
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'review')
            self.assertEqual(row['candidates'][0]['customerMatch'], 'form')
            self.assertNotEqual(row['status'], 'exact')
        finally:
            c.close()
            folder.cleanup()

    def test_duplicate_reimport_and_conflict(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            before = snap(c)
            dup = xlsx([filled(), filled()])
            preview = ql.build_preview(c, body_of(dup))
            self.assertEqual(preview['countsRaw']['duplicate'], 2)
            with self.assertRaises(ValueError):
                ql.commit_preview(c, body_of(
                    dup, previewFingerprint=preview['previewFingerprint'],
                    token='qr-dup-token-01',
                ), db_path=str(path))
            c.rollback()
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)

            one = xlsx([filled()])
            first = ql.build_preview(c, body_of(one))
            ql.commit_preview(c, body_of(
                one, previewFingerprint=first['previewFingerprint'],
                token='qr-first-token1',
            ), db_path=str(path))
            c.commit()
            again = ql.build_preview(c, body_of(one))
            self.assertEqual(again['rows'][0]['status'], 'unchanged')
            second = ql.commit_preview(c, body_of(
                one, previewFingerprint=again['previewFingerprint'],
                token='qr-second-token',
            ), db_path=str(path))
            c.commit()
            self.assertEqual(second['created'], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 1)
            same = ql.commit_preview(c, body_of(
                one, previewFingerprint=again['previewFingerprint'],
                token='qr-first-token1',
            ), db_path=str(path))
            self.assertTrue(same['already'])
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 1)

            cu = c.execute("SELECT id FROM customers WHERE name='顧客A'").fetchone()['id']
            su = c.execute("SELECT id FROM suppliers WHERE name='メーカーA'").fetchone()['id']
            other = stock.save_product(c, {
                'code': 'AZ-1', 'name': '別商品', 'color': '', 'supplierId': su,
                'active': True, 'sizes': ['F'],
                'links': [{'customerId': cu, 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            changed = xlsx([filled(itemNum='AZ-1', itemName='別商品')])
            conflict = ql.build_preview(c, body_of(changed))
            self.assertEqual(conflict['rows'][0]['status'], 'conflict')
            self.assertFalse(conflict['rows'][0]['includeDefault'])
            with self.assertRaises(ValueError):
                ql.commit_preview(c, body_of(
                    changed, previewFingerprint=conflict['previewFingerprint'],
                    token='qr-conflict-nope',
                ), db_path=str(path))
            c.rollback()
            self.assertEqual(
                c.execute('SELECT product_id FROM qr_stock_links').fetchone()['product_id'],
                c.execute("SELECT id FROM products WHERE code='AZ-8618-003'").fetchone()['id'],
            )
            key = conflict['rows'][0]['candidates'][0]['key']
            conflict2 = ql.build_preview(c, body_of(changed))
            replaced = ql.commit_preview(c, body_of(
                changed, previewFingerprint=conflict2['previewFingerprint'],
                token='qr-conflict-yes1',
                selections={'2': {'include': True, 'replace': True, 'candidateKey': key}},
            ), db_path=str(path))
            c.commit()
            self.assertEqual(replaced['replaced'], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 1)
            self.assertEqual(
                c.execute('SELECT product_id FROM qr_stock_links').fetchone()['product_id'],
                other['id'],
            )
            self.assertEqual(snap(c)['qty'], before['qty'])
            self.assertEqual(snap(c)['amount'], before['amount'])
            self.assertEqual(snap(c)['moves'], before['moves'])
        finally:
            c.close()
            folder.cleanup()

    def test_numeric_id_json_bad_file_permission_and_stale(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            before = snap(c)
            numeric = filled()
            numeric[0] = 20230829160854438
            blob = xlsx([numeric])
            preview = ql.build_preview(c, body_of(blob))
            self.assertEqual(preview['rows'][0]['status'], 'invalid')
            self.assertTrue(any('数値' in r for r in preview['rows'][0]['reasons']))
            with self.assertRaises(ValueError):
                ql.commit_preview(c, body_of(
                    blob, previewFingerprint=preview['previewFingerprint'],
                    token='qr-numeric-bad1',
                ), db_path=str(path))
            c.rollback()
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)

            json_row = filled(legacyStockId='', legacyCustomerId='', legacyMakerId='', qr=QR)
            jblob = xlsx([json_row])
            jprev = ql.build_preview(c, body_of(jblob))
            self.assertEqual(jprev['rows'][0]['status'], 'exact')
            self.assertEqual(jprev['rows'][0]['legacy']['stockId'], OLD)
            ql.commit_preview(c, body_of(
                jblob, previewFingerprint=jprev['previewFingerprint'],
                token='qr-json-token01',
            ), db_path=str(path))
            c.commit()
            self.assertEqual(c.execute('SELECT legacy_maker_id FROM qr_stock_links').fetchone()['legacy_maker_id'], MAKER)
            self.assertEqual(snap(c), before)

            with self.assertRaises(ValueError):
                ql.build_preview(c, body_of(b'not-a-zip'))
            with self.assertRaises(ValueError):
                ql.parse_workbook(excel_book.workbook([('別', ['顧客'], [['A']])]))

            code, denied = stock_server.api_post(
                str(path), 'desk', '/api/qr-link/commit', {'file': b64(jblob)}, can_write=False,
            )
            self.assertEqual(code, 403)
            self.assertIn('閲覧専用', denied['error'])
            code, preview_ro = stock_server.api_post(
                str(path), 'desk', '/api/qr-link/preview', body_of(jblob), can_write=False,
            )
            self.assertEqual(code, 200)
            self.assertEqual(preview_ro['rows'][0]['status'], 'unchanged')

            fresh = xlsx([filled(legacyStockId=OLD + 'X')])
            # 新しいIDなので未登録。顧客を足してから確定すると指紋が違う。
            stale_preview = ql.build_preview(c, body_of(fresh))
            stock.save_customer(c, {'name': 'あとから追加', 'active': True})
            c.commit()
            with self.assertRaises(stock.StockConflict):
                ql.commit_preview(c, body_of(
                    fresh, previewFingerprint=stale_preview['previewFingerprint'],
                    token='qr-stale-token1',
                ), db_path=str(path))
            c.rollback()
            ids = {r['legacy_stock_id'] for r in c.execute('SELECT legacy_stock_id FROM qr_stock_links')}
            self.assertEqual(ids, {OLD})
            self.assertEqual(snap(c)['qty'], before['qty'])
            self.assertEqual(snap(c)['moves'], before['moves'])
        finally:
            c.close()
            folder.cleanup()

    def test_ui_and_limits_are_wired(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        js = (ROOT / 'static' / 'qr_link.js').read_text(encoding='utf-8')
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn('qrLinkOpen', app)
        self.assertIn('旧QR対応', app)
        self.assertIn('qr_link.js', html)
        self.assertIn('/api/qr-link/preview', js)
        self.assertIn('/api/qr-link/commit', js)
        self.assertIn('X-Stock-Request', (ROOT / 'static' / 'app.js').read_text(encoding='utf-8'))
        self.assertIn("'/api/qr-link/commit'", server)
        self.assertIn('/api/qr-link/preview', ql.__dict__ and server)
        import upload_limits as ul
        self.assertIn('/api/qr-link/preview', ul.LARGE_BODY_PATHS)
        self.assertIn('/api/qr-link/commit', ul.LARGE_BODY_PATHS)
        self.assertIn('/api/qr-link/commit', stock_server.WRITE_PATHS)
        src = (ROOT / 'qr_link_import.py').read_text(encoding='utf-8')
        self.assertNotIn('register_opening', src)
        self.assertNotIn('save_product', src)
        self.assertNotIn('register_outbound', src)


if __name__ == '__main__':
    unittest.main()
