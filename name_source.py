"""ネーム情報（ソース）。

統合ネームラベル用Excelの表をそのまま別テーブルに持ち、閲覧・編集・Excel入出力する。
WEBのネーム情報（name_formats）へは「差分を確認」→行ごとに選んだものだけ反映する（自動同期しない）。
name_formats の列は増やさない（了承の照合値が変わるため）。紐づけはこの表の format_id で持つ。
"""
import base64, io, json, re, sqlite3, unicodedata
from datetime import datetime
from pathlib import Path

import names

DATA_DIR = None

# (キー, Excelの見出し)。先頭14列は元のExcelと同じ並び（C～P）。後ろ3列は追加（Q～S）。
COLUMNS = (
    ('customer', '顧客名'), ('placement', '貼付け部位'), ('size', '大きさ'), ('color', '色'),
    ('font', 'フォント'), ('font_size', 'フォントサイズ'), ('bold', '太字'), ('lines', '列'),
    ('name_rule', '名前'), ('fixed_text', '定型'), ('band', '段サイズ（上下幅/左右/上下）'),
    ('note', '備考'), ('memo', '個人的メモ'), ('assignee', '担当'),
    ('name_id', 'ネームID'), ('form_name', '形式名'), ('dims', '寸法(mm)'),
)
KEYS = tuple(k for k, _ in COLUMNS)
LABELS = dict(COLUMNS)
TEXT_KEYS = tuple(k for k in KEYS if k != 'name_id')
WIDTHS = (53.875, 41.0, 25.0, 55.125, 16.125, 19.5, 11.75, 5.5, 15.5, 55.0, 29.75, 54.625, 19.5, 19.625, 12.0, 30.0, 11.0)
FIRST_COL = 3  # C
DEFAULT_TITLES = ['小汎用（4文字26\u30005文字24\u30006文字20)\u3000大汎用 (4文字48\u30005文字38\u30006文字30)', 'ネームラベル']
# WEBと比べる列（WEBに同じ意味の項目がある列）
SYNC_FIELDS = ('customer', 'placement', 'size', 'color', 'font', 'font_size', 'fixed_text', 'form_name', 'dims')
LIMIT = 2000


def bind(data_dir):
    global DATA_DIR
    DATA_DIR = Path(data_dir)


def schema(c):
    cols = ', '.join(f"{k} TEXT NOT NULL DEFAULT ''" for k in TEXT_KEYS)
    c.execute(f'''CREATE TABLE IF NOT EXISTS name_source_rows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        position REAL NOT NULL,
        kind TEXT NOT NULL DEFAULT 'row',
        format_id INTEGER,
        {cols},
        updated_at TEXT NOT NULL DEFAULT '',
        updated_by TEXT NOT NULL DEFAULT ''
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS name_source_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', action TEXT NOT NULL,
        row_id INTEGER, format_id INTEGER, detail TEXT NOT NULL DEFAULT '{}'
    )''')


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _str(value):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _norm(value):
    text = unicodedata.normalize('NFKC', _str(value))
    text = text.replace('?', '？').replace(':', '：').replace('=', '：').replace('詳細未指定', '')
    return re.sub(r'[\s/／・、,]+', '', text).lower()


def _id_text(format_id):
    return f'NM-{int(format_id):06}' if format_id else ''


def _parse_id(value):
    text = unicodedata.normalize('NFKC', _str(value)).upper()
    if not text:
        return None
    m = re.fullmatch(r'(?:NM-?)?0*(\d+)', text)
    if not m:
        raise ValueError(f'ネームIDは NM-000123 の形で入力してください：{text}')
    return int(m.group(1))


def _require(user):
    blocked = names._require(user)
    if blocked:
        return blocked
    if user['role'] != 'owner':
        return 403, {'error': 'ネーム情報（ソース）は主管理者だけが閲覧できます。'}
    return None


def _owner(user):
    return _require(user)


def _history(c, user, action, row_id=None, format_id=None, detail=None):
    c.execute('INSERT INTO name_source_history(at, actor, action, row_id, format_id, detail) VALUES(?,?,?,?,?,?)',
              (_now(), (user['name'] if user else '') or '', action, row_id, format_id, json.dumps(detail or {}, ensure_ascii=False)))


def backup_db(c, label):
    """反映・取り込み前に DB を data/backups/<label>-<stamp>/ へ退避。退避できなければ中止。
    呼び出し側の接続は書き込みトランザクション中なので、別の接続で確定済みの内容を写す。"""
    if DATA_DIR is None:
        return ''
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    folder = Path(DATA_DIR) / 'backups' / f'{label}-{stamp}'
    try:
        folder.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(str(Path(DATA_DIR) / 'instructions.sqlite3'), timeout=15)
        try:
            with sqlite3.connect(str(folder / 'instructions.sqlite3')) as dest:
                src.backup(dest)
        finally:
            src.close()
    except (OSError, sqlite3.Error) as e:
        raise ValueError(f'バックアップできなかったため中止しました：{e}')
    return str(folder.relative_to(Path(DATA_DIR))).replace('\\', '/')


