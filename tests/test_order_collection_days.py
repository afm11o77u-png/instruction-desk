"""依頼画面：ご依頼先の下に集配曜日（登録がある店舗だけ）。"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OrderCollectionDaysTest(unittest.TestCase):
    def test_days_line_uses_same_delivery_source(self):
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        self.assertIn("const d=resolveDelivery(selectedOrgNode());return d.days.length?'集配曜日：'+d.days.join('・'):''", js)
        self.assertIn("name.after(el)}el.textContent=text;el.hidden=!text", js)
        self.assertIn("el=document.createElement('div')", js)  # .context p（案内文）を奪わない


if __name__ == '__main__':
    unittest.main()
