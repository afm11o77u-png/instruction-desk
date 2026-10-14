import name_review
"""ネーム情報。

統合ネームラベルの登録を、形式ごとの見本つきで参照する。
元ソフトと元資料は変更しない。Excelの行番号は登録番号にしない。
"""
import hashlib, json, re, uuid, base64
from pathlib import Path

DATA_DIR = None
LAYOUTS = {
    'Legacy': '氏名・番号・コード',
    'Name': '氏名',
    'Surname': '苗字',
    'HeadingName': '見出しと氏名',
    'NumberName': '番号と氏名',
    'SplitNumber': '番号を左右に印字',
    'NumberOnly': '番号のみ',
    'QR': '部署・氏名・QR',
    'DeptName': '部署と氏名',
    'DeptNumberName': '部署・番号・氏名',
    'DeptOnly': '部署名のみ',
    'DeptRoleName': '部署・役職・氏名',
    'DeptFurigana': '部署・ふりがな・氏名',
    'RoleName': '役職と氏名',
    'Fixed': '固定の資格表示',
    'FixedSingle': '固定の文字',
    'PrefixName': '丸数字と氏名',
    'CohortName': '期生と氏名',
    'CompanyCodeName': '会社コードと氏名',
    'RepeatName': '氏名を上下に印字',
    'NameParts': '苗字と名前を分けて印字',
    'InitialName': '苗字と追加の1文字',
    'InitialMixed': '苗字と追加文字の大きさ違い',
    'MixedName': '一部を大きくした氏名',
    'StarRoman': '星の数・氏名・ローマ字',
    'OptionalHeading': '見出しがあるときだけ上段',
    'SplitCode': '記号と番号を左右に印字',
    'SizeNumber': 'サイズと番号',
    'FreeText': '自由入力',
    'Blank': '無地（印字なし）',
    'Unavailable': '仕様未記入',
}

def bind(data_dir):
    global DATA_DIR
    DATA_DIR = Path(data_dir)

