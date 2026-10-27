"""月別シフト表（予定）の保存・読込。出勤簿実績には反映しない。

在籍者は出勤簿マスタ（kintai_people）を参照し、別名簿は持たない。
年月ごとのシートに氏名スナップショットとセルを保存し、在籍変化後も過去入力を消さない。
同時編集は version による楽観ロックで上書きを防ぐ。
"""
from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import date, datetime, timedelta

WEEKDAY_JA = ('月', '火', '水', '木', '金', '土', '日')
CELL_KINDS = frozenset({'off', 'time'})
TIME_TEXT_RE = re.compile(r'^.{1,40}$')
PERSON_KEY_RE = re.compile(r'^[a-z]+:.{1,80}$')


def schema(c):
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_shift_sheets (
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT '',
            people_json TEXT NOT NULL DEFAULT '[]',
            cells_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (year, month)
        )'''
    )


def person_key(employment: str, employee_code: str) -> str:
    return f'{employment}:{employee_code}'


def _parse_year_month(year, month) -> tuple[int, int]:
    try:
        y = int(year)
        m = int(month)
    except (TypeError, ValueError) as e:
        raise ValueError('年月を確認してください。') from e
    if y < 2000 or y > 2100 or not 1 <= m <= 12:
        raise ValueError('年月を確認してください。')
    return y, m


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """weekday: Mon=0 … Sun=6. n-th occurrence in month."""
    d = date(year, month, 1)
    add = (weekday - d.weekday()) % 7
    first = d + timedelta(days=add)
    return first + timedelta(weeks=n - 1)


def _vernal_equinox(year: int) -> date:
    # 文科省近似（1980–2099）
    day = int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return date(year, 3, day)


def _autumnal_equinox(year: int) -> date:
    day = int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))
    return date(year, 9, day)


def japan_holidays(year: int) -> dict[date, str]:
    """日本の国民の祝日・振替休日（2000年以降の現行法想定）。"""
    if year < 2000 or year > 2099:
        return {}
    fixed: list[tuple[int, int, str]] = [
        (1, 1, '元日'),
        (2, 11, '建国記念の日'),
        (2, 23, '天皇誕生日'),
        (4, 29, '昭和の日'),
        (5, 3, '憲法記念日'),
        (5, 4, 'みどりの日'),
        (5, 5, 'こどもの日'),
        (8, 11, '山の日'),
        (11, 3, '文化の日'),
        (11, 23, '勤労感謝の日'),
    ]
    holidays: dict[date, str] = {}
    for mo, day, name in fixed:
        holidays[date(year, mo, day)] = name
    holidays[_nth_weekday(year, 1, 0, 2)] = '成人の日'
    holidays[_vernal_equinox(year)] = '春分の日'
    holidays[_nth_weekday(year, 7, 0, 3)] = '海の日'
    holidays[_nth_weekday(year, 9, 0, 3)] = '敬老の日'
    holidays[_autumnal_equinox(year)] = '秋分の日'
    holidays[_nth_weekday(year, 10, 0, 2)] = 'スポーツの日'

    # 振替休日: 祝日が日曜なら翌月曜（連続祝日も繰り下げ）
    for base in sorted(list(holidays)):
        if base.weekday() != 6:
            continue
        d = base + timedelta(days=1)
        while d in holidays:
            d += timedelta(days=1)
        holidays.setdefault(d, '振替休日')

    # 国民の休日: 祝日に挟まれた平日
    for d in sorted(list(holidays)):
        mid = d + timedelta(days=1)
        nxt = d + timedelta(days=2)
        if mid not in holidays and nxt in holidays and mid.weekday() < 5:
            holidays.setdefault(mid, '国民の休日')
    return holidays


def month_calendar(year: int, month: int) -> list[dict]:
    y, m = _parse_year_month(year, month)
    holidays = japan_holidays(y)
    last = monthrange(y, m)[1]
    days = []
    for day in range(1, last + 1):
        d = date(y, m, day)
        wd = d.weekday()  # Mon=0
        name = holidays.get(d)
        kind = 'holiday' if name else ('sun' if wd == 6 else ('sat' if wd == 5 else 'weekday'))
        days.append({
            'day': day,
            'weekday': wd,
            'weekdayLabel': WEEKDAY_JA[wd],
            'kind': kind,
            'holidayName': name or '',
        })
    return days


def _active_roster(c) -> list[dict]:
    rows = c.execute(
        'SELECT id, employee_code, employment, display_name, sort_order, retired '
        'FROM kintai_people WHERE active=1 AND COALESCE(retired,0)=0 '
        'ORDER BY sort_order, id'
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            'personId': r['id'],
            'employeeCode': r['employee_code'],
            'employment': r['employment'],
            'displayName': r['display_name'],
            'sortOrder': r['sort_order'],
            'key': person_key(r['employment'], r['employee_code']),
            'source': 'roster',
        })
    return out


def _normalize_people(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError('対象者一覧の形式が不正です。')
    people = []
    seen = set()
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            raise ValueError('対象者一覧の形式が不正です。')
        key = str(p.get('key') or '').strip()
        name = str(p.get('displayName') or '').strip()
        employment = str(p.get('employment') or '').strip()
        code = str(p.get('employeeCode') or '').strip()
        if not key and employment and code:
            key = person_key(employment, code)
        if not key or not PERSON_KEY_RE.match(key):
            raise ValueError(f'対象者キーが不正です（{i + 1}件目）。')
        if not name:
            raise ValueError(f'氏名が空です（{i + 1}件目）。')
        if key in seen:
            continue
        seen.add(key)
        pid = p.get('personId')
        people.append({
            'key': key,
            'personId': int(pid) if isinstance(pid, int) else None,
            'employeeCode': code,
            'employment': employment,
            'displayName': name[:80],
            'sortOrder': int(p.get('sortOrder') or i),
            'source': str(p.get('source') or 'saved')[:20],
        })
    return people


def _normalize_cells(raw, days_in_month: int) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError('シフトセルの形式が不正です。')
    out = {}
    for key, by_day in raw.items():
        key = str(key)
        if not PERSON_KEY_RE.match(key):
            continue
        if not isinstance(by_day, dict):
            continue
        person_cells = {}
        for day_s, cell in by_day.items():
            try:
                day = int(day_s)
            except (TypeError, ValueError):
                continue
            if not 1 <= day <= days_in_month:
                continue
            if cell is None or cell == '' or cell == {}:
                continue
            if not isinstance(cell, dict):
                continue
            kind = str(cell.get('kind') or '')
            if kind not in CELL_KINDS:
                continue
            text = str(cell.get('text') or '').strip()
            if kind == 'off':
                person_cells[str(day)] = {'kind': 'off', 'text': '×'}
            else:
                if not text or not TIME_TEXT_RE.match(text):
                    raise ValueError('時間指定の文言を確認してください。')
                person_cells[str(day)] = {'kind': 'time', 'text': text}
        if person_cells:
            out[key] = person_cells
    return out


def _row_public(year: int, month: int, row, days: list[dict], people: list[dict], cells: dict) -> dict:
    return {
        'year': year,
        'month': month,
        'version': int(row['version']) if row else 0,
        'updatedAt': row['updated_at'] if row else None,
        'updatedBy': row['updated_by'] if row else '',
        'days': days,
        'people': people,
        'cells': cells,
        'exists': bool(row),
    }


def _merge_people(roster: list[dict], saved: list[dict], cells: dict) -> list[dict]:
    """出勤簿在籍を主とし、過去シートに残る人を消さない。二重名簿は作らない。"""
    by_key = {}
    for p in roster:
        by_key[p['key']] = dict(p)
    for p in saved:
        key = p.get('key')
        if not key:
            continue
        if key in by_key:
            # 在籍側の氏名・IDを優先。表示名だけ過去が違う場合も在籍を採用
            continue
        # セルが無くてもシートに載っていた人は過去保持
        by_key[key] = {
            'key': key,
            'personId': p.get('personId'),
            'employeeCode': p.get('employeeCode') or '',
            'employment': p.get('employment') or '',
            'displayName': p.get('displayName') or key,
            'sortOrder': int(p.get('sortOrder') or 10_000),
            'source': 'history',
        }
    # セルだけ残っているキーも氏名保持
    for key in cells:
        if key not in by_key:
            emp, _, code = key.partition(':')
            by_key[key] = {
                'key': key,
                'personId': None,
                'employeeCode': code,
                'employment': emp,
                'displayName': code or key,
                'sortOrder': 20_000,
                'source': 'history',
            }
    people = list(by_key.values())
    people.sort(key=lambda p: (
        0 if p.get('source') == 'roster' else 1,
        int(p.get('sortOrder') or 0),
        p.get('displayName') or '',
        p.get('key') or '',
    ))
    return people


def get_shift_sheet(c, year, month) -> dict:
    y, m = _parse_year_month(year, month)
    days = month_calendar(y, m)
    row = c.execute(
        'SELECT * FROM kintai_shift_sheets WHERE year=? AND month=?',
        (y, m),
    ).fetchone()
    saved_people = []
    cells = {}
    if row:
        try:
            saved_people = json.loads(row['people_json'] or '[]')
        except json.JSONDecodeError:
            saved_people = []
        try:
            cells = json.loads(row['cells_json'] or '{}')
        except json.JSONDecodeError:
            cells = {}
        if not isinstance(saved_people, list):
            saved_people = []
        if not isinstance(cells, dict):
            cells = {}
    roster = _active_roster(c)
    people = _merge_people(roster, saved_people, cells)
    return _row_public(y, m, row, days, people, cells)


def save_shift_sheet(c, user, payload: dict) -> tuple[int, dict]:
    """保存。version 不一致は 409。出勤簿テーブルは触らない。"""
    y, m = _parse_year_month(payload.get('year'), payload.get('month'))
    try:
        client_ver = int(payload.get('version'))
    except (TypeError, ValueError) as e:
        raise ValueError('version を確認してください。') from e
    if client_ver < 0:
        raise ValueError('version を確認してください。')

    days_in_month = monthrange(y, m)[1]
    people = _normalize_people(payload.get('people'))
    cells = _normalize_cells(payload.get('cells'), days_in_month)

    # 在籍マスタとマージした名簿をスナップショット（過去保持）
    roster = _active_roster(c)
    people = _merge_people(roster, people, cells)

    now = datetime.now().replace(microsecond=0).isoformat(sep=' ')
    actor = ''
    if user:
        try:
            actor = str(user['name'] or user['login'] or '')[:80]
        except (KeyError, IndexError, TypeError):
            actor = ''

    row = c.execute(
        'SELECT version FROM kintai_shift_sheets WHERE year=? AND month=?',
        (y, m),
    ).fetchone()
    current_ver = int(row['version']) if row else 0
    if client_ver != current_ver:
        sheet = get_shift_sheet(c, y, m)
        return 409, {
            'error': 'ほかの画面で先に保存されています。内容を確認してから再度保存してください。',
            'conflict': True,
            'sheet': sheet,
        }

    new_ver = current_ver + 1
    people_json = json.dumps(people, ensure_ascii=False)
    cells_json = json.dumps(cells, ensure_ascii=False)
    if row:
        c.execute(
            '''UPDATE kintai_shift_sheets
               SET version=?, updated_at=?, updated_by=?, people_json=?, cells_json=?
               WHERE year=? AND month=? AND version=?''',
            (new_ver, now, actor, people_json, cells_json, y, m, current_ver),
        )
        if c.rowcount != 1:
            sheet = get_shift_sheet(c, y, m)
            return 409, {
                'error': 'ほかの画面で先に保存されています。内容を確認してから再度保存してください。',
                'conflict': True,
                'sheet': sheet,
            }
    else:
        try:
            c.execute(
                '''INSERT INTO kintai_shift_sheets(
                    year, month, version, updated_at, updated_by, people_json, cells_json
                ) VALUES (?,?,?,?,?,?,?)''',
                (y, m, new_ver, now, actor, people_json, cells_json),
            )
        except Exception:
            sheet = get_shift_sheet(c, y, m)
            return 409, {
                'error': 'ほかの画面で先に保存されています。内容を確認してから再度保存してください。',
                'conflict': True,
                'sheet': sheet,
            }
    return 200, get_shift_sheet(c, y, m)
