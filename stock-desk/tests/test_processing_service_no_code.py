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
        self.assertEqual(body['category'], stock.CATEGORY_UNIFORM)
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


class ProcessingDedupeTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def services(self):
        return [dict(r) for r in self.c.execute(
            'SELECT id, name, supplier_id FROM products WHERE category=? ORDER BY id',
            (stock.CATEGORY_PROCESS_SERVICE,))]

    def test_key_normalizes_but_keeps_different_services(self):
        k = proc_svc.normalize_service_key
        self.assertEqual(k('ポケット加工'), k('＊ﾎﾟｹｯﾄ 加工'))
        self.assertNotEqual(k('ポケット加工'), k('ポケット付け'))
        self.assertEqual(proc_svc.unify_processing_service_name('＊ポケット加工'), 'ポケット加工')

    def test_csv_variants_and_vendors_become_one_with_sub_supplier(self):
        commit(self.c, self.path, [
            '顧客A,仕入先X,P1,ポケット加工,F',
            '顧客A,仕入先X,P2,ポケット 加工,F',
            '顧客A,仕入先Y,P3,ﾎﾟｹｯﾄ加工,F',
            '顧客A,仕入先X,P4,＊ポケット加工,F',
            '顧客A,仕入先X,P6,ポケット付け,F',
        ])
        names = sorted(s['name'] for s in self.services())
        self.assertEqual(names, ['ポケット付け', 'ポケット加工'])
        pid = [s for s in self.services() if s['name'] == 'ポケット加工'][0]['id']
        subs = {s['name'] for s in stock.product_sub_suppliers(self.c, pid)}
        self.assertIn('仕入先Y', subs)

    def test_reuses_existing_service_in_db(self):
        commit(self.c, self.path, ['顧客A,仕入先X,P1,ポケット加工,F'])
        commit(self.c, self.path, ['顧客B,仕入先Z,P9,＊ポケット加工,F'])
        self.assertEqual(len([s for s in self.services() if s['name'] == 'ポケット加工']), 1)

    def test_duplicate_repair_dry_run_then_merge(self):
        sup = stock.save_supplier(self.c, {'name': '仕入先X', 'active': True})
        for n in ('ポケット加工', '＊ポケット加工', 'ポケット付け'):
            stock.save_product(self.c, {'code': '', 'name': n, 'category': stock.CATEGORY_PROCESS_SERVICE,
                                        'supplierId': sup['id'], 'sizes': ['—'], 'active': True})
        inv = proc_svc.inventory_duplicate_process_services(self.c)
        self.assertEqual(inv['count'], 1)
        out = proc_svc.apply_duplicate_process_service_merge(self.c)
        self.assertTrue(out['dryRun'])
        self.assertEqual(len(self.services()), 3)
        proc_svc.apply_duplicate_process_service_merge(self.c, dry_run=False)
        self.assertEqual(proc_svc.inventory_duplicate_process_services(self.c)['count'], 0)
        merged = self.c.execute('SELECT merged_into_id FROM products WHERE name=?', ('＊ポケット加工',)).fetchone()[0]
        self.assertIsNotNone(merged)


class CodeDisplayTest(unittest.TestCase):
    def test_codeless_categories_show_nothing(self):
        self.assertEqual(stock.product_code_display('', stock.CATEGORY_PROCESS_SERVICE), '')
        self.assertEqual(stock.product_code_display('', stock.CATEGORY_PARTS), '')
        self.assertEqual(stock.product_code_display('', ''), stock.BLANK_SKU_DISPLAY)
        self.assertEqual(stock.product_code_display('A1', stock.CATEGORY_PARTS), 'A1')
        js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('function productCodeShow(r)', js)
        self.assertNotIn("r.codeDisplay||r.code||'品番なし'", js)


