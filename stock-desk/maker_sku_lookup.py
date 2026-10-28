"""旧CSV一括取り込み: メーカー品番からの無料補正候補取得。

- 有料AI／検索APIは使わない（urllib による無料の公開ページ取得のみ）
- 仕入先 ≠ メーカー（無条件同一視しない）。明示メーカー or 仕入先名に含まれる既知ブランドのみヒント
- メーカー品番に加え、旧商品名から「上／下」や独立した色語を検索補助に使う
- 上下区分は補足ではなく登録予定品番末尾（例: VEH30B下）へ連結する
- 採用内容は SQLite に永続化し、次回取込で再利用（再検索で手動確定を上書きしない）
- 類似度だけでは候補にしない。メーカー・品番（全半角吸収・型番一部一致は同一視しない）・上下の区別が必要
"""
from __future__ import annotations

import html as html_lib
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import legacy_csv_import as legacy
import product_ledger_paren as paren_classify
import product_ledger_position as position_sku
import stock
import stock_product_merge

# 管理用・非メーカー品番（ボタン非表示）
_MANAGEMENT_SKUS = frozenset({
    '',
    legacy.CUSTOM_ORDER_CODE,
    stock_product_merge.UNKNOWN_CODE,
    stock.BLANK_SKU_DISPLAY,
    stock.BLANK_SKU_UNIFORM_DISPLAY,
    '品番なし',
})

# 公式・販社っぽいホスト（優先）
_PREFERRED_HOST_HINTS = (
    'midori-anzen', 'midori', 'servo', 'montblanc', 'chitose', 'aitoz', 'aito-s',
    'bonmax', 'karsee', 'selery', 'faceblend', 'toms', 'kurodaruma',
    'kazen-ec', 'kazen', 'unifice',
)

# 仕入先名にブランドが含まれるときだけメーカーヒントにする（無条件同一視しない）
_SUPPLIER_BRAND_MARKERS = (
    ('ミドリ安全', 'ミドリ安全'),
    ('サーヴォ', 'サーヴォ'),
    ('住商モンブラン', '住商モンブラン'),
    ('モンブラン', 'モンブラン'),
    ('チトセ', 'チトセ'),
    ('アイトス', 'アイトス'),
    ('カゼン', 'KAZEN'),
    ('KAZEN', 'KAZEN'),
)

_POSITION_TOKENS = position_sku.POSITION_TOKENS

# 掲載名の「色／サイズ」判定用（配色 黒／ワイン と区別）。CSVサイズは上書きしない。
_SIZE_TERMS = (
    'フリー', 'SS', 'XS', 'XL', 'XXL', '3L', '4L', '5L', '6L', '2L', 'LL',
    'EL', 'S', 'M', 'L', 'F',
)

# DDG HTML は bot 風 UA を弾くため、通常ブラウザ相当で無料の公開HTMLだけ取得する
_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)
_FETCH_TIMEOUT_SEC = 10

_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_OG_TITLE_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
    re.I,
)
_H1_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.I | re.S)
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_TAG_RE = re.compile(r'<[^>]+>')
_MIDORI_RESULT_RE = re.compile(
    r'href="(/shop/g/g[^"]+)"[^>]*title="([^"]+)"',
    re.I,
)
_KAZEN_ITEM_RE = re.compile(
    r'href="(item_detail\.php\?@uid=[^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_LABELED_SKU_RE = re.compile(
    r'(?:商品コード|メーカー品番|品番コード|型番|品番|SKU|MPN)'
    r'[\s\u3000]*[:：]?[\s\u3000]*'
    r'([A-Za-z0-9Ａ-Ｚａ-ｚ０-９][A-Za-z0-9Ａ-Ｚａ-ｚ０-９\-_＿－]*)',
    re.I,
)
_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_PAREN_CHUNK_RE = re.compile(r'[（(]([^（）()]*)[）)]')
_SKU_VALUE_RE = re.compile(
    r'^[A-Za-z0-9Ａ-Ｚａ-ｚ０-９][A-Za-z0-9Ａ-Ｚａ-ｚ０-９\-_＿－]*$'
)
# 商品情報表のラベル文言（sku_match_key 適用前の原文。値として誤採用しない）
_FIELD_LABEL_RAW = (
    '品番', '品名', 'ブランド', 'ブランド名', '商品コード', 'メーカー品番',
    '品番コード', '型番', '商品番号', 'SKU', 'MPN', 'カラー', '色', 'サイズ', '商品名',
)
_NAME_NOISE_TOKENS = frozenset({
    'KAZEN', 'カゼン', 'WLD', 'ミドリ安全', 'サーヴォ', 'モンブラン',
    '住商モンブラン', 'MONTBLANC', 'チトセ', 'アイトス', '公式',
    'オンラインストア', 'COOKING&SERVICE', 'COOKING＆SERVICE',
    'ユニフォームのユニフィス', 'ユニフィス', '通販', '激安',
})
_PRIVATE_HOST_RE = re.compile(
    r'^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.|'
    r'0\.0\.0\.0|::1|\[::1\])',
    re.I,
)
_COLOR_TERMS_SORTED = sorted(
    {paren_classify.unify_import_notation(t) for t in paren_classify.COLOR_TERMS},
    key=len,
    reverse=True,
)


def maker_sku_key(value: str) -> str:
    """永続キー用。空白除去。先頭ゼロ維持のため全体 NFKC はしない。"""
    return re.sub(r'[\s\u3000]+', '', stock.text(value) or '')


def sku_match_key(value: str) -> str:
    """照合用。全角英数→半角（NFKC）＋空白除去＋大文字。型番一部一致には使わない。"""
    t = unicodedata.normalize('NFKC', stock.text(value) or '')
    t = re.sub(r'[\s\u3000]+', '', t)
    return t.upper()


_FIELD_LABEL_KEYS = frozenset(sku_match_key(x) for x in _FIELD_LABEL_RAW)
# 商品情報表の色ラベル（ページ横断の色一覧・関連商品は対象外）
_COLOR_FIELD_LABEL_KEYS = frozenset(
    sku_match_key(x) for x in ('色', 'カラー', 'カラー名', '色名', '配色')
)
_SKU_FIELD_LABEL_KEYS = frozenset(
    sku_match_key(x) for x in (
        '品番', '商品コード', '型番', 'メーカー品番', '品番コード', '商品番号', 'SKU', 'MPN',
    )
)
_NAME_FIELD_LABEL_KEYS = frozenset(
    sku_match_key(x) for x in ('品名', '商品名')
)
_BRAND_FIELD_LABEL_KEYS = frozenset(
    sku_match_key(x) for x in ('ブランド', 'ブランド名', 'メーカー')
)


def skus_equal(a: str, b: str) -> bool:
    ka, kb = sku_match_key(a), sku_match_key(b)
    return bool(ka) and ka == kb



# メーカー別: 基礎品番＋色番（末尾数字の無条件分離はしない）
_AITOZ_SKU_RE = re.compile(
    r'^(AZ)[-－]?(\d+)(?:[-－](\d{2,4}))?$',
    re.I,
)
_AITOZ_GOODSNUMBER_RE = re.compile(
    r'id=["\']goodsnumber["\'][^>]*>\s*([^<]+)',
    re.I,
)
_AITOZ_GOODSNAME_RE = re.compile(
    r'id=["\']goodsname["\'][^>]*>\s*([^<]+)',
    re.I,
)
_AITOZ_MENU_CODE_RE = re.compile(
    r'id=["\']lblMenuItemCode["\'][^>]*>\s*([^<]+)',
    re.I,
)
_AITOZ_COLOR_BLOCK_RE = re.compile(
    r'id=["\']itemDetailColor["\'][\s\S]*?</table>',
    re.I,
)
_AITOZ_COLOR_ROW_RE = re.compile(
    r'<th[^>]*>\s*(\d{2,4})\s*([^<]*?)\s*</th>',
    re.I,
)


def maker_sku_rule_id(*, manufacturer: str = '', page_url: str = '') -> str:
    """拡張可能なメーカー別品番ルール ID。未対応は空文字。"""
    mfr = paren_classify.unify_import_notation(stock.text(manufacturer) or '')
    mfr_u = mfr.upper()
    if 'アイトス' in mfr or 'AITOZ' in mfr_u:
        return 'aitoz'
    url = (page_url or '').lower()
    if 'aitoz' in url or 'aito-s' in url or 'azweb.aitoz' in url:
        return 'aitoz'
    return ''


def aitoz_base_match_key(value: str) -> str:
    """アイトス基礎品番の照合キー。AZ8960 ↔ AZ-8960 を同一視。色番の先頭ゼロは触らない。"""
    t = sku_match_key(value)
    if not t:
        return ''
    m = re.fullmatch(r'(AZ)[-－]?(\d+)', t, re.I)
    if m:
        return f'AZ{m.group(2)}'
    return t.replace('-', '').replace('－', '')


def aitoz_bases_equal(a: str, b: str) -> bool:
    ka, kb = aitoz_base_match_key(a), aitoz_base_match_key(b)
    return bool(ka) and ka == kb


def split_maker_sku_parts(
    maker_sku: str,
    *,
    manufacturer: str = '',
    page_url: str = '',
) -> dict:
    """メーカー規則で完全品番を基礎＋色番へ分解する。

    全メーカーで末尾数字を無条件分離しない。規則が無い／非対象は base=原文・colorCode=''。
    """
    raw = stock.text(maker_sku) or ''
    rule = maker_sku_rule_id(manufacturer=manufacturer, page_url=page_url)
    empty = {
        'rule': rule or '',
        'full': raw,
        'base': raw,
        'colorCode': '',
        'hinban': '',
    }
    if not raw:
        return empty
    if rule == 'aitoz':
        m = _AITOZ_SKU_RE.fullmatch(raw)
        if not m:
            base_only, _pos = position_sku.strip_sku_position_suffix(raw)
            m = _AITOZ_SKU_RE.fullmatch(base_only or raw)
        if not m:
            return empty
        prefix, digits, color = m.group(1), m.group(2), m.group(3) or ''
        base = f'{prefix.upper()}-{digits}'
        return {
            'rule': 'aitoz',
            'full': raw,
            'base': base,
            'colorCode': color,
            'hinban': digits,
        }
    return empty


def color_codes_equal(a: str, b: str) -> bool:
    """色番比較。先頭ゼロを維持した文字列一致（018 ≠ 18）。"""
    ca = unicodedata.normalize('NFKC', stock.text(a) or '')
    cb = unicodedata.normalize('NFKC', stock.text(b) or '')
    ca = re.sub(r'[\s\u3000]+', '', ca)
    cb = re.sub(r'[\s\u3000]+', '', cb)
    return bool(ca) and ca == cb


def extract_aitoz_page_product(html: str) -> dict:
    """アイトス商品詳細から基礎品番・商品名・カラーバリエーションだけを取る。

    関連商品・ナビは itemDetailColor / goodsnumber の外なので拾わない。
    """
    raw = html or ''
    base = ''
    for cre in (_AITOZ_GOODSNUMBER_RE, _AITOZ_MENU_CODE_RE):
        m = cre.search(raw)
        if m:
            base = stock.text(m.group(1)) or ''
            if base:
                break
    name = ''
    nm = _AITOZ_GOODSNAME_RE.search(raw)
    if nm:
        name = paren_classify.unify_import_notation(stock.text(nm.group(1)) or '') or ''
    colors = []
    seen = set()
    block = _AITOZ_COLOR_BLOCK_RE.search(raw)
    block_html = block.group(0) if block else ''
    for m in _AITOZ_COLOR_ROW_RE.finditer(block_html):
        code = unicodedata.normalize('NFKC', m.group(1) or '')
        label = paren_classify.unify_import_notation(stock.text(m.group(2) or '') or '') or ''
        if label and any(ch in label for ch in ('/', '／', '×', '✕')):
            label = paren_classify.normalize_colorway_display(label) or label
        if not code or code in seen:
            continue
        seen.add(code)
        colors.append({'code': code, 'name': label})
    return {
        'baseSku': base,
        'name': name,
        'colors': colors,
    }


def _looks_like_sku_value(value: str) -> bool:
    """品番らしい英数トークンか。表ヘッダの「品名」などを誤採用しない。"""
    v = stock.text(value) or ''
    if not v or not _SKU_VALUE_RE.fullmatch(v):
        return False
    if sku_match_key(v) in _FIELD_LABEL_KEYS:
        return False
    if not re.search(r'[A-Za-z0-9Ａ-Ｚａ-ｚ０-９]', v):
        return False
    return True


def _sku_boundary_char(ch: str) -> bool:
    """品番の前後が英数字のときだけ密着とみなす（日本語は境界扱い）。"""
    if not ch:
        return False
    # NFKC 後の ASCII 英数字のみ。ひらがな・漢字・カナは境界。
    return bool(re.fullmatch(r'[A-Za-z0-9]', ch))


def _text_contains_full_sku(text: str, maker_sku: str) -> bool:
    """見出し等に完全品番が含まれるか。

    空白は境界（WLD APK499-10 を WLDAPK499-10 と誤結合しない）。
    日本語との密着（APK499胸当て）は一致とみなす。
    """
    sk = sku_match_key(maker_sku)
    if not sk:
        return False
    unified = unicodedata.normalize('NFKC', stock.text(text) or '')
    if not unified:
        return False
    # トークン分割（空白・括弧等を境界に。ハイフンは品番の一部として残す）
    token_blob = re.sub(r'[^\w\-－]+', ' ', unified, flags=re.UNICODE)
    # 日本語との境界も空白化（APK499胸当て → APK499 胸当て）
    token_blob = re.sub(
        r'([A-Za-z0-9Ａ-Ｚａ-ｚ０-９\-－])([\u3040-\u30ff\u3400-\u9fff])',
        r'\1 \2',
        token_blob,
    )
    token_blob = re.sub(
        r'([\u3040-\u30ff\u3400-\u9fff])([A-Za-z0-9Ａ-Ｚａ-ｚ０-９\-－])',
        r'\1 \2',
        token_blob,
    )
    for tok in token_blob.split():
        if sku_match_key(tok) == sk:
            return True
    # 空白除去後でも、前後が ASCII 英数字でなければ密着を許可
    compact = sku_match_key(unified)
    start = 0
    while True:
        idx = compact.find(sk, start)
        if idx < 0:
            return False
        before = compact[idx - 1] if idx > 0 else ''
        after = compact[idx + len(sk)] if idx + len(sk) < len(compact) else ''
        if not _sku_boundary_char(before) and not _sku_boundary_char(after):
            return True
        start = idx + 1


def _compact_contains_full_sku(compact: str, sk: str) -> bool:
    """後方互換: 空白除去済み文字列向け。新規は _text_contains_full_sku を使う。"""
    if not compact or not sk:
        return False
    start = 0
    while True:
        idx = compact.find(sk, start)
        if idx < 0:
            return False
        before = compact[idx - 1] if idx > 0 else ''
        after = compact[idx + len(sk)] if idx + len(sk) < len(compact) else ''
        if not _sku_boundary_char(before) and not _sku_boundary_char(after):
            return True
        start = idx + 1


def manufacturer_key(value: str) -> str:
    t = paren_classify.unify_import_notation(stock.text(value) or '')
    return re.sub(r'[\s\u3000]+', '', t)


def is_management_sku(sku: str) -> bool:
    s = stock.text(sku) or ''
    if s in _MANAGEMENT_SKUS:
        return True
    if s.startswith('品番なし'):
        return True
    if legacy.is_custom_order_code(s):
        return True
    if s == stock_product_merge.UNKNOWN_CODE:
        return True
    return False


def is_valid_maker_sku(sku: str) -> bool:
    s = stock.text(sku) or ''
    if not s or is_management_sku(s):
        return False
    if len(s) < 2:
        return False
    if re.fullmatch(r'[-_/／・.]+', s):
        return False
    return True


# 用途・顧客・現場などの注記っぽい語（括弧内の判定補助。これだけで機械削除しない）
_USAGE_NOTE_MARKERS = (
    '警備', '用途', '向け', '専用', '顧客', '作業', '業務', '駐車場',
    '食堂', '厨房', '受付', '清掃', '配達', '配送', '工場', '店舗',
    '病棟', '厨房', 'フロント', 'バックヤード',
)


def _paren_content_is_sku_part(content: str) -> bool:
    """括弧内が色・サイズ・上下・型番枝など品番の一部とみなせるか。"""
    raw = paren_classify.unify_import_notation(stock.text(content) or '')
    if not raw:
        return True
    if raw in _POSITION_TOKENS:
        return True
    # 英数・記号のみ（色番・型番枝・識別仕様）
    if re.fullmatch(r'[A-Za-z0-9Ａ-Ｚａ-ｚ０-９\-－_/／.]+', raw):
        return True
    hit = paren_classify.classify_paren_segment(raw)
    if hit and hit.get('kind') in ('color', 'material', 'processing', 'hem_ignore'):
        return True
    if _token_is_size(raw):
        return True
    slash = _classify_slash_pair(raw)
    if slash.get('kind') in ('color', 'colorway', 'color_size', 'size'):
        return True
    return False


