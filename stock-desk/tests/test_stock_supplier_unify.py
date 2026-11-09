import unittest
import tempfile
from pathlib import Path
import stock

class SupplierUnifyTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'stock.sqlite3'
        self.c = stock.connect(self.path)
        stock.initialize(self.c)

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_supplier_duplicate_preview_and_apply(self):
        c = self.c
        # 1. 重複仕入先を作成
        # CUP: 半角 ID 1, 全角 ID 2
        c.execute("INSERT INTO suppliers (name, created_at, updated_at) VALUES ('㈱CUP', '2026-01-01', '2026-01-01')")
        c.execute("INSERT INTO suppliers (name, created_at, updated_at) VALUES ('㈱ＣＵＰ', '2026-01-01', '2026-01-01')")
        # KAZEN: 半角 ID 3, 全角 ID 4
        c.execute("INSERT INTO suppliers (name, created_at, updated_at) VALUES ('KAZEN WLD㈱', '2026-01-01', '2026-01-01')")
        c.execute("INSERT INTO suppliers (name, created_at, updated_at) VALUES ('ＫＡＺＥＮ　ＷＬＤ㈱', '2026-01-01', '2026-01-01')")
        # エルムス: (有) ID 5, ㈲ ID 6
        c.execute("INSERT INTO suppliers (name, created_at, updated_at) VALUES ('(有)エルムスコーポレーション', '2026-01-01', '2026-01-01')")
        c.execute("INSERT INTO suppliers (name, created_at, updated_at) VALUES ('㈲エルムスコーポレーション', '2026-01-01', '2026-01-01')")
        c.commit()

        # プレビュー取得
        prev = stock.preview_supplier_duplicates(c)
        self.assertEqual(prev['count'], 3)

        kazen_group = next(g for g in prev['duplicates'] if 'kazen' in g['key'])
        # 半角の KAZEN WLD㈱ が target になっていること
        self.assertEqual(kazen_group['target']['name'], 'KAZEN WLD㈱')
        self.assertEqual(len(kazen_group['sources']), 1)
        self.assertEqual(kazen_group['sources'][0]['name'], 'ＫＡＺＥＮ　ＷＬＤ㈱')

        elms_group = next(g for g in prev['duplicates'] if 'エルムス' in g['key'])
        # 環境依存文字 ㈲エルムスコーポレーション が target になっていること
        self.assertEqual(elms_group['target']['name'], '㈲エルムスコーポレーション')

        # 権限なしではエラー
        with self.assertRaises(ValueError):
            stock.apply_supplier_duplicates_merge(c, {'isOwner': False})

        # 主管理者権限で適用
        res = stock.apply_supplier_duplicates_merge(c, {'isOwner': True})
        self.assertTrue(res['ok'])
        self.assertEqual(res['mergedCount'], 3)
        c.commit()

        # 統合後は重複が 0 件
        prev_after = stock.preview_supplier_duplicates(c)
        self.assertEqual(prev_after['count'], 0)

        # データベースに残っている仕入先を確認
        remaining = [r['name'] for r in c.execute('SELECT name FROM suppliers ORDER BY id')]
        self.assertIn('KAZEN WLD㈱', remaining)
        self.assertNotIn('ＫＡＺＥＮ　ＷＬＤ㈱', remaining)
        self.assertIn('㈲エルムスコーポレーション', remaining)
        self.assertNotIn('(有)エルムスコーポレーション', remaining)


if __name__ == '__main__':
    unittest.main()
