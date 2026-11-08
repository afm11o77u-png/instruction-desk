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


if __name__ == '__main__':
    unittest.main()
