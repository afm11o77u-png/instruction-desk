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
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'

# 暦年・4月始まりではない。2026年度 = 2026-02〜2027-01
DEFAULT_FISCAL_START = (2026, 2)
LOCAL_SETTINGS_NAME = 'kintai-local.json'
LOCAL_SETTINGS_KEYS = ('shareRoot', 'layoutId', 'peopleFiles', 'peopleLayouts')
PATH_MAX = 1000
# UI・ドキュメント用の例示のみ。自動設定や mark_kintai の推測ONには使わない。
# 主運用は対象者ごとのフルパス。年度ルートは相対指定のときの任意補助。
EXAMPLE_YEAR_SHARE_ROOT = (
    r'\\Desktop-iu7d38q\e\札幌発寒支店共有\札幌発寒支店\札幌発寒支店管理職以外閲覧禁止'
    r'\※閲覧禁止※\出勤簿   2026年度版'
)
# 後方互換エイリアス（旧UIキー）
EXAMPLE_KATO_SHARE_ROOT = EXAMPLE_YEAR_SHARE_ROOT
EXAMPLE_PATHS_BY_EMPLOYMENT = {
    'regular': {
        'label': '正社員（加藤）',
        'employeeCode': '009',
        'employment': 'regular',
        'relative': (
            r'①　正社員（営業手当無し）001～050'
            r'\009  正社員（営業手当無し）出勤簿2026.2～.xlsx'
        ),
        'fullPath': (
            EXAMPLE_YEAR_SHARE_ROOT
            + r'\①　正社員（営業手当無し）001～050'
            + r'\009  正社員（営業手当無し）出勤簿2026.2～.xlsx'
        ),
        'layoutId': 'shukkinbo-2026-seishain-v1',
    },
    'contract': {
        'label': '契約社員（036）',
        'employeeCode': '036',
        'employment': 'contract',
        'relative': (
            r'③　契約社員（営業手当無し）001～100'
            r'\036　契約社員（営業手当無し）出勤簿2026.2～.xlsx'
        ),
        'fullPath': (
            EXAMPLE_YEAR_SHARE_ROOT
            + r'\③　契約社員（営業手当無し）001～100'
            + r'\036　契約社員（営業手当無し）出勤簿2026.2～.xlsx'
        ),
        'layoutId': 'shukkinbo-2026-seishain-v1',
    },
    'part': {
        'label': 'パート（010）',
        'employeeCode': '010',
        'employment': 'part',
        'relative': (
            r'⑤　パート　001～300'
            r'\010　パート用　出勤簿2026.2～.xlsx'
        ),
        'fullPath': (
            EXAMPLE_YEAR_SHARE_ROOT
            + r'\⑤　パート　001～300'
            + r'\010　パート用　出勤簿2026.2～.xlsx'
        ),
        'layoutId': 'shukkinbo-2026-seishain-v1',
    },
}
EXAMPLE_KATO_FILE = EXAMPLE_PATHS_BY_EMPLOYMENT['regular']['fullPath']
EXAMPLE_KATO_RELATIVE = EXAMPLE_PATHS_BY_EMPLOYMENT['regular']['relative']
LAYOUT_SAMPLE_NEEDED = (
    '実出勤簿のシート名・セル番地は未確認のため集計できません。'
    'サンプルxlsx（個人情報を伏せたコピー可）または ImportShukkinbo.bas / live-sync を提供してください。'
    'クラウド開発環境から共有UNCには接続できないため、接続できたようには扱いません。'
)

# 対象者マスタ（リンク用短い名前。共有上の実ファイル名は人が設定。推測で mark_kintai は付けない）
DEFAULT_PEOPLE = [
    {'employee_code': '009', 'employment': 'regular', 'display_name': '加藤 篤郎', 'sort_order': 1, 'file_hint': '01_加藤　篤郎'},
    {'employee_code': '031', 'employment': 'regular', 'display_name': '今城 卓也', 'sort_order': 2, 'file_hint': '02_今城　卓也'},
    # 036 は契約社員フォルダの出勤簿（ユーザー確認のパス例）
    {'employee_code': '036', 'employment': 'contract', 'display_name': '伊藤 良子', 'sort_order': 3, 'file_hint': '03_伊藤　良子'},
    {'employee_code': '037', 'employment': 'contract', 'display_name': '須永 結香', 'sort_order': 4, 'file_hint': '04_須永　結香'},
    {'employee_code': '040', 'employment': 'part', 'display_name': '小川 春佳', 'sort_order': 5, 'file_hint': '05_小川　春佳'},
    {'employee_code': '010', 'employment': 'part', 'display_name': '菊池 敏子', 'sort_order': 6, 'file_hint': '06_菊池　敏子'},
    {'employee_code': '024', 'employment': 'part', 'display_name': '田中 明美', 'sort_order': 7, 'file_hint': '07_田中　明美'},
    {'employee_code': '014', 'employment': 'part', 'display_name': '八島 里恵', 'sort_order': 8, 'file_hint': '08_八島　里恵'},
    {'employee_code': '018', 'employment': 'part', 'display_name': '廣瀬 愛', 'sort_order': 9, 'file_hint': '09_廣瀬　愛'},
    {'employee_code': '028', 'employment': 'part', 'display_name': '小山 さとみ', 'sort_order': 10, 'file_hint': '10_小山　さとみ'},
    {'employee_code': '037', 'employment': 'part', 'display_name': '南田 久美子', 'sort_order': 11, 'file_hint': '11_南田　久美子'},
]

# fixture-v1: 検証用レイアウト（実出勤簿のセル番地ではない）
FIXTURE_LAYOUT = {
    'id': 'fixture-v1',
    'kind': 'fixture-days',
    'note': '検証用。実出勤簿のシート名・列位置とは別物。',
    'sheet_name_format': '{yyyy}-{mm:02d}',  # 例 2026-02
    'header_row': 1,
    'day_col': 1,          # A
    'start_col': 2,        # B 始業
    'leave_col': 3,        # C 休み
    'ordinary_col': 4,     # D 普通勤務（分）
    'overtime_col': 5,     # E 残業（分）
    'first_data_row': 2,
}

