"""旧商品台帳CSVの商品名括弧分類（色・加工・生地／素材）。

AI・外部サービスは使わない。明示辞書＋規則のみ。
括弧内の分類に加え、候補選定・登録時は括弧外の明示色も辞書＋境界規則で抽出する。
色に見えるが商品種の一部である語（例: 白衣の「白」）は色として切り出さない。
日本語・ひらがな・半角カナの色名は COLOR_SYNONYMS で全角カタカナ代表へ統一する。
緑 と 蛍光グリーン、ブルー と ライトブルー／サックスは別色（同一視しない）。
行ごとの手編集をグローバル規則へ自動昇格しない。
数値だけの「色コード」は根拠なく色と断定・削除しない。
"""
from __future__ import annotations

import re
import unicodedata

# 長い語を先に照合するため、定義順は長さ降順でソートして使う。
# 緑 と 蛍光グリーンは別エントリ（同一視しない）。
# ライトブルー は ブルー と別色（この規則だけでは同一視しない）。
# 代表表記は全角カタカナ（日本語・ひらがな・読みカナは COLOR_SYNONYMS で寄せる）。
# 水色はライトブルーと別色のため代表のまま残す。グレイはグレーへ寄せる。
COLOR_TERMS = (
    '蛍光グリーン', '蛍光イエロー', '蛍光オレンジ', '蛍光ピンク',
    'ライトブルー', 'ネイビー', 'ブラック', 'ホワイト', 'レッド', 'ブルー',
    'グリーン', 'イエロー', 'オレンジ', 'ピンク', 'グレー',
    'ブラウン', 'ベージュ', 'シルバー', 'ゴールド', 'サックス', 'チャコール',
    'カーキ', 'キャメル', 'ワイン', 'エンジ', 'アイボリー', 'ターコイズ',
    'バイオレット',
    '水色',
)

# 誤記・別名 → 代表色。明示エントリのみ（「ミスト」を機械的に全除去しない）。
# 類似度だけでは自動統一しない。ブルー↔ライトブルー／サックスはここに入れない。
# 日本語・ひらがな・カタカナ読み → 登録・表示用の全角カタカナ代表へ統一。
COLOR_SYNONYMS = {
    'バイオレッド': 'バイオレット',
    'ミストバイオレッド': 'バイオレット',
    'ミストバイオレット': 'バイオレット',
    # 白
    '白': 'ホワイト',
    'しろ': 'ホワイト',
    'シロ': 'ホワイト',
    # 黒
    '黒': 'ブラック',
    'くろ': 'ブラック',
    'クロ': 'ブラック',
    # 赤
    '赤': 'レッド',
    'あか': 'レッド',
    'アカ': 'レッド',
    # 青（ライトブルー・サックスとは別色のまま）
    '青': 'ブルー',
    'あお': 'ブルー',
    'アオ': 'ブルー',
    # 緑（蛍光グリーンとは別色のまま）
    '緑': 'グリーン',
    'みどり': 'グリーン',
    'ミドリ': 'グリーン',
    # 黄
    '黄': 'イエロー',
    'きいろ': 'イエロー',
    'キイロ': 'イエロー',
    # 茶
    '茶': 'ブラウン',
    'ちゃ': 'ブラウン',
    'チャ': 'ブラウン',
    # 灰／グレイ
    '灰': 'グレー',
    'グレイ': 'グレー',
    'はいいろ': 'グレー',
    'ハイイロ': 'グレー',
    # 銀・金
    '銀': 'シルバー',
    'ぎん': 'シルバー',
    'ギン': 'シルバー',
    '金': 'ゴールド',
    'きん': 'ゴールド',
    'キン': 'ゴールド',
    # 紫・橙・桃・紺（水色はライトブルーと別扱いのまま代表にしない）
    '紫': 'バイオレット',
    'むらさき': 'バイオレット',
    'ムラサキ': 'バイオレット',
    '橙': 'オレンジ',
    'だいだい': 'オレンジ',
    'ダイダイ': 'オレンジ',
    '桃': 'ピンク',
    'もも': 'ピンク',
    'モモ': 'ピンク',
    '紺': 'ネイビー',
    'こん': 'ネイビー',
    'コン': 'ネイビー',
}

# 色文字を含むが色ではない複合語（切り出し禁止・機械置換禁止）。長い語を先に見る。
_COLOR_COMPOUND_BLOCKLIST = (
    '白衣',
    '赤外線',
)

PROCESSING_TERMS = (
    'マジックテープ', 'スナッパー', 'コーティング', 'アイロン',
    '裾上げ', '裾直し', '丈直し', 'サイズダウン', 'ワッペン', 'ネーム', '刺繍', 'プリント',
    '反射', 'カット', '縫製', '加工',
)

# 加工は「DHC刺繍あり」「背中プリント」のように語を含む複合も可（完全一致優先）。
PROCESSING_COMPOUND_OK = True

# パンツ系の一般記載「丈加工」は統合判定・加工欄から除外する。
# 商品種ヒント（product_ledger_import._KIND_HINT_RE）と同じパンツ語を使う。
# 股下寸法・仕上がり寸法などの具体数値は対象外（削除しない）。
PANTS_KIND_MARKERS = ('パンツ', 'スラックス', 'ズボン')
HEM_PROCESSING_LITERAL = '丈加工'
# 括弧内外・空白差を吸収した「丈加工」フレーズ
_HEM_PHRASE_RE = re.compile(r'丈[\s\u3000]*加[\s\u3000]*工')

MATERIAL_TERMS = (
    'クールマックス', 'ポリエステル', 'ナイロン', 'ウール',
    '綿100％', '綿100%', '綿100パーセント', '麻混', 'デニム',
    'ツイル', 'オックス', '綿', '吸汗', '速乾', '防炎', '難燃', '制電', '透湿',
)

