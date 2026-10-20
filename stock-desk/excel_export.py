"""登録・設定台帳の確認用 Excel 出力（記入用ひな形とは別形式・非破壊）。"""
from datetime import datetime
from functools import cmp_to_key
from urllib.parse import quote
import re

import excel_book
import stock

KINDS = ('customers', 'suppliers', 'products', 'locations', 'opening')
KIND_LABELS = {
    'customers': '顧客',
    'suppliers': '仕入先',
    'products': '商品',
    'locations': '保管場所',
    'opening': '初期在庫',
}
SCOPE_FILTERED = 'filtered'
SCOPE_ALL = 'all'
ACTIVE_ON = '使用中'
ACTIVE_OFF = '停止'
PRODUCT_SORT_DEFAULT = 'codeAsc'
PRODUCT_SORTS = (
    'codeAsc', 'codeDesc', 'nameAsc', 'nameDesc', 'idAsc', 'idDesc',
)
PRODUCT_SORT_LABELS = {
    'codeAsc': '品番順（昇順）',
    'codeDesc': '品番順（降順）',
    'nameAsc': '商品名順（昇順）',
    'nameDesc': '商品名順（降順）',
    'idAsc': '商品ID順（昇順）',
    'idDesc': '商品ID順（降順）',
}
_NAT_SPLIT = re.compile(r'(\d+)')


def _args(query):
    if not isinstance(query, dict):
        return {}
    out = {}
    for key, value in query.items():
        if isinstance(value, (list, tuple)):
            out[key] = value[0] if value else ''
        else:
            out[key] = value
    return out


def _truthy(value):
    return str(value or '').lower() in ('1', 'true', 'yes', 'on')


def _q(args):
    return stock.text(args.get('q'))


def _include_stopped(args):
    return _truthy(args.get('includeStopped') or args.get('stopped'))


def _scope(args):
    raw = stock.text(args.get('scope')) or SCOPE_FILTERED
    if raw not in (SCOPE_FILTERED, SCOPE_ALL):
        raise ValueError('出力範囲を選んでください。')
    return raw


def _kind(args):
    kind = stock.text(args.get('kind'))
    if kind not in KINDS:
        raise ValueError('出力する台帳を選んでください。')
    return kind


def _product_sort(args):
    raw = stock.text(args.get('sort')) or PRODUCT_SORT_DEFAULT
    if raw not in PRODUCT_SORTS:
        return PRODUCT_SORT_DEFAULT
    return raw


def _blank_product_code(code):
    return not stock.text(code)


def _natural_code_parts(text):
    """品番の自然順キー（数値部分は整数。JS localeCompare numeric に寄せる）。"""
    parts = []
    for chunk in _NAT_SPLIT.split(str(text or '')):
        if chunk == '':
            continue
        if chunk.isdigit():
            parts.append((0, int(chunk)))
        else:
            parts.append((1, chunk))
    return parts


def _cmp_natural_code(a, b):
    ka, kb = _natural_code_parts(a), _natural_code_parts(b)
    n = min(len(ka), len(kb))
    for i in range(n):
        ta, va = ka[i]
        tb, vb = kb[i]
        if ta != tb:
            return -1 if ta < tb else 1
        if va != vb:
            if ta == 0:
                return -1 if va < vb else 1
            # 非数字は日本語ロケール相当（Unicode 順。かなはあいうえお順）
            return -1 if va < vb else 1
    if len(ka) != len(kb):
        return -1 if len(ka) < len(kb) else 1
    return 0


def _cmp_ja_name(a, b):
    sa, sb = str(a or ''), str(b or '')
    if sa == sb:
        return 0
    return -1 if sa < sb else 1


