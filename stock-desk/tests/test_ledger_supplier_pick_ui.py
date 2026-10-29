"""通常仕入先の手動選択UI — 表示と登録ゲートの一致（一時DBのみ。本番登録なし）。"""
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


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64_from_text(text: str, encoding='utf-8-sig') -> str:
    return base64.b64encode(text.encode(encoding)).decode('ascii')


def tiny_ledger_rows(rows):
    header = '顧客,仕入先,商品コード,商品名,サイズ'
    return '\n'.join([header] + rows)


class MultiSupplierColorDoesNotAutoPrimaryTest(unittest.TestCase):
    """明示色で名は決まっても、複社なら通常仕入先は自動決定しない。"""

    def test_6801_style_color_multi_supplier_stays_undecided(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,アイトス㈱,6801-014,コックコート（白）,L',
                '顧客A,㈱セブンユニフォーム,6801-014,コックコート,M',
                '顧客A,K2スタッフ工房,6801-014,インナーネット付け,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': '6801.csv',
                'importMode': 'products',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '6801-014')
            aux = g.get('auxPrimary') or {}
            self.assertTrue(g['needsDecision'])
            self.assertTrue(aux.get('needsManualPrimary'))
            self.assertEqual(aux.get('autoPrimarySupplier') or '', '')
            self.assertEqual(g.get('plannedPrimarySupplier') or '', '')
            self.assertTrue(aux.get('nameDecidedSupplierUndecided') or g.get('supplierDiff'))
            self.assertEqual(g.get('reviewTier'), 'individual')
            # 名・色の予定は付いてよい
            self.assertTrue(g.get('plannedRegisteredName') or aux.get('suggestedOfficialName'))
            labels = [r.get('label') or '' for r in (g.get('reviewReasons') or [])]
            self.assertTrue(any('通常仕入先' in lab for lab in labels), labels)
        finally:
            c.close()
            folder.cleanup()

    def test_merge_undecided_gate_and_process_row_separate(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,アイトス㈱,6801-014,コックコート,L',
                '顧客A,㈱セブンユニフォーム,6801-014,コックコート,M',
                '顧客A,K2スタッフ工房,6801-014,インナーネット付け,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': '6801.csv',
                'importMode': 'products',
                'skuDecisions': {'6801-014': {'decision': 'merge'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            garment_und = [
                r for r in preview['rows']
                if not (r.get('processingService') or {}).get('isProcessingService')
                and any('通常仕入先が未定' in str(x) for x in (r.get('reasons') or []))
            ]
            proc_und = [
                r for r in preview['rows']
                if (r.get('processingService') or {}).get('isProcessingService')
                and any('通常仕入先が未定' in str(x) for x in (r.get('reasons') or []))
            ]
            self.assertGreaterEqual(len(garment_und), 1)
            self.assertEqual(proc_und, [], '加工サービス行へ本体の通常仕入先未定を載せない')
        finally:
            c.close()
            folder.cleanup()

    def test_sapporo_hakui_inner_net_process_checklist_separate(self):
        """whitelist外でも加工名だけの行は加工サービス側へ。本体の未定を引き継がない。"""
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,アイトス㈱,6801-014,コックコート,L',
                '顧客A,㈱セブンユニフォーム,6801-014,コックコート,M',
                '顧客A,㈱札幌白衣,6801-014,インナーネット付け,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': '6801.csv',
                'importMode': 'products',
                'skuDecisions': {'6801-014': {'decision': 'merge'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '6801-014')
            self.assertEqual(
                sorted(g.get('supplierCandidates') or []),
                ['アイトス㈱', '㈱セブンユニフォーム'],
            )
            self.assertNotIn('㈱札幌白衣', g.get('supplierCandidates') or [])
            proc_rows = [
                r for r in preview['rows']
                if (r.get('csv') or {}).get('productName') == 'インナーネット付け'
            ]
            self.assertEqual(len(proc_rows), 1)
            ps = proc_rows[0].get('processingService') or {}
            self.assertTrue(ps.get('isProcessingService'))
            self.assertEqual(ps.get('serviceName'), 'インナーネット付け')
            self.assertEqual(ps.get('vendorName'), '㈱札幌白衣')
            self.assertTrue(proc_rows[0].get('processingServiceView'))
            self.assertFalse(
                any('通常仕入先が未定' in str(x) for x in (proc_rows[0].get('reasons') or [])),
                proc_rows[0].get('reasons'),
            )
            garment_und = [
                r for r in preview['rows']
                if (r.get('csv') or {}).get('productName') == 'コックコート'
                and any('通常仕入先が未定' in str(x) for x in (r.get('reasons') or []))
            ]
            self.assertGreaterEqual(len(garment_und), 1)
            plans = preview.get('processingServicePlans') or []
            self.assertTrue(
                any(
                    (p.get('serviceName') == 'インナーネット付け'
                     and p.get('vendorName') == '㈱札幌白衣')
                    for p in plans
                ),
                plans,
            )
        finally:
            c.close()
            folder.cleanup()

    def test_manual_pick_clears_undecided_after_rematch(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,アイトス㈱,6801-014,コックコート（白）,L',
                '顧客A,㈱セブンユニフォーム,6801-014,コックコート,M',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': '6801.csv',
                'importMode': 'products',
                'skuDecisions': {
                    '6801-014': {
                        'decision': 'merge',
                        'primarySupplier': 'アイトス㈱',
                        'primarySupplierManual': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == '6801-014')
            self.assertEqual(g.get('plannedPrimarySupplier'), 'アイトス㈱')
            self.assertEqual(g.get('plannedPrimarySupplierSource'), 'manual')
            und = [
                r for r in preview['rows']
                if any('通常仕入先が未定' in str(x) for x in (r.get('reasons') or []))
            ]
            self.assertEqual(und, [])
            self.assertEqual(preview.get('reviewCount') or 0, 0)
        finally:
            c.close()
            folder.cleanup()

    def test_never_overwrite_manual_with_auto(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,SKU-M,作業着,M',
                '顧客A,仕入B,SKU-M,作業着,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'm.csv',
                'importMode': 'products',
                'skuDecisions': {
                    'SKU-M': {
                        'decision': 'merge',
                        'primarySupplier': '仕入B',
                        'primarySupplierManual': True,
                    },
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            g = next(x for x in preview['skuGroups'] if x['code'] == 'SKU-M')
            resolved = g.get('primarySupplierResolved') or {}
            self.assertEqual(resolved.get('primarySupplier'), '仕入B')
            self.assertEqual(resolved.get('source'), 'manual')
        finally:
            c.close()
            folder.cleanup()


class CodeNameSwapNoticeTest(unittest.TestCase):
    """品番／商品名が逆に見える行は案内のみ（自動入替しない）。"""

    def test_warn_without_swap(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,仕入A,コックコート,6-441,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': 'swap.csv',
                'importMode': 'products',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            row = preview['rows'][0]
            csv = row.get('csv') or {}
            self.assertEqual(csv.get('code'), 'コックコート')
            self.assertEqual(csv.get('productName'), '6-441')
            self.assertTrue(
                any('自動では入れ替えません' in str(x) for x in (row.get('reasons') or [])),
                row.get('reasons'),
            )
        finally:
            c.close()
            folder.cleanup()


PLAN_HARNESS = r'''
const fs = require('fs');
const vm = require('vm');
const jsPath = process.argv[2];
const casesPath = process.argv[3];
const code = fs.readFileSync(jsPath, 'utf8');
const sandbox = {
  window: {},
  document: { getElementById() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; } },
  console,
  setTimeout,
  clearTimeout,
  CSS: { escape: (s) => String(s) },
  esc: (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  $: () => null,
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const api = sandbox.window.ProductLedgerCsv.__test;
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const out = {};
for (const [name, st] of Object.entries(cases)) {
  const items = api.buildUnifiedReviewItems(st);
  let sku = items.find(i => i.kind === 'sku') || null;
  if (!sku) {
    const g = ((st.preview && st.preview.skuGroups) || [])[0];
    if (g) {
      sku = {
        id: 'sku:' + g.code, kind: 'sku', group: g, code: g.code,
        targetLabel: g.codeDisplay || g.code, rows: [], rowNos: g.rowNos || [],
        issues: (g.reviewReasons || []).map(r => ({
          type: 'sku_reason', code: r.code || '', label: r.label || '', resolved: false,
        })),
        unresolvedIssueCount: (g.reviewReasons || []).length,
      };
    }
  }
  const fields = sku ? api.buildPlannedStatusFields(st, sku) : [];
  const fmap = {};
  fields.forEach(f => { fmap[f.key] = f; });
  const detail = sku && sku.group
    ? api.skuRowDetailHtml(st, sku.group, { colspan: 5 })
    : '';
  const planHtml = sku ? api.unifiedPlanHtml(st, sku) : '';
  const skuRow = items.find(i => i.kind === 'sku_row') || null;
  let skuRowFields = [];
  let skuRowFmap = {};
  let skuRowPlanHtml = '';
  if (skuRow) {
    skuRowFields = api.buildPlannedStatusFields(st, skuRow);
    skuRowFields.forEach(f => { skuRowFmap[f.key] = f; });
    skuRowPlanHtml = api.unifiedPlanHtml(st, skuRow);
  }
  const procItem = items.find(i => i.kind === 'processing') || null;
  out[name] = {
    unresolved: items.length,
    kinds: items.map(i => i.kind + ':' + (i.targetLabel || i.code || '')),
    tier: sku && sku.group && sku.group.reviewTier,
    supplierStatus: fmap.supplier && fmap.supplier.status,
    supplierValue: fmap.supplier && fmap.supplier.value,
    supplierHint: fmap.supplier && fmap.supplier.hint,
    hasAutoWhileUndecided: !!(fmap.supplier
      && fmap.supplier.status === api.PLAN_STATUS.AUTO
      && (sku.issues || []).some(i => /通常仕入先が未定|通常仕入先を選んで/.test(i.label || ''))),
    allSettledWhileUnresolved: fields.length
      ? fields.every(f => !f.unsettled) && ((sku && sku.unresolvedIssueCount) > 0)
      : false,
    hasPicker: /data-pl-primary-sup/.test(detail) && /選択してください/.test(detail),
    planSaysAligned: /予定は揃っています/.test(planHtml),
    needsChoice: !!(sku && sku.group && api.primarySupplierNeedsChoice(st, sku.group)),
    skuRowSupplierStatus: skuRowFmap.supplier && skuRowFmap.supplier.status,
    skuRowHasAutoWhileUndecided: !!(skuRowFmap.supplier
      && skuRowFmap.supplier.status === api.PLAN_STATUS.AUTO
      && (skuRow.issues || []).some(i => /通常仕入先が未定|通常仕入先を選んで/.test(i.label || ''))),
    skuRowPlanSaysAligned: /予定は揃っています/.test(skuRowPlanHtml),
    procHasSupplierUndecided: !!(procItem && (procItem.issues || []).some(
      i => /通常仕入先が未定|通常仕入先を選んで/.test(i.label || '')
    )),
    procKind: procItem && procItem.kind,
    procLabel: procItem && procItem.targetLabel,
  };
}
process.stdout.write(JSON.stringify(out));
'''


class SupplierPickUiDisplayTest(unittest.TestCase):
    """登録予定表示と未定チェックリスト・ピッカーの整合。"""

    def _run(self, cases):
        folder = tempfile.TemporaryDirectory()
        try:
            cases_path = Path(folder.name) / 'cases.json'
            harness_path = Path(folder.name) / 'harness.js'
            cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
            harness_path.write_text(PLAN_HARNESS, encoding='utf-8')
            proc = subprocess.run(
                ['node', str(harness_path), str(JS), str(cases_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            return json.loads(proc.stdout)
        finally:
            folder.cleanup()

    def test_undecided_shows_need_choice_and_picker_not_auto(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,アイトス㈱,6801-014,コックコート（白）,L',
                '顧客A,㈱セブンユニフォーム,6801-014,コックコート,M',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': '6801.csv',
                'importMode': 'products',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            st = {
                'preview': {
                    'skuGroups': preview.get('skuGroups') or [],
                    'rows': preview.get('rows') or [],
                    'autoMergePlans': preview.get('autoMergePlans') or [],
                    'plannedCounts': preview.get('plannedCounts') or {},
                    'outcomeSummary': preview.get('outcomeSummary') or {},
                    'reviewCount': preview.get('reviewCount') or 0,
                },
                'skuDecisions': {},
                'appliedSkuDecisions': {},
                'rowClassifications': {},
                'selections': {},
                'selectedCodes': {},
                'expandedCodes': {'6801-014': True},
                'expandedUnified': {},
                'sectionOpen': {'unified': True},
                'busy': False,
            }
            out = self._run({'undecided': st})
            u = out['undecided']
            self.assertNotEqual(u['supplierStatus'], '自動決定済み', u)
            self.assertEqual(u['supplierStatus'], '選択が必要', u)
            self.assertFalse(u['hasAutoWhileUndecided'], u)
            self.assertFalse(u['allSettledWhileUnresolved'], u)
            self.assertFalse(u['planSaysAligned'], u)
            self.assertTrue(u['hasPicker'], u)
            self.assertTrue(u['needsChoice'], u)
        finally:
            c.close()
            folder.cleanup()

    def test_js_source_has_picker_and_manual_pending_labels(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('normalSupplierPickerHtml', js)
        self.assertIn('選択してください', js)
        self.assertIn('手動選択・未反映', js)
        self.assertIn('primarySupplierNeedsChoice', js)
        self.assertIn('skuSupplierPendingRematch', js)
        self.assertIn('CSV各行の仕入先は行ごと', js)

    def test_sku_row_plan_not_auto_while_undecided(self):
        """行バケットでも CSV仕入先を自動決定済みにしない（ゲート未定と一致）。"""
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,アイトス㈱,6801-014,コックコート（白）,L',
                '顧客A,㈱セブンユニフォーム,6801-014,コックコート,M',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': '6801.csv',
                'importMode': 'products',
                'skuDecisions': {'6801-014': {'decision': 'merge'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            # 確認リストから外した状態を模して行バケット経路を直接検証
            g = next(x for x in preview['skuGroups'] if x['code'] == '6801-014')
            g = dict(g)
            g['needsDecision'] = False
            g['reviewTier'] = 'auto'
            und_rows = [
                r for r in (preview.get('rows') or [])
                if any('通常仕入先が未定' in str(x) for x in (r.get('reasons') or []))
            ]
            self.assertTrue(und_rows, 'gate should leave undecided reasons on rows')
            st = {
                'preview': {
                    'skuGroups': [g],
                    'rows': preview.get('rows') or [],
                    'autoMergePlans': [],
                    'plannedCounts': {},
                    'outcomeSummary': {},
                    'reviewCount': preview.get('reviewCount') or 0,
                },
                'skuDecisions': {'6801-014': {'decision': 'merge'}},
                'appliedSkuDecisions': {'6801-014': {'decision': 'merge'}},
                'rowClassifications': {},
                'selections': {},
                'selectedCodes': {},
                'expandedCodes': {},
                'expandedUnified': {},
                'sectionOpen': {'unified': True},
                'busy': False,
            }
            out = self._run({'sku_row': st})
            u = out['sku_row']
            self.assertFalse(u['skuRowHasAutoWhileUndecided'], u)
            self.assertFalse(u['skuRowPlanSaysAligned'], u)
            if u.get('skuRowSupplierStatus'):
                self.assertNotEqual(u['skuRowSupplierStatus'], '自動決定済み', u)
        finally:
            c.close()
            folder.cleanup()

    def test_process_service_item_omits_parent_supplier_undecided(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,アイトス㈱,6801-014,コックコート,L',
                '顧客A,㈱セブンユニフォーム,6801-014,コックコート,M',
                '顧客A,㈱札幌白衣,6801-014,インナーネット付け,L',
            ])
            preview = pli.build_preview(c, {
                'file': csv_b64_from_text(text), 'name': '6801.csv',
                'importMode': 'products',
                'skuDecisions': {'6801-014': {'decision': 'merge'}},
                '_canWrite': True, '_actor': {'name': 't'},
            })
            st = {
                'preview': {
                    'skuGroups': preview.get('skuGroups') or [],
                    'rows': preview.get('rows') or [],
                    'autoMergePlans': preview.get('autoMergePlans') or [],
                    'plannedCounts': preview.get('plannedCounts') or {},
                    'outcomeSummary': preview.get('outcomeSummary') or {},
                    'reviewCount': preview.get('reviewCount') or 0,
                    'processingServicePlans': preview.get('processingServicePlans') or [],
                },
                'skuDecisions': {'6801-014': {'decision': 'merge'}},
                'appliedSkuDecisions': {},
                'rowClassifications': {},
                'selections': {},
                'selectedCodes': {},
                'expandedCodes': {'6801-014': True},
                'expandedUnified': {},
                'sectionOpen': {'unified': True},
                'busy': False,
            }
            out = self._run({'proc': st})
            u = out['proc']
            self.assertFalse(u['procHasSupplierUndecided'], u)
            self.assertFalse(u['hasAutoWhileUndecided'], u)
            self.assertFalse(u['planSaysAligned'], u)
        finally:
            c.close()
            folder.cleanup()


class UiSourceWiringTest(unittest.TestCase):
    def test_helpers_exported(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('primarySupplierNeedsChoice', js)
        self.assertIn('effectivePrimarySupplier', js)
        self.assertIn('normalSupplierPickerHtml', js)


if __name__ == '__main__':
    unittest.main()
