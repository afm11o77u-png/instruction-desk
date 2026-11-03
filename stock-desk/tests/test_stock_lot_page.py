"""在庫明細の高速版（stock_lot_page）が従来（slim_stock_view(stock_query)）と同じ結果を返すこと。"""
from __future__ import annotations

import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock

ROOT = Path(__file__).resolve().parents[1]
T = '2026-01-01T00:00:00'
NAMES = ['㈱あおば', '（株）あおば', 'あおば', '株式会社 いろは', 'ｉｒｏｈａ', 'Iroha', '有限会社うみ', 'うみ',
         '㊑かぜ', 'NPG', 'ＮＰＧ', 'さくら', '(株)さくら', 'わかば']


def _ins(c, table, d):
    cols = {r['name']: r for r in c.execute(f'PRAGMA table_info({table})')}
    for k, r in cols.items():
        if k not in d and r['notnull'] and r['dflt_value'] is None and not r['pk']:
            d[k] = T if k.endswith('_at') else 0
    c.execute(f"INSERT INTO {table}({','.join(d)}) VALUES({','.join('?' * len(d))})", list(d.values()))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


class StockLotPageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = stock.connect(str(Path(self.tmp.name) / 't.sqlite3'))
        stock.initialize(self.c)
        c = self.c
        rnd = random.Random(7)
        self.site = _ins(c, 'locations', {'name': '本社', 'active': 1, 'created_at': T, 'updated_at': T})
        site2 = _ins(c, 'locations', {'name': '倉庫B', 'active': 1, 'created_at': T, 'updated_at': T})
        shelves = [_ins(c, 'locations', {'name': f'棚{i}', 'parent_id': self.site, 'active': 1,
                                         'created_at': T, 'updated_at': T}) for i in range(3)]
        locs = shelves + [self.site, site2]
        sup = _ins(c, 'suppliers', {'name': '仕入先A', 'created_at': T, 'updated_at': T})
        custs = [_ins(c, 'customers', {'name': n, 'office_code': str(1000 + i), 'active': 1,
                                       'created_at': T, 'updated_at': T}) for i, n in enumerate(NAMES)]
        prods = [_ins(c, 'products', {'code': rnd.choice(['A-1', 'A-2', 'B-1', 'b-1', 'Ｃ-1']), 'name': f'品{i}',
                                      'color': '', 'supplier_id': sup, 'active': 1,
                                      'created_at': T, 'updated_at': T}) for i in range(8)]
        cps = []
        for cu in custs:
            for p in rnd.sample(prods, 3):
                cps.append(_ins(c, 'customer_products', {'customer_id': cu, 'product_id': p,
                                                         'embroidery': rnd.choice(['', '', 'ネーム']), 'active': 1,
                                                         'created_at': T, 'updated_at': T}))
        self.cust, self.prod = custs[0], prods[0]
        for _ in range(400):
            q = rnd.randint(-5, 20)
            pending = 1 if rnd.random() < 0.15 else 0
            lot = _ins(c, 'lots', {
                'customer_product_id': rnd.choice(cps), 'size_label': rnd.choice(['S', 'M', 'L', 'F']),
                'location_id': rnd.choice(locs), 'unit_cost': 0 if pending else rnd.randint(100, 3000),
                'qty_received': max(q, 0) + rnd.choice([0, 0, 3]), 'qty_remaining': q,
                'basis_date': '2026-01-01', 'received_on': '2026-01-01', 'received_unknown': 0, 'payout_order': 0,
                'condition': rnd.choice(['new', 'new', 'used']), 'cost_pending': pending,
                'created_at': T, 'updated_at': T})
            for kind in rnd.sample(['opening', 'opening_correction', 'inbound', 'outbound', 'stocktake'],
                                   rnd.randint(1, 2)):
                _ins(c, 'moves', {'lot_id': lot, 'kind': kind, 'qty_delta': 1, 'unit_cost': 0,
                                  'location_id': locs[0], 'business_on': '2026-01-01', 'note': '', 'at': T})
        c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_same_as_full_query(self):
        filters = [
            {}, {'group': 'lot'}, {'customerId': str(self.cust)}, {'productId': str(self.prod)},
            {'size': 'M'}, {'spec': 'none'}, {'spec': 'ネーム'}, {'locationId': str(self.site)},
            {'condition': 'used'}, {'stockFilter': 'shortage'}, {'size': 'XXX'},
        ]
        for f in filters:
            for limit in (None, -1, 0, 1, 7, 200, 1000):
                with self.subTest(filters=f, limit=limit):
                    old = stock.slim_stock_view(stock.stock_query(self.c, dict(f)), lot_limit=limit)
                    new = stock.stock_lot_page(self.c, dict(f), lot_limit=limit)
                    self.assertEqual(new, old)

    def test_order_follows_customer_sort_key(self):
        lots = stock.stock_lot_page(self.c, {}, lot_limit=None)['lots']
        keys = [(stock.customer_sort_key(x['customerName']), x['customerName']) for x in lots]
        self.assertEqual(keys, sorted(keys))
        self.assertGreater(len({x['customerId'] for x in lots}), 5)

    def test_server_uses_page_for_lot_view(self):
        src = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn('stock.stock_lot_page(c, args, lot_limit=lot_limit)', src)


if __name__ == '__main__':
    unittest.main()
