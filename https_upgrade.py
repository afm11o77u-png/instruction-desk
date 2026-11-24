"""TLS 待ち受けに来た平文 HTTP を https:// へ案内する。

カメラやQRのために HTTPS は維持したまま、昔の http:// ブックマークと
LAN からの同じポートアクセスを 308 で https://<Host><path> に送る。
最初の1バイトが 0x16 なら TLS ハンドシェイク、それ以外を平文 HTTP とみなす。
"""
import re
import socket
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

HOST_OK = re.compile(r'^[A-Za-z0-9.:\[\]-]+$')
TLS_HANDSHAKE = 0x16
HEADER_LIMIT = 65536


def sanitize_host(host, fallback):
    host = (host or '').strip()
    if not host or not HOST_OK.fullmatch(host):
        return fallback
    return host


def request_target(data, fallback_host):
    text = data.decode('iso-8859-1', 'replace')
    head = text.split('\r\n\r\n', 1)[0]
    lines = head.split('\r\n')
    parts = (lines[0] if lines else '').split(' ')
    raw = parts[1] if len(parts) >= 2 else '/'
    host = ''
    for line in lines[1:]:
        if line.lower().startswith('host:'):
            host = line.split(':', 1)[1].strip()
            break
    if raw.lower().startswith('http://') or raw.lower().startswith('https://'):
        parsed = urlparse(raw)
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        if parsed.netloc:
            host = host or parsed.netloc
    else:
        path = raw if raw.startswith('/') else '/'
    if path.startswith('//') or '\r' in path or '\n' in path or '\x00' in path:
        path = '/'
    return path, sanitize_host(host, fallback_host)


def redirect_response(location):
    return (
        'HTTP/1.1 308 Permanent Redirect\r\n'
        f'Location: {location}\r\n'
        'Connection: close\r\n'
        'Content-Length: 0\r\n'
        '\r\n'
    ).encode('ascii')


def _read_headers(sock):
    data = b''
    while b'\r\n\r\n' not in data and len(data) < HEADER_LIMIT:
        chunk = sock.recv(min(4096, HEADER_LIMIT - len(data)))
        if not chunk:
            break
        data += chunk
    return data


def redirect_if_plain_http(sock, fallback_host, timeout=5):
    """平文 HTTP なら 308 を書いて True。TLS ならバイトを消費せず False。"""
    previous = sock.gettimeout()
    sock.settimeout(timeout)
    try:
        first = sock.recv(1, socket.MSG_PEEK)
    except OSError:
        sock.settimeout(previous)
        raise
    if not first:
        sock.settimeout(previous)
        return False
    if first[0] == TLS_HANDSHAKE:
        sock.settimeout(previous)
        return False
    try:
        data = _read_headers(sock)
        path, host = request_target(data, fallback_host)
        sock.sendall(redirect_response('https://' + host + path))
    finally:
        sock.settimeout(previous)
    return True


class DeskServer(ThreadingHTTPServer):
    """tls がオンのとき、同じポートの http:// を https:// へ案内する。"""

    def __init__(self, address, handler, ssl_context=None, fallback_host=None):
        super().__init__(address, handler)
        self.ssl_context = ssl_context
        self.fallback_host = fallback_host or f'127.0.0.1:{address[1]}'

    def get_request(self):
        sock, addr = self.socket.accept()
        if not self.ssl_context:
            return sock, addr
        try:
            if redirect_if_plain_http(sock, self.fallback_host):
                raise OSError('plain HTTP redirected to HTTPS')
            wrapped = self.ssl_context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
            wrapped.settimeout(None)
            return wrapped, addr
        except Exception:
            try:
                sock.close()
            except OSError:
                pass
            raise
