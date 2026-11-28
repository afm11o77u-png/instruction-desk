import unittest
from datetime import datetime

import openpyxl
import picking_sorter as ps

ROW = dict(
    requester='マーク', customer='テスト顧客', name='白衣', maker='国立',
    sku='MM100', size='L', condition='新\n古', quantity=1, note='', note2='',
    stock='b', source_file='',
)


class BlankFormatTest(unittest.TestCase):
    def test_picking_list_blank_rows_share_format_and_append_uses_last_value(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        now = datetime(2026, 11, 28)
        ps._apply_sheet_styling(ws, [ROW, dict(ROW, name='パンツ')], '石狩倉庫ピッキングリスト', now, '2026112801.xlsx')
        last = ps._last_value_row(ws)
        self.assertEqual(last, 3)
        self.assertTrue(ws.cell(2, 3).value)
        # 書式だけの空行があり、値はない
        self.assertGreater(ws.max_row, last)
        blank = last + 1
        self.assertIsNone(ws.cell(blank, 3).value)
        self.assertTrue(ws.cell(blank, 3).border.left and ws.cell(blank, 3).border.left.style)
        self.assertEqual(ws.cell(blank, 3).font.name, ws.cell(last, 3).font.name)
        self.assertEqual(ws.row_dimensions[blank].height, ps._DATA_ROW_HEIGHT)
        # 印刷範囲はページ末尾までに留まる
        rows_per_page = ps._data_rows_per_page(ws)
        end = ps._blank_format_end_row(last, rows_per_page)
        self.assertTrue(str(ws.print_area).endswith(f"$A$1:$O${end}"), ws.print_area)
        self.assertGreaterEqual(end, last + 8)  # 1ページ分の空行余地

        # 追記は書式空行の下ではなく、最終データ行の直下
        # 報告用シートで追記の安全性を見る
        report = openpyxl.Workbook()
        rws = report.active
        rws.title = '持ち込み'
        ps._append_rows_to_report_sheet(rws, [ROW, dict(ROW, name='パンツ')], '持ち込みリスト', now, '報告用261128.xlsx')
        last = ps._last_value_row(rws)
        self.assertEqual(last, 3)
        self.assertGreater(rws.max_row, last)
        self.assertIsNone(rws.cell(last + 1, 3).value)
        self.assertTrue(rws.cell(last + 1, 3).border.left and rws.cell(last + 1, 3).border.left.style)
        ps._append_rows_to_report_sheet(rws, [dict(ROW, name='追記分')], '持ち込みリスト', now, '報告用261128.xlsx')
        self.assertEqual(ps._last_value_row(rws), 4)
        self.assertEqual(rws.cell(4, 3).value, '追記分')
        self.assertIsNone(rws.cell(5, 3).value)
        self.assertTrue(rws.cell(5, 3).border.left and rws.cell(5, 3).border.left.style)

    def test_header_only_sheet_gets_data_style_blanks(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ps._append_rows_to_report_sheet(ws, [], '倉庫', datetime(2026, 11, 28), '報告用261128.xlsx')
        self.assertEqual(ps._last_value_row(ws), 1)
        self.assertEqual(ws.cell(1, 3).value, 'アイテム名')
        self.assertIsNone(ws.cell(2, 3).value)
        self.assertTrue(ws.cell(2, 3).border.left and ws.cell(2, 3).border.left.style)


if __name__ == '__main__':
    unittest.main()
