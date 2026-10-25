"""旧CSV: 加工サービス（部品・資材／加工）の判定・名寄せ・本体分離。

表示の基本情報は「加工名＋加工業者」。対象衣服名は加工表示名に含めない。
プレビュー／再照合 alone では DB を変えない（呼び出し側の commit まで）。
"""
from __future__ import annotations

import re
import unicodedata

import product_ledger_paren as paren_classify
import stock

# 加工業者（正規化キー → 表示名）。K2 は常に加工業者。
_PROCESSING_VENDOR_DISPLAY = {
    'k2スタッフ工房': 'K2スタッフ工房',
    'k2スタツフ工房': 'K2スタッフ工房',
    '(有)エルムスコーポレーション': '(有)エルムスコーポレーション',
    '有エルムスコーポレーション': '(有)エルムスコーポレーション',
    'エルムスコーポレーション': '(有)エルムスコーポレーション',
    'エルムス': '(有)エルムスコーポレーション',
}

# エルムスコーポレーション専用: 仕入先行は品番・商品名にかかわらず「刺繍」1サービスへ。
ELMS_VENDOR_DISPLAY = '(有)エルムスコーポレーション'
ELMS_EMBROIDERY_SERVICE_NAME = '刺繍'
# 同一サービスとみなす加工名（表示は常に「刺繍」。旧「刺繍加工」等は再利用対象）
_ELMS_EMBROIDERY_ALIASES = (
    '刺繍',
    '刺繍入れ',
    '刺繍加工',
    'ししゅう',
    'ししゅういれ',
    'ししゅう加工',
)
_ELMS_VENDOR_KEYS = frozenset({
    'エルムスコーポレーション',
    'エルムス',
    '有エルムスコーポレーション',
})

# 実物部品・資材（加工サービスと区別）
_PHYSICAL_PARTS_MARKERS = (
    '生地', 'ボタン', '釦', '糸', '芯地', '裏地', 'ファスナー', 'チャック',
    'ゴム', 'テープ', 'ラベル', 'タグ', 'パッキン', '金具', 'フック',
    'ベルト部品', '芯', 'パッド',
)

# 取付・縫付などの表記ゆれ → 短い正規形（同一サービスへの名寄せ）
_ATTACH_VERB_CANON = (
    ('取り付け', '取付'),
    ('取付け', '取付'),
    ('縫い付け', '縫付'),
    ('縫付け', '縫付'),
)

# 加工名の別名 → 統一表示名（対象衣服名を含めない）
# paren の PROCESSING_TERMS（スナッパー・裾上げ等）とも揃える
_SERVICE_NAME_ALIASES = {
    '刺繍': '刺繍加工',
    '刺繍入れ': '刺繍加工',
    'ししゅう': '刺繍加工',
    'ししゅういれ': '刺繍加工',
    '刺繍加工': '刺繍加工',
    '七分袖加工': '七分袖加工',
    '七分袖': '七分袖加工',
    '半袖加工': '半袖加工',
    '長袖加工': '長袖加工',
    '袖加工': '袖加工',
    '丈加工': '丈加工',
    '丈直し': '丈直し',
    '裾上げ': '裾上げ',
    '裾直し': '裾直し',
    # 「加工」接尾が無くても明確な加工サービス（原文のまま。推測で別語へ置き換えない）
    'サイズダウン': 'サイズダウン',
    'マジックテープ': 'マジックテープ付け',
    'マジックテープ付け': 'マジックテープ付け',
    'リフレクター': 'リフレクター',
    '反射': '反射加工',
    '反射加工': '反射加工',
    'プリント': 'プリント加工',
    'プリント加工': 'プリント加工',
    'ネーム': 'ネーム加工',
    'ネーム加工': 'ネーム加工',
    'ワッペン': 'ワッペン付け',
    'ワッペン付け': 'ワッペン付け',
    'スナッパー': 'スナッパー加工',
    'スナッパー加工': 'スナッパー加工',
    'コーティング': 'コーティング',
    'アイロン': 'アイロン',
    'カット': 'カット',
    '縫製': '縫製',
    # ポケット取付系（「加工」文字なしでも明確な加工サービス）
    'ポケット取付': 'ポケット取付',
    'ポケット取り付け': 'ポケット取付',
    'ポケット取付け': 'ポケット取付',
    'ポケット縫付': 'ポケット取付',
    'ポケット縫い付け': 'ポケット取付',
    'ポケット縫付け': 'ポケット取付',
    '上衣加工': '上衣加工',
}