class GarmentParenSplitTest(unittest.TestCase):
    """「ポロシャツ（刺繍）」: 品番ありは商品＋加工サービス、品番なしは加工サービスだけ。"""

    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def services(self):
        return [dict(r) for r in self.c.execute(
            'SELECT id, code, name, supplier_id FROM products WHERE category=? AND merged_into_id IS NULL',
            (stock.CATEGORY_PROCESS_SERVICE,))]

    def test_coded_row_makes_product_and_service(self):
        p = commit(self.c, self.path, ['顧客A,仕入先X,ABC-200,ポロシャツ（刺繍）,L'])
        self.assertEqual(p['rows'][0]['companionProcessingService'], '刺繍加工')
        body = self.c.execute("SELECT id, name FROM products WHERE code='ABC-200'").fetchone()
        self.assertEqual(body['name'], 'ポロシャツ')
        h = self.c.execute('SELECT embroidery FROM customer_products WHERE product_id=?', (body['id'],)).fetchone()
        self.assertEqual(h['embroidery'], '刺繍')
        svc = self.services()
        self.assertEqual(len(svc), 1)
        self.assertEqual((svc[0]['code'], svc[0]['supplier_id']), ('', None))

    def test_no_code_row_makes_service_only_and_dedupes(self):
        commit(self.c, self.path, [
            '顧客A,仕入先X,ABC-200,ポロシャツ（刺繍）,L',
            '顧客A,仕入先Y,,ポロシャツ（刺繍）,L',
            '顧客A,仕入先X,,刺繍,F',
        ])
        self.assertEqual(len(self.services()), 1)
        names = [r['name'] for r in self.c.execute(
            "SELECT name FROM products WHERE COALESCE(category,'')<>?", (stock.CATEGORY_PROCESS_SERVICE,))]
        self.assertEqual(names, ['ポロシャツ'])

    def test_repair_creates_missing_services_dry_run_first(self):
        sup = stock.save_supplier(self.c, {'name': '仕入先X'})
        sup_id = sup['id'] if isinstance(sup, dict) else int(sup)
        cust = stock.save_customer(self.c, {'name': '顧客A'})
        cust_id = cust['id'] if isinstance(cust, dict) else int(cust)
        stock.save_product(self.c, {'code': 'ABC-1', 'name': 'ポロシャツ', 'supplierId': sup_id, 'sizes': ['L'],
                                    'links': [{'customerId': cust_id, 'embroidery': '刺繍'}], 'active': True})
        stock.save_product(self.c, {'code': '', 'name': 'ポロシャツ（刺繍）', 'category': stock.CATEGORY_PARTS,
                                    'sizes': ['L'], 'active': True})
        dry = proc_svc.apply_missing_companion_services(self.c)
        self.assertTrue(dry['dryRun'])
        self.assertEqual([m['serviceName'] for m in dry['plan']], ['刺繍加工'])
        self.assertEqual(len(dry['reviewOnly']), 1)
        self.assertEqual(self.services(), [])
        done = proc_svc.apply_missing_companion_services(self.c, dry_run=False)
        self.assertEqual(len(done['created']), 1)
        self.assertEqual(len(self.services()), 1)
        self.assertEqual(proc_svc.apply_missing_companion_services(self.c)['plan'], [])


class CategoryDefaultTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_coded_garments_become_uniform(self):
        commit(self.c, self.path, ['顧客A,仕入先X,ABC-1,ポロシャツ,L', '顧客A,仕入先X,別注,特注ベスト,L'])
        cats = {r['code']: r['category'] for r in self.c.execute('SELECT code, category FROM products')}
        self.assertEqual(cats, {'ABC-1': stock.CATEGORY_UNIFORM, '別注': stock.CATEGORY_UNIFORM})

    def test_category_repair_dry_run_then_commit(self):
        pid = stock.save_product(self.c, {'code': 'OLD-1', 'name': 'シャツ', 'category': '', 'sizes': ['L'], 'active': True})
        pid = pid['id'] if isinstance(pid, dict) else int(pid)
        self.c.execute("UPDATE products SET category='' WHERE id=?", (pid,))
        dry = proc_svc.apply_category_fixes(self.c)
        self.assertEqual([x['id'] for x in dry['fix']], [pid])
        self.assertEqual(self.c.execute('SELECT category FROM products WHERE id=?', (pid,)).fetchone()[0], '')
        proc_svc.apply_category_fixes(self.c, dry_run=False)
        self.assertEqual(self.c.execute('SELECT category FROM products WHERE id=?', (pid,)).fetchone()[0], stock.CATEGORY_UNIFORM)
        self.assertEqual(proc_svc.apply_category_fixes(self.c)['fix'], [])
