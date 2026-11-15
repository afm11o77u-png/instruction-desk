"""Private instruction form presets and folders."""
import json
import re

FIELDS = frozenset((
    'template', 'customer', 'department', 'contract', 'request', 'barcodeColor',
    'nameColor', 'nameRequired', 'method', 'methodNote', 'packing', 'car',
    'advance', 'advanceCondition', 'enclosures',
))
ITEM_FIELDS = ('name', 'maker', 'sku', 'size', 'condition', 'stock', 'note', 'note2')
ID = re.compile(r'[a-f0-9-]{36}')


def initialize(c):
    c.execute("""CREATE TABLE IF NOT EXISTS personal_formats (
        user_id INTEGER PRIMARY KEY, version INTEGER NOT NULL, entries TEXT NOT NULL
    )""")


def load(c, user_id):
    row = c.execute('SELECT version,entries FROM personal_formats WHERE user_id=?', (user_id,)).fetchone()
    return {'version': row[0], 'entries': json.loads(row[1])} if row else {'version': 0, 'entries': []}


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
    out = {key: clean_text(value[key]) for key in FIELDS if key in value}
    out['items'] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('商品明細を確認してください。')
        qty = item.get('quantity', 0)
        if type(qty) is not int or not 0 <= qty <= 99999:
            raise ValueError('商品明細の数量を確認してください。')
        out['items'].append({key: clean_text(item.get(key, '')) for key in ITEM_FIELDS} | {'quantity': qty})
    return out


def save(c, user_id, data):
    if not isinstance(data, dict) or type(data.get('version')) is not int:
        raise ValueError('書式一覧を開き直してください。')
    previous = load(c, user_id)
    if data['version'] != previous['version']:
        return 409, {'error': '別の画面で書式が変更されました。自分の書式を開き直してください。'}
    entries = data.get('entries')
    if not isinstance(entries, list) or len(entries) > 300:
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
    entries = list(cleaned.values())
    version = previous['version'] + 1
    c.execute('INSERT INTO personal_formats VALUES(?,?,?) ON CONFLICT(user_id) '
              'DO UPDATE SET version=excluded.version, entries=excluded.entries',
              (user_id, version, json.dumps(entries, ensure_ascii=False)))
    return 200, {'version': version, 'entries': entries}
