import copy
import json
import unittest
from pathlib import Path
import test_web_request_settings as fixture
import web_requests as web
import common_contracts as shared
from rental_management import service as rental


class CommonContractsTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.SettingsTest(); self.f.setUp()
        self.path = Path(self.f.tmp.name) / 'rental.sqlite3'; rental.initialize(self.path)
        self.user = dict(self.f.owner, name='管理者')

    def tearDown(self):
        self.f.tearDown()

    def data(self):
        result = rental.catalog(self.f.c, self.f.path, self.user)
        shared.synchronize(self.path, result)
        return result

    def rows(self, entity, **params):
        return rental.get(self.path, self.data(), dict(entity=entity, **params))['rows']

    def test_shared_quantities_and_publication_remain_separate(self):
        self.f.save(action='publish')
        before = copy.deepcopy(web.read_config(self.f.c, 1)['publication'])
        first = self.rows('contracts')[0]
        self.assertEqual(first['lines'][0]['quantity'], 3)
        self.f.cfg['departments'][0]['contracts'][0]['uniforms'][0]['default_qty'] = 5
        self.f.cfg['departments'][0]['contracts'][0].update(service_type='クリーニングのみ', default_ownership='顧客所有', start_date='2026-01-01', notes='共通備考')
        self.f.save()
        second = self.rows('contracts')[0]
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(second['lines'][0]['quantity'], 5)
        self.assertEqual(second['service_type'], 'クリーニングのみ')
        self.assertEqual(second['notes'], '共通備考')
        self.assertEqual(web.read_config(self.f.c, 1)['publication'], before)
        with self.assertRaisesRegex(ValueError, '顧客・契約管理'):
            rental.save(self.path, self.data(), self.user, dict(first, entity='contract', active=True))
        self.assertEqual(self.rows('contracts')[0]['version'], second['version'])

    def test_current_links_and_past_loan_survive_contract_changes(self):
        self.f.save(); k = self.rows('contracts')[0]; data = self.data()
        w = rental.save(self.path, data, self.user, dict(entity='wearer', customer_id=1, employee_code='E', name='着用者', status='在籍', contract_ids=[k['id']]))['id']
        u = rental.save(self.path, data, self.user, dict(entity='unit', customer_id=1, barcode='U', customer_product_id=10, size='M', status='待機', ownership='自社所有'))['id']
        loan = rental.save(self.path, data, self.user, dict(entity='loan', action='issue', unit_id=u, wearer_id=w, contract_id=k['id'], issued_date='2026-11-01'))['id']
        self.f.cfg['departments'][0]['contracts'][0]['uniforms'][0]['default_qty'] = 9; self.f.save()
        self.assertEqual(self.rows('wearers')[0]['contract_ids'], [k['id']])
        self.assertEqual(self.rows('loans', id=loan)[0]['issued_snapshot']['lines'][0]['quantity'], 3)
        self.f.cfg['departments'][0]['contracts'] = []; self.f.save()
        self.assertFalse(self.rows('contracts')[0]['active'])
        self.assertEqual(len(self.rows('loans')), 1)
        shared.migrate(self.f.c, self.path)
        self.assertEqual(web.read_config(self.f.c, 1)['draft']['departments'][0]['contracts'], [])

    def test_independent_legacy_contract_is_imported_once_without_publishing(self):
        self.f.save(action='publish'); before = copy.deepcopy(web.read_config(self.f.c, 1)['publication'])
        data = rental.catalog(self.f.c, self.f.path, self.user); data.pop('sharedContracts')
        ident = rental.save(self.path, data, self.user, dict(entity='contract', customer_id=1, name='既存の洗濯契約', kind='その他', service_type='クリーニングのみ', default_ownership='着用者所有', start_date='2026-01-01', active=True, lines=[dict(customer_product_id=11, quantity=7)]))['id']
        shared.migrate(self.f.c, self.path); shared.migrate(self.f.c, self.path)
        profile = web.read_config(self.f.c, 1)
        imported = next(d for d in profile['draft']['departments'] if d['id'] == 'uniform-import')['contracts']
        self.assertEqual(len(imported), 1); self.assertEqual(imported[0]['uniforms'][0]['default_qty'], 7)
        self.assertEqual(profile['publication'], before)
        after = next(k for k in self.rows('contracts') if k['id'] == ident)
        self.assertEqual(after['default_ownership'], '着用者所有')
        self.assertEqual(after['lines'][0]['quantity'], 7)

    def test_previously_linked_contract_keeps_rental_metadata_and_web_quantities(self):
        self.f.save()
        legacy=web.read_config(self.f.c,1)['draft']
        for key in ('service_type','default_ownership','start_date','end_date','notes'):legacy['departments'][0]['contracts'][0].pop(key,None)
        self.f.c.execute('UPDATE web_request_customer_settings SET draft=? WHERE customer_id=1',(json.dumps(legacy),));self.f.c.commit()
        data=rental.catalog(self.f.c,self.f.path,self.user);data.pop('sharedContracts')
        ident=rental.save(self.path,data,self.user,dict(entity='contract',customer_id=1,name='古い名称',kind='その他',service_type='クリーニングのみ',default_ownership='顧客所有',start_date='2026-01-01',active=True,source_ref='1:dep1:k1',lines=[dict(customer_product_id=10,quantity=9)]))['id']
        shared.migrate(self.f.c,self.path)
        after=next(k for k in self.rows('contracts') if k['id']==ident)
        self.assertEqual(after['lines'][0]['quantity'],3)
        self.assertEqual(after['service_type'],'クリーニングのみ')
        self.assertEqual(after['start_date'],'2026-01-01')
        self.assertEqual(len(web.read_config(self.f.c,1)['draft']['departments']),1)

    def test_scoped_contract_uses_existing_inheritance(self):
        import web_request_organizations as org
        import web_request_inheritance as inheritance
        self.f.save()
        value = org.read(self.f.c)
        value['nodes'] = [dict(id='company', kind='company', name='顧客', parent_id=None, customer_id=1), dict(id='category',kind='region',name='直属',parent_id='company',customer_id=1),dict(id='site', kind='site', name='札幌店', parent_id='category', customer_id=1)]
        # Use the production organization's normal save validation.
        org.save(self.f.c, value, value['version'], {1, 2}, {1, 2, 3}, self.user)
        parent = web.read_config(self.f.c, 1)['draft']; changed = copy.deepcopy(parent)
        changed['departments'][0]['contracts'][0]['uniforms'][0]['default_qty'] = 8
        payload = dict(mode='custom', patch=inheritance.difference(parent, changed))
        self.f.c.execute("INSERT INTO web_request_scope_settings VALUES(?,?,1,?,NULL,'draft')", ('site', 1, json.dumps(payload)))
        self.f.c.commit()
        rows = self.rows('contracts')
        self.assertEqual(sorted(k['lines'][0]['quantity'] for k in rows), [3, 8])


if __name__ == '__main__': unittest.main()