# 商品名から加工サービス名を拾うときの候補（長い語優先）
_SERVICE_NAME_CANDIDATES = sorted(
    {
        *_SERVICE_NAME_ALIASES.keys(),
        *_SERVICE_NAME_ALIASES.values(),
        '半袖加工＋リフレクター＋マジックテープ付け',
        '半袖加工+リフレクター+マジックテープ付け',
    },
    key=len,
    reverse=True,
)

# 明確な加工サービスを示す接尾（「加工」文字がなくても可）
_SERVICE_SUFFIX_RE = re.compile(
    r'(?:加工|取付|取り付け|取付け|縫付|縫い付け|縫付け|裾上げ|裾直し|丈直し|サイズダウン|付け)$'
)
# 顧客固有の「〜用」接頭（わらく堂用上衣加工 など）
_CUSTOMER_USE_RE = re.compile(r'^(.+?用)(.+)$')
_PAREN_INNER_RE = re.compile(r'[（(]([^（）()]*)[）)]')

_KIND_STRIP_RE = re.compile(
    r'(帽子|キャップ|ヘルメット|ベスト|パンツ|ズボン|スラックス|シャツ|ブラウス|'
    r'ブルゾン|ジャケット|コート|スカート|エプロン|手袋|グローブ|靴|シューズ|安全靴|'
    r'つなぎ|カバーオール|白衣|スクラブ|レインウェア|ポロシャツ|ポロ|'
    r'コックコート|ハンチング|ハンチング帽|調理服|割烹着)'
)

_COMPOSITION_SPLIT_RE = re.compile(r'[＋+・･＆&／/]+')
_SLASH_NAME_RE = re.compile(r'[／/]+')


def normalize_vendor_key(value: str) -> str:
    t = unicodedata.normalize('NFKC', stock.text(value) or '')
    t = t.replace('株式会社', '').replace('(株)', '').replace('（株）', '')
    t = t.replace('有限会社', '').replace('(有)', '').replace('（有）', '')
    # 残った括弧付き有表記もキーから落とす
    t = t.replace('(', '').replace(')', '').replace('（', '').replace('）', '')
    t = re.sub(r'[\s\u3000]+', '', t)
    return t.casefold()


def normalize_service_key(value: str) -> str:
    """照合用キー。取付／取り付け等の表記差も揃えて重複登録を防ぐ。"""
    t = canonicalize_attach_verbs(value)
    return t.casefold()


def is_elms_corporation_vendor(supplier_name: str) -> bool:
    """エルムスコーポレーション（表記差含む）か。無関係な業者には False。"""
    key = normalize_vendor_key(supplier_name)
    if not key:
        return False
    if key in _ELMS_VENDOR_KEYS:
        return True
    if 'エルムスコーポレーション' in key:
        return True
    # 表示マップ上のエルムス系キー（正規化後）
    for map_key, disp in _PROCESSING_VENDOR_DISPLAY.items():
        if 'エルムス' not in map_key and 'エルムス' not in normalize_vendor_key(disp):
            continue
        mk = normalize_vendor_key(map_key)
        if key == mk:
            return True
    return False


def is_elms_embroidery_alias(service_name: str) -> bool:
    """刺繍／刺繍入れ／刺繍加工など同一サービス別名か。"""
    sk = normalize_service_key(service_name)
    if not sk:
        return False
    return sk in {normalize_service_key(a) for a in _ELMS_EMBROIDERY_ALIASES}


def elms_embroidery_canonical_name(service_name: str = '') -> str:
    """エルムス刺繍サービスの統一表示名。別名でも常に「刺繍」。"""
    if service_name and not is_elms_embroidery_alias(service_name):
        # 呼び出し側が別名以外を渡してもエルムス強制時は刺繍へ
        pass
    return ELMS_EMBROIDERY_SERVICE_NAME


def is_processing_vendor(supplier_name: str) -> bool:
    key = normalize_vendor_key(supplier_name)
    if not key:
        return False
    if key in _PROCESSING_VENDOR_DISPLAY:
        return True
    # K2 を含む業者名は加工業者
    if 'k2' in key and 'スタッフ' in key:
        return True
    if is_elms_corporation_vendor(supplier_name):
        return True
    return False