# ---------- 行の読み書き ----------

def _row_dict(row):
    item = {'id': row['id'], 'kind': row['kind'], 'formatId': row['format_id']}
    for key in TEXT_KEYS:
        item[key] = row[key]
    item['name_id'] = _id_text(row['format_id'])
    return item


def _rows(c):
    return c.execute('SELECT * FROM name_source_rows ORDER BY position, id').fetchall()


def list_rows(handler, c):
    blocked = _require(handler.user)
    if blocked:
        return blocked
    meta = c.execute("SELECT value FROM settings WHERE key='name_source_meta'").fetchone()
    meta = json.loads(meta['value']) if meta else {}
    return 200, {
        'columns': [{'key': k, 'label': v} for k, v in COLUMNS],
        'syncFields': list(SYNC_FIELDS),
        'rows': [_row_dict(r) for r in _rows(c)],
        'meta': {'importedAt': meta.get('importedAt') or '', 'fileName': meta.get('fileName') or ''},
        'canEdit': handler.user['role'] == 'owner',
    }


def _clean(key, value):
    text = _str(value)
    if len(text) > LIMIT:
        raise ValueError(f'{LABELS[key]}は{LIMIT}文字以内にしてください。')
    return text


def save_cell(handler, c, d):
    blocked = _owner(handler.user)
    if blocked:
        return blocked
    try:
        row_id = int(d.get('id'))
    except (TypeError, ValueError):
        return 400, {'error': '行が見つかりません。'}
    key = d.get('field')
    row = c.execute('SELECT * FROM name_source_rows WHERE id=?', (row_id,)).fetchone()
    if not row:
        return 404, {'error': '行が見つかりません。'}
    if key == 'name_id':
        format_id = _parse_id(d.get('value'))
        if format_id:
            exists = c.execute(f'SELECT id FROM name_formats WHERE id=? AND retired=0 AND {names.NOT_SOFT_DELETED}', (format_id,)).fetchone()
            if not exists:
                return 400, {'error': f'{_id_text(format_id)} はWEBのネーム情報にありません。'}
            other = c.execute('SELECT id FROM name_source_rows WHERE format_id=? AND id<>?', (format_id, row_id)).fetchone()
            if other:
                return 400, {'error': f'{_id_text(format_id)} は別の行に紐づいています。'}
        c.execute('UPDATE name_source_rows SET format_id=?, updated_at=?, updated_by=? WHERE id=?',
                  (format_id, _now(), handler.user['name'], row_id))
        before, after = _id_text(row['format_id']), _id_text(format_id)
    elif key in TEXT_KEYS:
        after = _clean(key, d.get('value'))
        before = row[key]
        c.execute(f'UPDATE name_source_rows SET {key}=?, updated_at=?, updated_by=? WHERE id=?',
                  (after, _now(), handler.user['name'], row_id))
    else:
        return 400, {'error': '列が見つかりません。'}
    if before != after:
        _history(c, handler.user, '編集', row_id, None, {'field': key, 'before': before, 'after': after})
    return 200, _row_dict(c.execute('SELECT * FROM name_source_rows WHERE id=?', (row_id,)).fetchone())


def add_row(handler, c, d):
    blocked = _owner(handler.user)
    if blocked:
        return blocked
    after_id = d.get('afterId')
    kind = 'note' if d.get('kind') == 'note' else 'row'
    customer = ''
    if after_id:
        base = c.execute('SELECT * FROM name_source_rows WHERE id=?', (int(after_id),)).fetchone()
        if not base:
            return 404, {'error': '行が見つかりません。'}
        nxt = c.execute('SELECT position FROM name_source_rows WHERE position>? ORDER BY position LIMIT 1', (base['position'],)).fetchone()
        position = (base['position'] + nxt['position']) / 2 if nxt else base['position'] + 1
        customer = base['customer']
    else:
        last = c.execute('SELECT MAX(position) p FROM name_source_rows').fetchone()
        position = (last['p'] or 0) + 1
    c.execute('INSERT INTO name_source_rows(position, kind, customer, updated_at, updated_by) VALUES(?,?,?,?,?)',
              (position, kind, customer, _now(), handler.user['name']))
    row_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    _history(c, handler.user, '行追加', row_id)
    return 200, _row_dict(c.execute('SELECT * FROM name_source_rows WHERE id=?', (row_id,)).fetchone())


