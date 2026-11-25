"""左メニューはロゴを最上段に置き、その下の業務名を1行で表示する。"""
import http.server
import re
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
    def test_logo_sits_above_a_single_line_title(self):
        style = (ROOT / 'style.css').read_text(encoding='utf-8')
        picking = (ROOT / 'picking.css').read_text(encoding='utf-8')
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        picking_html = (ROOT / 'picking.html').read_text(encoding='utf-8')
        brand = re.search(r'\.brand\{([^}]+)\}', style).group(1)
        self.assertIn('flex-direction:column', brand)
        title = re.search(r'#brandtitle\{([^}]+)\}', style).group(1)
        self.assertIn('white-space:nowrap', title)
        self.assertNotIn('word-break:break-word', title)
        logo = re.search(r'\.brand \.brandlogo\{([^}]+)\}', style).group(1)
        self.assertIn('flex:0 0 auto', logo)
        self.assertIn('max-width:112px', logo)
        self.assertIn('max-height:28px', logo)
        self.assertNotIn('min-width:0', logo)
        pbrand = re.search(r'aside\.deskmenu \.brand\{([^}]+)\}', picking).group(1)
        self.assertIn('flex-direction:column', pbrand)
        ptitle = re.search(r'aside\.deskmenu \.brand>span\{([^}]+)\}', picking).group(1)
        self.assertIn('white-space:nowrap', ptitle)
        aside = index[index.index('<aside>'):index.index('</aside>')]
        top = aside.split('class="workspace-back"', 1)[0]
        self.assertLess(top.index('id="brandimages"'), top.index('id="brandtitle"'))
        self.assertLess(top.index('id="brandtitle"'), aside.index('業務を切り替える'))
        menu = picking_html[picking_html.index('aside class="deskmenu"'):picking_html.index('</aside>')]
        self.assertLess(menu.index('src="/logo.jpg"'), menu.index('指示書デスク'))


@unittest.skipUnless(playwright_available(), 'playwright が必要です')
class BrandTitleBrowserTest(unittest.TestCase):
    def test_logo_stays_full_size_above_one_line_titles(self):
        from playwright.sync_api import sync_playwright

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(ROOT), **kwargs)

            def log_message(self, *args):
                pass

        html = (
            '<!doctype html><meta charset="utf-8">'
            '<link rel="stylesheet" href="/style.css">'
            '<aside><div class="brand">'
            '<div class="brandrow"><img class="brandlogo" src="/logo.jpg" alt="">'
            '<img class="mascots" src="/mascots.png" alt=""></div>'
            '<span id="brandtitle">指示書デスク</span></div>'
            '<a class="workspace-back" href="/">業務を切り替える</a>'
            '<div id="desknav"><button type="button" class="nav">指示書一覧</button></div>'
            '</aside>'
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
                sizes = []
                for title in TITLES:
                    info = page.evaluate(
                        '''(title) => {
                          const el = document.getElementById('brandtitle');
                          el.textContent = title;
                          const aside = document.querySelector('aside').getBoundingClientRect();
                          const logo = document.querySelector('.brandlogo').getBoundingClientRect();
                          const mascots = document.querySelector('.mascots').getBoundingClientRect();
                          const box = el.getBoundingClientRect();
                          const switcher = document.querySelector('.workspace-back').getBoundingClientRect();
                          const nav = document.querySelector('#desknav').getBoundingClientRect();
                          const right = Math.max(box.right, logo.right, mascots.right);
                          return {
                            lines: el.getClientRects().length,
                            overflow: Math.max(0, right - aside.right),
                            clipped: el.scrollWidth > el.clientWidth + 0.5,
                            logoW: logo.width, logoH: logo.height,
                            stacked: logo.bottom <= box.top + 0.5 && box.bottom <= switcher.top + 0.5 && switcher.bottom <= nav.top + 0.5,
                          };
                        }''',
                        title,
                    )
                    sizes.append((info['logoW'], info['logoH']))
                    self.assertEqual(info['lines'], 1, title)
                    self.assertLess(info['overflow'], 1, title)
                    self.assertFalse(info['clipped'], title)
                    self.assertTrue(info['stacked'], title)
                    # logo.jpg is wide (240x30); max-width:112px is the binding cap.
                    self.assertGreaterEqual(info['logoW'], 111)
                    self.assertLessEqual(info['logoW'], 113)
                    self.assertGreaterEqual(info['logoH'], 18)
                widths = {round(w, 2) for w, _h in sizes}
                self.assertEqual(len(widths), 1, 'logo width should not shrink with longer titles')
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            fixture.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
