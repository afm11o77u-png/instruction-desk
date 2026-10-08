"""在庫モジュール。数量と金額はロット（入荷分）から計算し、画面から現在庫を手入力させない。"""
import sqlite3
from datetime import date, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kana TEXT NOT NULL DEFAULT '',
  address TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  contact TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  address TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  contact TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  color TEXT NOT NULL DEFAULT '',
  supplier_id INTEGER REFERENCES suppliers(id),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_sizes (
  id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(id),
  label TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  UNIQUE(product_id, label)
);
CREATE TABLE IF NOT EXISTS customer_products (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  embroidery TEXT NOT NULL DEFAULT '',
  is_standard INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(customer_id, product_id, embroidery)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_standard_spec
  ON customer_products(customer_id, product_id) WHERE is_standard=1;
CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES locations(id),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS locations_root_name ON locations(name) WHERE parent_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS locations_child_name ON locations(parent_id, name) WHERE parent_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS opening_batches (
  id INTEGER PRIMARY KEY,
  client_token TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lots (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER REFERENCES opening_batches(id),
  customer_product_id INTEGER NOT NULL REFERENCES customer_products(id),
  size_label TEXT NOT NULL,
  location_id INTEGER NOT NULL REFERENCES locations(id),
  unit_cost INTEGER NOT NULL,
  qty_received INTEGER NOT NULL,
  qty_remaining INTEGER NOT NULL,
  basis_date TEXT NOT NULL,
  received_on TEXT,
  received_unknown INTEGER NOT NULL DEFAULT 0,
  payout_order INTEGER NOT NULL DEFAULT 0,
  condition TEXT NOT NULL DEFAULT 'new',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS moves (
  id INTEGER PRIMARY KEY,
  lot_id INTEGER NOT NULL REFERENCES lots(id),
  kind TEXT NOT NULL,
  qty_delta INTEGER NOT NULL,
  unit_cost INTEGER NOT NULL,
  qty_before INTEGER,
  qty_after INTEGER,
  cost_before INTEGER,
  cost_after INTEGER,
  location_id INTEGER,
  related_lot_id INTEGER,
  loan_id INTEGER,
  note TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loans (
  id INTEGER PRIMARY KEY,
  stock_customer_id INTEGER NOT NULL REFERENCES customers(id),
  use_customer_id INTEGER NOT NULL REFERENCES customers(id),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS loan_lines (
  id INTEGER PRIMARY KEY,
  loan_id INTEGER NOT NULL REFERENCES loans(id),
  lot_id INTEGER NOT NULL REFERENCES lots(id),
  qty INTEGER NOT NULL,
  unit_cost INTEGER NOT NULL,
  received_on TEXT,
  received_unknown INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS loan_events (
  id INTEGER PRIMARY KEY,
  loan_id INTEGER NOT NULL REFERENCES loans(id),
  kind TEXT NOT NULL,
  qty INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def now():
    return datetime.now().isoformat(timespec='seconds')


def connect(path):
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def initialize(c):
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript(SCHEMA)


def text(value):
    return (value or '').strip()


def need(value, label, limit=80):
    t = text(value)
    if not t:
        raise ValueError(f'{label}を入力してください。')
    if len(t) > limit:
        raise ValueError(f'{label}が長すぎます。')
    return t


def optional(value, label, limit=200):
    t = text(value)
    if len(t) > limit:
        raise ValueError(f'{label}が長すぎます。')
    return t


def flag(value):
    return 1 if value in (True, 1, '1') else 0


def parse_date(value, label):
    t = text(value)
    if not t:
        raise ValueError(f'{label}を入力してください。')
    try:
        date.fromisoformat(t)
    except ValueError:
        raise ValueError(f'{label}は日付で入力してください。')
    return t


def parse_money(value, label='仕入単価'):
    if value is None or (isinstance(value, str) and text(value) == ''):
        raise ValueError(f'{label}を入力してください。空欄を0円にはしません。')
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f'{label}は円の整数で入力してください。')
    if isinstance(value, str):
        t = text(value).replace(',', '')
        if not t.isdigit():
            raise ValueError(f'{label}は円の整数で入力してください。空欄を0円にはしません。')
        value = int(t)
    if not isinstance(value, int):
        raise ValueError(f'{label}は円の整数で入力してください。')
    if value < 0 or value > 9999999:
        raise ValueError(f'{label}は0円から9999999円までです。')
    return value


def parse_qty(value, label='数量', allow_zero=False):
    if value is None or (isinstance(value, str) and text(value) == ''):
        raise ValueError(f'{label}を入力してください。')
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f'{label}は整数で入力してください。')
    if isinstance(value, str):
        t = text(value).replace(',', '')
        if not t.isdigit():
            raise ValueError(f'{label}は整数で入力してください。')
        value = int(t)
    if not isinstance(value, int):
        raise ValueError(f'{label}は整数で入力してください。')
    if value < 0 or value > 99999 or (value == 0 and not allow_zero):
        raise ValueError(f'{label}は1から99999までです。' if not allow_zero else f'{label}は0から99999までです。')
    return value


def stamp():
    return now()


def save_customer(c, body):
    name = need(body.get('name'), '顧客名')
    kana = optional(body.get('kana'), 'フリガナ', 80)
    address = optional(body.get('address'), '住所', 200)
    phone = optional(body.get('phone'), '電話番号', 40)
    contact = optional(body.get('contact'), '担当者', 80)
    active = flag(body.get('active', True))
    t = stamp()
    cid = body.get('id')
    if cid:
        row = c.execute('SELECT id FROM customers WHERE id=?', (cid,)).fetchone()
        if not row:
            raise ValueError('顧客が見つかりません。')
        other = c.execute('SELECT id FROM customers WHERE name=? AND id<>?', (name, cid)).fetchone()
        if other:
            raise ValueError('同じ名前の顧客が既にあります。名前の変更は、開いている顧客の修正として保存してください。')
        c.execute('UPDATE customers SET name=?,kana=?,address=?,phone=?,contact=?,active=?,updated_at=? WHERE id=?',
                  (name, kana, address, phone, contact, active, t, cid))
    else:
        if c.execute('SELECT id FROM customers WHERE name=?', (name,)).fetchone():
            raise ValueError('同じ名前の顧客が既にあります。新しい顧客を増やさず、一覧から修正してください。')
        c.execute('INSERT INTO customers(name,kana,address,phone,contact,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                  (name, kana, address, phone, contact, active, t, t))
        cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return customer_row(c, cid)


def save_supplier(c, body):
    name = need(body.get('name'), '仕入先名')
    address = optional(body.get('address'), '住所', 200)
    phone = optional(body.get('phone'), '電話番号', 40)
    contact = optional(body.get('contact'), '担当者', 80)
    active = flag(body.get('active', True))
    t = stamp()
    sid = body.get('id')
    if sid:
        if not c.execute('SELECT id FROM suppliers WHERE id=?', (sid,)).fetchone():
            raise ValueError('仕入先が見つかりません。')
        if c.execute('SELECT id FROM suppliers WHERE name=? AND id<>?', (name, sid)).fetchone():
            raise ValueError('同じ名前の仕入先が既にあります。一覧から修正してください。')
        c.execute('UPDATE suppliers SET name=?,address=?,phone=?,contact=?,active=?,updated_at=? WHERE id=?',
                  (name, address, phone, contact, active, t, sid))
    else:
        if c.execute('SELECT id FROM suppliers WHERE name=?', (name,)).fetchone():
            raise ValueError('同じ名前の仕入先が既にあります。一覧から修正してください。')
        c.execute('INSERT INTO suppliers(name,address,phone,contact,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                  (name, address, phone, contact, active, t, t))
        sid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return supplier_row(c, sid)


def _sizes(body):
    labels = []
    for raw in body.get('sizes') or []:
        if not isinstance(raw, str):
            raise ValueError('サイズの指定が不正です。')
        label = raw.strip()
        if not label:
            continue
        if len(label) > 40:
            raise ValueError('サイズ名が長すぎます。')
        if label not in labels:
            labels.append(label)
    if not labels:
        raise ValueError('サイズを1つ以上選んでください。特殊サイズは、その場で追加できます。')
    return labels


def _links(c, body):
    links = []
    seen = set()
    standards = set()
    for raw in body.get('links') or []:
        if not isinstance(raw, dict):
            raise ValueError('顧客ごとの取扱の指定が不正です。')
        customer_id = raw.get('customerId')
        if not isinstance(customer_id, int) or not c.execute('SELECT id FROM customers WHERE id=?', (customer_id,)).fetchone():
            raise ValueError('取扱の顧客を、顧客台帳から選んでください。')
        embroidery = optional(raw.get('embroidery'), '加工仕様', 80)
        key = (customer_id, embroidery)
        if key in seen:
            raise ValueError('同じ顧客で、同じ加工仕様が重複しています。刺繍ありとなしは別の行にしてください。')
        seen.add(key)
        standard = flag(raw.get('isStandard'))
        if standard:
            if customer_id in standards:
                raise ValueError('同じ顧客の標準仕様は1つだけにしてください。')
            standards.add(customer_id)
        links.append({'customerId': customer_id, 'embroidery': embroidery, 'isStandard': standard, 'active': flag(raw.get('active', True))})
    return links


def save_product(c, body):
    code = need(body.get('code'), '品番', 40)
    name = need(body.get('name'), '商品名', 80)
    color = optional(body.get('color'), '色', 40)
    active = flag(body.get('active', True))
    supplier_id = body.get('supplierId')
    if supplier_id in ('', None):
        supplier_id = None
    elif not isinstance(supplier_id, int) or not c.execute('SELECT id FROM suppliers WHERE id=?', (supplier_id,)).fetchone():
        raise ValueError('通常の仕入先を、仕入先台帳から選んでください。未定なら空のままにできます。')
    labels = _sizes(body)
    links = _links(c, body)
    t = stamp()
    pid = body.get('id')
    if pid:
        if not c.execute('SELECT id FROM products WHERE id=?', (pid,)).fetchone():
            raise ValueError('商品が見つかりません。')
        c.execute('UPDATE products SET code=?,name=?,color=?,supplier_id=?,active=?,updated_at=? WHERE id=?',
                  (code, name, color, supplier_id, active, t, pid))
        existing = {r['label']: r['id'] for r in c.execute('SELECT id,label FROM product_sizes WHERE product_id=?', (pid,))}
        for label in existing:
            if label not in labels and c.execute(
                '''SELECT 1 FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
                   WHERE cp.product_id=? AND l.size_label=?''', (pid, label)).fetchone():
                raise ValueError(f'サイズ「{label}」には在庫の履歴があるため外せません。')
        for label, sid in existing.items():
            if label not in labels:
                c.execute('DELETE FROM product_sizes WHERE id=?', (sid,))
        for i, label in enumerate(labels):
            if label in existing:
                c.execute('UPDATE product_sizes SET sort_order=? WHERE id=?', (i, existing[label]))
            else:
                c.execute('INSERT INTO product_sizes(product_id,label,sort_order) VALUES(?,?,?)', (pid, label, i))
        current = {(r['customer_id'], r['embroidery']): r for r in c.execute(
            'SELECT * FROM customer_products WHERE product_id=?', (pid,))}
        incoming = {(link['customerId'], link['embroidery']) for link in links}
        for key, row in current.items():
            if key not in incoming and c.execute('SELECT 1 FROM lots WHERE customer_product_id=?', (row['id'],)).fetchone():
                raise ValueError('在庫の履歴がある顧客・加工仕様は外せません。使用停止にして残してください。')
        for key, row in current.items():
            if key not in incoming:
                c.execute('DELETE FROM customer_products WHERE id=?', (row['id'],))
        c.execute('UPDATE customer_products SET is_standard=0 WHERE product_id=?', (pid,))
        for link in links:
            key = (link['customerId'], link['embroidery'])
            if key in current:
                c.execute('UPDATE customer_products SET is_standard=?,active=?,updated_at=? WHERE id=?',
                          (link['isStandard'], link['active'], t, current[key]['id']))
            else:
                c.execute('''INSERT INTO customer_products(customer_id,product_id,embroidery,is_standard,active,created_at,updated_at)
                             VALUES(?,?,?,?,?,?,?)''',
                          (link['customerId'], pid, link['embroidery'], link['isStandard'], link['active'], t, t))
    else:
        c.execute('INSERT INTO products(code,name,color,supplier_id,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                  (code, name, color, supplier_id, active, t, t))
        pid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        for i, label in enumerate(labels):
            c.execute('INSERT INTO product_sizes(product_id,label,sort_order) VALUES(?,?,?)', (pid, label, i))
        for link in links:
            c.execute('''INSERT INTO customer_products(customer_id,product_id,embroidery,is_standard,active,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,?)''',
                      (link['customerId'], pid, link['embroidery'], link['isStandard'], link['active'], t, t))
    return product_row(c, pid)


def _site_id(value):
    if value in ('', None):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError('拠点を選んでください。')


def location_has_stock(c, lid):
    """この場所に紐付く在庫ロットがあれば True（数量0でも行があれば削除不可）。"""
    return c.execute('SELECT 1 FROM lots WHERE location_id=? LIMIT 1', (lid,)).fetchone() is not None


def _insert_shelf(c, site_id, shelf_name, active, t):
    clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id=?', (shelf_name, site_id)).fetchone()
    if clash:
        raise ValueError('同じ棚・区画名がこの拠点に既にあります。新しい場所を増やさず、一覧から選んでください。')
    c.execute('INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,?,?,?)',
              (shelf_name, site_id, active, t, t))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _rename_shelf(c, shelf_id, site_id, shelf_name, active, t):
    row = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (shelf_id,)).fetchone()
    if not row or row['parent_id'] != site_id:
        raise ValueError('棚・区画が見つかりません。')
    clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id=? AND id<>?',
                      (shelf_name, site_id, shelf_id)).fetchone()
    if clash:
        raise ValueError('同じ棚・区画名がこの拠点に既にあります。一覧から選んで修正してください。')
    # 親拠点は変えない（名称変更と在庫の移動を混ぜない）
    c.execute('UPDATE locations SET name=?,active=?,updated_at=? WHERE id=?', (shelf_name, active, t, shelf_id))
    return shelf_id


def _delete_shelf(c, shelf_id, site_id):
    row = c.execute('SELECT id,parent_id,name FROM locations WHERE id=?', (shelf_id,)).fetchone()
    if not row or row['parent_id'] != site_id:
        raise ValueError('棚・区画が見つかりません。')
    if location_has_stock(c, shelf_id):
        raise ValueError(
            f'「{row["name"]}」には在庫が紐付いているため削除できません。'
            '在庫を別の場所へ移すか、使い切ってから削除してください。'
        )
    c.execute('DELETE FROM locations WHERE id=?', (shelf_id,))


def _parse_shelves(body):
    """shelves キーがあれば正規化した一覧を返す。無ければ None（単一棚API）。"""
    if 'shelves' not in body:
        return None
    raw = body.get('shelves')
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('棚・区画の一覧の形式が正しくありません。')
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError('棚・区画の一覧の形式が正しくありません。')
        shelf_id = entry.get('id')
        if shelf_id in ('', None):
            shelf_id = None
        elif isinstance(shelf_id, int):
            pass
        elif isinstance(shelf_id, str) and shelf_id.isdigit():
            shelf_id = int(shelf_id)
        else:
            raise ValueError('棚・区画が見つかりません。')
        remove = entry.get('remove') in (True, 1, '1')
        name = optional(entry.get('name', entry.get('shelfName')), '棚・区画名', 40)
        items.append({'id': shelf_id, 'name': name, 'remove': remove})
    return items


def _apply_shelves(c, site_id, shelves, active, t):
    """拠点下の棚を一括で追加・改名・削除。空欄の新規行は無視する。"""
    pending = []
    seen_names = {}
    for item in shelves:
        if item['remove']:
            if item['id'] is None:
                continue
            pending.append(item)
            continue
        if not item['name']:
            if item['id'] is not None:
                raise ValueError('棚・区画名を入力してください。')
            continue
        if item['name'] in seen_names:
            raise ValueError('同じ棚・区画名がこの拠点に既にあります。新しい場所を増やさず、一覧から選んでください。')
        seen_names[item['name']] = item
        pending.append(item)

    for item in pending:
        if item['remove']:
            _delete_shelf(c, item['id'], site_id)
            continue
        if item['id'] is not None:
            shelf_active = c.execute('SELECT active FROM locations WHERE id=?', (item['id'],)).fetchone()
            keep_active = active if shelf_active is None else shelf_active['active']
            _rename_shelf(c, item['id'], site_id, item['name'], keep_active, t)
        else:
            _insert_shelf(c, site_id, item['name'], active, t)


def _site_result(c, site_id):
    result = location_row(c, site_id)
    result['shelves'] = [
        location_row(c, r['id'])
        for r in c.execute('SELECT id FROM locations WHERE parent_id=? ORDER BY id', (site_id,))
    ]
    return result


def _resolve_or_create_site(c, body, active, t):
    """編集・既存選択・新規作成のいずれかで拠点IDを返す。"""
    lid = body.get('id')
    if lid:
        row = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (lid,)).fetchone()
        if not row:
            raise ValueError('保管場所が見つかりません。')
        if row['parent_id'] is not None:
            site_id = row['parent_id']
        else:
            site_id = lid
            name = need(body.get('siteName', body.get('name')), '拠点名', 40)
            clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL AND id<>?',
                              (name, site_id)).fetchone()
            if clash:
                raise ValueError('同じ拠点名が既にあります。一覧から選んで修正してください。')
            c.execute('UPDATE locations SET name=?,active=?,updated_at=? WHERE id=?', (name, active, t, site_id))
        return site_id

    site_id = _site_id(body.get('siteId'))
    if site_id is not None:
        parent = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (site_id,)).fetchone()
        if not parent or parent['parent_id'] is not None:
            raise ValueError('棚・区画は、登録済みの拠点の下にだけ作れます。')
        if body.get('siteName') not in ('', None):
            name = need(body.get('siteName'), '拠点名', 40)
            clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL AND id<>?',
                              (name, site_id)).fetchone()
            if clash:
                raise ValueError('同じ拠点名が既にあります。一覧から選んで修正してください。')
            c.execute('UPDATE locations SET name=?,updated_at=? WHERE id=?', (name, t, site_id))
        return site_id

    site_name = need(body.get('siteName', body.get('name')), '拠点名', 40)
    existing = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL', (site_name,)).fetchone()
    if existing:
        raise ValueError('同じ拠点名が既にあります。一覧からその拠点を選んで、棚・区画を追加してください。')
    c.execute('INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,?,?,?)',
              (site_name, None, active, t, t))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def save_location(c, body):
    """拠点名＋棚・区画。shelves 一覧があれば一括保存。単一 shelfName も互換維持。"""
    active = flag(body.get('active', True))
    t = stamp()
    shelves = _parse_shelves(body)

    if shelves is not None:
        site_id = _resolve_or_create_site(c, body, active, t)
        _apply_shelves(c, site_id, shelves, active, t)
        return _site_result(c, site_id)

    lid = body.get('id')
    if lid:
        row = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (lid,)).fetchone()
        if not row:
            raise ValueError('保管場所が見つかりません。')
        if row['parent_id'] is None:
            name = need(body.get('siteName', body.get('name')), '拠点名', 40)
            clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL AND id<>?',
                              (name, lid)).fetchone()
            if clash:
                raise ValueError('同じ拠点名が既にあります。一覧から選んで修正してください。')
            c.execute('UPDATE locations SET name=?,active=?,updated_at=? WHERE id=?', (name, active, t, lid))
        else:
            name = need(body.get('shelfName', body.get('name')), '棚・区画名', 40)
            clash = c.execute('SELECT id FROM locations WHERE name=? AND parent_id=? AND id<>?',
                              (name, row['parent_id'], lid)).fetchone()
            if clash:
                raise ValueError('同じ棚・区画名がこの拠点に既にあります。一覧から選んで修正してください。')
            # 親拠点は変えない（名称変更と在庫の移動を混ぜない）
            c.execute('UPDATE locations SET name=?,active=?,updated_at=? WHERE id=?', (name, active, t, lid))
        return location_row(c, lid)

    site_id = _site_id(body.get('siteId'))
    shelf_name = optional(body.get('shelfName'), '棚・区画名', 40)
    if site_id is not None:
        parent = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (site_id,)).fetchone()
        if not parent or parent['parent_id'] is not None:
            raise ValueError('棚・区画は、登録済みの拠点の下にだけ作れます。')
        if not shelf_name:
            raise ValueError('棚・区画名を入力してください。')
        lid = _insert_shelf(c, site_id, shelf_name, active, t)
        return location_row(c, lid)

    site_name = need(body.get('siteName', body.get('name')), '拠点名', 40)
    # 旧API互換: parentId 付きの name だけ渡された場合は棚として登録
    parent_id = body.get('parentId')
    if parent_id not in ('', None) and 'siteId' not in body and 'siteName' not in body and 'shelfName' not in body:
        parent_id = _site_id(parent_id)
        parent = c.execute('SELECT id,parent_id FROM locations WHERE id=?', (parent_id,)).fetchone()
        if not parent or parent['parent_id'] is not None:
            raise ValueError('棚・区画は、登録済みの拠点の下にだけ作れます。')
        lid = _insert_shelf(c, parent_id, site_name, active, t)
        return location_row(c, lid)

    existing = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL', (site_name,)).fetchone()
    if existing:
        if shelf_name:
            raise ValueError('同じ拠点名が既にあります。一覧からその拠点を選んで、棚・区画を追加してください。')
        raise ValueError('同じ拠点名が既にあります。新しい場所を増やさず、一覧から選んでください。')
    c.execute('INSERT INTO locations(name,parent_id,active,created_at,updated_at) VALUES(?,?,?,?,?)',
              (site_name, None, active, t, t))
    site_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    if not shelf_name:
        return location_row(c, site_id)
    lid = _insert_shelf(c, site_id, shelf_name, active, t)
    return location_row(c, lid)


