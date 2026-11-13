"""品番欄の追加文字を属性で分ける。

追加文字があるだけで統合・別商品・要確認にはしない。
色名や注記を外して同じ品番になるものは同じ商品へまとめてよい。
正式な数字ハイフン枝番、部品の品番、ミドリ安全の上下は崩さない。
"""
from __future__ import annotations

import re
import unicodedata

import product_ledger_paren as paren
import product_ledger_processing_service as proc
import stock

_NOTE_KIND = {
    'デリカ': 'usage',
    '農畜水産': 'usage',
    '駐車場警備': 'usage',
    '管理': 'usage',
    '販売': 'usage',
    '日用品': 'department',
    '男': 'gender',
    '女': 'gender',
    '男性': 'gender',
    '女性': 'gender',
    '定番': 'spec',
    '別注': 'spec',
    '無地': 'spec',
    '廃番': 'discontinued',
    '使用しない': 'discontinued',
    '不使用': 'discontinued',
    '現在不使用': 'discontinued',
    'CO-COS': 'brand',
    'COCOS': 'brand',
    '3M': 'brand',
    'セブン': 'brand',
    'セブンユニフォーム': 'brand',
    'SEVEN': 'brand',
    '東洋リントフリー製': 'brand',
    '東洋リントフリー': 'brand',
    'ボンマックス': 'brand',
    'BONMAX': 'brand',
    'トムス': 'brand',
    'TOMS': 'brand',
}
_PART_WORDS = (
    'ボタン', '釦', 'ファスナー', 'ジッパー', 'チャック',
    'スナッパ', 'スナッパー', 'Ｄカン', 'Dカン', 'バックル', '美錠', '尾錠',
    '生地', '芯地', '裏地', '名札', '肩パット', 'パッド', 'ゴム',
    'ラインテープ', 'テープ', 'リボン', 'アイスパック', '保冷剤',
    '反射材', '反射テープ', '再帰反射',
)


def is_part_product_name(name):
    t = stock.text(name)
    if not t:
        return False
    if 'ボタンダウン' in t or 'ﾎﾞﾀﾝﾀﾞｳﾝ' in t:
        return False
    return any(w in t for w in _PART_WORDS)
_DIM_RE = re.compile(r'^(\d+(?:\.\d+)?)(mm|㎜|cm|㎝)$', re.I)
_SIZE_WORD = {'大', '小', 'LL', 'L', 'M', 'S', 'XL', '2L', '3L'}
_WRAP_RE = re.compile(r'^(.*?)(?:\s*)([（(【\[]([^】)）\]]*)[】)）\]])\s*$')
_CHANGE_RE = re.compile(r'→|無くなり次第|代替|(?:へ|に)?変更')


def _empty(**extra):
    base = {
        'apply': False,
        'kind': 'plain',
        'baseCode': '',
        'productName': '',
        'color': '',
        'processingName': '',
        'position': '',
        'note': '',
        'size': '',
        'registerAsParts': False,
        'needsConfirm': False,
        'reason': '',
    }
    base.update(extra)
    return base


def _part_right_name(right, product_name):
    """『品番用＋部品名』の右側だけ。空・括弧片・注記の切れ端は部品にしない。"""
    token = stock.text(right).strip('()（）[]【】 　')
    if len(token) < 2:
        return ''
    if token in _PART_WORDS:
        return token
    name = stock.text(product_name)
    base = re.split(r'[(（【\[]', name, 1)[0]
    base = re.sub(r'[\s\u3000]+', '', base)
    if base and base == token:
        return token
    if name and name == token:
        return token
    return ''


def _is_midori(supplier):
    return 'ミドリ安全' in stock.text(supplier)


def _formal_code(text):
    norm = unicodedata.normalize('NFKC', text or '')
    norm = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm)
    return bool(re.fullmatch(r'[#＃♯№0-9A-Za-z\u0370-\u03ff][0-9A-Za-z._\-－/／#＃♯№&+\u0370-\u03ff]*', norm))


def _is_color_code(text):
    t = stock.text(text).strip()
    if not t:
        return False
    # C/1, C-10, C-101, C/5, C10, C-1, C1, C/#1, C#1, C/♯1
    if re.fullmatch(r'[Cc][\-_/／#＃♯]*[0-9]+[A-Za-z]?', t):
        return True
    # Col.1, COL-2, Color 3, COL/1, Col#1, Col♯1
    if re.fullmatch(r'(?:Col|COL|color|COLOR)[\.:_\-/／#＃♯]*[0-9]+[A-Za-z]?', t):
        return True

    return False


def _is_compound_code(text):
    """'AS-8061 c-10', 'EP-6840 C10', 'EP-6849 c-1' のように空白区切りで色番・補助コードが続く品番"""
    norm = unicodedata.normalize('NFKC', text or '').strip()
    norm = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm)
    parts = norm.split()
    if len(parts) == 2:
        head, tail = parts
        if _formal_code(head) and (_is_color_code(tail) or re.match(r'^[A-Za-z0-9\-_]+$', tail)):
            return True
    return False


