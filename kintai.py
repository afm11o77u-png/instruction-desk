"""勤怠管理（マーク）: 個人出勤簿 xlsx を読取専用で取り込み、月別集計を保存・表示する。

元出勤簿の書換・保存・名前変更は行わない。数式の再計算もしない。
実ファイルのセル番地・シート名が未確認の場合は layoutId を fixture 以外にしない。
共有ルート等の端末固有設定は data/kintai-local.json（Git 除外）に置く。
"""
from __future__ import annotations

import base64
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

import kintai_shift
import kintai_bihin

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'

# 暦年・4月始まりではない。2026年度 = 2026-02〜2027-01
DEFAULT_FISCAL_START = (2026, 2)
LOCAL_SETTINGS_NAME = 'kintai-local.json'
LOCAL_SETTINGS_KEYS = ('shareRoot', 'layoutId', 'peopleFiles', 'peopleLayouts')
PATHS_LIST_NAME = 'kintai-paths.xlsx'  # 出勤簿パス一覧のローカルコピー（Git除外）
PATH_MAX = 1000
# Excel から貼った UNC 先頭に付くことがある双方向制御文字
PATH_BIDI_MARKS = dict.fromkeys(map(ord, '\u202a\u202b\u202c\u202d\u202e\u200e\u200f\ufeff'))
RETIREE_HEADING_RE = re.compile(r'^退職者')
# 出勤簿パス.xlsx のセクション見出し（読み仮名が張り付いていても normalize 後に一致）
EMPLOYMENT_SECTION_LABELS = {
    '正社員': 'regular',
    '契約社員': 'contract',
    'パート': 'part',
}
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
LAYOUT_SAMPLE_NEEDED_MONTHDOT = (
    '契約・パート出勤簿は月シート名が「2026.2」形式で、正社員の「26.2／集計／年間ｶﾚﾝﾀﾞｰ」とは別物です。'
    '雇用区分の既定は shukkinbo-2026-monthdot-v1（契約）／shukkinbo-2026-monthdot-part-v1（パート）です。'
)

