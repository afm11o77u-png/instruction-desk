"""WEB依頼の依頼本体（サーバー保存）と、指示書とのひも付け。

依頼は customer_id ごとに、WEB依頼の設定で見られる顧客（web_requests.state の customers）だけ読み書きできる。
指示書とのひも付けは web_request_sheet_links（1依頼に複数の指示書）。進み具合の連動は後の段階で、
links から orders を引けば作業状況を出せるようにしてある。
"""
import json
from datetime import datetime

GENDERS = ('', '男性', '女性')  # 着用者の性別：新規は必須、それ以外は任意（以前の依頼は空欄）
STATUSES = ('受付待ち', '受付済み', '差し戻し')
ITEM_KEYS = ('id', 'name', 'stock_name', 'code', 'color', 'size', 'qty', 'customer_product_id', 'product_id', 'embroidery',
             'default_qty', 'hem_processing', 'processing_days', 'hem_length', 'manual', 'note', 'maker', 'processing')


def ensure(c):
    c.execute('''CREATE TABLE IF NOT EXISTS web_request_entries(
      id TEXT PRIMARY KEY, customer_id INTEGER NOT NULL, node_id TEXT, payload TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT '受付待ち', created_at TEXT NOT NULL, created_by INTEGER,
      updated_at TEXT, updated_by INTEGER, version INTEGER NOT NULL DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS web_request_sheet_links(
      request_id TEXT NOT NULL, order_id INTEGER NOT NULL, created_at TEXT NOT NULL, created_by INTEGER,
      PRIMARY KEY(request_id, order_id))''')


def _text(v, limit=200):
    return v.strip()[:limit] if isinstance(v, str) else ''


def _visible(c, path, user, account_id=None, page_id=None):
    import web_requests
    data = web_requests.state(c, path, user, account_id, page_id)
    return data, {x['id'] for x in data['customers']}


def _names(data):
    return ({x['id']: x for x in data['customers']}, {n['id']: n.get('name') or '' for n in data['organizations']['nodes']})


def _row(c, r, names=None):
    p = json.loads(r['payload'])
    links = []
    for l in c.execute('SELECT l.order_id,l.created_at,o.payload FROM web_request_sheet_links l LEFT JOIN orders o ON o.id=l.order_id WHERE l.request_id=? ORDER BY l.created_at', (r['id'],)):
        op = json.loads(l['payload']) if l['payload'] else {}
        links.append(dict(orderId=l['order_id'], number=op.get('number') or '', orderStatus=op.get('status') or ('削除済み' if not l['payload'] else ''), linkedAt=l['created_at']))
    cust, nodes = names or ({}, {})
    k = cust.get(r['customer_id']) or {}
    return dict(p, customerName=k.get('name') or '', customerCode=k.get('customer_code') or '', nodeName=nodes.get(r['node_id']) or '', id=r['id'], customerId=r['customer_id'], nodeId=r['node_id'], status=r['status'], createdAt=r['created_at'], version=r['version'], sheets=links)


def listing(c, path, user):
    ensure(c)
    data, allowed = _visible(c, path, user)
    rows = [r for r in c.execute('SELECT * FROM web_request_entries ORDER BY created_at, id') if r['customer_id'] in allowed]
    names = _names(data)
    return dict(requests=[_row(c, r, names) for r in rows])


def get(c, path, user, rid):
    ensure(c)
    data, allowed = _visible(c, path, user)
    r = c.execute('SELECT * FROM web_request_entries WHERE id=?', (rid,)).fetchone()
    if not r or r['customer_id'] not in allowed: raise PermissionError('この依頼を確認できません。')
    return _row(c, r, _names(data))