def canonical_processing_vendor(supplier_name: str) -> str:
    """表示用の加工業者名。未知でも原文を返す（判定済みのとき）。"""
    raw = stock.text(supplier_name)
    if is_elms_corporation_vendor(raw):
        return ELMS_VENDOR_DISPLAY
    key = normalize_vendor_key(raw)
    if key in _PROCESSING_VENDOR_DISPLAY:
        return _PROCESSING_VENDOR_DISPLAY[key]
    for k, disp in _PROCESSING_VENDOR_DISPLAY.items():
        if k in key or key in k:
            return disp
    if is_processing_vendor(raw):
        # 正規化表示を少し整える
        t = unicodedata.normalize('NFKC', raw)
        t = re.sub(r'[\s\u3000]+', '', t)
        return t or raw
    return raw


def is_physical_parts_name(product_name: str) -> bool:
    """生地・ボタン等の実物部品・資材（加工サービスではない）。"""
    core = normalize_service_key(product_name)
    if not core:
        return False
    # 加工語が主なら部品扱いしない
    if any(normalize_service_key(a) in core for a in ('加工', '刺繍', 'プリント', '縫製')):
        return False
    for marker in _PHYSICAL_PARTS_MARKERS:
        mk = normalize_service_key(marker)
        if core == mk or core.startswith(mk) or core.endswith(mk):
            return True
    return False


def _alias_map():
    return {normalize_service_key(k): v for k, v in _SERVICE_NAME_ALIASES.items()}


def _canonical_from_term(term: str) -> str:
    aliases = _alias_map()
    tk = normalize_service_key(term)
    if tk in aliases:
        return aliases[tk]
    for k, v in _SERVICE_NAME_ALIASES.items():
        if normalize_service_key(v) == tk:
            return v
    return stock.text(term)


def canonicalize_attach_verbs(value: str) -> str:
    """取り付け／取付け → 取付、縫い付け → 縫付 など表記差を揃える。"""
    t = unicodedata.normalize('NFKC', stock.text(value) or '')
    t = re.sub(r'[\s\u3000]+', '', t)
    for src, dst in _ATTACH_VERB_CANON:
        t = t.replace(src, dst)
    return t


def _strip_parens_keep_inner(value: str):
    """括弧を外した本体と、括弧内テキストのリスト。"""
    nfkc = unicodedata.normalize('NFKC', stock.text(value) or '')
    inners = [stock.text(m.group(1)) for m in _PAREN_INNER_RE.finditer(nfkc) if stock.text(m.group(1))]
    base = _PAREN_INNER_RE.sub('', nfkc)
    base = re.sub(r'[\s\u3000]+', '', base)
    return base, inners


def _strip_customer_use_prefix(value: str):
    """『わらく堂用上衣加工』→ (『わらく堂用』, 『上衣加工』)。無ければ ('', value)。"""
    t = stock.text(value) or ''
    m = _CUSTOMER_USE_RE.match(t)
    if not m:
        return '', t
    prefix, rest = stock.text(m.group(1)), stock.text(m.group(2))
    if not rest or len(rest) < 2:
        return '', t
    return prefix, rest


def _looks_like_service_core(core: str) -> bool:
    """衣服種を除いた核が明確な加工サービス名か。業者名だけでは True にしない。"""
    if not core or len(core) < 2:
        return False
    if re.search(r'[＋+]', core):
        return False
    if is_physical_parts_name(core):
        return False
    aliases = _alias_map()
    sk = normalize_service_key(core)
    if sk in aliases:
        return True
    if '刺繍' in core:
        return True
    if core.endswith('加工') and len(core) >= 3:
        return True
    if _SERVICE_SUFFIX_RE.search(core):
        # 接尾だけの語（「取付」単体など）は対象物が無いので不可
        stem = _SERVICE_SUFFIX_RE.sub('', core)
        return bool(stem) and len(stem) >= 1
    return False


