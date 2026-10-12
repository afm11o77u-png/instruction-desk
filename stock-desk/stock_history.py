"""在庫履歴一覧（読取専用）。ロット単位の moves を記録日時で絞り込み・ページ送りする。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import stock
import stock_period

JST = ZoneInfo('Asia/Tokyo')

KIND_FILTERS = {
    '': None,
    'all': None,
    'opening': ('opening',),
    'inbound': ('inbound',),
    'outbound': ('outbound',),
    'correction': ('opening_correction', 'inbound_correction'),
    'opening_correction': ('opening_correction',),
    'inbound_correction': ('inbound_correction',),
}

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _jst_today():
    return datetime.now(JST).date()


def _default_period():
    today = _jst_today()
    return date(today.year, today.month, 1), today


def _parse_optional_date(args, key, label):
    raw = args.get(key)
    if raw in (None, ''):
        return None
    return date.fromisoformat(stock.parse_date(raw, label))


def _int_arg(args, key):
    raw = args.get(key)
    if raw in (None, ''):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    raise ValueError('絞り込みの指定が不正です。')


def _page_args(args):
    page = _int_arg(args, 'page') or 1
    if page < 1:
        raise ValueError('ページ番号が不正です。')
    size = _int_arg(args, 'pageSize') or DEFAULT_PAGE_SIZE
    if size < 1 or size > MAX_PAGE_SIZE:
        raise ValueError(f'表示件数は1から{MAX_PAGE_SIZE}までです。')
    return page, size


def _kind_tuple(args):
    raw = (args.get('kind') or args.get('operation') or '').strip()
    if raw not in KIND_FILTERS:
        raise ValueError('操作内容の指定が不正です。')
    return KIND_FILTERS[raw], raw or 'all'


def _recorded_bounds(start: date, end: date):
    """Inclusive end date in JST calendar days, applied to naive `at` (stored as local/JST)."""
    start_key = start.isoformat()
    end_exclusive = (end + timedelta(days=1)).isoformat()
    return start_key, end_exclusive


def _display_or_unrecorded(value):
    text = stock.text(value) if value is not None else ''
    return text if text else '未記録'


def _serialize_event(c, r, batch_counts):
    lot = {
        'basis_date': r['basis_date'],
        'received_on': r['lot_received_on'],
        'received_unknown': r['received_unknown'],
    }
    business_on = stock_period.resolve_business_on(r, lot)
    loc = {'name': r['location_name'], 'parent_id': r['parent_id']}
    batch_id = r['move_batch_id']
    batch_total = batch_counts.get(batch_id, 1) if batch_id else 1
    cost_before = r['cost_before']
    cost_after = r['cost_after']
    qty_before = r['qty_before']
    qty_after = r['qty_after']
    amount_before = None if qty_before is None or cost_before is None else qty_before * cost_before
    amount_after = None if qty_after is None or cost_after is None else qty_after * cost_after
    actor = r['actor_name'] if 'actor_name' in r.keys() else ''
    return {
        'id': r['id'],
        'lotId': r['lot_id'],
        'kind': r['kind'],
        'kindLabel': stock_period.KIND_LABELS.get(r['kind'], r['kind']),
        'at': r['at'],
        'businessOn': business_on,
        'businessOnLabel': _display_or_unrecorded(business_on),
        'actorName': actor or '',
        'actorNameLabel': _display_or_unrecorded(actor),
        'note': r['note'] or '',
        'qtyDelta': r['qty_delta'],
        'qtyBefore': qty_before,
        'qtyAfter': qty_after,
        'unitCost': r['unit_cost'],
        'costBefore': cost_before,
        'costAfter': cost_after,
        'amountBefore': amount_before,
        'amountAfter': amount_after,
        'moveBatchId': batch_id,
        'batchLineCount': batch_total,
        'batchLineIndex': None,  # filled after sort within page if needed
        'customerId': r['customer_id'],
        'customerName': r['customer_name'],
        'customerActive': bool(r['customer_active']),
        'productId': r['product_id'],
        'code': r['code'],
        'productName': r['product_name'],
        'color': r['color'],
        'productActive': bool(r['product_active']),
        'embroidery': r['embroidery'],
        'embroideryLabel': r['embroidery'] or '刺繍なし',
        'size': r['size_label'],
        'locationId': r['location_id'],
        'locationLabel': stock.location_label(c, loc),
        'condition': r['condition'],
        'conditionLabel': stock.condition_label(r['condition']),
        'lotQtyRemaining': r['lot_qty_remaining'],
        'namesSource': 'current',
    }


def _batch_peers(c, batch_id, self_id):
    if not batch_id:
        return []
    rows = c.execute(
        '''SELECT m.id, m.lot_id, m.qty_delta, m.qty_before, m.qty_after, m.unit_cost,
                  l.size_label, l.condition
           FROM moves m JOIN lots l ON l.id=m.lot_id
           WHERE m.move_batch_id=? ORDER BY m.id''',
        (batch_id,),
    ).fetchall()
    peers = []
    for r in rows:
        peers.append({
            'id': r['id'],
            'lotId': r['lot_id'],
            'qtyDelta': r['qty_delta'],
            'qtyBefore': r['qty_before'],
            'qtyAfter': r['qty_after'],
            'unitCost': r['unit_cost'],
            'size': r['size_label'],
            'condition': r['condition'],
            'conditionLabel': stock.condition_label(r['condition']),
            'isSelf': r['id'] == self_id,
        })
    return peers


def list_history(c, args):
    """Read-only paginated stock history. Filters by recorded_at (moves.at) in JST days."""
    start = _parse_optional_date(args, 'from', '開始日')
    end = _parse_optional_date(args, 'to', '終了日')
    if start is None or end is None:
        d_from, d_to = _default_period()
        start = start or d_from
        end = end or d_to
    if start > end:
        raise ValueError('開始日が終了日より後になっています。期間を確認してください。')
    page, page_size = _page_args(args)
    kinds, kind_key = _kind_tuple(args)
    start_key, end_exclusive = _recorded_bounds(start, end)

    sql = '''SELECT m.*, l.customer_product_id, l.size_label, l.location_id, l.unit_cost AS lot_unit_cost,
                    l.basis_date, l.received_on AS lot_received_on, l.received_unknown,
                    l.qty_remaining AS lot_qty_remaining, l.condition,
                    cp.customer_id, cp.product_id, cp.embroidery,
                    cu.name AS customer_name, cu.active AS customer_active,
                    p.code, p.name AS product_name, p.color, p.active AS product_active,
                    loc.name AS location_name, loc.parent_id
             FROM moves m
             JOIN lots l ON l.id=m.lot_id
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             JOIN locations loc ON loc.id=l.location_id
             WHERE m.at >= ? AND m.at < ?'''
    params = [start_key, end_exclusive]

    cond_sql, cond_params = stock.condition_filter_sql(args)
    sql += cond_sql
    params.extend(cond_params)

    if kinds:
        placeholders = ','.join('?' for _ in kinds)
        sql += f' AND m.kind IN ({placeholders})'
        params.extend(kinds)

    cid = _int_arg(args, 'customerId')
    if cid is not None:
        sql += ' AND cp.customer_id=?'
        params.append(cid)
    pid = _int_arg(args, 'productId')
    if pid is not None:
        sql += ' AND cp.product_id=?'
        params.append(pid)
    lid = _int_arg(args, 'locationId')
    if lid is not None:
        sql += ' AND (l.location_id=? OR loc.parent_id=?)'
        params.extend([lid, lid])
    lot_id = _int_arg(args, 'lotId')
    if lot_id is not None:
        sql += ' AND m.lot_id=?'
        params.append(lot_id)

    count_sql = 'SELECT COUNT(*) AS n FROM (' + sql + ')'
    total = c.execute(count_sql, params).fetchone()['n']

    sql += ' ORDER BY m.at DESC, m.id DESC LIMIT ? OFFSET ?'
    offset = (page - 1) * page_size
    params_page = list(params) + [page_size, offset]
    rows = list(c.execute(sql, params_page))

    batch_ids = {r['move_batch_id'] for r in rows if r['move_batch_id']}
    batch_counts = {}
    if batch_ids:
        placeholders = ','.join('?' for _ in batch_ids)
        for r in c.execute(
            f'SELECT move_batch_id, COUNT(*) AS n FROM moves WHERE move_batch_id IN ({placeholders}) GROUP BY move_batch_id',
            list(batch_ids),
        ):
            batch_counts[r['move_batch_id']] = r['n']

    events = []
    for r in rows:
        item = _serialize_event(c, r, batch_counts)
        if item['moveBatchId'] and item['batchLineCount'] > 1:
            item['batchPeers'] = _batch_peers(c, item['moveBatchId'], item['id'])
            # Index among peers (1-based by move id order)
            peers = item['batchPeers']
            for i, peer in enumerate(peers, 1):
                if peer['isSelf']:
                    item['batchLineIndex'] = i
                    break
        else:
            item['batchPeers'] = []
            item['batchLineIndex'] = 1 if item['moveBatchId'] else None
        events.append(item)

    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        'events': events,
        'total': total,
        'page': page,
        'pageSize': page_size,
        'totalPages': total_pages,
        'sort': 'recordedAtDesc',
        'sortLabel': '記録日時の新しい順',
        'from': start.isoformat(),
        'to': end.isoformat(),
        'periodBasis': 'recordedAt',
        'periodBasisLabel': '期間は記録日時（日本時間）を基準にします。終了日の一日分を含みます。',
        'qtyScopeNote': '変更前・変更後の数量は商品全体の残数ではなく、対象ロットの残数です。',
        'namesNote': '顧客名・商品名は現在の台帳の名称です。操作当時の名称を保存しているわけではありません。',
        'kind': kind_key if kind_key != '' else 'all',
        'filters': {
            'customerId': cid,
            'productId': pid,
            'locationId': lid,
            'condition': args.get('condition') or '',
            'lotId': lot_id,
            'kind': kind_key if kind_key != '' else 'all',
        },
    }