def _cmp_product_rows(a, b, sort_key):
    key = sort_key or PRODUCT_SORT_DEFAULT
    if key == 'idAsc':
        return (a['id'] > b['id']) - (a['id'] < b['id'])
    if key == 'idDesc':
        return (b['id'] > a['id']) - (b['id'] < a['id'])
    if key in ('codeAsc', 'codeDesc'):
        dir_ = 1 if key == 'codeAsc' else -1
        a_blank = _blank_product_code(a.get('code'))
        b_blank = _blank_product_code(b.get('code'))
        if a_blank and b_blank:
            return (a['id'] > b['id']) - (a['id'] < b['id'])
        if a_blank:
            return 1
        if b_blank:
            return -1
        cmp = _cmp_natural_code(a.get('code') or '', b.get('code') or '')
        if cmp:
            return cmp * dir_
        return (a['id'] > b['id']) - (a['id'] < b['id'])
    if key in ('nameAsc', 'nameDesc'):
        dir_ = 1 if key == 'nameAsc' else -1
        cmp = _cmp_ja_name(a.get('name'), b.get('name'))
        if cmp:
            return cmp * dir_
        return (a['id'] > b['id']) - (a['id'] < b['id'])
    return (a['id'] > b['id']) - (a['id'] < b['id'])


def sort_product_rows(products, sort_key=None):
    key = sort_key or PRODUCT_SORT_DEFAULT
    return sorted(products, key=cmp_to_key(lambda a, b: _cmp_product_rows(a, b, key)))


def _active_label(active):
    return ACTIVE_ON if active else ACTIVE_OFF


def _match_text(haystack, needle):
    if not needle:
        return True
    return needle in (haystack or '')


def _now_stamp():
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def _scope_label(scope):
    return '表示条件' if scope == SCOPE_FILTERED else '全登録'


def filename_for(kind, scope, when=None):
    stamp = when or _now_stamp()
    return f'{KIND_LABELS[kind]}_{_scope_label(scope)}_{stamp}.xlsx'


def content_disposition(filename):
    # ASCII fallback + UTF-8 filename* for Japanese names
    ascii_name = 'export.xlsx'
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def preview(c, query):
    args = _args(query)
    kind = _kind(args)
    filtered = _collect(c, kind, {**args, 'scope': SCOPE_FILTERED})
    all_rows = _collect(c, kind, {**args, 'scope': SCOPE_ALL})
    return {
        'kind': kind,
        'label': KIND_LABELS[kind],
        'filtered': _count_payload(kind, filtered),
        'all': _count_payload(kind, all_rows),
        'notice': '確認用Excelです。登録用の記入用Excelとは形式が異なります。',
    }


def export_xlsx(c, query):
    args = _args(query)
    kind = _kind(args)
    scope = _scope(args)
    payload = _collect(c, kind, {**args, 'scope': scope})
    when = _now_stamp()
    sheets = _sheets_for(kind, scope, payload, when, args)
    blob = excel_book.workbook(sheets)
    return blob, filename_for(kind, scope, when)


def _count_payload(kind, payload):
    if kind == 'products':
        return {
            'count': len(payload['products']),
            'productCount': len(payload['products']),
            'handleCount': len(payload['handles']),
            'label': f'商品{len(payload["products"])}件・取扱{len(payload["handles"])}件',
        }
    if kind == 'locations':
        return {
            'count': len(payload['rows']),
            'siteCount': payload['siteCount'],
            'shelfCount': payload['shelfCount'],
            'label': f'{len(payload["rows"])}件（拠点{payload["siteCount"]}・棚{payload["shelfCount"]}）',
        }
    n = len(payload['rows'])
    return {'count': n, 'label': f'{n}件'}


def _collect(c, kind, args):
    if kind == 'customers':
        return {'rows': _customers(c, args)}
    if kind == 'suppliers':
        return {'rows': _suppliers(c, args)}
    if kind == 'products':
        return _products(c, args)
    if kind == 'locations':
        return _locations(c, args)
    if kind == 'opening':
        return {'rows': _opening(c, args)}
    raise ValueError('出力する台帳を選んでください。')


def _customers(c, args):
    scope = _scope(args)
    q = _q(args)
    include_stopped = True if scope == SCOPE_ALL else _include_stopped(args)
    rows = []
    for r in c.execute('SELECT id FROM customers ORDER BY id'):
        item = stock.customer_row(c, r['id'])
        if scope == SCOPE_FILTERED:
            if not include_stopped and not item['active']:
                continue
            hay = f"{item['name']}{item['kana']}{item['phone']}{item['contact']}{item['address']}"
            if not _match_text(hay, q):
                continue
        rows.append(item)
    return rows


