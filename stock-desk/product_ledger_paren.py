"""旧商品台帳CSVの商品名括弧分類（色・加工・生地／素材）。

AI・外部サービスは使わない。明示辞書＋規則のみ。
括弧内の分類に加え、候補選定・登録時は括弧外の明示色も辞書＋境界規則で抽出する。
色に見えるが商品種の一部である語（例: 白衣の「白」）は色として切り出さない。
緑 と 蛍光グリーンは別色（同一視しない）。
行ごとの手編集をグローバル規則へ自動昇格しない。
数値だけの「色コード」は根拠なく色と断定・削除しない。
"""
from __future__ import annotations

import re
import unicodedata

# 長い語を先に照合するため、定義順は長さ降順でソートして使う。
# 緑 と 蛍光グリーンは別エントリ（同一視しない）。
COLOR_TERMS = (
    '蛍光グリーン', '蛍光イエロー', '蛍光オレンジ', '蛍光ピンク',
    'ネイビー', 'ブラック', 'ホワイト', 'レッド', 'ブルー', 'グリーン',
    'イエロー', 'オレンジ', 'ピンク', 'グレー', 'グレイ', 'ブラウン',
    'ベージュ', 'シルバー', 'ゴールド', 'サックス', 'チャコール',
    'カーキ', 'キャメル', 'ワイン', 'エンジ', 'アイボリー', 'ターコイズ',
    '水色', '紺', '白', '黒', '赤', '青', '緑', '黄', '茶', '灰',
    '銀', '金', '紫', '橙', '桃',
)

# 色文字を含むが色ではない複合語（切り出し禁止）。長い語を先に見る。
_COLOR_COMPOUND_BLOCKLIST = (
    '白衣',
)

PROCESSING_TERMS = (
    'マジックテープ', 'スナッパー', 'コーティング', 'アイロン',
    '裾上げ', '裾直し', 'ワッペン', 'ネーム', '刺繍', 'プリント',
    '反射', 'カット', '縫製', '加工',
)

# 加工は「DHC刺繍あり」「背中プリント」のように語を含む複合も可（完全一致優先）。
PROCESSING_COMPOUND_OK = True

MATERIAL_TERMS = (
    'クールマックス', 'ポリエステル', 'ナイロン', 'ウール',
    '綿100％', '綿100%', '綿100パーセント', '麻混', 'デニム',
    'ツイル', 'オックス', '綿', '吸汗', '速乾', '防炎', '難燃', '制電', '透湿',
)

_SEGMENT_SPLIT_RE = re.compile(r'[・･、,，/／&＆]+')
_PAREN_PAIR_RE = re.compile(r'[（(]([^（）()]*)[）)]')


def _nfkc(value: str) -> str:
    return unicodedata.normalize('NFKC', value or '')


def _norm_token(value: str) -> str:
    """照合用。空白除去・NFKC。％/% は両方残すため NFKC 後もそのまま。"""
    t = _nfkc(value)
    t = re.sub(r'[\s\u3000]+', '', t)
    return t


def _sorted_terms(terms):
    return sorted({_norm_token(t) for t in terms if _norm_token(t)}, key=len, reverse=True)


_COLORS = _sorted_terms(COLOR_TERMS)
_PROCESSINGS = _sorted_terms(PROCESSING_TERMS)
_MATERIALS = _sorted_terms(MATERIAL_TERMS)

# 表示用（正規化キー → 辞書の代表表記）
_COLOR_DISPLAY = {_norm_token(t): t for t in COLOR_TERMS}
_PROCESSING_DISPLAY = {_norm_token(t): t for t in PROCESSING_TERMS}
_MATERIAL_DISPLAY = {_norm_token(t): t for t in MATERIAL_TERMS}


def _display_for(kind: str, key: str, original: str) -> str:
    if kind == 'color':
        return _COLOR_DISPLAY.get(key) or original
    if kind == 'processing':
        return _PROCESSING_DISPLAY.get(key) or original
    if kind == 'material':
        return _MATERIAL_DISPLAY.get(key) or original
    return original


