"""旧CSV品番チェックの連続選択パフォーマンス（UIのみ。実データ検索・登録なし）。"""
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
    getAttribute(k) { return this.attrs[k] == null ? null : String(this.attrs[k]); },
    setAttribute(k, v) { this.attrs[k] = String(v); },
  };
  return o;
}

const boxes = [
  el('input', {'data-pl-sku-check': 'A001'}, {checked: false}),
  el('input', {'data-pl-sku-check': 'A001'}, {checked: false}), // 同一品番の別箇所
  el('input', {'data-pl-sku-check': 'B002'}, {checked: false}),
  el('input', {'data-pl-sku-check': 'C003'}, {checked: true}),
];
const countEl = el('span', {id: 'plSkuSelCount'}, {textContent: '選択中 1 品番（開いている区分の表示ページ／個別チェック）'});
const clearSel = el('button', {id: 'plSkuClearSel'}, {disabled: false});
const bulkHold = el('button', {id: 'plSkuBulkHold'}, {disabled: false});
const bulkMaker = el('button', {id: 'plBulkMakerFetch'}, {disabled: false});
const byId = {
  plSkuSelCount: countEl,
  plSkuClearSel: clearSel,
  plSkuBulkHold: bulkHold,
  plBulkMakerFetch: bulkMaker,
};

const sandbox = {
  window: {},
  document: {
    getElementById(id) { return byId[id] || null; },
    querySelector() { return null; },
    querySelectorAll(sel) {
      if (sel === '[data-pl-sku-check]') return boxes.slice();
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
st.selectedCodes = {A001: true, B002: false, C003: true};
st.busy = false;
st.bulkMakerLookup = null;
api.syncSkuSelectionUi(st);

const out = {
  countText: countEl.textContent,
  clearDisabled: clearSel.disabled,
  holdDisabled: bulkHold.disabled,
  makerDisabled: bulkMaker.disabled,
  checks: boxes.map(b => ({code: b.getAttribute('data-pl-sku-check'), checked: b.checked})),
  selectedList: api.selectedCodeList(st),
};

// 全解除
st.selectedCodes = {};
api.syncSkuSelectionUi(st);
out.afterClear = {
  countText: countEl.textContent,
  clearDisabled: clearSel.disabled,
  holdDisabled: bulkHold.disabled,
  makerDisabled: bulkMaker.disabled,
  checks: boxes.map(b => ({code: b.getAttribute('data-pl-sku-check'), checked: b.checked})),
};

// busy 中はボタン無効のまま（選択件数があっても）
st.selectedCodes = {A001: true};
st.busy = true;
api.syncSkuSelectionUi(st);
out.whenBusy = {
  clearDisabled: clearSel.disabled,
  holdDisabled: bulkHold.disabled,
  makerDisabled: bulkMaker.disabled,
  a001Checked: boxes.filter(b => b.getAttribute('data-pl-sku-check') === 'A001').every(b => b.checked),
};

process.stdout.write(JSON.stringify(out));
'''


class CheckboxSelectionPerfSourceTest(unittest.TestCase):
    def test_lightweight_selection_wiring(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('function syncSkuSelectionUi', js)
        self.assertIn('id="plSkuSelCount"', js)
        self.assertIn('syncSkuSelectionUi(st)', js)
        # 個別チェックは paintDialog ではなく軽量同期
        self.assertIn("st.selectedCodes[code]=!!el.checked", js)
        self.assertIn('チェックだけでは検索・再照合・承認・登録を開始しない', js)
        # ページ選択・選択解除も軽量同期
        self.assertIn('一覧・詳細は作り直さない', js)
        # 旧: チェックごとに paintDialog({anchorCode:code}) — bindDialog 内の onchange のみ検査
        bind = js.split('function bindDialog(')[1]
        check_handler = bind.split("document.querySelectorAll('[data-pl-sku-check]')")[1].split(
            "document.querySelectorAll('[data-pl-sku-expand]')"
        )[0]
        self.assertIn('syncSkuSelectionUi(st)', check_handler)
        self.assertNotIn('paintDialog', check_handler)
        clear_handler = bind.split("const clearSel=$('#plSkuClearSel')")[1].split(
            "const bulkMakerFetch=$('#plBulkMakerFetch')"
        )[0]
        self.assertIn('syncSkuSelectionUi(st)', clear_handler)
        self.assertNotIn('paintDialog', clear_handler)
        sel_handler = bind.split("const selPage=$('#plSkuSelectPage')")[1].split(
            "const clearSel=$('#plSkuClearSel')"
        )[0]
        self.assertIn('syncSkuSelectionUi(st)', sel_handler)
        self.assertNotIn('paintDialog', sel_handler)


class CheckboxSelectionPerfHarnessTest(unittest.TestCase):
    def test_sync_updates_count_buttons_and_duplicate_checks(self):
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
        self.assertEqual(out['countText'], '選択中 2 品番（開いている区分の表示ページ／個別チェック）')
        self.assertFalse(out['clearDisabled'])
        self.assertFalse(out['holdDisabled'])
        self.assertFalse(out['makerDisabled'])
        self.assertEqual(
            sorted(out['selectedList']),
            ['A001', 'C003'],
        )
        # 同一品番 A001 の2箇所がどちらも checked
        a001 = [c for c in out['checks'] if c['code'] == 'A001']
        self.assertEqual(len(a001), 2)
        self.assertTrue(all(c['checked'] for c in a001))
        self.assertFalse(next(c for c in out['checks'] if c['code'] == 'B002')['checked'])
        self.assertTrue(next(c for c in out['checks'] if c['code'] == 'C003')['checked'])

        self.assertEqual(out['afterClear']['countText'], '選択中 0 品番（開いている区分の表示ページ／個別チェック）')
        self.assertTrue(out['afterClear']['clearDisabled'])
        self.assertTrue(out['afterClear']['holdDisabled'])
        self.assertTrue(out['afterClear']['makerDisabled'])
        self.assertTrue(all(not c['checked'] for c in out['afterClear']['checks']))

        self.assertTrue(out['whenBusy']['clearDisabled'])
        self.assertTrue(out['whenBusy']['holdDisabled'])
        self.assertTrue(out['whenBusy']['makerDisabled'])
        self.assertTrue(out['whenBusy']['a001Checked'])


if __name__ == '__main__':
    unittest.main()