def _resolve_service_core(core: str) -> str:
    """核テキストを統一加工名へ。不明なら空。原文にない具体化はしない。"""
    aliases = _alias_map()
    cleaned = canonicalize_attach_verbs(core)
    sk = normalize_service_key(cleaned)
    if sk in aliases:
        return aliases[sk]
    for term in _SERVICE_NAME_CANDIDATES:
        tk = normalize_service_key(term)
        if sk == tk or (tk and sk.endswith(tk) and _looks_like_service_core(
            cleaned[-(len(term)):] if len(cleaned) >= len(term) else cleaned
        )):
            # endswith 候補は核全体が候補語で終わるときだけ採用
            if sk == tk or sk.endswith(tk):
                # 衣服種＋加工語（コックコート七分袖加工）→ 候補語側
                return _canonical_from_term(term)
    if '刺繍' in cleaned:
        return '刺繍加工'
    if cleaned.endswith('加工') and len(cleaned) >= 3:
        return aliases.get(sk, cleaned)
    if _SERVICE_SUFFIX_RE.search(cleaned) and _looks_like_service_core(cleaned):
        return aliases.get(sk, cleaned)
    return ''


def unify_processing_service_name(raw_name: str) -> str:
    """加工サービス表示名へ統一。衣服種・顧客用接頭・括弧仕様は落とす。読めなければ空。

    「ポケット取付」のように『加工』文字が無くても、取付／縫付等の明確な加工名は認識する。
    「袖加工」を「七分袖加工」へ置き換えるなど原文にない具体化はしない。
    """
    raw = stock.text(raw_name)
    if not raw:
        return ''
    aliases = _alias_map()
    # 構成加工（半袖＋リフレクター＋…）は接尾マッチより先にまとめて保持
    composed = _extract_composition_label(raw)
    if composed:
        return composed
    # スラッシュ区切りなら末尾側（加工側）を優先
    parts = [stock.text(p) for p in _SLASH_NAME_RE.split(raw) if stock.text(p)]
    candidates = list(reversed(parts)) if len(parts) > 1 else [raw]
    if raw not in candidates:
        candidates.append(raw)

    for cand in candidates:
        key = normalize_service_key(canonicalize_attach_verbs(cand))
        if key in aliases:
            return aliases[key]

        base, _inners = _strip_parens_keep_inner(cand)
        _cust, without_cust = _strip_customer_use_prefix(base)
        for trial in (without_cust, base, cand):
            stripped = _KIND_STRIP_RE.sub('', trial)
            stripped = re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', stripped))
            stripped = canonicalize_attach_verbs(stripped)
            if not stripped:
                continue
            # 単一核に ＋ が残る場合は構成として扱う（勝手に1加工へ潰さない）
            if re.search(r'[＋+]', stripped):
                sub = _extract_composition_label(stripped)
                if sub:
                    return sub
                continue
            resolved = _resolve_service_core(stripped)
            if resolved:
                return resolved
            # 衣服種を残したまま末尾だけ加工語のとき（コックコート七分袖加工）
            for term in _SERVICE_NAME_CANDIDATES:
                tk = normalize_service_key(term)
                sk = normalize_service_key(stripped)
                if tk and sk.endswith(tk):
                    return _canonical_from_term(term)

    return ''


def _extract_composition_label(raw: str) -> str:
    """半袖加工＋リフレクター＋マジックテープ付け のような構成を保持。"""
    nfkc = unicodedata.normalize('NFKC', stock.text(raw) or '')
    if not re.search(r'[＋+]', nfkc):
        return ''
    bits = [stock.text(b) for b in _COMPOSITION_SPLIT_RE.split(nfkc) if stock.text(b)]
    if len(bits) < 2:
        return ''
    unified = []
    alias_map = {normalize_service_key(k): v for k, v in _SERVICE_NAME_ALIASES.items()}
    for b in bits:
        cleaned = _KIND_STRIP_RE.sub('', b)
        cleaned = re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', cleaned))
        cleaned = canonicalize_attach_verbs(cleaned)
        if not cleaned:
            continue
        unified.append(alias_map.get(normalize_service_key(cleaned), cleaned))
    if len(unified) < 2:
        return ''
    return '＋'.join(unified)


