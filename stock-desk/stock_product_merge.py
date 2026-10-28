"""重複商品の比較・統合。在庫ロットは付け替えのみ（数量の合算・削除・再加算はしない）。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import stock

CUSTOM_ORDER_CODE = '別注'
UNKNOWN_CODE = '不明'
SPECIAL_CODES = frozenset({CUSTOM_ORDER_CODE, UNKNOWN_CODE})
ALIAS_KIND_NAME = 'name'
ALIAS_KIND_SUPPLIER = 'supplier'


def is_normal_product_code(code):
    """通常品番（1品番=1商品の対象）。別注・不明・空欄は対象外。"""
    t = stock.text(code)
    return bool(t) and t not in SPECIAL_CODES


def is_special_product_code(code):
    t = stock.text(code)
    return (not t) or t in SPECIAL_CODES


def alias_value_key(value):
    """照合用キー（空白種類・全角半角を畳む。原文表記は value 側に保持）。"""
    import re
    import unicodedata
    t = unicodedata.normalize('NFKC', stock.text(value))
    return re.sub(r'[\s\u3000]+', '', t)


def find_live_products_by_code(c, code):
    return list(c.execute(
        '''SELECT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,
                  p.merged_into_id,s.name AS supplier_name
           FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id
           WHERE p.code=? AND p.merged_into_id IS NULL ORDER BY p.id''',
        (stock.text(code),),
    ))


def assert_normal_code_available(c, code, *, product_id=None, allow_historical_duplicate=False):
    """通常品番の新規採番・他品番からの乗換えを拒否。既存重複の編集は許可。

    既存DBに同品番の複数商品があっても UNIQUE 制約は張らない。
    新規作成や、別コードから既存コードへの変更だけを止める。
    allow_historical_duplicate はサンプル／テストで既存重複を意図的に作るときだけ。
    """
    if allow_historical_duplicate:
        return
    if not is_normal_product_code(code):
        return
    others = [
        r for r in find_live_products_by_code(c, code)
        if product_id is None or int(r['id']) != int(product_id)
    ]
    if not others:
        return
    if product_id is not None:
        prev = c.execute('SELECT code FROM products WHERE id=?', (product_id,)).fetchone()
        if prev and stock.text(prev['code']) == stock.text(code):
            # 既にこの品番を持つ商品の編集（歴史的重複の残存）は許可
            return
    ids = ', '.join(str(r['id']) for r in others[:5])
    more = ' など' if len(others) > 5 else ''
    raise ValueError(
        f'品番「{stock.text(code)}」は既に商品ID {ids}{more} で使われています。'
        f'通常品番は1品番=1商品です。既存商品を編集するか、'
        f'「重複商品の整理」で1つにまとめてから登録してください。'
    )


def ensure_name_alias(c, product_id, name):
    """旧商品名を照合用別名として残す（公式名の書き換え時）。"""
    key = alias_value_key(name)
    if not key or not product_id:
        return False
    live = resolve_live_product_id(c, product_id)
    prod = c.execute('SELECT id,name FROM products WHERE id=?', (live,)).fetchone()
    if not prod:
        return False
    if alias_value_key(prod['name']) == key:
        return False
    exists = c.execute(
        'SELECT id FROM product_match_aliases WHERE product_id=? AND kind=? AND value_key=?',
        (live, ALIAS_KIND_NAME, key),
    ).fetchone()
    if exists:
        return False
    c.execute(
        '''INSERT INTO product_match_aliases(product_id,kind,value,value_key,created_at)
           VALUES(?,?,?,?,?)''',
        (live, ALIAS_KIND_NAME, stock.text(name), key, stock.stamp()),
    )
    return True


def ensure_supplier_alias(c, product_id, supplier_name):
    key = alias_value_key(supplier_name)
    if not key or not product_id:
        return False
    live = resolve_live_product_id(c, product_id)
    exists = c.execute(
        'SELECT id FROM product_match_aliases WHERE product_id=? AND kind=? AND value_key=?',
        (live, ALIAS_KIND_SUPPLIER, key),
    ).fetchone()
    if exists:
        return False
    c.execute(
        '''INSERT INTO product_match_aliases(product_id,kind,value,value_key,created_at)
           VALUES(?,?,?,?,?)''',
        (live, ALIAS_KIND_SUPPLIER, stock.text(supplier_name), key, stock.stamp()),
    )
    return True


def find_products_by_name_alias(c, code, name):
    """品番＋旧商品名別名で既存商品を探す。

    先頭の中古標記「＊/*」の有無だけが違う別名も同一視する。
    """
    if not is_normal_product_code(code):
        return []
    import legacy_csv_import as legacy
    keys = []
    seen_keys = set()
    core, _marked = legacy.strip_used_marker(name)
    for candidate in (name, core):
        key = alias_value_key(candidate)
        if key and key not in seen_keys:
            seen_keys.add(key)
            keys.append(key)
    if not keys:
        return []
    placeholders = ','.join('?' * len(keys))
    by_key = list(c.execute(
        f'''SELECT DISTINCT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,
                  p.merged_into_id,s.name AS supplier_name
           FROM product_match_aliases a
           JOIN products p ON p.id=a.product_id
           LEFT JOIN suppliers s ON s.id=p.supplier_id
           WHERE a.kind=? AND a.value_key IN ({placeholders}) AND p.code=? AND p.merged_into_id IS NULL
           ORDER BY p.id''',
        (ALIAS_KIND_NAME, *keys, stock.text(code)),
    ))
    if by_key:
        return by_key
    core_key = legacy.product_name_core_key(name)
    if not core_key:
        return []
    # ＊緑つなぎ / *緑つなぎ のように value_key が違う標記違い別名
    rows = list(c.execute(
        '''SELECT DISTINCT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,
                  p.merged_into_id,s.name AS supplier_name, a.value AS alias_value
           FROM product_match_aliases a
           JOIN products p ON p.id=a.product_id
           LEFT JOIN suppliers s ON s.id=p.supplier_id
           WHERE a.kind=? AND p.code=? AND p.merged_into_id IS NULL
           ORDER BY p.id''',
        (ALIAS_KIND_NAME, stock.text(code)),
    ))
    out = []
    seen_ids = set()
    for r in rows:
        if legacy.product_name_core_key(r['alias_value']) != core_key:
            continue
        if r['id'] in seen_ids:
            continue
        seen_ids.add(r['id'])
        out.append(r)
    return out


def list_name_aliases(c, product_id):
    live = resolve_live_product_id(c, product_id)
    return [
        r['value'] for r in c.execute(
            '''SELECT value FROM product_match_aliases
               WHERE product_id=? AND kind=? ORDER BY id''',
            (live, ALIAS_KIND_NAME),
        )
    ]


def _tables(c):
    return {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def needs_merge_migration(c):
    tables = _tables(c)
    if 'product_suppliers' not in tables or 'product_merges' not in tables:
        return True
    if 'product_match_aliases' not in tables:
        return True
    if 'products' in tables:
        cols = {r['name'] for r in c.execute('PRAGMA table_info(products)')}
        if 'merged_into_id' not in cols:
            return True
    return False


def migrate_product_merge(c):
    c.execute('''CREATE TABLE IF NOT EXISTS product_suppliers (
      id INTEGER PRIMARY KEY,
      product_id INTEGER NOT NULL REFERENCES products(id),
      supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      UNIQUE(product_id, supplier_id)
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_suppliers_product '
        'ON product_suppliers(product_id, sort_order, id)'
    )
    c.execute('''CREATE TABLE IF NOT EXISTS product_merges (
      id INTEGER PRIMARY KEY,
      keep_product_id INTEGER NOT NULL,
      actor_name TEXT NOT NULL DEFAULT '',
      client_token TEXT NOT NULL UNIQUE,
      before_qty INTEGER NOT NULL,
      after_qty INTEGER NOT NULL,
      before_amount INTEGER NOT NULL,
      after_amount INTEGER NOT NULL,
      fingerprint TEXT NOT NULL DEFAULT '',
      request_json TEXT NOT NULL DEFAULT '{}',
      result_json TEXT NOT NULL DEFAULT '{}',
      backup_path TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS product_merge_sources (
      id INTEGER PRIMARY KEY,
      merge_id INTEGER NOT NULL REFERENCES product_merges(id),
      source_product_id INTEGER NOT NULL,
      source_snapshot_json TEXT NOT NULL DEFAULT '{}'
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_merge_sources_merge '
        'ON product_merge_sources(merge_id)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_merge_sources_product '
        'ON product_merge_sources(source_product_id)'
    )
    c.execute('''CREATE TABLE IF NOT EXISTS product_match_aliases (
      id INTEGER PRIMARY KEY,
      product_id INTEGER NOT NULL REFERENCES products(id),
      kind TEXT NOT NULL,
      value TEXT NOT NULL,
      value_key TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(product_id, kind, value_key)
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_match_aliases_lookup '
        'ON product_match_aliases(kind, value_key, product_id)'
    )
    tables = _tables(c)
    if 'products' in tables:
        cols = {r['name'] for r in c.execute('PRAGMA table_info(products)')}
        if 'merged_into_id' not in cols:
            c.execute(
                'ALTER TABLE products ADD COLUMN merged_into_id INTEGER REFERENCES products(id)'
            )
        if 'merged_at' not in cols:
            c.execute('ALTER TABLE products ADD COLUMN merged_at TEXT')


def resolve_live_product_id(c, product_id, *, max_hops=20):
    """統合元IDを最終の統合先へ辿る。環や欠落時は最後に辿れたIDを返す。"""
    if product_id in (None, ''):
        return None
    pid = int(product_id)
    seen = set()
    for _ in range(max_hops):
        if pid in seen:
            break
        seen.add(pid)
        row = c.execute(
            'SELECT id, merged_into_id FROM products WHERE id=?', (pid,)
        ).fetchone()
        if not row:
            return pid
        nxt = row['merged_into_id']
        if nxt in (None, ''):
            return int(row['id'])
        pid = int(nxt)
    return pid


def product_stock_totals(c, product_ids):
    """対象商品に紐づくロット残数量・金額（原価未確定は金額0として合算せず除外扱いは別）。

    金額は unit_cost * qty_remaining（cost_pending=1 でも数値は保持し合計に含めるが、
    不変検証では before/after の同一を見る）。
    """
    ids = [int(x) for x in product_ids if x is not None]
    if not ids:
        return {'qty': 0, 'amount': 0, 'lotCount': 0, 'handleCount': 0}
    placeholders = ','.join('?' * len(ids))
    lot = c.execute(
        f'''SELECT COALESCE(SUM(l.qty_remaining),0) AS qty,
                   COALESCE(SUM(l.qty_remaining * l.unit_cost),0) AS amount,
                   COUNT(*) AS n
            FROM lots l
            JOIN customer_products cp ON cp.id=l.customer_product_id
            WHERE cp.product_id IN ({placeholders})''',
        ids,
    ).fetchone()
    handles = c.execute(
        f'SELECT COUNT(*) AS n FROM customer_products WHERE product_id IN ({placeholders})',
        ids,
    ).fetchone()['n']
    return {
        'qty': int(lot['qty'] or 0),
        'amount': int(lot['amount'] or 0),
        'lotCount': int(lot['n'] or 0),
        'handleCount': int(handles or 0),
    }


def _product_compare_card(c, pid):
    row = stock.product_row(c, pid)
    totals = product_stock_totals(c, [pid])
    lots = []
    for r in c.execute(
        '''SELECT l.id, l.size_label, l.qty_remaining, l.unit_cost, l.condition,
                  l.received_on, l.received_unknown, l.payout_order, l.cost_pending,
                  l.location_id, cp.id AS handle_id, cp.customer_id, cp.embroidery,
                  cu.name AS customer_name, loc.name AS location_name, loc.parent_id,
                  parent.name AS parent_name
           FROM lots l
           JOIN customer_products cp ON cp.id=l.customer_product_id
           JOIN customers cu ON cu.id=cp.customer_id
           JOIN locations loc ON loc.id=l.location_id
           LEFT JOIN locations parent ON parent.id=loc.parent_id
           WHERE cp.product_id=?
           ORDER BY l.id''',
        (pid,),
    ):
        if r['parent_id']:
            loc_label = f"{r['parent_name']}／{r['location_name']}"
        else:
            loc_label = r['location_name']
        lots.append({
            'id': r['id'],
            'handleId': r['handle_id'],
            'customerId': r['customer_id'],
            'customerName': r['customer_name'],
            'embroidery': r['embroidery'] or '',
            'embroideryLabel': stock.embroidery_label(r['embroidery']),
            'size': r['size_label'],
            'qty': r['qty_remaining'],
            'unitCost': r['unit_cost'],
            'amount': (r['qty_remaining'] or 0) * (r['unit_cost'] or 0),
            'condition': r['condition'],
            'conditionLabel': stock.condition_label(r['condition']),
            'locationId': r['location_id'],
            'locationLabel': loc_label,
            'receivedOn': r['received_on'],
            'receivedUnknown': bool(r['received_unknown']),
            'payoutOrder': r['payout_order'],
            'costPending': bool(r['cost_pending']),
        })
    handles = []
    for link in row['links']:
        htot = c.execute(
            '''SELECT COALESCE(SUM(qty_remaining),0) AS qty,
                      COALESCE(SUM(qty_remaining * unit_cost),0) AS amount,
                      COUNT(*) AS n
               FROM lots WHERE customer_product_id=?''',
            (link['id'],),
        ).fetchone()
        handles.append({
            **link,
            'embroideryLabel': stock.embroidery_label(link['embroidery']),
            'stockQty': int(htot['qty'] or 0),
            'stockAmount': int(htot['amount'] or 0),
            'lotCount': int(htot['n'] or 0),
        })
    return {
        **row,
        'stockQty': totals['qty'],
        'stockAmount': totals['amount'],
        'lotCount': totals['lotCount'],
        'hasStock': totals['lotCount'] > 0,
        'handles': handles,
        'lots': lots,
    }


def _bucket_for_code(code):
    t = stock.text(code)
    if not t:
        return 'blank'
    if t == CUSTOM_ORDER_CODE:
        return 'customOrder'
    if t == UNKNOWN_CODE:
        return 'unknown'
    return 'normal'


def list_duplicate_groups(c):
    """品番重複グループと、別注／不明／品番なしの一覧を返す。

    通常品番のみ「同一品番＝候補グループ」。別注・不明・品番なしは
    その理由だけでは同一商品とせず、手動確認用に分けて列挙する。
    """
    rows = list(c.execute(
        '''SELECT id, code, name, color, category, supplier_id, active, merged_into_id
           FROM products ORDER BY id'''
    ))
    live = [r for r in rows if r['merged_into_id'] in (None, '')]
    by_code = {}
    special = {'customOrder': [], 'unknown': [], 'blank': []}
    for r in live:
        bucket = _bucket_for_code(r['code'])
        if bucket == 'normal':
            by_code.setdefault(stock.text(r['code']), []).append(int(r['id']))
        else:
            special[bucket].append(int(r['id']))

    normal_groups = []
    for code, ids in sorted(by_code.items(), key=lambda x: x[0]):
        if len(ids) < 2:
            continue
        cards = [_product_compare_card(c, pid) for pid in ids]
        normal_groups.append({
            'kind': 'normal',
            'code': code,
            'codeDisplay': code,
            'productIds': ids,
            'products': cards,
            'autoSame': True,
            'note': '同じ品番で複数の商品IDがあります。統合するか、別商品のまま残すかを選べます。',
        })

    def special_section(kind, label, note, ids):
        return {
            'kind': kind,
            'code': label,
            'codeDisplay': label,
            'productIds': ids,
            'products': [_product_compare_card(c, pid) for pid in ids],
            'autoSame': False,
            'note': note,
        }

    sections = []
    if special['customOrder']:
        sections.append(special_section(
            'customOrder', CUSTOM_ORDER_CODE,
            '品番「別注」は通常の品番重複とは別扱いです。別注という理由だけでは同一商品と判断しません。'
            '顧客・商品名などを見て、必要な組み合わせだけ手動で統合してください。',
            special['customOrder'],
        ))
    if special['unknown']:
        sections.append(special_section(
            'unknown', UNKNOWN_CODE,
            '品番「不明」は通常の品番重複とは別扱いです。不明という理由だけでは同一商品と判断しません。',
            special['unknown'],
        ))
    if special['blank']:
        sections.append(special_section(
            'blank', stock.BLANK_SKU_DISPLAY,
            '品番なしは通常の品番重複とは別扱いです。空欄という理由だけでは同一商品と判断しません。',
            special['blank'],
        ))

    return {
        'normalGroups': normal_groups,
        'specialSections': sections,
        'normalGroupCount': len(normal_groups),
        'specialProductCount': sum(len(s['productIds']) for s in sections),
    }


def _fingerprint(c, product_ids):
    ids = sorted(int(x) for x in product_ids)
    parts = []
    for pid in ids:
        p = c.execute(
            'SELECT id, code, name, color, category, supplier_id, active, updated_at, merged_into_id '
            'FROM products WHERE id=?',
            (pid,),
        ).fetchone()
        if not p:
            parts.append(f'missing:{pid}')
            continue
        sizes = [r['label'] for r in c.execute(
            'SELECT label FROM product_sizes WHERE product_id=? ORDER BY sort_order,id', (pid,)
        )]
        subs = [r['supplier_id'] for r in c.execute(
            'SELECT supplier_id FROM product_suppliers WHERE product_id=? ORDER BY sort_order,id',
            (pid,),
        )]
        links = list(c.execute(
            'SELECT id, customer_id, embroidery, is_standard, active FROM customer_products '
            'WHERE product_id=? ORDER BY id',
            (pid,),
        ))
        lots = list(c.execute(
            '''SELECT l.id, l.customer_product_id, l.qty_remaining, l.unit_cost, l.updated_at
               FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
               WHERE cp.product_id=? ORDER BY l.id''',
            (pid,),
        ))
        parts.append(json.dumps({
            'p': dict(p),
            'sizes': sizes,
            'subs': subs,
            'links': [dict(x) for x in links],
            'lots': [dict(x) for x in lots],
        }, ensure_ascii=False, sort_keys=True, default=str))
    return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()


def _parse_id_list(raw, label):
    if not isinstance(raw, list) or not raw:
        raise ValueError(f'{label}を選んでください。')
    out = []
    seen = set()
    for item in raw:
        if isinstance(item, bool) or item in ('', None):
            raise ValueError(f'{label}の指定が不正です。')
        if isinstance(item, int):
            vid = item
        elif isinstance(item, str) and item.isdigit():
            vid = int(item)
        else:
            raise ValueError(f'{label}の指定が不正です。')
        if vid <= 0:
            raise ValueError(f'{label}の指定が不正です。')
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


def _handle_diff(keep_link, source_link):
    diffs = []
    if bool(keep_link['isStandard']) != bool(source_link['isStandard']):
        diffs.append({
            'field': 'isStandard',
            'label': '標準仕様',
            'keep': bool(keep_link['isStandard']),
            'source': bool(source_link['isStandard']),
        })
    if bool(keep_link['active']) != bool(source_link['active']):
        diffs.append({
            'field': 'active',
            'label': '使用状態',
            'keep': bool(keep_link['active']),
            'source': bool(source_link['active']),
        })
    return diffs


def preview_merge(c, body):
    product_ids = _parse_id_list(body.get('productIds'), '統合対象の商品')
    if len(product_ids) < 2:
        raise ValueError('統合するには商品を2件以上選んでください。統合せず残す場合はこのまま閉じてください。')
    keep_id = body.get('keepProductId')
    if keep_id in ('', None):
        keep_id = product_ids[0]
    elif isinstance(keep_id, str) and keep_id.isdigit():
        keep_id = int(keep_id)
    if not isinstance(keep_id, int) or keep_id not in product_ids:
        raise ValueError('残す商品IDを、統合対象の中から選んでください。')

    cards = {pid: _product_compare_card(c, pid) for pid in product_ids}
    for pid, card in cards.items():
        if card.get('mergedIntoId'):
            raise ValueError(f'商品ID {pid} は既に統合済みです（統合先 {card["mergedIntoId"]}）。')

    codes = {stock.text(cards[pid]['code']) for pid in product_ids}
    if len(codes) > 1:
        raise ValueError('品番が異なる商品同士は、この画面では統合できません。')
    code = next(iter(codes))
    bucket = _bucket_for_code(code)

    # 属性候補
    name_choices = sorted({cards[pid]['name'] for pid in product_ids})
    color_choices = sorted({cards[pid]['color'] or '' for pid in product_ids})
    category_choices = list(stock.PRODUCT_CATEGORY_OPTIONS)
    for cat in sorted({cards[pid].get('category') or '' for pid in product_ids}):
        if cat not in category_choices:
            category_choices.append(cat)
    size_union = []
    for pid in product_ids:
        for label in cards[pid]['sizes']:
            if label not in size_union:
                size_union.append(label)

    # 仕入先候補（通常＋サブ）
    supplier_ids = []
    for pid in product_ids:
        card = cards[pid]
        if card.get('supplierId') is not None and card['supplierId'] not in supplier_ids:
            supplier_ids.append(card['supplierId'])
        for s in card.get('subSuppliers') or []:
            if s['id'] not in supplier_ids:
                supplier_ids.append(s['id'])

    # 取扱の突合
    handle_moves = []
    handle_conflicts = []
    keep_by_key = {
        (l['customerId'], stock.normalize_embroidery(l['embroidery'])): l
        for l in cards[keep_id]['handles']
    }
    for pid in product_ids:
        if pid == keep_id:
            continue
        for link in cards[pid]['handles']:
            key = (link['customerId'], stock.normalize_embroidery(link['embroidery']))
            # 加工なし('') と 加工未整理 は別キーのまま（normalizeしない限り別）
            # PENDING is stored as literal '加工未整理', empty is ''
            existing = keep_by_key.get(key)
            move = {
                'sourceProductId': pid,
                'sourceHandleId': link['id'],
                'customerId': link['customerId'],
                'customerName': link['customerName'],
                'embroidery': link['embroidery'],
                'embroideryLabel': stock.embroidery_label(link['embroidery']),
                'lotCount': link['lotCount'],
                'stockQty': link['stockQty'],
                'stockAmount': link['stockAmount'],
            }
            if existing:
                diffs = _handle_diff(existing, link)
                move['action'] = 'mergeInto'
                move['keepHandleId'] = existing['id']
                move['diffs'] = diffs
                if diffs:
                    handle_conflicts.append(move)
            else:
                move['action'] = 'reattach'
                move['keepHandleId'] = None
                move['diffs'] = []
            handle_moves.append(move)

    totals = product_stock_totals(c, product_ids)
    fingerprint = _fingerprint(c, product_ids)

    # リクエストの希望値（プレビュー再計算用）
    name = body.get('name')
    if name is None:
        name = cards[keep_id]['name']
    else:
        name = stock.need(name, '商品名', 80)
    color = body.get('color')
    if color is None:
        color = cards[keep_id]['color']
    else:
        color = stock.optional(color, '色', 40)
    category = body.get('category')
    if category is None:
        category = cards[keep_id].get('category') or ''
    else:
        category = stock.normalize_category(category)

    primary = body.get('supplierId', cards[keep_id]['supplierId'])
    if primary in ('', None):
        primary = None
    elif isinstance(primary, str) and primary.isdigit():
        primary = int(primary)
    if primary is not None:
        if not isinstance(primary, int) or not c.execute(
            'SELECT id FROM suppliers WHERE id=?', (primary,)
        ).fetchone():
            raise ValueError('通常の仕入先を、仕入先台帳から選んでください。')
        if supplier_ids and primary not in supplier_ids:
            # 候補外も明示選択可だが存在確認済み
            pass

    sub_raw = body.get('subSupplierIds')
    if sub_raw is None:
        subs = [sid for sid in supplier_ids if sid != primary]
    else:
        subs = []
        if not isinstance(sub_raw, list):
            raise ValueError('サブ仕入先の指定が不正です。')
        for item in sub_raw:
            if isinstance(item, str) and item.isdigit():
                item = int(item)
            if not isinstance(item, int) or not c.execute(
                'SELECT id FROM suppliers WHERE id=?', (item,)
            ).fetchone():
                raise ValueError('サブ仕入先を、仕入先台帳から選んでください。')
            if item == primary:
                raise ValueError('通常仕入先と同じ仕入先をサブにできません。')
            if item not in subs:
                subs.append(item)

    sizes = body.get('sizes')
    if sizes is None:
        sizes = list(size_union)
    else:
        sizes = stock._sizes({'sizes': sizes})
    # 在庫があるサイズは外せない（数量を消さないため）
    sized_with_stock = set()
    for pid in product_ids:
        for lot in cards[pid]['lots']:
            if lot['qty'] or lot.get('id'):
                sized_with_stock.add(lot['size'])
    missing_sizes = [s for s in size_union if s in sized_with_stock and s not in sizes]
    if missing_sizes:
        raise ValueError(
            '在庫があるサイズは外せません: ' + '、'.join(missing_sizes)
            + '（LLと2Lなどは別サイズのまま引き継ぎます）'
        )

    return {
        'bucket': bucket,
        'code': code,
        'codeDisplay': stock.product_code_display(code, category),
        'autoSame': bucket == 'normal',
        'productIds': product_ids,
        'keepProductId': keep_id,
        'products': [cards[pid] for pid in product_ids],
        'name': name,
        'color': color,
        'category': category,
        'nameChoices': name_choices,
        'colorChoices': color_choices,
        'categoryChoices': category_choices,
        'sizeUnion': size_union,
        'sizes': sizes,
        'supplierIds': supplier_ids,
        'supplierId': primary,
        'subSupplierIds': subs,
        'suppliers': [
            stock.supplier_row(c, sid) for sid in supplier_ids
            if c.execute('SELECT id FROM suppliers WHERE id=?', (sid,)).fetchone()
        ],
        'handleMoves': handle_moves,
        'handleConflicts': handle_conflicts,
        'unresolvedConflicts': len(handle_conflicts),
        'totals': totals,
        'fingerprint': fingerprint,
        'notes': [
            '在庫ロットは付け替えるだけで、数量の合算・削除・再加算はしません。',
            '「LL」と「2L」など表記が違うサイズは別サイズのまま引き継ぎます。',
            '加工なしと加工未整理は別の取扱として残します。',
            '統合元の商品は一覧・選択候補から外れ、統合先IDで追跡できます。',
        ],
    }


def _actor_name(body):
    actor = body.get('_actor') or {}
    if isinstance(actor, dict):
        return stock.text(actor.get('name') or '')[:80]
    return ''


def _conflict_resolutions(body, conflicts):
    """未解決の取扱差異へのユーザー回答。未指定なら拒否。"""
    raw = body.get('conflictResolutions') or {}
    if not isinstance(raw, dict):
        raise ValueError('取扱の差異の解決方法が不正です。')
    resolved = {}
    for move in conflicts:
        key = str(move['sourceHandleId'])
        choice = raw.get(key) or raw.get(move['sourceHandleId'])
        if choice not in ('keep', 'source'):
            raise ValueError(
                f'顧客「{move["customerName"]}」／{move["embroideryLabel"]} の取扱に差異があります。'
                f'残す側の設定を確認画面で選んでください。'
            )
        resolved[move['sourceHandleId']] = choice
    return resolved


def execute_merge(c, body, *, db_path=None):
    token = stock.text(body.get('token'))
    if not token:
        raise ValueError('操作トークンがありません。画面からやり直してください。')
    existing = c.execute(
        'SELECT id, result_json FROM product_merges WHERE client_token=?', (token,)
    ).fetchone()
    if existing:
        try:
            payload = json.loads(existing['result_json'] or '{}')
        except json.JSONDecodeError:
            payload = {}
        payload['already'] = True
        payload['mergeId'] = existing['id']
        return payload

    preview = preview_merge(c, body)
    if preview['unresolvedConflicts'] and not body.get('conflictResolutions'):
        raise ValueError('取扱の差異が未解決です。確認画面で残す設定を選んでください。')
    resolutions = _conflict_resolutions(body, preview['handleConflicts']) if preview['handleConflicts'] else {}

    expected_fp = stock.text(body.get('fingerprint'))
    if not expected_fp or expected_fp != preview['fingerprint']:
        raise stock.StockConflict({
            'error': '統合対象の商品または在庫内訳が変わったため、確認し直してください。',
            'code': 'product_merge_stale',
            'fingerprint': preview['fingerprint'],
        })

    product_ids = preview['productIds']
    keep_id = preview['keepProductId']
    before = product_stock_totals(c, product_ids)

    backup_path = ''
    if db_path:
        try:
            dest = stock.backup_sqlite(Path(db_path), tag='pre-product-merge')
            backup_path = str(dest) if dest else ''
        except stock.BackupError:
            raise
        except Exception as e:
            print(
                f'product_merge backup failed before merge: {type(e).__name__}: {e}',
                flush=True,
            )
            raise stock.BackupError(stock.BACKUP_FAILED_MESSAGE, cause=e) from e

    t = stock.stamp()
    # 残す商品のマスタ更新
    stock.save_product(c, {
        'id': keep_id,
        'code': preview['code'],
        'name': preview['name'],
        'color': preview['color'],
        'category': preview['category'],
        'supplierId': preview['supplierId'],
        'subSupplierIds': preview['subSupplierIds'],
        'sizes': preview['sizes'],
        'links': [
            {
                'customerId': l['customerId'],
                'embroidery': l['embroidery'],
                'isStandard': l['isStandard'],
                'active': l['active'],
            }
            for l in stock.product_row(c, keep_id)['links']
        ],
        'active': True,
    })

    source_snapshots = []
    for pid in product_ids:
        if pid == keep_id:
            continue
        source_snapshots.append({
            'id': pid,
            'snapshot': _product_compare_card(c, pid),
        })
        _merge_one_source(
            c, keep_id=keep_id, source_id=pid, resolutions=resolutions, when=t,
        )

    after = product_stock_totals(c, [keep_id])
    # 統合元は在庫ゼロのはず
    for pid in product_ids:
        if pid == keep_id:
            continue
        left = product_stock_totals(c, [pid])
        if left['qty'] != 0 or left['lotCount'] != 0:
            raise ValueError('統合後も統合元に在庫が残っています。処理を取り消します。')

    if after['qty'] != before['qty'] or after['amount'] != before['amount']:
        raise ValueError(
            f'統合前後で在庫数量または金額が変わりました'
            f'（数量 {before["qty"]}→{after["qty"]}、金額 {before["amount"]}→{after["amount"]}）。'
            f'処理を取り消します。'
        )

    actor = _actor_name(body)
    result = {
        'already': False,
        'keepProductId': keep_id,
        'mergedProductIds': [p['id'] for p in source_snapshots],
        'beforeQty': before['qty'],
        'afterQty': after['qty'],
        'beforeAmount': before['amount'],
        'afterAmount': after['amount'],
        'backupPath': backup_path,
        'product': stock.product_row(c, keep_id),
    }
    c.execute(
        '''INSERT INTO product_merges(
             keep_product_id, actor_name, client_token,
             before_qty, after_qty, before_amount, after_amount,
             fingerprint, request_json, result_json, backup_path, created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            keep_id, actor, token,
            before['qty'], after['qty'], before['amount'], after['amount'],
            preview['fingerprint'],
            json.dumps({
                'productIds': product_ids,
                'keepProductId': keep_id,
                'name': preview['name'],
                'color': preview['color'],
                'category': preview['category'],
                'supplierId': preview['supplierId'],
                'subSupplierIds': preview['subSupplierIds'],
                'sizes': preview['sizes'],
                'conflictResolutions': {
                    str(k): v for k, v in resolutions.items()
                },
            }, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            backup_path,
            t,
        ),
    )
    merge_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    for item in source_snapshots:
        c.execute(
            '''INSERT INTO product_merge_sources(merge_id, source_product_id, source_snapshot_json)
               VALUES(?,?,?)''',
            (merge_id, item['id'], json.dumps(item['snapshot'], ensure_ascii=False, default=str)),
        )
    result['mergeId'] = merge_id
    c.execute(
        'UPDATE product_merges SET result_json=? WHERE id=?',
        (json.dumps(result, ensure_ascii=False), merge_id),
    )
    return result


def _merge_one_source(c, *, keep_id, source_id, resolutions, when):
    keep_links = {
        (r['customer_id'], stock.normalize_embroidery(r['embroidery'])): r
        for r in c.execute('SELECT * FROM customer_products WHERE product_id=?', (keep_id,))
    }
    source_links = list(c.execute(
        'SELECT * FROM customer_products WHERE product_id=? ORDER BY id', (source_id,)
    ))

    for link in source_links:
        key = (link['customer_id'], stock.normalize_embroidery(link['embroidery']))
        existing = keep_links.get(key)
        if existing:
            # ロット・発注行などを残す取扱へ付け替え
            _remap_handle_refs(c, from_handle_id=link['id'], to_handle_id=existing['id'])
            choice = resolutions.get(link['id'], 'keep')
            if choice == 'source':
                # 標準フラグは顧客内で1つだけ
                if link['is_standard']:
                    c.execute(
                        'UPDATE customer_products SET is_standard=0, updated_at=? '
                        'WHERE product_id=? AND customer_id=?',
                        (when, keep_id, link['customer_id']),
                    )
                c.execute(
                    'UPDATE customer_products SET is_standard=?, active=?, updated_at=? WHERE id=?',
                    (link['is_standard'], link['active'], when, existing['id']),
                )
            c.execute('DELETE FROM customer_products WHERE id=?', (link['id'],))
        else:
            # 取扱ごと統合先へ付け替え（加工キーはそのまま＝加工なしと加工未整理を同一化しない）
            # 顧客内の標準仕様は1件だけ。統合先に既にあれば付け替え側の標準を外す。
            is_standard = link['is_standard']
            if is_standard and c.execute(
                'SELECT 1 FROM customer_products WHERE product_id=? AND customer_id=? AND is_standard=1',
                (keep_id, link['customer_id']),
            ).fetchone():
                is_standard = 0
                c.execute(
                    'UPDATE customer_products SET is_standard=0 WHERE id=?', (link['id'],),
                )
            try:
                c.execute(
                    'UPDATE customer_products SET product_id=?, is_standard=?, updated_at=? WHERE id=?',
                    (keep_id, is_standard, when, link['id']),
                )
            except sqlite3.IntegrityError:
                # 競合時は再読込してマージ（同一顧客・加工、または標準ユニーク）
                existing = c.execute(
                    'SELECT * FROM customer_products WHERE customer_id=? AND product_id=? AND embroidery=?',
                    (link['customer_id'], keep_id, link['embroidery']),
                ).fetchone()
                if not existing:
                    raise
                _remap_handle_refs(c, from_handle_id=link['id'], to_handle_id=existing['id'])
                c.execute('DELETE FROM customer_products WHERE id=?', (link['id'],))
            else:
                keep_links[key] = c.execute(
                    'SELECT * FROM customer_products WHERE id=?', (link['id'],)
                ).fetchone()

    # サイズは preview で統合先へ既に反映済み。統合元サイズ行は削除可。
    c.execute('DELETE FROM product_sizes WHERE product_id=?', (source_id,))
    # サブ仕入先は save_product で統合先へ反映済み。統合元の関連を削除。
    c.execute('DELETE FROM product_suppliers WHERE product_id=?', (source_id,))

    # 発注ヘッダ以外で商品ID直参照を付け替え
    _remap_product_refs(c, from_product_id=source_id, to_product_id=keep_id)

    c.execute(
        'UPDATE products SET active=0, merged_into_id=?, merged_at=?, updated_at=? WHERE id=?',
        (keep_id, when, when, source_id),
    )


def _remap_handle_refs(c, *, from_handle_id, to_handle_id):
    if from_handle_id == to_handle_id:
        return
    c.execute(
        'UPDATE lots SET customer_product_id=?, updated_at=? WHERE customer_product_id=?',
        (to_handle_id, stock.stamp(), from_handle_id),
    )
    tables = _tables(c)
    if 'purchase_order_lines' in tables:
        c.execute(
            'UPDATE purchase_order_lines SET customer_product_id=? WHERE customer_product_id=?',
            (to_handle_id, from_handle_id),
        )
    if 'legacy_csv_row_maps' in tables:
        c.execute(
            'UPDATE legacy_csv_row_maps SET customer_product_id=?, updated_at=? WHERE customer_product_id=?',
            (to_handle_id, stock.stamp(), from_handle_id),
        )
    if 'product_ledger_row_maps' in tables:
        c.execute(
            'UPDATE product_ledger_row_maps SET customer_product_id=?, updated_at=? WHERE customer_product_id=?',
            (to_handle_id, stock.stamp(), from_handle_id),
        )


def _remap_product_refs(c, *, from_product_id, to_product_id):
    tables = _tables(c)
    if 'purchase_order_lines' in tables:
        c.execute(
            'UPDATE purchase_order_lines SET product_id=? WHERE product_id=?',
            (to_product_id, from_product_id),
        )
    if 'legacy_csv_row_maps' in tables:
        c.execute(
            'UPDATE legacy_csv_row_maps SET product_id=?, updated_at=? WHERE product_id=?',
            (to_product_id, stock.stamp(), from_product_id),
        )
    if 'product_ledger_row_maps' in tables:
        c.execute(
            'UPDATE product_ledger_row_maps SET product_id=?, updated_at=? WHERE product_id=?',
            (to_product_id, stock.stamp(), from_product_id),
        )
    # 連鎖統合: 既にこの商品を指す統合元があれば、最終先を更新
    c.execute(
        'UPDATE products SET merged_into_id=?, updated_at=? WHERE merged_into_id=?',
        (to_product_id, stock.stamp(), from_product_id),
    )
