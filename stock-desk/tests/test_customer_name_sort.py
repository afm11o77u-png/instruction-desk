"""顧客名の並び替え: 先頭の会社種別（（株）・(株)・㈱・株式会社など）を無視する。登録名は変えない。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock

NAMES = ['（株）しめまる', '(株) しめまる', '㈱しめまる', '株式会社　しめまる', '㈱ロトス', 'ワタリ', 'あおば', '有限会社かもめ', '㈲いろは', '合同会社さくら', '㈹たける']


class SortKeyTest(unittest.TestCase):
    def test_markers_stripped(self):
        for n in ['（株）しめまる', '(株)しめまる', '(株) しめまる', '㈱しめまる', '株式会社しめまる', '株式会社　しめまる', '（ 株 ）しめまる']:
            self.assertEqual(stock.customer_sort_key(n), 'しめまる', n)
        self.assertEqual(stock.customer_sort_key('有限会社かもめ'), 'かもめ')
        self.assertEqual(stock.customer_sort_key('㈲いろは'), 'いろは')
        self.assertEqual(stock.customer_sort_key('合同会社さくら'), 'さくら')
        self.assertEqual(stock.customer_sort_key('㈹たける'), 'たける')
        self.assertEqual(stock.customer_sort_key('株田商店'), '株田商店')  # 種別でない「株」は残す
        self.assertEqual(stock.customer_sort_key('しめまる（株）'), 'しめまる（株）'.replace('（', '(').replace('）', ')'))
        self.assertEqual(stock.customer_sort_key('株式会社'), '株式会社')

    def test_romaji_together(self):
        self.assertEqual(stock.customer_sort_key('ＮＰＧコトメンフーズ'), 'npgコトメンフーズ')
        names = ['あおば', 'ＮＰＧコトメンフーズ', 'Umiosu', 'abc商事', '㈱ロトス']
        order = sorted(names, key=lambda n: (stock.customer_sort_key(n), n))
        self.assertEqual(order, ['abc商事', 'ＮＰＧコトメンフーズ', 'Umiosu', 'あおば', '㈱ロトス'])

    def test_order(self):
        order = sorted(NAMES, key=lambda n: (stock.customer_sort_key(n), n))
        keys = [stock.customer_sort_key(n) for n in order]
        self.assertEqual(keys, sorted(keys))
        self.assertLess(order.index('あおば'), order.index('（株）しめまる'))
        self.assertLess(order.index('㈱しめまる'), order.index('たける' if False else '㈹たける'))


def seed(c):
    su = stock.save_supplier(c, {'name': '仕入先', 'active': True})
    pr = stock.save_product(c, {'code': 'SRT-1', 'name': '並び替え', 'color': '白', 'supplierId': su['id'],
                                'active': True, 'sizes': ['M'], 'links': []})
    loc = c.execute('SELECT id FROM locations ORDER BY id LIMIT 1').fetchone()
    cids = []
    for n in ['（株）しめまる', 'あおば', '㈱ロトス', 'ワタリ', '株式会社いぶき']:
        cids.append(stock.save_customer(c, {'name': n, 'active': True})['id'])
    T = '2026-01-01'
    for cid in cids:
        c.execute('INSERT INTO customer_products(customer_id,product_id,embroidery,created_at,updated_at) VALUES(?,?,?,?,?)', (cid, pr['id'], '', T, T))
        cp = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO lots(customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,basis_date,condition,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (cp, 'M', loc[0] if loc else None, 100, 1, 1, T, 'new', T, T))
        c.execute("INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,at) VALUES(last_insert_rowid(),'opening',1,100,?)", (T,))
    c.commit()


class QueryOrderTest(unittest.TestCase):
    def test_stock_query_and_groups(self):
        with tempfile.TemporaryDirectory() as d:
            c = stock.connect(Path(d) / 's.sqlite3')
            stock.initialize(c)
            if not c.execute('SELECT 1 FROM locations').fetchone():
                stock.save_location(c, {'name': '倉庫', 'active': True})
            seed(c)
            expect = ['あおば', '株式会社いぶき', '（株）しめまる', '㈱ロトス', 'ワタリ']
            lots = stock.stock_query(c, {'group': 'lot'})['lots']
            self.assertEqual([l['customerName'] for l in lots], expect)
            limited = stock.slim_stock_view(stock.stock_query(c, {'group': 'lot'}), lot_limit=2) if 'lot_limit' in stock.slim_stock_view.__code__.co_varnames else None
            if limited:
                self.assertEqual([l['customerName'] for l in limited['lots']], expect[:2])
            groups = stock.stock_query(c, {'group': 'customer'})['groups']
            self.assertEqual([g['customerName'] for g in groups], expect)
            names = {r[0] for r in c.execute('SELECT name FROM customers')}
            self.assertIn('（株）しめまる', names)  # 登録名は変えない
            c.close()


if __name__ == '__main__':
    unittest.main()


class MoreMarkersTest(unittest.TestCase):
    def test_welfare_and_other_markers(self):
        cases = [
            '（福）やまびこ', '(福)やまびこ', '(福) やまびこ', '社会福祉法人やまびこ', '社会福祉法人　やまびこ',
            '（医）やまびこ', '㊩やまびこ', '医療法人やまびこ', '医療法人社団やまびこ', '医療法人財団やまびこ',
            '（財）やまびこ', '㈶やまびこ', '財団法人やまびこ', '一般財団法人やまびこ', '公益財団法人やまびこ',
            '（社）やまびこ', '㈳やまびこ', '社団法人やまびこ', '一般社団法人やまびこ', '公益社団法人やまびこ',
            '（学）やまびこ', '㈻やまびこ', '学校法人やまびこ',
            '（特非）やまびこ', 'NPO法人やまびこ', 'ＮＰＯ法人やまびこ', '特定非営利活動法人やまびこ',
            '株式会社やまびこ', '㊑やまびこ', '(資)やまびこ', '㈾やまびこ',
        ]
        for n in cases:
            self.assertEqual(stock.customer_sort_key(n), 'やまびこ', n)

    def test_not_stripped_inside_or_bare(self):
        self.assertEqual(stock.customer_sort_key('福田商店'), '福田商店')
        self.assertEqual(stock.customer_sort_key('医大前薬局'), '医大前薬局')
        self.assertEqual(stock.customer_sort_key('やまびこ(福)'), 'やまびこ(福)')
        self.assertEqual(stock.customer_sort_key('社会福祉法人'), '社会福祉法人')
