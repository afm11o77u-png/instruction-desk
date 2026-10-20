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
        name2, star2 = legacy_csv_import.strip_used_marker('＊ 帽子')
        self.assertTrue(star2)
        self.assertEqual(name2, '帽子')
        name3, star3 = legacy_csv_import.strip_used_marker('*帽子')
        self.assertTrue(star3)
        self.assertEqual(name3, '帽子')
        self.assertTrue(
            legacy_csv_import.names_differ_only_by_used_marker('緑つなぎ', '＊緑つなぎ')
        )
        self.assertEqual(
            legacy_csv_import.prefer_unmarked_product_name(['＊緑つなぎ', '*緑つなぎ']),
            '緑つなぎ',
        )
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
                '新品,仕入A,顧客A,スクラブ,,M,1000,0,0,0,0,2,0,1,,,0',  # 品番空→部品・資材として新規
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
            self.assertEqual(preview['countsRaw']['create'], 3)
            self.assertEqual(preview['countsRaw'].get('needs_review', 0), 0)
            self.assertEqual(preview['rowCount'], 3)
            blank = next(r for r in preview['rows'] if r['csv']['code'] == '')
            self.assertEqual(blank['product']['category'], '部品・資材')
            self.assertEqual(blank['csv']['codeDisplay'], '品番なし')
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
        """旧CSV取込タブは廃止。入口は右上「旧CSV一括取り込み」へ統合。"""
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        js = (ROOT / 'static' / 'legacy_csv.js').read_text(encoding='utf-8')
        self.assertNotIn("['legacyCsv','旧CSV取込']", app)
        self.assertIn('legacyCsvBulkOpen', app)
        self.assertIn('旧CSV一括取り込み', app)
        self.assertIn('legacy_csv.js', html)
        self.assertIn('legacyCsvPage', js)
        self.assertIn('統合されました', js)

    def test_preview_table_avoids_form_grid_class(self):
        """統合先モーダルの表は .grid を使わず、既存の table-wrap スタイルを共用。"""
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertNotIn('class="grid legacy-csv-table"', js)
        self.assertIn('legacy-csv-table-wrap', js)
        self.assertIn('.legacy-csv-table{', css)
        self.assertIn('display:table', css)
        self.assertIn('.legacy-csv-table-wrap', css)
        self.assertIn('PAGE_SIZE=80', js)


