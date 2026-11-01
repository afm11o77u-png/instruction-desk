"""取扱 → 加工サービスのリンク（テストDBのみ）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import stock  # noqa: E402
import product_registry_wipe as wipe  # noqa: E402
from test_ledger_color_split_register import newdb  # noqa: E402
from test_processing_service_no_code import commit  # noqa: E402
import product_ledger_import as pli  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv  # noqa: E402


def links(c, cp_id):
    return [r['name'] for r in c.execute(
        'SELECT p.name FROM customer_product_processes x JOIN products p ON p.id=x.process_product_id '
        'WHERE x.customer_product_id=? ORDER BY x.sort_order', (cp_id,))]


class HandleProcessLinkTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_import_links_handle_to_service(self):
        commit(self.c, self.path, ['顧客A,仕入先X,ABC-200,ポロシャツ（刺繍）,L'])
        cp = self.c.execute(
            "SELECT cp.id FROM customer_products cp JOIN products p ON p.id=cp.product_id WHERE p.code='ABC-200'"
        ).fetchone()
        self.assertEqual(links(self.c, cp['id']), ['刺繍加工'])

    def test_migration_backfills_by_name(self):
        c = self.c
        c.execute('DROP TABLE customer_product_processes')
        t = stock.stamp()
        cust = c.execute("SELECT id FROM customers LIMIT 1").fetchone() or None
        if cust is None:
            c.execute("INSERT INTO customers(name,created_at,updated_at) VALUES('顧客Z',?,?)", (t, t))
            cust = c.execute("SELECT id FROM customers LIMIT 1").fetchone()
        svc = c.execute("INSERT INTO products(code,name,color,category,active,created_at,updated_at) "
                        "VALUES('','刺繍加工','',?,1,?,?)", (stock.CATEGORY_PROCESS_SERVICE, t, t)).lastrowid
        body = c.execute("INSERT INTO products(code,name,color,category,active,created_at,updated_at) "
                         "VALUES('X1','シャツ','',?,1,?,?)", (stock.CATEGORY_UNIFORM, t, t)).lastrowid
        cp = c.execute("INSERT INTO customer_products(customer_id,product_id,embroidery,is_standard,active,created_at,updated_at) "
                       "VALUES(?,?,'刺繍',1,1,?,?)", (cust['id'], body, t, t)).lastrowid
        self.assertTrue(stock.needs_migration(c))
        stock.ensure_handle_process_schema(c)
        self.assertEqual(links(c, cp), ['刺繍加工'])
        self.assertEqual(c.execute('SELECT embroidery FROM customer_products WHERE id=?', (cp,)).fetchone()[0], '刺繍')
        self.assertTrue(svc)

    def test_set_rejects_non_service(self):
        commit(self.c, self.path, ['顧客A,仕入先X,ABC-200,ポロシャツ（刺繍）,L'])
        body = self.c.execute("SELECT id FROM products WHERE code='ABC-200'").fetchone()['id']
        cp = self.c.execute('SELECT id FROM customer_products WHERE product_id=?', (body,)).fetchone()['id']
        with self.assertRaises(ValueError):
            stock.set_handle_processes(self.c, cp, [body])
        stock.set_handle_processes(self.c, cp, [])
        self.assertEqual(links(self.c, cp), [])

    def test_unresolved_processing_goes_to_review(self):
        def row(emb):
            return {'status': 'create', 'product': {}, 'reasons': [],
                    'parenClassification': {'processing': emb},
                    'handle': {'embroidery': emb, 'fromParenClassification': True}}
        bad, ok, sales = row('謎XYZ'), row('刺繍'), row('')
        sales['handle']['embroidery'] = '販売'
        n = pli._mark_unresolved_process_links(self.c, [bad, ok, sales])
        self.assertEqual(n, 1)
        self.assertEqual(bad['status'], 'needs_review')
        self.assertEqual(bad['processLinkMissing'], '謎XYZ')
        self.assertNotIn('processLinkMissing', ok)

    def test_wipe_clears_links(self):
        commit(self.c, self.path, ['顧客A,仕入先X,ABC-200,ポロシャツ（刺繍）,L'])
        self.assertEqual(wipe.count_targets(self.c)['customerProductProcesses'], 1)
        wipe._delete_registry(self.c)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM customer_product_processes').fetchone()[0], 0)

    def test_save_product_with_process_ids(self):
        commit(self.c, self.path, ['顧客A,仕入先X,ABC-200,ポロシャツ（刺繍）,L'])
        row = stock.product_row(self.c, self.c.execute("SELECT id FROM products WHERE code='ABC-200'").fetchone()['id'])
        svc = row['links'][0]['processes'][0]['id']
        body = {k: row[k] for k in ('id', 'code', 'name', 'color', 'category', 'supplierId', 'sizes', 'active')}
        body['links'] = [{'customerId': row['links'][0]['customerId'], 'embroidery': '', 'isStandard': True,
                          'active': True, 'processIds': [svc]}]
        out = stock.save_product(self.c, body)
        new = [l for l in out['links'] if l['embroidery'] == '刺繍加工']
        self.assertEqual([p['id'] for p in new[0]['processes']], [svc])


if __name__ == '__main__':
    unittest.main()
