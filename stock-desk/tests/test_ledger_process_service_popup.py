import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')


class ProcessServicePopupTest(unittest.TestCase):
    def test_picker_is_popup_with_search(self):
        self.assertIn('class="pl-svc-pop"', JS)
        self.assertIn('data-pl-svc-filter', JS)
        self.assertIn("'加工なし'", JS)
        self.assertIn('.pl-svc-list{max-height', CSS)

    def test_applies_on_close_and_on_save(self):
        self.assertIn('pop.ontoggle', JS)
        self.assertIn('[data-dirty]', JS)


if __name__ == '__main__':
    unittest.main()