class LegacyCsvEmbroideryMatchTest(unittest.TestCase):
    """加工未整理 vs 加工なし不一致の修正: 5ケース＋商品名差の追跡。"""

    def _seed_pair(self, c, *, customer, supplier, code, name, embroidery):
        cu = stock.save_customer(c, {'name': customer, 'active': True})
        su = stock.save_supplier(c, {'name': supplier, 'active': True})
        pr = stock.save_product(c, {
            'code': code, 'name': name, 'color': '', 'supplierId': su['id'],
            'active': True, 'sizes': ['M'],
            'links': [{
                'customerId': cu['id'], 'embroidery': embroidery,
                'isStandard': False, 'active': True,
            }],
        })
        stock.ensure_unconfirmed_site(c)
        c.commit()
        return cu, su, pr

    def test_1_reuse_existing_no_embroidery(self):
        """既存が加工なし1件 → 再利用。加工未整理を重複作成せず要確認にしない。"""
        folder, path, c = newdb()
        try:
            cu, su, pr = self._seed_pair(
                c, customer='病院A', supplier='仕入A', code='A-1', name='白衣',
                embroidery='',  # 加工なし
            )
            cp_before = list(c.execute(
                'SELECT id,embroidery FROM customer_products WHERE customer_id=? AND product_id=?',
                (cu['id'], pr['id']),
            ))
            self.assertEqual(len(cp_before), 1)
            self.assertEqual(cp_before[0]['embroidery'], '')
            text = tiny_csv_rows([
                '新品,仕入A,病院A,白衣,A-1,M,1000,0,0,0,0,4,0,1,,,0',
            ])
            preview = legacy_csv_import.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'case1.csv',
                'basisDate': '2026-10-17', 'defaultSiteName': '未確認', '_canWrite': True,
            })
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'reuse')
            self.assertEqual(row['handle']['action'], 'reuse')
            self.assertEqual(row['handle']['id'], cp_before[0]['id'])
            self.assertEqual(row['handle']['embroideryLabel'], '加工なし')
            self.assertTrue(row['handle']['pendingEmbroiderySort'])
            self.assertFalse(any('加工なし' in x and '別に' in x for x in row['reasons']))
            # 確定しても取扱は増えない・加工キーは空のまま
            result = legacy_csv_import.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'case1.csv',
                'basisDate': '2026-10-17', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '検証'},
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'], 'token': 'emb-case1',
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['counts']['handlesCreated'], 0)
            cps = list(c.execute(
                'SELECT id,embroidery FROM customer_products WHERE customer_id=? AND product_id=?',
                (cu['id'], pr['id']),
            ))
            self.assertEqual(len(cps), 1)
            self.assertEqual(cps[0]['embroidery'], '')
            note = c.execute("SELECT note FROM moves WHERE kind='opening'").fetchone()['note']
            self.assertIn('A-1', note)
            self.assertIn('白衣', note)
            self.assertIn(str(row['rowNo']), note)
        finally:
            c.close()
            folder.cleanup()

    def test_2_create_when_no_handle(self):
        """取扱なし → 加工未整理を追加。商品名・品番は残す。"""
        folder, path, c = newdb()
        try:
            stock.save_customer(c, {'name': '病院B', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入B', 'active': True})
            stock.save_product(c, {
                'code': 'B-1', 'name': '白衣（DHC刺繍あり）', 'color': '',
                'supplierId': su['id'], 'active': True, 'sizes': ['M'], 'links': [],
            })
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入B,病院B,白衣（DHC刺繍あり）,B-1,M,1200,0,0,0,0,2,0,1,,,0',
            ])
            preview = legacy_csv_import.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'case2.csv',
                'basisDate': '2026-10-17', 'defaultSiteName': '未確認', '_canWrite': True,
            })
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'create')
            self.assertEqual(row['handle']['action'], 'create')
            self.assertEqual(row['handle']['embroidery'], '加工未整理')
            self.assertEqual(row['csv']['productName'], '白衣（DHC刺繍あり）')
            legacy_csv_import.commit_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'case2.csv',
                'basisDate': '2026-10-17', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '検証'},
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'], 'token': 'emb-case2',
            }, db_path=str(path))
            c.commit()
            emb = c.execute('SELECT embroidery FROM customer_products').fetchone()['embroidery']
            self.assertEqual(emb, '加工未整理')
            prod = c.execute('SELECT name FROM products WHERE code=?', ('B-1',)).fetchone()
            self.assertEqual(prod['name'], '白衣（DHC刺繍あり）')
        finally:
            c.close()
            folder.cleanup()

    def test_3_reuse_existing_pending_embroidery(self):
        """既存が加工未整理 → 再取込で重複追加しない。"""
        folder, path, c = newdb()
        try:
            cu, su, pr = self._seed_pair(
                c, customer='病院C', supplier='仕入C', code='C-1', name='スクラブ',
                embroidery='加工未整理',
            )
            cp_id = pr['links'][0]['id']
            text = tiny_csv_rows([
                '新品,仕入C,病院C,スクラブ,C-1,M,900,0,0,0,0,1,0,1,,,0',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'case3.csv',
                'basisDate': '2026-10-17', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '検証'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            self.assertEqual(preview['rows'][0]['handle']['id'], cp_id)
            self.assertEqual(preview['rows'][0]['status'], 'reuse')
            legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'], 'token': 'emb-case3',
            }, db_path=str(path))
            c.commit()
            n = c.execute(
                'SELECT COUNT(*) AS n FROM customer_products WHERE customer_id=? AND product_id=?',
                (cu['id'], pr['id']),
            ).fetchone()['n']
            self.assertEqual(n, 1)
        finally:
            c.close()
            folder.cleanup()

    def test_4_multiple_embroidery_needs_review(self):
        """加工仕様が複数 → 候補選択を残し理由を具体表示。"""
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '病院D', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入D', 'active': True})
            pr = stock.save_product(c, {
                'code': 'D-1', 'name': 'パンツ', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['L'],
                'links': [
                    {'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True},
                    {'customerId': cu['id'], 'embroidery': '左胸ネーム', 'isStandard': False, 'active': True},
                ],
            })
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入D,病院D,パンツ,D-1,L,1100,0,0,0,0,3,0,1,,,0',
            ])
            preview = legacy_csv_import.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'case4.csv',
                'basisDate': '2026-10-17', 'defaultSiteName': '未確認', '_canWrite': True,
            })
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'needs_review')
            self.assertEqual(row['handle']['action'], 'review')
            self.assertEqual(len(row['handle']['candidates']), 2)
            reason = ' '.join(row['reasons'])
            self.assertIn('加工なし', reason)
            self.assertIn('左胸ネーム', reason)
            self.assertIn('複数', reason)
        finally:
            c.close()
            folder.cleanup()

    def test_5_same_code_different_embroidery_in_name_not_merged(self):
        """同じ品番で加工記載が異なる商品名は無条件合算しない。"""
        folder, path, c = newdb()
        try:
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入E,病院E,白衣,E-1,M,1000,0,0,0,0,1,0,1,,,0',
                '新品,仕入E,病院E,白衣（DHC刺繍あり）,E-1,M,1000,0,0,0,0,2,0,1,,,0',
            ])
            preview = legacy_csv_import.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'case5.csv',
                'basisDate': '2026-10-17', 'defaultSiteName': '未確認', '_canWrite': True,
            })
            # 商品名が違うので CSV 重複キーにはならない
            self.assertEqual(preview['countsRaw'].get('hold', 0), 0)
            names = {r['csv']['productName'] for r in preview['rows']}
            self.assertEqual(names, {'白衣', '白衣（DHC刺繍あり）'})
            for r in preview['rows']:
                self.assertEqual(r['csv']['productName'], r['handle']['csvSource']['productName'])
                self.assertEqual(r['rowNo'], r['handle']['csvSource']['sourceRowNo'])
        finally:
            c.close()
            folder.cleanup()

    def test_single_specific_embroidery_still_review(self):
        """具体加工が1件だけのときもCSV加工欄なしでは自動確定しない。"""
        folder, path, c = newdb()
        try:
            self._seed_pair(
                c, customer='病院F', supplier='仕入F', code='F-1', name='帽',
                embroidery='左胸ネーム',
            )
            text = tiny_csv_rows([
                '新品,仕入F,病院F,帽,F-1,M,500,0,0,0,0,1,0,1,,,0',
            ])
            preview = legacy_csv_import.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'case-spec.csv',
                'basisDate': '2026-10-17', 'defaultSiteName': '未確認', '_canWrite': True,
            })
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'needs_review')
            self.assertIn('左胸ネーム', ' '.join(row['reasons']))
        finally:
            c.close()
            folder.cleanup()

    def test_star_not_treated_as_embroidery(self):
        name, star = legacy_csv_import.strip_used_marker('＊白衣（刺繍あり）')
        self.assertTrue(star)
        self.assertEqual(name, '白衣（刺繍あり）')


