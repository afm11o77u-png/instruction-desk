"""Exercise the real GET handler for browser scripts without opening the database."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]


def handler():
    tree = ast.parse((ROOT / 'server.py').read_text(encoding='utf-8'))
    method = next(node for cls in tree.body if isinstance(cls, ast.ClassDef)
                  for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'do_GET')
    scope = {'ROOT': ROOT, 'ALLOWED_HOSTS': {'127.0.0.1:8765'}, 're': re}
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'server.py', 'exec'), scope)
    return scope['do_GET']


class TotStaticTest(unittest.TestCase):
    def get(self, path, host='127.0.0.1:8765'):
        request = SimpleNamespace(path=path, headers={'Host': host}, reply=lambda *args: args)
        return handler()(request)

    def test_tot_browser_script_is_served(self):
        status, body, content_type = self.get('/kintai-tot-attendance.js')
        self.assertEqual(status, 200)
        self.assertEqual(body, (ROOT / 'kintai-tot-attendance.js').read_bytes())
        self.assertIn('javascript', content_type)
        self.assertIn(b'totattcsv', body)

    def test_every_page_script_is_served(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        scripts = re.findall(r'<script\b[^>]*\bsrc="(/[^"?]+\.js)"', html)
        self.assertIn('/kintai-tot-attendance.js', scripts)
        for path in scripts:
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 200)

    def test_host_check_still_applies(self):
        self.assertEqual(self.get('/kintai-tot-attendance.js', 'untrusted.invalid')[0], 403)


if __name__ == '__main__':
    unittest.main()
