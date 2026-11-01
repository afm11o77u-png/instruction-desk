"""まとめる選択時の簡易フォーム（代表名・色・通常仕入先）と「その他の詳細」折りたたみ。一時DBのみ。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ledger_supplier_pick_ui import JS, PLAN_HARNESS, csv_b64_from_text, newdb, pli, tiny_ledger_rows

HARNESS = PLAN_HARNESS.split('const cases = JSON.parse')[0] + r'''
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const out = {};
for (const [name, st] of Object.entries(cases)) {
  const g = st.preview.skuGroups[0];
  out[name] = api.skuRowDetailHtml(st, g, { colspan: 5 });
}
process.stdout.write(JSON.stringify(out));
'''


def state(preview, decisions):
    return {
        'preview': {k: preview.get(k) or ([] if k in ('skuGroups', 'rows', 'autoMergePlans') else {}) for k in ('skuGroups', 'rows', 'autoMergePlans', 'plannedCounts', 'outcomeSummary')},
        'skuDecisions': decisions, 'appliedSkuDecisions': {}, 'rowClassifications': {},
        'selections': {}, 'selectedCodes': {}, 'expandedCodes': {'6801-014': True},
        'expandedUnified': {}, 'sectionOpen': {'unified': True}, 'busy': False,
    }


class MergeQuickFormTest(unittest.TestCase):
    def render(self, cases):
        with tempfile.TemporaryDirectory() as d:
            cp = Path(d) / 'cases.json'; hp = Path(d) / 'h.js'
            cp.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
            hp.write_text(HARNESS, encoding='utf-8')
            p = subprocess.run(['node', str(hp), str(JS), str(cp)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout)

    def test_merge_shows_three_fields_and_collapses_rest(self):
        folder, path, c = newdb()
        try:
            text = tiny_ledger_rows([
                '顧客A,アイトス㈱,6801-014,コックコート（白）,L',
                '顧客A,㈱セブンユニフォーム,6801-014,コックコート,M',
            ])
            preview = pli.build_preview(c, {'file': csv_b64_from_text(text), 'name': 'x.csv', 'importMode': 'products', '_canWrite': True, '_actor': {'name': 't'}})
            out = self.render({
                'merge': state(preview, {'6801-014': {'decision': 'merge'}}),
                'none': state(preview, {}),
            })
            html = out['merge']
            quick, _, rest = html.partition('<details class="sku-more-details"')
            self.assertIn('data-pl-merge-quick', quick)
            for attr in ('data-pl-official-name-edit', 'data-pl-official-color', 'data-pl-primary-sup'):
                self.assertEqual(html.count(attr + '='), 1, attr)  # 重複なし（同じ state を編集）
                self.assertIn(attr + '=', quick)
            self.assertIn('選択してください', quick)
            self.assertIn('【選択が必要】', quick)  # 仕入先が未定
            self.assertIn('<summary>その他の詳細</summary>', rest)
            for hidden in ('登録プレビュー', '公式商品名', '顧客:'):
                self.assertNotIn(hidden, quick)
                self.assertIn(hidden, rest)
            self.assertNotRegex(rest, r'<details[^>]*\bopen\b')
            self.assertNotIn('data-pl-merge-quick', out['none'])
            self.assertNotIn('その他の詳細', out['none'])
        finally:
            c.close(); folder.cleanup()


if __name__ == '__main__':
    unittest.main()
