"""品番 fabric 等の生地行は部品・資材（商品段階に出さない）。fixtures / temp DB のみ。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import product_ledger_import as pli
import stock
from test_ledger_color_split_register import b64, ledger_csv, newdb

ROWS = [
    '顧客A,株サーヴォ,fabric,DCP515用生地(袖/裾加工用),—',
    '顧客B,株シーズン大阪,fabric,DBP1970生地,—',
    '顧客B,株シーズン大阪,fabric,NPC5980ポケット生地,—',
    '顧客B,株シーズン大阪,DBP1970,ブルゾン(生地厚手),L',
]


class FabricAsPartsTest(unittest.TestCase):
    def test_detect(self):
        self.assertTrue(pli.is_material_row('fabric', 'なんでも'))
        self.assertTrue(pli.is_material_row('ABC1', 'NPC5980ポケット生地'))
        self.assertTrue(pli.is_material_row('X', 'DCP515用生地（袖/裾加工用）'))
        self.assertFalse(pli.is_material_row('DBP1970', 'ブルゾン(生地厚手)'))
        self.assertFalse(pli.is_material_row('S1', '綿生地シャツ'))

    def test_phases_and_commit(self):
        folder, path, c = newdb()
        try:
            for i, ph in enumerate(['goods', 'parts']):
                b = {'importMode': 'products', 'productPhase': ph, 'name': 'n.csv',
                     'file': b64(ledger_csv(ROWS)), '_canWrite': True, '_actor': {'name': 't'}}
                p = pli.build_preview(c, b)
                cats = {(r.get('product') or {}).get('category') for r in p['rows']}
                if ph == 'goods':
                    self.assertEqual(len(p['rows']), 1)
                    self.assertNotIn('fabric', [g['code'] for g in p['skuGroups']])
                else:
                    self.assertEqual(len(p['rows']), 3)
                    self.assertEqual(cats, {stock.CATEGORY_PARTS})
                with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                    pli.commit_preview(c, {**b, 'commitToken': f'tok-fb-{i}',
                                           'previewFingerprint': p['previewFingerprint']}, db_path=path)
            parts = c.execute('SELECT id,name,code FROM products WHERE category=?',
                              (stock.CATEGORY_PARTS,)).fetchall()
            self.assertEqual(len(parts), 3)  # 名前ごとに別部品
            for r in parts:
                self.assertEqual(r['code'], '')
                sizes = [x[0] for x in c.execute(
                    'SELECT label FROM product_sizes WHERE product_id=?', (r['id'],))]
                self.assertEqual(sizes, ['F'])
                self.assertEqual(c.execute(
                    'SELECT COUNT(*) FROM customer_products WHERE product_id=?', (r['id'],)).fetchone()[0], 0)
            g = c.execute("SELECT category FROM products WHERE code='DBP1970'").fetchone()
            self.assertNotEqual(g['category'], stock.CATEGORY_PARTS)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
