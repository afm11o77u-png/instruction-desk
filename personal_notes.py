"""Notes scoped to the authenticated user."""
from datetime import datetime

def initialize(c):
    c.execute('CREATE TABLE IF NOT EXISTS personal_notes (user_id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)')

def get(c, user):
    row = c.execute('SELECT title, body, updated_at, version FROM personal_notes WHERE user_id=?', (user['id'],)).fetchone()
    return dict(row) if row else {'title': '', 'body': '', 'updated_at': None, 'version': 0}

def save(c, user, data):
    title = data.get('title', '')
    body = data.get('body', '')
    if not isinstance(title, str) or not isinstance(body, str):
        raise ValueError('題名とメモを確認してください。')
    title, body = title.strip(), body.strip()
    if not title or len(title) > 120: raise ValueError('題名は1〜120文字で入力してください。')
    if not body or len(body) > 20000: raise ValueError('メモは1〜20,000文字で入力してください。')
    old = c.execute('SELECT version FROM personal_notes WHERE user_id=?', (user['id'],)).fetchone()
    if data.get('version') != (old['version'] if old else 0): raise ValueError('別の画面で更新されました。メモを開き直してから保存してください。')
    now = datetime.now().isoformat(timespec='seconds')
    if old:
        c.execute('UPDATE personal_notes SET title=?,body=?,updated_at=?,version=version+1 WHERE user_id=?', (title, body, now, user['id']))
    else:
        c.execute('INSERT INTO personal_notes(user_id,title,body,updated_at,version) VALUES(?,?,?,?,1)', (user['id'], title, body, now))
    return get(c, user)
