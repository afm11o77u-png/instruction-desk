"""取扱の区分（販売／レンタル）と加工（temp DB only）."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock
from test_purchase_orders import newdb


class HandlingKindTest(unittest.TestCase):
    def setUp(self):
        self.folder, _, self.c = newdb()
        self.cu = stock.save_customer(self.c, {'name': '区分テスト病院', 'active': True})
        self.su = stock.save_supplier(self.c, {'name': '区分仕入先', 'active': True})
        self.pr = stock.save_product(self.c, {'code': 'HK-1', 'name': '白衣', 'supplierId': self.su['id'],
                                              'active': True, 'sizes': ['M'], 'links': []})

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def kinds(self):
        return {r['embroidery']: r['handling_kind'] for r in
                self.c.execute('SELECT embroidery,handling_kind FROM customer_products')}

    def test_migration_rule_and_idempotent(self):
        for emb in ['販売', '刺繍｜販売', '病院名', 'リース', '']:
            self.c.execute("INSERT INTO customer_products(customer_id,product_id,embroidery,created_at,updated_at)"
                           " VALUES(?,?,?,'x','x')", (self.cu['id'], self.pr['id'], emb))
        self.c.execute('UPDATE customer_products SET handling_kind=NULL')
        self.assertTrue(stock.needs_migration(self.c))
        stock.migrate_handling_kind(self.c)
        want = {'販売': '販売', '刺繍｜販売': '販売', '病院名': 'レンタル', 'リース': 'レンタル', '': 'レンタル'}
        self.assertEqual(self.kinds(), want)
        stock.migrate_handling_kind(self.c)
        self.assertEqual(self.kinds(), want)
        self.assertEqual(stock.handling_processing('販売'), '')
        self.assertEqual(stock.handling_processing('リース'), '')
        self.assertEqual(stock.handling_processing('刺繍｜販売'), '刺繍')

    def test_ensure_with_kind(self):
        stock.ensure_customer_product(self.c, {'customerId': self.cu['id'], 'productId': self.pr['id'],
                                               'embroidery': '刺繍', 'handlingKind': '販売'})
        stock.ensure_customer_product(self.c, {'customerId': self.cu['id'], 'productId': self.pr['id'],
                                               'embroidery': '刺繍', 'handlingKind': 'レンタル'})
        k = self.kinds()
        self.assertEqual(k.get('刺繍｜販売'), '販売')
        self.assertEqual(k.get('刺繍'), 'レンタル')
        with self.assertRaises(ValueError):
            stock.ensure_customer_product(self.c, {'customerId': self.cu['id'], 'productId': self.pr['id'],
                                                   'embroidery': '', 'handlingKind': 'リース'})

    def test_save_product_links(self):
        body = {'id': self.pr['id'], 'code': 'HK-1', 'name': '白衣', 'supplierId': self.su['id'],
                'active': True, 'sizes': ['M'],
                'links': [{'customerId': self.cu['id'], 'embroidery': '', 'handlingKind': '販売',
                           'isStandard': True, 'active': True}]}
        stock.save_product(self.c, body)
        self.assertEqual(self.kinds(), {'販売': '販売'})
        self.assertIn('販売', stock.embroidery_label('販売'))


if __name__ == '__main__':
    unittest.main()