def _paren_content_is_usage_note(content: str) -> bool:
    """用途・顧客・現場などの注記か。不明なら False（元情報を保持）。"""
    raw = paren_classify.unify_import_notation(stock.text(content) or '')
    if not raw:
        return False
    if _paren_content_is_sku_part(raw):
        return False
    # 日本語を含まない注記は想定外 → 保持
    if not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', raw):
        return False
    if any(m in raw for m in _USAGE_NOTE_MARKERS):
        return True
    # 漢字・かな・中点のみの記述語（例: 駐車場警備）。数字・ラテン混在は不明扱い
    if re.fullmatch(r'[\u3040-\u30ff\u3400-\u9fff・･\s\u3000]+', raw) and len(raw) >= 2:
        return True
    return False


def split_maker_sku_and_note(raw: str) -> dict:
    """検索用品番と用途注記を分離する。

    - 例: AZ8960-019（駐車場警備）→ searchSku=AZ8960-019 / usageNote=駐車場警備
    - 登録品番はこの結果だけでは書き換えない（sourceSku を原文として保持）
    - 括弧をすべて削除しない。色番・型番枝・上下・不明は残す
    """
    source = stock.text(raw) or ''
    working = source
    notes: list[str] = []
    uncertain_kept = False
    while working:
        m = re.search(r'[（(]([^（）()]*)[）)]\s*$', working)
        if not m:
            break
        content = m.group(1) or ''
        if _paren_content_is_usage_note(content):
            note = paren_classify.unify_import_notation(content) or content
            if note:
                notes.insert(0, note)
            working = working[: m.start()].rstrip(' \u3000')
            continue
        if _paren_content_is_sku_part(content):
            break
        uncertain_kept = True
        break
    search = working or source
    return {
        'sourceSku': source,
        'searchSku': search,
        'usageNote': '／'.join(notes) if notes else '',
        'noteStripped': bool(notes),
        'uncertainKept': uncertain_kept,
    }


def normalize_search_maker_sku(raw: str) -> dict:
    """用途注記を除き、上下を分離した検索用メーカー品番。"""
    split = split_maker_sku_and_note(raw)
    search = split['searchSku']
    base, pos = position_sku.strip_sku_position_suffix(search)
    base = base or search
    return {
        **split,
        'baseSku': base,
        'position': pos if pos in _POSITION_TOKENS else '',
    }


_ORDER_UNIT_RES = (
    re.compile(r'[（(]\s*ご注文単位[^）)]*[）)]'),
    re.compile(r'[（(]\s*注文単位[^）)]*[）)]'),
    re.compile(r'[（(]\s*販売単位[^）)]*[）)]'),
    re.compile(r'ご注文単位\s*\d*\s*枚?'),
    re.compile(r'注文単位\s*\d*\s*枚?'),
    re.compile(r'販売単位\s*\d*\s*枚?'),
    re.compile(r'\d+\s*枚\s*[（(]\s*ご?注文単位[^）)]*[）)]'),
)
_PRICE_PROMO_RES = (
    re.compile(r'[¥￥]\s*[\d,，]+'),
    re.compile(r'[\d,，]+\s*円(?:相当)?'),
    re.compile(r'送料(?:無料|込|別)?'),
    re.compile(r'(?:税込|税抜|本体価格|特別価格|セール|SALE)', re.I),
)
# ページ構造上の色番・サイズ一覧と判断できる数字列（2個以上＋省略記号可）
_DIGIT_ENUM_RE = re.compile(
    r'(?<![A-Za-z0-9])(?:\d{1,4}[\s\u3000]+){1,}\d{1,4}(?:\s*[…⋯．.]{1,3})?(?![A-Za-z0-9])'
)
_BARE_ORDER_QTY_RE = re.compile(r'(?<![A-Za-z0-9])\d+\s*枚(?![A-Za-z0-9])')


def clean_maker_product_name(
    name: str,
    *,
    maker_sku: str = '',
    brand: str = '',
) -> dict:
    """取得商品名から注文単位・価格・色番一覧などの販売情報を除く。

    - タイトル直採用前提ではなく、呼び出し側で品名欄を優先したうえで適用する
    - 七分袖・半袖・男女兼用などは保持。数字の一律削除はしない
    - 数字列は「連続する短い数字（＋省略記号）」のときだけ色番一覧とみなして除去
    """
    original = paren_classify.unify_import_notation(stock.text(name) or '') or ''
    if not original:
        return {
            'originalName': '',
            'cleanedName': '',
            'removedOrderUnit': False,
            'removedPrice': False,
            'removedDigitEnum': False,
        }
    working = original
    removed_order = False
    removed_price = False
    removed_enum = False

    # ブランド（読み仮名付き）を先頭から落とす: KAZEN(カゼン)
    brand_candidates = []
    for b in (brand, *(_NAME_NOISE_TOKENS)):
        t = paren_classify.unify_import_notation(stock.text(b) or '')
        if t and t not in brand_candidates:
            brand_candidates.append(t)
    for b in brand_candidates:
        # Brand(読み) / Brand（読み）
        pat = re.compile(
            r'^' + re.escape(b) + r'(?:\s*[（(][^）)]*[）)])?\s*',
            re.I,
        )
        nxt = pat.sub('', working, count=1)
        if nxt != working:
            working = nxt
            continue
        # 読みだけ括弧の Brand なしは _NAME_NOISE で後段処理

    for rx in _ORDER_UNIT_RES:
        nxt = rx.sub(' ', working)
        if nxt != working:
            removed_order = True
            working = nxt
    # 注文単位除去後の裸の「1枚」など
    if removed_order:
        nxt = _BARE_ORDER_QTY_RE.sub(' ', working)
        if nxt != working:
            working = nxt

    for rx in _PRICE_PROMO_RES:
        nxt = rx.sub(' ', working)
        if nxt != working:
            removed_price = True
            working = nxt

    nxt = _DIGIT_ENUM_RE.sub(' ', working)
    if nxt != working:
        removed_enum = True
        working = nxt

    # メーカー品番の残渣
    base = stock.text(maker_sku) or ''
    if base:
        working = _strip_sku_prefixes(working, base)
        stem = _model_stem(base)
        if stem:
            working = _strip_sku_prefixes(working, stem)

    # ノイズ語トークン
    noise_keys = {sku_match_key(n) for n in _NAME_NOISE_TOKENS}
    kept = []
    for tok in re.split(r'[\s\u3000]+', working):
        if not tok:
            continue
        key = sku_match_key(tok)
        if key and key in noise_keys:
            continue
        if paren_classify.unify_import_notation(tok) in _NAME_NOISE_TOKENS:
            continue
        kept.append(tok)
    working = ' '.join(kept)
    working = re.sub(r'【[^】]*】', ' ', working)
    # 半角括弧を全角へ（登録・表示の表記を揃える）
    working = working.replace('(', '（').replace(')', '）')
    working = re.sub(r'[\s\u3000]+', ' ', working).strip()
    working = re.sub(r'\s+（', '（', working)
    working = paren_classify.unify_import_notation(working) or working
    return {
        'originalName': original,
        'cleanedName': working or original,
        'removedOrderUnit': removed_order,
        'removedPrice': removed_price,
        'removedDigitEnum': removed_enum,
    }


def effective_maker_sku_from_row(row: dict) -> str:
    """検索用のメーカー品番（用途注記・上下区分を除いた基礎品番）。"""
    prod = row.get('product') if isinstance(row.get('product'), dict) else {}
    csv = row.get('csv') if isinstance(row.get('csv'), dict) else {}
    explicit = stock.text(
        prod.get('makerCode') or csv.get('makerCode') or csv.get('baseCode') or ''
    )
    if explicit:
        norm = normalize_search_maker_sku(explicit)
        code = norm['baseSku'] or norm['searchSku'] or explicit
        return code if is_valid_maker_sku(code) else ''
    code = stock.text(
        csv.get('baseCode')
        or csv.get('sourceCode')
        or csv.get('code')
        or prod.get('code')
        or ''
    )
    if prod.get('colorSplit') and not explicit:
        return ''
    norm = normalize_search_maker_sku(code)
    code = norm['baseSku'] or norm['searchSku'] or code
    if not stock_product_merge.is_normal_product_code(code):
        return ''
    return code if is_valid_maker_sku(code) else ''


def row_source_sku_and_note(row: dict) -> dict:
    """行の元品番（注記込み）と検索用品番・注記。登録品番は書き換えない。"""
    prod = row.get('product') if isinstance(row.get('product'), dict) else {}
    csv = row.get('csv') if isinstance(row.get('csv'), dict) else {}
    source = stock.text(
        csv.get('sourceCode')
        or prod.get('makerCode')
        or csv.get('makerCode')
        or csv.get('baseCode')
        or csv.get('code')
        or prod.get('code')
        or ''
    )
    return normalize_search_maker_sku(source)


def planned_registered_sku(*, maker_sku: str, position: str = '', source_code: str = '') -> str:
    """登録予定品番（上下を末尾密着）。

    用途注記は検索から外すが、ここでの基礎品番も注記なし側を使う。
    元品番全文（sourceSku）は provenance / sourceCode に残し、注記分離だけで
    原文キーを消さない。
    """
    raw_source = stock.text(source_code) or stock.text(maker_sku)
    norm = normalize_search_maker_sku(raw_source)
    base = norm['baseSku'] or norm['searchSku'] or stock.text(maker_sku)
    pos = stock.text(position) or norm.get('position') or ''
    if not pos:
        _b, pos_from_code = position_sku.strip_sku_position_suffix(
            stock.text(maker_sku) or ''
        )
        pos = pos_from_code
    if pos in _POSITION_TOKENS:
        return position_sku.attach_sku_position_suffix(base, pos)
    return base


def manufacturer_hint_from_supplier(supplier: str) -> str:
    """仕入先名に既知ブランドが含まれるときだけメーカー名ヒントを返す。

    仕入先全体をメーカーと同一視はしない（例: ミドリ安全北海道㈱ → ミドリ安全）。
    """
    unified = paren_classify.unify_import_notation(stock.text(supplier) or '')
    if not unified:
        return ''
    for marker, brand in _SUPPLIER_BRAND_MARKERS:
        if marker in unified:
            return brand
    return ''


def resolve_manufacturer(*, manufacturer: str = '', supplier: str = '') -> str:
    mfr = stock.text(manufacturer) or ''
    if mfr:
        return mfr
    return manufacturer_hint_from_supplier(supplier)


def row_maker_correction_meta(row: dict) -> dict:
    sku = effective_maker_sku_from_row(row)
    sku_meta = row_source_sku_and_note(row)
    csv = row.get('csv') if isinstance(row.get('csv'), dict) else {}
    existing = row.get('makerCorrection') if isinstance(row.get('makerCorrection'), dict) else {}
    mfr = stock.text(existing.get('manufacturer') or '') or resolve_manufacturer(
        manufacturer='',
        supplier=csv.get('supplier') or '',
    )
    return {
        'eligible': bool(sku) and is_valid_maker_sku(sku),
        'makerSku': sku,
        'sourceSku': sku_meta.get('sourceSku') or '',
        'searchMakerSku': sku_meta.get('baseSku') or sku,
        'usageNote': sku_meta.get('usageNote') or '',
        'manufacturer': mfr,
    }


def strip_color_tokens_from_name(name: str) -> str:
    """色語トークンを除いた商品名（色競合グループの検索で一色にロックしない）。"""
    unified = paren_classify.unify_import_notation(stock.text(name) or '')
    if not unified:
        return ''
    kept = []
    for tok in re.split(r'[\s\u3000]+', unified):
        if not tok:
            continue
        if _token_is_color(tok):
            continue
        if any(sep in tok for sep in ('/', '／', '×', '✕')):
            # 配色表記トークンも検索補助名から外す
            continue
        kept.append(tok)
    return ' '.join(kept).strip()


def neutral_search_source_name(names) -> str:
    """複数の旧名から、色を除いた検索補助名を選ぶ。"""
    stripped = []
    for n in names or []:
        s = strip_color_tokens_from_name(n)
        if s and s not in stripped:
            stripped.append(s)
    if not stripped:
        return ''
    # 最短の共通っぽい名を優先（例: スクラブ ブルー／ワイン → スクラブ）
    stripped.sort(key=lambda x: (len(x), x))
    return stripped[0]


def sku_group_maker_correction_meta(group: dict, rows=None) -> dict:
    """品番比較一覧の「メーカー情報で補正」表示用。

    表示条件:
    - 確認待ち（needsDecision）である
    - 有効なメーカー品番がある（別注・品番なし・空は不可）
    - メーカー情報をまだ採用していない
    色・商品名・商品種など確認理由の種類では非表示にしない。
    社内管理品番がある行は元のメーカー品番（makerCode）を使う。
    一括取得（bulkEligible）も同じ「有効メーカー品番」判定。
    """
    color_diff = bool(group.get('colorDiffPossible'))
    needs_decision = bool(group.get('needsDecision'))
    already_adopted = bool(group.get('makerCorrectionAdopted'))
    row_list = [r for r in (rows or []) if isinstance(r, dict)]
    maker_sku = ''
    manufacturer = ''
    supplier = ''
    source_names = []
    for row in row_list:
        meta = row.get('makerCorrection') if isinstance(row.get('makerCorrection'), dict) else {}
        if not meta:
            meta = row_maker_correction_meta(row)
        sku = stock.text(meta.get('makerSku') or '')
        if sku and is_valid_maker_sku(sku) and not maker_sku:
            maker_sku = sku
            manufacturer = stock.text(meta.get('manufacturer') or '')
        csv = row.get('csv') if isinstance(row.get('csv'), dict) else {}
        if not supplier:
            supplier = stock.text(csv.get('supplier') or '')
        n = stock.text(csv.get('productName') or '')
        if n and n not in source_names:
            source_names.append(n)
    if not maker_sku:
        # 行が無い／メタ未付与時はグループ品番を試す
        code = stock.text(group.get('code') or '')
        if is_valid_maker_sku(code):
            maker_sku = code
    if not manufacturer:
        manufacturer = resolve_manufacturer(manufacturer='', supplier=supplier)
    if not source_names:
        source_names = [
            stock.text(n)
            for n in (group.get('nameCandidates') or [])
            if stock.text(n)
        ]
    valid_sku = bool(maker_sku) and is_valid_maker_sku(maker_sku)
    # 確認待ち＋有効品番。色競合の有無では落とさない
    show_button = valid_sku and needs_decision and not already_adopted
    multi_names = len(source_names) > 1
    if color_diff or multi_names:
        # 複数旧名／色競合時は色語を除き、一色・一名ヒントで候補を落とさない
        source_hint = neutral_search_source_name(source_names)
    else:
        source_hint = source_names[0] if source_names else ''
    return {
        'eligible': show_button,
        'showButton': show_button,
        # 一括取得は色競合の有無に関わらず、有効メーカー品番があれば対象
        'bulkEligible': valid_sku,
        'makerSku': maker_sku if valid_sku else '',
        'manufacturer': manufacturer,
        'supplier': supplier,
        'sourceNameHint': source_hint,
        'colorDiffPossible': color_diff,
        'rowNos': list(group.get('rowNos') or []),
    }


def extract_name_search_hints(old_name: str) -> dict:
    """旧商品名から検索補助語を取り出す。

    例: 「白下 ブルー」→ positions=['下'], colors=['ブルー']
    「白下」内の「白」は検索色にしない（必ず色条件にしない）。
    「裾上げ」「上下セット」「上衣」等からは上下を採らない。
    """
    unified = paren_classify.unify_import_notation(stock.text(old_name) or '')
    positions = position_sku.extract_positions_for_search(old_name)
    colors = []
    tokens = [t for t in re.split(r'[\s\u3000]+', unified) if t]
    color_keys = {paren_classify.color_match_key(t): t for t in paren_classify.COLOR_TERMS}

    def _as_color(token: str):
        key = paren_classify.color_match_key(token)
        if key in color_keys:
            return paren_classify.unify_import_notation(color_keys[key]) or token
        return ''

    for tok in tokens:
        # トークン全体が色語 → 検索色
        col = _as_color(tok)
        if col:
            if col not in colors:
                colors.append(col)
            continue
        # 白下 のように色語＋上下が密着 → 上下は既に採取、短い色は検索条件にしない
        for pos in _POSITION_TOKENS:
            if tok.endswith(pos) and len(tok) > len(pos):
                head = tok[: -len(pos)]
                # head が単独色でも検索色にはしない（要件: 白を必ず色検索にしない）
                _ = _as_color(head)
                break

    return {
        'positions': positions,
        'colors': colors,
        'unified': unified,
        'tokens': tokens,
    }


