import copy
import unittest
import test_web_request_settings as fixtures
import web_requests as w
import web_request_organizations as org
import web_request_inheritance as inherit

class OrganizationTest(fixtures.SettingsTest):
    def setUp(self):
        super().setUp()
        self.c.execute("INSERT INTO customer_accounts VALUES(10,1,1,'地区担当','area@example.test'),(11,1,1,'店舗担当','site@example.test')");self.c.commit()
        self.organization=dict(nodes=[
            dict(id='company',parent_id=None,kind='company',name='ABCD社',customer_id=1),
            dict(id='north',parent_id='company',kind='region',name='北海道地区',customer_id=1),
            dict(id='east',parent_id='company',kind='region',name='東北地区',customer_id=1),
            dict(id='sapporo',parent_id='north',kind='site',name='札幌店',customer_id=1,site_type='店舗'),
            dict(id='sendai',parent_id='east',kind='site',name='仙台事業所',customer_id=1,site_type='事業所')
        ],customer_scopes=[dict(account_id=10,node_ids=['north'],can_request=False),dict(account_id=11,node_ids=['sapporo','sendai'],can_request=True)],staff_scopes=[dict(user_id=3,node_id='north')])
    def test_registered_internal_users_and_scope_modes(self):
        import auth
        self.c.execute("ALTER TABLE users ADD COLUMN login TEXT DEFAULT ''")
        self.c.execute("ALTER TABLE users ADD COLUMN password TEXT DEFAULT 'hash'")
        self.c.execute("INSERT INTO users VALUES(4,'現場担当',1,'floor',0,'staff-id','hash')")
        self.c.execute("INSERT INTO users VALUES(5,'停止中',0,'floor',0,'inactive','hash')")
        self.c.execute("INSERT INTO users VALUES(6,'名前のみ',1,'floor',0,?,'hash')",(auth.new_name_only_login(),))
        self.c.commit()
        users=w.state(self.c,self.path,self.owner)['organizationStaff']
        self.assertIn(dict(id=4,name='現場担当',login='staff-id'),users)
        self.assertFalse(any(u['id'] in (5,6) for u in users))
        self.assertTrue(all('password' not in u for u in users))
        self.organization['staff_scopes']=[dict(user_id=4,node_id='company')]
        self.save_org()
        self.assertTrue(org.staff_can_manage(org.read(self.c),4,1,'sendai'))
        self.organization['nodes'].append(dict(id='future',parent_id='north',kind='site',name='新拠点',customer_id=1))
        self.save_org()
        self.assertTrue(org.staff_can_manage(org.read(self.c),4,1,'future'))
        self.organization['staff_scopes']=[dict(user_id=4,node_id='sapporo')]
        self.save_org()
        self.assertTrue(org.staff_can_manage(org.read(self.c),4,1,'sapporo'))
        self.assertFalse(org.staff_can_manage(org.read(self.c),4,1,'sendai'))

    def test_department_subset_assignment_and_direct_contract_roundtrip(self):
        self.cfg['departments'].append(dict(id='direct',name='部署なし',no_department=True,contracts=[]))
        self.save();self.save_org()
        data=w.state(self.c,self.path,self.owner)
        source=w.read_config(self.c,1)['draft']['departments']
        target=next(p for p in data['scopeProfiles'] if p['node_id']=='sapporo')
        w.save(self.c,self.path,dict(action='save_many',customer_id=1,source_node_id=None,expected_departments=source,department_ids=['direct'],targets=[dict(node_id='sapporo',version=target['version'])]),self.owner)
        result=inherit.resolve(self.c,w.read_config(self.c,1),org.read(self.c),'sapporo')[0]
        self.assertEqual([d['id'] for d in result['departments']],['direct'])
        self.assertTrue(result['departments'][0]['no_department'])
        with self.assertRaises(ValueError):
            w.save(self.c,self.path,dict(action='save_many',customer_id=1,expected_departments=source,department_ids=['missing'],targets=[dict(node_id='sapporo',version=1)]),self.owner)

    def test_company_personnel_toggle_keeps_settings_and_filters_targets(self):
        self.save();self.save_org()
        before=w.read_config(self.c,1)
        state=w.state(self.c,self.path,self.owner)
        self.assertEqual({x['node_id'] for x in state['personnelTargets']},{'sapporo','sendai'})
        for enabled in (True,False):
            state=w.save(self.c,self.path,dict(action='company_personnel',customer_id=1,enabled=enabled,version=state['organizations']['version']),self.owner)
            self.assertEqual('company' in {x['node_id'] for x in state['personnelTargets']},enabled)
            self.assertFalse(any(x['kind']=='region' for x in state['personnelTargets']))
            self.assertEqual(w.read_config(self.c,1),before)
        with self.assertRaises(PermissionError):w.save(self.c,self.path,dict(action='company_personnel',customer_id=1,enabled=True,version=state['organizations']['version']),self.sales)
        payload=copy.deepcopy(self.organization);payload['nodes']=[payload['nodes'][0]];payload['customer_scopes']=[];payload['staff_scopes']=[]
        self.save_org(payload)
        self.assertTrue(org.personnel_enabled(org.read(self.c),org.read(self.c)['nodes'][0]))

    def test_department_library_is_not_auto_assigned_and_assignment_is_atomic(self):
        import web_request_department_registry as registry
        import common_contracts
        self.save();self.save_org()
        result=w.save(self.c,self.path,dict(action='department_library',customer_id=1,version=0,config=self.cfg),self.owner)
        self.assertTrue(all(not a['department_ids'] for a in result['departmentAssignments']))
        self.assertEqual(common_contracts.records(self.c,{1}),[])
        payload=dict(action='assign_department',customer_id=1,department_id='dep1',operation='add',library_version=1,targets=[dict(node_id='sapporo',version=0)])
        result=w.save(self.c,self.path,payload,self.owner)
        self.assertEqual(next(a for a in result['departmentAssignments'] if a['node_id']=='sapporo')['department_ids'],['dep1'])
        self.assertEqual(len(common_contracts.records(self.c,{1})),1)
        self.assertEqual(common_contracts.records(self.c,{1})[0]['node_id'],'sapporo')
        with self.assertRaises(ValueError):w.save(self.c,self.path,payload,self.owner)
        invalid=copy.deepcopy(payload);invalid['targets']=[dict(node_id='sendai',version=0),dict(node_id='north',version=0)]
        with self.assertRaises(PermissionError):w.save(self.c,self.path,invalid,self.owner)
        self.assertEqual(registry.selection(self.c,w.read_config(self.c,1),org.read(self.c),self.organization['nodes'][-1])['department_ids'],[])
        payload.update(operation='remove',targets=[dict(node_id='sapporo',version=1)])
        w.save(self.c,self.path,payload,self.owner)
        self.assertEqual(common_contracts.records(self.c,{1}),[])
        self.assertEqual(len(registry.library(self.c,w.read_config(self.c,1))['draft']['departments']),1)
        invalid['targets']=[dict(node_id='company',version=0)]
        with self.assertRaises(PermissionError):w.save(self.c,self.path,invalid,self.owner)

    def test_multiple_departments_assigned_and_removed_together(self):
        self.save_org()
        cfg=copy.deepcopy(self.cfg);cfg['departments'].append(dict(id='dep2',name='管理',contracts=[]))
        w.save(self.c,self.path,dict(action='department_library',customer_id=1,version=0,config=cfg),self.owner)
        payload=dict(action='assign_department',customer_id=1,department_ids=['dep1','dep2'],operation='add',library_version=1,targets=[dict(node_id='sapporo',version=0),dict(node_id='sendai',version=0)])
        result=w.save(self.c,self.path,payload,self.owner)
        self.assertTrue(all(set(a['department_ids'])=={'dep1','dep2'} for a in result['departmentAssignments']))
        payload.update(operation='remove',targets=[dict(node_id='sapporo',version=1)])
        result=w.save(self.c,self.path,payload,self.owner)
        self.assertEqual(next(a['department_ids'] for a in result['departmentAssignments'] if a['node_id']=='sapporo'),[])
        self.assertEqual(set(next(a['department_ids'] for a in result['departmentAssignments'] if a['node_id']=='sendai')),{'dep1','dep2'})

    def test_region_and_site_contact_details_round_trip(self):
        for node in self.organization['nodes']:
            node.update(kana='フリガナ',office_code='101',address='所在地',phone='011-123',contact='窓口')
        self.save_org()
        saved=org.read(self.c)
        for node in saved['nodes']:
            self.assertEqual(node['address'],'所在地')
            self.assertEqual(node['phone'],'011-123')
            self.assertEqual(node['contact'],'窓口')
        invalid=copy.deepcopy(self.organization);invalid['nodes'][1]['phone']='x'*41
        with self.assertRaises(ValueError):self.save_org(invalid)
        self.assertEqual(org.read(self.c),saved)

    def save_org(self,payload=None,user=None,version=None):
        return w.save(self.c,self.path,dict(action='organization',organization=payload or self.organization,version=org.read(self.c)['version'] if version is None else version),user or self.owner)
    def scope(self,node,config=None,mode='custom',action='publish'):
        base=w.read_config(self.c,1);p=inherit.profile(self.c,base,org.read(self.c),next(n for n in org.read(self.c)['nodes'] if n['id']==node))
        return w.save(self.c,self.path,dict(action=action,customer_id=1,node_id=node,version=p['version'],config=config or p['draft'],parent_config=w.validate_config(p['parent_config'],w.state(self.c,self.path,self.owner),1),inheritance_mode=mode),self.owner)
    def test_explicit_empty_staff_scope_survives_save(self):
        payload=copy.deepcopy(self.organization)
        payload['staff_scopes']=[]
        payload['staff_scope_settings']=[dict(user_id=3,customer_id=1)]
        self.save_org(payload)
        saved=org.read(self.c)
        self.assertEqual(saved['staff_scopes'],[])
        self.assertEqual(saved['staff_scope_settings'],payload['staff_scope_settings'])
        payload['staff_scope_settings']=[dict(user_id=9999,customer_id=1)]
        with self.assertRaises(ValueError):self.save_org(payload)

    def test_region_scopes_do_not_expose_other_sites_of_same_customer(self):
        self.save_org();data=w.state(self.c,self.path,self.owner,10)
        self.assertEqual([x['id'] for x in data['customers']],[1]);self.assertFalse(data['previewCanRequest'])
        self.assertEqual({n['id'] for n in data['organizations']['nodes']},{'company','north','sapporo'})
        self.assertEqual({p['node_id'] for p in data['scopeProfiles']},{'north','sapporo'})
        self.assertNotIn('sendai',data['allowedNodeIds'])
        self.assertIn('sendai',w.state(self.c,self.path,self.owner,11)['allowedNodeIds'])
    def test_regional_staff_cannot_edit_another_region_or_customer_common(self):
        self.save();self.save_org()
        state=w.state(self.c,self.path,self.other)
        self.assertNotIn('sendai',state['allowedNodeIds'])
        self.assertNotIn(1,state['manageCommonCustomerIds'])
        with self.assertRaises(PermissionError):self.save(user=self.other)
        profile=inherit.profile(self.c,w.read_config(self.c,1),org.read(self.c),org.read(self.c)['nodes'][4])
        with self.assertRaises(PermissionError):w.save(self.c,self.path,dict(action='save',customer_id=1,node_id='sendai',version=0,config=profile['draft'],inheritance_mode='custom'),self.other)

    def test_customer_page_can_be_created_before_any_account_link(self):
        payload=copy.deepcopy(self.organization);payload['customer_scopes']=[]
        payload['request_pages']=[dict(id='page1',name='札幌店用',customer_id=1,node_ids=['sapporo'],can_request=False)]
        self.save_org(payload)
        preview=w.state(self.c,self.path,self.owner,preview_page_id='page1')
        self.assertEqual(preview['previewPageName'],'札幌店用');self.assertFalse(preview['previewCanRequest'])
        self.assertEqual(preview['allowedNodeIds'],['sapporo'])
        self.assertEqual(org.read(self.c)['customer_scopes'],[])
        payload['customer_scopes']=[dict(account_id=10,page_id='page1'),dict(account_id=11,page_id='page1')]
        self.save_org(payload)
        for aid in [10,11]:
            self.assertEqual(org.account_scope(self.c,org.read(self.c),aid),({1},False))
            self.assertEqual(org.account_node_ids(self.c,org.read(self.c),aid),{'sapporo'})
        payload['request_pages'][0]['node_ids']=['north'];self.save_org(payload)
        self.assertEqual(org.account_node_ids(self.c,org.read(self.c),10),{'north','sapporo'})
    def test_page_validation_and_preview_authority(self):
        payload=copy.deepcopy(self.organization);payload['request_pages']=[dict(id='page1',name='店舗用',customer_id=2,node_ids=['sapporo'],can_request=True)]
        with self.assertRaises(ValueError):self.save_org(payload)
        payload['request_pages'][0]['customer_id']=1;payload['customer_scopes']=[dict(account_id=10,page_id='missing')]
        with self.assertRaises(ValueError):self.save_org(payload)
        payload['customer_scopes']=[];self.save_org(payload)
        with self.assertRaises(PermissionError):w.state(self.c,self.path,self.sales,preview_page_id='page1')
        with self.assertRaises(ValueError):w.state(self.c,self.path,self.owner,10,'page1')
        with self.assertRaises(ValueError):w.state(self.c,self.path,self.owner,preview_page_id='missing')

    def test_shared_inventory_for_direct_and_categorized_sites(self):
        p=copy.deepcopy(self.organization);p['nodes'][3]['parent_id']='company'
        self.save_org(p)
        saved=org.read(self.c)
        self.assertIn('sapporo',org.descendant_ids(saved,{'company'}))
        self.assertNotIn('sapporo',org.descendant_ids(saved,{'north'}))
        p['nodes'][3]['parent_id']='east';self.save_org(p)
        self.assertEqual(org.customer_ids(org.read(self.c),['company']),{1})
        self.assertEqual(len([n for n in org.read(self.c)['nodes'] if n['kind']=='site']),2)
    def test_invalid_cross_customer_and_duplicate_root(self):
        for change in ['parent','customer','duplicate','name','scope']:
            p=copy.deepcopy(self.organization)
            if change=='parent':p['nodes'][0]['parent_id']='north'
            if change=='customer':p['nodes'][4]['customer_id']=2
            if change=='duplicate':p['nodes'].append(dict(id='another',parent_id=None,kind='company',name='別名',customer_id=1))
            if change=='name':p['nodes'][2]['name']='北海道地区'
            if change=='scope':p['customer_scopes'][0]['node_ids']=['missing']
            with self.assertRaises(ValueError):self.save_org(p)
            self.assertEqual(org.read(self.c)['version'],0)
        with self.assertRaises(PermissionError):self.save_org(user=self.sales)
    def test_inheritance_changes_only_overridden_uniform(self):
        self.cfg['departments'][0]['contracts'][0]['uniforms'].append(dict(id='u2',customer_product_id=11,display_name='パンツ',description='',default_qty=3,sort_order=2,photo='',active=True))
        self.save(action='publish');self.save_org()
        region=copy.deepcopy(self.cfg);region['departments'][0]['contracts'][0]['uniforms'][0]['default_qty']=4
        self.scope('north',region)
        site=inherit.profile(self.c,w.read_config(self.c,1),org.read(self.c),org.read(self.c)['nodes'][3])['draft']
        site['departments'][0]['contracts'][0]['uniforms'][1]['default_qty']=2;self.scope('sapporo',site)
        self.cfg['departments'][0]['contracts'][0]['uniforms'][0]['description']='共通で変更';self.save(action='publish')
        config,status=inherit.resolve(self.c,w.read_config(self.c,1),org.read(self.c),'sapporo',True)
        uniforms=config['departments'][0]['contracts'][0]['uniforms']
        self.assertEqual([u['default_qty'] for u in uniforms],[4,2]);self.assertEqual([u['_source'] for u in uniforms],['北海道地区','札幌店'])
        self.assertEqual(uniforms[0]['description'],'共通で変更');self.assertEqual(status,'published')
        self.scope('sapporo',mode='inherit')
        config,_=inherit.resolve(self.c,w.read_config(self.c,1),org.read(self.c),'sapporo',True)
        self.assertEqual([u['default_qty'] for u in config['departments'][0]['contracts'][0]['uniforms']],[4,3])
    def test_draft_overrides_do_not_change_published_scope(self):
        self.save(action='publish');self.save_org();config=copy.deepcopy(self.cfg);config['departments'][0]['contracts'][0]['uniforms'][0]['default_qty']=2
        self.scope('north',config,action='save')
        published,_=inherit.resolve(self.c,w.read_config(self.c,1),org.read(self.c),'sapporo',True)
        draft,_=inherit.resolve(self.c,w.read_config(self.c,1),org.read(self.c),'sapporo')
        self.assertEqual(published['departments'][0]['contracts'][0]['uniforms'][0]['default_qty'],3)
        self.assertEqual(draft['departments'][0]['contracts'][0]['uniforms'][0]['default_qty'],2)
    def test_deleted_parent_uniform_is_not_resurrected_as_partial_data(self):
        patch=inherit.difference(dict(uniforms=[dict(id='a',default_qty=3)]),dict(uniforms=[dict(id='a',default_qty=2)]))
        self.assertEqual(inherit.apply(dict(uniforms=[]),patch,'地区')['uniforms'],[])

if __name__=='__main__':unittest.main()
