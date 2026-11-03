import pathlib, unittest
S = pathlib.Path(__file__).resolve().parents[1] / 'static'
JS = (S / 'app.js').read_text(encoding='utf-8')
CSS = (S / 'style.css').read_text(encoding='utf-8')


class StockLotShowAllTest(unittest.TestCase):
    def test_show_all_button_next_to_more(self):
        self.assertIn('id="stockLotMore">さらに表示</button> <button type="button" id="stockLotAll">すべて表示</button>', JS)

    def test_show_all_requests_unlimited(self):
        self.assertIn("$('#stockLotAll').onclick=async()=>{state.stockLotLimit=-1;await loadStock();paint()}", JS)

    def test_buttons_only_when_more_rows(self):
        i = JS.index('id="stockLotAll"')
        self.assertIn('if(lotCount>state.stock.lots.length)', JS[max(0, i - 600):i])

    def test_customer_fixed_product_flexible(self):
        self.assertIn('.stock-lot-table col.l-cust{width:calc(15em + 12px)}', CSS)
        self.assertIn('.stock-lot-table col.l-name{width:auto}', CSS)
        self.assertIn('.dm-standard .stock-lot-table col.l-cust{width:150px}', CSS)


if __name__ == '__main__':
    unittest.main()
