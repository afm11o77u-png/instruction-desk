"""同じポートの http:// を https:// へ案内する確認。"""
import http.client
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import https_upgrade


class NoPeekTest(unittest.TestCase):
    def test_accept_path_does_not_use_msg_peek(self):
        src = Path(__file__).resolve().parents[1].joinpath('https_upgrade.py').read_text(encoding='utf-8')
        self.assertNotIn('MSG_PEEK', src.split('"""', 2)[-1])
        self.assertIn('wrap_bio', src)
        self.assertIn('class BioSSLSocket', src)


class RequestTargetTest(unittest.TestCase):
    def test_path_query_and_host(self):
        raw = b'GET /?module=desk HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n'
        path, host = https_upgrade.request_target(raw, '127.0.0.1:8765')
        self.assertEqual(path, '/?module=desk')
        self.assertEqual(host, '127.0.0.1:8765')

    def test_lan_host_is_kept(self):
        raw = b'GET /picking HTTP/1.1\r\nHost: 192.168.1.10:8765\r\n\r\n'
        path, host = https_upgrade.request_target(raw, '127.0.0.1:8765')
        self.assertEqual(path, '/picking')
        self.assertEqual(host, '192.168.1.10:8765')

    def test_rejects_injected_host_and_protocol_relative_path(self):
        path, _ = https_upgrade.request_target(
            b'GET //evil.example/ HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n',
            '127.0.0.1:8765',
        )
        self.assertEqual(path, '/')
        _, host = https_upgrade.request_target(
            b'GET / HTTP/1.1\r\nHost: evil.example\r/nLocation: http://x\r\n\r\n',
            '127.0.0.1:8765',
        )
        self.assertEqual(host, '127.0.0.1:8765')

    def test_absolute_form_and_fallback_host(self):
        raw = b'GET http://shop:8765/a?b=1 HTTP/1.1\r\n\r\n'
        path, host = https_upgrade.request_target(raw, '127.0.0.1:8765')
        self.assertEqual(path, '/a?b=1')
        self.assertEqual(host, 'shop:8765')
        path, host = https_upgrade.request_target(b'GET / HTTP/1.0\r\n\r\n', '127.0.0.1:8765')
        self.assertEqual(path, '/')
        self.assertEqual(host, '127.0.0.1:8765')

    def test_redirect_bytes(self):
        body = https_upgrade.redirect_response('https://127.0.0.1:8765/?view=all')
        self.assertTrue(body.startswith(b'HTTP/1.1 308 Permanent Redirect\r\n'))
        self.assertIn(b'Location: https://127.0.0.1:8765/?view=all\r\n', body)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b'ok:' + self.path.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def _certs(folder):
    cert = folder / 'server.crt'
    key = folder / 'server.key'
    subprocess.run(
        ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-days', '1',
         '-nodes', '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost'],
        check=True, capture_output=True,
    )
    return cert, key


class LiveUpgradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cert, key = _certs(Path(self.tmp.name))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
        self.server = https_upgrade.DeskServer(('127.0.0.1', 0), _Handler, ssl_context=context)
        self.port = self.server.server_address[1]
        self.server.fallback_host = f'127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.tls = ssl._create_unverified_context()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def request(self, path, https, host=None):
        if https:
            conn = http.client.HTTPSConnection('127.0.0.1', self.port, context=self.tls, timeout=5)
        else:
            conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            headers = {'Host': host} if host else {}
            conn.request('GET', path, headers=headers)
            response = conn.getresponse()
            return response.status, response.getheader('Location'), response.read()
        finally:
            conn.close()

    def test_http_redirects_to_https_keeping_path_and_query(self):
        status, location, body = self.request('/?view=all', https=False)
        self.assertEqual(status, 308)
        self.assertEqual(location, f'https://127.0.0.1:{self.port}/?view=all')
        self.assertEqual(body, b'')

    def test_http_uses_request_host_for_lan(self):
        status, location, _ = self.request('/picking?embed=1', https=False, host=f'192.168.0.8:{self.port}')
        self.assertEqual(status, 308)
        self.assertEqual(location, f'https://192.168.0.8:{self.port}/picking?embed=1')

    def test_https_still_serves(self):
        status, location, body = self.request('/?module=desk', https=True)
        self.assertEqual(status, 200)
        self.assertIsNone(location)
        self.assertEqual(body, b'ok:/?module=desk')


if __name__ == '__main__':
    unittest.main()
