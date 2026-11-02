"""旧CSV一括取り込みを途中で閉じても入り直せる（JS単体、DB不使用）。"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / 'static' / 'product_ledger_csv.js'

HARNESS = r'''
const fs = require('fs'); const vm = require('vm');
const sandbox = {window: {}, document: {getElementById(){return null}, querySelector(){return null}, querySelectorAll(){return []}},
  console, CSS: {escape: s => String(s)}, $: () => null};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
const P = sandbox.window.ProductLedgerCsv; const t = P.__test; const st = t._state();
const out = {};
const opts = {api: async () => ({}), toast: () => {}};
// 1) 確認途中（照合済み）で閉じて再表示 → 続きから
P.open(opts);
st.importMode = 'products'; st.productPhase = 'goods'; st.fileB64 = 'eA=='; st.preview = {rows: []};
st.skuDecisions = {A: {decision: 'x'}}; st.open = false;
P.open(opts);
out.resumeMode = st.importMode; out.resumeKept = !!st.skuDecisions.A && !!st.preview;
// 2) 通信なしで busy が残っていても解除
st.busy = true; st.open = false; P.open(opts);
out.staleBusy = st.busy;
// 3) 登録完了後は段階選択から、ボタンは押せる
st.result = {ok: true}; st.open = false; P.open(opts);
out.chooseMode = st.importMode; out.chooserDisabled = /disabled/.test(t.modeChooserHtml(st));
// 4) 処理中で閉じた場合は理由を表示
const busyHtml = t.modeChooserHtml({busy: true});
out.busyNote = busyHtml.includes('前の処理中です');
console.log(JSON.stringify(out));
'''


class ReopenDialogTest(unittest.TestCase):
    def test_reopen(self):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
            f.write(HARNESS)
        r = subprocess.run(['node', f.name, str(JS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out['resumeMode'], 'products')
        self.assertTrue(out['resumeKept'])
        self.assertFalse(out['staleBusy'])
        self.assertFalse(out['chooserDisabled'])
        self.assertTrue(out['busyNote'])


if __name__ == '__main__':
    unittest.main()
