"""旧CSV判断一覧の3区分UX（UIのみ。登録・在庫ミューテーションなし）。"""
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
import stock  # noqa: E402

JS = ROOT / 'static' / 'product_ledger_csv.js'
CSS = ROOT / 'static' / 'style.css'


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64_from_text(text: str, encoding='utf-8-sig') -> str:
    import base64
    return base64.b64encode(text.encode(encoding)).decode('ascii')


def tiny_ledger_rows(rows):
    header = '顧客,仕入先,商品コード,商品名,サイズ'
    return '\n'.join([header] + rows)


NODE_HARNESS = r'''
const fs = require('fs');
const vm = require('vm');
const jsPath = process.argv[2];
const casesPath = process.argv[3];
const code = fs.readFileSync(jsPath, 'utf8');
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const sandbox = {
  window: {},
  document: { getElementById() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; } },
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
for (const [name, st] of Object.entries(cases)) {
  out[name] = {
    partition: api.partitionJudgmentGroups(st),
    counts: api.judgmentStatusCounts(st),
    labels: {
      merge: api.decisionLabel('merge'),
      hold: api.decisionLabel('hold'),
      separate: api.decisionLabel('separate'),
      none: api.decisionLabel(''),
    },
    sectionDefaults: api.SECTION_DEFAULTS,
  };
  // 折りたたみだけでは承認されない: sectionOpen を変えても partition は decision 依存
  const closed = JSON.parse(JSON.stringify(st));
  closed.sectionOpen = { individual: false, bulk: false, auto: false, hold: false };
  const open = JSON.parse(JSON.stringify(st));
  open.sectionOpen = { individual: true, bulk: true, auto: true, hold: true };
  const pClosed = api.partitionJudgmentGroups(closed);
  const pOpen = api.partitionJudgmentGroups(open);
  out[name].collapseDoesNotApprove =
    pClosed.bulkUnapproved.length === pOpen.bulkUnapproved.length &&
    pClosed.approved.length === pOpen.approved.length &&
    JSON.stringify(pClosed.bulkUnapproved.map(g => g.code).sort()) ===
      JSON.stringify(pOpen.bulkUnapproved.map(g => g.code).sort());
}
process.stdout.write(JSON.stringify(out));
'''


