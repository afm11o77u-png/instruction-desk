"""顧客向け WEB依頼の納品先・受取場所を出さない。"""
import http.server
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def playwright_available():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


class HideDeliveryFieldsSourceTest(unittest.TestCase):
    def test_order_form_hides_fields_and_drops_them_from_payload(self):
        css = (ROOT / 'request-web.css').read_text(encoding='utf-8')
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        self.assertIn('#order #request-form label:has(#delivery-to)', css)
        self.assertIn('#order #request-form label:has(#receive-place)', css)
        self.assertNotIn("deliveryTo:$('#delivery-to').value", js)
        self.assertNotIn("receivePlace:$('#receive-place').value", js)
        self.assertNotIn("${esc(r.deliveryTo||'')}", js)
        self.assertNotIn("${esc(r.receivePlace||'')}", js)
        self.assertIn('納品希望日', js)
        self.assertIn("$('#delivery').value", js)


@unittest.skipUnless(playwright_available(), 'playwright が必要です')
class HideDeliveryFieldsBrowserTest(unittest.TestCase):
    def test_order_fields_are_hidden_and_settings_defaults_remain(self):
        from playwright.sync_api import sync_playwright

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(ROOT), **kwargs)

            def log_message(self, *args):
                pass

        html = '''<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="/request-web.css">
<section id="order">
  <form id="request-form">
    <label>納品先<input id="delivery-to"></label>
    <label>受取場所<input id="receive-place"></label>
    <label>着用者名<input id="wearer" required></label>
    <label>納品希望日<input id="delivery" type="date" required></label>
  </form>
</section>
<section id="settings">
  <label>標準の納品先<input id="default-delivery"></label>
  <label>標準の受取場所<input id="default-receive"></label>
</section>
'''
        fixture = ROOT / 'tests/_hide_delivery_fixture.html'
        fixture.write_text(html, encoding='utf-8')
        httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                page = p.chromium.launch(headless=True).new_page(viewport={'width': 1280, 'height': 800})
                page.goto(
                    f'http://127.0.0.1:{httpd.server_address[1]}/tests/_hide_delivery_fixture.html',
                    wait_until='networkidle',
                )
                self.assertFalse(page.locator('#order label:has(#delivery-to)').is_visible())
                self.assertFalse(page.locator('#order label:has(#receive-place)').is_visible())
                self.assertTrue(page.locator('#delivery').is_visible())
                self.assertTrue(page.locator('#wearer').is_visible())
                self.assertTrue(page.locator('#default-delivery').is_visible())
                self.assertTrue(page.locator('#default-receive').is_visible())
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            fixture.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
