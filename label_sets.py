"""Saved address-label sheets, stored per login and kept out of instructions."""
import json
from auth import log

REQUESTS = {'新規', 'サイズ交換', '消耗・破損交換', '追加・補充', '復職', '異動・移動', 'その他'}
FIELDS = {'customer': 40, 'management': 20, 'department': 40, 'employee': 20, 'request': 20}
PATTERNS = {'3', '4'}

def initialize(c):
    c.execute('CREATE TABLE IF NOT EXISTS label_sets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(user_id,name))')

def handle(handler, c, path, data=None):
    u = handler.session_user(c)
    if not u or u['must_change']: return 401, {'error': 'ログインし直してください。'}
    if u['role'] not in ('owner', 'floor', 'floor2'): return 403, {'error': '宛名の記憶は現場・現場２と主管理者が使用します。'}
    if data is None:
        rows = c.execute('SELECT id,name,version,payload FROM label_sets WHERE user_id=? ORDER BY name', (u['id'],)).fetchall()
        return 200, [dict(r, payload=_decode_payload(r['payload'])) for r in rows]
    old = None
    if data.get('id'):
        old = c.execute('SELECT * FROM label_sets WHERE id=? AND user_id=?', (data['id'], u['id'])).fetchone()
        if not old: return 404, {'error': '記憶した宛名が見つかりません。'}
        if old['version'] != data.get('version'): return 409, {'error': '別の画面で更新されました。選び直してください。'}
    if path.endswith('/delete'):
        if not old: return 404, {'error': '削除する宛名を選んでください。'}
        c.execute('DELETE FROM label_sets WHERE id=? AND user_id=?', (old['id'], u['id']))
        log(c, u, '宛名の記憶を削除', old['name'])
        return 200, {'ok': True}
    if path != '/api/label-sets/save': return 404, {'error': '操作が見つかりません。'}
    name = data.get('name')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80: raise ValueError('保存名を1～80文字で入力してください。')
    name = name.strip()
    pattern = _pattern(data.get('pattern'))
    slots = _slots(data.get('slots'))
    collision = c.execute('SELECT id FROM label_sets WHERE user_id=? AND name=?', (u['id'], name)).fetchone()
    if collision and (not old or collision['id'] != old['id']): return 409, {'error': '同じ名前の宛名があります。名前を変えるか、選んで上書きしてください。'}
    encoded = json.dumps({'pattern': pattern, 'slots': slots}, ensure_ascii=False)
    if old:
        c.execute('UPDATE label_sets SET name=?,payload=?,version=version+1 WHERE id=? AND user_id=?', (name, encoded, old['id'], u['id']))
        sid = old['id']
    else:
        sid = c.execute('INSERT INTO label_sets(user_id,name,version,payload) VALUES(?,?,1,?)', (u['id'], name, encoded)).lastrowid
    log(c, u, '宛名を記憶', name)
    row = c.execute('SELECT id,name,version,payload FROM label_sets WHERE id=?', (sid,)).fetchone()
    return 200, dict(row, payload=_decode_payload(row['payload']))

def _pattern(value):
    text = str(value or '').strip()
    return text if text in PATTERNS else '4'

def _decode_payload(raw):
    data = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(data, list):
        return {'pattern': '4', 'slots': data}
    if isinstance(data, dict):
        slots = data.get('slots')
        if not isinstance(slots, list):
            slots = []
        return {'pattern': _pattern(data.get('pattern')), 'slots': slots}
    return {'pattern': '4', 'slots': []}

def _slots(slots):
    if not isinstance(slots, list) or not slots or len(slots) > 40: raise ValueError('記憶できる宛名は40件までです。')
    cleaned = [_slot(slot) for slot in slots]
    while len(cleaned) % 4: cleaned.append(None)
    while len(cleaned) > 4 and all(slot is None for slot in cleaned[:4]): cleaned = cleaned[4:]
    while len(cleaned) > 4 and all(slot is None for slot in cleaned[-4:]): cleaned = cleaned[:-4]
    if not any(cleaned): raise ValueError('宛名を入力してから記憶してください。')
    return cleaned

def _slot(slot):
    if slot is None: return None
    if not isinstance(slot, dict): raise ValueError('宛名の内容を確認してください。')
    out = {}
    for key, limit in FIELDS.items():
        value = slot.get(key, '')
        if not isinstance(value, str) or len(value.strip()) > limit: raise ValueError('宛名の文字数を確認してください。')
        value = value.strip()
        if key == 'request':
            if value not in REQUESTS: value = '消耗・破損交換'
        out[key] = value
    if not any(out[key] for key in ('customer', 'management', 'department', 'employee')): return None
    return out
