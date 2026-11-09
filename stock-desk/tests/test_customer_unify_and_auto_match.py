import unittest
import tempfile
from pathlib import Path
import stock
import legacy_csv_import as lci

class CustomerUnifyAndAutoMatchTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'stock.sqlite3'
        self.c = stock.connect(self.path)
        stock.initialize(self.c)

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_customer_duplicate_preview_and_apply(self):
        c = self.c
        # 1. 重複顧客を作成
        # 石屋商事: 半角 ID 1, 全角 ID 2
        c.execute("INSERT INTO customers (name, created_at, updated_at) VALUES ('石屋商事 オーバーザムーン他2ヶ所', '2026-01-01', '2026-01-01')")
        c.execute("INSERT INTO customers (name, created_at, updated_at) VALUES ('石屋商事　オーバーザムーン他2ヶ所', '2026-01-01', '2026-01-01')")
        # 柳月: ㈱あり ID 3, ㈱なし ID 4
        c.execute("INSERT INTO customers (name, created_at, updated_at) VALUES ('㈱柳月', '2026-01-01', '2026-01-01')")
        c.execute("INSERT INTO customers (name, created_at, updated_at) VALUES ('柳月', '2026-01-01', '2026-01-01')")
        c.commit()

        # プレビュー取得
        prev = stock.preview_customer_duplicates(c)
        self.assertEqual(prev['count'], 2)

        ishiya = next(g for g in prev['duplicates'] if '石屋商事' in g['target']['name'])
        # 半角スペース側が target になっていること
        self.assertEqual(ishiya['target']['name'], '石屋商事 オーバーザムーン他2ヶ所')

        ryugetsu = next(g for g in prev['duplicates'] if '柳月' in g['target']['name'])
        # ㈱あり側が target になっていること
        self.assertEqual(ryugetsu['target']['name'], '㈱柳月')

        # 統合適用
        res = stock.apply_customer_duplicates_merge(c, {'isOwner': True})
        self.assertTrue(res['ok'])
        self.assertEqual(res['mergedCount'], 2)
        c.commit()

        # 統合後の検証
        prev_after = stock.preview_customer_duplicates(c)
        self.assertEqual(prev_after['count'], 0)
        remaining = [r['name'] for r in c.execute('SELECT name FROM customers ORDER BY id')]
        self.assertIn('石屋商事 オーバーザムーン他2ヶ所', remaining)
        self.assertNotIn('石屋商事　オーバーザムーン他2ヶ所', remaining)
        self.assertIn('㈱柳月', remaining)
        self.assertNotIn('柳月', remaining)

    def test_single_form_variant_auto_reuses_without_review(self):
        c = self.c
        # 既存登録: 「外注　臼井正夫」（全角スペース）が1件だけ登録されている
        c.execute("INSERT INTO suppliers (name, created_at, updated_at) VALUES ('外注　臼井正夫', '2026-01-01', '2026-01-01')")
        c.commit()

        # CSV側が「外注 臼井正夫」（半角スペース）の場合
        info = lci._person_preview(c, 'suppliers', '仕入先', '外注 臼井正夫', 1)
        # 1件だけなので review ではなく自動で reuse になること！
        self.assertEqual(info['action'], 'reuse')
        self.assertEqual(len(info['reasons']), 0)


if __name__ == '__main__':
    unittest.main()
