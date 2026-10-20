"""登録・設定の確認用 Excel 出力（記入用ひな形とは別・非破壊）。"""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import excel_book  # noqa: E402
import excel_export  # noqa: E402
import sample_data  # noqa: E402
import server  # noqa: E402
import stock  # noqa: E402

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def read_sheet_rows(blob, sheet_name):
    sheets = excel_book.read_sheets(blob)
    return sheets[sheet_name]


def snapshot_counts(c):
    return {
        'customers': c.execute('SELECT COUNT(*) FROM customers').fetchone()[0],
        'suppliers': c.execute('SELECT COUNT(*) FROM suppliers').fetchone()[0],
        'products': c.execute('SELECT COUNT(*) FROM products').fetchone()[0],
        'links': c.execute('SELECT COUNT(*) FROM customer_products').fetchone()[0],
        'locations': c.execute('SELECT COUNT(*) FROM locations').fetchone()[0],
        'lots': c.execute('SELECT COUNT(*) FROM lots').fetchone()[0],
        'moves': c.execute('SELECT COUNT(*) FROM moves').fetchone()[0],
        'qty_sum': c.execute('SELECT COALESCE(SUM(qty_remaining),0) FROM lots').fetchone()[0],
        'cost_sum': c.execute('SELECT COALESCE(SUM(unit_cost),0) FROM lots').fetchone()[0],
    }


