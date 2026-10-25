"""旧CSV確認リスト: 「この行を登録しない」行除外（登録・在庫ミューテーションはテストDBのみ）。"""
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
import legacy_csv_bulk_import as bulk  # noqa: E402
import stock  # noqa: E402

JS = ROOT / 'static' / 'product_ledger_csv.js'
CSS = ROOT / 'static' / 'style.css'


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def csv_b64(text: str) -> str:
    return base64.b64encode(text.encode('utf-8-sig')).decode('ascii')


def ledger_csv(rows):
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
for (const [name, fn] of Object.entries(cases)) {
  out[name] = fn(api, sandbox);
}
process.stdout.write(JSON.stringify(out));
'''


class RowExcludeSourceTest(unittest.TestCase):
    def test_ui_wiring(self):
        js = JS.read_text(encoding='utf-8')
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('この行を登録しない', js)
        self.assertIn('同じ内容の', js)
        self.assertIn('登録しない項目', js)
        self.assertIn('登録対象に戻す', js)
        self.assertIn('rowExclusions', js)
        self.assertIn('stableRowKeyFromCsv', js)
        self.assertIn('sameContentKey', js)
        self.assertIn('USER_ROW_EXCLUDE_REASON', js)
        self.assertIn('sku-section-row-excluded', css)
        self.assertIn('pl-row-exclude-actions', css)
        # 「削除」名称をボタンに使わない
        self.assertNotIn('>この行を削除<', js)
        self.assertNotIn('data-pl-row-delete', js)

    def test_client_exclude_counts_and_same_content(self):
        cases = {
            'emptyProcNameDoesNotGroupByVendorOnly': '''
              function(api){
                const st={
                  preview:{rows:[
                    {rowNo:2,status:'needs_review',csv:{customer:'A',code:'',productName:'（加工）',size:'',supplier:'加工屋X'},
                     processingServiceView:true,processingService:{isProcessingVendor:true,serviceName:'',vendorName:'加工屋X',needsConfirm:true,missingFields:['加工名']},
                     parenClassification:{processing:'',needsConfirm:true},reasons:['加工名不足']},
                    {rowNo:3,status:'needs_review',csv:{customer:'B',code:'',productName:'別の加工',size:'',supplier:'加工屋X'},
                     processingServiceView:true,processingService:{isProcessingVendor:true,serviceName:'',vendorName:'加工屋X',needsConfirm:true,missingFields:['加工名']},
                     parenClassification:{processing:'',needsConfirm:true},reasons:['加工名不足']}
                  ]},
                  rowExclusions:{},rowClassifications:{},selections:{},skuDecisions:{},sectionOpen:{}
                };
                const k2=api.sameContentKey(st.preview.rows[0]);
                const k3=api.sameContentKey(st.preview.rows[1]);
                api.setRowExcluded(st, st.preview.rows[0], true);
                // サーバ再分類前でもクライアントで件数から外す
                st.preview.rows[0].status='hold';
                st.preview.rows[0].userRowExcluded=true;
                const items=api.buildUnifiedReviewItems(st);
                const counts=api.judgmentStatusCounts(st);
                return {
                  sameKeyEmpty: !k2 && !k3,
                  unresolved: counts.unresolved,
                  rowExcluded: counts.rowExcluded,
                  itemLabels: items.map(i=>i.targetLabel),
                  blocked: api.productsRegisterBlocked(st)
                };
              }
            ''',
            'singleExcludeKeepsSibling': '''
              function(api){
                const st={
                  preview:{rows:[
                    {rowNo:2,status:'needs_review',csv:{customer:'C',code:'',productName:'生地',size:'S',supplier:'部品屋'},
                     product:{blankSku:true,action:'create',category:''},reasons:['区分'],physicalPartsView:true,
                     parenClassification:{physicalPartsView:true}},
                    {rowNo:3,status:'needs_review',csv:{customer:'C',code:'',productName:'生地',size:'M',supplier:'部品屋'},
                     product:{blankSku:true,action:'create',category:''},reasons:['区分'],physicalPartsView:true,
                     parenClassification:{physicalPartsView:true}}
                  ]},
                  rowExclusions:{},rowClassifications:{},selections:{},skuDecisions:{},sectionOpen:{}
                };
                const peers=api.sameContentRows(st, st.preview.rows[0]);
                api.setRowExcluded(st, st.preview.rows[0], true);
                st.preview.rows[0].status='hold'; st.preview.rows[0].userRowExcluded=true;
                const items=api.buildUnifiedReviewItems(st);
                const counts=api.judgmentStatusCounts(st);
                return {
                  peerCount: peers.length,
                  unresolved: counts.unresolved,
                  rowExcluded: counts.rowExcluded,
                  remainingRows: (items[0]&&items[0].rowNos)||[]
                };
              }
            ''',
            'allExcludedClearsUnresolved': '''
              function(api){
                const st={
                  preview:{rows:[
                    {rowNo:2,status:'needs_review',csv:{customer:'D',code:'',productName:'ボタン',size:'',supplier:'部品屋'},
                     product:{blankSku:true,action:'create',category:''},reasons:['区分'],physicalPartsView:true,
                     parenClassification:{physicalPartsView:true}}
                  ]},
                  rowExclusions:{},rowClassifications:{},selections:{},skuDecisions:{},sectionOpen:{}
                };
                api.setRowExcluded(st, st.preview.rows[0], true);
                st.preview.rows[0].status='hold'; st.preview.rows[0].userRowExcluded=true;
                const counts=api.judgmentStatusCounts(st);
                return {unresolved:counts.unresolved, rowExcluded:counts.rowExcluded, blocked:api.productsRegisterBlocked(st)};
              }
            ''',
            'stableKeySurvivesReorder': '''
              function(api){
                const st={rowExclusions:{},preview:{rows:[]}};
                const r={rowNo:9,csv:{customer:'E',code:'X1',productName:'コート',size:'L',supplier:'メーカー'}};
                api.setRowExcluded(st,r,true);
                const key=api.stableRowKeyFromRow(r);
                const reordered={rowNo:2,csv:{customer:'E',code:'X1',productName:'コート',size:'L',supplier:'メーカー'}};
                st.preview.rows=[reordered];
                api.rebindExclusionsAfterPreview(st);
                return {
                  keyOk: !!key,
                  stillExcluded: api.isRowExcluded(st, reordered),
                  payloadHas: !!(api.previewPayload(Object.assign({importMode:'products',fileName:'a',fileB64:'x',selections:{},skuDecisions:{},rowClassifications:{},actorName:''},st)).rowExclusions[key])
                };
              }
            '''
        }
        with tempfile.TemporaryDirectory() as td:
            cases_path = Path(td) / 'cases.js'
            # wrap as evaluable object
            body = '{\n' + ',\n'.join(f'{json.dumps(k)}: {v}' for k, v in cases.items()) + '\n}'
            cases_path.write_text(body, encoding='utf-8')
            harness = Path(td) / 'harness.js'
            harness.write_text(
                NODE_HARNESS.replace(
                    "const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));",
                    "const cases = eval('('+fs.readFileSync(casesPath,'utf8')+')');",
                ),
                encoding='utf-8',
            )
            proc = subprocess.run(
                ['node', str(harness), str(JS), str(cases_path)],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)

        a = out['emptyProcNameDoesNotGroupByVendorOnly']
        self.assertTrue(a['sameKeyEmpty'])
        self.assertEqual(a['rowExcluded'], 1)
        self.assertEqual(a['unresolved'], 1)

        b = out['singleExcludeKeepsSibling']
        self.assertEqual(b['peerCount'], 2)
        self.assertEqual(b['rowExcluded'], 1)
        self.assertEqual(b['unresolved'], 1)
        self.assertEqual(b['remainingRows'], [3])

        c = out['allExcludedClearsUnresolved']
        self.assertEqual(c['unresolved'], 0)
        self.assertEqual(c['rowExcluded'], 1)
        self.assertFalse(c['blocked'])

        d = out['stableKeySurvivesReorder']
        self.assertTrue(d['keyOk'])
        self.assertTrue(d['stillExcluded'])
        self.assertTrue(d['payloadHas'])


class RowExcludeServerTest(unittest.TestCase):
    def test_preview_exclude_and_commit_skips_row(self):
        folder, path, c = newdb()
        self.addCleanup(folder.cleanup)
        text = ledger_csv([
            '顧客A,仕入先S,SKU1,ジャケット,S',
            '顧客A,仕入先S,SKU1,ジャケット,M',
            '顧客A,加工屋Z,,サイズダウン（加工）,',
        ])
        body = {
            'importMode': 'products',
            'name': 't.csv',
            'file': csv_b64(text),
            'registerOpeningStock': False,
            'selections': {},
            'skuDecisions': {},
            'rowClassifications': {},
            'rowExclusions': {},
            '_canWrite': True,
            '_actor': {'name': 'test'},
        }
        prev = pli.build_preview(c, body)
        proc_rows = [
            r for r in prev['rows']
            if (r.get('processingService') or {}).get('isProcessingVendor')
            or (r.get('processingServiceView'))
        ]
        # 加工名不足でも行はある想定。無ければ空加工行を直接探す
        target = None
        for r in prev['rows']:
            csv = r.get('csv') or {}
            if 'サイズダウン' in (csv.get('productName') or '') or (
                (r.get('processingService') or {}).get('needsConfirm')
            ):
                target = r
                break
        if target is None:
            # フォールバック: 最終行
            target = prev['rows'][-1]
        ident = pli.csv_row_identity(target)
        key = pli.stable_row_exclusion_key(
            customer=ident['customer'],
            code=ident['code'],
            product_name=ident['productName'],
            size=ident['size'],
            supplier=ident['supplier'],
        )
        body['rowExclusions'] = {
            key: {
                'excluded': True,
                **ident,
            }
        }
        # 残りの通常品番をまとめて登録可能に
        for g in prev.get('skuGroups') or []:
            if g.get('needsDecision'):
                body['skuDecisions'][g['code']] = {
                    'decision': 'merge',
                    'keepProductId': (g.get('existingProducts') or [{}])[0].get('id'),
                }

        prev2 = pli.build_preview(c, body)
        excluded = [r for r in prev2['rows'] if r.get('userRowExcluded')]
        self.assertTrue(excluded)
        self.assertTrue(all(r['status'] == pli.STATUS_HOLD for r in excluded))
        self.assertEqual(prev2.get('userRowExclusions', {}).get('count'), len(excluded))

        # 除外行の確認はブロックしない（他が解決済みなら）
        body['token'] = 'tok-row-exclude-1'
        body['previewAt'] = prev2.get('previewAt')
        body['previewFingerprint'] = prev2.get('previewFingerprint')
        body['allowCommitWithReview'] = True
        result = pli.commit_preview(c, body, db_path=path)
        self.assertFalse(result.get('already'))
        # 除外行は committed に入らない
        maps = list(c.execute(
            'SELECT product_id, spec_json, csv_product_name FROM product_ledger_row_maps'
        ))
        excl_maps = []
        for m in maps:
            try:
                spec = json.loads(m['spec_json'] or '') or {}
            except Exception:
                spec = {}
            if spec.get('excludedFromRegistration'):
                excl_maps.append(m)
        self.assertTrue(excl_maps, '除外判断が永続化されている')
        self.assertTrue(all(m['product_id'] is None for m in excl_maps))

        # 初期在庫段階: 数量を自動除外せずラベル表示
        aux = (
            '新古,仕入先,顧客,商品名,商品コード,サイズ,仕入単価,購入数量,出荷数,在庫,拠点,棚,補正在庫数\n'
            f"新品,{ident['supplier']},{ident['customer']},{ident['productName']},"
            f"{ident['code']},{ident['size']},100,,,3,本社,,\n"
        )
        open_body = {
            'importMode': 'opening_stock',
            'auxName': 'open.csv',
            'auxFile': csv_b64(aux),
            'registerOpeningStock': True,
            'basisDate': '2024-01-01',
            'defaultSiteName': '未確認',
            'openingSelections': {},
            '_canWrite': True,
            '_actor': {'name': 'test'},
        }
        open_prev = pli.build_preview(c, open_body)
        plans = (open_prev.get('openingStock') or {}).get('plans') or []
        self.assertTrue(plans)
        hit = plans[0]
        self.assertTrue(hit.get('productRegistrationExcluded'))
        self.assertIn(pli.PRODUCT_REGISTRATION_EXCLUDED_LABEL, ' '.join(hit.get('reasons') or []))
        # 自動で excluded にして数量破棄しない
        self.assertNotEqual(hit.get('status'), pli.STATUS_EXCLUDED)
        self.assertEqual(hit.get('qty'), 3)

    def test_same_content_size_variants_server_keys(self):
        a = pli.stable_row_exclusion_key(
            customer='C', code='P1', product_name='パンツ', size='S', supplier='S1',
        )
        b = pli.stable_row_exclusion_key(
            customer='C', code='P1', product_name='パンツ', size='M', supplier='S1',
        )
        self.assertNotEqual(a, b)


if __name__ == '__main__':
    unittest.main()
