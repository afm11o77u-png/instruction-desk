"""棚卸（月ごとに1枚の棚卸表）。顧客を選んで「追加」すると、その顧客の在庫を表の末尾に追加する。
実棚を入力し「実棚で在庫を上書き」で stock.register_stocktake を使い差分だけ棚卸調整する。
顧客の自動選別（帳簿のずれ等）は行わない。"""
import json
import re
import unicodedata
import uuid

import stock


def schema(c):
    # 旧形式（顧客×月ごとの棚卸表: stocktakes.customer_id）は試験データのみのため削除する。
    # 反映済みでも在庫の移動（moves / move_batches）には触れない。棚卸表の記録だけを消す。
    cols = {r['name'] for r in c.execute('PRAGMA table_info(stocktakes)')}
    if 'customer_id' in cols:
        c.execute('DROP TABLE IF EXISTS stocktake_rows')
        c.execute('DROP TABLE IF EXISTS stocktakes')
    c.execute('''CREATE TABLE IF NOT EXISTS stocktake_sheets (
      id INTEGER PRIMARY KEY,
      month TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL,
      created_by TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS stocktake_blocks (
      id INTEGER PRIMARY KEY,
      sheet_id INTEGER NOT NULL REFERENCES stocktake_sheets(id),
      customer_id INTEGER NOT NULL REFERENCES customers(id),
      sort_no INTEGER NOT NULL DEFAULT 0,
      added_at TEXT NOT NULL,
      added_by TEXT NOT NULL DEFAULT '',
      applied_at TEXT,
      applied_by TEXT NOT NULL DEFAULT '',
      UNIQUE(sheet_id, customer_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS stocktake_sheet_rows (
      id INTEGER PRIMARY KEY,
      block_id INTEGER NOT NULL REFERENCES stocktake_blocks(id),
      customer_product_id INTEGER NOT NULL,
      size_label TEXT NOT NULL,
      location_id INTEGER NOT NULL,
      condition TEXT NOT NULL,
      location_label TEXT NOT NULL DEFAULT '',
      office_code TEXT NOT NULL DEFAULT '',
      customer_name TEXT NOT NULL DEFAULT '',
      product_name TEXT NOT NULL DEFAULT '',
      product_code TEXT NOT NULL DEFAULT '',
      book_qty INTEGER NOT NULL,
      lots_json TEXT NOT NULL DEFAULT '[]',
      inc_price INTEGER,
      inc_source TEXT,
      actual_qty INTEGER,
      note TEXT NOT NULL DEFAULT '',
      applied_batch_id INTEGER REFERENCES move_batches(id),
      sort_no INTEGER NOT NULL DEFAULT 0
    )''')
    if 'check_json' not in {r['name'] for r in c.execute('PRAGMA table_info(stocktake_sheet_rows)')}:
        # 棚卸チェックシートの5つの入力欄（空欄は null）。計＝合計が実棚点数になる。
        c.execute("ALTER TABLE stocktake_sheet_rows ADD COLUMN check_json TEXT NOT NULL DEFAULT '[]'")
    c.execute('CREATE INDEX IF NOT EXISTS stocktake_sheet_rows_block ON stocktake_sheet_rows(block_id, sort_no)')
    # 顧客ごとの行の並び（ドラッグで保存）。月の棚卸表とは独立して保持し、再追加・翌月にも使う。
    c.execute('''CREATE TABLE IF NOT EXISTS stocktake_row_order (
      customer_id INTEGER NOT NULL,
      customer_product_id INTEGER NOT NULL,
      size_label TEXT NOT NULL,
      location_id INTEGER NOT NULL,
      condition TEXT NOT NULL,
      pos INTEGER NOT NULL,
      PRIMARY KEY(customer_id, customer_product_id, size_label, location_id, condition)
    )''')