# 対象者マスタ（リンク用短い名前。共有上の実ファイル名は人が設定。推測で mark_kintai は付けない）
# sort_order=0 はシフト表先頭固定。社員番号未確認の人は provisional code（パスは空・推測しない）
DEFAULT_PEOPLE = [
    {
        'employee_code': 'pending',
        'employment': 'regular',
        'display_name': '齊藤 遼太',
        'sort_order': 0,
        'file_hint': '',
        'provisional': True,
    },
    {'employee_code': '009', 'employment': 'regular', 'display_name': '加藤 篤郎', 'sort_order': 1, 'file_hint': '01_加藤　篤郎'},
    # 出勤簿パス.xlsx の契約社員セクション（パスも契約フォルダ）
    {'employee_code': '031', 'employment': 'contract', 'display_name': '今城 卓也', 'sort_order': 2, 'file_hint': '02_今城　卓也'},
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
# 既存DB向け: シフト表の先頭に固定する在籍者（出勤簿マスタと共有・二重名簿にしない）
SHIFT_TOP_PEOPLE = [p for p in DEFAULT_PEOPLE if int(p.get('sort_order') or 0) == 0]

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

# 契約／パート出勤簿 2026 — サンプルでセル確認済み（推測ではない）
# 契約: internal/kintai-sample-031-imaki-keiyaku-2026.xlsx
# パート: internal/kintai-sample-010-kikuchi-part-2026.xlsx
# シート: 2026.2〜2027.1 + 集計（年間カレンダー無し）。集計行は正社員と同位置。
MONTHDOT_2026_MONTH_SHEETS = {
    (2026, 2): '2026.2', (2026, 3): '2026.3', (2026, 4): '2026.4', (2026, 5): '2026.5',
    (2026, 6): '2026.6', (2026, 7): '2026.7', (2026, 8): '2026.8', (2026, 9): '2026.9',
    (2026, 10): '2026.10', (2026, 11): '2026.11', (2026, 12): '2026.12', (2027, 1): '2027.1',
}

# 正社員（営業手当無し）出勤簿 2026 — サンプル
# internal/kintai-sample-009-kato-2026.xlsx でセルを確認済み（推測ではない）
SEISHAIN_2026_MONTH_SHEETS = {
    (2026, 2): '26.2', (2026, 3): '26.3', (2026, 4): '26.4', (2026, 5): '26.5',
    (2026, 6): '26.6', (2026, 7): '26.7', (2026, 8): '26.8', (2026, 9): '26.9',
    (2026, 10): '26.10', (2026, 11): '26.11', (2026, 12): '26.12', (2027, 1): '27.1',
}
# 集計シート B〜M = 2月度〜1月度（正社員・契約・パート共通）
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

# 契約社員（営業手当無し）出勤簿 2026 — 今城サンプルで確認
MONTHDOT_2026_CONTRACT_LAYOUT = {
    'id': 'shukkinbo-2026-monthdot-v1',
    'kind': 'summary-monthdot',
    'note': (
        '契約社員出勤簿2026（今城サンプル確認済み）。月シート 2026.2〜2027.1＋集計。'
        '出勤=集計!行5／有給=行10／普通=法定内①行6／'
        '残業=普通残業行13＋深夜残業行14＋早朝深夜勤務行18（負は0）／'
        '勤務時間計=普通＋残業。行5=0の月は普通・残業を空白。'
        '休日は月シート日付（A列・各42日）×休み(D)・始業(E)。年間カレンダー無し。'
    ),
    'summary_sheet': '集計',
    'month_sheets': MONTHDOT_2026_MONTH_SHEETS,
    'summary_cols': SEISHAIN_2026_SUMMARY_COLS,
    'row_work_days': 5,
    'row_ordinary_hours': 6,
    'row_paid_leave': 10,
    'row_overtime_hours_parts': (13, 14, 18),
    'day_first_row': 5,
    'day_last_row': 46,
    'date_col': 1,   # A
    'leave_col': 4,  # D 休み
    'start_col': 5,  # E 始業時間（契約）
}

# パートタイマー出勤簿 2026 — 菊池サンプルで確認
# 集計行は契約と同じ。月シートの始業は H 列（契約の E ではない。E は有休時間）
MONTHDOT_2026_PART_LAYOUT = {
    'id': 'shukkinbo-2026-monthdot-part-v1',
    'kind': 'summary-monthdot',
    'note': (
        'パート出勤簿2026（菊池サンプル確認済み）。月シート 2026.2〜2027.1＋集計。'
        '出勤=集計!行5／有給=行10／普通=法定内①行6／'
        '残業=普通残業行13＋深夜残業行14＋早朝深夜勤務行18（負は0）／'
        '勤務時間計=普通＋残業。行5=0の月は普通・残業を空白。'
        '休日は月シート日付（A列・各42日）×休み(D)・始業(H)。年間カレンダー無し。'
    ),
    'summary_sheet': '集計',
    'month_sheets': MONTHDOT_2026_MONTH_SHEETS,
    'summary_cols': SEISHAIN_2026_SUMMARY_COLS,
    'row_work_days': 5,
    'row_ordinary_hours': 6,
    'row_paid_leave': 10,
    'row_overtime_hours_parts': (13, 14, 18),
    'day_first_row': 5,
    'day_last_row': 46,
    'date_col': 1,   # A
    'leave_col': 4,  # D 休み
    'start_col': 8,  # H 始業時間（パート。Eは有休時間のため使わない）
}

# 旧 pending ID（設定に残っていても契約レイアウトとして読める）
MONTHDOT_2026_LAYOUT = {
    **MONTHDOT_2026_CONTRACT_LAYOUT,
    'id': 'shukkinbo-2026-monthdot-pending',
    'note': MONTHDOT_2026_CONTRACT_LAYOUT['note'] + '（旧IDエイリアス。新規は monthdot-v1／part-v1）',
}

LAYOUTS = {
    'fixture-v1': FIXTURE_LAYOUT,
    'shukkinbo-2026-seishain-v1': SEISHAIN_2026_LAYOUT,
    'shukkinbo-2026-monthdot-v1': MONTHDOT_2026_CONTRACT_LAYOUT,
    'shukkinbo-2026-monthdot-part-v1': MONTHDOT_2026_PART_LAYOUT,
    'shukkinbo-2026-monthdot-pending': MONTHDOT_2026_LAYOUT,
}

# 雇用区分 → 既定レイアウト（peopleLayouts 個別指定が優先）
DEFAULT_LAYOUT_BY_EMPLOYMENT = {
    'regular': 'shukkinbo-2026-seishain-v1',
    'contract': 'shukkinbo-2026-monthdot-v1',
    'part': 'shukkinbo-2026-monthdot-part-v1',
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
    people_cols = {r['name'] for r in c.execute('PRAGMA table_info(kintai_people)')}
    if 'retired' not in people_cols:
        c.execute('ALTER TABLE kintai_people ADD COLUMN retired INTEGER NOT NULL DEFAULT 0')
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
    kintai_shift.schema(c)
    kintai_bihin.schema(c)
    _seed_people(c)
    _ensure_shift_top_people(c)


def _seed_people(c):
    n = c.execute('SELECT COUNT(*) FROM kintai_people').fetchone()[0]
    if n:
        return
    for p in DEFAULT_PEOPLE:
        c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order) VALUES(?,?,?,?,1,?)',
            (p['employee_code'], p['employment'], p['display_name'], '', p['sort_order']),
        )


def _ensure_shift_top_people(c):
    """シフト先頭固定の在籍者を出勤簿マスタへ追加／先頭へ並べる。読取パスは推測しない。"""
    existing = [dict(r) for r in c.execute('SELECT * FROM kintai_people')]
    for want in SHIFT_TOP_PEOPLE:
        want_key = name_match_key(want['display_name'])
        row = next((p for p in existing if name_match_key(p['display_name']) == want_key), None)
        sort_order = int(want.get('sort_order') or 0)
        if row:
            c.execute(
                'UPDATE kintai_people SET active=1, retired=0, sort_order=?, display_name=? WHERE id=?',
                (sort_order, want['display_name'], row['id']),
            )
            continue
        code = str(want.get('employee_code') or 'pending')
        emp = str(want.get('employment') or 'regular')
        clash = c.execute(
            'SELECT id, display_name FROM kintai_people WHERE employee_code=? AND employment=?',
            (code, emp),
        ).fetchone()
        if clash:
            # provisional コードが他者に使われている場合は氏名ベースの仮番号へ退避
            code = f'pending-{want_key}'[:40]
            clash2 = c.execute(
                'SELECT id FROM kintai_people WHERE employee_code=? AND employment=?',
                (code, emp),
            ).fetchone()
            if clash2:
                continue
        c.execute(
            '''INSERT INTO kintai_people(
                employee_code,employment,display_name,source_file,active,sort_order,retired
            ) VALUES(?,?,?,?,1,?,0)''',
            (code, emp, want['display_name'], '', sort_order),
        )


