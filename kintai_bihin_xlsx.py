"""備品棚卸の保存済みデータを .xlsx に書き出す（依存ライブラリなし。picking_xlsx / shipping と同じ方式）。

読み取り専用: kintai_bihin_sheets を SELECT するだけで、DB には一切書き込まない。
金額は kintai_bihin._enrich_row（画面・保存と同じ round half up）の結果をそのまま使う。
"""
from __future__ import annotations

from io import BytesIO
from urllib.parse import quote
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

import kintai_bihin

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'

GROUPS = [
    ('opening', '月初在庫', 'openingQty', 'openingAmount'),
    ('purchase', '当月購入', 'purchaseQty', 'purchaseAmount'),
    ('closing', '月末在庫', 'closingQty', 'closingAmount'),
    ('usage', '当月使用', 'usageQty', 'usageAmount'),
]
BASE_HEADERS = ['科目', '品名', '単位', '単価']
NCOLS = len(BASE_HEADERS) + 2 * len(GROUPS)  # 12
HEADER_ROW = 3  # 見出し開始（2段: 3-4）
DATA_ROW = 5

# 罫線は表（見出し〜金額合計、A〜当月使用金額）だけ。外側には付けない。
_LINE = 'FF555555'
_BORDER_SPECS = {
    'none': {},
    'thin': dict(left='thin', right='thin', top='thin', bottom='thin'),
    'tl': dict(left='medium', right='thin', top='medium', bottom='thin'),
    't': dict(left='thin', right='thin', top='medium', bottom='thin'),
    'tr': dict(left='thin', right='medium', top='medium', bottom='thin'),
    'l': dict(left='medium', right='thin', top='thin', bottom='thin'),
    'r': dict(left='thin', right='medium', top='thin', bottom='thin'),
    'total': dict(left='thin', right='thin', top='double', bottom='medium'),
    'total_l': dict(left='medium', right='thin', top='double', bottom='medium'),
    'total_r': dict(left='thin', right='medium', top='double', bottom='medium'),
}


def _col(i: int) -> str:
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _border_xml(sides: dict) -> str:
    parts = []
    for name in ('left', 'right', 'top', 'bottom'):
        style = sides.get(name)
        if style:
            parts.append(f'<{name} style="{style}"><color rgb="{_LINE}"/></{name}>')
        else:
            parts.append(f'<{name}/>')
    return '<border>' + ''.join(parts) + '</border>'


def _table_border(col: int, row: int, last_row: int) -> str:
    """表内セルの罫線キー。外周は中太、金額合計の上は二重。"""
    last_col = NCOLS - 1
    left, right = col == 0, col == last_col
    if row == last_row:
        return 'total_l' if left else ('total_r' if right else 'total')
    if row == HEADER_ROW:
        return 'tl' if left else ('tr' if right else 't')
    return 'l' if left else ('r' if right else 'thin')


def _xf(num_fmt=0, font=0, fill=0, border=0, align=None, apply_num=False) -> str:
    attrs = [
        f'numFmtId="{num_fmt}"', f'fontId="{font}"', f'fillId="{fill}"',
        f'borderId="{border}"', 'xfId="0"',
    ]
    if apply_num:
        attrs.append('applyNumberFormat="1"')
    if font:
        attrs.append('applyFont="1"')
    if fill:
        attrs.append('applyFill="1"')
    if border:
        attrs.append('applyBorder="1"')
    if align:
        attrs.append('applyAlignment="1"')
        hor, ver, wrap = align
        wrap_attr = ' wrapText="1"' if wrap else ''
        body = f'<alignment horizontal="{hor}" vertical="{ver}"{wrap_attr}/>'
        return f'<xf {" ".join(attrs)}>{body}</xf>'
    return f'<xf {" ".join(attrs)}/>'


def _cell(col: int, row: int, value, style: int) -> str:
    ref = f'{_col(col)}{row}'
    if value is None or value == '':
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = int(value) if float(value).is_integer() else repr(float(value))
        return f'<c r="{ref}" s="{style}"><v>{num}</v></c>'
    text = escape(str(value)).replace('\n', '&#10;')
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _num(v):
    """_dec_to_json の値（int/float/文字列）を数値へ。空は None（0 にしない）。"""
    if v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return v
    f = float(str(v))
    return int(f) if f.is_integer() else f


def _is_int(v) -> bool:
    return v is not None and v != '' and isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer()


