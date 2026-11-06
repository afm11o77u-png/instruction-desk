"""旧QR（在庫行ID）と現在の商品・顧客・サイズ・新品中古・場所の対応表。

数量・単価・金額・入出庫履歴は更新しない。未一致から商品・顧客・場所は作らない。
照合単位は旧在庫行ID（QRの id）。ロットが統合されていても、対応先はロット1件に決めない。
品番の完全一致と、ハイフン等を畳んだ正規化一致は別ステータスにする。
顧客・品番・サイズが一致して候補が1件なら、品名が違っても完全一致にする。
完全一致でも正規化一致でも無い品番は、一方が他方に含まれるときだけ部分一致にする。
そのときは商品名も一致し、候補が1件の場合だけ完全一致にする。
今の商品名の文字列全体が旧品名に含まれるときも、途中の空白は消さずに見て、1件なら完全一致にする。
旧品名の末尾の（加工名）で取扱の加工仕様を選ぶ。無ければ加工なし。候補が1件なら完全一致。
括弧全体が一致しなくても、括弧の中に取扱の加工名が含まれていればその取扱を選ぶ。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import excel_book
import stock

SHEET_NAME = '対応'
NOTE_SHEET = '説明'

STATUS_EXACT = 'exact'
STATUS_REVIEW = 'review'
STATUS_AMBIGUOUS = 'ambiguous'
STATUS_UNMATCHED = 'unmatched'
STATUS_INVALID = 'invalid'
STATUS_DUPLICATE = 'duplicate'
STATUS_UNCHANGED = 'unchanged'
STATUS_CONFLICT = 'conflict'

STATUS_LABELS = {
    STATUS_EXACT: '完全一致',
    STATUS_REVIEW: '要確認',
    STATUS_AMBIGUOUS: '候補が複数',
    STATUS_UNMATCHED: '未一致',
    STATUS_INVALID: '不正',
    STATUS_DUPLICATE: '同じ旧ID',
    STATUS_UNCHANGED: '登録済み',
    STATUS_CONFLICT: '別対応が登録済み',
}

# テンプレートの列。IDは文字。数量・単価は照合の参考だけで在庫へ書かない。
COLUMNS = (
    ('旧在庫行ID', 'legacyStockId', True),
    ('旧顧客ID', 'legacyCustomerId', True),
    ('旧メーカーID', 'legacyMakerId', True),
    ('旧商品ID', 'legacyItemId', False),
    ('顧客名', 'customerName', False),
    ('メーカー名', 'makerName', False),
    ('品番', 'itemNum', False),
    ('品名', 'itemName', False),
    ('サイズ', 'itemSize', False),
    ('新品中古', 'isUsed', False),
    ('参考数量', 'qty', False),
    ('参考単価', 'price', False),
    ('拠点名', 'factoryName', False),
    ('旧拠点ID', 'factoryId', False),
    ('棚名', 'shelfName', False),
    ('旧棚ID', 'shelfId', False),
    ('入荷日', 'arrivalDate', False),
    ('QR文字列', 'qr', False),
)

HEADER_TO_KEY = {label: key for label, key, _req in COLUMNS}
HEADER_TO_KEY.update({
    'id': 'legacyStockId',
    'customer_id': 'legacyCustomerId',
    'maker_id': 'legacyMakerId',
    'item_id': 'legacyItemId',
    'maker_name': 'makerName',
    'item_num': 'itemNum',
    '商品コード': 'itemNum',
    '商品名': 'itemName',
    'name': 'itemName',
    'item_size': 'itemSize',
    'サイズ': 'itemSize',
    'is_used': 'isUsed',
    '新古': 'isUsed',
    '数量': 'qty',
    '在庫': 'qty',
    '単価': 'price',
    'price': 'price',
    'factory_name': 'factoryName',
    'factory_id': 'factoryId',
    'shelf_name': 'shelfName',
    'shelf_id': 'shelfId',
    'arrival_date': 'arrivalDate',
    'QR': 'qr',
})
REQUIRED_KEYS = ('legacyStockId', 'legacyCustomerId', 'legacyMakerId')
ID_KEYS = ('legacyStockId', 'legacyCustomerId', 'legacyMakerId', 'legacyItemId', 'factoryId', 'shelfId')
REF_KEYS = ('qty', 'price')

NOTES = (
    '旧在庫行ID・旧顧客ID・旧メーカーIDは、QRの文字列をそのまま文字として入れてください。数値セルにすると桁が欠けることがあります。',
    'QR文字列には {"id","customer_id","maker_id"} のJSONを入れられます。空のID列を補います。列の値とJSONが違う行は登録しません。',
    '新品中古が空欄または null のときは、新品にしません。状態は未指定のまま対応づけます。',
    '品番の完全一致と、ハイフン・空白・全角半角を畳んだ一致は別です。畳んだだけの候補は選ぶまで登録しません。',
    '顧客・品番・サイズが一致し、候補が1件のときは、品名が違っても完全一致にします。候補が複数のときは選ぶまで登録しません。',
    '品番が完全一致でも正規化一致でも無いときは、一方が他方に含まれる部分一致も見ます（例: NPC75082 と NPC75082下、別注 と 別注(顧客名)）。顧客・サイズ・商品名も一致して候補が1件のときだけ完全一致にします。先頭の＊や空白だけの品名の差は同じ名前です。品名の完全一致が無いときは、今の商品名の文字列全体が旧品名に含まれるものだけに絞ります（途中の空白は消しません。NFKCと先頭の＊だけ揃えます）。それが1件なら完全一致、複数なら候補が複数、一つも含まれなければ自動では確定しません。枝番の数字だけ長い品番（AZ-8618 と AZ-8618-003）は部分一致にしません。',
    '旧品名の末尾の（加工名）で取扱を選びます。例: ＊白衣(スナッパー加工) は「スナッパー加工」の取扱です。括弧全体が一致しなくても、括弧の中に取扱の加工名が含まれていればその取扱です（白・刺繍入り は 刺繍入り）。完全一致を優先します。短い加工名の含有では選びません。その取扱が無いとき、または括弧が無いときは「加工なし」です。先頭の＊や空白の差は同じ名前です。このあと候補が1件なら完全一致にします。候補が複数のときは選ぶまで登録しません。',
    '顧客名の㈱と株式会社、空白だけの違いで複数になるときは、選ぶまで登録しません。',
    '参考数量・参考単価・入荷日はメモです。在庫数量・金額・入出庫履歴は変えません。',
    '未一致の行から商品・顧客・取扱・場所は作りません。',
    '同じ旧在庫行IDが別の対応で登録済みのときは、明示するまで上書きしません。同じ対応の再取込は増やしません。',
    '場所やサイズが空の行は、その項目を未指定として残せます。候補が複数のときはどれか一つに決めません。',
)

_SCI = re.compile(r'^[+-]?(?:\d+\.?\d*|\d*\.\d+)[eE][+-]?\d+$')
_USED_TRUE = {'true', '1', 'yes', '中古', 'used', 'はい'}
_USED_FALSE = {'false', '0', 'no', '新品', 'new', 'いいえ'}
_USED_EMPTY = {'', 'null', 'none', '未設定', '空', '空欄'}


def ensure_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS qr_link_import_runs (
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
        'CREATE INDEX IF NOT EXISTS qr_link_import_runs_sha '
        'ON qr_link_import_runs(file_sha256, created_at DESC)'
    )
    c.execute('''CREATE TABLE IF NOT EXISTS qr_stock_links (
      id INTEGER PRIMARY KEY,
      legacy_stock_id TEXT NOT NULL UNIQUE,
      legacy_customer_id TEXT NOT NULL DEFAULT '',
      legacy_maker_id TEXT NOT NULL DEFAULT '',
      legacy_item_id TEXT NOT NULL DEFAULT '',
      customer_name TEXT NOT NULL DEFAULT '',
      maker_name TEXT NOT NULL DEFAULT '',
      item_num TEXT NOT NULL DEFAULT '',
      item_name TEXT NOT NULL DEFAULT '',
      item_size TEXT NOT NULL DEFAULT '',
      is_used TEXT NOT NULL DEFAULT '',
      price_text TEXT NOT NULL DEFAULT '',
      qty_text TEXT NOT NULL DEFAULT '',
      factory_id TEXT NOT NULL DEFAULT '',
      factory_name TEXT NOT NULL DEFAULT '',
      shelf_id TEXT NOT NULL DEFAULT '',
      shelf_name TEXT NOT NULL DEFAULT '',
      arrival_date TEXT NOT NULL DEFAULT '',
      qr_raw TEXT NOT NULL DEFAULT '',
      customer_id INTEGER,
      product_id INTEGER,
      customer_product_id INTEGER,
      size_label TEXT NOT NULL DEFAULT '',
      condition TEXT NOT NULL DEFAULT '',
      location_id INTEGER,
      match_kind TEXT NOT NULL DEFAULT '',
      file_sha256 TEXT NOT NULL DEFAULT '',
      file_name TEXT NOT NULL DEFAULT '',
      source_row_no INTEGER,
      run_id INTEGER,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS qr_stock_links_customer '
        'ON qr_stock_links(legacy_customer_id, legacy_maker_id)'
    )


def template_bytes():
    headers = [label for label, _key, _req in COLUMNS]
    widths = [18, 18, 18, 16, 18, 18, 16, 22, 12, 14, 12, 12, 14, 14, 12, 14, 14, 42]
    notes = [(line,) for line in NOTES]
    return excel_book.workbook([
        (SHEET_NAME, headers, [], widths),
        (NOTE_SHEET, ['説明'], notes, [88]),
    ])


# 部分一致の短い側。ASCIIだけの品番は短すぎると広く当たりすぎる。別注のような漢字は2文字から。
_PARTIAL_ASCII_MIN = 6
_PARTIAL_OTHER_MIN = 2
# 括弧文字列が加工名を含む、の下限。2文字の「刺繍」が「LEOC刺繍」や「刺繍入り」に当たらない。
_PROC_CONTAIN_MIN = 3
# 今の商品名全体が旧品名に含まれる、の下限。1文字の含有では別商品に寄せない。
_NAME_CONTAIN_MIN = 2
_LEADING_NAME_STARS = re.compile(r'^[＊*]+')


def code_norm_key(value):
    """ハイフン無視の照合キー。完全一致には使わない。

    空白除去・NFKC・大文字に加え、棚卸の並びキーと同じハイフンを外す。
    色番や枝番の数字は残す（AZ-8618 と AZ-8618-003 は別のまま）。
    """
    t = unicodedata.normalize('NFKC', stock.text(value))
    t = re.sub(r'[\s\u3000]+', '', t).upper()
    import stock_stocktake
    return t.translate(stock_stocktake._CODE_HYPHENS)


def _identity(value):
    import legacy_csv_import as legacy
    return legacy.identity_match_key(value)


def _form_variant(a, b):
    import legacy_csv_import as legacy
    return legacy.looks_like_company_form_variant(a, b)


def stock_snapshot(c):
    lots = c.execute(
        '''SELECT COUNT(*) AS n, COALESCE(SUM(qty_remaining),0) AS q,
                  COALESCE(SUM(qty_remaining * unit_cost),0) AS a FROM lots'''
    ).fetchone()
    return {
        'lots': lots['n'],
        'qty': lots['q'],
        'amount': lots['a'],
        'moves': c.execute('SELECT COUNT(*) AS n FROM moves').fetchone()['n'],
        'products': c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'],
        'customers': c.execute('SELECT COUNT(*) AS n FROM customers').fetchone()['n'],
        'handles': c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'],
        'locations': c.execute('SELECT COUNT(*) AS n FROM locations').fetchone()['n'],
    }


def _db_fingerprint(c):
    ensure_schema(c)
    parts = []
    for table in (
        'customers', 'suppliers', 'products', 'product_sizes', 'customer_products',
        'locations', 'lots', 'moves', 'qr_stock_links',
    ):
        exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            parts.append(f'{table}:0')
            continue
        row = c.execute(
            f'SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS m FROM {table}'
        ).fetchone()
        parts.append(f'{table}:{row["n"]}:{row["m"]}')
    link = c.execute(
        "SELECT COALESCE(MAX(updated_at),'') AS u FROM qr_stock_links"
    ).fetchone()
    parts.append(f'links_at:{link["u"]}')
    snap = stock_snapshot(c)
    parts.append(
        f"snap:{snap['lots']}:{snap['qty']}:{snap['amount']}:{snap['moves']}"
    )
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:40]


def _preview_fingerprint(c, rows):
    parts = [_db_fingerprint(c)]
    for row in rows:
        keys = ','.join(item['key'] for item in row['candidates'])
        existing = row.get('existing') or {}
        parts.append(
            f"{row['rowNo']}|{row['status']}|{keys}|{existing.get('id') or ''}|"
            f"{existing.get('productId') or ''}|{existing.get('customerProductId') or ''}|"
            f"{existing.get('sizeLabel') or ''}|{existing.get('condition') or ''}|"
            f"{existing.get('locationId') or ''}"
        )
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()[:40]


def _cell_detail(cell, strings):
    kind = cell.get('t') or ''
    if kind == 'inlineStr':
        text = ''.join(node.text or '' for node in cell.iter(f'{excel_book.NS}t'))
        return {'text': text.strip(), 'kind': 'inline'}
    value = cell.find(f'{excel_book.NS}v')
    if value is None or value.text is None:
        return {'text': '', 'kind': 'blank'}
    raw = value.text
    if kind == 's':
        try:
            text = strings[int(raw)]
        except (IndexError, ValueError):
            text = ''
        return {'text': text.strip(), 'kind': 's'}
    if kind == 'b':
        return {'text': 'TRUE' if raw.strip() in ('1', 'true', 'TRUE') else 'FALSE', 'kind': 'b'}
    if kind in ('e', 'str'):
        return {'text': raw.strip(), 'kind': kind}
    # 型なしは数値。末尾 .0 だけ整数文字にする（ID列では後で不正にする）。
    text = raw.strip()
    if text.endswith('.0') and text[:-2].lstrip('-').isdigit():
        text = text[:-2]
    return {'text': text, 'kind': 'n'}


def _read_sheet_rows(blob):
    if not isinstance(blob, (bytes, bytearray)) or not blob.startswith(b'PK'):
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    if len(blob) > 8_000_000:
        raise ValueError('ファイルが大きすぎます。')
    try:
        book = ZipFile(BytesIO(blob))
    except Exception as e:
        raise ValueError('Excelファイル（.xlsx）を選んでください。') from e
    strings = excel_book._strings(book)
    try:
        workbook_xml = ET.fromstring(book.read('xl/workbook.xml'))
        rels = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
    except KeyError as e:
        raise ValueError('このExcelの書式は読み取れませんでした。') from e
    targets = {rel.get('Id'): rel.get('Target') for rel in rels}
    for sheet in workbook_xml.findall(f'{excel_book.NS}sheets/{excel_book.NS}sheet'):
        if (sheet.get('name') or '') != SHEET_NAME:
            continue
        target = targets.get(sheet.get(f'{excel_book.REL}id')) or ''
        path = target if target.startswith('xl/') else 'xl/' + target.lstrip('/')
        try:
            root = ET.fromstring(book.read(path))
        except KeyError as e:
            raise ValueError('「対応」シートを読み取れませんでした。') from e
        rows = []
        for row in root.iter(f'{excel_book.NS}row'):
            cells = {}
            for cell in row.findall(f'{excel_book.NS}c'):
                ref = cell.get('r') or ''
                if not ref:
                    continue
                cells[excel_book._col(ref)] = _cell_detail(cell, strings)
            if not cells:
                rows.append([])
                continue
            width = max(cells) + 1
            rows.append([cells.get(i, {'text': '', 'kind': 'blank'}) for i in range(width)])
        return rows
    raise ValueError('シート「対応」がありません。テンプレートの見出しを使ってください。')


def _header_map(header_row):
    found = {}
    for index, cell in enumerate(header_row):
        label = stock.text(cell.get('text'))
        if not label:
            continue
        key = HEADER_TO_KEY.get(label)
        if not key:
            continue
        if key in found:
            raise ValueError(f'列「{label}」が重複しています。')
        found[key] = index
    missing = []
    for key in REQUIRED_KEYS:
        if key not in found:
            label = next(name for name, k, _req in COLUMNS if k == key)
            missing.append(label)
    if missing:
        raise ValueError(
            '必要な列がありません: ' + '、'.join(missing)
            + '。テンプレートの見出しを使ってください。列の位置から推測しません。'
        )
    return found


def _parse_qr_object(text):
    raw = stock.text(text)
    if not raw:
        return None, ''
    if not raw.startswith('{'):
        return None, 'QR文字列は {"id","customer_id","maker_id"} のJSONにしてください。'
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None, 'QR文字列をJSONとして読めません。'
    if not isinstance(obj, dict):
        return None, 'QR文字列はオブジェクトではありません。'
    return obj, ''


def _id_problem(cell, label):
    kind = cell.get('kind') or 'blank'
    text = cell.get('text') or ''
    if kind == 'n':
        return f'{label}が数値セルです。文字として入れ直してください。'
    if kind == 'e':
        return f'{label}のセルがエラーです。'
    if _SCI.fullmatch(text.replace(' ', '')):
        return f'{label}が指数表記です。文字として入れ直してください。'
    return ''


def _parse_is_used(cell):
    kind = cell.get('kind') or 'blank'
    text = stock.text(cell.get('text'))
    if kind == 'b':
        return ('used' if text == 'TRUE' else 'new'), ''
    if text.lower() in _USED_EMPTY or text == '':
        return '', ''
    if text.lower() in _USED_TRUE:
        return 'used', ''
    if text.lower() in _USED_FALSE:
        return 'new', ''
    return None, f'新品中古「{text}」は、新品・中古・空欄・null のいずれかにしてください。'


def _clip(value, limit=500):
    text = value if isinstance(value, str) else ('' if value is None else str(value))
    text = text.strip()
    if len(text) <= limit:
        return text, ''
    return '', f'長すぎます（{limit}文字まで）。'


def parse_workbook(blob):
    """対応シートを行辞書にする。IDの数値セルは不正理由だけ付け、値は残す。"""
    grid = _read_sheet_rows(blob)
    header_index = None
    for i, row in enumerate(grid):
        if any(stock.text(cell.get('text')) for cell in row):
            header_index = i
            break
    if header_index is None:
        raise ValueError('見出し行がありません。')
    headers = _header_map(grid[header_index])
    parsed = []
    for offset, row in enumerate(grid[header_index + 1:], start=header_index + 2):
        cells = {}
        blank = True
        for key, index in headers.items():
            cell = row[index] if index < len(row) else {'text': '', 'kind': 'blank'}
            cells[key] = cell
            if stock.text(cell.get('text')):
                blank = False
        if blank:
            continue
        parsed.append((offset, cells))
    if not parsed:
        raise ValueError('読み込む行がありません。')
    if len(parsed) > 5000:
        raise ValueError('行が多すぎます（5000行まで）。')
    return parsed


def _apply_qr_fill(cells, reasons):
    qr_cell = cells.get('qr') or {'text': '', 'kind': 'blank'}
    qr_text = qr_cell.get('text') or ''
    obj, err = _parse_qr_object(qr_text)
    if err and stock.text(qr_text):
        reasons.append(err)
        return qr_text
    if not obj:
        return ''
    for json_key, col in (
        ('id', 'legacyStockId'),
        ('customer_id', 'legacyCustomerId'),
        ('maker_id', 'legacyMakerId'),
    ):
        incoming = '' if obj.get(json_key) is None else str(obj.get(json_key)).strip()
        current = cells.get(col) or {'text': '', 'kind': 'blank'}
        have = stock.text(current.get('text'))
        if not incoming:
            continue
        if have and have != incoming:
            reasons.append(f'{json_key} が列の値とQR文字列で違います。')
            continue
        if not have:
            cells[col] = {'text': incoming, 'kind': 'qr'}
    return qr_text.strip()


def _load_catalog(c):
    products = []
    by_exact = defaultdict(list)
    by_norm = defaultdict(list)
    for row in c.execute(
        '''SELECT p.id, p.code, p.name, p.active, p.supplier_id, s.name AS supplier_name
           FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id
           WHERE p.merged_into_id IS NULL
           ORDER BY p.id'''
    ):
        item = {
            'id': row['id'],
            'code': row['code'] or '',
            'name': row['name'] or '',
            'active': bool(row['active']),
            'supplierId': row['supplier_id'],
            'supplierName': row['supplier_name'] or '',
            'sizes': [],
            'subSupplierNames': [],
        }
        products.append(item)
        if stock.text(item['code']):
            by_exact[stock.text(item['code'])].append(item)
            by_norm[code_norm_key(item['code'])].append(item)
    sizes = defaultdict(list)
    for row in c.execute('SELECT product_id, label FROM product_sizes ORDER BY sort_order, id'):
        sizes[row['product_id']].append(row['label'])
    for item in products:
        item['sizes'] = sizes.get(item['id'], [])
    subs = defaultdict(list)
    names = {row['id']: row['name'] for row in c.execute('SELECT id, name FROM suppliers')}
    for row in c.execute('SELECT product_id, supplier_id FROM product_suppliers ORDER BY sort_order, id'):
        name = names.get(row['supplier_id']) or ''
        if name:
            subs[row['product_id']].append(name)
    for item in products:
        item['subSupplierNames'] = subs.get(item['id'], [])
    customers = [
        {'id': row['id'], 'name': row['name'] or '', 'active': bool(row['active'])}
        for row in c.execute('SELECT id, name, active FROM customers ORDER BY id')
    ]
    handles = defaultdict(list)
    for row in c.execute(
        '''SELECT id, customer_id, product_id, embroidery, active
           FROM customer_products ORDER BY id'''
    ):
        handles[(row['customer_id'], row['product_id'])].append({
            'id': row['id'],
            'embroidery': row['embroidery'] or '',
            'active': bool(row['active']),
        })
    locations = [
        {
            'id': row['id'],
            'name': row['name'] or '',
            'parentId': row['parent_id'],
            'parent_id': row['parent_id'],
            'active': bool(row['active']),
        }
        for row in c.execute('SELECT id, name, parent_id, active FROM locations ORDER BY id')
    ]
    links = {}
    for row in c.execute('SELECT * FROM qr_stock_links'):
        links[row['legacy_stock_id']] = row
    return {
        'by_exact': by_exact,
        'by_norm': by_norm,
        'customers': customers,
        'handles': handles,
        'locations': locations,
        'links': links,
    }


def _customer_hits(catalog, name):
    text = stock.text(name)
    if not text:
        return [], []
    key = _identity(text)
    exact, form = [], []
    for row in catalog['customers']:
        if _identity(row['name']) == key:
            exact.append(row)
        elif _form_variant(text, row['name']):
            form.append(row)
    return exact, form


def _name_match(left, right):
    if not stock.text(left) or not stock.text(right):
        return 'blank'
    if stock.text(left) == stock.text(right) or _identity(left) == _identity(right):
        return 'exact'
    return 'diff'


def _partial_name_key(value):
    """部分一致の品名。先頭の＊と空白だけを外して比べる。"""
    text = _LEADING_NAME_STARS.sub('', stock.text(value))
    return _identity(text)


def _partial_name_match(left, right):
    if not stock.text(left) or not stock.text(right):
        return 'blank'
    a, b = _partial_name_key(left), _partial_name_key(right)
    if a and a == b:
        return 'exact'
    return 'diff'


def _name_contain_key(value):
    """品名含有用。NFKCと先頭の＊、前後の空白だけ。途中の空白は残す。"""
    text = unicodedata.normalize('NFKC', stock.text(value))
    return _LEADING_NAME_STARS.sub('', text).strip()


def _current_name_in_legacy(legacy_name, current_name):
    """今の商品名の文字列全体が旧品名に含まれる。空白を消してつなぐ含有はしない。"""
    cur = _name_contain_key(current_name)
    old = _name_contain_key(legacy_name)
    if not cur or not old or len(cur) < _NAME_CONTAIN_MIN or cur == old:
        return False
    return cur in old


def _partial_name_relation(legacy_name, current_name):
    """品番の部分一致で使う品名の関係。完全一致を含有より先に見る。"""
    kind = _partial_name_match(legacy_name, current_name)
    if kind in ('exact', 'blank'):
        return kind
    if _current_name_in_legacy(legacy_name, current_name):
        return 'contained'
    return 'diff'


def _legacy_processing_raw(item_name):
    """旧品名の末尾側の括弧を加工名として読む。無ければ空（加工なし）。"""
    text = unicodedata.normalize('NFKC', stock.text(item_name))
    if not text:
        return ''
    found = re.findall(r'\(([^()]*)\)', text)
    if not found:
        return ''
    return stock.text(found[-1])


def _proc_key(value):
    """取扱の加工仕様の照合キー。加工なし・空・加工未整理は空。"""
    return stock.spec_key(stock.handling_processing(value))


def _paren_contains_proc(paren_key, handle_key):
    """括弧側が取扱の加工名を含む。逆（取扱が括弧文字列を含む）は見ない。"""
    if not paren_key or not handle_key or len(handle_key) < _PROC_CONTAIN_MIN:
        return False
    return handle_key in paren_key


def _select_handles(handle_rows, item_name):
    """加工名が一致する取扱へ絞る。括弧の中に加工名が含まれていればそれも選ぶ。

    完全一致を優先する。含有が複数なら1件に絞らない。無ければ加工なし。
    どちらも無ければ候補は残し、自動確定しない。
    """
    if not handle_rows:
        return [], 'leftover'
    wanted = _proc_key(_legacy_processing_raw(item_name))

    def key_of(row):
        return _proc_key(row.get('embroidery'))

    if wanted:
        hits = [row for row in handle_rows if key_of(row) == wanted]
        if hits:
            return hits, 'matched'
        contained = [row for row in handle_rows if _paren_contains_proc(wanted, key_of(row))]
        if contained:
            return contained, 'matched'
    plain = [row for row in handle_rows if key_of(row) == '']
    if plain:
        return plain, 'matched'
    return list(handle_rows), 'leftover'


def _supplier_match(product, maker_name):
    if not stock.text(maker_name):
        return 'blank'
    names = [product['supplierName'], *product['subSupplierNames']]
    if any(_name_match(maker_name, name) == 'exact' for name in names if stock.text(name)):
        return 'exact'
    if any(_form_variant(maker_name, name) for name in names if stock.text(name)):
        return 'form'
    return 'diff'


def _size_hits(raw, labels):
    text = stock.text(raw)
    if not text:
        return 'blank', list(labels)
    if text in labels:
        return 'exact', [text]
    norm = unicodedata.normalize('NFKC', text)
    hits = [label for label in labels if unicodedata.normalize('NFKC', label) == norm and label != text]
    if hits:
        return 'normalized', hits
    return 'missing', []


def _location_hits(catalog, site_name, shelf_name):
    site = stock.text(site_name)
    shelf = stock.text(shelf_name)
    locations = catalog['locations']
    if not site and not shelf:
        return 'blank', [None]
    if site and shelf:
        sites = [row for row in locations if row['parentId'] is None and stock.text(row['name']) == site]
        if len(sites) != 1:
            return 'missing', []
        shelves = [
            row for row in locations
            if row['parentId'] == sites[0]['id'] and stock.text(row['name']) == shelf
        ]
        if not shelves:
            return 'missing', []
        return ('exact' if len(shelves) == 1 else 'ambiguous'), shelves
    if site:
        sites = [row for row in locations if row['parentId'] is None and stock.text(row['name']) == site]
        if not sites:
            return 'missing', []
        return ('exact' if len(sites) == 1 else 'ambiguous'), sites
    shelves = [row for row in locations if row['parentId'] is not None and stock.text(row['name']) == shelf]
    if not shelves:
        return 'missing', []
    return ('exact' if len(shelves) == 1 else 'ambiguous'), shelves


def _product_hits(catalog, item_num):
    code = stock.text(item_num)
    if not code:
        return [], []
    exact = list(catalog['by_exact'].get(code, []))
    exact_ids = {item['id'] for item in exact}
    norm = [
        item for item in catalog['by_norm'].get(code_norm_key(code), [])
        if item['id'] not in exact_ids
    ]
    return exact, norm


def _partial_key_ok(short):
    if len(short) < _PARTIAL_OTHER_MIN:
        return False
    if all(ch.isascii() and ch.isalnum() for ch in short):
        return len(short) >= _PARTIAL_ASCII_MIN
    return True


def _code_partial(left, right):
    """正規化後の品番が、等しくはないが一方が他方に含まれる。

    枝番の数字だけ長い場合（AZ8618 と AZ8618003）は含めない。
    """
    if not left or not right or left == right:
        return False
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if not _partial_key_ok(short):
        return False
    at = long.find(short)
    if at < 0:
        return False
    prefix, suffix = long[:at], long[at + len(short):]
    if at == 0 and suffix.isdigit():
        return False
    if suffix == '' and prefix.isdigit():
        return False
    return True


def _catalog_products(catalog):
    cached = catalog.get('_all_products')
    if cached is not None:
        return cached
    seen = set()
    found = []
    for items in catalog['by_norm'].values():
        for item in items:
            if item['id'] in seen:
                continue
            seen.add(item['id'])
            found.append(item)
    catalog['_all_products'] = found
    return found


def _partial_product_hits(catalog, item_num, item_name):
    """完全一致も正規化一致も無いときだけ。品名が合う商品があれば、そちらだけに絞る。"""
    key = code_norm_key(item_num)
    if not _partial_key_ok(key):
        return []
    hits = []
    seen = set()
    for item in _catalog_products(catalog):
        if item['id'] in seen:
            continue
        other = code_norm_key(item['code'])
        if not _code_partial(key, other):
            continue
        seen.add(item['id'])
        hits.append(item)
    if not hits:
        return []
    named = [item for item in hits if _partial_name_match(item_name, item['name']) == 'exact']
    if named:
        return named
    contained = [item for item in hits if _current_name_in_legacy(item_name, item['name'])]
    if contained:
        return contained
    return hits


def _candidate_key(product_id, customer_id, handle_id, size, condition, location_id):
    return f'{product_id}:{customer_id}:{handle_id}:{size}:{condition}:{location_id or ""}'


def _auto_ok(cand):
    # 品番の完全一致は品名を見ない。顧客・サイズが揃い候補が1件なら完全一致（品名違いを含む）。
    # 部分一致は品名の完全一致、または今の商品名全体が旧品名に含まれるときに限る。
    # 正規化一致は自動確定しない。
    # 顧客の法人表記、サイズ空欄・不一致、メーカー名の違いは通さない。複数候補は呼び出し側で確定しない。
    # 加工の取扱が決まらなかった候補は自動確定しない。
    if cand.get('_processingPick') == 'leftover':
        return False
    if cand['codeMatch'] == 'partial':
        if cand['nameMatch'] not in ('exact', 'contained'):
            return False
    elif cand['codeMatch'] != 'exact':
        return False
    return (
        cand['supplierMatch'] in ('exact', 'blank')
        and cand['customerMatch'] == 'exact'
        and cand['sizeMatch'] == 'exact'
        and cand['locationMatch'] in ('exact', 'blank')
        and cand['productActive'] and cand['customerActive'] and cand['handleActive']
        and cand['locationActive']
    )


def _match_note(cand):
    bits = []
    if cand['codeMatch'] == 'normalized':
        bits.append('品番はハイフン等を無視した一致')
    elif cand['codeMatch'] == 'partial':
        bits.append('品番が部分一致します')
    if cand['nameMatch'] == 'contained':
        bits.append('品名が含まれます')
    elif cand['nameMatch'] == 'diff':
        bits.append('品名が違います')
    if cand['supplierMatch'] == 'form':
        bits.append('メーカー名は法人表記だけ違います')
    elif cand['supplierMatch'] == 'diff':
        bits.append('メーカー名が違います')
    if cand['customerMatch'] == 'form':
        bits.append('顧客名は法人表記だけ違います')
    if cand['sizeMatch'] == 'normalized':
        bits.append('サイズは全角半角の差です')
    elif cand['sizeMatch'] == 'blank':
        bits.append('サイズは空欄です')
    if cand['locationMatch'] == 'blank':
        bits.append('場所は未指定')
    elif cand['locationMatch'] == 'ambiguous':
        bits.append('同じ場所名が複数あります')
    if not cand['productActive']:
        bits.append('商品は停止中')
    if not cand['customerActive']:
        bits.append('顧客は停止中')
    if not cand['handleActive']:
        bits.append('取扱は停止中')
    if not cand['locationActive']:
        bits.append('場所は停止中')
    if not bits:
        return '完全一致'
    return '、'.join(bits)


def _build_candidates(c, catalog, *, item_num, item_name, maker_name, customer_name,
                      item_size, condition, site_name, shelf_name):
    reasons = []
    exact_products, norm_products = _product_hits(catalog, item_num)
    partial_products = []
    if not stock.text(item_num):
        reasons.append('品番が空です。商品は推測しません。')
        return [], reasons
    if not exact_products and not norm_products:
        partial_products = _partial_product_hits(catalog, item_num, item_name)
    if not exact_products and not norm_products and not partial_products:
        reasons.append(f'品番「{stock.text(item_num)}」の商品がありません。新しくは作りません。')
        return [], reasons
    exact_customers, form_customers = _customer_hits(catalog, customer_name)
    if not stock.text(customer_name):
        reasons.append('顧客名が空です。顧客は推測しません。')
        return [], reasons
    if exact_customers:
        customers = [(row, 'exact') for row in exact_customers]
    else:
        customers = [(row, 'form') for row in form_customers]
    if not customers:
        reasons.append(f'顧客「{stock.text(customer_name)}」がありません。新しくは作りません。')
        return [], reasons
    if len(exact_customers) > 1:
        reasons.append('空白を除くと同じ顧客名が複数あります。選んでください。')
    loc_kind, loc_rows = _location_hits(catalog, site_name, shelf_name)
    if loc_kind == 'missing':
        reasons.append('拠点または棚が見つかりません。場所は作りません。')
        return [], reasons
    products = (
        [(item, 'exact') for item in exact_products]
        + [(item, 'normalized') for item in norm_products]
        + [(item, 'partial') for item in partial_products]
    )
    candidates = []
    for product, code_match in products:
        size_kind, size_labels = _size_hits(item_size, product['sizes'])
        if size_kind == 'missing':
            continue
        if code_match == 'partial':
            name_match = _partial_name_relation(item_name, product['name'])
        else:
            name_match = _name_match(item_name, product['name'])
        supplier_match = _supplier_match(product, maker_name)
        for customer, customer_match in customers:
            handle_rows = [
                row for row in catalog['handles'].get((customer['id'], product['id']), [])
                if row['active']
            ]
            if not handle_rows:
                continue
            chosen_handles, processing_pick = _select_handles(handle_rows, item_name)
            for handle in chosen_handles:
                for size in size_labels:
                    places = loc_rows if loc_kind != 'blank' else [None]
                    for place in places:
                        location_id = place['id'] if place else None
                        cand = {
                            'key': _candidate_key(
                                product['id'], customer['id'], handle['id'], size, condition, location_id,
                            ),
                            'productId': product['id'],
                            'code': product['code'],
                            'productName': product['name'],
                            'codeMatch': code_match,
                            'nameMatch': name_match,
                            'supplierMatch': supplier_match,
                            'customerId': customer['id'],
                            'customerName': customer['name'],
                            'customerMatch': customer_match,
                            'handleId': handle['id'],
                            'embroidery': stock.embroidery_label(handle['embroidery']),
                            '_processingPick': processing_pick,
                            'size': size,
                            'sizeMatch': 'blank' if size_kind == 'blank' else size_kind,
                            'locationId': location_id,
                            'locationLabel': (
                                stock.location_label(c, place) if place else '場所未指定'
                            ),
                            'locationMatch': 'blank' if place is None else (
                                'ambiguous' if loc_kind == 'ambiguous' else 'exact'
                            ),
                            'condition': condition,
                            'conditionLabel': stock.condition_label(condition) if condition else '状態未指定',
                            'productActive': product['active'],
                            'customerActive': customer['active'],
                            'handleActive': handle['active'],
                            'locationActive': True if place is None else place['active'],
                        }
                        cand['auto'] = _auto_ok(cand)
                        cand['matchNote'] = _match_note(cand)
                        lot_n = _lot_count(
                            c, handle['id'], size, condition, location_id,
                        )
                        cand['lotCount'] = lot_n
                        cand['label'] = (
                            f"{product['code']} {product['name']} / {customer['name']} / "
                            f"{size or 'サイズ未指定'} / {cand['embroidery']} / {cand['conditionLabel']} / {cand['locationLabel']}"
                            f"（{cand['matchNote']}、在庫ロット {lot_n}件）"
                        )
                        candidates.append(cand)
    if products:
        if not candidates and not any('見つかりません' in r or 'ありません' in r for r in reasons):
            if _size_hits(item_size, [label for p, _k in products for label in p['sizes']])[0] == 'missing' and stock.text(item_size):
                reasons.append(f'サイズ「{stock.text(item_size)}」は商品にありません。サイズは追加しません。')
            else:
                reasons.append('この顧客と商品の取扱がありません。取扱は作りません。')
    # 候補が多すぎるときは確定させない
    if len(candidates) > 30:
        reasons.append('候補が多すぎます。品番・顧客名・サイズ・場所を絞ってください。')
        return [], reasons
    return candidates, reasons


def _lot_count(c, handle_id, size, condition, location_id):
    sql = 'SELECT COUNT(*) AS n FROM lots WHERE customer_product_id=? AND size_label=?'
    args = [handle_id, size]
    if condition:
        sql += ' AND condition=?'
        args.append(condition)
    if location_id is not None:
        sql += ' AND location_id=?'
        args.append(location_id)
    return c.execute(sql, args).fetchone()['n']


def _existing_public(row):
    if not row:
        return None
    return {
        'id': row['id'],
        'legacyStockId': row['legacy_stock_id'],
        'legacyCustomerId': row['legacy_customer_id'],
        'legacyMakerId': row['legacy_maker_id'],
        'customerId': row['customer_id'],
        'productId': row['product_id'],
        'customerProductId': row['customer_product_id'],
        'sizeLabel': row['size_label'] or '',
        'condition': row['condition'] or '',
        'locationId': row['location_id'],
        'matchKind': row['match_kind'] or '',
    }


def _same_target(existing, cand, legacy_customer_id, legacy_maker_id):
    if not existing or not cand:
        return False
    return (
        (existing['legacy_customer_id'] or '') == legacy_customer_id
        and (existing['legacy_maker_id'] or '') == legacy_maker_id
        and existing['customer_id'] == cand['customerId']
        and existing['product_id'] == cand['productId']
        and existing['customer_product_id'] == cand['handleId']
        and (existing['size_label'] or '') == (cand['size'] or '')
        and (existing['condition'] or '') == (cand['condition'] or '')
        and (existing['location_id'] or None) == (cand['locationId'] or None)
    )


def _status_for(candidates, reasons_block):
    if not candidates:
        return STATUS_UNMATCHED
    auto = [item for item in candidates if item['auto']]
    if len(candidates) > 1:
        return STATUS_AMBIGUOUS
    if len(auto) == 1:
        return STATUS_EXACT
    return STATUS_REVIEW


def classify_rows(c, parsed):
    ensure_schema(c)
    catalog = _load_catalog(c)
    rows = []
    seen = defaultdict(list)
    for row_no, cells in parsed:
        reasons = []
        qr_raw = _apply_qr_fill(cells, reasons)
        values = {}
        for key in (
            'legacyStockId', 'legacyCustomerId', 'legacyMakerId', 'legacyItemId',
            'customerName', 'makerName', 'itemNum', 'itemName', 'itemSize',
            'factoryId', 'factoryName', 'shelfId', 'shelfName', 'arrivalDate',
            'qty', 'price',
        ):
            cell = cells.get(key) or {'text': '', 'kind': 'blank'}
            if key in ID_KEYS:
                problem = _id_problem(cell, _label_of(key))
                if problem and (stock.text(cell.get('text')) or key in REQUIRED_KEYS):
                    reasons.append(problem)
            text, clip_err = _clip(cell.get('text') or '')
            if clip_err and key in REQUIRED_KEYS:
                reasons.append(_label_of(key) + 'が' + clip_err)
            values[key] = text
        for key, label in (
            ('legacyStockId', '旧在庫行ID'),
            ('legacyCustomerId', '旧顧客ID'),
            ('legacyMakerId', '旧メーカーID'),
        ):
            if not values[key]:
                if not any(label in r and ('空' in r or '数値' in r or '指数' in r) for r in reasons):
                    reasons.append(f'{label}が空です。')
            elif len(values[key]) > 80:
                reasons.append(f'{label}が長すぎます。')
        condition, cond_err = _parse_is_used(cells.get('isUsed') or {'text': '', 'kind': 'blank'})
        if cond_err:
            reasons.append(cond_err)
            condition = None
        is_used_text = '' if condition is None else condition
        legacy = {
            'stockId': values['legacyStockId'],
            'customerId': values['legacyCustomerId'],
            'makerId': values['legacyMakerId'],
            'itemId': values['legacyItemId'],
            'customerName': values['customerName'],
            'makerName': values['makerName'],
            'itemNum': values['itemNum'],
            'itemName': values['itemName'],
            'itemSize': values['itemSize'],
            'isUsed': is_used_text,
            'condition': condition or '',
            'priceText': values['price'],
            'qtyText': values['qty'],
            'factoryId': values['factoryId'],
            'factoryName': values['factoryName'],
            'shelfId': values['shelfId'],
            'shelfName': values['shelfName'],
            'arrivalDate': values['arrivalDate'],
            'qr': qr_raw,
        }
        fatal = bool(reasons)
        candidates = []
        match_reasons = []
        if not fatal and condition is not None:
            candidates, match_reasons = _build_candidates(
                c, catalog,
                item_num=values['itemNum'],
                item_name=values['itemName'],
                maker_name=values['makerName'],
                customer_name=values['customerName'],
                item_size=values['itemSize'],
                condition=condition,
                site_name=values['factoryName'],
                shelf_name=values['shelfName'],
            )
            reasons.extend(match_reasons)
        elif fatal:
            candidates = []
        if fatal or condition is None:
            status = STATUS_INVALID
        else:
            status = _status_for(candidates, reasons)
            if status == STATUS_EXACT:
                reasons = [r for r in reasons if '複数あります' not in r]
            if status == STATUS_REVIEW and not reasons:
                reasons.append('表記ゆれ、または未指定の項目があります。選ぶまで登録しません。')
            if status == STATUS_AMBIGUOUS:
                reasons.append('候補が複数あります。一つ選ぶまで登録しません。')
        existing = catalog['links'].get(values['legacyStockId']) if values['legacyStockId'] else None
        rows.append({
            'rowNo': row_no,
            'status': status,
            'reasons': reasons,
            'legacy': legacy,
            'candidates': candidates,
            'existing': _existing_public(existing),
            '_existing_row': existing,
        })
        if values['legacyStockId']:
            seen[values['legacyStockId']].append(rows[-1])

    for stock_id, group in seen.items():
        if len(group) < 2:
            continue
        for row in group:
            row['status'] = STATUS_DUPLICATE
            row['reasons'] = list(row['reasons']) + [
                f'このファイルに旧在庫行ID「{stock_id}」が複数あります。一つにしてください。'
            ]
            row['candidates'] = []

    for row in rows:
        existing = row.pop('_existing_row')
        if row['status'] in (STATUS_INVALID, STATUS_DUPLICATE):
            pass
        elif existing is not None:
            same = any(
                _same_target(existing, cand, row['legacy']['customerId'], row['legacy']['makerId'])
                for cand in row['candidates']
            )
            if same:
                row['status'] = STATUS_UNCHANGED
                row['reasons'] = ['同じ旧QRの同じ対応が登録済みです。増やしません。']
                row['_has_other'] = any(
                    not _same_target(existing, cand, row['legacy']['customerId'], row['legacy']['makerId'])
                    for cand in row['candidates']
                )
            else:
                row['status'] = STATUS_CONFLICT
                row['reasons'] = list(row['reasons']) + [
                    'この旧QRは別の対応で登録済みです。変えるときだけ指定してください。'
                ]
        row['statusLabel'] = STATUS_LABELS[row['status']]
        row['includeDefault'] = row['status'] == STATUS_EXACT
        row['canCommit'] = row['status'] in (STATUS_EXACT, STATUS_REVIEW, STATUS_AMBIGUOUS) or (
            row['status'] == STATUS_CONFLICT and bool(row['candidates'])
        ) or (row['status'] == STATUS_UNCHANGED and row.get('_has_other'))
        row.pop('_has_other', None)
    return rows


def _label_of(key):
    for label, col, _req in COLUMNS:
        if col == key:
            return label
    return key


def _row_public(row):
    return {
        'rowNo': row['rowNo'],
        'status': row['status'],
        'statusLabel': row['statusLabel'],
        'reasons': row['reasons'],
        'legacy': row['legacy'],
        'candidates': [
            {k: v for k, v in cand.items() if not k.startswith('_')}
            for cand in row['candidates']
        ],
        'existing': row['existing'],
        'includeDefault': row['includeDefault'],
        'canCommit': row['canCommit'],
    }


def build_preview(c, body):
    ensure_schema(c)
    uploaded = _decode_upload(body)
    rows = classify_rows(c, uploaded['parsed'])
    counts = {key: 0 for key in STATUS_LABELS}
    for row in rows:
        counts[row['status']] += 1
    fp = _preview_fingerprint(c, rows)
    return {
        'fileName': uploaded['fileName'],
        'fileSize': uploaded['fileSize'],
        'fileSha256': uploaded['fileSha256'],
        'rowCount': len(rows),
        'previewAt': stock.stamp(),
        'previewFingerprint': fp,
        'actorName': stock._actor_name(body),
        'counts': {STATUS_LABELS[k]: counts[k] for k in STATUS_LABELS},
        'countsRaw': counts,
        'rows': [_row_public(row) for row in rows],
        'notices': [
            'この段階ではデータベースを変更していません。',
            '登録するのは旧QRと現在の商品・顧客・サイズ・状態・場所の対応だけです。',
            '在庫数量・単価・金額・入出庫履歴は変えません。',
            '完全一致だけを初期選択にします。要確認・候補が複数・未一致は自動では確定しません。',
            '新品中古が空欄または null の行は、新品にしません。',
            '未一致から商品・顧客・取扱・場所は作りません。',
        ],
    }


def _decode_upload(body):
    raw = body.get('file') if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as e:
        raise ValueError('Excelファイル（.xlsx）を選んでください。') from e
    parsed = parse_workbook(blob)
    name = stock.text(body.get('name') or body.get('fileName') or 'qr-link.xlsx') or 'qr-link.xlsx'
    return {
        'blob': blob,
        'parsed': parsed,
        'fileName': name,
        'fileSize': len(blob),
        'fileSha256': hashlib.sha256(blob).hexdigest(),
    }


def _selection_map(body):
    raw = body.get('selections') if isinstance(body, dict) else None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError('確定する行の指定が不正です。')
    return raw


def _sel_for(selections, row_no):
    val = selections.get(str(row_no))
    if val is None:
        val = selections.get(row_no)
    return val if isinstance(val, dict) else {}


def _wants(row, sel):
    status = row['status']
    include = sel.get('include')
    if status == STATUS_EXACT:
        return include is not False
    if status == STATUS_UNCHANGED:
        return bool(include) and bool(sel.get('replace'))
    if status in (STATUS_REVIEW, STATUS_AMBIGUOUS, STATUS_CONFLICT):
        return bool(include)
    if include:
        raise ValueError(f'{row["rowNo"]}行目は{row["statusLabel"]}のため登録できません。')
    return False


def _pick_candidate(row, sel):
    if row['status'] == STATUS_EXACT and not sel.get('candidateKey'):
        auto = [item for item in row['candidates'] if item['auto']]
        if len(auto) != 1:
            raise ValueError(f'{row["rowNo"]}行目の完全一致を確認し直してください。')
        return auto[0]
    key = stock.text(sel.get('candidateKey'))
    if not key:
        raise ValueError(f'{row["rowNo"]}行目の候補を選んでください。')
    found = [item for item in row['candidates'] if item['key'] == key]
    if len(found) != 1:
        raise ValueError(f'{row["rowNo"]}行目の候補が見つかりません。確認し直してください。')
    cand = found[0]
    if row['status'] == STATUS_EXACT and not cand['auto']:
        raise ValueError(f'{row["rowNo"]}行目は完全一致の候補だけ登録できます。')
    if row['status'] == STATUS_CONFLICT and not sel.get('replace'):
        raise ValueError(f'{row["rowNo"]}行目は登録済みの別対応です。変更する場合だけ指定してください。')
    if row['status'] == STATUS_UNCHANGED and not sel.get('replace'):
        raise ValueError(f'{row["rowNo"]}行目は登録済みです。')
    return cand


def _insert_link(c, *, run_id, uploaded, row, cand, match_kind):
    legacy = row['legacy']
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
            legacy['stockId'], legacy['customerId'], legacy['makerId'], legacy['itemId'],
            legacy['customerName'], legacy['makerName'], legacy['itemNum'], legacy['itemName'],
            legacy['itemSize'], legacy['isUsed'], legacy['priceText'], legacy['qtyText'],
            legacy['factoryId'], legacy['factoryName'], legacy['shelfId'], legacy['shelfName'],
            legacy['arrivalDate'], legacy['qr'], cand['customerId'], cand['productId'],
            cand['handleId'], cand['size'], cand['condition'], cand['locationId'],
            match_kind, uploaded['fileSha256'], uploaded['fileName'], row['rowNo'], run_id, t, t,
        ),
    )


def _update_link(c, *, run_id, uploaded, row, cand, match_kind):
    legacy = row['legacy']
    t = stock.stamp()
    cur = c.execute(
        '''UPDATE qr_stock_links SET
             legacy_customer_id=?, legacy_maker_id=?, legacy_item_id=?,
             customer_name=?, maker_name=?, item_num=?, item_name=?, item_size=?, is_used=?,
             price_text=?, qty_text=?, factory_id=?, factory_name=?, shelf_id=?, shelf_name=?,
             arrival_date=?, qr_raw=?, customer_id=?, product_id=?, customer_product_id=?,
             size_label=?, condition=?, location_id=?, match_kind=?, file_sha256=?, file_name=?,
             source_row_no=?, run_id=?, updated_at=?
           WHERE legacy_stock_id=?''',
        (
            legacy['customerId'], legacy['makerId'], legacy['itemId'],
            legacy['customerName'], legacy['makerName'], legacy['itemNum'], legacy['itemName'],
            legacy['itemSize'], legacy['isUsed'], legacy['priceText'], legacy['qtyText'],
            legacy['factoryId'], legacy['factoryName'], legacy['shelfId'], legacy['shelfName'],
            legacy['arrivalDate'], legacy['qr'], cand['customerId'], cand['productId'],
            cand['handleId'], cand['size'], cand['condition'], cand['locationId'],
            match_kind, uploaded['fileSha256'], uploaded['fileName'], row['rowNo'], run_id, t,
            legacy['stockId'],
        ),
    )
    if cur.rowcount != 1:
        raise ValueError(f'{row["rowNo"]}行目の登録済み対応を更新できませんでした。')


def commit_preview(c, body, *, db_path=None):
    stock._require_write(body)
    ensure_schema(c)
    token = stock._token(body.get('token') or body.get('commitToken'))
    existing_token = c.execute(
        'SELECT id, result_json FROM qr_link_import_runs WHERE commit_token=?', (token,)
    ).fetchone()
    if existing_token:
        try:
            result = json.loads(existing_token['result_json'] or '{}')
        except json.JSONDecodeError:
            result = {}
        if not isinstance(result, dict):
            result = {}
        return {**result, 'already': True, 'runId': existing_token['id']}

    uploaded = _decode_upload(body)
    rows = classify_rows(c, uploaded['parsed'])
    fp_now = _preview_fingerprint(c, rows)
    preview_fp = stock.text(body.get('previewFingerprint'))
    if not preview_fp:
        raise ValueError('プレビュー結果を指定してください。もう一度確認してください。')
    if preview_fp != fp_now:
        raise stock.StockConflict({
            'error': '確認のあとでデータが変わったため、対応を登録しませんでした。もう一度確認してください。',
            'code': 'stale_preview',
            'previewFingerprint': preview_fp,
            'currentFingerprint': fp_now,
            'registered': False,
        })
    selections = _selection_map(body)
    chosen = []
    for row in rows:
        sel = _sel_for(selections, row['rowNo'])
        if not _wants(row, sel):
            continue
        if row['status'] in (STATUS_CONFLICT, STATUS_UNCHANGED) and not sel.get('replace'):
            raise ValueError(f'{row["rowNo"]}行目は登録済みです。変えるときだけ指定してください。')
        cand = _pick_candidate(row, sel)
        if row['status'] in (STATUS_UNCHANGED, STATUS_CONFLICT):
            existing = c.execute(
                'SELECT * FROM qr_stock_links WHERE legacy_stock_id=?',
                (row['legacy']['stockId'],),
            ).fetchone()
            if existing and _same_target(existing, cand, row['legacy']['customerId'], row['legacy']['makerId']):
                continue
        chosen.append((row, cand))
    if not chosen and not any(row['status'] == STATUS_UNCHANGED for row in rows):
        raise ValueError('登録する対応がありません。要確認・未一致・不正は、選ぶか保留のままです。')

    before = stock_snapshot(c)
    backup_path = ''
    if db_path:
        try:
            backup_path = str(stock.backup_sqlite(db_path, tag='pre-qr-link') or '')
        except stock.BackupError:
            raise
        except Exception as e:
            raise stock.BackupError(stock.BACKUP_FAILED_MESSAGE, cause=e) from e

    actor = stock._actor_name(body)
    t0 = stock.stamp()
    c.execute(
        '''INSERT INTO qr_link_import_runs(
             file_sha256, file_name, file_size, row_count, actor_name,
             preview_at, preview_fingerprint, commit_token, created_at, backup_path)
           VALUES(?,?,?,?,?,?,?,?,?,?)''',
        (
            uploaded['fileSha256'], uploaded['fileName'], uploaded['fileSize'], len(rows),
            actor, stock.text(body.get('previewAt')) or t0, preview_fp, token, t0, backup_path,
        ),
    )
    run_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    created = replaced = 0
    for row, cand in chosen:
        kind = 'exact' if cand['auto'] and row['status'] == STATUS_EXACT else 'manual'
        existing = c.execute(
            'SELECT id FROM qr_stock_links WHERE legacy_stock_id=?',
            (row['legacy']['stockId'],),
        ).fetchone()
        if existing:
            _update_link(c, run_id=run_id, uploaded=uploaded, row=row, cand=cand, match_kind=kind)
            replaced += 1
        else:
            _insert_link(c, run_id=run_id, uploaded=uploaded, row=row, cand=cand, match_kind=kind)
            created += 1
    after = stock_snapshot(c)
    if after != before:
        raise ValueError('在庫数量・金額・履歴が変わったため、対応の登録を中止しました。')
    unchanged = sum(1 for row in rows if row['status'] == STATUS_UNCHANGED)
    chosen_nos = {row['rowNo'] for row, _cand in chosen}
    held = [
        row['rowNo'] for row in rows
        if row['rowNo'] not in chosen_nos and row['status'] != STATUS_UNCHANGED
    ]
    result = {
        'already': False,
        'registered': True,
        'runId': run_id,
        'commitToken': token,
        'committedAt': stock.stamp(),
        'actorName': actor,
        'backupPath': backup_path,
        'created': created,
        'replaced': replaced,
        'unchanged': unchanged,
        'heldRowNos': held[:200],
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'stockUnchanged': True,
        'stockSnapshot': after,
        'notices': [
            f'対応を {created} 件登録し、{replaced} 件を指定どおり変更しました。同じ対応 {unchanged} 件は増やしていません。',
            '在庫数量・単価・金額・入出庫履歴は変えていません。',
        ],
    }
    c.execute(
        'UPDATE qr_link_import_runs SET committed_at=?, result_json=? WHERE id=?',
        (result['committedAt'], json.dumps(result, ensure_ascii=False), run_id),
    )
    return result


def lookup_qr(c, qr, *, legacy_customer_id=None, legacy_maker_id=None):
    """保存済み対応と、その条件に今あるロット一覧を返す。

    ロットが複数でも1件には決めない。出庫処理はしない。
    qr は旧在庫行IDの文字列、JSON文字列、または id を持つ辞書。
    """
    ensure_schema(c)
    stock_id, cust, maker = _qr_identity(qr, legacy_customer_id, legacy_maker_id)
    if not stock_id:
        return {'found': False, 'mismatch': '', 'link': None, 'lots': [], 'lotCount': 0, 'pickedLotId': None}
    row = c.execute(
        'SELECT * FROM qr_stock_links WHERE legacy_stock_id=?', (stock_id,)
    ).fetchone()
    if not row:
        return {
            'found': False, 'legacyStockId': stock_id, 'mismatch': '',
            'link': None, 'lots': [], 'lotCount': 0, 'pickedLotId': None,
        }
    mismatch = ''
    if cust is not None and cust != (row['legacy_customer_id'] or ''):
        mismatch = 'customer'
    elif maker is not None and maker != (row['legacy_maker_id'] or ''):
        mismatch = 'maker'
    link = _existing_public(row)
    link['customerName'] = row['customer_name'] or ''
    link['itemNum'] = row['item_num'] or ''
    link['itemName'] = row['item_name'] or ''
    product = c.execute(
        'SELECT id, merged_into_id, active, code, name FROM products WHERE id=?',
        (row['product_id'],),
    ).fetchone()
    if product and product['merged_into_id']:
        import stock_product_merge
        link['liveProductId'] = stock_product_merge.resolve_live_product_id(c, product['id'])
        link['stale'] = True
    elif not product:
        link['liveProductId'] = None
        link['stale'] = True
    else:
        link['liveProductId'] = product['id']
        link['stale'] = False
    lots = []
    if not mismatch and row['customer_product_id']:
        sql = '''SELECT l.id, l.qty_remaining, l.condition, l.size_label, l.location_id, l.unit_cost
                 FROM lots l WHERE l.customer_product_id=?'''
        args = [row['customer_product_id']]
        if row['size_label']:
            sql += ' AND l.size_label=?'
            args.append(row['size_label'])
        if row['condition']:
            sql += ' AND l.condition=?'
            args.append(row['condition'])
        if row['location_id'] is not None:
            sql += ' AND l.location_id=?'
            args.append(row['location_id'])
        sql += ' ORDER BY l.id'
        for lot in c.execute(sql, args):
            place = c.execute('SELECT * FROM locations WHERE id=?', (lot['location_id'],)).fetchone()
            lots.append({
                'lotId': lot['id'],
                'qtyRemaining': lot['qty_remaining'],
                'condition': lot['condition'] or '',
                'conditionLabel': stock.condition_label(lot['condition']) if lot['condition'] else '未設定',
                'size': lot['size_label'] or '',
                'locationId': lot['location_id'],
                'locationLabel': stock.location_label(c, place) if place else '',
                'unitCost': lot['unit_cost'],
            })
    return {
        'found': True,
        'legacyStockId': stock_id,
        'mismatch': mismatch,
        'link': link,
        'lots': [] if mismatch else lots,
        'lotCount': 0 if mismatch else len(lots),
        'pickedLotId': None,
        'note': '在庫ロットが複数でも、ここでは1件に決めません。',
    }


def _qr_identity(qr, legacy_customer_id, legacy_maker_id):
    cust = None if legacy_customer_id is None else stock.text(legacy_customer_id)
    maker = None if legacy_maker_id is None else stock.text(legacy_maker_id)
    if isinstance(qr, dict):
        stock_id = stock.text(qr.get('id') or qr.get('legacyStockId'))
        if cust is None and 'customer_id' in qr:
            cust = stock.text(qr.get('customer_id'))
        if maker is None and 'maker_id' in qr:
            maker = stock.text(qr.get('maker_id'))
        return stock_id, cust, maker
    text = stock.text(qr)
    if text.startswith('{'):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return '', cust, maker
        if isinstance(obj, dict):
            return _qr_identity(obj, legacy_customer_id, legacy_maker_id)
    return text, cust, maker
