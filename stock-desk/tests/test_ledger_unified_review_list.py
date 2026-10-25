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
        # 詳細開閉・横幅不具合の再発防止
        self.assertIn('processingUnifiedKey', js)
        self.assertIn('UNIFIED_REVIEW_COL_COUNT', js)
        self.assertNotIn(r'proc:${name}\0${vendor}', js)
        self.assertNotIn('proc:${name}\\0${vendor}', js)
        self.assertIn('encodeURIComponent', js)
        self.assertIn('colspan:UNIFIED_REVIEW_COL_COUNT', js)
        self.assertIn('unified-review-table .sku-code', css)
        # 登録予定の項目別状態表示
        self.assertIn('buildPlannedStatusFields', js)
        self.assertIn('unifiedPlanHtml', js)
        self.assertIn('自動決定済み', js)
        self.assertIn('選択が必要', js)
        self.assertIn('登録確定前の確認画面です', js)
        self.assertIn('sku-plan-field', css)
        self.assertIn('is-need-choice', css)
        self.assertIn('sku-preregister-banner', css)


EXPAND_HARNESS = r'''
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
  CSS: { escape: (s) => String(s).replace(/["\\]/g, '\\$&') },
  esc: (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
  $: () => null,
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const api = sandbox.window.ProductLedgerCsv && sandbox.window.ProductLedgerCsv.__test;
if (!api) { console.error('missing __test'); process.exit(2); }

function simulateAttrRoundTrip(id) {
  // HTML 属性は NUL で切れ、制御文字も落ちうる。getAttribute 相当の切り出しを模擬。
  const raw = String(id == null ? '' : id);
  const cut = raw.split('\0')[0];
  return cut;
}

const out = {};
for (const [name, st] of Object.entries(cases)) {
  const items = api.buildUnifiedReviewItems(st);
  const procItems = items.filter(i => i.kind === 'processing');
  const skuItems = items.filter(i => i.kind === 'sku');
  const blankItems = items.filter(i => i.kind === 'blank' || i.kind === 'row' || i.kind === 'custom' || i.kind === 'unknown' || i.kind === 'sku_row');

  // クリック相当: data 属性の往復後に expandedUnified を立てて描画
  const expandedUnified = {};
  for (const it of procItems) {
    const attr = simulateAttrRoundTrip(it.id);
    expandedUnified[attr] = true;
  }
  const stOpen = Object.assign({}, st, {
    expandedUnified,
    expandedCodes: Object.fromEntries(skuItems.map(i => [i.code, true])),
  });
  const html = api.unifiedListRowsHtml(stOpen, items);
  const detailCols = [...html.matchAll(/class="sku-detail-row[^"]*"[^>]*>\s*<td colspan="(\d+)"/g)]
    .map(m => Number(m[1]));
  const makerCols = [...html.matchAll(/class="sku-maker-lookup-row"[^>]*>\s*<td colspan="(\d+)"/g)]
    .map(m => Number(m[1]));
  const expandBtns = [...html.matchAll(/data-pl-unified-expand="([^"]*)"/g)].map(m => m[1]);
  const detailIds = [...html.matchAll(/data-unified-detail="([^"]*)"/g)].map(m => m[1]);

  out[name] = {
    itemIds: items.map(i => i.id),
    itemKinds: items.map(i => i.kind),
    itemLabels: items.map(i => i.targetLabel),
    procIds: procItems.map(i => i.id),
    procLabels: procItems.map(i => i.targetLabel),
    idsHaveNul: items.some(i => String(i.id).includes('\0')),
    idsAttrSafe: items.every(i => simulateAttrRoundTrip(i.id) === String(i.id)),
    procExpandVisible: procItems.length
      ? procItems.every(i => detailIds.includes(String(i.id)))
      : true,
    expandAttrMatchesId: expandBtns.every((a, idx) => {
      // 各ボタンの属性値が対応 item.id と一致（NUL 切れなし）
      return items.some(i => String(i.id) === a);
    }),
    detailColspans: detailCols,
    makerColspans: makerCols,
    allDetailColspan5: detailCols.length ? detailCols.every(c => c === api.UNIFIED_REVIEW_COL_COUNT) : true,
    noColspan8InUnified: !detailCols.includes(8) && !makerCols.includes(8),
    skuDetailCount: detailCols.length,
    blankOrRowCount: blankItems.length,
    keyEmptyNameElm: api.processingUnifiedKey('', '(有)エルムスコーポレーション'),
    keyEmptyNameK2: api.processingUnifiedKey('', 'K2スタッフ工房'),
    keysDistinct: api.processingUnifiedKey('', '(有)エルムスコーポレーション')
      !== api.processingUnifiedKey('', 'K2スタッフ工房'),
  };
}
process.stdout.write(JSON.stringify(out));
'''


