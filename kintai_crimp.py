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
    cols = {r[1] for r in c.execute('PRAGMA table_info(kintai_crimp_readings)')}
    if 'worker_id' not in cols:
        c.execute('ALTER TABLE kintai_crimp_readings ADD COLUMN worker_id INTEGER')
    if 'worker_name' not in cols:
        c.execute("ALTER TABLE kintai_crimp_readings ADD COLUMN worker_name TEXT NOT NULL DEFAULT ''")


def workers(c) -> list[dict]:
    """出荷仕分けの作業者と同じ一覧（/api/ship-workers と共通）。"""
    return [{'id': r['id'], 'name': r['name']} for r in c.execute(
        "SELECT id,name FROM users WHERE active=1 AND role IN ('owner','floor') ORDER BY name")]


def can_admin(user) -> bool:
    """主管理者（role=owner）または管理者フラグ（users.manage）。

    利用者管理の「管理者」チェックは manage と overview を同時に立てる。
    ここでの管理者フラグは manage 列だけ（在庫の管理者フラグ、修正・削除と同じ）。
    overview のみ、マーク管理（mark_kintai）のみでは管理者にしない。
    """
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
    show_name = can_admin(user)
    # 一般の応答を組み立てるとき、氏名列は読まない（レスポンスに載せない）。
    name_cols = ', user_name, worker_name' if show_name else ''
    rows = c.execute(
        f'SELECT day, reading{name_cols}, updated_at FROM kintai_crimp_readings WHERE day <= ? ORDER BY day',
        (ym + '-31',),
    ).fetchall()
    prev = None
    out = []
    for r in rows:
        diff = None if prev is None else r['reading'] - prev
        prev = r['reading']
        if r['day'].startswith(ym):
            # 数量・日付は全員。入力者名（選択した作業者と保存操作のログイン名）は
            # 主管理者か管理者フラグがある閲覧者だけ。id も返さない。記録列は残す。
            item = {'day': r['day'], 'reading': r['reading'], 'diff': diff, 'updatedAt': r['updated_at']}
            if show_name:
                item['userName'] = r['worker_name'] or r['user_name']
                item['loginName'] = r['user_name']
            out.append(item)
    total = sum(x['diff'] for x in out if x['diff'] is not None)
    return {'month': ym, 'rows': out, 'total': total, 'canAdmin': show_name}


def previous_reading(c, day: str):
    r = c.execute('SELECT reading FROM kintai_crimp_readings WHERE day < ? ORDER BY day DESC LIMIT 1', (day,)).fetchone()
    return r['reading'] if r else None


def save(c, user, d: dict):
    day = _day(d.get('day'))
    reading = _reading(d.get('reading'))
    exists = c.execute('SELECT 1 FROM kintai_crimp_readings WHERE day=?', (day,)).fetchone()
    if exists and d.get('edit') and not can_admin(user):
        return 403, {'error': '修正は管理者だけです。'}
    try:
        wid = int(d.get('workerId'))
    except (TypeError, ValueError):
        wid = None
    worker = next((w for w in workers(c) if w['id'] == wid), None)
    if worker is None:
        raise ValueError('入力者を選んでください。')
    now = dt.datetime.now().isoformat(timespec='seconds')
    c.execute(
        '''INSERT INTO kintai_crimp_readings(day, reading, user_id, user_name, worker_id, worker_name, updated_at) VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(day) DO UPDATE SET reading=excluded.reading, user_id=excluded.user_id,
           user_name=excluded.user_name, worker_id=excluded.worker_id, worker_name=excluded.worker_name, updated_at=excluded.updated_at''',
        (day, reading, user['id'] if user else None, (user['name'] if user else '') or '', worker['id'], worker['name'], now),
    )
    return 200, {'ok': True, 'day': day}


def delete(c, user, d: dict):
    if not can_admin(user):
        return 403, {'error': '削除は管理者だけです。'}
    day = _day(d.get('day'))
    c.execute('DELETE FROM kintai_crimp_readings WHERE day=?', (day,))
    return 200, {'ok': True}
