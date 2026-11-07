"""月別集計の画面を .xlsx に書き出す。

画面と同じ month_hour_summary の結果だけを使う。勤怠の計算は足さない。
DB も元の出勤簿も書き換えない。
"""
from __future__ import annotations

from io import BytesIO
from urllib.parse import quote
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import kintai

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
HEADERS = (
    '月',
    '普通勤務時間（時間:分）',
    '残業時間（時間:分）',
    '勤務時間計（時間:分）',
    '通常勤務給与（円）',
    '残業給与（円）',
    '給与計（円）',
)
S_TEXT, S_HEAD, S_TITLE, S_PLAIN, S_TOTAL = range(5)


def _col(i: int) -> str:
    s = ''
    n = i + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def excel_text(value) -> str:
    """数式として解釈される先頭文字を避ける。画面の文言は変えない。"""
    text = str(value if value is not None else '')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if text[:1] in ('=', '+', '-', '@', '\t'):
        return "'" + text
    return text


def _cell(col: int, row: int, value, style: int = S_TEXT) -> str:
    ref = f'{_col(col)}{row}'
    if value is None or value == '':
        return f'<c r="{ref}" s="{style}"/>'
    text = escape(excel_text(value)).replace('\n', '&#10;')
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def filename(summary: dict) -> str:
    months = summary.get('months') or []
    if months:
        start = f"{months[0]['year']:04d}-{months[0]['month']:02d}"
        end = f"{months[-1]['year']:04d}-{months[-1]['month']:02d}"
    else:
        year = int(summary.get('fiscalStartYear') or 0)
        start, end = f'{year:04d}-02', f'{year + 1:04d}-01'
    return f'月別集計_{start}_{end}.xlsx'


def content_disposition(name: str) -> str:
    ascii_name = 'kintai-month-summary.xlsx'
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name, safe="")}'


def workbook(summary: dict) -> bytes:
    label = summary.get('fiscalLabel') or ''
    rows_xml = [
        f'<row r="1" ht="24" customHeight="1">{_cell(0, 1, f"月別集計　{label}　全員の合計", S_TITLE)}</row>',
        f'<row r="2">{_cell(0, 2, "在職・退職を含む、取込済みの時間だけを足しています。未取込の月は空欄です。給与は空欄です。この出力では保存していません。", S_PLAIN)}</row>',
    ]
    head = ''.join(_cell(i, 3, name, S_HEAD) for i, name in enumerate(HEADERS))
    rows_xml.append(f'<row r="3">{head}</row>')
    rn = 4
    for month in summary.get('months') or []:
        cells = [
            _cell(0, rn, month.get('key') or ''),
            _cell(1, rn, month.get('ordinaryHm') or ''),
            _cell(2, rn, month.get('overtimeHm') or ''),
            _cell(3, rn, month.get('totalHm') or ''),
            _cell(4, rn, ''),
            _cell(5, rn, ''),
            _cell(6, rn, ''),
        ]
        rows_xml.append(f'<row r="{rn}">{"".join(cells)}</row>')
        rn += 1
    period = summary.get('period') or {}
    total_cells = [
        _cell(0, rn, '期間合計', S_TOTAL),
        _cell(1, rn, period.get('ordinaryHm') or '', S_TOTAL),
        _cell(2, rn, period.get('overtimeHm') or '', S_TOTAL),
        _cell(3, rn, period.get('totalHm') or '', S_TOTAL),
        _cell(4, rn, '', S_TOTAL),
        _cell(5, rn, '', S_TOTAL),
        _cell(6, rn, '', S_TOTAL),
    ]
    rows_xml.append(f'<row r="{rn}">{"".join(total_cells)}</row>')
    widths = (16, 26, 24, 26, 18, 16, 14)
    cols = ''.join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
        for i, w in enumerate(widths, 1)
    )
    sheet = (
        f'<worksheet xmlns="{NS}"><sheetViews><sheetView workbookViewId="0">'
        f'<pane ySplit="3" topLeftCell="A4" activePane="bottomLeft" state="frozen"/>'
        f'</sheetView></sheetViews><cols>{cols}</cols><sheetData>{"".join(rows_xml)}</sheetData>'
        '<pageSetup orientation="landscape" paperSize="9" fitToWidth="1" fitToHeight="0"/></worksheet>'
    )
    styles = (
        f'<styleSheet xmlns="{NS}">'
        '<fonts count="3"><font><sz val="11"/><name val="Yu Gothic"/></font>'
        '<font><b/><sz val="11"/><name val="Yu Gothic"/></font>'
        '<font><b/><sz val="14"/><name val="Yu Gothic"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF0"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="2"><border/>'
        '<border><left style="thin"><color rgb="FF555555"/></left><right style="thin"><color rgb="FF555555"/></right>'
        '<top style="thin"><color rgb="FF555555"/></top><bottom style="thin"><color rgb="FF555555"/></bottom></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="5">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0"/>'
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    )
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as book:
        book.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        book.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        book.writestr('xl/workbook.xml', f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="月別集計" sheetId="1" r:id="rId1"/></sheets></workbook>')
        book.writestr('xl/styles.xml', styles)
        book.writestr('xl/worksheets/sheet1.xml', sheet)
    return out.getvalue()


def export(c, year):
    """画面と同じ集計を返す。保存はしない。"""
    if isinstance(year, str) and year.strip().isdigit():
        year = int(year.strip())
    summary = kintai.month_hour_summary(c, year)
    name = filename(summary)
    return 200, workbook(summary), XLSX, {'Content-Disposition': content_disposition(name)}
