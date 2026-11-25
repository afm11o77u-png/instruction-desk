"""集配曜日フラグの一括取り込み（RW-29）と、集配曜日に日曜を含めること。"""
import base64
import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import web_request_collection_import as imp  # noqa: E402
import web_request_delivery as delivery  # noqa: E402
import web_request_organizations as organizations  # noqa: E402

NODES = [
    dict(id='co', parent_id=None, kind='company', name='北雄ラッキー', customer_id=262, site_type=''),
    dict(id='rg', parent_id='co', kind='region', name='札幌', customer_id=262, site_type=''),
    dict(id='s1', parent_id='rg', kind='site', name='北雄ラッキー篠路', customer_id=262, site_type='店舗'),
    dict(id='s2', parent_id='rg', kind='site', name='山の手店', customer_id=262, site_type='店舗', office_code='AI-103-01'),
    dict(id='s3', parent_id='rg', kind='site', name='北雄ラッキー白老', customer_id=262, site_type='店舗', delivery_method='佐川便', collection_days=[]),
    dict(id='s4', parent_id='rg', kind='site', name='北雄ラッキー岩内', customer_id=262, site_type='店舗'),
    dict(id='s5', parent_id='rg', kind='site', name='北雄ラッキー長沼', customer_id=262, site_type='店舗', delivery_method='自社集配', collection_days=['月', '木']),
]
CSV = '\n'.join([
    '顧客コード,顧客名,集配曜日フラグ,集配サイクル',
    'AI-108-01,北雄ﾗｯｷｰ篠路 管理,90100100,3号車',
    'AI-108-02,北雄ﾗｯｷｰ篠路 OS,90100100,3号車',
    'AI-103-01,北雄ﾗｯｷｰ山の手 管理,90000001,5号車',
    'AI-303-01,北雄ﾗｯｷｰ白老,90000000,',
    'AI-302-01,北雄ﾗｯｷｰ岩内,0,オリコン',
    'AI-114-01,北雄ﾗｯｷｰ長沼,91001000,10号車',
    'AI-999-01,どこにもない店,90010010,',
    'AI-998-01,北雄ﾗｯｷｰ篠路 青果,9123,',
])


def db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE web_request_org_settings (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL)')
    c.execute('INSERT INTO web_request_org_settings VALUES(1,5,?)', (json.dumps(dict(nodes=NODES, customer_scopes=[], staff_scopes=[], request_pages=[]), ensure_ascii=False),))
    c.execute('CREATE TABLE customer_accounts (id INTEGER PRIMARY KEY)')
    c.commit()
    return c


OWNER = dict(id=1, role='owner', manage=1)
DATA = dict(customers=[dict(id=262)], staff=[], organizationStaff=[])


class FlagTest(unittest.TestCase):
    def test_parse_flag(self):
        self.assertEqual(imp.parse_flag(91001000), ('days', ['月', '木']))
        self.assertEqual(imp.parse_flag('90100100'), ('days', ['火', '金']))
        self.assertEqual(imp.parse_flag('90010010'), ('days', ['水', '土']))
        self.assertEqual(imp.parse_flag('90000001'), ('days', ['日']))
        self.assertEqual(imp.parse_flag('91111111'), ('days', list('月火水木金土日')))
        for v in ('90000000', '0', '', None):
            self.assertEqual(imp.parse_flag(v), ('none', []))
        for v in ('9123', '80010010', '900100100', '90020010', 'abc'):
            self.assertEqual(imp.parse_flag(v)[0], 'invalid')

    def test_rules(self):
        self.assertEqual(imp.outcome('90010010', '7号車'), ('set', '自社集配', ['水', '土']))
        self.assertEqual(imp.outcome('90000000', ''), ('set', '福山便', []))
        self.assertEqual(imp.outcome('0', ''), ('set', '福山便', []))
        self.assertEqual(imp.outcome('0', 'オリコン')[0], 'review')
        self.assertEqual(imp.outcome('9123', '')[0], 'skip')


