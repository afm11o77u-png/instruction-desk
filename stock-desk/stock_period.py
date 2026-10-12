"""期間入出荷集計（読取専用）。業務日ベースで期首・入出庫・訂正・期末を算出する。"""
from __future__ import annotations

import csv
import io
from datetime import date

import stock

KIND_LABELS = {
    'opening': '初期在庫',
    'opening_correction': '初期在庫の訂正',
    'inbound': '入庫',
    'inbound_correction': '入庫の訂正',
    'outbound': '出庫',
}


def _parse_period(args):
    start = stock.parse_date(args.get('from') or args.get('start') or args.get('startDate'), '開始日')
    end = stock.parse_date(args.get('to') or args.get('end') or args.get('endDate'), '終了日')
    if start > end:
        raise ValueError('開始日が終了日より後になっています。期間を確認してください。')
    return start, end


def _int_arg(args, key):
    raw = args.get(key)
    if raw in (None, ''):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    raise ValueError('絞り込みの指定が不正です。')


def resolve_business_on(move, lot):
    """Return YYYY-MM-DD business date or None (never invent from registration timestamp)."""
    cols = move.keys() if hasattr(move, 'keys') else move
    if 'business_on' in cols:
        value = move['business_on']
        if value:
            return value
    kind = move['kind']
    if kind == 'opening':
        return lot['basis_date']
    if kind == 'inbound':
        if lot['received_unknown']:
            return None
        return lot['received_on']
    return None


def _event_bucket(kind):
    if kind == 'inbound':
        return 'inbound'
    if kind == 'outbound':
        return 'outbound'
    if kind in ('opening_correction', 'inbound_correction'):
        return 'correction'
    if kind == 'opening':
        return 'opening'
    return 'other'


def _load_events(c, args):
    sql = '''SELECT m.*, l.customer_product_id, l.size_label, l.location_id, l.unit_cost AS lot_unit_cost,
                    l.basis_date, l.received_on AS lot_received_on, l.received_unknown,
                    l.qty_received AS lot_qty_received, l.condition,
                    cp.customer_id, cp.product_id, cp.embroidery,
                    cu.name AS customer_name,
                    p.code, p.name AS product_name, p.color,
                    loc.name AS location_name, loc.parent_id
             FROM moves m
             JOIN lots l ON l.id=m.lot_id
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             JOIN locations loc ON loc.id=l.location_id
             WHERE 1=1'''
    params = []
    cond_sql, cond_params = stock.condition_filter_sql(args)
    sql += cond_sql
    params.extend(cond_params)
    cid = _int_arg(args, 'customerId')
    if cid is not None:
        sql += ' AND cp.customer_id=?'
        params.append(cid)
    pid = _int_arg(args, 'productId')
    if pid is not None:
        sql += ' AND cp.product_id=?'
        params.append(pid)
    if args.get('size'):
        sql += ' AND l.size_label=?'
        params.append(args['size'])
    spec = args.get('spec')
    if spec == 'none':
        sql += " AND cp.embroidery=''"
    elif spec:
        sql += ' AND cp.embroidery=?'
        params.append(spec)
    lid = _int_arg(args, 'locationId')
    if lid is not None:
        sql += ' AND (l.location_id=? OR loc.parent_id=?)'
        params.extend([lid, lid])
    sql += ' ORDER BY m.id'
    events = []
    for r in c.execute(sql, params):
        lot = {
            'basis_date': r['basis_date'],
            'received_on': r['lot_received_on'],
            'received_unknown': r['received_unknown'],
        }
        business_on = resolve_business_on(r, lot)
        loc = {'name': r['location_name'], 'parent_id': r['parent_id']}
        site_id = r['parent_id'] if r['parent_id'] is not None else r['location_id']
        events.append({
            'id': r['id'],
            'lotId': r['lot_id'],
            'kind': r['kind'],
            'qtyDelta': r['qty_delta'],
            'unitCost': r['unit_cost'],
            'amount': abs(r['qty_delta']) * r['unit_cost'],
            'businessOn': business_on,
            'at': r['at'],
            'actorName': r['actor_name'] if 'actor_name' in r.keys() else '',
            'note': r['note'] or '',
            'customerProductId': r['customer_product_id'],
            'customerId': r['customer_id'],
            'customerName': r['customer_name'],
            'productId': r['product_id'],
            'code': r['code'],
            'productName': r['product_name'],
            'color': r['color'],
            'embroidery': r['embroidery'],
            'embroideryLabel': r['embroidery'] or '刺繍なし',
            'size': r['size_label'],
            'locationId': r['location_id'],
            'siteId': site_id,
            'locationLabel': stock.location_label(c, loc),
            'condition': r['condition'],
            'conditionLabel': stock.condition_label(r['condition']),
        })
    return events


