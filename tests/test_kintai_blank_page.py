"""勤怠画面が空白になる回帰: kintaidesk は workspace の外に置き、tablewrap 一括 hidden の対象外。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
APP = (ROOT / 'app.js').read_text(encoding='utf-8')
KINTAI_JS = (ROOT / 'kintai.js').read_text(encoding='utf-8')


class KintaiBlankPageFixTest(unittest.TestCase):
    def test_kintaidesk_outside_workspace(self):
        kd = INDEX.find('id="kintaidesk"')
        ws = INDEX.find('class="workspace"')
        self.assertGreater(kd, 0)
        self.assertGreater(ws, 0)
        self.assertLess(kd, ws, 'kintaidesk must come before workspace so listChrome can hide workspace without hiding kintai')
        # not nested: workspace open must not wrap kintaidesk
        self.assertNotIn(
            'class="workspace"><div class="sectionheading"',
            INDEX[kd:kd + 50],
        )
        between = INDEX[kd:ws]
        self.assertIn('id="kintaidesk"', between)
        self.assertIn('出勤簿から更新', between)

    def test_list_chrome_does_not_hide_first_tablewrap_globally(self):
        # 旧実装 document.querySelector('.tablewrap').hidden=quiet だと
        # kintaidesk を workspace 前に置いた瞬間に勤怠表が消える
        self.assertNotIn("document.querySelector('.tablewrap').hidden=quiet", APP)
        self.assertIn('orderliststage .tablewrap', APP)
        self.assertIn("kintaiDesk.hidden=!kintai", APP)
        self.assertIn("workspace').hidden=picking||placement||shipinfo||nameinfo||kintai", APP)

    def test_kintai_table_not_generic_tablewrap(self):
        self.assertIn('id="kintaitable" class="kintai-tablewrap"', INDEX)
        self.assertNotIn('id="kintaitable" class="tablewrap"', INDEX)

    def test_controls_bind_if_dom_already_ready(self):
        self.assertIn('bindKintaiControls', KINTAI_JS)
        self.assertIn("document.readyState === 'loading'", KINTAI_JS)


if __name__ == '__main__':
    unittest.main()
