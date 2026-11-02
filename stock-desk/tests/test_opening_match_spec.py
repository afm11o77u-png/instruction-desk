"""初期在庫: 仕様（○○ライン）で取扱を絞る／加工サービスは在庫対象外／品番なし部品は名前で照合（テストDBのみ）"""
import sys, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stock
import product_ledger_import as pli
from test_ledger_color_split_register import newdb, b64, ledger_csv, opening_csv

ROWS = [('顧客A', '仕入X', 'H1', '帽子(青) 赤ライン', 'F'), ('顧客A', '仕入X', 'H1', '帽子(青) 青ライン', 'F'),
        ('顧客A', '仕入X', 'H1', '帽子(青)', 'F'),
        ('顧客A', '生地屋', 'FB1', 'ツイル生地(紺)', 'F'), ('顧客A', '生地屋', 'FB1', 'ツイル生地(黒)', 'F'),
        ('顧客A', '加工屋', '', 'スナッパ加工', 'F'), ('顧客B', '加工屋', '', 'スナッパー加工', 'F'),
        ('ヨシツネ食品', 'オリゾン(有)', 'CJ2401-2', 'クリーンウェア インナー加工', 'M')]


class OpeningMatchSpecTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()
        t = stock.stamp()
        self.c.execute("INSERT INTO products(code,name,color,category,active,created_at,updated_at) VALUES('','ライン加工','',?,1,?,?)",
                       (stock.CATEGORY_PROCESS_SERVICE, t, t))
        base = {'importMode': 'products', 'name': 'p.csv', 'file': b64(ledger_csv([','.join(r) for r in ROWS])),
                '_canWrite': True, '_actor': {'name': 't'}}
        for i, ph in enumerate(('parts', 'goods')):
            b = {**base, 'productPhase': ph}
            p = pli.build_preview(self.c, b)
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                pli.commit_preview(self.c, {**b, 'commitToken': f'tok-oms-{i}', 'previewFingerprint': p['previewFingerprint']}, db_path=self.path)
        O = [f'新品,{s},{cu},{nm},{cd},{sz},1000,0,0,{i + 1},本社,棚1,0' for i, (cu, s, cd, nm, sz) in enumerate(ROWS)]
        self.stage = {'importMode': 'opening_stock', 'auxName': '初期在庫.csv', 'auxFile': b64(opening_csv(O)),
                      'basisDate': '2026-01-15', 'defaultSiteName': '未確認', '_canWrite': True, '_actor': {'name': 't'}}

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_end_to_end(self):
        p = pli.build_preview(self.c, self.stage)
        plans = {pl['rowNo']: pl for pl in p['openingStock']['plans']}
        self.assertEqual([plans[n]['target']['embroidery'] for n in (2, 3, 4)], ['赤ライン', '青ライン', ''])
        for n in (5, 6):
            self.assertEqual(plans[n]['status'], 'create', plans[n].get('reasons'))
        for n in (7, 8, 9):
            self.assertEqual(plans[n]['status'], 'excluded')
            self.assertEqual(plans[n]['reasons'], ['在庫対象外（加工サービス）'])
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(self.c, {**self.stage, 'commitToken': 'tok-oms-open', 'previewFingerprint': p['previewFingerprint']}, db_path=self.path)
        lots = sorted(tuple(r) for r in self.c.execute(
            "SELECT p.name,cp.embroidery,l.qty_received FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id JOIN products p ON p.id=cp.product_id"))
        self.assertEqual(lots, sorted([('帽子', '赤ライン', 1), ('帽子', '青ライン', 2), ('帽子', '', 3),
                                       ('ツイル生地(紺)', '加工未整理', 4), ('ツイル生地(黒)', '加工未整理', 5)]))


if __name__ == '__main__':
    unittest.main()
