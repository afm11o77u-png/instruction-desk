"""顧客・契約管理の案内ページを外し、作成順がわかるヘッダーにする。"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CustomerContractsNavTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        cls.css = (ROOT / 'request-web.css').read_text(encoding='utf-8')
        cls.html = (ROOT / 'request-web.html').read_text(encoding='utf-8')
        cls.switcher = (ROOT / 'workspace-switcher.js').read_text(encoding='utf-8')

    def test_shared_nav_drops_setup_and_shows_creation_order(self):
        nav_start = self.js.find("$('header nav').innerHTML=")
        self.assertGreater(nav_start, 0)
        nav = self.js[nav_start:nav_start + 900]
        self.assertIn('data-tab="customers"', nav)
        self.assertIn('顧客一覧', nav)
        self.assertIn('contracts-nav-directory', nav)
        self.assertIn('①', nav)
        self.assertIn('組織構成・担当者', nav)
        self.assertIn('→', nav)
        self.assertIn('②', nav)
        self.assertIn('部署・契約・制服', nav)
        self.assertIn('③', nav)
        self.assertIn('拠点の詳細', nav)
        self.assertNotIn('顧客の設定', nav)
        self.assertNotIn('data-tab="setup"', nav)
        # request-web.html itself is not edited; its WEB依頼用ボタンは残る
        self.assertIn('data-tab="setup"', self.html)
        self.assertIn('顧客・契約管理', self.html)

    def test_setup_page_is_redirected_to_organization(self):
        self.assertIn("const requestedSetup=id==='setup'", self.js)
        self.assertIn("if(requestedSetup)id='organization'", self.js)
        self.assertIn('requestedSetup||!updateHistory?history.replaceState:history.pushState', self.js)
        self.assertIn("$('#setup').hidden=true", self.js)
        self.assertNotIn("$('#setup').hidden=false", self.js)

    def test_web_and_rental_remain_reachable(self):
        self.assertIn("data-common-destination=\"web\"", self.js)
        self.assertIn("data-common-destination=\"rental\"", self.js)
        self.assertIn('WEB依頼設定', self.js)
        self.assertIn('レンタル管理', self.js)
        self.assertIn("['rental','ユニフォーム管理'", self.switcher)
        self.assertIn("['web','WEB依頼'", self.switcher)

    def test_css_promotes_directory_and_wraps_flow(self):
        self.assertIn('button[data-tab=customers].contracts-nav-directory', self.css)
        self.assertIn('background:#236644', self.css)
        self.assertIn('.contracts-nav-flow', self.css)
        self.assertIn('flex-wrap:wrap', self.css[self.css.find('.contracts-nav-flow'):])
        self.assertIn('@media(max-width:800px)', self.css[self.css.find('.contracts-nav-flow') - 200:])


if __name__ == '__main__':
    unittest.main()
