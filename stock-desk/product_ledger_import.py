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
STATUS_EXCLUDED = 'excluded'

STATUS_LABELS = {
    STATUS_CREATE: '新規',
    STATUS_REUSE: '既存使用',
    STATUS_SIZE_ADD: 'サイズ追加',
    STATUS_UNCHANGED: '変更なし',
    STATUS_REVIEW: '要確認',
    STATUS_EXCLUDED: '対象外',
}


def _collapse_space(value):
    return re.sub(r'[\s\u3000]+', '', stock.text(value))


def identity_match_key(value):
    return legacy.identity_match_key(value)


def match_group_key(*, customer, code, product_name, supplier):
    """同じ照合条件の行に選択結果をまとめて適用するためのキー。"""
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
    else:
        raw = '|'.join([
            'code',
            identity_match_key(customer),
            code_t,
            identity_match_key(product_name),
            identity_match_key(supplier),
        ])
    return hashlib.sha256(raw.encode()).hexdigest()


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
    t = stock.text(product_name)
    return t.startswith('＊') or t.startswith('*')


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
        # 括弧付き名・加工表記差は自動同一化しない
        info['action'] = 'review'
        info['id'] = prod['id']
        info['candidates'] = [_product_candidate(prod, c)]
        info['nameDiff'] = True
        info['reasons'].append(
            f'{row_no}行目：品番は一致しますが商品名が異なります'
            f'（既存: {prod["name"]} / CSV: {name}）。候補から選ぶか新規にしてください。'
        )
        return info
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
            f'{row_no}行目：商品名先頭の「＊/*」は中古の可能性があります。'
            f'記号を削除して新品へ統合しません（今回は中古在庫は作りません）。'
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

    matches = legacy.find_products_by_code(c, code)
    if not matches:
        info['action'] = 'create'
        info['sizeAction'] = 'add'
        return info

    by_name = [m for m in matches if stock.text(m['name']) == name]
    if not by_name:
        info['action'] = 'review'
        info['candidates'] = [_product_candidate(m, c) for m in matches]
        info['reasons'].append(
            f'{row_no}行目：同じ品番の商品はありますが商品名が一致しません。'
            f'同品番・別仕入先の既存商品へ紐づける場合は候補から選んでください。'
        )
        return info

    narrowed = by_name
    if supplier_id and len(by_name) > 1:
        by_sup = [
            m for m in by_name
            if m['supplier_id'] == supplier_id or stock.product_has_supplier(c, m['id'], supplier_id)
        ]
        if len(by_sup) == 1:
            narrowed = by_sup
        elif by_sup:
            narrowed = by_sup

    if len(narrowed) > 1:
        info['action'] = 'review'
        info['candidates'] = [_product_candidate(m, c) for m in narrowed]
        info['reasons'].append(f'{row_no}行目：同じ品番・商品名の商品が複数あります。候補から選んでください。')
        return info

    return _finalize_matched_product(
        c, info, narrowed[0], name, size_label, supplier_id,
        proposed_category='', selected_category=None, apply_category=False,
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


def build_preview(c, body):
    ensure_schema(c)
    uploaded = decode_upload(body)
    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    classified = []
    for row_no, item in uploaded['rows']:
        sel = selections.get(str(row_no)) or selections.get(row_no) or {}
        classified.append(classify_row(
            c, row_no, item,
            selected_product_id=sel.get('productId'),
            selected_handle_id=sel.get('handleId'),
            selected_category=sel.get('category') if 'category' in sel else None,
            force_create_product=bool(sel.get('forceCreate')),
        ))

    # 照合グループ件数
    group_counts = defaultdict(int)
    for row in classified:
        group_counts[row['matchGroupKey']] += 1
    for row in classified:
        row['matchGroupCount'] = group_counts[row['matchGroupKey']]

    counts = defaultdict(int)
    for row in classified:
        counts[row['status']] += 1

    review_count = counts.get(STATUS_REVIEW, 0)
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
        'counts': dict(STATUS_LABELS),
        'countsRaw': dict(counts),
        'reviewCount': review_count,
        'previewFingerprint': fp,
        'previewAt': stock.stamp(),
        'stockSnapshot': snap,
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
        'categoryOptions': ['', stock.CATEGORY_UNIFORM, stock.CATEGORY_PARTS],
        'note': (
            '在庫数量・仕入単価・初期在庫・入出庫履歴は登録しません。'
            '商品名・括弧内表記・「＊」は原文のまま扱い、自動では分解・削除しません。'
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


def _ensure_product(c, product_info, supplier_id, csv_name, *, customer_id=None, force_create=False):
    if product_info.get('id') and not force_create:
        size_added = False
        if product_info.get('sizeAction') == 'add':
            size_added = _save_product_sizes_only(c, product_info['id'], product_info.get('size'))
        sub_added = False
        if product_info.get('subSupplierAdd') and product_info.get('subSupplierId'):
            sub_added = _ensure_sub_supplier(c, product_info['id'], product_info['subSupplierId'])
        elif product_info.get('subSupplierAdd') and supplier_id:
            sub_added = _ensure_sub_supplier(c, product_info['id'], supplier_id)
        return product_info['id'], False, size_added, sub_added

    code = stock.text(product_info.get('code'))
    name = stock.text(csv_name)

    if not force_create:
        if legacy.is_custom_order_code(code):
            matches = legacy.find_custom_order_products_for_customer(
                c, name, customer_id=customer_id,
            )
            if len(matches) == 1:
                size_added = _save_product_sizes_only(c, matches[0]['id'], product_info.get('size'))
                return matches[0]['id'], False, size_added, False
        elif code:
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
        'name': csv_name,  # 括弧・＊含む原文。色・加工の自動分解なし
        'color': '',
        'category': category,
        'supplierId': supplier_id,
        'sizes': [product_info['size']],
        'links': [],
        'active': True,
    })
    return saved['id'], True, False, False


