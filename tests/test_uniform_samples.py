import base64,json,re,unittest
from pathlib import Path
import web_request_product_display as pd

ROOT=Path(__file__).resolve().parent.parent/'uniform-samples'


class UniformSamplesTest(unittest.TestCase):
    def test_manifest_files_exist_and_are_valid_photos(self):
        man=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(man['items']),23)
        for x in man['items']:
            self.assertIn(x['category'],man['categories'])
            self.assertRegex(x['file'],r'^[a-z0-9_]+\.jpg$')  # サーバーの配信条件と同じ
            raw=(ROOT/x['file']).read_bytes()
            got,mime=pd.decode_photo('data:image/jpeg;base64,'+base64.b64encode(raw).decode())  # アップロードと同じ検査を通る
            self.assertEqual(mime,'image/jpeg')