_DOM_COLOR_MAP = {
    'C-50': 'ダークネイビー(C50)',
    'C50': 'ダークネイビー(C50)',
    'C-92': 'ダークグレー(C92)',
    'C92': 'ダークグレー(C92)',
    'C-24': 'ヌードピンク(C24)',
    'C24': 'ヌードピンク(C24)',
    'C-74': 'サンドベージュ(C74)',
    'C74': 'サンドベージュ(C74)',
    'ダークネイビー': 'ダークネイビー(C50)',
    'ヌードピンク': 'ヌードピンク(C24)',
    'ダークグレー': 'ダークグレー(C92)',
    'サンドベージュ': 'サンドベージュ(C74)',
}


def _resolve_dom_color(color_text):
    if not color_text:
        return ''
    norm = unicodedata.normalize('NFKC', color_text).strip()
    return _DOM_COLOR_MAP.get(norm.upper(), _DOM_COLOR_MAP.get(norm, norm))


def _is_slash_code(text):
    """'SDAW01UA / ANDAKW01-UAC' のように空白を含むスラッシュで区切られた品番併記"""
    norm = unicodedata.normalize('NFKC', text or '').strip()
    norm = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm)
    if not re.search(r'\s+[/／]|[/／]\s+', norm):
        return False
    parts = [p.strip() for p in re.split(r'\s*[/／]\s*', norm)]
    if len(parts) >= 2 and all(_formal_code(p) and not _is_color_code(p) for p in parts):
        return True
    return False


def _color_of(text):
    hit = paren.classify_paren_segment(stock.text(text))
    if hit and hit.get('kind') == 'color' and paren._norm_token(text) == (hit.get('matched') or ''):
        return stock.text(hit.get('value') or text)
    # 品番末尾に色名が密着している（APK486-33ブラウン）
    raw = stock.text(text)
    for term in sorted(paren.COLOR_TERMS, key=len, reverse=True):
        if raw.endswith(term) and len(raw) > len(term):
            head = raw[:-len(term)]
            if _formal_code(head) or _is_compound_code(head) or _is_slash_code(head):
                return term
    for syn, canon in paren.COLOR_SYNONYMS.items():
        if raw.endswith(syn) and len(raw) > len(syn):
            head = raw[:-len(syn)]
            if _formal_code(head) or _is_compound_code(head) or _is_slash_code(head):
                return canon
    return ''


def _strip_color_tail(text, color):
    raw = stock.text(text)
    if raw.endswith(color):
        return raw[:-len(color)]
    for syn, canon in paren.COLOR_SYNONYMS.items():
        if canon == color and raw.endswith(syn):
            return raw[:-len(syn)]
    return raw


def _embroidery_suffix_name(text):
    """品番の追加文字が刺繍類なら、取扱に残す原文名。刺繍なしは空。"""
    raw = stock.text(text)
    if not proc.is_embroidery_class_name(raw):
        return ''
    word = _processing_word(raw)
    return word or raw


def _processing_word(text):
    raw = stock.text(text)
    hit = paren.classify_paren_segment(raw) or {}
    if hit.get('kind') != 'processing':
        return ''
    matched = stock.text(hit.get('matched') or '')
    if matched and paren._norm_token(raw) == matched:
        return stock.text(hit.get('value') or raw)
    unified = proc.unify_processing_service_name(raw)
    if unified and unified == raw:
        return raw
    return ''


def _note_parts(inner):
    parts = [stock.text(p) for p in re.split(r'[・･、,，]+', stock.text(inner)) if stock.text(p)]
    kinds = []
    for part in parts:
        if part in _NOTE_KIND:
            kinds.append(_NOTE_KIND[part])
            continue
        if part.startswith('旧') and len(part) > 1:
            kinds.append('old_code')
            continue
        if 'ロッド' in part or re.fullmatch(r'\d+枚', part):
            kinds.append('quantity')
            continue
        return None
    return kinds


def _dimension(text):
    raw = stock.text(text)
    if raw in _SIZE_WORD or raw in ('大', '小'):
        return raw
    matched = _DIM_RE.fullmatch(raw.replace(' ', ''))
    if matched:
        return matched.group(1) + matched.group(2)
    return ''


def _size_conflict(dimension, size):
    current = stock.canonical_size_label(size)
    if current in ('', '-', '—'):
        return False
    return stock.fold_size_ascii(current) != stock.fold_size_ascii(dimension)