# ・や、は複数候補分割。/／ は配色つなぎなのでここでは分割しない
# （配色判定後、非色混在時だけ /／ で再分割する）。
_SEGMENT_SPLIT_RE = re.compile(r'[・･、,，&＆]+')
_SLASH_SPLIT_RE = re.compile(r'[/／]+')
_PAREN_PAIR_RE = re.compile(r'[（(]([^（）()]*)[）)]')
# 配色のつなぎ（サックス×グレー、黒／ワイン、ネイビー*グレー）。
# ・は複数色分割のまま。先頭の中古標記 ＊/* は bridge 判定の対象外（色ヒット間だけ見る）。
_COLORWAY_JOIN_CHARS = '×✕ｘxX*＊/／'
_COLORWAY_BRIDGE_RE = re.compile(
    rf'^[{re.escape(_COLORWAY_JOIN_CHARS)}]+$'
)
_HALFWIDTH_KATAKANA_RE = re.compile(r'[\uff61-\uff9f]+')
# 色と色の間の配色区切り（比較・登録とも × へ統一）。先頭の中古標記 *／＊ は含めない。
_COLORWAY_SEP_CHARS = '/／×✕*＊'
_COLORWAY_SEP_NORMALIZE_RE = re.compile(
    rf'[\s\u3000]*[{re.escape(_COLORWAY_SEP_CHARS)}][{re.escape(_COLORWAY_SEP_CHARS)}\s\u3000]*'
)
_COLORWAY_SPLIT_RE = re.compile(rf'[{re.escape(_COLORWAY_SEP_CHARS)}]+')


def _nfkc(value: str) -> str:
    return unicodedata.normalize('NFKC', value or '')


def _norm_token(value: str) -> str:
    """照合用。空白除去・NFKC。％/% は両方残すため NFKC 後もそのまま。"""
    t = _nfkc(value)
    t = re.sub(r'[\s\u3000]+', '', t)
    return t


def _sorted_terms(terms):
    return sorted({_norm_token(t) for t in terms if _norm_token(t)}, key=len, reverse=True)


def _rebuild_color_tables():
    """COLOR_TERMS / COLOR_SYNONYMS から照合表を組み立てる。"""
    global _COLORS, _COLOR_DISPLAY, _COLOR_MATCH_TERMS
    global _COLOR_SYNONYM_BY_KEY, _COLOR_SYNONYM_PAIRS
    _COLOR_DISPLAY = {_norm_token(t): t for t in COLOR_TERMS}
    _COLOR_SYNONYM_BY_KEY = {}
    pairs = []
    for syn, canon in COLOR_SYNONYMS.items():
        syn_k = _norm_token(syn)
        canon_t = (canon or '').strip()
        if not syn_k or not canon_t:
            continue
        _COLOR_SYNONYM_BY_KEY[syn_k] = canon_t
        # 表示用原文キーも残す（unify 後の置換用）
        pairs.append((syn.strip(), canon_t))
        pairs.append((syn_k, canon_t))
    # 長い別名から置換（ミストバイオレッド を レッド より先に）
    _COLOR_SYNONYM_PAIRS = sorted(
        {(a, b) for a, b in pairs if a and b and a != b},
        key=lambda x: len(x[0]),
        reverse=True,
    )
    # 検索対象: 辞書色＋別名（長い語優先）
    match_keys = set(_COLOR_DISPLAY.keys()) | set(_COLOR_SYNONYM_BY_KEY.keys())
    _COLOR_MATCH_TERMS = sorted(match_keys, key=len, reverse=True)
    _COLORS = _sorted_terms(list(_COLOR_DISPLAY.values()))


_PROCESSINGS = _sorted_terms(PROCESSING_TERMS)
_MATERIALS = _sorted_terms(MATERIAL_TERMS)

# 表示用（正規化キー → 辞書の代表表記）
_COLOR_DISPLAY = {}
_COLOR_MATCH_TERMS = []
_COLOR_SYNONYM_BY_KEY = {}
_COLOR_SYNONYM_PAIRS = []
_COLORS = []
_rebuild_color_tables()

_PROCESSING_DISPLAY = {_norm_token(t): t for t in PROCESSING_TERMS}
_MATERIAL_DISPLAY = {_norm_token(t): t for t in MATERIAL_TERMS}


def _display_for(kind: str, key: str, original: str) -> str:
    if kind == 'color':
        if key in _COLOR_SYNONYM_BY_KEY:
            return _COLOR_SYNONYM_BY_KEY[key]
        return _COLOR_DISPLAY.get(key) or original
    if kind == 'processing':
        return _PROCESSING_DISPLAY.get(key) or original
    if kind == 'material':
        return _MATERIAL_DISPLAY.get(key) or original
    return original


def canonical_color_display(value: str) -> str:
    """単色トークンを辞書の代表表記へ。未知・非辞書はそのまま（類似度では寄せない）。"""
    t = unify_import_notation(value)
    if not t:
        return ''
    key = _norm_token(t)
    if not key:
        return ''
    if key in _COLOR_SYNONYM_BY_KEY:
        return _COLOR_SYNONYM_BY_KEY[key]
    if key in _COLOR_DISPLAY:
        return _COLOR_DISPLAY[key]
    return t


def apply_color_synonyms_in_text(value: str) -> str:
    """文字列中の明示色別名だけを代表表記へ置換（長い語優先）。

    ミストの機械除去はしない。白衣・赤外線・白下など禁止区間に重なる文字は置換しない。
    """
    t = value or ''
    if not t:
        return ''
    # 禁止区間をプレースホルダで保護（白→ホワイトが白衣／白下を壊さない）
    # 長い区間を優先して非重複に選び、後ろから置換して位置ずれを避ける。
    occupied = []
    chosen = []
    for start, end in sorted(
        _blocked_spans(t),
        key=lambda s: (-(s[1] - s[0]), s[0]),
    ):
        if any(start < b and end > a for a, b in occupied):
            continue
        occupied.append((start, end))
        chosen.append((start, end))
    placeholders = {}
    for i, (start, end) in enumerate(sorted(chosen, key=lambda s: s[0], reverse=True)):
        token = f'\ufffcC{i}\ufffc'
        placeholders[token] = t[start:end]
        t = t[:start] + token + t[end:]
    for syn, canon in _COLOR_SYNONYM_PAIRS:
        if syn and canon and syn in t:
            t = t.replace(syn, canon)
    for token, original in placeholders.items():
        t = t.replace(token, original)
    return t