def paren_balance_ok(name: str) -> bool:
    """全角・半角括弧の対応が取れているか（入れ子は非対応＝壊れている扱い）。"""
    text = name or ''
    depth = 0
    for ch in text:
        if ch in '(（':
            depth += 1
            if depth > 1:
                return False
        elif ch in ')）':
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def extract_paren_segments(name: str):
    """括弧内文字列のリスト（出現順）。壊れた括弧は None。"""
    if not paren_balance_ok(name):
        return None
    return [m.group(1) for m in _PAREN_PAIR_RE.finditer(name or '')]


def strip_parens(name: str) -> str:
    """分類できた括弧を除いたベース名（空白の正規化は最小限）。"""
    if not paren_balance_ok(name):
        return (name or '').strip()
    base = _PAREN_PAIR_RE.sub('', name or '')
    base = re.sub(r'[\s\u3000]{2,}', ' ', base)
    return base.strip()


def _exact_in(terms, key: str):
    return key if key in terms else None


def _processing_match(seg_key: str, seg_raw: str):
    """加工: 完全一致、または辞書語を含む複合（部分一致だけで色・素材へ流さない）。"""
    hit = _exact_in(_PROCESSINGS, seg_key)
    if hit:
        return hit, _display_for('processing', hit, seg_raw)
    if not PROCESSING_COMPOUND_OK:
        return None, None
    for term in _PROCESSINGS:
        if term and term in seg_key and seg_key != term:
            # 複合は原文セグメントを加工仕様として残す
            return term, seg_raw.strip()
    return None, None


def classify_paren_segment(segment: str):
    """1セグメントを色／加工／素材／不明に分類。

    部分文字列だけで色を取らない（例: 蛍光グリーン の中の 緑 は使わない）。
    表示は原文セグメントを優先（％/% の全半角など）。
    """
    raw = (segment or '').strip()
    if not raw:
        return None
    key = _norm_token(raw)
    if not key:
        return None

    # 完全一致を優先（長い語が先）。値は原文表記を残す
    color = _exact_in(_COLORS, key)
    if color:
        return {
            'kind': 'color',
            'value': raw,
            'matched': color,
            'original': raw,
        }
    material = _exact_in(_MATERIALS, key)
    if material:
        return {
            'kind': 'material',
            'value': raw,
            'matched': material,
            'original': raw,
        }
    proc_key, proc_val = _processing_match(key, raw)
    if proc_key:
        return {
            'kind': 'processing',
            'value': proc_val if proc_val == raw else raw,
            'matched': proc_key,
            'original': raw,
        }
    return {
        'kind': 'unknown',
        'value': raw,
        'matched': '',
        'original': raw,
    }


