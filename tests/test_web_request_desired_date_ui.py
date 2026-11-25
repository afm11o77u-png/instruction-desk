"""Headless check that the order form restricts 納品希望日."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HTML = '''<!doctype html><meta charset="utf-8">
<body>
<form id="request-form">
  <label>納品希望日<input id="delivery" type="date" required></label>
</form>
<select id="scope-select"><option value="shop">店</option></select>
</body>'''


def extract_helpers(src):
    start = src.find("const WEEKDAYS_JP=")
    end = src.find('window.WebRequestDelivery=')
    if start < 0 or end < 0:
        raise AssertionError('delivery helpers missing')
    # include the window assignment line
    end = src.find('\n', src.find(';', end)) + 1
    return src[start:end]


class DesiredDateUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def test_autoset_collection_day_and_hint(self):
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        helpers = extract_helpers(js)
        self.assertNotIn('id="delivery-hint"', (ROOT / 'request-web.html').read_text(encoding='utf-8'))
        page = self.browser.new_page()
        page.set_content(HTML)
        page.add_script_tag(content='''
          const $ = s => document.querySelector(s);
          let cart = [], products = [], mode = 'order';
          const data = {organizations:{nodes:[
            {id:'co',parent_id:null,kind:'company',customer_id:1,delivery_method:''},
            {id:'area',parent_id:'co',kind:'region',customer_id:1,delivery_method:'自社集配',collection_days:['月','木']},
            {id:'shop',parent_id:'area',kind:'site',customer_id:1,delivery_method:''}
          ]}};
          const cid = () => 1;
          window.__notices = [];
          function notify(s){window.__notices.push(s)}
        ''' + helpers + '''
          window.WebRequestDelivery.today = '2026-11-26';
          syncDesiredDelivery();
        ''')
        value = page.locator('#delivery').input_value()
        self.assertEqual(value, '2026-11-30')
        hint = page.locator('#delivery-hint').inner_text()
        self.assertIn('次回の集配日は11月30日（月）です', hint)
        self.assertEqual(page.locator('#delivery').get_attribute('min'), '2026-11-28')
        # picking a Saturday should snap to the next 月/木
        page.fill('#delivery', '2026-11-28')
        page.locator('#delivery').evaluate('el => el.dispatchEvent(new Event("change",{bubbles:true}))')
        self.assertEqual(page.locator('#delivery').input_value(), '2026-11-30')
        notices = page.evaluate('() => window.__notices')
        self.assertTrue(any('11月30日' in s for s in notices))
        # adding a 裾上げ item of 5 days: earliest Dec 3 (Thu) which is allowed
        page.evaluate('''() => {
          cart.push({id:'u1',hem_processing:true,processing_days:5});
          products.push({id:'u1',hem_processing:true,processing_days:5});
          syncDesiredDelivery();
        }''')
        self.assertEqual(page.locator('#delivery').input_value(), '2026-12-03')
        self.assertIn('裾上げ5日', page.locator('#delivery-hint').inner_text())
        # courier: any day from earliest
        page.evaluate('''() => {
          cart.length = 0; products.length = 0;
          data.organizations.nodes.find(n=>n.id==='shop').delivery_method='佐川便';
          data.organizations.nodes.find(n=>n.id==='shop').collection_days=[];
          document.querySelector('#delivery').value='';
          syncDesiredDelivery();
        }''')
        self.assertEqual(page.locator('#delivery').input_value(), '2026-11-28')
        page.fill('#delivery', '2026-11-29')
        page.locator('#delivery').evaluate('el => el.dispatchEvent(new Event("change",{bubbles:true}))')
        self.assertEqual(page.locator('#delivery').input_value(), '2026-11-29')
        page.close()


if __name__ == '__main__':
    unittest.main()
