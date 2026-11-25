"""WEB依頼の設定の整理（ページ廃止・直接範囲へ移行）、商品のWEB表示名・写真、＋制服を追加。"""
import base64
import io
import json
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import web_request_organizations as organizations  # noqa: E402
import web_request_product_display as product_display  # noqa: E402


def org_db(payload):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE web_request_org_settings (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL)')
    c.execute('INSERT INTO web_request_org_settings(id,version,payload) VALUES(1,3,?)', (json.dumps(payload, ensure_ascii=False),))
    return c


class PageScopeMigrationTest(unittest.TestCase):
    def test_page_assignment_becomes_same_direct_scope_and_is_idempotent(self):
        nodes = [
            dict(id='co', parent_id=None, kind='company', name='北雄ラッキー', customer_id=262, site_type=''),
            dict(id='rg', parent_id='co', kind='region', name='札幌市内', customer_id=262, site_type=''),
            dict(id='s1', parent_id='rg', kind='site', name='篠路店', customer_id=262, site_type='店舗'),
        ]
        pages = [dict(id='p1', name='篠路店用', customer_id=262, node_ids=['s1'], can_request=False)]
        scopes = [dict(account_id=7, page_id='p1'), dict(account_id=8, node_ids=['rg'], can_request=True), dict(account_id=9, page_id='gone')]
        c = org_db(dict(nodes=nodes, customer_scopes=scopes, staff_scopes=[], request_pages=pages))
        before = organizations.read(c)
        expected = (organizations.descendant_ids(before, ['s1']), False)
        organizations.schema(c)
        organizations.schema(c)
        org = organizations.read(c)
        s7 = next(s for s in org['customer_scopes'] if s['account_id'] == 7)
        self.assertNotIn('page_id', s7)
        self.assertEqual((organizations.descendant_ids(org, s7['node_ids']), s7['can_request']), expected)
        self.assertEqual(organizations.scope_nodes(org, s7), ['s1'])
        self.assertIn(dict(account_id=8, node_ids=['rg'], can_request=True), org['customer_scopes'])
        self.assertIn(dict(account_id=9, page_id='gone'), org['customer_scopes'])  # 参照先のないものは触らない
        self.assertEqual(org['request_pages'], pages)  # 旧データは残す
        backups = c.execute('SELECT previous_payload FROM web_request_org_migrations').fetchall()
        self.assertEqual(len(backups), 1)
        self.assertIn('"page_id": "p1"', backups[0][0])
        # 保存の検査を通っても移行元の印が残る
        cfg = organizations.validate(dict(nodes=nodes, customer_scopes=[s7], staff_scopes=[], request_pages=pages), {262}, set(), {7})
        self.assertEqual(cfg['customer_scopes'][0]['migrated_from_page_id'], 'p1')


def png_data_url(size=(4, 4)):
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', size, 'red').save(out, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()


class ProductDisplayTest(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        product_display.schema(self.c)
        self.owner = dict(id=1, role='owner', manage=0)

    def test_name_fallback(self):
        self.assertEqual(product_display.resolve_name('', '', '在庫名'), '在庫名')
        self.assertEqual(product_display.resolve_name('', 'WEB名', '在庫名'), 'WEB名')
        self.assertEqual(product_display.resolve_name('契約名', 'WEB名', '在庫名'), '契約名')

    def test_photo_validation(self):
        with self.assertRaises(ValueError):
            product_display.decode_photo('data:image/gif;base64,R0lGODlh')
        with self.assertRaises(ValueError):  # 形式と中身が違う
            product_display.decode_photo('data:image/png;base64,' + base64.b64encode(b'\xff\xd8\xff' + b'0' * 20).decode())
        with self.assertRaises(ValueError):  # 3MB超
            product_display.decode_photo('data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'0' * (3 * 1024 * 1024)).decode())
        raw, mime = product_display.decode_photo(png_data_url())
        self.assertEqual(mime, 'image/jpeg')
        self.assertTrue(raw.startswith(b'\xff\xd8\xff'))

    def test_save_permission_and_remove(self):
        with self.assertRaises(PermissionError):
            product_display.save(self.c, dict(id=2, role='sales', manage=0), dict(product_id=5, display_name='x'), {5})
        with self.assertRaises(ValueError):
            product_display.save(self.c, self.owner, dict(product_id=6, display_name='x'), {5})
        got = product_display.save(self.c, self.owner, dict(product_id=5, display_name=' 夏用 ポロ ', photo=png_data_url()), {5})
        self.assertEqual(got['display_name'], '夏用 ポロ')
        self.assertGreater(got['photo_version'], 0)
        self.assertEqual(product_display.photo(self.c, 5)[1], 'image/jpeg')
        got = product_display.save(self.c, self.owner, dict(product_id=5, remove_photo=True), {5})
        self.assertEqual(got['photo_version'], 0)
        self.assertIsNone(product_display.photo(self.c, 5))
        self.assertEqual(product_display.read_map(self.c)['5']['display_name'], '夏用 ポロ')


class ClientTest(unittest.TestCase):
    def test_extras_helpers(self):
        r = subprocess.run(['node', str(ROOT / 'tests' / 'test_web_request_extras.js')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_wiring(self):
        js = (ROOT / 'request-web.js').read_text(encoding='utf-8')
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("'/web-request-extras.js':'web-request-extras.js'", server)
        self.assertIn('/api/web-request/product-display', server)
        self.assertIn("cart.some(x=>!x.manual&&!products.some(p=>p.id===x.id))", js)
        self.assertNotIn('契約外', (ROOT / 'web-request-extras.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
