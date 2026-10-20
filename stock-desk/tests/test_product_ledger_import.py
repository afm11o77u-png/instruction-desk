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
        self.assertIn('productLedgerCsvOpen', app)
        self.assertIn('旧商品台帳CSVを取り込む', app)
        self.assertIn('product_ledger_csv.js', html)
        self.assertIn('ProductLedgerCsv', js)
        self.assertIn('/api/product-ledger-csv/preview', js)
        self.assertIn('/api/product-ledger-csv/commit', js)
        self.assertIn('同じ商品としてまとめる', js)
        self.assertIn('別物のため個別に整理する', js)
        self.assertIn('今回は保留する', js)
        self.assertIn('skuDecisions', js)
        self.assertIn('自動まとめ（要確認ではない）', js)
        self.assertIn('自動決定済み', js)
        self.assertIn('プレビュー', js)
        self.assertIn('autoMergePlans', js)
        self.assertIn('中古標記まとめ', js)
        self.assertIn('auxFile', js)
        self.assertIn('初期在庫CSV', js)
        self.assertIn('sku-compare-table', js)
        self.assertIn('一括承認候補', js)
        self.assertIn('個別確認', js)
        self.assertIn('plSkuBulkMerge', js)
        self.assertIn('plSkuSelectFiltered', js)
        self.assertIn('名称未整理', js)
        self.assertIn('1品番=1商品', app)
        # 初期在庫用旧CSVタブとは別入口
        self.assertIn("['legacyCsv','旧CSV取込']", app)

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
            self.assertEqual(group['productPlan']['productName'], '緑つなぎ')
            self.assertEqual(group['productPlan']['sizeCount'], 3)
            self.assertEqual(group['productPlan']['customerCount'], 2)
            for r in preview['rows']:
                self.assertNotEqual(r['status'], 'needs_review')
                self.assertFalse(r.get('skuDecisionNeeded'))
                self.assertTrue(r.get('usedMarkerAutoMerged'))
                self.assertEqual(r['product'].get('officialName'), '緑つなぎ')

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
            prod = c.execute("SELECT id,name FROM products WHERE code='1702'").fetchone()
            self.assertEqual(prod['name'], '緑つなぎ')
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
            prod = c.execute("SELECT id,name FROM products WHERE code='1702'").fetchone()
            self.assertEqual(prod['name'], '緑つなぎ')
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
                '顧客B,仕入A,AZ-6850-008,パンツ ネイビー,LL',
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
            self.assertIn('初期在庫：', aux.get('pairAdoptionRule') or group.get('auxStockPickReason') or '')
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
            self.assertEqual(prod['name'], 'ベーシックカラーベスト　046.蛍光グリーン')
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

    def test_both_positive_needs_manual(self):
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
            auxj = next(g for g in preview['skuGroups'] if g['code'] == '00008')['auxPrimary']
            self.assertEqual(auxj['autoPrimarySupplier'], '')
            self.assertTrue(auxj['needsManualPrimary'])
            self.assertIn('複数', auxj['manualReason'])
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
                '顧客B,仕入A,SKU-I2,ワークパンツ,M',
                '顧客B,仕入A,SKU-I2,パンツ ネイビー,L',
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
            self.assertEqual(aux_p.get('suggestedOfficialName'), '緑つなぎ')
            self.assertEqual(aux_p.get('suggestedPrimarySupplier'), '仕入A')
            self.assertFalse(aux_p.get('officialNameNeedsChoice'))
            self.assertEqual(g.get('plannedOfficialName'), '緑つなぎ')
            rule = aux_p.get('pairAdoptionRule') or ''
            self.assertIn('在庫が0以外の候補が1組', rule)
            self.assertIn('初期在庫：3', rule)
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

    def test_aux_main_name_ambiguous_not_by_max_qty(self):
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
            self.assertTrue(g['needsDecision'])
            aux_p = g.get('auxPrimary') or {}
            # 数量最大では選ばない。非ゼロ候補が複数なら確認
            self.assertTrue(aux_p.get('officialNameNeedsChoice'))
            self.assertEqual(aux_p.get('suggestedOfficialName') or '', '')
            self.assertTrue(any('複数' in r for r in (aux_p.get('nameReasons') or [])))
            self.assertNotEqual(aux_p.get('nameAdoptionRuleCode'), 'aux_any_unique')
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
            self.assertTrue(aux_p.get('pairNeedsChoice'))
            reasons = aux_p.get('nameReasons') or []
            self.assertTrue(
                any('在庫0' in r or '非ゼロ' in r for r in reasons),
                reasons,
            )
            self.assertTrue(any('要確認' in r for r in reasons), reasons)
            self.assertTrue(g['needsDecision'])
            # 比較一覧の要確認理由に在庫0が明示される
            review_labels = [r.get('label') or '' for r in (g.get('reviewReasons') or [])]
            self.assertTrue(
                any('在庫0' in lab or '初期在庫' in lab for lab in review_labels),
                review_labels,
            )
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
            self.assertIn('初期在庫：4', g.get('auxStockPickReason') or '')
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

    def test_aux_multi_nonzero_still_needs_choice(self):
        """非ゼロが複数 → 要確認（色差の有無に関わらず）。"""
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
            self.assertTrue(g['needsDecision'])
            aux_p = g.get('auxPrimary') or {}
            self.assertTrue(aux_p.get('pairNeedsChoice'))
            self.assertTrue(any('複数' in r for r in (aux_p.get('nameReasons') or [])))
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
        self.assertIn('在庫が0以外の候補が1組', js)
        self.assertIn('auxStockPickAuto', js)
        self.assertIn('keptPackages', js)
        self.assertIn('nameCandidateDetails', js)
        self.assertIn('supplierCandidateDetails', js)
        self.assertIn('初期在庫：', js)
        self.assertIn('sku-cand-stock', js)
        self.assertIn('自動決定済み（プレビュー）', js)
        self.assertIn('対象標記の有無', js)


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
            self.assertIn('プレビュー', preview.get('note') or '')

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


