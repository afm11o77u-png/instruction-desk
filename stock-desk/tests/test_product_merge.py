"""重複商品の比較・統合（サンプル／一時DBのみ。実データ統合なし）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sample_data  # noqa: E402
import server  # noqa: E402
import stock  # noqa: E402
import stock_product_merge  # noqa: E402


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


class ProductMergeTests(unittest.TestCase):
    def test_ui_has_merge_entry(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        merge_js = (ROOT / 'static' / 'product_merge.js').read_text(encoding='utf-8')
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('productMergeOpen', app)
        self.assertIn('重複商品の整理', app)
        self.assertIn('subSupplierIds', app)
        self.assertIn('product_merge.js', html)
        self.assertIn('ProductMerge', merge_js)
        self.assertIn('/api/products/merge', merge_js)
        self.assertIn('別注', merge_js)
        srv = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn('/api/products/duplicates', srv)
        self.assertIn('/api/products/merge', server.WRITE_PATHS)

    def test_sub_suppliers_roundtrip(self):
        folder, path, c = newdb()
        try:
            a = stock.save_supplier(c, {'name': '通常繊維', 'active': True})
            b = stock.save_supplier(c, {'name': 'サブ被服', 'active': True})
            p = stock.save_product(c, {
                'code': 'T-1', 'name': '試験商品', 'color': '白',
                'supplierId': a['id'], 'subSupplierIds': [b['id']],
                'sizes': ['M', 'L'], 'links': [], 'active': True,
            })
            c.commit()
            row = stock.product_row(c, p['id'])
            self.assertEqual(row['supplierId'], a['id'])
            self.assertEqual(row['subSupplierIds'], [b['id']])
            self.assertTrue(stock.product_has_supplier(c, p['id'], a['id']))
            self.assertTrue(stock.product_has_supplier(c, p['id'], b['id']))
            # 更新時に subSupplierIds 省略 → サブ維持
            stock.save_product(c, {
                'id': p['id'], 'code': 'T-1', 'name': '試験商品改', 'color': '白',
                'supplierId': a['id'], 'sizes': ['M', 'L'], 'links': [], 'active': True,
            })
            c.commit()
            row2 = stock.product_row(c, p['id'])
            self.assertEqual(row2['name'], '試験商品改')
            self.assertEqual(row2['subSupplierIds'], [b['id']])
        finally:
            c.close()
            folder.cleanup()

    def test_duplicate_groups_separate_special_codes(self):
        folder, path, c = newdb()
        try:
            sample_data.seed(c)
            c.commit()
            # 別注を2件（同一品番だけでは同一視しないセクションへ）
            stock.save_product(c, {
                'code': '別注', 'name': '病院A 特注上着', 'color': '', 'category': '制服',
                'supplierId': None, 'sizes': ['M'], 'links': [], 'active': True,
            })
            stock.save_product(c, {
                'code': '別注', 'name': '病院B 特注ズボン', 'color': '', 'category': '制服',
                'supplierId': None, 'sizes': ['L'], 'links': [], 'active': True,
            })
            stock.save_product(c, {
                'code': '', 'name': '品番なし部品', 'color': '', 'category': '部品・資材',
                'supplierId': None, 'sizes': ['フリー'], 'links': [], 'active': True,
            })
            c.commit()
            data = stock_product_merge.list_duplicate_groups(c)
            self.assertGreaterEqual(data['normalGroupCount'], 1)
            normal_codes = {g['code'] for g in data['normalGroups']}
            self.assertIn('SCR-100', normal_codes)
            self.assertNotIn('別注', normal_codes)
            kinds = {s['kind'] for s in data['specialSections']}
            self.assertIn('customOrder', kinds)
            self.assertIn('blank', kinds)
            custom = next(s for s in data['specialSections'] if s['kind'] == 'customOrder')
            self.assertFalse(custom['autoSame'])
        finally:
            c.close()
            folder.cleanup()

    def test_merge_keeps_qty_amount_and_lot_breakdown(self):
        folder, path, c = newdb()
        try:
            sample_data.seed(c)
            c.commit()
            products = list(c.execute(
                "SELECT id, supplier_id FROM products WHERE code='SCR-100' ORDER BY id"
            ))
            self.assertEqual(len(products), 2)
            keep_id = products[0]['id']
            source_id = products[1]['id']
            before = stock_product_merge.product_stock_totals(c, [keep_id, source_id])
            keep_lots_before = list(c.execute(
                '''SELECT l.id, l.qty_remaining, l.unit_cost, l.size_label, l.condition,
                          l.location_id, l.cost_pending, l.received_unknown, l.payout_order
                   FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
                   WHERE cp.product_id IN (?,?) ORDER BY l.id''',
                (keep_id, source_id),
            ))
            lot_snapshot = {
                r['id']: (
                    r['qty_remaining'], r['unit_cost'], r['size_label'], r['condition'],
                    r['location_id'], r['cost_pending'], r['received_unknown'], r['payout_order'],
                )
                for r in keep_lots_before
            }

            preview = stock_product_merge.preview_merge(c, {
                'productIds': [keep_id, source_id],
                'keepProductId': keep_id,
            })
            self.assertEqual(preview['totals']['qty'], before['qty'])
            self.assertEqual(preview['totals']['amount'], before['amount'])
            # LL と 2L が別サイズのまま候補に残る
            self.assertIn('LL', preview['sizeUnion'])
            self.assertIn('2L', preview['sizeUnion'])

            result = stock_product_merge.execute_merge(c, {
                'token': 'merge-test-1',
                'fingerprint': preview['fingerprint'],
                'productIds': [keep_id, source_id],
                'keepProductId': keep_id,
                'name': preview['name'],
                'color': preview['color'],
                'category': preview['category'],
                'supplierId': preview['supplierId'],
                'subSupplierIds': preview['subSupplierIds'],
                'sizes': preview['sizes'],
                '_actor': {'name': '試験ユーザー'},
            }, db_path=path)
            c.commit()

            self.assertFalse(result['already'])
            self.assertEqual(result['beforeQty'], result['afterQty'])
            self.assertEqual(result['beforeAmount'], result['afterAmount'])
            self.assertEqual(result['beforeQty'], before['qty'])
            self.assertEqual(result['beforeAmount'], before['amount'])

            after = stock_product_merge.product_stock_totals(c, [keep_id])
            self.assertEqual(after['qty'], before['qty'])
            self.assertEqual(after['amount'], before['amount'])
            self.assertEqual(after['lotCount'], before['lotCount'])

            # ロット内訳（数量・単価・サイズ等）が同じIDのまま残る
            for lid, snap in lot_snapshot.items():
                row = c.execute(
                    'SELECT qty_remaining, unit_cost, size_label, condition, location_id, '
                    'cost_pending, received_unknown, payout_order FROM lots WHERE id=?',
                    (lid,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(
                    (
                        row['qty_remaining'], row['unit_cost'], row['size_label'], row['condition'],
                        row['location_id'], row['cost_pending'], row['received_unknown'],
                        row['payout_order'],
                    ),
                    snap,
                )
                # すべて統合先の取扱へ
                cp = c.execute(
                    '''SELECT cp.product_id FROM lots l
                       JOIN customer_products cp ON cp.id=l.customer_product_id
                       WHERE l.id=?''',
                    (lid,),
                ).fetchone()
                self.assertEqual(cp['product_id'], keep_id)

            src = stock.product_row(c, source_id)
            self.assertEqual(src['mergedIntoId'], keep_id)
            self.assertFalse(src['active'])
            self.assertEqual(
                stock_product_merge.resolve_live_product_id(c, source_id), keep_id,
            )

            # サブ仕入先に統合元の通常仕入先が残る
            keep = stock.product_row(c, keep_id)
            self.assertIn(products[1]['supplier_id'], keep['subSupplierIds'] + (
                [keep['supplierId']] if keep['supplierId'] else []
            ))

            # 二重実行は already
            again = stock_product_merge.execute_merge(c, {
                'token': 'merge-test-1',
                'fingerprint': preview['fingerprint'],
                'productIds': [keep_id, source_id],
                'keepProductId': keep_id,
            }, db_path=path)
            self.assertTrue(again['already'])

            # 統合元は重複グループから消える
            data = stock_product_merge.list_duplicate_groups(c)
            for g in data['normalGroups']:
                if g['code'] == 'SCR-100':
                    self.fail('統合後も SCR-100 の重複グループが残っている')
        finally:
            c.close()
            folder.cleanup()

    def test_merge_does_not_equate_pending_and_empty_embroidery(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '差異病院', 'active': True})
            su = stock.save_supplier(c, {'name': '差異仕入', 'active': True})
            a = stock.save_product(c, {
                'code': 'DIFF-1', 'name': '加工差', 'color': '白', 'supplierId': su['id'],
                'sizes': ['M'], 'active': True,
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            b = stock.save_product(c, {
                'code': 'DIFF-1', 'name': '加工差', 'color': '白', 'supplierId': su['id'],
                'sizes': ['M'], 'active': True,
                'links': [{'customerId': cu['id'], 'embroidery': '加工未整理', 'isStandard': True, 'active': True}],
                '_allowHistoricalDuplicateCode': True,
            })
            c.commit()
            preview = stock_product_merge.preview_merge(c, {
                'productIds': [a['id'], b['id']], 'keepProductId': a['id'],
            })
            # 別取扱として付け替え（まとめない）
            moves = preview['handleMoves']
            self.assertEqual(len(moves), 1)
            self.assertEqual(moves[0]['action'], 'reattach')
            self.assertEqual(moves[0]['embroidery'], '加工未整理')
            result = stock_product_merge.execute_merge(c, {
                'token': 'merge-emb-1',
                'fingerprint': preview['fingerprint'],
                'productIds': [a['id'], b['id']],
                'keepProductId': a['id'],
                'sizes': ['M'],
            }, db_path=path)
            c.commit()
            links = stock.product_row(c, a['id'])['links']
            embs = sorted(stock.normalize_embroidery(l['embroidery']) for l in links)
            self.assertEqual(embs, ['', '加工未整理'])
            self.assertEqual(result['beforeQty'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_merge_conflict_requires_resolution(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '標準差病院', 'active': True})
            su = stock.save_supplier(c, {'name': '標準差仕入', 'active': True})
            a = stock.save_product(c, {
                'code': 'STD-1', 'name': '標準差', 'color': '', 'supplierId': su['id'],
                'sizes': ['M'], 'active': True,
                'links': [{'customerId': cu['id'], 'embroidery': '左胸', 'isStandard': True, 'active': True}],
            })
            b = stock.save_product(c, {
                'code': 'STD-1', 'name': '標準差', 'color': '', 'supplierId': su['id'],
                'sizes': ['M'], 'active': True,
                'links': [{'customerId': cu['id'], 'embroidery': '左胸', 'isStandard': False, 'active': True}],
                '_allowHistoricalDuplicateCode': True,
            })
            c.commit()
            preview = stock_product_merge.preview_merge(c, {
                'productIds': [a['id'], b['id']], 'keepProductId': a['id'],
            })
            self.assertGreaterEqual(preview['unresolvedConflicts'], 1)
            with self.assertRaises(ValueError):
                stock_product_merge.execute_merge(c, {
                    'token': 'merge-std-1',
                    'fingerprint': preview['fingerprint'],
                    'productIds': [a['id'], b['id']],
                    'keepProductId': a['id'],
                    'sizes': ['M'],
                }, db_path=path)
            c.rollback()
            result = stock_product_merge.execute_merge(c, {
                'token': 'merge-std-2',
                'fingerprint': preview['fingerprint'],
                'productIds': [a['id'], b['id']],
                'keepProductId': a['id'],
                'sizes': ['M'],
                'conflictResolutions': {
                    str(preview['handleConflicts'][0]['sourceHandleId']): 'keep',
                },
            }, db_path=path)
            c.commit()
            self.assertEqual(result['keepProductId'], a['id'])
        finally:
            c.close()
            folder.cleanup()

    def test_stale_fingerprint_conflict(self):
        folder, path, c = newdb()
        try:
            sample_data.seed(c)
            c.commit()
            products = [r['id'] for r in c.execute(
                "SELECT id FROM products WHERE code='SCR-100' ORDER BY id"
            )]
            preview = stock_product_merge.preview_merge(c, {
                'productIds': products, 'keepProductId': products[0],
            })
            with self.assertRaises(stock.StockConflict):
                stock_product_merge.execute_merge(c, {
                    'token': 'stale-1',
                    'fingerprint': 'deadbeef',
                    'productIds': products,
                    'keepProductId': products[0],
                    'sizes': preview['sizes'],
                }, db_path=path)
        finally:
            c.close()
            folder.cleanup()

    def test_api_merge_preview_and_duplicates(self):
        folder, path, c = newdb()
        try:
            sample_data.seed(c)
            c.commit()
            code, body = server.api_get(str(path), 'sample', '/api/products/duplicates', {})
            self.assertEqual(code, 200)
            self.assertIn('normalGroups', body)
            products = [r['id'] for r in c.execute(
                "SELECT id FROM products WHERE code='SCR-100' ORDER BY id"
            )]
            code2, body2 = server.api_post(
                str(path), 'sample', '/api/products/merge/preview',
                {'productIds': products, 'keepProductId': products[0]},
            )
            self.assertEqual(code2, 200)
            self.assertEqual(body2['keepProductId'], products[0])
        finally:
            c.close()
            folder.cleanup()

    def test_legacy_map_follows_merged_product(self):
        folder, path, c = newdb()
        try:
            sample_data.seed(c)
            c.commit()
            products = [r['id'] for r in c.execute(
                "SELECT id FROM products WHERE code='SCR-100' ORDER BY id"
            )]
            keep_id, source_id = products[0], products[1]
            # 対応表に統合元を記録
            import legacy_csv_import
            legacy_csv_import.upsert_row_map(
                c,
                run_id=None,
                file_sha256='abc',
                file_name='t.csv',
                row={
                    'rowNo': 1,
                    'csv': {
                        'customer': 'あおば記念病院', 'supplier': '丸善被服',
                        'code': 'SCR-100', 'productName': '白スクラブ',
                        'size': 'M', 'site': '', 'shelf': '',
                    },
                    'product': {'size': 'M'},
                    'condition': 'new',
                    'unitCost': 900,
                },
                customer_id=1,
                product_id=source_id,
                customer_product_id=None,
            )
            c.commit()
            preview = stock_product_merge.preview_merge(c, {
                'productIds': products, 'keepProductId': keep_id,
            })
            stock_product_merge.execute_merge(c, {
                'token': 'map-merge-1',
                'fingerprint': preview['fingerprint'],
                'productIds': products,
                'keepProductId': keep_id,
                'sizes': preview['sizes'],
                'supplierId': preview['supplierId'],
                'subSupplierIds': preview['subSupplierIds'],
            }, db_path=path)
            c.commit()
            mapped = legacy_csv_import.lookup_product_map(
                c, customer='あおば記念病院', code='SCR-100', product_name='白スクラブ',
            )
            self.assertEqual(mapped['product_id'], keep_id)
            live = stock_product_merge.resolve_live_product_id(c, source_id)
            self.assertEqual(live, keep_id)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