def create(c, path, user, d):
    ensure(c)
    account = d.get('requesterAccountId') if type(d.get('requesterAccountId')) is int else None
    page = d.get('requestPageId') if isinstance(d.get('requestPageId'), str) and d.get('requestPageId') and account is None else None
    data, allowed = _visible(c, path, user, account, page)
    cid = d.get('customerId')
    if type(cid) is not int or cid not in allowed: raise PermissionError('この顧客へ依頼できません。')
    node = d.get('nodeId') or None
    nodes = {n['id']: n for n in data['organizations']['nodes']}
    if node is not None and (node not in nodes or nodes[node]['customer_id'] != cid or node not in data['allowedNodeIds']): raise ValueError('依頼先の店舗を確認してください。')
    items = d.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 100: raise ValueError('制服を1つ以上選んでください。')
    clean = []
    for x in items:
        if not isinstance(x, dict): raise ValueError('制服の内容を確認してください。')
        qty = x.get('qty')
        if type(qty) is not int or not 1 <= qty <= 99999: raise ValueError('枚数を確認してください。')
        y = {k: x[k] for k in ITEM_KEYS if k in x and isinstance(x[k], (str, int, float, bool))}
        for k, v in list(y.items()):
            if isinstance(v, str): y[k] = v[:300]
        if not _text(y.get('name')): raise ValueError('制服名を確認してください。')
        clean.append(y)
    gender = d.get('wearerGender') or ''
    if gender not in GENDERS: raise ValueError('着用者の性別を確認してください。')
    if _text(d.get('requestType'), 40) == '新規' and not gender: raise ValueError('新規のご依頼は、着用者の性別を選んでください。')
    now = datetime.now()
    seq = c.execute("SELECT COUNT(*) FROM web_request_entries WHERE id LIKE ?", ('WR-' + now.strftime('%Y%m%d') + '-%',)).fetchone()[0] + 1
    rid = 'WR-%s-%03d' % (now.strftime('%Y%m%d'), seq)
    while c.execute('SELECT 1 FROM web_request_entries WHERE id=?', (rid,)).fetchone():
        seq += 1; rid = 'WR-%s-%03d' % (now.strftime('%Y%m%d'), seq)
    payload = dict(department=_text(d.get('department')), departmentId=_text(d.get('departmentId'), 80), contract=_text(d.get('contract')),
                   requestType=_text(d.get('requestType'), 40), wearer=_text(d.get('wearer'), 100), wearerGender=gender, delivery=_text(d.get('delivery'), 10),
                   notes=_text(d.get('notes'), 1000), organizationPath=_text(d.get('organizationPath'), 300), requesterName=_text(d.get('requesterName'), 100),
                   requesterAccountId=account, requestPageId=page, items=clean)
    c.execute('INSERT INTO web_request_entries(id,customer_id,node_id,payload,status,created_at,created_by) VALUES(?,?,?,?,?,?,?)',
              (rid, cid, node, json.dumps(payload, ensure_ascii=False), '受付待ち', now.isoformat(timespec='seconds'), user['id'] if user else None))
    c.commit()
    return get(c, path, user, rid)


def set_status(c, path, user, d):
    ensure(c)
    r = get(c, path, user, d.get('id') if isinstance(d.get('id'), str) else '')
    st = d.get('status')
    if st not in ('受付済み', '差し戻し'): raise ValueError('状態を確認してください。')
    now = datetime.now().isoformat(timespec='seconds')
    row = c.execute('SELECT payload FROM web_request_entries WHERE id=?', (r['id'],)).fetchone()
    payload = json.loads(row['payload'])
    if st == '差し戻し':
        # 差し戻しは理由必須。理由は依頼側の依頼履歴に表示する（受付済みに戻しても記録は残す）
        reason = _text(d.get('reason'), 300)
        if not reason: raise ValueError('差し戻しの理由を入力してください。')
        try: by = _text(user['name'], 80) if user else ''
        except (KeyError, IndexError, TypeError): by = ''
        payload.update(returnReason=reason, returnedAt=now, returnedBy=by)
    c.execute('UPDATE web_request_entries SET status=?,payload=?,updated_at=?,updated_by=?,version=version+1 WHERE id=?',
              (st, json.dumps(payload, ensure_ascii=False), now, user['id'] if user else None, r['id']))
    c.commit()
    return get(c, path, user, r['id'])


def link(c, request_id, order_id, user):
    """指示書の保存（同じトランザクション内）から呼ぶ。依頼がなければ保存自体を止める（誤ったひも付けを防ぐ）。"""
    ensure(c)
    if not isinstance(request_id, str) or not c.execute('SELECT 1 FROM web_request_entries WHERE id=?', (request_id,)).fetchone():
        raise ValueError('ひも付けるWEB依頼が見つかりません。依頼番号を確認してください。')
    c.execute('INSERT OR IGNORE INTO web_request_sheet_links(request_id,order_id,created_at,created_by) VALUES(?,?,?,?)',
              (request_id, order_id, datetime.now().isoformat(timespec='seconds'), user['id'] if user else None))