def _key_of(ev):
    return (ev['customerProductId'], ev['size'], ev['locationId'], ev.get('condition') or stock.CONDITION_NEW)


def summarize_period(c, args):
    """Read-only period summary. Never mutates stock."""
    start, end = _parse_period(args)
    group = args.get('group') or 'detail'
    if group not in ('detail', 'customer', 'product', 'location'):
        raise ValueError('集計の種類が不正です。')
    events = _load_events(c, args)
    buckets = {}
    for ev in events:
        key = _key_of(ev)
        bucket = buckets.setdefault(key, {
            'customerProductId': ev['customerProductId'],
            'customerId': ev['customerId'],
            'customerName': ev['customerName'],
            'productId': ev['productId'],
            'code': ev['code'],
            'productName': ev['productName'],
            'color': ev['color'],
            'embroidery': ev['embroidery'],
            'embroideryLabel': ev['embroideryLabel'],
            'size': ev['size'],
            'locationId': ev['locationId'],
            'siteId': ev['siteId'],
            'locationLabel': ev['locationLabel'],
            'condition': ev['condition'],
            'conditionLabel': ev['conditionLabel'],
            'events': [],
            'missingBusinessDate': False,
            'missingReasons': set(),
        })
        bucket['events'].append(ev)
        if not ev['businessOn']:
            bucket['missingBusinessDate'] = True
            if ev['kind'] == 'outbound':
                bucket['missingReasons'].add('出庫に業務日（出庫日）がない履歴があります。登録日時では集計しません。')
            elif ev['kind'] in ('opening_correction', 'inbound_correction'):
                bucket['missingReasons'].add('訂正に業務日がない履歴があります。登録日時では集計しません。')
            elif ev['kind'] == 'inbound':
                bucket['missingReasons'].add('入庫に入荷日がない履歴があります。')
            elif ev['kind'] == 'opening':
                bucket['missingReasons'].add('初期在庫に基準日がない履歴があります。')
            else:
                bucket['missingReasons'].add('業務日がない履歴があります。')

    rows = []
    for key, bucket in buckets.items():
        row = _summarize_bucket(bucket, start, end)
        rows.append(row)

    # Also include keys that have current lots but no moves? Not needed for period moves view.
    rows.sort(key=lambda r: (
        r['customerName'], r['code'], r['productId'], r['embroideryLabel'], r['size'], r['locationLabel'],
        0 if r.get('condition') == stock.CONDITION_NEW else 1, r.get('condition') or '',
    ))
    # Drop empty rows with all zeros and computable (no activity ever intersecting period or balance)
    rows = [r for r in rows if r['include']]

    totals = {
        'inboundQty': 0,
        'outboundQty': 0,
        'correctionQty': 0,
        'inboundAmount': 0,
        'outboundAmount': 0,
        'computableRows': 0,
        'unavailableRows': 0,
    }
    for r in rows:
        if r['computable']:
            totals['inboundQty'] += r['inboundQty']
            totals['outboundQty'] += r['outboundQty']
            totals['correctionQty'] += r['correctionQty']
            totals['inboundAmount'] += r['inboundAmount']
            totals['outboundAmount'] += r['outboundAmount']
            totals['computableRows'] += 1
        else:
            totals['unavailableRows'] += 1

    groups = _group_rows(rows, group) if group != 'detail' else []
    return {
        'from': start,
        'to': end,
        'group': group,
        'amountLabel': '仕入原価ベース',
        'rows': rows,
        'groups': groups,
        'totals': totals,
        'rules': {
            'businessDate': '入庫は入荷日、出庫は出庫日、初期在庫は在庫基準日。登録日時は使いません。',
            'opening': '初期在庫は期間内入庫に数えず、基準日前なら期首、期間内なら訂正増減（＋）に入れます。',
            'missingDate': '業務日がない履歴がある行は算出不可とし、0で埋めません。',
            'outboundAmount': '出庫金額は出庫時点のロット仕入単価×数量（FIFO結果）です。',
        },
    }