def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS name_customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_key TEXT NOT NULL UNIQUE,
        source_index INTEGER NOT NULL,
        source_name TEXT NOT NULL,
        reading TEXT NOT NULL DEFAULT '',
        linked_name TEXT NOT NULL DEFAULT '',
        link_status TEXT NOT NULL DEFAULT '未対応',
        retired INTEGER NOT NULL DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS name_formats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity TEXT NOT NULL UNIQUE,
        customer_id INTEGER NOT NULL,
        source_kind INTEGER NOT NULL,
        format_name TEXT NOT NULL,
        placement TEXT NOT NULL DEFAULT '',
        family TEXT NOT NULL DEFAULT '',
        header_family TEXT NOT NULL DEFAULT '',
        heading TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        layout TEXT NOT NULL DEFAULT '',
        reading TEXT NOT NULL DEFAULT '',
        small INTEGER NOT NULL DEFAULT 0,
        width_mm INTEGER NOT NULL DEFAULT 0,
        height_mm INTEGER NOT NULL DEFAULT 0,
        unknown_size INTEGER NOT NULL DEFAULT 0,
        fixed_color TEXT NOT NULL DEFAULT '',
        color_caption TEXT NOT NULL DEFAULT '',
        groups_json TEXT NOT NULL DEFAULT '[]',
        colors_json TEXT NOT NULL DEFAULT '[]',
        swatches_json TEXT NOT NULL DEFAULT '[]',
        departments_json TEXT NOT NULL DEFAULT 'null',
        pending TEXT NOT NULL DEFAULT '',
        maximum_font REAL NOT NULL DEFAULT 0,
        heading_font REAL NOT NULL DEFAULT 0,
        number_length INTEGER NOT NULL DEFAULT 0,
        source_row INTEGER,
        previous_source_row INTEGER,
        spec_json TEXT NOT NULL DEFAULT '{}',
        needs_review INTEGER NOT NULL DEFAULT 0,
        review_json TEXT NOT NULL DEFAULT '[]',
        blank INTEGER NOT NULL DEFAULT 0,
        retired INTEGER NOT NULL DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS name_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        format_id INTEGER NOT NULL,
        variant_key TEXT NOT NULL,
        condition_label TEXT NOT NULL DEFAULT '',
        paper_color TEXT NOT NULL DEFAULT '',
        relative_path TEXT NOT NULL DEFAULT '',
        caption TEXT NOT NULL DEFAULT '',
        UNIQUE(format_id, variant_key)
    )''')
    _column(c, 'name_customers', 'manual', 'manual INTEGER NOT NULL DEFAULT 0')
    _column(c, 'name_formats', 'manual', 'manual INTEGER NOT NULL DEFAULT 0')
    name_review.schema(c)
    correct_customer_names(c)

def _column(c, table, name, ddl):
    have = {row['name'] for row in c.execute(f'PRAGMA table_info({table})')}
    if name not in have:
        c.execute(f'ALTER TABLE {table} ADD COLUMN {ddl}')

# ネーム情報の顧客名の既知の表記ゆれ（取込元・既存行の表示名を直す）
CUSTOMER_NAME_CORRECTIONS = {
    '日綜中垣': '日総',
    '札幌、白石、岩見沢、宮の森明日香': '札幌/桜台/白石/岩見沢/宮の沢明日佳',
    '札幌/白石/岩見沢/宮の森明日佳': '札幌/桜台/白石/岩見沢/宮の沢明日佳',
}

# 表示名補正に合わせる読み（wrong source_name → reading）。未定義なら reading は触らない。
ASHUKA_READING_RIGHT = 'さっぽろさくらだいしろいいわみざわみやのさわあすか'
CUSTOMER_READING_CORRECTIONS = {
    '札幌、白石、岩見沢、宮の森明日香': ASHUKA_READING_RIGHT,
    '札幌/白石/岩見沢/宮の森明日佳': ASHUKA_READING_RIGHT,
}

def _correct_customer_name(name):
    text = str(name or '').strip()
    return CUSTOMER_NAME_CORRECTIONS.get(text, text)

def _correct_customer_reading(name, reading):
    text = str(name or '').strip()
    if text in CUSTOMER_READING_CORRECTIONS:
        return CUSTOMER_READING_CORRECTIONS[text]
    return str(reading or '')

def correct_customer_names(c):
    """ネーム情報の顧客表示名・読みを直す（指示書・在庫の履歴は触らない）。"""
    for wrong, right in CUSTOMER_NAME_CORRECTIONS.items():
        reading = CUSTOMER_READING_CORRECTIONS.get(wrong)
        if reading is not None:
            c.execute(
                'UPDATE name_customers SET source_name=?, reading=? WHERE source_name=?',
                (right, reading, wrong),
            )
        else:
            c.execute('UPDATE name_customers SET source_name=? WHERE source_name=?', (right, wrong))
        c.execute('UPDATE name_customers SET linked_name=? WHERE linked_name=?', (right, wrong))

def _app_customers(c):
    names = set()
    for table in ('placements', 'shipments'):
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not exists: continue
        for (name,) in c.execute(f"SELECT DISTINCT customer FROM {table} WHERE customer<>''"):
            if name: names.add(name)
    for (payload,) in c.execute('SELECT payload FROM orders'):
        try: value = json.loads(payload)
        except json.JSONDecodeError: continue
        customer = str(value.get('customer') or '').strip()
        if customer: names.add(customer)
    return names

def _link(name, known):
    if name in known: return name, '対応'
    return '', '未対応'

def import_catalog(connect, data_dir):
    bind(data_dir)
    path = Path(data_dir) / 'name-import' / 'catalog.json'
    if not path.exists(): return None
    blob = path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    data = json.loads(blob.decode('utf-8'))
    with connect() as c:
        schema(c)
        known = _app_customers(c)
        previous = c.execute("SELECT value FROM settings WHERE key='name_import'").fetchone()
        same = previous and json.loads(previous['value']).get('sha256') == digest
        if same:
            for row in c.execute('SELECT id, source_name FROM name_customers WHERE retired=0'):
                linked, status = _link(row['source_name'], known)
                c.execute('UPDATE name_customers SET linked_name=?, link_status=? WHERE id=?', (linked, status, row['id']))
            return {'unchanged': True}
        keys = set()
        for item in data.get('customers') or []:
            key = item['sourceKey']
            keys.add(key)
            raw_name = item.get('name') or ''
            name = _correct_customer_name(raw_name)
            reading = _correct_customer_reading(raw_name, item.get('reading') or '')
            linked, status = _link(name, known)
            current = c.execute('SELECT id, manual FROM name_customers WHERE source_key=?', (key,)).fetchone()
            if current and current['manual']:
                c.execute('UPDATE name_customers SET linked_name=?, link_status=?, retired=0 WHERE id=?', (linked, status, current['id']))
            elif current:
                c.execute('''UPDATE name_customers SET source_index=?, source_name=?, reading=?, linked_name=?, link_status=?, retired=0 WHERE id=?''',
                    (item.get('sourceIndex'), name, reading, linked, status, current['id']))
            else:
                c.execute('''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, retired)
                    VALUES(?,?,?,?,?,?,0)''', (key, item.get('sourceIndex'), name, reading, linked, status))
        identities = set()
        by_key = {row['source_key']: row['id'] for row in c.execute('SELECT id, source_key FROM name_customers')}
        for item in data.get('formats') or []:
            identity = item['identity']
            identities.add(identity)
            customer_id = by_key.get(item.get('customerKey'))
            if not customer_id: continue
            spec = {k: item.get(k) for k in (
                'surnameOnly', 'spaceSurname', 'strictFont', 'colorFromDepartment', 'inputCaption', 'numberPrefix', 'white', 'sourceFiles')}
            values = (
                customer_id, item.get('sourceKind'), item.get('format') or '', item.get('placement') or '',
                item.get('family') or '', item.get('headerFamily') or '', item.get('heading') or '', item.get('note') or '',
                item.get('layout') or '', item.get('reading') or '', 1 if item.get('small') else 0,
                item.get('widthMm') or 0, item.get('heightMm') or 0, 1 if item.get('unknownSize') else 0,
                item.get('fixedColor') or '', item.get('colorCaption') or '',
                json.dumps(item.get('groups') or [], ensure_ascii=False),
                json.dumps(item.get('colors') or [], ensure_ascii=False),
                json.dumps(item.get('colorSwatches') or [], ensure_ascii=False),
                json.dumps(item.get('departments'), ensure_ascii=False),
                item.get('pending') or '', item.get('maximumFont') or 0, item.get('headingFont') or 0,
                item.get('numberLength') or 0, item.get('sourceRow'), item.get('previousSourceRow'),
                json.dumps(spec, ensure_ascii=False), 1 if item.get('needsReview') else 0,
                json.dumps(item.get('reviewReasons') or [], ensure_ascii=False), 1 if item.get('blank') else 0,
            )
            current = c.execute('SELECT id, manual FROM name_formats WHERE identity=?', (identity,)).fetchone()
            if current and current['manual']:
                c.execute('UPDATE name_formats SET retired=0 WHERE id=?', (current['id'],))
                continue
            if current:
                fid = current['id']
                c.execute('''UPDATE name_formats SET customer_id=?, source_kind=?, format_name=?, placement=?, family=?, header_family=?, heading=?, note=?,
                    layout=?, reading=?, small=?, width_mm=?, height_mm=?, unknown_size=?, fixed_color=?, color_caption=?, groups_json=?, colors_json=?,
                    swatches_json=?, departments_json=?, pending=?, maximum_font=?, heading_font=?, number_length=?, source_row=?, previous_source_row=?,
                    spec_json=?, needs_review=?, review_json=?, blank=?, retired=0 WHERE id=?''', values + (fid,))
            else:
                c.execute('''INSERT INTO name_formats(identity, customer_id, source_kind, format_name, placement, family, header_family, heading, note,
                    layout, reading, small, width_mm, height_mm, unknown_size, fixed_color, color_caption, groups_json, colors_json, swatches_json,
                    departments_json, pending, maximum_font, heading_font, number_length, source_row, previous_source_row, spec_json, needs_review,
                    review_json, blank, retired) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)''', (identity,) + values)
                fid = c.execute('SELECT id FROM name_formats WHERE identity=?', (identity,)).fetchone()['id']
            c.execute('DELETE FROM name_samples WHERE format_id=?', (fid,))
            for sample in item.get('samples') or []:
                rel = str(sample.get('relativePath') or '')
                if not rel.startswith('images/names/') or not (Path(data_dir) / rel).is_file(): rel = ''
                c.execute('''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
                    VALUES(?,?,?,?,?,?)''', (fid, sample.get('key') or 'standard', sample.get('condition') or '', sample.get('paperColor') or '', rel, sample.get('caption') or ''))
        if identities:
            marks = ','.join('?' for _ in identities)
            c.execute(f'UPDATE name_formats SET retired=1 WHERE manual=0 AND identity NOT IN ({marks})', tuple(identities))
        if keys:
            marks = ','.join('?' for _ in keys)
            c.execute(f'''UPDATE name_customers SET retired=1 WHERE manual=0 AND source_key NOT IN ({marks})
                AND id NOT IN (SELECT customer_id FROM name_formats WHERE manual=1 AND retired=0)''', tuple(keys))
        summary = {
            'sha256': digest,
            'customers': data.get('customerCount'),
            'formats': data.get('formatCount'),
            'images': data.get('imageCount'),
            'review': data.get('reviewCount'),
            'pending': data.get('pendingCount'),
            'blank': data.get('blankCount'),
            'missingFonts': data.get('missingFonts') or [],
            'resolvedFonts': data.get('resolvedFonts') or {},
        }
        c.execute("INSERT OR REPLACE INTO settings VALUES('name_import',?)", (json.dumps(summary, ensure_ascii=False),))
    return summary

def _require(user):
    if not user or user['must_change']: return 401, {'error': 'ログインし直してください。'}
    if user['role'] not in ('owner', 'sales', 'floor', 'floor2'): return 403, {'error': 'ネーム情報は閲覧できません。'}
    return None

def _number(row_id):
    return f'NM-{row_id:06}'

def _size_text(row):
    if row['unknown_size']: return 'サイズ未指定（要確認）'
    if row['blank']: return '無地'
    if row['small']: return f"小（{row['width_mm']}×{row['height_mm']} mm）"
    return f"大（{row['width_mm']}×{row['height_mm']} mm）"

def _colors(row):
    groups = json.loads(row['groups_json'] or '[]')
    colors = json.loads(row['colors_json'] or '[]')
    swatches = json.loads(row['swatches_json'] or '[]')
    rows = []
    for index, group in enumerate(groups):
        color = colors[index] if index < len(colors) else ''
        swatch = swatches[index] if index < len(swatches) else '#FFFFFF'
        rows.append({'group': group, 'color': color, 'swatch': swatch})
    return rows

def search(handler, c):
    from urllib.parse import parse_qs, urlparse
    blocked = _require(handler.user)
    if blocked: return blocked
    q = (parse_qs(urlparse(handler.path).query).get('q') or [''])[0].strip()
    where = 'WHERE retired=0'
    args = []
    if q:
        where += ' AND source_name LIKE ?'
        args.append(f'%{q}%')
    rows = c.execute(f'SELECT * FROM name_customers {where} ORDER BY source_name, id', args).fetchall()
    items = []
    for row in rows:
        counts = c.execute('SELECT COUNT(*) n, SUM(needs_review) review FROM name_formats WHERE customer_id=? AND retired=0', (row['id'],)).fetchone()
        assignees, approved, unapproved = [], 0, 0
        for form in c.execute('SELECT id FROM name_formats WHERE customer_id=? AND retired=0', (row['id'],)):
            state = name_review.state(c, form['id'], handler.user)
            label = state['assigneeName'] or state['sourceLabel'] or '未設定'
            if label not in assignees: assignees.append(label)
            if state['status'] == '了承': approved += 1
            else: unapproved += 1
        items.append({
            'id': row['id'], 'sourceName': row['source_name'], 'reading': row['reading'],
            'linkedName': row['linked_name'], 'linkStatus': row['link_status'],
            'formatCount': counts['n'] or 0, 'reviewCount': counts['review'] or 0,
            'confirmationCount': approved, 'assignees': assignees,
            'approvedCount': approved, 'unapprovedCount': unapproved,
        })
    summary = c.execute("SELECT value FROM settings WHERE key='name_import'").fetchone()
    meta = json.loads(summary['value']) if summary else {}
    return 200, {'total': len(items), 'items': items, 'missingFonts': meta.get('missingFonts') or [], 'resolvedFonts': meta.get('resolvedFonts') or {}}

def customer(handler, c, customer_id):
    blocked = _require(handler.user)
    if blocked: return blocked
    row = c.execute('SELECT * FROM name_customers WHERE id=? AND retired=0', (customer_id,)).fetchone()
    if not row: return 404, {'error': '顧客が見つかりません。'}
    formats = []
    for item in c.execute('SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id', (row['id'],)):
        sample = c.execute('SELECT id, relative_path FROM name_samples WHERE format_id=? AND relative_path<>"" ORDER BY id LIMIT 1', (item['id'],)).fetchone()
        formats.append({
            'id': item['id'], 'number': _number(item['id']), 'format': item['format_name'],
            'placement': item['placement'], 'size': _size_text(item), 'needsReview': bool(item['needs_review']),
            'blank': bool(item['blank']), 'hasImage': bool(sample), 'sampleId': sample['id'] if sample else None,
            'colors': _colors(item), 'fixedColor': item['fixed_color'],
            'review': name_review.state(c,item['id'],handler.user),
        })
    return 200, {
        'id': row['id'], 'sourceName': row['source_name'], 'reading': row['reading'],
        'linkedName': row['linked_name'], 'linkStatus': row['link_status'], 'formats': formats,
    }

def detail(handler, c, format_id):
    blocked = _require(handler.user)
    if blocked: return blocked
    row = c.execute('SELECT * FROM name_formats WHERE id=? AND retired=0', (format_id,)).fetchone()
    if not row: return 404, {'error': '形式が見つかりません。'}
    customer_row = c.execute('SELECT * FROM name_customers WHERE id=?', (row['customer_id'],)).fetchone()
    samples = []
    for sample in c.execute('SELECT * FROM name_samples WHERE format_id=? ORDER BY id', (row['id'],)):
        samples.append({
            'id': sample['id'], 'condition': sample['condition_label'], 'paperColor': sample['paper_color'],
            'caption': sample['caption'], 'hasImage': bool(sample['relative_path']),
        })
    spec = json.loads(row['spec_json'] or '{}')
    return 200, {
        'id': row['id'], 'number': _number(row['id']), 'format': row['format_name'],
        'sourceName': customer_row['source_name'] if customer_row else '',
        'customerId': row['customer_id'],
        'customerReading': customer_row['reading'] if customer_row else '',
        'linkedName': customer_row['linked_name'] if customer_row else '',
        'linkStatus': customer_row['link_status'] if customer_row else '未対応',
        'placement': row['placement'], 'size': _size_text(row),
        'sizeKind': 'blank' if row['blank'] else 'unknown' if row['unknown_size'] else 'small' if row['small'] else 'large',
        'widthMm': row['width_mm'], 'heightMm': row['height_mm'],
        'layout': LAYOUTS.get(row['layout'], row['layout']), 'layoutCode': row['layout'],
        'family': row['family'], 'headerFamily': row['header_family'], 'heading': row['heading'],
        'note': row['note'], 'reading': row['reading'], 'pending': row['pending'],
        'fixedColor': row['fixed_color'], 'colorCaption': row['color_caption'], 'colors': _colors(row),
        'colorText': _color_text(row),
        'departments': json.loads(row['departments_json'] or 'null'),
        'departmentText': '\n'.join(json.loads(row['departments_json'] or 'null') or []),
        'maximumFont': row['maximum_font'], 'headingFont': row['heading_font'], 'numberLength': row['number_length'],
        'sourceRow': row['source_row'], 'identity': row['identity'],
        'needsReview': bool(row['needs_review']), 'reviewReasons': json.loads(row['review_json'] or '[]'),
        'blank': bool(row['blank']), 'flags': spec, 'samples': samples,
        'review': name_review.state(c,format_id,handler.user),
        'reviewUsers': [dict(u) for u in c.execute("SELECT id,name FROM users WHERE active=1 AND login NOT LIKE '*name:%' ORDER BY name")] if handler.user['role']=='owner' else [],
    }

def send_sample(handler, c, sample_id):
    blocked = _require(handler.user)
    if blocked: return blocked
    row = c.execute('SELECT relative_path FROM name_samples WHERE id=?', (sample_id,)).fetchone()
    if not row or not row['relative_path']: return 404, {'error': '見本画像はありません。'}
    root = (DATA_DIR / 'images' / 'names').resolve()
    path = (DATA_DIR / row['relative_path']).resolve()
    if root not in path.parents or not path.is_file(): return 404, {'error': '見本画像はありません。'}
    kind = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(path.suffix.lower(), 'application/octet-stream')
    return 200, path.read_bytes(), kind

def _owner(user):
    blocked = _require(user)
    if blocked: return blocked
    if user['role'] != 'owner': return 403, {'error': 'ネーム情報の登録と修正は主管理者だけができます。'}
    return None

def _text(value, limit, label):
    if value is None: value = ''
    if not isinstance(value, str): raise ValueError(f'{label}を確認してください。')
    value = value.strip()
    if len(value) > limit: raise ValueError(f'{label}は{limit}文字以内にしてください。')
    return value

def _color_text(row):
    groups = json.loads(row['groups_json'] or '[]')
    colors = json.loads(row['colors_json'] or '[]')
    if groups:
        lines = []
        for index, group in enumerate(groups):
            color = colors[index] if index < len(colors) else ''
            lines.append(f'{group} {color}'.strip())
        return '\n'.join(lines)
    return row['fixed_color'] or ''

def _swatch(name):
    key = str(name or '').replace('?', '').replace('？', '').strip()
    return {'青': '#87CEFA', '緑': '#90EE90', '赤': '#F08080', '黄': '#FFFFE0', 'オレンジ': '#FFDAB9', '白': '#FFFFFF'}.get(key, '#FFFFFF')

def _parse_colors(text):
    groups, colors = [], []
    fixed = ''
    for raw in text.splitlines():
        line = raw.strip()
        if not line: continue
        parts = [part for part in re.split(r'[\s,、]+', line, maxsplit=1) if part]
        if len(parts) == 1:
            if not groups and not fixed:
                fixed = parts[0][:20]
            else:
                groups.append(parts[0][:40])
                colors.append('')
        else:
            groups.append(parts[0][:40])
            colors.append(parts[1][:20])
    if groups:
        fixed = ''
    return groups, colors, fixed

def _parse_lines(text, limit):
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line: continue
        if len(line) > limit: raise ValueError(f'1行は{limit}文字以内にしてください。')
        rows.append(line)
    return rows

def _size_fields(kind, width, height):
    if kind not in ('small', 'large', 'unknown', 'blank'):
        raise ValueError('サイズを選択してください。')
    if kind in ('unknown', 'blank'):
        return 0, 0, kind == 'unknown', kind == 'blank', kind == 'small'
    def number(value, label, default):
        if value in (None, ''): return default
        try: number_value = int(value)
        except (TypeError, ValueError): raise ValueError(f'{label}は数値で入力してください。')
        if not 1 <= number_value <= 200: raise ValueError(f'{label}は1から200までにしてください。')
        return number_value
    defaults = (50, 24) if kind == 'small' else (80, 50)
    return number(width, '幅', defaults[0]), number(height, '高さ', defaults[1]), False, False, kind == 'small'

def _fields(d):
    customer = _text(d.get('customerName'), 80, '顧客名')
    if not customer: raise ValueError('顧客名を入力してください。')
    reading = _text(d.get('reading'), 80, '読み')
    format_name = _text(d.get('formatName'), 80, '形式名')
    if not format_name: raise ValueError('形式名を入力してください。')
    placement = _text(d.get('placement'), 120, '貼付け位置')
    heading = _text(d.get('heading'), 400, '見出し・印字')
    note = _text(d.get('note'), 400, '注意')
    pending = _text(d.get('pending'), 200, '要確認')
    caption = _text(d.get('colorCaption'), 40, '色の条件の名前')
    groups, colors, fixed = _parse_colors(_text(d.get('colorText'), 2000, '用紙の色'))
    departments = _parse_lines(_text(d.get('departmentText'), 2000, '部署'), 40)
    width, height, unknown, blank, small = _size_fields(d.get('sizeKind'), d.get('widthMm'), d.get('heightMm'))
    reasons = []
    if pending: reasons.append('要確認：' + pending)
    if unknown: reasons.append('サイズが未指定です。')
    for color in ([fixed] if fixed else colors):
        if color and ('？' in color or '?' in color):
            reasons.append('色が未確定です：' + color)
    return {
        'customer': customer, 'reading': reading, 'format': format_name, 'placement': placement,
        'heading': heading, 'note': note, 'pending': pending, 'caption': caption,
        'groups': groups, 'colors': colors, 'fixed': fixed, 'departments': departments,
        'width': width, 'height': height, 'unknown': unknown, 'blank': blank, 'small': small,
        'reasons': reasons,
    }

def _store_upload(format_id, payload, filename):
    if not payload: return None
    if not isinstance(payload, str): raise ValueError('見本画像を確認してください。')
    try: raw = base64.b64decode(payload, validate=True)
    except ValueError: raise ValueError('見本画像を確認してください。')
    if not raw or len(raw) > 8 * 1024 * 1024: raise ValueError('見本画像は8MBまでです。')
    suffix = Path(str(filename or '')).suffix.lower()
    if suffix not in ('.png', '.jpg', '.jpeg', '.webp'):
        if raw.startswith(b'\x89PNG'): suffix = '.png'
        elif raw.startswith(b'\xff\xd8'): suffix = '.jpg'
        elif raw.startswith(b'RIFF') and raw[8:12] == b'WEBP': suffix = '.webp'
        else: raise ValueError('見本画像はPNG、JPEG、WebPにしてください。')
    folder = DATA_DIR / 'images' / 'names'
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f'upload-{format_id:06}.*'):
        old.unlink()
    name = f'upload-{format_id:06}{suffix}'
    path = folder / name
    path.write_bytes(raw)
    return f'images/names/{name}'

def _apply_customer(c, fields, customer_id):
    known = _app_customers(c)
    linked, status = _link(fields['customer'], known)
    if customer_id:
        row = c.execute('SELECT * FROM name_customers WHERE id=? AND retired=0', (customer_id,)).fetchone()
        if not row: raise ValueError('顧客が見つかりません。')
        other = c.execute('SELECT id FROM name_customers WHERE retired=0 AND source_name=? AND id<>?', (fields['customer'], customer_id)).fetchone()
        if other: raise ValueError('同じ顧客名がすでにあります。その顧客に形式を追加してください。')
        changed = row['source_name'] != fields['customer'] or (row['reading'] or '') != fields['reading']
        c.execute('UPDATE name_customers SET source_name=?, reading=?, linked_name=?, link_status=?, manual=? WHERE id=?',
            (fields['customer'], fields['reading'], linked, status, 1 if changed or row['manual'] else 0, customer_id))
        return customer_id
    found = c.execute('SELECT id FROM name_customers WHERE retired=0 AND source_name=?', (fields['customer'],)).fetchone()
    if found:
        if fields['reading']:
            c.execute('UPDATE name_customers SET reading=? WHERE id=? AND reading=?', (fields['reading'], found['id'], ''))
        c.execute('UPDATE name_customers SET linked_name=?, link_status=? WHERE id=?', (linked, status, found['id']))
        return found['id']
    c.execute('''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, manual, retired)
        VALUES(?,?,?,?,?,?,1,0)''', ('manual:' + uuid.uuid4().hex, -1, fields['customer'], fields['reading'], linked, status))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]

def _write_format(c, fields, customer_id, current=None):
    other = c.execute('SELECT id FROM name_formats WHERE retired=0 AND customer_id=? AND format_name=? AND id<>?',
        (customer_id, fields['format'], current['id'] if current else 0)).fetchone()
    if other: raise ValueError('この顧客に同じ形式名があります。')
    swatches = [_swatch(color) for color in fields['colors']]
    review = 1 if fields['reasons'] else 0
    if current:
        c.execute('''UPDATE name_formats SET customer_id=?, format_name=?, placement=?, heading=?, note=?, small=?, width_mm=?, height_mm=?,
            unknown_size=?, fixed_color=?, color_caption=?, groups_json=?, colors_json=?, swatches_json=?, departments_json=?, pending=?,
            needs_review=?, review_json=?, blank=?, manual=1, retired=0 WHERE id=?''', (
            customer_id, fields['format'], fields['placement'], fields['heading'], fields['note'], 1 if fields['small'] else 0,
            fields['width'], fields['height'], 1 if fields['unknown'] else 0, fields['fixed'], fields['caption'],
            json.dumps(fields['groups'], ensure_ascii=False), json.dumps(fields['colors'], ensure_ascii=False),
            json.dumps(swatches), json.dumps(fields['departments'], ensure_ascii=False), fields['pending'],
            review, json.dumps(fields['reasons'], ensure_ascii=False), 1 if fields['blank'] else 0, current['id']))
        return current['id']
    c.execute('''INSERT INTO name_formats(identity, customer_id, source_kind, format_name, placement, family, heading, note,
        layout, small, width_mm, height_mm, unknown_size, fixed_color, color_caption, groups_json, colors_json, swatches_json,
        departments_json, pending, needs_review, review_json, blank, manual, retired)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)''', (
        'manual:' + uuid.uuid4().hex, customer_id, -1, fields['format'], fields['placement'], 'HG丸ｺﾞｼｯｸM-PRO', fields['heading'], fields['note'],
        '', 1 if fields['small'] else 0, fields['width'], fields['height'], 1 if fields['unknown'] else 0, fields['fixed'], fields['caption'],
        json.dumps(fields['groups'], ensure_ascii=False), json.dumps(fields['colors'], ensure_ascii=False), json.dumps(swatches),
        json.dumps(fields['departments'], ensure_ascii=False), fields['pending'], review, json.dumps(fields['reasons'], ensure_ascii=False),
        1 if fields['blank'] else 0))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]

def _replace_image(c, format_id, payload, filename):
    rel = _store_upload(format_id, payload, filename)
    if not rel: return
    c.execute('DELETE FROM name_samples WHERE format_id=?', (format_id,))
    c.execute('''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
        VALUES(?,?,?,?,?,?)''', (format_id, 'upload', '', '', rel, '登録した見本画像です。書体の記載はHG丸ゴシック（太字）です。'))

def create(handler, c, d):
    blocked = _owner(handler.user)
    if blocked: return blocked
    fields = _fields(d)
    try: chosen = int(d['customerId']) if d.get('customerId') else None
    except (TypeError, ValueError): raise ValueError('顧客が見つかりません。')
    customer_id = _apply_customer(c, fields, chosen)
    format_id = _write_format(c, fields, customer_id)
    _replace_image(c, format_id, d.get('data'), d.get('filename'))
    return detail(handler, c, format_id)

def update(handler, c, d):
    blocked = _owner(handler.user)
    if blocked: return blocked
    try: format_id = int(d.get('id'))
    except (TypeError, ValueError): return 400, {'error': '形式が見つかりません。'}
    current = c.execute('SELECT * FROM name_formats WHERE id=? AND retired=0', (format_id,)).fetchone()
    if not current: return 404, {'error': '形式が見つかりません。'}
    fields = _fields(d)
    customer_id = _apply_customer(c, fields, current['customer_id'])
    _write_format(c, fields, customer_id, current)
    _replace_image(c, format_id, d.get('data'), d.get('filename'))
    return detail(handler, c, format_id)
