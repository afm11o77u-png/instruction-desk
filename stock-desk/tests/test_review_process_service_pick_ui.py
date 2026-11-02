"""確認リスト（商品）: 加工は登録済みサービスの選択のみ、自由入力なし。"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'static' / 'product_ledger_csv.js'

HARNESS = r'''
const fs=require('fs'),vm=require('vm');
const code=fs.readFileSync(process.argv[1],'utf8');
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sb={window:{},document:{getElementById(){return null},querySelector(){return null},querySelectorAll(){return []}},console,CSS:{escape:s=>String(s)},esc,$:()=>null};
vm.createContext(sb);vm.runInContext(code,sb);
const t=sb.window.ProductLedgerCsv.__test;
const st={preview:{processServices:[{id:1,name:'刺繍加工'},{id:2,name:'ライン加工'}]}};
const r={rowNo:5};
console.log(JSON.stringify({
  auto:t.processServicePickerHtml(st,r,{},'刺繍加工',''),
  memo:t.processServicePickerHtml(st,r,{},'左胸ワンポイント',''),
  chosen:t.processServicePickerHtml(st,r,{processServiceIds:[2]},'刺繍加工',''),
  none:t.processServicePickerHtml({preview:{processServices:[]}},r,{},'',''),
}));
'''


@unittest.skipUnless(shutil.which('node'), 'node required')
class PickerUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run(['node', '-e', HARNESS, str(JS)], capture_output=True, text=True, check=True)
        cls.o = json.loads(out.stdout)

    def test_no_free_text_product_processing_input(self):
        src = JS.read_text(encoding='utf-8')
        self.assertNotIn('<label>必要な加工・仕様<input', src)
        for html in self.o.values():
            self.assertNotIn('type="text"', html)
            self.assertNotIn('data-pl-paren-proc', html)

    def test_matching_text_prechecks_service(self):
        h = self.o['auto']
        self.assertIn('value="1" data-name="刺繍加工" checked', h)
        self.assertNotIn('value="2" data-name="ライン加工" checked', h)
        self.assertNotIn('仕様メモ', h)

    def test_unmatched_text_is_read_only_memo(self):
        self.assertIn('仕様メモ（原文）：左胸ワンポイント', self.o['memo'])
        self.assertNotIn('checked', self.o['memo'])

    def test_explicit_choice_wins(self):
        h = self.o['chosen']
        self.assertIn('data-name="ライン加工" checked', h)
        self.assertNotIn('data-name="刺繍加工" checked', h)

    def test_no_services_note(self):
        self.assertIn('登録済みの加工サービスはありません', self.o['none'])


if __name__ == '__main__':
    unittest.main()
