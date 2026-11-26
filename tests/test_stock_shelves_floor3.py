import unittest
from pathlib import Path

import auth

ROOT = Path(__file__).resolve().parents[1]


class StockShelvesFloor3Test(unittest.TestCase):
    def test_floor3_only_shelves_and_read_only(self):
        f3 = dict(role='floor3', manage=0, overview=0)
        self.assertTrue(auth.stock_shelves_only(f3))
        self.assertFalse(auth.can_open_stock(f3))
        for other in (dict(role='floor', manage=0, overview=0), dict(role='owner', manage=0, overview=0), dict(role='floor3', manage=0, overview=1)):
            self.assertFalse(auth.stock_shelves_only(other))
        self.assertEqual(set(auth.STOCK_SHELVES_ONLY_PATHS), {'/inventory/ishikari-2f.html', '/inventory/ishikari-2f.js', '/inventory/api/ishikari-2f', '/inventory/ishikari-2f.xlsx'})
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn('if shelves_only and path not in STOCK_SHELVES_ONLY_PATHS', server)
        self.assertIn('can_write = False if shelves_only else stock_can_write(u)', server)

    def test_route_hidden_by_default(self):
        html = (ROOT / 'stock-desk/static/ishikari-2f.html').read_text(encoding='utf-8')
        js = (ROOT / 'stock-desk/static/ishikari-2f.js').read_text(encoding='utf-8')
        self.assertIn('<input type="checkbox" id="route-show">', html)
        self.assertIn("localStorage.getItem(ROUTE_SHOW_KEY)==='1'", js)


if __name__ == '__main__':
    unittest.main()
