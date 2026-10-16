"""compare_item_report.py の読み取り専用突合テスト（モックDB）。"""
import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import compare_item_report as cmp  # noqa: E402
import stock  # noqa: E402


def _seed_db(path: Path):
    conn = stock.connect(path)
    stock.initialize(conn)
    s1 = stock.save_supplier(conn, {'name': 'アイトス株式会社', 'active': True})
    s2 = stock.save_supplier(conn, {'name': '丸善被服', 'active': True})
    stock.save_product(conn, {
        'code': 'AZ-8618-003', 'name': 'ワークキャップ', 'color': '紺',
        'supplierId': s1['id'], 'active': True, 'sizes': ['F'], 'links': [],
    })
    stock.save_product(conn, {
        'code': 'SCR-100', 'name': '白スクラブ', 'color': '白',
        'supplierId': s1['id'], 'active': True, 'sizes': ['M', 'L'], 'links': [],
    })
    stock.save_product(conn, {
        'code': 'SCR-100', 'name': '白スクラブ', 'color': '白',
        'supplierId': s2['id'], 'active': True, 'sizes': ['M'], 'links': [],
    })
    stock.save_product(conn, {
        'code': 'NPC71082', 'name': '白衣', 'color': '',
        'supplierId': s1['id'], 'active': True, 'sizes': ['S', 'M'], 'links': [],
    })
    conn.commit()
    conn.close()


class CompareItemReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'stock.sqlite3'
        _seed_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolve_prefers_instruction_data_not_sample(self):
        path = cmp.resolve_stock_db_path()
        self.assertTrue(str(path).endswith('stock.sqlite3'))
        self.assertNotIn(f'{Path("sample")}', path.parts)
        # live/sample under stock-desk/data must be rejected without flag
        sample = ROOT / 'data' / 'sample' / 'stock.sqlite3'
        with self.assertRaises(ValueError):
            cmp.resolve_stock_db_path(sample, allow_standalone=False)
        allowed = cmp.resolve_stock_db_path(sample, allow_standalone=True)
        self.assertEqual(allowed, sample.resolve())

    def test_open_readonly_blocks_writes(self):
        with cmp.open_readonly(self.db) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO suppliers(name, created_at, updated_at) VALUES ('x','t','t')")

    def test_normalize_keeps_hyphen_and_star(self):
        self.assertEqual(cmp.normalize_code(' ＡＺ－８６１８－００３ '), 'AZ-8618-003')
        self.assertEqual(cmp.name_core('＊白衣(DHC刺繍あり)'), '*白衣(DHC刺繍あり)')

    def test_single_code_match_despite_name_color_diff(self):
        csv_rows = [{
            'csv_row': 2, 'condition': '新品', 'supplier': 'アイトス㈱', 'customer': 'A',
            'name': 'ワークキャップ（表記違い）', 'code': 'AZ-8618-003', 'size': 'F',
            'unit_cost': '760', 'qty_purchase': '0', 'amount_purchase': '0',
            'qty_ship': '0', 'amount_ship': '0', 'qty_stock': '0', 'amount_stock': '0',
            'office_code': '', 'site': '', 'shelf': '', 'qty_adjust': '0',
            'code_key': cmp.normalize_code('AZ-8618-003'),
            'name_key': cmp.name_core('ワークキャップ（表記違い）'),
            'supplier_key': cmp.normalize_supplier('アイトス㈱'),
        }]
        with cmp.open_readonly(self.db) as conn:
            products = cmp.load_web_products(conn)
        result = cmp.compare_rows(csv_rows, products)
        self.assertEqual(result['group_stats']['matched'], 1)
        self.assertIn('表記', result['matched'][0]['note'])

    def test_supplier_conflict_goes_to_review(self):
        csv_rows = [{
            'csv_row': 2, 'condition': '新品', 'supplier': '別の仕入先', 'customer': 'A',
            'name': 'ワークキャップ', 'code': 'AZ-8618-003', 'size': 'F',
            'unit_cost': '1', 'qty_purchase': '0', 'amount_purchase': '0',
            'qty_ship': '0', 'amount_ship': '0', 'qty_stock': '0', 'amount_stock': '0',
            'office_code': '', 'site': '', 'shelf': '', 'qty_adjust': '0',
            'code_key': cmp.normalize_code('AZ-8618-003'),
            'name_key': cmp.name_core('ワークキャップ'),
            'supplier_key': cmp.normalize_supplier('別の仕入先'),
        }]
        with cmp.open_readonly(self.db) as conn:
            products = cmp.load_web_products(conn)
        result = cmp.compare_rows(csv_rows, products)
        self.assertEqual(result['group_stats']['review'], 1)

    def test_multi_code_narrowed_by_supplier(self):
        csv_rows = [{
            'csv_row': 2, 'condition': '新品', 'supplier': '丸善被服', 'customer': 'A',
            'name': '白スクラブ', 'code': 'SCR-100', 'size': 'M',
            'unit_cost': '1000', 'qty_purchase': '1', 'amount_purchase': '1000',
            'qty_ship': '0', 'amount_ship': '0', 'qty_stock': '1', 'amount_stock': '1000',
            'office_code': '', 'site': '', 'shelf': '', 'qty_adjust': '0',
            'code_key': cmp.normalize_code('SCR-100'),
            'name_key': cmp.name_core('白スクラブ'),
            'supplier_key': cmp.normalize_supplier('丸善被服'),
        }]
        with cmp.open_readonly(self.db) as conn:
            products = cmp.load_web_products(conn)
        result = cmp.compare_rows(csv_rows, products)
        self.assertEqual(result['group_stats']['matched'], 1)
        self.assertEqual(len(result['matched'][0]['candidates']), 1)
        self.assertEqual(result['matched'][0]['candidates'][0]['supplier_name'], '丸善被服')

    def test_sizes_grouped_but_not_summed(self):
        rows = []
        for i, (size, qty, cost) in enumerate((('S', '1', '2130'), ('M', '5', '2130'), ('L', '1', '2130')), start=2):
            rows.append({
                'csv_row': i, 'condition': '新品', 'supplier': 'アイトス㈱', 'customer': 'A',
                'name': '白衣(DHC刺繍あり)', 'code': 'NPC71082', 'size': size,
                'unit_cost': cost, 'qty_purchase': qty, 'amount_purchase': str(int(qty) * int(cost)),
                'qty_ship': qty, 'amount_ship': str(int(qty) * int(cost)),
                'qty_stock': '0', 'amount_stock': '0',
                'office_code': '', 'site': '', 'shelf': '', 'qty_adjust': '0',
                'code_key': cmp.normalize_code('NPC71082'),
                'name_key': cmp.name_core('白衣(DHC刺繍あり)'),
                'supplier_key': cmp.normalize_supplier('アイトス㈱'),
            })
        with cmp.open_readonly(self.db) as conn:
            products = cmp.load_web_products(conn)
        result = cmp.compare_rows(rows, products)
        self.assertEqual(result['group_stats']['matched'], 1)
        self.assertEqual(len(result['matched']), 3)
        qtys = [r['qty_purchase'] for r in result['matched']]
        self.assertEqual(qtys, ['1', '5', '1'])
        self.assertEqual(result['matched'][0]['group_csv_rows'], '2,3,4')
        self.assertEqual(result['matched'][0]['group_sizes'], 'S,M,L')

    def test_blank_and_missing(self):
        rows = [
            {
                'csv_row': 2, 'condition': '新品', 'supplier': 'x', 'customer': 'A',
                'name': '名なし', 'code': '', 'size': 'F',
                'unit_cost': '0', 'qty_purchase': '0', 'amount_purchase': '0',
                'qty_ship': '0', 'amount_ship': '0', 'qty_stock': '0', 'amount_stock': '0',
                'office_code': '', 'site': '', 'shelf': '', 'qty_adjust': '0',
                'code_key': '', 'name_key': cmp.name_core('名なし'),
                'supplier_key': cmp.normalize_supplier('x'),
            },
            {
                'csv_row': 3, 'condition': '新品', 'supplier': 'x', 'customer': 'A',
                'name': '未知', 'code': 'NO-SUCH-CODE', 'size': 'F',
                'unit_cost': '0', 'qty_purchase': '0', 'amount_purchase': '0',
                'qty_ship': '0', 'amount_ship': '0', 'qty_stock': '0', 'amount_stock': '0',
                'office_code': '', 'site': '', 'shelf': '', 'qty_adjust': '0',
                'code_key': cmp.normalize_code('NO-SUCH-CODE'),
                'name_key': cmp.name_core('未知'),
                'supplier_key': cmp.normalize_supplier('x'),
            },
        ]
        with cmp.open_readonly(self.db) as conn:
            products = cmp.load_web_products(conn)
        result = cmp.compare_rows(rows, products)
        self.assertEqual(result['group_stats']['blank'], 1)
        self.assertEqual(result['group_stats']['missing'], 1)

    def test_end_to_end_excel_and_no_db_mutation(self):
        csv_path = Path(self.tmp.name) / 'item_report.csv'
        with csv_path.open('w', encoding='cp932', newline='') as f:
            w = csv.writer(f)
            w.writerow(['新古', '仕入先', '顧客', '商品名', '商品コード', 'サイズ', '仕入単価',
                        '購入数量', '購入金額', '出荷数', '出荷金額', '在庫', '在庫金額',
                        '営業所コード', '拠点', '棚', '補正在庫数'])
            w.writerow(['新品', 'アイトス㈱', '顧客A', 'ワークキャップ', 'AZ-8618-003', 'F', '760',
                        '0', '0', '0', '0', '0', '0', '', '', '', '0'])
            w.writerow(['中古', 'アイトス㈱', '顧客A', '＊白衣', 'NPC71082', 'M', '0',
                        '2', '0', '2', '0', '0', '0', '', '', '', '0'])
            w.writerow(['新品', '不明', '顧客A', 'なし', '', 'F', '0',
                        '0', '0', '0', '0', '0', '0', '', '', '', '0'])
        before = self.db.read_bytes()
        out = Path(self.tmp.name) / 'out.xlsx'
        payload = cmp.run_compare(csv_path, self.db, out)
        after = self.db.read_bytes()
        self.assertEqual(before, after)
        self.assertTrue(out.is_file())
        self.assertGreater(out.stat().st_size, 1000)
        self.assertEqual(payload['result']['group_stats']['matched'], 2)
        self.assertEqual(payload['result']['group_stats']['blank'], 1)
        # cover sheet mentions absolute db path
        from zipfile import ZipFile
        with ZipFile(out) as z:
            xml = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
        self.assertIn(str(self.db.resolve()), xml)
        self.assertIn('mode=ro', xml)


if __name__ == '__main__':
    unittest.main()
