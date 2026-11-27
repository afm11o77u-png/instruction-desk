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

    def test_master_layout_import(self):
        import io, openpyxl
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; shipping.schema(c)
        for name in ('長野病院', '長野病院', 'ひのき'):  # 以前に顧客名だけで取り込んだ行（同名あり）
            c.execute("INSERT INTO shipment_list(source_id,customer,manager) VALUES((SELECT IFNULL(MAX(source_id),900000)+1 FROM shipment_list),?,'藤本')", (name,))
        def book(rows):
            wb = openpyxl.Workbook(); ws = wb.active
            ws.append(['顧客コード', '顧客名', '住所１', '電話番号', '集配曜日フラグ', '集配サイクル', '売上金額'])
            for r in rows: ws.append(r)
            b = io.BytesIO(); wb.save(b); return b.getvalue()
        rows = [['A1', '長野病院', '札幌市', '011', 91001000, '5号車', 999], ['A2', '長野病院', '', '', '90100100', '', 0], ['A3', 'ひのき', '', '', 90010010, 0, 0], ['A4', '新規', '', '', 12345, '', 0], ['', '', '', '', 90000000, '', 0]]
        imp = lambda b: shipping.import_book(H('/api/shipment-list/import'), c, b)[1]
        self.assertEqual(imp(book(rows)), dict(created=1, updated=3, unchanged=0, skipped=1, unknownFlags=1, master=True))
        got = [tuple(r) for r in c.execute('SELECT customer_code,customer,weekday,car,manager FROM shipment_list ORDER BY id')]
        self.assertEqual(got, [('A1', '長野病院', '月・木', '5号車', '藤本'), ('A2', '長野病院', '火・金', '', '藤本'), ('A3', 'ひのき', '水・土', '', '藤本'), ('A4', '新規', '', '', '')])
        self.assertNotIn('札幌市', json_dump(c))  # 住所・電話・金額は保存しない
        rows[0][4] = 90000000
        self.assertEqual(imp(book(rows))['updated'], 1)  # 再取込は顧客コードで照合し、増えない
        self.assertEqual(c.execute('SELECT COUNT(*) FROM shipment_list').fetchone()[0], 4)
        self.assertEqual(c.execute("SELECT weekday FROM shipment_list WHERE customer_code='A1'").fetchone()[0], '')
        self.assertEqual([shipping.decode_flag(x) for x in ('91010100', '90000001', '9100100', '81001000', '91002000', '0')], [('月・水・金', True), ('日曜日', True), ('', False), ('', False), ('', False), ('', True)])

    def test_contract_list_and_legacy_owner_only(self):
        import io, openpyxl
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; shipping.schema(c)
        shipping.create(H('/api/shipments'), c, dict(customer='旧顧客'))
        ct = H('/api/shipment-contract')
        self.assertEqual(shipping.search(H('/api/shipment-contract?customer='), c)[1]['total'], 0)  # 別保存・空から
        def book(rows):
            wb = openpyxl.Workbook(); ws = wb.active
            ws.append(['取引先', '住所', '契約形態', '管理担当', '営業担当', 'ネームラベル出力名'])
            for r in rows: ws.append(r)
            b = io.BytesIO(); wb.save(b); return b.getvalue()
        rows = [['A社', '札幌', 'リース', '藤本', '安藤', '山田太郎'], ['A社', '', '買取', '藤本', '', ''], ['A社', '', 'リース', '藤本', '安藤', ''], ['', '', 'x', '', '', '']]
        imp = lambda b: shipping.import_book(H('/api/shipment-contract/import'), c, b)
        self.assertEqual(imp(book(rows))[1], dict(created=2, updated=0, unchanged=0, skipped=1, duplicates=1, sheet='Sheet', missing=[]))
        rows[1][3] = '大島'
        self.assertEqual(imp(book(rows))[1], dict(created=0, updated=1, unchanged=1, skipped=1, duplicates=1, sheet='Sheet', missing=[]))  # 顧客名＋契約形態で照合
        self.assertEqual(imp(book([['A社', '', 'リース', '藤本', '安藤', ''], ['A社', '', 'リース', '大島', '', '']]))[0], 400)  # 同じ契約で担当違いは止める
        got = [tuple(r) for r in c.execute('SELECT source_id,customer,contract_type,manager,sales FROM shipment_contract ORDER BY id')]
        self.assertEqual(got, [(800001, 'A社', 'リース', '藤本', '安藤'), (800002, 'A社', '買取', '大島', '')])
        self.assertNotIn('山田太郎', ' '.join(str(tuple(r)) for t in ('shipment_contract', 'shipment_contract_history') for r in c.execute('SELECT * FROM ' + t)))
        r = shipping.search(H('/api/shipment-contract?customer='), c)[1]['items'][0]
        self.assertEqual(shipping.update(ct, c, dict(id=r['id'], version=r['version'], customer='A社', contractType='リース', manager='藤本', sales='菅沼'))[1]['sales'], '菅沼')
        import zipfile
        sheet = zipfile.ZipFile(io.BytesIO(shipping.export_book(ct, c)[1])).read('xl/worksheets/sheet1.xml').decode()
        self.assertIn('契約形態', sheet); self.assertNotIn('集配曜日', sheet)
        self.assertEqual(imp(shipping.export_book(ct, c)[1])[1]['unchanged'], 2)  # 書き出しをそのまま戻せる
        self.assertEqual(c.execute('SELECT COUNT(*) FROM shipments').fetchone()[0], 1)
        # 旧データは主管理者のみ。集配・契約・担当は今までどおり閲覧できる
        staff = H('/api/shipments?customer='); staff.user = dict(staff.user, role='sales')
        self.assertEqual(shipping.search(staff, c)[0], 403)
        staff.path = '/api/shipment-contract?customer='
        self.assertEqual(shipping.search(staff, c)[1]['total'], 2)

    def test_contract_import_picks_sheet_and_fills_blank_rows(self):
        import io, openpyxl
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; shipping.schema(c)
        imp = lambda b, **k: shipping.import_book(H('/api/shipment-contract/import'), c, b, **k)[1]
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = '売上'
        ws.append(['', '', '取引先', '2016年']); ws.append(['', '', 'A社', 100]); ws.append(['', '', 'B社', 100])
        b = io.BytesIO(); wb.save(b); first = b.getvalue()
        self.assertEqual(imp(first), dict(needsConfirm=True, sheet='売上', missing=['契約形態', '管理担当', '営業担当']))  # 黙って空欄で入れない
        self.assertEqual(imp(first, allow_missing=True)['created'], 2)  # 以前の取り込みと同じ状態（名前だけ）
        ws2 = wb.create_sheet('2021年度～'); ws2.append(['担当一覧']); ws2.append(['営業担当者', '管理\n担当', '契約形態', 'ﾄﾘﾋｷｻｷ', '取 引 先'])
        ws2.append(['安藤', '藤本', 'ＣＬ', '', 'A社']); ws2.append(['大島', '', 'R', '', 'C社'])
        b = io.BytesIO(); wb.save(b)
        res = imp(b.getvalue())
        self.assertEqual((res['sheet'], res['created'], res['updated'], res['missing']), ('2021年度～', 1, 1, []))
        got = [tuple(r) for r in c.execute('SELECT customer,contract_type,manager,sales FROM shipment_contract ORDER BY id')]
        self.assertEqual(got, [('A社', 'ＣＬ', '藤本', '安藤'), ('B社', '', '', ''), ('C社', 'R', '', '大島')])  # 名前だけの行を埋め、増やさない


def json_dump(c):
    return ' '.join(str(tuple(r)) for t in ('shipment_list', 'shipment_list_history') for r in c.execute('SELECT * FROM ' + t))


if __name__ == '__main__':
    unittest.main()