def _month(v):
    s = stock.text(v)
    if len(s) != 7 or s[4] != '-' or not (s[:4] + s[5:]).isdigit() or not 1 <= int(s[5:]) <= 12:
        raise ValueError('年月を選んでください。')
    return s


def _amount(lots):
    """確定原価の金額。原価未確定の数量が残っていれば None（0 と表示しない）。"""
    total = 0
    for qty, cost in lots:
        if qty == 0:
            continue
        if cost is None:
            return None
        total += qty * cost
    return total


def _unit(lots):
    costs = {cost for qty, cost in lots if qty > 0}
    if not costs or None in costs:
        return None, bool(costs)
    if len(costs) == 1:
        return next(iter(costs)), False
    return None, False


def _after_lots(lots, book, actual, inc_price):
    """上書き後のロット構成（register_stocktake と同じ考え方: 増分は新ロット、減分はFIFOで払出し）。"""
    lots = [list(x) for x in lots]
    if actual > book:
        lots.append([actual - book, inc_price])
    elif actual < book:
        need = book - actual
        for lot in lots:
            if need <= 0:
                break
            if lot[0] <= 0:
                continue
            take = min(need, lot[0])
            lot[0] -= take
            need -= take
        if need > 0:
            lots.append([-need, None])
    return [tuple(x) for x in lots]


def _row_payload(r, applied):
    lots = [tuple(x) for x in json.loads(r['lots_json'] or '[]')]
    unit, mixed_pending = _unit(lots)
    costs = {cost for qty, cost in lots if qty > 0 and cost is not None}
    book_amount = _amount(lots)
    actual = r['actual_qty']
    out = {
        'id': r['id'], 'blockId': r['block_id'], 'officeCode': r['office_code'], 'customerName': r['customer_name'],
        'productName': r['product_name'], 'productCode': r['product_code'], 'size': r['size_label'],
        'locationLabel': r['location_label'], 'condition': r['condition'],
        'conditionLabel': stock.condition_label(r['condition']),
        'unitCost': unit, 'unitMixed': unit is None and len(costs) > 1 and not mixed_pending,
        'bookQty': r['book_qty'], 'bookAmount': book_amount,
        'actualQty': actual, 'diffQty': None, 'actualAmount': None, 'diffAmount': None,
        'note': r['note'] or '', 'incPrice': r['inc_price'], 'applied': applied,
        'adjusted': r['applied_batch_id'] is not None,
        'supplierName': r['supplier_name'] if 'supplier_name' in r.keys() else '',
    }
    checks = check_values(r)
    out['checks'] = checks
    out['checkTotal'] = check_total(checks)
    # チェックシート入力後に棚卸表の実棚点数を直接変えて計と違う場合の印
    out['checkMismatch'] = out['checkTotal'] is not None and out['checkTotal'] != actual
    if actual is not None:
        out['diffQty'] = actual - r['book_qty']
        out['actualAmount'] = _amount(_after_lots(lots, r['book_qty'], actual, r['inc_price']))
        if out['actualAmount'] is not None and book_amount is not None:
            out['diffAmount'] = out['actualAmount'] - book_amount
    return out


CHECK_COLS = 5  # 入力欄の上限

# 棚卸チェックシートの印刷レイアウト（雛形の実測値）。A4横・左0.71in/右0.31in → 印刷幅 10.67in ≒ 1024px（96dpi）。
# Excelの列幅w（文字数）は約 w*7+5 px。雛形どおり 仕入先 以外は固定幅、仕入先は文字幅に合わせて広げる
# （雛形: 通常14.125、「石屋商事　あまり制服」で35.875）。残りの幅に入力欄（10.625）を入るだけ並べる。
PRINT_PX = int((11.6929 - 0.7086614173228347 - 0.31496062992125984) * 96)
FIXED_WIDTHS = {'B': 24.0, 'C': 21.125, 'D': 6.625, 'E': 6.625}
SUPPLIER_MIN_WIDTH = 14.125
INPUT_WIDTH = 10.625  # 入力欄と計