def extract_row_specific_specs(product_name: str, paren_processing: str = '') -> dict:
    """共通加工名とは別に、位置・寸法・顧客固有仕様を保持する。

    例: わらく堂用上衣加工（衿・裾・袖）
      → customerUse=わらく堂用 / parenSpecs=衿・裾・袖 / 共通名は上衣加工
    """
    raw = stock.text(product_name)
    specs = {
        'originalName': raw,
        'parenProcessing': stock.text(paren_processing or ''),
        'position': '',
        'dimensions': '',
        'embroideryText': '',
        'composition': '',
        'customerSpecific': '',
        'customerUse': '',
        'parenSpecs': '',
    }
    nfkc = unicodedata.normalize('NFKC', raw)
    base, inners = _strip_parens_keep_inner(nfkc)
    if inners:
        specs['parenSpecs'] = '・'.join(dict.fromkeys(inners))
    cust, _rest = _strip_customer_use_prefix(base)
    if cust:
        specs['customerUse'] = cust
    # 股下・仕上がり寸法
    dim_hits = re.findall(
        r'((?:股下|仕上がり丈|仕上り丈|裾丈|丈)\s*\d{2,3}(?:\.\d+)?(?:cm|㎝|センチ)?)',
        nfkc,
        flags=re.IGNORECASE,
    )
    if dim_hits:
        specs['dimensions'] = '・'.join(dict.fromkeys(dim_hits))
    # 位置っぽい語（括弧内仕様も位置候補に含めるが、顧客固有仕様を優先表示）
    pos_hits = re.findall(
        r'((?:左胸|右胸|胸|背中|袖|襟|衿|裾|前|後|左|右)(?:側)?(?:上|下|中央)?)',
        nfkc,
    )
    if pos_hits:
        specs['position'] = '・'.join(dict.fromkeys(pos_hits))
    composed = _extract_composition_label(raw)
    if composed:
        specs['composition'] = composed
    # 個別仕様テキスト（勝手に別加工へ置き換えないための補足）
    bits = []
    if specs['customerUse']:
        bits.append(specs['customerUse'])
    if specs['parenSpecs']:
        bits.append(specs['parenSpecs'])
    elif specs['parenProcessing'] and specs['parenProcessing'] not in (
        specs['composition'], specs['dimensions'],
    ):
        bits.append(specs['parenProcessing'])
    if bits:
        specs['customerSpecific'] = '／'.join(bits)
    return specs


def service_dedupe_key(service_name: str, vendor_name: str) -> str:
    name = stock.text(service_name)
    vendor = stock.text(vendor_name)
    # エルムス行は加工名を「刺繍」へ寄せるため、業者ごとに1サービスへ集約
    if is_elms_corporation_vendor(vendor):
        return (
            f'{normalize_service_key(ELMS_EMBROIDERY_SERVICE_NAME)}|'
            f'{normalize_vendor_key(ELMS_VENDOR_DISPLAY)}'
        )
    return f'{normalize_service_key(name)}|{normalize_vendor_key(vendor)}'


def classify_processing_service_row(
    *,
    supplier: str,
    product_name: str,
    code: str = '',
    paren_processing: str = '',
) -> dict:
    """1行が加工サービス行か判定する。

    戻り値:
      isProcessingVendor / isPhysicalParts / isProcessingService
      serviceName / vendorName / needsConfirm / confirmReasons
      rowSpecs / targetGarmentCode / identifiable
    """
    supplier_t = stock.text(supplier)
    name_t = stock.text(product_name)
    code_t = stock.text(code)
    vendor = is_processing_vendor(supplier_t)
    physical = is_physical_parts_name(name_t)
    elms = is_elms_corporation_vendor(supplier_t)
    result = {
        'isProcessingVendor': vendor,
        'isPhysicalParts': physical,
        'isProcessingService': False,
        'serviceName': '',
        'vendorName': canonical_processing_vendor(supplier_t) if vendor else '',
        'needsConfirm': False,
        'confirmReasons': [],
        'rowSpecs': extract_row_specific_specs(name_t, paren_processing),
        'targetGarmentCode': code_t if code_t else '',
        'identifiable': False,
        'excludeFromGarmentSuppliers': vendor,
        'elmsEmbroideryRule': False,
    }
    # エルムスコーポレーション: 品番・商品名・実物部品らしさにかかわらず「刺繍」へ確定
    # （エプロン／ポロシャツ袖／サンプル／ワッペン作製／空欄／刺繍入れ も同じ）
    if elms:
        service = ELMS_EMBROIDERY_SERVICE_NAME
        result['isProcessingVendor'] = True
        result['isPhysicalParts'] = False
        result['isProcessingService'] = True
        result['serviceName'] = service
        result['displayName'] = service
        result['vendorName'] = ELMS_VENDOR_DISPLAY
        result['identifiable'] = True
        result['needsConfirm'] = False
        result['confirmReasons'] = []
        result['missingFields'] = []
        result['excludeFromGarmentSuppliers'] = True
        result['elmsEmbroideryRule'] = True
        # 元品番が衣服SKUっぽくても、加工サービス共通名は刺繍のまま。
        # 品番「刺繍」「刺繍入れ」も対象コードとして保持（初期在庫照合用）
        result['rowSpecs'] = extract_row_specific_specs(name_t, paren_processing)
        result['rowSpecs']['originalCode'] = code_t
        result['rowSpecs']['forcedServiceRule'] = 'elms_embroidery'
        return result

    if physical:
        return result
    if not vendor:
        # 仕入先が加工業者でなくても、名が明確な加工のみ＋既知パターンはサービス候補にしない
        # （本体行の括弧加工は取扱へ。ここでは業者行のみサービス化する）
        return result

    # 加工業者行
    service = unify_processing_service_name(name_t)
    if not service and paren_processing:
        service = unify_processing_service_name(paren_processing)
    if service:
        result['isProcessingService'] = True
        result['serviceName'] = service
        # 表示名の唯一の入力元は加工名（別項目を持たず常に同期）
        result['displayName'] = service
        result['identifiable'] = True
        # 構成があれば specs に残す
        if result['rowSpecs'].get('composition') and result['rowSpecs']['composition'] != service:
            pass
        elif '＋' in service:
            result['rowSpecs']['composition'] = service
        return result

    # 業者は加工だが加工名が読めない → 架空加工を作らず確認
    # 業者名だけを根拠に不明な加工内容を推測しない
    # 表示名（予定名）から無条件に加工名を補完しない
    result['needsConfirm'] = True
    result['displayName'] = ''
    missing = []
    if not stock.text(name_t):
        missing.append('加工名（原文名が空）')
    else:
        missing.append(f'加工名（原文「{name_t}」から明確な加工内容を特定できない）')
    if not result['vendorName'] and not supplier_t:
        missing.append('加工業者')
    result['confirmReasons'] = [
        f'加工業者「{result["vendorName"] or supplier_t or "（不明）"}」行ですが、'
        f'{"・".join(missing)}。架空の加工サービスは自動作成しません。'
        f'加工名と加工業者が分かる場合のみ手入力してください。'
    ]
    result['missingFields'] = missing
    return result


