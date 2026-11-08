"""旧在庫JSONから初期在庫と旧QR対応を同時に登録する。

商品取り込みの判定はやり直さない。登録先は商品登録時の品番行マップだけを使う。
旧在庫行IDは移行元のIDとして残し、新システムの商品IDでは置き換えない。
旧顧客JSONを一緒に渡したときだけ、その名前と登録済み顧客名の完全一致で対応する。
顧客は新しく作らず、住所や電話は使わない。一致しない旧顧客IDは明示確認が必要。
"""
from __future__ import annotations

import base64
import hashlib
import json

import legacy_csv_bulk_import as bulk
import legacy_csv_import as legacy
import qr_link_import
import stock
import upload_limits

QTY_SOURCE_SAME = 'stock_count と calc_stock_count が同じ値'
QTY_EMPTY = '数量が空のため登録しません。0にはしません。'
QTY_NEGATIVE = '数量が負のため登録しません。0にはしません。'
QTY_MISMATCH = 'stock_count と calc_stock_count が違うため、どちらも採用しません。'
CUSTOMER_UNCONFIRMED = '旧顧客IDの対応が未確認です。既存の顧客を選んでください。新しくは作りません。'
CUSTOMER_INACTIVE = '選んだ顧客は停止中です。停止中の顧客には登録しません。'
CUSTOMER_MISSING = '選んだ顧客が見つかりません。'
DUPLICATE_ID = '同じ旧在庫行IDが、同じ内容でこのファイルに複数あります。数量は一度だけ登録し、二重にはしません。'
CONTENT_CONFLICT = (
    '同じ旧在庫行IDの内容がこのファイル内で違います。'
    '数量・顧客・メーカー・商品・サイズ・新品中古・場所が一致しないため、このIDは登録しません。'
)
LINK_CONFLICT = 'この旧在庫行IDは別の在庫に対応済みです。別の在庫へ付け替えません。'
QR_FORMAT_NOTE = '旧QRは {"id","customer_id","maker_id"} です。列の3つのIDから組み立て、別の形式は作りません。'
MAX_ROWS = 100000


def has_file(body):
    raw = body.get('legacyStockFile') if isinstance(body, dict) else None
    return isinstance(raw, str) and bool(raw.strip())


def ensure_schema(c):
    qr_link_import.ensure_schema(c)
    c.execute(
        '''CREATE TABLE IF NOT EXISTS legacy_customer_links (
          legacy_customer_id TEXT PRIMARY KEY,
          customer_id INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )'''
    )


def qr_text(stock_id, customer_id, maker_id):
    """既存の旧QR読取が受け付けるJSONだけを作る。"""
    return json.dumps(
        {'id': stock_id, 'customer_id': customer_id, 'maker_id': maker_id},
        ensure_ascii=False,
    )


