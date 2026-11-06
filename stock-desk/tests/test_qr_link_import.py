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