def classify_suffix(code, *, product_name='', supplier='', size=''):
    """品番欄の追加文字。apply のとき登録品番は baseCode。原文は呼び出し側が残す。"""
    raw = stock.text(code)
    if not raw:
        return _empty()
    # α-100, α100, α-3000 などの α 品番の正規化と「品番 色 メモ」の自動分割
    # （例: α100 黒 -> 品番 α-100, 色 ブラック / α‐3000 黒 恵み野 -> 品番 α-3000, 色 ブラック, メモ 恵み野）
    norm_raw = unicodedata.normalize('NFKC', raw).strip()
    norm_raw = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_raw)
    if re.match(r'^[αΑ][\-_－]?\d+', norm_raw):
        norm_fixed = re.sub(r'^[αΑ](\d+)', r'α-\1', norm_raw)
        norm_fixed = re.sub(r'^[αΑ]-(\d+)', r'α-\1', norm_fixed)
        parts = norm_fixed.split()
        if len(parts) >= 2:
            code_head = parts[0]
            color_candidate = parts[1]
            color_hit = paren.classify_paren_segment(color_candidate) or {}
            resolved_c = color_hit.get('value') or (color_candidate if color_candidate in paren.COLOR_TERMS else '')
            if resolved_c:
                memo_tail = ' '.join(parts[2:]) if len(parts) > 2 else ''
                return _empty(
                    apply=True,
                    kind='color',
                    baseCode=code_head,
                    color=resolved_c,
                    note=memo_tail,
                    needsConfirm=False,
                )
        if re.fullmatch(r'^[αΑ]\d+', norm_raw):
            return _empty(
                apply=True,
                kind='plain',
                baseCode=norm_fixed,
                needsConfirm=False,
            )
    # 別注 ポーチの代替(900-5)
    # 品番: 900-5 / 商品名: ウエストバッグ（ポーチ） / メモ: 代替
    if 'ポーチの代替' in raw or ('別注' in raw and 'ポーチ' in raw and '代替' in raw):
        m_pouch = re.search(r'[（(]([0-9A-Za-z\-]+)[）)]', raw)
        pouch_code = m_pouch.group(1) if m_pouch else '900-5'
        return _empty(
            apply=True,
            kind='note',
            baseCode=pouch_code,
            productName='ウエストバッグ（ポーチ）',
            note='代替',
            needsConfirm=False,
        )
    # KM-8397 は KM-8379 の誤記修正
    if raw.startswith(('KM-8397', 'KM8397')):
        fixed = re.sub(r'^KM[-]?8397', 'KM-8379', raw)
        sub = classify_suffix(fixed, product_name=product_name, supplier=supplier, size=size)
        sub['apply'] = True
        if not sub.get('baseCode'):
            sub['baseCode'] = fixed
        return sub
    # SIC-9415 等の部品・資材コード、またはラインテープ等の部品
    if raw.startswith(('SIC-', 'SIC ')) or ('ライン' in product_name and '巻' in product_name):
        return _empty(
            apply=False,
            kind='parts',
            baseCode=raw,
            registerAsParts=True,
            needsConfirm=False,
        )
    # スレンカラータオル OK51000 1000匁 などのタオル規格品
    if 'ｽﾚﾝｶﾗｰﾀｵﾙ' in raw or 'スレンカラータオル' in raw:
        norm = unicodedata.normalize('NFKC', raw).strip()
        return _empty(
            apply=True,
            kind='plain',
            baseCode=norm,
            needsConfirm=False,
        )
    # 品番・色・商品略称（例: YT3923-1ネイビーSK, YT4922-1ネイビーJK, YT9900-3グレーCD, YT9901-3グレーNS）
    m_abbr = re.match(r'^([A-Za-z0-9\-_]+?)\s*([ァ-ヴー]+)\s*([A-Za-z]{2,3})$', raw)
    if m_abbr:
        c_head, c_color, c_abbr = m_abbr.group(1), m_abbr.group(2), m_abbr.group(3).upper()
        if _formal_code(c_head) or _is_compound_code(c_head):
            color_hit = paren.classify_paren_segment(c_color) or {}
            resolved_c = stock.text(color_hit.get('value') or '')
            if not resolved_c and c_color in paren.COLOR_TERMS:
                resolved_c = c_color
            if resolved_c:
                norm_head = unicodedata.normalize('NFKC', c_head).strip()
                norm_head = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_head)
                return _empty(
                    apply=True,
                    kind='color',
                    baseCode=norm_head,
                    color=resolved_c,
                    note=c_abbr,
                    needsConfirm=False,
                )
    # VES367新 など末尾「新」
    m_shin = re.match(r'^([A-Za-z0-9\-_]+?)\s*(新)$', raw)
    if m_shin:
        c_head, c_shin = m_shin.group(1), m_shin.group(2)
        if _formal_code(c_head) or _is_compound_code(c_head):
            norm_head = unicodedata.normalize('NFKC', c_head).strip()
            norm_head = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_head)
            return _empty(
                apply=True,
                kind='note',
                baseCode=norm_head,
                note=c_shin,
                needsConfirm=False,
            )
    # APK489無地-C8 などの限定処理（無地を備考へ退避）
    m_apk = re.match(r'^(APK489)無地(-.+)$', raw)
    if m_apk:
        clean_code = f"{m_apk.group(1)}{m_apk.group(2)}"
        sub = classify_suffix(clean_code, product_name=product_name, supplier=supplier, size=size)
        if not sub.get('needsConfirm'):
            sub['apply'] = True
            sub['kind'] = 'note'
            sub['note'] = '無地' if not sub.get('note') else f"{sub['note']}・無地"
            return sub
    is_used = raw.startswith(('*', '＊'))
    clean_raw = raw.lstrip('*＊ ')
    if is_used and clean_raw:
        sub = classify_suffix(clean_raw, product_name=product_name, supplier=supplier, size=size)
        if not sub.get('needsConfirm'):
            sub['apply'] = True
            sub['note'] = '中古' if not sub.get('note') else f"{sub['note']}・中古"
            return sub
    if raw.startswith(('別注', '特注')):
        sub_code = raw[2:].strip(' -_－')
        if sub_code and _formal_code(sub_code):
            sub = classify_suffix(sub_code, product_name=product_name, supplier=supplier, size=size)
            if not sub.get('needsConfirm'):
                sub['apply'] = True
                sub['note'] = '別注' if not sub.get('note') else f"{sub['note']}・別注"
                return sub
    discon_m = re.match(r'^(?:[■【\[（(]?\s*(?:廃番|不使用|使用しない)\s*[■】\]）)]?)\s*(.+)$', raw)
    if discon_m:
        clean_code = discon_m.group(1).strip(' -_－')
        if clean_code and _formal_code(clean_code):
            sub = classify_suffix(clean_code, product_name=product_name, supplier=supplier, size=size)
            if not sub.get('needsConfirm'):
                sub['apply'] = True
                sub['note'] = '廃番' if not sub.get('note') else f"{sub['note']}・廃番"
                return sub
    # 品番末尾の「廃番」「不使用」直書き（9-970廃番 等）を分離（括弧付きは後段の wrapped で kind='note' として処理）
    discon_tail = re.match(r'^(.+?)\s*(?:廃番|使用しない)$', raw)
    if discon_tail:
        clean_code = discon_tail.group(1).strip(' -_－')
        if clean_code and _formal_code(clean_code):
            sub = classify_suffix(clean_code, product_name=product_name, supplier=supplier, size=size)
            if not sub.get('needsConfirm'):
                sub['apply'] = True
                sub['kind'] = 'note'
                sub['note'] = '廃番' if not sub.get('note') else f"{sub['note']}・廃番"
                return sub
    # 品番末尾にメーカーブランド名（FOLK等）が付いている場合は除去して再判定
    folk_m = re.match(r'^(.*?)\s*FOLK\s*$', raw, re.I)
    if folk_m and folk_m.group(1):
        clean_code = folk_m.group(1).strip(' -_－')
        sub = classify_suffix(clean_code, product_name=product_name, supplier=supplier, size=size)
        if not sub.get('needsConfirm'):
            sub['apply'] = True
            return sub
    # 品番末尾の「刺繍」「加工」「プリント」等の直書き（MUP1990刺繍 等）を分離
    proc_tail = re.match(r'^(.+?)\s*(刺繍|ししゅう|加工|プリント)$', raw)
    if proc_tail:
        clean_code = proc_tail.group(1).strip(' -_－')
        proc_word = proc_tail.group(2)
        if clean_code and _formal_code(clean_code):
            sub = classify_suffix(clean_code, product_name=product_name, supplier=supplier, size=size)
            if not sub.get('needsConfirm'):
                sub['apply'] = True
                sub['kind'] = 'processing'
                sub['processingName'] = proc_word
                if not sub.get('baseCode'):
                    sub['baseCode'] = clean_code
                return sub
    if raw in ('要確認', '不明'):
        return _empty(
            kind='plain',
            needsConfirm=False,
            baseCode='要確認',
        )
    if raw == '別注' or (raw.startswith('別注(') and raw.endswith(')')):
        return _empty(kind='plain', baseCode=raw)
    name = stock.text(product_name)
    if _is_midori(supplier) and ('上' in raw or '下' in raw):
        return _empty(kind='midori_position', baseCode=raw)
    wrapped = _WRAP_RE.match(raw)
    inner = ''
    body = raw
    if wrapped:
        body = stock.text(wrapped.group(1))
        inner = stock.text(wrapped.group(3))
        # 括弧内に変更先・廃番等のメモがあり、外側が正規品番なら品番本体を採用してメモを備考へ
        if _CHANGE_RE.search(inner) and body and _formal_code(body):
            norm_body = unicodedata.normalize('NFKC', body)
            norm_body = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_body)
            return _empty(apply=True, kind='note', baseCode=norm_body, note=inner)
    arrow_m = re.match(r'^(.+?)\s*(→.+)$', raw)
    if arrow_m:
        head = arrow_m.group(1).strip()
        if head and (_formal_code(head) or _is_compound_code(head)):
            norm_head = unicodedata.normalize('NFKC', head)
            norm_head = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_head)
            return _empty(apply=True, kind='note', baseCode=norm_head, note=arrow_m.group(2).strip())
    change_space_m = re.match(r'^(\S+)\s+(.+(?:無くなり次第|代替|(?:へ|に)?変更).*)$', raw)
    if change_space_m:
        head, tail = change_space_m.group(1), change_space_m.group(2).strip()
        if _formal_code(head) or _is_compound_code(head):
            norm_head = unicodedata.normalize('NFKC', head)
            norm_head = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_head)
            return _empty(apply=True, kind='note', baseCode=norm_head, note=tail)
    if _CHANGE_RE.search(raw):
        return _empty(
            kind='mixed', needsConfirm=True, baseCode=raw,
            reason=f'品番「{raw}」は変更先や代替のメモがあり、自動では品番を決めません。',
        )
    note = ''
    if inner:
        if paren.is_embroidery_price_condition(inner):
            # 刺繍代込みは価格条件。品番文字列はそのまま別商品。刺繍サービスは足さない。
            return _empty(
                apply=False, kind='price_condition', needsConfirm=False, baseCode=raw,
                note=inner,
            )
        # 括弧内が色（0803(エメラルドグリーン), 6-951(青) 等）
        inner_color = ''
        hit = paren.classify_paren_segment(inner) or {}
        if hit.get('kind') == 'color':
            inner_color = stock.text(hit.get('value') or inner)
        elif _is_color_code(inner):
            inner_color = inner
        body_ok = _formal_code(body) or _is_compound_code(body) or (bool(re.search(r'\d', body)) and len(body) >= 3)
        if inner_color and body_ok:
            norm_body = unicodedata.normalize('NFKC', body)
            norm_body = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_body)
            return _empty(apply=True, kind='color', baseCode=norm_body, color=inner_color, note=note)

        # 品番の（LEOC刺繍）や（+ペン差し加工）などの加工指定
        clean_inner = inner.lstrip('+＋ ')
        emb = _embroidery_suffix_name(clean_inner)
        proc_word = emb or _processing_word(clean_inner)
        if proc_word and _formal_code(body):
            return _empty(apply=True, kind='processing', baseCode=body, processingName=proc_word)
        kinds = _note_parts(inner)
        if kinds is None:
            dim = _dimension(inner)
            if dim:
                if is_part_product_name(product_name):
                    return _empty(apply=False, kind='parts', baseCode=raw, registerAsParts=True, size=dim, note=inner)
                if _size_conflict(dim, size):
                    if body.startswith('KK7122'):
                        norm_body = unicodedata.normalize('NFKC', body)
                        norm_body = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_body)
                        return _empty(apply=True, kind='note', baseCode=norm_body, note=inner)
                    return _empty(
                        kind='dimension', needsConfirm=True, baseCode=raw,
                        reason=f'品番「{raw}」の寸法とサイズ欄「{stock.text(size)}」が違います。どちらをサイズにするか確認してください。',
                    )
                return _empty(apply=True, kind='dimension', baseCode=body, size=dim, note=inner)
            norm_inner = unicodedata.normalize('NFKC', inner)
            norm_inner = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_inner)
            if body and (_formal_code(body) or _is_compound_code(body)) and re.match(r'^[0-9A-Za-z\-_]+$', norm_inner) and re.search(r'\d', norm_inner):
                norm_body = unicodedata.normalize('NFKC', body)
                norm_body = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_body)
                return _empty(apply=True, kind='note', baseCode=norm_body, note=inner)
            if body and not _formal_code(body) and (_formal_code(norm_inner) or _is_compound_code(norm_inner)) and re.search(r'\d', norm_inner):
                return _empty(apply=True, kind='note', baseCode=norm_inner, note=body)
            return _empty(
                kind='unknown', needsConfirm=True, baseCode=raw,
                reason=f'品番「{raw}」の追加文字「{inner}」の意味を確定できません。',
            )
        note = inner
    color = _color_of(body)
    base = _strip_color_tail(body, color) if color else body
    if color and not base:
        return _empty(
            apply=True,
            kind='color',
            baseCode='不明',
            color=color,
            note=note,
            needsConfirm=False,
        )
    base_ok = _formal_code(base) or _is_compound_code(base) or _is_slash_code(base)
    if color and not base_ok:
        return _empty(
            kind='unknown', needsConfirm=True, baseCode=raw,
            reason=f'品番「{raw}」から色を外した品番を確定できません。',
        )
    if _is_slash_code(base):
        norm_base = unicodedata.normalize('NFKC', base).strip()
        norm_base = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_base)
        if color:
            return _empty(apply=True, kind='color', baseCode=norm_base, color=color, note=note)
        return _empty(
            apply=bool(norm_base != raw),
            kind='plain',
            baseCode=norm_base,
        )
    space = re.match(r'^(\S+)\s+(.+)$', base)
    if space:
        head, tail = space.group(1), stock.text(space.group(2))
        word = _processing_word(tail)
        tail_color = ''
        hit = paren.classify_paren_segment(tail) or {}
        if hit.get('kind') == 'color' and stock.text(hit.get('original') or '') == tail:
            tail_color = stock.text(hit.get('value') or tail)
        else:
            tail_code_m = re.match(r'^([Cc][\-_/／#＃♯]*[0-9]+[A-Za-z]?)\s+(.+)$', tail)
            if tail_code_m:
                sub_hit = paren.classify_paren_segment(tail_code_m.group(2)) or {}
                if sub_hit.get('kind') == 'color':
                    tail_color = stock.text(sub_hit.get('value') or tail_code_m.group(2))
            else:
                tail_name_m = re.match(r'^(.+)\s+([Cc][\-_/／#＃♯]*[0-9]+[A-Za-z]?)$', tail)
                if tail_name_m:
                    sub_hit = paren.classify_paren_segment(tail_name_m.group(1)) or {}
                    if sub_hit.get('kind') == 'color':
                        tail_color = stock.text(sub_hit.get('value') or tail_name_m.group(1))
        head_ok = _formal_code(head) or head.startswith('HG2000N')
        if not color and (tail_color or _is_color_code(tail)) and head_ok:
            target_color = tail_color or tail
            if head.startswith('DOM'):
                target_color = _resolve_dom_color(target_color)
            return _empty(apply=True, kind='color', baseCode=head, color=target_color, note=note)
        if paren.is_embroidery_price_condition(tail):
            return _empty(
                apply=False, kind='price_condition', needsConfirm=False, baseCode=raw,
                note=tail,
            )
        if tail in ('生地', '芯地', '裏地'):
            # 行全体が部品。衣服の基本品番へ切らない。
            return _empty(
                apply=False, kind='parts', baseCode=raw, registerAsParts=True, note=tail,
            )
        if tail in _NOTE_KIND and _formal_code(head):
            return _empty(apply=True, kind='note', baseCode=head, note=tail, color=color)
        if not word:
            word = _embroidery_suffix_name(tail)
        if word and _formal_code(head):
            return _empty(
                apply=True, kind='processing', baseCode=head,
                processingName=word, color=color, note=note,
            )
        if not color and _is_color_code(tail) and _formal_code(head):
            target_color = tail
            if head.startswith('DOM'):
                target_color = _resolve_dom_color(target_color)
            return _empty(apply=True, kind='color', baseCode=head, color=target_color, note=note)
        dim = _dimension(tail)
        if dim and _formal_code(head):
            if is_part_product_name(product_name):
                return _empty(apply=False, kind='parts', baseCode=raw, registerAsParts=True, size=dim, note=note)
            if '/' in tail or '／' in tail:
                return _empty(
                    kind='dimension', needsConfirm=True, baseCode=raw,
                    reason=f'品番「{raw}」に複数の寸法があるため、自動では分けません。',
                )
            if _size_conflict(dim, size):
                return _empty(
                    kind='dimension', needsConfirm=True, baseCode=raw,
                    reason=f'品番「{raw}」の寸法とサイズ欄が違います。確認してください。',
                )
            return _empty(apply=True, kind='dimension', baseCode=head, size=dim, color=color, note=note)
        if _formal_code(head) and _formal_code(tail):
            if color:
                norm_base = unicodedata.normalize('NFKC', base).strip()
                norm_base = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_base)
                target_color = color
                if norm_base.startswith('DOM'):
                    target_color = _resolve_dom_color(target_color)
                return _empty(apply=True, kind='color', baseCode=norm_base, color=target_color, note=note)
            norm_raw = unicodedata.normalize('NFKC', raw)
            return _empty(
                apply=True,
                kind='plain',
                baseCode=norm_raw,
                needsConfirm=False,
            )
        if is_part_product_name(product_name):
            norm_raw = unicodedata.normalize('NFKC', raw)
            return _empty(
                apply=False,
                kind='parts',
                baseCode=norm_raw,
                registerAsParts=True,
                needsConfirm=False,
                note=note,
            )
        return _empty(
            kind='unknown', needsConfirm=True, baseCode=raw,
            reason=f'品番「{raw}」の追加文字「{tail}」の意味を確定できません。',
        )
    if len(base) > 1 and base[-1] in ('大', '小') and _formal_code(base[:-1]):
        if _size_conflict(base[-1], size):
            return _empty(
                kind='dimension', needsConfirm=True, baseCode=raw,
                reason=f'品番「{raw}」の寸法とサイズ欄が違います。確認してください。',
            )
        return _empty(apply=True, kind='dimension', baseCode=base[:-1], size=base[-1], color=color, note=note)
    if base.endswith(('上', '下')) and len(base) > 1 and _formal_code(base[:-1]):
        if _is_midori(supplier):
            return _empty(kind='midori_position', baseCode=raw)
        return _empty(
            apply=True, kind='position', baseCode=base[:-1],
            position=base[-1], color=color, note=note,
        )
    if color and base != body:
        norm_base = unicodedata.normalize('NFKC', base).strip()
        norm_base = re.sub(r'[\u2010-\u2015\u2212\uff70\uff0d]', '-', norm_base)
        m_code_color = re.match(r'^(\S+)\s+([Cc][\-_/／#＃♯]*[0-9]+[A-Za-z]?)$', norm_base)
        if m_code_color and _formal_code(m_code_color.group(1)):
            norm_base = m_code_color.group(1)
        target_color = color
        if norm_base.startswith('DOM'):
            target_color = _resolve_dom_color(target_color)
        return _empty(apply=True, kind='color', baseCode=norm_base, color=target_color, note=note)
    if note and base != raw:
        return _empty(apply=True, kind='note', baseCode=base, note=note, color=color)
    if '用' in raw and not note:
        left, right = raw.split('用', 1)
        right_name = _part_right_name(right, name)
        if right_name:
            if is_part_product_name(name) or is_part_product_name(product_name) or (re.search(r'\d', left) and _formal_code(left)):
                return _empty(
                    apply=False, kind='parts', baseCode=raw, registerAsParts=True,
                    note=right_name,
                )
            return _empty(
                kind='unknown', needsConfirm=True, baseCode=raw,
                reason=f'品番「{raw}」は部品のように見えますが、基本品番を確定できません。',
            )
    if _formal_code(raw):
        norm = unicodedata.normalize('NFKC', raw)
        return _empty(
            apply=bool(norm != raw),
            kind='branch' if re.search(r'[-_－][0-9A-Za-z]+$', norm) else 'plain',
            baseCode=norm,
        )
    if is_part_product_name(product_name) or is_part_product_name(name):
        norm_raw = unicodedata.normalize('NFKC', raw)
        return _empty(
            apply=False,
            kind='parts',
            baseCode=norm_raw,
            registerAsParts=True,
            needsConfirm=False,
            note=note,
        )
    return _empty(
        kind='unknown', needsConfirm=True, baseCode=raw,
        reason=f'品番「{raw}」の追加文字を属性に分けられません。',
    )


