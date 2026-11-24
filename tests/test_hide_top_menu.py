"""TOP 画面を隠して指示書デスクへ案内する切替の確認。"""
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
        self.assertEqual(workspace_config.entry_path(), '/?module=desk')

    def test_only_top_urls_are_redirected(self):
        self.assertTrue(workspace_config.is_top_menu_request('/'))
        self.assertTrue(workspace_config.is_top_menu_request('/?module=home'))
        self.assertFalse(workspace_config.is_top_menu_request('/?module=desk'))
        self.assertFalse(workspace_config.is_top_menu_request('/?view=all'))
        self.assertFalse(workspace_config.is_top_menu_request('/rental'))
        self.assertTrue(workspace_config.should_redirect_top('/'))
        self.assertFalse(workspace_config.should_redirect_top('/?module=desk'))

    def test_desk_is_the_usual_fallback_and_top_is_last_resort(self):
        floor = {'role': 'floor', 'manage': 0}
        self.assertTrue(workspace_config.workspace_allowed(floor)['desk'])
        self.assertEqual(workspace_config.entry_path_for(floor), '/?module=desk')
        with mock.patch.object(workspace_config, 'workspace_allowed', return_value={}):
            self.assertEqual(workspace_config.entry_path_for({'role': 'floor'}), '/')

    def test_restoring_top_is_a_single_switch(self):
        with mock.patch.object(workspace_config, 'SHOW_TOP_MENU', True):
            self.assertEqual(workspace_config.entry_path(), '/')
            self.assertEqual(workspace_config.entry_path_for({'role': 'owner'}), '/')
            self.assertFalse(workspace_config.should_redirect_top('/'))


class HideTopMenuSourceTest(unittest.TestCase):
    def test_switcher_hides_top_link_and_keeps_workspaces(self):
        src = (ROOT / 'workspace-switcher.js').read_text(encoding='utf-8')
        self.assertIn("topLink.hidden=data.show_top_menu===false", src)
        self.assertIn("['desk','指示書デスク'", src)
        self.assertIn("['web','WEB依頼'", src)
        self.assertIn('a:not([hidden])', src)

    def test_account_opens_desk_when_top_is_hidden(self):
        src = (ROOT / 'account.js').read_text(encoding='utf-8')
        self.assertIn('showTopMenu=m.show_top_menu!==false', src)
        self.assertIn('if(home&&showTopMenu)', src)
        self.assertIn("await showCatalog('desk')", src)
        self.assertIn('function firstWorkspacePath', src)


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

    def test_root_and_home_redirect_to_desk(self):
        for path in ('/', '/?module=home'):
            status, location, _ = self.request(path)
            self.assertEqual(status, 302, path)
            self.assertEqual(location, '/?module=desk', path)

    def test_desk_and_view_queries_are_not_redirected(self):
        for path in ('/?module=desk', '/?view=all'):
            status, location, body = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertIsNone(location, path)
            self.assertIn(b'workspace-home', body)

    def test_api_me_reports_the_flag(self):
        status, _, body = self.request('/api/me')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIs(data['show_top_menu'], False)

    def test_flag_on_serves_top_without_redirect(self):
        with mock.patch.object(workspace_config, 'SHOW_TOP_MENU', True):
            status, location, body = self.request('/')
            self.assertEqual(status, 200)
            self.assertIsNone(location)
            self.assertIn('使う業務を選んでください'.encode(), body)


if __name__ == '__main__':
    unittest.main()
