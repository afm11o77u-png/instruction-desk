"""未加工ラベル（加工なし）の表示・入力正規化。保存キーは空文字のまま。"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import excel_book  # noqa: E402
import stock  # noqa: E402


class EmbroideryLabelHelpersTest(unittest.TestCase):
    def test_empty_displays_as_no_process(self):
        self.assertEqual(stock.embroidery_label(''), '加工なし')
        self.assertEqual(stock.embroidery_label(None), '加工なし')
        self.assertEqual(stock.embroidery_label('  '), '加工なし')

    def test_aliases_display_as_no_process_without_rewriting_partial_text(self):
        self.assertEqual(stock.embroidery_label('刺繍なし'), '加工なし')
        self.assertEqual(stock.embroidery_label('加工なし'), '加工なし')
        self.assertEqual(stock.embroidery_label('左胸・刺繍なし'), '左胸・刺繍なし')
        self.assertEqual(stock.embroidery_label('左胸ネーム'), '左胸ネーム')

    def test_normalize_aliases_to_empty_exact_match_only(self):
        self.assertEqual(stock.normalize_embroidery(''), '')
        self.assertEqual(stock.normalize_embroidery('刺繍なし'), '')
        self.assertEqual(stock.normalize_embroidery('加工なし'), '')
        self.assertEqual(stock.normalize_embroidery('左胸・刺繍なし'), '左胸・刺繍なし')


class EmbroideryLabelPersistenceTest(unittest.TestCase):
    def test_ensure_alias_reuses_empty_handle(self):
        folder = tempfile.TemporaryDirectory()
        try:
            path = str(Path(folder.name) / 'stock.db')
            c = stock.connect(path)
            stock.initialize(c)
            cu = stock.save_customer(c, {'name': '別名顧客', 'active': True})
            pr = stock.save_product(c, {
                'code': 'A-1', 'name': '別名商品', 'color': '', 'supplierId': None,
                'active': True, 'sizes': ['M'], 'links': [],
            })
            empty = stock.ensure_customer_product(c, {
                'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '',
            })
            c.commit()
            via_old = stock.ensure_customer_product(c, {
                'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '刺繍なし',
            })
            via_new = stock.ensure_customer_product(c, {
                'customerId': cu['id'], 'productId': pr['id'], 'embroidery': '加工なし',
            })
            c.commit()
            self.assertTrue(via_old['already'])
            self.assertTrue(via_new['already'])
            self.assertEqual(via_old['id'], empty['id'])
            self.assertEqual(via_new['id'], empty['id'])
            self.assertEqual(via_old['embroidery'], '')
            self.assertEqual(via_old['embroideryLabel'], '加工なし')
            n = c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n']
            self.assertEqual(n, 1)
            stored = c.execute('SELECT embroidery FROM customer_products WHERE id=?', (empty['id'],)).fetchone()
            self.assertEqual(stored['embroidery'], '')
            c.close()
        finally:
            folder.cleanup()

    def test_excel_import_alias_matches_empty_handle(self):
        folder = tempfile.TemporaryDirectory()
        try:
            path = str(Path(folder.name) / 'stock.db')
            c = stock.connect(path)
            stock.initialize(c)
            cu = stock.save_customer(c, {'name': 'Excel顧客', 'active': True})
            pr = stock.save_product(c, {
                'code': 'E-1', 'name': 'Excel商品', 'color': '白', 'supplierId': None,
                'active': True, 'sizes': ['M'],
                'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
            })
            c.commit()
            # 取扱シートに「加工なし」を書いても空キーの既存取扱へ合流する
            blob = excel_book.workbook([
                ('顧客', ['顧客名', 'カナ', '住所', '電話番号', '担当者'], []),
                ('仕入先', ['仕入先名', '住所', '電話番号', '担当者'], []),
                ('商品', ['品番', '商品名', '色', '仕入先名', 'サイズ'], []),
                ('取扱', ['品番', '商品名', '色', '顧客名', '加工仕様', '標準'],
                 [['E-1', 'Excel商品', '白', 'Excel顧客', '加工なし', '標準']]),
                ('保管場所', ['拠点名', '棚名'], []),
                ('初期在庫', [
                    '顧客名', '品番', '商品名', '色', '加工仕様', '拠点名', '棚名',
                    '在庫基準日', '入荷日', '入荷日不明', '払出し順', '状態', 'サイズ', '数量', '仕入単価',
                ], []),
            ])
            result = excel_book.apply(c, {'file': base64.b64encode(blob).decode('ascii')})
            c.commit()
            rows = c.execute(
                'SELECT embroidery FROM customer_products WHERE product_id=? ORDER BY id',
                (pr['id'],),
            ).fetchall()
            self.assertEqual([r['embroidery'] for r in rows], [''])
            self.assertIn('取扱', result['summary'])
            c.close()
        finally:
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
