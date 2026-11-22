import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from rental_management import service as r

class RentalTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'rental.sqlite3';r.initialize(self.path)
        self.owner=dict(id=1,name='主管理者',role='owner',manage=0,must_change=0)
        self.viewer=dict(id=2,name='倉庫担当',role='warehouse',manage=0,must_change=0)
        self.data=dict(customers=[dict(id=1,name='顧客A'),dict(id=2,name='顧客B')],products=[dict(id=1,code='A01',name='白衣',color='白',sizes=['S','M']),dict(id=2,code='B01',name='パンツ',color='白',sizes=['L'])],handles=[dict(id=10,customer_id=1,product_id=1,embroidery='刺繍'),dict(id=20,customer_id=2,product_id=2,embroidery='')],sites=[dict(id='site1',customer_id=1,name='札幌店')],presets=[])
    def tearDown(self):self.temp.cleanup()
    def save(self,body,user=None,data=None):return r.save(self.path,data or self.data,user or self.owner,body)['id']
    def get(self,entity,**kwargs):return r.get(self.path,self.data,dict(entity=entity,**kwargs))
    def contract(self,cid=1):return self.save(dict(entity='contract',customer_id=cid,name='通常契約',kind='個人レンタル',start_date='2026-01-01',end_date='',active=True,lines=[dict(customer_product_id=10 if cid==1 else 20,quantity=3)]))
    def wearer(self,cid=1,contract=None,code='EMP01'):return self.save(dict(entity='wearer',customer_id=cid,employee_code=code,name='山田太郎',status='在籍',contract_id=contract))
    def unit(self,cid=1,barcode='UNIT001'):return self.save(dict(entity='unit',customer_id=cid,barcode=barcode,customer_product_id=10 if cid==1 else 20,size='M' if cid==1 else 'L',status='待機'))
    def test_full_lifecycle_history_and_snapshots(self):
        k=self.contract();w=self.wearer(contract=k);u=self.unit();body=dict(entity='loan',action='issue',unit_id=u,wearer_id=w,contract_id=k,issued_date='2026-11-01');loan=self.save(body)
        self.assertEqual(self.get('summary')['loaned'],1)
        self.assertEqual(self.get('units',status='待機')['total'],0)
        self.assertEqual(self.get('units',status='貸与中')['total'],1)
        old=self.get('wearers',id=w)['rows'][0];self.save(dict(old,entity='wearer',name='山田改名',status='退職',left_date='2026-11-10'))
        self.assertEqual(self.get('summary')['return_pending'],1)
        before=self.get('loans',id=loan)['rows'][0];self.assertEqual(before['issued_snapshot']['wearer']['name'],'山田太郎')
        self.save(dict(entity='loan',action='return',id=loan,version=before['version'],returned_date='2026-11-11'))
        self.assertEqual(self.get('summary')['loaned'],0);self.assertEqual(self.get('units',status='待機')['total'],1)
        self.assertEqual([x['action'] for x in self.get('events',record_type='unit',record_id=u)['rows']],['返却','貸与','登録'])
    def test_duplicate_identity_double_loan_and_return(self):
        k=self.contract();w=self.wearer();u=self.unit()
        with self.assertRaises(ValueError):self.unit(barcode='UNIT001')
        with self.assertRaises(ValueError):self.wearer(code='EMP01')
        body=dict(entity='loan',action='issue',unit_id=u,wearer_id=w,contract_id=k,issued_date='2026-11-01');loan=self.save(body)
        with self.assertRaises(ValueError):self.save(body)
        with self.assertRaises(ValueError):self.save(dict(entity='loan',action='return',id=loan,version=1,returned_date='2026-10-31'))
        self.save(dict(entity='loan',action='return',id=loan,version=1,returned_date='2026-11-02'))
        with self.assertRaises(ValueError):self.save(dict(entity='loan',action='return',id=loan,version=2,returned_date='2026-11-03'))
        self.assertEqual(self.get('loans')['total'],1)
    def test_cross_customer_authorization(self):
        k=self.contract();w=self.wearer(cid=2);u=self.unit()
        with self.assertRaises(ValueError):self.save(dict(entity='loan',action='issue',unit_id=u,wearer_id=w,contract_id=k,issued_date='2026-11-01'))
        restricted=copy.deepcopy(self.data);restricted['customers']=[dict(id=2,name='顧客B')]
        with self.assertRaises(PermissionError):self.save(dict(entity='unit',id=u,version=1,customer_id=1),data=restricted)
        with self.assertRaises(PermissionError):r.get(self.path,restricted,dict(entity='units',customer_id='1'))
        with self.assertRaises(PermissionError):self.save(dict(entity='unit'),user=self.viewer)
    def test_contract_version_and_immutable_unit(self):
        k=self.contract();row=self.get('contracts',id=k)['rows'][0];self.save(dict(row,entity='contract',name='改定後',active=True))
        with self.assertRaises(ValueError):self.save(dict(row,entity='contract',active=True))
        u=self.unit();row=self.get('units',id=u)['rows'][0]
        with self.assertRaises(ValueError):self.save(dict(row,entity='unit',barcode='CHANGED'))
        with self.assertRaises(ValueError):self.save(dict(row,entity='unit',size='S'))
        self.assertEqual(self.get('contracts',id=k)['rows'][0]['version'],2)
    def test_status_period_target_product_and_size(self):
        k=self.contract();w=self.wearer();u=self.unit()
        with self.assertRaises(ValueError):self.save(dict(entity='unit',customer_id=1,barcode='X',customer_product_id=10,size='LL',status='待機'))
        with self.assertRaises(ValueError):self.save(dict(entity='loan',action='issue',unit_id=u,wearer_id=w,contract_id=k,issued_date='2025-12-31'))
        row=self.get('units',id=u)['rows'][0];self.save(dict(row,entity='unit',status='修理'))
        with self.assertRaises(ValueError):self.save(dict(entity='loan',action='issue',unit_id=u,wearer_id=w,contract_id=k,issued_date='2026-11-01'))
    def test_pagination_and_literal_search(self):
        for i in range(53):self.wearer(code='E'+str(i))
        self.assertEqual(len(self.get('wearers')['rows']),50);self.assertEqual(len(self.get('wearers',page='2')['rows']),3)
        self.assertEqual(self.get('wearers',q='E52')['total'],1);self.assertEqual(self.get('wearers',q='%')['total'],0)
        self.assertEqual(self.get('wearers',q="' OR 1=1")['total'],0)

if __name__=='__main__':unittest.main()
