"""まとめるの色 select が詳細を閉じても保持されること（一時DB・合成データのみ）。"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ledger_supplier_pick_ui import JS, csv_b64_from_text, newdb, pli, tiny_ledger_rows

HARNESS = r'''
const fs=require('fs'),vm=require('vm');
const code=fs.readFileSync(process.argv[2],'utf8');
let els=[];
const doc={getElementById(){return null},querySelector(){return null},
  querySelectorAll(sel){const m=sel.match(/^\[([a-z-]+)\]$/);return m?els.filter(e=>e.attr===m[1]):[]}};
const sandbox={window:{},document:doc,console,setTimeout,clearTimeout,CSS:{escape:s=>String(s)},esc:s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),localStorage:{getItem(){return null},setItem(){},removeItem(){}}};
vm.createContext(sandbox);vm.runInContext(code,sandbox);
const api=sandbox.window.ProductLedgerCsv.__test;
const st=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const g=st.preview.skuGroups.find(x=>x.code===st.code);
api.snapshotAppliedSkuDecisions(st);
const before=api.skuColorPlanField(st,g,api.skuDecision(st,g.code)).status;
const html=api.mergeQuickFormHtml(st,g,api.skuDecision(st,g.code));
const opt=(html.match(/data-pl-official-color[^>]*>.*?<option value="([^"]+)"/)||[])[1];
// 色を選んだ select（DOM）を収穫 → 状態へ
els=[{attr:'data-pl-official-color',value:opt,getAttribute:()=>g.code}];
api.harvestFormEdits(st);
els=[];
const d=api.skuDecision(st,g.code);
const after=api.skuColorPlanField(st,g,d);
const html2=api.mergeQuickFormHtml(st,g,d);
api.snapshotAppliedSkuDecisions(st);
const applied=api.skuColorPlanField(st,g,api.skuDecision(st,g.code)).status;
console.log(JSON.stringify({before,opt,saved:d.color,after:after.status,
  selected:html2.includes(`value="${opt}" selected`),needMark:/data-plan-edit="color">色 <span class="sku-quick-need">/.test(html2),applied}));
'''


class MergeColorSelectTest(unittest.TestCase):
    def test_color_choice_kept_and_pending(self):
        folder, _, c = newdb()
        try:
            rows = ['顧客A,㈱ユニーク,8,ベーシックカラーベスト 046.蛍光グリーン,F',
                    '顧客B,アイトス,8,駐車場ベスト(緑),F']
            dec = {'8': {'decision': 'merge', 'primarySupplier': '㈱ユニーク', 'primarySupplierManual': True}}
            pv = pli.build_preview(c, {'file': csv_b64_from_text(tiny_ledger_rows(rows)), 'name': 'x.csv',
                                       'importMode': 'products', 'skuDecisions': dec,
                                       '_canWrite': True, '_actor': {'name': 't'}})
        finally:
            c.close(); folder.cleanup()
        st = {'preview': pv, 'code': '8', 'skuDecisions': dec, 'rowClassifications': {}, 'rowExclusions': {},
              'selections': {}, 'expandedCodes': {}, 'expandedUnified': {}, 'importMode': 'products',
              'sectionOpen': {}, 'sectionPages': {}}
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / 'h.js'; h.write_text(HARNESS, encoding='utf-8')
            s = Path(d) / 's.json'; s.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
            out = json.loads(subprocess.run(['node', str(h), str(JS), str(s)], capture_output=True, text=True).stdout or '{}')
        self.assertEqual(out['before'], '選択が必要')
        self.assertTrue(out['opt'])
        self.assertEqual(out['saved'], out['opt'])
        self.assertEqual(out['after'], '未反映')
        self.assertTrue(out['selected'])
        self.assertFalse(out['needMark'])
        self.assertEqual(out['applied'], '手動確定済み')

    def test_expand_toggles_harvest_before_repaint(self):
        js = JS.read_text(encoding='utf-8')
        for attr in ('data-pl-sku-expand', 'data-pl-unified-expand', 'data-pl-confirmed-expand'):
            block = js.split(f"querySelectorAll('[{attr}]')", 1)[1][:400]
            self.assertLess(block.index('harvestFormEdits(st)'), block.index('paintDialog()'), attr)
        self.assertRegex(js, re.escape("[data-pl-official-color]').forEach(el=>{\n      el.onchange"))


if __name__ == '__main__':
    unittest.main()
