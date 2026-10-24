"""旧CSV一括取り込み: メーカー品番からの無料補正候補取得。

- 有料AI／検索APIは使わない（urllib による無料の公開ページ取得のみ）
- 仕入先 ≠ メーカー。検索キーは manufacturer + maker SKU
- 採用内容は SQLite に永続化し、次回取込で再利用
- 手動確定済みの採用内容は再検索で上書きしない
- 一致しない候補は自動採用しない。取得失敗時は「取得できない」
"""
from __future__ import annotations

import html as html_lib
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import legacy_csv_import as legacy
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

# 公式・販社っぽいホスト（優先）。完全一致ではなく部分一致。
_PREFERRED_HOST_HINTS = (
    'servo', 'montblanc', 'chitose', 'aitoz', 'aito-s', 'midori',
    'bonmax', 'karsee', 'selery', 'faceblend', 'toms', 'kurodaruma',
    'workman', 'asics', 'mizuno', 'uniclo',
)

_USER_AGENT = (
    'Mozilla/5.0 (compatible; instruction-desk-stock/1.0; +local; free-lookup)'
)
_FETCH_TIMEOUT_SEC = 8

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
_COLOR_HINT_RE = re.compile(
    r'(蛍光グリーン|蛍光イエロー|蛍光オレンジ|蛍光ピンク|'
    r'ネイビー|ブラック|ホワイト|レッド|ブルー|グリーン|'
    r'イエロー|オレンジ|ピンク|グレー|グレイ|ブラウン|'
    r'ベージュ|シルバー|ゴールド|サックス|チャコール|'
    r'カーキ|キャメル|ワイン|エンジ|アイボリー|ターコイズ|'
    r'水色|紺|白|黒|赤|青|緑|黄|茶|灰|銀|金|紫|橙|桃)'
)


def maker_sku_key(value: str) -> str:
    """メーカー品番の永続キー（空白除去。先頭ゼロは維持するため NFKC 全体はしない）。"""
    return re.sub(r'[\s\u3000]+', '', stock.text(value) or '')


def manufacturer_key(value: str) -> str:
    t = stock.text(value) or ''
    # メーカー名は表記ゆれを軽く吸収
    try:
        import product_ledger_paren as paren_classify
        t = paren_classify.unify_import_notation(t)
    except Exception:
        pass
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
    """具体的なメーカー品番としてボタン表示してよいか。"""
    s = stock.text(sku) or ''
    if not s or is_management_sku(s):
        return False
    # 極端に短い／記号のみは対象外
    if len(s) < 2:
        return False
    if re.fullmatch(r'[-_/／・.]+', s):
        return False
    return True


def effective_maker_sku_from_row(row: dict) -> str:
    """行からメーカー品番を取る。

    色分割などで makerCode が明示されていればそれを優先。
    無ければ従来どおり社内品番（通常品番）をメーカー品番扱い。
    管理値（別注・品番なし等）は空を返す。
    """
    prod = row.get('product') if isinstance(row.get('product'), dict) else {}
    csv = row.get('csv') if isinstance(row.get('csv'), dict) else {}
    explicit = stock.text(
        prod.get('makerCode') or csv.get('makerCode') or ''
    )
    if explicit:
        return explicit if is_valid_maker_sku(explicit) else ''
    code = stock.text(csv.get('code') or prod.get('code') or '')
    # 色分割後の社内品番（品番-色）をメーカー品番扱いしない
    if prod.get('colorSplit') and not explicit:
        # makerCode が落ちている異常系。codeOverride 側は社内品番
        return ''
    if not stock_product_merge.is_normal_product_code(code):
        return ''
    return code if is_valid_maker_sku(code) else ''


def row_maker_correction_meta(row: dict) -> dict:
    sku = effective_maker_sku_from_row(row)
    return {
        'eligible': bool(sku) and is_valid_maker_sku(sku),
        'makerSku': sku,
        # 仕入先はメーカーではない。推定メーカーは空（候補取得後に埋まる）
        'manufacturer': stock.text((row.get('makerCorrection') or {}).get('manufacturer') or ''),
    }


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
    # メーカー名が空のときは品番のみでも探す（採用時にメーカーが埋まっているもの優先）
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
    """採用内容を永続化。手動確定済みは overwrite_manual=False なら維持。"""
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
            # 再検索・再取得では上書きしない（採用ボタン経由は overwrite_manual=True）
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


