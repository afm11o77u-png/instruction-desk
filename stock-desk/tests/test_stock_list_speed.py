"""在庫一覧の速度改善 — moves(lot_id) 索引、/api/stock 応答の軽量化、表示中モードのみ描画。"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock

APP = Path(__file__).resolve().parents[1] / 'static' / 'app.js'


def indexes(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='moves'")}


class MovesIndexTest(unittest.TestCase):
    def test_index_on_new_and_existing_db(self):
        with tempfile.TemporaryDirectory() as d:
            c = stock.connect(Path(d) / 's.sqlite3')
            stock.initialize(c)
            self.assertIn('moves_lot', indexes(c))
            c.execute('DROP INDEX moves_lot')
            stock.migrate_schema(c)
            self.assertIn('moves_lot', indexes(c))
            stock.migrate_schema(c)  # 冪等
            stock.initialize(c)
            self.assertIn('moves_lot', indexes(c))
            c.close()


def body(group):
    return {
        'group': group, 'lots': [{'id': 1}], 'totals': {'qty': 3},
        'groups': [{'rows': [
            {'prices': [{'unitCost': 100, 'qty': 1, 'amount': 100, 'receipts': [{'at': 'x'}]}]},
            {'prices': [{'unitCost': 100, 'qty': 2, 'amount': 200, 'receipts': [{'at': 'x'}, {'at': 'y'}]}]},
            {'prices': [{'unitCost': 100, 'qty': 1, 'amount': 100, 'receipts': [{'at': 'x'}]},
                        {'unitCost': 120, 'qty': 1, 'amount': 120, 'receipts': [{'at': 'y'}]}]},
        ]}],
    }


class SlimViewTest(unittest.TestCase):
    def test_grouped_views_drop_lots_and_single_receipts(self):
        for g in ('customer', 'product', 'location'):
            out = stock.slim_stock_view(body(g))
            self.assertNotIn('lots', out)
            self.assertEqual(out['totals'], {'qty': 3})
            rows = out['groups'][0]['rows']
            self.assertNotIn('receipts', rows[0]['prices'][0])
            self.assertEqual(rows[0]['prices'][0]['unitCost'], 100)
            self.assertEqual(len(rows[1]['prices'][0]['receipts']), 2)  # 内訳を出す行は保持
            self.assertTrue(all('receipts' in p for p in rows[2]['prices']))

    def test_lot_view_untouched(self):
        b = body('lot')
        self.assertEqual(stock.slim_stock_view(b), body('lot'))

    def test_server_uses_slim(self):
        src = (Path(__file__).resolve().parents[1] / 'server.py').read_text(encoding='utf-8')
        self.assertIn('stock.slim_stock_view(stock.stock_query(', src)


class VisibleModeOnlyTest(unittest.TestCase):
    def test_stock_list_renders_only_visible_mode(self):
        src = APP.read_text(encoding='utf-8')
        fn = src[src.index('function stockList('):]
        fn = fn[:fn.index('\nfunction ', 1)]
        self.assertGreaterEqual(fn.count("const showGrid=mode==='grid'"), 2)
        self.assertIn("body.innerHTML=showGrid?''", fn)
        self.assertIn("grid.innerHTML=showGrid?renderers[1](state.stock.groups):''", fn)
        self.assertIn("grid.innerHTML=!showGrid?''", fn)
        self.assertIn('repaint()', src)  # 切替時は再描画


if __name__ == '__main__':
    unittest.main()
