"""内容別登録後に sku_row が残る不具合（fixtures / temp DB のみ）。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ledger_content_split_register import JS, NODE_HARNESS, csv_b64, ledger, newdb
import product_ledger_import as pli

FABRIC = ledger(
    '顧客A,㈱サーヴォ,fabric,DCP515用生地,—',
    '顧客B,㈱シーズン大阪,fabric,DBP1970生地,—',
    '顧客B,㈱シーズン大阪,fabric,NPC5980ポケット生地,—',
)
HAT = ledger(
    '顧客C,仕入Y,SD5000AM-5U,帽子 グリーン,—',
    '顧客C,仕入Y,SD5000AM-5U,帽子 ブルー,—',
    '顧客D,仕入Y,SD5000AM-5U,帽子 グリーン,—',
)


class ContentSplitInternalCodeTest(unittest.TestCase):
    def _rematch(self, text, code):
        folder, path, c = newdb()
        try:
            return pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'x.csv',
                'skuDecisions': {code: {'decision': 'content_split'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
        finally:
            c.close()
            folder.cleanup()

    def _assert_resolved(self, pv, code):
        g = next(g for g in pv['skuGroups'] if g['code'] == code)
        plans = (g.get('contentSplitPlans') or {}).get('plans') or []
        self.assertTrue(plans)
        for p in plans:
            self.assertTrue(p.get('internalCode'))
            self.assertFalse(p.get('needsInternalCode'))
        rows = [r for r in pv['rows'] if (r.get('csv') or {}).get('code') == code]
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue(r.get('canCommit'), r.get('reasons'))
            self.assertFalse(any('社内管理品番' in x for x in (r.get('reasons') or [])))

    def test_fabric_rows_are_parts_not_sku_group(self):
        # 品番 fabric は生地の総称 → 品番なしの部品・資材（品番グループにしない）
        pv = self._rematch(FABRIC, 'fabric')
        self.assertNotIn('fabric', [g['code'] for g in pv['skuGroups']])

    def test_hat_colors_no_collision_needs_no_confirm(self):
        self._assert_resolved(self._rematch(HAT, 'SD5000AM-5U'), 'SD5000AM-5U')


HARNESS_TAIL = r'''
const st = {skuDecisions: {}, preview: {skuGroups: []}};
const g = {code: 'fabric', supplierCandidates: ['㈱サーヴォ', '㈱シーズン大阪'],
  decision: {decision: 'content_split'}, auxPrimary: {}, needsDecision: true};
st.preview.skuGroups = [g];
const item = {kind: 'sku_row', code: 'fabric', group: g, issues: [],
  rows: [{rowNo: 2, csv: {code: 'fabric', productName: 'DCP515用生地', supplier: '㈱サーヴォ'}}]};
const f = api.buildPlannedStatusFields(st, item).find(x => x.key === 'supplier' || x.field === 'supplier');
console.log(JSON.stringify({status: f && f.status, need: f && f.status === api.PLAN_STATUS.NEED_CHOICE}));
'''


class ContentSplitSupplierFieldTest(unittest.TestCase):
    def test_sku_row_supplier_not_need_choice(self):
        head = NODE_HARNESS.split('function baseSt()')[0]
        with tempfile.TemporaryDirectory() as td:
            h = Path(td) / 'h.js'
            cp = Path(td) / 'c.json'
            cp.write_text('{}', encoding='utf-8')
            h.write_text(head + HARNESS_TAIL, encoding='utf-8')
            out = subprocess.run(['node', str(h), str(JS), str(cp)],
                                 capture_output=True, text=True, check=True)
        res = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertTrue(res['status'])
        self.assertFalse(res['need'])


if __name__ == '__main__':
    unittest.main()