def reference_search_url(*, manufacturer: str, maker_sku: str) -> str:
    q_parts = [stock.text(maker_sku)]
    if stock.text(manufacturer):
        q_parts.insert(0, stock.text(manufacturer))
    q = ' '.join(p for p in q_parts if p)
    return 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(q)


def _http_get(url: str, *, timeout: float = _FETCH_TIMEOUT_SEC) -> tuple[str, str]:
    """Returns (final_url, text). Raises on failure."""
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': _USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ja,en;q=0.8',
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(400_000)
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


def _guess_color_from_text(text: str) -> str:
    t = stock.text(text) or ''
    hits = _COLOR_HINT_RE.findall(t)
    if not hits:
        return ''
    # 先頭の色語を採用（複数は自動確定しない）
    uniq = []
    for h in hits:
        if h not in uniq:
            uniq.append(h)
    if len(uniq) == 1:
        return uniq[0]
    return ''


def _guess_name_from_title(title: str, maker_sku: str) -> str:
    t = stock.text(title) or ''
    if not t:
        return ''
    # サイト名除去
    for sep in (' | ', '｜', ' - ', ' – ', ' — ', ' / '):
        if sep in t:
            t = t.split(sep)[0].strip()
    # 品番そのものだけのタイトルは名として弱い
    if maker_sku_key(t) == maker_sku_key(maker_sku):
        return ''
    # 長すぎるタイトルは切り詰め
    if len(t) > 80:
        t = t[:80].rstrip()
    return t


