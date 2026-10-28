"""旧システム「商品台帳.csv」からの商品・顧客取扱一括登録。

標準フローは二段階:
  1. importMode=products … 商品・取扱登録（初期在庫CSVは判定補助のみ。在庫は書かない）
  2. importMode=opening_stock … 初期在庫CSVのみ＋永続マップで初期在庫登録

後方互換: importMode なしで registerOpeningStock=true のときのみ従来の同時登録。
プレビューは DB を変えない。確定は一括トランザクション。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import time
import unicodedata
from collections import defaultdict

import legacy_csv_bulk_import as bulk
import legacy_csv_import as legacy
import maker_sku_lookup
import product_ledger_paren as paren_classify
import product_ledger_position as position_sku
import product_ledger_processing_service as proc_svc
import product_ledger_scoped_color as scoped_color
import stock
import stock_product_merge

# 配色つなぎ（先頭中古標記を除いたあとの /／×✕*＊ → 比較上 ×）
_COLORWAY_SEP_RE = re.compile(r'[\s\u3000]*[/／×✕*＊][\s\u3000]*')

REQUIRED_COLUMNS = ('顧客', '仕入先', '商品コード', '商品名', 'サイズ')
IMPORT_NOTE = '旧CSV一括取り込み'
IMPORT_MODE_PRODUCTS = 'products'
IMPORT_MODE_OPENING = 'opening_stock'
IMPORT_MODE_COMBINED = 'combined'
AUX_JUDGMENT_ONLY_NOTE = (
    '初期在庫CSVは商品選定の判断にだけ使います。在庫は登録しません'
)
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
SKU_DECISION_COLOR_SPLIT = 'color_split'
SKU_DECISION_CONTENT_SPLIT = 'content_split'

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
# 具体的な帽子種は一般の「帽子／キャップ」同義より先に取る（誤統合防止）
_KIND_SPECIFIC_HAT_RE = re.compile(
    r'(ハンチング帽|ニット帽|キャスケット|ベレー帽|ワークキャップ|ヘルメット)'
)
_KIND_HINT_RE = re.compile(
    r'(ハンチング帽|ニット帽|キャスケット|ベレー帽|ワークキャップ|'
    r'帽子|キャップ|ヘルメット|ベスト|パンツ|ズボン|スラックス|シャツ|ブラウス|'
    r'ブルゾン|ジャケット|コート|スカート|エプロン|手袋|グローブ|靴|シューズ|安全靴|'
    r'つなぎ|カバーオール|白衣|スクラブ|レインウェア|ポロシャツ)'
)
_GENDER_HINT_RE = re.compile(
    r'(男性用|女性用|メンズ|レディース|紳士用|婦人用|男子|女子|男児|女児)'
)
# 男女・対象の明示対立（在庫一意でも自動クリアしない）。有無だけの差は別扱い。
_GENDER_FEMALE_TOKENS = frozenset({'女性用', 'レディース', '婦人用', '女子', '女児'})
_GENDER_MALE_TOKENS = frozenset({'男性用', 'メンズ', '紳士用', '男子', '男児'})
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
    """商品種ヒント。明示シノニム（パンツ／スラックス・帽子／キャップ等）は代表へそろえる。

    ハンチング帽・ニット帽など具体種があるときは一般の帽子／キャップ同義へ落とさない。
    """
    raw = stock.text(text) or ''
    specific = _hint_tokens(raw, _KIND_SPECIFIC_HAT_RE)
    if specific:
        # 具体種が取れたら一般帽子トークンは混ぜない
        other = {
            legacy.synonym_canonical_token(t)
            for t in _hint_tokens(raw, _KIND_HINT_RE)
            if t not in ('帽子', 'キャップ') and t not in specific
        }
        return set(specific) | other
    return {legacy.synonym_canonical_token(t) for t in _hint_tokens(raw, _KIND_HINT_RE)}


def _gender_sides(tokens):
    """性別・対象トークンを male / female 側へ正規化する（対立検出用）。"""
    sides = set()
    for t in tokens or ():
        tok = stock.text(t)
        if tok in _GENDER_FEMALE_TOKENS:
            sides.add('female')
        elif tok in _GENDER_MALE_TOKENS:
            sides.add('male')
    return sides


def _gender_conflict_across(gender_sets):
    """候補名のあいだで男性側と女性側が両方出るときだけ対立とみなす。

    「女性用長袖白衣」対「白衣」のような有無だけの差は対立ではない。
    """
    sides = set()
    for s in gender_sets or ():
        sides |= _gender_sides(s)
    return 'male' in sides and 'female' in sides


def format_provisional_official_name(base_name):
    """代表名文字列を返す。「名称未整理」は名前へ埋め込まず status フラグ側で扱う。"""
    base = stock.text(base_name)
    if not base:
        return ''
    # 旧データ互換: 以前埋め込んでいたタグが残っていれば除去
    cleaned = base.replace(f'［{PROVISIONAL_NAME_TAG}］', '').replace(PROVISIONAL_NAME_TAG, '')
    return stock.text(cleaned) or base


def apply_representative_name_color_split(
    chosen_name,
    *,
    existing_color='',
    lock_name=False,
    lock_color=False,
):
    """代表名決定後の共有色抽出＋商品名分離。

    括弧内・括弧外とも product_ledger_paren.split_name_and_color を使う。
    色が明確なときだけ color を立て、その色だけ名から除去する。
    要確認・複数色は色を立てず名もいじらない。
    lock_name: 既存公式名など名を変えない。
    lock_color: 手動／メーカー採用色を上書きしない。
    白衣の「白」・「2枚入」「七分袖」等は共有辞書／規則のまま残す。
    """
    name = stock.text(chosen_name)
    color = stock.text(existing_color)
    empty = {
        'plannedName': name,
        'color': color,
        'didSplit': False,
        'colorSource': '',
        'changeSummary': '',
        'needsConfirm': False,
    }
    if not name:
        return empty
    try:
        split = paren_classify.split_name_and_color(name)
    except Exception:
        return empty
    if split.get('needsConfirm'):
        return {
            **empty,
            'needsConfirm': True,
            'changeSummary': stock.text(split.get('changeSummary') or ''),
        }
    extracted = stock.text(split.get('color') or '')
    planned = stock.text(split.get('plannedName') or '') or name
    out_name = name
    out_color = color
    did_split = False
    if lock_color and color:
        # 手動／メーカー色は維持。名に同じ色が残っていれば除去だけする
        if (
            not lock_name
            and extracted
            and paren_classify.colors_equivalent(extracted, color)
            and planned
            and planned != name
        ):
            out_name = planned
            did_split = True
    elif extracted:
        if not lock_color:
            out_color = extracted
        if not lock_name and planned and planned != name:
            out_name = planned
            did_split = True
    if lock_name:
        out_name = name
        did_split = False
    return {
        'plannedName': out_name,
        'color': out_color,
        'didSplit': did_split,
        'colorSource': stock.text(split.get('colorSource') or ''),
        'changeSummary': stock.text(split.get('changeSummary') or ''),
        'needsConfirm': False,
    }


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
        # 加工業者は本体の仕入先候補・仕入先差に混ぜない
        if s and s not in suppliers and not proc_svc.is_processing_vendor(s):
            suppliers.append(s)

    # 名差は本体キー（色除去＋シノニム後）。色の有無・括弧位置だけでは言い回し差にしない。
    base_cores = {legacy.product_name_base_key(n) for n in names if n}
    name_wording_diff = len(base_cores) > 1
    supplier_diff = len(suppliers) > 1

    # 先頭「＊/*」だけの差は同一候補。色・男女などのヒント判定では標記を剥がして
    # 本体＋色キーごとに1名使う（＊白衣／白衣を色差の証拠に並べない）。
    # 色違い（青パンツ／赤スラックス）は別ヒントとして残し、色差検出する。
    # パンツ一般『丈加工』もヒント名から除き、加工差の誤検出を防ぐ。
    hint_names = []
    seen_hint_cores = set()
    for n in names:
        core = product_name_core_key(n)
        if not core or core in seen_hint_cores:
            continue
        seen_hint_cores.add(core)
        unmarked, _ = legacy.strip_used_marker(n)
        unmarked = paren_classify.strip_pants_hem_processing(stock.text(unmarked) or n)
        hint_names.append(stock.text(unmarked) or n)

    # 色は明示抽出（括弧＋括弧外辞書）。白衣の「白」などは色にしない。
    # 色なし候補と色あり候補の共存は「色の違い」にしない（異なる明示色同士だけ）。
    color_keys = []
    color_label_names = []
    for n in hint_names:
        info = paren_classify.extract_explicit_color_from_name(n)
        key = stock.text(info.get('colorKey') or '')
        if not key and info.get('colorCandidates'):
            # 複数色で未確定のときも差検出用に候補キーを残す
            cands = [
                paren_classify.color_match_key(c)
                for c in (info.get('colorCandidates') or [])
                if paren_classify.color_match_key(c)
            ]
            key = '|'.join(sorted(set(cands))) if cands else ''
        color_keys.append(key)
        if key:
            color_label_names.append(n)
    proc_sets = [_hint_tokens(n, _PROCESSING_HINT_RE) for n in hint_names]
    kind_sets = [_kind_tokens(n) for n in hint_names]
    gender_sets = [_hint_tokens(n, _GENDER_HINT_RE) for n in hint_names]
    fabric_sets = [_hint_tokens(n, _FABRIC_HINT_RE) for n in hint_names]

    def _set_diff(sets):
        if len(hint_names) < 2:
            return False
        nonempty = [s for s in sets if s]
        if len(nonempty) >= 2 and any(s != nonempty[0] for s in nonempty[1:]):
            return True
        if nonempty and any(not s for s in sets):
            return True
        return False

    def _explicit_color_diff(keys):
        """明示色キーの差。空（色なし）は衝突に数えない。緑≠蛍光グリーン。"""
        if len(hint_names) < 2:
            return False
        nonempty = [k for k in keys if k]
        if len(nonempty) < 2:
            return False
        return any(k != nonempty[0] for k in nonempty[1:])

    color_diff = _explicit_color_diff(color_keys)
    processing_diff = _set_diff(proc_sets)
    # 商品種はシノニム正規化済み。差があれば個別確認。
    kind_diff = False
    if len(hint_names) >= 2:
        nonempty_kinds = [s for s in kind_sets if s]
        if len(nonempty_kinds) >= 2 and any(s != nonempty_kinds[0] for s in nonempty_kinds[1:]):
            kind_diff = True
    gender_diff = _set_diff(gender_sets)
    gender_conflict = _gender_conflict_across(gender_sets)
    # 有無だけの差（例: 女性用あり／なし）も gender_diff。対立は別フラグ。
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
            'values': list(color_label_names),
        })
        reasons.append({
            'code': 'color',
            'label': '色の違いの可能性（個別確認）',
            'values': list(color_label_names),
        })
    if processing_diff:
        other_diffs.append({
            'code': 'processing',
            'label': '加工の違いの可能性',
            'values': [n for n, s in zip(hint_names, proc_sets) if s],
        })
        reasons.append({
            'code': 'processing',
            'label': '加工の違いの可能性（個別確認）',
            'values': [n for n, s in zip(hint_names, proc_sets) if s],
        })
    if kind_diff:
        other_diffs.append({
            'code': 'kind',
            'label': '商品種の違いの可能性',
            'values': [n for n, s in zip(hint_names, kind_sets) if s],
        })
        reasons.append({
            'code': 'kind',
            'label': '商品種の違いの可能性（個別確認）',
            'values': [n for n, s in zip(hint_names, kind_sets) if s],
        })
    if gender_diff:
        gender_label = (
            '男女・対象の明示対立（個別確認）'
            if gender_conflict
            else '男女・対象の違いの可能性（個別確認）'
        )
        other_diffs.append({
            'code': 'gender',
            'label': (
                '男女・対象の明示対立'
                if gender_conflict
                else '男女・対象の違いの可能性'
            ),
            'values': [n for n, s in zip(hint_names, gender_sets) if s],
        })
        reasons.append({
            'code': 'gender',
            'label': gender_label,
            'values': [n for n, s in zip(hint_names, gender_sets) if s],
        })
    if fabric_diff:
        other_diffs.append({
            'code': 'fabric',
            'label': '生地・素材の違いの可能性',
            'values': [n for n, s in zip(hint_names, fabric_sets) if s],
        })
        reasons.append({
            'code': 'fabric',
            'label': '生地・素材の違いの可能性（個別確認）',
            'values': [n for n, s in zip(hint_names, fabric_sets) if s],
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
        'genderConflictPossible': gender_conflict,
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

    def _note_name_row(n, rn):
        nonlocal earliest_name, earliest_row
        n = stock.text(n)
        if not n:
            return
        if n not in all_csv_names:
            all_csv_names.append(n)
        name_row_map.append({'originalName': n, 'sourceRowNo': rn})
        try:
            rn_i = int(rn)
        except (TypeError, ValueError):
            return
        if earliest_row is None or rn_i < int(earliest_row):
            earliest_row = rn_i
            earliest_name = n

    for v in variants:
        source_rows = v.get('sourceRows') or []
        if source_rows:
            for sr in source_rows:
                _note_name_row(sr.get('productName'), sr.get('rowNo'))
            continue
        for n in v.get('csvNames') or ([v.get('productName')] if v.get('productName') else []):
            for rn in v.get('rowNos') or []:
                _note_name_row(n, rn)

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

    # 既存公式名は維持。新規採用名からはパンツ一般『丈加工』を除く
    if rule_code != 'existing' and rule_code != 'user':
        stripped = paren_classify.strip_pants_hem_processing(chosen)
        if stripped:
            chosen = stripped
    chosen = format_provisional_official_name(chosen) or chosen

    # 代表名決定後: 共有色抽出＋名分離（括弧内外）。
    # 既存公式名は維持。手動／メーカーが明示した色は上書きしない。
    raw_color = stock.text(raw.get('color') or '')
    aux_color = stock.text(aux.get('plannedRegisteredColor') or aux.get('adoptedColor') or '')
    lock_name = rule_code == 'existing'
    lock_color = bool(raw_color)
    seed_color = raw_color or aux_color
    split_res = apply_representative_name_color_split(
        chosen,
        existing_color=seed_color,
        lock_name=lock_name,
        lock_color=lock_color,
    )
    if not lock_name:
        chosen = stock.text(split_res.get('plannedName') or '') or chosen
    plan_color = stock.text(split_res.get('color') or '') or seed_color

    # 原文 → 登録先（代表名）の対応（原文名・色は初期在庫照合用に残す）
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
        'color': plan_color,
        'colorSource': stock.text(split_res.get('colorSource') or ''),
        'colorNameSplit': bool(split_res.get('didSplit') or plan_color),
        'changeSummary': stock.text(split_res.get('changeSummary') or ''),
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


def _name_processing_key(product_name):
    """括弧分類で取り出した加工の照合キー（丈加工は除く）。"""
    raw = stock.text(product_name)
    if not raw:
        return ''
    try:
        split = paren_classify.split_name_and_color(raw)
        proc = stock.text(split.get('processing') or '')
        if not proc:
            return ''
        # パンツ一般『丈加工』だけの差はバリアントにしない
        hem = paren_classify.strip_pants_hem_processing(f'（{proc}）')
        if not stock.text(hem).strip('（）()'):
            return ''
        return identity_match_key(proc)
    except Exception:
        return ''


def sku_variant_key(*, product_name, color='', supplier='', processing=''):
    """CSV内の商品内容バリアント（サイズ・顧客は含めない）。

    商品名は本体キー＋明示色キー＋加工キーで同一視
    （括弧内外・半角全角・名シノニムを吸収。刺繍など実加工差は別バリアント）。
    color / processing は呼び出し側で既に抽出済みのときだけ渡し、空なら名から取る。
    """
    base_key, color_key, _bd, _cd = legacy.product_name_base_and_color(product_name)
    color_t = stock.text(color)
    if color_t:
        try:
            color_key = paren_classify.color_match_key(color_t) or identity_match_key(color_t)
        except Exception:
            color_key = identity_match_key(color_t)
    proc_key = identity_match_key(processing) if stock.text(processing) else _name_processing_key(product_name)
    return '|'.join([
        base_key or product_name_core_key(product_name),
        color_key or '',
        proc_key or '',
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


# 今回の取り込みだけ「今回登録しない」。恒久ルールにはしない。
USER_ROW_EXCLUDE_REASON = '利用者が今回登録しないと指定'
USER_ROW_EXCLUDE_REASON_LEGACY = 'この行を登録しない（今回の取り込み）'
# 自動除外（K2 加工内容不明など）。手動除外と理由を区別する。
K2_AUTO_EXCLUDE_REASON = proc_svc.K2_AUTO_EXCLUDE_REASON
PRODUCT_REGISTRATION_EXCLUDED_LABEL = '商品登録時に除外した項目'


def user_exclude_reason_tokens():
    return (USER_ROW_EXCLUDE_REASON, USER_ROW_EXCLUDE_REASON_LEGACY, K2_AUTO_EXCLUDE_REASON)


def csv_row_identity(csv_or_row):
    """元CSV行の安定識別（表示順・再描画でずれない）。"""
    if not isinstance(csv_or_row, dict):
        return {
            'customer': '', 'code': '', 'productName': '', 'size': '', 'supplier': '',
        }
    csv = csv_or_row.get('csv') if isinstance(csv_or_row.get('csv'), dict) else csv_or_row
    return {
        'customer': stock.text(csv.get('customer') or ''),
        'code': stock.text(csv.get('code') or csv.get('productCode') or ''),
        'productName': stock.text(csv.get('productName') or ''),
        'size': stock.text(csv.get('size') or ''),
        'supplier': stock.text(csv.get('supplier') or ''),
    }


def stable_row_exclusion_key(*, customer='', code='', product_name='', size='', supplier='', **_extra):
    """除外判断の安定キー（row_map_key と同材料）。"""
    return row_map_key(
        customer=customer, code=code, product_name=product_name, size=size, supplier=supplier,
    )


def _parse_row_exclusions(body):
    """preview/commit の rowExclusions を安定キー集合へ。

    形式:
    - { "<stableKey>": true | {excluded:true, ...identity} , ... }
    - [ {customer, code, productName, size, supplier, excluded?} , ... ]
    """
    raw = body.get('rowExclusions') if isinstance(body, dict) else None
    keys = set()
    if isinstance(raw, dict):
        for k, v in raw.items():
            if v is False or v is None:
                continue
            if isinstance(v, dict):
                if v.get('excluded') is False:
                    continue
                ident = csv_row_identity(v)
                if any(ident.values()):
                    keys.add(stable_row_exclusion_key(
                        customer=ident['customer'],
                        code=ident['code'],
                        product_name=ident['productName'],
                        size=ident['size'],
                        supplier=ident['supplier'],
                    ))
                    continue
            sk = stock.text(k)
            if sk:
                keys.add(sk)
    elif isinstance(raw, list):
        for v in raw:
            if not isinstance(v, dict) or v.get('excluded') is False:
                continue
            ident = csv_row_identity(v)
            keys.add(stable_row_exclusion_key(
                customer=ident['customer'],
                code=ident['code'],
                product_name=ident['productName'],
                size=ident['size'],
                supplier=ident['supplier'],
            ))
    return keys


def _mark_row_excluded_from_registration(
    row,
    *,
    reason,
    exclusion_kind='manual',
):
    """1行を今回の登録対象外へ（商品・加工は作らない）。手動／自動を区別。"""
    reason_t = stock.text(reason) or USER_ROW_EXCLUDE_REASON
    row['status'] = STATUS_HOLD
    row['statusLabel'] = STATUS_LABELS[STATUS_HOLD]
    row['canCommit'] = False
    row['skuDecisionNeeded'] = False
    row['deferReason'] = reason_t
    row['processingServiceNeedsConfirm'] = False
    if exclusion_kind == 'auto':
        row['autoRowExcluded'] = True
        row['userRowExcluded'] = False
        row['exclusionKind'] = 'auto'
    else:
        row['userRowExcluded'] = True
        row['autoRowExcluded'] = False
        row['exclusionKind'] = 'manual'
    drop_tokens = user_exclude_reason_tokens()
    reasons = [
        r for r in (row.get('reasons') or [])
        if not any(tok in stock.text(r) for tok in drop_tokens)
    ]
    reasons.append(reason_t)
    row['reasons'] = reasons
    # 加工サービス計画から外すための表示フラグ
    pc = dict(row.get('parenClassification') or {})
    pc['processingServiceView'] = True
    pc['rowExcludedView'] = True
    pc['exclusionKind'] = row['exclusionKind']
    pc['exclusionReason'] = reason_t
    row['parenClassification'] = pc
    row['processingServiceView'] = True


def _prune_excluded_from_processing_plans(processing_split_meta, excluded_set):
    dropped_plans = 0
    meta = processing_split_meta if isinstance(processing_split_meta, dict) else {}
    plans = list(meta.get('processingServicePlans') or [])
    kept = []
    for plan in plans:
        src = [
            n for n in (plan.get('sourceRowNos') or [])
            if n not in excluded_set
        ]
        if not src:
            dropped_plans += 1
            continue
        plan = dict(plan)
        plan['sourceRowNos'] = src
        kept.append(plan)
    if plans:
        meta['processingServicePlans'] = kept
    return dropped_plans


def _sync_sku_groups_after_row_exclusions(classified, sku_groups):
    """除外後の品番グループ: 全行除外なら未決定から外す（他対象は維持）。"""
    rows_by_code = defaultdict(list)
    for row in classified or []:
        code = stock.text((row.get('csv') or {}).get('code') or '')
        if stock_product_merge.is_normal_product_code(code):
            rows_by_code[code].append(row)
    for group in sku_groups or []:
        code = stock.text(group.get('code') or '')
        grow = rows_by_code.get(code) or []
        if not grow:
            continue
        active = [
            r for r in grow
            if not r.get('userRowExcluded') and not r.get('autoRowExcluded')
        ]
        group['userExcludedRowNos'] = [
            r.get('rowNo') for r in grow
            if r.get('userRowExcluded') or r.get('autoRowExcluded')
        ]
        group['activeRowNos'] = [r.get('rowNo') for r in active]
        if not active and group.get('needsDecision'):
            # 除外理由は先頭の除外行から（手動優先表示）
            reason = USER_ROW_EXCLUDE_REASON
            kind = 'manual'
            for r in grow:
                if r.get('userRowExcluded'):
                    reason = r.get('deferReason') or USER_ROW_EXCLUDE_REASON
                    kind = 'manual'
                    break
                if r.get('autoRowExcluded'):
                    reason = r.get('deferReason') or K2_AUTO_EXCLUDE_REASON
                    kind = 'auto'
            group['needsDecision'] = False
            group['userAllRowsExcluded'] = True
            group['decision'] = {
                'decision': SKU_DECISION_HOLD,
                'deferReason': reason,
                'userRowExcluded': kind == 'manual',
                'autoRowExcluded': kind == 'auto',
                'exclusionKind': kind,
            }


def _apply_k2_auto_exclusions(classified, sku_groups, processing_split_meta):
    """K2スタッフ工房で加工名不明の行を確認待ちから外し、登録しないへ移す。

    - 当該加工元行だけを除外（同品番の正常な商品本体は巻き込まない）
    - 別行の加工名は借用しない（判定は行単位で完了済み）
    - 利用者が明示復帰した行は再適用しない
    """
    excluded_nos = []
    for row in classified or []:
        info = row.get('processingService') if isinstance(row.get('processingService'), dict) else {}
        if not info.get('k2AutoExclude'):
            continue
        if info.get('identifiable'):
            continue
        pc = row.get('parenClassification') if isinstance(row.get('parenClassification'), dict) else {}
        if pc.get('userRestoredFromAutoExclude'):
            continue
        if row.get('userRowExcluded'):
            # 手動除外が優先（理由は手動のまま）
            continue
        reason = stock.text(info.get('autoExcludeReason') or '') or K2_AUTO_EXCLUDE_REASON
        _mark_row_excluded_from_registration(row, reason=reason, exclusion_kind='auto')
        excluded_nos.append(row.get('rowNo'))

    excluded_set = {n for n in excluded_nos if n is not None}
    dropped = _prune_excluded_from_processing_plans(processing_split_meta, excluded_set)
    _sync_sku_groups_after_row_exclusions(classified, sku_groups)
    return {
        'autoExcludedRowCount': len(excluded_nos),
        'autoExcludedRowNos': excluded_nos,
        'processingPlansDropped': dropped,
    }


def _apply_user_row_exclusions(classified, sku_groups, processing_split_meta, exclusion_keys):
    """ユーザーが選んだ元行だけを今回の登録対象から外す（STATUS_HOLD 再利用）。

    - 未除外の同行グループ／他対象の予定・確認は維持
    - 全行除外の品番は未決定ブロックから外す
    - 加工サービス計画の sourceRowNos からも外す（空なら計画削除）
    - 自動整理・メーカー補正のあとに呼ぶ（勝手に登録対象へ戻さない）
    - 自動除外済みの行は手動理由で上書き可
    """
    keys = set(exclusion_keys or [])
    if not keys:
        return {
            'excludedRowCount': 0,
            'excludedRowNos': [],
            'processingPlansDropped': 0,
        }
    excluded_nos = []
    for row in classified or []:
        ident = csv_row_identity(row)
        sk = stable_row_exclusion_key(
            customer=ident['customer'],
            code=ident['code'],
            product_name=ident['productName'],
            size=ident['size'],
            supplier=ident['supplier'],
        )
        if sk not in keys:
            # 既存のユーザー除外フラグを自動処理が付け直さないよう、未指定なら落とす
            if row.get('userRowExcluded') and not row.get('skuHold'):
                pass
            continue
        _mark_row_excluded_from_registration(
            row, reason=USER_ROW_EXCLUDE_REASON, exclusion_kind='manual',
        )
        excluded_nos.append(row.get('rowNo'))

    excluded_set = {n for n in excluded_nos if n is not None}
    dropped_plans = _prune_excluded_from_processing_plans(processing_split_meta, excluded_set)
    _sync_sku_groups_after_row_exclusions(classified, sku_groups)
    return {
        'excludedRowCount': len(excluded_nos),
        'excludedRowNos': excluded_nos,
        'processingPlansDropped': dropped_plans,
    }


def lookup_product_registration_exclusion(c, *, customer, code, product_name, size, supplier):
    """商品登録時に「この行を登録しない」とした記録（product_id なしのマップ）。"""
    ensure_schema(c)
    key = row_map_key(
        customer=customer, code=code, product_name=product_name, size=size, supplier=supplier,
    )
    row = c.execute(
        '''SELECT * FROM product_ledger_row_maps
           WHERE row_map_key=? ORDER BY id DESC LIMIT 1''',
        (key,),
    ).fetchone()
    if not row:
        return None
    try:
        spec = json.loads(row['spec_json'] or '') or {}
    except (TypeError, ValueError, json.JSONDecodeError):
        spec = {}
    if not isinstance(spec, dict) or not spec.get('excludedFromRegistration'):
        return None
    return {
        'mapId': row['id'],
        'rowMapKey': key,
        'sourceRowNo': row['source_row_no'],
        'label': PRODUCT_REGISTRATION_EXCLUDED_LABEL,
        'productId': row['product_id'],
        'runId': row['run_id'],
    }


def upsert_exclusion_row_map(c, *, run_id, file_sha256, file_name, row):
    """除外判断だけを永続化（商品・取扱・在庫は作らない）。手動／自動理由を保持。"""
    ensure_schema(c)
    csv_info = row.get('csv') or {}
    customer = stock.text(csv_info.get('customer'))
    supplier = stock.text(csv_info.get('supplier'))
    code = stock.text(csv_info.get('sourceCode') or csv_info.get('code'))
    product_name = stock.text(csv_info.get('productName'))
    size = stock.text(csv_info.get('size'))
    pc = row.get('parenClassification') if isinstance(row.get('parenClassification'), dict) else {}
    ps = row.get('processingService') if isinstance(row.get('processingService'), dict) else {}
    exclusion_kind = stock.text(row.get('exclusionKind') or '') or (
        'auto' if row.get('autoRowExcluded') else 'manual'
    )
    exclusion_reason = stock.text(row.get('deferReason') or '') or (
        K2_AUTO_EXCLUDE_REASON if exclusion_kind == 'auto' else USER_ROW_EXCLUDE_REASON
    )
    spec_json = json.dumps({
        'excludedFromRegistration': True,
        'exclusionScope': 'this_import',
        'exclusionKind': exclusion_kind,
        'exclusionReason': exclusion_reason,
        'originalName': product_name,
        'sourceName': product_name,
        'processing': stock.text(pc.get('processing') or ps.get('serviceName') or ''),
        'processingVendor': stock.text(ps.get('vendorName') or supplier or ''),
        'processingServiceName': stock.text(ps.get('serviceName') or ''),
        'rowKind': 'excluded_from_registration',
        'csvSize': size,
        'k2AutoExclude': bool(row.get('autoRowExcluded') or ps.get('k2AutoExclude')),
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
        '', '', '', 0, spec_json,
        None, None, None, run_id, t,
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
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        vals + (t,),
    )
    return c.execute('SELECT last_insert_rowid() AS id').fetchone()['id']


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


def _request_decode_cache(body):
    if not isinstance(body, dict):
        return {}
    cache = body.get('_decodeCache')
    if not isinstance(cache, dict):
        cache = {}
        body['_decodeCache'] = cache
    return cache


def decode_upload(body):
    cache = _request_decode_cache(body)
    raw = body.get('file') if isinstance(body, dict) else None
    raw_key = raw if isinstance(raw, str) else ''
    cached = cache.get('ledger')
    if cached and cache.get('ledgerRaw') == raw_key:
        return cached
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
    result = {
        'fileName': name,
        'fileSha256': sha,
        'fileSize': len(blob),
        'encoding': encoding,
        'fields': fields,
        'rows': rows,
        'blob': blob,
    }
    cache['ledger'] = result
    cache['ledgerRaw'] = raw_key
    return result


def decode_aux_upload(body):
    """初期在庫用CSV（任意）。プレビューではDBを触らず列検証のみ。"""
    if not isinstance(body, dict):
        return None
    cache = _request_decode_cache(body)
    raw = body.get('auxFile') or body.get('openingStockFile')
    raw_key = raw if isinstance(raw, str) else ''
    if 'aux' in cache and cache.get('auxRaw') == raw_key:
        return cache['aux']
    if not isinstance(raw, str) or not raw.strip():
        cache['aux'] = None
        cache['auxRaw'] = raw_key
        return None
    # legacy の decode を再利用（列検証のみ。DB・在庫は触らない）
    aux_body = {
        'file': raw,
        'name': body.get('auxName') or body.get('openingStockFileName') or '初期在庫.csv',
    }
    uploaded = legacy.decode_upload(aux_body)
    result = {
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'fileSize': uploaded['fileSize'],
        'encoding': uploaded['encoding'],
        'fields': uploaded['fields'],
        'rows': uploaded['rows'],
        'ignoredColumns': list(legacy.IGNORED_QTY_COLUMNS),
    }
    cache['aux'] = result
    cache['auxRaw'] = raw_key
    return result


def resolve_import_mode(body, aux_uploaded=None):
    """取込モード: products / opening_stock / combined（後方互換）。"""
    if not isinstance(body, dict):
        return IMPORT_MODE_PRODUCTS
    raw = stock.text(body.get('importMode') or body.get('mode') or '').lower()
    if raw in ('products', 'products_only', 'product', 'ledger', 'stage1'):
        return IMPORT_MODE_PRODUCTS
    if raw in ('opening_stock', 'opening', 'stock', 'initial_stock', 'stage2'):
        return IMPORT_MODE_OPENING
    # 明示モードなし: 旧 registerOpeningStock フラグのみ後方互換で同時登録
    if aux_uploaded is None:
        has_aux = bool(body.get('auxFile') or body.get('openingStockFile'))
        aux_uploaded = True if has_aux and body.get('registerOpeningStock') else None
        if has_aux and not body.get('registerOpeningStock'):
            return IMPORT_MODE_PRODUCTS
        if has_aux and body.get('registerOpeningStock'):
            return IMPORT_MODE_COMBINED
        return IMPORT_MODE_PRODUCTS
    if aux_uploaded and body.get('registerOpeningStock'):
        return IMPORT_MODE_COMBINED
    return IMPORT_MODE_PRODUCTS


def register_opening_stock_requested(body, aux_uploaded):
    """初期在庫の準備・登録が必要か。

    importMode=products では常に False（判定補助CSVがあっても在庫準備しない）。
    importMode=opening_stock / combined（旧フラグ）では True。
    """
    mode = resolve_import_mode(body, aux_uploaded)
    if mode == IMPORT_MODE_PRODUCTS:
        return False
    if mode == IMPORT_MODE_OPENING:
        return bool(aux_uploaded)
    if not aux_uploaded or not isinstance(body, dict):
        return False
    return bool(body.get('registerOpeningStock'))


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


def _summarize_aux_stock_rows(size_rows):
    """サイズ別在庫行から表示用の合計を作る（比較一覧・理由文用）。"""
    rows = list(size_rows or [])
    if not rows:
        return {
            'auxStockTotal': None,
            'auxStockDisplay': '—',
            'hasAuxMatch': False,
            'qtyUndecidable': False,
        }
    total = 0
    any_ok = False
    undecidable = False
    for row in rows:
        if row.get('qtyStatus') != 'ok' or row.get('qty') is None:
            undecidable = True
            continue
        any_ok = True
        try:
            total += int(row['qty'])
        except (TypeError, ValueError):
            total += row['qty']
    if undecidable and not any_ok:
        return {
            'auxStockTotal': None,
            'auxStockDisplay': '（不正/空）',
            'hasAuxMatch': True,
            'qtyUndecidable': True,
        }
    display = str(total)
    if undecidable:
        display = f'{total}（一部不正）'
    return {
        'auxStockTotal': total if any_ok else None,
        'auxStockDisplay': display,
        'hasAuxMatch': True,
        'qtyUndecidable': undecidable,
    }


def format_aux_stock_label(display):
    """比較一覧用の短い在庫ラベル。"""
    text = stock.text(display or '')
    if not text or text == '—':
        return '—'
    if text.startswith('（') or text.startswith('('):
        return text
    return f'初期在庫：{text}'


def _supplier_names_match(a, b):
    ta, tb = stock.text(a), stock.text(b)
    if not ta or not tb:
        return False
    if identity_match_key(ta) == identity_match_key(tb):
        return True
    return legacy.looks_like_company_form_variant(ta, tb)


def _aux_row_matches_candidate(aux_item, *, code, supplier, product_name, customers):
    """台帳側の仕入先候補へ、品番＋仕入先＋商品名（＋顧客）で紐づける。品番だけでは結びつけない。

    品番の上下表記ゆれ（VEH30B / VEH30B 下 / VEH30B下）は、商品名側の上下と矛盾しないときだけ許容。
    上下が特定できない基礎品番だけでは上・下のどちらへも自動振り分けしない。
    """
    aux_code = stock.text(aux_item.get('商品コード'))
    ledger_code = stock.text(code)
    aux_name = stock.text(aux_item.get('商品名'))
    if not aux_code or not ledger_code:
        return False
    aux_pos_info = position_sku.resolve_garment_position(code=aux_code, product_name=aux_name)
    led_pos_info = position_sku.resolve_garment_position(code=ledger_code, product_name=product_name)
    aux_reg = stock.text(aux_pos_info.get('registeredCode') or aux_code)
    led_reg = stock.text(led_pos_info.get('registeredCode') or ledger_code)
    aux_base = stock.text(aux_pos_info.get('baseCode') or aux_code)
    led_base = stock.text(led_pos_info.get('baseCode') or ledger_code)
    aux_pos = stock.text(aux_pos_info.get('position') or '')
    led_pos = stock.text(led_pos_info.get('position') or '')
    code_ok = False
    if aux_code == ledger_code or aux_reg == led_reg:
        code_ok = True
    elif aux_base and aux_base == led_base:
        # 基礎一致時は上下が両方特定でき一致するときだけ
        if aux_pos and led_pos and aux_pos == led_pos:
            code_ok = True
        elif not aux_pos and not led_pos:
            code_ok = True
        else:
            code_ok = False
    if not code_ok:
        return False
    check_code = aux_reg or aux_code
    if legacy.is_custom_order_code(check_code) or not stock_product_merge.is_normal_product_code(
        aux_base or check_code
    ):
        return False
    if not _supplier_names_match(aux_item.get('仕入先'), supplier):
        return False
    if product_name_core_key(aux_name) != product_name_core_key(product_name):
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
    """品番文字列で束ねた補助索引（在庫数量は判定時に読む）。

    原文・密着・スペース付き・基礎品番の各キーへ同じ行を載せる（上下照合は行マッチ側）。
    """
    by_code = defaultdict(list)
    for row_no, item in aux_rows or []:
        code = stock.text(item.get('商品コード'))
        if not code:
            continue
        seen_keys = set()
        for key in position_sku.sku_code_variants(code):
            if key in seen_keys:
                continue
            seen_keys.add(key)
            by_code[key].append((row_no, item))
        base, _pos = position_sku.strip_sku_position_suffix(code)
        if base and base not in seen_keys:
            by_code[base].append((row_no, item))
    return by_code


def _aux_rows_for_code(aux_index, code):
    """品番表記ゆれを吸収して補助行を返す（行番号で一意化）。"""
    if not aux_index:
        return []
    out = []
    seen = set()
    for key in position_sku.sku_code_variants(code):
        for row_no, item in aux_index.get(key, []):
            if row_no in seen:
                continue
            seen.add(row_no)
            out.append((row_no, item))
    base, _pos = position_sku.strip_sku_position_suffix(code)
    if base:
        for row_no, item in aux_index.get(base, []):
            if row_no in seen:
                continue
            seen.add(row_no)
            out.append((row_no, item))
    return out


def _collect_supplier_candidates(group):
    """同一商品候補内の仕入先一覧（台帳側名・サイズ・顧客）。

    加工業者（K2・エルムス等）は商品本体の通常仕入先候補へ混ぜない。
    """
    by_sup = {}
    for v in group.get('variants') or []:
        sup = stock.text(v.get('supplier') or '')
        if not sup:
            continue
        if proc_svc.is_processing_vendor(sup):
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
        for row_no, item in _aux_rows_for_code(aux_index, code):
            for name in names:
                if _aux_row_matches_candidate(
                    item, code=code, supplier=supplier, product_name=name, customers=customers,
                ):
                    matched.append((row_no, item))
                    break

    size_rows = []
    matched_aux_rows = []
    has_positive = False
    has_nonzero = False
    undecidable = False
    undecidable_reasons = []
    for row_no, item in matched:
        size = stock.text(item.get('サイズ'))
        qty, qstatus = parse_aux_stock_qty(item.get('在庫'))
        # 初期在庫登録用に原文列を保持（判定後に改名で再照合しない）
        matched_aux_rows.append({
            'rowNo': row_no,
            'code': stock.text(item.get('商品コード')),
            'supplier': stock.text(item.get('仕入先')),
            'customer': stock.text(item.get('顧客')),
            'productName': stock.text(item.get('商品名')),
            'size': size,
            'conditionRaw': stock.text(item.get('新古')),
            'site': stock.text(item.get('拠点')),
            'shelf': stock.text(item.get('棚')),
            'unitCostRaw': stock.text(item.get('仕入単価')),
            'qtyRaw': stock.text(item.get('在庫')),
            'qty': qty if qstatus == 'ok' else None,
            'qtyStatus': qstatus,
        })
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

    stock_summary = _summarize_aux_stock_rows(size_rows)
    return {
        'supplier': supplier,
        'ledgerNames': list(names),
        'ledgerName': prefer_unmarked_product_name(names) or (names[0] if names else ''),
        'auxStatus': aux_status,
        'auxStatusLabel': aux_status_label,
        'auxSizeStocks': size_rows,
        'matchedAuxRows': matched_aux_rows,
        'hasAnyAuxRows': bool(matched),
        'hasPositiveStock': bool(has_positive and not undecidable),
        'hasNonZeroStock': bool(has_nonzero and not undecidable),
        'qtyUndecidable': undecidable,
        'qtyUndecidableReasons': undecidable_reasons,
        'auxStockTotal': stock_summary['auxStockTotal'],
        'auxStockDisplay': stock_summary['auxStockDisplay'],
        'customers': list(customers),
        'ledgerSizes': list(candidate.get('sizes') or []),
    }


# 仕入先候補がちょうど1社のときの自動設定理由（プレビュー表示用）
SINGLE_SUPPLIER_AUTO_REASON = '候補が1社のため自動設定'


def _stock_qty_clause(judgment):
    """理由文用の「初期在庫：N」節（サイズ合計）。不明なら空。"""
    if not judgment or judgment.get('auxStockTotal') is None:
        return ''
    return f'初期在庫：{judgment["auxStockTotal"]}'


def _unique_nonzero_adoption_rule(hit):
    """非ゼロ在庫が一意のときの採用理由（商品名＋在庫数を明示）。"""
    name = stock.text((hit or {}).get('productName') or '') or 'この候補'
    if hit and hit.get('auxStockTotal') is not None:
        return (
            f'在庫が0以外の候補が1組のため、'
            f'{name}（在庫{hit["auxStockTotal"]}）を採用'
        )
    return f'在庫が0以外の候補が1組のため、{name}を採用'


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
        qty_bit = _stock_qty_clause(positive[0])
        qty_paren = f'（{qty_bit}）' if qty_bit else ''
        return {
            'autoPrimarySupplier': positive[0]['supplier'],
            'autoReason': (
                f'初期在庫CSVで正の在庫がある仕入先が1社のみ{qty_paren}のため、'
                '通常仕入先にしました。'
            ),
            'needsManualPrimary': False,
            'manualReason': '',
        }
    if len(positive) > 1:
        qty_bits = []
        for j in positive:
            clause = _stock_qty_clause(j)
            if clause:
                qty_bits.append(f'{j.get("supplier") or "?"}={clause}')
            else:
                qty_bits.append(stock.text(j.get('supplier') or '?'))
        detail = '、'.join(qty_bits[:4])
        return {
            'autoPrimarySupplier': '',
            'autoReason': '',
            'needsManualPrimary': True,
            'manualReason': (
                '正の在庫がある仕入先が複数あるため、通常仕入先は手動で選んでください'
                + (f'（{detail}）' if detail else '')
                + '。'
            ),
        }

    with_rows = [j for j in judgments if j.get('hasAnyAuxRows')]
    if len(with_rows) == 1:
        qty_bit = _stock_qty_clause(with_rows[0])
        # 正の在庫なし＝在庫0（または負のみだがこの分岐は正なし）を明示
        stock_note = qty_bit or '在庫0'
        return {
            'autoPrimarySupplier': with_rows[0]['supplier'],
            'autoReason': (
                f'正の在庫はないが（{stock_note}）、初期在庫CSVに該当行がある仕入先が1社のみのため、'
                '通常仕入先にしました。'
            ),
            'needsManualPrimary': False,
            'manualReason': '',
        }
    if len(with_rows) > 1:
        return {
            'autoPrimarySupplier': '',
            'autoReason': '',
            'needsManualPrimary': True,
            'manualReason': (
                '正の在庫がなく（在庫0の該当のみ）、該当行がある仕入先も複数あるため、'
                '通常仕入先は手動で選んでください。'
            ),
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
    """同一商品候補内の 商品名+仕入先 ペア（バリアント単位）。別候補の名と仕入先を混ぜない。

    バリアントキー（本体・色・加工・仕入先）で一意化する。
    刺繍あり／なしなど加工差を core 名だけで潰さない。
    """
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
        # バリアントキー優先（加工差を含む）。無ければ従来の名コア＋仕入先。
        key = stock.text(v.get('key') or '') or (
            f'{product_name_core_key(display_name)}|{identity_match_key(supplier)}'
        )
        if key in seen:
            for slot in out:
                if stock.text(slot.get('variantKey') or '') == key:
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
            'variantKey': key,
            'processing': stock.text(v.get('processing') or ''),
            'color': stock.text(v.get('color') or ''),
        })
    return out


def _full_package_for_candidate(cand):
    """候補1組のフルパッケージ（名・仕入先・色・加工・素材）。別候補と混ぜない。

    商品名は原文（採用ソース）と、色を振り分けた登録予定名を分けて持つ。
    """
    names = list(cand.get('ledgerNames') or [])
    display = prefer_unmarked_product_name(names) or stock.text(cand.get('productName') or '')
    pc = paren_classify.split_name_and_color(display)
    planned = (
        stock.text(pc.get('plannedName') or '')
        or unify_import_notation(display)
        or display
    )
    color = stock.text(pc.get('color') or '')
    change_summary = stock.text(pc.get('changeSummary') or '')
    if not change_summary and color:
        change_summary = f"商品名から色『{color}』を設定します"
    return {
        'productName': display,
        'sourceName': display,
        'plannedName': planned,
        'supplier': stock.text(cand.get('supplier') or ''),
        'ledgerNames': names,
        'color': color,
        'colorKey': stock.text(pc.get('colorKey') or '') or paren_classify.color_match_key(color),
        'colorSource': stock.text(pc.get('colorSource') or ''),
        'sourceColor': stock.text(pc.get('sourceColor') or ''),
        'processing': stock.text(pc.get('processing') or ''),
        'material': stock.text(pc.get('material') or ''),
        'parenNeedsConfirm': bool(pc.get('needsConfirm')),
        'parenConfirmReasons': list(pc.get('confirmReasons') or []),
        'autoClassified': bool(pc.get('autoClassified')),
        'hasExplicitColor': bool(pc.get('hasExplicitColor') or color),
        'changeSummary': change_summary,
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


def _pair_candidate_payload(cand):
    """比較一覧・詳細パネル用の 名+仕入先 候補（在庫表示付き）。"""
    return {
        'productName': cand.get('productName') or '',
        'supplier': cand.get('supplier') or '',
        'ledgerNames': list(cand.get('ledgerNames') or []),
        'auxStockTotal': cand.get('auxStockTotal'),
        'auxPositiveStockTotal': cand.get('auxPositiveStockTotal'),
        'auxStockDisplay': cand.get('auxStockDisplay') or '—',
        'hasAuxMatch': bool(cand.get('hasAuxMatch')),
        'hasNonZeroStock': bool(cand.get('hasNonZeroStock')),
        'hasPositiveStock': bool(cand.get('hasPositiveStock')),
        'hasNegativeStock': bool(cand.get('hasNegativeStock')),
    }


def _candidate_compare_qty(cand):
    """比較表示用の数量。正の在庫合計があればそれを、なければ在庫合計。"""
    if cand.get('auxPositiveStockTotal') is not None and int(cand.get('auxPositiveStockTotal') or 0) > 0:
        return cand['auxPositiveStockTotal']
    if cand.get('auxStockTotal') is not None:
        return cand['auxStockTotal']
    return None


def _format_candidate_stock_comparison(candidates, *, qty_key='compare'):
    """候補ごとの比較数量を「名 qty／名 qty」形式で返す。"""
    bits = []
    for cand in candidates or []:
        name = stock.text(cand.get('productName') or '') or '?'
        if qty_key == 'positive':
            qty = cand.get('auxPositiveStockTotal')
            if qty is None or int(qty or 0) <= 0:
                qty = cand.get('auxStockTotal')
        else:
            qty = _candidate_compare_qty(cand)
        if qty is None:
            bits.append(f'{name} —')
        else:
            bits.append(f'{name} {qty}')
    return '／'.join(bits)


def _adopt_candidate_package(hit, *, candidates, rule, rule_code, reasons=None):
    """採用候補のフルパッケージ＋非採用の原文パッケージを返す。"""
    package = _full_package_for_candidate(hit)
    kept = _kept_packages_excluding(candidates, package)
    # 登録予定名は色振り分け＋表記統一後。括弧／色が曖昧でも表記は統一する。
    chosen_name = registered_name_from_package(package)
    change_summary = stock.text(package.get('changeSummary') or '')
    if not change_summary and package.get('color'):
        change_summary = f"商品名から色『{package['color']}』を設定します"
    package['changeSummary'] = change_summary
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
        'pairCandidates': [_pair_candidate_payload(c) for c in (candidates or [])],
        'nameReasons': list(reasons or []),
        'adoptedPackage': package,
        'keptPackages': kept,
        'adoptedColor': package.get('color') or '',
        'adoptedProcessing': package.get('processing') or '',
        'adoptedMaterial': package.get('material') or '',
        'sourceAdoptedName': package.get('sourceName') or package.get('productName') or '',
        'plannedRegisteredName': chosen_name,
        'plannedRegisteredColor': package.get('color') or '',
        'changeSummary': change_summary,
        'colorDecidedNameUndecided': False,
    }


def _undecided_pair_result(
    *,
    empty,
    name_candidates,
    pair_candidates,
    reasons,
    adopted_color='',
    color_decided_name_undecided=False,
    change_summary='',
    ledger_name_votes=None,
):
    out = {
        **empty,
        'nameCandidates': name_candidates,
        'pairCandidates': pair_candidates,
        'nameReasons': reasons,
        'adoptedColor': stock.text(adopted_color or ''),
        'plannedRegisteredColor': stock.text(adopted_color or ''),
        'colorDecidedNameUndecided': bool(color_decided_name_undecided),
        'changeSummary': stock.text(change_summary or ''),
    }
    if ledger_name_votes is not None:
        out['ledgerNameVotes'] = ledger_name_votes
    return out


def _ledger_source_rows_for_votes(group):
    """商品台帳CSVの各データ行を1票として列挙（同一行の二重計上なし）。"""
    rows = []
    seen = set()
    for v in group.get('variants') or []:
        source_rows = v.get('sourceRows')
        if source_rows:
            for sr in source_rows:
                rn = sr.get('rowNo')
                try:
                    rn_key = int(rn)
                except (TypeError, ValueError):
                    rn_key = rn
                if rn_key in seen:
                    continue
                seen.add(rn_key)
                name = stock.text(sr.get('productName') or '')
                if not name:
                    continue
                rows.append({
                    'rowNo': rn_key,
                    'productName': name,
                    'supplier': stock.text(sr.get('supplier') or v.get('supplier') or ''),
                    'preNormalizeName': name,
                })
            continue
        # フォールバック: バリアント代表名で各行を1票（sourceRows 未付与時）
        display = (
            prefer_unmarked_product_name(v.get('csvNames') or [])
            or stock.text(v.get('productName') or '')
        )
        if not display:
            continue
        for rn in v.get('rowNos') or []:
            try:
                rn_key = int(rn)
            except (TypeError, ValueError):
                rn_key = rn
            if rn_key in seen:
                continue
            seen.add(rn_key)
            rows.append({
                'rowNo': rn_key,
                'productName': display,
                'supplier': stock.text(v.get('supplier') or ''),
                'preNormalizeName': display,
            })
    return rows


def _count_ledger_name_votes(group):
    """正規化名ごとの票数（商品台帳行のみ。初期在庫行・在庫数は混ぜない）。

    色省略の寄せはバリアントまとめ側で行い、票カウントでは色なし行を
    推測で色付き票へ振り分けない（明示色が複数あるSKUの票を守る）。
    """
    buckets = {}
    order = []
    for row in _ledger_source_rows_for_votes(group):
        key = normalize_ledger_name_for_vote(row['productName'])
        if not key:
            continue
        slot = buckets.get(key)
        if not slot:
            slot = {
                'normalizedName': key,
                'count': 0,
                'preNormalizeNames': [],
                'rowNos': [],
            }
            buckets[key] = slot
            order.append(key)
        slot['count'] += 1
        raw = row['preNormalizeName']
        if raw and raw not in slot['preNormalizeNames']:
            slot['preNormalizeNames'].append(raw)
        if row['rowNo'] not in slot['rowNos']:
            slot['rowNos'].append(row['rowNo'])
    return [buckets[k] for k in order]


def _all_candidates_confirmed_stock_zero(candidates):
    """全候補が「確認済み在庫0」か。

    初期在庫CSVなし・該当行なし・照合不能・数量不正/空は在庫0扱いしない。
    """
    if not candidates:
        return False
    for cand in candidates:
        if not cand.get('hasAuxMatch'):
            return False
        if cand.get('auxStockTotal') is None:
            return False
        if cand.get('hasNonZeroStock') or cand.get('hasPositiveStock') or cand.get('hasNegativeStock'):
            return False
        try:
            if int(cand.get('auxStockTotal') or 0) != 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _candidate_matches_vote_key(cand, vote_key):
    key = stock.text(vote_key or '')
    if not key:
        return False
    names = list(cand.get('ledgerNames') or [])
    display = stock.text(cand.get('productName') or '')
    if display and display not in names:
        names = [display, *names]
    for n in names:
        if normalize_ledger_name_for_vote(n) == key:
            return True
    return False


def _full_package_for_adopted_ledger_name(cand, adopted_name):
    """票で決めた正規化名をソースに色・加工・素材を振り分けたパッケージ。"""
    names = list(cand.get('ledgerNames') or [])
    display = format_ledger_adopted_name(adopted_name) or stock.text(adopted_name or '')
    pc = paren_classify.split_name_and_color(display)
    planned = stock.text(pc.get('plannedName') or '') or display
    color = stock.text(pc.get('color') or '')
    change_summary = stock.text(pc.get('changeSummary') or '')
    if not change_summary and color:
        change_summary = f"商品名から色『{color}』を設定します"
    return {
        'productName': display,
        'sourceName': display,
        'plannedName': planned,
        'supplier': stock.text(cand.get('supplier') or ''),
        'ledgerNames': names,
        'color': color,
        'colorKey': stock.text(pc.get('colorKey') or '') or paren_classify.color_match_key(color),
        'colorSource': stock.text(pc.get('colorSource') or ''),
        'sourceColor': stock.text(pc.get('sourceColor') or ''),
        'processing': stock.text(pc.get('processing') or ''),
        'material': stock.text(pc.get('material') or ''),
        'parenNeedsConfirm': bool(pc.get('needsConfirm')),
        'parenConfirmReasons': list(pc.get('confirmReasons') or []),
        'autoClassified': bool(pc.get('autoClassified')),
        'hasExplicitColor': bool(pc.get('hasExplicitColor') or color),
        'changeSummary': change_summary,
        'sizes': list(cand.get('sizes') or []),
        'customers': list(cand.get('customers') or []),
        'variantKey': cand.get('variantKey') or '',
    }


def _pick_candidate_for_vote_name(matching):
    """採用名に紐づく候補から仕入先を既存規則で選ぶ（行順では選ばない）。"""
    pool = list(matching or [])
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]
    suppliers = []
    seen = set()
    for c in pool:
        s = stock.text(c.get('supplier') or '')
        key = identity_match_key(s)
        if not s or key in seen:
            continue
        seen.add(key)
        suppliers.append(c)
    if len(suppliers) == 1:
        return suppliers[0]
    positive = [c for c in pool if c.get('hasPositiveStock')]
    if len(positive) == 1:
        return positive[0]
    with_rows = [c for c in pool if c.get('hasAuxMatch')]
    if len(with_rows) == 1:
        return with_rows[0]
    # 複数仕入先かつ在庫では決まらない → 行順では選ばない
    return None


def _adopt_ledger_max_name_package(
    hit,
    *,
    candidates,
    adopted_name,
    vote_count,
    votes,
    reasons=None,
):
    """最多名称採用のフルパッケージ結果。"""
    package = _full_package_for_adopted_ledger_name(hit, adopted_name)
    kept = []
    win_key = normalize_ledger_name_for_vote(adopted_name)
    for cand in candidates or []:
        if _candidate_matches_vote_key(cand, win_key):
            # 同一正規化名の別仕入先は別名パッケージではなく仕入先差として残す
            if identity_match_key(cand.get('supplier') or '') == identity_match_key(
                hit.get('supplier') or ''
            ):
                continue
            kept.append(_full_package_for_candidate(cand))
            continue
        kept.append(_full_package_for_candidate(cand))
    chosen_name = registered_name_from_package(package)
    change_summary = stock.text(package.get('changeSummary') or '')
    if not change_summary and package.get('color'):
        change_summary = f"商品名から色『{package['color']}』を設定します"
    package['changeSummary'] = change_summary
    rule = (
        f'全候補の在庫が0のため、商品台帳で最多の名称を採用'
        f'（表記違いを合算：{int(vote_count)}行）'
    )
    return {
        'suggestedOfficialName': chosen_name,
        'suggestedPrimarySupplier': package['supplier'],
        'officialNameNeedsChoice': False,
        'pairNeedsChoice': False,
        'nameAdoptionRule': rule,
        'nameAdoptionRuleCode': 'ledger_max_name_zero_stock',
        'pairAdoptionRule': rule,
        'pairAdoptionRuleCode': 'ledger_max_name_zero_stock',
        'nameCandidates': [
            c.get('productName') for c in (candidates or []) if c.get('productName')
        ],
        'pairCandidates': [_pair_candidate_payload(c) for c in (candidates or [])],
        'nameReasons': list(reasons or []),
        'adoptedPackage': package,
        'keptPackages': kept,
        'adoptedColor': package.get('color') or '',
        'adoptedProcessing': package.get('processing') or '',
        'adoptedMaterial': package.get('material') or '',
        'sourceAdoptedName': package.get('sourceName') or package.get('productName') or '',
        'plannedRegisteredName': chosen_name,
        'plannedRegisteredColor': package.get('color') or '',
        'changeSummary': change_summary,
        'colorDecidedNameUndecided': False,
        'ledgerNameVotes': votes,
        'ledgerNameVoteAdopted': {
            'normalizedName': win_key,
            'count': int(vote_count),
            'preNormalizeNames': list(
                next(
                    (v.get('preNormalizeNames') or [] for v in (votes or [])
                     if v.get('normalizedName') == win_key),
                    [],
                )
            ),
        },
    }


def _try_ledger_max_name_adoption(
    group,
    *,
    candidates,
    name_candidates,
    pair_candidates,
    reasons,
    empty,
):
    """全候補が確認済み在庫0のとき、商品台帳の最多正規化名を採用する。

    明示色優先よりこちらを優先。同数最多は要確認（候補＋票数を示す）。
    """
    votes = _count_ledger_name_votes(group)
    if not votes:
        return None
    max_count = max(int(v.get('count') or 0) for v in votes)
    winners = [v for v in votes if int(v.get('count') or 0) == max_count]
    vote_payload = [
        {
            'normalizedName': v.get('normalizedName') or '',
            'count': int(v.get('count') or 0),
            'preNormalizeNames': list(v.get('preNormalizeNames') or []),
            'rowNos': list(v.get('rowNos') or []),
        }
        for v in votes
    ]
    if len(winners) != 1:
        bits = [
            f'{v.get("normalizedName") or "?"}（{int(v.get("count") or 0)}行）'
            for v in sorted(winners, key=lambda x: x.get('normalizedName') or '')
        ]
        tie_reasons = list(reasons or []) + [
            '全候補の在庫が0だが、商品台帳の最多名称が同数のため要確認：'
            f'{"／".join(bits)}。手動でメイン候補を選んでください。'
        ]
        return _undecided_pair_result(
            empty=empty,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates,
            reasons=tie_reasons,
            ledger_name_votes=vote_payload,
        )

    win = winners[0]
    win_key = win.get('normalizedName') or ''
    adopted_name = format_ledger_adopted_name(win_key) or win_key
    matching = [c for c in candidates if _candidate_matches_vote_key(c, win_key)]
    if not matching:
        # 票はあるが候補照合できない（理論上まれ）→ 要確認
        return _undecided_pair_result(
            empty=empty,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates,
            reasons=list(reasons or []) + [
                '商品台帳の最多名称に対応する候補を特定できません（要確認）。'
            ],
            ledger_name_votes=vote_payload,
        )
    hit = _pick_candidate_for_vote_name(matching)
    if hit is None:
        # 名称は決まっても仕入先は既存規則で未決 → 名称提案＋仕入先要確認（行順では選ばない）
        package = _full_package_for_adopted_ledger_name(matching[0], adopted_name)
        package['supplier'] = ''
        chosen_name = registered_name_from_package(package) or adopted_name
        rule = (
            f'全候補の在庫が0のため、商品台帳で最多の名称を採用'
            f'（表記違いを合算：{int(win.get("count") or 0)}行）。'
            '仕入先は複数あるため手動で選んでください（行順では選びません）。'
        )
        return {
            **empty,
            'suggestedOfficialName': chosen_name,
            'suggestedPrimarySupplier': '',
            'officialNameNeedsChoice': False,
            'pairNeedsChoice': True,
            'nameAdoptionRule': rule,
            'nameAdoptionRuleCode': 'ledger_max_name_zero_stock',
            'pairAdoptionRule': rule,
            'pairAdoptionRuleCode': 'ledger_max_name_zero_stock',
            'nameCandidates': name_candidates,
            'pairCandidates': pair_candidates,
            'nameReasons': list(reasons or []) + [rule],
            'adoptedPackage': package,
            'keptPackages': [
                _full_package_for_candidate(c)
                for c in candidates
                if not _candidate_matches_vote_key(c, win_key)
            ],
            'adoptedColor': package.get('color') or '',
            'adoptedProcessing': package.get('processing') or '',
            'adoptedMaterial': package.get('material') or '',
            'sourceAdoptedName': package.get('sourceName') or '',
            'plannedRegisteredName': chosen_name,
            'plannedRegisteredColor': package.get('color') or '',
            'changeSummary': package.get('changeSummary') or '',
            'colorDecidedNameUndecided': False,
            'ledgerNameVotes': vote_payload,
            'ledgerNameVoteAdopted': {
                'normalizedName': win_key,
                'count': int(win.get('count') or 0),
                'preNormalizeNames': list(win.get('preNormalizeNames') or []),
            },
            'nameDecidedSupplierUndecided': True,
        }

    return _adopt_ledger_max_name_package(
        hit,
        candidates=candidates,
        adopted_name=adopted_name,
        vote_count=int(win.get('count') or 0),
        votes=vote_payload,
        reasons=reasons,
    )


def _try_explicit_color_adoption(
    pool,
    *,
    all_candidates,
    name_candidates,
    pair_candidates,
    reasons,
    empty,
):
    """在庫で決まらないとき、明示色がある候補を優先する。

    - 色なし候補は色ありとの「色違い」にしない
    - 色あり同士で色キーが異なる → 自動確定しない
    - 色ありが一意（他は色なし）→ その候補を採用し名／色を振り分け
    - 同じ色が複数名・仕入先に付く → 色は提案、名は要確認
    """
    if not pool:
        return None
    annotated = []
    for cand in pool:
        pkg = _full_package_for_candidate(cand)
        key = stock.text(pkg.get('colorKey') or '')
        if not key and pkg.get('color'):
            key = paren_classify.color_match_key(pkg.get('color') or '')
        annotated.append({
            'cand': cand,
            'package': pkg,
            'colorKey': key,
            'hasColor': bool(key),
        })
    colored = [a for a in annotated if a['hasColor']]
    if not colored:
        return None

    unique_keys = {a['colorKey'] for a in colored}
    if len(unique_keys) > 1:
        bits = []
        for a in colored:
            bits.append(
                f'{a["package"].get("productName") or "?"}（色:{a["package"].get("color") or "?"}）'
            )
        reasons = list(reasons or [])
        reasons.append(
            '候補の商品名に異なる色が明示されているため、色だけではメインを'
            f'自動確定しません：{"／".join(bits)}。手動で選んでください。'
        )
        return _undecided_pair_result(
            empty=empty,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates or [_pair_candidate_payload(c) for c in all_candidates],
            reasons=reasons,
        )

    color_key = next(iter(unique_keys))
    color_display = colored[0]['package'].get('color') or color_key
    # プール内で色あり候補が一意 → 採用
    if len(colored) == 1:
        hit = colored[0]['cand']
        pkg = colored[0]['package']
        rule = (
            f'商品名に色「{pkg.get("color") or color_display}」が明示されているため採用'
            f'（在庫では代表が決まらなかった候補から）'
        )
        adopted = _adopt_candidate_package(
            hit,
            candidates=all_candidates,
            rule=rule,
            rule_code='explicit_color_name',
            reasons=reasons,
        )
        # ペア候補表示は呼び出し側の在庫付きを優先
        if pair_candidates:
            adopted['pairCandidates'] = pair_candidates
        if name_candidates:
            adopted['nameCandidates'] = name_candidates
        return adopted

    # 同じ色が複数候補 → 色は決まっても名・加工・仕入先パッケージが未決なら要確認
    cores = {
        stock.text(a['cand'].get('variantKey') or '') or (
            product_name_core_key(a['cand'].get('productName') or ''),
            identity_match_key(a['cand'].get('supplier') or ''),
            identity_match_key(a['cand'].get('processing') or ''),
        )
        for a in colored
    }
    reasons = list(reasons or [])
    if len(cores) > 1:
        reasons.append(
            f'色「{color_display}」は候補間で一致していますが、'
            '商品名・加工（または仕入先）が未確定のため要確認。色のみ先に設定候補とします。'
        )
        change_summary = f"商品名から色『{color_display}』を設定します"
        return _undecided_pair_result(
            empty=empty,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates or [_pair_candidate_payload(c) for c in all_candidates],
            reasons=reasons,
            adopted_color=color_display,
            color_decided_name_undecided=True,
            change_summary=change_summary,
        )

    # 同一コア・同一仕入先で色あり複数（理論上は統合済み）→ 先頭を採用
    hit = colored[0]['cand']
    rule = (
        f'商品名に色「{color_display}」が明示されているため採用'
        f'（在庫では代表が決まらなかった候補から）'
    )
    adopted = _adopt_candidate_package(
        hit,
        candidates=all_candidates,
        rule=rule,
        rule_code='explicit_color_name',
        reasons=reasons,
    )
    if pair_candidates:
        adopted['pairCandidates'] = pair_candidates
    if name_candidates:
        adopted['nameCandidates'] = name_candidates
    return adopted


def suggest_main_candidate_from_aux(group, aux_index):
    """同一商品候補の中だけで、初期在庫CSVから代表候補のフルパッケージを提案する。

    規則:
    - 「在庫」列のみ（購入数量・出荷数・補正在庫数は使わない）
    - 候補ごとに在庫を合算（正の数量同士は足す。負は正の合計から差し引かない）
    - 同一補助CSV行が複数別名に当たっても行番号で1回だけ加算（二重計上しない）
    - 先頭「＊/*」だけの名差は既存どおり同一候補
    - 候補が1組 → 在庫0でもその候補のフルパッケージを採用
    - 正の在庫合計が最大の候補が一意 → そのフルセット（名・仕入先・色・加工・素材）を採用
    - 正の在庫がなく、負の在庫がある候補がちょうど1つ → その候補を採用
    - 正の最大が同数／負が複数／数量不正/空 → 在庫では確定せず、
      続けて明示色優先（色なしは色違いにしない。異なる色同士は要確認）
    - 全候補が確認済み在庫0 → 商品台帳の最多正規化名を採用（明示色より優先）。
      同数最多は要確認。初期在庫CSVなし・該当なし・不正/空は在庫0扱いしない
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
        'sourceAdoptedName': '',
        'plannedRegisteredName': '',
        'plannedRegisteredColor': '',
        'changeSummary': '',
        'colorDecidedNameUndecided': False,
        'ledgerNameVotes': [],
        'ledgerNameVoteAdopted': None,
        'nameDecidedSupplierUndecided': False,
    }
    code = stock.text(group.get('code') or '')
    if not stock_product_merge.is_normal_product_code(code):
        return empty

    candidates = _collect_name_supplier_candidates(group)
    if not candidates:
        return empty

    # 補助CSVなし → 在庫判定を飛ばし明示色のみ
    if not aux_index:
        name_candidates = []
        for cand in candidates:
            display_name = cand.get('productName') or ''
            if display_name and display_name not in name_candidates:
                name_candidates.append(display_name)
        pair_candidates = [_pair_candidate_payload(c) for c in candidates]
        if len(candidates) == 1:
            return _adopt_candidate_package(
                candidates[0], candidates=candidates,
                rule='候補が1組のため、この候補をメインに採用（初期在庫CSVなし）',
                rule_code='aux_single_candidate',
            )
        color_hit = _try_explicit_color_adoption(
            candidates,
            all_candidates=candidates,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates,
            reasons=[],
            empty=empty,
        )
        if color_hit is not None:
            return color_hit
        return _undecided_pair_result(
            empty=empty,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates,
            reasons=['初期在庫CSVがなく、明示色でも代表候補を自動確定できません（要確認）。'],
        )

    name_candidates = []
    pair_candidates = []
    reasons = []
    any_undecidable = False

    for cand in candidates:
        supplier = cand['supplier']
        names = cand['ledgerNames']
        display_name = cand['productName']
        if display_name and display_name not in name_candidates:
            name_candidates.append(display_name)
        # 補助CSV行は行番号で一意化（複数別名が同じ行に当たっても1回だけ）
        matched_by_row = {}
        for row_no, item in _aux_rows_for_code(aux_index, code):
            if row_no in matched_by_row:
                continue
            for name in names:
                if _aux_row_matches_candidate(
                    item, code=code, supplier=supplier, product_name=name,
                    customers=cand.get('customers') or [],
                ):
                    matched_by_row[row_no] = (item, name)
                    break
        has_nonzero = False
        has_positive = False
        has_negative = False
        positive_total = 0
        size_rows = []
        for row_no, (item, _name) in matched_by_row.items():
            qty, qstatus = parse_aux_stock_qty(item.get('在庫'))
            size = stock.text(item.get('サイズ'))
            if qstatus != 'ok':
                any_undecidable = True
                if qstatus == 'empty':
                    reasons.append(f'{row_no}行目：在庫が空です（0扱いしません）。')
                else:
                    reasons.append(
                        f'{row_no}行目：在庫「{stock.text(item.get("在庫"))}」が不正です（0扱いしません）。'
                    )
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
                positive_total += qty
            if qty is not None and qty < 0:
                has_negative = True
            if qty is not None and qty != 0:
                has_nonzero = True
            size_rows.append({
                'rowNo': row_no,
                'size': size,
                'qty': qty,
                'qtyDisplay': str(qty),
                'qtyStatus': 'ok',
            })
        stock_summary = _summarize_aux_stock_rows(size_rows)
        cand['auxStockTotal'] = stock_summary['auxStockTotal']
        if stock_summary['qtyUndecidable']:
            cand['auxPositiveStockTotal'] = None
        elif stock_summary['hasAuxMatch']:
            cand['auxPositiveStockTotal'] = positive_total
        else:
            cand['auxPositiveStockTotal'] = None
        cand['auxStockDisplay'] = stock_summary['auxStockDisplay']
        cand['hasAuxMatch'] = stock_summary['hasAuxMatch']
        cand['hasNonZeroStock'] = bool(has_nonzero and not stock_summary['qtyUndecidable'])
        cand['hasPositiveStock'] = bool(has_positive and not stock_summary['qtyUndecidable'])
        cand['hasNegativeStock'] = bool(has_negative and not stock_summary['qtyUndecidable'])
        pair_candidates.append(_pair_candidate_payload(cand))

    def _after_stock_undecided(pool, stock_reasons):
        """在庫未決時: 明示色優先 → それでもダメなら要確認。"""
        color_hit = _try_explicit_color_adoption(
            pool,
            all_candidates=candidates,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates,
            reasons=list(stock_reasons or []),
            empty=empty,
        )
        if color_hit is not None:
            return color_hit
        return _undecided_pair_result(
            empty=empty,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates,
            reasons=list(stock_reasons or []),
        )

    if any_undecidable:
        reasons.append(
            '在庫の空欄または不正があるため、代表候補は自動確定しません。'
        )
        # 数量不正時は在庫判断不能。色でも自動確定しない（根拠が弱い）
        return _undecided_pair_result(
            empty=empty,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates,
            reasons=reasons,
        )

    # 候補1組 → 在庫0でもフルパッケージ採用
    if len(candidates) == 1:
        only = candidates[0]
        qty_bit = _stock_qty_clause(only)
        if qty_bit:
            rule = f'候補が1組のため、この候補をメインに採用（{qty_bit}）'
        elif only.get('hasAuxMatch'):
            rule = '候補が1組のため、この候補をメインに採用（初期在庫：0）'
        else:
            rule = '候補が1組のため、この候補をメインに採用（初期在庫CSVに該当なし）'
        return _adopt_candidate_package(
            only, candidates=candidates, rule=rule,
            rule_code='aux_single_candidate', reasons=reasons,
        )

    positive_hits = [
        c for c in candidates
        if c.get('hasPositiveStock') and int(c.get('auxPositiveStockTotal') or 0) > 0
    ]
    # 正の行がなく負の行だけある候補（正負混在は positive 側で扱う。負は正から差し引かない）
    negative_only_hits = [
        c for c in candidates
        if c.get('hasNegativeStock') and not c.get('hasPositiveStock')
    ]

    comparison = _format_candidate_stock_comparison(candidates)

    if positive_hits:
        max_qty = max(int(c.get('auxPositiveStockTotal') or 0) for c in positive_hits)
        winners = [
            c for c in positive_hits
            if int(c.get('auxPositiveStockTotal') or 0) == max_qty
        ]
        if len(winners) == 1:
            hit = winners[0]
            if len(positive_hits) >= 2:
                rule = f'初期在庫が最多のため採用：{comparison}'
                rule_code = 'aux_max_positive_stock'
            else:
                # 正の在庫がある候補が1組だけ（他は0／負）
                rule = _unique_nonzero_adoption_rule(hit)
                rule_code = 'aux_nonzero_pair'
            return _adopt_candidate_package(
                hit, candidates=candidates, rule=rule,
                rule_code=rule_code, reasons=reasons,
            )
        tie_reasons = list(reasons) + [
            '初期在庫の最大が同数のため要確認：'
            f'{comparison}。手動でメイン候補を選んでください'
            '（別候補の名・仕入先・仕様は組み合わせません）。'
        ]
        # 同数タイは最多候補の中だけで色優先（在庫0の色ありへ飛ばない）
        return _after_stock_undecided(winners, tie_reasons)

    if len(negative_only_hits) == 1:
        hit = negative_only_hits[0]
        return _adopt_candidate_package(
            hit, candidates=candidates, rule=_unique_nonzero_adoption_rule(hit),
            rule_code='aux_nonzero_pair', reasons=reasons,
        )

    if len(negative_only_hits) > 1:
        neg_reasons = list(reasons) + [
            '負の在庫がある候補が複数あるため要確認：'
            f'{comparison}。絶対値では選びません。手動でメイン候補を選んでください。'
        ]
        return _after_stock_undecided(negative_only_hits, neg_reasons)

    # 全候補が確認済み在庫0 → 商品台帳の最多名称（明示色優先よりこちら）
    if _all_candidates_confirmed_stock_zero(candidates):
        zero_bits = []
        for cand in candidates:
            zero_bits.append(
                f'{cand.get("productName") or "?"}／{cand.get("supplier") or "?"}'
                f'=初期在庫：{cand.get("auxStockTotal")}'
            )
        detail = '、'.join(zero_bits[:4])
        zero_reasons = list(reasons) + (
            [f'全候補の初期在庫が0です（{detail}）。'] if detail else []
        )
        vote_hit = _try_ledger_max_name_adoption(
            group,
            candidates=candidates,
            name_candidates=name_candidates,
            pair_candidates=pair_candidates,
            reasons=zero_reasons,
            empty=empty,
        )
        if vote_hit is not None:
            return vote_hit

    zero_bits = []
    for cand in candidates:
        if cand.get('hasAuxMatch') and cand.get('auxStockTotal') is not None:
            zero_bits.append(
                f'{cand.get("productName") or "?"}／{cand.get("supplier") or "?"}'
                f'=初期在庫：{cand["auxStockTotal"]}'
            )
    detail = '、'.join(zero_bits[:4])
    if detail:
        zero_reasons = list(reasons) + [
            '初期在庫がすべて在庫0のため、代表候補は自動確定しません'
            f'（{detail}。該当行の有無だけでは選びません。要確認）。'
        ]
    else:
        zero_reasons = list(reasons) + [
            '非ゼロ在庫がある候補がないため、初期在庫CSVからは代表候補を'
            '自動確定しません（該当行の有無だけでは選びません。要確認）。'
        ]
    return _after_stock_undecided(candidates, zero_reasons)


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
        'matchedAuxRows': [],
        'hasAnyAuxRows': False,
        'hasPositiveStock': False,
        'hasNonZeroStock': False,
        'qtyUndecidable': False,
        'qtyUndecidableReasons': [],
        'auxStockTotal': None,
        'auxStockDisplay': '—',
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


def attach_aux_primary_judgment(sku_groups, aux_uploaded, *, register_opening=False):
    """品番グループへ通常仕入先の自動判定を付与。

    補助CSVの有無に関わらず候補1社なら自動設定する。複社は補助CSV規則（#152）。
    register_opening=True のときは確認後に初期在庫も登録する旨を meta に載せる。
    products モードでは判定補助のみ（在庫は登録しない）を明示する。
    """
    aux_index = build_aux_row_index(aux_uploaded['rows']) if aux_uploaded else None
    meta = None
    if aux_uploaded:
        if register_opening:
            note = (
                '初期在庫CSVを選んでいるため、確認後に初期在庫も登録します。'
                '数量は「在庫」列のみです（補正在庫数・購入・出荷は使いません）。'
                'あわせて通常仕入先・代表候補のフルパッケージ判定にも使います。'
                '非ゼロ在庫（負含む）がある候補が1組のとき、その候補の'
                '商品名・仕入先・色・加工・素材をセットで採用します。'
                '台帳に無い初期在庫だけの商品は補完候補として示し、自動では登録しません。'
            )
        else:
            note = (
                f'{AUX_JUDGMENT_ONLY_NOTE}。'
                '通常仕入先・代表候補のフルパッケージ判定補助だけに使います。'
                '在庫数量・単価・初期在庫・入出庫履歴は登録・変更しません。'
                '非ゼロ在庫（負含む）がある候補が1組のとき、その候補の'
                '商品名・仕入先・色・加工・素材をセットで採用します。'
                '補助CSVにだけある商品・仕入先は新規作成しません。'
            )
        meta = {
            'fileName': aux_uploaded['fileName'],
            'fileSha256': aux_uploaded['fileSha256'],
            'fileSize': aux_uploaded['fileSize'],
            'encoding': aux_uploaded['encoding'],
            'rowCount': len(aux_uploaded['rows']),
            'ignoredColumns': aux_uploaded.get('ignoredColumns') or [],
            'willRegisterOpeningStock': bool(register_opening),
            'judgmentOnly': not bool(register_opening),
            'note': note,
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

        # 代表候補のフルパッケージ提案（在庫 → だめなら明示色。補助CSVなしでも色優先を試す）
        pair_from_aux = suggest_main_candidate_from_aux(group, aux_index)
        pair_supplier = stock.text(pair_from_aux.get('suggestedPrimarySupplier') or '')
        pair_code = stock.text(pair_from_aux.get('pairAdoptionRuleCode') or '')
        # 在庫採用（最多／非ゼロ一意／候補1組）で決まったときは、その仕入先を通常に
        # （別候補の名と混ぜない）。候補1社の自動（#154）は維持し、理由を上書きしない。
        if (
            pair_supplier
            and not pair_from_aux.get('pairNeedsChoice')
            and decision_kind != 'single_candidate'
        ):
            auto_primary = pair_supplier
            auto_reason = stock.text(pair_from_aux.get('pairAdoptionRule') or '') or (
                '初期在庫が最多のため、この候補をメインに採用'
                if pair_code == 'aux_max_positive_stock'
                else (
                    '全候補の在庫が0のため、商品台帳で最多の名称を採用'
                    if pair_code == 'ledger_max_name_zero_stock'
                    else '在庫が0以外の候補が1組のため、この候補をメインに採用'
                )
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
            'sourceAdoptedName': stock.text(pair_from_aux.get('sourceAdoptedName') or ''),
            'plannedRegisteredName': stock.text(
                pair_from_aux.get('plannedRegisteredName')
                or pair_from_aux.get('suggestedOfficialName')
                or ''
            ),
            'plannedRegisteredColor': stock.text(
                pair_from_aux.get('plannedRegisteredColor')
                or pair_from_aux.get('adoptedColor')
                or ''
            ),
            'changeSummary': stock.text(pair_from_aux.get('changeSummary') or ''),
            'colorDecidedNameUndecided': bool(pair_from_aux.get('colorDecidedNameUndecided')),
            'ledgerNameVotes': list(pair_from_aux.get('ledgerNameVotes') or []),
            'ledgerNameVoteAdopted': pair_from_aux.get('ledgerNameVoteAdopted'),
            'nameDecidedSupplierUndecided': bool(
                pair_from_aux.get('nameDecidedSupplierUndecided')
            ),
        }
    attach_aux_stock_visibility(sku_groups, aux_uploaded=bool(aux_uploaded))
    return meta


def _candidate_stock_detail(value, *, aux_stock_display, has_aux_match, aux_stock_total=None):
    return {
        'value': stock.text(value or ''),
        'auxStockTotal': aux_stock_total,
        'auxStockDisplay': stock.text(aux_stock_display or '—') or '—',
        'auxStockLabel': format_aux_stock_label(aux_stock_display),
        'hasAuxMatch': bool(has_aux_match),
    }


def attach_aux_stock_visibility(sku_groups, *, aux_uploaded=False):
    """比較一覧用に候補ごとの初期在庫表示を付与し、在庫起因の要確認理由を明示する。"""
    for group in sku_groups or []:
        aux = group.get('auxPrimary') or {}
        judgments = list(aux.get('supplierJudgments') or [])
        pairs = list(aux.get('pairCandidates') or [])

        # 仕入先候補 → 判定行の在庫合計
        by_supplier = {}
        for j in judgments:
            key = identity_match_key(j.get('supplier') or '')
            if not key:
                continue
            by_supplier[key] = j

        supplier_details = []
        seen_sup = set()
        for name in group.get('supplierCandidates') or []:
            name = stock.text(name or '')
            if not name or name in seen_sup:
                continue
            seen_sup.add(name)
            j = by_supplier.get(identity_match_key(name))
            if not aux_uploaded:
                supplier_details.append(_candidate_stock_detail(
                    name, aux_stock_display='—', has_aux_match=False,
                ))
            elif j:
                supplier_details.append(_candidate_stock_detail(
                    name,
                    aux_stock_display=j.get('auxStockDisplay') or '—',
                    has_aux_match=bool(j.get('hasAnyAuxRows')),
                    aux_stock_total=j.get('auxStockTotal'),
                ))
            else:
                supplier_details.append(_candidate_stock_detail(
                    name, aux_stock_display='—', has_aux_match=False,
                ))

        # 商品名候補 → 同名ペアの在庫を合算（別仕入先は足す）
        by_name = {}
        for pair in pairs:
            pname = stock.text(pair.get('productName') or '')
            if not pname:
                continue
            key = product_name_core_key(pname)
            slot = by_name.get(key)
            if not slot:
                slot = {
                    'total': 0,
                    'any_match': False,
                    'any_ok': False,
                    'undecidable': False,
                    'display_name': pname,
                }
                by_name[key] = slot
            if pair.get('hasAuxMatch'):
                slot['any_match'] = True
            if pair.get('auxStockDisplay') in ('（不正/空）',) or (
                isinstance(pair.get('auxStockDisplay'), str)
                and '不正' in pair.get('auxStockDisplay')
            ):
                slot['undecidable'] = True
            if pair.get('auxStockTotal') is not None:
                slot['any_ok'] = True
                try:
                    slot['total'] += int(pair['auxStockTotal'])
                except (TypeError, ValueError):
                    slot['total'] += pair['auxStockTotal']

        name_details = []
        seen_names = set()
        for name in group.get('nameCandidates') or []:
            name = stock.text(name or '')
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            slot = by_name.get(product_name_core_key(name))
            if not aux_uploaded or not slot:
                name_details.append(_candidate_stock_detail(
                    name, aux_stock_display='—', has_aux_match=False,
                ))
            elif slot['undecidable'] and not slot['any_ok']:
                name_details.append(_candidate_stock_detail(
                    name, aux_stock_display='（不正/空）', has_aux_match=True,
                ))
            elif slot['any_ok']:
                disp = str(slot['total'])
                if slot['undecidable']:
                    disp = f'{slot["total"]}（一部不正）'
                name_details.append(_candidate_stock_detail(
                    name,
                    aux_stock_display=disp,
                    has_aux_match=True,
                    aux_stock_total=slot['total'],
                ))
            elif slot['any_match']:
                name_details.append(_candidate_stock_detail(
                    name, aux_stock_display='0', has_aux_match=True, aux_stock_total=0,
                ))
            else:
                name_details.append(_candidate_stock_detail(
                    name, aux_stock_display='—', has_aux_match=False,
                ))

        group['nameCandidateDetails'] = name_details
        group['supplierCandidateDetails'] = supplier_details

        # 在庫が決め手の要確認を理由一覧へ明示（既存の名差・仕入先差と併記）
        if not aux_uploaded or not group.get('needsDecision'):
            continue
        if not aux.get('pairNeedsChoice') and not aux.get('needsManualPrimary'):
            continue
        stock_labels = [
            stock.text(r.get('label') or '')
            for r in (group.get('reviewReasons') or [])
        ]
        already = any(
            '在庫0' in lab or '初期在庫' in lab or '非ゼロ在庫' in lab
            for lab in stock_labels
        )
        if already:
            continue
        name_reasons = list(aux.get('nameReasons') or [])
        stock_reason = ''
        for r in name_reasons:
            if '在庫0' in r or '非ゼロ' in r or '初期在庫' in r:
                stock_reason = r
                break
        if not stock_reason and aux.get('manualReason'):
            mr = stock.text(aux.get('manualReason') or '')
            if '在庫' in mr or '初期在庫' in mr:
                stock_reason = mr
        if not stock_reason:
            continue
        # 長文は要約して先頭へ
        if 'すべて在庫0' in stock_reason or '在庫0のため' in stock_reason:
            label = '初期在庫がすべて在庫0のため要確認（自動では決めません）'
            code = 'aux_stock_all_zero'
        elif '最大が同数' in stock_reason:
            label = '初期在庫の最大が同数のため要確認（手動でメイン候補を選んでください）'
            code = 'aux_stock_positive_tie'
        elif '負の在庫がある候補が複数' in stock_reason:
            label = '負の在庫がある候補が複数あるため要確認（絶対値では選びません）'
            code = 'aux_stock_multi_negative'
        elif '複数' in stock_reason:
            label = '初期在庫で候補が複数あるため要確認'
            code = 'aux_stock_multi_nonzero'
        else:
            label = stock_reason
            code = 'aux_stock_review'
        reasons = list(group.get('reviewReasons') or [])
        reasons.insert(0, {
            'code': code,
            'label': label,
            'values': [
                d['value'] for d in name_details
                if d.get('hasAuxMatch')
            ][:6],
        })
        group['reviewReasons'] = reasons


def apply_aux_full_package_adoption(sku_groups, sku_decisions=None):
    """在庫一意／正の在庫最多／候補1組でメイン採用できた品番は、名／種／色／加工／素材差だけでは要確認に残さない。

    - 採用候補のフルパッケージを代表に
    - 非採用候補は別名／サブ／原文仕様マッピングとして保持（メイン仕様へ書き換えない）
    - 男女の明示対立（男性用⇔女性用）、既存重複（2件以上）は引き続き確認
    - 商品種の疑い・「女性用」の有無だけ・一般名と具体名の言い回し差は在庫採用でクリア
    - 既存の通常仕入先変更は明示確認が必要（黙って変えない）
    - ユーザーの品番判断があれば自動クリアせず手動を優先
    - 括弧分類が曖昧な採用候補は自動確定しない
    """
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    stock_adopt_codes = (
        'aux_nonzero_pair',
        'aux_single_candidate',
        'aux_max_positive_stock',
        'explicit_color_name',
        'ledger_max_name_zero_stock',
    )
    for group in sku_groups:
        aux = group.get('auxPrimary') or {}
        rule_code = stock.text(
            aux.get('pairAdoptionRuleCode') or aux.get('nameAdoptionRuleCode') or ''
        )
        # 名称は最多票で決定・仕入先だけ未決 → 名差だけでは要確認に残さない
        if (
            rule_code == 'ledger_max_name_zero_stock'
            and aux.get('nameDecidedSupplierUndecided')
            and not aux.get('officialNameNeedsChoice')
        ):
            _soften_name_diff_after_ledger_vote(group, aux)
            continue
        if rule_code not in stock_adopt_codes:
            continue
        if aux.get('pairNeedsChoice') or aux.get('officialNameNeedsChoice'):
            continue
        package = aux.get('adoptedPackage')
        if not isinstance(package, dict) or not package.get('productName'):
            continue
        code = stock.text(group.get('code') or '')
        raw = decisions.get(code) or decisions.get(str(code)) or {}
        if isinstance(raw, dict) and stock.text(raw.get('decision')):
            # 手動判断あり（色分割含む）→ 提案表示のみ。在庫自動まとめしない
            _attach_stock_pick_display(group, aux, package, auto_cleared=False)
            group['auxStockPickManualOverride'] = True
            if stock.text(raw.get('decision')) == SKU_DECISION_COLOR_SPLIT:
                group['excludeAuxStockAutoMerge'] = True
                group['colorSplit'] = True
            if stock.text(raw.get('decision')) == SKU_DECISION_CONTENT_SPLIT:
                group['excludeAuxStockAutoMerge'] = True
                group['contentSplit'] = True
            continue
        if package.get('parenNeedsConfirm'):
            # メイン候補は在庫で決定済み。色・括弧の振り分けだけが未確定 → 「候補未決定」とはしない
            group['auxStockPickBlocked'] = True
            paren_reasons = [
                stock.text(r) for r in (package.get('parenConfirmReasons') or []) if stock.text(r)
            ]
            detail = '／'.join(paren_reasons[:3]) if paren_reasons else '色・加工・括弧の振り分けが曖昧'
            group['auxStockPickBlockReason'] = (
                'メイン候補は在庫を根拠に決定済みですが、'
                f'{detail}のため、登録名と色の振り分けを確認してください。'
            )
            _attach_stock_pick_display(group, aux, package, auto_cleared=False)
            _soften_spec_only_reasons(group)
            # 残確認を色振り分けとして明示（候補差の個別確認理由に見せない）
            reasons = [
                r for r in (group.get('reviewReasons') or [])
                if stock.text(r.get('code') or '') not in (
                    'aux_stock_pick_partial', 'aux_stock_pick', 'explicit_color_pick',
                )
            ]
            reasons.insert(0, {
                'code': 'aux_stock_pick_paren',
                'label': group['auxStockPickBlockReason'],
                'values': [
                    package.get('productName') or '',
                    package.get('supplier') or '',
                ],
            })
            group['reviewReasons'] = reasons
            continue
        if int(group.get('existingCount') or 0) > 1:
            # 残す商品の選択が必要。パッケージ情報だけ付与
            group['auxStockPickPendingExisting'] = True
            _attach_stock_pick_display(group, aux, package, auto_cleared=False)
            _soften_spec_only_reasons(group)
            existing_n = int(group.get('existingCount') or 0)
            qty_bit = ''
            adopted_pair = next(
                (
                    p for p in (aux.get('pairCandidates') or [])
                    if product_name_core_key(p.get('productName') or '') == product_name_core_key(
                        package.get('productName') or ''
                    )
                    and identity_match_key(p.get('supplier') or '') == identity_match_key(
                        package.get('supplier') or ''
                    )
                ),
                None,
            )
            if adopted_pair and adopted_pair.get('auxStockTotal') is not None:
                qty_bit = str(adopted_pair['auxStockTotal'])
            elif adopted_pair and adopted_pair.get('auxPositiveStockTotal') is not None:
                qty_bit = str(adopted_pair['auxPositiveStockTotal'])
            label = (
                f'メイン候補は在庫{qty_bit}を根拠に決定済みですが、'
                f'登録済み商品が{existing_n}件あるため、残す商品を選んでください。'
                if qty_bit else
                f'メイン候補は在庫を根拠に決定済みですが、'
                f'登録済み商品が{existing_n}件あるため、残す商品を選んでください。'
            )
            reasons = [
                r for r in (group.get('reviewReasons') or [])
                if stock.text(r.get('code') or '') not in (
                    'existing_dup', 'aux_stock_pick_partial', 'aux_stock_pick',
                )
            ]
            reasons.insert(0, {
                'code': 'aux_stock_pick_existing',
                'label': label,
                'values': [
                    package.get('productName') or '',
                    package.get('supplier') or '',
                ],
            })
            group['reviewReasons'] = reasons
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

        # 「女性用」有無・商品種差は在庫採用で解消（男女の明示対立だけ残す）
        gender_conflict = bool(group.get('genderConflictPossible'))
        if group.get('genderDiffPossible') and not gender_conflict:
            _clear_gender_presence_absence_flags(group)
        if group.get('kindDiffPossible'):
            _clear_kind_diff_flags(group)

        if gender_conflict:
            # 男女の明示対立 → 確認のまま（種・色・加工・素材・対象有無・名差は外しうる）
            _soften_spec_only_reasons(group)
            _attach_stock_pick_display(group, aux, package, auto_cleared=False)
            continue

        # 色・加工・素材・名差・仕入先差・対象標記の有無・商品種は在庫採用で解消 → 自動まとめ
        was_decision = bool(group.get('needsDecision'))
        group['needsDecision'] = False
        group['plannedSingleProduct'] = True
        group['status'] = 'auto'
        group['usedMarkerAutoMerged'] = bool(group.get('usedMarkerAutoMerged'))
        group['auxStockPickAuto'] = True
        group['auxStockPickClearedReview'] = was_decision
        if rule_code == 'aux_max_positive_stock':
            default_reason = '初期在庫が最多のため、この候補をメインに採用'
        elif rule_code == 'explicit_color_name':
            default_reason = '商品名に色が明示されているため、この候補をメインに採用'
        elif rule_code == 'ledger_max_name_zero_stock':
            default_reason = (
                '全候補の在庫が0のため、商品台帳で最多の名称を採用'
            )
        else:
            default_reason = '在庫が0以外の候補が1組のため、この候補をメインに採用'
        reason = stock.text(aux.get('pairAdoptionRule') or '') or default_reason
        group['auxStockPickReason'] = reason
        group['reviewTier'] = REVIEW_TIER_AUTO
        group['reviewTierLabel'] = REVIEW_TIER_LABELS[REVIEW_TIER_AUTO]
        group['bulkApprovable'] = False
        group['colorDiffPossible'] = False
        group['processingDiffPossible'] = False
        group['fabricDiffPossible'] = False
        group['kindDiffPossible'] = False
        group['genderDiffPossible'] = False
        group['genderConflictPossible'] = False
        # 名差・仕入先差は採用で解消済みとして表示上も落とす
        group['nameWordingDiff'] = False
        group['supplierDiff'] = False
        group['ledgerNameVotes'] = list(aux.get('ledgerNameVotes') or [])
        group['ledgerNameVoteAdopted'] = aux.get('ledgerNameVoteAdopted')
        kept = list(aux.get('keptPackages') or [])
        kept_names = []
        for k in kept:
            for n in k.get('ledgerNames') or ([k.get('productName')] if k.get('productName') else []):
                if n and n not in kept_names:
                    kept_names.append(n)
        stock_values = [
            package.get('productName') or '',
            package.get('supplier') or '',
        ]
        adopted_pair = next(
            (
                p for p in (aux.get('pairCandidates') or [])
                if product_name_core_key(p.get('productName') or '') == product_name_core_key(
                    package.get('productName') or ''
                )
                and identity_match_key(p.get('supplier') or '') == identity_match_key(
                    package.get('supplier') or ''
                )
            ),
            None,
        )
        if adopted_pair and adopted_pair.get('auxStockTotal') is not None:
            stock_values.append(f'初期在庫：{adopted_pair["auxStockTotal"]}')
        registered_name = (
            stock.text(aux.get('plannedRegisteredName') or '')
            or stock.text(aux.get('suggestedOfficialName') or '')
            or package.get('plannedName')
            or package.get('productName')
            or ''
        )
        registered_color = (
            stock.text(aux.get('plannedRegisteredColor') or '')
            or stock.text(aux.get('adoptedColor') or '')
            or package.get('color')
            or ''
        )
        source_name = (
            stock.text(aux.get('sourceAdoptedName') or '')
            or package.get('sourceName')
            or package.get('productName')
            or ''
        )
        change_summary = stock.text(aux.get('changeSummary') or package.get('changeSummary') or '')
        if not change_summary and registered_color:
            change_summary = f"商品名から色『{registered_color}』を設定します"
        if rule_code == 'explicit_color_name':
            reason_code = 'explicit_color_pick'
        elif rule_code == 'ledger_max_name_zero_stock':
            reason_code = 'ledger_max_name_pick'
        else:
            reason_code = 'aux_stock_pick'
        group['reviewReasons'] = [{
            'code': reason_code,
            'label': reason,
            'values': stock_values,
        }]
        vote_adopted = aux.get('ledgerNameVoteAdopted') or {}
        if vote_adopted and rule_code == 'ledger_max_name_zero_stock':
            pre_names = list(vote_adopted.get('preNormalizeNames') or [])
            group['reviewReasons'].append({
                'code': 'ledger_name_votes',
                'label': (
                    f'商品台帳の名称票（正規化後最多 '
                    f'{vote_adopted.get("count") or "?"} 行）'
                ),
                'values': pre_names[:8],
            })
        if change_summary:
            group['reviewReasons'].append({
                'code': 'color_split',
                'label': change_summary,
                'values': [
                    f'採用ソース名:{source_name}' if source_name else '',
                    f'登録予定名:{registered_name}' if registered_name else '',
                    f'登録予定色:{registered_color}' if registered_color else '',
                ],
            })
        if kept_names:
            group['reviewReasons'].append({
                'code': 'kept_aliases',
                'label': '非採用の商品名は照合用別名として残します',
                'values': kept_names,
            })
        group['otherDiffs'] = []
        if rule_code == 'explicit_color_name':
            group['applyScope'] = (
                f'品番「{group.get("code")}」は在庫では代表が決まらなかったため、'
                f'商品名の明示色でメイン候補を自動採用します。'
                f'登録予定名「{registered_name}」・色「{registered_color}」。'
                f'原文名は照合用別名として残します。'
            )
        elif rule_code == 'ledger_max_name_zero_stock':
            group['applyScope'] = (
                f'品番「{group.get("code")}」は全候補の在庫が0のため、'
                f'商品台帳で最多の名称を採用します。'
                f'登録予定名「{registered_name}」・色「{registered_color}」。'
                f'原文表記は照合用別名として残します。'
            )
        else:
            group['applyScope'] = (
                f'品番「{group.get("code")}」は初期在庫CSVの在庫判定で'
                f'メイン候補のフルパッケージを自動採用します。'
                f'非採用候補の名・仕入先・色・加工・素材は原文のままマッピングに残します。'
            )
        _attach_stock_pick_display(group, aux, package, auto_cleared=True)

        # 商品計画を採用パッケージへ（登録予定名＝色振り分け後）
        plan = dict(group.get('productPlan') or {})
        plan['productName'] = registered_name or plan.get('productName') or ''
        plan['supplier'] = package.get('supplier') or plan.get('supplier') or ''
        plan['color'] = registered_color
        plan['processing'] = package.get('processing') or ''
        plan['material'] = package.get('material') or ''
        plan['auxStockPickAuto'] = True
        plan['auxStockPickReason'] = reason
        plan['keptPackages'] = kept
        plan['nameAdoptionRule'] = reason
        plan['sourceAdoptedName'] = source_name
        plan['plannedRegisteredName'] = registered_name
        plan['plannedRegisteredColor'] = registered_color
        plan['changeSummary'] = change_summary
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
        group['plannedColor'] = registered_color
        group['plannedProcessing'] = package.get('processing') or ''
        group['plannedMaterial'] = package.get('material') or ''
        group['nameAdoptionRule'] = reason
        group['sourceAdoptedName'] = source_name
        group['plannedRegisteredName'] = registered_name
        group['plannedRegisteredColor'] = registered_color
        group['changeSummary'] = change_summary
        group['colorNameSplit'] = bool(registered_color)


def _soften_name_diff_after_ledger_vote(group, aux):
    """最多名称は決定済みだが仕入先未決のとき、名差だけでは要確認に残さない。"""
    package = aux.get('adoptedPackage') if isinstance(aux.get('adoptedPackage'), dict) else {}
    registered_name = (
        stock.text(aux.get('plannedRegisteredName') or '')
        or stock.text(aux.get('suggestedOfficialName') or '')
        or package.get('plannedName')
        or package.get('productName')
        or ''
    )
    registered_color = (
        stock.text(aux.get('plannedRegisteredColor') or '')
        or stock.text(aux.get('adoptedColor') or '')
        or package.get('color')
        or ''
    )
    reason = stock.text(aux.get('pairAdoptionRule') or '') or (
        '全候補の在庫が0のため、商品台帳で最多の名称を採用'
    )
    group['nameWordingDiff'] = False
    group['ledgerNameVotes'] = list(aux.get('ledgerNameVotes') or [])
    group['ledgerNameVoteAdopted'] = aux.get('ledgerNameVoteAdopted')
    group['sourceAdoptedName'] = stock.text(aux.get('sourceAdoptedName') or '') or (
        package.get('sourceName') or package.get('productName') or ''
    )
    group['plannedRegisteredName'] = registered_name
    group['plannedRegisteredColor'] = registered_color
    group['changeSummary'] = stock.text(aux.get('changeSummary') or package.get('changeSummary') or '')
    group['nameAdoptionRule'] = reason
    # 名差理由を外し、仕入先・その他の未解決理由だけ残す
    cleaned = []
    for r in (group.get('reviewReasons') or []):
        code = stock.text(r.get('code') or '')
        if code in ('name', 'name_wording', 'aux_stock_all_zero', 'name_candidates'):
            continue
        cleaned.append(r)
    vote_adopted = aux.get('ledgerNameVoteAdopted') or {}
    cleaned.insert(0, {
        'code': 'ledger_max_name_pick_partial',
        'label': reason,
        'values': [
            registered_name,
            f'票数:{vote_adopted.get("count")}' if vote_adopted.get('count') is not None else '',
        ],
    })
    if aux.get('needsManualPrimary') or group.get('supplierDiff'):
        cleaned.append({
            'code': 'supplier',
            'label': '通常仕入先を選んでください（名称は商品台帳の最多で決定済み）',
            'values': list(group.get('supplierCandidates') or [])[:6],
        })
    group['reviewReasons'] = cleaned
    group['plannedOfficialName'] = registered_name or group.get('plannedOfficialName') or ''
    plan = dict(group.get('productPlan') or {})
    if registered_name:
        plan['productName'] = registered_name
    if registered_color:
        plan['color'] = registered_color
    plan['sourceAdoptedName'] = group['sourceAdoptedName']
    plan['plannedRegisteredName'] = registered_name
    plan['plannedRegisteredColor'] = registered_color
    plan['nameAdoptionRule'] = reason
    group['productPlan'] = plan
    _attach_stock_pick_display(group, aux, package or {
        'productName': registered_name,
        'color': registered_color,
    }, auto_cleared=False)


def _clear_gender_presence_absence_flags(group):
    """在庫一意採用時、「女性用」有無などの対象標記の有無だけを理由から外す（明示対立は残す）。"""
    group['genderDiffPossible'] = False
    group['reviewReasons'] = [
        r for r in (group.get('reviewReasons') or [])
        if stock.text(r.get('code') or '') != 'gender'
    ]
    group['otherDiffs'] = [
        d for d in (group.get('otherDiffs') or [])
        if stock.text(d.get('code') or '') != 'gender'
    ]


def _clear_kind_diff_flags(group):
    """在庫一意／最多採用時、商品種の疑いだけでは要確認に残さない。"""
    group['kindDiffPossible'] = False
    group['reviewReasons'] = [
        r for r in (group.get('reviewReasons') or [])
        if stock.text(r.get('code') or '') != 'kind'
    ]
    group['otherDiffs'] = [
        d for d in (group.get('otherDiffs') or [])
        if stock.text(d.get('code') or '') != 'kind'
    ]


def _soften_after_maker_adopt(group, raw):
    """メーカー情報で補正を採用した品番グループ: 解消した名・商品種・色競合を理由から外す。

    加工・男女など未解決の理由は残す。原文CSV名は照合用に維持（ここでは触らない）。
    """
    if not isinstance(raw, dict) or not raw.get('makerAdopted'):
        return
    if stock.text(raw.get('decision')) != SKU_DECISION_MERGE:
        return
    drop_codes = {'color', 'name_wording', 'kind'}
    group['colorDiffPossible'] = False
    group['nameWordingDiff'] = False
    group['kindDiffPossible'] = False
    reasons = []
    for r in group.get('reviewReasons') or []:
        code = stock.text(r.get('code') or '')
        if code in drop_codes:
            continue
        reasons.append(r)
    adopted_name = stock.text(raw.get('officialName') or raw.get('name') or '')
    adopted_color = stock.text(raw.get('color') or '')
    label_bits = []
    if adopted_name:
        label_bits.append(f'登録予定名「{adopted_name}」')
    if adopted_color:
        label_bits.append(f'色「{adopted_color}」')
    detail = '・'.join(label_bits) if label_bits else 'メーカー情報'
    reasons.insert(0, {
        'code': 'maker_adopt',
        'label': (
            f'メーカー情報で補正を採用し、商品名・商品種・色の競合を解消（{detail}）。'
            '原文名は照合用に残します。'
        ),
        'values': [adopted_name, adopted_color],
    })
    group['reviewReasons'] = reasons
    group['otherDiffs'] = [
        d for d in (group.get('otherDiffs') or [])
        if stock.text(d.get('code') or '') not in drop_codes
    ]
    # メーカー補正後は1商品まとめ前提（色分割を自動では戻さない）
    if adopted_name:
        group['plannedOfficialName'] = adopted_name
        group['plannedRegisteredName'] = adopted_name
    # 元品番・注記と補正後の対応（初期在庫照合用。表示名は触らない）
    if stock.text(raw.get('usageNote') or ''):
        group['makerUsageNote'] = stock.text(raw.get('usageNote') or '')
    if stock.text(raw.get('sourceSku') or ''):
        group['makerSourceSku'] = stock.text(raw.get('sourceSku') or '')
    if stock.text(raw.get('searchMakerSku') or ''):
        group['makerSearchSku'] = stock.text(raw.get('searchMakerSku') or '')
    if adopted_color:
        group['plannedRegisteredColor'] = adopted_color
        group['plannedColor'] = adopted_color
    group['makerCorrectionAdopted'] = True
    # ボタン再表示: 採用済みフラグで showButton は後段メタ再計算で落ちる


def _soften_spec_only_reasons(group):
    """在庫一意採用時、色・加工・素材・種・対象有無の疑い理由を外し、残確認を明示（確認は継続）。"""
    drop_codes = {'color', 'processing', 'fabric', 'kind'}
    group['kindDiffPossible'] = False
    if not group.get('genderConflictPossible'):
        # 有無だけの対象差は在庫一意で解消済み扱い
        drop_codes.add('gender')
        group['genderDiffPossible'] = False
    # 一般名⇔具体名の言い回しも在庫一意採用でメインが決まっている場合は落とす
    drop_codes.add('name_wording')
    group['nameWordingDiff'] = False
    reasons = []
    for r in group.get('reviewReasons') or []:
        code = stock.text(r.get('code') or '')
        if code in drop_codes:
            continue
        reasons.append(r)
    aux = group.get('auxPrimary') or {}
    qty_bit = ''
    for p in aux.get('pairCandidates') or []:
        if not p.get('hasNonZeroStock'):
            continue
        if p.get('auxStockTotal') is not None:
            qty_bit = f'（初期在庫：{p["auxStockTotal"]}）'
            break
    remaining_bits = []
    if group.get('genderConflictPossible'):
        remaining_bits.append('男女・対象の明示対立')
    remain_label = '・'.join(remaining_bits) if remaining_bits else '内容'
    pick_label = '在庫／色判定'
    aux_code = stock.text(
        (group.get('auxPrimary') or {}).get('pairAdoptionRuleCode')
        or (group.get('auxPrimary') or {}).get('nameAdoptionRuleCode')
        or ''
    )
    if aux_code == 'explicit_color_name':
        pick_label = '明示色優先'
    elif aux_code in ('aux_nonzero_pair', 'aux_max_positive_stock', 'aux_single_candidate'):
        pick_label = '在庫判定'
    reasons.append({
        'code': 'aux_stock_pick_partial',
        'label': (
            f'{pick_label}{qty_bit}でメイン候補は決まっていますが、'
            f'{remain_label}の確認が残っています'
        ),
        'values': [],
    })
    group['reviewReasons'] = reasons
    group['colorDiffPossible'] = False
    group['processingDiffPossible'] = False
    group['fabricDiffPossible'] = False
    group['otherDiffs'] = [
        d for d in (group.get('otherDiffs') or [])
        if stock.text(d.get('code') or '') not in drop_codes
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
        usage_kind = stock.text(pc.get('usageKind') or '')
        if usage_kind != paren_classify.USAGE_KIND_SALES:
            usage_kind = ''
        provenance = {
            'originalName': csv_name,
            'originalSupplier': csv_supplier,
            'color': stock.text(pc.get('color') or ''),
            'sourceColor': stock.text(pc.get('sourceColor') or ''),
            'processing': stock.text(pc.get('processing') or ''),
            'material': stock.text(pc.get('material') or ''),
            'usageKind': usage_kind,
            'usageFromCustomerName': bool(pc.get('usageFromCustomerName')),
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
        # 販売用途は行ごとの取扱キーへ残す（同一商品でも販売／他は合算しない）。
        handle = dict(row.get('handle') or {})
        if is_main:
            main_processing = stock.text(package.get('processing') or '')
            row_processing = stock.text(pc.get('processing') or '')
            use_processing = row_processing or main_processing
            composed = paren_classify.compose_handling_embroidery(use_processing, usage_kind)
            if composed:
                handle['proposedEmbroidery'] = composed
                handle['usageKind'] = usage_kind
                if handle.get('action') == 'create' or handle.get('embroidery') in (
                    '', None, PENDING_EMBROIDERY,
                ):
                    handle['embroidery'] = composed
                    handle['embroideryLabel'] = composed
                    handle['pendingEmbroiderySort'] = False
                    handle['fromParenClassification'] = True
        else:
            # 非メイン行の括弧加工は商品取扱へ載せず、原文仕様マッピング側に保持
            # ただし販売用途の取扱分離は行ごとに残す
            row_proc = stock.text(pc.get('processing') or '')
            composed = paren_classify.compose_handling_embroidery(row_proc, usage_kind)
            handle['provenanceEmbroidery'] = composed or row_proc
            handle['usageKind'] = usage_kind
            if usage_kind == paren_classify.USAGE_KIND_SALES and composed:
                handle['proposedEmbroidery'] = composed
                if handle.get('action') == 'create' or handle.get('embroidery') in (
                    '', None, PENDING_EMBROIDERY,
                ):
                    handle['embroidery'] = composed
                    handle['embroideryLabel'] = composed
                    handle['pendingEmbroiderySort'] = False
                    handle['fromParenClassification'] = True
            else:
                handle['fromParenClassification'] = False
                if row_proc and stock.text(handle.get('embroidery') or '') == row_proc:
                    handle['embroidery'] = PENDING_EMBROIDERY
                    handle['embroideryLabel'] = PENDING_EMBROIDERY
                    handle['pendingEmbroiderySort'] = True
                    handle.pop('proposedEmbroidery', None)
        row['handle'] = handle

        # 自動まとめ扱い
        row['skuAutoMerged'] = True
        # 非メイン行の括弧曖昧さは原文仕様（provenance）に残し、行の要確認は外す。
        # 採用メイン自体の括弧曖昧は apply_aux_full_package_adoption で自動確定を止めている。
        if row.get('parenNeedsConfirm') and not is_main:
            row['parenNeedsConfirm'] = False
            row['reasons'] = [
                r for r in (row.get('reasons') or [])
                if '括弧' not in stock.text(r)
            ]
        if row.get('status') in (STATUS_REVIEW,) and not row.get('parenNeedsConfirm'):
            # 色差・種差などで needs_review になっていた行をコミット可能へ戻す
            if prod.get('action') == 'reuse' or prod.get('id'):
                row['status'] = STATUS_REUSE
                row['statusLabel'] = STATUS_LABELS[STATUS_REUSE]
            else:
                row['status'] = STATUS_CREATE
                row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
            row['canCommit'] = True
            row['reasons'] = [
                r for r in (row.get('reasons') or [])
                if (
                    '色' not in stock.text(r)
                    and '加工' not in stock.text(r)
                    and '生地' not in stock.text(r)
                    and '括弧' not in stock.text(r)
                )
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


def halfwidth_katakana_to_fullwidth(text):
    """半角カタカナだけを全角へ（濁点・半濁点は NFKC で合成）。他記号は触らない。"""
    return paren_classify.halfwidth_katakana_to_fullwidth(stock.text(text))


def unify_import_notation(text):
    """取込用表記統一（半角カナ→全角・空白整理）。SKU/ASCII は全角化しない。"""
    return paren_classify.unify_import_notation(stock.text(text))


def normalize_ledger_name_for_vote(name):
    """同一通常品番内の名称票カウント用正規化。

    - 先頭中古標記「＊/*」は比較から除外（表示用原文は別保持）
    - 半角カタカナ → 全角カタカナ（濁点・半濁点を正しく合成）
    - 全角/半角空白 → 半角空白、連続を1つに、trim
    - 括弧内外の明示色を本体＋色キーへそろえる（位置差で票を割らない）
    - 明示名シノニム（帽子／キャップ等）を代表へ
    - 配色つなぎの /／×✕*＊ → ×（色要素確認時のみ。先頭中古標記の * と区別）
    - 色辞書の別名・誤記 → 代表色（共有 normalize。類似度では寄せない）
    - その他の記号は無条件に消さない
    - 品番・メーカー品番には使わない（先頭ゼロ維持）
    - 文字列全体への NFKC はしない（SKU・先頭ゼロを守る）
    """
    raw = stock.text(name)
    if not raw:
        return ''
    base_key, color_key, base_disp, color_disp = legacy.product_name_base_and_color(raw)
    if base_key:
        # 票キーは本体＋色。色なしは本体のみ（色省略の合算は上位で扱う）
        if color_key:
            return f'{base_disp or base_key} {color_disp or color_key}'.strip()
        return paren_classify.normalize_name_for_color_compare(base_disp or raw)
    return paren_classify.normalize_name_for_color_compare(raw)


def format_ledger_adopted_name(name):
    """採用表示名: 全角カタカナ・半角空白（票キーと同じ正規化結果）。"""
    return normalize_ledger_name_for_vote(name)


def registered_name_from_package(package):
    """登録予定名: 表記統一済み plannedName を優先（要確認時も統一表記）。"""
    pkg = package or {}
    planned = stock.text(pkg.get('plannedName') or '')
    source = stock.text(pkg.get('sourceName') or pkg.get('productName') or '')
    if pkg.get('parenNeedsConfirm'):
        # 要確認でも半角カナ等は統一（括弧構造は planned 側で維持済み）
        return planned or unify_import_notation(source) or source
    if planned:
        return planned
    return unify_import_notation(source) or source


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


def suggest_color_split_internal_code(maker_code, color):
    """社内管理品番の提案: メーカー品番 + '-' + 色名（先頭ゼロは落とさない）。"""
    maker = stock.text(maker_code)
    color_t = stock.text(color)
    if not maker or not color_t:
        return ''
    return f'{maker}-{color_t}'


def _row_color_extraction(row):
    """CSV行から明示色を抽出。曖昧・複数・無しは autoColor を空にする。"""
    csv_info = row.get('csv') or {}
    name = stock.text(csv_info.get('productName') or '')
    pc = row.get('parenClassification') if isinstance(row.get('parenClassification'), dict) else {}
    provenance = row.get('specProvenance') if isinstance(row.get('specProvenance'), dict) else {}
    split = paren_classify.split_name_and_color(name)
    extracted = paren_classify.extract_explicit_color_from_name(name)
    color = stock.text(
        provenance.get('color')
        or pc.get('color')
        or split.get('color')
        or extracted.get('color')
        or ''
    )
    needs = bool(
        split.get('needsConfirm')
        or extracted.get('needsConfirm')
        or pc.get('needsConfirm')
    )
    candidates = []
    for src in (
        provenance.get('colorCandidates'),
        pc.get('colorCandidates'),
        split.get('colorCandidates'),
        extracted.get('colorCandidates'),
    ):
        for c in (src or []):
            t = stock.text(c)
            if t and t not in candidates:
                candidates.append(t)
    if needs or (len({paren_classify.color_match_key(c) for c in candidates if c}) > 1):
        color = ''
    if not color and len(candidates) == 1 and not needs:
        color = candidates[0]
    planned_name = stock.text(
        provenance.get('plannedName')
        or split.get('plannedName')
        or pc.get('plannedName')
        or name
    )
    color_source = stock.text(
        provenance.get('colorSource')
        or split.get('colorSource')
        or extracted.get('colorSource')
        or ''
    )
    # 色が決まっているときだけ名から色を外した登録予定名を使う（括弧内色向け）
    if color and planned_name:
        product_name = planned_name
    else:
        product_name = name
    return {
        'color': color,
        'colorKey': paren_classify.color_match_key(color) if color else '',
        'colorCandidates': candidates,
        'needsConfirm': bool(needs or (not color and candidates)),
        'colorSource': color_source if color else '',
        'productName': product_name,
        'originalName': name,
        'processing': stock.text(
            provenance.get('processing') or pc.get('processing') or split.get('processing') or ''
        ),
        'material': stock.text(
            provenance.get('material') or pc.get('material') or split.get('material') or ''
        ),
    }


def _color_split_preferred_product_name(info):
    """色別登録の既定商品名。

    色が抽出できていれば色除去後の名（例: Tシャツ白 → Tシャツ、ポロシャツ（赤）→ ポロシャツ）。
    色不明時は原文。表示は名/色（例: Tシャツ/ホワイト）。
    """
    info = info if isinstance(info, dict) else {}
    orig = stock.text(info.get('originalName') or '')
    stripped = stock.text(info.get('productName') or '')
    color = stock.text(info.get('color') or '')
    if color and stripped:
        return stripped
    return stripped or orig


def color_split_target_display_label(product_name, color):
    """色別登録先の表示ラベル（名/色）。"""
    name = stock.text(product_name or '')
    col = stock.text(color or '')
    if name and col:
        return f'{name}/{col}'
    return name or col or ''


def estimate_color_split_count(group):
    """選択前の色候補件数。確定予定ではなく候補数として扱う。"""
    colors = []
    for v in (group.get('variants') or []):
        col = stock.text(v.get('color') or '')
        if not col:
            split = paren_classify.split_name_and_color(stock.text(v.get('productName') or ''))
            col = stock.text(split.get('color') or '')
        key = paren_classify.color_match_key(col) if col else ''
        if key and key not in colors:
            colors.append(key)
    if colors:
        return len(colors)
    # variants に色が無いときは商品名候補から推定
    for name in (group.get('nameCandidates') or []):
        split = paren_classify.split_name_and_color(stock.text(name or ''))
        col = stock.text(split.get('color') or '')
        key = paren_classify.color_match_key(col) if col else ''
        if key and key not in colors:
            colors.append(key)
    return len(colors)


def color_only_split_recommended(group):
    """色差のみで加工／内容差が無いとき色別分割を推奨（自動切替はしない。UI向け）。"""
    if not group or not group.get('colorDiffPossible'):
        return False
    reasons = group.get('reviewReasons') or []
    for r in reasons:
        code = stock.text((r or {}).get('code') or '')
        label = stock.text((r or {}).get('label') or '')
        if code in ('processing', 'content') or '加工' in label:
            return False
    # 加工サービス検出がある variants は内容別向き
    for v in (group.get('variants') or []):
        if stock.text(v.get('processing') or ''):
            return False
        ps = v.get('processingService') if isinstance(v.get('processingService'), dict) else {}
        if stock.text(ps.get('serviceName') or ''):
            return False
    return True


def color_split_summary_label(plans):
    """一覧用: 「色別登録：白・黒」"""
    colors = []
    for p in plans or []:
        col = stock.text((p or {}).get('color') or '')
        if col and col not in colors:
            colors.append(col)
    if not colors:
        return '色別登録（色未確定）'
    return '色別登録：' + '・'.join(colors)


def build_color_split_name_mappings(plans, unassigned_rows=None, *, row_by_no=None):
    """色別の登録予定から名称対応表を作る（統合用 namePlan は使わない）。"""
    mappings = []
    seen = set()
    row_by_no = row_by_no if isinstance(row_by_no, dict) else {}
    for plan in plans or []:
        pname = stock.text(plan.get('productName') or '')
        color = stock.text(plan.get('color') or '')
        for rn in plan.get('rowNos') or []:
            try:
                row_no = int(rn)
            except (TypeError, ValueError):
                continue
            key = (row_no, pname, color)
            if key in seen:
                continue
            seen.add(key)
            row = row_by_no.get(row_no) or {}
            orig = stock.text((row.get('csv') or {}).get('productName') or '')
            if not orig:
                info = _row_color_extraction(row) if row else {}
                orig = stock.text(info.get('originalName') or '') or pname
            mappings.append({
                'originalName': orig,
                'sourceRowNo': row_no,
                'registrationName': pname,
                'isAlias': bool(orig and pname and stock.text(orig) != stock.text(pname)),
                'color': color,
            })
    for u in unassigned_rows or []:
        try:
            row_no = int(u.get('rowNo'))
        except (TypeError, ValueError):
            continue
        orig = stock.text(u.get('originalName') or '')
        mappings.append({
            'originalName': orig,
            'sourceRowNo': row_no,
            'registrationName': '',
            'isAlias': False,
            'color': '',
            'unassigned': True,
            'reason': stock.text(u.get('reason') or '行き先未定'),
        })
    return mappings


def apply_color_split_name_plan_display(group):
    """色別選択中は統合用の仮代表名・別名対応を有効予定として見せない。"""
    split = group.get('colorSplitPlans') if isinstance(group.get('colorSplitPlans'), dict) else {}
    plans = split.get('plans') or []
    unassigned = split.get('unassignedRows') or []
    summary = color_split_summary_label(plans)
    mappings = list(split.get('nameMappings') or [])
    if not mappings:
        mappings = build_color_split_name_mappings(plans, unassigned)
    group['plannedOfficialName'] = summary
    group['nameAdoptionRule'] = '色別に分けて登録'
    group['provisionalName'] = False
    group['namePlan'] = {
        'officialName': summary,
        'provisional': False,
        'provisionalTag': '',
        'provisionalStatus': '',
        'adoptionRule': '色別に分けて登録（統合用の仮代表名・別名対応は使いません）',
        'adoptionRuleCode': 'color_split',
        'nameMappings': mappings,
        'nameCandidates': [
            stock.text(p.get('productName') or '') for p in plans
            if stock.text(p.get('productName') or '')
        ],
        'auxNameReasons': [],
        'keepsExistingOfficialName': False,
        'colorSplit': True,
    }


def build_color_split_plans(c, group, decision_raw=None, *, classified=None):
    """同一メーカー品番を色ごとに別商品として登録する予定を組み立てる。

    - 緑と蛍光グリーンは別色（color_match_key で同一視しない）
    - 色不明・複数解釈・ラベルなしは自動割当しない
    - 社内品番衝突は use-existing / 品番編集を求め、無条件の上書きはしない
    - クリック時点では保存しない（プレビューのみ）
    """
    raw = decision_raw if isinstance(decision_raw, dict) else {}
    maker_code = stock.text(group.get('code') or '')
    user_plans = raw.get('colorPlans') if isinstance(raw.get('colorPlans'), dict) else {}
    row_destinations = raw.get('rowDestinations') if isinstance(raw.get('rowDestinations'), dict) else {}

    row_by_no = {}
    for row in (classified or []):
        try:
            row_by_no[int(row.get('rowNo'))] = row
        except (TypeError, ValueError):
            continue

    color_buckets = {}  # colorKey -> plan draft
    unassigned_rows = []

    def _ensure_bucket(color_display, product_name_hint=''):
        key = paren_classify.color_match_key(color_display)
        if not key:
            return None
        if key not in color_buckets:
            suggested = suggest_color_split_internal_code(maker_code, color_display)
            user = user_plans.get(key) or user_plans.get(color_display) or {}
            if not isinstance(user, dict):
                user = {}
            internal = stock.text(user.get('internalCode') or user.get('code') or '') or suggested
            pname = stock.text(user.get('productName') or product_name_hint or '')
            color_val = stock.text(user.get('color') or color_display)
            color_buckets[key] = {
                'colorKey': key,
                'color': color_val,
                'makerCode': maker_code,
                'csvProductCode': maker_code,
                'internalCode': internal,
                'suggestedInternalCode': suggested,
                'productName': pname,
                'displayLabel': color_split_target_display_label(pname, color_val),
                'category': stock.text(user.get('category') or '商品'),
                'supplier': stock.text(user.get('supplier') or ''),
                'material': stock.text(user.get('material') or ''),
                'notes': stock.text(user.get('notes') or ''),
                'processing': stock.text(user.get('processing') or ''),
                'rowNos': [],
                'sizes': [],
                'customers': [],
                'rowSpecsSamples': [],
                'keepProductId': user.get('keepProductId') or user.get('productId'),
                'collisionAction': stock.text(user.get('collisionAction') or ''),
                'existingProducts': [],
                'collision': False,
                'needsCollisionChoice': False,
                'statusLabel': '色別分割案・未反映',
            }
        return color_buckets[key]

    source_rows = []
    for rn in (group.get('rowNos') or []):
        row = row_by_no.get(int(rn))
        if row:
            source_rows.append(row)
    if not source_rows:
        # variants から擬似行（classified 未渡し時のフォールバック）
        for v in (group.get('variants') or []):
            for rn in (v.get('rowNos') or []):
                source_rows.append({
                    'rowNo': rn,
                    'csv': {
                        'code': maker_code,
                        'productName': v.get('productName') or (v.get('csvNames') or [''])[0],
                        'supplier': v.get('supplier') or '',
                        'size': '',
                        'customer': '',
                    },
                    'parenClassification': {},
                    'specProvenance': {},
                })

    def _inherit_color_row(bucket, row, info, preferred_name):
        """ソース行から仕入・サイズ・仕様を継承（空欄のみ埋め、一括上書きしない）。"""
        if not bucket:
            return
        row_no = int(row.get('rowNo') or 0)
        if row_no and row_no not in bucket['rowNos']:
            bucket['rowNos'].append(row_no)
        if preferred_name and not bucket.get('productName'):
            bucket['productName'] = preferred_name
        csv = row.get('csv') or {}
        for field, val in (
            ('supplier', csv.get('supplier') or ''),
            ('material', info.get('material') or ''),
            ('notes', info.get('processing') or ''),
            ('processing', info.get('processing') or ''),
        ):
            if stock.text(val) and not stock.text(bucket.get(field) or ''):
                bucket[field] = stock.text(val)
        cust = stock.text(csv.get('customer') or '')
        if cust and cust not in bucket['customers']:
            bucket['customers'].append(cust)
        size = stock.text(csv.get('size') or '')
        if size and size not in bucket['sizes']:
            bucket['sizes'].append(size)
        bucket['displayLabel'] = color_split_target_display_label(
            bucket.get('productName'), bucket.get('color'),
        )
        sample = {
            'rowNo': row_no,
            'originalName': csv.get('productName') or '',
            'originalCode': maker_code,
            'customer': cust,
            'size': size,
            'supplier': csv.get('supplier') or '',
            'color': bucket.get('color') or '',
            'material': bucket.get('material') or '',
            'notes': bucket.get('notes') or '',
            'processing': bucket.get('processing') or '',
            'splitTargetId': f"{maker_code}::{bucket.get('colorKey') or bucket.get('color') or ''}",
        }
        samples = list(bucket.get('rowSpecsSamples') or [])
        if row_no and not any(int(s.get('rowNo') or 0) == row_no for s in samples):
            samples.append(sample)
            bucket['rowSpecsSamples'] = samples[:12]

    row_info_by_no = {}
    for row in source_rows:
        row_no = int(row.get('rowNo') or 0)
        info = _row_color_extraction(row)
        row_info_by_no[row_no] = info
        preferred_name = _color_split_preferred_product_name(info)
        dest_key = stock.text(row_destinations.get(str(row_no)) or row_destinations.get(row_no) or '')
        if dest_key:
            # ユーザー指定の行き先（色キーまたは色表示）
            bucket = _ensure_bucket(dest_key, preferred_name or info.get('originalName'))
            if bucket:
                _inherit_color_row(bucket, row, info, preferred_name)
                continue
        if info['color'] and not info['needsConfirm']:
            bucket = _ensure_bucket(info['color'], preferred_name or info.get('originalName'))
            if bucket:
                _inherit_color_row(bucket, row, info, preferred_name)
                continue
        unassigned_rows.append({
            'rowNo': row_no,
            'originalName': info.get('originalName') or '',
            'colorCandidates': list(info.get('colorCandidates') or []),
            'reason': (
                '色が不明・複数解釈のため自動割当しません'
                if (info.get('needsConfirm') or info.get('colorCandidates'))
                else '色ラベルがないため行き先を選んでください'
            ),
        })

    # ユーザーが colorPlans だけ追加した場合も残す
    for uk, user in user_plans.items():
        if not isinstance(user, dict):
            continue
        color_disp = stock.text(user.get('color') or uk)
        if not color_disp:
            continue
        bucket = _ensure_bucket(color_disp, user.get('productName') or '')
        if bucket and stock.text(user.get('internalCode') or ''):
            bucket['internalCode'] = stock.text(user.get('internalCode'))

    # 衝突検査（既存商品・計画同士）
    seen_internal = {}
    plans = []
    for key in sorted(color_buckets.keys()):
        plan = color_buckets[key]
        internal = stock.text(plan.get('internalCode') or '')
        existing = []
        if internal and stock_product_merge.is_normal_product_code(internal):
            existing = [
                {
                    'id': r['id'],
                    'code': r['code'],
                    'name': r['name'],
                    'color': r['color'] or '',
                    'supplierName': r['supplier_name'] or '',
                }
                for r in stock_product_merge.find_live_products_by_code(c, internal)
            ]
        plan['existingProducts'] = existing
        collision = bool(existing)
        if internal and internal in seen_internal:
            collision = True
            plan['collidesWithPlan'] = seen_internal[internal]
        else:
            plan['collidesWithPlan'] = ''
        if internal:
            seen_internal[internal] = plan['color']
        plan['collision'] = collision
        keep_id = plan.get('keepProductId')
        action = stock.text(plan.get('collisionAction') or '')
        if collision:
            if keep_id and any(int(p['id']) == int(keep_id) for p in existing):
                plan['needsCollisionChoice'] = False
                plan['collisionAction'] = action or 'reuse'
            elif action == 'edit' and internal and internal != plan.get('suggestedInternalCode'):
                # 編集後に衝突が消えていればOK（上で existing 再評価済み）
                plan['needsCollisionChoice'] = bool(existing) or bool(plan.get('collidesWithPlan'))
            else:
                plan['needsCollisionChoice'] = True
        else:
            plan['needsCollisionChoice'] = False
        if not plan.get('productName'):
            # フォールバック: 割当行の原文／除去後名（統合用仮代表名は使わない）
            for rn in plan.get('rowNos') or []:
                info = row_info_by_no.get(int(rn)) or {}
                hint = _color_split_preferred_product_name(info)
                if hint:
                    plan['productName'] = hint
                    break
            if not plan.get('productName'):
                plan['productName'] = stock.text(
                    ((group.get('nameCandidates') or [None])[0])
                    or maker_code
                )
        plan['displayLabel'] = color_split_target_display_label(
            plan.get('productName'), plan.get('color'),
        )
        plan['statusLabel'] = (
            '確認済み'
            if (
                plan.get('rowNos')
                and not plan.get('needsCollisionChoice')
                and stock.text(plan.get('internalCode') or '')
                and stock.text(plan.get('productName') or '')
            )
            else '色別分割案・未反映'
        )
        plans.append(plan)

    complete = bool(plans) and not unassigned_rows and not any(p.get('needsCollisionChoice') for p in plans)
    name_mappings = build_color_split_name_mappings(
        plans,
        unassigned_rows,
        row_by_no={int(r.get('rowNo') or 0): r for r in source_rows if r.get('rowNo') is not None},
    )
    return {
        'makerCode': maker_code,
        'plans': plans,
        'planCount': len(plans),
        'unassignedRows': unassigned_rows,
        'complete': complete,
        'summaryLabel': color_split_summary_label(plans),
        'nameMappings': name_mappings,
        'note': (
            '色ごとに別商品として登録予定です（まだ保存していません）。'
            '社内管理品番は編集できます。メーカー品番は元の品番のままです。'
            '統合用の仮代表名・別名対応は使いません。'
        ),
    }


def attach_color_split_plans(c, sku_groups, sku_decisions, *, classified=None):
    """色分割を選んだ品番（または再照合で保持）に予定を付与。在庫自動まとめ対象外。"""
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    for group in sku_groups:
        code = stock.text(group.get('code') or '')
        # 候補件数は方針未選択でも見積もる（確定予定ではない）
        group['colorSplitEstimateCount'] = estimate_color_split_count(group)
        group['colorOnlySplitRecommended'] = color_only_split_recommended(group)
        raw = decisions.get(code) or decisions.get(str(code)) or {}
        if not isinstance(raw, dict):
            continue
        if stock.text(raw.get('decision')) != SKU_DECISION_COLOR_SPLIT:
            continue
        group['colorSplit'] = True
        group['excludeAuxStockAutoMerge'] = True
        # 在庫数量によるメイン自動採用を抑止（選択は再照合でも維持）
        if group.get('auxStockPickAuto'):
            group['auxStockPickAuto'] = False
            group['needsDecision'] = True
            group['plannedSingleProduct'] = False
            group['status'] = 'needs_decision'
        group['colorSplitPlans'] = build_color_split_plans(
            c, group, raw, classified=classified,
        )
        apply_color_split_name_plan_display(group)


def suggest_content_split_internal_code(maker_code, content_label):
    """商品向け社内管理品番の提案: メーカー品番 + '-' + 内容ラベル（先頭ゼロ維持）。"""
    maker = stock.text(maker_code)
    label = stock.text(content_label)
    if not maker or not label:
        return ''
    safe = (
        label.replace('|', '-')
        .replace('/', '-')
        .replace('\\', '-')
        .strip()
    )
    if len(safe) > 40:
        safe = safe[:40].rstrip('-')
    return f'{maker}-{safe}' if safe else ''


def _inherit_content_field(plan, key, value):
    """ソース行の値を継承。空欄だけ埋める（一括上書きしない）。"""
    if value is None:
        return
    text_v = stock.text(value) if not isinstance(value, (list, dict, bool, int, float)) else value
    if text_v == '' or text_v is None:
        return
    cur = plan.get(key)
    if cur is None or cur == '':
        plan[key] = text_v


def _row_content_split_info(row, user_plan=None):
    """カテゴリ＋編集済み商品名／名称／加工名から内容キーを作る。サイズは含めない。"""
    csv = row.get('csv') or {}
    pc = row.get('parenClassification') if isinstance(row.get('parenClassification'), dict) else {}
    ps = row.get('processingService') if isinstance(row.get('processingService'), dict) else {}
    prov = row.get('specProvenance') if isinstance(row.get('specProvenance'), dict) else {}
    prod = row.get('product') if isinstance(row.get('product'), dict) else {}
    user = user_plan if isinstance(user_plan, dict) else {}

    supplier = stock.text(user.get('supplier') or csv.get('supplier') or '')
    color = stock.text(
        user.get('color') or prov.get('color') or pc.get('color') or ''
    )
    material = stock.text(
        user.get('material') or prov.get('material') or pc.get('material') or ''
    )
    notes = stock.text(
        user.get('notes')
        or user.get('supplement')
        or pc.get('supplement')
        or ''
    )
    product_name = stock.text(
        user.get('productName')
        or user.get('officialName')
        or pc.get('plannedName')
        or csv.get('productName')
        or ''
    )
    service_name = stock.text(
        user.get('serviceName')
        or user.get('processing')
        or ps.get('serviceName')
        or pc.get('processing')
        or ''
    )
    vendor = stock.text(
        user.get('vendorName')
        or user.get('processingVendor')
        or ps.get('vendorName')
        or pc.get('processingVendor')
        or supplier
    )
    category = stock.text(
        user.get('category')
        or prod.get('category')
        or prod.get('proposedCategory')
        or ''
    )

    classified = {}
    try:
        classified = proc_svc.classify_processing_service_row(
            supplier=vendor or supplier,
            product_name=product_name,
            code=csv.get('code') or '',
            paren_processing=service_name or pc.get('processing') or '',
        ) or {}
    except Exception:
        classified = {}

    user_kind = stock.text(user.get('kind') or user.get('targetKind') or '')
    is_proc = bool(
        stock.is_process_service_category(category)
        or user_kind == 'process_service'
        or ps.get('isProcessingService')
        or row.get('processingServiceView')
        or classified.get('isProcessingService')
    )

    if is_proc:
        raw_svc = (
            service_name
            or classified.get('serviceName')
            or proc_svc.unify_processing_service_name(product_name)
            or product_name
        )
        unified = proc_svc.unify_processing_service_name(raw_svc) or stock.text(raw_svc)
        vend = stock.text(classified.get('vendorName') or vendor or supplier)
        if unified and vend:
            dedupe = proc_svc.service_dedupe_key(unified, vend)
            return {
                'kind': 'process_service',
                'contentKey': f'ps|{dedupe}',
                'dedupeKey': dedupe,
                'category': stock.CATEGORY_PROCESS_SERVICE,
                'serviceName': unified,
                'vendorName': vend,
                'productName': unified,
                'supplier': vend,
                'color': color,
                'material': material,
                'notes': notes,
                'processing': unified,
                'blankSku': True,
                'identifiable': True,
                'originalName': stock.text(csv.get('productName') or product_name),
            }

    processing = stock.text(service_name or pc.get('processing') or '')
    name_core = product_name_core_key(product_name) or identity_match_key(product_name)
    color_key = ''
    if color:
        try:
            color_key = paren_classify.color_match_key(color) or identity_match_key(color)
        except Exception:
            color_key = identity_match_key(color)
    elif product_name:
        try:
            _bk, color_key, _bd, color_disp = legacy.product_name_base_and_color(product_name)
            if color_disp and not color:
                color = color_disp
        except Exception:
            color_key = ''
    proc_key = (
        identity_match_key(processing) if processing else _name_processing_key(product_name)
    )
    content_key = '|'.join([
        'prod',
        name_core or '',
        color_key or '',
        proc_key or '',
        identity_match_key(supplier),
    ])
    return {
        'kind': 'product',
        'contentKey': content_key,
        'dedupeKey': content_key,
        'category': category or '',
        'productName': product_name,
        'supplier': supplier,
        'vendorName': '',
        'serviceName': '',
        'color': color,
        'material': material,
        'notes': notes,
        'processing': processing,
        'blankSku': False,
        'identifiable': bool(product_name),
        'originalName': stock.text(csv.get('productName') or product_name),
    }


def content_split_summary_label(plans):
    """一覧用: 「加工サービス2件」／「商品2件」／混在。"""
    plans = list(plans or [])
    if not plans:
        return '内容別登録（内容未確定）'
    n_proc = sum(1 for p in plans if stock.text(p.get('kind') or '') == 'process_service')
    n_prod = sum(1 for p in plans if stock.text(p.get('kind') or '') != 'process_service')
    bits = []
    if n_proc:
        bits.append(f'加工サービス{n_proc}件')
    if n_prod:
        bits.append(f'商品{n_prod}件')
    if not bits:
        bits.append(f'登録先{len(plans)}件')
    return '・'.join(bits)


def estimate_content_split_count(group):
    """選択前の件数見積り。variants はサイズのみ差を既に統合済み。"""
    variants = list(group.get('variants') or [])
    if variants:
        return len(variants)
    row_nos = group.get('rowNos') or []
    return len(row_nos) if row_nos else 0


def build_content_split_name_mappings(plans, unassigned_rows=None, *, row_by_no=None):
    """内容別の登録予定から名称対応表を作る（統合用 namePlan は使わない）。"""
    mappings = []
    seen = set()
    row_by_no = row_by_no if isinstance(row_by_no, dict) else {}
    for plan in plans or []:
        pname = stock.text(plan.get('productName') or plan.get('serviceName') or '')
        kind = stock.text(plan.get('kind') or '')
        for rn in plan.get('rowNos') or []:
            try:
                row_no = int(rn)
            except (TypeError, ValueError):
                continue
            key = (row_no, pname, kind, stock.text(plan.get('contentKey') or ''))
            if key in seen:
                continue
            seen.add(key)
            row = row_by_no.get(row_no) or {}
            orig = stock.text((row.get('csv') or {}).get('productName') or '')
            if not orig:
                orig = stock.text(plan.get('originalName') or '') or pname
            mappings.append({
                'originalName': orig,
                'sourceRowNo': row_no,
                'registrationName': pname,
                'isAlias': bool(orig and pname and stock.text(orig) != stock.text(pname)),
                'kind': kind,
                'splitTargetId': stock.text(plan.get('splitTargetId') or ''),
                'contentKey': stock.text(plan.get('contentKey') or ''),
            })
    for u in unassigned_rows or []:
        try:
            row_no = int(u.get('rowNo'))
        except (TypeError, ValueError):
            continue
        mappings.append({
            'originalName': stock.text(u.get('originalName') or ''),
            'sourceRowNo': row_no,
            'registrationName': '',
            'isAlias': False,
            'kind': '',
            'unassigned': True,
            'reason': stock.text(u.get('reason') or '行き先未定'),
        })
    return mappings


def apply_content_split_name_plan_display(group):
    """内容別選択中は統合用の仮代表名・別名対応を有効予定として見せない。"""
    split = group.get('contentSplitPlans') if isinstance(group.get('contentSplitPlans'), dict) else {}
    plans = split.get('plans') or []
    unassigned = split.get('unassignedRows') or []
    summary = content_split_summary_label(plans)
    mappings = list(split.get('nameMappings') or [])
    if not mappings:
        mappings = build_content_split_name_mappings(plans, unassigned)
    group['plannedOfficialName'] = summary
    group['nameAdoptionRule'] = '内容別に分けて登録'
    group['provisionalName'] = False
    group['namePlan'] = {
        'officialName': summary,
        'provisional': False,
        'provisionalTag': '',
        'provisionalStatus': '',
        'adoptionRule': '内容別に分けて登録（統合用の仮代表名・別名対応は使いません）',
        'adoptionRuleCode': 'content_split',
        'nameMappings': mappings,
        'nameCandidates': [
            stock.text(p.get('productName') or p.get('serviceName') or '')
            for p in plans
            if stock.text(p.get('productName') or p.get('serviceName') or '')
        ],
        'auxNameReasons': [],
        'keepsExistingOfficialName': False,
        'contentSplit': True,
        'colorSplit': False,
    }


def build_content_split_plans(c, group, decision_raw=None, *, classified=None):
    """同一メーカー品番を内容（カテゴリ＋名／加工名）ごとに別登録先へ分ける予定。

    - サイズだけの差では増やさない（正規化キーで統合）
    - 真の重複はマージ。加工名・仕様差は残す
    - 加工サービスは 加工名+業者 で再利用キー。商品は社内管理品番の割当／確認
    - 仕入先／業者／色／素材／補足はソース行から継承（一括上書きしない）
    - クリック時点では保存しない（プレビューのみ）
    """
    raw = decision_raw if isinstance(decision_raw, dict) else {}
    maker_code = stock.text(group.get('code') or '')
    user_plans = raw.get('contentPlans') if isinstance(raw.get('contentPlans'), dict) else {}
    row_destinations = (
        raw.get('contentRowDestinations')
        if isinstance(raw.get('contentRowDestinations'), dict)
        else {}
    )

    row_by_no = {}
    for row in (classified or []):
        try:
            row_by_no[int(row.get('rowNo'))] = row
        except (TypeError, ValueError):
            continue

    buckets = {}  # contentKey -> plan draft
    unassigned_rows = []

    def _ensure_bucket(info, user=None):
        key = stock.text((info or {}).get('contentKey') or '')
        if not key:
            return None
        user = user if isinstance(user, dict) else {}
        if key not in buckets:
            kind = stock.text(user.get('kind') or info.get('kind') or 'product')
            pname = stock.text(
                user.get('productName')
                or user.get('serviceName')
                or info.get('productName')
                or info.get('serviceName')
                or ''
            )
            suggested = ''
            if kind != 'process_service':
                suggested = suggest_content_split_internal_code(maker_code, pname)
            internal = stock.text(user.get('internalCode') or user.get('code') or '') or suggested
            split_target_id = stock.text(
                user.get('splitTargetId') or f'{maker_code}::{key}'
            )
            buckets[key] = {
                'contentKey': key,
                'splitTargetId': split_target_id,
                'kind': kind,
                'category': stock.text(
                    user.get('category') or info.get('category') or ''
                ),
                'makerCode': maker_code,
                'csvProductCode': maker_code,
                'internalCode': '' if kind == 'process_service' else internal,
                'suggestedInternalCode': '' if kind == 'process_service' else suggested,
                'blankSku': kind == 'process_service',
                'productName': pname,
                'serviceName': stock.text(
                    user.get('serviceName') or info.get('serviceName') or ''
                ),
                'vendorName': stock.text(
                    user.get('vendorName') or info.get('vendorName') or ''
                ),
                'supplier': stock.text(user.get('supplier') or info.get('supplier') or ''),
                'color': stock.text(user.get('color') or info.get('color') or ''),
                'material': stock.text(user.get('material') or info.get('material') or ''),
                'notes': stock.text(user.get('notes') or info.get('notes') or ''),
                'processing': stock.text(
                    user.get('processing') or info.get('processing') or ''
                ),
                'dedupeKey': stock.text(
                    user.get('dedupeKey') or info.get('dedupeKey') or key
                ),
                'originalName': stock.text(info.get('originalName') or ''),
                'rowNos': [],
                'customers': [],
                'sizes': [],
                'rowSpecsSamples': [],
                'keepProductId': user.get('keepProductId') or user.get('productId'),
                'collisionAction': stock.text(user.get('collisionAction') or ''),
                'existingProducts': [],
                'collision': False,
                'needsCollisionChoice': False,
                'needsInternalCode': kind != 'process_service',
                'statusLabel': '内容別分割案・未反映',
            }
        return buckets[key]

    source_rows = []
    for rn in (group.get('rowNos') or []):
        row = row_by_no.get(int(rn))
        if row:
            source_rows.append(row)
    if not source_rows:
        for v in (group.get('variants') or []):
            for rn in (v.get('rowNos') or []):
                source_rows.append({
                    'rowNo': rn,
                    'csv': {
                        'code': maker_code,
                        'productName': v.get('productName') or (v.get('csvNames') or [''])[0],
                        'supplier': v.get('supplier') or '',
                        'size': '',
                        'customer': '',
                    },
                    'parenClassification': {
                        'processing': v.get('processing') or '',
                        'color': v.get('color') or '',
                    },
                    'processingService': {},
                    'specProvenance': {},
                    'product': {},
                })

    for row in source_rows:
        row_no = int(row.get('rowNo') or 0)
        dest_key = stock.text(
            row_destinations.get(str(row_no)) or row_destinations.get(row_no) or ''
        )
        user_for_row = {}
        if dest_key and dest_key in user_plans and isinstance(user_plans.get(dest_key), dict):
            user_for_row = user_plans[dest_key]
        elif dest_key:
            # 行き先キーだけ指定された場合
            user_for_row = {'contentKey': dest_key}
        info = _row_content_split_info(row, user_for_row if user_for_row else None)
        if dest_key and not info.get('contentKey'):
            info['contentKey'] = dest_key
        # ユーザー上書き（contentPlans）をキー単位で優先
        user = user_plans.get(info.get('contentKey') or '') or user_plans.get(dest_key) or {}
        if not isinstance(user, dict):
            user = {}
        if user:
            # 編集値を info に反映（空で消さない）
            for field in (
                'kind', 'productName', 'serviceName', 'vendorName', 'supplier',
                'color', 'material', 'notes', 'processing', 'category', 'internalCode',
            ):
                if stock.text(user.get(field) or '') or field in ('internalCode',):
                    if field in user and user.get(field) is not None:
                        if field == 'internalCode' or stock.text(user.get(field) or ''):
                            info[field] = user.get(field)
            if stock.text(user.get('kind') or ''):
                info['kind'] = stock.text(user.get('kind'))
            if stock.text(user.get('contentKey') or ''):
                info['contentKey'] = stock.text(user.get('contentKey'))

        if not info.get('identifiable') or not info.get('contentKey'):
            unassigned_rows.append({
                'rowNo': row_no,
                'originalName': stock.text((row.get('csv') or {}).get('productName') or ''),
                'reason': '内容（商品名／加工名）が特定できないため自動割当しません',
            })
            continue

        bucket = _ensure_bucket(info, user)
        if not bucket:
            unassigned_rows.append({
                'rowNo': row_no,
                'originalName': stock.text((row.get('csv') or {}).get('productName') or ''),
                'reason': '内容キーを作れません',
            })
            continue
        if row_no not in bucket['rowNos']:
            bucket['rowNos'].append(row_no)
        # ソース行から継承（空欄のみ）
        csv = row.get('csv') or {}
        _inherit_content_field(bucket, 'supplier', csv.get('supplier') or info.get('supplier'))
        _inherit_content_field(bucket, 'vendorName', info.get('vendorName'))
        _inherit_content_field(bucket, 'color', info.get('color'))
        _inherit_content_field(bucket, 'material', info.get('material'))
        _inherit_content_field(bucket, 'notes', info.get('notes'))
        _inherit_content_field(bucket, 'processing', info.get('processing'))
        _inherit_content_field(bucket, 'productName', info.get('productName'))
        _inherit_content_field(bucket, 'serviceName', info.get('serviceName'))
        _inherit_content_field(bucket, 'originalName', info.get('originalName'))
        cust = stock.text(csv.get('customer') or '')
        if cust and cust not in bucket['customers']:
            bucket['customers'].append(cust)
        size = stock.text(csv.get('size') or '')
        if size and size not in bucket['sizes']:
            bucket['sizes'].append(size)
        sample = {
            'rowNo': row_no,
            'originalName': csv.get('productName') or '',
            'originalCode': maker_code,
            'customer': cust,
            'size': size,
            'supplier': csv.get('supplier') or '',
            'color': info.get('color') or '',
            'material': info.get('material') or '',
            'notes': info.get('notes') or '',
            'processing': info.get('processing') or '',
            'splitTargetId': bucket['splitTargetId'],
        }
        samples = list(bucket.get('rowSpecsSamples') or [])
        if not any(int(s.get('rowNo') or 0) == row_no for s in samples):
            samples.append(sample)
            bucket['rowSpecsSamples'] = samples[:12]

    # ユーザーが contentPlans だけ追加した場合も残す
    for uk, user in user_plans.items():
        if not isinstance(user, dict):
            continue
        key = stock.text(user.get('contentKey') or uk)
        if not key:
            continue
        info = {
            'contentKey': key,
            'kind': stock.text(user.get('kind') or 'product'),
            'productName': stock.text(user.get('productName') or user.get('serviceName') or ''),
            'serviceName': stock.text(user.get('serviceName') or ''),
            'vendorName': stock.text(user.get('vendorName') or ''),
            'supplier': stock.text(user.get('supplier') or ''),
            'category': stock.text(user.get('category') or ''),
            'dedupeKey': stock.text(user.get('dedupeKey') or key),
            'color': stock.text(user.get('color') or ''),
            'material': stock.text(user.get('material') or ''),
            'notes': stock.text(user.get('notes') or ''),
            'processing': stock.text(user.get('processing') or ''),
            'identifiable': True,
            'originalName': stock.text(user.get('originalName') or ''),
        }
        bucket = _ensure_bucket(info, user)
        if bucket and stock.text(user.get('internalCode') or '') and bucket.get('kind') != 'process_service':
            bucket['internalCode'] = stock.text(user.get('internalCode'))

    # 衝突検査（商品のみ。加工サービスは品番なし）
    seen_internal = {}
    plans = []
    for key in sorted(buckets.keys()):
        plan = buckets[key]
        if plan.get('kind') == 'process_service':
            plan['blankSku'] = True
            plan['internalCode'] = ''
            plan['needsInternalCode'] = False
            plan['needsCollisionChoice'] = False
            plan['collision'] = False
            # 加工名＋業者必須
            if not stock.text(plan.get('serviceName') or '') or not stock.text(plan.get('vendorName') or ''):
                plan['needsFields'] = True
            else:
                plan['needsFields'] = False
                existing_svc = proc_svc.find_existing_processing_service(
                    c,
                    plan.get('serviceName') or '',
                    plan.get('vendorName') or '',
                )
                if existing_svc:
                    plan['keepProductId'] = existing_svc['id']
                    plan['action'] = 'reuse'
                    plan['existingProducts'] = [{
                        'id': existing_svc['id'],
                        'code': existing_svc.get('code') or '',
                        'name': existing_svc.get('name') or '',
                        'supplierName': plan.get('vendorName') or '',
                    }]
                else:
                    plan['action'] = 'create'
            plans.append(plan)
            continue

        internal = stock.text(plan.get('internalCode') or '')
        existing = []
        if internal and stock_product_merge.is_normal_product_code(internal):
            existing = [
                {
                    'id': r['id'],
                    'code': r['code'],
                    'name': r['name'],
                    'color': r['color'] or '',
                    'supplierName': r['supplier_name'] or '',
                }
                for r in stock_product_merge.find_live_products_by_code(c, internal)
            ]
        plan['existingProducts'] = existing
        collision = bool(existing)
        if internal and internal in seen_internal:
            collision = True
            plan['collidesWithPlan'] = seen_internal[internal]
        else:
            plan['collidesWithPlan'] = ''
        if internal:
            seen_internal[internal] = plan.get('productName') or key
        plan['collision'] = collision
        keep_id = plan.get('keepProductId')
        action = stock.text(plan.get('collisionAction') or '')
        if not internal:
            plan['needsInternalCode'] = True
            plan['needsCollisionChoice'] = True
        elif collision:
            if keep_id and any(int(p['id']) == int(keep_id) for p in existing):
                plan['needsCollisionChoice'] = False
                plan['collisionAction'] = action or 'reuse'
                plan['action'] = 'reuse'
            elif action == 'edit' and internal and internal != plan.get('suggestedInternalCode'):
                plan['needsCollisionChoice'] = bool(existing) or bool(plan.get('collidesWithPlan'))
                plan['action'] = 'create'
            else:
                plan['needsCollisionChoice'] = True
                plan['action'] = 'create'
        else:
            plan['needsCollisionChoice'] = False
            plan['action'] = 'create'
        if not plan.get('productName'):
            plan['productName'] = stock.text(
                ((group.get('nameCandidates') or [None])[0]) or maker_code
            )
        if plan.get('kind') == 'process_service':
            plan['statusLabel'] = (
                '確認済み'
                if (
                    plan.get('rowNos')
                    and stock.text(plan.get('serviceName') or '')
                    and stock.text(plan.get('vendorName') or '')
                    and not plan.get('needsFields')
                )
                else '内容別分割案・未反映'
            )
        else:
            plan['statusLabel'] = (
                '確認済み'
                if (
                    plan.get('rowNos')
                    and not plan.get('needsCollisionChoice')
                    and not plan.get('needsFields')
                    and stock.text(plan.get('internalCode') or '')
                    and stock.text(plan.get('productName') or '')
                )
                else '内容別分割案・未反映'
            )
        plans.append(plan)

    complete = (
        bool(plans)
        and not unassigned_rows
        and not any(
            p.get('needsCollisionChoice') or p.get('needsFields')
            for p in plans
        )
    )
    name_mappings = build_content_split_name_mappings(
        plans,
        unassigned_rows,
        row_by_no={int(r.get('rowNo') or 0): r for r in source_rows if r.get('rowNo') is not None},
    )
    return {
        'makerCode': maker_code,
        'plans': plans,
        'unassignedRows': unassigned_rows,
        'complete': complete,
        'planCount': len(plans),
        'summaryLabel': content_split_summary_label(plans),
        'nameMappings': name_mappings,
        'preferForProcess': any(
            stock.text(p.get('kind') or '') == 'process_service' for p in plans
        ),
        'note': (
            '内容（商品名／加工名など）ごとに別登録先として登録予定です（まだ保存していません）。'
            'サイズだけの差は統合します。加工サービスは加工名＋業者で再利用し、'
            '別加工名は別名統合しません。メーカー品番・旧CSV品番は元のままです。'
            '統合用の仮代表名・別名対応は使いません。'
        ),
    }


def attach_content_split_plans(c, sku_groups, sku_decisions, *, classified=None):
    """内容分割を選んだ品番に予定を付与。在庫自動まとめ対象外。"""
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    for group in sku_groups:
        code = stock.text(group.get('code') or '')
        # 選択前でも件数見積りを載せる（ボタン表示用）
        group['contentSplitEstimateCount'] = estimate_content_split_count(group)
        raw = decisions.get(code) or decisions.get(str(code)) or {}
        if not isinstance(raw, dict):
            continue
        if stock.text(raw.get('decision')) != SKU_DECISION_CONTENT_SPLIT:
            continue
        group['contentSplit'] = True
        group['excludeAuxStockAutoMerge'] = True
        if group.get('auxStockPickAuto'):
            group['auxStockPickAuto'] = False
            group['needsDecision'] = True
            group['plannedSingleProduct'] = False
            group['status'] = 'needs_decision'
        group['contentSplitPlans'] = build_content_split_plans(
            c, group, raw, classified=classified,
        )
        apply_content_split_name_plan_display(group)


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
    maker_sku_lookup.ensure_schema(c)


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
    # マップの品番キーは原文（初期在庫の旧表記照合用）。登録予定品番は spec_json へ。
    code = stock.text(csv_info.get('sourceCode') or csv_info.get('code'))
    registered_code = stock.text(
        csv_info.get('registeredCode')
        or csv_info.get('plannedRegisteredSku')
        or (row.get('product') or {}).get('codeOverride')
        or csv_info.get('code')
        or code
    )
    product_name = stock.text(csv_info.get('productName'))
    size = stock.text(csv_info.get('size'))
    provenance = row.get('specProvenance') if isinstance(row.get('specProvenance'), dict) else {}
    pc = row.get('parenClassification') if isinstance(row.get('parenClassification'), dict) else {}
    # 登録色（統一表記）。変換前色は sourceColor に残し通常画面には出さない
    csv_color = stock.text(
        provenance.get('color') or pc.get('color') or ''
    )
    if csv_color:
        csv_color = paren_classify.normalize_color_value(csv_color) or csv_color
    source_color = stock.text(
        provenance.get('sourceColor')
        or pc.get('sourceColor')
        or ''
    )
    csv_processing = stock.text(
        provenance.get('processing') or pc.get('processing') or ''
    )
    csv_material = stock.text(
        provenance.get('material') or pc.get('material') or ''
    )
    is_main = 1 if provenance.get('isMainCandidate') else 0
    planned_name = stock.text(
        provenance.get('plannedName') or pc.get('plannedName') or product_name
    )
    ps = row.get('processingService') if isinstance(row.get('processingService'), dict) else {}
    spec_json = json.dumps({
        # 照合用対応（表示用ではない）: 変換前名／色と登録先IDは行マップ本体
        'originalName': provenance.get('originalName') or product_name,
        'sourceName': provenance.get('originalName') or product_name,
        'sourceColor': source_color,
        'originalSupplier': provenance.get('originalSupplier') or supplier,
        'color': csv_color,
        'unifiedColor': csv_color,
        'processing': csv_processing or stock.text(provenance.get('processingServiceName') or ps.get('serviceName') or ''),
        'material': csv_material,
        'supplement': stock.text(
            provenance.get('supplement')
            or pc.get('supplement')
            or (row.get('product') or {}).get('officialSupplement')
            or ''
        ),
        'position': stock.text(
            provenance.get('position')
            or pc.get('position')
            or csv_info.get('position')
            or (row.get('product') or {}).get('position')
            or ''
        ),
        'baseCode': stock.text(
            provenance.get('baseCode')
            or csv_info.get('baseCode')
            or ''
        ),
        'registeredCode': stock.text(
            provenance.get('registeredCode')
            or registered_code
            or ''
        ),
        'sourceCode': stock.text(
            provenance.get('sourceCode')
            or csv_info.get('sourceCode')
            or code
            or ''
        ),
        # メーカー補正: 元品番・注記と検索／登録先の対応（初期在庫照合用）
        'searchMakerSku': stock.text(
            provenance.get('searchMakerSku')
            or (row.get('makerCorrection') or {}).get('searchMakerSku')
            or (row.get('makerCorrection') or {}).get('makerSku')
            or ''
        ),
        'usageNote': stock.text(
            provenance.get('usageNote')
            or (row.get('makerCorrection') or {}).get('usageNote')
            or ''
        ),
        'plannedName': planned_name,
        'unifiedName': planned_name,
        'isMainCandidate': bool(is_main),
        'auxStockPickAuto': bool(row.get('auxStockPickAuto')),
        'auxStockPickReason': stock.text(row.get('auxStockPickReason') or ''),
        # 販売用途（取扱種別）。商品名からは除去済みでも原文と対応を残す
        'usageKind': stock.text(
            provenance.get('usageKind')
            or pc.get('usageKind')
            or (row.get('handle') or {}).get('usageKind')
            or ''
        ),
        'usageFromCustomerName': bool(
            provenance.get('usageFromCustomerName')
            or pc.get('usageFromCustomerName')
        ),
        'handlingType': stock.text(
            provenance.get('usageKind')
            or pc.get('usageKind')
            or (row.get('handle') or {}).get('usageKind')
            or ''
        ),
        'scopedColorCorrection': bool(
            pc.get('scopedColorCorrection')
            or (row.get('product') or {}).get('scopedColorCorrection')
        ),
        'colorNotationUnify': bool(source_color and csv_color and source_color != csv_color),
        # 加工サービス分離の内部照合（初期在庫で利用）
        'rowKind': stock.text(provenance.get('rowKind') or '') or (
            'processing_service' if (row.get('product') or {}).get('processingServiceProduct') else ''
        ),
        'processingVendor': stock.text(
            provenance.get('processingVendor') or ps.get('vendorName') or ''
        ),
        'processingServiceName': stock.text(
            provenance.get('processingServiceName') or ps.get('serviceName') or ''
        ),
        'processingDisplayName': stock.text(
            provenance.get('processingServiceName')
            or ps.get('serviceName')
            or provenance.get('processingDisplayName')
            or ''
        ),
        'targetGarmentCode': stock.text(
            provenance.get('targetGarmentCode') or csv_info.get('code') or ''
        ),
        'targetCustomer': stock.text(
            provenance.get('targetCustomer') or customer or ''
        ),
        'csvSize': stock.text(provenance.get('csvSize') or size or ''),
        'customerSpecific': stock.text(provenance.get('customerSpecific') or ''),
        'processingProductId': provenance.get('processingProductId'),
        'rowSpecs': provenance.get('rowSpecs') or ps.get('rowSpecs') or {},
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
    """括弧分類＋明示色振り分け＋ユーザー上書き（行編集はグローバル辞書へ昇格しない）。"""
    base = paren_classify.split_name_and_color(product_name)
    raw = override if isinstance(override, dict) else {}
    user_touched = bool(raw.get('userEdited') or raw.get('manual'))
    planned = stock.text(raw.get('plannedName')) if 'plannedName' in raw else base['plannedName']
    color = stock.text(raw.get('color')) if 'color' in raw else base['color']
    processing = stock.text(raw.get('processing')) if 'processing' in raw else base['processing']
    material = stock.text(raw.get('material')) if 'material' in raw else base['material']
    if 'partsUsage' in raw:
        parts_usage = stock.text(raw.get('partsUsage') or '')
    else:
        parts_usage = stock.text(base.get('partsUsage') or '')
    if 'supplement' in raw:
        supplement = stock.text(raw.get('supplement') or '')
    else:
        supplement = stock.text(base.get('supplement') or '')
    # 上下区分は品番末尾へ移す。補足欄へは残さない（胸当など他補足は維持）
    position = stock.text(raw.get('position') or '')
    if position not in position_sku.POSITION_TOKENS:
        if supplement in position_sku.POSITION_TOKENS:
            position = supplement
            supplement = ''
        else:
            position = ''
    elif supplement in position_sku.POSITION_TOKENS:
        supplement = ''
    # 未分類の手編集はリストで受けてもよい
    if 'unclassified' in raw:
        unc = raw.get('unclassified')
        if isinstance(unc, list):
            unclassified = [stock.text(x) for x in unc if stock.text(x)]
        else:
            unclassified = [stock.text(unc)] if stock.text(unc) else []
    else:
        unclassified = list(base.get('unclassified') or [])
    # 分類できなかった原文の行き先（黙って落とさない）
    unc_move = stock.text(raw.get('unclassifiedMove') or '')
    if unclassified and unc_move == 'to_purpose':
        joined = '・'.join(unclassified)
        parts_usage = f'{parts_usage}・{joined}'.strip('・') if parts_usage else joined
        unclassified = []
    elif unclassified and unc_move == 'to_supplement':
        joined = '・'.join(unclassified)
        supplement = f'{supplement}／{joined}'.strip('／') if supplement else joined
        unclassified = []
    # keep_in_name / 未指定: unclassified を名に残す（後段 attach で planned 再構成）

    if 'usageKind' in raw:
        usage_kind = stock.text(raw.get('usageKind') or '')
    else:
        usage_kind = stock.text(base.get('usageKind') or '')
    if usage_kind != paren_classify.USAGE_KIND_SALES:
        # 予定名にマーカーが残っていれば再検知（手編集で消した場合は usageKind 明示優先）
        if 'usageKind' not in raw:
            _cleaned, detected = paren_classify.strip_sales_usage_markers(planned or base.get('plannedName') or '')
            if detected:
                usage_kind = detected
                if _cleaned and not user_touched:
                    planned = _cleaned
            else:
                usage_kind = ''

    needs = bool(base.get('needsConfirm'))
    reasons = list(base.get('confirmReasons') or [])
    if user_touched:
        # 手動確定後は要確認を外せる（ユーザーが内容を編集した）
        # 未分類の残存だけでは要確認に戻さない（情報通知のみ）
        if raw.get('confirmed') or raw.get('acceptProposal'):
            needs = False
            reasons = []
        elif not raw.get('confirmed') and base.get('needsConfirm'):
            needs = True
    # 曖昧なまま自動確定しない
    auto = bool(base.get('autoClassified')) and not needs and not user_touched
    if user_touched and not needs:
        auto = False  # 手動
    # 販売用途除去だけでも予定名を採用
    if usage_kind == paren_classify.USAGE_KIND_SALES and not needs and not user_touched:
        auto = True

    decision = 'needs_review' if needs else ('auto' if (auto or user_touched) else 'passthrough')
    change_summary = stock.text(base.get('changeSummary') or '')
    if color and not change_summary:
        change_summary = f"商品名から色『{color}』を設定します"
    if usage_kind == paren_classify.USAGE_KIND_SALES and not change_summary:
        change_summary = '販売用途表記を除き、取扱種別「販売」へ引き継ぎます'
    if parts_usage and not change_summary:
        change_summary = f"用途『{parts_usage}』を部品・資材の用途へ"
    info_notices = list(base.get('infoNotices') or [])
    if color and not user_touched:
        color = paren_classify.normalize_color_value(color) or color
    source_color = stock.text(base.get('sourceColor') or '')
    if user_touched and 'sourceColor' in raw:
        source_color = stock.text(raw.get('sourceColor') or '') or source_color
    # 手動登録区分（商品／部品・資材／加工サービス）。入力だけでは自動切替しない
    register_kind = stock.text(raw.get('registerKind') or '')
    if register_kind not in ('商品', '部品・資材', '加工サービス'):
        register_kind = ''
    return {
        'originalName': base['originalName'],
        'plannedName': planned or base['originalName'],
        'baseName': base.get('baseName') or base['originalName'],
        'color': color,
        'colorSource': stock.text(base.get('colorSource') or ''),
        'sourceColor': source_color,
        'processing': processing,
        'material': material,
        'partsUsage': parts_usage,
        'usageKind': usage_kind,
        'supplement': supplement,
        'position': position,
        'registerKind': register_kind,
        'plannedRegisteredSku': stock.text(
            raw.get('plannedRegisteredSku') or raw.get('registeredCode') or ''
        ),
        'unclassified': unclassified,
        'unclassifiedMove': unc_move,
        'needsConfirm': needs,
        'confirmReasons': reasons,
        'infoNotices': info_notices,
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
        'changeSummary': change_summary,
        'hasExplicitColor': bool(color),
        'scopedColorCorrection': bool(base.get('scopedColorCorrection')),
    }


def attach_paren_classification(row, override=None):
    """行へ括弧分類を付け、色・加工・生地の登録予定を反映する。"""
    csv = dict(row.get('csv') or {})
    original = stock.text(csv.get('productName') or '')
    plan = build_paren_classification(original, override)
    raw_override = override if isinstance(override, dict) else {}
    user_touched = bool(raw_override.get('userEdited') or raw_override.get('manual'))
    # LEOC×ユニスタイル×別注×ブルー/サックスエプロン: スコープ付き色名補正
    # 手編集がある行は自動補正しない（メーカー補正の採用は userEdited で再投入される）
    if (
        not user_touched
        and scoped_color.matches_scoped_color_correction(
            customer=csv.get('customer') or '',
            supplier=csv.get('supplier') or '',
            code=csv.get('code') or '',
            product_name=original,
        )
    ):
        plan = scoped_color.apply_scoped_color_correction(plan, original_name=original)
    row['parenClassification'] = plan

    prod = dict(row.get('product') or {})
    handle = dict(row.get('handle') or {})

    # 予定名（括弧分類後）。原文は別名／マッピング用に残す。
    # 別注・品番なしは顧客×原文名で区別するため、自動では予定名へ置き換えない
    # （色・加工・生地フィールドへの抽出は行う）。
    # ただしスコープ付き色名補正が付いた別注は、登録予定名へ反映する（原文は別名維持）。
    code = stock.text(csv.get('code') or '')
    scoped = bool(plan.get('scopedColorCorrection'))
    keep_original_name = (
        (
            legacy.is_custom_order_code(code)
            or not stock.text(code)
            or code == stock_product_merge.UNKNOWN_CODE
        )
        and not scoped
    )
    if plan.get('plannedName'):
        prod['csvOriginalName'] = original
        if plan['plannedName'] != original:
            prod['plannedNameFromParen'] = plan['plannedName']
        if (
            not keep_original_name
            and (plan.get('autoClassified') or (plan.get('userEdited') and not plan.get('needsConfirm')) or scoped)
        ):
            prod['suggestedNameFromParen'] = plan['plannedName']
            # 代表名未設定なら括弧分類後の名を公式名候補に（SKUまとめ後も優先）
            if not prod.get('officialName') or prod.get('officialName') == original or scoped:
                prod['officialName'] = plan['plannedName']
                prod['applyOfficialName'] = True

    if plan.get('color'):
        prod['proposedColor'] = plan['color']
        if not prod.get('officialColor') or scoped:
            prod['officialColor'] = plan['color']
    if plan.get('material'):
        prod['proposedMaterial'] = plan['material']
        prod['officialMaterial'] = plan['material']
    # 部品用途は補足へ載せて登録時に名へ括弧付与（専用カラム無し）
    # 補足が既にあれば用途／補足の順で結合（黙って落とさない）
    parts_usage = stock.text(plan.get('partsUsage') or '')
    supplement = stock.text(plan.get('supplement') or '')
    if parts_usage and parts_usage not in supplement:
        supplement = f'{parts_usage}／{supplement}'.strip('／') if supplement else parts_usage
        plan['supplement'] = supplement
        row['parenClassification'] = plan
    if supplement:
        # 専用カラムが無いため、既存の括弧分類構造（supplement）と
        # 商品側の公式補足として保持。コミット時は名に括弧で載せる。
        prod['officialSupplement'] = supplement
        prod['proposedSupplement'] = supplement
    if parts_usage:
        prod['partsUsage'] = parts_usage

    if scoped:
        prod['plannedRegisteredName'] = plan.get('plannedName') or ''
        prod['plannedRegisteredColor'] = plan.get('color') or ''
        prod['plannedRegisteredSupplement'] = plan.get('supplement') or ''
        prod['scopedColorCorrection'] = True
        prod['scopedColorCorrectionReason'] = scoped_color.SCOPED_REASON
        row['plannedRegisteredName'] = prod['plannedRegisteredName']
        row['plannedRegisteredColor'] = prod['plannedRegisteredColor']
        row['plannedRegisteredSupplement'] = prod['plannedRegisteredSupplement']
        row['changeSummary'] = plan.get('changeSummary') or scoped_color.SCOPED_REASON

    # 販売用途: 商品名マーカー、または顧客名の識別可能マーカー（顧客マスタはリネームしない）
    usage_kind = stock.text(plan.get('usageKind') or '')
    cust_name = stock.text(csv.get('customer') or '')
    cust_has_sales, _cust_markers = paren_classify.detect_sales_usage_marker(cust_name)
    if cust_has_sales:
        usage_kind = paren_classify.USAGE_KIND_SALES
        plan['usageKind'] = usage_kind
        plan['usageFromCustomerName'] = True
        notice = '顧客名の販売用途表記から取扱種別「販売」を引き継ぎます（顧客名は変更しません）'
        notices = list(plan.get('infoNotices') or [])
        if notice not in notices:
            notices.append(notice)
        plan['infoNotices'] = notices
        row['parenClassification'] = plan

    # 顧客別加工 → 取扱の加工仕様（不明時は「加工なし」へ強制しない）
    # 販売用途は取扱キーへ載せる（在庫同一性。レンタルはマーカー無し＝既存／手選択を優先）
    composed = paren_classify.compose_handling_embroidery(
        plan.get('processing') or '',
        usage_kind,
    )
    if plan.get('processing') or usage_kind == paren_classify.USAGE_KIND_SALES:
        handle['proposedEmbroidery'] = composed or plan.get('processing') or ''
        handle['usageKind'] = usage_kind
        if handle.get('action') == 'create' or handle.get('embroidery') in (
            '', None, PENDING_EMBROIDERY,
        ):
            if composed:
                handle['embroidery'] = composed
                handle['embroideryLabel'] = composed
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
    # メーカー品番補正ボタン用メタ（要確認行の操作欄で参照）
    row['makerCorrection'] = maker_sku_lookup.row_maker_correction_meta(row)
    return row


def _apply_paren_names_after_sku_plans(classified, sku_groups):
    """括弧分類で得た予定名を、SKU代表名より優先して商品計画へ反映する。

    ただしユーザー指定の公式名・既存維持は上書きしない。
    別注・品番なしは原文名を維持（顧客×原文名の区別を壊さない）。
    スコープ付き色名補正が付いた別注は登録予定名へ反映する。
    色別に分けて登録の行は色別予定の商品名を優先し、括弧除去名で上書きしない。
    加工サービス行は加工名同期を apply_processing_service_split に任せ、ここでは触らない
    （古い表示名やメーカー採用名が加工名・本体名へ混入しないようにする）。
    """
    by_code = {g['code']: g for g in sku_groups}
    for row in classified:
        pc = row.get('parenClassification') or {}
        if (
            row.get('processingServiceView')
            or pc.get('processingServiceView')
            or (row.get('processingService') or {}).get('isProcessingService')
            or (row.get('product') or {}).get('processingServiceProduct')
            or (row.get('product') or {}).get('processingServiceSplit')
        ):
            continue
        scoped = bool(pc.get('scopedColorCorrection'))
        if not (
            pc.get('autoClassified')
            or scoped
            or (pc.get('userEdited') and not pc.get('needsConfirm'))
        ):
            continue
        planned = stock.text(pc.get('plannedName') or '')
        if not planned:
            continue
        code = stock.text((row.get('csv') or {}).get('code') or '')
        group = by_code.get(code) or {}
        prod = dict(row.get('product') or {})
        # 色別／内容別登録の商品名を括弧分類の除去後名で潰さない
        if (
            prod.get('colorSplit')
            or prod.get('contentSplit')
            or group.get('colorSplit')
            or group.get('contentSplit')
            or stock.text((group.get('decision') or {}).get('decision') or '') in (
                SKU_DECISION_COLOR_SPLIT,
                SKU_DECISION_CONTENT_SPLIT,
            )
        ):
            continue
        keep_original_name = (
            (
                legacy.is_custom_order_code(code)
                or not stock.text(code)
                or code == stock_product_merge.UNKNOWN_CODE
            )
            and not scoped
        )
        original = stock.text((row.get('csv') or {}).get('productName') or '')
        if not keep_original_name:
            # SKUまとめの officialName が原文のままなら置き換え
            cur = stock.text(prod.get('officialName') or '')
            if not cur or cur == original or scoped:
                prod['officialName'] = planned
                prod['applyOfficialName'] = True
                prod['suggestedNameFromParen'] = planned
                prod['csvOriginalName'] = original
        if pc.get('color') and (not prod.get('officialColor') or scoped):
            prod['officialColor'] = pc['color']
        if pc.get('material'):
            prod['officialMaterial'] = pc['material']
            prod['proposedMaterial'] = pc['material']
        if pc.get('supplement'):
            prod['officialSupplement'] = pc['supplement']
            prod['proposedSupplement'] = pc['supplement']
        if scoped:
            prod['scopedColorCorrection'] = True
            prod['plannedRegisteredName'] = planned
            prod['plannedRegisteredColor'] = stock.text(pc.get('color') or '')
            prod['plannedRegisteredSupplement'] = stock.text(pc.get('supplement') or '')
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
    code_raw = stock.text(item.get('商品コード'))
    product_name = stock.text(item.get('商品名'))
    size_raw = item.get('サイズ')
    size = size_raw if isinstance(size_raw, str) else ('' if size_raw is None else str(size_raw))
    size_label = size.strip()
    raw_override = paren_override if isinstance(paren_override, dict) else {}
    maker_pos = ''
    if stock.text(raw_override.get('position') or '') in position_sku.POSITION_TOKENS:
        maker_pos = stock.text(raw_override.get('position'))
    elif stock.text(raw_override.get('supplement') or '') in position_sku.POSITION_TOKENS:
        # 旧UI互換: 補足に残った上下は品番側へ移す
        maker_pos = stock.text(raw_override.get('supplement'))
    pos_info = position_sku.resolve_garment_position(
        code=code_raw,
        product_name=product_name,
        maker_position=maker_pos,
        override_position=stock.text(raw_override.get('position') or ''),
        override_registered_code=stock.text(
            raw_override.get('plannedRegisteredSku')
            or raw_override.get('registeredCode')
            or ''
        ),
    )
    code = stock.text(pos_info.get('registeredCode') or code_raw)
    source_code = stock.text(pos_info.get('sourceCode') or code_raw)
    base_code = stock.text(pos_info.get('baseCode') or code)
    position = stock.text(pos_info.get('position') or '')
    group_key = match_group_key(
        customer=customer_name, code=code, product_name=product_name, supplier=supplier_name,
    )
    reasons = []

    if not customer_name and not product_name and not code_raw:
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

    # マップは原文品番＋商品名で探す（上下を名で区別した行を取り違えない）
    mapped = lookup_row_map(
        c, customer=customer_name, code=source_code, product_name=product_name,
        size=size_label, supplier=supplier_name,
    )
    if not mapped and source_code != code:
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
        pm = lookup_product_map(c, customer=customer_name, code=source_code, product_name=product_name)
        if not pm and source_code != code:
            pm = lookup_product_map(c, customer=customer_name, code=code, product_name=product_name)
        if pm:
            mapped_product_id = pm['product_id']

    # 既存商品照合は登録予定品番（VEH30B下）で行う
    item_for_product = dict(item) if isinstance(item, dict) else {'商品コード': code}
    item_for_product['商品コード'] = code
    product = _product_preview(
        c, item_for_product, supp, row_no,
        customer_name=customer_name,
        customer_id=cust.get('id'),
        selected_category=selected_category,
        mapped_product_id=mapped_product_id,
        selected_product_id=selected_product_id,
        force_create=force_create_product,
    )
    if code and code != source_code:
        product['code'] = code
        product['codeOverride'] = code
        product['sourceCode'] = source_code
        product['baseCode'] = base_code
        product['position'] = position
        product['codeDisplay'] = stock.product_code_display(code, product.get('category') or '')
    if pos_info.get('conflict') or pos_info.get('needsConfirm'):
        product['action'] = 'review'
        product.setdefault('reasons', []).append(
            f'{row_no}行目：上下区分（上／下）の判断材料が矛盾しています。'
            '推測では付与せず、確認してください。'
        )
        reasons.append(product['reasons'][-1])

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
                    'sourceProductCode': source_code, 'supplier': supplier_name,
                    'customer': customer_name,
                    'usedMarker': used_marker_possible(product_name),
                },
            )
    elif product.get('id') and cust.get('id'):
        handle = _handle_preview(
            c, cust.get('id'), product.get('id'), row_no,
            csv_source={
                'sourceRowNo': row_no, 'productCode': code, 'productName': product_name,
                'sourceProductCode': source_code, 'supplier': supplier_name,
                'customer': customer_name,
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
            'sourceCode': source_code,
            'baseCode': base_code,
            'position': position,
            'registeredCode': code,
            'plannedRegisteredSku': code,
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
    # メーカー採用で上下が補足に残っていれば品番側へ移し補足から除く
    override_for_paren = dict(raw_override) if raw_override else None
    if override_for_paren is not None and position:
        override_for_paren.setdefault('position', position)
        override_for_paren.setdefault('plannedRegisteredSku', code)
        if stock.text(override_for_paren.get('supplement') or '') in position_sku.POSITION_TOKENS:
            override_for_paren['supplement'] = ''
    return attach_paren_classification(row, override_for_paren)


def _row_variant(row):
    csv = row.get('csv') or {}
    name = csv.get('productName') or ''
    _bk, color_key, base_display, color_display = legacy.product_name_base_and_color(name)
    proc_key = _name_processing_key(name)
    processing = ''
    try:
        split = paren_classify.split_name_and_color(name)
        processing = stock.text(split.get('processing') or '')
        if proc_key == '':
            processing = ''
    except Exception:
        processing = ''
    return {
        'key': sku_variant_key(
            product_name=name,
            color=color_display,
            supplier=csv.get('supplier') or '',
            processing=processing,
        ),
        'productName': name,
        'productNameCore': prefer_unmarked_product_name([name]) or name,
        'productNameBase': base_display or name,
        'supplier': csv.get('supplier') or '',
        'color': color_display or '',
        'colorKey': color_key or '',
        'processing': processing or '',
        'rowNos': [],
        'sizes': [],
        'customers': [],
        'csvNames': [],
        'sourceRows': [],
    }


def _display_base_without_substring_synonym(name):
    """表示用本体名。色は除去するが、パンツ→スラックス等の部分置換はしない。"""
    raw = stock.text(name)
    if not raw:
        return ''
    try:
        split = paren_classify.split_name_and_color(raw)
        base = stock.text(split.get('plannedName') or '') or raw
        base = paren_classify.strip_pants_hem_processing(base) or base
        base = paren_classify.unify_import_notation(base) or base
    except Exception:
        base = raw
    # 完全一致シノニム（帽子／キャップ）だけ表示に反映。部分置換は照合キー側のみ。
    return legacy.preferred_name_synonym(base) or base


def prefer_synonym_display_name(names):
    """同一グループ内の表示名。帽子／キャップが共存すればキャップを優先。

    帽子だけのグループは帽子のまま（無条件改名しない）。
    パンツ／スラックスの部分置換は表示名へは適用しない（照合キーのみ）。
    """
    cleaned = [stock.text(n) for n in (names or []) if stock.text(n)]
    if not cleaned:
        return ''
    raw_bases = [_display_base_without_substring_synonym(n) for n in cleaned]
    has_hat_raw = any(b == '帽子' for b in raw_bases)
    has_cap_raw = any(b == 'キャップ' for b in raw_bases)
    if has_cap_raw and has_hat_raw:
        return 'キャップ'
    unmarked = prefer_unmarked_product_name(cleaned) or cleaned[0]
    base_disp = _display_base_without_substring_synonym(unmarked)
    if has_cap_raw and not has_hat_raw:
        return base_disp or 'キャップ'
    if has_hat_raw and not has_cap_raw:
        return '帽子'
    return base_disp or unmarked


def merge_color_omission_variants(variants):
    """同じ仕入先・同じ本体名で明示色が1種類だけのとき、色なし候補をその色へまとめる。

    明示色が複数／メーカー色番・加工などの矛盾は呼び出し側の差分検出に残す。
    戻り値: (merged_variants, color_omission_auto)
    """
    variants = list(variants or [])
    if len(variants) < 2:
        return variants, False

    def _slot_base(v):
        return identity_match_key(
            v.get('productNameBase')
            or legacy.product_name_base_and_color(v.get('productName') or '')[2]
            or v.get('productName')
            or ''
        )

    def _slot_supplier(v):
        return identity_match_key(v.get('supplier') or '')

    def _slot_processing(v):
        return identity_match_key(v.get('processing') or '')

    by_group = defaultdict(list)
    for v in variants:
        # 加工差がある候補は色省略でまとめない
        by_group[(_slot_supplier(v), _slot_base(v), _slot_processing(v))].append(v)

    merged = []
    color_omission_auto = False
    for (_sup, _base, _proc), slots in by_group.items():
        if len(slots) < 2:
            merged.extend(slots)
            continue
        color_keys = [(stock.text(s.get('colorKey') or ''), s) for s in slots]
        nonempty = [ck for ck, _s in color_keys if ck]
        unique_colors = set(nonempty)
        if len(unique_colors) != 1:
            # 色なし同士／明示色が複数 → まとめない
            merged.extend(slots)
            continue
        only_key = next(iter(unique_colors))
        target = next(s for ck, s in color_keys if ck == only_key)
        colorless = [s for ck, s in color_keys if not ck]
        if not colorless:
            merged.extend(slots)
            continue
        # 色なしを明示色スロットへ吸収
        color_omission_auto = True
        for s in colorless:
            for rn in s.get('rowNos') or []:
                if rn not in target['rowNos']:
                    target['rowNos'].append(rn)
            for n in s.get('csvNames') or []:
                if n and n not in target['csvNames']:
                    target['csvNames'].append(n)
            for sr in s.get('sourceRows') or []:
                target.setdefault('sourceRows', []).append(sr)
            for size in s.get('sizes') or []:
                if size and size not in target['sizes']:
                    target['sizes'].append(size)
            for cust in s.get('customers') or []:
                if cust and cust not in target['customers']:
                    target['customers'].append(cust)
        # 代表名はシノニム優先の本体（色は color 欄）
        target['productName'] = prefer_synonym_display_name(
            target.get('csvNames') or [target.get('productName')]
        ) or target.get('productNameBase') or target.get('productName')
        target['productNameBase'] = target.get('productNameBase') or target['productName']
        target['key'] = sku_variant_key(
            product_name=target.get('productName') or '',
            color=target.get('color') or '',
            supplier=target.get('supplier') or '',
        )
        merged.append(target)
        # 同色の他スロット（理論上1つ）も残す必要なし
        for ck, s in color_keys:
            if ck == only_key and s is not target:
                merged.append(s)
    return merged, color_omission_auto


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
    official = prefer_synonym_display_name(all_csv_names) or prefer_unmarked_product_name(all_csv_names) or (primary.get('productName') or '')
    # 既存1件で標記差だけのときは、標記なし公式名を優先（既存が標記付きなら書き換え候補）
    if group.get('existingCount') == 1 and not group.get('needsDecision'):
        existing_name = (group.get('existingProducts') or [{}])[0].get('name') or ''
        official = prefer_synonym_display_name([*all_csv_names, existing_name]) or prefer_unmarked_product_name([*all_csv_names, existing_name]) or official
    else:
        # 新規まとめではパンツ一般『丈加工』を代表名に残さない／色は color 欄へ
        # （部分シノニムのパンツ→スラックスは表示名へ適用しない）
        if official and not group.get('needsDecision'):
            official = _display_base_without_substring_synonym(official) or official
        else:
            stripped = paren_classify.strip_pants_hem_processing(official)
            if stripped:
                official = stripped
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
        'pantsHemAutoMerged': bool(group.get('pantsHemAutoMerged')),
        'colorOmissionAutoMerged': bool(group.get('colorOmissionAutoMerged')),
        'colorNotationAutoMerged': bool(group.get('colorNotationAutoMerged')),
        'plannedRegisteredColor': stock.text(group.get('plannedRegisteredColor') or ''),
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
            slot.setdefault('sourceRows', []).append({
                'rowNo': row['rowNo'],
                'productName': csv_name,
                'supplier': (row.get('csv') or {}).get('supplier') or '',
            })
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
            # 代表表示はシノニム優先の本体名（色は color 欄）
            disp = prefer_synonym_display_name(v.get('csvNames') or [v.get('productName')])
            if disp:
                v['productName'] = disp
                v['productNameBase'] = disp
            else:
                v['productName'] = prefer_unmarked_product_name(
                    v.get('csvNames') or [v.get('productName')]
                ) or v.get('productName') or ''
        # 候補作成・在庫比較より前: 色省略（明示色が1種類）をまとめる
        variants, color_omission_auto = merge_color_omission_variants(variants)
        existing = stock_product_merge.find_live_products_by_code(c, code)
        existing_cards = [_product_candidate(m, c) for m in existing]
        existing_bases = {legacy.product_name_base_key(m['name']) for m in existing}
        csv_bases = {
            legacy.product_name_base_key(n)
            for v in variants
            for n in (v.get('csvNames') or [v.get('productName') or ''])
            if n
        }
        # 本体名が既存と一致しないときだけ名差（色の名内／名外や標記だけの差は一致扱い）
        name_mismatch = bool(existing) and bool(csv_bases - existing_bases)
        # 別名ヒットのみでも公式名差として扱う（中古標記だけの差は除外）
        for row in rows:
            if row.get('product', {}).get('nameDiff'):
                name_mismatch = True
                break

        used_marker_auto = False
        synonym_auto = False
        whitespace_auto = False
        color_notation_auto = False
        pants_hem_auto = False
        all_csv_names = []
        for v in variants:
            for n in v.get('csvNames') or []:
                if n and n not in all_csv_names:
                    all_csv_names.append(n)
        cores = {product_name_core_key(n) for n in all_csv_names if n}
        base_keys = {legacy.product_name_base_key(n) for n in all_csv_names if n}
        raw_names = {stock.text(n) for n in all_csv_names if stock.text(n)}
        if len(cores) == 1 and any(used_marker_possible(n) for n in all_csv_names):
            used_marker_auto = len(raw_names) > 1 or any(
                used_marker_possible(n) for n in all_csv_names
            )
        if (len(cores) == 1 or (len(base_keys) == 1 and color_omission_auto)) and len(raw_names) > 1:
            # 標記以外の表記ゆれ（空白・丈加工・シノニム・色表記・色省略）を区別
            import unicodedata
            cores_nfkc = set()
            cores_after_hem = set()
            cores_after_syn = set()
            cores_after_color_split = set()
            for n in all_csv_names:
                if not stock.text(n):
                    continue
                core, _ = legacy.strip_used_marker(n)
                core = unicodedata.normalize('NFKC', stock.text(core))
                cores_nfkc.add(identity_match_key(core))
                hemmed = paren_classify.strip_pants_hem_processing(core)
                cores_after_hem.add(identity_match_key(hemmed))
                cores_after_syn.add(
                    identity_match_key(legacy.apply_name_synonyms(hemmed))
                )
                cores_after_color_split.add(product_name_core_key(n))
            if len(cores_nfkc) == 1:
                unmarked_set = {
                    stock.text(legacy.strip_used_marker(n)[0]) for n in all_csv_names if stock.text(n)
                }
                if len(unmarked_set) > 1:
                    whitespace_auto = True
            elif len(cores_after_hem) == 1:
                pants_hem_auto = True
            elif len(cores_after_syn) == 1 and not color_omission_auto:
                synonym_auto = True
            elif color_omission_auto or len(cores_after_color_split) == 1:
                # 括弧内外の色・半角カナ・色省略・名シノニムの組み合わせ
                color_notation_auto = True
                # 色除去後・シノニム適用前の本体に帽子とキャップが混在していれば同義
                raw_bases = set()
                for n in all_csv_names:
                    if not stock.text(n):
                        continue
                    try:
                        split = paren_classify.split_name_and_color(n)
                        raw_bases.add(stock.text(split.get('plannedName') or '') or '')
                    except Exception:
                        raw_bases.add(stock.text(n))
                if '帽子' in raw_bases and 'キャップ' in raw_bases:
                    synonym_auto = True
            else:
                color_notation_auto = True

        # サイズ・顧客・中古標記・空白/全角半角・明示シノニム・色表記・色省略・丈加工だけの差はバリアントに出ない
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
            if pants_hem_auto:
                auto_bits.append('パンツの一般「丈加工」')
            if synonym_auto:
                auto_bits.append('明示シノニム（帽子／キャップ・パンツ／スラックス等）')
            if color_omission_auto:
                auto_bits.append('色省略（明示色が1種類のため補完）')
            if color_notation_auto:
                auto_bits.append('色表記の統一（括弧・半角カナ・配色区切り・色辞書）')
            if whitespace_auto:
                auto_bits.append('空白・全角半角の表記差')
            # 登録予定の統一名／色
            planned_name = prefer_synonym_display_name(all_csv_names) or (
                variants[0].get('productName') if variants else ''
            )
            planned_color = ''
            for v in variants:
                if stock.text(v.get('color') or ''):
                    planned_color = stock.text(v.get('color') or '')
                    break
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
                'genderConflictPossible': False,
                'fabricDiffPossible': False,
                'bulkApprovable': False,
                'plannedRegisteredName': planned_name,
                'plannedRegisteredColor': planned_color,
            }

        reason_labels = [r.get('label') or '' for r in (diff_meta.get('reviewReasons') or []) if r.get('label')]
        apply_scope = (
            f'品番「{code}」の対象行 {len(rows)} 件'
            f'（候補名 {len(diff_meta.get("nameCandidates") or [])} / '
            f'仕入先候補 {len(diff_meta.get("supplierCandidates") or [])} / '
            f'登録済 {len(existing)} 件）。'
            f'サイズ・顧客・先頭「＊/*」・空白/全角半角・明示シノニム・'
            f'括弧色／色省略だけの差は同じ商品へまとめます。'
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
            'pantsHemAutoMerged': bool(pants_hem_auto and planned_single),
            'colorOmissionAutoMerged': bool(color_omission_auto and planned_single),
            'colorNotationAutoMerged': bool(color_notation_auto and planned_single),
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


def apply_manual_register_kind(classified, row_classifications=None):
    """手動の登録区分を優先（加工／素材の入力だけでは区分を切り替えない）。

    registerKind: 商品 / 部品・資材 / 加工サービス
    区分変更でも入力値は消さない（未使用欄は保持し、画面で案内）。
    """
    overrides = row_classifications if isinstance(row_classifications, dict) else {}
    for row in classified or []:
        pc = dict(row.get('parenClassification') or {})
        ov = (
            overrides.get(str(row.get('rowNo')))
            or overrides.get(row.get('rowNo'))
            or {}
        )
        kind = stock.text(
            (ov.get('registerKind') if isinstance(ov, dict) else '')
            or pc.get('registerKind')
            or ''
        )
        auto_kind = '商品'
        ps = row.get('processingService') if isinstance(row.get('processingService'), dict) else {}
        if row.get('physicalPartsView') or pc.get('physicalPartsView') or ps.get('isPhysicalParts'):
            auto_kind = '部品・資材'
        elif (
            row.get('processingServiceView')
            or pc.get('processingServiceView')
            or (ps.get('isProcessingVendor') and not ps.get('isPhysicalParts'))
        ):
            auto_kind = '加工サービス'
        pc['autoRegisterKind'] = auto_kind
        if kind not in ('商品', '部品・資材', '加工サービス'):
            kind = auto_kind
            pc['registerKindManual'] = False
        else:
            pc['registerKindManual'] = bool(
                isinstance(ov, dict) and stock.text(ov.get('registerKind') or '')
            )
        pc['registerKind'] = kind
        # 手動が勝つ。入力欄の値は消さない
        if kind == '部品・資材':
            row['physicalPartsView'] = True
            row['processingServiceView'] = False
            pc['physicalPartsView'] = True
            pc['processingServiceView'] = False
            prod = dict(row.get('product') or {})
            if prod.get('blankSku') or not stock.text((row.get('csv') or {}).get('code') or ''):
                prod['category'] = stock.CATEGORY_PARTS
                prod['proposedCategory'] = stock.CATEGORY_PARTS
                row['product'] = prod
        elif kind == '加工サービス':
            row['physicalPartsView'] = False
            row['processingServiceView'] = True
            pc['physicalPartsView'] = False
            pc['processingServiceView'] = True
            prod = dict(row.get('product') or {})
            prod['category'] = stock.CATEGORY_PROCESS_SERVICE
            prod['proposedCategory'] = stock.CATEGORY_PROCESS_SERVICE
            # 加工名が空で予定名がある場合でも、無条件にはコピーしない（既存方針）
            row['product'] = prod
        else:
            # 商品: 加工サービス／部品ビューを外す（フィールド値は保持）
            row['physicalPartsView'] = False
            row['processingServiceView'] = False
            pc['physicalPartsView'] = False
            pc['processingServiceView'] = False
            prod = dict(row.get('product') or {})
            # 品番なしで商品を選んだ場合は空カテゴリ（部品へ強制しない）
            if prod.get('blankSku') and prod.get('category') in (
                stock.CATEGORY_PARTS, stock.CATEGORY_PROCESS_SERVICE,
            ):
                if pc.get('registerKindManual'):
                    prod['category'] = ''
                    prod['proposedCategory'] = ''
                    row['product'] = prod
        row['parenClassification'] = pc


def attach_processing_service_detection(classified):
    """各行へ加工サービス判定を付与（DBは変えない）。

    加工名が唯一の入力元。表示名（plannedName）から無条件に加工名を補完しない。
    ユーザーが加工名欄を明示したときだけ、その値を優先して再判定する。
    利用者が明示復帰した行（userRestoredFromAutoExclude）は再照合で勝手に自動除外しない。
    """
    for row in classified or []:
        if row.get('status') == STATUS_EXCLUDED:
            continue
        csv = row.get('csv') or {}
        pc = row.get('parenClassification') if isinstance(row.get('parenClassification'), dict) else {}
        csv_name = stock.text(csv.get('productName') or '')
        user_proc = stock.text(pc.get('processing') or '')
        user_edited = bool(pc.get('userEdited') or pc.get('manual'))
        user_restored = bool(pc.get('userRestoredFromAutoExclude'))
        # 手入力の加工名を優先（表示名＝plannedName は使わない）
        if (user_edited or user_restored) and user_proc:
            info = proc_svc.classify_processing_service_row(
                supplier=csv.get('supplier') or '',
                product_name=user_proc,
                code=csv.get('code') or '',
                paren_processing='',
            )
            if not info.get('identifiable'):
                info = proc_svc.classify_processing_service_row(
                    supplier=csv.get('supplier') or '',
                    product_name=csv_name,
                    code=csv.get('code') or '',
                    paren_processing=user_proc,
                )
            # 明示復帰＋加工名ありなら、自動除外フラグを立て直さない
            if info.get('identifiable'):
                info['k2AutoExclude'] = False
                info['autoExcludeReason'] = ''
                info['userRestoredFromAutoExclude'] = True
        else:
            info = proc_svc.classify_processing_service_row(
                supplier=csv.get('supplier') or '',
                product_name=csv_name,
                code=csv.get('code') or '',
                paren_processing=user_proc,
            )
            # 加工名なしで復帰フラグだけ残っている場合は自動除外を抑止し確認へ
            if user_restored and info.get('k2AutoExclude'):
                info['k2AutoExclude'] = False
                info['needsConfirm'] = True
                info['autoExcludeReason'] = ''
                info['userRestoredFromAutoExclude'] = True
        # 特定できた加工名は表示名へ常に同期（古い別表示名を残さない）
        if info.get('identifiable') and info.get('serviceName'):
            info['displayName'] = info['serviceName']
        row['processingService'] = info
        if info.get('k2AutoExclude') and not info.get('identifiable'):
            # 確認待ちに残さず、後段の自動除外適用へ（ここではフラグのみ）
            row['processingServiceNeedsConfirm'] = False
            continue
        if info.get('needsConfirm') and not info.get('identifiable'):
            row['reasons'] = list(row.get('reasons') or []) + list(info.get('confirmReasons') or [])
            if row.get('status') not in (STATUS_EXCLUDED, STATUS_HOLD):
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                row['processingServiceNeedsConfirm'] = True


def _processing_service_plan_payload(info, *, source_row_nos=None, garment_codes=None):
    name = stock.text(info.get('serviceName') or '')
    vendor = stock.text(info.get('vendorName') or '')
    # 表示名は加工名からのみ（別項目・旧 displayName を優先しない）
    return {
        'serviceName': name,
        'displayName': name,
        'vendorName': vendor,
        'dedupeKey': proc_svc.service_dedupe_key(name, vendor),
        'category': stock.CATEGORY_PROCESS_SERVICE,
        'blankSku': True,
        'code': '',
        'internalKind': 'processing_service',
        'displayLabel': f'{name}／{vendor}' if name and vendor else name or vendor,
        'sourceRowNos': list(source_row_nos or []),
        'targetGarmentCodes': sorted({stock.text(x) for x in (garment_codes or []) if stock.text(x)}),
        'autoRegister': bool(info.get('identifiable') and name and vendor),
        'rowSpecsSamples': [],
    }


def apply_processing_service_split(c, classified, sku_groups):
    """加工業者行を本体SKUから切り離し、加工サービス計画を組み立てる。

    - 特定できた加工は自動登録対象（衣服種差・在庫0では個別確認に出さない）
    - 本体が特定できるときは再利用し、加工側に本体品番を流用しない
    - 本体不明のときは本体登録だけ確認対象。特定できた加工は確認待ちにしない
    """
    service_plans = {}
    by_code = {g['code']: g for g in (sku_groups or [])}

    # グループ単位: 加工業者を仕入先候補・差分から外し、本体名を推定
    for group in sku_groups or []:
        code = stock.text(group.get('code') or '')
        rows = [
            r for r in (classified or [])
            if stock.text((r.get('csv') or {}).get('code') or '') == code
        ]
        proc_rows = [r for r in rows if (r.get('processingService') or {}).get('isProcessingVendor')]
        garment_rows = [
            r for r in rows
            if not (r.get('processingService') or {}).get('isProcessingVendor')
        ]
        if not proc_rows:
            # 仕入先候補だけでも加工業者を落とす
            group['supplierCandidates'] = proc_svc.filter_garment_supplier_names(
                group.get('supplierCandidates') or []
            )
            continue

        body_names = []
        for r in garment_rows:
            n = stock.text((r.get('csv') or {}).get('productName') or '')
            if n and n not in body_names:
                body_names.append(n)
        for r in proc_rows:
            n = stock.text((r.get('csv') or {}).get('productName') or '')
            if n and n not in body_names:
                body_names.append(n)

        service_names = [
            stock.text((r.get('processingService') or {}).get('serviceName') or '')
            for r in proc_rows
            if (r.get('processingService') or {}).get('identifiable')
        ]
        body_name = proc_svc.garment_body_name_from_variants(
            body_names, service_name=service_names[0] if service_names else '',
        )
        # 既存商品があれば本体再利用
        existing = group.get('existingProducts') or []
        body_known = bool(garment_rows) or bool(existing) or bool(body_name and _KIND_HINT_RE.search(body_name))
        if existing and not body_name:
            body_name = stock.text((existing[0] or {}).get('name') or '')

        group['processingVendorRows'] = [r['rowNo'] for r in proc_rows]
        group['garmentBodyKnown'] = bool(body_known and (garment_rows or existing or body_name))
        group['plannedGarmentName'] = body_name
        group['supplierCandidates'] = proc_svc.filter_garment_supplier_names(
            group.get('supplierCandidates') or []
        )
        # 加工業者バリアントを本体比較から外す（名差・仕入先差・加工／種の疑い）
        garment_variants = []
        for v in group.get('variants') or []:
            if proc_svc.is_processing_vendor(v.get('supplier') or ''):
                continue
            garment_variants.append(v)
        garment_names = []
        for v in garment_variants:
            for n in v.get('csvNames') or ([v.get('productName')] if v.get('productName') else []):
                n = stock.text(n)
                if n and n not in garment_names:
                    garment_names.append(n)
        if body_name and body_name not in garment_names:
            garment_names = [body_name] + garment_names
        if garment_names:
            group['nameCandidates'] = garment_names
        group['processingDiffPossible'] = False

        if group.get('needsDecision') and group.get('garmentBodyKnown'):
            soft_reasons = []
            for r in list(group.get('reviewReasons') or []):
                code_r = stock.text(r.get('code') or '')
                if code_r in ('processing', 'kind', 'fabric', 'color'):
                    # 加工行由来の疑い。本体側に実差が残るかは garment_variants で見る
                    continue
                if code_r == 'supplier':
                    if len(group.get('supplierCandidates') or []) <= 1:
                        continue
                if code_r in ('name_wording', 'name'):
                    # 加工業者行の元名称との差だけなら無視
                    cores = {product_name_core_key(n) for n in garment_names if n}
                    if len(cores) <= 1:
                        continue
                soft_reasons.append(r)
            group['reviewReasons'] = soft_reasons
            group['nameWordingDiff'] = len({product_name_core_key(n) for n in garment_names if n}) > 1
            if len(garment_variants) <= 1 and len(existing) <= 1 and not soft_reasons:
                group['needsDecision'] = False
                group['plannedSingleProduct'] = True
                group['status'] = 'auto'
                group['reviewTier'] = REVIEW_TIER_AUTO
                group['reviewTierLabel'] = REVIEW_TIER_LABELS[REVIEW_TIER_AUTO]
                group['reviewReasons'] = [{
                    'code': 'auto',
                    'label': '加工サービス行を分離（本体は自動まとめ）',
                    'values': [body_name] if body_name else list(garment_names),
                }]
                group['processingServiceAutoSplit'] = True
                if body_name:
                    plan = dict(group.get('productPlan') or {})
                    plan['productName'] = body_name
                    group['productPlan'] = plan
                else:
                    group['productPlan'] = _sku_product_plan(group)
                # 行の skuDecisionNeeded を後で落とすためフラグ
                group['skuDecisionNeeded'] = False

        # エルムス刺繍のみ（本体行なし）: 本体確認待ちにしない。加工サービス1件へ集約
        elms_only = bool(proc_rows) and not garment_rows and all(
            (r.get('processingService') or {}).get('elmsEmbroideryRule')
            for r in proc_rows
        )
        if elms_only:
            group['garmentBodyKnown'] = False
            group['garmentBodyReviewOnly'] = False
            group['elmsEmbroideryOnly'] = True
            group['needsDecision'] = False
            group['plannedSingleProduct'] = True
            group['status'] = 'auto'
            group['reviewTier'] = REVIEW_TIER_AUTO
            group['reviewTierLabel'] = REVIEW_TIER_LABELS[REVIEW_TIER_AUTO]
            group['reviewReasons'] = [{
                'code': 'elms_embroidery',
                'label': 'エルムスコーポレーション行を加工サービス「刺繍」として自動整理',
                'values': [code] if code else ['（品番なし）'],
            }]
            group['processingServiceAutoSplit'] = True
            group['skuDecisionNeeded'] = False
            group['productPlan'] = {
                **dict(group.get('productPlan') or {}),
                'skipGarmentBody': True,
                'processingServiceOnly': True,
            }
        # 本体不明: 本体側だけ確認。加工サービスは別途自動
        elif not group.get('garmentBodyKnown'):
            group['needsDecision'] = True
            group['plannedSingleProduct'] = False
            group['status'] = 'needs_decision'
            group['reviewTier'] = REVIEW_TIER_INDIVIDUAL
            group['reviewTierLabel'] = REVIEW_TIER_LABELS[REVIEW_TIER_INDIVIDUAL]
            reasons = list(group.get('reviewReasons') or [])
            if not any(stock.text(r.get('code')) == 'garment_body_unknown' for r in reasons):
                reasons.insert(0, {
                    'code': 'garment_body_unknown',
                    'label': '加工行のみで商品本体情報を特定できません（本体登録の確認が必要）',
                    'values': [code],
                })
            group['reviewReasons'] = reasons
            group['garmentBodyReviewOnly'] = True

        # 予定の通常仕入先から加工業者を除外
        primary = stock.text(group.get('plannedPrimarySupplier') or '')
        if primary and proc_svc.is_processing_vendor(primary):
            group['plannedPrimarySupplier'] = (
                (group.get('supplierCandidates') or [None])[0] or ''
            )
            group['plannedPrimarySupplierReason'] = '加工業者を本体の通常仕入先から除外'
        plan = dict(group.get('productPlan') or {})
        if body_name:
            plan['productName'] = body_name
        if group.get('plannedPrimarySupplier'):
            plan['supplier'] = group['plannedPrimarySupplier']
        # 加工業者を本体の primary / sub にしない
        plan['excludeProcessingVendors'] = True
        group['productPlan'] = plan

    # 行単位: 加工サービス計画・本体への取扱加工・行マップ用 provenance
    for row in classified or []:
        info = row.get('processingService') if isinstance(row.get('processingService'), dict) else {}
        if not info:
            continue
        csv = dict(row.get('csv') or {})
        code = stock.text(csv.get('code') or '')
        group = by_code.get(code)

        if info.get('identifiable') and info.get('isProcessingService'):
            key = proc_svc.service_dedupe_key(info['serviceName'], info['vendorName'])
            plan = service_plans.get(key)
            if not plan:
                plan = _processing_service_plan_payload(
                    info,
                    source_row_nos=[row['rowNo']],
                    garment_codes=[code] if code else [],
                )
                existing_svc = proc_svc.find_existing_processing_service(
                    c, info['serviceName'], info['vendorName'],
                )
                dup_info = proc_svc.describe_processing_service_duplicates(
                    c, info['serviceName'], info['vendorName'],
                )
                if dup_info.get('hasDuplicates'):
                    plan['duplicateServices'] = dup_info
                if existing_svc:
                    plan['action'] = 'reuse'
                    plan['productId'] = existing_svc['id']
                    plan['autoRegister'] = True
                    # 表示は正規名。既存が「刺繍加工」等でも計画上は「刺繍」
                    if info.get('elmsEmbroideryRule'):
                        plan['reuseName'] = stock.text(existing_svc.get('name') or '')
                else:
                    plan['action'] = 'create'
                if info.get('elmsEmbroideryRule'):
                    plan['elmsEmbroideryRule'] = True
                service_plans[key] = plan
            else:
                if row['rowNo'] not in plan['sourceRowNos']:
                    plan['sourceRowNos'].append(row['rowNo'])
                if code and code not in plan['targetGarmentCodes']:
                    plan['targetGarmentCodes'].append(code)
            specs = info.get('rowSpecs') or {}
            if specs and len(plan.get('rowSpecsSamples') or []) < 8:
                plan.setdefault('rowSpecsSamples', []).append({
                    'rowNo': row['rowNo'],
                    **{k: specs.get(k) for k in (
                        'originalName', 'originalCode', 'position', 'dimensions',
                        'composition', 'customerSpecific', 'parenProcessing',
                        'customerUse', 'parenSpecs', 'forcedServiceRule',
                    )},
                    'customer': csv.get('customer') or '',
                    'size': csv.get('size') or '',
                    'qty': csv.get('qty') or csv.get('quantity') or '',
                    'unitPrice': csv.get('unitPrice') or csv.get('unitCost') or '',
                })

            # 本体があるとき: この行の商品は本体へ寄せ、仕入先は加工業者のまま provenance に残す
            prod = dict(row.get('product') or {})
            handle = dict(row.get('handle') or {})
            # 表示名＝加工名（旧 displayName や別の予定名を復活させない）
            service_display = stock.text(info.get('serviceName') or '')
            info['displayName'] = service_display
            if group and group.get('garmentBodyKnown'):
                body_name = stock.text(
                    group.get('plannedGarmentName')
                    or (group.get('productPlan') or {}).get('productName')
                    or ''
                )
                if body_name:
                    prod['officialName'] = body_name
                    prod['applyOfficialName'] = True
                    prod['csvOriginalName'] = csv.get('productName') or ''
                # 通常仕入先は本体候補（加工業者はサブにもしない）
                garment_sup = ''
                if group.get('supplierCandidates'):
                    garment_sup = stock.text(group['supplierCandidates'][0] or '')
                elif group.get('plannedPrimarySupplier'):
                    garment_sup = stock.text(group['plannedPrimarySupplier'] or '')
                if garment_sup and not proc_svc.is_processing_vendor(garment_sup):
                    prod['primarySupplierName'] = garment_sup
                prod['subSupplierAdd'] = False
                prod['processingServiceSplit'] = True
                # 既存本体があれば再利用
                if group.get('existingCount') == 1 and (group.get('existingProducts') or []):
                    keep = group['existingProducts'][0]
                    prod['id'] = keep.get('id')
                    prod['action'] = 'reuse'
                elif group.get('productPlan', {}).get('action') == 'reuse' and group.get('productPlan', {}).get('id'):
                    prod['id'] = group['productPlan']['id']
                    prod['action'] = 'reuse'
                row['product'] = prod
                # 取扱の加工仕様 = 統一加工名（個別仕様は provenance）
                emb = service_display
                # 寸法・位置は取扱キーへ足さず provenance に残す（共通サービスと分離）
                if emb:
                    handle['proposedEmbroidery'] = emb
                    handle['embroidery'] = emb
                    handle['embroideryLabel'] = emb
                    handle['pendingEmbroiderySort'] = False
                    handle['fromProcessingServiceSplit'] = True
                row['handle'] = handle
                # 括弧分類欄: 加工サービス行は加工名＝表示名に同期（色・生地の迷いを減らす）
                pc = dict(row.get('parenClassification') or {})
                pc['processing'] = emb
                pc['plannedName'] = emb
                pc['processingServiceView'] = True
                row['parenClassification'] = pc
                # ステータス: 本体寄せができていればコミット可
                if row.get('status') not in (STATUS_EXCLUDED, STATUS_HOLD) and not info.get('needsConfirm'):
                    if prod.get('action') in ('create', 'reuse') and (
                        row.get('customer', {}).get('action') in ('create', 'reuse')
                    ):
                        row['status'] = STATUS_CREATE if prod.get('action') == 'create' else STATUS_REUSE
                        row['statusLabel'] = STATUS_LABELS[row['status']]
                        row['canCommit'] = True
                        row['skuDecisionNeeded'] = bool(
                            group.get('needsDecision')
                            and not group.get('decision')
                            and not group.get('processingServiceAutoSplit')
                        )
                prov_kind = 'processing_service_link'
            else:
                # 本体不明 or 品番なし: この行は加工サービス商品のみ登録（本体品番は流用しない）
                # 本体SKUグループ側は確認対象のまま。特定できた加工は確認待ちにしない。
                if group and group.get('garmentBodyReviewOnly'):
                    row['garmentBodyReviewOnly'] = True
                prod['category'] = stock.CATEGORY_PROCESS_SERVICE
                prod['proposedCategory'] = stock.CATEGORY_PROCESS_SERVICE
                prod['code'] = ''
                prod['codeOverride'] = ''
                prod['blankSku'] = True
                # 登録時の商品名＝加工名（別表示名欄なし）
                prod['officialName'] = service_display or info['serviceName']
                prod['applyOfficialName'] = True
                prod['primarySupplierName'] = info['vendorName']
                prod['processingServiceProduct'] = True
                prod['ignoreSkuDecisionGate'] = True
                if plan.get('productId'):
                    prod['id'] = plan['productId']
                    prod['action'] = 'reuse'
                else:
                    prod['action'] = 'create'
                row['product'] = prod
                row['handle'] = {
                    'action': 'excluded', 'id': None, 'candidates': [], 'reasons': [],
                    'embroidery': '', 'embroideryLabel': '',
                    'skipForProcessingService': True,
                }
                pc = dict(row.get('parenClassification') or {})
                synced = service_display or info.get('serviceName') or ''
                pc['processing'] = synced
                # 古い予定名／表示名を残さず加工名へ揃える
                pc['plannedName'] = synced
                pc['processingServiceView'] = True
                row['parenClassification'] = pc
                if row.get('status') not in (STATUS_EXCLUDED, STATUS_HOLD) and not info.get('needsConfirm'):
                    row['status'] = STATUS_CREATE if prod.get('action') == 'create' else STATUS_REUSE
                    row['statusLabel'] = STATUS_LABELS[row['status']]
                    row['canCommit'] = True
                    row['skuDecisionNeeded'] = False
                prov_kind = 'processing_service'

            # 内部照合用 provenance（初期在庫で旧名称→商品・加工仕様へ）
            # 通常画面の表示は加工名「刺繍」等。旧商品名は別名一覧へ出さず詳細確認用に保持。
            prov = dict(row.get('specProvenance') or {})
            row_specs = info.get('rowSpecs') or {}
            prov.update({
                'originalName': csv.get('productName') or '',
                'originalCode': code,
                'originalSupplier': csv.get('supplier') or '',
                'processing': info.get('serviceName') or '',
                'processingVendor': info.get('vendorName') or '',
                'processingServiceName': info.get('serviceName') or '',
                'processingDisplayName': service_display or info.get('serviceName') or '',
                'rowKind': prov_kind,
                'targetGarmentCode': code,
                'targetCustomer': csv.get('customer') or '',
                'csvSize': csv.get('size') or '',
                'csvQty': csv.get('qty') or csv.get('quantity') or '',
                'csvUnitPrice': csv.get('unitPrice') or csv.get('unitCost') or '',
                'isMainCandidate': True,
                'rowSpecs': row_specs,
                'customerSpecific': stock.text(row_specs.get('customerSpecific') or ''),
                'elmsEmbroideryRule': bool(info.get('elmsEmbroideryRule')),
                'hideOriginalNameAsAlias': bool(info.get('elmsEmbroideryRule')),
            })
            row['specProvenance'] = prov
            row['processingServicePlanKey'] = key
            row['processingServiceView'] = True
            if info.get('elmsEmbroideryRule'):
                # エルムス行は加工名不足などの確認待ちにしない
                row['processingServiceNeedsConfirm'] = False
                if row.get('status') == STATUS_REVIEW and row.get('canCommit') is False:
                    # 確認理由が加工名不足のみなら解除（他理由は後段で再評価）
                    reasons = [
                        r for r in (row.get('reasons') or [])
                        if '加工名' not in str(r) and '架空の加工' not in str(r)
                    ]
                    row['reasons'] = reasons

        elif info.get('isPhysicalParts'):
            # 実物の部品・資材: 加工サービス欄ではなく名称・仕入先・用途
            # 『袖／裾加工用』は用途（partsUsage）。加工サービスは作らない
            row['physicalPartsView'] = True
            pc = dict(row.get('parenClassification') or {})
            pc['physicalPartsView'] = True
            pc['processingServiceView'] = False
            # 誤って加工欄に残った用途は用途へ戻す
            usage = stock.text(pc.get('partsUsage') or '')
            proc = stock.text(pc.get('processing') or '')
            if not usage and proc and paren_classify.is_parts_usage_token(proc):
                pc['partsUsage'] = proc
                pc['processing'] = ''
            row['parenClassification'] = pc
        elif info.get('k2AutoExclude'):
            # 後段 _apply_k2_auto_exclusions で登録しないへ。確認待ち欄は出さない
            row['processingServiceView'] = True
            row['processingServiceNeedsConfirm'] = False
            pc = dict(row.get('parenClassification') or {})
            pc['processingServiceView'] = True
            pc['processingVendor'] = info.get('vendorName') or (row.get('csv') or {}).get('supplier') or ''
            if not stock.text(pc.get('processing') or ''):
                pc['processing'] = ''
            pc['k2AutoExcludePending'] = True
            row['parenClassification'] = pc
        elif info.get('isProcessingVendor') and info.get('needsConfirm'):
            # 既に attach_processing_service_detection で要確認化済み
            # 画面は加工サービス向け欄へ（色・生地と加工名の迷いを減らす）
            # 表示名（plannedName）を加工名へ無条件コピーしない
            row['processingServiceView'] = True
            pc = dict(row.get('parenClassification') or {})
            pc['processingServiceView'] = True
            pc['processingVendor'] = info.get('vendorName') or (row.get('csv') or {}).get('supplier') or ''
            # 加工名が空のまま予定名だけ残っている場合は加工名欄へ流し込まない
            if not stock.text(pc.get('processing') or ''):
                pc['processing'] = ''
            missing = info.get('missingFields') or []
            if missing:
                pc['processingMissingHint'] = '・'.join(str(x) for x in missing)
            row['parenClassification'] = pc

    # 分離後に自動まとめになった品番は、全行の SKU ゲートを下ろす
    for group in sku_groups or []:
        if not group.get('processingServiceAutoSplit'):
            continue
        code = stock.text(group.get('code') or '')
        for r in classified or []:
            if stock.text((r.get('csv') or {}).get('code') or '') == code:
                r['skuDecisionNeeded'] = False
                r['skuAutoMerged'] = True

    plans = list(service_plans.values())
    plans.sort(key=lambda p: (p.get('vendorName') or '', p.get('serviceName') or ''))
    return {
        'processingServicePlans': plans,
        'autoCount': sum(1 for p in plans if p.get('autoRegister')),
        'reuseCount': sum(1 for p in plans if p.get('action') == 'reuse'),
        'createCount': sum(1 for p in plans if p.get('action') == 'create'),
    }


def _ensure_processing_service_product(c, plan):
    """加工サービスを CATEGORY_PROCESS_SERVICE として確保（本体品番は流用しない）。

    登録名・一覧表示は加工名のみ。別表示名は使わない。加工に衣服数量は付けない。
    エルムス刺繍は既存の「刺繍加工」等があれば再利用し、表示名を「刺繍」へ揃える
    （旧名称は別名一覧へ追加しない）。複数件ある場合も削除せず1件を再利用。
    既存が部品・資材のまま残っている場合も ID 再利用し、カテゴリは自動変更しない
    （移行は inventory / 明示 apply でレビュー後に行う）。
    """
    name = stock.text(plan.get('serviceName') or '')
    vendor = stock.text(plan.get('vendorName') or '')
    if not name or not vendor:
        raise ValueError('加工サービス名と業者が必要です。')
    existing = proc_svc.find_existing_processing_service(c, name, vendor)
    if existing:
        pid = int(existing['id'])
        # エルムス規則: 表示は「刺繍」。旧「刺繍加工」等の別名登録はしない
        if plan.get('elmsEmbroideryRule') or (
            proc_svc.is_elms_corporation_vendor(vendor)
            and proc_svc.is_elms_embroidery_alias(name)
        ):
            canon = proc_svc.ELMS_EMBROIDERY_SERVICE_NAME
            cur = stock.text(existing.get('name') or '')
            if cur != canon:
                c.execute(
                    'UPDATE products SET name=?, updated_at=? WHERE id=?',
                    (canon, stock.stamp(), pid),
                )
        return pid, False
    # 別名表記（取り付け等）でも同じキーで再利用できるよう正規化名で保存
    # 表示名は加工名からのみ（plan.displayName の古い値を優先しない）
    plan['displayName'] = name
    sup_id, _ = _ensure_person(c, 'suppliers', stock.save_supplier, vendor, create=True)
    # 品番なし・加工サービス。管理品番に対象衣服品番を載せない
    saved = stock.save_product(c, {
        'code': '',
        'name': name,
        'color': '',
        'material': '',
        'category': stock.CATEGORY_PROCESS_SERVICE,
        'supplierId': sup_id,
        # サイズ必須制約回避。加工サービスに衣服サイズ在庫は付けない
        'sizes': ['—'],
        'links': [],  # 顧客紐づけしない（衣服在庫を付けない）
        'active': True,
    })
    pid = saved['id'] if isinstance(saved, dict) else int(saved)
    # エルムス規則: 対象衣服の旧商品名を別名一覧へ出さない。品番メモのみ（空品番はスキップ）
    if plan.get('elmsEmbroideryRule'):
        for gcode in plan.get('targetGarmentCodes') or []:
            gcode = stock.text(gcode)
            if not gcode or gcode in ('刺繍', '刺繍入れ'):
                continue
            note = f'加工対象品番:{gcode}'
            stock_product_merge.ensure_name_alias(c, pid, note)
        return pid, True
    # 対象品番は別名メモとして残す（品番カラムは空のまま）
    for gcode in plan.get('targetGarmentCodes') or []:
        note = f'加工対象品番:{stock.text(gcode)}'
        stock_product_merge.ensure_name_alias(c, pid, note)
    return pid, True


def _resolve_separate_process_service_fields(row, plan, variant):
    """個別整理の加工サービス欄。利用者入力 → 検出値 → CSV仕入先／商品名。"""
    plan = plan if isinstance(plan, dict) else {}
    variant = variant if isinstance(variant, dict) else {}
    csv_row = row.get('csv') or {}
    ps = row.get('processingService') if isinstance(row.get('processingService'), dict) else {}
    pc = row.get('parenClassification') if isinstance(row.get('parenClassification'), dict) else {}
    service_name = stock.text(plan.get('serviceName') or '')
    vendor_name = stock.text(plan.get('vendorName') or '')
    supplement = stock.text(plan.get('supplement') or '')
    if not service_name:
        service_name = stock.text(
            ps.get('serviceName')
            or pc.get('processing')
            or variant.get('processing')
            or ''
        )
        if not service_name:
            guessed = proc_svc.unify_processing_service_name(csv_row.get('productName') or '')
            service_name = stock.text(guessed or '')
        if not service_name:
            service_name = stock.text(csv_row.get('productName') or '')
    if not vendor_name:
        vendor_name = stock.text(
            ps.get('vendorName')
            or csv_row.get('supplier')
            or variant.get('supplier')
            or ''
        )
    if not supplement:
        specs = ps.get('rowSpecs') if isinstance(ps.get('rowSpecs'), dict) else {}
        bits = [
            stock.text(specs.get(k) or '')
            for k in ('customerSpecific', 'parenSpecs', 'dimensions', 'composition')
        ]
        supplement = '／'.join(b for b in bits if b) or stock.text(pc.get('supplement') or '')
    return service_name, vendor_name, supplement


def apply_separate_as_process_service_plans(c, classified, sku_groups, sku_decisions, processing_split_meta):
    """個別整理「加工サービスとして整理」を自動分離のあとに適用。

    - 登録名＝表示名＝加工名（別の表示名欄なし）
    - CATEGORY_PROCESS_SERVICE・品番なし。衣服SKUを加工SKUにしない
    - 同名・同業者のみ再利用。異なる加工名は別名統合しない
      （例: 同一SKUグループ内の ライン加工 と スナッパー加工）
    - CSV行・元品番・顧客・仕様は provenance に残し初期在庫照合へ
    """
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    meta = dict(processing_split_meta or {})
    plans_by_key = {}
    for p in (meta.get('processingServicePlans') or []):
        if not isinstance(p, dict):
            continue
        key = stock.text(p.get('dedupeKey') or '')
        if key:
            plans_by_key[key] = dict(p)
    group_by_code = {g['code']: g for g in (sku_groups or [])}
    # 個別整理で取り込む行番号（自動計画から外して二重登録しない）
    separate_owned_row_nos = set()

    for row in classified or []:
        code = stock.text((row.get('csv') or {}).get('code') or '')
        if not stock_product_merge.is_normal_product_code(code):
            continue
        group = group_by_code.get(code)
        if not group:
            continue
        raw = decisions.get(code) or decisions.get(str(code)) or {}
        if not isinstance(raw, dict):
            continue
        if stock.text(raw.get('decision')) != SKU_DECISION_SEPARATE:
            continue
        sep_plans = raw.get('plans') if isinstance(raw.get('plans'), dict) else {}
        variant = _row_variant(row)
        plan = (
            sep_plans.get(variant['key'])
            or sep_plans.get((row.get('csv') or {}).get('productName'))
            or {}
        )
        if stock.text(plan.get('approach') or '') != 'as_process_service':
            continue
        separate_owned_row_nos.add(int(row['rowNo']))

        csv_row = dict(row.get('csv') or {})
        service_name, vendor_name, supplement = _resolve_separate_process_service_fields(
            row, plan, variant,
        )
        if not service_name or not vendor_name:
            row['status'] = STATUS_REVIEW
            row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
            row['canCommit'] = False
            row['reasons'] = list(row.get('reasons') or []) + [
                f'品番「{code}」を加工サービスとして整理する場合は、'
                f'加工名と加工業者を入力してください。'
            ]
            row['pendingSeparateProcessService'] = True
            continue

        # 表示名は加工名のみ。エルムス以外で別名へ寄せない
        info = {
            'serviceName': service_name,
            'vendorName': vendor_name,
            'displayName': service_name,
            'identifiable': True,
            'isProcessingService': True,
        }
        key = proc_svc.service_dedupe_key(service_name, vendor_name)
        pplan = plans_by_key.get(key)
        if not pplan:
            pplan = _processing_service_plan_payload(
                info,
                source_row_nos=[row['rowNo']],
                garment_codes=[code] if code else [],
            )
            existing_svc = proc_svc.find_existing_processing_service(
                c, service_name, vendor_name,
            )
            if existing_svc:
                pplan['action'] = 'reuse'
                pplan['productId'] = existing_svc['id']
            else:
                pplan['action'] = 'create'
            pplan['autoRegister'] = True
            pplan['fromSeparateOrganize'] = True
            plans_by_key[key] = pplan
        else:
            if row['rowNo'] not in (pplan.get('sourceRowNos') or []):
                pplan.setdefault('sourceRowNos', []).append(row['rowNo'])
            if code and code not in (pplan.get('targetGarmentCodes') or []):
                pplan.setdefault('targetGarmentCodes', []).append(code)
            pplan['autoRegister'] = True
            pplan['fromSeparateOrganize'] = True
            # 個別整理の明示名を優先（自動検出の別名へ戻さない）
            pplan['serviceName'] = service_name
            pplan['displayName'] = service_name
            pplan['vendorName'] = vendor_name

        sample = {
            'rowNo': row['rowNo'],
            'originalName': csv_row.get('productName') or '',
            'originalCode': code,
            'customer': csv_row.get('customer') or '',
            'size': csv_row.get('size') or '',
            'qty': csv_row.get('qty') or csv_row.get('quantity') or '',
            'unitPrice': csv_row.get('unitPrice') or csv_row.get('unitCost') or '',
            'customerSpecific': supplement,
        }
        samples = list(pplan.get('rowSpecsSamples') or [])
        if not any(int(s.get('rowNo') or 0) == int(row['rowNo']) for s in samples):
            samples.append(sample)
            pplan['rowSpecsSamples'] = samples[:12]

        prod = dict(row.get('product') or {})
        prod['category'] = stock.CATEGORY_PROCESS_SERVICE
        prod['proposedCategory'] = stock.CATEGORY_PROCESS_SERVICE
        # 衣服SKUを加工サービスの品番にしない
        prod['code'] = ''
        prod['codeOverride'] = ''
        prod['blankSku'] = True
        prod['officialName'] = service_name
        prod['applyOfficialName'] = True
        prod['primarySupplierName'] = vendor_name
        prod['processingServiceProduct'] = True
        prod['ignoreSkuDecisionGate'] = True
        if pplan.get('productId'):
            prod['id'] = pplan['productId']
            prod['action'] = 'reuse'
        else:
            prod['id'] = None
            prod['action'] = 'create'
        row['product'] = prod
        row['handle'] = {
            'action': 'excluded', 'id': None, 'candidates': [], 'reasons': [],
            'embroidery': '', 'embroideryLabel': '',
            'skipForProcessingService': True,
        }
        pc = dict(row.get('parenClassification') or {})
        pc['processing'] = service_name
        pc['plannedName'] = service_name
        pc['processingVendor'] = vendor_name
        pc['processingServiceView'] = True
        pc['physicalPartsView'] = False
        if supplement:
            pc['supplement'] = supplement
        row['parenClassification'] = pc

        ps = dict(row.get('processingService') or {})
        ps.update({
            'serviceName': service_name,
            'vendorName': vendor_name,
            'displayName': service_name,
            'isProcessingService': True,
            'isProcessingVendor': True,
            'identifiable': True,
            'needsConfirm': False,
            'fromSeparateOrganize': True,
        })
        if supplement:
            specs = dict(ps.get('rowSpecs') or {})
            specs['customerSpecific'] = supplement
            ps['rowSpecs'] = specs
        row['processingService'] = ps

        # 初期在庫照合用: 元のCSV品番・顧客・仕様を保持（登録品番は空のまま）
        prov = dict(row.get('specProvenance') or {})
        row_specs = dict(ps.get('rowSpecs') or {})
        if supplement:
            row_specs['customerSpecific'] = supplement
        prov.update({
            'originalName': csv_row.get('productName') or '',
            'originalCode': code,
            'originalSupplier': csv_row.get('supplier') or '',
            'processing': service_name,
            'processingVendor': vendor_name,
            'processingServiceName': service_name,
            'processingDisplayName': service_name,
            'rowKind': 'processing_service',
            'targetGarmentCode': code,
            'targetCustomer': csv_row.get('customer') or '',
            'csvSize': csv_row.get('size') or '',
            'csvQty': csv_row.get('qty') or csv_row.get('quantity') or '',
            'csvUnitPrice': csv_row.get('unitPrice') or csv_row.get('unitCost') or '',
            'isMainCandidate': True,
            'rowSpecs': row_specs,
            'customerSpecific': stock.text(row_specs.get('customerSpecific') or ''),
            'fromSeparateOrganize': True,
            'hideOriginalNameAsAlias': False,
        })
        row['specProvenance'] = prov
        row['processingServicePlanKey'] = key
        row['processingServiceView'] = True
        row['physicalPartsView'] = False
        row['separateAsProcessService'] = True
        row['pendingSeparateProcessService'] = False
        row['skuDecisionNeeded'] = False
        if row.get('status') not in (STATUS_EXCLUDED, STATUS_HOLD):
            row['status'] = STATUS_CREATE if prod.get('action') == 'create' else STATUS_REUSE
            row['statusLabel'] = STATUS_LABELS[row['status']]
            row['canCommit'] = True

        # グループ表示用（確認済み詳細）
        group.setdefault('separateProcessServicePlans', [])
        group['separateProcessServicePlans'] = [
            p for p in (group.get('separateProcessServicePlans') or [])
            if stock.text(p.get('dedupeKey') or '') != key
        ]
        group['separateProcessServicePlans'].append({
            'variantKey': variant.get('key') or '',
            'serviceName': service_name,
            'vendorName': vendor_name,
            'category': stock.CATEGORY_PROCESS_SERVICE,
            'dedupeKey': key,
            'action': pplan.get('action') or 'create',
            'productId': pplan.get('productId'),
            'supplement': supplement,
            'sourceRowNos': list(pplan.get('sourceRowNos') or []),
            'originalCode': code,
        })

    # 個別整理が取り込んだ行を、自動検出計画の sourceRowNos から外す
    # （加工名編集で dedupeKey が変わったときの二重確保を防ぐ）
    if separate_owned_row_nos:
        for key, pplan in list(plans_by_key.items()):
            if pplan.get('fromSeparateOrganize'):
                continue
            src = [
                rn for rn in (pplan.get('sourceRowNos') or [])
                if int(rn) not in separate_owned_row_nos
            ]
            if not src:
                plans_by_key.pop(key, None)
                continue
            pplan['sourceRowNos'] = src

    plans = list(plans_by_key.values())
    plans.sort(key=lambda p: (p.get('vendorName') or '', p.get('serviceName') or ''))
    meta['processingServicePlans'] = plans
    meta['autoCount'] = sum(1 for p in plans if p.get('autoRegister'))
    meta['reuseCount'] = sum(1 for p in plans if p.get('action') == 'reuse')
    meta['createCount'] = sum(1 for p in plans if p.get('action') == 'create')
    return meta


def apply_content_split_as_process_service_plans(
    c, classified, sku_groups, sku_decisions, processing_split_meta,
):
    """内容別に分けて登録のうち加工サービス先を自動分離のあとに適用。

    - 加工名＋業者キーで再利用。別加工名は別名統合しない
      （例: 同一旧品番の ライン加工 と スナッパー加工 → 独立2件）
    - CSV行↔splitTargetId・顧客・仕様を provenance に残す
    - メーカー品番／旧CSV品番は元のまま（加工SKUに流用しない）
    """
    decisions = sku_decisions if isinstance(sku_decisions, dict) else {}
    meta = dict(processing_split_meta or {})
    plans_by_key = {}
    for p in (meta.get('processingServicePlans') or []):
        if not isinstance(p, dict):
            continue
        key = stock.text(p.get('dedupeKey') or '')
        if key:
            plans_by_key[key] = dict(p)
    group_by_code = {g['code']: g for g in (sku_groups or [])}
    owned_row_nos = set()

    for row in classified or []:
        code = stock.text((row.get('csv') or {}).get('code') or '')
        if not stock_product_merge.is_normal_product_code(code):
            continue
        group = group_by_code.get(code)
        if not group:
            continue
        raw = decisions.get(code) or decisions.get(str(code)) or {}
        if not isinstance(raw, dict):
            continue
        if stock.text(raw.get('decision')) != SKU_DECISION_CONTENT_SPLIT:
            continue
        split_info = group.get('contentSplitPlans') or {}
        if not split_info:
            split_info = build_content_split_plans(
                c, group, raw, classified=classified,
            )
            group['contentSplitPlans'] = split_info
        plan_by_row = {}
        for plan in (split_info.get('plans') or []):
            for rn in (plan.get('rowNos') or []):
                plan_by_row[int(rn)] = plan
        row_no = int(row.get('rowNo') or 0)
        plan = plan_by_row.get(row_no)
        if not plan or stock.text(plan.get('kind') or '') != 'process_service':
            continue
        owned_row_nos.add(row_no)

        service_name = stock.text(plan.get('serviceName') or plan.get('productName') or '')
        vendor_name = stock.text(plan.get('vendorName') or plan.get('supplier') or '')
        notes = stock.text(plan.get('notes') or '')
        if not service_name or not vendor_name:
            row['status'] = STATUS_REVIEW
            row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
            row['canCommit'] = False
            row['reasons'] = list(row.get('reasons') or []) + [
                f'品番「{code}」の内容別登録（加工サービス）は加工名と加工業者が必要です。'
            ]
            continue

        csv_row = dict(row.get('csv') or {})
        info = {
            'serviceName': service_name,
            'vendorName': vendor_name,
            'displayName': service_name,
            'identifiable': True,
            'isProcessingService': True,
        }
        key = proc_svc.service_dedupe_key(service_name, vendor_name)
        pplan = plans_by_key.get(key)
        if not pplan:
            pplan = _processing_service_plan_payload(
                info,
                source_row_nos=[row_no],
                garment_codes=[code] if code else [],
            )
            existing_svc = proc_svc.find_existing_processing_service(
                c, service_name, vendor_name,
            )
            if existing_svc:
                pplan['action'] = 'reuse'
                pplan['productId'] = existing_svc['id']
            else:
                pplan['action'] = 'create'
            pplan['autoRegister'] = True
            pplan['fromContentSplit'] = True
            pplan['splitTargetId'] = stock.text(plan.get('splitTargetId') or '')
            plans_by_key[key] = pplan
        else:
            if row_no not in (pplan.get('sourceRowNos') or []):
                pplan.setdefault('sourceRowNos', []).append(row_no)
            if code and code not in (pplan.get('targetGarmentCodes') or []):
                pplan.setdefault('targetGarmentCodes', []).append(code)
            pplan['autoRegister'] = True
            pplan['fromContentSplit'] = True
            pplan['serviceName'] = service_name
            pplan['displayName'] = service_name
            pplan['vendorName'] = vendor_name
            pplan['splitTargetId'] = stock.text(
                plan.get('splitTargetId') or pplan.get('splitTargetId') or ''
            )

        sample = {
            'rowNo': row_no,
            'originalName': csv_row.get('productName') or '',
            'originalCode': code,
            'customer': csv_row.get('customer') or '',
            'size': csv_row.get('size') or '',
            'qty': csv_row.get('qty') or csv_row.get('quantity') or '',
            'unitPrice': csv_row.get('unitPrice') or csv_row.get('unitCost') or '',
            'customerSpecific': notes,
            'splitTargetId': stock.text(plan.get('splitTargetId') or ''),
            'color': stock.text(plan.get('color') or ''),
            'material': stock.text(plan.get('material') or ''),
        }
        samples = list(pplan.get('rowSpecsSamples') or [])
        if not any(int(s.get('rowNo') or 0) == row_no for s in samples):
            samples.append(sample)
            pplan['rowSpecsSamples'] = samples[:12]

        prod = dict(row.get('product') or {})
        prod['category'] = stock.CATEGORY_PROCESS_SERVICE
        prod['proposedCategory'] = stock.CATEGORY_PROCESS_SERVICE
        prod['code'] = ''
        prod['codeOverride'] = ''
        prod['blankSku'] = True
        prod['officialName'] = service_name
        prod['applyOfficialName'] = True
        prod['primarySupplierName'] = vendor_name
        prod['processingServiceProduct'] = True
        prod['ignoreSkuDecisionGate'] = True
        prod['contentSplit'] = True
        prod['makerCode'] = code
        if pplan.get('productId'):
            prod['id'] = pplan['productId']
            prod['action'] = 'reuse'
        else:
            prod['id'] = None
            prod['action'] = 'create'
        row['product'] = prod
        row['handle'] = {
            'action': 'excluded', 'id': None, 'candidates': [], 'reasons': [],
            'embroidery': '', 'embroideryLabel': '',
            'skipForProcessingService': True,
        }
        pc = dict(row.get('parenClassification') or {})
        pc['processing'] = service_name
        pc['plannedName'] = service_name
        pc['processingVendor'] = vendor_name
        pc['processingServiceView'] = True
        pc['physicalPartsView'] = False
        if notes:
            pc['supplement'] = notes
        if stock.text(plan.get('color') or ''):
            _inherit_content_field(pc, 'color', plan.get('color'))
        if stock.text(plan.get('material') or ''):
            _inherit_content_field(pc, 'material', plan.get('material'))
        row['parenClassification'] = pc

        ps = dict(row.get('processingService') or {})
        ps.update({
            'serviceName': service_name,
            'vendorName': vendor_name,
            'displayName': service_name,
            'isProcessingService': True,
            'isProcessingVendor': True,
            'identifiable': True,
            'needsConfirm': False,
            'fromContentSplit': True,
            'splitTargetId': stock.text(plan.get('splitTargetId') or ''),
        })
        row['processingService'] = ps

        prov = dict(row.get('specProvenance') or {})
        row_specs = dict(ps.get('rowSpecs') or {})
        if notes:
            row_specs['customerSpecific'] = notes
        if stock.text(plan.get('material') or ''):
            row_specs['material'] = plan.get('material')
        if stock.text(plan.get('color') or ''):
            row_specs['color'] = plan.get('color')
        prov.update({
            'originalName': csv_row.get('productName') or '',
            'originalCode': code,
            'originalSupplier': csv_row.get('supplier') or '',
            'processing': service_name,
            'processingVendor': vendor_name,
            'processingServiceName': service_name,
            'processingDisplayName': service_name,
            'rowKind': 'processing_service',
            'targetGarmentCode': code,
            'targetCustomer': csv_row.get('customer') or '',
            'csvSize': csv_row.get('size') or '',
            'csvQty': csv_row.get('qty') or csv_row.get('quantity') or '',
            'csvUnitPrice': csv_row.get('unitPrice') or csv_row.get('unitCost') or '',
            'isMainCandidate': True,
            'rowSpecs': row_specs,
            'customerSpecific': stock.text(row_specs.get('customerSpecific') or ''),
            'contentSplitTargetId': stock.text(plan.get('splitTargetId') or ''),
            'contentSplitContentKey': stock.text(plan.get('contentKey') or ''),
            'fromContentSplit': True,
            'hideOriginalNameAsAlias': False,
        })
        row['specProvenance'] = prov
        row['processingServicePlanKey'] = key
        row['processingServiceView'] = True
        row['contentSplitTargetId'] = stock.text(plan.get('splitTargetId') or '')
        row['pendingContentSplitProcessService'] = False
        row['skuDecisionNeeded'] = False
        row['csv'] = dict(csv_row)
        row['csv']['makerCode'] = code
        if row.get('status') not in (STATUS_EXCLUDED, STATUS_HOLD):
            row['status'] = STATUS_CREATE if prod.get('action') == 'create' else STATUS_REUSE
            row['statusLabel'] = STATUS_LABELS[row['status']]
            row['canCommit'] = True

        group.setdefault('contentProcessServicePlans', [])
        group['contentProcessServicePlans'] = [
            p for p in (group.get('contentProcessServicePlans') or [])
            if stock.text(p.get('dedupeKey') or '') != key
        ]
        group['contentProcessServicePlans'].append({
            'contentKey': stock.text(plan.get('contentKey') or ''),
            'splitTargetId': stock.text(plan.get('splitTargetId') or ''),
            'serviceName': service_name,
            'vendorName': vendor_name,
            'category': stock.CATEGORY_PROCESS_SERVICE,
            'dedupeKey': key,
            'action': pplan.get('action') or 'create',
            'productId': pplan.get('productId'),
            'sourceRowNos': list(pplan.get('sourceRowNos') or []),
            'originalCode': code,
            'statusLabel': '確認済み',
        })

    if owned_row_nos:
        for key, pplan in list(plans_by_key.items()):
            if pplan.get('fromContentSplit') or pplan.get('fromSeparateOrganize'):
                continue
            src = [
                rn for rn in (pplan.get('sourceRowNos') or [])
                if int(rn) not in owned_row_nos
            ]
            if not src:
                plans_by_key.pop(key, None)
                continue
            pplan['sourceRowNos'] = src

    plans = list(plans_by_key.values())
    plans.sort(key=lambda p: (p.get('vendorName') or '', p.get('serviceName') or ''))
    meta['processingServicePlans'] = plans
    meta['autoCount'] = sum(1 for p in plans if p.get('autoRegister'))
    meta['reuseCount'] = sum(1 for p in plans if p.get('action') == 'reuse')
    meta['createCount'] = sum(1 for p in plans if p.get('action') == 'create')
    return meta


def _notation_auto_merged(group_or_row):
    """自動まとめ全般（公式名適用の判定用）。"""
    g = group_or_row or {}
    return bool(
        g.get('synonymAutoMerged')
        or g.get('colorOmissionAutoMerged')
        or g.get('colorNotationAutoMerged')
        or g.get('whitespaceAutoMerged')
        or g.get('pantsHemAutoMerged')
        or g.get('usedMarkerAutoMerged')
    )


def _skip_display_name_aliases(group_or_row):
    """同義・色省略のまとめだけ、通常画面の別名一覧へ原文を出さない。

    中古標記・空白・丈加工・配色区切りなどの従来まとめは別名を残す。
    """
    g = group_or_row or {}
    return bool(g.get('synonymAutoMerged') or g.get('colorOmissionAutoMerged'))


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
        row['pantsHemAutoMerged'] = bool(group.get('pantsHemAutoMerged'))
        row['colorOmissionAutoMerged'] = bool(group.get('colorOmissionAutoMerged'))
        row['colorNotationAutoMerged'] = bool(group.get('colorNotationAutoMerged'))
        row['skuRowNos'] = list(group.get('rowNos') or [])


def _apply_auto_merge_official_names(classified, sku_groups):
    """自動まとめ品番に公式名（標記なし優先）と色を付ける。

    同義・色表記・色省略のまとめでは、原文は通常画面の別名一覧へ出さず
    product_ledger_row_maps 側の内部照合に残す。
    """
    by_code = {g['code']: g for g in sku_groups}
    for row in classified:
        code = stock.text((row.get('csv') or {}).get('code'))
        group = by_code.get(code)
        if not group or not group.get('plannedSingleProduct'):
            continue
        plan = group.get('productPlan') or {}
        official = stock.text(
            group.get('plannedRegisteredName')
            or plan.get('productName')
            or ''
        )
        if not official:
            continue
        prod = dict(row.get('product') or {})
        prod['officialName'] = official
        planned_color = stock.text(
            group.get('plannedRegisteredColor')
            or plan.get('plannedRegisteredColor')
            or ''
        )
        if not planned_color:
            for v in group.get('variants') or []:
                if stock.text(v.get('color') or ''):
                    planned_color = stock.text(v.get('color') or '')
                    break
        if planned_color and not prod.get('officialColor'):
            prod['officialColor'] = planned_color
            prod['proposedColor'] = planned_color
        csv_name = stock.text((row.get('csv') or {}).get('productName') or '')
        if (
            _notation_auto_merged(group)
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
        # 同義・色省略まとめ: 通常画面の別名一覧へ原文を載せない（row_maps で照合）
        if _skip_display_name_aliases(group):
            prod['skipDisplayNameAliases'] = True
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

    # 色分割・内容分割予定は品番単位で一度だけ組み立てる
    for group in sku_groups:
        code = stock.text(group.get('code') or '')
        raw = decisions.get(code) or decisions.get(str(code)) or {}
        if isinstance(raw, dict) and stock.text(raw.get('decision')) == SKU_DECISION_COLOR_SPLIT:
            group['colorSplitPlans'] = build_color_split_plans(
                c, group, raw, classified=classified,
            )
            group['colorSplit'] = True
            group['excludeAuxStockAutoMerge'] = True
            apply_color_split_name_plan_display(group)
        if isinstance(raw, dict) and stock.text(raw.get('decision')) == SKU_DECISION_CONTENT_SPLIT:
            group['contentSplitPlans'] = build_content_split_plans(
                c, group, raw, classified=classified,
            )
            group['contentSplit'] = True
            group['excludeAuxStockAutoMerge'] = True
            apply_content_split_name_plan_display(group)
        # メーカー情報で補正の採用 → 名・色競合理由だけ落とす（プレビュー）
        if isinstance(raw, dict) and raw.get('makerAdopted'):
            _soften_after_maker_adopt(group, raw)

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

        # 自動まとめ済みでも、利用者が色別／内容別登録を選んだときはその決定を行へ反映する
        # （色省略の背景まとめが色別の未割当行を潰さない）
        if (
            not group['needsDecision']
            and decision not in (SKU_DECISION_COLOR_SPLIT, SKU_DECISION_CONTENT_SPLIT)
        ):
            continue

        if not decision:
            pending_decision += 1
            # サイズ・顧客だけの行差を要確認一覧へ溢れさせない
            row['canCommit'] = False
            row['skuDecisionNeeded'] = True
            row['reasons'] = list(row.get('reasons') or []) + [
                f'品番「{code}」の比較で【同じ商品としてまとめる】'
                f'【色別に分けて登録】【内容別に分けて登録】'
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
            primary_supplier, primary_source, primary_conflict, auto_primary, auto_reason = (
                _resolve_effective_primary_supplier(group, raw)
            )
            aux = group.get('auxPrimary') or {}
            # 公式名未指定でも仮代表名で登録可（既存公式名は上書きしない）
            name_plan = plan_provisional_official_name(group, {**raw, 'keepProductId': keep_id})
            group['namePlan'] = name_plan
            official_name = stock.text(name_plan.get('officialName') or '')
            # 手動色を優先。空なら代表名から分離した色を使う
            color = stock.text(raw.get('color') or '') or stock.text(name_plan.get('color') or '')
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
            group['plannedRegisteredName'] = official_name
            group['plannedRegisteredColor'] = color
            group['plannedColor'] = color
            if name_plan.get('changeSummary'):
                group['changeSummary'] = name_plan.get('changeSummary') or ''
            group['colorNameSplit'] = bool(name_plan.get('colorNameSplit') or color)
            group['plannedPrimarySupplier'] = primary_supplier
            group['plannedPrimarySupplierReason'] = (
                auto_reason if primary_source in ('auto', 'single') and primary_supplier else ''
            )
            group['plannedPrimarySupplierSource'] = primary_source
            group['provisionalName'] = provisional
            group['nameAdoptionRule'] = name_plan.get('adoptionRule') or ''
            # 確認済み／詳細／保存で同じ名・色になるよう productPlan も揃える
            merge_plan = dict(group.get('productPlan') or {})
            merge_plan['productName'] = official_name
            merge_plan['color'] = color
            merge_plan['plannedRegisteredName'] = official_name
            merge_plan['plannedRegisteredColor'] = color
            merge_plan['nameAdoptionRule'] = name_plan.get('adoptionRule') or ''
            merge_plan['provisional'] = provisional
            group['productPlan'] = merge_plan

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
                # 実物資材向け。加工サービスは as_process_service を使う
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
            elif approach == 'as_process_service':
                # 加工サービスとして整理。確定は apply_separate_as_process_service_plans で行う
                # （自動分離のあとで利用者の加工名・業者を優先する）
                service_name = stock.text(plan.get('serviceName') or '')
                vendor_name = stock.text(plan.get('vendorName') or '')
                ps = row.get('processingService') if isinstance(row.get('processingService'), dict) else {}
                csv_row = row.get('csv') or {}
                if not service_name:
                    service_name = stock.text(
                        ps.get('serviceName')
                        or (row.get('parenClassification') or {}).get('processing')
                        or variant.get('processing')
                        or ''
                    )
                    if not service_name:
                        guessed = proc_svc.unify_processing_service_name(
                            csv_row.get('productName') or ''
                        )
                        service_name = stock.text(guessed or '')
                    if not service_name:
                        service_name = stock.text(csv_row.get('productName') or '')
                if not vendor_name:
                    vendor_name = stock.text(
                        ps.get('vendorName')
                        or csv_row.get('supplier')
                        or variant.get('supplier')
                        or ''
                    )
                if not service_name or not vendor_name:
                    row['status'] = STATUS_REVIEW
                    row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                    row['canCommit'] = False
                    row['reasons'] = list(row.get('reasons') or []) + [
                        f'品番「{code}」を加工サービスとして整理する場合は、'
                        f'加工名と加工業者を入力してください。'
                    ]
                    pending_decision += 1
                else:
                    row['product'] = dict(row.get('product') or {})
                    row['product']['category'] = stock.CATEGORY_PROCESS_SERVICE
                    row['product']['proposedCategory'] = stock.CATEGORY_PROCESS_SERVICE
                    row['product']['code'] = ''
                    row['product']['codeOverride'] = ''
                    row['product']['blankSku'] = True
                    row['product']['officialName'] = service_name
                    row['product']['applyOfficialName'] = True
                    row['product']['primarySupplierName'] = vendor_name
                    row['product']['processingServiceProduct'] = True
                    row['product']['action'] = 'create'
                    row['product']['id'] = None
                    row['pendingSeparateProcessService'] = True
                    row['status'] = STATUS_CREATE
                    row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                    row['canCommit'] = True
            else:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の整理方法（品番修正 / 部品・資材 / 加工サービス / 品番なし / 保留）を選んでください。'
                    f'同じ通常品番のまま別商品として新規登録することはできません。'
                ]
                pending_decision += 1
            continue

        if decision == SKU_DECISION_COLOR_SPLIT:
            split_info = group.get('colorSplitPlans') or {}
            plans = split_info.get('plans') or []
            unassigned = {
                int(u.get('rowNo')): u for u in (split_info.get('unassignedRows') or [])
            }
            plan_by_row = {}
            for plan in plans:
                for rn in (plan.get('rowNos') or []):
                    plan_by_row[int(rn)] = plan

            row_no = int(row.get('rowNo') or 0)
            if row_no in unassigned or row_no not in plan_by_row:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                why = (unassigned.get(row_no) or {}).get('reason') or (
                    '色別登録の行き先が未定です。色を選ぶか、行の行き先を指定してください。'
                )
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の色別登録: {why}'
                ]
                pending_decision += 1
                continue

            plan = plan_by_row[row_no]
            if plan.get('needsCollisionChoice'):
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                existing_ids = ', '.join(
                    str(p['id']) for p in (plan.get('existingProducts') or [])[:5]
                )
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の色「{plan.get("color")}」の社内管理品番'
                    f'「{plan.get("internalCode")}」が既存商品（ID {existing_ids or "—"}）'
                    f'または他の予定と衝突しています。既存を使うか、社内品番を編集してください'
                    f'（無条件の上書きはしません）。'
                ]
                pending_decision += 1
                continue

            internal = stock.text(plan.get('internalCode') or '')
            color = stock.text(plan.get('color') or '')
            pname = stock.text(plan.get('productName') or '') or stock.text(
                (row.get('csv') or {}).get('productName') or ''
            )
            # 同色の原文だけを別名候補に（別色の旧名称を相互登録しない）
            same_color_mappings = [
                m for m in (split_info.get('nameMappings') or [])
                if (
                    stock.text(m.get('color') or '') == color
                    and not m.get('unassigned')
                    and stock.text(m.get('registrationName') or '') == pname
                )
            ]
            keep_id = plan.get('keepProductId')
            if keep_id and plan.get('collisionAction') in ('', 'reuse'):
                row['product'] = dict(row.get('product') or {})
                row['product']['id'] = int(keep_id)
                row['product']['action'] = 'reuse'
                row['product']['code'] = internal
                row['product']['codeOverride'] = internal
                row['product']['makerCode'] = code
                row['product']['officialName'] = pname
                row['product']['officialColor'] = color
                row['product']['applyOfficialName'] = True
                row['product']['colorSplit'] = True
                row['product']['skuMerge'] = False
                row['product']['nameMappings'] = same_color_mappings
                # メーカー品番は CSV 原文のまま（マップ・初期在庫照合用）。社内品番は codeOverride。
                row['csv'] = dict(row.get('csv') or {})
                row['csv']['makerCode'] = code
                if row.get('product', {}).get('sizeAction') == 'add':
                    row['status'] = STATUS_SIZE_ADD
                    row['statusLabel'] = STATUS_LABELS[STATUS_SIZE_ADD]
                else:
                    row['status'] = STATUS_REUSE
                    row['statusLabel'] = STATUS_LABELS[STATUS_REUSE]
                row['canCommit'] = True
            else:
                row['product'] = dict(row.get('product') or {})
                row['product']['id'] = None
                row['product']['action'] = 'create'
                row['product']['code'] = internal
                row['product']['codeOverride'] = internal
                row['product']['makerCode'] = code
                row['product']['officialName'] = pname
                row['product']['officialColor'] = color
                row['product']['applyOfficialName'] = True
                row['product']['colorSplit'] = True
                row['product']['skuMerge'] = False
                row['product']['nameMappings'] = same_color_mappings
                row['csv'] = dict(row.get('csv') or {})
                row['csv']['makerCode'] = code
                # 原文名は別名に残す（同色のみ。別色は載せない）
                orig = stock.text((row.get('csv') or {}).get('productName') or '')
                if orig and orig != pname:
                    row['product']['csvOriginalName'] = orig
                row['status'] = STATUS_CREATE
                row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                row['canCommit'] = True
            # 色・予定名を仕様由来に残し、初期在庫マップで色解決できるようにする
            provenance = dict(row.get('specProvenance') or {})
            provenance['color'] = color
            provenance['plannedName'] = pname
            provenance['originalName'] = stock.text(
                provenance.get('originalName')
                or (row.get('csv') or {}).get('productName')
                or ''
            )
            provenance['isMainCandidate'] = True
            row['specProvenance'] = provenance
            continue

        if decision == SKU_DECISION_CONTENT_SPLIT:
            split_info = group.get('contentSplitPlans') or {}
            plans = split_info.get('plans') or []
            unassigned = {
                int(u.get('rowNo')): u for u in (split_info.get('unassignedRows') or [])
            }
            plan_by_row = {}
            for plan in plans:
                for rn in (plan.get('rowNos') or []):
                    plan_by_row[int(rn)] = plan

            row_no = int(row.get('rowNo') or 0)
            if row_no in unassigned or row_no not in plan_by_row:
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                why = (unassigned.get(row_no) or {}).get('reason') or (
                    '内容別登録の行き先が未定です。商品名／加工名を確認してください。'
                )
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の内容別登録: {why}'
                ]
                pending_decision += 1
                continue

            plan = plan_by_row[row_no]
            row['contentSplitTargetId'] = stock.text(plan.get('splitTargetId') or '')
            row['contentSplitContentKey'] = stock.text(plan.get('contentKey') or '')
            if stock.text(plan.get('kind') or '') == 'process_service':
                # 加工サービス行の確定は apply_content_split_as_process_service_plans で行う
                if plan.get('needsFields'):
                    row['status'] = STATUS_REVIEW
                    row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                    row['canCommit'] = False
                    row['reasons'] = list(row.get('reasons') or []) + [
                        f'品番「{code}」の内容別登録（加工サービス）: 加工名と加工業者が必要です。'
                    ]
                    pending_decision += 1
                else:
                    row['pendingContentSplitProcessService'] = True
                    row['product'] = dict(row.get('product') or {})
                    row['product']['category'] = stock.CATEGORY_PROCESS_SERVICE
                    row['product']['proposedCategory'] = stock.CATEGORY_PROCESS_SERVICE
                    row['product']['code'] = ''
                    row['product']['codeOverride'] = ''
                    row['product']['blankSku'] = True
                    row['product']['officialName'] = stock.text(
                        plan.get('serviceName') or plan.get('productName') or ''
                    )
                    row['product']['applyOfficialName'] = True
                    row['product']['primarySupplierName'] = stock.text(
                        plan.get('vendorName') or ''
                    )
                    row['product']['processingServiceProduct'] = True
                    row['product']['contentSplit'] = True
                    row['product']['makerCode'] = code
                    row['csv'] = dict(row.get('csv') or {})
                    row['csv']['makerCode'] = code
                    row['status'] = STATUS_CREATE
                    row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                    row['canCommit'] = True
                continue

            if plan.get('needsCollisionChoice') or plan.get('needsInternalCode'):
                row['status'] = STATUS_REVIEW
                row['statusLabel'] = STATUS_LABELS[STATUS_REVIEW]
                row['canCommit'] = False
                existing_ids = ', '.join(
                    str(p['id']) for p in (plan.get('existingProducts') or [])[:5]
                )
                row['reasons'] = list(row.get('reasons') or []) + [
                    f'品番「{code}」の内容「{plan.get("productName") or ""}」の社内管理品番'
                    f'「{plan.get("internalCode") or "（未設定）"}」を確認してください'
                    f'（既存 ID {existing_ids or "—"}／衝突時は既存利用か品番編集。'
                    f'同じ社内品番の重複新規はしません）。'
                ]
                pending_decision += 1
                continue

            internal = stock.text(plan.get('internalCode') or '')
            pname = stock.text(plan.get('productName') or '') or stock.text(
                (row.get('csv') or {}).get('productName') or ''
            )
            color = stock.text(plan.get('color') or '')
            keep_id = plan.get('keepProductId')
            if keep_id and plan.get('collisionAction') in ('', 'reuse'):
                row['product'] = dict(row.get('product') or {})
                row['product']['id'] = int(keep_id)
                row['product']['action'] = 'reuse'
                row['product']['code'] = internal
                row['product']['codeOverride'] = internal
                row['product']['makerCode'] = code
                row['product']['officialName'] = pname
                row['product']['officialColor'] = color
                row['product']['applyOfficialName'] = True
                row['product']['contentSplit'] = True
                row['product']['skuMerge'] = False
                if stock.text(plan.get('material') or ''):
                    row['product']['material'] = plan.get('material')
                if stock.text(plan.get('supplier') or ''):
                    row['product']['primarySupplierName'] = plan.get('supplier')
                row['csv'] = dict(row.get('csv') or {})
                row['csv']['makerCode'] = code
                if row.get('product', {}).get('sizeAction') == 'add':
                    row['status'] = STATUS_SIZE_ADD
                    row['statusLabel'] = STATUS_LABELS[STATUS_SIZE_ADD]
                else:
                    row['status'] = STATUS_REUSE
                    row['statusLabel'] = STATUS_LABELS[STATUS_REUSE]
                row['canCommit'] = True
            else:
                row['product'] = dict(row.get('product') or {})
                row['product']['id'] = None
                row['product']['action'] = 'create'
                row['product']['code'] = internal
                row['product']['codeOverride'] = internal
                row['product']['makerCode'] = code
                row['product']['officialName'] = pname
                row['product']['officialColor'] = color
                row['product']['applyOfficialName'] = True
                row['product']['contentSplit'] = True
                row['product']['skuMerge'] = False
                if stock.text(plan.get('material') or ''):
                    row['product']['material'] = plan.get('material')
                if stock.text(plan.get('supplier') or ''):
                    row['product']['primarySupplierName'] = plan.get('supplier')
                row['csv'] = dict(row.get('csv') or {})
                row['csv']['makerCode'] = code
                orig = stock.text((row.get('csv') or {}).get('productName') or '')
                if orig and orig != pname:
                    row['product']['csvOriginalName'] = orig
                row['status'] = STATUS_CREATE
                row['statusLabel'] = STATUS_LABELS[STATUS_CREATE]
                row['canCommit'] = True
            provenance = dict(row.get('specProvenance') or {})
            provenance['color'] = color
            provenance['plannedName'] = pname
            provenance['material'] = stock.text(plan.get('material') or '')
            provenance['processing'] = stock.text(plan.get('processing') or '')
            provenance['originalName'] = stock.text(
                provenance.get('originalName')
                or (row.get('csv') or {}).get('productName')
                or ''
            )
            provenance['originalCode'] = code
            provenance['contentSplitTargetId'] = stock.text(plan.get('splitTargetId') or '')
            provenance['isMainCandidate'] = True
            row['specProvenance'] = provenance
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
    """サイズ／取扱の重複集計用キー（通常品番は品番単位。色分割は社内品番）。"""
    prod = row.get('product') or {}
    code = stock.text(
        prod.get('codeOverride') if prod.get('codeOverride') is not None
        else (row.get('csv') or {}).get('code')
    )
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
        csv = row.get('csv') or {}
        prod = row.get('product') or {}
        code = stock.text(
            prod.get('codeOverride') if prod.get('codeOverride') is not None else csv.get('code')
        )
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


def build_opening_stock_preview(c, body):
    """段階2: 初期在庫CSVのみ。商品台帳CSVは不要。永続マップで登録先を解決。"""
    ensure_schema(c)
    stock_product_merge.migrate_product_merge(c)
    t_all = time.perf_counter()
    t0 = time.perf_counter()
    aux_uploaded = decode_aux_upload(body)
    parse_ms = round((time.perf_counter() - t0) * 1000, 2)
    if not aux_uploaded:
        raise ValueError('初期在庫CSVを選んでください。')
    opening_stock = bulk.build_opening_stock_plans_from_maps(
        c,
        aux_uploaded=aux_uploaded,
        default_site_name=stock.text(body.get('defaultSiteName')) or stock.UNCONFIRMED_SITE_NAME,
        opening_selections=(
            body.get('openingSelections') if isinstance(body.get('openingSelections'), dict) else {}
        ),
    )
    if body.get('basisDate'):
        try:
            opening_stock['basisDate'] = stock.parse_date(body.get('basisDate'), '在庫基準日')
        except ValueError as e:
            opening_stock['basisDateError'] = str(e)
    prep_ms = (opening_stock.get('timingsMs') or {}).get('openingStockPrep', 0)
    fp = db_fingerprint(c)
    snap = stock_snapshot(c)
    total_ms = round((time.perf_counter() - t_all) * 1000, 2)
    return {
        'importMode': IMPORT_MODE_OPENING,
        'registerOpeningStock': True,
        'fileName': '',
        'fileSha256': '',
        'fileSize': 0,
        'encoding': '',
        'fields': [],
        'rowCount': 0,
        'rows': [],
        'skuGroups': [],
        'skuGroupCount': 0,
        'skuDecisionPending': 0,
        'skuDecisionPendingRows': 0,
        'bulkApprovableCount': 0,
        'individualReviewCount': 0,
        'autoMergePlans': [],
        'auxOpeningStock': {
            'fileName': aux_uploaded['fileName'],
            'fileSha256': aux_uploaded['fileSha256'],
            'fileSize': aux_uploaded['fileSize'],
            'encoding': aux_uploaded['encoding'],
            'rowCount': len(aux_uploaded['rows']),
            'ignoredColumns': aux_uploaded.get('ignoredColumns') or [],
            'willRegisterOpeningStock': True,
            'judgmentOnly': False,
            'note': opening_stock.get('note') or '',
        },
        'openingStock': opening_stock,
        'openingStockSummary': bulk.summarize_opening_for_preview(opening_stock),
        'reviewTierLabels': dict(REVIEW_TIER_LABELS),
        'provisionalNameTag': PROVISIONAL_NAME_TAG,
        'counts': dict(STATUS_LABELS),
        'countsRaw': {},
        'plannedCounts': {},
        'outcomeSummary': {},
        'reviewCount': 0,
        'previewFingerprint': fp,
        'previewAt': stock.stamp(),
        'stockSnapshot': snap,
        'previousImports': previous_imports_for_file(c, aux_uploaded['fileSha256']),
        'categoryOptions': list(stock.PRODUCT_CATEGORY_OPTIONS),
        'progressLabels': {
            'parse': 'CSVを読み込み中',
            'judge': '商品を照合中',
            'opening': '初期在庫の登録予定を作成中',
        },
        'timingsMs': {
            'csvParse': parse_ms,
            'productJudgment': 0,
            'openingStockPrep': prep_ms,
            'total': total_ms,
        },
        'note': (
            '初期在庫CSVだけで登録予定を作ります。商品台帳CSVの再選択は不要です。'
            '登録先は商品登録時に保存した原文対応マップから解決します'
            '（改名・色／加工分割後も原文名で照合）。'
            '曖昧・削除済み・未登録は理由を表示し、別商品へ推測登録しません。'
            '数量は「在庫」列のみ。プレビューではDBを変えません。'
        ),
    }


def build_preview(c, body):
    ensure_schema(c)
    stock_product_merge.migrate_product_merge(c)
    mode = resolve_import_mode(body)
    if mode == IMPORT_MODE_OPENING:
        return build_opening_stock_preview(c, body)

    t_all = time.perf_counter()
    t0 = time.perf_counter()
    uploaded = decode_upload(body)
    aux_uploaded = decode_aux_upload(body)
    parse_ms = round((time.perf_counter() - t0) * 1000, 2)
    mode = resolve_import_mode(body, aux_uploaded)
    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    sku_decisions = body.get('skuDecisions') if isinstance(body.get('skuDecisions'), dict) else {}
    row_classifications = (
        body.get('rowClassifications') if isinstance(body.get('rowClassifications'), dict) else {}
    )
    t_judge = time.perf_counter()

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

    attach_processing_service_detection(classified)
    sku_groups = build_sku_groups(c, classified)
    processing_split_meta = apply_processing_service_split(c, classified, sku_groups)
    apply_manual_register_kind(classified, row_classifications)
    register_opening = register_opening_stock_requested(body, aux_uploaded)
    # products モードでは初期在庫準備を絶対に走らせない（判定補助のみ）
    if mode == IMPORT_MODE_PRODUCTS:
        register_opening = False
    aux_meta = attach_aux_primary_judgment(
        sku_groups, aux_uploaded, register_opening=register_opening,
    )
    # 在庫一意／候補1組 → フルパッケージ採用。色・加工・素材差だけでは要確認に残さない
    apply_aux_full_package_adoption(sku_groups, sku_decisions)
    # 色別に分けて登録: 選択を再照合でも保持し、在庫自動まとめ対象外の予定を付与
    attach_color_split_plans(c, sku_groups, sku_decisions, classified=classified)
    # 内容別に分けて登録: 色別とは独立。件数見積りは常時、予定は選択時
    attach_content_split_plans(c, sku_groups, sku_decisions, classified=classified)
    # 補助CSV判定後に仮代表名の予定を更新（決定前の画面表示用）
    for g in sku_groups:
        raw = sku_decisions.get(g['code']) or sku_decisions.get(str(g['code'])) or {}
        if not isinstance(raw, dict):
            raw = {}
        is_color_split = stock.text(raw.get('decision')) == SKU_DECISION_COLOR_SPLIT
        is_content_split = stock.text(raw.get('decision')) == SKU_DECISION_CONTENT_SPLIT
        if is_color_split and g.get('colorSplitPlans'):
            # 色別選択中は統合用 namePlan を有効予定として載せない
            apply_color_split_name_plan_display(g)
        elif is_content_split and g.get('contentSplitPlans'):
            apply_content_split_name_plan_display(g)
        else:
            g['namePlan'] = plan_provisional_official_name(g, raw if stock.text(raw.get('decision')) else None)
            # 在庫一意採用時は aux の代表名を優先表示（既存維持は namePlan 側で守る）
            if g.get('auxStockPickAuto') and (g.get('auxPrimary') or {}).get('suggestedOfficialName'):
                if not (g.get('namePlan') or {}).get('keepsExistingOfficialName'):
                    np = dict(g.get('namePlan') or {})
                    np['officialName'] = stock.text(
                        (g.get('auxPrimary') or {}).get('suggestedOfficialName') or ''
                    ) or np.get('officialName')
                    aux_col = stock.text(
                        (g.get('auxPrimary') or {}).get('plannedRegisteredColor')
                        or (g.get('auxPrimary') or {}).get('adoptedColor')
                        or ''
                    )
                    if aux_col and not stock.text(raw.get('color') or ''):
                        np['color'] = aux_col
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
            np = g.get('namePlan') or {}
            g['plannedOfficialName'] = np.get('officialName') or ''
            g['nameAdoptionRule'] = np.get('adoptionRule') or ''
            g['provisionalName'] = bool(np.get('provisional'))
            # 代表名分離色を予定へ載せる（手動色優先。aux 自動採用済みの色は維持）
            plan_color = (
                stock.text(raw.get('color') or '')
                or stock.text(np.get('color') or '')
                or stock.text(g.get('plannedRegisteredColor') or '')
                or stock.text(g.get('plannedColor') or '')
            )
            if plan_color:
                if not stock.text(g.get('plannedRegisteredColor') or ''):
                    g['plannedRegisteredColor'] = plan_color
                if not stock.text(g.get('plannedColor') or ''):
                    g['plannedColor'] = plan_color
            if (
                stock.text(np.get('officialName') or '')
                and not stock.text(g.get('plannedRegisteredName') or '')
            ):
                g['plannedRegisteredName'] = np.get('officialName') or ''
        # 自動まとめ品番は namePlan の代表名を商品計画へ反映（タグは埋め込まない）
        if (
            g.get('plannedSingleProduct')
            and g.get('namePlan')
            and not is_color_split
            and not is_content_split
        ):
            plan = dict(g.get('productPlan') or {})
            if g['plannedOfficialName']:
                plan['productName'] = g['plannedOfficialName']
            plan['nameAdoptionRule'] = g['nameAdoptionRule']
            plan['provisional'] = g['provisionalName']
            plan_color = (
                stock.text(g.get('plannedColor') or '')
                or stock.text(g.get('plannedRegisteredColor') or '')
                or stock.text((g.get('namePlan') or {}).get('color') or '')
                or stock.text(plan.get('color') or '')
            )
            if plan_color:
                plan['color'] = plan_color
                plan['plannedRegisteredColor'] = plan_color
            if g.get('plannedOfficialName'):
                plan['plannedRegisteredName'] = g['plannedOfficialName']
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
    # 補助CSV・SKU決定のあとでも加工分離を再適用（provenance / 仕入先除外を守る）
    processing_split_meta = apply_processing_service_split(c, classified, sku_groups)
    # 個別整理「加工サービスとして整理」は自動分離のあとに適用（編集した加工名・業者を優先）
    processing_split_meta = apply_separate_as_process_service_plans(
        c, classified, sku_groups, sku_decisions, processing_split_meta,
    )
    # 内容別に分けて登録の加工サービス先（ライン／スナッパー等を独立登録）
    processing_split_meta = apply_content_split_as_process_service_plans(
        c, classified, sku_groups, sku_decisions, processing_split_meta,
    )
    # 手動登録区分は最後に再適用（自動分離・個別整理のあとでも手動が勝つ）
    apply_manual_register_kind(classified, row_classifications)
    # 色分割などで makerCode が付いたあと、ボタン表示用メタを再計算
    for row in classified:
        mk = maker_sku_lookup.row_maker_correction_meta(row)
        # 採用時に渡した元品番・注記を保持（初期在庫照合用）
        ov = (
            row_classifications.get(str(row.get('rowNo')))
            or row_classifications.get(row.get('rowNo'))
            or {}
        )
        code_t = stock.text((row.get('csv') or {}).get('code') or '')
        sku_ov = sku_decisions.get(code_t) or sku_decisions.get(str(code_t)) or {}
        if not isinstance(ov, dict):
            ov = {}
        if not isinstance(sku_ov, dict):
            sku_ov = {}
        for key in ('sourceSku', 'searchMakerSku', 'usageNote', 'originalTitle', 'cleanedName'):
            val = stock.text(ov.get(key) or sku_ov.get(key) or '')
            if val:
                mk[key] = val
        # 注記分離だけの再計算結果より、採用時の明示値を優先
        if not mk.get('usageNote'):
            note_meta = maker_sku_lookup.row_source_sku_and_note(row)
            if note_meta.get('usageNote'):
                mk['usageNote'] = note_meta['usageNote']
            if note_meta.get('sourceSku') and not mk.get('sourceSku'):
                mk['sourceSku'] = note_meta['sourceSku']
        row['makerCorrection'] = mk
        if mk.get('usageNote') or mk.get('sourceSku') or mk.get('searchMakerSku'):
            prov = dict(row.get('specProvenance') or {})
            if mk.get('usageNote'):
                prov['usageNote'] = mk['usageNote']
            if mk.get('sourceSku'):
                prov['sourceSku'] = mk['sourceSku']
                # マップキー用の原文品番は注記込みのまま
                if not stock.text(prov.get('sourceCode') or ''):
                    prov['sourceCode'] = mk['sourceSku']
            if mk.get('searchMakerSku'):
                prov['searchMakerSku'] = mk['searchMakerSku']
            row['specProvenance'] = prov
    # 品番比較一覧用: 確認待ち＋有効メーカー品番があるグループにボタンメタ
    rows_by_no = {r.get('rowNo'): r for r in classified}
    for g in sku_groups:
        group_rows = [
            rows_by_no[n] for n in (g.get('rowNos') or [])
            if n in rows_by_no
        ]
        g['makerCorrection'] = maker_sku_lookup.sku_group_maker_correction_meta(
            g, group_rows,
        )
    # K2 加工名不明の自動除外 → ユーザー手動除外の順
    # （手動が最終上書き。自動整理・メーカー補正のあとに適用し、勝手に登録対象へ戻さない）
    k2_auto_meta = _apply_k2_auto_exclusions(
        classified, sku_groups, processing_split_meta,
    )
    row_exclusion_meta = _apply_user_row_exclusions(
        classified, sku_groups, processing_split_meta, _parse_row_exclusions(body),
    )
    if isinstance(decision_meta, dict):
        decision_meta = dict(decision_meta)
        decision_meta['userExcludedRows'] = row_exclusion_meta.get('excludedRowCount') or 0
        decision_meta['autoExcludedRows'] = k2_auto_meta.get('autoExcludedRowCount') or 0
        # 全行除外で needsDecision を落とした分を pending から再計算
        decision_meta['pendingDecisionGroups'] = sum(
            1 for g in sku_groups if g.get('needsDecision') and not g.get('decision')
        )

    counts = defaultdict(int)
    for row in classified:
        counts[row['status']] += 1

    review_count = counts.get(STATUS_REVIEW, 0)
    sku_pending_rows = sum(1 for r in classified if r.get('skuDecisionNeeded'))
    planned = _planned_counts(classified, sku_groups)
    planned['userExcludedRows'] = row_exclusion_meta.get('excludedRowCount') or 0
    planned['autoExcludedRows'] = k2_auto_meta.get('autoExcludedRowCount') or 0
    planned['registrationExcludedRows'] = (
        (planned['userExcludedRows'] or 0) + (planned['autoExcludedRows'] or 0)
    )
    summary = _preview_outcome_summary(classified, sku_groups, planned, decision_meta)
    summary['userExcludedRows'] = row_exclusion_meta.get('excludedRowCount') or 0
    summary['autoExcludedRows'] = k2_auto_meta.get('autoExcludedRowCount') or 0
    summary['registrationExcludedRows'] = planned['registrationExcludedRows']
    auto_plans = [
        g.get('productPlan') for g in sku_groups
        if g.get('plannedSingleProduct') and g.get('productPlan')
    ]
    fp = db_fingerprint(c)
    snap = stock_snapshot(c)
    if register_opening:
        note = (
            '商品台帳と初期在庫をまとめて確認します。登録ボタンを押すまでDBは変わりません。'
            '通常品番は1品番=1商品です。サイズ・顧客・先頭「＊/*」中古標記・空白/全角半角・'
            '明示シノニム（例: パンツ／スラックス）だけの差は自動で1商品にまとめます。'
            '商品名の括弧内は辞書規則で色・加工・生地へ分類し、取扱の加工仕様／商品の色・生地へ保存します。'
            '初期在庫の数量は「在庫」列のみ（補正在庫数・購入・出荷は不使用）。0・マイナスもそのまま登録します。'
            '対応は元CSV行→確定した商品・取扱・サイズ・仕様です（改名後の商品名だけでは再照合しません）。'
            '保留した品番の初期在庫は同時に保留し、片方だけ登録しません。'
            '品番空欄は部品・資材として取り込み（登録後にカテゴリ変更可）。'
        )
    else:
        note = (
            '商品と顧客取扱を登録します。'
            + (
                f'{AUX_JUDGMENT_ONLY_NOTE}。'
                if aux_meta else
                '初期在庫CSV未選択のため在庫数量は書き込みません。'
            )
            + '通常品番は1品番=1商品です。サイズ・顧客・先頭「＊/*」中古標記・空白/全角半角・'
            '明示シノニム（例: パンツ／スラックス）だけの差は自動で1商品にまとめます。'
            '商品名の括弧内は辞書規則で色・加工・生地へ分類し、取扱の加工仕様／商品の色・生地へ保存します。'
            '未分類の括弧文言は商品名に残して登録します（未分類だけでは要確認にしません）。'
            '複数色・複数生地など曖昧な括弧は原文のまま要確認（自動確定しません）。'
            '色・生地・加工・商品種の疑いや残り曖昧さ、男女の明示対立（男性用⇔女性用）は確認が必要です。'
            '代表名が未確定のときは「名称未整理」ステータスで示し、名前文字列には埋め込みません。'
            'CSV原文は照合用別名に残します。登録時に原文→商品／取扱／仕様の対応を永続化します。'
            '品番空欄は部品・資材として取り込み（登録後にカテゴリ変更可）。'
            '保留した品番は今回登録せず、同じCSVの再読込で処理できます。'
            'この画面は確定前のプレビューです（登録ボタンを押すまで台帳は変わりません）。'
            '初期在庫の登録は別段階「初期在庫を登録」で行います。'
        )
    if aux_meta and not register_opening:
        note += (
            f' {AUX_JUDGMENT_ONLY_NOTE}。'
            '正の在庫合計が最多の候補が一意のとき'
            '（または非ゼロが1組／候補自体が1組のとき）'
            'その候補の商品名・仕入先・色・加工・素材をセットで採用し、'
            '通常仕入先の判定補助にも使います。在庫数量は書き込みません。'
            '色・加工・素材・「女性用」等の対象標記の有無・一般名と具体名の言い回し差だけでは、'
            '在庫採用なら要確認にしません。男性用と女性用の明示対立は確認のままです。'
        )

    judge_ms = round((time.perf_counter() - t_judge) * 1000, 2)
    opening_stock = None
    opening_prep_ms = 0.0
    if register_opening and aux_uploaded and mode != IMPORT_MODE_PRODUCTS:
        t_open = time.perf_counter()
        opening_stock = bulk.build_opening_stock_plans(
            c,
            classified=classified,
            sku_groups=sku_groups,
            aux_uploaded=aux_uploaded,
            default_site_name=stock.text(body.get('defaultSiteName')) or stock.UNCONFIRMED_SITE_NAME,
            opening_selections=body.get('openingSelections') if isinstance(body.get('openingSelections'), dict) else {},
        )
        opening_prep_ms = round((time.perf_counter() - t_open) * 1000, 2)
        # basisDate は確定時必須。プレビューでは任意（空なら当日想定をUI側で埋める）
        if body.get('basisDate'):
            try:
                opening_stock['basisDate'] = stock.parse_date(body.get('basisDate'), '在庫基準日')
            except ValueError as e:
                opening_stock['basisDateError'] = str(e)

    total_ms = round((time.perf_counter() - t_all) * 1000, 2)
    return {
        'importMode': mode,
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
        'processingServicePlans': (processing_split_meta or {}).get('processingServicePlans') or [],
        'processingServiceSummary': {
            'autoCount': (processing_split_meta or {}).get('autoCount') or 0,
            'reuseCount': (processing_split_meta or {}).get('reuseCount') or 0,
            'createCount': (processing_split_meta or {}).get('createCount') or 0,
        },
        'auxOpeningStock': aux_meta,
        'registerOpeningStock': register_opening,
        'openingStock': opening_stock,
        'openingStockSummary': bulk.summarize_opening_for_preview(opening_stock),
        'reviewTierLabels': dict(REVIEW_TIER_LABELS),
        'provisionalNameTag': PROVISIONAL_NAME_TAG,
        'counts': dict(STATUS_LABELS),
        'countsRaw': dict(counts),
        'plannedCounts': planned,
        'outcomeSummary': summary,
        'userRowExclusions': {
            'count': row_exclusion_meta.get('excludedRowCount') or 0,
            'rowNos': row_exclusion_meta.get('excludedRowNos') or [],
            'reason': USER_ROW_EXCLUDE_REASON,
        },
        'autoRowExclusions': {
            'count': k2_auto_meta.get('autoExcludedRowCount') or 0,
            'rowNos': k2_auto_meta.get('autoExcludedRowNos') or [],
            'reason': K2_AUTO_EXCLUDE_REASON,
        },
        'reviewCount': review_count,
        'previewFingerprint': fp,
        'previewAt': stock.stamp(),
        'stockSnapshot': snap,
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
        'categoryOptions': list(stock.PRODUCT_CATEGORY_OPTIONS),
        'progressLabels': {
            'parse': 'CSVを読み込み中',
            'judge': '商品を照合中',
            'opening': '初期在庫の登録予定を作成中',
        },
        'timingsMs': {
            'csvParse': parse_ms,
            'productJudgment': judge_ms,
            'openingStockPrep': opening_prep_ms,
            'total': total_ms,
        },
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


def _apply_official_name(
    c, product_id, official_name, color=None, csv_name=None, material=None,
    *, skip_display_name_aliases=False,
):
    """公式名を確定し、CSV名・旧名を別名に残す。

    skip_display_name_aliases=True のときは通常画面の別名一覧へ出さない
    （同義・色省略などの表記ゆれ。内部照合は row_maps 側）。
    """
    if not product_id or not official_name:
        return False
    current = stock.product_row(c, product_id)
    if not skip_display_name_aliases:
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


def _registration_name_with_supplement(official_name, supplement):
    """補足専用カラムが無いため、既存の商品名構造（括弧）へ載せる。

    上下区分（上／下）は品番末尾へ移す方針のため、補足としては載せず名にも付けない。
    """
    name = stock.text(official_name or '')
    supp = stock.text(supplement or '')
    if supp in position_sku.POSITION_TOKENS:
        return name
    if not supp:
        return name
    if not name:
        return f'（{supp}）'
    if supp in name:
        return name
    return f'{name}（{supp}）'


def _row_registered_code(row) -> str:
    """行の登録予定品番（上下末尾付きを優先）。"""
    csv_info = row.get('csv') if isinstance(row, dict) else {}
    prod = row.get('product') if isinstance(row, dict) else {}
    if not isinstance(csv_info, dict):
        csv_info = {}
    if not isinstance(prod, dict):
        prod = {}
    if prod.get('codeOverride') is not None and stock.text(prod.get('codeOverride')):
        return stock.text(prod.get('codeOverride'))
    for key in ('registeredCode', 'plannedRegisteredSku', 'code'):
        val = stock.text(csv_info.get(key) or '')
        if val:
            return val
    return stock.text(prod.get('code') or '')


def _ensure_product(c, product_info, supplier_id, csv_name, *, customer_id=None, force_create=False):
    if product_info.get('codeOverride') is not None:
        product_info = dict(product_info)
        product_info['code'] = product_info.get('codeOverride')

    code_for_name = stock.text(product_info.get('codeOverride') if product_info.get('codeOverride') is not None else product_info.get('code') or '')
    scoped = bool(product_info.get('scopedColorCorrection'))
    # 別注・品番なしは原文名を優先（括弧除去名へ自動置換しない）
    # スコープ付き色名補正が付いた別注は登録予定名を使う（原文は別名へ）。
    keep_csv_name = (
        (
            legacy.is_custom_order_code(code_for_name)
            or not stock.text(code_for_name)
            or code_for_name == stock_product_merge.UNKNOWN_CODE
        )
        and not scoped
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
    official_name = _registration_name_with_supplement(
        official_name,
        product_info.get('officialSupplement') or product_info.get('proposedSupplement') or '',
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
                skip_display_name_aliases=bool(product_info.get('skipDisplayNameAliases')),
            )
        else:
            # 再利用時は CSV 名・対応表の原文を別名に残す（公式名は変えない）
            # 表記ゆれ自動まとめは通常画面別名へ出さず、row_maps で内部照合する
            if not product_info.get('skipDisplayNameAliases'):
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
                if (
                    not product_info.get('skipDisplayNameAliases')
                    and stock.text(matches[0]['name']) != stock.text(csv_name)
                ):
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
                        skip_display_name_aliases=bool(product_info.get('skipDisplayNameAliases')),
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
    maker_code = stock.text(product_info.get('makerCode') or product_info.get('maker_code') or '')
    save_body = {
        'code': code,
        'name': name,  # 公式名（括弧分類後の予定名またはCSV原文）
        'color': stock.text(official_color) if official_color is not None else '',
        'material': stock.text(official_material) if official_material is not None else '',
        'category': category,
        'supplierId': create_supplier_id,
        'sizes': [product_info['size']],
        'links': [],
        'active': True,
    }
    if maker_code:
        save_body['makerCode'] = maker_code
    saved = stock.save_product(c, save_body)
    # 原文名は照合用別名（初期在庫CSVなどの後続解決用）。
    # 同義・色省略などの表記ゆれ自動まとめは通常画面別名へ出さず、row_maps で照合する。
    if not product_info.get('skipDisplayNameAliases'):
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


def _assert_fresh_preview(c, body, preview):
    preview_fp = stock.text(body.get('previewFingerprint'))
    if not preview_fp:
        raise ValueError('プレビュー結果を指定してください。もう一度読み込み直してください。')
    fp_now = db_fingerprint(c)
    if preview_fp != fp_now or preview.get('previewFingerprint') != fp_now:
        raise stock.StockConflict({
            'error': 'プレビュー後にデータが変わったため、確認し直してください。',
            'code': 'stale_preview',
            'previewFingerprint': preview_fp,
            'currentFingerprint': fp_now,
        })
    return preview_fp


def _reuse_commit_token(c, token):
    existing_token = c.execute(
        'SELECT id,result_json FROM product_ledger_import_runs WHERE commit_token=?', (token,)
    ).fetchone()
    if not existing_token:
        return None
    try:
        result = json.loads(existing_token['result_json'] or '{}')
    except json.JSONDecodeError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    return {**result, 'already': True, 'runId': existing_token['id']}


def commit_opening_stock_preview(c, body, *, db_path=None):
    """段階2確定: 初期在庫のみ。商品・取扱の新規作成ループは走らせない。"""
    stock._require_write(body)
    ensure_schema(c)
    stock_product_merge.migrate_product_merge(c)
    preview = build_opening_stock_preview(c, body)
    token = stock._token(body.get('token') or body.get('commitToken'))
    reused = _reuse_commit_token(c, token)
    if reused is not None:
        return reused
    preview_fp = _assert_fresh_preview(c, body, preview)

    aux_uploaded = decode_aux_upload(body)
    if not aux_uploaded:
        raise ValueError('初期在庫CSVを選んでください。')
    opening_stock = preview.get('openingStock') or {}
    opening_plans = list(opening_stock.get('plans') or [])
    opening_selections = (
        body.get('openingSelections') if isinstance(body.get('openingSelections'), dict) else {}
    )
    opening_commitable = False
    for p in opening_plans:
        st = p.get('status')
        if st in (legacy.STATUS_CREATE, legacy.STATUS_REUSE):
            opening_commitable = True
            break
        if st == legacy.STATUS_QTY_DIFF:
            sel = opening_selections.get(str(p['rowNo'])) or opening_selections.get(p['rowNo']) or {}
            if sel.get('updateQty'):
                opening_commitable = True
                break
    if not opening_commitable and not body.get('allowEmptyCommit'):
        raise ValueError(
            '登録できる初期在庫行がありません。'
            '曖昧・未登録・削除済みの行は理由を確認し、商品登録や補完を先に行ってください。'
        )
    if not body.get('basisDate'):
        raise ValueError('初期在庫を登録するには在庫基準日を指定してください。')
    basis_date = stock.parse_date(body.get('basisDate'), '在庫基準日')
    if opening_stock.get('basisDateError'):
        raise ValueError(opening_stock['basisDateError'])

    before_stock = stock_snapshot(c)
    backup_path = ''
    if db_path:
        try:
            backup_path = str(stock.backup_sqlite(db_path, tag='pre-legacy-csv-opening') or '')
        except stock.BackupError:
            raise
        except Exception as e:
            print(
                f'opening_stock backup failed before register: {type(e).__name__}: {e}',
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
            aux_uploaded['fileSha256'], aux_uploaded['fileName'], aux_uploaded['fileSize'],
            len(opening_plans),
            actor, stock.text(body.get('previewAt')) or t0, preview_fp, token, t0, backup_path,
        ),
    )
    run_id = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    result_counts = {
        'customersCreated': 0, 'suppliersCreated': 0, 'productsCreated': 0,
        'sizesAdded': 0, 'handlesCreated': 0, 'subSuppliersAdded': 0,
        'productsMerged': 0, 'reused': 0, 'unchanged': 0,
        'reviewed': 0, 'excluded': 0, 'deferred': 0,
        'committedRows': 0, 'failed': 0,
        'openingCreated': 0, 'openingQtyUpdated': 0, 'openingUnchanged': 0,
        'openingHeld': 0, 'openingComplementSkipped': 0,
    }
    failures = []
    product_id_by_code = {}
    for m in c.execute(
        '''SELECT csv_product_code, product_id FROM product_ledger_row_maps
           WHERE product_id IS NOT NULL'''
    ):
        code_t = stock.text(m['csv_product_code'] or '')
        if code_t and code_t not in product_id_by_code:
            product_id_by_code[code_t] = m['product_id']

    def handle_resolver(cust_id, product_id, embroidery, prefer_id):
        info = {
            'id': prefer_id,
            'action': 'reuse' if prefer_id else 'create',
            'embroidery': embroidery or PENDING_EMBROIDERY,
            'proposedEmbroidery': embroidery or '',
            'fromParenClassification': bool(
                embroidery and embroidery != PENDING_EMBROIDERY
            ),
        }
        cp_id, created_h = _ensure_handle(c, cust_id, product_id, info)
        if created_h:
            result_counts['handlesCreated'] += 1
        return cp_id

    try:
        opening_counts = bulk.commit_opening_stock_plans(
            c,
            body=body,
            aux_uploaded=aux_uploaded,
            plans=opening_plans,
            run_id=run_id,
            product_id_by_code=product_id_by_code,
            handle_resolver=handle_resolver,
            actor=actor,
            basis_date=basis_date,
        )
        result_counts['openingCreated'] = opening_counts.get('openingCreated', 0)
        result_counts['openingQtyUpdated'] = opening_counts.get('qtyUpdated', 0)
        result_counts['openingUnchanged'] = opening_counts.get('unchanged', 0)
        result_counts['openingHeld'] = opening_counts.get('held', 0)
        result_counts['openingComplementSkipped'] = opening_counts.get('complementSkipped', 0)
        after_stock = stock_snapshot(c)
        result = {
            'runId': run_id,
            'already': False,
            'importMode': IMPORT_MODE_OPENING,
            'counts': result_counts,
            'confirmSummary': {
                'registerOpeningStock': True,
                'openingStockRows': len(opening_plans),
                'fromPersistedMap': True,
            },
            'deferredRows': [],
            'failures': failures,
            'backupPath': backup_path,
            'stockSnapshotBefore': before_stock,
            'stockSnapshotAfter': after_stock,
            'stockUnchanged': after_stock == before_stock,
            'registerOpeningStock': True,
            'openingCounts': opening_counts,
            'reviewRemaining': 0,
            'excludedCount': 0,
            'deferredCount': 0,
            'fileName': aux_uploaded['fileName'],
            'fileSha256': aux_uploaded['fileSha256'],
            'actorName': actor,
            'committedAt': stock.stamp(),
            'note': bulk.STAGE2_OPENING_NOTE,
            'timingsMs': preview.get('timingsMs') or {},
        }
        c.execute(
            'UPDATE product_ledger_import_runs SET committed_at=?, result_json=? WHERE id=?',
            (result['committedAt'], json.dumps(result, ensure_ascii=False), run_id),
        )
        return result
    except Exception as e:
        result_counts['failed'] += 1
        failures.append({'rowNo': None, 'error': str(e) or '初期在庫の登録に失敗しました。'})
        raise


def commit_preview(c, body, *, db_path=None):
    stock._require_write(body)
    ensure_schema(c)
    stock_product_merge.migrate_product_merge(c)
    mode = resolve_import_mode(body)
    if mode == IMPORT_MODE_OPENING:
        return commit_opening_stock_preview(c, body, db_path=db_path)

    # プレビューと同じ経路で再分類（確定直前の再チェック）
    preview = build_preview(c, body)
    classified = preview['rows']
    sku_groups = preview.get('skuGroups') or []
    planned = preview.get('plannedCounts') or {}

    token = stock._token(body.get('token') or body.get('commitToken'))
    reused = _reuse_commit_token(c, token)
    if reused is not None:
        return reused

    preview_fp = _assert_fresh_preview(c, body, preview)

    uploaded = decode_upload(body)
    selections = body.get('selections') if isinstance(body.get('selections'), dict) else {}
    sku_decisions = body.get('skuDecisions') if isinstance(body.get('skuDecisions'), dict) else {}

    review_rows = [r for r in classified if r['status'] == STATUS_REVIEW]
    excluded_rows = [r for r in classified if r['status'] == STATUS_EXCLUDED]
    hold_rows = [r for r in classified if r['status'] == STATUS_HOLD]
    user_excluded_rows = [r for r in hold_rows if r.get('userRowExcluded')]
    auto_excluded_rows = [r for r in hold_rows if r.get('autoRowExcluded')]
    registration_excluded_rows = user_excluded_rows + [
        r for r in auto_excluded_rows if not r.get('userRowExcluded')
    ]
    # 登録確定時: 除外状態と登録条件をサーバー側でも再検証（プレビュー改ざん耐性）
    exclusion_keys = _parse_row_exclusions(body)
    for r in classified:
        if r.get('userRowExcluded') or r.get('autoRowExcluded'):
            if r['status'] not in (STATUS_HOLD, STATUS_EXCLUDED):
                raise ValueError(
                    f'行 {r.get("rowNo")} は除外扱いですが状態が不正です。再照合してから確定してください。'
                )
            if r.get('canCommit'):
                raise ValueError(
                    f'行 {r.get("rowNo")} は登録対象外のため確定できません。'
                )
        ident = csv_row_identity(r)
        sk = stable_row_exclusion_key(
            customer=ident['customer'],
            code=ident['code'],
            product_name=ident['productName'],
            size=ident['size'],
            supplier=ident['supplier'],
        )
        if sk in exclusion_keys and not r.get('userRowExcluded'):
            raise ValueError(
                f'行 {r.get("rowNo")} の除外指定がプレビュー結果と一致しません。再照合してください。'
            )
    commit_rows = [
        r for r in classified
        if r['status'] in (STATUS_CREATE, STATUS_REUSE, STATUS_SIZE_ADD, STATUS_MERGE)
        and not r.get('skuDecisionNeeded')
        and not r.get('userRowExcluded')
        and not r.get('autoRowExcluded')
    ]

    # 加工行のみで本体不明のグループは「本体確認残」だが、特定できた加工サービスの確定は止めない
    pending_sku = sum(
        1 for g in sku_groups
        if g.get('needsDecision') and not g.get('decision') and not g.get('garmentBodyReviewOnly')
    )
    if pending_sku:
        raise ValueError(
            f'品番比較で未決定が {pending_sku} 件あります。'
            f'【同じ商品としてまとめる】【別物のため個別に整理する】【今回は保留する】を選んでください。'
        )
    if any(
        r.get('skuDecisionNeeded')
        for r in classified
        if not (r.get('product') or {}).get('processingServiceProduct')
        and not r.get('garmentBodyReviewOnly')
    ):
        raise ValueError(
            '品番比較の未決定行が残っています。'
            'サイズ差だけの行は要確認ではありません。品番ごとの比較で決定してください。'
        )
    # 加工名不足などの真の要確認だけブロック（本体不明でサービス化済みの行は除く）
    blocking_review = [
        r for r in review_rows
        if not (r.get('product') or {}).get('processingServiceProduct')
        and not (
            r.get('garmentBodyReviewOnly')
            and (r.get('processingService') or {}).get('identifiable')
        )
    ]
    if blocking_review and not body.get('allowCommitWithReview'):
        raise ValueError(
            f'要確認が {len(blocking_review)} 件残っています。'
            f'未解決の行を黙って除外せず、候補を選ぶか解決してから確定してください。'
        )
    register_opening = bool(preview.get('registerOpeningStock')) and mode != IMPORT_MODE_PRODUCTS
    opening_stock = preview.get('openingStock') if register_opening else None
    opening_plans = list((opening_stock or {}).get('plans') or [])
    aux_uploaded = decode_aux_upload(body) if register_opening else None
    opening_selections = (
        body.get('openingSelections') if isinstance(body.get('openingSelections'), dict) else {}
    )
    opening_commitable = False
    if register_opening:
        for p in opening_plans:
            st = p.get('status')
            if st in (legacy.STATUS_CREATE, legacy.STATUS_REUSE):
                opening_commitable = True
                break
            if st == legacy.STATUS_QTY_DIFF:
                sel = opening_selections.get(str(p['rowNo'])) or opening_selections.get(p['rowNo']) or {}
                if sel.get('updateQty'):
                    opening_commitable = True
                    break

    if not commit_rows and not opening_commitable and not body.get('allowEmptyCommit'):
        raise ValueError(
            '登録する行がありません。'
            f'要確認 {len(review_rows)} 件、保留 {len(hold_rows)} 件、対象外 {len(excluded_rows)} 件、'
            f'変更なし {sum(1 for r in classified if r["status"] == STATUS_UNCHANGED)} 件です。'
        )

    if register_opening:
        if not body.get('basisDate'):
            raise ValueError('初期在庫を登録するには在庫基準日を指定してください。')
        basis_date = stock.parse_date(body.get('basisDate'), '在庫基準日')
        if (opening_stock or {}).get('basisDateError'):
            raise ValueError(opening_stock['basisDateError'])
    else:
        basis_date = None

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
        'registerOpeningStock': register_opening,
        'openingStockRows': len(opening_plans),
        'importMode': mode,
    }

    before_stock = stock_snapshot(c)
    backup_path = ''
    if db_path:
        # Must succeed before any registration writes. Do not skip on failure.
        tag = 'pre-legacy-csv-bulk' if register_opening else 'pre-product-ledger'
        try:
            backup_path = str(stock.backup_sqlite(db_path, tag=tag) or '')
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
        'userExcludedRows': len(user_excluded_rows),
        'autoExcludedRows': len(auto_excluded_rows),
        'committedRows': 0, 'failed': 0,
        'openingCreated': 0, 'openingQtyUpdated': 0, 'openingUnchanged': 0,
        'openingHeld': 0, 'openingComplementSkipped': 0,
    }
    failures = []
    defer_records = [
        {
            'rowNo': r['rowNo'],
            'code': (r.get('csv') or {}).get('code'),
            'productName': (r.get('csv') or {}).get('productName'),
            'reason': r.get('deferReason') or '',
            'csv': r.get('csv'),
            'userRowExcluded': bool(r.get('userRowExcluded')),
            'autoRowExcluded': bool(r.get('autoRowExcluded')),
            'exclusionKind': r.get('exclusionKind') or (
                'auto' if r.get('autoRowExcluded') else ('manual' if r.get('userRowExcluded') else '')
            ),
        }
        for r in hold_rows
    ]

    try:
        # 除外行は商品・加工・サイズ・取扱を作らず、判断だけ永続化（初期在庫で表示用）
        for ex_row in registration_excluded_rows:
            upsert_exclusion_row_map(
                c,
                run_id=run_id,
                file_sha256=uploaded['fileSha256'],
                file_name=uploaded['fileName'],
                row=ex_row,
            )

        merge_results = _run_confirmed_product_merges(
            c, classified, sku_decisions, actor_name=actor, token_prefix=token,
        )
        result_counts['productsMerged'] = sum(
            len(m.get('mergedProductIds') or []) for m in merge_results
        )

        # 同一通常品番の新規はコミット内で1商品にまとめる
        created_by_code = {}
        product_id_by_code = {}
        # 加工サービスは先に一意キーで確保し、本体コミットから再利用
        processing_plans = list(preview.get('processingServicePlans') or [])
        processing_ids_by_key = {}
        result_counts['processingServicesCreated'] = 0
        result_counts['processingServicesReused'] = 0
        for pplan in processing_plans:
            if not pplan.get('autoRegister'):
                continue
            key = stock.text(pplan.get('dedupeKey') or '')
            if not key or key in processing_ids_by_key:
                continue
            ps_id, ps_created = _ensure_processing_service_product(c, pplan)
            processing_ids_by_key[key] = ps_id
            if ps_created:
                result_counts['processingServicesCreated'] += 1
                result_counts['productsCreated'] += 1
            else:
                result_counts['processingServicesReused'] += 1

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
                csv_supplier = stock.text(row['csv'].get('supplier') or '')
                # 加工業者は本体商品の通常仕入先／サブにしない（サービス商品側で確保済み）
                link_supplier = csv_supplier and not proc_svc.is_processing_vendor(csv_supplier)
                if link_supplier:
                    sup_id, created = _ensure_person(
                        c, 'suppliers', stock.save_supplier, csv_supplier,
                        create=row['supplier']['action'] == 'create',
                    )
                    if created:
                        result_counts['suppliersCreated'] += 1
                elif csv_supplier and proc_svc.is_processing_vendor(csv_supplier):
                    # 業者マスタ自体は作成（サービス確保で済んでいることが多い）
                    _ensure_person(
                        c, 'suppliers', stock.save_supplier, csv_supplier, create=True,
                    )

                sel = selections.get(str(row_no)) or selections.get(row_no) or {}
                prod_info = dict(row['product'] or {})
                # 通常仕入先名が行の仕入先と違う場合でも先に存在させる（補助CSV由来の新規は作らない＝台帳候補内のみ）
                primary_name = stock.text(prod_info.get('primarySupplierName') or '')
                if primary_name and proc_svc.is_processing_vendor(primary_name):
                    primary_name = ''
                    prod_info['primarySupplierName'] = ''
                if primary_name and primary_name != csv_supplier:
                    _ensure_person(
                        c, 'suppliers', stock.save_supplier, primary_name, create=True,
                    )
                # 加工サービス商品行: 既に確保した ID を再利用
                ps_key = stock.text(row.get('processingServicePlanKey') or '')
                if prod_info.get('processingServiceProduct') and ps_key in processing_ids_by_key:
                    prod_info['id'] = processing_ids_by_key[ps_key]
                    prod_info['action'] = 'reuse'
                    prod_info['code'] = ''
                    prod_info['codeOverride'] = ''
                    prod_info['blankSku'] = True
                    prod_info['category'] = stock.CATEGORY_PROCESS_SERVICE
                code_t = stock.text(prod_info.get('codeOverride') if prod_info.get('codeOverride') is not None else row['csv']['code'])
                if prod_info.get('processingServiceProduct'):
                    code_t = ''
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

                # 加工業者仕入先を本体へ渡さない
                ensure_sup_id = None if (
                    prod_info.get('processingServiceSplit') and not prod_info.get('processingServiceProduct')
                ) else sup_id
                if prod_info.get('processingServiceProduct'):
                    ensure_sup_id = None  # ID 再利用時は仕入先変更しない

                pid, created, size_added, sub_added = _ensure_product(
                    c, prod_info, ensure_sup_id, row['csv']['productName'],
                    customer_id=cust_id,
                    force_create=force,
                )
                if created and stock_product_merge.is_normal_product_code(code_t):
                    created_by_code[code_t] = pid
                if code_t and pid:
                    product_id_by_code[code_t] = pid
                if created:
                    result_counts['productsCreated'] += 1
                if size_added:
                    result_counts['sizesAdded'] += 1
                if sub_added:
                    result_counts['subSuppliersAdded'] += 1
                if not created and not size_added and not sub_added:
                    result_counts['reused'] += 1

                handle_info = dict(row.get('handle') or {})
                cp_id = None
                if handle_info.get('skipForProcessingService') or handle_info.get('action') == 'excluded':
                    cp_id = None
                else:
                    if sel.get('handleId'):
                        handle_info['id'] = sel['handleId']
                        handle_info['action'] = 'reuse'
                    elif row.get('handle', {}).get('id'):
                        handle_info['id'] = row['handle']['id']
                        handle_info['action'] = 'reuse'
                    cp_id, created_h = _ensure_handle(c, cust_id, pid, handle_info)
                    if created_h:
                        result_counts['handlesCreated'] += 1

                # 加工サービス ID を provenance へ
                if ps_key and ps_key in processing_ids_by_key:
                    prov = dict(row.get('specProvenance') or {})
                    prov['processingProductId'] = processing_ids_by_key[ps_key]
                    if not prov.get('rowKind'):
                        prov['rowKind'] = (
                            'processing_service'
                            if prod_info.get('processingServiceProduct')
                            else 'processing_service_link'
                        )
                    row['specProvenance'] = prov

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

        opening_counts = {}
        if register_opening and aux_uploaded and opening_plans:
            for m in c.execute(
                '''SELECT csv_product_code, product_id FROM product_ledger_row_maps
                   WHERE run_id=? AND product_id IS NOT NULL''',
                (run_id,),
            ):
                code_t = stock.text(m['csv_product_code'] or '')
                if code_t and code_t not in product_id_by_code:
                    product_id_by_code[code_t] = m['product_id']

            def handle_resolver(cust_id, product_id, embroidery, prefer_id):
                info = {
                    'id': prefer_id,
                    'action': 'reuse' if prefer_id else 'create',
                    'embroidery': embroidery or PENDING_EMBROIDERY,
                    'proposedEmbroidery': embroidery or '',
                    'fromParenClassification': bool(
                        embroidery and embroidery != PENDING_EMBROIDERY
                    ),
                }
                cp_id, created_h = _ensure_handle(c, cust_id, product_id, info)
                if created_h:
                    result_counts['handlesCreated'] += 1
                return cp_id

            try:
                opening_counts = bulk.commit_opening_stock_plans(
                    c,
                    body=body,
                    aux_uploaded=aux_uploaded,
                    plans=opening_plans,
                    run_id=run_id,
                    product_id_by_code=product_id_by_code,
                    handle_resolver=handle_resolver,
                    actor=actor,
                    basis_date=basis_date,
                )
            except Exception as e:
                result_counts['failed'] += 1
                failures.append({'rowNo': None, 'error': str(e) or '初期在庫の登録に失敗しました。'})
                raise ValueError(
                    f'初期在庫登録で失敗したため、台帳も含めすべて取り消します: {e}'
                ) from e
            result_counts['openingCreated'] = opening_counts.get('openingCreated', 0)
            result_counts['openingQtyUpdated'] = opening_counts.get('qtyUpdated', 0)
            result_counts['openingUnchanged'] = opening_counts.get('unchanged', 0)
            result_counts['openingHeld'] = opening_counts.get('held', 0)
            result_counts['openingComplementSkipped'] = opening_counts.get('complementSkipped', 0)

        after_stock = stock_snapshot(c)
        if not register_opening and after_stock != before_stock:
            raise RuntimeError(
                '在庫数量・金額・履歴が変化したため取込を中止しました。'
                f' before={before_stock} after={after_stock}'
            )

        result = {
            'runId': run_id,
            'already': False,
            'importMode': mode,
            'counts': result_counts,
            'confirmSummary': confirm_summary,
            'deferredRows': defer_records,
            'failures': failures,
            'backupPath': backup_path,
            'stockSnapshotBefore': before_stock,
            'stockSnapshotAfter': after_stock,
            'stockUnchanged': after_stock == before_stock,
            'registerOpeningStock': register_opening,
            'openingCounts': opening_counts,
            'reviewRemaining': len(review_rows),
            'excludedCount': len(excluded_rows),
            'deferredCount': len(hold_rows),
            'fileName': uploaded['fileName'],
            'fileSha256': uploaded['fileSha256'],
            'actorName': actor,
            'committedAt': stock.stamp(),
            'note': IMPORT_NOTE,
            'timingsMs': preview.get('timingsMs') or {},
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
    mode = resolve_import_mode(body)
    if mode == IMPORT_MODE_OPENING:
        aux = decode_aux_upload(body)
        if not aux:
            raise ValueError('初期在庫CSVを選んでください。')
        return {
            'fileName': aux['fileName'],
            'fileSha256': aux['fileSha256'],
            'previousImports': previous_imports_for_file(c, aux['fileSha256']),
            'importMode': IMPORT_MODE_OPENING,
        }
    uploaded = decode_upload(body)
    return {
        'fileName': uploaded['fileName'],
        'fileSha256': uploaded['fileSha256'],
        'previousImports': previous_imports_for_file(c, uploaded['fileSha256']),
        'importMode': mode,
    }
