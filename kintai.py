"""勤怠管理（マーク）: 個人出勤簿 xlsx を読取専用で取り込み、月別集計を保存・表示する。

元出勤簿の書換・保存・名前変更は行わない。数式の再計算もしない。
実ファイルのセル番地・シート名が未確認の場合は layoutId を fixture 以外にしない。
共有ルート等の端末固有設定は data/kintai-local.json（Git 除外）に置く。
"""
from __future__ import annotations

import calendar
import json
import os
import re
import sqlite3
import zipfile
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'

# 暦年・4月始まりではない。2026年度 = 2026-02〜2027-01
DEFAULT_FISCAL_START = (2026, 2)
LOCAL_SETTINGS_NAME = 'kintai-local.json'
LOCAL_SETTINGS_KEYS = ('shareRoot', 'layoutId', 'peopleFiles')

# 対象者マスタ（リンク用短い名前。共有上の実ファイル名は人が設定。推測で mark_kintai は付けない）
DEFAULT_PEOPLE = [
    {'employee_code': '009', 'employment': 'regular', 'display_name': '加藤 篤郎', 'sort_order': 1, 'file_hint': '01_加藤　篤郎'},
    {'employee_code': '031', 'employment': 'regular', 'display_name': '今城 卓也', 'sort_order': 2, 'file_hint': '02_今城　卓也'},
    {'employee_code': '036', 'employment': 'regular', 'display_name': '伊藤 良子', 'sort_order': 3, 'file_hint': '03_伊藤　良子'},
    {'employee_code': '037', 'employment': 'contract', 'display_name': '須永 結香', 'sort_order': 4, 'file_hint': '04_須永　結香'},
    {'employee_code': '040', 'employment': 'part', 'display_name': '小川 春佳', 'sort_order': 5, 'file_hint': '05_小川　春佳'},
    {'employee_code': '010', 'employment': 'part', 'display_name': '菊池 敏子', 'sort_order': 6, 'file_hint': '06_菊池　敏子'},
    {'employee_code': '024', 'employment': 'part', 'display_name': '田中 明美', 'sort_order': 7, 'file_hint': '07_田中　明美'},
    {'employee_code': '014', 'employment': 'part', 'display_name': '八島 里恵', 'sort_order': 8, 'file_hint': '08_八島　里恵'},
    {'employee_code': '018', 'employment': 'part', 'display_name': '廣瀬 愛', 'sort_order': 9, 'file_hint': '09_廣瀬　愛'},
    {'employee_code': '028', 'employment': 'part', 'display_name': '小山 さとみ', 'sort_order': 10, 'file_hint': '10_小山　さとみ'},
    {'employee_code': '037', 'employment': 'part', 'display_name': '南田 久美子', 'sort_order': 11, 'file_hint': '11_南田　久美子'},
]

# fixture-v1: 検証用レイアウト（実出勤簿のセル番地ではない。未確認の実ファイル構造を推測しない）
FIXTURE_LAYOUT = {
    'id': 'fixture-v1',
    'note': '検証用。実出勤簿のシート名・列位置は未確認のため本番レイアウトとしては使わない。',
    'sheet_name_format': '{yyyy}-{mm:02d}',  # 例 2026-02
    'header_row': 1,
    'day_col': 1,          # A
    'start_col': 2,        # B 始業
    'leave_col': 3,        # C 休み
    'ordinary_col': 4,     # D 普通勤務（分）
    'overtime_col': 5,     # E 残業（分）
    'first_data_row': 2,
}

LAYOUTS = {
    'fixture-v1': FIXTURE_LAYOUT,
}

PAID_LEAVE_RE = re.compile(r'(?P<num>\d+(?:\.\d+)?)\s*有休')
TINY_NEG = -0.05  # 分換算前の微小負数（時間）相当の閾値は分で扱う


def bind(data_dir: Path):
    global DATA_DIR
    DATA_DIR = Path(data_dir)


DATA_DIR = Path('.')


