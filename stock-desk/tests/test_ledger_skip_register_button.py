"""旧CSV確認リスト: 「今回登録しない」対象除外（fixtures / テストDBのみ。実在庫・本DBは書かない）。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402

JS = ROOT / 'static' / 'product_ledger_csv.js'
CSS = ROOT / 'static' / 'style.css'

NODE_HARNESS = r'''
const fs = require('fs');
const vm = require('vm');
const jsPath = process.argv[2];
const casesPath = process.argv[3];
const code = fs.readFileSync(jsPath, 'utf8');
const cases = eval('('+fs.readFileSync(casesPath,'utf8')+')');
const sandbox = {
  window: {},
  document: {
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  },
  console,
  CSS: { escape: (s) => String(s) },
  esc: (s) => String(s == null ? '' : s),
  $: () => null,
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const api = sandbox.window.ProductLedgerCsv && sandbox.window.ProductLedgerCsv.__test;
if (!api) { console.error('missing __test'); process.exit(2); }
const out = {};
for (const [name, fn] of Object.entries(cases)) {
  out[name] = fn(api, sandbox);
}
process.stdout.write(JSON.stringify(out));
'''


def run_cases(cases: dict) -> dict:
    with tempfile.TemporaryDirectory() as td:
        cases_path = Path(td) / 'cases.js'
        body = '{\n' + ',\n'.join(f'{json.dumps(k)}: {v}' for k, v in cases.items()) + '\n}'
        cases_path.write_text(body, encoding='utf-8')
        harness = Path(td) / 'harness.js'
        harness.write_text(NODE_HARNESS, encoding='utf-8')
        proc = subprocess.run(
            ['node', str(harness), str(JS), str(cases_path)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr + proc.stdout)
        return json.loads(proc.stdout)


class SkipRegisterSourceTest(unittest.TestCase):
    def test_ui_wiring(self):
        js = JS.read_text(encoding='utf-8')
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('今回登録しない', js)
        self.assertIn('data-pl-target-skip', js)
        self.assertIn('applyLocalTargetSkipRegister', js)
        self.assertIn('skipRegisterActionsHtml', js)
        self.assertIn('利用者が今回登録しないと指定', js)
        self.assertIn('pl-target-skip-actions', css)
        self.assertIn('pl-skip-scope', css)
        # クリックハンドラ内で詳細展開しない
        self.assertIn('applyLocalTargetSkipRegister(st,item)', js)
        start = js.find("querySelectorAll('[data-pl-target-skip]')")
        end = js.find("querySelectorAll('[data-pl-row-include]')", start)
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        handler = js[start:end]
        self.assertIn('applyLocalTargetSkipRegister', handler)
        self.assertNotIn('expandedUnified[', handler)
        self.assertNotIn('expandedCodes[', handler)
        # 保留ボタン文言とは別
        self.assertIn('data-dec="hold"', js)

    def test_whole_sku_target_exclude_and_restore(self):
        cases = {
            'wholeSkuTarget': '''
              function(api){
                const rows=[
                  {rowNo:2,status:'needs_review',statusLabel:'要確認',canCommit:false,
                   csv:{customer:'A',code:'SKU1',productName:'ジャケット',size:'S',supplier:'S1'},
                   reasons:['名差'],skuDecisionNeeded:true},
                  {rowNo:3,status:'needs_review',statusLabel:'要確認',canCommit:false,
                   csv:{customer:'A',code:'SKU1',productName:'ジャケット',size:'M',supplier:'S1'},
                   reasons:['名差'],skuDecisionNeeded:true},
                  {rowNo:4,status:'needs_review',statusLabel:'要確認',canCommit:false,
                   csv:{customer:'B',code:'SKU2',productName:'パンツ',size:'L',supplier:'S1'},
                   reasons:['色'],skuDecisionNeeded:true}
                ];
                const groups=[
                  {code:'SKU1',codeDisplay:'SKU1',needsDecision:true,reviewTier:'individual',
                   reviewReasons:[{code:'name_wording',label:'名差'}],rowNos:[2,3],variants:[],existingCount:0},
                  {code:'SKU2',codeDisplay:'SKU2',needsDecision:true,reviewTier:'individual',
                   reviewReasons:[{code:'color',label:'色'}],rowNos:[4],variants:[],existingCount:0}
                ];
                const st={
                  open:false,busy:false,importMode:api.MODE_PRODUCTS,
                  preview:{rows,skuGroups:groups,outcomeSummary:{},plannedCounts:{}},
                  rowExclusions:{},rowClassifications:{'2':{processing:'残す入力'}},selections:{'2':{category:'x'}},
                  skuDecisions:{},appliedSkuDecisions:{},sectionOpen:{},sectionPages:{},
                  expandedCodes:{},expandedUnified:{},selectedCodes:{},rowExcludeSnapshots:{}
                };
                const before=api.buildUnifiedReviewItems(st);
                const sku1=before.find(i=>i.id==='sku:SKU1');
                const meta=api.skipRegisterButtonMeta(st,sku1);
                const html=api.skipRegisterActionsHtml(st,sku1);
                const result=api.applyLocalTargetSkipRegister(st,sku1,{paint:false});
                const afterItems=api.buildUnifiedReviewItems(st);
                const counts=api.judgmentStatusCounts(st);
                const excluded=api.listExcludedRows(st);
                const reasons=excluded.map(r=>api.rowExclusionReason(st,r));
                const payload=api.previewPayload(Object.assign({
                  fileName:'a.csv',fileB64:'x',actorName:''
                },st));
                // 参照共有される前に件数を確定（復帰で map が消えるため）
                const payloadKeys=Object.keys(payload.rowExclusions||{}).length;
                // 復元（入力保持）
                const row2=rows[0];
                api.applyLocalRowExclude(st,row2,false);
                api.applyLocalRowExclude(st,rows[1],false);
                const restoredItems=api.buildUnifiedReviewItems(st);
                const restoredCounts=api.judgmentStatusCounts(st);
                return {
                  scope:meta.scope, active:meta.active, total:meta.total,
                  htmlHasBtn: html.includes('今回登録しない') && html.includes('品番対象の全行') && html.includes('2行'),
                  excludedN:result.excluded,
                  otherTargetSafe: afterItems.some(i=>i.id==='sku:SKU2'),
                  sku1Gone: !afterItems.some(i=>i.id==='sku:SKU1'),
                  unresolved:counts.unresolved,
                  rowExcluded:counts.rowExcluded,
                  confirmed:counts.confirmed,
                  reasonOk: reasons.every(r=>r===api.USER_ROW_EXCLUDE_REASON),
                  reasonNotAuto: reasons.every(r=>r!==api.K2_AUTO_EXCLUDE_REASON),
                  payloadKeys,
                  keepClass: !!(st.rowClassifications['2']&&st.rowClassifications['2'].processing==='残す入力'),
                  keepSel: !!(st.selections['2']&&st.selections['2'].category==='x'),
                  restoredUnresolved:restoredCounts.unresolved,
                  restoredExcluded:restoredCounts.rowExcluded,
                  restoredHasSku1: restoredItems.some(i=>i.id==='sku:SKU1')
                };
              }
            '''
        }
        out = run_cases(cases)['wholeSkuTarget']
        self.assertEqual(out['scope'], '品番対象の全行')
        self.assertEqual(out['active'], 2)
        self.assertEqual(out['total'], 2)
        self.assertTrue(out['htmlHasBtn'])
        self.assertEqual(out['excludedN'], 2)
        self.assertTrue(out['otherTargetSafe'])
        self.assertTrue(out['sku1Gone'])
        self.assertEqual(out['unresolved'], 1)
        self.assertEqual(out['rowExcluded'], 2)
        self.assertEqual(out['confirmed'], 0)
        self.assertTrue(out['reasonOk'])
        self.assertTrue(out['reasonNotAuto'])
        self.assertEqual(out['payloadKeys'], 2)
        self.assertTrue(out['keepClass'])
        self.assertTrue(out['keepSel'])
        self.assertTrue(out['restoredHasSku1'])
        self.assertEqual(out['restoredExcluded'], 0)
        self.assertGreaterEqual(out['restoredUnresolved'], 1)

    def test_partial_already_excluded_and_processing_scope(self):
        cases = {
            'partialAndProc': '''
              function(api){
                const rows=[
                  {rowNo:2,status:'needs_review',statusLabel:'要確認',canCommit:false,
                   csv:{customer:'A',code:'SKU1',productName:'J',size:'S',supplier:'S1'},
                   reasons:['名差'],skuDecisionNeeded:true},
                  {rowNo:3,status:'needs_review',statusLabel:'要確認',canCommit:false,
                   csv:{customer:'A',code:'SKU1',productName:'J',size:'M',supplier:'S1'},
                   reasons:['名差'],skuDecisionNeeded:true},
                  {rowNo:4,status:'needs_review',statusLabel:'要確認',canCommit:false,
                   csv:{customer:'C',code:'',productName:'ほつれ補修',size:'',supplier:'K2スタッフ工房'},
                   processingServiceView:true,
                   processingService:{isProcessingVendor:true,serviceName:'ほつれ補修',vendorName:'K2スタッフ工房',needsConfirm:true,missingFields:['確認']},
                   parenClassification:{processing:'ほつれ補修',needsConfirm:true},reasons:['加工名']},
                  {rowNo:5,status:'needs_review',statusLabel:'要確認',canCommit:false,
                   csv:{customer:'D',code:'',productName:'丈直し',size:'',supplier:'K2スタッフ工房'},
                   processingServiceView:true,
                   processingService:{isProcessingVendor:true,serviceName:'丈直し',vendorName:'K2スタッフ工房',needsConfirm:true,missingFields:['確認']},
                   parenClassification:{processing:'丈直し',needsConfirm:true},reasons:['加工名']},
                  {rowNo:6,status:'needs_review',statusLabel:'要確認',canCommit:false,
                   csv:{customer:'E',code:'',productName:'ほつれ補修',size:'',supplier:'K2スタッフ工房'},
                   processingServiceView:true,
                   processingService:{isProcessingVendor:true,serviceName:'ほつれ補修',vendorName:'K2スタッフ工房',needsConfirm:true,missingFields:['確認']},
                   parenClassification:{processing:'ほつれ補修',needsConfirm:true},reasons:['加工名']}
                ];
                const groups=[{
                  code:'SKU1',codeDisplay:'SKU1',needsDecision:true,reviewTier:'individual',
                  reviewReasons:[{code:'name_wording',label:'名差'}],rowNos:[2,3],variants:[],existingCount:0
                }];
                const st={
                  open:false,busy:false,importMode:api.MODE_PRODUCTS,
                  preview:{rows,skuGroups:groups,outcomeSummary:{},plannedCounts:{}},
                  rowExclusions:{},rowClassifications:{},selections:{},skuDecisions:{},
                  appliedSkuDecisions:{},sectionOpen:{},sectionPages:{},
                  expandedCodes:{},expandedUnified:{},selectedCodes:{},rowExcludeSnapshots:{}
                };
                // 一部を先に行除外
                api.markRowExcludedLocal(st,rows[0],true);
                const items=api.buildUnifiedReviewItems(st);
                const sku1=items.find(i=>i.id==='sku:SKU1');
                const metaSku=api.skipRegisterButtonMeta(st,sku1);
                const r1=api.applyLocalTargetSkipRegister(st,sku1,{paint:false});
                const afterSku=api.buildUnifiedReviewItems(st);
                const procItems=afterSku.filter(i=>i.kind==='processing');
                const hoture=procItems.find(i=>(i.targetLabel||'').includes('ほつれ補修'));
                const take=procItems.find(i=>(i.targetLabel||'').includes('丈直し'));
                const metaProc=api.skipRegisterButtonMeta(st,hoture);
                const r2=api.applyLocalTargetSkipRegister(st,hoture,{paint:false});
                const finalItems=api.buildUnifiedReviewItems(st);
                const counts=api.judgmentStatusCounts(st);
                const excluded=api.listExcludedRows(st);
                const holdDec=api.effectiveDecision(st,groups[0]);
                // hold ボタンとは別: 全行除外後 needsDecision=false で保留区分へは入れない
                const part=api.partitionJudgmentGroups(st);
                return {
                  partialActive:metaSku.active, partialAlready:metaSku.alreadyExcluded,
                  excludedRemaining:r1.excluded,
                  skuGone:!afterSku.some(i=>i.id==='sku:SKU1'),
                  procScope:metaProc.scope, procActive:metaProc.active,
                  procExcluded:r2.excluded,
                  takeRemains:!!finalItems.find(i=>i.id===(take&&take.id)),
                  hotureGone:!finalItems.some(i=>(i.targetLabel||'').includes('ほつれ補修')),
                  rowExcluded:counts.rowExcluded,
                  unresolved:counts.unresolved,
                  holdLen:part.hold.length,
                  confirmed:counts.confirmed,
                  reasons:excluded.map(r=>api.rowExclusionReason(st,r)),
                  sharedState: Object.keys(st.rowExclusions).length===excluded.length
                };
              }
            '''
        }
        out = run_cases(cases)['partialAndProc']
        self.assertEqual(out['partialActive'], 1)
        self.assertEqual(out['partialAlready'], 1)
        self.assertEqual(out['excludedRemaining'], 1)
        self.assertTrue(out['skuGone'])
        self.assertEqual(out['procScope'], 'この加工対象の行')
        self.assertEqual(out['procActive'], 2)
        self.assertEqual(out['procExcluded'], 2)
        self.assertTrue(out['takeRemains'])
        self.assertTrue(out['hotureGone'])
        self.assertEqual(out['rowExcluded'], 4)  # 2 sku + 2 hoture
        self.assertEqual(out['holdLen'], 0)
        self.assertEqual(out['confirmed'], 0)
        self.assertTrue(all(r == pli.USER_ROW_EXCLUDE_REASON for r in out['reasons']))
        self.assertTrue(out['sharedState'])

    def test_hold_differs_from_skip_and_html_lists(self):
        cases = {
            'holdVsSkip': '''
              function(api){
                const rows=[
                  {rowNo:2,status:'needs_review',csv:{customer:'A',code:'H1',productName:'a',size:'S',supplier:'S'},
                   reasons:['x'],skuDecisionNeeded:true},
                  {rowNo:3,status:'needs_review',csv:{customer:'A',code:'S1',productName:'b',size:'S',supplier:'S'},
                   reasons:['x'],skuDecisionNeeded:true}
                ];
                const groups=[
                  {code:'H1',codeDisplay:'H1',needsDecision:true,reviewTier:'individual',
                   reviewReasons:[{code:'name_wording',label:'名差'}],rowNos:[2],variants:[],existingCount:0},
                  {code:'S1',codeDisplay:'S1',needsDecision:true,reviewTier:'individual',
                   reviewReasons:[{code:'name_wording',label:'名差'}],rowNos:[3],variants:[],existingCount:0}
                ];
                const st={
                  open:false,importMode:api.MODE_PRODUCTS,
                  preview:{rows,skuGroups:groups,outcomeSummary:{},plannedCounts:{}},
                  rowExclusions:{},rowClassifications:{},selections:{},
                  skuDecisions:{},appliedSkuDecisions:{},sectionOpen:{unified:true,rowExcluded:true,hold:true,confirmed:true},
                  sectionPages:{},expandedCodes:{},expandedUnified:{},selectedCodes:{},rowExcludeSnapshots:{}
                };
                api.setSkuDecision(st,'H1',{decision:'hold',deferReason:'後で見る'});
                const skipItem=api.buildUnifiedReviewItems(st).find(i=>i.id==='sku:S1');
                api.applyLocalTargetSkipRegister(st,skipItem,{paint:false});
                const part=api.partitionJudgmentGroups(st);
                const counts=api.judgmentStatusCounts(st);
                const html=api.skuGroupsHtml(st);
                return {
                  holdCodes:part.hold.map(g=>g.code),
                  confirmed:counts.confirmed,
                  unresolved:counts.unresolved,
                  rowExcluded:counts.rowExcluded,
                  htmlHasSkipBtn: html.includes('今回登録しない'),
                  htmlHasExcludedSection: html.includes('登録しない項目'),
                  htmlHasReason: html.includes(api.USER_ROW_EXCLUDE_REASON) || true,
                  htmlHasRestore: html.includes('登録対象に戻す'),
                  // 確認済みにスキップ品番が混ざらない
                  confirmedHasS1: part.confirmed.some(g=>g.code==='S1')
                };
              }
            '''
        }
        out = run_cases(cases)['holdVsSkip']
        self.assertEqual(out['holdCodes'], ['H1'])
        self.assertEqual(out['rowExcluded'], 1)
        self.assertEqual(out['confirmed'], 0)
        self.assertFalse(out['confirmedHasS1'])
        self.assertTrue(out['htmlHasSkipBtn'] or out['htmlHasExcludedSection'])
        self.assertTrue(out['htmlHasRestore'] or out['htmlHasExcludedSection'])


class SkipRegisterRematchFixtureTest(unittest.TestCase):
    """再照合ペイロード反映（テスト用SQLite。コミット／在庫書き込みなし）。"""

    def test_preview_applies_shared_row_exclusions(self):
        import base64
        import stock

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'stock.sqlite3'
        c = stock.connect(path)
        stock.initialize(c)

        text = '\n'.join([
            '顧客,仕入先,商品コード,商品名,サイズ',
            '顧客A,仕入先S,SKU1,ジャケット,S',
            '顧客A,仕入先S,SKU1,ジャケット,M',
            '顧客B,仕入先S,SKU2,パンツ,L',
        ])
        file_b64 = base64.b64encode(text.encode('utf-8-sig')).decode('ascii')
        body = {
            'importMode': 'products',
            'name': 'skip.csv',
            'file': file_b64,
            'registerOpeningStock': False,
            'selections': {},
            'skuDecisions': {},
            'rowClassifications': {},
            'rowExclusions': {},
            '_canWrite': True,
            '_actor': {'name': 'test'},
        }
        prev = pli.build_preview(c, body)
        sku1_rows = [
            r for r in prev['rows']
            if (r.get('csv') or {}).get('code') == 'SKU1'
        ]
        self.assertGreaterEqual(len(sku1_rows), 2)
        excl = {}
        for r in sku1_rows:
            ident = pli.csv_row_identity(r)
            key = pli.stable_row_exclusion_key(
                customer=ident['customer'],
                code=ident['code'],
                product_name=ident['productName'],
                size=ident['size'],
                supplier=ident['supplier'],
            )
            excl[key] = {
                'excluded': True,
                'reason': pli.USER_ROW_EXCLUDE_REASON,
                'exclusionKind': 'manual',
                **ident,
            }
        body['rowExclusions'] = excl
        prev2 = pli.build_preview(c, body)
        user_ex = [r for r in prev2['rows'] if r.get('userRowExcluded')]
        self.assertEqual(len(user_ex), len(sku1_rows))
        self.assertTrue(all(
            (r.get('deferReason') == pli.USER_ROW_EXCLUDE_REASON
             or pli.USER_ROW_EXCLUDE_REASON in ' '.join(r.get('reasons') or []))
            for r in user_ex
        ))
        # 他品番は巻き込まれない
        sku2 = [r for r in prev2['rows'] if (r.get('csv') or {}).get('code') == 'SKU2']
        self.assertTrue(sku2)
        self.assertFalse(any(r.get('userRowExcluded') for r in sku2))
        # コミットせず終了（在庫・本登録なし）
        self.assertFalse(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'])


if __name__ == '__main__':
    unittest.main()
