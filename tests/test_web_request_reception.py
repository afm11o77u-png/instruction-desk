"""①組織構成の図から WEB依頼の受付中/停止を切り替える（reception）。"""
import json
import unittest
import test_web_request_settings as fixtures
import web_requests as w
import web_request_organizations as org
import web_request_department_registry as registry


class ReceptionTest(unittest.TestCase):
    tearDown=fixtures.SettingsTest.tearDown
    save=fixtures.SettingsTest.save
    def setUp(self):
        fixtures.SettingsTest.setUp(self);self.save()
        payload=dict(nodes=[dict(id='root',parent_id=None,kind='company',name='会社',customer_id=1,personnel_registration=False),dict(id='a',parent_id='root',kind='site',name='A店',customer_id=1),dict(id='b',parent_id='root',kind='site',name='B店',customer_id=1)],staff_scopes=[],customer_scopes=[])
        org.save(self.c,payload,0,{1,2},{1,2,3},self.owner)

    def call(self,ids,paused,user=None):
        return w.save(self.c,self.path,dict(action='reception',customer_id=1,node_ids=ids,paused=paused),user or self.owner)

    def live(self,data):
        return {p.get('node_id') or 'root':p['live']['status'] for p in data['profiles']+data['scopeProfiles'] if p['customer_id']==1}

    def test_toggle_sites_and_company(self):
        with self.assertRaises(ValueError):self.call(['a'],True)  # 部署登録のない会社は対象外（WEB依頼の設定で操作）
        registry_lib(self)
        draft_before=w.read_config(self.c,1)['draft']
        self.assertEqual(self.live(self.call(['a','b'],True)),{'root':'published','a':'paused','b':'paused'})
        self.assertEqual(self.live(self.call(['root'],True)),{'root':'paused','a':'paused','b':'paused'})
        self.assertEqual(self.live(self.call(['root'],False)),{'root':'published','a':'paused','b':'paused'})  # 店の停止は残る
        self.assertEqual(self.live(self.call(['a'],False)),{'root':'published','a':'published','b':'paused'})
        self.assertEqual(w.read_config(self.c,1)['draft'],draft_before)  # 内容は変えない
        with self.assertRaises(PermissionError):self.call(['zzz'],True)
        with self.assertRaises(ValueError):self.call(['a','a'],True)


def registry_lib(t):
    """部署ライブラリを作って「部署登録のある会社」にする。"""
    t.c.execute("INSERT INTO web_request_department_library VALUES(1,1,?)",(json.dumps(dict(departments=[])),));t.c.commit()


if __name__ == '__main__':
    unittest.main()