def delete_row(handler, c, d):
    blocked = _owner(handler.user)
    if blocked:
        return blocked
    try:
        row_id = int(d.get('id'))
    except (TypeError, ValueError):
        return 400, {'error': '行が見つかりません。'}
    row = c.execute('SELECT * FROM name_source_rows WHERE id=?', (row_id,)).fetchone()
    if not row:
        return 404, {'error': '行が見つかりません。'}
    c.execute('DELETE FROM name_source_rows WHERE id=?', (row_id,))
    _history(c, handler.user, '行削除', row_id, row['format_id'], {'row': dict(row)})
    return 200, {'ok': True}


# ---------- Excel ----------

def _find_header(ws):
    for r in range(1, min(ws.max_row, 20) + 1):
        for col in range(1, min(ws.max_column, 30) + 1):
            if _str(ws.cell(r, col).value) == '顧客名':
                return r
    raise ValueError('見出し「顧客名」の行が見つかりません。')


def parse_workbook(blob):
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob))
    except Exception:
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    ws = wb.active
    header = _find_header(ws)
    colmap = {}
    for col in range(1, ws.max_column + 1):
        label = _str(ws.cell(header, col).value)
        for key, name in COLUMNS:
            if label == name or (key == 'band' and label.startswith('段サイズ')):
                colmap.setdefault(key, col)
    if 'customer' not in colmap or 'placement' not in colmap:
        raise ValueError('「顧客名」「貼付け部位」の列が見つかりません。')
    merged = {}
    for rng in ws.merged_cells.ranges:
        for r in range(rng.min_row, rng.max_row + 1):
            for col in range(rng.min_col, rng.max_col + 1):
                merged[(r, col)] = rng
    titles = []
    for r in range(1, header):
        for col in range(1, ws.max_column + 1):
            v = _str(ws.cell(r, col).value)
            if v:
                titles.append(v)
                break
    pcol, last_data_col = colmap['placement'], max(colmap.get(k, 0) for k in ('memo', 'note', 'band'))
    rows = []
    for r in range(header + 1, ws.max_row + 1):
        item = {}
        for key, col in colmap.items():
            rng = merged.get((r, col))
            value = ws.cell(rng.min_row, rng.min_col).value if rng and key in ('customer', 'assignee') else ws.cell(r, col).value
            if rng and key not in ('customer', 'assignee') and (rng.min_row, rng.min_col) != (r, col):
                value = None
            item[key] = _str(value)
        rng = merged.get((r, pcol))
        kind = 'note' if rng and rng.min_col == pcol and rng.max_col >= pcol + 3 and rng.min_row == rng.max_row else 'row'
        if not any(item.get(k) for k in KEYS if k not in ('customer', 'assignee')) and not (
                item.get('customer') and not merged.get((r, colmap['customer']))):
            continue
        item['kind'] = kind
        rows.append(item)
    if not rows:
        raise ValueError('取り込める行がありません。')
    return {'titles': titles or DEFAULT_TITLES, 'rows': rows, 'hasIdColumn': 'name_id' in colmap}


def import_excel(handler, c, d):
    blocked = _owner(handler.user)
    if blocked:
        return blocked
    raw = d.get('data')
    if not isinstance(raw, str) or not raw:
        raise ValueError('Excelファイルを選んでください。')
    try:
        blob = base64.b64decode(raw, validate=True)
    except ValueError:
        raise ValueError('Excelファイルを読めませんでした。')
    parsed = parse_workbook(blob)
    current = c.execute('SELECT COUNT(*) n FROM name_source_rows').fetchone()['n']
    preview = {'rows': sum(1 for r in parsed['rows'] if r['kind'] == 'row'),
               'notes': sum(1 for r in parsed['rows'] if r['kind'] == 'note'), 'current': current}
    if not d.get('confirm'):
        return 200, {'preview': preview}
    backup = backup_db(c, 'pre-name-source-import')
    c.execute('DELETE FROM name_source_rows')
    used = set()
    for index, item in enumerate(parsed['rows'], start=1):
        format_id = None
        try:
            format_id = _parse_id(item.get('name_id'))
        except ValueError:
            format_id = None
        if format_id and (format_id in used or not c.execute('SELECT id FROM name_formats WHERE id=?', (format_id,)).fetchone()):
            format_id = None
        if format_id:
            used.add(format_id)
        values = [_clean(k, item.get(k, '')) for k in TEXT_KEYS]
        c.execute(f'INSERT INTO name_source_rows(position, kind, format_id, {", ".join(TEXT_KEYS)}, updated_at, updated_by) '
                  f'VALUES(?,?,?,{",".join("?" * len(TEXT_KEYS))},?,?)',
                  [index, item['kind'], format_id] + values + [_now(), handler.user['name']])
    linked = auto_link(c)
    meta = {'importedAt': _now(), 'fileName': _str(d.get('filename'))[:200], 'titles': parsed['titles'], 'backup': backup}
    c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('name_source_meta', ?)", (json.dumps(meta, ensure_ascii=False),))
    _history(c, handler.user, '取り込み', detail={'rows': preview['rows'], 'notes': preview['notes'], 'replaced': current,
                                                 'autoLinked': linked, 'backup': backup, 'file': meta['fileName']})
    return 200, {'imported': preview, 'autoLinked': linked, 'backup': backup}


