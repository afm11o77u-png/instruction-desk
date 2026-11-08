"""新しい在庫QRのID。

割当単位は、顧客と商品と取扱（customer_products）・サイズ・新品中古。
色は商品の属性なので、別の色は別の商品になり、ここでも分かれる。色の文字はIDに入れない。
場所・入荷・単価・数量はIDに入れない。同じ区分は同じIDを再利用する。

文字列は nqr1. と UUID の16進（例: nqr1.3f2a9c1e7b804d6a91c0e5f4a2b8d714）。
旧在庫行IDや旧QRのJSONとは形が違う。この形は旧対応表を見ない。

発行と読取は在庫数量を変えない。ロットが0件でも複数でも、1件には決めない。
"""
from __future__ import annotations

import base64
import re
import sqlite3
import uuid

import stock

PREFIX = 'nqr1.'
PUBLIC_RE = re.compile(r'^nqr1\.[0-9a-f]{32}\Z')


def ensure_schema(c):
    c.execute(
        '''CREATE TABLE IF NOT EXISTS stock_qr_codes (
          id INTEGER PRIMARY KEY,
          public_id TEXT NOT NULL UNIQUE,
          customer_product_id INTEGER NOT NULL REFERENCES customer_products(id),
          size_label TEXT NOT NULL,
          condition TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(customer_product_id, size_label, condition)
        )'''
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS stock_qr_codes_handle ON stock_qr_codes(customer_product_id)'
    )


def is_new_qr(value):
    return PUBLIC_RE.fullmatch(stock.text(value)) is not None


def _condition_key(value):
    """空文字は未設定。新品へまとめない。省略は受け取らない。"""
    if not isinstance(value, str):
        raise ValueError('新品か中古かを指定してください。')
    raw = value.strip()
    if raw in ('', '未設定'):
        return ''
    if raw in ('new', '新品'):
        return stock.CONDITION_NEW
    if raw in ('used', '中古'):
        return stock.CONDITION_USED
    raise ValueError('状態は新品・中古・未設定のいずれかです。')


def _load_handle(c, cp_id):
    return c.execute(
        '''SELECT cp.id, cp.customer_id, cp.product_id, cp.embroidery, cp.active,
                  cu.name AS customer_name, cu.active AS customer_active,
                  p.code, p.name AS product_name, p.color, p.active AS product_active
           FROM customer_products cp
           JOIN customers cu ON cu.id=cp.customer_id
           JOIN products p ON p.id=cp.product_id
           WHERE cp.id=?''',
        (cp_id,),
    ).fetchone()


def _classification(c, body):
    cp_id = stock._as_positive_id(body.get('customerProductId'), '顧客と商品の取扱')
    cp = _load_handle(c, cp_id)
    if not cp:
        raise ValueError('取扱が見つかりません。')
    size = body.get('size')
    if not isinstance(size, str):
        raise ValueError('サイズを指定してください。')
    size = stock.resolve_size_label(c, cp['product_id'], size)
    condition = _condition_key(body.get('condition'))
    return cp, size, condition


def _find(c, cp_id, size, condition):
    return c.execute(
        '''SELECT * FROM stock_qr_codes
           WHERE customer_product_id=? AND size_label=? AND condition=?''',
        (cp_id, size, condition),
    ).fetchone()


def _insert(c, cp_id, size, condition):
    t = stock.stamp()
    for _ in range(5):
        public_id = PREFIX + uuid.uuid4().hex
        try:
            c.execute(
                '''INSERT INTO stock_qr_codes(
                     public_id, customer_product_id, size_label, condition, created_at
                   ) VALUES(?,?,?,?,?)''',
                (public_id, cp_id, size, condition, t),
            )
            return _find(c, cp_id, size, condition), True
        except sqlite3.IntegrityError:
            existing = _find(c, cp_id, size, condition)
            if existing:
                return existing, False
    raise ValueError('QRを発行できませんでした。もう一度操作してください。')


