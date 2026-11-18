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
    jobs = [public(r) for r in c.execute('''SELECT * FROM qr_work_jobs
        WHERE status!='collecting' OR actor_id=? ORDER BY status='collecting' DESC, started_at DESC LIMIT 200''', (who,))]
    locations = [{'id': r['id'], 'name': stock.location_label(c, r)}
                 for r in c.execute('SELECT * FROM locations WHERE active=1 ORDER BY id')]
    return {'jobs': jobs, 'locations': locations, 'actorId': who,
            'canConfirm': bool(body.get('_canWrite'))}


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