def build_workbook(c):
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    meta = c.execute("SELECT value FROM settings WHERE key='name_source_meta'").fetchone()
    titles = (json.loads(meta['value']).get('titles') if meta else None) or DEFAULT_TITLES
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(vertical='center', wrap_text=True)
    last_orig = FIRST_COL + 13  # P
    last = FIRST_COL + len(COLUMNS) - 1
    ws.column_dimensions['A'].width = 1.375
    for i, width in enumerate(WIDTHS):
        ws.column_dimensions[get_column_letter(FIRST_COL + i)].width = width
    for i, title in enumerate(titles[:2]):
        cell = ws.cell(2 + i, 2, title)
        cell.font = Font(bold=True, size=14 if i == 1 else 11)
        ws.merge_cells(start_row=2 + i, start_column=2, end_row=2 + i, end_column=last_orig - 1)
    header = 4
    fill = PatternFill('solid', fgColor='D9D9D9')
    for i, (key, label) in enumerate(COLUMNS):
        cell = ws.cell(header, FIRST_COL + i, label)
        cell.font = Font(bold=True)
        cell.fill = fill
        cell.border = border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    rows = _rows(c)
    r = header
    for row in rows:
        r += 1
        for i, key in enumerate(KEYS):
            value = _id_text(row['format_id']) if key == 'name_id' else row[key]
            if key in ('lines', 'font_size') and re.fullmatch(r'\d+', value or ''):
                value = int(value)
            cell = ws.cell(r, FIRST_COL + i, value if value != '' else None)
            cell.border = border
            cell.alignment = wrap
        if row['kind'] == 'note':
            ws.merge_cells(start_row=r, start_column=FIRST_COL + 1, end_row=r, end_column=last_orig - 1)
    # 同じ顧客が続く行は顧客名を結合、担当は同じ値が続くときだけ結合（元のExcelと同じ見た目）
    i = 0
    while i < len(rows):
        j = i
        key = _norm(rows[i]['customer'])
        while key and j + 1 < len(rows) and _norm(rows[j + 1]['customer']) == key:
            j += 1
        if j > i:
            top, bottom = header + 1 + i, header + 1 + j
            for rr in range(top + 1, bottom + 1):
                ws.cell(rr, FIRST_COL).value = None
            ws.merge_cells(start_row=top, start_column=FIRST_COL, end_row=bottom, end_column=FIRST_COL)
            block = rows[i:j + 1]
            if block[0]['assignee'] and all(x['assignee'] == block[0]['assignee'] for x in block):
                for rr in range(top + 1, bottom + 1):
                    ws.cell(rr, last_orig).value = None
                ws.merge_cells(start_row=top, start_column=last_orig, end_row=bottom, end_column=last_orig)
        i = j + 1
    ws.freeze_panes = 'D5'
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def export_excel(handler, c):
    blocked = _require(handler.user)
    if blocked:
        return blocked
    from urllib.parse import quote
    name = f'ネーム情報ソース_{datetime.now():%Y%m%d}.xlsx'
    return (200, build_workbook(c), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            {'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name)})


# ---------- WEB との対応 ----------

FONT_TO_WEB = {'HG丸ゴシック': 'HG丸ｺﾞｼｯｸM-PRO', 'MSPゴシック': 'MS PGothic', 'MSP明朝': 'MS PMincho'}


def _font_key(value):
    text = unicodedata.normalize('NFKC', _str(value)).replace(' ', '').replace('\u3000', '')
    text = text.replace('M-PRO', '').replace('Gothic', 'ゴシック').replace('Mincho', '明朝')
    return text.upper()


def _font_excel(value):
    key = _font_key(value)
    for excel in FONT_TO_WEB:
        if _font_key(excel) == key:
            return excel
    return _str(value)


def _font_web(value):
    key = _font_key(value)
    for excel, web in FONT_TO_WEB.items():
        if _font_key(excel) == key:
            return web
    return _str(value)


def _placement_web(value):
    return '／'.join(p for p in re.split(r'[\u3000\s]+|／', _str(value)) if p)


def _placement_excel(value):
    return '\u3000'.join(p for p in re.split(r'／|\n', _str(value)) if p.strip())


def _size_kind(value):
    text = unicodedata.normalize('NFKC', _str(value))
    if text.startswith('小'):
        return 'small'
    if text.startswith('大'):
        return 'large'
    return ''


def _web_size_kind(f):
    if f['blank']:
        return 'blank'
    if f['unknown_size']:
        return 'unknown'
    return 'small' if f['small'] else 'large'


def _web_color_text(f):
    groups = json.loads(f['groups_json'] or '[]')
    colors = json.loads(f['colors_json'] or '[]')
    if groups:
        return '\u3000'.join(f'{g}：{colors[i] if i < len(colors) else ""}' for i, g in enumerate(groups))
    return f['fixed_color'] or ''


def _parse_color(text):
    text = _str(text)
    parts = [p for p in re.split(r'[\u3000\n]+', text) if p.strip()]
    pairs = [re.split(r'[：:]', p, maxsplit=1) for p in parts]
    if parts and all(len(x) == 2 and x[0].strip() and x[1].strip() for x in pairs):
        return [x[0].strip() for x in pairs], [x[1].strip() for x in pairs], ''
    return [], [], text[:20]


def _num(value):
    text = unicodedata.normalize('NFKC', _str(value))
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number > 0 else None


def _dims_text(f):
    if f['blank'] or f['unknown_size'] or not f['width_mm']:
        return ''
    return f"{f['width_mm']}×{f['height_mm']}"


def _parse_dims(value):
    text = unicodedata.normalize('NFKC', _str(value))
    m = re.fullmatch(r'\s*(\d{1,3})\s*[x×*]\s*(\d{1,3})\s*', text)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if not (1 <= w <= 200 and 1 <= h <= 200):
        return None
    return w, h


def _web_formats(c):
    rows = c.execute(f'''SELECT f.*, cu.source_name customer_name FROM name_formats f
        JOIN name_customers cu ON cu.id=f.customer_id
        WHERE f.retired=0 AND {names.NOT_SOFT_DELETED.replace('deleted_at', 'f.deleted_at')}
          AND cu.retired=0 AND {names.NOT_SOFT_DELETED.replace('deleted_at', 'cu.deleted_at')}
        ORDER BY f.id''').fetchall()
    return {r['id']: r for r in rows}


def web_values(f):
    """WEBの形式を、ソースの列の書き方にした値。"""
    return {
        'customer': f['customer_name'],
        'placement': _placement_excel(f['placement']),
        'size': {'small': '小ネーム', 'large': '大ネーム'}.get(_web_size_kind(f), ''),
        'color': _web_color_text(f),
        'font': _font_excel(f['family']),
        'font_size': (str(int(f['maximum_font'])) if float(f['maximum_font'] or 0).is_integer() else str(f['maximum_font'])) if f['maximum_font'] else '',
        'fixed_text': f['heading'] or '',
        'form_name': f['format_name'],
        'dims': _dims_text(f),
    }


def _same_customer(source, web):
    return _norm(source) == _norm(web) or _norm(names._correct_customer_name(source)) == _norm(web)


def _near_customer(source, web):
    a, b = _norm(source), _norm(web)
    return len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))