def _suppliers(c, args):
    scope = _scope(args)
    q = _q(args)
    include_stopped = True if scope == SCOPE_ALL else _include_stopped(args)
    rows = []
    for r in c.execute('SELECT id FROM suppliers ORDER BY id'):
        item = stock.supplier_row(c, r['id'])
        if scope == SCOPE_FILTERED:
            if not include_stopped and not item['active']:
                continue
            hay = f"{item['name']}{item['phone']}{item['contact']}{item['address']}"
            if not _match_text(hay, q):
                continue
        rows.append(item)
    return rows


def _products(c, args):
    scope = _scope(args)
    q = _q(args)
    include_stopped = True if scope == SCOPE_ALL else _include_stopped(args)
    supplier_raw = args.get('supplierId')
    supplier_id = None
    if scope == SCOPE_FILTERED and supplier_raw not in (None, '', 'all', 'すべて'):
        if str(supplier_raw).isdigit():
            supplier_id = int(supplier_raw)
    products = []
    for r in c.execute('SELECT id FROM products ORDER BY id'):
        item = stock.product_row(c, r['id'])
        if scope == SCOPE_FILTERED:
            if supplier_id is not None and not stock.product_has_supplier(c, item['id'], supplier_id):
                continue
            if not include_stopped and not item['active']:
                continue
            sub_names = ''.join(s.get('name') or '' for s in (item.get('subSuppliers') or []))
            hay = (
                f"{item['code']}{item.get('codeDisplay') or ''}{item['name']}"
                f"{item['color']}{item.get('category') or ''}{item['supplierName']}{sub_names}"
            )
            hay += ''.join(f"{l['customerName']}{l['embroidery']}" for l in item['links'])
            if not _match_text(hay, q):
                continue
        products.append(item)
    products = sort_product_rows(products, _product_sort(args))
    handles = []
    for item in products:
        for link in item['links']:
            handles.append({
                'id': link['id'],
                'productId': item['id'],
                'code': item['code'],
                'codeDisplay': item.get('codeDisplay') or stock.product_code_display(
                    item['code'], item.get('category') or '',
                ),
                'productName': item['name'],
                'color': item['color'],
                'category': item.get('category') or '',
                'customerId': link['customerId'],
                'customerName': link['customerName'],
                'embroidery': link['embroidery'],
                'embroideryLabel': stock.embroidery_label(link['embroidery']),
                'isStandard': link['isStandard'],
                'active': link['active'],
            })
    return {'products': products, 'handles': handles}


def _locations(c, args):
    scope = _scope(args)
    q = _q(args)
    include_stopped = True if scope == SCOPE_ALL else _include_stopped(args)
    locations = [stock.location_row(c, r['id']) for r in c.execute(
        'SELECT id FROM locations ORDER BY parent_id IS NOT NULL, id'
    )]
    sites = [loc for loc in locations if loc.get('parentId') is None]
    by_parent = {}
    for loc in locations:
        if loc.get('parentId') is not None:
            by_parent.setdefault(loc['parentId'], []).append(loc)

    rows = []
    site_count = 0
    shelf_count = 0
    for site in sites:
        shelves = list(by_parent.get(site['id'], []))
        if scope == SCOPE_FILTERED:
            if not include_stopped and not site.get('active', True):
                continue
            if not include_stopped:
                shelves = [s for s in shelves if s.get('active', True)]
            if q:
                site_hit = q in (site.get('name') or '') or q in (site.get('label') or '')
                shelf_hit = any(q in (s.get('name') or '') or q in (s.get('label') or '') for s in shelves)
                if not site_hit and not shelf_hit:
                    continue
        site_count += 1
        rows.append({
            'id': site['id'],
            'parentId': None,
            'siteName': site['name'],
            'shelfName': '',
            'active': site['active'],
            'isSite': True,
        })
        for shelf in shelves:
            shelf_count += 1
            rows.append({
                'id': shelf['id'],
                'parentId': site['id'],
                'siteName': site['name'],
                'shelfName': shelf['name'],
                'active': shelf['active'],
                'isSite': False,
            })
    return {'rows': rows, 'siteCount': site_count, 'shelfCount': shelf_count}