class ParenClassificationTest(unittest.TestCase):
    """括弧内の色・加工・生地分類（辞書規則のみ）。"""

    def test_examples_auto(self):
        import product_ledger_paren as pp
        cases = [
            ('ポロシャツ（ネイビー）', 'ポロシャツ', 'ネイビー', '', ''),
            ('白衣（DHC刺繍あり）', '白衣', '', 'DHC刺繍あり', ''),
            ('ベスト（赤・背中プリント）', 'ベスト', '赤', '背中プリント', ''),
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

    def test_unknown_keeps_original(self):
        import product_ledger_paren as pp
        r = pp.classify_product_name('パンツ（代替品）')
        self.assertTrue(r['needsConfirm'])
        self.assertEqual(r['plannedName'], 'パンツ（代替品）')
        self.assertIn('代替品', r['unclassified'])
        self.assertFalse(r['autoClassified'])

    def test_green_not_fluorescent_green(self):
        import product_ledger_paren as pp
        r = pp.classify_product_name('ベスト（蛍光グリーン）')
        self.assertEqual(r['color'], '蛍光グリーン')
        self.assertNotEqual(r['color'], '緑')
        r2 = pp.classify_product_name('ベスト（緑）')
        self.assertEqual(r2['color'], '緑')

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
            self.assertTrue(p4['needsConfirm'])
            self.assertEqual(by_no[5]['status'], 'needs_review')

            # 代替品は確認済みにしてから登録（他は自動）
            body = {
                'file': csv_b64_from_text(text), 'name': 'paren.csv',
                'rowClassifications': {
                    '5': {
                        'plannedName': 'パンツ（代替品）',
                        'color': '', 'processing': '', 'material': '',
                        'unclassified': ['代替品'],
                        'userEdited': True, 'confirmed': True, 'acceptProposal': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview2 = pli.build_preview(c, body)
            self.assertEqual(stock_snap(c), before)
            by2 = {r['rowNo']: r for r in preview2['rows']}
            self.assertNotEqual(by2[5]['status'], 'needs_review')

            result = pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
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

            # 原文が別名に残る
            aliases = {
                r['value'] for r in c.execute(
                    "SELECT value FROM product_match_aliases WHERE kind='name' AND product_id=?",
                    (navy['id'],),
                )
            }
            self.assertIn('ポロシャツ（ネイビー）', aliases)

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
            self.assertIn('赤', colors)
            self.assertIn('緑', colors)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
