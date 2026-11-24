"""TOP を隠し、/ 自身が指示書デスクを出す切替の確認（サーバー転送は使わない）。"""
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault('winreg', types.ModuleType('winreg'))

import workspace_config  # noqa: E402
import server  # noqa: E402


class WorkspaceConfigTest(unittest.TestCase):
    def test_flag_hides_top_by_default(self):
        self.assertIs(workspace_config.SHOW_TOP_MENU, False)
        self.assertIn('window.SHOW_TOP_MENU=false', workspace_config.browser_script())

    def test_restoring_top_is_a_single_switch(self):
        with mock.patch.object(workspace_config, 'SHOW_TOP_MENU', True):
            self.assertIn('window.SHOW_TOP_MENU=true', workspace_config.browser_script())


class HideTopMenuSourceTest(unittest.TestCase):
    def test_index_loads_config_before_entry_script(self):
        src = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<script src="/workspace-config.js"></script>', src)
        self.assertIn('window.SHOW_TOP_MENU!==false', src)
        cfg = src.find('<script src="/workspace-config.js"></script>')
        entry = src.find("dataset.workspaceEntry='home'")
        self.assertGreater(entry, cfg)
        self.assertLess(cfg, src.find('</head>'))

    def test_switcher_hides_top_link_and_keeps_workspaces(self):
        src = (ROOT / 'workspace-switcher.js').read_text(encoding='utf-8')
        self.assertIn("topLink.hidden=data.show_top_menu===false", src)
        self.assertIn("['desk','指示書デスク'", src)
        self.assertIn("['web','WEB依頼'", src)
        self.assertIn('a:not([hidden])', src)
        self.assertIn('TOPに戻る', src)

    def test_account_opens_desk_on_root_without_navigation(self):
        src = (ROOT / 'account.js').read_text(encoding='utf-8')
        self.assertIn('showTopMenu=m.show_top_menu!==false', src)
        self.assertIn('if(home&&showTopMenu)', src)
        self.assertIn('if(home&&!showTopMenu)', src)
        self.assertIn("await showCatalog('desk')", src)
        self.assertIn('function firstWorkspacePath', src)
        self.assertNotIn("location.replace('/?module=desk')", src)

    def test_server_does_not_redirect_top(self):
        src = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertNotIn('should_redirect_top', src)
        self.assertNotIn("Location': '/?module=desk'", src)
        self.assertNotIn('redirect_hidden_top', src)


class HideTopMenuHTTPTest(unittest.TestCase):
    def setUp(self):
        import auth
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'desk.sqlite3'
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            c.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, x TEXT)')
            auth.schema(c)
            c.commit()
        class Handler(server.Handler):
            def session_user(self, c):
                return None
            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.host = '127.0.0.1:' + str(self.http.server_address[1])
        self.port = self.http.server_address[1]
        self.patches = [
            mock.patch.object(server, 'ALLOWED_HOSTS', {self.host}),
            mock.patch.object(server, 'DB', self.db),
        ]
        for item in self.patches:
            item.start()
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def request(self, path):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request('GET', path, headers={'Host': self.host})
            response = conn.getresponse()
            body = response.read()
            return response.status, response.getheader('Location'), body
        finally:
            conn.close()

    def test_root_and_home_serve_desk_html_without_redirect(self):
        desk_status, desk_location, desk_body = self.request('/?module=desk')
        self.assertEqual(desk_status, 200)
        self.assertIsNone(desk_location)
        self.assertIn(b'workspace-home', desk_body)
        for path in ('/', '/?module=home'):
            status, location, body = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertIsNone(location, path)
            self.assertEqual(body, desk_body, path)

    def test_workspace_config_js_follows_the_flag(self):
        status, location, body = self.request('/workspace-config.js')
        self.assertEqual(status, 200)
        self.assertIsNone(location)
        self.assertEqual(body, b'window.SHOW_TOP_MENU=false;\n')
        with mock.patch.object(workspace_config, 'SHOW_TOP_MENU', True):
            status, location, body = self.request('/workspace-config.js')
            self.assertEqual(status, 200)
            self.assertEqual(body, b'window.SHOW_TOP_MENU=true;\n')

    def test_api_me_reports_the_flag(self):
        status, _, body = self.request('/api/me')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIs(data['show_top_menu'], False)
        with mock.patch.object(workspace_config, 'SHOW_TOP_MENU', True):
            status, _, body = self.request('/api/me')
            self.assertIs(json.loads(body)['show_top_menu'], True)

    def test_flag_on_still_serves_root_without_redirect(self):
        with mock.patch.object(workspace_config, 'SHOW_TOP_MENU', True):
            status, location, body = self.request('/')
            self.assertEqual(status, 200)
            self.assertIsNone(location)
            self.assertIn('使う業務を選んでください'.encode(), body)


if __name__ == '__main__':
    unittest.main()
