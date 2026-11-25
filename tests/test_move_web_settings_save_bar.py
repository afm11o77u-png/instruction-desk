"""Move the WEB依頼の設定 save bar below section ② and rename the button."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MoveWebSettingsSaveBarTest(unittest.TestCase):
    def test_js_moves_bar_renames_button_and_updates_messages(self):
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        org = (ROOT / 'request-organizations.js').read_text(encoding='utf-8')
        html = (ROOT / 'request-web.html').read_text(encoding='utf-8')
        self.assertIn("function placeWebPageSaveBar()", js)
        self.assertIn('editors.after(bar)', js)
        self.assertIn("save.textContent='ページ・利用者の設定を保存'", js)
        self.assertIn('①・②で適用した内容をまとめて保存します。「保存済みを再読込」で未保存の変更を取り消します。', js)
        self.assertIn('placeWebPageSaveBar();', js)
        self.assertIn('先に「ページ・利用者の設定を保存」を押してください。', js)
        self.assertNotIn('先にWEB依頼の設定を保存してください。', js)
        self.assertIn('先に編集中の内容を適用し、「ページ・利用者の設定を保存」を押してください。', org)
        self.assertIn('「ページ・利用者の設定を保存」で保存してください。', org)
        self.assertIn('if(pageSaveBtn)pageSaveBtn.hidden=!admin()', org)
        # HTML is left unchanged (local user edits); labels are applied in JS
        self.assertIn('id="web-page-save"', html)
        self.assertIn('id="web-page-editors"', html)
        self.assertIn('WEB依頼の設定をまとめて保存', html)


if __name__ == '__main__':
    unittest.main()
