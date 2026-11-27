"""WEB依頼のサーバー保存と、指示書とのひも付け（1依頼に複数の指示書）。"""
import json
import re
import unittest
from pathlib import Path
import test_web_request_settings as fixtures
import web_request_entries as e
import web_request_organizations as org

ROOT = Path(__file__).resolve().parents[1]


class EntriesTest(unittest.TestCase):
    tearDown = fixtures.SettingsTest.tearDown
    save = fixtures.SettingsTest.save

    def setUp(self):
        fixtures.SettingsTest.setUp(self); self.save()
        nodes = [dict(id='root', parent_id=None, kind='company', name='会社', customer_id=1, personnel_registration=False), dict(id='a', parent_id='root', kind='site', name='A店', customer_id=1)]
        org.save(self.c, dict(nodes=nodes, staff_scopes=[], customer_scopes=[]), 0, {1, 2}, {1, 2, 3}, self.owner)
        self.c.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY, version INTEGER, payload TEXT)')

    def req(self, **kw):
        return dict(dict(customerId=1, nodeId='a', department='管理', requestType='新規', wearer='着用者', wearerGender='女性', items=[dict(name='白衣', code='A01', size='M', qty=2, product_id=1, customer_product_id=10)]), **kw)

    def test_create_scope_status_and_links(self):
        r = e.create(self.c, self.path, self.owner, self.req())
        self.assertRegex(r['id'], r'^WR-\d{8}-001$'); self.assertEqual((r['status'], r['nodeName'], r['customerName'], r['sheets']), ('受付待ち', 'A店', '顧客A', []))
        for bad, err in ((dict(nodeId='zzz'), ValueError), (dict(customerId=99), PermissionError), (dict(items=[]), ValueError), (dict(items=[dict(name='x', qty=0)]), ValueError)):
            with self.assertRaises(err): e.create(self.c, self.path, self.owner, self.req(**bad))
        self.assertEqual(e.set_status(self.c, self.path, self.owner, dict(id=r['id'], status='受付済み'))['status'], '受付済み')
        with self.assertRaises(ValueError): e.set_status(self.c, self.path, self.owner, dict(id=r['id'], status='完了'))
        with self.assertRaises(ValueError): e.link(self.c, 'WR-NONE', 1, self.owner)  # 存在しない依頼にはひも付けない
        for oid, number in ((1, 'S-1'), (2, '')):
            self.c.execute('INSERT INTO orders VALUES(?,1,?)', (oid, json.dumps(dict(number=number, status='未着手' if number else '下書き'))))
            e.link(self.c, r['id'], oid, self.owner); e.link(self.c, r['id'], oid, self.owner)  # 再保存しても重複しない
        sheets = e.listing(self.c, self.path, self.owner)['requests'][0]['sheets']
        self.assertEqual([(s['orderId'], s['number'], s['orderStatus']) for s in sheets], [(1, 'S-1', '未着手'), (2, '', '下書き')])

    def test_wearer_gender(self):
        self.assertEqual(e.create(self.c, self.path, self.owner, self.req())['wearerGender'], '女性')
        for bad in (dict(wearerGender=''), dict(wearerGender='不明')):  # 新規は必須・値は男性/女性のみ
            with self.assertRaises(ValueError): e.create(self.c, self.path, self.owner, self.req(**bad))
        self.assertEqual(e.create(self.c, self.path, self.owner, self.req(requestType='サイズ交換', wearerGender=''))['wearerGender'], '')  # ほかは任意
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8'); cp = (ROOT / 'contract-products.js').read_text(encoding='utf-8')
        self.assertIn("wearerGender:wearerGender()", js); self.assertIn("r.wearerGender||'—'", js); self.assertIn("sex:r.wearerGender||''", cp)
        self.assertIn("u.searchParams.delete('web_request')", cp); self.assertIn('.then(dropParams)', cp); self.assertIn("NO_PROCESSING.has(x.embroidery||'')", cp)  # 再読み込みで再度開かない・加工未整理は備考に出さない

    def test_wiring(self):
        server = (ROOT / 'server.py').read_text(encoding='utf-8'); js = (ROOT / 'request-web.js').read_text(encoding='utf-8'); cp = (ROOT / 'contract-products.js').read_text(encoding='utf-8')
        self.assertIn("web_request_entries.link(c, payload['webRequestId'], oid, current)", server)
        self.assertNotIn('localStorage.setItem(key', js); self.assertIn('data-sheet=', js); self.assertIn('指示書作成済み', js)
        self.assertIn("'破損交換':'消耗・破損交換'", cp); self.assertIn('要確認：手入力の制服', cp); self.assertIn("input.name='webRequestId'", cp)


if __name__ == '__main__':
    unittest.main()
