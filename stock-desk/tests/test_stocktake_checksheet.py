import base64
import io
import unittest

from openpyxl import load_workbook

import stock_checksheet as cs
import stock_stocktake as st
try:
    from test_stocktake_sheet import W, setup
except ImportError:
    from tests.test_stocktake_sheet import W, setup


class CheckSheetTest(unittest.TestCase):
    def _sheet(self):
        folder, c, cu, other, h, loc = setup()
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
            st.add_customer(c, {**W, 'month': '2026-10', 'customerId': other['id']})
            rows = st.get(c, '2026-10')['rows']
            m = next(r for r in rows if r['size'] == 'M')
            st.save(c, {**W, 'month': '2026-10', 'rows': [{'id': m['id'], 'checks': [2]}]})
            data, name = cs.export_xlsx(c, '2026-10')
            self.assertIn('2026-10', name)
            wb = load_workbook(io.BytesIO(data))
            ws = wb.worksheets[0]
            self.assertEqual([x.value for x in ws[1]][:11], cs.HEAD[:5] + [None] * 5 + ['計'])
            self.assertEqual(ws.print_title_rows, '$1:$1')
            self.assertTrue(ws.column_dimensions['L'].hidden)
            self.assertEqual(ws.column_dimensions['B'].width, 24.0)
            self.assertEqual(ws['K1'].font.sz, 14)
            self.assertEqual(wb['_meta'].sheet_state, 'hidden')
            mrow = next(r for r in ws.iter_rows(min_row=2) if r[11].value == m['id'])
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
        self.assertEqual(cs.sheet_title('株式会社ころころ', used), 'ころころ')
        self.assertEqual(cs.sheet_title('ころころ㈱', used), 'ころころ(2)')


if __name__ == '__main__':
    unittest.main()
