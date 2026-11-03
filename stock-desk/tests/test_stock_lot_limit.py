"""在庫明細の表示件数制限（既定200件・さらに表示）と既定タブ。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')


def body(group, n):
    return {'group': group, 'lots': [{'id': i, 'qty': 1} for i in range(n)], 'groups': [], 'totalQty': n}


class LotLimitServerTest(unittest.TestCase):
    def test_lot_view_limited_but_totals_and_count_full(self):
        out = stock.slim_stock_view(body('lot', 450), lot_limit=200)
        self.assertEqual(len(out['lots']), 200)
        self.assertEqual(out['lotCount'], 450)
        self.assertEqual(out['totalQty'], 450)
        self.assertEqual([l['id'] for l in out['lots']], list(range(200)))

    def test_no_limit_keeps_all(self):
        out = stock.slim_stock_view(body('lot', 450))
        self.assertEqual(len(out['lots']), 450)
        self.assertEqual(out['lotCount'], 450)

    def test_grouped_views_ignore_limit(self):
        b = body('customer', 450)
        b['groups'] = [{'rows': [{'prices': []}] * 300}]
        out = stock.slim_stock_view(b, lot_limit=200)
        self.assertNotIn('lots', out)
        self.assertEqual(len(out['groups'][0]['rows']), 300)

    def test_server_passes_limit(self):
        src = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("args.pop('limit')", src)
        self.assertIn('lot_limit=lot_limit', src)


class LotLimitClientTest(unittest.TestCase):
    def test_default_lot_view_and_constant(self):
        self.assertIn("filters:{group:'lot',", APP)
        self.assertIn('const STOCK_LOT_PAGE_SIZE=200', APP)
        self.assertIn("if(f.group==='lot')q.set('limit'", APP)

    def test_more_button_and_resets(self):
        self.assertIn('件中${num(state.stock.lots.length)}件表示', APP)
        self.assertIn('さらに表示', APP)
        self.assertIn('state.stockLotLimit=state.stock.lots.length+STOCK_LOT_PAGE_SIZE', APP)
        self.assertGreaterEqual(APP.count('state.stockLotLimit=STOCK_LOT_PAGE_SIZE;await loadStock()'), 2)


if __name__ == '__main__':
    unittest.main()
