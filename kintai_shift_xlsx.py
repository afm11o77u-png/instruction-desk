"""表示中のシフト表を .xlsx に書き出す（依存ライブラリなし。備品棚卸と同じ方式）。

読むのはリクエストの表（画面と同じ並び・記号・勤務メモ）と、サーバ側の暦だけ。
月シート・勤務メモ・出勤簿・在籍マスタは参照も更新もしない。保存はしない。
セルは文字列（inlineStr）で、画像にはしない。先頭が数式になる文字も数式セルにはしない。
"""
from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from urllib.parse import quote
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import kintai_shift

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
MAX_PEOPLE = 400
LEGEND = (
    '空欄：出勤　×：休み　？：不明　ピ：ピッキング　バ：バーコード　貼：シール貼　'
    '時刻：退勤予定（「まで」は付けない）　　氏名の色：社員／契約社員／パート'
)
_TIME_MADE = re.compile(r'^(\d{1,2}:\d{2})\s*まで$')
_ILLEGAL_XML = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')

# 画面の色（style.css）。ARGB。空欄と×は土日祝の列色、記号セルは記号側の背景（画面と同じ優先）。
FILL_RGB = {
    'head': 'FFF3F7F3',
    'sat': 'FFE8F2FB',
    'sun': 'FFFDECEB',
    'memo': 'FFFAFCFA',
    'name': 'FFFAFCFA',
    'reg': 'FFE6F2EA',
    'con': 'FFEEF3F8',
    'part': 'FFF7F0E8',
    'shift': 'FFF3F7FB',
    'hist': 'FFFAF7F3',
    'unk': 'FFFAF6EA',
    'task': 'FFEEF7F1',
    'time': 'FFF3F8FF',
}
FONT_RGB = {
    'meta': 'FF3D5344',
    'head': 'FF163828',
    'sat': 'FF1A5A9A',
    'sun': 'FF9D3434',
    'off': 'FF333333',
    'unk': 'FF6A5A2A',
    'task': 'FF1F5C45',
    'time': 'FF1A4A7A',
    'wday': 'FF4A5D50',
}


def display_time_text(text: str) -> str:
    """画面と同じ。「15:00まで」は「15:00」。それ以外はそのまま。"""
    raw = str(text or '')
    matched = _TIME_MADE.match(raw)
    return matched.group(1) if matched else raw


def cell_display(cell) -> str | None:
    """画面の cellLabel と同じ文字。空欄（出勤）は None。"""
    if not isinstance(cell, dict):
        return None
    kind = cell.get('kind')
    text = str(cell.get('text') or '')
    if kind == 'off':
        return '×'
    if kind == 'unknown':
        return '？'
    if kind == 'task':
        return text if text in kintai_shift.TASK_TEXTS else None
    if kind == 'time':
        shown = display_time_text(text).strip()
        return shown or None
    return None


def clean_text(value, limit: int) -> str:
    text = _ILLEGAL_XML.sub('', str(value or ''))
    return text.replace('\r\n', '\n').replace('\r', '\n')[:limit]


def _label(payload: dict) -> str:
    pending = bool(payload.get('pending'))
    if pending:
        bits = []
        if payload.get('shiftPending'):
            bits.append('シフト')
        if payload.get('memoPending'):
            bits.append('勤務メモ')
        what = '・'.join(bits) if bits else '表示'
        return f'画面の未保存（{what}）を出力しています。データベースは更新していません。'
    if not payload.get('exists'):
        return '未保存の月の表示内容を出力しています。データベースは更新していません。'
    at = clean_text(payload.get('updatedAt'), 40).replace('\n', ' ').strip()
    by = clean_text(payload.get('updatedBy'), 80).replace('\n', ' ').strip()
    label = '保存済みの表示内容を出力しています。この操作では保存していません。'
    if at:
        label += f'　最終保存：{at}'
        if by:
            label += f'（{by}）'
    return label


def _memos(raw, keys: set[str]) -> dict[str, str]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        items = [{'key': k, 'memo': v} for k, v in raw.items()]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError('勤務メモの形式が不正です。')
    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or '').strip()
        if key not in keys:
            continue
        text = clean_text(item.get('memo'), kintai_shift.MEMO_MAX_LEN)
        if text:
            out[key] = text
    return out