class LegacyCsvBlankSkuTest(unittest.TestCase):
    def test_uniform_exception_and_parts_and_excluded(self):
        folder, path, c = newdb()
        try:
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入U,グローヴ㈱,チェック柄ベスト(ムーリベッキ),,S,8500,0,0,0,0,13,0,1,,,0',
                '新品,仕入U,グローヴ㈱,チェック柄ベスト(ムーリベッキ),,M,8500,0,0,0,0,5,0,1,,,0',
                '新品,仕入P,顧客P,丈直し,,-,1000,0,0,0,0,1,0,1,,,0',
                '新品,住商モンブラン㈱,コープフーズ【釧路】,,,M,1750,0,0,0,0,0,0,1,,,0',
                '新品,仕入F,㈱ホクショク,NPC白生地,,5M,2900,0,0,0,0,1,0,1,,,0',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'blank.csv',
                'basisDate': '2026-10-18', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '検証'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            self.assertEqual(preview['countsRaw'].get('excluded', 0), 1)
            self.assertEqual(preview['excludedCount'], 1)
            self.assertEqual(preview['excludedRows'][0]['rowNo'], 5)
            uniforms = [r for r in preview['rows'] if r['csv']['productName'] == 'チェック柄ベスト(ムーリベッキ)']
            self.assertEqual(len(uniforms), 2)
            for r in uniforms:
                self.assertEqual(r['status'], 'create')
                self.assertEqual(r['product']['category'], '制服')
                self.assertEqual(r['csv']['codeDisplay'], '品番なし（要確認）')
                self.assertEqual(r['product']['code'], '')
            parts = next(r for r in preview['rows'] if r['csv']['productName'] == '丈直し')
            self.assertEqual(parts['product']['category'], '部品・資材')
            self.assertEqual(parts['csv']['codeDisplay'], '品番なし')
            fabric = next(r for r in preview['rows'] if r['csv']['productName'] == 'NPC白生地')
            self.assertEqual(fabric['qty'], 1)
            self.assertEqual(fabric['unitCost'], 2900)
            self.assertEqual(fabric['csv']['size'], '5M')

            result = legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'blank-sku-1',
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['excludedRowCount'], 1)
            # サイズ違いは同一商品
            vest = c.execute(
                "SELECT id,code,category FROM products WHERE name=? AND code=''",
                ('チェック柄ベスト(ムーリベッキ)',),
            ).fetchall()
            self.assertEqual(len(vest), 1)
            self.assertEqual(vest[0]['category'], '制服')
            sizes = {r['label'] for r in c.execute(
                'SELECT label FROM product_sizes WHERE product_id=?', (vest[0]['id'],)
            )}
            self.assertEqual(sizes, {'S', 'M'})
            # 対象外行だけで顧客を作らない
            coop = c.execute(
                "SELECT id FROM customers WHERE name=?", ('コープフーズ【釧路】',)
            ).fetchone()
            self.assertIsNone(coop)
            # 生地数量・単価そのまま
            lot = c.execute(
                '''SELECT l.qty_remaining,l.unit_cost,l.size_label FROM lots l
                   JOIN customer_products cp ON cp.id=l.customer_product_id
                   JOIN products p ON p.id=cp.product_id
                   WHERE p.name=?''',
                ('NPC白生地',),
            ).fetchone()
            self.assertEqual(lot['qty_remaining'], 1)
            self.assertEqual(lot['unit_cost'], 2900)
            self.assertEqual(lot['size_label'], '5M')

            # 再取込プレビュー: 同一商品を増やさない（変更なし／既存使用）
            preview2 = legacy_csv_import.build_preview(c, body)
            vest_rows = [
                r for r in preview2['rows']
                if r['csv']['productName'] == 'チェック柄ベスト(ムーリベッキ)'
            ]
            self.assertTrue(all(r['status'] in ('unchanged', 'reuse', 'qty_diff') for r in vest_rows))
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) AS n FROM products WHERE name=? AND code=''",
                    ('チェック柄ベスト(ムーリベッキ)',),
                ).fetchone()['n'],
                1,
            )
            # 同一 token 連打は already（二重登録しない）
            again = legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'blank-sku-1',
            }, db_path=str(path))
            self.assertTrue(again.get('already'))
        finally:
            c.close()
            folder.cleanup()

    def test_blank_sku_not_merged_by_empty_code_alone(self):
        folder, path, c = newdb()
        try:
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入1,顧客1,システム代,,-,500,0,0,0,0,1,0,1,,,0',
                '新品,仕入2,顧客2,ポケット取付,,-,800,0,0,0,0,1,0,1,,,0',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'sep.csv',
                'basisDate': '2026-10-18', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '検証'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'blank-sep',
            }, db_path=str(path))
            c.commit()
            n = c.execute("SELECT COUNT(*) AS n FROM products WHERE code=''").fetchone()['n']
            self.assertEqual(n, 2)
        finally:
            c.close()
            folder.cleanup()

    def test_similar_uniform_name_not_auto_extended(self):
        self.assertFalse(legacy_csv_import.is_uniform_blank_sku_exception(
            'グローヴ㈱', '衣装係ベスト',
        ))
        self.assertTrue(legacy_csv_import.is_uniform_blank_sku_exception(
            '石屋商事　パーク共通', '黒ロングコート',
        ))