def _strip_bands(value):
    text = re.sub(r'(上段|中段|下段)\s*[：:]?\s*', ' ', _str(value))
    return re.sub(r'[\s\u3000]+', ' ', text).strip()


def field_diffs(row, f):
    """紐づいた行と形式の違い。比べられない書き方（汎用・部署別など）は比べない。"""
    wv = web_values(f)
    out = []
    def add(key):
        out.append({'field': key, 'label': LABELS[key], 'source': row[key], 'web': wv[key]})
    if not _same_customer(row['customer'], wv['customer']):
        add('customer')
    if _norm(row['placement']) != _norm(wv['placement']):
        add('placement')
    kind = _size_kind(row['size'])
    if kind and kind != _web_size_kind(f):
        add('size')
    if not ('下記参照' in row['color'] and json.loads(f['groups_json'] or '[]')) and _norm(row['color']) != _norm(wv['color']):
        add('color')
    if row['font'] and not re.search(r'上段|下段', row['font']) and _font_key(row['font']) != _font_key(f['family']):
        add('font')
    a, b = _num(row['font_size']), _num(wv['font_size'])
    if a is not None and a != b:
        add('font_size')
    if row['fixed_text'] and wv['fixed_text'] and _norm(_strip_bands(row['fixed_text'])) != _norm(_strip_bands(wv['fixed_text'])):
        add('fixed_text')
    if row['form_name'] and row['form_name'] != wv['form_name']:
        add('form_name')
    if row['dims'] and _parse_dims(row['dims']) != _parse_dims(wv['dims']):
        add('dims')
    return out