def build_search_query_stages(*, manufacturer: str, maker_sku: str, hints: dict) -> list[str]:
    """段階的な検索語。基礎品番＋上下の表記差（スペース／密着）を試す。

    完全品番で見つからない場合に備え、メーカー規則の基礎品番でも検索する。
    """
    raw_sku = stock.text(maker_sku)
    base, pos_from_sku = position_sku.strip_sku_position_suffix(raw_sku)
    sku = base or raw_sku
    mfr = stock.text(manufacturer)
    positions = list(hints.get('positions') or [])
    if pos_from_sku and pos_from_sku not in positions:
        positions = [pos_from_sku] + positions
    colors = list(hints.get('colors') or [])
    stages = []
    parts = split_maker_sku_parts(sku, manufacturer=mfr)
    base_variants = []
    if parts.get('rule') == 'aitoz' and parts.get('base'):
        b = parts['base']
        compact = aitoz_base_match_key(b)
        for v in (b, compact, parts.get('hinban') or ''):
            if v and v not in base_variants and v != sku:
                base_variants.append(v)

    def add(parts_q):
        q = ' '.join(p for p in parts_q if p)
        if q and q not in stages:
            stages.append(q)

    add([mfr, sku])
    add([sku])
    for bv in base_variants:
        add([mfr, bv])
        add([bv])
    mfr_u = paren_classify.unify_import_notation(mfr).upper()
    if 'KAZEN' in mfr_u or 'カゼン' in (mfr or ''):
        add([sku, 'site:kazen-ec.com'])
        add([mfr, sku, 'site:kazen-ec.com'])
        add(['KAZEN', sku])
    if parts.get('rule') == 'aitoz':
        for qsku in [sku, *base_variants]:
            add([qsku, 'site:azweb.aitoz.co.jp'])
            add([mfr, qsku, 'site:azweb.aitoz.co.jp'])
            add(['アイトス', qsku])
    for pos in positions:
        add([mfr, sku, pos])
        add([sku, pos])
        # 密着表記（登録予定品番と同じ形）も検索
        add([mfr, f'{sku}{pos}'])
        add([f'{sku}{pos}'])
    for pos in positions:
        for col in colors:
            add([mfr, sku, pos, col])
            add([sku, pos, col])
            add([mfr, f'{sku}{pos}', col])
            add([f'{sku}{pos}', col])
    for col in colors:
        add([mfr, sku, col])
        add([sku, col])
        if parts.get('colorCode'):
            add([mfr, sku, parts['colorCode']])
            add([sku, parts['colorCode']])
            for bv in base_variants:
                add([mfr, bv, parts['colorCode']])
                add([bv, parts['colorCode']])
    return stages


def ensure_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS maker_sku_corrections (
      id INTEGER PRIMARY KEY,
      manufacturer TEXT NOT NULL DEFAULT '',
      maker_sku TEXT NOT NULL,
      manufacturer_key TEXT NOT NULL,
      maker_sku_key TEXT NOT NULL,
      adopted_name TEXT NOT NULL DEFAULT '',
      adopted_color TEXT NOT NULL DEFAULT '',
      source_url TEXT NOT NULL DEFAULT '',
      adopted_json TEXT NOT NULL DEFAULT '{}',
      manually_confirmed INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(manufacturer_key, maker_sku_key)
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS maker_sku_corrections_sku '
        'ON maker_sku_corrections(maker_sku_key, id DESC)'
    )


def lookup_saved_correction(c, *, manufacturer: str, maker_sku: str):
    ensure_schema(c)
    mk = manufacturer_key(manufacturer)
    sk = maker_sku_key(maker_sku)
    if not sk:
        return None
    if mk:
        row = c.execute(
            '''SELECT * FROM maker_sku_corrections
               WHERE manufacturer_key=? AND maker_sku_key=?
               ORDER BY id DESC LIMIT 1''',
            (mk, sk),
        ).fetchone()
        if row:
            return dict(row)
    row = c.execute(
        '''SELECT * FROM maker_sku_corrections
           WHERE maker_sku_key=?
           ORDER BY
             CASE WHEN manufacturer_key='' THEN 1 ELSE 0 END,
             id DESC
           LIMIT 1''',
        (sk,),
    ).fetchone()
    return dict(row) if row else None


def save_adopted_correction(
    c, *,
    manufacturer: str,
    maker_sku: str,
    adopted_name: str,
    adopted_color: str = '',
    source_url: str = '',
    adopted_extra: dict | None = None,
    overwrite_manual: bool = False,
):
    ensure_schema(c)
    mk = manufacturer_key(manufacturer)
    sk = maker_sku_key(maker_sku)
    if not sk:
        raise ValueError('メーカー品番が空です。')
    existing = c.execute(
        '''SELECT * FROM maker_sku_corrections
           WHERE manufacturer_key=? AND maker_sku_key=?''',
        (mk, sk),
    ).fetchone()
    t = stock.stamp()
    payload = {
        'name': stock.text(adopted_name),
        'color': stock.text(adopted_color),
        'sourceUrl': stock.text(source_url),
        **(adopted_extra or {}),
    }
    if existing:
        if int(existing['manually_confirmed'] or 0) and not overwrite_manual:
            return dict(existing), False
        c.execute(
            '''UPDATE maker_sku_corrections SET
                 manufacturer=?, maker_sku=?,
                 adopted_name=?, adopted_color=?, source_url=?,
                 adopted_json=?, manually_confirmed=1, updated_at=?
               WHERE id=?''',
            (
                stock.text(manufacturer), stock.text(maker_sku),
                stock.text(adopted_name), stock.text(adopted_color),
                stock.text(source_url),
                json.dumps(payload, ensure_ascii=False),
                t, existing['id'],
            ),
        )
        row = c.execute(
            'SELECT * FROM maker_sku_corrections WHERE id=?', (existing['id'],)
        ).fetchone()
        return dict(row), True
    c.execute(
        '''INSERT INTO maker_sku_corrections(
             manufacturer, maker_sku, manufacturer_key, maker_sku_key,
             adopted_name, adopted_color, source_url, adopted_json,
             manually_confirmed, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,1,?,?)''',
        (
            stock.text(manufacturer), stock.text(maker_sku), mk, sk,
            stock.text(adopted_name), stock.text(adopted_color),
            stock.text(source_url),
            json.dumps(payload, ensure_ascii=False),
            t, t,
        ),
    )
    rid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    row = c.execute('SELECT * FROM maker_sku_corrections WHERE id=?', (rid,)).fetchone()
    return dict(row), True


def reference_search_url(*, manufacturer: str, maker_sku: str, hints: dict | None = None) -> str:
    parts = [stock.text(manufacturer), stock.text(maker_sku)]
    hints = hints or {}
    parts.extend(hints.get('positions') or [])
    parts.extend(hints.get('colors') or [])
    q = ' '.join(p for p in parts if p)
    # 公式 EC がある場合はメーカー検索も併記できるよう DDG を参考に残す
    return 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(q)


def midori_catalog_search_url(keyword: str) -> str:
    return (
        'https://ec.midori-anzen.com/shop/goods/search.aspx?search=x&keyword='
        + urllib.parse.quote(stock.text(keyword) or '')
    )


def kazen_catalog_search_url(keyword: str) -> str:
    return (
        'https://kazen-ec.com/item_list.php?keyword='
        + urllib.parse.quote(stock.text(keyword) or '')
    )


def unifice_catalog_search_url(keyword: str) -> str:
    """ユニフィスの品番コード検索（無料公開ページ）。"""
    return (
        'https://www.unifice.com/UNIFICE/search.php?code='
        + urllib.parse.quote(stock.text(keyword) or '')
    )


def unifice_detail_url(code: str) -> str:
    return (
        'https://www.unifice.com/U2/search.php?action=syousai&code='
        + urllib.parse.quote(stock.text(code) or '')
    )


def is_montblanc_manufacturer(manufacturer: str) -> bool:
    m = paren_classify.unify_import_notation(stock.text(manufacturer) or '')
    if not m:
        return False
    u = m.upper()
    return (
        'モンブラン' in m
        or 'MONTBLANC' in u
        or '住商モンブラン' in m
    )


def _http_get(url: str, *, timeout: float = _FETCH_TIMEOUT_SEC) -> tuple[str, str]:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': _USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ja,en;q=0.8',
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(500_000)
        charset = resp.headers.get_content_charset() or 'utf-8'
        try:
            text = raw.decode(charset, errors='replace')
        except LookupError:
            text = raw.decode('utf-8', errors='replace')
        return str(resp.geturl() or url), text


def _strip_tags(value: str) -> str:
    t = _TAG_RE.sub(' ', value or '')
    t = html_lib.unescape(t)
    return re.sub(r'\s+', ' ', t).strip()


def _extract_title(html: str) -> str:
    m = _OG_TITLE_RE.search(html or '') or _OG_TITLE_RE2.search(html or '')
    if m:
        return _strip_tags(m.group(1))
    m = _TITLE_RE.search(html or '')
    if m:
        return _strip_tags(m.group(1))
    return ''


def _host_preferred(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(h in host for h in _PREFERRED_HOST_HINTS)


def _page_mentions_sku(html: str, maker_sku: str) -> bool:
    """後方互換: ページ文字列に品番が含まれるか（厳密照合には使わない）。"""
    sk = sku_match_key(maker_sku)
    if not sk:
        return False
    return sk in sku_match_key(html or '')


def _token_is_color(token: str) -> str:
    key = paren_classify.color_match_key(token)
    for term in paren_classify.COLOR_TERMS:
        if paren_classify.color_match_key(term) == key:
            return paren_classify.unify_import_notation(term) or term
    return ''


def _token_is_size(token: str) -> str:
    """サイズ語なら代表表記を返す。フリーと F は別表記のまま（同一視は既存サイズ正規化側）。"""
    raw = paren_classify.unify_import_notation(stock.text(token) or '')
    if not raw:
        return ''
    compact = re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', raw))
    if not compact:
        return ''
    upper = compact.upper()
    for term in _SIZE_TERMS:
        term_u = unicodedata.normalize('NFKC', term).upper()
        if upper == term_u:
            return 'フリー' if term_u == 'フリー' else term
    return ''


def _model_stem(maker_sku: str) -> str:
    """APK499-10 → APK499。ハイフン後が短い枝番のときだけ茎を返す。"""
    s = stock.text(maker_sku) or ''
    if '-' not in s and '－' not in s:
        return ''
    head, tail = re.split(r'[-－]', s, maxsplit=1)
    head, tail = head.strip(), tail.strip()
    if not head or len(head) < 3:
        return ''
    # 枝番が色・サイズコードっぽい（短い英数字）ときだけ
    if re.fullmatch(r'[A-Za-z0-9Ａ-Ｚａ-ｚ０-９]{1,6}', tail or ''):
        return head
    return ''


def _strip_sku_prefixes(text: str, maker_sku: str) -> str:
    """見出しから完全品番・モデル茎を除去（空白なし密着にも対応）。"""
    t = paren_classify.unify_import_notation(stock.text(text) or '')
    if not t:
        return ''
    candidates = []
    full = stock.text(maker_sku) or ''
    stem = _model_stem(maker_sku)
    if full:
        candidates.append(full)
    if stem and stem not in candidates:
        candidates.append(stem)
    # 長い方から
    candidates.sort(key=len, reverse=True)
    for cand in candidates:
        ck = sku_match_key(cand)
        if not ck:
            continue
        # 先頭密着
        head = t[: len(cand)]
        if sku_match_key(head) == ck:
            t = t[len(cand):].lstrip(' \u3000　-－_＿/')
            continue
        # トークン一致
        parts = []
        for tok in re.split(r'[\s\u3000]+', t):
            if not tok:
                continue
            if skus_equal(tok, cand):
                continue
            # トークン先頭に品番が密着
            if len(tok) > len(cand) and sku_match_key(tok[: len(cand)]) == ck:
                parts.append(tok[len(cand):].lstrip('-－_＿'))
            else:
                parts.append(tok)
        t = ' '.join(parts).strip()
    return paren_classify.unify_import_notation(t) or t


def _classify_slash_pair(content: str) -> dict:
    """括弧内や断片の A／B を 色+サイズ / 配色 / その他 に分ける。"""
    raw = paren_classify.unify_import_notation(stock.text(content) or '')
    if not raw:
        return {'kind': 'other', 'raw': ''}
    if not any(ch in raw for ch in '/／'):
        col = _token_is_color(raw)
        if col:
            return {'kind': 'color', 'color': col, 'size': '', 'raw': raw}
        sz = _token_is_size(raw)
        if sz:
            return {'kind': 'size', 'color': '', 'size': sz, 'raw': raw}
        return {'kind': 'other', 'raw': raw}
    parts = [p for p in re.split(r'[/／]+', raw) if p.strip()]
    parts = [paren_classify.unify_import_notation(p) for p in parts]
    if len(parts) == 2:
        a, b = parts[0], parts[1]
        ca, cb = _token_is_color(a), _token_is_color(b)
        sa, sb = _token_is_size(a), _token_is_size(b)
        if ca and sb and not sa and not cb:
            return {'kind': 'color_size', 'color': ca, 'size': sb, 'raw': raw}
        if cb and sa and not sb and not ca:
            return {'kind': 'color_size', 'color': cb, 'size': sa, 'raw': raw}
        if ca and cb and not sa and not sb:
            # 黒／ワイン = 1商品配色
            way = paren_classify.normalize_colorway_display(raw) or raw
            return {'kind': 'colorway', 'color': way, 'size': '', 'raw': raw}
    # 3色以上のスラッシュ配色など
    if parts and all(_token_is_color(p) for p in parts) and not any(_token_is_size(p) for p in parts):
        way = paren_classify.normalize_colorway_display(raw) or raw
        return {'kind': 'colorway', 'color': way, 'size': '', 'raw': raw}
    return {'kind': 'other', 'raw': raw}


def _extract_h1(html: str) -> str:
    m = _H1_RE.search(html or '')
    if not m:
        return ''
    return _strip_tags(m.group(1))


def _plain_lines_from_html(html: str) -> list[str]:
    text = re.sub(r'<script\b[^>]*>[\s\S]*?</script>', ' ', html or '', flags=re.I)
    text = re.sub(r'<style\b[^>]*>[\s\S]*?</style>', ' ', text, flags=re.I)
    text = _TAG_RE.sub('\n', text)
    text = html_lib.unescape(text)
    lines = []
    for ln in text.splitlines():
        s = re.sub(r'[\s\u3000]+', ' ', ln).strip()
        if s:
            lines.append(s)
    return lines


def extract_structured_sku_values(html: str) -> list[str]:
    """本文の商品コード／品番ラベル・JSON-LD から品番値だけを拾う。

    関連リンクやナビにたまたま含まれる品番はここでは取らない。
    """
    found = []
    seen = set()

    def add(val: str):
        v = stock.text(val) or ''
        # サイズが付いた「APK499-10 M」などは先頭トークンだけ
        v = re.split(r'[\s\u3000]+', v, maxsplit=1)[0].strip()
        if not _looks_like_sku_value(v):
            return
        key = sku_match_key(v)
        if not key or key in seen:
            return
        seen.add(key)
        found.append(v)

    info = extract_product_info_fields(html or '')
    for key in ('sku', 'mpn', 'makerSku'):
        if info.get(key):
            add(info[key])
    for extra in info.get('skuValues') or []:
        add(extra)

    for m in _LABELED_SKU_RE.finditer(html or ''):
        add(m.group(1))

    lines = _plain_lines_from_html(html or '')
    labels = {
        sku_match_key('商品コード'), sku_match_key('品番'), sku_match_key('型番'),
        sku_match_key('メーカー品番'), sku_match_key('品番コード'),
        sku_match_key('SKU'), sku_match_key('MPN'),
    }
    for i, ln in enumerate(lines):
        compact = sku_match_key(ln)
        # 行全体がラベル、次行が値（KAZEN公式）。次行が別ラベルならスキップ
        if compact in labels and i + 1 < len(lines):
            nxt = lines[i + 1]
            if sku_match_key(nxt) in _FIELD_LABEL_KEYS:
                continue
            add(nxt)
            continue
        # 同一行「商品コード APK499-10」
        m = _LABELED_SKU_RE.search(ln)
        if m:
            add(m.group(1))

    for m in _JSON_LD_RE.finditer(html or ''):
        raw = (m.group(1) or '').strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            for key in ('sku', 'mpn', 'productID', 'productId'):
                if node.get(key):
                    add(str(node.get(key)))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return found


def normalize_maker_page_color(value: str) -> str:
    """商品情報表の色セルを登録用に正規化する。

    「ワイン／ネイビー」など配色は1色（ワイン×ネイビー）として扱う。
    長文・Q&A・ページ横断の色一覧は空を返す。
    """
    raw = stock.text(value) or ''
    if not raw:
        return ''
    # セル想定の短い値のみ（関連文・説明文を落とす）
    if len(raw) > 40:
        return ''
    if re.search(r'[。．？?！!]|または|もしくは|\bor\b', raw, flags=re.I):
        return ''
    if paren_classify.looks_like_colorway_text(raw):
        return paren_classify.normalize_colorway_display(raw) or ''
    if any(ch in raw for ch in '/／×✕*＊'):
        # 区切りがあるが配色と判定できない（色+サイズ等）→ 色欄としては不採用
        slash = _classify_slash_pair(raw)
        if slash.get('kind') in ('color', 'colorway'):
            return stock.text(slash.get('color') or '') or ''
        if slash.get('kind') == 'color_size':
            return stock.text(slash.get('color') or '') or ''
        return ''
    col = _token_is_color(raw)
    if col:
        return paren_classify.normalize_color_value(col) or col
    # 辞書外でも normalize が色語へ寄せられる場合のみ
    norm = paren_classify.normalize_color_value(raw)
    if norm and (_token_is_color(norm) or paren_classify.looks_like_colorway_text(norm)):
        return norm
    return ''


def _apply_product_info_label_value(out: dict, label: str, value: str, *, source: str) -> None:
    """商品情報のラベル／値を out へ反映（関連商品リストは呼ばない前提）。"""
    lab = sku_match_key(label)
    val = stock.text(value) or ''
    if not lab or not val:
        return
    if lab in _FIELD_LABEL_KEYS and sku_match_key(val) in _FIELD_LABEL_KEYS:
        return
    if lab in _SKU_FIELD_LABEL_KEYS and _looks_like_sku_value(val):
        if not out.get('sku'):
            out['sku'] = val
            out['source'] = out.get('source') or source
        out['makerSku'] = out.get('makerSku') or val
        return
    if lab in _NAME_FIELD_LABEL_KEYS:
        if sku_match_key(val) not in _FIELD_LABEL_KEYS:
            # 表の品名を優先（JSON-LD 名に品番・店名が混ざることがある）
            out['name'] = val
            out['source'] = source
        return
    if lab in _COLOR_FIELD_LABEL_KEYS:
        color = normalize_maker_page_color(val)
        if color and not out.get('color'):
            out['color'] = color
            out['colorSource'] = source
        return
    if lab in _BRAND_FIELD_LABEL_KEYS and not out.get('brand'):
        out['brand'] = val


def extract_product_info_fields(html: str) -> dict:
    """商品情報表・見出し・JSON-LD から同一商品の品番／品名／ブランド／色を取る。

    関連商品リンクやパンくず・ページ横断の色一覧は混ぜない
    （表セル・dl と Product 構造化データを優先）。
    """
    out = {
        'sku': '',
        'mpn': '',
        'makerSku': '',
        'name': '',
        'brand': '',
        'color': '',
        'colorSource': '',
        'skuValues': [],
        'source': '',
    }
    raw_html = html or ''

    # 1) JSON-LD Product
    for m in _JSON_LD_RE.finditer(raw_html):
        raw = (m.group(1) or '').strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            types = node.get('@type') or node.get('type') or ''
            type_l = (
                ' '.join(types) if isinstance(types, list) else str(types)
            ).lower()
            if 'product' in type_l:
                sku = stock.text(str(node.get('sku') or ''))
                mpn = stock.text(str(node.get('mpn') or ''))
                name = stock.text(str(node.get('name') or ''))
                color = normalize_maker_page_color(str(node.get('color') or ''))
                brand = ''
                b = node.get('brand')
                if isinstance(b, dict):
                    brand = stock.text(str(b.get('name') or ''))
                elif b:
                    brand = stock.text(str(b))
                if not brand:
                    mf = node.get('manufacturer')
                    if isinstance(mf, dict):
                        brand = stock.text(str(mf.get('name') or ''))
                    elif mf:
                        brand = stock.text(str(mf))
                if sku and not out['sku']:
                    out['sku'] = sku
                if mpn and not out['mpn']:
                    out['mpn'] = mpn
                if name and not out['name']:
                    out['name'] = name
                if brand and not out['brand']:
                    out['brand'] = brand
                if color and not out['color']:
                    out['color'] = color
                    out['colorSource'] = 'json_ld'
                if sku or mpn or name:
                    out['source'] = out['source'] or 'json_ld'
            for v in node.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)

    # 2) 商品情報表
    # 横並び: <TH>品番</TH><TH>品名</TH><TH>色</TH> … <TD>…</TD>
    # 縦並び: <tr><th>色</th><td>ワイン／ネイビー</td></tr>（uniform1 等）
    table_chunks = re.findall(r'<table\b[^>]*>[\s\S]*?</table>', raw_html, flags=re.I)
    for table in table_chunks:
        headers = [
            _strip_tags(h)
            for h in re.findall(r'<th\b[^>]*>([\s\S]*?)</th>', table, flags=re.I)
        ]
        header_keys = [sku_match_key(h) for h in headers]
        # 縦型: 行ごとに th + td
        for row_html in re.findall(r'<tr\b[^>]*>[\s\S]*?</tr>', table, flags=re.I):
            ths = [
                _strip_tags(h)
                for h in re.findall(r'<th\b[^>]*>([\s\S]*?)</th>', row_html, flags=re.I)
            ]
            tds = [
                _strip_tags(td)
                for td in re.findall(r'<td\b[^>]*>([\s\S]*?)</td>', row_html, flags=re.I)
            ]
            if len(ths) == 1 and len(tds) == 1:
                _apply_product_info_label_value(
                    out, ths[0], tds[0], source='product_table',
                )

        if not header_keys:
            continue
        sku_i = next(
            (i for i, k in enumerate(header_keys) if k in _SKU_FIELD_LABEL_KEYS),
            -1,
        )
        name_i = next(
            (i for i, k in enumerate(header_keys) if k in _NAME_FIELD_LABEL_KEYS),
            -1,
        )
        color_i = next(
            (i for i, k in enumerate(header_keys) if k in _COLOR_FIELD_LABEL_KEYS),
            -1,
        )
        brand_i = next(
            (i for i, k in enumerate(header_keys) if k in _BRAND_FIELD_LABEL_KEYS),
            -1,
        )
        if sku_i < 0 and name_i < 0 and color_i < 0:
            continue
        # ヘッダ行以外の最初のデータ行
        for row_html in re.findall(r'<tr\b[^>]*>[\s\S]*?</tr>', table, flags=re.I):
            if re.search(r'<th\b', row_html, flags=re.I) and not re.search(
                r'<td\b', row_html, flags=re.I,
            ):
                continue
            cells = [
                _strip_tags(td)
                for td in re.findall(r'<td\b[^>]*>([\s\S]*?)</td>', row_html, flags=re.I)
            ]
            if not cells:
                continue
            if sku_i >= 0 and sku_i < len(cells):
                _apply_product_info_label_value(
                    out, headers[sku_i], cells[sku_i], source='product_table',
                )
            if name_i >= 0 and name_i < len(cells):
                _apply_product_info_label_value(
                    out, headers[name_i], cells[name_i], source='product_table',
                )
            if color_i >= 0 and color_i < len(cells):
                _apply_product_info_label_value(
                    out, headers[color_i], cells[color_i], source='product_table',
                )
            if brand_i >= 0 and brand_i < len(cells) and not out.get('brand'):
                _apply_product_info_label_value(
                    out, headers[brand_i], cells[brand_i], source='product_table',
                )
            # 横並び表は先頭データ行だけ（関連行を混ぜない）
            if sku_i >= 0 or name_i >= 0:
                break

    # 3) dl/dt/dd の商品情報（空のカラー欄は無視）
    for m in re.finditer(
        r'<dt\b[^>]*>([\s\S]*?)</dt>\s*<dd\b[^>]*>([\s\S]*?)</dd>',
        raw_html,
        flags=re.I,
    ):
        _apply_product_info_label_value(
            out, _strip_tags(m.group(1)), _strip_tags(m.group(2)), source='product_dl',
        )

    # ブランド名だけの小さな表（従来フォールバック）
    brand_m = re.search(
        r'ブランド名[\s\S]{0,120}?</th>[\s\S]{0,80}?<td\b[^>]*>([\s\S]*?)</td>',
        raw_html,
        flags=re.I,
    )
    if brand_m and not out['brand']:
        out['brand'] = _strip_tags(brand_m.group(1))

    # サイト管理番号（mpn）とメーカー品番の対応メモ
    values = []
    for key in ('sku', 'mpn', 'makerSku'):
        if out.get(key):
            values.append(out[key])
    out['skuValues'] = values
    if out.get('sku') and not out.get('makerSku'):
        out['makerSku'] = out['sku']
    return out