def _unavailable_row(base, reason):
    return {
        **base,
        'computable': False,
        'unavailableReason': reason,
        'openingQty': None,
        'inboundQty': None,
        'outboundQty': None,
        'correctionQty': None,
        'closingQty': None,
        'inboundAmount': None,
        'outboundAmount': None,
        'identityOk': None,
        'history': [],
        'priceBreakdown': [],
        'include': True,
    }


def _summarize_bucket(bucket, start, end):
    events = bucket['events']
    base = {
        'customerProductId': bucket['customerProductId'],
        'customerId': bucket['customerId'],
        'customerName': bucket['customerName'],
        'productId': bucket['productId'],
        'code': bucket['code'],
        'productName': bucket['productName'],
        'color': bucket['color'],
        'embroidery': bucket['embroidery'],
        'embroideryLabel': bucket['embroideryLabel'],
        'size': bucket['size'],
        'locationId': bucket['locationId'],
        'siteId': bucket.get('siteId') or bucket['locationId'],
        'locationLabel': bucket['locationLabel'],
        'condition': bucket.get('condition') or stock.CONDITION_NEW,
        'conditionLabel': bucket.get('conditionLabel') or stock.condition_label(bucket.get('condition')),
        'include': True,
    }
    if bucket['missingBusinessDate']:
        return _unavailable_row(
            base,
            ' / '.join(sorted(bucket['missingReasons'])) or '業務日がない履歴があるため算出できません。',
        )

    dated = sorted(events, key=lambda e: (e['businessOn'], e['id']))
    # Walk full history once: detect insufficient history (running qty < 0) without inventing stock.
    running = 0
    for ev in dated:
        kind = _event_bucket(ev['kind'])
        qty = ev['qtyDelta']
        abs_qty = abs(qty)
        if kind == 'opening' or kind == 'inbound':
            running += abs_qty
        elif kind == 'outbound':
            running -= abs_qty
        elif kind == 'correction':
            running += qty
        if running < 0:
            return _unavailable_row(
                base,
                '履歴が足りないため期首・期末を正確に算出できません（業務日順の在庫がマイナスになる区間があります）。',
            )

    opening_qty = 0
    inbound_qty = 0
    outbound_qty = 0
    correction_qty = 0
    inbound_amount = 0
    outbound_amount = 0
    history = []
    price_map = {}

    for ev in dated:
        d = ev['businessOn']
        bucket_kind = _event_bucket(ev['kind'])
        qty = ev['qtyDelta']
        abs_qty = abs(qty)
        amount = abs_qty * ev['unitCost']

        if d < start:
            if bucket_kind == 'opening':
                opening_qty += abs_qty
            elif bucket_kind == 'inbound':
                opening_qty += abs_qty
            elif bucket_kind == 'outbound':
                opening_qty -= abs_qty
            elif bucket_kind == 'correction':
                opening_qty += qty
            continue

        if d > end:
            continue

        # In period [start, end] inclusive
        hist = {
            'id': ev['id'],
            'businessOn': d,
            'at': ev['at'],
            'kind': ev['kind'],
            'kindLabel': KIND_LABELS.get(ev['kind'], ev['kind']),
            'qty': abs_qty,
            'unitCost': ev['unitCost'],
            'amount': amount,
            'actorName': ev['actorName'] or '',
            'note': ev['note'],
            'lotId': ev['lotId'],
        }
        if bucket_kind == 'outbound':
            hist['qty'] = -abs_qty
            outbound_qty += abs_qty
            outbound_amount += amount
            price = price_map.setdefault(ev['unitCost'], {'unitCost': ev['unitCost'], 'qty': 0, 'amount': 0})
            price['qty'] += abs_qty
            price['amount'] += amount
        elif bucket_kind == 'inbound':
            inbound_qty += abs_qty
            inbound_amount += amount
        elif bucket_kind == 'opening':
            # 期間内の初期在庫は入庫に数えず訂正増減へ（不変式を保つ・二重計上しない）
            hist['kindLabel'] = '初期在庫（期間内・訂正増減扱い）'
            correction_qty += abs_qty
        elif bucket_kind == 'correction':
            hist['qty'] = qty
            hist['amount'] = qty * ev['unitCost']
            correction_qty += qty
        history.append(hist)

    closing_qty = opening_qty + inbound_qty - outbound_qty + correction_qty
    identity_ok = (opening_qty + inbound_qty - outbound_qty + correction_qty) == closing_qty

    include = bool(history) or opening_qty != 0 or closing_qty != 0
    if not include and any(ev['businessOn'] and ev['businessOn'] < start for ev in dated):
        include = opening_qty != 0 or closing_qty != 0

    return {
        **base,
        'computable': True,
        'unavailableReason': '',
        'openingQty': opening_qty,
        'inboundQty': inbound_qty,
        'outboundQty': outbound_qty,
        'correctionQty': correction_qty,
        'closingQty': closing_qty,
        'inboundAmount': inbound_amount,
        'outboundAmount': outbound_amount,
        'identityOk': identity_ok,
        'history': history,
        'priceBreakdown': sorted(price_map.values(), key=lambda p: p['unitCost']),
        'include': include,
    }