def load_saved(c, year, month) -> dict | None:
    """保存済みの月だけを返す。未保存なら None。DB は読むだけ。"""
    y, m = kintai_bihin._parse_year_month(year, month)
    db_row = kintai_bihin._load_sheet_row(c, y, m)
    if not db_row:
        return None
    raw_rows = kintai_bihin._parse_rows_json(db_row['rows_json'])
    rows = [kintai_bihin._enrich_row({**r, 'sortOrder': i}) for i, r in enumerate(raw_rows) if isinstance(r, dict)]
    return {
        'year': y, 'month': m,
        'version': int(db_row['version']),
        'updatedAt': db_row['updated_at'],
        'updatedBy': db_row['updated_by'] or '',
        'rows': rows,
        'totals': kintai_bihin._totals(rows),
    }


def filename(year: int, month: int) -> str:
    return f'備品棚卸_{year:04d}-{month:02d}.xlsx'


def content_disposition(name: str) -> str:
    ascii_name = name.encode('ascii', 'replace').decode('ascii').replace('?', '_').replace('"', '_')
    if ascii_name.startswith('_'):
        ascii_name = 'bihin' + ascii_name.lstrip('_')
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name, safe="")}'


def _saved_label(at, by) -> str:
    text = str(at or '').replace('T', ' ')
    return f'最終保存：{text}' + (f'（{by}）' if by else '')


def _build_styles():
    """styles.xml と、用途別 style 番号を返す。"""
    keys = list(_BORDER_SPECS)
    border_id = {k: i for i, k in enumerate(keys)}
    borders_xml = ''.join(_border_xml(_BORDER_SPECS[k]) for k in keys)

    # cellXfs: 0 title, 1 plain, then head/text/int/dec/total_label/total_int × 罫線
    xfs = [
        _xf(font=2, align=('left', 'center', False)),  # title
        _xf(font=0, align=('left', 'center', False)),  # plain
    ]
    kinds = ('head', 'text', 'int', 'dec', 'total_label', 'total_int')
    index = {k: {} for k in kinds}
    for bkey, bid in border_id.items():
        if bkey == 'none':
            continue
        index['head'][bkey] = len(xfs)
        xfs.append(_xf(font=1, fill=2, border=bid, align=('center', 'center', True)))
        index['text'][bkey] = len(xfs)
        xfs.append(_xf(font=0, border=bid, align=('left', 'center', False)))
        index['int'][bkey] = len(xfs)
        xfs.append(_xf(num_fmt=3, font=0, border=bid, apply_num=True, align=('right', 'center', False)))
        index['dec'][bkey] = len(xfs)
        xfs.append(_xf(num_fmt=164, font=0, border=bid, apply_num=True, align=('right', 'center', False)))
        index['total_label'][bkey] = len(xfs)
        xfs.append(_xf(font=1, fill=3, border=bid, align=('left', 'center', False)))
        index['total_int'][bkey] = len(xfs)
        xfs.append(_xf(num_fmt=3, font=1, fill=3, border=bid, apply_num=True, align=('right', 'center', False)))

    styles = (
        f'<styleSheet xmlns="{NS}">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.##"/></numFmts>'
        '<fonts count="3">'
        '<font><sz val="10"/><name val="Yu Gothic"/><color rgb="FF222222"/></font>'
        '<font><b/><sz val="10"/><name val="Yu Gothic"/><color rgb="FF222222"/></font>'
        '<font><b/><sz val="16"/><name val="Yu Gothic"/><color rgb="FF1A1A1A"/></font>'
        '</fonts>'
        '<fills count="4"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD6E3F0"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFEEF3F6"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        f'<borders count="{len(keys)}">{borders_xml}</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )
    return styles, 0, 1, index


