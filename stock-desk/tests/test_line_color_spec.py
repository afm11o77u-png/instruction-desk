"""'○○ライン' は衣類の色ではなく取扱の仕様。加工サービスは「ライン加工」1つに自動でつなぐ"""
import sys, unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stock
import product_ledger_import as pli
import product_ledger_paren as paren
from test_ledger_color_split_register import newdb, b64, ledger_csv


def _svc(c, name):
    t = stock.stamp()
    c.execute("INSERT INTO products(code,name,color,category,active,created_at,updated_at) VALUES('',?,'',?,1,?,?)",
              (name, stock.CATEGORY_PROCESS_SERVICE, t, t))


class ParseTest(unittest.TestCase):
    def test_split(self):
        for src, color in [('帽子(青) 赤ライン', 'ブルー'), ('帽子 赤ライン', ''), ('帽子(赤ライン)', '')]:
            d = paren.split_name_and_color(src)
            self.assertEqual(d['plannedName'], '帽子', src)
            self.assertEqual(d['color'], color, src)
            self.assertEqual(d['processing'], '赤ライン', src)

    def test_rhinestone_untouched(self):
        self.assertEqual(paren.extract_line_specs('ラインストーン')[1], [])


class ImportTest(unittest.TestCase):
    def run_import(self, rows, services=('ライン加工',), dec=None):
        f, path, c = newdb()
        for s in services:
            _svc(c, s)
        src = {'importMode': 'products', 'name': 'a.csv', 'file': b64(ledger_csv(rows)),
               '_canWrite': True, '_actor': {'name': 't'}}
        if dec:
            src['skuDecisions'] = dec
        p = pli.build_preview(c, src)
        return c, path, src, p

    def commit(self, c, path, src, p):
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(c, {**src, 'commitToken': 'tok-line-1', 'previewFingerprint': p['previewFingerprint']}, db_path=path)

    def test_links_line_service(self):
        c, path, src, p = self.run_import(['顧客A,仕入X,H4,帽子(青) 赤ライン,F'])
        r = p['rows'][0]
        self.assertEqual(r['status'], 'create')
        self.assertEqual(r['lineSpec'], '赤ライン')
        self.assertFalse(r.get('processLinkMissing'))
        self.commit(c, path, src, p)
        prod = c.execute("SELECT name,color FROM products WHERE code='H4'").fetchone()
        self.assertEqual(tuple(prod), ('帽子', 'ブルー'))
        cp = c.execute("SELECT cp.embroidery,q.name FROM customer_products cp JOIN customer_product_processes x ON x.customer_product_id=cp.id JOIN products q ON q.id=x.process_product_id").fetchall()
        self.assertEqual([tuple(x) for x in cp], [('赤ライン', 'ライン加工')])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM products WHERE name LIKE '%赤ライン%'").fetchone()[0], 0)

    def test_missing_service_note(self):
        c, path, src, p = self.run_import(['顧客A,仕入X,H4,帽子(青) 赤ライン,F'], services=())
        r = p['rows'][0]
        self.assertEqual(r['status'], 'needs_review')
        self.assertIn('加工「ライン加工」に対応する加工サービスがありません', r['reasons'])

    def test_variants_merge_into_one_product(self):
        c, path, src, p = self.run_import(['顧客A,仕入X,H1,帽子(青) 赤ライン,F', '顧客A,仕入X,H1,帽子(青) 青ライン,F'],
                                          dec={'H1': {'decision': 'merge'}})
        self.commit(c, path, src, p)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM products WHERE code='H1'").fetchone()[0], 1)
        self.assertEqual(sorted(x[0] for x in c.execute("SELECT embroidery FROM customer_products")), ['赤ライン', '青ライン'])

    def test_auto_merge_line_variants(self):
        c, path, src, p = self.run_import(['顧客A,仕入X,H1,帽子(青) 赤ライン,F', '顧客A,仕入X,H1,帽子(青) 青ライン,F',
                                           '顧客A,仕入X,H1,帽子(青),F'])
        self.assertEqual(p['autoLineSpecCodes'], ['H1'])
        self.assertFalse(p['skuDecisionPending'])
        self.assertTrue(all(r['status'] == 'create' for r in p['rows']))
        self.commit(c, path, src, p)
        self.assertEqual([tuple(x) for x in c.execute("SELECT name,color FROM products WHERE code='H1'")], [('帽子', 'ブルー')])
        self.assertEqual(sorted(x[0] for x in c.execute("SELECT embroidery FROM customer_products")), ['', '赤ライン', '青ライン'])

    def test_color_differs_keeps_color_split(self):
        rows = ['顧客A,仕入X,H2,帽子(青) 赤ライン,F', '顧客A,仕入X,H2,帽子(白) 赤ライン,F']
        c, path, src, p = self.run_import(rows)
        self.assertEqual(p['autoLineSpecCodes'], [])
        c, path, src, p = self.run_import(rows, dec={'H2': {'decision': 'color_split'}})
        self.commit(c, path, src, p)
        got = sorted(tuple(x) for x in c.execute("SELECT p.color,cp.embroidery FROM customer_products cp JOIN products p ON p.id=cp.product_id"))
        self.assertEqual(got, [('ブルー', '赤ライン'), ('ホワイト', '赤ライン')])

    def test_user_decision_wins(self):
        c, path, src, p = self.run_import(['顧客A,仕入X,H1,帽子(青) 赤ライン,F', '顧客A,仕入X,H1,帽子(青) 青ライン,F'],
                                          dec={'H1': {'decision': 'hold'}})
        self.assertEqual(p['autoLineSpecCodes'], [])


if __name__ == '__main__':
    unittest.main()
