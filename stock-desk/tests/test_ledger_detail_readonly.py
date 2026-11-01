"""「詳細を開く」は閲覧専用、入力欄は方針選択後のみ。別注など方針ボタンの無い行は入力欄を直接表示（合成データのみ）。"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ledger_supplier_pick_ui import JS, PLAN_HARNESS
from test_ledger_split_quick_form import CODE, group, rows, state

HARNESS = PLAN_HARNESS.split('const cases = JSON.parse')[0] + r'''
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const out = {};
for (const [name, c] of Object.entries(cases)) {
  const g = c.st.preview.skuGroups[0];
  const item = Object.assign({}, c.item, c.item.kind === 'sku' ? { group: g } : {});
  out[name] = {
    detail: c.item.kind === 'sku' ? api.skuRowDetailHtml(c.st, g, { colspan: 5 }) : '',
    rows: api.unifiedRowDetailHtml(c.st, item),
  };
}
process.stdout.write(JSON.stringify(out));
'''

INPUT = re.compile(r'<(input|select|textarea)\b')


class DetailReadOnlyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sku = {'id': 'sku:' + CODE, 'kind': 'sku', 'code': CODE, 'rowNos': [1, 2]}
        custom_row = {'rowNo': 9, 'csv': {'code': '別注', 'productName': '別注ベスト', 'customer': '顧客C', 'size': 'F', 'supplier': 'C社'}, 'reasons': ['別注']}
        custom_st = state(group(), '')
        custom_st['skuDecisions'] = {}
        custom_st['preview']['rows'].append(custom_row)
        none_st = state(group(), '')
        none_st['skuDecisions'] = {}
        cases = {
            'none': {'st': none_st, 'item': sku},
            'merge': {'st': state(group(), 'merge'), 'item': sku},
            'hold': {'st': state(group(), 'hold'), 'item': sku},
            'parts': {'st': state(group(), 'separate', {'v1': {'approach': 'as_parts'}}), 'item': sku},
            'custom': {'st': custom_st, 'item': {'id': 'row:9', 'kind': 'custom', 'rows': [custom_row], 'rowNos': [9]}},
        }
        with tempfile.TemporaryDirectory() as d:
            cp = Path(d) / 'c.json'; hp = Path(d) / 'h.js'
            cp.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
            hp.write_text(HARNESS, encoding='utf-8')
            p = subprocess.run(['node', str(hp), str(JS), str(cp)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr
            cls.out = json.loads(p.stdout)

    def test_no_decision_is_read_only(self):
        o = self.out['none']
        self.assertIsNone(INPUT.search(o['detail']), o['detail'])
        self.assertIsNone(INPUT.search(o['rows']), o['rows'])
        self.assertIn('data-pl-sku-dec=', o['detail'])  # 方針ボタンは表示
        self.assertIn('顧客A', o['detail'])  # 根拠は表示
        self.assertIn('原文', o['rows'])
        self.assertNotIn('この行を登録しない', o['rows'])

    def test_merge_shows_inputs(self):
        o = self.out['merge']
        self.assertIn('data-pl-primary-sup=', o['detail'])
        self.assertIn('data-pl-official-name-edit=', o['detail'])
        rest = o['rows'].split('<details class="sku-more-details"', 1)[1]
        self.assertIn('data-pl-paren-name=', rest)  # 行ごとの編集は折りたたみ内

    def test_hold_keeps_inputs(self):
        self.assertIn('data-pl-defer-reason=', self.out['hold']['detail'])
        self.assertIn('data-pl-paren-name=', self.out['hold']['rows'])

    def test_separate_parts_fields_in_compact_form(self):
        o = self.out['parts']
        quick = o['detail'].split('<details class="sku-more-details"', 1)[0]
        self.assertIn('data-pl-paren-name="1"', quick)
        self.assertIn('仕入先', quick)
        self.assertEqual((o['detail'] + o['rows']).count('data-pl-paren-name="1"'), 1)  # 複製なし
        self.assertIn('data-pl-paren-name="2"', o['rows'])

    def test_custom_row_shows_inputs_directly(self):
        r = self.out['custom']['rows']
        self.assertNotIn('sku-more-details', r)
        self.assertIn('data-pl-row-quick="9"', r)
        quick = r.split('data-pl-row-quick="9"', 1)[1]
        for a in ('data-pl-paren-name="9"', 'data-pl-paren-color="9"', 'data-pl-row-exclude="9"'):
            self.assertIn(a, quick)
        info = r.split('data-pl-row-quick', 1)[0]
        self.assertIn('別注ベスト', info)
        self.assertIsNone(INPUT.search(info))


if __name__ == '__main__':
    unittest.main()
