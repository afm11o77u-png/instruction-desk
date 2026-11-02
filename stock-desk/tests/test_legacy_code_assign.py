"""旧CSV: 別注→別注(顧客名)、品番空欄の衣服→要確認（テスト用DBのみ）。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stock  # noqa: E402
import legacy_code_assign as lca  # noqa: E402
import product_ledger_import as pli  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv, newdb, opening_csv  # noqa: E402

LEDGER = [
    '顧客A,仕入S,別注,特注ベスト,M',
    '顧客A,仕入S,別注,特注ベスト,L',
    '顧客A,仕入S,別注,特注エプロン,F',
    '顧客B,仕入S,別注,特注ベスト,M',
    'コカコーラ,仕入P,,男パンツ（ロールイン仕様）,96-76',
    'コカコーラ,仕入P,,男性パンツ（ロールイン仕様）,100-92',
    'コカコーラ,仕入P,,男性用パンツ（ロールイン仕様）,72-92',
    '顧客C,仕入P,,男パンツ（ロールイン仕様）,96-76',
    '顧客A,Ｋ２スタッフ工房,,パンツ裾上げ,F',
    '顧客A,仕入R,,白リブ,F',
]
OPEN = [
    '新品,仕入S,顧客A,特注ベスト,別注,M,1000,0,0,3,本社,棚1,0',
    '新品,仕入S,顧客A,特注エプロン,別注,F,500,0,0,2,本社,棚1,0',
    '新品,仕入S,顧客B,特注ベスト,別注,M,1000,0,0,4,本社,棚1,0',
    '新品,仕入P,コカコーラ,男性用パンツ（ロールイン仕様）,,72-92,2850,0,0,5,本社,棚1,0',
    '新品,仕入P,コカコーラ,男パンツ（ロールイン仕様）,,96-76,2850,0,0,6,本社,棚1,0',
]


class ClassifyTest(unittest.TestCase):
    def test_blank_code_kinds(self):
        for n in ('衣装係ベスト', '耐切創夏ＪＰ', '黒ロングコート', 'シェフメイト\u3000α-3000\u3000白',
                  '事務マタニティドレス', '男性用パンツ（ロールイン仕様）', 'チェック柄ベスト(ムーリベッキ)'):
            self.assertEqual(lca.blank_code_kind(n), 'garment', n)
        for n in ('パンツ裾上げ', 'コック帽ネット付け', 'インナー外し', 'ポケット潰し', 'エプロン刺繍'):
            self.assertEqual(lca.blank_code_kind(n), 'processing', n)
        for n in ('白リブ', 'パンツ用釦\u3000黒', 'フライス生地', 'システム代', '〇井手\u3000サンプル2型'):
            self.assertNotEqual(lca.blank_code_kind(n), 'garment', n)

    def test_name_variants_unified(self):
        for n in ('男パンツ（ロールイン仕様）', '男性パンツ（ロールイン仕様）', '男性用パンツ（ロールイン仕様）'):
            self.assertEqual(lca.canonical_needs_code_name(n), '男パンツ（ロールイン仕様）')


class LedgerAndOpeningTest(unittest.TestCase):
    def _import(self, c, path, tag):
        base = {'importMode': 'products', 'name': 'p.csv', 'file': b64(ledger_csv(LEDGER)),
                '_canWrite': True, '_actor': {'name': 't'}}
        for i, phase in enumerate(('parts', 'goods')):
            body = {**base, 'productPhase': phase}
            p = pli.build_preview(c, body)
            try:
                with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                    pli.commit_preview(c, {**body, 'commitToken': f'code-assign-{tag}-{i}',
                                           'previewFingerprint': p['previewFingerprint']}, db_path=path)
            except ValueError as e:
                # 再取込で全行「変更なし」なら正常
                self.assertIn('登録する行がありません', str(e))
                self.assertEqual(tag, 'b')

    def _open(self, c, path, tag):
        st = {'importMode': 'opening_stock', 'auxName': 'o.csv', 'auxFile': b64(opening_csv(OPEN)),
              'basisDate': '2026-01-15', 'defaultSiteName': '未確認', '_canWrite': True, '_actor': {'name': 't'}}
        p = pli.build_preview(c, st)
        if tag:
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(c, {**st, 'commitToken': f'code-assign-open-{tag}',
                                       'previewFingerprint': p['previewFingerprint']}, db_path=path)
        return p

    def _products(self, c):
        return sorted((r[0], r[1]) for r in c.execute(
            "SELECT code,name FROM products WHERE merged_into_id IS NULL AND (code GLOB '別注*' OR code='要確認')"))

    def test_register_reimport_open_and_code_edit(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        self._import(c, path, 'a')
        expect = [
            ('別注(顧客A)', '特注エプロン'), ('別注(顧客A)', '特注ベスト'), ('別注(顧客B)', '特注ベスト'),
            ('要確認', '男パンツ（ロールイン仕様）'), ('要確認', '男パンツ（ロールイン仕様）'),
        ]
        self.assertEqual(self._products(c), expect)
        sizes = {r[0] for r in c.execute(
            """SELECT ps.label FROM product_sizes ps JOIN products p ON p.id=ps.product_id
               JOIN customer_products cp ON cp.product_id=p.id JOIN customers cu ON cu.id=cp.customer_id
               WHERE p.code='要確認' AND cu.name='コカコーラ'""")}
        self.assertEqual(sizes, {'96-76', '100-92', '72-92'})
        # 加工・部品は従来どおり（要確認にしない）
        self.assertIsNone(c.execute("SELECT 1 FROM products WHERE code='要確認' AND name IN ('パンツ裾上げ','白リブ')").fetchone())
        pid = c.execute("""SELECT p.id FROM products p JOIN customer_products cp ON cp.product_id=p.id
                           JOIN customers cu ON cu.id=cp.customer_id WHERE p.code='要確認' AND cu.name='コカコーラ'""").fetchone()[0]
        self.assertTrue(stock.product_row(c, pid)['codeNeedsReview'])
        # 再取込で増えない
        self._import(c, path, 'b')
        self.assertEqual(self._products(c), expect)
        # 初期在庫が登録先へ載る
        p = self._open(c, path, 'x')
        self.assertEqual(p['openingStockSummary']['needsReview'], 0, p['openingStockSummary'])
        lots = {(r[0], r[1], r[2]): r[3] for r in c.execute(
            """SELECT cu.name,p.code,p.name,SUM(l.qty_received) FROM lots l
               JOIN customer_products cp ON cp.id=l.customer_product_id
               JOIN customers cu ON cu.id=cp.customer_id JOIN products p ON p.id=cp.product_id
               GROUP BY 1,2,3""")}
        self.assertEqual(lots, {
            ('顧客A', '別注(顧客A)', '特注ベスト'): 3, ('顧客A', '別注(顧客A)', '特注エプロン'): 2,
            ('顧客B', '別注(顧客B)', '特注ベスト'): 4,
            ('コカコーラ', '要確認', '男パンツ（ロールイン仕様）'): 11,
        })
        # 品番を後で直しても在庫はそのまま、再取込でも同じ商品のまま
        c.execute("UPDATE products SET code='CC-PANTS' WHERE id=?", (pid,))
        c.commit()
        n = c.execute("""SELECT SUM(l.qty_received) FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
                         WHERE cp.product_id=?""", (pid,)).fetchone()[0]
        self.assertEqual(n, 11)
        s = self._open(c, path, '')['openingStockSummary']
        self.assertEqual((s['create'], s['reuse']), (0, 0), s)


class UsedStarCustomOrderTest(unittest.TestCase):
    def test_star_variant_is_same_product_and_used(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        led = ['顧客N,仕入S,別注,緑つなぎ,L', '顧客N,仕入S,別注,＊緑つなぎ,L']
        base = {'importMode': 'products', 'name': 'p.csv', 'file': b64(ledger_csv(led)),
                '_canWrite': True, '_actor': {'name': 't'}}
        for i, phase in enumerate(('parts', 'goods')):
            body = {**base, 'productPhase': phase}
            p = pli.build_preview(c, body)
            if not any(r['status'] in ('create', 'size_add', 'reuse') for r in p['rows']):
                continue
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(c, {**body, 'commitToken': f'star-used-token-{i}',
                                       'previewFingerprint': p['previewFingerprint']}, db_path=path)
        prods = [tuple(r) for r in c.execute("SELECT code,name FROM products WHERE code GLOB '別注(*)'")]
        self.assertEqual(prods, [('別注(顧客N)', '緑つなぎ')])
        op = ['新品,仕入S,顧客N,緑つなぎ,別注,L,1000,0,0,3,本社,棚1,0',
              '新品,仕入S,顧客N,＊緑つなぎ,別注,L,1000,0,0,2,本社,棚1,0']
        st = {'importMode': 'opening_stock', 'auxName': 'o.csv', 'auxFile': b64(opening_csv(op)),
              'basisDate': '2026-01-15', 'defaultSiteName': '未確認', '_canWrite': True, '_actor': {'name': 't'}}
        p = pli.build_preview(c, st)
        self.assertEqual(p['openingStockSummary']['needsReview'], 0,
                         [x.get('reasons') for x in p['openingStock']['plans']])
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(c, {**st, 'commitToken': 'star-used-token-open',
                                   'previewFingerprint': p['previewFingerprint']}, db_path=path)
        lots = sorted((r[0], r[1]) for r in c.execute('SELECT condition, qty_received FROM lots'))
        self.assertEqual(lots, sorted([(stock.CONDITION_NEW, 3), (stock.CONDITION_USED, 2)]))


class ReopenMergedRowsTest(unittest.TestCase):
    def test_reimport_after_merged_rows_is_unchanged(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        led = ['顧客L,仕入S,7-851,イージーパンツ,L']
        base = {'importMode': 'products', 'name': 'p.csv', 'file': b64(ledger_csv(led)),
                '_canWrite': True, '_actor': {'name': 't'}}
        for i, phase in enumerate(('parts', 'goods')):
            body = {**base, 'productPhase': phase}
            p = pli.build_preview(c, body)
            if not any(r['status'] in ('create', 'size_add', 'reuse') for r in p['rows']):
                continue
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(c, {**body, 'commitToken': f'reopen-merge-token-{i}',
                                       'previewFingerprint': p['previewFingerprint']}, db_path=path)
        op = ['新品,仕入S,顧客L,イージーパンツ,7-851,L,3240,0,0,2,本社,棚1,0',
              '新品,仕入S,顧客L,イージーパンツ,7-851,L,2970,0,0,1,本社,棚1,0']
        st = {'importMode': 'opening_stock', 'auxName': 'o.csv', 'auxFile': b64(opening_csv(op)),
              'basisDate': '2026-01-15', 'defaultSiteName': '未確認', '_canWrite': True, '_actor': {'name': 't'}}
        p = pli.build_preview(c, st)
        self.assertEqual(p['openingStockSummary']['merged'], 1)
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(c, {**st, 'commitToken': 'reopen-merge-token-open',
                                   'previewFingerprint': p['previewFingerprint']}, db_path=path)
        c.commit()
        self.assertEqual(c.execute('SELECT SUM(qty_received) FROM lots').fetchone()[0], 3)
        s = pli.build_preview(c, st)['openingStockSummary']
        self.assertEqual((s['needsReview'], s['create'], s['reuse'], s['unchanged'], s['merged']),
                         (0, 0, 0, 1, 1), s)


if __name__ == '__main__':
    unittest.main()
