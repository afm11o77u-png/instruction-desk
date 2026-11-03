import unittest
from pathlib import Path
JS=(Path(__file__).resolve().parents[1]/'static'/'app.js').read_text(encoding='utf-8')
class BootLoadErrorTest(unittest.TestCase):
    def test_error_message_instead_of_blank(self):
        self.assertIn("読み込みに失敗しました。再起動.cmdで再起動してください。",JS)
        self.assertNotIn("boot().catch(e=>{$('#main').textContent=e.message})",JS)
        self.assertIn("m.textContent=STOCK_LOAD_ERROR",JS)
        self.assertIn("!state.bootFailed",JS)
    def test_response_shape_checked(self):
        self.assertIn("throw new Error(STOCK_LOAD_ERROR)",JS)
        self.assertIn("Array.isArray(state.stock.lots)||Array.isArray(state.stock.groups)",JS)
if __name__=='__main__':unittest.main()
