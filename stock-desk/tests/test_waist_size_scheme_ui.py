"""Source assertions: 商品登録・編集にウエストサイズ表記を追加。"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
DOC = (ROOT / '説明.md').read_text(encoding='utf-8')

WAIST_PRESETS = [
    '70', '73', '76', '79', '82', '85', '88', '91',
    '95', '100', '105', '110', '115', '120', '125',
]


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\s*\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\nfunction ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class WaistSizeSchemeUiTest(unittest.TestCase):
    def test_size_schemes_include_waist_presets_in_numeric_order(self):
        m = re.search(
            r"waist:\{id:'waist',label:'ウエスト',sizes:\[([^\]]+)\]\}",
            APP_JS,
        )
        self.assertIsNotNone(m)
        sizes = re.findall(r"'([^']+)'", m.group(1))
        self.assertEqual(sizes, WAIST_PRESETS)
        nums = [int(s) for s in sizes]
        self.assertEqual(nums, sorted(nums))

    def test_product_form_has_waist_toggle_and_cm_unit(self):
        body = _fn(APP_JS, 'productForm')
        self.assertIn("data-scheme=\"waist\"", body)
        self.assertIn('ウエスト', body)
        self.assertIn('id="sizeUnit"', body)
        self.assertIn('単位：cm', body)
        self.assertIn("scheme==='waist'", body)

    def test_editor_keeps_sizes_on_scheme_switch_and_shows_unit(self):
        body = _fn(APP_JS, 'bindProductEditor')
        self.assertIn('shownSizeChips(editor.scheme,editor.sizes)', body)
        self.assertIn("unit.hidden=editor.scheme!=='waist'", body)
        self.assertIn('sizeSchemeHint(editor.scheme)', body)
        # scheme switch only changes editor.scheme — does not clear editor.sizes
        self.assertIn('editor.scheme=b.dataset.scheme', body)
        self.assertNotIn('editor.sizes=[]', body)
        self.assertNotIn('editor.sizes=detectSizeScheme', body)

    def test_exact_label_dedupe_without_w70_bulk_convert(self):
        self.assertIn('sizeLabelAlreadySelected', APP_JS)
        self.assertIn('一括変換', APP_JS)
        add = _fn(APP_JS, 'bindProductEditor')
        self.assertIn('sizeLabelAlreadySelected(editor.sizes,s)', add)
        # No rename / normalize of W70 → 70（表記は完全一致のみ重複扱い）
        self.assertNotRegex(APP_JS, r"replace\([^)]*W\d+")
        self.assertNotIn("label.replace('W'", APP_JS)
        self.assertNotIn('.replace(/^W/i', APP_JS)
        self.assertIn('「70」と「W70」は別サイズ', APP_JS)

    def test_detect_prefers_waist_when_presets_present(self):
        body = _fn(APP_JS, 'detectSizeScheme')
        self.assertIn('isWaistPresetSize', body)
        self.assertIn("return 'waist'", body)

    def test_opening_move_orders_still_use_product_sizes(self):
        # 初期在庫・入出庫・発注は商品の sizes をそのまま選択肢に使う
        self.assertIn('product.sizes.map(size=>({size,qty:', APP_JS)
        self.assertIn('data-size=', APP_JS)  # move target select uses product sizes
        move = APP_JS[APP_JS.index('id="moveSize"'):APP_JS.index('id="moveSize"') + 400]
        self.assertIn('sizes.map(s=>', move)
        po = (ROOT / 'static' / 'purchase_orders.js').read_text(encoding='utf-8')
        self.assertIn('sizeList:p.sizes||[]', po)
        self.assertIn('sizes.map(sz=>', po)
        opening = (ROOT / 'static' / 'opening_pickers.js').read_text(encoding='utf-8')
        self.assertIn('product.sizes', opening)
        self.assertIn('rows:sizes.map(size=', opening)

    def test_css_and_docs_mention_waist_unit(self):
        self.assertIn('.size-unit', CSS)
        self.assertIn('ウエスト', DOC)
        self.assertIn('単位 cm', DOC)
        self.assertIn('W70', DOC)

    def test_node_unit_helpers(self):
        script = ROOT / 'tests' / 'test_waist_size_scheme.js'
        proc = subprocess.run(['node', str(script)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == '__main__':
    unittest.main()
