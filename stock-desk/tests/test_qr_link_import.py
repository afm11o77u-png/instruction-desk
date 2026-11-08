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

    def test_name_difference_alone_is_exact(self):
        """旧「＊白衣」と今「白衣」。顧客・品番・サイズが一致し候補が1件なら完全一致。"""
        folder, path, c = newdb()
        try:
            seeded = seed_cap(c, code='AZ-100', name='白衣', sizes=['F'])
            before = snap(c)
            self.assertEqual(ql._name_match('＊白衣', '白衣'), 'diff')
            blob = xlsx([filled(
                legacyStockId=OLD + 'N',
                itemNum='AZ-100', itemName='＊白衣', itemSize='F',
            )])
            preview = ql.build_preview(c, body_of(blob))
            row = preview['rows'][0]
            self.assertEqual(len(row['candidates']), 1)
            cand = row['candidates'][0]
            self.assertEqual(cand['nameMatch'], 'diff')
            self.assertEqual(cand['codeMatch'], 'exact')
            self.assertEqual(cand['customerMatch'], 'exact')
            self.assertEqual(cand['sizeMatch'], 'exact')
            self.assertEqual(cand['supplierMatch'], 'exact')
            self.assertTrue(cand['auto'])
            self.assertIn('品名が違います', cand['matchNote'])
            self.assertEqual(row['status'], 'exact')
            self.assertTrue(row['includeDefault'])
            self.assertIn('品名が違っても完全一致', '\n'.join(ql.NOTES))
            result = ql.commit_preview(c, body_of(
                blob, previewFingerprint=preview['previewFingerprint'],
                token='qr-name-diff-exact1',
            ), db_path=str(path))
            c.commit()
            self.assertEqual(result['created'], 1)
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(snap(c), before)
            link = c.execute('SELECT * FROM qr_stock_links').fetchone()
            self.assertEqual(link['product_id'], seeded['product']['id'])
            self.assertEqual(link['item_name'], '＊白衣')
            self.assertEqual(link['item_num'], 'AZ-100')
            self.assertEqual(link['size_label'], 'F')
            self.assertEqual(link['match_kind'], 'exact')
        finally:
            c.close()
            folder.cleanup()

    def test_name_difference_does_not_relax_other_matches(self):
        folder, path, c = newdb()
        try:
            seed_cap(c, code='AZ8618003', name='白衣', sizes=['F'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='AZ-8618-003', itemName='＊白衣', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'review')
            self.assertFalse(row['includeDefault'])
            self.assertEqual(row['candidates'][0]['codeMatch'], 'normalized')
            self.assertEqual(row['candidates'][0]['nameMatch'], 'diff')
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            seed_cap(c, customer='株式会社北光', code='AZ-100', name='白衣', sizes=['F'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName='㈱北光', itemNum='AZ-100', itemName='＊白衣', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'review')
            self.assertFalse(row['includeDefault'])
            self.assertEqual(row['candidates'][0]['customerMatch'], 'form')
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            seed_cap(c, code='AZ-100', name='白衣', sizes=['F'])
            blank = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='AZ-100', itemName='＊白衣', itemSize='',
            )])))['rows'][0]
            self.assertEqual(blank['status'], 'review')
            self.assertFalse(blank['includeDefault'])
            self.assertEqual(blank['candidates'][0]['sizeMatch'], 'blank')
            wide = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='AZ-100', itemName='＊白衣', itemSize='Ｆ',
            )])))['rows'][0]
            self.assertEqual(wide['status'], 'review')
            self.assertFalse(wide['includeDefault'])
            self.assertEqual(wide['candidates'][0]['sizeMatch'], 'normalized')
            maker = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='AZ-100', itemName='＊白衣', itemSize='F', makerName='別のメーカー',
            )])))['rows'][0]
            self.assertEqual(maker['status'], 'review')
            self.assertFalse(maker['includeDefault'])
            self.assertEqual(maker['candidates'][0]['supplierMatch'], 'diff')
            self.assertEqual(len(maker['candidates']), 1)
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            seed_cap(c, code='AZ-8618-003', name='白衣', sizes=['F'])
            cu = c.execute("SELECT id FROM customers WHERE name='顧客A'").fetchone()['id']
            su = c.execute("SELECT id FROM suppliers WHERE name='メーカーA'").fetchone()['id']
            stock.save_product(c, {
                'code': 'AZ8618003', 'name': '白衣', 'color': '', 'supplierId': su,
                'active': True, 'sizes': ['F'],
                'links': [{'customerId': cu, 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='AZ-8618-003', itemName='＊白衣', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'ambiguous')
            self.assertFalse(row['includeDefault'])
            self.assertGreaterEqual(len(row['candidates']), 2)
            self.assertTrue(all(item['nameMatch'] == 'diff' for item in row['candidates']))
        finally:
            c.close()
            folder.cleanup()

    def _add_product(self, c, *, code, name, customer_id, supplier_id, sizes=None):
        pr = stock.save_product(c, {
            'code': code, 'name': name, 'color': '', 'supplierId': supplier_id,
            'active': True, 'sizes': sizes or ['F'],
            'links': [{'customerId': customer_id, 'embroidery': '', 'isStandard': True, 'active': True}],
        })
        c.commit()
        return pr

    def test_partial_code_with_matching_name_is_exact(self):
        """旧 NPC75082 / 白下（＊白下も）は、今の NPC75082下 / 白下 が1件なら完全一致。"""
        folder, path, c = newdb()
        try:
            seeded = seed_cap(c, code='NPC75082下', name='白下', sizes=['F'])
            before = snap(c)
            for legacy_name in ('白下', '＊白下'):
                blob = xlsx([filled(
                    legacyStockId=OLD + 'P' + legacy_name,
                    itemNum='NPC75082', itemName=legacy_name, itemSize='F',
                )])
                preview = ql.build_preview(c, body_of(blob))
                row = preview['rows'][0]
                self.assertEqual(len(row['candidates']), 1, legacy_name)
                cand = row['candidates'][0]
                self.assertEqual(cand['code'], 'NPC75082下')
                self.assertEqual(cand['codeMatch'], 'partial')
                self.assertEqual(cand['nameMatch'], 'exact')
                self.assertEqual(cand['customerMatch'], 'exact')
                self.assertEqual(cand['sizeMatch'], 'exact')
                self.assertTrue(cand['auto'])
                self.assertIn('品番が部分一致します', cand['matchNote'])
                self.assertNotIn('品名が違います', cand['matchNote'])
                self.assertEqual(row['status'], 'exact', legacy_name)
                self.assertTrue(row['includeDefault'])
            blob = xlsx([filled(
                legacyStockId=OLD + 'P',
                itemNum='NPC75082', itemName='＊白下', itemSize='F',
            )])
            preview = ql.build_preview(c, body_of(blob))
            result = ql.commit_preview(c, body_of(
                blob, previewFingerprint=preview['previewFingerprint'],
                token='qr-partial-npc-1',
            ), db_path=str(path))
            c.commit()
            self.assertEqual(result['created'], 1)
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(snap(c), before)
            link = c.execute('SELECT * FROM qr_stock_links').fetchone()
            self.assertEqual(link['product_id'], seeded['product']['id'])
            self.assertEqual(link['item_num'], 'NPC75082')
            self.assertEqual(link['match_kind'], 'exact')
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], before['products'])
        finally:
            c.close()
            folder.cleanup()

    def test_partial_code_name_selects_down_not_up(self):
        folder, path, c = newdb()
        try:
            seeded = seed_cap(c, code='NPC75082下', name='白下', sizes=['F'])
            self._add_product(
                c, code='NPC75082上', name='白上',
                customer_id=seeded['customer']['id'], supplier_id=seeded['supplier']['id'],
            )
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='NPC75082', itemName='＊白下', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'exact')
            self.assertTrue(row['includeDefault'])
            self.assertEqual([item['code'] for item in row['candidates']], ['NPC75082下'])
        finally:
            c.close()
            folder.cleanup()

    def test_partial_code_same_name_stays_ambiguous(self):
        folder, path, c = newdb()
        try:
            seeded = seed_cap(c, code='NPC75082下', name='白下', sizes=['F'])
            self._add_product(
                c, code='NPC75082上', name='白下',
                customer_id=seeded['customer']['id'], supplier_id=seeded['supplier']['id'],
            )
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='NPC75082', itemName='白下', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'ambiguous')
            self.assertFalse(row['includeDefault'])
            self.assertEqual({item['code'] for item in row['candidates']}, {'NPC75082下', 'NPC75082上'})
            self.assertGreaterEqual(len(row['candidates']), 2)
        finally:
            c.close()
            folder.cleanup()

    def test_partial_code_name_mismatch_is_not_exact(self):
        folder, path, c = newdb()
        try:
            seed_cap(c, code='NPC75082下', name='白下', sizes=['F'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='NPC75082', itemName='白上', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(len(row['candidates']), 1)
            self.assertEqual(row['candidates'][0]['nameMatch'], 'diff')
            self.assertFalse(row['candidates'][0]['auto'])
            self.assertEqual(row['status'], 'review')
            self.assertFalse(row['includeDefault'])
            self.assertIn('品番が部分一致します', row['candidates'][0]['matchNote'])
        finally:
            c.close()
            folder.cleanup()

    def test_betchu_partial_code_is_exact_when_name_matches(self):
        folder, path, c = newdb()
        try:
            seeded = seed_cap(c, code='別注(顧客A)', name='白衣', sizes=['F'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='別注', itemName='＊白衣', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'exact')
            self.assertTrue(row['includeDefault'])
            self.assertEqual(row['candidates'][0]['code'], '別注(顧客A)')
            self.assertEqual(row['candidates'][0]['codeMatch'], 'partial')
            self.assertEqual(row['candidates'][0]['nameMatch'], 'exact')
            self._add_product(
                c, code='別注(顧客B)', name='白衣',
                customer_id=seeded['customer']['id'], supplier_id=seeded['supplier']['id'],
            )
            both = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='別注', itemName='白衣', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(both['status'], 'ambiguous')
            self.assertFalse(both['includeDefault'])
            self.assertEqual({item['code'] for item in both['candidates']}, {'別注(顧客A)', '別注(顧客B)'})
        finally:
            c.close()
            folder.cleanup()

    def test_partial_code_does_not_relax_other_rules_or_branch_digits(self):
        folder, path, c = newdb()
        try:
            seeded = seed_cap(c, code='NPC75082下', name='白下', sizes=['F'])
            blank = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='NPC75082', itemName='白下', itemSize='',
            )])))['rows'][0]
            self.assertEqual(blank['status'], 'review')
            self.assertFalse(blank['includeDefault'])
            self.assertEqual(blank['candidates'][0]['sizeMatch'], 'blank')
            wide = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='NPC75082', itemName='白下', itemSize='Ｆ',
            )])))['rows'][0]
            self.assertEqual(wide['status'], 'review')
            self.assertEqual(wide['candidates'][0]['sizeMatch'], 'normalized')
            maker = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='NPC75082', itemName='白下', itemSize='F', makerName='別のメーカー',
            )])))['rows'][0]
            self.assertEqual(maker['status'], 'review')
            self.assertEqual(maker['candidates'][0]['supplierMatch'], 'diff')
            self.assertFalse(maker['includeDefault'])
            self._add_product(
                c, code='NPC75082', name='白衣',
                customer_id=seeded['customer']['id'], supplier_id=seeded['supplier']['id'],
            )
            exact = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='NPC75082', itemName='＊白衣', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(exact['status'], 'exact')
            self.assertEqual([item['code'] for item in exact['candidates']], ['NPC75082'])
            self.assertEqual(exact['candidates'][0]['codeMatch'], 'exact')
            self.assertEqual(exact['candidates'][0]['nameMatch'], 'diff')
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            seed_cap(c, customer='株式会社北光', code='NPC75082下', name='白下', sizes=['F'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName='㈱北光', itemNum='NPC75082', itemName='白下', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'review')
            self.assertEqual(row['candidates'][0]['customerMatch'], 'form')
            self.assertFalse(row['includeDefault'])
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            seed_cap(c, code='AZ-8618-003', name='ワークキャップ', sizes=['F'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='AZ-8618', itemName='ワークキャップ', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'unmatched')
            self.assertFalse(row['candidates'])
            short = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='AZ', itemName='ワークキャップ', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(short['status'], 'unmatched')
            self.assertFalse(short['candidates'])
        finally:
            c.close()
            folder.cleanup()

    def _seed_handles(self, c, *, code, name, handles, sizes=None, customer='ミルクの郷'):
        cu = stock.save_customer(c, {'name': customer, 'active': True})
        su = stock.save_supplier(c, {'name': 'メーカーA', 'active': True})
        stock.save_location(c, {'siteName': '本社', 'shelfName': 'A-1', 'active': True})
        links = []
        for emb in handles:
            links.append({
                'customerId': cu['id'],
                'embroidery': emb,
                'isStandard': emb == '',
                'active': True,
            })
        pr = stock.save_product(c, {
            'code': code, 'name': name, 'color': '', 'supplierId': su['id'],
            'active': True, 'sizes': sizes or ['5L'],
            'links': links,
        })
        c.commit()
        return {'customer': cu, 'supplier': su, 'product': pr}

    def test_processing_paren_selects_matching_handle(self):
        """＊白衣(スナッパー加工) はスナッパー加工の取扱だけ。白衣だけなら加工なし。"""
        folder, path, c = newdb()
        try:
            self._seed_handles(c, code='DCP514', name='白衣', handles=['', 'スナッパー加工'])
            before = snap(c)
            for legacy_name in ('＊白衣(スナッパー加工)', '＊白衣（スナッパー加工）', '白衣(スナッパー加工)'):
                row = ql.build_preview(c, body_of(xlsx([filled(
                    legacyStockId=OLD + 'S',
                    customerName='ミルクの郷', itemNum='DCP514', itemName=legacy_name,
                    itemSize='5L', isUsed='中古',
                )])))['rows'][0]
                self.assertEqual(row['status'], 'exact', legacy_name)
                self.assertTrue(row['includeDefault'], legacy_name)
                self.assertEqual(len(row['candidates']), 1, legacy_name)
                cand = row['candidates'][0]
                self.assertEqual(cand['embroidery'], 'スナッパー加工')
                self.assertIn('スナッパー加工', cand['label'])
                self.assertEqual(cand['code'], 'DCP514')
                self.assertEqual(cand['size'], '5L')
                self.assertEqual(cand['customerName'], 'ミルクの郷')
            plain = ql.build_preview(c, body_of(xlsx([filled(
                legacyStockId=OLD + 'N',
                customerName='ミルクの郷', itemNum='DCP514', itemName='白衣',
                itemSize='5L', isUsed='中古',
            )])))['rows'][0]
            self.assertEqual(plain['status'], 'exact')
            self.assertTrue(plain['includeDefault'])
            self.assertEqual(len(plain['candidates']), 1)
            self.assertEqual(plain['candidates'][0]['embroidery'], '加工なし')
            self.assertIn('加工なし', plain['candidates'][0]['label'])
            starred = ql.build_preview(c, body_of(xlsx([filled(
                legacyStockId=OLD + 'NS',
                customerName='ミルクの郷', itemNum='DCP514', itemName='＊白衣',
                itemSize='5L', isUsed='中古',
            )])))['rows'][0]
            self.assertEqual(starred['status'], 'exact')
            self.assertEqual(starred['candidates'][0]['embroidery'], '加工なし')
            blob = xlsx([filled(
                legacyStockId=OLD + 'C',
                customerName='ミルクの郷', itemNum='DCP514', itemName='＊白衣(スナッパー加工)',
                itemSize='5L', isUsed='中古',
            )])
            preview = ql.build_preview(c, body_of(blob))
            result = ql.commit_preview(c, body_of(
                blob, previewFingerprint=preview['previewFingerprint'],
                token='qr-proc-snap-1',
            ), db_path=str(path))
            c.commit()
            self.assertEqual(result['created'], 1)
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(snap(c), before)
            link = c.execute('SELECT customer_product_id FROM qr_stock_links').fetchone()
            emb = c.execute(
                'SELECT embroidery FROM customer_products WHERE id=?',
                (link['customer_product_id'],),
            ).fetchone()['embroidery']
            self.assertEqual(emb, 'スナッパー加工')
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], before['products'])
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM customer_products').fetchone()['n'], before['handles'])
        finally:
            c.close()
            folder.cleanup()

    def test_missing_processing_handle_falls_back_to_plain(self):
        folder, path, c = newdb()
        try:
            self._seed_handles(c, code='DCP843', name='女白下', handles=['', '裾上げ'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName='ミルクの郷', itemNum='DCP843', itemName='女白下(スナッパー加工)',
                itemSize='5L', isUsed='中古',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'exact')
            self.assertTrue(row['includeDefault'])
            self.assertEqual(len(row['candidates']), 1)
            self.assertEqual(row['candidates'][0]['embroidery'], '加工なし')
            self.assertIn('加工なし', row['candidates'][0]['label'])
            bare = ql.build_preview(c, body_of(xlsx([filled(
                customerName='ミルクの郷', itemNum='DCP843', itemName='女白下',
                itemSize='5L', isUsed='新品',
            )])))['rows'][0]
            self.assertEqual(bare['status'], 'exact')
            self.assertEqual(bare['candidates'][0]['embroidery'], '加工なし')
        finally:
            c.close()
            folder.cleanup()

    def test_unresolved_processing_stays_ambiguous_and_labels_differ(self):
        folder, path, c = newdb()
        try:
            self._seed_handles(c, code='DCP514', name='白衣', handles=['スナッパー加工', '裾上げ'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName='ミルクの郷', itemNum='DCP514', itemName='白衣',
                itemSize='5L', isUsed='中古',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'ambiguous')
            self.assertFalse(row['includeDefault'])
            labels = {item['embroidery']: item['label'] for item in row['candidates']}
            self.assertEqual(set(labels), {'スナッパー加工', '裾上げ'})
            self.assertIn('スナッパー加工', labels['スナッパー加工'])
            self.assertIn('裾上げ', labels['裾上げ'])
            self.assertNotEqual(labels['スナッパー加工'], labels['裾上げ'])
        finally:
            c.close()
            folder.cleanup()

    def test_paren_containing_processing_name_selects_that_handle(self):
        """括弧に色などが混ざっていても、中に加工名があればその取扱を選ぶ。"""
        folder, path, c = newdb()
        try:
            self._seed_handles(
                c, code='73-1424 (LEOC刺繍)', name='ナースコート',
                handles=['', 'LEOC刺繍'], customer='顧客A',
            )
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='73-1424 (LEOC刺繍)',
                itemName=' ナースコート(白/ｻｯｸｽ・LEOC刺繍)',
                itemSize='5L',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'exact')
            self.assertTrue(row['includeDefault'])
            self.assertEqual(len(row['candidates']), 1)
            self.assertEqual(row['candidates'][0]['embroidery'], 'LEOC刺繍')
            self.assertEqual(row['candidates'][0]['codeMatch'], 'exact')
            self.assertIn('LEOC刺繍', row['candidates'][0]['label'])
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            customer = '㈱ＬＥＯＣ'
            self._seed_handles(
                c, code='OV5003-2', name='サロンエプロン',
                handles=['', '刺繍入り'], customer=customer,
            )
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, itemNum='OV5003-2',
                itemName=' サロンエプロン (白・刺繍入り)',
                itemSize='5L',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'exact')
            self.assertTrue(row['includeDefault'])
            self.assertEqual(len(row['candidates']), 1)
            self.assertEqual(row['candidates'][0]['embroidery'], '刺繍入り')
            self.assertEqual(row['candidates'][0]['customerName'], customer)
        finally:
            c.close()
            folder.cleanup()

    def test_processing_containment_does_not_reverse_or_overmatch(self):
        folder, path, c = newdb()
        try:
            self._seed_handles(c, code='DCP514', name='白衣', handles=['', 'LEOC刺繍'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName='ミルクの郷', itemNum='DCP514',
                itemName='白衣(刺繍入り)', itemSize='5L',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'exact')
            self.assertEqual(row['candidates'][0]['embroidery'], '加工なし')
            short = ql.build_preview(c, body_of(xlsx([filled(
                customerName='ミルクの郷', itemNum='DCP514',
                itemName='白衣(白・刺繍入り)', itemSize='5L',
            )])))['rows'][0]
            self.assertEqual(short['candidates'][0]['embroidery'], '加工なし')
            self.assertNotEqual(short['candidates'][0]['embroidery'], 'LEOC刺繍')
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            self._seed_handles(c, code='DCP514', name='白衣', handles=['', '刺繍'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName='ミルクの郷', itemNum='DCP514',
                itemName='白衣(白・刺繍入り)', itemSize='5L',
            )])))['rows'][0]
            self.assertEqual(row['candidates'][0]['embroidery'], '加工なし')
            self.assertNotEqual(row['candidates'][0]['embroidery'], '刺繍')
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            self._seed_handles(
                c, code='DCP514', name='白衣',
                handles=['', '刺繍入り', 'LEOC刺繍'],
            )
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName='ミルクの郷', itemNum='DCP514',
                itemName='白衣(白・LEOC刺繍・刺繍入り)', itemSize='5L',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'ambiguous')
            self.assertFalse(row['includeDefault'])
            self.assertEqual(
                {item['embroidery'] for item in row['candidates']},
                {'刺繍入り', 'LEOC刺繍'},
            )
        finally:
            c.close()
            folder.cleanup()

    def test_two_products_stay_ambiguous_when_paren_is_not_processing(self):
        folder, path, c = newdb()
        try:
            seeded = self._seed_handles(
                c, code='EM-710', name='エプロン', handles=[''],
                customer='顧客A', sizes=['F'],
            )
            self._add_product(
                c, code='EM710', name='胸あてエプロン',
                customer_id=seeded['customer']['id'], supplier_id=seeded['supplier']['id'],
                sizes=['F'],
            )
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='EM-710', itemName='エプロン(ﾍｯｰｼｮ・EM-710)', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'ambiguous')
            self.assertFalse(row['includeDefault'])
            self.assertEqual({item['code'] for item in row['candidates']}, {'EM-710', 'EM710'})
        finally:
            c.close()
            folder.cleanup()

    def _seed_same_code(self, c, *, code, names, customer, sizes=None):
        cu = stock.save_customer(c, {'name': customer, 'active': True})
        su = stock.save_supplier(c, {'name': 'メーカーA', 'active': True})
        stock.save_location(c, {'siteName': '本社', 'shelfName': 'A-1', 'active': True})
        products = []
        for name in names:
            products.append(stock.save_product(c, {
                'code': code, 'name': name, 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': sizes or ['F'],
                '_allowHistoricalDuplicateCode': True,
                'links': [{
                    'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True,
                }],
            }))
        c.commit()
        return {'customer': cu, 'supplier': su, 'products': products}

    def test_current_name_contained_in_legacy_name_is_exact(self):
        """別注の複数商品から、旧品名に文字列全体が含まれるブルーエプロンだけを完全一致にする。"""
        folder, path, c = newdb()
        try:
            customer = '⒈ＬＥＯＣ'
            code = '別注(⒈ＬＥＯＣ)'
            names = ['ネイビーエプロン', 'ピンクエプロン', 'ブルーエプロン', 'エプロン（胸当）', '衛生帽子(三角帽子)']
            self._seed_same_code(c, code=code, names=names, customer=customer)
            before = snap(c)
            blob = xlsx([filled(
                legacyStockId=OLD + 'B',
                customerName=customer, makerName='メーカーA',
                itemNum=' 別注 ', itemName=' ブルーエプロン (胸当・ｻｯｸｽ) ',
                itemSize='F',
            )])
            row = ql.build_preview(c, body_of(blob))['rows'][0]
            self.assertEqual(len(row['candidates']), 1)
            cand = row['candidates'][0]
            self.assertEqual(cand['productName'], 'ブルーエプロン')
            self.assertEqual(cand['code'], code)
            self.assertEqual(cand['codeMatch'], 'partial')
            self.assertEqual(cand['nameMatch'], 'contained')
            self.assertEqual(cand['customerMatch'], 'exact')
            self.assertEqual(cand['sizeMatch'], 'exact')
            self.assertEqual(cand['supplierMatch'], 'exact')
            self.assertNotIn('エプロン（胸当）', cand['productName'])
            self.assertEqual(row['status'], 'exact')
            self.assertTrue(row['includeDefault'])
            self.assertIn('品名が含まれます', cand['matchNote'])
            self.assertIn('旧：ブルーエプロン (胸当・ｻｯｸｽ)', cand['matchNote'])
            self.assertIn('今：ブルーエプロン', cand['matchNote'])
            self.assertNotIn('品名が含まれます', '\n'.join(row['reasons']))
            preview = ql.build_preview(c, body_of(blob))
            result = ql.commit_preview(c, body_of(
                blob, previewFingerprint=preview['previewFingerprint'],
                token='qr-name-contained-1',
            ), db_path=str(path))
            c.commit()
            self.assertEqual(result['created'], 1)
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(snap(c), before)
            link = c.execute(
                'SELECT p.name FROM qr_stock_links q JOIN products p ON p.id=q.product_id'
            ).fetchone()
            self.assertEqual(link['name'], 'ブルーエプロン')
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], before['products'])
        finally:
            c.close()
            folder.cleanup()

    def test_name_containment_keeps_multi_and_skips_supplier_mismatch(self):
        folder, path, c = newdb()
        try:
            customer = '⒈ＬＥＯＣ'
            code = '別注(⒈ＬＥＯＣ)'
            self._seed_same_code(
                c, code=code, customer=customer,
                names=['ネイビーエプロン', 'ブルーエプロン', 'エプロン（胸当）'],
            )
            both = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, itemNum='別注',
                itemName='ネイビーエプロンとブルーエプロン', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(both['status'], 'ambiguous')
            self.assertFalse(both['includeDefault'])
            self.assertEqual(
                {item['productName'] for item in both['candidates']},
                {'ネイビーエプロン', 'ブルーエプロン'},
            )
            none = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, itemNum='別注',
                itemName='存在しない商品', itemSize='F',
            )])))['rows'][0]
            self.assertNotEqual(none['status'], 'exact')
            self.assertFalse(none['includeDefault'])
            self.assertGreaterEqual(len(none['candidates']), 2)
            self.assertTrue(all(item['nameMatch'] == 'diff' for item in none['candidates']))
            maker = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='別のメーカー', itemNum='別注',
                itemName='ブルーエプロン (胸当・サックス)', itemSize='F',
            )])))['rows'][0]
            self.assertEqual(len(maker['candidates']), 1)
            self.assertEqual(maker['candidates'][0]['productName'], 'ブルーエプロン')
            self.assertEqual(maker['candidates'][0]['supplierMatch'], 'diff')
            self.assertEqual(maker['status'], 'review')
            self.assertFalse(maker['includeDefault'])
            note = maker['candidates'][0]['matchNote']
            self.assertIn('メーカーが違います（旧：別のメーカー / 今：メーカーA）', note)
            self.assertIn('品名が含まれます（旧：ブルーエプロン (胸当・サックス) / 今：ブルーエプロン）', note)
            self.assertIn(note, maker['reasons'])
            self.assertNotIn('または未指定', note)
            self.assertNotIn('表記ゆれ、または未指定の項目があります', '\n'.join(maker['reasons']))
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            seed_cap(c, code='AZ-100', name='白衣', sizes=['F'])
            row = ql.build_preview(c, body_of(xlsx([filled(
                itemNum='AZ-100', itemName='白衣', itemSize='', makerName='',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'review')
            note = row['candidates'][0]['matchNote']
            self.assertIn('サイズは空欄です（旧：未指定）', note)
            self.assertIn('メーカーが未指定です（旧：未指定 / 今：メーカーA）', note)
            self.assertNotIn('または未指定です', note)
            self.assertIn('メーカーが未指定です（旧：未指定 / 今：メーカーA）', '\n'.join(row['reasons']))
        finally:
            c.close()
            folder.cleanup()

    def _seed_color_siblings(self, c, *, customer, code, rows):
        cu = stock.save_customer(c, {'name': customer, 'active': True})
        suppliers = {}
        for _name, supplier_name, _color in rows:
            if supplier_name not in suppliers:
                suppliers[supplier_name] = stock.save_supplier(
                    c, {'name': supplier_name, 'active': True},
                )
        stock.save_location(c, {'siteName': '本社', 'shelfName': 'A-1', 'active': True})
        products = []
        for name, supplier_name, color in rows:
            products.append(stock.save_product(c, {
                'code': code, 'name': name, 'color': color, 'supplierId': suppliers[supplier_name]['id'],
                'active': True, 'sizes': ['S', 'M', 'L', 'LL', '3L'],
                '_allowHistoricalDuplicateCode': True,
                'links': [{
                    'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True,
                }],
            }))
        c.commit()
        return products

    def test_supplier_and_color_select_chest_apron(self):
        """ユニスタイルと色ブルー・サックスで、エプロン（胸当）だけを完全一致にする。"""
        folder, path, c = newdb()
        try:
            customer = '顧客L'
            code = '別注(顧客L)'
            self._seed_color_siblings(c, customer=customer, code=code, rows=[
                ('ブルーエプロン', '熊谷縫製', 'ブルー'),
                ('エプロン（胸当）', '(有)ユニスタイル', 'ブルー・サックス'),
                ('ピンクエプロン (胸当・ﾌﾞﾓｸ)', '(有)ユニスタイル', 'ピンク'),
            ])
            before = snap(c)
            blob = xlsx([filled(
                legacyStockId=OLD + 'SC',
                customerName=customer, makerName='(有)ユニスタイル',
                itemNum='別注', itemName='ブルーエプロン (胸当・ｻｯｸｽ)',
                itemSize='L',
            )])
            row = ql.build_preview(c, body_of(blob))['rows'][0]
            self.assertEqual(len(row['candidates']), 1)
            cand = row['candidates'][0]
            self.assertEqual(cand['productName'], 'エプロン（胸当）')
            self.assertEqual(cand['color'], 'ブルー・サックス')
            self.assertEqual(cand['supplierName'], '(有)ユニスタイル')
            self.assertEqual(cand['nameMatch'], 'supplier_color')
            self.assertEqual(cand['codeMatch'], 'partial')
            self.assertEqual(cand['supplierMatch'], 'exact')
            self.assertEqual(row['status'], 'exact')
            self.assertTrue(row['includeDefault'])
            self.assertNotIn('ピンク', cand['productName'])
            self.assertIn('仕入先と色が一致します', cand['matchNote'])
            preview = ql.build_preview(c, body_of(blob))
            result = ql.commit_preview(c, body_of(
                blob, previewFingerprint=preview['previewFingerprint'],
                token='qr-supplier-color-1',
            ), db_path=str(path))
            c.commit()
            self.assertEqual(result['created'], 1)
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(snap(c), before)
        finally:
            c.close()
            folder.cleanup()

    def test_supplier_color_does_not_match_without_color_or_supplier(self):
        folder, path, c = newdb()
        try:
            customer = '顧客L'
            code = '別注(顧客L)'
            self._seed_color_siblings(c, customer=customer, code=code, rows=[
                ('ブルーエプロン', '熊谷縫製', 'ブルー'),
                ('エプロン（胸当）', '(有)ユニスタイル', ''),
                ('ピンクエプロン (胸当・ﾌﾞﾓｸ)', '(有)ユニスタイル', 'ピンク'),
            ])
            blank_color = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='(有)ユニスタイル',
                itemNum='別注', itemName='ブルーエプロン (胸当・サックス)',
                itemSize='L',
            )])))['rows'][0]
            self.assertEqual(blank_color['status'], 'review')
            self.assertFalse(blank_color['includeDefault'])
            self.assertEqual(blank_color['candidates'][0]['productName'], 'ブルーエプロン')
            self.assertEqual(blank_color['candidates'][0]['supplierMatch'], 'diff')
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            customer = '顧客L'
            code = '別注(顧客L)'
            self._seed_color_siblings(c, customer=customer, code=code, rows=[
                ('ブルーエプロン', '熊谷縫製', 'ブルー'),
                ('エプロン（胸当）', '(有)ユニスタイル', 'ブルー・サックス'),
                ('ピンクエプロン (胸当・ﾌﾞﾓｸ)', '(有)ユニスタイル', 'ピンク'),
            ])
            missed = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='別の縫製',
                itemNum='別注', itemName='ブルーエプロン (胸当・サックス)',
                itemSize='L',
            )])))['rows'][0]
            self.assertEqual(missed['status'], 'review')
            self.assertFalse(missed['includeDefault'])
            self.assertEqual(missed['candidates'][0]['productName'], 'ブルーエプロン')
            both = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='(有)ユニスタイル',
                itemNum='別注', itemName='ブルーエプロン (胸当・サックス・ピンク)',
                itemSize='L',
            )])))['rows'][0]
            self.assertEqual(both['status'], 'ambiguous')
            self.assertFalse(both['includeDefault'])
            self.assertEqual(
                {item['productName'] for item in both['candidates']},
                {'エプロン（胸当）', 'ピンクエプロン (胸当・ﾌﾞﾓｸ)'},
            )
        finally:
            c.close()
            folder.cleanup()

    def _seed_hats(self, c, specs, *, customer='顧客H', code='別注(顧客H)', supplier='メーカーA'):
        cu = stock.save_customer(c, {'name': customer, 'active': True})
        su = stock.save_supplier(c, {'name': supplier, 'active': True})
        stock.save_location(c, {'siteName': '本社', 'shelfName': 'A-1', 'active': True})
        products = []
        for name, color, sizes in specs:
            products.append(stock.save_product(c, {
                'code': code, 'name': name, 'color': color, 'supplierId': su['id'],
                'active': True, 'sizes': sizes,
                '_allowHistoricalDuplicateCode': True,
                'links': [{
                    'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True,
                }],
            }))
        c.commit()
        return {'customer': cu, 'supplier': su, 'products': products}

    def test_clean_and_semi_clean_hats_are_not_the_same(self):
        """清浄帽子と準清浄帽子は、品名の含有では結ばない。"""
        folder, path, c = newdb()
        try:
            customer = '顧客H'
            self._seed_hats(c, [
                ('清浄帽子 緑', '緑', ['L']),
                ('準清浄帽子 緑', '緑', ['L']),
            ], customer=customer)
            jun = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='別注',
                itemName='＊緑準清浄帽子', itemSize='L',
            )])))['rows'][0]
            self.assertEqual(jun['status'], 'exact')
            self.assertTrue(jun['includeDefault'])
            self.assertEqual([item['productName'] for item in jun['candidates']], ['準清浄帽子 緑'])
            self.assertEqual(jun['candidates'][0]['nameMatch'], 'hat')
            self.assertIn('帽子の種類と色が一致します', jun['candidates'][0]['matchNote'])
            self.assertIn('＊緑準清浄帽子', jun['candidates'][0]['matchNote'])
            sei = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='別注',
                itemName='＊緑清浄帽子', itemSize='L',
            )])))['rows'][0]
            self.assertEqual(sei['status'], 'exact')
            self.assertEqual([item['productName'] for item in sei['candidates']], ['清浄帽子 緑'])
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            customer = '顧客H'
            self._seed_hats(c, [('清浄帽子 緑', '緑', ['L'])], customer=customer)
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='別注',
                itemName='＊緑準清浄帽子', itemSize='L',
            )])))['rows'][0]
            self.assertNotEqual(row['status'], 'exact')
            self.assertFalse(any(item['productName'] == '清浄帽子 緑' for item in row['candidates']))
            self.assertTrue(any('清浄帽子と準清浄帽子は別商品' in str(r) for r in row['reasons']))
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            customer = '顧客H'
            self._seed_hats(c, [('準清浄帽子', 'グリーン', ['L'])], customer=customer, code='HAT1')
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='HAT1',
                itemName='緑清浄帽子', itemSize='L',
            )])))['rows'][0]
            self.assertNotEqual(row['status'], 'exact')
            self.assertFalse(row['candidates'])
        finally:
            c.close()
            folder.cleanup()

    def test_jun_hat_green_synonym_respects_other_conditions(self):
        folder, path, c = newdb()
        try:
            customer = '顧客H'
            self._seed_hats(c, [('準清浄帽子', 'グリーン', ['L'])], customer=customer)
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='別注',
                itemName='＊緑準清浄帽子', itemSize='L', isUsed='中古',
            )])))['rows'][0]
            self.assertEqual(row['status'], 'exact')
            self.assertEqual(row['candidates'][0]['productName'], '準清浄帽子')
            self.assertEqual(row['candidates'][0]['condition'], 'used')
            self.assertEqual(row['legacy']['condition'], 'used')
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            customer = '顧客H'
            self._seed_hats(c, [
                ('準清浄帽子', 'グリーン', ['L']),
                ('準清浄帽子 白', '白', ['L']),
            ], customer=customer)
            white = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='別注',
                itemName='＊緑準清浄帽子', itemSize='L',
            )])))['rows'][0]
            self.assertEqual([item['productName'] for item in white['candidates']], ['準清浄帽子'])
            self.assertNotIn('準清浄帽子 白', [item['productName'] for item in white['candidates']])
            other_size = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='別注',
                itemName='＊緑準清浄帽子', itemSize='M',
            )])))['rows'][0]
            self.assertNotEqual(other_size['status'], 'exact')
            other_customer = ql.build_preview(c, body_of(xlsx([filled(
                customerName='他の顧客', makerName='メーカーA', itemNum='別注',
                itemName='＊緑準清浄帽子', itemSize='L',
            )])))['rows'][0]
            self.assertNotEqual(other_customer['status'], 'exact')
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            customer = '顧客H'
            cu = stock.save_customer(c, {'name': customer, 'active': True})
            stock.save_customer(c, {'name': '他の顧客', 'active': True})
            stock.save_supplier(c, {'name': 'メーカーA', 'active': True})
            other = stock.save_supplier(c, {'name': '別の縫製', 'active': True})
            stock.save_location(c, {'siteName': '本社', 'shelfName': 'A-1', 'active': True})
            stock.save_product(c, {
                'code': '別注(顧客H)', 'name': '準清浄帽子', 'color': 'グリーン',
                'supplierId': other['id'], 'active': True, 'sizes': ['L'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            wrong_supplier = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='別注',
                itemName='＊緑準清浄帽子', itemSize='L',
            )])))['rows'][0]
            self.assertEqual(wrong_supplier['status'], 'review')
            self.assertFalse(wrong_supplier['includeDefault'])
            self.assertEqual(wrong_supplier['candidates'][0]['supplierMatch'], 'diff')
            self.assertIn('メーカーが違います', wrong_supplier['candidates'][0]['matchNote'])
        finally:
            c.close()
            folder.cleanup()

        folder, path, c = newdb()
        try:
            customer = '顧客H'
            self._seed_hats(c, [
                ('準清浄帽子', 'グリーン', ['L']),
                ('準清浄帽子 緑', '緑', ['L']),
            ], customer=customer)
            many = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='別注',
                itemName='＊緑準清浄帽子', itemSize='L',
            )])))['rows'][0]
            self.assertEqual(many['status'], 'ambiguous')
            self.assertFalse(many['includeDefault'])
            self.assertEqual(len(many['candidates']), 2)
            blob = xlsx([
                filled(legacyStockId=OLD + 'N', customerName=customer, makerName='メーカーA',
                       itemNum='別注', itemName='＊緑準清浄帽子', itemSize='L', isUsed='新品'),
                filled(legacyStockId=OLD + 'U', customerName=customer, makerName='メーカーA',
                       itemNum='別注', itemName='＊緑準清浄帽子', itemSize='L', isUsed='中古'),
            ])
            # 候補が複数のままなので、このファイルは自動確定しない。数量もまとめていない。
            preview = ql.build_preview(c, body_of(blob))
            self.assertEqual(len(preview['rows']), 2)
            self.assertTrue(all(not row['includeDefault'] for row in preview['rows']))
            self.assertEqual(
                {row['legacy']['condition'] for row in preview['rows']},
                {'new', 'used'},
            )
        finally:
            c.close()
            folder.cleanup()

    def test_green_synonym_is_not_applied_to_other_products(self):
        folder, path, c = newdb()
        try:
            customer = '顧客H'
            cu = stock.save_customer(c, {'name': customer, 'active': True})
            su = stock.save_supplier(c, {'name': 'メーカーA', 'active': True})
            stock.save_location(c, {'siteName': '本社', 'shelfName': 'A-1', 'active': True})
            stock.save_product(c, {
                'code': '別注(顧客H)', 'name': '作業衣', 'color': 'グリーン',
                'supplierId': su['id'], 'active': True, 'sizes': ['L'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            row = ql.build_preview(c, body_of(xlsx([filled(
                customerName=customer, makerName='メーカーA', itemNum='別注',
                itemName='緑の服', itemSize='L',
            )])))['rows'][0]
            self.assertNotEqual(row['status'], 'exact')
            self.assertFalse(row['includeDefault'])
            self.assertNotEqual(row['candidates'][0]['nameMatch'], 'supplier_color')
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


class QrLinkNonUniformScopeTest(unittest.TestCase):
    def _product(self, c, *, code, name, category, customer='顧客A'):
        cu = c.execute('SELECT id FROM customers WHERE name=?', (customer,)).fetchone()
        if not cu:
            cu = stock.save_customer(c, {'name': customer, 'active': True})
            cu_id = cu['id']
        else:
            cu_id = cu['id']
        su = c.execute('SELECT id FROM suppliers').fetchone()
        if not su:
            su = stock.save_supplier(c, {'name': 'メーカーA', 'active': True})
            su_id = su['id']
        else:
            su_id = su['id']
        pr = stock.save_product(c, {
            'code': code, 'name': name, 'color': '', 'category': category,
            'supplierId': su_id, 'active': True, 'sizes': ['F'],
            'links': [{'customerId': cu_id, 'embroidery': '', 'isStandard': True, 'active': True}],
        })
        c.commit()
        return pr

    def test_parts_and_processing_are_out_of_scope_not_unmatched(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            self._product(c, code='BTN-18', name='ボタン 18mm', category=stock.CATEGORY_PARTS)
            self._product(c, code='', name='身頃刺繍', category=stock.CATEGORY_PROCESS_SERVICE)
            before = snap(c)
            blob = xlsx([
                filled(legacyStockId=OLD + 'P', itemNum='BTN-18', itemName='ボタン 18mm'),
                filled(legacyStockId=OLD + 'S', itemNum='', itemName='身頃刺繍', itemSize=''),
                filled(legacyStockId=OLD + 'U', itemNum='', itemName='襟上げテープ', itemSize=''),
                filled(),
            ])
            preview = ql.build_preview(c, body_of(blob))
            by_id = {row['legacy']['stockId']: row for row in preview['rows']}
            parts = by_id[OLD + 'P']
            service = by_id[OLD + 'S']
            unknown = by_id[OLD + 'U']
            uniform = by_id[OLD]
            self.assertEqual(parts['status'], 'out_of_scope')
            self.assertFalse(parts['canCommit'])
            self.assertEqual(parts['candidates'], [])
            self.assertIn('対象外', parts['reasons'][0])
            self.assertEqual(service['status'], 'out_of_scope')
            self.assertNotEqual(service['status'], 'unmatched')
            self.assertEqual(unknown['status'], 'unmatched')
            self.assertEqual(unknown['scopeHint'], 'unknown_blank')
            self.assertIn('品番が空です', unknown['reasons'][0])
            self.assertEqual(uniform['status'], 'exact')
            self.assertEqual(preview['countsRaw']['out_of_scope'], 2)
            self.assertEqual(preview['countsRaw']['unmatched'], 1)
            self.assertEqual(preview['countsRaw']['exact'], 1)
            with self.assertRaises(ValueError) as caught:
                ql.commit_preview(c, body_of(
                    blob, previewFingerprint=preview['previewFingerprint'],
                    token='qr-bypass-parts-1',
                    selections={str(parts['rowNo']): {'include': True}},
                ), db_path=str(path))
            self.assertIn('対象外', str(caught.exception))
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
            self.assertEqual(snap(c), before)
            result = ql.commit_preview(c, body_of(
                blob, previewFingerprint=preview['previewFingerprint'],
                token='qr-uniform-keep-1',
            ), db_path=str(path))
            c.commit()
            self.assertEqual(result['created'], 1)
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(
                c.execute('SELECT legacy_stock_id FROM qr_stock_links').fetchone()['legacy_stock_id'],
                OLD,
            )
        finally:
            c.close()
            folder.cleanup()

    def test_blank_category_and_shared_name_stay_in_scope(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            self._product(c, code='', name='白衣', category=stock.CATEGORY_UNIFORM)
            self._product(c, code='', name='白衣', category=stock.CATEGORY_PROCESS_SERVICE)
            blob = xlsx([
                filled(legacyStockId=OLD + 'N', itemNum='', itemName='白衣', itemSize=''),
            ])
            preview = ql.build_preview(c, body_of(blob))
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'unmatched')
            self.assertNotEqual(row['status'], 'out_of_scope')
            code, body = stock_server.api_post(
                str(path), 'desk', '/api/qr-link/commit',
                body_of(
                    blob, previewFingerprint=preview['previewFingerprint'],
                    token='qr-bypass-http-1',
                    selections={str(row['rowNo']): {'include': True}},
                ),
                can_write=True,
            )
            self.assertEqual(code, 400)
            self.assertIn('未一致', body['error'])
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_saved_parts_link_stays_readable(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            self._product(c, code='TAPE-1', name='襟上げテープ', category=stock.CATEGORY_PARTS)
            t = stock.stamp()
            product_id = c.execute(
                "SELECT id FROM products WHERE code='TAPE-1'"
            ).fetchone()['id']
            handle = c.execute(
                'SELECT id, customer_id FROM customer_products WHERE product_id=?',
                (product_id,),
            ).fetchone()
            c.execute(
                '''INSERT INTO qr_stock_links(
                     legacy_stock_id, legacy_customer_id, legacy_maker_id, customer_name,
                     item_num, item_name, customer_id, product_id, customer_product_id,
                     size_label, condition, match_kind, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    OLD + 'K', CUST, MAKER, '顧客A', 'TAPE-1', '襟上げテープ',
                    handle['customer_id'], product_id, handle['id'], 'F', 'new', 'manual', t, t,
                ),
            )
            c.commit()
            blob = xlsx([
                filled(legacyStockId=OLD + 'K', itemNum='TAPE-1', itemName='襟上げテープ'),
            ])
            preview = ql.build_preview(c, body_of(blob))
            self.assertEqual(preview['rows'][0]['status'], 'unchanged')
            self.assertNotEqual(preview['rows'][0]['status'], 'unmatched')
            found = ql.lookup_qr(c, {'id': OLD + 'K', 'customer_id': CUST, 'maker_id': MAKER})
            self.assertTrue(found['found'])
            self.assertEqual(found['link']['itemNum'], 'TAPE-1')
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 1)
        finally:
            c.close()
            folder.cleanup()

    def test_ui_splits_out_of_scope_from_unmatched(self):
        js = (ROOT / 'static' / 'qr_link.js').read_text(encoding='utf-8')
        self.assertIn("statuses:['out_of_scope']", js)
        self.assertIn('対象外', js)
        self.assertIn('data-qr-dismiss-group', js)
        self.assertIn('件を対応不要にする', js)
        self.assertIn('対応不要', js)
        self.assertLess(js.find("statuses:['unmatched']"), js.find("statuses:['out_of_scope']"))


class QrLinkScopeGroupTest(unittest.TestCase):
    def test_name_groups_bulk_dismiss_restore_and_reload(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            before = snap(c)
            rows = []
            for i, name in enumerate(['身頃刺繍', '身頃刺繍', '身頃刺繍', 'ワッペン', '白衣', '襟上げテープ']):
                rows.append(filled(
                    legacyStockId=OLD + str(i),
                    itemNum='',
                    itemName=name,
                    itemSize='',
                ))
            rows.append(filled())
            blob = xlsx(rows)
            preview = ql.build_preview(c, body_of(blob))
            by_name = {}
            for row in preview['rows']:
                by_name.setdefault(row['legacy']['itemName'], []).append(row)
            self.assertTrue(all(row['status'] == 'out_of_scope' for row in by_name['身頃刺繍']))
            self.assertTrue(all(not row['dismissSaved'] for row in by_name['身頃刺繍']))
            self.assertFalse(all(row['canCommit'] for row in by_name['身頃刺繍']))
            self.assertEqual(by_name['ワッペン'][0]['status'], 'out_of_scope')
            self.assertIn('ワッペン', by_name['ワッペン'][0]['reasons'][0])
            self.assertEqual(by_name['白衣'][0]['scopeHint'], '')
            self.assertEqual(by_name['白衣'][0]['status'], 'unmatched')
            self.assertEqual(by_name['襟上げテープ'][0]['scopeHint'], 'unknown_blank')
            self.assertEqual(by_name['襟上げテープ'][0]['status'], 'unmatched')
            self.assertEqual(preview['rows'][-1]['status'], 'exact')
            groups = {g['itemName']: g for g in preview['scopeGroups']}
            self.assertNotIn('身頃刺繍', groups)
            self.assertNotIn('ワッペン', groups)
            self.assertNotIn('白衣', groups)
            self.assertIn('襟上げテープ', groups)
            with self.assertRaises(ValueError):
                ql.commit_preview(c, body_of(
                    blob, previewFingerprint=preview['previewFingerprint'],
                    token='qr-auto-parts-bypass',
                    selections={str(by_name['身頃刺繍'][0]['rowNo']): {'include': True}},
                ), db_path=str(path))
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_link_dismissals').fetchone()['n'], 0)
            self.assertEqual(snap(c), before)
            with self.assertRaises(ValueError):
                ql.dismiss_group(c, body_of(blob, groupKey='processing|白衣'))
        finally:
            c.close()
            folder.cleanup()

    def test_uniform_with_processing_words_stays_and_coded_uniform_wins(self):
        folder, path, c = newdb()
        try:
            seed_cap(c, code='CD-636', name='白衣')
            rows = [
                filled(legacyStockId=OLD + 'A', itemNum='', itemName='白衣(スナッパー加工)', itemSize=''),
                filled(legacyStockId=OLD + 'B', itemNum='', itemName='＊白衣(刺繍)', itemSize=''),
                filled(legacyStockId=OLD + 'C', itemNum='', itemName='白衣・刺繍', itemSize=''),
                filled(legacyStockId=OLD + 'D', itemNum='', itemName='上衣 ポケット取付', itemSize=''),
                filled(legacyStockId=OLD + 'E', itemNum='CD-636', itemName='＊白衣(刺繍)', itemSize='F'),
            ]
            preview = ql.build_preview(c, body_of(xlsx(rows)))
            by_id = {row['legacy']['stockId']: row for row in preview['rows']}
            self.assertEqual(by_id[OLD + 'A']['status'], 'unmatched')
            self.assertEqual(by_id[OLD + 'B']['status'], 'unmatched')
            self.assertEqual(by_id[OLD + 'C']['status'], 'unmatched')
            self.assertEqual(by_id[OLD + 'D']['status'], 'out_of_scope')
            self.assertIn('取付', by_id[OLD + 'D']['reasons'][0])
            self.assertFalse(by_id[OLD + 'D']['canCommit'])
            self.assertEqual(by_id[OLD + 'E']['status'], 'exact')
            self.assertEqual(by_id[OLD + 'E']['candidates'][0]['productName'], '白衣')
        finally:
            c.close()
            folder.cleanup()

    def test_hem_tape_is_parts_even_with_unmatched_code(self):
        folder, path, c = newdb()
        try:
            seed_cap(c, code='CD-636', name='白衣')
            c.execute(
                "UPDATE products SET category=? WHERE code='CD-636'",
                (stock.CATEGORY_UNIFORM,),
            )
            cu = c.execute('SELECT id FROM customers').fetchone()
            su = c.execute('SELECT id FROM suppliers').fetchone()
            stock.save_product(c, {
                'code': 'BLK-1', 'name': '作業帽', 'color': '', 'category': '',
                'supplierId': su['id'], 'active': True, 'sizes': ['F'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            before = snap(c)
            rows = [
                filled(legacyStockId=OLD + 'T', itemNum='01', itemName='裾上げテープ', itemSize=''),
                filled(legacyStockId=OLD + 'N', itemNum='', itemName='裾上げテープ', itemSize=''),
                filled(legacyStockId=OLD + 'V', itemNum='01', itemName='０１ 裾上テープ', itemSize=''),
                filled(legacyStockId=OLD + 'K', itemNum='', itemName='ｽｿｱｹﾞﾃｰﾌﾟ', itemSize=''),
                filled(legacyStockId=OLD + 'S', itemNum='', itemName='＊裾上げ・テープ', itemSize=''),
                filled(legacyStockId=OLD + 'H', itemNum='', itemName='すそ上げテープ', itemSize=''),
                filled(legacyStockId=OLD + 'P', itemNum='', itemName='白衣(裾上げ)', itemSize=''),
                filled(legacyStockId=OLD + 'Q', itemNum='', itemName='白衣・裾上げ', itemSize=''),
                filled(legacyStockId=OLD + 'M', itemNum='', itemName='マスキングテープ', itemSize=''),
                filled(legacyStockId=OLD + 'U', itemNum='CD-636', itemName='裾上げテープ', itemSize='F'),
                filled(legacyStockId=OLD + 'B', itemNum='BLK-1', itemName='裾上げテープ', itemSize='F'),
            ]
            blob = xlsx(rows)
            preview = ql.build_preview(c, body_of(blob))
            by_id = {row['legacy']['stockId']: row for row in preview['rows']}
            for key in (OLD + 'T', OLD + 'N', OLD + 'V', OLD + 'K', OLD + 'S', OLD + 'H'):
                self.assertEqual(by_id[key]['status'], 'out_of_scope', key)
                self.assertIn('裾上げテープ', by_id[key]['reasons'][0])
                self.assertNotIn('加工（裾上げ）', by_id[key]['reasons'][0])
                self.assertFalse(by_id[key]['canCommit'])
                self.assertEqual(by_id[key]['scopeHint'], '')
            self.assertEqual(by_id[OLD + 'P']['status'], 'unmatched')
            self.assertNotIn('裾上げテープ', ' '.join(by_id[OLD + 'P']['reasons']))
            self.assertNotIn('部品・資材（裾上げテープ）', ' '.join(by_id[OLD + 'Q']['reasons']))
            self.assertEqual(by_id[OLD + 'M']['status'], 'unmatched')
            self.assertEqual(by_id[OLD + 'M']['scopeHint'], 'unknown_blank')
            self.assertEqual(by_id[OLD + 'U']['status'], 'exact')
            self.assertEqual(by_id[OLD + 'U']['candidates'][0]['code'], 'CD-636')
            self.assertEqual(by_id[OLD + 'B']['status'], 'exact')
            self.assertEqual(by_id[OLD + 'B']['candidates'][0]['code'], 'BLK-1')
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_link_dismissals').fetchone()['n'], 0)
            with self.assertRaises(ValueError) as caught:
                ql.commit_preview(c, body_of(
                    blob, previewFingerprint=preview['previewFingerprint'],
                    token='qr-hem-tape-bypass',
                    selections={str(by_id[OLD + 'T']['rowNo']): {'include': True}},
                ), db_path=str(path))
            self.assertIn('対象外', str(caught.exception))
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
            self.assertEqual(snap(c)['qty'], before['qty'])
            self.assertEqual(snap(c)['amount'], before['amount'])
        finally:
            c.close()
            folder.cleanup()

    def test_dismiss_refuses_exact_uniform_row(self):
        folder, path, c = newdb()
        try:
            seed_cap(c)
            blob = xlsx([filled()])
            with self.assertRaises(ValueError) as caught:
                ql.dismiss_group(c, body_of(blob, legacyStockId=OLD))
            self.assertIn('未一致ではない', str(caught.exception))
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_link_dismissals').fetchone()['n'], 0)
            code, body = stock_server.api_post(
                str(path), 'desk', '/api/qr-link/dismiss',
                body_of(blob, legacyStockId=OLD),
                can_write=True,
            )
            self.assertEqual(code, 400)
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM qr_stock_links').fetchone()['n'], 0)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
