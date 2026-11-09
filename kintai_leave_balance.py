"""有給管理簿の集計セルを読み取り、対応と最新値だけを保存する。"""
import base64
import binascii
import math
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO

MAX_BYTES = 10 * 1024 * 1024


def ensure_schema(c):
    people_cols = {r['name'] for r in c.execute('PRAGMA table_info(kintai_people)')}
    if people_cols and 'paid_leave_balance' not in people_cols:
        c.execute('ALTER TABLE kintai_people ADD COLUMN paid_leave_balance REAL')
    c.execute('''CREATE TABLE IF NOT EXISTS kintai_leave_cells (
        person_id INTEGER PRIMARY KEY, cell TEXT NOT NULL,
        balance REAL, note TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
    )''')


def bundle(c):
    ensure_schema(c)
    rows = c.execute('''SELECT p.id, p.employee_code, p.display_name, p.retired,
        l.cell, l.balance, l.note, l.filename, l.updated_at
        FROM kintai_people p LEFT JOIN kintai_leave_cells l ON l.person_id=p.id
        WHERE p.active=1 ORDER BY COALESCE(p.retired,0), p.sort_order, p.id''')
    return {'people': [
        {'personId': r['id'], 'name': r['display_name'], 'employeeCode': r['employee_code'],
         'retired': bool(r['retired']), 'cell': r['cell'] or '', 'balance': r['balance'],
         'note': r['note'] or '', 'filename': r['filename'] or '', 'updatedAt': r['updated_at'] or ''}
        for r in rows
    ]}


def normalize_cell(raw):
    if not isinstance(raw, str):
        raise ValueError('セル位置はK3の形式で入力してください。')
    cell = raw.strip().upper().replace('$', '')
    if not cell:
        return ''
    m = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', cell)
    if not m:
        raise ValueError('セル位置はK3の形式で入力してください（範囲や数式は使えません）。')
    col = 0
    for letter in m[1]:
        col = col * 26 + ord(letter) - 64
    if col > 16384 or int(m[2]) > 1048576:
        raise ValueError('Excelのセル範囲を超えています。')
    return cell


def mappings(c, payload):
    entries = payload.get('people')
    if not isinstance(entries, list) or len(entries) > 2000:
        raise ValueError('従業員のセル位置を確認してください。')
    ids = {r['id'] for r in c.execute('SELECT id FROM kintai_people WHERE active=1')}
    result, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('セル位置を確認してください。')
        pid = entry.get('personId')
        if type(pid) is not int or pid not in ids or pid in seen:
            raise ValueError('対象従業員が不正または重複しています。')
        seen.add(pid)
        result.append((pid, normalize_cell(entry.get('cell', ''))))
    return result


def save_mappings(c, entries):
    ensure_schema(c)
    for pid, cell in entries:
        old = c.execute('SELECT cell FROM kintai_leave_cells WHERE person_id=?', (pid,)).fetchone()
        if old and old['cell'] == cell:
            continue
        c.execute('''INSERT INTO kintai_leave_cells(person_id,cell) VALUES(?,?)
            ON CONFLICT(person_id) DO UPDATE SET cell=excluded.cell,
            balance=NULL,note='',filename='',updated_at='' ''', (pid, cell))


