"""刺繍類の加工先はエルム。それ以外の未登録加工はK2。合成データのみ。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as proc  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    conn = stock.connect(path)
    stock.initialize(conn)
    pli.ensure_schema(conn)
    return folder, path, conn


def commit(conn, path, lines, token='embroidery-vendor-elms-1'):
    text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
    body = {
        'importMode': 'products', 'name': 'emb.csv',
        'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
        'skuDecisions': {'6-615': {'decision': 'merge', 'nameChoice': 'コックコート'}},
        '_canWrite': True, '_actor': {'name': '確認'},
    }
    preview = pli.build_preview(conn, body)
    result = pli.commit_preview(conn, {
        **body,
        'previewFingerprint': preview['previewFingerprint'],
        'previewAt': preview['previewAt'],
        'token': token,
    }, db_path=str(path))
    conn.commit()
    return preview, result


class EmbroideryClassTest(unittest.TestCase):
    def test_known_embroidery_names(self):
        for name in ('刺繍', '刺繍加工', '刺繍入れ', 'LEOC刺繍', 'ＤＨＣ刺繍'):
            self.assertTrue(proc.is_embroidery_class_name(name), name)
        self.assertFalse(proc.is_embroidery_class_name('刺繍なし'))
        self.assertFalse(proc.is_embroidery_class_name('プリント'))
        self.assertFalse(proc.is_embroidery_class_name('裾上げ'))


class VendorRoutingTest(unittest.TestCase):
    LINES = [
        '顧客A,住商モンブラン,6-615,コックコート,L',
        '顧客B,住商モンブラン,6-615 刺繍,コックコート(LEOC刺繍),L',
        '顧客C,住商モンブラン,6-615 プリント,コックコート,M',
    ]

    def test_unique_elms_is_reused_and_print_stays_k2(self):
        folder, path, conn = newdb()
        try:
            elms = stock.save_supplier(conn, {'name': 'エルムス', 'active': True})
            preview, _result = commit(conn, path, self.LINES)
            cand = {row['serviceName']: row for row in preview['processingCandidates']}
            self.assertEqual(cand['刺繍']['vendorStatus'], 'unique')
            self.assertEqual(cand['刺繍']['vendorName'], 'エルムス')
            self.assertIn('K2スタッフ工房', cand['プリント']['vendorMessage'])
            services = {
                row['name']: row['supplier_id']
                for row in conn.execute("SELECT name, supplier_id FROM products WHERE category='加工サービス'")
            }
            self.assertEqual(services.get('刺繍'), elms['id'])
            k2 = conn.execute("SELECT id FROM suppliers WHERE name='K2スタッフ工房'").fetchone()
            self.assertEqual(services.get('プリント'), k2['id'])
            garment = conn.execute("SELECT id FROM suppliers WHERE name='住商モンブラン'").fetchone()['id']
            self.assertNotEqual(services.get('刺繍'), garment)
            handle = conn.execute(
                "SELECT embroidery FROM customer_products WHERE embroidery LIKE '%LEOC%'"
            ).fetchone()
            self.assertEqual(handle['embroidery'], 'LEOC刺繍')
            again = conn.execute(
                "SELECT COUNT(*) n FROM products WHERE name='刺繍' AND category='加工サービス'"
            ).fetchone()['n']
            self.assertEqual(again, 1)
        finally:
            conn.close()
            folder.cleanup()

    def test_missing_or_ambiguous_elms_does_not_fall_back_to_k2(self):
        folder, path, conn = newdb()
        try:
            preview, result = commit(conn, path, self.LINES, 'embroidery-vendor-elms-missing')
            self.assertTrue(any(row.get('needsConfirm') for row in preview['processingCandidates'] if row['serviceName'] == '刺繍'))
            services = {
                row['name']: row['supplier_id']
                for row in conn.execute("SELECT name, supplier_id FROM products WHERE category='加工サービス'")
            }
            self.assertNotIn('刺繍', services)
            k2 = conn.execute("SELECT id FROM suppliers WHERE name='K2スタッフ工房'").fetchone()
            self.assertEqual(services.get('プリント'), k2['id'])
            self.assertIn('エルム', result['processingCandidatesRegistered'].get('skipped') or '')
            stock.save_supplier(conn, {'name': '(有)エルムスコーポレーション', 'active': True})
            stock.save_supplier(conn, {'name': 'エルムス', 'active': True})
            conn.commit()
            text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(self.LINES)
            body = {
                'importMode': 'products', 'name': 'emb.csv',
                'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
                'skuDecisions': {'6-615': {'decision': 'merge', 'nameChoice': 'コックコート'}},
                '_canWrite': True, '_actor': {'name': '確認'},
            }
            preview2 = pli.build_preview(conn, body)
            emb = next(row for row in preview2['processingCandidates'] if row['serviceName'] == '刺繍')
            self.assertEqual(emb['vendorStatus'], 'ambiguous')
            before = conn.execute("SELECT COUNT(*) n FROM products WHERE category='加工サービス'").fetchone()['n']
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'embroidery-vendor-elms-2',
            }, db_path=str(path))
            conn.commit()
            after = conn.execute("SELECT COUNT(*) n FROM products WHERE name='刺繍' AND category='加工サービス'").fetchone()['n']
            self.assertEqual(after, 0)
            self.assertGreaterEqual(before, 1)
        finally:
            conn.close()
            folder.cleanup()

    def test_existing_other_vendor_service_is_kept(self):
        folder, path, conn = newdb()
        try:
            k2 = stock.save_supplier(conn, {'name': 'K2スタッフ工房', 'active': True})
            elms = stock.save_supplier(conn, {'name': 'エルムス', 'active': True})
            old = stock.save_product(conn, {
                'code': '', 'name': '刺繍', 'category': stock.CATEGORY_PROCESS_SERVICE,
                'supplierId': k2['id'], 'active': True, 'sizes': ['—'], 'links': [],
            })
            conn.commit()
            preview, _result = commit(conn, path, self.LINES)
            emb = next(row for row in preview['processingCandidates'] if row['serviceName'] == '刺繍')
            self.assertTrue(emb.get('existingVendorKept'))
            kept = conn.execute('SELECT supplier_id FROM products WHERE id=?', (old['id'],)).fetchone()
            self.assertEqual(kept['supplier_id'], k2['id'])
            elms_rows = conn.execute(
                "SELECT COUNT(*) n FROM products WHERE name='刺繍' AND supplier_id=? AND category='加工サービス'",
                (elms['id'],),
            ).fetchone()['n']
            self.assertEqual(elms_rows, 1)
            handle = conn.execute(
                "SELECT embroidery FROM customer_products WHERE embroidery='LEOC刺繍'"
            ).fetchone()
            self.assertIsNotNone(handle)
        finally:
            conn.close()
            folder.cleanup()

    def test_code_paren_leoc_stays_on_handle_and_uses_common_embroidery(self):
        folder, path, conn = newdb()
        try:
            elms = stock.save_supplier(conn, {'name': 'エルムス', 'active': True})
            conn.commit()
            text = (
                '顧客,仕入先,商品コード,商品名,サイズ\n'
                '顧客A,住商モンブラン,6-951(LEOC刺繍),コックコート（ホワイト×ブラック）,L\n'
            )
            body = {
                'importMode': 'products', 'name': 'leoc.csv',
                'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True, '_actor': {'name': '確認'},
            }
            preview = pli.build_preview(conn, body)
            row = preview['rows'][0]
            self.assertNotIn('意味を確定できません', ' '.join(row.get('reasons') or []))
            self.assertTrue(row.get('canCommit'))
            self.assertEqual(row['csv']['code'], '6-951')
            self.assertEqual(row['handle']['embroidery'], 'LEOC刺繍')
            self.assertIn('ホワイト', row['parenClassification'].get('color') or '')
            cand = preview['processingCandidates']
            self.assertTrue(cand)
            self.assertEqual(cand[0]['serviceName'], '刺繍')
            self.assertEqual(cand[0]['customerProcessingName'], 'LEOC刺繍')
            self.assertIn('LEOC刺繍', cand[0]['vendorMessage'])
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'embroidery-vendor-elms-leoc',
            }, db_path=str(path))
            conn.commit()
            product = conn.execute("SELECT code, supplier_id FROM products WHERE code='6-951'").fetchone()
            garment = conn.execute("SELECT id FROM suppliers WHERE name='住商モンブラン'").fetchone()
            self.assertEqual(product['supplier_id'], garment['id'])
            services = list(conn.execute(
                "SELECT name, supplier_id FROM products WHERE category='加工サービス'"
            ))
            names = [r['name'] for r in services]
            self.assertNotIn('LEOC刺繍', names)
            self.assertEqual(names.count('刺繍'), 1)
            self.assertEqual(services[0]['supplier_id'], elms['id'])
            handle = conn.execute(
                "SELECT embroidery FROM customer_products WHERE embroidery='LEOC刺繍'"
            ).fetchone()
            self.assertIsNotNone(handle)
        finally:
            conn.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
