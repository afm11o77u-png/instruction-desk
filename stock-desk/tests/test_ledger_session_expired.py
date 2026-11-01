"""旧CSV取り込み: ログイン切れ時の表示と選択の保持（JS のみ）。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ledger_content_split_register import JS, NODE_HARNESS

TAIL = r'''
const store = {};
sandbox.localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; }};
const e401 = Object.assign(Error('ログインしてください。'), {status: 401, payload: {error: 'ログインしてください。'}});
const msg = api.apiErrorMessage(e401, '照合に失敗しました。');
const st = {fileName: 'a.csv', fileSize: 10, skuDecisions: {fabric: {decision: 'content_split'}}, selections: {}, rowClassifications: {'8848': {category: 'parts'}}, rowExclusions: {}};
api.saveDraft(st);
const st2 = {fileName: 'a.csv', fileSize: 10, skuDecisions: {}, selections: {}, rowClassifications: {}, rowExclusions: {}};
const restored = api.restoreDraft(st2);
const other = api.restoreDraft({fileName: 'b.csv', fileSize: 10});
api.clearDraft(st2);
const afterClear = api.restoreDraft({fileName: 'a.csv', fileSize: 10, skuDecisions: {}});
console.log(JSON.stringify({msg, restored, dec: st2.skuDecisions, rc: st2.rowClassifications, other, afterClear}));
'''


class SessionExpiredTest(unittest.TestCase):
    def test_message_and_draft(self):
        head = NODE_HARNESS.split('function baseSt()')[0]
        # localStorage はスクリプト評価前に用意
        head = head.replace('vm.createContext(sandbox);',
                            'sandbox.localStorage = null;\nvm.createContext(sandbox);')
        with tempfile.TemporaryDirectory() as td:
            h = Path(td) / 'h.js'
            cp = Path(td) / 'c.json'
            cp.write_text('{}', encoding='utf-8')
            h.write_text(head + TAIL, encoding='utf-8')
            out = subprocess.run(['node', str(h), str(JS), str(cp)],
                                 capture_output=True, text=True, check=True)
        r = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertIn('ログインが切れました', r['msg'])
        self.assertTrue(r['restored'])
        self.assertEqual(r['dec'], {'fabric': {'decision': 'content_split'}})
        self.assertEqual(r['rc'], {'8848': {'category': 'parts'}})
        self.assertFalse(r['other'])
        self.assertFalse(r['afterClear'])


if __name__ == '__main__':
    unittest.main()
