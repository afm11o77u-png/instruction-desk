"""Source assertions for 貼付け位置 per-image print preview."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEMENT_JS = (ROOT / 'placement.js').read_text(encoding='utf-8')
INDEX_HTML = (ROOT / 'index.html').read_text(encoding='utf-8')
STYLE_CSS = (ROOT / 'style.css').read_text(encoding='utf-8')


def _fn(name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\{{', PLACEMENT_JS)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\n(?:function |let |const |async function )', PLACEMENT_JS[m.end():])
    end = m.end() + nxt.start() if nxt else len(PLACEMENT_JS)
    return PLACEMENT_JS[start:end]


class PlacementPrintTest(unittest.TestCase):
    def test_card_has_print_beside_open(self):
        body = _fn('paintPlacementCard')
        self.assertIn('placementcardprint', body)
        self.assertIn('placementcardopen', body)
        self.assertIn('>印刷</button>', body)
        self.assertIn('item.hasImage', body)
        self.assertIn('openPlacementPrint(item)', body)
        self.assertIn('disabled', body)

    def test_detail_view_print_button(self):
        self.assertIn('id="placementviewprint"', INDEX_HTML)
        self.assertIn('$(\'#placementviewprint\')', PLACEMENT_JS)
        open_body = _fn('openPlacement')
        self.assertIn('placementviewprint', open_body)
        self.assertIn('!item.hasImage', open_body)

    def test_print_uses_original_image_not_thumb(self):
        body = _fn('openPlacementPrint')
        self.assertIn("placementUrl(item.id,'image')", body)
        self.assertNotIn("placementUrl(item.id,'thumb')", body)
        self.assertIn('naturalWidth', body)
        self.assertIn('landscape', body)
        self.assertIn('portrait', body)
        self.assertIn('ヘッダーとフッター', body)
        self.assertIn('printBtn.disabled', body)
        self.assertIn('画像の読み込みに失敗', body)
        self.assertIn('placementPrintScrollY', body)

    def test_orientation_helper(self):
        body = _fn('placementPrintOrientation')
        self.assertIn('w>h', body)
        self.assertIn("'landscape'", body)
        self.assertIn("'portrait'", body)

    def test_print_css_keeps_aspect_and_one_page(self):
        self.assertIn('object-fit:contain', PLACEMENT_JS)
        self.assertIn('@page{size:A4 portrait;margin:10mm}', PLACEMENT_JS)
        self.assertIn('@page{size:A4 landscape;margin:10mm}', PLACEMENT_JS)
        self.assertIn('overflow-wrap:anywhere', PLACEMENT_JS)
        self.assertIn('break-after:avoid', PLACEMENT_JS)
        self.assertIn('.placementprintpage.is-portrait{width:190mm;height:277mm}', PLACEMENT_JS)
        self.assertIn('.placementprintpage.is-landscape{width:277mm;height:190mm}', PLACEMENT_JS)

    def test_overlay_css_and_card_actions(self):
        self.assertIn('.placementcardactions', STYLE_CSS)
        self.assertIn('#placementprintpreview{position:fixed', STYLE_CSS)
        self.assertIn('body.placementprinting{overflow:hidden}', STYLE_CSS)

    def test_existing_sheet_print_untouched(self):
        sheet = (ROOT / 'sheet.js').read_text(encoding='utf-8')
        self.assertIn('function openPrintPreview', sheet)
        self.assertIn('function queuePrint', sheet)
        self.assertNotIn('openPlacementPrint', sheet)


if __name__ == '__main__':
    unittest.main()
