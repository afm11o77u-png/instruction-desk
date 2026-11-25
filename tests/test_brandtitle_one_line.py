"""左メニューの業務名が、ロゴ横で1行に収まることを確認する。"""
import http.server
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TITLES = [
    '指示書デスク',
    '顧客・商品情報',
    '在庫管理',
    'ユニフォーム管理',
    'WEB依頼',
    '顧客・契約管理',
    'マーク管理',
]


def playwright_available():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


class BrandTitleSourceTest(unittest.TestCase):
    def test_title_stays_on_one_line_in_shared_sidebar_css(self):
        style = (ROOT / 'style.css').read_text(encoding='utf-8')
        picking = (ROOT / 'picking.css').read_text(encoding='utf-8')
        title = __import__('re').search(r'#brandtitle\{([^}]+)\}', style).group(1)
        self.assertIn('white-space:nowrap', title)
        self.assertNotIn('word-break:break-word', title)
        self.assertNotIn('overflow-wrap:anywhere', title)
        ptitle = __import__('re').search(r'aside\.deskmenu \.brand>span\{([^}]+)\}', picking).group(1)
        self.assertIn('white-space:nowrap', ptitle)
        self.assertNotIn('word-break:break-word', ptitle)
        row = __import__('re').search(r'\.brandrow\{([^}]+)\}', style).group(1)
        self.assertNotIn('max-width:55%', row)


@unittest.skipUnless(playwright_available(), 'playwright が必要です')
class BrandTitleBrowserTest(unittest.TestCase):
    def test_longest_names_fit_one_line_in_sidebar(self):
        from playwright.sync_api import sync_playwright

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(ROOT), **kwargs)

            def log_message(self, *args):
                pass

        html = (
            '<!doctype html><meta charset="utf-8">'
            '<link rel="stylesheet" href="/style.css">'
            '<aside><div class="brand"><span id="brandtitle">指示書デスク</span>'
            '<div class="brandrow"><img class="brandlogo" src="/logo.jpg" alt="">'
            '<img class="mascots" src="/mascots.png" alt=""></div></div></aside>'
        )
        fixture = ROOT / 'tests/_brandtitle_fixture.html'
        fixture.write_text(html, encoding='utf-8')
        httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                page = p.chromium.launch(headless=True).new_page(viewport={'width': 1280, 'height': 800})
                page.goto(
                    f'http://127.0.0.1:{httpd.server_address[1]}/tests/_brandtitle_fixture.html',
                    wait_until='networkidle',
                )
                page.evaluate('document.fonts && document.fonts.ready')
                for title in TITLES:
                    info = page.evaluate(
                        '''(title) => {
                          const el = document.getElementById('brandtitle');
                          el.textContent = title;
                          const aside = document.querySelector('aside').getBoundingClientRect();
                          const brand = document.querySelector('.brand').getBoundingClientRect();
                          const box = el.getBoundingClientRect();
                          const logo = document.querySelector('.brandlogo').getBoundingClientRect();
                          const mascots = document.querySelector('.mascots').getBoundingClientRect();
                          const right = Math.max(box.right, logo.right, mascots.right, brand.right);
                          return {
                            lines: el.getClientRects().length,
                            overflow: Math.max(0, right - aside.right),
                            clipped: el.scrollWidth > el.clientWidth + 0.5,
                          };
                        }''',
                        title,
                    )
                    self.assertEqual(info['lines'], 1, title)
                    self.assertLess(info['overflow'], 1, title)
                    self.assertFalse(info['clipped'], title)
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            fixture.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