def col_px(w):
    return int(w * 7 + 5)


def display_width(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in 'WFA' else 1 for ch in str(s or ''))


SUPPLIER_WRAP_MAX = 28  # 標準幅14.125・行高30で折り返し2行に収まる文字幅（半角換算）


def supplier_width(names):
    # 雛形: 2行に収まる仕入先（半角換算26まで）は14.125のまま、収まらない長さ（38）は1行で入る幅（35.875）に広げる
    m = max([display_width(n) for n in names] + [0])
    if m <= SUPPLIER_WRAP_MAX:
        return SUPPLIER_MIN_WIDTH
    return round(min(m * 0.92 + 1, 60), 3)


def check_layout(names):
    sw = supplier_width(names)
    fixed = col_px(sw) + sum(col_px(w) for w in FIXED_WIDTHS.values()) + col_px(INPUT_WIDTH)  # 計
    n = (PRINT_PX - fixed) // col_px(INPUT_WIDTH)
    return {'supplierWidth': sw, 'checkCols': max(1, min(CHECK_COLS, n))}


def check_values(r):
    try:
        vals = json.loads(r['check_json'] or '[]')
    except (ValueError, KeyError, IndexError):
        vals = []
    return [v if isinstance(v, int) and not isinstance(v, bool) else None for v in vals][:CHECK_COLS]


def fit_checks(vals, n):
    # 入力欄の数に合わせる（欄より後ろに値がある場合は消さずに残す）
    vals = list(vals)
    while len(vals) > n and vals[-1] is None:
        vals.pop()
    return vals + [None] * (n - len(vals))


def check_total(vals):
    nums = [v for v in vals if v is not None]
    return sum(nums) if nums else None


def parse_checks(raw):
    if not isinstance(raw, list) or len(raw) > CHECK_COLS:
        raise ValueError('チェック欄の値が正しくありません。')
    out = []
    for v in raw:
        if v is None or (isinstance(v, str) and v.strip() == ''):
            out.append(None)
        else:
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            out.append(stock.parse_qty(v, 'チェック欄', allow_zero=True))
    while out and out[-1] is None:
        out.pop()
    return out


def _sheet(c, month):
    return c.execute('SELECT * FROM stocktake_sheets WHERE month=?', (month,)).fetchone()


def get(c, month):
    month = _month(month)
    sh = _sheet(c, month)
    out = {'month': month, 'id': None, 'blocks': [], 'rows': [], 'totals': {}}
    rows = []
    if sh:
        out['id'] = sh['id']
        for b in c.execute('''SELECT b.*, cu.name AS customer_name FROM stocktake_blocks b
                             JOIN customers cu ON cu.id=b.customer_id WHERE b.sheet_id=? ORDER BY b.sort_no, b.id''',
                          (sh['id'],)).fetchall():
            applied = bool(b['applied_at'])
            brows = [_row_payload(r, applied) for r in c.execute(
                '''SELECT x.*, COALESCE(su.name,'') AS supplier_name FROM stocktake_sheet_rows x
                   LEFT JOIN customer_products cp ON cp.id=x.customer_product_id
                   LEFT JOIN products p ON p.id=cp.product_id LEFT JOIN suppliers su ON su.id=p.supplier_id
                   WHERE x.block_id=? ORDER BY x.sort_no, x.id''', (b['id'],))]
            lay = check_layout([r['supplierName'] for r in brows])
            for r in brows:
                r['checks'] = fit_checks(r['checks'], lay['checkCols'])
            out['blocks'].append({**lay, 'id': b['id'], 'customerId': b['customer_id'], 'customerName': b['customer_name'],
                                  'addedAt': b['added_at'], 'addedBy': b['added_by'], 'appliedAt': b['applied_at'],
                                  'appliedBy': b['applied_by'], 'applied': applied, 'rowCount': len(brows),
                                  'enteredCount': sum(1 for r in brows if r['actualQty'] is not None)})
            rows += brows
    out['rows'] = rows
    def total(key, src):
        vals = [r[key] for r in src]
        return None if any(v is None for v in vals) else sum(vals)
    entered = [r for r in rows if r['actualQty'] is not None]
    out['totals'] = {
        'bookQty': sum(r['bookQty'] for r in rows), 'bookAmount': total('bookAmount', rows),
        'actualQty': sum(r['actualQty'] for r in entered) if entered else None,
        'actualAmount': None if not rows or len(entered) != len(rows) else total('actualAmount', rows),
    }
    return out


