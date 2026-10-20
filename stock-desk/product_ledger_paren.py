"""旧商品台帳CSVの商品名括弧分類（色・加工・生地／素材）。

AI・外部サービスは使わない。明示辞書＋規則のみ。
括弧外の色・加工らしき文言は移動・削除しない（本機能の対象外）。
行ごとの手編集をグローバル規則へ自動昇格しない。
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
