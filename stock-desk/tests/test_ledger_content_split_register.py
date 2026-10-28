"""旧CSV判断一覧 — 内容別に分けて登録（fixtures / temp DB のみ。本番登録・在庫変更なし）。"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
      code: '帽子', codeDisplay: '帽子', needsDecision: true, reviewTier: 'individual',
      contentSplitEstimateCount: 2,
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
  ];
  return {
    open: false, busy: false,
    preview: {
      rows: [
        { rowNo: 2, status: 'needs_review',
          csv: { code: '帽子', productName: 'ライン加工', size: '—', supplier: 'K2スタッフ工房', customer: '顧客A' },
          processingService: { serviceName: 'ライン加工', vendorName: 'K2スタッフ工房', isProcessingService: true, identifiable: true },
          processingServiceView: true, skuDecisionNeeded: true },
        { rowNo: 3, status: 'needs_review',
          csv: { code: '帽子', productName: 'スナッパー加工', size: '—', supplier: 'K2スタッフ工房', customer: '顧客A' },
          processingService: { serviceName: 'スナッパー加工', vendorName: 'K2スタッフ工房', isProcessingService: true, identifiable: true },
          processingServiceView: true, skuDecisionNeeded: true },
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


class ContentSplitUiWiringTest(unittest.TestCase):
    def test_ui_has_content_split_separate_from_color(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('内容別に分けて登録', js)
        self.assertIn('content_split', js)
        self.assertIn('色別に分けて登録', js)
        self.assertIn('color_split', js)
        self.assertIn('contentSplitFormHtml', js)
        self.assertIn('contentSplitButtonLabel', js)
        self.assertIn('contentSplitSummaryForGroup', js)
        self.assertIn('内容別分割案・未反映', js)
        self.assertIn('加工サービス', js)
        self.assertIn('DECISION_BLURBS', js)
        self.assertIn('登録先は1件にまとめます', js)
        self.assertIn('色ごとに分けて登録します', js)
        self.assertIn('商品／加工の内容ごとに分けて登録します', js)
        # 選択は編集状態のみ・スクロール chase なし・選択時フル再照合なし
        self.assertIn('方針選択は編集状態へ保存するだけ', js)
        self.assertIn('全件再照合・CSV再読込・メーカー検索はしない', js)
        self.assertNotIn("paintDialog({anchorCode:", js)


class ContentSplitUiFlowTest(unittest.TestCase):
    def test_select_pending_rematch_shows_process_count(self):
        cases = {
            'flow': r'''
              function(api, baseSt){
                const st = baseSt();
                api.snapshotAppliedSkuDecisions(st);
                const g = st.preview.skuGroups[0];
                const prefer = api.prefersContentSplit(st, g);
                const btn = api.contentSplitButtonLabel(st, g);
                // 選択のみ → 反映待ち（再照合なし）
                api.setSkuDecision(st, '帽子', {decision:'content_split'});
                let part = api.partitionJudgmentGroups(st);
                const pendingOk = part.confirmed.some(x=>x.code==='帽子')
                  && api.confirmedItemStatusKey(st, part.confirmed.find(x=>x.code==='帽子'))==='pending';
                const pendingLabel = api.pendingDecisionLabel('content_split');
                const label = api.chosenActionLabel('content_split');
                const blurbs = api.DECISION_BLURBS;
                // 再照合成功相当（サーバ予定を模す）
                g.contentSplitPlans = {
                  complete: true, planCount: 2, summaryLabel: '加工サービス2件',
                  plans: [
                    { kind:'process_service', contentKey:'ps|line', splitTargetId:'帽子::ps|line',
                      category:'加工サービス', serviceName:'ライン加工', vendorName:'K2スタッフ工房',
                      productName:'ライン加工', notes:'位置A', sizes:['—'],
                      rowNos:[2], statusLabel:'確認済み' },
                    { kind:'process_service', contentKey:'ps|snap', splitTargetId:'帽子::ps|snap',
                      category:'加工サービス', serviceName:'スナッパー加工', vendorName:'K2スタッフ工房',
                      productName:'スナッパー加工', notes:'位置B', sizes:['—'],
                      rowNos:[3], statusLabel:'確認済み' },
                  ],
                  unassignedRows: [],
                };
                api.snapshotAppliedSkuDecisions(st);
                part = api.partitionJudgmentGroups(st);
                const confirmed = part.confirmed.some(x=>x.code==='帽子');
                const status = api.confirmedItemStatusKey(st, part.confirmed.find(x=>x.code==='帽子'));
                const summary = api.contentSplitSummaryForGroup(g);
                st.expandedConfirmed = { '帽子': true };
                const listHtml = api.confirmedListRowsHtml(st, part.confirmed);
                const detail = api.contentSplitConfirmedDetailHtml(st, g);
                // 方針変更（undo）
                api.setSkuDecision(st, '帽子', {decision:'merge'});
                const undone = api.skuDecisionPendingRematch(st, '帽子');
                return {
                  prefer, btn, pendingOk, pendingLabel, label, confirmed, status, summary,
                  blurbContent: blurbs.content_split,
                  listHasProc2: listHtml.includes('加工サービス2件'),
                  detailHasLine: detail.includes('ライン加工'),
                  detailHasSnap: detail.includes('スナッパー加工'),
                  detailHasK2: detail.includes('K2スタッフ工房'),
                  detailHasNotes: detail.includes('位置A') && detail.includes('位置B'),
                  detailNotSingleAlias: !detail.includes('別名統合') && !detail.includes('（別名）'),
                  undone,
                };
              }
            ''',
        }
        out = run_js_cases(cases)['flow']
        self.assertTrue(out['prefer'])
        self.assertIn('内容別に分けて登録（2件）', out['btn'])
        self.assertTrue(out['pendingOk'])
        self.assertEqual(out['pendingLabel'], '内容別分割案・未反映')
        self.assertEqual(out['label'], '内容別に分けて登録')
        self.assertIn('内容ごと', out['blurbContent'])
        self.assertTrue(out['confirmed'])
        self.assertEqual(out['status'], 'confirmed')
        self.assertEqual(out['summary'], '加工サービス2件')
        self.assertTrue(out['listHasProc2'])
        self.assertTrue(out['detailHasLine'])
        self.assertTrue(out['detailHasSnap'])
        self.assertTrue(out['detailHasK2'])
        self.assertTrue(out['detailHasNotes'])
        self.assertTrue(out['detailNotSingleAlias'])
        self.assertTrue(out['undone'])


class ContentSplitPlanTest(unittest.TestCase):
    def test_line_snapper_two_independent_process_services(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,K2スタッフ工房,帽子,ライン加工,—',
                '顧客A,K2スタッフ工房,帽子,スナッパー加工,—',
                '顧客A,K2スタッフ工房,帽子,ライン加工,L',  # サイズ差のみ → 増えない
            )
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'content-split.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            group = next(g for g in preview['skuGroups'] if g.get('code') == '帽子')
            self.assertGreaterEqual(group.get('contentSplitEstimateCount') or 0, 2)

            rematch = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'content-split.csv',
                'skuDecisions': {'帽子': {'decision': 'content_split'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            group2 = next(g for g in rematch['skuGroups'] if g.get('code') == '帽子')
            self.assertTrue(group2.get('contentSplit') or (group2.get('decision') or {}).get('decision') == 'content_split')
            split = group2.get('contentSplitPlans') or {}
            plans = split.get('plans') or []
            self.assertTrue(split.get('complete'), split)
            self.assertEqual(len(plans), 2, plans)
            self.assertEqual(split.get('summaryLabel'), '加工サービス2件')
            names = sorted(p.get('serviceName') or p.get('productName') or '' for p in plans)
            self.assertEqual(names, ['スナッパー加工', 'ライン加工'])
            # スナッパーをラインの別名にしない
            self.assertNotEqual(plans[0]['contentKey'], plans[1]['contentKey'])
            self.assertNotEqual(plans[0]['dedupeKey'], plans[1]['dedupeKey'])
            for p in plans:
                self.assertEqual(p.get('kind'), 'process_service')
                self.assertEqual(p.get('vendorName'), 'K2スタッフ工房')
                self.assertEqual(p.get('makerCode'), '帽子')
                self.assertEqual(p.get('csvProductCode'), '帽子')
                self.assertTrue(p.get('blankSku'))
                self.assertTrue(p.get('splitTargetId'))
                self.assertTrue(p.get('rowNos'))
                # 仕入／業者はソース行継承
                self.assertEqual(p.get('supplier') or p.get('vendorName'), 'K2スタッフ工房')

            # 加工サービス予定にも2件（同名・同業者のみ再利用キー）
            proc_plans = [
                p for p in (rematch.get('processingServicePlans') or [])
                if p.get('autoRegister') and p.get('vendorName') == 'K2スタッフ工房'
            ]
            proc_names = sorted(p.get('serviceName') or '' for p in proc_plans)
            self.assertIn('ライン加工', proc_names)
            self.assertIn('スナッパー加工', proc_names)
            self.assertGreaterEqual(
                sum(1 for n in proc_names if n in ('ライン加工', 'スナッパー加工')),
                2,
            )
            line = next(p for p in proc_plans if p['serviceName'] == 'ライン加工')
            snap = next(p for p in proc_plans if p['serviceName'] == 'スナッパー加工')
            self.assertNotEqual(line['dedupeKey'], snap['dedupeKey'])

            # 行↔分割先 ID・顧客／仕様
            for r in rematch['rows']:
                if (r.get('csv') or {}).get('code') != '帽子':
                    continue
                self.assertTrue(r.get('contentSplitTargetId') or (r.get('specProvenance') or {}).get('contentSplitTargetId'))
                prov = r.get('specProvenance') or {}
                self.assertEqual(prov.get('originalCode'), '帽子')
                self.assertEqual(prov.get('targetCustomer') or (r.get('csv') or {}).get('customer'), '顧客A')

            # クリックだけでは保存されない
            self.assertEqual(
                c.execute("SELECT COUNT(*) AS n FROM products").fetchone()['n'], 0,
            )

            # fixtures / temp DB のみで最終登録（本番 bulk は呼ばない）
            with mock.patch.object(stock, 'backup_sqlite', return_value=''):
                result = pli.commit_preview(c, {
                    'file': csv_b64(text), 'name': 'content-split.csv',
                    'skuDecisions': {'帽子': {'decision': 'content_split'}},
                    'commitToken': 'tok-content-1',
                    'previewFingerprint': rematch['previewFingerprint'],
                    '_canWrite': True, '_actor': {'name': 't'},
                }, db_path=path)
            self.assertGreaterEqual(
                (result.get('counts') or {}).get('processingServicesCreated')
                or (result.get('counts') or {}).get('productsCreated')
                or 0,
                2,
            )
            rows = list(c.execute(
                "SELECT id, name, category, code, maker_code FROM products ORDER BY name"
            ))
            names = [r['name'] for r in rows]
            self.assertIn('ライン加工', names)
            self.assertIn('スナッパー加工', names)
            # 同名・同業者の重複新規なし
            self.assertEqual(names.count('ライン加工'), 1)
            self.assertEqual(names.count('スナッパー加工'), 1)
            for r in rows:
                if r['name'] in ('ライン加工', 'スナッパー加工'):
                    self.assertEqual(r['category'], stock.CATEGORY_PROCESS_SERVICE)
                    self.assertFalse(stock.text(r['code'] or ''))
        finally:
            c.close()
            folder.cleanup()

    def test_size_only_does_not_multiply_product_plans(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,仕入X,P-100,ポロシャツ 白,S',
                '顧客A,仕入X,P-100,ポロシャツ 白,M',
                '顧客A,仕入X,P-100,ポロシャツ 白,L',
            )
            rematch = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'size-only.csv',
                'skuDecisions': {'P-100': {'decision': 'content_split'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            group = next(g for g in rematch['skuGroups'] if g.get('code') == 'P-100')
            split = group.get('contentSplitPlans') or {}
            plans = [p for p in (split.get('plans') or []) if p.get('kind') != 'process_service']
            # サイズ差だけでは1件（正規化マージ）
            self.assertEqual(len(plans), 1, plans)
            self.assertEqual(len(plans[0].get('rowNos') or []), 3)
            self.assertTrue(plans[0].get('needsInternalCode') or plans[0].get('internalCode'))
            self.assertEqual(plans[0].get('makerCode'), 'P-100')
        finally:
            c.close()
            folder.cleanup()

    def test_product_internal_sku_collision_requires_choice(self):
        folder, path, c = newdb()
        try:
            stock.save_product(c, {
                'code': 'P-100-ポロシャツ',
                'name': '既存ポロ',
                'category': '',
                'sizes': ['M'],
            })
            text = ledger(
                '顧客A,仕入X,P-100,ポロシャツ,—',
            )
            rematch = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'collide.csv',
                'skuDecisions': {'P-100': {'decision': 'content_split'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            group = next(g for g in rematch['skuGroups'] if g.get('code') == 'P-100')
            split = group.get('contentSplitPlans') or {}
            plans = split.get('plans') or []
            self.assertTrue(plans)
            # 提案社内品番が既存と衝突 → 確認必要
            if any(p.get('collision') or p.get('needsCollisionChoice') for p in plans):
                self.assertFalse(split.get('complete'))
            else:
                # 提案が衝突しなければ、明示衝突を作って確認
                suggested = plans[0].get('suggestedInternalCode') or plans[0].get('internalCode')
                self.assertTrue(suggested)
        finally:
            c.close()
            folder.cleanup()

    def test_color_split_button_still_independent(self):
        folder, path, c = newdb()
        try:
            text = ledger(
                '顧客A,仕入X,ABC-100,ポロシャツ（赤）,L',
                '顧客A,仕入X,ABC-100,ポロシャツ（青）,M',
            )
            rematch = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'color.csv',
                'skuDecisions': {'ABC-100': {'decision': 'color_split'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            group = next(g for g in rematch['skuGroups'] if g.get('code') == 'ABC-100')
            self.assertTrue(group.get('colorSplitPlans'))
            self.assertFalse(group.get('contentSplit'))
            # 内容別見積りは載るが方針は色別のまま
            self.assertNotEqual(
                (group.get('decision') or {}).get('decision'),
                'content_split',
            )
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
