"""QR読み取り。合成DBと合成画像だけ。実カメラと専用端末は使わない。"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

import qr_image  # noqa: E402
import qr_link_import  # noqa: E402
import stock  # noqa: E402
import stock_qr  # noqa: E402

JSQR = ROOT / 'static' / 'vendor' / 'jsqr' / 'jsQR.js'


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def seed(c):
    cu = stock.save_customer(c, {'name': '顧客A', 'active': True})
    su = stock.save_supplier(c, {'name': '仕入A', 'active': True})
    pr = stock.save_product(c, {
        'code': 'AZ-1', 'name': '白衣', 'color': '白', 'category': '制服',
        'supplierId': su['id'], 'active': True, 'sizes': ['M'],
        'links': [{'customerId': cu['id'], 'embroidery': '', 'isStandard': True, 'active': True}],
    })
    loc_a = stock.save_location(c, {'siteName': '本社', 'shelfName': 'A', 'active': True})
    site_id = loc_a.get('siteId') or loc_a.get('parentId')
    loc_b = stock.save_location(c, {'siteId': site_id, 'shelfName': 'B', 'active': True})
    cp = pr['links'][0]['id']
    for i, loc in enumerate((loc_a, loc_b), start=1):
        stock.register_opening(c, {
            '_canWrite': True, '_actor': {'name': 'seed'},
            'token': f'qr-read-open-{i}',
            'customerProductId': cp, 'locationId': loc['id'], 'condition': 'new',
            'basisDate': '2026-01-01', 'receivedUnknown': True, 'payoutOrder': i,
            'allowZero': True, 'lines': [{'size': 'M', 'qty': i, 'unitCost': 100}],
        })
    qr_link_import.ensure_schema(c)
    c.execute(
        '''INSERT INTO qr_stock_links(
             legacy_stock_id, legacy_customer_id, legacy_maker_id, customer_name,
             item_num, item_name, customer_id, product_id, customer_product_id,
             size_label, condition, location_id, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        ('oldqr1', 'c1', 'm1', '顧客A', 'AZ-1', '白衣', cu['id'], pr['id'], cp,
         'M', 'new', None, 't', 't'),
    )
    c.commit()
    return {'customer': cu, 'product': pr, 'handle': cp}


def png_gray(blob):
    pos = 8
    width = height = None
    raw = b''
    while pos < len(blob):
        n, tag = struct.unpack('>I4s', blob[pos:pos + 8])
        data = blob[pos + 8:pos + 8 + n]
        pos += 12 + n
        if tag == b'IHDR':
            width, height = struct.unpack('>II', data[:8])
        elif tag == b'IDAT':
            raw += data
        elif tag == b'IEND':
            break
    raw = zlib.decompress(raw)
    stride = width + 1
    gray = bytearray()
    for y in range(height):
        row = raw[y * stride:(y + 1) * stride]
        if row[:1] != b'\x00':
            raise ValueError('QR画像の形式を読めません。')
        gray.extend(row[1:])
    return width, height, bytes(gray)


def decode_png(blob):
    width, height, gray = png_gray(blob)
    rgba = bytearray()
    for px in gray:
        rgba.extend((px, px, px, 255))
    script = r'''
const fs = require('fs');
const jsQR = require(process.env.JSQR);
const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
const data = Uint8ClampedArray.from(spec.rgba);
const found = jsQR(data, spec.width, spec.height, {inversionAttempts:'dontInvert'});
if (!found) process.exit(2);
process.stdout.write(found.data);
'''
    proc = subprocess.run(
        ['node', '-e', script],
        input=json.dumps({'width': width, 'height': height, 'rgba': list(rgba)}),
        text=True, capture_output=True,
        env={**dict(**{k: v for k, v in __import__('os').environ.items()}), 'JSQR': str(JSQR)},
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or 'decode failed')
    return proc.stdout


class QrReadApiTest(unittest.TestCase):
    def test_old_and_new_do_not_change_qty_or_pick_one(self):
        folder, _path, c = newdb()
        try:
            seeded = seed(c)
            before = c.execute('SELECT COALESCE(SUM(qty_remaining),0) q FROM lots').fetchone()['q']
            shown = stock_qr.show(c, {
                '_canWrite': True, '_actor': {'name': 'seed'},
                'customerProductId': seeded['handle'], 'size': 'M', 'condition': 'new',
            })
            c.commit()
            new = stock_qr.read_scan(c, {'qr': shown['publicId']})
            self.assertEqual(new['qrKind'], 'new')
            self.assertTrue(new['found'])
            self.assertEqual(new['lotCount'], 2)
            self.assertIsNone(new['pickedLotId'])
            self.assertFalse(new['qtyChanged'])
            old = stock_qr.read_scan(c, {'qr': json.dumps({'id': 'oldqr1', 'customer_id': 'c1', 'maker_id': 'm1'})})
            self.assertEqual(old['qrKind'], 'legacy')
            self.assertEqual(old['lotCount'], 2)
            self.assertIsNone(old['pickedLotId'])
            self.assertEqual(c.execute('SELECT COALESCE(SUM(qty_remaining),0) q FROM lots').fetchone()['q'], before)
            missing = stock_qr.read_scan(c, {'qr': 'nqr1.' + 'ab' * 16})
            self.assertFalse(missing['found'])
            self.assertEqual(missing['lotCount'], 0)
        finally:
            c.close()
            folder.cleanup()

    def test_rejects_url_and_overlong_without_navigation(self):
        folder, _path, c = newdb()
        try:
            with self.assertRaises(ValueError):
                stock_qr.read_scan(c, {'qr': 'https://example.invalid/qr'})
            with self.assertRaises(ValueError):
                stock_qr.read_scan(c, {'qr': 'x' * 401})
            with self.assertRaises(ValueError):
                stock_qr.parse_scan('javascript:alert(1)')
        finally:
            c.close()
            folder.cleanup()


class QrImageDecodeTest(unittest.TestCase):
    def test_local_jsqr_reads_synthetic_old_and_new(self):
        new_id = 'nqr1.' + 'a1' * 16
        old_id = 'oldqr1'
        self.assertEqual(decode_png(qr_image.png_bytes(new_id)), new_id)
        self.assertEqual(decode_png(qr_image.png_bytes(old_id)), old_id)


class QrReadUiSourceTest(unittest.TestCase):
    def test_menu_and_page_are_shared(self):
        desk = (REPO / 'index.html').read_text(encoding='utf-8')
        place = (REPO / 'placement.js').read_text(encoding='utf-8')
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        page = (ROOT / 'static' / 'qr_read.js').read_text(encoding='utf-8')
        html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('data-stock-section="qrread"', desk)
        self.assertIn('>QR読み取り<', desk)
        self.assertIn("section==='qrread'", place)
        self.assertIn("'qrread'", app)
        self.assertIn('vendor/jsqr/jsQR.js', html)
        self.assertIn('qr_read.js', html)
        self.assertNotIn('unpkg.com', page)
        self.assertNotIn('cdn.', page)
        self.assertIn('/api/stock-qr/read', page)
        self.assertIn('facingMode:{ideal:\'environment\'}', page)
        self.assertIn('BarcodeDetector', page)
        self.assertIn('jsQR', page)
        self.assertIn('閲覧専用', page)
        self.assertIn('openInboundDialog', page)
        self.assertIn('openOutboundDialog', page)
        self.assertIn('https://', page)
        license_text = (ROOT / 'static' / 'vendor' / 'jsqr' / 'LICENSE').read_text(encoding='utf-8')
        self.assertIn('Apache License', license_text)


if __name__ == '__main__':
    unittest.main()
