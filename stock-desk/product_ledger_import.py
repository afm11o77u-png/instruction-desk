"""旧システム「商品台帳.csv」からの商品・顧客取扱一括登録。

初期在庫用の旧CSV取込（legacy_csv_import）とは別入口・別処理。
在庫数量・仕入単価・初期在庫・入出庫履歴は登録・変更しない。
プレビュー段階では DB を変更しない。確定は一括トランザクション。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
from collections import defaultdict

import legacy_csv_import as legacy
import product_ledger_paren as paren_classify
import stock
import stock_product_merge

REQUIRED_COLUMNS = ('顧客', '仕入先', '商品コード', '商品名', 'サイズ')
IMPORT_NOTE = '旧商品台帳CSV取込'
PENDING_EMBROIDERY = legacy.PENDING_EMBROIDERY

STATUS_CREATE = 'create'
STATUS_REUSE = 'reuse'
STATUS_SIZE_ADD = 'size_add'
STATUS_UNCHANGED = 'unchanged'
STATUS_REVIEW = 'needs_review'
STATUS_HOLD = 'hold'
STATUS_EXCLUDED = 'excluded'
STATUS_MERGE = 'merge'  # 既存重複を統合してから登録

STATUS_LABELS = {
    STATUS_CREATE: '新規',
    STATUS_REUSE: '既存使用',
    STATUS_SIZE_ADD: 'サイズ追加',
    STATUS_UNCHANGED: '変更なし',
    STATUS_REVIEW: '要確認',
    STATUS_HOLD: '保留',
    STATUS_EXCLUDED: '対象外',
    STATUS_MERGE: '統合して登録',
}

SKU_DECISION_MERGE = 'merge'
SKU_DECISION_SEPARATE = 'separate'
SKU_DECISION_HOLD = 'hold'

# 品番比較の判断ティア（自動まとめは needsDecision=False）
REVIEW_TIER_AUTO = 'auto'
REVIEW_TIER_BULK = 'bulk'  # 名差・仕入先差のみ → 一括承認候補
REVIEW_TIER_INDIVIDUAL = 'individual'  # 色・加工・商品種の疑い → 個別確認

REVIEW_TIER_LABELS = {
    REVIEW_TIER_AUTO: '自動まとめ',
    REVIEW_TIER_BULK: '一括承認候補',
    REVIEW_TIER_INDIVIDUAL: '個別確認',
}

PROVISIONAL_NAME_TAG = '名称未整理'

# 色・加工・商品種の「疑い」検出（自動で同一商品と断定しない。個別確認へ振り分けるだけ）
_COLOR_HINT_RE = re.compile(
    r'(蛍光|ネイビー|ブラック|ホワイト|レッド|ブルー|グリーン|イエロー|オレンジ|ピンク|'
    r'グレー|グレイ|ブラウン|ベージュ|シルバー|ゴールド|'
    r'紺|白|黒|赤|青|緑|黄|茶|灰|銀|金|紫|橙|桃|水色|カーキ|キャメル|ワイン|エンジ|アイボリー|'
    r'カラー|色|'
    r'\d{2,3}\.\s*[\u3040-\u30ff\u4e00-\u9fff]+)'
)
_PROCESSING_HINT_RE = re.compile(
    r'(加工|刺繍|プリント|ネーム|スナッパー|マジックテープ|反射|コーティング|'
    r'アイロン|ワッペン|カット|縫製)'
)
_KIND_HINT_RE = re.compile(
    r'(帽子|キャップ|ヘルメット|ベスト|パンツ|ズボン|スラックス|シャツ|ブラウス|'
    r'ブルゾン|ジャケット|コート|スカート|エプロン|手袋|グローブ|靴|シューズ|安全靴|'
    r'つなぎ|カバーオール|白衣|スクラブ|レインウェア|ポロシャツ)'
)
_GENDER_HINT_RE = re.compile(
    r'(男性用|女性用|メンズ|レディース|紳士用|婦人用|男子|女子|男児|女児)'
)
_FABRIC_HINT_RE = re.compile(
    r'(綿100|ポリエステル|ナイロン|ウール|麻混|デニム|ツイル|オックス|'
    r'クールマックス|吸汗|速乾|防炎|難燃|制電|透湿)'
)


def _collapse_space(value):
    return re.sub(r'[\s\u3000]+', '', stock.text(value))


def _hint_tokens(text, pattern):
    found = pattern.findall(stock.text(text) or '')
    return {stock.text(t) for t in found if stock.text(t)}


def _kind_tokens(text):
    """商品種ヒント。明示シノニム（パンツ／スラックス等）は代表トークンへそろえる。"""
    return {legacy.synonym_canonical_token(t) for t in _hint_tokens(text, _KIND_HINT_RE)}


def format_provisional_official_name(base_name):
    """代表名文字列を返す。「名称未整理」は名前へ埋め込まず status フラグ側で扱う。"""
    base = stock.text(base_name)
    if not base:
        return ''
    # 旧データ互換: 以前埋め込んでいたタグが残っていれば除去
    cleaned = base.replace(f'［{PROVISIONAL_NAME_TAG}］', '').replace(PROVISIONAL_NAME_TAG, '')
    return stock.text(cleaned) or base


def analyze_sku_review_diffs(variants, *, existing_count=0, name_mismatch=False):
    """品番グループの差分を具体値つきで整理し、判断ティアを返す。

    - auto: 呼び出し側で needsDecision=False のときのみ
    - bulk: 商品名の言い回し差・仕入先差のみ（色・加工・商品種の疑いなし）
    - individual: 色／加工／商品種の疑い、または既存重複など慎重確認が必要

    キーワード一致だけで「同じ商品」とは断定しない。
    """
    names = []
    suppliers = []
    for v in variants or []:
        for n in v.get('csvNames') or ([v.get('productName')] if v.get('productName') else []):
            n = stock.text(n)
            if n and n not in names:
                names.append(n)
        s = stock.text(v.get('supplier') or '')
        if s and s not in suppliers:
            suppliers.append(s)

    cores = {product_name_core_key(n) for n in names if n}
    name_wording_diff = len(cores) > 1
    supplier_diff = len(suppliers) > 1

    color_sets = [_hint_tokens(n, _COLOR_HINT_RE) for n in names]
    proc_sets = [_hint_tokens(n, _PROCESSING_HINT_RE) for n in names]
    kind_sets = [_kind_tokens(n) for n in names]
    gender_sets = [_hint_tokens(n, _GENDER_HINT_RE) for n in names]
    fabric_sets = [_hint_tokens(n, _FABRIC_HINT_RE) for n in names]

    def _set_diff(sets):
        if len(names) < 2:
            return False
        nonempty = [s for s in sets if s]
        if len(nonempty) >= 2 and any(s != nonempty[0] for s in nonempty[1:]):
            return True
        if nonempty and any(not s for s in sets):
            return True
        return False

    color_diff = _set_diff(color_sets)
    processing_diff = _set_diff(proc_sets)
    # 商品種はシノニム正規化済み。差があれば個別確認。
    kind_diff = False
    if len(names) >= 2:
        nonempty_kinds = [s for s in kind_sets if s]
        if len(nonempty_kinds) >= 2 and any(s != nonempty_kinds[0] for s in nonempty_kinds[1:]):
            kind_diff = True
    gender_diff = _set_diff(gender_sets)
    fabric_diff = _set_diff(fabric_sets)

    reasons = []
    other_diffs = []
    if name_wording_diff or name_mismatch:
        reasons.append({
            'code': 'name_wording',
            'label': '商品名の言い回しが異なる',
            'values': list(names),
        })
    if supplier_diff:
        reasons.append({
            'code': 'supplier',
            'label': '仕入先が異なる',
            'values': list(suppliers),
        })
    if color_diff:
        other_diffs.append({
            'code': 'color',
            'label': '色の違いの可能性',
            'values': [n for n, s in zip(names, color_sets) if s],
        })
        reasons.append({
            'code': 'color',
            'label': '色の違いの可能性（個別確認）',
            'values': [n for n, s in zip(names, color_sets) if s],
        })
    if processing_diff:
        other_diffs.append({
            'code': 'processing',
            'label': '加工の違いの可能性',
            'values': [n for n, s in zip(names, proc_sets) if s],
        })
        reasons.append({
            'code': 'processing',
            'label': '加工の違いの可能性（個別確認）',
            'values': [n for n, s in zip(names, proc_sets) if s],
        })
    if kind_diff:
        other_diffs.append({
            'code': 'kind',
            'label': '商品種の違いの可能性',
            'values': [n for n, s in zip(names, kind_sets) if s],
        })
        reasons.append({
            'code': 'kind',
            'label': '商品種の違いの可能性（個別確認）',
            'values': [n for n, s in zip(names, kind_sets) if s],
        })
    if gender_diff:
        other_diffs.append({
            'code': 'gender',
            'label': '男女・対象の違いの可能性',
            'values': [n for n, s in zip(names, gender_sets) if s],
        })
        reasons.append({
            'code': 'gender',
            'label': '男女・対象の違いの可能性（個別確認）',
            'values': [n for n, s in zip(names, gender_sets) if s],
        })
    if fabric_diff:
        other_diffs.append({
            'code': 'fabric',
            'label': '生地・素材の違いの可能性',
            'values': [n for n, s in zip(names, fabric_sets) if s],
        })
        reasons.append({
            'code': 'fabric',
            'label': '生地・素材の違いの可能性（個別確認）',
            'values': [n for n, s in zip(names, fabric_sets) if s],
        })
    if existing_count > 1:
        reasons.append({
            'code': 'existing_dup',
            'label': f'登録済が {existing_count} 件（残す商品の選択が必要）',
            'values': [],
        })
    if not reasons and existing_count <= 1:
        reasons.append({
            'code': 'compare',
            'label': '内容の確認が必要',
            'values': list(names) or list(suppliers),
        })

    if (
        existing_count > 1
        or color_diff
        or processing_diff
        or kind_diff
        or gender_diff
        or fabric_diff
    ):
        tier = REVIEW_TIER_INDIVIDUAL
    elif name_wording_diff or supplier_diff or name_mismatch:
        tier = REVIEW_TIER_BULK
    else:
        # 既存1件との名差など、上記に入らない要確認
        tier = REVIEW_TIER_INDIVIDUAL if existing_count else REVIEW_TIER_BULK

    return {
        'reviewTier': tier,
        'reviewTierLabel': REVIEW_TIER_LABELS.get(tier, tier),
        'reviewReasons': reasons,
        'nameCandidates': names,
        'supplierCandidates': suppliers,
        'otherDiffs': other_diffs,
        'nameWordingDiff': name_wording_diff or bool(name_mismatch),
        'supplierDiff': supplier_diff,
        'colorDiffPossible': color_diff,
        'processingDiffPossible': processing_diff,
        'kindDiffPossible': kind_diff,
        'genderDiffPossible': gender_diff,
        'fabricDiffPossible': fabric_diff,
        'bulkApprovable': tier == REVIEW_TIER_BULK,
    }


def plan_provisional_official_name(group, raw_decision=None):
    """代表名を決める。「名称未整理」は名前へ埋めず provisional ステータスで示す。

    優先順:
    1. ユーザー指定（手動が最優先。再照合でも上書きしない）
    2. 既存商品の公式名（黙って上書きしない）
    3. 初期在庫CSV補助の非ゼロ在庫ペア提案（商品名+仕入先をセット）
    4. 台帳CSVの商品名コアが一意
    5. 曖昧なときは仮代表名（名称未整理ステータス）＋候補表示

    ※「在庫で決まらなければ補助CSVに出た名を採用」する旧規則（aux_any_unique）は使わない。
    """
    raw = raw_decision if isinstance(raw_decision, dict) else {}
    user = stock.text(raw.get('officialName') or raw.get('name') or '')
    variants = group.get('variants') or []
    all_csv_names = []
    earliest_name = ''
    earliest_row = None
    name_row_map = []
    for v in variants:
        for n in v.get('csvNames') or ([v.get('productName')] if v.get('productName') else []):
            n = stock.text(n)
            if not n:
                continue
            if n not in all_csv_names:
                all_csv_names.append(n)
            for rn in v.get('rowNos') or []:
                name_row_map.append({'originalName': n, 'sourceRowNo': rn})
                if earliest_row is None or int(rn) < int(earliest_row):
                    earliest_row = rn
                    earliest_name = n

    existing = group.get('existingProducts') or []
    existing_name = ''
    if len(existing) == 1:
        existing_name = stock.text(existing[0].get('name') or '')
    elif raw.get('keepProductId'):
        for p in existing:
            if int(p.get('id') or 0) == int(raw.get('keepProductId') or 0):
                existing_name = stock.text(p.get('name') or '')
                break

    aux = group.get('auxPrimary') or {}
    aux_name = ''
    aux_rule = ''
    aux_rule_code = ''
    if aux.get('suggestedOfficialName') and not aux.get('officialNameNeedsChoice'):
        aux_name = stock.text(aux.get('suggestedOfficialName') or '')
        aux_rule = stock.text(aux.get('nameAdoptionRule') or '') or (
            '初期在庫CSVで一意だった商品名を代表名に採用'
        )
        aux_rule_code = stock.text(aux.get('nameAdoptionRuleCode') or '') or 'aux_unique'

    unmarked = prefer_unmarked_product_name(all_csv_names) or ''
    name_candidates = list(all_csv_names)
    for n in (aux.get('nameCandidates') or []):
        n = stock.text(n)
        if n and n not in name_candidates:
            name_candidates.append(n)

    if user:
        chosen = format_provisional_official_name(user) or user
        provisional = False
        rule = 'ユーザー指定の公式名'
        rule_code = 'user'
    elif existing_name:
        chosen = existing_name
        provisional = False
        rule = '既存商品の公式名を維持（仮名では上書きしない）'
        rule_code = 'existing'
    elif aux_name:
        chosen = prefer_unmarked_product_name([aux_name]) or aux_name
        provisional = False
        rule = aux_rule
        rule_code = aux_rule_code
    elif unmarked and len({product_name_core_key(n) for n in all_csv_names if n}) == 1:
        chosen = unmarked
        provisional = False
        rule = '台帳CSVの商品名コアが一意のため標記なしを代表名に採用'
        rule_code = 'unique_core'
    elif earliest_name:
        chosen = prefer_unmarked_product_name([earliest_name]) or earliest_name
        provisional = True
        rule = (
            f'候補が複数のため仮代表名（行{earliest_row}の商品名）。名称未整理'
            if earliest_row is not None
            else '候補が複数のため仮代表名。名称未整理'
        )
        rule_code = 'csv_row_order'
    else:
        chosen = stock.text(group.get('code') or '') or '未命名'
        provisional = True
        rule = '商品名が空のため品番をもとに仮代表名を作成（名称未整理）'
        rule_code = 'fallback_code'

    chosen = format_provisional_official_name(chosen) or chosen

    # 原文 → 登録先（代表名）の対応
    mappings = []
    seen = set()
    for item in name_row_map:
        key = (item['originalName'], item['sourceRowNo'])
        if key in seen:
            continue
        seen.add(key)
        mappings.append({
            'originalName': item['originalName'],
            'sourceRowNo': item['sourceRowNo'],
            'registrationName': chosen,
            'isAlias': product_name_core_key(item['originalName']) != product_name_core_key(chosen)
            or stock.text(item['originalName']) != stock.text(chosen),
        })

    return {
        'officialName': chosen,
        'provisional': provisional,
        'provisionalTag': PROVISIONAL_NAME_TAG if provisional else '',
        'provisionalStatus': PROVISIONAL_NAME_TAG if provisional else '',
        'adoptionRule': rule,
        'adoptionRuleCode': rule_code,
        'nameMappings': mappings,
        'nameCandidates': name_candidates,
        'auxNameReasons': list(aux.get('nameReasons') or []),
        'keepsExistingOfficialName': bool(existing_name) and rule_code == 'existing',
    }


def identity_match_key(value):
    return legacy.identity_match_key(value)


def match_group_key(*, customer, code, product_name, supplier):
    """同じ照合条件の行に選択結果をまとめて適用するためのキー。

    通常品番は品番単位（1品番=1商品）。サイズ・顧客差だけでは分けない。
    別注・品番なしは従来どおり顧客・名前等で区別する。
    """
    code_t = stock.text(code)
    if legacy.is_custom_order_code(code_t):
        raw = '|'.join([
            'custom',
            identity_match_key(customer),
            code_t,
            identity_match_key(product_name),
        ])
    elif not code_t:
        raw = '|'.join([
            'blank',
            identity_match_key(customer),
            identity_match_key(product_name),
            identity_match_key(supplier),
        ])
    elif code_t == stock_product_merge.UNKNOWN_CODE:
        raw = '|'.join([
            'unknown',
            identity_match_key(customer),
            code_t,
            identity_match_key(product_name),
            identity_match_key(supplier),
        ])
    else:
        # 通常品番: 品番だけでグループ（名差・仕入先差は品番比較UIで解決）
        raw = '|'.join(['normal', code_t])
    return hashlib.sha256(raw.encode()).hexdigest()


def sku_variant_key(*, product_name, color='', supplier=''):
    """CSV内の商品内容バリアント（サイズ・顧客は含めない）。

    通常品番の商品名は先頭中古標記「＊/*」だけ無視して同一視する。
    """
    return '|'.join([
        product_name_core_key(product_name),
        identity_match_key(color),
        identity_match_key(supplier),
    ])


def product_map_key(*, customer, code, product_name):
    return legacy.product_map_key(customer=customer, code=code, product_name=product_name)


def row_map_key(*, customer, code, product_name, size, supplier):
    """台帳取込の行対応キー（在庫キーは持たない）。"""
    raw = '|'.join([
        identity_match_key(customer),
        stock.text(code),
        identity_match_key(product_name),
        stock.text(size),
        identity_match_key(supplier),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def decode_csv_bytes(blob):
    return legacy.decode_csv_bytes(blob)


def parse_csv_text(text):
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError('CSVの見出し行がありません。')
    fields = [stock.text(h) for h in reader.fieldnames]
    # UTF-8 BOM 付き見出しの残骸を除去
    if fields and fields[0].startswith('\ufeff'):
        fields[0] = fields[0].lstrip('\ufeff')
    missing = [col for col in REQUIRED_COLUMNS if col not in fields]
    if missing:
        raise ValueError(
            '必要な列がありません: ' + '、'.join(missing)
            + '。列を推測して取り込むことはしません。見出しを確認してください。'
        )
    rows = []
    for i, raw in enumerate(reader, start=2):
        item = {}
        for k in fields:
            v = raw.get(k)
            item[k] = '' if v is None else v
        rows.append((i, item))
    if not rows:
        raise ValueError('読み込む行がありません。')
    return fields, rows


def decode_upload(body):
    raw = body.get('file') if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('CSVファイルを選んでください。')
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as e:
        raise ValueError('CSVファイルを選んでください。') from e
    text, encoding = decode_csv_bytes(blob)
    fields, rows = parse_csv_text(text)
    name = stock.text(body.get('name') or body.get('fileName') or '商品台帳.csv') or '商品台帳.csv'
    sha = hashlib.sha256(blob).hexdigest()
    return {
        'fileName': name,
        'fileSha256': sha,
        'fileSize': len(blob),
        'encoding': encoding,
        'fields': fields,
        'rows': rows,
        'blob': blob,
    }


def decode_aux_upload(body):
    """初期在庫用CSV（任意）。在庫登録はせず、通常仕入先判定の補助入力として読む。"""
    if not isinstance(body, dict):
        return None
    raw = body.get('auxFile') or body.get('openingStockFile')
    if not isinstance(raw, str) or not raw.strip():
        return None
    # legacy の decode を再利用（列検証のみ。DB・在庫は触らない）
    aux_body = {
        'file': raw,
        'name': body.get('auxName') or body.get('openingStockFileName') or '初期在庫.csv',
    }
    uploaded = legacy.decode_upload(aux_body)
    return {
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'fileSize': uploaded['fileSize'],
        'encoding': uploaded['encoding'],
        'fields': uploaded['fields'],
        'rows': uploaded['rows'],
        'ignoredColumns': list(legacy.IGNORED_QTY_COLUMNS),
    }


def parse_aux_stock_qty(raw):
    """「在庫」列のみ。空・不正は 0 扱いせず undecidable。"""
    t = stock.text(raw)
    if not t:
        return None, 'empty'
    try:
        qty = stock.parse_qty(raw, '在庫', allow_zero=True, allow_negative=True)
    except ValueError:
        return None, 'invalid'
    return qty, 'ok'


def _supplier_names_match(a, b):
    ta, tb = stock.text(a), stock.text(b)
    if not ta or not tb:
        return False
    if identity_match_key(ta) == identity_match_key(tb):
        return True
    return legacy.looks_like_company_form_variant(ta, tb)


def _aux_row_matches_candidate(aux_item, *, code, supplier, product_name, customers):
    """台帳側の仕入先候補へ、品番＋仕入先＋商品名（＋顧客）で紐づける。品番だけでは結びつけない。"""
    aux_code = stock.text(aux_item.get('商品コード'))
    # 品番は文字列のまま（先頭ゼロ保持。「8」と「00008」は同一視しない）
    if aux_code != stock.text(code):
        return False
    if legacy.is_custom_order_code(aux_code) or not stock_product_merge.is_normal_product_code(aux_code):
        return False
    if not _supplier_names_match(aux_item.get('仕入先'), supplier):
        return False
    if product_name_core_key(aux_item.get('商品名')) != product_name_core_key(product_name):
        return False
    aux_customer = stock.text(aux_item.get('顧客'))
    ledger_customers = [stock.text(x) for x in (customers or []) if stock.text(x)]
    if aux_customer and ledger_customers:
        if not any(_supplier_names_match(aux_customer, lc) or identity_match_key(aux_customer) == identity_match_key(lc)
                   for lc in ledger_customers):
            # 顧客名の会社表記ゆれも許容
            if not any(legacy.looks_like_company_form_variant(aux_customer, lc) for lc in ledger_customers):
                return False
    return True


def build_aux_row_index(aux_rows):
    """品番文字列で束ねた補助索引（在庫数量は判定時に読む）。"""
    by_code = defaultdict(list)
    for row_no, item in aux_rows or []:
        code = stock.text(item.get('商品コード'))
        if not code:
            continue
        by_code[code].append((row_no, item))
    return by_code


def _collect_supplier_candidates(group):
    """同一商品候補内の仕入先一覧（台帳側名・サイズ・顧客）。"""
    by_sup = {}
    for v in group.get('variants') or []:
        sup = stock.text(v.get('supplier') or '')
        if not sup:
            continue
        slot = by_sup.get(sup)
        if not slot:
            slot = {
                'supplier': sup,
                'ledgerNames': [],
                'sizes': [],
                'customers': [],
                'variantKeys': [],
            }
            by_sup[sup] = slot
        for n in v.get('csvNames') or ([v.get('productName')] if v.get('productName') else []):
            n = stock.text(n)
            if n and n not in slot['ledgerNames']:
                slot['ledgerNames'].append(n)
        for size in v.get('sizes') or []:
            if size and size not in slot['sizes']:
                slot['sizes'].append(size)
        for cust in v.get('customers') or []:
            if cust and cust not in slot['customers']:
                slot['customers'].append(cust)
        key = v.get('key')
        if key and key not in slot['variantKeys']:
            slot['variantKeys'].append(key)
    return list(by_sup.values())


def _judge_aux_for_supplier(aux_index, *, code, candidate):
    """1仕入先候補について初期在庫CSVの照合結果を返す（在庫は書かない）。"""
    supplier = candidate['supplier']
    names = [stock.text(n) for n in (candidate.get('ledgerNames') or []) if stock.text(n)]
    customers = candidate.get('customers') or []
    matched = []
    if names:
        for row_no, item in aux_index.get(stock.text(code), []):
            for name in names:
                if _aux_row_matches_candidate(
                    item, code=code, supplier=supplier, product_name=name, customers=customers,
                ):
                    matched.append((row_no, item))
                    break

    size_rows = []
    has_positive = False
    has_nonzero = False
    undecidable = False
    undecidable_reasons = []
    for row_no, item in matched:
        size = stock.text(item.get('サイズ'))
        qty, qstatus = parse_aux_stock_qty(item.get('在庫'))
        if qstatus != 'ok':
            undecidable = True
            if qstatus == 'empty':
                undecidable_reasons.append(f'{row_no}行目：在庫が空です（0扱いしません）。')
            else:
                undecidable_reasons.append(f'{row_no}行目：在庫「{stock.text(item.get("在庫"))}」が不正です。')
            size_rows.append({
                'rowNo': row_no,
                'size': size,
                'qty': None,
                'qtyDisplay': '（不正/空）',
                'qtyStatus': qstatus,
            })
            continue
        if qty is not None and qty > 0:
            has_positive = True
        if qty is not None and qty != 0:
            has_nonzero = True
        size_rows.append({
            'rowNo': row_no,
            'size': size,
            'qty': qty,
            'qtyDisplay': str(qty),
            'qtyStatus': 'ok',
        })

    if matched:
        aux_status = 'matched'
        aux_status_label = '初期在庫CSVに該当あり'
    else:
        aux_status = 'none'
        aux_status_label = '該当登録なし'  # 「在庫0」とは表示しない

    return {
        'supplier': supplier,
        'ledgerNames': list(names),
        'ledgerName': prefer_unmarked_product_name(names) or (names[0] if names else ''),
        'auxStatus': aux_status,
        'auxStatusLabel': aux_status_label,
        'auxSizeStocks': size_rows,
        'hasAnyAuxRows': bool(matched),
        'hasPositiveStock': bool(has_positive and not undecidable),
        'hasNonZeroStock': bool(has_nonzero and not undecidable),
        'qtyUndecidable': undecidable,
        'qtyUndecidableReasons': undecidable_reasons,
        'customers': list(customers),
        'ledgerSizes': list(candidate.get('sizes') or []),
    }


# 仕入先候補がちょうど1社のときの自動設定理由（プレビュー表示用）
SINGLE_SUPPLIER_AUTO_REASON = '候補が1社のため自動設定'


def judge_primary_supplier_from_aux(supplier_judgments):
    """初期在庫CSVによる通常仕入先の自動判定（候補2社以上）。数量最大・CSV先頭行では選ばない。"""
    judgments = list(supplier_judgments or [])
    if len(judgments) < 2:
        # 0/1社は decide_auto_primary_supplier 側で扱う（ここは複社用）
        return {
            'autoPrimarySupplier': '',
            'autoReason': '',
            'needsManualPrimary': False,
            'manualReason': '',
        }

    if any(j.get('qtyUndecidable') for j in judgments):
        reasons = []
        for j in judgments:
            reasons.extend(j.get('qtyUndecidableReasons') or [])
        return {
            'autoPrimarySupplier': '',
            'autoReason': '',
            'needsManualPrimary': True,
            'manualReason': '初期在庫CSVの在庫に空欄または不正値があるため自動判定できません。'
            + (' ' + ' '.join(reasons[:3]) if reasons else ''),
        }

    positive = [j for j in judgments if j.get('hasPositiveStock')]
    if len(positive) == 1:
        return {
            'autoPrimarySupplier': positive[0]['supplier'],
            'autoReason': '初期在庫CSVで正の在庫がある仕入先が1社のみのため、通常仕入先にしました。',
            'needsManualPrimary': False,
            'manualReason': '',
        }
    if len(positive) > 1:
        return {
            'autoPrimarySupplier': '',
            'autoReason': '',
            'needsManualPrimary': True,
            'manualReason': '正の在庫がある仕入先が複数あるため、通常仕入先は手動で選んでください。',
        }

    with_rows = [j for j in judgments if j.get('hasAnyAuxRows')]
    if len(with_rows) == 1:
        return {
            'autoPrimarySupplier': with_rows[0]['supplier'],
            'autoReason': '正の在庫はないが、初期在庫CSVに該当行がある仕入先が1社のみのため、通常仕入先にしました。',
            'needsManualPrimary': False,
            'manualReason': '',
        }
    if len(with_rows) > 1:
        return {
            'autoPrimarySupplier': '',
            'autoReason': '',
            'needsManualPrimary': True,
            'manualReason': '正の在庫がなく、該当行がある仕入先も複数あるため、通常仕入先は手動で選んでください。',
        }
    return {
        'autoPrimarySupplier': '',
        'autoReason': '',
        'needsManualPrimary': True,
        'manualReason': '初期在庫CSVに該当登録がないため、通常仕入先は手動で選んでください。',
    }


def _suggested_official_name_for_primary(group, primary_supplier):
    """自動で通常仕入先が決まったとき、その仕入先の商品名が唯一なら公式名候補にする。"""
    if not primary_supplier:
        return '', True
    names = []
    for v in group.get('variants') or []:
        if not _supplier_names_match(v.get('supplier'), primary_supplier):
            continue
        for n in v.get('csvNames') or ([v.get('productName')] if v.get('productName') else []):
            n = stock.text(n)
            if n and n not in names:
                names.append(n)
    cores = {product_name_core_key(n) for n in names if n}
    if len(cores) != 1:
        return '', True
    official = prefer_unmarked_product_name(names) or (names[0] if names else '')
    return official, False


def _unique_display_name(names):
    """コアが一意なら標記なし優先の表示名。複数なら空。"""
    cleaned = [stock.text(n) for n in (names or []) if stock.text(n)]
    if not cleaned:
        return ''
    cores = {product_name_core_key(n) for n in cleaned}
    if len(cores) != 1:
        return ''
    return prefer_unmarked_product_name(cleaned) or cleaned[0]


def _collect_name_supplier_candidates(group):
    """同一商品候補内の 商品名+仕入先 ペア（バリアント単位）。別候補の名と仕入先を混ぜない。"""
    out = []
    seen = set()
    for v in group.get('variants') or []:
        supplier = stock.text(v.get('supplier') or '')
        names = []
        for n in v.get('csvNames') or ([v.get('productName')] if v.get('productName') else []):
            n = stock.text(n)
            if n and n not in names:
                names.append(n)
        if not names:
            continue
        display_name = prefer_unmarked_product_name(names) or names[0]
        key = (product_name_core_key(display_name), identity_match_key(supplier))
        if key in seen:
            # 同一コア名＋仕入先は名前を統合
            for slot in out:
                if (
                    product_name_core_key(slot['productName']) == key[0]
                    and identity_match_key(slot['supplier']) == key[1]
                ):
                    for n in names:
                        if n not in slot['ledgerNames']:
                            slot['ledgerNames'].append(n)
                    slot['productName'] = prefer_unmarked_product_name(slot['ledgerNames']) or slot['productName']
                    for size in v.get('sizes') or []:
                        if size and size not in slot['sizes']:
                            slot['sizes'].append(size)
                    for cust in v.get('customers') or []:
                        if cust and cust not in slot['customers']:
                            slot['customers'].append(cust)
                    break
            continue
        seen.add(key)
        out.append({
            'productName': display_name,
            'supplier': supplier,
            'ledgerNames': list(names),
            'sizes': list(v.get('sizes') or []),
            'customers': list(v.get('customers') or []),
            'variantKey': v.get('key') or '',
        })
    return out


def _full_package_for_candidate(cand):
    """候補1組のフルパッケージ（名・仕入先・色・加工・素材）。別候補と混ぜない。"""
    names = list(cand.get('ledgerNames') or [])
    display = prefer_unmarked_product_name(names) or stock.text(cand.get('productName') or '')
    pc = paren_classify.classify_product_name(display)
    planned = stock.text(pc.get('plannedName') or '') or display
    return {
        'productName': display,
        'plannedName': planned,
        'supplier': stock.text(cand.get('supplier') or ''),
        'ledgerNames': names,
        'color': stock.text(pc.get('color') or ''),
        'processing': stock.text(pc.get('processing') or ''),
        'material': stock.text(pc.get('material') or ''),
        'parenNeedsConfirm': bool(pc.get('needsConfirm')),
        'parenConfirmReasons': list(pc.get('confirmReasons') or []),
        'autoClassified': bool(pc.get('autoClassified')),
        'sizes': list(cand.get('sizes') or []),
        'customers': list(cand.get('customers') or []),
        'variantKey': cand.get('variantKey') or '',
    }


def _kept_packages_excluding(candidates, chosen):
    """非採用候補を原文パッケージのまま残す（メイン仕様へ書き換えない）。"""
    chosen_key = (
        product_name_core_key(chosen.get('productName') or ''),
        identity_match_key(chosen.get('supplier') or ''),
    )
    kept = []
    for cand in candidates or []:
        key = (
            product_name_core_key(cand.get('productName') or ''),
            identity_match_key(cand.get('supplier') or ''),
        )
        if key == chosen_key:
            continue
        pkg = _full_package_for_candidate(cand)
        kept.append(pkg)
    return kept


def _adopt_candidate_package(hit, *, candidates, rule, rule_code, reasons=None):
    """採用候補のフルパッケージ＋非採用の原文パッケージを返す。"""
    package = _full_package_for_candidate(hit)
    kept = _kept_packages_excluding(candidates, package)
    chosen_name = package['plannedName'] if (
        package.get('autoClassified') and not package.get('parenNeedsConfirm')
    ) else package['productName']
    # 括弧分類が曖昧なら代表名は原文のまま（フィールドだけ埋める）
    if package.get('parenNeedsConfirm'):
        chosen_name = package['productName']
    return {
        'suggestedOfficialName': chosen_name,
        'suggestedPrimarySupplier': package['supplier'],
        'officialNameNeedsChoice': False,
        'pairNeedsChoice': False,
        'nameAdoptionRule': rule,
        'nameAdoptionRuleCode': rule_code,
        'pairAdoptionRule': rule,
        'pairAdoptionRuleCode': rule_code,
        'nameCandidates': [
            c.get('productName') for c in (candidates or []) if c.get('productName')
        ],
        'pairCandidates': [
            {
                'productName': c.get('productName') or '',
                'supplier': c.get('supplier') or '',
                'ledgerNames': list(c.get('ledgerNames') or []),
            }
            for c in (candidates or [])
        ],
        'nameReasons': list(reasons or []),
        'adoptedPackage': package,
        'keptPackages': kept,
        'adoptedColor': package.get('color') or '',
        'adoptedProcessing': package.get('processing') or '',
        'adoptedMaterial': package.get('material') or '',
    }


def suggest_main_candidate_from_aux(group, aux_index):
    """同一商品候補の中だけで、初期在庫CSVから代表候補のフルパッケージを提案する。

    規則:
    - 「在庫」列のみ（購入数量・出荷数・補正在庫数は使わない）
    - 候補ごとに非ゼロ在庫行の有無を見る（正負の相殺なし。数量最大では選ばない）
    - 候補が1組 → 在庫0でもその候補のフルパッケージを採用（単一仕入先自動は別途維持）
    - 非ゼロを持つ候補がちょうど1つ → その候補のフルセット（名・仕入先・色・加工・素材）を採用
    - 色・加工・素材が候補間で違っても、在庫一意採用ならそれだけでは要確認にしない
    - 複数・全ゼロ（候補2組以上）・数量不正/空・照合不明 → 自動確定しない
    - 別候補の商品名・仕入先・仕様を組み合わせない
    """
    empty = {
        'suggestedOfficialName': '',
        'suggestedPrimarySupplier': '',
        'officialNameNeedsChoice': True,
        'pairNeedsChoice': True,
        'nameAdoptionRule': '',
        'nameAdoptionRuleCode': '',
        'pairAdoptionRule': '',
        'pairAdoptionRuleCode': '',
        'nameCandidates': [],
        'pairCandidates': [],
        'nameReasons': [],
        'adoptedPackage': None,
        'keptPackages': [],
        'adoptedColor': '',
        'adoptedProcessing': '',
        'adoptedMaterial': '',
    }
    if not aux_index:
        return empty
    code = stock.text(group.get('code') or '')
    if not stock_product_merge.is_normal_product_code(code):
        return empty

    candidates = _collect_name_supplier_candidates(group)
    if not candidates:
        return empty

    name_candidates = []
    pair_candidates = []
    reasons = []
    nonzero_hits = []
    any_undecidable = False

    for cand in candidates:
        supplier = cand['supplier']
        names = cand['ledgerNames']
        display_name = cand['productName']
        if display_name and display_name not in name_candidates:
            name_candidates.append(display_name)
        pair_candidates.append({
            'productName': display_name,
            'supplier': supplier,
            'ledgerNames': list(names),
        })
        matched = []
        for row_no, item in aux_index.get(code, []):
            for name in names:
                if _aux_row_matches_candidate(
                    item, code=code, supplier=supplier, product_name=name,
                    customers=cand.get('customers') or [],
                ):
                    matched.append((row_no, item, name))
                    break
        has_nonzero = False
        for row_no, item, _name in matched:
            qty, qstatus = parse_aux_stock_qty(item.get('在庫'))
            if qstatus != 'ok':
                any_undecidable = True
                if qstatus == 'empty':
                    reasons.append(f'{row_no}行目：在庫が空です（0扱いしません）。')
                else:
                    reasons.append(
                        f'{row_no}行目：在庫「{stock.text(item.get("在庫"))}」が不正です（0扱いしません）。'
                    )
                continue
            if qty is not None and qty != 0:
                has_nonzero = True
        if has_nonzero:
            nonzero_hits.append(cand)

    if any_undecidable:
        reasons.append(
            '在庫の空欄または不正があるため、代表候補は自動確定しません。'
        )
        return {
            **empty,
            'nameCandidates': name_candidates,
            'pairCandidates': pair_candidates,
            'nameReasons': reasons,
        }

    # 候補1組 → 在庫0でもフルパッケージ採用
    if len(candidates) == 1:
        rule = '候補が1組のため、この候補をメインに採用'
        return _adopt_candidate_package(
            candidates[0], candidates=candidates, rule=rule,
            rule_code='aux_single_candidate', reasons=reasons,
        )

    if len(nonzero_hits) == 1:
        rule = (
            '在庫が0以外の候補が1組のため、この候補をメインに採用'
        )
        return _adopt_candidate_package(
            nonzero_hits[0], candidates=candidates, rule=rule,
            rule_code='aux_nonzero_pair', reasons=reasons,
        )

    if len(nonzero_hits) > 1:
        reasons.append(
            '非ゼロ在庫がある候補が複数あるため、代表候補は自動確定しません'
            '（数量最大では選びません。別候補の名・仕入先・仕様も組み合わせません）。'
        )
    elif not nonzero_hits:
        reasons.append(
            '非ゼロ在庫がある候補がないため、初期在庫CSVからは代表候補を'
            '自動確定しません（該当行の有無だけでは選びません）。'
        )
    return {
        **empty,
        'nameCandidates': name_candidates,
        'pairCandidates': pair_candidates,
        'nameReasons': reasons,
    }


def suggest_main_name_from_aux(group, aux_index):
    """後方互換: ペア提案のうち名前側を返す。"""
    return suggest_main_candidate_from_aux(group, aux_index)


def decide_auto_primary_supplier(candidates, *, aux_judgments=None, aux_uploaded=False):
    """通常仕入先の自動決定（プレビューと確定で共通）。

    - 候補0社 → 未設定（捏造しない）
    - 候補1社 → その仕入先を自動設定（初期在庫CSVの有無に依存しない）
    - 候補2社以上 → 初期在庫CSV判定（#152）。補助なしなら手動
    """
    cands = list(candidates or [])
    if len(cands) == 0:
        return {
            'autoPrimarySupplier': '',
            'autoReason': '',
            'needsManualPrimary': False,
            'manualReason': '',
            'decisionKind': 'none',
        }
    if len(cands) == 1:
        return {
            'autoPrimarySupplier': stock.text(cands[0].get('supplier') or ''),
            'autoReason': SINGLE_SUPPLIER_AUTO_REASON,
            'needsManualPrimary': False,
            'manualReason': '',
            'decisionKind': 'single_candidate',
        }
    if aux_uploaded and aux_judgments is not None:
        decision = judge_primary_supplier_from_aux(aux_judgments)
        kind = 'aux' if decision.get('autoPrimarySupplier') else 'manual_needed'
        return {**decision, 'decisionKind': kind}
    return {
        'autoPrimarySupplier': '',
        'autoReason': '',
        'needsManualPrimary': True,
        'manualReason': '通常仕入先を選んでください。',
        'decisionKind': 'manual_needed',
    }


def _empty_supplier_judgment(candidate):
    """補助CSVなしのときの仕入先候補表示用。"""
    names = [stock.text(n) for n in (candidate.get('ledgerNames') or []) if stock.text(n)]
    return {
        'supplier': stock.text(candidate.get('supplier') or ''),
        'ledgerNames': list(names),
        'ledgerName': prefer_unmarked_product_name(names) or (names[0] if names else ''),
        'auxStatus': 'skipped',
        'auxStatusLabel': '（補助CSVなし）',
        'auxSizeStocks': [],
        'hasAnyAuxRows': False,
        'hasPositiveStock': False,
        'hasNonZeroStock': False,
        'qtyUndecidable': False,
        'qtyUndecidableReasons': [],
        'customers': list(candidate.get('customers') or []),
        'ledgerSizes': list(candidate.get('sizes') or []),
    }


def _apply_planned_roles(judgments, auto_primary):
    for j in judgments:
        if auto_primary and _supplier_names_match(j.get('supplier'), auto_primary):
            j['plannedRole'] = 'primary'
            j['plannedRoleLabel'] = '通常'
        elif auto_primary:
            j['plannedRole'] = 'sub'
            j['plannedRoleLabel'] = 'サブ'
        else:
            j['plannedRole'] = 'undecided'
            j['plannedRoleLabel'] = '未定（選択が必要）'


def attach_aux_primary_judgment(sku_groups, aux_uploaded):
    """品番グループへ通常仕入先の自動判定を付与（在庫は変更しない）。

    補助CSVの有無に関わらず候補1社なら自動設定する。複社は補助CSV規則（#152）。
    """
    aux_index = build_aux_row_index(aux_uploaded['rows']) if aux_uploaded else None
    meta = None
    if aux_uploaded:
        meta = {
            'fileName': aux_uploaded['fileName'],
            'fileSha256': aux_uploaded['fileSha256'],
            'fileSize': aux_uploaded['fileSize'],
            'encoding': aux_uploaded['encoding'],
            'rowCount': len(aux_uploaded['rows']),
            'ignoredColumns': aux_uploaded.get('ignoredColumns') or [],
            'note': (
                '初期在庫用CSVは通常仕入先・代表候補のフルパッケージ判定補助だけに使います。'
                '在庫数量・単価・初期在庫・入出庫履歴は登録・変更しません。'
                '非ゼロ在庫（負含む）がある候補が1組のとき、その候補の'
                '商品名・仕入先・色・加工・素材をセットで採用します。'
                '補助CSVにだけある商品・仕入先は新規作成しません。'
            ),
        }

    for group in sku_groups:
        candidates = _collect_supplier_candidates(group)
        if aux_index is not None:
            judgments = [
                _judge_aux_for_supplier(aux_index, code=group['code'], candidate=c)
                for c in candidates
            ]
        else:
            judgments = [_empty_supplier_judgment(c) for c in candidates]

        decision = decide_auto_primary_supplier(
            candidates,
            aux_judgments=judgments if aux_index is not None else None,
            aux_uploaded=bool(aux_uploaded),
        )
        auto_primary = decision['autoPrimarySupplier']
        auto_reason = decision['autoReason']
        needs_manual = decision['needsManualPrimary']
        manual_reason = decision['manualReason']
        decision_kind = decision.get('decisionKind') or ''

        # 代表候補のフルパッケージ提案（非ゼロ在庫 or 候補1組）
        pair_from_aux = suggest_main_candidate_from_aux(group, aux_index) if aux_index is not None else {
            'suggestedOfficialName': '',
            'suggestedPrimarySupplier': '',
            'officialNameNeedsChoice': True,
            'pairNeedsChoice': True,
            'nameAdoptionRule': '',
            'nameAdoptionRuleCode': '',
            'pairAdoptionRule': '',
            'pairAdoptionRuleCode': '',
            'nameCandidates': [],
            'pairCandidates': [],
            'nameReasons': [],
            'adoptedPackage': None,
            'keptPackages': [],
            'adoptedColor': '',
            'adoptedProcessing': '',
            'adoptedMaterial': '',
        }
        pair_supplier = stock.text(pair_from_aux.get('suggestedPrimarySupplier') or '')
        pair_code = stock.text(pair_from_aux.get('pairAdoptionRuleCode') or '')
        # 在庫一意／候補1組で決まったときは、その仕入先を通常に（別候補の名と混ぜない）。
        # 候補1社の自動（#154）は維持し、理由を上書きしない。
        if (
            pair_supplier
            and not pair_from_aux.get('pairNeedsChoice')
            and decision_kind != 'single_candidate'
        ):
            auto_primary = pair_supplier
            auto_reason = stock.text(pair_from_aux.get('pairAdoptionRule') or '') or (
                '在庫が0以外の候補が1組のため、この候補をメインに採用'
            )
            needs_manual = False
            manual_reason = ''
            decision_kind = pair_code or 'aux_nonzero_pair'
        elif (
            pair_supplier
            and not pair_from_aux.get('pairNeedsChoice')
            and decision_kind == 'single_candidate'
            and not auto_primary
        ):
            auto_primary = pair_supplier

        _apply_planned_roles(judgments, auto_primary)

        suggested_name = stock.text(pair_from_aux.get('suggestedOfficialName') or '')
        name_needs = bool(pair_from_aux.get('officialNameNeedsChoice', True))
        name_rule = stock.text(pair_from_aux.get('nameAdoptionRule') or '')
        name_rule_code = stock.text(pair_from_aux.get('nameAdoptionRuleCode') or '')
        if not suggested_name and auto_primary and not name_needs:
            pass
        if not suggested_name:
            # ペア未決でも、通常仕入先側の台帳名が一意なら名前候補にする（仕入先は変えない）
            fallback_name, fallback_needs = _suggested_official_name_for_primary(group, auto_primary)
            if fallback_name and not fallback_needs:
                suggested_name = fallback_name
                name_needs = False
                name_rule = '通常仕入先候補に紐づく台帳商品名が一意のため代表名に採用'
                name_rule_code = 'primary_supplier_unique_name'
        multi = len(candidates) >= 2
        group['auxPrimary'] = {
            # 複社＋補助CSVのとき判定パネルを出す
            'applicable': bool(multi and aux_uploaded),
            'singleCandidateAuto': decision_kind == 'single_candidate',
            'reason': (
                '' if multi and aux_uploaded
                else (
                    SINGLE_SUPPLIER_AUTO_REASON if decision_kind == 'single_candidate'
                    else ('仕入先候補なし' if len(candidates) == 0 else '')
                )
            ),
            'supplierJudgments': judgments,
            'autoPrimarySupplier': auto_primary,
            'autoReason': auto_reason,
            'needsManualPrimary': needs_manual,
            'manualReason': manual_reason,
            'decisionKind': decision_kind,
            'suggestedOfficialName': suggested_name,
            'suggestedPrimarySupplier': pair_supplier,
            'officialNameNeedsChoice': name_needs,
            'pairNeedsChoice': bool(pair_from_aux.get('pairNeedsChoice', True)),
            'nameAdoptionRule': name_rule,
            'nameAdoptionRuleCode': name_rule_code,
            'pairAdoptionRule': stock.text(pair_from_aux.get('pairAdoptionRule') or ''),
            'pairAdoptionRuleCode': stock.text(pair_from_aux.get('pairAdoptionRuleCode') or ''),
            'nameCandidates': list(pair_from_aux.get('nameCandidates') or []),
            'pairCandidates': list(pair_from_aux.get('pairCandidates') or []),
            'nameReasons': list(pair_from_aux.get('nameReasons') or []),
            'adoptedPackage': pair_from_aux.get('adoptedPackage'),
            'keptPackages': list(pair_from_aux.get('keptPackages') or []),
            'adoptedColor': stock.text(pair_from_aux.get('adoptedColor') or ''),
            'adoptedProcessing': stock.text(pair_from_aux.get('adoptedProcessing') or ''),
            'adoptedMaterial': stock.text(pair_from_aux.get('adoptedMaterial') or ''),
        }
    return meta


def apply_aux_full_package_adoption(sku_groups, sku_decisions=None):
    """在庫一意／候補1組でメイン採用できた品番は、色・加工・素材差だけでは要確認に残さない。

    - 採用候補のフルパッケージを代表に
    - 非採用候補は別名／サブ／原文仕様マッピングとして保持（メイン仕様へ書き換えない）
    - 商品種・男女の疑い、既存重複（2件以上）は引き続き確認
    - 既存の通常仕入先変更は明示確認が必要（黙って変えない）
    - ユーザーの品番判断があれば自動クリアせず手動を優先
    - 括弧分類が曖昧な採用候補は自動確定しない
    """
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    for group in sku_groups:
        aux = group.get('auxPrimary') or {}
        rule_code = stock.text(
            aux.get('pairAdoptionRuleCode') or aux.get('nameAdoptionRuleCode') or ''
        )
        if rule_code not in ('aux_nonzero_pair', 'aux_single_candidate'):
            continue
        if aux.get('pairNeedsChoice') or aux.get('officialNameNeedsChoice'):
            continue
        package = aux.get('adoptedPackage')
        if not isinstance(package, dict) or not package.get('productName'):
            continue
        code = stock.text(group.get('code') or '')
        raw = decisions.get(code) or decisions.get(str(code)) or {}
        if isinstance(raw, dict) and stock.text(raw.get('decision')):
            # 手動判断あり → 提案表示のみ（上書きしない）
            _attach_stock_pick_display(group, aux, package, auto_cleared=False)
            group['auxStockPickManualOverride'] = True
            continue
        if package.get('parenNeedsConfirm'):
            # 採用候補自体の括弧が曖昧 → 自動確定しない
            group['auxStockPickBlocked'] = True
            group['auxStockPickBlockReason'] = '採用候補の括弧分類が曖昧なため自動確定しません'
            continue
        if int(group.get('existingCount') or 0) > 1:
            # 残す商品の選択が必要。パッケージ情報だけ付与
            group['auxStockPickPendingExisting'] = True
            _attach_stock_pick_display(group, aux, package, auto_cleared=False)
            continue
        # 既存1件で通常仕入先が変わる場合は明示確認が必要
        if int(group.get('existingCount') or 0) == 1:
            existing_primary = stock.text(
                ((group.get('existingProducts') or [{}])[0]).get('supplierName') or ''
            )
            adopted_sup = stock.text(
                aux.get('autoPrimarySupplier') or package.get('supplier') or ''
            )
            if (
                existing_primary
                and adopted_sup
                and not _supplier_names_match(existing_primary, adopted_sup)
                and not bool(raw.get('confirmPrimaryChange'))
            ):
                group['auxStockPickNeedsPrimaryConfirm'] = True
                _attach_stock_pick_display(group, aux, package, auto_cleared=False)
                _soften_spec_only_reasons(group)
                continue
        if group.get('kindDiffPossible') or group.get('genderDiffPossible'):
            # 色・加工・素材以外の実質差 → 確認のまま（理由から色・加工・素材は外しうる）
            _soften_spec_only_reasons(group)
            _attach_stock_pick_display(group, aux, package, auto_cleared=False)
            continue

        # 色・加工・素材・名差・仕入先差は在庫一意採用で解消 → 自動まとめ
        was_decision = bool(group.get('needsDecision'))
        group['needsDecision'] = False
        group['plannedSingleProduct'] = True
        group['status'] = 'auto'
        group['usedMarkerAutoMerged'] = bool(group.get('usedMarkerAutoMerged'))
        group['auxStockPickAuto'] = True
        group['auxStockPickClearedReview'] = was_decision
        reason = stock.text(aux.get('pairAdoptionRule') or '') or (
            '在庫が0以外の候補が1組のため、この候補をメインに採用'
        )
        group['auxStockPickReason'] = reason
        group['reviewTier'] = REVIEW_TIER_AUTO
        group['reviewTierLabel'] = REVIEW_TIER_LABELS[REVIEW_TIER_AUTO]
        group['bulkApprovable'] = False
        group['colorDiffPossible'] = False
        group['processingDiffPossible'] = False
        group['fabricDiffPossible'] = False
        # 名差・仕入先差は採用で解消済みとして表示上も落とす
        group['nameWordingDiff'] = False
        group['supplierDiff'] = False
        kept = list(aux.get('keptPackages') or [])
        kept_names = []
        for k in kept:
            for n in k.get('ledgerNames') or ([k.get('productName')] if k.get('productName') else []):
                if n and n not in kept_names:
                    kept_names.append(n)
        group['reviewReasons'] = [{
            'code': 'aux_stock_pick',
            'label': reason,
            'values': [package.get('productName') or '', package.get('supplier') or ''],
        }]
        if kept_names:
            group['reviewReasons'].append({
                'code': 'kept_aliases',
                'label': '非採用の商品名は照合用別名として残します',
                'values': kept_names,
            })
        group['otherDiffs'] = []
        group['applyScope'] = (
            f'品番「{group.get("code")}」は初期在庫CSVの在庫一意採用で'
            f'メイン候補のフルパッケージを自動採用します。'
            f'非採用候補の名・仕入先・色・加工・素材は原文のままマッピングに残します。'
        )
        _attach_stock_pick_display(group, aux, package, auto_cleared=True)

        # 商品計画を採用パッケージへ
        plan = dict(group.get('productPlan') or {})
        plan['productName'] = (
            stock.text(aux.get('suggestedOfficialName') or '')
            or package.get('plannedName')
            or package.get('productName')
            or plan.get('productName')
            or ''
        )
        plan['supplier'] = package.get('supplier') or plan.get('supplier') or ''
        plan['color'] = package.get('color') or ''
        plan['processing'] = package.get('processing') or ''
        plan['material'] = package.get('material') or ''
        plan['auxStockPickAuto'] = True
        plan['auxStockPickReason'] = reason
        plan['keptPackages'] = kept
        plan['nameAdoptionRule'] = reason
        plan['label'] = '新規商品' if int(group.get('existingCount') or 0) == 0 else '既存商品を使用'
        plan['kind'] = 'reuse' if int(group.get('existingCount') or 0) == 1 else 'create'
        group['productPlan'] = plan
        group['plannedOfficialName'] = plan['productName']
        group['plannedPrimarySupplier'] = (
            stock.text(aux.get('autoPrimarySupplier') or '')
            or package.get('supplier')
            or ''
        )
        group['plannedPrimarySupplierReason'] = reason
        group['plannedColor'] = package.get('color') or ''
        group['plannedProcessing'] = package.get('processing') or ''
        group['plannedMaterial'] = package.get('material') or ''
        group['nameAdoptionRule'] = reason


def _soften_spec_only_reasons(group):
    """在庫一意採用時、色・加工・素材の疑い理由を「採用で解消」表示へ差し替え（確認は継続）。"""
    reasons = []
    for r in group.get('reviewReasons') or []:
        code = stock.text(r.get('code') or '')
        if code in ('color', 'processing', 'fabric'):
            continue
        reasons.append(r)
    reasons.append({
        'code': 'aux_stock_pick_partial',
        'label': '在庫一意でメイン候補は決まっていますが、商品種・対象などの確認が残っています',
        'values': [],
    })
    group['reviewReasons'] = reasons
    group['colorDiffPossible'] = False
    group['processingDiffPossible'] = False
    group['fabricDiffPossible'] = False
    group['otherDiffs'] = [
        d for d in (group.get('otherDiffs') or [])
        if stock.text(d.get('code') or '') not in ('color', 'processing', 'fabric')
    ]


def _attach_stock_pick_display(group, aux, package, *, auto_cleared):
    group['adoptedPackage'] = package
    group['keptPackages'] = list(aux.get('keptPackages') or [])
    group['auxStockPickReason'] = stock.text(
        aux.get('pairAdoptionRule') or group.get('auxStockPickReason') or ''
    )
    group['auxStockPickAuto'] = bool(auto_cleared)


def stamp_aux_adopted_package_on_rows(classified, sku_groups):
    """在庫一意採用グループの各行へメイン仕様を載せ、非採用行は原文仕様を provenance に残す。"""
    by_code = {g['code']: g for g in sku_groups}
    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code') or '')
        group = by_code.get(code)
        if not group or not group.get('auxStockPickAuto'):
            continue
        aux = group.get('auxPrimary') or {}
        package = aux.get('adoptedPackage') or group.get('adoptedPackage') or {}
        if not package:
            continue
        csv_name = stock.text((row.get('csv') or {}).get('productName') or '')
        csv_supplier = stock.text((row.get('csv') or {}).get('supplier') or '')
        main_name = stock.text(
            aux.get('suggestedOfficialName')
            or package.get('plannedName')
            or package.get('productName')
            or ''
        )
        main_supplier = stock.text(
            aux.get('autoPrimarySupplier') or package.get('supplier') or ''
        )
        is_main = (
            product_name_core_key(csv_name) == product_name_core_key(package.get('productName') or '')
            and (
                not csv_supplier
                or not main_supplier
                or identity_match_key(csv_supplier) == identity_match_key(main_supplier)
            )
        )
        # 行自身の括弧分類（原文）を provenance に残す
        pc = row.get('parenClassification') or {}
        provenance = {
            'originalName': csv_name,
            'originalSupplier': csv_supplier,
            'color': stock.text(pc.get('color') or ''),
            'processing': stock.text(pc.get('processing') or ''),
            'material': stock.text(pc.get('material') or ''),
            'plannedName': stock.text(pc.get('plannedName') or '') or csv_name,
            'isMainCandidate': bool(is_main),
        }
        row['specProvenance'] = provenance
        row['auxStockPickAuto'] = True
        row['auxStockPickReason'] = group.get('auxStockPickReason') or ''

        prod = dict(row.get('product') or {})
        prod['officialName'] = main_name
        prod['applyOfficialName'] = True
        prod['csvOriginalName'] = csv_name
        prod['primarySupplierName'] = main_supplier
        prod['officialColor'] = stock.text(package.get('color') or '')
        prod['proposedColor'] = stock.text(package.get('color') or '')
        prod['officialMaterial'] = stock.text(package.get('material') or '')
        prod['proposedMaterial'] = stock.text(package.get('material') or '')
        prod['skuMerge'] = True
        # 既存1件なら再利用
        if int(group.get('existingCount') or 0) == 1 and (group.get('existingProducts') or []):
            prod['id'] = (group.get('existingProducts') or [{}])[0].get('id')
            prod['action'] = 'reuse'
            # 既存公式名は黙って上書きしない（namePlan 側の existing 優先と整合）
            name_plan = group.get('namePlan') or {}
            if name_plan.get('keepsExistingOfficialName'):
                prod['applyOfficialName'] = False
                prod['officialName'] = stock.text(name_plan.get('officialName') or '') or main_name
        mappings = list((group.get('namePlan') or {}).get('nameMappings') or [])
        if not mappings:
            # 最低限、原文→代表の対応を残す
            mappings = [{
                'originalName': csv_name,
                'sourceRowNo': row.get('rowNo'),
                'registrationName': prod['officialName'],
                'isAlias': product_name_core_key(csv_name) != product_name_core_key(prod['officialName']),
            }]
        prod['nameMappings'] = mappings
        prod['nameAdoptionRule'] = group.get('auxStockPickReason') or ''
        row['product'] = prod

        # 取扱の加工: メイン候補のみ反映。非メインの加工は provenance / row_map に残し、
        # 取扱を増やして後続の初期在庫取込を止めない（メイン仕様へも書き換えない）。
        handle = dict(row.get('handle') or {})
        if is_main:
            main_processing = stock.text(package.get('processing') or '')
            row_processing = stock.text(pc.get('processing') or '')
            use_processing = row_processing or main_processing
            if use_processing:
                handle['proposedEmbroidery'] = use_processing
                if handle.get('action') == 'create' or handle.get('embroidery') in (
                    '', None, PENDING_EMBROIDERY,
                ):
                    handle['embroidery'] = use_processing
                    handle['embroideryLabel'] = use_processing
                    handle['pendingEmbroiderySort'] = False
                    handle['fromParenClassification'] = True
        else:
            # 非メイン行の括弧加工は商品取扱へ載せず、原文仕様マッピング側に保持
            row_proc = stock.text(pc.get('processing') or '')
            handle['provenanceEmbroidery'] = row_proc
            handle['fromParenClassification'] = False
            if row_proc and stock.text(handle.get('embroidery') or '') == row_proc:
                handle['embroidery'] = PENDING_EMBROIDERY
                handle['embroideryLabel'] = PENDING_EMBROIDERY
                handle['pendingEmbroiderySort'] = True
                handle.pop('proposedEmbroidery', None)
        row['handle'] = handle

        # 自動まとめ扱い
        row['skuAutoMerged'] = True
        if row.get('status') in (STATUS_REVIEW,) and not row.get('parenNeedsConfirm'):
            # 色差だけで needs_review になっていた行をコミット可能へ戻す
            if prod.get('action') == 'reuse' or prod.get('id'):
                row['status'] = STATUS_REUSE
                row['statusLabel'] = STATUS_LABELS[STATUS_REUSE]
            else:
                row['status'] = STATUS_CREATE
                row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
            row['canCommit'] = True
            row['reasons'] = [
                r for r in (row.get('reasons') or [])
                if '色' not in stock.text(r) and '加工' not in stock.text(r) and '生地' not in stock.text(r)
            ]


def _existing_primary_supplier_name(group, keep_product_id):
    if not keep_product_id:
        return ''
    for p in group.get('existingProducts') or []:
        if int(p.get('id') or 0) == int(keep_product_id):
            return stock.text(p.get('supplierName') or '')
    return ''


def _resolve_effective_primary_supplier(group, raw_decision):
    """ユーザー手動指定を優先。未指定なら自動結果（候補1社 or 初期在庫CSV）を使う。"""
    raw = raw_decision if isinstance(raw_decision, dict) else {}
    manual = bool(raw.get('primarySupplierManual'))
    chosen = stock.text(raw.get('primarySupplier') or '')
    aux = group.get('auxPrimary') or {}
    auto = stock.text(aux.get('autoPrimarySupplier') or '')
    auto_reason = stock.text(aux.get('autoReason') or '')
    conflict = False
    if manual and chosen:
        if auto and not _supplier_names_match(chosen, auto):
            conflict = True
        return chosen, 'manual', conflict, auto, auto_reason
    if chosen and not manual:
        # 古いUI互換: 明示値があればそれを使う（再計算で上書きしない）
        return chosen, 'user', False, auto, auto_reason
    if auto:
        source = 'single' if aux.get('decisionKind') == 'single_candidate' else 'auto'
        return auto, source, False, auto, auto_reason
    return '', 'none', False, auto, auto_reason


def used_marker_possible(product_name):
    return legacy.strip_used_marker(product_name)[1]


def product_name_core_key(name):
    return legacy.product_name_core_key(name)


def prefer_unmarked_product_name(names):
    return legacy.prefer_unmarked_product_name(names)


def names_differ_only_by_used_marker(a, b):
    return legacy.names_differ_only_by_used_marker(a, b)


def names_equivalent_core(a, b):
    """空白・全角半角・先頭＊/*・明示シノニムを吸収したコアが一致するか。"""
    ta, tb = stock.text(a), stock.text(b)
    if not ta or not tb:
        return False
    return product_name_core_key(ta) == product_name_core_key(tb)


def ensure_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS product_ledger_import_runs (
      id INTEGER PRIMARY KEY,
      file_sha256 TEXT NOT NULL,
      file_name TEXT NOT NULL,
      file_size INTEGER NOT NULL,
      row_count INTEGER NOT NULL,
      actor_name TEXT NOT NULL DEFAULT '',
      preview_at TEXT NOT NULL,
      preview_fingerprint TEXT NOT NULL DEFAULT '',
      commit_token TEXT UNIQUE,
      committed_at TEXT,
      result_json TEXT NOT NULL DEFAULT '{}',
      backup_path TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    )''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_import_runs_sha '
        'ON product_ledger_import_runs(file_sha256, created_at DESC)'
    )
    c.execute('''CREATE TABLE IF NOT EXISTS product_ledger_row_maps (
      id INTEGER PRIMARY KEY,
      product_map_key TEXT NOT NULL,
      row_map_key TEXT NOT NULL,
      match_group_key TEXT NOT NULL DEFAULT '',
      file_sha256 TEXT NOT NULL DEFAULT '',
      file_name TEXT NOT NULL DEFAULT '',
      source_row_no INTEGER,
      csv_customer TEXT NOT NULL DEFAULT '',
      csv_supplier TEXT NOT NULL DEFAULT '',
      csv_product_code TEXT NOT NULL DEFAULT '',
      csv_product_name TEXT NOT NULL DEFAULT '',
      csv_size TEXT NOT NULL DEFAULT '',
      csv_color TEXT NOT NULL DEFAULT '',
      csv_processing TEXT NOT NULL DEFAULT '',
      csv_material TEXT NOT NULL DEFAULT '',
      is_main_candidate INTEGER NOT NULL DEFAULT 0,
      spec_json TEXT NOT NULL DEFAULT '',
      customer_id INTEGER,
      product_id INTEGER,
      customer_product_id INTEGER,
      run_id INTEGER,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )''')
    # 既存DB向け: 原文仕様カラムを追加（無いときだけ）
    cols = {
        r['name']
        for r in c.execute('PRAGMA table_info(product_ledger_row_maps)').fetchall()
    }
    for col, decl in (
        ('csv_color', "TEXT NOT NULL DEFAULT ''"),
        ('csv_processing', "TEXT NOT NULL DEFAULT ''"),
        ('csv_material', "TEXT NOT NULL DEFAULT ''"),
        ('is_main_candidate', 'INTEGER NOT NULL DEFAULT 0'),
        ('spec_json', "TEXT NOT NULL DEFAULT ''"),
    ):
        if col not in cols:
            c.execute(f'ALTER TABLE product_ledger_row_maps ADD COLUMN {col} {decl}')
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_row_maps_product_key '
        'ON product_ledger_row_maps(product_map_key, id DESC)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_row_maps_row_key '
        'ON product_ledger_row_maps(row_map_key, id DESC)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS product_ledger_row_maps_file '
        'ON product_ledger_row_maps(file_sha256, source_row_no)'
    )


def db_fingerprint(c):
    ensure_schema(c)
    parts = []
    tables = (
        'customers', 'suppliers', 'products', 'product_sizes', 'product_suppliers',
        'customer_products', 'lots', 'product_ledger_row_maps',
    )
    for table in tables:
        exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            parts.append(f'{table}:0:0')
            continue
        row = c.execute(f'SELECT COUNT(*) AS n, COALESCE(MAX(id),0) AS m FROM {table}').fetchone()
        parts.append(f'{table}:{row["n"]}:{row["m"]}')
    lot_sum = c.execute('SELECT COALESCE(SUM(qty_remaining),0) AS q FROM lots').fetchone()['q']
    lot_cost = c.execute(
        'SELECT COALESCE(SUM(qty_remaining * unit_cost),0) AS a FROM lots'
    ).fetchone()['a']
    move_n = c.execute('SELECT COUNT(*) AS n FROM moves').fetchone()['n']
    parts.append(f'qty:{lot_sum}')
    parts.append(f'amount:{lot_cost}')
    parts.append(f'moves:{move_n}')
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:40]


def stock_snapshot(c):
    """取込前後の在庫不変検証用。"""
    row = c.execute(
        '''SELECT COUNT(*) AS lots,
                  COALESCE(SUM(qty_remaining),0) AS qty,
                  COALESCE(SUM(qty_remaining * unit_cost),0) AS amount
           FROM lots'''
    ).fetchone()
    moves = c.execute('SELECT COUNT(*) AS n FROM moves').fetchone()['n']
    return {
        'lots': row['lots'],
        'qty': row['qty'],
        'amount': row['amount'],
        'moves': moves,
    }


def previous_imports_for_file(c, sha):
    ensure_schema(c)
    rows = c.execute(
        '''SELECT id,file_name,file_size,row_count,actor_name,preview_at,committed_at,
                  commit_token,result_json,created_at,backup_path
           FROM product_ledger_import_runs WHERE file_sha256=? ORDER BY id DESC LIMIT 5''',
        (sha,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            result = json.loads(r['result_json'] or '{}')
        except json.JSONDecodeError:
            result = {}
        out.append({
            'id': r['id'],
            'fileName': r['file_name'],
            'fileSize': r['file_size'],
            'rowCount': r['row_count'],
            'actorName': r['actor_name'],
            'previewAt': r['preview_at'],
            'committedAt': r['committed_at'],
            'createdAt': r['created_at'],
            'backupPath': r['backup_path'] or '',
            'result': result if isinstance(result, dict) else {},
        })
    return out


def lookup_row_map(c, *, customer, code, product_name, size, supplier):
    ensure_schema(c)
    key = row_map_key(
        customer=customer, code=code, product_name=product_name, size=size, supplier=supplier,
    )
    return c.execute(
        '''SELECT * FROM product_ledger_row_maps
           WHERE row_map_key=? ORDER BY id DESC LIMIT 1''',
        (key,),
    ).fetchone()


def lookup_product_map(c, *, customer, code, product_name):
    ensure_schema(c)
    key = product_map_key(customer=customer, code=code, product_name=product_name)
    return c.execute(
        '''SELECT * FROM product_ledger_row_maps
           WHERE product_map_key=? ORDER BY id DESC LIMIT 1''',
        (key,),
    ).fetchone()


def upsert_row_map(
    c, *, run_id, file_sha256, file_name, row, customer_id, product_id, customer_product_id,
):
    ensure_schema(c)
    csv_info = row.get('csv') or {}
    customer = stock.text(csv_info.get('customer'))
    supplier = stock.text(csv_info.get('supplier'))
    code = stock.text(csv_info.get('code'))
    product_name = stock.text(csv_info.get('productName'))
    size = stock.text(csv_info.get('size'))
    provenance = row.get('specProvenance') if isinstance(row.get('specProvenance'), dict) else {}
    pc = row.get('parenClassification') if isinstance(row.get('parenClassification'), dict) else {}
    csv_color = stock.text(
        provenance.get('color') or pc.get('color') or ''
    )
    csv_processing = stock.text(
        provenance.get('processing') or pc.get('processing') or ''
    )
    csv_material = stock.text(
        provenance.get('material') or pc.get('material') or ''
    )
    is_main = 1 if provenance.get('isMainCandidate') else 0
    spec_json = json.dumps({
        'originalName': provenance.get('originalName') or product_name,
        'originalSupplier': provenance.get('originalSupplier') or supplier,
        'color': csv_color,
        'processing': csv_processing,
        'material': csv_material,
        'plannedName': provenance.get('plannedName') or pc.get('plannedName') or product_name,
        'isMainCandidate': bool(is_main),
        'auxStockPickAuto': bool(row.get('auxStockPickAuto')),
        'auxStockPickReason': stock.text(row.get('auxStockPickReason') or ''),
    }, ensure_ascii=False)
    pkey = product_map_key(customer=customer, code=code, product_name=product_name)
    rkey = row_map_key(
        customer=customer, code=code, product_name=product_name, size=size, supplier=supplier,
    )
    gkey = row.get('matchGroupKey') or match_group_key(
        customer=customer, code=code, product_name=product_name, supplier=supplier,
    )
    t = stock.stamp()
    existing = c.execute(
        'SELECT id FROM product_ledger_row_maps WHERE row_map_key=? ORDER BY id DESC LIMIT 1',
        (rkey,),
    ).fetchone()
    vals = (
        pkey, rkey, gkey, file_sha256, file_name, row.get('rowNo'),
        customer, supplier, code, product_name, size,
        csv_color, csv_processing, csv_material, is_main, spec_json,
        customer_id, product_id, customer_product_id, run_id, t,
    )
    if existing:
        c.execute(
            '''UPDATE product_ledger_row_maps SET
                 product_map_key=?, row_map_key=?, match_group_key=?, file_sha256=?, file_name=?,
                 source_row_no=?, csv_customer=?, csv_supplier=?, csv_product_code=?,
                 csv_product_name=?, csv_size=?,
                 csv_color=?, csv_processing=?, csv_material=?, is_main_candidate=?, spec_json=?,
                 customer_id=?, product_id=?,
                 customer_product_id=?, run_id=?, updated_at=?
               WHERE id=?''',
            vals + (existing['id'],),
        )
        return existing['id']
    c.execute(
        '''INSERT INTO product_ledger_row_maps(
             product_map_key,row_map_key,match_group_key,file_sha256,file_name,source_row_no,
             csv_customer,csv_supplier,csv_product_code,csv_product_name,csv_size,
             csv_color,csv_processing,csv_material,is_main_candidate,spec_json,
             customer_id,product_id,customer_product_id,run_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        vals + (t,),
    )
    return c.execute('SELECT last_insert_rowid()').fetchone()[0]


def _person_preview(c, table, label, name, row_no):
    return legacy._person_preview(c, table, label, name, row_no)


def _product_candidate(m, c=None):
    return legacy._product_candidate(m, c)


def _load_product(c, product_id):
    if not product_id:
        return None
    live_id = stock_product_merge.resolve_live_product_id(c, product_id)
    return c.execute(
        '''SELECT p.id,p.code,p.name,p.color,p.category,p.supplier_id,p.active,
                  p.merged_into_id,s.name AS supplier_name
           FROM products p LEFT JOIN suppliers s ON s.id=p.supplier_id WHERE p.id=?''',
        (live_id,),
    ).fetchone()


def _finalize_matched_product(
    c, info, prod, name, size_label, supplier_id, *,
    proposed_category, selected_category, apply_category=False,
    allow_name_diff=False,
):
    row_no = info.get('_rowNo')
    merged_into = prod['merged_into_id'] if 'merged_into_id' in prod.keys() else None
    if merged_into not in (None, ''):
        live = _load_product(c, prod['id'])
        if not live:
            info['action'] = 'review'
            info['reasons'].append(f'{row_no}行目：統合先の商品が見つかりません。')
            return info
        prod = live
    if not prod['active']:
        info['action'] = 'review'
        info['id'] = prod['id']
        info['reasons'].append(
            f'{row_no}行目：商品ID {prod["id"]} は停止中です。重複追加・無断再開しません。'
        )
        return info
    if stock.text(prod['name']) != name:
        if names_equivalent_core(prod['name'], name):
            # 標記・空白/全角半角・明示シノニムのみ → 同一商品。原文は別名に残す。
            if names_differ_only_by_used_marker(prod['name'], name):
                info['usedMarkerNameDiff'] = True
            else:
                info['normalizedNameDiff'] = True
        elif not allow_name_diff:
            # 別注・品番なし等: 括弧付き名・加工表記差は自動同一化しない
            info['action'] = 'review'
            info['id'] = prod['id']
            info['candidates'] = [_product_candidate(prod, c)]
            info['nameDiff'] = True
            info['reasons'].append(
                f'{row_no}行目：品番は一致しますが商品名が異なります'
                f'（既存: {prod["name"]} / CSV: {name}）。候補から選ぶか新規にしてください。'
            )
            return info
        else:
            # 通常品番: 名差だけでは別商品にしない。公式名は品番比較UIで確定。
            info['nameDiff'] = True
            info['reasons'].append(
                f'{row_no}行目：品番は一致、商品名が異なります'
                f'（既存: {prod["name"]} / CSV: {name}）。'
                f'同じ商品としてまとめる場合は公式名を選んでください。'
            )
    info['action'] = 'reuse'
    info['id'] = prod['id']
    if supplier_id and prod['supplier_id'] not in (None, supplier_id):
        if stock.product_has_supplier(c, prod['id'], supplier_id):
            info['supplierKept'] = True
            info['subSupplierAdd'] = False
        else:
            info['supplierConflict'] = True
            info['supplierKept'] = True
            info['subSupplierAdd'] = True
            info['subSupplierId'] = supplier_id
    existing_sizes = {
        r['label'] for r in c.execute(
            'SELECT label FROM product_sizes WHERE product_id=?', (prod['id'],)
        )
    }
    if size_label not in existing_sizes:
        info['sizeAction'] = 'add'
    else:
        info['sizeAction'] = 'reuse'
    existing_cat = prod['category'] if 'category' in prod.keys() else ''
    if apply_category:
        legacy._apply_category_decision(
            info, existing_cat, proposed_category, selected_category,
        )
    else:
        info['category'] = existing_cat or ''
        info['proposedCategory'] = existing_cat or ''
        info['categoryAction'] = 'keep'
        info['categoryConflict'] = False
    info['codeDisplay'] = stock.product_code_display(
        info['code'], info.get('category') or proposed_category,
    )
    return info


def _product_preview(
    c, item, supplier_info, row_no, *,
    customer_name='', customer_id=None, selected_category=None,
    mapped_product_id=None, selected_product_id=None, force_create=False,
):
    code = stock.text(item.get('商品コード'))
    # 日付・数値への自動変換はしない（CSVは文字列のまま）
    name_raw = item.get('商品名')
    name = stock.text(name_raw if isinstance(name_raw, str) else ('' if name_raw is None else str(name_raw)))
    size_raw = item.get('サイズ')
    size = size_raw if isinstance(size_raw, str) else ('' if size_raw is None else str(size_raw))
    size_label = size.strip()
    blank_sku = not code
    custom_order = legacy.is_custom_order_code(code)
    star = used_marker_possible(name)
    # 台帳取込の品番空欄は一律「部品・資材」（旧・品番なし制服例外は使わない）。
    # 登録後のカテゴリ変更は選択／編集で可能。
    proposed_category = (
        stock.CATEGORY_PARTS if blank_sku
        else (stock.CATEGORY_UNIFORM if custom_order else '')
    )
    if selected_category is not None and selected_category != '':
        try:
            proposed_category = stock.normalize_category(selected_category) or proposed_category
        except ValueError:
            pass
    info = {
        'code': code, 'name': name, 'size': size_label,
        'action': 'missing', 'id': None, 'candidates': [], 'reasons': [],
        'sizeAction': 'ok', 'nameDiff': False,
        'supplierConflict': False, 'supplierKept': False,
        'subSupplierAdd': False, 'subSupplierId': None,
        'category': proposed_category if (blank_sku or custom_order) else '',
        'proposedCategory': proposed_category if (blank_sku or custom_order) else '',
        'categoryAction': 'set' if (blank_sku or custom_order) else 'keep',
        'categoryConflict': False,
        'blankSku': blank_sku,
        'customOrder': custom_order,
        'usedMarkerPossible': star,
        'fromImportMap': False,
        'codeDisplay': stock.product_code_display(
            code, proposed_category if (blank_sku or custom_order) else '',
        ),
        '_rowNo': row_no,
    }
    if not name and not code:
        info['action'] = 'excluded'
        info['reasons'].append(f'{row_no}行目：商品名も品番もないため対象外です。')
        return info
    if not name:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：商品名が空です。')
        return info
    if not size_label:
        info['action'] = 'review'
        info['reasons'].append(f'{row_no}行目：サイズが空です。')
        return info
    if star:
        info['reasons'].append(
            f'{row_no}行目：商品名先頭の「＊/*」は中古標記です。'
            f'同一品番で標記なし名とだけ違う場合は1商品へ自動まとめし、'
            f'公式名は標記なし、CSV原文は照合用別名に残します'
            f'（台帳取込では在庫数量・新古は作りません）。'
        )

    supplier_id = supplier_info.get('id')

    if force_create:
        info['action'] = 'create'
        info['sizeAction'] = 'add'
        info['id'] = None
        return info

    if selected_product_id:
        prod = _load_product(c, selected_product_id)
        if not prod:
            info['action'] = 'review'
            info['reasons'].append(f'{row_no}行目：選択した商品が見つかりません。')
            return info
        # 選択時は名差・仕入先差を許容し、通常仕入先は変更しない
        if not prod['active']:
            info['action'] = 'review'
            info['id'] = prod['id']
            info['reasons'].append(f'{row_no}行目：選択した商品は停止中です。')
            return info
        info['action'] = 'reuse'
        info['id'] = prod['id']
        if stock.text(prod['name']) != name:
            if names_equivalent_core(prod['name'], name):
                if names_differ_only_by_used_marker(prod['name'], name):
                    info['usedMarkerNameDiff'] = True
                else:
                    info['normalizedNameDiff'] = True
            else:
                info['nameDiff'] = True
        if supplier_id and prod['supplier_id'] not in (None, supplier_id):
            if stock.product_has_supplier(c, prod['id'], supplier_id):
                info['supplierKept'] = True
            else:
                info['supplierConflict'] = True
                info['supplierKept'] = True
                info['subSupplierAdd'] = True
                info['subSupplierId'] = supplier_id
        existing_sizes = {
            r['label'] for r in c.execute(
                'SELECT label FROM product_sizes WHERE product_id=?', (prod['id'],)
            )
        }
        info['sizeAction'] = 'add' if size_label not in existing_sizes else 'reuse'
        info['category'] = prod['category'] or ''
        info['categoryAction'] = 'keep'
        info['codeDisplay'] = stock.product_code_display(code, info['category'])
        return info

    if mapped_product_id:
        prod = _load_product(c, mapped_product_id)
        if prod and prod['active']:
            info['fromImportMap'] = True
            return _finalize_matched_product(
                c, info, prod, name, size_label, supplier_id,
                proposed_category=proposed_category, selected_category=selected_category,
                apply_category=bool(blank_sku or custom_order),
            )

    if blank_sku:
        matches = legacy.find_blank_sku_products(c, name, supplier_id)
        if not matches and supplier_id is not None:
            loose = legacy.find_blank_sku_products(c, name, None)
            if loose:
                info['action'] = 'review'
                info['candidates'] = [_product_candidate(m, c) for m in loose]
                info['reasons'].append(
                    f'{row_no}行目：品番なしの同名商品が仕入先未設定であります。候補から選んでください。'
                )
                return info
        if not matches:
            info['action'] = 'create'
            info['sizeAction'] = 'add'
            if selected_category is not None:
                try:
                    info['category'] = stock.normalize_category(selected_category)
                except ValueError:
                    info['action'] = 'review'
                    info['reasons'].append(
                        f'{row_no}行目：カテゴリ「{selected_category}」は使えません。'
                    )
            info['codeDisplay'] = stock.product_code_display('', info['category'])
            return info
        if len(matches) > 1:
            info['action'] = 'review'
            info['candidates'] = [_product_candidate(m, c) for m in matches]
            info['reasons'].append(
                f'{row_no}行目：品番なしで商品名・仕入先が同じ商品が複数あります。'
            )
            return info
        return _finalize_matched_product(
            c, info, matches[0], name, size_label, supplier_id,
            proposed_category=proposed_category, selected_category=selected_category,
            apply_category=True,
        )

    if custom_order:
        info['category'] = proposed_category or stock.CATEGORY_UNIFORM
        info['proposedCategory'] = info['category']
        info['categoryAction'] = 'set'
        info['codeDisplay'] = stock.product_code_display(code, info['category'])
        matches = legacy.find_custom_order_products_for_customer(
            c, name, customer_id=customer_id,
        )
        if not matches:
            info['action'] = 'create'
            info['sizeAction'] = 'add'
            return info
        if len(matches) > 1:
            info['action'] = 'review'
            info['candidates'] = [_product_candidate(m, c) for m in matches]
            info['reasons'].append(
                f'{row_no}行目：品番「別注」で顧客・商品名が同じ商品が複数あります。'
            )
            return info
        return _finalize_matched_product(
            c, info, matches[0], name, size_label, supplier_id,
            proposed_category=info['category'], selected_category=selected_category,
            apply_category=True,
        )

    # 通常品番: 1品番=1商品。名差・仕入先差だけでは新規を作らない。
    matches = stock_product_merge.find_live_products_by_code(c, code)
    if not matches:
        # 旧商品名別名でも探す（再取込で重複新規を防ぐ）
        matches = stock_product_merge.find_products_by_name_alias(c, code, name)
    if not matches:
        info['action'] = 'create'
        info['sizeAction'] = 'add'
        return info

    if len(matches) > 1:
        # 先頭へ自動紐づけしない。品番比較UIで残す商品・統合を確認。
        info['action'] = 'review'
        info['candidates'] = [_product_candidate(m, c) for m in matches]
        info['existingDuplicates'] = True
        info['reasons'].append(
            f'{row_no}行目：品番「{code}」の登録商品が {len(matches)} 件あります。'
            f'先頭へ自動では紐づけません。品番ごとの比較で残す商品を選び、'
            f'必要なら統合してから登録してください。'
        )
        return info

    return _finalize_matched_product(
        c, info, matches[0], name, size_label, supplier_id,
        proposed_category='', selected_category=None, apply_category=False,
        allow_name_diff=True,
    )


def _handle_preview(c, customer_id, product_id, row_no, *, csv_source=None):
    return legacy._handle_preview(c, customer_id, product_id, row_no, csv_source=csv_source)


def _excluded_row(row_no, item, *, reason):
    customer = stock.text(item.get('顧客'))
    supplier = stock.text(item.get('仕入先'))
    code = stock.text(item.get('商品コード'))
    name = stock.text(item.get('商品名'))
    size_raw = item.get('サイズ')
    size = size_raw if isinstance(size_raw, str) else ('' if size_raw is None else str(size_raw))
    return {
        'rowNo': row_no,
        'status': STATUS_EXCLUDED,
        'statusLabel': STATUS_LABELS[STATUS_EXCLUDED],
        'matchGroupKey': match_group_key(
            customer=customer, code=code, product_name=name, supplier=supplier,
        ),
        'csv': {
            'customer': customer,
            'supplier': supplier,
            'code': code,
            'codeDisplay': stock.product_code_display(code, ''),
            'productName': name,
            'size': size.strip(),
            'usedMarkerPossible': used_marker_possible(name),
        },
        'customer': {'action': 'excluded', 'id': None, 'candidates': [], 'reasons': []},
        'supplier': {'action': 'excluded', 'id': None, 'candidates': [], 'reasons': []},
        'product': {
            'action': 'excluded', 'id': None, 'code': code, 'name': name,
            'size': size.strip(), 'candidates': [], 'reasons': [reason],
            'sizeAction': 'ok',
        },
        'handle': {'action': 'excluded', 'id': None, 'candidates': [], 'reasons': []},
        'reasons': [reason],
        'canCommit': False,
    }


def build_paren_classification(product_name, override=None):
    """括弧分類＋ユーザー上書き（行編集はグローバル辞書へ昇格しない）。"""
    base = paren_classify.classify_product_name(product_name)
    raw = override if isinstance(override, dict) else {}
    user_touched = bool(raw.get('userEdited') or raw.get('manual'))
    planned = stock.text(raw.get('plannedName')) if 'plannedName' in raw else base['plannedName']
    color = stock.text(raw.get('color')) if 'color' in raw else base['color']
    processing = stock.text(raw.get('processing')) if 'processing' in raw else base['processing']
    material = stock.text(raw.get('material')) if 'material' in raw else base['material']
    # 未分類の手編集はリストで受けてもよい
    if 'unclassified' in raw:
        unc = raw.get('unclassified')
        if isinstance(unc, list):
            unclassified = [stock.text(x) for x in unc if stock.text(x)]
        else:
            unclassified = [stock.text(unc)] if stock.text(unc) else []
    else:
        unclassified = list(base.get('unclassified') or [])

    needs = bool(base.get('needsConfirm'))
    reasons = list(base.get('confirmReasons') or [])
    if user_touched:
        # 手動確定後は要確認を外せる（ユーザーが内容を編集した）
        if raw.get('confirmed') or raw.get('acceptProposal'):
            needs = False
            reasons = []
        elif not raw.get('confirmed') and (unclassified or base.get('needsConfirm')):
            needs = True
    # 曖昧なまま自動確定しない
    auto = bool(base.get('autoClassified')) and not needs and not user_touched
    if user_touched and not needs:
        auto = False  # 手動

    decision = 'needs_review' if needs else ('auto' if (auto or user_touched) else 'passthrough')
    return {
        'originalName': base['originalName'],
        'plannedName': planned or base['originalName'],
        'baseName': base.get('baseName') or base['originalName'],
        'color': color,
        'processing': processing,
        'material': material,
        'unclassified': unclassified,
        'needsConfirm': needs,
        'confirmReasons': reasons,
        'autoClassified': auto and not user_touched,
        'userEdited': user_touched,
        'decision': decision,
        'parenContents': list(base.get('parenContents') or []),
        'segments': list(base.get('segments') or []),
        'keptOriginal': bool(
            stock.text(planned or '') == stock.text(base['originalName'])
            or base.get('keptOriginal')
        ),
        'colorCandidates': list(base.get('colorCandidates') or []),
        'processingCandidates': list(base.get('processingCandidates') or []),
        'materialCandidates': list(base.get('materialCandidates') or []),
    }


def attach_paren_classification(row, override=None):
    """行へ括弧分類を付け、色・加工・生地の登録予定を反映する。"""
    csv = dict(row.get('csv') or {})
    original = stock.text(csv.get('productName') or '')
    plan = build_paren_classification(original, override)
    row['parenClassification'] = plan

    prod = dict(row.get('product') or {})
    handle = dict(row.get('handle') or {})

    # 予定名（括弧分類後）。原文は別名／マッピング用に残す。
    # 別注・品番なしは顧客×原文名で区別するため、自動では予定名へ置き換えない
    # （色・加工・生地フィールドへの抽出は行う）。
    code = stock.text(csv.get('code') or '')
    keep_original_name = (
        legacy.is_custom_order_code(code)
        or not stock.text(code)
        or code == stock_product_merge.UNKNOWN_CODE
    )
    if plan.get('plannedName'):
        prod['csvOriginalName'] = original
        if plan['plannedName'] != original:
            prod['plannedNameFromParen'] = plan['plannedName']
        if (
            not keep_original_name
            and (plan.get('autoClassified') or (plan.get('userEdited') and not plan.get('needsConfirm')))
        ):
            prod['suggestedNameFromParen'] = plan['plannedName']
            # 代表名未設定なら括弧分類後の名を公式名候補に（SKUまとめ後も優先）
            if not prod.get('officialName') or prod.get('officialName') == original:
                prod['officialName'] = plan['plannedName']
                prod['applyOfficialName'] = True

    if plan.get('color'):
        prod['proposedColor'] = plan['color']
        if not prod.get('officialColor'):
            prod['officialColor'] = plan['color']
    if plan.get('material'):
        prod['proposedMaterial'] = plan['material']
        prod['officialMaterial'] = plan['material']

    # 顧客別加工 → 取扱の加工仕様（不明時は「加工なし」へ強制しない）
    if plan.get('processing'):
        handle['proposedEmbroidery'] = plan['processing']
        if handle.get('action') == 'create' or handle.get('embroidery') in (
            '', None, PENDING_EMBROIDERY,
        ):
            handle['embroidery'] = plan['processing']
            handle['embroideryLabel'] = plan['processing']
            handle['pendingEmbroiderySort'] = False
            handle['fromParenClassification'] = True

    if plan.get('needsConfirm'):
        row['reasons'] = list(row.get('reasons') or []) + list(plan.get('confirmReasons') or [])
        # 未解決の曖昧さは自動確定しない（行を要確認へ。SKU未決とは独立）
        if row.get('status') not in (STATUS_EXCLUDED, STATUS_HOLD) and not (
            plan.get('userEdited') and not plan.get('needsConfirm')
        ):
            # 括弧のみの要確認は status を needs_review に（コミット不可）
            if row.get('status') in (STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD, STATUS_UNCHANGED):
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                row['parenNeedsConfirm'] = True

    row['product'] = prod
    row['handle'] = handle
    return row


def _apply_paren_names_after_sku_plans(classified, sku_groups):
    """括弧分類で得た予定名を、SKU代表名より優先して商品計画へ反映する。

    ただしユーザー指定の公式名・既存維持は上書きしない。
    別注・品番なしは原文名を維持（顧客×原文名の区別を壊さない）。
    """
    by_code = {g['code']: g for g in sku_groups}
    for row in classified:
        pc = row.get('parenClassification') or {}
        if not (pc.get('autoClassified') or (pc.get('userEdited') and not pc.get('needsConfirm'))):
            continue
        planned = stock.text(pc.get('plannedName') or '')
        if not planned:
            continue
        code = stock.text((row.get('csv') or {}).get('code') or '')
        keep_original_name = (
            legacy.is_custom_order_code(code)
            or not stock.text(code)
            or code == stock_product_merge.UNKNOWN_CODE
        )
        prod = dict(row.get('product') or {})
        original = stock.text((row.get('csv') or {}).get('productName') or '')
        if not keep_original_name:
            # SKUまとめの officialName が原文のままなら置き換え
            cur = stock.text(prod.get('officialName') or '')
            if not cur or cur == original:
                prod['officialName'] = planned
                prod['applyOfficialName'] = True
                prod['suggestedNameFromParen'] = planned
                prod['csvOriginalName'] = original
        if pc.get('color') and not prod.get('officialColor'):
            prod['officialColor'] = pc['color']
        if pc.get('material'):
            prod['officialMaterial'] = pc['material']
            prod['proposedMaterial'] = pc['material']
        row['product'] = prod
        group = by_code.get(code)
        if (
            group
            and group.get('plannedSingleProduct')
            and not keep_original_name
        ):
            plan = dict(group.get('productPlan') or {})
            if stock.text(plan.get('productName') or '') in ('', original):
                plan['productName'] = planned
                group['productPlan'] = plan
                group['plannedOfficialName'] = planned


def classify_row(
    c, row_no, item, *,
    selected_product_id=None, selected_handle_id=None, selected_category=None,
    force_create_product=False,
    paren_override=None,
):
    customer_name = stock.text(item.get('顧客'))
    supplier_name = stock.text(item.get('仕入先'))
    code = stock.text(item.get('商品コード'))
    product_name = stock.text(item.get('商品名'))
    size_raw = item.get('サイズ')
    size = size_raw if isinstance(size_raw, str) else ('' if size_raw is None else str(size_raw))
    size_label = size.strip()
    group_key = match_group_key(
        customer=customer_name, code=code, product_name=product_name, supplier=supplier_name,
    )
    reasons = []

    if not customer_name and not product_name and not code:
        return _excluded_row(row_no, item, reason=f'{row_no}行目：顧客・商品名・品番が空です。')

    if not customer_name:
        # 顧客なしは取扱を作れない
        cust = {
            'action': 'review', 'id': None, 'name': '', 'candidates': [],
            'reasons': [f'{row_no}行目：顧客が空です。'],
        }
    else:
        cust = _person_preview(c, 'customers', '顧客', customer_name, row_no)

    if supplier_name:
        supp = _person_preview(c, 'suppliers', '仕入先', supplier_name, row_no)
    else:
        supp = {
            'action': 'missing', 'id': None, 'name': '', 'candidates': [],
            'reasons': [f'{row_no}行目：仕入先が空です（未設定のまま登録できます）。'],
        }

    mapped = lookup_row_map(
        c, customer=customer_name, code=code, product_name=product_name,
        size=size_label, supplier=supplier_name,
    )
    mapped_product_id = None
    mapped_handle_id = None
    if mapped:
        mapped_product_id = mapped['product_id']
        mapped_handle_id = mapped['customer_product_id']
    else:
        pm = lookup_product_map(c, customer=customer_name, code=code, product_name=product_name)
        if pm:
            mapped_product_id = pm['product_id']

    product = _product_preview(
        c, item, supp, row_no,
        customer_name=customer_name,
        customer_id=cust.get('id'),
        selected_category=selected_category,
        mapped_product_id=mapped_product_id,
        selected_product_id=selected_product_id,
        force_create=force_create_product,
    )

    handle = {'action': 'defer', 'id': None, 'candidates': [], 'reasons': [], 'embroidery': PENDING_EMBROIDERY}
    if cust.get('action') == 'review' or product.get('action') in ('review', 'excluded', 'missing'):
        if product.get('action') == 'excluded':
            handle = {'action': 'excluded', 'id': None, 'candidates': [], 'reasons': []}
        else:
            handle = {
                'action': 'defer', 'id': None, 'candidates': [], 'reasons': [],
                'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
            }
    elif selected_handle_id:
        row = c.execute(
            'SELECT id,embroidery,active FROM customer_products WHERE id=?',
            (selected_handle_id,),
        ).fetchone()
        if row and row['active']:
            handle = legacy._reuse_handle(
                {
                    'action': 'reuse', 'id': None, 'candidates': [], 'reasons': [],
                    'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
                    'pendingEmbroiderySort': True, 'csvSource': {},
                },
                row, pending_sort=True,
            )
        else:
            handle = {
                'action': 'review', 'id': None, 'candidates': [],
                'reasons': [f'{row_no}行目：選択した取扱が使えません。'],
                'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
            }
    elif mapped_handle_id and product.get('id'):
        row = c.execute(
            'SELECT id,embroidery,active,customer_id,product_id FROM customer_products WHERE id=?',
            (mapped_handle_id,),
        ).fetchone()
        live_pid = product['id']
        if row and row['active'] and row['product_id'] == live_pid and (
            not cust.get('id') or row['customer_id'] == cust['id']
        ):
            handle = legacy._reuse_handle(
                {
                    'action': 'reuse', 'id': None, 'candidates': [], 'reasons': [],
                    'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
                    'pendingEmbroiderySort': True, 'csvSource': {},
                },
                row, pending_sort=True,
            )
        else:
            handle = _handle_preview(
                c, cust.get('id'), product.get('id'), row_no,
                csv_source={
                    'sourceRowNo': row_no, 'productCode': code, 'productName': product_name,
                    'supplier': supplier_name, 'customer': customer_name,
                    'usedMarker': used_marker_possible(product_name),
                },
            )
    elif product.get('id') and cust.get('id'):
        handle = _handle_preview(
            c, cust.get('id'), product.get('id'), row_no,
            csv_source={
                'sourceRowNo': row_no, 'productCode': code, 'productName': product_name,
                'supplier': supplier_name, 'customer': customer_name,
                'usedMarker': used_marker_possible(product_name),
            },
        )
    elif product.get('action') == 'create' and cust.get('action') in ('create', 'reuse'):
        handle = {
            'action': 'create', 'id': None, 'candidates': [], 'reasons': [],
            'embroidery': PENDING_EMBROIDERY, 'embroideryLabel': PENDING_EMBROIDERY,
            'pendingEmbroiderySort': True,
        }

    for block in (cust, supp, product, handle):
        reasons.extend(block.get('reasons') or [])

    # ステータス集約（要確認・対象外を優先）
    if product.get('action') == 'excluded':
        status = STATUS_EXCLUDED
    elif (
        cust.get('action') == 'review'
        or product.get('action') == 'review'
        or handle.get('action') == 'review'
        or (supplier_name and supp.get('action') == 'review')
    ):
        status = STATUS_REVIEW
    elif (
        product.get('action') == 'create'
        or handle.get('action') == 'create'
        or cust.get('action') == 'create'
        or (supplier_name and supp.get('action') == 'create')
        or product.get('subSupplierAdd')
    ):
        status = STATUS_CREATE
    elif (
        product.get('action') == 'reuse'
        and handle.get('action') == 'reuse'
        and product.get('sizeAction') == 'add'
    ):
        status = STATUS_SIZE_ADD
    elif (
        product.get('action') == 'reuse'
        and handle.get('action') == 'reuse'
        and product.get('sizeAction') == 'reuse'
        and not product.get('subSupplierAdd')
        and cust.get('action') == 'reuse'
        and (not supplier_name or supp.get('action') in ('reuse', 'missing'))
    ):
        status = STATUS_UNCHANGED
    else:
        status = STATUS_REUSE

    can_commit = status in (STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD)
    row = {
        'rowNo': row_no,
        'status': status,
        'statusLabel': STATUS_LABELS[status],
        'matchGroupKey': group_key,
        'csv': {
            'customer': customer_name,
            'supplier': supplier_name,
            'code': code,
            'codeDisplay': product.get('codeDisplay') or stock.product_code_display(code, product.get('category') or ''),
            'productName': product_name,
            'size': size_label,
            'usedMarkerPossible': used_marker_possible(product_name),
            'category': product.get('category') or '',
        },
        'customer': cust,
        'supplier': supp,
        'product': {k: v for k, v in product.items() if not str(k).startswith('_')},
        'handle': handle,
        'reasons': reasons,
        'canCommit': can_commit,
        'fromImportMap': bool(product.get('fromImportMap')),
    }
    return attach_paren_classification(row, paren_override)


def _row_variant(row):
    csv = row.get('csv') or {}
    name = csv.get('productName') or ''
    return {
        'key': sku_variant_key(
            product_name=name,
            color='',
            supplier=csv.get('supplier') or '',
        ),
        'productName': name,
        'productNameCore': prefer_unmarked_product_name([name]) or name,
        'supplier': csv.get('supplier') or '',
        'color': '',
        'rowNos': [],
        'sizes': [],
        'customers': [],
        'csvNames': [],
    }


def _sku_product_plan(group):
    """サイズ・顧客差の自動まとめ／比較用の商品計画サマリ。"""
    variants = group.get('variants') or []
    all_sizes = []
    all_customers = []
    all_csv_names = []
    for v in variants:
        for size in v.get('sizes') or []:
            if size and size not in all_sizes:
                all_sizes.append(size)
        for cust in v.get('customers') or []:
            if cust and cust not in all_customers:
                all_customers.append(cust)
        for n in v.get('csvNames') or ([v.get('productName')] if v.get('productName') else []):
            if n and n not in all_csv_names:
                all_csv_names.append(n)
    primary = variants[0] if variants else {}
    official = prefer_unmarked_product_name(all_csv_names) or (primary.get('productName') or '')
    # 既存1件で標記差だけのときは、標記なし公式名を優先（既存が標記付きなら書き換え候補）
    if group.get('existingCount') == 1 and not group.get('needsDecision'):
        existing_name = (group.get('existingProducts') or [{}])[0].get('name') or ''
        official = prefer_unmarked_product_name([*all_csv_names, existing_name]) or official
    if group.get('existingCount') == 1 and not group.get('needsDecision'):
        plan_kind = 'reuse'
        plan_label = '既存商品を使用'
    elif group.get('existingCount') > 1:
        plan_kind = 'compare'
        plan_label = '品番比較が必要'
    elif group.get('needsDecision'):
        plan_kind = 'compare'
        plan_label = '品番比較が必要'
    else:
        plan_kind = 'create'
        plan_label = '新規商品'
    return {
        'kind': plan_kind,
        'label': plan_label,
        'code': group.get('code') or '',
        'productName': official,
        'supplier': primary.get('supplier') or '',
        'customers': all_customers,
        'sizes': all_sizes,
        'sizeCount': len(all_sizes),
        'customerCount': len(all_customers),
        'csvRowCount': group.get('rowCount') or 0,
        'variantCount': group.get('variantCount') or 0,
        'csvNames': all_csv_names,
        'usedMarkerAutoMerged': bool(group.get('usedMarkerAutoMerged')),
        'synonymAutoMerged': bool(group.get('synonymAutoMerged')),
        'whitespaceAutoMerged': bool(group.get('whitespaceAutoMerged')),
        'autoMergeReason': (
            ((group.get('reviewReasons') or [{}])[0].get('label') or '')
            if group.get('plannedSingleProduct') else ''
        ),
    }


def build_sku_groups(c, classified):
    """通常品番の比較グループを作る。

    品番+商品名+仕入先が同じでサイズ／顧客だけの差 → 自動で1商品（needsDecision=False）。
    先頭中古標記「＊/*」だけの商品名差も自動まとめ（標記なしを公式名）。
    商品名・仕入先・色・既存重複など判断が必要な差だけ品番比較UIへ。
    既存DBに同一品番が複数ある場合は自動まとめしない（重複整理の確認フローへ）。
    """
    by_code = defaultdict(list)
    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code'))
        if stock_product_merge.is_normal_product_code(code):
            by_code[code].append(row)

    groups = []
    for code in sorted(by_code.keys()):
        rows = by_code[code]
        variants_map = {}
        for row in rows:
            v = _row_variant(row)
            key = v['key']
            if key not in variants_map:
                variants_map[key] = v
            slot = variants_map[key]
            slot['rowNos'].append(row['rowNo'])
            csv_name = (row.get('csv') or {}).get('productName') or ''
            if csv_name and csv_name not in slot['csvNames']:
                slot['csvNames'].append(csv_name)
            # 表示用は標記なしを優先（バリアント代表名）
            if csv_name and (
                not slot['productName']
                or (
                    used_marker_possible(slot['productName'])
                    and not used_marker_possible(csv_name)
                )
            ):
                slot['productName'] = csv_name
            size = (row.get('csv') or {}).get('size') or ''
            if size and size not in slot['sizes']:
                slot['sizes'].append(size)
            cust = (row.get('csv') or {}).get('customer') or ''
            if cust and cust not in slot['customers']:
                slot['customers'].append(cust)

        variants = list(variants_map.values())
        for v in variants:
            v['productName'] = prefer_unmarked_product_name(v.get('csvNames') or [v.get('productName')]) or v.get('productName') or ''
        existing = stock_product_merge.find_live_products_by_code(c, code)
        existing_cards = [_product_candidate(m, c) for m in existing]
        existing_cores = {product_name_core_key(m['name']) for m in existing}
        csv_cores = {product_name_core_key(n) for v in variants for n in (v.get('csvNames') or [v.get('productName') or '']) if n}
        # コア名が既存と一致しないときだけ名差（標記だけの差は一致扱い）
        name_mismatch = bool(existing) and bool(csv_cores - existing_cores)
        # 別名ヒットのみでも公式名差として扱う（中古標記だけの差は除外）
        for row in rows:
            if row.get('product', {}).get('nameDiff'):
                name_mismatch = True
                break

        used_marker_auto = False
        synonym_auto = False
        whitespace_auto = False
        all_csv_names = []
        for v in variants:
            for n in v.get('csvNames') or []:
                if n and n not in all_csv_names:
                    all_csv_names.append(n)
        cores = {product_name_core_key(n) for n in all_csv_names if n}
        raw_names = {stock.text(n) for n in all_csv_names if stock.text(n)}
        if len(cores) == 1 and any(used_marker_possible(n) for n in all_csv_names):
            used_marker_auto = len(raw_names) > 1 or any(
                used_marker_possible(n) for n in all_csv_names
            )
        if len(cores) == 1 and len(raw_names) > 1:
            # 標記以外の表記ゆれ（空白・全角半角・明示シノニム）を区別
            import unicodedata
            cores_no_syn = set()
            for n in all_csv_names:
                if not stock.text(n):
                    continue
                core, _ = legacy.strip_used_marker(n)
                core = unicodedata.normalize('NFKC', stock.text(core))
                cores_no_syn.add(identity_match_key(core))
            if len(cores_no_syn) > 1:
                # NFKC+空白だけでは不一致 → 明示シノニムが効いて同一化した
                synonym_auto = True
            else:
                unmarked_set = {
                    stock.text(legacy.strip_used_marker(n)[0]) for n in all_csv_names if stock.text(n)
                }
                if len(unmarked_set) > 1:
                    whitespace_auto = True

        # サイズ・顧客・中古標記・空白/全角半角・明示シノニムだけの差はバリアントに出ない
        needs_decision = (
            len(variants) > 1
            or len(existing) > 1
            or name_mismatch
        )
        planned_single = not needs_decision

        if needs_decision:
            diff_meta = analyze_sku_review_diffs(
                variants, existing_count=len(existing), name_mismatch=name_mismatch,
            )
        else:
            auto_bits = ['サイズ', '顧客']
            if used_marker_auto:
                auto_bits.append('先頭「＊/*」中古標記')
            if synonym_auto:
                auto_bits.append('明示シノニム（例: パンツ／スラックス）')
            if whitespace_auto:
                auto_bits.append('空白・全角半角の表記差')
            diff_meta = {
                'reviewTier': REVIEW_TIER_AUTO,
                'reviewTierLabel': REVIEW_TIER_LABELS[REVIEW_TIER_AUTO],
                'reviewReasons': [{
                    'code': 'auto',
                    'label': '・'.join(auto_bits) + 'のみの差（自動まとめ）',
                    'values': list(all_csv_names),
                }],
                'nameCandidates': all_csv_names,
                'supplierCandidates': [
                    stock.text(v.get('supplier') or '')
                    for v in variants
                    if stock.text(v.get('supplier') or '')
                ],
                'otherDiffs': [],
                'nameWordingDiff': False,
                'supplierDiff': False,
                'colorDiffPossible': False,
                'processingDiffPossible': False,
                'kindDiffPossible': False,
                'genderDiffPossible': False,
                'fabricDiffPossible': False,
                'bulkApprovable': False,
            }

        reason_labels = [r.get('label') or '' for r in (diff_meta.get('reviewReasons') or []) if r.get('label')]
        apply_scope = (
            f'品番「{code}」の対象行 {len(rows)} 件'
            f'（候補名 {len(diff_meta.get("nameCandidates") or [])} / '
            f'仕入先候補 {len(diff_meta.get("supplierCandidates") or [])} / '
            f'登録済 {len(existing)} 件）。'
            f'サイズ・顧客・先頭「＊/*」・空白/全角半角・明示シノニムだけの差は同じ商品へまとめます。'
            f'加工仕様の違いは取扱として別管理し、商品を勝手に1つへ潰しません。'
        )
        if reason_labels:
            apply_scope += ' 要確認理由: ' + '／'.join(reason_labels)

        group = {
            'code': code,
            'codeDisplay': code,
            'rowNos': [r['rowNo'] for r in rows],
            'rowCount': len(rows),
            'variants': variants,
            'variantCount': len(variants),
            'existingProducts': existing_cards,
            'existingCount': len(existing),
            'needsDecision': needs_decision,
            'plannedSingleProduct': planned_single,
            'usedMarkerAutoMerged': bool(used_marker_auto and planned_single),
            'synonymAutoMerged': bool(synonym_auto and planned_single),
            'whitespaceAutoMerged': bool(whitespace_auto and planned_single),
            'applyScope': apply_scope,
            'decision': None,
            'status': 'needs_decision' if needs_decision else 'auto',
            **diff_meta,
        }
        # 仮代表名の予定（決定前でも画面表示用）
        group['namePlan'] = plan_provisional_official_name(group, None)
        group['productPlan'] = _sku_product_plan(group)
        groups.append(group)
    return groups


def _attach_product_plans(classified, sku_groups):
    """各行に商品計画（品番単位のまとめ先）を付け、行トレースを残す。"""
    by_code = {g['code']: g for g in sku_groups}
    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code'))
        group = by_code.get(code)
        if not group:
            continue
        plan = dict(group.get('productPlan') or {})
        row['productPlan'] = plan
        row['skuAutoMerged'] = bool(group.get('plannedSingleProduct'))
        row['usedMarkerAutoMerged'] = bool(group.get('usedMarkerAutoMerged'))
        row['synonymAutoMerged'] = bool(group.get('synonymAutoMerged'))
        row['whitespaceAutoMerged'] = bool(group.get('whitespaceAutoMerged'))
        row['skuRowNos'] = list(group.get('rowNos') or [])


def _apply_auto_merge_official_names(classified, sku_groups):
    """自動まとめ品番に公式名（標記なし優先）を付け、CSV原文を別名登録できるようにする。"""
    by_code = {g['code']: g for g in sku_groups}
    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code'))
        group = by_code.get(code)
        if not group or not group.get('plannedSingleProduct'):
            continue
        plan = group.get('productPlan') or {}
        official = stock.text(plan.get('productName') or '')
        if not official:
            continue
        prod = dict(row.get('product') or {})
        prod['officialName'] = official
        csv_name = stock.text((row.get('csv') or {}).get('productName') or '')
        if (
            group.get('usedMarkerAutoMerged')
            or group.get('synonymAutoMerged')
            or group.get('whitespaceAutoMerged')
            or used_marker_possible(csv_name)
            or (
                prod.get('id')
                and stock.text(prod.get('name') or '') != official
                and names_equivalent_core(prod.get('name'), official)
            )
            or (
                csv_name
                and stock.text(csv_name) != official
                and names_equivalent_core(csv_name, official)
            )
        ):
            prod['applyOfficialName'] = True
        row['product'] = prod


def _apply_sku_decisions(c, classified, sku_groups, sku_decisions):
    """品番決定を行ステータスへ反映。

    未決定でもサイズ行を要確認へ流し込まない。品番比較パネルで止め、
    行ステータスは新規／既存使用／サイズ追加のまま残す。
    """
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    group_by_code = {g['code']: g for g in sku_groups}
    hold_count = 0
    pending_decision = 0

    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code'))
        if not stock_product_merge.is_normal_product_code(code):
            continue
        group = group_by_code.get(code)
        if not group:
            continue
        raw = decisions.get(code) or decisions.get(str(code)) or {}
        if not isinstance(raw, dict):
            raw = {}
        decision = stock.text(raw.get('decision'))
        group['decision'] = raw if decision else None

        if not group['needsDecision']:
            continue

        if not decision:
            pending_decision += 1
            # サイズ・顧客だけの行差を要確認一覧へ溢れさせない
            row['canCommit'] = False
            row['skuDecisionNeeded'] = True
            row['reasons'] = list(row.get('reasons') or []) + [
                f'品番「{code}」の比較で【同じ商品としてまとめる】'
                f'【別物のため個別に整理する】【今回は保留する】のいずれかを選んでください。'
            ]
            continue

        row['skuDecision'] = decision
        row['skuApplyScope'] = group['applyScope']

        if decision == SKU_DECISION_HOLD:
            row['status'] = STATUS_HOLD
            row['statusLabel'] = STATUS_LABELS[STATUS_HOLD]
            row['canCommit'] = False
            row['deferReason'] = stock.text(raw.get('deferReason') or raw.get('reason') or '')
            hold_count += 1
            continue

        if decision == SKU_DECISION_MERGE:
            keep_id = raw.get('keepProductId') or raw.get('productId')
            # 既存が1件だけのときは残す商品を省略しても再利用（再取込の二重新規を防ぐ）
            if not keep_id and group.get('existingCount') == 1:
                keep_id = (group.get('existingProducts') or [{}])[0].get('id')
            color = stock.text(raw.get('color') or '')
            primary_supplier, primary_source, primary_conflict, auto_primary, auto_reason = (
                _resolve_effective_primary_supplier(group, raw)
            )
            aux = group.get('auxPrimary') or {}
            # 公式名未指定でも仮代表名で登録可（既存公式名は上書きしない）
            name_plan = plan_provisional_official_name(group, {**raw, 'keepProductId': keep_id})
            group['namePlan'] = name_plan
            official_name = stock.text(name_plan.get('officialName') or '')
            provisional = bool(name_plan.get('provisional'))
            merge_source_ids = raw.get('mergeSourceIds') or []
            if isinstance(merge_source_ids, list):
                merge_source_ids = [int(x) for x in merge_source_ids if str(x).isdigit() or isinstance(x, int)]
            else:
                merge_source_ids = []

            # グループ表示用（UIの確認パネル）
            existing_primary = _existing_primary_supplier_name(group, keep_id)
            primary_change = bool(
                keep_id
                and primary_supplier
                and existing_primary
                and not _supplier_names_match(existing_primary, primary_supplier)
            )
            confirm_primary = bool(raw.get('confirmPrimaryChange'))
            group['primarySupplierResolved'] = {
                'primarySupplier': primary_supplier,
                'source': primary_source,
                'autoPrimarySupplier': auto_primary,
                'autoReason': auto_reason,
                'conflictWithAuto': primary_conflict,
                'existingPrimarySupplier': existing_primary,
                'primaryChangeRequiresConfirm': primary_change,
                'primaryChangeConfirmed': confirm_primary,
            }
            group['plannedOfficialName'] = official_name
            group['plannedPrimarySupplier'] = primary_supplier
            group['plannedPrimarySupplierReason'] = (
                auto_reason if primary_source in ('auto', 'single') and primary_supplier else ''
            )
            group['plannedPrimarySupplierSource'] = primary_source
            group['provisionalName'] = provisional
            group['nameAdoptionRule'] = name_plan.get('adoptionRule') or ''

            if group['existingCount'] > 1 and not keep_id:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の既存商品が複数あります。残す商品IDを選んでください。'
                ]
                pending_decision += 1
                continue
            if not official_name:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の代表商品名を決められませんでした。'
                ]
                pending_decision += 1
                continue

            suppliers_in_group = {
                stock.text(v.get('supplier') or '')
                for v in (group.get('variants') or [])
                if stock.text(v.get('supplier') or '')
            }
            if len(suppliers_in_group) > 1 and not primary_supplier:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                why = (aux.get('manualReason') if aux else '') or '通常仕入先を選んでください。'
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の通常仕入先が未定です。{why}'
                ]
                pending_decision += 1
                continue
            if primary_change and not confirm_primary:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の既存の通常仕入先は「{existing_primary}」です。'
                    f'「{primary_supplier}」へ変更するには明示確認が必要です（黙って変更しません）。'
                ]
                pending_decision += 1
                continue

            # 行ごとの登録先
            # 既存商品へ仮代表名を適用しない（公式名の黙った上書き禁止）
            apply_official = bool(official_name) and not (
                keep_id and name_plan.get('keepsExistingOfficialName')
            )
            # ユーザーが明示した公式名は既存にも適用可。仮名は新規のみ。
            user_set_name = bool(stock.text(raw.get('officialName') or raw.get('name') or ''))
            if keep_id and provisional and not user_set_name:
                apply_official = False
            elif keep_id and name_plan.get('keepsExistingOfficialName') and not user_set_name:
                apply_official = False

            if keep_id:
                row['product'] = dict(row.get('product') or {})
                row['product']['id'] = int(keep_id)
                row['product']['action'] = 'reuse'
                row['product']['officialName'] = official_name
                row['product']['officialColor'] = color
                row['product']['primarySupplierName'] = primary_supplier
                row['product']['confirmPrimaryChange'] = bool(primary_change and confirm_primary)
                row['product']['mergeSourceIds'] = merge_source_ids
                row['product']['skuMerge'] = True
                row['product']['provisionalName'] = provisional
                row['product']['applyOfficialName'] = apply_official
                row['product']['nameMappings'] = name_plan.get('nameMappings') or []
                row['product']['nameAdoptionRule'] = name_plan.get('adoptionRule') or ''
                if group['existingCount'] > 1 and merge_source_ids:
                    row['status'] = STATUS_MERGE
                    row['statusLabel'] = STATUS_LABELS[STATUS_MERGE]
                elif row.get('product', {}).get('sizeAction') == 'add':
                    row['status'] = STATUS_SIZE_ADD
                    row['statusLabel'] = STATUS_LABELS[STATUS_SIZE_ADD]
                else:
                    # 再分類: 取扱新規なら create/reuse
                    # 名差のまとめ・別名登録がある行は UNCHANGED にせずコミット対象へ
                    if row.get('handle', {}).get('action') == 'create':
                        row['status'] = STATUS_CREATE
                        row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                    elif (
                        row.get('handle', {}).get('action') == 'reuse'
                        and row.get('product', {}).get('sizeAction') == 'reuse'
                        and not row.get('product', {}).get('nameDiff')
                        and not row.get('product', {}).get('skuMerge')
                    ):
                        row['status'] = STATUS_UNCHANGED
                        row['statusLabel'] = STATUS_LABELS[STATUS_UNCHANGED]
                    else:
                        row['status'] = STATUS_REUSE
                        row['statusLabel'] = STATUS_LABELS[STATUS_REUSE]
                row['canCommit'] = row['status'] in (
                    STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD, STATUS_MERGE,
                )
            else:
                # 既存なし → 新規1商品（仮代表名可）
                row['product'] = dict(row.get('product') or {})
                row['product']['action'] = 'create'
                row['product']['id'] = None
                row['product']['officialName'] = official_name
                row['product']['officialColor'] = color
                row['product']['primarySupplierName'] = primary_supplier
                row['product']['skuMerge'] = True
                row['product']['provisionalName'] = provisional
                row['product']['applyOfficialName'] = True
                row['product']['nameMappings'] = name_plan.get('nameMappings') or []
                row['product']['nameAdoptionRule'] = name_plan.get('adoptionRule') or ''
                row['status'] = STATUS_CREATE
                row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                row['canCommit'] = True
            continue

        if decision == SKU_DECISION_SEPARATE:
            plans = raw.get('plans') if isinstance(raw.get('plans'), dict) else {}
            variant = _row_variant(row)
            plan = plans.get(variant['key']) or plans.get(row.get('csv', {}).get('productName')) or {}
            approach = stock.text(plan.get('approach') or raw.get('approach') or '')
            if approach in ('', 'defer'):
                row['status'] = STATUS_HOLD
                row['statusLabel'] = STATUS_LABELS[STATUS_HOLD]
                row['canCommit'] = False
                row['deferReason'] = stock.text(plan.get('reason') or '別物として未整理のため保留')
                hold_count += 1
            elif approach == 'change_code':
                new_code = stock.text(plan.get('newCode') or '')
                if not new_code or new_code == code:
                    row['status'] = STATUS_REVIEW
                    row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                    row['canCommit'] = False
                    row['reasons'] = list(row.get('reasons') or []) + [
                        f'品番「{code}」を別物にする場合は、修正後の品番を指定してください。'
                        f'同じ通常品番のまま新規登録はできません。'
                    ]
                    pending_decision += 1
                elif stock_product_merge.is_normal_product_code(new_code):
                    # 別品番として登録（既存があれば再利用判定はコミット時）
                    row['product'] = dict(row.get('product') or {})
                    row['product']['code'] = new_code
                    row['product']['codeOverride'] = new_code
                    row['product']['action'] = 'create'
                    row['product']['id'] = None
                    row['csv'] = dict(row.get('csv') or {})
                    row['csv']['code'] = new_code
                    row['csv']['codeDisplay'] = new_code
                    row['status'] = STATUS_CREATE
                    row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                    row['canCommit'] = True
                else:
                    row['product'] = dict(row.get('product') or {})
                    row['product']['code'] = new_code
                    row['product']['codeOverride'] = new_code
                    row['status'] = STATUS_CREATE
                    row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                    row['canCommit'] = True
            elif approach in ('as_parts', 'as_blank'):
                # 部品・資材 / 品番なしとして整理（通常品番の重複新規はしない）
                row['product'] = dict(row.get('product') or {})
                if approach == 'as_blank':
                    row['product']['code'] = ''
                    row['product']['codeOverride'] = ''
                    row['product']['blankSku'] = True
                row['product']['category'] = stock.CATEGORY_PARTS
                row['product']['action'] = 'create'
                row['product']['id'] = None
                row['status'] = STATUS_CREATE
                row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                row['canCommit'] = True
            else:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の整理方法（品番修正 / 部品・資材 / 品番なし / 保留）を選んでください。'
                    f'同じ通常品番のまま別商品として新規登録することはできません。'
                ]
                pending_decision += 1
            continue

        # 不明な decision
        row['status'] = STATUS_REVIEW
        row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
        row['canCommit'] = False
        pending_decision += 1

    pending_groups = 0
    for g in sku_groups:
        raw = decisions.get(g['code']) or decisions.get(str(g['code'])) or {}
        if isinstance(raw, dict) and stock.text(raw.get('decision')):
            g['decision'] = raw
            g['status'] = stock.text(raw.get('decision'))
        elif g['needsDecision']:
            g['status'] = 'needs_decision'
            pending_groups += 1

    return {
        'pendingDecisionGroups': pending_groups,
        'holdRows': hold_count,
    }


def _product_plan_key(row):
    """サイズ／取扱の重複集計用キー（通常品番は品番単位）。"""
    code = stock.text((row.get('csv') or {}).get('code'))
    prod = row.get('product') or {}
    if stock_product_merge.is_normal_product_code(code):
        return f'code:{code}'
    if prod.get('id'):
        return f'id:{prod["id"]}'
    return f'row:{row.get("rowNo")}'


def _planned_counts(classified, sku_groups):
    """CSV行数と新規商品数を混同しないための集計。

    サイズ追加・取扱新規は行数ではなく、商品×サイズ／顧客×商品×加工のユニーク数。
    """
    product_create_codes = set()
    product_reuse_ids = set()
    product_merge_codes = set()
    defer_rows = 0
    size_keys = set()
    handle_keys = set()
    for row in classified:
        st = row.get('status')
        if st == STATUS_HOLD:
            defer_rows += 1
            continue
        if st in (STATUS_EXCLUDED, STATUS_REVIEW):
            continue
        if row.get('skuDecisionNeeded') and st not in (
            STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD, STATUS_MERGE, STATUS_UNCHANGED,
        ):
            continue
        code = stock.text((row.get('csv') or {}).get('code'))
        prod = row.get('product') or {}
        csv = row.get('csv') or {}
        pkey = _product_plan_key(row)
        if prod.get('action') == 'create' or st == STATUS_CREATE:
            if stock_product_merge.is_normal_product_code(code):
                product_create_codes.add(code)
            else:
                product_create_codes.add(f'special:{row["rowNo"]}')
        if prod.get('id') and prod.get('action') == 'reuse':
            product_reuse_ids.add(prod['id'])
        if st == STATUS_MERGE or (prod.get('skuMerge') and prod.get('mergeSourceIds')):
            product_merge_codes.add(code)
        size_label = stock.text(csv.get('size') or prod.get('size') or '')
        if size_label and (prod.get('sizeAction') == 'add' or st == STATUS_SIZE_ADD):
            size_keys.add((pkey, size_label))
        if (row.get('handle') or {}).get('action') == 'create':
            handle_keys.add((
                identity_match_key(csv.get('customer') or ''),
                pkey,
                PENDING_EMBROIDERY,
            ))
    auto_groups = [g for g in sku_groups if g.get('plannedSingleProduct')]
    bulk_groups = [
        g for g in sku_groups
        if g.get('needsDecision') and g.get('reviewTier') == REVIEW_TIER_BULK
    ]
    individual_groups = [
        g for g in sku_groups
        if g.get('needsDecision') and g.get('reviewTier') == REVIEW_TIER_INDIVIDUAL
    ]
    defer_reasons = []
    for row in classified:
        if row.get('status') != STATUS_HOLD:
            continue
        defer_reasons.append({
            'rowNo': row.get('rowNo'),
            'code': stock.text((row.get('csv') or {}).get('code') or ''),
            'reason': stock.text(row.get('deferReason') or '') or '保留',
        })
    resolved_sku = sum(
        1 for g in sku_groups
        if g.get('needsDecision') and isinstance(g.get('decision'), dict)
        and stock.text((g.get('decision') or {}).get('decision'))
    )
    pending_sku = sum(
        1 for g in sku_groups if g.get('needsDecision') and not g.get('decision')
    )
    return {
        'csvRows': len(classified),
        'productsToCreate': len(product_create_codes),
        'productsToReuse': len(product_reuse_ids),
        'productsToMerge': len(product_merge_codes),
        'rowsDeferred': defer_rows,
        'sizesToAdd': len(size_keys),
        'handlesToCreate': len(handle_keys),
        'skuGroupsNeedingDecision': pending_sku,
        'skuGroupsResolved': resolved_sku,
        'bulkApprovableSkuGroups': len(bulk_groups),
        'individualReviewSkuGroups': len(individual_groups),
        'deferredReasons': defer_reasons,
        'autoMergedSkuGroups': len(auto_groups),
        'autoMergedCsvRows': sum(g.get('rowCount') or 0 for g in auto_groups),
    }


def _preview_outcome_summary(classified, sku_groups, planned, decision_meta):
    """プレビュー要約: 自動まとめ / 要確認 / 対象外（理由つき）。"""
    auto_reasons = defaultdict(int)
    for g in sku_groups:
        if not g.get('plannedSingleProduct'):
            continue
        label = ''
        for r in g.get('reviewReasons') or []:
            if r.get('code') == 'auto':
                label = r.get('label') or ''
                break
        auto_reasons[label or '自動まとめ'] += 1

    review_reasons = defaultdict(int)
    for g in sku_groups:
        if not g.get('needsDecision'):
            continue
        decided = isinstance(g.get('decision'), dict) and stock.text(
            (g.get('decision') or {}).get('decision') or ''
        )
        if decided:
            continue
        for r in g.get('reviewReasons') or []:
            label = stock.text(r.get('label') or '') or '要確認'
            review_reasons[label] += 1
        if not (g.get('reviewReasons') or []):
            review_reasons['要確認'] += 1

    # 行単位の要確認・対象外（品番比較以外）
    row_review_reasons = defaultdict(int)
    excluded_reasons = defaultdict(int)
    for row in classified:
        st = row.get('status')
        if st == STATUS_EXCLUDED:
            reason = ''
            for r in row.get('reasons') or []:
                reason = stock.text(r)
                if reason:
                    break
            excluded_reasons[reason or '対象外'] += 1
        elif st == STATUS_REVIEW and not row.get('skuDecisionNeeded'):
            reason = ''
            for r in row.get('reasons') or []:
                reason = stock.text(r)
                if reason:
                    break
            row_review_reasons[reason or '要確認'] += 1

    pending_sku = int(decision_meta.get('pendingDecisionGroups') or 0)
    return {
        'autoMergedSkuGroups': int(planned.get('autoMergedSkuGroups') or 0),
        'autoMergedCsvRows': int(planned.get('autoMergedCsvRows') or 0),
        'autoMergedReasons': [
            {'label': k, 'count': v} for k, v in sorted(auto_reasons.items(), key=lambda x: (-x[1], x[0]))
        ],
        'needsReviewSkuGroups': pending_sku,
        'needsReviewSkuReasons': [
            {'label': k, 'count': v}
            for k, v in sorted(review_reasons.items(), key=lambda x: (-x[1], x[0]))
        ],
        'needsReviewRows': sum(row_review_reasons.values()),
        'needsReviewRowReasons': [
            {'label': k, 'count': v}
            for k, v in sorted(row_review_reasons.items(), key=lambda x: (-x[1], x[0]))
        ],
        'excludedRows': sum(excluded_reasons.values()),
        'excludedReasons': [
            {'label': k, 'count': v}
            for k, v in sorted(excluded_reasons.items(), key=lambda x: (-x[1], x[0]))
        ],
    }


def build_preview(c, body):
    ensure_schema(c)
    stock_product_merge.migrate_product_merge(c)
    uploaded = decode_upload(body)
    aux_uploaded = decode_aux_upload(body)
    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    sku_decisions = body.get('skuDecisions') if isinstance(body.get('skuDecisions'), dict) else {}
    row_classifications = (
        body.get('rowClassifications') if isinstance(body.get('rowClassifications'), dict) else {}
    )

    # 品番グループへの商品選択を行へ展開
    group_product = {}
    group_handle = {}
    group_force = {}
    group_category = {}
    for key, val in selections.items():
        if not isinstance(val, dict):
            continue
        gk = val.get('matchGroupKey')
        if not gk:
            continue
        if val.get('productId') is not None:
            group_product[gk] = val.get('productId')
        if val.get('handleId') is not None:
            group_handle[gk] = val.get('handleId')
        if val.get('forceCreate'):
            group_force[gk] = True
        if 'category' in val:
            group_category[gk] = val.get('category')

    classified = []
    for row_no, item in uploaded['rows']:
        sel = dict(selections.get(str(row_no)) or selections.get(row_no) or {})
        paren_ov = (
            row_classifications.get(str(row_no))
            or row_classifications.get(row_no)
            or sel.get('parenClassification')
        )
        temp = classify_row(c, row_no, item, paren_override=paren_ov)
        gk = temp['matchGroupKey']
        if gk in group_product and sel.get('productId') is None:
            sel['productId'] = group_product[gk]
        if gk in group_handle and sel.get('handleId') is None:
            sel['handleId'] = group_handle[gk]
        # 通常品番の forceCreate は抜け穴にしない（別物整理の change_code 等で対応）
        code_t = stock.text(item.get('商品コード'))
        force = bool(sel.get('forceCreate'))
        if force and stock_product_merge.is_normal_product_code(code_t):
            force = False
        if gk in group_force and not stock_product_merge.is_normal_product_code(code_t):
            force = True
        if gk in group_category and 'category' not in sel:
            sel['category'] = group_category[gk]
        # sku merge で keepProductId が付いている場合
        sku = sku_decisions.get(code_t) or sku_decisions.get(str(code_t)) or {}
        if isinstance(sku, dict) and stock.text(sku.get('decision')) == SKU_DECISION_MERGE:
            kid = sku.get('keepProductId') or sku.get('productId')
            if kid and sel.get('productId') is None:
                sel['productId'] = kid
        classified.append(classify_row(
            c, row_no, item,
            selected_product_id=sel.get('productId'),
            selected_handle_id=sel.get('handleId'),
            selected_category=sel.get('category') if 'category' in sel else None,
            force_create_product=force,
            paren_override=paren_ov,
        ))

    # 照合グループ件数
    group_counts = defaultdict(int)
    for row in classified:
        group_counts[row['matchGroupKey']] += 1
    for row in classified:
        row['matchGroupCount'] = group_counts[row['matchGroupKey']]

    sku_groups = build_sku_groups(c, classified)
    aux_meta = attach_aux_primary_judgment(sku_groups, aux_uploaded)
    # 在庫一意／候補1組 → フルパッケージ採用。色・加工・素材差だけでは要確認に残さない
    apply_aux_full_package_adoption(sku_groups, sku_decisions)
    # 補助CSV判定後に仮代表名の予定を更新（決定前の画面表示用）
    for g in sku_groups:
        raw = sku_decisions.get(g['code']) or sku_decisions.get(str(g['code'])) or {}
        if not isinstance(raw, dict):
            raw = {}
        g['namePlan'] = plan_provisional_official_name(g, raw if stock.text(raw.get('decision')) else None)
        # 在庫一意採用時は aux の代表名を優先表示（既存維持は namePlan 側で守る）
        if g.get('auxStockPickAuto') and (g.get('auxPrimary') or {}).get('suggestedOfficialName'):
            if not (g.get('namePlan') or {}).get('keepsExistingOfficialName'):
                np = dict(g.get('namePlan') or {})
                np['officialName'] = stock.text(
                    (g.get('auxPrimary') or {}).get('suggestedOfficialName') or ''
                ) or np.get('officialName')
                np['provisional'] = False
                np['adoptionRule'] = (
                    g.get('auxStockPickReason')
                    or stock.text((g.get('auxPrimary') or {}).get('pairAdoptionRule') or '')
                    or np.get('adoptionRule')
                )
                np['adoptionRuleCode'] = stock.text(
                    (g.get('auxPrimary') or {}).get('pairAdoptionRuleCode') or ''
                ) or np.get('adoptionRuleCode')
                g['namePlan'] = np
        g['plannedOfficialName'] = (g.get('namePlan') or {}).get('officialName') or ''
        g['nameAdoptionRule'] = (g.get('namePlan') or {}).get('adoptionRule') or ''
        g['provisionalName'] = bool((g.get('namePlan') or {}).get('provisional'))
        # 自動まとめ品番は namePlan の代表名を商品計画へ反映（タグは埋め込まない）
        if g.get('plannedSingleProduct') and g.get('namePlan'):
            plan = dict(g.get('productPlan') or {})
            if g['plannedOfficialName']:
                plan['productName'] = g['plannedOfficialName']
            plan['nameAdoptionRule'] = g['nameAdoptionRule']
            plan['provisional'] = g['provisionalName']
            if g.get('auxStockPickAuto'):
                plan['auxStockPickAuto'] = True
                plan['auxStockPickReason'] = g.get('auxStockPickReason') or ''
                plan['color'] = g.get('plannedColor') or plan.get('color') or ''
                plan['processing'] = g.get('plannedProcessing') or plan.get('processing') or ''
                plan['material'] = g.get('plannedMaterial') or plan.get('material') or ''
                plan['keptPackages'] = list(g.get('keptPackages') or [])
            g['productPlan'] = plan
        # 通常仕入先の予定（自動 or 手動）— 確定と同じ決定結果
        primary, source, _, _, auto_reason = _resolve_effective_primary_supplier(g, raw)
        if g.get('auxStockPickAuto') and not stock.text(raw.get('primarySupplier') or ''):
            # 在庫一意の仕入先を予定へ（手動がなければ）
            aux_sup = stock.text(
                (g.get('auxPrimary') or {}).get('autoPrimarySupplier')
                or (g.get('adoptedPackage') or {}).get('supplier')
                or ''
            )
            if aux_sup:
                primary = aux_sup
                source = 'auto'
                auto_reason = g.get('auxStockPickReason') or auto_reason
        g['plannedPrimarySupplier'] = primary
        g['plannedPrimarySupplierReason'] = (
            auto_reason if source in ('auto', 'single') and primary else ''
        )
        g['plannedPrimarySupplierSource'] = source
    decision_meta = _apply_sku_decisions(c, classified, sku_groups, sku_decisions)
    _attach_product_plans(classified, sku_groups)
    _apply_auto_merge_official_names(classified, sku_groups)
    _apply_paren_names_after_sku_plans(classified, sku_groups)
    stamp_aux_adopted_package_on_rows(classified, sku_groups)

    counts = defaultdict(int)
    for row in classified:
        counts[row['status']] += 1

    review_count = counts.get(STATUS_REVIEW, 0)
    sku_pending_rows = sum(1 for r in classified if r.get('skuDecisionNeeded'))
    planned = _planned_counts(classified, sku_groups)
    summary = _preview_outcome_summary(classified, sku_groups, planned, decision_meta)
    auto_plans = [
        g.get('productPlan') for g in sku_groups
        if g.get('plannedSingleProduct') and g.get('productPlan')
    ]
    fp = db_fingerprint(c)
    snap = stock_snapshot(c)
    note = (
        '在庫数量・仕入単価・初期在庫・入出庫履歴は登録しません。'
        '通常品番は1品番=1商品です。サイズ・顧客・先頭「＊/*」中古標記・空白/全角半角・'
        '明示シノニム（例: パンツ／スラックス）だけの差は自動で1商品にまとめます。'
        '商品名の括弧内は辞書規則で色・加工・生地へ分類し、取扱の加工仕様／商品の色・生地へ保存します。'
        '未分類・曖昧な括弧は原文のまま要確認（自動確定しません）。'
        '色・生地・加工・男女・商品種の疑いや残り曖昧さは確認が必要です。'
        '代表名が未確定のときは「名称未整理」ステータスで示し、名前文字列には埋め込みません。'
        'CSV原文は照合用別名に残し、後続の初期在庫取込で解決できるようにします。'
        '品番空欄は部品・資材として取り込み（登録後にカテゴリ変更可）。'
        '保留した品番は今回登録せず、同じCSVの再読込で処理できます。'
    )
    if aux_meta:
        note += (
            ' 初期在庫用CSVは、非ゼロ在庫がある候補が1組のとき（または候補自体が1組のとき）'
            'その候補の商品名・仕入先・色・加工・素材をセットで採用し、'
            '通常仕入先の判定補助にも使います。在庫数量は書き込みません。'
            '色・加工・素材が候補間で違っても、在庫一意採用ならそれだけでは要確認にしません。'
        )
    return {
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'fileSize': uploaded['fileSize'],
        'encoding': uploaded['encoding'],
        'fields': uploaded['fields'],
        'rowCount': len(classified),
        'rows': classified,
        'skuGroups': sku_groups,
        'skuGroupCount': len(sku_groups),
        'skuDecisionPending': decision_meta['pendingDecisionGroups'],
        'skuDecisionPendingRows': sku_pending_rows,
        'bulkApprovableCount': planned.get('bulkApprovableSkuGroups', 0),
        'individualReviewCount': planned.get('individualReviewSkuGroups', 0),
        'autoMergePlans': auto_plans,
        'auxOpeningStock': aux_meta,
        'reviewTierLabels': dict(REVIEW_TIER_LABELS),
        'provisionalNameTag': PROVISIONAL_NAME_TAG,
        'counts': dict(STATUS_LABELS),
        'countsRaw': dict(counts),
        'plannedCounts': planned,
        'outcomeSummary': summary,
        'reviewCount': review_count,
        'previewFingerprint': fp,
        'previewAt': stock.stamp(),
        'stockSnapshot': snap,
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
        'categoryOptions': ['', stock.CATEGORY_UNIFORM, stock.CATEGORY_PARTS],
        'note': note,
    }


def _ensure_person(c, table, save_fn, name, *, create):
    return legacy._ensure_person(c, table, save_fn, name, create=create)


def _save_product_sizes_only(c, pid, size_label):
    current = stock.product_row(c, pid)
    labels = list(current['sizes'])
    if not size_label or size_label in labels:
        return False
    labels.append(size_label)
    stock.save_product(c, {
        'id': pid,
        'code': current['code'],
        'name': current['name'],
        'color': current['color'],
        'category': current.get('category') or '',
        'supplierId': current['supplierId'],
        'subSupplierIds': current.get('subSupplierIds') or [],
        'sizes': labels,
        'active': current['active'],
        'links': [
            {
                'customerId': link['customerId'],
                'embroidery': link['embroidery'],
                'isStandard': link['isStandard'],
                'active': link['active'],
            }
            for link in current['links']
        ],
    })
    return True


def _ensure_sub_supplier(c, product_id, supplier_id):
    if not product_id or not supplier_id:
        return False
    if stock.product_has_supplier(c, product_id, supplier_id):
        return False
    # 通常仕入先は変更しない。サブとして追加。
    max_sort = c.execute(
        'SELECT COALESCE(MAX(sort_order), -1) AS m FROM product_suppliers WHERE product_id=?',
        (product_id,),
    ).fetchone()['m']
    c.execute(
        'INSERT INTO product_suppliers(product_id,supplier_id,sort_order,created_at) VALUES(?,?,?,?)',
        (product_id, supplier_id, int(max_sort) + 1, stock.stamp()),
    )
    return True


def _find_supplier_id_by_name(c, name):
    name = stock.text(name)
    if not name:
        return None
    matches, _how = legacy.find_person_matches(c, 'suppliers', name)
    if len(matches) == 1:
        return matches[0]['id']
    exact = [m for m in matches if stock.text(m['name']) == name]
    if len(exact) == 1:
        return exact[0]['id']
    return None


def _product_save_payload(c, pid, *, supplier_id=None, sub_supplier_ids=None):
    cur = stock.product_row(c, pid)
    return {
        'id': pid,
        'code': cur['code'],
        'name': cur['name'],
        'color': cur['color'],
        'material': cur.get('material') or '',
        'category': cur.get('category') or '',
        'supplierId': cur['supplierId'] if supplier_id is None else supplier_id,
        'subSupplierIds': (
            list(cur.get('subSupplierIds') or [])
            if sub_supplier_ids is None
            else list(sub_supplier_ids)
        ),
        'sizes': cur['sizes'],
        'active': cur['active'],
        'links': [
            {
                'customerId': link['customerId'],
                'embroidery': link['embroidery'],
                'isStandard': link['isStandard'],
                'active': link['active'],
            }
            for link in cur['links']
        ],
    }


def _apply_primary_supplier_change(c, product_id, primary_supplier_id, *, confirm):
    """明示確認があるときだけ既存商品の通常仕入先を差し替える。旧通常はサブへ。"""
    if not product_id or not primary_supplier_id or not confirm:
        return False
    prod = _load_product(c, product_id)
    if not prod:
        return False
    old_id = prod['supplier_id']
    if old_id == primary_supplier_id:
        return False
    cur = stock.product_row(c, product_id)
    new_subs = [sid for sid in (cur.get('subSupplierIds') or []) if sid != primary_supplier_id]
    if old_id and old_id != primary_supplier_id and old_id not in new_subs:
        new_subs.append(old_id)
    stock.save_product(c, _product_save_payload(
        c, product_id, supplier_id=primary_supplier_id, sub_supplier_ids=new_subs,
    ))
    return True


def _apply_official_name(c, product_id, official_name, color=None, csv_name=None, material=None):
    """公式名を確定し、CSV名・旧名を別名に残す。"""
    if not product_id or not official_name:
        return False
    current = stock.product_row(c, product_id)
    if stock.text(current['name']) != stock.text(official_name):
        stock_product_merge.ensure_name_alias(c, product_id, current['name'])
    if csv_name and stock.text(csv_name) != stock.text(official_name):
        stock_product_merge.ensure_name_alias(c, product_id, csv_name)
    new_color = current['color'] if color is None else stock.text(color)
    new_material = current.get('material') or '' if material is None else stock.text(material)
    if (
        stock.text(current['name']) == stock.text(official_name)
        and stock.text(current['color']) == new_color
        and stock.text(current.get('material') or '') == new_material
    ):
        return False
    stock.save_product(c, {
        'id': product_id,
        'code': current['code'],
        'name': official_name,
        'color': new_color,
        'material': new_material,
        'category': current.get('category') or '',
        'supplierId': current['supplierId'],
        'subSupplierIds': current.get('subSupplierIds') or [],
        'sizes': current['sizes'],
        'active': current['active'],
        'links': [
            {
                'customerId': link['customerId'],
                'embroidery': link['embroidery'],
                'isStandard': link['isStandard'],
                'active': link['active'],
            }
            for link in current['links']
        ],
    })
    return True


def _ensure_product(c, product_info, supplier_id, csv_name, *, customer_id=None, force_create=False):
    if product_info.get('codeOverride') is not None:
        product_info = dict(product_info)
        product_info['code'] = product_info.get('codeOverride')

    code_for_name = stock.text(product_info.get('codeOverride') if product_info.get('codeOverride') is not None else product_info.get('code') or '')
    # 別注・品番なしは原文名を優先（括弧除去名へ自動置換しない）
    keep_csv_name = (
        legacy.is_custom_order_code(code_for_name)
        or not stock.text(code_for_name)
        or code_for_name == stock_product_merge.UNKNOWN_CODE
    )
    if keep_csv_name:
        official_name = stock.text(product_info.get('officialName') or '') or stock.text(csv_name)
    else:
        official_name = (
            stock.text(product_info.get('officialName') or '')
            or stock.text(product_info.get('suggestedNameFromParen') or '')
            or stock.text(product_info.get('plannedNameFromParen') or '')
            or stock.text(csv_name)
        )
    official_color = product_info.get('officialColor')
    if official_color is None:
        official_color = product_info.get('proposedColor')
    if official_color is None:
        official_color = ''
    official_material = product_info.get('officialMaterial')
    if official_material is None:
        official_material = product_info.get('proposedMaterial')
    if official_material is None:
        official_material = ''

    primary_name = stock.text(product_info.get('primarySupplierName') or '')
    primary_id = _find_supplier_id_by_name(c, primary_name) if primary_name else None

    if product_info.get('id') and not force_create:
        pid = product_info['id']
        # applyOfficialName=False（仮名・既存維持）のときは公式名を上書きしない
        if 'applyOfficialName' in product_info:
            should_apply_official = bool(product_info.get('applyOfficialName'))
        else:
            should_apply_official = bool(product_info.get('skuMerge'))
        if should_apply_official and official_name:
            _apply_official_name(
                c, pid, official_name,
                color=official_color if (
                    'officialColor' in product_info or 'proposedColor' in product_info
                ) else None,
                material=official_material if (
                    'officialMaterial' in product_info or 'proposedMaterial' in product_info
                ) else None,
                csv_name=csv_name,
            )
        else:
            # 再利用時は CSV 名・対応表の原文を別名に残す（公式名は変えない）
            if stock.text(csv_name) and stock_product_merge.is_normal_product_code(product_info.get('code')):
                prod = _load_product(c, pid)
                if prod and stock.text(prod['name']) != stock.text(csv_name):
                    stock_product_merge.ensure_name_alias(c, pid, csv_name)
            for mapping in product_info.get('nameMappings') or []:
                orig = stock.text(mapping.get('originalName') or '')
                if orig:
                    stock_product_merge.ensure_name_alias(c, pid, orig)
        size_added = False
        if product_info.get('sizeAction') == 'add':
            size_added = _save_product_sizes_only(c, pid, product_info.get('size'))
        sub_added = False
        if product_info.get('subSupplierAdd') and product_info.get('subSupplierId'):
            sub_added = _ensure_sub_supplier(c, pid, product_info['subSupplierId'])
        elif product_info.get('subSupplierAdd') and supplier_id:
            sub_added = _ensure_sub_supplier(c, pid, supplier_id)
        # 通常仕入先: 未設定なら設定。変更は明示確認があるときだけ。それ以外はサブ追加。
        if primary_name:
            prod = _load_product(c, pid)
            target_primary = primary_id or supplier_id
            if prod and target_primary and prod['supplier_id'] in (None, ''):
                stock.save_product(c, _product_save_payload(c, pid, supplier_id=target_primary))
            elif (
                prod
                and target_primary
                and prod['supplier_id'] not in (None, target_primary)
                and product_info.get('confirmPrimaryChange')
            ):
                _apply_primary_supplier_change(
                    c, pid, target_primary, confirm=True,
                )
            elif prod and supplier_id and prod['supplier_id'] not in (None, supplier_id):
                sub_added = _ensure_sub_supplier(c, pid, supplier_id) or sub_added
            if supplier_id and primary_id and supplier_id != primary_id:
                sub_added = _ensure_sub_supplier(c, pid, supplier_id) or sub_added
            stock_product_merge.ensure_supplier_alias(c, pid, primary_name)
        elif supplier_id:
            prod = _load_product(c, pid)
            if prod and prod['supplier_id'] not in (None, supplier_id):
                sub_added = _ensure_sub_supplier(c, pid, supplier_id) or sub_added
        return pid, False, size_added, sub_added

    code = stock.text(product_info.get('code'))
    name = official_name or stock.text(csv_name)

    if not force_create:
        if legacy.is_custom_order_code(code):
            matches = legacy.find_custom_order_products_for_customer(
                c, name, customer_id=customer_id,
            )
            if len(matches) == 1:
                size_added = _save_product_sizes_only(c, matches[0]['id'], product_info.get('size'))
                return matches[0]['id'], False, size_added, False
        elif stock_product_merge.is_normal_product_code(code):
            matches = stock_product_merge.find_live_products_by_code(c, code)
            if not matches:
                matches = stock_product_merge.find_products_by_name_alias(c, code, csv_name or name)
            if len(matches) > 1:
                raise ValueError(
                    f'品番「{code}」の商品が複数あります。先に統合してから登録してください。'
                )
            if len(matches) == 1:
                pid = matches[0]['id']
                if stock.text(matches[0]['name']) != stock.text(csv_name):
                    stock_product_merge.ensure_name_alias(c, pid, csv_name)
                if (product_info.get('skuMerge') or product_info.get('applyOfficialName')) and official_name:
                    _apply_official_name(
                        c, pid, official_name,
                        color=official_color if (
                            'officialColor' in product_info or 'proposedColor' in product_info
                        ) else None,
                        material=official_material if (
                            'officialMaterial' in product_info or 'proposedMaterial' in product_info
                        ) else None,
                        csv_name=csv_name,
                    )
                size_added = _save_product_sizes_only(c, pid, product_info.get('size'))
                sub_added = False
                if supplier_id and matches[0]['supplier_id'] not in (None, supplier_id):
                    sub_added = _ensure_sub_supplier(c, pid, supplier_id)
                return pid, False, size_added, sub_added
        elif code:
            # 不明など特殊で空でない
            matches = legacy.find_products_by_code(c, code)
            by_name = [m for m in matches if stock.text(m['name']) == name]
            narrowed = by_name
            if supplier_id is not None and len(by_name) > 1:
                by_sup = [m for m in by_name if m['supplier_id'] == supplier_id]
                if by_sup:
                    narrowed = by_sup
            if len(narrowed) == 1:
                size_added = _save_product_sizes_only(c, narrowed[0]['id'], product_info.get('size'))
                sub_added = False
                if supplier_id and narrowed[0]['supplier_id'] not in (None, supplier_id):
                    sub_added = _ensure_sub_supplier(c, narrowed[0]['id'], supplier_id)
                return narrowed[0]['id'], False, size_added, sub_added
        else:
            matches = legacy.find_blank_sku_products(c, name, supplier_id)
            if len(matches) == 1:
                size_added = _save_product_sizes_only(c, matches[0]['id'], product_info.get('size'))
                return matches[0]['id'], False, size_added, False

    category = stock.normalize_category(
        product_info.get('category') or (
            stock.CATEGORY_UNIFORM if legacy.is_custom_order_code(code) else ''
        )
    )
    # 新規作成時は指定の通常仕入先を優先（行順に依存しない）
    create_supplier_id = primary_id or supplier_id
    saved = stock.save_product(c, {
        'code': code,
        'name': name,  # 公式名（括弧分類後の予定名またはCSV原文）
        'color': stock.text(official_color) if official_color is not None else '',
        'material': stock.text(official_material) if official_material is not None else '',
        'category': category,
        'supplierId': create_supplier_id,
        'sizes': [product_info['size']],
        'links': [],
        'active': True,
    })
    # 原文名は照合用別名（初期在庫CSVなどの後続解決用）
    if csv_name and stock.text(csv_name) != stock.text(name):
        stock_product_merge.ensure_name_alias(c, saved['id'], csv_name)
    orig_csv = stock.text(product_info.get('csvOriginalName') or '')
    if orig_csv and stock.text(orig_csv) != stock.text(name):
        stock_product_merge.ensure_name_alias(c, saved['id'], orig_csv)
    for mapping in product_info.get('nameMappings') or []:
        orig = stock.text(mapping.get('originalName') or '')
        if orig and stock.text(orig) != stock.text(name):
            stock_product_merge.ensure_name_alias(c, saved['id'], orig)
    if primary_name:
        stock_product_merge.ensure_supplier_alias(c, saved['id'], primary_name)
    sub_added = False
    if supplier_id and create_supplier_id and supplier_id != create_supplier_id:
        sub_added = _ensure_sub_supplier(c, saved['id'], supplier_id)
    return saved['id'], True, False, sub_added


def _ensure_handle(c, customer_id, product_id, handle_info):
    """取扱を確保。括弧分類の加工仕様があれば「加工未整理」ではなくそれを使う。"""
    info = handle_info if isinstance(handle_info, dict) else {}
    if info.get('id'):
        return legacy._ensure_handle(c, customer_id, product_id, info)
    emb = stock.text(info.get('embroidery') or '')
    proposed = stock.text(info.get('proposedEmbroidery') or '')
    if info.get('fromParenClassification') and (emb or proposed):
        use = emb if emb and emb != PENDING_EMBROIDERY else proposed
        saved = stock.ensure_customer_product(c, {
            'customerId': customer_id,
            'productId': product_id,
            'embroidery': use or PENDING_EMBROIDERY,
        })
        return saved['id'], not saved.get('already')
    if emb and emb != PENDING_EMBROIDERY:
        saved = stock.ensure_customer_product(c, {
            'customerId': customer_id,
            'productId': product_id,
            'embroidery': emb,
        })
        return saved['id'], not saved.get('already')
    return legacy._ensure_handle(c, customer_id, product_id, info)


def _run_confirmed_product_merges(c, classified, sku_decisions, *, actor_name, token_prefix):
    """ユーザー確認済みの既存重複統合だけ実行（自動統合はしない）。"""
    merged = []
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    seen_keep = set()
    for row in classified:
        prod = row.get('product') or {}
        if not prod.get('skuMerge'):
            continue
        keep_id = prod.get('id')
        sources = [int(x) for x in (prod.get('mergeSourceIds') or []) if x]
        if not keep_id or not sources:
            continue
        key = (int(keep_id), tuple(sorted(sources)))
        if key in seen_keep:
            continue
        seen_keep.add(key)
        product_ids = sorted({int(keep_id), *[int(s) for s in sources]})
        if len(product_ids) < 2:
            continue
        merge_body = {
            'productIds': product_ids,
            'keepProductId': int(keep_id),
            'name': prod.get('officialName') or row.get('csv', {}).get('productName'),
            'color': prod.get('officialColor') if prod.get('officialColor') is not None else '',
            'token': f'{token_prefix}-merge-{keep_id}',
            '_canWrite': True,
            '_actor': {'name': actor_name},
        }
        preview = stock_product_merge.preview_merge(c, merge_body)
        merge_body['fingerprint'] = preview['fingerprint']
        merge_body['name'] = merge_body['name'] or preview['name']
        if 'officialColor' in prod:
            merge_body['color'] = stock.text(prod.get('officialColor') or '')
        else:
            merge_body['color'] = preview['color']
        merge_body['category'] = preview['category']
        merge_body['supplierId'] = preview['supplierId']
        merge_body['subSupplierIds'] = preview['subSupplierIds']
        # 通常仕入先の明示指定（初期在庫CSV判定 or ユーザー選択）があれば統合結果へ反映
        primary_name = stock.text(prod.get('primarySupplierName') or '')
        if primary_name:
            primary_id = _find_supplier_id_by_name(c, primary_name)
            if primary_id:
                old_primary = preview.get('supplierId')
                merge_body['supplierId'] = primary_id
                subs = [sid for sid in (preview.get('subSupplierIds') or []) if sid != primary_id]
                if old_primary and old_primary != primary_id and old_primary not in subs:
                    # 明示確認なしでは既存通常を黙って変えない（ここは confirm 済み行のみ）
                    if prod.get('confirmPrimaryChange') or not old_primary:
                        subs.append(old_primary)
                    elif not prod.get('confirmPrimaryChange') and old_primary:
                        # 確認なし: 統合プレビューの通常を維持し、指定先はサブへ
                        merge_body['supplierId'] = old_primary
                        if primary_id not in subs:
                            subs.append(primary_id)
                merge_body['subSupplierIds'] = subs
        merge_body['sizes'] = preview['sizes']
        # 取扱差異は「残す商品」側を優先（CSV確認で keep を選んだ前提）
        if preview.get('handleConflicts'):
            merge_body['conflictResolutions'] = {
                str(m['sourceHandleId']): 'keep' for m in preview['handleConflicts']
            }
        # バックアップは台帳取込側で取得済みのため db_path=None
        result = stock_product_merge.execute_merge(c, merge_body, db_path=None)
        merged.append(result)
        # 統合後、行の product.id を統合先へ
        for r in classified:
            p = r.get('product') or {}
            if p.get('id') in sources:
                p['id'] = int(keep_id)
    return merged


def commit_preview(c, body, *, db_path=None):
    stock._require_write(body)
    ensure_schema(c)
    stock_product_merge.migrate_product_merge(c)
    # プレビューと同じ経路で再分類（確定直前の再チェック）
    preview = build_preview(c, body)
    classified = preview['rows']
    sku_groups = preview.get('skuGroups') or []
    planned = preview.get('plannedCounts') or {}

    token = stock._token(body.get('token') or body.get('commitToken'))
    existing_token = c.execute(
        'SELECT id,result_json FROM product_ledger_import_runs WHERE commit_token=?', (token,)
    ).fetchone()
    if existing_token:
        try:
            result = json.loads(existing_token['result_json'] or '{}')
        except json.JSONDecodeError:
            result = {}
        if not isinstance(result, dict):
            result = {}
        return {**result, 'already': True, 'runId': existing_token['id']}

    preview_fp = stock.text(body.get('previewFingerprint'))
    if not preview_fp:
        raise ValueError('プレビュー結果を指定してください。もう一度読み込み直してください。')
    fp_now = db_fingerprint(c)
    if preview_fp != fp_now:
        raise stock.StockConflict({
            'error': 'プレビュー後にデータが変わったため、確認し直してください。',
            'code': 'stale_preview',
            'previewFingerprint': preview_fp,
            'currentFingerprint': fp_now,
        })

    # プレビュー応答の fingerprint と一致させるため、build_preview 結果を使う
    if preview['previewFingerprint'] != fp_now:
        # build_preview は DB を変えないので通常は一致。不一致なら再読込を促す。
        raise stock.StockConflict({
            'error': 'プレビュー後にデータが変わったため、確認し直してください。',
            'code': 'stale_preview',
            'previewFingerprint': preview_fp,
            'currentFingerprint': fp_now,
        })

    uploaded = decode_upload(body)
    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    sku_decisions = body.get('skuDecisions') if isinstance(body.get('skuDecisions'), dict) else {}

    review_rows = [r for r in classified if r['status'] == STATUS_REVIEW]
    excluded_rows = [r for r in classified if r['status'] == STATUS_EXCLUDED]
    hold_rows = [r for r in classified if r['status'] == STATUS_HOLD]
    commit_rows = [
        r for r in classified
        if r['status'] in (STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD, STATUS_MERGE)
        and not r.get('skuDecisionNeeded')
    ]

    pending_sku = sum(1 for g in sku_groups if g.get('needsDecision') and not g.get('decision'))
    if pending_sku:
        raise ValueError(
            f'品番比較で未決定が {pending_sku} 件あります。'
            f'【同じ商品としてまとめる】【別物のため個別に整理する】【今回は保留する】を選んでください。'
        )
    if any(r.get('skuDecisionNeeded') for r in classified):
        raise ValueError(
            '品番比較の未決定行が残っています。'
            'サイズ差だけの行は要確認ではありません。品番ごとの比較で決定してください。'
        )
    if review_rows and not body.get('allowCommitWithReview'):
        raise ValueError(
            f'要確認が {len(review_rows)} 件残っています。'
            f'未解決の行を黙って除外せず、候補を選ぶか解決してから確定してください。'
        )
    if not commit_rows and not body.get('allowEmptyCommit'):
        raise ValueError(
            '登録する行がありません。'
            f'要確認 {len(review_rows)} 件、保留 {len(hold_rows)} 件、対象外 {len(excluded_rows)} 件、'
            f'変更なし {sum(1 for r in classified if r["status"] == STATUS_UNCHANGED)} 件です。'
        )

    # 確定前サマリ（行数≠新規商品数）
    confirm_summary = {
        'csvRows': planned.get('csvRows', len(classified)),
        'productsToCreate': planned.get('productsToCreate', 0),
        'productsToReuse': planned.get('productsToReuse', 0),
        'productsToMerge': planned.get('productsToMerge', 0),
        'rowsDeferred': planned.get('rowsDeferred', len(hold_rows)),
        'sizesToAdd': planned.get('sizesToAdd', 0),
        'handlesToCreate': planned.get('handlesToCreate', 0),
        'commitRows': len(commit_rows),
    }

    before_stock = stock_snapshot(c)
    backup_path = ''
    if db_path:
        # Must succeed before any registration writes. Do not skip on failure.
        try:
            backup_path = str(stock.backup_sqlite(db_path, tag='pre-product-ledger') or '')
        except stock.BackupError:
            raise
        except Exception as e:
            # Unexpected errors still abort with a clear pre-registration message.
            print(
                f'product_ledger backup failed before register: {type(e).__name__}: {e}',
                flush=True,
            )
            raise stock.BackupError(stock.BACKUP_FAILED_MESSAGE, cause=e) from e

    actor = stock._actor_name(body)
    t0 = stock.stamp()
    c.execute(
        '''INSERT INTO product_ledger_import_runs(
             file_sha256,file_name,file_size,row_count,actor_name,
             preview_at,preview_fingerprint,commit_token,created_at,backup_path)
           VALUES(?,?,?,?,?,?,?,?,?,?)''',
        (
            uploaded['fileSha256'], uploaded['fileName'], uploaded['fileSize'], len(classified),
            actor, stock.text(body.get('previewAt')) or t0, preview_fp, token, t0, backup_path,
        ),
    )
    run_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]

    result_counts = {
        'customersCreated': 0, 'suppliersCreated': 0, 'productsCreated': 0,
        'sizesAdded': 0, 'handlesCreated': 0, 'subSuppliersAdded': 0,
        'productsMerged': 0,
        'reused': 0, 'unchanged': sum(1 for r in classified if r['status'] == STATUS_UNCHANGED),
        'reviewed': len(review_rows), 'excluded': len(excluded_rows),
        'deferred': len(hold_rows),
        'committedRows': 0, 'failed': 0,
    }
    failures = []
    defer_records = [
        {
            'rowNo': r['rowNo'],
            'code': (r.get('csv') or {}).get('code'),
            'productName': (r.get('csv') or {}).get('productName'),
            'reason': r.get('deferReason') or '',
            'csv': r.get('csv'),
        }
        for r in hold_rows
    ]

    try:
        merge_results = _run_confirmed_product_merges(
            c, classified, sku_decisions, actor_name=actor, token_prefix=token,
        )
        result_counts['productsMerged'] = sum(
            len(m.get('mergedProductIds') or []) for m in merge_results
        )

        # 同一通常品番の新規はコミット内で1商品にまとめる
        created_by_code = {}

        for row in commit_rows:
            row_no = row['rowNo']
            try:
                cust_id, created = _ensure_person(
                    c, 'customers', stock.save_customer, row['csv']['customer'],
                    create=row['customer']['action'] == 'create',
                )
                if created:
                    result_counts['customersCreated'] += 1

                sup_id = None
                if row['csv']['supplier']:
                    sup_id, created = _ensure_person(
                        c, 'suppliers', stock.save_supplier, row['csv']['supplier'],
                        create=row['supplier']['action'] == 'create',
                    )
                    if created:
                        result_counts['suppliersCreated'] += 1

                sel = selections.get(str(row_no)) or selections.get(row_no) or {}
                prod_info = dict(row['product'] or {})
                # 通常仕入先名が行の仕入先と違う場合でも先に存在させる（補助CSV由来の新規は作らない＝台帳候補内のみ）
                primary_name = stock.text(prod_info.get('primarySupplierName') or '')
                if primary_name and primary_name != stock.text(row['csv'].get('supplier') or ''):
                    _ensure_person(
                        c, 'suppliers', stock.save_supplier, primary_name, create=True,
                    )
                code_t = stock.text(prod_info.get('codeOverride') if prod_info.get('codeOverride') is not None else row['csv']['code'])
                force = bool(sel.get('forceCreate')) and not stock_product_merge.is_normal_product_code(code_t)

                # 同一品番で直前に作った商品へ再利用
                if (
                    stock_product_merge.is_normal_product_code(code_t)
                    and not prod_info.get('id')
                    and code_t in created_by_code
                ):
                    prod_info['id'] = created_by_code[code_t]
                    prod_info['action'] = 'reuse'
                    if prod_info.get('size') and prod_info.get('sizeAction') != 'reuse':
                        prod_info['sizeAction'] = 'add'

                pid, created, size_added, sub_added = _ensure_product(
                    c, prod_info, sup_id, row['csv']['productName'],
                    customer_id=cust_id,
                    force_create=force,
                )
                if created and stock_product_merge.is_normal_product_code(code_t):
                    created_by_code[code_t] = pid
                if created:
                    result_counts['productsCreated'] += 1
                if size_added:
                    result_counts['sizesAdded'] += 1
                if sub_added:
                    result_counts['subSuppliersAdded'] += 1
                if not created and not size_added and not sub_added:
                    result_counts['reused'] += 1

                handle_info = dict(row.get('handle') or {})
                if sel.get('handleId'):
                    handle_info['id'] = sel['handleId']
                    handle_info['action'] = 'reuse'
                elif row.get('handle', {}).get('id'):
                    handle_info['id'] = row['handle']['id']
                    handle_info['action'] = 'reuse'
                cp_id, created_h = _ensure_handle(c, cust_id, pid, handle_info)
                if created_h:
                    result_counts['handlesCreated'] += 1

                upsert_row_map(
                    c, run_id=run_id, file_sha256=uploaded['fileSha256'],
                    file_name=uploaded['fileName'], row=row,
                    customer_id=cust_id, product_id=pid, customer_product_id=cp_id,
                )
                result_counts['committedRows'] += 1
            except Exception as e:
                result_counts['failed'] += 1
                failures.append({'rowNo': row_no, 'error': str(e) or '登録に失敗しました。'})
                raise

        after_stock = stock_snapshot(c)
        if after_stock != before_stock:
            raise RuntimeError(
                '在庫数量・金額・履歴が変化したため取込を中止しました。'
                f' before={before_stock} after={after_stock}'
            )

        result = {
            'runId': run_id,
            'already': False,
            'counts': result_counts,
            'confirmSummary': confirm_summary,
            'deferredRows': defer_records,
            'failures': failures,
            'backupPath': backup_path,
            'stockSnapshotBefore': before_stock,
            'stockSnapshotAfter': after_stock,
            'stockUnchanged': True,
            'reviewRemaining': len(review_rows),
            'excludedCount': len(excluded_rows),
            'deferredCount': len(hold_rows),
            'fileName': uploaded['fileName'],
            'fileSha256': uploaded['fileSha256'],
            'actorName': actor,
            'committedAt': stock.stamp(),
            'note': IMPORT_NOTE,
        }
        c.execute(
            'UPDATE product_ledger_import_runs SET committed_at=?, result_json=? WHERE id=?',
            (result['committedAt'], json.dumps(result, ensure_ascii=False), run_id),
        )
        return result
    except Exception:
        # BEGIN IMMEDIATE の呼び出し側でロールバックされる想定。部分登録を残さない。
        raise


def file_history(c, body):
    ensure_schema(c)
    uploaded = decode_upload(body)
    return {
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
    }