def _token(value):
    token = text(value)
    if len(token) < 8 or len(token) > 80:
        raise ValueError('登録の確認が途中で切れました。もう一度、内容を確認して保存してください。')
    return token


def register_opening(c, body):
    token = _token(body.get('token'))
    existing = c.execute('SELECT id FROM opening_batches WHERE client_token=?', (token,)).fetchone()
    if existing:
        return batch_result(c, existing['id'], True)
    cp_id = body.get('customerProductId')
    if not isinstance(cp_id, int):
        raise ValueError('顧客と商品の取扱を選んでください。')
    cp = c.execute('''SELECT cp.*, p.active AS product_active, cu.active AS customer_active
                      FROM customer_products cp
                      JOIN products p ON p.id=cp.product_id
                      JOIN customers cu ON cu.id=cp.customer_id
                      WHERE cp.id=?''', (cp_id,)).fetchone()
    if not cp or not cp['active'] or not cp['product_active'] or not cp['customer_active']:
        raise ValueError('使用中の顧客・商品・加工仕様を選んでください。')
    location_id = body.get('locationId')
    loc = c.execute('SELECT * FROM locations WHERE id=?', (location_id,)).fetchone() if isinstance(location_id, int) else None
    if not loc or not loc['active']:
        raise ValueError('保管場所を、登録済みの場所から選んでください。')
    basis = parse_date(body.get('basisDate'), '在庫基準日')
    unknown = body.get('receivedUnknown') in (True, 1, '1')
    if unknown:
        if text(body.get('receivedOn')):
            raise ValueError('入荷日が不明な場合は、架空の日付を入れず「入荷日不明」のままにしてください。')
        payout = parse_qty(body.get('payoutOrder'), '払出し順', allow_zero=False)
        received = None
    else:
        received = parse_date(body.get('receivedOn'), '入荷日')
        payout = 0
    sizes = {r['label'] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (cp['product_id'],))}
    cleaned = []
    seen = set()
    for raw in body.get('lines') or []:
        if not isinstance(raw, dict):
            raise ValueError('数量の表が不正です。')
        size = raw.get('size')
        if not isinstance(size, str) or size.strip() not in sizes or size != size.strip():
            raise ValueError('商品に登録したサイズだけを入力してください。新しいサイズは商品台帳で追加します。')
        qty = parse_qty(raw.get('qty'), f'{size}の数量')
        cost = parse_money(raw.get('unitCost'), f'{size}の仕入単価')
        key = (size, cost)
        if key in seen:
            raise ValueError('同じサイズで同じ仕入単価の行が重複しています。数量を1行にまとめてください。単価が違う場合は行を分けたままにしてください。')
        seen.add(key)
        cleaned.append((size, qty, cost))
    if not cleaned:
        raise ValueError('数量のある行がありません。')
    t = stamp()
    c.execute('INSERT INTO opening_batches(client_token,created_at) VALUES(?,?)', (token, t))
    batch_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    for size, qty, cost in cleaned:
        c.execute('''INSERT INTO lots(batch_id,customer_product_id,size_label,location_id,unit_cost,qty_received,qty_remaining,
                     basis_date,received_on,received_unknown,payout_order,condition,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,'new',?,?)''',
                  (batch_id, cp_id, size, location_id, cost, qty, qty, basis, received, 1 if unknown else 0, payout, t, t))
        lot_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,location_id,note,at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (lot_id, 'opening', qty, cost, 0, qty, None, cost, location_id, '初期在庫', t))
    return batch_result(c, batch_id, False)


