"""旧CSV取り込み確認リストの5件修正（一時DB・合成データのみ）。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ledger_supplier_pick_ui import JS, csv_b64_from_text, newdb, pli, tiny_ledger_rows

HARNESS = r'''
const fs=require('fs'),vm=require('vm');
const code=fs.readFileSync(process.argv[2],'utf8');
const sandbox={window:{},document:{getElementById(){return null},querySelector(){return null},querySelectorAll(){return []}},console,setTimeout,clearTimeout,CSS:{escape:s=>String(s)},localStorage:{getItem(){return null},setItem(){},removeItem(){}}};
vm.createContext(sandbox);vm.runInContext(code,sandbox);
const api=sandbox.window.ProductLedgerCsv.__test;
const st=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const items=api.buildUnifiedReviewItems(st);
console.log(JSON.stringify(items.map(i=>({kind:i.kind,code:i.code,
  issues:(i.issues||[]).filter(x=>!x.resolved).map(x=>x.type+':'+(x.fieldKey||'')),
  fields:Object.fromEntries(api.buildPlannedStatusFields(st,i).map(f=>[f.key,f.status]))}))));
'''

BF = ['顧客A,仕入X,BF3100,ズキン ツバ無 白（ゴールド）,M',
      '顧客A,仕入X,BF3100,ズキン ツバ無 白（ホワイト）,L']


class ReviewFiveFixesTest(unittest.TestCase):
    def run_case(self, rows, applied=None, extra=None):
        folder, _, c = newdb()
        try:
            pv = pli.build_preview(c, {
                'file': csv_b64_from_text(tiny_ledger_rows(rows)), 'name': 'x.csv',
                'importMode': 'products', 'skuDecisions': applied or {},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            st = {'preview': pv, 'skuDecisions': json.loads(json.dumps(applied or {})),
                  'appliedSkuDecisions': json.loads(json.dumps(applied or {})),
                  'rowClassifications': {}, 'rowExclusions': {}, 'selections': {},
                  'expandedCodes': {}, 'expandedUnified': {}, 'importMode': 'products',
                  'sectionOpen': {}, 'sectionPages': {}}
            st.update(extra or {})
            with tempfile.TemporaryDirectory() as d:
                h = Path(d) / 'h.js'; h.write_text(HARNESS, encoding='utf-8')
                s = Path(d) / 's.json'; s.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
                out = subprocess.run(['node', str(h), str(JS), str(s)], capture_output=True, text=True, check=True)
            return pv, json.loads(out.stdout)
        finally:
            c.close(); folder.cleanup()

    def test_1_multi_color_same_name_needs_merge_or_split(self):
        pv, items = self.run_case(BF)
        g = next(x for x in pv['skuGroups'] if x['code'] == 'BF3100')
        self.assertTrue(g['needsDecision'])
        self.assertEqual([i['kind'] for i in items], ['sku'])
        pv, items = self.run_case(BF, {'BF3100': {'decision': 'merge', 'color': 'ホワイト'}})
        self.assertEqual(items, [])
        self.assertTrue(all(r['status'] != 'needs_review' for r in pv['rows']))

    def test_2_setting_color_resolves_paren_color_issue(self):
        rows = ['顧客A,仕入X,SD551S,一体型ズキン ゴールド（ブルー）,M']
        _, items = self.run_case(rows)
        self.assertEqual(items[0]['fields']['name'], '自動決定済み')
        self.assertIn('paren_confirm:color', items[0]['issues'])
        _, items = self.run_case(rows, extra={'rowClassifications': {'2': {'color': 'ブルー'}}})
        self.assertEqual(items[0]['fields']['color'], '手動確定済み')
        self.assertNotIn('paren_confirm:color', items[0]['issues'])

    def test_3_confirm_name_without_editing(self):
        rows = ['顧客A,仕入X,SD551S,一体型ズキン ゴールド（ブルー）,M']
        _, items = self.run_case(rows, extra={'rowClassifications': {'2': {'confirmed': True}}})
        for it in items:
            self.assertNotEqual(it['fields'].get('name'), '選択が必要')

    def test_4_5_pending_supplier_stays_in_list_marked_pending(self):
        rows = [BF[0], '顧客A,仕入Y,BF3100,ズキン ツバ無 白（ホワイト）,L']
        local = {'BF3100': {'decision': 'merge', 'color': 'ホワイト',
                            'primarySupplier': '仕入Y', 'primarySupplierManual': True}}
        _, items = self.run_case(rows, extra={'skuDecisions': local})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['fields']['supplier'], '未反映')
        self.assertIn('repreview:', items[0]['issues'])
        _, items = self.run_case(rows, local)
        self.assertEqual(items, [])


if __name__ == '__main__':
    unittest.main()
