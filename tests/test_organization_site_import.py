"""拠点の一括取り込み（RW-30）：権限、直下の拠点の保存と控え、画面の配置。"""
import copy
import unittest
from pathlib import Path
import test_web_request_settings as fixtures
import web_requests as w
import web_request_organizations as org

ROOT = Path(__file__).resolve().parents[1]


class SiteImportTest(unittest.TestCase):
    def setUp(self):
        f = fixtures.SettingsTest(); f.setUp(); self.addCleanup(f.tearDown)
        self.c, self.path, self.owner, self.sales = f.c, f.path, f.owner, f.sales
        self.organization = dict(nodes=[
            dict(id='company', parent_id=None, kind='company', name='ABCD社', customer_id=1),
            dict(id='north', parent_id='company', kind='region', name='北海道地区', customer_id=1),
            dict(id='sapporo', parent_id='north', kind='site', name='札幌店', customer_id=1, site_type='店舗'),
        ], customer_scopes=[], staff_scopes=[])

    def save_org(self):
        return w.save(self.c, self.path, dict(action='organization', organization=self.organization, version=org.read(self.c)['version']), self.owner)

    def test_preview_is_admin_only_and_save_keeps_backup(self):
        with self.assertRaises(PermissionError):
            w.save(self.c, self.path, dict(action='organization_site_import_preview', customer_id=1, file=''), self.sales)
        self.save_org()
        before = org.read(self.c)
        p = copy.deepcopy(self.organization)
        p['nodes'].append(dict(id='new', parent_id='company', kind='site', name='北雄ラッキー篠路', customer_id=1, site_type='その他'))
        w.save(self.c, self.path, dict(action='organization', organization=p, version=before['version'], backup=True), self.owner)
        saved = org.read(self.c)
        self.assertEqual(next(n for n in saved['nodes'] if n['id'] == 'new')['parent_id'], 'company')
        row = self.c.execute('SELECT previous_version FROM web_request_org_migrations ORDER BY id DESC').fetchone()
        self.assertEqual(row[0], before['version'])

    def test_ui_wiring(self):
        js = (ROOT / 'request-organizations.js').read_text(encoding='utf-8')
        self.assertIn("btn.textContent='拠点の一括取り込み'", js)
        self.assertIn('sk.some(k=>keys.has(k))', js)  # 登録済みの拠点（店の有無を無視）・営業所コードは追加しない
        self.assertIn('{backup:siteImported}', js)
        cd = (ROOT / 'collection-days-import.js').read_text(encoding='utf-8')
        self.assertIn("window.orgImportActions?.()", cd)
        self.assertIn("box=document.createElement('dialog');box.id='collection-import'", cd)

    def test_extra_terms_add_candidates_and_are_remembered(self):
        import web_request_site_import_terms as terms
        from test_department_excel import book
        self.save_org()
        raw = book([['A1', 'ABCD社 札幌東店', 0], ['A2', 'ｼﾃｨ あばしり店', 0], ['A3', '他社 本店', 0]])
        call = lambda **kw: w.save(self.c, self.path, dict(action='organization_site_import_preview', customer_id=1, file=raw, **kw), self.owner)
        self.assertEqual([x['name'] for x in call()['candidates']], ['札幌東店'])  # 会社名で始まる行だけ
        plan = call(extra_terms='シティ、 シティ ラッキーマート')
        self.assertEqual(sorted(x['name'] for x in plan['candidates']), ['シティ あばしり店', '札幌東店'])
        self.assertEqual(plan['extra_terms'], ['シティ', 'ラッキーマート'])
        self.assertEqual(w.save(self.c, self.path, dict(action='organization_site_import_terms', customer_id=1), self.owner)['extra_terms'], ['シティ', 'ラッキーマート'])
        self.assertEqual(len(call()['candidates']), 2)  # 次回は覚えた名前で探す
        with self.assertRaises(PermissionError):
            w.save(self.c, self.path, dict(action='organization_site_import_terms', customer_id=1), self.sales)
        with self.assertRaises(ValueError):
            terms.parse('あ' * 41)


if __name__ == '__main__':
    unittest.main()
