"""旧CSV詳細: 登録区分と入力整理（fixtures / temp DB のみ。本番書込なし）。"""
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
import product_ledger_paren as paren  # noqa: E402
import product_ledger_processing_service as proc_svc  # noqa: E402
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
  return {
    open: false, busy: false,
    preview: {
      rows: [
        {
          rowNo: 1, status: 'needs_review',
          csv: { code: 'P1', productName: 'スクラブ（紺）', size: 'M', supplier: '仕入A', customer: '顧客A' },
          parenClassification: {
            plannedName: 'スクラブ', color: 'ネイビー', processing: '', material: '',
            partsUsage: '', unclassified: [], autoClassified: true,
            autoRegisterKind: '商品', registerKind: '商品'
          }
        },
        {
          rowNo: 2, status: 'create',
          csv: { code: '', productName: 'DCP515用生地（袖／裾加工用）', size: '—', supplier: '某資材', customer: '顧客A' },
          physicalPartsView: true,
          processingService: { isPhysicalParts: true, isProcessingService: false },
          parenClassification: {
            plannedName: 'DCP515用生地', color: '', processing: '', material: '',
            partsUsage: '袖／裾加工用', unclassified: [], autoClassified: true,
            physicalPartsView: true, autoRegisterKind: '部品・資材', registerKind: '部品・資材'
          },
          product: { category: '部品・資材', blankSku: true, action: 'create' }
        },
        {
          rowNo: 3, status: 'create',
          csv: { code: '', productName: 'サイズダウン', size: '—', supplier: 'K2スタッフ工房', customer: '顧客A' },
          processingServiceView: true,
          processingService: {
            isProcessingService: true, identifiable: true, needsConfirm: false,
            serviceName: 'サイズダウン', vendorName: 'K2スタッフ工房'
          },
          parenClassification: {
            plannedName: 'サイズダウン', processing: 'サイズダウン',
            processingServiceView: true, autoRegisterKind: '加工サービス', registerKind: '加工サービス'
          },
          product: { category: '加工サービス', blankSku: true, action: 'create' }
        },
        {
          rowNo: 4, status: 'needs_review',
          csv: { code: 'X1', productName: 'ブルゾン（冬）', size: 'L', supplier: '仕入B', customer: '顧客B' },
          parenClassification: {
            plannedName: 'ブルゾン（冬）', color: '', processing: '', material: '',
            unclassified: ['冬'], infoNotices: ['分類できなかった原文『冬』は商品名に残して登録します'],
            autoRegisterKind: '商品', registerKind: '商品'
          }
        }
      ],
      skuGroups: []
    },
    skuDecisions: {}, appliedSkuDecisions: {},
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


class CategoryInputClaritySourceTest(unittest.TestCase):
    def test_ui_labels_and_guards(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('登録区分', js)
        self.assertIn('登録後の内容', js)
        self.assertIn('分類できなかった原文', js)
        self.assertIn('data-pl-register-kind', js)
        self.assertIn('data-pl-paren-purpose', js)
        self.assertIn('加工・素材欄への入力だけでは区分は切り替わりません', js)
        self.assertIn('袖／裾加工用', js)
        self.assertIn('pendingRematch', js)
        self.assertIn('preserveScroll:true', js)
        self.assertNotIn("paintDialog({anchorCode:", js)
        # 旧ラベルは商品区分では使わない
        self.assertIn('>商品名<', js.replace(' ', ''))
        self.assertIn('素材は材質', js)


class PartsUsageClassifyTest(unittest.TestCase):
    def test_dcp515_fabric_purpose_not_process_service(self):
        name = 'DCP515用生地（袖／裾加工用）'
        self.assertTrue(proc_svc.is_physical_parts_name(name))
        seg = paren.classify_paren_segment('袖／裾加工用')
        self.assertEqual(seg.get('kind'), 'parts_usage')
        split = paren.split_name_and_color(name)
        self.assertEqual(split.get('plannedName'), 'DCP515用生地')
        self.assertEqual(split.get('partsUsage'), '袖／裾加工用')
        self.assertEqual(split.get('processing') or '', '')

    def test_sleeve_process_service_still_processing(self):
        # 『袖加工』は用途ではなく加工語（用なし）
        seg = paren.classify_paren_segment('袖加工')
        self.assertEqual(seg.get('kind'), 'processing')
        self.assertFalse(paren.is_parts_usage_token('袖加工'))


class PartsUsagePreviewSaveTest(unittest.TestCase):
    def test_preview_and_commit_parts_usage(self):
        folder, path, c = newdb()
        try:
            text = ledger('顧客A,某資材,,DCP515用生地（袖／裾加工用）,—')
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'dcp.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            })
            rows = preview.get('rows') or []
            self.assertEqual(len(rows), 1)
            r = rows[0]
            pc = r.get('parenClassification') or {}
            self.assertTrue(r.get('physicalPartsView'))
            self.assertEqual(pc.get('registerKind'), '部品・資材')
            self.assertEqual(pc.get('partsUsage'), '袖／裾加工用')
            self.assertEqual(pc.get('plannedName'), 'DCP515用生地')
            self.assertFalse((r.get('processingService') or {}).get('isProcessingService'))
            self.assertEqual((r.get('product') or {}).get('category'), stock.CATEGORY_PARTS)

            # 保存: 用途は補足経由で名へ括弧付き。加工サービスは作らない
            pli.commit_preview(c, {
                'file': csv_b64(text), 'name': 'dcp.csv',
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'dcp-parts-commit-1',
                '_canWrite': True, '_actor': {'name': 't'},
            }, db_path=str(path))
            c.commit()
            row = c.execute(
                "SELECT name, category FROM products WHERE category=? ORDER BY id DESC LIMIT 1",
                (stock.CATEGORY_PARTS,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['category'], stock.CATEGORY_PARTS)
            self.assertIn('DCP515用生地', row['name'])
            # 用途は補足として括弧付きで名に載る（専用カラム無し）
            self.assertIn('袖／裾加工用', row['name'])
            n_ps = c.execute(
                "SELECT COUNT(*) AS n FROM products WHERE category=?",
                (stock.CATEGORY_PROCESS_SERVICE,),
            ).fetchone()['n']
            self.assertEqual(n_ps, 0)
        finally:
            c.close()
            folder.cleanup()

    def test_manual_kind_wins_typing_does_not_switch(self):
        folder, path, c = newdb()
        try:
            text = ledger('顧客A,仕入A,S-1,スクラブ（紺）,M')
            # 加工欄を手入力しても registerKind=商品のまま
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'p.csv',
                'rowClassifications': {
                    # CSV データ行は rowNo=2（1行目はヘッダ）
                    '2': {
                        'userEdited': True,
                        'registerKind': '商品',
                        'processing': '裾上げ',
                        'material': '綿',
                        'plannedName': 'スクラブ',
                        'color': 'ネイビー',
                    }
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            r = (preview.get('rows') or [None])[0]
            self.assertIsNotNone(r)
            self.assertEqual(r.get('rowNo'), 2)
            pc = r.get('parenClassification') or {}
            self.assertEqual(pc.get('registerKind'), '商品')
            self.assertFalse(r.get('processingServiceView'))
            self.assertFalse(r.get('physicalPartsView'))
            self.assertEqual(pc.get('processing'), '裾上げ')
            self.assertEqual(pc.get('material'), '綿')
        finally:
            c.close()
            folder.cleanup()

    def test_category_change_keeps_inputs(self):
        folder, path, c = newdb()
        try:
            text = ledger('顧客A,某資材,,DCP515用生地（袖／裾加工用）,—')
            preview = pli.build_preview(c, {
                'file': csv_b64(text), 'name': 'keep.csv',
                'rowClassifications': {
                    '2': {
                        'userEdited': True,
                        'registerKind': '商品',
                        'plannedName': 'DCP515用生地',
                        'partsUsage': '袖／裾加工用',
                        'color': 'ホワイト',
                        'material': '綿',
                    }
                },
                '_canWrite': True, '_actor': {'name': 't'},
            })
            r = (preview.get('rows') or [None])[0]
            pc = r.get('parenClassification') or {}
            self.assertEqual(pc.get('registerKind'), '商品')
            self.assertEqual(pc.get('partsUsage'), '袖／裾加工用')
            self.assertEqual(pc.get('color'), 'ホワイト')
            self.assertEqual(pc.get('material'), '綿')
            self.assertEqual(pc.get('plannedName'), 'DCP515用生地')
            # 区分を商品にしても用途・色・素材は消えない
            self.assertFalse(r.get('physicalPartsView'))
        finally:
            c.close()
            folder.cleanup()

    def test_unclassified_move_to_purpose(self):
        plan = pli.build_paren_classification(
            'パーツ（特別仕様）',
            {
                'userEdited': True,
                'plannedName': 'パーツ',
                'unclassified': ['特別仕様'],
                'unclassifiedMove': 'to_purpose',
            },
        )
        self.assertEqual(plan.get('partsUsage'), '特別仕様')
        self.assertEqual(plan.get('unclassified') or [], [])
        self.assertTrue(plan.get('partsUsage'))


class CategoryInputClarityUiTest(unittest.TestCase):
    def test_detail_fields_by_category_and_pending(self):
        cases = {
            'kinds': r'''
              function(api, baseSt){
                const st = baseSt();
                const rows = st.preview.rows;
                const productHtml = api.parenCell(st, rows[0]);
                const partsHtml = api.parenCell(st, rows[1]);
                const procHtml = api.parenCell(st, rows[2]);
                const uncHtml = api.parenCell(st, rows[3]);
                // 加工欄入力だけでは区分不変
                api.setRowClassification(st, 1, {processing: '裾上げ'});
                const kindAfterType = api.effectiveRegisterKind(st, rows[0]);
                // 手動区分
                api.setRowClassification(st, 1, {registerKind: '部品・資材'});
                const kindManual = api.effectiveRegisterKind(st, Object.assign({}, rows[0]));
                const pending = !!(st.rowClassifications['1']||{}).pendingRematch;
                api.clearRowClassificationPending(st);
                const cleared = !!(st.rowClassifications['1']||{}).pendingRematch;
                return {
                  productHas: productHtml.indexOf('登録区分')>=0 && productHtml.indexOf('商品名')>=0
                    && productHtml.indexOf('必要な加工・仕様')>=0 && productHtml.indexOf('素材')>=0
                    && productHtml.indexOf('登録後の内容')>=0,
                  partsHas: partsHtml.indexOf('名称')>=0 && partsHtml.indexOf('用途')>=0
                    && partsHtml.indexOf('袖／裾加工用')>=0 && partsHtml.indexOf('登録後の内容')>=0
                    && partsHtml.indexOf('加工サービス名にはしません')>=0,
                  procHas: procHtml.indexOf('加工名')>=0 && procHtml.indexOf('加工業者')>=0
                    && procHtml.indexOf('補足・個別仕様')>=0
                    && procHtml.indexOf('登録時表示名')>=0,
                  uncHas: uncHtml.indexOf('分類できなかった原文')>=0
                    && uncHtml.indexOf('名称に残す')>=0
                    && uncHtml.indexOf('用途へ移す')>=0,
                  noOldUncLabel: uncHtml.indexOf('>未分類<')<0 && productHtml.indexOf('>予定名<')<0,
                  kindAfterType: kindAfterType,
                  kindManual: kindManual,
                  pending: pending,
                  cleared: cleared,
                  finalName: api.finalNameWithSupplement('DCP515用生地', '袖／裾加工用')
                };
              }
            '''
        }
        out = run_js_cases(cases)['kinds']
        self.assertTrue(out['productHas'])
        self.assertTrue(out['partsHas'])
        self.assertTrue(out['procHas'])
        self.assertTrue(out['uncHas'])
        self.assertTrue(out['noOldUncLabel'])
        self.assertEqual(out['kindAfterType'], '商品')
        self.assertEqual(out['kindManual'], '部品・資材')
        self.assertTrue(out['pending'])
        self.assertFalse(out['cleared'])
        self.assertEqual(out['finalName'], 'DCP515用生地（袖／裾加工用）')


if __name__ == '__main__':
    unittest.main()
