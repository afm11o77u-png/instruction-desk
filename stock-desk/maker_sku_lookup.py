"""旧CSV一括取り込み: メーカー品番からの無料補正候補取得。

- 有料AI／検索APIは使わない（urllib による無料の公開ページ取得のみ）
- 仕入先 ≠ メーカー（無条件同一視しない）。明示メーカー or 仕入先名に含まれる既知ブランドのみヒント
- メーカー品番に加え、旧商品名から「上／下」や独立した色語を検索補助に使う
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
)

# 仕入先名にブランドが含まれるときだけメーカーヒントにする（無条件同一視しない）
_SUPPLIER_BRAND_MARKERS = (
    ('ミドリ安全', 'ミドリ安全'),
    ('サーヴォ', 'サーヴォ'),
    ('住商モンブラン', '住商モンブラン'),
    ('モンブラン', 'モンブラン'),
    ('チトセ', 'チトセ'),
    ('アイトス', 'アイトス'),
)

_POSITION_TOKENS = ('上', '下')

_USER_AGENT = (
    'Mozilla/5.0 (compatible; instruction-desk-stock/1.0; +local; free-lookup)'
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
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_TAG_RE = re.compile(r'<[^>]+>')
_MIDORI_RESULT_RE = re.compile(
    r'href="(/shop/g/g[^"]+)"[^>]*title="([^"]+)"',
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


def skus_equal(a: str, b: str) -> bool:
    ka, kb = sku_match_key(a), sku_match_key(b)
    return bool(ka) and ka == kb


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
    prod = row.get('product') if isinstance(row.get('product'), dict) else {}
    csv = row.get('csv') if isinstance(row.get('csv'), dict) else {}
    explicit = stock.text(
        prod.get('makerCode') or csv.get('makerCode') or ''
    )
    if explicit:
        return explicit if is_valid_maker_sku(explicit) else ''
    code = stock.text(csv.get('code') or prod.get('code') or '')
    if prod.get('colorSplit') and not explicit:
        return ''
    if not stock_product_merge.is_normal_product_code(code):
        return ''
    return code if is_valid_maker_sku(code) else ''


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
    - 有効なメーカー品番がある（別注・品番なし・空は不可）
    - 同じ品番内に、正規化後も異なる色候補が複数ある（colorDiffPossible）
    社内管理品番がある行は元のメーカー品番（makerCode）を使う。
    """
    color_diff = bool(group.get('colorDiffPossible'))
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
    eligible = bool(maker_sku) and is_valid_maker_sku(maker_sku) and color_diff
    return {
        'eligible': eligible,
        'showButton': eligible,
        'makerSku': maker_sku if (maker_sku and is_valid_maker_sku(maker_sku)) else '',
        'manufacturer': manufacturer,
        'supplier': supplier,
        # 色競合時は色語を除いた名で検索し、一色ヒントで候補を落とさない
        'sourceNameHint': neutral_search_source_name(source_names) if color_diff else (
            source_names[0] if source_names else ''
        ),
        'colorDiffPossible': color_diff,
        'rowNos': list(group.get('rowNos') or []),
    }


def extract_name_search_hints(old_name: str) -> dict:
    """旧商品名から検索補助語を取り出す。

    例: 「白下 ブルー」→ positions=['下'], colors=['ブルー']
    「白下」内の「白」は検索色にしない（必ず色条件にしない）。
    """
    unified = paren_classify.unify_import_notation(stock.text(old_name) or '')
    positions = [p for p in _POSITION_TOKENS if p in unified]
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
    """段階的な検索語。保存品番は書き換えず、クエリ文字列だけ拡張する。"""
    sku = stock.text(maker_sku)
    mfr = stock.text(manufacturer)
    positions = list(hints.get('positions') or [])
    colors = list(hints.get('colors') or [])
    stages = []

    def add(parts):
        q = ' '.join(p for p in parts if p)
        if q and q not in stages:
            stages.append(q)

    add([mfr, sku])
    add([sku])
    for pos in positions:
        add([mfr, sku, pos])
        add([sku, pos])
        # カタログによっては密着語も試す（保存SKUは変えない）
        add([mfr, f'{sku}{pos}'])
        add([f'{sku}{pos}'])
    for pos in positions:
        for col in colors:
            add([mfr, sku, pos, col])
            add([sku, pos, col])
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
    sk = sku_match_key(maker_sku)
    if not sk:
        return False
    # ページ全体を NFKC して全角品番も拾う
    return sk in sku_match_key(html or '')


