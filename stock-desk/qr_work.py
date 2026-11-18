"""Saved QR collection jobs. Only explicit confirmation changes inventory."""
import json
import uuid
import hashlib
from contextlib import closing
import stock
import stock_qr


def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS qr_work_jobs (
        id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL,
        status TEXT NOT NULL, version INTEGER NOT NULL, direction TEXT NOT NULL,
        started_at TEXT NOT NULL, completed_at TEXT, confirmed_at TEXT,
        confirmed_by TEXT, lines TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS qr_work_scans (
        token TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES qr_work_jobs(id))''')
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS qr_work_one_active ON qr_work_jobs(actor_id) WHERE status='collecting'")
    c.execute('''CREATE TABLE IF NOT EXISTS qr_work_receipts (
        id TEXT PRIMARY KEY REFERENCES qr_work_jobs(id), actor_id TEXT NOT NULL, digest TEXT NOT NULL)''')


def actor(body):
    a = body.get('_actor') or {}
    if a.get('id') is None:
        raise PermissionError('ログインしてから作業してください。')
    return str(a['id']), str(a.get('name') or '')


def public(row):
    d = dict(row)
    d['lines'] = json.loads(d['lines'])
    return d


def state(c, body):
    schema(c)
    who, _ = actor(body)
    inbox_only = body.get('inbox') is True
    legacy_only = body.get('legacyOnly') is True
    jobs = [public(r) for r in c.execute('''SELECT * FROM qr_work_jobs
        WHERE status!='collecting' OR (actor_id=? AND ?=0)
        ORDER BY status='collecting' DESC, started_at DESC LIMIT 200''', (who, int(inbox_only)))] if not legacy_only else [
        public(r) for r in c.execute("SELECT * FROM qr_work_jobs WHERE status='collecting' AND actor_id=?", (who,))]
    locations = [{'id': r['id'], 'name': stock.location_label(c, r)}
                 for r in c.execute('SELECT * FROM locations WHERE active=1 ORDER BY id')] if not legacy_only else []
    return {'jobs': jobs, 'locations': locations, 'actorId': who,
            'canConfirm': bool(body.get('_canWrite'))}


def catalog(c, body):
    """Only QR identity and garment attributes, never live stock quantities."""
    actor(body)
    stock_qr.ensure_schema(c)
    import qr_link_import
    qr_link_import.ensure_schema(c)
    handles = {}
    sizes_by_product = {}
    for row in c.execute('SELECT product_id,label FROM product_sizes ORDER BY product_id,sort_order'):
        sizes_by_product.setdefault(row['product_id'], []).append(row['label'])
    for cp in c.execute('''SELECT cp.*,cu.name customer_name,p.code,p.name product_name,p.color
        FROM customer_products cp JOIN customers cu ON cu.id=cp.customer_id
        JOIN products p ON p.id=cp.product_id
        WHERE cp.active=1 AND cu.active=1 AND p.active=1 AND p.merged_into_id IS NULL'''):
        sizes = sizes_by_product.get(cp['product_id'], [])
        handles[cp['id']] = {'customerProductId': cp['id'], 'customerName': cp['customer_name'],
            'code': cp['code'], 'productName': cp['product_name'], 'color': cp['color'],
            'spec': stock.embroidery_label(cp['embroidery']), 'sizes': sizes,
            'size': '', 'condition': '', 'locationId': None, 'unitCost': '', 'lots': []}
    entries = []
    for row in c.execute('SELECT * FROM stock_qr_codes'):
        target = handles.get(row['customer_product_id'])
        if target:
            entries.append({'qr': row['public_id'], 'kind': 'new', 'target': {
                **target, 'size': row['size_label'], 'condition': row['condition']}})
    for row in c.execute('SELECT * FROM qr_stock_links'):
        target = handles.get(row['customer_product_id'])
        if target:
            entries.append({'qr': str(row['legacy_stock_id']), 'kind': 'legacy',
                'customer': row['legacy_customer_id'] or '', 'maker': row['legacy_maker_id'] or '',
                'target': {**target, 'size': row['size_label'] or '', 'condition': row['condition'] or '',
                           'locationId': row['location_id']}})
    revision = hashlib.sha256(json.dumps(entries, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if body.get('revision') == revision:
        return {'notModified': True, 'revision': revision, 'entryCount': len(entries), 'preparedAt': stock.stamp()}
    return {'entries': entries, 'revision': revision, 'entryCount': len(entries), 'preparedAt': stock.stamp()}


def submit(c, body):
    """Receive an immutable completed device job, resolve all targets on server."""
    schema(c)
    who, name = actor(body)
    job_id = str(uuid.UUID(str(body.get('id') or '')))
    rows = body.get('lines')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError('送信する制服の明細を確認してください（1～1000種類）。')
    normalized = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('送信する明細が不正です。')
        raw = stock_qr.parse_scan(row.get('qr'))
        qty = stock.parse_qty(row.get('qty'), allow_zero=True)
        count = stock.parse_qty(row.get('scanCount'))
        if raw in seen:
            raise ValueError('同じQRの明細は1行にまとめてください。')
        seen.add(raw)
        normalized.append({'qr': raw, 'qty': qty, 'scanCount': count})
    if not any(r['qty'] for r in normalized):
        raise ValueError('数量が1以上の明細を残してください。')
    # Store a receipt hash separately: a retry remains identical even after office edits.
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    receipt = c.execute('SELECT * FROM qr_work_receipts WHERE id=?', (job_id,)).fetchone()
    if receipt:
        if receipt['actor_id'] != who or receipt['digest'] != digest:
            raise ValueError('同じ作業番号で異なる内容が送信されました。元の作業を確認してください。')
        return {'job': _job(c, {'id': job_id}), 'duplicate': True}
    legacy = c.execute('SELECT * FROM qr_work_jobs WHERE id=?', (job_id,)).fetchone()
    if legacy and not (legacy['actor_id'] == who and legacy['status'] == 'collecting'
                       and type(body.get('sourceVersion')) is int and legacy['version'] == body['sourceVersion']):
        raise ValueError('元の作業が更新されたか、作業番号が重複しています。管理者に確認してください。端末の記録は残っています。')
    lines = []
    for row in normalized:
        found = stock_qr.read_scan(c, {'qr': row['qr']})
        link = found.get('link') or found
        if not found.get('found') or found.get('mismatch') or link.get('stale') or not link.get('customerProductId'):
            raise ValueError('対応が変わったQRが含まれています。商品登録を確認して再送信してください。端末の記録は残っています。')
        cp = stock.resolve_customer_product(c, link['customerProductId'])
        lots = found.get('lots') or []
        def single(field):
            values = {x.get(field) for x in lots}
            return next(iter(values)) if len(values) == 1 else None
        lines.append({'key': hashlib.sha256(row['qr'].encode()).hexdigest(),
            'qr': row['qr'], 'customerProductId': cp['id'], 'customerName': cp['customer_name'],
            'code': cp['code'], 'productName': cp['product_name'], 'color': cp['color'],
            'spec': stock.embroidery_label(cp['embroidery']),
            'size': link.get('sizeLabel') or found.get('size') or single('size') or '',
            'condition': link.get('condition') or found.get('condition') or single('condition') or '',
            'locationId': link.get('locationId') or single('locationId'), 'unitCost': '',
            'sizes': [r[0] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (cp['product_id'],))],
            'qty': row['qty'], 'scanCount': row['scanCount'], 'lots': []})
    # Device dates are display metadata only; validate them, never use for stock posting dates.
    def device_time(key):
        from datetime import datetime
        raw = body.get(key)
        if not isinstance(raw, str) or len(raw) > 40:
            raise ValueError('作業の開始・完了日時を確認してください。')
        datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return raw
    started, completed = device_time('startedAt'), device_time('completedAt')
    if legacy:
        c.execute("UPDATE qr_work_jobs SET status='completed',version=version+1,direction='outbound',completed_at=?,lines=? WHERE id=?",
                  (completed, json.dumps(lines, ensure_ascii=False), job_id))
    else:
        c.execute('''INSERT INTO qr_work_jobs(id,actor_id,actor_name,status,version,direction,
            started_at,completed_at,lines) VALUES(?,?,?,'completed',1,'outbound',?,?,?)''',
            (job_id, who, name, started, completed, json.dumps(lines, ensure_ascii=False)))
    c.execute('INSERT INTO qr_work_receipts(id,actor_id,digest) VALUES(?,?,?)', (job_id, who, digest))
    return {'job': _job(c, {'id': job_id}), 'duplicate': False}


def resolve(c, body):
    """Resolve an existing QR only: never issue a new identity or change stock."""
    actor(body)
    raw = stock_qr.parse_scan(body.get('qr'))
    found = stock_qr.read_scan(c, {'qr': raw})
    link = found.get('link') or found
    if not found.get('found') or found.get('mismatch') or link.get('stale') or not link.get('customerProductId'):
        raise ValueError('商品情報と照合できないQRです。QRの対応登録を確認してください。')
    cp = stock.resolve_customer_product(c, link['customerProductId'])
    kind = 'new' if stock_qr.is_new_qr(raw) else 'legacy'
    entry = {'kind': kind, 'qr': found.get('publicId') if kind == 'new' else str(found['legacyStockId']),
        'customer': link.get('legacyCustomerId') or '', 'maker': link.get('legacyMakerId') or '',
        'target': {'customerProductId': cp['id'], 'customerName': cp['customer_name'],
            'code': cp['code'], 'productName': cp['product_name'], 'color': cp['color'],
            'spec': stock.embroidery_label(cp['embroidery']),
            'size': link.get('sizeLabel') or found.get('size') or '',
            'condition': link.get('condition') or found.get('condition') or '',
            'sizes': [r[0] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=? ORDER BY sort_order', (cp['product_id'],))],
            'locationId': link.get('locationId'), 'unitCost': '', 'lots': []}}
    return {'entry': entry, 'qtyChanged': False}


def text_message(c, body, *, send=False):
    who, name = actor(body)
    c.execute('''CREATE TABLE IF NOT EXISTS qr_text_messages (
        id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL,
        body TEXT NOT NULL, received_at TEXT NOT NULL)''')
    if not send:
        return {'messages': [dict(r) for r in c.execute(
            'SELECT * FROM qr_text_messages WHERE actor_id=? ORDER BY received_at DESC,rowid DESC LIMIT 100', (who,))]}
    message_id = str(uuid.UUID(str(body.get('id') or '')))
    text = body.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 50000:
        raise ValueError('送信する文章を1～50000文字で入力してください。')
    old = c.execute('SELECT * FROM qr_text_messages WHERE id=?', (message_id,)).fetchone()
    if old:
        if old['actor_id'] != who or old['body'] != text:
            raise ValueError('同じ送信番号で異なる文章が送られました。元の文章を確認してください。')
        return {'message': dict(old), 'duplicate': True}
    c.execute('INSERT INTO qr_text_messages(id,actor_id,actor_name,body,received_at) VALUES(?,?,?,?,?)',
              (message_id, who, name, text, stock.stamp()))
    return {'message': dict(c.execute('SELECT * FROM qr_text_messages WHERE id=?', (message_id,)).fetchone()),
            'duplicate': False}


def _job(c, body):
    row = c.execute('SELECT * FROM qr_work_jobs WHERE id=?', (str(body.get('id') or ''),)).fetchone()
    if not row:
        raise ValueError('作業が見つかりません。一覧を更新してください。')
    return public(row)


def _editable(job, body):
    who, _ = actor(body)
    if job['status'] == 'confirmed':
        raise ValueError('在庫反映済みの作業は編集できません。')
    if job['actor_id'] != who and not (job['status'] == 'completed' and body.get('_canWrite')):
        raise PermissionError('読み取り中の作業は本人だけが編集できます。')
    if body.get('version') != job['version']:
        raise ValueError('別の画面で作業が更新されました。一覧を更新して確認してください。')


def _save(c, job):
    c.execute('''UPDATE qr_work_jobs SET lines=?, status=?, direction=?,
        completed_at=?, version=version+1 WHERE id=?''',
        (json.dumps(job['lines'], ensure_ascii=False), job['status'], job['direction'],
         job['completed_at'], job['id']))
    return public(c.execute('SELECT * FROM qr_work_jobs WHERE id=?', (job['id'],)).fetchone())


def scan(c, body):
    schema(c)
    who, name = actor(body)
    token = str(uuid.UUID(str(body.get('token') or '')))
    previous = c.execute('SELECT job_id FROM qr_work_scans WHERE token=?', (token,)).fetchone()
    if previous:
        old = _job(c, {'id': previous['job_id']})
        if old['actor_id'] != who:
            raise PermissionError('他の利用者の読み取りです。')
        return {'job': old, 'duplicate': True}
    raw = stock_qr.parse_scan(body.get('qr'))
    found = stock_qr.read_scan(c, {'qr': raw})
    link = found.get('link') or found
    if not found.get('found') or found.get('mismatch') or link.get('stale') or not link.get('customerProductId'):
        raise ValueError('在庫の商品に対応していないQRです。対応登録を確認してください。数量は追加していません。')
    if body.get('id'):
        job = _job(c, body)
        _editable(job, body)
        if job['status'] != 'collecting':
            raise ValueError('完了した作業には追加できません。新しい作業を開始してください。')
    else:
        existing = c.execute("SELECT * FROM qr_work_jobs WHERE actor_id=? AND status='collecting'", (who,)).fetchone()
        if existing:
            raise ValueError('読み取り中の作業があります。一覧を更新して再開してください。')
        job_id = str(uuid.uuid4())
        c.execute('''INSERT INTO qr_work_jobs(id,actor_id,actor_name,status,version,direction,started_at,lines)
            VALUES(?,?,?,'collecting',0,'outbound',?,'[]')''', (job_id, who, name, stock.stamp()))
        job = _job(c, {'id': job_id})
    # Different encodings of an old JSON QR identify the same old stock row.
    key = found.get('publicId') or ('legacy:' + str(found.get('legacyStockId') or raw))
    key = hashlib.sha256(key.encode()).hexdigest()
    line = next((x for x in job['lines'] if x['key'] == key), None)
    if line:
        line['qty'] = stock.parse_qty(line['qty'] + 1)
        line['scanCount'] += 1
    else:
        if len(job['lines']) >= 1000:
            raise ValueError('1回の作業は1000種類までです。作業を完了してください。')
        lots = found.get('lots') or []
        def single(field):
            values = {x.get(field) for x in lots}
            return next(iter(values)) if len(values) == 1 else None
        cp = stock.resolve_customer_product(c, link['customerProductId'])
        sizes = [r['label'] for r in c.execute('SELECT label FROM product_sizes WHERE product_id=?', (cp['product_id'],))]
        line = {'key': key, 'customerProductId': cp['id'],
                'customerName': link.get('customerName') or found.get('customerName') or '',
                'code': link.get('itemNum') or found.get('code') or '',
                'productName': link.get('itemName') or found.get('productName') or '',
                'spec': stock.embroidery_label(cp['embroidery']), 'color': cp['color'] or '',
                'size': link.get('sizeLabel') or found.get('size') or single('size') or '',
                'condition': link.get('condition') or found.get('condition') or single('condition') or '',
                'locationId': link.get('locationId') or single('locationId'),
                'qty': 1, 'scanCount': 1, 'sizes': sizes, 'unitCost': '',
                'lots': lots}
        job['lines'].append(line)
    c.execute('INSERT INTO qr_work_scans(token,job_id) VALUES(?,?)', (token, job['id']))
    return {'job': _save(c, job)}


def edit(c, body, *, complete=False):
    schema(c)
    job = _job(c, body)
    if job['status'] == 'completed' and not body.get('_canWrite') and c.execute(
            'SELECT 1 FROM qr_work_receipts WHERE id=?', (job['id'],)).fetchone():
        raise PermissionError('送信済みの作業は、PCの在庫更新権限を持つ担当者が修正してください。')
    _editable(job, body)
    if complete and (job['status'] != 'collecting' or job['actor_id'] != actor(body)[0]):
        raise ValueError('本人の読み取り中の作業だけ完了できます。')
    updates = body.get('lines')
    if not isinstance(updates, list) or len(updates) != len(job['lines']):
        raise ValueError('作業の明細を確認してください。')
    by_key = {x.get('key'): x for x in updates if isinstance(x, dict)}
    if len(by_key) != len(job['lines']) or set(by_key) != {x['key'] for x in job['lines']}:
        raise ValueError('明細が変わりました。一覧を更新してください。')
    kept = []
    for line in job['lines']:
        u = by_key.get(line['key'])
        if u is None:
            raise ValueError('明細が変わりました。一覧を更新してください。')
        if u.get('deleted') is True:
            continue
        line['qty'] = stock.parse_qty(u.get('qty'), allow_zero=True)
        line['size'] = str(u.get('size') or '')
        line['condition'] = stock_qr._condition_key(u.get('condition'))
        loc = u.get('locationId')
        line['locationId'] = stock.resolve_active_location(c, loc)['id'] if loc else None
        cost = u.get('unitCost')
        line['unitCost'] = '' if cost in ('', None) else stock.parse_money(cost)
        kept.append(line)
    job['lines'] = kept
    direction = body.get('direction', job['direction'])
    if direction not in ('inbound', 'outbound'):
        raise ValueError('出庫か入庫を選んでください。')
    job['direction'] = direction
    if complete:
        if not any(x['qty'] for x in job['lines']):
            raise ValueError('数量が1以上の明細を残してください。')
        job['status'] = 'completed'
        job['completed_at'] = stock.stamp()
    return {'job': _save(c, job)}


def confirm(c, body):
    if not body.get('_canWrite'):
        raise PermissionError('在庫の更新権限が必要です。')
    schema(c)
    job = _job(c, body)
    if job['status'] == 'confirmed':
        return {'job': job, 'duplicate': True}
    _editable(job, body)
    if job['status'] != 'completed':
        raise ValueError('先に読み取り作業を完了してください。')
    if not any(line['qty'] for line in job['lines']):
        raise ValueError('反映する数量がありません。数量を確認してください。')
    # Caller owns a single BEGIN IMMEDIATE transaction: every line or none.
    for line in job['lines']:
        if not line['qty']:
            continue
        qty = stock.parse_qty(line['qty'])
        cp = stock.resolve_customer_product(c, line['customerProductId'])
        size = stock.resolve_size_label(c, cp['product_id'], line['size'])
        if line['condition'] not in ('new', 'used'):
            raise ValueError('新品・中古を選択してください。')
        loc = stock.resolve_active_location(c, line['locationId'])
        payload = {'_canWrite': True, '_actor': body.get('_actor'),
                   'token': str(uuid.uuid5(uuid.NAMESPACE_URL, job['id'] + line['key'])),
                   'customerProductId': cp['id'], 'size': size, 'condition': line['condition'],
                   'locationId': loc['id'], 'qty': qty,
                   'note': 'QR作業 ' + job['id'], 'receivedOn': stock.stamp()[:10],
                   'businessOn': stock.stamp()[:10]}
        if job['direction'] == 'inbound':
            if line['unitCost'] == '':
                raise ValueError('入庫する明細の仕入単価を入力してください。')
            payload['unitCost'] = line['unitCost']
            stock.register_inbound(c, payload)
        else:
            stock.register_outbound(c, payload)
    c.execute("UPDATE qr_work_jobs SET status='confirmed',confirmed_at=?,confirmed_by=?,version=version+1 WHERE id=?",
              (stock.stamp(), actor(body)[1], job['id']))
    return {'job': _job(c, body)}


def dispatch(db_path, action, body):
    try:
        with closing(stock.connect(db_path)) as c, c:
            c.execute('BEGIN IMMEDIATE')
            if action == 'state':
                result = state(c, body)
            elif action == 'catalog':
                result = catalog(c, body)
            elif action == 'submit':
                result = submit(c, body)
            elif action == 'resolve':
                result = resolve(c, body)
            elif action in ('text-list', 'text-send'):
                result = text_message(c, body, send=action == 'text-send')
            elif action == 'scan':
                result = scan(c, body)
            elif action in ('edit', 'complete'):
                result = edit(c, body, complete=action == 'complete')
            elif action == 'confirm':
                result = confirm(c, body)
            else:
                raise ValueError('操作が見つかりません。')
        return 200, result
    except PermissionError as e:
        return 403, {'error': str(e)}
    except (ValueError, TypeError) as e:
        return 400, {'error': str(e)}
