"""重複商品モーダルのレイアウト（幅は dialog 本体・比較は横スクロール表）。"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProductMergeModalUiTest(unittest.TestCase):
    def test_dialog_class_on_dialog_not_form_width(self):
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        js = (ROOT / 'static' / 'product_merge.js').read_text(encoding='utf-8')
        # 旧: form 側セレクタ dialog .merge-dialog に幅 → dialog(880) と食い違いではみ出し
        self.assertNotRegex(css, r'dialog\s+\.merge-dialog\s*\{')
        self.assertRegex(css, r'dialog\.merge-dialog\s*\{')
        self.assertIn('classList.add(\'merge-dialog\')', js)
        self.assertIn('merge-compare-scroll', js)
        self.assertIn('table class="merge-compare"', js)
        self.assertIn('position:sticky', css)
        self.assertIn('.merge-row-label', css)
        self.assertIn('overflow:auto', css)
        # 比較列の選択コントロール
        self.assertIn('data-merge-pick', js)
        self.assertIn('name="mergeKeep"', js)
        self.assertIn('統合対象', js)
        self.assertIn('残す', js)

    def test_viewport_constrained_modal(self):
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('max-height:min(90vh', css)
        self.assertIn('max-width:96vw', css)
        self.assertIn('@media(max-width:640px)', css)


if __name__ == '__main__':
    unittest.main()
