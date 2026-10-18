"""旧システム CSV（item_report 形式）の一括取込: プレビュー → 確認後登録。

商品台帳.xlsx の同時取込は扱わない。パス固定ではなく、利用者が選んだ CSV を読む。
プレビュー段階では DB を変更しない。登録・更新は確定対象のみを一括トランザクションで反映する。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
from collections import defaultdict

import stock

# 旧 CSV 列（実ファイル先頭行に合わせる）
REQUIRED_COLUMNS = (
    '新古', '仕入先', '顧客', '商品名', '商品コード', 'サイズ', '仕入単価',
    '在庫', '拠点', '棚',
)
# 使わない列（明示。購入・出荷・補正からの再計算はしない）
IGNORED_QTY_COLUMNS = ('購入数量', '購入金額', '出荷数', '出荷金額', '在庫金額', '補正在庫数', '営業所コード')

PENDING_EMBROIDERY = '加工未整理'
LEGACY_IMPORT_NOTE = '旧システムCSV一括取込'
COMPANY_FORMS = (
    ('㈱', '株式会社'),
    ('㈲', '有限会社'),
    ('㈾', '合同会社'),
    ('(株)', '株式会社'),
    ('（株）', '株式会社'),
    ('(有)', '有限会社'),
    ('（有）', '有限会社'),
)

STATUS_CREATE = 'create'          # 新規追加（在庫なし→登録）
STATUS_REUSE = 'reuse'            # 既存マスター使用（在庫新規）
STATUS_QTY_DIFF = 'qty_diff'      # 数量差異
STATUS_UNCHANGED = 'unchanged'    # 変更なし
STATUS_REVIEW = 'needs_review'    # 要確認
STATUS_HOLD = 'hold'              # 保留（CSV内重複など）

STATUS_LABELS = {
    STATUS_CREATE: '新規追加',
    STATUS_REUSE: '既存使用',
    STATUS_QTY_DIFF: '数量差異',
    STATUS_UNCHANGED: '変更なし',
    STATUS_REVIEW: '要確認',
    STATUS_HOLD: '保留',
}


def _collapse_space(value):
    return re.sub(r'[\s\u3000]+', '', stock.text(value))


def company_norm_key(name):
    """㈱／株式会社などの表記ゆれを畳んだ比較キー（自動統合には使わず候補検出用）。"""
    t = _collapse_space(name)
    for short, long in COMPANY_FORMS:
        t = t.replace(short, long)
    return t.casefold()


def looks_like_company_form_variant(a, b):
    if not a or not b:
        return False
    if stock.text(a) == stock.text(b):
        return False
    return company_norm_key(a) == company_norm_key(b)


def decode_csv_bytes(blob):
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise ValueError('CSVファイルを選んでください。')
    if len(blob) > 8_000_000:
        raise ValueError('ファイルが大きすぎます。')
    raw = bytes(blob)
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw.decode('utf-8-sig'), 'utf-8-sig'
    for enc in ('utf-8', 'cp932'):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError('文字コードを判別できませんでした（UTF-8 / CP932）。')


def parse_csv_text(text):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError('CSVの見出し行がありません。')
    fields = [stock.text(h) for h in reader.fieldnames]
    missing = [col for col in REQUIRED_COLUMNS if col not in fields]
    if missing:
        raise ValueError('必要な列がありません: ' + '、'.join(missing))
    rows = []
    for i, raw in enumerate(reader, start=2):  # 1=見出し
        item = {k: (raw.get(k) if raw.get(k) is not None else '') for k in fields}
        rows.append((i, item))
    if not rows:
        raise ValueError('読み込む行がありません。')
    return fields, rows


def decode_upload(body):
    raw = body.get('file') if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('CSVファイルを選んでください。')
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as e:
        raise ValueError('CSVファイルを選んでください。') from e
    text, encoding = decode_csv_bytes(blob)
    fields, rows = parse_csv_text(text)
    name = stock.text(body.get('name') or body.get('fileName') or 'item_report.csv') or 'item_report.csv'
    sha = hashlib.sha256(blob).hexdigest()
    return {
        'blob': blob,
        'text': text,
        'encoding': encoding,
        'fields': fields,
        'rows': rows,
        'fileName': name,
        'fileSize': len(blob),
        'fileSha256': sha,
    }


def strip_used_marker(name):
    t = stock.text(name)
    if t.startswith('＊') or t.startswith('*'):
        return t[1:].lstrip(), True
    return t, False


def parse_row_condition(item, row_no):
    name = stock.text(item.get('商品名'))
    _, star = strip_used_marker(name)
    raw = stock.text(item.get('新古'))
    try:
        condition = stock.parse_condition(raw, default=None) if raw else None
    except ValueError:
        return None, [f'{row_no}行目：新古「{raw}」が不正です。']
    reasons = []
    if condition is None and not star:
        reasons.append(f'{row_no}行目：新古が空です。')
        return None, reasons
    if condition is None and star:
        condition = stock.CONDITION_USED
    if star and condition == stock.CONDITION_NEW:
        # ＊は中古。新古列が新品なら矛盾→要確認（判定上は中古として扱う）
        reasons.append(f'{row_no}行目：商品名の「＊」は中古ですが、新古列が新品です。')
        return stock.CONDITION_USED, reasons
    if star and condition == stock.CONDITION_USED:
        return condition, reasons
    return condition, reasons


def parse_row_qty_cost(item, row_no):
    reasons = []
    try:
        qty = stock.parse_qty(item.get('在庫'), '在庫', allow_zero=True, allow_negative=True)
    except ValueError as e:
        reasons.append(f'{row_no}行目：{e}')
        qty = None
    try:
        cost = stock.parse_money(item.get('仕入単価'), '仕入単価')
    except ValueError as e:
        reasons.append(f'{row_no}行目：{e}')
        cost = None
    return qty, cost, reasons


def find_person_matches(c, table, name):
    exact = list(c.execute(f'SELECT id,name,active FROM {table} WHERE name=?', (name,)))
    if exact:
        return exact, 'exact'
    key = company_norm_key(name)
    if not key:
        return [], 'empty'
    soft = []
    for row in c.execute(f'SELECT id,name,active FROM {table}'):
        if looks_like_company_form_variant(name, row['name']):
            soft.append(row)
    return soft, 'form_variant' if soft else 'none'


def find_products_by_code(c, code):
    return list(c.execute(
        '''SELECT p.id,p.code,p.name,p.color,p.supplier_id,p.active,s.name AS supplier_name
           FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id
           WHERE p.code=? ORDER BY p.id''',
        (code,),
    ))


def location_resolve(c, site_name, shelf_name, default_site_name):
    """Return (site_id|None, shelf_id|None, effective_site, shelf, flags)."""
    site = stock.text(site_name)
    shelf = stock.text(shelf_name)
    empty_site = not site
    if empty_site:
        site = stock.text(default_site_name) or stock.UNCONFIRMED_SITE_NAME
    site_row = c.execute(
        'SELECT id,name,active FROM locations WHERE name=? AND parent_id IS NULL', (site,)
    ).fetchone()
    shelf_row = None
    if site_row and shelf:
        shelf_row = c.execute(
            'SELECT id,name,active FROM locations WHERE name=? AND parent_id=?',
            (shelf, site_row['id']),
        ).fetchone()
    return {
        'siteName': site,
        'shelfName': shelf,
        'siteEmpty': empty_site,
        'siteId': site_row['id'] if site_row else None,
        'shelfId': shelf_row['id'] if shelf_row else None,
        'siteActive': bool(site_row['active']) if site_row else None,
        'shelfActive': bool(shelf_row['active']) if shelf_row else None,
        'siteExists': site_row is not None,
        'shelfExists': shelf_row is not None if shelf else True,
        'locationId': (shelf_row['id'] if shelf_row else (site_row['id'] if site_row and not shelf else None)),
    }


def db_fingerprint(c):
    """Detect master/stock drift between preview and commit."""
    parts = []
    for table in ('customers', 'suppliers', 'products', 'customer_products', 'locations', 'lots'):
        row = c.execute(f'SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS m FROM {table}').fetchone()
        parts.append(f'{table}:{row["n"]}:{row["m"]}')
    lot_sum = c.execute('SELECT COALESCE(SUM(qty_remaining),0) AS q FROM lots').fetchone()['q']
    parts.append(f'qty:{lot_sum}')
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:40]


def previous_imports_for_file(c, sha):
    rows = c.execute(
        '''SELECT id,file_name,file_size,row_count,basis_date,default_site_name,actor_name,
                  preview_at,committed_at,commit_token,result_json,created_at
           FROM legacy_csv_import_runs WHERE file_sha256=? ORDER BY id DESC LIMIT 5''',
        (sha,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            result = json.loads(r['result_json'] or '{}')
        except json.JSONDecodeError:
            result = {}
        out.append({
            'id': r['id'],
            'fileName': r['file_name'],
            'fileSize': r['file_size'],
            'rowCount': r['row_count'],
            'basisDate': r['basis_date'],
            'defaultSiteName': r['default_site_name'],
            'actorName': r['actor_name'],
            'previewAt': r['preview_at'],
            'committedAt': r['committed_at'],
            'commitToken': r['commit_token'],
            'result': result,
            'createdAt': r['created_at'],
        })
    return out


def _person_preview(c, table, label, name, row_no):
    name = stock.text(name)
    info = {
        'label': label, 'name': name, 'action': 'missing', 'id': None,
        'candidates': [], 'reasons': [],
    }
    if not name:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：{label}名が空です。架空名は作りません。')
        return info
    matches, how = find_person_matches(c, table, name)
    if how == 'exact' and len(matches) == 1:
        row = matches[0]
        if not row['active']:
            info['action'] = 'review'
            info['id'] = row['id']
            info['reasons'].append(f'{row_no}行目：{label}「{name}」は停止中です。重複追加・無断再開しません。')
        else:
            info['action'] = 'reuse'
            info['id'] = row['id']
        return info
    if how == 'form_variant':
        info['action'] = 'review'
        info['candidates'] = [{'id': m['id'], 'name': m['name'], 'active': bool(m['active'])} for m in matches]
        info['reasons'].append(
            f'{row_no}行目：{label}「{name}」は㈱／株式会社などの表記差の候補があります。別会社として勝手に統合しません。'
        )
        return info
    info['action'] = 'create'
    return info


def _product_preview(c, item, supplier_info, row_no):
    code = stock.text(item.get('商品コード'))
    name = stock.text(item.get('商品名'))
    size = item.get('サイズ')
    size = size if isinstance(size, str) else ('' if size is None else str(size))
    # サイズは前後空白のみ整理。先頭ゼロ・ハイフン・数字はそのまま。
    size_label = size.strip()
    info = {
        'code': code, 'name': name, 'size': size_label,
        'action': 'missing', 'id': None, 'candidates': [], 'reasons': [],
        'sizeAction': 'ok', 'nameDiff': False, 'supplierConflict': False,
    }
    if not code:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：品番（商品コード）が空です。')
        return info
    if not name:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：商品名が空です。')
        return info
    if not size_label:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：サイズが空です。')
        return info
    matches = find_products_by_code(c, code)
    if not matches:
        info['action'] = 'create'
        info['sizeAction'] = 'add'
        return info
    supplier_id = supplier_info.get('id')
    narrowed = matches
    if supplier_id and len(matches) > 1:
        by_sup = [m for m in matches if m['supplier_id'] == supplier_id]
        if by_sup:
            narrowed = by_sup
    if len(narrowed) > 1:
        # Prefer exact name match among duplicates
        by_name = [m for m in narrowed if stock.text(m['name']) == name]
        if len(by_name) == 1:
            narrowed = by_name
        else:
            info['action'] = 'review'
            info['candidates'] = [
                {
                    'id': m['id'], 'code': m['code'], 'name': m['name'], 'color': m['color'],
                    'supplierName': m['supplier_name'] or '', 'active': bool(m['active']),
                }
                for m in narrowed
            ]
            info['reasons'].append(f'{row_no}行目：同じ品番の商品が複数あります。候補から選んでください。')
            return info
    prod = narrowed[0]
    if not prod['active']:
        info['action'] = 'review'
        info['id'] = prod['id']
        info['reasons'].append(f'{row_no}行目：商品「{code}」は停止中です。重複追加・無断再開しません。')
        return info
    if supplier_id and prod['supplier_id'] not in (None, supplier_id):
        info['supplierConflict'] = True
        info['action'] = 'review'
        info['id'] = prod['id']
        info['reasons'].append(
            f'{row_no}行目：品番「{code}」の仕入先が既存と異なります（既存: {prod["supplier_name"] or "未設定"}）。'
        )
        return info
    info['action'] = 'reuse'
    info['id'] = prod['id']
    if stock.text(prod['name']) != name:
        info['nameDiff'] = True  # 名・色表記差だけで除外しない
    existing_sizes = {
        r['label'] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (prod['id'],))
    }
    if size_label not in existing_sizes:
        info['sizeAction'] = 'add'
    else:
        info['sizeAction'] = 'reuse'
    return info


def _handle_candidate(row):
    return {
        'id': row['id'],
        'embroidery': row['embroidery'],
        'embroideryLabel': stock.embroidery_label(row['embroidery']),
        'active': bool(row['active']),
    }


def _reuse_handle(info, row, *, pending_sort=True):
    """既存取扱を再利用。加工仕様キーは変更しない。"""
    emb = stock.normalize_embroidery(row['embroidery'])
    info['action'] = 'reuse'
    info['id'] = row['id']
    info['embroidery'] = emb
    info['embroideryLabel'] = stock.embroidery_label(row['embroidery'])
    info['pendingEmbroiderySort'] = pending_sort
    info['candidates'] = []
    info['reasons'] = []
    return info


def _handle_preview(c, customer_id, product_id, row_no, *, csv_source=None):
    """CSVに加工欄が無い前提の取扱照合。

    自動付与の「加工未整理」だけを理由に取込を止めない。
    顧客・商品が確定し有効取扱が「加工なし」1件だけならそれを再利用する。
    既存の加工仕様は書き換えない。整理待ちは pendingEmbroiderySort / csvSource で保持し照合を妨げない。
    """
    source = dict(csv_source or {})
    info = {
        'action': 'missing', 'id': None,
        'embroidery': PENDING_EMBROIDERY,
        'embroideryLabel': PENDING_EMBROIDERY,
        'candidates': [], 'reasons': [],
        # 実加工仕様キーとは別に、CSV由来の整理待ちを保持する
        'pendingEmbroiderySort': True,
        'csvSource': source,
    }
    if not customer_id or not product_id:
        info['action'] = 'defer'
        return info
    rows = list(c.execute(
        '''SELECT id,embroidery,active FROM customer_products
           WHERE customer_id=? AND product_id=? ORDER BY id''',
        (customer_id, product_id),
    ))
    if not rows:
        # 取扱なし → 加工未整理を追加（後から品名・品番で整理）
        info['action'] = 'create'
        return info

    active = [r for r in rows if r['active']]
    if not active:
        info['action'] = 'review'
        info['candidates'] = [_handle_candidate(r) for r in rows]
        info['reasons'].append(f'{row_no}行目：取扱が停止中のみです。無断再開しません。')
        return info

    if len(active) == 1:
        emb = stock.normalize_embroidery(active[0]['embroidery'])
        if emb == '' or emb == PENDING_EMBROIDERY:
            # 加工なし / 過去取込の加工未整理 → 再利用（重複作成しない・仕様は変えない）
            return _reuse_handle(info, active[0], pending_sort=True)
        # 具体的な加工仕様が1件だけ → CSVに加工欄が無いため自動確定しない
        info['action'] = 'review'
        info['candidates'] = [_handle_candidate(r) for r in active]
        info['reasons'].append(
            f'{row_no}行目：既存取扱の加工仕様は「{stock.embroidery_label(active[0]["embroidery"])}」です。'
            f'CSVに加工欄が無いため自動では選べません（候補から選んでください）。'
        )
        return info

    # 複数の有効取扱 → 特定不可。候補を具体表示（無断統合・削除しない）
    labels = [stock.embroidery_label(r['embroidery']) for r in active]
    info['action'] = 'review'
    info['candidates'] = [_handle_candidate(r) for r in active]
    info['reasons'].append(
        f'{row_no}行目：同一顧客×商品の取扱が複数あります'
        f'（{", ".join(labels)}）。CSVに加工欄が無いため特定できません。候補から選んでください。'
    )
    return info


def _stock_match(c, cp_id, size, location_id, condition, unit_cost):
    if not cp_id or not location_id or condition is None or unit_cost is None:
        return None
    lots = list(c.execute(
        '''SELECT id,qty_remaining,unit_cost,condition FROM lots
           WHERE customer_product_id=? AND size_label=? AND location_id=? AND condition=? AND unit_cost=?
           ORDER BY id''',
        (cp_id, size, location_id, condition, unit_cost),
    ))
    if not lots:
        return {'exists': False, 'lotIds': [], 'currentQty': 0, 'fingerprint': ''}
    qty = sum(int(l['qty_remaining']) for l in lots)
    fp = '|'.join(f"{l['id']}:{l['qty_remaining']}" for l in lots)
    return {
        'exists': True,
        'lotIds': [l['id'] for l in lots],
        'currentQty': qty,
        'fingerprint': fp,
        'lotCount': len(lots),
    }


def classify_row(c, row_no, item, *, default_site_name, selected_product_id=None, selected_handle_id=None):
    reasons = []
    customer = _person_preview(c, 'customers', '顧客', item.get('顧客'), row_no)
    supplier = _person_preview(c, 'suppliers', '仕入先', item.get('仕入先'), row_no)
    reasons.extend(customer['reasons'])
    reasons.extend(supplier['reasons'])

    condition, creasons = parse_row_condition(item, row_no)
    reasons.extend(creasons)
    qty, cost, qreasons = parse_row_qty_cost(item, row_no)
    reasons.extend(qreasons)

    product = _product_preview(c, item, supplier, row_no)
    if selected_product_id and isinstance(selected_product_id, int):
        prod = c.execute(
            '''SELECT p.id,p.code,p.name,p.color,p.supplier_id,p.active,s.name AS supplier_name
               FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?''',
            (selected_product_id,),
        ).fetchone()
        if prod and prod['active'] and prod['code'] == product['code']:
            product = {
                **product, 'action': 'reuse', 'id': prod['id'], 'reasons': [],
                'candidates': [],
            }
            existing_sizes = {
                r['label'] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (prod['id'],))
            }
            product['sizeAction'] = 'reuse' if product['size'] in existing_sizes else 'add'
    reasons.extend(product['reasons'])

    place = location_resolve(c, item.get('拠点'), item.get('棚'), default_site_name)
    if place['siteEmpty']:
        # 空→指定先（初期「未確認」）。プレビューに明示。
        pass
    if place['siteExists'] is False:
        place['action'] = 'create_site'
    elif place['siteActive'] is False:
        reasons.append(f'{row_no}行目：拠点「{place["siteName"]}」は停止中です。')
        place['action'] = 'review'
    else:
        place['action'] = 'reuse_site'
    if place['shelfName']:
        if place['shelfExists'] is False and place['action'] != 'review':
            place['action'] = 'create_shelf' if place.get('siteId') or place.get('action') == 'create_site' else place['action']
        elif place['shelfActive'] is False:
            reasons.append(f'{row_no}行目：棚「{place["shelfName"]}」は停止中です。')
            place['action'] = 'review'

    csv_source = {
        'sourceRowNo': row_no,
        'productCode': stock.text(item.get('商品コード')),
        'productName': stock.text(item.get('商品名')),
        # ＊は中古標記。加工としては扱わない（条件判定側で処理済み）
        'usedMarker': strip_used_marker(stock.text(item.get('商品名')))[1],
    }
    handle = _handle_preview(
        c, customer.get('id'), product.get('id'), row_no, csv_source=csv_source,
    )
    if selected_handle_id and isinstance(selected_handle_id, int):
        h = c.execute('SELECT id,embroidery,active,customer_id,product_id FROM customer_products WHERE id=?',
                      (selected_handle_id,)).fetchone()
        if h and h['active'] and h['customer_id'] == customer.get('id') and h['product_id'] == product.get('id'):
            handle = _reuse_handle(
                {
                    'action': 'reuse', 'id': None, 'embroidery': PENDING_EMBROIDERY,
                    'embroideryLabel': PENDING_EMBROIDERY, 'candidates': [], 'reasons': [],
                    'pendingEmbroiderySort': True, 'csvSource': csv_source,
                },
                h,
                pending_sort=True,
            )
    reasons.extend(handle['reasons'])

    stock_info = _stock_match(
        c, handle.get('id'), product.get('size'), place.get('locationId'), condition, cost,
    )

    status = STATUS_CREATE
    if reasons or any(
        x.get('action') == 'review' for x in (customer, supplier, product, handle)
    ) or place.get('action') == 'review' or condition is None or qty is None or cost is None:
        status = STATUS_REVIEW
    elif stock_info and stock_info['exists']:
        if stock_info['currentQty'] == qty:
            status = STATUS_UNCHANGED
        else:
            status = STATUS_QTY_DIFF
    else:
        # 在庫なし。マスターが既存なら reuse、新規要素があれば create
        master_new = any(
            x.get('action') == 'create' for x in (customer, supplier, product, handle)
        ) or place.get('action') in ('create_site', 'create_shelf')
        status = STATUS_CREATE if master_new else STATUS_REUSE

    display_name, star = strip_used_marker(stock.text(item.get('商品名')))
    return {
        'rowNo': row_no,
        'status': status,
        'statusLabel': STATUS_LABELS[status],
        'selected': False,  # qty_diff は初期非選択
        'condition': condition,
        'conditionLabel': stock.condition_label(condition) if condition else '',
        'qty': qty,
        'unitCost': cost,
        'customer': customer,
        'supplier': supplier,
        'product': product,
        'handle': handle,
        'place': place,
        'stock': stock_info,
        'csv': {
            'conditionRaw': stock.text(item.get('新古')),
            'supplier': stock.text(item.get('仕入先')),
            'customer': stock.text(item.get('顧客')),
            'productName': stock.text(item.get('商品名')),
            'productNameBase': display_name,
            'usedMarker': star,
            'code': stock.text(item.get('商品コード')),
            'size': product.get('size') or '',
            'site': stock.text(item.get('拠点')),
            'shelf': stock.text(item.get('棚')),
            'unitCostRaw': stock.text(item.get('仕入単価')),
            'qtyRaw': stock.text(item.get('在庫')),
        },
        'reasons': reasons,
        'sourceRowNo': row_no,
    }


def find_csv_duplicates(classified):
    """同一キーの CSV 行を重複候補に（自動合算・先後勝ちしない）。サイズ・単価・新古が違う行は別扱い。"""
    groups = defaultdict(list)
    for row in classified:
        csv = row['csv']
        key = (
            csv['customer'], csv['code'], csv['productName'], csv['size'],
            row.get('condition'), csv['site'], csv['shelf'], row.get('unitCost'),
        )
        groups[key].append(row['rowNo'])
    dup_map = {}
    for key, nos in groups.items():
        if len(nos) > 1:
            for n in nos:
                dup_map[n] = nos
    return dup_map


def build_preview(c, body):
    # プレビューは台帳・在庫を変更しない。登録権限は確定時に再確認する。
    uploaded = decode_upload(body)
    basis = stock.parse_date(body.get('basisDate'), '在庫基準日')
    default_site = stock.text(body.get('defaultSiteName')) or stock.UNCONFIRMED_SITE_NAME
    site_row = c.execute(
        'SELECT id,name FROM locations WHERE name=? AND parent_id IS NULL', (default_site,)
    ).fetchone()
    if not site_row:
        raise ValueError(
            f'拠点空欄時の登録先「{default_site}」が保管場所にありません。'
            f'（「{stock.UNCONFIRMED_SITE_NAME}」は起動時に用意されます）'
        )

    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    classified = []
    for row_no, item in uploaded['rows']:
        sel = selections.get(str(row_no)) or selections.get(row_no) or {}
        classified.append(classify_row(
            c, row_no, item,
            default_site_name=default_site,
            selected_product_id=sel.get('productId'),
            selected_handle_id=sel.get('handleId'),
        ))

    dup_map = find_csv_duplicates(classified)
    for row in classified:
        dups = dup_map.get(row['rowNo'])
        if dups:
            row['csvDuplicateRowNos'] = dups
            row['status'] = STATUS_HOLD
            row['statusLabel'] = STATUS_LABELS[STATUS_HOLD]
            row['reasons'] = list(row['reasons']) + [
                f'CSV内で重複候補です（行 {", ".join(str(n) for n in dups)}）。自動合算・削除しません。解決まで保留します。'
            ]

    counts = {k: 0 for k in STATUS_LABELS}
    for row in classified:
        counts[row['status']] = counts.get(row['status'], 0) + 1

    fp = db_fingerprint(c)
    t = stock.stamp()
    actor = stock._actor_name(body)
    # プレビューは台帳・在庫を変更しない（取込履歴行もここでは書かない）

    notices = [
        'この段階ではデータベースを変更していません。',
        '数量は「在庫」列のみ使用します（補正在庫数・購入・出荷は使いません）。',
        '入荷日はCSVに無いため入荷日不明で登録します。架空の日付は作りません。',
        '払出し順はCSV行番号を仮に使います。CSVの並び＝入荷順とは断定しません。',
        'CSVに加工欄がありません。既存取扱が「加工なし」1件だけのときはそれを再利用し、'
        '加工仕様は変更しません。取扱が無いときだけ「加工未整理」を追加します。',
        '品番・商品名の加工記載（例: 刺繍あり）はそのまま残し、後から整理できます。'
        '同じ品番でも商品名が違う行は無条件に合算しません。',
        '「＊」は中古標記です（加工と混同しません）。',
        '数量差異は初期状態で非選択です。明示選択したものだけCSV数量へ更新します。',
        '要確認・保留は黙って除外しません。確定時は対象件数と保留件数を明示します。',
    ]

    return {
        'fileName': uploaded['fileName'],
        'fileSize': uploaded['fileSize'],
        'fileSha256': uploaded['fileSha256'],
        'encoding': uploaded['encoding'],
        'rowCount': len(classified),
        'basisDate': basis,
        'defaultSiteName': default_site,
        'defaultSiteId': site_row['id'],
        'previewAt': t,
        'previewFingerprint': fp,
        'actorName': actor,
        'counts': {STATUS_LABELS[k]: counts.get(k, 0) for k in STATUS_LABELS},
        'countsRaw': counts,
        'notices': notices,
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
        'rows': classified,
        'ignoredColumns': list(IGNORED_QTY_COLUMNS),
    }


def _ensure_person(c, table, save_fn, name, *, create):
    name = stock.text(name)
    row = c.execute(f'SELECT id,active FROM {table} WHERE name=?', (name,)).fetchone()
    if row:
        if not row['active']:
            raise ValueError(f'停止中の{("顧客" if table == "customers" else "仕入先")}「{name}」は再開しません。')
        return row['id'], False
    if not create:
        raise ValueError(f'{name} が未登録です。')
    saved = save_fn(c, {'name': name, 'active': True})
    return saved['id'], True


def _ensure_location(c, place):
    site_name = place['siteName']
    shelf_name = place['shelfName']
    site = c.execute(
        'SELECT id,active FROM locations WHERE name=? AND parent_id IS NULL', (site_name,)
    ).fetchone()
    created_site = created_shelf = False
    if not site:
        site = stock.save_location(c, {'siteName': site_name, 'active': True})
        site_id = site['id']
        created_site = True
    else:
        if not site['active']:
            raise ValueError(f'停止中の拠点「{site_name}」は使いません。')
        site_id = site['id']
    if not shelf_name:
        return site_id, created_site, created_shelf
    shelf = c.execute(
        'SELECT id,active FROM locations WHERE name=? AND parent_id=?', (shelf_name, site_id)
    ).fetchone()
    if shelf:
        if not shelf['active']:
            raise ValueError(f'停止中の棚「{shelf_name}」は使いません。')
        return shelf['id'], created_site, False
    saved = stock.save_location(c, {'siteId': site_id, 'shelfName': shelf_name, 'active': True})
    return saved['id'], created_site, True


def _ensure_product(c, product_info, supplier_id, csv_name):
    if product_info.get('id'):
        pid = product_info['id']
        # サイズだけ追加。名・色・仕入先は無断上書きしない。
        if product_info.get('sizeAction') == 'add':
            labels = [r['label'] for r in c.execute(
                'SELECT label FROM product_sizes WHERE product_id=? ORDER BY sort_order,id', (pid,)
            )]
            size = product_info['size']
            if size not in labels:
                labels.append(size)
                current = stock.product_row(c, pid)
                stock.save_product(c, {
                    'id': pid,
                    'code': current['code'],
                    'name': current['name'],
                    'color': current['color'],
                    'supplierId': current['supplierId'],
                    'sizes': labels,
                    'active': current['active'],
                    'links': [
                        {
                            'customerId': link['customerId'],
                            'embroidery': link['embroidery'],
                            'isStandard': link['isStandard'],
                            'active': link['active'],
                        }
                        for link in current['links']
                    ],
                })
        return pid, False
    saved = stock.save_product(c, {
        'code': product_info['code'],
        'name': csv_name,  # 加工記載を含む商品名をそのまま
        'color': '',  # 色必須にしない・無理抽出しない
        'supplierId': supplier_id,
        'sizes': [product_info['size']],
        'links': [],
        'active': True,
    })
    return saved['id'], True


def _ensure_handle(c, customer_id, product_id, handle_info):
    """既存取扱IDがあればそのまま返す（加工仕様は変更しない）。なければ加工未整理を追加。"""
    if handle_info.get('id'):
        return handle_info['id'], False
    saved = stock.ensure_customer_product(c, {
        'customerId': customer_id,
        'productId': product_id,
        'embroidery': PENDING_EMBROIDERY,
    })
    return saved['id'], not saved.get('already')


def commit_preview(c, body, *, db_path=None):
    stock._require_write(body)
    uploaded = decode_upload(body)
    token = stock._token(body.get('token') or body.get('commitToken'))
    existing_token = c.execute(
        'SELECT id,result_json FROM legacy_csv_import_runs WHERE commit_token=?', (token,)
    ).fetchone()
    if existing_token:
        try:
            result = json.loads(existing_token['result_json'] or '{}')
        except json.JSONDecodeError:
            result = {}
        if not isinstance(result, dict):
            result = {}
        return {**result, 'already': True, 'runId': existing_token['id']}

    preview_fp = stock.text(body.get('previewFingerprint'))
    if not preview_fp:
        raise ValueError('プレビュー結果を指定してください。もう一度「取込内容を確認」してください。')
    fp_now = db_fingerprint(c)
    if preview_fp != fp_now:
        raise stock.StockConflict({
            'error': 'プレビュー後にデータが変わったため、確認し直してください。',
            'code': 'stale_preview',
            'previewFingerprint': preview_fp,
            'currentFingerprint': fp_now,
        })

    basis = stock.parse_date(body.get('basisDate'), '在庫基準日')
    default_site = stock.text(body.get('defaultSiteName')) or stock.UNCONFIRMED_SITE_NAME
    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    selected_qty_diffs = set()
    for key, val in selections.items():
        if isinstance(val, dict) and val.get('updateQty'):
            try:
                selected_qty_diffs.add(int(key))
            except (TypeError, ValueError):
                pass
    explicit_include = body.get('includeRowNos')
    if explicit_include is not None and not isinstance(explicit_include, list):
        raise ValueError('確定対象の行指定が不正です。')

    classified = []
    items_by_no = {no: item for no, item in uploaded['rows']}
    for row_no, item in uploaded['rows']:
        sel = selections.get(str(row_no)) or selections.get(row_no) or {}
        classified.append(classify_row(
            c, row_no, item,
            default_site_name=default_site,
            selected_product_id=sel.get('productId'),
            selected_handle_id=sel.get('handleId'),
        ))
    dup_map = find_csv_duplicates(classified)
    for row in classified:
        if dup_map.get(row['rowNo']):
            row['status'] = STATUS_HOLD
            row['csvDuplicateRowNos'] = dup_map[row['rowNo']]

    hold_rows = [r for r in classified if r['status'] in (STATUS_REVIEW, STATUS_HOLD)]
    commit_rows = []
    for row in classified:
        if explicit_include is not None:
            if row['rowNo'] in explicit_include or str(row['rowNo']) in explicit_include:
                if row['status'] in (STATUS_REVIEW, STATUS_HOLD, STATUS_UNCHANGED):
                    raise ValueError(f'{row["rowNo"]}行目は要確認・保留・変更なしのため登録対象にできません。')
                commit_rows.append(row)
            continue
        if row['status'] in (STATUS_CREATE, STATUS_REUSE):
            commit_rows.append(row)
        elif row['status'] == STATUS_QTY_DIFF and row['rowNo'] in selected_qty_diffs:
            commit_rows.append(row)

    if not commit_rows and not body.get('allowEmptyCommit'):
        raise ValueError(
            '登録・更新する行がありません。'
            f'保留・要確認は {len(hold_rows)} 件残っています。対象行を選ぶか、解決してから確定してください。'
        )

    backup_path = None
    if db_path:
        backup_path = str(stock.backup_sqlite(db_path, tag='pre-legacy-csv') or '')

    actor = stock._actor_name(body)
    t0 = stock.stamp()
    c.execute(
        '''INSERT INTO legacy_csv_import_runs(
             file_sha256,file_name,file_size,row_count,basis_date,default_site_name,actor_name,
             preview_at,preview_fingerprint,commit_token,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        (
            uploaded['fileSha256'], uploaded['fileName'], uploaded['fileSize'], len(classified),
            basis, default_site, actor, stock.text(body.get('previewAt')) or t0, preview_fp, token, t0,
        ),
    )
    run_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]

    result_counts = {
        'customersCreated': 0, 'suppliersCreated': 0, 'productsCreated': 0,
        'sizesAdded': 0, 'handlesCreated': 0, 'sitesCreated': 0, 'shelvesCreated': 0,
        'openingCreated': 0, 'qtyUpdated': 0, 'unchanged': 0,
        'held': len(hold_rows), 'failed': 0,
    }
    failures = []

    for row in commit_rows:
        row_no = row['rowNo']
        try:
            if row['status'] == STATUS_QTY_DIFF:
                stock_info = row['stock'] or {}
                stock.adjust_opening_qty_diff(c, {
                    '_canWrite': True,
                    '_actor': body.get('_actor'),
                    'lotIds': stock_info.get('lotIds') or [],
                    'targetQty': row['qty'],
                    'fingerprint': stock_info.get('fingerprint') or '',
                    'reason': f'{LEGACY_IMPORT_NOTE}（数量差異・CSV {row_no}行目）',
                    'businessOn': basis,
                    'actorName': actor,
                })
                result_counts['qtyUpdated'] += 1
                continue

            cust_id, created = _ensure_person(
                c, 'customers', stock.save_customer, row['csv']['customer'],
                create=row['customer']['action'] == 'create',
            )
            if created:
                result_counts['customersCreated'] += 1
            sup_id = None
            if row['csv']['supplier']:
                sup_id, created = _ensure_person(
                    c, 'suppliers', stock.save_supplier, row['csv']['supplier'],
                    create=row['supplier']['action'] == 'create',
                )
                if created:
                    result_counts['suppliersCreated'] += 1

            pid, created = _ensure_product(c, row['product'], sup_id, row['csv']['productName'])
            if created:
                result_counts['productsCreated'] += 1
            elif row['product'].get('sizeAction') == 'add':
                result_counts['sizesAdded'] += 1

            loc_id, created_site, created_shelf = _ensure_location(c, row['place'])
            if created_site:
                result_counts['sitesCreated'] += 1
            if created_shelf:
                result_counts['shelvesCreated'] += 1

            csv_source = {
                'sourceRowNo': row_no,
                'productCode': row['csv']['code'],
                'productName': row['csv']['productName'],
                'usedMarker': bool(row['csv'].get('usedMarker')),
            }
            handle_info = _handle_preview(c, cust_id, pid, row_no, csv_source=csv_source)
            sel = selections.get(str(row_no)) or selections.get(row_no) or {}
            if sel.get('handleId'):
                handle_info['id'] = sel['handleId']
                handle_info['action'] = 'reuse'
            cp_id, created = _ensure_handle(c, cust_id, pid, handle_info)
            if created:
                result_counts['handlesCreated'] += 1

            stock_info = _stock_match(
                c, cp_id, row['product']['size'], loc_id, row['condition'], row['unitCost'],
            )
            if stock_info and stock_info['exists']:
                if stock_info['currentQty'] == row['qty']:
                    result_counts['unchanged'] += 1
                    continue
                raise ValueError('在庫が既にあるため新規登録できません。数量差異として選択し直してください。')

            token_src = json.dumps({
                'runId': run_id, 'rowNo': row_no, 'sha': uploaded['fileSha256'],
                'cp': cp_id, 'size': row['product']['size'], 'loc': loc_id,
                'cond': row['condition'], 'cost': row['unitCost'], 'qty': row['qty'],
                'basis': basis,
            }, ensure_ascii=False, sort_keys=True)
            open_token = 'legacy-csv-' + hashlib.sha256(token_src.encode()).hexdigest()[:40]
            # 品番・商品名・元行番号を履歴に残し、後から加工整理できるようにする（商品名の加工記載は消さない）
            note = (
                f'{LEGACY_IMPORT_NOTE}（CSV {row_no}行目 / 元品番 {row["csv"]["code"]} '
                f'/ {row["csv"]["productName"]}）'
            )
            if handle_info.get('pendingEmbroiderySort'):
                note += '／加工仕様は後整理（既存取扱の加工は未変更）'
            stock.register_opening(c, {
                'token': open_token,
                'customerProductId': cp_id,
                'locationId': loc_id,
                'basisDate': basis,
                'receivedUnknown': True,
                'receivedOn': '',
                'payoutOrder': row_no,
                'condition': row['condition'],
                'allowZero': True,
                'allowNegative': True,
                'note': note,
                '_actor': body.get('_actor'),
                'actorName': actor,
                'lines': [{'size': row['product']['size'], 'qty': row['qty'], 'unitCost': row['unitCost']}],
            })
            result_counts['openingCreated'] += 1
        except stock.StockConflict:
            raise
        except Exception as e:
            result_counts['failed'] += 1
            failures.append({'rowNo': row_no, 'reason': str(e) or '登録に失敗しました。'})
            raise ValueError(
                f'{row_no}行目で失敗したため、すべて取り消します: {e}'
            ) from e

    t = stock.stamp()
    result = {
        'already': False,
        'runId': run_id,
        'commitToken': token,
        'committedAt': t,
        'actorName': actor,
        'backupPath': backup_path,
        'counts': result_counts,
        'committedRowCount': len(commit_rows),
        'heldRowCount': len(hold_rows),
        'heldRowNos': [r['rowNo'] for r in hold_rows][:200],
        'heldRowNosTruncated': len(hold_rows) > 200,
        'failures': failures,
        'fileSha256': uploaded['fileSha256'],
        'fileName': uploaded['fileName'],
        'notices': [
            f'確定 {len(commit_rows)} 件、保留・要確認 {len(hold_rows)} 件を残しました。',
            '保留・要確認は登録していません（黙って除外していません）。',
        ],
    }
    c.execute(
        'UPDATE legacy_csv_import_runs SET committed_at=?, result_json=? WHERE id=?',
        (t, json.dumps(result, ensure_ascii=False), run_id),
    )
    return result


def file_history(c, body):
    """同ファイル再選択時の前回取込情報（自動再反映はしない）。"""
    uploaded = decode_upload(body)
    return {
        'fileName': uploaded['fileName'],
        'fileSize': uploaded['fileSize'],
        'fileSha256': uploaded['fileSha256'],
        'encoding': uploaded['encoding'],
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
        'autoReapply': False,
    }
