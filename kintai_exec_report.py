"""幹部会議報告（月別集計＋ユニフォーム契約 新規分・入替分＋備品棚卸）の保存・Excel出力。"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import datetime
from io import BytesIO
from urllib.parse import quote
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

import kintai_bihin
import kintai_crimp

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'

DEFAULT_SLOTS = 6
MAX_SLOTS = 30
NCOLS = 12

GROUPS = [
    ('opening', '月初在庫', 'openingQty', 'openingAmount'),
    ('purchase', '当月購入', 'purchaseQty', 'purchaseAmount'),
    ('closing', '月末在庫', 'closingQty', 'closingAmount'),
    ('usage', '当月使用', 'usageQty', 'usageAmount'),
]
BASE_HEADERS = ['科目', '品名', '単位', '単価']

# style ids
S_TEXT, S_HEAD, S_TITLE, S_SECTION, S_PLAIN, S_INT, S_DEC, S_TOTAL_LABEL, S_TOTAL_INT, S_CENTER = range(10)


def ensure_schema(c) -> None:
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_exec_reports (
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            new_contracts_json TEXT NOT NULL DEFAULT '[]',
            renewal_contracts_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (year, month)
        )'''
    )


def _normalize_slots(raw_list, min_slots: int = DEFAULT_SLOTS) -> list[dict]:
    items = []
    if isinstance(raw_list, list):
        for entry in raw_list[:MAX_SLOTS]:
            if not isinstance(entry, dict):
                continue
            items.append({
                'customer': str(entry.get('customer') or '').strip()[:120],
                'period': str(entry.get('period') or '').strip()[:120],
                'note': str(entry.get('note') or '').strip()[:200],
            })
    while len(items) < min_slots:
        items.append({'customer': '', 'period': '', 'note': ''})
    return items


def load_contracts(c, year: int, month: int) -> dict:
    ensure_schema(c)
    row = c.execute(
        'SELECT * FROM kintai_exec_reports WHERE year=? AND month=?',
        (int(year), int(month)),
    ).fetchone()
    if not row:
        return {
            'year': int(year),
            'month': int(month),
            'newContracts': _normalize_slots([]),
            'renewalContracts': _normalize_slots([]),
            'updatedAt': '',
            'updatedBy': '',
        }
    try:
        new_c = json.loads(row['new_contracts_json'] or '[]')
    except json.JSONDecodeError:
        new_c = []
    try:
        ren_c = json.loads(row['renewal_contracts_json'] or '[]')
    except json.JSONDecodeError:
        ren_c = []
    slot_count = max(DEFAULT_SLOTS, len(new_c), len(ren_c))
    return {
        'year': int(year),
        'month': int(month),
        'newContracts': _normalize_slots(new_c, slot_count),
        'renewalContracts': _normalize_slots(ren_c, slot_count),
        'updatedAt': row['updated_at'] or '',
        'updatedBy': row['updated_by'] or '',
    }


def save_contracts(c, user, payload: dict) -> dict:
    ensure_schema(c)
    if not isinstance(payload, dict):
        raise ValueError('保存内容を確認してください。')
    y, m = kintai_bihin._parse_year_month(payload.get('year'), payload.get('month'))
    raw_new = payload.get('newContracts')
    raw_ren = payload.get('renewalContracts')
    slot_count = max(
        DEFAULT_SLOTS,
        len(raw_new) if isinstance(raw_new, list) else 0,
        len(raw_ren) if isinstance(raw_ren, list) else 0,
    )
    slot_count = min(slot_count, MAX_SLOTS)
    new_c = _normalize_slots(raw_new, slot_count)
    ren_c = _normalize_slots(raw_ren, slot_count)
    actor = ''
    if user:
        try:
            actor = str(user['name'] or user['login'] or '')[:80]
        except (KeyError, IndexError, TypeError):
            actor = ''
    now = datetime.now().isoformat(timespec='seconds')
    c.execute(
        '''INSERT INTO kintai_exec_reports(year, month, new_contracts_json, renewal_contracts_json, updated_at, updated_by)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(year, month) DO UPDATE SET
             new_contracts_json=excluded.new_contracts_json,
             renewal_contracts_json=excluded.renewal_contracts_json,
             updated_at=excluded.updated_at,
             updated_by=excluded.updated_by
        ''',
        (
            y,
            m,
            json.dumps(new_c, ensure_ascii=False),
            json.dumps(ren_c, ensure_ascii=False),
            now,
            actor,
        ),
    )
    return load_contracts(c, y, m)


def _resolve_fy(year: int, month: int, fiscal_start_year) -> int:
    if fiscal_start_year not in (None, '', 0, '0'):
        try:
            fy = int(fiscal_start_year)
            if 2000 <= fy <= 2100:
                return fy
        except (TypeError, ValueError):
            pass
    return int(year) if int(month) >= 2 else int(year) - 1


def bundle(c, year, month, fiscal_start_year, summary_fn) -> dict:
    y, m = kintai_bihin._parse_year_month(year, month)
    fy = _resolve_fy(y, m, fiscal_start_year)
    contracts = load_contracts(c, y, m)
    summary = _summary_through_month(summary_fn(c, fy), y, m)
    ym = f'{y:04d}-{m:02d}'
    crimp = kintai_crimp.list_month(c, ym)
    bihin_sheet = kintai_bihin.get_sheet(c, y, m)

    # 処理済み指示書枚数（報告対象月以前の月のみ。未来月は隠す）
    processed_orders = None
    try:
        import processed_stats
        p_stats = processed_stats.get_stats()
        cutoff_key = f"{y:04d}-{m:02d}"
        past_months = [
            mon for mon in p_stats.get("months", [])
            if mon.get("key", "") <= cutoff_key
        ]
        target_month = next((mon for mon in past_months if mon.get("key") == cutoff_key), None)
        processed_orders = {
            'target': target_month,
            'recentMonths': past_months[:6],
            'totalThroughTarget': sum(mon.get("total", 0) for mon in past_months),
        }
    except Exception:
        processed_orders = None

    return {
        **contracts,
        'fiscalStartYear': fy,
        'summary': summary,
        'bihin': bihin_sheet,
        'processedOrders': processed_orders,
        'crimp': {
            'rows': [{'day': row['day'], 'reading': row['reading'], 'diff': row['diff']} for row in crimp['rows']],
            'start': f'{ym}-01',
            'end': f'{ym}-{monthrange(y, m)[1]:02d}',
            'total': crimp['total'] if any(row['diff'] is not None for row in crimp['rows']) else None,
        },
    }


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
    if v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return v
    f = float(str(v))
    return int(f) if f.is_integer() else f


def _range_cells(row: int, start_col: int, end_col: int, first_val, style: int) -> list[str]:
    out = [_cell(start_col, row, first_val, style)]
    for col in range(start_col + 1, end_col + 1):
        out.append(_cell(col, row, None, style))
    return out



def _summary_through_month(summary: dict, year: int, month: int) -> dict:
    """Excelの月別集計と期間合計を報告対象月までに揃える。"""
    cutoff = (int(year), int(month))
    months = []
    for row in summary.get('months') or []:
        if row.get('year') is not None and row.get('month') is not None:
            ym = (int(row['year']), int(row['month']))
        else:
            ym = tuple(map(int, row['key'].replace('-', '/').split('/')))
        if ym <= cutoff:
            months.append(row)

    period = {}
    for field in ('ordinary', 'overtime', 'total'):
        values = []
        for row in months:
            value = row.get(field + 'Minutes')
            if value is None and row.get(field + 'Hm'):
                hours, minutes = map(int, row[field + 'Hm'].split(':'))
                value = hours * 60 + minutes
            if value is not None:
                values.append(value)
        total = sum(values) if values else None
        period[field + 'Minutes'] = total
        period[field + 'Hm'] = f'{total // 60}:{total % 60:02d}' if total is not None else ''

    label = summary.get('fiscalLabel') or ''
    if months:
        first = months[0]
        first_key = first.get('key') or f"{first['year']}/{int(first['month']):02d}"
        start_year, start_month = map(int, first_key.replace('-', '/').split('/'))
        label = f'{start_year}年{start_month}月〜{year}年{month}月'
    return {**summary, 'months': months, 'period': period, 'fiscalLabel': label}


def workbook(report: dict) -> bytes:
    y = report['year']
    m = report['month']
    summary = _summary_through_month(report.get('summary') or {}, y, m)
    bihin = report.get('bihin') or {}
    def filled_contracts(items):
        return [item for item in items or [] if any(str(item.get(key) or '').strip() for key in ('customer', 'period', 'note'))]

    new_contracts = filled_contracts(report.get('newContracts'))
    ren_contracts = filled_contracts(report.get('renewalContracts'))

    rows_xml = []
    merges = []
    rn = 1

    # タイトル
    rows_xml.append(
        f'<row r="{rn}" ht="26" customHeight="1">{_cell(0, rn, f"幹部会議報告書　{y}年{m}月度", S_TITLE)}</row>'
    )
    merges.append(f'A{rn}:{_col(NCOLS - 1)}{rn}')
    rn += 1

    # ==================== 1. 月別集計 ====================
    fiscal_label = summary.get('fiscalLabel') or f'{report.get("fiscalStartYear", y)}年2月〜'
    rows_xml.append(
        f'<row r="{rn}" ht="22" customHeight="1">{_cell(0, rn, f"1. 月別集計（{fiscal_label}　全員の合計）", S_SECTION)}</row>'
    )
    merges.append(f'A{rn}:{_col(NCOLS - 1)}{rn}')
    rn += 1

    # 月別集計ヘッダ (12列幅に合わせる: A:月, B-C:普通勤務時間, D-E:残業時間, F-G:勤務時間計, H-I:通常勤務給与, J:残業給与, K-L:給与計)
    sum_cols = [
        (0, 0, '月'),
        (1, 2, '普通勤務時間（時間:分）'),
        (3, 4, '残業時間（時間:分）'),
        (5, 6, '勤務時間計（時間:分）'),
        (7, 8, '通常勤務給与（円）'),
        (9, 9, '残業給与（円）'),
        (10, 11, '給与計（円）'),
    ]
    h_cells = []
    for c_start, c_end, label in sum_cols:
        h_cells.extend(_range_cells(rn, c_start, c_end, label, S_HEAD))
        if c_end > c_start:
            merges.append(f'{_col(c_start)}{rn}:{_col(c_end)}{rn}')
    rows_xml.append(f'<row r="{rn}" ht="20" customHeight="1">{"".join(h_cells)}</row>')
    rn += 1

    for sm in (summary.get('months') or []):
        m_label = sm.get('key') or f'{sm.get("year")}/{int(sm.get("month") or 0):02d}'
        row_cells = []
        vals = [
            (0, 0, m_label, S_CENTER),
            (1, 2, sm.get('ordinaryHm') or '', S_CENTER),
            (3, 4, sm.get('overtimeHm') or '', S_CENTER),
            (5, 6, sm.get('totalHm') or '', S_CENTER),
            (7, 8, '', S_TEXT),
            (9, 9, '', S_TEXT),
            (10, 11, '', S_TEXT),
        ]
        for c_start, c_end, val, st in vals:
            row_cells.extend(_range_cells(rn, c_start, c_end, val, st))
            if c_end > c_start:
                merges.append(f'{_col(c_start)}{rn}:{_col(c_end)}{rn}')
        rows_xml.append(f'<row r="{rn}" ht="18" customHeight="1">{"".join(row_cells)}</row>')
        rn += 1

    period = summary.get('period') or {}
    p_cells = []
    p_vals = [
        (0, 0, '期間合計', S_TOTAL_LABEL),
        (1, 2, period.get('ordinaryHm') or '', S_TOTAL_LABEL),
        (3, 4, period.get('overtimeHm') or '', S_TOTAL_LABEL),
        (5, 6, period.get('totalHm') or '', S_TOTAL_LABEL),
        (7, 8, '', S_TOTAL_LABEL),
        (9, 9, '', S_TOTAL_LABEL),
        (10, 11, '', S_TOTAL_LABEL),
    ]
    for c_start, c_end, val, st in p_vals:
        p_cells.extend(_range_cells(rn, c_start, c_end, val, st))
        if c_end > c_start:
            merges.append(f'{_col(c_start)}{rn}:{_col(c_end)}{rn}')
    rows_xml.append(f'<row r="{rn}" ht="19" customHeight="1">{"".join(p_cells)}</row>')
    rn += 1

    # 空行
    rows_xml.append(f'<row r="{rn}" ht="10" customHeight="1"/>')
    rn += 1

    # ==================== 2. ユニフォーム契約（新規分・入替分） ====================
    rows_xml.append(
        f'<row r="{rn}" ht="22" customHeight="1">{_cell(0, rn, f"2. ユニフォーム契約報告（{y}年{m}月度）", S_SECTION)}</row>'
    )
    merges.append(f'A{rn}:{_col(NCOLS - 1)}{rn}')
    rn += 1

    # 左右見出し: A-F = 新規分, G-L = 入替分
    sub_cells = (
        _range_cells(rn, 0, 5, '【新規分】', S_HEAD)
        + _range_cells(rn, 6, 11, '【入替分】', S_HEAD)
    )
    merges.append(f'A{rn}:F{rn}')
    merges.append(f'G{rn}:L{rn}')
    rows_xml.append(f'<row r="{rn}" ht="20" customHeight="1">{"".join(sub_cells)}</row>')
    rn += 1

    # 列見出し:
    # 新規分: A=No., B-C=顧客名, D-E=作業期間, F=備考
    # 入替分: G=No., H-I=顧客名, J-K=作業期間, L=備考
    ch_cells = (
        _range_cells(rn, 0, 0, 'No.', S_HEAD)
        + _range_cells(rn, 1, 2, '顧客名', S_HEAD)
        + _range_cells(rn, 3, 4, '作業期間', S_HEAD)
        + _range_cells(rn, 5, 5, '備考', S_HEAD)
        + _range_cells(rn, 6, 6, 'No.', S_HEAD)
        + _range_cells(rn, 7, 8, '顧客名', S_HEAD)
        + _range_cells(rn, 9, 10, '作業期間', S_HEAD)
        + _range_cells(rn, 11, 11, '備考', S_HEAD)
    )
    merges.extend([f'B{rn}:C{rn}', f'D{rn}:E{rn}', f'H{rn}:I{rn}', f'J{rn}:K{rn}'])
    rows_xml.append(f'<row r="{rn}" ht="19" customHeight="1">{"".join(ch_cells)}</row>')
    rn += 1

    slot_len = max(1, len(new_contracts), len(ren_contracts))
    for idx in range(slot_len):
        nc = new_contracts[idx] if idx < len(new_contracts) else {'customer': '', 'period': '', 'note': ''}
        rc = ren_contracts[idx] if idx < len(ren_contracts) else {'customer': '', 'period': '', 'note': ''}
        c_row = (
            _range_cells(rn, 0, 0, idx + 1 if idx < len(new_contracts) else '', S_CENTER)
            + _range_cells(rn, 1, 2, nc.get('customer') or ('記載なし' if not new_contracts and idx == 0 else ''), S_TEXT)
            + _range_cells(rn, 3, 4, nc.get('period') or '', S_TEXT)
            + _range_cells(rn, 5, 5, nc.get('note') or '', S_TEXT)
            + _range_cells(rn, 6, 6, idx + 1 if idx < len(ren_contracts) else '', S_CENTER)
            + _range_cells(rn, 7, 8, rc.get('customer') or ('記載なし' if not ren_contracts and idx == 0 else ''), S_TEXT)
            + _range_cells(rn, 9, 10, rc.get('period') or '', S_TEXT)
            + _range_cells(rn, 11, 11, rc.get('note') or '', S_TEXT)
        )
        merges.extend([f'B{rn}:C{rn}', f'D{rn}:E{rn}', f'H{rn}:I{rn}', f'J{rn}:K{rn}'])
        rows_xml.append(f'<row r="{rn}" ht="20" customHeight="1">{"".join(c_row)}</row>')
        rn += 1

    # 空行
    rows_xml.append(f'<row r="{rn}" ht="10" customHeight="1"/>')
    rn += 1

    # ==================== 3. 備品棚卸 ====================
    rows_xml.append(
        f'<row r="{rn}" ht="22" customHeight="1">{_cell(0, rn, f"3. 備品棚卸（{y}年{m}月）", S_SECTION)}</row>'
    )
    merges.append(f'A{rn}:{_col(NCOLS - 1)}{rn}')
    rn += 1

    h1_row = rn
    h2_row = rn + 1
    h1 = [_cell(i, h1_row, t, S_HEAD) for i, t in enumerate(BASE_HEADERS)]
    h2 = [_cell(i, h2_row, None, S_HEAD) for i in range(len(BASE_HEADERS))]
    for gi, (_, label, _, _) in enumerate(GROUPS):
        c0 = len(BASE_HEADERS) + gi * 2
        h1 += [_cell(c0, h1_row, label, S_HEAD), _cell(c0 + 1, h1_row, None, S_HEAD)]
        h2 += [_cell(c0, h2_row, '数量', S_HEAD), _cell(c0 + 1, h2_row, '金額', S_HEAD)]
    rows_xml.append(f'<row r="{h1_row}" ht="18" customHeight="1">{"".join(h1)}</row>')
    rows_xml.append(f'<row r="{h2_row}" ht="18" customHeight="1">{"".join(h2)}</row>')
    merges += [f'{_col(i)}{h1_row}:{_col(i)}{h2_row}' for i in range(len(BASE_HEADERS))]
    merges += [
        f'{_col(len(BASE_HEADERS) + g * 2)}{h1_row}:{_col(len(BASE_HEADERS) + g * 2 + 1)}{h1_row}'
        for g in range(len(GROUPS))
    ]
    rn += 2

    bihin_rows = [
        kintai_bihin._enrich_row({**r, 'sortOrder': i})
        for i, r in enumerate(bihin.get('rows') or [])
        if isinstance(r, dict)
    ]
    for r in bihin_rows:
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
        rows_xml.append(f'<row r="{rn}" ht="18" customHeight="1">{"".join(cells)}</row>')
        rn += 1

    totals = kintai_bihin._totals(bihin_rows)
    tcells = [_cell(0, rn, '金額合計', S_TOTAL_LABEL)] + [_cell(i, rn, None, S_TOTAL_LABEL) for i in (1, 2, 3)]
    for gi, (_, _, _, ak) in enumerate(GROUPS):
        c0 = len(BASE_HEADERS) + gi * 2
        tcells.append(_cell(c0, rn, None, S_TOTAL_LABEL))
        tcells.append(_cell(c0 + 1, rn, totals.get(ak), S_TOTAL_INT))
    rows_xml.append(f'<row r="{rn}" ht="19" customHeight="1">{"".join(tcells)}</row>')
    merges.append(f'A{rn}:D{rn}')

    crimp = report.get('crimp')
    if crimp:
        rn += 2
        period_label = f"{crimp['start']}〜{crimp['end']}"
        section = _cell(0, rn, f'4. 圧着数（{period_label}）', S_SECTION)
        rows_xml.append(f'<row r="{rn}" ht="22" customHeight="1">{section}</row>')
        merges.append(f'A{rn}:{_col(NCOLS - 1)}{rn}')
        rn += 1
        # Two parallel day lists keep a full month compact on A4.
        headers = []
        for start_col in (0, 6):
            for offset, label in ((0, '日付'), (2, 'カウンター'), (4, '圧着数')):
                col = start_col + offset
                headers.extend(_range_cells(rn, col, col + 1, label, S_HEAD))
                merges.append(f'{_col(col)}{rn}:{_col(col + 1)}{rn}')
        rows_xml.append(f'<row r="{rn}" ht="18" customHeight="1">{"".join(headers)}</row>')
        rn += 1
        readings = {row['day']: row for row in crimp.get('rows') or []}
        days = monthrange(y, m)[1]
        half = (days + 1) // 2
        for index in range(half):
            cells = []
            for start_col, day in ((0, index + 1), (6, index + half + 1)):
                record = readings.get(f'{y:04d}-{m:02d}-{day:02d}')
                values = [f'{m}/{day:02d}' if day <= days else '',
                          record['reading'] if record else '',
                          record['diff'] if record and record['diff'] is not None else ('—' if record else '')]
                for offset, value in zip((0, 2, 4), values):
                    col = start_col + offset
                    cells.extend(_range_cells(rn, col, col + 1, value, S_CENTER))
                    merges.append(f'{_col(col)}{rn}:{_col(col + 1)}{rn}')
            rows_xml.append(f'<row r="{rn}" ht="16" customHeight="1">{"".join(cells)}</row>')
            rn += 1
        total = crimp['total'] if crimp['total'] is not None else '集計できる記録なし'
        cells = _range_cells(rn, 0, 3, '圧着数 合計', S_TOTAL_LABEL)
        cells.extend(_range_cells(rn, 4, NCOLS - 1, total, S_TOTAL_LABEL))
        rows_xml.append(f'<row r="{rn}" ht="19" customHeight="1">{"".join(cells)}</row>')
        merges.extend([f'A{rn}:D{rn}', f'E{rn}:{_col(NCOLS - 1)}{rn}'])

    p_orders = report.get('processedOrders')
    if p_orders and p_orders.get('target'):
        target = p_orders['target']
        t_days = target.get('days') or []
        rn += 2
        section = _cell(0, rn, f'5. 指示書処理枚数（{y}年{m}月度　合計: {target.get("total", 0):,}枚）', S_SECTION)
        rows_xml.append(f'<row r="{rn}" ht="22" customHeight="1">{section}</row>')
        merges.append(f'A{rn}:{_col(NCOLS - 1)}{rn}')
        rn += 1

        headers = []
        for start_col in (0, 6):
            headers.extend(_range_cells(rn, start_col, start_col + 1, '日付', S_HEAD))
            merges.append(f'{_col(start_col)}{rn}:{_col(start_col + 1)}{rn}')
            headers.append(_cell(start_col + 2, rn, '曜日', S_HEAD))
            headers.extend(_range_cells(rn, start_col + 3, start_col + 4, '指示書枚数', S_HEAD))
            merges.append(f'{_col(start_col + 3)}{rn}:{_col(start_col + 4)}{rn}')
            headers.append(_cell(start_col + 5, rn, '割合', S_HEAD))
        rows_xml.append(f'<row r="{rn}" ht="18" customHeight="1">{"".join(headers)}</row>')
        rn += 1

        total_m = target.get('total', 0) or 1
        half = (len(t_days) + 1) // 2
        for index in range(half):
            cells = []
            for start_col, day_idx in ((0, index), (6, index + half)):
                if day_idx < len(t_days):
                    d = t_days[day_idx]
                    pct_str = f"{(d.get('count', 0) / total_m * 100):.1f}%"
                    cells.extend(_range_cells(rn, start_col, start_col + 1, d.get('label', ''), S_CENTER))
                    merges.append(f'{_col(start_col)}{rn}:{_col(start_col + 1)}{rn}')
                    cells.append(_cell(start_col + 2, rn, d.get('weekday', ''), S_CENTER))
                    cells.extend(_range_cells(rn, start_col + 3, start_col + 4, d.get('count', 0), S_TOTAL_INT))
                    merges.append(f'{_col(start_col + 3)}{rn}:{_col(start_col + 4)}{rn}')
                    cells.append(_cell(start_col + 5, rn, pct_str, S_CENTER))
                else:
                    cells.extend(_range_cells(rn, start_col, start_col + 5, '', S_CENTER))
                    merges.append(f'{_col(start_col)}{rn}:{_col(start_col + 5)}{rn}')
            rows_xml.append(f'<row r="{rn}" ht="16" customHeight="1">{"".join(cells)}</row>')
            rn += 1

        cells = _range_cells(rn, 0, 2, '指示書枚数 合計', S_TOTAL_LABEL)
        cells.extend(_range_cells(rn, 3, 5, target.get('total', 0), S_TOTAL_INT))
        cells.extend(_range_cells(rn, 6, NCOLS - 1, f'月間稼働: {len(t_days)} 日', S_TOTAL_LABEL))
        rows_xml.append(f'<row r="{rn}" ht="19" customHeight="1">{"".join(cells)}</row>')
        merges.extend([f'A{rn}:C{rn}', f'D{rn}:F{rn}', f'G{rn}:{_col(NCOLS - 1)}{rn}'])

    paper_size = '8'  # 幹部会議報告書は基本的にA3で大判印刷。
    widths = [12, 24, 10, 10, 10, 14, 10, 14, 10, 14, 10, 14]
    cols = ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, 1))
    sheet_xml = (
        f'<worksheet xmlns="{NS}">'
        '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        f'<cols>{cols}</cols><sheetData>{"".join(rows_xml)}</sheetData>'
        f'<mergeCells count="{len(merges)}">' + ''.join(f'<mergeCell ref="{x}"/>' for x in merges) + '</mergeCells>'
        '<pageMargins left="0.4" right="0.4" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
        f'<pageSetup orientation="portrait" paperSize="{paper_size}" fitToWidth="1" fitToHeight="1"/></worksheet>'
    )
    border = (
        '<border><left style="thin"><color rgb="FF555555"/></left>'
        '<right style="thin"><color rgb="FF555555"/></right>'
        '<top style="thin"><color rgb="FF555555"/></top>'
        '<bottom style="thin"><color rgb="FF555555"/></bottom></border>'
    )
    styles = (
        f'<styleSheet xmlns="{NS}">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.###"/></numFmts>'
        '<fonts count="4">'
        '<font><sz val="10"/><name val="Yu Gothic"/></font>'
        '<font><b/><sz val="10"/><name val="Yu Gothic"/></font>'
        '<font><b/><sz val="14"/><name val="Yu Gothic"/></font>'
        '<font><b/><sz val="11"/><name val="Yu Gothic"/></font>'
        '</fonts>'
        '<fills count="4">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF0"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFD9E6DF"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        f'<borders count="2"><border/>{border}</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="10">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf>'  # S_TEXT
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'  # S_HEAD
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf>'  # S_TITLE
        '<xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf>'  # S_SECTION
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'  # S_PLAIN
        '<xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="center"/></xf>'  # S_INT
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="center"/></xf>'  # S_DEC
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'  # S_TOTAL_LABEL
        '<xf numFmtId="3" fontId="1" fillId="2" borderId="1" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment vertical="center"/></xf>'  # S_TOTAL_INT
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'  # S_CENTER
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    )
    sheet_title = f'幹部会議報告_{y}年{m}月'
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as z:
        z.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        z.writestr(
            '_rels/.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        z.writestr(
            'xl/_rels/workbook.xml.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>',
        )
        z.writestr(
            'xl/workbook.xml',
            f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(sheet_title)}" sheetId="1" r:id="rId1"/></sheets>'
            '</workbook>',
        )
        z.writestr('xl/styles.xml', styles)
        z.writestr('xl/worksheets/sheet1.xml', sheet_xml)
    return out.getvalue()


def filename(year: int, month: int) -> str:
    return f'幹部会議報告_{year:04d}-{month:02d}.xlsx'


def content_disposition(name: str) -> str:
    ascii_name = name.encode('ascii', 'replace').decode('ascii').replace('?', '_').replace('"', '_')
    if ascii_name.startswith('_'):
        ascii_name = 'exec_report' + ascii_name.lstrip('_')
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name, safe="")}'


def export(c, year, month, fiscal_start_year: int, summary_fn):
    data = bundle(c, year, month, fiscal_start_year, summary_fn)
    name = filename(data['year'], data['month'])
    return 200, workbook(data), XLSX, {'Content-Disposition': content_disposition(name)}