def site_management_confirms_maker_sku(
    *,
    maker_sku: str,
    page_sku: str = '',
    page_mpn: str = '',
    html: str = '',
) -> dict:
    """サイト管理番号（例: MON9-970）がメーカー品番と対応するか。

    MON を全品番から機械削除しない。ページ内でメーカー品番自体が
    確認できるときだけ True。
    """
    sk = sku_match_key(maker_sku)
    empty = {'confirmed': False, 'siteCode': '', 'makerSkuOnPage': ''}
    if not sk:
        return empty
    page_sku_t = stock.text(page_sku) or ''
    page_mpn_t = stock.text(page_mpn) or ''
    if not page_sku_t and not page_mpn_t:
        info = extract_product_info_fields(html or '')
        page_sku_t = info.get('sku') or ''
        page_mpn_t = info.get('mpn') or ''
    if not skus_equal(page_sku_t, maker_sku):
        return empty
    # メーカー品番がページ上で確認できた。管理番号が別表記なら併記
    site = ''
    if page_mpn_t and not skus_equal(page_mpn_t, maker_sku):
        mpn_k = sku_match_key(page_mpn_t)
        if mpn_k.endswith(sk) and len(mpn_k) > len(sk):
            # 接頭辞付き管理番号（MON9-970 等）。接頭辞はメーカー固有で機械削除しない
            site = page_mpn_t
    return {
        'confirmed': True,
        'siteCode': site,
        'makerSkuOnPage': page_sku_t or maker_sku,
    }


def _sku_match_evidence_label(*, match: str, color_code: str = '') -> str:
    if match == 'base_color' and color_code:
        return f'基礎品番一致＋色番{color_code}一致'
    if match == 'base_only':
        return '基礎品番一致・色番未確認'
    if match == 'structured':
        return '本文商品コード一致'
    if match == 'title':
        return '見出し品番一致'
    return ''


def page_sku_match(
    html: str,
    title: str,
    maker_sku: str,
    *,
    manufacturer: str = '',
    page_url: str = '',
) -> dict:
    """品番一致の根拠を返す。

    match: structured / title / base_color / base_only / none
    本文にたまたま含まれるだけ・関連リンク内だけの一致は none。
    メーカー別ルールで基礎＋色番照合（例: アイトス）を行う。
    """
    sk = sku_match_key(maker_sku)
    empty = {
        'match': 'none',
        'matchedValue': '',
        'evidence': '',
        'evidenceLabel': '',
        'siteCode': '',
        'baseSku': '',
        'colorCode': '',
        'colorName': '',
        'pageName': '',
        'productName': '',
        'brand': '',
    }
    if not sk:
        return empty

    def _ok(payload: dict) -> dict:
        out = dict(empty)
        out.update(payload)
        out['evidenceLabel'] = _sku_match_evidence_label(
            match=out.get('match') or '',
            color_code=out.get('colorCode') or '',
        ) or out.get('evidenceLabel') or ''
        return out

    info = extract_product_info_fields(html or '')
    # 商品情報表・JSON-LD のメーカー品番を最優先
    for val, evidence in (
        (info.get('sku') or '', 'product_sku_field'),
        (info.get('makerSku') or '', 'product_table'),
    ):
        if val and skus_equal(val, maker_sku):
            conf = site_management_confirms_maker_sku(
                maker_sku=maker_sku,
                page_sku=info.get('sku') or val,
                page_mpn=info.get('mpn') or '',
            )
            return _ok({
                'match': 'structured',
                'matchedValue': val,
                'evidence': evidence,
                'siteCode': conf.get('siteCode') or '',
                'productName': info.get('name') or '',
                'brand': info.get('brand') or '',
            })

    for val in extract_structured_sku_values(html or ''):
        if skus_equal(val, maker_sku):
            conf = site_management_confirms_maker_sku(
                maker_sku=maker_sku,
                page_sku=info.get('sku') or val,
                page_mpn=info.get('mpn') or '',
                html=html,
            )
            return _ok({
                'match': 'structured',
                'matchedValue': val,
                'evidence': 'product_code_field',
                'siteCode': conf.get('siteCode') or '',
                'productName': info.get('name') or '',
                'brand': info.get('brand') or '',
            })

    title_u = paren_classify.unify_import_notation(stock.text(title) or '')
    h1 = _extract_h1(html or '')
    for label, text in (('title', title_u), ('h1', h1), ('og_or_title', _extract_title(html or ''))):
        if not text:
            continue
        if _text_contains_full_sku(text, maker_sku):
            return _ok({
                'match': 'title',
                'matchedValue': maker_sku,
                'evidence': label,
                'productName': info.get('name') or '',
                'brand': info.get('brand') or '',
            })

    parts = split_maker_sku_parts(
        maker_sku, manufacturer=manufacturer, page_url=page_url,
    )
    if parts.get('rule') == 'aitoz' and parts.get('base'):
        page = extract_aitoz_page_product(html or '')
        page_base = page.get('baseSku') or ''
        base_hit = False
        matched_base = ''
        if page_base and aitoz_bases_equal(page_base, parts['base']):
            base_hit = True
            matched_base = page_base
        else:
            for val in extract_structured_sku_values(html or ''):
                if aitoz_bases_equal(val, parts['base']):
                    base_hit = True
                    matched_base = val
                    break
            if not base_hit:
                for text in (title_u, h1, _extract_title(html or '')):
                    if text and (
                        _text_contains_full_sku(text, parts['base'])
                        or _text_contains_full_sku(text, aitoz_base_match_key(parts['base']))
                    ):
                        base_hit = True
                        matched_base = parts['base']
                        break
        if base_hit:
            want_color = parts.get('colorCode') or ''
            page_name = page.get('name') or info.get('name') or ''
            if want_color:
                color_name = ''
                color_hit = False
                for col in page.get('colors') or []:
                    if color_codes_equal(col.get('code') or '', want_color):
                        color_hit = True
                        color_name = col.get('name') or ''
                        break
                if color_hit:
                    return _ok({
                        'match': 'base_color',
                        'matchedValue': matched_base or parts['base'],
                        'evidence': 'aitoz_base_and_color',
                        'baseSku': matched_base or parts['base'],
                        'colorCode': want_color,
                        'colorName': color_name,
                        'pageName': page_name,
                        'productName': page_name,
                        'brand': info.get('brand') or '',
                    })
                return _ok({
                    'match': 'base_only',
                    'matchedValue': matched_base or parts['base'],
                    'evidence': 'aitoz_base_only',
                    'baseSku': matched_base or parts['base'],
                    'colorCode': want_color,
                    'pageName': page_name,
                    'productName': page_name,
                    'brand': info.get('brand') or '',
                })
            return _ok({
                'match': 'structured',
                'matchedValue': matched_base or parts['base'],
                'evidence': 'aitoz_base',
                'baseSku': matched_base or parts['base'],
                'pageName': page_name,
                'productName': page_name,
                'brand': info.get('brand') or '',
            })
    return empty


