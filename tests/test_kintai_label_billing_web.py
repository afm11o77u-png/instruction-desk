"""各支店ラベル集計のWEB表：一番下の合計行。"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LabelBillingWebTotalsTest(unittest.TestCase):
    def test_table_footer_shows_live_size_shipping_and_amount_totals(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        start = html.index('id="kintailabelbilling"')
        end = html.index('id="kintaibihin"')
        panel = html[start:end]
        self.assertIn('id="kintailabelbillingtotal"', panel)
        self.assertIn('<tfoot></tfoot>', panel)
        self.assertIn('kintai.js?v=20261128_label_billing_total', html)

        js = (ROOT / 'kintai.js').read_text(encoding='utf-8')
        start = js.index('function bindLabelBilling()')
        end = js.index('function fiscalStartYear()')
        fn = js[start:end]
        self.assertIn('foot.innerHTML = branch.sizes.map', fn)
        self.assertIn('data-total="qty-0"', fn)
        self.assertIn('data-total="qty-${kind}"', fn)
        self.assertIn('data-total="shipping"', fn)
        self.assertIn('data-total="amount"', fn)
        self.assertIn("qty[kind] = (qty[kind] ?? 0) + (amount(entry?.quantity || '') ?? 0)", fn)
        self.assertIn("shipping += amount(entry?.shipping || '') ?? 0", fn)
        self.assertIn("total.textContent = Number.isNaN(sum) ? '入力を確認' : `${number.format(sum)} 円`", fn)
        self.assertIn('amountCell.textContent = fmt(sum)', fn)
        self.assertIn('refreshAmounts();', fn)
        self.assertIn("body.addEventListener('input'", fn)
        self.assertLess(fn.index("body.addEventListener('input'"), fn.rindex('refreshAmounts();'))

        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn('#kintailabelbillingtable tfoot td', css)
        self.assertIn('font-weight: 700', css[css.index('#kintailabelbillingtable tfoot td'):])
