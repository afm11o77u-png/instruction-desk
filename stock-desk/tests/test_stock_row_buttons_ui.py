"""在庫一覧の入庫/出庫ボタンサイズ統一（色は .primary で区別）。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
DM_CSS = (ROOT / 'static' / 'display-mode.css').read_text(encoding='utf-8')


class StockRowButtonsUiTest(unittest.TestCase):
    def test_in_and_out_share_stock_row_btn_class(self):
        self.assertIn('class="primary stock-row-btn"', APP_JS)
        self.assertIn('class="stock-row-btn"', APP_JS)
        self.assertIn('data-stock-in=', APP_JS)
        self.assertIn('data-stock-out=', APP_JS)
        # 入庫・出庫は操作メニューの中。高さは同じ stock-row-btn。
        menu = re.search(
            r'function stockOpMenuItemsHtml\([^)]*\)\{.*?return `([^`]+)`',
            APP_JS,
            re.S,
        )
        self.assertIsNotNone(menu)
        items = menu.group(1)
        self.assertIn('primary stock-row-btn', items)
        self.assertRegex(items, r'class="stock-row-btn"[^>]*data-stock-out=')
        self.assertIn('>入庫</button>', items)
        self.assertIn('>出庫</button>', items)
        self.assertIn('>QR表示</button>', items)
        row = re.search(
            r'function stockActionButtonsHtml\([^)]*\)\{.*?return `([^`]+)`',
            APP_JS,
            re.S,
        )
        self.assertIsNotNone(row)
        html = row.group(1)
        self.assertIn('>操作</button>', html)
        self.assertIn('>発注</button>', html)
        self.assertNotIn('data-stock-in=', html)
        self.assertNotIn('>QR表示</button>', html)
        self.assertNotIn('>入庫</button>', html)
        self.assertNotIn('>出庫</button>', html)

    def test_css_unifies_padding_including_primary(self):
        self.assertIn('.stock-row-btn{', CSS)
        self.assertRegex(CSS, r'\.stock-row-btn\{[^}]*min-height:\s*28px')
        self.assertRegex(
            DM_CSS,
            r'\.list-stage\.dm-compact \.stock-row-btn,\s*\.list-stage\.dm-compact button\.stock-row-btn\.primary\{[^}]*min-height:\s*26px',
        )
        self.assertRegex(
            DM_CSS,
            r'\.list-stage\.dm-standard \.stock-row-btn,\s*\.list-stage\.dm-standard button\.stock-row-btn\.primary\{[^}]*min-height:\s*26px',
        )


if __name__ == '__main__':
    unittest.main()