def filter_garment_supplier_names(names):
    """本体の仕入先候補から加工業者を除く。"""
    out = []
    seen = set()
    for n in names or []:
        t = stock.text(n)
        if not t or is_processing_vendor(t):
            continue
        key = normalize_vendor_key(t)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def garment_body_name_from_variants(names, *, service_name=''):
    """同一品番の本体候補名から衣服本体名を推定。加工名だけの行は除外。"""
    service_key = normalize_service_key(service_name) if service_name else ''
    scored = []
    for n in names or []:
        t = stock.text(n)
        if not t:
            continue
        if is_physical_parts_name(t):
            continue
        # 加工サービス名そのもの／スラッシュ末尾だけ加工 → 本体名にしない
        unified = unify_processing_service_name(t)
        core = normalize_service_key(t)
        if unified and (
            core == normalize_service_key(unified)
            or (service_key and core == service_key)
        ):
            continue
        kind_hit = bool(_KIND_STRIP_RE.search(t))
        body = t
        if unified:
            for part in _SLASH_NAME_RE.split(unicodedata.normalize('NFKC', t)):
                part_t = stock.text(part)
                if not part_t:
                    continue
                if unify_processing_service_name(part_t) == unified and not _KIND_STRIP_RE.search(part_t):
                    continue
                body = part_t
                break
            if body.endswith(unified):
                body = stock.text(body[: -len(unified)]) or body
        body = stock.text(body) or t
        if unify_processing_service_name(body) and not _KIND_STRIP_RE.search(body):
            continue
        scored.append((1 if kind_hit else 0, len(body), body))
    if not scored:
        return ''
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return scored[0][2]


def _as_product_dict(m):
    if m is None:
        return None
    if isinstance(m, dict):
        return m
    try:
        return dict(m)
    except (TypeError, ValueError):
        return m


def _product_field(m, key):
    if m is None:
        return ''
    if isinstance(m, dict):
        return m.get(key) or ''
    try:
        return m[key] if key in m.keys() else ''
    except (TypeError, KeyError, IndexError):
        return ''


def embroidery_alias_lookup_names(service_name: str):
    """既存照合用の名前候補（刺繍系は別名をすべて含める）。"""
    name = stock.text(service_name)
    names = []
    if name:
        names.append(name)
    if is_elms_embroidery_alias(name) or name == ELMS_EMBROIDERY_SERVICE_NAME:
        for alias in _ELMS_EMBROIDERY_ALIASES:
            if alias not in names:
                names.append(alias)
    # 重複除去（順序維持）
    out = []
    seen = set()
    for n in names:
        k = normalize_service_key(n)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(n)
    return out