def build_view(payload: dict) -> dict:
    """画面ペイロードを暦つきの表にする。DB は触らない。"""
    if not isinstance(payload, dict):
        raise ValueError('シフト表の形式が不正です。')
    year, month = kintai_shift._parse_year_month(payload.get('year'), payload.get('month'))
    days = kintai_shift.month_calendar(year, month)
    raw_people = payload.get('people')
    if raw_people is None:
        raw_people = []
    people = kintai_shift._normalize_people(raw_people)
    if len(people) > MAX_PEOPLE:
        raise ValueError('行数が多すぎます。')
    cells = kintai_shift._normalize_cells(payload.get('cells'), len(days))
    memos = _memos(payload.get('memos'), {p['key'] for p in people})
    holidays = [d for d in days if d.get('holidayName')]
    holiday_note = ''
    if holidays:
        holiday_note = '　　祝日：' + '、'.join(f"{d['day']}日 {d['holidayName']}" for d in holidays)
    return {
        'year': year,
        'month': month,
        'days': days,
        'people': people,
        'cells': cells,
        'memos': memos,
        'label': _label(payload) + holiday_note,
    }


def filename(year: int, month: int) -> str:
    return f'マークシフト表_{year:04d}-{month:02d}.xlsx'


def content_disposition(name: str) -> str:
    matched = re.search(r'(\d{4}-\d{2}\.xlsx)$', name)
    ascii_name = 'shift_' + (matched.group(1) if matched else 'export.xlsx')
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def _col(index: int) -> str:
    s = ''
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def excel_serial(day: date) -> int:
    """Excel の 1900 日付システムの通算日（1899-12-30 起点）。"""
    return (day - date(1899, 12, 30)).days


def _bucket(day: dict) -> str:
    kind = day.get('kind')
    if kind in ('sun', 'holiday'):
        return 'sun'
    if kind == 'sat':
        return 'sat'
    return 'wd'


def _name_style(person: dict) -> str:
    emp = person.get('employment')
    if emp == 'regular':
        return 'name_reg'
    if emp == 'contract':
        return 'name_con'
    if emp == 'part':
        return 'name_part'
    if person.get('source') == 'shift' or emp == 'shift':
        return 'name_shift'
    if person.get('source') == 'history':
        return 'name_hist'
    return 'name'


def _mark_style(kind: str, bucket: str) -> str:
    if kind == 'off':
        return {'wd': 'off', 'sat': 'off_sat', 'sun': 'off_sun'}[bucket]
    if kind in ('unknown', 'task', 'time'):
        # 画面では記号の背景が土日色より優先される
        return {'unknown': 'unk', 'task': 'task', 'time': 'time'}[kind]
    return {'wd': 'blank', 'sat': 'blank_sat', 'sun': 'blank_sun'}[bucket]


