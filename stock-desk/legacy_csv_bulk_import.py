"""旧CSV一括取り込み: 商品台帳の確定結果へ初期在庫CSV行を対応づけてプレビュー／登録する。

判定エンジンは product_ledger_import をそのまま使う。ここでは初期在庫CSV行を
「元CSV行→商品・取扱・サイズ・仕様」対応で載せ、在庫数量の登録計画だけを組み立てる。
商品名の改名だけで再照合しない。

二段階取込の「初期在庫を登録」では、商品登録時に永続化した
product_ledger_row_maps から登録先を解決する（メイン名選定のやり直しはしない）。
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict

import legacy_csv_import as legacy
import product_ledger_position as position_sku
import stock

BULK_OPENING_NOTE = '旧CSV一括取り込み（初期在庫）'
STAGE2_OPENING_NOTE = '旧CSV一括取り込み（初期在庫・段階2）'
COMPLEMENT_STATUS = 'complement'
COMPLEMENT_LABEL = '補完候補'

OPENING_STATUS_ORDER = (
    legacy.STATUS_CREATE,
    legacy.STATUS_REUSE,
    legacy.STATUS_QTY_DIFF,
    legacy.STATUS_UNCHANGED,
    legacy.STATUS_REVIEW,
    legacy.STATUS_HOLD,
    legacy.STATUS_EXCLUDED,
    COMPLEMENT_STATUS,
)

OPENING_STATUS_LABELS = {
    **legacy.STATUS_LABELS,
    COMPLEMENT_STATUS: COMPLEMENT_LABEL,
}


def _names_equivalent_core(a, b):
    """原文名の同一判定。

    core key（本体＋明示色）・取込表記統一に加え、共有の色表記正規化（半角カナ・配色区切り・色辞書）
    でも一致を見る。括弧内外の色位置差・帽子／キャップ等の明示シノニムも吸収する。
    類似度だけでは寄せない。別SKUへは呼び出し側の品番フィルタが守る。
    """
    ta, tb = stock.text(a), stock.text(b)
    if not ta or not tb:
        return False
    if legacy.product_name_core_key(ta) == legacy.product_name_core_key(tb):
        return True
    # 本体キー一致＋色キー一致（片方色なしはここでは同一視しない＝色省略は上位処理）
    ba, ca, _, _ = legacy.product_name_base_and_color(ta)
    bb, cb, _, _ = legacy.product_name_base_and_color(tb)
    if ba and ba == bb and ca == cb:
        return True
    # 半角カナ等: classify と同じ unify（SKU 全体 NFKC はしない）でも照合
    import product_ledger_paren as paren_classify
    ua = paren_classify.unify_import_notation(ta)
    ub = paren_classify.unify_import_notation(tb)
    if ua and ub and ua == ub:
        return True
    # 色別名・配色区切りゆれ（ﾐｽﾄﾊﾞｲｵﾚｯﾄﾞ*ﾈｲﾋﾞｰ ↔ バイオレット×ネイビー）
    na = paren_classify.normalize_name_for_color_compare(ta)
    nb = paren_classify.normalize_name_for_color_compare(tb)
    return bool(na and nb and na == nb)


def _aux_item_payload(row_no, item):
    size = item.get('サイズ')
    size = size if isinstance(size, str) else ('' if size is None else str(size))
    return {
        'rowNo': row_no,
        'code': stock.text(item.get('商品コード')),
        'supplier': stock.text(item.get('仕入先')),
        'customer': stock.text(item.get('顧客')),
        'productName': stock.text(item.get('商品名')),
        'size': size.strip(),
        'conditionRaw': stock.text(item.get('新古')),
        'site': stock.text(item.get('拠点')),
        'shelf': stock.text(item.get('棚')),
        'unitCostRaw': stock.text(item.get('仕入単価')),
        'qtyRaw': stock.text(item.get('在庫')),
    }


def collect_matched_aux_bindings(sku_groups):
    """判定時に紐づけ済みの初期在庫CSV行を、行番号一意で集める（別名二重計上しない）。"""
    by_row = {}
    for group in sku_groups or []:
        code = stock.text(group.get('code') or '')
        aux = group.get('auxPrimary') or {}
        for judgment in aux.get('supplierJudgments') or []:
            matched_supplier = stock.text(judgment.get('supplier') or '')
            ledger_names = list(judgment.get('ledgerNames') or [])
            customers = list(judgment.get('customers') or [])
            for hit in judgment.get('matchedAuxRows') or []:
                row_no = hit.get('rowNo')
                if row_no is None or row_no in by_row:
                    continue
                by_row[row_no] = {
                    'rowNo': row_no,
                    'code': code,
                    'matchedSupplier': matched_supplier,
                    'ledgerNames': ledger_names,
                    'customers': customers,
                    'aux': hit,
                    'group': group,
                }
    return by_row


def _ledger_rows_for_aux(classified, *, code, product_name, supplier, customer):
    """台帳側の確定行のうち、初期在庫行の原文キーに対応するものを探す（改名後名では探さない）。"""
    hits = []
    for row in classified or []:
        csv = row.get('csv') or {}
        if stock.text(csv.get('code') or '') != stock.text(code):
            continue
        if not _names_equivalent_core(csv.get('productName'), product_name):
            # コア一致しない場合でも、nameMappings の原文側なら対応
            mappings = (row.get('product') or {}).get('nameMappings') or []
            mapped = any(
                _names_equivalent_core(m.get('originalName'), product_name)
                for m in mappings
                if isinstance(m, dict)
            )
            if not mapped:
                continue
        csv_sup = stock.text(csv.get('supplier') or '')
        if supplier and csv_sup:
            if (
                legacy.identity_match_key(supplier) != legacy.identity_match_key(csv_sup)
                and not legacy.looks_like_company_form_variant(supplier, csv_sup)
            ):
                continue
        csv_cust = stock.text(csv.get('customer') or '')
        if customer and csv_cust:
            if (
                legacy.identity_match_key(customer) != legacy.identity_match_key(csv_cust)
                and not legacy.looks_like_company_form_variant(customer, csv_cust)
            ):
                continue
        hits.append(row)
    return hits


def _group_deferred(group):
    """品番グループが保留／未決定なら、紐づく初期在庫も片方だけ登録しない。"""
    if not group:
        return True, '対応する商品台帳側が未確定のため保留'
    decision = group.get('decision') or {}
    decided = stock.text(decision.get('decision') or '')
    if decided == 'hold' or group.get('deferred'):
        reason = stock.text(decision.get('deferReason') or '') or '商品台帳側が保留のため初期在庫も保留'
        return True, reason
    if group.get('needsDecision') and not decided:
        return True, '商品台帳側の品番比較が未決定のため初期在庫も保留'
    return False, ''


def _resolve_embroidery_for_aux(ledger_rows, aux_name, adopted_package):
    """非メイン仕様をメイン仕様へ書き換えない。対応台帳行の provenance / 取扱を優先。"""
    for row in ledger_rows:
        prov = row.get('specProvenance') or {}
        orig = prov.get('originalName') or (row.get('csv') or {}).get('productName')
        if _names_equivalent_core(orig, aux_name):
            if not prov.get('isMainCandidate', True):
                emb = stock.text(prov.get('processing') or '') or stock.text(
                    (row.get('handle') or {}).get('provenanceEmbroidery') or ''
                )
                if emb:
                    return emb, False
            emb = stock.text((row.get('handle') or {}).get('embroidery') or '')
            if emb and emb != legacy.PENDING_EMBROIDERY:
                return emb, True
            emb = stock.text(prov.get('processing') or '')
            if emb:
                return emb, True
    pkg = adopted_package or {}
    return stock.text(pkg.get('processing') or '') or legacy.PENDING_EMBROIDERY, True


def _planned_product_label(group, ledger_rows):
    plan = (group or {}).get('productPlan') or {}
    name = stock.text(plan.get('productName') or '')
    if not name and ledger_rows:
        prod = ledger_rows[0].get('product') or {}
        name = stock.text(prod.get('officialName') or prod.get('name') or '')
    if not name:
        aux = (group or {}).get('auxPrimary') or {}
        name = stock.text(aux.get('suggestedOfficialName') or '')
    return name


def build_opening_stock_plans(
    c,
    *,
    classified,
    sku_groups,
    aux_uploaded,
    default_site_name='',
    opening_selections=None,
):
    """初期在庫CSVの各行について登録予定を返す（DBは変更しない）。"""
    if not aux_uploaded:
        return {
            'enabled': False,
            'plans': [],
            'counts': {},
            'complementCandidates': [],
            'mastersToCreate': {'customers': [], 'suppliers': [], 'sites': [], 'shelves': []},
            'note': '',
        }

    default_site = stock.text(default_site_name) or stock.UNCONFIRMED_SITE_NAME
    selections = opening_selections if isinstance(opening_selections, dict) else {}
    bindings = collect_matched_aux_bindings(sku_groups)
    aux_rows = list(aux_uploaded.get('rows') or [])
    seen_aux = set(bindings.keys())

    plans = []
    complement = []
    masters = {
        'customers': set(),
        'suppliers': set(),
        'sites': set(),
        'shelves': set(),
    }

    # マッチ済み行
    for row_no, bind in sorted(bindings.items(), key=lambda x: x[0]):
        aux = bind['aux']
        group = bind['group']
        item = {
            '商品コード': aux.get('code') or bind['code'],
            '仕入先': aux.get('supplier') or bind['matchedSupplier'],
            '顧客': aux.get('customer') or '',
            '商品名': aux.get('productName') or '',
            'サイズ': aux.get('size') or '',
            '新古': aux.get('conditionRaw') or '',
            '拠点': aux.get('site') or '',
            '棚': aux.get('shelf') or '',
            '仕入単価': aux.get('unitCostRaw') or '',
            '在庫': aux.get('qtyRaw') if aux.get('qtyRaw') is not None else aux.get('qty'),
        }
        plan = _plan_one_opening_row(
            c,
            row_no=row_no,
            item=item,
            group=group,
            classified=classified,
            default_site=default_site,
            selection=selections.get(str(row_no)) or selections.get(row_no) or {},
            matched=True,
        )
        plans.append(plan)
        _collect_master_additions(plan, masters)

    # 未マッチ行 → 補完候補 or 対象外
    for row_no, item in aux_rows:
        if row_no in seen_aux:
            continue
        product_name = stock.text(item.get('商品名'))
        qty, cost, qreasons = legacy.parse_row_qty_cost(item, row_no)
        if not product_name and qty == 0 and not qreasons:
            plans.append({
                'rowNo': row_no,
                'status': legacy.STATUS_EXCLUDED,
                'statusLabel': OPENING_STATUS_LABELS[legacy.STATUS_EXCLUDED],
                'qty': qty,
                'unitCost': cost,
                'condition': None,
                'conditionLabel': '',
                'reasons': [f'{row_no}行目：商品名が空欄で在庫が0のため対象外です（登録しません）。'],
                'csv': _aux_item_payload(row_no, item),
                'target': None,
                'place': legacy.location_resolve(c, item.get('拠点'), item.get('棚'), default_site),
                'stock': None,
                'matchedLedger': False,
                'complement': False,
                'heldWithProduct': False,
            })
            continue
        payload = _aux_item_payload(row_no, item)
        place = legacy.location_resolve(c, item.get('拠点'), item.get('棚'), default_site)
        condition, creasons = legacy.parse_row_condition(item, row_no)
        reasons = [
            f'{row_no}行目：商品台帳CSVに対応する行がなく、初期在庫CSVのみに存在する補完候補です（自動では登録しません）。',
            *creasons,
            *qreasons,
        ]
        plan = {
            'rowNo': row_no,
            'status': COMPLEMENT_STATUS,
            'statusLabel': COMPLEMENT_LABEL,
            'qty': qty,
            'unitCost': cost,
            'condition': condition,
            'conditionLabel': stock.condition_label(condition) if condition else '',
            'reasons': reasons,
            'csv': payload,
            'target': {
                'productId': None,
                'productName': product_name,
                'productCode': stock.text(item.get('商品コード')),
                'officialName': '',
                'customer': stock.text(item.get('顧客')),
                'supplier': stock.text(item.get('仕入先')),
                'size': payload['size'],
                'embroidery': legacy.PENDING_EMBROIDERY,
                'isMainSpec': False,
                'fromOriginalName': product_name,
            },
            'place': place,
            'stock': None,
            'matchedLedger': False,
            'complement': True,
            'heldWithProduct': False,
            'selected': False,
        }
        plans.append(plan)
        complement.append(plan)
        _collect_master_additions(plan, masters)

    counts = defaultdict(int)
    for p in plans:
        counts[p['status']] += 1

    return {
        'enabled': True,
        'willRegisterOpeningStock': True,
        'fileName': aux_uploaded.get('fileName') or '',
        'fileSha256': aux_uploaded.get('fileSha256') or '',
        'encoding': aux_uploaded.get('encoding') or '',
        'rowCount': len(aux_rows),
        'plans': plans,
        'counts': dict(counts),
        'complementCandidates': complement,
        'mastersToCreate': {
            'customers': sorted(masters['customers']),
            'suppliers': sorted(masters['suppliers']),
            'sites': sorted(masters['sites']),
            'shelves': sorted(masters['shelves']),
        },
        'defaultSiteName': default_site,
        'ignoredQtyColumns': list(legacy.IGNORED_QTY_COLUMNS),
        'note': (
            '初期在庫CSVを選んでいるため、確認後に初期在庫も登録します。'
            '数量は「在庫」列のみです（補正在庫数・購入数量・出荷数は使いません）。'
            '0・マイナスもそのまま登録します。商品台帳側で保留した品番の在庫は同時に保留します。'
            '対応は元CSV行→確定した商品・取扱・サイズ・仕様です（改名後の商品名だけでは再照合しません）。'
        ),
    }


def _collect_master_additions(plan, masters):
    csv = plan.get('csv') or {}
    target = plan.get('target') or {}
    place = plan.get('place') or {}
    if plan.get('status') in (legacy.STATUS_HOLD, legacy.STATUS_EXCLUDED, COMPLEMENT_STATUS):
        # 補完候補は予定追加として見せる
        if plan.get('status') == COMPLEMENT_STATUS:
            if csv.get('customer'):
                masters['customers'].add(csv['customer'])
            if csv.get('supplier'):
                masters['suppliers'].add(csv['supplier'])
            if place.get('siteExists') is False and place.get('siteName'):
                masters['sites'].add(place['siteName'])
            if place.get('shelfName') and place.get('shelfExists') is False:
                masters['shelves'].add(f"{place.get('siteName')}/{place['shelfName']}")
        return
    cust = target.get('customer') or csv.get('customer')
    if cust and (target.get('customerAction') == 'create' or not target.get('customerId')):
        # preview: unknown id → show as planned addition when person preview says create
        pass
    if place.get('action') == 'create_site' or place.get('siteExists') is False:
        if place.get('siteName'):
            masters['sites'].add(place['siteName'])
    if place.get('action') == 'create_shelf' or (
        place.get('shelfName') and place.get('shelfExists') is False
    ):
        if place.get('shelfName'):
            masters['shelves'].add(f"{place.get('siteName')}/{place['shelfName']}")


def _plan_one_opening_row(
    c, *, row_no, item, group, classified, default_site, selection, matched,
):
    payload = _aux_item_payload(row_no, item)
    qty, cost, qreasons = legacy.parse_row_qty_cost(item, row_no)
    condition, creasons = legacy.parse_row_condition(item, row_no)
    place = legacy.location_resolve(c, item.get('拠点'), item.get('棚'), default_site)
    if place.get('siteEmpty'):
        place = dict(place)
        place['usedDefaultSite'] = True
    if place.get('siteExists') is False:
        place['action'] = 'create_site'
    elif place.get('siteActive') is False:
        place['action'] = 'review'
    else:
        place['action'] = 'reuse_site'
    if place.get('shelfName'):
        if place.get('shelfExists') is False and place.get('action') != 'review':
            place['action'] = 'create_shelf'
        elif place.get('shelfActive') is False:
            place['action'] = 'review'

    ledger_rows = _ledger_rows_for_aux(
        classified,
        code=payload['code'] or stock.text(group.get('code') or ''),
        product_name=payload['productName'],
        supplier=payload['supplier'],
        customer=payload['customer'],
    )
    deferred, defer_reason = _group_deferred(group)
    # 台帳行が保留なら在庫も保留
    if any(r.get('status') == legacy.STATUS_HOLD for r in ledger_rows):
        deferred, defer_reason = True, '対応する商品台帳行が保留のため初期在庫も保留'
    if any(r.get('status') == legacy.STATUS_REVIEW or r.get('skuDecisionNeeded') for r in ledger_rows):
        if not deferred:
            deferred, defer_reason = True, '対応する商品台帳行が要確認のため初期在庫も保留'

    adopted = (group.get('auxPrimary') or {}).get('adoptedPackage') or group.get('adoptedPackage') or {}
    embroidery, is_main_spec = _resolve_embroidery_for_aux(
        ledger_rows, payload['productName'], adopted,
    )
    official_name = _planned_product_label(group, ledger_rows)
    product_id = None
    customer_id = None
    handle_id = None
    customer_action = 'create'
    if ledger_rows:
        prod = ledger_rows[0].get('product') or {}
        product_id = prod.get('id')
        cust = ledger_rows[0].get('customer') or {}
        customer_id = cust.get('id')
        customer_action = cust.get('action') or ('reuse' if customer_id else 'create')
        handle_id = (ledger_rows[0].get('handle') or {}).get('id')
    if not product_id and int(group.get('existingCount') or 0) == 1:
        product_id = (group.get('existingProducts') or [{}])[0].get('id')

    # 顧客・仕入先の予定
    cust_preview = legacy._person_preview(c, 'customers', '顧客', payload['customer'], row_no)
    sup_preview = legacy._person_preview(c, 'suppliers', '仕入先', payload['supplier'], row_no)
    if cust_preview.get('id'):
        customer_id = cust_preview['id']
        customer_action = cust_preview.get('action') or 'reuse'

    reasons = list(creasons) + list(qreasons)
    prior = None
    stock_info = None
    if place.get('action') == 'review':
        reasons.append(f'{row_no}行目：拠点または棚が停止中です。')
    if deferred:
        status = legacy.STATUS_HOLD
        reasons = [defer_reason, *reasons]
    elif reasons or condition is None or qty is None or cost is None:
        status = legacy.STATUS_REVIEW
    else:
        # 既存在庫照合（取扱IDがある場合のみ。未作成なら新規扱い）
        stock_info = legacy._stock_match(
            c, handle_id, payload['size'], place.get('locationId'), condition, cost,
        )
        prior = legacy.lookup_stock_map(
            c,
            customer=payload['customer'],
            code=payload['code'],
            product_name=payload['productName'],
            size=payload['size'],
            condition=condition,
            unit_cost=cost,
            site=payload['site'],
            shelf=payload['shelf'],
        )

        if stock_info and stock_info.get('exists'):
            if stock_info['currentQty'] == qty:
                status = legacy.STATUS_UNCHANGED
            else:
                status = legacy.STATUS_QTY_DIFF
                reasons.append(
                    f'{row_no}行目：既存在庫 {stock_info["currentQty"]} とCSV数量 {qty} が異なります。'
                    '明示選択したときだけ更新します（黙って上書きしません）。'
                )
        elif prior and prior.get('lot_ids_json'):
            # マップはあるがロットが消えた等 → 要確認
            status = legacy.STATUS_REVIEW
            reasons.append(f'{row_no}行目：以前取り込んだ行の記録があります。内容を確認してください。')
        else:
            master_new = customer_action == 'create' or not product_id or place.get('action') in (
                'create_site', 'create_shelf',
            )
            status = legacy.STATUS_CREATE if master_new else legacy.STATUS_REUSE

    sel = selection if isinstance(selection, dict) else {}
    return {
        'rowNo': row_no,
        'status': status,
        'statusLabel': OPENING_STATUS_LABELS.get(status, status),
        'qty': qty,
        'unitCost': cost,
        'condition': condition,
        'conditionLabel': stock.condition_label(condition) if condition else '',
        'reasons': reasons,
        'csv': payload,
        'target': {
            'productId': product_id,
            'productCode': payload['code'] or stock.text(group.get('code') or ''),
            'productName': official_name or payload['productName'],
            'officialName': official_name,
            'fromOriginalName': payload['productName'],
            'customer': payload['customer'],
            'customerId': customer_id,
            'customerAction': customer_action,
            'supplier': payload['supplier'],
            'supplierId': sup_preview.get('id'),
            'supplierAction': sup_preview.get('action'),
            'size': payload['size'],
            'embroidery': embroidery,
            'handleId': handle_id,
            'isMainSpec': bool(is_main_spec),
            'color': stock.text(adopted.get('color') or '') if is_main_spec else stock.text(
                ((ledger_rows[0].get('specProvenance') or {}) if ledger_rows else {}).get('color') or ''
            ),
            'processing': embroidery if embroidery != legacy.PENDING_EMBROIDERY else '',
            'material': stock.text(adopted.get('material') or '') if is_main_spec else '',
        },
        'place': place,
        'stock': stock_info,
        'matchedLedger': matched,
        'complement': False,
        'heldWithProduct': status == legacy.STATUS_HOLD,
        'selected': bool(sel.get('updateQty')) if status == legacy.STATUS_QTY_DIFF else False,
        'priorImport': bool(prior) if status != legacy.STATUS_HOLD else False,
    }


def commit_opening_stock_plans(
    c,
    *,
    body,
    aux_uploaded,
    plans,
    run_id,
    product_id_by_code,
    handle_resolver,
    actor,
    basis_date,
):
    """確定済みの初期在庫計画を登録。失敗時は例外を投げて呼び出し側でロールバックする。"""
    if not plans:
        return {
            'openingCreated': 0,
            'qtyUpdated': 0,
            'unchanged': 0,
            'held': 0,
            'excluded': 0,
            'complementSkipped': 0,
            'failed': 0,
        }

    selections = body.get('openingSelections') if isinstance(body.get('openingSelections'), dict) else {}
    selected_qty = set()
    for key, val in selections.items():
        if isinstance(val, dict) and val.get('updateQty'):
            try:
                selected_qty.add(int(key))
            except (TypeError, ValueError):
                pass

    counts = {
        'openingCreated': 0,
        'qtyUpdated': 0,
        'unchanged': 0,
        'held': 0,
        'excluded': 0,
        'complementSkipped': 0,
        'reviewedSkipped': 0,
        'failed': 0,
    }
    default_site = stock.text(body.get('defaultSiteName')) or stock.UNCONFIRMED_SITE_NAME
    basis = basis_date

    for plan in plans:
        row_no = plan['rowNo']
        status = plan['status']
        if status == legacy.STATUS_HOLD:
            counts['held'] += 1
            continue
        if status == legacy.STATUS_EXCLUDED:
            counts['excluded'] += 1
            continue
        if status == COMPLEMENT_STATUS:
            counts['complementSkipped'] += 1
            continue
        if status == legacy.STATUS_REVIEW:
            counts['reviewedSkipped'] += 1
            continue
        if status == legacy.STATUS_UNCHANGED:
            counts['unchanged'] += 1
            _upsert_opening_map(c, run_id=run_id, aux_uploaded=aux_uploaded, plan=plan, lot_ids=(plan.get('stock') or {}).get('lotIds') or [])
            continue
        if status == legacy.STATUS_QTY_DIFF:
            if row_no not in selected_qty:
                counts['reviewedSkipped'] += 1
                continue
            stock_info = plan.get('stock') or {}
            stock.adjust_opening_qty_diff(c, {
                '_canWrite': True,
                '_actor': body.get('_actor'),
                'lotIds': stock_info.get('lotIds') or [],
                'targetQty': plan['qty'],
                'fingerprint': stock_info.get('fingerprint') or '',
                'reason': f'{BULK_OPENING_NOTE}（数量差異・初期在庫CSV {row_no}行目）',
                'businessOn': basis,
                'actorName': actor,
            })
            counts['qtyUpdated'] += 1
            _upsert_opening_map(
                c, run_id=run_id, aux_uploaded=aux_uploaded, plan=plan,
                lot_ids=stock_info.get('lotIds') or [],
            )
            continue

        if status not in (legacy.STATUS_CREATE, legacy.STATUS_REUSE):
            continue

        target = plan.get('target') or {}
        csv = plan.get('csv') or {}
        cust_id, _ = legacy._ensure_person(
            c, 'customers', stock.save_customer, csv.get('customer') or target.get('customer'),
            create=True,
        )
        code = stock.text(target.get('productCode') or csv.get('code') or '')
        product_id = target.get('productId') or product_id_by_code.get(code)
        if not product_id:
            raise ValueError(
                f'{row_no}行目：対応する商品が台帳側で登録されていません。'
                '商品台帳の確定結果を確認してください。'
            )
        embroidery = stock.text(target.get('embroidery') or '') or legacy.PENDING_EMBROIDERY
        cp_id = handle_resolver(cust_id, product_id, embroidery, target.get('handleId'))
        loc_id, _, _ = legacy._ensure_location(c, plan.get('place') or {
            'siteName': default_site, 'shelfName': '',
        })
        size = stock.text(target.get('size') or csv.get('size') or '')
        if size:
            _ensure_product_size(c, product_id, size)
        condition = plan.get('condition')
        unit_cost = plan.get('unitCost')
        qty = plan.get('qty')

        stock_info = legacy._stock_match(c, cp_id, size, loc_id, condition, unit_cost)
        if stock_info and stock_info.get('exists'):
            if stock_info['currentQty'] == qty:
                counts['unchanged'] += 1
                _upsert_opening_map(
                    c, run_id=run_id, aux_uploaded=aux_uploaded, plan=plan,
                    lot_ids=stock_info.get('lotIds') or [],
                    customer_id=cust_id, product_id=product_id, customer_product_id=cp_id,
                )
                continue
            raise ValueError(
                f'{row_no}行目：在庫が既にあるため新規登録できません。'
                '数量差異として明示選択し直してください。'
            )

        token_src = json.dumps({
            'bulk': True,
            'runId': run_id,
            'rowNo': row_no,
            'sha': aux_uploaded.get('fileSha256'),
            'cp': cp_id,
            'size': size,
            'loc': loc_id,
            'cond': condition,
            'cost': unit_cost,
            'qty': qty,
            'basis': basis,
            'map': legacy.stock_map_key(
                customer=csv.get('customer') or '',
                code=code,
                product_name=csv.get('productName') or '',
                size=size,
                condition=condition,
                unit_cost=unit_cost,
                site=csv.get('site') or '',
                shelf=csv.get('shelf') or '',
            ),
        }, ensure_ascii=False, sort_keys=True)
        open_token = 'legacy-bulk-' + hashlib.sha256(token_src.encode()).hexdigest()[:40]
        note = (
            f'{BULK_OPENING_NOTE}（初期在庫CSV {row_no}行目 / 元品番 {code} '
            f'/ 元商品名 {csv.get("productName") or ""}'
            f' → 登録名 {target.get("officialName") or target.get("productName") or ""}）'
        )
        opening = stock.register_opening(c, {
            'token': open_token,
            'customerProductId': cp_id,
            'locationId': loc_id,
            'basisDate': basis,
            'receivedUnknown': True,
            'receivedOn': '',
            'payoutOrder': row_no,
            'condition': condition,
            'allowZero': True,
            'allowNegative': True,
            'note': note,
            '_actor': body.get('_actor'),
            'actorName': actor,
            'lines': [{'size': size, 'qty': qty, 'unitCost': unit_cost}],
        })
        lot_ids = []
        if isinstance(opening, dict):
            lot_ids = [
                x.get('id') or x.get('lotId')
                for x in (opening.get('lots') or opening.get('lines') or [])
                if isinstance(x, dict) and (x.get('id') or x.get('lotId'))
            ]
        if not lot_ids:
            lot_ids = [
                r['id'] for r in c.execute(
                    '''SELECT id FROM lots
                       WHERE customer_product_id=? AND size_label=? AND location_id=?
                         AND condition=? AND unit_cost=?
                       ORDER BY id DESC''',
                    (cp_id, size, loc_id, condition, unit_cost),
                )
            ]
        _upsert_opening_map(
            c, run_id=run_id, aux_uploaded=aux_uploaded, plan=plan,
            lot_ids=lot_ids, opening_token=open_token,
            customer_id=cust_id, product_id=product_id, customer_product_id=cp_id,
        )
        counts['openingCreated'] += 1

    return counts


def _upsert_opening_map(
    c, *, run_id, aux_uploaded, plan, lot_ids, opening_token='',
    customer_id=None, product_id=None, customer_product_id=None,
):
    """再取込用に legacy_csv_row_maps へ初期在庫行の対応を残す。"""
    csv = plan.get('csv') or {}
    target = plan.get('target') or {}
    size = csv.get('size') or target.get('size') or ''
    row = {
        'rowNo': plan['rowNo'],
        'csv': {
            'customer': csv.get('customer') or '',
            'code': csv.get('code') or target.get('productCode') or '',
            'productName': csv.get('productName') or '',
            'size': size,
            'site': csv.get('site') or '',
            'shelf': csv.get('shelf') or '',
        },
        'product': {'size': size},
        'condition': plan.get('condition'),
        'unitCost': plan.get('unitCost'),
        'qty': plan.get('qty'),
    }
    legacy.upsert_row_map(
        c,
        run_id=run_id,
        file_sha256=aux_uploaded.get('fileSha256') or '',
        file_name=aux_uploaded.get('fileName') or '',
        row=row,
        customer_id=customer_id or target.get('customerId'),
        product_id=product_id or target.get('productId'),
        customer_product_id=customer_product_id or target.get('handleId'),
        opening_token=opening_token,
        lot_ids=lot_ids or [],
    )


def _ensure_product_size(c, product_id, size_label):
    """初期在庫CSVのサイズが商品に無いときだけ追加（名・色は触らない）。"""
    size = stock.text(size_label)
    if not product_id or not size:
        return False
    exists = c.execute(
        'SELECT 1 FROM product_sizes WHERE product_id=? AND label=?',
        (product_id, size),
    ).fetchone()
    if exists:
        return False
    current = stock.product_row(c, product_id)
    labels = list(current['sizes'])
    labels.append(size)
    stock.save_product(c, {
        'id': product_id,
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


def summarize_opening_for_preview(opening):
    """プレビューUI用の短い集計。"""
    if not opening or not opening.get('enabled'):
        return None
    counts = opening.get('counts') or {}
    return {
        'willRegister': True,
        'fileName': opening.get('fileName') or '',
        'rowCount': opening.get('rowCount') or 0,
        'create': counts.get(legacy.STATUS_CREATE, 0),
        'reuse': counts.get(legacy.STATUS_REUSE, 0),
        'qtyDiff': counts.get(legacy.STATUS_QTY_DIFF, 0),
        'unchanged': counts.get(legacy.STATUS_UNCHANGED, 0),
        'needsReview': counts.get(legacy.STATUS_REVIEW, 0),
        'hold': counts.get(legacy.STATUS_HOLD, 0),
        'excluded': counts.get(legacy.STATUS_EXCLUDED, 0),
        'complement': counts.get(COMPLEMENT_STATUS, 0),
        'note': opening.get('note') or '',
    }


def _person_names_match(a, b):
    ta, tb = stock.text(a), stock.text(b)
    if not ta or not tb:
        return False
    if legacy.identity_match_key(ta) == legacy.identity_match_key(tb):
        return True
    return legacy.looks_like_company_form_variant(ta, tb)


def _spec_signature(color, processing, material):
    return '|'.join([
        legacy.identity_match_key(color or ''),
        legacy.identity_match_key(processing or ''),
        legacy.identity_match_key(material or ''),
    ])


def _map_row_spec_full(map_row):
    """色・加工・素材に加え、加工サービス分離・上下品番メタを返す。"""
    color = ''
    processing = ''
    material = ''
    row_kind = ''
    processing_product_id = None
    position = ''
    registered_code = ''
    base_code = ''
    source_code = ''
    if map_row is None:
        return {
            'color': color, 'processing': processing, 'material': material,
            'rowKind': row_kind, 'processingProductId': processing_product_id,
            'position': position, 'registeredCode': registered_code,
            'baseCode': base_code, 'sourceCode': source_code,
        }
    keys = map_row.keys() if hasattr(map_row, 'keys') else []
    if 'csv_color' in keys:
        color = stock.text(map_row['csv_color'] or '')
    if 'csv_processing' in keys:
        processing = stock.text(map_row['csv_processing'] or '')
    if 'csv_material' in keys:
        material = stock.text(map_row['csv_material'] or '')
    raw = ''
    if 'spec_json' in keys:
        raw = map_row['spec_json'] or ''
    spec = {}
    if raw:
        try:
            spec = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            spec = {}
    if isinstance(spec, dict):
        if not (color or processing or material):
            color = stock.text(spec.get('color') or color)
            processing = stock.text(spec.get('processing') or processing)
            material = stock.text(spec.get('material') or material)
        if not processing:
            processing = stock.text(spec.get('processingServiceName') or '')
        row_kind = stock.text(spec.get('rowKind') or '')
        pid = spec.get('processingProductId')
        if pid not in (None, ''):
            try:
                processing_product_id = int(pid)
            except (TypeError, ValueError):
                processing_product_id = None
        position = stock.text(spec.get('position') or '')
        registered_code = stock.text(spec.get('registeredCode') or '')
        base_code = stock.text(spec.get('baseCode') or '')
        source_code = stock.text(spec.get('sourceCode') or '')
        # 旧保存: 補足に上下だけ残っている場合
        if not position:
            supp = stock.text(spec.get('supplement') or '')
            if supp in position_sku.POSITION_TOKENS:
                position = supp
    return {
        'color': color, 'processing': processing, 'material': material,
        'rowKind': row_kind, 'processingProductId': processing_product_id,
        'position': position, 'registeredCode': registered_code,
        'baseCode': base_code, 'sourceCode': source_code,
    }


def _map_row_spec(map_row):
    full = _map_row_spec_full(map_row)
    return full['color'], full['processing'], full['material']


def _map_row_position_meta(map_row):
    """マップ行の上下・登録品番メタ。"""
    code = stock.text(map_row['csv_product_code'] or '')
    name = stock.text(map_row['csv_product_name'] or '')
    full = _map_row_spec_full(map_row)
    pos = stock.text(full.get('position') or '')
    registered = stock.text(full.get('registeredCode') or '')
    base = stock.text(full.get('baseCode') or '')
    source = stock.text(full.get('sourceCode') or '') or code
    if not pos or not registered or not base:
        resolved = position_sku.resolve_garment_position(
            code=code, product_name=name, maker_position=pos,
            override_registered_code=registered,
        )
        pos = pos or stock.text(resolved.get('position') or '')
        registered = registered or stock.text(resolved.get('registeredCode') or code)
        base = base or stock.text(resolved.get('baseCode') or code)
        source = source or stock.text(resolved.get('sourceCode') or code)
    return {
        'position': pos,
        'registeredCode': registered,
        'baseCode': base,
        'sourceCode': source,
        'color': full.get('color') or '',
        'processing': full.get('processing') or '',
        'material': full.get('material') or '',
        'rowKind': full.get('rowKind') or '',
        'processingProductId': full.get('processingProductId'),
    }


def _map_matches_aux_identity(map_row, *, customer, code, product_name, size, supplier):
    """原文キーで対応（改名後の公式名では探さない）。行番号は使わない。

    品番は VEH30B / VEH30B 下 / VEH30B下 の表記差を、上下矛盾がないときだけ許容。
    上下未特定の基礎品番だけでは上・下へ自動振り分けしない。
    """
    map_code = stock.text(map_row['csv_product_code'] or '')
    aux_code = stock.text(code)
    map_name = stock.text(map_row['csv_product_name'] or '')
    meta = _map_row_position_meta(map_row)
    aux_info = position_sku.resolve_garment_position(code=aux_code, product_name=product_name)
    aux_pos = stock.text(aux_info.get('position') or '')
    map_pos = stock.text(meta.get('position') or '')
    aux_reg = stock.text(aux_info.get('registeredCode') or aux_code)
    map_reg = stock.text(meta.get('registeredCode') or map_code)
    aux_base = stock.text(aux_info.get('baseCode') or aux_code)
    map_base = stock.text(meta.get('baseCode') or map_code)

    code_ok = False
    if map_code == aux_code or map_reg == aux_reg:
        code_ok = True
    elif aux_code in position_sku.sku_code_variants(map_code) or map_code in position_sku.sku_code_variants(aux_code):
        code_ok = True
    elif map_base and map_base == aux_base:
        if map_pos and aux_pos:
            code_ok = map_pos == aux_pos
        elif not map_pos and not aux_pos:
            code_ok = True
        else:
            # 片方だけ上下あり → 名コア一致と合わせて後段で判定（ここでは基礎だけでは OK にしない）
            code_ok = False
    if not code_ok:
        # 基礎一致＋商品名から同じ上下が取れるときだけ許可
        if map_base and map_base == aux_base and map_pos and aux_pos and map_pos == aux_pos:
            code_ok = True
    if not code_ok:
        return False
    if map_pos in position_sku.POSITION_TOKENS and aux_pos in position_sku.POSITION_TOKENS:
        if map_pos != aux_pos:
            return False
    if supplier and stock.text(map_row['csv_supplier'] or ''):
        if not _person_names_match(supplier, map_row['csv_supplier']):
            return False
    if customer and stock.text(map_row['csv_customer'] or ''):
        if not _person_names_match(customer, map_row['csv_customer']):
            return False
    if not _names_equivalent_core(map_name, product_name):
        return False
    map_size = stock.text(map_row['csv_size'] or '')
    aux_size = size if isinstance(size, str) else ('' if size is None else str(size))
    aux_size = aux_size.strip()
    # サイズが双方あるときは一致必須（空はワイルドカード扱い）
    if map_size and aux_size and map_size != aux_size:
        return False
    return True


def lookup_ledger_destinations_for_aux(c, *, customer, code, product_name, size, supplier):
    """商品登録で永続化したマップから登録先候補を返す（推測で別商品へ寄せない）。"""
    import product_ledger_import as pli

    pli.ensure_schema(c)
    code_t = stock.text(code)
    if not code_t:
        return {
            'status': 'missing',
            'reason': '商品コードが空のため、商品登録マップから登録先を特定できません。',
            'matches': [],
        }
    variants = position_sku.sku_code_variants(code_t)
    base, _pos = position_sku.strip_sku_position_suffix(code_t)
    # 原文・密着・スペース付き・基礎（基礎は後で上下で絞る）
    code_candidates = list(dict.fromkeys([code_t, *variants, base]))
    rows = []
    seen_ids = set()
    for cand in code_candidates:
        if not cand:
            continue
        for row in c.execute(
            '''SELECT * FROM product_ledger_row_maps
               WHERE csv_product_code=? AND product_id IS NOT NULL
               ORDER BY id DESC''',
            (cand,),
        ).fetchall():
            if row['id'] in seen_ids:
                continue
            seen_ids.add(row['id'])
            rows.append(row)
    # registeredCode が JSON 内だけ、の旧マップも拾う（件数制限付き）
    if base and base != code_t:
        for row in c.execute(
            '''SELECT * FROM product_ledger_row_maps
               WHERE product_id IS NOT NULL
                 AND (spec_json LIKE ? OR spec_json LIKE ?)
               ORDER BY id DESC LIMIT 200''',
            (f'%{base}%', f'%{code_t}%'),
        ).fetchall():
            if row['id'] in seen_ids:
                continue
            meta = _map_row_position_meta(row)
            if stock.text(meta.get('baseCode') or '') == base or stock.text(
                meta.get('registeredCode') or ''
            ) in variants:
                seen_ids.add(row['id'])
                rows.append(row)
    matches = []
    seen = set()
    for row in rows:
        if not _map_matches_aux_identity(
            row,
            customer=customer,
            code=code_t,
            product_name=product_name,
            size=size,
            supplier=supplier,
        ):
            continue
        full = _map_row_spec_full(row)
        # 加工サービス商品自体には衣服在庫を付けない（リンク行の本体 product_id を使う）
        if full.get('rowKind') == 'processing_service':
            continue
        pid = row['product_id']
        # 誤って加工サービス商品 ID が本体欄に入っていればスキップ
        if full.get('processingProductId') and int(pid) == int(full['processingProductId']):
            if full.get('rowKind') == 'processing_service':
                continue
        cpid = row['customer_product_id']
        cid = row['customer_id']
        color, processing, material = full['color'], full['processing'], full['material']
        key = (pid, cpid, cid, _spec_signature(color, processing, material))
        if key in seen:
            continue
        seen.add(key)
        matches.append({
            'mapId': row['id'],
            'productId': pid,
            'customerProductId': cpid,
            'customerId': cid,
            'csvCustomer': stock.text(row['csv_customer'] or ''),
            'csvSupplier': stock.text(row['csv_supplier'] or ''),
            'csvProductName': stock.text(row['csv_product_name'] or ''),
            'csvSize': stock.text(row['csv_size'] or ''),
            'color': color,
            'processing': processing,
            'material': material,
            'rowKind': full.get('rowKind') or '',
            'processingProductId': full.get('processingProductId'),
            'isMainCandidate': bool(row['is_main_candidate']) if 'is_main_candidate' in row.keys() else True,
            'fileSha256': stock.text(row['file_sha256'] or ''),
            'runId': row['run_id'],
            'sourceRowNo': row['source_row_no'],
        })

    if not matches:
        return {
            'status': 'missing',
            'reason': (
                '商品登録マップに対応がありません。先に「商品を登録」で台帳を確定するか、'
                '不足している商品・取扱を補完してください（別商品へ推測登録しません）。'
            ),
            'matches': [],
        }

    # 同一別名でも仕様（色・加工・素材）や登録先が食い違うときは曖昧
    # 色分割後: 初期在庫CSVの商品名から明示色が取れ、マップ色と一意に一致すれば絞る
    # 上下: 初期在庫側で区分が取れ、マップの上下／登録品番と一意一致すれば絞る（未特定では振り分けない）
    dest_keys = {
        (m['productId'], m['customerProductId'], _spec_signature(m['color'], m['processing'], m['material']))
        for m in matches
    }
    if len(dest_keys) > 1:
        aux_info = position_sku.resolve_garment_position(code=code_t, product_name=product_name)
        aux_pos = stock.text(aux_info.get('position') or '')
        if aux_pos in position_sku.POSITION_TOKENS:
            narrowed_pos = []
            for m in matches:
                # matches 時点の row メタは再取得
                map_row = next((r for r in rows if r['id'] == m.get('mapId')), None)
                if map_row is None:
                    continue
                meta = _map_row_position_meta(map_row)
                map_pos = stock.text(meta.get('position') or '')
                map_reg = stock.text(meta.get('registeredCode') or '')
                if map_pos == aux_pos or map_reg == stock.text(aux_info.get('registeredCode') or ''):
                    narrowed_pos.append(m)
            narrow_pos_keys = {
                (m['productId'], m['customerProductId'], _spec_signature(m['color'], m['processing'], m['material']))
                for m in narrowed_pos
            }
            if len(narrow_pos_keys) == 1:
                matches = narrowed_pos
                dest_keys = narrow_pos_keys
        try:
            import product_ledger_paren as paren_classify
            extracted = paren_classify.extract_explicit_color_from_name(product_name)
            aux_color = stock.text(extracted.get('color') or '')
            aux_key = paren_classify.color_match_key(aux_color) if aux_color else ''
        except Exception:
            aux_key = ''
        if aux_key:
            narrowed = [
                m for m in matches
                if paren_classify.color_match_key(m.get('color') or '') == aux_key
            ]
            narrow_keys = {
                (m['productId'], m['customerProductId'], _spec_signature(m['color'], m['processing'], m['material']))
                for m in narrowed
            }
            if len(narrow_keys) == 1:
                matches = narrowed
                dest_keys = narrow_keys
        if len(dest_keys) > 1:
            return {
                'status': 'ambiguous',
                'reason': (
                    '同じ原文キーに複数の登録先（色分割・上下区分・別仕様・別商品／取扱）が残っています。'
                    '推測で一方へ寄せず、確認してください（在庫数量の二重登録もしません）。'
                ),
                'matches': matches,
            }

    chosen = matches[0]
    # 生存確認（削除・統合・停止は推測で別商品へ振り替えない）
    live_pid = None
    if chosen['productId']:
        live_pid = None
        try:
            import stock_product_merge
            live_pid = stock_product_merge.resolve_live_product_id(c, chosen['productId'])
        except Exception:
            live_pid = chosen['productId']
        prod = c.execute(
            'SELECT id, name, code, active, merged_into_id FROM products WHERE id=?',
            (live_pid,),
        ).fetchone()
        if not prod:
            return {
                'status': 'deleted',
                'reason': 'マップ先の商品が削除されています。別商品へ推測登録しません。商品を補完してください。',
                'matches': matches,
            }
        if not prod['active']:
            return {
                'status': 'deleted',
                'reason': 'マップ先の商品が停止中です。別商品へ推測登録しません。商品を確認してください。',
                'matches': matches,
                'productId': prod['id'],
                'productName': prod['name'],
            }
        chosen = dict(chosen)
        chosen['productId'] = prod['id']
        chosen['officialName'] = prod['name']
        chosen['productCode'] = prod['code']

    handle = None
    if chosen.get('customerProductId'):
        handle = c.execute(
            '''SELECT id, customer_id, product_id, embroidery, active
               FROM customer_products WHERE id=?''',
            (chosen['customerProductId'],),
        ).fetchone()
        if not handle:
            return {
                'status': 'deleted',
                'reason': 'マップ先の取扱が削除されています。別取扱へ推測登録しません。取扱を補完してください。',
                'matches': matches,
                'productId': chosen.get('productId'),
            }
        if not handle['active']:
            return {
                'status': 'deleted',
                'reason': 'マップ先の取扱が停止中です。別取扱へ推測登録しません。取扱を確認してください。',
                'matches': matches,
                'productId': chosen.get('productId'),
                'handleId': handle['id'],
            }
        if chosen.get('productId') and handle['product_id'] != chosen['productId']:
            return {
                'status': 'ambiguous',
                'reason': 'マップの商品と取扱の紐づけが一致しません。推測で修正せず確認してください。',
                'matches': matches,
            }
        chosen = dict(chosen)
        chosen['customerId'] = handle['customer_id']
        chosen['embroidery'] = handle['embroidery']
        chosen['handleId'] = handle['id']

    return {
        'status': 'ok',
        'reason': '',
        'matches': matches,
        'destination': chosen,
    }


def _plan_opening_row_from_map(c, *, row_no, item, default_site, selection):
    """段階2: 永続マップだけで1行の初期在庫予定を組み立てる（判定UIを再実行しない）。"""
    payload = _aux_item_payload(row_no, item)
    qty, cost, qreasons = legacy.parse_row_qty_cost(item, row_no)
    condition, creasons = legacy.parse_row_condition(item, row_no)
    place = legacy.location_resolve(c, item.get('拠点'), item.get('棚'), default_site)
    if place.get('siteEmpty'):
        place = dict(place)
        place['usedDefaultSite'] = True
    if place.get('siteExists') is False:
        place['action'] = 'create_site'
    elif place.get('siteActive') is False:
        place['action'] = 'review'
    else:
        place['action'] = 'reuse_site'
    if place.get('shelfName'):
        if place.get('shelfExists') is False and place.get('action') != 'review':
            place['action'] = 'create_shelf'
        elif place.get('shelfActive') is False:
            place['action'] = 'review'

    resolved = lookup_ledger_destinations_for_aux(
        c,
        customer=payload['customer'],
        code=payload['code'],
        product_name=payload['productName'],
        size=payload['size'],
        supplier=payload['supplier'],
    )
    reasons = list(creasons) + list(qreasons)
    dest = resolved.get('destination') or {}
    map_status = resolved.get('status') or 'missing'

    if map_status != 'ok':
        status = legacy.STATUS_REVIEW if map_status in ('ambiguous', 'deleted') else COMPLEMENT_STATUS
        if map_status == 'missing':
            status = COMPLEMENT_STATUS
        reasons = [resolved.get('reason') or '登録先を特定できません。', *reasons]
        cust_preview = legacy._person_preview(c, 'customers', '顧客', payload['customer'], row_no)
        return {
            'rowNo': row_no,
            'status': status,
            'statusLabel': OPENING_STATUS_LABELS.get(status, status),
            'qty': qty,
            'unitCost': cost,
            'condition': condition,
            'conditionLabel': stock.condition_label(condition) if condition else '',
            'reasons': reasons,
            'csv': payload,
            'target': {
                'productId': dest.get('productId'),
                'productCode': payload['code'],
                'productName': payload['productName'],
                'officialName': dest.get('officialName') or '',
                'fromOriginalName': payload['productName'],
                'customer': payload['customer'],
                'customerId': dest.get('customerId') or cust_preview.get('id'),
                'customerAction': cust_preview.get('action') or 'create',
                'supplier': payload['supplier'],
                'size': payload['size'],
                'embroidery': dest.get('embroidery') or legacy.PENDING_EMBROIDERY,
                'handleId': dest.get('handleId') or dest.get('customerProductId'),
                'isMainSpec': bool(dest.get('isMainCandidate', True)),
                'color': dest.get('color') or '',
                'processing': dest.get('processing') or '',
                'material': dest.get('material') or '',
                'mapStatus': map_status,
                'mapMatches': len(resolved.get('matches') or []),
            },
            'place': place,
            'stock': None,
            'matchedLedger': False,
            'complement': status == COMPLEMENT_STATUS,
            'heldWithProduct': False,
            'selected': False,
            'mapResolve': resolved,
            'fromPersistedMap': True,
        }

    product_id = dest.get('productId')
    handle_id = dest.get('handleId') or dest.get('customerProductId')
    customer_id = dest.get('customerId')
    embroidery = stock.text(dest.get('embroidery') or '') or legacy.PENDING_EMBROIDERY
    official_name = stock.text(dest.get('officialName') or '')
    cust_preview = legacy._person_preview(c, 'customers', '顧客', payload['customer'], row_no)
    if cust_preview.get('id'):
        customer_id = cust_preview['id']
    customer_action = cust_preview.get('action') or ('reuse' if customer_id else 'create')
    sup_preview = legacy._person_preview(c, 'suppliers', '仕入先', payload['supplier'], row_no)

    if place.get('action') == 'review':
        reasons.append(f'{row_no}行目：拠点または棚が停止中です。')

    prior = None
    stock_info = None
    if reasons or condition is None or qty is None or cost is None:
        status = legacy.STATUS_REVIEW
    else:
        stock_info = legacy._stock_match(
            c, handle_id, payload['size'], place.get('locationId'), condition, cost,
        )
        prior = legacy.lookup_stock_map(
            c,
            customer=payload['customer'],
            code=payload['code'],
            product_name=payload['productName'],
            size=payload['size'],
            condition=condition,
            unit_cost=cost,
            site=payload['site'],
            shelf=payload['shelf'],
        )
        if stock_info and stock_info.get('exists'):
            if stock_info['currentQty'] == qty:
                status = legacy.STATUS_UNCHANGED
            else:
                status = legacy.STATUS_QTY_DIFF
                reasons.append(
                    f'{row_no}行目：既存在庫 {stock_info["currentQty"]} とCSV数量 {qty} が異なります。'
                    '明示選択したときだけ更新します（黙って上書きしません）。'
                )
        elif prior and prior.get('lot_ids_json'):
            status = legacy.STATUS_REVIEW
            reasons.append(f'{row_no}行目：以前取り込んだ行の記録があります。内容を確認してください。')
        else:
            master_new = customer_action == 'create' or not product_id or place.get('action') in (
                'create_site', 'create_shelf',
            )
            status = legacy.STATUS_CREATE if master_new else legacy.STATUS_REUSE

    sel = selection if isinstance(selection, dict) else {}
    return {
        'rowNo': row_no,
        'status': status,
        'statusLabel': OPENING_STATUS_LABELS.get(status, status),
        'qty': qty,
        'unitCost': cost,
        'condition': condition,
        'conditionLabel': stock.condition_label(condition) if condition else '',
        'reasons': reasons,
        'csv': payload,
        'target': {
            'productId': product_id,
            'productCode': dest.get('productCode') or payload['code'],
            'productName': official_name or payload['productName'],
            'officialName': official_name,
            'fromOriginalName': payload['productName'],
            'customer': payload['customer'],
            'customerId': customer_id,
            'customerAction': customer_action,
            'supplier': payload['supplier'],
            'supplierId': sup_preview.get('id'),
            'supplierAction': sup_preview.get('action'),
            'size': payload['size'],
            'embroidery': embroidery,
            'handleId': handle_id,
            'isMainSpec': bool(dest.get('isMainCandidate', True)),
            'color': dest.get('color') or '',
            'processing': dest.get('processing') or '',
            'material': dest.get('material') or '',
            'mapStatus': 'ok',
            'mapId': dest.get('mapId'),
        },
        'place': place,
        'stock': stock_info,
        'matchedLedger': True,
        'complement': False,
        'heldWithProduct': False,
        'selected': bool(sel.get('updateQty')) if status == legacy.STATUS_QTY_DIFF else False,
        'priorImport': bool(prior) if status != legacy.STATUS_HOLD else False,
        'mapResolve': resolved,
        'fromPersistedMap': True,
    }


def build_opening_stock_plans_from_maps(
    c,
    *,
    aux_uploaded,
    default_site_name='',
    opening_selections=None,
):
    """段階2専用: 初期在庫CSVのみ＋永続マップで登録予定を組む（商品判定は再実行しない）。"""
    t0 = time.perf_counter()
    if not aux_uploaded:
        return {
            'enabled': False,
            'plans': [],
            'counts': {},
            'complementCandidates': [],
            'mastersToCreate': {'customers': [], 'suppliers': [], 'sites': [], 'shelves': []},
            'note': '',
            'fromPersistedMap': True,
            'timingsMs': {'openingStockPrep': 0},
        }

    default_site = stock.text(default_site_name) or stock.UNCONFIRMED_SITE_NAME
    selections = opening_selections if isinstance(opening_selections, dict) else {}
    aux_rows = list(aux_uploaded.get('rows') or [])
    plans = []
    complement = []
    masters = {
        'customers': set(),
        'suppliers': set(),
        'sites': set(),
        'shelves': set(),
    }

    for row_no, item in aux_rows:
        product_name = stock.text(item.get('商品名'))
        qty, cost, qreasons = legacy.parse_row_qty_cost(item, row_no)
        if not product_name and qty == 0 and not qreasons:
            plans.append({
                'rowNo': row_no,
                'status': legacy.STATUS_EXCLUDED,
                'statusLabel': OPENING_STATUS_LABELS[legacy.STATUS_EXCLUDED],
                'qty': qty,
                'unitCost': cost,
                'condition': None,
                'conditionLabel': '',
                'reasons': [f'{row_no}行目：商品名が空欄で在庫が0のため対象外です（登録しません）。'],
                'csv': _aux_item_payload(row_no, item),
                'target': None,
                'place': legacy.location_resolve(c, item.get('拠点'), item.get('棚'), default_site),
                'stock': None,
                'matchedLedger': False,
                'complement': False,
                'heldWithProduct': False,
                'fromPersistedMap': True,
            })
            continue
        plan = _plan_opening_row_from_map(
            c,
            row_no=row_no,
            item=item,
            default_site=default_site,
            selection=selections.get(str(row_no)) or selections.get(row_no) or {},
        )
        plans.append(plan)
        if plan.get('complement') or plan.get('status') == COMPLEMENT_STATUS:
            complement.append(plan)
        _collect_master_additions(plan, masters)

    counts = defaultdict(int)
    for p in plans:
        counts[p['status']] += 1

    prep_ms = round((time.perf_counter() - t0) * 1000, 2)
    return {
        'enabled': True,
        'willRegisterOpeningStock': True,
        'fromPersistedMap': True,
        'fileName': aux_uploaded.get('fileName') or '',
        'fileSha256': aux_uploaded.get('fileSha256') or '',
        'encoding': aux_uploaded.get('encoding') or '',
        'rowCount': len(aux_rows),
        'plans': plans,
        'counts': dict(counts),
        'complementCandidates': complement,
        'mastersToCreate': {
            'customers': sorted(masters['customers']),
            'suppliers': sorted(masters['suppliers']),
            'sites': sorted(masters['sites']),
            'shelves': sorted(masters['shelves']),
        },
        'defaultSiteName': default_site,
        'ignoredQtyColumns': list(legacy.IGNORED_QTY_COLUMNS),
        'timingsMs': {'openingStockPrep': prep_ms},
        'note': (
            '初期在庫CSVと、商品登録時に保存した原文→登録先マップで照合します。'
            'メイン名選定や一括承認のやり直しはしません。'
            '数量は「在庫」列のみ（0・マイナスもそのまま）。拠点空欄は「未確認」へ登録します。'
            '曖昧・削除済み・未登録の行は理由を示し、別商品へ推測登録しません。'
            '再取込では二重加算しません。数量差異は明示選択時のみ更新します。'
        ),
    }
