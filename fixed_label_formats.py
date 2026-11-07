"""登録して再利用する定型書式。

印刷そのものは指示書を更新しない。ここは書式の定義だけを保存する。
組み込み書式（fixed_label.js の FIXED_LABEL_FORMATS）はデータベースに入れず、変更もしない。

閲覧できる人は、宛名書き・管理票印刷を開ける人と同じ
（主管理者・現場・現場２・現場３・倉庫担当）。
登録と修正ができる人は、宛名の記憶（label_sets）と同じ
（主管理者・現場・現場２・現場３）。営業には広げない。倉庫担当は印刷だけ。
削除APIは作らない。
"""
import json
import re
import sqlite3

from auth import log

# 宛名書き画面を開ける役割。account.js の data-view="labels" と揃える。
VIEW_ROLES = frozenset({'owner', 'floor', 'floor2', 'floor3', 'warehouse'})
# 宛名の記憶を保存できる役割。倉庫担当と営業は含めない。
WRITE_ROLES = frozenset({'owner', 'floor', 'floor2', 'floor3'})

NAME_LIMIT = 80
TIER_LIMIT = 40
CUSTOMER_LIMIT = 40
DEPT_LIMIT = 16
DEPT_MAX = 24
KINDS = frozenset({'tiers', 'departments'})

# fixed_label.js の組み込み書式名。同じ名前では登録できない。
BUILTIN_NAMES = (
    'だるま食品銭函工場',
    '日鉄FT',
    'ローズデリカ２課',
    'ローズデリカ１課',
    '石屋商事',
    'ひまわり1',
    'ひまわり2',
    'ひまわり3',
    'ひまわり4',
)

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\ufeff]')
_BLANK = re.compile(r'[\s\u3000]+')


def initialize(c):
    """表が無ければ作る。既存の行や他の表は消さない。"""
    c.execute(
        'CREATE TABLE IF NOT EXISTS fixed_label_formats ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT,'
        'name TEXT NOT NULL UNIQUE,'
        'kind TEXT NOT NULL,'
        'version INTEGER NOT NULL,'
        'payload TEXT NOT NULL,'
        'updated_at TEXT NOT NULL,'
        'updated_by_id INTEGER,'
        'updated_by TEXT NOT NULL DEFAULT \'\')'
    )


def handle(handler, c, path, data=None):
    user, denied = _session(handler, c)
    if denied:
        return denied
    if data is None:
        if user['role'] not in VIEW_ROLES:
            return 403, {'error': '定型書式の一覧は、宛名書きを使う担当（主管理者・現場・現場２・現場３・倉庫担当）だけが見られます。'}
        rows = c.execute(
            'SELECT id,name,kind,version,payload,updated_at,updated_by FROM fixed_label_formats ORDER BY name,id'
        ).fetchall()
        return 200, [_public(row) for row in rows]
    if user['role'] not in WRITE_ROLES:
        return 403, {'error': '定型書式の登録と修正は、現場・現場２・現場３と主管理者が行えます。'}
    if path != '/api/fixed-label-formats/save':
        return 404, {'error': '操作が見つかりません。'}
    name, kind, payload = _draft(data)
    old = _existing(c, data)
    if isinstance(old, tuple):
        return old
    collision = c.execute('SELECT id FROM fixed_label_formats WHERE name=?', (name,)).fetchone()
    if collision and (not old or collision['id'] != old['id']):
        return 409, {'error': '同じ名前の書式があります。名前を変えるか、選んで修正してください。'}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    try:
        if old:
            c.execute(
                'UPDATE fixed_label_formats SET name=?,kind=?,payload=?,version=version+1,'
                'updated_at=datetime(\'now\',\'localtime\'),updated_by_id=?,updated_by=? WHERE id=?',
                (name, kind, encoded, user['id'], user['name'], old['id']),
            )
            saved_id = old['id']
            log(c, user, '定型書式を修正', name)
        else:
            saved_id = c.execute(
                'INSERT INTO fixed_label_formats(name,kind,version,payload,updated_at,updated_by_id,updated_by) '
                'VALUES(?,?,1,?,datetime(\'now\',\'localtime\'),?,?)',
                (name, kind, encoded, user['id'], user['name']),
            ).lastrowid
            log(c, user, '定型書式を登録', name)
    except sqlite3.IntegrityError:
        return 409, {'error': '同じ名前の書式があります。名前を変えるか、選んで修正してください。'}
    row = c.execute(
        'SELECT id,name,kind,version,payload,updated_at,updated_by FROM fixed_label_formats WHERE id=?',
        (saved_id,),
    ).fetchone()
    return 200, _public(row)