def can_use_kintai(user) -> bool:
    if not user:
        return False
    # 主管理者は運用設定のため常に可。管理者チェック（manage/overview）も可。
    # 一般利用者は mark_kintai（初期OFF・推測ONしない）。
    if user['role'] == 'owner':
        return True
    try:
        if user['manage'] or user['overview']:
            return True
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return bool(user['mark_kintai'])
    except (KeyError, IndexError, TypeError):
        return False


def require_kintai(user):
    if not can_use_kintai(user):
        return 403, {'error': 'マーク管理の権限がありません。'}
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
        # hours_to_minutes と同じ: 微小負数（Excel 誤差）は 0、大きな負のみエラー
        if v < TINY_NEG:
            if v > -1:
                return 0.0
            raise ValueError(f'{label}が負です（{v}）。')
        if v < 0:
            return 0.0
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
    """個別指定 → 雇用区分の既定 → 全体既定の順。契約・パートは monthdot（サンプル確認済み）。"""
    settings = settings if settings is not None else load_local_settings()
    pl = settings.get('peopleLayouts') or {}
    pid = str(person.get('id') or '')
    if pid and pl.get(pid):
        lid = str(pl.get(pid)).strip()
        return lid if lid in LAYOUTS else 'shukkinbo-2026-seishain-v1'
    emp = str(person.get('employment') or '').strip()
    by_emp = DEFAULT_LAYOUT_BY_EMPLOYMENT.get(emp)
    if by_emp and by_emp in LAYOUTS:
        return by_emp
    # パスから雇用区分を推定（peopleFiles のフルUNC）
    src = ''
    try:
        src = person_source_relative(person, settings)
    except Exception:
        src = ''
    inferred = infer_employment_from_path(src) if src else None
    if inferred and DEFAULT_LAYOUT_BY_EMPLOYMENT.get(inferred) in LAYOUTS:
        return DEFAULT_LAYOUT_BY_EMPLOYMENT[inferred]
    lid = (settings.get('layoutId') or 'shukkinbo-2026-seishain-v1').strip()
    return lid if lid in LAYOUTS else 'shukkinbo-2026-seishain-v1'


def detect_sheet_pattern(sheet_names: list[str] | set[str]) -> str:
    """シート名から構造ヒント。セル意味は判定しない。

    戻り値: seishain-2026 | monthdot-yyyy-m | unknown
    """
    names = set(sheet_names or [])
    if (
        '集計' in names
        and ('年間ｶﾚﾝﾀﾞｰ' in names or '年間カレンダー' in names)
        and any(re.fullmatch(r'2[6-7]\.\d{1,2}', n or '') for n in names)
    ):
        return 'seishain-2026'
    monthdot = [n for n in names if re.fullmatch(r'20\d{2}\.\d{1,2}', n or '')]
    if len(monthdot) >= 6 and '集計' not in names:
        return 'monthdot-yyyy-m'
    if monthdot and not any(re.fullmatch(r'2[6-7]\.\d{1,2}', n or '') for n in names):
        return 'monthdot-yyyy-m'
    return 'unknown'


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


def count_holidays_from_month_sheet(
    month_grid: dict,
    year: int,
    month: int,
    layout: dict,
    as_of: date | None = None,
) -> float:
    """月シートの日付列を正とし、休み・始業から休日数を数える（年間カレンダー無し用）。

    契約・パート実サンプルは各月シートに42日分の日付がある。
    ルールは正社員と同じ: as_of 以前・始業なし・有休でない → 休日。
    """
    as_of = as_of or date.today()
    day_first = layout['day_first_row']
    day_last = layout['day_last_row']
    date_col = layout.get('date_col', 1)
    leave_col = layout['leave_col']
    start_col = layout['start_col']
    holidays = 0.0
    found_dates = 0
    for row in range(day_first, day_last + 1):
        raw = _cell(month_grid, row, date_col)
        if isinstance(raw, dict) and raw.get('__missing_cache__'):
            raise ValueError(
                f'{year}-{month:02d} の月シート日付に計算結果がありません（数式の再計算はしません）。'
            )
        day_date = _excel_serial_to_date(raw)
        if day_date is None:
            continue
        found_dates += 1
        if day_date > as_of:
            continue
        leave = _cell(month_grid, row, leave_col)
        start = _cell(month_grid, row, start_col)
        try:
            if _has_start_value(start):
                continue
        except ValueError as e:
            raise ValueError(f'{year}-{month:02d} 日{day_date.isoformat()} の{e}') from e
        if is_paid_leave_cell(leave):
            continue
        holidays += 1.0
    if found_dates == 0:
        raise ValueError(f'{year}-{month:02d} の月シートに日付がありません。')
    return holidays