def _opening(c, args):
    scope = _scope(args)
    q = _q(args).lower()
    sql = '''SELECT l.*, cp.id AS handle_id, cp.customer_id, cp.product_id, cp.embroidery,
                    cu.name AS customer_name,
                    p.code, p.name AS product_name, p.color,
                    loc.name AS location_name, loc.parent_id,
                    parent.name AS parent_name,
                    EXISTS(
                      SELECT 1 FROM moves m2
                      WHERE m2.lot_id=l.id AND m2.kind='opening_correction'
                    ) AS corrected
             FROM lots l
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             JOIN locations loc ON loc.id=l.location_id
             LEFT JOIN locations parent ON parent.id=loc.parent_id
             WHERE EXISTS (SELECT 1 FROM moves m WHERE m.lot_id=l.id AND m.kind='opening')
             ORDER BY l.id'''
    rows = []
    for r in c.execute(sql):
        if scope == SCOPE_FILTERED and q:
            hay = f"{r['customer_name']} {r['code']} {r['product_name']}".lower()
            if q not in hay:
                continue
        if r['parent_id']:
            site_name = r['parent_name'] or ''
            shelf_name = r['location_name'] or ''
        else:
            site_name = r['location_name'] or ''
            shelf_name = ''
        qty = r['qty_received']
        cost = r['unit_cost']
        unknown = bool(r['received_unknown'])
        received = r['received_on']
        payout = r['payout_order']
        rows.append({
            'id': r['id'],
            'handleId': r['handle_id'],
            'customerId': r['customer_id'],
            'customerName': r['customer_name'],
            'productId': r['product_id'],
            'code': r['code'],
            'productName': r['product_name'],
            'color': r['color'],
            'embroideryLabel': stock.embroidery_label(r['embroidery']),
            'size': r['size_label'],
            'conditionLabel': stock.condition_label(r['condition']),
            'siteName': site_name,
            'shelfName': shelf_name,
            'qtyRegistered': qty,
            'qtyRemaining': r['qty_remaining'],
            'unitCost': cost,
            'amount': qty * cost if qty is not None and cost is not None else None,
            'basisDate': r['basis_date'],
            'receivedOn': '' if unknown or received is None else received,
            'receivedUnknown': 'はい' if unknown else '',
            'payoutOrder': payout if unknown else None,
            'corrected': bool(r['corrected']),
            'qtyPoint': '訂正後の数量・単価' if r['corrected'] else '登録時の数量・単価',
        })
    return rows


def _meta_sheet(kind, scope, payload, when, args):
    counts = _count_payload(kind, payload)
    q = _q(args)
    include_stopped = _include_stopped(args) if scope == SCOPE_FILTERED else True
    filter_parts = []
    if scope == SCOPE_FILTERED:
        if q:
            filter_parts.append(f'検索={q}')
        if kind != 'opening':
            filter_parts.append('停止も表示' if include_stopped else '停止は非表示')
        if kind == 'products':
            sid = args.get('supplierId')
            if sid not in (None, '', 'all', 'すべて'):
                filter_parts.append(f'仕入先ID={sid}')
    sort_label = ''
    if kind == 'products':
        sort_key = _product_sort(args)
        sort_label = PRODUCT_SORT_LABELS.get(sort_key, PRODUCT_SORT_LABELS[PRODUCT_SORT_DEFAULT])
    headers = ['項目', '値']
    rows = [
        ['台帳', KIND_LABELS[kind]],
        ['出力範囲', _scope_label(scope)],
        ['出力日時', when.replace('_', ' ')],
        ['対象件数', counts['label']],
        ['絞り込み', '、'.join(filter_parts) if filter_parts else ('なし' if scope == SCOPE_FILTERED else '全件（検索・停止表示は反映しません）')],
    ]
    if sort_label:
        rows.append(['並び順', sort_label])
    rows.extend([
        ['形式', '確認用Excel（記入用Excelとは形式が異なります）'],
        ['注意', 'このファイルは確認用です。登録内容は変更しません。'],
    ])
    return ('出力情報', headers, rows, [18, 48])


