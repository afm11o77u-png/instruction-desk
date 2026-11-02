"""区分ごとの表示番号（K-0001 / B-0001 / S-00001）。内部 id は変えない。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock  # noqa: E402
import product_ledger_import as pli  # noqa: E402
import product_registry_wipe as wipe  # noqa: E402
from test_ledger_color_split_register import newdb, b64, ledger_csv  # noqa: E402


def add(c, cat, name='n'):
    t = '2026-01-01'
    cur = c.execute("INSERT INTO products(code,name,category,created_at,updated_at) VALUES('',?,?,?,?)",
                    (name, cat, t, t))
    return cur.lastrowid


def dno(c, pid):
    return c.execute('SELECT display_no FROM products WHERE id=?', (pid,)).fetchone()[0]


class DisplayNoTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_sequential_per_category(self):
        c = self.c
        k1, b1, s1, u1, k2 = (add(c, stock.CATEGORY_PROCESS_SERVICE), add(c, stock.CATEGORY_PARTS),
                              add(c, stock.CATEGORY_UNIFORM), add(c, ''), add(c, stock.CATEGORY_PROCESS_SERVICE))
        self.assertEqual([dno(c, x) for x in (k1, b1, s1, u1, k2)],
                         ['K-0001', 'B-0001', 'S-00001', 'S-00002', 'K-0002'])
        self.assertEqual(stock.product_row(c, k2)['displayNo'], 'K-0002')
        self.assertEqual(stock.product_row(c, k2)['id'], k2)

    def test_stable_on_delete_and_new_on_category_change(self):
        c = self.c
        a, b = add(c, stock.CATEGORY_PARTS), add(c, stock.CATEGORY_PARTS)
        c.execute('DELETE FROM products WHERE id=?', (b,))
        cnew = add(c, stock.CATEGORY_PARTS)
        self.assertEqual(dno(c, a), 'B-0001')
        self.assertEqual(dno(c, cnew), 'B-0003')
        c.execute('UPDATE products SET category=? WHERE id=?', (stock.CATEGORY_PROCESS_SERVICE, a))
        self.assertEqual(dno(c, a), 'K-0001')
        c.execute('UPDATE products SET name=?, category=? WHERE id=?', ('x', stock.CATEGORY_PROCESS_SERVICE, a))
        self.assertEqual(dno(c, a), 'K-0001')
        # 制服↔空欄は同じ S なので振り直さない
        s = add(c, '')
        c.execute('UPDATE products SET category=? WHERE id=?', (stock.CATEGORY_UNIFORM, s))
        self.assertEqual(dno(c, s), 'S-00001')

    def test_width_grows(self):
        self.assertEqual(stock.format_display_no('K', 12345), 'K-12345')
        self.assertEqual(stock.format_display_no('S', 123456), 'S-123456')
        self.c.execute("UPDATE product_display_no_seq SET last_no=9999 WHERE prefix='B'")
        self.assertEqual(dno(self.c, add(self.c, stock.CATEGORY_PARTS)), 'B-10000')

    def test_backfill_old_db_by_id(self):
        c = self.c
        ids = [add(c, cat) for cat in ('', stock.CATEGORY_PARTS, stock.CATEGORY_UNIFORM, stock.CATEGORY_PARTS)]
        # 旧DB相当: 番号・カウンタ・トリガー無し
        c.execute('DROP TRIGGER products_display_no_ins')
        c.execute('DROP TRIGGER products_display_no_cat')
        c.execute('DROP TABLE product_display_no_seq')
        c.execute("UPDATE products SET display_no=''")
        stock.migrate_schema(c)
        self.assertEqual([dno(c, x) for x in ids], ['S-00001', 'B-0001', 'S-00002', 'B-0002'])
        stock.migrate_schema(c)  # 冪等
        self.assertEqual([dno(c, x) for x in ids], ['S-00001', 'B-0001', 'S-00002', 'B-0002'])
        self.assertEqual(dno(c, add(c, stock.CATEGORY_PARTS)), 'B-0003')

    def test_legacy_import_and_scope_wipe(self):
        c = self.c
        body = {'importMode': 'products', 'name': '台帳.csv', '_canWrite': True, '_actor': {'name': 't'},
                'file': b64(ledger_csv(['顧客A,仕入先X,N001,パンツ,L', '顧客A,仕入先X,N002,シャツ,M']))}
        preview = pli.build_preview(c, body)
        pli.commit_preview(c, {**body, 'commitToken': 'tok-dno-1',
                               'previewFingerprint': preview['previewFingerprint']}, db_path=None)
        rows = c.execute("SELECT code, display_no FROM products WHERE code IN ('N001','N002') ORDER BY id").fetchall()
        self.assertEqual([r['display_no'] for r in rows], ['S-00001', 'S-00002'])
        self.assertFalse(c.execute("SELECT COUNT(*) FROM products WHERE COALESCE(display_no,'')=''").fetchone()[0])
        k = add(c, stock.CATEGORY_PROCESS_SERVICE)
        wipe.execute_wipe(c, {'_canWrite': True, '_actor': {'id': 1, 'name': 'o', 'role': 'owner'},
                              'scope': 'uniform', 'withStock': True}, db_path=self.path)
        c.commit()
        self.assertEqual(dno(c, k), 'K-0001')
        self.assertEqual(dno(c, add(c, stock.CATEGORY_UNIFORM)), 'S-00003')


class DisplayNoUiTest(unittest.TestCase):
    def test_list_shows_and_searches_display_no(self):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')
        app = open(os.path.join(root, 'app.js'), encoding='utf-8').read()
        self.assertIn('<th>No.</th><th>カテゴリ</th>', app)
        self.assertIn('r.displayNo||String(r.id)', app)
        self.assertIn('const text=`${r.displayNo||\'\'}', app)
        picker = open(os.path.join(root, 'opening_pickers.js'), encoding='utf-8').read()
        self.assertIn('p.displayNo', picker)
        merge = open(os.path.join(root, 'product_merge.js'), encoding='utf-8').read()
        self.assertIn("['No.', esc(p.displayNo", merge)


if __name__ == '__main__':
    unittest.main()
