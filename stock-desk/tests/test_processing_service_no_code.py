"""加工サービスを品番付きで登録しない（テストDBのみ）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as proc_svc  # noqa: E402
import stock  # noqa: E402
from scripts import repair_coded_process_services as repair  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv, newdb  # noqa: E402


def commit(c, path, rows, overrides=None):
    s = {'importMode': 'products', 'name': 'a.csv', 'file': b64(ledger_csv(rows)),
         '_canWrite': True, '_actor': {'name': 't'}}
    if overrides:
        s['rowClassifications'] = overrides
    p = pli.build_preview(c, s)
    with mock.patch.object(stock, 'backup_sqlite', return_value=''):
        pli.commit_preview(c, {**s, 'commitToken': 'tok-proc-no-code-1',
                               'previewFingerprint': p['previewFingerprint']}, db_path=path)
    return p


def coded_services(c):
    return [dict(r) for r in c.execute(
        "SELECT code, name FROM products WHERE category=? AND code<>''", (stock.CATEGORY_PROCESS_SERVICE,))]


class ProcessingNoCodeTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_garment_with_paren_processing_is_product(self):
        self.assertFalse(proc_svc.is_standalone_processing_service_name('ポロシャツ（刺繍）'))
        self.assertTrue(proc_svc.is_standalone_processing_service_name('刺繍'))
        self.assertTrue(proc_svc.is_standalone_processing_service_name('赤ライン加工'))

    def test_no_process_service_gets_code(self):
        commit(self.c, self.path, [
            '顧客A,仕入先X,ABC-100,ポロシャツ,L',
            '顧客A,仕入先X,ABC-100,刺繍,L',
            '顧客A,仕入先X,NM-1,ネーム加工,F',
            '顧客A,ケイツー,ABC-100,ネーム,F',
            '顧客A,仕入先X,ABC-200,ポロシャツ（刺繍）,L',
        ])
        self.assertEqual(coded_services(self.c), [])
        body = self.c.execute("SELECT category FROM products WHERE code='ABC-200'").fetchone()
        self.assertEqual(body['category'], '')
        names = {r['name'] for r in self.c.execute(
            'SELECT name FROM products WHERE category=?', (stock.CATEGORY_PROCESS_SERVICE,))}
        self.assertIn('ネーム加工', names)

    def test_manual_process_kind_drops_code(self):
        rows = ['顧客A,仕入先X,SS-2,ネーム入れ,F']
        s = {'importMode': 'products', 'name': 'a.csv', 'file': b64(ledger_csv(rows))}
        p = pli.build_preview(self.c, s)
        rn = p['rows'][0]['rowNo']
        classified = [dict(p['rows'][0])]
        pli.apply_manual_register_kind(classified, {str(rn): {'registerKind': '加工サービス'}})
        prod = classified[0]['product']
        self.assertEqual(prod['code'], '')
        self.assertTrue(prod['blankSku'])

    def test_repair_defaults_to_dry_run(self):
        a = stock.save_product(self.c, {'code': 'NM-1', 'name': 'ネーム加工', 'category': stock.CATEGORY_PROCESS_SERVICE, 'sizes': ['F'], 'active': True})
        b = stock.save_product(self.c, {'code': 'ABC-9', 'name': 'ポロシャツ(刺繍)', 'category': stock.CATEGORY_PROCESS_SERVICE, 'sizes': ['F'], 'active': True})
        inv = proc_svc.inventory_coded_process_services(self.c)
        acts = {i['id']: i['action'] for i in inv['items']}
        self.assertEqual(acts, {a['id']: 'clear_code', b['id']: 'to_product'})
        out = proc_svc.apply_coded_process_service_repair(self.c, [a['id'], b['id']])
        self.assertTrue(out['dryRun'])
        self.assertEqual(len(coded_services(self.c)), 2)
        proc_svc.apply_coded_process_service_repair(self.c, [a['id'], b['id']], dry_run=False)
        self.assertEqual(coded_services(self.c), [])
        self.assertEqual(self.c.execute('SELECT category FROM products WHERE id=?', (b['id'],)).fetchone()[0], '')

    def test_repair_script_readonly_by_default(self):
        stock.save_product(self.c, {'code': 'NM-1', 'name': 'ネーム加工', 'category': stock.CATEGORY_PROCESS_SERVICE, 'sizes': ['F'], 'active': True})
        self.c.commit()
        pid = self.c.execute("SELECT id FROM products WHERE code='NM-1'").fetchone()[0]
        with mock.patch('sys.stdout'):
            self.assertEqual(repair.main([str(self.path), '--ids', str(pid)]), 0)
        self.assertEqual(len(coded_services(self.c)), 1)


if __name__ == '__main__':
    unittest.main()