def _decode(body):
    raw = body.get('legacyStockFile')
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('旧在庫JSONを選んでください。')
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as e:
        raise ValueError('旧在庫JSONを読み取れません。') from e
    if len(blob) > upload_limits.RAW_LEGACY_JSON_MAX_BYTES:
        raise ValueError(upload_limits.OVERSIZE_BODY_ERROR)
    try:
        doc = json.loads(blob.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError('旧在庫JSONを読み取れません。') from e
    if not isinstance(doc, dict) or doc.get('result') is not True or not isinstance(doc.get('data'), list):
        raise ValueError('旧在庫JSONは {"result":true,"data":[...]} の形にしてください。')
    rows = doc['data']
    if not rows:
        raise ValueError('旧在庫JSONに行がありません。')
    if len(rows) > MAX_ROWS:
        raise ValueError(f'行が多すぎます（{MAX_ROWS}行まで）。')
    name = stock.text(body.get('legacyStockName') or body.get('name') or 'old-stock.json') or 'old-stock.json'
    return {
        'rows': rows,
        'fileName': name,
        'fileSize': len(blob),
        'fileSha256': hashlib.sha256(blob).hexdigest(),
        'encoding': 'utf-8',
    }


def _text_id(value):
    if isinstance(value, str):
        return value.strip()
    return ''


def _agreed_qty_text(row):
    """商品判定の「在庫」列へ渡す文字。両方が同じ整数のときだけその数。空や不一致は空（0にしない）。"""
    stock_count = row.get('stock_count')
    calc = row.get('calc_stock_count')
    if type(stock_count) is bool or type(calc) is bool:
        return ''
    if type(stock_count) is int and type(calc) is int and stock_count == calc:
        return str(stock_count)
    return ''


def judgment_aux_upload(body):
    """商品登録の判定補助。旧在庫JSONを初期在庫CSVと同じ列の行にする。在庫は書かない。"""
    uploaded = _decode(body)
    catalog = decode_customer_file(body)
    names = (catalog or {}).get('byId') or {}
    signatures = {}
    conflict_ids = set()
    for row in uploaded['rows']:
        if not isinstance(row, dict):
            raise ValueError('旧在庫JSONの行がオブジェクトではありません。')
        sid = _text_id(row.get('id'))
        if not sid:
            continue
        sig = _source_signature(row)
        prev = signatures.get(sid)
        if prev is None:
            signatures[sid] = sig
        elif prev != sig:
            conflict_ids.add(sid)
    rows = []
    seen = set()
    for index, row in enumerate(uploaded['rows'], start=1):
        sid = _text_id(row.get('id'))
        if sid and (sid in conflict_ids or sid in seen):
            continue
        if sid:
            seen.add(sid)
        customer = ''
        cid = _text_id(row.get('customer_id'))
        entry = names.get(cid) if cid else None
        if entry and not entry.get('conflict') and entry.get('name'):
            customer = entry['name']
        price = row.get('price')
        rows.append((index, {
            '新古': _condition_text(row),
            '仕入先': stock.text(row.get('maker_name')),
            '顧客': customer,
            '商品名': stock.text(row.get('name')),
            '商品コード': stock.text(row.get('item_num')),
            'サイズ': stock.canonical_size_label(row.get('item_size')),
            '仕入単価': str(price) if type(price) is int and not isinstance(price, bool) else '',
            '在庫': _agreed_qty_text(row),
            '拠点': stock.text(row.get('factory_name')),
            '棚': stock.text(row.get('shelf_name')),
        }))
    if not rows:
        raise ValueError('旧在庫JSONに、商品判定に使える行がありません。')
    import legacy_code_assign
    legacy_code_assign.assign_rows(rows)
    return {
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'fileSize': uploaded['fileSize'],
        'encoding': uploaded['encoding'],
        'fields': ['新古', '仕入先', '顧客', '商品名', '商品コード', 'サイズ', '仕入単価', '在庫', '拠点', '棚'],
        'rows': rows,
        'ignoredColumns': [],
        'fromLegacyJson': True,
        'customerFileName': (catalog or {}).get('fileName') or '',
    }


def _qty_decision(row):
    stock_count = row.get('stock_count')
    calc = row.get('calc_stock_count')
    if type(stock_count) is bool or type(calc) is bool:
        return None, '数量の型が不正なため登録しません。'
    if stock_count in (None, '') or calc in (None, ''):
        return None, QTY_EMPTY
    if type(stock_count) is not int or type(calc) is not int:
        return None, '数量が整数でないため登録しません。'
    if stock_count != calc:
        return None, QTY_MISMATCH
    if stock_count < 0:
        return None, QTY_NEGATIVE
    return stock_count, f'{QTY_SOURCE_SAME}（{stock_count}）を在庫数量にします。'


def _condition_text(row):
    """is_used が null のときは新品にしない。空のまま既存の初期在庫ルールへ渡す。

    品名先頭の ＊ は、その既存ルールが中古として優先する（新品列より ＊ が勝つ）。
    """
    used = row.get('is_used')
    if used is True:
        return '中古'
    if used is False:
        return '新品'
    return ''


def _source_signature(row):
    """同一旧IDの内容が同じか。数量・顧客・メーカー・商品・サイズ・状態・場所・単価を見る。"""
    def norm(value):
        if value is None or isinstance(value, (bool, int)):
            return value
        return stock.text(value)

    return (
        norm(row.get('customer_id')),
        norm(row.get('maker_id')),
        norm(row.get('maker_name')),
        norm(row.get('item_id')),
        norm(row.get('item_num')),
        norm(row.get('name')),
        stock.canonical_size_label(row.get('item_size')),
        row.get('is_used') if isinstance(row.get('is_used'), bool) or row.get('is_used') is None else norm(row.get('is_used')),
        row.get('stock_count') if type(row.get('stock_count')) is int else norm(row.get('stock_count')),
        row.get('calc_stock_count') if type(row.get('calc_stock_count')) is int else norm(row.get('calc_stock_count')),
        row.get('price') if type(row.get('price')) is int else norm(row.get('price')),
        norm(row.get('factory_id')),
        norm(row.get('factory_name')),
        norm(row.get('shelf_id')),
        norm(row.get('shelf_name')),
    )


def decode_customer_file(body):
    """旧顧客JSON。無ければ None。不正なら例外（この時点では在庫を書かない）。"""
    raw = body.get('legacyCustomerFile') if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as e:
        raise ValueError('旧顧客JSONを読み取れません。') from e
    if len(blob) > upload_limits.RAW_LEGACY_JSON_MAX_BYTES:
        raise ValueError(upload_limits.OVERSIZE_BODY_ERROR)
    try:
        doc = json.loads(blob.decode('utf-8'))
    except Exception as e:
        raise ValueError('旧顧客JSONを読み取れません。') from e
    if not isinstance(doc, dict) or doc.get('result') is not True or not isinstance(doc.get('data'), list):
        raise ValueError('旧顧客JSONは {"result":true,"data":[...]} の形にしてください。')
    by_id = {}
    for item in doc['data']:
        if not isinstance(item, dict):
            raise ValueError('旧顧客JSONの行がオブジェクトではありません。')
        cid = item.get('id')
        if not isinstance(cid, str) or not cid.strip():
            raise ValueError('旧顧客JSONのidは空でない文字にしてください。数値にはしません。')
        cid = cid.strip()
        name = item.get('name')
        if not isinstance(name, str) or not stock.text(name):
            by_id[cid] = {'conflict': True, 'kind': 'empty', 'names': []}
            continue
        name = stock.text(name)
        prev = by_id.get(cid)
        if prev is None:
            by_id[cid] = {'conflict': False, 'kind': 'ok', 'name': name, 'names': [name]}
            continue
        if prev.get('conflict'):
            if name not in prev['names']:
                prev['names'].append(name)
            continue
        if prev.get('name') == name:
            continue
        by_id[cid] = {'conflict': True, 'kind': 'names', 'names': [prev['name'], name]}
    return {
        'byId': by_id,
        'fileSha256': hashlib.sha256(blob).hexdigest(),
        'fileName': stock.text(body.get('legacyCustomerName') or 'old-customers.json') or 'old-customers.json',
        'rowCount': len(doc['data']),
    }


def source_fingerprint(stock_sha, customer_sha=''):
    return hashlib.sha256(f'{stock_sha}|{customer_sha or ""}'.encode()).hexdigest()[:40]


def _load_customer(c, customer_id):
    return c.execute(
        'SELECT id, name, active FROM customers WHERE id=?', (customer_id,)
    ).fetchone()


def _manual_choice(c, raw):
    if raw in (None, ''):
        return {'customerId': None, 'name': '', 'reason': CUSTOMER_UNCONFIRMED, 'source': 'manual'}
    try:
        customer_id = int(raw)
    except (TypeError, ValueError):
        return {'customerId': None, 'name': '', 'reason': CUSTOMER_MISSING, 'source': 'manual'}
    row = _load_customer(c, customer_id)
    if not row:
        return {'customerId': None, 'name': '', 'reason': CUSTOMER_MISSING, 'source': 'manual'}
    if not row['active']:
        return {
            'customerId': row['id'], 'name': row['name'], 'reason': CUSTOMER_INACTIVE, 'source': 'manual',
        }
    return {'customerId': row['id'], 'name': row['name'], 'reason': '', 'source': 'manual'}


def _match_json_name(c, name):
    """登録名の完全一致だけ自動にする。表記ゆれや株式会社の除去では決めない。"""
    name = stock.text(name)
    matches, how = legacy.find_person_matches(c, 'customers', name)
    candidates = [
        {'id': row['id'], 'name': row['name'], 'active': bool(row['active'])}
        for row in matches
    ]
    if how == 'exact' and len(matches) == 1 and matches[0]['active']:
        return {
            'customerId': matches[0]['id'],
            'name': matches[0]['name'],
            'jsonName': name,
            'candidates': candidates,
            'reason': '',
            'source': 'json',
        }
    if how == 'exact' and len(matches) == 1 and not matches[0]['active']:
        return {
            'customerId': None, 'name': '', 'jsonName': name, 'candidates': candidates,
            'reason': f'旧顧客JSONの名前「{name}」は停止中の顧客です。確認してください。',
            'source': 'json-review',
        }
    if how == 'exact' and len(matches) > 1:
        return {
            'customerId': None, 'name': '', 'jsonName': name, 'candidates': candidates,
            'reason': f'旧顧客JSONの名前「{name}」は同名の顧客が複数あります。選んでください。',
            'source': 'json-review',
        }
    if how == 'form_variant':
        shown = '、'.join(row['name'] for row in matches[:5])
        return {
            'customerId': None, 'name': '', 'jsonName': name, 'candidates': candidates,
            'reason': (
                f'旧顧客JSONの名前「{name}」は表記が違う候補（{shown}）があります。'
                '自動では対応づけません。'
            ),
            'source': 'json-review',
        }
    return {
        'customerId': None, 'name': '', 'jsonName': name, 'candidates': [],
        'reason': f'旧顧客JSONの名前「{name}」に一致する登録済み顧客がありません。',
        'source': 'json-review',
    }


def _json_suggestion(c, catalog, legacy_id):
    entry = catalog['byId'].get(legacy_id)
    if entry is None:
        return {
            'customerId': None, 'name': '', 'jsonName': '', 'candidates': [],
            'reason': '旧顧客JSONにこの旧顧客IDがありません。',
            'source': 'missing',
        }
    if entry.get('conflict'):
        if entry.get('kind') == 'empty':
            reason = '旧顧客JSONの名前が空です。'
        else:
            shown = '、'.join(entry.get('names') or [])
            reason = f'旧顧客JSONの同じidに違う名前（{shown}）があります。'
        return {
            'customerId': None, 'name': '', 'jsonName': '', 'candidates': [],
            'reason': reason, 'source': 'json-invalid',
        }
    return _match_json_name(c, entry['name'])


def _customer_map(c, body, legacy_ids):
    requested = body.get('legacyCustomerLinks') if isinstance(body.get('legacyCustomerLinks'), dict) else {}
    saved = {
        r['legacy_customer_id']: r['customer_id']
        for r in c.execute('SELECT legacy_customer_id, customer_id FROM legacy_customer_links')
    }
    catalog = decode_customer_file(body)
    resolved = {}
    for legacy_id in legacy_ids:
        suggestion = _json_suggestion(c, catalog, legacy_id) if catalog is not None else None
        if legacy_id in requested:
            chosen = _manual_choice(c, requested.get(legacy_id))
            chosen['jsonName'] = (suggestion or {}).get('jsonName') or ''
            chosen['candidates'] = (suggestion or {}).get('candidates') or []
            if (
                not chosen.get('reason')
                and suggestion
                and suggestion.get('customerId')
                and suggestion['customerId'] != chosen.get('customerId')
            ):
                other = _load_customer(c, suggestion['customerId'])
                other_name = other['name'] if other else ''
                chosen['note'] = (
                    f'手動で選んだ顧客を使います。旧顧客JSONの名前は「{other_name}」に一致します。'
                )
            resolved[legacy_id] = chosen
            continue
        if catalog is None:
            if legacy_id not in saved:
                resolved[legacy_id] = {
                    'customerId': None, 'name': '', 'reason': CUSTOMER_UNCONFIRMED, 'source': '',
                }
                continue
            chosen = _manual_choice(c, saved[legacy_id])
            chosen['source'] = 'saved' if not chosen.get('reason') else 'saved'
            resolved[legacy_id] = chosen
            continue
        saved_id = saved.get(legacy_id)
        if suggestion and not suggestion.get('reason') and suggestion.get('customerId'):
            if saved_id and saved_id != suggestion['customerId']:
                saved_row = _load_customer(c, saved_id)
                resolved[legacy_id] = {
                    'customerId': None,
                    'name': '',
                    'jsonName': suggestion.get('jsonName') or '',
                    'candidates': [
                        {'id': saved_id, 'name': saved_row['name'] if saved_row else '', 'active': bool(saved_row['active']) if saved_row else False},
                        {'id': suggestion['customerId'], 'name': suggestion.get('name') or '', 'active': True},
                    ],
                    'reason': (
                        f'保存済みの対応は「{saved_row["name"] if saved_row else ""}」、'
                        f'旧顧客JSONの名前「{suggestion.get("jsonName") or ""}」は'
                        f'「{suggestion.get("name") or ""}」に一致します。上書きせず、選んでください。'
                    ),
                    'source': 'saved-conflict',
                }
            else:
                info = dict(suggestion)
                info['source'] = 'saved' if saved_id else 'json'
                resolved[legacy_id] = info
            continue
        if saved_id and suggestion and suggestion.get('reason'):
            saved_row = _load_customer(c, saved_id)
            suggestion = dict(suggestion)
            suggestion['reason'] = (
                f'{suggestion["reason"]} 保存済みの対応「{saved_row["name"] if saved_row else ""}」は自動では使いません。'
            )
            suggestion['customerId'] = None
            suggestion['name'] = ''
            resolved[legacy_id] = suggestion
            continue
        resolved[legacy_id] = suggestion or {
            'customerId': None, 'name': '', 'reason': CUSTOMER_UNCONFIRMED, 'source': '',
        }
    return resolved, catalog


def _blocked_plan(row_no, item, reasons, *, legacy_info, qty, status=None):
    status = status or legacy.STATUS_REVIEW
    return {
        'rowNo': row_no,
        'status': status,
        'statusLabel': bulk.OPENING_STATUS_LABELS.get(status, status),
        'qty': qty,
        'unitCost': None,
        'condition': None,
        'conditionLabel': '',
        'reasons': reasons,
        'csv': {
            'customer': '',
            'code': stock.text(item.get('item_num')),
            'productName': stock.text(item.get('name')),
            'size': stock.canonical_size_label(item.get('item_size')),
            'supplier': stock.text(item.get('maker_name')),
            'site': stock.text(item.get('factory_name')),
            'shelf': stock.text(item.get('shelf_name')),
        },
        'target': None,
        'place': None,
        'stock': None,
        'matchedLedger': False,
        'complement': False,
        'fromPersistedMap': True,
        'legacy': legacy_info,
        'qrAction': 'skip',
    }


def _legacy_payload(row, qty_note):
    stock_id = _text_id(row.get('id'))
    customer_id = _text_id(row.get('customer_id'))
    maker_id = _text_id(row.get('maker_id'))
    return {
        'stockId': stock_id,
        'customerId': customer_id,
        'makerId': maker_id,
        'itemId': _text_id(row.get('item_id')),
        'itemNum': stock.text(row.get('item_num')),
        'itemName': stock.text(row.get('name')),
        'itemSize': stock.canonical_size_label(row.get('item_size')),
        'makerName': stock.text(row.get('maker_name')),
        'factoryId': _text_id(row.get('factory_id')),
        'factoryName': stock.text(row.get('factory_name')),
        'shelfId': _text_id(row.get('shelf_id')),
        'shelfName': stock.text(row.get('shelf_name')),
        'isUsed': row.get('is_used'),
        'qtyNote': qty_note,
        'qr': qr_text(stock_id, customer_id, maker_id) if stock_id and customer_id and maker_id else '',
    }


def _qr_skip_reason(c, plan):
    target = plan.get('target') or {}
    product_id = target.get('productId')
    if product_id:
        row = c.execute('SELECT category FROM products WHERE id=?', (product_id,)).fetchone()
        if row and stock.text(row['category']) in (stock.CATEGORY_PARTS, stock.CATEGORY_PROCESS_SERVICE):
            return qr_link_import.OUT_OF_SCOPE_REASON
    code = stock.text((plan.get('csv') or {}).get('code'))
    name = stock.text((plan.get('csv') or {}).get('productName'))
    if not code:
        return (
            qr_link_import._hem_tape_reason(name)
            or qr_link_import._keyword_out_of_scope_reason(name)
            or ''
        )
    return ''


def _same_link_target(link, plan):
    target = plan.get('target') or {}
    place = plan.get('place') or {}
    if link['product_id'] != target.get('productId'):
        return False
    if link['customer_id'] != target.get('customerId'):
        return False
    handle = target.get('handleId') or target.get('customerProductId')
    if link['customer_product_id'] and handle and link['customer_product_id'] != handle:
        return False
    if stock.fold_size_ascii(link['size_label'] or '') != stock.fold_size_ascii(
        stock.text(target.get('size') or (plan.get('csv') or {}).get('size'))
    ):
        return False
    if (link['condition'] or '') != (plan.get('condition') or ''):
        return False
    if link['location_id'] != (place.get('locationId') if place else None):
        return False
    return True


def _apply_existing_link(c, plan):
    legacy_row = plan.get('legacy') or {}
    stock_id = legacy_row.get('stockId') or ''
    if not stock_id or plan.get('qrOutOfScope'):
        plan['qrAction'] = 'skip'
        return plan
    existing = c.execute(
        'SELECT * FROM qr_stock_links WHERE legacy_stock_id=?', (stock_id,)
    ).fetchone()
    if not existing:
        if plan.get('status') in (
            legacy.STATUS_CREATE, legacy.STATUS_REUSE, legacy.STATUS_UNCHANGED, bulk.MERGED_STATUS,
        ):
            plan['qrAction'] = 'insert'
        return plan
    if _same_link_target(existing, plan):
        plan['qrAction'] = 'keep'
        plan['legacyLinkId'] = existing['id']
        return plan
    plan['status'] = legacy.STATUS_REVIEW
    plan['statusLabel'] = bulk.OPENING_STATUS_LABELS.get(legacy.STATUS_REVIEW, legacy.STATUS_REVIEW)
    plan['qrAction'] = 'skip'
    plan['legacyConflict'] = True
    reasons = list(plan.get('reasons') or [])
    reasons.append(LINK_CONFLICT)
    plan['reasons'] = reasons
    return plan


def build_plans(c, body):
    ensure_schema(c)
    uploaded = _decode(body)
    default_site = stock.text(body.get('defaultSiteName')) or stock.UNCONFIRMED_SITE_NAME
    prepared = []
    signatures = {}
    for index, row in enumerate(uploaded['rows'], start=1):
        if not isinstance(row, dict):
            prepared.append({'blocked': _blocked_plan(
                index, {}, [f'{index}件目はオブジェクトではありません。'],
                legacy_info={}, qty=None,
            )})
            continue
        legacy_row = _legacy_payload(row, '')
        qty, qty_note = _qty_decision(row)
        legacy_row['qtyNote'] = qty_note
        tape_reason = qr_link_import.reflective_tape_reason(legacy_row['itemNum'], legacy_row['itemName'])
        if tape_reason:
            plan = _blocked_plan(index, row, [tape_reason], legacy_info=legacy_row, qty=qty)
            plan['status'] = legacy.STATUS_EXCLUDED
            plan['statusLabel'] = bulk.OPENING_STATUS_LABELS[legacy.STATUS_EXCLUDED]
            plan['qrAction'] = 'skip'
            plan['qrOutOfScope'] = True
            plan['reasons'] = [tape_reason]
            prepared.append({'blocked': plan})
            continue
        reasons = [qty_note]
        if not legacy_row['stockId'] or not legacy_row['customerId'] or not legacy_row['makerId']:
            reasons.append('旧在庫行ID・旧顧客ID・旧メーカーIDは文字として必要です。数値にはしません。')
            prepared.append({'blocked': _blocked_plan(index, row, reasons, legacy_info=legacy_row, qty=qty)})
            continue
        signature = _source_signature(row)
        signatures.setdefault(legacy_row['stockId'], set()).add(signature)
        prepared.append({
            'index': index, 'row': row, 'legacy': legacy_row,
            'qty': qty, 'reasons': reasons, 'signature': signature,
        })
    conflict_ids = {stock_id for stock_id, found in signatures.items() if len(found) > 1}
    seen_same = set()
    parsed = []
    legacy_customer_ids = []
    for item in prepared:
        if 'blocked' in item:
            parsed.append((item['blocked']['rowNo'], None, item['blocked']))
            continue
        index = item['index']
        row = item['row']
        legacy_row = item['legacy']
        qty = item['qty']
        reasons = list(item['reasons'])
        if legacy_row['stockId'] in conflict_ids:
            reasons.append(CONTENT_CONFLICT)
            plan = _blocked_plan(index, row, reasons, legacy_info=legacy_row, qty=qty)
            plan['legacyContentConflict'] = True
            plan['legacyConflict'] = True
            parsed.append((index, None, plan))
            if legacy_row['customerId'] not in legacy_customer_ids:
                legacy_customer_ids.append(legacy_row['customerId'])
            continue
        if legacy_row['stockId'] in seen_same:
            reasons.append(DUPLICATE_ID)
            plan = _blocked_plan(index, row, reasons, legacy_info=legacy_row, qty=qty)
            plan['legacyDuplicate'] = True
            parsed.append((index, None, plan))
            continue
        seen_same.add(legacy_row['stockId'])
        if legacy_row['customerId'] not in legacy_customer_ids:
            legacy_customer_ids.append(legacy_row['customerId'])
        if qty is None:
            parsed.append((index, None, _blocked_plan(index, row, reasons, legacy_info=legacy_row, qty=None)))
            continue
        if not legacy_row['makerName']:
            reasons.append('メーカー名が空です。仕入先を推測しません。')
            parsed.append((index, None, _blocked_plan(index, row, reasons, legacy_info=legacy_row, qty=qty)))
            continue
        parsed.append((index, row, legacy_row, qty, reasons))

    customers, customer_catalog = _customer_map(c, body, legacy_customer_ids)
    plans = []
    for item in parsed:
        if item[1] is None and len(item) == 3:
            plans.append(item[2])
            continue
        index, row, legacy_row, qty, reasons = item
        confirmed = customers.get(legacy_row['customerId']) or {}
        if confirmed.get('reason'):
            reasons = [confirmed['reason'], *reasons]
            plan = _blocked_plan(index, row, reasons, legacy_info=legacy_row, qty=qty)
            plan['customerUnconfirmed'] = True
            plans.append(plan)
            continue
        aux = {
            '顧客': confirmed['name'],
            '仕入先': legacy_row['makerName'],
            '商品コード': legacy_row['itemNum'],
            '商品名': legacy_row['itemName'],
            'サイズ': legacy_row['itemSize'],
            '新古': _condition_text(row),
            '在庫': str(qty),
            '仕入単価': str(row.get('price')) if type(row.get('price')) is int else '',
            '拠点': legacy_row['factoryName'],
            '棚': legacy_row['shelfName'],
        }
        plan = bulk._plan_opening_row_from_map(
            c, row_no=index, item=aux, default_site=default_site, selection={},
        )
        plan['legacy'] = legacy_row
        plan['qrAction'] = 'skip'
        plan_reasons = list(plan.get('reasons') or [])
        plan_reasons.insert(0, reasons[0])
        plan['reasons'] = plan_reasons
        target = plan.get('target') or {}
        if target.get('customerAction') == 'create' or not confirmed.get('customerId'):
            plan['status'] = legacy.STATUS_REVIEW
            plan['statusLabel'] = bulk.OPENING_STATUS_LABELS[legacy.STATUS_REVIEW]
            plan['reasons'] = [CUSTOMER_UNCONFIRMED, *plan['reasons']]
            plan['customerUnconfirmed'] = True
        else:
            target['customerId'] = confirmed['customerId']
            target['customer'] = confirmed['name']
            target['customerAction'] = 'reuse'
            plan['target'] = target
        skip = _qr_skip_reason(c, plan)
        if skip:
            plan['qrAction'] = 'skip'
            plan['qrOutOfScope'] = True
            if skip not in plan['reasons']:
                plan['reasons'] = [*plan['reasons'], skip]
        elif plan.get('serviceExcluded'):
            plan['qrAction'] = 'skip'
            plan['qrOutOfScope'] = True
        plans.append(plan)
        prior_only = [
            reason for reason in (plan.get('reasons') or [])
            if '以前取り込んだ行の記録があります' in reason
        ]
        if prior_only and not (plan.get('stock') or {}).get('exists') and plan.get('target', {}).get('productId'):
            plan['reasons'] = [
                reason for reason in plan['reasons']
                if '以前取り込んだ行の記録があります' not in reason
            ]
            plan['status'] = legacy.STATUS_REUSE
            plan['statusLabel'] = bulk.OPENING_STATUS_LABELS[legacy.STATUS_REUSE]
        if (
            not plan.get('qrOutOfScope')
            and not plan.get('customerUnconfirmed')
            and plan.get('status') in (
                legacy.STATUS_CREATE, legacy.STATUS_REUSE,
                legacy.STATUS_UNCHANGED, legacy.STATUS_QTY_DIFF,
            )
        ):
            _apply_existing_link(c, plan)

    bulk.merge_duplicate_opening_plans(c, plans)
    by_row = {plan['rowNo']: plan for plan in plans}
    for plan in plans:
        lead = by_row.get(plan.get('mergedInto'))
        if plan.get('status') != bulk.MERGED_STATUS or not lead:
            continue
        plan['target'] = lead.get('target')
        plan['place'] = lead.get('place')
        plan['condition'] = lead.get('condition')
        plan['qrOutOfScope'] = lead.get('qrOutOfScope')
        if (
            plan.get('qrOutOfScope')
            or lead.get('legacyConflict')
            or lead.get('status') == legacy.STATUS_QTY_DIFF
        ):
            plan['qrAction'] = 'skip'
            lead['qrAction'] = 'skip'
            continue
        _apply_existing_link(c, plan)
    pending = [
        legacy_id for legacy_id, info in customers.items() if info.get('reason')
    ]
    summary = {
        'customerPending': len(pending),
        'qtyBlocked': sum(1 for plan in plans if plan.get('qty') is None or any(
            text in ' '.join(plan.get('reasons') or [])
            for text in (QTY_EMPTY, QTY_NEGATIVE, QTY_MISMATCH)
        )),
        'conflicts': sum(1 for plan in plans if plan.get('legacyConflict')),
        'duplicates': sum(1 for plan in plans if plan.get('legacyDuplicate')),
        'qrSkipped': sum(1 for plan in plans if plan.get('qrOutOfScope')),
        'note': QR_FORMAT_NOTE,
        'customerFileName': (customer_catalog or {}).get('fileName') or '',
        'customerAuto': sum(1 for info in customers.values() if info.get('source') in ('json', 'saved') and not info.get('reason')),
        'customerReview': len(pending),
    }
    return uploaded, plans, customers, summary, customer_catalog


def customer_rows(customers, counts):
    rows = []
    for legacy_id, info in customers.items():
        rows.append({
            'legacyCustomerId': legacy_id,
            'rowCount': counts.get(legacy_id, 0),
            'customerId': info.get('customerId'),
            'customerName': info.get('name') or '',
            'jsonName': info.get('jsonName') or '',
            'source': info.get('source') or '',
            'note': info.get('note') or '',
            'candidates': info.get('candidates') or [],
            'confirmed': not info.get('reason'),
            'reason': info.get('reason') or '',
        })
    return rows


def build_opening_stock(c, body):
    uploaded, plans, customers, summary, customer_catalog = build_plans(c, body)
    counts = {}
    customer_counts = {}
    for plan in plans:
        counts[plan['status']] = counts.get(plan['status'], 0) + 1
        legacy_row = plan.get('legacy') or {}
        if legacy_row.get('customerId'):
            customer_counts[legacy_row['customerId']] = customer_counts.get(legacy_row['customerId'], 0) + 1
    choices = [
        {'id': row['id'], 'name': row['name'], 'active': bool(row['active'])}
        for row in c.execute('SELECT id, name, active FROM customers ORDER BY name, id')
    ]
    return {
        'enabled': True,
        'willRegisterOpeningStock': True,
        'fromPersistedMap': True,
        'fromLegacyJson': True,
        'plans': plans,
        'counts': counts,
        'complementCandidates': [],
        'mastersToCreate': {'customers': [], 'suppliers': [], 'sites': [], 'shelves': []},
        'legacyCustomers': customer_rows(customers, customer_counts),
        'customerChoices': choices,
        'legacySummary': summary,
        'qtySource': QTY_SOURCE_SAME,
        'note': (
            '旧在庫JSONの数量と、旧在庫行ID・旧QRを同時に登録します。'
            '登録先は商品登録時の品番行マップです。候補が曖昧な行は登録しません。'
            + (
                ' 旧顧客JSONの名前が登録済み顧客と一意に一致したIDは自動で対応します。一致しないものだけ選んでください。'
                if customer_catalog else ''
            )
            + QR_FORMAT_NOTE
        ),
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'customerFileName': (customer_catalog or {}).get('fileName') or '',
        'customerFileSha256': (customer_catalog or {}).get('fileSha256') or '',
        'sourceFingerprint': source_fingerprint(
            uploaded['fileSha256'], (customer_catalog or {}).get('fileSha256') or '',
        ),
        'uploaded': uploaded,
    }


def assert_customers_confirmed(opening_stock):
    pending = (opening_stock.get('legacySummary') or {}).get('customerPending') or 0
    if pending:
        raise ValueError(
            f'旧顧客IDの対応が未確認です（{pending}件）。'
            '既存の顧客を選んでから登録してください。新しくは作りません。'
        )


def _location_id(c, plan, default_site):
    place = plan.get('place') or {}
    if place.get('locationId'):
        return place.get('locationId')
    resolved = legacy._ensure_location(c, {
        'siteName': place.get('siteName') or default_site,
        'shelfName': place.get('shelfName') or '',
    })
    return resolved[0]


def _handle_id(c, plan):
    target = plan.get('target') or {}
    handle = target.get('handleId') or target.get('customerProductId')
    if handle:
        return handle
    embroidery = stock.text(target.get('embroidery') or '') or legacy.PENDING_EMBROIDERY
    row = c.execute(
        '''SELECT id FROM customer_products
           WHERE customer_id=? AND product_id=? AND embroidery=? AND active=1
           ORDER BY id DESC LIMIT 1''',
        (target.get('customerId'), target.get('productId'), embroidery),
    ).fetchone()
    if not row:
        raise ValueError(f'{plan.get("rowNo")}件目の取扱を特定できないため、旧QR対応は保存しません。')
    return row['id']


def save_links(c, plans, *, uploaded, run_id, default_site):
    """在庫登録のあと、同じトランザクションで旧QR対応を書く。失敗したら例外。"""
    ensure_schema(c)
    by_row = {plan['rowNo']: plan for plan in plans}
    saved = 0
    kept = 0
    for plan in plans:
        if plan.get('qrAction') not in ('insert', 'keep'):
            continue
        if plan.get('status') == bulk.MERGED_STATUS:
            lead = by_row.get(plan.get('mergedInto'))
            if not lead or lead.get('qrAction') == 'skip' or lead.get('legacyConflict'):
                continue
        legacy_row = plan.get('legacy') or {}
        if not legacy_row.get('qr'):
            continue
        target = plan.get('target') or {}
        if not target.get('productId') or not target.get('customerId'):
            raise ValueError(f'{plan.get("rowNo")}件目の登録先が無いため、旧QR対応は保存しません。')
        handle_id = _handle_id(c, plan)
        location_id = _location_id(c, plan, default_site)
        existing = c.execute(
            'SELECT * FROM qr_stock_links WHERE legacy_stock_id=?',
            (legacy_row['stockId'],),
        ).fetchone()
        comparable = dict(plan)
        comparable['target'] = dict(target)
        comparable['target']['handleId'] = handle_id
        comparable['place'] = dict(plan.get('place') or {})
        comparable['place']['locationId'] = location_id
        if existing:
            if not _same_link_target(existing, comparable):
                raise ValueError(LINK_CONFLICT)
            kept += 1
            continue
        if plan.get('qrAction') != 'insert':
            continue
        t = stock.stamp()
        c.execute(
            '''INSERT INTO qr_stock_links(
                 legacy_stock_id, legacy_customer_id, legacy_maker_id, legacy_item_id,
                 customer_name, maker_name, item_num, item_name, item_size, is_used,
                 price_text, qty_text, factory_id, factory_name, shelf_id, shelf_name,
                 arrival_date, qr_raw, customer_id, product_id, customer_product_id,
                 size_label, condition, location_id, match_kind, file_sha256, file_name,
                 source_row_no, run_id, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                legacy_row['stockId'], legacy_row['customerId'], legacy_row['makerId'],
                legacy_row.get('itemId') or '',
                target.get('customer') or '', legacy_row.get('makerName') or '',
                legacy_row.get('itemNum') or '', legacy_row.get('itemName') or '',
                legacy_row.get('itemSize') or '',
                '' if legacy_row.get('isUsed') is None else ('true' if legacy_row.get('isUsed') else 'false'),
                '', str(plan.get('qty') if plan.get('qty') is not None else ''),
                legacy_row.get('factoryId') or '', legacy_row.get('factoryName') or '',
                legacy_row.get('shelfId') or '', legacy_row.get('shelfName') or '',
                '', legacy_row['qr'], target.get('customerId'), target.get('productId'),
                handle_id, stock.canonical_size_label(target.get('size') or legacy_row.get('itemSize')),
                plan.get('condition') or '', location_id, 'opening_json',
                uploaded['fileSha256'], uploaded['fileName'], plan['rowNo'], run_id, t, t,
            ),
        )
        saved += 1
    return {'inserted': saved, 'kept': kept}


def remember_customers(c, customers):
    t = stock.stamp()
    for legacy_id, info in customers.items():
        if info.get('reason') or not info.get('customerId'):
            continue
        c.execute(
            '''INSERT INTO legacy_customer_links(legacy_customer_id, customer_id, created_at, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(legacy_customer_id) DO UPDATE SET
                 customer_id=excluded.customer_id, updated_at=excluded.updated_at''',
            (legacy_id, info['customerId'], t, t),
        )