def normalize_color_value(value: str) -> str:
    """色・配色の共有正規化（表記統一＋区切り＋色辞書）。照合・登録表示の双方で使う。

    配色区切り（/／×✕*＊）は、各要素が色名と確認できるときだけ × へ統一する。
    色+サイズ・色+加工などは配色にしない。先頭の中古標記 * は色欄入力には来ない想定。
    """
    t = unify_import_notation(value)
    if not t:
        return ''
    if any(ch in t for ch in _COLORWAY_SEP_CHARS):
        if looks_like_colorway_text(t):
            return normalize_colorway_display(t)
        return t
    return canonical_color_display(t)


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


def name_looks_like_pants(name: str) -> bool:
    """パンツ／スラックス／ズボン系の商品名か（既存の商品種ヒント語を利用）。"""
    t = _nfkc(name or '')
    if not t:
        return False
    return any(marker in t for marker in PANTS_KIND_MARKERS)


def is_hem_processing_segment(text: str) -> bool:
    """一般記載『丈加工』か。股下72・仕上がり丈70 などは False。"""
    key = _norm_token(text)
    return bool(key) and key == _norm_token(HEM_PROCESSING_LITERAL)


def strip_hem_processing_from_text(text: str) -> str:
    """括弧内外の『丈加工』を除去し、空括弧・余分空白・余分な区切りを整理する。"""
    t = text if text is not None else ''
    if not t or not _HEM_PHRASE_RE.search(t):
        return t

    def _paren_repl(m):
        inner = m.group(1)
        open_b = m.group(0)[0]
        close_b = m.group(0)[-1]
        parts = [p.strip() for p in _SEGMENT_SPLIT_RE.split(inner) if p.strip()]
        if not parts:
            raw_inner = (inner or '').strip()
            parts = [raw_inner] if raw_inner else []
        kept = [p for p in parts if not is_hem_processing_segment(p)]
        if not kept:
            return ''
        if len(kept) == len(parts) and '・'.join(kept) == '・'.join(parts):
            # 丈加工以外はそのまま（区切りを推測し直さない）
            return m.group(0)
        return f'{open_b}{"・".join(kept)}{close_b}'

    t = _PAREN_PAIR_RE.sub(_paren_repl, t)
    # 括弧外に残った『丈加工』フレーズを除去
    t = _HEM_PHRASE_RE.sub('', t)
    # 空括弧・連続区切り・余分空白
    t = re.sub(r'[（(][\s\u3000]*[）)]', '', t)
    t = re.sub(r'[・･]{2,}', '・', t)
    t = re.sub(r'[\s\u3000]*[・･][\s\u3000]*$', '', t)
    t = re.sub(r'^[\s\u3000]*[・･][\s\u3000]*', '', t)
    t = re.sub(r'[\s\u3000]{2,}', ' ', t)
    t = re.sub(r'[\s\u3000]+([（(])', r'\1', t)
    return t.strip()


def strip_pants_hem_processing(name: str) -> str:
    """パンツ系なら『丈加工』を名から除去。非パンツはそのまま。"""
    original = name if name is not None else ''
    if not original or not name_looks_like_pants(original):
        return original
    return strip_hem_processing_from_text(original)


def halfwidth_katakana_to_fullwidth(text: str) -> str:
    """半角カタカナだけを全角へ（濁点・半濁点は NFKC で合成）。他記号は触らない。"""
    t = text or ''
    if not t:
        return t
    return _HALFWIDTH_KATAKANA_RE.sub(
        lambda m: unicodedata.normalize('NFKC', m.group(0)),
        t,
    )


# 後方互換エイリアス
_halfwidth_katakana_to_fullwidth = halfwidth_katakana_to_fullwidth


def unify_import_notation(value: str) -> str:
    """取込用の表記統一。分類・位置検出・登録表示の前に必ず適用する。

    - 半角カタカナ → 全角（濁点・半濁点を正しく合成）
    - 全角/半角空白 → 半角空白、連続を1つに、trim
    - ASCII・数字・SKU は全角化しない（文字列全体への NFKC はしない）
    - スラッシュ有無・エラー有無にかかわらず適用（冪等）
    """
    t = value or ''
    if not t:
        return ''
    t = halfwidth_katakana_to_fullwidth(t)
    t = re.sub(r'[\s\u3000]+', ' ', t).strip()
    return t


# 名中の配色つなぎ（先頭中古標記を除いたあとの /／×✕*＊）
_NAME_COLORWAY_SEP_RE = re.compile(
    rf'[\s\u3000]*[{re.escape(_COLORWAY_SEP_CHARS)}][{re.escape(_COLORWAY_SEP_CHARS)}\s\u3000]*'
)


def _is_known_color_token(value: str) -> bool:
    """単色トークンが辞書色（代表または別名）か。配色や複合未知語は False。"""
    t = unify_import_notation(value)
    if not t or any(ch in t for ch in _COLORWAY_SEP_CHARS):
        return False
    key = _norm_token(t)
    if not key:
        return False
    return key in _COLOR_SYNONYM_BY_KEY or key in _COLOR_DISPLAY


def _colorway_sep_runs(value: str):
    """配色区切りで割った断片と、断片間の区切り有無を返す。"""
    t = value or ''
    if not t:
        return [], False
    parts = _COLORWAY_SPLIT_RE.split(t)
    has_sep = bool(_COLORWAY_SPLIT_RE.search(t))
    return [p.strip() for p in parts], has_sep


def _part_is_color_span(part: str) -> bool:
    """1断片が辞書単色、または辞書色の直結複合（シルバーグレー等）か。"""
    if _is_known_color_token(part):
        return True
    t = unify_import_notation(part)
    if not t or any(ch in t for ch in _COLORWAY_SEP_CHARS):
        return False
    hits = find_explicit_colors_in_text(t)
    if len(hits) != 1:
        return False
    hit = hits[0]
    return int(hit.get('start') or 0) == 0 and int(hit.get('end') or 0) == len(t)


