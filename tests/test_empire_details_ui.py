"""Headless Chromium checks for injected 裾上げ fields, 拠点の詳細, and org multi-select."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HTML = '''<!doctype html><meta charset="utf-8">
<style>__CSS__</style>
<body>
<header><nav></nav></header>
<section id="settings">
  <form id="uniform-form">
    <label>表示名<input id="display-name"></label>
    <label>枚数<input id="uniform-qty" type="number" value="3"></label>
    <label>説明<textarea id="uniform-description"></textarea></label>
  </form>
  <div id="setting-list"></div>
</section>
<section id="organization">
  <div class="settings-grid org-structure-layout">
    <section class="org-diagram-panel">
      <div id="org-tree" style="position:relative;width:600px;height:300px;border:1px solid #ccc">
        <button type="button" class="org-diagram-card" data-org-select="a" data-org-kind="site" style="position:absolute;left:20px;top:40px;width:120px;height:80px">札幌店</button>
        <button type="button" class="org-diagram-card" data-org-select="b" data-org-kind="site" style="position:absolute;left:200px;top:40px;width:120px;height:80px">仙台店</button>
        <button type="button" class="org-diagram-card" data-org-select="c" data-org-kind="site" style="position:absolute;left:380px;top:40px;width:120px;height:80px">函館店</button>
      </div>
    </section>
    <section id="org-editor"><h3>組織を追加</h3></section>
  </div>
</section>
</body>'''


def extract(src, name):
    m = re.search(r'function %s\b' % re.escape(name), src)
    if not m:
        raise AssertionError('missing ' + name)
    start = m.start()
    # take until next top-level function at column 0 or "window."
    rest = src[start:]
    n = re.search(r'\nfunction |\nwindow\.', rest[10:])
    return rest if not n else rest[:10+n.start()]


class EmpireDetailsUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def test_injected_ui_and_rubber_band(self):
        css = (ROOT / 'request-web.css').read_text(encoding='utf-8')
        webjs = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        orgjs = (ROOT / 'request-organizations.js').read_text(encoding='utf-8')
        page = self.browser.new_page()
        page.set_content(HTML.replace('__CSS__', css.replace('</', '<\\/')))
        # stub + inject functions from production source
        page.evaluate(
            '''() => { window.$ = s => document.querySelector(s); }'''
        )
        # hem + details
        snippets = []
        for name in ('syncHemFields', 'ensureHemFields', 'ensureDetailsPage'):
            snippets.append(extract(webjs, name))
        page.add_script_tag(content='\n'.join(snippets) + '\nensureHemFields();ensureDetailsPage();')
        self.assertTrue(page.locator('#hem-processing').count())
        self.assertTrue(page.locator('#hem-processing').evaluate('el => el.closest("label").textContent.trim()').startswith('裾上げ（他）'))
        self.assertTrue(page.locator('#processing-days-field').is_hidden())
        page.locator('#hem-processing').check()
        self.assertFalse(page.locator('#processing-days-field').is_hidden())
        self.assertTrue(page.locator('#details').count())
        self.assertIn('顧客コード', page.locator('#details-form').inner_text())
        self.assertIn('拠点の詳細', page.locator('#details h2').inner_text())

        # rubber band: bind function with stubs
        bind = extract(orgjs, 'bindRubberBand')
        page.add_script_tag(content='''
          const $ = s => document.querySelector(s);
          let selectedSites = new Set();
          let band = null, lastSiteClick = null, diagramSelection = null;
          const admin = () => true;
          function tree(){document.querySelectorAll('#org-tree [data-org-kind="site"]').forEach(el=>{el.classList.toggle("multi-selected", selectedSites.has(el.dataset.orgSelect));});}
          function renderDeliveryBulk(){}
          window.__sel = () => [...selectedSites];
        ''' + bind + '\nbindRubberBand();')
        tree = page.locator('#org-tree')
        box = tree.bounding_box()
        # drag a rectangle covering first two cards (left 20-320, top 40-120) from empty area at y=10
        page.mouse.move(box['x'] + 10, box['y'] + 8)
        page.mouse.down()
        page.mouse.move(box['x'] + 340, box['y'] + 150, steps=8)
        page.mouse.up()
        selected = page.evaluate('() => window.__sel()')
        self.assertEqual(set(selected), {'a', 'b'})
        page.close()


if __name__ == '__main__':
    unittest.main()