def aggregate_month_from_summary(
    sheets: dict[str, dict],
    year: int,
    month: int,
    layout: dict,
    as_of: date | None = None,
) -> dict:
    """集計シート（出勤・有給・法定内①・残業内訳）＋休日。

    管理表個人シート: J=行6、K=行13+14+18。行5=0の月は J/K 空白。
    正社員は年間カレンダー休日、契約・パートは月シート日付で休日。
    """
    summary_name = layout['summary_sheet']
    month_sheets = layout['month_sheets']
    summary_cols = layout['summary_cols']
    if (year, month) not in summary_cols or (year, month) not in month_sheets:
        raise ValueError(f'{year}-{month:02d} はこのレイアウトの対象外です。')
    summary = sheets.get(summary_name)
    if summary is None:
        raise ValueError(f'「{summary_name}」シートがありません。')
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
    cal_name = layout.get('calendar_sheet')
    if cal_name:
        cal = sheets.get(cal_name)
        if cal is None:
            raise ValueError(f'「{cal_name}」シートがありません。')
        holiday_days = count_holidays_summary_shukkinbo(
            cal, month_grid, year, month, layout, as_of=as_of
        )
    else:
        holiday_days = count_holidays_from_month_sheet(
            month_grid, year, month, layout, as_of=as_of
        )
    return {
        'work_days': work_days,
        'holiday_days': holiday_days,
        'paid_leave_days': paid_leave_days,
        'ordinary_minutes': ordinary,
        'overtime_minutes': overtime,
        'total_minutes': total,
    }


def _preview_sheet_cells(grid: dict, max_rows: int = 5, max_cols: int = 8) -> list[dict]:
    """先頭付近の生セル（値のみ）。意味付けしない。"""
    if not grid:
        return []
    cells = []
    for (r, c), v in sorted(grid.items()):
        if r > max_rows or c > max_cols:
            continue
        if isinstance(v, dict) and v.get('__missing_cache__'):
            show = f"(数式キャッシュなし: {v.get('formula') or ''})"
        else:
            show = v
        cells.append({'row': r, 'col': c, 'ref': f'{_col_letters(c)}{r}', 'value': show})
    return cells