def _embedded(note):
    found = {}
    for line in _str(note).splitlines():
        m = re.match(r'(貼付け部位|大きさ|色|フォント|フォントサイズ)：(.*)$', line.strip())
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2)
    return found


def _score(row, f):
    emb = _embedded(f['note'])
    customer = 4 if _same_customer(row['customer'], f['customer_name']) else 3 if _near_customer(row['customer'], f['customer_name']) else 0
    place = _norm(row['placement']) in (_norm(_placement_excel(f['placement'])), _norm(emb.get('貼付け部位')))
    size_exact = bool(row['size']) and _norm(row['size']) == _norm(emb.get('大きさ'))
    color = _norm(row['color']) in (_norm(_web_color_text(f)), _norm(emb.get('色')))
    font = bool(row['font']) and _font_key(row['font']) == _font_key(emb.get('フォント') or f['family'])
    fsize = bool(row['font_size']) and _norm(row['font_size']) == _norm(emb.get('フォントサイズ'))
    kind = _size_kind(row['size']) == _web_size_kind(f)
    if not customer and not (place and size_exact and color and emb):
        return 0
    if not place and not (customer == 4 and (size_exact or kind) and color):
        return 0
    bands = json.loads(f['spec_json'] or '{}').get('bandFontsPt') or []
    band = bool(bands) and [int(x) for x in re.findall(r'\d+', unicodedata.normalize('NFKC', row['font_size']))] == [int(x) for x in bands]
    return customer + 3 * place + band + (2 if size_exact else 1 if kind else 0) + 2 * color + font + fsize


def auto_link(c):
    """未紐づけの行と形式を内容で対応付ける。同点で決まらないものは紐づけない。"""
    rows = [r for r in c.execute("SELECT * FROM name_source_rows WHERE kind='row' AND format_id IS NULL")]
    taken = {r['format_id'] for r in c.execute('SELECT format_id FROM name_source_rows WHERE format_id IS NOT NULL')}
    formats = [f for fid, f in _web_formats(c).items() if fid not in taken]
    scores = {}
    for r in rows:
        for f in formats:
            s = _score(r, f)
            if s >= 7:
                scores[(r['id'], f['id'])] = s
    linked = 0
    used_r, used_f = set(), set()
    for (rid, fid), s in sorted(scores.items(), key=lambda x: -x[1]):
        if rid in used_r or fid in used_f:
            continue
        rival = any(v == s and ((a == rid and b != fid and b not in used_f) or (b == fid and a != rid and a not in used_r))
                    for (a, b), v in scores.items())
        if rival:
            continue
        used_r.add(rid); used_f.add(fid)
        c.execute('UPDATE name_source_rows SET format_id=? WHERE id=?', (fid, rid))
        linked += 1
    return linked


def compute(c):
    formats = _web_formats(c)
    items = []
    linked_ids = set()
    for row in c.execute("SELECT * FROM name_source_rows WHERE kind='row' ORDER BY position, id"):
        f = formats.get(row['format_id']) if row['format_id'] else None
        if f:
            linked_ids.add(f['id'])
            diffs = field_diffs(row, f)
            if diffs:
                items.append({'key': f'r{row["id"]}', 'type': 'changed', 'rowId': row['id'], 'formatId': f['id'],
                              'nameId': _id_text(f['id']), 'customer': row['customer'] or f['customer_name'],
                              'title': f['format_name'], 'diffs': diffs})
        else:
            if not any(row[k] for k in TEXT_KEYS):
                continue
            items.append({'key': f'r{row["id"]}', 'type': 'source_only', 'rowId': row['id'], 'formatId': None,
                          'nameId': '', 'customer': row['customer'], 'title': row['size'],
                          'diffs': [{'field': k, 'label': LABELS[k], 'source': row[k], 'web': ''} for k in SYNC_FIELDS if row[k]]})
    for fid, f in formats.items():
        if fid in linked_ids:
            continue
        wv = web_values(f)
        items.append({'key': f'f{fid}', 'type': 'web_only', 'rowId': None, 'formatId': fid, 'nameId': _id_text(fid),
                      'customer': f['customer_name'], 'title': f['format_name'],
                      'diffs': [{'field': k, 'label': LABELS[k], 'source': '', 'web': wv[k]} for k in SYNC_FIELDS if wv[k]]})
    counts = {t: sum(1 for i in items if i['type'] == t) for t in ('changed', 'source_only', 'web_only')}
    total_linked = len(linked_ids)
    counts['same'] = total_linked - counts['changed']
    return items, counts