def _sheets_for(kind, scope, payload, when, args):
    meta = _meta_sheet(kind, scope, payload, when, args)
    if kind == 'customers':
        headers = ['顧客ID', '顧客名', 'カナ', '住所', '電話番号', '担当者', '使用状態']
        rows = [[
            r['id'], r['name'], r.get('kana') or '', r.get('address') or '',
            r.get('phone') or '', r.get('contact') or '', _active_label(r['active']),
        ] for r in payload['rows']]
        return [meta, ('顧客', headers, rows, [10, 22, 16, 28, 16, 12, 10])]
    if kind == 'suppliers':
        headers = ['仕入先ID', '仕入先名', '住所', '電話番号', '担当者', '使用状態']
        rows = [[
            r['id'], r['name'], r.get('address') or '', r.get('phone') or '',
            r.get('contact') or '', _active_label(r['active']),
        ] for r in payload['rows']]
        return [meta, ('仕入先', headers, rows, [10, 22, 28, 16, 12, 10])]
    if kind == 'products':
        product_headers = [
            '商品ID', '品番', '品番表示', '商品名', '色', 'カテゴリ',
            '通常仕入先ID', '通常仕入先名', 'サブ仕入先ID', 'サブ仕入先名',
            '登録サイズ', '使用状態', '統合先商品ID',
        ]
        product_rows = []
        for p in payload['products']:
            subs = p.get('subSuppliers') or []
            product_rows.append([
                p['id'], p['code'], p.get('codeDisplay') or p['code'] or '品番なし',
                p['name'], p.get('color') or '', p.get('category') or '',
                p['supplierId'] if p['supplierId'] is not None else '',
                p.get('supplierName') or '',
                '、'.join(str(s['id']) for s in subs),
                '、'.join(s.get('name') or '' for s in subs),
                '、'.join(p.get('sizes') or []),
                _active_label(p['active']),
                p.get('mergedIntoId') if p.get('mergedIntoId') is not None else '',
            ])
        handle_headers = [
            '取扱ID', '商品ID', '品番', '品番表示', '商品名', '色', 'カテゴリ', '顧客ID', '顧客名',
            '加工仕様', '標準設定', '使用状態',
        ]
        handle_rows = [[
            h['id'], h['productId'], h['code'], h.get('codeDisplay') or h['code'] or '品番なし',
            h['productName'], h.get('color') or '', h.get('category') or '',
            h['customerId'], h['customerName'], h['embroideryLabel'],
            '標準' if h['isStandard'] else '',
            _active_label(h['active']),
        ] for h in payload['handles']]
        return [
            meta,
            ('商品一覧', product_headers, product_rows, [10, 14, 16, 22, 10, 12, 10, 18, 12, 18, 18, 10, 12]),
            ('取扱一覧', handle_headers, handle_rows, [10, 10, 14, 16, 22, 10, 12, 10, 18, 16, 10, 10]),
        ]
    if kind == 'locations':
        headers = ['場所ID', '親拠点ID', '拠点名', '棚名', '使用状態']
        rows = [[
            r['id'],
            r['parentId'] if r['parentId'] is not None else '',
            r['siteName'],
            r['shelfName'],
            _active_label(r['active']),
        ] for r in payload['rows']]
        return [meta, ('保管場所', headers, rows, [10, 10, 20, 16, 10])]
    if kind == 'opening':
        headers = [
            '登録特定ID', '取扱ID', '顧客ID', '顧客名', '商品ID', '品番', '商品名', '色',
            '加工仕様', 'サイズ', '新品／中古', '拠点名', '棚名',
            '初期登録数量', '現在庫数', '仕入単価', '金額',
            '在庫基準日', '入荷日', '入荷日不明', '払出し順',
            '数量・単価の時点', '訂正あり',
        ]
        rows = []
        for r in payload['rows']:
            rows.append([
                r['id'], r['handleId'], r['customerId'], r['customerName'],
                r['productId'], r['code'], r['productName'], r.get('color') or '',
                r['embroideryLabel'], r['size'], r['conditionLabel'],
                r['siteName'], r['shelfName'],
                r['qtyRegistered'], r['qtyRemaining'],
                r['unitCost'] if r['unitCost'] is not None else '',
                r['amount'] if r['amount'] is not None else '',
                r['basisDate'] or '',
                r['receivedOn'] or '',
                r['receivedUnknown'],
                r['payoutOrder'] if r['payoutOrder'] is not None else '',
                r['qtyPoint'],
                'はい' if r['corrected'] else '',
            ])
        widths = [12, 10, 10, 18, 10, 14, 18, 10, 14, 10, 10, 16, 12, 12, 10, 10, 10, 12, 12, 10, 10, 18, 10]
        return [meta, ('初期在庫', headers, rows, widths)]
    raise ValueError('出力する台帳を選んでください。')
