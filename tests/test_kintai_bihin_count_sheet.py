"""備品棚卸 記入用紙印刷: HIQ展開・空数量・非保存・ページ分割契約。"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'kintai-bihin.js').read_text(encoding='utf-8')
CSS = (ROOT / 'style.css').read_text(encoding='utf-8')
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')


def _node_eval_helpers() -> str:
    """kintai-bihin.js から印刷ヘルパーを抜き、Node で検証する。"""
    # IIFE 内の関数をテスト用に切り出す（DOM / window 非依存部分）
    extract = r'''
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
'''
    # ソースから対象関数本体を正規表現で取得
    names = [
        'isHiqColorLabel',
        'formatPrintPrice',
        'expandCountSheetRows',
        'countSheetTitleYm',
        'buildCountSheetHtml',
    ]
    chunks = [extract]
    for name in names:
        m = re.search(
            rf'(function {name}\([^)]*\)\s*\{{)',
            JS,
        )
        if not m:
            raise AssertionError(f'missing function {name}')
        start = m.start()
        # brace match
        i = JS.find('{', m.start())
        depth = 0
        j = i
        while j < len(JS):
            if JS[j] == '{':
                depth += 1
            elif JS[j] == '}':
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        chunks.append(JS[start:j])
    return '\n'.join(chunks)


class BihinCountSheetUiContractTest(unittest.TestCase):
    def test_print_button_and_dialog(self):
        self.assertIn('id="bihinprint"', INDEX)
        self.assertIn('棚卸用紙を印刷', INDEX)
        self.assertIn('id="bihinprintdialog"', INDEX)
        self.assertIn('id="bihincountsheet"', INDEX)
        self.assertIn('ヘッダー／フッター', INDEX)
        self.assertIn('A4・縦向き', INDEX)
        self.assertIn('保存はしません', INDEX)

    def test_print_css_isolates_and_portrait(self):
        self.assertIn('printing-bihin-count', CSS)
        compact = CSS.replace(' ', '').replace('\n', '')
        self.assertIn('@pagebihin-count-sheet{size:A4portrait', compact)
        self.assertIn('body.printing-bihin-count>#bihincountsheet{', compact)
        self.assertIn('display:block!important', compact)
        # 緑区切り（見本 #355c4c）
        self.assertIn('2.6pt solid #355c4c', CSS)
        self.assertIn('bihin-count-hiq-total', CSS)
        self.assertIn('break-inside:avoid', CSS)
        self.assertIn('page-break-inside:avoid', CSS)
        # シフト印刷クラスと混線しない
        self.assertNotIn('printing-bihin-count .shift-grid', CSS)

    def test_js_print_does_not_save(self):
        self.assertIn('runBihinCountPrint', JS)
        self.assertIn('保存データは書き換えない', JS)
        self.assertIn('save も呼ばない', JS)
        self.assertIn('printing-bihin-count', JS)
        self.assertIn('@page{size:A4 portrait;margin:12mm}', JS)
        self.assertIn('ensureBihinPortraitPage', JS)
        self.assertIn('clearBihinPortraitPage', JS)
        self.assertIn('afterprint', JS)
        # runBihinCountPrint 本体に saveBihin / POST が無いこと
        m = re.search(r'function runBihinCountPrint\(\)\s*\{', JS)
        self.assertIsNotNone(m)
        start = m.start()
        i = JS.find('{', start)
        depth = 0
        j = i
        while j < len(JS):
            if JS[j] == '{':
                depth += 1
            elif JS[j] == '}':
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        body = JS[start:j]
        self.assertNotIn('saveBihin', body)
        self.assertNotIn("api('/api/kintai/bihin'", body)
        self.assertNotIn('method: \'POST\'', body)
        self.assertIn('window.print()', body)
        # 報告UIの1行構造は維持（色分割は印刷HTMLのみ）
        self.assertIn('paintBihin', JS)
        self.assertNotIn('赤', (ROOT / 'kintai-bihin.js').read_text(encoding='utf-8')[
            JS.index('function paintBihin'): JS.index('function api(')
        ])


class BihinCountSheetLogicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helpers = _node_eval_helpers()
        cls.helpers = helpers

    def _run(self, script: str) -> str:
        full = self.helpers + '\n' + script
        r = subprocess.run(
            ['node', '-e', full],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            self.fail(r.stderr or r.stdout or 'node failed')
        return r.stdout.strip()

    def test_hiq_matching_rule_paren_and_space_variants(self):
        out = self._run(r'''
const cases = [
  ['HIQラベル（カラー）', true],
  ['HIQラベル(カラー)', true],
  ['HIQラベル （カラー）', true],
  ['HIQラベル（ カラー ）', true],
  ['ＨＩＱラベル（カラー）', true],
  ['HIQラベル（白）', false],
  ['HIQラベル（透明）', false],
  ['OSKラベル', false],
  ['カラーラベル', false],
  ['HIQラベルカラー', false],
];
const bad = cases.filter(([n, exp]) => isHiqColorLabel(n) !== exp);
if (bad.length) {
  console.log('FAIL ' + JSON.stringify(bad));
  process.exit(1);
}
console.log('ok');
''')
        self.assertEqual(out, 'ok')

    def test_hiq_expand_and_price_separation(self):
        out = self._run(r'''
const rows = [
  { name: 'OSKラベル', unit: '個', unitPrice: 38000 },
  { name: 'HIQラベル（カラー）', unit: '個', unitPrice: 63000 },
  { name: 'HIQラベル（カラー）', unit: '個', unitPrice: 67000 },
  { name: 'HIQラベル（白）', unit: '個', unitPrice: 10000 },
];
const blocks = expandCountSheetRows(rows);
if (blocks.length !== 4) throw new Error('blocks ' + blocks.length);
if (blocks[0].kind !== 'single' || blocks[0].color !== '—') throw new Error('osk');
if (blocks[1].kind !== 'hiq' || blocks[1].unitPrice != 63000) throw new Error('hiq63');
if (blocks[2].kind !== 'hiq' || blocks[2].unitPrice != 67000) throw new Error('hiq67');
if (blocks[3].kind !== 'single') throw new Error('white should be single');
const html = buildCountSheetHtml(rows, 2026, 10);
if (!html.includes('rowspan="4"')) throw new Error('no rowspan');
if ((html.match(/この合計を報告画面へ入力/g) || []).length !== 2) throw new Error('total note count');
if (!html.includes('単価 63,000円') || !html.includes('単価 67,000円')) throw new Error('prices');
// 数量セルは空（在庫数量を印字しない）
if (/bihin-count-qty">\s*[^<\s]/.test(html)) throw new Error('qty not empty: ' + (html.match(/bihin-count-qty">[^<]*/)||[])[0]);
if (html.includes('38000') && /opening|closing|purchase|在庫/.test(html)) throw new Error('stock leak');
if (!html.includes('>赤<') || !html.includes('>緑<') || !html.includes('>青<')) throw new Error('colors');
if (!html.includes('bihin-count-hiq-total')) throw new Error('total class');
// 非HIQは1行ブロック
const singles = (html.match(/bihin-count-block">/g) || []).length;
const hiqs = (html.match(/bihin-count-hiq"/g) || []).length;
if (singles !== 2) throw new Error('single blocks ' + singles);
if (hiqs !== 2) throw new Error('hiq blocks ' + hiqs);
console.log('ok');
''')
        self.assertEqual(out, 'ok')

    def test_empty_qty_cells_even_when_source_has_stock(self):
        out = self._run(r'''
const rows = [
  { name: 'OSKラベル', unit: '個', unitPrice: 38000, openingQty: 5, purchaseQty: 2, closingQty: 3 },
  { name: 'HIQラベル（カラー）', unit: '個', unitPrice: 63000, closingQty: 99 },
];
const html = buildCountSheetHtml(rows, 2026, 10);
if (html.includes('>5<') || html.includes('>2<') || html.includes('>3<') || html.includes('>99<')) {
  throw new Error('stock qty leaked into print html');
}
const qtyCells = html.match(/class="bihin-count-qty"[^>]*>[^<]*</g) || [];
if (!qtyCells.length) throw new Error('no qty cells');
for (const c of qtyCells) {
  if (!/bihin-count-qty"><$/.test(c) && !/bihin-count-qty"><\/td>/.test(c.replace('</','<'))) {
    // allow <td class="bihin-count-qty"></td>
  }
}
if (!/<td class="bihin-count-qty"><\/td>/.test(html)) throw new Error('expected empty qty td');
if (html.includes('openingQty') || html.includes('closingQty')) throw new Error('field names in html');
console.log('ok');
''')
        self.assertEqual(out, 'ok')

    def test_non_hiq_single_row(self):
        out = self._run(r'''
const rows = [{ name: '熱転写リボン', unit: '本', unitPrice: 3500 }];
const blocks = expandCountSheetRows(rows);
if (blocks.length !== 1 || blocks[0].kind !== 'single') throw new Error('kind');
const html = buildCountSheetHtml(rows, 2026, 10);
if (html.includes('rowspan')) throw new Error('unexpected rowspan');
if (!html.includes('>—<')) throw new Error('dash color');
if ((html.match(/<tbody class="bihin-count-block">/g) || []).length !== 1) throw new Error('blocks');
console.log('ok');
''')
        self.assertEqual(out, 'ok')

    def test_sheet_copy_matches_preview(self):
        out = self._run(r'''
const html = buildCountSheetHtml([
  { name: 'OSKラベル', unit: '個', unitPrice: 38000 },
  { name: 'HIQラベル（カラー）', unit: '個', unitPrice: 63000 },
], 2026, 10);
const need = [
  '備品棚卸 記入用紙',
  '2026年10月分',
  '棚卸日：',
  '記入者：',
  '実際に数えた月末在庫を、数量欄へ記入してください。',
  'HIQラベル（カラー）は赤・緑・青を数え、単価ごとに合計してください。',
  '品名・単価',
  '棚卸数量（手書き）',
  '連絡事項・メモ',
  '※ 単価は同名品を区別するための表示です。数量・金額の実績は印字しません。',
  '※ 報告画面ではHIQラベルを色別に分けず、各単価の合計数量を入力します。',
  'この合計を報告画面へ入力',
];
for (const s of need) {
  if (!html.includes(s)) throw new Error('missing ' + s);
}
console.log('ok');
''')
        self.assertEqual(out, 'ok')

    def test_pagination_contract_hiq_block_unbroken(self):
        """6品相当は1枚想定のマークアップ。HIQは tbody.bihin-count-hiq で非分割。"""
        self.assertIn('bihin-count-block', JS)
        self.assertIn('bihin-count-hiq', JS)
        self.assertIn('page-break-inside:avoid', CSS)
        compact = CSS.replace(' ', '').replace('\n', '')
        self.assertIn('.bihin-count-block{break-inside:avoid;page-break-inside:avoid}', compact)
        out = self._run(r'''
const rows = [];
for (let i = 0; i < 6; i++) rows.push({ name: '品目' + i, unit: '個', unitPrice: 1000 + i });
rows.push({ name: 'HIQラベル（カラー）', unit: '個', unitPrice: 63000 });
rows.push({ name: 'HIQラベル（カラー）', unit: '個', unitPrice: 67000 });
const html = buildCountSheetHtml(rows, 2026, 10);
const hiqTbodies = html.match(/<tbody class="bihin-count-block bihin-count-hiq">[\s\S]*?<\/tbody>/g) || [];
if (hiqTbodies.length !== 2) throw new Error('hiq tbody ' + hiqTbodies.length);
for (const tb of hiqTbodies) {
  if ((tb.match(/<tr/g) || []).length !== 4) throw new Error('hiq rows != 4');
  if (!tb.includes('rowspan="4"')) throw new Error('rowspan missing in block');
}
// 品目が増えてもブロック単位（縮小強制クラスが無い）
if (html.includes('transform:scale') || html.includes('zoom:')) throw new Error('must not shrink');
console.log('ok');
''')
        self.assertEqual(out, 'ok')


class BihinCountSheetImpactNoneTest(unittest.TestCase):
    def test_report_ui_unchanged_no_color_split(self):
        # paintBihin は従来どおり1行/品目（色分割なし）。既存の見出し rowspan=2 は対象外。
        start = JS.index('function paintBihin')
        end = JS.index('async function api', start)
        paint = JS[start:end]
        self.assertNotIn('isHiqColorLabel', paint)
        self.assertNotIn('bihin-count', paint)
        self.assertNotIn('この合計を報告画面へ入力', paint)
        self.assertNotIn(">赤<", paint)
        self.assertIn('月末在庫', paint)
        self.assertIn('closingQty', paint)
        self.assertIn('rowspan="2"', paint)  # 見出しグループ用（色分割ではない）

    def test_backend_math_untouched_by_print_feature(self):
        py = (ROOT / 'kintai_bihin.py').read_text(encoding='utf-8')
        self.assertNotIn('count sheet', py.lower())
        self.assertNotIn('printing-bihin', py)
        self.assertNotIn('HIQ', py)
        # 既存テストファイルも残存
        self.assertTrue((ROOT / 'tests' / 'test_kintai_bihin.py').exists())

    def test_no_stock_desk_coupling(self):
        self.assertNotIn('/api/stock', JS)
        self.assertNotIn('stock-desk', JS)
        self.assertNotIn('/inventory/', JS)


if __name__ == '__main__':
    unittest.main()
