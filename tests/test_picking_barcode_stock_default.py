import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BarcodeStockDefaultTest(unittest.TestCase):
    def test_initial_stock_is_ishikari_and_empty_guard_kept(self):
        js = (ROOT / 'picking.js').read_text(encoding='utf-8')
        html = (ROOT / 'picking.html').read_text(encoding='utf-8')
        self.assertIn("if(scanStock&&!scanStock.value)scanStock.value='warehouse';", js)
        self.assertIn('if(!code||!stock)return;', js)
        self.assertIn('<select id="pickbarcodestock" required>', html)
        self.assertIn('<option value="warehouse">石狩倉庫</option>', html)


if __name__ == '__main__':
    unittest.main()
