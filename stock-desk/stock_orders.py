"""発注（purchase orders）: save / issue / PDF preview / receive into inbound."""
from __future__ import annotations

import html
from datetime import date

import stock

STATUS_UNORDERED = 'unordered'
STATUS_ORDERED = 'ordered'
STATUS_PARTIAL = 'partial'
STATUS_COMPLETE = 'complete'
STATUS_CANCELLED = 'cancelled'

STATUS_LABELS = {
    STATUS_UNORDERED: '未発注',
    STATUS_ORDERED: '発注済み',
    STATUS_PARTIAL: '一部入荷',
    STATUS_COMPLETE: '入荷完了',
    STATUS_CANCELLED: '取消',
}

COMPANY_KEYS = (
    ('company_name', 'companyName', '会社名'),
    ('company_branch', 'companyBranch', '支店・部署'),
    ('company_address', 'companyAddress', '住所'),
    ('company_tel', 'companyTel', '電話'),
    ('company_fax', 'companyFax', 'FAX'),
)

# Seeded into settings once when missing. Reference PDF order-20261010.pdf was not in
# the Project store; defaults use the in-app brand only (editable via company-profile API).
DEFAULT_COMPANY = {
    'company_name': 'エンパイアー',
    'company_branch': '',
    'company_address': '',
    'company_tel': '',
    'company_fax': '',
}

PO_SCHEMA = """
CREATE TABLE IF NOT EXISTS purchase_orders (
  id INTEGER PRIMARY KEY,
  number TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  order_date TEXT NOT NULL,
  ordered_at TEXT,
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  supplier_name TEXT NOT NULL,
  supplier_contact TEXT NOT NULL DEFAULT '',
  supplier_fax TEXT NOT NULL DEFAULT '',
  supplier_address TEXT NOT NULL DEFAULT '',
  supplier_phone TEXT NOT NULL DEFAULT '',
  customer_name TEXT NOT NULL,
  customer_address TEXT NOT NULL DEFAULT '',
  orderer_name TEXT NOT NULL DEFAULT '',
  orderer_user_id INTEGER,
  remarks TEXT NOT NULL DEFAULT '',
  client_token TEXT NOT NULL UNIQUE,
  needs_resend INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_order_lines (
  id INTEGER PRIMARY KEY,
  purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
  line_no INTEGER NOT NULL,
  customer_product_id INTEGER NOT NULL REFERENCES customer_products(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  product_code TEXT NOT NULL,
  product_name TEXT NOT NULL,
  product_color TEXT NOT NULL DEFAULT '',
  embroidery TEXT NOT NULL DEFAULT '',
  size_label TEXT NOT NULL,
  qty_ordered INTEGER NOT NULL,
  qty_received INTEGER NOT NULL DEFAULT 0,
  line_note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(purchase_order_id, line_no)
);
CREATE TABLE IF NOT EXISTS purchase_order_events (
  id INTEGER PRIMARY KEY,
  purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
  kind TEXT NOT NULL,
  actor_name TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_order_receipts (
  id INTEGER PRIMARY KEY,
  purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
  purchase_order_line_id INTEGER NOT NULL REFERENCES purchase_order_lines(id),
  move_batch_id INTEGER NOT NULL REFERENCES move_batches(id),
  lot_id INTEGER NOT NULL REFERENCES lots(id),
  qty INTEGER NOT NULL,
  unit_cost INTEGER NOT NULL,
  received_on TEXT NOT NULL,
  location_id INTEGER NOT NULL,
  client_token TEXT NOT NULL UNIQUE,
  actor_name TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS po_list_idx ON purchase_orders(order_date DESC, id DESC);
CREATE INDEX IF NOT EXISTS po_line_po_idx ON purchase_order_lines(purchase_order_id);
CREATE INDEX IF NOT EXISTS po_receipt_line_idx ON purchase_order_receipts(purchase_order_line_id);
"""


def migrate_purchase_orders(c):
    """Additive migration for PO tables + optional supplier.fax."""
    c.executescript(PO_SCHEMA)
    cols = {r['name'] for r in c.execute('PRAGMA table_info(suppliers)')}
    if 'fax' not in cols:
        c.execute("ALTER TABLE suppliers ADD COLUMN fax TEXT NOT NULL DEFAULT ''")


