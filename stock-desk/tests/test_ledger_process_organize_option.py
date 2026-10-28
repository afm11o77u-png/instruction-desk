"""個別整理「加工サービスとして整理」（fixtures / temp DB のみ。本番書込なし）。"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import stock  # noqa: E402

JS = ROOT / 'static' / 'product_ledger_csv.js'

NODE_HARNESS = r'''
const fs = require('fs');
const vm = require('vm');
const jsPath = process.argv[2];
const casesPath = process.argv[3];
const code = fs.readFileSync(jsPath, 'utf8');
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
  ));
}
const sandbox = {
  window: {},
  document: {
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  },
  console,
  CSS: { escape: (s) => String(s) },
  esc,
  $: () => null,
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const api = sandbox.window.ProductLedgerCsv && sandbox.window.ProductLedgerCsv.__test;
if (!api) { console.error('missing __test'); process.exit(2); }

function baseSt() {
  const groups = [
    {
      code: 'K2-1', codeDisplay: 'K2-1', needsDecision: true, reviewTier: 'individual',
      nameCandidates: ['ライン加工', 'スナッパー加工'],
      supplierCandidates: ['K2スタッフ工房'],
      variants: [
        { key: 'line', productName: 'ライン加工', supplier: 'K2スタッフ工房',
          processing: 'ライン加工', sizes: ['—'], customers: ['顧客A'], rowNos: [2] },
        { key: 'snap', productName: 'スナッパー加工', supplier: 'K2スタッフ工房',
          processing: 'スナッパー加工', sizes: ['—'], customers: ['顧客A'], rowNos: [3] },
      ],
      rowNos: [2, 3], existingProducts: [], existingCount: 0,
      reviewReasons: [{ code: 'processing', label: '加工差' }],
    },
    {
      code: 'P1', codeDisplay: 'P1', needsDecision: true, reviewTier: 'individual',
      nameCandidates: ['生地カット'], supplierCandidates: ['某資材'],
      variants: [
        { key: 'fab', productName: '生地カット', supplier: '某資材',
          sizes: ['—'], customers: ['顧客A'], rowNos: [4] },
      ],
      rowNos: [4], existingProducts: [], existingCount: 0,
      reviewReasons: [{ code: 'kind', label: '商品種の疑い' }],
    },
  ];
  return {
    open: false, busy: false,
    preview: {
      rows: [
        { rowNo: 2, status: 'needs_review',
          csv: { code: 'K2-1', productName: 'ライン加工', size: '—', supplier: 'K2スタッフ工房', customer: '顧客A' },
          processingService: { serviceName: 'ライン加工', vendorName: 'K2スタッフ工房', isProcessingService: true, identifiable: true },
          processingServiceView: true, skuDecisionNeeded: true },
        { rowNo: 3, status: 'needs_review',
          csv: { code: 'K2-1', productName: 'スナッパー加工', size: '—', supplier: 'K2スタッフ工房', customer: '顧客A' },
          processingService: { serviceName: 'スナッパー加工', vendorName: 'K2スタッフ工房', isProcessingService: true, identifiable: true },
          processingServiceView: true, skuDecisionNeeded: true },
        { rowNo: 4, status: 'needs_review',
          csv: { code: 'P1', productName: '生地カット', size: '—', supplier: '某資材', customer: '顧客A' },
          physicalPartsView: true, skuDecisionNeeded: true },
      ],
      skuGroups: groups,
    },
    skuDecisions: {}, appliedSkuDecisions: {},
    selectedCodes: {}, expandedCodes: {}, expandedConfirmed: {},
    sectionOpen: Object.assign({}, api.SECTION_DEFAULTS),
    rowClassifications: {}, rowExclusions: {},
  };
}

const out = {};
for (const [name, src] of Object.entries(cases)) {
  // eslint-disable-next-line no-new-func
  const fn = new Function('api', 'baseSt', `return (${src})(api, baseSt);`);
  out[name] = fn(api, baseSt);
}
process.stdout.write(JSON.stringify(out));
'''


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8-sig')).decode('ascii')


def ledger(*rows):
    return '\n'.join(['顧客,仕入先,商品コード,商品名,サイズ', *rows])


def run_js_cases(cases: dict) -> dict:
    with tempfile.TemporaryDirectory() as td:
        cases_path = Path(td) / 'cases.json'
        harness = Path(td) / 'harness.js'
        cases_path.write_text(json.dumps(cases), encoding='utf-8')
        harness.write_text(NODE_HARNESS, encoding='utf-8')
        proc = subprocess.run(
            ['node', str(harness), str(JS), str(cases_path)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr + proc.stdout)
        return json.loads(proc.stdout)


class ProcessOrganizeOptionSourceTest(unittest.TestCase):
    def test_ui_option_and_fields(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('加工サービスとして整理', js)
        self.assertIn('as_process_service', js)
        self.assertIn('部品・資材として整理', js)
        self.assertIn('data-pl-sep-service', js)
        self.assertIn('data-pl-sep-vendor', js)
        self.assertIn('data-pl-sep-supp', js)
        self.assertIn('initSeparateProcessServicePlan', js)
        self.assertIn('detectedProcessServiceForVariant', js)
        self.assertIn('separateProcessServiceConfirmedDetailHtml', js)
        self.assertIn('区分: 加工サービス', js)
        # 選択は編集状態のみ・スクロール chase なし
        self.assertIn('方針・整理方法の選択は編集状態のみ', js)
        self.assertNotIn("paintDialog({anchorCode:", js)


class ProcessOrganizeOptionUiTest(unittest.TestCase):
    def test_select_init_rematch_confirmed_detail(self):
        cases = {
            'flow': r'''
              function(api, baseSt){
                const st = baseSt();
                api.snapshotAppliedSkuDecisions(st);
                // 選択のみ → 反映待ち（再照合なし）
                api.setSkuDecision(st, 'K2-1', {decision:'separate'});
                let part = api.partitionJudgmentGroups(st);
                const pendingOk = part.confirmed.some(g=>g.code==='K2-1')
                  && api.confirmedItemStatusKey(st, part.confirmed.find(g=>g.code==='K2-1'))==='pending';
                // 整理方法選択 → 検出値で初期化（編集状態）
                api.initSeparateProcessServicePlan(st, 'K2-1', 'line');
                api.initSeparateProcessServicePlan(st, 'K2-1', 'snap');
                const d = st.skuDecisions['K2-1'];
                const line = d.plans.line;
                const snap = d.plans.snap;
                // 再照合前は approach 未完でも確認済み（反映待ち）のまま
                const completePending = api.userActionChoiceComplete(
                  st, st.preview.skuGroups.find(g=>g.code==='K2-1'));
                // 再照合成功相当
                api.snapshotAppliedSkuDecisions(st);
                const completeAfter = api.userActionChoiceComplete(
                  st, st.preview.skuGroups.find(g=>g.code==='K2-1'));
                part = api.partitionJudgmentGroups(st);
                const confirmed = part.confirmed.some(g=>g.code==='K2-1');
                st.expandedConfirmed = { 'K2-1': true };
                // サーバ再照合後の予定を模す
                st.preview.skuGroups.find(g=>g.code==='K2-1').separateProcessServicePlans = [
                  { variantKey:'line', serviceName:'ライン加工', vendorName:'K2スタッフ工房',
                    category:'加工サービス', action:'create', dedupeKey:'line|k2' },
                  { variantKey:'snap', serviceName:'スナッパー加工', vendorName:'K2スタッフ工房',
                    category:'加工サービス', action:'create', dedupeKey:'snap|k2' },
                ];
                const detail = api.separateProcessServiceConfirmedDetailHtml(
                  st, st.preview.skuGroups.find(g=>g.code==='K2-1'));
                const listHtml = api.confirmedListRowsHtml(st, part.confirmed);
                st.skuDecisions['K2-1'] = {decision:'separate', plans:d.plans};
                const detailFull = api.skuRowDetailHtml(st, st.preview.skuGroups.find(g=>g.code==='K2-1'));
                // 部品・資材オプションも同居（P1 を個別整理表示にして確認）
                api.setSkuDecision(st, 'P1', {decision:'separate'});
                const partsOpt = api.skuRowDetailHtml(st, st.preview.skuGroups.find(g=>g.code==='P1'));
                return {
                  pendingOk, completePending, completeAfter, confirmed,
                  lineName: line.serviceName, lineVendor: line.vendorName, lineApproach: line.approach,
                  snapName: snap.serviceName, snapVendor: snap.vendorName,
                  detailHasLine: detail.includes('ライン加工'),
                  detailHasSnap: detail.includes('スナッパー加工'),
                  detailHasVendor: detail.includes('K2スタッフ工房'),
                  detailHasCategory: detail.includes('加工サービス'),
                  listHasCategory: listHtml.includes('加工サービス'),
                  fullHasOption: detailFull.includes('加工サービスとして整理'),
                  partsKept: partsOpt.includes('部品・資材として整理'),
                  partsHasProcess: partsOpt.includes('加工サービスとして整理'),
                };
              }
            ''',
            'missingFields': r'''
              function(api, baseSt){
                const st = baseSt();
                api.snapshotAppliedSkuDecisions(st);
                api.setSkuDecision(st, 'K2-1', {
                  decision:'separate',
                  plans:{
                    line:{approach:'as_process_service', serviceName:'', vendorName:'K2スタッフ工房'},
                    snap:{approach:'as_process_service', serviceName:'スナッパー加工', vendorName:''}
                  }
                });
                api.snapshotAppliedSkuDecisions(st);
                const issues = api.remainingDecisionIssues(
                  st, st.preview.skuGroups.find(g=>g.code==='K2-1'));
                const incomplete = !api.userActionChoiceComplete(
                  st, st.preview.skuGroups.find(g=>g.code==='K2-1'));
                const part = api.partitionJudgmentGroups(st);
                return {
                  incomplete,
                  backToList: part.individual.some(g=>g.code==='K2-1'),
                  labels: issues.map(x=>x.label),
                };
              }
            ''',
        }
        out = run_js_cases(cases)
        f = out['flow']
        self.assertTrue(f['pendingOk'])
        self.assertTrue(f['completePending'])
        self.assertTrue(f['completeAfter'])
        self.assertTrue(f['confirmed'])
        self.assertEqual(f['lineApproach'], 'as_process_service')
        self.assertEqual(f['lineName'], 'ライン加工')
        self.assertEqual(f['lineVendor'], 'K2スタッフ工房')
        self.assertEqual(f['snapName'], 'スナッパー加工')
        self.assertEqual(f['snapVendor'], 'K2スタッフ工房')
        self.assertTrue(f['detailHasLine'])
        self.assertTrue(f['detailHasSnap'])
        self.assertTrue(f['detailHasVendor'])
        self.assertTrue(f['detailHasCategory'])
        self.assertTrue(f['listHasCategory'])
        self.assertTrue(f['fullHasOption'])
        self.assertTrue(f['partsKept'])
        self.assertTrue(f['partsHasProcess'])

        m = out['missingFields']
        self.assertTrue(m['incomplete'])
        self.assertTrue(m['backToList'])
        self.assertTrue(any('加工名' in x for x in m['labels']))
        self.assertTrue(any('加工業者' in x for x in m['labels']))


class ProcessOrganizeOptionImportTest(unittest.TestCase):
    def test_select_rematch_planned_save_reuse_separate_services(self):
        folder, path, c = newdb()
        try:
            # 同一SKUに異なる加工名 → 別名統合せず別サービス
            text = ledger(
                '顧客A,K2スタッフ工房,K2-1,ライン加工,—',
                '顧客A,K2スタッフ工房,K2-1,スナッパー加工,—',
                '顧客A,某資材,P-FAB,生地ロール,—',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'proc-org.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            group = next(
                (g for g in preview['skuGroups'] if g.get('code') == 'K2-1'),
                None,
            )
            # 自動整理で既に分かれている場合でも、個別整理経路を検証できるよう
            # 決定が必要なグループ、または variants があることを前提にする
            self.assertTrue(group is not None or any(
                (r.get('csv') or {}).get('code') == 'K2-1' for r in preview['rows']
            ))

            # variants / 行からキーを組み立て
            line_rows = [
                r for r in preview['rows']
                if (r.get('csv') or {}).get('productName') == 'ライン加工'
            ]
            snap_rows = [
                r for r in preview['rows']
                if (r.get('csv') or {}).get('productName') == 'スナッパー加工'
            ]
            self.assertTrue(line_rows and snap_rows)
            line_key = pli._row_variant(line_rows[0])['key']
            snap_key = pli._row_variant(snap_rows[0])['key']
            self.assertNotEqual(line_key, snap_key)

            decisions = {
                'K2-1': {
                    'decision': 'separate',
                    'plans': {
                        line_key: {
                            'approach': 'as_process_service',
                            'serviceName': 'ライン加工',
                            'vendorName': 'K2スタッフ工房',
                            'supplement': '左胸',
                        },
                        snap_key: {
                            'approach': 'as_process_service',
                            'serviceName': 'スナッパー加工',
                            'vendorName': 'K2スタッフ工房',
                        },
                    },
                },
            }
            rematch = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'proc-org.csv',
                'skuDecisions': decisions,
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans = [
                p for p in (rematch.get('processingServicePlans') or [])
                if p.get('autoRegister') and p.get('vendorName') == 'K2スタッフ工房'
            ]
            names = sorted(p.get('serviceName') or '' for p in plans)
            self.assertIn('ライン加工', names)
            self.assertIn('スナッパー加工', names)
            # 別名統合しない → 2件
            self.assertGreaterEqual(
                sum(1 for n in names if n in ('ライン加工', 'スナッパー加工')),
                2,
            )

            line_plan = next(p for p in plans if p['serviceName'] == 'ライン加工')
            snap_plan = next(p for p in plans if p['serviceName'] == 'スナッパー加工')
            self.assertNotEqual(line_plan['dedupeKey'], snap_plan['dedupeKey'])
            self.assertEqual(line_plan['category'], stock.CATEGORY_PROCESS_SERVICE)
            self.assertTrue(line_plan.get('blankSku') or line_plan.get('code') == '')

            for r in rematch['rows']:
                csv = r.get('csv') or {}
                if csv.get('productName') not in ('ライン加工', 'スナッパー加工'):
                    continue
                prod = r.get('product') or {}
                self.assertEqual(prod.get('category'), stock.CATEGORY_PROCESS_SERVICE)
                self.assertTrue(prod.get('blankSku') or prod.get('code') in ('', None))
                self.assertNotEqual(prod.get('code'), 'K2-1')
                self.assertTrue(prod.get('processingServiceProduct'))
                self.assertTrue(r.get('canCommit'))
                # CSV 元品番・顧客は照合用に残す
                prov = r.get('specProvenance') or {}
                self.assertEqual(prov.get('originalCode') or csv.get('code'), 'K2-1')
                self.assertEqual(prov.get('targetCustomer') or csv.get('customer'), '顧客A')
                self.assertEqual(
                    (r.get('product') or {}).get('officialName'),
                    csv.get('productName'),
                )

            # 加工サービス以外の行は加工サービス区分にしない
            fab = next(
                r for r in rematch['rows']
                if (r.get('csv') or {}).get('productName') == '生地ロール'
            )
            fab_cat = (
                (fab.get('product') or {}).get('proposedCategory')
                or (fab.get('product') or {}).get('category')
                or ''
            )
            self.assertNotEqual(fab_cat, stock.CATEGORY_PROCESS_SERVICE)
            self.assertFalse((fab.get('product') or {}).get('processingServiceProduct'))

            g2 = next((g for g in rematch['skuGroups'] if g.get('code') == 'K2-1'), None)
            if g2 and g2.get('separateProcessServicePlans'):
                sep_names = {p['serviceName'] for p in g2['separateProcessServicePlans']}
                self.assertEqual(sep_names, {'ライン加工', 'スナッパー加工'})
                for p in g2['separateProcessServicePlans']:
                    self.assertEqual(p['category'], stock.CATEGORY_PROCESS_SERVICE)

            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'proc-org.csv',
                'skuDecisions': decisions,
                'previewFingerprint': rematch['previewFingerprint'],
                'previewAt': rematch['previewAt'],
                'token': 'proc-org-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()

            line = c.execute(
                "SELECT id, code, category, name FROM products WHERE name=?",
                ('ライン加工',),
            ).fetchone()
            snap = c.execute(
                "SELECT id, code, category, name FROM products WHERE name=?",
                ('スナッパー加工',),
            ).fetchone()
            self.assertIsNotNone(line)
            self.assertIsNotNone(snap)
            self.assertNotEqual(line['id'], snap['id'])
            self.assertEqual(line['category'], stock.CATEGORY_PROCESS_SERVICE)
            self.assertEqual(snap['category'], stock.CATEGORY_PROCESS_SERVICE)
            self.assertEqual(line['code'] or '', '')
            self.assertEqual(snap['code'] or '', '')

            # 再取込 → 同名・同業者 reuse
            rematch2 = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'proc-org.csv',
                'skuDecisions': decisions,
                '_canWrite': True, '_actor': {'name': 't'},
            })
            plans2 = [
                p for p in (rematch2.get('processingServicePlans') or [])
                if p.get('serviceName') in ('ライン加工', 'スナッパー加工')
            ]
            self.assertTrue(all(p.get('action') == 'reuse' for p in plans2), plans2)
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'proc-org.csv',
                'skuDecisions': decisions,
                'previewFingerprint': rematch2['previewFingerprint'],
                'previewAt': rematch2['previewAt'],
                'token': 'proc-org-2',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            n_line = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE name=? AND category=?",
                ('ライン加工', stock.CATEGORY_PROCESS_SERVICE),
            ).fetchone()['n']
            n_snap = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE name=? AND category=?",
                ('スナッパー加工', stock.CATEGORY_PROCESS_SERVICE),
            ).fetchone()['n']
            self.assertEqual(n_line, 1)
            self.assertEqual(n_snap, 1)
        finally:
            c.close()
            folder.cleanup()

    def test_as_parts_still_physical(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,某メーカー,MIX-1,作業上着,M',
                '顧客A,某メーカー,MIX-1,作業上着（別色）,L',
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'parts.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            group = next(g for g in preview['skuGroups'] if g['code'] == 'MIX-1')
            self.assertTrue(group.get('needsDecision') or len(group.get('variants') or []) >= 1)
            keys = [v['key'] for v in (group.get('variants') or [])]
            self.assertTrue(keys)
            decisions = {
                'MIX-1': {
                    'decision': 'separate',
                    'plans': {
                        k: {'approach': 'as_parts'} for k in keys
                    },
                },
            }
            rematch = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'parts.csv',
                'skuDecisions': decisions,
                '_canWrite': True, '_actor': {'name': 't'},
            })
            for r in rematch['rows']:
                if (r.get('csv') or {}).get('code') != 'MIX-1':
                    continue
                if (r.get('skuDecision') or '') != 'separate':
                    continue
                prod = r.get('product') or {}
                self.assertEqual(prod.get('category'), stock.CATEGORY_PARTS)
                self.assertNotEqual(prod.get('category'), stock.CATEGORY_PROCESS_SERVICE)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