def parse_maker_listing_title(
    title: str,
    maker_sku: str,
    *,
    sku_verified: bool = False,
) -> dict | None:
    """掲載名から 商品名／色／サイズ／上下区分を分解。

    - 既定: 見出しに完全品番が必要（従来）
    - sku_verified=True: 本文の商品コード等で品番一致済みなら、見出しが
      モデル茎のみ（APK499…）でも分解する
    - 「ホワイト／フリー」は色+サイズ。「黒／ワイン」は配色1色
    - （無地）など非色・非サイズの括弧は商品名に残す
    - 単独トークンの「上」「下」は品番末尾用の position（補足にはしない）
    """
    unified = paren_classify.unify_import_notation(stock.text(title) or '')
    if not unified:
        return None
    base_sku, _ = position_sku.strip_sku_position_suffix(maker_sku)
    base_sku = base_sku or stock.text(maker_sku)
    sk = sku_match_key(base_sku)
    # 見出しに完全品番があるか（茎だけの APK499 は完全一致にしない）
    # VEH30B下 / VEH30B 下 も基礎品番一致として認める
    title_has_full = bool(sk) and (
        _text_contains_full_sku(unified, base_sku)
        or _text_contains_full_sku(unified, maker_sku)
    )
    stem = _model_stem(base_sku)
    # 本文商品コード等で品番確認済みなら、見出しに完全品番が無くても分解する
    if not title_has_full and not sku_verified:
        return None

    # ECサイトの「｜店名」サフィックスは商品名から外す
    heading = re.split(r'[｜|]', unified, maxsplit=1)[0].strip()
    working = _strip_sku_prefixes(heading, base_sku)
    # 見出し内の「VEH30B下」「VEH30B 下」も品番として落とす
    for variant in position_sku.sku_code_variants(maker_sku) + position_sku.sku_code_variants(base_sku):
        working = _strip_sku_prefixes(working, variant)
    color = ''
    size = ''
    position = ''
    kept_parens = []

    def _consume_paren(content: str) -> str:
        nonlocal color, size
        info = _classify_slash_pair(content)
        kind = info.get('kind')
        if kind in ('color', 'colorway'):
            if not color:
                color = info.get('color') or ''
            return ''
        if kind == 'color_size':
            if not color:
                color = info.get('color') or ''
            if not size:
                size = info.get('size') or ''
            return ''
        if kind == 'size':
            if not size:
                size = info.get('size') or ''
            return ''
        # 無地 等は商品名側へ
        return f'（{paren_classify.unify_import_notation(content) or content}）'

    # 括弧を先に処理
    pieces = []
    pos = 0
    for m in _PAREN_CHUNK_RE.finditer(working):
        pieces.append(working[pos:m.start()])
        kept = _consume_paren(m.group(1) or '')
        if kept:
            pieces.append(kept)
            kept_parens.append(kept)
        pos = m.end()
    pieces.append(working[pos:])
    residual = ''.join(pieces)
    residual = paren_classify.unify_import_notation(residual) or residual

    name_parts = []
    for tok in re.split(r'[\s\u3000]+', residual):
        if not tok:
            continue
        if skus_equal(tok, base_sku) or skus_equal(tok, maker_sku) or (
            stem and skus_equal(tok, stem)
        ):
            continue
        # 品番＋上下が密着したトークン
        tok_base, tok_pos = position_sku.strip_sku_position_suffix(tok)
        if tok_pos and skus_equal(tok_base, base_sku):
            position = tok_pos
            continue
        if tok in _POSITION_TOKENS:
            position = tok
            continue
        # トークン自体が ホワイト／フリー
        slash_info = _classify_slash_pair(tok) if any(ch in tok for ch in '/／') else None
        if slash_info and slash_info.get('kind') in ('color_size', 'color', 'colorway', 'size'):
            if slash_info['kind'] == 'color_size':
                if not color:
                    color = slash_info.get('color') or ''
                if not size:
                    size = slash_info.get('size') or ''
            elif slash_info['kind'] in ('color', 'colorway'):
                if not color:
                    color = slash_info.get('color') or ''
            elif slash_info['kind'] == 'size':
                if not size:
                    size = slash_info.get('size') or ''
            continue
        col = _token_is_color(tok)
        if col:
            if not color:
                color = col
            continue
        sz = _token_is_size(tok)
        if sz:
            if not size:
                size = sz
            continue
        name_parts.append(tok)

    # 見出しに混ざるメーカー／販社・店名トークンは商品名から外す
    noise_keys = {sku_match_key(n) for n in _NAME_NOISE_TOKENS}
    cleaned_parts = []
    for p in name_parts:
        key = sku_match_key(p)
        if key and key in noise_keys:
            continue
        if paren_classify.unify_import_notation(p) in _NAME_NOISE_TOKENS:
            continue
        # 「の9-970」のような所有格＋品番
        if p.startswith('の') and len(p) > 1 and (
            skus_equal(p[1:], base_sku) or skus_equal(p[1:], maker_sku)
            or (stem and skus_equal(p[1:], stem))
        ):
            continue
        cleaned_parts.append(p)
    name = ' '.join(cleaned_parts).strip()
    name = re.sub(r'[\s\u3000]+', ' ', name).strip()
    # 店名・販促の【…】【】は商品名から外す（中身は仕様ではない）
    name = re.sub(r'【[^】]*】', ' ', name)
    # 末尾の「の品番」残渣
    name = re.sub(
        r'(?:の)?' + re.escape(base_sku) + r'\s*$',
        '',
        name,
        flags=re.I,
    )
    name = re.sub(r'[\s\u3000]+', ' ', name).strip()
    # 括弧直前の余分な空白を整える
    name = re.sub(r'\s+（', '（', name)
    name = paren_classify.unify_import_notation(name) or name
    if not name:
        return None
    # 注文単位・価格・色番一覧など販売情報を除去（七分袖等は保持）
    cleaned = clean_maker_product_name(name, maker_sku=base_sku)
    name = cleaned.get('cleanedName') or name
    if not name:
        return None
    planned_sku = planned_registered_sku(maker_sku=base_sku, position=position)
    return {
        'name': name,
        'color': color,
        'size': size,
        # 上下は品番末尾へ移す。補足欄へは載せない（他補足と混同しない）
        'position': position,
        'supplement': '',
        'plannedRegisteredSku': planned_sku,
        'title': unified,
        'originalTitle': unified,
        'cleanedName': name,
        'skuInTitle': bool(title_has_full),
        'missingFields': [
            f for f, v in (('name', name), ('color', color)) if not v
        ],
    }


def _candidate_passes_hints(parsed: dict, hints: dict) -> bool:
    """上下・色のヒントがあるときは区別を一致させる。類似度だけでは通さない。"""
    positions = list(hints.get('positions') or [])
    colors = list(hints.get('colors') or [])
    pos = stock.text(parsed.get('position') or '')
    if not pos:
        # 旧データ互換: supplement に上／下が残っている場合
        supp = stock.text(parsed.get('supplement') or '')
        if supp in _POSITION_TOKENS:
            pos = supp
    col = stock.text(parsed.get('color') or '')
    if positions:
        if pos not in positions:
            return False
    if colors:
        # ヒント色があるとき、候補色が取れていれば一致必須。取れなければ却下
        if not col:
            return False
        if paren_classify.color_match_key(col) not in {
            paren_classify.color_match_key(c) for c in colors
        }:
            return False
    return True


def _ddg_candidates(query: str, *, limit: int = 8) -> list[dict]:
    url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(query)
    _final_url, html = _http_get(url)
    out = []
    for m in _DDG_RESULT_RE.finditer(html):
        href = html_lib.unescape(m.group(1) or '')
        title = _strip_tags(m.group(2) or '')
        if 'uddg=' in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if qs.get('uddg'):
                href = qs['uddg'][0]
        if not href.startswith('http'):
            continue
        out.append({
            'title': title,
            'url': href,
            'source': 'duckduckgo_html',
            'preferred': _host_preferred(href),
        })
        if len(out) >= limit:
            break
    return out


def _midori_ec_candidates(keyword: str, *, limit: int = 12) -> list[dict]:
    """ミドリ安全 EC の無料商品検索。"""
    url = midori_catalog_search_url(keyword)
    _final, html = _http_get(url)
    out = []
    seen = set()
    for m in _MIDORI_RESULT_RE.finditer(html):
        path = m.group(1)
        title = html_lib.unescape(m.group(2) or '')
        abs_url = urllib.parse.urljoin('https://ec.midori-anzen.com', path)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        out.append({
            'title': title,
            'url': abs_url,
            'source': 'midori_ec',
            'preferred': True,
            'manufacturer': 'ミドリ安全',
        })
        if len(out) >= limit:
            break
    return out


def _kazen_ec_candidates(keyword: str, *, limit: int = 12) -> list[dict]:
    """KAZEN 公式 EC の無料商品リスト（keyword 検索）。

    一覧のタイトルに完全品番が無いことが多いので、候補 URL を返し
    取得後に本文「商品コード」で厳密照合する。
    キーワードと無関係な一覧ノイズはタイトルにキーワード茎が無いものは落とす。
    """
    url = kazen_catalog_search_url(keyword)
    _final, html = _http_get(url)
    out = []
    seen = set()
    kw = stock.text(keyword) or ''
    stem = _model_stem(kw) or kw
    stem_k = sku_match_key(stem)
    for m in _KAZEN_ITEM_RE.finditer(html):
        path = m.group(1)
        title = _strip_tags(m.group(2) or '')
        abs_url = urllib.parse.urljoin('https://kazen-ec.com/', path)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        title_k = sku_match_key(title)
        if stem_k and stem_k not in title_k and sku_match_key(kw) not in title_k:
            # 一覧が検索語と無関係な別商品ばかりのことがある
            continue
        out.append({
            'title': title,
            'url': abs_url,
            'source': 'kazen_ec',
            'preferred': True,
            'manufacturer': 'KAZEN',
        })
        if len(out) >= limit:
            break
    return out


def _unifice_page_looks_like_product(html: str, maker_sku: str) -> bool:
    """ユニフィスの code= 応答が当該メーカー品番の商品詳細か。"""
    if not html or not maker_sku:
        return False
    info = extract_product_info_fields(html)
    if info.get('sku') and skus_equal(info['sku'], maker_sku):
        return True
    title = _extract_title(html) or ''
    h1 = _extract_h1(html) or ''
    if _text_contains_full_sku(title, maker_sku) or _text_contains_full_sku(h1, maker_sku):
        # 検索一覧のノイズタイトルを避ける
        if '品名から色や人気順' in title:
            return False
        return True
    return False


def _unifice_ec_candidates(keyword: str, *, manufacturer: str = '', limit: int = 6) -> list[dict]:
    """ユニフィス（住商モンブラン等の販社カタログ）の無料品番検索。

    code=メーカー品番 で詳細が返ることが多い。モンブラン向けにサイト管理番号
    MON+品番も試すが、ページ内でメーカー品番が確認できた場合のみ採用する
    （全品番から MON を機械削除しない）。
    """
    kw = re.sub(r'\bsite:\S+', '', stock.text(keyword) or '').strip()
    # クエリにメーカー名が混ざる場合は末尾の品番トークンを使う
    parts = [p for p in re.split(r'[\s\u3000]+', kw) if p]
    sku_token = ''
    for p in reversed(parts):
        if _looks_like_sku_value(p):
            sku_token = p
            break
    if not sku_token and parts:
        sku_token = parts[-1]
    if not sku_token:
        return []

    codes_to_try = [sku_token]
    # モンブラン系のみサイト管理番号の候補を追加（接頭辞の機械削除ではない）
    if is_montblanc_manufacturer(manufacturer) or not manufacturer:
        mon = 'MON' + sku_token
        if mon not in codes_to_try:
            codes_to_try.append(mon)

    out = []
    seen = set()
    for code in codes_to_try:
        if len(out) >= limit:
            break
        for build_url in (unifice_catalog_search_url, unifice_detail_url):
            url = build_url(code)
            if url in seen:
                continue
            try:
                final_url, html = _http_get(url)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
                continue
            seen.add(url)
            seen.add(final_url)
            if not _unifice_page_looks_like_product(html, sku_token):
                continue
            # MON 管理番号ページはメーカー品番確認済みのものだけ
            info = extract_product_info_fields(html)
            if code.upper().startswith('MON') and not skus_equal(code, sku_token):
                conf = site_management_confirms_maker_sku(
                    maker_sku=sku_token,
                    page_sku=info.get('sku') or '',
                    page_mpn=info.get('mpn') or code,
                    html=html,
                )
                if not conf.get('confirmed'):
                    continue
            title = (
                info.get('name')
                or _extract_h1(html)
                or _extract_title(html)
                or f'{sku_token}'
            )
            out.append({
                'title': title,
                'url': final_url or url,
                'source': 'unifice',
                'preferred': True,
                'manufacturer': stock.text(manufacturer) or '住商モンブラン',
                'pageHtml': html,
            })
            if len(out) >= limit:
                break
    return out


def _aitoz_sku_token_from_text(text: str) -> str:
    """検索語やクエリから AZ 形品番トークンを1つ取る。"""
    for tok in re.split(r'[\s\u3000]+', stock.text(text) or ''):
        t = tok.strip().strip('「」『』()（）[]')
        if t and _AITOZ_SKU_RE.fullmatch(t):
            return t
    return ''


def _aitoz_direct_candidates(maker_sku: str, *, manufacturer: str = '') -> list[dict]:
    """アイトス公式の無料商品詳細 URL を基礎品番から直接組み立てる（検索APIなし）。"""
    raw = stock.text(maker_sku) or ''
    token = raw if _AITOZ_SKU_RE.fullmatch(raw) else _aitoz_sku_token_from_text(raw)
    if not token:
        return []
    parts = split_maker_sku_parts(token, manufacturer=manufacturer or 'アイトス')
    if parts.get('rule') != 'aitoz' or not parts.get('hinban'):
        return []
    hinban = parts['hinban']
    base = parts['base']
    compact = aitoz_base_match_key(base)
    urls = [
        f'https://azweb.aitoz.co.jp/item/detail.aspx?hinban={hinban}',
        f'https://azweb.aitoz.co.jp/item/detail.aspx?itemcode={urllib.parse.quote(base)}',
    ]
    if compact and compact != base:
        urls.append(
            f'https://azweb.aitoz.co.jp/item/detail.aspx?itemcode={urllib.parse.quote(compact)}'
        )
    out = []
    seen = set()
    for abs_url in urls:
        if abs_url in seen:
            continue
        seen.add(abs_url)
        out.append({
            'title': f'{base} アイトス',
            'url': abs_url,
            'source': 'aitoz_direct',
            'preferred': True,
            'manufacturer': 'アイトス',
        })
    return out


def _catalog_searches_for(
    *,
    manufacturer: str,
    query: str,
    maker_sku: str = '',
) -> list[dict]:
    """メーカーに応じた無料カタログ検索。"""
    hits = []
    mfr = stock.text(manufacturer) or ''
    mfr_u = paren_classify.unify_import_notation(mfr).upper()
    # ミドリ安全（仕入先ヒントまたは明示）
    if (not mfr) or ('ミドリ安全' in mfr):
        try:
            hits.extend(_midori_ec_candidates(query))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            pass
    if (not mfr) or ('KAZEN' in mfr_u) or ('カゼン' in mfr):
        # site: 付きクエリは公式サイト検索語から外す
        kw = re.sub(r'\bsite:\S+', '', query or '').strip() or query
        try:
            hits.extend(_kazen_ec_candidates(kw))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            pass
    if (not mfr) or is_montblanc_manufacturer(mfr):
        try:
            hits.extend(_unifice_ec_candidates(query, manufacturer=mfr or '住商モンブラン'))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            pass
    if (not mfr) or ('アイトス' in mfr) or ('AITOZ' in mfr_u):
        direct_sku = stock.text(maker_sku) or _aitoz_sku_token_from_text(query)
        if direct_sku:
            try:
                hits.extend(
                    _aitoz_direct_candidates(direct_sku, manufacturer=mfr or 'アイトス')
                )
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
                pass
    return hits


def _brand_matches_manufacturer(brand: str, manufacturer: str) -> bool:
    """ページ上のブランドと指定メーカーが矛盾しないか（空は未判定＝通す）。"""
    b = paren_classify.unify_import_notation(stock.text(brand) or '')
    m = paren_classify.unify_import_notation(stock.text(manufacturer) or '')
    if not b or not m:
        return True
    bk, mk = sku_match_key(b), sku_match_key(m)
    if not bk or not mk:
        return True
    if bk in mk or mk in bk:
        return True
    # 既知ブランドの別名（MONTBLANC ↔ 住商モンブラン）
    aliases = (
        ('MONTBLANC', 'モンブラン', '住商モンブラン'),
        ('KAZEN', 'カゼン'),
        ('ミドリ安全', 'MIDORI'),
    )
    for group in aliases:
        keys = {sku_match_key(x) for x in group}
        if any(k and k in bk for k in keys) and any(k and k in mk for k in keys):
            return True
    return False


