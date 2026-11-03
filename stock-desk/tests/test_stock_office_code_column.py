import sys, tempfile, unittest, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import stock  # noqa: E402

APP = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')


class OfficeCodeColumnTest(unittest.TestCase):
    def test_ui_leftmost_in_lot_and_customer_only(self):
        self.assertIn("lot:['c-office','l-cust',", APP)
        self.assertIn("customer:['c-office','c-code',", APP)
        self.assertIn("product:['c-name',", APP)
        self.assertIn("location:['c-shelf',", APP)
        self.assertIn("<thead><tr><th class=\"office-code\">営業所</th><th>顧客</th>", APP)
        self.assertIn("<thead><tr><th class=\"office-code\">営業所</th><th>品番</th>", APP)
        self.assertIn('.stock-fixed-table col.c-office{width:calc(3em + 12px)}', CSS)
        self.assertIn('td.office-code{white-space:nowrap', CSS)

    def test_api_carries_customer_office_code(self):
        with tempfile.TemporaryDirectory() as d:
            c = stock.connect(os.path.join(d, 't.db'))
            stock.initialize(c)
            t = stock.now()
            cid = stock.save_customer(c, {'name': '㈱ＬＥＯＣ', 'officeCode': '4015'})['id']
            pid = c.execute('INSERT INTO products(code,name,created_at,updated_at) VALUES(?,?,?,?)', ('X1', '品', t, t)).lastrowid
            cp = c.execute('INSERT INTO customer_products(customer_id,product_id,created_at,updated_at) VALUES(?,?,?,?)', (cid, pid, t, t)).lastrowid
            loc = c.execute('SELECT id FROM locations LIMIT 1').fetchone()[0]
            c.execute('''INSERT INTO lots(customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,
                         basis_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)''', (cp, 'M', loc, 100, 1, 1, '2026-01-01', t, t))
            lots = stock.stock_query(c, {'group': 'lot'})
            self.assertEqual(lots['lots'][0]['officeCode'], '4015')
            groups = stock.stock_query(c, {'group': 'customer'})['groups']
            self.assertEqual(groups[0]['officeCode'], '4015')
            c.close()


if __name__ == '__main__':
    unittest.main()