def needs_purchase_order_migration(c):
    tables = {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'purchase_orders' not in tables:
        return True
    cols = {r['name'] for r in c.execute('PRAGMA table_info(suppliers)')}
    return 'fax' not in cols


def status_label(code):
    return STATUS_LABELS.get(code, code or '')


def company_profile(c, *, ensure=True):
    """Load company block from settings. Optionally seed defaults once if none set."""
    out = {}
    any_set = False
    for key, camel, _label in COMPANY_KEYS:
        row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        value = row['value'] if row else ''
        if value:
            any_set = True
        out[camel] = value
    if ensure and not any_set:
        for key, camel, _label in COMPANY_KEYS:
            value = DEFAULT_COMPANY.get(key, '')
            c.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            out[camel] = value
        c.execute(
            "INSERT INTO settings(key,value) VALUES('company_profile_seeded','1') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        )
    return out


def save_company_profile(c, body):
    stock._require_write(body)
    t = stock.stamp()
    for key, camel, label in COMPANY_KEYS:
        value = stock.optional(body.get(camel), label, 200 if 'address' in key or 'name' in key or 'branch' in key else 80)
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
    # touch marker
    c.execute(
        "INSERT INTO settings(key,value) VALUES('company_profile_updated',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (t,),
    )
    return company_profile(c, ensure=False)


def _next_po_number(c, created_day=None):
    """Auto unique 発注番号 from system 作成日 (not handwritten 発注日)."""
    day = (created_day or date.today().isoformat()).replace('-', '')
    if len(day) != 8 or not day.isdigit():
        day = date.today().isoformat().replace('-', '')
    prefix = f'PO-{day}-'
    row = c.execute(
        "SELECT number FROM purchase_orders WHERE number LIKE ? ORDER BY number DESC LIMIT 1",
        (prefix + '%',),
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(str(row['number'])[len(prefix):]) + 1
        except ValueError:
            seq = 1
    for _ in range(1000):
        number = f'{prefix}{seq:04d}'
        if not c.execute('SELECT 1 FROM purchase_orders WHERE number=?', (number,)).fetchone():
            return number
        seq += 1
    raise ValueError('発注番号を採番できませんでした。')


def _add_event(c, po_id, kind, actor, detail=''):
    c.execute(
        'INSERT INTO purchase_order_events(purchase_order_id,kind,actor_name,detail,at) VALUES(?,?,?,?,?)',
        (po_id, kind, actor or '', detail or '', stock.stamp()),
    )


def _normalize_lines(c, body, customer_id, supplier_id):
    """Expand product size matrices into size lines. Does not mutate product.supplier_id."""
    raw = body.get('lines') or body.get('products') or []
    if not isinstance(raw, list) or not raw:
        raise ValueError('発注明細を1行以上入力してください。')
    lines = []
    line_no = 0
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('発注明細の形式が不正です。')
        cp_id = item.get('customerProductId')
        if not isinstance(cp_id, int):
            raise ValueError('商品の取扱を選んでください。')
        cp = c.execute(
            '''SELECT cp.*, p.id AS product_id, p.code, p.name AS product_name, p.color,
                      p.supplier_id, p.active AS product_active, cu.active AS customer_active
               FROM customer_products cp
               JOIN products p ON p.id=cp.product_id
               JOIN customers cu ON cu.id=cp.customer_id
               WHERE cp.id=?''',
            (cp_id,),
        ).fetchone()
        if not cp or not cp['active'] or not cp['product_active'] or not cp['customer_active']:
            raise ValueError('使用中の顧客・商品・取扱を選んでください。')
        if cp['customer_id'] != customer_id:
            raise ValueError('発注の対象顧客と、明細の取扱顧客が一致しません。')
        if not stock.product_has_supplier(c, cp['product_id'], supplier_id):
            raise ValueError('選択した仕入先の商品だけを発注できます（商品マスタの通常仕入先は変更しません）。')
        sizes = item.get('sizes') or item.get('qtyBySize') or {}
        size_rows = []
        if isinstance(sizes, dict):
            for label, qty in sizes.items():
                if qty in (None, '', 0, '0'):
                    continue
                q = stock.parse_qty(qty, f'サイズ{label}の数量')
                size_rows.append((stock.need(label, 'サイズ', 40), q))
        elif isinstance(item.get('size'), str) and item.get('qty') not in (None, '', 0, '0'):
            size_rows.append((
                stock.resolve_size_label(c, cp['product_id'], item.get('size')),
                stock.parse_qty(item.get('qty'), '数量'),
            ))
        if not size_rows:
            raise ValueError('サイズごとの数量を、1以上の整数で入力してください（空欄・0は含めません）。')
        registered = {
            r['label']
            for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (cp['product_id'],))
        }
        note = stock.optional(item.get('note') or item.get('lineNote'), '明細備考', 200)
        for label, qty in size_rows:
            if label not in registered:
                raise ValueError(f'登録済みのサイズから選んでください（{label}）。')
            line_no += 1
            lines.append({
                'lineNo': line_no,
                'customerProductId': cp['id'],
                'productId': cp['product_id'],
                'productCode': cp['code'],
                'productName': cp['product_name'],
                'productColor': cp['color'] or '',
                'embroidery': cp['embroidery'] or '',
                'size': label,
                'qtyOrdered': qty,
                'lineNote': note,
            })
    if not lines:
        raise ValueError('発注明細を1行以上入力してください。')
    return lines


def product_candidates(c, args=None):
    """Customer's active handling ∩ products whose usual supplier is the PO supplier."""
    args = args or {}
    try:
        customer_id = int(args.get('customerId'))
        supplier_id = int(args.get('supplierId'))
    except (TypeError, ValueError):
        raise ValueError('顧客と仕入先を選んでください。')
    q = stock.text(args.get('q')).lower()
    rows = []
    for r in c.execute(
        '''SELECT cp.id AS customer_product_id, cp.embroidery, cp.is_standard,
                  p.id AS product_id, p.code, p.name, p.color, p.supplier_id,
                  s.name AS supplier_name
           FROM customer_products cp
           JOIN products p ON p.id=cp.product_id
           JOIN customers cu ON cu.id=cp.customer_id
           LEFT JOIN suppliers s ON s.id=p.supplier_id
           WHERE cp.customer_id=? AND cp.active=1 AND p.active=1 AND cu.active=1
             AND p.merged_into_id IS NULL
             AND (
               p.supplier_id=?
               OR EXISTS (
                 SELECT 1 FROM product_suppliers ps
                 WHERE ps.product_id=p.id AND ps.supplier_id=?
               )
             )
           ORDER BY p.code, p.id, cp.id''',
        (customer_id, supplier_id, supplier_id),
    ):
        sizes = [
            x['label']
            for x in c.execute(
                'SELECT label FROM product_sizes WHERE product_id=? ORDER BY sort_order,id',
                (r['product_id'],),
            )
        ]
        item = {
            'customerProductId': r['customer_product_id'],
            'productId': r['product_id'],
            'code': r['code'],
            'name': r['name'],
            'color': r['color'] or '',
            'embroidery': r['embroidery'] or '',
            'embroideryLabel': stock.embroidery_label(r['embroidery']),
            'isStandard': bool(r['is_standard']),
            'supplierId': r['supplier_id'],
            'supplierName': r['supplier_name'] or '',
            'sizes': sizes,
            'label': f"{r['code']} {r['name']} {r['color'] or ''} / {stock.embroidery_label(r['embroidery'])}".strip(),
        }
        if q:
            hay = ' '.join([
                item['code'], item['name'], item['color'], item['embroideryLabel'],
                str(item['productId']), item['supplierName'],
            ]).lower()
            if q not in hay:
                continue
        rows.append(item)
    return {'customerId': customer_id, 'supplierId': supplier_id, 'products': rows}


def _line_dict(r):
    ordered = r['qty_ordered']
    received = r['qty_received']
    return {
        'id': r['id'],
        'lineNo': r['line_no'],
        'customerProductId': r['customer_product_id'],
        'productId': r['product_id'],
        'code': r['product_code'],
        'productName': r['product_name'],
        'color': r['product_color'],
        'embroidery': r['embroidery'],
        'embroideryLabel': stock.embroidery_label(r['embroidery']),
        'size': r['size_label'],
        'qtyOrdered': ordered,
        'qtyReceived': received,
        'qtyRemaining': max(0, ordered - received),
        'lineNote': r['line_note'],
    }


def _po_dict(c, po_id, *, include_events=False, include_receipts=False):
    po = c.execute('SELECT * FROM purchase_orders WHERE id=?', (po_id,)).fetchone()
    if not po:
        raise ValueError('発注書が見つかりません。')
    lines = [
        _line_dict(r)
        for r in c.execute(
            'SELECT * FROM purchase_order_lines WHERE purchase_order_id=? ORDER BY line_no,id',
            (po_id,),
        )
    ]
    body = {
        'id': po['id'],
        'number': po['number'],
        'status': po['status'],
        'statusLabel': status_label(po['status']),
        # orderDate is handwritten on paper (blank in system). createdAt is system 作成日時.
        'orderDate': po['order_date'] or '',
        'orderedAt': po['ordered_at'],
        'supplierId': po['supplier_id'],
        'customerId': po['customer_id'],
        'supplierName': po['supplier_name'],
        'supplierContact': po['supplier_contact'],
        'supplierFax': po['supplier_fax'],
        'supplierAddress': po['supplier_address'],
        'supplierPhone': po['supplier_phone'],
        'customerName': po['customer_name'],
        'customerAddress': po['customer_address'],
        'ordererName': po['orderer_name'],  # internal 作成利用者
        'createdBy': po['orderer_name'],
        'ordererUserId': po['orderer_user_id'],
        'remarks': po['remarks'],
        'needsResend': bool(po['needs_resend']),
        'createdAt': po['created_at'],
        'updatedAt': po['updated_at'],
        'lines': lines,
        'totalOrdered': sum(x['qtyOrdered'] for x in lines),
        'totalReceived': sum(x['qtyReceived'] for x in lines),
        'totalRemaining': sum(x['qtyRemaining'] for x in lines),
        'company': company_profile(c),
    }
    if include_events:
        body['events'] = [
            {
                'id': e['id'], 'kind': e['kind'], 'actorName': e['actor_name'],
                'detail': e['detail'], 'at': e['at'],
            }
            for e in c.execute(
                'SELECT * FROM purchase_order_events WHERE purchase_order_id=? ORDER BY id',
                (po_id,),
            )
        ]
    if include_receipts:
        body['receipts'] = [
            {
                'id': r['id'],
                'lineId': r['purchase_order_line_id'],
                'moveBatchId': r['move_batch_id'],
                'lotId': r['lot_id'],
                'qty': r['qty'],
                'unitCost': r['unit_cost'],
                'receivedOn': r['received_on'],
                'locationId': r['location_id'],
                'actorName': r['actor_name'],
                'createdAt': r['created_at'],
                'poNumber': po['number'],
            }
            for r in c.execute(
                'SELECT * FROM purchase_order_receipts WHERE purchase_order_id=? ORDER BY id',
                (po_id,),
            )
        ]
    return body


def list_purchase_orders(c, args=None):
    args = args or {}
    q = stock.text(args.get('q')).lower()
    status = stock.text(args.get('status'))
    sql = 'SELECT * FROM purchase_orders'
    params = []
    clauses = []
    if status:
        if status not in STATUS_LABELS:
            raise ValueError('状態の指定が不正です。')
        clauses.append('status=?')
        params.append(status)
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    sql += ' ORDER BY created_at DESC, id DESC'
    rows = []
    for r in c.execute(sql, params):
        item = {
            'id': r['id'],
            'number': r['number'],
            'orderDate': r['order_date'] or '',
            'createdAt': r['created_at'],
            'supplierName': r['supplier_name'],
            'customerName': r['customer_name'],
            'ordererName': r['orderer_name'],
            'createdBy': r['orderer_name'],
            'status': r['status'],
            'statusLabel': status_label(r['status']),
            'needsResend': bool(r['needs_resend']),
        }
        if q:
            hay = ' '.join([
                item['number'], item['supplierName'], item['customerName'],
                item['createdBy'], item['statusLabel'], item['createdAt'],
            ]).lower()
            if q not in hay:
                continue
        rows.append(item)
    return {'orders': rows, 'statuses': [{'id': k, 'label': v} for k, v in STATUS_LABELS.items()]}


def get_purchase_order(c, po_id):
    if not isinstance(po_id, int):
        raise ValueError('発注書を選んでください。')
    return _po_dict(c, po_id, include_events=True, include_receipts=True)


def _snapshot_parties(c, supplier_id, customer_id, body=None):
    """Snapshot supplier/customer names for history. Contact/FAX left blank (handwritten on paper)."""
    su = c.execute('SELECT * FROM suppliers WHERE id=?', (supplier_id,)).fetchone()
    if not su or not su['active']:
        raise ValueError('使用中の仕入先を選んでください。')
    cu = c.execute('SELECT * FROM customers WHERE id=?', (customer_id,)).fetchone()
    if not cu or not cu['active']:
        raise ValueError('使用中の顧客を選んでください。')
    return {
        'supplier_name': su['name'],
        # Handwritten on the paper form — do not auto-fill from master for print.
        'supplier_contact': '',
        'supplier_fax': '',
        'supplier_address': su['address'] or '',
        'supplier_phone': su['phone'] or '',
        'customer_name': cu['name'],
        'customer_address': cu['address'] or '',
    }


def save_purchase_order(c, body):
    """Create or update a PO. Idempotent by client_token for create."""
    stock._require_write(body)
    actor = stock._actor_name(body)
    token = stock._token(body.get('token'))
    po_id = body.get('id')
    if po_id is not None and not isinstance(po_id, int):
        raise ValueError('発注書の指定が不正です。')

    if po_id is None:
        existing = c.execute('SELECT id FROM purchase_orders WHERE client_token=?', (token,)).fetchone()
        if existing:
            return {**_po_dict(c, existing['id'], include_events=True), 'already': True}

    try:
        supplier_id = int(body.get('supplierId'))
        customer_id = int(body.get('customerId'))
    except (TypeError, ValueError):
        raise ValueError('仕入先と対象顧客を選んでください。')

    # Handwritten 発注日 on paper — not system-filled. order_date kept empty.
    # orderer_name stores 作成利用者 for history (printed 発注者 stays blank).
    order_date = ''
    creator = actor or stock.optional(body.get('ordererName'), '作成利用者', 80) or '（未設定）'
    remarks = stock.optional(body.get('remarks') or body.get('note'), '備考', 500)
    lines = _normalize_lines(c, body, customer_id, supplier_id)
    snap = _snapshot_parties(c, supplier_id, customer_id, body)
    orderer_user_id = None
    actor_obj = body.get('_actor') if isinstance(body.get('_actor'), dict) else {}
    if isinstance(actor_obj.get('id'), int):
        orderer_user_id = actor_obj['id']
    t = stock.stamp()
    created_day = t[:10]

    if po_id is None:
        number = _next_po_number(c, created_day)
        c.execute(
            '''INSERT INTO purchase_orders(
                 number,status,order_date,ordered_at,supplier_id,customer_id,
                 supplier_name,supplier_contact,supplier_fax,supplier_address,supplier_phone,
                 customer_name,customer_address,orderer_name,orderer_user_id,remarks,
                 client_token,needs_resend,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)''',
            (
                number, STATUS_UNORDERED, order_date, None, supplier_id, customer_id,
                snap['supplier_name'], snap['supplier_contact'], snap['supplier_fax'],
                snap['supplier_address'], snap['supplier_phone'],
                snap['customer_name'], snap['customer_address'], creator, orderer_user_id, remarks,
                token, t, t,
            ),
        )
        po_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        for line in lines:
            c.execute(
                '''INSERT INTO purchase_order_lines(
                     purchase_order_id,line_no,customer_product_id,product_id,
                     product_code,product_name,product_color,embroidery,size_label,
                     qty_ordered,qty_received,line_note,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,?)''',
                (
                    po_id, line['lineNo'], line['customerProductId'], line['productId'],
                    line['productCode'], line['productName'], line['productColor'],
                    line['embroidery'], line['size'], line['qtyOrdered'], line['lineNote'], t, t,
                ),
            )
        _add_event(c, po_id, 'created', actor, f'発注書 {number} を保存')
        return {**_po_dict(c, po_id, include_events=True), 'already': False}

    po = c.execute('SELECT * FROM purchase_orders WHERE id=?', (po_id,)).fetchone()
    if not po:
        raise ValueError('発注書が見つかりません。')
    if po['status'] == STATUS_CANCELLED:
        raise ValueError('取消済みの発注書は編集できません。')
    if po['status'] in (STATUS_PARTIAL, STATUS_COMPLETE):
        # Allow header/remarks edits but not line qty reductions below received.
        pass

    # Preserve received counts by matching identity keys when possible.
    old_lines = list(c.execute(
        'SELECT * FROM purchase_order_lines WHERE purchase_order_id=? ORDER BY line_no',
        (po_id,),
    ))
    received_map = {
        (r['customer_product_id'], r['size_label']): r['qty_received']
        for r in old_lines
    }
    for line in lines:
        key = (line['customerProductId'], line['size'])
        got = received_map.get(key, 0)
        if got > line['qtyOrdered']:
            raise ValueError(
                f"{line['productCode']} {line['size']} は既に{got}枚入荷済みのため、"
                f"発注数を{line['qtyOrdered']}には減らせません。"
            )
        line['qtyReceived'] = got

    needs_resend = 1 if po['status'] in (STATUS_ORDERED, STATUS_PARTIAL, STATUS_COMPLETE) else 0
    # Keep system 作成利用者 / empty handwritten 発注日; refresh party name snapshots.
    c.execute(
        '''UPDATE purchase_orders SET
             supplier_id=?, customer_id=?,
             supplier_name=?, supplier_contact='', supplier_fax='',
             supplier_address=?, supplier_phone=?,
             customer_name=?, customer_address=?,
             remarks=?,
             needs_resend=CASE WHEN ?=1 THEN 1 ELSE needs_resend END,
             updated_at=?
           WHERE id=?''',
        (
            supplier_id, customer_id,
            snap['supplier_name'],
            snap['supplier_address'], snap['supplier_phone'],
            snap['customer_name'], snap['customer_address'],
            remarks, needs_resend, t, po_id,
        ),
    )
    # Update lines in place so receipt FK / qty_received history stays intact.
    old_by_key = {(r['customer_product_id'], r['size_label']): r for r in old_lines}
    keep_ids = set()
    for line in lines:
        key = (line['customerProductId'], line['size'])
        prev = old_by_key.get(key)
        if prev:
            c.execute(
                '''UPDATE purchase_order_lines SET
                     line_no=?, product_id=?, product_code=?, product_name=?, product_color=?,
                     embroidery=?, qty_ordered=?, line_note=?, updated_at=?
                   WHERE id=?''',
                (
                    line['lineNo'], line['productId'], line['productCode'], line['productName'],
                    line['productColor'], line['embroidery'], line['qtyOrdered'],
                    line['lineNote'], t, prev['id'],
                ),
            )
            keep_ids.add(prev['id'])
        else:
            c.execute(
                '''INSERT INTO purchase_order_lines(
                     purchase_order_id,line_no,customer_product_id,product_id,
                     product_code,product_name,product_color,embroidery,size_label,
                     qty_ordered,qty_received,line_note,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,?)''',
                (
                    po_id, line['lineNo'], line['customerProductId'], line['productId'],
                    line['productCode'], line['productName'], line['productColor'],
                    line['embroidery'], line['size'], line['qtyOrdered'], line['lineNote'], t, t,
                ),
            )
            keep_ids.add(c.execute('SELECT last_insert_rowid()').fetchone()[0])
    for prev in old_lines:
        if prev['id'] in keep_ids:
            continue
        if prev['qty_received'] > 0:
            raise ValueError(
                f"入荷済みの明細（{prev['product_code']} {prev['size_label']}）は削除できません。"
            )
        linked = c.execute(
            'SELECT 1 FROM purchase_order_receipts WHERE purchase_order_line_id=?',
            (prev['id'],),
        ).fetchone()
        if linked:
            raise ValueError('入荷履歴のある明細は削除できません。')
        c.execute('DELETE FROM purchase_order_lines WHERE id=?', (prev['id'],))
    _recompute_status(c, po_id)
    detail = '発注内容を修正'
    if needs_resend:
        detail += '（紙の再送が必要）'
    _add_event(c, po_id, 'updated', actor, detail)
    return {**_po_dict(c, po_id, include_events=True), 'already': False}


def _recompute_status(c, po_id):
    po = c.execute('SELECT status FROM purchase_orders WHERE id=?', (po_id,)).fetchone()
    if not po or po['status'] == STATUS_CANCELLED:
        return po['status'] if po else None
    lines = list(c.execute(
        'SELECT qty_ordered, qty_received FROM purchase_order_lines WHERE purchase_order_id=?',
        (po_id,),
    ))
    if not lines:
        return po['status']
    total_o = sum(r['qty_ordered'] for r in lines)
    total_r = sum(r['qty_received'] for r in lines)
    if total_r <= 0:
        new = STATUS_UNORDERED if po['status'] == STATUS_UNORDERED else STATUS_ORDERED
    elif total_r < total_o:
        new = STATUS_PARTIAL
    else:
        new = STATUS_COMPLETE
    if new != po['status']:
        c.execute(
            'UPDATE purchase_orders SET status=?, updated_at=? WHERE id=?',
            (new, stock.stamp(), po_id),
        )
    return new


def mark_ordered(c, body):
    stock._require_write(body)
    po_id = body.get('id')
    if not isinstance(po_id, int):
        raise ValueError('発注書を選んでください。')
    token = stock.text(body.get('token'))
    po = c.execute('SELECT * FROM purchase_orders WHERE id=?', (po_id,)).fetchone()
    if not po:
        raise ValueError('発注書が見つかりません。')
    if po['status'] == STATUS_CANCELLED:
        raise ValueError('取消済みの発注書は発注済みにできません。')
    if po['status'] != STATUS_UNORDERED:
        # Idempotent: already ordered / receiving
        return {**_po_dict(c, po_id, include_events=True), 'already': True}
    actor = stock._actor_name(body)
    t = stock.stamp()
    c.execute(
        '''UPDATE purchase_orders SET status=?, ordered_at=?, needs_resend=0, updated_at=? WHERE id=? AND status=?''',
        (STATUS_ORDERED, t, t, po_id, STATUS_UNORDERED),
    )
    if c.execute('SELECT changes()').fetchone()[0] != 1:
        return {**_po_dict(c, po_id, include_events=True), 'already': True}
    _add_event(c, po_id, 'ordered', actor, 'FAX/メール送信後に発注済みにした' + (f' token={token}' if token else ''))
    return {**_po_dict(c, po_id, include_events=True), 'already': False}


def cancel_purchase_order(c, body):
    stock._require_write(body)
    po_id = body.get('id')
    if not isinstance(po_id, int):
        raise ValueError('発注書を選んでください。')
    po = c.execute('SELECT * FROM purchase_orders WHERE id=?', (po_id,)).fetchone()
    if not po:
        raise ValueError('発注書が見つかりません。')
    if po['status'] == STATUS_CANCELLED:
        return {**_po_dict(c, po_id, include_events=True), 'already': True}
    # Do NOT reverse received stock
    actor = stock._actor_name(body)
    t = stock.stamp()
    c.execute(
        'UPDATE purchase_orders SET status=?, updated_at=? WHERE id=?',
        (STATUS_CANCELLED, t, po_id),
    )
    _add_event(c, po_id, 'cancelled', actor, '取消（入荷済み在庫は減らしません）')
    return {**_po_dict(c, po_id, include_events=True, include_receipts=True), 'already': False}


def receive_purchase_order(c, body):
    """Receive one or more PO lines via the same register_inbound path. Atomic."""
    stock._require_write(body)
    token = stock._token(body.get('token'))
    existing = c.execute(
        'SELECT purchase_order_id FROM purchase_order_receipts WHERE client_token=?',
        (token,),
    ).fetchone()
    if existing:
        return {**_po_dict(c, existing['purchase_order_id'], include_events=True, include_receipts=True), 'already': True}

    po_id = body.get('id') or body.get('purchaseOrderId')
    if not isinstance(po_id, int):
        raise ValueError('発注書を選んでください。')
    po = c.execute('SELECT * FROM purchase_orders WHERE id=?', (po_id,)).fetchone()
    if not po:
        raise ValueError('発注書が見つかりません。')
    if po['status'] == STATUS_CANCELLED:
        raise ValueError('取消済みの発注書には入荷登録できません。')
    if po['status'] == STATUS_UNORDERED:
        raise ValueError('未発注のままでは入荷登録できません。先に「発注済みにする」を押してください。')

    items = body.get('items') or body.get('lines') or []
    if not isinstance(items, list) or not items:
        # Single-line convenience
        if body.get('lineId') is not None:
            items = [body]
        else:
            raise ValueError('入荷する明細を選んでください。')

    actor = stock._actor_name(body)
    received_on = stock.parse_date(body.get('receivedOn') or date.today().isoformat(), '入荷日')
    results = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError('入荷明細の形式が不正です。')
        line_id = item.get('lineId') or item.get('id')
        if not isinstance(line_id, int):
            raise ValueError('入荷する明細を選んでください。')
        line = c.execute(
            'SELECT * FROM purchase_order_lines WHERE id=? AND purchase_order_id=?',
            (line_id, po_id),
        ).fetchone()
        if not line:
            raise ValueError('発注明細が見つかりません。')
        remaining = line['qty_ordered'] - line['qty_received']
        if remaining <= 0:
            raise ValueError(f"明細{line['line_no']}は入荷完了済みです。")
        qty = stock.parse_qty(item.get('qty'), '入荷数量')
        if qty > remaining:
            raise ValueError(
                f"明細{line['line_no']}の残数は{remaining}枚です。残数を超える入荷はできません。"
            )
        cost = stock.parse_money(item.get('unitCost'), '仕入単価')
        loc_id = item.get('locationId') if item.get('locationId') is not None else body.get('locationId')
        loc = stock.resolve_active_location(c, loc_id)
        note = stock.optional(item.get('note'), '備考', 200) or f"発注 {po['number']} 明細{line['line_no']}"
        line_token = f'{token}:{i}'
        if len(line_token) > 80:
            line_token = f'{token[:60]}:{i}'
        inbound = stock.register_inbound(c, {
            'token': line_token,
            'customerProductId': line['customer_product_id'],
            'size': line['size_label'],
            'locationId': loc['id'],
            'qty': qty,
            'unitCost': cost,
            'receivedOn': item.get('receivedOn') or received_on,
            'note': note,
            '_canWrite': True,
            '_actor': body.get('_actor') or {'name': actor},
        })
        before = line['qty_received']
        after = before + qty
        c.execute(
            '''UPDATE purchase_order_lines
               SET qty_received=?, updated_at=?
               WHERE id=? AND qty_received=? AND qty_received+? <= qty_ordered''',
            (after, stock.stamp(), line_id, before, qty),
        )
        if c.execute('SELECT changes()').fetchone()[0] != 1:
            raise ValueError('同時に入荷登録されたため、保存を中止しました。最新の残数を確認してください。')
        c.execute(
            '''INSERT INTO purchase_order_receipts(
                 purchase_order_id,purchase_order_line_id,move_batch_id,lot_id,
                 qty,unit_cost,received_on,location_id,client_token,actor_name,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (
                po_id, line_id, inbound['batchId'], inbound['lotId'],
                qty, cost, item.get('receivedOn') or received_on, loc['id'],
                token if i == 0 else f'{token}#{i}',
                actor, stock.stamp(),
            ),
        )
        # Only the first receipt row uses the main token for idempotency lookup;
        # subsequent lines in the same request use derived tokens that won't collide.
        if i > 0:
            pass
        results.append({
            'lineId': line_id,
            'qty': qty,
            'unitCost': cost,
            'lotId': inbound['lotId'],
            'batchId': inbound['batchId'],
            'currentQtyAfter': inbound.get('currentQtyAfter'),
        })
        _add_event(
            c, po_id, 'received', actor,
            f"明細{line['line_no']} {line['size_label']} {qty}枚 @ {cost}円（ロット{inbound['lotId']}）",
        )

    # Ensure primary token is on first receipt (already). For multi-line, store a
    # marker receipt? Already first uses token. Re-query with same token → already.
    new_status = _recompute_status(c, po_id)
    out = _po_dict(c, po_id, include_events=True, include_receipts=True)
    out['already'] = False
    out['receiveResults'] = results
    out['status'] = new_status or out['status']
    return out


def _esc(value):
    return html.escape('' if value is None else str(value), quote=True)


def build_po_document(c, source):
    """Build a print/PDF document dict from saved PO id or draft body (pre-save).

    Handwritten header fields (発注日 / 担当 / FAX / 発注者) are intentionally blank.
    Company block always comes from settings (seeded defaults if unset).
    """
    company = company_profile(c)
    if isinstance(source, int) or (isinstance(source, dict) and isinstance(source.get('id'), int) and not source.get('draft')):
        po_id = source if isinstance(source, int) else source['id']
        po = _po_dict(c, po_id)
        return {
            'number': po['number'],
            'orderDate': '',  # handwritten blank
            'statusLabel': po['statusLabel'],
            'supplierName': po['supplierName'],
            'supplierContact': '',
            'supplierFax': '',
            'supplierAddress': po['supplierAddress'],
            'supplierPhone': po['supplierPhone'],
            'customerName': po['customerName'],
            'ordererName': '',  # handwritten blank (createdBy kept in DB only)
            'remarks': po['remarks'],
            'lines': po['lines'],
            'company': company,
            'saved': True,
            'createdAt': po['createdAt'],
            'createdBy': po.get('createdBy') or '',
        }

    body = source if isinstance(source, dict) else {}
    try:
        supplier_id = int(body.get('supplierId'))
        customer_id = int(body.get('customerId'))
    except (TypeError, ValueError):
        raise ValueError('仕入先と対象顧客を選んでください。')
    remarks = stock.optional(body.get('remarks') or body.get('note'), '備考', 500)
    lines = _normalize_lines(c, body, customer_id, supplier_id)
    snap = _snapshot_parties(c, supplier_id, customer_id, body)
    return {
        'number': stock.text(body.get('number')) or '（保存前）',
        'orderDate': '',
        'statusLabel': '未保存',
        'supplierName': snap['supplier_name'],
        'supplierContact': '',
        'supplierFax': '',
        'supplierAddress': snap['supplier_address'],
        'supplierPhone': snap['supplier_phone'],
        'customerName': snap['customer_name'],
        'ordererName': '',
        'remarks': remarks,
        'lines': [
            {
                'lineNo': ln['lineNo'],
                'code': ln['productCode'],
                'productName': ln['productName'],
                'color': ln['productColor'],
                'size': ln['size'],
                'qtyOrdered': ln['qtyOrdered'],
                'lineNote': ln['lineNote'],
            }
            for ln in lines
        ],
        'company': company,
        'saved': False,
        'createdAt': '',
        'createdBy': '',
    }


PO_PRINT_CSS = """
@page { size: A4 portrait; margin: 12mm 12mm 14mm 12mm; }
*{ box-sizing: border-box; }
body {
  margin: 0; color: #000; background: #fff;
  font-family: "BIZ UDPGothic","Yu Gothic UI","Yu Gothic",Meiryo,sans-serif;
  font-size: 10.5pt; line-height: 1.35;
}
.po-sheet { width: 100%; }
.po-head {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px;
  border-bottom: 2px solid #000; padding-bottom: 8px; margin-bottom: 10px;
}
.po-title { font-size: 22pt; font-weight: 700; letter-spacing: 0.35em; margin: 0 0 6px; }
.po-meta { font-size: 10pt; margin-top: 4px; }
.po-blank {
  display: inline-block; min-width: 8em; border-bottom: 1px solid #000;
  height: 1.15em; vertical-align: bottom; margin: 0 0.2em;
}
.po-blank.wide { min-width: 12em; }
.po-company { text-align: right; font-size: 9.5pt; }
.po-company .name { font-size: 12pt; font-weight: 700; }
.po-company .co-label { color: #000; }
.po-to { margin: 0 0 10px; font-size: 12pt; }
.po-to .attn { margin-top: 6px; font-size: 10pt; display: flex; flex-wrap: wrap; gap: 10px 18px; }
.po-context { margin: 0 0 8px; display: flex; justify-content: space-between; gap: 12px; font-size: 10.5pt; align-items: end; }
.po-table { width: 100%; border-collapse: collapse; }
.po-table th, .po-table td {
  border: 1px solid #000; padding: 5px 6px; vertical-align: top;
  font-size: 9.5pt; background: #fff;
}
.po-table th { font-weight: 700; text-align: center; }
.po-table .qty { text-align: right; white-space: nowrap; width: 4.5em; }
.po-table .size { text-align: center; width: 4em; white-space: nowrap; }
.po-table .note { width: 22%; }
.po-table thead { display: table-header-group; }
.po-table tfoot { display: table-footer-group; }
.po-table tr { break-inside: avoid; page-break-inside: avoid; }
.po-remarks {
  margin-top: 10px; border: 1px solid #000; padding: 8px; min-height: 3.2em;
  font-size: 9.5pt; white-space: pre-wrap;
}
.po-foot {
  margin-top: 8px; display: flex; justify-content: space-between; align-items: end;
  font-size: 8.5pt;
}
.po-number { font-size: 8.5pt; letter-spacing: 0.02em; }
.page-num::after { content: counter(page) " / " counter(pages); }
@media screen {
  body { background: #e8ebe8; padding: 16px; }
  .po-sheet {
    max-width: 210mm; min-height: 297mm; margin: 0 auto; padding: 12mm;
    background: #fff; box-shadow: 0 2px 12px #0002;
  }
}
"""


def render_po_html(doc):
    company = doc.get('company') or {}
    # Always print the same five company fields from settings (may be blank until edited).
    company_bits = [
        f'<div class="name">{_esc(company.get("companyName") or "")}</div>',
        f'<div><span class="co-label">支店</span>　{_esc(company.get("companyBranch") or "")}</div>',
        f'<div><span class="co-label">住所</span>　{_esc(company.get("companyAddress") or "")}</div>',
        f'<div>TEL {_esc(company.get("companyTel") or "")}</div>',
        f'<div>FAX {_esc(company.get("companyFax") or "")}</div>',
    ]

    rows = []
    for ln in doc.get('lines') or []:
        name = f"{ln.get('productName') or ''} {ln.get('color') or ''}".strip()
        code = ln.get('code') or ''
        rows.append(
            '<tr>'
            f'<td>{_esc(name)}<div style="font-size:8.5pt;margin-top:2px">品番 {_esc(code)}</div></td>'
            f'<td class="size">{_esc(ln.get("size"))}</td>'
            f'<td class="qty">{_esc(ln.get("qtyOrdered"))}</td>'
            f'<td class="note">{_esc(ln.get("lineNote") or "")}</td>'
            '</tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="4">明細がありません</td></tr>')

    remarks = doc.get('remarks') or ''
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>発注書 {_esc(doc.get('number'))}</title>
<style>{PO_PRINT_CSS}</style>
</head><body>
<article class="po-sheet">
  <header class="po-head">
    <div>
      <h1 class="po-title">発注書</h1>
      <div class="po-meta">発注日　<span class="po-blank wide" aria-label="発注日（手書き）"></span></div>
    </div>
    <div class="po-company">{''.join(company_bits)}</div>
  </header>
  <section class="po-to">
    <div><strong>{_esc(doc.get('supplierName'))}</strong>　御中</div>
    <div class="attn">
      <span>担当　<span class="po-blank" aria-label="仕入先担当者（手書き）"></span></span>
      <span>FAX　<span class="po-blank" aria-label="仕入先FAX（手書き）"></span></span>
    </div>
  </section>
  <section class="po-context">
    <div>対象顧客　{_esc(doc.get('customerName'))}様分</div>
    <div>発注者　<span class="po-blank" aria-label="発注者（手書き）"></span></div>
  </section>
  <table class="po-table">
    <thead>
      <tr><th>品名・品番・色</th><th>サイズ</th><th>数量</th><th>備考</th></tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
  <section class="po-remarks"><div>備考</div>{_esc(remarks)}</section>
  <footer class="po-foot">
    <div class="po-number">発注番号 {_esc(doc.get('number'))}</div>
    <div class="page-num"></div>
  </footer>
</article>
</body></html>"""


def preview_purchase_order_html(c, body):
    doc = build_po_document(c, body if isinstance(body, dict) else {})
    return render_po_html(doc)


def purchase_order_html(c, po_id):
    doc = build_po_document(c, po_id)
    return render_po_html(doc)
