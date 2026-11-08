"""K2製作の制服は商品。品番は空欄衣服の現行規則「要確認」のまま。一律の別注化はしない。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import legacy_csv_import as legacy  # noqa: E402
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


def preview_of(conn, lines, phase=''):
    text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
    body = {
        'importMode': 'products', 'productPhase': phase, 'name': 'k2.csv',
        'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
        '_canWrite': True, '_actor': {'name': '確認'},
    }
    return pli.build_preview(conn, body), body


UNIFORMS = [
    'エプロン ピンク',
    'エプロン ブルー',
    'エプロン ミント',
    'チェック柄ベスト(ムーリベッキ)',
    'バスタオル アイスグリーン',
    'ムーリベッキベスト(現在不使用)',
    '衣装係ストレートパンツ',
    '衣装係ドットジャケット',
    '衣装係ベスト',
    '衣装係ワイドパンツ',
    '衣装係襟ドットジャケット',
    '赤エプロン',
    '別注エプロン 赤',
]

PROCESSING = [
    ('K2スタッフ工房', '', 'アジャスター直し'),
    ('既存加工店', 'コックコート', 'インターネット付け'),
    ('K2スタッフ工房', '', 'スナッパー加工'),
    ('K2スタッフ工房', '', 'ポケット加工'),
    ('K2スタッフ工房', '', 'ポケット取付'),
    ('K2スタッフ工房', '', 'ライン加工'),
    ('K2スタッフ工房', '', '丈詰め'),
]


def all_lines():
    lines = [f'顧客A,K2スタッフ工房,,{name},L' for name in UNIFORMS]
    lines += [f'顧客A,{sup},{code},{name},-' for sup, code, name in PROCESSING]
    lines += [
        '顧客A,K2スタッフ工房,,エプロン用ボタン,-',
        '顧客A,K2スタッフ工房,,青リボン,-',
        '顧客A,KAZEN WLD(株),,長白衣ボタン,-',
        '顧客A,住商モンブラン,6-615,コックコート(裾上げ),L',
        '顧客A,KAZEN WLD(株),APK205-10 (不使用),シャツコート(不使用),L',
    ]
    return lines


class K2UniformPlanTest(unittest.TestCase):
    def test_blank_uniforms_stay_needs_code_and_distinct(self):
        folder, path, conn = newdb()
        try:
            preview, body = preview_of(conn, all_lines())
            by_name = {row['csv']['productName']: row for row in preview['rows']}
            for name in UNIFORMS:
                row = by_name[name]
                self.assertTrue(row.get('k2UniformProduct'), name)
                self.assertFalse((row.get('processingService') or {}).get('isProcessingService'), name)
                self.assertEqual(row['csv']['code'], '要確認', name)
                self.assertNotEqual(row['csv']['code'], '別注', name)
                self.assertEqual((row.get('product') or {}).get('category'), '制服', name)
                self.assertTrue(row['canCommit'], (name, row.get('status'), row.get('reasons')))
                self.assertNotEqual(row['status'], 'needs_review', name)
            names = [row['csv']['productName'] for row in preview['rows'] if row.get('k2UniformProduct')]
            self.assertEqual(len(names), len(set(names)))
            pink = by_name['エプロン ピンク']['product']
            red = by_name['赤エプロン']['product']
            self.assertEqual(pink.get('proposedColor') or pink.get('officialColor'), 'ピンク')
            self.assertEqual(red.get('proposedColor') or red.get('officialColor'), 'レッド')
            self.assertNotEqual(pink.get('officialName') or pink.get('name'), red.get('officialName') or red.get('name'))
            mint = by_name['エプロン ミント']['product']
            self.assertEqual(mint.get('proposedColor') or mint.get('officialColor'), 'ミント')
            towel = by_name['バスタオル アイスグリーン']['product']
            self.assertEqual(towel.get('proposedColor') or towel.get('officialColor'), 'アイスグリーン')
            self.assertIn('バスタオル', towel.get('officialName') or by_name['バスタオル アイスグリーン']['csv']['productName'])
            unused = by_name['ムーリベッキベスト(現在不使用)']['product']
            self.assertEqual(unused.get('officialName'), 'ムーリベッキベスト')
            self.assertIn('衣装係', by_name['衣装係ストレートパンツ']['product'].get('officialName')
                          or by_name['衣装係ストレートパンツ']['csv']['productName'])

            button = by_name['エプロン用ボタン']
            self.assertEqual(button['product']['category'], '部品・資材')
            self.assertTrue(button['canCommit'])
            ribbon = by_name['青リボン']
            self.assertEqual(ribbon['product']['category'], '部品・資材')
            self.assertEqual(ribbon['product'].get('officialName'), 'リボン')
            self.assertEqual(ribbon['product'].get('proposedColor') or ribbon['product'].get('officialColor'), 'ブルー')
            white = by_name['長白衣ボタン']
            self.assertEqual(white['csv']['supplier'], 'KAZEN WLD(株)')
            self.assertEqual(white['product']['category'], '部品・資材')

            net = by_name['インターネット付け']
            self.assertTrue((net.get('processingService') or {}).get('isProcessingService'))
            self.assertEqual((net.get('processingService') or {}).get('serviceName'), 'インターネット付け')
            self.assertTrue(net['canCommit'], net.get('reasons'))
            self.assertFalse(any('追加文字' in str(r) for r in (net.get('reasons') or [])))
            self.assertEqual(net['csv']['supplier'], '既存加工店')
            for name in ('アジャスター直し', 'スナッパー加工', 'ポケット加工', 'ポケット取付', 'ライン加工', '丈詰め'):
                row = by_name[name]
                self.assertEqual(row['product']['category'], '加工サービス', name)
                self.assertTrue(row['canCommit'], (name, row.get('reasons')))
                self.assertNotEqual(row['status'], 'needs_review', name)

            coat = by_name['コックコート(裾上げ)']
            self.assertEqual(coat['csv']['code'], '6-615')
            self.assertNotEqual(coat['product'].get('category'), '加工サービス')
            self.assertFalse((coat.get('processingService') or {}).get('isProcessingService'))
            self.assertIn('裾上げ', str(coat.get('handle') or {}))
            formal = by_name['シャツコート(不使用)']
            self.assertEqual(formal['csv']['code'], 'APK205-10')
            self.assertNotEqual(formal['csv']['code'], '別注')
            self.assertNotEqual(formal['product'].get('category'), '部品・資材')

            again, _ = preview_of(conn, all_lines())
            for row in again['rows']:
                if row['csv']['productName'] in UNIFORMS or row['csv']['productName'] in (
                    'アジャスター直し', '丈詰め', '青リボン', 'エプロン用ボタン',
                ):
                    self.assertNotEqual(row['status'], 'needs_review', row['csv']['productName'])
                    self.assertFalse(row.get('reviewAccepted'))

            result = pli.commit_preview(conn, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'k2-uniform-needs-code',
            }, db_path=str(path))
            conn.commit()
            self.assertFalse(result.get('error'), result)
            products = list(conn.execute(
                "SELECT id, code, name, color, category, supplier_id FROM products ORDER BY id"
            ))
            by_saved = {row['name']: row for row in products}
            self.assertEqual(by_saved['エプロン ピンク']['code'], '要確認')
            self.assertEqual(by_saved['赤エプロン']['code'], '要確認')
            self.assertNotEqual(by_saved['エプロン ピンク']['id'], by_saved['赤エプロン']['id'])
            self.assertEqual(by_saved['エプロン ピンク']['color'], 'ピンク')
            self.assertEqual(by_saved['赤エプロン']['color'], 'レッド')
            self.assertEqual(by_saved['エプロン ミント']['name'], 'エプロン ミント')
            self.assertEqual(by_saved['ムーリベッキベスト']['code'], '要確認')
            self.assertNotIn('ムーリベッキベスト(現在不使用)', by_saved)
            self.assertEqual(by_saved['衣装係ストレートパンツ']['code'], '要確認')
            self.assertEqual(by_saved['リボン']['color'], 'ブルー')
            self.assertEqual(by_saved['リボン']['code'], '')
            self.assertEqual(by_saved['長白衣ボタン']['code'], '')
            kazen = conn.execute("SELECT id FROM suppliers WHERE name='KAZEN WLD(株)'").fetchone()
            self.assertEqual(by_saved['長白衣ボタン']['supplier_id'], kazen['id'])
            k2 = conn.execute("SELECT id FROM suppliers WHERE name='K2スタッフ工房'").fetchone()
            self.assertEqual(by_saved['エプロン ピンク']['supplier_id'], k2['id'])
            self.assertEqual(by_saved['インターネット付け']['category'], '加工サービス')
            shop = conn.execute("SELECT id FROM suppliers WHERE name='既存加工店'").fetchone()
            self.assertEqual(by_saved['インターネット付け']['supplier_id'], shop['id'])
            formal_row = conn.execute("SELECT code, name, category FROM products WHERE code='APK205-10'").fetchone()
            self.assertIsNotNone(formal_row)
            self.assertEqual(formal_row['code'], 'APK205-10')
            self.assertEqual(formal_row['category'], '制服')
            coat_saved = conn.execute("SELECT category FROM products WHERE code='6-615'").fetchone()
            self.assertEqual(coat_saved['category'], '制服')
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], 0)

            reimport, _ = preview_of(conn, all_lines())
            reused = {row['csv']['productName']: (row.get('product') or {}).get('id') for row in reimport['rows']}
            self.assertEqual(reused['エプロン ピンク'], by_saved['エプロン ピンク']['id'])
            self.assertEqual(reused['赤エプロン'], by_saved['赤エプロン']['id'])
            self.assertEqual(reused['ムーリベッキベスト(現在不使用)'], by_saved['ムーリベッキベスト']['id'])
            self.assertEqual(reused['青リボン'], by_saved['リボン']['id'])
            self.assertNotEqual(reused['エプロン ピンク'], reused['赤エプロン'])
            for name in UNIFORMS:
                self.assertNotEqual(reimport['rows'] and next(
                    r for r in reimport['rows'] if r['csv']['productName'] == name
                )['status'], 'needs_review', name)
        finally:
            conn.close()
            folder.cleanup()


class BespokeCodeRiskTest(unittest.TestCase):
    def test_same_stripped_name_under_betsuchu_would_collide(self):
        """空品番を別注にし、色を名前から落とすと別制服が一つになる。今回はその変換をしない。"""
        folder, _path, conn = newdb()
        try:
            customer = stock.save_customer(conn, {'name': '顧客A', 'active': True})
            supplier = stock.save_supplier(conn, {'name': 'K2スタッフ工房', 'active': True})
            saved = stock.save_product(conn, {
                'code': '別注(顧客A)',
                'name': 'エプロン',
                'category': '制服',
                'supplierId': supplier['id'],
                'color': 'ピンク',
                'sizes': ['L'],
                'links': [],
                'active': True,
                '_allowHistoricalDuplicateCode': True,
            })
            conn.execute(
                '''INSERT INTO customer_products(customer_id,product_id,embroidery,is_standard,active,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)''',
                (customer['id'], saved['id'], '', 1, 1, stock.stamp(), stock.stamp()),
            )
            conn.commit()
            same = legacy.find_custom_order_products_for_customer(
                conn, 'エプロン', customer_id=customer['id'], code='別注',
            )
            other = legacy.find_custom_order_products_for_customer(
                conn, '赤エプロン', customer_id=customer['id'], code='別注',
            )
            self.assertEqual([row['id'] for row in same], [saved['id']])
            self.assertEqual(other, [])
        finally:
            conn.close()
            folder.cleanup()


class ElmsOfficialNameTest(unittest.TestCase):
    def test_official_name_wins_over_alias(self):
        folder, _path, conn = newdb()
        try:
            official = stock.save_supplier(conn, {'name': '(有)エルムスコーポレーション', 'active': True})
            stock.save_supplier(conn, {'name': 'エルム', 'active': True})
            conn.commit()
            found = proc.elms_supplier_matches(conn)
            self.assertEqual(found['status'], 'unique')
            self.assertEqual(found['id'], official['id'])
            self.assertEqual(found['name'], '(有)エルムスコーポレーション')
        finally:
            conn.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
