"""旧CSV取込・メーカー補正: 上下区分（上／下）を品番末尾へ連結する。

方針:
- 「VEH30B 下」「VEH30B下」→ 登録品番「VEH30B下」（スペース除去・密着）
- 既に末尾へ付いている場合は二重付与しない
- 「上」と「下」は別品番・別商品（同一商品へ統合しない）
- 「裾上げ」「上下セット」「上衣」「股下」などから機械的に切り出さない
- 品番欄に無く、旧商品名／メーカー情報に明確な区分があるときだけ反映
- 判断材料が矛盾するときは付与せず要確認
"""
from __future__ import annotations

import re
import unicodedata

import product_ledger_paren as paren_classify
import stock

POSITION_TOKENS = ('上', '下')

# 機械抽出してはいけない複合語（これらに含まれる上／下は区分にしない）
_FALSE_POSITIVE_FRAGMENTS = (
    '裾上げ', '裾上', '上下セット', '上下', '上衣', '下衣',
    '股下', 'ひざ下', '膝下', '腰上', '胸上', '胸下',
    '右上', '右下', '左上', '左下', '真上', '真下',
    '以上', '以下', '上質', '上等', '上記', '下記',
    '引き上げ', '引き下げ', '値上げ', '値下げ',
)

# 色語＋上下の密着（白下・黒上 等）だけ名からの区分を認める
_COLOR_HEADS = frozenset(
    paren_classify.unify_import_notation(t) or t
    for t in paren_classify.COLOR_TERMS
)


def _unify(text: str) -> str:
    return paren_classify.unify_import_notation(stock.text(text) or '') or ''


def is_position_token(value: str) -> bool:
    return stock.text(value) in POSITION_TOKENS


def strip_sku_position_suffix(code: str) -> tuple[str, str]:
    """品番末尾の上下区分を分離する。

    例:
      VEH30B 下 → (VEH30B, 下)
      VEH30B下 → (VEH30B, 下)
      ＶＥＨ３０Ｂ　下 → (VEH30B相当の原文ベース, 下) ※ベースは空白除去のみ・英数は原文維持
      VEH30B → (VEH30B, '')
    """
    raw = stock.text(code)
    if not raw:
        return '', ''
    # 末尾の半角／全角スペース＋上／下
    m = re.search(r'^(.*?)(?:[\s\u3000]+)([上下])$', raw)
    if m and stock.text(m.group(1)):
        return stock.text(m.group(1)), m.group(2)
    # 密着: 末尾が上／下で、その直前がスペース以外
    if raw[-1] in POSITION_TOKENS and len(raw) > 1:
        head = raw[:-1]
        # ベースが空／記号のみは付けない
        if stock.text(head) and not head[-1].isspace():
            # 「白下」のような色＋上下だけの品番は稀だが、英数・ハイフンを含む型番を優先
            return stock.text(head), raw[-1]
    return raw, ''


def attach_sku_position_suffix(base_sku: str, position: str) -> str:
    """基礎品番へ上下を密着連結。既付与ならそのまま。"""
    base = stock.text(base_sku)
    pos = stock.text(position)
    if not base:
        return ''
    if pos not in POSITION_TOKENS:
        return base
    cur_base, cur_pos = strip_sku_position_suffix(base)
    if cur_pos == pos:
        # スペース付きで来ても正規形（密着）へ
        return f'{cur_base}{pos}' if cur_base else base
    if cur_pos and cur_pos != pos:
        # 呼び出し側で矛盾扱い。ここでは既存末尾を維持しない（空返却はしない）
        return f'{cur_base}{pos}' if cur_base else f'{base}{pos}'
    return f'{base}{pos}'


def sku_code_variants(code: str) -> list[str]:
    """照合用の品番表記ゆれ（原文・密着・スペース区切り・基礎のみ）。"""
    raw = stock.text(code)
    if not raw:
        return []
    base, pos = strip_sku_position_suffix(raw)
    out = []
    for v in (
        raw,
        attach_sku_position_suffix(base, pos) if pos else base,
        f'{base} {pos}' if base and pos else '',
        f'{base}\u3000{pos}' if base and pos else '',
        base,
    ):
        t = stock.text(v)
        if t and t not in out:
            out.append(t)
    return out


def _mask_false_positive_fragments(unified: str) -> str:
    """複合語内の上／下を潰して、単独トークン判定を誤らせない。"""
    masked = unified
    for frag in sorted(_FALSE_POSITIVE_FRAGMENTS, key=len, reverse=True):
        if frag in masked:
            masked = masked.replace(frag, '・' * len(frag))
    return masked