class LegacyCsvCustomOrderTest(unittest.TestCase):
    """品番「別注」の重複許可・顧客×商品名照合・対応記録。"""

    def test_is_custom_order_code_exact_only(self):
        self.assertTrue(legacy_csv_import.is_custom_order_code('別注'))
        self.assertTrue(legacy_csv_import.is_custom_order_code(' 別注 '))
        self.assertFalse(legacy_csv_import.is_custom_order_code('別注(SD5000AM-5U)'))
        self.assertFalse(legacy_csv_import.is_custom_order_code('BE-EMPCAP-GR 別注'))
        self.assertFalse(legacy_csv_import.is_custom_order_code(''))

    def test_separate_by_customer_and_full_name(self):
        folder, path, c = newdb()
        try:
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入A,顧客A,白衣（青・胸に社名刺繍）,別注,M,1000,0,0,0,0,2,0,1,,,0',
                '新品,仕入A,顧客B,白衣（青・胸に社名刺繍）,別注,M,1000,0,0,0,0,3,0,1,,,0',
                '新品,仕入A,顧客A,白衣（赤・袖ロゴ）,別注,M,1000,0,0,0,0,1,0,1,,,0',
                '新品,仕入A,顧客A,白衣（青・胸に社名刺繍）,別注,L,1000,0,0,0,0,4,0,1,,,0',
                '新品,仕入B,顧客A,白衣（青・胸に社名刺繍）,別注,M,1500,0,0,0,0,1,0,1,,,0',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'bechu.csv',
                'basisDate': '2026-10-18', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '別注検証'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            for r in preview['rows']:
                self.assertEqual(r['status'], 'create')
                self.assertTrue(r['product']['customOrder'])
                self.assertEqual(r['product']['category'], '制服')
                self.assertEqual(r['csv']['productName'], r['product']['name'])
            result = legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'bechu-sep-01',
            }, db_path=str(path))
            c.commit()
            self.assertEqual(result['committedRowCount'], 5)
            products = list(c.execute(
                "SELECT id,name,category,supplier_id FROM products WHERE code='別注' ORDER BY id"
            ))
            # 顧客違い・括弧内差で分離。サイズ違いは同一商品。仕入先差だけでは増やさない。
            self.assertEqual(len(products), 3)
            self.assertTrue(all(p['category'] == '制服' for p in products))
            cust_a = c.execute("SELECT id FROM customers WHERE name='顧客A'").fetchone()['id']
            blue_a = c.execute(
                '''SELECT p.id,p.supplier_id FROM products p
                   JOIN customer_products cp ON cp.product_id=p.id
                   WHERE p.code='別注' AND p.name=? AND cp.customer_id=?''',
                ('白衣（青・胸に社名刺繍）', cust_a),
            ).fetchone()
            self.assertIsNotNone(blue_a)
            sizes = {
                r['label'] for r in c.execute(
                    'SELECT label FROM product_sizes WHERE product_id=?', (blue_a['id'],)
                )
            }
            self.assertEqual(sizes, {'M', 'L'})
            # 単価違いも商品を増やさずロットで区別
            lots = list(c.execute(
                '''SELECT l.unit_cost,l.qty_remaining,l.size_label FROM lots l
                   JOIN customer_products cp ON cp.id=l.customer_product_id
                   WHERE cp.product_id=? ORDER BY l.unit_cost,l.size_label''',
                (blue_a['id'],),
            ))
            costs = {(l['size_label'], l['unit_cost'], l['qty_remaining']) for l in lots}
            self.assertIn(('M', 1000, 2), costs)
            self.assertIn(('M', 1500, 1), costs)
            self.assertIn(('L', 1000, 4), costs)
            # 既存通常仕入先を仕入先差で上書きしない（先に作った仕入Aのまま）
            su_a = c.execute("SELECT id FROM suppliers WHERE name='仕入A'").fetchone()['id']
            self.assertEqual(blue_a['supplier_id'], su_a)
            # 対応記録
            maps = c.execute('SELECT COUNT(*) AS n FROM legacy_csv_row_maps').fetchone()['n']
            self.assertEqual(maps, 5)
        finally:
            c.close()
            folder.cleanup()

    def test_reuse_existing_and_not_assign_other_customer(self):
        folder, path, c = newdb()
        try:
            cu_a = stock.save_customer(c, {'name': '病院X', 'active': True})
            cu_b = stock.save_customer(c, {'name': '病院Y', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入X', 'active': True})
            pr = stock.save_product(c, {
                'code': '別注', 'name': 'エプロン（緑）', 'color': '',
                'category': '制服', 'supplierId': su['id'], 'active': True, 'sizes': ['M'],
                'links': [{
                    'customerId': cu_a['id'], 'embroidery': '加工未整理',
                    'isStandard': False, 'active': True,
                }],
            })
            # 顧客対応のない同名別注（勝手に割り当てない）
            stock.save_product(c, {
                'code': '別注', 'name': 'エプロン（緑）', 'color': '緑',
                'category': '制服', 'supplierId': su['id'], 'active': True, 'sizes': ['M'],
                'links': [],
            })
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入X,病院X,エプロン（緑）,別注,M,900,0,0,0,0,5,0,1,,,0',
                '新品,仕入X,病院Y,エプロン（緑）,別注,M,900,0,0,0,0,2,0,1,,,0',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'reuse.csv',
                'basisDate': '2026-10-18', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '別注検証'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            row_x = next(r for r in preview['rows'] if r['csv']['customer'] == '病院X')
            row_y = next(r for r in preview['rows'] if r['csv']['customer'] == '病院Y')
            self.assertEqual(row_x['product']['action'], 'reuse')
            self.assertEqual(row_x['product']['id'], pr['id'])
            self.assertEqual(row_y['product']['action'], 'create')
            legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'bechu-reuse',
            }, db_path=str(path))
            c.commit()
            # 病院Y用に新規別注が追加（孤児の同名は使わない）
            y_products = list(c.execute(
                '''SELECT p.id FROM products p
                   JOIN customer_products cp ON cp.product_id=p.id
                   WHERE p.code='別注' AND p.name=? AND cp.customer_id=?''',
                ('エプロン（緑）', cu_b['id']),
            ))
            self.assertEqual(len(y_products), 1)
            self.assertNotEqual(y_products[0]['id'], pr['id'])
        finally:
            c.close()
            folder.cleanup()

    def test_multiple_candidates_need_review(self):
        folder, path, c = newdb()
        try:
            cu = stock.save_customer(c, {'name': '病院Z', 'active': True})
            su = stock.save_supplier(c, {'name': '仕入Z', 'active': True})
            for color in ('', '青'):
                stock.save_product(c, {
                    'code': '別注', 'name': '帽子（紺）', 'color': color,
                    'category': '制服', 'supplierId': su['id'], 'active': True, 'sizes': ['F'],
                    'links': [{
                        'customerId': cu['id'], 'embroidery': '加工未整理',
                        'isStandard': False, 'active': True,
                    }],
                })
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入Z,病院Z,帽子（紺）,別注,F,700,0,0,0,0,1,0,1,,,0',
            ])
            preview = legacy_csv_import.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'cand.csv',
                'basisDate': '2026-10-18', 'defaultSiteName': '未確認', '_canWrite': True,
            })
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'needs_review')
            self.assertEqual(len(row['product']['candidates']), 2)
            self.assertTrue(any(c.get('customerNames') for c in row['product']['candidates']))
            reason = ' '.join(row['reasons'])
            self.assertIn('別注', reason)
            self.assertIn('複数', reason)
        finally:
            c.close()
            folder.cleanup()

    def test_reimport_uses_map_after_name_change(self):
        folder, path, c = newdb()
        try:
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入M,顧客M,白衣（青・胸に社名刺繍）,別注,M,1000,0,0,0,0,2,0,1,,,0',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'map.csv',
                'basisDate': '2026-10-18', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '別注検証'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'bechu-map-1',
            }, db_path=str(path))
            c.commit()
            prod = c.execute("SELECT id,name FROM products WHERE code='別注'").fetchone()
            self.assertEqual(prod['name'], '白衣（青・胸に社名刺繍）')
            # WEB側で商品名を整理しても対応記録から参照
            c.execute(
                "UPDATE products SET name=?, color=?, updated_at=? WHERE id=?",
                ('白衣', '青', stock.stamp(), prod['id']),
            )
            c.commit()
            preview2 = legacy_csv_import.build_preview(c, body)
            row = preview2['rows'][0]
            self.assertTrue(row['product'].get('fromImportMap'))
            self.assertEqual(row['product']['id'], prod['id'])
            self.assertIn(row['status'], ('unchanged', 'reuse', 'qty_diff'))
            n = c.execute("SELECT COUNT(*) AS n FROM products WHERE code='別注'").fetchone()['n']
            self.assertEqual(n, 1)
            # 再取込で在庫を増やさない
            lots_before = c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n']
            with self.assertRaises(ValueError):
                # 変更なし／既存在庫のため空確定は不可（本登録しない）
                legacy_csv_import.commit_preview(c, {
                    **body,
                    'previewFingerprint': preview2['previewFingerprint'],
                    'previewAt': preview2['previewAt'],
                    'token': 'bechu-map-2',
                }, db_path=str(path))
            self.assertEqual(
                c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'], lots_before,
            )
        finally:
            c.close()
            folder.cleanup()

    def test_normal_code_and_blank_sku_unchanged(self):
        folder, path, c = newdb()
        try:
            stock.ensure_unconfirmed_site(c)
            c.commit()
            text = tiny_csv_rows([
                '新品,仕入N,顧客N,通常スクラブ,N-100,M,800,0,0,0,0,1,0,1,,,0',
                '新品,仕入N,顧客N,部品A,,-,100,0,0,0,0,1,0,1,,,0',
                '新品,仕入N,グローヴ㈱,衣装係ストレートパンツ,,L,2000,0,0,0,0,1,0,1,,,0',
                '新品,仕入N,顧客N,別注っぽい名前,別注(48-880),M,500,0,0,0,0,1,0,1,,,0',
            ])
            body = {
                'file': csv_b64_from_text(text), 'name': 'normal.csv',
                'basisDate': '2026-10-18', 'defaultSiteName': '未確認',
                '_canWrite': True, '_actor': {'name': '別注検証'},
            }
            preview = legacy_csv_import.build_preview(c, body)
            normal = next(r for r in preview['rows'] if r['csv']['code'] == 'N-100')
            parts = next(r for r in preview['rows'] if r['csv']['productName'] == '部品A')
            uniform = next(r for r in preview['rows'] if 'ストレートパンツ' in r['csv']['productName'])
            other = next(r for r in preview['rows'] if r['csv']['code'] == '別注(48-880)')
            self.assertFalse(normal['product'].get('customOrder'))
            self.assertEqual(parts['product']['category'], '部品・資材')
            self.assertEqual(uniform['product']['category'], '制服')
            self.assertFalse(other['product'].get('customOrder'))
            legacy_csv_import.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'bechu-normal',
            }, db_path=str(path))
            c.commit()
            self.assertEqual(
                c.execute("SELECT COUNT(*) AS n FROM products WHERE code='別注'").fetchone()['n'], 0,
            )
            self.assertEqual(
                c.execute("SELECT COUNT(*) AS n FROM products WHERE code='別注(48-880)'").fetchone()['n'], 1,
            )
        finally:
            c.close()
            folder.cleanup()


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
            self.assertEqual(raw.get('excluded', 0), 1)
            uniforms = [
                r for r in preview['rows']
                if r.get('product', {}).get('blankSku')
                and r['product'].get('category') == '制服'
                and r['status'] == 'create'
            ]
            self.assertGreaterEqual(len(uniforms), 5)
            custom = [
                r for r in preview['rows']
                if r.get('product', {}).get('customOrder')
            ]
            self.assertGreater(len(custom), 0)
            self.assertTrue(all(r['product'].get('category') == '制服' for r in custom if r['status'] == 'create'))
            # 補正在庫は使っていないことの確認用メタ
            self.assertIn('補正在庫数', preview['ignoredColumns'])
            # 結果をファイルに残す用のサマリ
            self._preview_summary = {
                'rowCount': preview['rowCount'],
                'countsRaw': raw,
                'counts': preview['counts'],
                'encoding': preview['encoding'],
                'excludedCount': preview.get('excludedCount'),
                'customOrderRows': len(custom),
            }
            print('SAMPLE_PREVIEW', self._preview_summary)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
