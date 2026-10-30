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

# style ids (cellXfs の並び)
S_TEXT, S_HEAD, S_TITLE, S_PLAIN, S_INT, S_DEC, S_TOTAL_LABEL, S_TOTAL_INT = range(8)


def _col(i: int) -> str:
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(col: int, row: int, value, style: int = S_TEXT) -> str:
    ref = f'{_col(col)}{row}'
    if value is None or value == '':
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if style in (S_TEXT, S_INT, S_DEC):
            style = S_INT if float(value).is_integer() else S_DEC
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


def workbook(data: dict) -> bytes:
    y, m = data['year'], data['month']
    rows_xml = []
    rows_xml.append(f'<row r="1" ht="24" customHeight="1">{_cell(0, 1, f"備品棚卸　{y}年{m}月", S_TITLE)}</row>')
    rows_xml.append(f'<row r="2">{_cell(0, 2, _saved_label(data.get("updatedAt"), data.get("updatedBy")), S_PLAIN)}</row>')
    # 見出し（2段）
    h1 = [_cell(i, 3, t, S_HEAD) for i, t in enumerate(BASE_HEADERS)]
    h2 = [_cell(i, 4, None, S_HEAD) for i in range(len(BASE_HEADERS))]
    for gi, (_, label, _, _) in enumerate(GROUPS):
        c0 = len(BASE_HEADERS) + gi * 2
        h1 += [_cell(c0, 3, label, S_HEAD), _cell(c0 + 1, 3, None, S_HEAD)]
        h2 += [_cell(c0, 4, '数量', S_HEAD), _cell(c0 + 1, 4, '金額', S_HEAD)]
    rows_xml.append(f'<row r="3">{"".join(h1)}</row>')
    rows_xml.append(f'<row r="4">{"".join(h2)}</row>')
    merges = [f'A1:{_col(NCOLS - 1)}1', f'A2:{_col(NCOLS - 1)}2']
    merges += [f'{_col(i)}3:{_col(i)}4' for i in range(len(BASE_HEADERS))]
    merges += [f'{_col(len(BASE_HEADERS) + g * 2)}3:{_col(len(BASE_HEADERS) + g * 2 + 1)}3' for g in range(len(GROUPS))]

    rn = 5
    for r in data['rows']:
        cells = [
            _cell(0, rn, r.get('category') or None),
            _cell(1, rn, r.get('name') or None),
            _cell(2, rn, r.get('unit') or None),
            _cell(3, rn, _num(r.get('unitPrice'))),
        ]
        for gi, (_, _, qk, ak) in enumerate(GROUPS):
            c0 = len(BASE_HEADERS) + gi * 2
            cells.append(_cell(c0, rn, _num(r.get(qk))))
            cells.append(_cell(c0 + 1, rn, _num(r.get(ak)), S_INT))
        rows_xml.append(f'<row r="{rn}">{"".join(cells)}</row>')
        rn += 1

    totals = data.get('totals') or {}
    tcells = [_cell(0, rn, '金額合計', S_TOTAL_LABEL)] + [_cell(i, rn, None, S_TOTAL_LABEL) for i in (1, 2, 3)]
    for gi, (_, _, _, ak) in enumerate(GROUPS):
        c0 = len(BASE_HEADERS) + gi * 2
        tcells.append(_cell(c0, rn, None, S_TOTAL_LABEL))
        tcells.append(_cell(c0 + 1, rn, totals.get(ak), S_TOTAL_INT))
    rows_xml.append(f'<row r="{rn}">{"".join(tcells)}</row>')
    merges.append(f'A{rn}:D{rn}')

    widths = [12, 26, 8, 10] + [9, 12] * len(GROUPS)
    cols = ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1))
    sheet = (
        f'<worksheet xmlns="{NS}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<cols>{cols}</cols><sheetData>{"".join(rows_xml)}</sheetData>'
        f'<mergeCells count="{len(merges)}">' + ''.join(f'<mergeCell ref="{x}"/>' for x in merges) + '</mergeCells>'
        '<pageSetup orientation="landscape" paperSize="9" fitToWidth="1" fitToHeight="0"/></worksheet>'
    )
    border = '<border><left style="thin"><color rgb="FF555555"/></left><right style="thin"><color rgb="FF555555"/></right><top style="thin"><color rgb="FF555555"/></top><bottom style="thin"><color rgb="FF555555"/></bottom></border>'
    styles = (
        f'<styleSheet xmlns="{NS}">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.###"/></numFmts>'
        '<fonts count="3"><font><sz val="10"/><name val="Yu Gothic"/></font><font><b/><sz val="10"/><name val="Yu Gothic"/></font><font><b/><sz val="14"/><name val="Yu Gothic"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF0"/><bgColor indexed="64"/></patternFill></fill></fills>'
        f'<borders count="2"><border/>{border}</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="8">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/>'  # S_TEXT
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'  # S_HEAD
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/>'  # S_TITLE
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'  # S_PLAIN
        '<xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/>'  # S_INT #,##0
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/>'  # S_DEC
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0"/>'  # S_TOTAL_LABEL
        '<xf numFmtId="3" fontId="1" fillId="2" borderId="1" xfId="0" applyNumberFormat="1"/>'  # S_TOTAL_INT
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    )
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{y}年{m}月" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm.Print_Titles" localSheetId="0">\'{y}年{m}月\'!$3:$4</definedName></definedNames></workbook>')
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