def list_stocktakes(c):
    months = []
    for r in c.execute('''SELECT s.id, s.month, s.created_at,
                                 (SELECT COUNT(*) FROM stocktake_blocks b WHERE b.sheet_id=s.id) AS customers,
                                 (SELECT COUNT(*) FROM stocktake_blocks b WHERE b.sheet_id=s.id AND b.applied_at IS NOT NULL) AS applied,
                                 (SELECT COUNT(*) FROM stocktake_sheet_rows x JOIN stocktake_blocks b ON b.id=x.block_id WHERE b.sheet_id=s.id) AS n,
                                 (SELECT COUNT(*) FROM stocktake_sheet_rows x JOIN stocktake_blocks b ON b.id=x.block_id WHERE b.sheet_id=s.id AND x.actual_qty IS NOT NULL) AS entered
                          FROM stocktake_sheets s ORDER BY s.month DESC'''):
        months.append({'month': r['month'], 'customerCount': r['customers'], 'appliedCount': r['applied'],
                       'rowCount': r['n'], 'enteredCount': r['entered'], 'createdAt': r['created_at']})
    customers = [{'id': r['id'], 'name': r['name'], 'kana': r['kana'] or ''} for r in c.execute(
        'SELECT id, name, kana FROM customers WHERE active=1 ORDER BY name')]
    customers.sort(key=lambda x: stock.customer_sort_key(x['name']))
    return {'months': months, 'customers': customers}


def _inc_price(c, cp_id, size, location_id, condition):
    """増分の単価: 登録単価（特値→現単価）、なければ同じ対象の最新の確定ロット単価、なければ原価未確定。"""
    price, source = stock.registered_shortage_price(c, cp_id, size)
    if source:
        return price, source
    r = c.execute('''SELECT unit_cost FROM lots WHERE customer_product_id=? AND size_label=? AND location_id=?
                     AND condition=? AND COALESCE(cost_pending,0)=0 AND unit_cost IS NOT NULL
                     ORDER BY COALESCE(received_on,'') DESC, id DESC LIMIT 1''',
                  (cp_id, size, location_id, condition)).fetchone()
    if r:
        return r['unit_cost'], 'lot'
    return None, None


_SIZE_RANK = {'XS': 1, 'SS': 2, 'S': 3, 'M': 4, 'L': 5, 'LL': 6, 'XL': 6, 'XXL': 7, '2L': 6, '3L': 7, '4L': 8,
              '5L': 9, '6L': 10, '7L': 11, '8L': 12}


def size_key(size):
    """サイズの小→大。数値は数値順、SS<S<M<L<LL<3L<4L…、F/FREE は文字サイズの後、その他は文字列順。"""
    s = unicodedata.normalize('NFKC', str(size or '')).strip().upper()
    if s in _SIZE_RANK:
        return (1, _SIZE_RANK[s], s)
    m = re.fullmatch(r'(\d+)X?L', s)
    if m:
        return (1, int(m.group(1)) + 4, s)
    m = re.fullmatch(r'(\d+(?:\.\d+)?)(.*)', s)
    if m:
        return (0, float(m.group(1)), m.group(2))
    if s in ('F', 'FREE', 'フリー', 'FS'):
        return (2, 0, s)
    return (3, 0, s)


