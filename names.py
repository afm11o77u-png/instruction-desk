import name_review
"""ネーム情報。

統合ネームラベルの登録を、形式ごとの見本つきで参照する。
元ソフトと元資料は変更しない。Excelの行番号は登録番号にしない。
"""
import hashlib, json, re, uuid, base64, shutil
from datetime import datetime
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
    if DATA_DIR is not None:
        correct_female_sample_names(c, DATA_DIR)

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

# 女性用見本の架空氏名（山田太郎 → 山田花子）。色名だけでは判定しない。
FEMALE_MARK = re.compile(r'女性用|女性専用')
MALE_OR_SHARED_MARK = re.compile(r'男性用|男性専用|男女共用|男女共通|男女兼用')
# 間隔つきを先に置換（半角・全角スペースを維持）
YAMADA_TARO_TO_HANAKO = (
    ('山田　太郎', '山田　花子'),
    ('山田 太郎', '山田 花子'),
    ('山田太郎', '山田花子'),
)
FEMALE_HANAKO_REPORT = 'female-sample-hanako-report.json'
FEMALE_IMAGE_SUFFIX = '-female-hanako'

def _has_female_mark(text):
    return bool(FEMALE_MARK.search(str(text or '')))

def _has_male_or_shared_mark(text):
    return bool(MALE_OR_SHARED_MARK.search(str(text or '')))

def replace_yamada_taro(text):
    """山田太郎系だけを花子へ。苗字のみの「山田」や他の氏名は変えない。"""
    value = str(text or '')
    if '山田太郎' not in value and '山田 太郎' not in value and '山田　太郎' not in value:
        return value
    out = value
    for old, new in YAMADA_TARO_TO_HANAKO:
        out = out.replace(old, new)
    return out

def _row_get(row, *keys):
    if isinstance(row, dict):
        for key in keys:
            if key in row and row[key] is not None:
                return row[key]
        return ''
    for key in keys:
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            continue
    return ''

def _sample_is_explicit_female(sample, fmt):
    """見本または形式に明示の「女性用／女性専用」があるときだけ対象。

    赤など色名だけでは女性用とみなさない。サンプル側に男性用・共通用があるときは除外。
    形式に男女が混在し、見本側に女性用の明示が無い場合は判別不能として除外。
    """
    sample_bits = [
        _row_get(sample, 'condition_label', 'condition'),
        _row_get(sample, 'paper_color', 'paperColor'),
        _row_get(sample, 'variant_key', 'key'),
    ]
    if any(_has_female_mark(bit) for bit in sample_bits):
        return True
    if any(_has_male_or_shared_mark(bit) for bit in sample_bits):
        return False

    groups = []
    raw_groups = _row_get(fmt, 'groups_json')
    if raw_groups:
        try:
            groups = json.loads(raw_groups) or []
        except (TypeError, json.JSONDecodeError):
            groups = []
    elif isinstance(fmt, dict) and fmt.get('groups'):
        groups = fmt.get('groups') or []

    format_bits = [
        _row_get(fmt, 'color_caption', 'colorCaption'),
        _row_get(fmt, 'format_name', 'format'),
        _row_get(fmt, 'note'),
        _row_get(fmt, 'fixed_color', 'fixedColor'),
        *groups,
    ]
    female = any(_has_female_mark(bit) for bit in format_bits)
    male_or_shared = any(_has_male_or_shared_mark(bit) for bit in format_bits)
    return bool(female and not male_or_shared)

def _female_image_rel(rel):
    path = Path(str(rel))
    if path.stem.endswith(FEMALE_IMAGE_SUFFIX):
        return str(path).replace('\\', '/')
    return str(path.with_name(path.stem + FEMALE_IMAGE_SUFFIX + path.suffix)).replace('\\', '/')

def _label_font_paths(family_hint=''):
    hint = str(family_hint or '')
    windir = Path(r'C:\Windows\Fonts')
    candidates = []
    if '丸' in hint or 'HG' in hint.upper() or 'ｺﾞｼｯｸ' in hint or 'ゴシック' in hint:
        candidates.extend([
            windir / 'HGRSMP.TTF', windir / 'HGRGM.TTC', windir / 'HGRMB.TTC',
            windir / 'HGRGE.TTC', windir / 'HGRME.TTC', windir / 'HGRPP1.TTC',
        ])
    candidates.extend([
        windir / 'msgothic.ttc', windir / 'meiryo.ttc', windir / 'YuGothM.ttc',
        windir / 'YuGothB.ttc', windir / 'BIZ-UDGothicM.ttc', windir / 'BIZ-UDGothicB.ttc',
        Path('/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'),
        Path('/usr/share/fonts/truetype/wqy/wqy-microhei.ttc'),
        Path('/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc'),
        Path('/System/Library/Fonts/Hiragino Sans GB.ttc'),
    ])
    return [path for path in candidates if path.is_file()]