def _session(handler, c):
    user = handler.session_user(c)
    if not user or user['must_change']:
        return None, (401, {'error': 'ログインし直してください。'})
    return user, None


def _public(row):
    return {
        'id': row['id'],
        'name': row['name'],
        'kind': row['kind'],
        'version': row['version'],
        'payload': json.loads(row['payload']),
        'updatedAt': row['updated_at'],
        'updatedBy': row['updated_by'],
    }


def _existing(c, data):
    if data.get('id') in (None, ''):
        return None
    fid = _positive_int(data.get('id'), '書式を選び直してください。')
    old = c.execute('SELECT * FROM fixed_label_formats WHERE id=?', (fid,)).fetchone()
    if not old:
        return 404, {'error': '登録した書式が見つかりません。'}
    version = data.get('version')
    if isinstance(version, bool) or not isinstance(version, int) or version != old['version']:
        return 409, {'error': '別の画面で更新されました。読み込み直してください。'}
    return old


def _positive_int(value, message):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(message)
    return value


def _draft(data):
    if not isinstance(data, dict):
        raise ValueError('入力内容を確認してください。')
    name = _line(data.get('name'), NAME_LIMIT, '保存名')
    if _is_blank(name) or not 1 <= len(name) <= NAME_LIMIT:
        raise ValueError('保存名を1～80文字で入力してください。')
    if name in BUILTIN_NAMES:
        raise ValueError('この名前は組み込みの書式で使われています。別の名前にしてください。')
    kind = data.get('kind')
    payload = _payload(kind, data.get('payload'))
    return name, kind, payload


def _payload(kind, payload):
    if kind not in KINDS:
        raise ValueError('書式の種類を選んでください。')
    if not isinstance(payload, dict):
        raise ValueError('書式の内容を確認してください。')
    if kind == 'tiers':
        lines = payload.get('lines')
        if not isinstance(lines, list) or len(lines) != 3:
            raise ValueError('上段・中段・下段の3つを入力してください。')
        cleaned = [_line(item, TIER_LIMIT, '各段') for item in lines]
        if any(_is_blank(item) for item in cleaned):
            raise ValueError('上段・中段・下段をすべて入力してください。')
        return {'lines': cleaned}
    departments = payload.get('departments')
    customer = _line(payload.get('customer'), CUSTOMER_LIMIT, '顧客名')
    if _is_blank(customer):
        raise ValueError('顧客名を入力してください。')
    if not isinstance(departments, list) or not departments:
        raise ValueError('部署名を1件以上入力してください。')
    if len(departments) > DEPT_MAX:
        raise ValueError(f'部署は{DEPT_MAX}件までです。')
    cleaned = []
    for item in departments:
        text = _line(item, DEPT_LIMIT, '部署名')
        if _is_blank(text):
            raise ValueError('空の部署名があります。行を外すか、部署名を入力してください。')
        cleaned.append(text)
    # 店舗名そのものは保存しない。印刷のときに入れる印だけを残す。
    store_slot = payload.get('storeSlot', False)
    if store_slot not in (True, False):
        raise ValueError('店舗名の設定を確認してください。')
    out = {'customer': customer, 'departments': cleaned}
    if store_slot:
        out['storeSlot'] = True
    # キーが無い、または true のときは従来どおり NO. を出す。false のときだけ残す。
    print_no = payload.get('printNo', True)
    if print_no not in (True, False):
        raise ValueError('No.欄の設定を確認してください。')
    if print_no is False:
        out['printNo'] = False
    return out


def _line(value, limit, label):
    if not isinstance(value, str):
        raise ValueError(f'{label}を文字で入力してください。')
    text = _CONTROL.sub('', value).replace('\r', '').replace('\n', '').strip(' \t')
    if len(text) > limit:
        raise ValueError(f'{label}は{limit}文字以内で入力してください。')
    return text


def _is_blank(text):
    return not _BLANK.sub('', text or '')
