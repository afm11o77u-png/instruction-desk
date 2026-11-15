"""Private instruction form presets and folders."""
import json
import re
from uuid import NAMESPACE_URL, uuid5

FIELDS = frozenset((
    'template', 'customer', 'department', 'contract', 'request', 'barcodeColor',
    'nameColor', 'nameRequired', 'method', 'methodNote', 'packing', 'car',
    'advance', 'advanceCondition', 'enclosures', 'code', 'management', 'employeeNumber',
    'employee', 'phonetic', 'sex', 'repair', 'notes', 'chest',
))
ITEM_FIELDS = ('name', 'maker', 'sku', 'size', 'condition', 'stock', 'note', 'note2')
ID = re.compile(r'[a-f0-9-]{36}')


def initialize(c):
    c.execute("""CREATE TABLE IF NOT EXISTS personal_formats (
        user_id INTEGER PRIMARY KEY, version INTEGER NOT NULL, entries TEXT NOT NULL
    )""")


def load(c, user_id):
    row = c.execute('SELECT version,entries FROM personal_formats WHERE user_id=?', (user_id,)).fetchone()
    value = json.loads(row[1]) if row else []
    if isinstance(value, list):
        value = {'entries': value}
    return {'version': row[0] if row else 0, 'entries': value['entries'],
            'layout': value.get('layout', 'auto'), 'iconSize': value.get('iconSize', 'medium'),
            'legacyMigrated': value.get('legacyMigrated', False)}


def clean_text(value, limit=300):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('書式の入力内容を確認してください。')
    return value


def form_data(value):
    if not isinstance(value, dict):
        raise ValueError('書式の入力内容を確認してください。')
    items = value.get('items', [])
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError('商品明細は100行まで保存できます。')
    out = {key: clean_text(value[key], 2000) for key in FIELDS if key in value}
    out['items'] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('商品明細を確認してください。')
        qty = item.get('quantity', 0)
        if type(qty) is not int or not 0 <= qty <= 99999:
            raise ValueError('商品明細の数量を確認してください。')
        out['items'].append({key: clean_text(item.get(key, ''), 2000) for key in ITEM_FIELDS} | {'quantity': qty})
    return out



def _store(c, user_id, state):
    c.execute('INSERT INTO personal_formats VALUES(?,?,?) ON CONFLICT(user_id) '
              'DO UPDATE SET version=excluded.version, entries=excluded.entries',
              (user_id, state['version'], json.dumps(state, ensure_ascii=False)))


def legacy_form_data(payload):
    data = {key: str(payload[key])[:2000] for key in FIELDS if payload.get(key) is not None}
    data['items'] = []
    for item in (payload.get('items') or [])[:100]:
        if not isinstance(item, dict):
            continue
        values = {key: str(item.get(key) or '')[:2000] for key in ITEM_FIELDS}
        if not any(values.values()):
            continue
        qty = item.get('quantity')
        try:
            values['quantity'] = max(0, min(99999, int(qty)))
        except (ValueError, TypeError, OverflowError):
            values['quantity'] = 0
        data['items'].append(values)
    return form_data(data)


def migrate_legacy(c):
    users = c.execute('SELECT DISTINCT user_id FROM templates').fetchall()
    for (user_id,) in users:
        state = load(c, user_id)
        if state['legacyMigrated']:
            continue
        entries = state['entries']
        folders = {entry['name']: entry['id'] for entry in entries
                   if entry['kind'] == 'folder' and not entry['parent']}
        names = {(entry['parent'], entry['name']) for entry in entries if entry['kind'] == 'format'}
        for tid, folder, name, encoded in c.execute(
                'SELECT id,folder,name,payload FROM templates WHERE user_id=? ORDER BY id', (user_id,)):
            parent = ''
            if folder:
                if folder not in folders:
                    folders[folder] = str(uuid5(NAMESPACE_URL, f'instruction-desk:legacy-folder:{user_id}:{folder}'))
                    entries.append({'id': folders[folder], 'kind': 'folder', 'name': folder, 'parent': ''})
                parent = folders[folder]
            candidate, number = name, 1
            while (parent, candidate) in names:
                suffix = '（旧ひな形）' if number == 1 else f'（旧ひな形 {number}）'
                candidate = name[:80-len(suffix)] + suffix
                number += 1
            names.add((parent, candidate))
            entries.append({'id': str(uuid5(NAMESPACE_URL, f'instruction-desk:legacy-format:{user_id}:{tid}')),
                            'name': candidate, 'kind': 'format', 'parent': parent,
                            'data': legacy_form_data(json.loads(encoded))})
        state['legacyMigrated'] = True
        state['version'] += 1
        _store(c, user_id, state)

def save(c, user_id, data):
    if not isinstance(data, dict) or type(data.get('version')) is not int:
        raise ValueError('書式一覧を開き直してください。')
    previous = load(c, user_id)
    if data['version'] != previous['version']:
        return 409, {'error': '別の画面で書式が変更されました。自分の書式を開き直してください。'}
    entries = data.get('entries')
    if not isinstance(entries, list) or len(entries) > max(300, len(previous['entries'])):
        raise ValueError('フォルダと書式は合わせて300件までです。')
    cleaned = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('書式一覧を確認してください。')
        key = clean_text(entry.get('id'), 36)
        kind = entry.get('kind')
        name = clean_text(entry.get('name'), 80).strip()
        parent = clean_text(entry.get('parent', ''), 36)
        if not ID.fullmatch(key) or key in cleaned or kind not in ('folder', 'format') or not name:
            raise ValueError('フォルダまたは書式の名前を確認してください。')
        item = {'id': key, 'name': name, 'kind': kind, 'parent': parent}
        slot = entry.get('slot')
        if slot is not None:
            if type(slot) is not int or not 0 <= slot < 600:
                raise ValueError('アイコンの配置を確認してください。')
            item['slot'] = slot
        if kind == 'format':
            item['data'] = form_data(entry.get('data'))
        cleaned[key] = item
    for item in cleaned.values():
        parent = item['parent']
        visited = {item['id']}
        while parent:
            target = cleaned.get(parent)
            if not target or target['kind'] != 'folder' or parent in visited:
                raise ValueError('移動先のフォルダを確認してください。')
            visited.add(parent)
            parent = target['parent']
    layout = data.get('layout', previous['layout'])
    icon_size = data.get('iconSize', previous['iconSize'])
    if layout not in ('auto', 'aligned') or icon_size not in ('small', 'medium', 'large'):
        raise ValueError('アイコンの表示設定を確認してください。')
    value = {'version': previous['version'] + 1, 'entries': list(cleaned.values()),
             'layout': layout, 'iconSize': icon_size, 'legacyMigrated': previous['legacyMigrated']}
    _store(c, user_id, value)
    return 200, value
