"""旧CSV（商品台帳・初期在庫）の品番補完。

- 品番「別注」 → 「別注(顧客名)」（顧客名は CSV のまま）。品番＋商品名で商品を区別する。
- 品番が空の衣服行（加工・部品・商品以外は除く） → 品番「要確認」。
  顧客＋商品名で商品を区別し、商品一覧で「品番要確認」と表示して後から品番を直してもらう。
元の品番は item['元品番']、名寄せ前の商品名は item['元商品名'] に残す。
"""
import re
import unicodedata

import stock

CUSTOM_ORDER_CODE = '別注'
NEEDS_CODE = '要確認'
NEEDS_CODE_BADGE = '品番要確認'

# 衣服とみなす名詞（品番空欄行のうち、これを含み加工・部品・商品以外の語を含まないもの）
GARMENT_WORDS = (
    'パンツ', 'ズボン', 'スカート', 'ジャケット', 'ベスト', 'コート', 'ドレス', 'シャツ',
    'ブルゾン', 'つなぎ', 'ツナギ', 'エプロン', '白衣', '上衣', 'ＪＰ', 'JP', 'ジャンパー',
    'シェフメイト', 'ワンピース', 'スモック', '前掛け', '帽子', 'キャップ',
    'バスタオル', 'タオル',
)
# 加工（裾上げ・ネット付け等）を表す語。含めば既存の加工サービス扱いのまま
PROCESSING_WORDS = (
    '加工', '刺繍', '取付', '取り付', '付け', '付', '直し', '裾上げ', '丈上げ', '丈詰', '外し',
    '潰し', 'ダウン', 'アップ', '補修', '修理', '交換', '作製', '貼', 'カン止', '型代', '反射',
)
# 部品・資材（白リブ・釦・フライス等）や商品以外（システム代・サンプル等）
NON_GARMENT_WORDS = (
    '釦', 'ボタン', 'リブ', 'フライス', '生地', 'ゴム', 'ファスナー', 'Dカン', 'バックル', '美錠', '尾錠', 'リボン', 'ワッペン',
    'ネーム', 'システム代', 'サンプル', '代',
)


def _nfkc(s):
    return unicodedata.normalize('NFKC', stock.text(s))


def is_custom_order_code(code):
    """別注・別注(顧客)。"""
    t = stock.text(code)
    return t == CUSTOM_ORDER_CODE or (t.startswith(CUSTOM_ORDER_CODE + '(') and t.endswith(')'))


def is_needs_code(code):
    return stock.text(code) == NEEDS_CODE


def custom_order_code_for(customer):
    cust = stock.text(customer)
    return f'{CUSTOM_ORDER_CODE}({cust})' if cust else CUSTOM_ORDER_CODE


def blank_code_kind(name):
    """品番空欄行の種類: garment / processing / parts / other。"""
    n = _nfkc(name)
    if not n:
        return 'other'
    for w in PROCESSING_WORDS:
        if _nfkc(w) in n:
            return 'processing'
    for w in NON_GARMENT_WORDS:
        if _nfkc(w) in n:
            return 'parts'
    if any(_nfkc(w) in n for w in GARMENT_WORDS):
        return 'garment'
    return 'other'


_GENDER_RE = re.compile(r'^(男|女)性用?')


def canonical_needs_code_name(name):
    """品番要確認の衣服名の表記ゆれ（男パンツ／男性パンツ／男性用パンツ）を1つに寄せる。"""
    t = stock.text(name)
    return _GENDER_RE.sub(r'\1', t)


def _strip_used_mark_for_custom(item):
    """商品台帳の 別注／要確認 行: 先頭＊（中古）を外し、通常品番と同じく1商品へ寄せる。

    原文は 元商品名 に残す（照合用）。中古の判定は初期在庫CSV側の＊で行う。
    """
    import legacy_csv_import as legacy
    name = stock.text(item.get('商品名'))
    core, star = legacy.strip_used_marker(name)
    core = stock.text(core)
    if star and core:
        item.setdefault('元商品名', name)
        item['商品名'] = core
        return True
    return False


def assign_item_code(item, *, strip_used=False):
    """1行分の品番を補完（item を直接更新）。変更した種類を返す。"""
    code = stock.text(item.get('商品コード'))
    strip_used = strip_used and '新古' not in item
    if strip_used and is_custom_order_code(code):
        _strip_used_mark_for_custom(item)
    # 別注は CSV 上「別注」のまま照合し、登録時に「別注(顧客名)」にする（_ensure_product）
    if code or stock.text(item.get('元品番')):
        return ''
    name = item.get('商品名')
    if blank_code_kind(name) != 'garment':
        return ''
    item['元品番'] = ''
    item['商品コード'] = NEEDS_CODE
    if strip_used and _strip_used_mark_for_custom(item):
        name = item.get('商品名')
    canon = canonical_needs_code_name(name)
    if canon != stock.text(name):
        item.setdefault('元商品名', stock.text(name))
        item['商品名'] = canon
    return 'needs_code'


def assign_rows(rows, *, strip_used=False):
    """[(rowNo, item)] を補完。種類別件数を返す。"""
    counts = {}
    for _no, item in rows or []:
        if isinstance(item, dict):
            k = assign_item_code(item, strip_used=strip_used)
            if k:
                counts[k] = counts.get(k, 0) + 1
    n = align_color_word_position(rows)
    if n:
        counts['color_position'] = n
    return counts


_SEP = ' \u3000'


def _color_words():
    import product_ledger_paren as paren
    return sorted({*paren.COLOR_TERMS, *paren.COLOR_SYNONYMS.keys()}, key=len, reverse=True)


def align_color_word_position(rows):
    """同じ 顧客＋品番 の中で、色語の位置（先頭／末尾）だけが違う商品名を末尾形へそろえる。

    例: 「緑清浄帽子」→「清浄帽子　緑」（同じ 顧客＋品番 に「清浄帽子　緑」がある場合のみ）。
    先頭＊（中古）は残したまま照合する。色語以外は完全一致が条件（清浄帽子≠準清浄帽子）。
    原文は 元商品名 に残す。直した行数を返す。
    """
    import legacy_csv_import as legacy
    colors = _color_words()
    groups = {}
    for _no, item in rows or []:
        if not isinstance(item, dict):
            continue
        key = (stock.text(item.get('顧客')), stock.text(item.get('商品コード')))
        groups.setdefault(key, []).append(item)
    fixed = 0
    for items in groups.values():
        suffix = {}  # (本体, 色) -> 末尾形の名前
        for item in items:
            core, _star = legacy.strip_used_marker(item.get('商品名'))
            core = stock.text(core)
            for color in colors:
                for sep in _SEP:
                    tail = sep + color
                    if core.endswith(tail):
                        body = core[:-len(tail)].rstrip(_SEP)
                        if body:
                            suffix.setdefault((body, color), core)
        if not suffix:
            continue
        for item in items:
            name = stock.text(item.get('商品名'))
            core, star = legacy.strip_used_marker(name)
            core = stock.text(core)
            for color in colors:
                if not core.startswith(color):
                    continue
                body = core[len(color):].lstrip(_SEP)
                target = suffix.get((body, color))
                if body and target:
                    item.setdefault('元商品名', name)
                    item['商品名'] = ('＊' if star else '') + target
                    fixed += 1
                    break
    return fixed
