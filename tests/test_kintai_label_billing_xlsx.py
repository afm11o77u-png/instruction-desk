"""各支店ラベル集計のExcel出力: マス位置・内容は維持し、表の外側に罫線を出さない。"""
from __future__ import annotations

import unittest
from io import BytesIO
from urllib.parse import unquote
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

import kintai_label_billing as lb

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def _export(year=2026, month=11, entries=(), scope='all'):
    status, blob, ctype, headers = lb.export(year, month, entries, scope=scope)
    assert status == 200
    return blob, headers


class LabelBillingXlsxTest(unittest.TestCase):
    def setUp(self):
        self.entries = [
            dict(branch='石狩', day=1, kind=0, details='店舗A', quantity=12, shipping=800),
            dict(branch='石狩', day=1, kind=1, details='店舗A', quantity=3, shipping=None),
            dict(branch='千歳', day=3, kind=0, details='サンプル', quantity=10, shipping=500),
        ]

    def test_values_and_positions_unchanged(self):
        blob, headers = _export(entries=self.entries)
        self.assertIn("filename*=UTF-8''", headers['Content-Disposition'])
        self.assertIn('%E5%90%84%E6%94%AF%E5%BA%97', headers['Content-Disposition'])
        wb = load_workbook(BytesIO(blob))
        self.assertEqual(wb.sheetnames, ['石狩支店', '千歳支店', '東支店', '函館支店', '帯広支店', '西支店'])
        ws = wb['石狩支店']
        self.assertEqual(ws['B2'].value, '石狩')
        self.assertEqual(ws['C2'].value, '支店')
        self.assertEqual(ws['D2'].value, 'ラベル発行')
        self.assertEqual(ws['F2'].value, '2026年')
        self.assertEqual(ws['G2'].value, 11)
        self.assertEqual(ws['H2'].value, '月分')
        self.assertEqual([ws.cell(3, c).value for c in range(2, 10)],
                         ['日', '曜日', '内訳', 'サイズ', '枚数', '単価', '送料', '合計'])
        self.assertEqual(ws['B4'].value, 1)
        self.assertEqual(ws['C4'].value, '日')  # 2026-11-01 日曜
        self.assertEqual(ws['D4'].value, '店舗A')
        self.assertEqual(ws['E4'].value, '小')
        self.assertEqual(ws['E5'].value, '小貼付け')
        self.assertEqual(ws['F4'].value, 12)
        self.assertEqual(ws['G4'].value, 30)
        self.assertEqual(ws['G5'].value, 60)
        self.assertEqual(ws['H4'].value, 800)
        self.assertEqual(ws['I4'].value, '=+F4*G4+H4')
        self.assertEqual(ws['B66'].value, '合計')
        self.assertTrue(str(ws['F66'].value).startswith('=+F4+F6'))
        self.assertEqual(ws['H66'].value, '=SUM(H4:H65)')
        self.assertEqual(ws['I66'].value, '=SUM(I4:I65)')
        self.assertEqual(ws['F67'].value, '=SUM(F4:F65)-F66')
        self.assertTrue(ws.row_dimensions[64].hidden)  # 11月は31日なし
        self.assertFalse(ws.row_dimensions[62].hidden)
        # 千歳のサイズ表示（テンプレの「大」や個別セル）は維持
        chitose = wb['千歳支店']
        self.assertEqual(chitose['B2'].value, '千歳')
        self.assertEqual(chitose['E67'].value, '大')
        self.assertEqual(chitose['E21'].value, 'カラー')
        self.assertEqual(chitose['F4'].value, None)
        self.assertEqual(chitose['D4'].value, None)
        self.assertEqual(chitose['D8'].value, 'サンプル')
        self.assertEqual(chitose['F8'].value, 10)
        wb.close()

    def test_ishikari_scope_keeps_only_one_sheet(self):
        blob, headers = _export(entries=self.entries, scope='ishikari')
        self.assertIn('石狩支店ラベル集計_2026.11.xlsx', unquote(headers['Content-Disposition']))
        wb = load_workbook(BytesIO(blob))
        self.assertEqual(wb.sheetnames, ['石狩支店'])
        self.assertEqual(wb['石狩支店']['F4'].value, 12)
        wb.close()

    def test_borders_stay_inside_table_and_gridlines_off(self):
        blob, _ = _export(entries=self.entries)
        wb = load_workbook(BytesIO(blob))
        ws = wb['石狩支店']
        self.assertFalse(ws.sheet_view.showGridLines)
        self.assertEqual(ws.freeze_panes, 'A4')
        self.assertIn('$B$2:$I$67', ws.print_area or '')
        outside = []
        for row in range(1, 80):
            for col in range(1, 12):
                if 2 <= col <= 9 and 3 <= row <= 67:
                    continue
                cell = ws.cell(row, col)
                if cell.border and any(s and s.style for s in (cell.border.left, cell.border.right, cell.border.top, cell.border.bottom)):
                    outside.append(f'{get_column_letter(col)}{row}')
        self.assertEqual(outside, [])
        # 表の四隅には罫線がある
        self.assertEqual(ws['B3'].border.left.style, 'medium')
        self.assertEqual(ws['I3'].border.right.style, 'medium')
        self.assertEqual(ws['B67'].border.bottom.style, 'medium')
        self.assertEqual(ws['I67'].border.right.style, 'medium')
        self.assertEqual(ws['B66'].border.top.style, 'double')
        self.assertEqual(ws['F4'].number_format, '#,##0')
        self.assertEqual(ws['I66'].number_format, '#,##0')
        self.assertEqual(ws['F4'].alignment.horizontal, 'right')
        wb.close()
        # グリッド非表示はシートXMLでも確認
        with ZipFile(BytesIO(blob)) as z:
            sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        view = sheet.find(NS + 'sheetViews').find(NS + 'sheetView')
        self.assertEqual(view.get('showGridLines'), '0')


if __name__ == '__main__':
    unittest.main()
