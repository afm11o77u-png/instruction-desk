import copy
import unittest
import test_web_request_settings as fixtures
from test_department_excel import book
import web_requests as w
import web_request_organizations as org
import web_request_department_registry as registry


class ExcelAssignmentTest(unittest.TestCase):
    tearDown=fixtures.SettingsTest.tearDown
    save=fixtures.SettingsTest.save
    def setUp(self):
        fixtures.SettingsTest.setUp(self);self.save()
        payload=dict(nodes=[dict(id='root',parent_id=None,kind='company',name='会社',customer_id=1,personnel_registration=False),dict(id='group',parent_id='root',kind='region',name='店舗',customer_id=1),dict(id='site',parent_id='group',kind='site',name='札幌店',customer_id=1,office_code='A')],staff_scopes=[],customer_scopes=[])
        org.save(self.c,payload,0,{1,2},{1,2,3},self.owner)

    def preview(self,source='customer',customer=None,contract=None):
        customer=customer or book([['A','札幌店 製造',0],['B','新店 精肉',0]])
        return w.save(self.c,self.path,dict(action='department_assignment_import_preview',customer_id=1,source=source,file=contract or customer,customer_file=customer),self.owner)

    def apply(self,plan,rows):
        return w.save(self.c,self.path,dict(action='department_assignment_import',customer_id=1,version=plan['version'],organization_version=plan['organization_version'],target_versions=plan['target_versions'],category_id='group',rows=rows),self.owner)

    def test_customer_preview_and_atomic_assignment_preserves_contracts(self):
        plan=self.preview();self.assertEqual(plan['rows'][0]['node_id'],'site');self.assertFalse(plan['rows'][1]['selected'])
        before=registry.library(self.c,w.read_config(self.c,1))
        with self.assertRaises(PermissionError):self.apply(plan,[dict(name='精肉',node_id='site'),dict(name='惣菜',node_id='group')])
        self.assertEqual(registry.library(self.c,w.read_config(self.c,1)),before)
        result=self.apply(plan,[dict(name='製造',node_id='site'),dict(name='精肉',node_id='site')])
        self.assertEqual(result['departmentImportResult']['assigned'],2)
        self.assertEqual(registry.library(self.c,w.read_config(self.c,1))['draft']['departments'][0]['contracts'],before['draft']['departments'][0]['contracts'])
        with self.assertRaises(ValueError):self.apply(plan,[dict(name='製造',node_id='site')])
        result=self.apply(self.preview(),[dict(name='製造',node_id='site'),dict(name='精肉',node_id='site')])
        self.assertEqual(result['departmentImportResult']['assigned'],0)

    def test_departments_only_import_registers_without_assignment(self):
        raw=book([['A','札幌店 管理',0],['B','新店 管理',0],['C','新店 ｸﾞﾛｻﾘｰ',0],['D','名前だけ',0]])
        call=lambda action,**kw:w.save(self.c,self.path,dict(action=action,customer_id=1,departments_only=True,file=raw,**kw),self.owner)
        plan=call('department_assignment_import_preview')
        rows={r['name']:r for r in plan['rows']};self.assertEqual(rows['管理']['count'],2);self.assertIn('グロサリー',rows);self.assertEqual(plan['unresolved'],1)
        before=[a for a in w.state(self.c,self.path,self.owner)['departmentAssignments']]
        result=call('department_assignment_import',version=plan['version'],rows=[dict(name='管理'),dict(name='グロサリー')])['departmentImportResult']
        self.assertEqual((result['added'],result['assigned']),(2,0))
        lib=registry.library(self.c,w.read_config(self.c,1));self.assertEqual({'管理','グロサリー'}<={d['name'] for d in lib['draft']['departments']},True)
        self.assertEqual(w.state(self.c,self.path,self.owner)['departmentAssignments'],before)  # どの拠点にも割り当てない
        again=call('department_assignment_import_preview');self.assertTrue(all(r['existing'] and not r['selected'] for r in again['rows'] if r['name'] in ('管理','グロサリー')))
        with self.assertRaises(ValueError):call('department_assignment_import',version=plan['version'],rows=[dict(name='管理')])  # 古い版は拒否
        self.assertEqual(call('department_assignment_import',version=again['version'],rows=[dict(name='ｸﾞﾛｻﾘｰ')])['departmentImportResult']['added'],0)
        with self.assertRaises(PermissionError):w.save(self.c,self.path,dict(action='department_assignment_import_preview',customer_id=1,departments_only=True,file=raw),self.other)

    def test_contract_joins_customer_code_and_rejects_unknown_sites(self):
        customer=book([['A','札幌店',0],['B','新店',0]])
        contract=book([['A','製造'],['B','精肉'],['B','惣菜']],['顧客コード','部署名'])
        plan=self.preview('contract',customer,contract)
        self.assertEqual(plan['rows'][0]['customer'],'札幌店');self.assertEqual(plan['rows'][0]['node_id'],'site')
        self.assertFalse(plan['rows'][1]['selected'])
        before=copy.deepcopy(org.read(self.c))
        with self.assertRaises(PermissionError):self.apply(plan,[dict(node_id='__new__',name='精肉')])
        self.assertEqual(org.read(self.c),before)
        result=self.apply(plan,[dict(node_id='site',name='製造')])
        self.assertEqual(result['departmentImportResult']['assigned'],1)
