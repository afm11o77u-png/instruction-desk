"""棚卸（顧客×月の棚卸表）。作成時に顧客の在庫をスナップショットし、実棚を入力、
「実棚で在庫を上書き」で stock.register_stocktake を使って差分だけ棚卸調整する。"""
import json
import uuid

import stock


def schema(c):
    # 旧 customers.stocktake_target 列は使わない（既存DBに残っていても無害。データは消さない）。
    c.execute('''CREATE TABLE IF NOT EXISTS stocktakes (
      id INTEGER PRIMARY KEY,
      customer_id INTEGER NOT NULL REFERENCES customers(id),
      month TEXT NOT NULL,
      created_at TEXT NOT NULL,
      created_by TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL,
      applied_at TEXT,
      applied_by TEXT NOT NULL DEFAULT '',
      UNIQUE(customer_id, month)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS stocktake_rows (
      id INTEGER PRIMARY KEY,
      stocktake_id INTEGER NOT NULL REFERENCES stocktakes(id),
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
      applied_batch_id INTEGER REFERENCES move_batches(id),
      sort_no INTEGER NOT NULL DEFAULT 0
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS stocktake_rows_st ON stocktake_rows(stocktake_id, sort_no)')


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


def _row_payload(r):
    lots = [tuple(x) for x in json.loads(r['lots_json'] or '[]')]
    unit, mixed_pending = _unit(lots)
    costs = {cost for qty, cost in lots if qty > 0 and cost is not None}
    book_amount = _amount(lots)
    actual = r['actual_qty']
    out = {
        'id': r['id'], 'officeCode': r['office_code'], 'customerName': r['customer_name'],
        'productName': r['product_name'], 'productCode': r['product_code'], 'size': r['size_label'],
        'locationLabel': r['location_label'], 'condition': r['condition'],
        'conditionLabel': stock.condition_label(r['condition']),
        'unitCost': unit, 'unitMixed': unit is None and len(costs) > 1 and not mixed_pending,
        'bookQty': r['book_qty'], 'bookAmount': book_amount,
        'actualQty': actual, 'diffQty': None, 'actualAmount': None, 'diffAmount': None,
        'incPrice': r['inc_price'], 'applied': r['applied_batch_id'] is not None,
    }
    if actual is not None:
        out['diffQty'] = actual - r['book_qty']
        out['actualAmount'] = _amount(_after_lots(lots, r['book_qty'], actual, r['inc_price']))
        if out['actualAmount'] is not None and book_amount is not None:
            out['diffAmount'] = out['actualAmount'] - book_amount
    return out


def get(c, sid):
    st = c.execute('''SELECT s.*, cu.name AS customer_name FROM stocktakes s
                      JOIN customers cu ON cu.id=s.customer_id WHERE s.id=?''', (sid,)).fetchone()
    if not st:
        raise ValueError('棚卸が見つかりません。')
    rows = [_row_payload(r) for r in c.execute(
        'SELECT * FROM stocktake_rows WHERE stocktake_id=? ORDER BY sort_no, id', (sid,))]
    def total(key):
        vals = [r[key] for r in rows]
        return None if any(v is None for v in vals) else sum(vals)
    entered = [r for r in rows if r['actualQty'] is not None]
    return {
        'id': st['id'], 'customerId': st['customer_id'], 'customerName': st['customer_name'],
        'month': st['month'], 'createdAt': st['created_at'], 'createdBy': st['created_by'],
        'appliedAt': st['applied_at'], 'appliedBy': st['applied_by'], 'applied': bool(st['applied_at']),
        'rows': rows,
        'totals': {
            'bookQty': sum(r['bookQty'] for r in rows), 'bookAmount': total('bookAmount'),
            'actualQty': sum(r['actualQty'] for r in entered) if entered else None,
            'actualAmount': None if len(entered) != len(rows) else total('actualAmount'),
        },
    }


def list_stocktakes(c):
    out = []
    for r in c.execute('''SELECT s.id, s.month, s.customer_id, s.applied_at, s.created_at, cu.name AS customer_name,
                                 (SELECT COUNT(*) FROM stocktake_rows x WHERE x.stocktake_id=s.id) AS n,
                                 (SELECT COUNT(*) FROM stocktake_rows x WHERE x.stocktake_id=s.id AND x.actual_qty IS NOT NULL) AS entered
                          FROM stocktakes s JOIN customers cu ON cu.id=s.customer_id
                          ORDER BY s.month DESC, cu.name'''):
        out.append({'id': r['id'], 'month': r['month'], 'customerId': r['customer_id'],
                    'customerName': r['customer_name'], 'appliedAt': r['applied_at'],
                    'createdAt': r['created_at'], 'rowCount': r['n'], 'enteredCount': r['entered']})
    customers = [{'id': r['id'], 'name': r['name'], 'kana': r['kana'] or ''} for r in c.execute(
        'SELECT id, name, kana FROM customers WHERE active=1 ORDER BY name')]
    customers.sort(key=lambda x: stock.customer_sort_key(x['name']))
    return {'stocktakes': out, 'customers': customers}


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


def create(c, body):
    stock._require_write(body)
    cid = body.get('customerId')
    month = _month(body.get('month'))
    cu = c.execute('SELECT * FROM customers WHERE id=?', (cid,)).fetchone() if isinstance(cid, int) else None
    if not cu:
        raise ValueError('顧客を選んでください。')
    existing = c.execute('SELECT id FROM stocktakes WHERE customer_id=? AND month=?', (cid, month)).fetchone()
    if existing:
        return {**get(c, existing['id']), 'already': True}
    t = stock.stamp()
    actor = stock._actor_name(body)
    c.execute('INSERT INTO stocktakes(customer_id,month,created_at,created_by,updated_at) VALUES(?,?,?,?,?)',
              (cid, month, t, actor, t))
    sid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    lots = c.execute('''SELECT l.*, p.code, p.name AS product_name, p.color, cp.embroidery
                        FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
                        JOIN products p ON p.id=cp.product_id
                        WHERE cp.customer_id=? AND l.qty_remaining<>0
                        ORDER BY p.code, p.id, cp.embroidery, l.size_label, l.location_id, l.condition, l.id''',
                     (cid,)).fetchall()
    groups = {}
    for lot in lots:
        key = (lot['customer_product_id'], lot['size_label'], lot['location_id'], lot['condition'])
        groups.setdefault(key, []).append(lot)
    no = 0
    for key, glots in groups.items():
        cp_id, size, loc_id, cond = key
        ordered = stock.issue_sequence([l for l in glots if l['qty_remaining'] > 0])
        ordered += [l for l in glots if l['qty_remaining'] <= 0]
        snap = [[l['qty_remaining'], None if l['cost_pending'] else l['unit_cost']] for l in ordered]
        book = sum(l['qty_remaining'] for l in glots)
        if book == 0 and all(q == 0 for q, _ in snap):
            continue
        first = glots[0]
        name = first['product_name']
        if first['color']:
            name += ' ' + first['color']
        if first['embroidery']:
            name += f"（{stock.embroidery_label(first['embroidery'])}）"
        loc = c.execute('SELECT * FROM locations WHERE id=?', (loc_id,)).fetchone()
        inc, src = _inc_price(c, cp_id, size, loc_id, cond)
        no += 1
        c.execute('''INSERT INTO stocktake_rows(stocktake_id,customer_product_id,size_label,location_id,condition,
                     location_label,office_code,customer_name,product_name,product_code,book_qty,lots_json,
                     inc_price,inc_source,sort_no) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (sid, cp_id, size, loc_id, cond, stock.location_label(c, loc) if loc else '',
                   (cu['office_code'] or ''), cu['name'], name, first['code'] or '', book,
                   json.dumps(snap), inc, src, no))
    return get(c, sid)


