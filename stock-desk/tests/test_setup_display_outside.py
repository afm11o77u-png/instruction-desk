"""登録・設定: 表示 control outside content frame; default compact."""
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
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')

SETUP_TABS = ('customers', 'suppliers', 'products', 'locations', 'opening')
SETUP_SCREENS = tuple(f'stock-{t}' for t in SETUP_TABS)


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\s*\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\n\s*function ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


class SetupDisplayOutsideTest(unittest.TestCase):
    def test_five_setup_tabs(self):
        self.assertIn(
            "SETUP_TABS=[['customers','顧客'],['suppliers','仕入先'],['products','商品'],"
            "['locations','保管場所'],['opening','初期在庫']]",
            APP_JS,
        )

    def test_setup_defaults_are_compact_page_scoped(self):
        for src in (DM_JS, ROOT_DM_JS, DISPLAY_MODES_PY):
            for screen in SETUP_SCREENS:
                self.assertIn(f"'{screen}': 'compact'", src)
            # Other stock pages stay standard (do not inherit setup default)
            self.assertIn("'stock-list': 'standard'", src)
            self.assertIn("'stock-move': 'standard'", src)

    def test_setup_section_shows_title_row_slot(self):
        sync = _fn(APP_JS, 'syncStockListDisplaySlot')
        self.assertIn("section==='setup'", sync)
        self.assertIn('slot.hidden=false', sync)
        self.assertIn('id="stocklistdisplay"', INDEX)
        head = INDEX[INDEX.index('class="stockhead"'):INDEX.index('<main')]
        self.assertIn('id="stocklistdisplay"', head)
        self.assertLess(head.index('class="switch"'), head.index('id="stocklistdisplay"'))

    def test_setup_tabs_mount_display_on_title_slot(self):
        mount = _fn(APP_JS, 'mountStockDisplay')
        self.assertIn('SETUP_TAB_IDS.has(tab)', mount)
        self.assertIn('useTitleSlot', mount)
        self.assertIn("#stocklistdisplay", mount)
        # Title slot chosen before falling back to panel bar
        self.assertLess(
            mount.index("#stocklistdisplay"),
            mount.index("#main .bar"),
        )
        bind = _fn(APP_JS, 'bindList')
        self.assertIn('mountStockDisplay(stage,state.tab', bind)
        opening = _fn(APP_JS, 'opening')
        self.assertIn("mountStockDisplay(stage,'opening'", opening)
        # Static panel bars must not embed the 表示 control
        for tab in SETUP_TABS:
            body = _fn(APP_JS, tab)
            if 'class="bar' not in body:
                continue
            bar_start = body.index('class="bar')
            stage_idx = body.find('id="stockliststage"')
            if stage_idx <= bar_start:
                continue
            bar_html = body[bar_start:stage_idx]
            self.assertNotIn('>表示<', bar_html)
            self.assertNotIn('display-mode', bar_html)


if __name__ == '__main__':
    unittest.main()
