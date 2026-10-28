"""メーカー補正候補チェック時のスクロール保持（UIのみ。本番登録・在庫変更なし）。"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'static' / 'product_ledger_csv.js'

NODE_HARNESS = r'''
const fs = require('fs');
const vm = require('vm');
const jsPath = process.argv[2];
const code = fs.readFileSync(jsPath, 'utf8');

function el(tag, attrs, props) {
  const o = {
    tagName: String(tag || 'DIV').toUpperCase(),
    attrs: Object.assign({}, attrs || {}),
    checked: !!(props && props.checked),
    disabled: !!(props && props.disabled),
    textContent: (props && props.textContent) || '',
    value: (props && props.value) || '',
    getAttribute(k) { return this.attrs[k] == null ? null : String(this.attrs[k]); },
    setAttribute(k, v) { this.attrs[k] = String(v); },
  };
  return o;
}

const boxes = [
  el('input', {'data-pl-bulk-maker-pick': 'SKU-A#0', 'data-result-idx': '0', 'data-cand-idx': '0'}, {checked: true}),
  el('input', {'data-pl-bulk-maker-pick': 'SKU-A#1', 'data-result-idx': '0', 'data-cand-idx': '1'}, {checked: false}),
  el('input', {'data-pl-bulk-maker-pick': 'SKU-B#0', 'data-result-idx': '1', 'data-cand-idx': '0'}, {checked: false}),
];
const adoptBtn = el('button', {id: 'plBulkMakerAdopt'}, {disabled: false});
const meta = el('span', {id: 'plBulkMakerAdoptMeta'}, {textContent: '採用済 0　確認残り 1'});
const progress = el('span', {id: 'plBulkMakerProgress'}, {textContent: '完了 2／2'});
const counts = el('p', {id: 'plBulkMakerCounts'}, {textContent: '候補1件 0　複数 0'});
const byId = {
  plBulkMakerAdopt: adoptBtn,
  plBulkMakerAdoptMeta: meta,
  plBulkMakerProgress: progress,
  plBulkMakerCounts: counts,
};

const sandbox = {
  window: {},
  document: {
    getElementById(id) { return byId[id] || null; },
    querySelector() { return null; },
    querySelectorAll(sel) {
      if (sel === '[data-pl-bulk-maker-pick]') return boxes.slice();
      return [];
    },
  },
  console,
  CSS: { escape: (s) => String(s) },
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const api = sandbox.window.ProductLedgerCsv && sandbox.window.ProductLedgerCsv.__test;
if (!api) { console.error('missing __test'); process.exit(2); }

const st = api.ensureState();
st.busy = false;
st.bulkMakerLookup = {
  running: false,
  finished: true,
  done: 2,
  total: 2,
  adoptedCount: 0,
  counts: {ok: 0, multiple: 2, needsConfirm: 0, fetchFailed: 0, pageUnavailable: 0, error: 0, noMatch: 0, cancelled: 0, pending: 0},
  adoptSelected: {'SKU-A#0': true, 'SKU-A#1': false, 'SKU-B#0': false},
  results: [
    {
      searchKey: 'SKU-A',
      status: 'multiple',
      adopted: false,
      candidates: [{name: 'A1'}, {name: 'A2'}],
    },
    {
      searchKey: 'SKU-B',
      status: 'multiple',
      adopted: false,
      candidates: [{name: 'B1'}],
    },
  ],
};

api.syncBulkMakerAdoptSelectionUi(st);
const out = {
  adoptDisabled: adoptBtn.disabled,
  meta: meta.textContent,
  checks: boxes.map(b => ({
    key: b.getAttribute('data-pl-bulk-maker-pick'),
    checked: b.checked,
  })),
  keyA0: api.bulkMakerAdoptSelectionKey({searchKey: 'SKU-A'}, 0),
  remaining: api.bulkMakerRemainingConfirmCount(st.bulkMakerLookup),
};

// 同一商品の別候補へ付け替え（状態だけ更新→軽量同期）
st.bulkMakerLookup.adoptSelected['SKU-A#0'] = false;
st.bulkMakerLookup.adoptSelected['SKU-A#1'] = true;
api.syncBulkMakerAdoptSelectionUi(st);
out.afterReselect = {
  adoptDisabled: adoptBtn.disabled,
  meta: meta.textContent,
  checks: boxes.map(b => ({
    key: b.getAttribute('data-pl-bulk-maker-pick'),
    checked: b.checked,
  })),
  remaining: api.bulkMakerRemainingConfirmCount(st.bulkMakerLookup),
};

// 全解除
st.bulkMakerLookup.adoptSelected = {'SKU-A#0': false, 'SKU-A#1': false, 'SKU-B#0': false};
api.syncBulkMakerAdoptSelectionUi(st);
out.afterClear = {
  adoptDisabled: adoptBtn.disabled,
  meta: meta.textContent,
  checks: boxes.map(b => b.checked),
};

// 進捗テキストのみ（構造は同じ）
st.bulkMakerLookup.done = 2;
st.bulkMakerLookup.counts.multiple = 2;
const sig1 = api.bulkMakerStructureSignature(st.bulkMakerLookup);
st.bulkMakerLookup.done = 2;
st.bulkMakerLookup.currentMakerSku = 'X';
const sig2 = api.bulkMakerStructureSignature(st.bulkMakerLookup);
api.patchBulkMakerProgressTexts(st);
out.progressPatch = {
  sameStructure: sig1 === sig2,
  progress: progress.textContent,
  counts: counts.textContent,
  meta: meta.textContent,
};

// 結果が増えたら構造シグネチャが変わる
const grew = Object.assign({}, st.bulkMakerLookup, {
  results: st.bulkMakerLookup.results.concat([{
    searchKey: 'SKU-C', status: 'ok', adopted: false, candidates: [{name: 'C1'}],
  }]),
});
out.structureChangesOnNewResult = api.bulkMakerStructureSignature(st.bulkMakerLookup)
  !== api.bulkMakerStructureSignature(grew);

process.stdout.write(JSON.stringify(out));
'''


class MakerScrollPreserveSourceTest(unittest.TestCase):
    def test_check_does_not_paint_dialog(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('function syncBulkMakerAdoptSelectionUi', js)
        self.assertIn('function refreshBulkMakerPanelUi', js)
        self.assertIn('function patchBulkMakerProgressTexts', js)
        self.assertIn('makerResults', js)
        self.assertIn('preventScroll', js)
        self.assertIn('id="plBulkMakerAdoptMeta"', js)
        self.assertIn('id="plBulkMakerProgress"', js)
        # 候補チェックは paintDialog ではなく軽量同期
        self.assertIn('チェックだけでは採用・再照合・登録しない', js)
        bind = js.split('function bindBulkMakerPanelHandlers(')[1]
        pick_handler = bind.split("document.querySelectorAll('[data-pl-bulk-maker-pick]')")[1].split(
            "document.querySelectorAll('.pl-bulk-maker-panel [data-pl-maker-url-input]')"
        )[0]
        self.assertIn('syncBulkMakerAdoptSelectionUi(st)', pick_handler)
        self.assertNotIn('paintDialog', pick_handler)
        # 進捗 poll は全ダイアログ再描画しない
        poll = js.split('async function pollBulkMakerLookup(')[1].split('async function cancelBulkMakerLookup(')[0]
        self.assertIn('patchBulkMakerProgressTexts', poll)
        self.assertIn('refreshBulkMakerPanelUi', poll)
        self.assertNotIn('paintDialog()', poll)


class MakerScrollPreserveHarnessTest(unittest.TestCase):
    def test_sync_updates_checks_button_and_meta(self):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as hf:
            hf.write(NODE_HARNESS)
            harness = hf.name
        try:
            proc = subprocess.run(
                ['node', harness, str(JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            Path(harness).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out['keyA0'], 'SKU-A#0')
        self.assertFalse(out['adoptDisabled'])
        self.assertEqual(out['remaining'], 1)  # SKU-B 未選択
        self.assertIn('確認残り 1', out['meta'])
        by_key = {c['key']: c['checked'] for c in out['checks']}
        self.assertTrue(by_key['SKU-A#0'])
        self.assertFalse(by_key['SKU-A#1'])
        self.assertFalse(by_key['SKU-B#0'])

        after = out['afterReselect']
        self.assertFalse(after['adoptDisabled'])
        by_key2 = {c['key']: c['checked'] for c in after['checks']}
        self.assertFalse(by_key2['SKU-A#0'])
        self.assertTrue(by_key2['SKU-A#1'])
        self.assertEqual(after['remaining'], 1)

        self.assertTrue(out['afterClear']['adoptDisabled'])
        self.assertTrue(all(not c for c in out['afterClear']['checks']))
        self.assertIn('確認残り 2', out['afterClear']['meta'])

        self.assertTrue(out['progressPatch']['sameStructure'])
        self.assertTrue(out['structureChangesOnNewResult'])


if __name__ == '__main__':
    unittest.main()