def looks_like_colorway_text(value: str) -> bool:
    """区切った各要素が色として確認できるときだけ配色テキストとみなす。

    色+サイズ（ホワイト／フリー）・色+加工（ホワイト／刺繍）・非色（冬／夏）は False。
    直結複合色（サックス×シルバーグレー）は True。順序の入れ替えはしない。
    """
    t = unify_import_notation(value)
    if not t or not any(ch in t for ch in _COLORWAY_SEP_CHARS):
        return False
    parts, has_sep = _colorway_sep_runs(t)
    parts = [p for p in parts if p]
    if not has_sep or len(parts) < 2:
        return False
    return all(_part_is_color_span(p) for p in parts)


def normalize_name_for_color_compare(value: str) -> str:
    """同一SKU内の名照合用。中古標記除去＋表記統一＋配色区切り＋色辞書。

    先頭の中古標記は比較から外す（表示用原文は呼び出し側で保持）。
    色間の /／×✕*＊ は、両側が辞書色の配色区間だけ比較上 × へそろえる
    （中古標記の先頭 * とは区別。色+サイズ／色+加工は触らない）。
    類似度では寄せない。SKU・ASCII は全角化しない。
    """
    t = (value or '').strip()
    if not t:
        return ''
    # 先頭中古標記（legacy と同じ規則を軽量に）
    m = re.match(r'^[\s\u3000]*[*＊]+[\s\u3000]*(.*)$', t)
    if m:
        t = (m.group(1) or '').strip()
    t = unify_import_notation(t)
    if not t:
        return ''
    t = unify_known_colorway_seps_in_text(t)
    t = apply_color_synonyms_in_text(t)
    return t


def unify_known_colorway_seps_in_text(value: str) -> str:
    """空白区切りトークン単位で、辞書色のみの配色区間の区切りを × へそろえる。"""
    t = value or ''
    if not t or not any(ch in t for ch in _COLORWAY_SEP_CHARS):
        return t
    # 空白でトークン分割し、各トークンが配色なら正規化（名の他部分は維持）
    pieces = re.split(r'([\s\u3000]+)', t)
    out = []
    for piece in pieces:
        if not piece or re.fullmatch(r'[\s\u3000]+', piece):
            out.append(piece)
            continue
        if looks_like_colorway_text(piece):
            out.append(normalize_colorway_display(piece))
        else:
            # 括弧付き「（黒／ワイン）」など
            def _paren_repl(m):
                inner = m.group(1)
                open_b, close_b = m.group(0)[0], m.group(0)[-1]
                if looks_like_colorway_text(inner):
                    return f'{open_b}{normalize_colorway_display(inner)}{close_b}'
                return m.group(0)
            out.append(_PAREN_PAIR_RE.sub(_paren_repl, piece))
    return ''.join(out)


def normalize_colorway_display(value: str) -> str:
    """配色の登録表示: 表記統一、区切りを × へ統一、成分ごとの色辞書適用。

    - / ／ × ✕ * ＊ → ×（色間の配色つなぎ。先頭中古標記の * とは別）
    - 各成分を COLOR_SYNONYMS / COLOR_TERMS の代表へ
    - 順序は維持（入れ替えない）
    """
    t = unify_import_notation(value)
    if not t:
        return ''
    if not any(ch in t for ch in _COLORWAY_SEP_CHARS):
        return canonical_color_display(t) or t
    t = _COLORWAY_SEP_NORMALIZE_RE.sub('×', t)
    t = t.strip('×').strip()
    if not t:
        return ''
    parts = [p.strip() for p in re.split(r'×+', t) if p.strip()]
    if len(parts) <= 1:
        return canonical_color_display(parts[0] if parts else t) or (parts[0] if parts else t)
    canon_parts = []
    for part in parts:
        c = canonical_color_display(part)
        canon_parts.append(c if c else part.strip())
    return '×'.join(canon_parts)


def _is_colorway_bridge(between: str) -> bool:
    """色ヒット間が配色として1色にまとめてよいか。

    - 空（辞書色の直結: シルバーグレー）→ まとめる
    - × / * ＊ /／ 系のみ（空白可）→ 配色1色（登録表示は × に正規化）
    - 空白だけ・など → まとめない（別色の並び）
    """
    if between == '':
        return True
    compact = re.sub(r'[\s\u3000]+', '', between or '')
    if not compact:
        return False
    return bool(_COLORWAY_BRIDGE_RE.fullmatch(compact))


def coalesce_colorway_hits(raw: str, hits):
    """隣接／×／スラッシュ接続の辞書色ヒットを配色1色へまとめる。

    例: サックス×シルバーグレー → 1ヒット（シルバー＋グレーの直結も含む）。
    例: 黒／ワイン・紺/白/赤 → 1ヒット（区切りは × に正規化）。
    ・で区切られた複数色はまとめない。
    """
    if not hits:
        return []
    ordered = sorted(hits, key=lambda h: (h.get('start', 0), h.get('end', 0)))
    if len(ordered) == 1:
        return list(ordered)
    text = raw or ''
    groups = [[ordered[0]]]
    for nxt in ordered[1:]:
        prev = groups[-1][-1]
        between = text[int(prev['end']):int(nxt['start'])]
        if _is_colorway_bridge(between):
            groups[-1].append(nxt)
        else:
            groups.append([nxt])
    out = []
    for group in groups:
        if len(group) == 1:
            hit = dict(group[0])
            # 単色も代表表記へ（別名→辞書色）。原文スライスは sourceColor に残す。
            src = hit.get('sourceColor') or hit.get('value') or ''
            display = hit.get('display') or canonical_color_display(src) or src
            hit['sourceColor'] = src
            hit['display'] = display
            hit['value'] = display
            hit['key'] = _norm_token(display) or hit.get('key') or ''
            out.append(hit)
            continue
        start = int(group[0]['start'])
        end = int(group[-1]['end'])
        raw_value = text[start:end]
        value = normalize_colorway_display(raw_value)
        key = _norm_token(value)
        out.append({
            'value': value,
            'matched': key,
            'display': value,
            'sourceColor': raw_value,
            'start': start,
            'end': end,
            'key': key,
            'isColorway': True,
        })
    return out


