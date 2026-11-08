"""商品台帳と旧JSONを同じ送信に載せるときの容量上限。

実ファイルは使わない。報告されたバイト数と同じ長さの合成データで、
旧CSV用の8MB/12MBでは拒否され、新しい有限上限では通ることを確認する。
"""
from __future__ import annotations

import base64
import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import legacy_csv_import as legacy  # noqa: E402
import opening_legacy_json as olj  # noqa: E402
import server as stock_server  # noqa: E402
import upload_limits as ul  # noqa: E402

# 部品・加工の商品登録で選択された実ファイルのバイト数（中身は使わない）。
REPORTED_CSV = 1_852_305
REPORTED_STOCK = 8_163_189
REPORTED_CUSTOMER = 83_153
OLD_RAW_CAP = 8_000_000
OLD_POST_CAP = 12_000_000


def b64_len(n: int) -> int:
    return 4 * ((n + 2) // 3)


def json_of_size(n: int, kind: str) -> bytes:
    if kind == 'stock':
        head = b'{"result":true,"data":[{"id":"s","name":"'
        tail = b'"}]}'
    else:
        head = b'{"result":true,"data":[{"id":"c","name":"'
        tail = b'"}]}'
    pad = n - len(head) - len(tail)
    if pad < 1:
        raise AssertionError(n)
    return head + (b'x' * pad) + tail


class ReportedSizeGateTest(unittest.TestCase):
    def test_old_csv_caps_reject_the_reported_bundle(self):
        self.assertGreater(REPORTED_STOCK, OLD_RAW_CAP)
        bundle = b64_len(REPORTED_CSV) + b64_len(REPORTED_STOCK) + b64_len(REPORTED_CUSTOMER)
        self.assertEqual(bundle, 13_464_864)
        self.assertGreater(bundle, OLD_POST_CAP)

    def test_new_caps_accept_reported_sizes_and_stay_finite(self):
        self.assertEqual(ul.RAW_CSV_MAX_BYTES, OLD_RAW_CAP)
        self.assertLessEqual(REPORTED_CSV, ul.RAW_CSV_MAX_BYTES)
        self.assertLessEqual(REPORTED_STOCK, ul.RAW_LEGACY_JSON_MAX_BYTES)
        self.assertLessEqual(REPORTED_CUSTOMER, ul.RAW_LEGACY_JSON_MAX_BYTES)
        bundle = b64_len(REPORTED_CSV) + b64_len(REPORTED_STOCK) + b64_len(REPORTED_CUSTOMER)
        self.assertLessEqual(bundle, ul.LEDGER_BUNDLE_MAX_BYTES)
        self.assertEqual(ul.LEDGER_BUNDLE_MAX_BYTES, 43_666_668)
        self.assertLess(ul.LEDGER_BUNDLE_MAX_BYTES, 50_000_000)
        for path in ('/api/product-ledger-csv/preview', '/api/product-ledger-csv/commit'):
            self.assertEqual(ul.body_limit_for(path), ul.LEDGER_BUNDLE_MAX_BYTES)
            self.assertEqual(stock_server.body_limit_for(path), ul.LEDGER_BUNDLE_MAX_BYTES)
        self.assertEqual(ul.body_limit_for('/api/product-ledger-csv/history'), OLD_POST_CAP)
        self.assertEqual(ul.body_limit_for('/api/import'), OLD_POST_CAP)
        self.assertEqual(ul.body_limit_for('/api/qr-link/preview'), OLD_POST_CAP)
        self.assertEqual(ul.body_limit_for('/api/not-an-upload'), ul.DEFAULT_BODY_MAX_BYTES)

    def test_client_constants_match_the_server_caps(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('MAX_RAW_FILE_BYTES=8_000_000', js)
        self.assertIn('MAX_RAW_JSON_BYTES=12_000_000', js)
        self.assertIn('MAX_POST_BODY_BYTES=12_000_000', js)
        self.assertEqual(ul.RAW_CSV_MAX_BYTES, 8_000_000)
        self.assertEqual(ul.RAW_LEGACY_JSON_MAX_BYTES, 12_000_000)
        self.assertEqual(ul.LARGE_BODY_MAX_BYTES, 12_000_000)
        self.assertIn(f'MAX_BUNDLE_BODY_BYTES={ul.LEDGER_BUNDLE_MAX_BYTES}', js)
        self.assertIn("assertUploadSize(st.legacyStockFile,st.legacyStockB64,'json')", js)
        self.assertIn("assertUploadSize(st.legacyCustomerFile,st.legacyCustomerB64,'json')", js)
        self.assertEqual(js.count('assertSelectedUploads(st)'), 5)


class DecodeBoundaryTest(unittest.TestCase):
    def test_csv_raw_cap_is_unchanged(self):
        with self.assertRaises(ValueError) as ctx:
            legacy.decode_csv_bytes(b'a' * (ul.RAW_CSV_MAX_BYTES + 1))
        self.assertEqual(str(ctx.exception), 'ファイルが大きすぎます。')

    def test_json_accepts_exact_cap_and_rejects_one_extra_byte(self):
        exact = json_of_size(ul.RAW_LEGACY_JSON_MAX_BYTES, 'stock')
        self.assertEqual(len(exact), ul.RAW_LEGACY_JSON_MAX_BYTES)
        decoded = olj._decode({'legacyStockFile': base64.b64encode(exact).decode('ascii')})
        self.assertEqual(len(decoded['rows']), 1)
        self.assertEqual(decoded['rows'][0]['id'], 's')
        over = base64.b64encode(b'x' * (ul.RAW_LEGACY_JSON_MAX_BYTES + 1)).decode('ascii')
        with self.assertRaises(ValueError) as ctx:
            olj._decode({'legacyStockFile': over})
        self.assertEqual(str(ctx.exception), ul.OVERSIZE_BODY_ERROR)

        customer = json_of_size(ul.RAW_LEGACY_JSON_MAX_BYTES, 'customer')
        catalog = olj.decode_customer_file({
            'legacyCustomerFile': base64.b64encode(customer).decode('ascii'),
        })
        self.assertTrue(catalog['byId']['c']['name'].startswith('x'))
        self.assertGreater(len(catalog['byId']['c']['name']), 1000)
        with self.assertRaises(ValueError) as ctx:
            olj.decode_customer_file({'legacyCustomerFile': over})
        self.assertEqual(str(ctx.exception), ul.OVERSIZE_BODY_ERROR)

    def test_small_json_still_decodes(self):
        blob = json.dumps({'result': True, 'data': [{'id': 'stock-1'}]}).encode('utf-8')
        decoded = olj._decode({'legacyStockFile': base64.b64encode(blob).decode('ascii')})
        self.assertEqual(decoded['rows'][0]['id'], 'stock-1')


class HttpLimitTest(unittest.TestCase):
    def test_handler_rejects_over_cap_before_reading_the_body(self):
        folder = tempfile.TemporaryDirectory()
        db = Path(folder.name) / 'stock.sqlite3'
        stock_server.prepare(db, 'live')
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        httpd = stock_server.ThreadingHTTPServer(('127.0.0.1', port), stock_server.make_handler(db, 'live'))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f'http://127.0.0.1:{port}'
            cases = (
                ('/api/product-ledger-csv/preview', ul.LEDGER_BUNDLE_MAX_BYTES + 1),
                ('/api/product-ledger-csv/commit', ul.LEDGER_BUNDLE_MAX_BYTES + 1),
                ('/api/product-ledger-csv/history', ul.LARGE_BODY_MAX_BYTES + 1),
                ('/api/import', ul.LARGE_BODY_MAX_BYTES + 1),
                ('/api/not-an-upload', ul.DEFAULT_BODY_MAX_BYTES + 1),
            )
            for path, claim in cases:
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
                conn.request('POST', path, body=b'{}', headers={
                    'X-Stock-Request': '1',
                    'Origin': origin,
                    'Content-Type': 'application/json',
                    'Content-Length': str(claim),
                })
                resp = conn.getresponse()
                raw = resp.read()
                conn.close()
                self.assertEqual(resp.status, 400, path)
                self.assertEqual(json.loads(raw)['error'], ul.OVERSIZE_BODY_ERROR, path)
        finally:
            httpd.shutdown()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
