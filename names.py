import name_review
"""ネーム情報。

統合ネームラベルの登録を、形式ごとの見本つきで参照する。
元ソフトと元資料は変更しない。Excelの行番号は登録番号にしない。
"""
import hashlib, json, os, re, uuid, base64, shutil
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
    # 女性見本の画像変換は起動・schema では行わない（DBロック／起動失敗の再発防止）。
    # 明示の保守 CLI（DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1）だけが実行する。
    if DATA_DIR is not None:
        correct_rinko_yellow(c, DATA_DIR)

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

def _fit_font_to_box(text, family_hint, target_w, target_h, prefer_size):
    """指定矩形に収まる最大フォントを探す。"""
    if target_w < 4 or target_h < 4:
        return None, None
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None, None
    lo, hi = 8, max(8, int(max(prefer_size * 1.35, target_h)))
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
    return str(name or '').strip() == MIX_FIXED_COLOR

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
