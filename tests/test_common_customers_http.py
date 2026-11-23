import json
from contextlib import closing
from unittest.mock import patch
import server
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]/'stock-desk'))
import stock
import test_rental_http as fixture

class CommonCustomersHTTPTest(fixture.RentalHTTPTest):
    def setUp(self):
        super().setUp()
        p=patch.object(server,'ensure_stock',lambda:None);p.start();self.patches.append(p)
        with closing(stock.connect(self.fixture.path)) as c,c:
            c.executescript("""ALTER TABLE customers RENAME TO old_customers;
CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,kana TEXT NOT NULL DEFAULT '',address TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',contact TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL DEFAULT '',office_code TEXT NOT NULL DEFAULT '');
INSERT INTO customers(id,name,kana,active) SELECT id,name,kana,active FROM old_customers;
DROP TABLE old_customers;""")
    def test_common_directory_permissions_and_csrf(self):
        self.assertEqual(self.request('/api/customer-directory')[0],403)
        self.assertEqual(self.request('/api/customer-directory','warehouse')[0],403)
        self.assertEqual(self.request('/api/customer-directory','sales')[0],200)
        self.assertEqual(self.request('/api/customer-directory?action=save','sales',{'name':'不許可'})[0],403)
        self.assertEqual(self.request('/api/customer-directory?action=save','owner',{'name':'不許可'},desk_header=False)[0],403)
        self.assertEqual(self.request('/api/customer-directory?action=save','owner',{'name':'不許可'},origin='http://outside.example')[0],403)
    def test_shared_save_and_inventory_legacy_write_block(self):
        code,data=self.request('/api/customer-directory?action=save','owner',dict(name='共通登録',kana='キョウツウトウロク',active=True),origin=self.url)
        self.assertEqual(code,200,data)
        cid=json.loads(data)['id']
        with closing(stock.connect(self.fixture.path)) as c:self.assertEqual(c.execute('select kana from customers where id=?',(cid,)).fetchone()[0],'キョウツウトウロク')
        code,data=self.request('/inventory/api/customers','owner',dict(name='旧入口から追加'))
        self.assertEqual(code,409,data)
        self.assertEqual(self.request('/customer-directory.js','owner')[0],200)
    def test_export_template_is_customer_only(self):
        import excel_book
        code,data=self.request('/api/customer-directory?action=template','owner')
        self.assertEqual(code,200);self.assertEqual(set(excel_book.read_sheets(data)),{'顧客'})