def schema(c):
    cols = {r['name'] for r in c.execute('PRAGMA table_info(users)')}
    if 'mark_kintai' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN mark_kintai INTEGER NOT NULL DEFAULT 0')
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_people (
            id INTEGER PRIMARY KEY,
            employee_code TEXT NOT NULL,
            employment TEXT NOT NULL,
            display_name TEXT NOT NULL,
            source_file TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE(employee_code, employment)
        )'''
    )
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_monthly (
            person_id INTEGER NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            work_days REAL,
            holiday_days REAL,
            paid_leave_days REAL,
            ordinary_minutes INTEGER,
            overtime_minutes INTEGER,
            source_mtime TEXT,
            imported_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (person_id, year, month)
        )'''
    )
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_import_runs (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            actor_id INTEGER,
            actor TEXT NOT NULL,
            fiscal_label TEXT NOT NULL,
            summary_json TEXT NOT NULL
        )'''
    )
    c.execute(
        '''CREATE TABLE IF NOT EXISTS kintai_person_status (
            person_id INTEGER PRIMARY KEY,
            last_import_at TEXT,
            last_status TEXT NOT NULL DEFAULT '未取得',
            last_error TEXT NOT NULL DEFAULT '',
            source_path TEXT NOT NULL DEFAULT '',
            source_mtime TEXT NOT NULL DEFAULT ''
        )'''
    )
    _seed_people(c)


def _seed_people(c):
    n = c.execute('SELECT COUNT(*) FROM kintai_people').fetchone()[0]
    if n:
        return
    for p in DEFAULT_PEOPLE:
        c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order) VALUES(?,?,?,?,1,?)',
            (p['employee_code'], p['employment'], p['display_name'], '', p['sort_order']),
        )


def can_use_kintai(user) -> bool:
    if not user:
        return False
    # 主管理者は運用設定のため常に可。一般利用者は mark_kintai（初期OFF）のみ。
    if user['role'] == 'owner':
        return True
    try:
        return bool(user['mark_kintai'])
    except (KeyError, IndexError, TypeError):
        return False


def require_kintai(user):
    if not can_use_kintai(user):
        return 403, {'error': '勤怠管理（マーク）の権限がありません。'}
    return None


def fiscal_months(start_year: int, start_month: int = 2):
    """start_month から12か月。既定は 2月始まり（例: 2026-02〜2027-01）。"""
    y, m = start_year, start_month
    out = []
    for _ in range(12):
        out.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def fiscal_label(start_year: int, start_month: int = 2) -> str:
    months = fiscal_months(start_year, start_month)
    ey, em = months[-1]
    return f'{start_year}年{start_month}月〜{ey}年{em}月'


def format_hm(minutes) -> str:
    if minutes is None:
        return ''
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return ''
    if total < 0:
        return ''
    h, m = divmod(total, 60)
    return f'{h}:{m:02d}'


def parse_paid_leave(text) -> float | None:
    """休み列から有休日数。該当しなければ None（有休でない）。"""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    m = PAID_LEAVE_RE.search(s)
    if not m:
        if '有休' in s or '有給' in s:
            # 数値なしの有休表記は未確定→呼び出し側でエラーにできるように None ではなく特別扱い
            return None
        return None
    return float(m.group('num'))


def is_paid_leave_cell(text) -> bool:
    if text is None:
        return False
    s = str(text).strip()
    return bool(PAID_LEAVE_RE.search(s)) or ('有休' in s) or ('有給' in s)


def _as_minutes(value, label: str) -> int:
    """セル値を整数分へ。Excel 時刻値（1日=1.0）は使わず、fixture は分または H:MM / 小数時間を明示。"""
    if value is None or value == '':
        return 0
    if isinstance(value, bool):
        raise ValueError(f'{label}の値が不正です。')
    if isinstance(value, (int, float)):
        # 小数時間が 0〜数日程度の範囲で来た場合の二重変換を避けるため、
        # fixture-v1 は「分」を数値で書く前提。|value| < 48 かつ非整数っぽい場合は時間とみなす。
        v = float(value)
        if v < TINY_NEG:
            if v > -1:
                return 0  # 微小負数→0
            raise ValueError(f'{label}が大きな負数です（{v}）。')
        if v < 0:
            return 0
        # 整数または大きい値は分。48未満の非整数は時間→分
        if abs(v - round(v)) < 1e-9 or abs(v) >= 48:
            return int(round(v))
        return int(round(v * 60))
    s = str(value).strip()
    if not s:
        return 0
    if re.fullmatch(r'\d+:\d{1,2}', s):
        h, m = s.split(':')
        total = int(h) * 60 + int(m)
        if total < 0:
            raise ValueError(f'{label}が負です。')
        return total
    try:
        return _as_minutes(float(s), label)
    except ValueError as e:
        raise ValueError(f'{label}を読み取れません（{s}）。') from e


def local_settings_path() -> Path:
    return Path(DATA_DIR) / LOCAL_SETTINGS_NAME


def load_local_settings() -> dict:
    path = local_settings_path()
    env_root = os.environ.get('KINTAI_SHARE_ROOT', '').strip()
    env_layout = os.environ.get('KINTAI_LAYOUT_ID', '').strip()
    data = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(raw, dict):
                data = {k: raw.get(k) for k in LOCAL_SETTINGS_KEYS if k in raw}
        except (OSError, json.JSONDecodeError):
            data = {}
    if env_root:
        data['shareRoot'] = env_root
    if env_layout:
        data['layoutId'] = env_layout
    data.setdefault('shareRoot', '')
    data.setdefault('layoutId', 'fixture-v1')
    data.setdefault('peopleFiles', {})
    if not isinstance(data.get('peopleFiles'), dict):
        data['peopleFiles'] = {}
    return data


def save_local_settings(patch: dict, actor_may_write: bool) -> dict:
    if not actor_may_write:
        raise PermissionError('共有パス設定を変更する権限がありません。')
    cur = load_local_settings()
    if 'shareRoot' in patch:
        root = patch['shareRoot']
        if not isinstance(root, str) or len(root) > 500:
            raise ValueError('共有ルートは500文字以内で入力してください。')
        # 資格情報を埋め込ませない（user:pass@ やパスワードっぽい断片）
        if '://' in root and '@' in root.split('://', 1)[1].split('/', 1)[0]:
            raise ValueError('共有パスに接続資格情報を含めないでください。')
        cur['shareRoot'] = root.strip()
    if 'layoutId' in patch:
        lid = patch['layoutId']
        if lid not in LAYOUTS:
            raise ValueError('未対応のレイアウトです。実ファイル確認前は fixture-v1 のみです。')
        cur['layoutId'] = lid
    if 'peopleFiles' in patch:
        pf = patch['peopleFiles']
        if not isinstance(pf, dict):
            raise ValueError('対象者ファイル設定が不正です。')
        clean = {}
        for k, v in pf.items():
            key = str(k)
            if not isinstance(v, str) or len(v) > 400:
                raise ValueError('ファイル名が長すぎます。')
            if '..' in v.replace('\\', '/').split('/'):
                raise ValueError('相対パスに .. は使えません。')
            clean[key] = v.strip()
        cur['peopleFiles'] = clean
    path = local_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {k: cur.get(k) for k in LOCAL_SETTINGS_KEYS}


def get_layout(layout_id: str | None = None) -> dict:
    lid = layout_id or load_local_settings().get('layoutId') or 'fixture-v1'
    layout = LAYOUTS.get(lid)
    if not layout:
        raise ValueError(
            f'レイアウト「{lid}」は未確認のため使用できません。'
            '実出勤簿のセル／シートを確認するまで fixture-v1（検証用）のみ利用できます。'
        )
    return layout


def _col_letters(n: int) -> str:
    s = ''
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _parse_cell_ref(ref: str):
    m = re.fullmatch(r'([A-Z]+)(\d+)', ref.upper())
    if not m:
        return None, None
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return col, int(m.group(2))


def read_xlsx_grid(blob: bytes) -> dict[str, dict[tuple[int, int], object]]:
    """シート名 → {(row, col): value}。数式は再計算せず、キャッシュ値があればそれを使う。"""
    if not blob.startswith(b'PK'):
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    try:
        book = zipfile.ZipFile(BytesIO(blob))
    except zipfile.BadZipFile as e:
        raise ValueError('Excelファイル（.xlsx）を選んでください。') from e
    strings = _shared_strings(book)
    sheets = _sheet_map(book)
    out = {}
    for name, path in sheets.items():
        out[name] = _sheet_grid(book, path, strings)
    return out


def _shared_strings(book):
    try:
        root = ET.fromstring(book.read('xl/sharedStrings.xml'))
    except KeyError:
        return []
    values = []
    for si in root.findall(f'{NS}si'):
        texts = [t.text or '' for t in si.iter(f'{NS}t')]
        values.append(''.join(texts))
    return values


def _sheet_map(book):
    wb = ET.fromstring(book.read('xl/workbook.xml'))
    rels = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
    rel_map = {r.attrib['Id']: r.attrib['Target'] for r in rels}
    sheets = {}
    for sh in wb.find(f'{NS}sheets'):
        name = sh.attrib.get('name')
        rid = sh.attrib.get(f'{REL}id')
        target = rel_map.get(rid, '')
        if not target.startswith('/'):
            target = 'xl/' + target.lstrip('/')
        else:
            target = target.lstrip('/')
        sheets[name] = target
    return sheets


def _sheet_grid(book, path, strings):
    root = ET.fromstring(book.read(path))
    grid = {}
    for row in root.findall(f'.//{NS}sheetData/{NS}row'):
        for c in row.findall(f'{NS}c'):
            ref = c.attrib.get('r')
            col, rn = _parse_cell_ref(ref or '')
            if not col or not rn:
                continue
            typ = c.attrib.get('t')
            f = c.find(f'{NS}f')
            v = c.find(f'{NS}v')
            is_el = c.find(f'{NS}is')
            if typ == 'inlineStr' and is_el is not None:
                texts = [t.text or '' for t in is_el.iter(f'{NS}t')]
                grid[(rn, col)] = ''.join(texts)
                continue
            if v is None or v.text is None:
                if f is not None:
                    # 数式があり計算結果キャッシュが無い → 空欄を0とせず呼び出し側でエラー
                    grid[(rn, col)] = {'__missing_cache__': True, 'formula': (f.text or '')}
                continue
            raw = v.text
            if typ == 's':
                try:
                    grid[(rn, col)] = strings[int(raw)]
                except (ValueError, IndexError):
                    grid[(rn, col)] = raw
            elif typ == 'b':
                grid[(rn, col)] = raw in ('1', 'true', 'TRUE')
            else:
                try:
                    if '.' in raw or 'e' in raw.lower():
                        grid[(rn, col)] = float(raw)
                    else:
                        grid[(rn, col)] = int(raw)
                except ValueError:
                    grid[(rn, col)] = raw
    return grid


def _cell(grid, row, col):
    return grid.get((row, col))


def aggregate_month_from_grid(grid, year: int, month: int, layout: dict, as_of: date | None = None) -> dict:
    """日次行から B/C/F/J/K 相当を集計。欠損キャッシュはエラー。"""
    as_of = as_of or date.today()
    last_day = calendar.monthrange(year, month)[1]
    first = layout['first_data_row']
    work_days = 0.0
    holiday_days = 0.0
    paid_leave_days = 0.0
    ordinary = 0
    overtime = 0
    seen_days = set()
    for row in range(first, first + 40):
        day_val = _cell(grid, row, layout['day_col'])
        if day_val is None or day_val == '':
            continue
        if isinstance(day_val, dict) and day_val.get('__missing_cache__'):
            raise ValueError(f'{year}-{month:02d} の日付セルに計算結果がありません（数式の再計算はしません）。')
        try:
            day = int(float(day_val))
        except (TypeError, ValueError):
            continue
        if day < 1 or day > last_day:
            continue
        if day in seen_days:
            raise ValueError(f'{year}-{month:02d} の日付 {day} が重複しています。')
        seen_days.add(day)
        start = _cell(grid, row, layout['start_col'])
        leave = _cell(grid, row, layout['leave_col'])
        ord_v = _cell(grid, row, layout['ordinary_col'])
        ot_v = _cell(grid, row, layout['overtime_col'])
        for label, cell in (('普通勤務', ord_v), ('残業', ot_v), ('始業', start), ('休み', leave)):
            if isinstance(cell, dict) and cell.get('__missing_cache__'):
                raise ValueError(
                    f'{year}-{month:02d} 日{day} の{label}に計算結果がありません（数式ライブラリは再計算しません）。'
                )
        start_text = '' if start is None else str(start).strip()
        day_date = date(year, month, day)
        paid = parse_paid_leave(leave)
        if leave is not None and str(leave).strip() and is_paid_leave_cell(leave) and paid is None:
            raise ValueError(f'{year}-{month:02d} 日{day} の有休日数を読み取れません（{leave}）。')
        if start_text:
            work_days += 1.0
            # 半日有休+出勤もあり得るが、fixture では有休日数のみ加算
        if paid is not None:
            paid_leave_days += paid
        elif not start_text:
            # 休日: 青入力帯（当月の実在日）かつ将来日でない、有休でない
            if day_date <= as_of:
                holiday_days += 1.0
        ordinary += _as_minutes(ord_v, f'{year}-{month:02d} 日{day} 普通勤務')
        try:
            overtime += _as_minutes(ot_v, f'{year}-{month:02d} 日{day} 残業')
        except ValueError as e:
            raise ValueError(str(e)) from e
    return {
        'work_days': work_days,
        'holiday_days': holiday_days,
        'paid_leave_days': paid_leave_days,
        'ordinary_minutes': ordinary,
        'overtime_minutes': overtime,
        'total_minutes': ordinary + overtime,
    }


def parse_person_workbook(blob: bytes, months: list[tuple[int, int]], layout: dict, as_of: date | None = None) -> dict[tuple[int, int], dict]:
    sheets = read_xlsx_grid(blob)
    results = {}
    missing = []
    for y, m in months:
        name = layout['sheet_name_format'].format(yyyy=y, mm=m, month=m)
        grid = sheets.get(name)
        if grid is None:
            missing.append(name)
            continue
        results[(y, m)] = aggregate_month_from_grid(grid, y, m, layout, as_of=as_of)
    if missing and not results:
        raise ValueError('対象月のシートが見つかりません: ' + '、'.join(missing[:6]))
    if missing:
        # 一部欠ける場合はファイル単位で失敗（不完全結果を混ぜない）
        raise ValueError('一部の月シートがありません: ' + '、'.join(missing))
    return results


def _unc_to_path(share_root: str) -> Path:
    root = (share_root or '').strip()
    if not root:
        return Path()
    # Windows UNC \\server\share\... → //server/share/... （Linux でも試す）
    if root.startswith('\\\\') or root.startswith('//'):
        normalized = root.replace('\\', '/')
        return Path(normalized)
    return Path(root)


def resolve_person_file(person: dict, settings: dict) -> Path | None:
    pid = str(person['id'])
    override = (settings.get('peopleFiles') or {}).get(pid) or (settings.get('peopleFiles') or {}).get(person['id'])
    name = (override or person.get('source_file') or '').strip()
    if not name:
        return None
    if name.startswith('\\\\') or name.startswith('//') or (len(name) > 2 and name[1] == ':'):
        return _unc_to_path(name)
    root = _unc_to_path(settings.get('shareRoot') or '')
    if not root.parts:
        # 相対指定のみ → DATA 配下の検証用ディレクトリを許可
        fixture_root = Path(DATA_DIR) / 'kintai-fixtures'
        return fixture_root / name
    return root / name


def _read_file_bytes(path: Path) -> tuple[bytes, str]:
    if path.name.startswith('~$'):
        raise ValueError('Excelの一時ファイル（~$）は読みません。')
    # 読取専用。書込ハンドルは開かない
    data = path.read_bytes()
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds')
    except OSError:
        mtime = ''
    return data, mtime


def import_all(c, user, fiscal_start_year: int, as_of: date | None = None) -> dict:
    settings = load_local_settings()
    layout = get_layout(settings.get('layoutId'))
    months = fiscal_months(fiscal_start_year, DEFAULT_FISCAL_START[1])
    people = [dict(r) for r in c.execute(
        'SELECT * FROM kintai_people WHERE active=1 ORDER BY sort_order, id'
    )]
    person_results = []
    for person in people:
        item = {
            'personId': person['id'],
            'displayName': person['display_name'],
            'employeeCode': person['employee_code'],
            'employment': person['employment'],
            'status': '未取得',
            'error': '',
            'monthsUpdated': 0,
        }
        path = resolve_person_file(person, settings)
        if path is None:
            item['status'] = '未取得'
            item['error'] = '読取元ファイルが未設定です。'
            _set_person_status(c, person['id'], item['status'], item['error'], '', '')
            person_results.append(item)
            continue
        item['sourcePath'] = str(path)
        try:
            if not path.is_file():
                raise FileNotFoundError(f'ファイルが見つかりません: {path.name}')
            blob, mtime = _read_file_bytes(path)
            parsed = parse_person_workbook(blob, months, layout, as_of=as_of)
            # ファイル単位で検証後に保存
            now = datetime.now().isoformat(timespec='seconds')
            for (y, m), agg in parsed.items():
                c.execute(
                    '''INSERT INTO kintai_monthly(
                        person_id,year,month,work_days,holiday_days,paid_leave_days,
                        ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
                    ) VALUES(?,?,?,?,?,?,?,?,?,?, 'ok','')
                    ON CONFLICT(person_id,year,month) DO UPDATE SET
                        work_days=excluded.work_days,
                        holiday_days=excluded.holiday_days,
                        paid_leave_days=excluded.paid_leave_days,
                        ordinary_minutes=excluded.ordinary_minutes,
                        overtime_minutes=excluded.overtime_minutes,
                        source_mtime=excluded.source_mtime,
                        imported_at=excluded.imported_at,
                        status='ok',
                        error=''
                    ''',
                    (
                        person['id'], y, m,
                        agg['work_days'], agg['holiday_days'], agg['paid_leave_days'],
                        agg['ordinary_minutes'], agg['overtime_minutes'],
                        mtime, now,
                    ),
                )
            item['status'] = '成功'
            item['monthsUpdated'] = len(parsed)
            item['sourceMtime'] = mtime
            _set_person_status(c, person['id'], '成功', '', str(path), mtime)
        except Exception as e:
            # 失敗時は前回正常データを保持（月次行は触らない）
            item['status'] = 'エラー'
            item['error'] = str(e)[:500]
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        person_results.append(item)
    summary = {
        'fiscal': fiscal_label(fiscal_start_year),
        'layoutId': layout['id'],
        'shareRootConfigured': bool((settings.get('shareRoot') or '').strip()),
        'people': person_results,
    }
    c.execute(
        'INSERT INTO kintai_import_runs(at,actor_id,actor,fiscal_label,summary_json) VALUES(datetime(\'now\',\'localtime\'),?,?,?,?)',
        (
            user['id'] if user else None,
            f"{user['name']} ({user['login']})" if user else 'システム',
            summary['fiscal'],
            json.dumps({'ok': sum(1 for p in person_results if p['status'] == '成功'),
                        'error': sum(1 for p in person_results if p['status'] == 'エラー'),
                        'missing': sum(1 for p in person_results if p['status'] == '未取得')}, ensure_ascii=False),
        ),
    )
    return summary


def _set_person_status(c, person_id, status, error, source_path, source_mtime):
    c.execute(
        '''INSERT INTO kintai_person_status(person_id,last_import_at,last_status,last_error,source_path,source_mtime)
           VALUES(?,datetime('now','localtime'),?,?,?,?)
           ON CONFLICT(person_id) DO UPDATE SET
             last_import_at=excluded.last_import_at,
             last_status=excluded.last_status,
             last_error=excluded.last_error,
             source_path=excluded.source_path,
             source_mtime=excluded.source_mtime
        ''',
        (person_id, status, error or '', source_path or '', source_mtime or ''),
    )


def _month_public(row) -> dict:
    if not row:
        return None
    ordinary = row['ordinary_minutes']
    overtime = row['overtime_minutes']
    total = None if ordinary is None or overtime is None else ordinary + overtime
    return {
        'year': row['year'],
        'month': row['month'],
        'workDays': row['work_days'],
        'holidayDays': row['holiday_days'],
        'paidLeaveDays': row['paid_leave_days'],
        'ordinaryMinutes': ordinary,
        'overtimeMinutes': overtime,
        'totalMinutes': total,
        'ordinaryHm': format_hm(ordinary),
        'overtimeHm': format_hm(overtime),
        'totalHm': format_hm(total),
        'importedAt': row['imported_at'],
        'sourceMtime': row['source_mtime'],
        'status': row['status'],
        'error': row['error'],
    }


def list_overview(c, fiscal_start_year: int, month: int | None = None, person_id: int | None = None) -> dict:
    settings = load_local_settings()
    months = fiscal_months(fiscal_start_year, DEFAULT_FISCAL_START[1])
    people = [dict(r) for r in c.execute(
        'SELECT p.*, s.last_import_at, s.last_status, s.last_error, s.source_path, s.source_mtime '
        'FROM kintai_people p '
        'LEFT JOIN kintai_person_status s ON s.person_id=p.id '
        'WHERE p.active=1 ORDER BY p.sort_order, p.id'
    )]
    rows = []
    if person_id:
        person = next((p for p in people if p['id'] == person_id), None)
        if not person:
            raise ValueError('対象者が見つかりません。')
        month_rows = []
        for y, m in months:
            rec = c.execute(
                'SELECT * FROM kintai_monthly WHERE person_id=? AND year=? AND month=?',
                (person_id, y, m),
            ).fetchone()
            month_rows.append(_month_public(rec) if rec else {
                'year': y, 'month': m, 'status': '未取得', 'error': '',
                'workDays': None, 'holidayDays': None, 'paidLeaveDays': None,
                'ordinaryMinutes': None, 'overtimeMinutes': None, 'totalMinutes': None,
                'ordinaryHm': '', 'overtimeHm': '', 'totalHm': '',
                'importedAt': None, 'sourceMtime': None,
            })
        rows.append({
            'personId': person['id'],
            'employeeCode': person['employee_code'],
            'employment': person['employment'],
            'displayName': person['display_name'],
            'lastStatus': person.get('last_status') or '未取得',
            'lastError': person.get('last_error') or '',
            'lastImportAt': person.get('last_import_at'),
            'months': month_rows,
        })
    else:
        target_month = month or months[0][1]
        # month alone is ambiguous across year boundary; pick the matching (y,m) in fiscal
        ym = next(((y, m) for y, m in months if m == target_month), None)
        if ym is None:
            raise ValueError('対象月が年度の範囲外です。')
        y, m = ym
        for person in people:
            rec = c.execute(
                'SELECT * FROM kintai_monthly WHERE person_id=? AND year=? AND month=?',
                (person['id'], y, m),
            ).fetchone()
            rows.append({
                'personId': person['id'],
                'employeeCode': person['employee_code'],
                'employment': person['employment'],
                'displayName': person['display_name'],
                'lastStatus': person.get('last_status') or '未取得',
                'lastError': person.get('last_error') or '',
                'lastImportAt': person.get('last_import_at'),
                'month': _month_public(rec) if rec else {
                    'year': y, 'month': m, 'status': '未取得', 'error': '',
                    'workDays': None, 'holidayDays': None, 'paidLeaveDays': None,
                    'ordinaryMinutes': None, 'overtimeMinutes': None, 'totalMinutes': None,
                    'ordinaryHm': '', 'overtimeHm': '', 'totalHm': '',
                    'importedAt': None, 'sourceMtime': None,
                },
            })
    last_run = c.execute(
        'SELECT at, actor, fiscal_label, summary_json FROM kintai_import_runs ORDER BY id DESC LIMIT 1'
    ).fetchone()
    # 権限のない利用者には共有パスを返さない（この関数は can_use 後のみ）
    share = settings.get('shareRoot') or ''
    return {
        'fiscalLabel': fiscal_label(fiscal_start_year),
        'fiscalStartYear': fiscal_start_year,
        'fiscalStartMonth': DEFAULT_FISCAL_START[1],
        'months': [{'year': y, 'month': m} for y, m in months],
        'layoutId': settings.get('layoutId') or 'fixture-v1',
        'shareRoot': share,
        'shareRootConfigured': bool(share.strip()),
        'layoutNote': get_layout(settings.get('layoutId')).get('note', ''),
        'lastImport': dict(last_run) if last_run else None,
        'people': [
            {
                'id': p['id'],
                'employeeCode': p['employee_code'],
                'employment': p['employment'],
                'displayName': p['display_name'],
                'sourceFile': p['source_file'],
            }
            for p in people
        ],
        'rows': rows,
        'opsNote': 'サーバー実行ユーザーに共有フォルダの読取権限が必要です。元出勤簿は変更しません。',
    }


def update_person_file(c, person_id: int, source_file: str):
    if type(person_id) is not int:
        raise ValueError('対象者を選択してください。')
    if not isinstance(source_file, str) or len(source_file) > 400:
        raise ValueError('ファイル名は400文字以内です。')
    if '..' in source_file.replace('\\', '/'):
        raise ValueError('相対パスに .. は使えません。')
    row = c.execute('SELECT id FROM kintai_people WHERE id=?', (person_id,)).fetchone()
    if not row:
        raise ValueError('対象者が見つかりません。')
    c.execute('UPDATE kintai_people SET source_file=? WHERE id=?', (source_file.strip(), person_id))


def _query_map(h) -> dict:
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(getattr(h, 'path', '') or '').query)
    return {k: (v[0] if v else '') for k, v in qs.items()}


def handle_get(h, c, path: str):
    denied = require_kintai(h.user)
    if denied:
        return denied
    if path == '/api/kintai':
        q = _query_map(h)
        year = int(q.get('year') or DEFAULT_FISCAL_START[0])
        month = int(q['month']) if q.get('month') not in (None, '') else None
        person_id = int(q['personId']) if q.get('personId') not in (None, '') else None
        return 200, list_overview(c, year, month=month, person_id=person_id)
    if path == '/api/kintai/settings':
        # マーク権限者のみ。資格情報は含めない
        s = load_local_settings()
        return 200, {
            'shareRoot': s.get('shareRoot') or '',
            'layoutId': s.get('layoutId') or 'fixture-v1',
            'layouts': [{'id': k, 'note': v.get('note', '')} for k, v in LAYOUTS.items()],
            'opsNote': 'サーバー実行ユーザーに共有の読取権限が必要です。パスにパスワードを書かないでください。',
        }
    return 404, {'error': '操作が見つかりません。'}


def handle_post(h, c, path: str, d: dict):
    denied = require_kintai(h.user)
    if denied:
        return denied
    if path == '/api/kintai/import':
        year = d.get('year', DEFAULT_FISCAL_START[0])
        if type(year) is not int or year < 2000 or year > 2100:
            raise ValueError('年度の開始年を確認してください。')
        summary = import_all(c, h.user, year)
        return 200, summary
    if path == '/api/kintai/settings':
        # 共有パス変更は利用者管理権限者のみ
        u = h.user
        if not (u['role'] == 'owner' or u['manage']):
            return 403, {'error': '共有パス設定は利用者管理の権限が必要です。'}
        saved = save_local_settings(d, True)
        return 200, saved
    if path == '/api/kintai/person-file':
        if not (h.user['role'] == 'owner' or h.user['manage']):
            return 403, {'error': '対象者の読取元設定は利用者管理の権限が必要です。'}
        update_person_file(c, d.get('personId'), d.get('sourceFile', ''))
        return 200, {'ok': True}
    return 404, {'error': '操作が見つかりません。'}


def build_fixture_xlsx(months_data: dict[tuple[int, int], list[dict]]) -> bytes:
    """検証用 xlsx を生成。months_data[(y,m)] = [{day,start,leave,ordinary_minutes,overtime_minutes}, ...]"""
    from xml.sax.saxutils import escape

    def cell_xml(col, row, value):
        ref = f'{_col_letters(col)}{row}'
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        text = escape(str(value or ''))
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'

    sheets = sorted(months_data.keys())
    out = BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + ''.join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(sheets) + 1)
            )
            + '</Types>',
        )
        z.writestr(
            '_rels/.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        z.writestr(
            'xl/_rels/workbook.xml.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + ''.join(
                f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(sheets) + 1)
            )
            + '</Relationships>',
        )
        sheet_tags = ''.join(
            f'<sheet name="{y}-{m:02d}" sheetId="{i}" r:id="rId{i}"/>'
            for i, (y, m) in enumerate(sheets, 1)
        )
        ss_ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        z.writestr(
            'xl/workbook.xml',
            f'<workbook xmlns="{ss_ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheet_tags}</sheets></workbook>',
        )
        for i, (y, m) in enumerate(sheets, 1):
            days = months_data[(y, m)]
            last = calendar.monthrange(y, m)[1]
            by_day = {int(d['day']): d for d in days}
            rows_xml = '<row r="1">' + ''.join(
                cell_xml(c, 1, h) for c, h in enumerate(['日', '始業', '休み', '普通分', '残業分'], 1)
            ) + '</row>'
            for day in range(1, last + 1):
                d = by_day.get(day, {'day': day, 'start': '', 'leave': '', 'ordinary_minutes': 0, 'overtime_minutes': 0})
                rn = day + 1
                rows_xml += f'<row r="{rn}">' + ''.join([
                    cell_xml(1, rn, day),
                    cell_xml(2, rn, d.get('start') or ''),
                    cell_xml(3, rn, d.get('leave') or ''),
                    cell_xml(4, rn, int(d.get('ordinary_minutes') or 0)),
                    cell_xml(5, rn, int(d.get('overtime_minutes') or 0)),
                ]) + '</row>'
            z.writestr(
                f'xl/worksheets/sheet{i}.xml',
                f'<worksheet xmlns="{ss_ns}"><sheetData>{rows_xml}</sheetData></worksheet>',
            )
    return out.getvalue()