def workbook_bytes(payload):
    raw = payload.get('data')
    if not isinstance(raw, str) or len(raw) > (MAX_BYTES * 4 // 3 + 8):
        raise ValueError('10MB以下のExcelを選択してください。')
    try:
        blob = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Excelファイルを読み込めません。') from None
    if not blob or len(blob) > MAX_BYTES:
        raise ValueError('10MB以下のExcelを選択してください。')
    if not zipfile.is_zipfile(BytesIO(blob)):
        if not blob.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
            raise ValueError('Excel（.xlsx）を選択してください。')
        password = payload.get('password', '')
        if not isinstance(password, str) or not password:
            raise ValueError('Excelを開くためのパスワードを入力してください。')
        if len(password) > 1024:
            raise ValueError('パスワードが長すぎます。')
        try:
            import msoffcrypto
        except ImportError:
            raise ValueError('暗号化Excelの読取準備が必要です。アプリの「有給Excel読取準備.cmd」を実行してください。') from None
        try:
            office = msoffcrypto.OfficeFile(BytesIO(blob))
            office.load_key(password=password, verify_password=True)
            output = BytesIO()
            office.decrypt(output)
            blob = output.getvalue()
        except Exception:
            raise ValueError('パスワードが違うか、このExcelの暗号化形式に対応していません。') from None
    if len(blob) > MAX_BYTES:
        raise ValueError('復号後のExcelが10MBを超えています。')
    try:
        with zipfile.ZipFile(BytesIO(blob)) as book:
            infos = book.infolist()
            if len(infos) > 2000 or sum(i.file_size for i in infos) > 100 * MAX_BYTES:
                raise ValueError('Excelの展開サイズが大きすぎます。')
            if 'xl/workbook.xml' not in book.namelist():
                raise ValueError('Excel（.xlsx）を選択してください。')
    except zipfile.BadZipFile:
        raise ValueError('Excelファイルが破損しています。') from None
    return blob


def cell_value(grid, cell):
    match = re.fullmatch(r'([A-Z]+)([0-9]+)', cell)
    col = 0
    for letter in match[1]:
        col = col * 26 + ord(letter) - 64
    raw = grid.get((int(match[2]), col))
    if raw is None or raw == '':
        return None, '空欄'
    if isinstance(raw, dict) and raw.get('__missing_cache__'):
        return None, '計算結果なし（Excelで再計算・保存してください）'
    if type(raw) not in (int, float) or not math.isfinite(raw) or not 0 <= raw <= 10000:
        return None, '現保有数が数値ではないか、範囲外です'
    return float(raw), ''


def handle(c, payload, read_grid):
    action = payload.get('action')
    if action == 'apply':
        ensure_schema(c)
        rows = c.execute(
            '''SELECT p.id, l.balance
               FROM kintai_people p
               LEFT JOIN kintai_leave_cells l ON l.person_id=p.id
               WHERE p.active=1'''
        ).fetchall()
        applied = 0
        for r in rows:
            bal = r['balance']
            c.execute(
                'UPDATE kintai_people SET paid_leave_balance=? WHERE id=?',
                (bal, r['id']),
            )
            if bal is not None:
                applied += 1
        out = bundle(c)
        out['appliedCount'] = applied
        return out
    entries = mappings(c, payload)
    action = payload.get('action')
    if action == 'save':
        save_mappings(c, entries)
        return bundle(c)
    if action != 'update':
        raise ValueError('操作を確認してください。')
    if not any(cell for _, cell in entries):
        raise ValueError('読み取るセル位置を入力してください。')
    filename = payload.get('filename')
    if not isinstance(filename, str) or not filename.lower().endswith('.xlsx'):
        raise ValueError('有給休暇管理簿の.xlsxを選択してください。')
    blob = workbook_bytes(payload)
    try:
        sheets = read_grid(blob)
    except Exception:
        raise ValueError('Excelの内容を読み込めません。') from None
    if '集計' not in sheets:
        raise ValueError('「集計」シートがありません。')
    # ファイルと設定を検証してから更新。空欄・エラーは古い値を残さない。
    values = [(pid, cell, *cell_value(sheets['集計'], cell)) for pid, cell in entries if cell]
    save_mappings(c, entries)
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    filename = re.split(r'[/\\]', filename)[-1][:255]
    for pid, cell, value, note in values:
        c.execute('''UPDATE kintai_leave_cells SET balance=?,note=?,filename=?,updated_at=?
            WHERE person_id=?''', (value, note, filename, stamp, pid))
    return bundle(c)
