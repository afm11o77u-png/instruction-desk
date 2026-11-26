"""②制服の詳細から契約ごとの写真欄を外し、依頼画面は商品の共通写真だけを使う。"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ContractPhotoRemovedTest(unittest.TestCase):
    def test_contract_photo_not_used_or_shown(self):
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        css = (ROOT / 'request-web.css').read_text(encoding='utf-8')
        self.assertNotIn('photo:u.photo||', js)
        self.assertNotIn('<img src="${esc(u.photo)}"', js)
        self.assertIn("photo:(pd.photo_version?'/api/web-request/product-photo/'", js)
        self.assertIn('label:has(> #uniform-photo),#photo-preview,#clear-photo{display:none!important}', css)


if __name__ == '__main__':
    unittest.main()