def _ensure_handle(c, customer_id, product_id, handle_info):
    return legacy._ensure_handle(c, customer_id, product_id, handle_info)


def commit_preview(c, body, *, db_path=None):
    stock._require_write(body)
    ensure_schema(c)
    uploaded = decode_upload(body)
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

    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    # 照合グループへの選択一括適用
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
        # 仮分類でグループキー取得
        temp = classify_row(c, row_no, item)
        gk = temp['matchGroupKey']
        if gk in group_product and sel.get('productId') is None:
            sel['productId'] = group_product[gk]
        if gk in group_handle and sel.get('handleId') is None:
            sel['handleId'] = group_handle[gk]
        if gk in group_force:
            sel['forceCreate'] = True
        if gk in group_category and 'category' not in sel:
            sel['category'] = group_category[gk]
        classified.append(classify_row(
            c, row_no, item,
            selected_product_id=sel.get('productId'),
            selected_handle_id=sel.get('handleId'),
            selected_category=sel.get('category') if 'category' in sel else None,
            force_create_product=bool(sel.get('forceCreate')),
        ))

    review_rows = [r for r in classified if r['status'] == STATUS_REVIEW]
    excluded_rows = [r for r in classified if r['status'] == STATUS_EXCLUDED]
    commit_rows = [
        r for r in classified
        if r['status'] in (STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD)
    ]

    if review_rows and not body.get('allowCommitWithReview'):
        raise ValueError(
            f'要確認が {len(review_rows)} 件残っています。'
            f'未解決の行を黙って除外せず、候補を選ぶか解決してから確定してください。'
        )
    if not commit_rows and not body.get('allowEmptyCommit'):
        raise ValueError(
            '登録する行がありません。'
            f'要確認 {len(review_rows)} 件、対象外 {len(excluded_rows)} 件、'
            f'変更なし {sum(1 for r in classified if r["status"] == STATUS_UNCHANGED)} 件です。'
        )

    before_stock = stock_snapshot(c)
    backup_path = ''
    if db_path:
        backup_path = str(stock.backup_sqlite(db_path, tag='pre-product-ledger') or '')

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
        'reused': 0, 'unchanged': sum(1 for r in classified if r['status'] == STATUS_UNCHANGED),
        'reviewed': len(review_rows), 'excluded': len(excluded_rows),
        'committedRows': 0, 'failed': 0,
    }
    failures = []

    try:
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
                pid, created, size_added, sub_added = _ensure_product(
                    c, row['product'], sup_id, row['csv']['productName'],
                    customer_id=cust_id,
                    force_create=bool(sel.get('forceCreate')),
                )
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
            'failures': failures,
            'backupPath': backup_path,
            'stockSnapshotBefore': before_stock,
            'stockSnapshotAfter': after_stock,
            'stockUnchanged': True,
            'reviewRemaining': len(review_rows),
            'excludedCount': len(excluded_rows),
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
