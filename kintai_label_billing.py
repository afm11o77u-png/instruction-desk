"""Generate the monthly Excel billing form for branch labels."""
import json
from calendar import monthrange
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.properties import PageSetupProperties


XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
BRANCHES = ('石狩', '千歳', '東', '函館', '帯広', '西')
WEEKDAYS = '月火水木金土日'
TEMPLATE = Path(__file__).resolve().parent / 'assets' / 'branch-label-billing-template.xlsx'


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
        if not isinstance(details, str) or len(details) > 403 or any(ord(c) < 32 for c in details):
            raise ValueError('内訳は403文字以内で入力してください。')
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


def export(year: int, month: int, entries=(), *, scope='all'):
    if scope not in ('all', 'ishikari'):
        raise ValueError('Excelの出力範囲を確認してください。')
    day_count = _day_count(year, month)
    rows = _validated_entries(entries, day_count) if entries != () else {}
    workbook = load_workbook(TEMPLATE)
    try:
        title = '各支店ラベル集計'
        if scope == 'ishikari':
            for branch in BRANCHES[1:]:
                workbook.remove(workbook[f'{branch}支店'])
            title = '石狩支店ラベル集計'
        for branch in BRANCHES:
            if f'{branch}支店' in workbook:
                _fill_branch(workbook[f'{branch}支店'], branch, year, month, day_count, rows)
        with BytesIO() as out:
            workbook.save(out)
            filename = quote(f'{title}_{year}.{month:02d}.xlsx')
            return 200, out.getvalue(), XLSX, {
                'Content-Disposition': f"attachment; filename*=UTF-8''{filename}",
            }
    finally:
        workbook.close()



# 書式のみ。マスの位置・数式・シート名は変えない。
_FONT = 'Yu Gothic'
_LINE = 'FF555555'
_FILL_HEADER = PatternFill('solid', fgColor='FFD6E3F0')
_FILL_TOTAL = PatternFill('solid', fgColor='FFEEF3F6')
_FILL_WEEKEND = PatternFill('solid', fgColor='FFF3F6FA')
_FILL_EDIT = PatternFill('solid', fgColor='FFF2A6')
_THIN = Side(style='thin', color=_LINE)
_MEDIUM = Side(style='medium', color=_LINE)
_DOTTED = Side(style='dotted', color=_LINE)
_DOUBLE = Side(style='double', color=_LINE)
_CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
_LEFT = Alignment(horizontal='left', vertical='center', wrap_text=True)
_RIGHT = Alignment(horizontal='right', vertical='center')
_NUM = '#,##0'
_TABLE_FIRST = 3
_TABLE_LAST = 67
_TABLE_COLS = range(2, 10)  # B-I


def _border(left=None, right=None, top=None, bottom=None):
    return Border(left=left, right=right, top=top, bottom=bottom)


def _font(size=11, bold=False):
    return Font(name=_FONT, size=size, bold=bold, color='FF222222')


def _edge(col, left=_THIN, right=_THIN, top=None, bottom=None):
    """左右は表の外周だけ中太。"""
    return _border(
        left=_MEDIUM if col == 2 else left,
        right=_MEDIUM if col == 9 else right,
        top=top,
        bottom=bottom,
    )