def extract_garment_position_from_text(text: str) -> str:
    """商品名などから上下区分を安全に取る。取れなければ空。

    認めるもの:
    - 空白区切りの単独トークン「上」「下」
    - 色語＋上下の密着（白下・ネイビー上 等）
    認めないもの:
    - 裾上げ・上下セット・上衣・股下 等の複合語内
    """
    unified = _unify(text)
    if not unified:
        return ''
    masked = _mask_false_positive_fragments(unified)
    found = []
    for tok in re.split(r'[\s\u3000]+', masked):
        if not tok:
            continue
        if tok in POSITION_TOKENS:
            if tok not in found:
                found.append(tok)
            continue
        for pos in POSITION_TOKENS:
            if tok.endswith(pos) and len(tok) > len(pos):
                head = tok[: -len(pos)]
                head_u = paren_classify.unify_import_notation(head) or head
                if head_u in _COLOR_HEADS:
                    if pos not in found:
                        found.append(pos)
                break
    if len(found) == 1:
        return found[0]
    return ''


def extract_positions_for_search(text: str) -> list[str]:
    """メーカー検索ヒント用。安全抽出できた区分だけ返す（複数は矛盾として空）。"""
    pos = extract_garment_position_from_text(text)
    return [pos] if pos else []


def resolve_garment_position(
    *,
    code: str = '',
    product_name: str = '',
    maker_position: str = '',
    override_position: str = '',
    override_registered_code: str = '',
) -> dict:
    """行の上下区分と登録予定品番を決定する。

    戻り値:
      sourceCode, baseCode, position, registeredCode,
      conflict, needsConfirm, sources
    """
    source = stock.text(code)
    override_reg = stock.text(override_registered_code)
    if override_reg:
        base, pos = strip_sku_position_suffix(override_reg)
        # オーバーライド品番が基礎のみなら、他材料と合成
        if not pos:
            pos = stock.text(override_position)
            if pos in POSITION_TOKENS:
                override_reg = attach_sku_position_suffix(base or override_reg, pos)
                base, pos = strip_sku_position_suffix(override_reg)
        return {
            'sourceCode': source or override_reg,
            'baseCode': base or override_reg,
            'position': pos,
            'registeredCode': attach_sku_position_suffix(base or override_reg, pos) if pos else (base or override_reg),
            'conflict': False,
            'needsConfirm': False,
            'sources': ['override'],
        }

    code_base, code_pos = strip_sku_position_suffix(source)
    name_pos = extract_garment_position_from_text(product_name)
    maker_pos = stock.text(maker_position)
    if maker_pos not in POSITION_TOKENS:
        maker_pos = ''
    ov_pos = stock.text(override_position)
    if ov_pos not in POSITION_TOKENS:
        ov_pos = ''

    sources = {}
    if code_pos:
        sources['code'] = code_pos
    if name_pos:
        sources['name'] = name_pos
    if maker_pos:
        sources['maker'] = maker_pos
    if ov_pos:
        sources['override'] = ov_pos

    positions = list(dict.fromkeys(sources.values()))
    conflict = len(positions) > 1
    position = ''
    needs_confirm = False
    if conflict:
        needs_confirm = True
        position = ''
    elif positions:
        position = positions[0]
    elif ov_pos:
        position = ov_pos

    base = code_base or source
    registered = attach_sku_position_suffix(base, position) if (base and position) else base

    return {
        'sourceCode': source,
        'baseCode': base,
        'position': position,
        'registeredCode': registered,
        'conflict': conflict,
        'needsConfirm': needs_confirm,
        'sources': sources,
    }


def codes_match_for_aux(map_code: str, aux_code: str) -> bool:
    """初期在庫CSVの品番とマップ品番の表記ゆれ照合（基礎一致含む）。"""
    a = stock.text(map_code)
    b = stock.text(aux_code)
    if not a or not b:
        return False
    if a == b:
        return True
    a_vars = set(sku_code_variants(a))
    b_vars = set(sku_code_variants(b))
    if a_vars & b_vars:
        return True
    a_base, _ = strip_sku_position_suffix(a)
    b_base, _ = strip_sku_position_suffix(b)
    # 基礎一致だけでは True にしない（上下未特定の自動振り分け防止）。
    # 表記ゆれ（スペース有無）のみここで許す。
    return bool(a_base and a_base == b_base and (
        set(sku_code_variants(a)) & set(sku_code_variants(b))
    ))


def position_compatible(map_position: str, aux_position: str) -> bool:
    """双方に区分があるときだけ一致必須。片方空は互換（他キーで絞る）。"""
    mp = stock.text(map_position)
    ap = stock.text(aux_position)
    if mp in POSITION_TOKENS and ap in POSITION_TOKENS:
        return mp == ap
    return True


def nfkc_alnum_key(value: str) -> str:
    """英数照合用（先頭ゼロは残す方針のため全体置換は呼び出し側で限定利用）。"""
    t = unicodedata.normalize('NFKC', stock.text(value) or '')
    return re.sub(r'[\s\u3000]+', '', t)
