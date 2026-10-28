"""旧CSV確認済み一覧（UIのみ。実DB登録・在庫変更なし。fixtures）。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

JS = ROOT / 'static' / 'product_ledger_csv.js'
CSS = ROOT / 'static' / 'style.css'

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
      code: 'M1', codeDisplay: 'M1', needsDecision: true, reviewTier: 'individual',
      nameCandidates: ['白シャツ', '黒シャツ'], supplierCandidates: ['仕入A'],
      variants: [
        { key: 'v1', productName: '白シャツ', supplier: '仕入A', sizes: ['M','L'], customers: ['C'], rowNos: [2] },
        { key: 'v2', productName: '黒シャツ', supplier: '仕入A', sizes: ['M'], customers: ['C'], rowNos: [3] },
      ],
      rowNos: [2, 3], existingProducts: [], existingCount: 0,
      reviewReasons: [{ code: 'color', label: '色差' }],
      plannedOfficialName: '白シャツ', plannedPrimarySupplier: '仕入A',
      plannedRegisteredColor: '',
    },
    {
      code: 'C1', codeDisplay: 'C1', needsDecision: true, reviewTier: 'individual',
      nameCandidates: ['緑ジャケット', '蛍光グリーンジャケット'], supplierCandidates: ['仕入B'],
      variants: [
        { key: 'g', productName: '緑ジャケット', supplier: '仕入B', sizes: ['S'], customers: ['C'], rowNos: [4] },
        { key: 'fg', productName: '蛍光グリーンジャケット', supplier: '仕入B', sizes: ['M'], customers: ['C'], rowNos: [5] },
      ],
      rowNos: [4, 5], existingProducts: [], existingCount: 0,
      reviewReasons: [{ code: 'color', label: '色差' }],
      colorSplitPlans: {
        complete: true,
        plans: [
          { colorKey: '緑', color: '緑', makerCode: 'C1', internalCode: 'C1-緑', productName: 'ジャケット', rowNos: [4] },
          { colorKey: '蛍光グリーン', color: '蛍光グリーン', makerCode: 'C1', internalCode: 'C1-蛍光グリーン', productName: 'ジャケット', rowNos: [5] },
        ],
        unassignedRows: [],
      },
    },
    {
      code: 'S1', codeDisplay: 'S1', needsDecision: true, reviewTier: 'individual',
      nameCandidates: ['Aパンツ', 'Bパンツ'], supplierCandidates: ['仕入C'],
      variants: [
        { key: 'a', productName: 'Aパンツ', supplier: '仕入C', sizes: ['M'], customers: ['C'], rowNos: [6] },
        { key: 'b', productName: 'Bパンツ', supplier: '仕入C', sizes: ['L'], customers: ['C'], rowNos: [7] },
      ],
      rowNos: [6, 7], existingProducts: [], existingCount: 0,
      reviewReasons: [{ code: 'kind', label: '商品種の疑い' }],
    },
    {
      code: 'H1', codeDisplay: 'H1', needsDecision: true, reviewTier: 'individual',
      nameCandidates: ['保留品'], supplierCandidates: ['仕入D'],
      variants: [{ key: 'h', productName: '保留品', supplier: '仕入D', sizes: ['M'], customers: ['C'], rowNos: [8] }],
      rowNos: [8], existingProducts: [], existingCount: 0,
      reviewReasons: [{ code: 'color', label: '色差' }],
    },
    {
      code: 'B1', codeDisplay: 'B1', needsDecision: true, reviewTier: 'bulk',
      nameCandidates: ['同名シャツ'], supplierCandidates: ['仕入X', '仕入Y'],
      variants: [
        { key: 'x', productName: '同名シャツ', supplier: '仕入X', sizes: ['M'], customers: ['C'], rowNos: [9] },
        { key: 'y', productName: '同名シャツ', supplier: '仕入Y', sizes: ['L'], customers: ['C'], rowNos: [10] },
      ],
      rowNos: [9, 10], existingProducts: [], existingCount: 0,
      nameWordingDiff: false, supplierDiff: true,
      reviewReasons: [{ code: 'supplier', label: '仕入先差' }],
      plannedOfficialName: '同名シャツ', plannedPrimarySupplier: '仕入X',
    },
    {
      code: 'A1', codeDisplay: 'A1', needsDecision: false, reviewTier: 'auto',
      nameCandidates: ['自動パンツ'], supplierCandidates: ['仕入Z'],
      variants: [], rowNos: [11], existingProducts: [], existingCount: 0,
      reviewReasons: [],
    },
  ];
  return {
    open: false, busy: false,
    preview: {
      rows: [
        { rowNo: 2, status: 'needs_review', csv: { code: 'M1', productName: '白シャツ', size: 'M', supplier: '仕入A', customer: 'C' }, skuDecisionNeeded: true },
        { rowNo: 3, status: 'needs_review', csv: { code: 'M1', productName: '黒シャツ', size: 'M', supplier: '仕入A', customer: 'C' }, skuDecisionNeeded: true },
        { rowNo: 4, status: 'needs_review', csv: { code: 'C1', productName: '緑ジャケット', size: 'S', supplier: '仕入B', customer: 'C' }, skuDecisionNeeded: true },
        { rowNo: 5, status: 'needs_review', csv: { code: 'C1', productName: '蛍光グリーンジャケット', size: 'M', supplier: '仕入B', customer: 'C' }, skuDecisionNeeded: true },
        { rowNo: 6, status: 'needs_review', csv: { code: 'S1', productName: 'Aパンツ', size: 'M', supplier: '仕入C', customer: 'C' }, skuDecisionNeeded: true },
        { rowNo: 7, status: 'needs_review', csv: { code: 'S1', productName: 'Bパンツ', size: 'L', supplier: '仕入C', customer: 'C' }, skuDecisionNeeded: true },
        { rowNo: 8, status: 'needs_review', csv: { code: 'H1', productName: '保留品', size: 'M', supplier: '仕入D', customer: 'C' }, skuDecisionNeeded: true },
        { rowNo: 9, status: 'needs_review', csv: { code: 'B1', productName: '同名シャツ', size: 'M', supplier: '仕入X', customer: 'C' }, skuDecisionNeeded: true },
        { rowNo: 10, status: 'needs_review', csv: { code: 'B1', productName: '同名シャツ', size: 'L', supplier: '仕入Y', customer: 'C' }, skuDecisionNeeded: true },
      ],
      skuGroups: groups,
      autoMergePlans: [{ code: 'A1', label: '自動パンツ', productName: '自動パンツ', supplier: '仕入Z', sizes: ['M'], customers: ['C'], csvRowCount: 1 }],
      plannedCounts: { autoMergedSkuGroups: 1 },
      outcomeSummary: { excludedRows: 0 },
    },
    skuDecisions: {},
    appliedSkuDecisions: {},
    rowClassifications: {},
    rowExclusions: {},
    selections: {},
    selectedCodes: {},
    expandedCodes: {},
    expandedConfirmed: {},
    sectionOpen: Object.assign({}, api.SECTION_DEFAULTS),
    sectionPages: {},
    confirmedQ: '',
    skuQ: '',
  };
}

const out = {};
for (const [name, fnSrc] of Object.entries(cases)) {
  const fn = new Function('api', 'baseSt', 'return (' + fnSrc + ')(api, baseSt);');
  out[name] = fn(api, baseSt);
}
process.stdout.write(JSON.stringify(out));
'''


def run_cases(cases: dict) -> dict:
    with tempfile.TemporaryDirectory() as td:
        cases_path = Path(td) / 'cases.json'
        cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
        harness = Path(td) / 'harness.js'
        harness.write_text(NODE_HARNESS, encoding='utf-8')
        proc = subprocess.run(
            ['node', str(harness), str(JS), str(cases_path)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr + proc.stdout)
        return json.loads(proc.stdout)


class ConfirmedReviewListSourceTest(unittest.TestCase):
    def test_confirmed_section_wiring(self):
        js = JS.read_text(encoding='utf-8')
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('確認済み一覧', js)
        self.assertIn('data-sku-section-block="confirmed"', js)
        self.assertIn('confirmed:true', js)
        self.assertIn('反映待ち', js)
        self.assertIn('CONFIRMED_STATUS', js)
        self.assertIn('userActionChoiceComplete', js)
        self.assertIn('stillProblematicAfterRematch', js)
        self.assertIn('対応を変更する', js)
        self.assertIn('plConfirmedQ', js)
        self.assertIn('data-pl-confirmed-expand', js)
        self.assertIn('data-pl-confirmed-change', js)
        self.assertIn('reselectCodes', js)
        self.assertIn('対応の再選択モード', js)
        self.assertIn('confirmed-reselect-panel', js)
        self.assertIn('確認済み ≠ 登録完了', js)
        self.assertIn('確認して一括登録（保留・登録しない項目を除く）', js)
        self.assertIn('sku-section-confirmed', css)
        self.assertIn('confirmed-reselect-panel', css)
        # 二重追跡を避ける: 一括側の「承認済み」サブリストを出さない
        self.assertIn('承認済みの品番は「承認済み（未登録）」としてここに残さず', js)
        self.assertNotIn('<h4>承認済み（未登録） ${part.approved.length} 件</h4>', js)
        self.assertNotIn('個別整理済み（未登録）', js)
        # 自動決定は確認済みに含めないコメント
        self.assertIn('自動決定は含めない', js)
        # スクロール維持（方針移動で resetListScroll／操作行 chase を付けない）
        self.assertIn('確認済み移動でも操作行 chase せず、ビューポートを維持', js)
        self.assertNotIn('paintDialog({anchorCode:', js)


class ConfirmedReviewListLogicTest(unittest.TestCase):
    def test_merge_color_separate_hold_auto_flow(self):
        cases = {
            'selectThenRematch': r'''
              function(api, baseSt){
                const st = baseSt();
                api.snapshotAppliedSkuDecisions(st);
                // まとめる → 反映待ちで確認済みへ
                api.applyMergeDefaults(st, 'M1', st.preview.skuGroups.find(g=>g.code==='M1'));
                api.setSkuDecision(st, 'C1', {decision:'color_split'});
                api.setSkuDecision(st, 'S1', {decision:'separate'});
                api.setSkuDecision(st, 'H1', {decision:'hold'});
                api.applyMergeDefaults(st, 'B1', st.preview.skuGroups.find(g=>g.code==='B1'));
                let part = api.partitionJudgmentGroups(st);
                let counts = api.judgmentStatusCounts(st);
                const pendingCodes = part.confirmed.map(g=>g.code).sort();
                const statuses = {};
                part.confirmed.forEach(g=>{ statuses[g.code]=api.confirmedItemStatusKey(st,g); });
                const holdCodes = part.hold.map(g=>g.code);
                const autoCount = part.autoCount;
                const htmlPending = api.confirmedListRowsHtml(st, part.confirmed);
                // 対応を変更する → 行内再選択（詳細を開くだけではボタンを出さない）
                st.reselectCodes = { M1: true };
                const htmlReselect = api.confirmedListRowsHtml(st, part.confirmed);
                // 再選択で別方針 → 反映待ちのまま編集保持
                api.setSkuDecision(st, 'M1', {decision:'color_split'});
                st.reselectCodes = { M1: false };
                const m1Pending = api.skuDecisionPendingRematch(st, 'M1');
                const m1Status = api.confirmedItemStatusKey(st, part.confirmed.find(g=>g.code==='M1')||st.preview.skuGroups.find(g=>g.code==='M1'));
                // 再照合成功スナップショット → 問題なければ確認済み。個別整理は approach 未入力なら確認リストへ戻る
                api.setSkuDecision(st, 'M1', {decision:'merge'});
                api.snapshotAppliedSkuDecisions(st);
                part = api.partitionJudgmentGroups(st);
                counts = api.judgmentStatusCounts(st);
                const afterStatuses = {};
                part.confirmed.forEach(g=>{ afterStatuses[g.code]=api.confirmedItemStatusKey(st,g); });
                const separateBack = part.individual.map(g=>g.code);
                // 色別詳細は分割商品ごと
                st.expandedConfirmed = { C1: true };
                const splitHtml = api.colorSplitConfirmedDetailHtml(st, st.preview.skuGroups.find(g=>g.code==='C1'));
                const sectionHtml = api.skuGroupsHtml(st);
                // 再照合後に色別 incomplete → 確認リストへ戻す
                st.preview.skuGroups.find(g=>g.code==='C1').colorSplitPlans = {
                  complete: false, plans: [], unassignedRows: [{ rowNo: 4, reason: '行き先未定' }]
                };
                part = api.partitionJudgmentGroups(st);
                const backToList = part.individual.map(g=>g.code);
                const leftover = api.remainingDecisionIssues(st, st.preview.skuGroups.find(g=>g.code==='C1'));
                // 除外は確認済みに混ぜない
                const row = st.preview.rows[0];
                api.setRowExcluded(st, row, true, { reason: api.USER_ROW_EXCLUDE_REASON });
                const excl = api.listExcludedRows(st).length;
                const partAfterEx = api.partitionJudgmentGroups(st);
                return {
                  pendingCodes, statuses, holdCodes, autoCount,
                  confirmedPendingBeforeSnap: counts.confirmedPending,
                  confirmedAfterSnap: counts.confirmed,
                  confirmedDone: counts.confirmedDone,
                  afterStatuses,
                  separateBack,
                  htmlHasPending: htmlPending.includes('反映待ち'),
                  htmlHasAction: htmlPending.includes('まとめる') && htmlPending.includes('色別に分けて登録'),
                  reselectHasPanel: htmlReselect.includes('対応の再選択モード') && htmlReselect.includes('confirmed-reselect-panel'),
                  reselectHasBtns: htmlReselect.includes('data-pl-sku-dec="M1"') && htmlReselect.includes('data-dec="color_split"'),
                  reselectHasBlurbs: htmlReselect.includes('登録先は1件') || htmlReselect.includes('色ごとに分けて'),
                  m1PendingAfterReselect: m1Pending,
                  m1StatusAfterReselect: m1Status,
                  splitHasInternal: splitHtml.includes('C1-緑') && splitHtml.includes('C1-蛍光グリーン'),
                  splitHasMaker: splitHtml.includes('メーカー品番') || splitHtml.includes('C1'),
                  sectionHasConfirmed: sectionHtml.includes('確認済み一覧'),
                  sectionHasBulkLabel: sectionHtml.includes('確認して一括登録（保留・登録しない項目を除く）'),
                  sectionNoApprovedSub: !sectionHtml.includes('承認済み（未登録） ${'),
                  backToList, leftoverLabels: leftover.map(x=>x.label),
                  excl, confirmedNoHold: !partAfterEx.confirmed.some(g=>g.code==='H1'),
                  autoNotConfirmed: !partAfterEx.confirmed.some(g=>g.code==='A1'),
                  labels: {
                    merge: api.chosenActionLabel('merge'),
                    pending: api.CONFIRMED_STATUS.pending,
                    confirmed: api.CONFIRMED_STATUS.confirmed,
                    colorPending: api.pendingDecisionLabel('color_split'),
                  },
                  defaults: api.SECTION_DEFAULTS.confirmed,
                };
              }
            ''',
        }
        out = run_cases(cases)['selectThenRematch']
        self.assertEqual(sorted(out['pendingCodes']), ['B1', 'C1', 'M1', 'S1'])
        self.assertEqual(out['statuses']['M1'], 'pending')
        self.assertEqual(out['statuses']['C1'], 'pending')
        self.assertEqual(out['holdCodes'], ['H1'])
        self.assertEqual(out['autoCount'], 1)
        self.assertTrue(out['htmlHasPending'])
        self.assertTrue(out['htmlHasAction'])
        self.assertTrue(out['reselectHasPanel'])
        self.assertTrue(out['reselectHasBtns'])
        self.assertTrue(out['reselectHasBlurbs'])
        self.assertTrue(out['m1PendingAfterReselect'])
        self.assertEqual(out['m1StatusAfterReselect'], 'pending')
        self.assertEqual(out['afterStatuses']['M1'], 'confirmed')
        self.assertEqual(out['afterStatuses']['C1'], 'confirmed')
        self.assertEqual(out['afterStatuses']['B1'], 'confirmed')
        # 個別整理は approach 未入力のため再照合後は確認リストへ戻る
        self.assertIn('S1', out['separateBack'])
        self.assertNotIn('S1', out['afterStatuses'])
        self.assertTrue(out['splitHasInternal'])
        self.assertTrue(out['sectionHasConfirmed'])
        self.assertTrue(out['sectionHasBulkLabel'])
        self.assertIn('C1', out['backToList'])
        self.assertTrue(any('行き先' in x or '未作成' in x for x in out['leftoverLabels']))
        self.assertEqual(out['excl'], 1)
        self.assertTrue(out['confirmedNoHold'])
        self.assertTrue(out['autoNotConfirmed'])
        self.assertEqual(out['labels']['merge'], 'まとめる')
        self.assertEqual(out['labels']['pending'], '反映待ち')
        self.assertEqual(out['labels']['confirmed'], '確認済み')
        self.assertEqual(out['labels']['colorPending'], '色別分割案・未反映')
        self.assertTrue(out['defaults'])


if __name__ == '__main__':
    unittest.main()
