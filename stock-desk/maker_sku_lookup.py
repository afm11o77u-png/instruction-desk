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
    'kazen-ec', 'kazen',
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


def skus_equal(a: str, b: str) -> bool:
    ka, kb = sku_match_key(a), sku_match_key(b)
    return bool(ka) and ka == kb


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


def effective_maker_sku_from_row(row: dict) -> str:
    """検索用のメーカー品番（上下区分を除いた基礎品番）。"""
    prod = row.get('product') if isinstance(row.get('product'), dict) else {}
    csv = row.get('csv') if isinstance(row.get('csv'), dict) else {}
    explicit = stock.text(
        prod.get('makerCode') or csv.get('makerCode') or csv.get('baseCode') or ''
    )
    if explicit:
        base, _pos = position_sku.strip_sku_position_suffix(explicit)
        code = base or explicit
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
    base, _pos = position_sku.strip_sku_position_suffix(code)
    code = base or code
    if not stock_product_merge.is_normal_product_code(code):
        return ''
    return code if is_valid_maker_sku(code) else ''


def planned_registered_sku(*, maker_sku: str, position: str = '', source_code: str = '') -> str:
    """登録予定品番（上下を末尾密着）。"""
    raw = stock.text(source_code) or stock.text(maker_sku)
    base, pos_from_code = position_sku.strip_sku_position_suffix(raw)
    pos = stock.text(position) or pos_from_code
    base = base or stock.text(maker_sku) or raw
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
    csv = row.get('csv') if isinstance(row.get('csv'), dict) else {}
    existing = row.get('makerCorrection') if isinstance(row.get('makerCorrection'), dict) else {}
    mfr = stock.text(existing.get('manufacturer') or '') or resolve_manufacturer(
        manufacturer='',
        supplier=csv.get('supplier') or '',
    )
    return {
        'eligible': bool(sku) and is_valid_maker_sku(sku),
        'makerSku': sku,
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
    """段階的な検索語。基礎品番＋上下の表記差（スペース／密着）を試す。"""
    raw_sku = stock.text(maker_sku)
    base, pos_from_sku = position_sku.strip_sku_position_suffix(raw_sku)
    sku = base or raw_sku
    mfr = stock.text(manufacturer)
    positions = list(hints.get('positions') or [])
    if pos_from_sku and pos_from_sku not in positions:
        positions = [pos_from_sku] + positions
    colors = list(hints.get('colors') or [])
    stages = []

    def add(parts):
        q = ' '.join(p for p in parts if p)
        if q and q not in stages:
            stages.append(q)

    add([mfr, sku])
    add([sku])
    mfr_u = paren_classify.unify_import_notation(mfr).upper()
    if 'KAZEN' in mfr_u or 'カゼン' in (mfr or ''):
        add([sku, 'site:kazen-ec.com'])
        add([mfr, sku, 'site:kazen-ec.com'])
        add(['KAZEN', sku])
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
        key = sku_match_key(v)
        if not key or key in seen:
            return
        seen.add(key)
        found.append(v)

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
        # 行全体がラベル、次行が値（KAZEN公式）
        if compact in labels and i + 1 < len(lines):
            add(lines[i + 1])
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


def page_sku_match(html: str, title: str, maker_sku: str) -> dict:
    """品番一致の根拠を返す。

    match: structured / title / none
    本文にたまたま含まれるだけ・関連リンク内だけの一致は none。
    """
    sk = sku_match_key(maker_sku)
    empty = {
        'match': 'none', 'matchedValue': '', 'evidence': '',
    }
    if not sk:
        return empty

    for val in extract_structured_sku_values(html or ''):
        if skus_equal(val, maker_sku):
            return {
                'match': 'structured',
                'matchedValue': val,
                'evidence': 'product_code_field',
            }

    title_u = paren_classify.unify_import_notation(stock.text(title) or '')
    h1 = _extract_h1(html or '')
    for label, text in (('title', title_u), ('h1', h1), ('og_or_title', _extract_title(html or ''))):
        if not text:
            continue
        if _text_contains_full_sku(text, maker_sku):
            return {
                'match': 'title',
                'matchedValue': maker_sku,
                'evidence': label,
            }
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

    # 見出しに混ざるメーカー／販社トークンは商品名から外す
    _NAME_NOISE = {
        'KAZEN', 'カゼン', 'WLD', 'ミドリ安全', 'サーヴォ', 'モンブラン',
        'チトセ', 'アイトス', '公式', 'オンラインストア',
    }
    cleaned_parts = []
    for p in name_parts:
        key = sku_match_key(p)
        if key and key in {sku_match_key(n) for n in _NAME_NOISE}:
            continue
        if paren_classify.unify_import_notation(p) in _NAME_NOISE:
            continue
        cleaned_parts.append(p)
    name = ' '.join(cleaned_parts).strip()
    name = re.sub(r'[\s\u3000]+', ' ', name).strip()
    # 括弧直前の余分な空白を整える
    name = re.sub(r'\s+（', '（', name)
    name = paren_classify.unify_import_notation(name) or name
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


def _catalog_searches_for(*, manufacturer: str, query: str) -> list[dict]:
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
    return hits


def _build_candidate_from_hit(
    hit: dict, *,
    maker_sku: str,
    manufacturer: str,
    hints: dict,
    current_name: str,
    current_color: str,
    current_supplement: str = '',
    current_sku: str = '',
) -> dict:
    """候補化の結果を返す。

    result:
      - candidate: 採用可能な候補 dict
      - rejection: sku_mismatch / parse_incomplete / hint_reject / page_unavailable
    """
    title = hit.get('title') or ''
    url = hit.get('url') or ''
    page_html = ''
    page_title = title
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

    base_sku, _ = position_sku.strip_sku_position_suffix(maker_sku)
    base_sku = base_sku or stock.text(maker_sku)
    heading = page_title or title or _extract_h1(page_html)
    sku_info = page_sku_match(page_html, heading, base_sku)
    if sku_info.get('match') == 'none':
        sku_info = page_sku_match(page_html, heading, maker_sku)
    if sku_info.get('match') == 'none':
        return {
            'candidate': None,
            'rejection': 'sku_mismatch',
            'detail': {
                'sourceUrl': url,
                'title': heading,
                'reason': 'sku_mismatch',
            },
        }

    sku_verified = sku_info.get('match') == 'structured'
    parsed = parse_maker_listing_title(
        heading, base_sku, sku_verified=sku_verified,
    )
    if not parsed and heading != title:
        parsed = parse_maker_listing_title(
            title, base_sku, sku_verified=sku_verified,
        )
    if not parsed:
        h1 = _extract_h1(page_html)
        if h1:
            parsed = parse_maker_listing_title(
                h1, base_sku, sku_verified=sku_verified,
            )
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
                'missingFields': missing,
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
    planned_sku = planned_registered_sku(maker_sku=base_sku, position=position)
    # 上下を補足へ重複表示しない。上下以外の補足のみ残す
    supplement = stock.text(parsed.get('supplement') or '')
    if supplement in _POSITION_TOKENS:
        supplement = ''

    hit_mfr = stock.text(hit.get('manufacturer') or manufacturer or '')
    missing = list(parsed.get('missingFields') or [])
    current_sku_t = stock.text(current_sku) or base_sku
    # 色が空でも名が取れれば候補にはする（不足は表示用）
    cand = {
        'name': parsed['name'],
        'color': parsed.get('color') or '',
        'size': parsed.get('size') or '',
        'position': position,
        'supplement': supplement,
        'makerSku': base_sku,
        'plannedRegisteredSku': planned_sku,
        'manufacturer': hit_mfr,
        'sourceUrl': url,
        'title': parsed.get('title') or page_title,
        'preferred': bool(hit.get('preferred')) or _host_preferred(url),
        'source': hit.get('source') or 'sales_page',
        'skuMatch': sku_info.get('match'),
        'missingFields': missing,
        'diff': {
            'nameChanged': stock.text(parsed['name']) != stock.text(current_name),
            'colorChanged': stock.text(parsed.get('color') or '') != stock.text(current_color),
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
    base_sku, pos_from_sku = position_sku.strip_sku_position_suffix(raw_sku)
    sku = base_sku or raw_sku
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
    current_sku_t = (
        stock.text(current_sku)
        or planned_registered_sku(maker_sku=sku, position=position, source_code=raw_sku)
    )

    if not is_valid_maker_sku(sku):
        return {
            'status': 'invalid_sku',
            'statusLabel': 'メーカー品番が対象外です',
            'makerSku': sku,
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
            'manuallyConfirmed': bool(int(saved.get('manually_confirmed') or 0)),
            'fromSaved': True,
            'savedKey': saved_key,
        }

    reference = reference_search_url(manufacturer=mfr, maker_sku=sku, hints=hints)
    if mfr == 'ミドリ安全' or manufacturer_hint_from_supplier(supplier_t) == 'ミドリ安全':
        reference = midori_catalog_search_url(sku)
    elif mfr == 'KAZEN' or manufacturer_hint_from_supplier(supplier_t) == 'KAZEN':
        reference = kazen_catalog_search_url(sku)

    stages = build_search_query_stages(manufacturer=mfr, maker_sku=sku, hints=hints)
    raw_hits = []
    queries_tried = []
    search_errors = 0
    page_errors = 0
    sku_mismatches = 0
    parse_incomplete_pages = []
    seen_urls = set()

    for query in stages:
        queries_tried.append(query)
        # 1) メーカー公式カタログ（無料）
        try:
            for hit in _catalog_searches_for(manufacturer=mfr, query=query):
                u = hit.get('url') or ''
                if u in seen_urls:
                    continue
                seen_urls.add(u)
                raw_hits.append(hit)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            search_errors += 1
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
        if any(
            hit.get('source') in ('midori_ec', 'kazen_ec')
            and sku_match_key(sku) in sku_match_key(hit.get('title') or '')
            for hit in raw_hits
        ):
            break

    sk_key = sku_match_key(sku)
    stem_key = sku_match_key(_model_stem(sku) or '')
    mfr_u = paren_classify.unify_import_notation(mfr).upper()

    def _hit_rank(h):
        title_k = sku_match_key(h.get('title') or '')
        url_k = sku_match_key(h.get('url') or '')
        url = (h.get('url') or '').lower()
        exact = 0 if (sk_key and (sk_key in title_k or sk_key in url_k)) else 1
        stem_hit = 0 if (stem_key and stem_key in title_k) else 1
        # メーカー公式ホストを仕入先カタログのノイズより優先
        host_boost = 1
        if 'KAZEN' in mfr_u and 'kazen-ec' in url:
            host_boost = 0
        elif 'ミドリ安全' in mfr and 'midori-anzen' in url:
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
        if rejection == 'parse_incomplete':
            if detail:
                parse_incomplete_pages.append(detail)
            continue
        if rejection == 'hint_reject' or not cand:
            continue
        key = (
            maker_sku_key(cand['name']),
            paren_classify.color_match_key(cand.get('color') or ''),
            cand.get('position') or '',
            cand.get('plannedRegisteredSku') or '',
            cand.get('supplement') or '',
            stock.text(cand.get('size') or ''),
        )
        if any(
            (
                maker_sku_key(x['name']),
                paren_classify.color_match_key(x.get('color') or ''),
                x.get('position') or '',
                x.get('plannedRegisteredSku') or '',
                x.get('supplement') or '',
                stock.text(x.get('size') or ''),
            ) == key
            for x in live_candidates
        ):
            continue
        live_candidates.append(cand)

    # 名・色が揃った公式寄りの候補を先に見せる（自動採用はしない）
    live_candidates.sort(key=lambda c: (
        0 if (c.get('name') and c.get('color')) else 1,
        0 if c.get('preferred') else 1,
        0 if c.get('skuMatch') == 'structured' else 1,
    ))

    def _with_saved(cands):
        if not (saved_payload and saved_payload.get('name')):
            return list(cands)
        planned = saved_payload.get('plannedRegisteredSku') or current_sku_t
        front = {
            'name': saved_payload['name'],
            'color': saved_payload.get('color') or '',
            'size': saved_payload.get('size') or '',
            'position': saved_payload.get('position') or position,
            'supplement': saved_payload.get('supplement') or '',
            'makerSku': sku,
            'plannedRegisteredSku': planned,
            'manufacturer': saved_payload.get('manufacturer') or mfr,
            'sourceUrl': saved_payload.get('sourceUrl') or '',
            'title': saved_payload['name'],
            'preferred': True,
            'source': 'saved',
            'fromSaved': True,
            'missingFields': [],
            'diff': {
                'nameChanged': stock.text(saved_payload['name']) != stock.text(current_name),
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
                and stock.text(x.get('plannedRegisteredSku')) == stock.text(front.get('plannedRegisteredSku'))
                and stock.text(x.get('sourceUrl')) == stock.text(front.get('sourceUrl'))
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
        elif sku_mismatches and raw_hits:
            status, label = 'sku_mismatch', '品番不一致'
            note = (
                '検索でページは見つかりましたが、商品コード／見出しの完全品番が一致しませんでした。'
                '関連商品やナビ内の品番だけの一致は候補にしません。'
            )
        else:
            status, label = 'no_match', '候補なし'
            note = (
                'メーカー品番と旧商品名ヒントに一致する商品情報を取れませんでした。'
                '参考ページを開いて確認できます（開いただけでは補正完了になりません）。'
            )
        # 通信失敗時は保存済みを candidates に混ぜず saved のみ返す（状態を曖昧にしない）
        return {
            'status': status,
            'statusLabel': label,
            'makerSku': sku,
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
    if len(candidates) == 1:
        status, status_label = 'ok', '補正候補取得できた'
    else:
        status, status_label = 'multiple', '複数候補'

    return {
        'status': status,
        'statusLabel': status_label,
        'makerSku': sku,
        'plannedRegisteredSku': (
            (candidates[0].get('plannedRegisteredSku') if candidates else '')
            or current_sku_t
        ),
        'manufacturer': mfr or (saved_payload or {}).get('manufacturer') or '',
        'candidates': candidates,
        'saved': saved_payload,
        'referenceUrl': reference,
        'note': (
            '採用するまで登録予定は変わりません。'
            '仕入先はメーカーではないため、ブランド名が含まれる場合のみメーカーヒントに使います。'
            '上下区分がある場合は登録予定品番末尾へ連結します（例: VEH30B下）。'
            '取得サイズは照合・表示用で、旧CSVのサイズ割当は上書きしません。'
        ),
        'supplierIgnored': supplier_t,
        'searchHints': hints,
        'queriesTried': queries_tried,
        'matchedPages': [],
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
) -> dict:
    raw_sku = stock.text(maker_sku)
    base_sku, pos_from_sku = position_sku.strip_sku_position_suffix(raw_sku)
    base_sku = base_sku or raw_sku
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
    # 保存キーは上下を含む登録予定品番（上と下を別補正として保持）
    save_sku = planned if pos else base_sku
    row, changed = save_adopted_correction(
        c,
        manufacturer=manufacturer,
        maker_sku=save_sku,
        adopted_name=name,
        adopted_color=color,
        source_url=source_url,
        adopted_extra={
            'supplement': supp,
            'position': pos,
            'baseMakerSku': base_sku,
            'plannedRegisteredSku': planned,
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
        'plannedRegisteredSku': stock.text(extra.get('plannedRegisteredSku') or planned),
        'position': stock.text(extra.get('position') or pos),
        'name': row.get('adopted_name') or name,
        'color': row.get('adopted_color') or color,
        'supplement': stock.text(extra.get('supplement') or supp),
        'sourceUrl': row.get('source_url') or source_url,
    }
