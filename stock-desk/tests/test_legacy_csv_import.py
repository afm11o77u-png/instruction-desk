"""旧システムCSV一括取込のプレビュー／確定（テスト用DBのみ。本番登録はしない）。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import legacy_csv_import  # noqa: E402
import stock  # noqa: E402

SAMPLE_CSV = Path(
    '/cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/internal/item_report.csv'
)


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64_from_text(text: str, encoding='utf-8') -> str:
    return base64.b64encode(text.encode(encoding)).decode('ascii')


def tiny_csv_rows(rows):
    header = '新古,仕入先,顧客,商品名,商品コード,サイズ,仕入単価,購入数量,購入金額,出荷数,出荷金額,在庫,在庫金額,営業所コード,拠点,棚,補正在庫数'
    lines = [header] + rows
    return '\n'.join(lines)


class LegacyCsvParseTest(unittest.TestCase):
    def test_decode_cp932_sample_header(self):
        if not SAMPLE_CSV.is_file():
            self.skipTest('sample CSV not mounted')
        blob = SAMPLE_CSV.read_bytes()
        text, enc = legacy_csv_import.decode_csv_bytes(blob)
        self.assertEqual(enc, 'cp932')
        fields, rows = legacy_csv_import.parse_csv_text(text)
        self.assertIn('在庫', fields)
        self.assertIn('補正在庫数', fields)
        self.assertGreater(len(rows), 1000)
        # サイズ先頭ゼロ等を壊さない（パースは文字列のまま）
        for _, item in rows[:50]:
            self.assertIsInstance(item['サイズ'], str)

    def test_star_is_used_not_embroidery(self):
        name, star = legacy_csv_import.strip_used_marker('＊帽子')
        self.assertTrue(star)
        self.assertEqual(name, '帽子')
        cond, reasons = legacy_csv_import.parse_row_condition(
            {'新古': '新品', '商品名': '＊帽子'}, 2,
        )
        self.assertEqual(cond, stock.CONDITION_USED)
        self.assertTrue(reasons)

    def test_company_form_variant_detection(self):
        self.assertTrue(legacy_csv_import.looks_like_company_form_variant('㈱テスト', '株式会社テスト'))
        self.assertFalse(legacy_csv_import.looks_like_company_form_variant('㈱テスト', '㈱別会社'))


class LegacyCsvPreviewCommitTest(unittest.TestCase):
    def test_preview_does_not_mutate_masters(self):
        folder, path, c = newdb()
        try:
            before = list(c.execute('SELECT name FROM customers'))
            text = tiny_csv_rows([
                '新品,仕入A,顧客A,スクラブ,SC-1,M,1000,0,0,0,0,3,0,1,,,0',
                '中古,仕入A,顧客A,＊スクラブ,SC-1,M,0,0,0,0,0,1,0,1,,,0',
                '新品,仕入A,顧客A,スクラブ,,M,1000,0,0,0,0,2,0,1,,,0',  # 品番空→要確認
            ])
            body = {
                'file': csv_b64_from_text(text),
                'name': 't.csv',
                'basisDate': '2026-10-01',
                'defaultSiteName': '未確認',
                '_canWrite': True,
                '_actor': {'name': 'テスト担当'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            c.commit()
            after = list(c.execute('SELECT name FROM customers'))
            self.assertEqual(before, after)
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], 0)
            self.assertEqual(preview['countsRaw']['create'], 2)
            self.assertEqual(preview['countsRaw']['needs_review'], 1)
            self.assertEqual(preview['rowCount'], 3)
            # 0・マイナスも対象（別ケース）
            text2 = tiny_csv_rows([
                '新品,仕入B,顧客B,パンツ,P-1,L,2000,0,0,0,0,0,0,1,,,0',
                '新品,仕入B,顧客B,パンツ,P-2,L,2000,0,0,0,0,-2,0,1,,,0',
            ])
            p2 = legacy_csv_import.build_preview(c, {
                **body, 'file': csv_b64_from_text(text2), 'name': 't2.csv',
            })
            self.assertEqual(p2['countsRaw']['create'], 2)
            zeros = [r for r in p2['rows'] if r['qty'] == 0]
            negs = [r for r in p2['rows'] if r['qty'] is not None and r['qty'] < 0]
            self.assertEqual(len(zeros), 1)
            self.assertEqual(len(negs), 1)
            # 加工は加工未整理（加工なしへ確定しない）
            self.assertTrue(all(r['handle']['embroidery'] == '加工未整理' for r in p2['rows'] if r['status'] == 'create'))
        finally:
            c.close()
            folder.cleanup()

    def test_commit_reuses_and_creates_with_token_idempotent(self):
        folder, path, c = newdb()
        try:
            # 既存顧客・仕入先・商品・取扱（加工未整理）
            cu = stock.save_customer(c, {'name': '既存病院', 'active': True})
            su = stock.save_supplier(c, {'name': '既存仕入', 'active': True})
            pr = stock.save_product(c, {
                'code': 'EX-1', 'name': '既存スクラブ', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '加工未整理', 'isStandard': False, 'active': True}],
            })
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,既存仕入,既存病院,既存スクラブ,EX-1,M,1500,0,0,0,0,5,0,1,,,0',
                '新品,新規仕入,新規顧客,新規商品,NW-1,S,800,0,0,0,0,0,0,1,石狩倉庫CSV,1F,0',
            ])
            file_b64 = csv_b64_from_text(text)
            body = {
                'file': file_b64, 'name': 'commit.csv',
                'basisDate': '2026-10-02', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '取込太郎'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            self.assertEqual(preview['countsRaw']['reuse'], 1)
            self.assertEqual(preview['countsRaw']['create'], 1)
            token = 'commit-token-legacy-01'
            result = legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': token,
            }, db_path=str(path))
            c.commit()
            self.assertFalse(result['already'])
            self.assertEqual(result['committedRowCount'], 2)
            self.assertEqual(result['counts']['openingCreated'], 2)
            self.assertEqual(result['actorName'], '取込太郎')
            # 既存商品名・仕入先を上書きしていない
            fresh = stock.product_row(c, pr['id'])
            self.assertEqual(fresh['name'], '既存スクラブ')
            self.assertEqual(fresh['supplierId'], su['id'])
            # 0枚もロットあり
            q0 = c.execute(
                '''SELECT l.qty_remaining FROM lots l
                   JOIN customer_products cp ON cp.id=l.customer_product_id
                   JOIN products p ON p.id=cp.product_id WHERE p.code=?''',
                ('NW-1',),
            ).fetchone()
            self.assertEqual(q0['qty_remaining'], 0)
            # 二重送信
            again = legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': token,
            }, db_path=str(path))
            self.assertTrue(again['already'])
            lots = c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n']
            self.assertEqual(lots, 2)
            # 履歴に記録者・取込更新
            move = c.execute(
                "SELECT actor_name,note FROM moves WHERE kind='opening' ORDER BY id LIMIT 1"
            ).fetchone()
            self.assertEqual(move['actor_name'], '取込太郎')
            self.assertIn('旧システムCSV', move['note'])
        finally:
            c.close()
            folder.cleanup()

    def test_qty_diff_requires_explicit_selection(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '差異病院', 'active': True})
            su = stock.save_supplier(c, {'name': '差異仕入', 'active': True})
            pr = stock.save_product(c, {
                'code': 'DF-1', 'name': '差異商品', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['L'],
                'links': [{'customerId': cu['id'], 'embroidery': '加工未整理', 'isStandard': False, 'active': True}],
            })
            loc = stock.ensure_unconfirmed_site(c)
            stock.register_opening(c, {
                'token': 'seed-diff-01', 'customerProductId': pr['links'][0]['id'],
                'locationId': loc['id'], 'basisDate': '2026-09-01',
                'receivedUnknown': True, 'payoutOrder': 1, 'condition': 'new',
                'allowZero': True, 'lines': [{'size': 'L', 'qty': 3, 'unitCost': 900}],
            })
            c.commit()
            text = tiny_csv_rows([
                '新品,差異仕入,差異病院,差異商品,DF-1,L,900,0,0,0,0,7,0,1,,,0',
            ])
            file_b64 = csv_b64_from_text(text)
            body = {
                'file': file_b64, 'name': 'diff.csv',
                'basisDate': '2026-10-03', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '差異確認'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            self.assertEqual(preview['countsRaw']['qty_diff'], 1)
            row_no = preview['rows'][0]['rowNo']
            with self.assertRaises(ValueError):
                legacy_csv_import.commit_preview(c, {
                    **body,
                    'previewFingerprint': preview['previewFingerprint'],
                    'previewAt': preview['previewAt'],
                    'token': 'diff-token-no-select',
                }, db_path=str(path))
            # 明示選択で更新
            result = legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'diff-token-yes',
                'selections': {str(row_no): {'updateQty': True}},
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['counts']['qtyUpdated'], 1)
            qty = stock.current_qty_for_target(c, pr['links'][0]['id'], 'L', loc['id'])
            self.assertEqual(qty, 7)
            adj = c.execute(
                "SELECT qty_before,qty_after,actor_name,note FROM moves WHERE kind='legacy_csv_adjust'"
            ).fetchone()
            self.assertEqual(adj['qty_before'], 3)
            self.assertEqual(adj['qty_after'], 7)
            self.assertEqual(adj['actor_name'], '差異確認')
            self.assertIn('旧システムCSV', adj['note'])
        finally:
            c.close()
            folder.cleanup()

    def test_csv_duplicate_held_and_stopped_not_reactivated(self):
        folder, path, c = newdb()
        try:
            stock.save_customer(c, {'name': '停止顧客', 'active': False})
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入X,重複顧客,商品X,X-1,M,100,0,0,0,0,1,0,1,,,0',
                '新品,仕入X,重複顧客,商品X,X-1,M,100,0,0,0,0,2,0,1,,,0',
                '新品,仕入Y,停止顧客,商品Y,Y-1,M,100,0,0,0,0,1,0,1,,,0',
            ])
            preview = legacy_csv_import.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'dup.csv',
                'basisDate': '2026-10-04', 'defaultSiteName': '未確認',
                '_canWrite': True,
            })
            self.assertEqual(preview['countsRaw']['hold'], 2)
            self.assertEqual(preview['countsRaw']['needs_review'], 1)
            stopped = [r for r in preview['rows'] if r['csv']['customer'] == '停止顧客'][0]
            self.assertEqual(stopped['status'], 'needs_review')
            self.assertTrue(any('停止' in x for x in stopped['reasons']))
        finally:
            c.close()
            folder.cleanup()

    def test_stale_preview_conflict(self):
        folder, path, c = newdb()
        try:
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入S,顧客S,商品S,S-1,M,100,0,0,0,0,1,0,1,,,0',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'stale.csv',
                'basisDate': '2026-10-05', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '変動'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            # プレビュー後に台帳変更
            stock.save_customer(c, {'name': '割り込み顧客', 'active': True})
            c.commit()
            with self.assertRaises(stock.StockConflict):
                legacy_csv_import.commit_preview(c, {
                    **body,
                    'previewFingerprint': preview['previewFingerprint'],
                    'previewAt': preview['previewAt'],
                    'token': 'stale-token-1',
                }, db_path=str(path))
        finally:
            c.close()
            folder.cleanup()

    def test_negative_opening_visible_as_shortage_filter_without_cost_pending(self):
        """マイナス初期在庫は残数マイナスとして一覧できる。不足出庫(cost_pending)とは別経路。"""
        folder, path, c = newdb()
        try:
            stock.ensure_unconfirmed_site(c)
            text = tiny_csv_rows([
                '新品,仕入N,顧客N,商品N,N-1,M,500,0,0,0,0,-3,0,1,,,0',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'neg.csv',
                'basisDate': '2026-10-06', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': 'マイナス'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'neg-token-1',
            }, db_path=str(path))
            c.commit()
            view = stock.stock_query(c, {'stockFilter': 'shortage'})
            self.assertTrue(any(l['qty'] == -3 for l in view['lots']))
            # 取込マイナスは仕入単価保持（cost_pending ではない）
            lot = c.execute('SELECT qty_remaining,unit_cost,COALESCE(cost_pending,0) AS cp FROM lots').fetchone()
            self.assertEqual(lot['qty_remaining'], -3)
            self.assertEqual(lot['unit_cost'], 500)
            self.assertEqual(lot['cp'], 0)
        finally:
            c.close()
            folder.cleanup()


class LegacyCsvUiSourceTest(unittest.TestCase):
    def test_setup_tab_and_script_wired(self):
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn("['legacyCsv','旧CSV取込']", app)
        self.assertIn("legacyCsv:'stock-legacy-csv'", app)
        self.assertIn('legacy_csv.js', html)
        self.assertIn('legacyCsvPage', (ROOT / 'static' / 'legacy_csv.js').read_text(encoding='utf-8'))

    def test_preview_table_avoids_form_grid_class(self):
        """フォーム用 .grid { display:grid } を表に付けると見出しとデータが左右分離する。"""
        js = (ROOT / 'static' / 'legacy_csv.js').read_text(encoding='utf-8')
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertNotIn('class="grid legacy-csv-table"', js)
        self.assertIn('class="legacy-csv-table"', js)
        self.assertIn('.legacy-csv-table{', css)
        self.assertIn('display:table', css)
        self.assertIn('.legacy-csv-table-wrap', css)
        self.assertIn('data-lc-detail', js)
        self.assertIn('PAGE_SIZE=80', js)


class LegacyCsvSamplePreviewStatsTest(unittest.TestCase):
    def test_sample_preview_counts_on_empty_db(self):
        if not SAMPLE_CSV.is_file():
            self.skipTest('sample CSV not mounted')
        folder, path, c = newdb()
        try:
            blob = SAMPLE_CSV.read_bytes()
            body = {
                'file': base64.b64encode(blob).decode('ascii'),
                'name': 'item_report.csv',
                'basisDate': '2026-10-16',
                'defaultSiteName': '未確認',
                '_canWrite': True,
                '_actor': {'name': '検証'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            c.commit()
            # 本番DBへは書き込まない（このテストDBのみ）
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM customers').fetchone()['n'], 0)
            self.assertEqual(preview['encoding'], 'cp932')
            self.assertEqual(preview['rowCount'], 14742)
            raw = preview['countsRaw']
            self.assertGreater(raw['create'], 0)
            self.assertGreater(raw['needs_review'], 0)
            self.assertGreater(raw['hold'], 0)
            # 補正在庫は使っていないことの確認用メタ
            self.assertIn('補正在庫数', preview['ignoredColumns'])
            # 結果をファイルに残す用のサマリ
            self._preview_summary = {
                'rowCount': preview['rowCount'],
                'countsRaw': raw,
                'counts': preview['counts'],
                'encoding': preview['encoding'],
            }
            print('SAMPLE_PREVIEW', self._preview_summary)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
