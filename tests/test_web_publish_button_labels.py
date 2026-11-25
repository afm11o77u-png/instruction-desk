"""WEB依頼の設定: 公開・更新 / 受付停止 button labels."""
import unittest
from pathlib import Path
import web_requests as w
import test_web_request_settings as fixtures

ROOT = Path(__file__).resolve().parents[1]


class PublishButtonLabelTest(unittest.TestCase):
    def test_js_sets_new_labels_and_notices(self):
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        html = (ROOT / 'request-web.html').read_text(encoding='utf-8')
        self.assertIn("publish.textContent='公開・更新'", js)
        self.assertIn("pause.textContent='受付停止'", js)
        self.assertIn("publish.disabled=!p||busy", js)
        self.assertIn("pause.disabled=!p||busy", js)
        self.assertIn("notify(action==='pause'?'受付を停止しました。':'公開・更新しました。')", js)
        self.assertNotIn('保存済みの契約・制服を依頼画面へ反映しました。', js)
        self.assertNotIn('選択した対象の受付を停止しますか？', js)
        # labels come from JS, not an HTML edit in this change
        self.assertIn('id="web-publish-common"', html)
        self.assertIn('id="web-pause-common"', html)

    def test_server_asks_to_publish_company_first(self):
        src = (ROOT / 'web_requests.py').read_text(encoding='utf-8')
        self.assertIn('先に会社全体の共通設定を「公開・更新」してください。', src)
        self.assertNotIn('先に顧客共通の設定を依頼画面へ反映してください。', src)


if __name__ == '__main__':
    unittest.main()
