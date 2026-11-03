"""表示番号の振り直し（検証用・主管理者のみ）— 一時DBのみ。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import server as stock_server  # noqa: E402
import stock  # noqa: E402

OWNER = {'id': 1, 'name': '主', 'role': 'owner'}
STAFF = {'id': 2, 'name': '担当', 'role': 'staff'}
PATH = '/api/products/display-no/renumber'


class RenumberTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'stock.sqlite3'
        c = stock.connect(self.path)
        stock.initialize(c)
        c.execute('DELETE FROM products')
        self.ids = {}
        for name, cat in [('ベスト', '制服'), ('アウター', ''), ('ベスト', '制服'),
                          ('ボタン', '部品・資材'), ('糸', '部品・資材'), ('刺繍', '加工サービス'),
                          ('裾上げ', '加工サービス')]:
            cur = c.execute('INSERT INTO products(code,name,color,category,active,created_at,updated_at) '
                            "VALUES('',?,'',?,1,'t','t')", (name, cat))
            self.ids.setdefault(name, []).append(cur.lastrowid)
        c.commit()
        c.close()

    def tearDown(self):
        self.folder.cleanup()

    def _post(self, actor, body=None):
        return stock_server.api_post(str(self.path), 'desk', PATH, dict(body or {}),
                                     can_write=True, actor=actor)

    def _nos(self):
        c = stock.connect(self.path)
        try:
            return {r['id']: r['display_no'] for r in c.execute('SELECT id, display_no FROM products')}, \
                {r['prefix']: r['last_no'] for r in c.execute('SELECT * FROM product_display_no_seq')}
        finally:
            c.close()

    def test_staff_forbidden_and_unchanged(self):
        before = self._nos()
        code, _ = self._post(STAFF)
        self.assertEqual(code, 403)
        code, _ = self._post(None)
        self.assertEqual(code, 403)
        self.assertEqual(self._nos(), before)

    def test_read_only_forbidden(self):
        code, _ = stock_server.api_post(str(self.path), 'desk', PATH, {}, can_write=False, actor=OWNER)
        self.assertEqual(code, 403)

    def test_client_order_used_per_category(self):
        # 画面の商品名順（同名は ID 昇順）で送られた順に、区分ごと 1 から
        i = self.ids
        order = [i['アウター'][0], i['ベスト'][0], i['ベスト'][1], i['ボタン'][0], i['刺繍'][0], i['糸'][0], i['裾上げ'][0]]
        code, res = self._post(OWNER, {'order': order})
        self.assertEqual(code, 200)
        self.assertEqual(res['counts'], {'K': 2, 'B': 2, 'S': 3})
        nos, seq = self._nos()
        self.assertEqual([nos[x] for x in order],
                         ['S-00001', 'S-00002', 'S-00003', 'B-0001', 'K-0001', 'B-0002', 'K-0002'])
        self.assertEqual(seq, {'K': 2, 'B': 2, 'S': 3})

    def test_fallback_name_order_and_ids_unchanged_and_new_continues(self):
        before_ids = set(self._nos()[0])
        code, _ = self._post(OWNER, {'order': [999999, 'x']})
        self.assertEqual(code, 200)
        nos, _ = self._nos()
        self.assertEqual(set(nos), before_ids)
        # 品番がすべて空欄なので ID 順
        self.assertEqual(nos[self.ids['ベスト'][0]], 'S-00001')
        self.assertEqual(nos[self.ids['アウター'][0]], 'S-00002')
        c = stock.connect(self.path)
        cur = c.execute('INSERT INTO products(code,name,color,category,active,created_at,updated_at) '
                        "VALUES('','新','','部品・資材',1,'t','t')")
        new_no = c.execute('SELECT display_no FROM products WHERE id=?', (cur.lastrowid,)).fetchone()[0]
        c.commit(); c.close()
        self.assertEqual(new_no, 'B-0003')

    def test_server_code_order_blank_and_hidden_last(self):
        c = stock.connect(self.path)
        c.execute('DELETE FROM products')
        made = {}
        for code, name, active in [('1-001', '白衣', 1), ('', '空欄', 1), ('#1702', 'つなぎ', 1),
                                   ('#3', '和帽子', 1), ('1', '裾上げ', 1), ('0-1', '停止品', 0),
                                   ('1-002', '統合元', 1), ('1-011', 'b', 1), ('1-002', '同品番', 1)]:
            cur = c.execute('INSERT INTO products(code,name,color,category,active,created_at,updated_at) '
                            "VALUES(?,?,'','制服',?,'t','t')", (code, name, active))
            made[name] = cur.lastrowid
        c.execute('UPDATE products SET merged_into_id=? WHERE id=?', (made['同品番'], made['統合元']))
        c.commit(); c.close()
        code, _ = self._post(OWNER, {})
        self.assertEqual(code, 200)
        nos, _ = self._nos()
        got = sorted(made, key=lambda n: nos[made[n]])
        self.assertEqual(got, ['和帽子', 'つなぎ', '裾上げ', '白衣', '同品番', 'b', '空欄', '停止品', '統合元'])

    def test_js_code_order_matches(self):
        import json, shutil, subprocess
        if not shutil.which('node'):
            self.skipTest('node なし')
        js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        start = js.index('function isBlankProductCode')
        end = js.index('function productSuppliersWithItems')
        rows = [{'id': 1, 'code': '1-001'}, {'id': 2, 'code': ''}, {'id': 3, 'code': '#1702'},
                {'id': 4, 'code': '#3'}, {'id': 5, 'code': '1'}, {'id': 6, 'code': '■廃番■9-970'}]
        prog = ("const PRODUCT_SORT_DEFAULT='codeAsc';function resolveProductSort(){return 'codeAsc'}\n"
                + js[start:end] + f"\nconsole.log(JSON.stringify(sortProductRows({json.dumps(rows)},'codeAsc').map(r=>r.id)))")
        out = subprocess.run(['node', '-e', prog], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), [4, 3, 6, 5, 1, 2])

    def test_owner_button_in_verify_zone(self):
        js = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        zone = js[js.index('class="verify-zone"'):]
        zone = zone[:zone.index("`:''}")]
        self.assertIn('id="displayNoRenumber"', zone)
        self.assertIn("sortProductRows(live,'codeAsc')", js)
        self.assertIn("sortProductRows(rest,'codeAsc')", js)
        self.assertIn('品番順', js[js.index("getElementById('displayNoRenumber')"):][:600])
        self.assertIn('confirm(', js[js.index("getElementById('displayNoRenumber')"):][:600])


if __name__ == '__main__':
    unittest.main()
