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
# 品番がこの値（前後空白除去後の完全一致）のときだけ複数登録を許可する例外。
# 「別注(…)」など別品番までは対象にしない。
CUSTOM_ORDER_CODE = '別注'
COMPANY_FORMS = (
    ('㈱', '株式会社'),
    ('㈲', '有限会社'),
    ('㈾', '合同会社'),
    ('(株)', '株式会社'),
    ('（株）', '株式会社'),
    ('(有)', '有限会社'),
    ('（有）', '有限会社'),
)

# 品番空欄でも制服として取り込む例外（顧客名×商品名。空白種類だけ正規化して完全一致）
UNIFORM_BLANK_SKU_PAIRS = frozenset((
    ('グローヴ㈱', 'チェック柄ベスト(ムーリベッキ)'),
    ('グローヴ㈱', '衣装係ストレートパンツ'),
    ('石屋商事 パーク共通', '黒ロングコート'),
    ('日鉄ファーストテック㈱', '耐切創夏ＪＰ'),
    ('明日佳病院（事務', '事務マタニティドレス'),
))

STATUS_CREATE = 'create'          # 新規追加（在庫なし→登録）
STATUS_REUSE = 'reuse'            # 既存マスター使用（在庫新規）
STATUS_QTY_DIFF = 'qty_diff'      # 数量差異
STATUS_UNCHANGED = 'unchanged'    # 変更なし
STATUS_REVIEW = 'needs_review'    # 要確認
STATUS_HOLD = 'hold'              # 保留（CSV内重複など）
STATUS_EXCLUDED = 'excluded'      # 対象外（エラーではない。登録しない）

STATUS_LABELS = {
    STATUS_CREATE: '新規追加',
    STATUS_REUSE: '既存使用',
    STATUS_QTY_DIFF: '数量差異',
    STATUS_UNCHANGED: '変更なし',
    STATUS_REVIEW: '要確認',
    STATUS_HOLD: '保留',
    STATUS_EXCLUDED: '対象外',
}


def _collapse_space(value):
    return re.sub(r'[\s\u3000]+', '', stock.text(value))


def identity_match_key(value):
    """顧客名・商品名の完全一致用キー。半角/全角空白の差だけ畳む（類似拡張はしない）。"""
    return _collapse_space(value)


UNIFORM_BLANK_SKU_KEYS = frozenset(
    (identity_match_key(c), identity_match_key(n)) for c, n in UNIFORM_BLANK_SKU_PAIRS
)


def is_uniform_blank_sku_exception(customer_name, product_name):
    return (
        identity_match_key(customer_name),
        identity_match_key(product_name),
    ) in UNIFORM_BLANK_SKU_KEYS


def proposed_blank_sku_category(customer_name, product_name):
    if is_uniform_blank_sku_exception(customer_name, product_name):
        return stock.CATEGORY_UNIFORM
    return stock.CATEGORY_PARTS


def is_custom_order_code(code):
    """品番「別注」限定。空白は照合用に整理するが、部分一致はしない。"""
    return stock.text(code) == CUSTOM_ORDER_CODE


def custom_order_name_key(product_name):
    """別注の商品名照合キー。括弧内の色・加工・仕様も含めた全文（空白種類のみ畳む）。"""
    return identity_match_key(product_name)


