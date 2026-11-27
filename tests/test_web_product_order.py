"""② の↑↓で決めた制服の表示順（顧客ごと・商品単位）を保存し、依頼画面で並べる。"""
import sqlite3
import unittest
from pathlib import Path
import web_request_product_display as d

ROOT = Path(__file__).resolve().parents[1]
OWNER = dict(id=1, role='owner', manage=0)


class ProductOrderTest(unittest.TestCase):
    def test_save_and_read_order(self):
        c = sqlite3.connect(':memory:'); d.schema(c)
        owned = {1: {10, 11, 12}, 2: {20}}
        self.assertEqual(d.save_order(c, OWNER, dict(customer_id=1, product_ids=[12, 10]), owned), [12, 10])
        self.assertEqual(d.save_order(c, OWNER, dict(customer_id=1, product_ids=[10, 12, 11]), owned), [10, 12, 11])  # 置き換え
        d.save_order(c, OWNER, dict(customer_id=2, product_ids=[20]), owned)
        self.assertEqual(d.read_order(c, [1, 2]), {'1': [10, 12, 11], '2': [20]})
        for bad in (dict(customer_id=1, product_ids=[20]), dict(customer_id=1, product_ids=[10, 10]), dict(customer_id=3, product_ids=[10]), dict(customer_id=1, product_ids=[])):
            with self.assertRaises(ValueError): d.save_order(c, OWNER, bad, owned)
        with self.assertRaises(PermissionError): d.save_order(c, dict(id=2, role='sales', manage=0), dict(customer_id=1, product_ids=[10]), owned)
        self.assertEqual(d.read_order(c, [1]), {'1': [10, 12, 11]})  # 失敗した保存は残らない

    def test_request_page_uses_order(self):
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        self.assertIn('products=productOrderSort(products);renderProducts();', js)
        self.assertIn("data-order-up=", (ROOT / 'web-request-extras.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
