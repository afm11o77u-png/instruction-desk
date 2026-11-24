import copy
import unittest
import test_web_request_organizations as orgtests
import web_requests as w
import web_request_organizations as org
import web_request_inheritance as inherit


class BulkScopeTest(orgtests.OrganizationTest):

    def prepare(self):
        self.save(action='publish');self.save_org()
        site=self.profile('sapporo')['draft'];site['departments'][0]['contracts'][0]['uniforms'][0]['default_qty']=2
        self.scope('sapporo',site,action='save')

    def profile(self,node):
        o=org.read(self.c);return inherit.profile(self.c,w.read_config(self.c,1),o,next(n for n in o['nodes'] if n['id']==node))

    def departments(self,node):
        return w.validate_config(self.profile(node)['draft'],w.state(self.c,self.path,self.owner),1)['departments']

    def bulk(self,source,targets,user=None,expected=None):
        exp=expected if expected is not None else (self.departments(source) if source else w.read_config(self.c,1)['draft']['departments'])
        return w.save(self.c,self.path,dict(action='save_many',customer_id=1,source_node_id=source,expected_departments=exp,
            targets=[dict(node_id=n,version=self.profile(n)['version'] if v is None else v) for n,v in targets]),user or self.owner)

    def test_copies_effective_departments_and_inherits_when_equal_to_parent(self):
        self.prepare();before=inherit.record(self.c,'sapporo')
        self.bulk('sapporo',[('sendai',None)])
        self.assertEqual(self.departments('sendai'),self.departments('sapporo'))
        self.assertEqual(inherit.record(self.c,'sendai')['draft']['mode'],'custom')
        self.assertEqual(inherit.record(self.c,'sapporo'),before)
        self.bulk(None,[('sapporo',None),('sendai',None)])
        for node in ('sapporo','sendai'):
            self.assertEqual(inherit.record(self.c,node)['draft'],dict(mode='inherit',patch={}))
            self.assertEqual(self.departments(node),w.read_config(self.c,1)['draft']['departments'])

    def test_version_conflict_rolls_back_every_target(self):
        self.prepare();before={n:inherit.record(self.c,n) for n in ('sapporo','sendai')}
        with self.assertRaisesRegex(ValueError,'仙台事業所'):self.bulk(None,[('sapporo',None),('sendai',5)])
        self.assertEqual({n:inherit.record(self.c,n) for n in ('sapporo','sendai')},before)

    def test_stale_source_permissions_and_site_only(self):
        self.prepare()
        stale=copy.deepcopy(self.departments('sapporo'));stale[0]['contracts'][0]['uniforms'][0]['default_qty']=9
        with self.assertRaisesRegex(ValueError,'別の画面'):self.bulk('sapporo',[('sendai',None)],expected=stale)
        with self.assertRaises(PermissionError):self.bulk('sapporo',[('sendai',None)],user=self.other)
        with self.assertRaises(ValueError):self.bulk('sapporo',[('north',None)])
        self.assertEqual(inherit.record(self.c,'sendai')['version'],0)


def load_tests(loader,tests,pattern):
    # Run only the bulk tests here; inherited fixture tests run in their own modules.
    suite=unittest.TestSuite()
    for name in loader.getTestCaseNames(BulkScopeTest):
        if name in BulkScopeTest.__dict__:suite.addTest(BulkScopeTest(name))
    return suite


if __name__=='__main__':unittest.main()
