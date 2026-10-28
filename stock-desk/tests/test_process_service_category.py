"""加工サービス区分の分離（temp DB / fixtures のみ。本番書込なし）。"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as ps  # noqa: E402
import server as stock_server  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8-sig')).decode('ascii')


def ledger(*rows):
    return '\n'.join(['顧客,仕入先,商品コード,商品名,サイズ', *rows])


class ProcessServiceCategoryTest(unittest.TestCase):
    def test_normalize_accepts_process_service(self):
        self.assertEqual(
            stock.normalize_category(stock.CATEGORY_PROCESS_SERVICE),
            '加工サービス',
        )
        with self.assertRaises(ValueError):
            stock.normalize_category('不明カテゴリ')

    def test_category_options_include_process_service(self):
        self.assertIn(stock.CATEGORY_PROCESS_SERVICE, stock.PRODUCT_CATEGORY_OPTIONS)
        self.assertIn(stock.CATEGORY_PARTS, stock.PRODUCT_CATEGORY_OPTIONS)
        folder, path, c = newdb()
        try:
            boot = stock.bootstrap(c)
            self.assertEqual(
                boot['categoryOptions'],
                list(stock.PRODUCT_CATEGORY_OPTIONS),
            )
        finally:
            c.close()
            folder.cleanup()

    def test_new_process_vs_physical_categories(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,K2スタッフ工房,,七分袖加工,—',
                '顧客A,某資材,,ボタン,—',
                '顧客A,某メーカー,U-1,スクラブ,M',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'cat.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertIn(stock.CATEGORY_PROCESS_SERVICE, preview.get('categoryOptions') or [])
            plans = [
                p for p in (preview.get('processingServicePlans') or [])
                if p.get('autoRegister')
            ]
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]['category'], stock.CATEGORY_PROCESS_SERVICE)
            self.assertEqual(plans[0]['serviceName'], '七分袖加工')

            btn = next(
                r for r in preview['rows']
                if (r.get('csv') or {}).get('productName') == 'ボタン'
            )
            self.assertTrue((btn.get('processingService') or {}).get('isPhysicalParts'))
            self.assertEqual(
                (btn.get('product') or {}).get('proposedCategory') or (btn.get('product') or {}).get('category'),
                stock.CATEGORY_PARTS,
            )

            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'cat.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'cat-split-01',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()

            svc = c.execute(
                "SELECT category,code FROM products WHERE name=?",
                ('七分袖加工',),
            ).fetchone()
            self.assertEqual(svc['category'], stock.CATEGORY_PROCESS_SERVICE)
            self.assertEqual(svc['code'], '')

            parts = c.execute(
                "SELECT category FROM products WHERE name=?",
                ('ボタン',),
            ).fetchone()
            self.assertEqual(parts['category'], stock.CATEGORY_PARTS)

            uni = c.execute(
                "SELECT category FROM products WHERE code=?",
                ('U-1',),
            ).fetchone()
            self.assertIn(uni['category'], ('', stock.CATEGORY_UNIFORM))
        finally:
            c.close()
            folder.cleanup()

    def test_reimport_dedupes_same_name_vendor(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,K2スタッフ工房,,丈直し,',
                '顧客B,K2スタッフ工房,,丈直し,',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'dedupe.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'dedupe.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'dedupe-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            n1 = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE name=? AND category=?",
                ('丈直し', stock.CATEGORY_PROCESS_SERVICE),
            ).fetchone()['n']
            self.assertEqual(n1, 1)

            preview2 = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'dedupe.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = [
                p for p in (preview2.get('processingServicePlans') or [])
                if p.get('autoRegister')
            ]
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0].get('action'), 'reuse')
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'dedupe.csv',
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'dedupe-2',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            n2 = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE name=? AND category=?",
                ('丈直し', stock.CATEGORY_PROCESS_SERVICE),
            ).fetchone()['n']
            self.assertEqual(n2, 1)
        finally:
            c.close()
            folder.cleanup()

    def test_manual_save_process_service_and_list_filter_helpers(self):
        folder, path, c = newdb()
        try:
            k2 = stock.save_supplier(c, {'name': 'K2スタッフ工房', 'active': True})
            k2_id = k2['id'] if isinstance(k2, dict) else int(k2)
            fabric_sup = stock.save_supplier(c, {'name': '生地屋', 'active': True})
            fabric_id = fabric_sup['id'] if isinstance(fabric_sup, dict) else int(fabric_sup)
            svc = stock.save_product(c, {
                'code': '', 'name': '裾上げ', 'category': stock.CATEGORY_PROCESS_SERVICE,
                'supplierId': k2_id, 'sizes': ['—'], 'links': [], 'active': True,
            })
            parts = stock.save_product(c, {
                'code': '', 'name': '芯地', 'category': stock.CATEGORY_PARTS,
                'supplierId': fabric_id, 'sizes': ['—'], 'links': [], 'active': True,
            })
            c.commit()
            svc_id = svc['id'] if isinstance(svc, dict) else int(svc)
            parts_id = parts['id'] if isinstance(parts, dict) else int(parts)

            boot = stock.bootstrap(c)
            by_id = {p['id']: p for p in boot['products']}
            self.assertEqual(by_id[svc_id]['category'], stock.CATEGORY_PROCESS_SERVICE)
            self.assertEqual(by_id[parts_id]['category'], stock.CATEGORY_PARTS)

            # UI フィルタ相当（app.js productMatchesCategoryFilter）
            def match(cat, filt):
                if not filt:
                    return True
                if filt == '__empty__':
                    return not cat
                return cat == filt

            self.assertTrue(match(by_id[svc_id]['category'], '加工サービス'))
            self.assertFalse(match(by_id[parts_id]['category'], '加工サービス'))
            self.assertTrue(match(by_id[parts_id]['category'], '部品・資材'))
        finally:
            c.close()
            folder.cleanup()

    def test_ui_picker_includes_process_service(self):
        app_js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("['加工サービス','加工サービス']", app_js)
        self.assertIn('productCategoryControl', app_js)
        self.assertIn('productMatchesCategoryFilter', app_js)
        ledger_js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn("'加工サービス'", ledger_js)
        self.assertIn('区分: 加工サービス', ledger_js)

    def test_migration_inventory_clear_vs_ambiguous_no_write(self):
        folder, path, c = newdb()
        try:
            k2 = stock.save_supplier(c, {'name': 'K2スタッフ工房', 'active': True})
            k2_id = k2['id'] if isinstance(k2, dict) else int(k2)
            other = stock.save_supplier(c, {'name': '某メーカー', 'active': True})
            other_id = other['id'] if isinstance(other, dict) else int(other)

            clear = stock.save_product(c, {
                'code': '', 'name': '七分袖加工', 'category': stock.CATEGORY_PARTS,
                'supplierId': k2_id, 'sizes': ['—'], 'links': [], 'active': True,
            })
            physical = stock.save_product(c, {
                'code': '', 'name': 'ボタン', 'category': stock.CATEGORY_PARTS,
                'supplierId': other_id, 'sizes': ['—'], 'links': [], 'active': True,
            })
            ambiguous = stock.save_product(c, {
                'code': '', 'name': '裾上げ', 'category': stock.CATEGORY_PARTS,
                'supplierId': other_id, 'sizes': ['—'], 'links': [], 'active': True,
            })
            c.commit()
            clear_id = clear['id'] if isinstance(clear, dict) else int(clear)
            physical_id = physical['id'] if isinstance(physical, dict) else int(physical)
            amb_id = ambiguous['id'] if isinstance(ambiguous, dict) else int(ambiguous)

            inv = ps.inventory_process_service_migration(c, include_leave=True)
            self.assertFalse(inv['writes'])
            cand_ids = {x['id'] for x in inv['candidates']}
            amb_ids = {x['id'] for x in inv['ambiguous']}
            leave_ids = {x['id'] for x in inv['leave']}
            self.assertIn(clear_id, cand_ids)
            self.assertIn(amb_id, amb_ids)
            self.assertIn(physical_id, leave_ids)

            # dry-run apply: 計画のみ・カテゴリ不変
            plan = ps.apply_process_service_migration(c, [clear_id], dry_run=True)
            self.assertTrue(plan['dryRun'])
            self.assertFalse(plan['writes'])
            self.assertEqual(len(plan['planned']), 1)
            still = c.execute(
                'SELECT category FROM products WHERE id=?', (clear_id,),
            ).fetchone()['category']
            self.assertEqual(still, stock.CATEGORY_PARTS)

            # 曖昧はスキップ
            skip = ps.apply_process_service_migration(c, [amb_id], dry_run=False)
            self.assertFalse(skip['writes'])
            self.assertTrue(skip['skipped'])

            # 明示 apply（fixture DB のみ）
            done = ps.apply_process_service_migration(c, [clear_id], dry_run=False)
            self.assertTrue(done['writes'])
            c.commit()
            after = c.execute(
                'SELECT category FROM products WHERE id=?', (clear_id,),
            ).fetchone()['category']
            self.assertEqual(after, stock.CATEGORY_PROCESS_SERVICE)
            # 実物は不変
            self.assertEqual(
                c.execute(
                    'SELECT category FROM products WHERE id=?', (physical_id,),
                ).fetchone()['category'],
                stock.CATEGORY_PARTS,
            )
        finally:
            c.close()
            folder.cleanup()

    def test_inventory_api_readonly(self):
        folder, path, c = newdb()
        try:
            c.close()
            status, body = stock_server.api_get(
                str(path), 'live',
                '/api/products/process-service-migration/inventory',
                {},
                can_write=True,
                actor={'name': 't', 'role': 'owner'},
            )
            self.assertEqual(status, 200)
            self.assertFalse(body.get('writes'))
            self.assertIn('candidates', body)
            self.assertIn('method', body)
        finally:
            folder.cleanup()

    def test_legacy_parts_reuse_without_auto_migrate(self):
        """旧・部品・資材の加工登録は ID 再利用し、取込ではカテゴリを勝手に変えない。"""
        folder, path, c = newdb()
        try:
            k2 = stock.save_supplier(c, {'name': 'K2スタッフ工房', 'active': True})
            k2_id = k2['id'] if isinstance(k2, dict) else int(k2)
            old = stock.save_product(c, {
                'code': '', 'name': 'ポケット取付', 'category': stock.CATEGORY_PARTS,
                'supplierId': k2_id, 'sizes': ['—'], 'links': [], 'active': True,
            })
            old_id = old['id'] if isinstance(old, dict) else int(old)
            c.commit()
            text = ledger('顧客A,K2スタッフ工房,,ポケット取付,')
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'reuse-legacy.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = [
                p for p in (preview.get('processingServicePlans') or [])
                if p.get('autoRegister')
            ]
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]['action'], 'reuse')
            self.assertEqual(int(plans[0]['productId']), old_id)
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'reuse-legacy.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'reuse-legacy-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            row = c.execute(
                'SELECT id,category FROM products WHERE id=?', (old_id,),
            ).fetchone()
            self.assertEqual(row['category'], stock.CATEGORY_PARTS)
            # inventory では candidate になる
            inv = ps.inventory_process_service_migration(c)
            self.assertIn(old_id, {x['id'] for x in inv['candidates']})
        finally:
            c.close()
            folder.cleanup()

    def test_body_split_keeps_process_match_for_opening(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,某メーカー,B-1,シャツ,M',
                '顧客A,K2スタッフ工房,B-1,半袖加工,M',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'split.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'B-1')
            self.assertNotIn('K2スタッフ工房', g.get('supplierCandidates') or [])
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'split.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'body-split-01',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            body = c.execute(
                "SELECT id,supplier_id FROM products WHERE code='B-1'",
            ).fetchone()
            svc = c.execute(
                "SELECT id,category FROM products WHERE name='半袖加工'",
            ).fetchone()
            self.assertIsNotNone(body)
            self.assertEqual(svc['category'], stock.CATEGORY_PROCESS_SERVICE)
            self.assertNotEqual(body['id'], svc['id'])
            maps = list(c.execute(
                "SELECT product_id,spec_json FROM product_ledger_row_maps "
                "WHERE csv_product_code='B-1'"
            ))
            self.assertTrue(maps)
            for m in maps:
                spec = json.loads(m['spec_json'] or '{}')
                if spec.get('processingServiceName') == '半袖加工':
                    self.assertEqual(int(m['product_id']), int(body['id']))
                    self.assertEqual(spec.get('processingVendor'), 'K2スタッフ工房')
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