def _lots(c, cp_id, size, condition):
    rows = []
    for lot in c.execute(
        '''SELECT l.id, l.qty_remaining, l.condition, l.size_label, l.location_id, l.unit_cost
           FROM lots l
           WHERE l.customer_product_id=? AND l.size_label=? AND l.condition=?
           ORDER BY l.id''',
        (cp_id, size, condition),
    ):
        place = c.execute('SELECT * FROM locations WHERE id=?', (lot['location_id'],)).fetchone()
        rows.append({
            'lotId': lot['id'],
            'qtyRemaining': lot['qty_remaining'],
            'condition': lot['condition'] or '',
            'conditionLabel': stock.condition_label(lot['condition']) if lot['condition'] else '未設定',
            'size': lot['size_label'] or '',
            'locationId': lot['location_id'],
            'locationLabel': stock.location_label(c, place) if place else '',
            'unitCost': lot['unit_cost'],
        })
    return rows


def _image(public_id):
    try:
        import qr_image
        png = qr_image.png_bytes(public_id)
    except Exception:
        return '', 'QR画像を作れませんでした。IDはそのまま使えます。'
    return base64.b64encode(png).decode('ascii'), ''


def _payload(c, cp, row, *, created):
    lots = _lots(c, cp['id'], row['size_label'], row['condition'])
    png, image_error = _image(row['public_id'])
    condition = row['condition'] or ''
    body = {
        'found': True,
        'kind': 'stock',
        'issued': True,
        'created': bool(created),
        'publicId': row['public_id'],
        'customerProductId': cp['id'],
        'customerId': cp['customer_id'],
        'customerName': cp['customer_name'] or '',
        'productId': cp['product_id'],
        'code': cp['code'] or '',
        'productName': cp['product_name'] or '',
        'color': cp['color'] or '',
        'embroidery': cp['embroidery'] or '',
        'embroideryLabel': stock.embroidery_label(cp['embroidery']),
        'size': row['size_label'],
        'condition': condition,
        'conditionLabel': stock.condition_label(condition) if condition else '未設定',
        'lots': lots,
        'lotCount': len(lots),
        'pickedLotId': None,
        'qtyChanged': False,
        'pngBase64': png,
        'note': '同じ顧客・商品・取扱・サイズ・新品中古は、場所や入荷が違っても同じQRです。在庫数量は変えていません。',
    }
    if image_error:
        body['imageError'] = image_error
    return body


def show(c, body):
    """区分のQRを返す。無ければ、更新できるときだけ発行する。数量は変えない。"""
    ensure_schema(c)
    cp, size, condition = _classification(c, body)
    existing = _find(c, cp['id'], size, condition)
    if not cp['active'] or not cp['product_active'] or not cp['customer_active']:
        if existing:
            return _payload(c, cp, existing, created=False)
        raise ValueError('使用中の顧客・商品・仕様を選んでください。')
    if existing:
        return _payload(c, cp, existing, created=False)
    if body.get('_canWrite') is False:
        raise PermissionError('閲覧専用のため、QRは発行できません。')
    row, created = _insert(c, cp['id'], size, condition)
    return _payload(c, cp, row, created=created)


def _lookup_new(c, public_id):
    ensure_schema(c)
    row = c.execute(
        'SELECT * FROM stock_qr_codes WHERE public_id=?', (public_id,)
    ).fetchone()
    if not row:
        return {
            'found': False,
            'kind': 'stock',
            'publicId': public_id,
            'lots': [],
            'lotCount': 0,
            'pickedLotId': None,
            'qtyChanged': False,
            'mismatch': '',
        }
    cp = _load_handle(c, row['customer_product_id'])
    if not cp:
        return {
            'found': False,
            'kind': 'stock',
            'publicId': public_id,
            'lots': [],
            'lotCount': 0,
            'pickedLotId': None,
            'qtyChanged': False,
            'mismatch': 'handle',
        }
    body = _payload(c, cp, row, created=False)
    body['mismatch'] = ''
    return body


def lookup(c, qr):
    """新QRなら在庫区分、それ以外は今まで通りの旧QR。数量は変えない。"""
    text = stock.text(qr if not isinstance(qr, dict) else '')
    if not isinstance(qr, dict) and is_new_qr(text):
        return _lookup_new(c, text)
    import qr_link_import
    found = qr_link_import.lookup_qr(c, qr)
    found['kind'] = 'legacy'
    found['qtyChanged'] = False
    found['pickedLotId'] = None
    return found
