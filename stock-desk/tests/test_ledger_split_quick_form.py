"""色別／内容別／個別整理の選択時も簡易フォーム＋「その他の詳細」折りたたみ（合成データのみ）。"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ledger_supplier_pick_ui import JS, PLAN_HARNESS

HARNESS = PLAN_HARNESS.split('const cases = JSON.parse')[0] + r'''
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const out = {};
for (const [name, c] of Object.entries(cases)) {
  const g = c.st.preview.skuGroups[0];
  out[name] = {
    detail: api.skuRowDetailHtml(c.st, g, { colspan: 5 }),
    rows: c.item ? api.unifiedRowDetailHtml(c.st, Object.assign({}, c.item, { group: g })) : '',
  };
}
process.stdout.write(JSON.stringify(out));
'''

CODE = 'SD5000AM-5U'


def group(**extra):
    g = {
        'code': CODE, 'reviewTier': 'individual', 'existingCount': 0, 'existingProducts': [],
        'nameCandidates': ['ポロシャツ', 'ポロシャツ 刺繍'], 'supplierCandidates': ['A社', 'B社'],
        'variants': [
            {'key': 'v1', 'productName': 'ポロシャツ', 'supplier': 'A社', 'sizes': ['M'], 'customers': ['顧客A'], 'rowNos': [1]},
            {'key': 'v2', 'productName': 'ポロシャツ 刺繍', 'supplier': 'B社', 'sizes': ['L'], 'customers': ['顧客B'], 'rowNos': [2]},
        ],
        'rowNos': [1, 2], 'namePlan': {'adoptionRule': '最多の名称'},
    }
    g.update(extra)
    return g


def rows():
    return [{'rowNo': n, 'csv': {'code': CODE, 'productName': 'ポロシャツ', 'customer': '顧客', 'size': 'M', 'supplier': 'A社'}, 'reasons': []} for n in range(1, 6)]


def state(g, decision, plans=None):
    d = {'decision': decision}
    if plans:
        d['plans'] = plans
    return {
        'preview': {'skuGroups': [g], 'rows': rows(), 'autoMergePlans': [], 'plannedCounts': {}, 'outcomeSummary': {}},
        'skuDecisions': {CODE: d}, 'appliedSkuDecisions': {CODE: d}, 'rowClassifications': {}, 'selections': {},
        'selectedCodes': {}, 'expandedCodes': {CODE: True}, 'expandedUnified': {}, 'sectionOpen': {}, 'busy': False,
    }


CONTENT = {'complete': False, 'note': '内容ごとの説明文', 'unassignedRows': [], 'plans': [
    {'contentKey': 'k1', 'kind': 'product', 'productName': 'ポロシャツ', 'color': '白', 'internalCode': 'SD5000AM-5U',
     'needsCollisionChoice': True, 'existingProducts': [{'id': 7, 'name': '既存'}], 'rowNos': [1]},
    {'contentKey': 'k2', 'kind': 'process_service', 'serviceName': '刺繍', 'vendorName': 'B社', 'notes': '左胸', 'rowNos': [2]},
]}
COLOR = {'complete': True, 'unassignedRows': [], 'plans': [
    {'colorKey': '白', 'color': '白', 'productName': 'ポロシャツ', 'internalCode': 'SD-白', 'rowNos': [1], 'sizes': ['M']},
    {'colorKey': '黒', 'color': '黒', 'productName': 'ポロシャツ', 'internalCode': 'SD-黒', 'rowNos': [2], 'sizes': ['L']},
]}


class SplitQuickFormTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        item = {'id': 'sku:' + CODE, 'kind': 'sku', 'code': CODE, 'rowNos': [1, 2, 3, 4, 5]}
        cases = {
            'content': {'st': state(group(contentSplitPlans=CONTENT), 'content_split'), 'item': item},
            'color': {'st': state(group(colorSplitPlans=COLOR), 'color_split')},
            'separate': {'st': state(group(), 'separate', {'v1': {'approach': 'as_process_service', 'serviceName': '刺繍'}}), 'item': item},
            'hold': {'st': state(group(), 'hold'), 'item': item},
        }
        with tempfile.TemporaryDirectory() as d:
            cp = Path(d) / 'c.json'; hp = Path(d) / 'h.js'
            cp.write_text(json.dumps(cases, ensure_ascii=False), encoding='utf-8')
            hp.write_text(HARNESS, encoding='utf-8')
            p = subprocess.run(['node', str(hp), str(JS), str(cp)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr
            cls.out = json.loads(p.stdout)

    def split(self, name):
        html = self.out[name]['detail']
        quick, sep, rest = html.partition('<details class="sku-more-details"')
        self.assertTrue(sep, name)
        self.assertIn('data-pl-merge-quick', quick)
        self.assertIn('<summary>その他の詳細</summary>', rest)
        return html, quick, rest

    def once_in_quick(self, html, quick, attrs):
        for a in attrs:
            n = html.count(a + '=')
            self.assertGreaterEqual(n, 1, a)
            self.assertEqual(n, quick.count(a + '='), a)  # すべて簡易フォーム内（複製なし）

    def test_content_split(self):
        html, quick, rest = self.split('content')
        self.once_in_quick(html, quick, ['data-pl-content-internal', 'data-pl-content-name', 'data-pl-content-color',
                                         'data-pl-content-service', 'data-pl-content-vendor', 'data-pl-content-keep', 'data-pl-primary-sup'])
        self.assertEqual(html.count('data-pl-content-internal='), 1)
        # 衝突メッセージは社内管理品番の直後
        i = quick.index('data-pl-content-internal=')
        self.assertLess(quick.index('【選択が必要】'), i)
        self.assertLess(i, quick.index('の確認が必要です'))
        self.assertLess(quick.index('の確認が必要です'), quick.index('data-pl-content-name='))
        self.assertIn('data-pl-content-notes=', rest)
        for hidden in ('内容ごとの説明文', '名称採用ルール', '顧客:'):
            self.assertNotIn(hidden, quick)
        rowsHtml = self.out['content']['rows']
        self.assertIn('<details class="sku-more-details"', rowsHtml)
        self.assertLess(rowsHtml.index('<summary>'), rowsHtml.index('対象内の行だけを編集'))

    def test_color_split(self):
        html, quick, rest = self.split('color')
        self.once_in_quick(html, quick, ['data-pl-color-internal', 'data-pl-color-name', 'data-pl-color-color', 'data-pl-primary-sup'])
        self.assertEqual(html.count('data-pl-color-internal='), 2)
        self.assertIn('先頭ゼロも維持', rest)
        self.assertNotIn('先頭ゼロも維持', quick)

    def test_separate(self):
        html, quick, rest = self.split('separate')
        self.once_in_quick(html, quick, ['data-pl-sep-approach', 'data-pl-sep-service', 'data-pl-sep-vendor'])
        self.assertIn('data-pl-sep-supp=', rest)
        self.assertNotIn('data-pl-sep-supp=', quick)
        self.assertIn('【選択が必要】', quick)  # v2 は整理方法が未選択
        self.assertIn('同じ通常品番のまま', rest)

    def test_hold_unchanged(self):
        self.assertNotIn('data-pl-merge-quick', self.out['hold']['detail'])
        self.assertNotIn('その他の詳細', self.out['hold']['detail'])
        self.assertNotIn('sku-more-details', self.out['hold']['rows'])


if __name__ == '__main__':
    unittest.main()
