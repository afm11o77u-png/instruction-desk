"""出荷情報一覧：既存の出荷情報と同じ処理で、保存先だけ別（空から始まり、互いに書き込まない）。"""
import sqlite3
import unittest
import shipping


class H:
    def __init__(self, path): self.path = path; self.user = dict(id=1, name='主管理者', login='o', role='owner', must_change=0)


class ShipmentListTest(unittest.TestCase):
    def test_separate_store_starts_empty(self):
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; shipping.schema(c)
        old, new = H('/api/shipments'), H('/api/shipment-list')
        shipping.create(old, c, dict(customer='既存顧客'))
        self.assertEqual(shipping.search(H('/api/shipment-list?customer='), c)[1]['total'], 0)  # 空から始まる
        row = shipping.create(new, c, dict(customer='新しい顧客', weekday='火・金'))[1]
        self.assertEqual(row['number'], 900001)  # 既存のExcelの登録番号と重ならない
        self.assertEqual([x['customer'] for x in shipping.search(H('/api/shipments?customer='), c)[1]['items']], ['既存顧客'])
        self.assertEqual([x['customer'] for x in shipping.search(H('/api/shipment-list?customer='), c)[1]['items']], ['新しい顧客'])
        self.assertEqual(shipping.customers(new, c)[1]['customers'], ['新しい顧客'])
        # 一覧から書き出したExcelを既存の出荷情報へ取り込んでも、番号が見つからず止まる（上書きしない）
        book = shipping.export_book(new, c)[1]
        status, result = shipping.import_book(H('/api/shipments/import'), c, book)
        self.assertEqual(status, 400); self.assertIn('見つかりません', result['error'])
        self.assertEqual(shipping.import_book(H('/api/shipment-list/import'), c, book)[1]['unchanged'], 1)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM shipments').fetchone()[0], 1)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM shipment_list_history').fetchone()[0], 1)

    def test_import_limit_5000_rows(self):
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; shipping.schema(c)
        blank = dict(source_id=None, id=None, method='', pack='', mark_pack='', weekday='火・金', time_band='', car='', manager='', sales='', contract_start='', contract_end='')
        book = lambda n: shipping._workbook([dict(blank, customer=f'顧客{i}') for i in range(n)])
        self.assertEqual(shipping.import_book(H('/api/shipment-list/import'), c, book(3200))[1]['created'], 3200)
        status, result = shipping.import_book(H('/api/shipment-list/import'), c, book(5001))
        self.assertEqual((status, result['error']), (400, '行が多すぎます。5000行までにしてください。'))

    def test_list_ignores_unused_fields(self):
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; shipping.schema(c)
        new = H('/api/shipment-list')
        c.execute("INSERT INTO shipment_list(source_id,customer,method,sales,weekday) VALUES(900001,'顧客A','自社便','安藤','月・木')")  # 取込済みの値は残す
        full = dict(source_id=None, id=None, customer='顧客B', method='自社便', pack='x', mark_pack='x', weekday='火・金', time_band='9:00～10:00', car='1号車', manager='藤本', sales='安藤', contract_start='bad', contract_end='')
        self.assertEqual(shipping.import_book(H('/api/shipment-list/import'), c, shipping._workbook([full]))[1]['created'], 1)  # 使わない列は無視（不正な日付でも止めない）
        b = c.execute("SELECT * FROM shipment_list WHERE customer='顧客B'").fetchone()
        self.assertEqual((b['method'], b['sales'], b['contract_start'], b['weekday'], b['car'], b['manager']), ('', '', '', '火・金', '1号車', '藤本'))
        a = c.execute("SELECT * FROM shipment_list WHERE customer='顧客A'").fetchone()
        shipping.update(new, c, dict(id=a['id'], version=a['version'], customer='顧客A', weekday='水・土'))
        a = c.execute("SELECT * FROM shipment_list WHERE customer='顧客A'").fetchone()
        self.assertEqual((a['weekday'], a['method'], a['sales']), ('水・土', '自社便', '安藤'))
        import io, zipfile
        sheet = zipfile.ZipFile(io.BytesIO(shipping.export_book(new, c)[1])).read('xl/worksheets/sheet1.xml').decode()
        self.assertIn('集配号車', sheet); self.assertNotIn('出荷方法', sheet); self.assertNotIn('営業担当', sheet)
        old_sheet = zipfile.ZipFile(io.BytesIO(shipping.export_book(H('/api/shipments'), c)[1])).read('xl/worksheets/sheet1.xml').decode()
        self.assertIn('出荷方法', old_sheet)  # 既存の出荷情報は変えない


if __name__ == '__main__':
    unittest.main()
