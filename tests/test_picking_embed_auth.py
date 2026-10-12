"""Regression: picking embed must not dump the login screen into the iframe."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'app.js').read_text(encoding='utf-8')
PICKING = (ROOT / 'picking.js').read_text(encoding='utf-8')


class PickingEmbedAuthTest(unittest.TestCase):
    def test_embed_auth_failure_notifies_parent_instead_of_replacing_root(self):
        self.assertIn('function denyPickingAccess(', PICKING)
        self.assertIn("type:'picking-auth'", PICKING)
        self.assertIn("pageQuery.get('embed')==='1'", PICKING)
        # Top-level still redirects home; embed/popup must not.
        deny = re.search(r'function denyPickingAccess\([^)]*\)\{.*?\n(?=run\(async)', PICKING, re.S)
        self.assertIsNotNone(deny)
        body = deny.group(0)
        self.assertIn('parent.postMessage', body)
        self.assertIn("location.replace('/')", body)
        # The unconditional replace that used to run for embed is gone from boot.
        boot = PICKING[PICKING.index('run(async()=>{const me=await api(\'/api/me\')'):]
        boot_head = boot[:boot.index('if(role===\'owner\')')]
        self.assertIn('denyPickingAccess(true)', boot_head)
        self.assertNotIn("location.replace('/')", boot_head)

    def test_parent_handles_picking_auth_and_reloads_drifted_iframe(self):
        self.assertIn("e.data.type==='picking-auth'", APP)
        self.assertIn("authScreen('login')", APP)
        self.assertIn('drifted', APP)
        self.assertIn("path!=='/picking'", APP)
        self.assertIn('enteringPicking', APP)
        self.assertIn('listChrome(enteringPicking)', APP)
        chrome = APP[APP.index('function listChrome()'):APP.index("document.querySelectorAll('[data-view]')")]
        self.assertIn('arguments[0]||frame.getAttribute(\'src\')!==want||drifted', chrome)
        account = (ROOT / 'account.js').read_text(encoding='utf-8')
        self.assertIn("pickingFrame.src='about:blank'", account)


if __name__ == '__main__':
    unittest.main()
