"""「加工未整理」取扱は表示「加工なし」。保存値は変えず、加工なしの入力は既存の加工未整理取扱へ寄せる（テストDBのみ）。"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stock
from test_ledger_color_split_register import newdb


class PendingLabelTest(unittest.TestCase):
    def test_label(self):
        self.assertEqual(stock.embroidery_label('加工未整理'), '加工なし')
        self.assertEqual(stock.embroidery_label(''), '加工なし')
        self.assertEqual(stock.embroidery_label('赤ライン'), '赤ライン')

    def test_match(self):
        self.assertEqual(stock.match_existing_spec(['加工未整理'], '加工なし'), '加工未整理')
        self.assertEqual(stock.match_existing_spec(['加工未整理', ''], ''), '')
        self.assertEqual(stock.match_existing_spec(['刺繍'], ''), '')

    def test_save_keeps_stored_value(self):
        folder, path, c = newdb()
        try:
            t = stock.stamp()
            cid = c.execute("INSERT INTO customers(name,created_at,updated_at) VALUES('顧客A',?,?)", (t, t)).lastrowid
            pid = stock.save_product(c, {'code': 'X1', 'name': '服', 'sizes': ['L'],
                                         'links': [{'customerId': cid, 'embroidery': '加工未整理'}]})['id']
            self.assertEqual(stock.normalize_embroidery('加工未整理'), '加工未整理')
            stock.save_product(c, {'id': pid, 'code': 'X1', 'name': '服', 'sizes': ['L'],
                                   'links': [{'customerId': cid, 'embroidery': '加工なし'}]})
            rows = [r[0] for r in c.execute('SELECT embroidery FROM customer_products WHERE product_id=?', (pid,))]
            self.assertEqual(rows, ['加工未整理'])
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
