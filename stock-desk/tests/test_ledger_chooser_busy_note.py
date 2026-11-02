"""段階選択：処理中は『読み込み中…』を出し、終われば押せる（JS単体、DB不使用）。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parents[1] / 'static' / 'product_ledger_csv.js'
HARNESS = r'''
const fs = require('fs'); const vm = require('vm');
const sb = {window: {}, document: {getElementById(){return null}, querySelector(){return null}, querySelectorAll(){return []}},
  console, CSS: {escape: s => String(s)}, $: () => null};
vm.createContext(sb); vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sb);
const t = sb.window.ProductLedgerCsv.__test;
const busy = t.modeChooserHtml({busy: true}), idle = t.modeChooserHtml({busy: false});
console.log(JSON.stringify({busyNote: busy.includes('読み込み中…'), busyDisabled: /disabled/.test(busy),
  idleNote: idle.includes('読み込み中…'), idleDisabled: /disabled/.test(idle)}));
'''


class ChooserBusyNoteTest(unittest.TestCase):
    def test_note(self):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
            f.write(HARNESS)
        r = subprocess.run(['node', f.name, str(JS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out, {'busyNote': True, 'busyDisabled': True, 'idleNote': False, 'idleDisabled': False})


if __name__ == '__main__':
    unittest.main()
