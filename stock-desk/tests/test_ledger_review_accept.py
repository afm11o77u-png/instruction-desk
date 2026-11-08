"""確認リスト: 判断不要な自動決定を混ぜず、了承は明示操作だけをサーバへ反映する。"""
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
const code = fs.readFileSync(process.argv[2], 'utf8');
const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
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
  const html = api.unifiedListRowsHtml(st, items);
  out[name] = {
    labels: items.map(i => i.targetLabel),
    kinds: items.map(i => i.kind),
    issues: items.map(i => (i.issues || []).filter(x => !x.resolved).map(x => x.label)),
    htmlHasAccept: html.includes('この内容で了承'),
    htmlHasRepreviewOnly: html.includes('再照合して反映') && !html.includes('この内容で了承'),
  };
}
process.stdout.write(JSON.stringify(out));
'''


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    conn = stock.connect(path)
    stock.initialize(conn)
    pli.ensure_schema(conn)
    return folder, path, conn


def preview_lines(conn, lines, phase='parts'):
    text = '顧客,仕入先,商品コード,商品名,サイズ\n' + '\n'.join(lines)
    body = {
        'importMode': 'products', 'productPhase': phase, 'name': 'p.csv',
        'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
        '_canWrite': True, '_actor': {'name': '確認'},
    }
    return pli.build_preview(conn, body), body


class PartsAutoNotListedTest(unittest.TestCase):
    def test_fabric_row_stays_parts_and_is_not_a_judgment(self):
        folder, path, conn = newdb()
        try:
            preview, body = preview_lines(conn, [
                '顧客A,ミドリ安全北海道 釧路営業所,NT7003難燃作業衣 生地,NT7003難燃作業衣 生地,-',
                '顧客A,ミドリ安全北海道 釧路営業所,NT7003,難燃作業衣,L',
            ], phase='')
            fabric = next(r for r in preview['rows'] if '生地' in (r['csv'].get('productName') or ''))
            self.assertEqual(fabric['csv']['code'], 'NT7003難燃作業衣 生地')
            self.assertEqual((fabric.get('product') or {}).get('category'), '部品・資材')
            self.assertTrue(fabric['canCommit'])
            self.assertNotEqual(fabric['status'], 'needs_review')
            garment = next(r for r in preview['rows'] if r['csv'].get('code') == 'NT7003')
            self.assertNotEqual(garment['csv']['code'], fabric['csv']['code'])
            st = {
                'preview': preview, 'skuDecisions': {}, 'rowClassifications': {},
                'selections': {}, 'selectedCodes': {}, 'expandedCodes': {}, 'expandedUnified': {},
                'sectionOpen': {'unified': True},
            }
            cases = {'fabric': st}
            harness = Path(folder.name) / 'h.js'
            data = Path(folder.name) / 'c.json'
            harness.write_text(NODE_HARNESS, encoding='utf-8')
            data.write_text(json.dumps(cases), encoding='utf-8')
            proc = subprocess.run(['node', str(harness), str(JS), str(data)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            out = json.loads(proc.stdout)['fabric']
            self.assertFalse(any('生地' in lab for lab in out['labels']), out)
        finally:
            conn.close()
            folder.cleanup()

    def test_star_komae_blank_code_is_not_a_judgment(self):
        folder, path, conn = newdb()
        try:
            preview, _body = preview_lines(conn, [
                '顧客A,(株)サーヴォ,,*小前,-',
            ])
            row = preview['rows'][0]
            self.assertEqual(row['csv']['productName'], '*小前')
            self.assertEqual(row['csv']['code'], '')
            self.assertEqual((row.get('product') or {}).get('category'), '部品・資材')
            self.assertTrue(row['canCommit'])
            self.assertNotEqual(row['status'], 'needs_review')
        finally:
            conn.close()
            folder.cleanup()


class ReviewAcceptApiTest(unittest.TestCase):
    def test_accept_clears_safe_suffix_and_rejects_supplier_choice(self):
        folder, path, conn = newdb()
        try:
            stock.save_supplier(conn, {'name': '仕入A', 'active': True})
            stock.save_supplier(conn, {'name': '仕入A支店', 'active': True})
            conn.commit()
            text = '顧客,仕入先,商品コード,商品名,サイズ\n顧客A,仕入A,ZZ9 メモ不明,帽子,L\n'
            body = {
                'importMode': 'products', 'name': 'z.csv',
                'file': base64.b64encode(text.encode('utf-8-sig')).decode('ascii'),
                '_canWrite': True, '_actor': {'name': '確認'},
            }
            preview = pli.build_preview(conn, body)
            row = preview['rows'][0]
            self.assertEqual(row['status'], 'needs_review')
            self.assertFalse(row['canCommit'])
            fp = pli._review_fingerprint(row)
            again = pli.build_preview(conn, {**body, 'rowClassifications': {'2': {'userEdited': True}}})
            self.assertEqual(again['rows'][0]['status'], 'needs_review')
            accepted = pli.build_preview(conn, {**body, 'rowClassifications': {
                '2': {'reviewAccepted': True, 'reviewFingerprint': fp, 'userActed': True},
            }})
            self.assertTrue(accepted['rows'][0]['canCommit'])
            self.assertNotEqual(accepted['rows'][0]['status'], 'needs_review')
            changed = pli.build_preview(conn, {**body, 'rowClassifications': {
                '2': {'reviewAccepted': True, 'reviewFingerprint': fp + 'x', 'userActed': True},
            }})
            self.assertEqual(changed['rows'][0]['status'], 'needs_review')
        finally:
            conn.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
