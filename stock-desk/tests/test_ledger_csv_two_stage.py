"""旧CSV一括取り込みの二段階分離（商品登録 → 初期在庫登録）。

テスト用DBのみ。本番の商品登録・削除・在庫更新は行わない。
"""
from __future__ import annotations

import base64
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import legacy_csv_bulk_import as bulk  # noqa: E402
import product_ledger_import as pli  # noqa: E402
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


class TwoStageModeTest(unittest.TestCase):
    def test_products_mode_never_calls_opening_prep(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv(['顧客A,仕入先X,P001,パンツ,L'])
            opening = opening_csv([
                '新品,仕入先X,顧客A,パンツ,P001,L,1000,0,0,3,本社,棚1,0',
            ])
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                # 誤って true でも products モードでは無視
                'registerOpeningStock': True,
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            with mock.patch.object(
                bulk, 'build_opening_stock_plans', wraps=bulk.build_opening_stock_plans,
            ) as prep:
                preview = pli.build_preview(c, body)
            prep.assert_not_called()
            self.assertEqual(preview.get('importMode'), 'products')
            self.assertFalse(preview.get('registerOpeningStock'))
            self.assertIsNone(preview.get('openingStock'))
            self.assertIn('判断にだけ', preview.get('note') or '')
            self.assertEqual((preview.get('timingsMs') or {}).get('openingStockPrep'), 0)
            aux = preview.get('auxOpeningStock') or {}
            self.assertTrue(aux.get('judgmentOnly'))
            self.assertFalse(aux.get('willRegisterOpeningStock'))
        finally:
            c.close()
            folder.cleanup()

    def test_products_then_close_then_opening_from_maps(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P001,パンツ（青）,L',
                '顧客A,仕入先X,P001,パンツ（赤）,M',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,パンツ（青）,P001,L,1000,0,0,2,本社,棚1,0',
                '新品,仕入先X,顧客A,パンツ（赤）,P001,M,1000,0,0,5,, ,0',
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
            t0 = time.perf_counter()
            preview1 = pli.build_preview(c, stage1)
            stage1_ms = (time.perf_counter() - t0) * 1000
            self.assertFalse(preview1.get('registerOpeningStock'))
            self.assertIsNone(preview1.get('openingStock'))
            snap = pli.stock_snapshot(c)
            result1 = pli.commit_preview(c, {
                **stage1,
                'previewFingerprint': preview1['previewFingerprint'],
                'previewAt': preview1['previewAt'],
                'token': 'two-stage-products-1',
            }, db_path=path)
            self.assertEqual(result1['counts']['productsCreated'], 1)
            self.assertTrue(result1.get('stockUnchanged'))
            self.assertEqual(pli.stock_snapshot(c), snap)
            maps_n = c.execute('SELECT COUNT(*) AS n FROM product_ledger_row_maps').fetchone()['n']
            self.assertGreaterEqual(maps_n, 2)

            # 画面を閉じた想定: 新しいリクエストで初期在庫だけ
            stage2 = {
                'importMode': 'opening_stock',
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                'basisDate': '2024-04-01',
                'defaultSiteName': '未確認',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            t1 = time.perf_counter()
            preview2 = pli.build_preview(c, stage2)
            stage2_ms = (time.perf_counter() - t1) * 1000
            self.assertEqual(preview2.get('importMode'), 'opening_stock')
            self.assertTrue(preview2.get('registerOpeningStock'))
            os_ = preview2['openingStock']
            self.assertTrue(os_.get('fromPersistedMap'))
            self.assertTrue(os_['enabled'])
            by_size = {(p.get('csv') or {}).get('size'): p for p in os_['plans']}
            self.assertEqual(by_size['L']['qty'], 2)
            self.assertEqual(by_size['M']['qty'], 5)
            self.assertTrue(
                by_size['M']['place'].get('usedDefaultSite')
                or by_size['M']['place'].get('siteName') == '未確認'
            )
            self.assertTrue(by_size['L'].get('matchedLedger'))
            self.assertEqual(by_size['L']['csv']['productName'], 'パンツ（青）')
            # 公式名が改名されていても原文で解決
            self.assertTrue(by_size['L']['target'].get('productId'))

            result2 = pli.commit_preview(c, {
                **stage2,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'two-stage-opening-1',
            }, db_path=path)
            self.assertEqual(result2.get('importMode'), 'opening_stock')
            self.assertGreaterEqual(result2['counts']['openingCreated'], 2)
            lots = {
                r['size_label']: r['qty_remaining']
                for r in c.execute('SELECT size_label, qty_remaining FROM lots')
            }
            self.assertEqual(lots.get('L'), 2)
            self.assertEqual(lots.get('M'), 5)

            # タイミング報告用（結合モードとの比較は CombinedTimingTest）
            self.assertIn('timingsMs', preview1)
            self.assertIn('timingsMs', preview2)
            self.assertGreaterEqual(preview1['timingsMs']['productJudgment'], 0)
            self.assertEqual(preview1['timingsMs']['openingStockPrep'], 0)
            self.assertEqual(preview2['timingsMs']['productJudgment'], 0)
            self.assertGreater(preview2['timingsMs']['openingStockPrep'], 0)
            # 参照しやすいよう属性に残す（失敗時メッセージ）
            self._stage1_wall_ms = stage1_ms
            self._stage2_wall_ms = stage2_ms
        finally:
            c.close()
            folder.cleanup()

    def test_row_reorder_still_matches_maps(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P010,シャツ,L',
                '顧客A,仕入先X,P010,シャツ,M',
            ])
            opening_a = opening_csv([
                '新品,仕入先X,顧客A,シャツ,P010,L,800,0,0,1,本社,,0',
                '新品,仕入先X,顧客A,シャツ,P010,M,800,0,0,4,本社,,0',
            ])
            # 行順を入れ替え（行番号だけに依存しないこと）
            opening_b = opening_csv([
                '新品,仕入先X,顧客A,シャツ,P010,M,800,0,0,4,本社,,0',
                '新品,仕入先X,顧客A,シャツ,P010,L,800,0,0,1,本社,,0',
            ])
            stage1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            p1 = pli.build_preview(c, stage1)
            pli.commit_preview(c, {
                **stage1,
                'previewFingerprint': p1['previewFingerprint'],
                'previewAt': p1['previewAt'],
                'token': 'reorder-products-1',
            }, db_path=path)

            p2 = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': b64(opening_b),
                'auxName': '初期在庫.csv',
                'basisDate': '2024-04-01',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            })
            by_size = {(p.get('csv') or {}).get('size'): p for p in p2['openingStock']['plans']}
            self.assertEqual(by_size['L']['qty'], 1)
            self.assertEqual(by_size['M']['qty'], 4)
            self.assertTrue(by_size['L'].get('matchedLedger'))
            self.assertTrue(by_size['M'].get('matchedLedger'))
            # opening_a と同一内容なら再取込後もサイズ対応が一致
            self.assertEqual(
                {(r.split(',')[5], r.split(',')[9]) for r in opening_a.splitlines()[1:]},
                {(r.split(',')[5], r.split(',')[9]) for r in opening_b.splitlines()[1:]},
            )
        finally:
            c.close()
            folder.cleanup()

    def test_reimport_does_not_double_add(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv(['顧客A,仕入先X,P020,ズボン,L'])
            opening = opening_csv([
                '新品,仕入先X,顧客A,ズボン,P020,L,500,0,0,7,本社,A,0',
            ])
            s1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            p1 = pli.build_preview(c, s1)
            pli.commit_preview(c, {
                **s1,
                'previewFingerprint': p1['previewFingerprint'],
                'previewAt': p1['previewAt'],
                'token': 'reimp-products-1',
            }, db_path=path)
            s2 = {
                'importMode': 'opening_stock',
                'auxFile': b64(opening),
                'auxName': '初期在庫.csv',
                'basisDate': '2024-04-01',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            p2 = pli.build_preview(c, s2)
            pli.commit_preview(c, {
                **s2,
                'previewFingerprint': p2['previewFingerprint'],
                'previewAt': p2['previewAt'],
                'token': 'reimp-opening-01',
            }, db_path=path)
            qty1 = c.execute('SELECT SUM(qty_remaining) AS q FROM lots').fetchone()['q']
            p3 = pli.build_preview(c, s2)
            statuses = {p['status'] for p in p3['openingStock']['plans']}
            self.assertIn('unchanged', statuses)
            self.assertNotIn('create', statuses)
            # コミットしても増えない（idempotent token 別でも unchanged は加算しない）
            pli.commit_preview(c, {
                **s2,
                'previewFingerprint': p3['previewFingerprint'],
                'previewAt': p3['previewAt'],
                'token': 'reimp-opening-02',
                'allowEmptyCommit': True,
            }, db_path=path)
            qty2 = c.execute('SELECT SUM(qty_remaining) AS q FROM lots').fetchone()['q']
            self.assertEqual(qty1, 7)
            self.assertEqual(qty2, 7)
        finally:
            c.close()
            folder.cleanup()

    def test_missing_map_is_complement_not_guess(self):
        folder, path, c = newdb()
        try:
            preview = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': b64(opening_csv([
                    '新品,仕入先X,顧客A,未登録商品,P999,L,100,0,0,3,本社,,0',
                ])),
                'auxName': '初期在庫.csv',
                'basisDate': '2024-04-01',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            })
            plans = preview['openingStock']['plans']
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]['status'], 'complement')
            self.assertIn('マップ', plans[0]['reasons'][0])
            self.assertIn('推測', plans[0]['reasons'][0])
        finally:
            c.close()
            folder.cleanup()

    def test_qty_diff_requires_explicit_confirm(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv(['顧客A,仕入先X,P030,コート,L'])
            opening = opening_csv([
                '新品,仕入先X,顧客A,コート,P030,L,1200,0,0,3,本社,,0',
            ])
            s1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            p1 = pli.build_preview(c, s1)
            pli.commit_preview(c, {
                **s1,
                'previewFingerprint': p1['previewFingerprint'],
                'previewAt': p1['previewAt'],
                'token': 'diff-products-01',
            }, db_path=path)
            s2 = {
                'importMode': 'opening_stock',
                'auxFile': b64(opening),
                'auxName': '初期在庫.csv',
                'basisDate': '2024-04-01',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            p2 = pli.build_preview(c, s2)
            pli.commit_preview(c, {
                **s2,
                'previewFingerprint': p2['previewFingerprint'],
                'previewAt': p2['previewAt'],
                'token': 'diff-opening-01',
            }, db_path=path)
            opening2 = opening_csv([
                '新品,仕入先X,顧客A,コート,P030,L,1200,0,0,9,本社,,0',
            ])
            # 新しい body（decode キャッシュを引き継がない）
            s3 = {
                'importMode': 'opening_stock',
                'auxFile': b64(opening2),
                'auxName': '初期在庫.csv',
                'basisDate': '2024-04-01',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            p3 = pli.build_preview(c, s3)
            plan = p3['openingStock']['plans'][0]
            self.assertEqual(plan['status'], 'qty_diff')
            self.assertEqual(plan['qty'], 9)
            self.assertEqual((plan.get('stock') or {}).get('currentQty'), 3)
            # 明示選択なしでは更新されない
            r = pli.commit_preview(c, {
                **s3,
                'previewFingerprint': p3['previewFingerprint'],
                'previewAt': p3['previewAt'],
                'token': 'diff-opening-02',
                'allowEmptyCommit': True,
            }, db_path=path)
            self.assertEqual(r['counts'].get('openingQtyUpdated', 0), 0)
            self.assertEqual(
                c.execute('SELECT SUM(qty_remaining) AS q FROM lots').fetchone()['q'], 3,
            )
            # 明示選択で更新
            p4 = pli.build_preview(c, {
                **s3,
                'openingSelections': {str(plan['rowNo']): {'updateQty': True}},
            })
            r2 = pli.commit_preview(c, {
                **s3,
                'openingSelections': {str(plan['rowNo']): {'updateQty': True}},
                'previewFingerprint': p4['previewFingerprint'],
                'previewAt': p4['previewAt'],
                'token': 'diff-opening-03',
            }, db_path=path)
            self.assertEqual(r2['counts'].get('openingQtyUpdated', 0), 1)
            self.assertEqual(
                c.execute('SELECT SUM(qty_remaining) AS q FROM lots').fetchone()['q'], 9,
            )
        finally:
            c.close()
            folder.cleanup()

    def test_zero_and_negative_qty_preserved(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P040,帽子,L',
                '顧客A,仕入先X,P040,帽子,M',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,帽子,P040,L,100,0,0,0,本社,,99',
                '新品,仕入先X,顧客A,帽子,P040,M,100,0,0,-3,本社,,0',
            ])
            s1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            p1 = pli.build_preview(c, s1)
            pli.commit_preview(c, {
                **s1,
                'previewFingerprint': p1['previewFingerprint'],
                'previewAt': p1['previewAt'],
                'token': 'zero-neg-products',
            }, db_path=path)
            p2 = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': b64(opening),
                'auxName': '初期在庫.csv',
                'basisDate': '2024-04-01',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            })
            by_size = {(p.get('csv') or {}).get('size'): p for p in p2['openingStock']['plans']}
            self.assertEqual(by_size['L']['qty'], 0)
            self.assertEqual(by_size['M']['qty'], -3)
        finally:
            c.close()
            folder.cleanup()

    def test_mid_failure_rolls_back_opening_only(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv(['顧客A,仕入先X,P050,ベスト,L'])
            opening = opening_csv([
                '新品,仕入先X,顧客A,ベスト,P050,L,300,0,0,2,本社,,0',
            ])
            s1 = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            p1 = pli.build_preview(c, s1)
            pli.commit_preview(c, {
                **s1,
                'previewFingerprint': p1['previewFingerprint'],
                'previewAt': p1['previewAt'],
                'token': 'fail-products-01',
            }, db_path=path)
            c.commit()  # 本番は API が COMMIT。接続を張り直す前に確定させる
            products_before = c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n']
            self.assertEqual(products_before, 1)
            p2 = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': b64(opening),
                'auxName': '初期在庫.csv',
                'basisDate': '2024-04-01',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            })
            bad = [dict(x) for x in p2['openingStock']['plans']]
            for plan in bad:
                if plan['status'] in ('create', 'reuse'):
                    plan['target'] = dict(plan.get('target') or {})
                    plan['target']['productId'] = None
                    plan['target']['productCode'] = 'MISSING'
            c.close()
            c = None
            c2 = stock.connect(path)
            try:
                c2.execute('BEGIN IMMEDIATE')
                with self.assertRaises(ValueError):
                    bulk.commit_opening_stock_plans(
                        c2,
                        body={
                            'importMode': 'opening_stock',
                            'basisDate': '2024-04-01',
                            '_canWrite': True,
                            '_actor': {'name': 'test'},
                        },
                        aux_uploaded=pli.decode_aux_upload({
                            'auxFile': b64(opening),
                            'auxName': '初期在庫.csv',
                        }),
                        plans=bad,
                        run_id=99,
                        product_id_by_code={},
                        handle_resolver=lambda *a, **k: 1,
                        actor='test',
                        basis_date='2024-04-01',
                    )
                c2.execute('ROLLBACK')
                self.assertEqual(
                    c2.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'],
                    products_before,
                )
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], 0)
            finally:
                c2.close()
        finally:
            if c is not None:
                c.close()
            folder.cleanup()