def find_existing_processing_services(c, service_name: str, vendor_name: str):
    """同業者の部品・資材（加工）を別名込みで列挙。複数件あり得る。"""
    name = stock.text(service_name)
    vendor = stock.text(vendor_name)
    if not name or not vendor:
        return []
    import legacy_csv_import as legacy

    vendor_ids = []
    key = normalize_vendor_key(vendor)
    elms = is_elms_corporation_vendor(vendor)
    for row in c.execute('SELECT id, name FROM suppliers WHERE active=1'):
        rk = normalize_vendor_key(row['name'])
        if rk == key or (elms and is_elms_corporation_vendor(row['name'])):
            vendor_ids.append(row['id'])
    if not vendor_ids:
        return []

    lookup_names = embroidery_alias_lookup_names(name)
    if elms:
        # エルムスは常に刺繍サービスとして別名も探す
        lookup_names = embroidery_alias_lookup_names(ELMS_EMBROIDERY_SERVICE_NAME)

    by_id = {}
    for vid in vendor_ids:
        for n in lookup_names:
            for m in legacy.find_blank_sku_products(c, n, vid):
                mid = _product_field(m, 'id')
                if mid in (None, ''):
                    continue
                by_id[int(mid)] = _as_product_dict(m)
    matches = list(by_id.values())
    parts = [
        m for m in matches
        if stock.text(_product_field(m, 'category')) == stock.CATEGORY_PARTS
    ]
    return parts or matches


def find_existing_processing_service(c, service_name: str, vendor_name: str):
    """同名＋同業者の部品・資材（加工）を探す。

    刺繍系別名（刺繍／刺繍入れ／刺繍加工）は同一サービスとして再利用する。
    同一業者で別名の加工サービスが複数ある場合は、正規名「刺繍」を優先し、
    なければ最初の1件を返す（実データの削除・統合はしない。呼び出し側で重複報告可）。
    """
    name = stock.text(service_name)
    vendor = stock.text(vendor_name)
    if not name or not vendor:
        return None
    pool = find_existing_processing_services(c, name, vendor)
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]

    # 複数: 正規名一致を優先、次に刺繍加工、それ以外は id 昇順の先頭
    preferred_order = [ELMS_EMBROIDERY_SERVICE_NAME, '刺繍加工', '刺繍入れ']
    for pref in preferred_order:
        pref_key = normalize_service_key(pref)
        hits = [
            m for m in pool
            if normalize_service_key(_product_field(m, 'name')) == pref_key
        ]
        if len(hits) == 1:
            return hits[0]
        if hits:
            hits.sort(key=lambda m: int(_product_field(m, 'id') or 0))
            return hits[0]
    pool_sorted = sorted(pool, key=lambda m: int(_product_field(m, 'id') or 0))
    return pool_sorted[0]


def describe_processing_service_duplicates(c, service_name: str, vendor_name: str):
    """重複候補の報告用（実データは変更しない）。"""
    pool = find_existing_processing_services(c, service_name, vendor_name)
    if len(pool) <= 1:
        return {
            'hasDuplicates': False,
            'count': len(pool),
            'products': [],
            'suggestedKeepId': (pool[0].get('id') if pool else None),
            'consolidationHint': '',
        }
    prefer = find_existing_processing_service(c, service_name, vendor_name)
    products = []
    for m in pool:
        products.append({
            'id': _product_field(m, 'id'),
            'name': _product_field(m, 'name'),
            'code': _product_field(m, 'code'),
            'category': _product_field(m, 'category'),
            'supplierId': _product_field(m, 'supplier_id'),
        })
    keep_id = prefer.get('id') if prefer else None
    return {
        'hasDuplicates': True,
        'count': len(pool),
        'products': products,
        'suggestedKeepId': keep_id,
        'consolidationHint': (
            f'同一業者の刺繍系加工サービスが {len(pool)} 件あります。'
            f'参照（customer_products / product_ledger_row_maps / 在庫ロット）を'
            f' suggestedKeepId={keep_id} へ付け替えたうえで、他方を停止または統合してください。'
            f'自動削除は行いません。'
        ),
    }
