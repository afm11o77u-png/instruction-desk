"""Headless Chromium + TLS で、TOP 非表示時に / が指示書デスクになることを確認する。"""
import os
import ssl
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault('winreg', types.ModuleType('winreg'))

LOGIN = 'sales@example.co.jp'
PASSWORD = 'synthetic-pass-12'


def playwright_available():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def make_certs(folder):
    cert = folder / 'server.crt'
    key = folder / 'server.key'
    subprocess.check_call([
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256',
        '-keyout', str(key), '-out', str(cert), '-days', '1', '-nodes',
        '-subj', '/CN=127.0.0.1',
        '-addext', 'subjectAltName=IP:127.0.0.1',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cert, key


def wrap_tls(httpd, cert, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    def handle_error(request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, ssl.SSLError):
            return
        ThreadingHTTPServer.handle_error(httpd, request, client_address)
    httpd.handle_error = handle_error
    return httpd


def start_tls_app(db, cert, key):
    import server
    class Quiet(server.Handler):
        def log_message(self, *args):
            pass
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
    host = '127.0.0.1:' + str(httpd.server_address[1])
    wrap_tls(httpd, cert, key)
    url = f'https://127.0.0.1:{httpd.server_address[1]}'
    patches = [
        mock.patch.object(server, 'ALLOWED_HOSTS', {host, '127.0.0.1'}),
        mock.patch.object(server, 'ALLOWED_ORIGINS', {url, 'https://127.0.0.1'}),
        mock.patch.object(server, 'DB', db),
    ]
    for item in patches:
        item.start()
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, patches, url


def stop_server(httpd, thread, patches):
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)
    for item in reversed(patches):
        item.stop()


def prepare_db(path):
    import auth
    import display_modes
    import picking
    import server
    with mock.patch.object(server, 'DB', path):
        with server.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, payload TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
            auth.schema(c)
            display_modes.initialize(c)
            picking.initialize(c)
            c.execute(
                'INSERT INTO users(login,name,password,role,manage,overview,active,must_change) VALUES(?,?,?,?,0,0,1,0)',
                (LOGIN, '検証用営業', auth.password_hash(PASSWORD), 'sales'),
            )
            c.commit()


@unittest.skipUnless(playwright_available(), 'playwright が必要です')
class HideTopMenuBrowserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.db = folder / 'desk.sqlite3'
        prepare_db(self.db)
        self.cert, self.key = make_certs(folder)
        self.httpd, self.thread, self.patches, self.url = start_tls_app(self.db, self.cert, self.key)

    def tearDown(self):
        stop_server(self.httpd, self.thread, self.patches)
        self.tmp.cleanup()

    def test_login_and_root_open_desk_and_switcher_hides_top(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.goto(self.url + '/', wait_until='domcontentloaded', timeout=30000)
            expect(page.locator('#authform')).to_be_visible(timeout=15000)
            expect(page.locator('#workspace-home')).to_be_hidden()
            self.assertNotIn('使う業務を選んでください', page.locator('h1:visible').all_text_contents())
            page.fill('input[name="login"]', LOGIN)
            page.fill('input[name="password"]', PASSWORD)
            page.click('#authform button[type="submit"]')
            expect(page.locator('body.authenticated')).to_be_visible(timeout=20000)
            expect(page.locator('#workspace-home')).to_be_hidden()
            expect(page.locator('#brandtitle')).to_have_text('指示書デスク')
            expect(page.locator('#authscreen')).to_be_hidden()
            expect(page.locator('#desknav .nav[data-view="all"]')).to_be_visible()
            self.assertEqual(page.evaluate('document.documentElement.dataset.workspaceEntry || ""'), '')
            self.assertEqual(page.url.rstrip('/').split('?')[0], self.url)

            page.goto(self.url + '/', wait_until='domcontentloaded', timeout=30000)
            expect(page.locator('body.authenticated')).to_be_visible(timeout=20000)
            expect(page.locator('#workspace-home')).to_be_hidden()
            expect(page.locator('#brandtitle')).to_have_text('指示書デスク')
            expect(page.locator('#desknav .nav[data-view="all"]')).to_be_visible()

            page.locator('[data-workspace-switch]').first.click()
            panel = page.locator('#workspace-switcher')
            expect(panel).to_be_visible(timeout=5000)
            expect(panel.locator('a', has_text='指示書デスク')).to_be_visible()
            expect(panel.locator('a', has_text='顧客・商品情報')).to_be_visible()
            top = panel.locator('.ws-top')
            self.assertTrue(top.count() == 0 or not top.is_visible())
            self.assertFalse(panel.locator('a:visible', has_text='TOPに戻る').count())
            browser.close()


@unittest.skipUnless(playwright_available(), 'playwright が必要です')
class Pr481RedirectReproTest(unittest.TestCase):
    """PR481 と同じ HTTP/1.0 302 を TLS 上で再現し、Linux Chromium で空応答になるか見る。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cert, self.key = make_certs(Path(self.tmp.name))
        html = '<!doctype html><title>desk</title><h1>指示書デスク</h1>'.encode()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.split('?')[0]
                query = self.path.split('?', 1)[1] if '?' in self.path else ''
                if path == '/' and 'module=desk' not in query:
                    self.send_response(302)
                    self.send_header('Location', '/?module=desk')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                self.wfile.write(html)

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        wrap_tls(self.httpd, self.cert, self.key)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'https://127.0.0.1:{self.httpd.server_address[1]}/'

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def test_http10_302_over_tls(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(ignore_https_errors=True).new_page()
            errors = []
            page.on('requestfailed', lambda req: errors.append(f'{req.url} {req.failure}'))
            try:
                page.goto(self.url, wait_until='domcontentloaded', timeout=15000)
                body = page.inner_text('body')
                title = page.title()
                result = f'ok title={title} body={body} final={page.url}'
            except Exception as exc:
                result = f'failed {type(exc).__name__}: {exc}; requestfailed={errors}'
            Path('/tmp/pr481-302-repro.txt').write_text(result, encoding='utf-8')
            print('PR481_REPRO', result, flush=True)
            browser.close()
            # 再現できなくても本テストは記録だけする（Linux では通る可能性が高い）
            if result.startswith('ok'):
                self.assertIn('指示書デスク', result)
            else:
                self.assertIn('ERR_EMPTY_RESPONSE', result + ''.join(errors) + result)


if __name__ == '__main__':
    unittest.main()
