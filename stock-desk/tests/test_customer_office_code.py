import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stock  # noqa: E402
import excel_book  # noqa: E402
import product_ledger_import as pli  # noqa: E402
from test_ledger_color_split_register import newdb  # noqa: E402


class CustomerOfficeCodeTest(unittest.TestCase):
    def setUp(self):
        folder, _path, self.c = newdb()
        self.addCleanup(folder.cleanup)

    def test_save_and_keep(self):
        r = stock.save_customer(self.c, {'name': '甲', 'officeCode': '7817'})
        self.assertEqual(r['officeCode'], '7817')
        r = stock.save_customer(self.c, {'id': r['id'], 'name': '甲'})
        self.assertEqual(r['officeCode'], '7817')
        r = stock.save_customer(self.c, {'id': r['id'], 'name': '甲', 'officeCode': ''})
        self.assertEqual(r['officeCode'], '')
        stock.save_customer(self.c, {'name': '乙', 'officeCode': '7817'})  # 重複可

    def test_opening_plan_sets_only_empty(self):
        a = stock.save_customer(self.c, {'name': '甲'})
        b = stock.save_customer(self.c, {'name': '乙', 'officeCode': 'OLD'})
        stock.save_customer(self.c, {'name': '丙'})
        rows = [
            {'顧客': '甲', '営業所コード': '7817'}, {'顧客': '甲', '営業所コード': '7817'},
            {'顧客': '乙', '営業所コード': 'NEW'},
            {'顧客': '丙', '営業所コード': '1'}, {'顧客': '丙', '営業所コード': '2'},
            {'顧客': '丁', '営業所コード': 'HAN-9940'},
            {'顧客': '戊', '営業所コード': ''},
        ]
        plan = pli.plan_office_codes(self.c, rows)
        self.assertEqual(plan['willSet'], 2)
        self.assertEqual(len(plan['warnings']), 2)
        stock.save_customer(self.c, {'name': '丁'})
        self.assertEqual(pli.apply_office_codes(self.c, plan), 2)
        self.assertEqual(stock.customer_row(self.c, a['id'])['officeCode'], '7817')
        self.assertEqual(stock.customer_row(self.c, b['id'])['officeCode'], 'OLD')

    def test_excel_people(self):
        excel_book._people(self.c, [(2, {'name': '甲', 'kana': '', 'address': '', 'phone': '',
                                         'contact': '', 'officeCode': 'K1'})],
                           'customers', stock.save_customer, stock.customer_row, '顧客')
        excel_book._people(self.c, [(2, {'name': '甲', 'kana': '', 'address': '', 'phone': '',
                                         'contact': '', 'officeCode': ''})],
                           'customers', stock.save_customer, stock.customer_row, '顧客')
        cid = self.c.execute("SELECT id FROM customers WHERE name='甲'").fetchone()[0]
        self.assertEqual(stock.customer_row(self.c, cid)['officeCode'], 'K1')


if __name__ == '__main__':
    unittest.main()
