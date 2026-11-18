"""在庫QRの画像。標準ライブラリだけでPNGを作る。

外部のQRサービスへは送らない。Pillow や qrcode が無くても在庫アプリは起動する。
版3（新QRの37文字ID用）と版8（旧QRのJSON文字列用、最大152バイト）に対応。
"""
from __future__ import annotations

import struct
import zlib

_QUIET = 4
_SCALE = 8

# (version, size, total_data_codewords, groups: ((count, data_cw, ecc_cw), ...), align_centers)
_SPECS = (
    (3, 29, 44, ((1, 44, 26),), (22,)),
    (8, 49, 154, ((2, 38, 22), (2, 39, 22)), (6, 24, 42)),
)

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


def _bch_version_info(version):
    g12 = 0b1111100100101
    d = version << 12
    while d.bit_length() - g12.bit_length() >= 0:
        d ^= g12 << (d.bit_length() - g12.bit_length())
    return (version << 12) | d


def _select_spec(payload_len):
    for spec in _SPECS:
        # 4bit mode + 8bit length (for v1..9) = 12bit header -> max bytes = total_data - 2
        if payload_len <= spec[2] - 2:
            return spec
    raise ValueError('QRに入る文字数を超えています。')


def _byte_codewords(payload: bytes, spec):
    _ver, _size, total_data, groups, _align = spec
    bits = []

    def put(value, length):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)
    put(len(payload), 8)
    for byte in payload:
        put(byte, 8)
    capacity = total_data * 8
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
    if len(data) != total_data:
        raise ValueError('QRの符号数を合わせられませんでした。')
    data_blocks = []
    ecc_blocks = []
    pos = 0
    for count, dcw, ecw in groups:
        for _ in range(count):
            blk = data[pos:pos + dcw]
            pos += dcw
            data_blocks.append(blk)
            ecc_blocks.append(_rs_ecc(blk, ecw))
    out = []
    max_d = max(len(b) for b in data_blocks)
    for i in range(max_d):
        for b in data_blocks:
            if i < len(b):
                out.append(b[i])
    max_e = max(len(e) for e in ecc_blocks)
    for i in range(max_e):
        for e in ecc_blocks:
            if i < len(e):
                out.append(e[i])
    return out


def _blank(size):
    return [[None] * size for _ in range(size)]


def _finder(modules, size, row, col):
    for r in range(-1, 8):
        if not 0 <= row + r < size:
            continue
        for c in range(-1, 8):
            if not 0 <= col + c < size:
                continue
            on = (
                (0 <= r <= 6 and c in (0, 6))
                or (0 <= c <= 6 and r in (0, 6))
                or (2 <= r <= 4 and 2 <= c <= 4)
            )
            modules[row + r][col + c] = on


def _patterns(modules, spec):
    version, size, _total, _groups, align_centers = spec
    _finder(modules, size, 0, 0)
    _finder(modules, size, 0, size - 7)
    _finder(modules, size, size - 7, 0)
    for i in range(8, size - 8):
        if modules[i][6] is None:
            modules[i][6] = i % 2 == 0
        if modules[6][i] is None:
            modules[6][i] = i % 2 == 0
    for ar in align_centers:
        for ac in align_centers:
            if (ar < 9 and ac < 9) or (ar < 9 and ac >= size - 8) or (ar >= size - 8 and ac < 9):
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    modules[ar + r][ac + c] = r in (-2, 2) or c in (-2, 2) or (r == 0 and c == 0)
    if version >= 7:
        vbits = _bch_version_info(version)
        for i in range(18):
            bit = ((vbits >> i) & 1) == 1
            r = i // 3
            c = size - 11 + (i % 3)
            modules[r][c] = bit
            modules[c][r] = bit


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


def _draw_format(modules, size, mask, test=False):
    # 誤り訂正Mは 00。
    bits = _bch_type_info(mask & 7)
    for i in range(15):
        bit = False if test else ((bits >> i) & 1) == 1
        if i < 6:
            modules[i][8] = bit
        elif i < 8:
            modules[i + 1][8] = bit
        else:
            modules[size - 15 + i][8] = bit
        if i < 8:
            modules[8][size - i - 1] = bit
        elif i < 9:
            modules[8][15 - i - 1 + 1] = bit
        else:
            modules[8][15 - i - 1] = bit
    modules[size - 8][8] = not test


def _map_data(modules, size, codewords, mask):
    inc = -1
    row = size - 1
    bit_index = 7
    byte_index = 0
    data_len = len(codewords)
    for col in range(size - 1, 0, -2):
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
            if row < 0 or row >= size:
                row -= inc
                inc = -inc
                break


def _penalty(modules):
    n = len(modules)
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
    spec = _select_spec(len(payload))
    size = spec[1]
    codewords = _byte_codewords(payload, spec)
    best = None
    best_score = None
    for mask in range(8):
        modules = _blank(size)
        _patterns(modules, spec)
        _draw_format(modules, size, mask, test=True)
        _map_data(modules, size, codewords, mask)
        _draw_format(modules, size, mask, test=False)
        score = _penalty(modules)
        if best_score is None or score < best_score:
            best = modules
            best_score = score
    return best


def png_bytes(text: str) -> bytes:
    modules = modules_for(text)
    size = len(modules)
    n = size + _QUIET * 2
    width = n * _SCALE
    height = width
    rows = []
    for r in range(n):
        src = r - _QUIET
        line = bytearray()
        for c in range(n):
            sc = c - _QUIET
            dark = 0 <= src < size and 0 <= sc < size and bool(modules[src][sc])
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
