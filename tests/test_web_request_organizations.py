import copy
import unittest
from test_web_request_settings import SettingsTest
import web_requests as w
import web_request_organizations as org

class OrganizationTest(SettingsTest):
    def setUp(self):
        super().setUp()
        self.c.execute("INSERT INTO customer_accounts VALUES(10,1,1,'地区担当','area@example.test'),(11,2,1,'店舗担当','site@example.test')");self.c.commit()
        self.organization=dict(nodes=[
            dict(id='company',parent_id=None,kind='company',name='ABCD社'),
            dict(id='north',parent_id='company',kind='region',name='北海道地区'),
            dict(id='east',parent_id='company',kind='region',name='東北地区'),
            dict(id='sapporo',parent_id='north',kind='site',name='札幌店',customer_id=1,site_type='店舗'),
            dict(id='sendai',parent_id='east',kind='site',name='仙台事業所',customer_id=2,site_type='事業所')
        ],customer_scopes=[dict(account_id=10,node_ids=['north'],can_request=False),dict(account_id=11,node_ids=['sapporo','sendai'],can_request=True)],staff_scopes=[dict(user_id=3,node_id='north')])
    def save_org(self,payload=None,user=None,version=None):
        return w.save(self.c,self.path,dict(action='organization',organization=payload or self.organization,version=org.read(self.c)['version'] if version is None else version),user or self.owner)
    def test_region_and_multiple_site_customer_scopes(self):
        self.save_org()
        data=w.state(self.c,self.path,self.owner,10)
        self.assertEqual([c['id'] for c in data['customers']],[1]);self.assertFalse(data['previewCanRequest'])
        self.assertEqual({n['id'] for n in data['organizations']['nodes']},{'company','north','sapporo'})
        self.assertEqual(len(data['handles']),2);self.assertEqual(data['organizations']['accounts'],[])
        self.assertEqual({c['id'] for c in w.state(self.c,self.path,self.owner,11)['customers']},{1,2})
    def test_staff_scope_and_server_authority(self):
        self.save();self.save_org()
        self.assertIn(1,[c['id'] for c in w.state(self.c,self.path,self.other)['customers']])
        self.assertIn(1,w.state(self.c,self.path,self.other)['assignedCustomerIds'])
        self.save(user=self.other)
        with self.assertRaises(PermissionError):self.save_org(user=self.sales)
        with self.assertRaises(PermissionError):w.state(self.c,self.path,self.sales,10)
    def test_company_direct_site_no_region_and_empty_scope(self):
        p=copy.deepcopy(self.organization);p['nodes'][3]['parent_id']='company';p['customer_scopes'][0]['node_ids']=['company']
        self.save_org(p);self.assertEqual(org.account_scope(self.c,org.read(self.c),10)[0],{1,2})
        p['customer_scopes'][0]['node_ids']=[];self.save_org(p);self.assertEqual(w.state(self.c,self.path,self.owner,10)['customers'],[])
    def test_invalid_parent_duplicate_link_and_version_rollback(self):
        for change in ('parent','link','name','scope'):
            p=copy.deepcopy(self.organization)
            if change=='parent':p['nodes'][0]['parent_id']='north'
            if change=='link':p['nodes'][4]['customer_id']=1
            if change=='name':p['nodes'][2]['name']='北海道地区'
            if change=='scope':p['customer_scopes'][0]['node_ids']=['missing']
            with self.assertRaises(ValueError):self.save_org(p)
            self.assertEqual(org.read(self.c)['version'],0)
        self.save_org()
        with self.assertRaises(ValueError):self.save_org(version=0)
    def test_unconfigured_legacy_scope_and_new_descendant(self):
        self.assertEqual(org.account_scope(self.c,org.read(self.c),10),({1},True))
        self.save_org();p=copy.deepcopy(self.organization);p['nodes'][4]['parent_id']='north';self.save_org(p)
        self.assertEqual(org.account_scope(self.c,org.read(self.c),10)[0],{1,2})

if __name__=='__main__':unittest.main()
