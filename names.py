import name_review
"""ネーム情報。

統合ネームラベルの登録を、形式ごとの見本つきで参照する。
元ソフトと元資料は変更しない。Excelの行番号は登録番号にしない。
"""
import hashlib, json, os, re, uuid, base64, shutil, sqlite3
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
    _ensure_soft_delete_columns(c)
    name_review.schema(c)
    correct_customer_names(c)
    # 女性見本の画像変換は起動・schema では行わない（DBロック／起動失敗の再発防止）。
    # 明示の保守 CLI（DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1）だけが実行する。
    if DATA_DIR is not None:
        correct_rinko_yellow(c, DATA_DIR)
        correct_rinko_name_formats(c, DATA_DIR)
        correct_ashuka_name_formats(c, DATA_DIR)
        ensure_three_customer_name_formats(c, DATA_DIR)
        ensure_fk_name_formats(c, DATA_DIR)
        ensure_benkama_name_formats(c, DATA_DIR)
        ensure_six_customers_name_formats(c, DATA_DIR)
        correct_moritan_solo_white(c, DATA_DIR)
        correct_fresh_legacy_formats(c, DATA_DIR)
        correct_takeda_duplicate_formats(c, DATA_DIR)
        ensure_asuka_bakery_name_formats(c, DATA_DIR)
        ensure_small_surname_36_exact_samples(c, DATA_DIR)

def _column(c, table, name, ddl):
    have = {row['name'] for row in c.execute(f'PRAGMA table_info({table})')}
    if name not in have:
        c.execute(f'ALTER TABLE {table} ADD COLUMN {ddl}')

# 利用者による論理削除（catalog の retired とは別）。ensure_* / import_catalog は retired のみ見る。
SOFT_DELETE_CUSTOMER_COLS = (
    ('deleted_at', 'deleted_at TEXT'),
    ('deleted_by', 'deleted_by INTEGER'),
    ('deleted_by_name', 'deleted_by_name TEXT NOT NULL DEFAULT \'\''),
)
SOFT_DELETE_FORMAT_COLS = SOFT_DELETE_CUSTOMER_COLS + (
    ('deleted_with_parent', 'deleted_with_parent INTEGER NOT NULL DEFAULT 0'),
)
# 通常一覧・詳細で「未削除」とみなす SQL 断片（retired とは独立）
NOT_SOFT_DELETED = "(deleted_at IS NULL OR deleted_at='')"
IS_SOFT_DELETED = "(deleted_at IS NOT NULL AND deleted_at<>'')"

def _soft_delete_columns_missing(c):
    cust = {row['name'] for row in c.execute('PRAGMA table_info(name_customers)')}
    fmt = {row['name'] for row in c.execute('PRAGMA table_info(name_formats)')}
    return any(name not in cust for name, _ in SOFT_DELETE_CUSTOMER_COLS) or any(
        name not in fmt for name, _ in SOFT_DELETE_FORMAT_COLS
    )

def _backup_before_soft_delete_migrate(c):
    """カラム追加前に instructions.sqlite3 を data/backups/pre-name-soft-delete-<stamp>/ へ退避。"""
    if DATA_DIR is None:
        return None
    db_path = Path(DATA_DIR) / 'instructions.sqlite3'
    if not db_path.is_file():
        return None
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = Path(DATA_DIR) / 'backups' / f'pre-name-soft-delete-{stamp}'
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / 'instructions.sqlite3'
    try:
        with sqlite3.connect(str(dest)) as dest_conn:
            c.backup(dest_conn)
        (backup_dir / 'README.txt').write_text(
            'ネーム情報の論理削除カラム追加前のバックアップです。\n'
            '既存データは ALTER TABLE で保持されます。万一戻す場合は、\n'
            'サーバ停止後にこの instructions.sqlite3 を data/ へ戻してください。\n'
            f'作成日時: {stamp}\n',
            encoding='utf-8',
        )
        return str(backup_dir.relative_to(Path(DATA_DIR))).replace('\\', '/')
    except (OSError, sqlite3.Error):
        return None

def _ensure_soft_delete_columns(c):
    """利用者論理削除用カラムを追加（既存行は NULL／0 のまま。retired は触らない）。"""
    if _soft_delete_columns_missing(c):
        _backup_before_soft_delete_migrate(c)
    for name, ddl in SOFT_DELETE_CUSTOMER_COLS:
        _column(c, 'name_customers', name, ddl)
    for name, ddl in SOFT_DELETE_FORMAT_COLS:
        _column(c, 'name_formats', name, ddl)

def _now():
    return datetime.now().isoformat(timespec='seconds')

def _row_deleted(row):
    if row is None:
        return False
    try:
        value = row['deleted_at']
    except (KeyError, IndexError):
        return False
    return bool(value)

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

# 女性区分の見本の架空氏名（山田太郎 → 山田花子）。色名だけでは判定しない。
# v1(#97)=女性用|女性専用 のみ → 条件「女性」だけの見本（例: NM-000115）を取りこぼした。
# v2=明示の性別区分「女性」も対象（女性用／女性専用を含む）。「男性」も除外側に追加。
# v3=起動時変換を廃止。DESK_RUN_FEMALE_IMAGE_MAINTENANCE の保守 CLI のみ。
#     画像生成中は DB 書き込みトランザクションを保持しない。完了見本は再変換しない。
# v4=画素テンプレ照合の主経路を廃止。登録 layout / 書体 / 寸法 / 最大フォントと
#     元画像の用紙色・キャンバスを再利用し、氏名のみレイアウトを「山田花子」で再生成。
#     再生成できない見本は推測上書きせず保留（画素マッチへフォールバックしない）。
# v5=DeptName / HeadingName / SplitNumber も氏名領域だけ差し替えて再生成。
#     部署・見出し・番号などは元キャンバスを保持。Surname（苗字のみ）は対象外（失敗に含めない）。
FEMALE_HANAKO_RULE_VERSION = 5
FEMALE_MAINTENANCE_ENV = 'DESK_RUN_FEMALE_IMAGE_MAINTENANCE'
FEMALE_MARK = re.compile(r'女性')
MALE_OR_SHARED_MARK = re.compile(r'男性|男女共用|男女共通|男女兼用')
# 間隔つきを先に置換（半角・全角スペースを維持）
YAMADA_TARO_TO_HANAKO = (
    ('山田　太郎', '山田　花子'),
    ('山田 太郎', '山田 花子'),
    ('山田太郎', '山田花子'),
)
# 氏名のみ: キャンバスを用紙色で塗り直し氏名を再描画
REGENERABLE_NAME_ONLY_LAYOUTS = frozenset({'Name'})
# 複合: 元画像を保持し氏名印字だけ差し替え（部署・見出し・番号等を残す）
REGENERABLE_COMPOSITE_LAYOUTS = frozenset({'DeptName', 'HeadingName', 'SplitNumber'})
REGENERABLE_NAME_LAYOUTS = REGENERABLE_NAME_ONLY_LAYOUTS | REGENERABLE_COMPOSITE_LAYOUTS
# 苗字のみは氏名置換の対象外（失敗件数に入れない）
SURNAME_EXCLUDED_LAYOUTS = frozenset({'Surname'})
FEMALE_HANAKO_REPORT = 'female-sample-hanako-report.json'
FEMALE_HANAKO_PROGRESS = 'female-sample-hanako-progress.json'
FEMALE_HANAKO_SETTINGS_KEY = 'female_sample_hanako'
FEMALE_IMAGE_SUFFIX = '-female-hanako'

def female_image_maintenance_allowed():
    """保守 CLI 用。通常起動では必ず False（単純解除しない）。"""
    value = str(os.environ.get(FEMALE_MAINTENANCE_ENV, '') or '').strip().lower()
    return value in ('1', 'true', 'yes', 'on')

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

def _female_hanako_prior(c):
    """settings の前回適用情報と ruleVersion を返す。(data_or_None, version)。"""
    row = c.execute(
        "SELECT value FROM settings WHERE key=?",
        (FEMALE_HANAKO_SETTINGS_KEY,),
    ).fetchone()
    if not row:
        return None, 0
    raw = row['value'] if hasattr(row, 'keys') else row[0]
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, json.JSONDecodeError):
        return {'raw': raw}, 1
    if not isinstance(data, dict):
        return {'raw': raw}, 1
    try:
        version = int(data.get('ruleVersion') or 1)
    except (TypeError, ValueError):
        version = 1
    return data, version

def _sample_is_explicit_female(sample, fmt):
    """見本または形式に明示の性別区分「女性」（女性用／女性専用を含む）があるときだけ対象。

    赤など色名だけでは女性とみなさない。サンプル側に男性・共通用があるときは除外。
    形式に男女が混在し、見本側に女性の明示が無い場合は判別不能として除外。
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

_FONT_CACHE = {}

def _load_font(size, family_hint=''):
    key = (int(size), str(family_hint or ''))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    try:
        from PIL import ImageFont
    except ImportError:
        _FONT_CACHE[key] = None
        return None
    font = None
    for path in _label_font_paths(family_hint):
        try:
            font = ImageFont.truetype(str(path), size=size)
            break
        except OSError:
            continue
    _FONT_CACHE[key] = font
    return font

try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _np = None
    _HAS_NUMPY = False

def _gray_bytes(im):
    return im.convert('L').tobytes(), im.size

def _sad_at_bytes(h_bytes, n_bytes, hw, nw, nh, x, y, limit=None):
    """純Pythonの1位置 SAD。limit 以上になったら打ち切って limit を返す。"""
    total = 0
    for row in range(nh):
        h_off = (y + row) * hw + x
        n_off = row * nw
        for col in range(nw):
            diff = h_bytes[h_off + col] - n_bytes[n_off + col]
            total += diff if diff >= 0 else -diff
        if limit is not None and total >= limit:
            return total
    return total

def _match_template_numpy(h_arr, n_arr, step=2, max_score=None):
    """numpy 配列での粗探索＋近傍 refine。戻り値 (x, y, score)。"""
    hh, hw = h_arr.shape
    nh, nw = n_arr.shape
    ys = range(0, hh - nh + 1, step)
    xs = range(0, hw - nw + 1, step)
    best = None
    best_score = None
    for y in ys:
        row_slice = h_arr[y:y + nh]
        for x in xs:
            patch = row_slice[:, x:x + nw]
            total = int(_np.abs(patch - n_arr).sum())
            if best_score is None or total < best_score:
                best_score = total
                best = (x, y, total)
                if total == 0:
                    return best
    if best is None:
        return None
    # 粗探索の最良でも閾値超えなら refine 不要（誤サイズ試行の高速化）
    if max_score is not None and best[2] >= max_score:
        return best
    bx, by, _ = best
    refined = best
    y0 = max(0, by - step)
    y1 = min(hh - nh + 1, by + step + 1)
    x0 = max(0, bx - step)
    x1 = min(hw - nw + 1, bx + step + 1)
    for y in range(y0, y1):
        row_slice = h_arr[y:y + nh]
        for x in range(x0, x1):
            patch = row_slice[:, x:x + nw]
            total = int(_np.abs(patch - n_arr).sum())
            if total < refined[2]:
                refined = (x, y, total)
                if total == 0:
                    return refined
    return refined

def _match_template_bytes(h_bytes, n_bytes, hw, hh, nw, nh, step=2, max_score=None):
    """バイト列での粗探索（早期打ち切り）＋近傍 refine。"""
    # 大きい針は粗ステップを広げ、その後 step 近傍〜1px へ絞る
    coarse = max(step, min(8, max(2, nw // 16), max(2, nh // 12)))
    best = None
    for y in range(0, hh - nh + 1, coarse):
        for x in range(0, hw - nw + 1, coarse):
            limit = None if best is None else best[2]
            if max_score is not None and (limit is None or max_score < limit):
                limit = int(max_score) if best is None else min(limit, int(max_score))
            total = _sad_at_bytes(h_bytes, n_bytes, hw, nw, nh, x, y, limit=limit)
            if best is None or total < best[2]:
                best = (x, y, total)
                if total == 0:
                    return best
    if best is None:
        return None
    if max_score is not None and best[2] >= max_score:
        return best
    # coarse → step、さらに step → 1 へ段階 refine
    for radius, refine_step in ((coarse, step), (max(step, 1), 1)):
        bx, by, _ = best
        refined = best
        for y in range(max(0, by - radius), min(hh - nh + 1, by + radius + 1), refine_step):
            for x in range(max(0, bx - radius), min(hw - nw + 1, bx + radius + 1), refine_step):
                total = _sad_at_bytes(
                    h_bytes, n_bytes, hw, nw, nh, x, y, limit=refined[2],
                )
                if total < refined[2]:
                    refined = (x, y, total)
                    if total == 0:
                        return refined
        best = refined
    return best

def _match_template(haystack, needle, step=2, max_score=None):
    """Return (x, y, score) for best SAD match; lower score is better.

    numpy があれば配列演算、無ければバイト操作＋早期打ち切り＋粗ステップ。
    max_score を渡すと粗探索の最良がそれ以上なら refine を省略する。
    """
    hw, hh = haystack.size
    nw, nh = needle.size
    if nw >= hw or nh >= hh or nw < 2 or nh < 2:
        return None
    if _HAS_NUMPY:
        h_arr = _np.asarray(haystack.convert('L'), dtype=_np.int16)
        n_arr = _np.asarray(needle.convert('L'), dtype=_np.int16)
        return _match_template_numpy(h_arr, n_arr, step=step, max_score=max_score)
    h_bytes, _ = _gray_bytes(haystack)
    n_bytes, _ = _gray_bytes(needle)
    return _match_template_bytes(
        h_bytes, n_bytes, hw, hh, nw, nh, step=step, max_score=max_score,
    )

def _ink_mass(needle_rgb, bg):
    """針画像のうち背景より明らかに暗い画素のインク量。"""
    bg_l = (bg[0] + bg[1] + bg[2]) // 3 if isinstance(bg, tuple) else int(bg)
    gray = needle_rgb.convert('L')
    if _HAS_NUMPY:
        arr = _np.asarray(gray, dtype=_np.int32)
        mask = arr < (bg_l - 25)
        if not mask.any():
            return 0
        return int((bg_l - arr[mask]).sum())
    pixels = gray.get_flattened_data() if hasattr(gray, 'get_flattened_data') else gray.getdata()
    total = 0
    for pixel in pixels:
        if pixel < bg_l - 25:
            total += bg_l - pixel
    return total

def _font_sizes_for_image(height):
    """見本高さに対し実フォントサイズを細かく試す（レガシー画素照合用・主経路では使わない）。"""
    hi = max(12, int(height))
    ratios = (0.12, 0.15, 0.18, 0.2, 0.22, 0.25, 0.28, 0.3, 0.33, 0.35, 0.38, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.8)
    sizes = {max(10, int(height * ratio)) for ratio in ratios}
    sizes.update(range(10, min(hi, 48) + 1, 2))
    prefer = max(10, int(height * 0.28))
    return sorted(sizes, key=lambda size: (abs(size - prefer), size))

def _yamada_hanako_label(caption=''):
    """画像に描く氏名。caption の間隔（半角／全角／無し）を優先する。"""
    text = str(caption or '')
    for old, new in YAMADA_TARO_TO_HANAKO:
        if old in text:
            return new
    return '山田 花子'

def _corner_background(image):
    corners = [
        image.getpixel((0, 0)),
        image.getpixel((image.width - 1, 0)),
        image.getpixel((0, image.height - 1)),
        image.getpixel((image.width - 1, image.height - 1)),
    ]
    return max(set(corners), key=corners.count)

def _ink_bbox(image, bg, margin=2):
    """背景より暗い印字領域の外接矩形。無ければ None。"""
    bg_l = (bg[0] + bg[1] + bg[2]) // 3 if isinstance(bg, tuple) else int(bg)
    gray = image.convert('L')
    if _HAS_NUMPY:
        arr = _np.asarray(gray, dtype=_np.int16)
        mask = arr < (bg_l - 25)
        if not mask.any():
            return None
        ys, xs = _np.where(mask)
        x0 = max(0, int(xs.min()) - margin)
        y0 = max(0, int(ys.min()) - margin)
        x1 = min(image.width - 1, int(xs.max()) + margin)
        y1 = min(image.height - 1, int(ys.max()) + margin)
        return (x0, y0, x1, y1)
    pixels = gray.load()
    x0 = y0 = None
    x1 = y1 = 0
    for y in range(image.height):
        for x in range(image.width):
            if pixels[x, y] < bg_l - 25:
                if x0 is None:
                    x0, y0 = x, y
                    x1, y1 = x, y
                else:
                    if x < x0:
                        x0 = x
                    if y < y0:
                        y0 = y
                    if x > x1:
                        x1 = x
                    if y > y1:
                        y1 = y
    if x0 is None:
        return None
    return (
        max(0, x0 - margin),
        max(0, y0 - margin),
        min(image.width - 1, x1 + margin),
        min(image.height - 1, y1 + margin),
    )

def _font_px_from_meta(image, width_mm, height_mm, maximum_font_pt, ink_box):
    """登録の最大 pt または既存印字高さから描画サイズを決める。"""
    try:
        pt = float(maximum_font_pt or 0)
    except (TypeError, ValueError):
        pt = 0
    w_mm = float(width_mm or 0)
    h_mm = float(height_mm or 0)
    if pt > 0 and w_mm > 0 and h_mm > 0 and image.width > 0 and image.height > 0:
        dpi = image.height / (h_mm / 25.4)
        size = int(round(pt * dpi / 72.0))
        if size >= 8:
            return max(8, size)
    if ink_box:
        return max(8, int((ink_box[3] - ink_box[1]) * 0.92))
    return max(10, int(image.height * 0.28))

def _fit_font_to_box(text, family_hint, target_w, target_h, prefer_size, *, allow_grow=True):
    """指定矩形に収まるフォントを探す。

    allow_grow=True（既定）: prefer 以上にも拡大して枠を埋める（大汎用・小汎用など）。
    allow_grow=False: 登録 pt 由来の prefer を上限にし、溢れるときだけ縮小する。
    """
    if target_w < 4 or target_h < 4:
        return None, None
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None, None
    prefer = max(8, int(prefer_size or 8))
    if allow_grow:
        hi = max(8, int(max(prefer * 1.35, target_h)))
    else:
        hi = prefer
    lo = 8
    best = None
    best_font = None
    probe = Image.new('L', (1, 1))
    draw = ImageDraw.Draw(probe)
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _load_font(mid, family_hint)
        if font is None:
            return None, None
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        if tw <= target_w and th <= target_h:
            best, best_font = mid, font
            lo = mid + 1
        else:
            hi = mid - 1
    return best_font, best


def _family_is_maru_or_p_gothic(family_hint):
    """丸ゴシック36 / Pゴシック36 系の書体か。"""
    text = str(family_hint or '')
    if '丸ゴシック' in text:
        return True
    if 'Pゴシック' in text or 'Ｐゴシック' in text:
        return True
    if 'MSP' in text.upper() or 'MS P' in text.upper() or 'ＭＳＰ' in text:
        return True
    return False


def _is_small_surname_36_format(fmt_or_def):
    """小ネーム・苗字のみ・36pt・丸/Pゴシック系か（見本を exact-pt 描画する対象）。"""
    small = int(_row_get(fmt_or_def, 'small', 'small') or 0)
    layout = str(_row_get(fmt_or_def, 'layout', 'layout') or '')
    reading = str(_row_get(fmt_or_def, 'reading', 'reading') or '')
    family = str(_row_get(fmt_or_def, 'family', 'family') or '')
    try:
        maximum = float(
            _row_get(fmt_or_def, 'maximum_font', 'maximumFont')
            or _row_get(fmt_or_def, 'fontPt', 'font_pt')
            or 0
        )
    except (TypeError, ValueError):
        maximum = 0
    if small != 1:
        return False
    if layout != 'Surname' and reading != '苗字のみ':
        return False
    if abs(maximum - 36.0) > 0.01:
        return False
    return _family_is_maru_or_p_gothic(family)


def _sample_fit_mode_for_format(fmt_or_def, *, font_pt=0):
    """帯描画のフィット方針。36pt 小ネーム苗字のみは登録 pt 厳守。"""
    probe = dict(fmt_or_def) if isinstance(fmt_or_def, dict) else {
        'small': _row_get(fmt_or_def, 'small'),
        'layout': _row_get(fmt_or_def, 'layout'),
        'reading': _row_get(fmt_or_def, 'reading'),
        'family': _row_get(fmt_or_def, 'family'),
        'maximumFont': _row_get(fmt_or_def, 'maximum_font', 'maximumFont'),
        'fontPt': font_pt or _row_get(fmt_or_def, 'fontPt', 'font_pt'),
    }
    if font_pt:
        probe['fontPt'] = font_pt
        probe['maximumFont'] = probe.get('maximumFont') or font_pt
    return 'exact' if _is_small_surname_36_format(probe) else 'fill'

def _regeneration_hold_reason(candidate):
    """再生成できないときの保留理由。閾値緩和や推測描き直しへは落とさない。"""
    layout = str(candidate.get('layout') or '').strip()
    if candidate.get('blank'):
        return '無地見本のため氏名再生成の対象外'
    if layout in SURNAME_EXCLUDED_LAYOUTS:
        return '苗字のみ（layout=Surname）は対象外'
    if not layout:
        return 'layout未設定のため再生成に必要な配置情報が不足（推測上書きしない）'
    if layout not in REGENERABLE_NAME_LAYOUTS:
        label = LAYOUTS.get(layout, layout)
        return (
            f'layout={layout}（{label}）は対応する再生成手順が未実装のため保留'
            '（QR・役職・複数行氏名等を推測再生成しない）'
        )
    return ''

def _composite_font_sizes(image, width_mm, height_mm, maximum_font, ink_box):
    """複合レイアウト用。メタ由来サイズを優先し、近傍と画像高さ比率を試す。"""
    prefer = _font_px_from_meta(image, width_mm, height_mm, maximum_font, ink_box)
    sizes = {max(8, prefer)}
    for delta in range(1, 12):
        sizes.add(max(8, prefer - delta))
        sizes.add(prefer + delta)
    # 画像高さ比率（氏名行の一般的なサイズ帯）。閾値は緩めない。
    for ratio in (0.12, 0.15, 0.18, 0.2, 0.22, 0.25, 0.28, 0.3, 0.33, 0.35, 0.4):
        sizes.add(max(8, int(image.height * ratio)))
    return sorted(sizes, key=lambda size: (abs(size - prefer), size))

def _regenerate_name_sample(
    src_path, dst_path, *, name_text, family_hint='', maximum_font=0,
    width_mm=0, height_mm=0, progress_callback=None,
):
    """氏名のみ見本を登録メタ＋元キャンバス／用紙色で再生成する。

    元ソフトの生成器はリポジトリに無いため、catalog 由来の layout=Name・書体・
    寸法・最大フォントと、元画像の画素寸法・四隅用紙色・既存印字位置を再利用する。
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False, 'Pillowがありません'
    try:
        image = Image.open(src_path).convert('RGB')
    except OSError as exc:
        return False, str(exc)
    if progress_callback:
        progress_callback({
            'phase': 'image-try',
            'method': 'regenerate',
            'variant': name_text,
            'variantIndex': 1,
            'variantTotal': 1,
            'size': 0,
            'sizeIndex': 1,
            'sizeTotal': 1,
            'width': image.width,
            'height': image.height,
        })
    bg = _corner_background(image)
    ink_box = _ink_bbox(image, bg)
    if not ink_box:
        return False, '印字領域を特定できないため再生成保留（空画像または検出失敗）'
    ink_w = ink_box[2] - ink_box[0] + 1
    ink_h = ink_box[3] - ink_box[1] + 1
    if ink_w < 8 or ink_h < 8:
        return False, '印字領域が小さすぎるため再生成保留'
    # 氏名レイアウト想定: 極端に縦長な印字塊（バーコード帯など）は保留
    if ink_h > ink_w * 1.35 and ink_h > image.height * 0.55:
        return False, '印字領域の形状が氏名のみ想定と異なるため再生成保留'
    prefer = _font_px_from_meta(image, width_mm, height_mm, maximum_font, ink_box)
    # 既存印字枠に収まるようフィット（配置・文字サイズを維持）
    pad_w = max(ink_w, int(image.width * 0.7))
    pad_h = max(ink_h, int(image.height * 0.35))
    font, used_size = _fit_font_to_box(
        name_text, family_hint, pad_w, pad_h, prefer,
    )
    if font is None:
        return False, '利用可能なフォントがありません（Windows のゴシック系／HG丸ゴシックを優先）'
    if progress_callback:
        progress_callback({
            'phase': 'image-try',
            'method': 'regenerate',
            'variant': name_text,
            'variantIndex': 1,
            'variantTotal': 1,
            'size': used_size or prefer,
            'sizeIndex': 1,
            'sizeTotal': 1,
            'width': image.width,
            'height': image.height,
        })
    work = Image.new('RGB', image.size, bg)
    draw = ImageDraw.Draw(work)
    box = draw.textbbox((0, 0), name_text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    cx = (ink_box[0] + ink_box[2]) / 2
    cy = (ink_box[1] + ink_box[3]) / 2
    x = int(round(cx - tw / 2 - box[0]))
    y = int(round(cy - th / 2 - box[1]))
    draw.text((x, y), name_text, font=font, fill=(0, 0, 0))
    if work.tobytes() == image.tobytes():
        return False, '再生成結果が元画像と同じでした'
    new_ink = _ink_bbox(work, bg)
    if not new_ink:
        return False, '再生成後に印字がありません'
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = 'PNG' if dst_path.suffix.lower() == '.png' else 'JPEG' if dst_path.suffix.lower() in ('.jpg', '.jpeg') else 'WEBP'
    save_kw = {}
    if fmt == 'JPEG':
        save_kw['quality'] = 95
    work.save(dst_path, format=fmt, **save_kw)
    return True, f'layout=Name を氏名 {name_text} で再生成 (fontPx={used_size or prefer})'

def _regenerate_composite_name_sample(
    src_path, dst_path, *, name_text, family_hint='', maximum_font=0,
    width_mm=0, height_mm=0, layout='', progress_callback=None,
):
    """部署・見出し・番号付き見本で、氏名印字だけを山田花子に差し替える。

    元キャンバスを複製し、山田太郎系の印字位置を登録フォント情報＋テンプレ照合で
    特定して同位置へ花子を描く。部署名・見出し・番号・バーコード・罫線は元のまま。
    照合閾値は緩和しない。検出できない場合は氏名のみ画像への作り直しをせず失敗。
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False, 'Pillowがありません'
    try:
        image = Image.open(src_path).convert('RGB')
    except OSError as exc:
        return False, str(exc)
    bg = _corner_background(image)
    ink_box = _ink_bbox(image, bg)
    if not ink_box:
        return False, '印字領域を特定できないため複合レイアウト再生成を保留'
    sizes = _composite_font_sizes(image, width_mm, height_mm, maximum_font, ink_box)
    work = image.copy()
    layout_code = str(layout or '').strip() or 'composite'
    last_note = '画像内に山田太郎系の氏名印字を検出できないため保留（推測描き直ししない）'
    # name_text に対応する太郎形を先に試し、続けて全バリエーション
    preferred = []
    for old, new in YAMADA_TARO_TO_HANAKO:
        if str(name_text or '') == new:
            preferred.append((old, new))
    image_variants = preferred + [pair for pair in YAMADA_TARO_TO_HANAKO if pair not in preferred]
    variant_total = len(image_variants)
    replaced = 0
    used_size = 0
    used_old = ''
    for variant_index, (old, new) in enumerate(image_variants, start=1):
        size_total = len(sizes)
        for size_index, size in enumerate(sizes, start=1):
            if progress_callback:
                progress_callback({
                    'phase': 'image-try',
                    'method': 'regenerate-composite',
                    'variant': old,
                    'variantIndex': variant_index,
                    'variantTotal': variant_total,
                    'size': size,
                    'sizeIndex': size_index,
                    'sizeTotal': size_total,
                    'width': work.width,
                    'height': work.height,
                    'layout': layout_code,
                })
            font = _load_font(size, family_hint)
            if font is None:
                last_note = '利用可能なフォントがありません（Windows のゴシック系／HG丸ゴシックを優先）'
                continue
            probe = Image.new('L', (1, 1))
            box = ImageDraw.Draw(probe).textbbox((0, 0), old, font=font)
            tw, th = max(1, box[2] - box[0] + 4), max(1, box[3] - box[1] + 4)
            if tw >= work.width or th >= work.height:
                continue
            needle = Image.new('RGB', (tw, th), bg)
            ImageDraw.Draw(needle).text((2, 2), old, font=font, fill=(0, 0, 0))
            ink = _ink_mass(needle, bg)
            if ink < 400:
                continue
            # 閾値は従来どおり（緩和禁止）
            threshold = ink * 0.55
            match = _match_template(work, needle, step=2, max_score=threshold)
            if not match:
                continue
            x, y, score = match
            if score >= threshold:
                continue
            ImageDraw.Draw(work).rectangle([x, y, x + tw, y + th], fill=bg)
            ImageDraw.Draw(work).text((x + 2, y + 2), new, font=font, fill=(0, 0, 0))
            replaced += 1
            used_size = size
            used_old = old
            break
        if replaced:
            break
    if not replaced:
        return False, last_note
    if work.tobytes() == image.tobytes():
        return False, '再生成結果が元画像と同じでした'
    # 氏名以外が大きく消えていないか（元画像の大半が残っていること）
    if _HAS_NUMPY:
        diff = _np.abs(
            _np.asarray(work, dtype=_np.int16) - _np.asarray(image, dtype=_np.int16)
        ).sum(axis=2)
        changed_px = int((diff > 12).sum())
    else:
        changed_px = 0
        a = image.load()
        b = work.load()
        for yy in range(image.height):
            for xx in range(image.width):
                pa, pb = a[xx, yy], b[xx, yy]
                if abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2]) > 36:
                    changed_px += 1
    max_changed = max(800, int(image.width * image.height * 0.22))
    if changed_px > max_changed:
        return False, (
            f'氏名以外の領域まで変化したため保留（changedPx={changed_px}）。'
            '複合見本を氏名のみ画像に作り直さない'
        )
    new_ink = _ink_bbox(work, bg)
    if not new_ink:
        return False, '再生成後に印字がありません'
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = 'PNG' if dst_path.suffix.lower() == '.png' else 'JPEG' if dst_path.suffix.lower() in ('.jpg', '.jpeg') else 'WEBP'
    save_kw = {}
    if fmt == 'JPEG':
        save_kw['quality'] = 95
    work.save(dst_path, format=fmt, **save_kw)
    return True, (
        f'layout={layout_code} の氏名領域を {used_old}→{name_text} で再生成 '
        f'(fontPx={used_size}, changedPx={changed_px})'
    )

def _rewrite_name_pixels(src_path, dst_path, family_hint='', progress_callback=None):
    """レガシー: 画素テンプレ照合による描き換え（主経路では呼ばない）。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False, 'Pillowがありません'
    try:
        image = Image.open(src_path).convert('RGB')
    except OSError as exc:
        return False, str(exc)
    bg = _corner_background(image)
    sizes = _font_sizes_for_image(image.height)
    replaced = 0
    work = image.copy()
    last_note = '画像内に山田太郎系の文字を検出できませんでした'
    image_variants = (
        ('山田 太郎', '山田 花子'),
        ('山田太郎', '山田花子'),
        ('山田　太郎', '山田　花子'),
    )
    variant_total = len(image_variants)
    for variant_index, (old, new) in enumerate(image_variants, start=1):
        size_total = len(sizes)
        for size_index, size in enumerate(sizes, start=1):
            if progress_callback:
                progress_callback({
                    'phase': 'image-try',
                    'variant': old,
                    'variantIndex': variant_index,
                    'variantTotal': variant_total,
                    'size': size,
                    'sizeIndex': size_index,
                    'sizeTotal': size_total,
                    'width': work.width,
                    'height': work.height,
                })
            font = _load_font(size, family_hint)
            if font is None:
                last_note = '利用可能なフォントがありません（Windows のゴシック系を優先）'
                continue
            probe = Image.new('L', (1, 1))
            box = ImageDraw.Draw(probe).textbbox((0, 0), old, font=font)
            tw, th = max(1, box[2] - box[0] + 4), max(1, box[3] - box[1] + 4)
            if tw >= work.width or th >= work.height:
                continue
            needle = Image.new('RGB', (tw, th), bg)
            ImageDraw.Draw(needle).text((2, 2), old, font=font, fill=(0, 0, 0))
            ink = _ink_mass(needle, bg)
            if ink < 400:
                continue
            threshold = ink * 0.55
            match = _match_template(work, needle, step=2, max_score=threshold)
            if not match:
                continue
            x, y, score = match
            if score >= threshold:
                continue
            ImageDraw.Draw(work).rectangle([x, y, x + tw, y + th], fill=bg)
            ImageDraw.Draw(work).text((x + 2, y + 2), new, font=font, fill=(0, 0, 0))
            replaced += 1
            break
        if replaced:
            break
    if not replaced:
        return False, last_note
    if work.tobytes() == image.tobytes():
        return False, '描き換え結果が元画像と同じでした'
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

def _row_as_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}

def _female_completed_ids(prior):
    if not isinstance(prior, dict):
        return set()
    raw = prior.get('completedSampleIds') or []
    out = set()
    for value in raw:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out

def _load_female_hanako_rows(c):
    have_samples = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_samples'"
    ).fetchone()
    if not have_samples:
        return None, {}, None, 0
    path_users = {}
    for row in c.execute('SELECT id, relative_path FROM name_samples WHERE relative_path<>""'):
        path_users.setdefault(row['relative_path'], []).append(int(row['id']))
    rows = [
        _row_as_dict(row)
        for row in c.execute('''
            SELECT s.id AS sample_id, s.format_id, s.variant_key, s.condition_label, s.paper_color,
                   s.relative_path, s.caption,
                   f.format_name, f.color_caption, f.note, f.fixed_color, f.groups_json, f.family,
                   f.layout, f.width_mm, f.height_mm, f.maximum_font, f.blank,
                   cu.source_name, f.identity AS format_identity
            FROM name_samples s
            JOIN name_formats f ON f.id=s.format_id
            JOIN name_customers cu ON cu.id=f.customer_id
            WHERE f.retired=0
            ORDER BY s.id
        ''')
    ]
    prior, prior_version = _female_hanako_prior(c)
    return rows, path_users, prior, prior_version

def _caption_has_yamada_hanako(caption):
    text = str(caption or '')
    return ('山田花子' in text) or ('山田 花子' in text) or ('山田　花子' in text)

def _female_candidate_from_row(row, path_users, completed_ids):
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
    caption = row['caption'] or ''
    new_caption = replace_yamada_taro(caption)
    needs_caption = new_caption != caption
    has_hanako = _caption_has_yamada_hanako(caption)
    rel = row['relative_path'] or ''
    already_forked = FEMALE_IMAGE_SUFFIX in Path(rel).stem if rel else False
    sample_id = int(row['sample_id'])
    layout = str(row.get('layout') or '').strip()
    base = {
        'sampleId': sample_id,
        'formatId': row['format_id'],
        'formatIdentity': row.get('format_identity') or '',
        'customer': row['source_name'],
        'format': row['format_name'],
        'condition': row['condition_label'],
        'paperColor': row['paper_color'],
        'variantKey': row['variant_key'],
        'family': row.get('family') or '',
        'layout': layout,
        'widthMm': row.get('width_mm') or 0,
        'heightMm': row.get('height_mm') or 0,
        'maximumFont': row.get('maximum_font') or 0,
        'blank': bool(row.get('blank') or 0),
        'oldCaption': caption,
        'newCaption': new_caption if needs_caption else caption,
        'oldRelativePath': rel,
        'newRelativePath': rel,
        'needsCaption': needs_caption,
        'alreadyForked': already_forked,
        'shared': False,
        'hasImagePath': bool(rel),
        'imageName': _yamada_hanako_label(caption),
    }
    if not female:
        if needs_caption:
            base['status'] = 'excluded'
            base['reason'] = '女性区分ではないためcaptionも維持'
            base['excludedKind'] = 'non-female'
            return base
        return None
    # 苗字のみ layout は氏名置換の対象外（失敗件数に含めない）
    if layout in SURNAME_EXCLUDED_LAYOUTS:
        base['status'] = 'excluded'
        base['reason'] = '苗字のみ（layout=Surname / 「山田」のみ）は対象外（山田のまま）'
        base['excludedKind'] = 'surname'
        base['needsCaption'] = False
        base['newCaption'] = caption
        return base
    if sample_id in completed_ids and already_forked and not needs_caption:
        base['status'] = 'already-complete'
        base['reason'] = '完了済みのため再変換しない'
        return base
    if sample_id in completed_ids and not rel and not needs_caption:
        base['status'] = 'already-complete'
        base['reason'] = '完了済みのため再変換しない'
        return base
    if not needs_caption and already_forked:
        base['status'] = 'already-complete'
        base['reason'] = '既に花子用画像を参照済み'
        return base
    if not needs_caption and not rel:
        base['status'] = 'noop'
        base['reason'] = '画像なし・caption変更なし'
        return base
    # caption に太郎も花子も無い → 利用者が別文言へ編集した可能性。触らない。
    if not needs_caption and not has_hanako:
        base['status'] = 'noop'
        base['reason'] = '山田太郎／山田花子系のcaptionではないため対象外'
        return base
    # 花子captionだが未フォーク画像 → 過去の不完全適用の再検査
    users = path_users.get(rel) or [sample_id]
    base['shared'] = any(int(sid) != sample_id for sid in users)
    base['status'] = 'pending'
    return base

def _prepare_female_image(candidate, root, backup_dir, progress_callback=None):
    """DB 非接続で画像を用意する。成功時だけ newRelativePath を返す。

    Name は氏名のみ再描画。DeptName / HeadingName / SplitNumber は元画像を保持して
    氏名領域だけ差し替え。再生成不可は保留（閾値緩和や氏名のみ画像への作り直しはしない）。
    """
    rel = candidate['oldRelativePath'] or ''
    entry = dict(candidate)
    entry['imageAction'] = 'none'
    entry['imageNote'] = ''
    layout = str(candidate.get('layout') or '').strip()
    if layout in SURNAME_EXCLUDED_LAYOUTS:
        entry['status'] = 'excluded'
        entry['reason'] = '苗字のみ（layout=Surname）は対象外（山田のまま）'
        entry['excludedKind'] = 'surname'
        entry['imageAction'] = 'excluded-surname'
        return entry
    if not rel:
        entry['status'] = 'ready-caption-only'
        return entry
    src = (root / rel).resolve()
    names_root = (root / 'images' / 'names').resolve()
    if names_root not in src.parents or not src.is_file():
        entry['status'] = 'failed'
        entry['imageAction'] = 'missing'
        entry['imageNote'] = '見本画像ファイルがありません'
        entry['reason'] = entry['imageNote']
        return entry
    hold = _regeneration_hold_reason(candidate)
    if hold:
        entry['status'] = 'failed'
        entry['imageAction'] = 'held'
        entry['imageNote'] = hold
        entry['reason'] = hold
        entry['newRelativePath'] = rel
        return entry
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest_backup = backup_dir / 'images' / Path(rel).name
    dest_backup.parent.mkdir(parents=True, exist_ok=True)
    if not dest_backup.exists():
        shutil.copy2(src, dest_backup)
    already_forked = candidate.get('alreadyForked')
    shared = candidate.get('shared')
    candidate_rel = _female_image_rel(rel) if (shared or not already_forked) else rel
    dst = root / candidate_rel
    name_text = candidate.get('imageName') or _yamada_hanako_label(candidate.get('oldCaption') or '')
    common = dict(
        name_text=name_text,
        family_hint=candidate.get('family') or '',
        maximum_font=candidate.get('maximumFont') or 0,
        width_mm=candidate.get('widthMm') or 0,
        height_mm=candidate.get('heightMm') or 0,
        progress_callback=progress_callback,
    )
    if layout in REGENERABLE_COMPOSITE_LAYOUTS:
        ok, note = _regenerate_composite_name_sample(
            src, dst, layout=layout, **common,
        )
        success_note = note or f'layout={layout} の氏名領域を山田花子で再生成'
    else:
        ok, note = _regenerate_name_sample(src, dst, **common)
        success_note = note or '氏名のみ見本を山田花子で再生成'
    if ok:
        entry['newRelativePath'] = candidate_rel
        entry['imageAction'] = 'forked-regenerated' if candidate_rel != rel else 'regenerated'
        entry['imageNote'] = success_note
        entry['status'] = 'ready'
        return entry
    # 再生成失敗 → caption だけの「完了」にしない。元のまま・再実行可。閾値緩和しない。
    entry['status'] = 'failed'
    entry['imageAction'] = 'regenerate-failed'
    entry['imageNote'] = note or '再生成に失敗したため保留'
    entry['reason'] = entry['imageNote']
    entry['newRelativePath'] = rel
    return entry

def _commit_female_sample(c, prepared):
    """短いトランザクション前提。更新直前に元登録が変わっていないか確認する。"""
    sample_id = int(prepared['sampleId'])
    row = c.execute(
        'SELECT caption, relative_path FROM name_samples WHERE id=?',
        (sample_id,),
    ).fetchone()
    if not row:
        prepared = dict(prepared)
        prepared['status'] = 'failed'
        prepared['reason'] = '見本が削除されています'
        return prepared
    current_caption = row['caption'] or ''
    current_rel = row['relative_path'] or ''
    if current_caption != (prepared.get('oldCaption') or '') or current_rel != (prepared.get('oldRelativePath') or ''):
        prepared = dict(prepared)
        prepared['status'] = 'conflict'
        prepared['reason'] = '利用者が編集したため上書きしません'
        return prepared
    new_caption = prepared.get('newCaption') if prepared.get('needsCaption') else current_caption
    new_rel = prepared.get('newRelativePath') or current_rel
    if new_caption == current_caption and new_rel == current_rel:
        prepared = dict(prepared)
        prepared['status'] = 'noop'
        prepared['reason'] = '変更なし'
        return prepared
    c.execute(
        'UPDATE name_samples SET caption=?, relative_path=? WHERE id=?',
        (new_caption, new_rel, sample_id),
    )
    prepared = dict(prepared)
    prepared['newCaption'] = new_caption
    prepared['newRelativePath'] = new_rel
    prepared['status'] = 'changed'
    return prepared

def _write_female_hanako_report(root, backup_dir, stamp, items, excluded, failed, conflicts, skipped):
    changed_items = [item for item in items if item.get('status') == 'changed']
    catalog_changed = _patch_female_catalog(root, backup_dir, changed_items)
    surname_excluded = [item for item in excluded if item.get('excludedKind') == 'surname']
    other_excluded = [item for item in excluded if item.get('excludedKind') != 'surname']
    failed_by_layout = {}
    for item in failed:
        key = str(item.get('layout') or '(none)')
        failed_by_layout[key] = failed_by_layout.get(key, 0) + 1
    report = {
        'at': stamp,
        'changed': len(changed_items),
        'failed': len(failed),
        'excluded': len(excluded),
        'excludedSurname': len(surname_excluded),
        'conflicts': len(conflicts),
        'skippedComplete': len(skipped),
        'catalogSamplesPatched': catalog_changed,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'ruleVersion': FEMALE_HANAKO_RULE_VERSION,
        'maintenanceEnv': FEMALE_MAINTENANCE_ENV,
        'rule': {
            'include': ['女性', '女性用', '女性専用', '女性／…（複合条件）'],
            'exclude': [
                '男性', '男性用', '男性専用', '男女共用', '男女共通', '男女兼用',
                '色名のみ（例: 赤）', '判別不能', '苗字のみ（Surname / 「山田」のみ）',
            ],
            'nameReplace': list(YAMADA_TARO_TO_HANAKO),
            'keepSurnameOnly': True,
            'colorAloneNotFemale': True,
            'imageRequiredWhenPresent': True,
            'noStartupConversion': True,
            'imageStrategy': 'regenerate-name-or-composite',
            'regenerableLayouts': sorted(REGENERABLE_NAME_LAYOUTS),
            'nameOnlyLayouts': sorted(REGENERABLE_NAME_ONLY_LAYOUTS),
            'compositeLayouts': sorted(REGENERABLE_COMPOSITE_LAYOUTS),
            'surnameExcludedLayouts': sorted(SURNAME_EXCLUDED_LAYOUTS),
            'noPixelMatchThresholdRelaxation': True,
        },
        'failedByLayout': failed_by_layout,
        'items': changed_items,
        'failedItems': failed,
        'conflictItems': conflicts,
        'skippedCompleteItems': skipped[:100],
        'excludedItems': excluded[:100],
        'surnameExcludedItems': surname_excluded[:50],
        'nonFemaleCaptionLeftAlone': other_excluded[:50],
    }
    report_path = root / FEMALE_HANAKO_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if backup_dir.exists():
        (backup_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report

def _save_female_hanako_settings(c, stamp, report, completed_ids):
    c.execute(
        "INSERT OR REPLACE INTO settings VALUES(?,?)",
        (
            FEMALE_HANAKO_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': report.get('changed', 0),
                'failed': report.get('failed', 0),
                'report': FEMALE_HANAKO_REPORT,
                'ruleVersion': FEMALE_HANAKO_RULE_VERSION,
                'completedSampleIds': sorted(completed_ids),
            }, ensure_ascii=False),
        ),
    )

def _write_female_progress(root, payload):
    path = root / FEMALE_HANAKO_PROGRESS
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

def run_female_sample_hanako_maintenance(connect, data_dir=None, *, require_env=True, progress_callback=None):
    """起動と独立した女性見本の花子保守。

    require_env=True のときは DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1 が必要。
    対象は条件などに女性 / 女性用 / 女性専用がある見本のみ（色名だけでは対象外）。
    画像生成中は DB 書き込みトランザクションを保持せず、完成・検証後に短い
    トランザクションで参照先だけ更新する。更新直前に caption / relative_path が
    計画時と一致するか確認し、利用者編集は上書きしない。
    画像があるのに描き換え失敗した見本は caption だけの変更で完了にしない。
    完了見本は再変換しない。失敗は元のまま再実行可。
    """
    if require_env and not female_image_maintenance_allowed():
        return {
            'changed': 0, 'items': [], 'skipped': True,
            'reason': f'{FEMALE_MAINTENANCE_ENV}=1 のときだけ実行します（通常起動では変換しません）',
        }
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    with connect() as c:
        rows, path_users, prior, prior_version = _load_female_hanako_rows(c)
    if rows is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_samples未作成'}

    completed_ids = _female_completed_ids(prior)
    # ルール版上げ（v1/v2→v3）では完了IDをリセットして未フォークを再検査
    if prior_version < FEMALE_HANAKO_RULE_VERSION:
        completed_ids = set()

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'female-sample-hanako-{stamp}'
    candidates = []
    excluded = []
    skipped = []
    for row in rows:
        item = _female_candidate_from_row(row, path_users, completed_ids)
        if item is None:
            continue
        status = item.get('status')
        if status == 'excluded':
            excluded.append(item)
        elif status == 'already-complete':
            skipped.append(item)
            completed_ids.add(int(item['sampleId']))
        elif status == 'noop':
            skipped.append(item)
        elif status == 'pending':
            candidates.append(item)

    if not candidates and prior_version >= FEMALE_HANAKO_RULE_VERSION and not excluded:
        report = {
            'at': stamp, 'changed': 0, 'items': [], 'skipped': True,
            'reason': '適用済み' if prior else '対象なし',
            'ruleVersion': FEMALE_HANAKO_RULE_VERSION,
            'skippedComplete': len(skipped),
        }
        _write_female_progress(root, {'phase': 'idle', 'report': report})
        return report

    prepared_list = []
    failed = []
    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        base_progress = {
            'phase': 'image', 'index': index, 'total': total,
            'sampleId': candidate['sampleId'],
            'customer': candidate.get('customer'),
            'format': candidate.get('format'),
            'condition': candidate.get('condition'),
            'at': stamp,
        }
        if progress_callback:
            progress_callback(dict(base_progress))
        _write_female_progress(root, dict(base_progress))

        def on_try(info, _base=base_progress, _index=index, _total=total):
            payload = dict(_base)
            payload.update(info)
            payload['phase'] = 'image-try'
            payload['index'] = _index
            payload['total'] = _total
            if progress_callback:
                progress_callback(payload)
            # フォント試行は回数が多いので進捗ファイルは間引き（5件ごと＋最後）
            size_index = int(info.get('sizeIndex') or 0)
            size_total = int(info.get('sizeTotal') or 0)
            if size_index == 1 or size_index == size_total or size_index % 5 == 0:
                _write_female_progress(root, payload)

        prepared = _prepare_female_image(
            candidate, root, backup_dir, progress_callback=on_try,
        )
        status = prepared.get('status')
        if status == 'failed':
            failed.append(prepared)
        elif status == 'excluded':
            excluded.append(prepared)
        else:
            prepared_list.append(prepared)

    changed = []
    conflicts = []
    for index, prepared in enumerate(prepared_list, start=1):
        if progress_callback:
            progress_callback({
                'phase': 'commit', 'index': index, 'total': len(prepared_list),
                'sampleId': prepared['sampleId'],
            })
        _write_female_progress(root, {
            'phase': 'commit', 'index': index, 'total': len(prepared_list),
            'sampleId': prepared['sampleId'], 'at': stamp,
        })
        with connect() as c:
            outcome = _commit_female_sample(c, prepared)
        if outcome.get('status') == 'changed':
            changed.append(outcome)
            completed_ids.add(int(outcome['sampleId']))
        elif outcome.get('status') == 'conflict':
            conflicts.append(outcome)
        elif outcome.get('status') == 'failed':
            failed.append(outcome)
        elif outcome.get('status') == 'excluded':
            excluded.append(outcome)
        else:
            skipped.append(outcome)
            if outcome.get('status') in ('noop', 'already-complete'):
                completed_ids.add(int(outcome['sampleId']))

    report = _write_female_hanako_report(
        root, backup_dir, stamp, changed, excluded, failed, conflicts, skipped,
    )
    with connect() as c:
        _save_female_hanako_settings(c, stamp, report, completed_ids)
    _write_female_progress(root, {
        'phase': 'done', 'at': stamp,
        'changed': report['changed'], 'failed': report['failed'],
        'conflicts': report['conflicts'], 'skippedComplete': report['skippedComplete'],
    })
    return report

def correct_female_sample_names(c, data_dir=None, *, force=False):
    """女性見本の花子化（ゲート付き）。

    通常起動・schema・import_catalog からは呼ばない。
    DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1 または force=True（テスト／明示保守）のときだけ実行。
    本番の画像変換は run_female_sample_hanako_maintenance(connect, ...) を使うこと
    （画像生成中に DB トランザクションを保持しないため）。
    """
    if not force and not female_image_maintenance_allowed():
        return {
            'changed': 0, 'items': [], 'skipped': True,
            'reason': f'{FEMALE_MAINTENANCE_ENV} 未設定のためスキップ（起動時変換は行いません）',
        }

    # 単一接続向け（テスト用）。画像処理はこの接続のトランザクション内になる点に注意。
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    rows, path_users, prior, prior_version = _load_female_hanako_rows(c)
    if rows is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_samples未作成'}

    completed_ids = _female_completed_ids(prior)
    if prior_version < FEMALE_HANAKO_RULE_VERSION:
        completed_ids = set()

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'female-sample-hanako-{stamp}'
    changed, failed, conflicts, skipped, excluded = [], [], [], [], []
    pending = []
    for row in rows:
        item = _female_candidate_from_row(row, path_users, completed_ids)
        if item is None:
            continue
        status = item.get('status')
        if status == 'excluded':
            excluded.append(item)
        elif status in ('already-complete', 'noop'):
            skipped.append(item)
            if status == 'already-complete':
                completed_ids.add(int(item['sampleId']))
        else:
            pending.append(item)

    if not pending and prior_version >= FEMALE_HANAKO_RULE_VERSION:
        return {
            'changed': 0, 'items': [], 'skipped': True,
            'reason': '適用済み' if prior else '対象なし',
            'ruleVersion': FEMALE_HANAKO_RULE_VERSION,
        }

    for candidate in pending:
        prepared = _prepare_female_image(candidate, root, backup_dir)
        status = prepared.get('status')
        if status == 'failed':
            failed.append(prepared)
            continue
        if status == 'excluded':
            excluded.append(prepared)
            continue
        outcome = _commit_female_sample(c, prepared)
        if outcome.get('status') == 'changed':
            changed.append(outcome)
            completed_ids.add(int(outcome['sampleId']))
        elif outcome.get('status') == 'conflict':
            conflicts.append(outcome)
        elif outcome.get('status') == 'failed':
            failed.append(outcome)
        elif outcome.get('status') == 'excluded':
            excluded.append(outcome)
        else:
            skipped.append(outcome)

    report = _write_female_hanako_report(
        root, backup_dir, stamp, changed, excluded, failed, conflicts, skipped,
    )
    _save_female_hanako_settings(c, stamp, report, completed_ids)
    return report

# 鱗幸食品：先方から指定の色一覧へ「黄色」を追加（他顧客・他形式は触らない）。
# #99 で追加した「黄色専用の見本行」は表示用に不要なため削除し、再追加しない。
# 黄色は mixColors／説明文に残し、左側の「先方から指定」5色見本だけで示す。
RINKO_CUSTOMER_NAME = '鱗幸食品'
MIX_FIXED_COLOR = '先方から指定'
DEFAULT_MIX_COLORS = ['赤', '青', '緑', '白']
RINKO_MIX_COLORS = ['赤', '青', '緑', '白', '黄色']
YELLOW_COLOR_NAMES = frozenset({'黄色', '黄'})
RINKO_YELLOW_REPORT = 'rinko-yellow-report.json'
RINKO_YELLOW_SETTINGS_KEY = 'rinko_yellow'
RINKO_YELLOW_POLICY = 'mix-colors-only'  # 黄色専用見本は持たない
RINKO_YELLOW_IMAGE_SUFFIX = '-yellow'
RINKO_YELLOW_VARIANT = 'yellow'
YELLOW_PAPER_RGB = (255, 255, 224)  # #FFFFE0（互換用。新規生成はしない）

def _is_yellow_color(name):
    key = str(name or '').replace('?', '').replace('？', '').strip()
    return key in YELLOW_COLOR_NAMES

def _is_mix_color(name):
    """先方から指定（末尾の？付き原文も含む）。疑問符は照合時だけ外す。"""
    key = str(name or '').replace('?', '').replace('？', '').strip()
    return key == MIX_FIXED_COLOR

def _mix_color_label(existing):
    """既存が「先方から指定？」なら疑問符付き原文を維持。無ければ確定表記。"""
    raw = str(existing or '').strip()
    if _is_mix_color(raw) and ('?' in raw or '？' in raw):
        return raw
    return MIX_FIXED_COLOR

def _load_spec(row):
    raw = _row_get(row, 'spec_json', 'spec')
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}

def mix_colors_for(row):
    """形式の先方指定色一覧。未設定なら標準4色。"""
    spec = _load_spec(row)
    colors = spec.get('mixColors')
    if isinstance(colors, list) and colors:
        out = []
        for item in colors:
            text = str(item or '').strip()
            if text and text not in out:
                out.append(text)
        if out:
            return out
    return list(DEFAULT_MIX_COLORS)

def _format_has_mix(fmt, samples=None):
    if _is_mix_color(_row_get(fmt, 'fixed_color', 'fixedColor')):
        return True
    raw_colors = _row_get(fmt, 'colors_json')
    colors = []
    if raw_colors:
        try:
            colors = json.loads(raw_colors) or []
        except (TypeError, json.JSONDecodeError):
            colors = []
    elif isinstance(fmt, dict) and fmt.get('colors'):
        colors = fmt.get('colors') or []
    if any(_is_mix_color(color) for color in colors):
        return True
    for sample in samples or []:
        if _is_mix_color(_row_get(sample, 'paper_color', 'paperColor')):
            return True
    return False

def _append_yellow_to_text(text):
    """括弧内などの赤・青・緑・白一覧へ黄色を足す。既にあればそのまま。"""
    value = str(text or '')
    if not value:
        return value
    if '黄色' in value or re.search(r'(?<![男女])黄(?!色)', value):
        return value
    replacements = (
        ('赤・青・緑・白', '赤・青・緑・白・黄色'),
        ('赤、青、緑、白', '赤、青、緑、白、黄色'),
        ('赤/青/緑/白', '赤/青/緑/白/黄色'),
        ('赤･青･緑･白', '赤･青･緑･白･黄色'),
    )
    out = value
    for old, new in replacements:
        if old in out:
            out = out.replace(old, new)
    if out != value:
        out = out.replace('4色', '5色')
    return out

def _yellow_image_rel(rel):
    path = Path(str(rel))
    if path.stem.endswith(RINKO_YELLOW_IMAGE_SUFFIX):
        return str(path).replace('\\', '/')
    return str(path.with_name(path.stem + RINKO_YELLOW_IMAGE_SUFFIX + path.suffix)).replace('\\', '/')

def _is_generated_yellow_image(rel):
    """#99 移行が作った *-yellow.* 画像かどうか（元見本は対象外）。"""
    text = str(rel or '').replace('\\', '/')
    if not text.startswith('images/names/'):
        return False
    return Path(text).stem.endswith(RINKO_YELLOW_IMAGE_SUFFIX)

def _make_yellow_paper_image(src_path, dst_path):
    """互換用。現行移行では呼び出さない（黄色専用見本を再生成しない）。"""
    try:
        from PIL import Image
    except ImportError:
        return False, 'Pillowがありません'
    try:
        image = Image.open(src_path).convert('RGB')
    except OSError as exc:
        return False, str(exc)
    corners = [
        image.getpixel((0, 0)),
        image.getpixel((image.width - 1, 0)),
        image.getpixel((0, image.height - 1)),
        image.getpixel((image.width - 1, image.height - 1)),
    ]
    bg = max(set(corners), key=corners.count)
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            r, g, b = pixels[x, y]
            near_bg = abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) < 120
            near_white = r >= 200 and g >= 200 and b >= 200
            if near_bg or near_white:
                pixels[x, y] = YELLOW_PAPER_RGB
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.resolve() == Path(src_path).resolve():
        return False, '元画像と同じパスには保存しません'
    fmt = 'PNG' if dst_path.suffix.lower() == '.png' else 'JPEG' if dst_path.suffix.lower() in ('.jpg', '.jpeg') else 'WEBP'
    save_kw = {}
    if fmt == 'JPEG':
        save_kw['quality'] = 95
    image.save(dst_path, format=fmt, **save_kw)
    return True, ''

def _patch_rinko_catalog(data_dir, backup_dir, _report_items=None):
    """catalog.json の鱗幸食品・先方指定形式：mixColors／説明へ黄色を残し、黄色専用見本は除く。"""
    path = Path(data_dir) / 'name-import' / 'catalog.json'
    if not path.is_file():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / 'catalog.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    customers = {
        item.get('sourceKey'): item
        for item in (data.get('customers') or [])
        if str(item.get('name') or '').strip() == RINKO_CUSTOMER_NAME
    }
    if not customers:
        return 0
    changed = 0
    for fmt in data.get('formats') or []:
        if fmt.get('customerKey') not in customers:
            continue
        samples = list(fmt.get('samples') or [])
        if not _format_has_mix(fmt, samples):
            continue
        dirty = False
        if list(fmt.get('mixColors') or []) != RINKO_MIX_COLORS:
            fmt['mixColors'] = list(RINKO_MIX_COLORS)
            dirty = True
        for field in ('note', 'colorCaption'):
            before = fmt.get(field) or ''
            after = _append_yellow_to_text(before)
            if after != before:
                fmt[field] = after
                dirty = True
        kept = [s for s in samples if not _is_yellow_color(s.get('paperColor'))]
        if len(kept) != len(samples):
            fmt['samples'] = kept
            dirty = True
        if dirty:
            changed += 1
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return changed

def _rinko_mix_format_needs_work(fmt, samples):
    """mixColors／説明の黄色反映、または黄色専用見本の除去が必要か。"""
    current_mix = mix_colors_for(fmt)
    has_yellow_sample = any(_is_yellow_color(s['paper_color']) for s in samples)
    note_needs = _append_yellow_to_text(fmt['note'] or '') != (fmt['note'] or '')
    caption_needs = _append_yellow_to_text(fmt['color_caption'] or '') != (fmt['color_caption'] or '')
    mix_needs = current_mix != RINKO_MIX_COLORS or '黄色' not in current_mix
    return mix_needs or has_yellow_sample or note_needs or caption_needs

def correct_rinko_yellow(c, data_dir=None):
    """鱗幸食品の「先方から指定」へ黄色を色一覧として残し、黄色専用見本は削除する。

    - 他顧客・担当者・利用者・他業務データは変更しない
    - 既存色・書体・寸法・氏名・配置は維持
    - 黄色は mixColors／説明文に残す（再追加防止のため黄色専用見本は作らない）
    - 削除前に backups/rinko-yellow-<stamp>/ へ退避
    - 了承は name_samples / name_formats 更新トリガに任せ、勝手に了承しない
    """
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    customers = c.execute(
        'SELECT id, source_name FROM name_customers WHERE retired=0 AND source_name=?',
        (RINKO_CUSTOMER_NAME,),
    ).fetchall()
    if not customers:
        report = {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': '鱗幸食品なし',
            'customer': RINKO_CUSTOMER_NAME,
            'mixColors': list(RINKO_MIX_COLORS),
            'policy': RINKO_YELLOW_POLICY,
        }
        return report

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'rinko-yellow-{stamp}'
    items = []
    pending = False
    catalog_needs = False

    for customer in customers:
        formats = c.execute(
            'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
            (customer['id'],),
        ).fetchall()
        for fmt in formats:
            samples = c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (fmt['id'],),
            ).fetchall()
            if not _format_has_mix(fmt, samples):
                continue
            if _rinko_mix_format_needs_work(fmt, samples):
                pending = True
                break
        if pending:
            break

    catalog_path = root / 'name-import' / 'catalog.json'
    if catalog_path.is_file():
        try:
            catalog_data = json.loads(catalog_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            catalog_data = None
        if catalog_data:
            cat_customers = {
                item.get('sourceKey')
                for item in (catalog_data.get('customers') or [])
                if str(item.get('name') or '').strip() == RINKO_CUSTOMER_NAME
            }
            for fmt in catalog_data.get('formats') or []:
                if fmt.get('customerKey') not in cat_customers:
                    continue
                samples = list(fmt.get('samples') or [])
                if not _format_has_mix(fmt, samples):
                    continue
                if list(fmt.get('mixColors') or []) != RINKO_MIX_COLORS:
                    catalog_needs = True
                    break
                if any(_is_yellow_color(s.get('paperColor')) for s in samples):
                    catalog_needs = True
                    break
                for field in ('note', 'colorCaption'):
                    before = fmt.get(field) or ''
                    if _append_yellow_to_text(before) != before:
                        catalog_needs = True
                        break
                if catalog_needs:
                    break

    prior = c.execute(
        "SELECT value FROM settings WHERE key=?",
        (RINKO_YELLOW_SETTINGS_KEY,),
    ).fetchone()
    prior_policy = ''
    if prior:
        try:
            prior_policy = json.loads(prior['value'] or '{}').get('policy') or ''
        except (TypeError, json.JSONDecodeError):
            prior_policy = ''
    policy_needs = prior_policy != RINKO_YELLOW_POLICY

    if not pending and not catalog_needs and not policy_needs:
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': '適用済み' if prior else '対象形式なし',
            'customer': RINKO_CUSTOMER_NAME,
            'policy': RINKO_YELLOW_POLICY,
        }

    # データは既に正しいが settings の policy だけ古い場合は、重い退避なしで印だけ更新
    if not pending and not catalog_needs and policy_needs:
        c.execute(
            "INSERT OR REPLACE INTO settings VALUES(?,?)",
            (
                RINKO_YELLOW_SETTINGS_KEY,
                json.dumps({
                    'at': stamp,
                    'changed': 0,
                    'report': RINKO_YELLOW_REPORT,
                    'mixColors': list(RINKO_MIX_COLORS),
                    'policy': RINKO_YELLOW_POLICY,
                }, ensure_ascii=False),
            ),
        )
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': 'policy更新のみ',
            'customer': RINKO_CUSTOMER_NAME,
            'policy': RINKO_YELLOW_POLICY,
        }

    backup_dir.mkdir(parents=True, exist_ok=True)
    images_backup = backup_dir / 'images'
    images_backup.mkdir(parents=True, exist_ok=True)
    samples_backup = []

    for customer in customers:
        formats = c.execute(
            'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
            (customer['id'],),
        ).fetchall()
        for fmt in formats:
            samples = list(c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (fmt['id'],),
            ).fetchall())
            if not _format_has_mix(fmt, samples):
                continue

            before_mix = mix_colors_for(fmt)
            yellow_samples = [s for s in samples if _is_yellow_color(s['paper_color'])]
            kept_samples = [s for s in samples if not _is_yellow_color(s['paper_color'])]
            note_before = fmt['note'] or ''
            caption_before = fmt['color_caption'] or ''
            new_note = _append_yellow_to_text(note_before)
            new_color_caption = _append_yellow_to_text(caption_before)
            mix_needs = before_mix != RINKO_MIX_COLORS or '黄色' not in before_mix
            text_needs = new_note != note_before or new_color_caption != caption_before
            if not mix_needs and not text_needs and not yellow_samples:
                continue

            spec = _load_spec(fmt)
            spec['mixColors'] = list(RINKO_MIX_COLORS)
            c.execute(
                'UPDATE name_formats SET spec_json=?, note=?, color_caption=? WHERE id=?',
                (json.dumps(spec, ensure_ascii=False), new_note, new_color_caption, fmt['id']),
            )

            removed = []
            for sample in yellow_samples:
                row = {
                    'id': sample['id'],
                    'formatId': fmt['id'],
                    'variantKey': sample['variant_key'],
                    'conditionLabel': sample['condition_label'],
                    'paperColor': sample['paper_color'],
                    'relativePath': sample['relative_path'] or '',
                    'caption': sample['caption'] or '',
                }
                samples_backup.append(row)
                rel = sample['relative_path'] or ''
                image_action = 'none'
                if rel and _is_generated_yellow_image(rel):
                    src_path = root / rel
                    if src_path.is_file():
                        dest_backup = images_backup / Path(rel).name
                        if not dest_backup.exists():
                            shutil.copy2(src_path, dest_backup)
                        # 他見本が同じパスを参照していなければ削除
                        still_used = c.execute(
                            'SELECT 1 FROM name_samples WHERE relative_path=? AND id<>? LIMIT 1',
                            (rel, sample['id']),
                        ).fetchone()
                        if not still_used:
                            src_path.unlink(missing_ok=True)
                            image_action = 'deleted'
                        else:
                            image_action = 'kept-shared'
                    else:
                        image_action = 'missing'
                c.execute('DELETE FROM name_samples WHERE id=?', (sample['id'],))
                removed.append({
                    'sampleId': sample['id'],
                    'relativePath': rel,
                    'imageAction': image_action,
                })

            items.append({
                'customer': customer['source_name'],
                'formatId': fmt['id'],
                'number': _number(fmt['id']),
                'format': fmt['format_name'],
                'identity': fmt['identity'],
                'fixedColor': fmt['fixed_color'],
                'mixColorsBefore': before_mix,
                'mixColorsAfter': list(RINKO_MIX_COLORS),
                'keptSampleCount': len(kept_samples),
                'removedYellowSamples': removed,
                'noteUpdated': new_note != note_before,
                'colorCaptionUpdated': new_color_caption != caption_before,
            })

    if samples_backup:
        (backup_dir / 'yellow-samples.json').write_text(
            json.dumps(samples_backup, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    catalog_changed = _patch_rinko_catalog(root, backup_dir, items)
    report = {
        'at': stamp,
        'changed': len(items),
        'customer': RINKO_CUSTOMER_NAME,
        'mixColors': list(RINKO_MIX_COLORS),
        'policy': RINKO_YELLOW_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'catalogChanged': catalog_changed,
        'rules': {
            'onlyCustomer': RINKO_CUSTOMER_NAME,
            'onlyMixFormats': True,
            'addColor': '黄色',
            'keepExistingColors': True,
            'removeSoloYellowSamples': True,
            'noReaddSoloYellowSamples': True,
            'noAutoApprove': True,
        },
        'items': items,
    }
    report_path = root / RINKO_YELLOW_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if backup_dir.exists():
        (backup_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    c.execute(
        "INSERT OR REPLACE INTO settings VALUES(?,?)",
        (
            RINKO_YELLOW_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': len(items),
                'report': RINKO_YELLOW_REPORT,
                'mixColors': list(RINKO_MIX_COLORS),
                'policy': RINKO_YELLOW_POLICY,
            }, ensure_ascii=False),
        ),
    )
    return report

# 鱗幸食品：訂正版 Excel Sheet1 239–240 行（大・小ネーム）に合わせる。
# - 大ネーム：列1・1段フルネーム・大汎用、80×50、白衣左袖25cm（上段○○プレースホルダは出さない）
# - 小ネーム：1段フルネーム・小汎用、50×24、白下ベルト後左／白帽子右耳上
# - 色：先方から指定（赤・青・緑・白・黄色）＋役職対応。黄色専用見本は作らない（#105）
# - 担当者・了承は勝手に変更しない。他顧客は変更しない。重複形式は追加しない。
# Excel フォントサイズ欄はなお「上段：26　下段：大汎用」表記だが、列=1・段サイズ空・
# 利用者要望により表示は大汎用1段（上段○○なし）。
RINKO_LARGE_FORMAT_NAME = '大ネーム（フルネーム）'
RINKO_SMALL_FORMAT_NAME = '小ネーム（フルネーム）'
RINKO_LARGE_PLACEMENT = '白衣：左袖25cm'
RINKO_SMALL_PLACEMENT = '白下：ベルト後左　白帽子：右耳上'
RINKO_FAMILY = 'HG丸ゴシック'
# B2 汎用文字サイズ（小／大）
RINKO_SMALL_GENERIC_PT = {4: 26, 5: 24, 6: 20}
RINKO_LARGE_GENERIC_PT = {4: 48, 5: 38, 6: 30}
RINKO_LARGE_WIDTH_MM = 80
RINKO_LARGE_HEIGHT_MM = 50
RINKO_SMALL_WIDTH_MM = 50
RINKO_SMALL_HEIGHT_MM = 24
RINKO_SAMPLE_NAME = '山田 太郎'
RINKO_COLOR_GROUPS = ['工場長・次長・課長', '主任・係長', '社員', 'パート', '特殊色']
RINKO_COLOR_VALUES = ['赤', '緑', '青', '白', '黄']
RINKO_COLOR_SWATCHES = ['#F08080', '#90EE90', '#87CEFA', '#FFFFFF', '#FFFFE0']
RINKO_COLOR_CAPTION = (
    '工場長・次長・課長：赤　主任・係長：緑　社員：青　パート：白　特殊色：黄'
)
RINKO_COLOR_NOTE = (
    '用紙色は先方から指定（赤・青・緑・白・黄色）。'
    '工場長・次長・課長：赤／主任・係長：緑／社員：青／パート：白／特殊色：黄。'
    '黄色は5色見本の選択肢に含め、黄色専用の見本は持たない。'
)
RINKO_LARGE_HEADING = 'フルネーム（大汎用）'
RINKO_LARGE_NOTE = (
    f'{RINKO_COLOR_NOTE}\n'
    '1段・フルネーム／大汎用（4文字48・5文字38・6文字30）。'
    'Excel 列1・段サイズ列は空。見出しプレースホルダは出さない。'
)
RINKO_SMALL_HEADING = 'フルネーム（小汎用）'
RINKO_SMALL_NOTE = (
    f'{RINKO_COLOR_NOTE}\n'
    '1段・フルネーム／小汎用（4文字26・5文字24・6文字20）。'
)
RINKO_LARGE_SAMPLE_CAPTION = (
    f'見本：フルネーム「{RINKO_SAMPLE_NAME}」（大汎用・1段）。'
    f'書体は{RINKO_FAMILY}（太字）。用紙色は先方から指定（赤・青・緑・白・黄色）。'
)
RINKO_SMALL_SAMPLE_CAPTION = (
    f'見本：フルネーム「{RINKO_SAMPLE_NAME}」（小汎用・1段）。'
    f'書体は{RINKO_FAMILY}（太字）。用紙色は先方から指定（赤・青・緑・白・黄色）。'
)
RINKO_NAMES_REPORT = 'rinko-name-formats-report.json'
RINKO_NAMES_SETTINGS_KEY = 'rinko_name_formats'
RINKO_NAMES_POLICY = 'excel-gojuon2-r239-large-1band-v1'
RINKO_LARGE_SOURCE_ROW = 239
RINKO_SMALL_SOURCE_ROW = 240
RINKO_SAMPLE_PX_PER_MM = 10

def _rinko_char_count(text):
    return len(re.sub(r'\s+', '', str(text or '')))

def _rinko_generic_font_pt(kind, text):
    n = _rinko_char_count(text)
    table = RINKO_LARGE_GENERIC_PT if kind == 'large' else RINKO_SMALL_GENERIC_PT
    if n <= 4:
        return table[4]
    if n == 5:
        return table[5]
    return table[6]

def _rinko_large_bands():
    """大ネーム見本は1段（大汎用）。上段○○は描画しない。"""
    pt = _rinko_generic_font_pt('large', RINKO_SAMPLE_NAME)
    return (
        {
            'role': 'name', 'text': RINKO_SAMPLE_NAME,
            'heightMm': float(RINKO_LARGE_HEIGHT_MM), 'fontPt': pt,
            'alignH': 'center', 'alignV': 'middle', 'indentMm': 0.0,
        },
    )

def _rinko_small_bands():
    pt = _rinko_generic_font_pt('small', RINKO_SAMPLE_NAME)
    return (
        {
            'role': 'name', 'text': RINKO_SAMPLE_NAME,
            'heightMm': float(RINKO_SMALL_HEIGHT_MM), 'fontPt': pt,
            'alignH': 'center', 'alignV': 'middle', 'indentMm': 0.0,
        },
    )

def _rinko_spec(kind):
    if kind == 'small':
        bands = _rinko_small_bands()
        return {
            'mixColors': list(RINKO_MIX_COLORS),
            'rinkoKind': 'small',
            'rinkoPolicy': RINKO_NAMES_POLICY,
            'bandFontsPt': [b['fontPt'] for b in bands],
            'bandHeightsMm': [b['heightMm'] for b in bands],
            'genericFontsPt': dict(RINKO_SMALL_GENERIC_PT),
            'sampleName': RINKO_SAMPLE_NAME,
            'colorRoles': dict(zip(RINKO_COLOR_GROUPS, RINKO_COLOR_VALUES)),
        }
    bands = _rinko_large_bands()
    return {
        'mixColors': list(RINKO_MIX_COLORS),
        'rinkoKind': 'large',
        'rinkoPolicy': RINKO_NAMES_POLICY,
        'bandFontsPt': [b['fontPt'] for b in bands],
        'bandHeightsMm': [b['heightMm'] for b in bands],
        'genericFontsPt': dict(RINKO_LARGE_GENERIC_PT),
        'sampleName': RINKO_SAMPLE_NAME,
        'colorRoles': dict(zip(RINKO_COLOR_GROUPS, RINKO_COLOR_VALUES)),
    }

def _rinko_format_kind(fmt):
    """大／小の判定。該当しなければ None。"""
    name = str(fmt['format_name'] or '')
    try:
        spec = json.loads(fmt['spec_json'] or '{}')
    except (TypeError, json.JSONDecodeError):
        spec = {}
    kind = spec.get('rinkoKind')
    if kind in ('large', 'small'):
        return kind
    if int(fmt['small'] or 0) == 1 or name.startswith('小ネーム'):
        return 'small'
    if name.startswith('大ネーム') or '上段自由' in name:
        return 'large'
    width = int(fmt['width_mm'] or 0)
    height = int(fmt['height_mm'] or 0)
    if width == RINKO_SMALL_WIDTH_MM and height == RINKO_SMALL_HEIGHT_MM:
        return 'small'
    if width == RINKO_LARGE_WIDTH_MM and height == RINKO_LARGE_HEIGHT_MM:
        return 'large'
    return None

def _rinko_is_target_format(fmt, samples):
    if not _format_has_mix(fmt, samples):
        return False
    return _rinko_format_kind(fmt) in ('large', 'small')

def _rinko_groups_match(fmt):
    try:
        groups = json.loads(fmt['groups_json'] or '[]')
        colors = json.loads(fmt['colors_json'] or '[]')
        swatches = json.loads(fmt['swatches_json'] or '[]')
    except (TypeError, json.JSONDecodeError):
        return False
    return groups == RINKO_COLOR_GROUPS and colors == RINKO_COLOR_VALUES and swatches == RINKO_COLOR_SWATCHES

def _rinko_large_is_current(fmt, samples):
    try:
        spec = json.loads(fmt['spec_json'] or '{}')
    except (TypeError, json.JSONDecodeError):
        spec = {}
    if str(fmt['format_name'] or '') != RINKO_LARGE_FORMAT_NAME:
        return False
    if str(fmt['placement'] or '') != RINKO_LARGE_PLACEMENT:
        return False
    if str(fmt['family'] or '') != RINKO_FAMILY:
        return False
    if str(fmt['heading'] or '') != RINKO_LARGE_HEADING:
        return False
    if str(fmt['note'] or '') != RINKO_LARGE_NOTE:
        return False
    if str(fmt['layout'] or '') != 'Name':
        return False
    if str(fmt['reading'] or '') != 'フルネーム':
        return False
    if int(fmt['small'] or 0) != 0:
        return False
    if int(fmt['width_mm'] or 0) != RINKO_LARGE_WIDTH_MM or int(fmt['height_mm'] or 0) != RINKO_LARGE_HEIGHT_MM:
        return False
    if not _is_mix_color(fmt['fixed_color']):
        return False
    if str(fmt['color_caption'] or '') != RINKO_COLOR_CAPTION:
        return False
    if float(fmt['maximum_font'] or 0) != float(RINKO_LARGE_GENERIC_PT[4]):
        return False
    if float(fmt['heading_font'] or 0) != 0:
        return False
    if int(fmt['source_row'] or 0) != RINKO_LARGE_SOURCE_ROW:
        return False
    note = str(fmt['note'] or '')
    caption_blob = ' '.join(str(s['caption'] or '') for s in samples)
    # 旧2段／○○プレースホルダが残っていれば未適用
    if any(token in note + caption_blob for token in ('○○', '〇〇', '上段：', '自由入力')):
        return False
    if '上段帯' in note or '25mm' in note:
        return False
    if spec.get('sampleUpper') or spec.get('upperFontPt'):
        return False
    if len(spec.get('bandHeightsMm') or []) != 1:
        return False
    if not _rinko_groups_match(fmt):
        return False
    if mix_colors_for(fmt) != RINKO_MIX_COLORS:
        return False
    if spec.get('rinkoPolicy') != RINKO_NAMES_POLICY or spec.get('rinkoKind') != 'large':
        return False
    kept = [s for s in samples if not _is_yellow_color(s['paper_color'])]
    if len(kept) != 1 or any(_is_yellow_color(s['paper_color']) for s in samples):
        return False
    sample = kept[0]
    if not _is_mix_color(sample['paper_color']):
        return False
    rel = str(sample['relative_path'] or '')
    if not rel.startswith('images/names/') or 'rinko-large' not in Path(rel).name:
        return False
    if str(sample['caption'] or '') != RINKO_LARGE_SAMPLE_CAPTION:
        return False
    if any(token in str(sample['caption'] or '') for token in ('○○', '〇〇', '上段', '25mm')):
        return False
    return True

def _rinko_small_is_current(fmt, samples):
    try:
        spec = json.loads(fmt['spec_json'] or '{}')
    except (TypeError, json.JSONDecodeError):
        spec = {}
    if str(fmt['format_name'] or '') != RINKO_SMALL_FORMAT_NAME:
        return False
    if str(fmt['placement'] or '') != RINKO_SMALL_PLACEMENT:
        return False
    if str(fmt['family'] or '') != RINKO_FAMILY:
        return False
    if str(fmt['heading'] or '') != RINKO_SMALL_HEADING:
        return False
    if str(fmt['note'] or '') != RINKO_SMALL_NOTE:
        return False
    if str(fmt['layout'] or '') != 'Name':
        return False
    if str(fmt['reading'] or '') != 'フルネーム':
        return False
    if int(fmt['small'] or 0) != 1:
        return False
    if int(fmt['width_mm'] or 0) != RINKO_SMALL_WIDTH_MM or int(fmt['height_mm'] or 0) != RINKO_SMALL_HEIGHT_MM:
        return False
    if not _is_mix_color(fmt['fixed_color']):
        return False
    if str(fmt['color_caption'] or '') != RINKO_COLOR_CAPTION:
        return False
    if float(fmt['maximum_font'] or 0) != float(RINKO_SMALL_GENERIC_PT[4]):
        return False
    if float(fmt['heading_font'] or 0) != 0:
        return False
    if int(fmt['source_row'] or 0) != RINKO_SMALL_SOURCE_ROW:
        return False
    if not _rinko_groups_match(fmt):
        return False
    if mix_colors_for(fmt) != RINKO_MIX_COLORS:
        return False
    if spec.get('rinkoPolicy') != RINKO_NAMES_POLICY or spec.get('rinkoKind') != 'small':
        return False
    kept = [s for s in samples if not _is_yellow_color(s['paper_color'])]
    if len(kept) != 1 or any(_is_yellow_color(s['paper_color']) for s in samples):
        return False
    sample = kept[0]
    if not _is_mix_color(sample['paper_color']):
        return False
    rel = str(sample['relative_path'] or '')
    if not rel.startswith('images/names/') or 'rinko-small' not in Path(rel).name:
        return False
    if str(sample['caption'] or '') != RINKO_SMALL_SAMPLE_CAPTION:
        return False
    return True

def _backup_rinko_format(c, root, backup_dir, fmt, samples):
    backup_dir.mkdir(parents=True, exist_ok=True)
    images_backup = backup_dir / 'images'
    images_backup.mkdir(parents=True, exist_ok=True)
    payload = {
        'format': _row_as_dict(fmt),
        'samples': [_row_as_dict(s) for s in samples],
    }
    (backup_dir / f'format-{fmt["id"]}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    for sample in samples:
        rel = str(sample['relative_path'] or '')
        if not rel:
            continue
        src = root / rel
        if src.is_file():
            dest = images_backup / f'{fmt["id"]}-{Path(rel).name}'
            if not dest.exists():
                shutil.copy2(src, dest)

def _write_rinko_sample(c, root, format_id, *, kind, paper_color):
    """通常見本を1件に揃える。黄色専用見本は作らない。"""
    if kind == 'small':
        bands = _rinko_small_bands()
        stem = 'rinko-small'
        caption = RINKO_SMALL_SAMPLE_CAPTION
        width_mm, height_mm = RINKO_SMALL_WIDTH_MM, RINKO_SMALL_HEIGHT_MM
    else:
        bands = _rinko_large_bands()
        stem = 'rinko-large'
        caption = RINKO_LARGE_SAMPLE_CAPTION
        width_mm, height_mm = RINKO_LARGE_WIDTH_MM, RINKO_LARGE_HEIGHT_MM
    rel = f'images/names/{stem}-{format_id:06}.png'
    dst = root / rel
    ok, note = _render_ashuka_banded_sample(
        dst, bands, width_mm=width_mm, height_mm=height_mm, family_hint=RINKO_FAMILY,
    )
    if not ok:
        return False, note, ''
    # 黄色専用を含む既存見本を除き、先方指定の通常見本だけ残す
    c.execute('DELETE FROM name_samples WHERE format_id=?', (format_id,))
    c.execute(
        '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
           VALUES(?,?,?,?,?,?)''',
        (format_id, 'standard', '', paper_color, rel, caption),
    )
    return True, note, rel

def _update_rinko_format_row(c, format_id, *, kind, existing_fixed_color):
    fixed = _mix_color_label(existing_fixed_color)
    groups = json.dumps(RINKO_COLOR_GROUPS, ensure_ascii=False)
    colors = json.dumps(RINKO_COLOR_VALUES, ensure_ascii=False)
    swatches = json.dumps(RINKO_COLOR_SWATCHES, ensure_ascii=False)
    if kind == 'small':
        values = (
            RINKO_SMALL_FORMAT_NAME, RINKO_SMALL_PLACEMENT, RINKO_FAMILY, '',
            RINKO_SMALL_HEADING, RINKO_SMALL_NOTE, 'Name', 'フルネーム',
            1, RINKO_SMALL_WIDTH_MM, RINKO_SMALL_HEIGHT_MM, 0, fixed, RINKO_COLOR_CAPTION,
            groups, colors, swatches, 'null',
            '', RINKO_SMALL_GENERIC_PT[4], 0, 0, RINKO_SMALL_SOURCE_ROW,
            json.dumps(_rinko_spec('small'), ensure_ascii=False),
            0, json.dumps([], ensure_ascii=False), 0, format_id,
        )
    else:
        values = (
            RINKO_LARGE_FORMAT_NAME, RINKO_LARGE_PLACEMENT, RINKO_FAMILY, '',
            RINKO_LARGE_HEADING, RINKO_LARGE_NOTE, 'Name', 'フルネーム',
            0, RINKO_LARGE_WIDTH_MM, RINKO_LARGE_HEIGHT_MM, 0, fixed, RINKO_COLOR_CAPTION,
            groups, colors, swatches, 'null',
            '', RINKO_LARGE_GENERIC_PT[4], 0, 0, RINKO_LARGE_SOURCE_ROW,
            json.dumps(_rinko_spec('large'), ensure_ascii=False),
            0, json.dumps([], ensure_ascii=False), 0, format_id,
        )
    c.execute(
        '''UPDATE name_formats SET format_name=?, placement=?, family=?, header_family=?, heading=?, note=?,
            layout=?, reading=?, small=?, width_mm=?, height_mm=?, unknown_size=?, fixed_color=?, color_caption=?,
            groups_json=?, colors_json=?, swatches_json=?, departments_json=?, pending=?, maximum_font=?,
            heading_font=?, number_length=?, source_row=?, spec_json=?, needs_review=?, review_json=?, blank=?,
            retired=0 WHERE id=?''',
        values,
    )

def _patch_rinko_name_catalog(data_dir, backup_dir, items):
    path = Path(data_dir) / 'name-import' / 'catalog.json'
    if not path.is_file():
        return False
    backup_dir.mkdir(parents=True, exist_ok=True)
    catalog_backup = backup_dir / 'catalog.json'
    if not catalog_backup.exists():
        shutil.copy2(path, catalog_backup)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    customers = {
        item.get('sourceKey'): item
        for item in (data.get('customers') or [])
        if str(item.get('name') or '').strip() == RINKO_CUSTOMER_NAME
    }
    if not customers:
        return False
    by_id = {item.get('formatId'): item for item in items if item.get('formatId')}
    changed = False
    for fmt in data.get('formats') or []:
        if fmt.get('customerKey') not in customers:
            continue
        item = by_id.get(fmt.get('dbId'))
        if item is None:
            name = str(fmt.get('format') or '')
            for candidate in items:
                if candidate.get('kind') == 'large' and (
                    '上段自由' in name or name.startswith('大ネーム')
                ):
                    item = candidate
                    break
                if candidate.get('kind') == 'small' and name.startswith('小ネーム'):
                    item = candidate
                    break
        if item is None:
            continue
        kind = item.get('kind')
        if kind == 'small':
            fmt['format'] = RINKO_SMALL_FORMAT_NAME
            fmt['placement'] = RINKO_SMALL_PLACEMENT
            fmt['heading'] = RINKO_SMALL_HEADING
            fmt['note'] = RINKO_SMALL_NOTE
            fmt['layout'] = 'Name'
            fmt['reading'] = 'フルネーム'
            fmt['small'] = True
            fmt['widthMm'] = RINKO_SMALL_WIDTH_MM
            fmt['heightMm'] = RINKO_SMALL_HEIGHT_MM
            fmt['maximumFont'] = RINKO_SMALL_GENERIC_PT[4]
            fmt['headingFont'] = 0
            fmt['sourceRow'] = RINKO_SMALL_SOURCE_ROW
            caption = RINKO_SMALL_SAMPLE_CAPTION
        else:
            fmt['format'] = RINKO_LARGE_FORMAT_NAME
            fmt['placement'] = RINKO_LARGE_PLACEMENT
            fmt['heading'] = RINKO_LARGE_HEADING
            fmt['note'] = RINKO_LARGE_NOTE
            fmt['layout'] = 'Name'
            fmt['reading'] = 'フルネーム'
            fmt['small'] = False
            fmt['widthMm'] = RINKO_LARGE_WIDTH_MM
            fmt['heightMm'] = RINKO_LARGE_HEIGHT_MM
            fmt['maximumFont'] = RINKO_LARGE_GENERIC_PT[4]
            fmt['headingFont'] = 0
            fmt['sourceRow'] = RINKO_LARGE_SOURCE_ROW
            caption = RINKO_LARGE_SAMPLE_CAPTION
        fmt['family'] = RINKO_FAMILY
        existing_fixed = fmt.get('fixedColor') or MIX_FIXED_COLOR
        fmt['fixedColor'] = _mix_color_label(existing_fixed)
        fmt['colorCaption'] = RINKO_COLOR_CAPTION
        fmt['groups'] = list(RINKO_COLOR_GROUPS)
        fmt['colors'] = list(RINKO_COLOR_VALUES)
        fmt['swatches'] = list(RINKO_COLOR_SWATCHES)
        for key, value in _rinko_spec(kind).items():
            fmt[key] = value
        # 黄色専用見本は載せない
        fmt['samples'] = [{
            'key': 'standard',
            'condition': '',
            'paperColor': fmt['fixedColor'],
            'relativePath': item.get('samplePath') or '',
            'caption': caption,
        }]
        changed = True
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return changed

def correct_rinko_name_formats(c, data_dir=None):
    """鱗幸食品の大・小ネームを Excel 239–240 行仕様へ更新する。

    - 既存形式を更新し、同じ形式を重複追加しない
    - 大ネームは列1の大汎用1段。見出し／見本の○○プレースホルダは削除
    - 黄色は mixColors／説明に残し、黄色専用見本は削除・再追加しない
    - 担当者は触らない。了承 digest は既存トリガのみ
    """
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    customers = c.execute(
        'SELECT id, source_name FROM name_customers WHERE retired=0 AND source_name=?',
        (RINKO_CUSTOMER_NAME,),
    ).fetchall()
    if not customers:
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': '鱗幸食品なし',
            'customer': RINKO_CUSTOMER_NAME,
            'policy': RINKO_NAMES_POLICY,
        }

    prior = c.execute(
        'SELECT value FROM settings WHERE key=?',
        (RINKO_NAMES_SETTINGS_KEY,),
    ).fetchone()
    prior_policy = ''
    if prior:
        try:
            prior_policy = json.loads(prior['value'] or '{}').get('policy') or ''
        except (TypeError, json.JSONDecodeError):
            prior_policy = ''

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'rinko-names-{stamp}'
    items = []
    customer = customers[0]
    formats = list(c.execute(
        'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
        (customer['id'],),
    ).fetchall())
    samples_by_fid = {}
    for fmt in formats:
        samples_by_fid[fmt['id']] = list(c.execute(
            'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
            (fmt['id'],),
        ).fetchall())

    large = None
    small = None
    for fmt in formats:
        samples = samples_by_fid.get(fmt['id'], [])
        if not _rinko_is_target_format(fmt, samples):
            continue
        kind = _rinko_format_kind(fmt)
        if kind == 'large' and large is None:
            large = fmt
        elif kind == 'small' and small is None:
            small = fmt

    needs = False
    if large is None or not _rinko_large_is_current(large, samples_by_fid.get(large['id'], [])):
        needs = True
    if small is None or not _rinko_small_is_current(small, samples_by_fid.get(small['id'], [])):
        needs = True

    catalog_path = root / 'name-import' / 'catalog.json'
    catalog_needs = False
    if catalog_path.is_file():
        try:
            catalog_data = json.loads(catalog_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            catalog_data = None
        if catalog_data:
            cat_customers = {
                item.get('sourceKey')
                for item in (catalog_data.get('customers') or [])
                if str(item.get('name') or '').strip() == RINKO_CUSTOMER_NAME
            }
            for fmt in catalog_data.get('formats') or []:
                if fmt.get('customerKey') not in cat_customers:
                    continue
                name = str(fmt.get('format') or '')
                is_large = '上段自由' in name or (name.startswith('大ネーム') and not name.startswith('小'))
                is_small = name.startswith('小ネーム')
                if not (is_large or is_small):
                    continue
                if fmt.get('rinkoPolicy') != RINKO_NAMES_POLICY:
                    catalog_needs = True
                    break
                samples = list(fmt.get('samples') or [])
                if any(_is_yellow_color(s.get('paperColor')) for s in samples):
                    catalog_needs = True
                    break

    policy_needs = prior_policy != RINKO_NAMES_POLICY
    if not needs and not catalog_needs and not policy_needs:
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': '適用済み' if prior else '対象形式なし',
            'customer': RINKO_CUSTOMER_NAME,
            'policy': RINKO_NAMES_POLICY,
        }

    if not needs and not catalog_needs and policy_needs:
        c.execute(
            'INSERT OR REPLACE INTO settings VALUES(?,?)',
            (
                RINKO_NAMES_SETTINGS_KEY,
                json.dumps({
                    'at': stamp,
                    'changed': 0,
                    'report': RINKO_NAMES_REPORT,
                    'policy': RINKO_NAMES_POLICY,
                    'mixColors': list(RINKO_MIX_COLORS),
                }, ensure_ascii=False),
            ),
        )
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': 'policy更新のみ',
            'customer': RINKO_CUSTOMER_NAME,
            'policy': RINKO_NAMES_POLICY,
        }

    for kind, fmt in (('large', large), ('small', small)):
        if fmt is None:
            items.append({
                'kind': kind,
                'action': 'missing',
                'reason': f'鱗幸食品に{"大" if kind == "large" else "小"}ネーム（先方指定）が無いためスキップ（新規追加しない）',
            })
            continue
        samples = samples_by_fid.get(fmt['id'], [])
        is_current = (
            _rinko_large_is_current(fmt, samples) if kind == 'large'
            else _rinko_small_is_current(fmt, samples)
        )
        if is_current:
            continue
        _backup_rinko_format(c, root, backup_dir, fmt, samples)
        _update_rinko_format_row(c, fmt['id'], kind=kind, existing_fixed_color=fmt['fixed_color'])
        paper = _mix_color_label(fmt['fixed_color'])
        ok, note, rel = _write_rinko_sample(c, root, fmt['id'], kind=kind, paper_color=paper)
        removed_yellow = []
        for sample in samples:
            if not _is_yellow_color(sample['paper_color']):
                continue
            yellow_rel = str(sample['relative_path'] or '')
            image_action = 'none'
            if yellow_rel and _is_generated_yellow_image(yellow_rel):
                src_path = root / yellow_rel
                if src_path.is_file():
                    still_used = c.execute(
                        'SELECT 1 FROM name_samples WHERE relative_path=? LIMIT 1',
                        (yellow_rel,),
                    ).fetchone()
                    if not still_used:
                        src_path.unlink(missing_ok=True)
                        image_action = 'deleted'
                    else:
                        image_action = 'kept-shared'
                else:
                    image_action = 'missing'
            elif yellow_rel:
                # 生成サフィックス以外でも、もう参照されない黄色専用画像は削除
                src_path = root / yellow_rel
                if src_path.is_file():
                    still_used = c.execute(
                        'SELECT 1 FROM name_samples WHERE relative_path=? LIMIT 1',
                        (yellow_rel,),
                    ).fetchone()
                    if not still_used:
                        src_path.unlink(missing_ok=True)
                        image_action = 'deleted'
                    else:
                        image_action = 'kept-shared'
            removed_yellow.append({
                'sampleId': sample['id'],
                'relativePath': yellow_rel,
                'imageAction': image_action,
            })
        items.append({
            'kind': kind,
            'action': 'update',
            'formatId': fmt['id'],
            'number': _number(fmt['id']),
            'identity': fmt['identity'],
            'formatBefore': fmt['format_name'],
            'formatAfter': RINKO_LARGE_FORMAT_NAME if kind == 'large' else RINKO_SMALL_FORMAT_NAME,
            'samplePath': rel,
            'sampleOk': ok,
            'sampleNote': note,
            'removedYellowSamples': removed_yellow,
        })

    catalog_changed = _patch_rinko_name_catalog(root, backup_dir, items) if items or catalog_needs else False
    changed = sum(1 for i in items if i.get('action') == 'update')
    report = {
        'at': stamp,
        'changed': changed,
        'customer': RINKO_CUSTOMER_NAME,
        'policy': RINKO_NAMES_POLICY,
        'mixColors': list(RINKO_MIX_COLORS),
        'colorRoles': dict(zip(RINKO_COLOR_GROUPS, RINKO_COLOR_VALUES)),
        'b2': {
            'smallGenericPt': dict(RINKO_SMALL_GENERIC_PT),
            'largeGenericPt': dict(RINKO_LARGE_GENERIC_PT),
            'raw': '小汎用（4文字26　5文字24　6文字20)　大汎用 (4文字48　5文字38　6文字30)',
        },
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'catalogChanged': catalog_changed,
        'rules': {
            'onlyCustomer': RINKO_CUSTOMER_NAME,
            'onlyLargeSmallMix': True,
            'largeOneBandGeneric': True,
            'noUpperPlaceholderOo': True,
            'noSoloYellowSamples': True,
            'noDuplicateFormats': True,
            'noAutoApprove': True,
            'preserveAssignee': True,
            'preserveMixQuestionMark': True,
        },
        'items': items,
    }
    report_path = root / RINKO_NAMES_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if backup_dir.exists():
        (backup_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            RINKO_NAMES_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': changed,
                'report': RINKO_NAMES_REPORT,
                'policy': RINKO_NAMES_POLICY,
                'mixColors': list(RINKO_MIX_COLORS),
            }, ensure_ascii=False),
        ),
    )
    return report

# 明日佳（札幌/桜台/白石/岩見沢/宮の沢明日佳）：訂正版 Excel Sheet1 78–80 行に合わせる。
# - NM-000150（id=150）の3段ネームを更新
# - 外国人（実習生）用2段ネームを追加（空の NM-000229 があれば再利用、内容ありは触らない）
# - 担当者・了承は勝手に変更しない（形式更新トリガで了承消化のみ）
# - 他顧客は変更しない
ASHUKA_CUSTOMER_NAME = '札幌/桜台/白石/岩見沢/宮の沢明日佳'
ASHUKA_THREE_FORMAT_ID = 150
ASHUKA_EMPTY_FORMAT_ID = 229
ASHUKA_THREE_FORMAT_NAME = '大ネーム（部署・ふりがな・苗字）'
ASHUKA_TRAINEE_FORMAT_NAME = '大ネーム（部署・カタカナ名／実習生用）'
ASHUKA_TRAINEE_IDENTITY = 'ashuka:trainee-dept-katakana'
ASHUKA_PLACEMENT = 'スクラブ：左胸　ケーシー：左胸'
ASHUKA_FAMILY = 'HG丸ゴシック'
ASHUKA_DEPARTMENTS = [
    '看護部', 'ﾘﾊﾋﾞﾘﾃｰｼｮﾝ科', '歯科', '薬剤科', '栄養科', '検査科',
    '放射線科', '臨床検査科', 'デイケア科', '医師', '歯科医師', '院長',
]
ASHUKA_THREE_HEADING = '上段：部署名　中段：ふりがな　下段：苗字'
ASHUKA_THREE_NOTE = (
    'ふりがなはひらがなで\n'
    'ふりがなは文字の間に半角スペースを入れる 例「や ま だ」\n'
    '上段のみ左にインデント\n'
    '上段帯：12.8mm／24pt／横左揃え・縦中央／中段帯：12.5mm／28pt／横中央・縦中央／'
    '下段帯：24.7mm／50pt／横中央・縦中央（合計50mm）'
)
ASHUKA_TRAINEE_HEADING = '上段：部署名　下段：ファーストネーム 例「ターシャー」'
ASHUKA_TRAINEE_NOTE = (
    '外国人（実習生）の場合はカタカナ表記のためふりがななし\n'
    '上段帯：12.8mm／24pt／横左揃え・縦中央／下段帯：37.2mm／50pt／横中央・縦中央（合計50mm）'
)
ASHUKA_THREE_SAMPLE_CAPTION = (
    '見本：部署「看護部」・ふりがな「や ま だ」・苗字「山田」。'
    '上段12.8mm（24pt・左インデント）／中段12.5mm（28pt・中央）／下段24.7mm（50pt・中央）。'
    '書体はHG丸ゴシック（太字）。用紙色は白。'
)
ASHUKA_TRAINEE_SAMPLE_CAPTION = (
    '見本：部署「看護部」・ファーストネーム「ターシャー」。'
    '上段12.8mm（24pt・左インデント）／下段37.2mm（50pt・中央）。ふりがななし。'
    '書体はHG丸ゴシック（太字）。用紙色は白。'
)
ASHUKA_THREE_BANDS = (
    {'role': 'dept', 'text': '看護部', 'heightMm': 12.8, 'fontPt': 24, 'alignH': 'left', 'alignV': 'middle', 'indentMm': 4.0},
    {'role': 'furigana', 'text': 'や ま だ', 'heightMm': 12.5, 'fontPt': 28, 'alignH': 'center', 'alignV': 'middle', 'indentMm': 0.0},
    {'role': 'surname', 'text': '山田', 'heightMm': 24.7, 'fontPt': 50, 'alignH': 'center', 'alignV': 'middle', 'indentMm': 0.0},
)
ASHUKA_TRAINEE_BANDS = (
    {'role': 'dept', 'text': '看護部', 'heightMm': 12.8, 'fontPt': 24, 'alignH': 'left', 'alignV': 'middle', 'indentMm': 4.0},
    {'role': 'given', 'text': 'ターシャー', 'heightMm': 37.2, 'fontPt': 50, 'alignH': 'center', 'alignV': 'middle', 'indentMm': 0.0},
)
ASHUKA_NAMES_REPORT = 'ashuka-name-formats-report.json'
ASHUKA_NAMES_SETTINGS_KEY = 'ashuka_name_formats'
ASHUKA_NAMES_POLICY = 'excel-gojuon2-r78-80-v1'
ASHUKA_SAMPLE_PX_PER_MM = 10

def _ashuka_spec(kind):
    """kind: 'three' | 'trainee'。帯仕様を spec_json に残し再生成と照合に使う。"""
    if kind == 'trainee':
        bands = ASHUKA_TRAINEE_BANDS
        return {
            'surnameOnly': True,
            'ashukaKind': 'trainee',
            'ashukaPolicy': ASHUKA_NAMES_POLICY,
            'bandFontsPt': [b['fontPt'] for b in bands],
            'bandHeightsMm': [b['heightMm'] for b in bands],
            'sampleName': 'ターシャー',
        }
    bands = ASHUKA_THREE_BANDS
    return {
        'surnameOnly': True,
        'spaceSurname': True,
        'ashukaKind': 'three',
        'ashukaPolicy': ASHUKA_NAMES_POLICY,
        'bandFontsPt': [b['fontPt'] for b in bands],
        'bandHeightsMm': [b['heightMm'] for b in bands],
        'sampleFurigana': 'や ま だ',
        'sampleSurname': '山田',
    }

def _ashuka_format_matches_target(fmt, *, kind):
    """登録済み形式が明日佳の3段／実習生用として扱えるか。"""
    name = str(fmt['format_name'] or '')
    layout = str(fmt['layout'] or '')
    heading = str(fmt['heading'] or '')
    note = str(fmt['note'] or '')
    try:
        spec = json.loads(fmt['spec_json'] or '{}')
    except (TypeError, json.JSONDecodeError):
        spec = {}
    if kind == 'trainee':
        if spec.get('ashukaKind') == 'trainee':
            return True
        if '実習生' in name or 'カタカナ' in name:
            return True
        if 'ターシャー' in heading or 'ふりがななし' in note:
            return True
        return False
    if spec.get('ashukaKind') == 'three':
        return True
    if fmt['id'] == ASHUKA_THREE_FORMAT_ID:
        return True
    if 'ふりがな' in name or 'ふりがな' in heading:
        return True
    if layout == 'DeptFurigana':
        return True
    return False

def _ashuka_is_empty_shell(fmt, samples):
    """内容未設定の「大ネーム」シェルか（NM-000229 再利用判定）。"""
    name = str(fmt['format_name'] or '').strip()
    if name not in ('', '大ネーム', '大ネーム（内容未設定）'):
        return False
    if _ashuka_format_matches_target(fmt, kind='three') or _ashuka_format_matches_target(fmt, kind='trainee'):
        return False
    heading = str(fmt['heading'] or '').strip()
    note = str(fmt['note'] or '').strip()
    try:
        departments = json.loads(fmt['departments_json'] or 'null') or []
    except (TypeError, json.JSONDecodeError):
        departments = []
    has_image = any(str(s['relative_path'] or '').strip() for s in samples)
    return not heading and not note and not departments and not has_image

def _ashuka_three_is_current(fmt, samples):
    try:
        departments = json.loads(fmt['departments_json'] or 'null') or []
    except (TypeError, json.JSONDecodeError):
        departments = []
    try:
        spec = json.loads(fmt['spec_json'] or '{}')
    except (TypeError, json.JSONDecodeError):
        spec = {}
    if str(fmt['format_name'] or '') != ASHUKA_THREE_FORMAT_NAME:
        return False
    if str(fmt['placement'] or '') != ASHUKA_PLACEMENT:
        return False
    if str(fmt['heading'] or '') != ASHUKA_THREE_HEADING:
        return False
    if str(fmt['note'] or '') != ASHUKA_THREE_NOTE:
        return False
    if str(fmt['family'] or '') != ASHUKA_FAMILY:
        return False
    if str(fmt['layout'] or '') != 'DeptFurigana':
        return False
    if str(fmt['reading'] or '') != '苗字のみ':
        return False
    if int(fmt['width_mm'] or 0) != 80 or int(fmt['height_mm'] or 0) != 50:
        return False
    if int(fmt['small'] or 0) != 0 or int(fmt['blank'] or 0) != 0 or int(fmt['unknown_size'] or 0) != 0:
        return False
    if str(fmt['fixed_color'] or '') != '白':
        return False
    if float(fmt['maximum_font'] or 0) != 50 or float(fmt['heading_font'] or 0) != 24:
        return False
    if departments != ASHUKA_DEPARTMENTS:
        return False
    if spec.get('ashukaPolicy') != ASHUKA_NAMES_POLICY or spec.get('ashukaKind') != 'three':
        return False
    if not samples:
        return False
    sample = samples[0]
    rel = str(sample['relative_path'] or '')
    if not rel.startswith('images/names/') or 'ashuka-three' not in Path(rel).name:
        return False
    if str(sample['caption'] or '') != ASHUKA_THREE_SAMPLE_CAPTION:
        return False
    if str(sample['paper_color'] or '') != '白':
        return False
    return True

def _ashuka_trainee_is_current(fmt, samples):
    try:
        departments = json.loads(fmt['departments_json'] or 'null') or []
    except (TypeError, json.JSONDecodeError):
        departments = []
    try:
        spec = json.loads(fmt['spec_json'] or '{}')
    except (TypeError, json.JSONDecodeError):
        spec = {}
    if str(fmt['format_name'] or '') != ASHUKA_TRAINEE_FORMAT_NAME:
        return False
    if str(fmt['placement'] or '') != ASHUKA_PLACEMENT:
        return False
    if str(fmt['heading'] or '') != ASHUKA_TRAINEE_HEADING:
        return False
    if str(fmt['note'] or '') != ASHUKA_TRAINEE_NOTE:
        return False
    if str(fmt['family'] or '') != ASHUKA_FAMILY:
        return False
    if str(fmt['layout'] or '') != 'DeptName':
        return False
    if str(fmt['reading'] or '') != 'カタカナのファーストネーム（ふりがななし）':
        return False
    if int(fmt['width_mm'] or 0) != 80 or int(fmt['height_mm'] or 0) != 50:
        return False
    if str(fmt['fixed_color'] or '') != '白':
        return False
    if float(fmt['maximum_font'] or 0) != 50 or float(fmt['heading_font'] or 0) != 24:
        return False
    if departments != ASHUKA_DEPARTMENTS:
        return False
    if spec.get('ashukaPolicy') != ASHUKA_NAMES_POLICY or spec.get('ashukaKind') != 'trainee':
        return False
    if not samples:
        return False
    sample = samples[0]
    rel = str(sample['relative_path'] or '')
    if not rel.startswith('images/names/') or 'ashuka-trainee' not in Path(rel).name:
        return False
    if str(sample['caption'] or '') != ASHUKA_TRAINEE_SAMPLE_CAPTION:
        return False
    return True

def _render_ashuka_banded_sample(
    dst_path, bands, *, width_mm=80, height_mm=50, family_hint=ASHUKA_FAMILY,
    paper_rgb=(255, 255, 255), fit_mode='fill',
):
    """帯付き見本を描画。帯高合計は呼び出し側で height_mm に合わせる。既定は白地。

    fit_mode:
      - fill: 枠に収まる最大サイズへ拡大可（大汎用・小汎用など従来どおり）
      - exact: 登録 fontPt→px を上限（溢れるときだけ縮小）。丸/Pゴシック36 苗字用
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False, 'Pillow未導入'
    total = sum(float(b['heightMm']) for b in bands)
    if abs(total - float(height_mm)) > 0.05:
        return False, f'帯高合計が{height_mm}mmでない（{total}）'
    font = _load_font(24, family_hint)
    if font is None:
        return False, '利用可能なフォントがありません（Windows のゴシック系／HG丸ゴシックを優先）'
    px = ASHUKA_SAMPLE_PX_PER_MM
    width = max(1, int(round(width_mm * px)))
    height = max(1, int(round(height_mm * px)))
    image = Image.new('RGB', (width, height), tuple(paper_rgb))
    draw = ImageDraw.Draw(image)
    allow_grow = str(fit_mode or 'fill') != 'exact'
    y = 0.0
    for band in bands:
        band_h = float(band['heightMm']) * px
        top = int(round(y))
        bottom = int(round(y + band_h))
        if band is bands[-1]:
            bottom = height
        box_h = max(1, bottom - top)
        font_pt = float(band['fontPt'])
        font_px = max(8, int(round(font_pt * px * 25.4 / 72.0)))
        text = str(band.get('text') or '')
        fitted_font, used = _fit_font_to_box(
            text, family_hint,
            max(8, width - int(round((float(band.get('indentMm') or 0) + 2.0) * px)) - 4),
            max(8, box_h - 4),
            font_px,
            allow_grow=allow_grow,
        )
        font = fitted_font or _load_font(used or font_px, family_hint) or _load_font(font_px, family_hint)
        if font is None:
            return False, '利用可能なフォントがありません'
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        indent = float(band.get('indentMm') or 0) * px
        align_h = str(band.get('alignH') or 'center')
        if align_h == 'left':
            x = int(round(indent if indent > 0 else 2 * px))
        else:
            x = int(round((width - tw) / 2))
        x = max(0, min(x, width - max(1, tw)))
        align_v = str(band.get('alignV') or 'middle')
        if align_v == 'top':
            ty = top + 2
        elif align_v == 'bottom':
            ty = bottom - th - 2
        else:
            ty = top + int(round((box_h - th) / 2))
        ty = max(top, min(ty, bottom - max(1, th)))
        draw.text((x - bbox[0], ty - bbox[1]), text, font=font, fill=(0, 0, 0))
        y += band_h
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(dst_path, format='PNG')
    mode_note = 'exact-pt' if not allow_grow else 'fill'
    return True, f'banded sample {width_mm}x{height_mm}mm bands={len(bands)} fit={mode_note}'

def _backup_ashuka_format(c, root, backup_dir, fmt, samples):
    """変更前の形式・見本画像を backups/ashuka-names-<stamp>/ へ退避。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    images_backup = backup_dir / 'images'
    images_backup.mkdir(parents=True, exist_ok=True)
    payload = {
        'format': _row_as_dict(fmt),
        'samples': [_row_as_dict(s) for s in samples],
    }
    (backup_dir / f'format-{fmt["id"]}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    for sample in samples:
        rel = str(sample['relative_path'] or '')
        if not rel:
            continue
        src = root / rel
        if src.is_file():
            dest = images_backup / f'{fmt["id"]}-{Path(rel).name}'
            if not dest.exists():
                shutil.copy2(src, dest)

def _write_ashuka_sample(c, root, format_id, *, kind, caption):
    """見本画像を生成し name_samples を1件に揃える。"""
    bands = ASHUKA_TRAINEE_BANDS if kind == 'trainee' else ASHUKA_THREE_BANDS
    stem = 'ashuka-trainee' if kind == 'trainee' else 'ashuka-three'
    rel = f'images/names/{stem}-{format_id:06}.png'
    dst = root / rel
    ok, note = _render_ashuka_banded_sample(dst, bands)
    if not ok:
        return False, note, ''
    c.execute('DELETE FROM name_samples WHERE format_id=?', (format_id,))
    c.execute(
        '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
           VALUES(?,?,?,?,?,?)''',
        (format_id, 'standard', '', '白', rel, caption),
    )
    return True, note, rel

def _update_ashuka_format_row(c, format_id, *, kind, source_row, identity=None):
    """明日佳の形式行を Excel 仕様へ更新（担当・了承レコードは触らない）。"""
    if kind == 'trainee':
        values = (
            ASHUKA_TRAINEE_FORMAT_NAME, ASHUKA_PLACEMENT, ASHUKA_FAMILY, '',
            ASHUKA_TRAINEE_HEADING, ASHUKA_TRAINEE_NOTE, 'DeptName',
            'カタカナのファーストネーム（ふりがななし）',
            0, 80, 50, 0, '白', '',
            json.dumps([], ensure_ascii=False), json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False), json.dumps(ASHUKA_DEPARTMENTS, ensure_ascii=False),
            '', 50, 24, 0, source_row,
            json.dumps(_ashuka_spec('trainee'), ensure_ascii=False),
            0, json.dumps([], ensure_ascii=False), 0, format_id,
        )
    else:
        values = (
            ASHUKA_THREE_FORMAT_NAME, ASHUKA_PLACEMENT, ASHUKA_FAMILY, '',
            ASHUKA_THREE_HEADING, ASHUKA_THREE_NOTE, 'DeptFurigana', '苗字のみ',
            0, 80, 50, 0, '白', '',
            json.dumps([], ensure_ascii=False), json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False), json.dumps(ASHUKA_DEPARTMENTS, ensure_ascii=False),
            '', 50, 24, 0, source_row,
            json.dumps(_ashuka_spec('three'), ensure_ascii=False),
            0, json.dumps([], ensure_ascii=False), 0, format_id,
        )
    c.execute(
        '''UPDATE name_formats SET format_name=?, placement=?, family=?, header_family=?, heading=?, note=?,
            layout=?, reading=?, small=?, width_mm=?, height_mm=?, unknown_size=?, fixed_color=?, color_caption=?,
            groups_json=?, colors_json=?, swatches_json=?, departments_json=?, pending=?, maximum_font=?,
            heading_font=?, number_length=?, source_row=?, spec_json=?, needs_review=?, review_json=?, blank=?,
            retired=0 WHERE id=?''',
        values,
    )
    if identity:
        c.execute('UPDATE name_formats SET identity=? WHERE id=?', (identity, format_id))

def _insert_ashuka_trainee_format(c, customer_id):
    """実習生用2段ネームを新規追加。identity は再追加防止用に固定。"""
    c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, family, header_family, heading, note,
            layout, reading, small, width_mm, height_mm, unknown_size, fixed_color, color_caption,
            groups_json, colors_json, swatches_json, departments_json, pending, maximum_font, heading_font,
            number_length, source_row, previous_source_row, spec_json, needs_review, review_json, blank, manual, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)''',
        (
            ASHUKA_TRAINEE_IDENTITY, customer_id, -1, ASHUKA_TRAINEE_FORMAT_NAME, ASHUKA_PLACEMENT,
            ASHUKA_FAMILY, '', ASHUKA_TRAINEE_HEADING, ASHUKA_TRAINEE_NOTE, 'DeptName',
            'カタカナのファーストネーム（ふりがななし）', 0, 80, 50, 0, '白', '',
            json.dumps([], ensure_ascii=False), json.dumps([], ensure_ascii=False),
            json.dumps([], ensure_ascii=False), json.dumps(ASHUKA_DEPARTMENTS, ensure_ascii=False),
            '', 50, 24, 0, 79, None,
            json.dumps(_ashuka_spec('trainee'), ensure_ascii=False),
            0, json.dumps([], ensure_ascii=False), 0,
        ),
    )
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]

def _patch_ashuka_catalog(data_dir, backup_dir, items):
    """catalog.json があれば明日佳の形式・見本を同じ内容へ揃える。"""
    path = Path(data_dir) / 'name-import' / 'catalog.json'
    if not path.is_file():
        return False
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / 'catalog.json')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    customers = {
        item.get('sourceKey'): item
        for item in (data.get('customers') or [])
        if str(item.get('name') or '').strip() == ASHUKA_CUSTOMER_NAME
        or _correct_customer_name(item.get('name') or '') == ASHUKA_CUSTOMER_NAME
    }
    if not customers:
        return False
    changed = False
    for fmt in data.get('formats') or []:
        if fmt.get('customerKey') not in customers:
            continue
        for item in items:
            if item.get('action') in ('update-three', 'update-trainee', 'reuse-empty-trainee', 'create-trainee'):
                match_id = item.get('formatId')
                match_identity = item.get('identity')
                if match_id and fmt.get('dbId') == match_id:
                    pass
                elif match_identity and fmt.get('identity') == match_identity:
                    pass
                elif item.get('action') == 'update-three' and (
                    fmt.get('identity') and any(
                        x.get('identity') == fmt.get('identity') for x in items if x.get('action') == 'update-three'
                    )
                ):
                    pass
                else:
                    # format 名で緩く合わせる
                    name = str(fmt.get('format') or '')
                    if item.get('action') == 'update-three' and ('ふりがな' in name or name == '大ネーム'):
                        if any(
                            str(f.get('format') or '') == ASHUKA_THREE_FORMAT_NAME
                            for f in (data.get('formats') or [])
                            if f.get('customerKey') in customers and f is not fmt
                        ):
                            continue
                    elif item.get('action') != 'update-three' and ('実習生' in name or 'カタカナ' in name):
                        pass
                    else:
                        continue
                kind = 'trainee' if 'trainee' in item.get('action', '') else 'three'
                fmt['format'] = ASHUKA_TRAINEE_FORMAT_NAME if kind == 'trainee' else ASHUKA_THREE_FORMAT_NAME
                fmt['placement'] = ASHUKA_PLACEMENT
                fmt['family'] = ASHUKA_FAMILY
                fmt['heading'] = ASHUKA_TRAINEE_HEADING if kind == 'trainee' else ASHUKA_THREE_HEADING
                fmt['note'] = ASHUKA_TRAINEE_NOTE if kind == 'trainee' else ASHUKA_THREE_NOTE
                fmt['layout'] = 'DeptName' if kind == 'trainee' else 'DeptFurigana'
                fmt['reading'] = (
                    'カタカナのファーストネーム（ふりがななし）' if kind == 'trainee' else '苗字のみ'
                )
                fmt['small'] = False
                fmt['widthMm'] = 80
                fmt['heightMm'] = 50
                fmt['fixedColor'] = '白'
                fmt['departments'] = list(ASHUKA_DEPARTMENTS)
                fmt['maximumFont'] = 50
                fmt['headingFont'] = 24
                fmt['sourceRow'] = 79 if kind == 'trainee' else 78
                for key, value in _ashuka_spec(kind).items():
                    fmt[key] = value
                rel = item.get('samplePath') or ''
                caption = ASHUKA_TRAINEE_SAMPLE_CAPTION if kind == 'trainee' else ASHUKA_THREE_SAMPLE_CAPTION
                fmt['samples'] = [{
                    'key': 'standard',
                    'condition': '',
                    'paperColor': '白',
                    'relativePath': rel,
                    'caption': caption,
                }]
                changed = True
                break
    # カタログに実習生用が無く、DB で新規作成した場合は追記
    has_trainee_cat = any(
        ('実習生' in str(fmt.get('format') or '') or 'カタカナ' in str(fmt.get('format') or '')
         or fmt.get('ashukaKind') == 'trainee')
        and fmt.get('customerKey') in customers
        for fmt in (data.get('formats') or [])
    )
    created = next((i for i in items if i.get('action') == 'create-trainee'), None)
    if created and not has_trainee_cat:
        customer_key = next(iter(customers))
        data.setdefault('formats', []).append({
            'identity': ASHUKA_TRAINEE_IDENTITY,
            'customerKey': customer_key,
            'format': ASHUKA_TRAINEE_FORMAT_NAME,
            'placement': ASHUKA_PLACEMENT,
            'family': ASHUKA_FAMILY,
            'heading': ASHUKA_TRAINEE_HEADING,
            'note': ASHUKA_TRAINEE_NOTE,
            'layout': 'DeptName',
            'reading': 'カタカナのファーストネーム（ふりがななし）',
            'small': False,
            'widthMm': 80,
            'heightMm': 50,
            'fixedColor': '白',
            'departments': list(ASHUKA_DEPARTMENTS),
            'maximumFont': 50,
            'headingFont': 24,
            'sourceRow': 79,
            **_ashuka_spec('trainee'),
            'samples': [{
                'key': 'standard',
                'condition': '',
                'paperColor': '白',
                'relativePath': created.get('samplePath') or '',
                'caption': ASHUKA_TRAINEE_SAMPLE_CAPTION,
            }],
        })
        changed = True
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return changed

def correct_ashuka_name_formats(c, data_dir=None):
    """明日佳の3段ネーム更新と実習生用2段追加。他顧客・担当者は変更しない。"""
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    customers = c.execute(
        'SELECT id, source_name FROM name_customers WHERE retired=0 AND source_name=?',
        (ASHUKA_CUSTOMER_NAME,),
    ).fetchall()
    if not customers:
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': '明日佳顧客なし',
            'customer': ASHUKA_CUSTOMER_NAME,
            'policy': ASHUKA_NAMES_POLICY,
        }

    prior = c.execute(
        'SELECT value FROM settings WHERE key=?',
        (ASHUKA_NAMES_SETTINGS_KEY,),
    ).fetchone()
    prior_policy = ''
    if prior:
        try:
            prior_policy = json.loads(prior['value'] or '{}').get('policy') or ''
        except (TypeError, json.JSONDecodeError):
            prior_policy = ''

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'ashuka-names-{stamp}'
    items = []
    customer = customers[0]
    formats = list(c.execute(
        'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
        (customer['id'],),
    ).fetchall())
    samples_by_fid = {}
    for fmt in formats:
        samples_by_fid[fmt['id']] = list(c.execute(
            'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
            (fmt['id'],),
        ).fetchall())

    three = None
    for fmt in formats:
        if fmt['id'] == ASHUKA_THREE_FORMAT_ID:
            three = fmt
            break
    if three is None:
        for fmt in formats:
            if _ashuka_format_matches_target(fmt, kind='three'):
                three = fmt
                break
    if three is None:
        # 顧客に大ネーム白地80×50が1つだけならそれを3段候補にする（空シェル229は除外）
        candidates = []
        for fmt in formats:
            if _ashuka_is_empty_shell(fmt, samples_by_fid.get(fmt['id'], [])):
                continue
            if _ashuka_format_matches_target(fmt, kind='trainee'):
                continue
            if str(fmt['format_name'] or '').startswith('大ネーム') and int(fmt['width_mm'] or 0) in (0, 80):
                candidates.append(fmt)
        if len(candidates) == 1:
            three = candidates[0]

    trainee = None
    for fmt in formats:
        if str(fmt['identity'] or '') == ASHUKA_TRAINEE_IDENTITY:
            trainee = fmt
            break
    if trainee is None:
        for fmt in formats:
            if three and fmt['id'] == three['id']:
                continue
            if _ashuka_format_matches_target(fmt, kind='trainee'):
                trainee = fmt
                break

    empty229 = None
    for fmt in formats:
        if fmt['id'] == ASHUKA_EMPTY_FORMAT_ID:
            if _ashuka_is_empty_shell(fmt, samples_by_fid.get(fmt['id'], [])):
                empty229 = fmt
            break

    needs = False
    if three is None or not _ashuka_three_is_current(three, samples_by_fid.get(three['id'], [])):
        needs = True
    if trainee is None:
        needs = True
    elif not _ashuka_trainee_is_current(trainee, samples_by_fid.get(trainee['id'], [])):
        needs = True

    catalog_path = root / 'name-import' / 'catalog.json'
    catalog_needs = False
    if catalog_path.is_file():
        try:
            catalog_data = json.loads(catalog_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            catalog_data = None
        if catalog_data:
            cat_keys = {
                item.get('sourceKey')
                for item in (catalog_data.get('customers') or [])
                if str(item.get('name') or '').strip() == ASHUKA_CUSTOMER_NAME
                or _correct_customer_name(item.get('name') or '') == ASHUKA_CUSTOMER_NAME
            }
            has_three = False
            has_trainee = False
            for fmt in catalog_data.get('formats') or []:
                if fmt.get('customerKey') not in cat_keys:
                    continue
                name = str(fmt.get('format') or '')
                if fmt.get('ashukaKind') == 'three' or 'ふりがな' in name:
                    has_three = True
                    if fmt.get('ashukaPolicy') != ASHUKA_NAMES_POLICY:
                        catalog_needs = True
                if fmt.get('ashukaKind') == 'trainee' or '実習生' in name or 'カタカナ' in name:
                    has_trainee = True
                    if fmt.get('ashukaPolicy') != ASHUKA_NAMES_POLICY:
                        catalog_needs = True
            if not has_three or not has_trainee:
                catalog_needs = True

    policy_needs = prior_policy != ASHUKA_NAMES_POLICY
    if not needs and not catalog_needs and not policy_needs:
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': '適用済み' if prior else '対象形式なし',
            'customer': ASHUKA_CUSTOMER_NAME,
            'policy': ASHUKA_NAMES_POLICY,
            'nm150': ASHUKA_THREE_FORMAT_ID,
            'nm229': ASHUKA_EMPTY_FORMAT_ID,
        }

    if not needs and not catalog_needs and policy_needs:
        c.execute(
            'INSERT OR REPLACE INTO settings VALUES(?,?)',
            (
                ASHUKA_NAMES_SETTINGS_KEY,
                json.dumps({
                    'at': stamp,
                    'changed': 0,
                    'report': ASHUKA_NAMES_REPORT,
                    'policy': ASHUKA_NAMES_POLICY,
                }, ensure_ascii=False),
            ),
        )
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': 'policy更新のみ',
            'customer': ASHUKA_CUSTOMER_NAME,
            'policy': ASHUKA_NAMES_POLICY,
        }

    # --- 3段ネーム ---
    if three is not None:
        samples = samples_by_fid.get(three['id'], [])
        if not _ashuka_three_is_current(three, samples):
            _backup_ashuka_format(c, root, backup_dir, three, samples)
            _update_ashuka_format_row(c, three['id'], kind='three', source_row=78)
            ok, note, rel = _write_ashuka_sample(
                c, root, three['id'], kind='three', caption=ASHUKA_THREE_SAMPLE_CAPTION,
            )
            items.append({
                'action': 'update-three',
                'customer': customer['source_name'],
                'formatId': three['id'],
                'number': _number(three['id']),
                'format': ASHUKA_THREE_FORMAT_NAME,
                'identity': three['identity'],
                'samplePath': rel,
                'sampleOk': ok,
                'sampleNote': note,
                'empty229Reused': False,
            })
    else:
        items.append({
            'action': 'skip-three',
            'customer': customer['source_name'],
            'reason': f'NM-{ASHUKA_THREE_FORMAT_ID:06}相当の3段形式が見つからない',
            'formatId': None,
        })

    # --- 実習生用2段 ---
    if trainee is not None:
        samples = samples_by_fid.get(trainee['id'], [])
        if not _ashuka_trainee_is_current(trainee, samples):
            _backup_ashuka_format(c, root, backup_dir, trainee, samples)
            _update_ashuka_format_row(c, trainee['id'], kind='trainee', source_row=79)
            ok, note, rel = _write_ashuka_sample(
                c, root, trainee['id'], kind='trainee', caption=ASHUKA_TRAINEE_SAMPLE_CAPTION,
            )
            items.append({
                'action': 'update-trainee',
                'customer': customer['source_name'],
                'formatId': trainee['id'],
                'number': _number(trainee['id']),
                'format': ASHUKA_TRAINEE_FORMAT_NAME,
                'identity': trainee['identity'],
                'samplePath': rel,
                'sampleOk': ok,
                'sampleNote': note,
                'empty229Reused': trainee['id'] == ASHUKA_EMPTY_FORMAT_ID,
            })
    elif empty229 is not None:
        samples = samples_by_fid.get(empty229['id'], [])
        _backup_ashuka_format(c, root, backup_dir, empty229, samples)
        _update_ashuka_format_row(
            c, empty229['id'], kind='trainee', source_row=79, identity=ASHUKA_TRAINEE_IDENTITY,
        )
        ok, note, rel = _write_ashuka_sample(
            c, root, empty229['id'], kind='trainee', caption=ASHUKA_TRAINEE_SAMPLE_CAPTION,
        )
        items.append({
            'action': 'reuse-empty-trainee',
            'customer': customer['source_name'],
            'formatId': empty229['id'],
            'number': _number(empty229['id']),
            'format': ASHUKA_TRAINEE_FORMAT_NAME,
            'identity': ASHUKA_TRAINEE_IDENTITY,
            'samplePath': rel,
            'sampleOk': ok,
            'sampleNote': note,
            'empty229Reused': True,
            'reason': 'NM-000229が内容未設定のため実習生用として再利用（重複追加回避）',
        })
    else:
        # 229に中身がある／他顧客／不在 → 新規（identity固定で再追加防止）
        existing_identity = c.execute(
            'SELECT id, customer_id, retired FROM name_formats WHERE identity=?',
            (ASHUKA_TRAINEE_IDENTITY,),
        ).fetchone()
        if existing_identity and existing_identity['customer_id'] == customer['id']:
            fid = existing_identity['id']
            c.execute('UPDATE name_formats SET retired=0 WHERE id=?', (fid,))
            fmt = c.execute('SELECT * FROM name_formats WHERE id=?', (fid,)).fetchone()
            samples = list(c.execute('SELECT * FROM name_samples WHERE format_id=? ORDER BY id', (fid,)))
            _backup_ashuka_format(c, root, backup_dir, fmt, samples)
            _update_ashuka_format_row(c, fid, kind='trainee', source_row=79)
            ok, note, rel = _write_ashuka_sample(
                c, root, fid, kind='trainee', caption=ASHUKA_TRAINEE_SAMPLE_CAPTION,
            )
            items.append({
                'action': 'update-trainee',
                'customer': customer['source_name'],
                'formatId': fid,
                'number': _number(fid),
                'format': ASHUKA_TRAINEE_FORMAT_NAME,
                'identity': ASHUKA_TRAINEE_IDENTITY,
                'samplePath': rel,
                'sampleOk': ok,
                'sampleNote': note,
                'empty229Reused': False,
                'reason': 'identity固定の既存実習生用を更新',
            })
        else:
            # 229が他用途なら触らず新規
            nm229 = c.execute(
                'SELECT id, customer_id, format_name FROM name_formats WHERE id=?',
                (ASHUKA_EMPTY_FORMAT_ID,),
            ).fetchone()
            skip_reason = ''
            if nm229 is not None:
                if nm229['customer_id'] != customer['id']:
                    skip_reason = 'NM-000229は他顧客のため未使用'
                else:
                    skip_reason = 'NM-000229に既存内容があるため未上書き・新規追加'
            else:
                skip_reason = 'NM-000229不在のため新規追加'
            fid = _insert_ashuka_trainee_format(c, customer['id'])
            ok, note, rel = _write_ashuka_sample(
                c, root, fid, kind='trainee', caption=ASHUKA_TRAINEE_SAMPLE_CAPTION,
            )
            items.append({
                'action': 'create-trainee',
                'customer': customer['source_name'],
                'formatId': fid,
                'number': _number(fid),
                'format': ASHUKA_TRAINEE_FORMAT_NAME,
                'identity': ASHUKA_TRAINEE_IDENTITY,
                'samplePath': rel,
                'sampleOk': ok,
                'sampleNote': note,
                'empty229Reused': False,
                'reason': skip_reason,
            })

    catalog_changed = _patch_ashuka_catalog(root, backup_dir, items) if items or catalog_needs else False
    changed_items = [i for i in items if i.get('action') not in ('skip-three',)]
    report = {
        'at': stamp,
        'changed': len(changed_items),
        'customer': ASHUKA_CUSTOMER_NAME,
        'policy': ASHUKA_NAMES_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'catalogChanged': catalog_changed,
        'departments': list(ASHUKA_DEPARTMENTS),
        'rules': {
            'onlyCustomer': ASHUKA_CUSTOMER_NAME,
            'updateThree': True,
            'preferFormatId': ASHUKA_THREE_FORMAT_ID,
            'reuseEmpty229': True,
            'traineeIdentity': ASHUKA_TRAINEE_IDENTITY,
            'noAssigneeChange': True,
            'noAutoApprove': True,
            'noDeleteOtherFormats': True,
        },
        'items': items,
    }
    report_path = root / ASHUKA_NAMES_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if backup_dir.exists():
        (backup_dir / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            ASHUKA_NAMES_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': len(changed_items),
                'report': ASHUKA_NAMES_REPORT,
                'policy': ASHUKA_NAMES_POLICY,
            }, ensure_ascii=False),
        ),
    )
    return report

# 3社ネーム追加：最新 Excel「ネーム仕分け_五十音順2」Sheet1
# 行25–27 エア・ウォーター・アグリ／行122–123 十勝ミートセンター／行158 ベル・クール研究所
# - 対象顧客のみ作成・更新。他顧客は変更しない。
# - identity 固定で重複追加しない。担当者・了承は触らない。
THREE_NAMES_POLICY = 'excel-gojuon2-r25-27-122-123-158-v1'
THREE_NAMES_SETTINGS_KEY = 'three_customer_name_formats'
THREE_NAMES_REPORT = 'three-customer-name-formats-report.json'
THREE_FAMILY = 'HG丸ゴシック'
# 小ネーム・苗字のみ・36pt（丸ゴシック36 / Pゴシック36）の exact-pt 見本方針
SMALL_SURNAME_36_POLICY = 'exact-pt-v1'
SMALL_SURNAME_36_SETTINGS_KEY = 'small_surname_36_exact_pt'
SMALL_SURNAME_36_REPORT = 'small-surname-36-exact-pt-report.json'
SMALL_SURNAME_36_DEFAULT_TEXT = '山田'
THREE_LARGE_W, THREE_LARGE_H = 80, 50
THREE_SMALL_W, THREE_SMALL_H = 50, 24
THREE_GENERIC_LARGE = {4: 48, 5: 38, 6: 30}  # B2 大汎用
THREE_GENERIC_SMALL = {4: 26, 5: 24, 6: 20}  # B2 小汎用
THREE_TOKACHI_SURNAME_PT = {2: 80, 3: 64, 'collision': 56}  # Excel「2文字：80　3文字：64　被：56」
THREE_SAMPLE_FULL = '山田 太郎'
THREE_SAMPLE_FULL_FEMALE = '山田 花子'  # 女性用フルネーム見本の規約（本関数の既定見本は男性）
THREE_SAMPLE_SURNAME = '山田'
THREE_PAPER_RGB = {
    '白': (255, 255, 255),
    '緑': (144, 238, 144),  # UI swatch #90EE90
}
THREE_OPEN_ITEMS = [
    '大／小ネームの実寸は Excel に mm が無いため、既存どおり大80×50・小50×24を採用',
    '1段・段サイズ未記入行の配置は横中央・縦中央と仮定',
    '「白下：左前(逆さ)」は貼付向きの注記として扱い、見本画像は正立のまま',
    '十勝「被：56」は備考どおり苗字被り時。見本は苗字「山田」（2文字→80pt）',
    'エア・ウォーター顧客名は「エア・ウォーター・アグリ」。更別工場/札幌工場は備考へ',
    'HG丸ゴシック太字は環境フォント次第（太字指定は備考・captionに記録）',
]


def _three_char_count(text):
    return len(re.sub(r'\s+', '', str(text or '')))


def _three_generic_pt(kind, text):
    n = _three_char_count(text)
    table = THREE_GENERIC_LARGE if kind == 'large' else THREE_GENERIC_SMALL
    if n <= 4:
        return table[4]
    if n == 5:
        return table[5]
    return table[6]


def _three_tokachi_surname_pt(text):
    n = _three_char_count(text)
    if n <= 2:
        return THREE_TOKACHI_SURNAME_PT[2]
    if n == 3:
        return THREE_TOKACHI_SURNAME_PT[3]
    return THREE_TOKACHI_SURNAME_PT['collision']


def _three_band(text, *, height_mm, font_pt):
    return {
        'role': 'name',
        'text': text,
        'heightMm': float(height_mm),
        'fontPt': float(font_pt),
        'alignH': 'center',
        'alignV': 'middle',
        'indentMm': 0.0,
    }


def _three_format_specs():
    """Excel 行ごとの形式定義。identity は再追加防止用に固定。"""
    air_site = '更別工場/札幌工場'
    air_note_base = (
        f'拠点：{air_site}。書体は{THREE_FAMILY}（太字）。'
        'Excel 行25–27（ネーム仕分け_五十音順2）。'
    )
    tokachi_note = (
        f'書体は{THREE_FAMILY}（太字）。用紙色は白 or 緑。'
        '苗字被りは名前の一文字目をつける（被：56pt）。'
        '大ネーム文字サイズ：2文字80／3文字64／被56。'
        'Excel 行122–123。'
    )
    bell_note = (
        f'書体は{THREE_FAMILY}（太字）。用紙色は白。'
        '1段・フルネーム／小汎用（4文字26・5文字24・6文字20）。'
        'Excel 行158。'
    )
    full_large_pt = _three_generic_pt('large', THREE_SAMPLE_FULL)
    full_small_pt = _three_generic_pt('small', THREE_SAMPLE_FULL)
    surname_tokachi_pt = _three_tokachi_surname_pt(THREE_SAMPLE_SURNAME)
    return (
        {
            'customerKey': 'excel:airwater-agri',
            'customerName': 'エア・ウォーター・アグリ',
            'reading': 'えあうぉーたーあぐり',
            'formats': (
                {
                    'identity': 'airwater-agri:large-fullname',
                    'kind': 'air-large-fullname',
                    'sourceRow': 25,
                    'formatName': '大ネーム（フルネーム）',
                    'placement': '白衣：左袖25cm',
                    'heading': 'フルネーム（大汎用）',
                    'note': air_note_base + '大ネーム・1段・フルネーム／大汎用。',
                    'layout': 'Name',
                    'reading': 'フルネーム',
                    'small': 0,
                    'widthMm': THREE_LARGE_W,
                    'heightMm': THREE_LARGE_H,
                    'fixedColor': '白',
                    'colorCaption': '白',
                    'maximumFont': THREE_GENERIC_LARGE[4],
                    'sampleText': THREE_SAMPLE_FULL,
                    'fontPt': full_large_pt,
                    'papers': ('白',),
                    'caption': (
                        f'見本：フルネーム「{THREE_SAMPLE_FULL}」（大汎用・1段）。'
                        f'書体は{THREE_FAMILY}（太字）。用紙色は白。'
                        f'女性用フルネームは「{THREE_SAMPLE_FULL_FEMALE}」。'
                    ),
                },
                {
                    'identity': 'airwater-agri:small-pants-fullname',
                    'kind': 'air-small-pants-fullname',
                    'sourceRow': 26,
                    'formatName': '小ネーム（フルネーム・ズボン）',
                    'placement': '白下：左前(逆さ)',
                    'heading': 'フルネーム（小汎用）',
                    'note': air_note_base + '小ネーム・1段・フルネーム／小汎用。貼付は逆さ。',
                    'layout': 'Name',
                    'reading': 'フルネーム',
                    'small': 1,
                    'widthMm': THREE_SMALL_W,
                    'heightMm': THREE_SMALL_H,
                    'fixedColor': '白',
                    'colorCaption': '白',
                    'maximumFont': THREE_GENERIC_SMALL[4],
                    'sampleText': THREE_SAMPLE_FULL,
                    'fontPt': full_small_pt,
                    'papers': ('白',),
                    'caption': (
                        f'見本：フルネーム「{THREE_SAMPLE_FULL}」（小汎用・1段）。'
                        f'書体は{THREE_FAMILY}（太字）。用紙色は白。貼付：白下左前（逆さ）。'
                        f'女性用フルネームは「{THREE_SAMPLE_FULL_FEMALE}」。'
                    ),
                },
                {
                    'identity': 'airwater-agri:small-hat-surname',
                    'kind': 'air-small-hat-surname',
                    'sourceRow': 27,
                    'formatName': '小ネーム（苗字・帽子）',
                    'placement': '帽子：左耳上＋右耳上',
                    'heading': '苗字のみ（36pt）',
                    'note': air_note_base + '小ネーム・1段・苗字のみ／36pt。',
                    'layout': 'Surname',
                    'reading': '苗字のみ',
                    'small': 1,
                    'widthMm': THREE_SMALL_W,
                    'heightMm': THREE_SMALL_H,
                    'fixedColor': '白',
                    'colorCaption': '白',
                    'maximumFont': 36,
                    'sampleText': THREE_SAMPLE_SURNAME,
                    'fontPt': 36,
                    'sampleFit': 'exact',
                    'papers': ('白',),
                    'caption': (
                        f'見本：苗字「{THREE_SAMPLE_SURNAME}」（36pt・1段）。'
                        f'書体は{THREE_FAMILY}（太字）。用紙色は白。'
                    ),
                },
            ),
        },
        {
            'customerKey': 'excel:tokachi-meat',
            'customerName': '十勝ミートセンター',
            'reading': 'とかちみーとせんたー',
            'formats': (
                {
                    'identity': 'tokachi-meat:large-surname',
                    'kind': 'tokachi-large-surname',
                    'sourceRow': 122,
                    'formatName': '大ネーム（苗字のみ）',
                    'placement': '白衣：左袖25cm',
                    'heading': '苗字のみ（2文字80／3文字64／被56）',
                    'note': tokachi_note,
                    'layout': 'Surname',
                    'reading': '苗字のみ',
                    'small': 0,
                    'widthMm': THREE_LARGE_W,
                    'heightMm': THREE_LARGE_H,
                    'fixedColor': '白 or 緑',
                    'colorCaption': '白 or 緑',
                    'groups': ['用紙色', '用紙色'],
                    'colors': ['白', '緑'],
                    'swatches': ['#FFFFFF', '#90EE90'],
                    'maximumFont': THREE_TOKACHI_SURNAME_PT[2],
                    'sampleText': THREE_SAMPLE_SURNAME,
                    'fontPt': surname_tokachi_pt,
                    'papers': ('白', '緑'),
                    'caption': (
                        f'見本：苗字「{THREE_SAMPLE_SURNAME}」（2文字→{surname_tokachi_pt}pt）。'
                        f'書体は{THREE_FAMILY}（太字）。用紙色は白 or 緑。'
                        '苗字被りは名前の一文字目（被：56pt）。'
                    ),
                },
                {
                    'identity': 'tokachi-meat:small-surname',
                    'kind': 'tokachi-small-surname',
                    'sourceRow': 123,
                    'formatName': '小ネーム（苗字のみ）',
                    'placement': '白下：左上　帽子：右耳上',
                    'heading': '苗字のみ（36pt）',
                    'note': tokachi_note + '小ネームは36pt。',
                    'layout': 'Surname',
                    'reading': '苗字のみ',
                    'small': 1,
                    'widthMm': THREE_SMALL_W,
                    'heightMm': THREE_SMALL_H,
                    'fixedColor': '白 or 緑',
                    'colorCaption': '白 or 緑',
                    'groups': ['用紙色', '用紙色'],
                    'colors': ['白', '緑'],
                    'swatches': ['#FFFFFF', '#90EE90'],
                    'maximumFont': 36,
                    'sampleText': THREE_SAMPLE_SURNAME,
                    'fontPt': 36,
                    'sampleFit': 'exact',
                    'papers': ('白', '緑'),
                    'caption': (
                        f'見本：苗字「{THREE_SAMPLE_SURNAME}」（36pt・1段）。'
                        f'書体は{THREE_FAMILY}（太字）。用紙色は白 or 緑。'
                        '苗字被りは名前の一文字目をつける。'
                    ),
                },
            ),
        },
        {
            'customerKey': 'excel:bellcool',
            'customerName': 'ベル・クール研究所',
            'reading': 'べるくーるけんきゅうじょ',
            'formats': (
                {
                    'identity': 'bellcool:small-fullname',
                    'kind': 'bell-small-fullname',
                    'sourceRow': 158,
                    'formatName': '小ネーム（フルネーム）',
                    'placement': '白衣：左袖25cm',
                    'heading': 'フルネーム（小汎用）',
                    'note': bell_note,
                    'layout': 'Name',
                    'reading': 'フルネーム',
                    'small': 1,
                    'widthMm': THREE_SMALL_W,
                    'heightMm': THREE_SMALL_H,
                    'fixedColor': '白',
                    'colorCaption': '白',
                    'maximumFont': THREE_GENERIC_SMALL[4],
                    'sampleText': THREE_SAMPLE_FULL,
                    'fontPt': full_small_pt,
                    'papers': ('白',),
                    'caption': (
                        f'見本：フルネーム「{THREE_SAMPLE_FULL}」（小汎用・1段）。'
                        f'書体は{THREE_FAMILY}（太字）。用紙色は白。'
                        f'女性用フルネームは「{THREE_SAMPLE_FULL_FEMALE}」。'
                    ),
                },
            ),
        },
    )


def _three_spec(fmt_def):
    spec = {
        'threeKind': fmt_def['kind'],
        'threePolicy': THREE_NAMES_POLICY,
        'sampleText': fmt_def['sampleText'],
        'fontPt': fmt_def['fontPt'],
        'papers': list(fmt_def['papers']),
        'genericFontsPt': dict(
            THREE_GENERIC_SMALL if fmt_def['small'] else THREE_GENERIC_LARGE
        ),
        'tokachiSurnamePt': dict(THREE_TOKACHI_SURNAME_PT) if 'tokachi' in fmt_def['kind'] else None,
        'femaleFullName': THREE_SAMPLE_FULL_FEMALE if fmt_def['reading'] == 'フルネーム' else None,
    }
    if fmt_def.get('sampleFit'):
        spec['sampleFit'] = fmt_def['sampleFit']
        if fmt_def['sampleFit'] == 'exact':
            spec['exactPtPolicy'] = SMALL_SURNAME_36_POLICY
    return spec


def _three_match_customer_name(name, target):
    text = str(name or '').replace('\n', ' ').replace('\r', ' ').strip()
    return text == target or text.startswith(target)


def _three_find_customer(c, customer_def):
    rows = c.execute(
        'SELECT * FROM name_customers WHERE retired=0 ORDER BY id'
    ).fetchall()
    for row in rows:
        if str(row['source_key'] or '') == customer_def['customerKey']:
            return row
    for row in rows:
        if _three_match_customer_name(row['source_name'], customer_def['customerName']):
            return row
    return None


def _three_ensure_customer(c, customer_def, backup_dir, items):
    existing = _three_find_customer(c, customer_def)
    if existing:
        return existing, False
    known = _app_customers(c)
    linked, status = _link(customer_def['customerName'], known)
    c.execute(
        '''INSERT INTO name_customers(
            source_key, source_index, source_name, reading, linked_name, link_status, manual, retired
           ) VALUES(?,?,?,?,?,?,1,0)''',
        (
            customer_def['customerKey'], -1, customer_def['customerName'],
            customer_def.get('reading') or '', linked, status,
        ),
    )
    cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    row = c.execute('SELECT * FROM name_customers WHERE id=?', (cid,)).fetchone()
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / f'customer-created-{cid}.json').write_text(
        json.dumps(_row_as_dict(row), ensure_ascii=False, indent=2), encoding='utf-8',
    )
    items.append({
        'action': 'create-customer',
        'customerId': cid,
        'customerName': customer_def['customerName'],
        'sourceKey': customer_def['customerKey'],
    })
    return row, True


def _three_is_current(fmt, samples, fmt_def):
    if str(fmt['format_name'] or '') != fmt_def['formatName']:
        return False
    if str(fmt['placement'] or '') != fmt_def['placement']:
        return False
    if str(fmt['family'] or '') != THREE_FAMILY:
        return False
    if str(fmt['heading'] or '') != fmt_def['heading']:
        return False
    if str(fmt['note'] or '') != fmt_def['note']:
        return False
    if str(fmt['layout'] or '') != fmt_def['layout']:
        return False
    if str(fmt['reading'] or '') != fmt_def['reading']:
        return False
    if int(fmt['small'] or 0) != int(fmt_def['small']):
        return False
    if int(fmt['width_mm'] or 0) != int(fmt_def['widthMm']):
        return False
    if int(fmt['height_mm'] or 0) != int(fmt_def['heightMm']):
        return False
    if str(fmt['fixed_color'] or '') != fmt_def['fixedColor']:
        return False
    if str(fmt['color_caption'] or '') != fmt_def['colorCaption']:
        return False
    if float(fmt['maximum_font'] or 0) != float(fmt_def['maximumFont']):
        return False
    if int(fmt['source_row'] or 0) != int(fmt_def['sourceRow']):
        return False
    spec = _load_spec(fmt)
    if spec.get('threePolicy') != THREE_NAMES_POLICY or spec.get('threeKind') != fmt_def['kind']:
        return False
    want_fit = fmt_def.get('sampleFit') or ''
    have_fit = spec.get('sampleFit') or ''
    if want_fit != have_fit:
        return False
    want_groups = fmt_def.get('groups') or []
    want_colors = fmt_def.get('colors') or []
    try:
        have_groups = json.loads(fmt['groups_json'] or '[]')
        have_colors = json.loads(fmt['colors_json'] or '[]')
    except (TypeError, json.JSONDecodeError):
        return False
    if have_groups != want_groups or have_colors != want_colors:
        return False
    if len(samples) != len(fmt_def['papers']):
        return False
    by_paper = {str(s['paper_color'] or ''): s for s in samples}
    for paper in fmt_def['papers']:
        sample = by_paper.get(paper)
        if sample is None:
            return False
        want_key = _three_variant_key(fmt_def['papers'], paper)
        if str(sample['variant_key'] or '') != want_key:
            return False
        rel = str(sample['relative_path'] or '')
        if not rel.startswith('images/names/'):
            return False
        if fmt_def['kind'] not in Path(rel).name:
            return False
        if str(sample['caption'] or '') != fmt_def['caption']:
            return False
    return True


def _three_variant_key(papers, paper):
    if len(papers) == 1:
        return 'standard'
    return f'paper-{paper}'


def _backup_three_format(c, root, backup_dir, fmt, samples):
    backup_dir.mkdir(parents=True, exist_ok=True)
    images_backup = backup_dir / 'images'
    images_backup.mkdir(parents=True, exist_ok=True)
    payload = {
        'format': _row_as_dict(fmt),
        'samples': [_row_as_dict(s) for s in samples],
    }
    (backup_dir / f'format-{fmt["id"]}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    for sample in samples:
        rel = str(sample['relative_path'] or '')
        if not rel:
            continue
        src = root / rel
        if src.is_file():
            dest = images_backup / f'{fmt["id"]}-{Path(rel).name}'
            if not dest.exists():
                shutil.copy2(src, dest)


def _write_three_samples(c, root, format_id, fmt_def):
    band = _three_band(
        fmt_def['sampleText'],
        height_mm=fmt_def['heightMm'],
        font_pt=fmt_def['fontPt'],
    )
    fit_mode = fmt_def.get('sampleFit') or _sample_fit_mode_for_format(
        {
            'small': fmt_def['small'],
            'layout': fmt_def['layout'],
            'reading': fmt_def['reading'],
            'family': THREE_FAMILY,
            'maximumFont': fmt_def['maximumFont'],
            'fontPt': fmt_def['fontPt'],
        },
        font_pt=fmt_def['fontPt'],
    )
    c.execute('DELETE FROM name_samples WHERE format_id=?', (format_id,))
    notes = []
    paths = []
    for paper in fmt_def['papers']:
        key = _three_variant_key(fmt_def['papers'], paper)
        stem = f'{fmt_def["kind"]}-{paper}' if len(fmt_def['papers']) > 1 else fmt_def['kind']
        rel = f'images/names/{stem}-{format_id:06}.png'
        dst = root / rel
        rgb = THREE_PAPER_RGB.get(paper, (255, 255, 255))
        ok, note = _render_ashuka_banded_sample(
            dst, (band,),
            width_mm=fmt_def['widthMm'],
            height_mm=fmt_def['heightMm'],
            family_hint=THREE_FAMILY,
            paper_rgb=rgb,
            fit_mode=fit_mode,
        )
        notes.append(f'{paper}:{note}')
        if not ok:
            return False, '; '.join(notes), paths
        c.execute(
            '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
               VALUES(?,?,?,?,?,?)''',
            (format_id, key, paper if len(fmt_def['papers']) > 1 else '', paper, rel, fmt_def['caption']),
        )
        paths.append(rel)
    return True, '; '.join(notes), paths


def _upsert_three_format_row(c, customer_id, fmt_def, existing=None):
    groups = json.dumps(fmt_def.get('groups') or [], ensure_ascii=False)
    colors = json.dumps(fmt_def.get('colors') or [], ensure_ascii=False)
    swatches = json.dumps(fmt_def.get('swatches') or [], ensure_ascii=False)
    spec = json.dumps(_three_spec(fmt_def), ensure_ascii=False)
    values = (
        fmt_def['formatName'], fmt_def['placement'], THREE_FAMILY, '',
        fmt_def['heading'], fmt_def['note'], fmt_def['layout'], fmt_def['reading'],
        int(fmt_def['small']), int(fmt_def['widthMm']), int(fmt_def['heightMm']), 0,
        fmt_def['fixedColor'], fmt_def['colorCaption'],
        groups, colors, swatches, 'null',
        '', float(fmt_def['maximumFont']), 0, 0, int(fmt_def['sourceRow']),
        spec, 0, '[]', 0,
    )
    if existing:
        c.execute(
            '''UPDATE name_formats SET format_name=?, placement=?, family=?, header_family=?, heading=?, note=?,
                layout=?, reading=?, small=?, width_mm=?, height_mm=?, unknown_size=?, fixed_color=?, color_caption=?,
                groups_json=?, colors_json=?, swatches_json=?, departments_json=?, pending=?, maximum_font=?,
                heading_font=?, number_length=?, source_row=?, spec_json=?, needs_review=?, review_json=?, blank=?,
                retired=0, identity=? WHERE id=?''',
            values + (fmt_def['identity'], existing['id']),
        )
        return existing['id']
    c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, family, header_family, heading, note,
            layout, reading, small, width_mm, height_mm, unknown_size, fixed_color, color_caption,
            groups_json, colors_json, swatches_json, departments_json, pending, maximum_font, heading_font,
            number_length, source_row, previous_source_row, spec_json, needs_review, review_json, blank, manual, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)''',
        (
            fmt_def['identity'], customer_id, -1,
            values[0], values[1], values[2], values[3], values[4], values[5],
            values[6], values[7], values[8], values[9], values[10], values[11],
            values[12], values[13], values[14], values[15], values[16], values[17],
            values[18], values[19], values[20], values[21], values[22], None,
            values[23], values[24], values[25], values[26],
        ),
    )
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _patch_three_catalog(data_dir, backup_dir, customer_defs, items):
    path = Path(data_dir) / 'name-import' / 'catalog.json'
    if not path.is_file():
        return False
    backup_dir.mkdir(parents=True, exist_ok=True)
    catalog_backup = backup_dir / 'catalog.json'
    if not catalog_backup.exists():
        shutil.copy2(path, catalog_backup)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    customers = list(data.get('customers') or [])
    formats = list(data.get('formats') or [])
    by_key = {c.get('sourceKey'): c for c in customers}
    changed = False
    item_by_identity = {i.get('identity'): i for i in items if i.get('identity')}
    for cust in customer_defs:
        if cust['customerKey'] not in by_key:
            customers.append({
                'sourceKey': cust['customerKey'],
                'sourceIndex': -1,
                'name': cust['customerName'],
                'reading': cust.get('reading') or '',
            })
            by_key[cust['customerKey']] = customers[-1]
            changed = True
        else:
            entry = by_key[cust['customerKey']]
            if entry.get('name') != cust['customerName']:
                entry['name'] = cust['customerName']
                changed = True
        for fmt_def in cust['formats']:
            item = item_by_identity.get(fmt_def['identity'])
            existing = next(
                (f for f in formats if f.get('identity') == fmt_def['identity']),
                None,
            )
            payload = {
                'identity': fmt_def['identity'],
                'customerKey': cust['customerKey'],
                'sourceKind': -1,
                'format': fmt_def['formatName'],
                'placement': fmt_def['placement'],
                'family': THREE_FAMILY,
                'heading': fmt_def['heading'],
                'note': fmt_def['note'],
                'layout': fmt_def['layout'],
                'reading': fmt_def['reading'],
                'small': bool(fmt_def['small']),
                'widthMm': fmt_def['widthMm'],
                'heightMm': fmt_def['heightMm'],
                'fixedColor': fmt_def['fixedColor'],
                'colorCaption': fmt_def['colorCaption'],
                'groups': list(fmt_def.get('groups') or []),
                'colors': list(fmt_def.get('colors') or []),
                'colorSwatches': list(fmt_def.get('swatches') or []),
                'maximumFont': fmt_def['maximumFont'],
                'headingFont': 0,
                'sourceRow': fmt_def['sourceRow'],
                'manual': True,
                **{k: v for k, v in _three_spec(fmt_def).items() if v is not None},
                'samples': [],
            }
            if item:
                payload['dbId'] = item.get('formatId')
                paths = item.get('samplePaths') or (
                    [item['samplePath']] if item.get('samplePath') else []
                )
                for idx, paper in enumerate(fmt_def['papers']):
                    rel = paths[idx] if idx < len(paths) else ''
                    payload['samples'].append({
                        'key': _three_variant_key(fmt_def['papers'], paper),
                        'condition': paper if len(fmt_def['papers']) > 1 else '',
                        'paperColor': paper,
                        'relativePath': rel,
                        'caption': fmt_def['caption'],
                    })
            if existing is None:
                formats.append(payload)
                changed = True
            else:
                for key, value in payload.items():
                    if existing.get(key) != value:
                        existing[key] = value
                        changed = True
    if changed:
        data['customers'] = customers
        data['formats'] = formats
        data['customerCount'] = len(customers)
        data['formatCount'] = len(formats)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return changed


def ensure_three_customer_name_formats(c, data_dir=None):
    """Excel 対象3社のネーム形式を追加／更新する。他顧客は変更しない。"""
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'three-names-{stamp}'
    customer_defs = _three_format_specs()
    items = []
    open_items = list(THREE_OPEN_ITEMS)

    # 既に policy 適用済みで変更不要ならバックアップディレクトリを作らない
    needs_work = False
    for cust in customer_defs:
        customer = _three_find_customer(c, cust)
        if customer is None:
            needs_work = True
            break
        for fmt_def in cust['formats']:
            row = c.execute(
                'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                (fmt_def['identity'],),
            ).fetchone()
            if row is None:
                # 同顧客・同配置の既存があれば再利用判定へ
                needs_work = True
                break
            samples = c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (row['id'],),
            ).fetchall()
            if not _three_is_current(row, samples, fmt_def):
                needs_work = True
                break
        if needs_work:
            break

    if not needs_work:
        report = {
            'at': stamp,
            'changed': 0,
            'policy': THREE_NAMES_POLICY,
            'backupDir': '',
            'catalogChanged': False,
            'openItems': open_items,
            'customers': [cdef['customerName'] for cdef in customer_defs],
            'items': [],
            'reason': 'already-applied',
        }
        (root / THREE_NAMES_REPORT).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        c.execute(
            'INSERT OR REPLACE INTO settings VALUES(?,?)',
            (
                THREE_NAMES_SETTINGS_KEY,
                json.dumps({
                    'at': stamp,
                    'changed': 0,
                    'report': THREE_NAMES_REPORT,
                    'policy': THREE_NAMES_POLICY,
                }, ensure_ascii=False),
            ),
        )
        return report

    backup_dir.mkdir(parents=True, exist_ok=True)

    for cust in customer_defs:
        customer, created = _three_ensure_customer(c, cust, backup_dir, items)
        for fmt_def in cust['formats']:
            by_identity = c.execute(
                'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                (fmt_def['identity'],),
            ).fetchone()
            existing = by_identity
            if existing is None:
                # 同顧客で同一 placement+reading+small の既存があれば再利用（重複回避）
                candidates = c.execute(
                    '''SELECT * FROM name_formats WHERE customer_id=? AND retired=0
                       AND placement=? AND reading=? AND small=? ORDER BY id''',
                    (
                        customer['id'], fmt_def['placement'], fmt_def['reading'],
                        int(fmt_def['small']),
                    ),
                ).fetchall()
                if len(candidates) == 1:
                    existing = candidates[0]
                elif len(candidates) > 1:
                    # 形式名が近いものを優先
                    for cand in candidates:
                        name = str(cand['format_name'] or '')
                        if fmt_def['formatName'] in name or name in fmt_def['formatName']:
                            existing = cand
                            break
                    if existing is None:
                        existing = candidates[0]

            samples = []
            if existing:
                samples = c.execute(
                    'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                    (existing['id'],),
                ).fetchall()
                if _three_is_current(existing, samples, fmt_def):
                    items.append({
                        'action': 'skip',
                        'kind': fmt_def['kind'],
                        'formatId': existing['id'],
                        'number': _number(existing['id']),
                        'identity': fmt_def['identity'],
                        'customerName': cust['customerName'],
                        'reason': 'already-current',
                    })
                    continue
                _backup_three_format(c, root, backup_dir, existing, samples)

            fid = _upsert_three_format_row(c, customer['id'], fmt_def, existing=existing)
            ok, note, paths = _write_three_samples(c, root, fid, fmt_def)
            items.append({
                'action': 'create' if existing is None else 'update',
                'kind': fmt_def['kind'],
                'formatId': fid,
                'number': _number(fid),
                'identity': fmt_def['identity'],
                'customerName': cust['customerName'],
                'sourceRow': fmt_def['sourceRow'],
                'samplePaths': paths,
                'samplePath': paths[0] if paths else '',
                'sampleOk': ok,
                'sampleNote': note,
                'customerCreated': created,
            })

    catalog_changed = _patch_three_catalog(root, backup_dir, customer_defs, items)
    changed = sum(1 for i in items if i.get('action') in ('create', 'update', 'create-customer'))
    report = {
        'at': stamp,
        'changed': changed,
        'policy': THREE_NAMES_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'catalogChanged': catalog_changed,
        'openItems': open_items,
        'customers': [cdef['customerName'] for cdef in customer_defs],
        'excel': {
            'path': 'media/ネーム仕分け_五十音順2.xlsx',
            'rows': {
                'エア・ウォーター・アグリ': [25, 26, 27],
                '十勝ミートセンター': [122, 123],
                'ベル・クール研究所': [158],
            },
        },
        'sampleNames': {
            'fullMale': THREE_SAMPLE_FULL,
            'fullFemale': THREE_SAMPLE_FULL_FEMALE,
            'surname': THREE_SAMPLE_SURNAME,
        },
        'rules': {
            'onlyThreeCustomers': True,
            'createIfMissing': True,
            'noDuplicateIdentities': True,
            'noAssigneeChange': True,
            'noAutoApprove': True,
            'noTouchOtherCustomers': True,
        },
        'items': items,
    }
    report_path = root / THREE_NAMES_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (backup_dir / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            THREE_NAMES_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': changed,
                'report': THREE_NAMES_REPORT,
                'policy': THREE_NAMES_POLICY,
                'backupDir': report['backupDir'],
            }, ensure_ascii=False),
        ),
    )
    return report


# --- エフケー（Excel 行29）ネーム形式 ---
FK_NAMES_POLICY = 'excel-gojuon2-r29-v1'
FK_NAMES_SETTINGS_KEY = 'fk_name_formats'
FK_NAMES_REPORT = 'fk-name-formats-report.json'
FK_CUSTOMER_KEY = 'excel:fk'
FK_CUSTOMER_NAME = 'エフケー'
FK_CUSTOMER_READING = 'えふけー'
FK_IDENTITY = 'fk:small-surname'
FK_KIND = 'fk-small-surname'
FK_SOURCE_ROW = 29
FK_FAMILY = THREE_FAMILY
FK_PLACEMENT = '白衣：左袖25cm　白下：左前　白帽子：右耳上'
FK_FORMAT_NAME = '小ネーム（苗字のみ）'
FK_HEADING = '苗字のみ（36pt）'
FK_COLOR_CAPTION = '社員：緑　パート：青'
FK_GROUPS = ['社員', 'パート']
FK_COLORS = ['緑', '青']
FK_SWATCHES = ['#90EE90', '#87CEFA']
FK_PAPERS = ('緑', '青')
FK_PAPER_RGB = {
    '緑': (144, 238, 144),  # #90EE90
    '青': (135, 206, 250),  # #87CEFA
}
FK_SAMPLE_SURNAME = THREE_SAMPLE_SURNAME
FK_MAXIMUM_FONT = 36
FK_WIDTH_MM, FK_HEIGHT_MM = THREE_SMALL_W, THREE_SMALL_H
FK_NOTE = (
    f'書体は{FK_FAMILY}（太字）。色は社員：緑／パート：青。'
    '小ネーム・1段・苗字のみ／36pt。'
    f'Excel 行{FK_SOURCE_ROW}（ネーム仕分け_五十音順2）。'
)
FK_CAPTION = (
    f'見本：苗字「{FK_SAMPLE_SURNAME}」（36pt・1段）。'
    f'書体は{FK_FAMILY}（太字）。色は社員：緑／パート：青。'
)
FK_OPEN_ITEMS = [
    '小ネーム実寸は Excel に mm が無いため、既存どおり 50×24 を採用',
    '1段・段サイズ未記入は横中央・縦中央と仮定',
    'HG丸ゴシック太字は環境フォント次第（太字指定は備考・captionに記録）',
]


def _fk_format_def():
    return {
        'identity': FK_IDENTITY,
        'kind': FK_KIND,
        'sourceRow': FK_SOURCE_ROW,
        'formatName': FK_FORMAT_NAME,
        'placement': FK_PLACEMENT,
        'heading': FK_HEADING,
        'note': FK_NOTE,
        'layout': 'Surname',
        'reading': '苗字のみ',
        'small': 1,
        'widthMm': FK_WIDTH_MM,
        'heightMm': FK_HEIGHT_MM,
        'fixedColor': '',
        'colorCaption': FK_COLOR_CAPTION,
        'groups': list(FK_GROUPS),
        'colors': list(FK_COLORS),
        'swatches': list(FK_SWATCHES),
        'maximumFont': FK_MAXIMUM_FONT,
        'sampleText': FK_SAMPLE_SURNAME,
        'fontPt': FK_MAXIMUM_FONT,
        'sampleFit': 'exact',
        'papers': FK_PAPERS,
        'caption': FK_CAPTION,
    }


def _fk_spec(fmt_def):
    return {
        'fkKind': fmt_def['kind'],
        'fkPolicy': FK_NAMES_POLICY,
        'surnameOnly': True,
        'sampleText': fmt_def['sampleText'],
        'fontPt': fmt_def['fontPt'],
        'sampleFit': fmt_def.get('sampleFit') or 'exact',
        'exactPtPolicy': SMALL_SURNAME_36_POLICY,
        'papers': list(fmt_def['papers']),
        'colorRoles': dict(zip(FK_GROUPS, FK_COLORS)),
    }


def _fk_customer_def():
    return {
        'customerKey': FK_CUSTOMER_KEY,
        'customerName': FK_CUSTOMER_NAME,
        'reading': FK_CUSTOMER_READING,
        'formats': (_fk_format_def(),),
    }


def _fk_find_customer(c):
    rows = c.execute(
        'SELECT * FROM name_customers WHERE retired=0 ORDER BY id'
    ).fetchall()
    for row in rows:
        if str(row['source_key'] or '') == FK_CUSTOMER_KEY:
            return row
    for row in rows:
        if str(row['source_name'] or '').strip() == FK_CUSTOMER_NAME:
            return row
    return None


def _fk_ensure_customer(c, backup_dir, items):
    existing = _fk_find_customer(c)
    if existing:
        return existing, False
    known = _app_customers(c)
    linked, status = _link(FK_CUSTOMER_NAME, known)
    c.execute(
        '''INSERT INTO name_customers(
            source_key, source_index, source_name, reading, linked_name, link_status, manual, retired
           ) VALUES(?,?,?,?,?,?,1,0)''',
        (FK_CUSTOMER_KEY, -1, FK_CUSTOMER_NAME, FK_CUSTOMER_READING, linked, status),
    )
    cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    row = c.execute('SELECT * FROM name_customers WHERE id=?', (cid,)).fetchone()
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / f'customer-created-{cid}.json').write_text(
        json.dumps(_row_as_dict(row), ensure_ascii=False, indent=2), encoding='utf-8',
    )
    items.append({
        'action': 'create-customer',
        'customerId': cid,
        'customerName': FK_CUSTOMER_NAME,
        'sourceKey': FK_CUSTOMER_KEY,
    })
    return row, True


def _fk_is_current(fmt, samples, fmt_def):
    if str(fmt['format_name'] or '') != fmt_def['formatName']:
        return False
    if str(fmt['placement'] or '') != fmt_def['placement']:
        return False
    if str(fmt['family'] or '') != FK_FAMILY:
        return False
    if str(fmt['heading'] or '') != fmt_def['heading']:
        return False
    if str(fmt['note'] or '') != fmt_def['note']:
        return False
    if str(fmt['layout'] or '') != fmt_def['layout']:
        return False
    if str(fmt['reading'] or '') != fmt_def['reading']:
        return False
    if int(fmt['small'] or 0) != int(fmt_def['small']):
        return False
    if int(fmt['width_mm'] or 0) != int(fmt_def['widthMm']):
        return False
    if int(fmt['height_mm'] or 0) != int(fmt_def['heightMm']):
        return False
    if str(fmt['fixed_color'] or '') != fmt_def['fixedColor']:
        return False
    if str(fmt['color_caption'] or '') != fmt_def['colorCaption']:
        return False
    if float(fmt['maximum_font'] or 0) != float(fmt_def['maximumFont']):
        return False
    if int(fmt['source_row'] or 0) != int(fmt_def['sourceRow']):
        return False
    if str(fmt['identity'] or '') != fmt_def['identity']:
        return False
    spec = _load_spec(fmt)
    if spec.get('fkPolicy') != FK_NAMES_POLICY or spec.get('fkKind') != fmt_def['kind']:
        return False
    if spec.get('surnameOnly') is not True:
        return False
    want_fit = fmt_def.get('sampleFit') or 'exact'
    if (spec.get('sampleFit') or '') != want_fit:
        return False
    try:
        have_groups = json.loads(fmt['groups_json'] or '[]')
        have_colors = json.loads(fmt['colors_json'] or '[]')
        have_swatches = json.loads(fmt['swatches_json'] or '[]')
    except (TypeError, json.JSONDecodeError):
        return False
    if have_groups != fmt_def['groups'] or have_colors != fmt_def['colors']:
        return False
    if have_swatches != fmt_def['swatches']:
        return False
    if len(samples) != len(fmt_def['papers']):
        return False
    by_paper = {str(s['paper_color'] or ''): s for s in samples}
    for paper in fmt_def['papers']:
        sample = by_paper.get(paper)
        if sample is None:
            return False
        want_key = _three_variant_key(fmt_def['papers'], paper)
        if str(sample['variant_key'] or '') != want_key:
            return False
        rel = str(sample['relative_path'] or '')
        if not rel.startswith('images/names/'):
            return False
        if fmt_def['kind'] not in Path(rel).name:
            return False
        if str(sample['caption'] or '') != fmt_def['caption']:
            return False
    return True


def _backup_fk_format(c, root, backup_dir, fmt, samples):
    backup_dir.mkdir(parents=True, exist_ok=True)
    images_backup = backup_dir / 'images'
    images_backup.mkdir(parents=True, exist_ok=True)
    payload = {
        'format': _row_as_dict(fmt),
        'samples': [_row_as_dict(s) for s in samples],
    }
    (backup_dir / f'format-{fmt["id"]}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    for sample in samples:
        rel = str(sample['relative_path'] or '')
        if not rel:
            continue
        src = root / rel
        if src.is_file():
            dest = images_backup / f'{fmt["id"]}-{Path(rel).name}'
            if not dest.exists():
                shutil.copy2(src, dest)


def _write_fk_samples(c, root, format_id, fmt_def):
    band = _three_band(
        fmt_def['sampleText'],
        height_mm=fmt_def['heightMm'],
        font_pt=fmt_def['fontPt'],
    )
    fit_mode = fmt_def.get('sampleFit') or _sample_fit_mode_for_format(
        {
            'small': fmt_def['small'],
            'layout': fmt_def['layout'],
            'reading': fmt_def['reading'],
            'family': FK_FAMILY,
            'maximumFont': fmt_def['maximumFont'],
            'fontPt': fmt_def['fontPt'],
        },
        font_pt=fmt_def['fontPt'],
    )
    c.execute('DELETE FROM name_samples WHERE format_id=?', (format_id,))
    notes = []
    paths = []
    for paper in fmt_def['papers']:
        key = _three_variant_key(fmt_def['papers'], paper)
        stem = f'{fmt_def["kind"]}-{paper}'
        rel = f'images/names/{stem}-{format_id:06}.png'
        dst = root / rel
        rgb = FK_PAPER_RGB.get(paper, (255, 255, 255))
        ok, note = _render_ashuka_banded_sample(
            dst, (band,),
            width_mm=fmt_def['widthMm'],
            height_mm=fmt_def['heightMm'],
            family_hint=FK_FAMILY,
            paper_rgb=rgb,
            fit_mode=fit_mode,
        )
        notes.append(f'{paper}:{note}')
        if not ok:
            return False, '; '.join(notes), paths
        c.execute(
            '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
               VALUES(?,?,?,?,?,?)''',
            (format_id, key, paper, paper, rel, fmt_def['caption']),
        )
        paths.append(rel)
    return True, '; '.join(notes), paths


def _upsert_fk_format_row(c, customer_id, fmt_def, existing=None):
    groups = json.dumps(fmt_def.get('groups') or [], ensure_ascii=False)
    colors = json.dumps(fmt_def.get('colors') or [], ensure_ascii=False)
    swatches = json.dumps(fmt_def.get('swatches') or [], ensure_ascii=False)
    spec = json.dumps(_fk_spec(fmt_def), ensure_ascii=False)
    values = (
        fmt_def['formatName'], fmt_def['placement'], FK_FAMILY, '',
        fmt_def['heading'], fmt_def['note'], fmt_def['layout'], fmt_def['reading'],
        int(fmt_def['small']), int(fmt_def['widthMm']), int(fmt_def['heightMm']), 0,
        fmt_def['fixedColor'], fmt_def['colorCaption'],
        groups, colors, swatches, 'null',
        '', float(fmt_def['maximumFont']), 0, 0, int(fmt_def['sourceRow']),
        spec, 0, '[]', 0,
    )
    if existing:
        c.execute(
            '''UPDATE name_formats SET format_name=?, placement=?, family=?, header_family=?, heading=?, note=?,
                layout=?, reading=?, small=?, width_mm=?, height_mm=?, unknown_size=?, fixed_color=?, color_caption=?,
                groups_json=?, colors_json=?, swatches_json=?, departments_json=?, pending=?, maximum_font=?,
                heading_font=?, number_length=?, source_row=?, spec_json=?, needs_review=?, review_json=?, blank=?,
                retired=0, identity=? WHERE id=?''',
            values + (fmt_def['identity'], existing['id']),
        )
        return existing['id']
    c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, family, header_family, heading, note,
            layout, reading, small, width_mm, height_mm, unknown_size, fixed_color, color_caption,
            groups_json, colors_json, swatches_json, departments_json, pending, maximum_font, heading_font,
            number_length, source_row, previous_source_row, spec_json, needs_review, review_json, blank, manual, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)''',
        (
            fmt_def['identity'], customer_id, -1,
            values[0], values[1], values[2], values[3], values[4], values[5],
            values[6], values[7], values[8], values[9], values[10], values[11],
            values[12], values[13], values[14], values[15], values[16], values[17],
            values[18], values[19], values[20], values[21], values[22], None,
            values[23], values[24], values[25], values[26],
        ),
    )
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _patch_fk_catalog(data_dir, backup_dir, fmt_def, items):
    path = Path(data_dir) / 'name-import' / 'catalog.json'
    if not path.is_file():
        return False
    backup_dir.mkdir(parents=True, exist_ok=True)
    catalog_backup = backup_dir / 'catalog.json'
    if not catalog_backup.exists():
        shutil.copy2(path, catalog_backup)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    customers = list(data.get('customers') or [])
    formats = list(data.get('formats') or [])
    by_key = {c.get('sourceKey'): c for c in customers}
    changed = False
    if FK_CUSTOMER_KEY not in by_key:
        customers.append({
            'sourceKey': FK_CUSTOMER_KEY,
            'sourceIndex': -1,
            'name': FK_CUSTOMER_NAME,
            'reading': FK_CUSTOMER_READING,
        })
        by_key[FK_CUSTOMER_KEY] = customers[-1]
        changed = True
    else:
        entry = by_key[FK_CUSTOMER_KEY]
        if entry.get('name') != FK_CUSTOMER_NAME:
            entry['name'] = FK_CUSTOMER_NAME
            changed = True
    item_by_identity = {i.get('identity'): i for i in items if i.get('identity')}
    item = item_by_identity.get(fmt_def['identity'])
    existing = next(
        (f for f in formats if f.get('identity') == fmt_def['identity']),
        None,
    )
    payload = {
        'identity': fmt_def['identity'],
        'customerKey': FK_CUSTOMER_KEY,
        'sourceKind': -1,
        'format': fmt_def['formatName'],
        'placement': fmt_def['placement'],
        'family': FK_FAMILY,
        'heading': fmt_def['heading'],
        'note': fmt_def['note'],
        'layout': fmt_def['layout'],
        'reading': fmt_def['reading'],
        'small': bool(fmt_def['small']),
        'widthMm': fmt_def['widthMm'],
        'heightMm': fmt_def['heightMm'],
        'fixedColor': fmt_def['fixedColor'],
        'colorCaption': fmt_def['colorCaption'],
        'groups': list(fmt_def.get('groups') or []),
        'colors': list(fmt_def.get('colors') or []),
        'colorSwatches': list(fmt_def.get('swatches') or []),
        'maximumFont': fmt_def['maximumFont'],
        'headingFont': 0,
        'sourceRow': fmt_def['sourceRow'],
        'manual': True,
        'surnameOnly': True,
        **{k: v for k, v in _fk_spec(fmt_def).items() if v is not None},
        'samples': [],
    }
    if item:
        payload['dbId'] = item.get('formatId')
        paths = item.get('samplePaths') or (
            [item['samplePath']] if item.get('samplePath') else []
        )
        for idx, paper in enumerate(fmt_def['papers']):
            rel = paths[idx] if idx < len(paths) else ''
            payload['samples'].append({
                'key': _three_variant_key(fmt_def['papers'], paper),
                'condition': paper,
                'paperColor': paper,
                'relativePath': rel,
                'caption': fmt_def['caption'],
            })
    if existing is None:
        formats.append(payload)
        changed = True
    else:
        for key, value in payload.items():
            if existing.get(key) != value:
                existing[key] = value
                changed = True
    if changed:
        data['customers'] = customers
        data['formats'] = formats
        data['customerCount'] = len(customers)
        data['formatCount'] = len(formats)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return changed


def ensure_fk_name_formats(c, data_dir=None):
    """Excel 行29 エフケーのネーム形式を追加／更新する。他顧客は変更しない。"""
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'fk-names-{stamp}'
    cust = _fk_customer_def()
    fmt_def = cust['formats'][0]
    items = []
    open_items = list(FK_OPEN_ITEMS)

    customer = _fk_find_customer(c)
    needs_work = customer is None
    if not needs_work:
        row = c.execute(
            'SELECT * FROM name_formats WHERE identity=? AND retired=0',
            (fmt_def['identity'],),
        ).fetchone()
        if row is None:
            needs_work = True
        else:
            samples = c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (row['id'],),
            ).fetchall()
            if not _fk_is_current(row, samples, fmt_def):
                needs_work = True

    if not needs_work:
        report = {
            'at': stamp,
            'changed': 0,
            'policy': FK_NAMES_POLICY,
            'backupDir': '',
            'catalogChanged': False,
            'openItems': open_items,
            'customers': [FK_CUSTOMER_NAME],
            'items': [],
            'reason': 'already-applied',
        }
        (root / FK_NAMES_REPORT).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        c.execute(
            'INSERT OR REPLACE INTO settings VALUES(?,?)',
            (
                FK_NAMES_SETTINGS_KEY,
                json.dumps({
                    'at': stamp,
                    'changed': 0,
                    'report': FK_NAMES_REPORT,
                    'policy': FK_NAMES_POLICY,
                }, ensure_ascii=False),
            ),
        )
        return report

    backup_dir.mkdir(parents=True, exist_ok=True)
    customer, created = _fk_ensure_customer(c, backup_dir, items)

    by_identity = c.execute(
        'SELECT * FROM name_formats WHERE identity=? AND retired=0',
        (fmt_def['identity'],),
    ).fetchone()
    existing = by_identity
    if existing is None:
        candidates = c.execute(
            '''SELECT * FROM name_formats WHERE customer_id=? AND retired=0
               AND placement=? ORDER BY id''',
            (customer['id'], fmt_def['placement']),
        ).fetchall()
        if len(candidates) == 1:
            existing = candidates[0]
        elif len(candidates) > 1:
            for cand in candidates:
                name = str(cand['format_name'] or '')
                if fmt_def['formatName'] in name or name in fmt_def['formatName'] or '小ネーム' in name:
                    existing = cand
                    break
            if existing is None:
                existing = candidates[0]
        if existing is None:
            # 旧仕様（フルネーム／先方から指定）の単一形式があれば再利用
            all_fmt = c.execute(
                'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
                (customer['id'],),
            ).fetchall()
            if len(all_fmt) == 1:
                existing = all_fmt[0]

    samples = []
    if existing:
        samples = c.execute(
            'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
            (existing['id'],),
        ).fetchall()
        if _fk_is_current(existing, samples, fmt_def):
            items.append({
                'action': 'skip',
                'kind': fmt_def['kind'],
                'formatId': existing['id'],
                'number': _number(existing['id']),
                'identity': fmt_def['identity'],
                'customerName': FK_CUSTOMER_NAME,
                'reason': 'already-current',
            })
        else:
            _backup_fk_format(c, root, backup_dir, existing, samples)
            fid = _upsert_fk_format_row(c, customer['id'], fmt_def, existing=existing)
            ok, note, paths = _write_fk_samples(c, root, fid, fmt_def)
            items.append({
                'action': 'update',
                'kind': fmt_def['kind'],
                'formatId': fid,
                'number': _number(fid),
                'identity': fmt_def['identity'],
                'customerName': FK_CUSTOMER_NAME,
                'sourceRow': fmt_def['sourceRow'],
                'samplePaths': paths,
                'samplePath': paths[0] if paths else '',
                'sampleOk': ok,
                'sampleNote': note,
                'customerCreated': created,
            })
    else:
        fid = _upsert_fk_format_row(c, customer['id'], fmt_def, existing=None)
        ok, note, paths = _write_fk_samples(c, root, fid, fmt_def)
        items.append({
            'action': 'create',
            'kind': fmt_def['kind'],
            'formatId': fid,
            'number': _number(fid),
            'identity': fmt_def['identity'],
            'customerName': FK_CUSTOMER_NAME,
            'sourceRow': fmt_def['sourceRow'],
            'samplePaths': paths,
            'samplePath': paths[0] if paths else '',
            'sampleOk': ok,
            'sampleNote': note,
            'customerCreated': created,
        })

    catalog_changed = _patch_fk_catalog(root, backup_dir, fmt_def, items)
    changed = sum(1 for i in items if i.get('action') in ('create', 'update', 'create-customer'))
    report = {
        'at': stamp,
        'changed': changed,
        'policy': FK_NAMES_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'catalogChanged': catalog_changed,
        'openItems': open_items,
        'customers': [FK_CUSTOMER_NAME],
        'excel': {
            'path': 'media/ネーム仕分け_五十音順2.xlsx',
            'rows': {FK_CUSTOMER_NAME: [FK_SOURCE_ROW]},
            'diff': {
                '色': {'old': '先方から指定', 'new': FK_COLOR_CAPTION},
                'フォントサイズ': {'old': '小汎用', 'new': '36'},
                '名前': {'old': 'フルネーム', 'new': '苗字のみ'},
            },
        },
        'sampleNames': {'surname': FK_SAMPLE_SURNAME},
        'rules': {
            'onlyFkCustomer': True,
            'createIfMissing': True,
            'noDuplicateIdentities': True,
            'noAssigneeChange': True,
            'noAutoApprove': True,
            'noTouchOtherCustomers': True,
        },
        'items': items,
    }
    report_path = root / FK_NAMES_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (backup_dir / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            FK_NAMES_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': changed,
                'report': FK_NAMES_REPORT,
                'policy': FK_NAMES_POLICY,
                'backupDir': report['backupDir'],
            }, ensure_ascii=False),
        ),
    )
    return report


# --- 小ネーム・苗字のみ・36pt（丸ゴシック36 / Pゴシック36）見本スケール修正 ---
# Excel「★ネーム作成管理用」シート相当。登録 36pt を fill 拡大せず exact-pt で描く。


def _paper_rgb_for_sample(paper_color, existing_path=None):
    """用紙色名または既存画像の四隅から RGB を決める。"""
    name = str(paper_color or '').strip()
    known = {
        **THREE_PAPER_RGB,
        **FK_PAPER_RGB,
        '赤': (255, 99, 71),
        '青': (135, 206, 250),
        '緑': (144, 238, 144),
        '白': (255, 255, 255),
    }
    if name in known:
        return known[name]
    if existing_path and Path(existing_path).is_file():
        try:
            from PIL import Image
            image = Image.open(existing_path).convert('RGB')
            return _corner_background(image)
        except (OSError, ImportError):
            pass
    return (255, 255, 255)


def _sample_text_for_surname_format(fmt, sample=None, spec=None):
    """見本に描く苗字。spec / caption の「…」を優先し、無ければ既定。"""
    spec = spec if spec is not None else _load_spec(fmt)
    text = str(spec.get('sampleText') or '').strip()
    if text:
        return text
    caption = ''
    if sample is not None:
        caption = str(_row_get(sample, 'caption') or '')
    if not caption:
        caption = str(_row_get(fmt, 'caption') or '')
    match = re.search(r'「([^」]+)」', caption)
    if match:
        return match.group(1).strip() or SMALL_SURNAME_36_DEFAULT_TEXT
    return SMALL_SURNAME_36_DEFAULT_TEXT


def _rewrite_small_surname_36_sample(root, fmt, sample, *, backup_dir):
    """1見本を exact-pt で描き直す。寸法・書体・用紙色・caption は維持。"""
    rel = str(sample['relative_path'] or '')
    width_mm = float(fmt['width_mm'] or 0) or float(THREE_SMALL_W)
    height_mm = float(fmt['height_mm'] or 0) or float(THREE_SMALL_H)
    family = str(fmt['family'] or THREE_FAMILY)
    paper = str(sample['paper_color'] or '') or str(fmt['fixed_color'] or '白')
    src = root / rel if rel else None
    if backup_dir is not None and src and src.is_file():
        images_backup = backup_dir / 'images'
        images_backup.mkdir(parents=True, exist_ok=True)
        dest = images_backup / f'{fmt["id"]}-{Path(rel).name}'
        if not dest.exists():
            shutil.copy2(src, dest)
    text = _sample_text_for_surname_format(fmt, sample)
    band = _three_band(text, height_mm=height_mm, font_pt=36)
    if not rel:
        stem = f'surname36-{fmt["id"]:06}-{sample["id"]}'
        rel = f'images/names/{stem}.png'
    dst = root / rel
    rgb = _paper_rgb_for_sample(paper, src if src and src.is_file() else None)
    ok, note = _render_ashuka_banded_sample(
        dst, (band,),
        width_mm=width_mm,
        height_mm=height_mm,
        family_hint=family,
        paper_rgb=rgb,
        fit_mode='exact',
    )
    return ok, note, rel, text


def ensure_small_surname_36_exact_samples(c, data_dir=None):
    """DB上の小ネーム・苗字のみ・36pt・丸/Pゴシック見本を exact-pt で再生成する。

    大汎用・小汎用・他サイズ／他 layout は対象外。形式メタ（mm・36pt・書体）は変更しない。
    """
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}
    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    rows = c.execute(
        '''SELECT * FROM name_formats
           WHERE retired=0 AND small=1 AND maximum_font=36
             AND (layout='Surname' OR reading='苗字のみ')
           ORDER BY id'''
    ).fetchall()
    targets = [row for row in rows if _family_is_maru_or_p_gothic(row['family'])]
    items = []
    needs = []
    for fmt in targets:
        spec = _load_spec(fmt)
        samples = c.execute(
            'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
            (fmt['id'],),
        ).fetchall()
        if not samples:
            continue
        if spec.get('sampleFit') == 'exact' and spec.get('exactPtPolicy') == SMALL_SURNAME_36_POLICY:
            # 既に exact 印付きでも、画像が旧 fill のままなら再描画が必要
            # → 印字高さ比が 0.65 超なら要再生成（Excel 目安は ~40%）
            stale = False
            for sample in samples:
                rel = str(sample['relative_path'] or '')
                path = root / rel if rel else None
                if not path or not path.is_file():
                    stale = True
                    break
                try:
                    from PIL import Image
                    image = Image.open(path).convert('RGB')
                    bg = _corner_background(image)
                    ink_box = _ink_bbox(image, bg)
                    if not ink_box:
                        stale = True
                        break
                    h_ratio = (ink_box[3] - ink_box[1] + 1) / max(1, image.height)
                    if h_ratio > 0.65:
                        stale = True
                        break
                except (OSError, ImportError, ValueError):
                    stale = True
                    break
            if not stale:
                items.append({
                    'action': 'skip',
                    'formatId': fmt['id'],
                    'number': _number(fmt['id']),
                    'identity': fmt['identity'],
                    'family': fmt['family'],
                    'reason': 'already-exact-pt',
                })
                continue
        needs.append((fmt, samples, spec))

    if not needs:
        report = {
            'at': stamp,
            'changed': 0,
            'policy': SMALL_SURNAME_36_POLICY,
            'backupDir': '',
            'matchedFormats': len(targets),
            'items': items,
            'reason': 'already-applied',
        }
        (root / SMALL_SURNAME_36_REPORT).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        c.execute(
            'INSERT OR REPLACE INTO settings VALUES(?,?)',
            (
                SMALL_SURNAME_36_SETTINGS_KEY,
                json.dumps({
                    'at': stamp,
                    'changed': 0,
                    'report': SMALL_SURNAME_36_REPORT,
                    'policy': SMALL_SURNAME_36_POLICY,
                }, ensure_ascii=False),
            ),
        )
        return report

    backup_dir = root / 'backups' / f'small-surname-36-{stamp}'
    backup_dir.mkdir(parents=True, exist_ok=True)
    changed = 0
    for fmt, samples, spec in needs:
        payload = {
            'format': _row_as_dict(fmt),
            'samples': [_row_as_dict(s) for s in samples],
        }
        (backup_dir / f'format-{fmt["id"]}.json').write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        sample_notes = []
        paths = []
        ok_all = True
        for sample in samples:
            ok, note, rel, text = _rewrite_small_surname_36_sample(
                root, fmt, sample, backup_dir=backup_dir,
            )
            sample_notes.append(f'{sample["id"]}:{note}')
            paths.append(rel)
            if not ok:
                ok_all = False
                continue
            c.execute(
                'UPDATE name_samples SET relative_path=? WHERE id=?',
                (rel, sample['id']),
            )
        new_spec = dict(spec or {})
        new_spec['sampleFit'] = 'exact'
        new_spec['exactPtPolicy'] = SMALL_SURNAME_36_POLICY
        if 'sampleText' not in new_spec:
            new_spec['sampleText'] = _sample_text_for_surname_format(fmt, samples[0], spec)
        c.execute(
            'UPDATE name_formats SET spec_json=? WHERE id=?',
            (json.dumps(new_spec, ensure_ascii=False), fmt['id']),
        )
        changed += 1
        items.append({
            'action': 'rewrite-samples',
            'formatId': fmt['id'],
            'number': _number(fmt['id']),
            'identity': fmt['identity'],
            'family': fmt['family'],
            'maximumFont': fmt['maximum_font'],
            'widthMm': fmt['width_mm'],
            'heightMm': fmt['height_mm'],
            'samplePaths': paths,
            'sampleOk': ok_all,
            'sampleNote': '; '.join(sample_notes),
            'sampleText': new_spec.get('sampleText'),
        })

    report = {
        'at': stamp,
        'changed': changed,
        'policy': SMALL_SURNAME_36_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/'),
        'matchedFormats': len(targets),
        'excelScope': {
            'source': 'media/ネーム仕分け_五十音順2.xlsx',
            'criteria': '小ネーム + 苗字のみ + 36pt + (HG丸ゴシック|MSPゴシック)',
            'maruGothic36Rows': 15,
            'pGothic36Rows': 10,
            'note': 'Excel全件。DBに存在する該当形式のみ再描画。',
        },
        'rules': {
            'keepLabelMm': True,
            'keepRegistered36pt': True,
            'keepFamilyAndPaper': True,
            'noTouchGenericLargeSmall': True,
            'exactPtNoFillUpscale': True,
        },
        'items': items,
    }
    (root / SMALL_SURNAME_36_REPORT).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    (backup_dir / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            SMALL_SURNAME_36_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': changed,
                'report': SMALL_SURNAME_36_REPORT,
                'policy': SMALL_SURNAME_36_POLICY,
                'backupDir': report['backupDir'],
            }, ensure_ascii=False),
        ),
    )
    return report


# --- 弁釜（Excel 行159–160）ネーム形式 ---
BENKAMA_NAMES_POLICY = 'excel-gojuon2-r159-160-v1'
BENKAMA_NAMES_SETTINGS_KEY = 'benkama_name_formats'
BENKAMA_NAMES_REPORT = 'benkama-name-formats-report.json'
BENKAMA_CUSTOMER_KEY = 'excel:benkama'
BENKAMA_CUSTOMER_NAME = '弁釜本社/札幌/八軒/旭川/函館/ローズD/'
BENKAMA_CUSTOMER_READING = 'べんかま'
BENKAMA_CUSTOMER_ALIASES = (
    '弁釜',
    '弁釜札幌',
    '弁釜本社',
    '弁釜本社/札幌/八軒/旭川/函館/ローズD',
)
BENKAMA_FAMILY = 'MSPゴシック'
BENKAMA_PLACEMENT = '白衣：左袖25cm'
BENKAMA_WIDTH_MM, BENKAMA_HEIGHT_MM = THREE_LARGE_W, THREE_LARGE_H
BENKAMA_BAND_HEIGHT_MM = 25.0
BENKAMA_UPPER_PT = 20
BENKAMA_HEADING = '上段：社員番号：〇〇〇〇〇〇〇'
BENKAMA_HEADING_SAMPLE = '社員番号：1234567'
BENKAMA_NUMBER_LENGTH = 7
BENKAMA_COLUMNS = 2
BENKAMA_SAMPLE_FULL = THREE_SAMPLE_FULL
BENKAMA_SAMPLE_PERIOD = '1 ﾃｷ ｼﾎｳ'  # Excel 例「① ﾃｷ ｼﾎｳ」に合わせたカタカナ＋期数字
BENKAMA_PAPER_RGB = {
    '白': (255, 255, 255),
    '青': (135, 206, 250),  # #87CEFA
    '緑': (144, 238, 144),  # #90EE90
    '赤': (240, 128, 128),  # #F08080
}
BENKAMA_STAFF_COLOR_CAPTION = '社員：青　パート：白'
BENKAMA_STAFF_GROUPS = ['社員', 'パート']
BENKAMA_STAFF_COLORS = ['青', '白']
BENKAMA_STAFF_SWATCHES = ['#87CEFA', '#FFFFFF']
BENKAMA_STAFF_PAPERS = ('青', '白')
BENKAMA_STAFF_NOTE = (
    '社員番号7ケタ。書体はMSPゴシック。上段：20／下段：大汎用。'
    '列2。上段帯25mm・中揃え／下段帯25mm・中揃え・上揃え。'
    f'Excel 行159（ネーム仕分け_五十音順2）。色は{BENKAMA_STAFF_COLOR_CAPTION}。'
)
BENKAMA_PERIOD_COLOR_CAPTION = (
    '札幌　1期生：赤　2期生：緑　3期生：青　4期生：白\n'
    '旭川　1期生：赤　2期生：緑　3期生：青　4期生：赤　5期生：青　'
    '6期生：白　7期生：赤　8期生：白　9期生：青　10期生：白\n'
    '函館　1～4期生：無し　5期生：緑　6期生：青　7期生：赤　8期生：白\n'
    '八軒　1期生：赤'
)
BENKAMA_PERIOD_NOTE = (
    'ラベル色は５期生以降ループ（札幌のみ、他は規則性なし？）\n'
    '名前は全てカタカナ表記で頭に何期生かを記す数字\n'
    '例　① ﾃｷ ｼﾎｳ\n'
    '書体はMSPゴシック。上段：20／下段：大汎用。列2。社員番号7ケタ。'
    'Excel 行160（ネーム仕分け_五十音順2）。'
)
BENKAMA_OPEN_ITEMS = [
    '大ネーム実寸は Excel に mm が無いため、既存どおり 80×50 を採用',
    '上段帯・下段帯は各25mm（合計50mm）。横中揃え／上段縦中・下段縦上',
    'MSPゴシックは環境フォント次第（msgothic 等へフォールバック）',
    '期生色の見本はユニーク色（赤・緑・青・白）のみ。無しは見本なし',
    '顧客名は Excel「弁釜本社/札幌/八軒/旭川/函館/ローズD/」。既存の弁釜系別名は同一顧客へ寄せる',
]


def _benkama_period_color_rows():
    """拠点×期生の色表（無しは除外）。UI groups/colors/swatches 用。"""
    rows = []
    for site, pairs in (
        ('札幌', [(1, '赤'), (2, '緑'), (3, '青'), (4, '白')]),
        ('旭川', [
            (1, '赤'), (2, '緑'), (3, '青'), (4, '赤'), (5, '青'),
            (6, '白'), (7, '赤'), (8, '白'), (9, '青'), (10, '白'),
        ]),
        ('函館', [(5, '緑'), (6, '青'), (7, '赤'), (8, '白')]),
        ('八軒', [(1, '赤')]),
    ):
        for period, color in pairs:
            rows.append((f'{site}{period}期生', color, _swatch(color)))
    return rows


def _benkama_lower_pt(text):
    return _three_generic_pt('large', text)


def _benkama_bands(fmt_def):
    lower_pt = float(fmt_def['fontPt'])
    return (
        {
            'role': 'upper',
            'text': BENKAMA_HEADING_SAMPLE,
            'heightMm': BENKAMA_BAND_HEIGHT_MM,
            'fontPt': float(BENKAMA_UPPER_PT),
            'alignH': 'center',
            'alignV': 'middle',
            'indentMm': 0.0,
        },
        {
            'role': 'name',
            'text': fmt_def['sampleText'],
            'heightMm': BENKAMA_BAND_HEIGHT_MM,
            'fontPt': lower_pt,
            'alignH': 'center',
            'alignV': 'top',
            'indentMm': 0.0,
        },
    )


def _benkama_format_defs():
    period_rows = _benkama_period_color_rows()
    period_groups = [r[0] for r in period_rows]
    period_colors = [r[1] for r in period_rows]
    period_swatches = [r[2] for r in period_rows]
    staff_pt = _benkama_lower_pt(BENKAMA_SAMPLE_FULL)
    period_pt = _benkama_lower_pt(BENKAMA_SAMPLE_PERIOD)
    return (
        {
            'identity': 'benkama:large-staff',
            'kind': 'benkama-staff',
            'sourceRow': 159,
            'formatName': '大ネーム（社員・パート色）',
            'placement': BENKAMA_PLACEMENT,
            'heading': BENKAMA_HEADING,
            'note': BENKAMA_STAFF_NOTE,
            'layout': 'HeadingName',
            'reading': 'フルネーム',
            'small': 0,
            'widthMm': BENKAMA_WIDTH_MM,
            'heightMm': BENKAMA_HEIGHT_MM,
            'fixedColor': '',
            'colorCaption': BENKAMA_STAFF_COLOR_CAPTION,
            'groups': list(BENKAMA_STAFF_GROUPS),
            'colors': list(BENKAMA_STAFF_COLORS),
            'swatches': list(BENKAMA_STAFF_SWATCHES),
            'maximumFont': THREE_GENERIC_LARGE[4],
            'headingFont': BENKAMA_UPPER_PT,
            'numberLength': BENKAMA_NUMBER_LENGTH,
            'sampleText': BENKAMA_SAMPLE_FULL,
            'fontPt': staff_pt,
            'papers': BENKAMA_STAFF_PAPERS,
            'caption': (
                f'見本：上段「{BENKAMA_HEADING_SAMPLE}」・下段フルネーム「{BENKAMA_SAMPLE_FULL}」。'
                f'上段{BENKAMA_UPPER_PT}pt／下段大汎用。書体は{BENKAMA_FAMILY}。'
                f'色は{BENKAMA_STAFF_COLOR_CAPTION}。'
            ),
        },
        {
            'identity': 'benkama:large-period',
            'kind': 'benkama-period',
            'sourceRow': 160,
            'formatName': '大ネーム（期生色表）',
            'placement': BENKAMA_PLACEMENT,
            'heading': BENKAMA_HEADING,
            'note': BENKAMA_PERIOD_NOTE,
            'layout': 'HeadingName',
            'reading': 'フルネーム',
            'small': 0,
            'widthMm': BENKAMA_WIDTH_MM,
            'heightMm': BENKAMA_HEIGHT_MM,
            'fixedColor': '',
            'colorCaption': BENKAMA_PERIOD_COLOR_CAPTION,
            'groups': period_groups,
            'colors': period_colors,
            'swatches': period_swatches,
            'maximumFont': THREE_GENERIC_LARGE[4],
            'headingFont': BENKAMA_UPPER_PT,
            'numberLength': BENKAMA_NUMBER_LENGTH,
            'sampleText': BENKAMA_SAMPLE_PERIOD,
            'fontPt': period_pt,
            'papers': ('赤', '緑', '青', '白'),
            'caption': (
                f'見本：上段「{BENKAMA_HEADING_SAMPLE}」・下段カタカナ期生名「{BENKAMA_SAMPLE_PERIOD}」。'
                f'上段{BENKAMA_UPPER_PT}pt／下段大汎用。書体は{BENKAMA_FAMILY}。'
                'ラベル色は５期生以降ループ（札幌のみ、他は規則性なし？）。'
            ),
        },
    )


def _benkama_spec(fmt_def):
    return {
        'benkamaKind': fmt_def['kind'],
        'benkamaPolicy': BENKAMA_NAMES_POLICY,
        'sampleText': fmt_def['sampleText'],
        'fontPt': fmt_def['fontPt'],
        'headingFontPt': BENKAMA_UPPER_PT,
        'papers': list(fmt_def['papers']),
        'columns': BENKAMA_COLUMNS,
        'numberLength': BENKAMA_NUMBER_LENGTH,
        'bandHeightsMm': [BENKAMA_BAND_HEIGHT_MM, BENKAMA_BAND_HEIGHT_MM],
        'bandFontsPt': [BENKAMA_UPPER_PT, fmt_def['fontPt']],
        'genericFontsPt': dict(THREE_GENERIC_LARGE),
        'colorRoles': dict(zip(fmt_def.get('groups') or [], fmt_def.get('colors') or [])),
    }


def _benkama_customer_def():
    return {
        'customerKey': BENKAMA_CUSTOMER_KEY,
        'customerName': BENKAMA_CUSTOMER_NAME,
        'reading': BENKAMA_CUSTOMER_READING,
        'formats': _benkama_format_defs(),
    }


def _benkama_normalize_name(name):
    return str(name or '').replace('\n', ' ').replace('\r', ' ').strip().rstrip('/')


def _benkama_is_alias(name):
    text = str(name or '').replace('\n', ' ').replace('\r', ' ').strip()
    if not text:
        return False
    if text == BENKAMA_CUSTOMER_NAME:
        return True
    if _benkama_normalize_name(text) == _benkama_normalize_name(BENKAMA_CUSTOMER_NAME):
        return True
    if text in BENKAMA_CUSTOMER_ALIASES:
        return True
    # 「弁釜」「弁釜札幌」など、弁釜で始まり他社名を含まないもの
    if text.startswith('弁釜') and '明日' not in text:
        return True
    return False


def _benkama_find_customer(c):
    rows = c.execute(
        'SELECT * FROM name_customers WHERE retired=0 ORDER BY id'
    ).fetchall()
    for row in rows:
        if str(row['source_key'] or '') == BENKAMA_CUSTOMER_KEY:
            return row
    for row in rows:
        if str(row['source_name'] or '').strip() == BENKAMA_CUSTOMER_NAME:
            return row
    for row in rows:
        if _benkama_is_alias(row['source_name']):
            return row
    return None


def _benkama_ensure_customer(c, backup_dir, items):
    existing = _benkama_find_customer(c)
    if existing:
        changed = False
        if str(existing['source_name'] or '') != BENKAMA_CUSTOMER_NAME:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f'customer-rename-{existing["id"]}.json').write_text(
                json.dumps({
                    'before': _row_as_dict(existing),
                    'afterName': BENKAMA_CUSTOMER_NAME,
                }, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            c.execute(
                'UPDATE name_customers SET source_name=?, source_key=?, reading=? WHERE id=?',
                (
                    BENKAMA_CUSTOMER_NAME, BENKAMA_CUSTOMER_KEY,
                    BENKAMA_CUSTOMER_READING, existing['id'],
                ),
            )
            changed = True
            items.append({
                'action': 'rename-customer',
                'customerId': existing['id'],
                'from': existing['source_name'],
                'to': BENKAMA_CUSTOMER_NAME,
            })
        elif str(existing['source_key'] or '') != BENKAMA_CUSTOMER_KEY:
            c.execute(
                'UPDATE name_customers SET source_key=?, reading=? WHERE id=?',
                (BENKAMA_CUSTOMER_KEY, BENKAMA_CUSTOMER_READING, existing['id']),
            )
            changed = True
        if changed:
            existing = c.execute(
                'SELECT * FROM name_customers WHERE id=?', (existing['id'],),
            ).fetchone()
        return existing, False
    known = _app_customers(c)
    linked, status = _link(BENKAMA_CUSTOMER_NAME, known)
    # 指示書側に「弁釜札幌」等があればリンクを寄せる
    if not linked:
        for alias in BENKAMA_CUSTOMER_ALIASES:
            if alias in known:
                linked, status = alias, '対応'
                break
    c.execute(
        '''INSERT INTO name_customers(
            source_key, source_index, source_name, reading, linked_name, link_status, manual, retired
           ) VALUES(?,?,?,?,?,?,1,0)''',
        (
            BENKAMA_CUSTOMER_KEY, -1, BENKAMA_CUSTOMER_NAME,
            BENKAMA_CUSTOMER_READING, linked, status,
        ),
    )
    cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    row = c.execute('SELECT * FROM name_customers WHERE id=?', (cid,)).fetchone()
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / f'customer-created-{cid}.json').write_text(
        json.dumps(_row_as_dict(row), ensure_ascii=False, indent=2), encoding='utf-8',
    )
    items.append({
        'action': 'create-customer',
        'customerId': cid,
        'customerName': BENKAMA_CUSTOMER_NAME,
        'sourceKey': BENKAMA_CUSTOMER_KEY,
    })
    return row, True


def _benkama_is_current(fmt, samples, fmt_def):
    if str(fmt['format_name'] or '') != fmt_def['formatName']:
        return False
    if str(fmt['placement'] or '') != fmt_def['placement']:
        return False
    if str(fmt['family'] or '') != BENKAMA_FAMILY:
        return False
    if str(fmt['heading'] or '') != fmt_def['heading']:
        return False
    if str(fmt['note'] or '') != fmt_def['note']:
        return False
    if str(fmt['layout'] or '') != fmt_def['layout']:
        return False
    if str(fmt['reading'] or '') != fmt_def['reading']:
        return False
    if int(fmt['small'] or 0) != int(fmt_def['small']):
        return False
    if int(fmt['width_mm'] or 0) != int(fmt_def['widthMm']):
        return False
    if int(fmt['height_mm'] or 0) != int(fmt_def['heightMm']):
        return False
    if str(fmt['fixed_color'] or '') != fmt_def['fixedColor']:
        return False
    if str(fmt['color_caption'] or '') != fmt_def['colorCaption']:
        return False
    if float(fmt['maximum_font'] or 0) != float(fmt_def['maximumFont']):
        return False
    if float(fmt['heading_font'] or 0) != float(fmt_def['headingFont']):
        return False
    if int(fmt['number_length'] or 0) != int(fmt_def['numberLength']):
        return False
    if int(fmt['source_row'] or 0) != int(fmt_def['sourceRow']):
        return False
    if str(fmt['identity'] or '') != fmt_def['identity']:
        return False
    spec = _load_spec(fmt)
    if spec.get('benkamaPolicy') != BENKAMA_NAMES_POLICY or spec.get('benkamaKind') != fmt_def['kind']:
        return False
    try:
        have_groups = json.loads(fmt['groups_json'] or '[]')
        have_colors = json.loads(fmt['colors_json'] or '[]')
        have_swatches = json.loads(fmt['swatches_json'] or '[]')
    except (TypeError, json.JSONDecodeError):
        return False
    if have_groups != fmt_def['groups'] or have_colors != fmt_def['colors']:
        return False
    if have_swatches != fmt_def['swatches']:
        return False
    if len(samples) != len(fmt_def['papers']):
        return False
    by_paper = {str(s['paper_color'] or ''): s for s in samples}
    for paper in fmt_def['papers']:
        sample = by_paper.get(paper)
        if sample is None:
            return False
        want_key = _three_variant_key(fmt_def['papers'], paper)
        if str(sample['variant_key'] or '') != want_key:
            return False
        rel = str(sample['relative_path'] or '')
        if not rel.startswith('images/names/'):
            return False
        if fmt_def['kind'] not in Path(rel).name:
            return False
        if str(sample['caption'] or '') != fmt_def['caption']:
            return False
    return True


def _backup_benkama_format(c, root, backup_dir, fmt, samples):
    backup_dir.mkdir(parents=True, exist_ok=True)
    images_backup = backup_dir / 'images'
    images_backup.mkdir(parents=True, exist_ok=True)
    payload = {
        'format': _row_as_dict(fmt),
        'samples': [_row_as_dict(s) for s in samples],
    }
    (backup_dir / f'format-{fmt["id"]}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    for sample in samples:
        rel = str(sample['relative_path'] or '')
        if not rel:
            continue
        src = root / rel
        if src.is_file():
            dest = images_backup / f'{fmt["id"]}-{Path(rel).name}'
            if not dest.exists():
                shutil.copy2(src, dest)


def _write_benkama_samples(c, root, format_id, fmt_def):
    bands = _benkama_bands(fmt_def)
    c.execute('DELETE FROM name_samples WHERE format_id=?', (format_id,))
    notes = []
    paths = []
    for paper in fmt_def['papers']:
        key = _three_variant_key(fmt_def['papers'], paper)
        stem = f'{fmt_def["kind"]}-{paper}'
        rel = f'images/names/{stem}-{format_id:06}.png'
        dst = root / rel
        rgb = BENKAMA_PAPER_RGB.get(paper, (255, 255, 255))
        ok, note = _render_ashuka_banded_sample(
            dst, bands,
            width_mm=fmt_def['widthMm'],
            height_mm=fmt_def['heightMm'],
            family_hint=BENKAMA_FAMILY,
            paper_rgb=rgb,
        )
        notes.append(f'{paper}:{note}')
        if not ok:
            return False, '; '.join(notes), paths
        c.execute(
            '''INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)
               VALUES(?,?,?,?,?,?)''',
            (format_id, key, paper, paper, rel, fmt_def['caption']),
        )
        paths.append(rel)
    return True, '; '.join(notes), paths


def _upsert_benkama_format_row(c, customer_id, fmt_def, existing=None):
    groups = json.dumps(fmt_def.get('groups') or [], ensure_ascii=False)
    colors = json.dumps(fmt_def.get('colors') or [], ensure_ascii=False)
    swatches = json.dumps(fmt_def.get('swatches') or [], ensure_ascii=False)
    spec = json.dumps(_benkama_spec(fmt_def), ensure_ascii=False)
    values = (
        fmt_def['formatName'], fmt_def['placement'], BENKAMA_FAMILY, '',
        fmt_def['heading'], fmt_def['note'], fmt_def['layout'], fmt_def['reading'],
        int(fmt_def['small']), int(fmt_def['widthMm']), int(fmt_def['heightMm']), 0,
        fmt_def['fixedColor'], fmt_def['colorCaption'],
        groups, colors, swatches, 'null',
        '', float(fmt_def['maximumFont']), float(fmt_def['headingFont']),
        int(fmt_def['numberLength']), int(fmt_def['sourceRow']),
        spec, 0, '[]', 0,
    )
    if existing:
        c.execute(
            '''UPDATE name_formats SET format_name=?, placement=?, family=?, header_family=?, heading=?, note=?,
                layout=?, reading=?, small=?, width_mm=?, height_mm=?, unknown_size=?, fixed_color=?, color_caption=?,
                groups_json=?, colors_json=?, swatches_json=?, departments_json=?, pending=?, maximum_font=?,
                heading_font=?, number_length=?, source_row=?, spec_json=?, needs_review=?, review_json=?, blank=?,
                retired=0, identity=? WHERE id=?''',
            values + (fmt_def['identity'], existing['id']),
        )
        return existing['id']
    c.execute(
        '''INSERT INTO name_formats(
            identity, customer_id, source_kind, format_name, placement, family, header_family, heading, note,
            layout, reading, small, width_mm, height_mm, unknown_size, fixed_color, color_caption,
            groups_json, colors_json, swatches_json, departments_json, pending, maximum_font, heading_font,
            number_length, source_row, previous_source_row, spec_json, needs_review, review_json, blank, manual, retired
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)''',
        (
            fmt_def['identity'], customer_id, -1,
            values[0], values[1], values[2], values[3], values[4], values[5],
            values[6], values[7], values[8], values[9], values[10], values[11],
            values[12], values[13], values[14], values[15], values[16], values[17],
            values[18], values[19], values[20], values[21], values[22], None,
            values[23], values[24], values[25], values[26],
        ),
    )
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _benkama_match_legacy(fmt, fmt_def):
    """同一顧客の既存大ネームを社員／期生のどちらかに割り当てる。"""
    caption = str(fmt['color_caption'] or '')
    note = str(fmt['note'] or '')
    name = str(fmt['format_name'] or '')
    if fmt_def['kind'] == 'benkama-staff':
        if '期生' in caption or '期生' in note or '期生' in name:
            return False
        if '社員' in caption or 'パート' in caption or '社員番号7ケタ' in note:
            return True
        if '社員' in name or 'パート' in name:
            return True
        return False
    if fmt_def['kind'] == 'benkama-period':
        return '期生' in caption or '期生' in note or '期生' in name or 'カタカナ' in note
    return False


def _patch_benkama_catalog(data_dir, backup_dir, fmt_defs, items):
    path = Path(data_dir) / 'name-import' / 'catalog.json'
    if not path.is_file():
        return False
    backup_dir.mkdir(parents=True, exist_ok=True)
    catalog_backup = backup_dir / 'catalog.json'
    if not catalog_backup.exists():
        shutil.copy2(path, catalog_backup)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    customers = list(data.get('customers') or [])
    formats = list(data.get('formats') or [])
    by_key = {c.get('sourceKey'): c for c in customers}
    changed = False
    if BENKAMA_CUSTOMER_KEY not in by_key:
        customers.append({
            'sourceKey': BENKAMA_CUSTOMER_KEY,
            'sourceIndex': -1,
            'name': BENKAMA_CUSTOMER_NAME,
            'reading': BENKAMA_CUSTOMER_READING,
        })
        by_key[BENKAMA_CUSTOMER_KEY] = customers[-1]
        changed = True
    else:
        entry = by_key[BENKAMA_CUSTOMER_KEY]
        if entry.get('name') != BENKAMA_CUSTOMER_NAME:
            entry['name'] = BENKAMA_CUSTOMER_NAME
            changed = True
    item_by_identity = {i.get('identity'): i for i in items if i.get('identity')}
    for fmt_def in fmt_defs:
        item = item_by_identity.get(fmt_def['identity'])
        existing = next(
            (f for f in formats if f.get('identity') == fmt_def['identity']),
            None,
        )
        payload = {
            'identity': fmt_def['identity'],
            'customerKey': BENKAMA_CUSTOMER_KEY,
            'sourceKind': -1,
            'format': fmt_def['formatName'],
            'placement': fmt_def['placement'],
            'family': BENKAMA_FAMILY,
            'heading': fmt_def['heading'],
            'note': fmt_def['note'],
            'layout': fmt_def['layout'],
            'reading': fmt_def['reading'],
            'small': bool(fmt_def['small']),
            'widthMm': fmt_def['widthMm'],
            'heightMm': fmt_def['heightMm'],
            'fixedColor': fmt_def['fixedColor'],
            'colorCaption': fmt_def['colorCaption'],
            'groups': list(fmt_def.get('groups') or []),
            'colors': list(fmt_def.get('colors') or []),
            'colorSwatches': list(fmt_def.get('swatches') or []),
            'maximumFont': fmt_def['maximumFont'],
            'headingFont': fmt_def['headingFont'],
            'numberLength': fmt_def['numberLength'],
            'sourceRow': fmt_def['sourceRow'],
            'manual': True,
            **{k: v for k, v in _benkama_spec(fmt_def).items() if v is not None},
            'samples': [],
        }
        if item:
            payload['dbId'] = item.get('formatId')
            paths = item.get('samplePaths') or (
                [item['samplePath']] if item.get('samplePath') else []
            )
            for idx, paper in enumerate(fmt_def['papers']):
                rel = paths[idx] if idx < len(paths) else ''
                payload['samples'].append({
                    'key': _three_variant_key(fmt_def['papers'], paper),
                    'condition': paper,
                    'paperColor': paper,
                    'relativePath': rel,
                    'caption': fmt_def['caption'],
                })
        if existing is None:
            formats.append(payload)
            changed = True
        else:
            for key, value in payload.items():
                if existing.get(key) != value:
                    existing[key] = value
                    changed = True
    if changed:
        data['customers'] = customers
        data['formats'] = formats
        data['customerCount'] = len(customers)
        data['formatCount'] = len(formats)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return changed


def ensure_benkama_name_formats(c, data_dir=None):
    """Excel 行159–160 弁釜のネーム形式を追加／更新する。他顧客は変更しない。"""
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'benkama-names-{stamp}'
    cust = _benkama_customer_def()
    fmt_defs = cust['formats']
    items = []
    open_items = list(BENKAMA_OPEN_ITEMS)

    customer = _benkama_find_customer(c)
    needs_work = customer is None
    if not needs_work:
        # 別名のままなら Excel 名へ寄せる
        if str(customer['source_name'] or '') != BENKAMA_CUSTOMER_NAME:
            needs_work = True
        else:
            for fmt_def in fmt_defs:
                row = c.execute(
                    'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                    (fmt_def['identity'],),
                ).fetchone()
                if row is None:
                    needs_work = True
                    break
                samples = c.execute(
                    'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                    (row['id'],),
                ).fetchall()
                if not _benkama_is_current(row, samples, fmt_def):
                    needs_work = True
                    break

    if not needs_work:
        report = {
            'at': stamp,
            'changed': 0,
            'policy': BENKAMA_NAMES_POLICY,
            'backupDir': '',
            'catalogChanged': False,
            'openItems': open_items,
            'customers': [BENKAMA_CUSTOMER_NAME],
            'items': [],
            'reason': 'already-applied',
        }
        (root / BENKAMA_NAMES_REPORT).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        c.execute(
            'INSERT OR REPLACE INTO settings VALUES(?,?)',
            (
                BENKAMA_NAMES_SETTINGS_KEY,
                json.dumps({
                    'at': stamp,
                    'changed': 0,
                    'report': BENKAMA_NAMES_REPORT,
                    'policy': BENKAMA_NAMES_POLICY,
                }, ensure_ascii=False),
            ),
        )
        return report

    backup_dir.mkdir(parents=True, exist_ok=True)
    customer, created = _benkama_ensure_customer(c, backup_dir, items)
    claimed_ids = set()

    for fmt_def in fmt_defs:
        by_identity = c.execute(
            'SELECT * FROM name_formats WHERE identity=? AND retired=0',
            (fmt_def['identity'],),
        ).fetchone()
        existing = by_identity
        if existing is None:
            candidates = c.execute(
                '''SELECT * FROM name_formats WHERE customer_id=? AND retired=0
                   AND placement=? AND small=? ORDER BY id''',
                (customer['id'], fmt_def['placement'], int(fmt_def['small'])),
            ).fetchall()
            matched = [cand for cand in candidates if cand['id'] not in claimed_ids and _benkama_match_legacy(cand, fmt_def)]
            if len(matched) == 1:
                existing = matched[0]
            elif len(matched) > 1:
                existing = matched[0]
            elif len(candidates) == 1 and candidates[0]['id'] not in claimed_ids:
                # 旧仕様が1件のみ（色＝白など）なら社員行へ寄せる
                if fmt_def['kind'] == 'benkama-staff':
                    existing = candidates[0]

        samples = []
        if existing:
            claimed_ids.add(existing['id'])
            samples = c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (existing['id'],),
            ).fetchall()
            if _benkama_is_current(existing, samples, fmt_def):
                items.append({
                    'action': 'skip',
                    'kind': fmt_def['kind'],
                    'formatId': existing['id'],
                    'number': _number(existing['id']),
                    'identity': fmt_def['identity'],
                    'customerName': BENKAMA_CUSTOMER_NAME,
                    'reason': 'already-current',
                })
                continue
            _backup_benkama_format(c, root, backup_dir, existing, samples)
            fid = _upsert_benkama_format_row(c, customer['id'], fmt_def, existing=existing)
            ok, note, paths = _write_benkama_samples(c, root, fid, fmt_def)
            items.append({
                'action': 'update',
                'kind': fmt_def['kind'],
                'formatId': fid,
                'number': _number(fid),
                'identity': fmt_def['identity'],
                'customerName': BENKAMA_CUSTOMER_NAME,
                'sourceRow': fmt_def['sourceRow'],
                'samplePaths': paths,
                'samplePath': paths[0] if paths else '',
                'sampleOk': ok,
                'sampleNote': note,
                'customerCreated': created,
            })
        else:
            fid = _upsert_benkama_format_row(c, customer['id'], fmt_def, existing=None)
            claimed_ids.add(fid)
            ok, note, paths = _write_benkama_samples(c, root, fid, fmt_def)
            items.append({
                'action': 'create',
                'kind': fmt_def['kind'],
                'formatId': fid,
                'number': _number(fid),
                'identity': fmt_def['identity'],
                'customerName': BENKAMA_CUSTOMER_NAME,
                'sourceRow': fmt_def['sourceRow'],
                'samplePaths': paths,
                'samplePath': paths[0] if paths else '',
                'sampleOk': ok,
                'sampleNote': note,
                'customerCreated': created,
            })

    catalog_changed = _patch_benkama_catalog(root, backup_dir, fmt_defs, items)
    changed = sum(
        1 for i in items
        if i.get('action') in ('create', 'update', 'create-customer', 'rename-customer')
    )
    report = {
        'at': stamp,
        'changed': changed,
        'policy': BENKAMA_NAMES_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'catalogChanged': catalog_changed,
        'openItems': open_items,
        'customers': [BENKAMA_CUSTOMER_NAME],
        'excel': {
            'path': 'media/ネーム仕分け_五十音順2.xlsx',
            'rows': {BENKAMA_CUSTOMER_NAME: [159, 160]},
            'diff': {
                '色(行159)': {'old': '白', 'new': BENKAMA_STAFF_COLOR_CAPTION},
                '色(行160)': {
                    'old': '札幌1–4／旭川1–4のみ',
                    'new': '札幌1–4＋旭川1–10＋函館1–8＋八軒1期',
                },
                '備考(行160)': {
                    'old': 'ラベル色は５期生以降ループ',
                    'new': 'ラベル色は５期生以降ループ（札幌のみ、他は規則性なし？）',
                },
            },
        },
        'sampleNames': {
            'full': BENKAMA_SAMPLE_FULL,
            'periodKatakana': BENKAMA_SAMPLE_PERIOD,
            'employeeNumber': BENKAMA_HEADING_SAMPLE,
        },
        'rules': {
            'onlyBenkamaCustomer': True,
            'createIfMissing': True,
            'noDuplicateIdentities': True,
            'noAssigneeChange': True,
            'noAutoApprove': True,
            'noTouchOtherCustomers': True,
        },
        'items': items,
    }
    report_path = root / BENKAMA_NAMES_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (backup_dir / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            BENKAMA_NAMES_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': changed,
                'report': BENKAMA_NAMES_REPORT,
                'policy': BENKAMA_NAMES_POLICY,
                'backupDir': report['backupDir'],
            }, ensure_ascii=False),
        ),
    )
    return report


# --- 6社ネーム更新（健診/タケダ/フレッシュ/モリタン + FK検証 + 鱗幸は correct_rinko） ---
# Excel: ネーム仕分け_五十音順2（行82/109-110/159/211-212）。明日佳・あすかHOUSE系は触らない。

SIX_NAMES_POLICY = 'excel-gojuon2-six-v2'
SIX_NAMES_SETTINGS_KEY = 'six_customers_name_formats'
SIX_NAMES_REPORT = 'six-customers-name-formats-report.json'
SIX_FAMILY_HG = THREE_FAMILY
SIX_FAMILY_MSP = 'MSPゴシック'
SIX_PAPER_RGB = {
    '白': (255, 255, 255),
    '緑': (144, 238, 144),  # #90EE90
    '青': (135, 206, 250),  # #87CEFA
    '赤': (240, 128, 128),  # #F08080
}
SIX_SAMPLE_FULL = THREE_SAMPLE_FULL
SIX_SAMPLE_FULL_FEMALE = THREE_SAMPLE_FULL_FEMALE
SIX_SAMPLE_SURNAME = THREE_SAMPLE_SURNAME
SIX_SAMPLE_SIZE = 'L'  # サイズ表記の見本文字（Excel例なし→確認事項）

KENSHIN_OLD_NAME = '札幌健診センター'
KENSHIN_CUSTOMER_NAME = '札幌健診センター/札幌きたはち健診センター（健診衣）'
KENSHIN_CUSTOMER_KEY = 'excel:kenshin-kitahachi'
KENSHIN_CUSTOMER_READING = 'さっぽろけんしん'
KENSHIN_ALIASES = (KENSHIN_OLD_NAME, KENSHIN_CUSTOMER_NAME)

TAKEDA_CUSTOMER_NAME = 'タケダ海産'
TAKEDA_CUSTOMER_KEY = 'excel:takeda-kaisan'
TAKEDA_CUSTOMER_READING = 'たけだかいさん'
TAKEDA_KEEP_IDENTITIES = frozenset({'takeda:large-fullname', 'takeda:small-fullname'})
TAKEDA_SMALL_PLACEMENT = '白下：ベルト後左　白帽子：右耳上'
TAKEDA_LARGE_PLACEMENT = '白衣：左袖25cm'
TAKEDA_DUP_REPORT = 'takeda-duplicate-formats-report.json'
TAKEDA_DUP_SETTINGS_KEY = 'takeda_duplicate_formats'
TAKEDA_DUP_POLICY = 'retire-duplicate-small-v1'

FRESH_CUSTOMER_NAME = 'フレッシュフーズ'
FRESH_CUSTOMER_KEY = 'excel:fresh-foods'
FRESH_CUSTOMER_READING = 'ふれっしゅふーず'
FRESH_KEEP_IDENTITIES = frozenset({'fresh:small-surname'})
FRESH_ROLE_GROUPS = ['社員', 'パート']
FRESH_ROLE_COLORS = ['緑', '青']
FRESH_LEGACY_REPORT = 'fresh-legacy-formats-report.json'
FRESH_LEGACY_SETTINGS_KEY = 'fresh_legacy_formats'
FRESH_LEGACY_POLICY = 'retire-legacy-keep-36-v1'

MORITAN_CUSTOMER_NAME = 'モリタン紋別'
MORITAN_CUSTOMER_KEY = 'excel:moritan-mombetsu'
MORITAN_CUSTOMER_READING = 'もりたんもんべつ'
MORITAN_KEEP_IDENTITIES = frozenset({'moritan:large-fullname', 'moritan:small-fullname'})
MORITAN_ROLE_GROUPS = ['社員', '派遣', '工員', '研修生']
MORITAN_ROLE_COLORS = ['白', '赤', '青', '緑']
MORITAN_SOLO_WHITE_REPORT = 'moritan-solo-white-report.json'
MORITAN_SOLO_WHITE_SETTINGS_KEY = 'moritan_solo_white'
MORITAN_SOLO_WHITE_POLICY = 'retire-solo-white-v1'

# 明日佳・あすかHOUSE系は HARD EXCLUSION（誤検知防止の照合用）
SIX_HARD_EXCLUSIONS = (
    '札幌/桜台/白石/岩見沢/宮の沢明日佳',
    'あすかHOUSE',
    'あすかHOUSE東苗穂',
)

SIX_OPEN_ITEMS = [
    '健診・タケダ・フレッシュ・モリタンの実寸は Excel に mm 無し。既存どおり小50×24／大80×50を採用（当該顧客で既に使用時）',
    '健診「サイズ表記」の見本文字は Excel 例が無いため「L」を仮置き（要確認）',
    'タケダ大ネーム：旧役職2段見本はバックアップ後に大汎用1段見本へ置換。役職専用見本の扱い要確認',
    '鱗幸「先方から指定？」表記と黄色専用見本は既存 yellow policy を維持（黄色見本は再追加しない）',
    '鱗幸大ネーム：Excel 列1のため大汎用1段（上段○○削除）。フォントサイズ欄の「上段：26　下段：大汎用」表記は Excel 原文のまま',
    'モリタン：色グループ更新後に残る単色白専用形式は correct_moritan_solo_white で退職',
    'フレッシュ：旧「先方から指定／フルネーム／小汎用」は退職。新形式は利用者指定で36pt exact（Excel行159は小汎用表記）',
    'タケダ：小ネーム重複は correct_takeda_duplicate_formats で1件残し退職。旧役職大が残れば同様に整理',
    'エフケーは #167 仕様と一致すれば no-op（36pt exact-fit 維持）。Excel行番号31への追随はしない',
]


def _six_variant_key(papers, paper):
    if len(papers) == 1:
        return 'standard'
    return f'paper-{paper}'


def _six_format_specs():
    """対象4社の形式定義。identity 固定で重複追加を防ぐ。"""
    full_large_pt = _three_generic_pt('large', SIX_SAMPLE_FULL)
    full_small_pt = _three_generic_pt('small', SIX_SAMPLE_FULL)
    surname_small_pt = _three_generic_pt('small', SIX_SAMPLE_SURNAME)

    kenshin_note = (
        f'書体は{SIX_FAMILY_MSP}。小ネーム・1段・サイズ表記／36pt。'
        f'Excel 行82（{KENSHIN_CUSTOMER_NAME}）。'
    )
    kenshin_caption = (
        f'見本：サイズ表記「{SIX_SAMPLE_SIZE}」（36pt・1段）。'
        f'書体は{SIX_FAMILY_MSP}。用紙色は白。'
    )

    takeda_large_note = (
        f'書体は{SIX_FAMILY_HG}（太字）。大ネーム・1段・フルネーム／大汎用。列1。'
        'Excel 行109（旧2段見出しは廃止し1列大汎用へ統一）。'
    )
    takeda_large_caption = (
        f'見本：フルネーム「{SIX_SAMPLE_FULL}」（大汎用・1段）。'
        f'書体は{SIX_FAMILY_HG}（太字）。用紙色は白。'
        f'女性用フルネームは「{SIX_SAMPLE_FULL_FEMALE}」。'
    )
    takeda_small_note = (
        f'書体は{SIX_FAMILY_HG}（太字）。小ネーム・1段・フルネーム／小汎用。'
        'Excel 行110。'
    )
    takeda_small_caption = (
        f'見本：フルネーム「{SIX_SAMPLE_FULL}」（小汎用・1段）。'
        f'書体は{SIX_FAMILY_HG}（太字）。用紙色は白。'
        f'女性用フルネームは「{SIX_SAMPLE_FULL_FEMALE}」。'
    )

    fresh_note = (
        f'書体は{SIX_FAMILY_HG}（太字）。色は社員：緑／パート：青。'
        '小ネーム・1段・苗字のみ／36pt（exact・fill拡大なし）。'
        'Excel 行159のフォントサイズ欄は小汎用だが、登録は36ptへ揃える。'
    )
    fresh_caption = (
        f'見本：苗字「{SIX_SAMPLE_SURNAME}」（36pt・exact・1段）。'
        f'書体は{SIX_FAMILY_HG}（太字）。色は社員：緑／パート：青。'
    )

    moritan_color = '社員：白　派遣：赤　工員：青　研修生：緑'
    moritan_groups = ['社員', '派遣', '工員', '研修生']
    moritan_colors = ['白', '赤', '青', '緑']
    moritan_swatches = ['#FFFFFF', '#F08080', '#87CEFA', '#90EE90']
    moritan_large_note = (
        f'書体は{SIX_FAMILY_HG}（太字）。色は{moritan_color}。'
        '大ネーム・1段・フルネーム／大汎用。Excel 行211。'
    )
    moritan_small_note = (
        f'書体は{SIX_FAMILY_HG}（太字）。色は{moritan_color}。'
        '小ネーム・1段・フルネーム／小汎用。Excel 行212。'
    )
    moritan_large_caption = (
        f'見本：フルネーム「{SIX_SAMPLE_FULL}」（大汎用・1段）。'
        f'書体は{SIX_FAMILY_HG}（太字）。色は{moritan_color}。'
        f'女性用フルネームは「{SIX_SAMPLE_FULL_FEMALE}」。'
    )
    moritan_small_caption = (
        f'見本：フルネーム「{SIX_SAMPLE_FULL}」（小汎用・1段）。'
        f'書体は{SIX_FAMILY_HG}（太字）。色は{moritan_color}。'
        f'女性用フルネームは「{SIX_SAMPLE_FULL_FEMALE}」。'
    )

    return (
        {
            'customerKey': KENSHIN_CUSTOMER_KEY,
            'customerName': KENSHIN_CUSTOMER_NAME,
            'reading': KENSHIN_CUSTOMER_READING,
            'aliases': KENSHIN_ALIASES,
            'formats': (
                {
                    'identity': 'kenshin:small-size',
                    'kind': 'kenshin-small-size',
                    'sourceRow': 82,
                    'formatName': '小ネーム（サイズ表記）',
                    'placement': '健診衣上下',
                    'family': SIX_FAMILY_MSP,
                    'heading': 'サイズ表記（36pt）',
                    'note': kenshin_note,
                    'layout': 'FreeText',
                    'reading': 'サイズ表記',
                    'small': 1,
                    'widthMm': THREE_SMALL_W,
                    'heightMm': THREE_SMALL_H,
                    'fixedColor': '白',
                    'colorCaption': '白',
                    'groups': [],
                    'colors': [],
                    'swatches': [],
                    'maximumFont': 36,
                    'sampleText': SIX_SAMPLE_SIZE,
                    'fontPt': 36,
                    'sampleFit': 'exact',
                    'papers': ('白',),
                    'caption': kenshin_caption,
                },
            ),
        },
        {
            'customerKey': TAKEDA_CUSTOMER_KEY,
            'customerName': TAKEDA_CUSTOMER_NAME,
            'reading': TAKEDA_CUSTOMER_READING,
            'aliases': (TAKEDA_CUSTOMER_NAME,),
            'formats': (
                {
                    'identity': 'takeda:large-fullname',
                    'kind': 'takeda-large-fullname',
                    'sourceRow': 109,
                    'formatName': '大ネーム（フルネーム）',
                    'placement': '白衣：左袖25cm',
                    'family': SIX_FAMILY_HG,
                    'heading': 'フルネーム（大汎用）',
                    'note': takeda_large_note,
                    'layout': 'Name',
                    'reading': 'フルネーム',
                    'small': 0,
                    'widthMm': THREE_LARGE_W,
                    'heightMm': THREE_LARGE_H,
                    'fixedColor': '白',
                    'colorCaption': '白',
                    'groups': [],
                    'colors': [],
                    'swatches': [],
                    'maximumFont': THREE_GENERIC_LARGE[4],
                    'sampleText': SIX_SAMPLE_FULL,
                    'fontPt': full_large_pt,
                    'papers': ('白',),
                    'caption': takeda_large_caption,
                },
                {
                    'identity': 'takeda:small-fullname',
                    'kind': 'takeda-small-fullname',
                    'sourceRow': 110,
                    'formatName': '小ネーム（フルネーム）',
                    'placement': '白下：ベルト後左　白帽子：右耳上',
                    'family': SIX_FAMILY_HG,
                    'heading': 'フルネーム（小汎用）',
                    'note': takeda_small_note,
                    'layout': 'Name',
                    'reading': 'フルネーム',
                    'small': 1,
                    'widthMm': THREE_SMALL_W,
                    'heightMm': THREE_SMALL_H,
                    'fixedColor': '白',
                    'colorCaption': '白',
                    'groups': [],
                    'colors': [],
                    'swatches': [],
                    'maximumFont': THREE_GENERIC_SMALL[4],
                    'sampleText': SIX_SAMPLE_FULL,
                    'fontPt': full_small_pt,
                    'papers': ('白',),
                    'caption': takeda_small_caption,
                },
            ),
        },
        {
            'customerKey': FRESH_CUSTOMER_KEY,
            'customerName': FRESH_CUSTOMER_NAME,
            'reading': FRESH_CUSTOMER_READING,
            'aliases': (FRESH_CUSTOMER_NAME,),
            'formats': (
                {
                    'identity': 'fresh:small-surname',
                    'kind': 'fresh-small-surname',
                    'sourceRow': 159,
                    'formatName': '小ネーム（苗字のみ）',
                    'placement': '白衣：左袖25cm　白下：左前　白帽子：右耳上',
                    'family': SIX_FAMILY_HG,
                    'heading': '苗字のみ（36pt）',
                    'note': fresh_note,
                    'layout': 'Surname',
                    'reading': '苗字のみ',
                    'small': 1,
                    'widthMm': THREE_SMALL_W,
                    'heightMm': THREE_SMALL_H,
                    'fixedColor': '',
                    'colorCaption': '社員：緑　パート：青',
                    'groups': ['社員', 'パート'],
                    'colors': ['緑', '青'],
                    'swatches': ['#90EE90', '#87CEFA'],
                    'maximumFont': 36,
                    'sampleText': SIX_SAMPLE_SURNAME,
                    'fontPt': 36,
                    'sampleFit': 'exact',
                    'papers': ('緑', '青'),
                    'caption': fresh_caption,
                    'surnameOnly': True,
                },
            ),
        },
        {
            'customerKey': MORITAN_CUSTOMER_KEY,
            'customerName': MORITAN_CUSTOMER_NAME,
            'reading': MORITAN_CUSTOMER_READING,
            'aliases': (MORITAN_CUSTOMER_NAME,),
            'formats': (
                {
                    'identity': 'moritan:large-fullname',
                    'kind': 'moritan-large-fullname',
                    'sourceRow': 211,
                    'formatName': '大ネーム（フルネーム）',
                    'placement': '白衣：左袖25cm',
                    'family': SIX_FAMILY_HG,
                    'heading': 'フルネーム（大汎用）',
                    'note': moritan_large_note,
                    'layout': 'Name',
                    'reading': 'フルネーム',
                    'small': 0,
                    'widthMm': THREE_LARGE_W,
                    'heightMm': THREE_LARGE_H,
                    'fixedColor': '',
                    'colorCaption': moritan_color,
                    'groups': list(moritan_groups),
                    'colors': list(moritan_colors),
                    'swatches': list(moritan_swatches),
                    'maximumFont': THREE_GENERIC_LARGE[4],
                    'sampleText': SIX_SAMPLE_FULL,
                    'fontPt': full_large_pt,
                    'papers': tuple(moritan_colors),
                    'caption': moritan_large_caption,
                },
                {
                    'identity': 'moritan:small-fullname',
                    'kind': 'moritan-small-fullname',
                    'sourceRow': 212,
                    'formatName': '小ネーム（フルネーム）',
                    'placement': '白下：左前　白帽子：左耳上',
                    'family': SIX_FAMILY_HG,
                    'heading': 'フルネーム（小汎用）',
                    'note': moritan_small_note,
                    'layout': 'Name',
                    'reading': 'フルネーム',
                    'small': 1,
                    'widthMm': THREE_SMALL_W,
                    'heightMm': THREE_SMALL_H,
                    'fixedColor': '',
                    'colorCaption': moritan_color,
                    'groups': list(moritan_groups),
                    'colors': list(moritan_colors),
                    'swatches': list(moritan_swatches),
                    'maximumFont': THREE_GENERIC_SMALL[4],
                    'sampleText': SIX_SAMPLE_FULL,
                    'fontPt': full_small_pt,
                    'papers': tuple(moritan_colors),
                    'caption': moritan_small_caption,
                },
            ),
        },
    )


def _six_spec(fmt_def):
    spec = {
        'sixKind': fmt_def['kind'],
        'sixPolicy': SIX_NAMES_POLICY,
        'sampleText': fmt_def['sampleText'],
        'fontPt': fmt_def['fontPt'],
        'papers': list(fmt_def['papers']),
    }
    if fmt_def.get('sampleFit'):
        spec['sampleFit'] = fmt_def['sampleFit']
    if fmt_def.get('surnameOnly'):
        spec['surnameOnly'] = True
    groups = fmt_def.get('groups') or []
    colors = fmt_def.get('colors') or []
    if groups and colors and len(groups) == len(colors):
        spec['colorRoles'] = dict(zip(groups, colors))
    return spec


def _six_is_excluded_name(name):
    text = str(name or '').strip()
    if not text:
        return False
    for ex in SIX_HARD_EXCLUSIONS:
        if text == ex or text.startswith(ex):
            return True
    if '明日佳' in text or '明日香' in text:
        return True
    if 'あすかHOUSE' in text:
        return True
    return False


def _six_find_customer(c, cust):
    rows = c.execute(
        'SELECT * FROM name_customers WHERE retired=0 ORDER BY id'
    ).fetchall()
    aliases = tuple(cust.get('aliases') or (cust['customerName'],))
    for row in rows:
        if str(row['source_key'] or '') == cust['customerKey']:
            return row
    for row in rows:
        name = str(row['source_name'] or '').strip()
        if _six_is_excluded_name(name):
            continue
        if name in aliases or name == cust['customerName']:
            return row
    if cust['customerKey'] == KENSHIN_CUSTOMER_KEY:
        for row in rows:
            name = str(row['source_name'] or '').strip()
            if name == KENSHIN_OLD_NAME:
                return row
    return None


def _six_ensure_customer(c, cust, backup_dir, items):
    existing = _six_find_customer(c, cust)
    if existing:
        if _six_is_excluded_name(existing['source_name']):
            raise RuntimeError(f'除外顧客を更新しようとした: {existing["source_name"]}')
        changed = False
        if str(existing['source_name'] or '') != cust['customerName']:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f'customer-rename-{existing["id"]}.json').write_text(
                json.dumps({
                    'before': _row_as_dict(existing),
                    'afterName': cust['customerName'],
                }, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            c.execute(
                'UPDATE name_customers SET source_name=?, source_key=?, reading=? WHERE id=?',
                (
                    cust['customerName'], cust['customerKey'],
                    cust.get('reading') or '', existing['id'],
                ),
            )
            changed = True
            items.append({
                'action': 'rename-customer',
                'customerId': existing['id'],
                'from': existing['source_name'],
                'to': cust['customerName'],
            })
        elif str(existing['source_key'] or '') != cust['customerKey']:
            c.execute(
                'UPDATE name_customers SET source_key=?, reading=? WHERE id=?',
                (cust['customerKey'], cust.get('reading') or '', existing['id']),
            )
            changed = True
        if changed:
            existing = c.execute(
                'SELECT * FROM name_customers WHERE id=?', (existing['id'],),
            ).fetchone()
        return existing, False
    known = _app_customers(c)
    linked, status = _link(cust['customerName'], known)
    if not linked:
        for alias in (cust.get('aliases') or ()):
            if alias in known:
                linked, status = alias, '対応'
                break
    c.execute(
        'INSERT INTO name_customers('
        'source_key, source_index, source_name, reading, linked_name, link_status, manual, retired'
        ') VALUES(?,?,?,?,?,?,1,0)',
        (
            cust['customerKey'], -1, cust['customerName'],
            cust.get('reading') or '', linked, status,
        ),
    )
    cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    row = c.execute('SELECT * FROM name_customers WHERE id=?', (cid,)).fetchone()
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / f'customer-created-{cid}.json').write_text(
        json.dumps(_row_as_dict(row), ensure_ascii=False, indent=2), encoding='utf-8',
    )
    items.append({
        'action': 'create-customer',
        'customerId': cid,
        'customerName': cust['customerName'],
        'sourceKey': cust['customerKey'],
    })
    return row, True


def _six_is_current(fmt, samples, fmt_def):
    if str(fmt['identity'] or '') != fmt_def['identity']:
        return False
    if str(fmt['format_name'] or '') != fmt_def['formatName']:
        return False
    if str(fmt['placement'] or '') != fmt_def['placement']:
        return False
    if str(fmt['family'] or '') != fmt_def['family']:
        return False
    if str(fmt['heading'] or '') != fmt_def['heading']:
        return False
    if str(fmt['note'] or '') != fmt_def['note']:
        return False
    if str(fmt['layout'] or '') != fmt_def['layout']:
        return False
    if str(fmt['reading'] or '') != fmt_def['reading']:
        return False
    if int(fmt['small'] or 0) != int(fmt_def['small']):
        return False
    if int(fmt['width_mm'] or 0) != int(fmt_def['widthMm']):
        return False
    if int(fmt['height_mm'] or 0) != int(fmt_def['heightMm']):
        return False
    if str(fmt['fixed_color'] or '') != fmt_def['fixedColor']:
        return False
    if str(fmt['color_caption'] or '') != fmt_def['colorCaption']:
        return False
    if float(fmt['maximum_font'] or 0) != float(fmt_def['maximumFont']):
        return False
    if int(fmt['source_row'] or 0) != int(fmt_def['sourceRow']):
        return False
    if fmt_def['kind'] == 'takeda-large-fullname':
        # 旧「上段：役職」見出し／形式名のみを未適用とみなす（備考の説明文は対象外）
        if '役職' in str(fmt['heading'] or '') or '役職' in str(fmt['format_name'] or ''):
            return False
    spec = _load_spec(fmt)
    if spec.get('sixPolicy') != SIX_NAMES_POLICY or spec.get('sixKind') != fmt_def['kind']:
        return False
    want_fit = fmt_def.get('sampleFit') or ''
    if (spec.get('sampleFit') or '') != want_fit:
        return False
    if fmt_def.get('surnameOnly') and spec.get('surnameOnly') is not True:
        return False
    try:
        have_groups = json.loads(fmt['groups_json'] or '[]')
        have_colors = json.loads(fmt['colors_json'] or '[]')
        have_swatches = json.loads(fmt['swatches_json'] or '[]')
    except (TypeError, json.JSONDecodeError):
        return False
    if have_groups != list(fmt_def.get('groups') or []):
        return False
    if have_colors != list(fmt_def.get('colors') or []):
        return False
    if have_swatches != list(fmt_def.get('swatches') or []):
        return False
    if len(samples) != len(fmt_def['papers']):
        return False
    by_paper = {str(s['paper_color'] or ''): s for s in samples}
    for paper in fmt_def['papers']:
        sample = by_paper.get(paper)
        if sample is None:
            return False
        want_key = _six_variant_key(fmt_def['papers'], paper)
        if str(sample['variant_key'] or '') != want_key:
            return False
        rel = str(sample['relative_path'] or '')
        if not rel.startswith('images/names/'):
            return False
        if fmt_def['kind'] not in Path(rel).name:
            return False
        if str(sample['caption'] or '') != fmt_def['caption']:
            return False
    return True


def _six_match_legacy(fmt, fmt_def):
    """旧形式を identity 無しで拾う（重複作成防止）。"""
    if int(fmt['small'] or 0) != int(fmt_def['small']):
        return False
    placement = str(fmt['placement'] or '')
    if placement and placement != fmt_def['placement']:
        if fmt_def['kind'].startswith('kenshin'):
            return False
    name = str(fmt['format_name'] or '')
    if fmt_def['small']:
        if not (name.startswith('小') or '小ネーム' in name or int(fmt['small'] or 0) == 1):
            return False
    else:
        if name.startswith('小'):
            return False
    if fmt_def['kind'] == 'takeda-large-fullname':
        if placement and placement != fmt_def['placement']:
            return False
        return True
    if placement == fmt_def['placement']:
        return True
    if not placement:
        return True
    return False


def _backup_six_format(c, root, backup_dir, fmt, samples):
    backup_dir.mkdir(parents=True, exist_ok=True)
    images_backup = backup_dir / 'images'
    images_backup.mkdir(parents=True, exist_ok=True)
    payload = {
        'format': _row_as_dict(fmt),
        'samples': [_row_as_dict(s) for s in samples],
    }
    (backup_dir / f'format-{fmt["id"]}.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    for sample in samples:
        rel = str(sample['relative_path'] or '')
        if not rel:
            continue
        src = root / rel
        if src.is_file():
            dest = images_backup / f'{fmt["id"]}-{Path(rel).name}'
            if not dest.exists():
                shutil.copy2(src, dest)


def _write_six_samples(c, root, format_id, fmt_def):
    band = _three_band(
        fmt_def['sampleText'],
        height_mm=fmt_def['heightMm'],
        font_pt=fmt_def['fontPt'],
    )
    fit_mode = fmt_def.get('sampleFit') or 'fill'
    c.execute('DELETE FROM name_samples WHERE format_id=?', (format_id,))
    notes = []
    paths = []
    for paper in fmt_def['papers']:
        key = _six_variant_key(fmt_def['papers'], paper)
        stem = f'{fmt_def["kind"]}-{paper}' if len(fmt_def['papers']) > 1 else fmt_def['kind']
        rel = f'images/names/{stem}-{format_id:06}.png'
        dst = root / rel
        rgb = SIX_PAPER_RGB.get(paper, (255, 255, 255))
        ok, note = _render_ashuka_banded_sample(
            dst, (band,),
            width_mm=fmt_def['widthMm'],
            height_mm=fmt_def['heightMm'],
            family_hint=fmt_def['family'],
            paper_rgb=rgb,
            fit_mode=fit_mode,
        )
        notes.append(f'{paper}:{note}')
        if not ok:
            return False, '; '.join(notes), paths
        c.execute(
            'INSERT INTO name_samples(format_id, variant_key, condition_label, paper_color, relative_path, caption)'
            ' VALUES(?,?,?,?,?,?)',
            (
                format_id, key,
                paper if len(fmt_def['papers']) > 1 else '',
                paper, rel, fmt_def['caption'],
            ),
        )
        paths.append(rel)
    return True, '; '.join(notes), paths


def _upsert_six_format_row(c, customer_id, fmt_def, existing=None):
    groups = json.dumps(fmt_def.get('groups') or [], ensure_ascii=False)
    colors = json.dumps(fmt_def.get('colors') or [], ensure_ascii=False)
    swatches = json.dumps(fmt_def.get('swatches') or [], ensure_ascii=False)
    spec = json.dumps(_six_spec(fmt_def), ensure_ascii=False)
    values = (
        fmt_def['formatName'], fmt_def['placement'], fmt_def['family'], '',
        fmt_def['heading'], fmt_def['note'], fmt_def['layout'], fmt_def['reading'],
        int(fmt_def['small']), int(fmt_def['widthMm']), int(fmt_def['heightMm']), 0,
        fmt_def['fixedColor'], fmt_def['colorCaption'],
        groups, colors, swatches, 'null',
        '', float(fmt_def['maximumFont']), 0, 0, int(fmt_def['sourceRow']),
        spec, 0, '[]', 0,
    )
    if existing:
        c.execute(
            'UPDATE name_formats SET format_name=?, placement=?, family=?, header_family=?, heading=?, note=?,'
            ' layout=?, reading=?, small=?, width_mm=?, height_mm=?, unknown_size=?, fixed_color=?, color_caption=?,'
            ' groups_json=?, colors_json=?, swatches_json=?, departments_json=?, pending=?, maximum_font=?,'
            ' heading_font=?, number_length=?, source_row=?, spec_json=?, needs_review=?, review_json=?, blank=?,'
            ' retired=0, identity=? WHERE id=?',
            values + (fmt_def['identity'], existing['id']),
        )
        return existing['id']
    c.execute(
        'INSERT INTO name_formats('
        'identity, customer_id, source_kind, format_name, placement, family, header_family, heading, note,'
        'layout, reading, small, width_mm, height_mm, unknown_size, fixed_color, color_caption,'
        'groups_json, colors_json, swatches_json, departments_json, pending, maximum_font, heading_font,'
        'number_length, source_row, previous_source_row, spec_json, needs_review, review_json, blank, manual, retired'
        ') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)',
        (
            fmt_def['identity'], customer_id, -1,
            values[0], values[1], values[2], values[3], values[4], values[5],
            values[6], values[7], values[8], values[9], values[10], values[11],
            values[12], values[13], values[14], values[15], values[16], values[17],
            values[18], values[19], values[20], values[21], values[22], None,
            values[23], values[24], values[25], values[26],
        ),
    )
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _patch_six_catalog(data_dir, backup_dir, customer_defs, items):
    path = Path(data_dir) / 'name-import' / 'catalog.json'
    if not path.is_file():
        return False
    backup_dir.mkdir(parents=True, exist_ok=True)
    catalog_backup = backup_dir / 'catalog.json'
    if not catalog_backup.exists():
        shutil.copy2(path, catalog_backup)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False
    customers = list(data.get('customers') or [])
    formats = list(data.get('formats') or [])
    by_key = {c.get('sourceKey'): c for c in customers}
    changed = False
    item_by_identity = {i.get('identity'): i for i in items if i.get('identity')}
    for cust in customer_defs:
        if cust['customerKey'] not in by_key:
            renamed = False
            for entry in customers:
                name = str(entry.get('name') or '').strip()
                if _six_is_excluded_name(name):
                    continue
                if name in (cust.get('aliases') or ()) or name == cust['customerName']:
                    entry['sourceKey'] = cust['customerKey']
                    entry['name'] = cust['customerName']
                    entry['reading'] = cust.get('reading') or entry.get('reading') or ''
                    by_key[cust['customerKey']] = entry
                    changed = True
                    renamed = True
                    break
            if not renamed:
                customers.append({
                    'sourceKey': cust['customerKey'],
                    'sourceIndex': -1,
                    'name': cust['customerName'],
                    'reading': cust.get('reading') or '',
                })
                by_key[cust['customerKey']] = customers[-1]
                changed = True
        else:
            entry = by_key[cust['customerKey']]
            if _six_is_excluded_name(entry.get('name')):
                continue
            if entry.get('name') != cust['customerName']:
                entry['name'] = cust['customerName']
                changed = True
        for fmt_def in cust['formats']:
            item = item_by_identity.get(fmt_def['identity'])
            existing = next(
                (f for f in formats if f.get('identity') == fmt_def['identity']),
                None,
            )
            payload = {
                'identity': fmt_def['identity'],
                'customerKey': cust['customerKey'],
                'sourceKind': -1,
                'format': fmt_def['formatName'],
                'placement': fmt_def['placement'],
                'family': fmt_def['family'],
                'heading': fmt_def['heading'],
                'note': fmt_def['note'],
                'layout': fmt_def['layout'],
                'reading': fmt_def['reading'],
                'small': bool(fmt_def['small']),
                'widthMm': fmt_def['widthMm'],
                'heightMm': fmt_def['heightMm'],
                'fixedColor': fmt_def['fixedColor'],
                'colorCaption': fmt_def['colorCaption'],
                'groups': list(fmt_def.get('groups') or []),
                'colors': list(fmt_def.get('colors') or []),
                'colorSwatches': list(fmt_def.get('swatches') or []),
                'maximumFont': fmt_def['maximumFont'],
                'headingFont': 0,
                'sourceRow': fmt_def['sourceRow'],
                'manual': True,
                **{k: v for k, v in _six_spec(fmt_def).items() if v is not None},
                'samples': [],
            }
            if item:
                payload['dbId'] = item.get('formatId')
                paths = item.get('samplePaths') or (
                    [item['samplePath']] if item.get('samplePath') else []
                )
                for idx, paper in enumerate(fmt_def['papers']):
                    rel = paths[idx] if idx < len(paths) else ''
                    payload['samples'].append({
                        'key': _six_variant_key(fmt_def['papers'], paper),
                        'condition': paper if len(fmt_def['papers']) > 1 else '',
                        'paperColor': paper,
                        'relativePath': rel,
                        'caption': fmt_def['caption'],
                    })
            if existing is None:
                formats.append(payload)
                changed = True
            else:
                for key, value in payload.items():
                    if existing.get(key) != value:
                        existing[key] = value
                        changed = True
    if changed:
        data['customers'] = customers
        data['formats'] = formats
        data['customerCount'] = len(customers)
        data['formatCount'] = len(formats)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return changed


def _six_verify_fk(c, data_dir):
    """エフケーは既存 ensure_fk に委譲。一致なら no-op。"""
    report = ensure_fk_name_formats(c, data_dir)
    return {
        'customer': FK_CUSTOMER_NAME,
        'action': 'verify-fk',
        'changed': int(report.get('changed') or 0),
        'reason': report.get('reason') or ('updated' if report.get('changed') else 'matched'),
        'policy': report.get('policy') or FK_NAMES_POLICY,
        'items': report.get('items') or [],
    }


def _six_snapshot_exclusions(c):
    snap = {}
    for row in c.execute(
        "SELECT id, source_name FROM name_customers WHERE retired=0 AND ("
        "source_name LIKE '%明日佳%' OR source_name LIKE '%明日香%' OR source_name LIKE '%あすかHOUSE%')"
    ):
        formats = c.execute(
            'SELECT id, identity, note, color_caption, reading, format_name '
            'FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
            (row['id'],),
        ).fetchall()
        snap[f'customer:{row["id"]}'] = {
            'name': row['source_name'],
            'formats': [
                {
                    'id': f['id'],
                    'identity': f['identity'],
                    'note': f['note'],
                    'color_caption': f['color_caption'],
                    'reading': f['reading'],
                    'format_name': f['format_name'],
                }
                for f in formats
            ],
        }
    return snap


def _six_verify_exclusions_untouched(c, before_snapshot):
    problems = []
    after = _six_snapshot_exclusions(c)
    for key, before in before_snapshot.items():
        if after.get(key) != before:
            problems.append({'key': key, 'name': before.get('name')})
    return problems


def ensure_six_customers_name_formats(c, data_dir=None):
    """Excel 6社更新のうち健診/タケダ/フレッシュ/モリタンを ensure。FK検証も記録。

    鱗幸大ネームの1段化（○○削除）は correct_rinko_name_formats が担当。
    モリタン白専用の残骸削除は correct_moritan_solo_white が担当。
    フレッシュ旧形式の退職は correct_fresh_legacy_formats が担当。
    タケダ小ネーム重複の整理は correct_takeda_duplicate_formats が担当。
    明日佳・あすかHOUSE系は変更しない。
    """
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'six-names-{stamp}'
    customer_defs = _six_format_specs()
    items = []
    open_items = list(SIX_OPEN_ITEMS)
    exclusion_before = _six_snapshot_exclusions(c)

    needs_work = False
    for cust in customer_defs:
        customer = _six_find_customer(c, cust)
        if customer is None:
            needs_work = True
            break
        if str(customer['source_name'] or '') != cust['customerName']:
            needs_work = True
            break
        for fmt_def in cust['formats']:
            row = c.execute(
                'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                (fmt_def['identity'],),
            ).fetchone()
            if row is None:
                needs_work = True
                break
            samples = c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (row['id'],),
            ).fetchall()
            if not _six_is_current(row, samples, fmt_def):
                needs_work = True
                break
        if needs_work:
            break

    if not needs_work:
        fk_verify = _six_verify_fk(c, root)
        report = {
            'at': stamp,
            'changed': 0,
            'policy': SIX_NAMES_POLICY,
            'backupDir': '',
            'catalogChanged': False,
            'openItems': open_items,
            'customers': [cdef['customerName'] for cdef in customer_defs] + [FK_CUSTOMER_NAME, RINKO_CUSTOMER_NAME],
            'fkVerify': fk_verify,
            'exclusionsUntouched': True,
            'hardExclusions': list(SIX_HARD_EXCLUSIONS),
            'items': [],
            'reason': 'already-applied',
        }
        (root / SIX_NAMES_REPORT).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        c.execute(
            'INSERT OR REPLACE INTO settings VALUES(?,?)',
            (
                SIX_NAMES_SETTINGS_KEY,
                json.dumps({
                    'at': stamp,
                    'changed': 0,
                    'report': SIX_NAMES_REPORT,
                    'policy': SIX_NAMES_POLICY,
                }, ensure_ascii=False),
            ),
        )
        return report

    backup_dir.mkdir(parents=True, exist_ok=True)

    for cust in customer_defs:
        customer, created = _six_ensure_customer(c, cust, backup_dir, items)
        if _six_is_excluded_name(customer['source_name']):
            items.append({
                'action': 'skip-excluded',
                'customerName': customer['source_name'],
                'reason': 'hard-exclusion',
            })
            continue
        claimed_ids = set()
        for fmt_def in cust['formats']:
            by_identity = c.execute(
                'SELECT * FROM name_formats WHERE identity=? AND retired=0',
                (fmt_def['identity'],),
            ).fetchone()
            existing = by_identity
            if existing is None:
                candidates = c.execute(
                    'SELECT * FROM name_formats WHERE customer_id=? AND retired=0'
                    ' AND small=? ORDER BY id',
                    (customer['id'], int(fmt_def['small'])),
                ).fetchall()
                matched = [
                    cand for cand in candidates
                    if cand['id'] not in claimed_ids and _six_match_legacy(cand, fmt_def)
                ]
                if len(matched) >= 1:
                    exact = [m for m in matched if str(m['placement'] or '') == fmt_def['placement']]
                    existing = exact[0] if exact else matched[0]

            samples = []
            if existing:
                claimed_ids.add(existing['id'])
                samples = c.execute(
                    'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                    (existing['id'],),
                ).fetchall()
                if _six_is_current(existing, samples, fmt_def):
                    items.append({
                        'action': 'skip',
                        'kind': fmt_def['kind'],
                        'formatId': existing['id'],
                        'number': _number(existing['id']),
                        'identity': fmt_def['identity'],
                        'customerName': cust['customerName'],
                        'reason': 'already-current',
                    })
                    continue
                _backup_six_format(c, root, backup_dir, existing, samples)
                if fmt_def['kind'] == 'takeda-large-fullname' and samples:
                    open_items.append(
                        f'タケダ大 NM-{existing["id"]:06}: 旧見本{len(samples)}件をバックアップ後に大汎用へ置換'
                    )

            fid = _upsert_six_format_row(c, customer['id'], fmt_def, existing=existing)
            if existing is None:
                claimed_ids.add(fid)
            ok, note, paths = _write_six_samples(c, root, fid, fmt_def)
            items.append({
                'action': 'create' if existing is None else 'update',
                'kind': fmt_def['kind'],
                'formatId': fid,
                'number': _number(fid),
                'identity': fmt_def['identity'],
                'customerName': cust['customerName'],
                'sourceRow': fmt_def['sourceRow'],
                'samplePaths': paths,
                'samplePath': paths[0] if paths else '',
                'sampleOk': ok,
                'sampleNote': note,
                'customerCreated': created,
            })

    catalog_changed = _patch_six_catalog(root, backup_dir, customer_defs, items)
    fk_verify = _six_verify_fk(c, root)
    exclusion_problems = _six_verify_exclusions_untouched(c, exclusion_before)
    changed = sum(
        1 for i in items
        if i.get('action') in ('create', 'update', 'create-customer', 'rename-customer')
    )
    report = {
        'at': stamp,
        'changed': changed,
        'policy': SIX_NAMES_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'catalogChanged': catalog_changed,
        'openItems': open_items,
        'customers': [cdef['customerName'] for cdef in customer_defs] + [FK_CUSTOMER_NAME, RINKO_CUSTOMER_NAME],
        'fkVerify': fk_verify,
        'exclusionsUntouched': not exclusion_problems,
        'exclusionProblems': exclusion_problems,
        'hardExclusions': list(SIX_HARD_EXCLUSIONS),
        'excel': {
            'path': 'media/ネーム仕分け_五十音順2.xlsx',
            'rows': {
                KENSHIN_CUSTOMER_NAME: [82],
                TAKEDA_CUSTOMER_NAME: [109, 110],
                FRESH_CUSTOMER_NAME: [159],
                MORITAN_CUSTOMER_NAME: [211, 212],
                FK_CUSTOMER_NAME: [31],
                RINKO_CUSTOMER_NAME: [239, 240],
            },
        },
        'sampleNames': {
            'fullMale': SIX_SAMPLE_FULL,
            'fullFemale': SIX_SAMPLE_FULL_FEMALE,
            'surname': SIX_SAMPLE_SURNAME,
            'size': SIX_SAMPLE_SIZE,
        },
        'rules': {
            'onlySixTargets': True,
            'createIfMissing': True,
            'noDuplicateIdentities': True,
            'noAssigneeChange': True,
            'noAutoApprove': True,
            'noTouchOtherCustomers': True,
            'noTouchAshukaAsuka': True,
        },
        'items': items,
    }
    report_path = root / SIX_NAMES_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (backup_dir / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            SIX_NAMES_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': changed,
                'report': SIX_NAMES_REPORT,
                'policy': SIX_NAMES_POLICY,
                'backupDir': report['backupDir'],
            }, ensure_ascii=False),
        ),
    )
    return report


def _moritan_role_colors_match(fmt):
    try:
        groups = json.loads(fmt['groups_json'] or '[]')
        colors = json.loads(fmt['colors_json'] or '[]')
    except (TypeError, json.JSONDecodeError, KeyError):
        return False
    return groups == MORITAN_ROLE_GROUPS and colors == MORITAN_ROLE_COLORS


def _moritan_is_keep_format(fmt):
    """色グループ付きの正規大／小は残す。"""
    if str(fmt['identity'] or '') in MORITAN_KEEP_IDENTITIES:
        return True
    return _moritan_role_colors_match(fmt)


def _moritan_is_solo_white_format(fmt, samples):
    """旧・単色白だけ登録の残骸か。曖昧なら False（削除しない）。"""
    if _moritan_is_keep_format(fmt):
        return False
    fixed = str(fmt['fixed_color'] or '').strip()
    papers = [str(s['paper_color'] or '').strip() for s in samples]
    non_empty_papers = [p for p in papers if p]
    try:
        groups = json.loads(fmt['groups_json'] or '[]')
        colors = json.loads(fmt['colors_json'] or '[]')
    except (TypeError, json.JSONDecodeError, KeyError):
        groups, colors = [], []
    caption = str(fmt['color_caption'] or '')
    note = str(fmt['note'] or '')
    # 役割色の説明が付いている形式は白専用とみなさない
    if any(token in caption + note for token in ('派遣', '工員', '研修生', '社員：白')):
        return False
    if groups == MORITAN_ROLE_GROUPS or colors == MORITAN_ROLE_COLORS:
        return False
    # 他色見本が1件でもあれば白専用ではない（推測削除しない）
    if any(p and p != '白' for p in non_empty_papers):
        return False
    # 固定色が白、かつ見本が無い／白のみ
    if fixed == '白' and (not non_empty_papers or all(p == '白' for p in non_empty_papers)):
        return True
    # 固定色なし・色グループなし・見本が白1色のみ
    if fixed in ('', '白') and not groups and (not colors or colors == ['白']):
        if non_empty_papers and all(p == '白' for p in non_empty_papers):
            return True
    return False


def correct_moritan_solo_white(c, data_dir=None):
    """モリタン紋別：色グループ更新後に残る単色「白」専用形式をバックアップ後に退職する。

    - 社員白／派遣赤／工員青／研修生緑の正規形式・見本は残す
    - 他顧客・明日佳・あすか・担当者は触らない
    - 曖昧な形式は削除せず ambiguous として報告する
    """
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    customers = c.execute(
        'SELECT id, source_name, source_key FROM name_customers WHERE retired=0'
        ' AND (source_name=? OR source_key=?)',
        (MORITAN_CUSTOMER_NAME, MORITAN_CUSTOMER_KEY),
    ).fetchall()
    if not customers:
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': 'モリタン紋別なし',
            'customer': MORITAN_CUSTOMER_NAME,
            'policy': MORITAN_SOLO_WHITE_POLICY,
        }

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'moritan-solo-white-{stamp}'
    items = []
    ambiguous = []
    removed_formats = 0
    removed_samples = 0

    for customer in customers:
        if _six_is_excluded_name(customer['source_name']):
            continue
        formats = c.execute(
            'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
            (customer['id'],),
        ).fetchall()
        for fmt in formats:
            samples = list(c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (fmt['id'],),
            ))
            if _moritan_is_keep_format(fmt):
                items.append({
                    'action': 'keep',
                    'formatId': fmt['id'],
                    'identity': fmt['identity'],
                    'formatName': fmt['format_name'],
                    'fixedColor': fmt['fixed_color'],
                    'sampleCount': len(samples),
                    'papers': [s['paper_color'] for s in samples],
                })
                continue
            if _moritan_is_solo_white_format(fmt, samples):
                _backup_six_format(c, root, backup_dir, fmt, samples)
                for sample in samples:
                    rel = str(sample['relative_path'] or '')
                    if rel:
                        path = root / rel
                        if path.is_file():
                            path.unlink()
                    c.execute('DELETE FROM name_samples WHERE id=?', (sample['id'],))
                    removed_samples += 1
                c.execute('UPDATE name_formats SET retired=1 WHERE id=?', (fmt['id'],))
                removed_formats += 1
                items.append({
                    'action': 'retire-solo-white',
                    'formatId': fmt['id'],
                    'identity': fmt['identity'],
                    'formatName': fmt['format_name'],
                    'fixedColor': fmt['fixed_color'],
                    'removedSamples': len(samples),
                    'papers': [s['paper_color'] for s in samples],
                })
                continue
            # 正規でも白専用でもない → 曖昧。消さない
            ambiguous.append({
                'formatId': fmt['id'],
                'identity': fmt['identity'],
                'formatName': fmt['format_name'],
                'fixedColor': fmt['fixed_color'],
                'papers': [s['paper_color'] for s in samples],
                'reason': 'not-solo-white-and-not-keep',
            })
            items.append({
                'action': 'ambiguous-skip',
                'formatId': fmt['id'],
                'identity': fmt['identity'],
                'formatName': fmt['format_name'],
                'fixedColor': fmt['fixed_color'],
                'papers': [s['paper_color'] for s in samples],
            })

    # catalog からも白専用残骸を落とす（正規 identity 以外・固定白）
    catalog_changed = False
    catalog_path = root / 'name-import' / 'catalog.json'
    if catalog_path.is_file() and removed_formats:
        try:
            data = json.loads(catalog_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            data = None
        if data:
            backup_dir.mkdir(parents=True, exist_ok=True)
            cat_backup = backup_dir / 'catalog.json'
            if not cat_backup.exists():
                shutil.copy2(catalog_path, cat_backup)
            cat_customers = {
                item.get('sourceKey')
                for item in (data.get('customers') or [])
                if str(item.get('name') or '').strip() == MORITAN_CUSTOMER_NAME
                or item.get('sourceKey') == MORITAN_CUSTOMER_KEY
            }
            kept_formats = []
            for fmt in data.get('formats') or []:
                if fmt.get('customerKey') not in cat_customers:
                    kept_formats.append(fmt)
                    continue
                identity = str(fmt.get('identity') or '')
                if identity in MORITAN_KEEP_IDENTITIES:
                    kept_formats.append(fmt)
                    continue
                fixed = str(fmt.get('fixedColor') or '').strip()
                samples = list(fmt.get('samples') or [])
                papers = [str(s.get('paperColor') or '').strip() for s in samples]
                groups = list(fmt.get('groups') or [])
                colors = list(fmt.get('colors') or [])
                if groups == MORITAN_ROLE_GROUPS and colors == MORITAN_ROLE_COLORS:
                    kept_formats.append(fmt)
                    continue
                if fixed == '白' and (not papers or all(p in ('', '白') for p in papers)):
                    catalog_changed = True
                    continue
                kept_formats.append(fmt)
            if catalog_changed:
                data['formats'] = kept_formats
                catalog_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8',
                )

    report = {
        'at': stamp,
        'changed': removed_formats,
        'removedFormats': removed_formats,
        'removedSamples': removed_samples,
        'customer': MORITAN_CUSTOMER_NAME,
        'policy': MORITAN_SOLO_WHITE_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'catalogChanged': catalog_changed,
        'ambiguous': ambiguous,
        'rules': {
            'onlyCustomer': MORITAN_CUSTOMER_NAME,
            'keepRoleColorFormats': True,
            'keepIdentities': list(MORITAN_KEEP_IDENTITIES),
            'retireSoloWhiteOnly': True,
            'noGuessDelete': True,
            'noTouchOtherCustomers': True,
        },
        'items': items,
    }
    report_path = root / MORITAN_SOLO_WHITE_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if backup_dir.exists():
        (backup_dir / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            MORITAN_SOLO_WHITE_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': removed_formats,
                'removedFormats': removed_formats,
                'removedSamples': removed_samples,
                'report': MORITAN_SOLO_WHITE_REPORT,
                'policy': MORITAN_SOLO_WHITE_POLICY,
                'backupDir': report['backupDir'],
            }, ensure_ascii=False),
        ),
    )
    return report


def _fresh_role_colors_match(fmt):
    try:
        groups = json.loads(fmt['groups_json'] or '[]')
        colors = json.loads(fmt['colors_json'] or '[]')
    except (TypeError, json.JSONDecodeError, KeyError):
        return False
    return groups == FRESH_ROLE_GROUPS and colors == FRESH_ROLE_COLORS


def _fresh_is_keep_format(fmt):
    if str(fmt['identity'] or '') in FRESH_KEEP_IDENTITIES:
        return True
    if str(fmt['reading'] or '') != '苗字のみ':
        return False
    return _fresh_role_colors_match(fmt)


def _fresh_is_legacy_format(fmt, samples):
    """更新前の先方指定／フルネーム／小汎用などの旧形式か。曖昧なら False。"""
    if _fresh_is_keep_format(fmt):
        return False
    fixed = str(fmt['fixed_color'] or '').strip()
    caption = str(fmt['color_caption'] or '')
    note = str(fmt['note'] or '')
    heading = str(fmt['heading'] or '')
    name = str(fmt['format_name'] or '')
    reading = str(fmt['reading'] or '')
    papers = [str(s['paper_color'] or '').strip() for s in samples]
    non_empty = [p for p in papers if p]
    try:
        groups = json.loads(fmt['groups_json'] or '[]')
        colors = json.loads(fmt['colors_json'] or '[]')
    except (TypeError, json.JSONDecodeError, KeyError):
        groups, colors = [], []

    # 既に社員緑／パート青の役割色なら旧ではない
    if groups == FRESH_ROLE_GROUPS and colors == FRESH_ROLE_COLORS:
        return False
    if any(p in ('緑', '青') for p in non_empty) and reading == '苗字のみ':
        # 役割色見本がある苗字形式は keep 判定漏れの可能性 → 消さない
        return False

    markers = 0
    if _is_mix_color(fixed) or '先方から指定' in fixed or '先方から指定' in caption:
        markers += 1
    if reading == 'フルネーム' or 'フルネーム' in name:
        markers += 1
    if '小汎用' in heading or '小汎用' in note or '小汎用' in caption:
        markers += 1
    if fixed == '白' and (not non_empty or all(p == '白' for p in non_empty)) and reading != '苗字のみ':
        markers += 1
    # 旧形式の典型はマーカー2つ以上
    return markers >= 2


def correct_fresh_legacy_formats(c, data_dir=None):
    """フレッシュフーズ：旧形式をバックアップ後に退職し、新形式（緑／青・苗字・36pt）を残す。"""
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    customers = c.execute(
        'SELECT id, source_name, source_key FROM name_customers WHERE retired=0'
        ' AND (source_name=? OR source_key=?)',
        (FRESH_CUSTOMER_NAME, FRESH_CUSTOMER_KEY),
    ).fetchall()
    if not customers:
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': 'フレッシュフーズなし',
            'customer': FRESH_CUSTOMER_NAME,
            'policy': FRESH_LEGACY_POLICY,
        }

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'fresh-legacy-{stamp}'
    items = []
    ambiguous = []
    removed_formats = 0
    removed_samples = 0

    for customer in customers:
        if _six_is_excluded_name(customer['source_name']):
            continue
        formats = c.execute(
            'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
            (customer['id'],),
        ).fetchall()
        for fmt in formats:
            samples = list(c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (fmt['id'],),
            ))
            if _fresh_is_keep_format(fmt):
                items.append({
                    'action': 'keep',
                    'formatId': fmt['id'],
                    'identity': fmt['identity'],
                    'formatName': fmt['format_name'],
                    'reading': fmt['reading'],
                    'maximumFont': fmt['maximum_font'],
                    'papers': [s['paper_color'] for s in samples],
                })
                continue
            if _fresh_is_legacy_format(fmt, samples):
                _backup_six_format(c, root, backup_dir, fmt, samples)
                for sample in samples:
                    rel = str(sample['relative_path'] or '')
                    if rel:
                        path = root / rel
                        if path.is_file():
                            path.unlink()
                    c.execute('DELETE FROM name_samples WHERE id=?', (sample['id'],))
                    removed_samples += 1
                c.execute('UPDATE name_formats SET retired=1 WHERE id=?', (fmt['id'],))
                removed_formats += 1
                items.append({
                    'action': 'retire-legacy',
                    'formatId': fmt['id'],
                    'identity': fmt['identity'],
                    'formatName': fmt['format_name'],
                    'fixedColor': fmt['fixed_color'],
                    'reading': fmt['reading'],
                    'removedSamples': len(samples),
                    'papers': [s['paper_color'] for s in samples],
                })
                continue
            ambiguous.append({
                'formatId': fmt['id'],
                'identity': fmt['identity'],
                'formatName': fmt['format_name'],
                'fixedColor': fmt['fixed_color'],
                'reading': fmt['reading'],
                'papers': [s['paper_color'] for s in samples],
            })
            items.append({
                'action': 'ambiguous-skip',
                'formatId': fmt['id'],
                'identity': fmt['identity'],
                'formatName': fmt['format_name'],
            })

    catalog_changed = False
    catalog_path = root / 'name-import' / 'catalog.json'
    if catalog_path.is_file() and removed_formats:
        try:
            data = json.loads(catalog_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            data = None
        if data:
            backup_dir.mkdir(parents=True, exist_ok=True)
            cat_backup = backup_dir / 'catalog.json'
            if not cat_backup.exists():
                shutil.copy2(catalog_path, cat_backup)
            cat_customers = {
                item.get('sourceKey')
                for item in (data.get('customers') or [])
                if str(item.get('name') or '').strip() == FRESH_CUSTOMER_NAME
                or item.get('sourceKey') == FRESH_CUSTOMER_KEY
            }
            kept = []
            for fmt in data.get('formats') or []:
                if fmt.get('customerKey') not in cat_customers:
                    kept.append(fmt)
                    continue
                identity = str(fmt.get('identity') or '')
                if identity in FRESH_KEEP_IDENTITIES:
                    kept.append(fmt)
                    continue
                groups = list(fmt.get('groups') or [])
                colors = list(fmt.get('colors') or [])
                if groups == FRESH_ROLE_GROUPS and colors == FRESH_ROLE_COLORS:
                    kept.append(fmt)
                    continue
                fixed = str(fmt.get('fixedColor') or '')
                reading = str(fmt.get('reading') or '')
                caption = str(fmt.get('colorCaption') or '')
                if (
                    _is_mix_color(fixed) or '先方から指定' in fixed or '先方から指定' in caption
                    or reading == 'フルネーム'
                ):
                    catalog_changed = True
                    continue
                kept.append(fmt)
            if catalog_changed:
                data['formats'] = kept
                catalog_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8',
                )

    report = {
        'at': stamp,
        'changed': removed_formats,
        'removedFormats': removed_formats,
        'removedSamples': removed_samples,
        'customer': FRESH_CUSTOMER_NAME,
        'policy': FRESH_LEGACY_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'catalogChanged': catalog_changed,
        'ambiguous': ambiguous,
        'rules': {
            'onlyCustomer': FRESH_CUSTOMER_NAME,
            'keepIdentities': list(FRESH_KEEP_IDENTITIES),
            'keepRoleColors': FRESH_ROLE_COLORS,
            'retireLegacyMixFullname': True,
            'noGuessDelete': True,
        },
        'items': items,
    }
    report_path = root / FRESH_LEGACY_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if backup_dir.exists():
        (backup_dir / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            FRESH_LEGACY_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': removed_formats,
                'removedFormats': removed_formats,
                'removedSamples': removed_samples,
                'report': FRESH_LEGACY_REPORT,
                'policy': FRESH_LEGACY_POLICY,
                'backupDir': report['backupDir'],
            }, ensure_ascii=False),
        ),
    )
    return report


def _takeda_is_canonical_small(fmt):
    if str(fmt['identity'] or '') == 'takeda:small-fullname':
        return True
    if int(fmt['small'] or 0) != 1:
        return False
    return (
        str(fmt['placement'] or '') == TAKEDA_SMALL_PLACEMENT
        and str(fmt['family'] or '') == SIX_FAMILY_HG
        and str(fmt['reading'] or '') == 'フルネーム'
        and str(fmt['fixed_color'] or '') in ('白', '')
    )


def _takeda_is_canonical_large(fmt):
    if str(fmt['identity'] or '') == 'takeda:large-fullname':
        return True
    if int(fmt['small'] or 0) != 0:
        return False
    heading = str(fmt['heading'] or '')
    name = str(fmt['format_name'] or '')
    if '役職' in heading or '役職' in name:
        return False
    return (
        str(fmt['placement'] or '') == TAKEDA_LARGE_PLACEMENT
        and str(fmt['family'] or '') == SIX_FAMILY_HG
        and str(fmt['layout'] or '') == 'Name'
        and str(fmt['reading'] or '') == 'フルネーム'
    )


def _takeda_is_legacy_large_title(fmt):
    """旧役職2段の大ネームか。"""
    if int(fmt['small'] or 0) != 0:
        return False
    if _takeda_is_canonical_large(fmt):
        return False
    heading = str(fmt['heading'] or '')
    name = str(fmt['format_name'] or '')
    layout = str(fmt['layout'] or '')
    return '役職' in heading or '役職' in name or layout == 'HeadingName'


def _takeda_retire_format(c, root, backup_dir, fmt, samples, action, items, counters):
    _backup_six_format(c, root, backup_dir, fmt, samples)
    for sample in samples:
        rel = str(sample['relative_path'] or '')
        if rel:
            path = root / rel
            if path.is_file():
                path.unlink()
        c.execute('DELETE FROM name_samples WHERE id=?', (sample['id'],))
        counters['samples'] += 1
    c.execute('UPDATE name_formats SET retired=1 WHERE id=?', (fmt['id'],))
    counters['formats'] += 1
    items.append({
        'action': action,
        'formatId': fmt['id'],
        'identity': fmt['identity'],
        'formatName': fmt['format_name'],
        'placement': fmt['placement'],
        'small': fmt['small'],
        'removedSamples': len(samples),
    })


def correct_takeda_duplicate_formats(c, data_dir=None):
    """タケダ海産：小ネーム重複を1件残して退職。旧役職大が残ればそれも整理。"""
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    customers = c.execute(
        'SELECT id, source_name, source_key FROM name_customers WHERE retired=0'
        ' AND (source_name=? OR source_key=?)',
        (TAKEDA_CUSTOMER_NAME, TAKEDA_CUSTOMER_KEY),
    ).fetchall()
    if not customers:
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': 'タケダ海産なし',
            'customer': TAKEDA_CUSTOMER_NAME,
            'policy': TAKEDA_DUP_POLICY,
        }

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'takeda-dup-{stamp}'
    items = []
    ambiguous = []
    counters = {'formats': 0, 'samples': 0, 'smallRetired': 0, 'largeRetired': 0}

    for customer in customers:
        if _six_is_excluded_name(customer['source_name']):
            continue
        formats = list(c.execute(
            'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
            (customer['id'],),
        ))
        smalls = [f for f in formats if int(f['small'] or 0) == 1]
        larges = [f for f in formats if int(f['small'] or 0) == 0]

        # --- 小ネーム：正規を1件残し、他の小は重複として退職 ---
        keep_small = None
        for fmt in smalls:
            if str(fmt['identity'] or '') == 'takeda:small-fullname':
                keep_small = fmt
                break
        if keep_small is None:
            canonical = [f for f in smalls if _takeda_is_canonical_small(f)]
            if canonical:
                keep_small = canonical[0]
        if keep_small is None and len(smalls) == 1 and _takeda_is_canonical_small(smalls[0]):
            keep_small = smalls[0]

        if keep_small is not None:
            samples = list(c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (keep_small['id'],),
            ))
            items.append({
                'action': 'keep-small',
                'formatId': keep_small['id'],
                'identity': keep_small['identity'],
                'formatName': keep_small['format_name'],
                'placement': keep_small['placement'],
                'sampleCount': len(samples),
            })
            for fmt in smalls:
                if fmt['id'] == keep_small['id']:
                    continue
                samples = list(c.execute(
                    'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                    (fmt['id'],),
                ))
                # 小ネーム同士の重複は削除。配置が違っても同顧客の余分な小は重複扱い
                _takeda_retire_format(
                    c, root, backup_dir, fmt, samples,
                    'retire-duplicate-small', items, counters,
                )
                counters['smallRetired'] += 1
        else:
            for fmt in smalls:
                ambiguous.append({
                    'formatId': fmt['id'],
                    'identity': fmt['identity'],
                    'formatName': fmt['format_name'],
                    'placement': fmt['placement'],
                    'reason': 'no-canonical-small-to-keep',
                })
                items.append({
                    'action': 'ambiguous-skip-small',
                    'formatId': fmt['id'],
                    'identity': fmt['identity'],
                })

        # --- 大ネーム：正規1件を残し、旧役職大を退職 ---
        keep_large = None
        for fmt in larges:
            if str(fmt['identity'] or '') == 'takeda:large-fullname':
                keep_large = fmt
                break
        if keep_large is None:
            canonical = [f for f in larges if _takeda_is_canonical_large(f)]
            if canonical:
                keep_large = canonical[0]

        if keep_large is not None:
            samples = list(c.execute(
                'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                (keep_large['id'],),
            ))
            items.append({
                'action': 'keep-large',
                'formatId': keep_large['id'],
                'identity': keep_large['identity'],
                'formatName': keep_large['format_name'],
                'sampleCount': len(samples),
            })
            for fmt in larges:
                if fmt['id'] == keep_large['id']:
                    continue
                if not _takeda_is_legacy_large_title(fmt):
                    # 役職でも正規でもない大は曖昧
                    ambiguous.append({
                        'formatId': fmt['id'],
                        'identity': fmt['identity'],
                        'formatName': fmt['format_name'],
                        'reason': 'extra-large-not-clearly-legacy',
                    })
                    items.append({
                        'action': 'ambiguous-skip-large',
                        'formatId': fmt['id'],
                        'identity': fmt['identity'],
                    })
                    continue
                samples = list(c.execute(
                    'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
                    (fmt['id'],),
                ))
                _takeda_retire_format(
                    c, root, backup_dir, fmt, samples,
                    'retire-legacy-large-title', items, counters,
                )
                counters['largeRetired'] += 1
        else:
            for fmt in larges:
                if _takeda_is_legacy_large_title(fmt) and len(larges) > 1:
                    # 正規が無く旧役職だけ複数 → 曖昧（全部消さない）
                    ambiguous.append({
                        'formatId': fmt['id'],
                        'identity': fmt['identity'],
                        'formatName': fmt['format_name'],
                        'reason': 'legacy-large-without-canonical',
                    })
                    items.append({
                        'action': 'ambiguous-skip-large',
                        'formatId': fmt['id'],
                    })

    report = {
        'at': stamp,
        'changed': counters['formats'],
        'removedFormats': counters['formats'],
        'removedSamples': counters['samples'],
        'smallRetired': counters['smallRetired'],
        'largeRetired': counters['largeRetired'],
        'customer': TAKEDA_CUSTOMER_NAME,
        'policy': TAKEDA_DUP_POLICY,
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'ambiguous': ambiguous,
        'rules': {
            'onlyCustomer': TAKEDA_CUSTOMER_NAME,
            'keepIdentities': list(TAKEDA_KEEP_IDENTITIES),
            'keepSmallPlacement': TAKEDA_SMALL_PLACEMENT,
            'retireDuplicateSmall': True,
            'retireLegacyLargeTitle': True,
            'noGuessDelete': True,
        },
        'items': items,
    }
    report_path = root / TAKEDA_DUP_REPORT
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if backup_dir.exists():
        (backup_dir / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            TAKEDA_DUP_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': counters['formats'],
                'removedFormats': counters['formats'],
                'removedSamples': counters['samples'],
                'smallRetired': counters['smallRetired'],
                'largeRetired': counters['largeRetired'],
                'report': TAKEDA_DUP_REPORT,
                'policy': TAKEDA_DUP_POLICY,
                'backupDir': report['backupDir'],
            }, ensure_ascii=False),
        ),
    )
    return report


# --- あすかベーカリー（Excel 行14）。あすかHOUSE / 東苗穂 / 明日佳は絶対に触らない。 ---
ASUKA_BAKERY_CUSTOMER_NAME = 'あすかベーカリー'
ASUKA_BAKERY_CUSTOMER_KEY = 'excel:asuka-bakery'
ASUKA_BAKERY_CUSTOMER_READING = 'あすかべーかりー'
ASUKA_BAKERY_IDENTITY = 'asuka-bakery:small-surname'
ASUKA_BAKERY_KIND = 'asuka-bakery-small-surname'
ASUKA_BAKERY_SOURCE_ROW = 14
ASUKA_BAKERY_FAMILY = SIX_FAMILY_MSP
ASUKA_BAKERY_PLACEMENT = (
    'シャツ：メーカータグ下　パンツ：ベルト後左裏　キャップ：挟み貼り　胸当て：左表上'
)
ASUKA_BAKERY_FORMAT_NAME = '小ネーム（苗字のみ）'
ASUKA_BAKERY_HEADING = '苗字のみ（36pt）'
ASUKA_BAKERY_COLOR_CAPTION = '赤 or 青'
ASUKA_BAKERY_COLORS = ['赤', '青']
ASUKA_BAKERY_SWATCHES = ['#F08080', '#87CEFA']
ASUKA_BAKERY_PAPERS = ('赤', '青')
ASUKA_BAKERY_MAXIMUM_FONT = 36
ASUKA_BAKERY_WIDTH_MM, ASUKA_BAKERY_HEIGHT_MM = THREE_SMALL_W, THREE_SMALL_H
ASUKA_BAKERY_NOTE = (
    f'書体は{ASUKA_BAKERY_FAMILY}。色は赤 or 青。'
    '小ネーム・1段・苗字のみ／36pt（exact）。'
    f'Excel 行{ASUKA_BAKERY_SOURCE_ROW}（ネーム仕分け_五十音順2）。'
)
ASUKA_BAKERY_CAPTION = (
    f'見本：苗字「{SIX_SAMPLE_SURNAME}」（36pt・exact・1段）。'
    f'書体は{ASUKA_BAKERY_FAMILY}。色は赤 or 青。'
)
ASUKA_BAKERY_REPORT = 'asuka-bakery-name-formats-report.json'
ASUKA_BAKERY_SETTINGS_KEY = 'asuka_bakery_name_formats'
# v1: 赤・青見本を ensure。v2: 子形式を必ず1件に統合（重複赤青も退職・画像は残す）
ASUKA_BAKERY_POLICY = 'excel-gojuon2-r14-one-format-red-blue-v2'
ASUKA_BAKERY_FORBIDDEN_SUBSTRINGS = (
    'あすかHOUSE', '明日佳', '明日香',
)


def _asuka_bakery_forbidden(name):
    text = str(name or '')
    if text.strip() == ASUKA_BAKERY_CUSTOMER_NAME:
        return False
    return any(token in text for token in ASUKA_BAKERY_FORBIDDEN_SUBSTRINGS)


def _asuka_bakery_format_def():
    return {
        'identity': ASUKA_BAKERY_IDENTITY,
        'kind': ASUKA_BAKERY_KIND,
        'sourceRow': ASUKA_BAKERY_SOURCE_ROW,
        'formatName': ASUKA_BAKERY_FORMAT_NAME,
        'placement': ASUKA_BAKERY_PLACEMENT,
        'family': ASUKA_BAKERY_FAMILY,
        'heading': ASUKA_BAKERY_HEADING,
        'note': ASUKA_BAKERY_NOTE,
        'layout': 'Surname',
        'reading': '苗字のみ',
        'small': 1,
        'widthMm': ASUKA_BAKERY_WIDTH_MM,
        'heightMm': ASUKA_BAKERY_HEIGHT_MM,
        'fixedColor': '',
        'colorCaption': ASUKA_BAKERY_COLOR_CAPTION,
        'groups': [],
        'colors': list(ASUKA_BAKERY_COLORS),
        'swatches': list(ASUKA_BAKERY_SWATCHES),
        'maximumFont': ASUKA_BAKERY_MAXIMUM_FONT,
        'sampleText': SIX_SAMPLE_SURNAME,
        'fontPt': ASUKA_BAKERY_MAXIMUM_FONT,
        'sampleFit': 'exact',
        'papers': ASUKA_BAKERY_PAPERS,
        'caption': ASUKA_BAKERY_CAPTION,
        'surnameOnly': True,
    }


def _asuka_bakery_spec(fmt_def):
    return {
        'asukaBakeryKind': fmt_def['kind'],
        'asukaBakeryPolicy': ASUKA_BAKERY_POLICY,
        'surnameOnly': True,
        'sampleText': fmt_def['sampleText'],
        'fontPt': fmt_def['fontPt'],
        'sampleFit': 'exact',
        'exactPtPolicy': SMALL_SURNAME_36_POLICY,
        'papers': list(fmt_def['papers']),
    }


def _asuka_bakery_find_customer(c):
    rows = c.execute(
        'SELECT * FROM name_customers WHERE retired=0 ORDER BY id'
    ).fetchall()
    for row in rows:
        if str(row['source_key'] or '') == ASUKA_BAKERY_CUSTOMER_KEY:
            if _asuka_bakery_forbidden(row['source_name']):
                return None
            return row
    for row in rows:
        name = str(row['source_name'] or '').strip()
        if name == ASUKA_BAKERY_CUSTOMER_NAME:
            return row
    return None


def _asuka_bakery_is_current(fmt, samples, fmt_def):
    if str(fmt['identity'] or '') != fmt_def['identity']:
        return False
    if str(fmt['format_name'] or '') != fmt_def['formatName']:
        return False
    if str(fmt['placement'] or '') != fmt_def['placement']:
        return False
    if str(fmt['family'] or '') != fmt_def['family']:
        return False
    if str(fmt['heading'] or '') != fmt_def['heading']:
        return False
    if str(fmt['note'] or '') != fmt_def['note']:
        return False
    if str(fmt['layout'] or '') != 'Surname':
        return False
    if str(fmt['reading'] or '') != '苗字のみ':
        return False
    if int(fmt['small'] or 0) != 1:
        return False
    if str(fmt['color_caption'] or '') != ASUKA_BAKERY_COLOR_CAPTION:
        return False
    if float(fmt['maximum_font'] or 0) != float(ASUKA_BAKERY_MAXIMUM_FONT):
        return False
    if int(fmt['source_row'] or 0) != ASUKA_BAKERY_SOURCE_ROW:
        return False
    try:
        colors = json.loads(fmt['colors_json'] or '[]')
        swatches = json.loads(fmt['swatches_json'] or '[]')
    except (TypeError, json.JSONDecodeError):
        return False
    if colors != ASUKA_BAKERY_COLORS or swatches != ASUKA_BAKERY_SWATCHES:
        return False
    spec = _load_spec(fmt)
    if spec.get('asukaBakeryPolicy') != ASUKA_BAKERY_POLICY:
        return False
    if spec.get('sampleFit') != 'exact':
        return False
    if len(samples) != len(ASUKA_BAKERY_PAPERS):
        return False
    by_paper = {str(s['paper_color'] or ''): s for s in samples}
    for paper in ASUKA_BAKERY_PAPERS:
        sample = by_paper.get(paper)
        if sample is None:
            return False
        if str(sample['caption'] or '') != ASUKA_BAKERY_CAPTION:
            return False
        rel = str(sample['relative_path'] or '')
        if not rel.startswith('images/names/') or ASUKA_BAKERY_KIND not in Path(rel).name:
            return False
    return True


def _asuka_bakery_retire_extra(c, root, backup_dir, fmt, samples, keep_id, items):
    """正規1件以外の子形式をバックアップ後に retired=1。見本画像ファイルは消さない。"""
    _backup_six_format(c, root, backup_dir, fmt, samples)
    # サンプル行は残す（復元用）。画像パスも unlink しない。
    c.execute('UPDATE name_formats SET retired=1 WHERE id=?', (fmt['id'],))
    sample_paths = [
        str(s['relative_path'] or '') for s in samples if str(s['relative_path'] or '')
    ]
    items.append({
        'action': 'retire-duplicate-format',
        'formatId': fmt['id'],
        'keepFormatId': keep_id,
        'identity': fmt['identity'],
        'formatName': fmt['format_name'],
        'fixedColor': fmt['fixed_color'],
        'colorCaption': fmt['color_caption'],
        'sampleCount': len(samples),
        'samplePathsKept': sample_paths,
        'imagesUnlinked': False,
    })
    return 1


def ensure_asuka_bakery_name_formats(c, data_dir=None):
    """あすかベーカリーのみ：子形式1件（赤・青見本）に統合。HOUSE/明日佳は触らない。"""
    root = Path(data_dir) if data_dir is not None else DATA_DIR
    if root is None:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'data_dir未設定'}

    have = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='name_formats'"
    ).fetchone()
    if not have:
        return {'changed': 0, 'items': [], 'skipped': True, 'reason': 'name_formats未作成'}

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup_dir = root / 'backups' / f'asuka-bakery-{stamp}'
    fmt_def = _asuka_bakery_format_def()
    items = []
    retired = 0

    # 除外顧客を誤って拾っていないか先に確認
    forbidden_hits = c.execute(
        "SELECT id, source_name FROM name_customers WHERE retired=0 AND ("
        "source_name LIKE '%あすかHOUSE%' OR source_name LIKE '%明日佳%' OR source_name LIKE '%明日香%')"
    ).fetchall()
    forbidden_snapshot = {
        row['id']: str(row['source_name'] or '') for row in forbidden_hits
    }

    customer = _asuka_bakery_find_customer(c)
    if customer is None:
        known = _app_customers(c)
        linked, status = _link(ASUKA_BAKERY_CUSTOMER_NAME, known)
        c.execute(
            'INSERT INTO name_customers('
            'source_key, source_index, source_name, reading, linked_name, link_status, manual, retired'
            ') VALUES(?,?,?,?,?,?,1,0)',
            (
                ASUKA_BAKERY_CUSTOMER_KEY, -1, ASUKA_BAKERY_CUSTOMER_NAME,
                ASUKA_BAKERY_CUSTOMER_READING, linked, status,
            ),
        )
        cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        customer = c.execute('SELECT * FROM name_customers WHERE id=?', (cid,)).fetchone()
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / f'customer-created-{cid}.json').write_text(
            json.dumps(_row_as_dict(customer), ensure_ascii=False, indent=2), encoding='utf-8',
        )
        items.append({
            'action': 'create-customer',
            'customerId': cid,
            'customerName': ASUKA_BAKERY_CUSTOMER_NAME,
        })
    elif _asuka_bakery_forbidden(customer['source_name']):
        return {
            'changed': 0,
            'items': [],
            'skipped': True,
            'reason': 'forbidden-customer-name',
            'customer': customer['source_name'],
            'policy': ASUKA_BAKERY_POLICY,
        }

    # この顧客配下の正規 identity のみを残す候補にする（他社の同 identity は触らない）
    by_identity = c.execute(
        'SELECT * FROM name_formats WHERE identity=? AND customer_id=? AND retired=0 ORDER BY id',
        (ASUKA_BAKERY_IDENTITY, customer['id']),
    ).fetchone()
    existing = by_identity
    if existing is None:
        candidates = c.execute(
            'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 AND small=1 ORDER BY id',
            (customer['id'],),
        ).fetchall()
        for cand in candidates:
            if str(cand['reading'] or '') == '苗字のみ' or str(cand['layout'] or '') == 'Surname':
                existing = cand
                break
        if existing is None and candidates:
            # Excel 行14は小ネーム1件のみ → 先頭の小を統合先にする
            existing = candidates[0]

    samples = []
    if existing:
        samples = list(c.execute(
            'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
            (existing['id'],),
        ))

    needs = existing is None or not _asuka_bakery_is_current(existing, samples, fmt_def)
    if needs:
        backup_dir.mkdir(parents=True, exist_ok=True)
        if existing:
            _backup_six_format(c, root, backup_dir, existing, samples)
        # upsert
        groups = json.dumps([], ensure_ascii=False)
        colors = json.dumps(ASUKA_BAKERY_COLORS, ensure_ascii=False)
        swatches = json.dumps(ASUKA_BAKERY_SWATCHES, ensure_ascii=False)
        spec = json.dumps(_asuka_bakery_spec(fmt_def), ensure_ascii=False)
        values = (
            ASUKA_BAKERY_FORMAT_NAME, ASUKA_BAKERY_PLACEMENT, ASUKA_BAKERY_FAMILY, '',
            ASUKA_BAKERY_HEADING, ASUKA_BAKERY_NOTE, 'Surname', '苗字のみ',
            1, ASUKA_BAKERY_WIDTH_MM, ASUKA_BAKERY_HEIGHT_MM, 0,
            '', ASUKA_BAKERY_COLOR_CAPTION,
            groups, colors, swatches, 'null',
            '', float(ASUKA_BAKERY_MAXIMUM_FONT), 0, 0, ASUKA_BAKERY_SOURCE_ROW,
            spec, 0, '[]', 0,
        )
        if existing:
            c.execute(
                'UPDATE name_formats SET format_name=?, placement=?, family=?, header_family=?, heading=?, note=?,'
                ' layout=?, reading=?, small=?, width_mm=?, height_mm=?, unknown_size=?, fixed_color=?, color_caption=?,'
                ' groups_json=?, colors_json=?, swatches_json=?, departments_json=?, pending=?, maximum_font=?,'
                ' heading_font=?, number_length=?, source_row=?, spec_json=?, needs_review=?, review_json=?, blank=?,'
                ' retired=0, identity=? WHERE id=?',
                values + (ASUKA_BAKERY_IDENTITY, existing['id']),
            )
            fid = existing['id']
            action = 'update'
        else:
            c.execute(
                'INSERT INTO name_formats('
                'identity, customer_id, source_kind, format_name, placement, family, header_family, heading, note,'
                'layout, reading, small, width_mm, height_mm, unknown_size, fixed_color, color_caption,'
                'groups_json, colors_json, swatches_json, departments_json, pending, maximum_font, heading_font,'
                'number_length, source_row, previous_source_row, spec_json, needs_review, review_json, blank, manual, retired'
                ') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)',
                (
                    ASUKA_BAKERY_IDENTITY, customer['id'], -1,
                    values[0], values[1], values[2], values[3], values[4], values[5],
                    values[6], values[7], values[8], values[9], values[10], values[11],
                    values[12], values[13], values[14], values[15], values[16], values[17],
                    values[18], values[19], values[20], values[21], values[22], None,
                    values[23], values[24], values[25], values[26],
                ),
            )
            fid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            action = 'create'
        ok, note, paths = _write_six_samples(c, root, fid, fmt_def)
        items.append({
            'action': action,
            'formatId': fid,
            'identity': ASUKA_BAKERY_IDENTITY,
            'samplePaths': paths,
            'sampleOk': ok,
            'sampleNote': note,
            'papers': list(ASUKA_BAKERY_PAPERS),
        })
    else:
        items.append({
            'action': 'skip',
            'formatId': existing['id'],
            'identity': ASUKA_BAKERY_IDENTITY,
            'reason': 'already-current',
        })
        fid = existing['id']

    # Excel 行14は1形式のみ → 同顧客の他子形式はすべて退職（単色残骸も赤青重複も）
    for fmt in c.execute(
        'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 ORDER BY id',
        (customer['id'],),
    ):
        if fmt['id'] == fid:
            continue
        samples = list(c.execute(
            'SELECT * FROM name_samples WHERE format_id=? ORDER BY id',
            (fmt['id'],),
        ))
        retired += _asuka_bakery_retire_extra(
            c, root, backup_dir, fmt, samples, fid, items,
        )

    active_count = c.execute(
        'SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND retired=0',
        (customer['id'],),
    ).fetchone()['n']

    # 除外顧客が変わっていないこと
    after_forbidden = c.execute(
        "SELECT id, source_name FROM name_customers WHERE retired=0 AND ("
        "source_name LIKE '%あすかHOUSE%' OR source_name LIKE '%明日佳%' OR source_name LIKE '%明日香%')"
    ).fetchall()
    after_map = {row['id']: str(row['source_name'] or '') for row in after_forbidden}
    exclusions_untouched = after_map == forbidden_snapshot

    changed = sum(
        1 for i in items
        if i.get('action') in (
            'create', 'update', 'create-customer',
            'retire-duplicate-format', 'retire-solo-color',
        )
    )
    report = {
        'at': stamp,
        'changed': changed,
        'retiredSoloFormats': retired,  # 互換キー（単色・重複を含む退職件数）
        'retiredExtraFormats': retired,
        'activeFormatCount': active_count,
        'keptFormatId': fid,
        'removedSamples': 0,
        'imagesUnlinked': False,
        'customer': ASUKA_BAKERY_CUSTOMER_NAME,
        'policy': ASUKA_BAKERY_POLICY,
        'colors': list(ASUKA_BAKERY_COLORS),
        'backupDir': str(backup_dir.relative_to(root)).replace('\\', '/') if backup_dir.exists() else '',
        'exclusionsUntouched': exclusions_untouched,
        'hardExclusions': list(ASUKA_BAKERY_FORBIDDEN_SUBSTRINGS),
        'rules': {
            'onlyCustomer': ASUKA_BAKERY_CUSTOMER_NAME,
            'neverTouchHouseOrAshuka': True,
            'oneChildFormat': True,
            'redBluePapers': True,
            'exact36Surname': True,
            'keepSampleImagesOnRetire': True,
        },
        'items': items,
    }
    (root / ASUKA_BAKERY_REPORT).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    if backup_dir.exists():
        (backup_dir / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
    c.execute(
        'INSERT OR REPLACE INTO settings VALUES(?,?)',
        (
            ASUKA_BAKERY_SETTINGS_KEY,
            json.dumps({
                'at': stamp,
                'changed': changed,
                'retiredSoloFormats': retired,
                'retiredExtraFormats': retired,
                'activeFormatCount': active_count,
                'report': ASUKA_BAKERY_REPORT,
                'policy': ASUKA_BAKERY_POLICY,
                'backupDir': report['backupDir'],
            }, ensure_ascii=False),
        ),
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
            if item.get('mixColors') is not None:
                spec['mixColors'] = item.get('mixColors')
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
        # 女性見本の画像変換は import_catalog では行わない（起動・取込を軽く保つ）。
        # 必要なときは maintain_female_sample_hanako.py を明示実行する。
        correct_rinko_yellow(c, data_dir)
        correct_rinko_name_formats(c, data_dir)
        correct_ashuka_name_formats(c, data_dir)
        ensure_three_customer_name_formats(c, data_dir)
        ensure_fk_name_formats(c, data_dir)
        ensure_benkama_name_formats(c, data_dir)
        ensure_six_customers_name_formats(c, data_dir)
        correct_moritan_solo_white(c, data_dir)
        correct_fresh_legacy_formats(c, data_dir)
        correct_takeda_duplicate_formats(c, data_dir)
        ensure_asuka_bakery_name_formats(c, data_dir)
        ensure_small_surname_36_exact_samples(c, data_dir)
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
    where = f'WHERE retired=0 AND {NOT_SOFT_DELETED}'
    args = []
    if q:
        where += ' AND source_name LIKE ?'
        args.append(f'%{q}%')
    rows = c.execute(f'SELECT * FROM name_customers {where} ORDER BY source_name, id', args).fetchall()
    items = []
    for row in rows:
        counts = c.execute(
            f'SELECT COUNT(*) n, SUM(needs_review) review FROM name_formats WHERE customer_id=? AND retired=0 AND {NOT_SOFT_DELETED}',
            (row['id'],),
        ).fetchone()
        assignees, approved, unapproved = [], 0, 0
        for form in c.execute(
            f'SELECT id FROM name_formats WHERE customer_id=? AND retired=0 AND {NOT_SOFT_DELETED}',
            (row['id'],),
        ):
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
    row = c.execute(
        f'SELECT * FROM name_customers WHERE id=? AND retired=0 AND {NOT_SOFT_DELETED}',
        (customer_id,),
    ).fetchone()
    if not row: return 404, {'error': '顧客が見つかりません。'}
    formats = []
    for item in c.execute(
        f'SELECT * FROM name_formats WHERE customer_id=? AND retired=0 AND {NOT_SOFT_DELETED} ORDER BY id',
        (row['id'],),
    ):
        sample = c.execute('SELECT id, relative_path FROM name_samples WHERE format_id=? AND relative_path<>"" ORDER BY id LIMIT 1', (item['id'],)).fetchone()
        formats.append({
            'id': item['id'], 'number': _number(item['id']), 'format': item['format_name'],
            'placement': item['placement'], 'size': _size_text(item), 'needsReview': bool(item['needs_review']),
            'blank': bool(item['blank']), 'hasImage': bool(sample), 'sampleId': sample['id'] if sample else None,
            'colors': _colors(item), 'fixedColor': item['fixed_color'],
            'mixColors': mix_colors_for(item),
            'review': name_review.state(c,item['id'],handler.user),
        })
    return 200, {
        'id': row['id'], 'sourceName': row['source_name'], 'reading': row['reading'],
        'linkedName': row['linked_name'], 'linkStatus': row['link_status'], 'formats': formats,
    }

def detail(handler, c, format_id):
    blocked = _require(handler.user)
    if blocked: return blocked
    row = c.execute(
        f'SELECT * FROM name_formats WHERE id=? AND retired=0 AND {NOT_SOFT_DELETED}',
        (format_id,),
    ).fetchone()
    if not row: return 404, {'error': '形式が見つかりません。'}
    customer_row = c.execute('SELECT * FROM name_customers WHERE id=?', (row['customer_id'],)).fetchone()
    if not customer_row or customer_row['retired'] or _row_deleted(customer_row):
        return 404, {'error': '形式が見つかりません。'}
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
        'mixColors': mix_colors_for(row),
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
    row = c.execute(
        f'''SELECT s.relative_path FROM name_samples s
            JOIN name_formats f ON f.id=s.format_id
            JOIN name_customers cu ON cu.id=f.customer_id
            WHERE s.id=? AND f.retired=0 AND cu.retired=0
              AND (f.deleted_at IS NULL OR f.deleted_at='')
              AND (cu.deleted_at IS NULL OR cu.deleted_at='')''',
        (sample_id,),
    ).fetchone()
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

def _owner_delete(user):
    blocked = _require(user)
    if blocked: return blocked
    if user['role'] != 'owner': return 403, {'error': 'ネーム情報の削除と復元は主管理者だけができます。'}
    return None

def _actor_fields(user):
    return _now(), user['id'], user['name'] or ''

def soft_delete_customer(handler, c, d):
    """大元（顧客）を論理削除。未削除の子形式もまとめて非表示（deleted_with_parent=1）。画像ファイルは残す。"""
    blocked = _owner_delete(handler.user)
    if blocked: return blocked
    try: customer_id = int(d.get('id'))
    except (TypeError, ValueError): return 400, {'error': '顧客が見つかりません。'}
    row = c.execute(
        f'SELECT * FROM name_customers WHERE id=? AND retired=0 AND {NOT_SOFT_DELETED}',
        (customer_id,),
    ).fetchone()
    if not row: return 404, {'error': '顧客が見つかりません。'}
    child_count = c.execute(
        f'SELECT COUNT(*) n FROM name_formats WHERE customer_id=? AND retired=0 AND {NOT_SOFT_DELETED}',
        (customer_id,),
    ).fetchone()['n']
    at, by_id, by_name = _actor_fields(handler.user)
    c.execute(
        'UPDATE name_customers SET deleted_at=?, deleted_by=?, deleted_by_name=? WHERE id=?',
        (at, by_id, by_name, customer_id),
    )
    c.execute(
        f'''UPDATE name_formats SET deleted_at=?, deleted_by=?, deleted_by_name=?, deleted_with_parent=1
            WHERE customer_id=? AND retired=0 AND {NOT_SOFT_DELETED}''',
        (at, by_id, by_name, customer_id),
    )
    return 200, {
        'ok': True, 'kind': 'customer', 'id': customer_id,
        'sourceName': row['source_name'], 'hiddenFormats': child_count,
        'deletedAt': at, 'deletedByName': by_name,
    }

def soft_delete_format(handler, c, d):
    """子情報（形式）だけを論理削除。大元と他の子は残す。画像ファイルは残す。"""
    blocked = _owner_delete(handler.user)
    if blocked: return blocked
    try: format_id = int(d.get('id'))
    except (TypeError, ValueError): return 400, {'error': '形式が見つかりません。'}
    row = c.execute(
        f'SELECT * FROM name_formats WHERE id=? AND retired=0 AND {NOT_SOFT_DELETED}',
        (format_id,),
    ).fetchone()
    if not row: return 404, {'error': '形式が見つかりません。'}
    customer_row = c.execute(
        f'SELECT * FROM name_customers WHERE id=? AND retired=0 AND {NOT_SOFT_DELETED}',
        (row['customer_id'],),
    ).fetchone()
    if not customer_row: return 404, {'error': '形式が見つかりません。'}
    at, by_id, by_name = _actor_fields(handler.user)
    c.execute(
        '''UPDATE name_formats SET deleted_at=?, deleted_by=?, deleted_by_name=?, deleted_with_parent=0
           WHERE id=?''',
        (at, by_id, by_name, format_id),
    )
    return 200, {
        'ok': True, 'kind': 'format', 'id': format_id,
        'format': row['format_name'], 'sourceName': customer_row['source_name'],
        'customerId': customer_row['id'], 'deletedAt': at, 'deletedByName': by_name,
    }

def restore_customer(handler, c, d):
    """大元を復元。親削除時に一緒に隠した子（deleted_with_parent=1）だけ戻す。個別削除の子は戻さない。"""
    blocked = _owner_delete(handler.user)
    if blocked: return blocked
    try: customer_id = int(d.get('id'))
    except (TypeError, ValueError): return 400, {'error': '顧客が見つかりません。'}
    row = c.execute(
        f'SELECT * FROM name_customers WHERE id=? AND retired=0 AND {IS_SOFT_DELETED}',
        (customer_id,),
    ).fetchone()
    if not row: return 404, {'error': '削除済みの顧客が見つかりません。'}
    restored_formats = c.execute(
        f'''SELECT COUNT(*) n FROM name_formats
            WHERE customer_id=? AND retired=0 AND {IS_SOFT_DELETED} AND deleted_with_parent=1''',
        (customer_id,),
    ).fetchone()['n']
    c.execute(
        'UPDATE name_customers SET deleted_at=NULL, deleted_by=NULL, deleted_by_name=? WHERE id=?',
        ('', customer_id),
    )
    c.execute(
        f'''UPDATE name_formats SET deleted_at=NULL, deleted_by=NULL, deleted_by_name=?, deleted_with_parent=0
            WHERE customer_id=? AND retired=0 AND {IS_SOFT_DELETED} AND deleted_with_parent=1''',
        ('', customer_id),
    )
    return 200, {
        'ok': True, 'kind': 'customer', 'id': customer_id,
        'sourceName': row['source_name'], 'restoredFormats': restored_formats,
    }

def restore_format(handler, c, d):
    """個別に削除した子情報を復元。親が削除中なら親復元後に見える。"""
    blocked = _owner_delete(handler.user)
    if blocked: return blocked
    try: format_id = int(d.get('id'))
    except (TypeError, ValueError): return 400, {'error': '形式が見つかりません。'}
    row = c.execute(
        f'SELECT * FROM name_formats WHERE id=? AND retired=0 AND {IS_SOFT_DELETED}',
        (format_id,),
    ).fetchone()
    if not row: return 404, {'error': '削除済みの形式が見つかりません。'}
    customer_row = c.execute('SELECT * FROM name_customers WHERE id=?', (row['customer_id'],)).fetchone()
    c.execute(
        'UPDATE name_formats SET deleted_at=NULL, deleted_by=NULL, deleted_by_name=?, deleted_with_parent=0 WHERE id=?',
        ('', format_id),
    )
    return 200, {
        'ok': True, 'kind': 'format', 'id': format_id,
        'format': row['format_name'],
        'sourceName': customer_row['source_name'] if customer_row else '',
        'customerId': row['customer_id'],
        'parentDeleted': bool(customer_row and _row_deleted(customer_row)),
    }

def list_deleted(handler, c):
    """主管理者向け削除済み一覧。親削除に巻き込まれた子は親行に含め、個別削除の子だけ別表示。"""
    blocked = _owner_delete(handler.user)
    if blocked: return blocked
    customers = []
    for row in c.execute(
        f'SELECT * FROM name_customers WHERE retired=0 AND {IS_SOFT_DELETED} ORDER BY deleted_at DESC, id DESC'
    ):
        with_parent = c.execute(
            f'''SELECT COUNT(*) n FROM name_formats
                WHERE customer_id=? AND retired=0 AND {IS_SOFT_DELETED} AND deleted_with_parent=1''',
            (row['id'],),
        ).fetchone()['n']
        individual = c.execute(
            f'''SELECT COUNT(*) n FROM name_formats
                WHERE customer_id=? AND retired=0 AND {IS_SOFT_DELETED} AND deleted_with_parent=0''',
            (row['id'],),
        ).fetchone()['n']
        customers.append({
            'kind': 'customer', 'id': row['id'], 'sourceName': row['source_name'],
            'reading': row['reading'], 'deletedAt': row['deleted_at'],
            'deletedByName': row['deleted_by_name'] or '',
            'hiddenWithParent': with_parent, 'individuallyDeletedFormats': individual,
        })
    formats = []
    for row in c.execute(
        f'''SELECT f.*, cu.source_name, cu.deleted_at AS customer_deleted_at
            FROM name_formats f
            JOIN name_customers cu ON cu.id=f.customer_id
            WHERE f.retired=0 AND cu.retired=0
              AND (f.deleted_at IS NOT NULL AND f.deleted_at<>'')
              AND f.deleted_with_parent=0
            ORDER BY f.deleted_at DESC, f.id DESC'''
    ):
        formats.append({
            'kind': 'format', 'id': row['id'], 'number': _number(row['id']),
            'format': row['format_name'], 'sourceName': row['source_name'],
            'customerId': row['customer_id'], 'deletedAt': row['deleted_at'],
            'deletedByName': row['deleted_by_name'] or '',
            'parentDeleted': bool(row['customer_deleted_at']),
        })
    return 200, {'customers': customers, 'formats': formats}

def soft_delete(handler, c, d):
    kind = str(d.get('kind') or '').strip()
    if kind == 'customer': return soft_delete_customer(handler, c, d)
    if kind == 'format': return soft_delete_format(handler, c, d)
    return 400, {'error': '削除対象を指定してください。'}

def restore(handler, c, d):
    kind = str(d.get('kind') or '').strip()
    if kind == 'customer': return restore_customer(handler, c, d)
    if kind == 'format': return restore_format(handler, c, d)
    return 400, {'error': '復元対象を指定してください。'}

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
    return {
        '青': '#87CEFA', '緑': '#90EE90', '赤': '#F08080',
        '黄': '#FFFFE0', '黄色': '#FFFFE0', 'オレンジ': '#FFDAB9', '白': '#FFFFFF',
    }.get(key, '#FFFFFF')

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
        row = c.execute(
            f'SELECT * FROM name_customers WHERE id=? AND retired=0 AND {NOT_SOFT_DELETED}',
            (customer_id,),
        ).fetchone()
        if not row: raise ValueError('顧客が見つかりません。')
        other = c.execute(
            f'SELECT id FROM name_customers WHERE retired=0 AND {NOT_SOFT_DELETED} AND source_name=? AND id<>?',
            (fields['customer'], customer_id),
        ).fetchone()
        if other: raise ValueError('同じ顧客名がすでにあります。その顧客に形式を追加してください。')
        changed = row['source_name'] != fields['customer'] or (row['reading'] or '') != fields['reading']
        c.execute('UPDATE name_customers SET source_name=?, reading=?, linked_name=?, link_status=?, manual=? WHERE id=?',
            (fields['customer'], fields['reading'], linked, status, 1 if changed or row['manual'] else 0, customer_id))
        return customer_id
    found = c.execute(
        f'SELECT id FROM name_customers WHERE retired=0 AND {NOT_SOFT_DELETED} AND source_name=?',
        (fields['customer'],),
    ).fetchone()
    if found:
        if fields['reading']:
            c.execute('UPDATE name_customers SET reading=? WHERE id=? AND reading=?', (fields['reading'], found['id'], ''))
        c.execute('UPDATE name_customers SET linked_name=?, link_status=? WHERE id=?', (linked, status, found['id']))
        return found['id']
    c.execute('''INSERT INTO name_customers(source_key, source_index, source_name, reading, linked_name, link_status, manual, retired)
        VALUES(?,?,?,?,?,?,1,0)''', ('manual:' + uuid.uuid4().hex, -1, fields['customer'], fields['reading'], linked, status))
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]

def _write_format(c, fields, customer_id, current=None):
    other = c.execute(
        f'SELECT id FROM name_formats WHERE retired=0 AND {NOT_SOFT_DELETED} AND customer_id=? AND format_name=? AND id<>?',
        (customer_id, fields['format'], current['id'] if current else 0),
    ).fetchone()
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
    current = c.execute(
        f'SELECT * FROM name_formats WHERE id=? AND retired=0 AND {NOT_SOFT_DELETED}',
        (format_id,),
    ).fetchone()
    if not current: return 404, {'error': '形式が見つかりません。'}
    fields = _fields(d)
    customer_id = _apply_customer(c, fields, current['customer_id'])
    _write_format(c, fields, customer_id, current)
    _replace_image(c, format_id, d.get('data'), d.get('filename'))
    return detail(handler, c, format_id)