def _token_is_color(token: str) -> str:
    key = paren_classify.color_match_key(token)
    for term in paren_classify.COLOR_TERMS:
        if paren_classify.color_match_key(term) == key:
            return paren_classify.unify_import_notation(term) or term
    return ''


def parse_maker_listing_title(title: str, maker_sku: str) -> dict | None:
    """掲載名から 商品名／色／補足（上・下）を分解。品番が一致しないタイトルは却下。"""
    unified = paren_classify.unify_import_notation(stock.text(title) or '')
    if not unified:
        return None
    if sku_match_key(maker_sku) not in sku_match_key(unified):
        return None
    tokens = [t for t in re.split(r'[\s\u3000]+', unified) if t]
    name_parts = []
    color = ''
    supplement = ''
    saw_sku = False
    for tok in tokens:
        if skus_equal(tok, maker_sku):
            saw_sku = True
            continue
        if tok in _POSITION_TOKENS:
            supplement = tok
            continue
        col = _token_is_color(tok)
        if col:
            color = col
            continue
        name_parts.append(tok)
    if not saw_sku and sku_match_key(maker_sku) not in sku_match_key(unified):
        return None
    name = ' '.join(name_parts).strip()
    if not name:
        return None
    return {
        'name': name,
        'color': color,
        'supplement': supplement,
        'title': unified,
    }


def _candidate_passes_hints(parsed: dict, hints: dict) -> bool:
    """上下・色のヒントがあるときは区別を一致させる。類似度だけでは通さない。"""
    positions = list(hints.get('positions') or [])
    colors = list(hints.get('colors') or [])
    supp = stock.text(parsed.get('supplement') or '')
    col = stock.text(parsed.get('color') or '')
    if positions:
        if supp not in positions:
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


def _catalog_searches_for(*, manufacturer: str, query: str) -> list[dict]:
    """メーカーに応じた無料カタログ検索。"""
    hits = []
    mfr = stock.text(manufacturer) or ''
    # ミドリ安全（仕入先ヒントまたは明示）
    if (not mfr) or ('ミドリ安全' in mfr):
        try:
            hits.extend(_midori_ec_candidates(query))
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
) -> dict | None:
    title = hit.get('title') or ''
    url = hit.get('url') or ''
    page_html = ''
    page_title = title
    try:
        _final, page_html = _http_get(url)
        page_title = _extract_title(page_html) or title
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        page_html = ''

    # 品番一致（全半角吸収）。一部一致のみは却下
    text_for_sku = page_html or page_title or title
    if not _page_mentions_sku(text_for_sku, maker_sku):
        return None
    # タイトルに別品番だけが載っているケースを弾くため、掲載名を厳密分解
    parsed = parse_maker_listing_title(page_title or title, maker_sku)
    if not parsed:
        # ページ本文の H1/og でもう一度
        parsed = parse_maker_listing_title(title, maker_sku)
    if not parsed:
        return None
    if not _candidate_passes_hints(parsed, hints):
        return None

    hit_mfr = stock.text(hit.get('manufacturer') or manufacturer or '')
    return {
        'name': parsed['name'],
        'color': parsed.get('color') or '',
        'supplement': parsed.get('supplement') or '',
        'makerSku': stock.text(maker_sku),
        'manufacturer': hit_mfr,
        'sourceUrl': url,
        'title': parsed.get('title') or page_title,
        'preferred': bool(hit.get('preferred')) or _host_preferred(url),
        'source': hit.get('source') or 'sales_page',
        'diff': {
            'nameChanged': stock.text(parsed['name']) != stock.text(current_name),
            'colorChanged': stock.text(parsed.get('color') or '') != stock.text(current_color),
            'supplementChanged': stock.text(parsed.get('supplement') or '') != stock.text(current_supplement),
            'currentName': stock.text(current_name),
            'currentColor': stock.text(current_color),
            'currentSupplement': stock.text(current_supplement),
        },
    }