def _style_sheet() -> tuple[str, dict[str, int]]:
    fonts = [
        ('base', 10, False, None),
        ('bold', 10, True, None),
        ('title', 14, True, None),
        ('meta', 10, False, FONT_RGB['meta']),
        ('head', 10, True, FONT_RGB['head']),
        ('head_sat', 10, True, FONT_RGB['sat']),
        ('head_sun', 10, True, FONT_RGB['sun']),
        ('off', 10, True, FONT_RGB['off']),
        ('off_sun', 10, True, FONT_RGB['sun']),
        ('unk', 10, True, FONT_RGB['unk']),
        ('task', 10, True, FONT_RGB['task']),
        ('time', 9, True, FONT_RGB['time']),
        ('wday', 9, False, FONT_RGB['wday']),
        ('wday_sat', 9, True, FONT_RGB['sat']),
        ('wday_sun', 9, True, FONT_RGB['sun']),
    ]
    font_id = {name: i for i, (name, *_) in enumerate(fonts)}
    fills = [('none', None), ('gray', None)] + [(name, rgb) for name, rgb in FILL_RGB.items()]
    fill_id = {name: i for i, (name, _) in enumerate(fills)}
    # name, font, fill, border, numFmt, align, wrap, quote
    specs = [
        ('title', 'title', 'none', 0, 0, 'left', False, False),
        ('meta', 'meta', 'none', 0, 0, 'left', True, True),
        ('head', 'head', 'head', 1, 0, 'center', True, False),
        ('head_sat', 'head_sat', 'sat', 1, 0, 'center', True, False),
        ('head_sun', 'head_sun', 'sun', 1, 0, 'center', True, False),
        ('wday', 'wday', 'head', 1, 0, 'center', False, False),
        ('wday_sat', 'wday_sat', 'sat', 1, 0, 'center', False, False),
        ('wday_sun', 'wday_sun', 'sun', 1, 0, 'center', False, False),
        ('day', 'head', 'head', 1, 165, 'center', False, False),
        ('day_sat', 'head_sat', 'sat', 1, 165, 'center', False, False),
        ('day_sun', 'head_sun', 'sun', 1, 165, 'center', False, False),
        ('memo', 'base', 'memo', 1, 0, 'left', True, True),
        ('name', 'bold', 'name', 1, 0, 'left', False, True),
        ('name_reg', 'bold', 'reg', 1, 0, 'left', False, True),
        ('name_con', 'bold', 'con', 1, 0, 'left', False, True),
        ('name_part', 'bold', 'part', 1, 0, 'left', False, True),
        ('name_shift', 'bold', 'shift', 1, 0, 'left', False, True),
        ('name_hist', 'bold', 'hist', 1, 0, 'left', False, True),
        ('blank', 'base', 'none', 1, 0, 'center', False, False),
        ('blank_sat', 'base', 'sat', 1, 0, 'center', False, False),
        ('blank_sun', 'base', 'sun', 1, 0, 'center', False, False),
        ('off', 'off', 'none', 1, 0, 'center', False, True),
        ('off_sat', 'off', 'sat', 1, 0, 'center', False, True),
        ('off_sun', 'off_sun', 'sun', 1, 0, 'center', False, True),
        ('unk', 'unk', 'unk', 1, 0, 'center', False, True),
        ('task', 'task', 'task', 1, 0, 'center', False, True),
        ('time', 'time', 'time', 1, 0, 'center', False, True),
    ]
    index = {spec[0]: i for i, spec in enumerate(specs)}

    def font_xml(size, bold, color):
        weight = '<b/>' if bold else ''
        paint = f'<color rgb="{color}"/>' if color else ''
        return f'<font>{weight}<sz val="{size}"/>{paint}<name val="Yu Gothic"/></font>'

    def fill_xml(name, rgb):
        if name == 'none':
            return '<fill><patternFill patternType="none"/></fill>'
        if name == 'gray':
            return '<fill><patternFill patternType="gray125"/></fill>'
        return (
            '<fill><patternFill patternType="solid">'
            f'<fgColor rgb="{rgb}"/><bgColor indexed="64"/></patternFill></fill>'
        )

    def xf_xml(spec):
        _name, font, fill, border, num_fmt, align, wrap, quoted = spec
        quote_attr = ' quotePrefix="1"' if quoted else ''
        wrap_attr = ' wrapText="1"' if wrap else ''
        apply_num = ' applyNumberFormat="1"' if num_fmt else ''
        return (
            f'<xf numFmtId="{num_fmt}" fontId="{font_id[font]}" fillId="{fill_id[fill]}" '
            f'borderId="{border}" xfId="0" applyFont="1" applyFill="1" applyBorder="1" '
            f'applyAlignment="1"{apply_num}{quote_attr}>'
            f'<alignment horizontal="{align}" vertical="center"{wrap_attr}/></xf>'
        )

    border = (
        '<border><left style="thin"><color rgb="FF555555"/></left>'
        '<right style="thin"><color rgb="FF555555"/></right>'
        '<top style="thin"><color rgb="FF555555"/></top>'
        '<bottom style="thin"><color rgb="FF555555"/></bottom></border>'
    )
    xml = (
        f'<styleSheet xmlns="{NS}">'
        '<numFmts count="1"><numFmt numFmtId="165" formatCode="d"/></numFmts>'
        f'<fonts count="{len(fonts)}">' + ''.join(font_xml(s, b, c) for _, s, b, c in fonts) + '</fonts>'
        f'<fills count="{len(fills)}">' + ''.join(fill_xml(n, rgb) for n, rgb in fills) + '</fills>'
        f'<borders count="2"><border/>{border}</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(specs)}">' + ''.join(xf_xml(spec) for spec in specs) + '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )
    return xml, index


def _cell(col: int, row: int, value, style: int) -> str:
    ref = f'{_col(col)}{row}'
    if value is None or value == '':
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(clean_text(value, 800)).replace('\n', '&#10;')
    return (
        f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'
    )


def workbook(view: dict) -> bytes:
    year, month = view['year'], view['month']
    days = view['days']
    people = view['people']
    cells = view['cells']
    memos = view['memos']
    styles_xml, sid = _style_sheet()
    last_col = 1 + len(days)  # 0=メモ, 1=氏名, 以降=日
    last_letter = _col(last_col)
    rows = []
    rows.append(
        f'<row r="1" ht="24" customHeight="1">{_cell(0, 1, f"{year}年 {month}月 マーク シフト表", sid["title"])}</row>'
    )
    rows.append(f'<row r="2" ht="18" customHeight="1">{_cell(0, 2, LEGEND, sid["meta"])}</row>')
    rows.append(f'<row r="3" ht="36" customHeight="1">{_cell(0, 3, view["label"], sid["meta"])}</row>')
    head = [
        _cell(0, 4, '勤務メモ', sid['head']),
        _cell(1, 4, '氏名', sid['head']),
    ]
    sub = [
        _cell(0, 5, None, sid['head']),
        _cell(1, 5, None, sid['head']),
    ]
    for day in days:
        bucket = _bucket(day)
        head_style = {'wd': 'day', 'sat': 'day_sat', 'sun': 'day_sun'}[bucket]
        sub_style = {'wd': 'wday', 'sat': 'wday_sat', 'sun': 'wday_sun'}[bucket]
        col = day['day'] + 1
        serial = excel_serial(date(year, month, day['day']))
        head.append(_cell(col, 4, serial, sid[head_style]))
        sub.append(_cell(col, 5, day['weekdayLabel'], sid[sub_style]))
    rows.append(f'<row r="4" ht="20" customHeight="1">{"".join(head)}</row>')
    rows.append(f'<row r="5" ht="18" customHeight="1">{"".join(sub)}</row>')

    data_row = 6
    for offset, person in enumerate(people):
        rn = data_row + offset
        key = person['key']
        by_day = cells.get(key) or {}
        bucket_cells = [
            _cell(0, rn, memos.get(key) or None, sid['memo']),
            _cell(1, rn, person.get('displayName') or '（未入力）', sid[_name_style(person)]),
        ]
        for day in days:
            cell = by_day.get(str(day['day']))
            shown = cell_display(cell)
            kind = cell.get('kind') if isinstance(cell, dict) else ''
            bucket_cells.append(_cell(day['day'] + 1, rn, shown, sid[_mark_style(kind, _bucket(day))]))
        rows.append(f'<row r="{rn}" ht="20" customHeight="1">{"".join(bucket_cells)}</row>')
    if not people:
        rows.append(
            f'<row r="6" ht="20" customHeight="1">{_cell(0, 6, "行がありません", sid["meta"])}</row>'
        )
        last_row = 6
    else:
        last_row = data_row + len(people) - 1

    merges = [
        f'A1:{last_letter}1',
        f'A2:{last_letter}2',
        f'A3:{last_letter}3',
        'A4:A5',
        'B4:B5',
    ]
    widths = [18, 14] + [5.2] * len(days)
    cols = ''.join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1)
    )
    sheet_name = f'{year}年{month}月'
    sheet = (
        f'<worksheet xmlns="{NS}">'
        '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        '<dimension ref="A1:' + last_letter + str(last_row) + '"/>'
        '<sheetViews><sheetView workbookViewId="0" tabSelected="1">'
        f'<pane xSplit="2" ySplit="5" topLeftCell="C6" activePane="bottomRight" state="frozen"/>'
        '</sheetView></sheetViews>'
        f'<cols>{cols}</cols>'
        f'<sheetData>{"".join(rows)}</sheetData>'
        f'<mergeCells count="{len(merges)}">'
        + ''.join(f'<mergeCell ref="{ref}"/>' for ref in merges)
        + '</mergeCells>'
        '<pageMargins left="0.4" right="0.4" top="0.5" bottom="0.5" header="0.2" footer="0.3"/>'
        '<pageSetup orientation="landscape" paperSize="9" fitToWidth="1" fitToHeight="0"/>'
        '<headerFooter><oddFooter>&amp;C&amp;P / &amp;N</oddFooter></headerFooter>'
        '</worksheet>'
    )
    workbook_xml = (
        f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
        '<definedNames><definedName name="_xlnm.Print_Titles" localSheetId="0">'
        f"'{sheet_name}'!$4:$5</definedName></definedNames></workbook>"
    )
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as zipped:
        zipped.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        zipped.writestr(
            '_rels/.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        zipped.writestr(
            'xl/_rels/workbook.xml.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>',
        )
        zipped.writestr('xl/workbook.xml', workbook_xml)
        zipped.writestr('xl/styles.xml', styles_xml)
        zipped.writestr('xl/worksheets/sheet1.xml', sheet)
    return out.getvalue()


def export_view(payload: dict):
    """(status, body, ctype, headers)。DB 接続は受け取らない。"""
    view = build_view(payload or {})
    name = filename(view['year'], view['month'])
    return 200, workbook(view), XLSX, {'Content-Disposition': content_disposition(name)}
