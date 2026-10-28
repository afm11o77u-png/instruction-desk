"""指示書デスク左側メニューの目的別グループ構成。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
ACCOUNT = (ROOT / 'account.js').read_text(encoding='utf-8')
APP = (ROOT / 'app.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'style.css').read_text(encoding='utf-8')


def _desknav() -> str:
    aside = INDEX[INDEX.index('<aside>'):INDEX.index('</aside>')]
    start = aside.index('id="desknav"')
    end = aside.index('id="infonav"')
    return aside[start:end]


class DeskNavGroupsTest(unittest.TestCase):
    def test_group_order_and_labels(self):
        nav = _desknav()
        expected = [
            ('指示書の作成・確認', ['指示書一覧', '下書き']),
            ('現場の作業・出荷', ['現場の作業一覧', 'ピッキングリスト', '出荷仕分け']),
            ('管理担当持込', ['入力', '出力']),
            ('印刷', ['宛名書き・管理票印刷']),
        ]
        pos = 0
        for caption, items in expected:
            i = nav.index(caption, pos)
            self.assertGreaterEqual(i, pos)
            pos = i
            for item in items:
                j = nav.index(f'>{item}<', pos)
                self.assertGreater(j, pos)
                pos = j
        self.assertIn('data-view="labels"', nav)
        self.assertIn('>宛名書き・管理票印刷<', nav)
        self.assertNotIn('>宛名書き<', nav)
        # 利用者管理・管理・修正は区切り線下の desksettings（desknav外）
        self.assertNotIn('id="manageusers"', nav)
        self.assertNotIn('id="repair-open"', nav)
        self.assertNotIn('設定・管理', nav)
        aside = INDEX[INDEX.index('<aside>'):INDEX.index('</aside>')]
        footer = aside[aside.index('id="sidebarfooter"'):]
        self.assertIn('id="desksettings"', footer)
        self.assertIn('id="manageusers"', footer)
        self.assertIn('id="repair-open"', footer)
        self.assertLess(footer.index('manageusers'), footer.index('repair-open'))
        self.assertNotIn('管理担当者の処理', nav)
        self.assertNotIn('管理担当持込(入力)', nav)
        self.assertNotIn('管理担当持込(出力)', nav)
        # 2ボタンは横並び1行
        self.assertIn('class="navrow"', nav)
        self.assertEqual(nav.count('class="navrow"'), 2)

    def test_view_targets_unchanged(self):
        nav = _desknav()
        for view in ('all', 'draft', 'floor', 'picking', 'ship', 'carry-in', 'carry-out', 'labels'):
            self.assertIn(f'data-view="{view}"', nav)

    def test_info_and_stock_nav_untouched(self):
        aside = INDEX[INDEX.index('<aside>'):INDEX.index('</aside>')]
        info = aside[aside.index('id="infonav"'):aside.index('id="stocknav"')]
        self.assertIn('data-view="placement"', info)
        self.assertIn('data-view="shipinfo"', info)
        self.assertIn('data-view="nameinfo"', info)
        self.assertNotIn('navgroup', info)
        stock = aside[aside.index('id="stocknav"'):aside.index('id="kintainav"')]
        self.assertIn('data-stock-section="list"', stock)
        self.assertNotIn('navgroup', stock)
        kintai = aside[aside.index('id="kintainav"'):]
        self.assertIn('data-kintai-section="attendance"', kintai)
        self.assertIn('data-kintai-section="shift"', kintai)
        self.assertIn('data-kintai-section="bihin"', kintai)
        self.assertIn('>出勤簿<', kintai)
        self.assertIn('>シフト表<', kintai)
        self.assertIn('>備品棚卸<', kintai)

    def test_settings_stay_permissioned(self):
        self.assertIn("manageNav.hidden=!(currentUser.manage||role==='owner')", ACCOUNT)
        self.assertIn("repairNav.hidden=role!=='owner'", ACCOUNT)
        self.assertNotIn('id="modekintai"', INDEX)
        self.assertNotIn('>勤怠管理<', INDEX)
        self.assertNotIn('data-catalog="kintai"', INDEX.split('id="modes"')[1].split('id="desknav"')[0])
        self.assertIn('id="kintainav"', INDEX)
        self.assertNotIn('kintai-open', INDEX)
        self.assertIn('mark_kintai', ACCOUNT)
        self.assertIn('id="markmanage"', ACCOUNT)
        self.assertIn("showCatalog('kintai')", ACCOUNT)
        self.assertIn("openAccounts()", ACCOUNT)
        self.assertIn("openRepair()", ACCOUNT)
        # ログイン欄からは業務メニューへ移した（利用者管理・管理・修正は desksettings のまま）
        self.assertNotIn('利用者管理</button>', ACCOUNT.split('block.innerHTML')[1].split('`;')[0])
        self.assertNotIn('管理・修正</button>', ACCOUNT.split('block.innerHTML')[1].split('`;')[0])
        # マーク管理はログイン者ブロック内（氏名・権限の上、パスワード変更の前）
        bar = ACCOUNT.split('block.innerHTML')[1].split('`;')[0]
        self.assertIn('>マーク管理<', bar)
        self.assertLess(bar.index('markmanage'), bar.index('mypassword'))
        self.assertLess(bar.index('markmanage'), bar.index('currentUser.name'))
        # ボタン風（quiet テキストリンクではなく枠付きコントロール）
        self.assertIn('id="markmanage"', bar)
        self.assertNotIn('id="markmanage" class="quiet"', bar)
        self.assertIn("role==='owner'||!!currentUser.mark_kintai", ACCOUNT)
        # navrow 内のボタンも空グループ判定に含める
        self.assertIn("g.querySelectorAll('.nav')", ACCOUNT)
        self.assertNotIn(":scope > .nav", ACCOUNT)

    def test_labels_title_renamed(self):
        self.assertIn("labels?'宛名書き・管理票印刷':", APP)

    def test_pair_row_styles(self):
        self.assertIn('#desknav .navrow', STYLE)
        self.assertIn('#desknav .navrow .nav', STYLE)
        self.assertRegex(STYLE, r'#desknav\.navrow|#desknav \.navrow\{[^}]*display:flex')
        self.assertRegex(STYLE, r'#desknav \.navrow \.nav\{[^}]*flex:1 1 0')

    def test_scroll_and_wrap_styles(self):
        self.assertIn('overflow-x:hidden', STYLE)
        self.assertIn('overflow-y:auto', STYLE)
        self.assertIn('overflow-wrap:anywhere', STYLE)
        self.assertIn('#desknav .navgroup', STYLE)
        self.assertRegex(STYLE, r'#desknav\{[^}]*display:flex')


if __name__ == '__main__':
    unittest.main()
