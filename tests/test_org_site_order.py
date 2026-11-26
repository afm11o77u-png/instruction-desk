"""拠点の並び順（ドラッグ並べ替え）：順番は組織データの配列順で保存され、そのまま読み戻せる。"""
import copy
import unittest
from pathlib import Path
import test_web_request_settings as fixtures
import web_requests as w
import web_request_organizations as org

ROOT = Path(__file__).resolve().parents[1]


class OrgSiteOrderTest(unittest.TestCase):
    def setUp(self):
        f = fixtures.SettingsTest(); f.setUp(); self.addCleanup(f.tearDown)
        self.c, self.path, self.owner = f.c, f.path, f.owner
        self.nodes = [dict(id='co', parent_id=None, kind='company', name='ABCD社', customer_id=1),
                      dict(id='r', parent_id='co', kind='region', name='シティ', customer_id=1)] + \
                     [dict(id=f's{i}', parent_id='r', kind='site', name=f'店{i}', customer_id=1, site_type='店舗') for i in range(1, 5)]

    def save(self, nodes):
        w.save(self.c, self.path, dict(action='organization', organization=dict(nodes=nodes, customer_scopes=[], staff_scopes=[]), version=org.read(self.c)['version']), self.owner)
        return [n['id'] for n in org.read(self.c)['nodes'] if n['kind'] == 'site']

    def test_order_is_kept_and_reorder_persists(self):
        self.assertEqual(self.save(self.nodes), ['s1', 's2', 's3', 's4'])  # 既存の順番は変わらない
        moved = copy.deepcopy(self.nodes); s4 = moved.pop(5); moved.insert(2, s4)  # 店4を先頭へ
        self.assertEqual(self.save(moved), ['s4', 's1', 's2', 's3'])
        self.assertEqual(self.save(org.read(self.c)['nodes']), ['s4', 's1', 's2', 's3'])  # 他の保存でも崩れない

    def test_ui_wiring(self):
        js = (ROOT / 'request-organizations.js').read_text(encoding='utf-8')
        for part in ('function reorderSites(', 'siteDropTarget(e)', "st.after?'drop-after':'drop-before'", 'toCategoryEnd([site])', 'if(last.order)restoreOrder(last.order)'):
            self.assertIn(part, js)


if __name__ == '__main__':
    unittest.main()
