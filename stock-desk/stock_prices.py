"""現単価（仕入先×商品×サイズの基準単価）と顧客別の特値。

- 基準単価: price_base（状態: ok / review=要確認 / unset=未設定）
- 特値: price_special（手入力のみ。size_label が NULL なら全サイズ）
- 棚卸Excelの品番読替: price_code_map
- 初期設定（run_initial）は毎回同じルールで基準単価を上書きする。在庫数量は変更しない。
"""
import base64
import collections
import datetime
import io
import json
import re
import unicodedata

import stock

STATUS_OK = 'ok'
STATUS_REVIEW = 'review'
STATUS_UNSET = 'unset'
STATUS_LABELS = {STATUS_OK: '', STATUS_REVIEW: '要確認', STATUS_UNSET: '未設定'}

REASON_STOCKTAKE_VS_DB = '棚卸と旧データの最高単価が違う'
REASON_CUSTOMER_SPREAD = '顧客間で棚卸単価が2倍以上違う'
REASON_MAX_VS_MODE = '最高単価が最多単価の3倍以上'
MAX_MODE_RATIO = 3
CUSTOMER_SPREAD_RATIO = 2

SEED_CODE_MAPS = (('㈱道新総合印刷', '2596145', '9008-1'),)
LAST_RUN_KEY = 'price_init_last'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS price_base (
  id INTEGER PRIMARY KEY,
  supplier_id INTEGER NOT NULL DEFAULT 0,
  product_id INTEGER NOT NULL REFERENCES products(id),
  size_label TEXT NOT NULL,
  price INTEGER,
  status TEXT NOT NULL DEFAULT 'ok',
  reason TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',
  updated_at TEXT NOT NULL,
  UNIQUE(supplier_id, product_id, size_label)
);
CREATE INDEX IF NOT EXISTS price_base_status ON price_base(status);
CREATE TABLE IF NOT EXISTS price_special (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  supplier_id INTEGER NOT NULL DEFAULT 0,
  product_id INTEGER NOT NULL REFERENCES products(id),
  size_label TEXT,
  price INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS price_special_key
  ON price_special(customer_id, supplier_id, product_id, COALESCE(size_label, ''));
CREATE TABLE IF NOT EXISTS price_code_map (
  id INTEGER PRIMARY KEY,
  customer_name TEXT NOT NULL DEFAULT '',
  stock_code TEXT NOT NULL,
  real_code TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(customer_name, stock_code)
);
'''


def migrate(c):
    """既存DBにも冪等に追加。初期在庫の再登録は不要。"""
    c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM settings WHERE key='price_code_map_seeded'").fetchone():
        t = stock.now()
        for cust, code, real in SEED_CODE_MAPS:
            c.execute(
                'INSERT OR IGNORE INTO price_code_map(customer_name,stock_code,real_code,created_at,updated_at) VALUES(?,?,?,?,?)',
                (cust, code, real, t, t),
            )
        c.execute("INSERT INTO settings(key,value) VALUES('price_code_map_seeded','1')")


def needs_migration(c):
    return not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_base'").fetchone()


# ---------- 正規化 ----------

def norm(value):
    """NFKC・空白除去。Excelの数値 105.0 は '105'。"""
    if value is None:
        return ''
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return f'{value.year}-{value.month:02d}'
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)))


_PAREN_TAIL = re.compile(r'[(（][^()（）]*[)）]$')


def strip_paren_memo(code):
    prev = None
    while code and code != prev:
        prev = code
        code = _PAREN_TAIL.sub('', code)
    return code


def serial_to_ym(code):
    """Excel日付シリアル（44927→'2023-01'）。範囲外は None。"""
    if not re.fullmatch(r'\d{5}', code or ''):
        return None
    n = int(code)
    if not 30000 <= n <= 60000:
        return None
    d = datetime.date(1899, 12, 30) + datetime.timedelta(days=n)
    return f'{d.year}-{d.month:02d}'


def _price(value):
    if value is None or value == '':
        return None
    try:
        v = float(str(value).replace(',', '').replace('¥', '').strip())
    except ValueError:
        return None
    return int(round(v))


# ---------- 参照（特値 → 基準単価） ----------

def lookup(c, *, customer_id, product_id, size_label, supplier_id=None):
    """顧客の特値があれば特値、なければ基準単価。納品確認の初期値用。"""
    size = stock.text(size_label)
    pid = int(product_id)
    if supplier_id in (None, ''):
        row = c.execute('SELECT supplier_id FROM products WHERE id=?', (pid,)).fetchone()
        supplier_id = (row['supplier_id'] if row else None) or 0
    sid = int(supplier_id or 0)
    if customer_id not in (None, ''):
        sp = c.execute(
            '''SELECT price,size_label FROM price_special
               WHERE customer_id=? AND supplier_id=? AND product_id=? AND (size_label=? OR size_label IS NULL)
               ORDER BY size_label IS NULL LIMIT 1''',
            (int(customer_id), sid, pid, size),
        ).fetchone()
        if sp:
            return {'price': sp['price'], 'source': 'special', 'status': STATUS_OK,
                    'allSizes': sp['size_label'] is None, 'supplierId': sid}
    b = c.execute(
        'SELECT price,status,reason FROM price_base WHERE supplier_id=? AND product_id=? AND size_label=?',
        (sid, pid, size),
    ).fetchone()
    if not b:
        return {'price': None, 'source': 'none', 'status': STATUS_UNSET, 'supplierId': sid}
    return {'price': b['price'], 'source': 'base', 'status': b['status'], 'reason': b['reason'], 'supplierId': sid}


# ---------- 旧データ（ロット）の読み出し ----------

def _lot_supplier_map(c):
    """ロットID→旧CSVの仕入先ID（取込行の記録があるもの）。"""
    names = {stock.text(r['name']): r['id'] for r in c.execute('SELECT id,name FROM suppliers')}
    out = {}
    for r in c.execute("SELECT csv_supplier,lot_ids_json FROM legacy_csv_row_maps WHERE csv_supplier<>''"):
        sid = names.get(stock.text(r['csv_supplier']))
        if not sid:
            continue
        try:
            ids = json.loads(r['lot_ids_json'] or '[]')
        except ValueError:
            continue
        for lid in ids:
            out[int(lid)] = sid
    return out


def db_lots(c):
    by_lot = _lot_supplier_map(c)
    rows = c.execute(
        '''SELECT l.id,l.size_label,l.unit_cost,l.qty_remaining,l.qty_received,
                  cp.customer_id,cp.product_id,p.supplier_id,p.code,p.maker_code,p.name,
                  cu.name AS customer_name
           FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
           JOIN products p ON p.id=cp.product_id JOIN customers cu ON cu.id=cp.customer_id
           WHERE l.condition='new' '''
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            'lotId': r['id'], 'supplierId': by_lot.get(r['id']) or r['supplier_id'] or 0,
            'productId': r['product_id'], 'size': r['size_label'], 'price': r['unit_cost'] or 0,
            'stockQty': r['qty_remaining'] or 0, 'buyQty': r['qty_received'] or 0,
            'customerId': r['customer_id'], 'customerName': r['customer_name'],
            'code': r['code'] or '', 'makerCode': r['maker_code'] or '', 'name': r['name'] or '',
        })
    return out


# ---------- 棚卸Excel ----------

STOCKTAKE_HEADERS = ('コード', '顧客', '商品名', '商品コード', 'サイズ', '仕入単価', '在庫数')


def read_stocktake_xlsx(blob):
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    except Exception as e:
        raise ValueError('棚卸Excel（.xlsx）を読めませんでした。') from e
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = None
    out = []
    for i, r in enumerate(rows, start=1):
        if header is None:
            head = [norm(v) for v in r]
            if all(h in head for h in ('顧客', '商品コード', 'サイズ', '仕入単価')):
                header = {h: head.index(h) for h in head if h}
            continue
        get = lambda k: r[header[k]] if k in header and header[k] < len(r) else None
        if all(v in (None, '') for v in r):
            continue
        out.append({
            'rowNo': i, 'customer': get('顧客'), 'name': get('商品名'), 'code': get('商品コード'),
            'size': get('サイズ'), 'price': get('仕入単価'), 'qty': get('在庫数'),
        })
    wb.close()
    if header is None:
        raise ValueError('1枚目のシートに「顧客・商品コード・サイズ・仕入単価」の見出しがありません。')
    return out


def code_maps(c):
    return [dict(r) for r in c.execute(
        'SELECT id,customer_name AS customerName,stock_code AS stockCode,real_code AS realCode,note FROM price_code_map ORDER BY customer_name,stock_code')]


def save_code_map(c, body):
    t = stock.now()
    if body.get('delete'):
        c.execute('DELETE FROM price_code_map WHERE id=?', (int(body['id']),))
        return {'ok': True, 'maps': code_maps(c)}
    cust = stock.text(body.get('customerName'))
    code = norm(body.get('stockCode'))
    real = norm(body.get('realCode'))
    if not code or not real:
        raise ValueError('棚卸の品番と正しい品番を入れてください。')
    if body.get('id'):
        c.execute('UPDATE price_code_map SET customer_name=?,stock_code=?,real_code=?,note=?,updated_at=? WHERE id=?',
                  (cust, code, real, stock.text(body.get('note')), t, int(body['id'])))
    else:
        c.execute('INSERT INTO price_code_map(customer_name,stock_code,real_code,note,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                  (cust, code, real, stock.text(body.get('note')), t, t))
    return {'ok': True, 'maps': code_maps(c)}


def _match_stocktake(c, srows, lots):
    """規則a: 顧客＋商品コード＋サイズで照合。戻り: (matched, ignored)"""
    idx = collections.defaultdict(set)          # (cust, code, size) -> {(sid,pid)}
    names = {}                                  # pid -> norm name
    cust_codes = collections.defaultdict(set)   # cust -> {code}
    for l in lots:
        cu = norm(l['customerName'])
        names[l['productId']] = norm(l['name'])
        for code in {norm(l['code']), norm(l['makerCode'])} - {''}:
            idx[(cu, code, norm(l['size']))].add((l['supplierId'], l['productId']))
            cust_codes[cu].add(code)
    maps = {}
    for m in c.execute('SELECT customer_name,stock_code,real_code FROM price_code_map'):
        maps[(norm(m['customer_name']), norm(m['stock_code']))] = norm(m['real_code'])
    matched, ignored = [], []
    for r in srows:
        cu, size, raw = norm(r['customer']), norm(r['size']), norm(r['code'])
        price = _price(r['price'])
        base = {'rowNo': r['rowNo'], 'customer': stock.text(r['customer']), 'code': raw,
                'name': stock.text(r['name']), 'size': size, 'price': price}
        if not cu or not raw:
            ignored.append({**base, 'reason': '顧客か品番が空'})
            continue
        cands = []
        mapped = maps.get((cu, raw)) or maps.get(('', raw))
        if mapped:
            cands.append((mapped, '読替表'))
        cands.append((raw, ''))
        p = strip_paren_memo(raw)
        if p != raw:
            cands.append((p, '括弧メモ除去'))
        ym = serial_to_ym(p)
        if ym:
            cands.append((ym, '日付シリアル'))
        if re.fullmatch(r'\d+', p):
            for code in sorted(cust_codes.get(cu, ())):
                if code != p and re.fullmatch(r'0+\d+', code) and code.lstrip('0') == p.lstrip('0'):
                    cands.append((code, '先頭0補完'))
        hit = None
        for code, how in cands:
            keys = idx.get((cu, code, size))
            if not keys:
                continue
            if len(keys) > 1:
                byname = {k for k in keys if names.get(k[1]) == norm(r['name'])}
                if len(byname) == 1:
                    keys = byname
                else:
                    hit = ('ambiguous', code)
                    break
            hit = (next(iter(keys)), code, how)
            break
        if not hit:
            ignored.append({**base, 'reason': '一致なし'})
        elif hit[0] == 'ambiguous':
            ignored.append({**base, 'reason': '候補が複数'})
        else:
            (sid, pid), code, how = hit
            matched.append({**base, 'supplierId': sid, 'productId': pid, 'dbSize': size,
                            'matchedCode': code, 'how': how})
    return matched, ignored


def _mode_price(lots):
    w = collections.defaultdict(lambda: [0, 0, 0])
    for l in lots:
        x = w[l['price']]
        x[0] += l['stockQty']
        x[1] += l['buyQty']
        x[2] += 1
    return max(w, key=lambda p: (w[p][0], w[p][1], w[p][2], p))


def compute(c, srows):
    lots = db_lots(c)
    groups = collections.defaultdict(list)
    for l in lots:
        groups[(l['supplierId'], l['productId'], l['size'])].append(l)
    # サイズ表記ゆれ対策: 照合キーはnorm(size)、グループは実際のsize_label
    size_label = {(k[0], k[1], norm(k[2])): k[2] for k in groups}
    matched, ignored = _match_stocktake(c, srows or [], lots)
    st = collections.defaultdict(list)
    for m in matched:
        key = (m['supplierId'], m['productId'], size_label.get((m['supplierId'], m['productId'], m['dbSize']), m['dbSize']))
        st[key].append(m)
    results = {}
    for key in set(groups) | set(st):
        g = groups.get(key, [])
        db_pos = [l for l in g if l['price'] > 0]
        db_max = max((l['price'] for l in db_pos), default=None)
        st_pos = [m for m in st.get(key, []) if (m['price'] or 0) > 0]
        reasons, price, rule = [], None, ''
        if st_pos:
            rule = 'b'
            price = max(m['price'] for m in st_pos)
            if db_max is not None and db_max != price:
                reasons.append(REASON_STOCKTAKE_VS_DB)
            per_cust = collections.defaultdict(int)
            for m in st_pos:
                per_cust[norm(m['customer'])] = max(per_cust[norm(m['customer'])], m['price'])
            if len(per_cust) > 1 and max(per_cust.values()) >= CUSTOMER_SPREAD_RATIO * min(per_cust.values()):
                reasons.append(REASON_CUSTOMER_SPREAD)
        elif db_pos:
            rule = 'c'
            mode = _mode_price(db_pos)
            price = db_max
            if db_max >= MAX_MODE_RATIO * mode:
                price = mode
                reasons.append(REASON_MAX_VS_MODE)
        else:
            rule = 'd'
        status = STATUS_UNSET if price is None else STATUS_REVIEW if reasons else STATUS_OK
        results[key] = {'supplierId': key[0], 'productId': key[1], 'size': key[2], 'price': price,
                        'status': status, 'reason': '／'.join(reasons), 'rule': rule,
                        'dbMax': db_max, 'stocktakeMax': max((m['price'] for m in st_pos), default=None)}
    return results, matched, ignored


def run_initial(c, body, *, srows=None):
    """初期設定。基準単価を毎回同じルールで上書き（在庫数量は触らない）。"""
    file_name = ''
    if srows is None:
        raw = body.get('file') if isinstance(body, dict) else None
        if isinstance(raw, str) and raw.strip():
            try:
                blob = base64.b64decode(raw, validate=True)
            except Exception as e:
                raise ValueError('棚卸Excelを選んでください。') from e
            srows = read_stocktake_xlsx(blob)
            file_name = stock.text(body.get('name'))
        else:
            srows = []
    results, matched, ignored = compute(c, srows)
    t = stock.now()
    for r in results.values():
        c.execute(
            '''INSERT INTO price_base(supplier_id,product_id,size_label,price,status,reason,source,updated_at)
               VALUES(?,?,?,?,?,?,'initial',?)
               ON CONFLICT(supplier_id,product_id,size_label) DO UPDATE SET
                 price=excluded.price,status=excluded.status,reason=excluded.reason,
                 source='initial',updated_at=excluded.updated_at''',
            (r['supplierId'], r['productId'], r['size'], r['price'], r['status'], r['reason'], t),
        )
    by_reason = collections.Counter()
    for r in results.values():
        for reason in filter(None, r['reason'].split('／')):
            by_reason[reason] += 1
    summary = {
        'groups': len(results),
        'set': sum(r['price'] is not None for r in results.values()),
        'fromStocktake': sum(r['rule'] == 'b' for r in results.values()),
        'fromDb': sum(r['rule'] == 'c' for r in results.values()),
        'review': sum(r['status'] == STATUS_REVIEW for r in results.values()),
        'reviewByReason': dict(by_reason),
        'unset': sum(r['status'] == STATUS_UNSET for r in results.values()),
        'stocktakeRows': len(srows),
        'matched': len(matched),
        'matchedBy': dict(collections.Counter(m['how'] or '一致' for m in matched)),
        'ignored': len(ignored),
        'ignoredByReason': dict(collections.Counter(i['reason'] for i in ignored)),
    }
    result = {'ok': True, 'fileName': file_name, 'ranAt': t, 'summary': summary, 'ignoredRows': ignored}
    c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
              (LAST_RUN_KEY, json.dumps(result, ensure_ascii=False)))
    return result


def last_run(c):
    r = c.execute('SELECT value FROM settings WHERE key=?', (LAST_RUN_KEY,)).fetchone()
    return json.loads(r['value']) if r else None


# ---------- 一覧・手修正 ----------

def _base_rows(c, where, args):
    return [{
        'id': r['id'], 'supplierId': r['supplier_id'], 'supplierName': r['supplier_name'] or '',
        'productId': r['product_id'], 'code': r['code'] or '', 'productName': r['name'] or '',
        'color': r['color'] or '', 'size': r['size_label'], 'price': r['price'], 'status': r['status'],
        'statusLabel': STATUS_LABELS.get(r['status'], ''), 'reason': r['reason'], 'source': r['source'],
    } for r in c.execute(
        f'''SELECT b.*,s.name AS supplier_name,p.code,p.name,p.color
            FROM price_base b JOIN products p ON p.id=b.product_id
            LEFT JOIN suppliers s ON s.id=b.supplier_id
            WHERE {where}
            ORDER BY s.name,p.code,p.name,COALESCE((SELECT ps.sort_order FROM product_sizes ps
                     WHERE ps.product_id=b.product_id AND ps.label=b.size_label),999),b.size_label''', args)]


def review_list(c):
    return {'items': _base_rows(c, "b.status IN ('review','unset')", ()), 'last': _last_summary(c)}


def _last_summary(c):
    r = last_run(c)
    return r and {'ranAt': r.get('ranAt'), 'fileName': r.get('fileName'), 'summary': r.get('summary')}


def product_prices(c, product_id):
    pid = int(product_id)
    specials = [dict(r) for r in c.execute(
        '''SELECT sp.id,sp.customer_id AS customerId,cu.name AS customerName,sp.supplier_id AS supplierId,
                  s.name AS supplierName,sp.size_label AS size,sp.price,sp.note
           FROM price_special sp JOIN customers cu ON cu.id=sp.customer_id
           LEFT JOIN suppliers s ON s.id=sp.supplier_id
           WHERE sp.product_id=? ORDER BY cu.name,s.name,sp.size_label IS NOT NULL,sp.size_label''', (pid,))]
    return {'productId': pid, 'base': _base_rows(c, 'b.product_id=?', (pid,)), 'specials': specials}


def save_base(c, body):
    """手で単価を直す。要確認・未設定は解除。"""
    price = stock.parse_money(body.get('price'), '現単価')
    t = stock.now()
    if body.get('id'):
        cur = c.execute('UPDATE price_base SET price=?,status=?,reason=?,source=?,updated_at=? WHERE id=?',
                        (price, STATUS_OK, '', 'manual', t, int(body['id'])))
        if not cur.rowcount:
            raise ValueError('対象の現単価が見つかりません。')
    else:
        pid = int(body.get('productId'))
        size = stock.text(body.get('size'))
        if not size:
            raise ValueError('サイズを選んでください。')
        sid = int(body.get('supplierId') or 0)
        c.execute(
            '''INSERT INTO price_base(supplier_id,product_id,size_label,price,status,reason,source,updated_at)
               VALUES(?,?,?,?,'ok','','manual',?)
               ON CONFLICT(supplier_id,product_id,size_label) DO UPDATE SET
                 price=excluded.price,status='ok',reason='',source='manual',updated_at=excluded.updated_at''',
            (sid, pid, size, price, t))
    return {'ok': True}


def save_special(c, body):
    """特値の追加・修正・削除（手入力のみ）。"""
    if body.get('delete'):
        c.execute('DELETE FROM price_special WHERE id=?', (int(body['id']),))
        return {'ok': True}
    price = stock.parse_money(body.get('price'), '特値')
    size = stock.text(body.get('size')) or None
    t = stock.now()
    note = stock.text(body.get('note'))
    if body.get('id'):
        c.execute('UPDATE price_special SET price=?,size_label=?,note=?,updated_at=? WHERE id=?',
                  (price, size, note, t, int(body['id'])))
        return {'ok': True}
    cid = body.get('customerId')
    if not cid:
        raise ValueError('顧客を選んでください。')
    c.execute(
        '''INSERT INTO price_special(customer_id,supplier_id,product_id,size_label,price,note,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?)''',
        (int(cid), int(body.get('supplierId') or 0), int(body['productId']), size, price, note, t, t))
    return {'ok': True}