def _group_rows(rows, group):
    buckets = {}
    for r in rows:
        if group == 'customer':
            key = r['customerId']
            title = r['customerName']
        elif group == 'product':
            key = r['productId']
            title = '　'.join(p for p in (r['code'], r['productName'], r['color']) if p)
        else:
            # location: group by site (parent), matching 在庫一覧
            key = r.get('siteId') or r['locationId']
            title = r['locationLabel'].split('／')[0]
        b = buckets.setdefault(key, {
            'key': key, 'title': title, 'rows': [],
            'inboundQty': 0, 'outboundQty': 0, 'correctionQty': 0,
            'inboundAmount': 0, 'outboundAmount': 0,
            'computableRows': 0, 'unavailableRows': 0,
        })
        b['rows'].append(r)
        if r['computable']:
            b['inboundQty'] += r['inboundQty']
            b['outboundQty'] += r['outboundQty']
            b['correctionQty'] += r['correctionQty']
            b['inboundAmount'] += r['inboundAmount']
            b['outboundAmount'] += r['outboundAmount']
            b['computableRows'] += 1
        else:
            b['unavailableRows'] += 1
    result = list(buckets.values())
    result.sort(key=lambda g: (g['title'], g['key']))
    return result


def period_history(c, args):
    """History lines for one detail key within the period."""
    summary = summarize_period(c, {**args, 'group': 'detail'})
    cp = _int_arg(args, 'customerProductId')
    size = args.get('size')
    loc = _int_arg(args, 'locationId')
    if cp is None or not size or loc is None:
        raise ValueError('内訳の対象を選んでください。')
    condition = stock.parse_condition(args.get('condition'), default=stock.CONDITION_NEW)
    for row in summary['rows']:
        if (
            row['customerProductId'] == cp
            and row['size'] == size
            and row['locationId'] == loc
            and (row.get('condition') or stock.CONDITION_NEW) == condition
        ):
            return {
                'from': summary['from'],
                'to': summary['to'],
                'row': row,
                'events': row.get('history') or [],
                'priceBreakdown': row.get('priceBreakdown') or [],
            }
    raise ValueError('該当する集計行がありません。')


