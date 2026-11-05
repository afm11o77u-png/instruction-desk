"""月末締めの記録と、通常の在庫更新の停止。

締め記録は保存時点のロットを複製するだけで、後の入出庫や棚卸反映では変わらない。
停止と解除は主管理者（role=owner）と現場（role=floor。出荷仕分け・ピッキングの倉庫側）だけ。
管理者フラグ（manage）だけでは停止・解除できない。
停止中の数量・金額の増減は、その2つの権限を含む全員に拒否する。
棚卸表の実棚反映（stock_stocktake.apply）だけは、一般の棚卸APIに偽装できないよう
コンテキスト内からの呼び出しに限って許可する。
"""
from __future__ import annotations

import contextvars
import re

import stock

_MONTH = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
# 利用者管理の表示名: owner=主管理者, floor=現場。
# 独立した「倉庫担当」ロールは無い。倉庫画面（出荷仕分け・ピッキング）は owner と floor。
HOLD_ROLES = frozenset({'owner', 'floor'})
HELD_MESSAGE = '在庫の更新を停止中です。入庫・出庫・納品確認などはできません。棚卸の反映は続けられます。'

_stocktake_apply = contextvars.ContextVar('stocktake_sheet_apply', default=False)


class StockUpdateHeld(ValueError):
    def __init__(self):
        super().__init__(HELD_MESSAGE)
        self.payload = {'error': HELD_MESSAGE, 'code': 'stock_update_held', 'registered': False}