class UnifiedReviewDetailExpandFixTest(unittest.TestCase):
    """加工の詳細開閉（NULキー切れ）と統合リスト colspan 不一致の回帰。"""

    def test_processing_expand_and_unified_colspan(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                # 通常商品・個別確認（色差）→ 109-PCT 相当の開閉＋colspan
                '顧客A,仕入A,109-PCT,パンツ黒,M',
                '顧客A,仕入A,109-PCT,パンツ紺,L',
                # エルムスは専用ルールで「刺繍」自動確定（要確認リストに出さない）
                '顧客A,(有)エルムスコーポレーション,,謎の加工行,1',
                # K2の加工名不明は自動除外（確認リストではなく「登録しない」側）
                '顧客A,K2スタッフ工房,,不明XYZ,1',
                '顧客A,K2スタッフ工房,,別の不明行,1',
                # 品番なし部品（要確認になるよう括弧色など）
                '顧客A,仕入B,,ボタン（赤・青）,1',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'expand.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            before_products = c.execute('select count(*) from products').fetchone()[0]
            # 加工サービスの詳細開閉用に、要確認の加工行を合成（NULキー切れ回帰）
            synth_proc = {
                'rowNo': 9001,
                'status': 'needs_review',
                'skuDecisionNeeded': False,
                'matchGroupKey': 'proc-synth',
                'processingServiceView': True,
                'csv': {
                    'customer': '顧客A', 'code': '', 'productName': '',
                    'size': '', 'supplier': '(有)エルムスコーポレーション',
                },
                'processingService': {
                    'isProcessingVendor': True,
                    'isProcessingService': True,
                    'serviceName': '',
                    'vendorName': '(有)エルムスコーポレーション',
                    'needsConfirm': True,
                    'missingFields': ['加工名'],
                    'rowSpecs': {},
                },
                'parenClassification': {'processingServiceView': True, 'needsConfirm': True},
                'reasons': ['加工名の確認'],
                'product': {},
                'handle': {},
            }
            rows = list(preview.get('rows') or []) + [synth_proc]

            st = {
                'preview': {
                    'skuGroups': preview.get('skuGroups') or [],
                    'rows': rows,
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
                'makerLookup': None,
                'sectionOpen': dict(
                    unified=True, individual=True, bulk=False, auto=False, hold=False, sourceRows=False
                ),
            }
            cases_path = Path(folder.name) / 'expand-cases.json'
            harness_path = Path(folder.name) / 'expand-harness.js'
            cases_path.write_text(json.dumps({'base': st}, ensure_ascii=False), encoding='utf-8')
            harness_path.write_text(EXPAND_HARNESS, encoding='utf-8')
            proc = subprocess.run(
                ['node', str(harness_path), str(JS), str(cases_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            out = json.loads(proc.stdout)['base']

            self.assertFalse(out['idsHaveNul'], out['itemIds'])
            self.assertTrue(out['idsAttrSafe'], out['itemIds'])
            self.assertTrue(out['keysDistinct'])
            self.assertTrue(out['keyEmptyNameElm'].startswith('proc:'))
            self.assertNotIn('\0', out['keyEmptyNameElm'])
            self.assertNotIn('\0', out['keyEmptyNameK2'])

            # エルムス「謎の加工行」は刺繍へ自動確定（確認リスト外・プランに載る）
            elms_row = next(
                r for r in (preview.get('rows') or [])
                if 'エルムス' in ((r.get('csv') or {}).get('supplier') or '')
            )
            elms_ps = elms_row.get('processingService') or {}
            self.assertTrue(elms_ps.get('elmsEmbroideryRule'))
            self.assertEqual(elms_ps.get('serviceName'), '刺繍')
            self.assertFalse(elms_row.get('processingServiceNeedsConfirm'))
            elms_plans = [
                p for p in (preview.get('processingServicePlans') or [])
                if p.get('elmsEmbroideryRule')
            ]
            self.assertEqual(len(elms_plans), 1)
            self.assertEqual(elms_plans[0].get('serviceName'), '刺繍')

            # 合成した加工要確認が統合リストに出る（K2不明は自動除外のためリスト外）
            proc_labs = out['procLabels']
            self.assertGreaterEqual(len(out['procIds']), 1, {
                'kinds': list(zip(out['itemKinds'], out['itemLabels'])),
                'procIds': out['procIds'],
            })
            self.assertTrue(out['procExpandVisible'], {
                'procIds': out['procIds'], 'labels': proc_labs,
            })
            # 実CSVのエルムス自動確定分は要確認ラベルに出さない。合成の要確認は業者名を含む
            self.assertTrue(any('エルムス' in lab for lab in proc_labs), proc_labs)
            self.assertFalse(any('K2' in lab for lab in proc_labs), proc_labs)

            self.assertTrue(out['allDetailColspan5'], out['detailColspans'])
            self.assertTrue(out['noColspan8InUnified'], {
                'detail': out['detailColspans'], 'maker': out['makerColspans'],
            })
            # 109-PCT の詳細が開いている
            self.assertIn('sku:109-PCT', out['itemIds'])
            self.assertGreaterEqual(out['skuDetailCount'], 1)

            after_products = c.execute('select count(*) from products').fetchone()[0]
            self.assertEqual(before_products, after_products)
        finally:
            c.close()
            folder.cleanup()


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


PLAN_STATUS_HARNESS = r'''
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
  CSS: { escape: (s) => String(s == null ? '' : s) },
  esc: (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
  $: () => null,
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const api = sandbox.window.ProductLedgerCsv && sandbox.window.ProductLedgerCsv.__test;
if (!api) { console.error('missing __test'); process.exit(2); }

function fieldMap(fields) {
  const out = {};
  for (const f of fields || []) {
    out[f.key] = { value: f.value, status: f.status, unsettled: !!f.unsettled, hint: f.hint || '' };
  }
  return out;
}

const out = {};
for (const [name, st] of Object.entries(cases)) {
  const items = api.buildUnifiedReviewItems(st);
  let sku = items.find(i => i.kind === 'sku' && String(i.code) === '72-1226')
    || items.find(i => i.kind === 'sku');
  // 色別登録などは承認済み区分へ移るため、skuGroups から直接 item を組む
  if (!sku || st.forceSkuFromGroup) {
    const g = ((st.preview && st.preview.skuGroups) || []).find(x => String(x.code) === '72-1226');
    if (g) {
      sku = {
        id: 'sku:' + g.code,
        kind: 'sku',
        code: g.code,
        targetLabel: g.codeDisplay || g.code,
        group: g,
        rows: [],
        rowNos: g.rowNos || [],
        issues: (g.reviewReasons || []).map(r => ({
          type: 'sku_reason', code: r.code || '', label: r.label || '',
          detail: (r.values || []).join(' / '), fieldKey: r.code === 'color' ? 'color' : '',
          resolved: false,
        })),
        unresolvedIssueCount: (g.reviewReasons || []).length || 1,
      };
    }
  }
  const proc = items.find(i => i.kind === 'processing') || (st.synthProcessingItem || null);
  const fields = sku ? api.buildPlannedStatusFields(st, sku) : [];
  const html = sku ? api.unifiedPlanHtml(st, sku) : '';
  const issuesHtml = sku ? api.unifiedIssuesHtml(sku) : '';
  const listHtml = api.unifiedListRowsHtml(st, items);
  const approvedHtml = (() => {
    const groups = ((st.preview && st.preview.skuGroups) || []).filter(g => {
      const d = (st.skuDecisions && st.skuDecisions[g.code]) || {};
      return (d.decision || '') === 'color_split' || (d.decision || '') === 'merge';
    });
    // skuListRowsHtml は __test に無いので unifiedPlanHtml で同等確認
    return groups.map(g => api.unifiedPlanHtml(st, {
      kind: 'sku', code: g.code, targetLabel: g.codeDisplay || g.code,
      group: g, rows: [], rowNos: g.rowNos || [], issues: [],
    })).join('\\n');
  })();
  const fmap = fieldMap(fields);
  const procFields = proc ? api.buildPlannedStatusFields(st, proc) : [];
  out[name] = {
    itemIds: items.map(i => i.id),
    skuId: sku && sku.id,
    fieldKeys: fields.map(f => f.key),
    fields: fmap,
    html,
    approvedHtml,
    hasProvisionalBlueAsDecided: /スクラブ ブルー【自動決定済み】|スクラブ ブルー【手動確定済み】/.test(html + approvedHtml),
    showsScrubCommon: /スクラブ/.test(html) && (fmap.name && fmap.name.value === 'スクラブ'),
    showsColorNeed: !!(fmap.color && fmap.color.status === api.PLAN_STATUS.NEED_CHOICE),
    showsSupplierAuto: !!(fmap.supplier && fmap.supplier.status === api.PLAN_STATUS.AUTO
      && String(fmap.supplier.value || '').includes('モンブラン')),
    showsProcessingNa: !!(fmap.processing && fmap.processing.status === api.PLAN_STATUS.NA),
    issueMapsColor: /→ 色/.test(issuesHtml),
    noCsvRowInPlan: !/CSV行/.test(html),
    listHasPlanFields: /sku-plan-field/.test(listHtml + approvedHtml),
    unsettledCount: fields.filter(f => f.unsettled).length,
    allSettledWhileUnresolved: fields.length
      ? fields.every(f => !f.unsettled) && ((sku && sku.unresolvedIssueCount) > 0)
      : false,
    procFieldKeys: procFields.map(f => f.key),
    procHasGarmentSlots: procFields.some(f => ['sku', 'name', 'color', 'supplier'].includes(f.key)),
    colorSplitHidesMergeProvisional: !!(fmap.name && !/色別登録：/.test(fmap.name.value || '')
      && fmap.name.value !== 'スクラブ ブルー'),
  };
}
process.stdout.write(JSON.stringify(out));
'''


class UnifiedPlannedStatusDisplayTest(unittest.TestCase):
    """登録予定欄の項目別状態表示（表示層のみ。登録値は変えない）。"""

    def _run(self, cases):
        folder = tempfile.TemporaryDirectory()
        try:
            cases_path = Path(folder.name) / 'plan-cases.json'
            harness_path = Path(folder.name) / 'plan-harness.js'
            cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
            harness_path.write_text(PLAN_STATUS_HARNESS, encoding='utf-8')
            proc = subprocess.run(
                ['node', str(harness_path), str(JS), str(cases_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            return json.loads(proc.stdout)
        finally:
            folder.cleanup()

    def _preview_state(self, c, text, *, sku_decisions=None, row_classifications=None):
        preview = pli.build_preview(c, {
            'importMode': 'products',
            'file': csv_b64_from_text(text),
            'name': 'plan-status.csv',
            'skuDecisions': sku_decisions or {},
            'rowClassifications': row_classifications or {},
            '_canWrite': True,
            '_actor': {'name': 't'},
        })
        return {
            'preview': {
                'skuGroups': preview.get('skuGroups') or [],
                'rows': preview.get('rows') or [],
                'autoMergePlans': preview.get('autoMergePlans') or [],
                'plannedCounts': preview.get('plannedCounts') or {},
                'outcomeSummary': preview.get('outcomeSummary') or {},
                'reviewCount': preview.get('reviewCount') or 0,
                'processingServicePlans': preview.get('processingServicePlans') or [],
            },
            'skuDecisions': sku_decisions or {},
            'rowClassifications': row_classifications or {},
            'selections': {},
            'selectedCodes': {},
            'expandedCodes': {},
            'expandedUnified': {},
            'sectionOpen': {
                'unified': True, 'individual': True, 'bulk': False,
                'auto': False, 'hold': False, 'sourceRows': False,
            },
            'busy': False,
        }

    def test_scrub_72_1226_supplier_auto_color_needs_choice(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,住商モンブラン㈱,72-1226,スクラブ ブルー,M',
                '顧客A,住商モンブラン㈱,72-1226,スクラブ ワイン,L',
            ])
            before = c.execute('select count(*) from products').fetchone()[0]
            synth_proc = {
                'id': 'proc:synth',
                'kind': 'processing',
                'targetLabel': '（加工名未定）／テスト加工店',
                'plannedName': '',
                'plannedSupplier': 'テスト加工店',
                'plannedColor': '',
                'issues': [{'type': 'processing_name', 'label': '加工名の確認', 'fieldKey': 'procName', 'resolved': False}],
                'unresolvedIssueCount': 1,
                'rows': [{
                    'rowNo': 8001,
                    'status': 'needs_review',
                    'processingServiceView': True,
                    'csv': {'supplier': 'テスト加工店', 'productName': '', 'code': ''},
                    'processingService': {
                        'serviceName': '', 'vendorName': 'テスト加工店',
                        'needsConfirm': True, 'missingFields': ['加工名'], 'rowSpecs': {},
                    },
                    'parenClassification': {},
                }],
                'rowNos': [8001],
                'code': '',
            }
            st = self._preview_state(c, text)
            st['synthProcessingItem'] = synth_proc
            st_split = self._preview_state(
                c, text, sku_decisions={'72-1226': {'decision': 'color_split'}}
            )
            st_split['forceSkuFromGroup'] = True
            st_manual = self._preview_state(
                c, text,
                sku_decisions={
                    '72-1226': {
                        'decision': 'merge',
                        'officialName': 'スクラブ',
                        'color': 'ワイン',
                        'primarySupplier': '住商モンブラン㈱',
                        'primarySupplierManual': True,
                    }
                },
            )
            st_manual['forceSkuFromGroup'] = True
            out = self._run({
                'undecided': st,
                'color_split': st_split,
                'manual_merge': st_manual,
            })

            u = out['undecided']
            self.assertEqual(u['skuId'], 'sku:72-1226')
            self.assertEqual(
                u['fieldKeys'],
                ['sku', 'name', 'color', 'supplier', 'processing'],
            )
            self.assertTrue(u['showsScrubCommon'], u['fields'])
            self.assertNotEqual(u['fields']['name']['value'], 'スクラブ ブルー')
            self.assertIn(u['fields']['name']['status'], ('仮', '自動決定済み'))
            self.assertTrue(u['showsColorNeed'], u['fields'])
            self.assertIn('ブルー', u['fields']['color']['value'])
            self.assertIn('ワイン', u['fields']['color']['value'])
            self.assertTrue(u['showsSupplierAuto'], u['fields'])
            self.assertIn('候補が1社', u['fields']['supplier']['hint'] or '')
            self.assertTrue(u['showsProcessingNa'], u['fields'])
            self.assertFalse(u['hasProvisionalBlueAsDecided'], u['html'])
            self.assertTrue(u['issueMapsColor'], u)
            self.assertTrue(u['noCsvRowInPlan'], u['html'])
            self.assertTrue(u['listHasPlanFields'])
            self.assertGreater(u['unsettledCount'], 0)
            self.assertFalse(u['allSettledWhileUnresolved'], u['fields'])
            # 加工サービスは商品用スロットを並べない
            self.assertEqual(
                u['procFieldKeys'],
                ['procName', 'procVendor', 'procSpec'],
                u['procFieldKeys'],
            )
            self.assertFalse(u['procHasGarmentSlots'])

            split = out['color_split']
            self.assertIn('color', split['fields'], split['fields'])
            self.assertIn('ブルー', split['fields']['color']['value'])
            self.assertIn('ワイン', split['fields']['color']['value'])
            # 統合用仮代表名（色別登録：…／スクラブ ブルー単体）を商品名確定値に見せない
            self.assertNotEqual(split['fields']['name']['value'], 'スクラブ ブルー')
            self.assertNotIn('色別登録：', split['fields']['name']['value'])
            self.assertIn('（ブルー）', split['fields']['name']['value'] or '')
            self.assertTrue(split['colorSplitHidesMergeProvisional'], split['fields'])
            self.assertFalse(split['hasProvisionalBlueAsDecided'], split['html'])

            manual = out['manual_merge']
            self.assertEqual(manual['fields']['name']['status'], '手動確定済み')
            self.assertEqual(manual['fields']['name']['value'], 'スクラブ')
            self.assertEqual(manual['fields']['color']['status'], '手動確定済み')
            self.assertEqual(manual['fields']['color']['value'], 'ワイン')
            self.assertEqual(manual['fields']['supplier']['status'], '手動確定済み')

            after = c.execute('select count(*) from products').fetchone()[0]
            self.assertEqual(before, after)
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
