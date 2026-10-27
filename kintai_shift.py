"""月別シフト表（予定）の保存・読込。出勤簿実績には反映しない。

在籍者は出勤簿マスタ（kintai_people）を初期行の元にする。
年月ごとのシートに氏名スナップショットとセルを保存し、行の追加・削除・氏名編集はその月の表に閉じる。
シフト専用行（source=shift）は出勤簿マスタへ書かない。過去月の入力は消さない。
同時編集は version による楽観ロックで上書きを防ぐ。
"""
from __future__ import annotations

import json
import re
import secrets
from calendar import monthrange
from datetime import date, datetime, timedelta

WEEKDAY_JA = ('月', '火', '水', '木', '金', '土', '日')
CELL_KINDS = frozenset({'off', 'time'})
TIME_TEXT_RE = re.compile(r'^.{1,40}$')
# roster: regular:009 / シフト専用: shift:xxxx
PERSON_KEY_RE = re.compile(r'^[a-z]+:.{1,80}$')
ALLOWED_SOURCES = frozenset({'roster', 'shift', 'history', 'saved'})


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
    # 今後の未作成月の初期並び（identity key のみ）。月シート本体とは分離。
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_shift_default_order (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            keys_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT ''
        )'''
    )


def person_key(employment: str, employee_code: str) -> str:
    return f'{employment}:{employee_code}'


def new_shift_row_key() -> str:
    return f'shift:{secrets.token_hex(6)}'


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

    for base in sorted(list(holidays)):
        if base.weekday() != 6:
            continue
        d = base + timedelta(days=1)
        while d in holidays:
            d += timedelta(days=1)
        holidays.setdefault(d, '振替休日')

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


def _is_shift_key(key: str) -> bool:
    return str(key or '').startswith('shift:')


def _orphan_person(key: str, display_name: str = '') -> dict:
    emp, _, code = key.partition(':')
    source = 'shift' if _is_shift_key(key) else 'history'
    return {
        'key': key,
        'personId': None,
        'employeeCode': code,
        'employment': 'shift' if source == 'shift' else emp,
        'displayName': (display_name or code or key)[:80],
        'sortOrder': 20_000,
        'source': source,
    }


def _sorted_people(by_key: dict, *, by_sort_order_only: bool = False) -> list[dict]:
    """未保存月は在籍種別→sortOrder。保存済み月はシート上の並び（sortOrder）を正とする。"""
    people = list(by_key.values())
    if by_sort_order_only:
        people.sort(key=lambda p: (
            int(p.get('sortOrder') if p.get('sortOrder') is not None else 0),
            p.get('displayName') or '',
            p.get('key') or '',
        ))
        return people
    source_rank = {'roster': 0, 'shift': 1, 'history': 2, 'saved': 1}
    people.sort(key=lambda p: (
        source_rank.get(p.get('source'), 9),
        int(p.get('sortOrder') or 0),
        p.get('displayName') or '',
        p.get('key') or '',
    ))
    return people


def _normalize_people(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError('対象者一覧の形式が不正です。')
    people = []
    seen = set()
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            raise ValueError('対象者一覧の形式が不正です。')
        key = str(p.get('key') or '').strip()
        name = str(p.get('displayName') or '').strip() or '（未入力）'
        employment = str(p.get('employment') or '').strip()
        code = str(p.get('employeeCode') or '').strip()
        source = str(p.get('source') or '').strip()
        if _is_shift_key(key) or source == 'shift' or employment == 'shift':
            if not key or not PERSON_KEY_RE.match(key):
                key = new_shift_row_key()
            employment = 'shift'
            if not code:
                code = key.split(':', 1)[1]
            source = 'shift'
        else:
            if not key and employment and code:
                key = person_key(employment, code)
            if not key or not PERSON_KEY_RE.match(key):
                raise ValueError(f'対象者キーが不正です（{i + 1}件目）。')
            if source not in ALLOWED_SOURCES:
                source = 'saved'
        if key in seen:
            continue
        seen.add(key)
        pid = p.get('personId')
        people.append({
            'key': key,
            'personId': int(pid) if isinstance(pid, int) else None,
            'employeeCode': code[:80],
            'employment': employment[:20],
            'displayName': name[:80],
            'sortOrder': int(p['sortOrder']) if isinstance(p.get('sortOrder'), int) else i,
            'source': source[:20],
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


def _annotate_with_roster(people: list[dict], roster: list[dict]) -> list[dict]:
    """出勤簿マスタとの紐づけを付与。シフト専用行はマスタへ書かない。
    並びはペイロード配列順を正とし、sortOrder を 0..n-1 に振り直す。"""
    roster_by_key = {p['key']: p for p in roster}
    out = []
    for i, p in enumerate(people):
        q = dict(p)
        q['sortOrder'] = i
        if _is_shift_key(q['key']) or q.get('source') == 'shift' or q.get('employment') == 'shift':
            q['source'] = 'shift'
            q['employment'] = 'shift'
            q['personId'] = None
            out.append(q)
            continue
        if q['key'] in roster_by_key:
            r = roster_by_key[q['key']]
            q['personId'] = r['personId']
            q['employeeCode'] = r['employeeCode']
            q['employment'] = r['employment']
            q['source'] = 'roster'
            # displayName はシート上の編集を優先（マスタは変更しない）
            out.append(q)
            continue
        q['source'] = 'history'
        out.append(q)
    return out


def _normalize_default_keys(raw) -> list[str]:
    """標準並びは identity key のみ（氏名・セルは保存しない）。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('標準の並び順の形式が不正です。')
    out: list[str] = []
    seen = set()
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            key = str(item.get('key') or '').strip()
        else:
            key = str(item or '').strip()
        if not key or not PERSON_KEY_RE.match(key):
            raise ValueError(f'標準の並び順のキーが不正です（{i + 1}件目）。')
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def get_default_order(c) -> dict:
    row = c.execute(
        'SELECT keys_json, updated_at, updated_by FROM kintai_shift_default_order WHERE id=1'
    ).fetchone()
    keys: list[str] = []
    updated_at = None
    updated_by = ''
    if row:
        try:
            raw = json.loads(row['keys_json'] or '[]')
        except json.JSONDecodeError:
            raw = []
        if isinstance(raw, list):
            keys = [str(k) for k in raw if isinstance(k, str) and PERSON_KEY_RE.match(k)]
        updated_at = row['updated_at'] or None
        updated_by = row['updated_by'] or ''
    return {
        'keys': keys,
        'updatedAt': updated_at,
        'updatedBy': updated_by,
        'exists': bool(row) and bool(keys),
    }


def save_default_order(c, user, payload: dict) -> dict:
    """今後の未作成月用の標準並びを保存。月シート（cells/people）は一切触らない。"""
    keys = _normalize_default_keys((payload or {}).get('keys'))
    now = datetime.now().replace(microsecond=0).isoformat(sep=' ')
    actor = ''
    if user:
        try:
            actor = str(user['name'] or user['login'] or '')[:80]
        except (KeyError, IndexError, TypeError):
            actor = ''
    keys_json = json.dumps(keys, ensure_ascii=False)
    c.execute(
        '''INSERT INTO kintai_shift_default_order(id, keys_json, updated_at, updated_by)
           VALUES (1, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             keys_json=excluded.keys_json,
             updated_at=excluded.updated_at,
             updated_by=excluded.updated_by''',
        (keys_json, now, actor),
    )
    return get_default_order(c)


def _roster_with_default_order(roster: list[dict], default_keys: list[str]) -> list[dict]:
    """未作成月: 保存済み標準並びを在籍者に適用。非在籍は飛ばし、新規在籍は末尾。"""
    if not default_keys:
        return [dict(p) for p in roster]
    by_key = {p['key']: dict(p) for p in roster}
    out: list[dict] = []
    seen: set[str] = set()
    for key in default_keys:
        key = str(key)
        if key in by_key and key not in seen:
            item = by_key[key]
            item['sortOrder'] = len(out)
            out.append(item)
            seen.add(key)
    for p in roster:
        if p['key'] not in seen:
            item = dict(p)
            item['sortOrder'] = len(out)
            out.append(item)
            seen.add(p['key'])
    return out


def _compose_people(
    roster: list[dict],
    saved: list[dict],
    cells: dict,
    *,
    sheet_exists: bool,
    default_keys: list[str] | None = None,
) -> list[dict]:
    """未保存月は在籍マスタ（標準並びがあれば適用）。保存済み月はシート名簿を正とし、削除した行は戻さない。"""
    roster_by_key = {p['key']: p for p in roster}
    by_key: dict[str, dict] = {}
    use_default = bool(not sheet_exists and default_keys)

    if not sheet_exists:
        for p in _roster_with_default_order(roster, default_keys or []):
            by_key[p['key']] = p
    else:
        for i, p in enumerate(saved or []):
            if not isinstance(p, dict):
                continue
            key = str(p.get('key') or '').strip()
            if not key or not PERSON_KEY_RE.match(key):
                continue
            name = str(p.get('displayName') or '').strip() or key
            item = {
                'key': key,
                'personId': p.get('personId') if isinstance(p.get('personId'), int) else None,
                'employeeCode': str(p.get('employeeCode') or ''),
                'employment': str(p.get('employment') or ''),
                'displayName': name[:80],
                'sortOrder': int(p['sortOrder']) if isinstance(p.get('sortOrder'), int) else i,
                'source': str(p.get('source') or 'saved')[:20],
            }
            if _is_shift_key(key) or item['source'] == 'shift' or item['employment'] == 'shift':
                item['source'] = 'shift'
                item['employment'] = 'shift'
                item['personId'] = None
            elif key in roster_by_key:
                r = roster_by_key[key]
                item['personId'] = r['personId']
                item['employeeCode'] = r['employeeCode']
                item['employment'] = r['employment']
                item['source'] = 'roster'
                # 氏名は保存スナップショットを維持
            else:
                item['source'] = 'history'
            by_key[key] = item

    # セルだけ残っているキーは過去保持（意図的削除後は save 側でセルを刈る）
    for key in cells:
        if key not in by_key and PERSON_KEY_RE.match(str(key)):
            by_key[key] = _orphan_person(str(key))

    # 保存済み／標準並び適用時は配列順（sortOrder）を正とする
    return _sorted_people(by_key, by_sort_order_only=sheet_exists or use_default)


def _row_public(
    year: int,
    month: int,
    row,
    days: list[dict],
    people: list[dict],
    cells: dict,
    roster: list[dict] | None = None,
) -> dict:
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
        # 出勤簿在籍（シフト表への「未掲載追加」用）。シフト専用行は含まない。
        'roster': roster or [],
    }


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
    default_keys = get_default_order(c)['keys'] if not row else []
    people = _compose_people(
        roster, saved_people, cells,
        sheet_exists=bool(row),
        default_keys=default_keys,
    )
    return _row_public(y, m, row, days, people, cells, roster=roster)


def save_shift_sheet(c, user, payload: dict) -> tuple[int, dict]:
    """保存。version 不一致は 409。出勤簿テーブル（kintai_people / monthly）は触らない。"""
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

    roster = _active_roster(c)
    people = _annotate_with_roster(people, roster)
    # この月から外した行のセルは残さない（他月のシートは別レコードのため影響なし）
    keep = {p['key'] for p in people}
    cells = {k: v for k, v in cells.items() if k in keep}

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
        # sqlite3.Connection に rowcount は無い。execute() が返す Cursor を見る。
        cur = c.execute(
            '''UPDATE kintai_shift_sheets
               SET version=?, updated_at=?, updated_by=?, people_json=?, cells_json=?
               WHERE year=? AND month=? AND version=?''',
            (new_ver, now, actor, people_json, cells_json, y, m, current_ver),
        )
        if cur.rowcount != 1:
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
            # 同時 INSERT など。ペイロード本文はログに出さない
            sheet = get_shift_sheet(c, y, m)
            return 409, {
                'error': 'ほかの画面で先に保存されています。内容を確認してから再度保存してください。',
                'conflict': True,
                'sheet': sheet,
            }
    return 200, get_shift_sheet(c, y, m)