def classify_product_name(product_name: str):
    """商品名の括弧内容を分類する。

    Returns dict:
      originalName, plannedName, color, processing, material,
      unclassified (list), needsConfirm, confirmReasons,
      autoClassified, parenContents, segments
    """
    original = (product_name or '').strip()
    empty = {
        'originalName': original,
        'plannedName': original,
        'baseName': original,
        'color': '',
        'processing': '',
        'material': '',
        'unclassified': [],
        'needsConfirm': False,
        'confirmReasons': [],
        'autoClassified': False,
        'parenContents': [],
        'segments': [],
        'keptOriginal': True,
    }
    if not original:
        return empty

    if not paren_balance_ok(original):
        return {
            **empty,
            'needsConfirm': True,
            'confirmReasons': ['括弧の対応が取れていないため原文のまま要確認'],
            'keptOriginal': True,
        }

    contents = extract_paren_segments(original)
    if not contents:
        return empty

    colors = []
    processings = []
    materials = []
    unclassified = []
    segments = []
    for content in contents:
        parts = [p.strip() for p in _SEGMENT_SPLIT_RE.split(content) if p.strip()]
        if not parts and content.strip():
            parts = [content.strip()]
        if not parts:
            unclassified.append(content)
            segments.append({
                'paren': content, 'kind': 'unknown', 'value': content, 'original': content,
            })
            continue
        for part in parts:
            hit = classify_paren_segment(part)
            segments.append({'paren': content, **hit})
            kind = hit['kind']
            if kind == 'color':
                if hit['value'] not in colors:
                    colors.append(hit['value'])
            elif kind == 'processing':
                if hit['value'] not in processings:
                    processings.append(hit['value'])
            elif kind == 'material':
                if hit['value'] not in materials:
                    materials.append(hit['value'])
            else:
                if hit['value'] not in unclassified:
                    unclassified.append(hit['value'])

    base = strip_parens(original)
    reasons = []
    needs = False

    # 複数色・複数素材は自動確定しない（商品.color / material は単一）
    color = colors[0] if len(colors) == 1 else ''
    material = materials[0] if len(materials) == 1 else ''
    processing = '・'.join(processings) if processings else ''

    if len(colors) > 1:
        needs = True
        reasons.append('括弧内の色が複数あるため要確認: ' + '、'.join(colors))
        color = ''
    if len(materials) > 1:
        needs = True
        reasons.append('括弧内の生地・素材が複数あるため要確認: ' + '、'.join(materials))
        material = ''
    if unclassified:
        needs = True
        reasons.append('括弧内に未分類の文言あり: ' + '、'.join(unclassified))

    # 未分類があれば原文を残す（削除しない）
    if unclassified or (len(colors) > 1) or (len(materials) > 1):
        planned = original
        kept = True
        needs = True
    elif colors or processings or materials:
        planned = base or original
        kept = planned == original
        # 分類できたものだけ抜き、ベース名へ
        if not base:
            planned = original
            kept = True
            needs = True
            reasons.append('括弧除去後の商品名が空になるため原文のまま要確認')
    else:
        # 括弧はあるが何も分類できない
        planned = original
        kept = True
        needs = True
        reasons.append('括弧内を色・加工・生地へ分類できないため原文のまま要確認')

    auto = bool((colors or processings or materials) and not needs)

    return {
        'originalName': original,
        'plannedName': planned,
        'baseName': base or original,
        'color': color,
        'processing': processing,
        'material': material,
        'unclassified': list(unclassified),
        'needsConfirm': needs,
        'confirmReasons': reasons,
        'autoClassified': auto,
        'parenContents': list(contents),
        'segments': segments,
        'keptOriginal': kept,
        # 複数候補の提示用
        'colorCandidates': list(colors),
        'processingCandidates': list(processings),
        'materialCandidates': list(materials),
    }


def color_match_key(value: str) -> str:
    """色の同一判定用キー。緑 と 蛍光グリーンは別キー。"""
    return _norm_token(value)


def colors_equivalent(a: str, b: str) -> bool:
    ka, kb = color_match_key(a), color_match_key(b)
    if not ka or not kb:
        return False
    return ka == kb


def _blocked_spans(text: str):
    """色切り出し禁止区間（白衣 など）。"""
    spans = []
    raw = text or ''
    norm = _norm_token(raw)
    if not norm:
        return spans
    # 正規化後テキスト上で禁止語を探し、原文インデックスへは近似対応
    # （空白除去正規化のため、原文走査でも同じ禁止語を探す）
    for compound in sorted(_COLOR_COMPOUND_BLOCKLIST, key=len, reverse=True):
        ckey = _norm_token(compound)
        if not ckey:
            continue
        start = 0
        while True:
            idx = raw.find(compound, start)
            if idx < 0:
                # NFKC 差（半角など）向けに正規化照合
                break
            spans.append((idx, idx + len(compound)))
            start = idx + 1
        # 正規化キーでも原文を走査（複合が原文にそのままある場合は上で拾える）
        if ckey != compound:
            # 例: 全半角差。簡易に raw を NFKC して位置を探す
            nfkc_raw = _nfkc(raw)
            start = 0
            while True:
                idx = nfkc_raw.find(ckey, start)
                if idx < 0:
                    break
                spans.append((idx, idx + len(ckey)))
                start = idx + 1
    return spans


def _span_overlaps_blocked(start: int, end: int, blocked) -> bool:
    for b0, b1 in blocked:
        if start < b1 and end > b0:
            return True
    return False