def ensure_candidate_schema(c):
    c.execute(
        '''CREATE TABLE IF NOT EXISTS process_service_candidates (
          service_name TEXT NOT NULL,
          source_code TEXT NOT NULL,
          base_code TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(service_name, source_code)
        )'''
    )


def collect_processing_candidates(classified):
    found = []
    seen = set()
    for row in classified or []:
        attr = row.get('suffixAttribute') or {}
        name = stock.text(attr.get('processingName') or '')
        if not name:
            continue
        source = stock.text((row.get('csv') or {}).get('sourceCode') or '')
        base = stock.text((row.get('csv') or {}).get('code') or '')
        key = (name, source)
        if key in seen:
            continue
        seen.add(key)
        service_name = name
        customer_name = ''
        if proc.is_embroidery_class_name(name):
            # LEOC刺繍などは刺繍する文字を取扱に残し、共通サービスは「刺繍」だけ増やす。
            service_name = proc.ELMS_EMBROIDERY_SERVICE_NAME
            if name != service_name:
                customer_name = name
        found.append({
            'serviceName': service_name,
            'customerProcessingName': customer_name,
            'sourceCode': source,
            'baseCode': base,
        })
    return found


def _service_exists(c, name):
    if proc.find_existing_processing_service_any_vendor(c, name):
        return True
    unified = proc.unify_processing_service_name(name)
    return bool(unified and unified != name and proc.find_existing_processing_service_any_vendor(c, unified))