class ImportTest(unittest.TestCase):
    def body(self, action, **kw):
        return dict(action=action, file=base64.b64encode(CSV.encode('cp932')).decode(), filename='m.csv', **kw)

    def test_preview_then_confirm(self):
        c = db()
        before = c.execute('SELECT version,payload FROM web_request_org_settings').fetchone()[:]
        p = imp.handle(c, self.body('collection_days_import_preview'), DATA, OWNER)
        self.assertEqual(c.execute('SELECT version,payload FROM web_request_org_settings').fetchone()[:], before)  # プレビューでは保存しない
        got = {x['node_id']: (x['delivery_method'], x['collection_days'], x['match']) for x in p['changes']}
        self.assertEqual(got, {'s1': ('自社集配', ['火', '金'], '拠点名一致'), 's2': ('自社集配', ['日'], 'コード一致'), 's3': ('福山便', [], '拠点名一致')})
        self.assertEqual(p['counts']['unchanged'], 1)  # 長沼は同じ設定
        self.assertEqual([x['code'] for x in p['unmatched']], ['AI-999-01'])
        self.assertEqual([x['code'] for x in p['review']], ['AI-302-01'])
        self.assertEqual([x['code'] for x in p['skipped']], ['AI-998-01'])
        self.assertEqual(next(x for x in p['changes'] if x['node_id'] == 's3')['before'], '佐川便')
        with self.assertRaises(ValueError):  # 了承なしでは保存しない
            imp.handle(c, self.body('collection_days_import', organization_version=5, expected_changes=3), DATA, OWNER)
        with self.assertRaises(PermissionError):
            imp.handle(c, self.body('collection_days_import', confirm=True, organization_version=5, expected_changes=3), DATA, dict(id=2, role='staff', manage=0))
        self.assertEqual(c.execute('SELECT version FROM web_request_org_settings').fetchone()[0], 5)
        r = imp.handle(c, self.body('collection_days_import', confirm=True, organization_version=5, expected_changes=3), DATA, OWNER)
        self.assertEqual(r['saved'], 3)
        org = organizations.read(c)
        by = {n['id']: n for n in org['nodes']}
        self.assertEqual((by['s2']['delivery_method'], by['s2']['collection_days']), ('自社集配', ['日']))
        self.assertEqual((by['s3']['delivery_method'], by['s3']['collection_days']), ('福山便', []))
        self.assertNotIn('delivery_method', by['s4'])  # 要確認は変えない
        self.assertEqual(org['version'], 6)
        self.assertEqual(len(org['nodes']), len(NODES))  # 拠点は作らない
        backup = c.execute('SELECT previous_version,previous_payload FROM web_request_org_migrations').fetchall()
        self.assertEqual((len(backup), backup[0][0]), (1, 5))
        with self.assertRaises(ValueError):  # 古いプレビューのまま再実行しない
            imp.handle(c, self.body('collection_days_import', confirm=True, organization_version=5, expected_changes=3), DATA, OWNER)


class SiteKeyTest(unittest.TestCase):
    def test_store_suffix_and_brand_match_but_no_guessing(self):
        sites = [dict(id='a', parent_id='co', kind='site', name='篠路店'), dict(id='b', parent_id='co', kind='site', name='西野店'),
                 dict(id='c', parent_id='co', kind='site', name='星置駅前店')]
        co = lambda n: '北雄ラッキー㉈'
        row = lambda name: dict(code='X', customer=name)
        self.assertEqual(imp.match_node(row('北雄ﾗｯｷｰ篠路 管理'), sites, co)[0]['id'], 'a')
        self.assertEqual(imp.match_node(row('ﾗｯｷｰﾏｰﾄ西野 管理'), sites, co)[0]['id'], 'b')
        node, reason = imp.match_node(row('北雄ﾗｯｷｰ星置女性 管理'), sites, co)
        self.assertIsNone(node)
        self.assertIn('星置駅前店', reason)

    def test_save_rejects_same_site_with_and_without_store_suffix(self):
        nodes = NODES[:2] + [dict(id='x1', parent_id='rg', kind='site', name='篠路店', customer_id=262, site_type='店舗'),
                             dict(id='x2', parent_id='rg', kind='site', name='北雄ラッキー篠路', customer_id=262, site_type='店舗')]
        with self.assertRaises(ValueError):
            organizations.validate(dict(nodes=nodes), {262}, set(), set())


class SundayTest(unittest.TestCase):
    def test_sunday_accepted_everywhere(self):
        cfg = organizations.validate(dict(nodes=NODES[:2] + [dict(NODES[2], delivery_method='自社集配', collection_days=['日', '月'])]), {262}, set(), set())
        self.assertEqual(cfg['nodes'][2]['collection_days'], ['月', '日'])
        nodes = [dict(id='s', delivery_method='自社集配', collection_days=['日'])]
        self.assertEqual(delivery.resolve_delivery(nodes, 's'), ('自社集配', ['日']))
        self.assertTrue(delivery.is_allowed('2026-11-29', '2026-11-28', '自社集配', ['日']))  # 2026-11-29は日曜
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        self.assertIn("COLLECTION_DAYS=['月','火','水','木','金','土','日']", js)
        self.assertIn('集配曜日（月〜日・複数可）', (ROOT / 'request-organizations.js').read_text(encoding='utf-8'))
        self.assertIn('/collection-days-import.js', (ROOT / 'server.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
