"""圧着カウンター: 1台の圧着機のカウンター表示数を日付ごとに1件記録する。"""
from __future__ import annotations

import datetime as dt
import re


def schema(c):
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_crimp_readings (
            day TEXT PRIMARY KEY,
            reading INTEGER NOT NULL,
            user_id INTEGER,
            user_name TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )'''
    )


def can_admin(user) -> bool:
    try:
        return bool(user) and (user['role'] == 'owner' or bool(user['manage']))
    except (KeyError, IndexError, TypeError):
        return False


def _day(value) -> str:
    s = str(value or '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
        raise ValueError('日付を確認してください。')
    dt.date.fromisoformat(s)
    return s


def _reading(value) -> int:
    if isinstance(value, bool):
        raise ValueError('カウンターの数値を確認してください。')
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError('カウンターの数値を確認してください。')
    if n < 0 or n > 10 ** 12:
        raise ValueError('カウンターの数値を確認してください。')
    return n


def list_month(c, ym: str | None, user=None) -> dict:
    if not ym:
        ym = dt.date.today().strftime('%Y-%m')
    if not re.fullmatch(r'\d{4}-\d{2}', ym):
        raise ValueError('対象月は YYYY-MM 形式で指定してください。')
    rows = c.execute(
        'SELECT day, reading, user_name, updated_at FROM kintai_crimp_readings WHERE day <= ? ORDER BY day',
        (ym + '-31',),
    ).fetchall()
    prev = None
    out = []
    for r in rows:
        diff = None if prev is None else r['reading'] - prev
        prev = r['reading']
        if r['day'].startswith(ym):
            out.append({'day': r['day'], 'reading': r['reading'], 'diff': diff,
                        'userName': r['user_name'], 'updatedAt': r['updated_at']})
    total = sum(x['diff'] for x in out if x['diff'] is not None)
    return {'month': ym, 'rows': out, 'total': total, 'canAdmin': can_admin(user)}


def previous_reading(c, day: str):
    r = c.execute('SELECT reading FROM kintai_crimp_readings WHERE day < ? ORDER BY day DESC LIMIT 1', (day,)).fetchone()
    return r['reading'] if r else None


def save(c, user, d: dict):
    day = _day(d.get('day'))
    reading = _reading(d.get('reading'))
    exists = c.execute('SELECT 1 FROM kintai_crimp_readings WHERE day=?', (day,)).fetchone()
    if exists and d.get('edit') and not can_admin(user):
        return 403, {'error': '修正は管理者だけです。'}
    now = dt.datetime.now().isoformat(timespec='seconds')
    c.execute(
        '''INSERT INTO kintai_crimp_readings(day, reading, user_id, user_name, updated_at) VALUES(?,?,?,?,?)
           ON CONFLICT(day) DO UPDATE SET reading=excluded.reading, user_id=excluded.user_id,
           user_name=excluded.user_name, updated_at=excluded.updated_at''',
        (day, reading, user['id'] if user else None, (user['name'] if user else '') or '', now),
    )
    return 200, {'ok': True, 'day': day}


def delete(c, user, d: dict):
    if not can_admin(user):
        return 403, {'error': '削除は管理者だけです。'}
    day = _day(d.get('day'))
    c.execute('DELETE FROM kintai_crimp_readings WHERE day=?', (day,))
    return 200, {'ok': True}
