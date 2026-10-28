"""左メニューの Empire ロゴ＋マスコットをログアウト右へ移した配置。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
ACCOUNT_JS = (ROOT / 'account.js').read_text(encoding='utf-8')
ACCOUNT_CSS = (ROOT / 'account.css').read_text(encoding='utf-8')
STYLE = (ROOT / 'style.css').read_text(encoding='utf-8')


def _aside() -> str:
    return INDEX[INDEX.index('<aside>'):INDEX.index('</aside>') + len('</aside>')]


class SidebarLogoByLogoutTest(unittest.TestCase):
    def test_top_brand_has_title_without_images(self):
        aside = _aside()
        top = aside.split('id="sidebarfooter"', 1)[0]
        self.assertIn('id="brandtitle"', top)
        self.assertNotIn('brandlogo', top)
        self.assertNotIn('mascots.png', top)
        self.assertNotIn('logo.jpg', top)

    def test_footer_has_single_logo_row(self):
        aside = _aside()
        footer = aside[aside.index('id="sidebarfooter"'):]
        self.assertIn('id="logoutrow"', footer)
        self.assertIn('id="logoutslot"', footer)
        self.assertIn('id="logoutbrand"', footer)
        self.assertIn('src="/logo.jpg"', footer)
        self.assertIn('src="/mascots.png"', footer)
        self.assertEqual(aside.count('src="/logo.jpg"'), 1)
        self.assertEqual(aside.count('src="/mascots.png"'), 1)
        self.assertLess(footer.index('id="desksettings"'), footer.index('id="logoutrow"'))
        self.assertIn('id="manageusers"', footer)
        self.assertIn('id="repair-open"', footer)

    def test_place_logout_brand_wiring(self):
        self.assertIn('function placeLogoutBrand()', ACCOUNT_JS)
        self.assertIn("slot.replaceChildren(logoutBtn)", ACCOUNT_JS)
        self.assertIn('placeLogoutBrand()', ACCOUNT_JS)
        # ログアウトは accountbar テンプレに残し、配置関数で右ロゴ行へ移す（#240 の account footer と両立）
        start = ACCOUNT_JS.split('block.innerHTML', 1)[1].split('`;', 1)[0]
        self.assertIn('id="logout"', start)
        self.assertIn('ログアウト', start)
        self.assertIn('id="markmanage"', start)
        self.assertNotIn('logo.jpg', start)
        self.assertNotIn('mascots.png', start)

    def test_layout_css_keeps_sidebar_width_and_aspect(self):
        self.assertRegex(STYLE, r'aside\{[^}]*width:248px')
        self.assertIn('.logoutrow{', STYLE)
        self.assertIn('align-items:center', STYLE[STYLE.index('.logoutrow{'):STYLE.index('.logoutrow{') + 200])
        self.assertIn('.logoutrow .brandlogo{', STYLE)
        self.assertIn('.logoutrow .mascots{', STYLE)
        logo = re.search(r'\.logoutrow \.brandlogo\{([^}]+)\}', STYLE).group(1)
        mascot = re.search(r'\.logoutrow \.mascots\{([^}]+)\}', STYLE).group(1)
        self.assertIn('height:auto', logo)
        self.assertIn('height:auto', mascot)
        self.assertIn('max-width:', logo)
        self.assertIn('#logoutrow #logout{', ACCOUNT_CSS)
        self.assertIn('overflow-y:auto', STYLE)

    def test_mark_manage_stays_in_account_footer(self):
        # #240 マージ後: 上部 modekintai は無く、accountbar にマーク管理
        self.assertNotIn('id="modekintai"', INDEX)
        self.assertIn('id="kintainav"', INDEX)
        self.assertIn('id="markmanage"', ACCOUNT_JS)
        self.assertIn('mark_kintai', ACCOUNT_JS)
        self.assertIn("showCatalog('kintai')", ACCOUNT_JS)
        # 氏名・権限テキストより上
        bar = ACCOUNT_JS.split('block.innerHTML', 1)[1].split('`;', 1)[0]
        self.assertLess(bar.index('markmanage'), bar.index('currentUser.name'))
        self.assertLess(bar.index('markmanage'), bar.index('roleNames'))

    def test_mark_manage_button_chrome(self):
        # 上部モード切替ピル（.modes .mode）と同サイズの枠付きボタン
        bar = ACCOUNT_JS.split('block.innerHTML', 1)[1].split('`;', 1)[0]
        self.assertIn('id="markmanage"', bar)
        self.assertNotIn('id="markmanage" class="quiet"', bar)
        rule = ACCOUNT_CSS[ACCOUNT_CSS.index('#accountbar #markmanage{'):ACCOUNT_CSS.index('#accountbar #markmanage:hover')]
        self.assertIn('border:1px solid #7eaa90', rule)
        self.assertIn('border-radius:8px', rule)
        self.assertIn('padding:5px 2px', rule)
        self.assertIn('font-size:10px', rule)
        self.assertIn('line-height:1.25', rule)
        self.assertIn('width:calc(50% - 3px)', rule)
        self.assertIn('#accountbar #markmanage:hover', ACCOUNT_CSS)
        self.assertIn('#accountbar #markmanage.on{', ACCOUNT_CSS)
        self.assertIn('background:#163828', ACCOUNT_CSS[ACCOUNT_CSS.index('#accountbar #markmanage:hover'):])
        self.assertIn('#accountbar #markmanage{flex:0 0 calc(50% - 3px)', ACCOUNT_CSS)


if __name__ == '__main__':
    unittest.main()
