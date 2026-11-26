"""石狩倉庫2F 棚番号（配置図＋棚番号一覧）の保存・読込・Excel出力。

配置図と一覧は1つのデータ（JSON）で持つ。初期値は ishikari_2f_seed.json（元の静的ページと同じ内容）。
保存のたびに版を上げ、すべての版を ishikari_shelf_history に残す（前の版へ戻せるように）。
"""
import copy
import io
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED_PATH = ROOT / 'ishikari_2f_seed.json'
TEMPLATE_XLSX = ROOT / 'stock-desk' / 'static' / 'ishikari-2f.xlsx'

MAX_BLOCKS = 3000
MAX_ITEMS = 3000
MAX_TEXT = 200
COLOR = re.compile(r'#[0-9A-Fa-f]{6}')
ITEM_FIELDS = ('name', 'shelf', 'branch', 'all_branch', 'rack', 'note')


def seed():
    return json.loads(SEED_PATH.read_text(encoding='utf-8'))


def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS ishikari_shelf_layout(
        id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, payload TEXT NOT NULL,
        updated_at TEXT NOT NULL, updated_by TEXT NOT NULL DEFAULT '')''')
    c.execute('''CREATE TABLE IF NOT EXISTS ishikari_shelf_history(
        version INTEGER PRIMARY KEY, payload TEXT NOT NULL, saved_at TEXT NOT NULL, saved_by TEXT NOT NULL DEFAULT '')''')


def read(c):
    """保存済みがなければ初期値（version 0）。"""
    schema(c)
    row = c.execute('SELECT version, payload, updated_at, updated_by FROM ishikari_shelf_layout WHERE id=1').fetchone()
    if not row:
        return dict(version=0, data=seed(), updated_at='', updated_by='')
    return dict(version=row[0], data=json.loads(row[1]), updated_at=row[2], updated_by=row[3])


def _text(value, label, limit=MAX_TEXT):
    if value is None:
        value = ''
    if not isinstance(value, str):
        value = str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if value is None or len(value) > limit:
        raise ValueError(f'{label}は{limit}文字以内で入力してください。')
    return value


def _int(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{label}を確認してください。')
    return value


def validate(data):
    """画面から届いた内容を確認し、決まった形に整える。おかしな値は保存しない。"""
    if not isinstance(data, dict):
        raise ValueError('保存する内容を読めませんでした。')
    base = seed()
    columns, rows = base['columns'], base['rows']  # 方眼（列幅・行高）は固定
    blocks, items = data.get('blocks'), data.get('items')
    if not isinstance(blocks, list) or not isinstance(items, list):
        raise ValueError('保存する内容を読めませんでした。')
    if len(blocks) > MAX_BLOCKS or len(items) > MAX_ITEMS:
        raise ValueError('件数が多すぎます。')
    ncol, nrow = len(columns), len(rows)
    out_blocks, ids = [], set()
    for i, b in enumerate(blocks, 1):
        if not isinstance(b, dict):
            raise ValueError(f'配置図の{i}件目を読めませんでした。')
        bid = _text(b.get('id'), f'配置図の{i}件目のID', 40)
        if not bid or bid in ids:
            raise ValueError(f'配置図の{i}件目のIDを確認してください。')
        ids.add(bid)
        r = _int(b.get('r'), f'配置図「{b.get("text", "")}」の行', 1, nrow)
        col = _int(b.get('c'), f'配置図「{b.get("text", "")}」の列', 1, ncol)
        rs = _int(b.get('rs'), f'配置図「{b.get("text", "")}」の高さ', 1, nrow - r + 1)
        cs = _int(b.get('cs'), f'配置図「{b.get("text", "")}」の幅', 1, ncol - col + 1)
        size = b.get('size')
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not 6 <= size <= 40:
            raise ValueError(f'配置図「{b.get("text", "")}」の文字の大きさを確認してください。')
        bg, fg = b.get('bg'), b.get('fg')
        if not isinstance(bg, str) or not COLOR.fullmatch(bg) or not isinstance(fg, str) or not COLOR.fullmatch(fg):
            raise ValueError(f'配置図「{b.get("text", "")}」の色を確認してください。')
        border = b.get('border', '')
        if not isinstance(border, str) or not re.fullmatch(r't?r?b?l?', border):
            raise ValueError(f'配置図「{b.get("text", "")}」の枠線を確認してください。')
        out_blocks.append(dict(id=bid, r=r, c=col, rs=rs, cs=cs, text=_text(b.get('text'), '配置図の文字'),
                               size=round(float(size), 1), bg=bg, fg=fg, border=border, vertical=bool(b.get('vertical'))))
    taken = {}
    for blk in out_blocks:
        for r in range(blk['r'], blk['r'] + blk['rs']):
            for col in range(blk['c'], blk['c'] + blk['cs']):
                other = taken.setdefault((r, col), blk)
                if other is not blk:
                    raise ValueError(f'配置図の「{other["text"]}」と「{blk["text"]}」が重なっています。位置か大きさを直してから保存してください。')
    route = data.get('route') or []
    if not isinstance(route, list) or len(route) > MAX_BLOCKS:
        raise ValueError('ルートを読めませんでした。')
    block_ids = ids
    if any(not isinstance(x, str) or x not in block_ids for x in route) or len(set(route)) != len(route):
        raise ValueError('ルートに配置図にない棚か、同じ棚が2回入っています。ルートを確認してください。')
    out_items, ids = [], set()
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise ValueError(f'棚番号一覧の{i}行目を読めませんでした。')
        iid = _text(it.get('id'), f'棚番号一覧の{i}行目のID', 40)
        if not iid or iid in ids:
            raise ValueError(f'棚番号一覧の{i}行目のIDを確認してください。')
        ids.add(iid)
        row = dict(id=iid)
        for f in ITEM_FIELDS:
            row[f] = _text(it.get(f), f'棚番号一覧の{i}行目').strip()
        if not row['name']:
            raise ValueError(f'棚番号一覧の{i}行目の名称を入力してください。')
        out_items.append(row)
    return dict(columns=columns, rows=rows, blocks=out_blocks, items=out_items, route=list(route))


def save(c, data, base_version, user_name=''):
    """base_version が今の版と違えば保存しない（他の人の保存を上書きしない）。"""
    clean = validate(data)
    schema(c)
    c.execute('BEGIN IMMEDIATE')
    try:
        current = read(c)
        if base_version != current['version']:
            raise ConflictError('ほかの画面で先に保存されています。再読み込みしてから編集し直してください。')
        now = datetime.now().isoformat(timespec='seconds')
        if current['version'] == 0:
            # 初期値も版0として履歴に残す
            c.execute('INSERT OR IGNORE INTO ishikari_shelf_history VALUES(0,?,?,?)',
                      (json.dumps(current['data'], ensure_ascii=False), now, '初期値'))
        version = current['version'] + 1
        payload = json.dumps(clean, ensure_ascii=False)
        c.execute('INSERT INTO ishikari_shelf_history VALUES(?,?,?,?)', (version, payload, now, user_name or ''))
        c.execute('INSERT OR REPLACE INTO ishikari_shelf_layout VALUES(1,?,?,?,?)', (version, payload, now, user_name or ''))
        c.execute('COMMIT')
    except BaseException:
        c.execute('ROLLBACK')
        raise
    return dict(version=version, data=clean, updated_at=now, updated_by=user_name or '')


class ConflictError(ValueError):
    pass


def _col(n):
    s = ''
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def build_xlsx(data):
    """元のExcelを型にして、配置図と一覧を保存済みの内容で書き直す。"""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = openpyxl.load_workbook(str(TEMPLATE_XLSX))
    ws, lst = wb.worksheets[0], wb.worksheets[1]
    for rng in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(rng))
    for row in ws.iter_rows(min_row=1, max_row=max(ws.max_row, len(data['rows'])), max_col=max(ws.max_column, len(data['columns']))):
        for cell in row:
            cell.value = None
            cell.fill = PatternFill()
            cell.border = Border()
            cell.font = Font()
    side = Side(style='thin', color='FF6D8479')
    for b in data['blocks']:
        top, left = b['r'], b['c']
        bottom, right = top + b['rs'] - 1, left + b['cs'] - 1
        fill = PatternFill('solid', fgColor='FF' + b['bg'][1:].upper())
        for r in range(top, bottom + 1):
            for col in range(left, right + 1):
                cell = ws.cell(r, col)
                cell.fill = fill
                if b['border']:
                    cell.border = Border(
                        top=side if 't' in b['border'] and r == top else Side(),
                        bottom=side if 'b' in b['border'] and r == bottom else Side(),
                        left=side if 'l' in b['border'] and col == left else Side(),
                        right=side if 'r' in b['border'] and col == right else Side())
        head = ws.cell(top, left)
        head.value = b['text']
        head.font = Font(name='Meiryo', bold=True, size=round(b['size'] * 0.75 * 2) / 2, color='FF' + b['fg'][1:].upper())
        head.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True,
                                   text_rotation=90 if b['vertical'] else 0)
        if bottom > top or right > left:
            ws.merge_cells(f'{_col(left)}{top}:{_col(right)}{bottom}')
    # 一覧：5行目が見出し、6行目から
    style_row = [copy.copy(lst.cell(6, col)._style) for col in range(1, 7)]
    if lst.max_row >= 6:
        lst.delete_rows(6, lst.max_row - 5)
    for i, it in enumerate(data['items']):
        r = 6 + i
        for col, f in enumerate(ITEM_FIELDS, 1):
            value = it[f]
            if f == 'shelf' and re.fullmatch(r'\d{1,6}', value or ''):
                value = int(value)
            cell = lst.cell(r, col, value if value != '' else None)
            cell._style = copy.copy(style_row[col - 1])
        lst.row_dimensions[r].height = 18.75
    if lst.auto_filter and lst.auto_filter.ref:
        lst.auto_filter.ref = f'A5:F{max(6, 5 + len(data["items"]))}'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
