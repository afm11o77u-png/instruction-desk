import pathlib, re, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')


class StockLotColumnsTest(unittest.TestCase):
    def test_header_order(self):
        self.assertIn('<th>顧客</th><th>商品</th><th>サイズ</th><th>仕様</th><th>状態</th><th>場所</th><th class="num lot-order" title="入荷日不明の払出し順">順</th><th class="num">単価</th>', APP)

    def test_colgroup_matches_header(self):
        m = re.search(r"lot:\[([^\]]*)\]", APP)
        self.assertEqual(len(m.group(1).split(',')), 11)
        self.assertIn('colspan="11">在庫はありません。', APP)

    def test_product_two_lines_and_split_size_spec(self):
        self.assertIn('<span class="lot-code">${esc(l.code)}</span><span class="lot-pname"', APP)
        self.assertIn('<td>${esc(l.size)}</td><td>${esc(l.embroideryLabel)}</td>', APP)

    def test_payout_order_column_only_for_unknown(self):
        self.assertIn("<td class=\"qty lot-order\">${l.receivedUnknown?esc(l.payoutOrder):''}</td>", APP)
        self.assertNotIn("'入荷日不明・払出し順 '+l.payoutOrder", APP)

    def test_cost_pending_kept_in_unit_cost(self):
        self.assertIn("<td class=\"money\">${l.costPending?'原価未確定':yen(l.unitCost)}</td>", APP)

    def test_customer_location_oneline_with_title(self):
        self.assertIn('class="dm-primary lot-oneline" title="${esc(l.customerName)}"', APP)
        self.assertIn('class="lot-oneline" title="${esc(l.locationLabel)}"', APP)
        self.assertIn('text-overflow:ellipsis', CSS)
        self.assertIn('.stock-lot-table col.l-ord{width:46px}', CSS)


if __name__ == '__main__':
    unittest.main()


class StockLotOrderWidthTest(unittest.TestCase):
    def test_order_and_unit_fit_large_values(self):
        for rule in ('.stock-lot-table col.l-ord{width:46px}', '.stock-lot-table col.l-unit{width:78px}',
                     '.dm-standard .stock-lot-table col.l-ord{width:48px}', '.dm-standard .stock-lot-table col.l-unit{width:88px}',
                     '.stock-lot-table .lot-order{padding-left:4px;padding-right:6px;white-space:nowrap}',
                     '.stock-lot-table col.l-cust{width:calc(15em + 12px)}', '.dm-standard .stock-lot-table col.l-cust{width:150px}'):
            self.assertIn(rule, CSS)
