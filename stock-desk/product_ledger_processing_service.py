"""旧CSV: 加工サービス（CATEGORY_PROCESS_SERVICE）の判定・名寄せ・本体分離。

表示の基本情報は「加工名＋加工業者」。対象衣服名は加工表示名に含めない。
プレビュー／再照合 alone では DB を変えない（呼び出し側の commit まで）。
既存の部品・資材へ誤登録された加工は inventory で候補列挙のみ（自動移行しない）。
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
    'エルム': '(有)エルムスコーポレーション',
}

K2_VENDOR_DISPLAY = 'K2スタッフ工房'
K2_GARMENT_CHOICE_NOTE = (
    'K2スタッフ工房の衣服らしい行です（持ち込み生地での製作など）。既定は加工サービスで登録します。'
    '制服として登録する場合は登録区分を「商品」に、不要なら「この行を登録しない」を選んでください。'
)
K2_AUTO_EXCLUDE_REASON = (
    'K2スタッフ工房の加工行ですが、加工内容を特定できないため登録対象外'
)
_K2_VENDOR_KEYS = frozenset({
    'k2スタッフ工房',
    'k2スタツフ工房',
})

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
    'エルム',
    '有エルムスコーポレーション',
})

# 実物部品・資材（加工サービスと区別）
_PHYSICAL_PARTS_MARKERS = (
    '生地', 'ボタン', '釦', '糸', '芯地', '裏地', 'ファスナー', 'チャック',
    'ゴム', 'テープ', 'ラベル', 'タグ', 'パッキン', '金具', 'フック', 'リボン',
    'ベルト部品', '芯', 'パッド',
)

# 名前の末尾がこれなら実物部品ではなく作業（加工サービス）
_WORK_SUFFIXES = (
    '補修', '交換', '修理', '直し', '作製', '取付', '取り付け', '付け', '縫付', '縫い付け',
    '付替', '付け替え', '詰め',
)

# 取付・縫付・付替などの表記ゆれ → 短い正規形（同一サービスへの名寄せ）
_ATTACH_VERB_CANON = (
    ('付け替え', '付替'),
    ('付替え', '付替'),
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
    # ポケット付替系（取付とは別サービス。付け替え／付替えは同一）
    'ポケット付替': 'ポケット付替',
    'ポケット付け替え': 'ポケット付替',
    'ポケット付替え': 'ポケット付替',
    '上衣加工': '上衣加工',
    # K2 実務で確認済みの加工名（衣服種だけの名称は対象外。帽子加工は「加工」へ落とさない）
    '帽子加工': '帽子加工',
    'パンツウエスト直し': 'パンツウエスト直し',
    'ウエスト直し': 'パンツウエスト直し',
    'パンツ丈上げ': 'パンツ丈上げ',
    '丈上げ': 'パンツ丈上げ',
    # 全角・半角空白差は normalize_service_key で吸収。表示は空白なし
    'エプロンカン止': 'エプロンカン止',
    'エプロン カン止': 'エプロンカン止',
    'エプロン　カン止': 'エプロンカン止',
    'カン止': 'エプロンカン止',
    # K2 実務で確認済み（「加工」接尾が無くても明確な加工内容）
    'ほつれ補修': 'ほつれ補修',
    'ホツレ補修': 'ほつれ補修',
    '綻び補修': 'ほつれ補修',
    'インナー外し': 'インナー外し',
    'インナー取り外し': 'インナー外し',
    'インナー取外し': 'インナー外し',
    'インナー取付': 'インナー取付',
    'インナー取り付け': 'インナー取付',
    'インナー取付け': 'インナー取付',
    'インナー付け': 'インナー取付',
    # ネット付けは取付とは別サービス（原文を保持）
    'インナーネット付け': 'インナーネット付け',
    'インナーネット取付': 'インナーネット付け',
    'インナーネット取り付け': 'インナーネット付け',
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
    r'(?:加工|取付|取り付け|取付け|縫付|縫い付け|縫付け|付替|付け替え|付替え|'
    r'裾上げ|裾直し|丈直し|丈上げ|ウエスト直し|カン止|'
    r'サイズダウン|付け|補修|外し|取り外し|取外し|'
    r'交換|修理|直し|作製|貼替|張替|詰め|潰し)$'
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


_SERVICE_MARKER_RE = re.compile(r'^[＊*※☆★◆◇●○・]+|[＊*※☆★◆◇●○・]+$')


def strip_service_markers(value: str) -> str:
    """先頭・末尾の ＊ ※ ☆ などの印を外す（中身の文字は変えない）。"""
    t = stock.text(value)
    prev = None
    while prev != t:
        prev = t
        t = _SERVICE_MARKER_RE.sub('', t).strip()
    return t


# カタカナ語の語末の長音「ー」の有無だけの差（スナッパ／スナッパー）は同じ加工とみなす。
# 語中の「ー」や、カタカナ1文字＋ー（例: 「ノー」）は変えない。
_KATA = '\u30a1-\u30fa\u30fc'
_WORD_END_LONG_VOWEL_RE = re.compile(rf'(?<=[\u30a1-\u30fa][\u30a1-\u30fa])ー+(?![{_KATA}])')


def _fold_word_end_long_vowel(t: str) -> str:
    return _WORD_END_LONG_VOWEL_RE.sub('', t)


def prefer_service_display_name(current: str, candidate: str) -> str:
    """同じ照合キーの表示名のうち、語末の長音「ー」がある方を正とする。"""
    cur, cand = stock.text(current), stock.text(candidate)
    if not cur:
        return cand
    if not cand or normalize_service_key(cur) != normalize_service_key(cand):
        return cur
    return cand if cand.count('ー') > cur.count('ー') else cur


# 同じ文字列の正規化を取り込み中に繰り返さない。上限を超えたら捨てて作り直す（結果は同じ）。
# 別名表・候補・表記ゆれ表を別オブジェクトへ差し替えたときは破棄する。
# 辞書の中身だけをその場で書き換えた場合は拾わない（定義はモジュール定数）。
_NORM_CACHE_MAX = 16384
_NORM_KEY_MAX_LEN = 512
_norm_cache: dict[str, str] = {}
_verb_cache: dict[str, str] = {}
_compiled_epoch = None
_compiled_index = None


def _service_tables_epoch():
    return (
        id(_SERVICE_NAME_ALIASES),
        id(_SERVICE_NAME_CANDIDATES),
        id(_ATTACH_VERB_CANON),
        id(_PHYSICAL_PARTS_MARKERS),
        id(_WORK_SUFFIXES),
    )


def clear_service_name_cache():
    """キャッシュを捨てる。テストで別名表を差し替えた直後に使う。"""
    global _compiled_epoch, _compiled_index
    _norm_cache.clear()
    _verb_cache.clear()
    _compiled_epoch = None
    _compiled_index = None


def _canonicalize_attach_verbs_body(value) -> str:
    t = unicodedata.normalize('NFKC', stock.text(value) or '')
    t = re.sub(r'[\s\u3000]+', '', t)
    for src, dst in _ATTACH_VERB_CANON:
        t = t.replace(src, dst)
    return t


def _normalize_service_key_body(value) -> str:
    """照合用キーの本体。キャッシュを通さない。"""
    t = unicodedata.normalize('NFKC', stock.text(value) or '')
    t = re.sub(r'[\s\u3000]+', '', t)
    t = strip_service_markers(t)
    t = _canonicalize_attach_verbs_body(t)
    t = _fold_word_end_long_vowel(t)
    return t.casefold()


def _ensure_service_index():
    """別名表と候補語の正規化結果を1回だけ組む。"""
    global _compiled_epoch, _compiled_index
    epoch = _service_tables_epoch()
    if _compiled_index is not None and _compiled_epoch == epoch:
        return _compiled_index
    _norm_cache.clear()
    _verb_cache.clear()
    alias_by_key = {}
    for key, display in _SERVICE_NAME_ALIASES.items():
        alias_by_key[_normalize_service_key_body(key)] = display
    _compiled_index = {
        'alias_by_key': alias_by_key,
        'value_keys': tuple(
            (_normalize_service_key_body(display), display)
            for display in _SERVICE_NAME_ALIASES.values()
        ),
        'candidates': tuple(
            (term, _normalize_service_key_body(term))
            for term in _SERVICE_NAME_CANDIDATES
        ),
        'marker_keys': tuple(
            _normalize_service_key_body(marker) for marker in _PHYSICAL_PARTS_MARKERS
        ),
        'work_keys': tuple(
            _normalize_service_key_body(suffix) for suffix in _WORK_SUFFIXES
        ),
        'service_word_keys': tuple(
            _normalize_service_key_body(word)
            for word in ('加工', '刺繍', 'プリント', '縫製')
        ),
    }
    _compiled_epoch = epoch
    return _compiled_index


def _remember(cache: dict, key: str, result: str) -> str:
    if len(cache) >= _NORM_CACHE_MAX:
        cache.clear()
    cache[key] = result
    return result


def normalize_service_key(value: str) -> str:
    """照合用キー。全角半角・空白・前後の印・取付／取り付け等の表記差を揃えて重複登録を防ぐ。

    「ポケット加工」と「ポケット付け」のように語が違うものは別のまま（加工の有無で寄せない）。
    文字列以外と極端に長い入力はキャッシュしない（結果の計算は同じ）。
    """
    _ensure_service_index()
    if isinstance(value, str) and len(value) <= _NORM_KEY_MAX_LEN:
        try:
            return _norm_cache[value]
        except KeyError:
            return _remember(_norm_cache, value, _normalize_service_key_body(value))
    return _normalize_service_key_body(value)


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


def is_k2_staff_workshop(supplier_name: str) -> bool:
    """K2スタッフ工房（全角・半角・空白・法人格表記差含む）か。"""
    key = normalize_vendor_key(supplier_name)
    if not key:
        return False
    if key in _K2_VENDOR_KEYS:
        return True
    if 'k2' in key and 'スタッフ' in key:
        return True
    if key in {normalize_vendor_key(k) for k in _K2_VENDOR_KEYS}:
        return True
    disp_key = normalize_vendor_key(K2_VENDOR_DISPLAY)
    if key == disp_key:
        return True
    return False


def is_elms_embroidery_alias(service_name: str) -> bool:
    """刺繍／刺繍入れ／刺繍加工など同一サービス別名か。"""
    sk = normalize_service_key(service_name)
    if not sk:
        return False
    return sk in {normalize_service_key(a) for a in _ELMS_EMBROIDERY_ALIASES}


def is_embroidery_class_name(service_name: str) -> bool:
    """既存の刺繍類か。刺繍なしは対象外。

    LEOC刺繍の LEOC は刺繍する文字であり、顧客名・仕入先名ではない。
    """
    raw = stock.text(service_name)
    if not raw:
        return False
    folded = unicodedata.normalize('NFKC', raw)
    if '刺繍なし' in folded or folded in ('なし', '加工なし'):
        return False
    if paren_classify.is_embroidery_price_condition(raw):
        return False
    if is_elms_embroidery_alias(raw):
        return True
    if '刺繍' in folded or 'ししゅう' in folded.casefold():
        return True
    unified = unify_processing_service_name(raw)
    return bool(unified and '刺繍' in unified and 'なし' not in unified)


def elms_supplier_matches(c):
    """登録済み仕入先からエルムを名前で探す。IDは新しく作らない。"""
    rows = []
    for row in c.execute('SELECT id, name, active FROM suppliers'):
        if row['active'] and is_elms_corporation_vendor(row['name']):
            rows.append({'id': row['id'], 'name': row['name']})
    official = [row for row in rows if stock.text(row['name']) == ELMS_VENDOR_DISPLAY]
    if len(official) == 1:
        # 正式名 (有)エルムスコーポレーション が一つあれば、略称があってもそれを使う。
        # 正式名が二つあるときはここを通さず、下で曖昧にする。
        return {'status': 'unique', 'id': official[0]['id'], 'name': official[0]['name'], 'candidates': rows}
    if len(rows) == 1:
        return {'status': 'unique', 'id': rows[0]['id'], 'name': rows[0]['name'], 'candidates': rows}
    if not rows:
        return {'status': 'missing', 'id': None, 'name': '', 'candidates': []}
    return {'status': 'ambiguous', 'id': None, 'name': '', 'candidates': rows}


def embroidery_vendor_plan(c, service_name: str) -> dict:
    """刺繍類の加工先。決まらなければ確認。別業者の既存サービスは消さない。"""
    name = stock.text(service_name)
    found = elms_supplier_matches(c)
    # 顧客固有名も共通サービス「刺繍」へつなぐ。固有名の加工サービスは新しく作らない。
    service_name_out = ELMS_EMBROIDERY_SERVICE_NAME
    existing = find_existing_processing_service_any_vendor(c, service_name_out)
    if not existing and name != service_name_out:
        existing = find_existing_processing_service_any_vendor(c, name)
    existing_vendor = ''
    existing_is_elms = False
    existing_name = ''
    if existing:
        existing_name = stock.text(existing.get('name') or '')
        sid = existing.get('supplier_id') or existing.get('supplierId')
        if sid:
            sup = c.execute('SELECT name FROM suppliers WHERE id=?', (sid,)).fetchone()
            existing_vendor = stock.text(sup['name'] if sup else '')
            existing_is_elms = is_elms_corporation_vendor(existing_vendor)
    base = {
        'embroidery': True,
        'serviceName': service_name_out,
        'customerProcessingName': name,
        'existingVendor': existing_vendor,
        'existingKept': bool(existing and not existing_is_elms),
    }
    if found['status'] == 'unique':
        note = f'刺繍類の加工先はエルムです。仕入先「{found["name"]}」を使います。他の加工は変えません。'
        if base['existingKept']:
            kept_name = existing_name or name
            note += f'既存の「{kept_name}」（{existing_vendor}）は残し、加工先は書き換えません。'
        return {
            **base,
            'status': 'unique',
            'vendorName': found['name'],
            'supplierId': found['id'],
            'needsConfirm': False,
            'message': note,
        }
    if found['status'] == 'ambiguous':
        labels = '、'.join(item['name'] for item in found['candidates'])
        return {
            **base,
            'status': 'ambiguous',
            'vendorName': '',
            'supplierId': None,
            'needsConfirm': True,
            'message': f'エルムに当たる仕入先が複数あります（{labels}）。どれを使うか確認してください。K2にはしません。',
        }
    return {
        **base,
        'status': 'missing',
        'vendorName': '',
        'supplierId': None,
        'needsConfirm': True,
        'message': 'エルムの仕入先が未登録です。K2にはしません。仕入先を確認してください。',
    }


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
    if is_k2_staff_workshop(raw):
        return K2_VENDOR_DISPLAY
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


def _resolve_service_name_from_row_fields(
    *,
    product_name: str = '',
    paren_processing: str = '',
    code: str = '',
) -> tuple[str, str]:
    """当該行の情報だけから加工名を取る。別行からの借用はしない。

    優先: 元商品名 → 加工欄 → 品番欄。衣服種だけの名称は採用しない。
    戻り値: (serviceName, sourceField) sourceField は name/paren/code/''。
    """
    for field, raw in (
        ('name', product_name),
        ('paren', paren_processing),
        ('code', code),
    ):
        t = stock.text(raw)
        if not t:
            continue
        # 衣服種だけの名称（帽子・ベスト等）を加工名へ流用しない
        if field != 'paren' and is_physical_parts_name(t):
            continue
        service = unify_processing_service_name(t)
        if not service:
            continue
        # 衣服種だけの名称（帽子等）を加工名へ流用しない
        # unify が空を返すのが本筋だが、strip 後が空なら棄却する
        if field in ('name', 'code'):
            stripped = _KIND_STRIP_RE.sub('', canonicalize_attach_verbs(t))
            stripped = re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', stripped or ''))
            if not stripped:
                continue
        return service, field
    return '', ''


def is_physical_parts_name(product_name: str) -> bool:
    """生地・ボタン等の実物部品・資材（加工サービスではない）。

    括弧内の用途（例: 袖／裾加工用）は本体判定から外す。
    『DCP515用生地（袖／裾加工用）』は生地＝部品・資材。
    """
    base, _inners = _strip_parens_keep_inner(product_name)
    core = normalize_service_key(base) or normalize_service_key(product_name)
    if not core:
        return False
    index = _ensure_service_index()
    # 本体側に加工サービス語があるときだけ部品扱いしない（括弧用途の『加工用』は見ない）
    if any(word in core for word in index['service_word_keys']):
        return False
    # 末尾が作業語（裏地補修・ファスナー交換・ボタン付け等）は修理／加工サービス
    if any(core.endswith(suffix) for suffix in index['work_keys']):
        return False
    for marker in index['marker_keys']:
        if core == marker or core.startswith(marker) or core.endswith(marker):
            return True
    return False


def _alias_map():
    return _ensure_service_index()['alias_by_key']


def _canonical_from_term(term: str) -> str:
    index = _ensure_service_index()
    aliases = index['alias_by_key']
    tk = normalize_service_key(term)
    if tk in aliases:
        return aliases[tk]
    for normalized, display in index['value_keys']:
        if normalized == tk:
            return display
    return stock.text(term)


def canonicalize_attach_verbs(value: str) -> str:
    """取り付け／取付け → 取付、縫い付け → 縫付 など表記差を揃える。"""
    _ensure_service_index()
    if isinstance(value, str) and len(value) <= _NORM_KEY_MAX_LEN:
        try:
            return _verb_cache[value]
        except KeyError:
            return _remember(_verb_cache, value, _canonicalize_attach_verbs_body(value))
    return _canonicalize_attach_verbs_body(value)


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
    index = _ensure_service_index()
    aliases = index['alias_by_key']
    cleaned = canonicalize_attach_verbs(core)
    sk = normalize_service_key(cleaned)
    if sk in aliases:
        return aliases[sk]
    for term, tk in index['candidates']:
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


def is_standalone_processing_service_name(product_name: str) -> bool:
    """商品名全体が加工サービス名だけで、衣服種を含まないとき True。

    例: 「インナーネット付け」→ True / 「コックコート（インナーネット付け）」→ False
    （括弧付き本体は unify が空になり、本体行の括弧加工を加工SKUへ誤分離しない）。
    """
    t = stock.text(product_name)
    if not t or is_physical_parts_name(t):
        return False
    unified = unify_processing_service_name(t)
    if not unified:
        return False
    # 衣服種を除いた核が空＝衣服種だけの名。加工サービス単独ではない
    stripped = _KIND_STRIP_RE.sub('', canonicalize_attach_verbs(t))
    stripped = re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', stripped or ''))
    # 括弧を除いた本体側でも判定（括弧仕様付き加工名は可）
    base, _inners = _strip_parens_keep_inner(t)
    base_stripped = _KIND_STRIP_RE.sub('', canonicalize_attach_verbs(base))
    base_stripped = re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', base_stripped or ''))
    # 「ポロシャツ（刺繍）」のように括弧外が衣服種だけなら本体行（括弧は取扱の加工）
    if _inners and stock.text(base) and not base_stripped:
        return False
    core = base_stripped or stripped
    if not core:
        return False
    return _looks_like_service_core(core) or normalize_service_key(core) == normalize_service_key(unified)


def garment_paren_processing_name(product_name: str) -> str:
    """「ポロシャツ（刺繍）」のように括弧外が衣服種だけ・括弧内が加工なら加工名を返す。"""
    t = stock.text(product_name)
    if not t or is_physical_parts_name(t):
        return ''
    base, inners = _strip_parens_keep_inner(t)
    if not inners or not stock.text(base):
        return ''
    base_stripped = _KIND_STRIP_RE.sub('', canonicalize_attach_verbs(base))
    base_stripped = re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', base_stripped or ''))
    if base_stripped:
        return ''
    for inner in inners:
        name = unify_processing_service_name(inner)
        if name:
            return name
    return ''


def _looks_like_garment_name(name):
    from legacy_code_assign import blank_code_kind
    return blank_code_kind(name) == 'garment'


def is_k2_made_uniform_name(name: str, code: str = '') -> bool:
    """品番のないK2製作の実物制服か。正式品番つきの行は対象外。加工名は False。"""
    raw_code = stock.text(code)
    if raw_code and raw_code not in ('要確認', '別注') and not (
        raw_code.startswith('別注(') and raw_code.endswith(')')
    ):
        return False
    raw = stock.text(name)
    if not raw:
        return False
    base = _strip_parens_keep_inner(raw)[0] or raw
    from legacy_code_assign import blank_code_kind
    if blank_code_kind(base) != 'garment':
        return False
    if unify_processing_service_name(base):
        return False
    return True


def is_garment_body_name(name):
    """括弧外が衣服名そのもの（七分袖シャツ・反射ベスト・裾上げパンツ等）なら True。

    括弧内の加工（白衣(内ポケット加工)の「内ポケット加工」）は別に扱うので見ない。
    「コックコート半袖加工」「千鳥パンツ裾上げ」のように末尾が加工語なら False。
    """
    base = _strip_parens_keep_inner(stock.text(name))[0] or stock.text(name)
    compact = re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', base or ''))
    if not compact or '加工' in compact or _SERVICE_SUFFIX_RE.search(compact):
        return False
    if re.search(_KIND_STRIP_RE.pattern + r'$', compact):
        return True
    return _looks_like_garment_name(compact)


def unify_processing_service_name(raw_name: str) -> str:
    """加工サービス表示名へ統一。衣服種・顧客用接頭・括弧仕様は落とす。読めなければ空。

    「ポケット取付」のように『加工』文字が無くても、取付／縫付等の明確な加工名は認識する。
    「袖加工」を「七分袖加工」へ置き換えるなど原文にない具体化はしない。
    """
    raw = strip_service_markers(unicodedata.normalize('NFKC', stock.text(raw_name) or ''))
    if not raw:
        return ''
    # 「インターネット付け」は誤記のまま残す（付け→取付へ直さない）
    if re.sub(r'[\s\u3000]+', '', raw) == 'インターネット付け':
        return 'インターネット付け'
    if paren_classify.is_embroidery_price_condition(raw):
        return ''
    # 「未加工」「加工なし」等は加工が無い意味（加工サービス名にしない）
    import product_ledger_paren as _paren
    if _paren.classify_paren_segment(raw) and (_paren.classify_paren_segment(raw) or {}).get('noProcessing'):
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
            trial_compact = canonicalize_attach_verbs(
                re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', trial or ''))
            )
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
            # 帽子加工 → 衣服種除去で「加工」だけになる場合は原文を採用（bare「加工」へ落とさない）
            resolve_target = trial_compact if stripped == '加工' and trial_compact != '加工' else stripped
            resolved = _resolve_service_core(resolve_target)
            if resolved:
                return resolved
            # 衣服種を残したまま末尾だけ加工語のとき（コックコート七分袖加工）
            sk = normalize_service_key(resolve_target)
            for term, tk in _ensure_service_index()['candidates']:
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
    alias_map = _alias_map()
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
    # 業者が違っても同じ加工は1件（業者はサブ仕入先）
    return f'{normalize_service_key(name)}|*'


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
    paren_t = stock.text(paren_processing)
    vendor = is_processing_vendor(supplier_t)
    physical = is_physical_parts_name(name_t)
    elms = is_elms_corporation_vendor(supplier_t)
    k2 = is_k2_staff_workshop(supplier_t)
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
        'k2StaffWorkshopRule': False,
        'k2AutoExclude': False,
        'autoExcludeReason': '',
        'serviceNameSource': '',
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
    # 品番ありで括弧外が衣服名なら衣服（商品フェーズで登録）。名前の加工語で加工にしない。
    # K2スタッフ工房は修理屋なので対象外（既定で加工）
    if code_t and not k2 and is_garment_body_name(name_t):
        result['garmentBody'] = True
        return result
    if not vendor:
        # whitelist外でも「商品名全体が明確な加工サービス名だけ」の行は加工サービス化する。
        # 本体行の括弧内加工（例: コックコート（裾上げ））は unify が空のため対象外。
        # 加工欄・品番欄からの推定は業者確定後に限る（誤分離防止）。
        service_only, source_only = _resolve_service_name_from_row_fields(
            product_name=name_t, paren_processing='', code='',
        )
        # メーカー品（加工業者でない仕入先）で原文に「加工」が無いのに「○○加工」へ補った名前は
        # 部品の現物（例: YKK「スナッパー(赤)」）。加工サービスへまとめない。
        if (
            service_only
            and '加工' in service_only
            and '加工' not in unicodedata.normalize('NFKC', name_t)
            and code_t
            and _strip_parens_keep_inner(name_t)[1]
            # 括弧外の名前そのもの（例: スナッパー）から加工名を補った場合だけ
            and unify_processing_service_name(_strip_parens_keep_inner(name_t)[0]) == service_only
            # 衣服名（例: コックシャツ七分袖 → 「七分袖加工」）は部品にしない
            and not _looks_like_garment_name(name_t)
        ):
            result['isPhysicalParts'] = True
            result['makerParts'] = True
            return result
        if service_only and source_only == 'name' and is_standalone_processing_service_name(name_t):
            result['isProcessingVendor'] = True
            result['isProcessingService'] = True
            result['serviceName'] = service_only
            result['displayName'] = service_only
            result['vendorName'] = stock.text(supplier_t)
            result['identifiable'] = bool(service_only and supplier_t)
            result['excludeFromGarmentSuppliers'] = True
            result['serviceNameSource'] = source_only
            result['clearServiceNameRow'] = True
            if not supplier_t:
                result['identifiable'] = False
                result['needsConfirm'] = True
                result['confirmReasons'] = [
                    f'加工サービス名「{service_only}」ですが加工業者が空です。'
                    f'業者を入力してください。'
                ]
                result['missingFields'] = ['加工業者']
            return result
        # それ以外の whitelist 外は本体／取扱側（括弧加工など）
        return result

    # 加工業者行: 当該行の商品名・加工欄・品番欄のみから加工名を取る（別行は見ない）
    service, source = _resolve_service_name_from_row_fields(
        product_name=name_t, paren_processing=paren_t, code=code_t,
    )
    if service:
        result['isProcessingService'] = True
        result['serviceName'] = service
        # 表示名の唯一の入力元は加工名（別項目を持たず常に同期）
        result['displayName'] = service
        result['identifiable'] = True
        result['serviceNameSource'] = source
        if k2:
            result['k2StaffWorkshopRule'] = True
            result['vendorName'] = K2_VENDOR_DISPLAY
        # 構成があれば specs に残す
        if result['rowSpecs'].get('composition') and result['rowSpecs']['composition'] != service:
            pass
        elif '＋' in service:
            result['rowSpecs']['composition'] = service
        # 品番欄から加工名を取った場合、元品番は衣服SKUではないので対象コードを空に近づける
        if source == 'code' and not unify_processing_service_name(name_t):
            # 品番欄は加工名。衣服SKUとしては保持しない（別行の本体を巻き込まない）
            result['targetGarmentCode'] = ''
            result['rowSpecs']['originalCode'] = code_t
            result['rowSpecs']['serviceNameFromCode'] = True
        return result

    # 業者は加工だが加工名が読めない → 架空加工を作らず確認
    # 業者名だけを根拠に不明な加工内容を推測しない
    # 表示名（予定名）から無条件に加工名を補完しない
    # K2: 確認待ちに残さず自動で登録対象外（別行の加工名は借用しない）
    result['displayName'] = ''
    missing = []
    if not stock.text(name_t) and not stock.text(code_t) and not paren_t:
        missing.append('加工名（原文名・品番・加工欄が空）')
    else:
        bits = []
        if name_t:
            bits.append(f'原文「{name_t}」')
        if code_t:
            bits.append(f'品番「{code_t}」')
        if paren_t:
            bits.append(f'加工欄「{paren_t}」')
        missing.append(
            f'加工名（{"・".join(bits) or "当該行"}から明確な加工内容を特定できない）'
        )
    if not result['vendorName'] and not supplier_t:
        missing.append('加工業者')
    if k2 and name_t and is_k2_made_uniform_name(name_t, code_t):
        # K2へ製作依頼した実物制服。加工サービスにしない。仕入先はK2のまま。
        result['isProcessingVendor'] = False
        result['isProcessingService'] = False
        result['k2UniformProduct'] = True
        result['excludeFromGarmentSuppliers'] = False
        return result
    if k2 and name_t:
        # K2スタッフ工房は修理屋。加工名が辞書に無くても原文名のまま加工サービスで登録
        # （修理・サイズダウン・アップ・丈直し・丈詰め・反射板調整 等）。
        # エプロン・ベスト・タオルなど実物制服は上で商品にする。
        service = re.sub(r'[\s\u3000]+', ' ', unicodedata.normalize('NFKC', name_t)).strip()
        result['isProcessingVendor'] = True
        result['isPhysicalParts'] = False
        result['isProcessingService'] = True
        result['k2StaffWorkshopRule'] = True
        result['serviceName'] = service
        result['displayName'] = service
        result['vendorName'] = K2_VENDOR_DISPLAY
        result['identifiable'] = True
        result['serviceNameSource'] = 'k2_raw_name'
        result['excludeFromGarmentSuppliers'] = True
        result['rowSpecs']['originalCode'] = code_t
        if is_garment_body_name(name_t) or _KIND_STRIP_RE.search(name_t):
            result['k2GarmentCandidate'] = True
            result['confirmReasons'] = [K2_GARMENT_CHOICE_NOTE]
        return result
    if k2:
        result['isProcessingVendor'] = True
        result['k2StaffWorkshopRule'] = True
        result['k2AutoExclude'] = True
        result['needsConfirm'] = False
        result['vendorName'] = K2_VENDOR_DISPLAY
        result['autoExcludeReason'] = K2_AUTO_EXCLUDE_REASON
        result['confirmReasons'] = [K2_AUTO_EXCLUDE_REASON]
        result['missingFields'] = missing
        result['excludeFromGarmentSuppliers'] = True
        return result

    result['needsConfirm'] = True
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


def _processing_service_category_rank(category: str) -> int:
    """再利用優先度。加工サービス > 旧・部品・資材 > その他。"""
    cat = stock.text(category)
    if cat == stock.CATEGORY_PROCESS_SERVICE:
        return 0
    if cat == stock.CATEGORY_PARTS:
        return 1
    return 2


def find_existing_processing_services(c, service_name: str, vendor_name: str):
    """同業者の加工サービス（＋移行前の部品・資材）を別名込みで列挙。複数件あり得る。"""
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
    preferred = [
        m for m in matches
        if stock.text(_product_field(m, 'category')) in (
            stock.CATEGORY_PROCESS_SERVICE, stock.CATEGORY_PARTS,
        )
    ]
    pool = preferred or matches
    pool.sort(
        key=lambda m: (
            _processing_service_category_rank(_product_field(m, 'category')),
            int(_product_field(m, 'id') or 0),
        )
    )
    return pool


def find_existing_processing_service(c, service_name: str, vendor_name: str):
    """同名＋同業者の加工サービスを探す（旧・部品・資材登録も含む）。

    刺繍系別名（刺繍／刺繍入れ／刺繍加工）は同一サービスとして再利用する。
    同一業者で別名の加工サービスが複数ある場合は、正規名「刺繍」を優先し、
    なければ最初の1件を返す（実データの削除・統合はしない。呼び出し側で重複報告可）。
    カテゴリは加工サービスを部品・資材より優先（移行途中の重複回避）。
    """
    name = stock.text(service_name)
    vendor = stock.text(vendor_name)
    if not name or not vendor:
        return None
    pool = find_existing_processing_services(c, name, vendor)
    if not pool and not is_elms_corporation_vendor(vendor):
        # 別業者で登録済みの同じ加工を再利用（業者はサブ仕入先として足す）
        return find_existing_processing_service_any_vendor(c, name)
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]

    # 複数: カテゴリ優先のあと、正規名一致 → 刺繍加工 → id 昇順
    preferred_order = [ELMS_EMBROIDERY_SERVICE_NAME, '刺繍加工', '刺繍入れ']
    for cat_rank in (0, 1, 2):
        ranked = [
            m for m in pool
            if _processing_service_category_rank(_product_field(m, 'category')) == cat_rank
        ]
        if not ranked:
            continue
        for pref in preferred_order:
            pref_key = normalize_service_key(pref)
            hits = [
                m for m in ranked
                if normalize_service_key(_product_field(m, 'name')) == pref_key
            ]
            if len(hits) == 1:
                return hits[0]
            if hits:
                hits.sort(key=lambda m: int(_product_field(m, 'id') or 0))
                return hits[0]
        ranked.sort(key=lambda m: int(_product_field(m, 'id') or 0))
        return ranked[0]
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


def _product_lot_qty(c, product_id: int) -> int:
    """衣服在庫の残数量合計。加工サービス移行の曖昧判定用（書込なし）。"""
    row = c.execute(
        '''SELECT COALESCE(SUM(l.qty_remaining), 0) AS n
           FROM lots l
           JOIN customer_products cp ON cp.id=l.customer_product_id
           WHERE cp.product_id=?''',
        (int(product_id),),
    ).fetchone()
    return int(row['n'] if row else 0)


def classify_parts_product_for_process_migration(c, product_row) -> dict:
    """部品・資材1件を加工サービス移行の観点で分類する（書込なし）。

    bucket:
      - candidate: 明確に加工サービス（業者 or 明確な加工名）
      - ambiguous: 判断が分かれうる（人手確認）
      - leave: 実物部品・資材のまま残す
    """
    pid = int(_product_field(product_row, 'id') or 0)
    name = stock.text(_product_field(product_row, 'name') or '')
    code = stock.text(_product_field(product_row, 'code') or '')
    category = stock.text(_product_field(product_row, 'category') or '')
    supplier_id = _product_field(product_row, 'supplier_id')
    supplier_name = ''
    if supplier_id not in (None, ''):
        srow = c.execute(
            'SELECT name FROM suppliers WHERE id=?', (int(supplier_id),)
        ).fetchone()
        supplier_name = stock.text(srow['name'] if srow else '')

    base = {
        'id': pid,
        'name': name,
        'code': code,
        'category': category,
        'supplierId': supplier_id,
        'supplierName': supplier_name,
        'lotQty': _product_lot_qty(c, pid) if pid else 0,
        'active': bool(_product_field(product_row, 'active')),
    }

    if category != stock.CATEGORY_PARTS:
        return {
            **base,
            'bucket': 'leave',
            'reason': '部品・資材以外のため対象外',
            'suggestedServiceName': '',
        }

    if is_physical_parts_name(name):
        return {
            **base,
            'bucket': 'leave',
            'reason': '実物の部品・資材名（生地・ボタン等）',
            'suggestedServiceName': '',
        }

    vendor = is_processing_vendor(supplier_name)
    unified = ''
    if vendor and is_elms_corporation_vendor(supplier_name):
        unified = ELMS_EMBROIDERY_SERVICE_NAME
    else:
        unified = unify_processing_service_name(name) or ''

    identifiable = bool(unified) and (
        vendor or _looks_like_service_core(canonicalize_attach_verbs(name))
    )

    if identifiable and vendor and not code:
        reason = (
            f'加工業者「{supplier_name}」＋明確な加工名「{unified}」（品番なし）'
        )
        if base['lotQty'] > 0:
            return {
                **base,
                'bucket': 'ambiguous',
                'reason': reason + '／衣服在庫数量あり（数量扱いの確認が必要）',
                'suggestedServiceName': unified,
            }
        return {
            **base,
            'bucket': 'candidate',
            'reason': reason,
            'suggestedServiceName': unified,
        }

    if identifiable and vendor and code:
        return {
            **base,
            'bucket': 'ambiguous',
            'reason': (
                f'加工業者＋加工名「{unified}」だが品番「{code}」あり'
                '（本体SKU混在の可能性）'
            ),
            'suggestedServiceName': unified,
        }

    if identifiable and not vendor and not code:
        return {
            **base,
            'bucket': 'ambiguous',
            'reason': (
                f'加工名「{unified}」に見えるが仕入先が加工業者ではない'
                f'（{supplier_name or "未設定"}）'
            ),
            'suggestedServiceName': unified,
        }

    if vendor and not identifiable and not code:
        return {
            **base,
            'bucket': 'ambiguous',
            'reason': (
                f'加工業者「{supplier_name}」だが加工内容を特定できない'
                f'（名称「{name or "（空）"}」）'
            ),
            'suggestedServiceName': '',
        }

    return {
        **base,
        'bucket': 'leave',
        'reason': '加工サービスと特定できない部品・資材',
        'suggestedServiceName': '',
    }


def inventory_process_service_migration(c, *, include_leave: bool = False) -> dict:
    """既存の部品・資材から加工サービス移行候補を列挙する（DB書込なし）。

    - candidate: 移行してよい明確な加工登録（ID維持・カテゴリ更新のみ想定）
    - ambiguous: 人手レビュー
    - leave: そのまま部品・資材（既定では件数のみ）
    本番適用は別途レビュー後。この関数は一覧のみ。
    """
    candidates = []
    ambiguous = []
    leave = []
    leave_count = 0
    for row in c.execute(
        '''SELECT id, code, name, category, supplier_id, active
           FROM products
           WHERE category=? AND COALESCE(merged_into_id, 0)=0
           ORDER BY id''',
        (stock.CATEGORY_PARTS,),
    ):
        item = classify_parts_product_for_process_migration(c, row)
        bucket = item['bucket']
        if bucket == 'candidate':
            candidates.append(item)
        elif bucket == 'ambiguous':
            ambiguous.append(item)
        else:
            leave_count += 1
            if include_leave:
                leave.append(item)

    already_row = c.execute(
        '''SELECT COUNT(*) AS n FROM products
           WHERE category=? AND COALESCE(merged_into_id, 0)=0''',
        (stock.CATEGORY_PROCESS_SERVICE,),
    ).fetchone()
    already_n = int(already_row['n'] if already_row else 0)

    return {
        'method': (
            '部品・資材のうち、(1) 仕入先が加工業者かつ品番なしかつ明確な加工名'
            ' → candidate。(2) 加工名のみ／品番あり／在庫数量あり／業者のみ'
            ' → ambiguous。(3) 実物部品マーカーや特定不能 → leave。'
            ' 適用時は products.category を加工サービスへ UPDATE し、'
            ' id・仕入先・価格・履歴・参照は維持（削除再作成しない）。'
            ' この inventory は書込しない。'
        ),
        'alreadyProcessServiceCount': already_n,
        'partsScanned': len(candidates) + len(ambiguous) + leave_count,
        'candidateCount': len(candidates),
        'ambiguousCount': len(ambiguous),
        'leaveCount': leave_count,
        'candidates': candidates,
        'ambiguous': ambiguous,
        'leave': leave if include_leave else [],
        'applySqlPreview': (
            "UPDATE products SET category='加工サービス', updated_at=? "
            "WHERE id=? AND category='部品・資材'"
        ),
        'writes': False,
    }


def apply_process_service_migration(c, product_ids, *, dry_run: bool = True) -> dict:
    """候補 ID のカテゴリを加工サービスへ更新する。既定は dry_run（書込なし）。

    本番適用は dry_run=False かつ明示呼び出しのみ。自動実行しない。
    """
    ids = []
    for raw in product_ids or []:
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in ids:
            ids.append(pid)
    planned = []
    skipped = []
    for pid in ids:
        row = c.execute(
            '''SELECT id, code, name, category, supplier_id, active
               FROM products WHERE id=?''',
            (pid,),
        ).fetchone()
        if not row:
            skipped.append({'id': pid, 'reason': '商品がありません'})
            continue
        item = classify_parts_product_for_process_migration(c, row)
        if item['bucket'] != 'candidate':
            skipped.append({
                'id': pid,
                'reason': f"bucket={item['bucket']}: {item.get('reason') or ''}",
                'bucket': item['bucket'],
            })
            continue
        planned.append(item)
    applied = []
    if not dry_run:
        stamp = stock.stamp()
        for item in planned:
            c.execute(
                '''UPDATE products SET category=?, updated_at=?
                   WHERE id=? AND category=?''',
                (
                    stock.CATEGORY_PROCESS_SERVICE, stamp, int(item['id']),
                    stock.CATEGORY_PARTS,
                ),
            )
            if c.total_changes:
                applied.append({**item, 'category': stock.CATEGORY_PROCESS_SERVICE})
            else:
                skipped.append({
                    'id': item['id'],
                    'reason': '更新対象外（カテゴリが変わった可能性）',
                })
    return {
        'dryRun': bool(dry_run),
        'requested': ids,
        'planned': planned,
        'applied': applied,
        'skipped': skipped,
        'writes': (not dry_run) and bool(applied),
    }


def inventory_coded_process_services(c) -> dict:
    """品番付きで登録された加工サービスの棚卸し（読取のみ）。

    action:
      clear_code … 名前が加工サービス名だけ → 品番を外す
      to_product … 名前が衣服など本体（例: ポロシャツ(刺繍)）→ 区分を外して商品に戻す
      confirm    … 判断できない（名前が空など）→ 画面で確認
    """
    items = []
    for row in c.execute(
        '''SELECT id, code, name, category FROM products
           WHERE TRIM(COALESCE(code, '')) <> '' ORDER BY id'''
    ):
        if not stock.is_process_service_category(row['category']):
            continue
        name = stock.text(row['name'])
        if not name:
            action, why = 'confirm', '商品名が空'
        elif is_standalone_processing_service_name(name):
            action, why = 'clear_code', '加工サービス名だけ'
        else:
            action, why = 'to_product', '本体の商品名'
        items.append({'id': row['id'], 'code': row['code'], 'name': name, 'action': action, 'reason': why})
    counts = {}
    for it in items:
        counts[it['action']] = counts.get(it['action'], 0) + 1
    return {'items': items, 'counts': counts}


def apply_coded_process_service_repair(c, product_ids, *, dry_run: bool = True) -> dict:
    """棚卸し結果のうち指定 ID だけ直す。既定は dry_run（書込なし）。confirm は直さない。"""
    want = {int(x) for x in product_ids or [] if str(x).isdigit()}
    inv = inventory_coded_process_services(c)
    planned, skipped = [], []
    for it in inv['items']:
        if it['id'] not in want:
            continue
        (planned if it['action'] in ('clear_code', 'to_product') else skipped).append(it)
    found = {it['id'] for it in inv['items']}
    skipped += [{'id': i, 'reason': '対象外（品番付きの加工サービスではない）'} for i in sorted(want - found)]
    if not dry_run and planned:
        with c:
            for it in planned:
                if it['action'] == 'clear_code':
                    c.execute("UPDATE products SET code='' WHERE id=?", (it['id'],))
                else:
                    c.execute("UPDATE products SET category='' WHERE id=?", (it['id'],))
    return {'dryRun': dry_run, 'planned': planned, 'skipped': skipped}


def find_existing_processing_service_any_vendor(c, service_name: str):
    """業者を問わず、同じ加工名（正規化キー一致）の品番なし加工サービスを1件（ID最小）。"""
    key = normalize_service_key(service_name)
    if not key:
        return None
    for row in c.execute(
        """SELECT * FROM products WHERE category=? AND TRIM(COALESCE(code,''))=''
           ORDER BY active DESC, id""",
        (stock.CATEGORY_PROCESS_SERVICE,),
    ):
        if normalize_service_key(row['name']) == key:
            return dict(row)
    # 利用者が登録した照合用別名（商品の別名と同じ表）
    try:
        rows = list(c.execute(
            """SELECT p.*, a.value AS alias_value FROM product_match_aliases a
               JOIN products p ON p.id=a.product_id
               WHERE a.kind='name' AND p.category=? AND TRIM(COALESCE(p.code,''))=''
                 AND p.merged_into_id IS NULL
               ORDER BY p.active DESC, p.id""",
            (stock.CATEGORY_PROCESS_SERVICE,),
        ))
    except Exception:  # noqa: BLE001  別名表が無い古いDB
        rows = []
    for row in rows:
        if normalize_service_key(row['alias_value']) == key:
            d = dict(row)
            d.pop('alias_value', None)
            return d
    return None


def inventory_duplicate_process_services(c) -> dict:
    """同じ加工名（正規化キー一致）の加工サービスが複数ある組の一覧（読取のみ）。ID最小を残す候補。"""
    cols = {r['name'] for r in c.execute('PRAGMA table_info(products)')}
    merged_filter = " AND merged_into_id IS NULL" if 'merged_into_id' in cols else ''
    groups = {}
    for row in c.execute(
        f"""SELECT id, code, name, supplier_id, active FROM products
            WHERE category=?{merged_filter} ORDER BY id""",
        (stock.CATEGORY_PROCESS_SERVICE,),
    ):
        if stock.text(row['code']):
            continue  # 品番付きは inventory_coded_process_services で先に直す
        key = normalize_service_key(row['name'])
        if key:
            groups.setdefault(key, []).append({'id': row['id'], 'name': row['name'], 'supplierId': row['supplier_id']})
    out = []
    for k, items in groups.items():
        if len(items) < 2:
            continue
        best = ''
        for it in items:
            best = prefer_service_display_name(best, it['name'])
        out.append({'key': k, 'keepId': items[0]['id'], 'items': items, 'suggestedName': best})
    return {'groups': out, 'count': len(out)}


def apply_duplicate_process_service_merge(c, keys=None, *, dry_run: bool = True, db_path=None) -> dict:
    """重複組を既存の「商品統合」で1件にまとめる（在庫・取扱・参照は統合処理が付け替え）。

    既定は dry_run（書込なし）。keys 未指定なら全組。取扱に差異がある組は統合しない。
    """
    import uuid

    import stock_product_merge as merge

    want = set(keys or [])
    done, skipped = [], []
    for g in inventory_duplicate_process_services(c)['groups']:
        if want and g['key'] not in want:
            continue
        body = {'productIds': [i['id'] for i in g['items']], 'keepProductId': g['keepId']}
        try:
            pv = merge.preview_merge(c, body)
        except Exception as e:  # noqa: BLE001
            skipped.append({**g, 'reason': str(e)})
            continue
        if pv.get('unresolvedConflicts') or pv.get('handleConflicts'):
            skipped.append({**g, 'reason': '取扱に差異あり（画面の商品統合で確認してください）'})
            continue
        if not dry_run:
            merge.execute_merge(c, {
                **body, 'fingerprint': pv['fingerprint'],
                'token': f'dup-proc-{uuid.uuid4().hex}', '_actor': {'name': '重複加工サービス修復'},
            }, db_path=db_path)
            # 正の表記（語末「ー」あり等）へ代表名を寄せる。旧名は別名に残る
            keep = c.execute('SELECT * FROM products WHERE id=?', (g['keepId'],)).fetchone()
            if keep and g.get('suggestedName') and stock.text(keep['name']) != g['suggestedName']:
                merge.ensure_name_alias(c, g['keepId'], keep['name'])
                c.execute('UPDATE products SET name=?, updated_at=? WHERE id=?',
                          (g['suggestedName'], stock.stamp(), g['keepId']))
        done.append(g)
    return {'dryRun': dry_run, 'merged' if not dry_run else 'planned': done, 'skipped': skipped}


def create_companion_processing_service(c, name):
    """品番なし・メイン仕入先なしの加工サービスを作る（同名があれば作らずその ID）。"""
    existing = find_existing_processing_service_any_vendor(c, name)
    if existing:
        return int(existing['id']), False
    saved = stock.save_product(c, {
        'code': '', 'name': name, 'color': '', 'material': '',
        'category': stock.CATEGORY_PROCESS_SERVICE, 'supplierId': None,
        'sizes': ['—'], 'links': [], 'active': True,
    })
    return (saved['id'] if isinstance(saved, dict) else int(saved)), True


def inventory_missing_companion_services(c):
    """品番付き商品の取扱 加工にあるのに、加工サービスが無い加工名を一覧。

    品番なしで「ポロシャツ（刺繍）」形のまま残る商品は自動では直さず reviewOnly に出す。
    """
    missing = {}
    for r in c.execute(
        '''SELECT p.id, p.code, p.name, cp.embroidery FROM customer_products cp
           JOIN products p ON p.id=cp.product_id
           WHERE p.code<>'' AND p.merged_into_id IS NULL
             AND COALESCE(p.category,'') NOT IN (?, ?)''',
        (stock.CATEGORY_PROCESS_SERVICE, stock.CATEGORY_PARTS),
    ):
        name = unify_processing_service_name(r['embroidery'] or '')
        if not name or find_existing_processing_service_any_vendor(c, name):
            continue
        key = normalize_service_key(name)
        m = missing.setdefault(key, {'serviceName': name, 'productIds': []})
        if r['id'] not in m['productIds']:
            m['productIds'].append(r['id'])
    review = []
    for r in c.execute(
        "SELECT id, name, category FROM products WHERE code='' AND merged_into_id IS NULL AND COALESCE(category,'')<>?",
        (stock.CATEGORY_PROCESS_SERVICE,),
    ):
        svc = garment_paren_processing_name(r['name'])
        if svc:
            review.append({'id': r['id'], 'name': r['name'], 'category': r['category'] or '', 'serviceName': svc})
    return {'missing': list(missing.values()), 'reviewOnly': review}


def apply_missing_companion_services(c, dry_run=True):
    """足りない加工サービスを作る。dry_run=True（既定）は計画だけ返し書き込まない。"""
    inv = inventory_missing_companion_services(c)
    created = []
    if not dry_run:
        for m in inv['missing']:
            pid, made = create_companion_processing_service(c, m['serviceName'])
            if made:
                created.append({'id': pid, 'serviceName': m['serviceName']})
    return {'dryRun': dry_run, 'plan': inv['missing'], 'created': created, 'reviewOnly': inv['reviewOnly']}


def inventory_category_fixes(c):
    """品番があるのにカテゴリ空欄の商品を一覧（→ 制服）。部品らしい名前は reviewOnly。"""
    fix, review = [], []
    for r in c.execute(
        "SELECT id, code, name FROM products WHERE code<>'' AND COALESCE(category,'')='' "
        'AND merged_into_id IS NULL ORDER BY id'
    ):
        item = {'id': r['id'], 'code': r['code'], 'name': r['name'], 'to': stock.CATEGORY_UNIFORM}
        (review if is_physical_parts_name(r['name'] or '') else fix).append(item)
    return {'fix': fix, 'reviewOnly': review}


def apply_category_fixes(c, dry_run=True):
    """カテゴリ空欄の品番付き商品を制服にする。dry_run=True（既定）は計画だけ。"""
    inv = inventory_category_fixes(c)
    if not dry_run:
        now = stock.stamp()
        for it in inv['fix']:
            c.execute(
                "UPDATE products SET category=?, updated_at=? WHERE id=? AND COALESCE(category,'')=''",
                (stock.CATEGORY_UNIFORM, now, it['id']),
            )
    return {'dryRun': dry_run, **inv}


def _size_in_use(c, pid, label):
    if c.execute('''SELECT 1 FROM lots l JOIN customer_products cp ON cp.id=l.customer_product_id
                    WHERE cp.product_id=? AND l.size_label=? LIMIT 1''', (pid, label)).fetchone():
        return True
    has_po = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='purchase_order_lines'").fetchone()
    return bool(has_po and c.execute(
        'SELECT 1 FROM purchase_order_lines WHERE product_id=? AND size_label=? LIMIT 1', (pid, label)).fetchone())


def apply_service_size_color_cleanup(c, dry_run=True):
    """加工サービスのサイズ・色を外す計画（既定: 書込なし）。

    在庫・発注で使っているサイズは残す。サイズは最低1つ必要なので、何も残らなければ「—」1つにする。
    """
    plans = []
    for p in c.execute(
        '''SELECT id, name, color FROM products WHERE category=? AND merged_into_id IS NULL ORDER BY id''',
        (stock.CATEGORY_PROCESS_SERVICE,),
    ).fetchall():
        pid = int(p['id'])
        sizes = [r['label'] for r in c.execute(
            'SELECT label FROM product_sizes WHERE product_id=? ORDER BY sort_order,id', (pid,))]
        used = [s for s in sizes if _size_in_use(c, pid, s)]
        keep = used or ['—']
        remove = [s for s in sizes if s not in keep]
        add = [s for s in keep if s not in sizes]
        clear_color = bool(stock.text(p['color'] or ''))
        if not remove and not add and not clear_color:
            continue
        plans.append({'id': pid, 'name': p['name'], 'color': p['color'] or '',
                      'clearColor': clear_color, 'removeSizes': remove, 'addSizes': add,
                      'keptInUse': used})
    if not dry_run:
        t = stock.stamp()
        for pl in plans:
            for s in pl['removeSizes']:
                c.execute('DELETE FROM product_sizes WHERE product_id=? AND label=?', (pl['id'], s))
            for s in pl['addSizes']:
                c.execute('INSERT INTO product_sizes(product_id,label,sort_order) VALUES(?,?,0)', (pl['id'], s))
            if pl['clearColor']:
                c.execute("UPDATE products SET color='', updated_at=? WHERE id=?", (t, pl['id']))
    return {'dryRun': dry_run, 'planned' if dry_run else 'applied': plans}
