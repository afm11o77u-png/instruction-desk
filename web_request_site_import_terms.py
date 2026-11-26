"""拠点の一括取り込みで「会社名で始まる行」以外に候補へ含める名前（会社ごと）。

組織の保存データ（版管理）とは別の小さな表に置く。変更のたびに前の値を履歴へ残す。
"""
import json
import re
import unicodedata

MAX_TERMS = 20
MAX_LEN = 40


def ensure(c):
    c.execute('CREATE TABLE IF NOT EXISTS web_request_site_import_terms(customer_id INTEGER PRIMARY KEY,terms TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_by TEXT NOT NULL DEFAULT \'\')')
    c.execute('CREATE TABLE IF NOT EXISTS web_request_site_import_terms_log(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,actor TEXT NOT NULL DEFAULT \'\',before TEXT NOT NULL,after TEXT NOT NULL)')


def key(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


def parse(value):
    """「シティ、ラッキーマート」→ ['シティ','ラッキーマート']。区切りは , 、 ， 空白・改行。"""
    if isinstance(value, list):
        value = '、'.join(str(x) for x in value)
    if not isinstance(value, str):
        raise ValueError('追加で含める名前を確認してください。')
    out, seen = [], set()
    for part in re.split(r'[,、，;；\s]+', unicodedata.normalize('NFKC', value)):
        part = part.strip()
        if not part:
            continue
        if len(part) > MAX_LEN:
            raise ValueError(f'追加で含める名前は1つ{MAX_LEN}文字以内で入力してください。')
        k = key(part)
        if k in seen:
            continue
        seen.add(k)
        out.append(part)
    if len(out) > MAX_TERMS:
        raise ValueError(f'追加で含める名前は{MAX_TERMS}個までです。')
    return out


def get(c, customer_id):
    ensure(c)
    row = c.execute('SELECT terms FROM web_request_site_import_terms WHERE customer_id=?', (customer_id,)).fetchone()
    try:
        terms = json.loads(row[0]) if row else []
    except (TypeError, ValueError):
        terms = []
    return [t for t in terms if isinstance(t, str)]


def put(c, customer_id, terms, user=None):
    """変わったときだけ保存し、前の値を履歴に残す。"""
    before = get(c, customer_id)
    if before == terms:
        return terms
    actor = ''
    try:
        actor = str(user['name'] or user['login'] or '') if user else ''
    except (KeyError, IndexError, TypeError):
        actor = ''
    c.execute('INSERT INTO web_request_site_import_terms_log(customer_id,actor,before,after) VALUES(?,?,?,?)',
              (customer_id, actor, json.dumps(before, ensure_ascii=False), json.dumps(terms, ensure_ascii=False)))
    c.execute('INSERT INTO web_request_site_import_terms(customer_id,terms,updated_by) VALUES(?,?,?) '
              'ON CONFLICT(customer_id) DO UPDATE SET terms=excluded.terms,updated_at=CURRENT_TIMESTAMP,updated_by=excluded.updated_by',
              (customer_id, json.dumps(terms, ensure_ascii=False), actor))
    return terms
