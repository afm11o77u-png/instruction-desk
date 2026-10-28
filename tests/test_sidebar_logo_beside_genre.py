"""左メニューの Empire ロゴ＋マスコットをジャンル見出し右隣へ配置。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
PICKING = (ROOT / 'picking.html').read_text(encoding='utf-8')
ACCOUNT_JS = (ROOT / 'account.js').read_text(encoding='utf-8')
ACCOUNT_CSS = (ROOT / 'account.css').read_text(encoding='utf-8')
STYLE = (ROOT / 'style.css').read_text(encoding='utf-8')
PICKING_CSS = (ROOT / 'picking.css').read_text(encoding='utf-8')


def _aside() -> str:
    return INDEX[INDEX.index('<aside>'):INDEX.index('</aside>') + len('</aside>')]


class SidebarLogoBesideGenreTest(unittest.TestCase):
    def test_brand_title_then_images_in_top(self):
        aside = _aside()
        top = aside.split('id="sidebarfooter"', 1)[0]
        brand = top[top.index('class="brand"'):top.index('id="modes"')]
        self.assertIn('id="brandtitle"', brand)
        self.assertIn('id="brandimages"', brand)
        self.assertLess(brand.index('id="brandtitle"'), brand.index('id="brandimages"'))
        self.assertLess(brand.index('id="brandtitle"'), brand.index('src="/logo.jpg"'))
        self.assertIn('src="/logo.jpg"', brand)
        self.assertIn('src="/mascots.png"', brand)
        self.assertEqual(aside.count('src="/logo.jpg"'), 1)
        self.assertEqual(aside.count('src="/mascots.png"'), 1)

    def test_footer_has_no_logo_or_logout_brand_row(self):
        aside = _aside()
        footer = aside[aside.index('id="sidebarfooter"'):]
        self.assertIn('id="desksettings"', footer)
        self.assertIn('id="manageusers"', footer)
        self.assertIn('id="repair-open"', footer)
        self.assertNotIn('logo.jpg', footer)
        self.assertNotIn('mascots.png', footer)
        self.assertNotIn('id="logoutrow"', footer)
        self.assertNotIn('id="logoutbrand"', footer)
        self.assertNotIn('id="logoutslot"', footer)

    def test_logout_stays_in_accountbar_without_brand_mover(self):
        self.assertNotIn('function placeLogoutBrand()', ACCOUNT_JS)
        self.assertNotIn('placeLogoutBrand()', ACCOUNT_JS)
        start = ACCOUNT_JS.split('block.innerHTML', 1)[1].split('`;', 1)[0]
        self.assertIn('id="logout"', start)
        self.assertIn('ログアウト', start)
        self.assertIn('id="markmanage"', start)
        self.assertNotIn('logo.jpg', start)
        self.assertNotIn('mascots.png', start)
        self.assertIn("$('#logout').onclick=logout", ACCOUNT_JS)

    def test_layout_css_keeps_sidebar_width_and_row_brand(self):
        self.assertRegex(STYLE, r'aside\{[^}]*width:248px')
        brand = re.search(r'\.brand\{([^}]+)\}', STYLE).group(1)
        self.assertIn('flex-direction:row', brand)
        self.assertIn('align-items:center', brand)
        self.assertIn('.brand .brandlogo{', STYLE)
        self.assertIn('.brand .mascots{', STYLE)
        logo = re.search(r'\.brand \.brandlogo\{([^}]+)\}', STYLE).group(1)
        mascot = re.search(r'\.brand \.mascots\{([^}]+)\}', STYLE).group(1)
        self.assertIn('height:auto', logo)
        self.assertIn('height:auto', mascot)
        self.assertIn('max-width:', logo)
        self.assertNotIn('.logoutrow{', STYLE)
        self.assertNotIn('#logoutrow #logout{', ACCOUNT_CSS)
        self.assertIn('overflow-y:auto', STYLE)
        # mobile chrome still styles .brand
        self.assertIn('@media(max-width:720px)', STYLE)
        mobile = STYLE[STYLE.index('@media(max-width:720px)'):]
        self.assertIn('.brand{', mobile)

    def test_picking_sidebar_matches_title_then_images(self):
        menu = PICKING[PICKING.index('aside class="deskmenu"'):PICKING.index('</aside>')]
        brand = menu[menu.index('class="brand"'):menu.index('</div><a class="nav"')]
        self.assertLess(brand.index('指示書デスク'), brand.index('src="/logo.jpg"'))
        self.assertIn('src="/mascots.png"', brand)
        pbrand = re.search(r'aside\.deskmenu \.brand\{([^}]+)\}', PICKING_CSS).group(1)
        self.assertIn('flex-direction:row', pbrand)
        self.assertIn('align-items:center', pbrand)

    def test_mark_manage_stays_in_account_footer(self):
        # #240 マージ後: 上部 modekintai は無く、accountbar にマーク管理
        self.assertNotIn('id="modekintai"', INDEX)
        self.assertIn('id="kintainav"', INDEX)
        self.assertIn('id="markmanage"', ACCOUNT_JS)
        self.assertIn('mark_kintai', ACCOUNT_JS)
        self.assertIn("showCatalog('kintai')", ACCOUNT_JS)


if __name__ == '__main__':
    unittest.main()
