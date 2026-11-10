"""Customer credentials prepared separately from staff login and WEB requests."""
import re
import sqlite3
from contextlib import closing
from auth import password_hash, log

def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS customer_accounts (
        id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL, customer_id INTEGER NOT NULL, notification_email TEXT NOT NULL DEFAULT '',
        password TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

def customers(path):
    with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True)) as s:
        return [dict(id=r[0], name=r[1]) for r in s.execute('SELECT id,name FROM customers WHERE active=1 ORDER BY name')]

def state(c, path):
    return {'customers': customers(path), 'accounts': [dict(r) for r in c.execute(
        'SELECT id,email,name,customer_id,notification_email,active,version,updated_at FROM customer_accounts ORDER BY id DESC')]}

def email(value, optional=False):
    value = str(value or '').strip().lower()
    if optional and not value: return ''
    if len(value)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise ValueError('メールアドレスを確認してください。')
    return value

def save(c, path, d, actor):
    action=d.get('action')
    if action not in ('create','update','reset'): raise ValueError('操作を確認してください。')
    uid=int(d.get('id') or 0)
    old=c.execute('SELECT * FROM customer_accounts WHERE id=?',(uid,)).fetchone()
    if action!='create' and (not old or old['version']!=int(d.get('version') or 0)):
        raise ValueError('他の操作で更新されています。再読み込みしてください。')
    if action=='reset':
        hashed=password_hash(d.get('password'))
        c.execute('UPDATE customer_accounts SET password=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',(hashed,uid))
    else:
        address=email(d.get('email')); notification=email(d.get('notification_email'),True)
        name=str(d.get('name') or '').strip(); customer=int(d.get('customer_id') or 0)
        if not name or len(name)>80: raise ValueError('担当者名を80文字以内で入力してください。')
        if customer not in {x['id'] for x in customers(path)}: raise ValueError('有効な顧客を選んでください。')
        active=1 if d.get('active') is True else 0
        if action=='create':
            hashed=password_hash(d.get('password'))
            uid=c.execute('INSERT INTO customer_accounts(email,name,customer_id,notification_email,password,active) VALUES(?,?,?,?,?,?)',
                (address,name,customer,notification,hashed,active)).lastrowid
        else:
            c.execute('UPDATE customer_accounts SET email=?,name=?,customer_id=?,notification_email=?,active=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                (address,name,customer,notification,active,uid))
    log(c,actor,{'create':'顧客ID作成','update':'顧客ID更新','reset':'顧客パスワード再設定'}[action],str(uid))
    return {'ok':True}
