"""加工サービス名の表記ゆれ（語末の長音）と照合用別名（テストDBのみ）。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as ps  # noqa: E402
import stock  # noqa: E402
from test_ledger_color_split_register import b64, ledger_csv, newdb  # noqa: E402

key = ps.normalize_service_key


class KeyTest(unittest.TestCase):
    def test_word_end_long_vowel_same(self):
        self.assertEqual(key('スナッパ加工'), key('スナッパー加工'))
        self.assertEqual(ps.unify_processing_service_name('スナッパ加工'), 'スナッパー加工')
        self.assertEqual(ps.unify_processing_service_name('スナッパ'), 'スナッパー加工')

    def test_not_over_merged(self):
        for a, b in [('スーパー加工', 'スパ加工'), ('ノー加工', 'ノ加工'), ('ネーム刺繍', 'ネム刺繍'),
                     ('ライン加工', 'スナッパー加工'), ('ポケット加工', 'ポケット付け')]:
            self.assertNotEqual(key(a), key(b), (a, b))

    def test_prefer_long_vowel(self):
        self.assertEqual(ps.prefer_service_display_name('スナッパ加工', 'スナッパー加工'), 'スナッパー加工')
        self.assertEqual(ps.prefer_service_display_name('スナッパー加工', 'スナッパ加工'), 'スナッパー加工')
        self.assertEqual(ps.prefer_service_display_name('ライン加工', 'スナッパー加工'), 'ライン加工')


def svc(c, name):
    return stock.save_product(c, {'code': '', 'name': name, 'color': '', 'material': '',
                                  'category': stock.CATEGORY_PROCESS_SERVICE, 'supplierId': None,
                                  'sizes': ['—'], 'links': [], 'active': True})


class DbTest(unittest.TestCase):
    def setUp(self):
        self.folder, self.path, self.c = newdb()

    def tearDown(self):
        self.c.close()
        self.folder.cleanup()

    def test_import_reuses_existing_variant(self):
        sid = svc(self.c, 'スナッパ加工')['id']
        rows = ['顧客A,仕入先X,ABC-1,ポロシャツ（スナッパ加工）,L',
                '顧客B,仕入先X,ABC-2,ポロシャツ（スナッパー加工）,L']
        b = {'importMode': 'products', 'name': 's.csv', 'file': b64(ledger_csv(rows)),
             '_canWrite': True, '_actor': {'name': 't'}}
        p = pli.build_preview(self.c, b)
        names = {r.get('companionProcessingService') for r in p['rows']}
        self.assertEqual(names, {'スナッパー加工'})
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            pli.commit_preview(self.c, {**b, 'commitToken': 'tok-sn-1',
                                        'previewFingerprint': p['previewFingerprint']}, db_path=self.path)
        n = self.c.execute('SELECT COUNT(*) FROM products WHERE category=?',
                           (stock.CATEGORY_PROCESS_SERVICE,)).fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(ps.find_existing_processing_service_any_vendor(self.c, 'スナッパー加工')['id'], sid)

    def test_user_alias_resolves(self):
        sid = svc(self.c, 'ボタンホール加工')['id']
        self.assertIsNone(ps.find_existing_processing_service_any_vendor(self.c, '穴かがり'))
        cur = dict(self.c.execute('SELECT * FROM products WHERE id=?', (sid,)).fetchone())
        stock.save_product(self.c, {'id': sid, 'code': '', 'name': cur['name'], 'color': '', 'material': '',
                                    'category': stock.CATEGORY_PROCESS_SERVICE, 'supplierId': None,
                                    'sizes': ['—'], 'links': [], 'active': True,
                                    'addNameAliases': ['穴かがり']})
        self.assertEqual(ps.find_existing_processing_service_any_vendor(self.c, '穴かがり')['id'], sid)

    def test_repair_dry_run_lists_variants(self):
        a = svc(self.c, 'スナッパ加工')['id']
        svc(self.c, 'スナッパー加工')
        inv = ps.inventory_duplicate_process_services(self.c)
        self.assertEqual(inv['count'], 1)
        g = inv['groups'][0]
        self.assertEqual(g['suggestedName'], 'スナッパー加工')
        before = self.c.execute('SELECT COUNT(*) FROM products').fetchone()[0]
        out = ps.apply_duplicate_process_service_merge(self.c, dry_run=True)
        self.assertTrue(out['dryRun'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM products').fetchone()[0], before)
        with mock.patch.object(stock, 'backup_sqlite', return_value=''):
            ps.apply_duplicate_process_service_merge(self.c, dry_run=False, db_path=self.path)
        self.assertEqual(self.c.execute('SELECT name FROM products WHERE id=?', (a,)).fetchone()[0], 'スナッパー加工')
        self.assertEqual(ps.find_existing_processing_service_any_vendor(self.c, 'スナッパ加工')['id'], a)


if __name__ == '__main__':
    unittest.main()