def supplier_key(name):
    """仕入先名の Shift-JIS（cp932）バイト順。"""
    try:
        return str(name or '').encode('cp932')
    except UnicodeEncodeError:
        return str(name or '').encode('cp932', 'replace')


def default_row_key(supplier, code, size):
    return (supplier_key(supplier), str(code or ''), size_key(size))


def order_items(items, saved):
    """items: 既定順に並んだキーの列。saved: キー→pos。保存済みは保存順に並べ、新しいものは既定順で
    直前の行の後ろ（先頭側で直前がなければ、次の保存済み行の前）へ入れる。"""
    out = sorted([k for k in items if k in saved], key=lambda k: saved[k])
    prev = None
    lead = []
    for k in items:
        if k in saved:
            if lead:
                i = out.index(k)
                out[i:i] = lead
                lead = []
            prev = k
        elif prev is None:
            lead.append(k)
        else:
            out.insert(out.index(prev) + 1, k)
            prev = k
    return out + lead


def save_order(c, body):
    """行のドラッグ並べ替え: rowIds（その顧客の全行）の順で保存。"""
    stock._require_write(body)
    month = _month(body.get('month'))
    b = _block(c, month, body.get('customerId'))
    ids = body.get('rowIds')
    rows = {r['id']: r for r in c.execute('SELECT * FROM stocktake_sheet_rows WHERE block_id=?', (b['id'],))}
    if not isinstance(ids, list) or sorted(ids) != sorted(rows):
        raise ValueError('並び順が正しくありません。')
    for i, rid in enumerate(ids, 1):
        r = rows[rid]
        c.execute('UPDATE stocktake_sheet_rows SET sort_no=? WHERE id=?', (i, rid))
        c.execute('''INSERT INTO stocktake_row_order(customer_id,customer_product_id,size_label,location_id,condition,pos)
                     VALUES(?,?,?,?,?,?) ON CONFLICT(customer_id,customer_product_id,size_label,location_id,condition)
                     DO UPDATE SET pos=excluded.pos''',
                  (b['customer_id'], r['customer_product_id'], r['size_label'], r['location_id'], r['condition'], i))
    return get(c, month)