def workbook(data: dict) -> bytes:
    y, m = data['year'], data['month']
    styles, s_title, s_plain, index = _build_styles()
    last_row = DATA_ROW + len(data['rows'])  # 金額合計行
    last_col = NCOLS - 1

    def style(kind: str, col: int, row: int) -> int:
        return index[kind][_table_border(col, row, last_row)]

    def num_style(col: int, row: int, value) -> int:
        return style('int' if (value is None or _is_int(value)) else 'dec', col, row)

    rows_xml = []
    rows_xml.append(f'<row r="1" ht="26" customHeight="1">{_cell(0, 1, f"備品棚卸　{y}年{m}月", s_title)}</row>')
    rows_xml.append(f'<row r="2" ht="18" customHeight="1">{_cell(0, 2, _saved_label(data.get("updatedAt"), data.get("updatedBy")), s_plain)}</row>')
    h1, h2 = [], []
    for i, t in enumerate(BASE_HEADERS):
        h1.append(_cell(i, 3, t, style('head', i, 3)))
        h2.append(_cell(i, 4, None, style('head', i, 4)))
    for gi, (_, label, _, _) in enumerate(GROUPS):
        c0 = len(BASE_HEADERS) + gi * 2
        h1 += [_cell(c0, 3, label, style('head', c0, 3)), _cell(c0 + 1, 3, None, style('head', c0 + 1, 3))]
        h2 += [_cell(c0, 4, '数量', style('head', c0, 4)), _cell(c0 + 1, 4, '金額', style('head', c0 + 1, 4))]
    rows_xml.append(f'<row r="3" ht="20" customHeight="1">{"".join(h1)}</row>')
    rows_xml.append(f'<row r="4" ht="18" customHeight="1">{"".join(h2)}</row>')
    merges = [f'A1:{_col(last_col)}1', f'A2:{_col(last_col)}2']
    merges += [f'{_col(i)}3:{_col(i)}4' for i in range(len(BASE_HEADERS))]
    merges += [f'{_col(len(BASE_HEADERS) + g * 2)}3:{_col(len(BASE_HEADERS) + g * 2 + 1)}3' for g in range(len(GROUPS))]

    rn = DATA_ROW
    for r in data['rows']:
        cells = [
            _cell(0, rn, r.get('category') or None, style('text', 0, rn)),
            _cell(1, rn, r.get('name') or None, style('text', 1, rn)),
            _cell(2, rn, r.get('unit') or None, style('text', 2, rn)),
            _cell(3, rn, _num(r.get('unitPrice')), num_style(3, rn, _num(r.get('unitPrice')))),
        ]
        for gi, (_, _, qk, ak) in enumerate(GROUPS):
            c0 = len(BASE_HEADERS) + gi * 2
            qty, amt = _num(r.get(qk)), _num(r.get(ak))
            cells.append(_cell(c0, rn, qty, num_style(c0, rn, qty)))
            cells.append(_cell(c0 + 1, rn, amt, num_style(c0 + 1, rn, amt)))
        rows_xml.append(f'<row r="{rn}" ht="20" customHeight="1">{"".join(cells)}</row>')
        rn += 1

    totals = data.get('totals') or {}
    tcells = [_cell(0, rn, '金額合計', style('total_label', 0, rn))]
    for i in (1, 2, 3):
        tcells.append(_cell(i, rn, None, style('total_label', i, rn)))
    for gi, (_, _, _, ak) in enumerate(GROUPS):
        c0 = len(BASE_HEADERS) + gi * 2
        tcells.append(_cell(c0, rn, None, style('total_label', c0, rn)))
        tcells.append(_cell(c0 + 1, rn, totals.get(ak), style('total_int', c0 + 1, rn)))
    rows_xml.append(f'<row r="{rn}" ht="22" customHeight="1">{"".join(tcells)}</row>')
    merges.append(f'A{rn}:D{rn}')

    # 科目・品名（広め）・単位・単価、数量・金額 × 4
    widths = [12, 34, 8, 12] + [10, 13] * len(GROUPS)
    cols = ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1))
    area = f'A1:{_col(last_col)}{last_row}'
    sheet_name = f'{y}年{m}月'
    sheet = (
        f'<worksheet xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        f'<dimension ref="{area}"/>'
        '<sheetViews><sheetView workbookViewId="0" showGridLines="0">'
        '<pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="20" defaultColWidth="10"/>'
        f'<cols>{cols}</cols><sheetData>{"".join(rows_xml)}</sheetData>'
        f'<mergeCells count="{len(merges)}">' + ''.join(f'<mergeCell ref="{x}"/>' for x in merges) + '</mergeCells>'
        f'<printOptions horizontalCentered="1"/><pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>'
        f'<pageSetup orientation="landscape" paperSize="9" fitToWidth="1" fitToHeight="0"/>'
        '</worksheet>'
    )
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', (
            f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets>'
            '<definedNames>'
            f'<definedName name="_xlnm.Print_Titles" localSheetId="0">\'{sheet_name}\'!$3:$4</definedName>'
            f'<definedName name="_xlnm.Print_Area" localSheetId="0">\'{sheet_name}\'!$A$1:${_col(last_col)}${last_row}</definedName>'
            '</definedNames></workbook>'
        ))
        z.writestr('xl/styles.xml', styles)
        z.writestr('xl/worksheets/sheet1.xml', sheet)
    return out.getvalue()


def export(c, year, month):
    """(status, body, ctype[, headers]) を返す。未保存月は 404。"""
    data = load_saved(c, year, month)
    if data is None:
        return 404, {'error': 'この月はまだ保存されていないため、エクセル出力できません。先に保存してください。'}
    name = filename(data['year'], data['month'])
    return 200, workbook(data), XLSX, {'Content-Disposition': content_disposition(name)}
