"""旧CSV判断一覧: 右端操作のはみ出し解消（UIのみ。登録・在庫ミューテーションなし）。"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'static' / 'product_ledger_csv.js'
CSS = ROOT / 'static' / 'style.css'


class JudgmentListOverflowSourceTest(unittest.TestCase):
    def test_dialog_near_viewport_and_wrap_constrained(self):
        css = CSS.read_text(encoding='utf-8')
        # 左右余白は最小限。幅を画面に合わせる
        self.assertIn('width:calc(100vw - 24px)', css)
        self.assertIn('max-width:calc(100vw - 24px)', css)
        # wrap / 親が幅を拘束し、overflow-x で必要時は右端まで辿れる
        wrap = css[css.find('.sku-compare-wrap{'):css.find('.sku-compare-wrap{') + 280]
        self.assertIn('width:100%', wrap)
        self.assertIn('max-width:100%', wrap)
        self.assertIn('min-width:0', wrap)
        self.assertIn('overflow-x:auto', wrap)
        self.assertIn('table-layout:fixed', css)

    def test_action_column_reserved_and_stacked_buttons(self):
        css = CSS.read_text(encoding='utf-8')
        js = JS.read_text(encoding='utf-8')
        # 操作列を先に確保（横一列の広い固定幅はやめる）
        self.assertIn('min-width:9.75rem', css)
        self.assertNotIn('min-width:17.5rem', css)
        self.assertIn('flex-direction:column', css)
        self.assertIn('position:sticky;right:0', css.replace(' ', ''))
        self.assertIn('sku-action-h', js)
        self.assertIn('data-label="操作"', js)
        # 情報列は折り返し（非表示にしない）
        self.assertIn('overflow-wrap:anywhere', css)
        self.assertIn('data-label="商品名候補"', js)
        self.assertIn('data-label="仕入先候補"', js)
        self.assertIn('data-label="要確認理由"', js)

    def test_narrow_stack_fallback(self):
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('@media(max-width:960px)', css)
        self.assertIn('display:block', css)
        self.assertIn('content:attr(data-label)', css)
        # 上下拡大・見出し固定・区分は維持側の痕跡
        self.assertIn('max-height:min(96vh,1100px)', css)
        self.assertIn('sku-section-header', css)

    def test_keep_scroll_and_sections(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('captureVisibleSkuAnchor', js)
        self.assertIn('unified:true', js)
        self.assertIn('individual:true', js)
        self.assertIn('bulk:false', js)
        self.assertIn('確認リスト', js)
        self.assertIn('一括承認候補', js)
        self.assertIn('自動決定済み', js)
        self.assertIn('元行一覧（詳細確認用）', js)


if __name__ == '__main__':
    unittest.main()
