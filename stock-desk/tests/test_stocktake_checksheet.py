import base64
import io
import unittest

from openpyxl import load_workbook

import stock_checksheet as cs
import stock_stocktake as st
import stock_month_close as mc
try:
    from test_stocktake_sheet import W, setup, close_month
except ImportError:
    from tests.test_stocktake_sheet import W, setup, close_month


class CheckSheetTest(unittest.TestCase):
    def _sheet(self):
        folder, c, cu, other, h, loc = setup()
        close_month(c)
        st.add_customer(c, {**W, 'month': '2026-10', 'customerId': cu['id']})
        return folder, c, cu, other

    def test_checks_become_actual_and_mismatch_mark(self):
        folder, c, cu, _ = self._sheet()
        with folder, c:
            rows = st.get(c, '2026-10')['rows']
            m = next(r for r in rows if r['size'] == 'M')
            s = st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': m['id'], 'checks': [1, '', 2, None, '']}]})
            m = next(r for r in s['rows'] if r['id'] == m['id'])
            self.assertEqual((m['checkTotal'], m['actualQty'], m['checkMismatch']), (3, 3, False))
            s = st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': m['id'], 'checks': ['', '', '', '', '']}]})
            m = next(r for r in s['rows'] if r['id'] == m['id'])
            self.assertIsNone(m['actualQty'])
            s = st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': m['id'], 'checks': [0]}]})
            self.assertEqual(next(r for r in s['rows'] if r['id'] == m['id'])['actualQty'], 0)
            s = st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': m['id'], 'actualQty': 4}]})
            self.assertTrue(next(r for r in s['rows'] if r['id'] == m['id'])['checkMismatch'])

    def test_export_format_and_import(self):
        folder, c, cu, other = self._sheet()
        with folder, c:
            close_month(c)
            st.add_customer(c, {**W, 'month': '2026-10', 'customerId': other['id']})
            rows = st.get(c, '2026-10')['rows']
            m = next(r for r in rows if r['size'] == 'M')
            st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': m['id'], 'checks': [2]}]})
            data, name = cs.export_xlsx(c, '2026-10')
            self.assertIn('2026-10', name)
            wb = load_workbook(io.BytesIO(data))
            ws = wb.worksheets[0]
            self.assertEqual([x.value for x in ws[1]][:12], ['仕入先', '商品名', '商品コード', 'サイズ', '在庫数'] + [None] * 5 + ['計', 'id'])
            self.assertEqual(ws.print_title_rows, '$1:$1')
            self.assertTrue(ws.column_dimensions['L'].hidden)
            self.assertEqual(ws.column_dimensions['B'].width, 24.0)
            self.assertEqual(ws['K1'].font.sz, 14)
            self.assertEqual(wb['_meta'].sheet_state, 'hidden')
            mrow = next(r for r in ws.iter_rows(min_row=2) if r[11].value == m['id'])
            self.assertFalse(ws.sheet_properties.pageSetUpPr.fitToPage)
            self.assertEqual(mrow[5].value, 2)
            mrow[5].value, mrow[6].value = 1, 1
            mrow[7].value = 3
            ws.append(['x', 'y', 'z', 'S', 1, 1, None, None, None, None, None, 99999])
            buf = io.BytesIO(); wb.save(buf)
            body = {**W, 'month': '2026-10', 'fileBase64': base64.b64encode(buf.getvalue()).decode()}
            pv = cs.import_preview(c, body)
            self.assertEqual((pv['updateCount'], pv['unmatched']), (1, 1))
            self.assertEqual(pv['updates'][0]['after'], 5)
            with self.assertRaises(ValueError):
                cs.import_apply(c, body)
            s = cs.import_apply(c, {**body, 'confirm': True})
            self.assertEqual(next(r for r in s['rows'] if r['id'] == m['id'])['actualQty'], 5)
            with self.assertRaises(ValueError):
                cs.import_preview(c, {**body, 'month': '2026-11'})
            # 反映済み顧客はスキップ
            st.apply(c, {**W, 'month': '2026-10', 'confirm': True})
            mrow[5].value = 9
            buf = io.BytesIO(); wb.save(buf)
            pv = cs.import_preview(c, {**body, 'fileBase64': base64.b64encode(buf.getvalue()).decode()})
            self.assertEqual((pv['updateCount'], pv['skippedApplied']), (0, 1))

    def test_sheet_titles_unique(self):
        used = set()
        self.assertEqual(cs.sheet_title('株式会社ころころ', used), '株式会社ころころ')
        self.assertEqual(cs.sheet_title('株式会社ころころ', used), '株式会社ころころ(2)')
        self.assertEqual(cs.sheet_title('A/B:C*[x]?', used), 'ABCx')
        self.assertEqual(len(cs.sheet_title('あ' * 40, used)), 31)

    def test_input_columns_fit_a4(self):
        # 雛形: 通常の仕入先なら入力欄5、「石屋商事　インフォメーション・チケット」があると3
        self.assertEqual(st.check_layout(['住商モンブラン㈱', '㈱マジェステックストレード'])['checkCols'], 5)
        lay = st.check_layout(['石屋商事　インフォメーション・チケット'])
        self.assertEqual(lay['checkCols'], 3)
        px = st.col_px(lay['supplierWidth']) + sum(map(st.col_px, st.FIXED_WIDTHS.values())) + st.col_px(st.INPUT_WIDTH) * 4
        self.assertLessEqual(px, st.PRINT_PX)
        self.assertGreater(px + st.col_px(st.INPUT_WIDTH), st.PRINT_PX)
        self.assertEqual(st.check_layout(['あ' * 60])['checkCols'], 1)

    def test_variable_columns_export_import(self):
        folder, c, cu, other = self._sheet()
        with folder, c:
            c.execute("UPDATE suppliers SET name='石屋商事　インフォメーション・チケット'")
            s = st.get(c, '2026-10')
            self.assertEqual(s['blocks'][0]['checkCols'], 3)
            self.assertEqual(len(s['rows'][0]['checks']), 3)
            wb = load_workbook(io.BytesIO(cs.export_xlsx(c, '2026-10')[0]))
            ws = wb.worksheets[0]
            self.assertEqual(ws.cell(1, 9).value, '計')
            self.assertTrue(ws.column_dimensions['J'].hidden)
            ws.cell(2, 6).value, ws.cell(2, 8).value = 2, 3
            buf = io.BytesIO(); wb.save(buf)
            body = {**W, 'month': '2026-10', 'fileBase64': base64.b64encode(buf.getvalue()).decode(), 'confirm': True}
            s = cs.import_apply(c, body)
            r = next(x for x in s['rows'] if x['id'] == ws.cell(2, 10).value)
            self.assertEqual((r['checks'], r['actualQty']), ([2, None, 3], 5))


if __name__ == '__main__':
    unittest.main()
