"""Notes scoped to the authenticated user."""
import sqlite3
from datetime import datetime

def initialize(c):
    for table in ('personal_notes', 'contact_notes'):
        c.execute(f'CREATE TABLE IF NOT EXISTS {table} (user_id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)')

    c.execute('CREATE TABLE IF NOT EXISTS contact_note_categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)')
    c.execute("INSERT OR IGNORE INTO contact_note_categories(id, name) VALUES(1, '一般')")
    c.execute('''CREATE TABLE IF NOT EXISTS note_entries (
        id INTEGER PRIMARY KEY, shared INTEGER NOT NULL, scope_id INTEGER NOT NULL,
        title TEXT NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1, legacy INTEGER,
        UNIQUE(shared, scope_id, legacy)
    )''')
    for shared, table in enumerate(('personal_notes', 'contact_notes')):
        c.execute(f'''INSERT OR IGNORE INTO note_entries
            (shared, scope_id, title, body, updated_at, version, legacy)
            SELECT ?, user_id, title, body, updated_at, version, 1 FROM {table}''', (shared,))

def categories(c):
    return [dict(row) for row in c.execute('SELECT id, name FROM contact_note_categories ORDER BY id')]

def contact_category(c, key, name=None):
    if name is not None:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise ValueError('カテゴリ名は1〜40文字で入力してください。')
        try:
            return c.execute('INSERT INTO contact_note_categories(name) VALUES(?)', (name.strip(),)).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError('同じ名前のカテゴリがあります。') from None
    if not c.execute('SELECT 1 FROM contact_note_categories WHERE id=?', (key,)).fetchone():
        raise ValueError('カテゴリを選び直してください。')
    return key

def get(c, user, shared=False, category_id=1, note_id=0):
    key = category_id if shared else user["id"]
    scope = (int(shared), key)
    notes = [dict(row) for row in c.execute(
        'SELECT id, title FROM note_entries WHERE shared=? AND scope_id=? ORDER BY updated_at DESC, id DESC', scope)]
    note = None
    if note_id:
        row = c.execute('''SELECT id, title, body, updated_at, version FROM note_entries
            WHERE shared=? AND scope_id=? AND id=?''', (*scope, note_id)).fetchone()
        if row is None:
            raise ValueError('メモを選び直してください。')
        note = dict(row)
    return {'notes': notes, 'note': note}

def save(c, user, data, shared=False, category_id=1, note_id=0):
    key = category_id if shared else user["id"]
    title = data.get('title', '')
    body = data.get('body', '')
    if not isinstance(title, str) or not isinstance(body, str):
        raise ValueError('題名とメモを確認してください。')
    title, body = title.strip(), body.strip()
    if not title or len(title) > 120: raise ValueError('題名は1〜120文字で入力してください。')
    if not body or len(body) > 20000: raise ValueError('メモは1〜20,000文字で入力してください。')
    scope = (int(shared), key)
    version = data.get('version')
    now = datetime.now().isoformat(timespec='seconds')
    if note_id:
        changed = c.execute('''UPDATE note_entries SET title=?, body=?, updated_at=?, version=version+1
            WHERE shared=? AND scope_id=? AND id=? AND version=?''',
            (title, body, now, *scope, note_id, version)).rowcount
        if not changed:
            raise ValueError('別の画面で更新されました。メモを開き直してから保存してください。')
    else:
        if version != 0:
            raise ValueError('メモを開き直してから保存してください。')
        note_id = c.execute('''INSERT INTO note_entries(shared, scope_id, title, body, updated_at)
            VALUES(?,?,?,?,?)''', (*scope, title, body, now)).lastrowid
    return get(c, user, shared, category_id, note_id)