def _load_font(size, family_hint=''):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for path in _label_font_paths(family_hint):
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return None

def _gray_bytes(im):
    return im.convert('L').tobytes(), im.size

def _match_template(haystack, needle, step=2):
    """Return (x, y, score) for best SAD match; lower score is better."""
    hw, hh = haystack.size
    nw, nh = needle.size
    if nw >= hw or nh >= hh or nw < 2 or nh < 2:
        return None
    h_bytes, _ = _gray_bytes(haystack)
    n_bytes, _ = _gray_bytes(needle)
    best = None
    for y in range(0, hh - nh + 1, step):
        for x in range(0, hw - nw + 1, step):
            total = 0
            for row in range(nh):
                h_off = (y + row) * hw + x
                n_off = row * nw
                for col in range(nw):
                    diff = h_bytes[h_off + col] - n_bytes[n_off + col]
                    total += diff if diff >= 0 else -diff
            if best is None or total < best[2]:
                best = (x, y, total)
    if best is None:
        return None
    # refine around best with step=1
    bx, by, _ = best
    refined = best
    for y in range(max(0, by - step), min(hh - nh + 1, by + step + 1)):
        for x in range(max(0, bx - step), min(hw - nw + 1, bx + step + 1)):
            total = 0
            for row in range(nh):
                h_off = (y + row) * hw + x
                n_off = row * nw
                for col in range(nw):
                    diff = h_bytes[h_off + col] - n_bytes[n_off + col]
                    total += diff if diff >= 0 else -diff
            if total < refined[2]:
                refined = (x, y, total)
    return refined