def parse_person_workbook(blob: bytes, months: list[tuple[int, int]], layout: dict, as_of: date | None = None) -> dict[tuple[int, int], dict]:
    sheets = read_xlsx_grid(blob)
    kind = layout.get('kind') or 'fixture-days'
    pattern = detect_sheet_pattern(list(sheets.keys()))
    found = '、'.join(list(sheets.keys())[:12]) or '（なし）'

    if kind in ('summary-shukkinbo', 'summary-monthdot'):
        if kind == 'summary-shukkinbo' and pattern == 'monthdot-yyyy-m':
            raise ValueError(
                'このファイルは契約・パート形式（月シート 2026.2 …）です。'
                '正社員用レイアウト（集計／年間ｶﾚﾝﾀﾞｰ）では読めません。'
                '雇用区分の既定レイアウト（monthdot-v1／monthdot-part-v1）を使ってください。'
                + f' ファイル内シート: {found}。'
            )
        if kind == 'summary-monthdot' and pattern == 'seishain-2026':
            raise ValueError(
                'このファイルは正社員形式（26.2／集計／年間ｶﾚﾝﾀﾞｰ）です。'
                '契約・パート用レイアウトでは読めません。shukkinbo-2026-seishain-v1 を使ってください。'
                + f' ファイル内シート: {found}。'
            )
        results = {}
        errors = []
        for y, m in months:
            try:
                results[(y, m)] = aggregate_month_from_summary(sheets, y, m, layout, as_of=as_of)
            except ValueError as e:
                errors.append(str(e))
        if errors and not results:
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
        hint = ''
        if layout.get('id') == 'fixture-v1':
            hint = ' ' + LAYOUT_SAMPLE_NEEDED
        elif pattern == 'monthdot-yyyy-m':
            hint = ' ' + LAYOUT_SAMPLE_NEEDED_MONTHDOT
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
    """読取専用の構造確認。シート名・範囲・先頭行の生値。列意味は推測しない。"""
    sheets = read_xlsx_grid(blob)
    out = []
    for name, grid in sheets.items():
        if not grid:
            out.append({
                'name': name, 'cells': 0,
                'rowMin': None, 'rowMax': None, 'colMin': None, 'colMax': None,
                'preview': [],
            })
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
            'preview': _preview_sheet_cells(grid),
        })
    names = [s['name'] for s in out]
    pattern = detect_sheet_pattern(names)
    note = 'シート名とセル範囲・先頭行の生値のみ返します（列の意味は判定しません）。'
    suggested = None
    if pattern == 'seishain-2026':
        note += ' 構造は正社員（営業手当無し）2026出勤簿に近いです。'
        suggested = 'shukkinbo-2026-seishain-v1'
    elif pattern == 'monthdot-yyyy-m':
        note += (
            ' 月シートが 2026.2 形式です（契約・パート系）。'
            '契約は shukkinbo-2026-monthdot-v1、パートは shukkinbo-2026-monthdot-part-v1'
            '（始業列が異なるため雇用区分で自動選択）。'
        )
        suggested = 'shukkinbo-2026-monthdot-v1'
    else:
        note += ' ' + LAYOUT_SAMPLE_NEEDED
    return {
        'sheetCount': len(out),
        'sheets': out,
        'sheetPattern': pattern,
        'suggestedLayoutId': suggested,
        'note': note,
        'sampleNeeded': pattern not in ('seishain-2026', 'monthdot-yyyy-m'),
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


def clean_source_path(value: str) -> str:
    """パス先頭の U+202A など双方向制御文字を除去。"""
    return (value or '').translate(PATH_BIDI_MARKS).strip()


def normalize_person_name(raw: str) -> str:
    """氏名の照合用。空白正規化＋末尾に張り付いたカタカナ読みを除去。"""
    s = unicodedata.normalize('NFKC', raw or '')
    s = s.replace('\u3000', ' ').replace('\xa0', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    # 例: 菊池 敏子キクチトシコ / 退職者タイショクシャ
    m = re.match(r'^(.*[\u4e00-\u9fff々〆ヵヶ])([ァ-ヶー]+)$', s)
    if m:
        s = m.group(1).rstrip()
    return re.sub(r'\s+', ' ', s).strip()


def name_match_key(raw: str) -> str:
    return re.sub(r'\s+', '', normalize_person_name(raw))


def infer_employment_from_path(path: str) -> str | None:
    """パス中の雇用区分フォルダ名から regular / contract / part を推定。"""
    s = clean_source_path(path).replace('/', '\\')
    if 'パート' in s:
        return 'part'
    if '契約' in s:
        return 'contract'
    if '正社員' in s:
        return 'regular'
    return None


def infer_code_from_path(path: str) -> str:
    """ファイル名先頭の社員番号（数字）を取る。"""
    name = clean_source_path(path).replace('/', '\\').rsplit('\\', 1)[-1]
    m = re.match(r'^(\d+)', name.strip())
    return m.group(1) if m else ''


def paths_list_path() -> Path:
    return Path(DATA_DIR) / PATHS_LIST_NAME


def parse_section_heading(name_s: str) -> tuple[str | None, bool | None]:
    """見出し行なら (employment|None, retired|None)。通常の氏名行は (None, None)。

    - 正社員 / 契約社員 / パート → 在職セクション（retired=False）
    - 退職者 → 退職セクション（retired=True、雇用はパスから）
    """
    norm = normalize_person_name(name_s)
    if RETIREE_HEADING_RE.match(norm) or RETIREE_HEADING_RE.match((name_s or '').strip()):
        return None, True
    emp = EMPLOYMENT_SECTION_LABELS.get(norm)
    if emp:
        return emp, False
    return None, None


def parse_paths_workbook(blob: bytes) -> list[dict]:
    """出勤簿パス.xlsx（Sheet1）を読む。

    見出し『正社員』『契約社員』『パート』『退職者』でセクション切替。
    列A=氏名、列B=フルUNC。パス先頭の双方向制御文字は除去する。
    """
    sheets = read_xlsx_grid(blob)
    # シート名は Sheet1 想定。1枚だけならそれ、複数なら Sheet1 優先
    if 'Sheet1' in sheets:
        grid = sheets['Sheet1']
    elif len(sheets) == 1:
        grid = next(iter(sheets.values()))
    else:
        raise ValueError('Sheet1 が見つかりません（出勤簿パス一覧）。')
    max_row = max((r for r, _c in grid.keys()), default=0)
    retired = False
    section_employment: str | None = None
    entries = []
    for rn in range(1, max_row + 1):
        name_raw = grid.get((rn, 1))
        path_raw = grid.get((rn, 2))
        name_s = '' if name_raw is None else str(name_raw).strip()
        if not name_s:
            continue
        heading_emp, heading_retired = parse_section_heading(name_s)
        if heading_retired is not None:
            retired = heading_retired
            section_employment = heading_emp  # 退職者見出しでは None
            continue
        path = clean_source_path('' if path_raw is None else str(path_raw))
        if not path:
            continue
        code = infer_code_from_path(path)
        path_emp = infer_employment_from_path(path)
        # 在職セクションは見出しを優先。退職者はパスの雇用区分フォルダから。
        employment = section_employment or path_emp
        entries.append({
            'displayName': normalize_person_name(name_s),
            'nameKey': name_match_key(name_s),
            'path': path,
            'retired': retired,
            'employeeCode': code,
            'employment': employment,
            'row': rn,
        })
    if not entries:
        raise ValueError('出勤簿パス一覧に氏名とパスの行がありません。')
    return entries


def _match_person_for_path_entry(people: list[dict], entry: dict) -> dict | None:
    """氏名キーで照合。同名が複数なら社員番号＋雇用区分で絞る（037混同防止）。"""
    key = entry['nameKey']
    hits = [p for p in people if name_match_key(p['display_name']) == key]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    code = (entry.get('employeeCode') or '').strip()
    emp = entry.get('employment')
    if code and emp:
        both = [p for p in hits if p['employee_code'] == code and p['employment'] == emp]
        if len(both) == 1:
            return both[0]
    if code:
        by_code = [p for p in hits if p['employee_code'] == code]
        if len(by_code) == 1:
            return by_code[0]
    if emp:
        by_emp = [p for p in hits if p['employment'] == emp]
        if len(by_emp) == 1:
            return by_emp[0]
    return None


def _insert_person_from_path_entry(c, entry: dict, sort_order: int) -> dict:
    code = (entry.get('employeeCode') or '').strip()
    emp = entry.get('employment')
    if not code or not emp:
        raise ValueError('社員番号または雇用区分がパスから取れません。')
    exists = c.execute(
        'SELECT id, display_name FROM kintai_people WHERE employee_code=? AND employment=?',
        (code, emp),
    ).fetchone()
    if exists:
        raise ValueError(
            f'社員番号{code}（{emp}）は「{exists["display_name"]}」として登録済みです。'
            '037混同を避けるため追加しませんでした。'
        )
    retired_flag = 1 if entry['retired'] else 0
    c.execute(
        '''INSERT INTO kintai_people(
            employee_code,employment,display_name,source_file,active,sort_order,retired
        ) VALUES(?,?,?,?,1,?,?)''',
        (code, emp, entry['displayName'], entry['path'], sort_order, retired_flag),
    )
    pid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return {
        'id': pid,
        'employee_code': code,
        'employment': emp,
        'display_name': entry['displayName'],
        'retired': retired_flag,
        'source_file': entry['path'],
    }


def apply_paths_import(c, entries: list[dict]) -> dict:
    """氏名→フルパスを一括反映。一覧に無い既存者は消さない・変更しない。

    雇用区分見出し（正社員／契約／パート）と退職者見出しを反映。
    未登録者は見出し＋パスから追加する。
    """
    people = [dict(r) for r in c.execute('SELECT * FROM kintai_people WHERE active=1')]
    # 旧seedで今城が regular のまま残っている場合、氏名照合で拾えるようにする
    pf = dict(load_local_settings().get('peopleFiles') or {})
    results = []
    updated = 0
    created = 0
    unmatched = 0
    max_sort = max((int(p.get('sort_order') or 0) for p in people), default=0)

    for entry in entries:
        person = _match_person_for_path_entry(people, entry)
        if person is None:
            code = (entry.get('employeeCode') or '').strip()
            emp = entry.get('employment')
            if not (code and emp):
                results.append({
                    'displayName': entry['displayName'],
                    'employeeCode': code,
                    'employment': emp or '',
                    'status': '未照合',
                    'detail': '対象者マスタに氏名がなく、パスから社員番号・雇用区分も取れません。',
                    'path': entry['path'],
                    'retired': entry['retired'],
                })
                unmatched += 1
                continue
            try:
                max_sort += 1
                person = _insert_person_from_path_entry(c, entry, max_sort)
            except ValueError as e:
                results.append({
                    'displayName': entry['displayName'],
                    'employeeCode': code,
                    'employment': emp,
                    'status': 'エラー',
                    'detail': str(e),
                    'path': entry['path'],
                    'retired': entry['retired'],
                })
                unmatched += 1
                continue
            pf[str(person['id'])] = entry['path']
            people.append(person)
            created += 1
            results.append({
                'personId': person['id'],
                'displayName': entry['displayName'],
                'employeeCode': code,
                'employment': emp,
                'status': '追加',
                'detail': '退職者として登録' if entry['retired'] else '在職として登録',
                'path': entry['path'],
                'retired': entry['retired'],
            })
            continue

        pid = person['id']
        retired_flag = 1 if entry['retired'] else 0
        new_emp = entry.get('employment')
        new_code = (entry.get('employeeCode') or '').strip()
        # 見出しの雇用区分が既存と違う場合（例: 今城 regular→contract）は衝突がなければ更新
        emp_note = ''
        if new_emp and new_emp != person['employment']:
            clash = c.execute(
                'SELECT id FROM kintai_people WHERE employee_code=? AND employment=? AND id!=?',
                (person['employee_code'], new_emp, pid),
            ).fetchone()
            if clash:
                emp_note = f'／雇用区分は{person["employment"]}のまま（{new_emp}は別登録あり）'
            else:
                c.execute('UPDATE kintai_people SET employment=? WHERE id=?', (new_emp, pid))
                person['employment'] = new_emp
                emp_note = f'／雇用区分を{new_emp}に更新'
        # 仮社員番号（pending…）をパスから取れた実番号へ更新（衝突時は触らない）
        code_note = ''
        if new_code and new_code != person['employee_code']:
            clash_code = c.execute(
                'SELECT id FROM kintai_people WHERE employee_code=? AND employment=? AND id!=?',
                (new_code, person['employment'], pid),
            ).fetchone()
            if clash_code:
                code_note = f'／社員番号は{person["employee_code"]}のまま（{new_code}は別登録あり）'
            else:
                c.execute('UPDATE kintai_people SET employee_code=? WHERE id=?', (new_code, pid))
                person['employee_code'] = new_code
                code_note = f'／社員番号を{new_code}に更新'
        c.execute(
            'UPDATE kintai_people SET source_file=?, retired=? WHERE id=?',
            (entry['path'], retired_flag, pid),
        )
        pf[str(pid)] = entry['path']
        person['source_file'] = entry['path']
        person['retired'] = retired_flag
        updated += 1
        results.append({
            'personId': pid,
            'displayName': person['display_name'],
            'employeeCode': person['employee_code'],
            'employment': person['employment'],
            'status': '更新',
            'detail': ('退職者' if retired_flag else '在職') + emp_note + code_note,
            'path': entry['path'],
            'retired': bool(retired_flag),
        })

    save_local_settings({'peopleFiles': pf}, True)
    # 加藤など一覧外は触らないことの確認用
    kept = [
        {
            'personId': p['id'],
            'displayName': p['display_name'],
            'employeeCode': p['employee_code'],
            'employment': p['employment'],
            'retired': bool(p.get('retired') or 0),
            'sourceFile': (pf.get(str(p['id'])) or p.get('source_file') or ''),
        }
        for p in people
        if name_match_key(p['display_name']) not in {e['nameKey'] for e in entries}
    ]
    return {
        'counts': {
            'updated': updated,
            'created': created,
            'unmatched': unmatched,
            'rows': len(entries),
            'keptUnlisted': len(kept),
        },
        'people': results,
        'keptUnlisted': kept,
    }


def import_paths_from_upload(c, filename: str, blob: bytes) -> dict:
    if not filename or not str(filename).lower().endswith('.xlsx'):
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    if str(filename).startswith('~$'):
        raise ValueError('Excelの一時ファイル（~$）は読みません。')
    if len(blob) > 2_000_000:
        raise ValueError('ファイルが大きすぎます。')
    entries = parse_paths_workbook(blob)
    # ローカルに保存して再読込できるようにする（Git除外）
    dest = paths_list_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    summary = apply_paths_import(c, entries)
    summary['savedAs'] = str(dest.name)
    summary['filename'] = str(filename)
    return summary


def import_paths_from_saved(c) -> dict:
    path = paths_list_path()
    if not path.is_file():
        raise ValueError(
            '保存済みの出勤簿パス一覧がありません。先に「出勤簿パス.xlsx」を取り込んでください。'
        )
    blob = path.read_bytes()
    entries = parse_paths_workbook(blob)
    summary = apply_paths_import(c, entries)
    summary['savedAs'] = path.name
    summary['filename'] = path.name
    summary['reloaded'] = True
    return summary


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


ERROR_TEXT_MAX = 2000


def _format_import_error(
    reason: str,
    *,
    path: Path | str | None = None,
    layout_id: str = '',
    employment: str = '',
    parse_hint: bool = False,
) -> str:
    """理由を先に書き、長いUNCは後ろへ。画面で切られても原因が残るようにする。"""
    parts = [str(reason or '').strip()]
    if layout_id:
        parts.append(f'レイアウト: {layout_id}')
    if employment:
        emp_ja = {'regular': '正社員', 'contract': '契約', 'part': 'パート'}.get(employment, employment)
        parts.append(f'雇用区分: {emp_ja}')
    if parse_hint and employment and employment != 'regular' and (
        not layout_id or layout_id == 'shukkinbo-2026-seishain-v1'
    ):
        parts.append(
            '※契約は monthdot-v1、パートは monthdot-part-v1（始業列が異なる）を使ってください。'
            'いまは正社員用レイアウトで読んでいます。'
        )
    if path:
        parts.append(f'パス: {path}')
    text = '\n'.join(p for p in parts if p)
    if len(text) > ERROR_TEXT_MAX:
        return text[: ERROR_TEXT_MAX - 1] + '…'
    return text


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
            item['error'] = _format_import_error(
                f'読取権限がありません（{e}）', path=path, layout_id=layout['id'], employment=person['employment']
            )
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        except FileNotFoundError as e:
            item['status'] = 'エラー'
            item['errorKind'] = 'not_found'
            item['error'] = _format_import_error(
                f'ファイルが見つかりません（{e}）', path=path, layout_id=layout['id'], employment=person['employment']
            )
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        except OSError as e:
            item['status'] = 'エラー'
            item['errorKind'] = 'io'
            item['error'] = _format_import_error(
                f'ファイルを開けません（{type(e).__name__}: {e}）',
                path=path, layout_id=layout['id'], employment=person['employment'],
            )
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        except ValueError as e:
            item['status'] = 'エラー'
            item['errorKind'] = 'parse'
            item['error'] = _format_import_error(
                f'パース失敗: {e}',
                path=path, layout_id=layout['id'], employment=person['employment'],
                parse_hint=True,
            )
            _set_person_status(c, person['id'], 'エラー', item['error'], str(path), '')
        except Exception as e:
            item['status'] = 'エラー'
            item['errorKind'] = 'other'
            item['error'] = _format_import_error(
                f'{type(e).__name__}: {e}', path=path, layout_id=layout['id'], employment=person['employment']
            )
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
        'WHERE p.active=1 ORDER BY COALESCE(p.retired,0), p.sort_order, p.id'
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
            'retired': bool(person.get('retired') or 0),
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
                'retired': bool(person.get('retired') or 0),
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
                'retired': bool(p.get('retired') or 0),
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
        'pathsListSaved': paths_list_path().is_file(),
        'opsNote': (
            '正社員・契約・パートとも集計可（契約 monthdot-v1／パート monthdot-part-v1。雇用区分で自動選択）'
            '（サンプル提供後にレイアウト追加）。パス一覧の一括取込・退職者別表示あり。元出勤簿は変更しません。'
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
    if path == '/api/kintai/shift':
        q = _query_map(h)
        return 200, kintai_shift.get_shift_sheet(c, q.get('year'), q.get('month'))
    if path == '/api/kintai/shift/default-order':
        return 200, kintai_shift.get_default_order(c)
    if path == '/api/kintai/shift/work-memos':
        # 勤務メモは月シートと独立。GET はシフト読込の workMemos でも足りるが、明示取得用。
        q = _query_map(h)
        sheet = kintai_shift.get_shift_sheet(c, q.get('year'), q.get('month'))
        return 200, {
            'workMemos': sheet.get('workMemos') or {},
            'people': [
                {'key': p.get('key'), 'personId': p.get('personId'), 'employment': p.get('employment')}
                for p in (sheet.get('people') or [])
            ],
        }
    if path == '/api/kintai/bihin':
        q = _query_map(h)
        return 200, kintai_bihin.get_sheet(c, q.get('year'), q.get('month'))
    if path == '/api/kintai/bihin/items':
        return 200, {'items': kintai_bihin.list_items(c, include_inactive=True)}
    if path == '/api/kintai/bihin/vendors':
        import kintai_bihin_order
        return 200, {'vendors': kintai_bihin_order.list_vendors(c, include_inactive=True)}
    if path == '/api/kintai/bihin/order/settings':
        import kintai_bihin_order
        return 200, kintai_bihin_order.settings_bundle(c)
    if path == '/api/kintai/bihin/orders':
        import kintai_bihin_order
        q = _query_map(h)
        return 200, {
            'orders': kintai_bihin_order.list_order_history(c, limit=q.get('limit') or 100),
            'history': kintai_bihin_order.history_flat(c, limit=q.get('limit') or 200),
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
    if path == '/api/kintai/paths-import':
        if not (h.user['role'] == 'owner' or h.user['manage']):
            return 403, {'error': '出勤簿パス一覧の取込は利用者管理の権限が必要です。'}
        if d.get('reload'):
            return 200, import_paths_from_saved(c)
        filename = d.get('filename') or ''
        raw = d.get('data')
        if not isinstance(raw, str) or not raw:
            raise ValueError('出勤簿パス.xlsx の内容がありません。')
        try:
            blob = base64.b64decode(raw, validate=False)
        except Exception as e:
            raise ValueError('ファイルデータを読めません。') from e
        return 200, import_paths_from_upload(c, str(filename), blob)
    if path == '/api/kintai/shift':
        # シフトは予定のみ。kintai_monthly（出勤簿実績）は変更しない。勤務メモも触らない。
        return kintai_shift.save_shift_sheet(c, h.user, d or {})
    if path == '/api/kintai/shift/default-order':
        # 未作成月の初期並びのみ。月シートの people/cells は触らない。
        return 200, kintai_shift.save_default_order(c, h.user, d or {})
    if path == '/api/kintai/shift/work-memos':
        # 勤務メモのみ。月シート（cells/people/version）は触らない。
        return 200, kintai_shift.save_work_memos(c, h.user, d or {})
    if path == '/api/kintai/bihin':
        # 備品棚卸。出勤簿・シフト・在庫デスク入出庫は変更しない。
        return kintai_bihin.save_sheet(c, h.user, d or {})
    if path == '/api/kintai/bihin/items':
        # マスタのみ。保存済み月のスナップショットは書き換えない。
        return 200, kintai_bihin.save_item(c, h.user, d or {})
    if path == '/api/kintai/bihin/items/delete':
        # 論理削除（active=0）。ID 指定。保存済み月・発注履歴は触らない。
        return 200, {'item': kintai_bihin.soft_delete_item(c, h.user, d or {})}
    if path == '/api/kintai/bihin/items/restore':
        # 復元（active=1）。過去月の書き換え・重複行作成はしない。
        return 200, {'item': kintai_bihin.restore_item(c, h.user, d or {})}
    if path == '/api/kintai/bihin/vendors':
        import kintai_bihin_order
        return 200, {'vendor': kintai_bihin_order.save_vendor(c, h.user, d or {})}
    if path == '/api/kintai/bihin/order/prepare':
        # Thunderbird で作成画面を開くだけ。自動送信しない。成功時のみ当月購入へ加算。
        import kintai_bihin_order
        import sys
        skip = bool((d or {}).get('skipMail')) or sys.platform != 'win32'
        # 本番 Windows では skipMail を無視（テスト用フラグの誤送信防止はローカルのみ）
        if sys.platform == 'win32':
            skip = False
        return 200, kintai_bihin_order.prepare_orders(
            c, h.user, d or {}, open_mail=True, skip_mail=skip,
        )
    if path == '/api/kintai/bihin/order/cancel':
        import kintai_bihin_order
        return 200, kintai_bihin_order.cancel_order(c, h.user, d or {})
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