def ensure_schema(c):
    started = not c.in_transaction
    c.execute('''CREATE TABLE IF NOT EXISTS stock_update_hold (
      id INTEGER PRIMARY KEY CHECK (id=1),
      held INTEGER NOT NULL DEFAULT 0,
      actor_id INTEGER,
      actor_name TEXT NOT NULL DEFAULT '',
      actor_role TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL DEFAULT ''
    )''')
    c.execute('INSERT OR IGNORE INTO stock_update_hold(id, held, updated_at) VALUES(1, 0, \'\')')
    c.execute('''CREATE TABLE IF NOT EXISTS stock_close_records (
      id INTEGER PRIMARY KEY,
      period_month TEXT NOT NULL,
      version INTEGER NOT NULL,
      saved_at TEXT NOT NULL,
      actor_id INTEGER,
      actor_name TEXT NOT NULL DEFAULT '',
      actor_role TEXT NOT NULL DEFAULT '',
      lot_count INTEGER NOT NULL,
      qty_total INTEGER NOT NULL,
      amount_total INTEGER NOT NULL,
      pending_qty INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      UNIQUE(period_month, version)
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS stock_close_records_month '
        'ON stock_close_records(period_month, version DESC)'
    )
    c.execute('''CREATE TABLE IF NOT EXISTS stock_close_lines (
      id INTEGER PRIMARY KEY,
      record_id INTEGER NOT NULL REFERENCES stock_close_records(id),
      lot_id INTEGER NOT NULL,
      product_id INTEGER,
      product_code TEXT NOT NULL DEFAULT '',
      product_name TEXT NOT NULL DEFAULT '',
      color TEXT NOT NULL DEFAULT '',
      customer_id INTEGER,
      customer_name TEXT NOT NULL DEFAULT '',
      customer_product_id INTEGER,
      embroidery TEXT NOT NULL DEFAULT '',
      size_label TEXT NOT NULL DEFAULT '',
      condition TEXT NOT NULL DEFAULT '',
      location_id INTEGER,
      location_label TEXT NOT NULL DEFAULT '',
      unit_cost INTEGER NOT NULL,
      cost_pending INTEGER NOT NULL DEFAULT 0,
      qty INTEGER NOT NULL,
      amount INTEGER,
      UNIQUE(record_id, lot_id)
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS stock_close_lines_record ON stock_close_lines(record_id, id)'
    )
    if started and c.in_transaction:
        c.commit()


def can_hold(actor):
    """停止・解除。role だけを見る。manage / overview / canWrite では許可しない。"""
    if not isinstance(actor, dict):
        return False
    return stock.text(actor.get('role')) in HOLD_ROLES


def hold_state(c):
    ensure_schema(c)
    row = c.execute('SELECT * FROM stock_update_hold WHERE id=1').fetchone()
    if not row:
        return {'held': False, 'actorName': '', 'actorRole': '', 'updatedAt': ''}
    return {
        'held': bool(row['held']),
        'actorName': row['actor_name'] or '',
        'actorRole': row['actor_role'] or '',
        'updatedAt': row['updated_at'] or '',
    }


def updates_held(c):
    return bool(hold_state(c)['held'])


def require_stock_open(c):
    """通常の在庫数量・金額の変更の直前。BEGIN IMMEDIATE の中で読む。"""
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')
    if updates_held(c):
        raise StockUpdateHeld()


def stocktake_apply_active():
    return bool(_stocktake_apply.get())


class stocktake_apply_scope:
    """棚卸表の実棚反映だけが在庫を変えてよい区間。APIの引数では入れない。"""

    def __enter__(self):
        self._token = _stocktake_apply.set(True)
        return self

    def __exit__(self, exc_type, exc, tb):
        _stocktake_apply.reset(self._token)
        return False


def _actor(body):
    actor = body.get('_actor') if isinstance(body, dict) and isinstance(body.get('_actor'), dict) else {}
    return actor


def _require_hold_role(body):
    actor = _actor(body)
    if not can_hold(actor):
        raise PermissionError('在庫更新の停止と解除は、現場（倉庫担当）と主管理者だけが実行できます。')
    return actor


def set_hold(c, body, *, held):
    """停止または解除。締め記録の保存とは独立。保存しただけでは切り替わらない。"""
    actor = _require_hold_role(body)
    ensure_schema(c)
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')
    current = c.execute('SELECT held FROM stock_update_hold WHERE id=1').fetchone()
    now = bool(current and current['held'])
    if now == bool(held):
        return {**hold_state(c), 'already': True}
    t = stock.stamp()
    c.execute(
        '''UPDATE stock_update_hold
           SET held=?, actor_id=?, actor_name=?, actor_role=?, updated_at=?
           WHERE id=1''',
        (
            1 if held else 0,
            actor.get('id'),
            stock.text(actor.get('name'))[:80],
            stock.text(actor.get('role'))[:40],
            t,
        ),
    )
    return {**hold_state(c), 'already': False}


def _month(value):
    text = stock.text(value)
    if not _MONTH.fullmatch(text):
        raise ValueError('対象月は YYYY-MM で指定してください。')
    return text


def _line_amount(qty, unit_cost, cost_pending):
    return stock.lot_confirmed_amount(qty, unit_cost, cost_pending)


def save_record(c, body):
    """対象月の版を1件追加する。同月の過去版は上書きしない。停止状態は変えない。"""
    stock._require_write(body)
    ensure_schema(c)
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')
    month = _month(body.get('month') or body.get('periodMonth'))
    actor = _actor(body)
    t = stock.stamp()
    rows = list(c.execute(
        '''SELECT l.id AS lot_id, l.qty_remaining, l.unit_cost, l.condition,
                  COALESCE(l.cost_pending,0) AS cost_pending,
                  l.customer_product_id, l.size_label, l.location_id,
                  cp.customer_id, cp.product_id, cp.embroidery,
                  cu.name AS customer_name,
                  p.code AS product_code, p.name AS product_name, p.color,
                  loc.name AS location_name, loc.parent_id AS location_parent
           FROM lots l
           JOIN customer_products cp ON cp.id=l.customer_product_id
           JOIN customers cu ON cu.id=cp.customer_id
           JOIN products p ON p.id=cp.product_id
           JOIN locations loc ON loc.id=l.location_id
           ORDER BY l.id'''
    ))
    ver = c.execute(
        'SELECT COALESCE(MAX(version),0) AS n FROM stock_close_records WHERE period_month=?',
        (month,),
    ).fetchone()['n'] + 1
    qty_total = 0
    amount_total = 0
    pending_qty = 0
    prepared = []
    for r in rows:
        qty = int(r['qty_remaining'])
        pending = int(r['cost_pending'] or 0)
        amount = _line_amount(qty, r['unit_cost'], pending)
        qty_total += qty
        if pending:
            pending_qty += qty
        elif amount is not None:
            amount_total += amount
        parent = ''
        if r['location_parent']:
            parent_row = c.execute(
                'SELECT name FROM locations WHERE id=?', (r['location_parent'],)
            ).fetchone()
            parent = (parent_row['name'] + '／') if parent_row else ''
        prepared.append((r, qty, pending, amount, parent + (r['location_name'] or '')))
    c.execute(
        '''INSERT INTO stock_close_records(
             period_month, version, saved_at, actor_id, actor_name, actor_role,
             lot_count, qty_total, amount_total, pending_qty, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        (
            month, ver, t, actor.get('id'),
            stock.text(actor.get('name'))[:80], stock.text(actor.get('role'))[:40],
            len(prepared), qty_total, amount_total, pending_qty, t,
        ),
    )
    record_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    for r, qty, pending, amount, loc_label in prepared:
        c.execute(
            '''INSERT INTO stock_close_lines(
                 record_id, lot_id, product_id, product_code, product_name, color,
                 customer_id, customer_name, customer_product_id, embroidery,
                 size_label, condition, location_id, location_label,
                 unit_cost, cost_pending, qty, amount)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                record_id, r['lot_id'], r['product_id'], r['product_code'] or '',
                r['product_name'] or '', r['color'] or '',
                r['customer_id'], r['customer_name'] or '', r['customer_product_id'],
                r['embroidery'] or '', r['size_label'] or '', r['condition'] or '',
                r['location_id'], loc_label, r['unit_cost'], pending, qty, amount,
            ),
        )
    return record_detail(c, record_id)


