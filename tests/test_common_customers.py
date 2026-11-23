import base64
from contextlib import closing
import copy
import json
import sqlite3
import sys
from pathlib import Path
import unittest
import test_web_request_settings as fixture
sys.path.append(str(Path(__file__).resolve().parents[1]/'stock-desk'))
import stock
import excel_book
import common_customers as master

class CommonCustomersTest(unittest.TestCase):
    def setUp(self):
        self.f=fixture.SettingsTest();self.f.setUp()
        with closing(stock.connect(self.f.path)) as c, c:
            c.executescript("""ALTER TABLE customers RENAME TO old_customers;
CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,kana TEXT NOT NULL DEFAULT '',address TEXT NOT NULL DEFAULT '',phone TEXT NOT NULL DEFAULT '',contact TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL DEFAULT '',office_code TEXT NOT NULL DEFAULT '');
INSERT INTO customers(id,name,kana,active) SELECT id,name,kana,active FROM old_customers;
DROP TABLE old_customers;""")
    def tearDown(self):self.f.tearDown()
    def payload(self,**extra):return dict(name='共通顧客',kana='キョウツウコキャク',address='住所',phone='011',contact='顧客窓口',officeCode='101',active=True,**extra)
    def test_create_rename_and_stop_keeps_id_and_stock_links(self):
        row=master.save(self.f.path,self.f.owner,self.payload())
        with closing(stock.connect(self.f.path)) as c, c:
            stamp=c.execute('select updated_at from customers where id=?',(row['id'],)).fetchone()[0]
            c.execute("INSERT INTO customer_products VALUES(99,?,1,'検証',1)",(row['id'],))
        renamed=self.payload();renamed.update(id=row['id'],updatedAt=stamp,name='改名した顧客',active=False)
        saved=master.save(self.f.path,self.f.owner,renamed)
        self.assertEqual(saved['id'],row['id']);self.assertFalse(saved['active'])
        with closing(stock.connect(self.f.path)) as c:self.assertEqual(c.execute('select customer_id from customer_products where id=99').fetchone()[0],row['id'])
        listing=master.directory(self.f.c,self.f.path,self.f.owner)
        self.assertIn(row['id'],[r['id'] for r in listing['customers']])
        with self.assertRaises(ValueError):master.save(self.f.path,self.f.owner,dict(renamed,updatedAt='stale'))
    def test_permissions_and_duplicate_name(self):
        master.save(self.f.path,self.f.owner,self.payload())
        with self.assertRaises(ValueError):master.save(self.f.path,self.f.owner,self.payload())
        with self.assertRaises(PermissionError):master.save(self.f.path,self.f.sales,self.payload())
        with self.assertRaises(PermissionError):master.directory(self.f.c,self.f.path,dict(self.f.owner,role='warehouse',manage=0))
    def test_excel_round_trip_and_reject_other_sheets(self):
        master.save(self.f.path,self.f.owner,self.payload())
        blob=master.excel(self.f.path)
        result=master.import_excel(self.f.path,self.f.owner,dict(file=base64.b64encode(blob).decode()))
        self.assertGreater(result['updated'],0);self.assertEqual(result['created'],0)
        bad=excel_book.workbook([('仕入先',['仕入先名'],[['別業務']])])
        with self.assertRaises(ValueError):master.import_excel(self.f.path,self.f.owner,dict(file=base64.b64encode(bad).decode()))
    def test_excel_validation_rolls_back_all_rows(self):
        blob=excel_book.workbook([('顧客',[x[0] for x in master.COLUMNS],[['','途中追加','トチュウ','','','','','使用中'],[999999,'存在しないID','','','','','','使用中']])])
        with self.assertRaises(ValueError):master.import_excel(self.f.path,self.f.owner,dict(file=base64.b64encode(blob).decode()))
        with closing(stock.connect(self.f.path)) as c, c:self.assertFalse(c.execute("select 1 from customers where name='途中追加'").fetchone())
    def test_merge_requires_review_and_blocks_shared_references(self):
        for name in ['㈱同名顧客','(株)同名顧客']:
            body=self.payload();body['name']=name;master.save(self.f.path,self.f.owner,body)
        preview=master.unify(self.f.c,self.f.path,self.f.owner)
        self.assertEqual(len(preview['duplicates']),1)
        source=preview['duplicates'][0]['sources'][0]['id']
        self.f.c.execute('INSERT INTO web_request_customer_settings(customer_id,version,draft,status) VALUES(?,0,?,?)',(source,json.dumps(self.f.cfg),'draft'));self.f.c.commit()
        with self.assertRaisesRegex(ValueError,'内容が変わり'):master.unify(self.f.c,self.f.path,self.f.owner,True,[])
        with self.assertRaisesRegex(ValueError,'使用中'):master.unify(self.f.c,self.f.path,self.f.owner,True,preview['duplicates'])
