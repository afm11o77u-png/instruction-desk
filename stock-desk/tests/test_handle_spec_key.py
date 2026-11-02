"""取扱の仕様: 表記ゆれ（全角半角・空白）は同じ仕様として扱う。保存値は変えない（テストDBのみ）。"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import stock  # noqa: E402


class HandleSpecKeyTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.c = stock.connect(str(Path(self.folder.name) / 'stock.db'))
        stock.initialize(self.c)
        self.cu = stock.save_customer(self.c, {'name': '顧客A', 'active': True})
        self.pr = stock.save_product(self.c, {
            'code': 'H1', 'name': '帽子', 'color': 'ブルー', 'supplierId': None,
            'active': True, 'sizes': ['F'], 'links': [],
        })

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def ensure(self, spec):
        return stock.ensure_customer_product(self.c, {
            'customerId': self.cu['id'], 'productId': self.pr['id'], 'embroidery': spec})

    def count(self):
        return self.c.execute('SELECT COUNT(*) FROM customer_products').fetchone()[0]

    def test_key(self):
        self.assertEqual(stock.spec_key('赤 ライン'), stock.spec_key('赤ライン'))
        self.assertEqual(stock.spec_key('ＡＢ　1'), stock.spec_key('AB1'))
        self.assertEqual(stock.spec_key('加工なし'), '')

    def test_two_specs_per_customer_product(self):
        red = self.ensure('赤ライン')
        blue = self.ensure('青ライン')
        self.assertNotEqual(red['id'], blue['id'])
        self.assertEqual(self.count(), 2)

    def test_variant_spelling_reuses(self):
        red = self.ensure('赤ライン')
        again = self.ensure('赤　ライン')
        self.assertTrue(again['already'])
        self.assertEqual(again['id'], red['id'])
        stored = self.c.execute('SELECT embroidery FROM customer_products').fetchone()[0]
        self.assertEqual(stored, '赤ライン')

    def base(self, links):
        return {'id': self.pr['id'], 'code': 'H1', 'name': '帽子', 'color': 'ブルー', 'supplierId': None,
                'active': True, 'sizes': ['F'], 'links': links}

    def test_editor_rejects_variant_duplicate(self):
        with self.assertRaises(ValueError):
            stock.save_product(self.c, self.base([
                {'customerId': self.cu['id'], 'embroidery': '赤ライン'},
                {'customerId': self.cu['id'], 'embroidery': '赤 ライン'},
            ]))

    def test_editor_keeps_existing_spelling(self):
        red = self.ensure('赤ライン')
        stock.save_product(self.c, self.base([{'customerId': self.cu['id'], 'embroidery': '赤 ライン', 'isStandard': True}]))
        rows = self.c.execute('SELECT id, embroidery FROM customer_products').fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [(red['id'], '赤ライン')])


class SpecLabelUiTest(unittest.TestCase):
    def test_ui_labels_unified(self):
        for name in ('app.js', 'purchase_orders.js'):
            src = (ROOT / 'static' / name).read_text(encoding='utf-8')
            self.assertIsNone(re.search('加工仕様|加工情報|仕様メモ|加工\\(社内\\)', src), name)


if __name__ == '__main__':
    unittest.main()