def find_explicit_colors_in_text(text: str):
    """辞書色を境界付きで列挙（長い語優先・重複区間は短い方を捨てる）。

    部分文字列だけで短い色を取らない（蛍光グリーン中の緑は採用しない）。
    白衣 など禁止複合に重なる「白」は採用しない。
    """
    raw = text or ''
    if not raw.strip():
        return []
    blocked = _blocked_spans(raw)
    # 走査は空白除去前の原文。照合は NFKC＋空白除去キーだが、
    # 位置は原文上で最長一致を探す。
    hits = []
    occupied = []
    nfkc_raw = _nfkc(raw)

    def _occupy(start, end):
        occupied.append((start, end))

    def _is_free(start, end):
        for a, b in occupied:
            if start < b and end > a:
                return False
        return True

    # nfkc_raw 上で探して raw と長さが同じ前提（空白除去はしない位置ベース）
    # 空白をまたぐ色語は辞書に無いので、NFKCのみで十分。
    search = nfkc_raw
    for term in _COLORS:
        if not term:
            continue
        start = 0
        while True:
            idx = search.find(term, start)
            if idx < 0:
                break
            end = idx + len(term)
            start = idx + 1
            if _span_overlaps_blocked(idx, end, blocked):
                continue
            if not _is_free(idx, end):
                continue
            # 語境界風: 直前直後が同じ色語の続き（より長い色の一部）になっていないか
            # 長い語を先に処理しているので、既占有で短い方は弾かれる。
            display = _COLOR_DISPLAY.get(term) or term
            # 原文スライス（NFKC前後で長さが変わらない範囲を想定）
            orig_slice = raw[idx:end] if len(raw) == len(search) else display
            hits.append({
                'value': orig_slice if orig_slice else display,
                'matched': term,
                'display': display,
                'start': idx,
                'end': end,
                'key': term,
            })
            _occupy(idx, end)
    hits.sort(key=lambda h: h['start'])
    return hits


def _outside_paren_text_spans(name: str):
    """括弧外の原文区間リスト (start, end, text)。壊れた括弧は全体を外側扱い。"""
    text = name or ''
    if not paren_balance_ok(text):
        return [(0, len(text), text)]
    spans = []
    last = 0
    for m in _PAREN_PAIR_RE.finditer(text):
        if m.start() > last:
            spans.append((last, m.start(), text[last:m.start()]))
        last = m.end()
    if last < len(text):
        spans.append((last, len(text), text[last:]))
    return spans


def extract_explicit_color_from_name(product_name: str):
    """商品名から明示色を1つ（または複数候補）取り出す。

    括弧内の色分類と、括弧外の辞書色の両方を見る。
    Returns dict: color, colorKey, colorCandidates, colorSource,
      outsideHits, parenColor, needsConfirm, confirmReasons
    """
    original = (product_name or '').strip()
    empty = {
        'color': '',
        'colorKey': '',
        'colorCandidates': [],
        'colorSource': '',
        'outsideHits': [],
        'parenColor': '',
        'needsConfirm': False,
        'confirmReasons': [],
        'hasExplicitColor': False,
    }
    if not original:
        return empty

    paren = classify_product_name(original)
    paren_colors = list(paren.get('colorCandidates') or [])
    paren_color = (paren.get('color') or '').strip()
    # classify が複数色で color='' のとき candidates に残る
    if not paren_color and len(paren_colors) == 1:
        paren_color = paren_colors[0]

    outside_hits = []
    for _a, _b, seg in _outside_paren_text_spans(original):
        for hit in find_explicit_colors_in_text(seg):
            outside_hits.append(hit)

    # 重複キー除去（出現順）
    outside_unique = []
    seen_out = set()
    for h in outside_hits:
        if h['key'] in seen_out:
            continue
        seen_out.add(h['key'])
        outside_unique.append(h)

    reasons = []
    needs = False
    color = ''
    source = ''
    candidates = []

    if len(paren_colors) > 1:
        needs = True
        reasons.append('括弧内の色が複数あるため要確認: ' + '、'.join(paren_colors))
        candidates.extend(paren_colors)
    elif paren_color:
        color = paren_color
        source = 'paren'
        candidates.append(paren_color)

    if len(outside_unique) > 1:
        needs = True
        vals = [h['display'] for h in outside_unique]
        reasons.append('括弧外の色が複数あるため要確認: ' + '、'.join(vals))
        for v in vals:
            if v not in candidates:
                candidates.append(v)
    elif len(outside_unique) == 1:
        out_val = outside_unique[0]['value'] or outside_unique[0]['display']
        out_key = outside_unique[0]['key']
        if color:
            if color_match_key(color) != out_key:
                needs = True
                reasons.append(
                    f'括弧内の色「{color}」と括弧外の色「{out_val}」が異なるため要確認'
                )
                if out_val not in candidates:
                    candidates.append(out_val)
                color = ''
                source = ''
            # 同じ色なら paren を優先
        else:
            color = out_val
            source = 'outside'
            if out_val not in candidates:
                candidates.append(out_val)

    if needs:
        color = '' if len({color_match_key(c) for c in candidates if color_match_key(c)}) != 1 else color
        # 要確認時は単一に決めない
        if len({color_match_key(c) for c in candidates if c}) > 1:
            color = ''
            source = ''

    return {
        'color': color,
        'colorKey': color_match_key(color) if color else '',
        'colorCandidates': list(candidates),
        'colorSource': source,
        'outsideHits': outside_unique,
        'parenColor': paren_color,
        'needsConfirm': needs,
        'confirmReasons': reasons,
        'hasExplicitColor': bool(color) or (len(candidates) == 1 and not needs),
        'parenResult': paren,
    }


