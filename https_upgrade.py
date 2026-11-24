"""TLS 待ち受けに来た平文 HTTP を https:// へ案内する。

カメラやQRのために HTTPS は維持したまま、昔の http:// ブックマークと
LAN からの同じポートアクセスを 308 で https://<Host><path> に送る。

最初の1バイトを通常の recv で読む（MSG_PEEK は使わない）。
0x16 ならそのバイトを MemoryBIO 経由で TLS 握手に渡す。
Windows では MSG_PEEK のあと OpenSSL が fd から読むと、Chrome の
ClientHello で握手が壊れて空応答になることがある。
"""
import io
import re
import socket
import ssl
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


def _read_headers(sock, initial=b''):
    data = initial
    while b'\r\n\r\n' not in data and len(data) < HEADER_LIMIT:
        chunk = sock.recv(min(4096, HEADER_LIMIT - len(data)))
        if not chunk:
            break
        data += chunk
    return data


def send_http_redirect(sock, fallback_host, initial=b''):
    data = _read_headers(sock, initial)
    path, host = request_target(data, fallback_host)
    sock.sendall(redirect_response('https://' + host + path))


class BioSSLSocket:
    """先頭バイトを消費済みの TCP ソケットを、fd 直読みせず TLS 化する。"""

    def __init__(self, sock, context, initial=b''):
        self._sock = sock
        self._incoming = ssl.MemoryBIO()
        self._outgoing = ssl.MemoryBIO()
        self._ssl = context.wrap_bio(self._incoming, self._outgoing, server_side=True)
        if initial:
            self._incoming.write(initial)
        self._handshaken = False
        self._closed = False
        self._io_refs = 0
        self.timeout = sock.gettimeout()

    def _flush(self):
        pending = self._outgoing.pending
        if pending:
            data = self._outgoing.read(pending)
            if data:
                self._sock.sendall(data)

    def _fill(self):
        data = self._sock.recv(65536)
        if not data:
            return False
        self._incoming.write(data)
        return True

    def do_handshake(self):
        if self._handshaken:
            return
        while True:
            try:
                self._ssl.do_handshake()
                self._flush()
                self._handshaken = True
                return
            except ssl.SSLWantReadError:
                self._flush()
                if not self._fill():
                    raise ConnectionResetError('TLS handshake closed by client')
            except ssl.SSLWantWriteError:
                self._flush()

    def recv(self, buflen, flags=0):
        if flags:
            raise OSError('flags are not supported on the TLS wrapper')
        if buflen <= 0:
            return b''
        if not self._handshaken:
            try:
                self.do_handshake()
            except (ConnectionResetError, OSError):
                return b''
        while True:
            try:
                data = self._ssl.read(buflen)
                self._flush()
                return data or b''
            except ssl.SSLWantReadError:
                self._flush()
                if not self._fill():
                    return b''
            except ssl.SSLWantWriteError:
                self._flush()
            except (ssl.SSLZeroReturnError, ssl.SSLEOFError, ConnectionResetError, OSError):
                return b''

    def recv_into(self, buffer, nbytes=0, flags=0):
        if nbytes <= 0:
            nbytes = len(buffer)
        data = self.recv(nbytes, flags)
        buffer[:len(data)] = data
        return len(data)

    def send(self, data, flags=0):
        if flags:
            raise OSError('flags are not supported on the TLS wrapper')
        if not self._handshaken:
            self.do_handshake()
        payload = bytes(data)
        if not payload:
            return 0
        while True:
            try:
                written = self._ssl.write(payload)
                self._flush()
                return written
            except ssl.SSLWantReadError:
                self._flush()
                if not self._fill():
                    raise ConnectionResetError('TLS closed while sending')
            except ssl.SSLWantWriteError:
                self._flush()

    def sendall(self, data, flags=0):
        view = memoryview(data)
        while view:
            written = self.send(view, flags)
            view = view[written:]

    def makefile(self, mode='r', buffering=None, *, encoding=None, errors=None, newline=None):
        if buffering is None:
            buffering = -1
        raw = socket.SocketIO(self, mode)
        self._io_refs += 1
        if buffering == 0:
            return raw
        if buffering < 0:
            buffering = io.DEFAULT_BUFFER_SIZE
        if 'r' in mode and 'w' not in mode:
            buffer = io.BufferedReader(raw, buffering)
        elif 'w' in mode and 'r' not in mode:
            buffer = io.BufferedWriter(raw, buffering)
        else:
            buffer = io.BufferedRWPair(raw, raw, buffering)
        if 'b' in mode:
            return buffer
        return io.TextIOWrapper(buffer, encoding=encoding, errors=errors, newline=newline)

    def settimeout(self, timeout):
        self._sock.settimeout(timeout)
        self.timeout = timeout

    def gettimeout(self):
        return self._sock.gettimeout()

    def setblocking(self, flag):
        self._sock.setblocking(flag)
        self.timeout = self._sock.gettimeout()

    def fileno(self):
        return self._sock.fileno()

    def getpeername(self):
        return self._sock.getpeername()

    def getsockname(self):
        return self._sock.getsockname()

    def setsockopt(self, *args):
        return self._sock.setsockopt(*args)

    def getsockopt(self, *args):
        return self._sock.getsockopt(*args)

    def shutdown(self, how):
        try:
            self._sock.shutdown(how)
        except OSError:
            pass

    def _decref_socketios(self):
        if self._io_refs > 0:
            self._io_refs -= 1
        if self._closed:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._io_refs <= 0:
            try:
                self._sock.close()
            except OSError:
                pass


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
        previous = sock.gettimeout()
        sock.settimeout(5)
        try:
            first = sock.recv(1)
            if not first:
                raise OSError('client closed before sending data')
            if first[0] != TLS_HANDSHAKE:
                send_http_redirect(sock, self.fallback_host, first)
                raise OSError('plain HTTP redirected to HTTPS')
            wrapped = BioSSLSocket(sock, self.ssl_context, first)
            wrapped.settimeout(None)
            return wrapped, addr
        except Exception:
            try:
                sock.settimeout(previous)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
            raise