def list_records(c):
    ensure_schema(c)
    rows = c.execute(
        '''SELECT id, period_month, version, saved_at, actor_name, actor_role,
                  lot_count, qty_total, amount_total, pending_qty
           FROM stock_close_records ORDER BY period_month DESC, version DESC, id DESC'''
    ).fetchall()
    return {
        'hold': hold_state(c),
        'records': [_record_public(r) for r in rows],
        'notices': list(NOTICES),
    }


NOTICES = (
    '現物の入出庫も止める必要があります。',
    '停止中は通常の在庫更新を拒否します。棚卸の実棚反映は続けられます。',
    '保存した締め記録は、その後の通常在庫や棚卸反映では変わりません。',
    '記録の日時は棚卸で自動では直しません。経理上、棚卸の前の記録か後の記録かはここでは決めません。',
)


def _record_public(r):
    return {
        'id': r['id'],
        'month': r['period_month'],
        'version': r['version'],
        'savedAt': r['saved_at'],
        'actorName': r['actor_name'] or '',
        'actorRole': r['actor_role'] or '',
        'lotCount': r['lot_count'],
        'qtyTotal': r['qty_total'],
        'amountTotal': r['amount_total'],
        'pendingQty': r['pending_qty'],
    }


def record_detail(c, record_id):
    ensure_schema(c)
    try:
        rid = int(record_id)
    except (TypeError, ValueError) as e:
        raise ValueError('締め記録を選んでください。') from e
    head = c.execute('SELECT * FROM stock_close_records WHERE id=?', (rid,)).fetchone()
    if not head:
        raise ValueError('締め記録が見つかりません。')
    lines = []
    for r in c.execute(
        '''SELECT * FROM stock_close_lines WHERE record_id=? ORDER BY id''', (rid,)
    ):
        lines.append({
            'lotId': r['lot_id'],
            'productId': r['product_id'],
            'code': r['product_code'],
            'productName': r['product_name'],
            'color': r['color'],
            'customerId': r['customer_id'],
            'customerName': r['customer_name'],
            'customerProductId': r['customer_product_id'],
            'embroidery': r['embroidery'],
            'size': r['size_label'],
            'condition': r['condition'],
            'conditionLabel': stock.condition_label(r['condition']) if r['condition'] else '未設定',
            'locationId': r['location_id'],
            'locationLabel': r['location_label'],
            'unitCost': r['unit_cost'],
            'costPending': bool(r['cost_pending']),
            'qty': r['qty'],
            'amount': r['amount'],
        })
    return {**_record_public(head), 'lines': lines}