class RegistryExcelExportTests(unittest.TestCase):
    def test_ui_keeps_template_and_adds_export_button(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('id="exceldown"', app)
        self.assertIn('記入用Excel', app)
        self.assertIn('id="excelin"', app)
        self.assertIn('Excelを読み込む', app)
        self.assertIn('id="excelexport"', app)
        self.assertIn('Excel出力', app)
        self.assertIn('確認用Excelです。登録用の記入用Excelとは形式が異なります。', app)
        self.assertNotIn('再取込', app)
        self.assertIn("excelTools(exportKind)", app)
        self.assertIn("bar('顧客名・カナ・電話番号・担当者','','新しく登録','customers')", app)
        self.assertIn("bar('仕入先名・電話番号・担当者','','新しく登録','suppliers')", app)
        self.assertIn("bar('品番・商品名・顧客',productSupplierControl(supplierId)+productSortControl(),'新しく登録','products')", app)
        self.assertIn("q.set('sort',resolveProductSort())", app)
        self.assertIn("bar('拠点名・棚・区画名','','拠点を新しく登録','locations')", app)
        self.assertIn("excelTools('opening')", app)
        self.assertIn('/api/registry-export.xlsx', app)
        self.assertIn('/api/registry-export/preview', app)
        self.assertIn('export-hint', css)
        # 在庫一覧・履歴は対象外
        list_fn = app[app.index('function stockList'):app.index('function movePage')]
        self.assertNotIn('excelexport', list_fn)
        hist = app[app.index('function historyPage'):app.index('function periodPage')]
        self.assertNotIn('excelexport', hist)

    def test_template_endpoint_unchanged_and_export_not_write(self):
        self.assertNotIn('/api/registry-export.xlsx', server.WRITE_PATHS)
        self.assertNotIn('/api/registry-export/preview', server.WRITE_PATHS)
        self.assertIn('/api/import', server.WRITE_PATHS)
        folder, path, c = newdb()
        try:
            code, body, ctype = server.api_get(str(path), 'live', '/api/import/template', {}, can_write=False)
            self.assertEqual(code, 200)
            self.assertIn('spreadsheetml', ctype)
            sheets = excel_book.read_sheets(body)
            self.assertEqual(list(sheets.keys()), ['顧客', '仕入先', '商品', '取扱', '保管場所', '初期在庫'])
            # 記入用は空ひな形（見出しのみ）
            self.assertEqual(len(sheets['顧客']), 1)
        finally:
            c.close()
            folder.cleanup()

    def test_customers_filtered_and_all_with_stopped(self):
        folder, path, c = newdb()
        try:
            active = stock.save_customer(c, {
                'name': '出力病院', 'kana': 'シュツリョクビョウイン', 'phone': '011-000-0099',
                'address': '札幌', 'contact': '山田', 'active': True,
            })
            stopped = stock.save_customer(c, {
                'name': '停止顧客', 'kana': 'テイシ', 'phone': '0120-00-0001',
                'address': '小樽', 'contact': '鈴木', 'active': False,
            })
            other = stock.save_customer(c, {
                'name': '別顧客', 'kana': 'ベツ', 'phone': '011-111-2222',
                'active': True,
            })
            c.commit()
            before = snapshot_counts(c)
            preview = excel_export.preview(c, {
                'kind': ['customers'], 'q': ['出力'], 'includeStopped': ['0'],
            })
            self.assertEqual(preview['filtered']['count'], 1)
            self.assertEqual(preview['all']['count'], 3)
            blob, name = excel_export.export_xlsx(c, {
                'kind': ['customers'], 'scope': ['filtered'], 'q': ['出力'], 'includeStopped': ['0'],
            })
            self.assertTrue(name.startswith('顧客_表示条件_'))
            self.assertTrue(name.endswith('.xlsx'))
            self.assertEqual(blob[:2], b'PK')
            rows = read_sheet_rows(blob, '顧客')
            self.assertEqual(rows[0][:7], ['顧客ID', '顧客名', 'カナ', '住所', '電話番号', '担当者', '使用状態'])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][0], str(active['id']))
            self.assertEqual(rows[1][1], '出力病院')
            self.assertEqual(rows[1][4], '011-000-0099')
            self.assertEqual(rows[1][6], '使用中')
            all_blob, all_name = excel_export.export_xlsx(c, {
                'kind': ['customers'], 'scope': ['all'],
            })
            self.assertTrue(all_name.startswith('顧客_全登録_'))
            all_rows = read_sheet_rows(all_blob, '顧客')
            ids = {r[0] for r in all_rows[1:]}
            self.assertEqual(ids, {str(active['id']), str(stopped['id']), str(other['id'])})
            stopped_row = next(r for r in all_rows[1:] if r[0] == str(stopped['id']))
            self.assertEqual(stopped_row[6], '停止')
            self.assertEqual(stopped_row[4], '0120-00-0001')
            after = snapshot_counts(c)
            self.assertEqual(before, after)
            # HTTP GET + readonly
            code, body, ctype, headers = server.api_get(
                str(path), 'live', '/api/registry-export.xlsx',
                {'kind': ['customers'], 'scope': ['all']},
                can_write=False,
            )
            self.assertEqual(code, 200)
            self.assertIn('spreadsheetml', ctype)
            self.assertIn('Content-Disposition', headers)
            self.assertEqual(snapshot_counts(c), before)
        finally:
            c.close()
            folder.cleanup()

    def test_suppliers_export(self):
        folder, path, c = newdb()
        try:
            s = stock.save_supplier(c, {
                'name': '試験仕入', 'phone': '0133-00-0099', 'address': '石狩',
                'contact': '伊藤', 'active': True,
            })
            c.commit()
            blob, _ = excel_export.export_xlsx(c, {'kind': ['suppliers'], 'scope': ['all']})
            rows = read_sheet_rows(blob, '仕入先')
            self.assertEqual(rows[0], ['仕入先ID', '仕入先名', '住所', '電話番号', '担当者', '使用状態'])
            self.assertEqual(rows[1][0], str(s['id']))
            self.assertEqual(rows[1][3], '0133-00-0099')
        finally:
            c.close()
            folder.cleanup()

    def test_products_two_sheets_include_no_handle_and_stopped_handle(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '取扱病院', 'active': True})
            su = stock.save_supplier(c, {'name': '取扱仕入', 'active': True})
            with_handle = stock.save_product(c, {
                'code': '00-ABC', 'name': 'ゼロ品番', 'color': '紺', 'supplierId': su['id'],
                'active': True, 'sizes': ['S', '08'],
                'links': [
                    {'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                    {'customerId': cu['id'], 'embroidery': '旧仕様', 'isStandard': False, 'active': False},
                ],
            })
            no_handle = stock.save_product(c, {
                'code': 'NO-LINK', 'name': '取扱なし商品', 'color': '白', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'], 'links': [],
            })
            stopped_product = stock.save_product(c, {
                'code': 'STOP-1', 'name': '停止商品', 'color': '黒', 'supplierId': su['id'],
                'active': False, 'sizes': ['L'], 'links': [],
            })
            c.commit()
            before = snapshot_counts(c)
            filtered_blob, _ = excel_export.export_xlsx(c, {
                'kind': ['products'], 'scope': ['filtered'], 'q': [''], 'includeStopped': ['0'],
            })
            products = read_sheet_rows(filtered_blob, '商品一覧')
            handles = read_sheet_rows(filtered_blob, '取扱一覧')
            product_ids = {r[0] for r in products[1:]}
            self.assertIn(str(with_handle['id']), product_ids)
            self.assertIn(str(no_handle['id']), product_ids)
            self.assertNotIn(str(stopped_product['id']), product_ids)
            zero_row = next(r for r in products[1:] if r[0] == str(with_handle['id']))
            self.assertEqual(zero_row[1], '00-ABC')
            self.assertEqual(zero_row[2], '00-ABC')  # 品番表示
            self.assertEqual(zero_row[10], 'S、08')  # 登録サイズ（通常/サブ仕入先列の後）
            self.assertEqual(products[0][8], 'サブ仕入先ID')
            self.assertEqual(products[0][12], '統合先商品ID')
            handle_rows = handles[1:]
            self.assertEqual(len(handle_rows), 2)
            # 取扱: … 顧客名, 加工仕様, 標準設定, 使用状態
            stopped_handle = next(r for r in handle_rows if r[9] == '旧仕様')
            self.assertEqual(stopped_handle[11], '停止')
            self.assertEqual(stopped_handle[10], '')
            standard = next(r for r in handle_rows if r[9] == '加工なし')
            self.assertEqual(standard[10], '標準')
            all_blob, _ = excel_export.export_xlsx(c, {'kind': ['products'], 'scope': ['all']})
            all_products = read_sheet_rows(all_blob, '商品一覧')
            all_ids = {r[0] for r in all_products[1:]}
            self.assertIn(str(stopped_product['id']), all_ids)
            self.assertEqual(len(all_products) - 1, 3)
            self.assertEqual(snapshot_counts(c), before)
        finally:
            c.close()
            folder.cleanup()

    def test_locations_include_shelfsless_site(self):
        folder, path, c = newdb()
        try:
            site = stock.save_location(c, {'siteName': '棚なし拠点', 'active': True})
            with_shelf = stock.save_location(c, {'siteName': '棚あり拠点', 'active': True})
            shelf = stock.save_location(c, {'siteId': with_shelf['id'], 'shelfName': 'A-1', 'active': True})
            c.commit()
            blob, _ = excel_export.export_xlsx(c, {'kind': ['locations'], 'scope': ['all']})
            rows = read_sheet_rows(blob, '保管場所')
            self.assertEqual(rows[0], ['場所ID', '親拠点ID', '拠点名', '棚名', '使用状態'])
            site_row = next(r for r in rows[1:] if r[0] == str(site['id']))
            self.assertEqual(site_row[1], '')
            self.assertEqual(site_row[2], '棚なし拠点')
            self.assertEqual(site_row[3], '')
            parent_row = next(r for r in rows[1:] if r[0] == str(with_shelf['id']))
            self.assertEqual(parent_row[3], '')
            shelf_row = next(r for r in rows[1:] if r[0] == str(shelf['id']))
            self.assertEqual(shelf_row[1], str(with_shelf['id']))
            self.assertEqual(shelf_row[2], '棚あり拠点')
            self.assertEqual(shelf_row[3], 'A-1')
        finally:
            c.close()
            folder.cleanup()

    def test_opening_preserves_unknown_date_and_correction_point(self):
        folder, path, c = newdb()
        try:
            sample_data.seed(c)
            c.commit()
            # 入荷日不明のロット
            unknown = c.execute(
                "SELECT id, qty_received, unit_cost FROM lots WHERE received_unknown=1 LIMIT 1"
            ).fetchone()
            # 訂正
            fixed = stock.correct_opening(c, {
                'lotId': unknown['id'], 'qty': unknown['qty_received'] - 1,
                'unitCost': unknown['unit_cost'], 'reason': '数え直し',
            })
            c.commit()
            before = snapshot_counts(c)
            blob, name = excel_export.export_xlsx(c, {'kind': ['opening'], 'scope': ['all']})
            self.assertTrue(name.startswith('初期在庫_全登録_'))
            rows = read_sheet_rows(blob, '初期在庫')
            headers = rows[0]
            self.assertIn('登録特定ID', headers)
            self.assertIn('取扱ID', headers)
            self.assertIn('初期登録数量', headers)
            self.assertIn('現在庫数', headers)
            self.assertIn('入荷日不明', headers)
            self.assertIn('数量・単価の時点', headers)
            idx = {h: i for i, h in enumerate(headers)}
            # 全ロットが集約で消えない
            lot_n = c.execute('SELECT COUNT(*) FROM lots').fetchone()[0]
            self.assertEqual(len(rows) - 1, lot_n)
            target = next(r for r in rows[1:] if r[idx['登録特定ID']] == str(unknown['id']))
            self.assertEqual(target[idx['初期登録数量']], str(fixed['afterQty']))
            self.assertEqual(target[idx['現在庫数']], str(fixed['afterQty']))
            self.assertEqual(target[idx['入荷日']], '')
            self.assertEqual(target[idx['入荷日不明']], 'はい')
            self.assertEqual(target[idx['訂正あり']], 'はい')
            self.assertEqual(target[idx['数量・単価の時点']], '訂正後の数量・単価')
            # 金額は 0 埋めしない（数量×単価）
            self.assertEqual(int(target[idx['金額']]), fixed['afterQty'] * fixed['afterCost'])
            # 既知入荷日は残す
            known = next(r for r in rows[1:] if r[idx['入荷日不明']] == '' and r[idx['入荷日']])
            self.assertRegex(known[idx['入荷日']], r'^\d{4}-\d{2}-\d{2}$')
            self.assertEqual(snapshot_counts(c), before)
            # zip として開ける
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                self.assertIn('xl/workbook.xml', zf.namelist())
                sheet_xml = zf.read('xl/worksheets/sheet2.xml')
                root = ET.fromstring(sheet_xml)
                self.assertIsNotNone(root.find(f'{NS}autoFilter'))
                self.assertIsNotNone(root.find(f'{NS}sheetViews/{NS}sheetView/{NS}pane'))
                self.assertTrue(root.findall(f'{NS}cols/{NS}col'))
        finally:
            c.close()
            folder.cleanup()

    def test_opening_filtered_search_matches_list(self):
        folder, path, c = newdb()
        try:
            sample_data.seed(c)
            c.commit()
            all_n = len(excel_export._opening(c, {'scope': 'all'}) )
            filtered = excel_export._opening(c, {'scope': 'filtered', 'q': 'かしわ'})
            self.assertGreater(all_n, len(filtered))
            self.assertTrue(all(r['customerName'].startswith('かしわ') for r in filtered))
            preview = excel_export.preview(c, {'kind': ['opening'], 'q': ['かしわ']})
            self.assertEqual(preview['filtered']['count'], len(filtered))
            self.assertEqual(preview['all']['count'], all_n)
        finally:
            c.close()
            folder.cleanup()

    def test_numeric_cells_and_text_preservation_in_xlsx(self):
        folder, path, c = newdb()
        try:
            stock.save_customer(c, {
                'name': '電話顧客', 'phone': '001-2345', 'active': True,
            })
            c.commit()
            blob, _ = excel_export.export_xlsx(c, {'kind': ['customers'], 'scope': ['all']})
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                xml = zf.read('xl/worksheets/sheet2.xml').decode('utf-8')
            # 電話は文字列（先頭ゼロ保持）
            self.assertIn('001-2345', xml)
            self.assertIn('t="inlineStr"', xml)
            # 顧客IDは数値セル
            self.assertRegex(xml, r'<c r="A2"><v>\d+</v></c>')
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