def _snapshot_rows(c, block_id, cu):
    lots = c.execute('''SELECT l.*, p.code, p.name AS product_name, p.color, cp.embroidery, COALESCE(su.name,'') AS supplier_name
                        FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
                        JOIN products p ON p.id=cp.product_id LEFT JOIN suppliers su ON su.id=p.supplier_id
                        WHERE cp.customer_id=? AND l.qty_remaining<>0
                        ORDER BY p.code, p.id, cp.embroidery, l.size_label, l.location_id, l.condition, l.id''',
                     (cu['id'],)).fetchall()
    groups = {}
    for lot in lots:
        key = (lot['customer_product_id'], lot['size_label'], lot['location_id'], lot['condition'])
        groups.setdefault(key, []).append(lot)
    keys = sorted(groups, key=lambda k: default_row_key(groups[k][0]['supplier_name'], groups[k][0]['code'], k[1]))
    saved = {(r['customer_product_id'], r['size_label'], r['location_id'], r['condition']): r['pos'] for r in c.execute(
        'SELECT * FROM stocktake_row_order WHERE customer_id=?', (cu['id'],))}
    no = 0
    for key in order_items(keys, saved):
        glots = groups[key]
        cp_id, size, loc_id, cond = key
        ordered = stock.issue_sequence([l for l in glots if l['qty_remaining'] > 0])
        ordered += [l for l in glots if l['qty_remaining'] <= 0]
        snap = [[l['qty_remaining'], None if l['cost_pending'] else l['unit_cost']] for l in ordered]
        book = sum(l['qty_remaining'] for l in glots)
        first = glots[0]
        name = first['product_name']
        if first['color']:
            name += ' ' + first['color']
        if first['embroidery']:
            name += f"（{stock.embroidery_label(first['embroidery'])}）"
        loc = c.execute('SELECT * FROM locations WHERE id=?', (loc_id,)).fetchone()
        inc, src = _inc_price(c, cp_id, size, loc_id, cond)
        no += 1
        c.execute('''INSERT INTO stocktake_sheet_rows(block_id,customer_product_id,size_label,location_id,condition,
                     location_label,office_code,customer_name,product_name,product_code,book_qty,lots_json,
                     inc_price,inc_source,sort_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (block_id, cp_id, size, loc_id, cond, stock.location_label(c, loc) if loc else '',
                   (cu['office_code'] or ''), cu['name'], name, first['code'] or '', book,
                   json.dumps(snap), inc, src, no))
    return no


def add_customer(c, body):
    """顧客をその月の棚卸表に追加（在庫をスナップショットして末尾に行を追加）。追加済みなら何もしない。
    customerIds（複数）にも対応。結果に added（行数）・addedCustomers・skipped（追加済みで飛ばした顧客数）。"""
    ids = body.get('customerIds')
    if ids is None:
        return _add_one(c, body, body.get('customerId'))
    if not isinstance(ids, list) or not ids:
        raise ValueError('顧客を選んでください。')
    rows = custs = skipped = 0
    for cid in ids:
        r = _add_one(c, body, cid)
        if r.get('already'):
            skipped += 1
        else:
            custs += 1
            rows += r['added']
    return {**get(c, _month(body.get('month'))), 'added': rows, 'addedCustomers': custs, 'skipped': skipped}


def _add_one(c, body, cid):
    stock._require_write(body)
    month = _month(body.get('month'))
    cu = c.execute('SELECT * FROM customers WHERE id=?', (cid,)).fetchone() if isinstance(cid, int) else None
    if not cu:
        raise ValueError('顧客を選んでください。')
    t = stock.stamp()
    actor = stock._actor_name(body)
    sh = _sheet(c, month)
    if not sh:
        c.execute('INSERT INTO stocktake_sheets(month,created_at,created_by,updated_at) VALUES(?,?,?,?)', (month, t, actor, t))
        sh = _sheet(c, month)
    if c.execute('SELECT id FROM stocktake_blocks WHERE sheet_id=? AND customer_id=?', (sh['id'], cid)).fetchone():
        return {**get(c, month), 'already': True, 'added': 0}
    no = c.execute('SELECT COALESCE(MAX(sort_no),0)+1 FROM stocktake_blocks WHERE sheet_id=?', (sh['id'],)).fetchone()[0]
    c.execute('INSERT INTO stocktake_blocks(sheet_id,customer_id,sort_no,added_at,added_by) VALUES(?,?,?,?,?)',
              (sh['id'], cid, no, t, actor))
    bid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    n = _snapshot_rows(c, bid, cu)
    c.execute('UPDATE stocktake_sheets SET updated_at=? WHERE id=?', (t, sh['id']))
    return {**get(c, month), 'added': n}


def _block(c, month, cid):
    sh = _sheet(c, month)
    b = c.execute('SELECT * FROM stocktake_blocks WHERE sheet_id=? AND customer_id=?',
                  (sh['id'], cid)).fetchone() if sh else None
    if not b:
        raise ValueError('この顧客は棚卸表にありません。')
    return b


def remove_customer(c, body):
    stock._require_write(body)
    month = _month(body.get('month'))
    b = _block(c, month, body.get('customerId'))
    if b['applied_at']:
        raise ValueError('在庫に反映済みの顧客は外せません。')
    c.execute('DELETE FROM stocktake_sheet_rows WHERE block_id=?', (b['id'],))
    c.execute('DELETE FROM stocktake_blocks WHERE id=?', (b['id'],))
    return get(c, month)


def save(c, body):
    stock._require_write(body)
    month = _month(body.get('month'))
    sh = _sheet(c, month)
    if not sh:
        raise ValueError('棚卸表が見つかりません。')
    rows = body.get('rows')
    if not isinstance(rows, list):
        raise ValueError('実棚を入力してください。')
    for r in rows:
        if not isinstance(r, dict):
            continue
        cur = c.execute('''SELECT x.*, b.applied_at FROM stocktake_sheet_rows x JOIN stocktake_blocks b ON b.id=x.block_id
                           WHERE x.id=? AND b.sheet_id=?''', (r.get('id'), sh['id'])).fetchone()
        if not cur:
            continue
        checks_json = cur['check_json']
        if 'checks' in r:
            checks = parse_checks(r.get('checks'))
            checks_json = json.dumps(checks)
            raw = check_total(checks)
        else:
            raw = r.get('actualQty')
        if raw is None or (isinstance(raw, str) and raw.strip() == ''):
            val = None
        else:
            val = stock.parse_qty(raw, '実棚点数', allow_zero=True)
        note = stock.optional(r.get('note'), '備考', 200) if 'note' in r else cur['note']
        if cur['applied_at']:
            if val != cur['actual_qty'] or note != cur['note'] or checks_json != cur['check_json']:
                raise ValueError('在庫に反映済みの顧客の行は変更できません。')
            continue
        c.execute('UPDATE stocktake_sheet_rows SET actual_qty=?, note=?, check_json=? WHERE id=?',
                  (val, note, checks_json, cur['id']))
    c.execute('UPDATE stocktake_sheets SET updated_at=? WHERE id=?', (stock.stamp(), sh['id']))
    return get(c, month)


def apply(c, body):
    """実棚で在庫を上書き。未反映の顧客のうち実棚が1行以上入力された顧客を対象に、
    実棚入力済みで現在庫と違う行だけ register_stocktake で棚卸調整し、その顧客を反映済みにする。"""
    stock._require_write(body)
    if body.get('confirm') not in (True, 1, '1'):
        raise ValueError('確認してから実行してください。')
    month = _month(body.get('month'))
    sh = _sheet(c, month)
    if not sh:
        raise ValueError('棚卸表が見つかりません。')
    t = stock.stamp()
    actor = stock._actor_name(body)
    adjusted = 0
    applied_customers = 0
    for b in c.execute('SELECT * FROM stocktake_blocks WHERE sheet_id=? AND applied_at IS NULL ORDER BY sort_no, id',
                       (sh['id'],)).fetchall():
        rows = c.execute('SELECT * FROM stocktake_sheet_rows WHERE block_id=? AND actual_qty IS NOT NULL ORDER BY sort_no, id',
                         (b['id'],)).fetchall()
        if not rows:
            continue
        for r in rows:
            cur = stock.current_qty_for_target(c, r['customer_product_id'], r['size_label'], r['location_id'], r['condition'])
            if cur == r['actual_qty']:
                continue
            res = stock.register_stocktake(c, {
                '_actor': body.get('_actor'), '_canWrite': body.get('_canWrite'),
                'token': f'stocktake-{sh["id"]}-{r["id"]}-{uuid.uuid4().hex[:8]}',
                'customerProductId': r['customer_product_id'], 'size': r['size_label'],
                'locationId': r['location_id'], 'condition': r['condition'],
                'actualQty': r['actual_qty'], 'businessOn': t[:10],
                'unitCost': r['inc_price'],
                'note': f"棚卸調整（{month} 棚卸・{r['customer_name']}・{cur}→{r['actual_qty']}）",
            })
            c.execute('UPDATE stocktake_sheet_rows SET applied_batch_id=? WHERE id=?', (res.get('batchId'), r['id']))
            adjusted += 1
        c.execute('UPDATE stocktake_blocks SET applied_at=?, applied_by=? WHERE id=?', (t, actor, b['id']))
        applied_customers += 1
    c.execute('UPDATE stocktake_sheets SET updated_at=? WHERE id=?', (t, sh['id']))
    return {**get(c, month), 'adjusted': adjusted, 'appliedCustomers': applied_customers}