def _style_branch(sheet, year: int, month: int, day_count: int):
    """見出し〜合計の表だけに罫線を付け、印刷とウィンドウ枠を整える。"""
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = 'A4'
    sheet.print_area = 'B2:I67'
    sheet.print_title_rows = '2:3'
    sheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    sheet.page_setup.orientation = 'portrait'
    sheet.page_setup.paperSize = 9
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.page_margins.left = 0.5
    sheet.page_margins.right = 0.5
    sheet.page_margins.top = 0.6
    sheet.page_margins.bottom = 0.6
    sheet.page_margins.header = 0.3
    sheet.page_margins.footer = 0.3
    for col, width in {'A': 3, 'B': 6, 'C': 7, 'D': 36, 'E': 11, 'F': 10, 'G': 10, 'H': 10, 'I': 12}.items():
        sheet.column_dimensions[col].width = width
    sheet.row_dimensions[2].height = 26
    sheet.row_dimensions[3].height = 20

    # タイトル行（罫線なし）
    for col, size, align in ((2, 16, Alignment(horizontal='right', vertical='center')),
                             (3, 16, Alignment(horizontal='left', vertical='center')),
                             (4, 20, Alignment(horizontal='center', vertical='center')),
                             (6, 14, Alignment(horizontal='right', vertical='center')),
                             (7, 14, Alignment(horizontal='right', vertical='center')),
                             (8, 14, Alignment(horizontal='left', vertical='center'))):
        cell = sheet.cell(2, col)
        cell.font = _font(size, True)
        cell.alignment = align
        cell.border = Border()

    # 見出し
    for col in _TABLE_COLS:
        cell = sheet.cell(3, col)
        cell.font = _font(11, True)
        cell.alignment = _CENTER
        cell.fill = _FILL_HEADER
        cell.border = _edge(col, top=_MEDIUM, bottom=_MEDIUM)

    # 日ごとの2行
    for day in range(1, 32):
        upper = 4 + (day - 1) * 2
        lower = upper + 1
        weekend = False
        if day <= day_count:
            weekend = date(year, month, day).weekday() >= 5
        for row, top, bottom in ((upper, _MEDIUM, None), (lower, _DOTTED, _MEDIUM)):
            sheet.row_dimensions[row].height = 17
            for col in _TABLE_COLS:
                cell = sheet.cell(row, col)
                cell.font = _font(11, bold=(col in (2, 3, 5) or (col == 4 and row == upper)))
                if col in (6, 7, 8, 9):
                    cell.alignment = _RIGHT
                    cell.number_format = _NUM
                elif col == 4:
                    cell.alignment = _LEFT if row == upper else _CENTER
                else:
                    cell.alignment = _CENTER
                cell.border = _edge(col, top=top, bottom=bottom)
                # 入力欄の黄は維持。土日は日付・曜日などに薄い色。
                if col in (4, 6, 8) and day <= day_count:
                    cell.fill = _FILL_EDIT
                elif weekend and day <= day_count:
                    cell.fill = _FILL_WEEKEND
                elif cell.fill and cell.fill.fgColor and str(cell.fill.fgColor.rgb or '') in ('FFF2A6', '00FFF2A6'):
                    pass
                else:
                    cell.fill = PatternFill(fill_type=None)

    # 合計（66-67）
    for row, top, bottom in ((66, _DOUBLE, None), (67, _DOTTED, _MEDIUM)):
        sheet.row_dimensions[row].height = 18
        for col in _TABLE_COLS:
            cell = sheet.cell(row, col)
            cell.font = _font(11, True)
            cell.fill = _FILL_TOTAL
            if col in (6, 7, 8, 9):
                cell.alignment = _RIGHT
                cell.number_format = _NUM
            else:
                cell.alignment = _CENTER
            cell.border = _edge(col, top=top, bottom=bottom)

    # 表の外に残った罫線を消す（列A・J、1行目、68行目以降）
    empty = Border()
    for row in range(1, 80):
        for col in range(1, 12):
            if 2 <= col <= 9 and _TABLE_FIRST <= row <= _TABLE_LAST:
                continue
            if 2 <= col <= 8 and row == 2:
                sheet.cell(row, col).border = empty
                continue
            cell = sheet.cell(row, col)
            if cell.border and any(s and s.style for s in (cell.border.left, cell.border.right, cell.border.top, cell.border.bottom)):
                cell.border = empty


def _fill_branch(sheet, branch: str, year: int, month: int, day_count: int, entries):
    sheet["F2"] = f"{year}年"
    sheet["G2"] = month
    editable = PatternFill("solid", fgColor="FFF2A6")
    for day in range(1, 32):
        upper = 4 + (day - 1) * 2
        active = day <= day_count
        sheet.row_dimensions[upper].hidden = not active
        sheet.row_dimensions[upper + 1].hidden = not active
        sheet[f"B{upper}"].value = day if active else None
        sheet[f"C{upper}"].value = WEEKDAYS[date(year, month, day).weekday()] if active else None
        if not active:
            continue
        details = []
        for kind in (0, 1):
            row = upper + kind
            text, quantity, shipping = entries.get((branch, day, kind), ("", None, None))
            if text and text not in details:
                details.append(text)
            sheet.cell(row, 6, quantity).fill = editable
            sheet.cell(row, 8, shipping).fill = editable
        merged_details = sheet[f"D{upper}"]
        merged_details.fill = editable
        if details:
            merged_details.value = " / ".join(details)
            merged_details.data_type = "s"

    last = 3 + day_count * 2
    numbers = DataValidation(type="whole", operator="greaterThanOrEqual", formula1=0, allow_blank=True)
    numbers.showErrorMessage = True
    numbers.error = "0以上の整数を入力してください。"
    sheet.add_data_validation(numbers)
    numbers.add(f"F4:F{last}")
    numbers.add(f"H4:H{last}")
    _style_branch(sheet, year, month, day_count)
