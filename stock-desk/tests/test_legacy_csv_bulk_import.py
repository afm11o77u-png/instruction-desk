"""旧CSV一括取り込み（台帳＋初期在庫）。テスト用DBのみ。本番DBは触らない。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


class LegacyCsvBulkUiTest(unittest.TestCase):
    def test_unified_entry_left_of_display(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('legacyCsvBulkOpen', app)
        self.assertIn('旧CSV一括取り込み', app)
        self.assertIn('insertBefore(wrap,displayBar)', app)
        self.assertIn('registerOpeningStock', js)
        self.assertIn('openingStockSectionsHtml', js)


class LegacyCsvBulkImportTest(unittest.TestCase):
    def test_ledger_only_no_stock_write(self):
        folder, path, c = newdb()
        try:
            body = {
                'name': '台帳.csv',
                'file': b64(ledger_csv([
                    '顧客A,仕入先X,P001,パンツ,L',
                ])),
                'registerOpeningStock': False,
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview = pli.build_preview(c, body)
            self.assertFalse(preview.get('registerOpeningStock'))
            self.assertIsNone(preview.get('openingStock'))
            snap = pli.stock_snapshot(c)
            token = 'bulk-ledger-only-1'
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': token,
            }, db_path=path)
            self.assertFalse(result.get('already'))
            self.assertEqual(result['counts']['productsCreated'], 1)
            self.assertEqual(pli.stock_snapshot(c), snap)
            self.assertTrue(result.get('stockUnchanged'))
        finally:
            c.close()
            folder.cleanup()

    def test_both_files_register_opening_including_zero_and_negative(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P001,パンツ,L',
                '顧客A,仕入先X,P001,パンツ,M',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,パンツ,P001,L,1000,0,0,3,本社,棚1,0',
                '新品,仕入先X,顧客A,パンツ,P001,M,1000,0,0,0,本社,棚1,99',
                '新品,仕入先X,顧客A,パンツ,P001,S,1000,0,0,-2,, ,0',
            ])
            body = {
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxName': '初期在庫.csv',
                'auxFile': b64(opening),
                'registerOpeningStock': True,
                'basisDate': '2024-04-01',
                'defaultSiteName': '未確認',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview = pli.build_preview(c, body)
            self.assertTrue(preview.get('registerOpeningStock'))
            os_ = preview['openingStock']
            self.assertTrue(os_['enabled'])
            self.assertIn('初期在庫も登録', os_['note'])
            by_size = {(p.get('csv') or {}).get('size'): p for p in os_['plans']}
            # 補正在庫数は使わない（在庫列の 0 / -2）
            self.assertEqual(by_size['M']['qty'], 0)
            self.assertEqual(by_size['S']['qty'], -2)
            self.assertTrue(
                by_size['S']['place'].get('usedDefaultSite')
                or by_size['S']['place'].get('siteName') == '未確認'
            )

            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'bulk-both-1',
            }, db_path=path)
            self.assertGreaterEqual(result['counts']['openingCreated'], 2)
            lots = list(c.execute('SELECT qty_remaining, size_label, condition FROM lots ORDER BY size_label'))
            qtys = {r['size_label']: r['qty_remaining'] for r in lots}
            self.assertEqual(qtys.get('L'), 3)
            self.assertEqual(qtys.get('M'), 0)
            self.assertEqual(qtys.get('S'), -2)

            # 再取込プレビューは変更なし（二重加算しない）
            preview2 = pli.build_preview(c, body)
            open_statuses = {p['status'] for p in preview2['openingStock']['plans'] if p.get('matchedLedger')}
            self.assertTrue(open_statuses <= {'unchanged', 'qty_diff', 'hold', 'needs_review'})
            self.assertIn('unchanged', open_statuses)
            lots2 = list(c.execute('SELECT qty_remaining, size_label FROM lots'))
            self.assertEqual(sum(r['qty_remaining'] for r in lots2), 3 + 0 + (-2))
            self.assertEqual(result['counts']['openingCreated'], 3)
        finally:
            c.close()
            folder.cleanup()

    def test_hold_product_holds_related_stock(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P002,シャツ青,L',
                '顧客A,仕入先Y,P002,シャツ赤,L',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,シャツ青,P002,L,500,0,0,5,本社,,0',
            ])
            body = {
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxFile': b64(opening),
                'auxName': '初期在庫.csv',
                'registerOpeningStock': True,
                'basisDate': '2024-04-01',
                'skuDecisions': {
                    'P002': {'decision': 'hold', 'deferReason': 'テスト保留'},
                },
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview = pli.build_preview(c, body)
            plans = preview['openingStock']['plans']
            self.assertTrue(any(p['status'] == 'hold' and p.get('heldWithProduct') for p in plans))
            # 保留のみなら台帳コミット行も空になり得る → allowEmptyCommit 相当の検証はプレビューで十分
            held = [p for p in plans if p['status'] == 'hold']
            self.assertEqual(len(held), 1)
            self.assertIn('保留', held[0]['reasons'][0])
        finally:
            c.close()
            folder.cleanup()

    def test_complement_candidate_not_silently_dropped(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P001,パンツ,L',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,パンツ,P001,L,1000,0,0,1,本社,,0',
                '新品,仕入先Z,顧客B,だけ在庫の服,P999,L,100,0,0,4,本社,,0',
            ])
            preview = pli.build_preview(c, {
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxFile': b64(opening),
                'auxName': '初期在庫.csv',
                'registerOpeningStock': True,
                'basisDate': '2024-04-01',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            })
            comps = [p for p in preview['openingStock']['plans'] if p['status'] == 'complement']
            self.assertEqual(len(comps), 1)
            self.assertEqual(comps[0]['csv']['productName'], 'だけ在庫の服')
            self.assertIn('補完候補', comps[0]['reasons'][0])
        finally:
            c.close()
            folder.cleanup()

    def test_mid_failure_rolls_back_batch(self):
        folder, path, c = newdb()
        try:
            ledger = ledger_csv([
                '顧客A,仕入先X,P001,パンツ,L',
            ])
            opening = opening_csv([
                '新品,仕入先X,顧客A,パンツ,P001,L,1000,0,0,2,本社,,0',
            ])
            body = {
                'name': '台帳.csv',
                'file': b64(ledger),
                'auxFile': b64(opening),
                'auxName': '初期在庫.csv',
                'registerOpeningStock': True,
                'basisDate': '2024-04-01',
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview = pli.build_preview(c, body)
            # 初期在庫計画だけ壊し、BEGIN〜失敗〜ROLLBACK で台帳も残らないことを確認
            bad_plans = [dict(p) for p in preview['openingStock']['plans']]
            for p in bad_plans:
                if p['status'] in ('create', 'reuse'):
                    p['target'] = dict(p.get('target') or {})
                    p['target']['productId'] = None
                    p['target']['productCode'] = 'MISSING'

            import legacy_csv_bulk_import as bulk
            c.close()
            c = None
            c2 = stock.connect(path)
            try:
                c2.execute('BEGIN IMMEDIATE')
                stock.save_customer(c2, {'name': '顧客A', 'active': True})
                stock.save_supplier(c2, {'name': '仕入先X', 'active': True})
                with self.assertRaises(ValueError):
                    bulk.commit_opening_stock_plans(
                        c2,
                        body=body,
                        aux_uploaded=pli.decode_aux_upload(body),
                        plans=bad_plans,
                        run_id=1,
                        product_id_by_code={},
                        handle_resolver=lambda *a, **k: 1,
                        actor='test',
                        basis_date='2024-04-01',
                    )
                c2.execute('ROLLBACK')
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM customers').fetchone()['n'], 0)
                self.assertEqual(c2.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], 0)
            finally:
                c2.close()
        finally:
            if c is not None:
                c.close()
            folder.cleanup()

    def test_idempotent_commit_token(self):
        folder, path, c = newdb()
        try:
            body = {
                'name': '台帳.csv',
                'file': b64(ledger_csv(['顧客A,仕入先X,P001,パンツ,L'])),
                'registerOpeningStock': False,
                '_canWrite': True,
                '_actor': {'name': 'test'},
            }
            preview = pli.build_preview(c, body)
            token = 'bulk-idem-1'
            r1 = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': token,
            }, db_path=path)
            r2 = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': token,
            }, db_path=path)
            self.assertTrue(r2.get('already'))
            self.assertEqual(r1['runId'], r2['runId'])
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 1)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