# 正社員（営業手当無し）出勤簿 2026 — サンプル
# internal/kintai-sample-009-kato-2026.xlsx でセルを確認済み（推測ではない）
SEISHAIN_2026_MONTH_SHEETS = {
    (2026, 2): '26.2', (2026, 3): '26.3', (2026, 4): '26.4', (2026, 5): '26.5',
    (2026, 6): '26.6', (2026, 7): '26.7', (2026, 8): '26.8', (2026, 9): '26.9',
    (2026, 10): '26.10', (2026, 11): '26.11', (2026, 12): '26.12', (2027, 1): '27.1',
}
# 集計シート B〜M = 2月度〜1月度
SEISHAIN_2026_SUMMARY_COLS = {
    (2026, 2): 2, (2026, 3): 3, (2026, 4): 4, (2026, 5): 5,
    (2026, 6): 6, (2026, 7): 7, (2026, 8): 8, (2026, 9): 9,
    (2026, 10): 10, (2026, 11): 11, (2026, 12): 12, (2027, 1): 13,
}
# 年間ｶﾚﾝﾀﾞｰ row2 の各月度ヘッダ列（日付も同列）
SEISHAIN_2026_CAL_COLS = {
    (2026, 2): 2, (2026, 3): 8, (2026, 4): 14, (2026, 5): 20,
    (2026, 6): 26, (2026, 7): 32, (2026, 8): 38, (2026, 9): 44,
    (2026, 10): 50, (2026, 11): 56, (2026, 12): 62, (2027, 1): 68,
}

SEISHAIN_2026_LAYOUT = {
    'id': 'shukkinbo-2026-seishain-v1',
    'kind': 'summary-shukkinbo',
    'note': (
        '正社員（営業手当無し）出勤簿2026。管理表【マーク】2026の個人シート数式に合わせる。'
        '出勤=集計!行5／有給=行10／普通(J)=法定内①行6／'
        '残業(K)=普通残業行13＋深夜残業行14＋早朝深夜勤務行18（MAX0）／'
        '勤務時間計(L)=普通＋残業。行5=0の月は普通・残業を空白。'
        '休日(C)は管理表で集計非連動のため、WEBは年間カレンダー日付×月シート休み・始業。'
        '月シートA列の年は26.3以降ずれるため日付は年間カレンダーを正とする。'
    ),
    'summary_sheet': '集計',
    'calendar_sheet': '年間ｶﾚﾝﾀﾞｰ',
    'month_sheets': SEISHAIN_2026_MONTH_SHEETS,
    'summary_cols': SEISHAIN_2026_SUMMARY_COLS,
    'calendar_cols': SEISHAIN_2026_CAL_COLS,
    'row_work_days': 5,
    'row_ordinary_hours': 6,   # 法定内時間① → 管理表 J
    'row_paid_leave': 10,      # 有給日数
    # 管理表 K = 行13+14+18（総労働行21−法定内ではない）
    'row_overtime_hours_parts': (13, 14, 18),
    'calendar_first_row': 3,   # 年間ｶﾚﾝﾀﾞｰの日付開始行
    'calendar_last_row': 49,
    'day_first_row': 5,        # 月シートの日付行（カレンダー行+2）
    'day_last_row': 46,
    'date_col': 1,             # A（参照用。年ずれがあるため突合には使わない）
    'leave_col': 4,            # D 休み
    'start_col': 5,            # E 始業
}