DETAIL_HEADERS = [
    '顧客', '商品ID', '品番', '商品名', '色', '加工仕様', 'サイズ', '保管場所', '状態',
    '期首在庫数', '期間内入庫数', '期間内出庫数', '訂正増減数', '期末在庫数',
    '期間内入庫金額（仕入原価）', '期間内出庫金額（仕入原価）', '算出', '算出不可の理由',
]


def _row_export_values(row):
    if not row['computable']:
        return [
            row['customerName'], row['productId'], row['code'], row['productName'], row['color'],
            row['embroideryLabel'], row['size'], row['locationLabel'],
            row.get('conditionLabel') or stock.condition_label(row.get('condition')),
            '', '', '', '', '', '', '',
            '算出不可', row.get('unavailableReason') or '',
        ]
    return [
        row['customerName'], row['productId'], row['code'], row['productName'], row['color'],
        row['embroideryLabel'], row['size'], row['locationLabel'],
        row.get('conditionLabel') or stock.condition_label(row.get('condition')),
        row['openingQty'], row['inboundQty'], row['outboundQty'], row['correctionQty'], row['closingQty'],
        row['inboundAmount'], row['outboundAmount'],
        '算出可', '',
    ]


def export_csv(c, args):
    data = summarize_period(c, {**args, 'group': 'detail'})
    buf = io.StringIO()
    buf.write('\ufeff')
    w = csv.writer(buf)
    w.writerow(['期間入出荷集計（仕入原価）'])
    w.writerow(['開始日', data['from'], '終了日', data['to']])
    w.writerow(['絞り込み', _filter_label(args)])
    w.writerow([
        '合計 入庫数', data['totals']['inboundQty'],
        '出庫数', data['totals']['outboundQty'],
        '訂正増減数', data['totals']['correctionQty'],
        '入庫金額', data['totals']['inboundAmount'],
        '出庫金額', data['totals']['outboundAmount'],
    ])
    w.writerow([])
    w.writerow(DETAIL_HEADERS)
    for row in data['rows']:
        w.writerow(_row_export_values(row))
    return buf.getvalue().encode('utf-8')


def export_xlsx(c, args):
    import excel_book
    data = summarize_period(c, {**args, 'group': 'detail'})
    meta_headers = ['項目', '値']
    meta_rows = [
        ['開始日', data['from']],
        ['終了日', data['to']],
        ['絞り込み', _filter_label(args)],
        ['合計 入庫数', data['totals']['inboundQty']],
        ['合計 出庫数', data['totals']['outboundQty']],
        ['合計 訂正増減数', data['totals']['correctionQty']],
        ['合計 入庫金額（仕入原価）', data['totals']['inboundAmount']],
        ['合計 出庫金額（仕入原価）', data['totals']['outboundAmount']],
    ]
    detail_rows = [_row_export_values(row) for row in data['rows']]
    return excel_book.workbook([
        ('条件と合計', meta_headers, meta_rows),
        ('明細', DETAIL_HEADERS, detail_rows),
    ])


def _filter_label(args):
    parts = []
    for key, label in (
        ('customerId', '顧客ID'), ('productId', '商品ID'), ('size', 'サイズ'),
        ('spec', '加工仕様'), ('locationId', '保管場所ID'), ('condition', '状態'),
    ):
        if args.get(key) not in (None, '', 'all', 'すべて'):
            parts.append(f'{label}={args.get(key)}')
    return '、'.join(parts) if parts else 'なし'


def month_bounds(today=None):
    today = today or date.today()
    start = today.replace(day=1).isoformat()
    return start, today.isoformat()


def prev_month_bounds(today=None):
    today = today or date.today()
    first = today.replace(day=1)
    prev_end = first.fromordinal(first.toordinal() - 1)
    prev_start = prev_end.replace(day=1)
    return prev_start.isoformat(), prev_end.isoformat()
