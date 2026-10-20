#!/usr/bin/env python3
"""Verify product-ledger CSV large-body fix on copy DBs (no commit of real data).

Covers:
  A) stock-desk standalone HTTP
  B) instruction-desk /inventory/ proxy HTTP
  - history + preview for ~1.78MB 商品台帳.csv
  - oversize Content-Length → 「ファイル容量が上限を超えています」
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESK_ROOT = ROOT.parent
SAMPLE = Path(
    '/cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/media/商品台帳.csv'
)
OVERSIZE = 'ファイル容量が上限を超えています'


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.15)
    raise RuntimeError(f'server not ready: {url} ({last})')


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip('/')
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(self, method: str, path: str, body=None, headers=None, origin=None):
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            hdrs.setdefault('Content-Type', 'application/json')
        if origin:
            hdrs['Origin'] = origin
        req = urllib.request.Request(
            self.base + path, data=data, headers=hdrs, method=method
        )
        try:
            with self.opener.open(req, timeout=300) as resp:
                text = resp.read().decode('utf-8')
                return resp.status, json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode('utf-8')
            try:
                parsed = json.loads(text) if text else {}
            except json.JSONDecodeError:
                parsed = {'_raw': text}
            return e.code, parsed

    def cookie_header(self) -> str:
        parts = []
        for c in self.jar:
            parts.append(f'{c.name}={c.value}')
        return '; '.join(parts)


def post_claimed_length(host: str, port: int, path: str, headers: dict, claim: int):
    """POST with Content-Length larger than body (server rejects before read)."""
    conn = http.client.HTTPConnection(host, port, timeout=10)
    hdrs = dict(headers)
    hdrs['Content-Type'] = 'application/json'
    hdrs['Content-Length'] = str(claim)
    conn.request('POST', path, body=b'{}', headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read().decode('utf-8')
    conn.close()
    return resp.status, json.loads(raw) if raw else {}


def load_csv_payload():
    if not SAMPLE.is_file():
        raise SystemExit(f'missing sample CSV: {SAMPLE}')
    blob = SAMPLE.read_bytes()
    assert len(blob) > 1_500_000, len(blob)
    b64 = base64.b64encode(blob).decode('ascii')
    body = {'name': '商品台帳.csv', 'file': b64}
    encoded_len = len(json.dumps(body, ensure_ascii=False).encode('utf-8'))
    assert encoded_len > 1_000_000, encoded_len
    assert encoded_len < 12_000_000, encoded_len
    return blob, body, encoded_len


def verify_standalone(tmpdir: Path, body: dict, encoded_len: int) -> None:
    sys.path.insert(0, str(ROOT))
    import server as stock_server

    port = free_port()
    db = tmpdir / 'stock-standalone' / 'stock.sqlite3'
    db.parent.mkdir(parents=True)
    stock_server.prepare(db, 'live')
    httpd = stock_server.ThreadingHTTPServer(
        ('127.0.0.1', port), stock_server.make_handler(db, 'live')
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        wait_http(f'http://127.0.0.1:{port}/')
        c = Client(f'http://127.0.0.1:{port}')
        origin = f'http://127.0.0.1:{port}'

        st, err = post_claimed_length(
            '127.0.0.1',
            port,
            '/api/product-ledger-csv/history',
            {'X-Stock-Request': '1', 'Origin': origin},
            12_000_001,
        )
        assert st == 400 and err.get('error') == OVERSIZE, (st, err)
        print('standalone: oversize message OK')

        st, hist = c.request(
            'POST',
            '/api/product-ledger-csv/history',
            body,
            headers={'X-Stock-Request': '1'},
            origin=origin,
        )
        assert st == 200 and 'previousImports' in hist, (st, hist)
        print(
            f'standalone history OK prev={len(hist.get("previousImports") or [])} '
            f'json_body={encoded_len}'
        )

        st, prev = c.request(
            'POST',
            '/api/product-ledger-csv/preview',
            {**body, '_canWrite': True, '_actor': {'name': 'verify'}},
            headers={'X-Stock-Request': '1'},
            origin=origin,
        )
        assert st == 200 and prev.get('rowCount', 0) > 1000, (st, prev)
        print(f"standalone preview OK rowCount={prev.get('rowCount')} (no commit)")
    finally:
        httpd.shutdown()


def _linux_winreg_stub_boot() -> str:
    """Root server imports winreg via picking.py; stub it on non-Windows CI."""
    return r'''
import sys, types
if sys.platform != "win32" and "winreg" not in sys.modules:
    m = types.ModuleType("winreg")
    m.HKEY_CURRENT_USER = 1
    m.HKEY_LOCAL_MACHINE = 2
    class _Err(OSError):
        pass
    m.OpenKey = lambda *a, **k: (_ for _ in ()).throw(_Err("stub"))
    m.QueryValue = lambda *a, **k: ""
    sys.modules["winreg"] = m
from http.server import ThreadingHTTPServer
import server
server.initialize()
httpd = ThreadingHTTPServer((server.BIND_HOST, server.PORT), server.Handler)
httpd.serve_forever()
'''


def verify_proxy(tmpdir: Path, body: dict, encoded_len: int) -> None:
    port = free_port()
    data = tmpdir / 'desk-data'
    data.mkdir()
    env = os.environ.copy()
    env['INSTRUCTION_DATA'] = str(data)
    env['INSTRUCTION_PORT'] = str(port)
    proc = subprocess.Popen(
        [sys.executable, '-X', 'utf8', '-c', _linux_winreg_stub_boot()],
        cwd=str(DESK_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_http(f'http://127.0.0.1:{port}/api/me')
        c = Client(f'http://127.0.0.1:{port}')
        origin = f'http://127.0.0.1:{port}'
        st, setup = c.request(
            'POST',
            '/api/setup',
            {
                'login': 'verify@example.com',
                'name': '検証',
                'password': 'Passw0rd!Verify',
                'gate': 'Gatew0rd!Verify',
                'gateConfirm': 'Gatew0rd!Verify',
            },
            headers={'X-Desk-Request': '1'},
            origin=origin,
        )
        assert st == 200, (st, setup)

        st, err = post_claimed_length(
            '127.0.0.1',
            port,
            '/inventory/api/product-ledger-csv/history',
            {
                'X-Stock-Request': '1',
                'Origin': origin,
                'Cookie': c.cookie_header(),
                'Host': f'127.0.0.1:{port}',
            },
            12_000_001,
        )
        assert st == 400 and err.get('error') == OVERSIZE, (st, err)
        print('proxy: oversize message OK')

        st, hist = c.request(
            'POST',
            '/inventory/api/product-ledger-csv/history',
            body,
            headers={'X-Stock-Request': '1'},
            origin=origin,
        )
        assert st == 200 and 'previousImports' in hist, (st, hist)
        print(
            f'proxy history OK prev={len(hist.get("previousImports") or [])} '
            f'json_body={encoded_len}'
        )

        st, prev = c.request(
            'POST',
            '/inventory/api/product-ledger-csv/preview',
            {**body, '_canWrite': True, '_actor': {'name': 'verify'}},
            headers={'X-Stock-Request': '1'},
            origin=origin,
        )
        assert st == 200 and prev.get('rowCount', 0) > 1000, (st, prev)
        print(f"proxy preview OK rowCount={prev.get('rowCount')} (no commit)")
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)
        if proc.returncode not in (0, -15, None) and out:
            print(out[-2000:], file=sys.stderr)


def main() -> int:
    blob, body, encoded_len = load_csv_payload()
    print(f'CSV bytes={len(blob)} json_body={encoded_len}')
    tmp = Path(tempfile.mkdtemp(prefix='pl-size-verify-'))
    try:
        verify_standalone(tmp, body, encoded_len)
        verify_proxy(tmp, body, encoded_len)
        print('ALL OK (copy DB only; no product-ledger commit)')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
