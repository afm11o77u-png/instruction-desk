"""左メニューはロゴとキャラクターを横一列・幅いっぱいに置き、その下の業務名を1行で表示する。"""
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
        row = re.search(r'\.brandrow\{([^}]+)\}', style).group(1)
        self.assertIn('display:grid', row)
        self.assertIn('grid-template-columns', row)
        logo = re.search(r'\.brand \.brandlogo\{([^}]+)\}', style).group(1)
        mascot = re.search(r'\.brand \.mascots\{([^}]+)\}', style).group(1)
        self.assertIn('height:auto', logo)
        self.assertIn('object-fit:contain', logo)
        self.assertIn('aspect-ratio:250/36', logo)
        self.assertNotIn('max-width:112px', logo)
        self.assertNotIn('max-height:28px', logo)
        self.assertIn('object-fit:contain', mascot)
        self.assertIn('aspect-ratio:162/146', mascot)
        self.assertNotIn('max-width:40px', mascot)
        self.assertNotIn('max-height:36px', mascot)
        pbrand = re.search(r'aside\.deskmenu \.brand\{([^}]+)\}', picking).group(1)
        self.assertIn('flex-direction:column', pbrand)
        prow = re.search(r'aside\.deskmenu \.brandrow\{([^}]+)\}', picking).group(1)
        self.assertIn('display:grid', prow)
        self.assertIn('grid-template-columns', prow)
        ptitle = re.search(r'aside\.deskmenu \.brand>span\{([^}]+)\}', picking).group(1)
        self.assertIn('white-space:nowrap', ptitle)
        aside = index[index.index('<aside>'):index.index('</aside>')]
        top = aside.split('class="workspace-back"', 1)[0]
        self.assertLess(top.index('id="brandimages"'), top.index('id="brandtitle"'))
        self.assertLess(top.index('id="brandtitle"'), aside.index('業務を切り替える'))
        menu = picking_html[picking_html.index('aside class="deskmenu"'):picking_html.index('</aside>')]
        self.assertLess(menu.index('src="/logo.jpg"'), menu.index('指示書デスク'))
        js = (ROOT / 'workspace-switcher.js').read_text(encoding='utf-8')
        self.assertIn('function placeSidebarSwitch()', js)
        self.assertIn('title.before(link)', js)
        self.assertIn('.brand:has(.workspace-back){gap:0}', style)
        self.assertIn('margin:12px 0', style)
        self.assertIn('aside.deskmenu .brand:has(.workspace-back){gap:0}', picking)


@unittest.skipUnless(playwright_available(), 'playwright が必要です')
class BrandTitleBrowserTest(unittest.TestCase):
    def test_logo_and_mascots_fill_row_above_one_line_titles(self):
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
            '<a class="workspace-back" href="/" data-workspace-switch>業務を切り替える</a>'
            '<div id="desknav"><button type="button" class="nav">指示書一覧</button></div>'
            '</aside>'
            '<script src="/workspace-switcher.js"></script>'
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
                          const row = document.querySelector('.brandrow').getBoundingClientRect();
                          const cs = getComputedStyle(document.querySelector('.brandlogo'));
                          const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
                          const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
                          const right = Math.max(box.right, logo.right, mascots.right);
                          return {
                            lines: el.getClientRects().length,
                            overflow: Math.max(0, right - aside.right),
                            clipped: el.scrollWidth > el.clientWidth + 0.5,
                            logoW: logo.width, logoH: logo.height,
                            mascotW: mascots.width, mascotH: mascots.height,
                            rowW: row.width,
                            spanW: mascots.right - logo.left,
                            logoLeft: logo.left - row.left,
                            mascotRightGap: row.right - mascots.right,
                            sideBySide: logo.right <= mascots.left + 0.5,
                            sameRow: Math.abs(logo.top - mascots.top) < 2 && Math.abs(logo.bottom - mascots.bottom) < 2,
                            contentW: logo.width - padX,
                            contentH: logo.height - padY,
                            stacked: row.bottom <= switcher.top + 0.5 && switcher.bottom <= box.top + 0.5 && box.bottom <= nav.top + 0.5,
                            gapAbove: switcher.top - row.bottom,
                            gapBelow: box.top - switcher.bottom,
                          };
                        }''',
                        title,
                    )
                    sizes.append((info['logoW'], info['logoH'], info['mascotW'], info['mascotH']))
                    self.assertEqual(info['lines'], 1, title)
                    self.assertLess(info['overflow'], 1, title)
                    self.assertFalse(info['clipped'], title)
                    self.assertTrue(info['stacked'], title)
                    self.assertGreaterEqual(info['gapAbove'], 10, title)
                    self.assertGreaterEqual(info['gapBelow'], 10, title)
                    self.assertTrue(info['sideBySide'], title)
                    self.assertTrue(info['sameRow'], title)
                    self.assertLess(info['logoLeft'], 1, title)
                    self.assertLess(info['mascotRightGap'], 1, title)
                    self.assertGreaterEqual(info['spanW'], info['rowW'] - 1)
                    self.assertLessEqual(info['spanW'], info['rowW'] + 1)
                    self.assertAlmostEqual(info['logoH'], info['mascotH'], delta=2)
                    self.assertGreater(info['logoH'], 18)
                    self.assertAlmostEqual(info['logoW'] / info['logoH'], 250 / 36, delta=0.35)
                    self.assertAlmostEqual(info['mascotW'] / info['mascotH'], 162 / 146, delta=0.35)
                widths = {round(w, 2) for w, _h, _mw, _mh in sizes}
                self.assertEqual(len(widths), 1, 'logo width should not shrink with longer titles')
        finally:
            httpd.shutdown()
            thread.join(timeout=5)
            fixture.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
