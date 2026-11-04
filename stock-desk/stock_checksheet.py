"""棚卸チェックシート（Excel）の出力と取り込み。書式は棚卸チェックシートの雛形に合わせる。
行の対応は非表示のL列（行ID）と非表示シート「_meta」（年月）で取る。"""
import base64
import io
import json
import re

import stock
import stock_stocktake as st

FONT = 'ＭＳ Ｐゴシック'
META = '_meta'


def sheet_title(name, used):
    # 顧客名そのまま。Excelで使えない文字だけ除き31文字まで。重複時のみ (2) を付ける。
    s = re.sub(r'[\[\]:*?/\\]', '', name or '').strip()[:31] or '顧客'
    base, n = s, 2
    while s in used or s == META:
        suf = f'({n})'
        s = base[:31 - len(suf)] + suf
        n += 1
    used.add(s)
    return s


def export_xlsx(c, month):
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    sheet = st.get(c, month)
    if not sheet['id'] or not sheet['blocks']:
        raise ValueError('棚卸表に顧客がありません。')
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill('solid', fgColor='D9D9D9')
    center = Alignment(horizontal='center', vertical='center')
    vcenter = Alignment(vertical='center')
    wrap = Alignment(vertical='center', wrap_text=True)
    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    for b in sheet['blocks']:
        ws = wb.create_sheet(sheet_title(b['customerName'], used))
        k = b['checkCols']
        head = ['仕入先', '商品名', '商品コード', 'サイズ', '在庫数'] + [''] * k + ['計']
        tc, idc = 6 + k, 7 + k
        ws.append(head)
        for i, cell in enumerate(ws[1], 1):
            cell.font = Font(name=FONT, size=14 if i == tc else 11, bold=bool(head[i - 1]))
            cell.fill = head_fill
            cell.alignment = center
            cell.border = border
        ws.cell(1, idc, 'id')
        rows = [r for r in sheet['rows'] if r['blockId'] == b['id']]
        for n, r in enumerate(rows, 2):
            size = r['size'] + (f"（{r['conditionLabel']}）" if r['condition'] != 'new' else '')
            vals = [r['supplierName'], r['productName'], r['productCode'], size, r['bookQty'], *r['checks'], r['checkTotal']]
            for i, v in enumerate(vals, 1):
                cell = ws.cell(n, i, v)
                cell.font = Font(name=FONT, size=11 if i == 1 or i > 5 else 12, bold=i <= 5 or i == tc)
                cell.alignment = wrap if i == 1 else vcenter
                cell.border = border
            last = get_column_letter(5 + k)
            ws.cell(n, tc).value = f'=IF(COUNT(F{n}:{last}{n})=0,"",SUM(F{n}:{last}{n}))'
            ws.cell(n, idc, r['id'])
            ws.row_dimensions[n].height = 30
        ws.row_dimensions[1].height = 30
        widths = [b['supplierWidth'], *st.FIXED_WIDTHS.values()] + [st.INPUT_WIDTH] * (k + 1)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.column_dimensions[get_column_letter(idc)].hidden = True
        ws.sheet_format.defaultRowHeight = 30
        ws.print_title_rows = '1:1'
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = 9
        m = ws.page_margins
        m.left, m.right, m.top, m.bottom, m.header, m.footer = 0.7086614173228347, 0.31496062992125984, 0.5511811023622047, 0.35433070866141736, 0.31496062992125984, 0.31496062992125984
        ws.print_area = f'A1:{get_column_letter(tc)}{max(1, len(rows) + 1)}'
        ws.sheet_properties.pageSetUpPr.fitToPage = False
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
        head = [v for v in next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())]
        if '計' not in head or 'id' not in head:
            continue
        tc, idc = head.index('計'), head.index('id')
        for n, line in enumerate(ws.iter_rows(min_row=2, max_col=idc + 1, values_only=True), 2):
            line = list(line) + [None] * (idc + 1 - len(line))
            rid = line[idc]
            raw = [_cell_value(v) for v in line[5:tc]]
            if rid is None and all(v is None for v in line[:tc]):
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
            if checks == st.parse_checks(r['checks']):
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
