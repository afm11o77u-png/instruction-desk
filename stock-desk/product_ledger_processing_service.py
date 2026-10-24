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

# 実物部品・資材（加工サービスと区別）
_PHYSICAL_PARTS_MARKERS = (
    '生地', 'ボタン', '釦', '糸', '芯地', '裏地', 'ファスナー', 'チャック',
    'ゴム', 'テープ', 'ラベル', 'タグ', 'パッキン', '金具', 'フック',
    'ベルト部品', '芯', 'パッド',
)

# 加工名の別名 → 統一表示名（対象衣服名を含めない）
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
    '丈加工': '丈加工',
    '裾上げ': '裾上げ',
    '裾直し': '裾直し',
    'マジックテープ': 'マジックテープ付け',
    'マジックテープ付け': 'マジックテープ付け',
    'リフレクター': 'リフレクター',
    '反射': '反射加工',
    'プリント': 'プリント加工',
    'ネーム': 'ネーム加工',
    'ワッペン': 'ワッペン付け',
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
    t = unicodedata.normalize('NFKC', stock.text(value) or '')
    t = re.sub(r'[\s\u3000]+', '', t)
    return t.casefold()


def is_processing_vendor(supplier_name: str) -> bool:
    key = normalize_vendor_key(supplier_name)
    if not key:
        return False
    if key in _PROCESSING_VENDOR_DISPLAY:
        return True
    # K2 を含む業者名は加工業者
    if 'k2' in key and 'スタッフ' in key:
        return True
    if 'エルムス' in key:
        return True
    return False


def canonical_processing_vendor(supplier_name: str) -> str:
    """表示用の加工業者名。未知でも原文を返す（判定済みのとき）。"""
    raw = stock.text(supplier_name)
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


def unify_processing_service_name(raw_name: str) -> str:
    """加工サービス表示名へ統一。衣服種は落とす。読めなければ空。"""
    raw = stock.text(raw_name)
    if not raw:
        return ''
    aliases = _alias_map()
    # スラッシュ区切りなら末尾側（加工側）を優先
    parts = [stock.text(p) for p in _SLASH_NAME_RE.split(raw) if stock.text(p)]
    candidates = list(reversed(parts)) if len(parts) > 1 else [raw]
    if raw not in candidates:
        candidates.append(raw)

    for cand in candidates:
        key = normalize_service_key(cand)
        if key in aliases:
            return aliases[key]
        stripped = _KIND_STRIP_RE.sub('', cand)
        stripped = re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', stripped))
        sk = normalize_service_key(stripped)
        if sk in aliases:
            return aliases[sk]
        for term in _SERVICE_NAME_CANDIDATES:
            tk = normalize_service_key(term)
            if sk == tk or (tk and sk.endswith(tk)):
                return _canonical_from_term(term)

    composed = _extract_composition_label(raw)
    if composed:
        return composed

    nfkc = unicodedata.normalize('NFKC', raw)
    for part in (list(reversed(parts)) if parts else [nfkc]):
        cleaned = _KIND_STRIP_RE.sub('', part)
        cleaned = re.sub(r'[\s\u3000]+', '', cleaned)
        if cleaned.endswith('加工') and len(cleaned) >= 3:
            return aliases.get(normalize_service_key(cleaned), cleaned)
        if '刺繍' in cleaned:
            return '刺繍加工'
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
        if not cleaned:
            continue
        unified.append(alias_map.get(normalize_service_key(cleaned), cleaned))
    if len(unified) < 2:
        return ''
    return '＋'.join(unified)


def extract_row_specific_specs(product_name: str, paren_processing: str = '') -> dict:
    """共通加工名とは別に、位置・寸法・顧客固有仕様を保持する。"""
    raw = stock.text(product_name)
    specs = {
        'originalName': raw,
        'parenProcessing': stock.text(paren_processing or ''),
        'position': '',
        'dimensions': '',
        'embroideryText': '',
        'composition': '',
        'customerSpecific': '',
    }
    nfkc = unicodedata.normalize('NFKC', raw)
    # 股下・仕上がり寸法
    dim_hits = re.findall(
        r'((?:股下|仕上がり丈|仕上り丈|裾丈|丈)\s*\d{2,3}(?:\.\d+)?(?:cm|㎝|センチ)?)',
        nfkc,
        flags=re.IGNORECASE,
    )
    if dim_hits:
        specs['dimensions'] = '・'.join(dict.fromkeys(dim_hits))
    # 位置っぽい語
    pos_hits = re.findall(
        r'((?:左胸|右胸|胸|背中|袖|襟|裾|前|後|左|右)(?:側)?(?:上|下|中央)?)',
        nfkc,
    )
    if pos_hits:
        specs['position'] = '・'.join(dict.fromkeys(pos_hits))
    composed = _extract_composition_label(raw)
    if composed:
        specs['composition'] = composed
    # 括弧内の加工が残っていれば個別仕様の一部
    if specs['parenProcessing'] and specs['parenProcessing'] not in (
        specs['composition'], specs['dimensions'],
    ):
        specs['customerSpecific'] = specs['parenProcessing']
    return specs


def service_dedupe_key(service_name: str, vendor_name: str) -> str:
    return f'{normalize_service_key(service_name)}|{normalize_vendor_key(vendor_name)}'


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
    }
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
        result['identifiable'] = True
        # 構成があれば specs に残す
        if result['rowSpecs'].get('composition') and result['rowSpecs']['composition'] != service:
            pass
        elif '＋' in service:
            result['rowSpecs']['composition'] = service
        return result

    # 業者は加工だが加工名が読めない → 架空加工を作らず確認
    result['needsConfirm'] = True
    result['confirmReasons'] = [
        f'加工業者「{result["vendorName"] or supplier_t}」行ですが、加工名を特定できません'
        f'（原文「{name_t}」）。架空の加工サービスは自動作成しません。'
    ]
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


def find_existing_processing_service(c, service_name: str, vendor_name: str):
    """同名＋同業者の部品・資材（加工）を探す。"""
    name = stock.text(service_name)
    vendor = stock.text(vendor_name)
    if not name or not vendor:
        return None
    # 品番なし＋名前＋仕入先
    import legacy_csv_import as legacy

    vendor_id = None
    key = normalize_vendor_key(vendor)
    for row in c.execute('SELECT id, name FROM suppliers WHERE active=1'):
        if normalize_vendor_key(row['name']) == key:
            vendor_id = row['id']
            break
    if vendor_id is None:
        return None
    matches = legacy.find_blank_sku_products(c, name, vendor_id)

    def _field(m, key):
        if m is None:
            return ''
        if isinstance(m, dict):
            return m.get(key) or ''
        try:
            return m[key] if key in m.keys() else ''
        except (TypeError, KeyError, IndexError):
            return ''

    # カテゴリ部品・資材を優先
    parts = [
        m for m in matches
        if stock.text(_field(m, 'category')) == stock.CATEGORY_PARTS
    ]
    pool = parts or matches
    if len(pool) == 1:
        return dict(pool[0]) if not isinstance(pool[0], dict) else pool[0]
    # 名前キー一致で絞る
    name_key = normalize_service_key(name)
    tight = [
        m for m in pool
        if normalize_service_key(_field(m, 'name')) == name_key
    ]
    if len(tight) == 1:
        return dict(tight[0]) if not isinstance(tight[0], dict) else tight[0]
    return None
