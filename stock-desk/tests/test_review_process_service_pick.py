"""確認リストの加工は登録済み加工サービスから選ぶ（テストDBのみ）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import stock  # noqa: E402
import product_ledger_import as pli  # noqa: E402
from test_ledger_color_split_register import newdb, b64, ledger_csv  # noqa: E402
from test_processing_service_no_code import commit  # noqa: E402
from test_handle_process_links import links  # noqa: E402


def add_service(c, name):
    t = stock.stamp()
    return c.execute("INSERT INTO products(code,name,color,category,active,created_at,updated_at) "
                     "VALUES('',?,'',?,1,?,?)", (name, stock.CATEGORY_PROCESS_SERVICE, t, t)).lastrowid


class ReviewProcessServicePickTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()
        self.s1 = add_service(self.c, '刺繍加工')
        self.s2 = add_service(self.c, 'ライン加工')

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_preview_lists_registered_services(self):
        p = pli.build_preview(self.c, {'importMode': 'products', 'name': 'a.csv',
                                       'file': b64(ledger_csv(['顧客A,国立街,H1,帽子(青),F'])),
                                       '_canWrite': True, '_actor': {'name': 't'}})
        names = [s['name'] for s in p['processServices']]
        self.assertEqual(sorted(names), ['ライン加工', '刺繍加工'])

    def test_chosen_services_are_linked(self):
        p = commit(self.c, self.path, ['顧客A,国立街,H1,ポロシャツ(刺繍),L'], overrides={
            '2': {'processServiceIds': [self.s2, self.s1, 99999], 'processing': 'ライン加工・刺繍加工'},
        })
        row = p['rows'][0]
        self.assertFalse(row.get('processLinkMissing'))
        self.assertEqual(row.get('chosenProcessServiceIds'), [self.s2, self.s1])
        cp = self.c.execute(
            "SELECT cp.id FROM customer_products cp JOIN products p ON p.id=cp.product_id WHERE p.code='H1'"
        ).fetchone()
        self.assertEqual(links(self.c, cp['id']), ['ライン加工', '刺繍加工'])

    def test_empty_choice_links_nothing(self):
        commit(self.c, self.path, ['顧客A,国立街,H2,ポロシャツ(刺繍),L'], overrides={
            '2': {'processServiceIds': [], 'processing': ''},
        })
        cp = self.c.execute(
            "SELECT cp.id FROM customer_products cp JOIN products p ON p.id=cp.product_id WHERE p.code='H2'"
        ).fetchone()
        self.assertEqual(links(self.c, cp['id']), [])


if __name__ == '__main__':
    unittest.main()
