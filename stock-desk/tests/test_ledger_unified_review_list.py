"""旧CSV確認画面: 個別確認＋行要確認の統合リスト（UI集約。登録・在庫ミューテーションなし）。"""
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
  const items = api.buildUnifiedReviewItems(st);
  const counts = api.judgmentStatusCounts(st);
  const unified = api.unifiedReviewCounts(st);
  const part = api.partitionJudgmentGroups(st);
  out[name] = {
    itemIds: items.map(i => i.id),
    itemKinds: items.map(i => i.kind),
    itemLabels: items.map(i => i.targetLabel),
    openIssueLabels: items.map(i => (i.issues || []).filter(x => !x.resolved).map(x => x.label)),
    unresolved: counts.unresolved,
    issueCount: counts.issueCount,
    unapproved: counts.unapproved,
    individualSku: counts.individualSku,
    unifiedUnresolved: unified.unresolved,
    blocked: api.productsRegisterBlocked(st),
    noDuplicateSkuAndRow: (() => {
      const skuCodes = new Set(items.filter(i => i.kind === 'sku').map(i => String(i.code || '')));
      return !items.some(i => i.kind === 'sku_row' && skuCodes.has(String(i.code || '')));
    })(),
    bulkLen: part.bulkUnapproved.length,
    sectionDefaults: api.SECTION_DEFAULTS,
  };
}
process.stdout.write(JSON.stringify(out));
'''


class UnifiedReviewListSourceTest(unittest.TestCase):
    def test_ui_wiring(self):
        js = JS.read_text(encoding='utf-8')
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('buildUnifiedReviewItems', js)
        self.assertIn('unifiedReviewCounts', js)
        self.assertIn('productsRegisterBlocked', js)
        self.assertIn('確認リスト（統合）', js)
        self.assertIn('元行一覧（詳細確認用）', js)
        self.assertIn('確認事項', js)
        self.assertIn('未解決が0で、必要な承認も完了すれば登録可能', js)
        self.assertIn('data-pl-unified-expand', js)
        self.assertIn('sourceRows:false', js)
        self.assertIn('sku-section-unified', css)
        self.assertIn('sku-section-source-rows', css)
        # 第二の作業リストにしない
        self.assertIn('確認作業の第二リストではありません', js)
        # チェックだけで承認しない（既存）
        self.assertIn('チェックだけでは検索・再照合・承認・登録を開始しない', js)


class UnifiedReviewListAggregationTest(unittest.TestCase):
    def test_sku_and_row_issues_merge_without_duplicate(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                # 品番個別確認（色差）
                '顧客A,仕入A,OV1,シャツ黒,M',
                '顧客A,仕入A,OV1,シャツ紺,L',
                # 行要確認のみ（括弧複数色）— 品番比較待ちではない
                '顧客A,仕入A,X1,シャツ（黒・白）,M',
                # 一括承認候補（仕入先差のみ）
                '顧客A,仕入X,B2,同じシャツ,M',
                '顧客A,仕入Y,B2,同じシャツ,L',
                # 品番なし部品（別 matchGroup → 別対象）
                '顧客A,仕入B,,生地ロール赤,1',
                '顧客A,仕入B,,ボタン青,1',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 't.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            before_products = c.execute('select count(*) from products').fetchone()[0]

            st = {
                'preview': {
                    'skuGroups': preview.get('skuGroups') or [],
                    'rows': preview.get('rows') or [],
                    'autoMergePlans': preview.get('autoMergePlans') or [],
                    'plannedCounts': preview.get('plannedCounts') or {},
                    'outcomeSummary': preview.get('outcomeSummary') or {},
                    'reviewCount': preview.get('reviewCount') or 0,
                    'skuDecisionPending': preview.get('skuDecisionPending') or 0,
                },
                'skuDecisions': {},
                'rowClassifications': {},
                'selections': {},
                'selectedCodes': {},
                'expandedCodes': {},
                'expandedUnified': {},
                'sectionOpen': dict(
                    unified=True, individual=True, bulk=False, auto=False, hold=False, sourceRows=False
                ),
            }
            # 括弧確認を一部解決しても他対象が消えないことのケース
            x1 = next(r for r in preview['rows'] if (r.get('csv') or {}).get('code') == 'X1')
            st_partial = json.loads(json.dumps(st))
            st_partial['rowClassifications'] = {
                str(x1['rowNo']): {'confirmed': True, 'acceptProposal': True, 'userEdited': True}
            }

            cases = {'base': st, 'x1_confirmed': st_partial}
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

            base = out['base']
            self.assertTrue(base['noDuplicateSkuAndRow'])
            self.assertIn('sku:OV1', base['itemIds'])
            self.assertTrue(any('X1' in lab for lab in base['itemLabels']), base['itemLabels'])
            self.assertEqual(base['unresolved'], base['unifiedUnresolved'])
            # 未解決は統合対象数。一括承認は別カウント
            self.assertEqual(base['unapproved'], 1)
            self.assertGreaterEqual(base['individualSku'], 1)
            self.assertTrue(base['blocked'])
            self.assertEqual(base['sectionDefaults']['unified'], True)
            self.assertEqual(base['sectionDefaults']['sourceRows'], False)
            # 品番なしは名称が似ていても別キー（まとめない）
            blank_kinds = [k for k, kind in zip(base['itemIds'], base['itemKinds']) if kind == 'blank']
            # 今回の blank 行は needs_review でなければ 0 件でもよい
            self.assertEqual(len(set(blank_kinds)), len(blank_kinds))

            # X1 を確認済みにしても再照合前は残るが、括弧確認は外れ「再照合して反映」のみ。OV1 は消えない
            partial = out['x1_confirmed']
            self.assertTrue(any(i == 'sku:OV1' for i in partial['itemIds']), partial['itemIds'])
            self.assertTrue(any('X1' in lab for lab in partial['itemLabels']), partial['itemLabels'])
            self.assertEqual(partial['unresolved'], base['unresolved'])
            x1_issues = next(
                labs for lab, labs in zip(partial['itemLabels'], partial['openIssueLabels'])
                if 'X1' in lab
            )
            self.assertTrue(any('再照合' in x for x in x1_issues), x1_issues)
            self.assertFalse(any('括弧' in x for x in x1_issues), x1_issues)

            after_products = c.execute('select count(*) from products').fetchone()[0]
            self.assertEqual(before_products, after_products)
        finally:
            c.close()
            folder.cleanup()

    def test_register_blocked_until_unified_and_bulk_clear(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入X,B2,同じシャツ,M',
                '顧客A,仕入Y,B2,同じシャツ,L',
                '顧客A,仕入A,I1,帽子,M',
                '顧客A,仕入A,I1,スナッパー加工,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 't.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            base = {
                'preview': {
                    'skuGroups': preview.get('skuGroups') or [],
                    'rows': preview.get('rows') or [],
                    'autoMergePlans': preview.get('autoMergePlans') or [],
                    'plannedCounts': preview.get('plannedCounts') or {},
                    'outcomeSummary': preview.get('outcomeSummary') or {},
                },
                'skuDecisions': {},
                'rowClassifications': {},
                'selections': {},
                'sectionOpen': {'unified': True, 'bulk': False, 'auto': False, 'hold': False, 'sourceRows': False},
            }
            after_bulk = {
                **json.loads(json.dumps(base)),
                'skuDecisions': {'B2': {'decision': 'merge'}},
            }
            after_all = {
                **json.loads(json.dumps(base)),
                'skuDecisions': {
                    'B2': {'decision': 'merge'},
                    'I1': {'decision': 'merge'},
                },
            }
            cases = {'base': base, 'after_bulk': after_bulk, 'after_all': after_all}
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
            self.assertTrue(out['base']['blocked'])
            self.assertTrue(out['after_bulk']['blocked'])  # I1 未解決
            self.assertEqual(out['after_bulk']['unapproved'], 0)
            self.assertGreaterEqual(out['after_bulk']['unresolved'], 1)
            self.assertFalse(out['after_all']['blocked'])
            self.assertEqual(out['after_all']['unresolved'], 0)
            self.assertEqual(out['after_all']['unapproved'], 0)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
