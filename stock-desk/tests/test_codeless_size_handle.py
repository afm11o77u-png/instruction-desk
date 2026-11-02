"""部品・加工サービス: サイズ・色・取扱を台帳取込で作らない（テストDBのみ）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import legacy_csv_bulk_import as bulk  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as proc_svc  # noqa: E402
import stock  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv, newdb  # noqa: E402

ROWS = ['よつ葉乳業,株サーヴォ,,刺繍加工,',
        'よつ葉乳業,丸和,,刺繍加工,L',
        'よつ葉乳業,株サーヴォ,DCP600,作業服上(刺繍),L',
        'よつ葉乳業,株サーヴォ,DCP600,作業服上(刺繍),M',
        'よつ葉乳業,YKK,SN10,スナッパー(赤),10mm',
        'よつ葉乳業,YKK,SN10,スナッパー(白),12mm',
        'よつ葉乳業,YKK,,ボタン,',
        'よつ葉乳業,YKK,,ボタン,L']


class Base(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def run_phase(self, phase, tok):
        b = {'importMode': 'products', 'name': 'n.csv', 'file': b64(ledger_csv(ROWS)),
             '_canWrite': True, '_actor': {'name': 't'}}
        if phase:
            b['productPhase'] = phase
        p = pli.build_preview(self.c, b)
        if p['rows']:
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(self.c, {**b, 'commitToken': tok,
                                            'previewFingerprint': p['previewFingerprint']}, db_path=self.path)
        return p

    def products(self):
        out = {}
        for r in self.c.execute('SELECT id,name,color,category FROM products'):
            sizes = [x[0] for x in self.c.execute('SELECT label FROM product_sizes WHERE product_id=?', (r[0],))]
            hc = self.c.execute('SELECT COUNT(*) FROM customer_products WHERE product_id=?', (r[0],)).fetchone()[0]
            out[r[1]] = (r[2], r[3], sizes, hc)
        return out


class ImportTest(Base):
    def check(self, prev_rows):
        for r in prev_rows:
            pr = r.get('product') or {}
            if pr.get('sizeHidden'):
                self.assertNotEqual(pr.get('sizeAction'), 'add')
                self.assertNotEqual(r['status'], pli.STATUS_SIZE_ADD)
                self.assertNotEqual((r.get('handle') or {}).get('action'), 'create')
            self.assertFalse([x for x in r.get('reasons') or [] if 'サイズが空' in x])
        got = self.products()
        self.assertEqual(got['刺繍加工'], ('', stock.CATEGORY_PROCESS_SERVICE, ['—'], 0))
        self.assertEqual(got['スナッパー加工'], ('', stock.CATEGORY_PROCESS_SERVICE, ['—'], 0))
        self.assertEqual(got['ボタン'], ('', stock.CATEGORY_PARTS, ['F'], 0))
        self.assertEqual(got['作業服上'][2], ['L', 'M'])
        self.assertEqual(got['作業服上'][3], 1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM customer_product_processes').fetchone()[0], 1)

    def test_phases(self):
        p1 = self.run_phase('parts', 'tok-cs-1')
        p2 = self.run_phase('goods', 'tok-cs-2')
        self.check(p1['rows'] + p2['rows'])

    def test_bulk(self):
        p = self.run_phase('', 'tok-cs-3')
        self.check(p['rows'])


class OpeningSizeTest(Base):
    def test_parts_stock_uses_default_size(self):
        pid = stock.save_product(self.c, {'code': '', 'name': 'ボタン', 'category': stock.CATEGORY_PARTS,
                                          'sizes': ['F'], 'links': []})['id']
        gid = stock.save_product(self.c, {'code': 'X1', 'name': '服', 'sizes': ['L'], 'links': []})['id']
        self.assertEqual(bulk._codeless_stock_size(self.c, pid, '10mm'), 'F')
        self.assertEqual(bulk._codeless_stock_size(self.c, gid, 'M'), 'M')


class RepairTest(Base):
    def test_cleanup(self):
        pid = stock.save_product(self.c, {'code': '', 'name': 'スナッパー加工', 'color': 'ホワイト',
                                          'category': stock.CATEGORY_PROCESS_SERVICE,
                                          'sizes': ['10mm', '12mm', '—'], 'links': []})['id']
        out = proc_svc.apply_service_size_color_cleanup(self.c, dry_run=True)
        self.assertEqual(out['planned'][0]['removeSizes'], ['10mm', '12mm'])
        self.assertEqual(self.products()['スナッパー加工'][2], ['10mm', '12mm', '—'])  # 既定は書込なし
        proc_svc.apply_service_size_color_cleanup(self.c, dry_run=False)
        self.assertEqual(self.products()['スナッパー加工'][:3], ('', stock.CATEGORY_PROCESS_SERVICE, ['—']))
        self.assertEqual(proc_svc.apply_service_size_color_cleanup(self.c)['planned'], [])
        self.assertTrue(pid)

    def test_keeps_size_in_use(self):
        cust = stock.save_customer(self.c, {'name': '顧客A'})['id']
        pid = stock.save_product(self.c, {'code': '', 'name': '刺繍加工',
                                          'category': stock.CATEGORY_PROCESS_SERVICE,
                                          'sizes': ['L', 'M'], 'links': []})['id']
        loc = stock.save_location(self.c, {'name': '倉庫'})['id']
        cp = stock.ensure_customer_product(self.c, {'customerId': cust, 'productId': pid, 'embroidery': ''})
        cp_id = cp['id'] if isinstance(cp, dict) else cp
        t = stock.stamp()
        self.c.execute('''INSERT INTO lots(customer_product_id,size_label,location_id,unit_cost,qty_received,
                          qty_remaining,basis_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                       (cp_id, 'L', loc, 0, 1, 1, '2026-01-01', t, t))
        proc_svc.apply_service_size_color_cleanup(self.c, dry_run=False)
        self.assertEqual(self.products()['刺繍加工'][2], ['L'])


if __name__ == '__main__':
    unittest.main()
