"""Generate the monthly Excel billing form for branch labels."""
from calendar import monthrange
from datetime import date
from io import BytesIO
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation


XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
BRANCHES = ('石狩', '千歳', '東', '函館', '帯広', '西')
WEEKDAYS = '月火水木金土日'


def export(year: int, month: int):
    day_count = monthrange(year, month)[1]
    if not 1900 <= year <= 9999:
        raise ValueError('対象年月を確認してください。')
    workbook = Workbook()
    workbook.remove(workbook.active)
    try:
        for branch in BRANCHES:
            sheet = workbook.create_sheet(f'{branch}支店')
            _fill_branch(sheet, branch, year, month, day_count)
        with BytesIO() as out:
            workbook.save(out)
            filename = quote(f'各支店ラベル集計_{year}.{month:02d}.xlsx')
            return 200, out.getvalue(), XLSX, {
                'Content-Disposition': f"attachment; filename*=UTF-8''{filename}",
            }
    finally:
        workbook.close()


def _fill_branch(sheet, branch: str, year: int, month: int, day_count: int):
    second_size = '大' if branch == '千歳' else '小貼付け'
    for cell, value in {
        'B2': branch, 'C2': '支店', 'D2': 'ラベル発行',
        'F2': f'{year}年', 'G2': month, 'H2': '月分',
    }.items():
        sheet[cell] = value
        sheet[cell].font = Font(name='Yu Gothic', size=11, bold=True)
    for col, label in enumerate(('日', '曜日', '内訳', 'サイズ', '枚数', '単価', '送料', '合計'), start=2):
        sheet.cell(3, col, label)

    first = 4
    last = first + day_count * 2 - 1
    total = last + 1
    for day in range(1, day_count + 1):
        upper = first + (day - 1) * 2
        sheet.cell(upper, 2, day)
        sheet.cell(upper, 3, WEEKDAYS[date(year, month, day).weekday()])
        for row, size, price in ((upper, '小', 30), (upper + 1, second_size, 60)):
            sheet.cell(row, 5, size)
            sheet.cell(row, 7, price)
            sheet.cell(row, 9, f'=F{row}*G{row}+H{row}')

    sheet.cell(total, 2, '合計')
    for row, size in ((total, '小'), (total + 1, second_size)):
        sheet.cell(row, 5, size)
        sheet.cell(row, 6, f'=SUMIF(E{first}:E{last},E{row},F{first}:F{last})')
    sheet.cell(total, 8, f'=SUM(H{first}:H{last})')
    sheet.cell(total, 9, f'=SUM(I{first}:I{last})')

    editable = PatternFill('solid', fgColor='FFF2A6')
    heading = PatternFill('solid', fgColor='E7EBEF')
    border = Side(style='thin', color='888888')
    grid = Border(left=border, right=border, top=border, bottom=border)
    for cells in sheet.iter_rows(min_row=3, max_row=total + 1, min_col=2, max_col=9):
        for cell in cells:
            cell.border = grid
            cell.font = Font(name='Yu Gothic', size=10, bold=cell.row in (3, total, total + 1))
            cell.alignment = Alignment(vertical='center', wrap_text=cell.column == 4)
            if cell.row in (3, total, total + 1):
                cell.fill = heading
            elif cell.column in (4, 6, 8):
                cell.fill = editable
            if cell.row >= first and cell.column >= 6:
                cell.number_format = '#,##0'

    numbers = DataValidation(type='whole', operator='greaterThanOrEqual', formula1=0, allow_blank=True)
    numbers.showErrorMessage = True
    numbers.error = '0以上の整数を入力してください。'
    sheet.add_data_validation(numbers)
    numbers.add(f'F{first}:F{last}')
    numbers.add(f'H{first}:H{last}')
    for col, width in enumerate((9, 9, 36.5, 9, 9, 9, 9, 14.25), start=2):
        sheet.column_dimensions[get_column_letter(col)].width = width
    sheet.freeze_panes = 'D4'
    sheet.print_area = f'B2:I{total + 1}'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.orientation = sheet.ORIENTATION_PORTRAIT
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 1
