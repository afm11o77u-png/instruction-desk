"""Generate the monthly Excel billing form for branch labels."""
import json
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


def _day_count(year, month):
    if not 1900 <= year <= 9999:
        raise ValueError('対象年月を確認してください。')
    return monthrange(year, month)[1]


def _sizes(branch):
    return (('小', 30), ('大' if branch == '千歳' else '小貼付け', 60))


def month_form(c, year, month):
    day_count = _day_count(year, month)
    saved = c.execute(
        'SELECT revision, entries FROM kintai_label_billing WHERE ym = ?',
        (f'{year:04d}-{month:02d}',),
    ).fetchone()
    return {
        'days': [WEEKDAYS[date(year, month, day).weekday()] for day in range(1, day_count + 1)],
        'branches': [{'name': branch, 'sizes': _sizes(branch)} for branch in BRANCHES],
        'revision': saved[0] if saved else 0,
        'entries': json.loads(saved[1]) if saved else [],
    }


def _validated_entries(entries, day_count):
    if not isinstance(entries, list) or len(entries) > len(BRANCHES) * day_count * 2:
        raise ValueError('ラベル集計の明細を確認してください。')
    result = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('ラベル集計の明細を確認してください。')
        branch, day, kind = (entry.get(key) for key in ('branch', 'day', 'kind'))
        key = (branch, day, kind)
        if (not isinstance(branch, str) or branch not in BRANCHES
                or type(day) is not int or not 1 <= day <= day_count
                or type(kind) is not int or kind not in (0, 1) or key in result):
            raise ValueError('明細の支店・日・サイズを確認してください。')
        details = entry.get('details', '')
        if not isinstance(details, str) or len(details) > 200 or any(ord(c) < 32 for c in details):
            raise ValueError('内訳は200文字以内で入力してください。')
        quantity, shipping = (entry.get(field) for field in ('quantity', 'shipping'))
        if any(n is not None and (type(n) is not int or not 0 <= n <= 999_999_999) for n in (quantity, shipping)):
            raise ValueError('枚数と送料は0以上、9桁以内の整数を入力してください。')
        result[key] = (details, quantity, shipping)
    return result


def save_month(c, year, month, revision, entries):
    if type(revision) is not int or revision < 0:
        raise ValueError('保存版を確認してください。')
    rows = _validated_entries(entries, _day_count(year, month))
    payload = [
        dict(branch=branch, day=day, kind=kind, details=details, quantity=quantity, shipping=shipping)
        for (branch, day, kind), (details, quantity, shipping) in rows.items()
    ]
    ym = f'{year:04d}-{month:02d}'
    stored = c.execute('SELECT revision FROM kintai_label_billing WHERE ym = ?', (ym,)).fetchone()
    if revision != (stored[0] if stored else 0):
        return 409, {'error': 'この月は別の画面で更新されました。編集内容はExcelに出力できます。再読み込みすると最新の保存内容が表示されます。'}
    c.execute('''INSERT INTO kintai_label_billing (ym, revision, entries) VALUES (?, ?, ?)
                 ON CONFLICT(ym) DO UPDATE SET revision=excluded.revision, entries=excluded.entries''',
              (ym, revision + 1, json.dumps(payload, ensure_ascii=False)))
    return 200, {'revision': revision + 1}


def export(year: int, month: int, entries=()):
    day_count = _day_count(year, month)
    rows = _validated_entries(entries, day_count) if entries != () else {}
    workbook = Workbook()
    workbook.remove(workbook.active)
    try:
        for branch in BRANCHES:
            sheet = workbook.create_sheet(f'{branch}支店')
            _fill_branch(sheet, branch, year, month, day_count, rows)
        with BytesIO() as out:
            workbook.save(out)
            filename = quote(f'各支店ラベル集計_{year}.{month:02d}.xlsx')
            return 200, out.getvalue(), XLSX, {
                'Content-Disposition': f"attachment; filename*=UTF-8''{filename}",
            }
    finally:
        workbook.close()


def _fill_branch(sheet, branch: str, year: int, month: int, day_count: int, entries):
    sizes = _sizes(branch)
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
        for kind, (size, price) in enumerate(sizes):
            row = upper + kind
            details, quantity, shipping = entries.get((branch, day, kind), ('', None, None))
            if details:
                cell = sheet.cell(row, 4, details)
                cell.data_type = 's'
            sheet.cell(row, 5, size)
            sheet.cell(row, 6, quantity)
            sheet.cell(row, 7, price)
            sheet.cell(row, 8, shipping)
            sheet.cell(row, 9, f'=F{row}*G{row}+H{row}')

    sheet.cell(total, 2, '合計')
    for row, (size, _) in enumerate(sizes, start=total):
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