def correct_opening(c, body):
    lot_id = body.get('lotId')
    if not isinstance(lot_id, int):
        raise ValueError('訂正する在庫を選んでください。')
    lot = c.execute('SELECT * FROM lots WHERE id=?', (lot_id,)).fetchone()
    if not lot:
        raise ValueError('在庫が見つかりません。')
    kinds = {r['kind'] for r in c.execute('SELECT DISTINCT kind FROM moves WHERE lot_id=?', (lot_id,))}
    if not kinds or not kinds <= {'opening', 'opening_correction'}:
        raise ValueError('入荷・出荷・移動が入った在庫は、この初期在庫の訂正では変えられません。')
    if lot['qty_remaining'] != lot['qty_received']:
        raise ValueError('残数量と入荷数量が一致しないため、訂正を中止しました。金額は変えていません。')
    qty = parse_qty(body.get('qty'), '訂正後の数量', allow_zero=True)
    cost = parse_money(body.get('unitCost'), '訂正後の仕入単価')
    reason = need(body.get('reason'), '訂正の理由', 200)
    if qty == lot['qty_remaining'] and cost == lot['unit_cost']:
        raise ValueError('数量も単価も変わっていません。')
    t = stamp()
    before_qty = lot['qty_remaining']
    before_cost = lot['unit_cost']
    c.execute('UPDATE lots SET qty_received=?,qty_remaining=?,unit_cost=?,updated_at=? WHERE id=?',
              (qty, qty, cost, t, lot_id))
    c.execute('''INSERT INTO moves(lot_id,kind,qty_delta,unit_cost,qty_before,qty_after,cost_before,cost_after,location_id,note,at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
              (lot_id, 'opening_correction', qty - before_qty, cost, before_qty, qty, before_cost, cost, lot['location_id'], reason, t))
    fresh = c.execute('SELECT qty_remaining,unit_cost FROM lots WHERE id=?', (lot_id,)).fetchone()
    if fresh['qty_remaining'] != qty or fresh['unit_cost'] != cost or fresh['qty_remaining'] * fresh['unit_cost'] != qty * cost:
        raise ValueError('訂正後の数量と金額が一致しないため、保存を中止しました。')
    return {'lotId': lot_id, 'beforeQty': before_qty, 'afterQty': qty, 'beforeCost': before_cost, 'afterCost': cost,
            'beforeAmount': before_qty * before_cost, 'afterAmount': qty * cost}


def batch_result(c, batch_id, already):
    rows = c.execute('SELECT size_label,unit_cost,qty_remaining FROM lots WHERE batch_id=? ORDER BY id', (batch_id,)).fetchall()
    lines = [{'size': r['size_label'], 'unitCost': r['unit_cost'], 'qty': r['qty_remaining'], 'amount': r['qty_remaining'] * r['unit_cost']} for r in rows]
    return {'already': already, 'batchId': batch_id, 'lines': lines,
            'totalQty': sum(x['qty'] for x in lines), 'totalAmount': sum(x['amount'] for x in lines)}


def customer_row(c, cid):
    r = c.execute('SELECT * FROM customers WHERE id=?', (cid,)).fetchone()
    return {'id': r['id'], 'name': r['name'], 'kana': r['kana'], 'address': r['address'], 'phone': r['phone'],
            'contact': r['contact'], 'active': bool(r['active'])}


def supplier_row(c, sid):
    r = c.execute('SELECT * FROM suppliers WHERE id=?', (sid,)).fetchone()
    return {'id': r['id'], 'name': r['name'], 'address': r['address'], 'phone': r['phone'],
            'contact': r['contact'], 'active': bool(r['active'])}


def product_row(c, pid):
    r = c.execute('''SELECT p.*, s.name AS supplier_name FROM products p
                     LEFT JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?''', (pid,)).fetchone()
    sizes = [x['label'] for x in c.execute('SELECT label FROM product_sizes WHERE product_id=? ORDER BY sort_order,id', (pid,))]
    links = []
    for x in c.execute('''SELECT cp.*, cu.name AS customer_name FROM customer_products cp
                          JOIN customers cu ON cu.id=cp.customer_id WHERE cp.product_id=? ORDER BY cp.id''', (pid,)):
        links.append({'id': x['id'], 'customerId': x['customer_id'], 'customerName': x['customer_name'],
                      'embroidery': x['embroidery'], 'isStandard': bool(x['is_standard']), 'active': bool(x['active'])})
    return {'id': r['id'], 'code': r['code'], 'name': r['name'], 'color': r['color'], 'supplierId': r['supplier_id'],
            'supplierName': r['supplier_name'] or '', 'active': bool(r['active']), 'sizes': sizes, 'links': links}


def location_label(c, row):
    if row['parent_id']:
        parent = c.execute('SELECT name FROM locations WHERE id=?', (row['parent_id'],)).fetchone()
        return f"{parent['name']}／{row['name']}"
    return row['name']


def location_row(c, lid):
    r = c.execute('SELECT * FROM locations WHERE id=?', (lid,)).fetchone()
    return {
        'id': r['id'], 'name': r['name'], 'parentId': r['parent_id'], 'active': bool(r['active']),
        'label': location_label(c, r), 'hasStock': location_has_stock(c, lid),
    }


SHELF_PREVIEW_LIMIT = 3
SITE_ONLY_SHELF_LABEL = '棚指定なし'


def format_shelf_summary(shelves, *, preview_limit=SHELF_PREVIEW_LIMIT):
    """一覧用の棚・区画表示。棚が無ければ「棚なし」、多いときは先頭＋ほか○件。"""
    if not shelves:
        return '棚なし', 0
    names = []
    for shelf in shelves:
        name = shelf.get('name') or ''
        if shelf.get('active', True) is False:
            name = f'{name}（停止）'
        names.append(name)
    if len(names) <= preview_limit:
        return '、'.join(names), 0
    extra = len(names) - preview_limit
    return '、'.join(names[:preview_limit]) + f'、ほか{extra}件', extra


def group_locations_by_site(locations, *, q='', include_stopped=False, preview_limit=SHELF_PREVIEW_LIMIT):
    """拠点ごとに1行の一覧へまとめる。棚名検索は親拠点行を返す（データは変更しない）。"""
    sites = [loc for loc in locations if loc.get('parentId') is None]
    by_parent = {}
    for loc in locations:
        parent_id = loc.get('parentId')
        if parent_id is not None:
            by_parent.setdefault(parent_id, []).append(loc)

    query = (q or '').strip()
    rows = []
    for site in sites:
        shelves = list(by_parent.get(site['id'], []))
        if not include_stopped:
            if not site.get('active', True):
                continue
            shelves = [s for s in shelves if s.get('active', True)]
        if query:
            site_hit = query in (site.get('name') or '') or query in (site.get('label') or '')
            shelf_hit = any(
                query in (s.get('name') or '') or query in (s.get('label') or '')
                for s in shelves
            )
            if not site_hit and not shelf_hit:
                continue
        summary, extra = format_shelf_summary(shelves, preview_limit=preview_limit)
        rows.append({
            'id': site['id'],
            'name': site['name'],
            'active': bool(site.get('active', True)),
            'parentId': None,
            'label': site.get('label') or site['name'],
            'hasStock': bool(site.get('hasStock')),
            'shelves': shelves,
            'shelfSummary': summary,
            'shelfExtraCount': extra,
        })
    return rows


def opening_picker_shelves(locations, site_id, *, current_location_id=None, include_stopped=False):
    """初期在庫などの拠点→棚ピッカー用。戻り値は (options, require_shelf)。
    棚が無い拠点は require_shelf=False（拠点自体が場所）。
    拠点直下に在庫がある既存参照は「棚指定なし」として残す。
    """
    if site_id in ('', None):
        return [], False
    site_id = int(site_id)
    shelves = [loc for loc in locations if loc.get('parentId') == site_id]
    if not include_stopped:
        shelves = [s for s in shelves if s.get('active', True)]
    if not shelves:
        return [], False
    options = [
        {
            'id': s['id'],
            'name': s['name'],
            'label': s.get('label') or s['name'],
            'siteOnly': False,
            'active': bool(s.get('active', True)),
        }
        for s in shelves
    ]
    if current_location_id not in ('', None) and int(current_location_id) == site_id:
        options.insert(0, {
            'id': site_id,
            'name': SITE_ONLY_SHELF_LABEL,
            'label': SITE_ONLY_SHELF_LABEL,
            'siteOnly': True,
            'active': True,
        })
    return options, True


def resolve_opening_location(site_id, shelf_id, locations):
    """拠点＋棚選択から保存用 locationId を決める。既存の拠点直下参照は維持する。"""
    if site_id in ('', None):
        raise ValueError('保管場所を、登録済みの場所から選んでください。')
    site_id = int(site_id)
    site = next((loc for loc in locations if loc['id'] == site_id and loc.get('parentId') is None), None)
    if not site:
        raise ValueError('保管場所を、登録済みの場所から選んでください。')
    active_shelves = [
        loc for loc in locations
        if loc.get('parentId') == site_id and loc.get('active', True)
    ]
    if not active_shelves:
        return site_id
    if shelf_id in ('', None):
        raise ValueError('棚・区画を選んでください。')
    shelf_id = int(shelf_id)
    if shelf_id == site_id:
        # 既存の拠点直下在庫（棚指定なし）を壊さない
        return site_id
    shelf = next(
        (loc for loc in locations if loc['id'] == shelf_id and loc.get('parentId') == site_id),
        None,
    )
    if not shelf:
        raise ValueError('保管場所を、登録済みの場所から選んでください。')
    return shelf_id


def bootstrap(c):
    customers = [customer_row(c, r['id']) for r in c.execute('SELECT id FROM customers ORDER BY id')]
    suppliers = [supplier_row(c, r['id']) for r in c.execute('SELECT id FROM suppliers ORDER BY id')]
    products = [product_row(c, r['id']) for r in c.execute('SELECT id FROM products ORDER BY id')]
    locations = [location_row(c, r['id']) for r in c.execute('SELECT id FROM locations ORDER BY parent_id IS NOT NULL, id')]
    return {'customers': customers, 'suppliers': suppliers, 'products': products, 'locations': locations}


def opening_entries(c):
    """Registered 初期在庫 lots from existing stock rows (no duplicate display records)."""
    sql = '''SELECT l.*, cp.customer_id, cp.product_id, cp.embroidery,
                    cu.name AS customer_name,
                    p.code, p.name AS product_name, p.color,
                    loc.name AS location_name, loc.parent_id
             FROM lots l
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             JOIN locations loc ON loc.id=l.location_id
             WHERE l.condition='new'
               AND EXISTS (SELECT 1 FROM moves m WHERE m.lot_id=l.id AND m.kind='opening')
             ORDER BY l.basis_date DESC, l.id DESC'''
    rows = []
    for r in c.execute(sql):
        loc = {'name': r['location_name'], 'parent_id': r['parent_id']}
        rows.append({
            'id': r['id'],
            'customerId': r['customer_id'],
            'customerName': r['customer_name'],
            'productId': r['product_id'],
            'code': r['code'],
            'productName': r['product_name'],
            'color': r['color'],
            'embroidery': r['embroidery'],
            'embroideryLabel': r['embroidery'] or '刺繍なし',
            'size': r['size_label'],
            'locationId': r['location_id'],
            'locationLabel': location_label(c, loc),
            'unitCost': r['unit_cost'],
            'qty': r['qty_received'],
            'basisDate': r['basis_date'],
            'receivedOn': r['received_on'],
            'receivedUnknown': bool(r['received_unknown']),
            'payoutOrder': r['payout_order'],
        })
    return {'entries': rows}


def stock_query(c, args):
    sql = '''SELECT l.*, cp.customer_id, cp.product_id, cp.embroidery, cp.is_standard, cp.active AS handle_active,
                    cu.name AS customer_name, cu.active AS customer_active,
                    p.code, p.name AS product_name, p.color, p.active AS product_active, p.supplier_id,
                    s.name AS supplier_name, loc.name AS location_name, loc.parent_id
             FROM lots l
             JOIN customer_products cp ON cp.id=l.customer_product_id
             JOIN customers cu ON cu.id=cp.customer_id
             JOIN products p ON p.id=cp.product_id
             LEFT JOIN suppliers s ON s.id=p.supplier_id
             JOIN locations loc ON loc.id=l.location_id
             WHERE l.condition='new' '''
    params = []
    if args.get('customerId'):
        sql += ' AND cp.customer_id=?'
        params.append(int(args['customerId']))
    if args.get('productId'):
        sql += ' AND cp.product_id=?'
        params.append(int(args['productId']))
    if args.get('size'):
        sql += ' AND l.size_label=?'
        params.append(args['size'])
    spec = args.get('spec')
    if spec == 'none':
        sql += " AND cp.embroidery=''"
    elif spec:
        sql += ' AND cp.embroidery=?'
        params.append(spec)
    if args.get('locationId'):
        sql += ' AND (l.location_id=? OR loc.parent_id=?)'
        params.append(int(args['locationId']))
        params.append(int(args['locationId']))
    sql += ' ORDER BY cu.name, p.code, p.id, cp.embroidery, l.size_label, l.id'
    lots = []
    for r in c.execute(sql, params):
        loc = {'name': r['location_name'], 'parent_id': r['parent_id']}
        kinds = {m['kind'] for m in c.execute('SELECT DISTINCT kind FROM moves WHERE lot_id=?', (r['id'],))}
        correctable = kinds <= {'opening', 'opening_correction'} and r['qty_remaining'] == r['qty_received']
        lots.append({
            'id': r['id'], 'customerId': r['customer_id'], 'customerName': r['customer_name'],
            'customerActive': bool(r['customer_active']), 'productId': r['product_id'], 'code': r['code'],
            'productName': r['product_name'], 'color': r['color'], 'supplierName': r['supplier_name'] or '',
            'embroidery': r['embroidery'], 'embroideryLabel': r['embroidery'] or '刺繍なし',
            'isStandard': bool(r['is_standard']), 'size': r['size_label'], 'locationId': r['location_id'],
            'locationLabel': location_label(c, loc), 'unitCost': r['unit_cost'], 'qty': r['qty_remaining'],
            'amount': r['qty_remaining'] * r['unit_cost'], 'basisDate': r['basis_date'],
            'receivedOn': r['received_on'], 'receivedUnknown': bool(r['received_unknown']),
            'payoutOrder': r['payout_order'], 'condition': r['condition'], 'correctable': correctable,
        })
    group = args.get('group') or 'lot'
    size_orders = product_size_orders(c, {lot['productId'] for lot in lots}) if group == 'product' else {}
    groups = group_lots(lots, group, size_orders=size_orders)
    return {'lots': lots, 'groups': groups, 'group': group,
            'totalQty': sum(x['qty'] for x in lots), 'totalAmount': sum(x['amount'] for x in lots),
            'amountLabel': '仕入原価ベースの在庫金額'}


def product_size_orders(c, product_ids):
    """Map productId -> {size_label: sort_order} for registered sizes."""
    orders = {}
    for pid in product_ids:
        orders[pid] = {
            r['label']: r['sort_order']
            for r in c.execute(
                'SELECT label, sort_order FROM product_sizes WHERE product_id=? ORDER BY sort_order, id',
                (pid,),
            )
        }
    return orders


def product_heading(lot):
    parts = [lot['code'], lot['productName'], lot['color']]
    return '　'.join(p for p in parts if p)


def group_lots_by_product(lots, size_orders=None):
    """One heading per 商品ID; rows are 顧客・加工仕様・サイズ・保管場所 with unit-price breakdown."""
    size_orders = size_orders or {}
    products = {}
    for lot in lots:
        pid = lot['productId']
        bucket = products.setdefault(pid, {
            'productId': pid,
            'code': lot['code'],
            'productName': lot['productName'],
            'color': lot['color'],
            'title': product_heading(lot),
            'qty': 0,
            'amount': 0,
            'rows': {},
        })
        bucket['qty'] += lot['qty']
        bucket['amount'] += lot['amount']
        row_key = (lot['customerId'], lot['embroidery'], lot['size'], lot['locationId'])
        row = bucket['rows'].setdefault(row_key, {
            'customerId': lot['customerId'],
            'customerName': lot['customerName'],
            'embroidery': lot['embroidery'],
            'embroideryLabel': lot['embroideryLabel'],
            'size': lot['size'],
            'locationId': lot['locationId'],
            'locationLabel': lot['locationLabel'],
            'qty': 0,
            'amount': 0,
            'prices': {},
        })
        row['qty'] += lot['qty']
        row['amount'] += lot['amount']
        price = row['prices'].setdefault(lot['unitCost'], {
            'unitCost': lot['unitCost'], 'qty': 0, 'amount': 0,
        })
        price['qty'] += lot['qty']
        price['amount'] += lot['amount']

    result = []
    for pid, bucket in products.items():
        order = size_orders.get(pid, {})
        rows = []
        for row in bucket['rows'].values():
            prices = sorted(row['prices'].values(), key=lambda p: (p['unitCost'], p['qty']))
            rows.append({
                'customerId': row['customerId'],
                'customerName': row['customerName'],
                'embroideryLabel': row['embroideryLabel'],
                'size': row['size'],
                'locationId': row['locationId'],
                'locationLabel': row['locationLabel'],
                'qty': row['qty'],
                'amount': row['amount'],
                'prices': prices,
            })
        rows.sort(key=lambda r: (
            r['customerName'],
            order.get(r['size'], 10_000),
            r['size'],
            r['embroideryLabel'],
            r['locationLabel'],
            r['locationId'],
        ))
        result.append({
            'title': bucket['title'],
            'productId': bucket['productId'],
            'code': bucket['code'],
            'productName': bucket['productName'],
            'color': bucket['color'],
            'qty': bucket['qty'],
            'amount': bucket['amount'],
            'rows': rows,
        })
    result.sort(key=lambda g: (g['code'], g['productId']))
    return result


def group_lots(lots, group, size_orders=None):
    if group == 'lot':
        return []
    if group == 'product':
        return group_lots_by_product(lots, size_orders=size_orders)
    buckets = {}
    for lot in lots:
        if group == 'customer':
            key = (lot['customerId'],)
            title = lot['customerName']
        elif group == 'location':
            key = (lot['locationId'],)
            title = lot['locationLabel']
        else:
            raise ValueError('集計の種類が不正です。')
        bucket = buckets.setdefault(key, {'title': title, 'qty': 0, 'amount': 0, 'parts': []})
        bucket['qty'] += lot['qty']
        bucket['amount'] += lot['amount']
        part_key = (lot['unitCost'], lot['locationLabel'], lot['code'], lot['size'], lot['embroideryLabel'], lot['customerName'])
        found = next((p for p in bucket['parts'] if p['key'] == part_key), None)
        if found:
            found['qty'] += lot['qty']
            found['amount'] += lot['amount']
        else:
            bucket['parts'].append({'key': part_key, 'unitCost': lot['unitCost'], 'qty': lot['qty'], 'amount': lot['amount'],
                                    'locationLabel': lot['locationLabel'], 'customerName': lot['customerName'],
                                    'code': lot['code'], 'productId': lot['productId'], 'productName': lot['productName'], 'size': lot['size'],
                                    'embroideryLabel': lot['embroideryLabel']})
    result = []
    for bucket in buckets.values():
        result.append({'title': bucket['title'], 'qty': bucket['qty'], 'amount': bucket['amount'],
                       'parts': [{k: v for k, v in p.items() if k != 'key'} for p in bucket['parts']]})
    return result


def issue_sequence(lots):
    """将来の出荷が使う払出し順。入荷日不明を先にし、単価では並べない。"""
    def key(r):
        if r['received_unknown']:
            return (0, r['payout_order'], r['id'])
        return (1, r['received_on'], r['id'])
    return sorted(lots, key=key)


def lot_history(c, lot_id):
    rows = []
    for r in c.execute('SELECT * FROM moves WHERE lot_id=? ORDER BY id', (lot_id,)):
        rows.append({'id': r['id'], 'kind': r['kind'], 'at': r['at'], 'qtyBefore': r['qty_before'], 'qtyAfter': r['qty_after'],
                     'costBefore': r['cost_before'], 'costAfter': r['cost_after'], 'note': r['note'],
                     'amountBefore': None if r['qty_before'] is None or r['cost_before'] is None else r['qty_before'] * r['cost_before'],
                     'amountAfter': None if r['qty_after'] is None or r['cost_after'] is None else r['qty_after'] * r['cost_after']})
    return rows
