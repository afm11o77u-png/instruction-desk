"""QR work lifecycle, permissions, atomic inventory and retry behavior (temporary DB only)."""
import concurrent.futures
import sys
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock
import stock_qr
import qr_work
import server


class QrWorkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'stock.sqlite3'
        with closing(stock.connect(self.db)) as c, c:
            stock.initialize(c)
            cu = stock.save_customer(c, {'name': 'QR顧客', 'active': True})
            su = stock.save_supplier(c, {'name': 'QR仕入先', 'active': True})
            p = stock.save_product(c, {'code': 'QR-1', 'name': '白衣', 'supplierId': su['id'],
                'active': True, 'sizes': ['M', 'L'], 'links': [
                    {'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}]})
            self.cp = p['links'][0]['id']
            self.loc = stock.save_location(c, {'siteName': 'QR倉庫', 'active': True})['id']
            stock.register_inbound(c, {'token': 'qr-test-initial', 'customerProductId': self.cp,
                'size': 'M', 'condition': 'new', 'locationId': self.loc, 'qty': 10,
                'unitCost': 100, 'receivedOn': '2026-01-01'})
            self.qr = stock_qr.show(c, {'customerProductId': self.cp, 'size': 'M', 'condition': 'new'})['publicId']
            self.qr2 = stock_qr.show(c, {'customerProductId': self.cp, 'size': 'L', 'condition': 'new'})['publicId']
        self.worker = {'id': 12, 'name': '倉庫担当', 'role': 'warehouse'}
        self.manager = {'id': 1, 'name': '主管理者', 'role': 'owner'}

    def tearDown(self):
        self.temp.cleanup()

    def call(self, action, body=None, *, manager=False, actor=None, expected=200):
        status, result = server.api_post(self.db, 'desk', '/api/qr-work/' + action, body or {},
            can_write=manager, actor=actor or (self.manager if manager else self.worker))
        self.assertEqual(status, expected, result)
        return result

    def scan(self, job=None, qr=None, **kw):
        body = {'qr': qr or self.qr, 'token': str(uuid.uuid4())}
        if job:
            body.update(id=job['id'], version=job['version'])
        return self.call('scan', body, **kw)['job']

    def edit_body(self, j, **changes):
        return {'id': j['id'], 'version': j['version'], 'lines': j['lines'], 'direction': j['direction'], **changes}

    def qty(self):
        with closing(stock.connect(self.db)) as c:
            return c.execute('SELECT SUM(qty_remaining) FROM lots').fetchone()[0]

    def test_complete_then_pc_confirm_only_once(self):
        j = self.scan()
        self.assertEqual(j['direction'], 'outbound')
        j = self.scan(j)
        self.assertEqual((j['lines'][0]['qty'], j['lines'][0]['scanCount']), (2, 2))
        self.assertEqual(self.qty(), 10)
        # Reload from another device resumes the user's saved collection.
        self.assertEqual(self.call('state')['jobs'][0]['id'], j['id'])
        j['lines'][0]['qty'] = 3
        j = self.call('complete', self.edit_body(j))['job']
        self.assertTrue(j['completed_at'])
        self.assertEqual(self.qty(), 10)
        self.call('confirm', {'id': j['id'], 'version': j['version']}, expected=403)
        self.call('confirm', {'id': j['id'], 'version': j['version'], '_canWrite': True,
                             '_actor': self.manager}, expected=403)
        r = self.call('confirm', {'id': j['id'], 'version': j['version']}, manager=True)
        self.assertEqual(r['job']['status'], 'confirmed')
        self.assertEqual(self.qty(), 7)
        self.assertTrue(self.call('confirm', {'id': j['id'], 'version': j['version']}, manager=True)['duplicate'])
        self.assertEqual(self.qty(), 7)
        self.call('edit', self.edit_body(r['job']), manager=True, expected=400)

    def test_scan_token_retry_and_concurrent_retries(self):
        body = {'qr': self.qr, 'token': str(uuid.uuid4())}
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.call('scan', body), range(2)))
        self.assertEqual(results[0]['job']['id'], results[1]['job']['id'])
        self.assertEqual(self.call('state')['jobs'][0]['lines'][0]['qty'], 1)
        self.assertEqual(self.qty(), 10)

    def test_stale_edit_and_private_active_job(self):
        old = self.scan()
        j = self.scan(old)
        self.call('edit', self.edit_body(old), expected=400)
        self.assertEqual(self.call('state', manager=True)['jobs'], [])
        self.call('edit', self.edit_body(j), manager=True, expected=403)
        self.call('scan', {'id': j['id'], 'version': j['version'], 'token': str(uuid.uuid4()), 'qr': self.qr},
                  actor={'id': 13, 'name': '他の倉庫担当'}, expected=403)

    def test_new_job_after_completion_and_zero_is_excluded(self):
        j = self.scan()
        j = self.scan(j, self.qr2)
        j['lines'][1]['qty'] = 0
        j = self.call('complete', self.edit_body(j))['job']
        self.call('confirm', {'id': j['id'], 'version': j['version']}, manager=True)
        self.assertEqual(self.qty(), 9)
        new = self.scan()
        self.assertNotEqual(j['id'], new['id'])

    def test_all_or_nothing_on_shortage(self):
        j = self.scan()
        j = self.scan(j, self.qr2)
        j['lines'][1]['locationId'] = self.loc
        j = self.call('complete', self.edit_body(j))['job']
        self.call('confirm', {'id': j['id'], 'version': j['version']}, manager=True, expected=400)
        self.assertEqual(self.qty(), 10)
        self.assertEqual(self.call('state', manager=True)['jobs'][0]['status'], 'completed')
        with closing(stock.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM moves WHERE kind='outbound'").fetchone()[0], 0)

    def test_inbound_requires_price_and_location_then_can_confirm(self):
        j = self.scan(qr=self.qr2)
        j = self.call('complete', self.edit_body(j, direction='inbound'))['job']
        self.call('confirm', {'id': j['id'], 'version': j['version']}, manager=True, expected=400)
        j['lines'][0].update(locationId=self.loc, unitCost=150, qty=4)
        j = self.call('edit', self.edit_body(j), manager=True)['job']
        self.call('confirm', {'id': j['id'], 'version': j['version']}, manager=True)
        self.assertEqual(self.qty(), 14)

    def test_unmatched_qr_does_not_start_job(self):
        self.call('scan', {'qr': 'unknown-qr', 'token': str(uuid.uuid4())}, expected=400)
        self.assertEqual(self.call('state')['jobs'], [])
        self.assertEqual(self.qty(), 10)

    def test_text_transfer_is_independent_idempotent_and_keeps_exact_text(self):
        text = 'スマホの説明\n  空白も維持\n<script>alert("test")</script>\n' + '長い文章です。' * 1000
        body = {'id': str(uuid.uuid4()), 'text': text}
        result = self.call('text-send', body)
        self.assertEqual(result['message']['body'], text)
        self.assertTrue(self.call('text-send', body)['duplicate'])
        messages = self.call('text-list')['messages']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['body'], text)
        self.assertEqual(self.call('text-list', manager=True)['messages'], [])
        self.call('text-send', dict(body, text='異なる本文'), expected=400)
        self.call('text-send', {'id': str(uuid.uuid4()), 'text': 'x' * 50001}, expected=400)
        self.call('text-send', {'id': str(uuid.uuid4()), 'text': '   '}, expected=400)
        self.assertEqual(self.qty(), 10)
        self.assertEqual(self.call('state')['jobs'], [])

    def test_delete_wrong_row_and_rescan_without_changing_inventory(self):
        j = self.scan()
        j = self.scan(j, self.qr2)
        body = self.edit_body(j)
        body['lines'][1] = {'key': j['lines'][1]['key'], 'deleted': True}
        j = self.call('edit', body)['job']
        self.assertEqual(len(j['lines']), 1)
        self.assertEqual(self.qty(), 10)

        j = self.scan(j, self.qr2)
        self.assertEqual(len(j['lines']), 2)
        self.assertEqual(j['lines'][1]['qty'], 1)
        self.assertEqual(self.qty(), 10)

    def submission(self, **changes):
        return {'id': str(uuid.uuid4()), 'startedAt': '2026-11-19T01:00:00Z',
                'completedAt': '2026-11-19T01:15:00Z',
                'lines': [{'qr': self.qr, 'qty': 3, 'scanCount': 4}], **changes}

    def test_device_submission_receipt_dedup_and_office_confirmation(self):
        body = self.submission()
        self.assertEqual(self.call('state', {'inbox': True}, manager=True)['jobs'], [])
        j = self.call('submit', body)['job']
        self.assertEqual(j['status'], 'completed')
        self.assertEqual(j['actor_id'], str(self.worker['id']))
        self.assertEqual(j['lines'][0]['qty'], 3)
        self.assertEqual(self.qty(), 10)
        self.assertEqual(self.call('state', {'inbox': True}, manager=True)['jobs'][0]['id'], j['id'])
        self.assertTrue(self.call('submit', body)['duplicate'])
        self.call('confirm', {'id': j['id'], 'version': j['version']}, expected=403)
        j['lines'][0]['qty'] = 2
        self.call('edit', self.edit_body(j), expected=403)
        j = self.call('edit', self.edit_body(j), manager=True)['job']
        # An acknowledgment lost before office edits still deduplicates the original upload.
        self.assertTrue(self.call('submit', body)['duplicate'])
        self.call('confirm', {'id': j['id'], 'version': j['version']}, manager=True)
        self.assertEqual(self.qty(), 8)
        self.assertTrue(self.call('submit', body)['duplicate'])
        self.assertEqual(self.qty(), 8)

    def test_inbox_hides_own_in_progress_and_catalog_has_no_stock_quantities(self):
        self.scan()
        self.assertEqual(self.call('state', {'inbox': True})['jobs'], [])
        catalog = self.call('catalog')
        self.assertIn(self.qr, [e['qr'] for e in catalog['entries']])
        import json
        self.assertNotIn('qtyRemaining', json.dumps(catalog))
        checked = self.call('catalog', {'revision': catalog['revision']})
        self.assertTrue(checked['notModified'])
        self.assertNotIn('entries', checked)
        with closing(stock.connect(self.db)) as c, c:
            c.execute("UPDATE products SET name='変更した白衣' WHERE id=(SELECT product_id FROM customer_products WHERE id=?)", (self.cp,))
        updated = self.call('catalog', {'revision': catalog['revision']})
        self.assertNotEqual(updated['revision'], catalog['revision'])
        self.assertTrue(updated['entries'])
        self.assertEqual(self.qty(), 10)


    def test_bad_or_changed_submission_is_not_received(self):
        body = self.submission()
        body['lines'].append({'qr': 'unknown-qr', 'qty': 1, 'scanCount': 1})
        self.call('submit', body, expected=400)
        self.assertEqual(self.call('state', {'inbox': True}, manager=True)['jobs'], [])
        body = self.submission()
        self.call('submit', body)
        body['lines'][0]['qty'] = 9
        self.call('submit', body, expected=400)
        self.call('submit', body, actor={'id': 13, 'name': '他の担当'}, expected=400)
        self.assertEqual(self.qty(), 10)

    def test_inherited_server_draft_is_not_received_until_submission(self):
        old = self.scan()
        body = self.submission(id=old['id'], sourceVersion=old['version'])
        wrong = dict(body, sourceVersion=old['version'] + 1)
        self.call('submit', wrong, expected=400)
        self.assertEqual(self.call('state', {'inbox': True}, manager=True)['jobs'], [])
        j = self.call('submit', body)['job']
        self.assertEqual(j['id'], old['id'])
        self.assertEqual(j['status'], 'completed')
        self.assertTrue(self.call('submit', body)['duplicate'])
        self.assertEqual(len(self.call('state', {'inbox': True}, manager=True)['jobs']), 1)
        self.assertEqual(self.qty(), 10)

    def test_old_qr_json_and_id_increment_same_line_and_mismatch_is_rejected(self):
        import qr_link_import
        import json
        with closing(stock.connect(self.db)) as c, c:
            qr_link_import.ensure_schema(c)
            cp = stock.resolve_customer_product(c, self.cp)
            c.execute('''INSERT INTO qr_stock_links(legacy_stock_id,legacy_customer_id,legacy_maker_id,
                customer_name,item_num,item_name,customer_id,product_id,customer_product_id,
                size_label,condition,location_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('old-test', 'customer-a', 'maker-a', 'QR顧客', 'QR-1', '白衣', cp['customer_id'],
                 cp['product_id'], self.cp, 'M', 'new', self.loc, 't', 't'))
        j = self.scan(qr='old-test')
        j = self.scan(j, json.dumps({'id': 'old-test', 'customer_id': 'customer-a'}))
        self.assertEqual(len(j['lines']), 1)
        self.assertEqual(j['lines'][0]['qty'], 2)
        self.call('scan', {'id': j['id'], 'version': j['version'], 'token': str(uuid.uuid4()),
                          'qr': json.dumps({'id': 'old-test', 'customer_id': 'wrong'})}, expected=400)
        self.assertEqual(self.qty(), 10)

    def test_normal_qr_display_preserves_original_legacy_payload_without_issuing(self):
        import qr_link_import
        import json
        raw = json.dumps({'id': 'box-old', 'customer_id': 'old-c', 'maker_id': 'old-m'}, ensure_ascii=False)
        with closing(stock.connect(self.db)) as c, c:
            qr_link_import.ensure_schema(c)
            cp = stock.resolve_customer_product(c, self.cp)
            for legacy_id in ['box-old', 'box-old-2']:
                payload = raw if legacy_id == 'box-old' else json.dumps({'id': legacy_id, 'customer_id': 'old-c', 'maker_id': 'old-m'})
                c.execute('''INSERT INTO qr_stock_links(legacy_stock_id,legacy_customer_id,legacy_maker_id,
                    customer_id,product_id,customer_product_id,size_label,condition,location_id,qr_raw,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (legacy_id,'old-c','old-m',cp['customer_id'],cp['product_id'],self.cp,'M','new',self.loc,payload,'t','t'))
            before = c.execute('SELECT COUNT(*) FROM stock_qr_codes').fetchone()[0]
        body = {'preferLegacy': True, 'customerProductId': self.cp, 'size': 'M', 'condition': 'new', 'locationId': self.loc}
        status, choices = server.api_post(self.db, 'desk', '/api/stock-qr', body, can_write=False, actor=self.worker)
        self.assertEqual(status, 200)
        self.assertEqual(len(choices['legacyChoices']), 2)
        status, shown = server.api_post(self.db, 'desk', '/api/stock-qr', dict(body, legacyStockId='box-old'), can_write=False, actor=self.worker)
        self.assertEqual(status, 200)
        self.assertEqual(shown['publicId'], raw)
        self.assertTrue(shown['legacy'])
        self.assertFalse(shown['issued'])
        status, missing = server.api_post(self.db, 'desk', '/api/stock-qr', dict(body, size='L'), can_write=True, actor=self.manager)
        self.assertEqual(status, 400)
        self.assertFalse(missing['issued'])
        resolved = self.call('resolve', {'qr': raw})
        self.assertEqual(resolved['entry']['qr'], 'box-old')
        self.assertEqual(resolved['entry']['target']['productName'], '白衣')
        self.call('resolve', {'qr': 'nqr1.' + 'f' * 32}, expected=400)
        with closing(stock.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM stock_qr_codes').fetchone()[0], before)
            self.assertEqual(c.execute("SELECT qr_raw FROM qr_stock_links WHERE legacy_stock_id='box-old'").fetchone()[0], raw)
        self.assertEqual(self.qty(), 10)

    def test_legacy_qr_is_selected_by_exact_imported_lot_even_when_price_changes(self):
        import json
        import qr_link_import
        with closing(stock.connect(self.db)) as c, c:
            qr_link_import.ensure_schema(c)
            cp = stock.resolve_customer_product(c, self.cp)
            first = c.execute('SELECT id FROM lots ORDER BY id LIMIT 1').fetchone()[0]
            second = stock.register_inbound(c, {'token':'second-price-test','customerProductId':self.cp,
                'size':'M','condition':'new','locationId':self.loc,'qty':4,'unitCost':200,'receivedOn':'2026-02-01'})['lotId']
            for number, lot, old_id in [(1,first,'box-price-a'),(2,second,'box-price-b')]:
                c.execute('''INSERT INTO qr_stock_links(legacy_stock_id,customer_id,product_id,
                    customer_product_id,size_label,condition,location_id,qr_raw,file_sha256,source_row_no,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (old_id,cp['customer_id'],cp['product_id'],self.cp,'M','new',self.loc,json.dumps({'id':old_id}),'same-file',number,'t','t'))
                c.execute('''INSERT INTO legacy_csv_row_maps(product_map_key,stock_map_key,file_sha256,
                    source_row_no,customer_product_id,product_id,lot_ids_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''', ('product',str(number),'same-file',number,self.cp,cp['product_id'],json.dumps([lot]),'t','t'))
            # Current cost is deliberately no longer a discriminator.
            c.execute('UPDATE lots SET unit_cost=999 WHERE id IN (?,?)', (first,second))
        for lot, old_id in [(first,'box-price-a'),(second,'box-price-b')]:
            status, shown = server.api_post(self.db,'desk','/api/stock-qr',{
                'preferLegacy':True,'lotId':lot,'customerProductId':self.cp,'size':'M','condition':'new'},
                can_write=False,actor=self.worker)
            self.assertEqual(status,200,shown)
            self.assertEqual(shown['legacyStockId'],old_id)
            self.assertNotIn('legacyChoices',shown)
        with closing(stock.connect(self.db)) as c, c:
            c.execute("UPDATE legacy_csv_row_maps SET file_sha256='other-file' WHERE source_row_no=1")
        status, result = server.api_post(self.db,'desk','/api/stock-qr',{
            'preferLegacy':True,'lotId':first,'customerProductId':self.cp,'size':'M','condition':'new'},actor=self.manager)
        self.assertEqual(status,400)
        self.assertFalse(result['issued'])
        self.assertEqual(self.qty(),14)


if __name__ == '__main__':
    unittest.main()
