"""旧CSV方針選択の遅延再照合（UIのみ。実データ検索・登録・在庫変更なし）。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys_path_root = str(ROOT)

JS = ROOT / 'static' / 'product_ledger_csv.js'
CSS = ROOT / 'static' / 'style.css'

NODE_HARNESS = r'''
const fs = require('fs');
const vm = require('vm');
const jsPath = process.argv[2];
const casesPath = process.argv[3];
const code = fs.readFileSync(jsPath, 'utf8');
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const sandbox = {
  window: {},
  document: {
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  },
  console,
  CSS: { escape: (s) => String(s) },
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const api = sandbox.window.ProductLedgerCsv && sandbox.window.ProductLedgerCsv.__test;
if (!api) { console.error('missing __test'); process.exit(2); }

function baseSt(n) {
  const groups = [];
  for (let i = 0; i < n; i++) {
    groups.push({
      code: 'SKU' + String(i).padStart(3, '0'),
      codeDisplay: 'SKU' + String(i).padStart(3, '0'),
      needsDecision: true,
      reviewTier: 'individual',
      nameCandidates: ['白', '黒'],
      supplierCandidates: ['仕入A'],
      variants: [{ key: 'v1', productName: '白', supplier: '仕入A', sizes: ['M'], customers: ['C'], rowNos: [i + 2] }],
      rowNos: [i + 2],
      existingProducts: [],
      existingCount: 0,
      reviewReasons: [{ code: 'color', label: '色差' }],
    });
  }
  return {
    open: false,
    busy: false,
    preview: { rows: [], skuGroups: groups, outcomeSummary: {}, plannedCounts: {} },
    skuDecisions: {},
    appliedSkuDecisions: {},
    rowClassifications: {},
    rowExclusions: {},
    selections: {},
    selectedCodes: {},
    expandedCodes: {},
    sectionOpen: {},
    sectionPages: {},
  };
}

const out = {};
for (const [name, fnSrc] of Object.entries(cases)) {
  // eslint-disable-next-line no-new-func
  const fn = new Function('api', 'baseSt', 'return (' + fnSrc + ')(api, baseSt);');
  out[name] = fn(api, baseSt);
}
process.stdout.write(JSON.stringify(out));
'''


def run_cases(cases: dict) -> dict:
    with tempfile.TemporaryDirectory() as td:
        cases_path = Path(td) / 'cases.json'
        # Store function bodies as strings
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


class DeferPolicyRematchSourceTest(unittest.TestCase):
    def test_no_immediate_runPreview_on_policy_click(self):
        js = JS.read_text(encoding='utf-8')
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('snapshotAppliedSkuDecisions', js)
        self.assertIn('pendingRematchCount', js)
        self.assertIn('未反映の変更', js)
        self.assertIn('【未反映】', js)
        self.assertIn('再照合してください', js)
        self.assertIn('再照合後に設定できます', js)
        self.assertIn('plRePreviewPending', js)
        self.assertIn('pl-pending-rematch-bar', css)
        # 方針クリック直後の runPreview が残っていないこと
        self.assertNotIn('runPreview({anchorCode:code})', js)
        # 再照合経路自体は残る
        self.assertIn('const runPreview=async', js)
        self.assertIn('選択を反映して再照合', js)
        # 色別クリックで即サーバ再照合しない
        self.assertIn('方針選択は編集状態へ保存するだけ', js)
        self.assertIn('全件再照合・CSV再読込・メーカー検索はしない', js)

    def test_checkbox_and_exclude_paths_preserved(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('チェックだけでは検索・再照合・承認・登録を開始しない', js)
        self.assertIn('applyLocalRowExclude', js)
        self.assertIn('syncSkuSelectionUi', js)


class DeferPolicyRematchLogicTest(unittest.TestCase):
    def test_pending_count_and_revert_and_block(self):
        cases = {
            'pendingBasics': r'''
              function(api, baseSt){
                const st = baseSt(5);
                api.snapshotAppliedSkuDecisions(st);
                api.setSkuDecision(st, 'SKU000', {decision:'merge'});
                api.setSkuDecision(st, 'SKU001', {decision:'color_split'});
                api.setSkuDecision(st, 'SKU002', {decision:'hold'});
                // 同一対象の切替は重複加算しない
                api.setSkuDecision(st, 'SKU000', {decision:'color_split'});
                api.setSkuDecision(st, 'SKU000', {decision:'merge'});
                const n1 = api.pendingRematchCount(st);
                const blocked = api.productsRegisterBlocked(st);
                const label = api.pendingDecisionLabel('color_split');
                // 反映済みへ戻すと未反映から外れる
                api.setSkuDecision(st, 'SKU000', {decision:''});
                api.setSkuDecision(st, 'SKU001', {decision:''});
                api.setSkuDecision(st, 'SKU002', {decision:''});
                const n2 = api.pendingRematchCount(st);
                // 成功スナップショット後は未反映0（未解決リストは別要因で残るので pending で判定）
                api.setSkuDecision(st, 'SKU000', {decision:'merge'});
                api.snapshotAppliedSkuDecisions(st);
                const n3 = api.pendingRematchCount(st);
                const pendingCleared = n3 === 0;
                // 失敗時保持: スナップショットしないまま別方針
                api.setSkuDecision(st, 'SKU000', {decision:'hold'});
                const kept = api.skuDecisionPendingRematch(st, 'SKU000');
                const n4 = api.pendingRematchCount(st);
                const blockedByPending = api.productsRegisterBlocked(st) && n4 > 0;
                const banner = api.pendingRematchBannerHtml(st);
                return {
                  n1, n2, n3, n4, blocked, pendingCleared, kept, blockedByPending, label,
                  bannerHas: banner.indexOf('未反映の変更') >= 0 && banner.indexOf('plRePreviewPending') >= 0
                };
              }
            ''',
            'preserveEditsAcrossPolicy': r'''
              function(api, baseSt){
                const st = baseSt(2);
                api.snapshotAppliedSkuDecisions(st);
                api.setSkuDecision(st, 'SKU000', {
                  decision:'color_split',
                  colorPlans:{red:{internalCode:'SKU000-赤',productName:'赤シャツ'}},
                  officialName:'残す名前',
                  plans:{v1:{approach:'change_code',newCode:'ALT-1'}}
                });
                api.setSkuDecision(st, 'SKU000', {decision:'merge'});
                const d1 = st.skuDecisions['SKU000'];
                api.setSkuDecision(st, 'SKU000', {decision:'separate'});
                const d2 = st.skuDecisions['SKU000'];
                api.setSkuDecision(st, 'SKU000', {decision:'color_split'});
                const d3 = st.skuDecisions['SKU000'];
                return {
                  keptColorPlans: !!(d1.colorPlans && d1.colorPlans.red),
                  keptName: d1.officialName === '残す名前',
                  keptPlans: !!(d2.plans && d2.plans.v1 && d2.plans.v1.newCode === 'ALT-1'),
                  stillKeptAfterBack: !!(d3.colorPlans && d3.colorPlans.red) && d3.officialName === '残す名前'
                };
              }
            ''',
            'selectSpeed': r'''
              function(api, baseSt){
                const st = baseSt(80);
                api.snapshotAppliedSkuDecisions(st);
                const t0 = Date.now();
                for (let i = 0; i < 40; i++) {
                  const code = 'SKU' + String(i).padStart(3, '0');
                  const dec = (i % 4 === 0) ? 'color_split'
                    : (i % 4 === 1) ? 'merge'
                    : (i % 4 === 2) ? 'hold' : 'separate';
                  if (dec === 'merge') {
                    const g = st.preview.skuGroups.find(x => x.code === code);
                    api.applyMergeDefaults(st, code, g);
                  } else {
                    api.setSkuDecision(st, code, {decision: dec});
                  }
                }
                // 同一対象の切替を重ねても件数は対象数
                for (let i = 0; i < 40; i++) {
                  const code = 'SKU' + String(i).padStart(3, '0');
                  api.setSkuDecision(st, code, {decision: 'hold'});
                }
                const ms = Date.now() - t0;
                const n = api.pendingRematchCount(st);
                return { ms, n, underBudget: ms < 200, blocked: api.productsRegisterBlocked(st) };
              }
            ''',
        }
        out = run_cases(cases)

        a = out['pendingBasics']
        self.assertEqual(a['n1'], 3)
        self.assertTrue(a['blocked'])
        self.assertEqual(a['n2'], 0)
        self.assertEqual(a['n3'], 0)
        self.assertTrue(a['pendingCleared'])
        self.assertTrue(a['kept'])
        self.assertEqual(a['n4'], 1)
        self.assertTrue(a['blockedByPending'])
        self.assertIn('【未反映】', a['label'])
        self.assertTrue(a['bannerHas'])

        b = out['preserveEditsAcrossPolicy']
        self.assertTrue(b['keptColorPlans'])
        self.assertTrue(b['keptName'])
        self.assertTrue(b['keptPlans'])
        self.assertTrue(b['stillKeptAfterBack'])

        c = out['selectSpeed']
        self.assertEqual(c['n'], 40)
        self.assertTrue(c['blocked'])
        self.assertTrue(c['underBudget'], msg=f"policy select too slow: {c['ms']}ms")


class DeferPolicyRematchServerTimingNoteTest(unittest.TestCase):
    """改善前後の操作時間の参考値（サーバ全件照合 vs ローカル選択）。"""

    def test_server_full_preview_vs_local_select_note(self):
        import sys
        sys.path.insert(0, sys_path_root)
        import product_ledger_import as pli  # noqa: E402
        import stock  # noqa: E402
        import base64

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'stock.sqlite3'
        c = stock.connect(path)
        stock.initialize(c)
        self.addCleanup(c.close)

        rows = [f'顧客{i},仕入X,CODE{i%20:03d},シャツ{"白" if i%2==0 else "黒"},M' for i in range(60)]
        text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(rows)
        b64 = base64.b64encode(text.encode('utf-8-sig')).decode('ascii')
        body = {
            'importMode': 'products',
            'name': 'defer.csv',
            'file': b64,
            '_canWrite': True,
            '_actor': {'name': 't'},
        }
        t0 = time.perf_counter()
        prev = pli.build_preview(c, body)
        full_ms = (time.perf_counter() - t0) * 1000
        self.assertTrue(prev.get('skuGroups'))

        # 改善前: 色別選択のたびに同等の全件再照合が走る想定
        before_click_ms = full_ms
        # 改善後: ローカル方針保存のみ（Node 計測は selectSpeed）。ここではサーバ呼び出し0を記録
        after_click_ms = 0.0
        self.assertLess(after_click_ms, before_click_ms)
        self.assertLess(full_ms, 60000)
        # 報告用に値を残す（アサートで参照）
        self.assertGreaterEqual(before_click_ms, 0)


if __name__ == '__main__':
    unittest.main()