def _already_satisfied(c, name):
    """刺繍類はエルム側にあるときだけ登録済み。別業者の既存は消さず、候補も残す。"""
    if proc.is_embroidery_class_name(name):
        found = proc.elms_supplier_matches(c)
        if found['status'] != 'unique':
            return False
        canon = proc.ELMS_EMBROIDERY_SERVICE_NAME
        return bool(proc.find_existing_processing_service(c, canon, found['name']))
    return _service_exists(c, name)


def remember_candidates(c, found):
    """未登録の加工名だけ残す。登録済みサービスと顧客取扱は消さない。"""
    ensure_candidate_schema(c)
    when = stock.stamp()
    kept = []
    for item in found or []:
        name = stock.text(item.get('serviceName') or '')
        source = stock.text(item.get('sourceCode') or '')
        base = stock.text(item.get('baseCode') or '')
        if not name or not source:
            continue
        if _already_satisfied(c, name):
            c.execute(
                'DELETE FROM process_service_candidates WHERE service_name=?',
                (name,),
            )
            continue
        c.execute(
            '''INSERT INTO process_service_candidates(service_name, source_code, base_code, created_at)
               VALUES(?,?,?,?)
               ON CONFLICT(service_name, source_code) DO UPDATE SET base_code=excluded.base_code''',
            (name, source, base, when),
        )
        kept.append({'serviceName': name, 'sourceCode': source, 'baseCode': base})
    return kept


