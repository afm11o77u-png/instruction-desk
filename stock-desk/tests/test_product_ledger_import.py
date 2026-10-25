"""旧商品台帳CSV取込のプレビュー／確定（テスト用DBのみ。実データ取込なし）。"""
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

SAMPLE_CSV = Path(
    '/cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/media/商品台帳.csv'
)


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64_from_text(text: str, encoding='utf-8-sig') -> str:
    return base64.b64encode(text.encode(encoding)).decode('ascii')


def tiny_ledger_rows(rows):
    header = '顧客,仕入先,商品コード,商品名,サイズ'
    return '\n'.join([header] + rows)


def stock_snap(c):
    return pli.stock_snapshot(c)


class ProductLedgerUiSourceTest(unittest.TestCase):
    def test_products_page_button_and_script_wired(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        # 入口は登録・設定右上「旧CSV一括取り込み」（表示の左）
        self.assertIn('legacyCsvBulkOpen', app)
        self.assertIn('旧CSV一括取り込み', app)
        self.assertIn('syncLegacyCsvBulkEntry', app)
        self.assertNotIn('productLedgerCsvOpen', app)
        self.assertNotIn('旧商品台帳CSVを取り込む', app)
        self.assertNotIn("['legacyCsv','旧CSV取込']", app)
        self.assertIn('product_ledger_csv.js', html)
        self.assertIn('ProductLedgerCsv', js)
        self.assertIn('/api/product-ledger-csv/preview', js)
        self.assertIn('/api/product-ledger-csv/commit', js)
        self.assertIn('同じ商品としてまとめる', js)
        self.assertIn('別物のため個別に整理する', js)
        self.assertIn('今回は保留する', js)
        self.assertIn('skuDecisions', js)
        self.assertIn('自動決定済み', js)
        self.assertIn('自動決定済み（未登録）', js)
        self.assertIn('autoMergePlans', js)
        self.assertIn('processingServicePlans', js)
        self.assertIn('加工サービスの自動整理', js)
        self.assertIn('中古標記まとめ', js)
        self.assertIn('auxFile', js)
        self.assertIn('初期在庫CSV', js)
        self.assertIn('registerOpeningStock', js)
        self.assertIn('初期在庫CSVは商品選定の判断にだけ使います。在庫は登録しません', js)
        self.assertIn('plModeProducts', js)
        self.assertIn('plModeOpening', js)
        self.assertIn('openingStockSectionsHtml', js)
        self.assertIn('sku-compare-table', js)
        self.assertIn('一括承認候補', js)
        self.assertIn('個別確認', js)
        self.assertIn('個別確認が必要', js)
        self.assertIn('plSkuBulkApproveSection', js)
        self.assertIn('partitionJudgmentGroups', js)
        self.assertIn('名称未整理', js)
        self.assertIn('1品番=1商品', js)

    def test_file_picker_and_history_error_copy(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('台帳CSVを変更', js)
        self.assertIn('product-ledger-file-input', js)
        self.assertIn('履歴を確認できませんでした', js)
        self.assertIn('ファイル容量が上限を超えています', js)
        self.assertIn('MAX_POST_BODY_BYTES=12_000_000', js)
        # Must not claim "no prior import" when history fetch failed
        self.assertIn('historyError', js)
        self.assertIn('このファイルの過去取込記録はありません', js)

    def test_preview_error_visible_in_modal_and_toast_css(self):
        """Preview failures must not rely only on a bottom toast behind the modal."""
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('previewError', js)
        self.assertIn('product-ledger-import-error', js)
        self.assertIn('role="alert"', js)
        self.assertIn("st.previewError=msg", js)
        self.assertIn('照合に失敗しました。', js)
        self.assertIn('scrollIntoView', js)
        # Toast: wrap long copy and stay above app chrome (modal still needs inline alert)
        self.assertIn('max-width:min(36rem,calc(100vw - 2rem))', css)
        self.assertIn('overflow-wrap:anywhere', css)
        self.assertIn('z-index:10000', css)
        self.assertIn('.product-ledger-import-error', css)


class UploadLimitsSyncTest(unittest.TestCase):
    def test_product_ledger_paths_share_large_body_limit(self):
        import upload_limits as ul
        import server as stock_server

        for path in (
            '/api/product-ledger-csv/history',
            '/api/product-ledger-csv/preview',
            '/api/product-ledger-csv/commit',
        ):
            self.assertIn(path, ul.LARGE_BODY_PATHS)
            self.assertEqual(ul.body_limit_for(path), 12_000_000)
            self.assertEqual(stock_server.body_limit_for(path), 12_000_000)
        self.assertEqual(ul.OVERSIZE_BODY_ERROR, 'ファイル容量が上限を超えています')
        self.assertEqual(stock_server.OVERSIZE_BODY_ERROR, ul.OVERSIZE_BODY_ERROR)
        self.assertIs(stock_server.LARGE_BODY_PATHS, ul.LARGE_BODY_PATHS)

    def test_root_inventory_proxy_uses_shared_limits(self):
        root_server = (ROOT.parent / 'server.py').read_text(encoding='utf-8')
        self.assertIn('body_limit_for(api_path)', root_server)
        self.assertIn('OVERSIZE_BODY_ERROR', root_server)
        # Old hardcoded allowlist without product-ledger must not remain
        self.assertNotIn(
            "large_body = api_path in (\n                '/api/import',\n                '/api/legacy-csv/preview',",
            root_server,
        )


class ProductLedgerParseTest(unittest.TestCase):
    def test_real_file_headers_utf8_bom(self):
        if not SAMPLE_CSV.is_file():
            self.skipTest('商品台帳.csv not mounted')
        blob = SAMPLE_CSV.read_bytes()
        self.assertTrue(blob.startswith(b'\xef\xbb\xbf'))
        text, enc = pli.decode_csv_bytes(blob)
        self.assertEqual(enc, 'utf-8-sig')
        fields, rows = pli.parse_csv_text(text)
        self.assertEqual(fields[:5], ['顧客', '仕入先', '商品コード', '商品名', 'サイズ'])
        self.assertGreater(len(rows), 1000)
        # 品番・サイズは文字列のまま
        for _, item in rows[:30]:
            self.assertIsInstance(item['商品コード'], str)
            self.assertIsInstance(item['サイズ'], str)

    def test_missing_columns_rejected(self):
        text = '顧客,商品名\nA,B\n'
        with self.assertRaises(ValueError) as ctx:
            pli.parse_csv_text(text)
        self.assertIn('必要な列がありません', str(ctx.exception))


class ProductLedgerPreviewCommitTest(unittest.TestCase):
    def test_preview_does_not_mutate(self):
        folder, path, c = newdb()
        try:
            before_c = c.execute('SELECT COUNT(*) AS n FROM customers').fetchone()['n']
            before_s = stock_snap(c)
            text = tiny_ledger_rows([
                '顧客A,仕入A,SC-1,スクラブ,M',
                '顧客A,仕入A,SC-1,スクラブ,L',
                '顧客B,仕入A,SC-1,スクラブ,M',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 't.csv',
                '_canWrite': True, '_actor': {'name': 'テスト'},
            })
            c.commit()
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM customers').fetchone()['n'], before_c)
            self.assertEqual(stock_snap(c), before_s)
            self.assertEqual(preview['rowCount'], 3)
            self.assertEqual(preview['countsRaw'].get('create', 0), 3)
        finally:
            c.close()
            folder.cleanup()

    def test_multi_size_same_product_and_stock_unchanged(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '既存病院', 'active': True})
            su = stock.save_supplier(c, {'name': '既存仕入', 'active': True})
            pr = stock.save_product(c, {
                'code': 'EX-1', 'name': '既存スクラブ', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '加工未整理', 'isStandard': False, 'active': True}],
            })
            # 在庫を1件入れて、取込で不変を確認
            loc = stock.ensure_unconfirmed_site(c)
            stock.register_opening(c, {
                '_canWrite': True, '_actor': {'name': 'seed'},
                'token': 'open-seed-pl-1',
                'customerProductId': pr['links'][0]['id'],
                'locationId': loc['id'],
                'condition': 'new',
                'basisDate': '2026-01-01',
                'receivedUnknown': True,
                'payoutOrder': 1,
                'allowZero': True,
                'lines': [{'size': 'M', 'qty': 7, 'unitCost': 1000}],
            })
            c.commit()
            before = stock_snap(c)
            self.assertEqual(before['qty'], 7)

            text = tiny_ledger_rows([
                '既存病院,既存仕入,EX-1,既存スクラブ,M',
                '既存病院,既存仕入,EX-1,既存スクラブ,L',
                '新規顧客,新規仕入,NW-1,新規商品,S',
                '新規顧客,新規仕入,NW-1,新規商品,M',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'commit.csv',
                '_canWrite': True, '_actor': {'name': '取込太郎'},
            }
            preview = pli.build_preview(c, body)
            # 既存M=unchanged, 既存L=size_add, 新規2行=create
            statuses = {r['rowNo']: r['status'] for r in preview['rows']}
            self.assertEqual(statuses[2], 'unchanged')
            self.assertEqual(statuses[3], 'size_add')
            self.assertEqual(statuses[4], 'create')
            self.assertEqual(statuses[5], 'create')

            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-token-01',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            self.assertEqual(result['counts']['productsCreated'], 1)
            # 既存EX-1へL追加 + 同一コミット内のNW-1へ2サイズ目追加
            self.assertEqual(result['counts']['sizesAdded'], 2)
            self.assertGreaterEqual(result['counts']['handlesCreated'], 1)
            nw = c.execute("SELECT id FROM products WHERE code='NW-1'").fetchone()
            sizes = {
                r['label'] for r in c.execute(
                    'SELECT label FROM product_sizes WHERE product_id=?', (nw['id'],)
                )
            }
            self.assertEqual(sizes, {'S', 'M'})

            # 再取込: 二重登録なし・在庫不変
            preview2 = pli.build_preview(c, body)
            self.assertEqual(preview2['countsRaw'].get('unchanged', 0), 4)
            result2 = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'pl-token-01',  # 同一トークン → already
            }, db_path=str(path))
            self.assertTrue(result2.get('already'))
            self.assertEqual(stock_snap(c), before)

            # 新トークンでも変更なし行はコミット対象外 → allowEmpty or no commit rows
            preview3 = pli.build_preview(c, body)
            with self.assertRaises(ValueError):
                pli.commit_preview(c, {
                    **body,
                    'previewFingerprint': preview3['previewFingerprint'],
                    'previewAt': preview3['previewAt'],
                    'token': 'pl-token-02',
                }, db_path=str(path))
        finally:
            c.close()
            folder.cleanup()

    def test_custom_order_and_blank_and_star_and_name_diff(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '病院A,仕入X,別注,青帽子（刺繍あり）,F',
                '病院A,仕入X,別注,青帽子,F',  # 別名 → 別商品候補（新規）
                '病院B,仕入Y,,ボタン 18mm,-',  # 品番空
                '病院C,仕入Z,FT1,＊帽子,F',  # 中古候補だが在庫は作らない
                '病院D,仕入Z,FT2,白衣,M',
                '病院D,仕入Z,FT2,白衣（刺繍あり）,M',  # 同品番・別名 → 品番比較
            ])
            # 先に白衣だけ登録
            cu = stock.save_customer(c, {'name': '病院D', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入Z', 'active': True})
            stock.save_product(c, {
                'code': 'FT2', 'name': '白衣', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': False, 'active': True}],
            })
            c.commit()
            before = stock_snap(c)

            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'edge.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            by_no = {r['rowNo']: r for r in preview['rows']}
            self.assertEqual(by_no[2]['status'], 'create')  # 別注 青帽子（刺繍あり）
            self.assertEqual(by_no[3]['status'], 'create')  # 別注 青帽子（別名）
            self.assertEqual(by_no[4]['product']['blankSku'], True)
            self.assertTrue(by_no[5]['csv']['usedMarkerPossible'])
            self.assertEqual(by_no[5]['status'], 'create')
            # FT2 は名差のため品番比較が必要（サイズ行を要確認へは流さない）
            ft2_group = next(g for g in preview['skuGroups'] if g['code'] == 'FT2')
            self.assertTrue(ft2_group['needsDecision'])
            self.assertTrue(by_no[7].get('skuDecisionNeeded'))
            self.assertNotEqual(by_no[7]['status'], 'needs_review')
            self.assertGreaterEqual(preview['skuDecisionPending'], 1)

            # 未決定のまま確定は拒否
            with self.assertRaises(ValueError):
                pli.commit_preview(c, {
                    'file': csv_b64_from_text(text), 'name': 'edge.csv',
                    'previewFingerprint': preview['previewFingerprint'],
                    'previewAt': preview['previewAt'],
                    'token': 'pl-edge-1',
                    '_canWrite': True, '_actor': {'name': 't'},
                }, db_path=str(path))

            # 同じ商品としてまとめる（公式名=白衣）
            sku_decisions = {
                'FT2': {
                    'decision': 'merge',
                    'officialName': '白衣',
                    'keepProductId': by_no[6]['product']['id'],
                    'mergeSourceIds': [],
                },
            }
            preview2 = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'edge.csv',
                'skuDecisions': sku_decisions,
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(preview2['reviewCount'], 0)
            self.assertEqual(preview2['skuDecisionPending'], 0)
            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'edge.csv',
                'skuDecisions': sku_decisions,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'pl-edge-2',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            # ＊帽子 → 公式名は標記なし「帽子」、CSV原文は別名
            star = c.execute("SELECT id,name FROM products WHERE code='FT1'").fetchone()
            self.assertEqual(star['name'], '帽子')
            star_aliases = [
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (star['id'],),
                )
            ]
            self.assertIn('＊帽子', star_aliases)
            # 別注は2商品（例外）
            betchu = c.execute("SELECT COUNT(*) AS n FROM products WHERE code='別注'").fetchone()['n']
            self.assertEqual(betchu, 2)
            # FT2 は1商品のまま。別名が残る
            ft2n = c.execute("SELECT COUNT(*) AS n FROM products WHERE code='FT2' AND merged_into_id IS NULL").fetchone()['n']
            self.assertEqual(ft2n, 1)
            aliases = [
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name'"
                )
            ]
            self.assertTrue(any('刺繍' in a for a in aliases))
        finally:
            c.close()
            folder.cleanup()

    def test_used_marker_auto_merge_not_needs_review(self):
        """同品番で商品名差が先頭＊/*だけ → 要確認にせず1商品。サイズ・取扱は保持。"""
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,1702,緑つなぎ,M',
                '顧客A,仕入A,1702,＊緑つなぎ,L',
                '顧客B,仕入A,1702,*緑つなぎ,M',
                '顧客A,仕入A,1702,＊ 緑つなぎ,LL',  # 標記直後の空白
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'star.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            before = stock_snap(c)
            preview = pli.build_preview(c, body)
            self.assertEqual(preview['reviewCount'], 0)
            self.assertEqual(preview['skuDecisionPending'], 0)
            self.assertEqual(preview['countsRaw'].get('needs_review', 0), 0)
            group = next(g for g in preview['skuGroups'] if g['code'] == '1702')
            self.assertFalse(group['needsDecision'])
            self.assertTrue(group['plannedSingleProduct'])
            self.assertTrue(group['usedMarkerAutoMerged'])
            self.assertEqual(group['variantCount'], 1)
            # 括弧外の明示色「緑」は色欄へ。登録名はベース「つなぎ」
            self.assertEqual(group['productPlan']['productName'], 'つなぎ')
            self.assertEqual(group['productPlan']['sizeCount'], 3)
            self.assertEqual(group['productPlan']['customerCount'], 2)
            for r in preview['rows']:
                self.assertNotEqual(r['status'], 'needs_review')
                self.assertFalse(r.get('skuDecisionNeeded'))
                self.assertTrue(r.get('usedMarkerAutoMerged'))
                self.assertEqual(r['product'].get('officialName'), 'つなぎ')
                self.assertEqual(
                    (r.get('parenClassification') or {}).get('color')
                    or r['product'].get('officialColor')
                    or r['product'].get('proposedColor'),
                    'グリーン',
                )

            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-star-1',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            self.assertEqual(result['counts']['productsCreated'], 1)
            n = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='1702' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(n, 1)
            prod = c.execute("SELECT id,name,color FROM products WHERE code='1702'").fetchone()
            self.assertEqual(prod['name'], 'つなぎ')
            self.assertEqual(prod['color'], 'グリーン')
            sizes = {
                r['label'] for r in c.execute(
                    'SELECT label FROM product_sizes WHERE product_id=?', (prod['id'],)
                )
            }
            self.assertEqual(sizes, {'M', 'L', 'LL'})
            handles = c.execute(
                'SELECT COUNT(*) AS n FROM customer_products WHERE product_id=?', (prod['id'],)
            ).fetchone()['n']
            self.assertEqual(handles, 2)
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (prod['id'],),
                )
            }
            self.assertTrue(any(a.startswith('＊') or a.startswith('*') or '＊' in a for a in aliases))
            self.assertTrue(any('緑つなぎ' in a for a in aliases) or '緑つなぎ' in aliases)

            # 再取込で二重登録なし
            preview2 = pli.build_preview(c, body)
            self.assertEqual(preview2['reviewCount'], 0)
            self.assertEqual(preview2['plannedCounts']['productsToCreate'], 0)
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) AS n FROM products WHERE code='1702' AND merged_into_id IS NULL"
                ).fetchone()['n'],
                1,
            )

            # 本物の名差は引き続き品番比較
            text_diff = tiny_ledger_rows([
                '顧客A,仕入A,1702,緑つなぎ,M',
                '顧客A,仕入A,1702,青つなぎ,L',
            ])
            preview_diff = pli.build_preview(c, {
                'file': csv_b64_from_text(text_diff), 'name': 'diff.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g2 = next(g for g in preview_diff['skuGroups'] if g['code'] == '1702')
            self.assertTrue(g2['needsDecision'])
            self.assertEqual(preview_diff['skuDecisionPending'], 1)
        finally:
            c.close()
            folder.cleanup()

    def test_used_marker_only_names_strip_for_official(self):
        """標記付き名だけしか無いときも公式名は標記を剥がす。要確認にしない。"""
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,UM-1,＊作業着,M',
                '顧客A,仕入A,UM-1,*作業着,L',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'only-star.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            group = next(g for g in preview['skuGroups'] if g['code'] == 'UM-1')
            self.assertFalse(group['needsDecision'])
            self.assertEqual(group['productPlan']['productName'], '作業着')
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-star-only-1',
            }, db_path=str(path))
            c.commit()
            prod = c.execute("SELECT id,name FROM products WHERE code='UM-1'").fetchone()
            self.assertEqual(prod['name'], '作業着')
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (prod['id'],),
                )
            }
            self.assertTrue({'＊作業着', '*作業着'} & aliases)
        finally:
            c.close()
            folder.cleanup()

    def test_existing_duplicate_products_not_auto_merged_by_marker(self):
        """DBに同一品番が複数あるときは中古標記でも自動まとめしない。"""
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入A', 'active': True})
            stock.save_product(c, {
                'code': 'DUP-1', 'name': '緑つなぎ', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': False, 'active': True}],
                '_allowHistoricalDuplicateCode': True,
            })
            stock.save_product(c, {
                'code': 'DUP-1', 'name': '＊緑つなぎ', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['L'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': False, 'active': True}],
                '_allowHistoricalDuplicateCode': True,
            })
            c.commit()
            text = tiny_ledger_rows([
                '顧客A,仕入A,DUP-1,緑つなぎ,M',
                '顧客A,仕入A,DUP-1,＊緑つなぎ,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'dup.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            group = next(g for g in preview['skuGroups'] if g['code'] == 'DUP-1')
            self.assertTrue(group['needsDecision'])
            self.assertEqual(group['existingCount'], 2)
            self.assertFalse(group.get('usedMarkerAutoMerged'))
        finally:
            c.close()
            folder.cleanup()

    def test_opening_stock_links_marked_name_keeps_condition_split(self):
        """台帳取込後の初期在庫CSV: 標記名も同一商品へ。新古在庫は分けたまま。"""
        import legacy_csv_import as legacy

        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '病院A,仕入A,1702,緑つなぎ,M',
                '病院A,仕入A,1702,＊緑つなぎ,L',
            ])
            body = {
                'file': csv_b64_from_text(ledger), 'name': 'ledger.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-open-link-1',
            }, db_path=str(path))
            c.commit()
            prod = c.execute("SELECT id,name,color FROM products WHERE code='1702'").fetchone()
            self.assertEqual(prod['name'], 'つなぎ')
            self.assertEqual(prod['color'], 'グリーン')
            before_qty = c.execute('SELECT COALESCE(SUM(qty_remaining),0) AS q FROM lots').fetchone()['q']
            self.assertEqual(before_qty, 0)

            header = (
                '新古,仕入先,顧客,商品名,商品コード,サイズ,仕入単価,購入数量,購入金額,'
                '出荷数,出荷金額,在庫,在庫金額,営業所コード,拠点,棚,補正在庫数'
            )
            opening = '\n'.join([
                header,
                '新品,仕入A,病院A,緑つなぎ,1702,M,1000,0,0,0,0,3,0,1,,,0',
                '中古,仕入A,病院A,＊緑つなぎ,1702,L,0,0,0,0,0,2,0,1,,,0',
            ])
            stock.ensure_unconfirmed_site(c)
            c.commit()
            obody = {
                'file': csv_b64_from_text(opening), 'name': 'open.csv',
                'basisDate': '2026-10-01', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            oprev = legacy.build_preview(c, obody)
            for r in oprev['rows']:
                self.assertEqual(r['product']['id'], prod['id'])
                self.assertIn(r['status'], ('create', 'reuse'))
            oresult = legacy.commit_preview(c, {
                **obody,
                'previewFingerprint': oprev['previewFingerprint'],
                'previewAt': oprev['previewAt'],
                'token': 'leg-open-link-1',
            }, db_path=str(path))
            c.commit()
            self.assertEqual(oresult['counts']['openingCreated'], 2)
            # 商品は1件のまま
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) AS n FROM products WHERE code='1702' AND merged_into_id IS NULL"
                ).fetchone()['n'],
                1,
            )
            lots = list(c.execute(
                '''SELECT l.condition, l.qty_remaining, l.size_label
                   FROM lots l
                   JOIN customer_products cp ON cp.id=l.customer_product_id
                   WHERE cp.product_id=? ORDER BY l.condition, l.size_label''',
                (prod['id'],),
            ))
            self.assertEqual(len(lots), 2)
            by_cond = {r['condition']: r for r in lots}
            self.assertEqual(by_cond['new']['qty_remaining'], 3)
            self.assertEqual(by_cond['used']['qty_remaining'], 2)
            # 台帳再取込でも在庫を変えない
            snap = pli.stock_snapshot(c)
            preview2 = pli.build_preview(c, body)
            self.assertEqual(preview2['plannedCounts']['productsToCreate'], 0)
            self.assertEqual(pli.stock_snapshot(c), snap)
        finally:
            c.close()
            folder.cleanup()

    def test_normal_code_uniqueness_and_multi_size_one_product(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-9,スクラブ青,M',
                '顧客B,仕入B,SKU-9,スクラブ（青）,L',  # 名差・仕入先差 → 比較
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'one.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            groups = [g for g in preview['skuGroups'] if g['code'] == 'SKU-9']
            self.assertEqual(len(groups), 1)
            self.assertTrue(groups[0]['needsDecision'])
            self.assertEqual(groups[0]['variantCount'], 2)

            sku = {
                'SKU-9': {
                    'decision': 'merge',
                    'officialName': 'スクラブ青',
                    'primarySupplier': '仕入A',
                },
            }
            preview2 = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'one.csv',
                'skuDecisions': sku, '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(preview2['plannedCounts']['productsToCreate'], 1)
            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'one.csv',
                'skuDecisions': sku,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'pl-one-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['counts']['productsCreated'], 1)
            n = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='SKU-9' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(n, 1)
            prod = c.execute("SELECT id,name FROM products WHERE code='SKU-9'").fetchone()
            self.assertEqual(prod['name'], 'スクラブ青')
            sizes = {
                r['label'] for r in c.execute(
                    'SELECT label FROM product_sizes WHERE product_id=?', (prod['id'],)
                )
            }
            self.assertEqual(sizes, {'M', 'L'})
            # 手動で同品番新規は拒否
            with self.assertRaises(ValueError) as ctx:
                stock.save_product(c, {
                    'code': 'SKU-9', 'name': '別物', 'color': '', 'supplierId': None,
                    'sizes': ['S'], 'links': [], 'active': True,
                })
            self.assertIn('1品番=1商品', str(ctx.exception))
        finally:
            c.close()
            folder.cleanup()

    def test_hold_excludes_from_commit(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,HD-1,本体,M',
                '顧客A,仕入A,HD-1,加工サービス,M',
            ])
            sku = {
                'HD-1': {'decision': 'hold', 'deferReason': '本体と加工の切り分け未確定'},
            }
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'hold.csv',
                'skuDecisions': sku, '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(preview['countsRaw'].get('hold'), 2)
            with self.assertRaises(ValueError):
                pli.commit_preview(c, {
                    'file': csv_b64_from_text(text), 'name': 'hold.csv',
                    'skuDecisions': sku,
                    'previewFingerprint': preview['previewFingerprint'],
                    'previewAt': preview['previewAt'],
                    'token': 'pl-hold-1',
                    '_canWrite': True, '_actor': {'name': 't'},
                }, db_path=str(path))
        finally:
            c.close()
            folder.cleanup()

    def test_supplier_diff_links_as_sub(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
            su1 = stock.save_supplier(c, {'name': '仕入本', 'active': True})
            su2 = stock.save_supplier(c, {'name': '仕入サブ', 'active': True})
            pr = stock.save_product(c, {
                'code': 'MS-1', 'name': 'マルチ商品', 'color': '', 'supplierId': su1['id'],
                'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '加工未整理', 'isStandard': False, 'active': True}],
            })
            c.commit()
            before = stock_snap(c)
            text = tiny_ledger_rows([
                '顧客A,仕入サブ,MS-1,マルチ商品,M',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'sub.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            row = preview['rows'][0]
            self.assertEqual(row['product']['action'], 'reuse')
            self.assertTrue(row['product']['subSupplierAdd'])
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-sub-1',
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['counts']['subSuppliersAdded'], 1)
            prod = stock.product_row(c, pr['id'])
            self.assertEqual(prod['supplierId'], su1['id'])  # 通常仕入先不変
            self.assertIn(su2['id'], prod['subSupplierIds'])
            self.assertEqual(stock_snap(c), before)
        finally:
            c.close()
            folder.cleanup()

    def test_size_only_auto_merge_not_needs_review(self):
        """品番+名+仕入先同一でサイズだけの差 → 要確認に出さず1商品へまとめる。"""
        folder, path, c = newdb()
        try:
            sizes = ['SSS', 'SS', 'S', 'M', 'L', 'LL', '3L', '4L', '5L', '6L', 'M']
            rows = [
                f'㈱ＤＨＣ夕張長沼工場,アイトス㈱,AZ-6850-008,ワークパンツ,{s}'
                for s in sizes
            ]
            # 別顧客・同内容 → 取扱だけ増やす（要確認にしない）
            rows.append('㈱別顧客,アイトス㈱,AZ-6850-008,ワークパンツ,L')
            text = tiny_ledger_rows(rows)
            body = {
                'file': csv_b64_from_text(text), 'name': 'size.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            before = stock_snap(c)
            preview = pli.build_preview(c, body)
            self.assertEqual(preview['reviewCount'], 0)
            self.assertEqual(preview['skuDecisionPending'], 0)
            self.assertEqual(preview['countsRaw'].get('needs_review', 0), 0)
            group = next(g for g in preview['skuGroups'] if g['code'] == 'AZ-6850-008')
            self.assertFalse(group['needsDecision'])
            self.assertTrue(group['plannedSingleProduct'])
            self.assertEqual(group['variantCount'], 1)
            self.assertEqual(group['productPlan']['label'], '新規商品')
            self.assertEqual(group['productPlan']['sizeCount'], 10)
            self.assertEqual(group['productPlan']['customerCount'], 2)
            for r in preview['rows']:
                self.assertNotEqual(r['status'], 'needs_review')
                self.assertFalse(r.get('skuDecisionNeeded'))
                self.assertTrue(r.get('skuAutoMerged'))
                self.assertEqual(r['productPlan']['label'], '新規商品')
            planned = preview['plannedCounts']
            self.assertEqual(planned['productsToCreate'], 1)
            self.assertEqual(planned['sizesToAdd'], 10)  # 重複Mは1回
            self.assertEqual(planned['handlesToCreate'], 2)
            self.assertEqual(planned['csvRows'], len(rows))

            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-size-auto-1',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            self.assertEqual(result['counts']['productsCreated'], 1)
            # 新規商品作成時に先頭サイズ1つが付き、残りが sizesAdded
            self.assertEqual(result['counts']['sizesAdded'], 9)
            self.assertEqual(result['counts']['handlesCreated'], 2)
            n = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='AZ-6850-008' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(n, 1)
            pid = c.execute("SELECT id FROM products WHERE code='AZ-6850-008'").fetchone()['id']
            got_sizes = {
                r['label'] for r in c.execute(
                    'SELECT label FROM product_sizes WHERE product_id=?', (pid,)
                )
            }
            self.assertEqual(got_sizes, set(sizes))  # M重複でも1つ
            self.assertNotIn('2L', got_sizes)  # LL≡2L 同一化しない・中間サイズ捏造なし
            handles = c.execute(
                'SELECT COUNT(*) AS n FROM customer_products WHERE product_id=?', (pid,)
            ).fetchone()['n']
            self.assertEqual(handles, 2)

            # 再取込で二重登録なし
            preview2 = pli.build_preview(c, body)
            self.assertEqual(preview2['reviewCount'], 0)
            self.assertEqual(preview2['countsRaw'].get('unchanged', 0), len(rows))
            self.assertEqual(preview2['plannedCounts']['productsToCreate'], 0)
            self.assertEqual(preview2['plannedCounts']['sizesToAdd'], 0)
            self.assertEqual(preview2['plannedCounts']['handlesToCreate'], 0)
            group2 = next(g for g in preview2['skuGroups'] if g['code'] == 'AZ-6850-008')
            self.assertEqual(group2['productPlan']['label'], '既存商品を使用')
        finally:
            c.close()
            folder.cleanup()

    def test_name_diff_stays_in_sku_compare_not_row_review_flood(self):
        """同品番で商品名差があるとき、品番比較は残すがサイズ行を要確認に溢れさせない。"""
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,AZ-6850-008,ワークパンツ,SSS',
                '顧客A,仕入A,AZ-6850-008,ワークパンツ,SS',
                '顧客A,仕入A,AZ-6850-008,ワークパンツ,M',
                # 色語を含まない名差（明示色優先で自動まとめされないこと）
                '顧客B,仕入A,AZ-6850-008,作業パンツ,LL',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'diff.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            group = next(g for g in preview['skuGroups'] if g['code'] == 'AZ-6850-008')
            self.assertTrue(group['needsDecision'])
            self.assertEqual(group['variantCount'], 2)
            self.assertEqual(preview['skuDecisionPending'], 1)
            # 行は要確認ステータスへ流さない（品番比較未決定フラグのみ）
            self.assertEqual(preview['countsRaw'].get('needs_review', 0), 0)
            self.assertEqual(preview['reviewCount'], 0)
            for r in preview['rows']:
                self.assertTrue(r.get('skuDecisionNeeded'))
                self.assertNotEqual(r['status'], 'needs_review')
                self.assertIn(r['status'], ('create', 'reuse', 'size_add', 'unchanged'))
            # バリアント内のサイズは比較パネル用に保持
            sizes0 = next(v['sizes'] for v in group['variants'] if v['productName'] == 'ワークパンツ')
            self.assertEqual(set(sizes0), {'SSS', 'SS', 'M'})
        finally:
            c.close()
            folder.cleanup()


def tiny_opening_rows(rows):
    header = (
        '新古,仕入先,顧客,商品名,商品コード,サイズ,仕入単価,'
        '購入数量,購入金額,出荷数,出荷金額,在庫,在庫金額,営業所コード,拠点,棚,補正在庫数'
    )
    return '\n'.join([header] + rows)


def opening_line(supplier, customer, name, code, size, qty, *, cost='100', adjust='0'):
    # 補正在庫数・購入・出荷は判定に使わない（ダミー値）
    return (
        f'新品,{supplier},{customer},{name},{code},{size},{cost},'
        f'9,900,8,800,{qty},0,1,本社,A1,{adjust}'
    )


class AuxOpeningPrimarySupplierTest(unittest.TestCase):
    """初期在庫CSVは通常仕入先判定のみ。在庫数量は書かない。"""

    def _ledger_00008(self):
        return tiny_ledger_rows([
            '㈱合田観光商事　ひまわり,㈱ユニーク,00008,ベーシックカラーベスト　046.蛍光グリーン,F',
            '㈱合田観光商事　ひまわり,㈱ユニーク,00008,ベーシックカラーベスト　046.蛍光グリーン,XL',
            '㈱合田観光商事　ひまわり,㈱ユニーク,00008,ベーシックカラーベスト　046.蛍光グリーン,2XL',
            '㈱合田観光商事　ひまわり,アイトス㈱,00008,駐車場ベスト（緑),F',
            '㈱合田観光商事　ひまわり,アイトス㈱,00008,駐車場ベスト（緑),2L',
        ])

    def _aux_unique_positive(self):
        cust = '㈱合田観光商事　ひまわり'
        name = 'ベーシックカラーベスト　046.蛍光グリーン'
        return tiny_opening_rows([
            opening_line('㈱ユニーク', cust, name, '00008', 'F', '0', adjust='-5'),
            opening_line('㈱ユニーク', cust, name, '00008', 'XL', '0', adjust='-1'),
            opening_line('㈱ユニーク', cust, name, '00008', '2XL', '2', adjust='-1'),
        ])

    def test_ui_mentions_aux_opening_csv(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('auxFile', js)
        self.assertIn('初期在庫CSV', js)
        self.assertIn('該当登録なし', js)
        self.assertIn('primarySupplierManual', js)
        self.assertIn('confirmPrimaryChange', js)

    def test_without_aux_legacy_still_works(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows(['顧客A,仕入A,SC-1,スクラブ,M'])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 't.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertIsNone(preview.get('auxOpeningStock'))
            self.assertEqual(preview['plannedCounts']['productsToCreate'], 1)
            self.assertEqual(stock_snap(c), before)
        finally:
            c.close()
            folder.cleanup()

    def test_example_unique_positive_aitos_none(self):
        folder, path, c = newdb()
        try:
            before = stock_snap(c)
            body = {
                'file': csv_b64_from_text(self._ledger_00008()),
                'name': 'ledger.csv',
                'auxFile': csv_b64_from_text(self._aux_unique_positive()),
                'auxName': 'opening.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            self.assertIsNotNone(preview['auxOpeningStock'])
            self.assertEqual(stock_snap(c), before)
            group = next(g for g in preview['skuGroups'] if g['code'] == '00008')
            # 非ゼロ在庫がユニーク側1組 → フルパッケージ自動採用（色差だけでは要確認にしない）
            self.assertFalse(group['needsDecision'])
            self.assertTrue(group.get('auxStockPickAuto'))
            aux = group['auxPrimary']
            self.assertTrue(aux['applicable'])
            self.assertEqual(aux['autoPrimarySupplier'], '㈱ユニーク')
            rule = aux.get('pairAdoptionRule') or group.get('auxStockPickReason') or ''
            self.assertIn('在庫が0以外の候補が1組', rule)
            self.assertRegex(rule, r'（在庫\d+）を採用')
            by_sup = {j['supplier']: j for j in aux['supplierJudgments']}
            self.assertEqual(by_sup['㈱ユニーク']['plannedRole'], 'primary')
            self.assertEqual(by_sup['アイトス㈱']['plannedRole'], 'sub')
            self.assertEqual(by_sup['アイトス㈱']['auxStatus'], 'none')
            self.assertEqual(by_sup['アイトス㈱'].get('auxStockDisplay'), '—')
            self.assertIsNotNone(by_sup['㈱ユニーク'].get('auxStockTotal'))
            self.assertGreater(by_sup['㈱ユニーク']['auxStockTotal'], 0)
            self.assertEqual(by_sup['アイトス㈱']['auxStatusLabel'], '該当登録なし')
            # 補正在庫は使わない（Unique の補正はマイナスでも 2XL=2 で正判定）
            self.assertTrue(by_sup['㈱ユニーク']['hasPositiveStock'])
            self.assertEqual(preview['reviewCount'], 0)
            self.assertEqual(preview['skuDecisionPending'], 0)
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-aux-00008',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            prod = c.execute(
                "SELECT id,name,supplier_id FROM products WHERE code='00008' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertEqual(prod['name'], 'ベーシックカラーベスト')
            color_row = c.execute(
                "SELECT color FROM products WHERE code='00008' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertEqual(color_row['color'], '蛍光グリーン')
            sup = c.execute('SELECT name FROM suppliers WHERE id=?', (prod['supplier_id'],)).fetchone()
            self.assertEqual(sup['name'], '㈱ユニーク')
            subs = {
                r['name'] for r in c.execute(
                    '''SELECT s.name FROM product_suppliers ps
                       JOIN suppliers s ON s.id=ps.supplier_id WHERE ps.product_id=?''',
                    (prod['id'],),
                )
            }
            self.assertIn('アイトス㈱', subs)
            # 再取込で二重登録なし
            preview3 = pli.build_preview(c, body)
            self.assertEqual(preview3['plannedCounts']['productsToCreate'], 0)
            n = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='00008' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(n, 1)
        finally:
            c.close()
            folder.cleanup()

    def test_both_positive_picks_max_package_as_primary(self):
        """正の在庫が両社にあるとき、最多一意ならその候補フルパッケージ（通常仕入先含む）を採用。"""
        folder, path, c = newdb()
        try:
            cust = '㈱合田観光商事　ひまわり'
            aux = tiny_opening_rows([
                opening_line('㈱ユニーク', cust, 'ベーシックカラーベスト　046.蛍光グリーン', '00008', '2XL', '2'),
                opening_line('アイトス㈱', cust, '駐車場ベスト（緑)', '00008', 'F', '1'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(self._ledger_00008()),
                'auxFile': csv_b64_from_text(aux),
                'name': 'l.csv', 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '00008')
            auxj = g.get('auxPrimary') or {}
            self.assertEqual(auxj.get('nameAdoptionRuleCode'), 'aux_max_positive_stock')
            self.assertEqual(auxj['autoPrimarySupplier'], '㈱ユニーク')
            self.assertFalse(auxj['needsManualPrimary'])
            self.assertEqual(auxj.get('suggestedOfficialName'), 'ベーシックカラーベスト')
            self.assertEqual(auxj.get('adoptedColor') or auxj.get('plannedRegisteredColor'), '蛍光グリーン')
            self.assertFalse(g['needsDecision'])
            # 同数なら従来どおり手動
            aux_tie = tiny_opening_rows([
                opening_line('㈱ユニーク', cust, 'ベーシックカラーベスト　046.蛍光グリーン', '00008', '2XL', '2'),
                opening_line('アイトス㈱', cust, '駐車場ベスト（緑)', '00008', 'F', '2'),
            ])
            preview_tie = pli.build_preview(c, {
                'file': csv_b64_from_text(self._ledger_00008()),
                'auxFile': csv_b64_from_text(aux_tie),
                'name': 'l.csv', 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            aux_tie_j = next(x for x in preview_tie['skuGroups'] if x['code'] == '00008')['auxPrimary']
            self.assertEqual(aux_tie_j.get('autoPrimarySupplier') or '', '')
            self.assertTrue(aux_tie_j.get('needsManualPrimary') or aux_tie_j.get('pairNeedsChoice'))
        finally:
            c.close()
            folder.cleanup()

    def test_all_zero_unique_has_rows(self):
        folder, path, c = newdb()
        try:
            cust = '㈱合田観光商事　ひまわり'
            aux = tiny_opening_rows([
                opening_line('㈱ユニーク', cust, 'ベーシックカラーベスト　046.蛍光グリーン', '00008', 'F', '0'),
                opening_line('㈱ユニーク', cust, 'ベーシックカラーベスト　046.蛍光グリーン', '00008', 'XL', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(self._ledger_00008()),
                'auxFile': csv_b64_from_text(aux),
                'name': 'l.csv', 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            auxj = next(g for g in preview['skuGroups'] if g['code'] == '00008')['auxPrimary']
            self.assertEqual(auxj['autoPrimarySupplier'], '㈱ユニーク')
            self.assertIn('該当行がある仕入先が1社', auxj['autoReason'])
            self.assertTrue(
                '在庫0' in auxj['autoReason'] or '初期在庫：0' in auxj['autoReason'],
                auxj['autoReason'],
            )
        finally:
            c.close()
            folder.cleanup()

    def test_negative_not_positive(self):
        folder, path, c = newdb()
        try:
            cust = '㈱合田観光商事　ひまわり'
            aux = tiny_opening_rows([
                opening_line('㈱ユニーク', cust, 'ベーシックカラーベスト　046.蛍光グリーン', '00008', 'F', '-3'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(self._ledger_00008()),
                'auxFile': csv_b64_from_text(aux),
                'name': 'l.csv', 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            auxj = next(g for g in preview['skuGroups'] if g['code'] == '00008')['auxPrimary']
            self.assertEqual(auxj['autoPrimarySupplier'], '㈱ユニーク')
            j = next(x for x in auxj['supplierJudgments'] if x['supplier'] == '㈱ユニーク')
            self.assertFalse(j['hasPositiveStock'])
            self.assertTrue(j['hasAnyAuxRows'])
        finally:
            c.close()
            folder.cleanup()

    def test_no_aux_rows_for_either(self):
        folder, path, c = newdb()
        try:
            aux = tiny_opening_rows([
                opening_line('別仕入', '別顧客', '別商品', '99999', 'M', '5'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(self._ledger_00008()),
                'auxFile': csv_b64_from_text(aux),
                'name': 'l.csv', 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            auxj = next(g for g in preview['skuGroups'] if g['code'] == '00008')['auxPrimary']
            self.assertEqual(auxj['autoPrimarySupplier'], '')
            self.assertTrue(auxj['needsManualPrimary'])
            for j in auxj['supplierJudgments']:
                self.assertEqual(j['auxStatusLabel'], '該当登録なし')
        finally:
            c.close()
            folder.cleanup()

    def test_empty_qty_undecidable(self):
        folder, path, c = newdb()
        try:
            cust = '㈱合田観光商事　ひまわり'
            aux = tiny_opening_rows([
                opening_line('㈱ユニーク', cust, 'ベーシックカラーベスト　046.蛍光グリーン', '00008', 'F', ''),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(self._ledger_00008()),
                'auxFile': csv_b64_from_text(aux),
                'name': 'l.csv', 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            auxj = next(g for g in preview['skuGroups'] if g['code'] == '00008')['auxPrimary']
            self.assertEqual(auxj['autoPrimarySupplier'], '')
            self.assertTrue(auxj['needsManualPrimary'])
            self.assertIn('空欄または不正', auxj['manualReason'])
        finally:
            c.close()
            folder.cleanup()

    def test_code_leading_zeros_not_equated(self):
        folder, path, c = newdb()
        try:
            cust = '㈱合田観光商事　ひまわり'
            # 補助が「8」でも台帳「00008」とは結びつけない
            aux = tiny_opening_rows([
                opening_line('㈱ユニーク', cust, 'ベーシックカラーベスト　046.蛍光グリーン', '8', '2XL', '9'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(self._ledger_00008()),
                'auxFile': csv_b64_from_text(aux),
                'name': 'l.csv', 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            auxj = next(g for g in preview['skuGroups'] if g['code'] == '00008')['auxPrimary']
            j = next(x for x in auxj['supplierJudgments'] if x['supplier'] == '㈱ユニーク')
            self.assertEqual(j['auxStatus'], 'none')
        finally:
            c.close()
            folder.cleanup()

    def test_existing_primary_change_requires_confirm(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '㈱合田観光商事　ひまわり', 'active': True})
            su_a = stock.save_supplier(c, {'name': 'アイトス㈱', 'active': True})
            stock.save_supplier(c, {'name': '㈱ユニーク', 'active': True})
            pr = stock.save_product(c, {
                'code': '00008', 'name': '駐車場ベスト（緑)', 'color': '',
                'supplierId': su_a['id'], 'active': True, 'sizes': ['F', '2L'],
                'links': [{'customerId': cu['id'], 'embroidery': '加工未整理', 'isStandard': False, 'active': True}],
            })
            c.commit()
            before = stock_snap(c)
            body = {
                'file': csv_b64_from_text(self._ledger_00008()),
                'auxFile': csv_b64_from_text(self._aux_unique_positive()),
                'name': 'l.csv', 'auxName': 'o.csv',
                'skuDecisions': {
                    '00008': {
                        'decision': 'merge',
                        'keepProductId': pr['id'],
                        'officialName': 'ベーシックカラーベスト　046.蛍光グリーン',
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            # 既存通常=アイトス → 自動通常=ユニーク のため確認必須
            self.assertGreater(preview['reviewCount'], 0)
            reasons = ' '.join(
                ' '.join(r.get('reasons') or []) for r in preview['rows'] if r.get('status') == 'needs_review'
            )
            self.assertIn('明示確認', reasons)
            body['skuDecisions']['00008']['confirmPrimaryChange'] = True
            preview2 = pli.build_preview(c, body)
            self.assertEqual(preview2['reviewCount'], 0)
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'pl-aux-confirm-1',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            fresh = c.execute('SELECT supplier_id FROM products WHERE id=?', (pr['id'],)).fetchone()
            self.assertEqual(
                c.execute('SELECT name FROM suppliers WHERE id=?', (fresh['supplier_id'],)).fetchone()['name'],
                '㈱ユニーク',
            )
        finally:
            c.close()
            folder.cleanup()

    def test_manual_primary_not_overwritten_by_auto(self):
        folder, path, c = newdb()
        try:
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(self._ledger_00008()),
                'auxFile': csv_b64_from_text(self._aux_unique_positive()),
                'name': 'l.csv', 'auxName': 'o.csv',
                'skuDecisions': {
                    '00008': {
                        'decision': 'merge',
                        'officialName': '駐車場ベスト（緑)',
                        'primarySupplier': 'アイトス㈱',
                        'primarySupplierManual': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            resolved = next(
                g for g in preview['skuGroups'] if g['code'] == '00008'
            )['primarySupplierResolved']
            self.assertEqual(resolved['primarySupplier'], 'アイトス㈱')
            self.assertEqual(resolved['source'], 'manual')
            self.assertTrue(resolved['conflictWithAuto'])
            self.assertEqual(resolved['autoPrimarySupplier'], '㈱ユニーク')
        finally:
            c.close()
            folder.cleanup()


class SingleSupplierPreviewCommitConsistencyTest(unittest.TestCase):
    """候補1社のときプレビューと確定の通常仕入先が一致する（一時DBのみ）。"""

    def test_ui_shows_single_candidate_reason(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('plannedSupplierDisplay', js)
        self.assertIn('候補が1社のため自動設定', pli.SINGLE_SUPPLIER_AUTO_REASON)

    def test_one_candidate_no_aux_preview_matches_commit(self):
        """候補1社・補助CSVなし → プレビューで自動設定、確定も同じ仕入先。"""
        folder, path, c = newdb()
        try:
            # 名差だけで要確認（仕入先はトムス㈱のみ）
            text = tiny_ledger_rows([
                '顧客A,トムス㈱,00085-CVT-031,CVTパンツ,M',
                '顧客A,トムス㈱,00085-CVT-031,CVTズボン,L',
            ])
            before = stock_snap(c)
            body = {
                'file': csv_b64_from_text(text), 'name': 'single.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            g = next(x for x in preview['skuGroups'] if x['code'] == '00085-CVT-031')
            self.assertEqual(g['plannedPrimarySupplier'], 'トムス㈱')
            self.assertEqual(g['plannedPrimarySupplierReason'], pli.SINGLE_SUPPLIER_AUTO_REASON)
            self.assertEqual(g['plannedPrimarySupplierSource'], 'single')
            aux = g['auxPrimary']
            self.assertIsNotNone(aux)
            self.assertEqual(aux['autoPrimarySupplier'], 'トムス㈱')
            self.assertEqual(aux['autoReason'], pli.SINGLE_SUPPLIER_AUTO_REASON)
            self.assertTrue(aux['singleCandidateAuto'])
            self.assertFalse(aux['applicable'])  # 補助CSVパネルは不要
            self.assertIsNone(preview.get('auxOpeningStock'))

            body2 = {
                **body,
                'skuDecisions': {
                    '00085-CVT-031': {'decision': 'merge'},
                },
            }
            preview2 = pli.build_preview(c, body2)
            g2 = next(x for x in preview2['skuGroups'] if x['code'] == '00085-CVT-031')
            resolved = g2['primarySupplierResolved']
            self.assertEqual(resolved['primarySupplier'], 'トムス㈱')
            self.assertEqual(resolved['source'], 'single')
            self.assertEqual(resolved['autoReason'], pli.SINGLE_SUPPLIER_AUTO_REASON)
            self.assertEqual(g2['plannedPrimarySupplier'], 'トムス㈱')
            self.assertEqual(preview2['reviewCount'], 0)

            result = pli.commit_preview(c, {
                **body2,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'pl-single-1',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            prod = c.execute(
                "SELECT id,supplier_id FROM products WHERE code='00085-CVT-031' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertIsNotNone(prod)
            sup = c.execute('SELECT name FROM suppliers WHERE id=?', (prod['supplier_id'],)).fetchone()
            self.assertEqual(sup['name'], 'トムス㈱')
        finally:
            c.close()
            folder.cleanup()

    def test_multi_candidate_no_aux_stays_unset_until_manual(self):
        """候補複数・補助なし → 未設定。手動選択後はプレビュー＝確定。"""
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-M1,作業着,M',
                '顧客A,仕入B,SKU-M1,作業着,L',
            ])
            before = stock_snap(c)
            body = {
                'file': csv_b64_from_text(text), 'name': 'multi.csv',
                'skuDecisions': {'SKU-M1': {'decision': 'merge'}},
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-M1')
            self.assertEqual(g['plannedPrimarySupplier'], '')
            self.assertTrue(g['auxPrimary']['needsManualPrimary'])
            self.assertGreater(preview['reviewCount'], 0)

            body['skuDecisions']['SKU-M1'] = {
                'decision': 'merge',
                'primarySupplier': '仕入B',
                'primarySupplierManual': True,
            }
            preview2 = pli.build_preview(c, body)
            g2 = next(x for x in preview2['skuGroups'] if x['code'] == 'SKU-M1')
            self.assertEqual(g2['plannedPrimarySupplier'], '仕入B')
            self.assertEqual(g2['primarySupplierResolved']['source'], 'manual')
            self.assertEqual(preview2['reviewCount'], 0)
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'pl-multi-manual-1',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            prod = c.execute(
                "SELECT supplier_id FROM products WHERE code='SKU-M1' AND merged_into_id IS NULL"
            ).fetchone()
            name = c.execute('SELECT name FROM suppliers WHERE id=?', (prod['supplier_id'],)).fetchone()['name']
            self.assertEqual(name, '仕入B')
        finally:
            c.close()
            folder.cleanup()

    def test_zero_candidates_stays_unset(self):
        """仕入先空欄のみ → 未設定（捏造しない）。"""
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,,SKU-Z0,名なし商品,M',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'zero.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-Z0')
            self.assertEqual(g['plannedPrimarySupplier'], '')
            self.assertEqual((g.get('auxPrimary') or {}).get('autoPrimarySupplier'), '')
            self.assertEqual((g.get('auxPrimary') or {}).get('decisionKind'), 'none')
        finally:
            c.close()
            folder.cleanup()

    def test_manual_overrides_single_candidate_auto(self):
        """候補1社の自動より手動選択を優先。"""
        folder, path, c = newdb()
        try:
            # 仕入先候補は1社だが、手動で別名を指定しても…候補外は作れない想定。
            # 同一候補を手動明示した場合は source=manual。
            text = tiny_ledger_rows([
                '顧客A,トムス㈱,00085-CVT-031,CVTパンツ,M',
                '顧客A,トムス㈱,00085-CVT-031,CVTズボン,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'manual.csv',
                'skuDecisions': {
                    '00085-CVT-031': {
                        'decision': 'merge',
                        'primarySupplier': 'トムス㈱',
                        'primarySupplierManual': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            resolved = next(
                g for g in preview['skuGroups'] if g['code'] == '00085-CVT-031'
            )['primarySupplierResolved']
            self.assertEqual(resolved['primarySupplier'], 'トムス㈱')
            self.assertEqual(resolved['source'], 'manual')
            self.assertEqual(resolved['autoPrimarySupplier'], 'トムス㈱')
        finally:
            c.close()
            folder.cleanup()

    def test_existing_primary_not_changed_without_confirm(self):
        """既存通常仕入先あり＋候補1社が異なる → 明示確認なしでは要確認。"""
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
            su_old = stock.save_supplier(c, {'name': '既存仕入', 'active': True})
            stock.save_supplier(c, {'name': 'トムス㈱', 'active': True})
            pr = stock.save_product(c, {
                'code': '00085-CVT-031', 'name': 'CVTパンツ', 'color': '',
                'supplierId': su_old['id'], 'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            text = tiny_ledger_rows([
                '顧客A,トムス㈱,00085-CVT-031,CVTパンツ,M',
                '顧客A,トムス㈱,00085-CVT-031,CVTズボン,L',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'exist.csv',
                'skuDecisions': {
                    '00085-CVT-031': {
                        'decision': 'merge',
                        'keepProductId': pr['id'],
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            g = next(x for x in preview['skuGroups'] if x['code'] == '00085-CVT-031')
            self.assertEqual(g['plannedPrimarySupplier'], 'トムス㈱')
            self.assertTrue(g['primarySupplierResolved']['primaryChangeRequiresConfirm'])
            self.assertGreater(preview['reviewCount'], 0)
            reasons = ' '.join(
                ' '.join(r.get('reasons') or []) for r in preview['rows'] if r.get('status') == 'needs_review'
            )
            self.assertIn('明示確認', reasons)
            # 確認なしでは既存のまま（コミットしない）
            fresh = c.execute('SELECT supplier_id FROM products WHERE id=?', (pr['id'],)).fetchone()
            self.assertEqual(fresh['supplier_id'], su_old['id'])
        finally:
            c.close()
            folder.cleanup()

    def test_one_candidate_with_aux_still_single_auto(self):
        """補助CSVがあっても候補1社なら候補1社ルール（補助複社判定に入らない）。"""
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,トムス㈱,00085-CVT-031,CVTパンツ,M',
            ])
            aux = tiny_opening_rows([
                opening_line('トムス㈱', '顧客A', 'CVTパンツ', '00085-CVT-031', 'M', '3'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text),
                'auxFile': csv_b64_from_text(aux),
                'name': 'l.csv', 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '00085-CVT-031')
            self.assertEqual(g['plannedPrimarySupplier'], 'トムス㈱')
            self.assertEqual(g['auxPrimary']['autoReason'], pli.SINGLE_SUPPLIER_AUTO_REASON)
            self.assertEqual(g['auxPrimary']['decisionKind'], 'single_candidate')
        finally:
            c.close()
            folder.cleanup()


class ReviewTierAndProvisionalNameTest(unittest.TestCase):
    """3ティア分類・仮代表名・部分確定（一時DBのみ）。"""

    def test_supplier_only_diff_is_bulk_approvable(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-B1,作業着,M',
                '顧客A,仕入B,SKU-B1,作業着,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'bulk.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-B1')
            self.assertTrue(g['needsDecision'])
            self.assertEqual(g['reviewTier'], 'bulk')
            self.assertTrue(g['bulkApprovable'])
            self.assertTrue(g['supplierDiff'])
            self.assertFalse(g['colorDiffPossible'])
            self.assertIn('仕入A', g['supplierCandidates'])
            self.assertIn('仕入B', g['supplierCandidates'])
            # 「内容の違い○種」だけに頼らない具体候補
            self.assertEqual(g['nameCandidates'], ['作業着'])
        finally:
            c.close()
            folder.cleanup()

    def test_color_or_processing_diff_is_individual(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-I1,帽子,F',
                '顧客A,仕入A,SKU-I1,スナッパー加工,F',
                # 明示色が異なる → 色だけでは自動確定せず個別確認
                '顧客B,仕入A,SKU-I2,パンツ ネイビー,M',
                '顧客B,仕入A,SKU-I2,パンツ レッド,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'indiv.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g1 = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-I1')
            g2 = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-I2')
            self.assertEqual(g1['reviewTier'], 'individual')
            self.assertFalse(g1['bulkApprovable'])
            self.assertTrue(g1['kindDiffPossible'] or g1['processingDiffPossible'])
            self.assertEqual(g2['reviewTier'], 'individual')
            self.assertTrue(g2['colorDiffPossible'])
            self.assertTrue(g2['needsDecision'])
            self.assertTrue(any('帽子' in n or 'スナッパー' in n for n in g1['nameCandidates']))
        finally:
            c.close()
            folder.cleanup()

    def test_provisional_name_allows_merge_without_official(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-P1,作業上着,M',
                '顧客A,仕入B,SKU-P1,作業 上着,L',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'prov.csv',
                'skuDecisions': {
                    'SKU-P1': {
                        'decision': 'merge',
                        'primarySupplier': '仕入A',
                        'primarySupplierManual': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            }
            before = stock_snap(c)
            preview = pli.build_preview(c, body)
            self.assertEqual(preview['skuDecisionPending'], 0)
            self.assertEqual(preview['reviewCount'], 0)
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-P1')
            # 空白差のみならコア一意 → 自動まとめ（名称未整理にしない）
            # 仕入先差があるため要確認ティアだが、merge 決定後の代表名にタグは埋め込まない
            planned = g.get('plannedOfficialName') or ''
            self.assertNotIn(pli.PROVISIONAL_NAME_TAG, planned)
            self.assertNotIn('［', planned)
            self.assertTrue(g.get('nameAdoptionRule') or (g.get('namePlan') or {}).get('adoptionRule'))
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-prov-1',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            row = c.execute(
                "SELECT id,name FROM products WHERE code='SKU-P1' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertNotIn(pli.PROVISIONAL_NAME_TAG, row['name'])
            self.assertNotIn('［', row['name'])
            # 後から公式名編集できる（別名に原文が残る）
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (row['id'],),
                )
            }
            self.assertTrue(aliases & {'作業上着', '作業 上着'} or '作業上着' in row['name'])
            stock.save_product(c, {
                **{k: stock.product_row(c, row['id'])[k] for k in ()},
                'id': row['id'],
                'code': 'SKU-P1',
                'name': '作業上着（整理済）',
                'color': '',
                'category': '',
                'supplierId': stock.product_row(c, row['id'])['supplierId'],
                'subSupplierIds': stock.product_row(c, row['id']).get('subSupplierIds') or [],
                'sizes': stock.product_row(c, row['id'])['sizes'],
                'active': True,
                'links': [
                    {
                        'customerId': link['customerId'],
                        'embroidery': link['embroidery'],
                        'isStandard': link['isStandard'],
                        'active': link['active'],
                    }
                    for link in stock.product_row(c, row['id'])['links']
                ],
            })
            c.commit()
            edited = c.execute('SELECT name FROM products WHERE id=?', (row['id'],)).fetchone()
            self.assertEqual(edited['name'], '作業上着（整理済）')
        finally:
            c.close()
            folder.cleanup()

    def test_provisional_does_not_overwrite_existing_official_name(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入A', 'active': True})
            pr = stock.save_product(c, {
                'code': 'SKU-E1', 'name': '既存公式名', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': False, 'active': True}],
            })
            c.commit()
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-E1,別名A,M',
                '顧客A,仕入B,SKU-E1,別名B,L',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'keep.csv',
                'skuDecisions': {
                    'SKU-E1': {
                        'decision': 'merge',
                        'keepProductId': pr['id'],
                        'primarySupplier': '仕入A',
                        'primarySupplierManual': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-keep-1',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            name = c.execute('SELECT name FROM products WHERE id=?', (pr['id'],)).fetchone()['name']
            self.assertEqual(name, '既存公式名')
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (pr['id'],),
                )
            }
            self.assertTrue({'別名A', '別名B'} & aliases)
        finally:
            c.close()
            folder.cleanup()

    def test_partial_commit_hold_then_reimport_no_dupes(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-H1,作業着,M',
                '顧客A,仕入B,SKU-H1,作業着,L',
                '顧客A,仕入A,SKU-H2,帽子,F',
                '顧客A,仕入A,SKU-H2,スナッパー加工,F',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'partial.csv',
                'skuDecisions': {
                    'SKU-H1': {
                        'decision': 'merge',
                        'primarySupplier': '仕入A',
                        'primarySupplierManual': True,
                    },
                    'SKU-H2': {'decision': 'hold', 'deferReason': '加工切り分け未確定'},
                },
                '_canWrite': True, '_actor': {'name': 't'},
            }
            before = stock_snap(c)
            preview = pli.build_preview(c, body)
            self.assertEqual(preview['skuDecisionPending'], 0)
            self.assertGreaterEqual(preview['plannedCounts']['rowsDeferred'], 1)
            defer_reasons = preview['plannedCounts'].get('deferredReasons') or []
            self.assertTrue(any(d.get('code') == 'SKU-H2' for d in defer_reasons))
            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-partial-1',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            n1 = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='SKU-H1' AND merged_into_id IS NULL"
            ).fetchone()['n']
            n2 = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='SKU-H2' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(n1, 1)
            self.assertEqual(n2, 0)

            # 保留分を後からまとめて再取込（二重登録なし）
            body2 = {
                'file': csv_b64_from_text(text), 'name': 'partial.csv',
                'skuDecisions': {
                    'SKU-H1': {
                        'decision': 'merge',
                        'primarySupplier': '仕入A',
                        'primarySupplierManual': True,
                    },
                    'SKU-H2': {
                        'decision': 'merge',
                        'officialName': '帽子（整理）',
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview2 = pli.build_preview(c, body2)
            result2 = pli.commit_preview(c, {
                **body2,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'pl-partial-2',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result2['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            n1b = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='SKU-H1' AND merged_into_id IS NULL"
            ).fetchone()['n']
            n2b = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='SKU-H2' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(n1b, 1)
            self.assertEqual(n2b, 1)
        finally:
            c.close()
            folder.cleanup()

    def test_size_auto_merge_still_auto_tier(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-A1,作業着,M',
                '顧客A,仕入A,SKU-A1,作業着,L',
                '顧客B,仕入A,SKU-A1,＊作業着,S',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'auto.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-A1')
            self.assertFalse(g['needsDecision'])
            self.assertEqual(g['reviewTier'], 'auto')
            self.assertEqual(preview['skuDecisionPending'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_synonym_and_whitespace_auto_merge(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-SYN,チノパンツ,M',
                '顧客A,仕入A,SKU-SYN,チノスラックス,L',
                '顧客B,仕入A,SKU-SYN,チノ　パンツ,S',  # 全角空白
                '顧客A,仕入A,SKU-WS,作業　上着,M',
                '顧客A,仕入A,SKU-WS,作業上着,L',
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'syn.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(stock_snap(c), before)
            g_syn = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-SYN')
            g_ws = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-WS')
            self.assertFalse(g_syn['needsDecision'])
            self.assertTrue(g_syn.get('synonymAutoMerged') or g_syn.get('whitespaceAutoMerged'))
            self.assertFalse(g_ws['needsDecision'])
            self.assertTrue(g_ws.get('whitespaceAutoMerged') or g_ws.get('plannedSingleProduct'))
            self.assertIn('outcomeSummary', preview)
            self.assertGreaterEqual(preview['outcomeSummary']['autoMergedSkuGroups'], 2)

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'syn.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-syn-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            n_syn = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='SKU-SYN' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(n_syn, 1)
            prod = c.execute(
                "SELECT id,name FROM products WHERE code='SKU-SYN' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertNotIn(pli.PROVISIONAL_NAME_TAG, prod['name'])
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (prod['id'],),
                )
            }
            # 原文表記が別名に残る（代表名以外）
            self.assertTrue(
                {'チノスラックス', 'チノ　パンツ'} & aliases
                or prod['name'] in {'チノパンツ', 'チノスラックス', 'チノ　パンツ'}
            )
            detail = stock.product_row(c, prod['id'])
            self.assertIn('nameAliases', detail)
        finally:
            c.close()
            folder.cleanup()

    def test_color_diff_still_needs_review(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-C1,青パンツ,M',
                '顧客A,仕入A,SKU-C1,赤スラックス,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'color.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-C1')
            self.assertTrue(g['needsDecision'])
            self.assertEqual(g['reviewTier'], 'individual')
            self.assertTrue(g.get('colorDiffPossible'))
        finally:
            c.close()
            folder.cleanup()

    def test_blank_sku_ledger_uses_parts_category(self):
        folder, path, c = newdb()
        try:
            # 旧制服例外ペアでも台帳取込では部品・資材
            text = tiny_ledger_rows([
                'グローヴ㈱,仕入A,,チェック柄ベスト(ムーリベッキ),F',
                '顧客X,仕入B,,ネジセット,個',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'blank.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            by_no = {r['rowNo']: r for r in preview['rows']}
            self.assertTrue(by_no[2]['product']['blankSku'])
            self.assertEqual(by_no[2]['product']['proposedCategory'], stock.CATEGORY_PARTS)
            self.assertEqual(by_no[3]['product']['proposedCategory'], stock.CATEGORY_PARTS)
            # 別商品として分かれる（全品番なしを1つに潰さない）
            self.assertNotEqual(by_no[2]['matchGroupKey'], by_no[3]['matchGroupKey'])
        finally:
            c.close()
            folder.cleanup()

    def test_aux_main_name_nonzero_pair_unique(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-N1,緑つなぎ,M',
                '顧客A,仕入B,SKU-N1,青つなぎ,L',
            ])
            aux = tiny_opening_rows([
                '新品,仕入A,顧客A,緑つなぎ,SKU-N1,M,1000,0,0,0,0,3,0,,本社,A1,0',
                '中古,仕入B,顧客A,青つなぎ,SKU-N1,L,1000,0,0,0,0,0,0,,本社,A1,0',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'l.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-N1')
            self.assertFalse(g['needsDecision'])
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_nonzero_pair')
            # 登録予定名は色振り分け後。ソース候補名は緑つなぎのまま
            self.assertEqual(aux_p.get('suggestedOfficialName'), 'つなぎ')
            self.assertEqual(aux_p.get('adoptedColor') or aux_p.get('plannedRegisteredColor'), 'グリーン')
            self.assertEqual(aux_p.get('sourceAdoptedName') or (aux_p.get('adoptedPackage') or {}).get('productName'), '緑つなぎ')
            self.assertEqual(aux_p.get('suggestedPrimarySupplier'), '仕入A')
            self.assertFalse(aux_p.get('officialNameNeedsChoice'))
            self.assertEqual(g.get('plannedOfficialName'), 'つなぎ')
            self.assertEqual(g.get('plannedColor') or g.get('plannedRegisteredColor'), 'グリーン')
            rule = aux_p.get('pairAdoptionRule') or ''
            self.assertIn('在庫が0以外の候補が1組', rule)
            self.assertIn('緑つなぎ（在庫3）を採用', rule)
            self.assertNotIn(pli.PROVISIONAL_NAME_TAG, g.get('plannedOfficialName') or '')
            self.assertTrue(g.get('auxStockPickAuto'))
            # 比較一覧用: 候補ごとの初期在庫表示
            name_details = {d['value']: d for d in (g.get('nameCandidateDetails') or [])}
            self.assertEqual(name_details.get('緑つなぎ', {}).get('auxStockDisplay'), '3')
            self.assertEqual(name_details.get('緑つなぎ', {}).get('auxStockLabel'), '初期在庫：3')
            self.assertEqual(name_details.get('青つなぎ', {}).get('auxStockDisplay'), '0')
            sup_details = {d['value']: d for d in (g.get('supplierCandidateDetails') or [])}
            self.assertEqual(sup_details.get('仕入A', {}).get('auxStockDisplay'), '3')
            self.assertEqual(sup_details.get('仕入B', {}).get('auxStockDisplay'), '0')
            pairs = {
                (p.get('productName'), p.get('supplier')): p
                for p in (aux_p.get('pairCandidates') or [])
            }
            self.assertEqual(pairs.get(('緑つなぎ', '仕入A'), {}).get('auxStockTotal'), 3)
            self.assertEqual(pairs.get(('青つなぎ', '仕入B'), {}).get('auxStockTotal'), 0)
        finally:
            c.close()
            folder.cleanup()

    def test_aux_main_name_picks_max_positive_qty(self):
        """正の在庫が複数 → 最多が一意ならその候補を採用（旧: 要確認）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-N2,名前甲,M',
                '顧客A,仕入B,SKU-N2,名前乙,L',
            ])
            aux = tiny_opening_rows([
                '新品,仕入A,顧客A,名前甲,SKU-N2,M,1000,0,0,0,0,100,0,,本社,A1,0',
                '新品,仕入B,顧客A,名前乙,SKU-N2,L,1000,0,0,0,0,1,0,,本社,A1,0',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'l.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-N2')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_max_positive_stock')
            self.assertEqual(aux_p.get('suggestedOfficialName'), '名前甲')
            self.assertEqual(aux_p.get('suggestedPrimarySupplier'), '仕入A')
            self.assertFalse(aux_p.get('officialNameNeedsChoice'))
            self.assertFalse(g['needsDecision'])
            rule = aux_p.get('pairAdoptionRule') or ''
            self.assertIn('初期在庫が最多のため採用', rule)
            self.assertIn('名前甲 100', rule)
            self.assertIn('名前乙 1', rule)
        finally:
            c.close()
            folder.cleanup()

    def test_aux_negative_only_picks_pair(self):
        """負在庫も非ゼロとして候補ペアを決める（相殺なし）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-NEG,名前甲,M',
                '顧客A,仕入B,SKU-NEG,名前乙,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '名前甲', 'SKU-NEG', 'M', '-3'),
                opening_line('仕入B', '顧客A', '名前乙', 'SKU-NEG', 'L', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'l.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-NEG')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_nonzero_pair')
            self.assertEqual(aux_p.get('suggestedOfficialName'), '名前甲')
            self.assertEqual(aux_p.get('autoPrimarySupplier'), '仕入A')
            self.assertEqual(aux_p.get('suggestedPrimarySupplier'), '仕入A')
        finally:
            c.close()
            folder.cleanup()

    def test_aux_all_zero_multi_candidate_needs_choice(self):
        """候補2組以上かつ全ゼロ → 確認（旧 aux_any_unique は使わない）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-Z0,名前甲,M',
                '顧客A,仕入B,SKU-Z0,名前乙,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '名前甲', 'SKU-Z0', 'M', '0'),
                opening_line('仕入B', '顧客A', '名前乙', 'SKU-Z0', 'L', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'l.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-Z0')
            aux_p = g.get('auxPrimary') or {}
            self.assertNotEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_any_unique')
            # 1票ずつ同数 → 最多名称でも決まらず要確認
            self.assertTrue(aux_p.get('pairNeedsChoice'))
            reasons = aux_p.get('nameReasons') or []
            self.assertTrue(
                any('同数' in r or '在庫0' in r or '非ゼロ' in r for r in reasons),
                reasons,
            )
            self.assertTrue(any('要確認' in r for r in reasons), reasons)
            self.assertTrue(g['needsDecision'])
            votes = aux_p.get('ledgerNameVotes') or g.get('ledgerNameVotes') or []
            self.assertEqual(len(votes), 2)
            self.assertEqual({int(v.get('count') or 0) for v in votes}, {1})
            for d in g.get('nameCandidateDetails') or []:
                self.assertEqual(d.get('auxStockDisplay'), '0')
                self.assertEqual(d.get('auxStockLabel'), '初期在庫：0')
        finally:
            c.close()
            folder.cleanup()

    def test_aux_single_candidate_stock_zero_adopts(self):
        """候補1組は在庫0でもフルパッケージ採用。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-S0,ポロシャツ（ネイビー）,M',
                '顧客A,仕入A,SKU-S0,＊ポロシャツ（ネイビー）,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'ポロシャツ（ネイビー）', 'SKU-S0', 'M', '0'),
                opening_line('仕入A', '顧客A', '＊ポロシャツ（ネイビー）', 'SKU-S0', 'L', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'l.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-S0')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_single_candidate')
            self.assertFalse(aux_p.get('pairNeedsChoice'))
            self.assertFalse(g['needsDecision'])
            self.assertEqual(aux_p.get('adoptedColor') or (aux_p.get('adoptedPackage') or {}).get('color'), 'ネイビー')
            rule = aux_p.get('pairAdoptionRule') or ''
            self.assertIn('候補が1組', rule)
            self.assertIn('初期在庫：0', rule)
        finally:
            c.close()
            folder.cleanup()

    def test_aux_unique_stock_clears_color_processing_review(self):
        """色・加工が候補間で違っても、非ゼロ在庫が1組なら自動採用（要確認に残さない）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-CP,パンツ（ネイビー）,M',
                '顧客A,仕入B,SKU-CP,パンツ（赤・背中プリント）,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'パンツ（ネイビー）', 'SKU-CP', 'M', '4'),
                opening_line('仕入B', '顧客A', 'パンツ（赤・背中プリント）', 'SKU-CP', 'L', '0'),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'l.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-CP')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_nonzero_pair')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertTrue(g.get('auxStockPickAuto'))
            self.assertEqual(g.get('reviewTier'), 'auto')
            self.assertFalse(g.get('colorDiffPossible'))
            self.assertFalse(g.get('processingDiffPossible'))
            self.assertFalse(g.get('kindDiffPossible'))
            self.assertIn('在庫が0以外の候補が1組', g.get('auxStockPickReason') or '')
            self.assertIn('パンツ（ネイビー）（在庫4）を採用', g.get('auxStockPickReason') or '')
            self.assertEqual(aux_p.get('adoptedColor'), 'ネイビー')
            self.assertEqual(aux_p.get('suggestedOfficialName'), 'パンツ')
            self.assertEqual(aux_p.get('suggestedPrimarySupplier'), '仕入A')
            kept = aux_p.get('keptPackages') or []
            self.assertTrue(any('赤' in (k.get('color') or '') or 'プリント' in (k.get('processing') or '') for k in kept))
            # プレビュー＝コミット（未解決なし・まとめ操作不要）
            self.assertEqual(preview['skuDecisionPending'], 0)
            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'l.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-cp-stock-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            prod = c.execute(
                "SELECT id,name,color,material FROM products WHERE code='SKU-CP' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertIsNotNone(prod)
            self.assertEqual(prod['name'], 'パンツ')
            self.assertEqual(prod['color'], 'ネイビー')
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (prod['id'],),
                )
            }
            self.assertTrue(
                any('背中プリント' in a or 'パンツ（赤' in a for a in aliases)
                or 'パンツ（赤・背中プリント）' in aliases
            )
            maps = list(c.execute(
                'SELECT csv_product_name,csv_color,csv_processing,is_main_candidate,spec_json '
                'FROM product_ledger_row_maps WHERE product_id=? ORDER BY source_row_no',
                (prod['id'],),
            ))
            self.assertGreaterEqual(len(maps), 2)
            main_rows = [m for m in maps if m['is_main_candidate']]
            other_rows = [m for m in maps if not m['is_main_candidate']]
            self.assertTrue(main_rows)
            self.assertTrue(other_rows)
            self.assertTrue(any(
                '背中プリント' in (m['csv_processing'] or '') or '赤' in (m['csv_color'] or '')
                for m in other_rows
            ))
            # 後続の初期在庫CSV: 非メイン別名でも同一商品へ照合。商品色はメインのまま上書きしない
            import legacy_csv_import as legacy
            opening = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'パンツ（赤・背中プリント）', 'SKU-CP', 'L', '2'),
            ])
            stock.ensure_unconfirmed_site(c)
            c.commit()
            color_before = prod['color']
            obody = {
                'file': csv_b64_from_text(opening), 'name': 'open.csv',
                'basisDate': '2026-10-01', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            oprev = legacy.build_preview(c, obody)
            for r in oprev['rows']:
                self.assertEqual(r['product']['id'], prod['id'])
                self.assertIn(r['status'], ('create', 'reuse', 'size_add'))
            legacy.commit_preview(c, {
                **obody,
                'previewFingerprint': oprev['previewFingerprint'],
                'previewAt': oprev['previewAt'],
                'token': 'leg-cp-open-1',
            }, db_path=str(path))
            c.commit()
            prod2 = c.execute('SELECT color,name FROM products WHERE id=?', (prod['id'],)).fetchone()
            self.assertEqual(prod2['color'], color_before)
            self.assertEqual(prod2['name'], 'パンツ')
            # 非メイン原文の色・加工は row_map に残る（メイン仕様へ書き換えない）
            other_after = list(c.execute(
                'SELECT csv_product_name,csv_color,csv_processing,spec_json FROM product_ledger_row_maps '
                'WHERE product_id=? AND is_main_candidate=0',
                (prod['id'],),
            ))
            self.assertTrue(any(
                '背中プリント' in (m['csv_processing'] or '') or '赤' in (m['csv_color'] or '')
                for m in other_after
            ))
            self.assertTrue(any('赤・背中プリント' in (m['csv_product_name'] or '') for m in other_after))
        finally:
            c.close()
            folder.cleanup()

    def test_aux_positive_beats_negative_without_netting(self):
        """正2・負-1 → 正の候補を採用（負と相殺せず、絶対値でも選ばない）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-M2,名前甲（赤）,M',
                '顧客A,仕入B,SKU-M2,名前乙（青）,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '名前甲（赤）', 'SKU-M2', 'M', '2'),
                opening_line('仕入B', '顧客A', '名前乙（青）', 'SKU-M2', 'L', '-1'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'l.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-M2')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_nonzero_pair')
            self.assertEqual(aux_p.get('suggestedOfficialName'), '名前甲')
            self.assertFalse(aux_p.get('pairNeedsChoice'))
            self.assertFalse(g['needsDecision'])
        finally:
            c.close()
            folder.cleanup()

    def test_custom_order_size_only_merges(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '病院A,仕入X,別注,青帽子,S',
                '病院A,仕入X,別注,青帽子,M',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'bespoke.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            by_no = {r['rowNo']: r for r in preview['rows']}
            self.assertEqual(by_no[2]['status'], 'create')
            # 2行目は同一別注商品へのサイズ追加予定（要確認にしない）
            self.assertIn(by_no[3]['status'], ('create', 'size_add', 'reuse'))
            self.assertEqual(by_no[2]['matchGroupKey'], by_no[3]['matchGroupKey'])
            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'bespoke.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-bespoke-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            n = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE code='別注' AND merged_into_id IS NULL"
            ).fetchone()['n']
            self.assertEqual(n, 1)
            sizes = {
                r['label'] for r in c.execute(
                    '''SELECT ps.label FROM product_sizes ps
                       JOIN products p ON p.id=ps.product_id
                       WHERE p.code='別注' AND p.merged_into_id IS NULL'''
                )
            }
            self.assertEqual(sizes, {'S', 'M'})
        finally:
            c.close()
            folder.cleanup()

    def test_ui_shows_aliases_and_outcome_summary(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('nameAliases', app)
        self.assertIn('照合用別名', app)
        self.assertIn('outcomeSummary', js)
        self.assertIn('取込判断の件数', js)
        self.assertIn('名称未整理', js)
        self.assertIn('名前には埋め込みません', js)
        self.assertIn('rowClassifications', js)
        self.assertIn('括弧分類', js)
        self.assertIn('生地・素材', app)
        self.assertIn('material', app)
        self.assertIn('初期在庫が最多のため', js)
        self.assertIn('auxStockPickAuto', js)
        self.assertIn('keptPackages', js)
        self.assertIn('nameCandidateDetails', js)
        self.assertIn('supplierCandidateDetails', js)
        self.assertIn('初期在庫：', js)
        self.assertIn('sku-cand-stock', js)
        self.assertIn('自動決定済み（未登録）', js)
        self.assertIn('全候補が確認済み在庫0', js)
        self.assertIn('ledgerNameVotes', js)
        py = (ROOT / 'product_ledger_import.py').read_text(encoding='utf-8')
        self.assertIn('対象標記の有無', py)


class MaxPositiveStockPickTest(unittest.TestCase):
    """正の初期在庫合計が最多の候補をメイン採用（1-002 型）。本番DBは触らない。"""

    def test_sku_1_002_adopts_highest_positive(self):
        """女性用半袖白衣 44／女性半袖調理衣 36 → 白衣側をフルパッケージ採用。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,1-002,女性用半袖白衣,M',
                '顧客A,仕入A,1-002,女性半袖調理衣,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '女性用半袖白衣', '1-002', 'M', '44'),
                opening_line('仕入A', '顧客A', '女性半袖調理衣', '1-002', 'L', '36'),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '1-002.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(stock_snap(c), before)
            g = next(x for x in preview['skuGroups'] if x['code'] == '1-002')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_max_positive_stock')
            self.assertEqual(aux_p.get('suggestedOfficialName'), '女性用半袖白衣')
            self.assertEqual(aux_p.get('suggestedPrimarySupplier'), '仕入A')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertTrue(g.get('auxStockPickAuto'))
            self.assertEqual(g.get('reviewTier'), 'auto')
            rule = aux_p.get('pairAdoptionRule') or g.get('auxStockPickReason') or ''
            self.assertIn('初期在庫が最多のため採用', rule)
            self.assertIn('女性用半袖白衣 44', rule)
            self.assertIn('女性半袖調理衣 36', rule)
            # 対象標記の有無だけでは要確認に残さない
            self.assertFalse(g.get('genderDiffPossible'))
            self.assertFalse(g.get('genderConflictPossible'))
            kept = aux_p.get('keptPackages') or []
            self.assertTrue(any('調理衣' in (k.get('productName') or '') for k in kept))
        finally:
            c.close()
            folder.cleanup()

    def test_alias_double_match_does_not_double_count(self):
        """同一補助行が標記あり／なし別名に当たっても行番号で1回だけ合算。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-DD,緑つなぎ,M',
                '顧客A,仕入A,SKU-DD,＊緑つなぎ,L',
                '顧客A,仕入B,SKU-DD,青つなぎ,M',
            ])
            # 補助CSVは標記なし1行のみ（qty=44）。別名＊が同じ行に当たっても 44+44 にしない
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '緑つなぎ', 'SKU-DD', 'M', '44'),
                opening_line('仕入B', '顧客A', '青つなぎ', 'SKU-DD', 'M', '36'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'dd.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-DD')
            aux_p = g.get('auxPrimary') or {}
            pairs = {
                (p.get('productName'), p.get('supplier')): p
                for p in (aux_p.get('pairCandidates') or [])
            }
            green = pairs.get(('緑つなぎ', '仕入A')) or {}
            self.assertEqual(green.get('auxStockTotal'), 44)
            self.assertEqual(green.get('auxPositiveStockTotal'), 44)
            self.assertEqual(aux_p.get('suggestedOfficialName'), 'つなぎ')
            self.assertEqual(aux_p.get('adoptedColor'), 'グリーン')
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_max_positive_stock')
            rule = aux_p.get('pairAdoptionRule') or ''
            self.assertIn('緑つなぎ 44', rule)
            self.assertNotIn('88', rule)
        finally:
            c.close()
            folder.cleanup()

    def test_positive_tie_needs_review(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-TIE,名前甲,M',
                '顧客A,仕入B,SKU-TIE,名前乙,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '名前甲', 'SKU-TIE', 'M', '10'),
                opening_line('仕入B', '顧客A', '名前乙', 'SKU-TIE', 'L', '10'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'tie.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-TIE')
            aux_p = g.get('auxPrimary') or {}
            self.assertTrue(g['needsDecision'])
            self.assertTrue(aux_p.get('pairNeedsChoice'))
            self.assertEqual(aux_p.get('suggestedOfficialName') or '', '')
            reasons = aux_p.get('nameReasons') or []
            self.assertTrue(any('同数' in r for r in reasons), reasons)
            self.assertTrue(any('手動' in r for r in reasons), reasons)
            review_labels = [r.get('label') or '' for r in (g.get('reviewReasons') or [])]
            self.assertTrue(any('同数' in lab for lab in review_labels), review_labels)
        finally:
            c.close()
            folder.cleanup()

    def test_multi_negative_needs_review_not_by_abs(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-NN,名前甲,M',
                '顧客A,仕入B,SKU-NN,名前乙,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '名前甲', 'SKU-NN', 'M', '-8'),
                opening_line('仕入B', '顧客A', '名前乙', 'SKU-NN', 'L', '-2'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'nn.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-NN')
            aux_p = g.get('auxPrimary') or {}
            self.assertTrue(g['needsDecision'])
            self.assertTrue(aux_p.get('pairNeedsChoice'))
            self.assertEqual(aux_p.get('suggestedOfficialName') or '', '')
            reasons = aux_p.get('nameReasons') or []
            self.assertTrue(any('負の在庫' in r and '絶対値' in r for r in reasons), reasons)
        finally:
            c.close()
            folder.cleanup()

    def test_positive_qty_not_netted_against_negative_sizes(self):
        """同一候補内の負在庫は正の合計から差し引かない（最多比較は正の合算）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-NET,名前甲,M',
                '顧客A,仕入B,SKU-NET,名前乙,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '名前甲', 'SKU-NET', 'M', '50'),
                opening_line('仕入A', '顧客A', '名前甲', 'SKU-NET', 'S', '-20'),
                opening_line('仕入B', '顧客A', '名前乙', 'SKU-NET', 'L', '40'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'net.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-NET')
            aux_p = g.get('auxPrimary') or {}
            # 正の合算 50 vs 40 → 甲。ネット 30 vs 40 では選ばない
            self.assertEqual(aux_p.get('suggestedOfficialName'), '名前甲')
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_max_positive_stock')
            pairs = {
                (p.get('productName'), p.get('supplier')): p
                for p in (aux_p.get('pairCandidates') or [])
            }
            self.assertEqual(pairs.get(('名前甲', '仕入A'), {}).get('auxPositiveStockTotal'), 50)
            self.assertEqual(pairs.get(('名前甲', '仕入A'), {}).get('auxStockTotal'), 30)
        finally:
            c.close()
            folder.cleanup()

    def test_used_marker_only_not_listed_as_color_diff_values(self):
        """＊だけの差を色差の証拠値に並べない（白衣／＊白衣）。"""
        variants = [
            {'csvNames': ['白衣', '＊白衣'], 'productName': '白衣', 'supplier': '仕入A'},
            {'csvNames': ['調理衣'], 'productName': '調理衣', 'supplier': '仕入A'},
        ]
        meta = pli.analyze_sku_review_diffs(variants)
        color_values = []
        for r in meta.get('reviewReasons') or []:
            if r.get('code') == 'color':
                color_values = list(r.get('values') or [])
        self.assertNotIn('＊白衣', color_values)
        if color_values:
            self.assertIn('白衣', color_values)


class UniqueNonzeroKindDiffStockPickTest(unittest.TestCase):
    """非ゼロ在庫が一意なら商品種差だけでは要確認に残さない（409-60 型）。"""

    def test_sku_409_60_kind_diff_cleared_by_unique_stock(self):
        """コックシャツ0／コックコート0／ストレッチコックコート8 → 自動決定済み。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,409-60,コックシャツ,M',
                '顧客A,仕入A,409-60,コックコート（社員）,L',
                '顧客A,仕入A,409-60,ストレッチコックコート,LL',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'コックシャツ', '409-60', 'M', '0'),
                opening_line('仕入A', '顧客A', 'コックコート（社員）', '409-60', 'L', '0'),
                opening_line('仕入A', '顧客A', 'ストレッチコックコート', '409-60', 'LL', '8'),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '409-60.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(stock_snap(c), before)

            g = next(x for x in preview['skuGroups'] if x['code'] == '409-60')
            # 商品種ヒント（シャツ≠コート）は検出されるが、在庫一意でクリア
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_nonzero_pair')
            self.assertEqual(aux_p.get('suggestedOfficialName'), 'ストレッチコックコート')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertTrue(g.get('auxStockPickAuto'))
            self.assertEqual(g.get('reviewTier'), 'auto')
            self.assertFalse(g.get('kindDiffPossible'))
            self.assertFalse(g.get('nameWordingDiff'))
            self.assertEqual(g.get('plannedOfficialName'), 'ストレッチコックコート')
            reason = g.get('auxStockPickReason') or aux_p.get('pairAdoptionRule') or ''
            self.assertIn('在庫が0以外の候補が1組', reason)
            self.assertIn('ストレッチコックコート（在庫8）を採用', reason)
            reason_codes = {r.get('code') for r in (g.get('reviewReasons') or [])}
            reason_labels = [r.get('label') or '' for r in (g.get('reviewReasons') or [])]
            self.assertIn('aux_stock_pick', reason_codes)
            self.assertNotIn('kind', reason_codes)
            self.assertFalse(any('商品種' in lab for lab in reason_labels), reason_labels)
            self.assertFalse(any('個別確認' in lab for lab in reason_labels), reason_labels)
            self.assertEqual(preview['skuDecisionPending'], 0)
            # 未決定／個別確認リストに出ない
            pending = [x for x in preview['skuGroups'] if x.get('needsDecision')]
            self.assertFalse(any(x['code'] == '409-60' for x in pending))
            individual = [
                x for x in preview['skuGroups']
                if x.get('needsDecision') and x.get('reviewTier') == 'individual'
            ]
            self.assertFalse(any(x['code'] == '409-60' for x in individual))

            kept_names = []
            for k in (aux_p.get('keptPackages') or []):
                for n in k.get('ledgerNames') or ([k.get('productName')] if k.get('productName') else []):
                    if n:
                        kept_names.append(n)
            self.assertTrue(
                {'コックシャツ', 'コックコート（社員）'} & set(kept_names)
                or any(n in ('コックシャツ', 'コックコート（社員）') for n in kept_names)
            )

            # プレビュー＝コミット同一判断。まとめ操作不要。本番在庫は不変
            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '409-60.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-409-60-kind-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            prod = c.execute(
                "SELECT id,name FROM products WHERE code='409-60' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertIsNotNone(prod)
            self.assertEqual(prod['name'], 'ストレッチコックコート')
        finally:
            c.close()
            folder.cleanup()

    def test_kind_diff_negative_only_unique_also_clears(self):
        """負在庫のみが一意でも商品種差だけでは要確認に残さない。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-KN,コックシャツ,M',
                '顧客A,仕入A,SKU-KN,ストレッチコックコート,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'コックシャツ', 'SKU-KN', 'M', '0'),
                opening_line('仕入A', '顧客A', 'ストレッチコックコート', 'SKU-KN', 'L', '-2'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'kn.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-KN')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertTrue(g.get('auxStockPickAuto'))
            self.assertFalse(g.get('kindDiffPossible'))
            self.assertEqual(g.get('plannedOfficialName'), 'ストレッチコックコート')
            reason = g.get('auxStockPickReason') or ''
            self.assertIn('ストレッチコックコート（在庫-2）を採用', reason)
        finally:
            c.close()
            folder.cleanup()

    def test_kind_diff_multi_positive_max_still_clears(self):
        """正の在庫が複数でも最多が一意なら種差だけでは要確認に残さない。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-KM,コックシャツ,M',
                '顧客A,仕入A,SKU-KM,ストレッチコックコート,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'コックシャツ', 'SKU-KM', 'M', '3'),
                opening_line('仕入A', '顧客A', 'ストレッチコックコート', 'SKU-KM', 'L', '10'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'km.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-KM')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_max_positive_stock')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertTrue(g.get('auxStockPickAuto'))
            self.assertFalse(g.get('kindDiffPossible'))
            self.assertEqual(g.get('plannedOfficialName'), 'ストレッチコックコート')
        finally:
            c.close()
            folder.cleanup()

    def test_kind_diff_positive_tie_still_needs_review(self):
        """正の在庫同数タイは種差の有無に関わらず要確認。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-KT,コックシャツ,M',
                '顧客A,仕入A,SKU-KT,ストレッチコックコート,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'コックシャツ', 'SKU-KT', 'M', '5'),
                opening_line('仕入A', '顧客A', 'ストレッチコックコート', 'SKU-KT', 'L', '5'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'kt.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-KT')
            self.assertTrue(g['needsDecision'])
            self.assertFalse(g.get('auxStockPickAuto'))
            aux_p = g.get('auxPrimary') or {}
            self.assertTrue(aux_p.get('pairNeedsChoice') or aux_p.get('officialNameNeedsChoice'))
        finally:
            c.close()
            folder.cleanup()

    def test_kind_diff_without_aux_still_individual(self):
        """補助在庫なしのとき、商品種差は従来どおり個別確認。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-KO,コックシャツ,M',
                '顧客A,仕入A,SKU-KO,ストレッチコックコート,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'ko.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-KO')
            self.assertTrue(g['needsDecision'])
            self.assertTrue(g.get('kindDiffPossible'))
            self.assertEqual(g.get('reviewTier'), 'individual')
        finally:
            c.close()
            folder.cleanup()


class GenderPresenceAbsenceStockPickTest(unittest.TestCase):
    """在庫一意採用時、「女性用」有無だけでは要確認に残さない（1-001 型）。"""

    def test_sku_1_001_gender_presence_cleared_by_stock(self):
        """在庫22の「女性用長袖白衣」をメイン採用。白衣／長袖白衣／調理衣の有無差だけでは未決定にしない。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,1-001,女性用長袖白衣,M',
                '顧客A,仕入A,1-001,白衣,L',
                '顧客A,仕入A,1-001,長袖白衣,LL',
                '顧客A,仕入A,1-001,調理衣,S',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '女性用長袖白衣', '1-001', 'M', '22'),
                opening_line('仕入A', '顧客A', '白衣', '1-001', 'L', '0'),
                opening_line('仕入A', '顧客A', '長袖白衣', '1-001', 'LL', '0'),
                opening_line('仕入A', '顧客A', '調理衣', '1-001', 'S', '0'),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '1-001.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            # プレビューだけ：本番DB相当の在庫は触らない
            self.assertEqual(stock_snap(c), before)

            g = next(x for x in preview['skuGroups'] if x['code'] == '1-001')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_nonzero_pair')
            self.assertEqual(aux_p.get('suggestedOfficialName'), '女性用長袖白衣')
            self.assertIn('女性用', aux_p.get('suggestedOfficialName') or '')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertTrue(g.get('auxStockPickAuto'))
            self.assertEqual(g.get('reviewTier'), 'auto')
            self.assertEqual(g.get('reviewTierLabel'), '自動まとめ')
            self.assertFalse(g.get('genderDiffPossible'))
            self.assertFalse(g.get('genderConflictPossible'))
            self.assertFalse(g.get('nameWordingDiff'))
            self.assertEqual(g.get('plannedOfficialName'), '女性用長袖白衣')
            reason_codes = {r.get('code') for r in (g.get('reviewReasons') or [])}
            reason_labels = [r.get('label') or '' for r in (g.get('reviewReasons') or [])]
            self.assertIn('aux_stock_pick', reason_codes)
            self.assertNotIn('gender', reason_codes)
            self.assertNotIn('name_wording', reason_codes)
            self.assertFalse(any('男女・対象' in lab for lab in reason_labels), reason_labels)
            self.assertFalse(any('個別確認' in lab for lab in reason_labels), reason_labels)
            self.assertEqual(preview['skuDecisionPending'], 0)
            note = preview.get('note') or ''
            self.assertTrue('プレビュー' in note or '登録ボタンを押すまで' in note)

            # 別名候補が kept に残る
            kept_names = []
            for k in (aux_p.get('keptPackages') or []):
                for n in k.get('ledgerNames') or ([k.get('productName')] if k.get('productName') else []):
                    if n:
                        kept_names.append(n)
            self.assertTrue(
                {'白衣', '長袖白衣', '調理衣'} & set(kept_names)
                or any(n in ('白衣', '長袖白衣', '調理衣') for n in kept_names)
            )
        finally:
            c.close()
            folder.cleanup()

    def test_male_female_conflict_still_needs_review_with_unique_stock(self):
        """男性用と女性用が明示対立するときは在庫一意でも要確認のまま。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-GF,女性用白衣,M',
                '顧客A,仕入B,SKU-GF,男性用白衣,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '女性用白衣', 'SKU-GF', 'M', '5'),
                opening_line('仕入B', '顧客A', '男性用白衣', 'SKU-GF', 'L', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'gf.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-GF')
            self.assertTrue(g['needsDecision'])
            self.assertTrue(g.get('genderConflictPossible'))
            self.assertTrue(g.get('genderDiffPossible'))
            self.assertFalse(g.get('auxStockPickAuto'))
            self.assertEqual(g.get('reviewTier'), 'individual')
            labels = [r.get('label') or '' for r in (g.get('reviewReasons') or [])]
            self.assertTrue(
                any('明示対立' in lab or '男女・対象' in lab for lab in labels),
                labels,
            )
            # メイン候補の提案自体はあるが自動クリアしない
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_nonzero_pair')
            self.assertEqual(aux_p.get('suggestedOfficialName'), '女性用白衣')
        finally:
            c.close()
            folder.cleanup()

    def test_gender_presence_without_aux_still_individual(self):
        """補助在庫なしのとき、対象標記の有無は従来どおり個別確認。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-GP,女性用長袖白衣,M',
                '顧客A,仕入A,SKU-GP,白衣,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'gp.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-GP')
            self.assertTrue(g['needsDecision'])
            self.assertTrue(g.get('genderDiffPossible'))
            self.assertFalse(g.get('genderConflictPossible'))
            self.assertEqual(g.get('reviewTier'), 'individual')
        finally:
            c.close()
            folder.cleanup()


class PantsHemProcessingIgnoreTest(unittest.TestCase):
    """パンツ一般『丈加工』は統合判定・加工欄から除外（股下寸法などは残す）。"""

    def test_classify_strips_hem_for_pants_only(self):
        import product_ledger_paren as pp
        cases = [
            ('黒パンツ（丈加工）', '黒パンツ', ''),
            ('黒パンツ(丈加工)', '黒パンツ', ''),
            ('ワークパンツ（丈 加 工）', 'ワークパンツ', ''),
            ('スラックス（丈加工）', 'スラックス', ''),
            ('ズボン（丈加工）', 'ズボン', ''),
            ('黒パンツ 丈加工', '黒パンツ', ''),
            ('黒パンツ（丈加工・刺繍）', '黒パンツ', '刺繍'),
            ('黒パンツ（刺繍）', '黒パンツ', '刺繍'),
            ('黒パンツ（股下72）', '黒パンツ（股下72）', ''),
            ('黒パンツ（仕上がり丈70）', '黒パンツ（仕上がり丈70）', ''),
            # 非パンツは従来どおり加工へ
            ('シャツ（丈加工）', 'シャツ', '丈加工'),
        ]
        for original, planned, proc in cases:
            with self.subTest(original=original):
                r = pp.classify_product_name(original)
                self.assertEqual(r['plannedName'], planned, r)
                self.assertEqual(r['processing'], proc, r)
                self.assertEqual(r['originalName'], original)

    def test_core_key_ignores_pants_hem(self):
        self.assertEqual(
            pli.product_name_core_key('黒パンツ（丈加工）'),
            pli.product_name_core_key('黒パンツ'),
        )
        self.assertEqual(
            pli.product_name_core_key('黒パンツ（丈加工・刺繍）'),
            pli.product_name_core_key('黒パンツ（刺繍）'),
        )
        # 具体股下寸法は同一視しない
        self.assertNotEqual(
            pli.product_name_core_key('黒パンツ（股下72）'),
            pli.product_name_core_key('黒パンツ（股下74）'),
        )
        self.assertNotEqual(
            pli.product_name_core_key('黒パンツ（股下72）'),
            pli.product_name_core_key('黒パンツ'),
        )

    def test_hem_presence_alone_auto_merges(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-HEM,黒パンツ（丈加工）,M',
                '顧客A,仕入A,SKU-HEM,黒パンツ,L',
                '顧客A,仕入A,SKU-HEM2,黒パンツ（丈加工・刺繍）,M',
                '顧客A,仕入A,SKU-HEM2,黒パンツ（刺繍）,L',
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'hem.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(stock_snap(c), before)
            g1 = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-HEM')
            g2 = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-HEM2')
            self.assertFalse(g1['needsDecision'], g1.get('reviewReasons'))
            self.assertTrue(g1.get('pantsHemAutoMerged') or g1.get('plannedSingleProduct'))
            self.assertFalse(g1.get('processingDiffPossible'))
            self.assertFalse(g2['needsDecision'], g2.get('reviewReasons'))
            self.assertFalse(g2.get('processingDiffPossible'))

            by_no = {r['rowNo']: r for r in preview['rows']}
            pc_hem = by_no[2]['parenClassification']
            self.assertEqual(pc_hem['processing'], '')
            self.assertNotIn('丈加工', pc_hem.get('plannedName') or '')
            pc_mix = by_no[4]['parenClassification']
            self.assertEqual(pc_mix['processing'], '刺繍')
            self.assertEqual(by_no[4]['handle'].get('embroidery'), '刺繍')
            self.assertEqual(by_no[5]['handle'].get('embroidery'), '刺繍')

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'hem.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-hem-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)

            prod = c.execute(
                "SELECT id,name FROM products WHERE code='SKU-HEM' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertNotIn('丈加工', prod['name'])
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (prod['id'],),
                )
            }
            # 原文（丈加工付き）は照合用別名に残る
            self.assertTrue(
                '黒パンツ（丈加工）' in aliases or '黒パンツ（丈加工）' == prod['name']
                or any('丈加工' in a for a in aliases),
                aliases,
            )

            emb_prod = c.execute(
                "SELECT id FROM products WHERE code='SKU-HEM2' AND merged_into_id IS NULL"
            ).fetchone()
            handles = list(c.execute(
                "SELECT embroidery FROM customer_products WHERE product_id=?",
                (emb_prod['id'],),
            ))
            self.assertTrue(any((h['embroidery'] or '') == '刺繍' for h in handles), handles)
            self.assertFalse(any('丈加工' in (h['embroidery'] or '') for h in handles), handles)
        finally:
            c.close()
            folder.cleanup()

    def test_other_processing_and_supplier_still_review(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-HX,黒パンツ（丈加工）,M',
                '顧客A,仕入A,SKU-HX,黒パンツ（刺繍）,L',
                '顧客A,仕入A,SKU-HS,黒パンツ（丈加工）,M',
                '顧客B,仕入B,SKU-HS,黒パンツ,L',
                '顧客A,仕入A,SKU-HI,黒パンツ（股下72）,M',
                '顧客A,仕入A,SKU-HI,黒パンツ（股下74）,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'hem-rev.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            gx = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-HX')
            gs = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-HS')
            gi = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-HI')
            self.assertTrue(gx['needsDecision'])
            self.assertTrue(gx.get('processingDiffPossible'))
            self.assertTrue(gs['needsDecision'])
            self.assertTrue(gs.get('supplierDiff'))
            self.assertFalse(gs.get('processingDiffPossible'))
            self.assertTrue(gi['needsDecision'])
            self.assertTrue(gi.get('nameWordingDiff'))
        finally:
            c.close()
            folder.cleanup()

    def test_opening_stock_matches_hem_alias_name(self):
        """登録済み補正名へ、丈加工付き旧名称の初期在庫行が照合できる。"""
        folder, path, c = newdb()
        try:
            # 先頭色語を避け、丈加工補正だけを見る
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-HO,ワークパンツ（丈加工）,M',
                '顧客A,仕入A,SKU-HO,ワークパンツ,L',
            ])
            opening = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'ワークパンツ（丈加工）', 'SKU-HO', 'M', '3'),
                opening_line('仕入A', '顧客A', 'ワークパンツ', 'SKU-HO', 'L', '2'),
                # 具体股下は別物（この補正だけでは混同しない）
                opening_line('仕入A', '顧客A', 'ワークパンツ（股下72）', 'SKU-HO', 'S', '9'),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'hem-ledger.csv',
                'auxFile': csv_b64_from_text(opening), 'auxName': 'open-hem.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(stock_snap(c), before)
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-HO')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            aux = g.get('auxPrimary') or {}
            official = (
                aux.get('suggestedOfficialName')
                or g.get('plannedOfficialName')
                or (g.get('productPlan') or {}).get('productName')
                or ''
            )
            self.assertTrue(official)
            self.assertNotIn('丈加工', official)
            self.assertIn('パンツ', official)

            # 初期在庫の丈加工付き名も同一コアへ照合
            self.assertTrue(pli._aux_row_matches_candidate(
                {'商品コード': 'SKU-HO', '仕入先': '仕入A', '商品名': 'ワークパンツ（丈加工）', '顧客': '顧客A'},
                code='SKU-HO', supplier='仕入A', product_name=official, customers=['顧客A'],
            ))
            self.assertTrue(pli._aux_row_matches_candidate(
                {'商品コード': 'SKU-HO', '仕入先': '仕入A', '商品名': 'ワークパンツ', '顧客': '顧客A'},
                code='SKU-HO', supplier='仕入A', product_name=official, customers=['顧客A'],
            ))
            # 股下寸法違いは同一視しない
            self.assertFalse(pli._aux_row_matches_candidate(
                {'商品コード': 'SKU-HO', '仕入先': '仕入A', '商品名': 'ワークパンツ（股下72）', '顧客': '顧客A'},
                code='SKU-HO', supplier='仕入A', product_name=official, customers=['顧客A'],
            ))

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'hem-ledger.csv',
                'auxFile': csv_b64_from_text(opening), 'auxName': 'open-hem.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-hem-open-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)

            prod = c.execute(
                "SELECT id,name FROM products WHERE code='SKU-HO' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertNotIn('丈加工', prod['name'])
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (prod['id'],),
                )
            }
            self.assertTrue(
                any('丈加工' in a for a in aliases) or 'ワークパンツ（丈加工）' in aliases,
                aliases,
            )
        finally:
            c.close()
            folder.cleanup()


class ParenClassificationTest(unittest.TestCase):
    """括弧内の色・加工・生地分類（辞書規則のみ）。"""

    def test_examples_auto(self):
        import product_ledger_paren as pp
        cases = [
            ('ポロシャツ（ネイビー）', 'ポロシャツ', 'ネイビー', '', ''),
            ('白衣（DHC刺繍あり）', '白衣', '', 'DHC刺繍あり', ''),
            ('ベスト（赤・背中プリント）', 'ベスト', 'レッド', '背中プリント', ''),
            ('パンツ（綿100％）', 'パンツ', '', '', '綿100％'),
            ('ポロシャツ(ネイビー)', 'ポロシャツ', 'ネイビー', '', ''),  # 半角括弧
        ]
        for original, planned, color, proc, mat in cases:
            with self.subTest(original=original):
                r = pp.classify_product_name(original)
                self.assertFalse(r['needsConfirm'], r)
                self.assertTrue(r['autoClassified'], r)
                self.assertEqual(r['plannedName'], planned)
                self.assertEqual(r['color'], color)
                self.assertEqual(r['processing'], proc)
                self.assertEqual(r['material'], mat)

    def test_unknown_keeps_in_name_without_blocking(self):
        """未分類のみは商品名に残し、要確認にしない。"""
        import product_ledger_paren as pp
        r = pp.classify_product_name('パンツ（代替品）')
        self.assertFalse(r['needsConfirm'], r)
        self.assertEqual(r['plannedName'], 'パンツ（代替品）')
        self.assertIn('代替品', r['unclassified'])
        self.assertTrue(r['autoClassified'], r)
        self.assertTrue(any('代替品' in n and '商品名に残して' in n for n in r.get('infoNotices') or []))

        winter = pp.classify_product_name('ブルゾン（冬）')
        self.assertFalse(winter['needsConfirm'], winter)
        self.assertEqual(winter['plannedName'], 'ブルゾン（冬）')
        self.assertEqual(winter['color'], '')
        self.assertIn('冬', winter['unclassified'])

        mixed = pp.classify_product_name('ブルゾン（冬）（紺）')
        self.assertFalse(mixed['needsConfirm'], mixed)
        self.assertEqual(mixed['plannedName'], 'ブルゾン（冬）')
        self.assertEqual(mixed['color'], 'ネイビー')
        self.assertIn('冬', mixed['unclassified'])

        mixed_proc = pp.classify_product_name('白衣（冬）（DHC刺繍あり）')
        self.assertFalse(mixed_proc['needsConfirm'], mixed_proc)
        self.assertEqual(mixed_proc['plannedName'], '白衣（冬）')
        self.assertEqual(mixed_proc['processing'], 'DHC刺繍あり')

        same_paren = pp.classify_product_name('ブルゾン（冬・紺）')
        self.assertFalse(same_paren['needsConfirm'], same_paren)
        self.assertEqual(same_paren['plannedName'], 'ブルゾン（冬）')
        self.assertEqual(same_paren['color'], 'ネイビー')

        # 冬／夏は照合キーでも区別したまま（剥がして同一視しない）
        import legacy_csv_import as legacy
        self.assertNotEqual(
            legacy.product_name_core_key('ブルゾン（冬）'),
            legacy.product_name_core_key('ブルゾン（夏）'),
        )
        winter2 = pp.split_name_and_color('ブルゾン（冬）')
        summer = pp.split_name_and_color('ブルゾン（夏）')
        self.assertEqual(winter2['plannedName'], 'ブルゾン（冬）')
        self.assertEqual(summer['plannedName'], 'ブルゾン（夏）')
        self.assertNotEqual(winter2['plannedName'], summer['plannedName'])

    def test_green_not_fluorescent_green(self):
        import product_ledger_paren as pp
        r = pp.classify_product_name('ベスト（蛍光グリーン）')
        self.assertEqual(r['color'], '蛍光グリーン')
        self.assertNotEqual(r['color'], '緑')
        r2 = pp.classify_product_name('ベスト（緑）')
        self.assertEqual(r2['color'], 'グリーン')

    def test_broken_paren_confirm(self):
        import product_ledger_paren as pp
        r = pp.classify_product_name('パンツ（赤')
        self.assertTrue(r['needsConfirm'])
        self.assertEqual(r['plannedName'], 'パンツ（赤')

    def test_preview_and_commit_paren_to_fields(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-P1,ポロシャツ（ネイビー）,M',
                '顧客B,仕入A,SKU-P2,白衣（DHC刺繍あり）,L',
                '顧客A,仕入A,SKU-P3,パンツ（綿100％）,S',
                '顧客A,仕入A,SKU-P4,パンツ（代替品）,M',
                '顧客A,仕入A,SKU-P5,ブルゾン（冬）（紺）,L',
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'paren.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            by_no = {r['rowNo']: r for r in preview['rows']}
            p1 = by_no[2]['parenClassification']
            self.assertEqual(p1['plannedName'], 'ポロシャツ')
            self.assertEqual(p1['color'], 'ネイビー')
            self.assertTrue(p1['autoClassified'])
            self.assertEqual(by_no[2]['product'].get('proposedColor'), 'ネイビー')

            p2 = by_no[3]['parenClassification']
            self.assertEqual(p2['processing'], 'DHC刺繍あり')
            self.assertEqual(by_no[3]['handle'].get('embroidery'), 'DHC刺繍あり')

            p3 = by_no[4]['parenClassification']
            self.assertEqual(p3['material'], '綿100％')
            self.assertEqual(by_no[4]['product'].get('proposedMaterial'), '綿100％')

            p4 = by_no[5]['parenClassification']
            self.assertFalse(p4['needsConfirm'], p4)
            self.assertEqual(p4['plannedName'], 'パンツ（代替品）')
            self.assertNotEqual(by_no[5]['status'], 'needs_review')
            self.assertTrue(any('代替品' in n for n in (p4.get('infoNotices') or [])))

            p5 = by_no[6]['parenClassification']
            self.assertFalse(p5['needsConfirm'], p5)
            self.assertEqual(p5['plannedName'], 'ブルゾン（冬）')
            self.assertEqual(p5['color'], 'ネイビー')
            self.assertEqual(by_no[6]['product'].get('proposedColor'), 'ネイビー')
            self.assertNotEqual(by_no[6]['status'], 'needs_review')

            body = {
                'file': csv_b64_from_text(text), 'name': 'paren.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            # 手動編集は再照合で上書きしない
            body_edit = {
                **body,
                'rowClassifications': {
                    '6': {
                        'plannedName': 'ブルゾン手編集',
                        'color': '紺',
                        'processing': '', 'material': '',
                        'unclassified': ['冬'],
                        'userEdited': True,
                    },
                },
            }
            preview_edit = pli.build_preview(c, body_edit)
            self.assertEqual(stock_snap(c), before)
            pe = {r['rowNo']: r for r in preview_edit['rows']}[6]['parenClassification']
            self.assertEqual(pe['plannedName'], 'ブルゾン手編集')
            self.assertTrue(pe.get('userEdited'))

            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-paren-1',
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)

            navy = c.execute(
                "SELECT id,name,color,material FROM products WHERE code='SKU-P1' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertEqual(navy['name'], 'ポロシャツ')
            self.assertEqual(navy['color'], 'ネイビー')

            white = c.execute(
                "SELECT p.id FROM products p WHERE p.code='SKU-P2' AND p.merged_into_id IS NULL"
            ).fetchone()
            emb = c.execute(
                'SELECT embroidery FROM customer_products WHERE product_id=?',
                (white['id'],),
            ).fetchone()['embroidery']
            self.assertEqual(emb, 'DHC刺繍あり')

            mat = c.execute(
                "SELECT name,material FROM products WHERE code='SKU-P3' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertEqual(mat['name'], 'パンツ')
            self.assertEqual(mat['material'], '綿100％')

            alt = c.execute(
                "SELECT name,color FROM products WHERE code='SKU-P4' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertEqual(alt['name'], 'パンツ（代替品）')
            self.assertEqual(alt['color'] or '', '')

            blouson = c.execute(
                "SELECT id,name,color FROM products WHERE code='SKU-P5' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertEqual(blouson['name'], 'ブルゾン（冬）')
            self.assertEqual(blouson['color'], 'ネイビー')

            # 原文が別名に残る
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (navy['id'],),
                )
            }
            self.assertIn('ポロシャツ（ネイビー）', aliases)
            blouson_aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (blouson['id'],),
                )
            }
            self.assertIn('ブルゾン（冬）（紺）', blouson_aliases)

            # 再取込で二重登録なし
            preview3 = pli.build_preview(c, body)
            self.assertEqual(preview3['plannedCounts']['productsToCreate'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_composite_and_spec_diff_not_mixed(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-X1,ベスト（赤・背中プリント）,M',
                '顧客A,仕入A,SKU-X1,ベスト（緑）,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'spec.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-X1')
            # 色差の疑いで確認（在庫有無だけではまとめない）
            self.assertTrue(g['needsDecision'])
            self.assertTrue(g.get('colorDiffPossible') or g.get('reviewTier') == 'individual')
            rows = [r for r in preview['rows'] if r['csv']['code'] == 'SKU-X1']
            colors = {(r.get('parenClassification') or {}).get('color') for r in rows}
            self.assertIn('レッド', colors)
            self.assertIn('グリーン', colors)
        finally:
            c.close()
            folder.cleanup()


class ExplicitColorNamePriorityTest(unittest.TestCase):
    """在庫未決時の明示色優先・名／色振り分け（9-471 等）。本番DBは触らない。"""

    def test_split_safeguards_hakui_and_fluorescent_green(self):
        import product_ledger_paren as pp
        hakui = pp.split_name_and_color('白衣')
        self.assertFalse(hakui['hasExplicitColor'])
        self.assertEqual(hakui['plannedName'], '白衣')
        self.assertEqual(hakui['color'], '')

        long = pp.split_name_and_color('女性用半袖白衣')
        self.assertEqual(long['color'], '')
        self.assertEqual(long['plannedName'], '女性用半袖白衣')

        fg = pp.split_name_and_color('ベスト（蛍光グリーン）')
        self.assertEqual(fg['color'], '蛍光グリーン')
        green = pp.split_name_and_color('ベスト（緑）')
        self.assertEqual(green['color'], 'グリーン')
        self.assertFalse(pp.colors_equivalent(fg['color'], green['color']))

        outside_fg = pp.split_name_and_color('蛍光グリーンつなぎ')
        self.assertEqual(outside_fg['color'], '蛍光グリーン')
        self.assertNotEqual(outside_fg['color'], '緑')
        self.assertEqual(outside_fg['plannedName'], 'つなぎ')

    def test_9_471_salon_apron_max_name_when_all_stock_zero(self):
        """品番9-471: 全候補在庫0 → 台帳最多名（サロンエプロン2行）を色優先より採用。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,9-471,サロン（黒）,M',
                '顧客A,仕入A,9-471,サロンエプロン,L',
                '顧客A,仕入A,9-471,＊サロンエプロン,LL',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'サロン（黒）', '9-471', 'M', '0'),
                opening_line('仕入A', '顧客A', 'サロンエプロン', '9-471', 'L', '0'),
                opening_line('仕入A', '顧客A', '＊サロンエプロン', '9-471', 'LL', '0'),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '9471.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '9-471')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'ledger_max_name_zero_stock')
            self.assertNotEqual(aux_p.get('nameAdoptionRuleCode'), 'explicit_color_name')
            self.assertFalse(g['needsDecision'])
            self.assertTrue(g.get('auxStockPickAuto'))
            self.assertEqual(
                aux_p.get('plannedRegisteredName') or aux_p.get('suggestedOfficialName'),
                'サロンエプロン',
            )
            self.assertIn('2行', aux_p.get('pairAdoptionRule') or '')

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '9471.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-9471-maxname',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            prod = c.execute(
                "SELECT id,name,color FROM products WHERE code='9-471' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertEqual(prod['name'], 'サロンエプロン')
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (prod['id'],),
                )
            }
            self.assertTrue(
                any('サロンエプロン' in a or 'サロン（黒）' in a or a == '＊サロンエプロン' for a in aliases)
                or 'サロンエプロン' in aliases,
                aliases,
            )
        finally:
            c.close()
            folder.cleanup()

    def test_9_471_salon_black_color_when_stock_undecidable(self):
        """在庫が不正/空で未決のときだけ明示色優先（全ゼロ最多名ルールは使わない）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,9-471,サロン（黒）,M',
                '顧客A,仕入A,9-471,サロンエプロン,L',
                '顧客A,仕入A,9-471,＊サロンエプロン,LL',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'サロン（黒）', '9-471', 'M', ''),
                opening_line('仕入A', '顧客A', 'サロンエプロン', '9-471', 'L', '0'),
                opening_line('仕入A', '顧客A', '＊サロンエプロン', '9-471', 'LL', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '9471-empty.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '9-471')
            aux_p = g.get('auxPrimary') or {}
            # 空在庫は0扱いでない → 最多名ルールなし。色でも自動確定しない（不正時）
            self.assertNotEqual(aux_p.get('nameAdoptionRuleCode'), 'ledger_max_name_zero_stock')
            self.assertTrue(aux_p.get('pairNeedsChoice'))
        finally:
            c.close()
            folder.cleanup()

    def test_outside_color_preferred_without_aux(self):
        """補助CSVなしでも、色あり候補が一意なら採用（色省略まとめ後でも可）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-OC,青エプロン,M',
                '顧客A,仕入A,SKU-OC,エプロン,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'oc.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-OC')
            aux_p = g.get('auxPrimary') or {}
            self.assertFalse(g['needsDecision'])
            # 色省略で先に1候補化するか、明示色優先か、どちらでも登録予定はエプロン／ブルー
            self.assertIn(
                aux_p.get('nameAdoptionRuleCode'),
                ('explicit_color_name', 'aux_single_candidate', None, ''),
            )
            planned_name = (
                g.get('plannedRegisteredName')
                or aux_p.get('suggestedOfficialName')
                or (g.get('productPlan') or {}).get('productName')
            )
            planned_color = (
                g.get('plannedRegisteredColor')
                or aux_p.get('adoptedColor')
                or (g.get('productPlan') or {}).get('plannedRegisteredColor')
            )
            self.assertEqual(planned_name, 'エプロン')
            self.assertEqual(planned_color, 'ブルー')
        finally:
            c.close()
            folder.cleanup()

    def test_different_explicit_colors_need_review(self):
        """全ゼロ＋票同数 → 色優先せず要確認（票数を示す）。色差フラグは分析側で残る。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-DC,サロン（黒）,M',
                '顧客A,仕入A,SKU-DC,サロン（白）,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'サロン（黒）', 'SKU-DC', 'M', '0'),
                opening_line('仕入A', '顧客A', 'サロン（白）', 'SKU-DC', 'L', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'dc.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-DC')
            aux_p = g.get('auxPrimary') or {}
            self.assertNotEqual(aux_p.get('nameAdoptionRuleCode'), 'explicit_color_name')
            self.assertTrue(g['needsDecision'])
            self.assertTrue(g.get('colorDiffPossible'))
            reasons = aux_p.get('nameReasons') or []
            self.assertTrue(any('同数' in r for r in reasons), reasons)
            votes = aux_p.get('ledgerNameVotes') or []
            self.assertEqual(sorted(int(v['count']) for v in votes), [1, 1])
        finally:
            c.close()
            folder.cleanup()

    def test_same_color_different_names_color_only(self):
        """同じ明示色・異なる商品名。全ゼロ同数票 → 色提案より票同数の要確認。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-SC,サロン（黒）,M',
                '顧客A,仕入A,SKU-SC,エプロン（黒）,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'サロン（黒）', 'SKU-SC', 'M', '0'),
                opening_line('仕入A', '顧客A', 'エプロン（黒）', 'SKU-SC', 'L', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sc.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-SC')
            aux_p = g.get('auxPrimary') or {}
            self.assertTrue(g['needsDecision'])
            self.assertNotEqual(aux_p.get('nameAdoptionRuleCode'), 'explicit_color_name')
            reasons = aux_p.get('nameReasons') or []
            self.assertTrue(any('同数' in r for r in reasons), reasons)
            votes = aux_p.get('ledgerNameVotes') or []
            self.assertEqual(len(votes), 2)
        finally:
            c.close()
            folder.cleanup()

    def test_same_color_different_names_when_stock_undecidable(self):
        """在庫不正で未決のとき: 同じ明示色・異なる商品名 → 色は提案、名は要確認。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-SC2,サロン（黒）,M',
                '顧客A,仕入A,SKU-SC2,エプロン（黒）,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'サロン（黒）', 'SKU-SC2', 'M', 'x'),
                opening_line('仕入A', '顧客A', 'エプロン（黒）', 'SKU-SC2', 'L', 'y'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sc2.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-SC2')
            aux_p = g.get('auxPrimary') or {}
            # 不正時は色でも自動確定しない
            self.assertTrue(aux_p.get('pairNeedsChoice'))
            self.assertNotEqual(aux_p.get('nameAdoptionRuleCode'), 'ledger_max_name_zero_stock')
        finally:
            c.close()
            folder.cleanup()

    def test_stock_max_beats_color_priority(self):
        """正の在庫最多が一意なら、色なしでも在庫側を採用（色優先は在庫の後）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-SB,無地エプロン,M',
                '顧客A,仕入B,SKU-SB,サロン（黒）,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '無地エプロン', 'SKU-SB', 'M', '50'),
                opening_line('仕入B', '顧客A', 'サロン（黒）', 'SKU-SB', 'L', '1'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sb.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-SB')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_max_positive_stock')
            self.assertEqual(aux_p.get('suggestedOfficialName'), '無地エプロン')
            self.assertEqual(aux_p.get('suggestedPrimarySupplier'), '仕入A')
            self.assertFalse(g['needsDecision'])
        finally:
            c.close()
            folder.cleanup()

    def test_stock_tie_broken_by_color_among_winners(self):
        """正の在庫同数タイ → タイ候補の中だけで明示色を優先（在庫0の色ありへ飛ばない）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-TIE,エプロン,M',
                '顧客A,仕入B,SKU-TIE,サロン（黒）,L',
                '顧客A,仕入C,SKU-TIE,無地表記,S',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'エプロン', 'SKU-TIE', 'M', '10'),
                opening_line('仕入B', '顧客A', 'サロン（黒）', 'SKU-TIE', 'L', '10'),
                opening_line('仕入C', '顧客A', '無地表記', 'SKU-TIE', 'S', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'tie.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-TIE')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'explicit_color_name')
            self.assertEqual(aux_p.get('sourceAdoptedName'), 'サロン（黒）')
            self.assertEqual(aux_p.get('suggestedOfficialName'), 'サロン')
            self.assertEqual(aux_p.get('adoptedColor'), 'ブラック')
            self.assertEqual(aux_p.get('suggestedPrimarySupplier'), '仕入B')
            # 在庫0の「無地表記」や色なし「エプロン」ではなく、タイ内の色ありを採用
            self.assertNotEqual(aux_p.get('suggestedPrimarySupplier'), '仕入C')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
        finally:
            c.close()
            folder.cleanup()

    def test_colorless_not_color_conflict_vs_colored(self):
        """色なし候補は色ありとの色違い扱いにしない。"""
        diff = pli.analyze_sku_review_diffs([
            {
                'csvNames': ['サロン（黒）', 'サロンエプロン'],
                'productName': 'サロン（黒）', 'supplier': '仕入A',
            },
        ])
        self.assertFalse(diff['colorDiffPossible'])
        self.assertTrue(diff['nameWordingDiff'])

    def test_manual_override_beats_color_auto(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,9-471,サロン（黒）,M',
                '顧客A,仕入A,9-471,サロン（黒）,L',
                '顧客A,仕入A,9-471,サロンエプロン,LL',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'サロン（黒）', '9-471', 'M', '0'),
                opening_line('仕入A', '顧客A', 'サロン（黒）', '9-471', 'L', '0'),
                opening_line('仕入A', '顧客A', 'サロンエプロン', '9-471', 'LL', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'man.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'skuDecisions': {
                    '9-471': {
                        'decision': 'merge',
                        'officialName': 'サロンエプロン',
                        'color': '',
                        'primarySupplier': '仕入A',
                        'primarySupplierManual': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '9-471')
            # 手動があるので自動クリアしない（最多名提案があっても上書きしない）
            self.assertTrue(g.get('auxStockPickManualOverride'))
            self.assertFalse(g.get('auxStockPickAuto'))
            name_plan = g.get('namePlan') or {}
            self.assertEqual(name_plan.get('officialName'), 'サロンエプロン')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'ledger_max_name_zero_stock')
        finally:
            c.close()
            folder.cleanup()


class StockAdoptColorwayReviewTest(unittest.TestCase):
    """在庫一意採用後、配色（×）を1色として自動決定へ進める。"""

    def test_colorway_outside_paren_extracts_as_single_color(self):
        import product_ledger_paren as pp
        r = pp.split_name_and_color('長袖ブルゾン　サックス×シルバーグレー')
        self.assertFalse(r['needsConfirm'], r)
        self.assertEqual(r['plannedName'], '長袖ブルゾン')
        self.assertEqual(r['color'], 'サックス×シルバーグレー')
        hits = pp.find_explicit_colors_in_text('長袖ブルゾン　サックス×シルバーグレー')
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['value'], 'サックス×シルバーグレー')

    def test_colorway_in_paren_extracts_as_single_color(self):
        import product_ledger_paren as pp
        r = pp.split_name_and_color('長袖ブルゾン（サックス×シルバーグレー）')
        self.assertFalse(r['needsConfirm'], r)
        self.assertEqual(r['plannedName'], '長袖ブルゾン')
        self.assertEqual(r['color'], 'サックス×シルバーグレー')

    def test_dot_separated_multi_color_still_needs_confirm(self):
        """・区切りの複数色は配色1色にしない（従来どおり要確認）。"""
        import product_ledger_paren as pp
        r = pp.split_name_and_color('ベスト（赤・青）')
        self.assertTrue(r['needsConfirm'])
        self.assertIn('複数', ''.join(r.get('confirmReasons') or ''))

    def test_6801_007_colorway_stock_adopt_auto_decides(self):
        """非ゼロ在庫1組の配色候補は個別確認に残さず自動決定済み（未登録）。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-007,長袖ブルゾン　サックス×シルバーグレー,M',
                '顧客A,仕入B,6801-007,長袖ブルゾン　ネイビー,L',
            ])
            aux = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', '長袖ブルゾン　サックス×シルバーグレー',
                    '6801-007', 'M', '2',
                ),
                opening_line(
                    '仕入B', '顧客A', '長袖ブルゾン　ネイビー',
                    '6801-007', 'L', '0',
                ),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '6801-007.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '6801-007')
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_nonzero_pair')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertTrue(g.get('auxStockPickAuto'))
            self.assertFalse(g.get('auxStockPickBlocked'))
            self.assertEqual(g.get('reviewTier'), 'auto')
            self.assertEqual(preview['skuDecisionPending'], 0)
            self.assertEqual(aux_p.get('suggestedOfficialName'), '長袖ブルゾン')
            self.assertEqual(
                aux_p.get('plannedRegisteredColor') or aux_p.get('adoptedColor'),
                'サックス×シルバーグレー',
            )
            self.assertEqual(g.get('plannedRegisteredName'), '長袖ブルゾン')
            self.assertEqual(g.get('plannedRegisteredColor'), 'サックス×シルバーグレー')
            reason = g.get('auxStockPickReason') or aux_p.get('pairAdoptionRule') or ''
            self.assertIn('在庫が0以外の候補が1組', reason)
            self.assertIn('サックス×シルバーグレー', reason)
            individual = [
                x for x in preview['skuGroups']
                if x.get('needsDecision') and x.get('reviewTier') == 'individual'
            ]
            self.assertFalse(any(x['code'] == '6801-007' for x in individual))

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '6801-007.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-6801-007-colorway-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            prod = c.execute(
                "SELECT id,name,color FROM products "
                "WHERE code='6801-007' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertIsNotNone(prod)
            self.assertEqual(prod['name'], '長袖ブルゾン')
            self.assertEqual(prod['color'], 'サックス×シルバーグレー')
            # 配色は1商品。色違い候補は別名として残る
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) AS n FROM products "
                    "WHERE code='6801-007' AND merged_into_id IS NULL"
                ).fetchone()['n'],
                1,
            )
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (prod['id'],),
                )
            }
            self.assertTrue(
                any('ネイビー' in a for a in aliases)
                or '長袖ブルゾン　ネイビー' in aliases
                or any('サックス' in a for a in aliases),
                aliases,
            )
        finally:
            c.close()
            folder.cleanup()

    def test_solid_color_stock_adopt_still_auto(self):
        """単色候補の在庫一意採用も従来どおり自動決定。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-SC,長袖ブルゾン　ネイビー,M',
                '顧客A,仕入B,SKU-SC,長袖ブルゾン　赤,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー', 'SKU-SC', 'M', '3'),
                opening_line('仕入B', '顧客A', '長袖ブルゾン　赤', 'SKU-SC', 'L', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sc.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-SC')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertTrue(g.get('auxStockPickAuto'))
            self.assertEqual(g.get('plannedRegisteredName'), '長袖ブルゾン')
            self.assertEqual(g.get('plannedRegisteredColor'), 'ネイビー')
        finally:
            c.close()
            folder.cleanup()

    def test_existing_dup_after_stock_adopt_keeps_review_with_clear_reason(self):
        """在庫でメイン決定後も、登録済み商品が複数なら残す商品の選択が必要。"""
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入A', 'active': True})
            stock.save_product(c, {
                'code': '6801-007', 'name': '長袖ブルゾン旧1', 'color': '',
                'supplierId': su['id'], 'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': False, 'active': True}],
                '_allowHistoricalDuplicateCode': True,
            })
            stock.save_product(c, {
                'code': '6801-007', 'name': '長袖ブルゾン旧2', 'color': '',
                'supplierId': su['id'], 'active': True, 'sizes': ['L'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': False, 'active': True}],
                '_allowHistoricalDuplicateCode': True,
            })
            c.commit()
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-007,長袖ブルゾン　サックス×シルバーグレー,M',
                '顧客A,仕入A,6801-007,長袖ブルゾン　ネイビー,L',
            ])
            aux = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', '長袖ブルゾン　サックス×シルバーグレー',
                    '6801-007', 'M', '2',
                ),
                opening_line(
                    '仕入A', '顧客A', '長袖ブルゾン　ネイビー',
                    '6801-007', 'L', '0',
                ),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'dup.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '6801-007')
            self.assertTrue(g['needsDecision'])
            self.assertEqual(g.get('existingCount'), 2)
            self.assertTrue(g.get('auxStockPickPendingExisting'))
            self.assertFalse(g.get('auxStockPickAuto'))
            labels = [r.get('label') or '' for r in (g.get('reviewReasons') or [])]
            self.assertTrue(
                any('登録済み商品が2件' in lab and '残す商品' in lab for lab in labels),
                labels,
            )
            self.assertTrue(
                any('在庫2' in lab or '在庫を根拠' in lab for lab in labels),
                labels,
            )
            # 候補差の色違いだけでは残さない（既存重複が理由）
            self.assertFalse(g.get('colorDiffPossible'))
        finally:
            c.close()
            folder.cleanup()

    def test_primary_supplier_change_still_needs_confirm_after_stock_adopt(self):
        """既存1件で通常仕入先が変わる場合は在庫採用後も明示確認。"""
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
            su_old = stock.save_supplier(c, {'name': '仕入旧', 'active': True})
            stock.save_supplier(c, {'name': '仕入A', 'active': True})
            stock.save_product(c, {
                'code': 'SKU-PS', 'name': '長袖ブルゾン', 'color': 'ネイビー',
                'supplierId': su_old['id'], 'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': False, 'active': True}],
            })
            c.commit()
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-PS,長袖ブルゾン　ネイビー,M',
                '顧客A,仕入B,SKU-PS,長袖ブルゾン　赤,L',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー', 'SKU-PS', 'M', '4'),
                opening_line('仕入B', '顧客A', '長袖ブルゾン　赤', 'SKU-PS', 'L', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'ps.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-PS')
            self.assertTrue(g['needsDecision'])
            self.assertTrue(g.get('auxStockPickNeedsPrimaryConfirm'))
            self.assertFalse(g.get('auxStockPickAuto'))
        finally:
            c.close()
            folder.cleanup()

    def test_manual_override_not_cleared_by_colorway_stock_adopt(self):
        """手動判断があるときは配色在庫採用でも自動クリアしない。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-007,長袖ブルゾン　サックス×シルバーグレー,M',
                '顧客A,仕入B,6801-007,長袖ブルゾン　ネイビー,L',
            ])
            aux = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', '長袖ブルゾン　サックス×シルバーグレー',
                    '6801-007', 'M', '2',
                ),
                opening_line(
                    '仕入B', '顧客A', '長袖ブルゾン　ネイビー',
                    '6801-007', 'L', '0',
                ),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'man.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'skuDecisions': {
                    '6801-007': {
                        'decision': 'merge',
                        'officialName': '手決めブルゾン',
                        'color': 'ネイビー',
                        'primarySupplier': '仕入B',
                        'primarySupplierManual': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '6801-007')
            self.assertTrue(g.get('auxStockPickManualOverride'))
            self.assertFalse(g.get('auxStockPickAuto'))
            name_plan = g.get('namePlan') or {}
            self.assertEqual(
                (preview.get('skuDecisions') or {}).get('6801-007', {}).get('officialName')
                or name_plan.get('officialName')
                or '手決めブルゾン',
                '手決めブルゾン',
            )
        finally:
            c.close()
            folder.cleanup()


class ZeroStockMaxLedgerNameTest(unittest.TestCase):
    """全候補が確認済み在庫0のとき、商品台帳の最多正規化名を採用（SKU 6801-008 型）。

    本番DBへの商品登録・削除・在庫ミューテーションはしない（一時DBのみ）。
    """

    CODE = '6801-008'

    def test_normalize_merges_kana_space_colorway_used_mark(self):
        a = '長袖ブルゾン　ネイビー×シルバーグレー'
        b = '長袖ブルゾン ﾈｲﾋﾞｰ*ｼﾙﾊﾞｰｸﾞﾚｰ'
        c = '＊長袖ブルゾン ネイビー×シルバーグレー'
        key = pli.normalize_ledger_name_for_vote(a)
        self.assertEqual(key, '長袖ブルゾン ネイビー×シルバーグレー')
        self.assertEqual(pli.normalize_ledger_name_for_vote(b), key)
        self.assertEqual(pli.normalize_ledger_name_for_vote(c), key)
        self.assertNotEqual(
            pli.normalize_ledger_name_for_vote('ネイビー'),
            pli.normalize_ledger_name_for_vote('ネイビー×シルバーグレー'),
        )

    def test_6801_008_notation_merge_adopts_max_name(self):
        """表記ゆれを合算し最多名を採用。色振り分け・サイズ差は1票ずつ。"""
        folder, path, c = newdb()
        before = stock_snap(c)
        try:
            ledger = tiny_ledger_rows([
                # 配色（全角×）: 3行
                'ユニシス,㈱ユニコロン,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,S',
                'ユニシス,㈱ユニコロン,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,M',
                'ユニシス,㈱ユニコロン,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,L',
                # 配色（半角カナ*）: 2行 → 上記と合算5票
                '㈱合田観光商事　ひまわり,㈱ユニコロン,6801-008,長袖ブルゾン　ﾈｲﾋﾞｰ*ｼﾙﾊﾞｰｸﾞﾚｰ,S',
                '㈱合田観光商事　ひまわり,㈱ユニコロン,6801-008,＊長袖ブルゾン　ﾈｲﾋﾞｰ*ｼﾙﾊﾞｰｸﾞﾚｰ,M',
                # 別名称: 2行
                '㈱ダイイチ,アイトス㈱,6801-008,長袖ブルゾン,L',
                '㈱ダイイチ,アイトス㈱,6801-008,長袖ブルゾン,LL',
                # ネイビーのみ（配色とは別）: 1行
                '㈱道新総合印刷,アイトス㈱,6801-008,長袖ブルゾン　ネイビー,LL',
            ])
            aux = tiny_opening_rows([
                opening_line('㈱ユニコロン', 'ユニシス', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'S', '0'),
                opening_line('㈱ユニコロン', 'ユニシス', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'M', '0'),
                opening_line('㈱ユニコロン', 'ユニシス', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'L', '0'),
                opening_line('㈱ユニコロン', '㈱合田観光商事　ひまわり', '長袖ブルゾン　ﾈｲﾋﾞｰ*ｼﾙﾊﾞｰｸﾞﾚｰ', '6801-008', 'S', '0'),
                opening_line('㈱ユニコロン', '㈱合田観光商事　ひまわり', '＊長袖ブルゾン　ﾈｲﾋﾞｰ*ｼﾙﾊﾞｰｸﾞﾚｰ', '6801-008', 'M', '0'),
                opening_line('アイトス㈱', '㈱ダイイチ', '長袖ブルゾン', '6801-008', 'L', '0'),
                opening_line('アイトス㈱', '㈱ダイイチ', '長袖ブルゾン', '6801-008', 'LL', '0'),
                opening_line('アイトス㈱', '㈱道新総合印刷', '長袖ブルゾン　ネイビー', '6801-008', 'LL', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': '6801-008.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('pairAdoptionRuleCode'), 'ledger_max_name_zero_stock')
            self.assertFalse(aux_p.get('pairNeedsChoice'))
            self.assertFalse(g['needsDecision'])
            self.assertTrue(g.get('auxStockPickAuto'))
            rule = aux_p.get('pairAdoptionRule') or g.get('auxStockPickReason') or ''
            self.assertIn('全候補の在庫が0のため、商品台帳で最多の名称を採用', rule)
            self.assertIn('5行', rule)
            self.assertEqual(g.get('plannedRegisteredName') or aux_p.get('plannedRegisteredName'), '長袖ブルゾン')
            self.assertEqual(
                g.get('plannedRegisteredColor') or aux_p.get('plannedRegisteredColor'),
                'ネイビー×シルバーグレー',
            )
            votes = aux_p.get('ledgerNameVotes') or []
            by_name = {v['normalizedName']: int(v['count']) for v in votes}
            self.assertEqual(by_name.get('長袖ブルゾン ネイビー×シルバーグレー'), 5)
            self.assertEqual(by_name.get('長袖ブルゾン'), 2)
            self.assertEqual(by_name.get('長袖ブルゾン ネイビー'), 1)
            # 原文表記→登録先の対応が残る
            mappings = (g.get('namePlan') or {}).get('nameMappings') or []
            originals = {m.get('originalName') for m in mappings}
            self.assertTrue(any('ﾈｲﾋﾞｰ' in (o or '') or 'ネイビー×' in (o or '') for o in originals))
            self.assertEqual(stock_snap(c), before)
        finally:
            c.close()
            folder.cleanup()

    def test_max_tie_leaves_review_with_counts(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,S',
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,M',
                '顧客B,仕入B,6801-008,長袖ブルゾン,S',
                '顧客B,仕入B,6801-008,長袖ブルゾン,M',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'S', '0'),
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'M', '0'),
                opening_line('仕入B', '顧客B', '長袖ブルゾン', '6801-008', 'S', '0'),
                opening_line('仕入B', '顧客B', '長袖ブルゾン', '6801-008', 'M', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'tie.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            aux_p = g.get('auxPrimary') or {}
            self.assertTrue(aux_p.get('pairNeedsChoice'))
            self.assertTrue(g['needsDecision'])
            self.assertTrue(
                any('同数' in (r or '') for r in (aux_p.get('nameReasons') or [])),
                aux_p.get('nameReasons'),
            )
            votes = aux_p.get('ledgerNameVotes') or []
            self.assertEqual(sorted(int(v['count']) for v in votes), [2, 2])
        finally:
            c.close()
            folder.cleanup()

    def test_prefers_max_count_over_explicit_color(self):
        """全ゼロ時は明示色より最多票を優先。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-008,長袖ブルゾン,S',
                '顧客A,仕入A,6801-008,長袖ブルゾン,M',
                '顧客A,仕入A,6801-008,長袖ブルゾン,L',
                '顧客B,仕入B,6801-008,長袖ブルゾン　ネイビー,S',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '長袖ブルゾン', '6801-008', 'S', '0'),
                opening_line('仕入A', '顧客A', '長袖ブルゾン', '6801-008', 'M', '0'),
                opening_line('仕入A', '顧客A', '長袖ブルゾン', '6801-008', 'L', '0'),
                opening_line('仕入B', '顧客B', '長袖ブルゾン　ネイビー', '6801-008', 'S', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'color.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            aux_p = g.get('auxPrimary') or {}
            self.assertEqual(aux_p.get('pairAdoptionRuleCode'), 'ledger_max_name_zero_stock')
            self.assertNotEqual(aux_p.get('pairAdoptionRuleCode'), 'explicit_color_name')
            self.assertEqual(aux_p.get('suggestedOfficialName'), '長袖ブルゾン')
            self.assertFalse(aux_p.get('adoptedColor'))
        finally:
            c.close()
            folder.cleanup()

    def test_no_matching_aux_rows_does_not_treat_as_zero(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,S',
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,M',
                '顧客B,仕入B,6801-008,長袖ブルゾン,S',
            ])
            aux = tiny_opening_rows([
                opening_line('別仕入', '別顧客', '別商品', '99999', 'M', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'nomatch.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            aux_p = g.get('auxPrimary') or {}
            # 該当なしは在庫0扱いにしない → 最多名称ルールは発動しない
            self.assertNotEqual(aux_p.get('pairAdoptionRuleCode'), 'ledger_max_name_zero_stock')
            self.assertFalse(aux_p.get('ledgerNameVoteAdopted'))
        finally:
            c.close()
            folder.cleanup()

    def test_invalid_qty_does_not_treat_as_zero(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,S',
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,M',
                '顧客B,仕入B,6801-008,長袖ブルゾン,S',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'S', 'abc'),
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'M', '0'),
                opening_line('仕入B', '顧客B', '長袖ブルゾン', '6801-008', 'S', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'badqty.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            aux_p = g.get('auxPrimary') or {}
            self.assertNotEqual(aux_p.get('pairAdoptionRuleCode'), 'ledger_max_name_zero_stock')
            self.assertTrue(aux_p.get('pairNeedsChoice'))
        finally:
            c.close()
            folder.cleanup()

    def test_positive_stock_still_wins_over_max_name(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-008,長袖ブルゾン,S',
                '顧客A,仕入A,6801-008,長袖ブルゾン,M',
                '顧客A,仕入A,6801-008,長袖ブルゾン,L',
                '顧客B,仕入B,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,S',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '長袖ブルゾン', '6801-008', 'S', '0'),
                opening_line('仕入A', '顧客A', '長袖ブルゾン', '6801-008', 'M', '0'),
                opening_line('仕入A', '顧客A', '長袖ブルゾン', '6801-008', 'L', '0'),
                opening_line('仕入B', '顧客B', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'S', '7'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'pos.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            aux_p = g.get('auxPrimary') or {}
            self.assertIn(aux_p.get('pairAdoptionRuleCode'), ('aux_nonzero_pair', 'aux_max_positive_stock'))
            self.assertNotEqual(aux_p.get('pairAdoptionRuleCode'), 'ledger_max_name_zero_stock')
            # 在庫7の配色側を採用（登録名は色振り分け後「長袖ブルゾン」）
            self.assertEqual(aux_p.get('plannedRegisteredName') or aux_p.get('suggestedOfficialName'), '長袖ブルゾン')
            self.assertEqual(
                aux_p.get('plannedRegisteredColor') or aux_p.get('adoptedColor'),
                'ネイビー×シルバーグレー',
            )
        finally:
            c.close()
            folder.cleanup()

    def test_manual_override_beats_max_name(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,S',
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,M',
                '顧客B,仕入B,6801-008,長袖ブルゾン,S',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'S', '0'),
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'M', '0'),
                opening_line('仕入B', '顧客B', '長袖ブルゾン', '6801-008', 'S', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'manual.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'skuDecisions': {
                    '6801-008': {
                        'decision': 'merge',
                        'officialName': '手決めブルゾン',
                        'primarySupplier': '仕入B',
                        'primarySupplierManual': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            self.assertTrue(g.get('auxStockPickManualOverride'))
            self.assertFalse(g.get('auxStockPickAuto'))
            name_plan = g.get('namePlan') or {}
            self.assertEqual(name_plan.get('officialName'), '手決めブルゾン')
        finally:
            c.close()
            folder.cleanup()

    def test_color_split_override_beats_max_name(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,S',
                '顧客A,仕入A,6801-008,長袖ブルゾン　ネイビー×シルバーグレー,M',
                '顧客B,仕入B,6801-008,長袖ブルゾン　ネイビー,S',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'S', '0'),
                opening_line('仕入A', '顧客A', '長袖ブルゾン　ネイビー×シルバーグレー', '6801-008', 'M', '0'),
                opening_line('仕入B', '顧客B', '長袖ブルゾン　ネイビー', '6801-008', 'S', '0'),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'csplit.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'skuDecisions': {'6801-008': {'decision': 'color_split'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            self.assertTrue(g.get('colorSplit') or (g.get('decision') or {}).get('decision') == 'color_split')
            self.assertTrue(g.get('excludeAuxStockAutoMerge') or g.get('auxStockPickManualOverride'))
            self.assertFalse(g.get('auxStockPickAuto'))
        finally:
            c.close()
            folder.cleanup()

    def test_ui_mentions_zero_stock_max_name(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('全候補が確認済み在庫0', js)
        self.assertIn('ledgerNameVotes', js)
        self.assertIn('商品台帳の名称票', js)


class SlashColorwayClassificationTest(unittest.TestCase):
    """スラッシュ配色（/／）を1色として扱う。テストDBのみ・本番未変更。"""

    def test_half_and_full_slash_paren_colorway(self):
        import product_ledger_paren as pp
        for original, color in (
            ('エプロン（黒／ワイン）', 'ブラック×ワイン'),
            ('エプロン（黒/ワイン）', 'ブラック×ワイン'),
            ('ブルゾン（紺/白/赤）', 'ネイビー×ホワイト×レッド'),
            ('ブルゾン（紺／白／赤）', 'ネイビー×ホワイト×レッド'),
        ):
            with self.subTest(original=original):
                r = pp.split_name_and_color(original)
                self.assertFalse(r['needsConfirm'], r)
                self.assertTrue(r['autoClassified'], r)
                self.assertEqual(r['plannedName'], original.split('（')[0])
                self.assertEqual(r['color'], color)
                self.assertIn(
                    f'配色として1商品で登録：{color}',
                    r.get('infoNotices') or [],
                )

    def test_outside_slash_and_whitespace_halfwidth_kana(self):
        import product_ledger_paren as pp
        r = pp.split_name_and_color('ブルゾン 紺 / 白 / 赤')
        self.assertFalse(r['needsConfirm'], r)
        self.assertEqual(r['plannedName'], 'ブルゾン')
        self.assertEqual(r['color'], 'ネイビー×ホワイト×レッド')

        kana = pp.split_name_and_color('エプロン（黒 / ﾜｲﾝ）')
        self.assertFalse(kana['needsConfirm'], kana)
        self.assertEqual(kana['plannedName'], 'エプロン')
        self.assertEqual(kana['color'], 'ブラック×ワイン')

    def test_mixed_non_color_does_not_force_colorway(self):
        import product_ledger_paren as pp
        mixed = pp.split_name_and_color('エプロン（黒／代替品）')
        self.assertFalse(mixed['needsConfirm'], mixed)
        self.assertNotEqual(mixed['color'], '黒／代替品')
        self.assertEqual(mixed['color'], 'ブラック')
        self.assertEqual(mixed['plannedName'], 'エプロン（代替品）')
        self.assertIn('代替品', mixed['unclassified'])

        season = pp.classify_product_name('ブルゾン（冬／夏）')
        self.assertFalse(season['needsConfirm'], season)
        self.assertEqual(season['plannedName'], 'ブルゾン（冬／夏）')
        self.assertEqual(season['color'], '')
        self.assertIn('冬／夏', season['unclassified'])

        # ・区切りの複数色は従来どおり要確認
        dot = pp.split_name_and_color('ベスト（赤・青）')
        self.assertTrue(dot['needsConfirm'])

    def test_times_colorway_still_works(self):
        """PR #178 の × 配色は維持。"""
        import product_ledger_paren as pp
        r = pp.split_name_and_color('長袖ブルゾン（サックス×シルバーグレー）')
        self.assertFalse(r['needsConfirm'], r)
        self.assertEqual(r['plannedName'], '長袖ブルゾン')
        self.assertEqual(r['color'], 'サックス×シルバーグレー')

    def test_slash_colorway_preview_commit_one_product(self):
        """配色は1商品。数量・取扱を色ごとに増やさない。原文は別名に残す。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SL-100,エプロン（黒／ワイン）,M',
                '顧客A,仕入A,SL-100,エプロン（黒/ワイン）,L',
            ])
            aux = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', 'エプロン（黒／ワイン）',
                    'SL-100', 'M', '3',
                ),
                opening_line(
                    '仕入A', '顧客A', 'エプロン（黒/ワイン）',
                    'SL-100', 'L', '2',
                ),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sl.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SL-100')
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertEqual(g.get('reviewTier'), 'auto')
            self.assertEqual(g.get('plannedRegisteredName'), 'エプロン')
            self.assertEqual(g.get('plannedRegisteredColor'), 'ブラック×ワイン')
            by_no = {r['rowNo']: r for r in preview['rows']}
            notices = []
            for r in by_no.values():
                if (r.get('csv') or {}).get('code') != 'SL-100':
                    continue
                pc = r.get('parenClassification') or {}
                self.assertEqual(pc.get('color'), 'ブラック×ワイン')
                self.assertEqual(pc.get('plannedName'), 'エプロン')
                notices.extend(pc.get('infoNotices') or [])
            self.assertTrue(
                any('配色として1商品で登録：ブラック×ワイン' in n for n in notices),
                notices,
            )

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sl.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-slash-colorway-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            products = c.execute(
                "SELECT id,name,color FROM products "
                "WHERE code='SL-100' AND merged_into_id IS NULL"
            ).fetchall()
            self.assertEqual(len(products), 1)
            self.assertEqual(products[0]['name'], 'エプロン')
            self.assertEqual(products[0]['color'], 'ブラック×ワイン')
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases "
                    "WHERE kind='name' AND product_id=?",
                    (products[0]['id'],),
                )
            }
            self.assertTrue(
                any('黒／ワイン' in a or '黒/ワイン' in a for a in aliases),
                aliases,
            )

            # 再取込・再照合でも同じ分類。商品は増えない
            preview2 = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sl.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g2 = next(x for x in preview2['skuGroups'] if x['code'] == 'SL-100')
            self.assertEqual(g2.get('plannedRegisteredName'), 'エプロン')
            self.assertEqual(g2.get('plannedRegisteredColor'), 'ブラック×ワイン')
            result2 = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sl.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'pl-slash-colorway-2',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result2['stockUnchanged'])
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) AS n FROM products "
                    "WHERE code='SL-100' AND merged_into_id IS NULL"
                ).fetchone()['n'],
                1,
            )
        finally:
            c.close()
            folder.cleanup()

    def test_manual_and_color_split_override_slash_colorway(self):
        """手動上書き・色別に分けて登録は配色自動より優先。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SL-200,エプロン（黒／ワイン）,M',
                '顧客A,仕入B,SL-200,エプロン（紺）,L',
            ])
            aux = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', 'エプロン（黒／ワイン）',
                    'SL-200', 'M', '4',
                ),
                opening_line(
                    '仕入B', '顧客A', 'エプロン（紺）',
                    'SL-200', 'L', '0',
                ),
            ])
            # 色分割を明示 → 自動配色まとめをしない
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sl2.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'skuDecisions': {'SL-200': {'decision': 'color_split'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SL-200')
            self.assertTrue(
                g.get('colorSplit')
                or (g.get('decision') or {}).get('decision') == 'color_split'
            )
            self.assertTrue(
                g.get('excludeAuxStockAutoMerge')
                or g.get('auxStockPickManualOverride')
            )

            # 行の手動色上書き（CSVヘッダ行の次が rowNo=2）
            preview2 = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sl2.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'rowClassifications': {
                    '2': {
                        'plannedName': 'エプロン特注',
                        'color': 'カスタム赤',
                        'userEdited': True,
                        'confirmed': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            by_no = {r['rowNo']: r for r in preview2['rows']}
            pc = by_no[2].get('parenClassification') or {}
            self.assertEqual(pc.get('plannedName'), 'エプロン特注')
            self.assertEqual(pc.get('color'), 'カスタム赤')
            self.assertTrue(pc.get('userEdited'))
        finally:
            c.close()
            folder.cleanup()

    def test_undecided_supplier_still_blocks_not_slash(self):
        """配色だけでは個別確認に残さない。他ブロッカー（仕入未決）は残す。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SL-300,エプロン（黒／ワイン）,M',
                '顧客A,仕入B,SL-300,エプロン（黒／ワイン）,L',
            ])
            aux = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', 'エプロン（黒／ワイン）',
                    'SL-300', 'M', '2',
                ),
                opening_line(
                    '仕入B', '顧客A', 'エプロン（黒／ワイン）',
                    'SL-300', 'L', '2',
                ),
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'sl3.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SL-300')
            reason_text = ' '.join(
                (r.get('label') if isinstance(r, dict) else str(r)) or ''
                for r in (g.get('reviewReasons') or [])
            )
            # 色が複数だから、では残さない
            self.assertNotIn('括弧内の色が複数', reason_text)
            by_no = {r['rowNo']: r for r in preview['rows']}
            pc_notices = []
            for r in by_no.values():
                if (r.get('csv') or {}).get('code') != 'SL-300':
                    continue
                pc = r.get('parenClassification') or {}
                self.assertFalse(pc.get('needsConfirm'), pc)
                self.assertEqual(pc.get('color'), 'ブラック×ワイン')
                pc_notices.extend(pc.get('infoNotices') or [])
            self.assertTrue(
                any('配色として1商品で登録' in n for n in pc_notices),
                pc_notices,
            )
            # 仕入が複数で未決なら要判断は残り得る（配色以外の理由）
            if g.get('needsDecision'):
                self.assertTrue(
                    any(k in reason_text for k in ('仕入', '在庫', '候補')),
                    reason_text,
                )
        finally:
            c.close()
            folder.cleanup()


class HalfwidthKanaColorwayUnifyTest(unittest.TestCase):
    """半角カナ表記統一とスラッシュ配色の位置ずれ修正。テストDBのみ。"""

    def test_unify_import_notation_kana_space_sku_safe(self):
        import product_ledger_paren as pp
        self.assertEqual(pp.unify_import_notation('ｴﾌﾟﾛﾝ'), 'エプロン')
        self.assertEqual(pp.unify_import_notation('長袖ﾌﾞﾙｿﾞﾝ'), '長袖ブルゾン')
        # 濁点・半濁点合成
        self.assertEqual(pp.unify_import_notation('ｶﾞｷﾞｸﾞｹﾞｺﾞ'), 'ガギグゲゴ')
        self.assertEqual(pp.unify_import_notation('ﾊﾟﾋﾟﾌﾟﾍﾟﾎﾟ'), 'パピプペポ')
        # 全角空白→半角・連続畳み
        self.assertEqual(pp.unify_import_notation('エプロン　  紺'), 'エプロン 紺')
        self.assertEqual(pp.unify_import_notation('エプロン　  ネイビー'), 'エプロン ネイビー')
        # SKU・先頭ゼロ・ASCII は全角化しない（全体 NFKC しない）
        self.assertEqual(pp.unify_import_notation('00008'), '00008')
        self.assertEqual(pp.unify_import_notation('SKU-001'), 'SKU-001')
        self.assertEqual(pp.unify_import_notation('ＡＢＣ'), 'ＡＢＣ')
        # 冪等
        self.assertEqual(
            pp.unify_import_notation(pp.unify_import_notation('ﾁｬｺｰﾙ')),
            'チャコール',
        )

    def test_halfwidth_slash_colorway_same_as_fullwidth(self):
        import product_ledger_paren as pp
        cases = (
            ('エプロン(ﾁｬｺｰﾙ/ｸﾞﾚｰ)', 'エプロン', 'チャコール×グレー'),
            ('エプロン(チャコール/グレー)', 'エプロン', 'チャコール×グレー'),
            ('エプロン(ﾌﾞﾗｳﾝ/ﾍﾞｰｼﾞｭ)', 'エプロン', 'ブラウン×ベージュ'),
            ('エプロン(ブラウン/ベージュ)', 'エプロン', 'ブラウン×ベージュ'),
            ('エプロン（ﾁｬｺｰﾙ／ｸﾞﾚｰ）', 'エプロン', 'チャコール×グレー'),
            ('ブルゾン(紺/白/赤)', 'ブルゾン', 'ネイビー×ホワイト×レッド'),
        )
        for original, name, color in cases:
            with self.subTest(original=original):
                r = pp.split_name_and_color(original)
                self.assertFalse(r['needsConfirm'], r)
                self.assertTrue(r['autoClassified'], r)
                self.assertEqual(r['originalName'], original)
                self.assertEqual(r['plannedName'], name)
                self.assertEqual(r['color'], color)
                self.assertEqual(
                    r.get('changeSummary'),
                    '商品名から配色を抽出。1商品として登録',
                )
                self.assertIn(
                    f'配色として1商品で登録：{color}',
                    r.get('infoNotices') or [],
                )

    def test_positions_use_same_normalized_string(self):
        """変換前後の位置を混ぜない。濁点で長さが変わる半角カナでも配色1色。"""
        import product_ledger_paren as pp
        raw = 'ﾁｬｺｰﾙ/ｸﾞﾚｰ'
        # 変換前 len と変換後 len が異なることが前提（バグ再現条件）
        self.assertNotEqual(len(raw), len(pp.unify_import_notation(raw)))
        hits = pp.find_explicit_colors_in_text(raw)
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(hits[0].get('isColorway'), hits)
        self.assertEqual(hits[0]['value'], 'チャコール×グレー')
        pure = pp._classify_as_pure_color_span(raw)
        self.assertIsNotNone(pure)
        self.assertEqual(pure['value'], 'チャコール×グレー')

    def test_name_without_slash_still_unifies(self):
        import product_ledger_paren as pp
        r = pp.split_name_and_color('ｴﾌﾟﾛﾝ')
        self.assertEqual(r['plannedName'], 'エプロン')
        self.assertEqual(r['color'], '')
        self.assertFalse(r['needsConfirm'])
        r2 = pp.split_name_and_color('長袖ﾌﾞﾙｿﾞﾝ')
        self.assertEqual(r2['plannedName'], '長袖ブルゾン')
        outside = pp.split_name_and_color('ﾌﾞﾙｿﾞﾝ ﾈｲﾋﾞｰ')
        self.assertEqual(outside['plannedName'], 'ブルゾン')
        self.assertEqual(outside['color'], 'ネイビー')

    def test_times_colorway_and_mixed_non_color_unaffected(self):
        import product_ledger_paren as pp
        times = pp.split_name_and_color('長袖ブルゾン（サックス×シルバーグレー）')
        self.assertFalse(times['needsConfirm'], times)
        self.assertEqual(times['plannedName'], '長袖ブルゾン')
        self.assertEqual(times['color'], 'サックス×シルバーグレー')
        mixed = pp.split_name_and_color('エプロン（黒／代替品）')
        self.assertEqual(mixed['color'], 'ブラック')
        self.assertEqual(mixed['plannedName'], 'エプロン（代替品）')

    def test_preview_register_halfwidth_colorway_one_product(self):
        """半角カナ配色: preview=register。1商品。別名に原文。在庫二重計上なし。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,HW-100,エプロン(ﾁｬｺｰﾙ/ｸﾞﾚｰ),M',
                '顧客A,仕入A,HW-100,エプロン(チャコール/グレー),L',
            ])
            aux = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', 'エプロン(ﾁｬｺｰﾙ/ｸﾞﾚｰ)',
                    'HW-100', 'M', '3',
                ),
                opening_line(
                    '仕入A', '顧客A', 'エプロン(チャコール/グレー)',
                    'HW-100', 'L', '2',
                ),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'hw.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'HW-100')
            self.assertEqual(g.get('plannedRegisteredName'), 'エプロン')
            self.assertEqual(g.get('plannedRegisteredColor'), 'チャコール×グレー')
            by_no = {r['rowNo']: r for r in preview['rows']}
            notices = []
            for r in by_no.values():
                if (r.get('csv') or {}).get('code') != 'HW-100':
                    continue
                pc = r.get('parenClassification') or {}
                self.assertFalse(pc.get('needsConfirm'), pc)
                self.assertEqual(pc.get('plannedName'), 'エプロン')
                self.assertEqual(pc.get('color'), 'チャコール×グレー')
                notices.extend(pc.get('infoNotices') or [])
            self.assertTrue(
                any('配色として1商品で登録：チャコール×グレー' in n for n in notices),
                notices,
            )
            # 半角行の原文は残る
            self.assertEqual(
                (by_no[2].get('parenClassification') or {}).get('originalName'),
                'エプロン(ﾁｬｺｰﾙ/ｸﾞﾚｰ)',
            )

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'hw.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-halfwidth-colorway-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            products = c.execute(
                "SELECT id,name,color FROM products "
                "WHERE code='HW-100' AND merged_into_id IS NULL"
            ).fetchall()
            self.assertEqual(len(products), 1)
            self.assertEqual(products[0]['name'], 'エプロン')
            self.assertEqual(products[0]['color'], 'チャコール×グレー')
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases "
                    "WHERE kind='name' AND product_id=?",
                    (products[0]['id'],),
                )
            }
            self.assertTrue(
                any('ﾁｬｺｰﾙ' in a or 'チャコール' in a for a in aliases),
                aliases,
            )

            # 初期在庫ステージ: 原文キーでマップ照合。unify で行・数量が増えない
            opening = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o2.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = (opening.get('openingStock') or {}).get('plans') or []
            qty_vals = []
            for p in plans:
                q = p.get('qty')
                if q is None and isinstance(p.get('opening'), dict):
                    q = p['opening'].get('qty')
                if q is not None:
                    qty_vals.append(int(q))
            self.assertEqual(len(qty_vals), 2, plans)
            self.assertEqual(sum(qty_vals), 5, plans)
        finally:
            c.close()
            folder.cleanup()


class ColorSynonymUnifyTest(unittest.TestCase):
    """色表記の共有正規化（辞書＋配色区切り）。テストDBのみ・本番未変更。"""

    CODE = 'CSU-6801'

    def test_split_violet_synonyms_and_asterisk_colorway(self):
        import product_ledger_paren as pp
        cases = (
            ('長袖ブルゾン ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ*ﾈｲﾋﾞｰ', '長袖ブルゾン', 'バイオレット×ネイビー'),
            ('長袖ブルゾン バイオレット×ネイビー', '長袖ブルゾン', 'バイオレット×ネイビー'),
            ('長袖ブルゾン（ミストバイオレット×ネイビー）', '長袖ブルゾン', 'バイオレット×ネイビー'),
            ('長袖ブルゾン バイオレッド×ネイビー', '長袖ブルゾン', 'バイオレット×ネイビー'),
        )
        for original, name, color in cases:
            with self.subTest(original=original):
                r = pp.split_name_and_color(original)
                self.assertFalse(r['needsConfirm'], r)
                self.assertEqual(r['plannedName'], name)
                self.assertEqual(r['color'], color)
                # 配色の半分を名に残さない
                self.assertNotIn('バイオ', r['plannedName'])
                self.assertNotIn('ネイビー', r['plannedName'])
                self.assertNotIn('ミスト', r['plannedName'])
        self.assertTrue(
            pp.colors_equivalent('ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ*ﾈｲﾋﾞｰ', 'バイオレット×ネイビー')
        )
        self.assertFalse(pp.colors_equivalent('ブルー', 'ライトブルー'))

    def test_no_mechanical_mist_strip_or_similarity_merge(self):
        import product_ledger_paren as pp
        # 「ミスト」を機械除去しない（辞書に無いミスト◯◯は別名扱いにしない）
        mist_only = pp.apply_color_synonyms_in_text('ミストグレー')
        self.assertEqual(mist_only, 'ミストグレー')
        # 類似度だけでは寄せない（辞書外の近傍表記）
        self.assertFalse(pp.colors_equivalent('バイオレット', 'バイオラット'))
        light = pp.split_name_and_color('エプロン ライトブルー')
        blue = pp.split_name_and_color('エプロン ブルー')
        self.assertEqual(light['color'], 'ライトブルー')
        self.assertEqual(blue['color'], 'ブルー')
        self.assertFalse(pp.colors_equivalent(light['color'], blue['color']))

    def test_leading_used_mark_not_colorway_bridge(self):
        import product_ledger_paren as pp
        r = pp.split_name_and_color('＊長袖ブルゾン バイオレット×ネイビー')
        self.assertFalse(r['needsConfirm'], r)
        self.assertEqual(r['plannedName'], '＊長袖ブルゾン')
        self.assertEqual(r['color'], 'バイオレット×ネイビー')

    def test_vote_key_and_core_unify_synonyms(self):
        a = '長袖ブルゾン ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ*ﾈｲﾋﾞｰ'
        b = '長袖ブルゾン バイオレット×ネイビー'
        c = '＊長袖ブルゾン ミストバイオレッド*ネイビー'
        key = pli.normalize_ledger_name_for_vote(a)
        self.assertEqual(key, '長袖ブルゾン バイオレット×ネイビー')
        self.assertEqual(pli.normalize_ledger_name_for_vote(b), key)
        self.assertEqual(pli.normalize_ledger_name_for_vote(c), key)
        self.assertEqual(
            pli.product_name_core_key(a),
            pli.product_name_core_key(b),
        )

    def test_preview_register_opening_stock_correspondence(self):
        """商品登録→初期在庫: 旧表記が永続対応で統一商品へ。UIは統一名/色のみ。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                f'顧客A,仕入A,{self.CODE},長袖ブルゾン　ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ*ﾈｲﾋﾞｰ,S',
                f'顧客A,仕入A,{self.CODE},長袖ブルゾン　ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ*ﾈｲﾋﾞｰ,M',
                f'顧客B,仕入A,{self.CODE},長袖ブルゾン　バイオレット×ネイビー,L',
                f'顧客B,仕入A,{self.CODE},長袖ブルゾン　バイオレット×ネイビー,LL',
            ])
            aux = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', '長袖ブルゾン　ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ*ﾈｲﾋﾞｰ',
                    self.CODE, 'S', '3',
                ),
                opening_line(
                    '仕入A', '顧客A', '長袖ブルゾン　ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ*ﾈｲﾋﾞｰ',
                    self.CODE, 'M', '2',
                ),
                opening_line(
                    '仕入A', '顧客B', '長袖ブルゾン　バイオレット×ネイビー',
                    self.CODE, 'L', '4',
                ),
                opening_line(
                    '仕入A', '顧客B', '長袖ブルゾン　バイオレット×ネイビー',
                    self.CODE, 'LL', '1',
                ),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'violet.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            reason_codes = {r.get('code') for r in (g.get('reviewReasons') or [])}
            self.assertNotIn('color', reason_codes)
            self.assertEqual(
                g.get('plannedRegisteredName')
                or (g.get('auxPrimary') or {}).get('plannedRegisteredName'),
                '長袖ブルゾン',
            )
            self.assertEqual(
                g.get('plannedRegisteredColor')
                or (g.get('auxPrimary') or {}).get('plannedRegisteredColor'),
                'バイオレット×ネイビー',
            )
            # 通常プレビューに変換前色の字幕リストを増やさない（planned は統一のみ）
            for row in preview['rows']:
                if (row.get('csv') or {}).get('code') != self.CODE:
                    continue
                pc = row.get('parenClassification') or {}
                self.assertEqual(pc.get('plannedName'), '長袖ブルゾン')
                self.assertEqual(pc.get('color'), 'バイオレット×ネイビー')
                self.assertNotIn('preConversionNames', pc)
                self.assertNotIn('legacyColorList', pc)

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'violet.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-color-synonym-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            products = c.execute(
                "SELECT id,name,color FROM products "
                "WHERE code=? AND merged_into_id IS NULL",
                (self.CODE,),
            ).fetchall()
            self.assertEqual(len(products), 1, products)
            self.assertEqual(products[0]['name'], '長袖ブルゾン')
            self.assertEqual(products[0]['color'], 'バイオレット×ネイビー')
            pid = products[0]['id']
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases "
                    "WHERE kind='name' AND product_id=?",
                    (pid,),
                )
            }
            self.assertTrue(
                any('ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ' in a or 'ミストバイオ' in a for a in aliases),
                aliases,
            )
            maps = list(c.execute(
                'SELECT csv_product_name,csv_color,csv_customer,csv_size,'
                'product_id,customer_product_id,spec_json '
                'FROM product_ledger_row_maps WHERE csv_product_code=?',
                (self.CODE,),
            ))
            self.assertGreaterEqual(len(maps), 4, maps)
            for m in maps:
                self.assertEqual(m['product_id'], pid)
                self.assertTrue(m['customer_product_id'])
                # 登録色は統一表記。変換前は spec_json（内部照合用）
                self.assertEqual(m['csv_color'], 'バイオレット×ネイビー')
                spec = m['spec_json'] or ''
                self.assertIn('sourceName', spec)
                self.assertIn('sourceColor', spec)

            # 初期在庫: 旧表記CSVが永続マップで同一商品・取扱・サイズへ。数量二重なし
            opening = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o2.csv',
                'basisDate': '2024-04-01',
                'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = (opening.get('openingStock') or {}).get('plans') or []
            self.assertTrue((opening.get('openingStock') or {}).get('fromPersistedMap'))
            qty_by_size = {}
            for p in plans:
                size = (p.get('csv') or {}).get('size') or ''
                q = p.get('qty')
                if q is None and isinstance(p.get('opening'), dict):
                    q = p['opening'].get('qty')
                self.assertIsNotNone(q, p)
                qty_by_size[size] = int(q)
                tgt = p.get('target') or {}
                self.assertEqual(tgt.get('productId'), pid, p)
                # 取扱は handleId（customer_product）。mapResolve にも customerProductId
                handle_id = tgt.get('handleId') or (
                    ((p.get('mapResolve') or {}).get('destination') or {})
                    .get('customerProductId')
                )
                self.assertTrue(handle_id, p)
                self.assertEqual(tgt.get('color'), 'バイオレット×ネイビー', p)
            self.assertEqual(qty_by_size, {'S': 3, 'M': 2, 'L': 4, 'LL': 1})
            self.assertEqual(sum(qty_by_size.values()), 10)

            # 旧表記だけでも解決（別表記マップとの color-normalize 対応）
            aux_old_only = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客B', '長袖ブルゾン　ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ*ﾈｲﾋﾞｰ',
                    self.CODE, 'L', '7',
                ),
            ])
            opening2 = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': csv_b64_from_text(aux_old_only), 'auxName': 'o3.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans2 = (opening2.get('openingStock') or {}).get('plans') or []
            self.assertEqual(len(plans2), 1, plans2)
            self.assertEqual((plans2[0].get('target') or {}).get('productId'), pid)
            self.assertEqual(
                (plans2[0].get('mapResolve') or {}).get('status'),
                'ok',
                plans2[0],
            )
        finally:
            c.close()
            folder.cleanup()

    def test_different_sku_and_color_split_not_merged(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                '顧客A,仕入A,SKU-A,長袖ブルゾン　バイオレット×ネイビー,M',
                '顧客A,仕入A,SKU-B,長袖ブルゾン　バイオレット×ネイビー,M',
                '顧客A,仕入A,SPLIT-1,エプロン（黒）,M',
                '顧客A,仕入A,SPLIT-1,エプロン（白）,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'nosku.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            by_code = {x['code']: x for x in preview['skuGroups']}
            self.assertIn('SKU-A', by_code)
            self.assertIn('SKU-B', by_code)
            # 色統一はSKUをまたがない
            self.assertEqual(by_code['SKU-A'].get('plannedRegisteredColor')
                             or 'バイオレット×ネイビー', 'バイオレット×ネイビー')
            # 黒/白は別色 → 色差の個別確認が残る（自動で1色に寄せない）
            split = by_code['SPLIT-1']
            if split.get('needsDecision'):
                codes = {r.get('code') for r in (split.get('reviewReasons') or [])}
                self.assertTrue(
                    'color' in codes or split.get('colorDiffPossible'),
                    split.get('reviewReasons'),
                )
        finally:
            c.close()
            folder.cleanup()

    def test_slash_and_leoc_unaffected(self):
        """#185 スラッシュ配色・#187 LEOC スコープ補正は維持。"""
        import product_ledger_paren as pp
        slash = pp.split_name_and_color('エプロン（黒／ワイン）')
        self.assertEqual(slash['color'], 'ブラック×ワイン')
        self.assertEqual(slash['plannedName'], 'エプロン')
        folder, path, c = newdb()
        try:
            import product_ledger_scoped_color as scoped
            plan = pli.build_paren_classification('ピンクエプロン (胸当・ﾋﾟﾝｸ)')
            # ピンク別注は LEOC スコープ外（顧客・仕入先条件なし）
            self.assertFalse(plan.get('scopedColorCorrection'))
            self.assertTrue(
                scoped.matches_scoped_color_correction(
                    customer='㈱ＬＥＯＣ',
                    supplier='(有)ユニスタイル',
                    code='別注',
                    product_name='エプロン(ブルー・サックス)',
                )
            )
        finally:
            c.close()
            folder.cleanup()


class ColorNameKanaUnifyTest(unittest.TestCase):
    """日本語・ひらがな・半角カナ色名 → 全角カタカナ代表。テストDBのみ。"""

    CODE = 'CNK-9101'

    def test_white_variants_unify_and_protect_compounds(self):
        import product_ledger_paren as pp
        for original in (
            'エプロン(白)',
            'エプロン しろ',
            'エプロン(ｼﾛ)',
            'エプロン(ﾎﾜｲﾄ)',
            'エプロン ホワイト',
            'エプロン（ホワイト）',
        ):
            with self.subTest(original=original):
                r = pp.split_name_and_color(original)
                self.assertFalse(r['needsConfirm'], r)
                self.assertEqual(r['plannedName'], 'エプロン')
                self.assertEqual(r['color'], 'ホワイト')
        self.assertTrue(pp.colors_equivalent('白', 'ホワイト'))
        self.assertTrue(pp.colors_equivalent('しろ', 'ﾎﾜｲﾄ'))
        self.assertTrue(pp.colors_equivalent('くろ', 'ブラック'))
        self.assertTrue(pp.colors_equivalent('あか', 'レッド'))
        self.assertTrue(pp.colors_equivalent('あお', 'ブルー'))
        # 商品語の一部は壊さない
        hakui = pp.split_name_and_color('白衣')
        self.assertEqual(hakui['plannedName'], '白衣')
        self.assertEqual(hakui['color'], '')
        infra = pp.split_name_and_color('赤外線センサー')
        self.assertEqual(infra['plannedName'], '赤外線センサー')
        self.assertEqual(infra['color'], '')
        self.assertEqual(pp.apply_color_synonyms_in_text('白衣'), '白衣')
        # 別色は無条件統合しない
        self.assertFalse(pp.colors_equivalent('ブルー', 'サックス'))
        self.assertFalse(pp.colors_equivalent('ブルー', 'ライトブルー'))
        self.assertFalse(pp.colors_equivalent('グリーン', '蛍光グリーン'))
        # 配色の成分は欠落させず代表へ
        way = pp.split_name_and_color('エプロン（黒／ワイン）')
        self.assertEqual(way['color'], 'ブラック×ワイン')
        self.assertEqual(way['plannedName'], 'エプロン')

    def test_notation_only_does_not_block_review(self):
        """白／しろ／半角ホワイトだけの差では色競合の確認待ちにしない。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                f'顧客A,仕入A,{self.CODE},エプロン(白),M',
                f'顧客A,仕入A,{self.CODE},エプロン しろ,L',
                f'顧客B,仕入A,{self.CODE},エプロン(ﾎﾜｲﾄ),LL',
                f'顧客B,仕入A,{self.CODE},エプロン ホワイト,S',
            ])
            aux = tiny_opening_rows([
                opening_line('仕入A', '顧客A', 'エプロン(白)', self.CODE, 'M', '2'),
                opening_line('仕入A', '顧客A', 'エプロン しろ', self.CODE, 'L', '3'),
                opening_line('仕入A', '顧客B', 'エプロン(ﾎﾜｲﾄ)', self.CODE, 'LL', '4'),
                opening_line('仕入A', '顧客B', 'エプロン ホワイト', self.CODE, 'S', '1'),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'white.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            reason_codes = {r.get('code') for r in (g.get('reviewReasons') or [])}
            self.assertNotIn('color', reason_codes)
            self.assertEqual(
                g.get('plannedRegisteredName')
                or (g.get('auxPrimary') or {}).get('plannedRegisteredName'),
                'エプロン',
            )
            self.assertEqual(
                g.get('plannedRegisteredColor')
                or (g.get('auxPrimary') or {}).get('plannedRegisteredColor'),
                'ホワイト',
            )
            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'white.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-color-kana-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            products = c.execute(
                "SELECT id,name,color FROM products "
                "WHERE code=? AND merged_into_id IS NULL",
                (self.CODE,),
            ).fetchall()
            self.assertEqual(len(products), 1, products)
            self.assertEqual(products[0]['name'], 'エプロン')
            self.assertEqual(products[0]['color'], 'ホワイト')

            # 色別登録でも白とホワイトを二重にしない
            split = pli.build_color_split_plans(
                c,
                {
                    'code': self.CODE,
                    'rowNos': [1, 2],
                    'variants': [
                        {'productName': 'エプロン(白)', 'rowNos': [1],
                         'csvNames': ['エプロン(白)'], 'supplier': '仕入A',
                         'sizes': ['M'], 'customers': ['顧客A']},
                        {'productName': 'エプロン ホワイト', 'rowNos': [2],
                         'csvNames': ['エプロン ホワイト'], 'supplier': '仕入A',
                         'sizes': ['L'], 'customers': ['顧客A']},
                    ],
                },
                {'decision': 'color_split'},
                classified=[
                    {'rowNo': 1, 'csv': {
                        'code': self.CODE, 'productName': 'エプロン(白)',
                        'supplier': '仕入A', 'size': 'M', 'customer': '顧客A',
                    }},
                    {'rowNo': 2, 'csv': {
                        'code': self.CODE, 'productName': 'エプロン ホワイト',
                        'supplier': '仕入A', 'size': 'L', 'customer': '顧客A',
                    }},
                ],
            )
            self.assertEqual(len(split['plans']), 1, split)
            self.assertEqual(split['plans'][0]['color'], 'ホワイト')
            self.assertEqual(sorted(split['plans'][0]['rowNos']), [1, 2])

            # 初期在庫: 旧表記から同一商品へ。数量二重なし
            opening = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o2.csv',
                'basisDate': '2024-04-01',
                'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = (opening.get('openingStock') or {}).get('plans') or []
            self.assertTrue((opening.get('openingStock') or {}).get('fromPersistedMap'))
            qty_by_size = {}
            pid = products[0]['id']
            for p in plans:
                size = (p.get('csv') or {}).get('size') or ''
                q = p.get('qty')
                if q is None and isinstance(p.get('opening'), dict):
                    q = p['opening'].get('qty')
                qty_by_size[size] = int(q)
                self.assertEqual((p.get('target') or {}).get('productId'), pid, p)
                self.assertEqual((p.get('target') or {}).get('color'), 'ホワイト', p)
            self.assertEqual(qty_by_size, {'M': 2, 'L': 3, 'LL': 4, 'S': 1})
            self.assertEqual(sum(qty_by_size.values()), 10)
        finally:
            c.close()
            folder.cleanup()


class ColorwaySeparatorUnifyTest(unittest.TestCase):
    """配色区切り（/／×✕*＊）を × へ共通統一。テストDBのみ・本番未変更。"""

    CODE = 'BW6501-1'

    def test_separator_variants_same_colorway(self):
        import product_ledger_paren as pp
        variants = (
            '白／黒',
            '白/黒',
            '白×黒',
            '白✕黒',
            '白*黒',
            '白＊黒',
            'ﾎﾜｲﾄ*ﾌﾞﾗｯｸ',
            'ホワイト×ブラック',
        )
        for raw in variants:
            with self.subTest(raw=raw):
                self.assertTrue(pp.looks_like_colorway_text(raw), raw)
                self.assertEqual(pp.normalize_color_value(raw), 'ホワイト×ブラック')
                self.assertTrue(pp.colors_equivalent(raw, 'ホワイト×ブラック'))
        # 3色・順序維持
        self.assertEqual(
            pp.normalize_color_value('紺／白／赤'),
            'ネイビー×ホワイト×レッド',
        )
        self.assertEqual(
            pp.normalize_color_value('赤／白／紺'),
            'レッド×ホワイト×ネイビー',
        )
        self.assertFalse(
            pp.colors_equivalent('紺／白／赤', '赤／白／紺'),
            '配色順序を入れ替えない',
        )

    def test_color_plus_size_or_processing_not_colorway(self):
        import product_ledger_paren as pp
        self.assertFalse(pp.looks_like_colorway_text('ホワイト／フリー'))
        self.assertFalse(pp.looks_like_colorway_text('ホワイト／刺繍'))
        self.assertEqual(pp.normalize_color_value('ホワイト／フリー'), 'ホワイト／フリー')
        self.assertEqual(pp.normalize_color_value('ホワイト／刺繍'), 'ホワイト／刺繍')
        # 名比較キーでも色+非色の区切りは触らない
        self.assertIn('／', pp.normalize_name_for_color_compare('エプロン ホワイト／フリー'))
        self.assertNotIn('×', pp.normalize_name_for_color_compare('エプロン ホワイト／刺繍'))

    def test_leading_used_mark_distinct_from_colorway_star(self):
        import product_ledger_paren as pp
        r = pp.split_name_and_color('＊コックコート 白＊黒')
        self.assertFalse(r['needsConfirm'], r)
        self.assertEqual(r['plannedName'], '＊コックコート')
        self.assertEqual(r['color'], 'ホワイト×ブラック')
        # 比較キーは中古標記を除き配色を統一
        self.assertEqual(
            pp.normalize_name_for_color_compare('＊コックコート 白／黒'),
            pp.normalize_name_for_color_compare('コックコート 白×黒'),
        )

    def test_bw6501_1_preview_one_product_no_review(self):
        """品番 BW6501-1: 白／黒 と 白×黒 は1商品。表記差だけの確認待ちにしない。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                f'顧客A,仕入A,{self.CODE},コックコート 白／黒,M',
                f'顧客A,仕入A,{self.CODE},コックコート 白×黒,L',
                f'顧客B,仕入A,{self.CODE},コックコート ﾎﾜｲﾄ*ﾌﾞﾗｯｸ,LL',
            ])
            # 在庫情報なしでも区切り差だけでまとまる
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'bw6501.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            reason_codes = {r.get('code') for r in (g.get('reviewReasons') or [])}
            self.assertNotIn('color', reason_codes)
            self.assertNotIn('name_wording', reason_codes)
            self.assertEqual(
                g.get('plannedRegisteredName')
                or (g.get('auxPrimary') or {}).get('plannedRegisteredName'),
                'コックコート',
            )
            self.assertEqual(
                g.get('plannedRegisteredColor')
                or g.get('plannedColor')
                or (g.get('auxPrimary') or {}).get('plannedRegisteredColor'),
                'ホワイト×ブラック',
            )
            # 商品名に配色を残さない
            for row in preview['rows']:
                if (row.get('csv') or {}).get('code') != self.CODE:
                    continue
                pc = row.get('parenClassification') or {}
                self.assertEqual(pc.get('plannedName'), 'コックコート', row)
                self.assertEqual(pc.get('color'), 'ホワイト×ブラック', row)
                self.assertNotIn('白', pc.get('plannedName') or '')
                self.assertNotIn('黒', pc.get('plannedName') or '')
            self.assertEqual(stock_snap(c), before)
        finally:
            c.close()
            folder.cleanup()

    def test_bw6501_1_register_and_opening_map(self):
        """登録色は ×。旧表記の初期在庫CSVも同一商品へ照合。"""
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                f'顧客A,仕入A,{self.CODE},コックコート 白／黒,M',
                f'顧客A,仕入A,{self.CODE},コックコート 白×黒,L',
            ])
            aux = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', 'コックコート 白／黒',
                    self.CODE, 'M', '3',
                ),
                opening_line(
                    '仕入A', '顧客A', 'コックコート 白×黒',
                    self.CODE, 'L', '2',
                ),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'bw.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertEqual(g.get('plannedRegisteredColor'), 'ホワイト×ブラック')

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'bw.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-bw6501-sep-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            products = c.execute(
                "SELECT id,name,color FROM products "
                "WHERE code=? AND merged_into_id IS NULL",
                (self.CODE,),
            ).fetchall()
            self.assertEqual(len(products), 1, products)
            self.assertEqual(products[0]['name'], 'コックコート')
            self.assertEqual(products[0]['color'], 'ホワイト×ブラック')
            pid = products[0]['id']
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases "
                    "WHERE kind='name' AND product_id=?",
                    (pid,),
                )
            }
            self.assertTrue(
                any('白／黒' in a or '白×黒' in a for a in aliases),
                aliases,
            )
            maps = list(c.execute(
                'SELECT csv_product_name,csv_color,product_id,spec_json '
                'FROM product_ledger_row_maps WHERE csv_product_code=?',
                (self.CODE,),
            ))
            self.assertGreaterEqual(len(maps), 2, maps)
            for m in maps:
                self.assertEqual(m['product_id'], pid)
                self.assertEqual(m['csv_color'], 'ホワイト×ブラック')
                spec = m['spec_json'] or ''
                self.assertIn('sourceName', spec)

            opening = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'o2.csv',
                'basisDate': '2024-04-01',
                'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = (opening.get('openingStock') or {}).get('plans') or []
            self.assertTrue((opening.get('openingStock') or {}).get('fromPersistedMap'))
            qty_by_size = {}
            for p in plans:
                size = (p.get('csv') or {}).get('size') or ''
                q = p.get('qty')
                if q is None and isinstance(p.get('opening'), dict):
                    q = p['opening'].get('qty')
                self.assertIsNotNone(q, p)
                qty_by_size[size] = int(q)
                tgt = p.get('target') or {}
                self.assertEqual(tgt.get('productId'), pid, p)
                self.assertEqual(tgt.get('color'), 'ホワイト×ブラック', p)
            self.assertEqual(qty_by_size, {'M': 3, 'L': 2})
            self.assertEqual(sum(qty_by_size.values()), 5)

            # 後日の旧表記のみCSVでも同一商品へ
            aux_old = tiny_opening_rows([
                opening_line(
                    '仕入A', '顧客A', 'コックコート 白／黒',
                    self.CODE, 'M', '7',
                ),
            ])
            opening2 = pli.build_preview(c, {
                'importMode': 'opening_stock',
                'auxFile': csv_b64_from_text(aux_old), 'auxName': 'o3.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans2 = (opening2.get('openingStock') or {}).get('plans') or []
            self.assertEqual(len(plans2), 1, plans2)
            self.assertEqual((plans2[0].get('target') or {}).get('productId'), pid)
            self.assertEqual(
                (plans2[0].get('mapResolve') or {}).get('status'),
                'ok',
                plans2[0],
            )
        finally:
            c.close()
            folder.cleanup()

    def test_manual_color_split_not_overridden(self):
        folder, path, c = newdb()
        try:
            ledger = tiny_ledger_rows([
                f'顧客A,仕入A,{self.CODE},コックコート 白／黒,M',
                f'顧客A,仕入A,{self.CODE},コックコート 白×黒,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(ledger), 'name': 'split.csv',
                'skuDecisions': {self.CODE: {'decision': 'color_split'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == self.CODE)
            self.assertTrue(
                g.get('colorSplit')
                or (g.get('decision') or {}).get('decision') == 'color_split'
            )
            self.assertTrue(
                g.get('excludeAuxStockAutoMerge')
                or g.get('auxStockPickManualOverride')
                or (g.get('decision') or {}).get('decision') == 'color_split'
            )
        finally:
            c.close()
            folder.cleanup()


class SynonymColorOmissionMergeTest(unittest.TestCase):
    """帽子／キャップ同義・括弧色・色省略の共通まとめ（OV9001-8 相当）。"""

    def test_ov9001_8_three_names_merge_to_cap_charcoal(self):
        folder, path, c = newdb()
        try:
            supplier = '住商モンブラン㈱'
            code = 'OV9001-8'
            text = tiny_ledger_rows([
                f'顧客A,{supplier},{code},キャップ（ﾁｬｺｰﾙ）,F',
                f'顧客A,{supplier},{code},キャップ,F',
                f'顧客B,{supplier},{code},帽子 チャコール,L',
            ])
            # 全行在庫0でも表記差だけで確認待ちにしない
            aux = tiny_opening_rows([
                opening_line(supplier, '顧客A', 'キャップ（ﾁｬｺｰﾙ）', code, 'F', '0'),
                opening_line(supplier, '顧客A', 'キャップ', code, 'F', '0'),
                opening_line(supplier, '顧客B', '帽子 チャコール', code, 'L', '0'),
            ])
            before = stock_snap(c)
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'ov.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'ov-open.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(stock_snap(c), before)
            g = next(x for x in preview['skuGroups'] if x['code'] == code)
            self.assertFalse(g['needsDecision'], g.get('reviewReasons'))
            self.assertTrue(g.get('plannedSingleProduct'))
            self.assertEqual(g.get('variantCount'), 1)
            self.assertEqual(
                g.get('plannedRegisteredName') or (g.get('productPlan') or {}).get('productName'),
                'キャップ',
            )
            self.assertEqual(g.get('plannedRegisteredColor'), 'チャコール')
            self.assertTrue(
                g.get('colorOmissionAutoMerged')
                or g.get('synonymAutoMerged')
                or g.get('colorNotationAutoMerged')
            )

            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'ov.csv',
                'auxFile': csv_b64_from_text(aux), 'auxName': 'ov-open.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-ov9001-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            products = list(c.execute(
                "SELECT id,name,color FROM products WHERE code=? AND merged_into_id IS NULL",
                (code,),
            ))
            self.assertEqual(len(products), 1)
            self.assertEqual(products[0]['name'], 'キャップ')
            self.assertEqual(products[0]['color'], 'チャコール')
            # 通常画面の別名一覧へ原文を出さない（内部マップで照合）
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (products[0]['id'],),
                )
            }
            for original in ('キャップ（ﾁｬｺｰﾙ）', '帽子 チャコール', 'キャップ'):
                self.assertNotIn(original, aliases)
            maps = list(c.execute(
                'SELECT csv_product_name,csv_customer,csv_size FROM product_ledger_row_maps '
                'WHERE csv_product_code=? ORDER BY source_row_no',
                (code,),
            ))
            self.assertEqual(len(maps), 3)
            map_names = {r['csv_product_name'] for r in maps}
            self.assertIn('キャップ（ﾁｬｺｰﾙ）', map_names)
            self.assertIn('帽子 チャコール', map_names)
            self.assertIn('キャップ', map_names)
        finally:
            c.close()
            folder.cleanup()

    def test_colorless_not_filled_when_multiple_explicit_colors(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-MULTI,キャップ（赤）,F',
                '顧客A,仕入A,SKU-MULTI,キャップ（青）,F',
                '顧客A,仕入A,SKU-MULTI,キャップ,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'multi.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-MULTI')
            self.assertTrue(g['needsDecision'])
            self.assertFalse(g.get('colorOmissionAutoMerged'))
            # 色なし行を赤／青へ勝手に振り分けない → バリアントが複数
            self.assertGreaterEqual(g.get('variantCount') or 0, 2)
        finally:
            c.close()
            folder.cleanup()

    def test_specific_hat_kinds_not_merged_via_synonym(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-HAT,ハンチング帽,F',
                '顧客A,仕入A,SKU-HAT,キャップ,F',
                '顧客A,仕入A,SKU-KNIT,ニット帽 黒,F',
                '顧客A,仕入A,SKU-KNIT,帽子 黒,F',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'hats.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g_hat = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-HAT')
            self.assertTrue(g_hat['needsDecision'])
            self.assertTrue(g_hat.get('kindDiffPossible') or g_hat.get('nameWordingDiff'))
            g_knit = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-KNIT')
            self.assertTrue(g_knit['needsDecision'])
        finally:
            c.close()
            folder.cleanup()

    def test_hat_only_group_not_renamed_to_cap(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-HATONLY,帽子,F',
                '顧客B,仕入A,SKU-HATONLY,帽子,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'hatonly.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-HATONLY')
            self.assertFalse(g['needsDecision'])
            name = (g.get('productPlan') or {}).get('productName') or g.get('plannedRegisteredName')
            self.assertEqual(name, '帽子')
            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'hatonly.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'pl-hatonly-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            prod = c.execute(
                "SELECT name FROM products WHERE code='SKU-HATONLY' AND merged_into_id IS NULL"
            ).fetchone()
            self.assertEqual(prod['name'], '帽子')
        finally:
            c.close()
            folder.cleanup()

    def test_charcoal_not_eaten_by_cha_brown_synonym(self):
        import product_ledger_paren as pp
        self.assertEqual(
            pp.apply_color_synonyms_in_text('キャップ(チャコール)'),
            'キャップ(チャコール)',
        )
        split = pp.split_name_and_color('キャップ（ﾁｬｺｰﾙ）')
        self.assertEqual(split.get('plannedName'), 'キャップ')
        self.assertEqual(split.get('color'), 'チャコール')


if __name__ == '__main__':
    unittest.main()
