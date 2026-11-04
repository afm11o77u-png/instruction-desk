"""棚卸チェックシート（Excel）の出力と取り込み。書式は棚卸チェックシートの雛形に合わせる。
行の対応は非表示のL列（行ID）と非表示シート「_meta」（年月）で取る。"""
import base64
import io
import json
import re

import stock
import stock_stocktake as st

HEAD = ['仕入先', '商品名', '商品コード', 'サイズ', '在庫数', '', '', '', '', '', '計']
WIDTHS = {'A': 14.125, 'B': 24.0, 'C': 21.125, 'D': 6.625, 'E': 6.625,
          'F': 10.625, 'G': 10.625, 'H': 10.625, 'I': 10.625, 'J': 10.625, 'K': 10.625}
FONT = 'ＭＳ Ｐゴシック'
META = '_meta'
ID_COL = 12  # L列（非表示）


def sheet_title(name, used):
    s = re.sub(r'株式会社|有限会社|合同会社|㈱|㈲|\(株\)|（株）|\(有\)|（有）', '', name or '').strip() or '顧客'
    s = re.sub(r'[\[\]:*?/\\]', '', s)[:31] or '顧客'
    base, n = s, 2
    while s in used or s == META:
        suf = f'({n})'
        s = base[:31 - len(suf)] + suf
        n += 1
    used.add(s)
    return s


def export_xlsx(c, month):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    sheet = st.get(c, month)
    if not sheet['id'] or not sheet['blocks']:
        raise ValueError('棚卸表に顧客がありません。')
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill('solid', fgColor='D9D9D9')
    center = Alignment(horizontal='center', vertical='center')
    vcenter = Alignment(vertical='center')
    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    for b in sheet['blocks']:
        ws = wb.create_sheet(sheet_title(b['customerName'], used))
        ws.append(HEAD)
        for i, cell in enumerate(ws[1], 1):
            cell.font = Font(name=FONT, size=14 if i == 11 else 11, bold=bool(HEAD[i - 1]))
            cell.fill = head_fill
            cell.alignment = center
            cell.border = border
        ws.cell(1, ID_COL, 'id')
        rows = [r for r in sheet['rows'] if r['blockId'] == b['id']]
        for n, r in enumerate(rows, 2):
            size = r['size'] + (f"（{r['conditionLabel']}）" if r['condition'] != 'new' else '')
            vals = [r['supplierName'], r['productName'], r['productCode'], size, r['bookQty'], *r['checks'], r['checkTotal']]
            for i, v in enumerate(vals, 1):
                cell = ws.cell(n, i, v)
                cell.font = Font(name=FONT, size=11 if i == 1 or i > 5 else 12, bold=i <= 5)
                cell.alignment = vcenter
                cell.border = border
            ws.cell(n, 11).value = f'=IF(COUNT(F{n}:J{n})=0,"",SUM(F{n}:J{n}))'
            ws.cell(n, ID_COL, r['id'])
            ws.row_dimensions[n].height = 30
        ws.row_dimensions[1].height = 30
        for k, w in WIDTHS.items():
            ws.column_dimensions[k].width = w
        ws.column_dimensions['L'].hidden = True
        ws.sheet_format.defaultRowHeight = 30
        ws.print_title_rows = '1:1'
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = 9
        m = ws.page_margins
        m.left, m.right, m.top, m.bottom, m.header, m.footer = 0.7086614173228347, 0.31496062992125984, 0.5511811023622047, 0.35433070866141736, 0.31496062992125984, 0.31496062992125984
        ws.print_area = f'A1:K{max(1, len(rows) + 1)}'
    meta = wb.create_sheet(META)
    meta['A1'], meta['B1'] = 'month', sheet['month']
    meta.sheet_state = 'hidden'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"棚卸チェックシート_{sheet['month']}.xlsx"


def _cell_value(v):
    if v is None or (isinstance(v, str) and v.strip() == ''):
        return None
    if isinstance(v, str):
        v = v.strip().translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    return v


def _read(c, body):
    from openpyxl import load_workbook
    month = st._month(body.get('month'))
    try:
        blob = base64.b64decode(body.get('fileBase64') or '', validate=True)
        wb = load_workbook(io.BytesIO(blob), data_only=True)
    except Exception:
        raise ValueError('Excelファイルを読めませんでした。')
    if META not in wb.sheetnames or wb[META]['B1'].value != month:
        raise ValueError(f'{month} の棚卸チェックシートではありません。')
    sheet = st.get(c, month)
    rows = {r['id']: r for r in sheet['rows']}
    blocks = {b['id']: b for b in sheet['blocks']}
    updates, unmatched, skipped, errors, unchanged = [], 0, 0, [], 0
    for ws in wb.worksheets:
        if ws.title == META:
            continue
        for n, line in enumerate(ws.iter_rows(min_row=2, max_col=ID_COL, values_only=True), 2):
            rid = line[ID_COL - 1] if len(line) >= ID_COL else None
            raw = [_cell_value(v) for v in line[5:10]]
            if rid is None and all(v is None for v in line[:10]):
                continue
            r = rows.get(rid) if isinstance(rid, int) else None
            if not r:
                unmatched += 1
                continue
            try:
                checks = st.parse_checks(raw)
            except ValueError as e:
                errors.append(f'{ws.title} {n}行目: {e}')
                continue
            if checks == r['checks']:
                unchanged += 1
                continue
            if blocks[r['blockId']]['applied']:
                skipped += 1
                continue
            updates.append({'id': rid, 'checks': checks, 'sheet': ws.title, 'line': n,
                            'productName': r['productName'], 'size': r['size'],
                            'before': r['checkTotal'], 'after': st.check_total(checks)})
    return month, {'month': month, 'updates': updates, 'updateCount': len(updates), 'unmatched': unmatched,
                   'skippedApplied': skipped, 'unchanged': unchanged, 'errors': errors}


def import_preview(c, body):
    stock._require_write(body)
    return _read(c, body)[1]


def import_apply(c, body):
    stock._require_write(body)
    if body.get('confirm') is not True:
        raise ValueError('取り込み内容を確認してください。')
    month, pv = _read(c, body)
    if pv['errors']:
        raise ValueError('取り込めない値があります。' + ' / '.join(pv['errors'][:3]))
    out = st.save(c, {**body, 'month': month, 'rows': [{'id': u['id'], 'checks': u['checks']} for u in pv['updates']]})
    return {**out, 'imported': pv['updateCount']}