def _rewrite_name_pixels(src_path, dst_path, family_hint=''):
    """見本画像内の山田太郎系を山田花子系に描き換える。見つからなければ False。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False, 'Pillowがありません'
    try:
        image = Image.open(src_path).convert('RGB')
    except OSError as exc:
        return False, str(exc)
    # 背景色は四隅の最頻に近い色を使う
    corners = [image.getpixel((0, 0)), image.getpixel((image.width - 1, 0)),
               image.getpixel((0, image.height - 1)), image.getpixel((image.width - 1, image.height - 1))]
    bg = max(set(corners), key=corners.count)
    sizes = sorted({max(10, image.height // k) for k in (3, 4, 5, 6, 8, 10, 12)})
    replaced = 0
    work = image.copy()
    for old, new in YAMADA_TARO_TO_HANAKO:
        for size in sizes:
            font = _load_font(size, family_hint)
            if font is None:
                continue
            # measure
            probe = Image.new('L', (1, 1))
            draw = ImageDraw.Draw(probe)
            box = draw.textbbox((0, 0), old, font=font)
            tw, th = max(1, box[2] - box[0] + 4), max(1, box[3] - box[1] + 4)
            needle = Image.new('RGB', (tw, th), bg)
            ImageDraw.Draw(needle).text((2, 2), old, font=font, fill=(0, 0, 0))
            # skip nearly blank needles
            if min(needle.convert('L').getextrema()) > 250:
                continue
            match = _match_template(work, needle, step=2)
            if not match:
                continue
            x, y, score = match
            # accept loose threshold relative to needle area
            if score > tw * th * 45:
                continue
            # cover old text with background, draw new
            ImageDraw.Draw(work).rectangle([x, y, x + tw, y + th], fill=bg)
            ImageDraw.Draw(work).text((x + 2, y + 2), new, font=font, fill=(0, 0, 0))
            replaced += 1
            break
    if not replaced:
        # 太郎 → 花子（氏名の名側だけ）
        for size in sizes:
            font = _load_font(size, family_hint)
            if font is None:
                continue
            probe = Image.new('L', (1, 1))
            box = ImageDraw.Draw(probe).textbbox((0, 0), '太郎', font=font)
            tw, th = max(1, box[2] - box[0] + 4), max(1, box[3] - box[1] + 4)
            needle = Image.new('RGB', (tw, th), bg)
            ImageDraw.Draw(needle).text((2, 2), '太郎', font=font, fill=(0, 0, 0))
            match = _match_template(work, needle, step=2)
            if not match or match[2] > tw * th * 45:
                continue
            x, y, _ = match
            ImageDraw.Draw(work).rectangle([x, y, x + tw, y + th], fill=bg)
            ImageDraw.Draw(work).text((x + 2, y + 2), '花子', font=font, fill=(0, 0, 0))
            replaced += 1
            break
    if not replaced:
        return False, '画像内に山田太郎系の文字を検出できませんでした'
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = 'PNG' if dst_path.suffix.lower() == '.png' else 'JPEG' if dst_path.suffix.lower() in ('.jpg', '.jpeg') else 'WEBP'
    save_kw = {}
    if fmt == 'JPEG':
        save_kw['quality'] = 95
    work.save(dst_path, format=fmt, **save_kw)
    return True, ''

def _patch_female_catalog(data_dir, backup_dir, report_items):
    """catalog.json の女性用見本の caption / relativePath を画像・DBと揃える。"""
    path = Path(data_dir) / 'name-import' / 'catalog.json'
    if not path.is_file():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / 'catalog.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    changed = 0
    for fmt in data.get('formats') or []:
        for sample in fmt.get('samples') or []:
            if not _sample_is_explicit_female(sample, fmt):
                continue
            before_cap = sample.get('caption') or ''
            after_cap = replace_yamada_taro(before_cap)
            rel = str(sample.get('relativePath') or '')
            new_rel = rel
            if rel.startswith('images/names/'):
                # 他見本と共有なら fork 名へ
                shared = False
                for other_fmt in data.get('formats') or []:
                    for other in other_fmt.get('samples') or []:
                        if other is sample:
                            continue
                        if str(other.get('relativePath') or '') == rel and not _sample_is_explicit_female(other, other_fmt):
                            shared = True
                            break
                    if shared:
                        break
                if shared or FEMALE_IMAGE_SUFFIX not in Path(rel).stem:
                    # fork when shared; also when we rewrote image to female path
                    forked = _female_image_rel(rel)
                    if any(item.get('newRelativePath') == forked for item in report_items):
                        new_rel = forked
                    elif shared:
                        new_rel = forked
            if after_cap != before_cap or new_rel != rel:
                sample['caption'] = after_cap
                if new_rel:
                    sample['relativePath'] = new_rel
                changed += 1
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return changed

def correct_female_sample_names(c, data_dir=None):
    """明示的に女性用の見本だけ、架空氏名を山田花子へ（画像・caption・catalog）。

    - 色名だけでは対象にしない
    - 男性用・共通用・判別不能は変更しない
    - 共有画像は上書きせず -female-hanako を新規作成して参照だけ切替
    - 了承 digest は name_samples 更新トリガに任せ、勝手に了承しない
    """
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have_samples = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_samples'"
    ).fetchone()
    if not have_samples:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_samples未作成'}

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'female-sample-hanako-{stamp}'
    items = []
    unchanged_checks = []

    # path → list of sample ids using it
    path_users = {}
    for row in c.execute('SELECT id, relative_path FROM name_samples WHERE relative_path<>""'):
        path_users.setdefault(row['relative_path'], []).append(row['id'])

    rows = c.execute('''
        SELECT s.id AS sample_id, s.format_id, s.variant_key, s.condition_label, s.paper_color,
               s.relative_path, s.caption,
               f.format_name, f.color_caption, f.note, f.fixed_color, f.groups_json, f.family,
               cu.source_name
        FROM name_samples s
        JOIN name_formats f ON f.id=s.format_id
        JOIN name_customers cu ON cu.id=f.customer_id
        WHERE f.retired=0
        ORDER BY s.id
    ''').fetchall()

    prior = c.execute("SELECT value FROM settings WHERE key='female_sample_hanako'").fetchone()
    pending = False
    for row in rows:
        sample = {
            'condition_label': row['condition_label'],
            'paper_color': row['paper_color'],
            'variant_key': row['variant_key'],
        }
        fmt = {
            'format_name': row['format_name'],
            'color_caption': row['color_caption'],
            'note': row['note'],
            'fixed_color': row['fixed_color'],
            'groups_json': row['groups_json'],
        }
        if not _sample_is_explicit_female(sample, fmt):
            continue
        caption = row['caption'] or ''
        if replace_yamada_taro(caption) != caption:
            pending = True
            break
        rel = row['relative_path'] or ''
        if rel and FEMALE_IMAGE_SUFFIX not in Path(rel).stem and not prior:
            pending = True
            break
    if not pending:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': '適用済み' if prior else '対象なし'}

    for row in rows:
        sample = {
            'condition_label': row['condition_label'],
            'paper_color': row['paper_color'],
            'variant_key': row['variant_key'],
            'caption': row['caption'],
            'relative_path': row['relative_path'],
        }
        fmt = {
            'format_name': row['format_name'],
            'color_caption': row['color_caption'],
            'note': row['note'],
            'fixed_color': row['fixed_color'],
            'groups_json': row['groups_json'],
        }
        female = _sample_is_explicit_female(sample, fmt)
        new_caption = replace_yamada_taro(row['caption'] or '')
        needs_caption = new_caption != (row['caption'] or '')
        if not female:
            if needs_caption:
                unchanged_checks.append({
                    'sampleId': row['sample_id'], 'reason': '女性用ではないためcaptionも維持',
                    'customer': row['source_name'], 'format': row['format_name'],
                })
            continue
        already_forked = FEMALE_IMAGE_SUFFIX in Path(row['relative_path'] or '').stem
        # caption に山田太郎系がある、または未フォーク画像の初回検査
        if not needs_caption and (already_forked or not row['relative_path'] or prior):
            continue

        entry = {
            'sampleId': row['sample_id'],
            'formatId': row['format_id'],
            'customer': row['source_name'],
            'format': row['format_name'],
            'condition': row['condition_label'],
            'paperColor': row['paper_color'],
            'variantKey': row['variant_key'],
            'oldCaption': row['caption'],
            'newCaption': new_caption if needs_caption else row['caption'],
            'oldRelativePath': row['relative_path'],
            'newRelativePath': row['relative_path'],
            'imageAction': 'none',
            'imageNote': '',
        }

        new_rel = row['relative_path'] or ''
        image_ok = False
        if row['relative_path']:
            users = path_users.get(row['relative_path']) or [row['sample_id']]
            shared = any(sid != row['sample_id'] for sid in users)

            src = (root / row['relative_path']).resolve()
            names_root = (root / 'images' / 'names').resolve()
            if names_root in src.parents and src.is_file():
                backup_dir.mkdir(parents=True, exist_ok=True)
                dest_backup = backup_dir / 'images' / Path(row['relative_path']).name
                dest_backup.parent.mkdir(parents=True, exist_ok=True)
                if not dest_backup.exists():
                    shutil.copy2(src, dest_backup)
                candidate_rel = _female_image_rel(row['relative_path']) if (shared or not already_forked) else row['relative_path']
                dst = root / candidate_rel
                ok, note = _rewrite_name_pixels(src, dst, row['family'] or '')
                if ok:
                    image_ok = True
                    new_rel = candidate_rel
                    entry['imageAction'] = 'forked' if new_rel != row['relative_path'] else 'rewritten'
                    entry['imageNote'] = note
                elif needs_caption:
                    # caption は直すが、文字検出失敗時は共有なら fork コピー、専有なら元パス維持
                    if shared:
                        new_rel = candidate_rel
                        if not dst.is_file():
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, dst)
                        entry['imageAction'] = 'copy-only'
                    else:
                        entry['imageAction'] = 'unchanged'
                    entry['imageNote'] = note
                else:
                    # caption に太郎が無く、画像からも検出できない → 触らない
                    entry['imageAction'] = 'skipped'
                    entry['imageNote'] = note
                entry['newRelativePath'] = new_rel
            else:
                entry['imageAction'] = 'missing'
                entry['imageNote'] = '見本画像ファイルがありません'

        if needs_caption or (image_ok and entry['newRelativePath'] != (row['relative_path'] or '')):
            c.execute(
                'UPDATE name_samples SET caption=?, relative_path=? WHERE id=?',
                (entry['newCaption'], entry['newRelativePath'] or '', row['sample_id']),
            )
            items.append(entry)

    catalog_changed = _patch_female_catalog(root, backup_dir, items)
    report = {
        'at': stamp,
        'changed': len(items),
        'catalogSamplesPatched': catalog_changed,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'rule': {
            'include': ['女性用', '女性専用'],
            'exclude': ['男性用', '男性専用', '男女共用', '男女共通', '男女兼用', '色名のみ（例: 赤）', '判別不能'],
            'nameReplace': list(YAMADA_TARO_TO_HANAKO),
            'keepSurnameOnly': True,
        },
        'items': items,
        'nonFemaleCaptionLeftAlone': unchanged_checks[:50],
    }
    report_path = root / FEMALE_HANAKO_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if backup_dir.exists():
        (backup_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    c.execute(
        "INSERT OR REPLACE INTO settings VALUES('female_sample_hanako',?)",
        (json.dumps({'at': stamp, 'changed': len(items), 'report': FEMALE_HANAKO_REPORT}, ensure_ascii=False),),
    )
    return report

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
        correct_female_sample_names(c, data_dir)
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