LAYOUTS = {
    'fixture-v1': FIXTURE_LAYOUT,
    'shukkinbo-2026-seishain-v1': SEISHAIN_2026_LAYOUT,
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


def preferred_fiscal_start_year(today: date | None = None) -> int:
    """2月始まり。1月は前年度（例: 2027-01 → 開始年 2026）。"""
    today = today or date.today()
    if today.month >= DEFAULT_FISCAL_START[1]:
        return today.year
    return today.year - 1


def fiscal_year_choices(center: int | None = None) -> list[int]:
    """選択候補。必ず DEFAULT（2026）を含み、表示は終了月まで明示する。"""
    c = center if center is not None else preferred_fiscal_start_year()
    years = {c - 1, c, c + 1, DEFAULT_FISCAL_START[0]}
    return sorted(y for y in years if 2000 <= y <= 2100)


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


def hours_to_minutes(value, label: str) -> int:
    """集計シートの労働時間（小数時間）を整数分へ。常に時間として扱う（分との二重判定をしない）。"""
    if value is None or value == '':
        return 0
    if isinstance(value, dict) and value.get('__missing_cache__'):
        raise ValueError(f'{label}に計算結果がありません（数式の再計算はしません）。')
    if isinstance(value, bool):
        raise ValueError(f'{label}の値が不正です。')
    if isinstance(value, (int, float)):
        v = float(value)
        if v < TINY_NEG:
            if v > -1:
                return 0
            raise ValueError(f'{label}が大きな負数です（{v}）。')
        if v < 0:
            return 0
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
        return hours_to_minutes(float(s.replace(',', '')), label)
    except ValueError as e:
        raise ValueError(f'{label}を読み取れません（{s}）。') from e


def _excel_serial_to_date(value) -> date | None:
    """xlsx キャッシュの日付（シリアルまたは文字列）を date へ。"""
    if value is None or value == '':
        return None
    if isinstance(value, dict) and value.get('__missing_cache__'):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        # Excel 1900 日付系（openpyxl/ECMA と同様 1899-12-30 起点）
        try:
            serial = float(value)
        except (TypeError, ValueError):
            return None
        if serial <= 0:
            return None
        return (datetime(1899, 12, 30) + timedelta(days=int(serial))).date()
    s = str(value).strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _require_number(value, label: str) -> float:
    if isinstance(value, dict) and value.get('__missing_cache__'):
        raise ValueError(f'{label}に計算結果がありません（数式の再計算はしません）。')
    if value is None or value == '':
        return 0.0
    if isinstance(value, bool):
        raise ValueError(f'{label}の値が不正です。')
    if isinstance(value, (int, float)):
        v = float(value)
        if v < TINY_NEG and v > -1:
            return 0.0
        if v < 0:
            raise ValueError(f'{label}が負です（{v}）。')
        return v
    s = str(value).strip().replace(',', '')
    try:
        v = float(s)
    except ValueError as e:
        raise ValueError(f'{label}を読み取れません（{value}）。') from e
    if v < 0:
        raise ValueError(f'{label}が負です（{v}）。')
    return v


def _has_start_value(value) -> bool:
    if value is None or value == '':
        return False
    if isinstance(value, dict) and value.get('__missing_cache__'):
        raise ValueError('始業に計算結果がありません（数式の再計算はしません）。')
    if isinstance(value, (int, float)):
        # Excel 時刻シリアル（0〜1）や時間数。0 は未入力扱い
        return abs(float(value)) > 1e-12
    if isinstance(value, datetime):
        return True
    return bool(str(value).strip())


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
    # 実サンプル確認済みの正社員2026レイアウトを既定にする（検証は layoutId=fixture-v1）
    data.setdefault('layoutId', 'shukkinbo-2026-seishain-v1')
    data.setdefault('peopleFiles', {})
    data.setdefault('peopleLayouts', {})
    if not isinstance(data.get('peopleFiles'), dict):
        data['peopleFiles'] = {}
    if not isinstance(data.get('peopleLayouts'), dict):
        data['peopleLayouts'] = {}
    return data


def save_local_settings(patch: dict, actor_may_write: bool) -> dict:
    if not actor_may_write:
        raise PermissionError('共有パス設定を変更する権限がありません。')
    cur = load_local_settings()
    if 'shareRoot' in patch:
        root = patch['shareRoot']
        if not isinstance(root, str) or len(root) > PATH_MAX:
            raise ValueError(f'共有ルートは{PATH_MAX}文字以内で入力してください。')
        # 資格情報を埋め込ませない（user:pass@ やパスワードっぽい断片）
        if '://' in root and '@' in root.split('://', 1)[1].split('/', 1)[0]:
            raise ValueError('共有パスに接続資格情報を含めないでください。')
        cur['shareRoot'] = root.strip()
    if 'layoutId' in patch:
        lid = patch['layoutId']
        if lid not in LAYOUTS:
            raise ValueError(
                '未対応のレイアウトです。利用可: '
                + '、'.join(LAYOUTS.keys())
                + '。'
            )
        cur['layoutId'] = lid
    if 'peopleFiles' in patch:
        pf = patch['peopleFiles']
        if not isinstance(pf, dict):
            raise ValueError('対象者ファイル設定が不正です。')
        clean = {}
        for k, v in pf.items():
            key = str(k)
            if not isinstance(v, str) or len(v) > PATH_MAX:
                raise ValueError(f'ファイルパスは{PATH_MAX}文字以内です。')
            if '..' in v.replace('\\', '/').split('/'):
                raise ValueError('相対パスに .. は使えません。')
            clean[key] = v.strip()
        cur['peopleFiles'] = clean
    if 'peopleLayouts' in patch:
        pl = patch['peopleLayouts']
        if not isinstance(pl, dict):
            raise ValueError('対象者レイアウト設定が不正です。')
        clean_l = {}
        for k, v in pl.items():
            key = str(k)
            if v in (None, ''):
                continue
            if not isinstance(v, str) or v not in LAYOUTS:
                raise ValueError(f'未対応のレイアウトです: {v}')
            clean_l[key] = v
        cur['peopleLayouts'] = clean_l
    path = local_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {k: cur.get(k) for k in LOCAL_SETTINGS_KEYS}


def get_layout(layout_id: str | None = None) -> dict:
    lid = layout_id or load_local_settings().get('layoutId') or 'shukkinbo-2026-seishain-v1'
    layout = LAYOUTS.get(lid)
    if not layout:
        raise ValueError(
            f'レイアウト「{lid}」は未登録です。利用可: ' + '、'.join(LAYOUTS.keys()) + '。'
        )
    return layout


def layout_id_for_person(person: dict, settings: dict | None = None) -> str:
    settings = settings if settings is not None else load_local_settings()
    pl = settings.get('peopleLayouts') or {}
    pid = str(person.get('id') or '')
    lid = (pl.get(pid) or settings.get('layoutId') or 'shukkinbo-2026-seishain-v1').strip()
    return lid if lid in LAYOUTS else 'shukkinbo-2026-seishain-v1'


def split_relative_parts(relative: str) -> list[str]:
    """相対パスを区切り（\\ /）で分割。.. は拒否。"""
    s = (relative or '').replace('\\', '/').strip()
    parts = [p for p in s.split('/') if p and p != '.']
    if any(p == '..' for p in parts):
        raise ValueError('相対パスに .. は使えません。')
    return parts


def join_root_relative(root: Path, relative: str) -> Path:
    """年度ルート＋（雇用区分フォルダ\\ファイル名）を結合。区切りは OS 非依存。"""
    out = root
    for part in split_relative_parts(relative):
        out = out / part
    return out


def person_source_relative(person: dict, settings: dict) -> str:
    pid = str(person['id'])
    override = (settings.get('peopleFiles') or {}).get(pid) or (settings.get('peopleFiles') or {}).get(person['id'])
    return (override or person.get('source_file') or '').strip()


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


def count_holidays_summary_shukkinbo(
    cal_grid: dict,
    month_grid: dict,
    year: int,
    month: int,
    layout: dict,
    as_of: date | None = None,
) -> float:
    """年間カレンダーの月度日付を正とし、対応する月シート行の休み・始業から休日数を数える。

    月シート A 列は 26.3 以降で年がずれることがあるため、日付の突合には使わない。
    カレンダー行 r → 月シート行 r + (day_first_row - calendar_first_row)。
    """
    as_of = as_of or date.today()
    cal_col = layout['calendar_cols'].get((year, month))
    if cal_col is None:
        raise ValueError(f'{year}-{month:02d} のカレンダー列がレイアウトにありません。')
    cal_first = layout['calendar_first_row']
    cal_last = layout.get('calendar_last_row', cal_first + 50)
    day_first = layout['day_first_row']
    row_offset = day_first - cal_first
    leave_col = layout['leave_col']
    start_col = layout['start_col']
    holidays = 0.0
    found_dates = 0
    for cal_row in range(cal_first, cal_last + 1):
        raw = _cell(cal_grid, cal_row, cal_col)
        if isinstance(raw, dict) and raw.get('__missing_cache__'):
            raise ValueError(
                f'{year}-{month:02d} の年間カレンダー日付に計算結果がありません（数式の再計算はしません）。'
            )
        day_date = _excel_serial_to_date(raw)
        if day_date is None:
            continue
        found_dates += 1
        if day_date > as_of:
            continue
        month_row = cal_row + row_offset
        if month_row < day_first or month_row > layout['day_last_row']:
            continue
        leave = _cell(month_grid, month_row, leave_col)
        start = _cell(month_grid, month_row, start_col)
        try:
            if _has_start_value(start):
                continue
        except ValueError as e:
            raise ValueError(f'{year}-{month:02d} 日{day_date.isoformat()} の{e}') from e
        if is_paid_leave_cell(leave):
            continue
        holidays += 1.0
    if found_dates == 0:
        raise ValueError(f'{year}-{month:02d} の年間カレンダーに日付がありません。')
    return holidays


def aggregate_month_from_summary(
    sheets: dict[str, dict],
    year: int,
    month: int,
    layout: dict,
    as_of: date | None = None,
) -> dict:
    """集計シート（出勤・有給・法定内①・残業内訳）＋カレンダー休日。

    管理表個人シート: J=行6、K=行13+14+18。行5=0の月は J/K 空白。
    """
    summary_name = layout['summary_sheet']
    cal_name = layout['calendar_sheet']
    month_sheets = layout['month_sheets']
    summary_cols = layout['summary_cols']
    if (year, month) not in summary_cols or (year, month) not in month_sheets:
        raise ValueError(f'{year}-{month:02d} はこのレイアウトの対象外です。')
    summary = sheets.get(summary_name)
    if summary is None:
        raise ValueError(f'「{summary_name}」シートがありません。')
    cal = sheets.get(cal_name)
    if cal is None:
        raise ValueError(f'「{cal_name}」シートがありません。')
    month_name = month_sheets[(year, month)]
    month_grid = sheets.get(month_name)
    if month_grid is None:
        raise ValueError(f'月シート「{month_name}」がありません。')
    col = summary_cols[(year, month)]
    label = f'{year}-{month:02d}'
    work_days = _require_number(_cell(summary, layout['row_work_days'], col), f'{label} 出勤日数')
    paid_leave_days = _require_number(_cell(summary, layout['row_paid_leave'], col), f'{label} 有給日数')
    # 管理表: IF(集計!行5=0,"", …) → 空月は普通・残業を空白
    if work_days == 0:
        ordinary = None
        overtime = None
        total = None
    else:
        ordinary = hours_to_minutes(
            _cell(summary, layout['row_ordinary_hours'], col), f'{label} 法定内時間①'
        )
        ot_parts = layout.get('row_overtime_hours_parts') or ()
        if not ot_parts:
            raise ValueError(f'{label} の残業行（row_overtime_hours_parts）がレイアウトにありません。')
        ot_hours = 0.0
        for rn in ot_parts:
            part = _require_number(_cell(summary, rn, col), f'{label} 残業内訳行{rn}')
            ot_hours += float(part)
        if ot_hours < 0:
            ot_hours = 0.0  # 管理表 MAX(0, …)
        overtime = hours_to_minutes(ot_hours, f'{label} 残業（行{"+".join(str(r) for r in ot_parts)}）')
        total = ordinary + overtime
    holiday_days = count_holidays_summary_shukkinbo(
        cal, month_grid, year, month, layout, as_of=as_of
    )
    return {
        'work_days': work_days,
        'holiday_days': holiday_days,
        'paid_leave_days': paid_leave_days,
        'ordinary_minutes': ordinary,
        'overtime_minutes': overtime,
        'total_minutes': total,
    }


def parse_person_workbook(blob: bytes, months: list[tuple[int, int]], layout: dict, as_of: date | None = None) -> dict[tuple[int, int], dict]:
    sheets = read_xlsx_grid(blob)
    kind = layout.get('kind') or 'fixture-days'
    if kind == 'summary-shukkinbo':
        results = {}
        errors = []
        for y, m in months:
            try:
                results[(y, m)] = aggregate_month_from_summary(sheets, y, m, layout, as_of=as_of)
            except ValueError as e:
                errors.append(str(e))
        if errors and not results:
            found = '、'.join(list(sheets.keys())[:12]) or '（なし）'
            raise ValueError(
                '出勤簿を集計できません: '
                + '／'.join(errors[:4])
                + f'（ファイル内シート: {found}）。'
            )
        if errors:
            raise ValueError('一部の月を集計できません: ' + '／'.join(errors[:6]))
        return results

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
        found = '、'.join(list(sheets.keys())[:12]) or '（なし）'
        hint = ''
        if layout.get('id') == 'fixture-v1':
            hint = ' ' + LAYOUT_SAMPLE_NEEDED
        raise ValueError(
            '対象月のシートが見つかりません（期待: '
            + '、'.join(missing[:6])
            + '／ファイル内: '
            + found
            + '）。'
            + hint
        )
    if missing:
        # 一部欠ける場合はファイル単位で失敗（不完全結果を混ぜない）
        raise ValueError('一部の月シートがありません: ' + '、'.join(missing))
    return results


def probe_workbook_bytes(blob: bytes) -> dict:
    """読取専用の構造確認。シート名と各シートの使用セル範囲のみ。列意味は推測しない。"""
    sheets = read_xlsx_grid(blob)
    out = []
    for name, grid in sheets.items():
        if not grid:
            out.append({'name': name, 'cells': 0, 'rowMin': None, 'rowMax': None, 'colMin': None, 'colMax': None})
            continue
        rows = [r for r, _ in grid]
        cols = [c for _, c in grid]
        out.append({
            'name': name,
            'cells': len(grid),
            'rowMin': min(rows),
            'rowMax': max(rows),
            'colMin': min(cols),
            'colMax': max(cols),
        })
    names = {s['name'] for s in out}
    known = (
        '集計' in names
        and '年間ｶﾚﾝﾀﾞｰ' in names
        and any(n.startswith('26.') or n.startswith('27.') for n in names)
    )
    note = 'シート名とセル範囲のみ返します（このAPIでは列の意味を判定しません）。'
    if known:
        note += ' 構造は正社員（営業手当無し）2026出勤簿に近いです。layoutId=shukkinbo-2026-seishain-v1 で集計できます。'
    else:
        note += ' ' + LAYOUT_SAMPLE_NEEDED
    return {
        'sheetCount': len(out),
        'sheets': out,
        'note': note,
    }


def _unc_to_path(share_root: str) -> Path:
    root = (share_root or '').strip()
    if not root:
        return Path()
    # Windows UNC \\server\share\... → //server/share/... （Linux でも試す）
    if root.startswith('\\\\') or root.startswith('//'):
        normalized = root.replace('\\', '/')
        return Path(normalized)
    return Path(root)


def resolve_dir_with_normalized_segments(path: Path) -> Path | None:
    """各パス区切りをスペース／全角差で正規化して実在フォルダを探す。見つからなければ None。"""
    if not path or not path.parts:
        return None
    try:
        if path.is_dir():
            return path
    except OSError:
        pass
    parts = list(path.parts)
    path_s = str(path)
    if not path.is_absolute():
        acc = Path('.')
        rest = parts
    elif path_s.startswith('//') and not path_s.startswith('///') and len(parts) >= 3:
        # UNC（\\server\share\... → //server/share/...）。server/share はそのまま、以降を正規化
        acc = Path('//' + parts[1]) / parts[2]
        rest = parts[3:]
    elif parts[0] == '/':
        acc = Path('/')
        rest = parts[1:]
    elif len(parts[0]) >= 2 and parts[0][1] == ':':
        acc = Path(parts[0] + os.sep)
        rest = parts[1:]
    else:
        acc = Path(parts[0])
        rest = parts[1:]
    try:
        if rest and not acc.exists():
            return None
    except OSError:
        return None
    for seg in rest:
        try:
            if not acc.is_dir():
                return None
            children = [p for p in acc.iterdir() if p.is_dir()]
        except OSError:
            return None
        exact = acc / seg
        try:
            if exact.is_dir():
                acc = exact
                continue
        except OSError:
            pass
        key = normalize_filename_key(seg)
        hits = [p for p in children if normalize_filename_key(p.name) == key]
        if len(hits) == 1:
            acc = hits[0]
        else:
            return None
    try:
        return acc if acc.is_dir() else None
    except OSError:
        return None


def normalize_filename_key(name: str) -> str:
    """ファイル名照合用キー。空白圧縮・NFKC（全角括弧等）・波ダッシュ類を揃える。"""
    s = unicodedata.normalize('NFKC', (name or '').strip())
    for ch in ('\u301c', '\uFF5E', '\u223c', '\u2053', '～', '〜'):
        s = s.replace(ch, '~')
    s = re.sub(r'[\s\u3000]+', ' ', s)
    return s.casefold()


def _is_attendance_xlsx(name: str) -> bool:
    lower = name.lower()
    if name.startswith('~$'):
        return False
    return lower.endswith('.xlsx') or lower.endswith('.xlsm')


def list_share_xlsx(directory: Path, prefix: str = '', limit: int = 80) -> list[dict]:
    """共有フォルダ直下の出勤簿候補（読取専用の名前一覧）。"""
    if not directory or not directory.parts:
        return []
    try:
        if not directory.is_dir():
            return []
        entries = list(directory.iterdir())
    except OSError:
        return []
    pref = (prefix or '').strip()
    pref_digits = re.sub(r'\D', '', pref)
    pref_key = normalize_filename_key(pref) if pref else ''
    out = []
    for p in sorted(entries, key=lambda x: x.name):
        if not p.is_file() or not _is_attendance_xlsx(p.name):
            continue
        if pref_key or pref_digits:
            nk = normalize_filename_key(p.name)
            head = re.match(r'^(\d+)', nk)
            ok = False
            if pref_digits and head and head.group(1) == pref_digits:
                ok = True
            if pref_key and (nk.startswith(pref_key) or pref_key in nk):
                ok = True
            if not ok:
                continue
        out.append({'name': p.name, 'path': str(p)})
        if len(out) >= limit:
            break
    return out


def find_matching_share_file(
    directory: Path,
    wanted_name: str,
    employee_code: str = '',
) -> tuple[Path | None, list[str]]:
    """正規化一致、または社員番号で一意な候補を探す。戻り値 (path, 候補名一覧)。"""
    candidates_meta = list_share_xlsx(directory, prefix='', limit=200)
    names = [c['name'] for c in candidates_meta]
    if not names:
        return None, []
    want_key = normalize_filename_key(wanted_name)
    norm_hits = [n for n in names if normalize_filename_key(n) == want_key]
    if len(norm_hits) == 1:
        return directory / norm_hits[0], names
    if len(norm_hits) > 1:
        return directory / norm_hits[0], names
    code = (employee_code or '').strip()
    if code:
        code_key = normalize_filename_key(code)
        code_hits = []
        for n in names:
            nk = normalize_filename_key(n)
            head = re.match(r'^(\d+)', nk)
            if head and head.group(1) == re.sub(r'\D', '', code_key):
                code_hits.append(n)
            elif nk.startswith(code_key + ' ') or nk.startswith(code_key + '_'):
                code_hits.append(n)
        if len(code_hits) == 1:
            return directory / code_hits[0], names
        # 絞り込み候補として社員番号一致だけ返す
        if code_hits:
            return None, code_hits
    return None, names[:12]


def _share_root_path(settings: dict) -> Path:
    root = _unc_to_path(settings.get('shareRoot') or '')
    if not root.parts:
        return root
    fuzzy = resolve_dir_with_normalized_segments(root)
    return fuzzy if fuzzy is not None else root


def is_absolute_source_path(name: str) -> bool:
    """フルUNC・ドライブレター付き・POSIX絶対パスか。"""
    s = (name or '').strip()
    if not s:
        return False
    if s.startswith('\\\\') or s.startswith('//'):
        return True
    if len(s) > 2 and s[1] == ':':
        return True
    if s.startswith('/'):
        return True
    return False


def resolve_person_file(person: dict, settings: dict) -> Path | None:
    """設定上の読取元パス。存在しなくても結合結果を返す（未設定のみ None）。

    読取元は次のいずれか:
    - フルUNC / ドライブレター付き絶対パス（推奨・主運用）
    - 年度ルート（shareRoot）からの相対（任意補助）
    - ファイル名のみ（年度ルート直下、または検証用 fixtures）
    """
    name = person_source_relative(person, settings)
    if not name:
        return None
    if is_absolute_source_path(name):
        full = _unc_to_path(name)
        # フルパスでも親フォルダのスペース差を吸収してからファイル名照合
        parent = resolve_dir_with_normalized_segments(full.parent) or full.parent
        return parent / full.name
    root = _share_root_path(settings)
    if not root.parts:
        # 相対指定のみ → DATA 配下の検証用ディレクトリを許可
        fixture_root = Path(DATA_DIR) / 'kintai-fixtures'
        return join_root_relative(fixture_root, name)
    # 相対パスの中間フォルダも正規化して辿る
    parts = split_relative_parts(name)
    if not parts:
        return None
    acc = root
    for seg in parts[:-1]:
        nxt = acc / seg
        try:
            if nxt.is_dir():
                acc = nxt
                continue
        except OSError:
            pass
        fuzzy = None
        try:
            children = [p for p in acc.iterdir() if p.is_dir()]
            key = normalize_filename_key(seg)
            hits = [p for p in children if normalize_filename_key(p.name) == key]
            if len(hits) == 1:
                fuzzy = hits[0]
        except OSError:
            fuzzy = None
        acc = fuzzy if fuzzy is not None else nxt
    return acc / parts[-1]


def resolve_person_file_existing(person: dict, settings: dict) -> dict:
    """実在ファイルを解決。kind: unset | ok | not_found | access。"""
    configured = resolve_person_file(person, settings)
    if configured is None:
        return {
            'ok': False,
            'kind': 'unset',
            'path': None,
            'configuredPath': '',
            'message': '読取元ファイルが未設定です。対象者の読取元を保存してください。',
            'resolvedNote': '',
            'candidates': [],
        }
    configured_s = str(configured)
    try:
        if configured.is_file():
            return {
                'ok': True,
                'kind': 'ok',
                'path': configured,
                'configuredPath': configured_s,
                'message': '',
                'resolvedNote': '',
                'candidates': [],
            }
    except OSError as e:
        return {
            'ok': False,
            'kind': 'access',
            'path': None,
            'configuredPath': configured_s,
            'message': f'パスにアクセスできません（{type(e).__name__}: {e}）。探索: {configured_s}',
            'resolvedNote': '',
            'candidates': [],
        }

    parent = configured.parent
    wanted = configured.name
    matched, candidates = find_matching_share_file(
        parent, wanted, employee_code=str(person.get('employee_code') or ''),
    )
    if matched is not None:
        try:
            if matched.is_file():
                return {
                    'ok': True,
                    'kind': 'ok',
                    'path': matched,
                    'configuredPath': configured_s,
                    'message': '',
                    'resolvedNote': f'ファイル名を正規化して一致: {matched.name}',
                    'candidates': candidates[:8],
                }
        except OSError as e:
            return {
                'ok': False,
                'kind': 'access',
                'path': None,
                'configuredPath': configured_s,
                'message': f'候補ファイルにアクセスできません（{type(e).__name__}: {e}）: {matched}',
                'resolvedNote': '',
                'candidates': candidates[:8],
            }

    root = _share_root_path(settings)
    root_ok = False
    try:
        root_ok = bool(root.parts) and root.is_dir()
    except OSError:
        root_ok = False
    hint_parts = [f'探索パス: {configured_s}']
    configured_name = person_source_relative(person, settings)
    if not is_absolute_source_path(configured_name):
        if not (settings.get('shareRoot') or '').strip():
            hint_parts.append(
                '相対パス指定ですが年度ルート未設定です。フルパスを人ごとに保存するか、'
                '任意の年度ルートを保存してください。'
            )
        elif not root_ok:
            hint_parts.append(
                f'年度ルートにフォルダとしてアクセスできません: {settings.get("shareRoot")}'
                '（UNC未接続・権限・パス誤り・フォルダ名のスペース差）。'
            )
    code = str(person.get('employee_code') or '').strip()
    code_cands = []
    if code:
        ck = normalize_filename_key(code)
        for n in candidates:
            nk = normalize_filename_key(n)
            head = re.match(r'^(\d+)', nk)
            if (head and head.group(1) == re.sub(r'\D', '', ck)) or nk.startswith(ck):
                code_cands.append(n)
    show = code_cands or candidates[:8]
    if show:
        hint_parts.append('候補: ' + '／'.join(show[:8]))
    elif root_ok:
        hint_parts.append('フォルダ内に一致するxlsxが見つかりません。')
    return {
        'ok': False,
        'kind': 'not_found',
        'path': None,
        'configuredPath': configured_s,
        'message': 'ファイルが見つかりません。' + ' '.join(hint_parts),
        'resolvedNote': '',
        'candidates': show[:8],
    }


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
    default_layout = get_layout(settings.get('layoutId'))
    months = fiscal_months(fiscal_start_year, DEFAULT_FISCAL_START[1])
    people = [dict(r) for r in c.execute(
        'SELECT * FROM kintai_people WHERE active=1 ORDER BY sort_order, id'
    )]
    person_results = []
    share_root = (settings.get('shareRoot') or '').strip()
    for person in people:
        layout_id = layout_id_for_person(person, settings)
        try:
            layout = get_layout(layout_id)
        except ValueError as e:
            item = {
                'personId': person['id'],
                'displayName': person['display_name'],
                'employeeCode': person['employee_code'],
                'employment': person['employment'],
                'status': 'エラー',
                'error': str(e),
                'errorKind': 'layout',
                'monthsUpdated': 0,
                'sourcePath': '',
                'configuredPath': '',
                'resolvedNote': '',
                'candidates': [],
                'layoutId': layout_id,
            }
            _set_person_status(c, person['id'], 'エラー', item['error'], '', '')
            person_results.append(item)
            continue
        item = {
            'personId': person['id'],
            'displayName': person['display_name'],
            'employeeCode': person['employee_code'],
            'employment': person['employment'],
            'status': '未取得',
            'error': '',
            'errorKind': '',
            'monthsUpdated': 0,
            'sourcePath': '',
            'configuredPath': '',
            'resolvedNote': '',
            'candidates': [],
            'layoutId': layout['id'],
        }
        resolved = resolve_person_file_existing(person, settings)
        item['candidates'] = resolved.get('candidates') or []
        item['configuredPath'] = resolved.get('configuredPath') or ''
        item['resolvedNote'] = resolved.get('resolvedNote') or ''
        item['sourcePath'] = str(resolved['path']) if resolved.get('path') else item['configuredPath']

        if resolved['kind'] == 'unset':
            item['status'] = '未取得'
            item['error'] = resolved['message']
            item['errorKind'] = 'unset'
            _set_person_status(c, person['id'], item['status'], item['error'], '', '')
            person_results.append(item)
            continue

        if not resolved['ok']:
            item['status'] = 'エラー'
            item['error'] = resolved['message']
            item['errorKind'] = resolved['kind']  # not_found | access
            _set_person_status(c, person['id'], 'エラー', item['error'], item['sourcePath'], '')
            person_results.append(item)
            continue

        path = resolved['path']
        try:
            blob, mtime = _read_file_bytes(path)
            parsed = parse_person_workbook(blob, months, layout, as_of=as_of)
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
            item['errorKind'] = ''
            item['monthsUpdated'] = len(parsed)
            item['sourceMtime'] = mtime
            item['sourcePath'] = str(path)
            _set_person_status(c, person['id'], '成功', '', str(path), mtime)
        except PermissionError as e:
            item['status'] = 'エラー'
            item['errorKind'] = 'permission'
            item['error'] = f'読取権限がありません: {path}（{e}）'[:500]
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        except FileNotFoundError as e:
            item['status'] = 'エラー'
            item['errorKind'] = 'not_found'
            item['error'] = f'ファイルが見つかりません: {path}（{e}）'[:500]
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        except OSError as e:
            item['status'] = 'エラー'
            item['errorKind'] = 'io'
            item['error'] = f'ファイルを開けません（{type(e).__name__}）: {path} — {e}'[:500]
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        except ValueError as e:
            item['status'] = 'エラー'
            item['errorKind'] = 'parse'
            item['error'] = f'パース失敗: {e}'[:500]
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        except Exception as e:
            item['status'] = 'エラー'
            item['errorKind'] = 'other'
            item['error'] = f'{type(e).__name__}: {e}'[:500]
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        person_results.append(item)
    summary = {
        'fiscal': fiscal_label(fiscal_start_year),
        'layoutId': default_layout['id'],
        'shareRoot': share_root,
        'shareRootConfigured': bool(share_root),
        'people': person_results,
        'counts': {
            'ok': sum(1 for p in person_results if p['status'] == '成功'),
            'error': sum(1 for p in person_results if p['status'] == 'エラー'),
            'missing': sum(1 for p in person_results if p['status'] == '未取得'),
        },
    }
    c.execute(
        'INSERT INTO kintai_import_runs(at,actor_id,actor,fiscal_label,summary_json) VALUES(datetime(\'now\',\'localtime\'),?,?,?,?)',
        (
            user['id'] if user else None,
            f"{user['name']} ({user['login']})" if user else 'システム',
            summary['fiscal'],
            json.dumps(summary['counts'], ensure_ascii=False),
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


def list_overview(
    c,
    fiscal_start_year: int,
    month: int | None = None,
    person_id: int | None = None,
    year_month: tuple[int, int] | None = None,
) -> dict:
    settings = load_local_settings()
    months = fiscal_months(fiscal_start_year, DEFAULT_FISCAL_START[1])
    people = [dict(r) for r in c.execute(
        'SELECT p.*, s.last_import_at, s.last_status, s.last_error, s.source_path, s.source_mtime '
        'FROM kintai_people p '
        'LEFT JOIN kintai_person_status s ON s.person_id=p.id '
        'WHERE p.active=1 ORDER BY p.sort_order, p.id'
    )]
    pf = settings.get('peopleFiles') or {}
    pl = settings.get('peopleLayouts') or {}
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
        if year_month is not None:
            ym = year_month
            if ym not in months:
                raise ValueError('対象月が年度の範囲外です。')
        else:
            target_month = month if month is not None else months[0][1]
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
    layout = get_layout(settings.get('layoutId'))
    return {
        'fiscalLabel': fiscal_label(fiscal_start_year),
        'fiscalStartYear': fiscal_start_year,
        'fiscalStartMonth': DEFAULT_FISCAL_START[1],
        'preferredFiscalStartYear': preferred_fiscal_start_year(),
        'fiscalYearOptions': [
            {'startYear': y, 'label': fiscal_label(y)} for y in fiscal_year_choices(fiscal_start_year)
        ],
        'months': [{'year': y, 'month': m, 'key': f'{y}-{m:02d}'} for y, m in months],
        'layoutId': layout['id'],
        'shareRoot': share,
        'shareRootConfigured': bool(share.strip()),
        'layoutNote': layout.get('note', ''),
        'realLayoutReady': layout['id'] != 'fixture-v1',
        'sampleNeeded': LAYOUT_SAMPLE_NEEDED if layout['id'] == 'fixture-v1' else '',
        'exampleKatoShareRoot': EXAMPLE_YEAR_SHARE_ROOT,
        'exampleYearShareRoot': EXAMPLE_YEAR_SHARE_ROOT,
        'exampleKatoFile': EXAMPLE_KATO_FILE,
        'examplePathsByEmployment': EXAMPLE_PATHS_BY_EMPLOYMENT,
        'lastImport': dict(last_run) if last_run else None,
        'people': [
            {
                'id': p['id'],
                'employeeCode': p['employee_code'],
                'employment': p['employment'],
                'displayName': p['display_name'],
                'sourceFile': (pf.get(str(p['id'])) or p['source_file'] or ''),
                'layoutId': pl.get(str(p['id'])) or settings.get('layoutId') or 'shukkinbo-2026-seishain-v1',
                'fileHint': next(
                    (d['file_hint'] for d in DEFAULT_PEOPLE
                     if d['employee_code'] == p['employee_code'] and d['employment'] == p['employment']),
                    '',
                ),
            }
            for p in people
        ],
        'rows': rows,
        'opsNote': (
            '対象者ごとに出勤簿のフルパスを1本保存し、「出勤簿から更新」で自動読込します。'
            '元出勤簿は変更しません。サーバー実行ユーザーに読取権限が必要です。'
            '年度ルートは相対パス運用時の任意補助です。'
        ),
    }


def update_person_file(c, person_id: int, source_file: str, layout_id: str | None = None):
    if type(person_id) is not int:
        raise ValueError('対象者を選択してください。')
    if not isinstance(source_file, str) or len(source_file) > PATH_MAX:
        raise ValueError(f'ファイルパスは{PATH_MAX}文字以内です。')
    if '..' in source_file.replace('\\', '/'):
        raise ValueError('相対パスに .. は使えません。')
    row = c.execute('SELECT id FROM kintai_people WHERE id=?', (person_id,)).fetchone()
    if not row:
        raise ValueError('対象者が見つかりません。')
    c.execute('UPDATE kintai_people SET source_file=? WHERE id=?', (source_file.strip(), person_id))
    if layout_id is not None:
        if layout_id == '':
            # 個別指定を外して既定レイアウトへ
            cur = load_local_settings()
            pl = dict(cur.get('peopleLayouts') or {})
            pl.pop(str(person_id), None)
            save_local_settings({'peopleLayouts': pl}, True)
        else:
            if layout_id not in LAYOUTS:
                raise ValueError('未対応のレイアウトです。')
            save_local_settings(
                {'peopleLayouts': {**(load_local_settings().get('peopleLayouts') or {}), str(person_id): layout_id}},
                True,
            )


def probe_person(c, person_id: int) -> dict:
    """対象者ファイルを読取専用で開き、シート一覧のみ返す（集計しない）。"""
    if type(person_id) is not int:
        raise ValueError('対象者を選択してください。')
    person = c.execute('SELECT * FROM kintai_people WHERE id=?', (person_id,)).fetchone()
    if not person:
        raise ValueError('対象者が見つかりません。')
    settings = load_local_settings()
    resolved = resolve_person_file_existing(dict(person), settings)
    if resolved['kind'] == 'unset':
        raise ValueError(resolved['message'])
    if not resolved['ok']:
        raise FileNotFoundError(resolved['message'])
    path = resolved['path']
    blob, mtime = _read_file_bytes(path)
    probed = probe_workbook_bytes(blob)
    probed['sourcePath'] = str(path)
    probed['configuredPath'] = resolved.get('configuredPath') or ''
    probed['resolvedNote'] = resolved.get('resolvedNote') or ''
    probed['sourceMtime'] = mtime
    probed['personId'] = person_id
    probed['displayName'] = person['display_name']
    return probed


def list_share_files_for_user(user, prefix: str = '', subdir: str = '', person: dict | None = None) -> dict:
    """権限者向け: 年度ルート直下、または雇用区分サブフォルダのxlsx一覧。"""
    if not user or not (user['role'] == 'owner' or user.get('manage')):
        return {'error': '共有フォルダ一覧は利用者管理の権限が必要です。'}, 403
    settings = load_local_settings()
    root_s = (settings.get('shareRoot') or '').strip()
    if not root_s:
        raise ValueError(
            '年度ルートが未設定です。入力後に「年度ルートとレイアウトを保存」を押してください。'
        )
    root = _share_root_path(settings)
    try:
        if not root.is_dir():
            raise ValueError(
                f'年度ルートにフォルダとしてアクセスできません: {root_s}（UNC未接続・権限・パス誤り・フォルダ名のスペース差）。'
            )
    except OSError as e:
        raise ValueError(f'年度ルートを開けません（{type(e).__name__}: {e}）: {root_s}') from e

    # 対象者の相対パスから雇用区分フォルダを推定
    rel_subdir = (subdir or '').strip()
    if not rel_subdir and person:
        src = person_source_relative(person, settings)
        if src and not (src.startswith('\\\\') or src.startswith('//') or (len(src) > 2 and src[1] == ':')):
            parts = split_relative_parts(src)
            if len(parts) >= 2:
                rel_subdir = '\\'.join(parts[:-1])

    target = join_root_relative(root, rel_subdir) if rel_subdir else root
    # 中間フォルダの正規化
    fuzzy_target = resolve_dir_with_normalized_segments(target)
    if fuzzy_target is not None:
        target = fuzzy_target
    try:
        if not target.is_dir():
            raise ValueError(
                f'一覧対象フォルダにアクセスできません: {target}（subdir={rel_subdir or "（年度ルート直下）"}）。'
            )
    except OSError as e:
        raise ValueError(f'フォルダを開けません（{type(e).__name__}: {e}）: {target}') from e

    files = list_share_xlsx(target, prefix=prefix, limit=100)
    # 候補選択用に、年度ルートからの相対パスも返す
    out_files = []
    for f in files:
        name = f['name']
        rel = f'{rel_subdir}\\{name}' if rel_subdir else name
        out_files.append({'name': name, 'path': f['path'], 'relative': rel.replace('/', '\\')})

    subfolders = []
    try:
        for p in sorted(root.iterdir(), key=lambda x: x.name):
            if p.is_dir() and not p.name.startswith('.'):
                subfolders.append({'name': p.name, 'relative': p.name})
    except OSError:
        subfolders = []

    return {
        'shareRoot': root_s,
        'resolvedShareRoot': str(root),
        'subdir': rel_subdir,
        'listPath': str(target),
        'prefix': prefix or '',
        'count': len(out_files),
        'files': out_files,
        'subfolders': subfolders,
        'note': (
            '年度ルート直下の雇用区分フォルダ、または指定サブフォルダ内のxlsx（読取専用）。'
            ' 候補を選ぶと「雇用区分フォルダ\\ファイル名」の相対パスが入ります。'
        ),
    }


def _query_map(h) -> dict:
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(getattr(h, 'path', '') or '').query)
    return {k: (v[0] if v else '') for k, v in qs.items()}


def _parse_ym(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    m = re.fullmatch(r'(\d{4})-(\d{1,2})', str(value).strip())
    if not m:
        raise ValueError('対象月は YYYY-MM 形式で指定してください。')
    y, mo = int(m.group(1)), int(m.group(2))
    if not 1 <= mo <= 12:
        raise ValueError('対象月が不正です。')
    return y, mo


def handle_get(h, c, path: str):
    denied = require_kintai(h.user)
    if denied:
        return denied
    if path == '/api/kintai':
        q = _query_map(h)
        year = int(q.get('year') or preferred_fiscal_start_year() or DEFAULT_FISCAL_START[0])
        person_id = int(q['personId']) if q.get('personId') not in (None, '') else None
        ym = _parse_ym(q.get('ym'))
        month = int(q['month']) if q.get('month') not in (None, '') else None
        return 200, list_overview(c, year, month=month, person_id=person_id, year_month=ym)
    if path == '/api/kintai/settings':
        # マーク権限者のみ。資格情報は含めない
        s = load_local_settings()
        return 200, {
            'shareRoot': s.get('shareRoot') or '',
            'layoutId': s.get('layoutId') or 'shukkinbo-2026-seishain-v1',
            'layouts': [{'id': k, 'note': v.get('note', '')} for k, v in LAYOUTS.items()],
            'exampleKatoShareRoot': EXAMPLE_YEAR_SHARE_ROOT,
            'exampleYearShareRoot': EXAMPLE_YEAR_SHARE_ROOT,
            'exampleKatoFile': EXAMPLE_KATO_FILE,
            'examplePathsByEmployment': EXAMPLE_PATHS_BY_EMPLOYMENT,
            'peopleLayouts': s.get('peopleLayouts') or {},
            'sampleNeeded': LAYOUT_SAMPLE_NEEDED if (s.get('layoutId') or '') == 'fixture-v1' else '',
            'opsNote': (
                '対象者ごとに出勤簿フルパスを1本保存します。パスにパスワードを書かないでください。'
                ' 年度ルートは相対パス用の任意補助です。'
            ),
        }
    return 404, {'error': '操作が見つかりません。'}


def handle_post(h, c, path: str, d: dict):
    denied = require_kintai(h.user)
    if denied:
        return denied
    if path == '/api/kintai/import':
        year = d.get('year', preferred_fiscal_start_year())
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
        pid = d.get('personId')
        layout_id = d.get('layoutId') if 'layoutId' in d else None
        update_person_file(c, pid, d.get('sourceFile', ''), layout_id=layout_id)
        # あわせて local peopleFiles も更新（相対／フルUNC）
        if type(pid) is int:
            save_local_settings(
                {'peopleFiles': {**(load_local_settings().get('peopleFiles') or {}), str(pid): str(d.get('sourceFile') or '').strip()}},
                True,
            )
        return 200, {'ok': True}
    if path == '/api/kintai/probe':
        try:
            return 200, probe_person(c, d.get('personId'))
        except FileNotFoundError as e:
            raise ValueError(str(e)) from e
        except OSError as e:
            raise ValueError(f'ファイルを読めません: {e}') from e
    if path == '/api/kintai/list-files':
        if not (h.user['role'] == 'owner' or h.user['manage']):
            return 403, {'error': '共有フォルダ一覧は利用者管理の権限が必要です。'}
        prefix = d.get('prefix', '')
        if prefix is None:
            prefix = ''
        if not isinstance(prefix, str) or len(prefix) > 80:
            raise ValueError('絞り込みは80文字以内です。')
        subdir = d.get('subdir', '') or ''
        if not isinstance(subdir, str) or len(subdir) > PATH_MAX:
            raise ValueError('サブフォルダ指定が長すぎます。')
        if '..' in subdir.replace('\\', '/'):
            raise ValueError('相対パスに .. は使えません。')
        person = None
        pid = d.get('personId')
        if type(pid) is int:
            person = c.execute('SELECT * FROM kintai_people WHERE id=?', (pid,)).fetchone()
            if person and not prefix:
                prefix = person['employee_code'] or ''
            person = dict(person) if person else None
        result = list_share_files_for_user(h.user, prefix=prefix, subdir=subdir, person=person)
        if isinstance(result, tuple):
            return result[1], result[0]
        return 200, result
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
