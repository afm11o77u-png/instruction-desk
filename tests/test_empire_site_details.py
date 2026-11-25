"""Customer codes, hem-processing flags, delivery days, and site details."""
from contextlib import closing
import copy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'stock-desk'))

import stock
import test_web_request_settings as fixtures
import web_requests as w
import web_request_organizations as org
import web_request_site_details as details


class CustomerCodeMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'stock.sqlite3'
        self.c = stock.connect(self.path)
        stock.initialize(self.c)
        # existing-like rows: codes empty, office_code already used for 販管
        self.c.execute("DELETE FROM customers")
        self.c.executemany(
            "INSERT INTO customers(id,name,kana,address,phone,contact,active,created_at,updated_at,office_code,customer_code) "
            "VALUES(?,?,?,?,?,?,1,'t','t',?,'')",
            [
                (10, '後から登録', '', '', '', '', '9120'),
                (2, '先に登録', '', '', '', '', '4245'),
                (5, '中間', '', '', '', '', ''),
            ],
        )
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_assigns_codes_by_id_order_and_leaves_office_code(self):
        stock.assign_customer_codes(self.c)
        self.c.commit()
        rows = {r['id']: r for r in self.c.execute('SELECT id,customer_code,office_code,name FROM customers')}
        self.assertEqual(rows[2]['customer_code'], 'C0001')
        self.assertEqual(rows[5]['customer_code'], 'C0002')
        self.assertEqual(rows[10]['customer_code'], 'C0003')
        self.assertEqual(rows[2]['office_code'], '4245')
        self.assertEqual(rows[10]['office_code'], '9120')
        stock.assign_customer_codes(self.c)
        again = {r['id']: r['customer_code'] for r in self.c.execute('SELECT id,customer_code FROM customers')}
        self.assertEqual(again, {2: 'C0001', 5: 'C0002', 10: 'C0003'})

    def test_does_not_overwrite_existing_code_and_new_gets_next(self):
        stock.assign_customer_codes(self.c)
        self.c.execute("UPDATE customers SET customer_code='C0099' WHERE id=10")
        self.c.commit()
        stock.assign_customer_codes(self.c)
        self.assertEqual(self.c.execute('SELECT customer_code FROM customers WHERE id=10').fetchone()[0], 'C0099')
        row = stock.save_customer(self.c, dict(name='新規会社', kana='', address='', phone='', contact=''))
        self.assertEqual(row['customerCode'], 'C0003')  # 1 and 2 taken, 3 free because 10 is C0099
        self.assertEqual(row['officeCode'], '')
        with self.assertRaises(ValueError):
            stock.save_customer(self.c, dict(id=row['id'], name='新規会社', customerCode='C0001', updatedAt=row.get('updatedAt')))

    def test_user_edit_rejects_duplicate_and_invalid_format(self):
        stock.assign_customer_codes(self.c)
        with self.assertRaises(ValueError):
            stock.save_customer(self.c, dict(id=2, name='先に登録', customerCode='C0002'))
        with self.assertRaises(ValueError):
            stock.save_customer(self.c, dict(id=2, name='先に登録', customerCode='FO-S011-Ho'))
        saved = stock.save_customer(self.c, dict(id=2, name='先に登録', customerCode='C1000'))
        self.assertEqual(saved['customerCode'], 'C1000')
        self.assertEqual(self.c.execute('SELECT office_code FROM customers WHERE id=2').fetchone()[0], '4245')