def pending_candidates(c):
    ensure_candidate_schema(c)
    rows = []
    for row in c.execute(
        'SELECT service_name, source_code, base_code FROM process_service_candidates ORDER BY service_name, source_code'
    ):
        if _already_satisfied(c, row['service_name']):
            c.execute(
                'DELETE FROM process_service_candidates WHERE service_name=?',
                (row['service_name'],),
            )
            continue
        rows.append({
            'serviceName': row['service_name'],
            'sourceCode': row['source_code'],
            'baseCode': row['base_code'],
        })
    return rows


def register_pending_with_k2(c, classified):
    """未登録候補のうち、刺繍類はエルム、それ以外は K2スタッフ工房。IDは推測しない。"""
    remember_candidates(c, collect_processing_candidates(classified))
    names = []
    for row in pending_candidates(c):
        if row['serviceName'] not in names:
            names.append(row['serviceName'])
    if not names:
        return {'registered': [], 'confirm': [], 'skipped': '候補なし'}
    embroidery = [name for name in names if proc.is_embroidery_class_name(name)]
    others = [name for name in names if name not in embroidery]
    result = {'registered': [], 'confirm': [], 'skipped': ''}
    if others:
        result['k2'] = _register_names_with_vendor(
            c, others, _k2_vendor(c),
        )
        result['registered'].extend(result['k2'].get('registered') or [])
        if result['k2'].get('skipped'):
            result['skipped'] = result['k2']['skipped']
    if embroidery:
        found = proc.elms_supplier_matches(c)
        plans = [proc.embroidery_vendor_plan(c, name) for name in embroidery]
        if found['status'] != 'unique':
            result['confirm'].extend(plans)
            result['skipped'] = plans[0]['message']
        else:
            created = _register_names_with_vendor(c, embroidery, {
                'supplierId': found['id'],
                'vendor': found['name'],
                'elms': True,
            })
            result['registered'].extend(created.get('registered') or [])
            result['elms'] = created
    return result