def save(c, body):
    stock._require_write(body)
    sid = body.get('id')
    st = c.execute('SELECT * FROM stocktakes WHERE id=?', (sid,)).fetchone() if isinstance(sid, int) else None
    if not st:
        raise ValueError('棚卸が見つかりません。')
    if st['applied_at']:
        raise ValueError('在庫に反映済みのため変更できません。')
    rows = body.get('rows')
    if not isinstance(rows, list):
        raise ValueError('実棚を入力してください。')
    for r in rows:
        rid = r.get('id') if isinstance(r, dict) else None
        raw = r.get('actualQty') if isinstance(r, dict) else None
        if raw is None or (isinstance(raw, str) and raw.strip() == ''):
            val = None
        else:
            val = stock.parse_qty(raw, '実棚点数', allow_zero=True)
        c.execute('UPDATE stocktake_rows SET actual_qty=? WHERE id=? AND stocktake_id=?', (val, rid, sid))
    c.execute('UPDATE stocktakes SET updated_at=? WHERE id=?', (stock.stamp(), sid))
    return get(c, sid)


def apply(c, body):
    """実棚で在庫を上書き。実棚入力済みで現在庫と違う行だけ、register_stocktake で棚卸調整を記録する。"""
    stock._require_write(body)
    if body.get('confirm') not in (True, 1, '1'):
        raise ValueError('確認してから実行してください。')
    sid = body.get('id')
    st = c.execute('SELECT * FROM stocktakes WHERE id=?', (sid,)).fetchone() if isinstance(sid, int) else None
    if not st:
        raise ValueError('棚卸が見つかりません。')
    if st['applied_at']:
        return {**get(c, sid), 'already': True, 'adjusted': 0}
    t = stock.stamp()
    adjusted = 0
    for r in c.execute('SELECT * FROM stocktake_rows WHERE stocktake_id=? AND actual_qty IS NOT NULL ORDER BY sort_no, id',
                       (sid,)).fetchall():
        cur = stock.current_qty_for_target(c, r['customer_product_id'], r['size_label'], r['location_id'], r['condition'])
        if cur == r['actual_qty']:
            continue
        res = stock.register_stocktake(c, {
            '_actor': body.get('_actor'), '_canWrite': body.get('_canWrite'),
            'token': f'stocktake-{sid}-{r["id"]}-{uuid.uuid4().hex[:8]}',
            'customerProductId': r['customer_product_id'], 'size': r['size_label'],
            'locationId': r['location_id'], 'condition': r['condition'],
            'actualQty': r['actual_qty'], 'businessOn': t[:10],
            'unitCost': r['inc_price'],
            'note': f"棚卸調整（{st['month']} 棚卸#{sid}・{cur}→{r['actual_qty']}）",
        })
        c.execute('UPDATE stocktake_rows SET applied_batch_id=? WHERE id=?', (res.get('batchId'), r['id']))
        adjusted += 1
    c.execute('UPDATE stocktakes SET applied_at=?, applied_by=?, updated_at=? WHERE id=?',
              (t, stock._actor_name(body), t, sid))
    return {**get(c, sid), 'adjusted': adjusted}