def _build_candidate_from_hit(
    hit: dict, *,
    maker_sku: str,
    manufacturer: str,
    hints: dict,
    current_name: str,
    current_color: str,
    current_supplement: str = '',
    current_sku: str = '',
    require_manufacturer_match: bool = False,
) -> dict:
    """候補化の結果を返す。

    result:
      - candidate: 採用可能な候補 dict
      - rejection: sku_mismatch / maker_mismatch / parse_incomplete /
                   hint_reject / page_unavailable
    """
    title = hit.get('title') or ''
    url = hit.get('url') or ''
    page_html = hit.get('pageHtml') or ''
    page_title = title
    if not page_html:
        try:
            _final, page_html = _http_get(url)
            page_title = _extract_title(page_html) or title
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            return {
                'candidate': None,
                'rejection': 'page_unavailable',
                'detail': {
                    'sourceUrl': url,
                    'title': title,
                    'reason': 'page_unavailable',
                },
            }
    else:
        page_title = _extract_title(page_html) or title

    pos_base, _ = position_sku.strip_sku_position_suffix(maker_sku)
    pos_base = pos_base or stock.text(maker_sku)
    # 登録予定は台帳の完全品番を維持（ページ基礎品番へ置き換えない）
    ledger_sku = stock.text(current_sku) or pos_base
    heading = page_title or title or _extract_h1(page_html)
    info = extract_product_info_fields(page_html)
    mfr = stock.text(manufacturer) or stock.text(hit.get('manufacturer') or '')
    sku_info = page_sku_match(
        page_html, heading, pos_base,
        manufacturer=mfr, page_url=url,
    )
    if sku_info.get('match') == 'none' and not skus_equal(pos_base, maker_sku):
        sku_info = page_sku_match(
            page_html, heading, maker_sku,
            manufacturer=mfr, page_url=url,
        )
    if sku_info.get('match') == 'none':
        return {
            'candidate': None,
            'rejection': 'sku_mismatch',
            'detail': {
                'sourceUrl': url,
                'title': heading,
                'reason': 'sku_mismatch',
                'note': '商品情報表・構造化データ／見出しの完全品番が一致しませんでした。',
            },
        }

    page_brand = stock.text(sku_info.get('brand') or info.get('brand') or '')
    if require_manufacturer_match and mfr and page_brand and not _brand_matches_manufacturer(
        page_brand, mfr,
    ):
        return {
            'candidate': None,
            'rejection': 'maker_mismatch',
            'detail': {
                'sourceUrl': url,
                'title': heading,
                'reason': 'maker_mismatch',
                'brand': page_brand,
                'manufacturer': mfr,
                'note': (
                    f'ページのブランド「{page_brand}」が指定メーカー「{mfr}」と一致しません。'
                ),
            },
        }

    sku_parts = split_maker_sku_parts(
        ledger_sku or pos_base, manufacturer=mfr, page_url=url,
    )
    sku_verified = sku_info.get('match') in (
        'structured', 'base_color', 'base_only',
    )
    parsed = None
    # アイトス公式は goodsname / カラー欄を優先
    if sku_parts.get('rule') == 'aitoz' or str(sku_info.get('evidence') or '').startswith('aitoz'):
        page_name = stock.text(sku_info.get('pageName') or sku_info.get('productName') or '')
        if not page_name:
            page_name = stock.text(extract_aitoz_page_product(page_html).get('name') or '')
        if page_name:
            color = ''
            if sku_info.get('match') == 'base_color':
                color = stock.text(sku_info.get('colorName') or '')
            missing = []
            if not color and sku_info.get('match') == 'base_only':
                missing.append('color')
            cleaned = clean_maker_product_name(page_name, maker_sku=pos_base)
            cleaned_name = stock.text(cleaned.get('cleanedName') or page_name)
            parsed = {
                'name': cleaned_name,
                'cleanedName': cleaned_name,
                'color': color,
                'size': '',
                'position': '',
                'supplement': '',
                'title': heading,
                'originalTitle': page_name,
                'skuInTitle': False,
                'missingFields': missing,
            }

    # 商品情報表・構造化の品名を最優先（タイトルの店名・メーカー混入を避ける）
    field_name = stock.text(sku_info.get('productName') or info.get('name') or '')
    h1 = _extract_h1(page_html)
    parse_sources = []
    for src in (field_name, heading, title, h1):
        s = stock.text(src) or ''
        if s and s not in parse_sources:
            parse_sources.append(s)

    parsed_list = []
    for src in parse_sources:
        p = parse_maker_listing_title(
            src, pos_base, sku_verified=sku_verified or (src == field_name and bool(field_name)),
        )
        if p and stock.text(p.get('name') or ''):
            parsed_list.append(p)

    if not parsed and field_name:
        field_parsed = parse_maker_listing_title(
            field_name, pos_base, sku_verified=True,
        )
        if field_parsed and stock.text(field_parsed.get('name') or ''):
            if not stock.text(field_parsed.get('color') or ''):
                for other in parsed_list:
                    if stock.text(other.get('color') or ''):
                        field_parsed = dict(field_parsed)
                        field_parsed['color'] = other.get('color') or ''
                        if not field_parsed.get('size') and other.get('size'):
                            field_parsed['size'] = other.get('size') or ''
                        miss = [
                            f for f in (field_parsed.get('missingFields') or [])
                            if not (f == 'color' and field_parsed.get('color'))
                        ]
                        field_parsed['missingFields'] = miss
                        break
            parsed = field_parsed
    if not parsed and parsed_list:
        parsed_list.sort(key=lambda p: (
            0 if (p.get('name') and p.get('color')) else 1,
            len(p.get('missingFields') or []),
            len(stock.text(p.get('name') or '')),
        ))
        parsed = parsed_list[0]

    if parsed and sku_info.get('match') == 'base_color' and sku_info.get('colorName'):
        parsed = dict(parsed)
        parsed['color'] = stock.text(sku_info.get('colorName') or '')
        miss = [f for f in (parsed.get('missingFields') or []) if f != 'color']
        parsed['missingFields'] = miss
    # 商品情報表・JSON-LD の色を、見出し分解で取れなかったときだけ補う
    # （ページ横断の色一覧は extract 側で除外済み）
    table_color = normalize_maker_page_color(info.get('color') or '')
    color_from_table = False
    if parsed and not stock.text(parsed.get('color') or '') and table_color:
        if sku_info.get('match') != 'base_only':
            parsed = dict(parsed)
            parsed['color'] = table_color
            color_from_table = True
            miss = [
                f for f in (parsed.get('missingFields') or [])
                if not (f == 'color' and parsed.get('color'))
            ]
            parsed['missingFields'] = miss
    if parsed and sku_info.get('match') == 'base_only':
        parsed = dict(parsed)
        parsed['color'] = ''
        miss = list(parsed.get('missingFields') or [])
        if 'color' not in miss:
            miss.append('color')
        parsed['missingFields'] = miss

    if not parsed or not stock.text(parsed.get('name') or ''):
        missing = ['name']
        if not (parsed and parsed.get('color')):
            missing.append('color')
        return {
            'candidate': None,
            'rejection': 'parse_incomplete',
            'detail': {
                'sourceUrl': url,
                'title': heading,
                'reason': 'parse_incomplete',
                'skuMatch': sku_info.get('match'),
                'skuMatchEvidence': sku_info.get('evidenceLabel') or '',
                'missingFields': missing,
                'siteCode': sku_info.get('siteCode') or '',
            },
        }
    if not _candidate_passes_hints(parsed, hints):
        return {
            'candidate': None,
            'rejection': 'hint_reject',
            'detail': {
                'sourceUrl': url,
                'title': heading,
                'reason': 'hint_reject',
            },
        }

    # ヒントの上下があれば登録予定品番へ反映（掲載に無い場合の補完はしない＝矛盾防止）
    position = stock.text(parsed.get('position') or '')
    hint_positions = list(hints.get('positions') or [])
    if not position and len(hint_positions) == 1:
        position = hint_positions[0]
    planned_src = ledger_sku or pos_base
    planned_sku = planned_registered_sku(maker_sku=planned_src, position=position)
    # 上下を補足へ重複表示しない。上下以外の補足のみ残す
    supplement = stock.text(parsed.get('supplement') or '')
    if supplement in _POSITION_TOKENS:
        supplement = ''

    hit_mfr = mfr or page_brand
    missing = list(parsed.get('missingFields') or [])
    cand_color = stock.text(parsed.get('color') or '')
    if not cand_color and 'color' not in missing:
        missing.append('color')
    if cand_color:
        missing = [f for f in missing if f != 'color']
    current_sku_t = stock.text(current_sku) or planned_src
    site_code = stock.text(sku_info.get('siteCode') or info.get('mpn') or '')
    cleaned_name = stock.text(parsed.get('cleanedName') or parsed.get('name') or '')
    if cleaned_name and not parsed.get('cleanedName'):
        cleaned = clean_maker_product_name(cleaned_name, maker_sku=pos_base)
        cleaned_name = stock.text(cleaned.get('cleanedName') or cleaned_name)
    # 元掲載名は整理前の見出し／表品名。整理後名だけを name に載せる
    original_title = stock.text(
        parsed.get('originalTitle')
        or field_name or heading or page_title or title or parsed.get('title') or ''
    )
    maker_color_code = stock.text(
        sku_info.get('colorCode') or sku_parts.get('colorCode') or ''
    )
    name_source_label = (
        '商品情報表' if (field_name and cleaned_name) else
        ('構造化データ' if info.get('source') == 'json_ld' else '見出し')
    )
    if color_from_table:
        color_source_label = (
            '商品情報表' if (info.get('colorSource') or '').startswith('product')
            else '構造化データ'
        )
    elif cand_color and sku_info.get('match') == 'base_color':
        color_source_label = 'メーカー色番'
    elif cand_color:
        color_source_label = '掲載名'
    else:
        color_source_label = '色未取得'
    sku_source_label = sku_info.get('evidenceLabel') or '品番照合'
    field_sources = {
        'name': {
            'value': cleaned_name,
            'sourceUrl': url,
            'sourceLabel': name_source_label,
        },
        'color': {
            'value': cand_color,
            'sourceUrl': url if cand_color else '',
            'sourceLabel': color_source_label if cand_color else '色未取得',
            'unavailable': not bool(cand_color),
        },
        'sku': {
            'value': planned_sku,
            'sourceUrl': url,
            'sourceLabel': sku_source_label,
        },
    }
    # 色が空でも名が取れれば候補にはする（不足は表示用。色補正完了とはしない）
    cand = {
        'name': cleaned_name,
        'cleanedName': cleaned_name,
        'originalTitle': original_title,
        'color': cand_color,
        'size': parsed.get('size') or '',
        'position': position,
        'supplement': supplement,
        'makerSku': pos_base,
        'baseSku': stock.text(sku_info.get('baseSku') or sku_parts.get('base') or '') or pos_base,
        'makerColorCode': maker_color_code,
        'plannedRegisteredSku': planned_sku,
        'manufacturer': hit_mfr,
        'brand': page_brand,
        'siteCode': site_code if site_code and not skus_equal(site_code, pos_base) else '',
        'sourceUrl': url,
        'title': original_title,
        'preferred': bool(hit.get('preferred')) or _host_preferred(url),
        'source': hit.get('source') or 'sales_page',
        'skuMatch': sku_info.get('match'),
        'skuMatchEvidence': sku_info.get('evidenceLabel') or '',
        'missingFields': missing,
        'colorUnavailable': not bool(cand_color),
        'fieldSources': field_sources,
        'diff': {
            'nameChanged': cleaned_name != stock.text(current_name),
            'colorChanged': (
                bool(cand_color) and cand_color != stock.text(current_color)
            ),
            'supplementChanged': supplement != stock.text(current_supplement),
            'skuChanged': planned_sku != current_sku_t,
            'currentName': stock.text(current_name),
            'currentColor': stock.text(current_color),
            'currentSupplement': stock.text(current_supplement),
            'currentSku': current_sku_t,
            'plannedRegisteredSku': planned_sku,
        },
    }
    return {'candidate': cand, 'rejection': None, 'detail': None}


def _candidate_identity_key(cand: dict) -> tuple:
    return (
        maker_sku_key(cand.get('name') or ''),
        paren_classify.color_match_key(cand.get('color') or ''),
        cand.get('position') or '',
        cand.get('plannedRegisteredSku') or '',
        cand.get('supplement') or '',
        stock.text(cand.get('size') or ''),
    )


def _same_product_sku_key(cand: dict) -> tuple:
    """同一メーカー品番（上下付き登録予定）のキー。別品番の色は混ぜない。"""
    return (
        maker_sku_key(cand.get('plannedRegisteredSku') or cand.get('makerSku') or ''),
        cand.get('position') or '',
    )


def _merge_candidate_urls(existing: dict, cand: dict) -> None:
    urls = list(existing.get('sourceUrls') or [])
    src = stock.text(cand.get('sourceUrl') or '')
    if src and src not in urls:
        urls.append(src)
    existing_src = stock.text(existing.get('sourceUrl') or '')
    if existing_src and existing_src not in urls:
        urls.insert(0, existing_src)
    existing['sourceUrls'] = urls
    if not existing.get('originalTitle') and cand.get('originalTitle'):
        existing['originalTitle'] = cand.get('originalTitle')


def complement_color_from_same_product_pages(candidates: list[dict]) -> list[dict]:
    """同一メーカー品番ページ間で、名あり・色なしへ色だけを補完する。

    別品番・関連商品の色は混ぜない。配色は1色のまま（分割しない）。
    """
    if not candidates:
        return []
    # 同一品番グループ内の利用可能な色（先に色付きを優先）
    color_by_sku: dict[tuple, dict] = {}
    for cand in candidates:
        color = stock.text(cand.get('color') or '')
        if not color:
            continue
        key = _same_product_sku_key(cand)
        prev = color_by_sku.get(key)
        # preferred / 商品情報表由来を優先
        fs = (cand.get('fieldSources') or {}).get('color') or {}
        score = (
            0 if (fs.get('sourceLabel') == '商品情報表') else 1,
            0 if cand.get('preferred') else 1,
        )
        if prev is None or score < prev['score']:
            color_by_sku[key] = {
                'color': color,
                'sourceUrl': stock.text(cand.get('sourceUrl') or ''),
                'sourceLabel': stock.text(fs.get('sourceLabel') or '同一品番ページ'),
                'score': score,
                'fieldSources': cand.get('fieldSources') or {},
            }

    out = []
    for cand in candidates:
        item = dict(cand)
        color = stock.text(item.get('color') or '')
        if not color:
            donor = color_by_sku.get(_same_product_sku_key(item))
            if donor:
                item['color'] = donor['color']
                item['colorUnavailable'] = False
                miss = [f for f in (item.get('missingFields') or []) if f != 'color']
                item['missingFields'] = miss
                fs = dict(item.get('fieldSources') or {})
                fs['color'] = {
                    'value': donor['color'],
                    'sourceUrl': donor['sourceUrl'],
                    'sourceLabel': donor['sourceLabel'] or '同一品番ページ',
                    'unavailable': False,
                    'complemented': True,
                }
                item['fieldSources'] = fs
                # 色出典URLを参照に残す
                urls = list(item.get('sourceUrls') or [])
                if item.get('sourceUrl') and item['sourceUrl'] not in urls:
                    urls.insert(0, item['sourceUrl'])
                if donor['sourceUrl'] and donor['sourceUrl'] not in urls:
                    urls.append(donor['sourceUrl'])
                item['sourceUrls'] = urls
                diff = dict(item.get('diff') or {})
                current_color = stock.text(diff.get('currentColor') or '')
                diff['colorChanged'] = bool(item['color']) and item['color'] != current_color
                item['diff'] = diff
        out.append(item)

    # 補完後に名・色・品番が同じ候補をまとめる
    merged: list[dict] = []
    for cand in out:
        key = _candidate_identity_key(cand)
        found = False
        for existing in merged:
            if _candidate_identity_key(existing) != key:
                continue
            _merge_candidate_urls(existing, cand)
            # より詳しい fieldSources を残す
            if cand.get('color') and not existing.get('color'):
                existing['color'] = cand['color']
                existing['colorUnavailable'] = False
                existing['fieldSources'] = cand.get('fieldSources') or existing.get('fieldSources')
            found = True
            break
        if not found:
            item = dict(cand)
            if item.get('sourceUrl') and not item.get('sourceUrls'):
                item['sourceUrls'] = [item['sourceUrl']]
            merged.append(item)
    return merged


def _lookup_status_for_candidates(candidates: list[dict]) -> tuple[str, str, str]:
    """候補一覧から状態ラベルを決める。名のみを色補正完了扱いにしない。"""
    if not candidates:
        return 'no_match', '候補なし', ''
    if len(candidates) > 1:
        any_color = any(stock.text(c.get('color') or '') for c in candidates)
        if not any_color:
            return (
                'multiple',
                '複数候補（色は未取得）',
                '商品名候補はありますが色を取得できませんでした。色補正完了ではありません。'
                '既存・手動の色は空では上書きしません。',
            )
        return 'multiple', '複数候補', ''
    cand = candidates[0]
    if stock.text(cand.get('color') or ''):
        return 'ok', '補正候補取得できた', ''
    return (
        'ok',
        '商品名取得・色未取得',
        '商品名は取得できましたが色は取得できませんでした。色補正完了ではありません。'
        '同一品番の別ページURLを指定するか、既存・手動の色を維持してください'
        '（空の色では上書きしません）。',
    )


