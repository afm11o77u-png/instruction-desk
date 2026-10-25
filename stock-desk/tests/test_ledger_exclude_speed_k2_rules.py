"""旧CSV: 除外ローカル高速化 + K2スタッフ工房の加工自動判定。

実運用DB・在庫は変更しない（テスト用SQLiteのみ）。
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import product_ledger_processing_service as ps  # noqa: E402
import stock  # noqa: E402

JS = ROOT / 'static' / 'product_ledger_csv.js'


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8-sig')).decode('ascii')


def ledger(*rows):
    header = '顧客,仕入先,商品コード,商品名,サイズ'
    return '\n'.join([header] + list(rows))


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
// paintDialog は DOM 無しで no-op 相当になるよう open=false のまま使う
const out = {};
for (const [name, fn] of Object.entries(cases)) {
  out[name] = fn(api, sandbox);
}
process.stdout.write(JSON.stringify(out));
'''


class K2ClassifyUnitTest(unittest.TestCase):
    def test_hoture_inner_aliases(self):
        for raw, want in (
            ('ほつれ補修', 'ほつれ補修'),
            ('インナー外し', 'インナー外し'),
            ('インナー取り外し', 'インナー外し'),
            ('インナー取付', 'インナー取付'),
        ):
            self.assertEqual(ps.unify_processing_service_name(raw), want, msg=raw)
            r = ps.classify_processing_service_row(
                supplier='Ｋ２スタッフ工房', product_name=raw, code='',
            )
            self.assertTrue(r['identifiable'], msg=raw)
            self.assertFalse(r['needsConfirm'], msg=raw)
            self.assertFalse(r.get('k2AutoExclude'), msg=raw)
            self.assertEqual(r['serviceName'], want)
            self.assertEqual(r['vendorName'], 'K2スタッフ工房')

    def test_process_name_from_code_field_only(self):
        """品番欄だけに加工名がある場合も認識。商品名（衣服種）は流用しない。"""
        r = ps.classify_processing_service_row(
            supplier='K2 スタッフ 工房',
            product_name='帽子',
            code='ほつれ補修',
        )
        self.assertTrue(r['identifiable'])
        self.assertEqual(r['serviceName'], 'ほつれ補修')
        self.assertEqual(r.get('serviceNameSource'), 'code')
        self.assertFalse(r.get('k2AutoExclude'))

    def test_garment_name_not_used_as_service(self):
        r = ps.classify_processing_service_row(
            supplier='K2スタッフ工房',
            product_name='チェック柄ベスト',
            code='',
        )
        self.assertFalse(r['identifiable'])
        self.assertTrue(r.get('k2AutoExclude'))
        self.assertIn('加工内容を特定できない', r.get('autoExcludeReason') or '')

    def test_vendor_normalization(self):
        for s in ('K2スタッフ工房', 'Ｋ２スタッフ工房', 'K2 スタッフ 工房', 'ｋ２ｽﾀｯﾌ工房'):
            self.assertTrue(ps.is_k2_staff_workshop(s), msg=s)
            self.assertEqual(ps.canonical_processing_vendor(s), 'K2スタッフ工房')


class K2PreviewIntegrationTest(unittest.TestCase):
    def test_known_names_auto_register_without_manual(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        text = ledger(
            'だるま食品,Ｋ２スタッフ工房,,ほつれ補修,-',
            'よつ葉,K2スタッフ工房,,インナー外し,-',
            '顧客A,某メーカー,G-1,シャツ,M',
            '顧客A,K2スタッフ工房,G-1,サイズダウン,M',
        )
        preview = pli.build_preview(c, {
            'file': csv_b64(text), 'name': 'k2-known.csv',
            '_canWrite': True, '_actor': {'name': 't'},
        })
        plans = {
            p['serviceName']: p
            for p in (preview.get('processingServicePlans') or [])
            if p.get('autoRegister')
        }
        self.assertIn('ほつれ補修', plans)
        self.assertIn('インナー外し', plans)
        self.assertIn('サイズダウン', plans)
        for name in ('ほつれ補修', 'インナー外し', 'サイズダウン'):
            self.assertEqual(plans[name]['vendorName'], 'K2スタッフ工房')
            self.assertEqual(plans[name]['displayName'], name)

        for row in preview.get('rows') or []:
            csv = row.get('csv') or {}
            if csv.get('productName') in ('ほつれ補修', 'インナー外し', 'サイズダウン'):
                self.assertFalse(row.get('processingServiceNeedsConfirm'))
                self.assertFalse(row.get('autoRowExcluded'))
                self.assertNotEqual(row.get('status'), pli.STATUS_REVIEW)

    def test_unknown_k2_auto_excludes_without_affecting_garment(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        text = ledger(
            '顧客A,某メーカー,G-9,チェック柄ベスト,M',
            '顧客A,K2スタッフ工房,G-9,チェック柄ベスト,M',
            '顧客B,K2スタッフ工房,,ほつれ補修,-',
        )
        preview = pli.build_preview(c, {
            'file': csv_b64(text), 'name': 'k2-unk.csv',
            '_canWrite': True, '_actor': {'name': 't'},
        })
        rows = preview.get('rows') or []
        auto_ex = [r for r in rows if r.get('autoRowExcluded')]
        self.assertEqual(len(auto_ex), 1, msg=[(r.get('csv'), r.get('reasons')) for r in rows])
        self.assertIn(pli.K2_AUTO_EXCLUDE_REASON, ' '.join(auto_ex[0].get('reasons') or []))
        # 確認待ちに残らない
        self.assertFalse(auto_ex[0].get('processingServiceNeedsConfirm'))
        self.assertNotEqual(auto_ex[0].get('status'), pli.STATUS_REVIEW)

        # 別行のほつれ補修は借用されず登録対象のまま
        hoture = [
            r for r in rows
            if (r.get('csv') or {}).get('productName') == 'ほつれ補修'
        ]
        self.assertEqual(len(hoture), 1)
        self.assertFalse(hoture[0].get('autoRowExcluded'))
        self.assertTrue((hoture[0].get('processingService') or {}).get('identifiable'))

        # 正常な商品本体は巻き添え除外されない
        garment = [
            r for r in rows
            if (r.get('csv') or {}).get('supplier') == '某メーカー'
        ]
        self.assertEqual(len(garment), 1)
        self.assertFalse(garment[0].get('autoRowExcluded'))
        self.assertFalse(garment[0].get('userRowExcluded'))

        # K2 は本体仕入先候補に混ざらない
        g = next(x for x in preview['skuGroups'] if x['code'] == 'G-9')
        self.assertNotIn('K2スタッフ工房', g.get('supplierCandidates') or [])

    def test_code_only_process_name_and_restore_persists(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        text = ledger(
            '顧客A,K2スタッフ工房,インナー外し,帽子,',
        )
        preview = pli.build_preview(c, {
            'file': csv_b64(text), 'name': 'code-only.csv',
            '_canWrite': True, '_actor': {'name': 't'},
        })
        row = (preview.get('rows') or [None])[0]
        self.assertIsNotNone(row)
        ps_info = row.get('processingService') or {}
        self.assertTrue(ps_info.get('identifiable'))
        self.assertEqual(ps_info.get('serviceName'), 'インナー外し')
        self.assertFalse(row.get('autoRowExcluded'))

        # 加工名不明→自動除外→手入力復帰→再照合で勝手に除外しない
        text2 = ledger('顧客B,K2スタッフ工房,,謎の帽子,')
        prev2 = pli.build_preview(c, {
            'file': csv_b64(text2), 'name': 'restore.csv',
            '_canWrite': True, '_actor': {'name': 't'},
        })
        ex = (prev2.get('rows') or [None])[0]
        self.assertTrue(ex.get('autoRowExcluded'))
        prev3 = pli.build_preview(c, {
            'file': csv_b64(text2), 'name': 'restore.csv',
            '_canWrite': True, '_actor': {'name': 't'},
            'rowClassifications': {
                str(ex['rowNo']): {
                    'processing': 'ほつれ補修',
                    'plannedName': 'ほつれ補修',
                    'userEdited': True,
                    'userRestoredFromAutoExclude': True,
                    'confirmed': True,
                },
            },
        })
        restored = (prev3.get('rows') or [None])[0]
        self.assertFalse(restored.get('autoRowExcluded'))
        self.assertTrue((restored.get('processingService') or {}).get('identifiable'))
        self.assertEqual((restored.get('processingService') or {}).get('serviceName'), 'ほつれ補修')


class ExcludeLocalSpeedTest(unittest.TestCase):
    def test_ui_no_longer_calls_runPreview_on_exclude(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('applyLocalRowExclude', js)
        self.assertIn('harvestFormEdits', js)
        self.assertIn('K2_AUTO_EXCLUDE_REASON', js)
        # 除外クリック直後の runPreview() 呼び出しが残っていないこと
        # （「選択を反映して再照合」経路の runPreview 定義自体は残る）
        self.assertNotIn('setRowExcluded(st,row,true)\n        st.toast', js)
        self.assertIn('applyLocalRowExclude(st,row,true)', js)
        self.assertIn('applyLocalRowExclude(st,row,false', js)

    def test_local_exclude_updates_counts_without_server(self):
        cases = {
            'localExcludeFastPath': '''
              function(api){
                const rows=[];
                for(let i=0;i<120;i++){
                  rows.push({
                    rowNo:i+2,status:'needs_review',statusLabel:'要確認',canCommit:false,
                    csv:{customer:'C'+i,code:'',productName:'部品'+i,size:'',supplier:'部品屋'},
                    product:{blankSku:true,action:'create',category:''},reasons:['区分'],
                    physicalPartsView:true,parenClassification:{physicalPartsView:true}
                  });
                }
                const st={
                  open:false,busy:false,
                  preview:{rows,skuGroups:[],outcomeSummary:{},plannedCounts:{}},
                  rowExclusions:{},rowClassifications:{},selections:{},skuDecisions:{},
                  sectionOpen:{},sectionPages:{},expandedCodes:{},expandedUnified:{},
                  selectedCodes:{},rowExcludeSnapshots:{}
                };
                // paintDialog は open=false で早期 return
                const t0=Date.now();
                for(let i=0;i<25;i++){
                  const row=rows[i];
                  api.snapshotRowBeforeExclude(st,row);
                  api.setRowExcluded(st,row,true);
                  row.status='hold';row.userRowExcluded=true;row.autoRowExcluded=false;
                  row.exclusionKind='manual';row.canCommit=false;
                  row.deferReason=api.USER_ROW_EXCLUDE_REASON;
                  row.reasons=[api.USER_ROW_EXCLUDE_REASON];
                }
                const ms=Date.now()-t0;
                const counts=api.judgmentStatusCounts(st);
                return {
                  ms, rowExcluded:counts.rowExcluded, unresolved:counts.unresolved,
                  underBudget: ms<500
                };
              }
            ''',
            'autoExcludeListedSeparately': '''
              function(api){
                const st={
                  open:false,
                  preview:{rows:[
                    {rowNo:2,status:'hold',userRowExcluded:true,exclusionKind:'manual',
                     deferReason:api.USER_ROW_EXCLUDE_REASON,
                     csv:{customer:'A',code:'',productName:'x',size:'',supplier:'S'},reasons:[api.USER_ROW_EXCLUDE_REASON]},
                    {rowNo:3,status:'hold',autoRowExcluded:true,exclusionKind:'auto',
                     deferReason:api.K2_AUTO_EXCLUDE_REASON,
                     csv:{customer:'B',code:'',productName:'帽子',size:'',supplier:'K2スタッフ工房'},
                     reasons:[api.K2_AUTO_EXCLUDE_REASON],
                     processingService:{k2AutoExclude:true}}
                  ]},
                  rowExclusions:{},rowClassifications:{},selections:{},skuDecisions:{},sectionOpen:{}
                };
                const list=api.listExcludedRows(st);
                return {
                  n:list.length,
                  reasons:list.map(r=>api.rowExclusionReason(st,r)),
                  kinds:list.map(r=>r.exclusionKind|| (r.autoRowExcluded?'auto':'manual'))
                };
              }
            '''
        }
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
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            out = json.loads(proc.stdout)

        a = out['localExcludeFastPath']
        self.assertEqual(a['rowExcluded'], 25)
        self.assertTrue(a['underBudget'], msg=f"local exclude too slow: {a['ms']}ms")
        self.assertLess(a['unresolved'], 120)

        b = out['autoExcludeListedSeparately']
        self.assertEqual(b['n'], 2)
        self.assertIn(pli.K2_AUTO_EXCLUDE_REASON, b['reasons'])
        self.assertIn('auto', b['kinds'])
        self.assertIn('manual', b['kinds'])


class ExcludeServerTimingSmokeTest(unittest.TestCase):
    def test_preview_exclude_meta_and_timing_note(self):
        """サーバ再照合は明示時のみ。除外キー適用後も件数整合。"""
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        rows = [f'顧客{i},部品屋,,生地{i},' for i in range(40)]
        rows.append('顧客X,K2スタッフ工房,,謎の帽子,')
        text = ledger(*rows)
        t0 = time.perf_counter()
        prev = pli.build_preview(c, {
            'file': csv_b64(text), 'name': 'speed.csv',
            '_canWrite': True, '_actor': {'name': 't'},
        })
        full_ms = (time.perf_counter() - t0) * 1000
        auto = [r for r in prev['rows'] if r.get('autoRowExcluded')]
        self.assertEqual(len(auto), 1)
        self.assertGreaterEqual(
            (prev.get('autoRowExclusions') or {}).get('count') or 0, 1,
        )
        # フル照合は残るが、クリック経路では呼ばない（報告用に計測値を残す）
        self.assertLess(full_ms, 60000)


if __name__ == '__main__':
    unittest.main()