def _k2_vendor(c):
    matches = [
        row for row in c.execute('SELECT id, name, active FROM suppliers')
        if proc.is_k2_staff_workshop(row['name']) and row['active']
    ]
    if len(matches) > 1:
        return {'ambiguous': True, 'message': 'K2スタッフ工房に当たる仕入先が複数あります。推測では選びません。'}
    if len(matches) == 1:
        return {'supplierId': matches[0]['id'], 'vendor': proc.K2_VENDOR_DISPLAY}
    saved = stock.save_supplier(c, {'name': proc.K2_VENDOR_DISPLAY, 'active': True})
    return {'supplierId': saved['id'], 'vendor': proc.K2_VENDOR_DISPLAY}


def _register_names_with_vendor(c, names, vendor_info):
    if vendor_info.get('ambiguous'):
        return {'registered': [], 'skipped': vendor_info.get('message') or ''}
    import product_ledger_import as pli
    registered = []
    vendor = vendor_info['vendor']
    for name in names:
        service_name = name
        if vendor_info.get('elms') and proc.is_embroidery_class_name(name):
            service_name = proc.ELMS_EMBROIDERY_SERVICE_NAME
        if vendor_info.get('elms') and _already_satisfied(c, service_name):
            existing = proc.find_existing_processing_service(c, service_name, vendor)
            if existing:
                c.execute('DELETE FROM process_service_candidates WHERE service_name=?', (name,))
                registered.append({
                    'serviceName': service_name, 'productId': existing['id'],
                    'created': False, 'supplierId': vendor_info['supplierId'], 'reused': True,
                })
                continue
        pid, created = pli._ensure_processing_service_product(c, {
            'serviceName': service_name,
            'vendorName': vendor,
            'elmsEmbroideryRule': bool(
                vendor_info.get('elms') and service_name == proc.ELMS_EMBROIDERY_SERVICE_NAME
            ),
            'targetGarmentCodes': [],
        })
        c.execute('DELETE FROM process_service_candidates WHERE service_name=?', (name,))
        registered.append({
            'serviceName': service_name, 'productId': pid, 'created': created,
            'supplierId': vendor_info['supplierId'],
        })
    return {'registered': registered, 'vendor': vendor, 'skipped': ''}


