"""石狩倉庫2F のルート順（配置図で入力）でピッキングリストの石狩倉庫の明細を並べる。

顧客名と棚の名称（配置図のブロック・棚番号一覧の名称）は表記をそろえてから比べる：
NFKC（全角・半角）、大文字小文字、空白、株式会社・(株)・有限会社など、かっこ書きの中身を無視。
顧客名が棚の名称と同じ／棚の名称で始まる（2文字以上）／棚の名称を含む（3文字以上）なら当てはめ、
いちばん長い名称を採る。同じ長さで別の名称が並んだら「候補が複数」として選んでもらう。
選んだ結果は ishikari_route_aliases に覚える（顧客名の正規化キー → ブロックID）。
picking-route.js の routeKey と同じ規則（tests/test_ishikari_route.py で突き合わせ）。
"""
import re
import unicodedata
from datetime import datetime

CORP = re.compile(r'株式会社|有限会社|合同会社|合資会社|合名会社|\((?:株|有|同|資|名)\)')
BRACKETS = re.compile(r'\([^()]*\)|\[[^\[\]]*\]|【[^【】]*】|「[^「」]*」|『[^『』]*』|〔[^〔〕]*〕|<[^<>]*>|《[^《》]*》|〈[^〈〉]*〉')
LABEL = re.compile(r'[\d①-⑳㉑-㉟㊱-㊿]+')


def route_key(value):
    s = unicodedata.normalize('NFKC', str(value or '')).lower()
    s = CORP.sub('', s)
    for _ in range(3):
        s = BRACKETS.sub('', s)
    s = re.sub(r'[()\[\]【】「」『』〔〕<>《》〈〉]', '', s)
    return re.sub(r'[\s・\-‐‑–—―]+', '', s)


def _named(text):
    k = route_key(text)
    return k if len(k) >= 2 and not LABEL.fullmatch(k) else ''


def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS ishikari_route_aliases(
        customer_key TEXT PRIMARY KEY, customer_name TEXT NOT NULL, block_id TEXT NOT NULL,
        updated_at TEXT NOT NULL, updated_by TEXT NOT NULL DEFAULT '')''')
    c.execute('''CREATE TABLE IF NOT EXISTS ishikari_route_alias_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_key TEXT NOT NULL, customer_name TEXT NOT NULL,
        before_block TEXT, after_block TEXT, at TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '')''')


def info(c):
    """画面とExcelで使うルート情報。candidates は棚の名称ごとのルート上の順番（なければ None）。"""
    import ishikari_shelves
    schema(c)
    data = ishikari_shelves.read(c)['data']
    blocks = {b['id']: b for b in data['blocks']}
    route = [bid for bid in (data.get('route') or []) if bid in blocks]
    step = {bid: i + 1 for i, bid in enumerate(route)}
    cands = {}
    def add(name, bid=None):
        k = _named(name)
        if not k:
            return
        c_ = cands.setdefault(k, dict(key=k, name=name, rank=None))
        if bid in step and (c_['rank'] is None or step[bid] < c_['rank']):
            c_['rank'] = step[bid]
    for b in data['blocks']:
        add(b['text'].replace('\n', ''), b['id'])
    for it in data['items']:
        add(it['name'])
    # 一覧だけにある名称も、同じキーのブロックがルート上にあればその順番
    aliases = {}
    for row in c.execute('SELECT customer_key, customer_name, block_id FROM ishikari_route_aliases'):
        b = blocks.get(row[2])
        if b:
            aliases[row[0]] = dict(block_id=row[2], name=b['text'].replace('\n', ''), rank=step.get(row[2]))
    choices = [dict(id=b['id'], text=b['text'].replace('\n', ''), step=step.get(b['id'])) for b in data['blocks'] if _named(b['text'])]
    choices.sort(key=lambda x: (x['step'] is None, x['step'] or 0, x['text']))
    return dict(route=route, candidates=sorted(cands.values(), key=lambda x: x['key']), aliases=aliases, choices=choices)


def match(customer, route_info):
    """{'status': matched|ambiguous|unmatched, 'name', 'rank', 'candidates', 'alias'}"""
    k = route_key(customer)
    if not k:
        return dict(status='unmatched', name='', rank=None, candidates=[], alias=False)
    a = route_info['aliases'].get(k)
    if a:
        return dict(status='matched', name=a['name'], rank=a['rank'], candidates=[], alias=True)
    hits = [c for c in route_info['candidates']
            if c['key'] == k or (len(c['key']) >= 2 and k.startswith(c['key'])) or (len(c['key']) >= 3 and c['key'] in k)]
    if not hits:
        return dict(status='unmatched', name='', rank=None, candidates=[], alias=False)
    best = max(len(c['key']) for c in hits)
    top = [c for c in hits if len(c['key']) == best]
    if len(top) > 1:
        return dict(status='ambiguous', name='', rank=None, candidates=[c['name'] for c in top], alias=False)
    return dict(status='matched', name=top[0]['name'], rank=top[0]['rank'], candidates=[], alias=False)


def ranker(c):
    """顧客名 → ルート上の順番（なければ None）。ルートが空なら None を返す（並べ替えなし）。"""
    try:
        ri = info(c)
    except Exception:
        return None
    if not ri['route']:
        return None
    cache = {}
    def rank(customer):
        if customer not in cache:
            cache[customer] = match(customer, ri)['rank']
        return cache[customer]
    return rank


def route_sorted(rows, rank, field='customer'):
    """ルート上の顧客を通る順に先へ、それ以外は今の並びのまま後ろへ（安定ソート）。"""
    if not rank:
        return list(rows)
    order = [(rank(r.get(field, '')), i, r) for i, r in enumerate(rows)]
    order.sort(key=lambda x: (x[0] is None, x[0] or 0, x[1]))
    return [r for _, _, r in order]


def save_alias(c, customer, block_id, actor=''):
    import ishikari_shelves
    schema(c)
    name = str(customer or '').strip()
    k = route_key(name)
    if not k or len(name) > 200:
        raise ValueError('顧客名を確認してください。')
    if block_id in (None, ''):
        before = c.execute('SELECT block_id FROM ishikari_route_aliases WHERE customer_key=?', (k,)).fetchone()
        c.execute('DELETE FROM ishikari_route_aliases WHERE customer_key=?', (k,))
        after = None
    else:
        if not isinstance(block_id, str) or not any(b['id'] == block_id for b in ishikari_shelves.read(c)['data']['blocks']):
            raise ValueError('選んだ棚が見つかりません。配置図を確認してください。')
        before = c.execute('SELECT block_id FROM ishikari_route_aliases WHERE customer_key=?', (k,)).fetchone()
        c.execute('INSERT OR REPLACE INTO ishikari_route_aliases VALUES(?,?,?,?,?)',
                  (k, name, block_id, datetime.now().isoformat(timespec='seconds'), actor or ''))
        after = block_id
    c.execute('INSERT INTO ishikari_route_alias_log(customer_key,customer_name,before_block,after_block,at,actor) VALUES(?,?,?,?,?,?)',
              (k, name, before[0] if before else None, after, datetime.now().isoformat(timespec='seconds'), actor or ''))
    return info(c)
