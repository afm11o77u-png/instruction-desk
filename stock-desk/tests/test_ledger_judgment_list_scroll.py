"""旧CSV判断一覧: 表示拡大とスクロール戻り（UIのみ。登録・在庫ミューテーションなし）。"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'static' / 'product_ledger_csv.js'
CSS = ROOT / 'static' / 'style.css'


class JudgmentListScrollLayoutSourceTest(unittest.TestCase):
    def test_single_vertical_scroll_and_taller_dialog(self):
        css = CSS.read_text(encoding='utf-8')
        js = JS.read_text(encoding='utf-8')
        # 内側一覧の短い max-height を廃し、縦はモーダル本体へ一本化
        self.assertIn('max-height:none', css)
        self.assertNotIn('max-height:min(40vh,420px)', css)
        self.assertIn('dialog.product-ledger-dialog', css)
        self.assertIn('max-height:min(96vh,1100px)', css)
        self.assertIn('white-space:normal', css)
        # sticky header / 操作バー到達性
        self.assertIn('sku-section-header', css)
        self.assertIn('product-ledger-preview-actions', css)
        self.assertIn('position:sticky', css)
        self.assertIn('product-ledger-preview-actions', js)
        # wrap は親幅拘束（はみ出し＋スクロール不可を防ぐ）
        self.assertIn('min-width:0', css)
        self.assertIn('table-layout:fixed', css)

    def test_scroll_anchor_and_stale_preview_guards(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('captureVisibleSkuAnchor', js)
        self.assertIn('restoreNearbySkuInView', js)
        self.assertIn('visibleAnchor', js)
        self.assertIn('previewReqSeq', js)
        self.assertIn('stale-preview-ignored', js)
        self.assertIn('keepScroll', js)
        # 再照合は既存プレビュー時にスクロール維持（初回のみ preserveScroll:false）
        self.assertIn('const keepScroll=!!st.preview', js)
        # 縦の主スクローラーは body（wrap は横）
        self.assertIn('縦は body に一本化', js)
        self.assertIn("applyScrollPos(el,{top:0,left:pos.left||0})", js)
        # 診断ログはクエリでゲート
        self.assertIn('ledgerScrollDebug', js)
        self.assertIn('ledgerScrollDebug=1', js)

    def test_section_defaults_unchanged(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('unified:true', js)
        self.assertIn('individual:true', js)
        self.assertIn('bulk:false', js)
        self.assertIn('sourceRows:false', js)
        # ページャは表の上（操作到達性）— unifiedBody 内の並び
        start = js.find('const unifiedBody=')
        self.assertGreater(start, 0)
        chunk = js[start:start + 900]
        pager = chunk.find("sectionPagerHtml('unified'")
        table = chunk.find('unifiedCompareTableHtml(st,unifiedSlice.pageItems')
        self.assertGreater(pager, 0)
        self.assertGreater(table, 0)
        self.assertLess(pager, table)


if __name__ == '__main__':
    unittest.main()
