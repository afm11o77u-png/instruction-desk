import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import stock  # noqa: E402
import stock_process_backfill  # noqa: E402


class ProcessBackfillTest(unittest.TestCase):
    def setUp(self):
        self.c = stock.connect(':memory:')
        stock.initialize(self.c)
        self.t = stock.now()
        self.cid = self.c.execute(
            'INSERT INTO customers(name,created_at,updated_at) VALUES(?,?,?)',
            ('㈱ＬＥＯＣ', self.t, self.t),
        ).lastrowid
        self.proc_id = self.c.execute(
            'INSERT INTO products(code,name,category,created_at,updated_at) VALUES(?,?,?,?,?)',
            ('', '刺繍', stock.CATEGORY_PROCESS_SERVICE, self.t, self.t),
        ).lastrowid
        self.p1 = self.c.execute(
            'INSERT INTO products(code,name,category,created_at,updated_at) VALUES(?,?,?,?,?)',
            ('71-221', '栄養士コート', stock.CATEGORY_UNIFORM, self.t, self.t),
        ).lastrowid
        self.p2 = self.c.execute(
            'INSERT INTO products(code,name,category,created_at,updated_at) VALUES(?,?,?,?,?)',
            ('73-1422', 'ナースコート', stock.CATEGORY_UNIFORM, self.t, self.t),
        ).lastrowid
        self.cp1 = self.c.execute(
            'INSERT INTO customer_products(customer_id,product_id,embroidery,handling_kind,created_at,updated_at) VALUES(?,?,?,?,?,?)',
            (self.cid, self.p1, stock.PENDING_EMBROIDERY, 'none', self.t, self.t),
        ).lastrowid
        self.cp2 = self.c.execute(
            'INSERT INTO customer_products(customer_id,product_id,embroidery,handling_kind,created_at,updated_at) VALUES(?,?,?,?,?,?)',
            (self.cid, self.p2, stock.PENDING_EMBROIDERY, 'none', self.t, self.t),
        ).lastrowid
        # cp1 already has process service link; cp2 only has legacy CSV row map
        self.c.execute(
            'INSERT INTO customer_product_processes(customer_product_id,process_product_id,sort_order) VALUES(?,?,0)',
            (self.cp1, self.proc_id),
        )
        self.c.execute(
            '''INSERT INTO legacy_csv_row_maps(product_map_key,stock_map_key,csv_customer,csv_product_code,
               csv_product_name,customer_id,product_id,customer_product_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)''',
            ('k1', 's1', '㈱ＬＥＯＣ', '73-1422', 'ナースコート(刺繍)', self.cid, self.p2, self.cp2, self.t, self.t),
        )

    def tearDown(self):
        self.c.close()

    def test_preview_and_commit(self):
        plan = stock_process_backfill.preview(self.c)
        self.assertEqual(plan['count'], 2)
        by_cp = {i['cpId']: i for i in plan['items']}
        self.assertEqual(by_cp[self.cp1]['proposedLabel'], '刺繍')
        self.assertEqual(by_cp[self.cp2]['proposedLabel'], '刺繍')
        self.assertEqual(by_cp[self.cp2]['addProcessIds'], [self.proc_id])

        res = stock_process_backfill.commit(self.c, {})
        self.assertEqual(res['updated'], 2)
        self.assertEqual(res['processesAdded'], 1)
        row1 = self.c.execute('SELECT embroidery FROM customer_products WHERE id=?', (self.cp1,)).fetchone()
        row2 = self.c.execute('SELECT embroidery FROM customer_products WHERE id=?', (self.cp2,)).fetchone()
        self.assertEqual(row1['embroidery'], '刺繍')
        self.assertEqual(row2['embroidery'], '刺繍')
        # Re-running finds 0 pending items
        self.assertEqual(stock_process_backfill.preview(self.c)['count'], 0)