def _classify_as_pure_color_span(raw: str):
    """セグメント全体が辞書色の直結・×配色・スラッシュ配色なら単一色として返す。

    位置検出・長さ比較は unify 後の同一文字列上で行う。
    """
    text = unify_import_notation(raw)
    if not text:
        return None
    hits = find_explicit_colors_in_text(text)
    if len(hits) != 1:
        return None
    hit = hits[0]
    if int(hit.get('start') or 0) != 0 or int(hit.get('end') or 0) != len(text):
        return None
    value = hit.get('value') or text
    if hit.get('isColorway') or any(ch in text for ch in '/／×✕*＊'):
        value = normalize_colorway_display(value)
    else:
        value = canonical_color_display(value) or value
    return {
        'kind': 'color',
        'value': value,
        'matched': hit.get('key') or _norm_token(value),
        'original': raw,
        'isColorway': bool(hit.get('isColorway') or any(ch in text for ch in '/／×✕*＊')),
    }


def classify_paren_segment(segment: str, *, pants: bool = False):
    """1セグメントを色／加工／素材／不明に分類。

    部分文字列だけで色を取らない（例: 蛍光グリーン の中の 緑 は使わない）。
    表示・照合は表記統一後の値を使う（％/% など ASCII 記号は全角化しない）。
    サックス×シルバーグレー・黒／ワイン のような配色は1色として扱う。
    パンツ系の一般『丈加工』は加工欄へ入れず、名から落とす印（hem_ignore）にする。
    """
    source = segment if segment is not None else ''
    raw = unify_import_notation(source)
    if not raw:
        return None
    key = _norm_token(raw)
    if not key:
        return None

    # 完全一致を優先（長い語が先）。別名は代表色へ。
    if key in _COLOR_SYNONYM_BY_KEY:
        canon = _COLOR_SYNONYM_BY_KEY[key]
        return {
            'kind': 'color',
            'value': canon,
            'matched': _norm_token(canon),
            'original': source,
            'sourceColor': raw,
        }
    color = _exact_in(_COLORS, key)
    if color:
        return {
            'kind': 'color',
            'value': _COLOR_DISPLAY.get(color) or raw,
            'matched': color,
            'original': source,
        }
    # 配色・辞書色の直結（括弧内の サックス×グレー / 黒／ワイン / ネイビー*グレー）
    colorway = _classify_as_pure_color_span(raw)
    if colorway:
        colorway = dict(colorway)
        colorway['original'] = source
        colorway['sourceColor'] = raw
        return colorway
    material = _exact_in(_MATERIALS, key)
    if material:
        return {
            'kind': 'material',
            'value': raw,
            'matched': material,
            'original': source,
        }
    # パンツの一般『丈加工』は加工にも未分類にもしない（括弧からは除去）
    if pants and is_hem_processing_segment(raw):
        return {
            'kind': 'hem_ignore',
            'value': raw,
            'matched': _norm_token(HEM_PROCESSING_LITERAL),
            'original': source,
        }
    proc_key, proc_val = _processing_match(key, raw)
    if proc_key:
        # 複合「丈加工・刺繍」は分割後に来る。単体複合で丈加工だけのときも無視
        if pants and is_hem_processing_segment(raw):
            return {
                'kind': 'hem_ignore',
                'value': raw,
                'matched': _norm_token(HEM_PROCESSING_LITERAL),
                'original': source,
            }
        return {
            'kind': 'processing',
            'value': proc_val if proc_val == raw else raw,
            'matched': proc_key,
            'original': source,
        }
    return {
        'kind': 'unknown',
        'value': raw,
        'matched': '',
        'original': source,
    }


def _classify_paren_parts(content: str, *, pants: bool = False):
    """括弧内側をセグメント分類。スラッシュ配色は1色、非色混在時は分割。"""
    source = content if content is not None else ''
    text = unify_import_notation(source)
    hits = []
    if not text:
        hits.append({
            'kind': 'unknown',
            'value': source,
            'matched': '',
            'original': source,
        })
        return hits

    # 全体が配色（× または /／）なら1色
    pure = _classify_as_pure_color_span(text)
    if pure:
        pure = dict(pure)
        pure['original'] = source
        return [pure]

    parts = [p.strip() for p in _SEGMENT_SPLIT_RE.split(text) if p.strip()]
    if not parts:
        parts = [text]

    for part in parts:
        if _SLASH_SPLIT_RE.search(part):
            pure_part = _classify_as_pure_color_span(part)
            if pure_part:
                hits.append(pure_part)
                continue
            # 色が1つでも含まれるときだけ / で割る。色が無ければ原文のまま
            # （冬／夏 などを配色扱いにせず、未分類として名に残す）
            subparts = [p.strip() for p in _SLASH_SPLIT_RE.split(part) if p.strip()]
            if len(subparts) < 2:
                hits.append(classify_paren_segment(part, pants=pants))
                continue
            sub_hits = [classify_paren_segment(sp, pants=pants) for sp in subparts]
            if not any(h and h.get('kind') == 'color' for h in sub_hits):
                hits.append(classify_paren_segment(part, pants=pants))
                continue
            # 非色混在: 配色にはせず個別分類（色は抽出、非色は名に残す等）
            hits.extend(sub_hits)
        else:
            hits.append(classify_paren_segment(part, pants=pants))
    return hits