def _lookup_saved_for_position(c, *, manufacturer: str, base_sku: str, position: str):
    """上下を区別して保存済み補正を取る。基礎品番だけで上下を取り違えない。"""
    planned = planned_registered_sku(maker_sku=base_sku, position=position)
    # 1) 登録予定品番キー（VEH30B下）
    if planned and planned != base_sku:
        hit = lookup_saved_correction(c, manufacturer=manufacturer, maker_sku=planned)
        if hit:
            return hit, planned
    # 2) 基礎＋ adopted_json.position 一致
    if position in _POSITION_TOKENS:
        ensure_schema(c)
        mk = manufacturer_key(manufacturer)
        sk = maker_sku_key(base_sku)
        rows = []
        if mk and sk:
            rows = c.execute(
                '''SELECT * FROM maker_sku_corrections
                   WHERE manufacturer_key=? AND (
                     maker_sku_key=? OR maker_sku_key=?
                   )
                   ORDER BY id DESC''',
                (mk, sk, maker_sku_key(planned)),
            ).fetchall()
        for row in rows or []:
            extra = {}
            try:
                extra = json.loads(row['adopted_json'] or '{}')
            except (TypeError, ValueError, json.JSONDecodeError):
                extra = {}
            row_pos = stock.text(extra.get('position') or '')
            if not row_pos:
                # 旧保存: supplement に上下
                supp = stock.text(extra.get('supplement') or '')
                if supp in _POSITION_TOKENS:
                    row_pos = supp
            saved_sku = stock.text(row['maker_sku'] or '')
            _b, saved_pos = position_sku.strip_sku_position_suffix(saved_sku)
            if not row_pos:
                row_pos = saved_pos
            if row_pos == position:
                return dict(row), planned
        return None, planned
    # 3) 区分なし → 基礎キー（区分付き保存は使わない）
    hit = lookup_saved_correction(c, manufacturer=manufacturer, maker_sku=base_sku)
    if hit:
        extra = {}
        try:
            extra = json.loads(hit.get('adopted_json') or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            extra = {}
        row_pos = stock.text(extra.get('position') or '')
        if not row_pos:
            supp = stock.text(extra.get('supplement') or '')
            if supp in _POSITION_TOKENS:
                row_pos = supp
        _b, saved_pos = position_sku.strip_sku_position_suffix(hit.get('maker_sku') or '')
        if not row_pos:
            row_pos = saved_pos
        # 基礎検索時に上下付き保存を誤適用しない
        if row_pos in _POSITION_TOKENS:
            return None, base_sku
    return hit, base_sku


def search_maker_product(
    c, *,
    manufacturer: str,
    maker_sku: str,
    current_name: str = '',
    current_color: str = '',
    current_supplement: str = '',
    current_sku: str = '',
    source_name: str = '',
    supplier: str = '',
) -> dict:
    """メーカー＋メーカー品番（＋旧名ヒント）で候補を無料取得する。

    status:
      ok / multiple / fetch_failed / page_unavailable /
      sku_mismatch / parse_incomplete / no_match / invalid_sku
    """
    ensure_schema(c)
    raw_sku = stock.text(maker_sku)
    sku_norm = normalize_search_maker_sku(raw_sku)
    source_sku = sku_norm.get('sourceSku') or raw_sku
    usage_note = sku_norm.get('usageNote') or ''
    pos_from_sku = sku_norm.get('position') or ''
    sku = sku_norm.get('baseSku') or sku_norm.get('searchSku') or raw_sku
    supplier_t = stock.text(supplier)
    mfr = resolve_manufacturer(manufacturer=manufacturer, supplier=supplier_t)
    # 旧商品名を検索補助の主とし、予定名は補助
    hint_source = stock.text(source_name) or stock.text(current_name)
    hints = extract_name_search_hints(hint_source)
    if pos_from_sku and pos_from_sku not in (hints.get('positions') or []):
        hints = dict(hints)
        hints['positions'] = [pos_from_sku] + list(hints.get('positions') or [])
    hint_positions = list(hints.get('positions') or [])
    position = hint_positions[0] if len(hint_positions) == 1 else (pos_from_sku or '')
    # 現在の登録予定は呼び出し側の値を優先（注記分離だけで書き換えない）
    current_sku_t = (
        stock.text(current_sku)
        or planned_registered_sku(maker_sku=sku, position=position, source_code=raw_sku)
    )

    def _sku_meta_fields():
        return {
            'makerSku': sku,
            'searchMakerSku': sku,
            'sourceSku': source_sku,
            'usageNote': usage_note,
        }

    if not is_valid_maker_sku(sku):
        return {
            'status': 'invalid_sku',
            'statusLabel': 'メーカー品番が対象外です',
            **_sku_meta_fields(),
            'plannedRegisteredSku': current_sku_t,
            'manufacturer': mfr,
            'candidates': [],
            'saved': None,
            'referenceUrl': reference_search_url(manufacturer=mfr, maker_sku=sku, hints=hints),
            'note': '別注・品番なし・空の品番では補正できません。',
            'supplierIgnored': supplier_t,
            'searchHints': hints,
            'queriesTried': [],
            'matchedPages': [],
        }

    saved, saved_key = _lookup_saved_for_position(
        c, manufacturer=mfr, base_sku=sku, position=position,
    )
    saved_payload = None
    if saved:
        extra = {}
        try:
            extra = json.loads(saved.get('adopted_json') or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            extra = {}
        saved_pos = stock.text(extra.get('position') or '')
        saved_supp = stock.text(extra.get('supplement') or '')
        if not saved_pos and saved_supp in _POSITION_TOKENS:
            saved_pos = saved_supp
            saved_supp = ''
        _sb, _sp = position_sku.strip_sku_position_suffix(saved.get('maker_sku') or '')
        if not saved_pos:
            saved_pos = _sp
        planned = planned_registered_sku(
            maker_sku=sku, position=saved_pos or position, source_code=saved.get('maker_sku') or '',
        )
        saved_payload = {
            'manufacturer': saved.get('manufacturer') or mfr,
            'makerSku': sku,
            'plannedRegisteredSku': planned,
            'position': saved_pos,
            'name': saved.get('adopted_name') or '',
            'color': saved.get('adopted_color') or '',
            'size': stock.text(extra.get('size') or ''),
            'supplement': saved_supp if saved_supp not in _POSITION_TOKENS else '',
            'sourceUrl': saved.get('source_url') or '',
            'originalTitle': stock.text(extra.get('originalTitle') or ''),
            'sourceSku': stock.text(extra.get('sourceSku') or source_sku),
            'usageNote': stock.text(extra.get('usageNote') or usage_note),
            'manuallyConfirmed': bool(int(saved.get('manually_confirmed') or 0)),
            'fromSaved': True,
            'savedKey': saved_key,
        }

    reference = reference_search_url(manufacturer=mfr, maker_sku=sku, hints=hints)
    if mfr == 'ミドリ安全' or manufacturer_hint_from_supplier(supplier_t) == 'ミドリ安全':
        reference = midori_catalog_search_url(sku)
    elif mfr == 'KAZEN' or manufacturer_hint_from_supplier(supplier_t) == 'KAZEN':
        reference = kazen_catalog_search_url(sku)
    elif is_montblanc_manufacturer(mfr) or is_montblanc_manufacturer(
        manufacturer_hint_from_supplier(supplier_t)
    ):
        reference = unifice_catalog_search_url(sku)
    elif (
        mfr == 'アイトス'
        or manufacturer_hint_from_supplier(supplier_t) == 'アイトス'
        or 'AITOZ' in paren_classify.unify_import_notation(mfr).upper()
    ):
        direct = _aitoz_direct_candidates(sku, manufacturer='アイトス')
        if direct:
            reference = direct[0]['url']

    stages = build_search_query_stages(manufacturer=mfr, maker_sku=sku, hints=hints)
    raw_hits = []
    queries_tried = []
    search_errors = 0
    page_errors = 0
    sku_mismatches = 0
    maker_mismatches = 0
    parse_incomplete_pages = []
    seen_urls = set()

    for query in stages:
        queries_tried.append(query)
        # 1) メーカー公式カタログ（無料）
        catalog_added = 0
        try:
            for hit in _catalog_searches_for(
                manufacturer=mfr, query=query, maker_sku=sku,
            ):
                u = hit.get('url') or ''
                if u in seen_urls:
                    continue
                seen_urls.add(u)
                raw_hits.append(hit)
                catalog_added += 1
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            search_errors += 1
        # カタログで十分な候補があれば DDG を足さない（関連・販促ノイズを混入させない）
        # ※色がカタログに無い場合は後段で同一品番の補完検索を行う
        has_catalog_hit = any(
            hit.get('source') in ('midori_ec', 'kazen_ec', 'unifice', 'aitoz_direct')
            for hit in raw_hits
        )
        if not has_catalog_hit:
            # 2) DuckDuckGo HTML（無料・ボット壁で空になり得る）
            try:
                for hit in _ddg_candidates(query, limit=6):
                    u = hit.get('url') or ''
                    if u in seen_urls:
                        continue
                    seen_urls.add(u)
                    raw_hits.append(hit)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
                search_errors += 1
        # カタログで品番一致候補が既にあれば打ち切り（無駄な段階を増やさない）
        if has_catalog_hit and (
            catalog_added
            or any(
                hit.get('source') in ('midori_ec', 'kazen_ec', 'unifice', 'aitoz_direct')
                and (
                    sku_match_key(sku) in sku_match_key(hit.get('title') or '')
                    or hit.get('source') in ('unifice', 'aitoz_direct')
                )
                for hit in raw_hits
            )
        ):
            break

    sk_key = sku_match_key(sku)
    stem_key = sku_match_key(_model_stem(sku) or '')
    sku_parts = split_maker_sku_parts(sku, manufacturer=mfr)
    base_key = (
        aitoz_base_match_key(sku_parts.get('base') or '')
        if sku_parts.get('rule') == 'aitoz' else ''
    )
    mfr_u = paren_classify.unify_import_notation(mfr).upper()

    def _hit_rank(h):
        title_k = sku_match_key(h.get('title') or '')
        url_k = sku_match_key(h.get('url') or '')
        url = (h.get('url') or '').lower()
        exact = 0 if (sk_key and (sk_key in title_k or sk_key in url_k)) else 1
        if exact and base_key and (
            base_key in title_k.replace('-', '') or base_key in url_k.replace('-', '')
        ):
            exact = 0
        stem_hit = 0 if (stem_key and stem_key in title_k) else 1
        # メーカー公式ホストを仕入先カタログのノイズより優先
        host_boost = 1
        if 'KAZEN' in mfr_u and 'kazen-ec' in url:
            host_boost = 0
        elif 'ミドリ安全' in mfr and 'midori-anzen' in url:
            host_boost = 0
        elif is_montblanc_manufacturer(mfr) and 'unifice' in url:
            host_boost = 0
        elif h.get('source') == 'unifice':
            host_boost = 0
        elif ('アイトス' in mfr or 'AITOZ' in mfr_u) and 'aitoz' in url:
            host_boost = 0
        elif h.get('source') == 'aitoz_direct':
            host_boost = 0
        pref = 0 if h.get('preferred') else 1
        # タイトル/URLに品番 → 公式ホスト → 茎一致 → preferred
        return (exact, host_boost, stem_hit, pref)

    raw_hits = sorted(raw_hits, key=_hit_rank)

    live_candidates = []
    # 品番一致ページを逃さないよう、優先順位付けしたうえで多めに見る
    for hit in raw_hits[:18]:
        try:
            built = _build_candidate_from_hit(
                hit,
                maker_sku=sku,
                manufacturer=mfr,
                hints=hints,
                current_name=current_name,
                current_color=current_color,
                current_supplement=current_supplement,
                current_sku=current_sku_t,
            )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            page_errors += 1
            continue
        rejection = built.get('rejection')
        cand = built.get('candidate')
        detail = built.get('detail')
        if rejection == 'page_unavailable':
            page_errors += 1
            continue
        if rejection == 'sku_mismatch':
            sku_mismatches += 1
            continue
        if rejection == 'maker_mismatch':
            maker_mismatches += 1
            continue
        if rejection == 'parse_incomplete':
            if detail:
                parse_incomplete_pages.append(detail)
            continue
        if rejection == 'hint_reject' or not cand:
            continue
        # 整理後名・色・品番が同じ候補は出典URLを残してまとめる（名一致だけでは色違いを統合しない）
        key = _candidate_identity_key(cand)
        merged = False
        for existing in live_candidates:
            if _candidate_identity_key(existing) != key:
                continue
            _merge_candidate_urls(existing, cand)
            merged = True
            break
        if merged:
            continue
        cand = dict(cand)
        if cand.get('sourceUrl'):
            cand['sourceUrls'] = [cand['sourceUrl']]
        else:
            cand['sourceUrls'] = []
        live_candidates.append(cand)

    # カタログで名は取れたが色が無いとき、同一品番の公開ページから色だけ補完する
    needs_color_complement = any(
        stock.text(c.get('name') or '') and not stock.text(c.get('color') or '')
        for c in live_candidates
    ) and not any(
        stock.text(c.get('name') or '') and stock.text(c.get('color') or '')
        for c in live_candidates
    )
    if needs_color_complement:
        complement_query = ' '.join(
            p for p in (stock.text(mfr), stock.text(sku)) if p
        ) or stock.text(sku)
        try:
            for hit in _ddg_candidates(complement_query, limit=8):
                u = hit.get('url') or ''
                if not u or u in seen_urls:
                    continue
                seen_urls.add(u)
                raw_hits.append(hit)
                try:
                    built = _build_candidate_from_hit(
                        hit,
                        maker_sku=sku,
                        manufacturer=mfr,
                        hints=hints,
                        current_name=current_name,
                        current_color=current_color,
                        current_supplement=current_supplement,
                        current_sku=current_sku_t,
                    )
                except (
                    urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
                ):
                    page_errors += 1
                    continue
                if built.get('rejection') or not built.get('candidate'):
                    if built.get('rejection') == 'page_unavailable':
                        page_errors += 1
                    elif built.get('rejection') == 'sku_mismatch':
                        sku_mismatches += 1
                    continue
                cand = dict(built['candidate'])
                # 色補完用途: 色が取れた同一品番ページだけ追加
                if not stock.text(cand.get('color') or ''):
                    continue
                key = _candidate_identity_key(cand)
                merged = False
                for existing in live_candidates:
                    if _candidate_identity_key(existing) != key:
                        continue
                    _merge_candidate_urls(existing, cand)
                    merged = True
                    break
                if merged:
                    continue
                if cand.get('sourceUrl'):
                    cand['sourceUrls'] = [cand['sourceUrl']]
                else:
                    cand['sourceUrls'] = []
                live_candidates.append(cand)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            search_errors += 1

    live_candidates = complement_color_from_same_product_pages(live_candidates)

    # 名・色が揃った公式寄りの候補を先に見せる（自動採用はしない）
    live_candidates.sort(key=lambda c: (
        0 if (c.get('name') and c.get('color')) else 1,
        0 if c.get('preferred') else 1,
        0 if c.get('skuMatch') in ('structured', 'base_color') else 1,
        0 if c.get('skuMatch') == 'base_only' else 1,
    ))

    def _with_saved(cands):
        if not (saved_payload and saved_payload.get('name')):
            return list(cands)
        planned = saved_payload.get('plannedRegisteredSku') or current_sku_t
        saved_name = saved_payload['name']
        saved_original = stock.text(saved_payload.get('originalTitle') or saved_name)
        front = {
            'name': saved_name,
            'cleanedName': saved_name,
            'originalTitle': saved_original,
            'color': saved_payload.get('color') or '',
            'size': saved_payload.get('size') or '',
            'position': saved_payload.get('position') or position,
            'supplement': saved_payload.get('supplement') or '',
            'makerSku': sku,
            'plannedRegisteredSku': planned,
            'manufacturer': saved_payload.get('manufacturer') or mfr,
            'sourceUrl': saved_payload.get('sourceUrl') or '',
            'sourceUrls': (
                [saved_payload.get('sourceUrl')] if saved_payload.get('sourceUrl') else []
            ),
            'title': saved_original,
            'preferred': True,
            'source': 'saved',
            'fromSaved': True,
            'missingFields': [],
            'diff': {
                'nameChanged': stock.text(saved_name) != stock.text(current_name),
                'colorChanged': stock.text(saved_payload.get('color') or '') != stock.text(current_color),
                'supplementChanged': (
                    stock.text(saved_payload.get('supplement') or '')
                    != stock.text(current_supplement)
                ),
                'skuChanged': stock.text(planned) != stock.text(current_sku_t),
                'currentName': stock.text(current_name),
                'currentColor': stock.text(current_color),
                'currentSupplement': stock.text(current_supplement),
                'currentSku': stock.text(current_sku_t),
                'plannedRegisteredSku': planned,
            },
        }
        return [front] + [
            x for x in cands
            if not (
                maker_sku_key(x.get('name')) == maker_sku_key(front['name'])
                and paren_classify.color_match_key(x.get('color') or '')
                == paren_classify.color_match_key(front.get('color') or '')
                and stock.text(x.get('plannedRegisteredSku')) == stock.text(front.get('plannedRegisteredSku'))
            )
        ]

    if not live_candidates:
        if search_errors and not raw_hits:
            status, label = 'fetch_failed', '検索結果取得不可'
            note = (
                '無料の公開検索へ届きませんでした。参考ページを開いて確認できます'
                '（検索ページを開いただけでは補正完了になりません）。'
            )
        elif raw_hits and page_errors >= max(1, min(len(raw_hits), 10)) and not parse_incomplete_pages:
            status, label = 'page_unavailable', '商品ページ取得不可'
            note = '検索ヒットはありましたが商品ページを取得できませんでした。'
        elif page_errors and not raw_hits:
            status, label = 'page_unavailable', '商品ページ取得不可'
            note = '候補ページを取得できませんでした。参考ページを開いて確認できます。'
        elif parse_incomplete_pages and not sku_mismatches and page_errors == 0:
            status, label = 'parse_incomplete', '品番一致したが項目抽出不可'
            miss = sorted({
                f
                for p in parse_incomplete_pages
                for f in (p.get('missingFields') or [])
            })
            note = (
                '商品コード等で品番は一致しましたが、商品名・色を抽出できませんでした。'
                + (f' 不足項目: {"、".join(miss)}。' if miss else '')
                + '参照URLを確認してください。'
            )
        elif parse_incomplete_pages:
            status, label = 'parse_incomplete', '品番一致したが項目抽出不可'
            miss = sorted({
                f
                for p in parse_incomplete_pages
                for f in (p.get('missingFields') or [])
            })
            note = (
                '品番一致ページはありますが、名・色の抽出に失敗しました。'
                + (f' 不足項目: {"、".join(miss)}。' if miss else '')
            )
        elif maker_mismatches and not sku_mismatches and not parse_incomplete_pages:
            status, label = 'maker_mismatch', 'メーカー不一致'
            note = (
                '品番は近いページがありましたが、ブランド／メーカーが指定と一致しませんでした。'
                '商品ページURLを指定して再取得できます。'
            )
        elif sku_mismatches and raw_hits:
            status, label = 'sku_mismatch', '品番不一致'
            note = (
                '検索でページは見つかりましたが、商品コード／見出しの完全品番が一致しませんでした。'
                '関連商品やナビ内の品番だけの一致は候補にしません。'
                '見つかった公開ページのURLを指定して再取得できます。'
            )
        else:
            status, label = 'no_match', '候補なし'
            note = (
                'メーカー品番と旧商品名ヒントに一致する商品情報を取れませんでした。'
                '参考ページを開くか、商品ページURLを指定して取得できます'
                '（開いただけ・URL入力だけでは補正完了になりません）。'
            )
        # 通信失敗時は保存済みを candidates に混ぜず saved のみ返す（状態を曖昧にしない）
        return {
            'status': status,
            'statusLabel': label,
            **_sku_meta_fields(),
            'plannedRegisteredSku': current_sku_t,
            'manufacturer': mfr,
            'candidates': [],
            'saved': saved_payload,
            'referenceUrl': reference,
            'note': note,
            'supplierIgnored': supplier_t,
            'searchHints': hints,
            'queriesTried': queries_tried,
            'matchedPages': parse_incomplete_pages,
        }

    candidates = _with_saved(live_candidates)
    # 保存済みを先頭に足したあとでも同一品番の色補完を再適用
    candidates = complement_color_from_same_product_pages(candidates)
    status, status_label, status_note = _lookup_status_for_candidates(
        [c for c in candidates if not c.get('fromSaved')] or candidates
    )

    return {
        'status': status,
        'statusLabel': status_label,
        **_sku_meta_fields(),
        'plannedRegisteredSku': (
            (candidates[0].get('plannedRegisteredSku') if candidates else '')
            or current_sku_t
        ),
        'manufacturer': mfr or (saved_payload or {}).get('manufacturer') or '',
        'candidates': candidates,
        'saved': saved_payload,
        'referenceUrl': reference,
        'note': (
            (status_note + ' ' if status_note else '')
            + '採用するまで登録予定は変わりません。'
            '仕入先はメーカーではないため、ブランド名が含まれる場合のみメーカーヒントに使います。'
            '上下区分がある場合は登録予定品番末尾へ連結します（例: VEH30B下）。'
            '取得サイズは照合・表示用で、旧CSVのサイズ割当は上書きしません。'
            + (
                f' 元品番の注記「{usage_note}」は検索から外し、照合用に保持します。'
                if usage_note else ''
            )
        ),
        'supplierIgnored': supplier_t,
        'searchHints': hints,
        'queriesTried': queries_tried,
        'matchedPages': [],
    }


def validate_public_http_url(url: str) -> dict:
    """公開 HTTP/HTTPS のみ許可。内部NW・localhost・file 等は拒否。"""
    raw = stock.text(url) or ''
    if not raw:
        return {'ok': False, 'reason': 'URLが空です。', 'normalized': ''}
    parsed = urllib.parse.urlparse(raw)
    scheme = (parsed.scheme or '').lower()
    if scheme not in ('http', 'https'):
        return {
            'ok': False,
            'reason': '公開の HTTP/HTTPS URL のみ指定できます（file や内部スキームは不可）。',
            'normalized': '',
        }
    host = (parsed.hostname or '').strip().lower()
    if not host:
        return {'ok': False, 'reason': 'ホスト名がありません。', 'normalized': ''}
    if _PRIVATE_HOST_RE.match(host) or host.endswith('.local') or host.endswith('.internal'):
        return {
            'ok': False,
            'reason': '内部ネットワークやローカルホストの URL は使えません。',
            'normalized': '',
        }
    # IPv6 ブラケットやユーザー情報付きは拒否（単純な公開URLに限定）
    if parsed.username or parsed.password:
        return {
            'ok': False,
            'reason': '認証情報付き URL は使えません。',
            'normalized': '',
        }
    normalized = urllib.parse.urlunparse((
        scheme,
        parsed.netloc,
        parsed.path or '',
        parsed.params,
        parsed.query,
        '',  # fragment は送らない
    ))
    return {'ok': True, 'reason': '', 'normalized': normalized}


def lookup_maker_product_from_url(
    c, *,
    url: str,
    manufacturer: str,
    maker_sku: str,
    current_name: str = '',
    current_color: str = '',
    current_supplement: str = '',
    current_sku: str = '',
    source_name: str = '',
    supplier: str = '',
) -> dict:
    """利用者が指定した商品ページURLから、検索と同じ照合・抽出で候補を返す。

    入力だけでは登録予定を変えない（採用は別操作）。
    """
    ensure_schema(c)
    raw_sku = stock.text(maker_sku)
    sku_norm = normalize_search_maker_sku(raw_sku)
    source_sku = sku_norm.get('sourceSku') or raw_sku
    usage_note = sku_norm.get('usageNote') or ''
    pos_from_sku = sku_norm.get('position') or ''
    sku = sku_norm.get('baseSku') or sku_norm.get('searchSku') or raw_sku
    supplier_t = stock.text(supplier)
    mfr = resolve_manufacturer(manufacturer=manufacturer, supplier=supplier_t)
    hint_source = stock.text(source_name) or stock.text(current_name)
    hints = extract_name_search_hints(hint_source)
    if pos_from_sku and pos_from_sku not in (hints.get('positions') or []):
        hints = dict(hints)
        hints['positions'] = [pos_from_sku] + list(hints.get('positions') or [])
    hint_positions = list(hints.get('positions') or [])
    position = hint_positions[0] if len(hint_positions) == 1 else (pos_from_sku or '')
    current_sku_t = (
        stock.text(current_sku)
        or planned_registered_sku(maker_sku=sku, position=position, source_code=raw_sku)
    )

    def _base(status, label, note, **extra):
        return {
            'status': status,
            'statusLabel': label,
            'makerSku': sku,
            'searchMakerSku': sku,
            'sourceSku': source_sku,
            'usageNote': usage_note,
            'plannedRegisteredSku': current_sku_t,
            'manufacturer': mfr,
            'candidates': [],
            'saved': None,
            'referenceUrl': stock.text(url) or '',
            'note': note,
            'supplierIgnored': supplier_t,
            'searchHints': hints,
            'queriesTried': [],
            'matchedPages': [],
            'fromUrl': True,
            'sourceUrl': stock.text(url) or '',
            **extra,
        }

    if not is_valid_maker_sku(sku):
        return _base(
            'invalid_sku', 'メーカー品番が対象外です',
            '別注・品番なし・空の品番では補正できません。',
        )

    checked = validate_public_http_url(url)
    if not checked.get('ok'):
        return _base(
            'invalid_url', 'URLが対象外',
            checked.get('reason') or '公開の HTTP/HTTPS URL のみ指定できます。',
        )
    normalized = checked['normalized']

    try:
        final_url, page_html = _http_get(normalized)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return _base(
            'page_unavailable', '商品ページ取得不可',
            f'指定URLのページを取得できませんでした（{type(e).__name__}）。',
            sourceUrl=normalized,
            referenceUrl=normalized,
        )

    hit = {
        'title': _extract_title(page_html) or '',
        'url': final_url or normalized,
        'source': 'url_specified',
        'preferred': _host_preferred(final_url or normalized),
        'manufacturer': mfr,
        'pageHtml': page_html,
    }
    built = _build_candidate_from_hit(
        hit,
        maker_sku=sku,
        manufacturer=mfr,
        hints=hints,
        current_name=current_name,
        current_color=current_color,
        current_supplement=current_supplement,
        current_sku=current_sku_t,
        require_manufacturer_match=bool(mfr),
    )
    rejection = built.get('rejection')
    cand = built.get('candidate')
    detail = built.get('detail') or {}

    if rejection == 'page_unavailable':
        return _base(
            'page_unavailable', '商品ページ取得不可',
            '指定URLのページを取得できませんでした。',
            sourceUrl=normalized, referenceUrl=normalized,
        )
    if rejection == 'sku_mismatch':
        return _base(
            'sku_mismatch', '品番不一致',
            detail.get('note') or (
                '指定ページにメーカー品番の完全一致がありません。'
                '関連商品・色違いリンクだけの一致は採用できません。'
            ),
            sourceUrl=final_url or normalized,
            referenceUrl=final_url or normalized,
            matchedPages=[detail] if detail else [],
        )
    if rejection == 'maker_mismatch':
        return _base(
            'maker_mismatch', 'メーカー不一致',
            detail.get('note') or '指定ページのブランドがメーカーと一致しません。',
            sourceUrl=final_url or normalized,
            referenceUrl=final_url or normalized,
            matchedPages=[detail] if detail else [],
        )
    if rejection == 'parse_incomplete':
        return _base(
            'parse_incomplete', '品番一致したが項目抽出不可',
            '品番は一致しましたが商品名・色を抽出できませんでした。',
            sourceUrl=final_url or normalized,
            referenceUrl=final_url or normalized,
            matchedPages=[detail] if detail else [],
        )
    if rejection == 'hint_reject' or not cand:
        return _base(
            'no_match', '候補なし',
            '指定ページは取得できましたが、上下・色のヒントと一致する候補になりませんでした。',
            sourceUrl=final_url or normalized,
            referenceUrl=final_url or normalized,
        )

    cand = dict(cand)
    if cand.get('sourceUrl'):
        cand['sourceUrls'] = [cand['sourceUrl']]
    else:
        cand['sourceUrls'] = []
    live = [cand]

    # 指定ページで名・色のどちらかが欠けていれば、同一メーカー品番のカタログページから補完
    need_name = not stock.text(cand.get('name') or '')
    need_color = not stock.text(cand.get('color') or '')
    if need_color or need_name:
        try:
            for hit in _catalog_searches_for(
                manufacturer=mfr, query=sku, maker_sku=sku,
            ):
                u = hit.get('url') or ''
                if not u or u == (final_url or normalized):
                    continue
                built2 = _build_candidate_from_hit(
                    hit,
                    maker_sku=sku,
                    manufacturer=mfr,
                    hints=hints,
                    current_name=current_name,
                    current_color=current_color,
                    current_supplement=current_supplement,
                    current_sku=current_sku_t,
                )
                other = built2.get('candidate')
                if built2.get('rejection') or not other:
                    continue
                # 同一品番のみ（別製品ページは除外済み）
                live.append(dict(other))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            pass
        live = complement_color_from_same_product_pages(live)
        # 指定URLの候補を先頭に保ちつつ、補完後の最良を選ぶ
        live.sort(key=lambda c: (
            0 if (c.get('sourceUrl') == (final_url or normalized)) else 1,
            0 if (c.get('name') and c.get('color')) else 1,
            0 if c.get('preferred') else 1,
        ))
        # 同一品番で名・色が揃った1件へまとめる（別色は残す）
        primary = None
        for c in live:
            if stock.text(c.get('name') or '') and stock.text(c.get('color') or ''):
                primary = c
                break
        if primary is None:
            primary = live[0] if live else cand
        live = [primary]

    status, status_label, status_note = _lookup_status_for_candidates(live)
    primary_cand = live[0]
    return {
        'status': status,
        'statusLabel': status_label,
        'makerSku': sku,
        'searchMakerSku': sku,
        'sourceSku': source_sku,
        'usageNote': usage_note,
        'plannedRegisteredSku': primary_cand.get('plannedRegisteredSku') or current_sku_t,
        'manufacturer': mfr or primary_cand.get('manufacturer') or '',
        'candidates': live,
        'saved': None,
        'referenceUrl': primary_cand.get('sourceUrl') or final_url or normalized,
        'note': (
            (status_note + ' ' if status_note else '')
            + 'URL指定で取得しました。採用するまで登録予定は変わりません。'
            '検索由来と同じ照合・抽出です。'
            + (
                f' 元品番の注記「{usage_note}」は検索から外し、照合用に保持します。'
                if usage_note else ''
            )
        ),
        'supplierIgnored': supplier_t,
        'searchHints': hints,
        'queriesTried': [],
        'matchedPages': [],
        'fromUrl': True,
        'sourceUrl': primary_cand.get('sourceUrl') or final_url or normalized,
    }


def adopt_maker_correction(
    c, *,
    manufacturer: str,
    maker_sku: str,
    name: str,
    color: str = '',
    supplement: str = '',
    position: str = '',
    planned_registered_sku: str = '',
    source_url: str = '',
    source_sku: str = '',
    usage_note: str = '',
    original_title: str = '',
    maker_color_code: str = '',
    base_sku_override: str = '',
    current_color: str = '',
) -> dict:
    raw_sku = stock.text(maker_sku)
    sku_norm = normalize_search_maker_sku(raw_sku)
    base_sku = sku_norm.get('baseSku') or sku_norm.get('searchSku') or raw_sku
    pos_from_sku = sku_norm.get('position') or ''
    source_sku_t = stock.text(source_sku) or sku_norm.get('sourceSku') or raw_sku
    usage_note_t = stock.text(usage_note) or sku_norm.get('usageNote') or ''
    if not is_valid_maker_sku(base_sku):
        raise ValueError('メーカー品番が対象外です。')
    if not stock.text(name):
        raise ValueError('採用する商品名が空です。')
    pos = stock.text(position) or pos_from_sku
    if pos not in _POSITION_TOKENS:
        pos = ''
    # 補足に残った上下は品番側へ移し、補足へは重複しない
    supp = stock.text(supplement)
    if not pos and supp in _POSITION_TOKENS:
        pos = supp
        supp = ''
    elif supp in _POSITION_TOKENS:
        supp = ''
    planned = stock.text(planned_registered_sku)
    if not planned:
        planned = position_sku.attach_sku_position_suffix(base_sku, pos) if pos else base_sku
    parts = split_maker_sku_parts(
        planned or base_sku, manufacturer=manufacturer, page_url=source_url,
    )
    product_base = (
        stock.text(base_sku_override)
        or stock.text(parts.get('base') or '')
        or base_sku
    )
    color_code = stock.text(maker_color_code) or stock.text(parts.get('colorCode') or '')
    # 保存キーは上下付き／色番付きの登録予定品番（色違い・上下を別補正として保持）
    save_sku = planned if pos else base_sku
    if not pos and planned and planned != base_sku:
        save_sku = planned
    cleaned = clean_maker_product_name(name, maker_sku=base_sku)
    adopted_name = cleaned.get('cleanedName') or stock.text(name)
    # 空の色で既存・手動色を消さない
    adopt_color = stock.text(color) or ''
    if not adopt_color:
        existing_row = lookup_saved_correction(
            c, manufacturer=manufacturer, maker_sku=save_sku,
        )
        if not existing_row and save_sku != base_sku:
            existing_row = lookup_saved_correction(
                c, manufacturer=manufacturer, maker_sku=base_sku,
            )
        keep = stock.text((existing_row or {}).get('adopted_color') or '') or stock.text(
            current_color
        )
        if keep:
            adopt_color = keep
    row, changed = save_adopted_correction(
        c,
        manufacturer=manufacturer,
        maker_sku=save_sku,
        adopted_name=adopted_name,
        adopted_color=adopt_color,
        source_url=source_url,
        adopted_extra={
            'supplement': supp,
            'position': pos,
            'baseMakerSku': product_base,
            'plannedRegisteredSku': planned,
            'makerColorCode': color_code,
            'fullMakerSku': planned or base_sku,
            'sourceSku': source_sku_t,
            'usageNote': usage_note_t,
            'searchMakerSku': base_sku,
            'originalTitle': stock.text(original_title) or '',
            'cleanedName': adopted_name,
        },
        overwrite_manual=True,
    )
    extra = {}
    try:
        extra = json.loads(row.get('adopted_json') or '{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        extra = {}
    return {
        'saved': True,
        'changed': changed,
        'manufacturer': row.get('manufacturer') or manufacturer,
        'makerSku': base_sku,
        'baseSku': stock.text(extra.get('baseMakerSku') or product_base),
        'makerColorCode': stock.text(extra.get('makerColorCode') or color_code),
        'searchMakerSku': base_sku,
        'sourceSku': stock.text(extra.get('sourceSku') or source_sku_t),
        'usageNote': stock.text(extra.get('usageNote') or usage_note_t),
        'plannedRegisteredSku': stock.text(extra.get('plannedRegisteredSku') or planned),
        'position': stock.text(extra.get('position') or pos),
        'name': row.get('adopted_name') or adopted_name,
        'cleanedName': stock.text(extra.get('cleanedName') or adopted_name),
        'originalTitle': stock.text(extra.get('originalTitle') or ''),
        'color': row.get('adopted_color') or adopt_color,
        'colorUnavailable': not bool(stock.text(row.get('adopted_color') or adopt_color)),
        'supplement': stock.text(extra.get('supplement') or supp),
        'sourceUrl': row.get('source_url') or source_url,
    }
