"""旧CSV判断一覧 — 色別に分けて登録（テストDBのみ。本番登録・在庫変更なし）。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import legacy_csv_bulk_import as bulk  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402
import stock_orders  # noqa: E402


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


class ColorSplitHelpersTest(unittest.TestCase):
    def test_suggest_internal_keeps_leading_zeros(self):
        self.assertEqual(
            pli.suggest_color_split_internal_code('00008', 'レッド'),
            '00008-レッド',
        )
        self.assertEqual(
            pli.suggest_color_split_internal_code('ABC-100', 'ブルー'),
            'ABC-100-ブルー',
        )

    def test_green_and_fluorescent_are_distinct(self):
        folder, path, c = newdb()
        try:
            group = {
                'code': 'G1',
                'rowNos': [1, 2],
                'variants': [
                    {'productName': 'パンツ（緑）', 'rowNos': [1], 'csvNames': ['パンツ（緑）'], 'supplier': 'S', 'sizes': ['L'], 'customers': ['C']},
                    {'productName': 'パンツ（蛍光グリーン）', 'rowNos': [2], 'csvNames': ['パンツ（蛍光グリーン）'], 'supplier': 'S', 'sizes': ['L'], 'customers': ['C']},
                ],
            }
            classified = [
                {'rowNo': 1, 'csv': {'code': 'G1', 'productName': 'パンツ（緑）', 'supplier': 'S', 'size': 'L', 'customer': 'C'}},
                {'rowNo': 2, 'csv': {'code': 'G1', 'productName': 'パンツ（蛍光グリーン）', 'supplier': 'S', 'size': 'L', 'customer': 'C'}},
            ]
            plans = pli.build_color_split_plans(c, group, {'decision': 'color_split'}, classified=classified)
            colors = {p['color'] for p in plans['plans']}
            self.assertIn('グリーン', colors)
            self.assertIn('蛍光グリーン', colors)
            self.assertEqual(len(plans['plans']), 2)
        finally:
            c.close()
            folder.cleanup()


class ColorSplitPreviewCommitTest(unittest.TestCase):
    def test_normal_product_unaffected(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv(['顧客A,仕入先X,N001,パンツ,L'])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            self.assertFalse(any(g.get('colorSplit') for g in preview.get('skuGroups') or []))
            before = pli.stock_snapshot(c)
            result = pli.commit_preview(c, {
                **body,
                'commitToken': 'tok-normal-1',
                'previewFingerprint': preview['previewFingerprint'],
            }, db_path=None)
            self.assertEqual(result['counts']['productsCreated'], 1)
            after = pli.stock_snapshot(c)
            self.assertEqual(before, after)
            prod = c.execute("SELECT code,maker_code,color,name FROM products WHERE code='N001'").fetchone()
            self.assertEqual(prod['code'], 'N001')
            self.assertEqual(prod['maker_code'] or '', '')
        finally:
            c.close()
            folder.cleanup()

    def test_red_blue_split_creates_two_products(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,ABC-100,ポロシャツ（赤）,L',
                '顧客A,仕入先X,ABC-100,ポロシャツ（青）,M',
            ])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'skuDecisions': {
                    'ABC-100': {'decision': 'color_split'},
                },
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            group = next(g for g in preview['skuGroups'] if g['code'] == 'ABC-100')
            self.assertTrue(group.get('colorSplit') or group.get('excludeAuxStockAutoMerge'))
            split = group.get('colorSplitPlans') or {}
            self.assertTrue(split.get('complete'), split)
            colors = {p['color'] for p in split['plans']}
            self.assertEqual(colors, {'レッド', 'ブルー'})
            for p in split['plans']:
                self.assertEqual(p['makerCode'], 'ABC-100')
                self.assertEqual(p['internalCode'], f"ABC-100-{p['color']}")
                self.assertFalse(p.get('needsCollisionChoice'))

            # クリックだけでは保存されない
            self.assertEqual(
                c.execute("SELECT COUNT(*) AS n FROM products").fetchone()['n'], 0,
            )

            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                result = pli.commit_preview(c, {
                    **body,
                    'commitToken': 'tok-split-1',
                    'previewFingerprint': preview['previewFingerprint'],
                }, db_path=path)
            self.assertEqual(result['counts']['productsCreated'], 2)
            rows = list(c.execute(
                "SELECT code,maker_code,name,color FROM products WHERE maker_code='ABC-100' ORDER BY color"
            ))
            self.assertEqual(len(rows), 2)
            by_color = {r['color']: r for r in rows}
            self.assertEqual(by_color['レッド']['code'], 'ABC-100-レッド')
            self.assertEqual(by_color['ブルー']['code'], 'ABC-100-ブルー')
            self.assertEqual(by_color['レッド']['maker_code'], 'ABC-100')
            self.assertEqual(by_color['レッド']['name'], 'ポロシャツ')
            # マップはメーカー品番のまま
            maps = list(c.execute(
                "SELECT csv_product_code,csv_color,product_id FROM product_ledger_row_maps ORDER BY id"
            ))
            self.assertEqual(len(maps), 2)
            self.assertTrue(all(m['csv_product_code'] == 'ABC-100' for m in maps))
            self.assertEqual({m['csv_color'] for m in maps}, {'レッド', 'ブルー'})
        finally:
            c.close()
            folder.cleanup()

    def test_unknown_color_not_auto_assigned(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,ABC-100,ポロシャツ（赤）,L',
                '顧客A,仕入先X,ABC-100,ポロシャツ,M',
            ])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'skuDecisions': {'ABC-100': {'decision': 'color_split'}},
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            group = next(g for g in preview['skuGroups'] if g['code'] == 'ABC-100')
            split = group['colorSplitPlans']
            self.assertFalse(split['complete'])
            self.assertTrue(split['unassignedRows'])
            # 未割当があるうちはコミット不可
            pending = [
                r for r in preview['rows']
                if r.get('status') == 'needs_review' and '色別' in ' '.join(r.get('reasons') or [])
            ]
            self.assertTrue(pending)
        finally:
            c.close()
            folder.cleanup()

    def test_sku_collision_requires_choice(self):
        folder, path, c = newdb()
        try:
            stock.save_product(c, {
                'code': 'ABC-100-レッド', 'name': '既存ポロ', 'color': 'レッド',
                'sizes': ['L'], 'links': [], 'active': True,
            })
            ledger = ledger_csv([
                '顧客A,仕入先X,ABC-100,ポロシャツ（赤）,L',
                '顧客A,仕入先X,ABC-100,ポロシャツ（青）,M',
            ])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'skuDecisions': {'ABC-100': {'decision': 'color_split'}},
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            group = next(g for g in preview['skuGroups'] if g['code'] == 'ABC-100')
            red = next(p for p in group['colorSplitPlans']['plans'] if p['color'] == 'レッド')
            self.assertTrue(red['needsCollisionChoice'])
            self.assertTrue(red['existingProducts'])

            # 既存利用を選ぶと完了
            existing_id = red['existingProducts'][0]['id']
            body['skuDecisions'] = {
                'ABC-100': {
                    'decision': 'color_split',
                    'colorPlans': {
                        red['colorKey']: {
                            'color': 'レッド',
                            'internalCode': 'ABC-100-レッド',
                            'keepProductId': existing_id,
                            'collisionAction': 'reuse',
                            'productName': 'ポロシャツ',
                        },
                    },
                },
            }
            preview2 = pli.build_preview(c, body)
            group2 = next(g for g in preview2['skuGroups'] if g['code'] == 'ABC-100')
            red2 = next(p for p in group2['colorSplitPlans']['plans'] if p['color'] == 'レッド')
            self.assertFalse(red2['needsCollisionChoice'])
        finally:
            c.close()
            folder.cleanup()

    def test_excluded_from_aux_stock_auto_merge_and_kept_on_rematch(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,ABC-100,ポロシャツ（赤）,L',
                '顧客A,仕入先X,ABC-100,ポロシャツ（青）,M',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,ポロシャツ（赤）,ABC-100,L,1000,0,0,9,本社,棚1,0',
                '新品,仕入先X,顧客A,ポロシャツ（青）,ABC-100,M,1000,0,0,1,本社,棚1,0',
            ])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                'skuDecisions': {'ABC-100': {'decision': 'color_split'}},
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            group = next(g for g in preview['skuGroups'] if g['code'] == 'ABC-100')
            self.assertTrue(group.get('excludeAuxStockAutoMerge') or group.get('colorSplit'))
            self.assertFalse(group.get('auxStockPickAuto'))
            # 再照合でも decision 保持
            preview2 = pli.build_preview(c, body)
            group2 = next(g for g in preview2['skuGroups'] if g['code'] == 'ABC-100')
            self.assertEqual((group2.get('decision') or {}).get('decision'), 'color_split')
            self.assertFalse(group2.get('auxStockPickAuto'))
        finally:
            c.close()
            folder.cleanup()

    def test_handling_size_carryover_and_opening_match(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,ABC-100,ポロシャツ（赤）,L',
                '顧客B,仕入先X,ABC-100,ポロシャツ（青）,M',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,ポロシャツ（赤）,ABC-100,L,1000,0,0,2,本社,棚1,0',
                '新品,仕入先X,顧客B,ポロシャツ（青）,ABC-100,M,1000,0,0,5,, ,0',
            ])
            stage1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                'skuDecisions': {'ABC-100': {'decision': 'color_split'}},
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, stage1)
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(c, {
                    **stage1,
                    'commitToken': 'tok-stage1',
                    'previewFingerprint': preview['previewFingerprint'],
                }, db_path=path)

            red = c.execute("SELECT id FROM products WHERE code='ABC-100-レッド'").fetchone()
            blue = c.execute("SELECT id FROM products WHERE code='ABC-100-ブルー'").fetchone()
            self.assertTrue(red and blue)
            # 取扱が顧客ごとに載る
            handles = list(c.execute('SELECT customer_id,product_id FROM customer_products'))
            self.assertEqual(len(handles), 2)

            stage2 = {
                'importMode': 'opening_stock',
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                'basisDate': '2026-01-15',
                'defaultSiteName': '未確認',
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            prev2 = pli.build_preview(c, stage2)
            plans = (prev2.get('openingStock') or {}).get('plans') or []
            self.assertTrue(plans)
            # 色付き名で正しい商品へ
            by_name = {((p.get('csv') or {}).get('productName') or ''): p for p in plans}
            red_plan = by_name.get('ポロシャツ（赤）')
            blue_plan = by_name.get('ポロシャツ（青）')
            self.assertIsNotNone(red_plan)
            self.assertIsNotNone(blue_plan)
            self.assertEqual((red_plan.get('target') or {}).get('productId'), red['id'])
            self.assertEqual((blue_plan.get('target') or {}).get('productId'), blue['id'])

            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                r2 = pli.commit_preview(c, {
                    **stage2,
                    'commitToken': 'tok-stage2',
                    'previewFingerprint': prev2['previewFingerprint'],
                }, db_path=path)
            self.assertGreaterEqual(r2['counts'].get('openingCreated', 0), 2)

            # 再取込で二重加算しない（変更なし行のみ → 空コミット許可）
            prev3 = pli.build_preview(c, stage2)
            plans3 = (prev3.get('openingStock') or {}).get('plans') or []
            self.assertTrue(all(p.get('status') in ('unchanged', 'reuse') for p in plans3))
            qty_before = c.execute('SELECT SUM(qty_remaining) AS q FROM lots').fetchone()['q']
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                r3 = pli.commit_preview(c, {
                    **stage2,
                    'commitToken': 'tok-stage2-re',
                    'previewFingerprint': prev3['previewFingerprint'],
                    'allowEmptyCommit': True,
                }, db_path=path)
            self.assertEqual(r3['counts'].get('openingCreated', 0), 0)
            qty_after = c.execute('SELECT SUM(qty_remaining) AS q FROM lots').fetchone()['q']
            self.assertEqual(qty_before, qty_after)
        finally:
            c.close()
            folder.cleanup()

    def test_ambiguous_opening_without_color_not_auto(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,ABC-100,ポロシャツ（赤）,L',
                '顧客A,仕入先X,ABC-100,ポロシャツ（青）,L',
            ])
            stage1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'skuDecisions': {'ABC-100': {'decision': 'color_split'}},
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, stage1)
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(c, {
                    **stage1,
                    'commitToken': 'tok-amb1',
                    'previewFingerprint': preview['previewFingerprint'],
                }, db_path=path)

            # 色なし・同一名だと曖昧（一方へ自動寄せしない）
            opening = opening_csv([
                '新品,仕入先X,顧客A,ポロシャツ,ABC-100,L,1000,0,0,3,本社,棚1,0',
            ])
            lookup = bulk.lookup_ledger_destinations_for_aux(
                c, customer='顧客A', code='ABC-100', product_name='ポロシャツ',
                size='L', supplier='仕入先X',
            )
            # 名が一致しない／複数色マップ → missing or ambiguous
            self.assertIn(lookup['status'], ('ambiguous', 'missing'))
        finally:
            c.close()
            folder.cleanup()

    def test_order_shows_maker_code_not_internal(self):
        folder, path, c = newdb()
        try:
            cust = stock.save_customer(c, {'name': '顧客A', 'active': True})
            sup = stock.save_supplier(c, {'name': '仕入先X', 'active': True})
            prod = stock.save_product(c, {
                'code': 'ABC-100-赤',
                'makerCode': 'ABC-100',
                'name': 'ポロシャツ',
                'color': '赤',
                'supplierId': sup['id'],
                'sizes': ['L'],
                'links': [{'customerId': cust['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
                'active': True,
            })
            cp = c.execute(
                'SELECT id FROM customer_products WHERE product_id=?', (prod['id'],)
            ).fetchone()
            cands = stock_orders.product_candidates(c, {
                'customerId': cust['id'], 'supplierId': sup['id'],
            })
            item = next(x for x in cands['products'] if x['productId'] == prod['id'])
            self.assertEqual(item['code'], 'ABC-100')
            self.assertEqual(item['internalCode'], 'ABC-100-赤')
            self.assertIn('赤', item['label'])
            self.assertIn('社内', item['label'])

            lines = stock_orders._normalize_lines(c, {
                'lines': [{'customerProductId': cp['id'], 'sizes': {'L': 2}}],
            }, cust['id'], sup['id'])
            self.assertEqual(lines[0]['productCode'], 'ABC-100')
            self.assertEqual(lines[0]['productColor'], '赤')
            self.assertNotEqual(lines[0]['productCode'], 'ABC-100-赤')

            html = stock_orders.render_po_html({
                'number': 'PO-1',
                'supplierName': '仕入先X',
                'customerName': '顧客A',
                'company': {},
                'lines': [{
                    'productName': 'ポロシャツ',
                    'color': '赤',
                    'code': 'ABC-100',
                    'internalCode': 'ABC-100-赤',
                    'size': 'L',
                    'qtyOrdered': 2,
                    'lineNote': '',
                }],
            })
            self.assertIn('品番 ABC-100', html)
            self.assertIn('色 赤', html)
            self.assertIn('社内 ABC-100-赤', html)
        finally:
            c.close()
            folder.cleanup()


class ColorSplitNameMapAndOutsideColorTest(unittest.TestCase):
    """109-PCT / Tシャツ白・黒 → Tシャツ/ホワイト・Tシャツ/ブラック（石屋商事）。"""

    def test_outside_color_splits_to_name_slash_color(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,石屋商事,109-PCT,Tシャツ白,S',
                '顧客A,石屋商事,109-PCT,Tシャツ白,M',
                '顧客A,石屋商事,109-PCT,Tシャツ白,L',
                '顧客A,石屋商事,109-PCT,Tシャツ白,LL',
                '顧客A,石屋商事,109-PCT,Tシャツ黒,S',
                '顧客A,石屋商事,109-PCT,Tシャツ黒,M',
                '顧客A,石屋商事,109-PCT,Tシャツ黒,L',
                '顧客A,石屋商事,109-PCT,Tシャツ黒,LL',
            ])
            opening = opening_csv([
                '新品,石屋商事,顧客A,Tシャツ白,109-PCT,S,1000,0,0,10,本社,棚1,0',
                '新品,石屋商事,顧客A,Tシャツ黒,109-PCT,S,1000,0,0,1,本社,棚1,0',
            ])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                'skuDecisions': {'109-PCT': {'decision': 'color_split'}},
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            group = next(g for g in preview['skuGroups'] if g['code'] == '109-PCT')
            self.assertTrue(group.get('colorOnlySplitRecommended') or group.get('colorDiffPossible'))
            self.assertEqual(group.get('plannedOfficialName'), '色別登録：ブラック・ホワイト')
            self.assertIn('色別に分けて登録', group.get('nameAdoptionRule') or '')
            np = group.get('namePlan') or {}
            self.assertTrue(np.get('colorSplit'))
            self.assertEqual(np.get('adoptionRuleCode'), 'color_split')
            # 統合用の「黒→白（別名）」が出ないこと
            for m in np.get('nameMappings') or []:
                self.assertFalse(m.get('unassigned'))
                self.assertFalse(m.get('isAlias'))
                if '黒' in (m.get('originalName') or ''):
                    self.assertEqual(m.get('registrationName'), 'Tシャツ')
                    self.assertEqual(m.get('color'), 'ブラック')
                    self.assertNotEqual(m.get('registrationName'), 'Tシャツ白')
                if '白' in (m.get('originalName') or ''):
                    self.assertEqual(m.get('registrationName'), 'Tシャツ')
                    self.assertEqual(m.get('color'), 'ホワイト')
            split = group['colorSplitPlans']
            self.assertTrue(split.get('complete'), split)
            by_color = {p['color']: p for p in split['plans']}
            self.assertEqual(by_color['ホワイト']['productName'], 'Tシャツ')
            self.assertEqual(by_color['ブラック']['productName'], 'Tシャツ')
            self.assertEqual(by_color['ホワイト']['displayLabel'], 'Tシャツ/ホワイト')
            self.assertEqual(by_color['ブラック']['displayLabel'], 'Tシャツ/ブラック')
            self.assertEqual(by_color['ホワイト']['supplier'], '石屋商事')
            self.assertEqual(by_color['ブラック']['supplier'], '石屋商事')
            self.assertEqual(sorted(by_color['ホワイト']['sizes']), ['L', 'LL', 'M', 'S'])
            self.assertEqual(sorted(by_color['ブラック']['sizes']), ['L', 'LL', 'M', 'S'])
            self.assertEqual(sorted(by_color['ホワイト']['rowNos']), [2, 3, 4, 5])
            self.assertEqual(sorted(by_color['ブラック']['rowNos']), [6, 7, 8, 9])
            self.assertEqual(by_color['ホワイト']['internalCode'], '109-PCT-ホワイト')
            self.assertEqual(by_color['ブラック']['internalCode'], '109-PCT-ブラック')
            self.assertEqual(by_color['ホワイト']['makerCode'], '109-PCT')

            # 行の登録予定も色別に一致
            for r in preview['rows']:
                if (r.get('csv') or {}).get('code') != '109-PCT':
                    continue
                prod = r.get('product') or {}
                orig = (r.get('csv') or {}).get('productName') or ''
                if '白' in orig:
                    self.assertEqual(prod.get('officialName'), 'Tシャツ')
                    self.assertEqual(prod.get('officialColor'), 'ホワイト')
                    self.assertEqual(prod.get('code'), '109-PCT-ホワイト')
                else:
                    self.assertEqual(prod.get('officialName'), 'Tシャツ')
                    self.assertEqual(prod.get('officialColor'), 'ブラック')
                    self.assertEqual(prod.get('code'), '109-PCT-ブラック')
                # 別色の nameMappings を載せない
                for m in prod.get('nameMappings') or []:
                    self.assertEqual(m.get('color'), prod.get('officialColor'))

            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                result = pli.commit_preview(c, {
                    **body,
                    'commitToken': 'tok-109pct-1',
                    'previewFingerprint': preview['previewFingerprint'],
                }, db_path=path)
            self.assertEqual(result['counts']['productsCreated'], 2)
            rows = list(c.execute(
                "SELECT id,code,maker_code,name,color FROM products WHERE maker_code='109-PCT' ORDER BY color"
            ))
            self.assertEqual(len(rows), 2)
            by_c = {r['color']: r for r in rows}
            self.assertEqual(by_c['ホワイト']['name'], 'Tシャツ')
            self.assertEqual(by_c['ブラック']['name'], 'Tシャツ')
            self.assertEqual(by_c['ホワイト']['code'], '109-PCT-ホワイト')
            self.assertEqual(by_c['ブラック']['code'], '109-PCT-ブラック')

            # 別名: 白商品に黒名が付いていない / 黒商品に白名が付いていない（黒↔白エイリアスなし）
            white_aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE product_id=? AND kind='name'",
                    (by_c['ホワイト']['id'],),
                )
            }
            black_aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE product_id=? AND kind='name'",
                    (by_c['ブラック']['id'],),
                )
            }
            self.assertNotIn('Tシャツ黒', white_aliases)
            self.assertNotIn('Tシャツ白', black_aliases)
            # 初期在庫照合用に原文は残す
            self.assertIn('Tシャツ白', white_aliases)
            self.assertIn('Tシャツ黒', black_aliases)

            # 初期在庫: 旧名称から白→白・黒→黒。共通メーカー品番だけで片方へ寄せない
            stage2 = {
                'importMode': 'opening_stock',
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                'basisDate': '2026-01-15',
                'defaultSiteName': '未確認',
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            prev2 = pli.build_preview(c, stage2)
            plans = (prev2.get('openingStock') or {}).get('plans') or []
            by_name = {((p.get('csv') or {}).get('productName') or ''): p for p in plans}
            self.assertEqual((by_name['Tシャツ白'].get('target') or {}).get('productId'), by_c['ホワイト']['id'])
            self.assertEqual((by_name['Tシャツ黒'].get('target') or {}).get('productId'), by_c['ブラック']['id'])
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                r2 = pli.commit_preview(c, {
                    **stage2,
                    'commitToken': 'tok-109pct-open',
                    'previewFingerprint': prev2['previewFingerprint'],
                }, db_path=path)
            self.assertGreaterEqual(r2['counts'].get('openingCreated', 0), 2)
            qty_white = c.execute(
                '''SELECT SUM(l.qty_remaining) AS q FROM lots l
                   JOIN customer_products cp ON cp.id=l.customer_product_id
                   WHERE cp.product_id=?''',
                (by_c['ホワイト']['id'],),
            ).fetchone()['q']
            qty_black = c.execute(
                '''SELECT SUM(l.qty_remaining) AS q FROM lots l
                   JOIN customer_products cp ON cp.id=l.customer_product_id
                   WHERE cp.product_id=?''',
                (by_c['ブラック']['id'],),
            ).fetchone()['q']
            self.assertEqual(qty_white, 10)
            self.assertEqual(qty_black, 1)
        finally:
            c.close()
            folder.cleanup()

    def test_merge_name_plan_restored_after_leaving_color_split(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,石屋商事,109-PCT,Tシャツ白,L',
                '顧客A,石屋商事,109-PCT,Tシャツ黒,L',
            ])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'skuDecisions': {'109-PCT': {'decision': 'color_split'}},
                '_canWrite': True,
                '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            group = next(g for g in preview['skuGroups'] if g['code'] == '109-PCT')
            self.assertTrue((group.get('namePlan') or {}).get('colorSplit'))

            body['skuDecisions'] = {'109-PCT': {'decision': 'merge'}}
            preview2 = pli.build_preview(c, body)
            group2 = next(g for g in preview2['skuGroups'] if g['code'] == '109-PCT')
            np2 = group2.get('namePlan') or {}
            self.assertFalse(np2.get('colorSplit'))
            self.assertNotEqual(np2.get('adoptionRuleCode'), 'color_split')
            self.assertNotIn('色別登録', group2.get('plannedOfficialName') or '')
        finally:
            c.close()
            folder.cleanup()


class ColorSplitUiWiringTest(unittest.TestCase):
    def test_ui_has_color_split_controls(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('色別に分けて登録', js)
        self.assertIn('color_split', js)
        self.assertIn('colorSplitFormHtml', js)
        self.assertIn('colorPlans', js)
        self.assertIn('社内管理品番', js)
        self.assertIn('colorSplitSummaryForGroup', js)
        self.assertIn('colorSplitNameMappings', js)
        self.assertIn('色別分割案・未反映', js)
        self.assertIn('今回は色別分割が適しています', js)
        self.assertIn('確認して一括登録（保留・登録しない項目を除く）', js)
        self.assertIn('prefersColorSplit', js)
        self.assertNotIn("paintDialog({anchorCode:", js)
        self.assertIn('色別登録：', js)
        self.assertIn('行き先未定', js)
        # 色別選択中は統合用 nameMappings をそのまま出さない
        self.assertIn("isColorSplit?colorSplitNameMappings(g):(namePlan.nameMappings||[])", js)


if __name__ == '__main__':
    unittest.main()