class JudgmentListSectionsUiSourceTest(unittest.TestCase):
    def test_three_sections_and_bulk_approve_wiring(self):
        js = JS.read_text(encoding='utf-8')
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('確認リスト', js)
        self.assertIn('buildUnifiedReviewItems', js)
        self.assertIn('一括承認候補', js)
        self.assertIn('自動決定済み', js)
        self.assertIn('plSkuBulkApproveSection', js)
        self.assertIn('一括承認候補${part.bulkUnapproved.length}件を承認', js)
        self.assertIn('data-pl-section-toggle', js)
        self.assertIn('SECTION_DEFAULTS', js)
        self.assertIn('unified:true', js)
        self.assertIn('individual:true', js)
        self.assertIn('bulk:false', js)
        self.assertIn('auto:false', js)
        self.assertIn('sourceRows:false', js)
        self.assertIn('partitionJudgmentGroups', js)
        self.assertIn('judgmentStatusCounts', js)
        self.assertIn('承認済み（未登録）', js)
        self.assertIn('未解決', js)
        self.assertIn('確認事項', js)
        self.assertIn('未承認', js)
        self.assertIn('【承認対象スコープ】', js)
        self.assertIn('元行一覧（詳細確認用）', js)
        self.assertIn('reviewTier', js)
        self.assertIn('needsDecision', js)
        # 色ではなく state で分類するコメント／実装
        self.assertIn('背景色では判定しない', js)
        self.assertIn("tier==='bulk'", js)
        self.assertIn("dec==='merge'", js)
        # 折りたたみ≠承認
        self.assertIn('折りたたみだけでは承認されません', js)
        self.assertIn('開閉は選択・編集を消さない', js)
        # 旧フラット一括（選択ベース）ボタンは区分一括へ置換
        self.assertNotIn('id="plSkuBulkMerge"', js)
        self.assertNotIn('自動まとめ（要確認ではない）', js)
        self.assertNotIn('まとめる選択済み（未登録）', js)
        # CSS
        self.assertIn('sku-judgment-section', css)
        self.assertIn('sku-section-individual', css)
        self.assertIn('sku-section-unified', css)
        self.assertIn('sku-section-bulk', css)
        self.assertIn('sku-section-auto', css)
        self.assertIn('sku-section-source-rows', css)

    def test_commit_confirm_lists_section_counts(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('【判断区分の件数】', js)
        self.assertIn('judgmentStatusCounts(st)', js)
        self.assertIn('保留・対象外', js)


class JudgmentListSectionsPartitionTest(unittest.TestCase):
    """Node で partition / counts を検証（DBミューテーションなし）。"""

    def test_partition_by_state_not_color(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入X,B2,同じシャツ,M',
                '顧客A,仕入Y,B2,同じシャツ,L',
                '顧客A,仕入A,I1,帽子,M',
                '顧客A,仕入A,I1,スナッパー加工,L',
                '顧客A,仕入A,A1,パンツ,M',
                '顧客A,仕入A,A1,パンツ,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 't.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            groups = preview['skuGroups']
            by_code = {g['code']: g for g in groups}
            self.assertEqual(by_code['B2']['reviewTier'], 'bulk')
            self.assertTrue(by_code['B2']['needsDecision'])
            self.assertEqual(by_code['I1']['reviewTier'], 'individual')
            self.assertTrue(by_code['I1']['needsDecision'])
            self.assertFalse(by_code['A1']['needsDecision'])

            initial = {
                'preview': {
                    'skuGroups': groups,
                    'autoMergePlans': preview.get('autoMergePlans') or [],
                    'plannedCounts': preview.get('plannedCounts') or {},
                    'outcomeSummary': preview.get('outcomeSummary') or {},
                },
                'skuDecisions': {},
                'sectionOpen': {'individual': True, 'bulk': False, 'auto': False, 'hold': False},
            }
            after_bulk = {
                **initial,
                'skuDecisions': {'B2': {'decision': 'merge'}},
            }
            after_indiv = {
                **initial,
                'skuDecisions': {
                    'B2': {'decision': 'merge'},
                    'I1': {'decision': 'hold', 'deferReason': '確認中'},
                },
            }
            cases = {
                'initial': initial,
                'after_bulk': after_bulk,
                'after_indiv': after_indiv,
            }
            cases_path = Path(folder.name) / 'cases.json'
            harness_path = Path(folder.name) / 'harness.js'
            cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
            harness_path.write_text(NODE_HARNESS, encoding='utf-8')
            proc = subprocess.run(
                ['node', str(harness_path), str(JS), str(cases_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            out = json.loads(proc.stdout)

            init = out['initial']
            self.assertTrue(init['collapseDoesNotApprove'])
            self.assertEqual(init['sectionDefaults']['individual'], True)
            self.assertEqual(init['sectionDefaults']['bulk'], False)
            self.assertEqual(init['sectionDefaults']['auto'], False)
            self.assertEqual(init['labels']['merge'], '承認済み（未登録）')
            self.assertIn('I1', [g['code'] for g in init['partition']['individual']])
            self.assertIn('B2', [g['code'] for g in init['partition']['bulkUnapproved']])
            self.assertEqual(init['counts']['unresolved'], 1)
            self.assertEqual(init['counts']['unapproved'], 1)
            self.assertEqual(init['counts']['approved'], 0)
            self.assertGreaterEqual(init['counts']['autoDecided'], 1)

            ab = out['after_bulk']
            self.assertNotIn('B2', [g['code'] for g in ab['partition']['bulkUnapproved']])
            self.assertIn('B2', [g['code'] for g in ab['partition']['approved']])
            self.assertEqual(ab['counts']['unapproved'], 0)
            self.assertEqual(ab['counts']['approved'], 1)
            # 個別確認は一括承認後も未解決のまま
            self.assertIn('I1', [g['code'] for g in ab['partition']['individual']])

            ai = out['after_indiv']
            self.assertNotIn('I1', [g['code'] for g in ai['partition']['individual']])
            self.assertIn('I1', [g['code'] for g in ai['partition']['hold']])
            self.assertEqual(ai['counts']['hold'], 1)
            self.assertEqual(ai['counts']['unresolved'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_bulk_approve_scope_excludes_individual_in_source(self):
        js = JS.read_text(encoding='utf-8')
        # ハンドラが bulkUnapproved のみを対象にし、確認リスト未解決を混ぜない
        self.assertIn('const bulk=part.bulkUnapproved.slice()', js)
        self.assertIn('確認リストの未解決', js)
        self.assertIn('は含めません', js)
        idx = js.find('plSkuBulkApproveSection')
        self.assertGreater(idx, 0)
        chunk = js[idx:idx + 1800]
        self.assertIn('bulkUnapproved', chunk)
        self.assertNotIn("reviewTier==='individual')individual.push", chunk)


class JudgmentListSectionsPreviewRegressionTest(unittest.TestCase):
    """プレビュー判定ロジック自体は変えていないことの軽い回帰。"""

    def test_tiers_unchanged_for_sample_rows(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入X,B2,同じシャツ,M',
                '顧客A,仕入Y,B2,同じシャツ,L',
            ])
            before = {
                'products': c.execute('select count(*) from products').fetchone()[0],
            }
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 't.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            after = {
                'products': c.execute('select count(*) from products').fetchone()[0],
            }
            self.assertEqual(before, after)
            g = next(x for x in preview['skuGroups'] if x['code'] == 'B2')
            self.assertEqual(g['reviewTier'], 'bulk')
            self.assertTrue(g['needsDecision'])
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