def _paren_inner_to_keep(content: str, part_hits: list):
    """括弧1個ぶんの残す内側テキスト。None なら括弧ごと除去。

    - 分類できた部分だけ → 除去
    - 未分類だけ → 原文の内側をそのまま残す（削除・推測しない）
    - 混在 → 未分類だけを残す（区切りで安全に分割できた場合）。
      分割できない／空になる場合は原文の内側を丸ごと残す
    """
    hits = [h for h in (part_hits or []) if h]
    if not hits:
        raw = (content or '').strip()
        return raw if raw else (content if content is not None else None)
    unclassified = [h for h in hits if h.get('kind') == 'unknown']
    classified = [h for h in hits if h.get('kind') != 'unknown']
    if classified and not unclassified:
        return None
    if unclassified and not classified:
        return content
    # 混在: 未分類を落とさない。分割結果から未分類だけ再構成
    kept_vals = [h.get('value') or h.get('original') or '' for h in unclassified]
    kept_vals = [v for v in kept_vals if str(v).strip()]
    if not kept_vals:
        return content
    # 元が単一未分類＋分類の並びなら・でつなぐ（元区切りを推測しすぎない）
    return '・'.join(kept_vals)


def _rebuild_name_keeping_unclassified(original: str, keep_inners: list):
    """分類済み括弧を除き、未分類を残した予定名を組み立てる。"""
    text = original or ''
    matches = list(_PAREN_PAIR_RE.finditer(text))
    if len(matches) != len(keep_inners):
        return text
    parts = []
    last = 0
    for m, inner in zip(matches, keep_inners):
        parts.append(text[last:m.start()])
        if inner is not None:
            open_br = text[m.start()]
            close_br = text[m.end() - 1]
            parts.append(f'{open_br}{inner}{close_br}')
        last = m.end()
    parts.append(text[last:])
    merged = ''.join(parts)
    merged = re.sub(r'[\s\u3000]{2,}', ' ', merged)
    merged = re.sub(r'[\s\u3000]+([（(])', r'\1', merged)
    return merged.strip()