def _host_preferred(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(h in host for h in _PREFERRED_HOST_HINTS)


def _ddg_candidates(query: str, *, limit: int = 8) -> list[dict]:
    url = 'https://html.duckduckgo.com/html/?q=' + urllib.parse.quote(query)
    final_url, html = _http_get(url)
    out = []
    for m in _DDG_RESULT_RE.finditer(html):
        href = html_lib.unescape(m.group(1) or '')
        title = _strip_tags(m.group(2) or '')
        # DDG のリダイレクトを解消
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


def _page_mentions_sku(html: str, maker_sku: str) -> bool:
    sk = maker_sku_key(maker_sku)
    if not sk:
        return False
    # 大文字小文字無視で素朴に含むか
    return sk.lower() in (html or '').lower()


def search_maker_product(
    c, *,
    manufacturer: str,
    maker_sku: str,
    current_name: str = '',
    current_color: str = '',
    supplier: str = '',
) -> dict:
    """メーカー＋メーカー品番で候補を無料取得する。

    status:
      saved / ok / no_match / multiple / fetch_failed / invalid_sku
    """
    ensure_schema(c)
    sku = stock.text(maker_sku)
    mfr = stock.text(manufacturer)
    if not is_valid_maker_sku(sku):
        return {
            'status': 'invalid_sku',
            'statusLabel': 'メーカー品番が対象外です',
            'makerSku': sku,
            'manufacturer': mfr,
            'candidates': [],
            'saved': None,
            'referenceUrl': reference_search_url(manufacturer=mfr, maker_sku=sku),
            'note': '別注・品番なし・空の品番では補正できません。',
            # 仕入先はメーカーではない
            'supplierIgnored': stock.text(supplier),
        }

    saved = lookup_saved_correction(c, manufacturer=mfr, maker_sku=sku)
    saved_payload = None
    if saved:
        saved_payload = {
            'manufacturer': saved.get('manufacturer') or mfr,
            'makerSku': saved.get('maker_sku') or sku,
            'name': saved.get('adopted_name') or '',
            'color': saved.get('adopted_color') or '',
            'sourceUrl': saved.get('source_url') or '',
            'manuallyConfirmed': bool(int(saved.get('manually_confirmed') or 0)),
            'fromSaved': True,
        }

    reference = reference_search_url(manufacturer=mfr, maker_sku=sku)
    query = ' '.join(p for p in (mfr, sku, '制服', '商品') if p)

    try:
        raw_hits = _ddg_candidates(query)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return {
            'status': 'fetch_failed',
            'statusLabel': '取得できない',
            'makerSku': sku,
            'manufacturer': mfr,
            'candidates': [],
            'saved': saved_payload,
            'referenceUrl': reference,
            'error': f'{type(e).__name__}',
            'note': '無料の公開検索へ届きませんでした。参考ページを開いて確認できます。',
            'supplierIgnored': stock.text(supplier),
        }

    # 公式っぽいホストを先に
    raw_hits = sorted(raw_hits, key=lambda h: (0 if h.get('preferred') else 1))

    candidates = []
    fetch_errors = 0
    for hit in raw_hits[:6]:
        url = hit['url']
        title = hit.get('title') or ''
        page_html = ''
        try:
            _final, page_html = _http_get(url)
            page_title = _extract_title(page_html) or title
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            fetch_errors += 1
            page_title = title
            page_html = ''
        if page_html and not _page_mentions_sku(page_html, sku):
            # 品番が本文に無いページは不一致として捨てる（自動採用しない）
            continue
        if not page_html and sku.lower() not in (title or '').lower():
            continue
        name = _guess_name_from_title(page_title, sku)
        color = _guess_color_from_text(page_title)
        if not name:
            continue
        candidates.append({
            'name': name,
            'color': color,
            'sourceUrl': url,
            'title': page_title,
            'preferred': bool(hit.get('preferred')),
            'source': 'official_or_sales_page' if hit.get('preferred') else 'sales_page',
            'diff': {
                'nameChanged': stock.text(name) != stock.text(current_name),
                'colorChanged': stock.text(color) != stock.text(current_color),
                'currentName': stock.text(current_name),
                'currentColor': stock.text(current_color),
            },
        })

    # 手動確定済みがあれば先頭に提示（再検索で上書きはしない）
    if saved_payload and saved_payload.get('name'):
        front = {
            'name': saved_payload['name'],
            'color': saved_payload.get('color') or '',
            'sourceUrl': saved_payload.get('sourceUrl') or '',
            'title': saved_payload['name'],
            'preferred': True,
            'source': 'saved',
            'fromSaved': True,
            'diff': {
                'nameChanged': stock.text(saved_payload['name']) != stock.text(current_name),
                'colorChanged': stock.text(saved_payload.get('color') or '') != stock.text(current_color),
                'currentName': stock.text(current_name),
                'currentColor': stock.text(current_color),
            },
        }
        # 重複除去
        candidates = [front] + [
            c for c in candidates
            if not (
                maker_sku_key(c.get('name')) == maker_sku_key(front['name'])
                and stock.text(c.get('sourceUrl')) == stock.text(front.get('sourceUrl'))
            )
        ]

    if not candidates:
        status = 'fetch_failed' if fetch_errors and not raw_hits else 'no_match'
        if raw_hits and fetch_errors == len(raw_hits[:6]):
            status = 'fetch_failed'
        return {
            'status': status,
            'statusLabel': '取得できない' if status == 'fetch_failed' else '候補なし',
            'makerSku': sku,
            'manufacturer': mfr,
            'candidates': [],
            'saved': saved_payload,
            'referenceUrl': reference,
            'note': (
                '公開ページから一致する商品情報を取れませんでした。'
                '参考ページを開いて確認できます（検索ページを開いただけでは補正完了になりません）。'
            ),
            'supplierIgnored': stock.text(supplier),
        }

    if len(candidates) == 1:
        status = 'ok'
        status_label = '候補あり'
    else:
        status = 'multiple'
        status_label = '候補が複数あります'

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
            '仕入先はメーカーではないため検索キーに使いません。'
        ),
        'supplierIgnored': stock.text(supplier),
    }


def adopt_maker_correction(
    c, *,
    manufacturer: str,
    maker_sku: str,
    name: str,
    color: str = '',
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
        overwrite_manual=True,
    )
    return {
        'saved': True,
        'changed': changed,
        'manufacturer': row.get('manufacturer') or manufacturer,
        'makerSku': row.get('maker_sku') or maker_sku,
        'name': row.get('adopted_name') or name,
        'color': row.get('adopted_color') or color,
        'sourceUrl': row.get('source_url') or source_url,
    }
