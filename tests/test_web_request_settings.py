from contextlib import closing
import copy
import io
import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path
import web_requests as w

class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'stock.db'
        with closing(sqlite3.connect(self.path)) as s:
            s.executescript("""CREATE TABLE customers(id INTEGER,name TEXT,active INTEGER);
CREATE TABLE products(id INTEGER,code TEXT,name TEXT,color TEXT,active INTEGER,merged_into_id INTEGER,category TEXT);
CREATE TABLE product_sizes(id INTEGER,product_id INTEGER,label TEXT,sort_order INTEGER);
CREATE TABLE customer_products(id INTEGER,customer_id INTEGER,product_id INTEGER,embroidery TEXT,active INTEGER);
INSERT INTO customers VALUES(1,'顧客A',1),(2,'顧客B',1);
INSERT INTO products VALUES(1,'A01','白衣','白',1,NULL,'制服'),(2,'B01','帽子','青',1,NULL,'制服');
INSERT INTO product_sizes VALUES(1,1,'S',1),(2,1,'M',2),(3,2,'F',1);
INSERT INTO customer_products VALUES(10,1,1,'刺繍A',1),(11,1,1,'加工なし',1),(20,2,2,'',1);""")
            s.commit()
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
        self.c.executescript("CREATE TABLE users(id INTEGER,name TEXT,active INTEGER,role TEXT,manage INTEGER); INSERT INTO users VALUES(1,'管理者',1,'owner',0),(2,'担当A',1,'sales',0),(3,'担当B',1,'sales',0);")
        w.schema(self.c);self.c.commit()
        self.owner=dict(id=1,role='owner',manage=0,must_change=0)
        self.sales=dict(id=2,role='sales',manage=0,must_change=0)
        self.other=dict(id=3,role='sales',manage=0,must_change=0)
        self.cfg=w.default_config();self.cfg['primary_user_id']=2
        self.cfg['departments']=[dict(id='dep1',name='製造',contracts=[dict(id='k1',name='通常契約',kind='個人レンタル',default_qty=3,active=True,uniforms=[dict(id='u1',customer_product_id=10,display_name='作業白衣',description='',default_qty=3,sort_order=1,photo='',active=True)])])]
    def tearDown(self):self.c.close();self.tmp.cleanup()
    def save(self,cfg=None,action='save',user=None,version=None):
        return w.save(self.c,self.path,dict(action=action,customer_id=1,version=w.read_config(self.c,1)['version'] if version is None else version,config=cfg or self.cfg),user or self.owner)
    def test_snapshots_and_sizes(self):
        self.save(action='publish');before=w.read_config(self.c,1)['publication']
        self.cfg['departments'][0]['contracts'][0]['uniforms'][0]['default_qty']=5
        self.save();p=w.read_config(self.c,1);self.assertEqual(p['publication'],before);self.assertEqual(p['draft']['departments'][0]['contracts'][0]['uniforms'][0]['default_qty'],5)
        with closing(sqlite3.connect(self.path)) as s:s.execute("INSERT INTO product_sizes VALUES(4,1,'L',3)");s.execute("UPDATE products SET name='変更後' WHERE id=1");s.commit()
        state=w.state(self.c,self.path,self.sales);self.assertEqual(state['products'][0]['sizes'],['S','M','L']);self.assertEqual(state['products'][0]['name'],'変更後')
        self.save(action='pause');self.assertEqual(w.read_config(self.c,1)['status'],'paused')
    def test_cross_customer_and_processing(self):
        cfg=copy.deepcopy(self.cfg);cfg['departments'][0]['contracts'][0]['uniforms'][0]['customer_product_id']=20
        with self.assertRaises(ValueError):self.save(cfg)
        self.assertEqual(w.read_config(self.c,1)['version'],0)
        cfg=copy.deepcopy(self.cfg);u=copy.deepcopy(cfg['departments'][0]['contracts'][0]['uniforms'][0]);u.update(id='u2',customer_product_id=11);cfg['departments'][0]['contracts'][0]['uniforms'].append(u)
        self.save(cfg,action='publish');self.assertEqual(len(w.read_config(self.c,1)['publication']['config']['departments'][0]['contracts'][0]['uniforms']),2)
    def test_authority_and_conflict(self):
        self.save(user=self.sales)
        self.assertNotIn(1,[x['id'] for x in w.state(self.c,self.path,self.other)['customers']])
        with self.assertRaises(PermissionError):self.save(user=self.other)
        with self.assertRaises(ValueError):self.save(version=0)
    def test_duplicate_departments_rejected_before_save(self):
        self.cfg['departments'][0]['name']='部門A'
        duplicate=copy.deepcopy(self.cfg['departments'][0]);duplicate.update(id='dep2',name=' 部門Ａ　',contracts=[])
        self.cfg['departments'].append(duplicate)
        with self.assertRaisesRegex(ValueError,'部署が重複'):self.save()
        self.assertEqual(w.read_config(self.c,1)['version'],0)
        self.cfg['departments'][1]['name']='検品部'
        self.save()
        self.assertEqual(len(w.read_config(self.c,1)['draft']['departments']),2)

    def test_missing_size_and_photo(self):
        with closing(sqlite3.connect(self.path)) as s:s.execute('DELETE FROM product_sizes WHERE product_id=1');s.commit()
        self.save()
        with self.assertRaises(ValueError):self.save(action='publish')
        from PIL import Image
        b=io.BytesIO();Image.new('RGB',(20,20),'red').save(b,'PNG')
        p=w.normalize_photo('data:image/png;base64,'+base64.b64encode(b.getvalue()).decode());self.assertTrue(p.startswith('data:image/jpeg;base64,'))
        with self.assertRaises(ValueError):w.normalize_photo('data:image/svg+xml;base64,PHN2Zz4=')

if __name__=='__main__':unittest.main()
