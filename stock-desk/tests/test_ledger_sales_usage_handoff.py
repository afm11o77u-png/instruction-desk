"""旧CSV: 販売用途表記の商品名除去と初期在庫への取扱種別「販売」引き継ぎ。

テスト用DB／フィクスチャのみ。本番DB・実在庫への書込は行わない。
"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import legacy_csv_bulk_import as bulk  # noqa: E402
import legacy_csv_import as legacy  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import product_ledger_paren as paren  # noqa: E402
import stock  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def b64(text: str, encoding='utf-8-sig') -> str:
    return base64.b64encode(text.encode(encoding)).decode('ascii')


def ledger_csv(rows):
    header = '顧客,仕入先,商品コード,商品名,サイズ'
    return '\n'.join([header] + rows)


def opening_csv(rows):
    header = '新古,仕入先,顧客,商品名,商品コード,サイズ,仕入単価,購入数量,出荷数,在庫,拠点,棚,補正在庫数'
    return '\n'.join([header] + rows)


class SalesUsageMarkerUnitTest(unittest.TestCase):
    def test_strip_identifiable_markers_only(self):
        samples = [
            ('コックコート（販売）', 'コックコート'),
            ('キャップ（販売用）', 'キャップ'),
            ('白衣(販売)', '白衣'),
            ('ベスト【販売】', 'ベスト'),
            ('アームカバー【販売分】', 'アームカバー'),
            ('パンツ（販売品）', 'パンツ'),
        ]
        for original, expected in samples:
            with self.subTest(original=original):
                r = paren.classify_product_name(original)
                self.assertEqual(r['plannedName'], expected)
                self.assertEqual(r['usageKind'], paren.USAGE_KIND_SALES)
                self.assertFalse(r['needsConfirm'])
                self.assertTrue(r['autoClassified'])
                self.assertEqual(r['originalName'], original)

    def test_do_not_strip_bare_sales_substring(self):
        for name in ('天寿会販売', '販売商事エプロン', 'KAZEN販売'):
            with self.subTest(name=name):
                has, markers = paren.detect_sales_usage_marker(name)
                self.assertFalse(has)
                self.assertEqual(markers, [])
                r = paren.classify_product_name(name)
                self.assertEqual(r['plannedName'], name)
                self.assertEqual(r.get('usageKind') or '', '')

    def test_sales_marker_does_not_split_product_core(self):
        self.assertEqual(
            legacy.product_name_core_key('コックコート（販売）'),
            legacy.product_name_core_key('コックコート'),
        )
        # 色・加工の実差は残す
        self.assertNotEqual(
            legacy.product_name_core_key('ブルゾン（紺）'),
            legacy.product_name_core_key('ブルゾン（赤）'),
        )
        self.assertNotEqual(
            legacy.product_name_core_key('白衣（刺繍）'),
            legacy.product_name_core_key('白衣'),
        )

    def test_customer_marker_detect_without_rename_helper(self):
        has, markers = paren.detect_sales_usage_marker('顧客A（販売）')
        self.assertTrue(has)
        self.assertEqual(markers, ['販売'])
        # ヘルパーは検知のみ。顧客名の自動リネームはしない
        cleaned, kind = paren.strip_sales_usage_markers('顧客A（販売）')
        self.assertEqual(kind, paren.USAGE_KIND_SALES)
        self.assertEqual(cleaned, '顧客A')

    def test_compose_handling_keeps_processing(self):
        self.assertEqual(paren.compose_handling_embroidery('', '販売'), '販売')
        self.assertEqual(
            paren.compose_handling_embroidery('DHC刺繍あり', '販売'),
            'DHC刺繍あり｜販売',
        )
        self.assertTrue(paren.handling_has_sales_usage('販売'))
        self.assertTrue(paren.handling_has_sales_usage('刺繍｜販売'))
        self.assertFalse(paren.handling_has_sales_usage(''))
        self.assertFalse(paren.handling_has_sales_usage('刺繍'))


class SalesUsageProductRegisterTest(unittest.TestCase):
    def test_products_mode_strips_name_sets_sales_handle_no_stock(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P001,コックコート（販売）,L',
                '顧客A,仕入先X,P001,コックコート,M',
            ])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview = pli.build_preview(c, body)
            self.assertEqual(preview.get('importMode'), 'products')
            self.assertIsNone(preview.get('openingStock'))
            rows = {r['rowNo']: r for r in preview['rows']}
            sales = rows[2]
            rental = rows[3]
            pc_s = sales.get('parenClassification') or {}
            self.assertEqual(pc_s.get('plannedName'), 'コックコート')
            self.assertEqual(pc_s.get('usageKind'), '販売')
            self.assertEqual(pc_s.get('originalName'), 'コックコート（販売）')
            self.assertFalse(pc_s.get('needsConfirm'))
            self.assertEqual((sales.get('handle') or {}).get('embroidery'), '販売')
            self.assertEqual((sales.get('handle') or {}).get('usageKind'), '販売')
            # マーカー無し行は販売を断定しない
            self.assertNotEqual((rental.get('handle') or {}).get('embroidery'), '販売')
            self.assertNotEqual((rental.get('handle') or {}).get('usageKind'), '販売')
            # 同一商品へまとめ（販売マーカーだけで商品分裂しない）
            snap = pli.stock_snapshot(c)
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'sales-usage-products-1',
            }, db_path=path)
            self.assertTrue(result.get('stockUnchanged'))
            self.assertEqual(pli.stock_snapshot(c), snap)
            self.assertEqual(result['counts']['productsCreated'], 1)
            prods = c.execute('SELECT id, name FROM products').fetchall()
            self.assertEqual(len(prods), 1)
            self.assertEqual(prods[0]['name'], 'コックコート')
            handles = c.execute(
                'SELECT embroidery FROM customer_products ORDER BY embroidery'
            ).fetchall()
            embs = {h['embroidery'] for h in handles}
            self.assertIn('販売', embs)
            # 加工なし（空）取扱も残る
            self.assertTrue('' in embs or any(e != '販売' for e in embs))
            # 原文対応をマップへ保持
            maps = c.execute(
                'SELECT csv_product_name, spec_json, customer_product_id FROM product_ledger_row_maps'
            ).fetchall()
            self.assertGreaterEqual(len(maps), 2)
            by_name = {m['csv_product_name']: m for m in maps}
            self.assertIn('コックコート（販売）', by_name)
            import json
            spec = json.loads(by_name['コックコート（販売）']['spec_json'])
            self.assertEqual(spec.get('usageKind'), '販売')
            self.assertEqual(spec.get('plannedName'), 'コックコート')
            self.assertEqual(spec.get('originalName'), 'コックコート（販売）')
        finally:
            c.close()
            folder.cleanup()

    def test_customer_name_sales_marker_sets_handling_keeps_customer_name(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客B（販売）,仕入先X,P002,キャップ,F',
            ])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview = pli.build_preview(c, body)
            row = preview['rows'][0]
            self.assertEqual((row.get('handle') or {}).get('embroidery'), '販売')
            self.assertEqual((row.get('parenClassification') or {}).get('usageKind'), '販売')
            self.assertTrue((row.get('parenClassification') or {}).get('usageFromCustomerName'))
            # 商品名にマーカーなし → 予定名はそのまま
            self.assertEqual(
                (row.get('parenClassification') or {}).get('plannedName') or row['csv']['productName'],
                'キャップ',
            )
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'sales-usage-cust-1',
            }, db_path=path)
            self.assertTrue(result.get('stockUnchanged'))
            cust = c.execute('SELECT name FROM customers').fetchone()
            # 顧客マスタは自動リネームしない
            self.assertEqual(cust['name'], '顧客B（販売）')
            handle = c.execute('SELECT embroidery FROM customer_products').fetchone()
            self.assertEqual(handle['embroidery'], '販売')
        finally:
            c.close()
            folder.cleanup()


class SalesUsageOpeningHandoffTest(unittest.TestCase):
    def test_opening_matches_stripped_product_and_keeps_sales_separate(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P001,コックコート（販売）,L',
                '顧客A,仕入先X,P001,コックコート,L',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,コックコート（販売）,P001,L,1000,0,0,3,本社,棚1,0',
                '新品,仕入先X,顧客A,コックコート,P001,L,1000,0,0,5,本社,棚1,0',
            ])
            stage1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview1 = pli.build_preview(c, stage1)
            snap = pli.stock_snapshot(c)
            result1 = pli.commit_preview(c, {
                **stage1,
                'previewFingerprint': preview1['previewFingerprint'],
                'previewAt': preview1['previewAt'],
                'token': 'sales-usage-stage1',
            }, db_path=path)
            self.assertTrue(result1.get('stockUnchanged'))
            self.assertEqual(pli.stock_snapshot(c), snap)

            stage2 = {
                'importMode': 'opening_stock',
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                'basisDate': '2024-04-01',
                'defaultSiteName': '未確認',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview2 = pli.build_preview(c, stage2)
            self.assertEqual(preview2.get('importMode'), 'opening_stock')
            os_ = preview2['openingStock']
            self.assertTrue(os_.get('fromPersistedMap'))
            plans = os_['plans']
            self.assertEqual(len(plans), 2)
            by_src = {(p.get('csv') or {}).get('productName'): p for p in plans}
            sales_plan = by_src['コックコート（販売）']
            other_plan = by_src['コックコート']
            # 数量を落としたり 0 にしない
            self.assertEqual(sales_plan['qty'], 3)
            self.assertEqual(other_plan['qty'], 5)
            self.assertTrue(sales_plan.get('matchedLedger'))
            self.assertTrue(other_plan.get('matchedLedger'))
            self.assertEqual((sales_plan.get('target') or {}).get('embroidery'), '販売')
            self.assertEqual((sales_plan.get('target') or {}).get('usageKind'), '販売')
            self.assertNotEqual((other_plan.get('target') or {}).get('embroidery'), '販売')
            # 別取扱 → 合算しない
            self.assertNotEqual(
                (sales_plan.get('target') or {}).get('handleId'),
                (other_plan.get('target') or {}).get('handleId'),
            )
            # プレビューでフィルタ可能な取扱ラベル
            self.assertEqual((sales_plan.get('target') or {}).get('handlingType'), '販売')

            # 確定（テストDBのみ）
            result2 = pli.commit_preview(c, {
                **stage2,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'sales-usage-stage2',
            }, db_path=path)
            self.assertEqual(result2.get('importMode'), 'opening_stock')
            self.assertGreaterEqual(result2['counts'].get('openingCreated', 0), 2)
            lots = c.execute(
                '''SELECT l.qty_remaining, cp.embroidery
                   FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
                   ORDER BY cp.embroidery'''
            ).fetchall()
            by_emb = {r['embroidery']: r['qty_remaining'] for r in lots}
            self.assertEqual(by_emb.get('販売'), 3)
            # 加工なし側
            non_sales = [r for r in lots if r['embroidery'] != '販売']
            self.assertEqual(len(non_sales), 1)
            self.assertEqual(non_sales[0]['qty_remaining'], 5)
        finally:
            c.close()
            folder.cleanup()

    def test_ambiguous_after_strip_needs_confirm(self):
        folder, path, c = newdb()
        try:
            # 先に販売／非販売の2取扱を登録
            ledger = ledger_csv([
                '顧客A,仕入先X,P010,コート（販売）,L',
                '顧客A,仕入先X,P010,コート,L',
            ])
            stage1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview1 = pli.build_preview(c, stage1)
            pli.commit_preview(c, {
                **stage1,
                'previewFingerprint': preview1['previewFingerprint'],
                'previewAt': preview1['previewAt'],
                'token': 'sales-amb-1',
            }, db_path=path)

            # 初期在庫名がマーカー除去後に両方へ当たるが、用途不明な別表記は曖昧に
            # → 販売マーカー付きは販売へ一意に絞れる
            opening_sales = opening_csv([
                '新品,仕入先X,顧客A,コート（販売）,P010,L,1000,0,0,2,本社,棚1,0',
            ])
            resolved = bulk.lookup_ledger_destinations_for_aux(
                c, customer='顧客A', code='P010', product_name='コート（販売）',
                size='L', supplier='仕入先X',
            )
            self.assertEqual(resolved.get('status'), 'ok')
            self.assertEqual(
                (resolved.get('destination') or {}).get('usageKind')
                or paren.usage_kind_from_embroidery(
                    (resolved.get('destination') or {}).get('embroidery') or ''
                ),
                '販売',
            )

            # マーカー無しで両候補が残る場合は先頭自動採用しない
            resolved2 = bulk.lookup_ledger_destinations_for_aux(
                c, customer='顧客A', code='P010', product_name='コート',
                size='L', supplier='仕入先X',
            )
            # 非販売へ絞れる（用途マーカー無し → 販売以外を優先）か、曖昧
            if resolved2.get('status') == 'ok':
                self.assertFalse(paren.handling_has_sales_usage(
                    (resolved2.get('destination') or {}).get('embroidery') or ''
                ))
            else:
                self.assertEqual(resolved2.get('status'), 'ambiguous')
                self.assertGreater(len(resolved2.get('matches') or []), 1)
        finally:
            c.close()
            folder.cleanup()

    def test_usage_conflict_between_opening_and_map_needs_confirm(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P020,シャツ（販売）,L',
            ])
            stage1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview1 = pli.build_preview(c, stage1)
            pli.commit_preview(c, {
                **stage1,
                'previewFingerprint': preview1['previewFingerprint'],
                'previewAt': preview1['previewAt'],
                'token': 'sales-conflict-1',
            }, db_path=path)

            # 初期在庫側は販売マーカー無し（マップは販売）→ 衝突確認
            opening = opening_csv([
                '新品,仕入先X,顧客A,シャツ,P020,L,1000,0,0,4,本社,棚1,0',
            ])
            stage2 = {
                'importMode': 'opening_stock',
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                'basisDate': '2024-04-01',
                'defaultSiteName': '未確認',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview2 = pli.build_preview(c, stage2)
            plan = preview2['openingStock']['plans'][0]
            # 数量は維持
            self.assertEqual(plan['qty'], 4)
            target = plan.get('target') or {}
            # 衝突フラグまたは要確認
            self.assertTrue(
                target.get('usageConflict')
                or plan.get('status') == legacy.STATUS_REVIEW
                or any('販売用途' in (r or '') for r in (plan.get('reasons') or []))
            )
        finally:
            c.close()
            folder.cleanup()


class SalesUsageUiWiringTest(unittest.TestCase):
    def test_opening_preview_has_usage_filter_and_column(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('plOpenUsageFilter', js)
        self.assertIn('openingUsageFilter', js)
        self.assertIn('取扱種別', js)
        self.assertIn('usageKind', js)
        self.assertIn('usageConflict', js)


if __name__ == '__main__':
    unittest.main()

