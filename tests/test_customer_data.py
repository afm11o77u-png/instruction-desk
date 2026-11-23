import copy
import json
import unittest
from test_common_customers import CommonCustomersTest
import customer_data as data
import web_requests as web
import web_request_organizations as org

class CustomerDataTest(CommonCustomersTest):
    def body(self, cid=1, **values):
        s=data.state(self.f.c,self.f.path,self.f.owner)
        row=next(r for g in s['groups'] if g['key']=='customers' for r in g['rows'] if r['id']==cid)
        after={k:row[k] for k in data.EDITABLE};after.update(values)
        return dict(orgVersion=s['orgVersion'],changes=[dict(id=cid,before=row,after=after)])
    def test_roles_preserve_contract_and_regional_scope_and_undo(self):
        self.f.save(action='publish')
        payload=dict(nodes=[dict(id='root',parent_id=None,kind='company',name='顧客A',customer_id=1),dict(id='region',parent_id='root',kind='region',name='地区',customer_id=1)],staff_scopes=[dict(user_id=2,node_id='root'),dict(user_id=2,node_id='region')],customer_scopes=[],request_pages=[],staff_scope_settings=[])
        org.save(self.f.c,payload,org.read(self.f.c)['version'],{1,2},{1,2,3},self.f.owner)
        before=web.read_config(self.f.c,1)
        result=data.save(self.f.c,self.f.path,self.f.owner,self.body(primary_user_id=3,backup_user_id=1))
        after=web.read_config(self.f.c,1)
        self.assertEqual(after['draft']['departments'],before['draft']['departments'])
        self.assertEqual(after['publication'],before['publication'])
        scopes=org.read(self.f.c)['staff_scopes']
        self.assertIn(dict(user_id=2,node_id='region'),scopes)
        self.assertNotIn(dict(user_id=2,node_id='root'),scopes)
        self.assertIn(dict(user_id=3,node_id='root'),scopes)
        data.undo(self.f.c,self.f.path,self.f.owner,result['history_id'])
        self.assertEqual(web.read_config(self.f.c,1)['draft']['primary_user_id'],2)
    def test_atomic_invalid_second_row_and_stale(self):
        body=self.body(name='変更',primary_user_id=2,backup_user_id=3)
        other=self.body(2,primary_user_id=2,backup_user_id=2)['changes'][0]
        body['changes'].append(other)
        with self.assertRaises(ValueError):data.save(self.f.c,self.f.path,self.f.owner,body)
        self.assertEqual(self.body()['changes'][0]['before']['name'],'顧客A')
        self.assertIsNone(web.read_config(self.f.c,1)['draft']['primary_user_id'])
        good=self.body(primary_user_id=2,backup_user_id=3)
        data.save(self.f.c,self.f.path,self.f.owner,good)
        with self.assertRaises(ValueError):data.save(self.f.c,self.f.path,self.f.owner,good)
    def test_owner_only_and_fields(self):
        with self.assertRaises(PermissionError):data.state(self.f.c,self.f.path,self.f.sales)
        s=data.state(self.f.c,self.f.path,self.f.owner)
        self.assertIn('contracts',[g['key'] for g in s['groups']])
        self.assertIn('backup_user_id',[f['key'] for f in s['groups'][0]['fields']])

if __name__=='__main__':unittest.main()
