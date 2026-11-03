import re
import unittest
from pathlib import Path

APP = (Path(__file__).resolve().parents[1] / 'static' / 'app.js').read_text(encoding='utf-8')


def _stock_list_fn():
    start = APP.index('<select id="groups"')
    return APP[APP.rfind('\nfunction ', 0, start):APP.index('\nfunction priceBreakdownHtml')]


class StockViewSelectTest(unittest.TestCase):
    def test_select_replaces_tabs(self):
        self.assertNotIn('class="tabs" id="groups"', APP)
        body = _stock_list_fn()
        self.assertIn('<select id="groups"', body)
        for v, label in (('lot', '在庫明細'), ('customer', '顧客別'), ('product', '商品別'), ('location', '場所別')):
            self.assertIn(f"['{v}','{label}']", body)
        self.assertIn("(f.group||'lot')===id?' selected'", body)

    def test_change_resets_lot_limit(self):
        self.assertIn("$('#groups').onchange=async e=>{f.group=e.target.value;state.stockLotLimit=STOCK_LOT_PAGE_SIZE;await loadStock();paint()}", APP)
        self.assertIn("if(f.group==='lot')q.set('limit'", APP)

    def test_product_id_hidden_in_stock_list(self):
        self.assertNotRegex(_stock_list_fn(), r'商品ID')
        for name in ('renderProductStockList', 'renderProductStockGrid', 'renderCustomerStockList',
                     'renderCustomerStockGrid', 'renderLocationStockList', 'renderLocationStockGrid'):
            i = APP.index(f'function {name}(')
            self.assertNotIn('商品ID', APP[i:APP.index('\n}', i)], name)






class StockTableAlignmentTest(unittest.TestCase):
    def test_fixed_colgroup_per_view(self):
        for v in ('lot', 'product', 'customer', 'location'):
            self.assertIn(f"${{stockColgroup('{v}')}}<thead>", APP)
        self.assertEqual(APP.count('<th class="num">数量（新品／中古／合計）</th><th class="num">在庫金額</th>'), 3)
        css = (Path(__file__).resolve().parents[1] / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('.stock-fixed-table{table-layout:fixed', css)
        self.assertIn('.stock-fixed-table th.num', css)


if __name__ == '__main__':
    unittest.main()
