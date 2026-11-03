"""bootstrap の商品一覧（まとめて取得）が、従来の商品ごとの product_row と同じJSONになること。"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock
import stock_product_merge
from tests.test_stock_lot_page import _ins, T


class BootstrapBatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = c = stock.connect(str(Path(self.tmp.name) / 't.sqlite3'))
        stock.initialize(c)
        rnd = random.Random(11)
        sups = [_ins(c, 'suppliers', {'name': f'仕入{i}', 'active': i % 3 != 0, 'created_at': T, 'updated_at': T}) for i in range(5)]
        custs = [_ins(c, 'customers', {'name': f'顧客{i}', 'office_code': str(i), 'active': 1, 'created_at': T, 'updated_at': T}) for i in range(6)]
        prods = []
        for i in range(40):
            prods.append(_ins(c, 'products', {
                'code': rnd.choice(['A-1', '要確認', '', 'b-2']), 'name': f'品{i}', 'color': rnd.choice(['', '紺']),
                'category': rnd.choice(['', '加工']), 'maker_code': rnd.choice(['', ' MK-1 ']), 'material': rnd.choice(['', '綿']),
                'supplier_id': rnd.choice(sups + [None]), 'active': rnd.random() < 0.8, 'created_at': T, 'updated_at': T}))
        for pid in prods:
            for j, lab in enumerate(rnd.sample(['S', 'M', 'L', 'LL', 'F'], rnd.randint(0, 4))):
                _ins(c, 'product_sizes', {'product_id': pid, 'label': lab, 'sort_order': rnd.randint(0, 2)})
            for j, sid in enumerate(rnd.sample(sups, rnd.randint(0, 2))):
                _ins(c, 'product_suppliers', {'product_id': pid, 'supplier_id': sid, 'sort_order': rnd.randint(0, 1), 'created_at': T})
            for j in range(rnd.randint(0, 2)):
                _ins(c, 'product_match_aliases', {'product_id': pid, 'kind': rnd.choice([stock_product_merge.ALIAS_KIND_NAME, 'code']),
                                                  'value': f'別名{pid}-{j}', 'value_key': f'k{pid}-{j}', 'created_at': T})
        cps = []
        for cu in custs:
            for pid in rnd.sample(prods, 8):
                cps.append(_ins(c, 'customer_products', {'customer_id': cu, 'product_id': pid, 'embroidery': rnd.choice(['', 'ネーム']),
                                                         'is_standard': rnd.random() < 0.3, 'active': rnd.random() < 0.9,
                                                         'created_at': T, 'updated_at': T}))
        for cp in rnd.sample(cps, 15):
            for j, pp in enumerate(rnd.sample(prods, 2)):
                _ins(c, 'customer_product_processes', {'customer_product_id': cp, 'process_product_id': pp, 'sort_order': rnd.randint(0, 1)})
        # 統合: 連鎖・環
        c.execute('UPDATE products SET merged_into_id=?, merged_at=? WHERE id=?', (prods[1], T, prods[0]))
        c.execute('UPDATE products SET merged_into_id=? WHERE id=?', (prods[2], prods[1]))
        c.execute('UPDATE products SET merged_into_id=? WHERE id=?', (prods[4], prods[3]))
        c.execute('UPDATE products SET merged_into_id=? WHERE id=?', (prods[3], prods[4]))
        c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_products_identical_json(self):
        old = [stock.product_row(self.c, r['id']) for r in self.c.execute('SELECT id FROM products ORDER BY id')]
        new = stock.product_rows_all(self.c)
        self.assertEqual(json.dumps(new, ensure_ascii=False), json.dumps(old, ensure_ascii=False))
        self.assertTrue(any(p['nameAliases'] for p in new))
        self.assertTrue(any(l['processes'] for p in new for l in p['links']))
        self.assertTrue(any(p['subSuppliers'] for p in new))

    def test_bootstrap_uses_batch(self):
        b = stock.bootstrap(self.c)
        old = [stock.product_row(self.c, r['id']) for r in self.c.execute('SELECT id FROM products ORDER BY id')]
        self.assertEqual(json.dumps(b['products'], ensure_ascii=False), json.dumps(old, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