class HemAndDeliveryTest(fixtures.SettingsTest):
    def test_hem_processing_round_trip_and_validation(self):
        u = self.cfg['departments'][0]['contracts'][0]['uniforms'][0]
        u['hem_processing'] = True
        u['processing_days'] = 5
        self.save()
        saved = w.read_config(self.c, 1)['draft']['departments'][0]['contracts'][0]['uniforms'][0]
        self.assertTrue(saved['hem_processing'])
        self.assertEqual(saved['processing_days'], 5)
        # flag off clears days
        u2 = copy.deepcopy(saved)
        u2['hem_processing'] = False
        u2['processing_days'] = 5
        self.cfg['departments'][0]['contracts'][0]['uniforms'] = [u2]
        self.save()
        saved = w.read_config(self.c, 1)['draft']['departments'][0]['contracts'][0]['uniforms'][0]
        self.assertFalse(saved['hem_processing'])
        self.assertEqual(saved['processing_days'], 0)
        bad = copy.deepcopy(self.cfg)
        bad['departments'][0]['contracts'][0]['uniforms'][0]['hem_processing'] = True
        bad['departments'][0]['contracts'][0]['uniforms'][0]['processing_days'] = 0
        with self.assertRaises(ValueError):
            self.save(bad)

    def test_delivery_fields_round_trip_and_rules(self):
        payload = dict(nodes=[
            dict(id='company', parent_id=None, kind='company', name='ABCD社', customer_id=1),
            dict(id='north', parent_id='company', kind='region', name='北海道地区', customer_id=1),
            dict(id='sapporo', parent_id='north', kind='site', name='札幌店', customer_id=1, site_type='店舗',
                 delivery_method='自社集配', collection_days=['月', '木'], postal_code='060-0001', address='札幌', fax='011-1', email='a@b.c', memo='メモ'),
        ], customer_scopes=[], staff_scopes=[])
        w.save(self.c, self.path, dict(action='organization', organization=payload, version=org.read(self.c)['version']), self.owner)
        saved = next(n for n in org.read(self.c)['nodes'] if n['id'] == 'sapporo')
        self.assertEqual(saved['delivery_method'], '自社集配')
        self.assertEqual(saved['collection_days'], ['月', '木'])
        self.assertEqual(saved['postal_code'], '060-0001')
        invalid = copy.deepcopy(payload)
        invalid['nodes'][2]['delivery_method'] = '福山便'
        invalid['nodes'][2]['collection_days'] = ['月']
        with self.assertRaises(ValueError):
            w.save(self.c, self.path, dict(action='organization', organization=invalid, version=org.read(self.c)['version']), self.owner)
        self.assertEqual(next(n for n in org.read(self.c)['nodes'] if n['id'] == 'sapporo')['delivery_method'], '自社集配')


class SiteDetailsSaveTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.SettingsTest();self.f.setUp()
        self.c,self.path,self.owner=self.f.c,self.f.path,self.f.owner
        with closing(stock.connect(self.path)) as s, s:
            cols={r["name"] for r in s.execute("PRAGMA table_info(customers)")}
            for name in ("address","phone","contact","created_at","updated_at","office_code"):
                if name not in cols:
                    default="'t'" if name in ("created_at","updated_at") else "''"
                    s.execute("ALTER TABLE customers ADD COLUMN %s TEXT NOT NULL DEFAULT %s"%(name,default))
            stock.ensure_customer_profile_columns(s)
        org_payload=dict(nodes=[
            dict(id="company",parent_id=None,kind="company",name="顧客A",customer_id=1),
            dict(id="area",parent_id="company",kind="region",name="地区",customer_id=1),
            dict(id="site",parent_id="area",kind="site",name="札幌店",customer_id=1,site_type="店舗"),
        ],customer_scopes=[],staff_scopes=[])
        w.save(self.c,self.path,dict(action="organization",organization=org_payload,version=org.read(self.c)["version"]),self.owner)

    def tearDown(self):
        self.f.tearDown()

    def test_company_and_site_details_and_unique_code(self):
        loaded = w.save(self.c, self.path, dict(action='load_site_details', customer_id=1), self.owner)
        self.assertTrue(loaded['customer']['customerCode'].startswith('C'))
        code = loaded['customer']['customerCode']
        first = w.save(self.c, self.path, dict(
            action='save_site_details', customer_id=1, node_id='company',
            orgVersion=loaded['orgVersion'], customerUpdatedAt=loaded['customerUpdatedAt'],
            customerCode=code, officialName='顧客A本社', kana='コキャクエー', postalCode='060-0001',
            address='札幌市', phone='011-1', fax='011-2', contact='山田', email='a@example.test', memo='注意',
        ), self.owner)
        self.assertEqual(first['customer']['name'], '顧客A本社')
        self.assertEqual(first['customer']['customerCode'], code)
        self.assertEqual(first['customer']['postalCode'], '060-0001')
        company = next(n for n in first['nodes'] if n['kind'] == 'company')
        self.assertEqual(company['name'], '顧客A本社')
        self.assertEqual(company['address'], '札幌市')
        # office_code untouched
        with closing(stock.connect(self.path)) as s:
            row = s.execute('SELECT office_code,customer_code FROM customers WHERE id=1').fetchone()
            self.assertEqual(row['office_code'] or '', '')
            self.assertEqual(row['customer_code'], code)
        site = w.save(self.c, self.path, dict(
            action='save_site_details', customer_id=1, node_id='site',
            orgVersion=first['orgVersion'], customerUpdatedAt=first['customerUpdatedAt'],
            officialName='札幌本店', kana='サッポロ', postalCode='060-0002', address='北区',
            phone='011-3', fax='', contact='佐藤', email='', memo='店舗メモ',
        ), self.owner)
        node = next(n for n in site['nodes'] if n['id'] == 'site')
        self.assertEqual(node['name'], '札幌本店')
        self.assertEqual(node['postal_code'], '060-0002')
        self.assertEqual(node['memo'], '店舗メモ')
        # site save must not change company code
        self.assertEqual(site['customer']['customerCode'], code)
        loaded2 = details.load(self.c, self.path, 2)
        with self.assertRaises(ValueError):
            w.save(self.c, self.path, dict(
                action='save_site_details', customer_id=1, node_id='company',
                orgVersion=site['orgVersion'], customerUpdatedAt=site['customerUpdatedAt'],
                customerCode=loaded2['customer']['customerCode'], officialName='顧客A本社', kana='',
                postalCode='060-0001', address='札幌市', phone='', fax='', contact='', email='', memo='',
            ), self.owner)
        free = w.save(self.c, self.path, dict(
            action='save_site_details', customer_id=1, node_id='company',
            orgVersion=site['orgVersion'], customerUpdatedAt=site['customerUpdatedAt'],
            customerCode='C8888', officialName='顧客A本社', kana='', postalCode='060-0001',
            address='札幌市', phone='', fax='', contact='', email='', memo='',
        ), self.owner)
        self.assertEqual(free['customer']['customerCode'], 'C8888')




class SourceAndHtmlGuardTest(unittest.TestCase):
    def test_request_web_html_untouched_and_sources_have_hooks(self):
        html = (ROOT / 'request-web.html').read_text(encoding='utf-8')
        self.assertNotIn('id="details"', html)
        self.assertNotIn('hem-processing', html)
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        self.assertIn('data-tab="details"', js)
        self.assertIn('裾上げ（他）', js)
        orgjs = (ROOT / 'request-organizations.js').read_text(encoding='utf-8')
        self.assertIn('すべてチェック', orgjs)
        self.assertIn('bindRubberBand', orgjs)
        self.assertIn('自社集配', orgjs)
        self.assertNotIn('legacy_customer_links', (ROOT / 'web_request_site_details.py').read_text(encoding='utf-8'))

    def test_details_page_main_uses_full_width(self):
        css=(ROOT/'request-web.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'body\.desk-common-page\[data-request-page=details\] main\{[^}]*max-width:\s*none')
        self.assertIn('main{max-width:1250px', css)
        # other customer-contracts tabs keep their own width rules
        self.assertIn('body.desk-common-page[data-request-page=settings] main{max-width:none', css)
        self.assertIn('body[data-request-page=organization] main{max-width:none', css)


if __name__ == '__main__':
    unittest.main()
