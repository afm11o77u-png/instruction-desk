"""月末締め: その瞬間の全在庫ロットを月ごとに保存（読み取り専用の記録）。

棚卸の基準（在庫数・在庫金額）と、実棚反映の差分計算に使う。
"""
from __future__ import annotations

import io

import stock


def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS month_closes (
      id INTEGER PRIMARY KEY,
      month TEXT NOT NULL UNIQUE,
      closed_at TEXT NOT NULL,
      closed_by TEXT NOT NULL DEFAULT ''
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS month_close_lots (
      id INTEGER PRIMARY KEY,
      close_id INTEGER NOT NULL REFERENCES month_closes(id) ON DELETE CASCADE,
      customer_id INTEGER NOT NULL,
      customer_product_id INTEGER NOT NULL,
      product_id INTEGER NOT NULL,
      size_label TEXT NOT NULL,
      location_id INTEGER NOT NULL,
      condition TEXT NOT NULL,
      unit_cost INTEGER,
      cost_pending INTEGER NOT NULL DEFAULT 0,
      qty INTEGER NOT NULL,
      amount INTEGER,
      office_code TEXT NOT NULL DEFAULT '',
      customer_name TEXT NOT NULL DEFAULT '',
      product_code TEXT NOT NULL DEFAULT '',
      product_name TEXT NOT NULL DEFAULT '',
      color TEXT NOT NULL DEFAULT '',
      embroidery TEXT NOT NULL DEFAULT '',
      supplier_name TEXT NOT NULL DEFAULT '',
      location_label TEXT NOT NULL DEFAULT '',
      lot_id INTEGER
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS month_close_lots_close ON month_close_lots(close_id)')
    c.execute('CREATE INDEX IF NOT EXISTS month_close_lots_cu ON month_close_lots(close_id, customer_id)')


def _month(v):
    s = stock.text(v)
    if len(s) != 7 or s[4] != '-' or not (s[:4] + s[5:]).isdigit() or not 1 <= int(s[5:]) <= 12:
        raise ValueError('年月を選んでください。')
    return s


def get(c, month):
    month = _month(month)
    h = c.execute('SELECT * FROM month_closes WHERE month=?', (month,)).fetchone()
    if not h:
        return {'month': month, 'closed': False, 'header': None, 'rows': [], 'rowCount': 0, 'totalQty': 0, 'totalAmount': None}
    rows = [_row(r) for r in c.execute(
        'SELECT * FROM month_close_lots WHERE close_id=? ORDER BY customer_name, product_code, size_label, location_label, condition, id',
        (h['id'],)).fetchall()]
    total_qty = sum(r['qty'] for r in rows)
    amounts = [r['amount'] for r in rows]
    total_amount = None if any(a is None for a in amounts) else sum(amounts)
    return {
        'month': month, 'closed': True,
        'header': {'id': h['id'], 'month': h['month'], 'closedAt': h['closed_at'], 'closedBy': h['closed_by']},
        'rows': rows, 'rowCount': len(rows), 'totalQty': total_qty, 'totalAmount': total_amount,
    }


def list_closes(c):
    rows = c.execute('SELECT * FROM month_closes ORDER BY month DESC').fetchall()
    out = []
    for h in rows:
        n, qty = c.execute(
            'SELECT COUNT(*), COALESCE(SUM(qty),0) FROM month_close_lots WHERE close_id=?', (h['id'],)
        ).fetchone()
        out.append({'id': h['id'], 'month': h['month'], 'closedAt': h['closed_at'], 'closedBy': h['closed_by'],
                    'rowCount': n, 'totalQty': qty})
    return {'months': out}


def _row(r):
    return {
        'id': r['id'], 'customerId': r['customer_id'], 'customerProductId': r['customer_product_id'],
        'productId': r['product_id'], 'size': r['size_label'], 'locationId': r['location_id'],
        'condition': r['condition'], 'conditionLabel': stock.condition_label(r['condition']),
        'unitCost': None if r['cost_pending'] else r['unit_cost'],
        'costPending': bool(r['cost_pending']), 'qty': r['qty'], 'amount': r['amount'],
        'officeCode': r['office_code'], 'customerName': r['customer_name'],
        'productCode': r['product_code'], 'productName': r['product_name'], 'color': r['color'],
        'embroidery': r['embroidery'], 'embroideryLabel': stock.embroidery_label(r['embroidery']),
        'supplierName': r['supplier_name'], 'locationLabel': r['location_label'], 'lotId': r['lot_id'],
    }


def _snapshot_lots(c):
    """その瞬間の全在庫ロット（残数≠0）。"""
    return c.execute('''
        SELECT l.id AS lot_id, l.customer_product_id, l.size_label, l.location_id, l.condition,
               l.unit_cost, COALESCE(l.cost_pending,0) AS cost_pending, l.qty_remaining AS qty,
               cp.customer_id, cp.embroidery, p.id AS product_id, p.code AS product_code,
               p.name AS product_name, COALESCE(p.color,'') AS color,
               COALESCE(su.name,'') AS supplier_name,
               COALESCE(cu.name,'') AS customer_name, COALESCE(cu.office_code,'') AS office_code
        FROM lots l
        JOIN customer_products cp ON cp.id=l.customer_product_id
        JOIN products p ON p.id=cp.product_id
        JOIN customers cu ON cu.id=cp.customer_id
        LEFT JOIN suppliers su ON su.id=p.supplier_id
        WHERE l.qty_remaining<>0
        ORDER BY cu.name, p.code, l.size_label, l.location_id, l.condition, l.id
    ''').fetchall()


def close_month(c, body):
    """締め実行。同月が既にあれば confirm=true で上書き。"""
    stock._require_write(body)
    month = _month(body.get('month'))
    existing = c.execute('SELECT * FROM month_closes WHERE month=?', (month,)).fetchone()
    if existing and body.get('confirm') not in (True, 1, '1'):
        raise ValueError('この月は締め済みです。上書きする場合は確認してください。')
    t = stock.stamp()
    actor = stock._actor_name(body)
    if existing:
        c.execute('DELETE FROM month_close_lots WHERE close_id=?', (existing['id'],))
        c.execute('UPDATE month_closes SET closed_at=?, closed_by=? WHERE id=?', (t, actor, existing['id']))
        close_id = existing['id']
    else:
        c.execute('INSERT INTO month_closes(month, closed_at, closed_by) VALUES(?,?,?)', (month, t, actor))
        close_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    lots = _snapshot_lots(c)
    for lot in lots:
        pending = 1 if lot['cost_pending'] else 0
        unit = None if pending else lot['unit_cost']
        qty = int(lot['qty'])
        amount = None if pending or unit is None else qty * unit
        loc = c.execute('SELECT * FROM locations WHERE id=?', (lot['location_id'],)).fetchone()
        loc_label = stock.location_label(c, loc) if loc else ''
        c.execute('''INSERT INTO month_close_lots(
            close_id, customer_id, customer_product_id, product_id, size_label, location_id, condition,
            unit_cost, cost_pending, qty, amount, office_code, customer_name, product_code, product_name,
            color, embroidery, supplier_name, location_label, lot_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (close_id, lot['customer_id'], lot['customer_product_id'], lot['product_id'],
                   lot['size_label'], lot['location_id'], lot['condition'],
                   0 if unit is None else unit, pending, qty, amount,
                   lot['office_code'], lot['customer_name'], lot['product_code'] or '',
                   lot['product_name'], lot['color'], lot['embroidery'] or '',
                   lot['supplier_name'], loc_label, lot['lot_id']))
    return get(c, month)


def lots_for_customer(c, month, customer_id):
    """棚卸表用: 締め明細のうち指定顧客分。無ければ None。"""
    month = _month(month)
    h = c.execute('SELECT id FROM month_closes WHERE month=?', (month,)).fetchone()
    if not h:
        return None
    return c.execute('SELECT * FROM month_close_lots WHERE close_id=? AND customer_id=? ORDER BY id',
                     (h['id'], customer_id)).fetchall()


def export_xlsx(c, month):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side

    data = get(c, month)
    if not data['closed']:
        raise ValueError('この月はまだ締めていません。')
    wb = Workbook()
    ws = wb.active
    ws.title = '月末締め'
    thin = Border(*(Side(style='thin') for _ in range(4)))
    headers = ['営業所コード', '顧客', '仕入先', '品番', '商品名', '色', '加工', 'サイズ', '場所', '状態',
               '単価', '数量', '金額']
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.border = thin
    for r in data['rows']:
        ws.append([
            r['officeCode'], r['customerName'], r['supplierName'], r['productCode'], r['productName'],
            r['color'], r['embroideryLabel'], r['size'], r['locationLabel'], r['conditionLabel'],
            '原価未確定' if r['costPending'] else r['unitCost'], r['qty'],
            '原価未確定' if r['amount'] is None else r['amount'],
        ])
        for cell in ws[ws.max_row]:
            cell.border = thin
            cell.alignment = Alignment(wrap_text=False)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue(), f'月末締め_{data["month"]}.xlsx'


def require_closed(c, month):
    month = _month(month)
    if not c.execute('SELECT id FROM month_closes WHERE month=?', (month,)).fetchone():
        raise ValueError('先に締めてください。')
    return month