def diff(handler, c, d=None):
    blocked = _require(handler.user)
    if blocked:
        return blocked
    if (d or {}).get('relink') and handler.user['role'] == 'owner':
        auto_link(c)
    items, counts = compute(c)
    return 200, {'items': items, 'counts': counts}


# ---------- 反映 ----------

def _move_customer(c, f, name):
    """形式の顧客名を変える。同名の顧客があれば移す、この形式だけの顧客なら名前を変える、それ以外は新しく作る。"""
    found = c.execute(f'SELECT id FROM name_customers WHERE retired=0 AND {names.NOT_SOFT_DELETED} AND source_name=?', (name,)).fetchone()
    if found:
        target = found['id']
    else:
        others = c.execute(f'SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND id<>? AND retired=0 AND {names.NOT_SOFT_DELETED}',
                           (f['customer_id'], f['id'])).fetchone()['n']
        if not others:
            c.execute('UPDATE name_customers SET source_name=?, manual=1 WHERE id=?', (name, f['customer_id']))
            return f['customer_id']
        c.execute('''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, manual, retired)
            VALUES(?,?,?,?,?,?,1,0)''', ('manual:' + __import__('uuid').uuid4().hex, -1, name, '', '', '未対応'))
        target = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    if c.execute(f'SELECT id FROM name_formats WHERE customer_id=? AND format_name=? AND id<>? AND retired=0 AND {names.NOT_SOFT_DELETED}',
                 (target, f['format_name'], f['id'])).fetchone():
        raise ValueError(f'{name} に同じ形式名「{f["format_name"]}」があります。')
    c.execute('UPDATE name_formats SET customer_id=? WHERE id=?', (target, f['id']))
    return target


def _apply_to_web(c, row, f, fields):
    sets = {}
    for key in fields:
        value = row[key]
        if key == 'customer':
            if not value:
                raise ValueError('顧客名が空の行はWEBへ反映できません。')
            _move_customer(c, f, value)
        elif key == 'placement':
            sets['placement'] = _placement_web(value)[:120]
        elif key == 'size':
            kind = _size_kind(value)
            dims = _parse_dims(row['dims']) or ((50, 24) if kind == 'small' else (80, 50))
            sets.update(small=1 if kind == 'small' else 0, width_mm=dims[0], height_mm=dims[1], unknown_size=0, blank=0)
        elif key == 'color':
            groups, colors, fixed = _parse_color(value)
            sets.update(groups_json=json.dumps(groups, ensure_ascii=False), colors_json=json.dumps(colors, ensure_ascii=False),
                        swatches_json=json.dumps([names._swatch(x) for x in colors]), fixed_color=fixed)
        elif key == 'font':
            sets['family'] = _font_web(value)
        elif key == 'font_size':
            sets['maximum_font'] = _num(value) or 0
        elif key == 'fixed_text':
            sets['heading'] = _strip_bands(value)[:400]
        elif key == 'form_name':
            if c.execute(f'SELECT id FROM name_formats WHERE customer_id=(SELECT customer_id FROM name_formats WHERE id=?) AND format_name=? AND id<>? AND retired=0 AND {names.NOT_SOFT_DELETED}',
                         (f['id'], value, f['id'])).fetchone():
                raise ValueError(f'同じ顧客に形式名「{value}」があります。')
            sets['format_name'] = value[:80]
        elif key == 'dims':
            dims = _parse_dims(value)
            if not dims:
                raise ValueError(f'寸法は 50×24 の形で入力してください：{value}')
            sets.update(width_mm=dims[0], height_mm=dims[1])
    if sets:
        sets['manual'] = 1
        c.execute(f'UPDATE name_formats SET {", ".join(k + "=?" for k in sets)} WHERE id=?', list(sets.values()) + [f['id']])