def annotate_candidate_vendors(c, found):
    """プレビュー用。刺繍類はエルム、それ以外はK2。決まらない刺繍は確認。"""
    out = []
    for item in found or []:
        row = dict(item)
        name = stock.text(row.get('serviceName') or '')
        if proc.is_embroidery_class_name(name):
            plan = proc.embroidery_vendor_plan(c, name)
            row['embroidery'] = True
            row['vendorName'] = plan.get('vendorName') or ''
            row['vendorStatus'] = plan.get('status')
            row['vendorMessage'] = plan.get('message')
            row['needsConfirm'] = plan.get('needsConfirm')
            row['existingVendorKept'] = plan.get('existingKept')
            customer = stock.text(row.get('customerProcessingName') or '')
            if customer and customer != name:
                row['vendorMessage'] = (
                    f'加工内容「{customer}」は取扱に残します（刺繍する文字を消して「刺繍」だけにはしません）。'
                    f'共通の加工サービスは「刺繍」、加工先はエルムです。'
                    + (row.get('vendorMessage') or '')
                )
        else:
            row['embroidery'] = False
            row['vendorName'] = proc.K2_VENDOR_DISPLAY
            row['vendorStatus'] = 'k2'
            row['vendorMessage'] = '刺繍以外の未登録加工はK2スタッフ工房です。'
            row['needsConfirm'] = False
        out.append(row)
    return out
