"""品番の追加文字を属性で分ける。合成データのみ。実ファイルは使わない。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import code_suffix_attributes as suffix  # noqa: E402
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


def commit_lines(conn, path, lines):
    text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
    body = {
        'importMode': 'products', 'name': 'attr.csv',
        'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
        '_canWrite': True, '_actor': {'name': '確認'},
    }
    preview = pli.build_preview(conn, body)
    result = pli.commit_preview(conn, {
        **body,
        'previewFingerprint': preview['previewFingerprint'],
        'previewAt': preview['previewAt'],
        'token': 'suffix-attr-commit-1',
    }, db_path=str(path))
    conn.commit()
    return preview, result


class ClassifyTest(unittest.TestCase):
    def test_examples(self):
        embroidery = suffix.classify_suffix(
            '6-615 刺繍', product_name='コックコート(LEOC刺繍)', supplier='住商モンブラン', size='L',
        )
        self.assertTrue(embroidery['apply'])
        self.assertEqual(embroidery['baseCode'], '6-615')
        self.assertEqual(embroidery['processingName'], '刺繍')
        leoc = suffix.classify_suffix(
            '6-951(LEOC刺繍)', product_name='コックコート', supplier='住商モンブラン', size='L',
        )
        self.assertTrue(leoc['apply'])
        self.assertFalse(leoc['needsConfirm'])
        self.assertEqual(leoc['baseCode'], '6-951')
        self.assertEqual(leoc['kind'], 'processing')
        self.assertEqual(leoc['processingName'], 'LEOC刺繍')
        wide = suffix.classify_suffix('6-951（LEOC刺繍）', product_name='コックコート')
        self.assertEqual(wide['baseCode'], '6-951')
        self.assertEqual(wide['processingName'], 'LEOC刺繍')
        self.assertFalse(suffix.classify_suffix('6-951(刺繍なし)')['apply'])
        parts = suffix.classify_suffix('BW554-73用Dカン', product_name='Dカン', supplier='住商モンブラン', size='-')
        self.assertTrue(parts['registerAsParts'])
        self.assertFalse(parts['apply'])
        unknown = suffix.classify_suffix('DBP用生地', product_name='生地', supplier='シーズン', size='-')
        self.assertTrue(unknown['needsConfirm'])
        self.assertEqual(suffix.classify_suffix('AZ-8618-003')['baseCode'], 'AZ-8618-003')
        self.assertFalse(suffix.classify_suffix('AZ-8618-003')['apply'])
        midori = suffix.classify_suffix('NT7003上', supplier='ミドリ安全北海道', product_name='ジャンパー')
        self.assertEqual(midori['kind'], 'midori_position')
        self.assertFalse(midori['apply'])
        lower = suffix.classify_suffix('7-522下', supplier='仕入A', product_name='ズボン')
        self.assertEqual(lower['baseCode'], '7-522')
        self.assertEqual(lower['position'], '下')
        conflict = suffix.classify_suffix('KK7122-16（LL）', size='XL', product_name='帽')
        self.assertTrue(conflict['needsConfirm'])
        change = suffix.classify_suffix('MH60B下無くなり次第F15BO78へ', product_name='帽')
        self.assertTrue(change['needsConfirm'])
        self.assertTrue(suffix.classify_suffix('7-731(廃番→7-733)')['needsConfirm'])
        unused = suffix.classify_suffix(
            'APK205-10 (不使用)', product_name='シャツコート(不使用)', supplier='KAZEN WLD(株)',
        )
        self.assertTrue(unused['apply'])
        self.assertEqual(unused['kind'], 'note')
        self.assertEqual(unused['baseCode'], 'APK205-10')
        self.assertEqual(unused['note'], '不使用')
        self.assertFalse(unused['registerAsParts'])
        wide_unused = suffix.classify_suffix(
            'APK205-10（不使用）', product_name='シャツコート（不使用）',
        )
        self.assertEqual(wide_unused['baseCode'], 'APK205-10')
        self.assertFalse(wide_unused['registerAsParts'])
        bare_you = suffix.classify_suffix('APK205-10用', product_name='シャツコート')
        self.assertFalse(bare_you['registerAsParts'])
        self.assertNotIn('部品のように見えます', bare_you.get('reason') or '')
        star = suffix.classify_suffix('*小前', product_name='*小前', supplier='(株)サーヴォ')
        self.assertFalse(star['registerAsParts'])
        self.assertNotEqual(star['kind'], 'parts')
        fabric = suffix.classify_suffix(
            'NT7003難燃作業衣 生地', product_name='NT7003難燃作業衣 生地',
            supplier='ミドリ安全北海道 釧路営業所',
        )
        self.assertTrue(fabric['registerAsParts'])
        self.assertFalse(fabric['needsConfirm'])
        self.assertEqual(fabric['baseCode'], 'NT7003難燃作業衣 生地')
        self.assertFalse(fabric['apply'])
        inclusive = suffix.classify_suffix(
            'AS7300-1 (刺繍代込み)', product_name='シャツ', supplier='仕入A',
        )
        self.assertEqual(inclusive['kind'], 'price_condition')
        self.assertFalse(inclusive['needsConfirm'])
        self.assertFalse(inclusive['apply'])
        self.assertEqual(inclusive['baseCode'], 'AS7300-1 (刺繍代込み)')
        self.assertFalse(inclusive['processingName'])
        self.assertFalse(proc.is_embroidery_class_name('刺繍代込み'))
        self.assertTrue(proc.is_embroidery_class_name('LEOC刺繍'))
        self.assertTrue(proc.is_embroidery_class_name('刺繍'))


class ImportTest(unittest.TestCase):
    def test_processing_color_parts_and_notes_register_without_shape_block(self):
        folder, path, conn = newdb()
        try:
            lines = [
                '顧客A,住商モンブラン,6-615,コックコート半袖加工,L',
                '顧客B,住商モンブラン,6-615 刺繍,コックコート(LEOC刺繍),６L',
                '顧客A,住商モンブラン,6-615 青,コックコート,M',
                '顧客A,住商モンブラン,BW554-73用Dカン,Dカン,-',
                '顧客A,住商モンブラン,6-827 (デリカ),帽子,F',
                '顧客A,仕入A,AZ-8618-003,白衣,M',
                '顧客A,仕入A,7-522下,作業ズボン,LL',
                '顧客A,仕入A,7-522,作業ズボン,2L',
                '顧客C,住商モンブラン,6-615 プリント,コックコート,S',
            ]
            preview, result = commit_lines(conn, path, lines)
            self.assertTrue(result['stockUnchanged'])
            ids = {g['id'] for g in preview['codeShapeGroups']}
            self.assertNotIn('extend:6-615', ids)
            self.assertNotIn('extend:7-522', ids)
            codes = {
                row['code']: row for row in conn.execute(
                    'SELECT id, code, name, color, category FROM products WHERE merged_into_id IS NULL'
                )
            }
            self.assertIn('6-615', codes)
            self.assertNotIn('6-615 刺繍', codes)
            self.assertNotIn('6-615 青', codes)
            self.assertEqual(codes['BW554-73用Dカン']['category'], '部品・資材')
            self.assertIn('AZ-8618-003', codes)
            self.assertIn('6-827', codes)
            self.assertIn('7-522', codes)
            self.assertNotIn('7-522下', codes)
            pid = codes['6-615']['id']
            sizes = {r['label'] for r in conn.execute('SELECT label FROM product_sizes WHERE product_id=?', (pid,))}
            self.assertIn('L', sizes)
            self.assertIn('6L', sizes)
            self.assertNotIn('６L', sizes)
            handles = list(conn.execute(
                'SELECT embroidery FROM customer_products WHERE product_id=? ORDER BY id', (pid,)
            ))
            self.assertIn('LEOC刺繍', [r['embroidery'] for r in handles])
            services = list(conn.execute(
                "SELECT name, supplier_id FROM products WHERE category='加工サービス'"
            ))
            names = {r['name'] for r in services}
            self.assertIn('プリント', names)
            k2 = conn.execute("SELECT id FROM suppliers WHERE name='K2スタッフ工房'").fetchone()
            self.assertIsNotNone(k2)
            printed = next(r for r in services if r['name'] == 'プリント')
            self.assertEqual(printed['supplier_id'], k2['id'])
            garment = conn.execute("SELECT id FROM suppliers WHERE name='住商モンブラン'").fetchone()
            self.assertNotEqual(printed['supplier_id'], garment['id'])
            pants = codes['7-522']['id']
            pants_sizes = {r['label'] for r in conn.execute('SELECT label FROM product_sizes WHERE product_id=?', (pants,))}
            self.assertEqual(pants_sizes, {'LL', '2L'})
            # 再取込で商品が増えない
            preview2, result2 = commit_lines(conn, path, lines)
            self.assertTrue(result2.get('already') or result2['counts'].get('productsCreated', 0) == 0)
            again = conn.execute(
                "SELECT COUNT(*) n FROM products WHERE code='6-615' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(again, 1)
            self.assertIn('6-615', {g.get('code') for g in preview2['skuGroups']})
        finally:
            conn.close()
            folder.cleanup()

    def test_dimension_conflict_stays_uncommitted(self):
        folder, path, conn = newdb()
        try:
            text = '顧客,仕入先,商品コード,商品名,サイズ\n顧客A,仕入A,KK7122-16（LL）,帽子,XL\n'
            body = {
                'importMode': 'products', 'name': 'dim.csv',
                'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True, '_actor': {'name': '確認'},
            }
            preview = pli.build_preview(conn, body)
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'needs_review')
            self.assertFalse(row['canCommit'])
            with self.assertRaises(ValueError):
                pli.commit_preview(conn, {
                    **body,
                    'previewFingerprint': preview['previewFingerprint'],
                    'previewAt': preview['previewAt'],
                    'token': 'suffix-dim-1',
                }, db_path=str(path))
            self.assertEqual(conn.execute('SELECT COUNT(*) n FROM products').fetchone()['n'], 0)
        finally:
            conn.close()
            folder.cleanup()

    def test_embroidery_price_included_registers_as_its_own_code(self):
        folder, path, conn = newdb()
        try:
            text = (
                '顧客,仕入先,商品コード,商品名,サイズ,仕入単価\n'
                '顧客A,仕入A,AS7300-1,シャツ,L,1200\n'
                '顧客A,仕入A,AS7300-1 (刺繍代込み),シャツ,L,1800\n'
            )
            body = {
                'importMode': 'products', 'name': 'price.csv',
                'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True, '_actor': {'name': '確認'},
            }
            preview = pli.build_preview(conn, body)
            by_code = {r['csv']['code']: r for r in preview['rows']}
            plain = by_code['AS7300-1']
            held = by_code['AS7300-1 (刺繍代込み)']
            self.assertTrue(plain['canCommit'])
            self.assertTrue(held['canCommit'])
            self.assertEqual(held['csv']['code'], 'AS7300-1 (刺繍代込み)')
            self.assertNotEqual(held['csv']['code'], plain['csv']['code'])
            names = [c.get('serviceName') for c in preview.get('processingCandidates') or []]
            self.assertNotIn('刺繍', names)
            self.assertNotIn('刺繍代込み', names)
            pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'suffix-price-own-code-1',
            }, db_path=str(path))
            conn.commit()
            codes = {
                r['code'] for r in conn.execute(
                    "SELECT code FROM products WHERE merged_into_id IS NULL"
                )
            }
            self.assertIn('AS7300-1', codes)
            self.assertIn('AS7300-1 (刺繍代込み)', codes)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) n FROM products WHERE category='加工サービス'"
                ).fetchone()['n'],
                0,
            )
        finally:
            conn.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