# classify 内で stock を import しないよう、色表示の正規化は自前
_COLOR_CODE_PREFIX_RE = re.compile(r'(\d{2,3})\.\s*$')


def _strip_outside_color_once(name: str, hit) -> str:
    """括弧外の色ヒット1件を商品名から除く（禁止複合・括弧内は触らない）。

    直前の「046.」形式は色コード接頭として色と一緒に除く（数字だけを色とは断定しない）。
    """
    text = name or ''
    if not hit:
        return text
    # 括弧外スパン内でのみ置換
    pieces = []
    last = 0
    removed = False
    for a, b, seg in _outside_paren_text_spans(text):
        pieces.append(text[last:a])
        if removed:
            pieces.append(seg)
        else:
            # seg 内で同じ matched を最長一致で1回だけ除去
            local_hits = find_explicit_colors_in_text(seg)
            target_key = hit.get('key') or ''
            out_seg = seg
            for lh in local_hits:
                if lh['key'] == target_key:
                    start = lh['start']
                    end = lh['end']
                    # 色の直前にある NN. / NNN. を色コード接頭として一緒に除去
                    prefix = seg[:start]
                    m = _COLOR_CODE_PREFIX_RE.search(prefix)
                    if m:
                        start = m.start()
                    out_seg = seg[:start] + seg[end:]
                    removed = True
                    break
            pieces.append(out_seg)
        last = b
    pieces.append(text[last:])
    merged = ''.join(pieces)
    merged = re.sub(r'[\s\u3000]{2,}', ' ', merged).strip()
    # 括弧直前の余分な空白・末尾の区切り
    merged = re.sub(r'[\s\u3000]+([（(])', r'\1', merged)
    merged = re.sub(r'[\s\u3000・･/／]+$', '', merged)
    return merged.strip()


