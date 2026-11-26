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
        # 1行目ご依頼先・2行目集配曜日、「ご依頼内容の選択」は依頼内容の選択欄のすぐ上
        self.assertIn("head.innerHTML='<strong id=\"customer-scope-name\"></strong>'", js)
        self.assertIn("($('#request-type-field')||head).before(label)", js)
        css = (ROOT / 'request-web.css').read_text(encoding='utf-8')
        self.assertIn('.context.customer-request .customer-scope-days[hidden]{display:none}', css)


if __name__ == '__main__':
    unittest.main()
