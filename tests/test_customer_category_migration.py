import copy
import json
import unittest
import test_web_request_settings as fixture
import web_request_organizations as org
import web_request_inheritance as inherit
import web_requests as web

class CategoryMigrationTest(unittest.TestCase):
    def setUp(self):
        self.f=fixture.SettingsTest();self.f.setUp();self.addCleanup(self.f.tearDown)
    def test_schema_keeps_direct_site_contract_staff_and_page(self):
        self.f.save()
        payload=dict(nodes=[dict(id='company',kind='company',parent_id=None,name='会社',customer_id=1),dict(id='old-category',kind='region',parent_id='company',name='直属',customer_id=1),dict(id='shop',kind='site',parent_id='company',name='既存店舗',customer_id=1,address='住所',site_type='店舗')],staff_scopes=[dict(user_id=2,node_id='company'),dict(user_id=3,node_id='shop')],customer_scopes=[],request_pages=[dict(id='page',name='店舗用',customer_id=1,node_ids=['shop'],can_request=True)])
        self.f.c.execute('UPDATE web_request_org_settings SET version=7,payload=?',(json.dumps(payload),))
        base=web.read_config(self.f.c,1);cfg=copy.deepcopy(base['draft']);cfg['departments'][0]['contracts'][0]['uniforms'][0]['default_qty']=9
        patch=inherit.difference(base['draft'],cfg)
        self.f.c.execute("INSERT INTO web_request_scope_settings VALUES('shop',1,2,?,NULL,'draft')",(json.dumps(dict(mode='custom',patch=patch)),));self.f.c.commit()
        org.schema(self.f.c)
        saved=org.read(self.f.c);shop=next(n for n in saved['nodes'] if n['id']=='shop')
        self.assertEqual(shop['parent_id'],'company')
        self.assertEqual(len(saved['nodes']),3)
        self.assertEqual(shop['address'],'住所');self.assertEqual(saved['staff_scopes'],payload['staff_scopes']);self.assertEqual(saved['request_pages'],payload['request_pages'])
        actual=inherit.profile(self.f.c,base,saved,shop)['draft']
        self.assertEqual(actual['departments'][0]['contracts'][0]['uniforms'][0]['default_qty'],9)
        self.assertEqual(actual['departments'][0]['contracts'][0]['id'],'k1')
        self.assertEqual(saved['version'],7)
        org.schema(self.f.c);self.assertEqual(org.read(self.f.c),saved)
    def test_new_company_starts_without_automatic_category(self):
        payload=dict(nodes=[dict(id='root',kind='company',name='会社',parent_id=None,customer_id=1)],staff_scopes=[],customer_scopes=[])
        org.save(self.f.c,payload,0,{1,2},{1,2,3},self.f.owner)
        org.schema(self.f.c);self.assertEqual(len(org.read(self.f.c)['nodes']),1)

    def test_save_new_direct_site_without_category(self):
        payload=dict(nodes=[
            dict(id='root',kind='company',name='会社',parent_id=None,customer_id=1),
            dict(id='site',kind='site',name='本店',parent_id='root',customer_id=1,site_type='店舗'),
        ],staff_scopes=[],customer_scopes=[])
        org.save(self.f.c,payload,0,{1,2},{1,2,3},self.f.owner)
        org.schema(self.f.c)
        saved=org.read(self.f.c)
        self.assertEqual({n['id']:n['parent_id'] for n in saved['nodes']},{'root':None,'site':'root'})
        self.assertEqual(org.descendant_ids(saved,{'root'}),{'root','site'})

    def test_direct_group_name_saved_on_company_without_category(self):
        payload=dict(nodes=[dict(id='root',kind='company',name='会社',parent_id=None,customer_id=1,direct_group_name=' 直属 '),dict(id='area',kind='region',name='地方',parent_id='root',customer_id=1),dict(id='site',kind='site',name='本店',parent_id='root',customer_id=1,site_type='店舗')],staff_scopes=[],customer_scopes=[])
        org.save(self.f.c,payload,0,{1,2},{1,2,3},self.f.owner);org.schema(self.f.c)
        saved=org.read(self.f.c);by={n['id']:n for n in saved['nodes']}
        self.assertEqual(by['root']['direct_group_name'],'直属');self.assertEqual(by['site']['parent_id'],'root');self.assertEqual(len(saved['nodes']),3)
        payload['nodes'][0]['direct_group_name']='地方'
        with self.assertRaises(ValueError):org.save(self.f.c,payload,saved['version'],{1,2},{1,2,3},self.f.owner)

if __name__=='__main__':unittest.main()