def search_maker_product(
    c, *,
    manufacturer: str,
    maker_sku: str,
    current_name: str = '',
    current_color: str = '',
    current_supplement: str = '',
    source_name: str = '',
    supplier: str = '',
) -> dict:
    """メーカー＋メーカー品番（＋旧名ヒント）で候補を無料取得する。

    status: ok / no_match / multiple / fetch_failed / page_unavailable / invalid_sku
    """
    ensure_schema(c)
    sku = stock.text(maker_sku)
    supplier_t = stock.text(supplier)
    mfr = resolve_manufacturer(manufacturer=manufacturer, supplier=supplier_t)
    # 旧商品名を検索補助の主とし、予定名は補助
    hint_source = stock.text(source_name) or stock.text(current_name)
    hints = extract_name_search_hints(hint_source)

    if not is_valid_maker_sku(sku):
        return {
            'status': 'invalid_sku',
            'statusLabel': 'メーカー品番が対象外です',
            'makerSku': sku,
            'manufacturer': mfr,
            'candidates': [],
            'saved': None,
            'referenceUrl': reference_search_url(manufacturer=mfr, maker_sku=sku, hints=hints),
            'note': '別注・品番なし・空の品番では補正できません。',
            'supplierIgnored': supplier_t,
            'searchHints': hints,
            'queriesTried': [],
        }

    saved = lookup_saved_correction(c, manufacturer=mfr, maker_sku=sku)
    saved_payload = None
    if saved:
        extra = {}
        try:
            extra = json.loads(saved.get('adopted_json') or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            extra = {}
        saved_payload = {
            'manufacturer': saved.get('manufacturer') or mfr,
            'makerSku': saved.get('maker_sku') or sku,
            'name': saved.get('adopted_name') or '',
            'color': saved.get('adopted_color') or '',
            'supplement': stock.text(extra.get('supplement') or ''),
            'sourceUrl': saved.get('source_url') or '',
            'manuallyConfirmed': bool(int(saved.get('manually_confirmed') or 0)),
            'fromSaved': True,
        }

    reference = reference_search_url(manufacturer=mfr, maker_sku=sku, hints=hints)
    if mfr == 'ミドリ安全' or manufacturer_hint_from_supplier(supplier_t) == 'ミドリ安全':
        reference = midori_catalog_search_url(sku)

    stages = build_search_query_stages(manufacturer=mfr, maker_sku=sku, hints=hints)
    raw_hits = []
    queries_tried = []
    search_errors = 0
    page_errors = 0
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
            hit.get('source') == 'midori_ec'
            and sku_match_key(sku) in sku_match_key(hit.get('title') or '')
            for hit in raw_hits
        ):
            break

    raw_hits = sorted(raw_hits, key=lambda h: (0 if h.get('preferred') else 1))

    live_candidates = []
    for hit in raw_hits[:10]:
        try:
            cand = _build_candidate_from_hit(
                hit,
                maker_sku=sku,
                manufacturer=mfr,
                hints=hints,
                current_name=current_name,
                current_color=current_color,
                current_supplement=current_supplement,
            )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            page_errors += 1
            continue
        if not cand:
            continue
        key = (
            maker_sku_key(cand['name']),
            paren_classify.color_match_key(cand.get('color') or ''),
            cand.get('supplement') or '',
        )
        if any(
            (
                maker_sku_key(x['name']),
                paren_classify.color_match_key(x.get('color') or ''),
                x.get('supplement') or '',
            ) == key
            for x in live_candidates
        ):
            continue
        live_candidates.append(cand)

    def _with_saved(cands):
        if not (saved_payload and saved_payload.get('name')):
            return list(cands)
        front = {
            'name': saved_payload['name'],
            'color': saved_payload.get('color') or '',
            'supplement': saved_payload.get('supplement') or '',
            'makerSku': sku,
            'manufacturer': saved_payload.get('manufacturer') or mfr,
            'sourceUrl': saved_payload.get('sourceUrl') or '',
            'title': saved_payload['name'],
            'preferred': True,
            'source': 'saved',
            'fromSaved': True,
            'diff': {
                'nameChanged': stock.text(saved_payload['name']) != stock.text(current_name),
                'colorChanged': stock.text(saved_payload.get('color') or '') != stock.text(current_color),
                'supplementChanged': (
                    stock.text(saved_payload.get('supplement') or '')
                    != stock.text(current_supplement)
                ),
                'currentName': stock.text(current_name),
                'currentColor': stock.text(current_color),
                'currentSupplement': stock.text(current_supplement),
            },
        }
        return [front] + [
            x for x in cands
            if not (
                maker_sku_key(x.get('name')) == maker_sku_key(front['name'])
                and stock.text(x.get('sourceUrl')) == stock.text(front.get('sourceUrl'))
            )
        ]

    if not live_candidates:
        if search_errors and not raw_hits:
            status, label = 'fetch_failed', '取得できない'
            note = (
                '無料の公開検索へ届きませんでした。参考ページを開いて確認できます'
                '（検索ページを開いただけでは補正完了になりません）。'
            )
        elif raw_hits and page_errors >= max(1, min(len(raw_hits), 10)):
            status, label = 'page_unavailable', 'ページ取得不可'
            note = '検索ヒットはありましたが商品ページを取得できませんでした。'
        elif page_errors and not raw_hits:
            status, label = 'page_unavailable', 'ページ取得不可'
            note = '候補ページを取得できませんでした。参考ページを開いて確認できます。'
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
            'manufacturer': mfr,
            'candidates': [],
            'saved': saved_payload,
            'referenceUrl': reference,
            'note': note,
            'supplierIgnored': supplier_t,
            'searchHints': hints,
            'queriesTried': queries_tried,
        }

    candidates = _with_saved(live_candidates)
    if len(candidates) == 1:
        status, status_label = 'ok', '候補あり'
    else:
        status, status_label = 'multiple', '候補が複数あります'

    return {
        'status': status,
        'statusLabel': status_label,
        'makerSku': sku,
        'manufacturer': mfr or (saved_payload or {}).get('manufacturer') or '',
        'candidates': candidates,
        'saved': saved_payload,
        'referenceUrl': reference,
        'note': (
            '採用するまで登録予定は変わりません。'
            '仕入先はメーカーではないため、ブランド名が含まれる場合のみメーカーヒントに使います。'
            'メーカー品番の保存値は書き換えません。'
        ),
        'supplierIgnored': supplier_t,
        'searchHints': hints,
        'queriesTried': queries_tried,
    }


def adopt_maker_correction(
    c, *,
    manufacturer: str,
    maker_sku: str,
    name: str,
    color: str = '',
    supplement: str = '',
    source_url: str = '',
) -> dict:
    if not is_valid_maker_sku(maker_sku):
        raise ValueError('メーカー品番が対象外です。')
    if not stock.text(name):
        raise ValueError('採用する商品名が空です。')
    row, changed = save_adopted_correction(
        c,
        manufacturer=manufacturer,
        maker_sku=maker_sku,
        adopted_name=name,
        adopted_color=color,
        source_url=source_url,
        adopted_extra={'supplement': stock.text(supplement)},
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
        'makerSku': row.get('maker_sku') or maker_sku,
        'name': row.get('adopted_name') or name,
        'color': row.get('adopted_color') or color,
        'supplement': stock.text(extra.get('supplement') or supplement),
        'sourceUrl': row.get('source_url') or source_url,
    }
