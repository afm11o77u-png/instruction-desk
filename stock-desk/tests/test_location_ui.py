"""Source/DOM-markup assertions for 保管場所 create vs edit and 停止も表示 toggle."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')


def _fn(name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\{{', APP_JS)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\nfunction ', APP_JS[m.end():])
    end = m.end() + nxt.start() if nxt else len(APP_JS)
    return APP_JS[start:end]


class LocationCreateEditUiTest(unittest.TestCase):
    def test_locations_button_is_new_site_register(self):
        body = _fn('locations')
        self.assertIn("bar('拠点名・棚・区画名','','拠点を新しく登録','locations')", body)
        self.assertNotIn("id=\"add\">新しく登録<", body)

    def test_new_site_form_is_blank_text_input_without_site_select(self):
        body = _fn('locationForm')
        # Create branch: blank site name field, no existing-site select
        self.assertIn("field('siteName','拠点名','',true)", body)
        self.assertNotIn('id="siteId"', body)
        self.assertNotIn('__new__', body)
        # Edit branch still shows registered site name + shelf editor
        self.assertIn("field('siteName','拠点名',siteName,true)", body)
        self.assertIn('id="siteNameDisplay"', body)
        self.assertIn('id="shelfRows"', body)
        self.assertIn('棚を追加', body)

    def test_new_create_payload_has_no_site_id(self):
        body = _fn('readForm')
        create = body[body.index('// 新規は常に create-only'):]
        self.assertIn('siteName:', create)
        self.assertIn('shelves', create)
        self.assertNotIn('siteId:', create)
        self.assertNotIn('id:', create.split('return')[1].split('}')[0] if 'return' in create else create)

    def test_location_site_context_blank_shelves_for_create(self):
        body = _fn('locationSiteContext')
        self.assertIn('if(!r||!r.id)return {site:null,shelves:[]}', body)

    def test_open_existing_loads_site_and_shelves(self):
        form = _fn('locationForm')
        ctx = _fn('locationSiteContext')
        self.assertIn('if(r.id)', form)
        self.assertIn('filter(x=>x.parentId===r.id)', ctx)
        self.assertIn('filter(x=>x.parentId===r.parentId)', ctx)


class InactiveToggleUiTest(unittest.TestCase):
    def test_shared_inactive_toggle_markup(self):
        body = _fn('inactiveToggle')
        self.assertIn('class="check inactive-toggle"', body)
        self.assertIn('id="stopped"', body)
        self.assertIn('停止も表示', body)
        self.assertIn('<label', body)
        bar = _fn('bar')
        self.assertIn('${inactiveToggle()}', bar)
        # Used by all four master list screens via bar()
        for name in ('customers', 'suppliers', 'products', 'locations'):
            self.assertIn('bar(', _fn(name))

    def test_inactive_toggle_css_tight_nowrap(self):
        self.assertRegex(CSS, r'\.check,\s*\.inactive-toggle\{[^}]*display:\s*inline-flex')
        self.assertRegex(CSS, r'\.check,\s*\.inactive-toggle\{[^}]*align-items:\s*center')
        self.assertRegex(CSS, r'\.check,\s*\.inactive-toggle\{[^}]*gap:\s*6px')
        self.assertRegex(CSS, r'\.check,\s*\.inactive-toggle\{[^}]*white-space:\s*nowrap')
        self.assertRegex(CSS, r'\.check,\s*\.inactive-toggle\{[^}]*flex-shrink:\s*0')
        self.assertRegex(CSS, r'\.check,\s*\.inactive-toggle\{[^}]*line-height:\s*1')
        self.assertRegex(CSS, r'\.check input,\s*\.inactive-toggle input\{[^}]*padding:\s*0')
        self.assertRegex(CSS, r'\.bar \.check,\s*\.bar \.inactive-toggle\{[^}]*align-self:\s*flex-end')
        self.assertRegex(CSS, r'\.bar \.check input,\s*\.bar \.inactive-toggle input\{[^}]*min-width:\s*0')
        self.assertNotRegex(CSS, r'\.bar \.check,\s*\.bar \.inactive-toggle\{[^}]*padding-bottom')
        # Filter behavior unchanged: still driven by #stopped + state.stopped
        bind = _fn('bindList')
        self.assertIn("$('#stopped').onchange=e=>{state.stopped=e.target.checked;paint()}", bind)


if __name__ == '__main__':
    unittest.main()
