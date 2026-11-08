"""在庫QRの画像。標準ライブラリだけでPNGを作る。

外部のQRサービスへは送らない。Pillow や qrcode が無くても在庫アプリは起動する。
中に入れる文字列はIDだけ。版は3、誤り訂正はM。このIDの長さ（37文字）に足りる。
"""
from __future__ import annotations

import struct
import zlib

# 版3・誤り訂正M。データ44符号、誤り訂正26符号、ブロック1つ。
_VERSION = 3
_SIZE = 17 + _VERSION * 4  # 29
_DATA_CODEWORDS = 44
_ECC_CODEWORDS = 26
_QUIET = 4
_SCALE = 8

_EXP = [0] * 512
_LOG = [0] * 256


def _init_gf():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_gf()


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_ecc(data, nsym):
    gen = [1]
    for i in range(nsym):
        nxt = [0] * (len(gen) + 1)
        for j, coef in enumerate(gen):
            nxt[j] ^= coef
            nxt[j + 1] ^= _mul(coef, _EXP[i])
        gen = nxt
    res = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = res[i]
        if coef == 0:
            continue
        for j, g in enumerate(gen):
            res[i + j] ^= _mul(g, coef)
    return res[-nsym:]


def _bch_type_info(data):
    g15 = 0b10100110111
    d = data << 10
    while d.bit_length() - g15.bit_length() >= 0:
        d ^= g15 << (d.bit_length() - g15.bit_length())
    return ((data << 10) | d) ^ 0b101010000010010


def _byte_codewords(payload: bytes):
    if len(payload) > 38:
        raise ValueError('QRに入る文字数を超えています。')
    bits = []

    def put(value, length):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)
    put(len(payload), 8)
    for byte in payload:
        put(byte, 8)
    capacity = _DATA_CODEWORDS * 8
    for _ in range(min(4, capacity - len(bits))):
        bits.append(0)
    while len(bits) % 8:
        bits.append(0)
    pads = (capacity - len(bits)) // 8
    for i in range(pads):
        put(0xEC if i % 2 == 0 else 0x11, 8)
    data = []
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i:i + 8]:
            value = (value << 1) | bit
        data.append(value)
    if len(data) != _DATA_CODEWORDS:
        raise ValueError('QRの符号数を合わせられませんでした。')
    return data + _rs_ecc(data, _ECC_CODEWORDS)


def _blank():
    return [[None] * _SIZE for _ in range(_SIZE)]


def _finder(modules, row, col):
    for r in range(-1, 8):
        if not 0 <= row + r < _SIZE:
            continue
        for c in range(-1, 8):
            if not 0 <= col + c < _SIZE:
                continue
            on = (
                (0 <= r <= 6 and c in (0, 6))
                or (0 <= c <= 6 and r in (0, 6))
                or (2 <= r <= 4 and 2 <= c <= 4)
            )
            modules[row + r][col + c] = on


def _patterns(modules):
    _finder(modules, 0, 0)
    _finder(modules, 0, _SIZE - 7)
    _finder(modules, _SIZE - 7, 0)
    for i in range(8, _SIZE - 8):
        if modules[i][6] is None:
            modules[i][6] = i % 2 == 0
        if modules[6][i] is None:
            modules[6][i] = i % 2 == 0
    # 版3の位置合わせは (22, 22) だけ。検出パターンと重なる位置は置かない。
    center = 22
    if modules[center][center] is None:
        for r in range(-2, 3):
            for c in range(-2, 3):
                modules[center + r][center + c] = r in (-2, 2) or c in (-2, 2) or (r == 0 and c == 0)


def _mask_on(pattern, row, col):
    if pattern == 0:
        return (row + col) % 2 == 0
    if pattern == 1:
        return row % 2 == 0
    if pattern == 2:
        return col % 3 == 0
    if pattern == 3:
        return (row + col) % 3 == 0
    if pattern == 4:
        return (row // 2 + col // 3) % 2 == 0
    if pattern == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if pattern == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    if pattern == 7:
        return ((row * col) % 3 + (row + col) % 2) % 2 == 0
    raise ValueError('マスクが不正です。')


def _draw_format(modules, mask, test=False):
    # 誤り訂正Mは 00。
    bits = _bch_type_info(mask & 7)
    for i in range(15):
        bit = False if test else ((bits >> i) & 1) == 1
        if i < 6:
            modules[i][8] = bit
        elif i < 8:
            modules[i + 1][8] = bit
        else:
            modules[_SIZE - 15 + i][8] = bit
        if i < 8:
            modules[8][_SIZE - i - 1] = bit
        elif i < 9:
            modules[8][15 - i - 1 + 1] = bit
        else:
            modules[8][15 - i - 1] = bit
    modules[_SIZE - 8][8] = not test


def _map_data(modules, codewords, mask):
    inc = -1
    row = _SIZE - 1
    bit_index = 7
    byte_index = 0
    data_len = len(codewords)
    for col in range(_SIZE - 1, 0, -2):
        if col <= 6:
            col -= 1
        while True:
            for c in (col, col - 1):
                if modules[row][c] is None:
                    dark = False
                    if byte_index < data_len:
                        dark = ((codewords[byte_index] >> bit_index) & 1) == 1
                    if _mask_on(mask, row, c):
                        dark = not dark
                    modules[row][c] = dark
                    bit_index -= 1
                    if bit_index < 0:
                        byte_index += 1
                        bit_index = 7
            row += inc
            if row < 0 or row >= _SIZE:
                row -= inc
                inc = -inc
                break


def _penalty(modules):
    n = _SIZE
    score = 0
    for row in modules:
        run = 1
        for c in range(1, n):
            if row[c] == row[c - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    for c in range(n):
        run = 1
        for r in range(1, n):
            if modules[r][c] == modules[r - 1][c]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    for r in range(n - 1):
        for c in range(n - 1):
            if modules[r][c] == modules[r][c + 1] == modules[r + 1][c] == modules[r + 1][c + 1]:
                score += 3
    dark = sum(sum(1 for cell in row if cell) for row in modules)
    percent = (dark * 100) // (n * n)
    score += abs(percent - 50) // 5 * 10
    return score


def modules_for(text: str):
    """True が黒。余白は含めない。"""
    payload = text.encode('utf-8')
    codewords = _byte_codewords(payload)
    best = None
    best_score = None
    for mask in range(8):
        modules = _blank()
        _patterns(modules)
        _draw_format(modules, mask, test=True)
        _map_data(modules, codewords, mask)
        _draw_format(modules, mask, test=False)
        score = _penalty(modules)
        if best_score is None or score < best_score:
            best = modules
            best_score = score
    return best


def png_bytes(text: str) -> bytes:
    modules = modules_for(text)
    n = _SIZE + _QUIET * 2
    width = n * _SCALE
    height = width
    rows = []
    for r in range(n):
        src = r - _QUIET
        line = bytearray()
        for c in range(n):
            sc = c - _QUIET
            dark = 0 <= src < _SIZE and 0 <= sc < _SIZE and bool(modules[src][sc])
            line.extend((b'\x00' if dark else b'\xff') * _SCALE)
        row = bytes(line)
        if len(row) != width:
            raise ValueError('QR画像の幅が合いません。')
        for _ in range(_SCALE):
            rows.append(row)
    raw = b''.join(b'\x00' + row for row in rows)
    return _png(width, height, raw)


def _png(width, height, raw):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
