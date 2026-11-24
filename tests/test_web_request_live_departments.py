import copy
import unittest
import test_web_request_organizations as orgtests
import web_requests as w


class LiveDepartmentTest(orgtests.OrganizationTest):
    """Saved department library + store assignments are shown on the order screen without publishing."""

    def library(self,cfg=None):
        lib=next(x for x in w.state(self.c,self.path,self.owner)['departmentLibraries'] if x['customer_id']==1)
        return w.save(self.c,self.path,dict(action='department_library',customer_id=1,version=lib['version'],config=cfg or self.cfg),self.owner)

    def assign(self,node,deps=('dep1',)):
        st=w.state(self.c,self.path,self.owner);lib=next(x for x in st['departmentLibraries'] if x['customer_id']==1)
        v=next((a['version'] for a in st['departmentAssignments'] if a['node_id']==node),0)
        return w.save(self.c,self.path,dict(action='assign_department',customer_id=1,department_ids=list(deps),operation='add',library_version=lib['version'],targets=[dict(node_id=node,version=v)]),self.owner)

    def live(self,node=None,user=None):
        st=w.state(self.c,self.path,user or self.owner)
        p=next(x for x in (st['scopeProfiles'] if node else st['profiles']) if x['customer_id']==1 and x.get('node_id')==node)
        return p.get('live')

    def names(self,node):
        return [d['name'] for d in self.live(node)['config']['departments']]

    def test_fallback_without_library_keeps_publication(self):
        self.save(action='publish');self.save_org()
        self.assertIsNone(self.live('sapporo'))
        p=next(x for x in w.state(self.c,self.path,self.owner)['scopeProfiles'] if x['node_id']=='sapporo')
        self.assertEqual(p['status'],'published');self.assertEqual(p['publication']['config']['departments'][0]['name'],'製造')

    def test_saved_library_is_live_and_filtered_by_assignment(self):
        self.save();self.save_org()
        cfg=copy.deepcopy(self.cfg);cfg['departments'].append(dict(id='dep2',name='管理',contracts=[]))
        self.library(cfg)
        self.assertEqual(self.names('sapporo'),[]);self.assertEqual(self.live('sapporo')['status'],'published')
        self.assign('sapporo',('dep1','dep2'));self.assign('sendai',('dep2',))
        self.assertEqual(self.names('sapporo'),['製造','管理']);self.assertEqual(self.names('sendai'),['管理'])
        self.assertEqual(self.names('north'),[])  # categories have no own assignments
        self.assertEqual(self.live()['config']['departments'],[])  # company with stores: choose a store
        self.assertEqual(self.live('sapporo')['config']['departments'][0]['contracts'][0]['uniforms'][0]['customer_product_id'],10)
        # A later save is visible immediately, without publishing.
        cfg['departments'][0]['name']='製造部';self.library(cfg)
        self.assertEqual(self.names('sapporo'),['製造部','管理'])

    def test_only_offerable_contracts_and_uniforms(self):
        self.save();self.save_org()
        cfg=copy.deepcopy(self.cfg);k=cfg['departments'][0]['contracts'][0]
        k['uniforms'].append(dict(id='u2',customer_product_id=11,display_name='',description='',default_qty=1,sort_order=2,photo='',active=False))
        for kid,extra in (('k2',dict(name='停止契約',active=False)),('k3',dict(name='終了契約',end_date='2000-01-01')),('k4',dict(name='未来契約',start_date='2999-01-01'))):
            x=dict(copy.deepcopy(k),id=kid,**extra);x['uniforms']=[dict(x['uniforms'][0],id=kid+'u')];cfg['departments'][0]['contracts'].append(x)
        self.library(cfg);self.assign('sapporo')
        contracts=self.live('sapporo')['config']['departments'][0]['contracts']
        self.assertEqual([x['name'] for x in contracts],['通常契約'])
        self.assertEqual([u['id'] for u in contracts[0]['uniforms']],['u1'])

    def test_intake_stop_still_hides(self):
        self.save(action='publish');self.save_org();self.library();self.assign('sapporo')
        self.scope('sapporo',action='pause')
        self.assertEqual(self.live('sapporo')['status'],'paused');self.assertEqual(self.live('sendai')['status'],'published')
        self.save(action='pause')
        self.assertEqual(self.live('sendai')['status'],'paused')


def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    for name in loader.getTestCaseNames(LiveDepartmentTest):
        if name in LiveDepartmentTest.__dict__:suite.addTest(LiveDepartmentTest(name))
    return suite


if __name__=='__main__':unittest.main()
