"""Hide the customer-contracts guide box and bottom link on WEB依頼の設定."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HideWebSettingsGuidesTest(unittest.TestCase):
    def test_css_hides_guides_and_js_guards_links(self):
        css = (ROOT / 'request-web.css').read_text(encoding='utf-8')
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        org = (ROOT / 'request-organizations.js').read_text(encoding='utf-8')
        html = (ROOT / 'request-web.html').read_text(encoding='utf-8')
        self.assertIn('#web-settings .web-common-settings', css)
        self.assertIn('#web-settings p:has(#web-organization-link)', css)
        self.assertIn('display:none!important', css[css.find('#web-settings .web-common-settings'):css.find('#web-settings .web-common-settings')+220])
        self.assertIn('const settingsLink=$(\'#common-settings-link\')', js)
        self.assertIn('if(settingsLink)', js)
        self.assertIn('const orgLink=$(\'#web-organization-link\')', org)
        self.assertIn('if(orgLink)', org)
        # elements stay in HTML; publication hint is kept
        self.assertIn('web-common-settings', html)
        self.assertIn('id="web-organization-link"', html)
        self.assertIn('契約・制服の変更は「顧客・契約管理」で行います。', html)


if __name__ == '__main__':
    unittest.main()
