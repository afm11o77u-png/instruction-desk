"""旧システム「商品台帳.csv」からの商品・顧客取扱一括登録。

初期在庫用の旧CSV取込（legacy_csv_import）とは別入口・別処理。
在庫数量・仕入単価・初期在庫・入出庫履歴は登録・変更しない。
プレビュー段階では DB を変更しない。確定は一括トランザクション。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
from collections import defaultdict

import legacy_csv_import as legacy
import stock
import stock_product_merge

REQUIRED_COLUMNS = ('顧客', '仕入先', '商品コード', '商品名', 'サイズ')
IMPORT_NOTE = '旧商品台帳CSV取込'
PENDING_EMBROIDERY = legacy.PENDING_EMBROIDERY

STATUS_CREATE = 'create'
STATUS_REUSE = 'reuse'
STATUS_SIZE_ADD = 'size_add'
STATUS_UNCHANGED = 'unchanged'
STATUS_REVIEW = 'needs_review'
STATUS_HOLD = 'hold'
STATUS_EXCLUDED = 'excluded'
STATUS_MERGE = 'merge'  # 既存重複を統合してから登録

STATUS_LABELS = {
    STATUS_CREATE: '新規',
    STATUS_REUSE: '既存使用',
    STATUS_SIZE_ADD: 'サイズ追加',
    STATUS_UNCHANGED: '変更なし',
    STATUS_REVIEW: '要確認',
    STATUS_HOLD: '保留',
    STATUS_EXCLUDED: '対象外',
    STATUS_MERGE: '統合して登録',
}

SKU_DECISION_MERGE = 'merge'
SKU_DECISION_SEPARATE = 'separate'
SKU_DECISION_HOLD = 'hold'


def _collapse_space(value):
    return re.sub(r'[\s\u3000]+', '', stock.text(value))


def identity_match_key(value):
    return legacy.identity_match_key(value)


def match_group_key(*, customer, code, product_name, supplier):
    """同じ照合条件の行に選択結果をまとめて適用するためのキー。

    通常品番は品番単位（1品番=1商品）。サイズ・顧客差だけでは分けない。
    別注・品番なしは従来どおり顧客・名前等で区別する。
    """
    code_t = stock.text(code)
    if legacy.is_custom_order_code(code_t):
        raw = '|'.join([
            'custom',
            identity_match_key(customer),
            code_t,
            identity_match_key(product_name),
        ])
    elif not code_t:
        raw = '|'.join([
            'blank',
            identity_match_key(customer),
            identity_match_key(product_name),
            identity_match_key(supplier),
        ])
    elif code_t == stock_product_merge.UNKNOWN_CODE:
        raw = '|'.join([
            'unknown',
            identity_match_key(customer),
            code_t,
            identity_match_key(product_name),
            identity_match_key(supplier),
        ])
    else:
        # 通常品番: 品番だけでグループ（名差・仕入先差は品番比較UIで解決）
        raw = '|'.join(['normal', code_t])
    return hashlib.sha256(raw.encode()).hexdigest()


def sku_variant_key(*, product_name, color='', supplier=''):
    """CSV内の商品内容バリアント（サイズ・顧客は含めない）。

    通常品番の商品名は先頭中古標記「＊/*」だけ無視して同一視する。
    """
    return '|'.join([
        product_name_core_key(product_name),
        identity_match_key(color),
        identity_match_key(supplier),
    ])


def product_map_key(*, customer, code, product_name):
    return legacy.product_map_key(customer=customer, code=code, product_name=product_name)


def row_map_key(*, customer, code, product_name, size, supplier):
    """台帳取込の行対応キー（在庫キーは持たない）。"""
    raw = '|'.join([
        identity_match_key(customer),
        stock.text(code),
        identity_match_key(product_name),
        stock.text(size),
        identity_match_key(supplier),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def decode_csv_bytes(blob):
    return legacy.decode_csv_bytes(blob)


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
    # UTF-8 BOM 付き見出しの残骸を除去
    if fields and fields[0].startswith('\ufeff'):
        fields[0] = fields[0].lstrip('\ufeff')
    missing = [col for col in REQUIRED_COLUMNS if col not in fields]
    if missing:
        raise ValueError(
            '必要な列がありません: ' + '、'.join(missing)
            + '。列を推測して取り込むことはしません。見出しを確認してください。'
        )
    rows = []
    for i, raw in enumerate(reader, start=2):
        item = {}
        for k in fields:
            v = raw.get(k)
            item[k] = '' if v is None else v
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
    name = stock.text(body.get('name') or body.get('fileName') or '商品台帳.csv') or '商品台帳.csv'
    sha = hashlib.sha256(blob).hexdigest()
    return {
        'fileName': name,
        'fileSha256': sha,
        'fileSize': len(blob),
        'encoding': encoding,
        'fields': fields,
        'rows': rows,
        'blob': blob,
    }


def used_marker_possible(product_name):
    return legacy.strip_used_marker(product_name)[1]


def product_name_core_key(name):
    return legacy.product_name_core_key(name)


def prefer_unmarked_product_name(names):
    return legacy.prefer_unmarked_product_name(names)


def names_differ_only_by_used_marker(a, b):
    return legacy.names_differ_only_by_used_marker(a, b)


def ensure_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS product_ledger_import_runs (
      id INTEGER PRIMARY KEY,
      file_sha256 TEXT NOT NULL,
      file_name TEXT NOT NULL,
      file_size INTEGER NOT NULL,
      row_count INTEGER NOT NULL,
      actor_name TEXT NOT NULL DEFAULT '',
      preview_at TEXT NOT NULL,
      preview_fingerprint TEXT NOT NULL DEFAULT '',
      commit_token TEXT UNIQUE,
      committed_at TEXT,
      result_json TEXT NOT NULL DEFAULT '{}',
      backup_path TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_import_runs_sha '
        'ON product_ledger_import_runs(file_sha256, created_at DESC)'
    )
    c.execute('''CREATE TABLE IF NOT EXISTS product_ledger_row_maps (
      id INTEGER PRIMARY KEY,
      product_map_key TEXT NOT NULL,
      row_map_key TEXT NOT NULL,
      match_group_key TEXT NOT NULL DEFAULT '',
      file_sha256 TEXT NOT NULL DEFAULT '',
      file_name TEXT NOT NULL DEFAULT '',
      source_row_no INTEGER,
      csv_customer TEXT NOT NULL DEFAULT '',
      csv_supplier TEXT NOT NULL DEFAULT '',
      csv_product_code TEXT NOT NULL DEFAULT '',
      csv_product_name TEXT NOT NULL DEFAULT '',
      csv_size TEXT NOT NULL DEFAULT '',
      customer_id INTEGER,
      product_id INTEGER,
      customer_product_id INTEGER,
      run_id INTEGER,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_row_maps_product_key '
        'ON product_ledger_row_maps(product_map_key, id DESC)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_row_maps_row_key '
        'ON product_ledger_row_maps(row_map_key, id DESC)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_row_maps_file '
        'ON product_ledger_row_maps(file_sha256, source_row_no)'
    )


def db_fingerprint(c):
    ensure_schema(c)
    parts = []
    tables = (
        'customers', 'suppliers', 'products', 'product_sizes', 'product_suppliers',
        'customer_products', 'lots', 'product_ledger_row_maps',
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
    lot_cost = c.execute(
        'SELECT COALESCE(SUM(qty_remaining * unit_cost),0) AS a FROM lots'
    ).fetchone()['a']
    move_n = c.execute('SELECT COUNT(*) AS n FROM moves').fetchone()['n']
    parts.append(f'qty:{lot_sum}')
    parts.append(f'amount:{lot_cost}')
    parts.append(f'moves:{move_n}')
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:40]


def stock_snapshot(c):
    """取込前後の在庫不変検証用。"""
    row = c.execute(
        '''SELECT COUNT(*) AS lots,
                  COALESCE(SUM(qty_remaining),0) AS qty,
                  COALESCE(SUM(qty_remaining * unit_cost),0) AS amount
           FROM lots'''
    ).fetchone()
    moves = c.execute('SELECT COUNT(*) AS n FROM moves').fetchone()['n']
    return {
        'lots': row['lots'],
        'qty': row['qty'],
        'amount': row['amount'],
        'moves': moves,
    }


def previous_imports_for_file(c, sha):
    ensure_schema(c)
    rows = c.execute(
        '''SELECT id,file_name,file_size,row_count,actor_name,preview_at,committed_at,
                  commit_token,result_json,created_at,backup_path
           FROM product_ledger_import_runs WHERE file_sha256=? ORDER BY id DESC LIMIT 5''',
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
            'actorName': r['actor_name'],
            'previewAt': r['preview_at'],
            'committedAt': r['committed_at'],
            'createdAt': r['created_at'],
            'backupPath': r['backup_path'] or '',
            'result': result if isinstance(result, dict) else {},
        })
    return out


def lookup_row_map(c, *, customer, code, product_name, size, supplier):
    ensure_schema(c)
    key = row_map_key(
        customer=customer, code=code, product_name=product_name, size=size, supplier=supplier,
    )
    return c.execute(
        '''SELECT * FROM product_ledger_row_maps
           WHERE row_map_key=? ORDER BY id DESC LIMIT 1''',
        (key,),
    ).fetchone()


def lookup_product_map(c, *, customer, code, product_name):
    ensure_schema(c)
    key = product_map_key(customer=customer, code=code, product_name=product_name)
    return c.execute(
        '''SELECT * FROM product_ledger_row_maps
           WHERE product_map_key=? ORDER BY id DESC LIMIT 1''',
        (key,),
    ).fetchone()


def upsert_row_map(
    c, *, run_id, file_sha256, file_name, row, customer_id, product_id, customer_product_id,
):
    ensure_schema(c)
    csv_info = row.get('csv') or {}
    customer = stock.text(csv_info.get('customer'))
    supplier = stock.text(csv_info.get('supplier'))
    code = stock.text(csv_info.get('code'))
    product_name = stock.text(csv_info.get('productName'))
    size = stock.text(csv_info.get('size'))
    pkey = product_map_key(customer=customer, code=code, product_name=product_name)
    rkey = row_map_key(
        customer=customer, code=code, product_name=product_name, size=size, supplier=supplier,
    )
    gkey = row.get('matchGroupKey') or match_group_key(
        customer=customer, code=code, product_name=product_name, supplier=supplier,
    )
    t = stock.stamp()
    existing = c.execute(
        'SELECT id FROM product_ledger_row_maps WHERE row_map_key=? ORDER BY id DESC LIMIT 1',
        (rkey,),
    ).fetchone()
    vals = (
        pkey, rkey, gkey, file_sha256, file_name, row.get('rowNo'),
        customer, supplier, code, product_name, size,
        customer_id, product_id, customer_product_id, run_id, t,
    )
    if existing:
        c.execute(
            '''UPDATE product_ledger_row_maps SET
                 product_map_key=?, row_map_key=?, match_group_key=?, file_sha256=?, file_name=?,
                 source_row_no=?, csv_customer=?, csv_supplier=?, csv_product_code=?,
                 csv_product_name=?, csv_size=?, customer_id=?, product_id=?,
                 customer_product_id=?, run_id=?, updated_at=?
               WHERE id=?''',
            vals + (existing['id'],),
        )
        return existing['id']
    c.execute(
        '''INSERT INTO product_ledger_row_maps(
             product_map_key,row_map_key,match_group_key,file_sha256,file_name,source_row_no,
             csv_customer,csv_supplier,csv_product_code,csv_product_name,csv_size,
             customer_id,product_id,customer_product_id,run_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        vals + (t,),
    )
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _person_preview(c, table, label, name, row_no):
    return legacy._person_preview(c, table, label, name, row_no)


def _product_candidate(m, c=None):
    return legacy._product_candidate(m, c)


def _load_product(c, product_id):
    if not product_id:
        return None
    live_id = stock_product_merge.resolve_live_product_id(c, product_id)
    return c.execute(
        '''SELECT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,
                  p.merged_into_id,s.name AS supplier_name
           FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?''',
        (live_id,),
    ).fetchone()


def _finalize_matched_product(
    c, info, prod, name, size_label, supplier_id, *,
    proposed_category, selected_category, apply_category=False,
    allow_name_diff=False,
):
    row_no = info.get('_rowNo')
    merged_into = prod['merged_into_id'] if 'merged_into_id' in prod.keys() else None
    if merged_into not in (None, ''):
        live = _load_product(c, prod['id'])
        if not live:
            info['action'] = 'review'
            info['reasons'].append(f'{row_no}行目：統合先の商品が見つかりません。')
            return info
        prod = live
    if not prod['active']:
        info['action'] = 'review'
        info['id'] = prod['id']
        info['reasons'].append(
            f'{row_no}行目：商品ID {prod["id"]} は停止中です。重複追加・無断再開しません。'
        )
        return info
    if stock.text(prod['name']) != name:
        if names_differ_only_by_used_marker(prod['name'], name):
            # 先頭の中古標記だけの差 → 同一商品。原文は別名に残す。
            info['usedMarkerNameDiff'] = True
        elif not allow_name_diff:
            # 別注・品番なし等: 括弧付き名・加工表記差は自動同一化しない
            info['action'] = 'review'
            info['id'] = prod['id']
            info['candidates'] = [_product_candidate(prod, c)]
            info['nameDiff'] = True
            info['reasons'].append(
                f'{row_no}行目：品番は一致しますが商品名が異なります'
                f'（既存: {prod["name"]} / CSV: {name}）。候補から選ぶか新規にしてください。'
            )
            return info
        else:
            # 通常品番: 名差だけでは別商品にしない。公式名は品番比較UIで確定。
            info['nameDiff'] = True
            info['reasons'].append(
                f'{row_no}行目：品番は一致、商品名が異なります'
                f'（既存: {prod["name"]} / CSV: {name}）。'
                f'同じ商品としてまとめる場合は公式名を選んでください。'
            )
    info['action'] = 'reuse'
    info['id'] = prod['id']
    if supplier_id and prod['supplier_id'] not in (None, supplier_id):
        if stock.product_has_supplier(c, prod['id'], supplier_id):
            info['supplierKept'] = True
            info['subSupplierAdd'] = False
        else:
            info['supplierConflict'] = True
            info['supplierKept'] = True
            info['subSupplierAdd'] = True
            info['subSupplierId'] = supplier_id
    existing_sizes = {
        r['label'] for r in c.execute(
            'SELECT label FROM product_sizes WHERE product_id=?', (prod['id'],)
        )
    }
    if size_label not in existing_sizes:
        info['sizeAction'] = 'add'
    else:
        info['sizeAction'] = 'reuse'
    existing_cat = prod['category'] if 'category' in prod.keys() else ''
    if apply_category:
        legacy._apply_category_decision(
            info, existing_cat, proposed_category, selected_category,
        )
    else:
        info['category'] = existing_cat or ''
        info['proposedCategory'] = existing_cat or ''
        info['categoryAction'] = 'keep'
        info['categoryConflict'] = False
    info['codeDisplay'] = stock.product_code_display(
        info['code'], info.get('category') or proposed_category,
    )
    return info


def _product_preview(
    c, item, supplier_info, row_no, *,
    customer_name='', customer_id=None, selected_category=None,
    mapped_product_id=None, selected_product_id=None, force_create=False,
):
    code = stock.text(item.get('商品コード'))
    # 日付・数値への自動変換はしない（CSVは文字列のまま）
    name_raw = item.get('商品名')
    name = stock.text(name_raw if isinstance(name_raw, str) else ('' if name_raw is None else str(name_raw)))
    size_raw = item.get('サイズ')
    size = size_raw if isinstance(size_raw, str) else ('' if size_raw is None else str(size_raw))
    size_label = size.strip()
    blank_sku = not code
    custom_order = legacy.is_custom_order_code(code)
    star = used_marker_possible(name)
    proposed_category = (
        legacy.proposed_blank_sku_category(customer_name, name) if blank_sku
        else (stock.CATEGORY_UNIFORM if custom_order else '')
    )
    if selected_category is not None and selected_category != '':
        try:
            proposed_category = stock.normalize_category(selected_category) or proposed_category
        except ValueError:
            pass
    info = {
        'code': code, 'name': name, 'size': size_label,
        'action': 'missing', 'id': None, 'candidates': [], 'reasons': [],
        'sizeAction': 'ok', 'nameDiff': False,
        'supplierConflict': False, 'supplierKept': False,
        'subSupplierAdd': False, 'subSupplierId': None,
        'category': proposed_category if (blank_sku or custom_order) else '',
        'proposedCategory': proposed_category if (blank_sku or custom_order) else '',
        'categoryAction': 'set' if (blank_sku or custom_order) else 'keep',
        'categoryConflict': False,
        'blankSku': blank_sku,
        'customOrder': custom_order,
        'usedMarkerPossible': star,
        'fromImportMap': False,
        'codeDisplay': stock.product_code_display(
            code, proposed_category if (blank_sku or custom_order) else '',
        ),
        '_rowNo': row_no,
    }
    if not name and not code:
        info['action'] = 'excluded'
        info['reasons'].append(f'{row_no}行目：商品名も品番もないため対象外です。')
        return info
    if not name:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：商品名が空です。')
        return info
    if not size_label:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：サイズが空です。')
        return info
    if star:
        info['reasons'].append(
            f'{row_no}行目：商品名先頭の「＊/*」は中古標記です。'
            f'同一品番で標記なし名とだけ違う場合は1商品へ自動まとめし、'
            f'公式名は標記なし、CSV原文は照合用別名に残します'
            f'（台帳取込では在庫数量・新古は作りません）。'
        )

    supplier_id = supplier_info.get('id')

    if force_create:
        info['action'] = 'create'
        info['sizeAction'] = 'add'
        info['id'] = None
        return info

    if selected_product_id:
        prod = _load_product(c, selected_product_id)
        if not prod:
            info['action'] = 'review'
            info['reasons'].append(f'{row_no}行目：選択した商品が見つかりません。')
            return info
        # 選択時は名差・仕入先差を許容し、通常仕入先は変更しない
        if not prod['active']:
            info['action'] = 'review'
            info['id'] = prod['id']
            info['reasons'].append(f'{row_no}行目：選択した商品は停止中です。')
            return info
        info['action'] = 'reuse'
        info['id'] = prod['id']
        if stock.text(prod['name']) != name:
            if names_differ_only_by_used_marker(prod['name'], name):
                info['usedMarkerNameDiff'] = True
            else:
                info['nameDiff'] = True
        if supplier_id and prod['supplier_id'] not in (None, supplier_id):
            if stock.product_has_supplier(c, prod['id'], supplier_id):
                info['supplierKept'] = True
            else:
                info['supplierConflict'] = True
                info['supplierKept'] = True
                info['subSupplierAdd'] = True
                info['subSupplierId'] = supplier_id
        existing_sizes = {
            r['label'] for r in c.execute(
                'SELECT label FROM product_sizes WHERE product_id=?', (prod['id'],)
            )
        }
        info['sizeAction'] = 'add' if size_label not in existing_sizes else 'reuse'
        info['category'] = prod['category'] or ''
        info['categoryAction'] = 'keep'
        info['codeDisplay'] = stock.product_code_display(code, info['category'])
        return info

    if mapped_product_id:
        prod = _load_product(c, mapped_product_id)
        if prod and prod['active']:
            info['fromImportMap'] = True
            return _finalize_matched_product(
                c, info, prod, name, size_label, supplier_id,
                proposed_category=proposed_category, selected_category=selected_category,
                apply_category=bool(blank_sku or custom_order),
            )

    if blank_sku:
        matches = legacy.find_blank_sku_products(c, name, supplier_id)
        if not matches and supplier_id is not None:
            loose = legacy.find_blank_sku_products(c, name, None)
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
                    info['reasons'].append(
                        f'{row_no}行目：カテゴリ「{selected_category}」は使えません。'
                    )
            info['codeDisplay'] = stock.product_code_display('', info['category'])
            return info
        if len(matches) > 1:
            info['action'] = 'review'
            info['candidates'] = [_product_candidate(m, c) for m in matches]
            info['reasons'].append(
                f'{row_no}行目：品番なしで商品名・仕入先が同じ商品が複数あります。'
            )
            return info
        return _finalize_matched_product(
            c, info, matches[0], name, size_label, supplier_id,
            proposed_category=proposed_category, selected_category=selected_category,
            apply_category=True,
        )

    if custom_order:
        info['category'] = proposed_category or stock.CATEGORY_UNIFORM
        info['proposedCategory'] = info['category']
        info['categoryAction'] = 'set'
        info['codeDisplay'] = stock.product_code_display(code, info['category'])
        matches = legacy.find_custom_order_products_for_customer(
            c, name, customer_id=customer_id,
        )
        if not matches:
            info['action'] = 'create'
            info['sizeAction'] = 'add'
            return info
        if len(matches) > 1:
            info['action'] = 'review'
            info['candidates'] = [_product_candidate(m, c) for m in matches]
            info['reasons'].append(
                f'{row_no}行目：品番「別注」で顧客・商品名が同じ商品が複数あります。'
            )
            return info
        return _finalize_matched_product(
            c, info, matches[0], name, size_label, supplier_id,
            proposed_category=info['category'], selected_category=selected_category,
            apply_category=True,
        )

    # 通常品番: 1品番=1商品。名差・仕入先差だけでは新規を作らない。
    matches = stock_product_merge.find_live_products_by_code(c, code)
    if not matches:
        # 旧商品名別名でも探す（再取込で重複新規を防ぐ）
        matches = stock_product_merge.find_products_by_name_alias(c, code, name)
    if not matches:
        info['action'] = 'create'
        info['sizeAction'] = 'add'
        return info

    if len(matches) > 1:
        # 先頭へ自動紐づけしない。品番比較UIで残す商品・統合を確認。
        info['action'] = 'review'
        info['candidates'] = [_product_candidate(m, c) for m in matches]
        info['existingDuplicates'] = True
        info['reasons'].append(
            f'{row_no}行目：品番「{code}」の登録商品が {len(matches)} 件あります。'
            f'先頭へ自動では紐づけません。品番ごとの比較で残す商品を選び、'
            f'必要なら統合してから登録してください。'
        )
        return info

    return _finalize_matched_product(
        c, info, matches[0], name, size_label, supplier_id,
        proposed_category='', selected_category=None, apply_category=False,
        allow_name_diff=True,
    )


def _handle_preview(c, customer_id, product_id, row_no, *, csv_source=None):
    return legacy._handle_preview(c, customer_id, product_id, row_no, csv_source=csv_source)


def _excluded_row(row_no, item, *, reason):
    customer = stock.text(item.get('顧客'))
    supplier = stock.text(item.get('仕入先'))
    code = stock.text(item.get('商品コード'))
    name = stock.text(item.get('商品名'))
    size_raw = item.get('サイズ')
    size = size_raw if isinstance(size_raw, str) else ('' if size_raw is None else str(size_raw))
    return {
        'rowNo': row_no,
        'status': STATUS_EXCLUDED,
        'statusLabel': STATUS_LABELS[STATUS_EXCLUDED],
        'matchGroupKey': match_group_key(
            customer=customer, code=code, product_name=name, supplier=supplier,
        ),
        'csv': {
            'customer': customer,
            'supplier': supplier,
            'code': code,
            'codeDisplay': stock.product_code_display(code, ''),
            'productName': name,
            'size': size.strip(),
            'usedMarkerPossible': used_marker_possible(name),
        },
        'customer': {'action': 'excluded', 'id': None, 'candidates': [], 'reasons': []},
        'supplier': {'action': 'excluded', 'id': None, 'candidates': [], 'reasons': []},
        'product': {
            'action': 'excluded', 'id': None, 'code': code, 'name': name,
            'size': size.strip(), 'candidates': [], 'reasons': [reason],
            'sizeAction': 'ok',
        },
        'handle': {'action': 'excluded', 'id': None, 'candidates': [], 'reasons': []},
        'reasons': [reason],
        'canCommit': False,
    }


def classify_row(
    c, row_no, item, *,
    selected_product_id=None, selected_handle_id=None, selected_category=None,
    force_create_product=False,
):
    customer_name = stock.text(item.get('顧客'))
    supplier_name = stock.text(item.get('仕入先'))
    code = stock.text(item.get('商品コード'))
    product_name = stock.text(item.get('商品名'))
    size_raw = item.get('サイズ')
    size = size_raw if isinstance(size_raw, str) else ('' if size_raw is None else str(size_raw))
    size_label = size.strip()
    group_key = match_group_key(
        customer=customer_name, code=code, product_name=product_name, supplier=supplier_name,
    )
    reasons = []

    if not customer_name and not product_name and not code:
        return _excluded_row(row_no, item, reason=f'{row_no}行目：顧客・商品名・品番が空です。')

    if not customer_name:
        # 顧客なしは取扱を作れない
        cust = {
            'action': 'review', 'id': None, 'name': '', 'candidates': [],
            'reasons': [f'{row_no}行目：顧客が空です。'],
        }
    else:
        cust = _person_preview(c, 'customers', '顧客', customer_name, row_no)

    if supplier_name:
        supp = _person_preview(c, 'suppliers', '仕入先', supplier_name, row_no)
    else:
        supp = {
            'action': 'missing', 'id': None, 'name': '', 'candidates': [],
            'reasons': [f'{row_no}行目：仕入先が空です（未設定のまま登録できます）。'],
        }

    mapped = lookup_row_map(
        c, customer=customer_name, code=code, product_name=product_name,
        size=size_label, supplier=supplier_name,
    )
    mapped_product_id = None
    mapped_handle_id = None
    if mapped:
        mapped_product_id = mapped['product_id']
        mapped_handle_id = mapped['customer_product_id']
    else:
        pm = lookup_product_map(c, customer=customer_name, code=code, product_name=product_name)
        if pm:
            mapped_product_id = pm['product_id']

    product = _product_preview(
        c, item, supp, row_no,
        customer_name=customer_name,
        customer_id=cust.get('id'),
        selected_category=selected_category,
        mapped_product_id=mapped_product_id,
        selected_product_id=selected_product_id,
        force_create=force_create_product,
    )

    handle = {'action': 'defer', 'id': None, 'candidates': [], 'reasons': [], 'embroidery': PENDING_EMBROIDERY}
    if cust.get('action') == 'review' or product.get('action') in ('review', 'excluded', 'missing'):
        if product.get('action') == 'excluded':
            handle = {'action': 'excluded', 'id': None, 'candidates': [], 'reasons': []}
        else:
            handle = {
                'action': 'defer', 'id': None, 'candidates': [], 'reasons': [],
                'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
            }
    elif selected_handle_id:
        row = c.execute(
            'SELECT id,embroidery,active FROM customer_products WHERE id=?',
            (selected_handle_id,),
        ).fetchone()
        if row and row['active']:
            handle = legacy._reuse_handle(
                {
                    'action': 'reuse', 'id': None, 'candidates': [], 'reasons': [],
                    'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
                    'pendingEmbroiderySort': True, 'csvSource': {},
                },
                row, pending_sort=True,
            )
        else:
            handle = {
                'action': 'review', 'id': None, 'candidates': [],
                'reasons': [f'{row_no}行目：選択した取扱が使えません。'],
                'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
            }
    elif mapped_handle_id and product.get('id'):
        row = c.execute(
            'SELECT id,embroidery,active,customer_id,product_id FROM customer_products WHERE id=?',
            (mapped_handle_id,),
        ).fetchone()
        live_pid = product['id']
        if row and row['active'] and row['product_id'] == live_pid and (
            not cust.get('id') or row['customer_id'] == cust['id']
        ):
            handle = legacy._reuse_handle(
                {
                    'action': 'reuse', 'id': None, 'candidates': [], 'reasons': [],
                    'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
                    'pendingEmbroiderySort': True, 'csvSource': {},
                },
                row, pending_sort=True,
            )
        else:
            handle = _handle_preview(
                c, cust.get('id'), product.get('id'), row_no,
                csv_source={
                    'sourceRowNo': row_no, 'productCode': code, 'productName': product_name,
                    'supplier': supplier_name, 'customer': customer_name,
                    'usedMarker': used_marker_possible(product_name),
                },
            )
    elif product.get('id') and cust.get('id'):
        handle = _handle_preview(
            c, cust.get('id'), product.get('id'), row_no,
            csv_source={
                'sourceRowNo': row_no, 'productCode': code, 'productName': product_name,
                'supplier': supplier_name, 'customer': customer_name,
                'usedMarker': used_marker_possible(product_name),
            },
        )
    elif product.get('action') == 'create' and cust.get('action') in ('create', 'reuse'):
        handle = {
            'action': 'create', 'id': None, 'candidates': [], 'reasons': [],
            'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
            'pendingEmbroiderySort': True,
        }

    for block in (cust, supp, product, handle):
        reasons.extend(block.get('reasons') or [])

    # ステータス集約（要確認・対象外を優先）
    if product.get('action') == 'excluded':
        status = STATUS_EXCLUDED
    elif (
        cust.get('action') == 'review'
        or product.get('action') == 'review'
        or handle.get('action') == 'review'
        or (supplier_name and supp.get('action') == 'review')
    ):
        status = STATUS_REVIEW
    elif (
        product.get('action') == 'create'
        or handle.get('action') == 'create'
        or cust.get('action') == 'create'
        or (supplier_name and supp.get('action') == 'create')
        or product.get('subSupplierAdd')
    ):
        status = STATUS_CREATE
    elif (
        product.get('action') == 'reuse'
        and handle.get('action') == 'reuse'
        and product.get('sizeAction') == 'add'
    ):
        status = STATUS_SIZE_ADD
    elif (
        product.get('action') == 'reuse'
        and handle.get('action') == 'reuse'
        and product.get('sizeAction') == 'reuse'
        and not product.get('subSupplierAdd')
        and cust.get('action') == 'reuse'
        and (not supplier_name or supp.get('action') in ('reuse', 'missing'))
    ):
        status = STATUS_UNCHANGED
    else:
        status = STATUS_REUSE

    can_commit = status in (STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD)
    return {
        'rowNo': row_no,
        'status': status,
        'statusLabel': STATUS_LABELS[status],
        'matchGroupKey': group_key,
        'csv': {
            'customer': customer_name,
            'supplier': supplier_name,
            'code': code,
            'codeDisplay': product.get('codeDisplay') or stock.product_code_display(code, product.get('category') or ''),
            'productName': product_name,
            'size': size_label,
            'usedMarkerPossible': used_marker_possible(product_name),
            'category': product.get('category') or '',
        },
        'customer': cust,
        'supplier': supp,
        'product': {k: v for k, v in product.items() if not str(k).startswith('_')},
        'handle': handle,
        'reasons': reasons,
        'canCommit': can_commit,
        'fromImportMap': bool(product.get('fromImportMap')),
    }


def _row_variant(row):
    csv = row.get('csv') or {}
    name = csv.get('productName') or ''
    return {
        'key': sku_variant_key(
            product_name=name,
            color='',
            supplier=csv.get('supplier') or '',
        ),
        'productName': name,
        'productNameCore': prefer_unmarked_product_name([name]) or name,
        'supplier': csv.get('supplier') or '',
        'color': '',
        'rowNos': [],
        'sizes': [],
        'customers': [],
        'csvNames': [],
    }


def _sku_product_plan(group):
    """サイズ・顧客差の自動まとめ／比較用の商品計画サマリ。"""
    variants = group.get('variants') or []
    all_sizes = []
    all_customers = []
    all_csv_names = []
    for v in variants:
        for size in v.get('sizes') or []:
            if size and size not in all_sizes:
                all_sizes.append(size)
        for cust in v.get('customers') or []:
            if cust and cust not in all_customers:
                all_customers.append(cust)
        for n in v.get('csvNames') or ([v.get('productName')] if v.get('productName') else []):
            if n and n not in all_csv_names:
                all_csv_names.append(n)
    primary = variants[0] if variants else {}
    official = prefer_unmarked_product_name(all_csv_names) or (primary.get('productName') or '')
    # 既存1件で標記差だけのときは、標記なし公式名を優先（既存が標記付きなら書き換え候補）
    if group.get('existingCount') == 1 and not group.get('needsDecision'):
        existing_name = (group.get('existingProducts') or [{}])[0].get('name') or ''
        official = prefer_unmarked_product_name([*all_csv_names, existing_name]) or official
    if group.get('existingCount') == 1 and not group.get('needsDecision'):
        plan_kind = 'reuse'
        plan_label = '既存商品を使用'
    elif group.get('existingCount') > 1:
        plan_kind = 'compare'
        plan_label = '品番比較が必要'
    elif group.get('needsDecision'):
        plan_kind = 'compare'
        plan_label = '品番比較が必要'
    else:
        plan_kind = 'create'
        plan_label = '新規商品'
    return {
        'kind': plan_kind,
        'label': plan_label,
        'code': group.get('code') or '',
        'productName': official,
        'supplier': primary.get('supplier') or '',
        'customers': all_customers,
        'sizes': all_sizes,
        'sizeCount': len(all_sizes),
        'customerCount': len(all_customers),
        'csvRowCount': group.get('rowCount') or 0,
        'variantCount': group.get('variantCount') or 0,
        'csvNames': all_csv_names,
        'usedMarkerAutoMerged': bool(group.get('usedMarkerAutoMerged')),
    }


def build_sku_groups(c, classified):
    """通常品番の比較グループを作る。

    品番+商品名+仕入先が同じでサイズ／顧客だけの差 → 自動で1商品（needsDecision=False）。
    先頭中古標記「＊/*」だけの商品名差も自動まとめ（標記なしを公式名）。
    商品名・仕入先・色・既存重複など判断が必要な差だけ品番比較UIへ。
    既存DBに同一品番が複数ある場合は自動まとめしない（重複整理の確認フローへ）。
    """
    by_code = defaultdict(list)
    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code'))
        if stock_product_merge.is_normal_product_code(code):
            by_code[code].append(row)

    groups = []
    for code in sorted(by_code.keys()):
        rows = by_code[code]
        variants_map = {}
        for row in rows:
            v = _row_variant(row)
            key = v['key']
            if key not in variants_map:
                variants_map[key] = v
            slot = variants_map[key]
            slot['rowNos'].append(row['rowNo'])
            csv_name = (row.get('csv') or {}).get('productName') or ''
            if csv_name and csv_name not in slot['csvNames']:
                slot['csvNames'].append(csv_name)
            # 表示用は標記なしを優先（バリアント代表名）
            if csv_name and (
                not slot['productName']
                or (
                    used_marker_possible(slot['productName'])
                    and not used_marker_possible(csv_name)
                )
            ):
                slot['productName'] = csv_name
            size = (row.get('csv') or {}).get('size') or ''
            if size and size not in slot['sizes']:
                slot['sizes'].append(size)
            cust = (row.get('csv') or {}).get('customer') or ''
            if cust and cust not in slot['customers']:
                slot['customers'].append(cust)

        variants = list(variants_map.values())
        for v in variants:
            v['productName'] = prefer_unmarked_product_name(v.get('csvNames') or [v.get('productName')]) or v.get('productName') or ''
        existing = stock_product_merge.find_live_products_by_code(c, code)
        existing_cards = [_product_candidate(m, c) for m in existing]
        existing_cores = {product_name_core_key(m['name']) for m in existing}
        csv_cores = {product_name_core_key(n) for v in variants for n in (v.get('csvNames') or [v.get('productName') or '']) if n}
        # コア名が既存と一致しないときだけ名差（標記だけの差は一致扱い）
        name_mismatch = bool(existing) and bool(csv_cores - existing_cores)
        # 別名ヒットのみでも公式名差として扱う（中古標記だけの差は除外）
        for row in rows:
            if row.get('product', {}).get('nameDiff'):
                name_mismatch = True
                break

        used_marker_auto = False
        all_csv_names = []
        for v in variants:
            for n in v.get('csvNames') or []:
                if n and n not in all_csv_names:
                    all_csv_names.append(n)
        if len({product_name_core_key(n) for n in all_csv_names if n}) == 1 and any(
            used_marker_possible(n) for n in all_csv_names
        ):
            used_marker_auto = len({stock.text(n) for n in all_csv_names}) > 1 or any(
                used_marker_possible(n) for n in all_csv_names
            )

        # サイズ・顧客・中古標記だけの差はバリアントに出ない
        needs_decision = (
            len(variants) > 1
            or len(existing) > 1
            or name_mismatch
        )
        planned_single = not needs_decision

        apply_scope = (
            f'品番「{code}」の対象行 {len(rows)} 件'
            f'（内容の違い {len(variants)} 種 / 登録済 {len(existing)} 件）。'
            f'サイズ・顧客・先頭「＊/*」中古標記だけの差は同じ商品へまとめます。'
            f'加工仕様の違いは取扱として別管理し、商品を勝手に1つへ潰しません。'
        )

        group = {
            'code': code,
            'codeDisplay': code,
            'rowNos': [r['rowNo'] for r in rows],
            'rowCount': len(rows),
            'variants': variants,
            'variantCount': len(variants),
            'existingProducts': existing_cards,
            'existingCount': len(existing),
            'needsDecision': needs_decision,
            'plannedSingleProduct': planned_single,
            'usedMarkerAutoMerged': bool(used_marker_auto and planned_single),
            'applyScope': apply_scope,
            'decision': None,
            'status': 'needs_decision' if needs_decision else 'auto',
        }
        group['productPlan'] = _sku_product_plan(group)
        groups.append(group)
    return groups


def _attach_product_plans(classified, sku_groups):
    """各行に商品計画（品番単位のまとめ先）を付け、行トレースを残す。"""
    by_code = {g['code']: g for g in sku_groups}
    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code'))
        group = by_code.get(code)
        if not group:
            continue
        plan = dict(group.get('productPlan') or {})
        row['productPlan'] = plan
        row['skuAutoMerged'] = bool(group.get('plannedSingleProduct'))
        row['usedMarkerAutoMerged'] = bool(group.get('usedMarkerAutoMerged'))
        row['skuRowNos'] = list(group.get('rowNos') or [])


def _apply_auto_merge_official_names(classified, sku_groups):
    """自動まとめ品番に公式名（標記なし優先）を付け、CSV原文を別名登録できるようにする。"""
    by_code = {g['code']: g for g in sku_groups}
    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code'))
        group = by_code.get(code)
        if not group or not group.get('plannedSingleProduct'):
            continue
        plan = group.get('productPlan') or {}
        official = stock.text(plan.get('productName') or '')
        if not official:
            continue
        prod = dict(row.get('product') or {})
        prod['officialName'] = official
        csv_name = stock.text((row.get('csv') or {}).get('productName') or '')
        if (
            group.get('usedMarkerAutoMerged')
            or used_marker_possible(csv_name)
            or (
                prod.get('id')
                and stock.text(prod.get('name') or '') != official
                and (
                    names_differ_only_by_used_marker(prod.get('name'), official)
                    or names_differ_only_by_used_marker(csv_name, official)
                )
            )
        ):
            prod['applyOfficialName'] = True
        row['product'] = prod


def _apply_sku_decisions(c, classified, sku_groups, sku_decisions):
    """品番決定を行ステータスへ反映。

    未決定でもサイズ行を要確認へ流し込まない。品番比較パネルで止め、
    行ステータスは新規／既存使用／サイズ追加のまま残す。
    """
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    group_by_code = {g['code']: g for g in sku_groups}
    hold_count = 0
    pending_decision = 0

    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code'))
        if not stock_product_merge.is_normal_product_code(code):
            continue
        group = group_by_code.get(code)
        if not group:
            continue
        raw = decisions.get(code) or decisions.get(str(code)) or {}
        if not isinstance(raw, dict):
            raw = {}
        decision = stock.text(raw.get('decision'))
        group['decision'] = raw if decision else None

        if not group['needsDecision']:
            continue

        if not decision:
            pending_decision += 1
            # サイズ・顧客だけの行差を要確認一覧へ溢れさせない
            row['canCommit'] = False
            row['skuDecisionNeeded'] = True
            row['reasons'] = list(row.get('reasons') or []) + [
                f'品番「{code}」の比較で【同じ商品としてまとめる】'
                f'【別物のため個別に整理する】【今回は保留する】のいずれかを選んでください。'
            ]
            continue

        row['skuDecision'] = decision
        row['skuApplyScope'] = group['applyScope']

        if decision == SKU_DECISION_HOLD:
            row['status'] = STATUS_HOLD
            row['statusLabel'] = STATUS_LABELS[STATUS_HOLD]
            row['canCommit'] = False
            row['deferReason'] = stock.text(raw.get('deferReason') or raw.get('reason') or '')
            hold_count += 1
            continue

        if decision == SKU_DECISION_MERGE:
            keep_id = raw.get('keepProductId') or raw.get('productId')
            official_name = stock.text(raw.get('officialName') or raw.get('name') or '')
            color = stock.text(raw.get('color') or '')
            primary_supplier = stock.text(raw.get('primarySupplier') or '')
            merge_source_ids = raw.get('mergeSourceIds') or []
            if isinstance(merge_source_ids, list):
                merge_source_ids = [int(x) for x in merge_source_ids if str(x).isdigit() or isinstance(x, int)]
            else:
                merge_source_ids = []

            if group['existingCount'] > 1 and not keep_id:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の既存商品が複数あります。残す商品IDを選んでください。'
                ]
                pending_decision += 1
                continue
            if not official_name:
                # 既存1件ならその公式名を仮採用可だが、名差があるなら明示を要求
                if group['existingCount'] == 1 and not any(
                    r.get('product', {}).get('nameDiff') for r in classified
                    if stock.text((r.get('csv') or {}).get('code')) == code
                ) and group['variantCount'] <= 1:
                    official_name = group['existingProducts'][0].get('name') or ''
                if not official_name:
                    row['status'] = STATUS_REVIEW
                    row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                    row['canCommit'] = False
                    row['reasons'] = list(row.get('reasons') or []) + [
                        f'品番「{code}」の公式商品名を指定してください。'
                    ]
                    pending_decision += 1
                    continue

            # 行ごとの登録先
            if keep_id:
                row['product'] = dict(row.get('product') or {})
                row['product']['id'] = int(keep_id)
                row['product']['action'] = 'reuse'
                row['product']['officialName'] = official_name
                row['product']['officialColor'] = color
                row['product']['primarySupplierName'] = primary_supplier
                row['product']['mergeSourceIds'] = merge_source_ids
                row['product']['skuMerge'] = True
                if group['existingCount'] > 1 and merge_source_ids:
                    row['status'] = STATUS_MERGE
                    row['statusLabel'] = STATUS_LABELS[STATUS_MERGE]
                elif row.get('product', {}).get('sizeAction') == 'add':
                    row['status'] = STATUS_SIZE_ADD
                    row['statusLabel'] = STATUS_LABELS[STATUS_SIZE_ADD]
                else:
                    # 再分類: 取扱新規なら create/reuse
                    # 名差のまとめ・別名登録がある行は UNCHANGED にせずコミット対象へ
                    if row.get('handle', {}).get('action') == 'create':
                        row['status'] = STATUS_CREATE
                        row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                    elif (
                        row.get('handle', {}).get('action') == 'reuse'
                        and row.get('product', {}).get('sizeAction') == 'reuse'
                        and not row.get('product', {}).get('nameDiff')
                        and not row.get('product', {}).get('skuMerge')
                    ):
                        row['status'] = STATUS_UNCHANGED
                        row['statusLabel'] = STATUS_LABELS[STATUS_UNCHANGED]
                    else:
                        row['status'] = STATUS_REUSE
                        row['statusLabel'] = STATUS_LABELS[STATUS_REUSE]
                row['canCommit'] = row['status'] in (
                    STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD, STATUS_MERGE,
                )
            else:
                # 既存なし → 新規1商品（公式名で作成）
                row['product'] = dict(row.get('product') or {})
                row['product']['action'] = 'create'
                row['product']['id'] = None
                row['product']['officialName'] = official_name
                row['product']['officialColor'] = color
                row['product']['primarySupplierName'] = primary_supplier
                row['product']['skuMerge'] = True
                row['status'] = STATUS_CREATE
                row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                row['canCommit'] = True
            continue

        if decision == SKU_DECISION_SEPARATE:
            plans = raw.get('plans') if isinstance(raw.get('plans'), dict) else {}
            variant = _row_variant(row)
            plan = plans.get(variant['key']) or plans.get(row.get('csv', {}).get('productName')) or {}
            approach = stock.text(plan.get('approach') or raw.get('approach') or '')
            if approach in ('', 'defer'):
                row['status'] = STATUS_HOLD
                row['statusLabel'] = STATUS_LABELS[STATUS_HOLD]
                row['canCommit'] = False
                row['deferReason'] = stock.text(plan.get('reason') or '別物として未整理のため保留')
                hold_count += 1
            elif approach == 'change_code':
                new_code = stock.text(plan.get('newCode') or '')
                if not new_code or new_code == code:
                    row['status'] = STATUS_REVIEW
                    row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                    row['canCommit'] = False
                    row['reasons'] = list(row.get('reasons') or []) + [
                        f'品番「{code}」を別物にする場合は、修正後の品番を指定してください。'
                        f'同じ通常品番のまま新規登録はできません。'
                    ]
                    pending_decision += 1
                elif stock_product_merge.is_normal_product_code(new_code):
                    # 別品番として登録（既存があれば再利用判定はコミット時）
                    row['product'] = dict(row.get('product') or {})
                    row['product']['code'] = new_code
                    row['product']['codeOverride'] = new_code
                    row['product']['action'] = 'create'
                    row['product']['id'] = None
                    row['csv'] = dict(row.get('csv') or {})
                    row['csv']['code'] = new_code
                    row['csv']['codeDisplay'] = new_code
                    row['status'] = STATUS_CREATE
                    row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                    row['canCommit'] = True
                else:
                    row['product'] = dict(row.get('product') or {})
                    row['product']['code'] = new_code
                    row['product']['codeOverride'] = new_code
                    row['status'] = STATUS_CREATE
                    row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                    row['canCommit'] = True
            elif approach in ('as_parts', 'as_blank'):
                # 部品・資材 / 品番なしとして整理（通常品番の重複新規はしない）
                row['product'] = dict(row.get('product') or {})
                if approach == 'as_blank':
                    row['product']['code'] = ''
                    row['product']['codeOverride'] = ''
                    row['product']['blankSku'] = True
                row['product']['category'] = stock.CATEGORY_PARTS
                row['product']['action'] = 'create'
                row['product']['id'] = None
                row['status'] = STATUS_CREATE
                row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                row['canCommit'] = True
            else:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の整理方法（品番修正 / 部品・資材 / 品番なし / 保留）を選んでください。'
                    f'同じ通常品番のまま別商品として新規登録することはできません。'
                ]
                pending_decision += 1
            continue

        # 不明な decision
        row['status'] = STATUS_REVIEW
        row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
        row['canCommit'] = False
        pending_decision += 1

    pending_groups = 0
    for g in sku_groups:
        raw = decisions.get(g['code']) or decisions.get(str(g['code'])) or {}
        if isinstance(raw, dict) and stock.text(raw.get('decision')):
            g['decision'] = raw
            g['status'] = stock.text(raw.get('decision'))
        elif g['needsDecision']:
            g['status'] = 'needs_decision'
            pending_groups += 1

    return {
        'pendingDecisionGroups': pending_groups,
        'holdRows': hold_count,
    }


def _product_plan_key(row):
    """サイズ／取扱の重複集計用キー（通常品番は品番単位）。"""
    code = stock.text((row.get('csv') or {}).get('code'))
    prod = row.get('product') or {}
    if stock_product_merge.is_normal_product_code(code):
        return f'code:{code}'
    if prod.get('id'):
        return f'id:{prod["id"]}'
    return f'row:{row.get("rowNo")}'


def _planned_counts(classified, sku_groups):
    """CSV行数と新規商品数を混同しないための集計。

    サイズ追加・取扱新規は行数ではなく、商品×サイズ／顧客×商品×加工のユニーク数。
    """
    product_create_codes = set()
    product_reuse_ids = set()
    product_merge_codes = set()
    defer_rows = 0
    size_keys = set()
    handle_keys = set()
    for row in classified:
        st = row.get('status')
        if st == STATUS_HOLD:
            defer_rows += 1
            continue
        if st in (STATUS_EXCLUDED, STATUS_REVIEW):
            continue
        if row.get('skuDecisionNeeded') and st not in (
            STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD, STATUS_MERGE, STATUS_UNCHANGED,
        ):
            continue
        code = stock.text((row.get('csv') or {}).get('code'))
        prod = row.get('product') or {}
        csv = row.get('csv') or {}
        pkey = _product_plan_key(row)
        if prod.get('action') == 'create' or st == STATUS_CREATE:
            if stock_product_merge.is_normal_product_code(code):
                product_create_codes.add(code)
            else:
                product_create_codes.add(f'special:{row["rowNo"]}')
        if prod.get('id') and prod.get('action') == 'reuse':
            product_reuse_ids.add(prod['id'])
        if st == STATUS_MERGE or (prod.get('skuMerge') and prod.get('mergeSourceIds')):
            product_merge_codes.add(code)
        size_label = stock.text(csv.get('size') or prod.get('size') or '')
        if size_label and (prod.get('sizeAction') == 'add' or st == STATUS_SIZE_ADD):
            size_keys.add((pkey, size_label))
        if (row.get('handle') or {}).get('action') == 'create':
            handle_keys.add((
                identity_match_key(csv.get('customer') or ''),
                pkey,
                PENDING_EMBROIDERY,
            ))
    auto_groups = [g for g in sku_groups if g.get('plannedSingleProduct')]
    return {
        'csvRows': len(classified),
        'productsToCreate': len(product_create_codes),
        'productsToReuse': len(product_reuse_ids),
        'productsToMerge': len(product_merge_codes),
        'rowsDeferred': defer_rows,
        'sizesToAdd': len(size_keys),
        'handlesToCreate': len(handle_keys),
        'skuGroupsNeedingDecision': sum(
            1 for g in sku_groups if g.get('needsDecision') and not g.get('decision')
        ),
        'autoMergedSkuGroups': len(auto_groups),
        'autoMergedCsvRows': sum(g.get('rowCount') or 0 for g in auto_groups),
    }


def build_preview(c, body):
    ensure_schema(c)
    stock_product_merge.migrate_product_merge(c)
    uploaded = decode_upload(body)
    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    sku_decisions = body.get('skuDecisions') if isinstance(body.get('skuDecisions'), dict) else {}

    # 品番グループへの商品選択を行へ展開
    group_product = {}
    group_handle = {}
    group_force = {}
    group_category = {}
    for key, val in selections.items():
        if not isinstance(val, dict):
            continue
        gk = val.get('matchGroupKey')
        if not gk:
            continue
        if val.get('productId') is not None:
            group_product[gk] = val.get('productId')
        if val.get('handleId') is not None:
            group_handle[gk] = val.get('handleId')
        if val.get('forceCreate'):
            group_force[gk] = True
        if 'category' in val:
            group_category[gk] = val.get('category')

    classified = []
    for row_no, item in uploaded['rows']:
        sel = dict(selections.get(str(row_no)) or selections.get(row_no) or {})
        temp = classify_row(c, row_no, item)
        gk = temp['matchGroupKey']
        if gk in group_product and sel.get('productId') is None:
            sel['productId'] = group_product[gk]
        if gk in group_handle and sel.get('handleId') is None:
            sel['handleId'] = group_handle[gk]
        # 通常品番の forceCreate は抜け穴にしない（別物整理の change_code 等で対応）
        code_t = stock.text(item.get('商品コード'))
        force = bool(sel.get('forceCreate'))
        if force and stock_product_merge.is_normal_product_code(code_t):
            force = False
        if gk in group_force and not stock_product_merge.is_normal_product_code(code_t):
            force = True
        if gk in group_category and 'category' not in sel:
            sel['category'] = group_category[gk]
        # sku merge で keepProductId が付いている場合
        sku = sku_decisions.get(code_t) or sku_decisions.get(str(code_t)) or {}
        if isinstance(sku, dict) and stock.text(sku.get('decision')) == SKU_DECISION_MERGE:
            kid = sku.get('keepProductId') or sku.get('productId')
            if kid and sel.get('productId') is None:
                sel['productId'] = kid
        classified.append(classify_row(
            c, row_no, item,
            selected_product_id=sel.get('productId'),
            selected_handle_id=sel.get('handleId'),
            selected_category=sel.get('category') if 'category' in sel else None,
            force_create_product=force,
        ))

    # 照合グループ件数
    group_counts = defaultdict(int)
    for row in classified:
        group_counts[row['matchGroupKey']] += 1
    for row in classified:
        row['matchGroupCount'] = group_counts[row['matchGroupKey']]

    sku_groups = build_sku_groups(c, classified)
    decision_meta = _apply_sku_decisions(c, classified, sku_groups, sku_decisions)
    _attach_product_plans(classified, sku_groups)
    _apply_auto_merge_official_names(classified, sku_groups)

    counts = defaultdict(int)
    for row in classified:
        counts[row['status']] += 1

    review_count = counts.get(STATUS_REVIEW, 0)
    sku_pending_rows = sum(1 for r in classified if r.get('skuDecisionNeeded'))
    planned = _planned_counts(classified, sku_groups)
    auto_plans = [
        g.get('productPlan') for g in sku_groups
        if g.get('plannedSingleProduct') and g.get('productPlan')
    ]
    fp = db_fingerprint(c)
    snap = stock_snapshot(c)
    return {
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'fileSize': uploaded['fileSize'],
        'encoding': uploaded['encoding'],
        'fields': uploaded['fields'],
        'rowCount': len(classified),
        'rows': classified,
        'skuGroups': sku_groups,
        'skuGroupCount': len(sku_groups),
        'skuDecisionPending': decision_meta['pendingDecisionGroups'],
        'skuDecisionPendingRows': sku_pending_rows,
        'autoMergePlans': auto_plans,
        'counts': dict(STATUS_LABELS),
        'countsRaw': dict(counts),
        'plannedCounts': planned,
        'reviewCount': review_count,
        'previewFingerprint': fp,
        'previewAt': stock.stamp(),
        'stockSnapshot': snap,
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
        'categoryOptions': ['', stock.CATEGORY_UNIFORM, stock.CATEGORY_PARTS],
        'note': (
            '在庫数量・仕入単価・初期在庫・入出庫履歴は登録しません。'
            '通常品番は1品番=1商品です。サイズ・顧客・先頭「＊/*」中古標記だけの差は'
            '自動で1商品にまとめ、要確認へは流しません。公式名は標記なしを優先し、'
            'CSV原文（標記付き含む）は照合用別名に残します。括弧内表記や色・加工は'
            '商品名から自動分解しません。加工列が無くてもエラーにはしません。'
        ),
    }


def _ensure_person(c, table, save_fn, name, *, create):
    return legacy._ensure_person(c, table, save_fn, name, create=create)


def _save_product_sizes_only(c, pid, size_label):
    current = stock.product_row(c, pid)
    labels = list(current['sizes'])
    if not size_label or size_label in labels:
        return False
    labels.append(size_label)
    stock.save_product(c, {
        'id': pid,
        'code': current['code'],
        'name': current['name'],
        'color': current['color'],
        'category': current.get('category') or '',
        'supplierId': current['supplierId'],
        'subSupplierIds': current.get('subSupplierIds') or [],
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
    return True


def _ensure_sub_supplier(c, product_id, supplier_id):
    if not product_id or not supplier_id:
        return False
    if stock.product_has_supplier(c, product_id, supplier_id):
        return False
    # 通常仕入先は変更しない。サブとして追加。
    max_sort = c.execute(
        'SELECT COALESCE(MAX(sort_order), -1) AS m FROM product_suppliers WHERE product_id=?',
        (product_id,),
    ).fetchone()['m']
    c.execute(
        'INSERT INTO product_suppliers(product_id,supplier_id,sort_order,created_at) VALUES(?,?,?,?)',
        (product_id, supplier_id, int(max_sort) + 1, stock.stamp()),
    )
    return True


def _apply_official_name(c, product_id, official_name, color=None, csv_name=None):
    """公式名を確定し、CSV名・旧名を別名に残す。"""
    if not product_id or not official_name:
        return False
    current = stock.product_row(c, product_id)
    if stock.text(current['name']) != stock.text(official_name):
        stock_product_merge.ensure_name_alias(c, product_id, current['name'])
    if csv_name and stock.text(csv_name) != stock.text(official_name):
        stock_product_merge.ensure_name_alias(c, product_id, csv_name)
    new_color = current['color'] if color is None else stock.text(color)
    if (
        stock.text(current['name']) == stock.text(official_name)
        and stock.text(current['color']) == new_color
    ):
        return False
    stock.save_product(c, {
        'id': product_id,
        'code': current['code'],
        'name': official_name,
        'color': new_color,
        'category': current.get('category') or '',
        'supplierId': current['supplierId'],
        'subSupplierIds': current.get('subSupplierIds') or [],
        'sizes': current['sizes'],
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
    return True


def _ensure_product(c, product_info, supplier_id, csv_name, *, customer_id=None, force_create=False):
    if product_info.get('codeOverride') is not None:
        product_info = dict(product_info)
        product_info['code'] = product_info.get('codeOverride')

    official_name = stock.text(product_info.get('officialName') or '') or stock.text(csv_name)
    official_color = product_info.get('officialColor')
    if official_color is None:
        official_color = ''

    if product_info.get('id') and not force_create:
        pid = product_info['id']
        if (product_info.get('skuMerge') or product_info.get('applyOfficialName')) and official_name:
            _apply_official_name(
                c, pid, official_name,
                color=official_color if 'officialColor' in product_info else None,
                csv_name=csv_name,
            )
        elif stock.text(csv_name) and stock_product_merge.is_normal_product_code(product_info.get('code')):
            # 再利用時も CSV 名を別名に残す（公式名は変えない）
            prod = _load_product(c, pid)
            if prod and stock.text(prod['name']) != stock.text(csv_name):
                stock_product_merge.ensure_name_alias(c, pid, csv_name)
        size_added = False
        if product_info.get('sizeAction') == 'add':
            size_added = _save_product_sizes_only(c, pid, product_info.get('size'))
        sub_added = False
        if product_info.get('subSupplierAdd') and product_info.get('subSupplierId'):
            sub_added = _ensure_sub_supplier(c, pid, product_info['subSupplierId'])
        elif product_info.get('subSupplierAdd') and supplier_id:
            sub_added = _ensure_sub_supplier(c, pid, supplier_id)
        # 通常仕入先名が指定されていれば、未設定のときだけ通常へ。既存通常は維持しサブ追加。
        primary_name = stock.text(product_info.get('primarySupplierName') or '')
        if primary_name and supplier_id:
            prod = _load_product(c, pid)
            if prod and prod['supplier_id'] in (None, ''):
                cur = stock.product_row(c, pid)
                stock.save_product(c, {
                    'id': pid, 'code': cur['code'], 'name': cur['name'], 'color': cur['color'],
                    'category': cur.get('category') or '', 'supplierId': supplier_id,
                    'subSupplierIds': cur.get('subSupplierIds') or [],
                    'sizes': cur['sizes'], 'active': cur['active'],
                    'links': [
                        {
                            'customerId': l['customerId'], 'embroidery': l['embroidery'],
                            'isStandard': l['isStandard'], 'active': l['active'],
                        }
                        for l in cur['links']
                    ],
                })
            elif prod and prod['supplier_id'] not in (None, supplier_id):
                sub_added = _ensure_sub_supplier(c, pid, supplier_id) or sub_added
        if csv_name and primary_name:
            stock_product_merge.ensure_supplier_alias(c, pid, primary_name)
        return pid, False, size_added, sub_added

    code = stock.text(product_info.get('code'))
    name = official_name or stock.text(csv_name)

    if not force_create:
        if legacy.is_custom_order_code(code):
            matches = legacy.find_custom_order_products_for_customer(
                c, name, customer_id=customer_id,
            )
            if len(matches) == 1:
                size_added = _save_product_sizes_only(c, matches[0]['id'], product_info.get('size'))
                return matches[0]['id'], False, size_added, False
        elif stock_product_merge.is_normal_product_code(code):
            matches = stock_product_merge.find_live_products_by_code(c, code)
            if not matches:
                matches = stock_product_merge.find_products_by_name_alias(c, code, csv_name or name)
            if len(matches) > 1:
                raise ValueError(
                    f'品番「{code}」の商品が複数あります。先に統合してから登録してください。'
                )
            if len(matches) == 1:
                pid = matches[0]['id']
                if stock.text(matches[0]['name']) != stock.text(csv_name):
                    stock_product_merge.ensure_name_alias(c, pid, csv_name)
                if (product_info.get('skuMerge') or product_info.get('applyOfficialName')) and official_name:
                    _apply_official_name(
                        c, pid, official_name,
                        color=official_color if 'officialColor' in product_info else None,
                        csv_name=csv_name,
                    )
                size_added = _save_product_sizes_only(c, pid, product_info.get('size'))
                sub_added = False
                if supplier_id and matches[0]['supplier_id'] not in (None, supplier_id):
                    sub_added = _ensure_sub_supplier(c, pid, supplier_id)
                return pid, False, size_added, sub_added
        elif code:
            # 不明など特殊で空でない
            matches = legacy.find_products_by_code(c, code)
            by_name = [m for m in matches if stock.text(m['name']) == name]
            narrowed = by_name
            if supplier_id is not None and len(by_name) > 1:
                by_sup = [m for m in by_name if m['supplier_id'] == supplier_id]
                if by_sup:
                    narrowed = by_sup
            if len(narrowed) == 1:
                size_added = _save_product_sizes_only(c, narrowed[0]['id'], product_info.get('size'))
                sub_added = False
                if supplier_id and narrowed[0]['supplier_id'] not in (None, supplier_id):
                    sub_added = _ensure_sub_supplier(c, narrowed[0]['id'], supplier_id)
                return narrowed[0]['id'], False, size_added, sub_added
        else:
            matches = legacy.find_blank_sku_products(c, name, supplier_id)
            if len(matches) == 1:
                size_added = _save_product_sizes_only(c, matches[0]['id'], product_info.get('size'))
                return matches[0]['id'], False, size_added, False

    category = stock.normalize_category(
        product_info.get('category') or (
            stock.CATEGORY_UNIFORM if legacy.is_custom_order_code(code) else ''
        )
    )
    saved = stock.save_product(c, {
        'code': code,
        'name': name,  # 公式名（未指定時はCSV原文）。色・加工の自動分解なし
        'color': stock.text(official_color) if official_color is not None else '',
        'category': category,
        'supplierId': supplier_id,
        'sizes': [product_info['size']],
        'links': [],
        'active': True,
    })
    if csv_name and stock.text(csv_name) != stock.text(name):
        stock_product_merge.ensure_name_alias(c, saved['id'], csv_name)
    return saved['id'], True, False, False


def _ensure_handle(c, customer_id, product_id, handle_info):
    return legacy._ensure_handle(c, customer_id, product_id, handle_info)


def _run_confirmed_product_merges(c, classified, sku_decisions, *, actor_name, token_prefix):
    """ユーザー確認済みの既存重複統合だけ実行（自動統合はしない）。"""
    merged = []
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    seen_keep = set()
    for row in classified:
        prod = row.get('product') or {}
        if not prod.get('skuMerge'):
            continue
        keep_id = prod.get('id')
        sources = [int(x) for x in (prod.get('mergeSourceIds') or []) if x]
        if not keep_id or not sources:
            continue
        key = (int(keep_id), tuple(sorted(sources)))
        if key in seen_keep:
            continue
        seen_keep.add(key)
        product_ids = sorted({int(keep_id), *[int(s) for s in sources]})
        if len(product_ids) < 2:
            continue
        merge_body = {
            'productIds': product_ids,
            'keepProductId': int(keep_id),
            'name': prod.get('officialName') or row.get('csv', {}).get('productName'),
            'color': prod.get('officialColor') if prod.get('officialColor') is not None else '',
            'token': f'{token_prefix}-merge-{keep_id}',
            '_canWrite': True,
            '_actor': {'name': actor_name},
        }
        preview = stock_product_merge.preview_merge(c, merge_body)
        merge_body['fingerprint'] = preview['fingerprint']
        merge_body['name'] = merge_body['name'] or preview['name']
        if 'officialColor' in prod:
            merge_body['color'] = stock.text(prod.get('officialColor') or '')
        else:
            merge_body['color'] = preview['color']
        merge_body['category'] = preview['category']
        merge_body['supplierId'] = preview['supplierId']
        merge_body['subSupplierIds'] = preview['subSupplierIds']
        merge_body['sizes'] = preview['sizes']
        # 取扱差異は「残す商品」側を優先（CSV確認で keep を選んだ前提）
        if preview.get('handleConflicts'):
            merge_body['conflictResolutions'] = {
                str(m['sourceHandleId']): 'keep' for m in preview['handleConflicts']
            }
        # バックアップは台帳取込側で取得済みのため db_path=None
        result = stock_product_merge.execute_merge(c, merge_body, db_path=None)
        merged.append(result)
        # 統合後、行の product.id を統合先へ
        for r in classified:
            p = r.get('product') or {}
            if p.get('id') in sources:
                p['id'] = int(keep_id)
    return merged


def commit_preview(c, body, *, db_path=None):
    stock._require_write(body)
    ensure_schema(c)
    stock_product_merge.migrate_product_merge(c)
    # プレビューと同じ経路で再分類（確定直前の再チェック）
    preview = build_preview(c, body)
    classified = preview['rows']
    sku_groups = preview.get('skuGroups') or []
    planned = preview.get('plannedCounts') or {}

    token = stock._token(body.get('token') or body.get('commitToken'))
    existing_token = c.execute(
        'SELECT id,result_json FROM product_ledger_import_runs WHERE commit_token=?', (token,)
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
        raise ValueError('プレビュー結果を指定してください。もう一度読み込み直してください。')
    fp_now = db_fingerprint(c)
    if preview_fp != fp_now:
        raise stock.StockConflict({
            'error': 'プレビュー後にデータが変わったため、確認し直してください。',
            'code': 'stale_preview',
            'previewFingerprint': preview_fp,
            'currentFingerprint': fp_now,
        })

    # プレビュー応答の fingerprint と一致させるため、build_preview 結果を使う
    if preview['previewFingerprint'] != fp_now:
        # build_preview は DB を変えないので通常は一致。不一致なら再読込を促す。
        raise stock.StockConflict({
            'error': 'プレビュー後にデータが変わったため、確認し直してください。',
            'code': 'stale_preview',
            'previewFingerprint': preview_fp,
            'currentFingerprint': fp_now,
        })

    uploaded = decode_upload(body)
    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    sku_decisions = body.get('skuDecisions') if isinstance(body.get('skuDecisions'), dict) else {}

    review_rows = [r for r in classified if r['status'] == STATUS_REVIEW]
    excluded_rows = [r for r in classified if r['status'] == STATUS_EXCLUDED]
    hold_rows = [r for r in classified if r['status'] == STATUS_HOLD]
    commit_rows = [
        r for r in classified
        if r['status'] in (STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD, STATUS_MERGE)
        and not r.get('skuDecisionNeeded')
    ]

    pending_sku = sum(1 for g in sku_groups if g.get('needsDecision') and not g.get('decision'))
    if pending_sku:
        raise ValueError(
            f'品番比較で未決定が {pending_sku} 件あります。'
            f'【同じ商品としてまとめる】【別物のため個別に整理する】【今回は保留する】を選んでください。'
        )
    if any(r.get('skuDecisionNeeded') for r in classified):
        raise ValueError(
            '品番比較の未決定行が残っています。'
            'サイズ差だけの行は要確認ではありません。品番ごとの比較で決定してください。'
        )
    if review_rows and not body.get('allowCommitWithReview'):
        raise ValueError(
            f'要確認が {len(review_rows)} 件残っています。'
            f'未解決の行を黙って除外せず、候補を選ぶか解決してから確定してください。'
        )
    if not commit_rows and not body.get('allowEmptyCommit'):
        raise ValueError(
            '登録する行がありません。'
            f'要確認 {len(review_rows)} 件、保留 {len(hold_rows)} 件、対象外 {len(excluded_rows)} 件、'
            f'変更なし {sum(1 for r in classified if r["status"] == STATUS_UNCHANGED)} 件です。'
        )

    # 確定前サマリ（行数≠新規商品数）
    confirm_summary = {
        'csvRows': planned.get('csvRows', len(classified)),
        'productsToCreate': planned.get('productsToCreate', 0),
        'productsToReuse': planned.get('productsToReuse', 0),
        'productsToMerge': planned.get('productsToMerge', 0),
        'rowsDeferred': planned.get('rowsDeferred', len(hold_rows)),
        'sizesToAdd': planned.get('sizesToAdd', 0),
        'handlesToCreate': planned.get('handlesToCreate', 0),
        'commitRows': len(commit_rows),
    }

    before_stock = stock_snapshot(c)
    backup_path = ''
    if db_path:
        # Must succeed before any registration writes. Do not skip on failure.
        try:
            backup_path = str(stock.backup_sqlite(db_path, tag='pre-product-ledger') or '')
        except stock.BackupError:
            raise
        except Exception as e:
            # Unexpected errors still abort with a clear pre-registration message.
            print(
                f'product_ledger backup failed before register: {type(e).__name__}: {e}',
                flush=True,
            )
            raise stock.BackupError(stock.BACKUP_FAILED_MESSAGE, cause=e) from e

    actor = stock._actor_name(body)
    t0 = stock.stamp()
    c.execute(
        '''INSERT INTO product_ledger_import_runs(
             file_sha256,file_name,file_size,row_count,actor_name,
             preview_at,preview_fingerprint,commit_token,created_at,backup_path)
           VALUES(?,?,?,?,?,?,?,?,?,?)''',
        (
            uploaded['fileSha256'], uploaded['fileName'], uploaded['fileSize'], len(classified),
            actor, stock.text(body.get('previewAt')) or t0, preview_fp, token, t0, backup_path,
        ),
    )
    run_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]

    result_counts = {
        'customersCreated': 0, 'suppliersCreated': 0, 'productsCreated': 0,
        'sizesAdded': 0, 'handlesCreated': 0, 'subSuppliersAdded': 0,
        'productsMerged': 0,
        'reused': 0, 'unchanged': sum(1 for r in classified if r['status'] == STATUS_UNCHANGED),
        'reviewed': len(review_rows), 'excluded': len(excluded_rows),
        'deferred': len(hold_rows),
        'committedRows': 0, 'failed': 0,
    }
    failures = []
    defer_records = [
        {
            'rowNo': r['rowNo'],
            'code': (r.get('csv') or {}).get('code'),
            'productName': (r.get('csv') or {}).get('productName'),
            'reason': r.get('deferReason') or '',
            'csv': r.get('csv'),
        }
        for r in hold_rows
    ]

    try:
        merge_results = _run_confirmed_product_merges(
            c, classified, sku_decisions, actor_name=actor, token_prefix=token,
        )
        result_counts['productsMerged'] = sum(
            len(m.get('mergedProductIds') or []) for m in merge_results
        )

        # 同一通常品番の新規はコミット内で1商品にまとめる
        created_by_code = {}

        for row in commit_rows:
            row_no = row['rowNo']
            try:
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

                sel = selections.get(str(row_no)) or selections.get(row_no) or {}
                prod_info = dict(row['product'] or {})
                code_t = stock.text(prod_info.get('codeOverride') if prod_info.get('codeOverride') is not None else row['csv']['code'])
                force = bool(sel.get('forceCreate')) and not stock_product_merge.is_normal_product_code(code_t)

                # 同一品番で直前に作った商品へ再利用
                if (
                    stock_product_merge.is_normal_product_code(code_t)
                    and not prod_info.get('id')
                    and code_t in created_by_code
                ):
                    prod_info['id'] = created_by_code[code_t]
                    prod_info['action'] = 'reuse'
                    if prod_info.get('size') and prod_info.get('sizeAction') != 'reuse':
                        prod_info['sizeAction'] = 'add'

                pid, created, size_added, sub_added = _ensure_product(
                    c, prod_info, sup_id, row['csv']['productName'],
                    customer_id=cust_id,
                    force_create=force,
                )
                if created and stock_product_merge.is_normal_product_code(code_t):
                    created_by_code[code_t] = pid
                if created:
                    result_counts['productsCreated'] += 1
                if size_added:
                    result_counts['sizesAdded'] += 1
                if sub_added:
                    result_counts['subSuppliersAdded'] += 1
                if not created and not size_added and not sub_added:
                    result_counts['reused'] += 1

                handle_info = dict(row.get('handle') or {})
                if sel.get('handleId'):
                    handle_info['id'] = sel['handleId']
                    handle_info['action'] = 'reuse'
                elif row.get('handle', {}).get('id'):
                    handle_info['id'] = row['handle']['id']
                    handle_info['action'] = 'reuse'
                cp_id, created_h = _ensure_handle(c, cust_id, pid, handle_info)
                if created_h:
                    result_counts['handlesCreated'] += 1

                upsert_row_map(
                    c, run_id=run_id, file_sha256=uploaded['fileSha256'],
                    file_name=uploaded['fileName'], row=row,
                    customer_id=cust_id, product_id=pid, customer_product_id=cp_id,
                )
                result_counts['committedRows'] += 1
            except Exception as e:
                result_counts['failed'] += 1
                failures.append({'rowNo': row_no, 'error': str(e) or '登録に失敗しました。'})
                raise

        after_stock = stock_snapshot(c)
        if after_stock != before_stock:
            raise RuntimeError(
                '在庫数量・金額・履歴が変化したため取込を中止しました。'
                f' before={before_stock} after={after_stock}'
            )

        result = {
            'runId': run_id,
            'already': False,
            'counts': result_counts,
            'confirmSummary': confirm_summary,
            'deferredRows': defer_records,
            'failures': failures,
            'backupPath': backup_path,
            'stockSnapshotBefore': before_stock,
            'stockSnapshotAfter': after_stock,
            'stockUnchanged': True,
            'reviewRemaining': len(review_rows),
            'excludedCount': len(excluded_rows),
            'deferredCount': len(hold_rows),
            'fileName': uploaded['fileName'],
            'fileSha256': uploaded['fileSha256'],
            'actorName': actor,
            'committedAt': stock.stamp(),
            'note': IMPORT_NOTE,
        }
        c.execute(
            'UPDATE product_ledger_import_runs SET committed_at=?, result_json=? WHERE id=?',
            (result['committedAt'], json.dumps(result, ensure_ascii=False), run_id),
        )
        return result
    except Exception:
        # BEGIN IMMEDIATE の呼び出し側でロールバックされる想定。部分登録を残さない。
        raise


def file_history(c, body):
    ensure_schema(c)
    uploaded = decode_upload(body)
    return {
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
    }