def split_name_and_color(product_name: str):
    """登録用に商品名と色を振り分ける。

    - 括弧内色: 既存 classify の plannedName / color を基本に使う
    - 括弧外の明示色: 辞書＋境界で取り、商品名から除去（白衣の白は除去しない）
    - 加工・素材・未分類は classify 結果を維持（消さない）
    - 数値色コードは辞書に無い限り触らない
    """
    original = (product_name or '').strip()
    paren = classify_product_name(original)
    extracted = extract_explicit_color_from_name(original)
    color = extracted.get('color') or ''
    reasons = list(paren.get('confirmReasons') or [])
    for r in extracted.get('confirmReasons') or []:
        if r not in reasons:
            reasons.append(r)
    needs = bool(paren.get('needsConfirm') or extracted.get('needsConfirm'))

    planned = paren.get('plannedName') or original
    source = extracted.get('colorSource') or ''

    if extracted.get('colorSource') == 'outside' and color and not extracted.get('needsConfirm'):
        # 括弧分類が自動で planned を作れている場合は、その planned から外側色を除く
        base_for_strip = planned if (
            paren.get('autoClassified') or (paren.get('color') or paren.get('processing') or paren.get('material'))
        ) and not paren.get('needsConfirm') else original
        # 未分類などで原文維持のときは、外側色だけ除いた名を予定名にする
        if paren.get('needsConfirm') and paren.get('keptOriginal'):
            base_for_strip = original
        hits = extracted.get('outsideHits') or []
        hit = next((h for h in hits if h.get('key') == extracted.get('colorKey')), None)
        if hit:
            stripped = _strip_outside_color_once(base_for_strip, hit)
            # 括弧分類で加工等を抜いた planned を使うときは、外側色除去後に再 classify しない
            if stripped:
                planned = stripped
            if not planned:
                planned = original
                needs = True
                reasons.append('色除去後の商品名が空になるため原文のまま要確認')
                color = ''
                source = ''

    # 括弧内色のみで auto のときは planned は classify 済み
    if extracted.get('colorSource') == 'paren' and color and not paren.get('needsConfirm'):
        planned = paren.get('plannedName') or planned

    # 自動確定: 括弧分類が自動、または括弧外の明示色を一意に振り分けられたとき
    final_planned = planned or original
    if not final_planned:
        needs = True
        reasons.append('色除去後の商品名が空になるため原文のまま要確認')
        final_planned = original
        color = ''
        source = ''
    auto = (not needs) and bool(
        paren.get('autoClassified')
        or (
            color
            and source == 'outside'
            and final_planned
            and final_planned != original
        )
        or (
            color
            and source == 'paren'
            and final_planned
            and not paren.get('needsConfirm')
        )
    )
    change_summary = f"商品名から色『{color}』を設定します" if color else ''

    return {
        'originalName': original,
        'sourceName': original,
        'plannedName': final_planned,
        'baseName': paren.get('baseName') or final_planned,
        'color': color,
        'colorKey': color_match_key(color) if color else '',
        'colorSource': source,
        'processing': paren.get('processing') or '',
        'material': paren.get('material') or '',
        'unclassified': list(paren.get('unclassified') or []),
        'needsConfirm': needs,
        'confirmReasons': reasons,
        'autoClassified': bool(auto and not needs),
        'parenContents': list(paren.get('parenContents') or []),
        'segments': list(paren.get('segments') or []),
        'keptOriginal': final_planned == original,
        'colorCandidates': list(extracted.get('colorCandidates') or paren.get('colorCandidates') or []),
        'processingCandidates': list(paren.get('processingCandidates') or []),
        'materialCandidates': list(paren.get('materialCandidates') or []),
        'hasExplicitColor': bool(color),
        'changeSummary': change_summary,
        'outsideHits': list(extracted.get('outsideHits') or []),
    }


def extend_color_terms(extra):
    """テスト・拡張用（実行時辞書への追加。行編集の自動昇格には使わない）。"""
    global _COLORS, _COLOR_DISPLAY
    for t in extra or []:
        k = _norm_token(t)
        if k:
            _COLOR_DISPLAY[k] = t
    _COLORS = _sorted_terms(list(_COLOR_DISPLAY.values()))


def extend_processing_terms(extra):
    global _PROCESSINGS, _PROCESSING_DISPLAY
    for t in extra or []:
        k = _norm_token(t)
        if k:
            _PROCESSING_DISPLAY[k] = t
    _PROCESSINGS = _sorted_terms(list(_PROCESSING_DISPLAY.values()))


def extend_material_terms(extra):
    global _MATERIALS, _MATERIAL_DISPLAY
    for t in extra or []:
        k = _norm_token(t)
        if k:
            _MATERIAL_DISPLAY[k] = t
    _MATERIALS = _sorted_terms(list(_MATERIAL_DISPLAY.values()))
