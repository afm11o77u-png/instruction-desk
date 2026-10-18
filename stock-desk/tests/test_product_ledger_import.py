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
        # 初期在庫用旧CSVタブとは別入口
        self.assertIn("['legacyCsv','旧CSV取込']", app)


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
                '病院D,仕入Z,FT2,白衣（刺繍あり）,M',  # 同品番・別名 → 要確認
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
            self.assertEqual(by_no[6]['status'], 'unchanged')  # 既存白衣
            self.assertEqual(by_no[7]['status'], 'needs_review')  # 白衣（刺繍あり）

            # 要確認ありのまま確定は拒否
            with self.assertRaises(ValueError):
                pli.commit_preview(c, {
                    'file': csv_b64_from_text(text), 'name': 'edge.csv',
                    'previewFingerprint': preview['previewFingerprint'],
                    'previewAt': preview['previewAt'],
                    'token': 'pl-edge-1',
                    '_canWrite': True, '_actor': {'name': 't'},
                }, db_path=str(path))

            # 要確認行を新規指定して再照合→確定
            selections = {
                '7': {'forceCreate': True, 'matchGroupKey': by_no[7]['matchGroupKey']},
            }
            preview2 = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'edge.csv',
                'selections': selections,
                '_canWrite': True, '_actor': {'name': 't'},
            })
            self.assertEqual(preview2['reviewCount'], 0)
            result = pli.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'edge.csv',
                'selections': selections,
                'previewFingerprint': preview2['previewFingerprint'],
                'previewAt': preview2['previewAt'],
                'token': 'pl-edge-2',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            self.assertTrue(result['stockUnchanged'])
            self.assertEqual(stock_snap(c), before)
            # ＊帽子は記号付きのまま商品名保存
            star = c.execute("SELECT name FROM products WHERE code='FT1'").fetchone()
            self.assertEqual(star['name'], '＊帽子')
            # 別注は2商品
            betchu = c.execute("SELECT COUNT(*) AS n FROM products WHERE code='別注'").fetchone()['n']
            self.assertEqual(betchu, 2)
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


if __name__ == '__main__':
    unittest.main()