def _create_web(c, row):
    if not row['customer']:
        raise ValueError('顧客名が空の行はWEBへ追加できません。')
    kind = _size_kind(row['size'])
    base = row['form_name'] or row['size'] or 'ネーム'
    groups, colors, fixed = _parse_color(row['color'])
    dims = _parse_dims(row['dims'])
    fields = {
        'customer': row['customer'][:80], 'reading': '', 'format': base[:80], 'placement': _placement_web(row['placement'])[:120],
        'heading': _strip_bands(row['fixed_text'])[:400], 'note': row['note'][:400], 'pending': '', 'caption': '',
        'groups': groups, 'colors': colors, 'fixed': fixed, 'departments': [],
        'width': dims[0] if dims else (50 if kind == 'small' else 80 if kind == 'large' else 0),
        'height': dims[1] if dims else (24 if kind == 'small' else 50 if kind == 'large' else 0),
        'unknown': not kind, 'blank': False, 'small': kind == 'small', 'reasons': [] if kind else ['サイズが未指定です。'],
    }
    customer_id = names._apply_customer(c, fields, None)
    name, n = fields['format'], 1
    while c.execute(f'SELECT id FROM name_formats WHERE customer_id=? AND format_name=? AND retired=0 AND {names.NOT_SOFT_DELETED}', (customer_id, name)).fetchone():
        n += 1
        name = f'{fields["format"]}（{n}）'
    fields['format'] = name
    fid = names._write_format(c, fields, customer_id)
    family = _font_web(row['font']) if row['font'] else 'HG丸ｺﾞｼｯｸM-PRO'
    c.execute('UPDATE name_formats SET family=?, maximum_font=? WHERE id=?', (family, _num(row['font_size']) or 0, fid))
    return fid


def _apply_to_source(c, row, f, fields, user):
    wv = web_values(f)
    sets = {k: wv[k] for k in fields}
    if not row['form_name']:
        sets['form_name'] = wv['form_name']
    if not row['dims'] and wv['dims']:
        sets['dims'] = wv['dims']
    sets.update(updated_at=_now(), updated_by=user['name'])
    c.execute(f'UPDATE name_source_rows SET {", ".join(k + "=?" for k in sets)}, format_id=? WHERE id=?',
              list(sets.values()) + [f['id'], row['id']])


def _append_source(c, f, user):
    wv = web_values(f)
    last = c.execute("SELECT MAX(position) p FROM name_source_rows WHERE customer=?", (wv['customer'],)).fetchone()['p']
    if last is None:
        position = (c.execute('SELECT MAX(position) p FROM name_source_rows').fetchone()['p'] or 0) + 1
    else:
        nxt = c.execute('SELECT position FROM name_source_rows WHERE position>? ORDER BY position LIMIT 1', (last,)).fetchone()
        position = (last + nxt['position']) / 2 if nxt else last + 1
    keys = [k for k in SYNC_FIELDS]
    c.execute(f'INSERT INTO name_source_rows(position, kind, format_id, {", ".join(keys)}, updated_at, updated_by) '
              f'VALUES(?,?,?,{",".join("?" * len(keys))},?,?)',
              [position, 'row', f['id']] + [wv[k] for k in keys] + [_now(), user['name']])
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def apply(handler, c, d):
    blocked = _owner(handler.user)
    if blocked:
        return blocked
    choices = d.get('choices')
    if not isinstance(choices, dict):
        raise ValueError('反映する行を選んでください。')
    choices = {k: v for k, v in choices.items() if v in ('to_web', 'to_source')}
    if not choices:
        raise ValueError('「ソース→WEB」か「WEB→ソース」を選んだ行がありません。')
    items, _ = compute(c)
    by_key = {i['key']: i for i in items}
    missing = [k for k in choices if k not in by_key]
    if missing:
        return 409, {'error': '差分が変わりました。もう一度「差分を確認」を開いてください。'}
    backup = backup_db(c, 'pre-name-source-sync')
    formats = _web_formats(c)
    done = {'to_web': 0, 'to_source': 0, 'created_web': 0, 'created_source': 0}
    for key, action in choices.items():
        item = by_key[key]
        row = c.execute('SELECT * FROM name_source_rows WHERE id=?', (item['rowId'],)).fetchone() if item['rowId'] else None
        f = formats.get(item['formatId']) if item['formatId'] else None
        fields = [x['field'] for x in item['diffs']]
        before_web = web_values(f) if f else None
        before_src = dict(row) if row else None
        if item['type'] == 'changed' and action == 'to_web':
            _apply_to_web(c, row, f, fields); done['to_web'] += 1
            fid = f['id']
        elif item['type'] == 'changed':
            _apply_to_source(c, row, f, fields, handler.user); done['to_source'] += 1
            fid = f['id']
        elif item['type'] == 'source_only' and action == 'to_web':
            fid = _create_web(c, row)
            c.execute('UPDATE name_source_rows SET format_id=? WHERE id=?', (fid, row['id'])); done['created_web'] += 1
        elif item['type'] == 'web_only' and action == 'to_source':
            _append_source(c, f, handler.user); done['created_source'] += 1
            fid = f['id']
        else:
            raise ValueError('選べない反映方向があります。もう一度選んでください。')
        _history(c, handler.user, 'ソース→WEB' if action == 'to_web' else 'WEB→ソース', item['rowId'], fid,
                 {'type': item['type'], 'fields': fields, 'webBefore': before_web, 'sourceBefore': before_src})
    return 200, {'done': done, 'backup': backup}
