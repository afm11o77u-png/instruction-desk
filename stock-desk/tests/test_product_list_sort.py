"""商品一覧の並び順（表示のみ・DB非破壊）と Excel 出力の一致。"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import excel_export  # noqa: E402
import stock  # noqa: E402

APP_JS = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
STYLE_CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')


def _fn(source: str, name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\s*\{{', source)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\n\s*function ', source[m.end():])
    end = m.end() + nxt.start() if nxt else len(source)
    return source[start:end]


def newdb():
    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    return folder, path, c


def _row(code, name, pid):
    return {'id': pid, 'code': code, 'name': name}


class ProductListSortUiTests(unittest.TestCase):
    def test_sort_control_and_persistence(self):
        self.assertIn("PRODUCT_SORT_KEY='stock-desk:product-list-sort'", APP_JS)
        self.assertIn("PRODUCT_SORT_DEFAULT='codeAsc'", APP_JS)
        self.assertIn('品番順（昇順）', APP_JS)
        self.assertIn('品番順（降順）', APP_JS)
        self.assertIn('商品名順（昇順）', APP_JS)
        self.assertIn('商品名順（降順）', APP_JS)
        self.assertIn('商品ID順（昇順）', APP_JS)
        self.assertIn('商品ID順（降順）', APP_JS)
        self.assertIn('id="productSort"', APP_JS)
        self.assertIn('並び順', APP_JS)
        self.assertIn('saveProductSort', APP_JS)
        self.assertIn('loadProductSort', APP_JS)
        self.assertIn('resolveProductSort', APP_JS)
        self.assertIn("localStorage.setItem(PRODUCT_SORT_KEY", APP_JS)
        self.assertIn('.product-sort select', STYLE_CSS)

    def test_products_applies_sort_to_table_and_grid(self):
        body = _fn(APP_JS, 'products')
        self.assertIn('sortProductRows(', body)
        self.assertIn('resolveProductSort()', body)
        self.assertIn('productSortControl()', body)
        self.assertIn("saveProductSort(state.productSort)", body)
        # 同一 rows を表・カード双方に使う
        self.assertIn('rows.map(r=>{', body)
        self.assertIn('rows.map(r=>`<article class="dm-card"', body)

    def test_sort_rules_in_js(self):
        self.assertIn("localeCompare(String(b),'ja',{numeric:true})", APP_JS)
        self.assertIn("localeCompare(String(b||''),'ja')", APP_JS)
        cmp_fn = _fn(APP_JS, 'compareProductRows')
        self.assertIn('isBlankProductCode', cmp_fn)
        self.assertIn('return 1', cmp_fn)  # empty last
        self.assertIn('return -1', cmp_fn)
        self.assertIn('compareProductIds', cmp_fn)


class ProductListSortLogicTests(unittest.TestCase):
    def test_natural_code_order_and_empty_last(self):
        rows = [
            _row('A10', '十', 10),
            _row('A1', '一', 1),
            _row('A2', '二', 2),
            _row('', '空1', 3),
            _row('別注', '別注名', 4),
            _row('不明', '不明名', 5),
            _row('', '空2', 6),
            _row('A1', '同一品番遅いID', 20),
        ]
        asc = excel_export.sort_product_rows(rows, 'codeAsc')
        self.assertEqual(
            [r['code'] for r in asc],
            ['A1', 'A1', 'A2', 'A10', '不明', '別注', '', ''],
        )
        self.assertEqual([r['id'] for r in asc if r['code'] == 'A1'], [1, 20])
        self.assertEqual([r['id'] for r in asc if r['code'] == ''], [3, 6])

        desc = excel_export.sort_product_rows(rows, 'codeDesc')
        # 空品番は降順でも末尾。非空は自然順の逆。
        non_empty = [r for r in desc if r['code']]
        empty = [r for r in desc if not r['code']]
        self.assertEqual([r['code'] for r in empty], ['', ''])
        self.assertEqual([r['id'] for r in empty], [3, 6])
        self.assertEqual(non_empty[0]['code'], '別注')
        self.assertEqual([r['code'] for r in non_empty if r['code'].startswith('A')], ['A10', 'A2', 'A1', 'A1'])

    def test_name_and_id_sort(self):
        rows = [
            _row('Z', 'みかん', 3),
            _row('Y', 'あお', 1),
            _row('X', 'いぬ', 2),
            _row('W', 'あお', 4),
        ]
        by_name = excel_export.sort_product_rows(rows, 'nameAsc')
        self.assertEqual([r['name'] for r in by_name], ['あお', 'あお', 'いぬ', 'みかん'])
        self.assertEqual([r['id'] for r in by_name if r['name'] == 'あお'], [1, 4])

        by_name_desc = excel_export.sort_product_rows(rows, 'nameDesc')
        self.assertEqual(by_name_desc[0]['name'], 'みかん')
        self.assertEqual([r['id'] for r in by_name_desc if r['name'] == 'あお'], [1, 4])

        by_id = excel_export.sort_product_rows(rows, 'idAsc')
        self.assertEqual([r['id'] for r in by_id], [1, 2, 3, 4])
        by_id_desc = excel_export.sort_product_rows(rows, 'idDesc')
        self.assertEqual([r['id'] for r in by_id_desc], [4, 3, 2, 1])

    def test_excel_export_uses_same_sort_without_db_mutation(self):
        folder, path, c = newdb()
        try:
            su = stock.save_supplier(c, {'name': '並び仕入', 'active': True})
            p_a10 = stock.save_product(c, {
                'code': 'A10', 'name': '十番', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'], 'links': [],
            })
            p_a1 = stock.save_product(c, {
                'code': 'A1', 'name': '一番', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'], 'links': [],
            })
            p_blank = stock.save_product(c, {
                'code': '', 'name': '空品番', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'], 'links': [],
            })
            p_custom = stock.save_product(c, {
                'code': '別注', 'name': '別注商品', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'], 'links': [],
            })
            p_a2 = stock.save_product(c, {
                'code': 'A2', 'name': '二番', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'], 'links': [],
            })
            c.commit()
            before = {
                'products': c.execute('SELECT COUNT(*) FROM products').fetchone()[0],
                'codes': [
                    r['code'] for r in c.execute(
                        'SELECT code FROM products ORDER BY id'
                    )
                ],
            }
            payload = excel_export._products(c, {
                'scope': 'filtered', 'q': '', 'includeStopped': '0', 'sort': 'codeAsc',
            })
            codes = [p['code'] for p in payload['products']]
            self.assertEqual(codes, ['A1', 'A2', 'A10', '別注', ''])
            self.assertEqual(
                [p['id'] for p in payload['products']],
                [p_a1['id'], p_a2['id'], p_a10['id'], p_custom['id'], p_blank['id']],
            )
            blob, _ = excel_export.export_xlsx(c, {
                'kind': ['products'], 'scope': ['filtered'], 'q': [''],
                'includeStopped': ['0'], 'sort': ['codeDesc'],
            })
            sheets = __import__('excel_book').read_sheets(blob)
            product_rows = sheets['商品一覧'][1:]
            export_codes = [r[1] for r in product_rows]
            self.assertEqual(export_codes[-1], '')  # empty last even on desc
            self.assertNotEqual(export_codes[0], '')
            meta = sheets['出力情報']
            meta_map = {r[0]: r[1] for r in meta[1:]}
            self.assertEqual(meta_map.get('並び順'), '品番順（降順）')
            after_codes = [
                r['code'] for r in c.execute('SELECT code FROM products ORDER BY id')
            ]
            self.assertEqual(after_codes, before['codes'])
            self.assertEqual(
                c.execute('SELECT COUNT(*) FROM products').fetchone()[0],
                before['products'],
            )
        finally:
            c.close()
            folder.cleanup()

    def test_sort_works_with_search_and_stopped_filter(self):
        folder, path, c = newdb()
        try:
            su = stock.save_supplier(c, {'name': '絞込仕入', 'active': True})
            stock.save_product(c, {
                'code': 'B2', 'name': '青シャツ', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'], 'links': [],
            })
            stock.save_product(c, {
                'code': 'B10', 'name': '青パンツ', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'], 'links': [],
            })
            stock.save_product(c, {
                'code': 'B1', 'name': '赤シャツ', 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': ['M'], 'links': [],
            })
            stopped = stock.save_product(c, {
                'code': 'B0', 'name': '青停止', 'color': '', 'supplierId': su['id'],
                'active': False, 'sizes': ['M'], 'links': [],
            })
            c.commit()
            filtered = excel_export._products(c, {
                'scope': 'filtered', 'q': '青', 'includeStopped': '0', 'sort': 'codeAsc',
            })
            self.assertEqual([p['code'] for p in filtered['products']], ['B2', 'B10'])
            with_stopped = excel_export._products(c, {
                'scope': 'filtered', 'q': '青', 'includeStopped': '1', 'sort': 'codeAsc',
            })
            self.assertEqual(
                [p['code'] for p in with_stopped['products']],
                ['B0', 'B2', 'B10'],
            )
            self.assertIn(stopped['id'], [p['id'] for p in with_stopped['products']])
        finally:
            c.close()
            folder.cleanup()


if __name__ == '__main__':
    unittest.main()
