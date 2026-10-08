"""Source/DOM-markup assertions for 初期在庫 list display modes."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
DM_JS = (ROOT / 'static' / 'display-mode.js').read_text(encoding='utf-8')
ROOT_DM_JS = (REPO / 'display-mode.js').read_text(encoding='utf-8')
DISPLAY_MODES_PY = (REPO / 'display_modes.py').read_text(encoding='utf-8')


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\s*\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\n\s*function ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class OpeningDisplayModesUiTest(unittest.TestCase):
    def test_opening_has_own_screen_key(self):
        self.assertIn("opening:'stock-opening'", APP_JS)
        self.assertIn("'stock-opening': 'standard'", DM_JS)
        self.assertIn("'stock-opening': 'standard'", ROOT_DM_JS)
        self.assertIn("'stock-opening': 'standard'", DISPLAY_MODES_PY)

    def test_opening_page_mounts_display_control_on_bar(self):
        body = _fn(APP_JS, 'opening')
        self.assertIn('id="stockliststage"', body)
        self.assertIn('id="stocklistgrid"', body)
        self.assertIn("mountStockDisplay(stage,'opening'", body)
        self.assertIn("stockScreenId('opening')", body)
        self.assertIn("grid.hidden=mode!=='grid'", body)
        mount = _fn(APP_JS, 'mountStockDisplay')
        self.assertIn("document.querySelector('#main .bar')", mount)
        self.assertIn("tab==='list'", mount)

    def test_opening_list_fields_unchanged_in_table(self):
        body = _fn(APP_JS, 'opening')
        for col in ('基準日', '顧客', '商品', '加工仕様', 'サイズ', '数量', '仕入単価', '保管場所'):
            self.assertIn(col, body)
        self.assertIn('e.basisDate', body)
        self.assertIn('e.customerName', body)
        self.assertIn('e.embroideryLabel', body)
        self.assertIn('e.locationLabel', body)
        self.assertIn('e.unitCost', body)
        self.assertIn('初期在庫を登録', body)
        self.assertNotIn('searchPickerMarkup', body)

    def test_opening_grid_cards_cover_same_entry_fields(self):
        body = _fn(APP_JS, 'opening')
        self.assertIn('class="dm-card"', body)
        self.assertIn('e.basisDate', body[body.index('list-grid'):])
        self.assertIn('e.customerName', body[body.index('list-grid'):])
        self.assertIn('e.embroideryLabel', body[body.index('list-grid'):])
        self.assertIn('e.locationLabel', body[body.index('list-grid'):])
        self.assertIn('e.unitCost', body[body.index('list-grid'):])

    def test_display_mode_apply_uses_dm_classes(self):
        apply = _fn(DM_JS, 'apply')
        self.assertIn("host.classList.add('dm-' + mode)", apply)
        self.assertIn("'dm-standard'", apply)
        self.assertIn("'dm-compact'", apply)
        self.assertIn("'dm-cozy'", apply)
        self.assertIn("'dm-grid'", apply)
        control = _fn(DM_JS, 'controlHtml')
        self.assertIn('表示', control)
        for label in ('ゆったり', '標準', 'コンパクト', 'グリッド'):
            self.assertIn(label, DM_JS)
        # Selector order: ゆったり → 標準 → コンパクト → グリッド
        cozy = DM_JS.index("id: 'cozy', label: 'ゆったり'")
        standard = DM_JS.index("id: 'standard', label: '標準'")
        compact = DM_JS.index("id: 'compact', label: 'コンパクト'")
        grid = DM_JS.index("id: 'grid', label: 'グリッド'")
        self.assertLess(cozy, standard)
        self.assertLess(standard, compact)
        self.assertLess(compact, grid)

    def test_stock_opening_is_persistable_screen(self):
        # SCREENS is derived from SCREEN_DEFAULTS; key must be present for POST.
        self.assertRegex(
            DISPLAY_MODES_PY,
            r"SCREEN_DEFAULTS\s*=\s*\{[^}]*'stock-opening'\s*:\s*'standard'",
        )
        self.assertIn('SCREENS = set(SCREEN_DEFAULTS)', DISPLAY_MODES_PY)
        self.assertIn('def normalize_screen(screen_id):', DISPLAY_MODES_PY)
        self.assertIn('value not in SCREENS', DISPLAY_MODES_PY)
        self.assertIn('def default_for(screen_id):', DISPLAY_MODES_PY)


if __name__ == '__main__':
    unittest.main()