def product_map_key(*, customer, code, product_name):
    """取込対応（商品単位）。行番号だけに依存しない。"""
    raw = '|'.join([
        identity_match_key(customer),
        stock.text(code),
        custom_order_name_key(product_name),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def stock_map_key(
    *, customer, code, product_name, size, condition, unit_cost, site, shelf,
):
    """取込対応（在庫単位）。並び替えCSVでも同一条件を辿れる。"""
    cost_s = '' if unit_cost is None else str(unit_cost).strip()
    raw = '|'.join([
        identity_match_key(customer),
        stock.text(code),
        custom_order_name_key(product_name),
        stock.text(size),
        stock.text(condition),
        cost_s,
        stock.text(site),
        stock.text(shelf),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


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
        '''SELECT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,s.name AS supplier_name
           FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id
           WHERE p.code=? AND p.merged_into_id IS NULL ORDER BY p.id''',
        (code,),
    ))


def find_custom_order_products_for_customer(c, product_name, *, customer_id=None):
    """品番「別注」を商品名全文＋対象顧客の取扱関係で照合。

    仕入先は照合キーに使わない。顧客対応が取れない既存別注は候補にしない。
    """
    name = stock.text(product_name)
    if not name or not customer_id:
        return []
    return list(c.execute(
        '''SELECT DISTINCT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,
                  s.name AS supplier_name
           FROM products p
           LEFT JOIN suppliers s ON s.id=p.supplier_id
           JOIN customer_products cp ON cp.product_id=p.id AND cp.active=1
           WHERE p.code=? AND p.name=? AND cp.customer_id=? AND p.merged_into_id IS NULL
           ORDER BY p.id''',
        (CUSTOM_ORDER_CODE, name, customer_id),
    ))


def find_custom_order_products_by_name(c, product_name):
    """品番「別注」＋商品名全文の候補（顧客未確定時の複数候補表示用）。"""
    name = stock.text(product_name)
    if not name:
        return []
    return list(c.execute(
        '''SELECT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,s.name AS supplier_name
           FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id
           WHERE p.code=? AND p.name=? AND p.merged_into_id IS NULL ORDER BY p.id''',
        (CUSTOM_ORDER_CODE, name),
    ))


def lookup_product_map(c, *, customer, code, product_name):
    key = product_map_key(customer=customer, code=code, product_name=product_name)
    return c.execute(
        '''SELECT * FROM legacy_csv_row_maps
           WHERE product_map_key=? ORDER BY id DESC LIMIT 1''',
        (key,),
    ).fetchone()


def lookup_stock_map(
    c, *, customer, code, product_name, size, condition, unit_cost, site, shelf,
):
    key = stock_map_key(
        customer=customer, code=code, product_name=product_name, size=size,
        condition=condition, unit_cost=unit_cost, site=site, shelf=shelf,
    )
    return c.execute(
        '''SELECT * FROM legacy_csv_row_maps
           WHERE stock_map_key=? ORDER BY id DESC LIMIT 1''',
        (key,),
    ).fetchone()


def upsert_row_map(c, *, run_id, file_sha256, file_name, row, customer_id, product_id,
                   customer_product_id, opening_token='', lot_ids=None):
    """取込対応を永続化。元CSV情報を保持し、再取込時はこちらを優先参照する。"""
    csv = row.get('csv') or {}
    product = row.get('product') or {}
    customer = stock.text(csv.get('customer'))
    code = stock.text(csv.get('code'))
    product_name = stock.text(csv.get('productName'))
    size = stock.text(product.get('size') or csv.get('size'))
    condition = stock.text(row.get('condition'))
    unit_cost = row.get('unitCost')
    unit_cost_s = '' if unit_cost is None else str(unit_cost)
    site = stock.text(csv.get('site'))
    shelf = stock.text(csv.get('shelf'))
    pkey = product_map_key(customer=customer, code=code, product_name=product_name)
    skey = stock_map_key(
        customer=customer, code=code, product_name=product_name, size=size,
        condition=condition, unit_cost=unit_cost_s, site=site, shelf=shelf,
    )
    t = stock.stamp()
    lot_ids_json = json.dumps(list(lot_ids or []), ensure_ascii=False)
    existing = c.execute(
        'SELECT id FROM legacy_csv_row_maps WHERE stock_map_key=? ORDER BY id DESC LIMIT 1',
        (skey,),
    ).fetchone()
    vals = (
        pkey, skey, file_sha256, file_name, row.get('rowNo'),
        customer, stock.text(csv.get('supplier')), code, product_name, size,
        condition, unit_cost_s, site, shelf,
        customer_id, product_id, customer_product_id, opening_token, lot_ids_json,
        run_id, t,
    )
    if existing:
        c.execute(
            '''UPDATE legacy_csv_row_maps SET
                 product_map_key=?, stock_map_key=?, file_sha256=?, file_name=?, source_row_no=?,
                 csv_customer=?, csv_supplier=?, csv_product_code=?, csv_product_name=?, csv_size=?,
                 csv_condition=?, csv_unit_cost=?, csv_site=?, csv_shelf=?,
                 customer_id=?, product_id=?, customer_product_id=?, opening_token=?, lot_ids_json=?,
                 run_id=?, updated_at=?
               WHERE id=?''',
            vals + (existing['id'],),
        )
        return existing['id']
    c.execute(
        '''INSERT INTO legacy_csv_row_maps(
             product_map_key,stock_map_key,file_sha256,file_name,source_row_no,
             csv_customer,csv_supplier,csv_product_code,csv_product_name,csv_size,
             csv_condition,csv_unit_cost,csv_site,csv_shelf,
             customer_id,product_id,customer_product_id,opening_token,lot_ids_json,
             run_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        vals + (t,),
    )
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def find_blank_sku_products(c, name, supplier_id):
    """空品番商品を商品名・仕入先で照合。空品番だけで同一視しない。"""
    name = stock.text(name)
    if not name:
        return []
    if supplier_id is None:
        rows = c.execute(
            '''SELECT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,s.name AS supplier_name
               FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id
               WHERE p.code='' AND p.name=? AND p.supplier_id IS NULL
                 AND p.merged_into_id IS NULL ORDER BY p.id''',
            (name,),
        )
    else:
        rows = c.execute(
            '''SELECT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,s.name AS supplier_name
               FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id
               WHERE p.code='' AND p.name=? AND p.supplier_id=?
                 AND p.merged_into_id IS NULL ORDER BY p.id''',
            (name, supplier_id),
        )
    return list(rows)


def _product_candidate(m, c=None):
    cat = m['category'] if 'category' in m.keys() else ''
    info = {
        'id': m['id'], 'code': m['code'], 'name': m['name'], 'color': m['color'],
        'category': cat or '',
        'supplierName': m['supplier_name'] or '', 'active': bool(m['active']),
        'codeDisplay': stock.product_code_display(m['code'], cat),
        'customers': [],
        'customerNames': [],
        'embroideries': [],
    }
    if c is not None:
        links = list(c.execute(
            '''SELECT cu.id AS customer_id, cu.name AS customer_name, cp.embroidery
               FROM customer_products cp
               JOIN customers cu ON cu.id=cp.customer_id
               WHERE cp.product_id=? AND cp.active=1
               ORDER BY cu.name, cp.id''',
            (m['id'],),
        ))
        info['customers'] = [
            {
                'id': r['customer_id'],
                'name': r['customer_name'],
                'embroidery': r['embroidery'],
                'embroideryLabel': stock.embroidery_label(r['embroidery']),
            }
            for r in links
        ]
        info['customerNames'] = sorted({r['customer_name'] for r in links})
        info['embroideries'] = [stock.embroidery_label(r['embroidery']) for r in links]
    return info


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
    tables = (
        'customers', 'suppliers', 'products', 'customer_products', 'locations', 'lots',
        'legacy_csv_row_maps',
    )
    for table in tables:
        exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            parts.append(f'{table}:0:0')
            continue
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


def _apply_category_decision(info, existing_category, proposed_category, selected_category):
    """既存カテゴリは無断変更しない。プレビューで選んだカテゴリだけ反映候補にする。"""
    existing = stock.text(existing_category)
    proposed = stock.text(proposed_category)
    selected = None
    if selected_category is not None and selected_category != '':
        try:
            selected = stock.normalize_category(selected_category)
        except ValueError:
            info['action'] = 'review'
            info['reasons'].append(f'カテゴリ「{selected_category}」は使えません。')
            info['category'] = existing or proposed
            info['proposedCategory'] = proposed
            info['categoryConflict'] = True
            return info
    elif selected_category == '':
        selected = ''

    if selected is not None:
        info['category'] = selected
        info['proposedCategory'] = proposed
        info['categoryAction'] = 'set' if selected != existing else 'keep'
        info['categoryConflict'] = False
        return info

    if existing == proposed:
        info['category'] = existing
        info['proposedCategory'] = proposed
        info['categoryAction'] = 'keep'
        info['categoryConflict'] = False
        return info

    # 既存ありで提案と違う／既存空で提案あり → 確認（無断変更しない）
    info['category'] = existing
    info['proposedCategory'] = proposed
    info['categoryAction'] = 'confirm'
    info['categoryConflict'] = True
    info['action'] = 'review'
    if existing:
        info['reasons'].append(
            f'既存カテゴリ「{existing}」と取込提案「{proposed}」が異なります。'
            f'カテゴリを選んでから取り込んでください（無断変更しません）。'
        )
    else:
        info['reasons'].append(
            f'既存商品のカテゴリが空です。提案は「{proposed}」です。'
            f'カテゴリを選んでから取り込んでください（無断変更しません）。'
        )
    return info


def _finalize_matched_product(
    c, info, prod, name, size_label, supplier_id, *,
    proposed_category, selected_category, apply_category=False,
    allow_supplier_diff=False,
):
    code = info['code']
    if not prod['active']:
        info['action'] = 'review'
        info['id'] = prod['id']
        label = code or name or prod['id']
        info['reasons'].append(f'{info.get("_rowNo")}行目：商品「{label}」は停止中です。重複追加・無断再開しません。')
        return info
    if supplier_id and prod['supplier_id'] not in (None, supplier_id):
        info['supplierConflict'] = True
        if allow_supplier_diff:
            # 別注: 仕入先差だけで別商品を作らず、既存仕入先も上書きしない。CSV仕入先は取込元として保持。
            info['supplierKept'] = True
        else:
            info['action'] = 'review'
            info['id'] = prod['id']
            info['reasons'].append(
                f'{info.get("_rowNo")}行目：品番「{code or "（空）"}」の仕入先が既存と異なります'
                f'（既存: {prod["supplier_name"] or "未設定"}）。'
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
    existing_cat = prod['category'] if 'category' in prod.keys() else ''
    if apply_category:
        _apply_category_decision(info, existing_cat, proposed_category, selected_category)
    else:
        info['category'] = existing_cat or ''
        info['proposedCategory'] = existing_cat or ''
        info['categoryAction'] = 'keep'
        info['categoryConflict'] = False
    info['codeDisplay'] = stock.product_code_display(code, info.get('category') or proposed_category)
    return info


def _product_preview(
    c, item, supplier_info, row_no, *,
    customer_name='', customer_id=None, selected_category=None, mapped_product_id=None,
):
    code = stock.text(item.get('商品コード'))
    name = stock.text(item.get('商品名'))
    size = item.get('サイズ')
    size = size if isinstance(size, str) else ('' if size is None else str(size))
    # サイズは前後空白のみ整理。先頭ゼロ・ハイフン・数字・メートル表記はそのまま（再換算しない）。
    size_label = size.strip()
    blank_sku = not code
    custom_order = is_custom_order_code(code)
    proposed_category = (
        proposed_blank_sku_category(customer_name, name) if blank_sku
        else (stock.CATEGORY_UNIFORM if custom_order else '')
    )
    if selected_category is not None and selected_category != '':
        try:
            # プレビューでカテゴリ変更可
            proposed_category = stock.normalize_category(selected_category) or proposed_category
        except ValueError:
            pass
    info = {
        'code': code, 'name': name, 'size': size_label,
        'action': 'missing', 'id': None, 'candidates': [], 'reasons': [],
        'sizeAction': 'ok', 'nameDiff': False, 'supplierConflict': False,
        'supplierKept': False,
        'category': proposed_category if (blank_sku or custom_order) else '',
        'proposedCategory': proposed_category if (blank_sku or custom_order) else '',
        'categoryAction': 'set' if (blank_sku or custom_order) else 'keep',
        'categoryConflict': False,
        'blankSku': blank_sku,
        'customOrder': custom_order,
        'fromImportMap': False,
        'codeDisplay': stock.product_code_display(
            code, proposed_category if (blank_sku or custom_order) else '',
        ),
        '_rowNo': row_no,
    }
    if not name:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：商品名が空です。')
        return info
    if not size_label:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：サイズが空です。')
        return info

    supplier_id = supplier_info.get('id')

    # 取込対応記録を優先（商品名を後から整理しても再取込で重複作成しない）
    if mapped_product_id:
        prod = c.execute(
            '''SELECT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,s.name AS supplier_name
               FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?''',
            (mapped_product_id,),
        ).fetchone()
        if prod and prod['active']:
            info['fromImportMap'] = True
            return _finalize_matched_product(
                c, info, prod, name, size_label, supplier_id,
                proposed_category=proposed_category, selected_category=selected_category,
                apply_category=bool(blank_sku or custom_order),
                allow_supplier_diff=custom_order,
            )

    if blank_sku:
        matches = find_blank_sku_products(c, name, supplier_id)
        if not matches and supplier_id is not None:
            # 仕入先未設定の同名空品番も候補に残す（無断統合はしない）
            loose = find_blank_sku_products(c, name, None)
            if loose:
                info['action'] = 'review'
                info['candidates'] = [_product_candidate(m, c) for m in loose]
                info['reasons'].append(
                    f'{row_no}行目：品番なしの同名商品が仕入先未設定であります。候補から選んでください。'
                )
                return info
        if not matches:
            info['action'] = 'create'
            info['sizeAction'] = 'add'
            if selected_category is not None:
                try:
                    info['category'] = stock.normalize_category(selected_category)
                except ValueError:
                    info['action'] = 'review'
                    info['reasons'].append(f'{row_no}行目：カテゴリ「{selected_category}」は使えません。')
            info['codeDisplay'] = stock.product_code_display('', info['category'])
            return info
        if len(matches) > 1:
            info['action'] = 'review'
            info['candidates'] = [_product_candidate(m, c) for m in matches]
            info['reasons'].append(
                f'{row_no}行目：品番なしで商品名・仕入先が同じ商品が複数あります。候補から選んでください。'
            )
            return info
        return _finalize_matched_product(
            c, info, matches[0], name, size_label, supplier_id,
            proposed_category=proposed_category, selected_category=selected_category,
            apply_category=True,
        )

    if custom_order:
        # 品番「別注」: 同一品番の複数登録を許可。照合は顧客×商品名全文。
        info['category'] = proposed_category or stock.CATEGORY_UNIFORM
        info['proposedCategory'] = info['category']
        info['categoryAction'] = 'set'
        info['codeDisplay'] = stock.product_code_display(code, info['category'])
        matches = find_custom_order_products_for_customer(
            c, name, customer_id=customer_id,
        )
        if not matches:
            # 顧客との取扱が無い既存別注は勝手に割り当てない → 新規
            info['action'] = 'create'
            info['sizeAction'] = 'add'
            return info
        if len(matches) > 1:
            info['action'] = 'review'
            info['candidates'] = [_product_candidate(m, c) for m in matches]
            info['reasons'].append(
                f'{row_no}行目：品番「別注」で顧客・商品名が同じ商品が複数あります。'
                f'商品ID・顧客・商品名・色・加工情報から選んでください。'
            )
            return info
        return _finalize_matched_product(
            c, info, matches[0], name, size_label, supplier_id,
            proposed_category=info['category'], selected_category=selected_category,
            apply_category=True, allow_supplier_diff=True,
        )

    matches = find_products_by_code(c, code)
    if not matches:
        info['action'] = 'create'
        info['sizeAction'] = 'add'
        return info
    narrowed = matches
    if supplier_id and len(matches) > 1:
        by_sup = [m for m in matches if m['supplier_id'] == supplier_id]
        if by_sup:
            narrowed = by_sup
    if len(narrowed) > 1:
        by_name = [m for m in narrowed if stock.text(m['name']) == name]
        if len(by_name) == 1:
            narrowed = by_name
        else:
            info['action'] = 'review'
            info['candidates'] = [_product_candidate(m, c) for m in narrowed]
            info['reasons'].append(f'{row_no}行目：同じ品番の商品が複数あります。候補から選んでください。')
            return info
    return _finalize_matched_product(
        c, info, narrowed[0], name, size_label, supplier_id,
        proposed_category='', selected_category=None,
        apply_category=False,
    )


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


def _excluded_row(row_no, item, *, qty, cost, reason):
    """商品名空×在庫0などの対象外行。顧客・仕入先は作らない。"""
    display_name, star = strip_used_marker(stock.text(item.get('商品名')))
    size = item.get('サイズ')
    size = size if isinstance(size, str) else ('' if size is None else str(size))
    return {
        'rowNo': row_no,
        'status': STATUS_EXCLUDED,
        'statusLabel': STATUS_LABELS[STATUS_EXCLUDED],
        'selected': False,
        'condition': None,
        'conditionLabel': '',
        'qty': qty,
        'unitCost': cost,
        'customer': {'label': '顧客', 'name': stock.text(item.get('顧客')), 'action': 'skip', 'id': None,
                     'candidates': [], 'reasons': []},
        'supplier': {'label': '仕入先', 'name': stock.text(item.get('仕入先')), 'action': 'skip', 'id': None,
                     'candidates': [], 'reasons': []},
            'product': {
            'code': stock.text(item.get('商品コード')), 'name': stock.text(item.get('商品名')),
            'size': size.strip(), 'action': 'skip', 'id': None, 'candidates': [], 'reasons': [],
            'category': '', 'proposedCategory': '',
            'blankSku': not stock.text(item.get('商品コード')),
            'customOrder': is_custom_order_code(item.get('商品コード')),
            'codeDisplay': stock.product_code_display(stock.text(item.get('商品コード')), ''),
        },
        'handle': {'action': 'skip', 'id': None, 'embroidery': '', 'embroideryLabel': '',
                   'candidates': [], 'reasons': [], 'pendingEmbroiderySort': False, 'csvSource': {}},
        'place': {'action': 'skip', 'siteName': '', 'shelfName': '', 'locationId': None},
        'stock': None,
        'csv': {
            'conditionRaw': stock.text(item.get('新古')),
            'supplier': stock.text(item.get('仕入先')),
            'customer': stock.text(item.get('顧客')),
            'productName': stock.text(item.get('商品名')),
            'productNameBase': display_name,
            'usedMarker': star,
            'code': stock.text(item.get('商品コード')),
            'codeDisplay': stock.product_code_display(stock.text(item.get('商品コード')), ''),
            'size': size.strip(),
            'site': stock.text(item.get('拠点')),
            'shelf': stock.text(item.get('棚')),
            'unitCostRaw': stock.text(item.get('仕入単価')),
            'qtyRaw': stock.text(item.get('在庫')),
            'category': '',
        },
        'reasons': [reason],
        'excludeReason': reason,
        'sourceRowNo': row_no,
    }


def classify_row(
    c, row_no, item, *, default_site_name,
    selected_product_id=None, selected_handle_id=None, selected_category=None,
):
    reasons = []
    product_name = stock.text(item.get('商品名'))
    qty, cost, qreasons = parse_row_qty_cost(item, row_no)

    # 商品名空欄かつ在庫=数値0 → 対象外（エラーでない）。空欄/不正値を0扱いしない。
    if not product_name:
        if qty == 0 and not qreasons:
            return _excluded_row(
                row_no, item, qty=qty, cost=cost,
                reason=f'{row_no}行目：商品名が空欄で在庫が0のため対象外です（登録しません）。',
            )
        # 在庫≠0 または数量不正 → 要確認（下で通常判定へ。顧客新規は作れるが商品は止まる）

    customer = _person_preview(c, 'customers', '顧客', item.get('顧客'), row_no)
    supplier = _person_preview(c, 'suppliers', '仕入先', item.get('仕入先'), row_no)
    reasons.extend(customer['reasons'])
    reasons.extend(supplier['reasons'])

    condition, creasons = parse_row_condition(item, row_no)
    reasons.extend(creasons)
    reasons.extend(qreasons)

    csv_customer = stock.text(item.get('顧客'))
    csv_code = stock.text(item.get('商品コード'))
    mapped = lookup_product_map(
        c, customer=csv_customer, code=csv_code, product_name=product_name,
    )
    mapped_product_id = None
    mapped_handle_id = None
    if mapped:
        mapped_product_id = mapped['product_id']
        mapped_handle_id = mapped['customer_product_id']
        if mapped['customer_id'] and customer.get('id') and mapped['customer_id'] != customer.get('id'):
            # 対応記録の顧客と現在照合顧客が食い違う場合は対応を使わない
            mapped_product_id = None
            mapped_handle_id = None
        elif mapped_product_id:
            # 統合済みなら統合先へ辿る（旧仕入先・旧商品名差で統合元を再作成しない）
            import stock_product_merge
            live_id = stock_product_merge.resolve_live_product_id(c, mapped_product_id)
            if live_id and live_id != mapped_product_id:
                mapped_product_id = live_id
                # 取扱も統合先側へ移っている想定。旧IDが無効なら後段で再判定。
                if mapped_handle_id:
                    h = c.execute(
                        'SELECT id, product_id, active FROM customer_products WHERE id=?',
                        (mapped_handle_id,),
                    ).fetchone()
                    if not h or h['product_id'] != live_id or not h['active']:
                        mapped_handle_id = None

    product = _product_preview(
        c, item, supplier, row_no,
        customer_name=csv_customer,
        customer_id=customer.get('id'),
        selected_category=selected_category,
        mapped_product_id=mapped_product_id,
    )
    if selected_product_id and isinstance(selected_product_id, int):
        import stock_product_merge
        selected_product_id = stock_product_merge.resolve_live_product_id(c, selected_product_id)
        prod = c.execute(
            '''SELECT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,s.name AS supplier_name
               FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?''',
            (selected_product_id,),
        ).fetchone()
        if prod and prod['active'] and stock.text(prod['code']) == stock.text(product['code']):
            product = _finalize_matched_product(
                c, {**product, 'reasons': [], 'candidates': [], 'action': 'reuse', '_rowNo': row_no},
                prod, product['name'], product['size'], supplier.get('id'),
                proposed_category=product.get('proposedCategory') or product.get('category') or '',
                selected_category=selected_category,
                apply_category=bool(product.get('blankSku') or product.get('customOrder')),
                allow_supplier_diff=bool(product.get('customOrder')),
            )
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
        'productCode': csv_code,
        'productName': product_name,
        'supplier': stock.text(item.get('仕入先')),
        'customer': csv_customer,
        # ＊は中古標記。加工としては扱わない（条件判定側で処理済み）
        'usedMarker': strip_used_marker(product_name)[1],
        'fromImportMap': bool(product.get('fromImportMap')),
    }
    handle = _handle_preview(
        c, customer.get('id'), product.get('id'), row_no, csv_source=csv_source,
    )
    # 対応記録の取扱IDを優先（明示選択があればそちら）
    prefer_handle_id = selected_handle_id if selected_handle_id else mapped_handle_id
    if prefer_handle_id and isinstance(prefer_handle_id, int):
        h = c.execute('SELECT id,embroidery,active,customer_id,product_id FROM customer_products WHERE id=?',
                      (prefer_handle_id,)).fetchone()
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
    category = product.get('category') or ''
    # 内部作業用キーを落とす
    product = {k: v for k, v in product.items() if not str(k).startswith('_')}
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
            'codeDisplay': stock.product_code_display(
                stock.text(item.get('商品コード')), category,
            ),
            'size': product.get('size') or '',
            'site': stock.text(item.get('拠点')),
            'shelf': stock.text(item.get('棚')),
            'unitCostRaw': stock.text(item.get('仕入単価')),
            'qtyRaw': stock.text(item.get('在庫')),
            'category': category,
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
            selected_category=sel.get('category') if 'category' in sel else None,
        ))

    dup_map = find_csv_duplicates(classified)
    for row in classified:
        if row['status'] == STATUS_EXCLUDED:
            continue
        dups = dup_map.get(row['rowNo'])
        if dups:
            # 対象外行は重複判定の相手にしない
            live = [n for n in dups if not any(
                r['rowNo'] == n and r['status'] == STATUS_EXCLUDED for r in classified
            )]
            if len(live) > 1 and row['rowNo'] in live:
                row['csvDuplicateRowNos'] = live
                row['status'] = STATUS_HOLD
                row['statusLabel'] = STATUS_LABELS[STATUS_HOLD]
                row['reasons'] = list(row['reasons']) + [
                    f'CSV内で重複候補です（行 {", ".join(str(n) for n in live)}）。自動合算・削除しません。解決まで保留します。'
                ]

    counts = {k: 0 for k in STATUS_LABELS}
    for row in classified:
        counts[row['status']] = counts.get(row['status'], 0) + 1

    excluded_rows = [r for r in classified if r['status'] == STATUS_EXCLUDED]
    fp = db_fingerprint(c)
    t = stock.stamp()
    actor = stock._actor_name(body)
    # プレビューは台帳・在庫を変更しない（取込履歴行もここでは書かない）

    notices = [
        'この段階ではデータベースを変更していません。',
        '数量は「在庫」列のみ使用します（補正在庫数・購入・出荷は使いません）。',
        '生地の数量・サイズ・仕入単価はCSVのまま保持します（5倍換算・単価割り返しはしません）。',
        '品番が空の行は、制服例外のみ「制服」、それ以外は「部品・資材」として取り込みます。'
        '内部品番は空欄のままです（画面表示は「品番なし」系）。',
        '商品名空欄かつ在庫0の行は対象外です（エラーではありません）。',
        '入荷日はCSVに無いため入荷日不明で登録します。架空の日付は作りません。',
        '払出し順はCSV行番号を仮に使います。CSVの並び＝入荷順とは断定しません。',
        'CSVに加工欄がありません。既存取扱が「加工なし」1件だけのときはそれを再利用し、'
        '加工仕様は変更しません。取扱が無いときだけ「加工未整理」を追加します。',
        '品番・商品名の加工記載（例: 刺繍あり）はそのまま残し、後から整理できます。'
        '同じ品番でも商品名が違う行は無条件に合算しません。空品番だけで同一視しません。',
        '品番「別注」は制服として、同じ品番の複数商品を許可します。'
        '照合は顧客×商品名全文（括弧内の色・加工含む）で行い、品番「別注」一致だけでは同一視しません。'
        '仕入先は照合キーに使いません。取込対応記録を永続化し、再取込・商品名整理後も優先参照します。',
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
        'excludedCount': len(excluded_rows),
        'excludedRows': [
            {
                'rowNo': r['rowNo'],
                'reason': r.get('excludeReason') or (r['reasons'][0] if r['reasons'] else ''),
                'customer': r['csv'].get('customer', ''),
                'supplier': r['csv'].get('supplier', ''),
            }
            for r in excluded_rows[:200]
        ],
        'excludedRowsTruncated': len(excluded_rows) > 200,
        'notices': notices,
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
        'rows': classified,
        'ignoredColumns': list(IGNORED_QTY_COLUMNS),
        'categoryOptions': ['', stock.CATEGORY_UNIFORM, stock.CATEGORY_PARTS],
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


def _save_product_sizes_and_category(c, pid, product_info):
    """サイズ追加と、確認済みカテゴリ変更のみ反映。名・色・仕入先は無断上書きしない。"""
    current = stock.product_row(c, pid)
    labels = list(current['sizes'])
    size = product_info.get('size')
    size_added = False
    if product_info.get('sizeAction') == 'add' and size and size not in labels:
        labels.append(size)
        size_added = True
    category = current.get('category') or ''
    if product_info.get('categoryAction') == 'set' and 'category' in product_info:
        category = stock.normalize_category(product_info.get('category'))
    if not size_added and category == (current.get('category') or ''):
        return False
    stock.save_product(c, {
        'id': pid,
        'code': current['code'],
        'name': current['name'],
        'color': current['color'],
        'category': category,
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
    return size_added


def _ensure_product(c, product_info, supplier_id, csv_name, *, customer_id=None):
    if product_info.get('id'):
        size_added = _save_product_sizes_and_category(c, product_info['id'], product_info)
        return product_info['id'], False, size_added

    code = stock.text(product_info.get('code'))
    name = stock.text(csv_name)
    # 同一コミット内のサイズ違い行で重複商品を作らない
    if is_custom_order_code(code):
        # 別注: 品番一致だけでは再利用しない。顧客取扱＋商品名全文で特定。
        matches = find_custom_order_products_for_customer(
            c, name, customer_id=customer_id,
        )
        if len(matches) == 1:
            product_info = {**product_info, 'id': matches[0]['id'], 'sizeAction': 'add'}
            size_added = _save_product_sizes_and_category(c, matches[0]['id'], product_info)
            return matches[0]['id'], False, size_added
        category = stock.normalize_category(
            product_info.get('category') or stock.CATEGORY_UNIFORM
        )
        saved = stock.save_product(c, {
            'code': CUSTOM_ORDER_CODE,
            'name': csv_name,  # 色・加工を含む商品名をそのまま（自動分解しない）
            'color': '',
            'category': category,
            'supplierId': supplier_id,
            'sizes': [product_info['size']],
            'links': [],
            'active': True,
        })
        return saved['id'], True, False

    if code:
        matches = find_products_by_code(c, code)
        narrowed = matches
        if supplier_id is not None and len(matches) > 1:
            by_sup = [m for m in matches if m['supplier_id'] == supplier_id]
            if by_sup:
                narrowed = by_sup
        by_name = [m for m in narrowed if stock.text(m['name']) == name]
        if len(by_name) == 1:
            narrowed = by_name
        if len(narrowed) == 1:
            product_info = {**product_info, 'id': narrowed[0]['id'], 'sizeAction': 'add'}
            size_added = _save_product_sizes_and_category(c, narrowed[0]['id'], product_info)
            return narrowed[0]['id'], False, size_added
    else:
        matches = find_blank_sku_products(c, name, supplier_id)
        if len(matches) == 1:
            product_info = {**product_info, 'id': matches[0]['id'], 'sizeAction': 'add'}
            size_added = _save_product_sizes_and_category(c, matches[0]['id'], product_info)
            return matches[0]['id'], False, size_added

    saved = stock.save_product(c, {
        'code': code,
        'name': csv_name,  # 加工記載を含む商品名をそのまま
        'color': '',  # 色必須にしない・無理抽出しない
        'category': stock.normalize_category(product_info.get('category') or ''),
        'supplierId': supplier_id,
        'sizes': [product_info['size']],
        'links': [],
        'active': True,
    })
    return saved['id'], True, False


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
            selected_category=sel.get('category') if 'category' in sel else None,
        ))
    dup_map = find_csv_duplicates(classified)
    for row in classified:
        if row['status'] == STATUS_EXCLUDED:
            continue
        dups = dup_map.get(row['rowNo'])
        if dups:
            live = [n for n in dups if not any(
                r['rowNo'] == n and r['status'] == STATUS_EXCLUDED for r in classified
            )]
            if len(live) > 1 and row['rowNo'] in live:
                row['status'] = STATUS_HOLD
                row['csvDuplicateRowNos'] = live

    hold_rows = [r for r in classified if r['status'] in (STATUS_REVIEW, STATUS_HOLD)]
    excluded_rows = [r for r in classified if r['status'] == STATUS_EXCLUDED]
    commit_rows = []
    for row in classified:
        if row['status'] == STATUS_EXCLUDED:
            continue
        if explicit_include is not None:
            if row['rowNo'] in explicit_include or str(row['rowNo']) in explicit_include:
                if row['status'] in (STATUS_REVIEW, STATUS_HOLD, STATUS_UNCHANGED, STATUS_EXCLUDED):
                    raise ValueError(f'{row["rowNo"]}行目は要確認・保留・変更なし・対象外のため登録対象にできません。')
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
        'held': len(hold_rows), 'excluded': len(excluded_rows), 'failed': 0,
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
                # 数量更新でも対応記録を残す（再取込の手がかり）
                handle_id = (row.get('handle') or {}).get('id')
                product_id = (row.get('product') or {}).get('id')
                customer_id = (row.get('customer') or {}).get('id')
                if handle_id and product_id and customer_id:
                    upsert_row_map(
                        c, run_id=run_id, file_sha256=uploaded['fileSha256'],
                        file_name=uploaded['fileName'], row=row,
                        customer_id=customer_id, product_id=product_id,
                        customer_product_id=handle_id, opening_token='',
                        lot_ids=stock_info.get('lotIds') or [],
                    )
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

            pid, created, size_added = _ensure_product(
                c, row['product'], sup_id, row['csv']['productName'],
                customer_id=cust_id,
            )
            if created:
                result_counts['productsCreated'] += 1
            elif size_added:
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
                'supplier': row['csv'].get('supplier') or '',
                'customer': row['csv'].get('customer') or '',
                'usedMarker': bool(row['csv'].get('usedMarker')),
            }
            handle_info = _handle_preview(c, cust_id, pid, row_no, csv_source=csv_source)
            sel = selections.get(str(row_no)) or selections.get(row_no) or {}
            if sel.get('handleId'):
                handle_info['id'] = sel['handleId']
                handle_info['action'] = 'reuse'
            elif row.get('handle', {}).get('id'):
                handle_info['id'] = row['handle']['id']
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
                    upsert_row_map(
                        c, run_id=run_id, file_sha256=uploaded['fileSha256'],
                        file_name=uploaded['fileName'], row=row,
                        customer_id=cust_id, product_id=pid, customer_product_id=cp_id,
                        opening_token='', lot_ids=stock_info.get('lotIds') or [],
                    )
                    continue
                raise ValueError('在庫が既にあるため新規登録できません。数量差異として選択し直してください。')

            token_src = json.dumps({
                'runId': run_id, 'rowNo': row_no, 'sha': uploaded['fileSha256'],
                'cp': cp_id, 'size': row['product']['size'], 'loc': loc_id,
                'cond': row['condition'], 'cost': row['unitCost'], 'qty': row['qty'],
                'basis': basis,
                # 行番号以外の安定キーも含め、並び替えCSVでも同一在庫へ寄せられるようにする
                'map': stock_map_key(
                    customer=row['csv']['customer'], code=row['csv']['code'],
                    product_name=row['csv']['productName'], size=row['product']['size'],
                    condition=row.get('condition'), unit_cost=row.get('unitCost'),
                    site=row['csv'].get('site') or '', shelf=row['csv'].get('shelf') or '',
                ),
            }, ensure_ascii=False, sort_keys=True)
            open_token = 'legacy-csv-' + hashlib.sha256(token_src.encode()).hexdigest()[:40]
            # 品番・商品名・元行番号を履歴に残し、後から加工整理できるようにする（商品名の加工記載は消さない）
            note = (
                f'{LEGACY_IMPORT_NOTE}（CSV {row_no}行目 / 元品番 {row["csv"]["code"]} '
                f'/ {row["csv"]["productName"]}）'
            )
            if handle_info.get('pendingEmbroiderySort'):
                note += '／加工仕様は後整理（既存取扱の加工は未変更）'
            opening = stock.register_opening(c, {
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
            lot_ids = []
            if isinstance(opening, dict):
                lot_ids = [
                    x.get('id') or x.get('lotId')
                    for x in (opening.get('lots') or opening.get('lines') or [])
                    if isinstance(x, dict) and (x.get('id') or x.get('lotId'))
                ]
            if not lot_ids:
                lot_ids = [
                    r['id'] for r in c.execute(
                        '''SELECT id FROM lots
                           WHERE customer_product_id=? AND size_label=? AND location_id=?
                             AND condition=? AND unit_cost=?
                           ORDER BY id DESC''',
                        (cp_id, row['product']['size'], loc_id, row['condition'], row['unitCost']),
                    )
                ]
            upsert_row_map(
                c, run_id=run_id, file_sha256=uploaded['fileSha256'],
                file_name=uploaded['fileName'], row=row,
                customer_id=cust_id, product_id=pid, customer_product_id=cp_id,
                opening_token=open_token, lot_ids=lot_ids,
            )
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
        'excludedRowCount': len(excluded_rows),
        'excludedRows': [
            {
                'rowNo': r['rowNo'],
                'reason': r.get('excludeReason') or (r['reasons'][0] if r['reasons'] else ''),
            }
            for r in excluded_rows[:200]
        ],
        'excludedRowsTruncated': len(excluded_rows) > 200,
        'failures': failures,
        'fileSha256': uploaded['fileSha256'],
        'fileName': uploaded['fileName'],
        'notices': [
            f'確定 {len(commit_rows)} 件、保留・要確認 {len(hold_rows)} 件、'
            f'対象外 {len(excluded_rows)} 件を残しました。',
            '保留・要確認は登録していません（黙って除外していません）。',
            '対象外はエラーではなく登録しません。',
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