def classify_product_name(product_name: str):
    """商品名の括弧内容を分類する。

    未分類の括弧文言は削除せず商品名に残す。未分類だけでは要確認にしない
    （情報通知のみ）。色・加工・生地へ明確に分類できる部分は従来どおり抽出する。
    パンツ系の一般『丈加工』は加工欄に入れず予定名からも除去する（原文は originalName）。
    分類・再構成は表記統一後の同一文字列上で行い、原文は originalName に残す。

    Returns dict:
      originalName, plannedName, color, processing, material,
      unclassified (list), needsConfirm, confirmReasons, infoNotices,
      autoClassified, parenContents, segments
    """
    original = (product_name or '').strip()
    unified = unify_import_notation(original)
    pants = name_looks_like_pants(unified or original)
    empty = {
        'originalName': original,
        'plannedName': unified,
        'baseName': unified,
        'color': '',
        'processing': '',
        'material': '',
        'unclassified': [],
        'needsConfirm': False,
        'confirmReasons': [],
        'infoNotices': [],
        'autoClassified': False,
        'parenContents': [],
        'segments': [],
        'keptOriginal': unified == original,
    }
    if not original:
        return empty

    if not paren_balance_ok(unified):
        # 括弧壊れでもパンツの括弧外『丈加工』は除去を試みる（構造は触らない）
        planned_broken = strip_pants_hem_processing(unified or original) if pants else (unified or original)
        planned_broken = unify_import_notation(planned_broken) or planned_broken
        return {
            **empty,
            'needsConfirm': True,
            'confirmReasons': ['括弧の対応が取れていないため原文のまま要確認'],
            'plannedName': planned_broken or unified or original,
            'keptOriginal': True,
        }

    # 以降の位置・分割はすべて unified 上（半角カナ変換後）で行う
    contents = extract_paren_segments(unified)
    if not contents:
        # 括弧なしでもパンツの外側『丈加工』を予定名から落とす
        if pants:
            planned_out = strip_pants_hem_processing(unified)
            planned_out = unify_import_notation(planned_out) or planned_out or unified
            if planned_out != unified:
                return {
                    **empty,
                    'plannedName': planned_out,
                    'baseName': planned_out,
                    'autoClassified': True,
                    'keptOriginal': planned_out == original,
                }
        return empty

    colors = []
    processings = []
    materials = []
    unclassified = []
    segments = []
    paren_hits = []  # 括弧ごとの part hit リスト
    colorway_notices = []
    hem_ignored = False
    for content in contents:
        hits_for_paren = _classify_paren_parts(content, pants=pants)
        if not hits_for_paren:
            unc = unify_import_notation(content) or content
            unclassified.append(unc)
            hit = {
                'kind': 'unknown', 'value': unc, 'matched': '', 'original': content,
            }
            hits_for_paren = [hit]
            segments.append({'paren': content, **hit})
            paren_hits.append(hits_for_paren)
            continue
        for hit in hits_for_paren:
            if not hit:
                continue
            segments.append({'paren': content, **hit})
            kind = hit['kind']
            if kind == 'color':
                if hit['value'] not in colors:
                    colors.append(hit['value'])
                if hit.get('isColorway'):
                    notice = f"配色として1商品で登録：{hit['value']}"
                    if notice not in colorway_notices:
                        colorway_notices.append(notice)
            elif kind == 'processing':
                if hit['value'] not in processings:
                    processings.append(hit['value'])
            elif kind == 'material':
                if hit['value'] not in materials:
                    materials.append(hit['value'])
            elif kind == 'hem_ignore':
                hem_ignored = True
            else:
                if hit['value'] not in unclassified:
                    unclassified.append(hit['value'])
        paren_hits.append(hits_for_paren)

    base = strip_parens(unified)
    base = unify_import_notation(base)
    reasons = []
    info_notices = list(colorway_notices)
    needs = False

    # 複数色・複数素材は自動確定しない（商品.color / material は単一）
    # スラッシュ／× 配色は1色にまとめたあとなので、ここでは色候補が複数のときだけ要確認
    color = colors[0] if len(colors) == 1 else ''
    material = materials[0] if len(materials) == 1 else ''
    processing = '・'.join(processings) if processings else ''
    multi_color = len(colors) > 1
    multi_material = len(materials) > 1

    if multi_color:
        needs = True
        reasons.append('括弧内の色が複数あるため要確認: ' + '、'.join(colors))
        color = ''
    if multi_material:
        needs = True
        reasons.append('括弧内の生地・素材が複数あるため要確認: ' + '、'.join(materials))
        material = ''

    # 未分類は商品名に残して登録（単独ではブロックしない）
    if unclassified:
        for u in unclassified:
            label = str(u).strip() or u
            notice = f"未分類の『{label}』は商品名に残して登録します"
            if notice not in info_notices:
                info_notices.append(notice)

    if multi_color or multi_material:
        # 曖昧なときは括弧構造を崩さない（表記だけ統一）
        planned = unified
        kept = planned == original
    else:
        keep_inners = [
            _paren_inner_to_keep(content, hits)
            for content, hits in zip(contents, paren_hits)
        ]
        planned = _rebuild_name_keeping_unclassified(unified, keep_inners)
        planned = unify_import_notation(planned)
        if not planned:
            planned = unified or original
            kept = True
            needs = True
            reasons.append('括弧除去後の商品名が空になるため原文のまま要確認')
        else:
            kept = planned == original

    # パンツ: 括弧外に残った『丈加工』も予定名から除去（加工欄には載せない）
    if pants and not needs:
        stripped = strip_pants_hem_processing(planned)
        stripped = unify_import_notation(stripped) or stripped
        if stripped:
            if stripped != planned:
                hem_ignored = True
            planned = stripped
            kept = planned == original
            base = strip_pants_hem_processing(base) if base else base
            base = unify_import_notation(base) or base

    # 分類抽出できた、または未分類を名に残して確定できるとき自動
    auto = (not needs) and bool(
        colors or processings or materials or unclassified or hem_ignored
    )

    return {
        'originalName': original,
        'plannedName': planned,
        'baseName': base or planned or unified,
        'color': color,
        'processing': processing,
        'material': material,
        'unclassified': list(unclassified),
        'needsConfirm': needs,
        'confirmReasons': reasons,
        'infoNotices': info_notices,
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
    """色の同一判定用キー。

    共有正規化（半角カナ・配色区切り・色辞書）後のキー。
    緑 と 蛍光グリーン、ブルー と ライトブルーは別キー。
    類似度だけでは同一視しない。
    """
    return _norm_token(normalize_color_value(value))


def colors_equivalent(a: str, b: str) -> bool:
    ka, kb = color_match_key(a), color_match_key(b)
    if not ka or not kb:
        return False
    return ka == kb


_COLOR_POSITION_SUFFIXES = ('上', '下')


def _blocked_spans(text: str):
    """色切り出し禁止区間（白衣・赤外線・白下 など）。位置は引数テキスト上（unify 済み想定）。"""
    spans = []
    raw = text or ''
    if not raw:
        return spans
    for compound in sorted(_COLOR_COMPOUND_BLOCKLIST, key=len, reverse=True):
        # 禁止語自体も表記統一して同一文字列上で探す（位置を混ぜない）
        needle = unify_import_notation(compound) or compound
        if not needle:
            continue
        start = 0
        while True:
            idx = raw.find(needle, start)
            if idx < 0:
                break
            spans.append((idx, idx + len(needle)))
            start = idx + 1
    # 色語＋上下の密着（白下・ネイビー上）は色ヒットにしない（区分語として残す）
    for term in _COLOR_MATCH_TERMS:
        if not term:
            continue
        for pos in _COLOR_POSITION_SUFFIXES:
            needle = f'{term}{pos}'
            start = 0
            while True:
                idx = raw.find(needle, start)
                if idx < 0:
                    break
                spans.append((idx, idx + len(needle)))
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

    表記統一後の同一文字列上で検索・スライス・配色結合する。
    変換前の位置と変換後の位置を混ぜない（半角カナ＋濁点で長さが変わるため）。
    """
    raw = unify_import_notation(text)
    if not raw.strip():
        return []
    blocked = _blocked_spans(raw)
    hits = []
    occupied = []

    def _occupy(start, end):
        occupied.append((start, end))

    def _is_free(start, end):
        for a, b in occupied:
            if start < b and end > a:
                return False
        return True

    # 検索対象もスライス対象も raw（表記統一済み）で統一する。
    # 別名（ミストバイオレッド 等）も長い語優先で照合し、display/key は代表色へ。
    search = raw
    for term in _COLOR_MATCH_TERMS:
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
            # 長い語を先に処理しているので、既占有で短い方は弾かれる
            display = _display_for('color', term, term)
            key = _norm_token(display) or term
            orig_slice = raw[idx:end]
            hits.append({
                'value': orig_slice if orig_slice else display,
                'matched': term,
                'display': display,
                'sourceColor': orig_slice if orig_slice else display,
                'start': idx,
                'end': end,
                'key': key,
            })
            _occupy(idx, end)
    hits.sort(key=lambda h: h['start'])
    # サックス×グレー・黒／ワイン・ネイビー*グレー は配色1色（辞書色へ割らない）
    return coalesce_colorway_hits(raw, hits)


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
    括弧外の走査も表記統一後の同一文字列上で行う。
    Returns dict: color, colorKey, colorCandidates, colorSource,
      outsideHits, parenColor, needsConfirm, confirmReasons
    """
    original = (product_name or '').strip()
    unified = unify_import_notation(original)
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
    # 括弧外区間も unified 上（hit 位置と strip 先を一致させる）
    for _a, _b, seg in _outside_paren_text_spans(unified):
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
        out_hit = outside_unique[0]
        out_val = (
            out_hit.get('display')
            or out_hit.get('value')
            or ''
        )
        if out_hit.get('isColorway') or any(ch in (out_val or '') for ch in '/／×✕'):
            out_val = normalize_colorway_display(out_val) or out_val
        else:
            out_val = canonical_color_display(out_val) or out_val
        out_key = out_hit.get('key') or color_match_key(out_val)
        if color:
            if color_match_key(color) != out_key and color_match_key(color) != color_match_key(out_val):
                needs = True
                reasons.append(
                    f'括弧内の色「{color}」と括弧外の色「{out_val}」が異なるため要確認'
                )
                if out_val not in candidates:
                    candidates.append(out_val)
                color = ''
                source = ''
            # 同じ色なら paren を優先（括弧側も代表表記へ）
            elif color:
                color = normalize_color_value(color) or color
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
    - 加工・素材は classify 結果を維持。未分類は商品名に残す（単独では要確認にしない）
    - 数値色コードは辞書に無い限り触らない
    - 分類・除去は表記統一後の同一文字列上で行う（preview と register で同じ変換）
    """
    original = (product_name or '').strip()
    unified = unify_import_notation(original)
    paren = classify_product_name(original)
    extracted = extract_explicit_color_from_name(original)
    color = extracted.get('color') or ''
    reasons = list(paren.get('confirmReasons') or [])
    for r in extracted.get('confirmReasons') or []:
        if r not in reasons:
            reasons.append(r)
    info_notices = list(paren.get('infoNotices') or [])
    needs = bool(paren.get('needsConfirm') or extracted.get('needsConfirm'))

    planned = paren.get('plannedName') or unified
    source = extracted.get('colorSource') or ''

    if extracted.get('colorSource') == 'outside' and color and not extracted.get('needsConfirm'):
        # 括弧分類が確定できる planned（未分類残しも含む）から外側色を除く
        if not paren.get('needsConfirm') and (
            paren.get('autoClassified')
            or paren.get('color')
            or paren.get('processing')
            or paren.get('material')
            or paren.get('unclassified')
        ):
            base_for_strip = unify_import_notation(planned)
        else:
            base_for_strip = unified
        # 複数色など括弧が要確認で原文維持のときは、外側色だけ統一名から除く
        if paren.get('needsConfirm') and paren.get('keptOriginal'):
            base_for_strip = unified
        hits = extracted.get('outsideHits') or []
        hit = next((h for h in hits if h.get('key') == extracted.get('colorKey')), None)
        if not hit and len(hits) == 1:
            hit = hits[0]
        if hit:
            # 括弧外配色の表示を × 正規化（strip は unified 位置のまま）
            if hit.get('isColorway'):
                color = normalize_colorway_display(hit.get('value') or color)
            stripped = _strip_outside_color_once(base_for_strip, hit)
            stripped = unify_import_notation(stripped)
            # 括弧分類で加工等を抜いた planned を使うときは、外側色除去後に再 classify しない
            if stripped:
                planned = stripped
            if not planned:
                planned = unified or original
                needs = True
                reasons.append('色除去後の商品名が空になるため原文のまま要確認')
                color = ''
                source = ''

    # 括弧内色のみで auto のときは planned は classify 済み
    if extracted.get('colorSource') == 'paren' and color and not paren.get('needsConfirm'):
        planned = paren.get('plannedName') or planned
        if color and any(ch in color for ch in '/／×✕*＊'):
            color = normalize_colorway_display(color)
        elif color:
            color = normalize_color_value(color) or color

    # 登録色は常に共有正規化（半色残り・別名残りを防ぐ）
    # 変換前色は照合用に残し、通常画面の表示には使わない
    source_color = ''
    if color:
        for h in (extracted.get('outsideHits') or []):
            if h.get('sourceColor'):
                source_color = (h.get('sourceColor') or '').strip()
                break
        if not source_color:
            for seg in (paren.get('segments') or []):
                if seg.get('kind') == 'color' and seg.get('sourceColor'):
                    source_color = (seg.get('sourceColor') or '').strip()
                    break
                if seg.get('kind') == 'color' and seg.get('original'):
                    source_color = (seg.get('original') or '').strip()
                    break
        if not source_color and paren.get('color') and paren.get('color') != color:
            source_color = (paren.get('color') or '').strip()
        color = normalize_color_value(color) or color

    # 自動確定: 括弧分類が自動、または括弧外の明示色を一意に振り分けられたとき
    final_planned = unify_import_notation(planned) or unified or original
    # 名に残った色別名も代表へ寄せない（色は color 欄へ抽出済み）。名側の半角等は unify 済み。
    if not final_planned:
        needs = True
        reasons.append('色除去後の商品名が空になるため原文のまま要確認')
        final_planned = unified or original
        color = ''
        source = ''
        source_color = ''
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
        # 色なしでも表記統一だけの差なら登録予定名を採用
        or (final_planned and final_planned != original)
    )
    is_cw = bool(
        color and (
            any(ch in color for ch in '／×')
            or any(
                h.get('isColorway')
                for h in (extracted.get('outsideHits') or [])
            )
            or any(
                s.get('isColorway')
                for s in (paren.get('segments') or [])
            )
        )
    )
    if color and is_cw:
        change_summary = '商品名から配色を抽出。1商品として登録'
    elif color:
        change_summary = f"商品名から色『{color}』を設定します"
    else:
        change_summary = ''

    # 括弧外配色の案内（括弧内は classify 側で付与済み）
    if color and not needs and is_cw:
        notice = f'配色として1商品で登録：{color}'
        if notice not in info_notices:
            info_notices.append(notice)

    return {
        'originalName': original,
        'sourceName': original,
        'plannedName': final_planned,
        'baseName': paren.get('baseName') or final_planned,
        'color': color,
        'colorKey': color_match_key(color) if color else '',
        'colorSource': source,
        'sourceColor': source_color,
        'processing': paren.get('processing') or '',
        'material': paren.get('material') or '',
        'unclassified': list(paren.get('unclassified') or []),
        'needsConfirm': needs,
        'confirmReasons': reasons,
        'infoNotices': info_notices,
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
    global COLOR_TERMS
    added = tuple(t for t in (extra or []) if t)
    if added:
        COLOR_TERMS = tuple(dict.fromkeys([*COLOR_TERMS, *added]))
    _rebuild_color_tables()


def extend_color_synonyms(extra):
    """テスト・拡張用の色別名追加（行編集の自動昇格には使わない）。"""
    global COLOR_SYNONYMS
    mapping = dict(COLOR_SYNONYMS)
    for syn, canon in (extra or {}).items():
        if syn and canon:
            mapping[str(syn)] = str(canon)
    COLOR_SYNONYMS = mapping
    _rebuild_color_tables()


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