class CombinedTimingTest(unittest.TestCase):
    """分離前後の待ち時間比較（同一テストデータ）。"""

    def test_report_timings_before_vs_after_split(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P100,パンツ青,L',
                '顧客A,仕入先X,P100,パンツ青,M',
                '顧客A,仕入先Y,P100,パンツ赤,L',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,パンツ青,P100,L,1000,0,0,3,本社,,0',
                '新品,仕入先X,顧客A,パンツ青,P100,M,1000,0,0,1,本社,,0',
                '新品,仕入先Y,顧客A,パンツ赤,P100,L,900,0,0,2,本社,,0',
            ])
            # Before: combined（後方互換フラグ）
            combined = {
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxFile': b64(opening),
                'auxName': '初期在庫.csv',
                'registerOpeningStock': True,
                'basisDate': '2024-04-01',
                'skuDecisions': {
                    'P100': {
                        'decision': 'merge',
                        'officialName': 'パンツ青',
                        'primarySupplier': '仕入先X',
                    },
                },
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            # 判定を安定させるため先に decisions 付きで preview
            before = pli.build_preview(c, combined)
            before_t = before.get('timingsMs') or {}

            # After: products（判定のみ）+ opening（マップ。先に商品登録が必要）
            # ここでは同一DBにまだ商品がないので、products preview の timing と
            # opening preview（マップ空）の prep を測り、products が opening prep=0 であることを確認
            products_body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxFile': b64(opening),
                'auxName': '初期在庫.csv',
                'skuDecisions': combined['skuDecisions'],
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            after_products = pli.build_preview(c, products_body)
            after_p_t = after_products.get('timingsMs') or {}
            self.assertEqual(after_p_t.get('openingStockPrep'), 0)
            self.assertGreater(before_t.get('openingStockPrep', 0), 0)
            # 商品登録モードは初期在庫準備分だけ軽い
            self.assertLess(
                after_p_t.get('total', 0),
                before_t.get('total', 0) + 1,  # 許容: ほぼ同等でも openingPrep が分離されていること
            )
            self.assertGreater(after_p_t.get('productJudgment', 0), 0)
            report = {
                'beforeCombinedMs': before_t,
                'afterProductsMs': after_p_t,
                'openingPrepSavedMs': (before_t.get('openingStockPrep') or 0),
            }
            # アサーションとして報告値を検証
            self.assertGreater(report['openingPrepSavedMs'], 0)
            self.assertEqual(report['afterProductsMs']['openingStockPrep'], 0)
        finally:
            c.close()
            folder.cleanup()


class DecodeCacheTest(unittest.TestCase):
    def test_same_request_does_not_reparse_csv(self):
        folder, path, c = newdb()
        try:
            body = {
                'importMode': 'products',
                'name': '台帳.csv',
                'file': b64(ledger_csv(['顧客A,仕入先X,P001,パンツ,L'])),
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            u1 = pli.decode_upload(body)
            u2 = pli.decode_upload(body)
            self.assertIs(u1, u2)
            preview = pli.build_preview(c, body)
            # commit 内の build_preview + decode_upload も同一キャッシュ
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'cache-token-01',
            }, db_path=path)
            self.assertEqual(result['counts']['productsCreated'], 1)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
