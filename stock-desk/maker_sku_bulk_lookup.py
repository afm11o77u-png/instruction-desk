"""旧CSV一括取り込み: 選択品番のメーカー情報を一括取得する（無料取得のみ）。

- 既存 search_maker_product を共通処理として再利用
- 同一メーカー・メーカー品番・上下区分は重複検索しない
- 順番実行（同時大量リクエスト禁止）・件数上限・中止対応
- 有料AI／検索APIは使わない
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable

import maker_sku_lookup as msl
import product_ledger_position as position_sku
import stock

# 処理上限（サイト負荷抑制）
BULK_LOOKUP_MAX_TARGETS = 40
BULK_LOOKUP_DELAY_SEC = 0.8
BULK_LOOKUP_JOB_TTL_SEC = 60 * 60

_STATUS_LABELS = {
    'ok': '一致候補1件',
    'multiple': '候補複数',
    'colorway_choice': '配色の選択が必要',
    'fetch_failed': '検索結果取得不可',
    'page_unavailable': '商品ページ取得不可',
    'sku_mismatch': '品番不一致',
    'maker_mismatch': 'メーカー不一致',
    'parse_incomplete': '品番一致したが必要項目抽出不可',
    'official_no_hit': '公式サイトに該当なし',
    'no_match': '候補なし',
    'invalid_sku': 'メーカー品番が対象外',
    'invalid_url': 'URLが対象外',
    'pending': '未処理',
    'cancelled': '未処理・中止',
    'error': '取得失敗',
}

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def bulk_lookup_dedupe_key(*, manufacturer: str, maker_sku: str, position: str = '') -> str:
    """同一処理内の重複検索キー（メーカー＋基礎品番＋上下）。

    用途注記付き品番も検索用品番へ揃える（単品・URL指定と同じ）。
    """
    mfr = stock.text(manufacturer) or ''
    norm = msl.normalize_search_maker_sku(maker_sku)
    base = norm.get('baseSku') or norm.get('searchSku') or stock.text(maker_sku) or ''
    pos = stock.text(position) or norm.get('position') or ''
    if pos not in position_sku.POSITION_TOKENS:
        pos = ''
    return f'{msl.sku_match_key(mfr)}|{msl.sku_match_key(base)}|{pos}'


def _position_from_group_or_row(group: dict, rows: list) -> str:
    code = stock.text(group.get('code') or '')
    _base, pos = position_sku.strip_sku_position_suffix(code)
    if pos in position_sku.POSITION_TOKENS:
        return pos
    for row in rows:
        csv = row.get('csv') if isinstance(row.get('csv'), dict) else {}
        for key in ('position', 'registeredCode', 'plannedRegisteredSku', 'code', 'sourceCode'):
            val = stock.text(csv.get(key) or '')
            if not val:
                continue
            if key == 'position' and val in position_sku.POSITION_TOKENS:
                return val
            _b, p = position_sku.strip_sku_position_suffix(val)
            if p in position_sku.POSITION_TOKENS:
                return p
        pc = row.get('parenClassification') if isinstance(row.get('parenClassification'), dict) else {}
        p2 = stock.text(pc.get('position') or '')
        if p2 in position_sku.POSITION_TOKENS:
            return p2
    return ''


def _rows_for_group(group: dict, all_rows: list) -> list:
    row_nos = {int(n) for n in (group.get('rowNos') or []) if str(n).isdigit() or isinstance(n, int)}
    if not row_nos:
        return []
    out = []
    for row in all_rows or []:
        try:
            if int(row.get('rowNo')) in row_nos:
                out.append(row)
        except (TypeError, ValueError):
            continue
    return out


def prepare_bulk_lookup_targets(
    *,
    selected_codes: list,
    sku_groups: list,
    rows: list = None,
    max_targets: int = BULK_LOOKUP_MAX_TARGETS,
) -> dict:
    """選択品番から一括取得対象を組み立てる。

    - 有効なメーカー品番があるものだけ eligible
    - 別注・品番なし・空欄等は excluded
    - 同じメーカー・品番・上下は searchKey でまとめ、重複検索しない
    """
    groups_by_code = {
        stock.text(g.get('code') or ''): g
        for g in (sku_groups or [])
        if isinstance(g, dict) and stock.text(g.get('code') or '')
    }
    all_rows = [r for r in (rows or []) if isinstance(r, dict)]
    selected = []
    excluded = []
    for code in selected_codes or []:
        c = stock.text(code)
        if not c:
            excluded.append({
                'skuCode': stock.text(code) or '(空)',
                'reason': '別注・品番なし・空欄などメーカー品番が対象外',
            })
            continue
        if c not in selected:
            selected.append(c)

    pending_items = []
    for code in selected:
        group = groups_by_code.get(code)
        if not group:
            excluded.append({
                'skuCode': code,
                'reason': '品番グループが見つかりません',
            })
            continue
        group_rows = _rows_for_group(group, all_rows)
        mk = group.get('makerCorrection') if isinstance(group.get('makerCorrection'), dict) else {}
        if not mk:
            mk = msl.sku_group_maker_correction_meta(group, group_rows)
        maker_sku = stock.text(mk.get('makerSku') or '')
        bulk_ok = bool(mk.get('bulkEligible'))
        if not bulk_ok:
            bulk_ok = bool(maker_sku) and msl.is_valid_maker_sku(maker_sku)
        if not bulk_ok or not maker_sku:
            reason = '別注・品番なし・空欄などメーカー品番が対象外'
            if msl.is_management_sku(stock.text(group.get('code') or '')):
                reason = '別注・品番なし・管理用品番のため対象外'
            excluded.append({'skuCode': code, 'reason': reason, 'makerSku': maker_sku})
            continue
        position = _position_from_group_or_row(group, group_rows)
        manufacturer = stock.text(mk.get('manufacturer') or '')
        supplier = stock.text(mk.get('supplier') or '')
        if not manufacturer:
            manufacturer = msl.resolve_manufacturer(manufacturer='', supplier=supplier)
        source_name = stock.text(mk.get('sourceNameHint') or '')
        if not source_name and group_rows:
            csv0 = group_rows[0].get('csv') if isinstance(group_rows[0].get('csv'), dict) else {}
            source_name = stock.text(csv0.get('productName') or '')
        if not source_name:
            names = [stock.text(n) for n in (group.get('nameCandidates') or []) if stock.text(n)]
            source_name = msl.neutral_search_source_name(names) if names else ''
        current_name = stock.text(
            group.get('plannedOfficialName')
            or (group.get('namePlan') or {}).get('officialName')
            or source_name
        )
        current_color = stock.text(
            group.get('plannedRegisteredColor') or group.get('plannedColor') or ''
        )
        current_sku = stock.text(code) or msl.planned_registered_sku(
            maker_sku=maker_sku, position=position,
        )
        search_key = bulk_lookup_dedupe_key(
            manufacturer=manufacturer, maker_sku=maker_sku, position=position,
        )
        pending_items.append({
            'skuCode': code,
            'makerSku': maker_sku,
            'position': position,
            'manufacturer': manufacturer,
            'supplier': supplier,
            'sourceName': source_name,
            'currentName': current_name,
            'currentColor': current_color,
            'currentSupplement': '',
            'currentSku': current_sku,
            'rowNos': list(group.get('rowNos') or mk.get('rowNos') or []),
            'searchKey': search_key,
        })

    # 上限は「ユニーク検索」単位。超過分は excluded ではなく limitSkipped として残す
    # （未処理扱い。候補なしにはしない）
    unique_order = []
    unique_map: dict[str, list] = {}
    for item in pending_items:
        key = item['searchKey']
        unique_map.setdefault(key, []).append(item)
        if key not in unique_order:
            unique_order.append(key)

    limit = max(1, int(max_targets or BULK_LOOKUP_MAX_TARGETS))
    accepted_keys = unique_order[:limit]
    skipped_keys = unique_order[limit:]
    limit_skipped = []
    for key in skipped_keys:
        for item in unique_map[key]:
            limit_skipped.append({
                'skuCode': item['skuCode'],
                'reason': f'一括取得の上限（{limit}件のユニーク検索）を超えたため未処理',
                'makerSku': item.get('makerSku') or '',
                'searchKey': key,
                'status': 'pending',
                'statusLabel': _STATUS_LABELS['pending'],
            })

    targets = []
    for key in accepted_keys:
        items = unique_map[key]
        primary = items[0]
        targets.append({
            'searchKey': key,
            'makerSku': primary['makerSku'],
            'position': primary.get('position') or '',
            'manufacturer': primary.get('manufacturer') or '',
            'supplier': primary.get('supplier') or '',
            'sourceName': primary.get('sourceName') or '',
            'currentName': primary.get('currentName') or '',
            'currentColor': primary.get('currentColor') or '',
            'currentSupplement': primary.get('currentSupplement') or '',
            'currentSku': primary.get('currentSku') or '',
            'skuCodes': [it['skuCode'] for it in items],
            'rowNosBySku': {it['skuCode']: list(it.get('rowNos') or []) for it in items},
            'items': items,
        })

    return {
        'targets': targets,
        'excluded': excluded,
        'limitSkipped': limit_skipped,
        'selectedCount': len(selected),
        'eligibleCount': len(pending_items) - len(limit_skipped),
        'excludedCount': len(excluded),
        'uniqueSearchCount': len(accepted_keys),
        'duplicateCollapsed': max(0, len(pending_items) - len(unique_order)),
        'maxTargets': limit,
        'delaySec': BULK_LOOKUP_DELAY_SEC,
    }


def _empty_counts() -> dict:
    return {
        'ok': 0,
        'multiple': 0,
        'colorwayChoice': 0,
        'needsConfirm': 0,  # multiple + parse_incomplete + sku_mismatch 等の要確認系
        'fetchFailed': 0,
        'pageUnavailable': 0,
        'noMatch': 0,
        'officialNoHit': 0,
        'cancelled': 0,
        'pending': 0,
        'error': 0,
    }


def _bump_counts(counts: dict, status: str) -> None:
    if status == 'ok':
        counts['ok'] += 1
    elif status == 'multiple':
        counts['multiple'] += 1
        counts['needsConfirm'] += 1
    elif status == 'colorway_choice':
        counts['colorwayChoice'] += 1
        counts['needsConfirm'] += 1
    elif status in (
        'parse_incomplete', 'sku_mismatch', 'maker_mismatch',
        'invalid_sku', 'invalid_url',
    ):
        counts['needsConfirm'] += 1
    elif status == 'fetch_failed':
        counts['fetchFailed'] += 1
    elif status == 'page_unavailable':
        counts['pageUnavailable'] += 1
    elif status == 'official_no_hit':
        counts['officialNoHit'] += 1
    elif status == 'no_match':
        counts['noMatch'] += 1
    elif status == 'cancelled':
        counts['cancelled'] += 1
    elif status == 'pending':
        counts['pending'] += 1
    else:
        counts['error'] += 1


def search_criteria_dict(*, manufacturer: str = '', maker_sku: str = '', position: str = '') -> dict:
    """検索条件の比較用（品番だけでは再利用しない）。"""
    return {
        'manufacturer': stock.text(manufacturer) or '',
        'makerSku': stock.text(maker_sku) or '',
        'position': stock.text(position) or '',
    }


def search_criteria_equal(a: dict | None, b: dict | None) -> bool:
    """メーカー・メーカー品番・上下が一致するか（SKU単独一致では真にしない）。"""
    left = a if isinstance(a, dict) else {}
    right = b if isinstance(b, dict) else {}
    return (
        msl.sku_match_key(left.get('manufacturer') or '')
        == msl.sku_match_key(right.get('manufacturer') or '')
        and msl.sku_match_key(left.get('makerSku') or '')
        == msl.sku_match_key(right.get('makerSku') or '')
        and (stock.text(left.get('position') or '') or '')
        == (stock.text(right.get('position') or '') or '')
    )


def is_fetched_bulk_status(status: str) -> bool:
    """一括取得で「取得済み」とみなし省略する状態（pending/cancelled は未取得）。"""
    st = stock.text(status) or ''
    return bool(st) and st not in ('pending', 'cancelled')


def criteria_from_result_row(row: dict | None) -> dict:
    row = row if isinstance(row, dict) else {}
    sc = row.get('searchCriteria') if isinstance(row.get('searchCriteria'), dict) else None
    if sc:
        return search_criteria_dict(
            manufacturer=sc.get('manufacturer') or '',
            maker_sku=sc.get('makerSku') or '',
            position=sc.get('position') or '',
        )
    return search_criteria_dict(
        manufacturer=row.get('manufacturer') or '',
        maker_sku=row.get('makerSku') or '',
        position=row.get('position') or '',
    )


def classify_incremental_bulk_selection(
    *,
    selected_codes: list,
    sku_groups: list,
    rows: list = None,
    prior_results: list = None,
    force_search_keys: list = None,
) -> dict:
    """再実行時の新規取得／取得済み省略を分類する。

    - 取得済み（同一メーカー・品番・上下）は省略
    - 条件が変わった場合は新規取得扱いにし、差分を返す
    - force_search_keys は明示的「再取得」
    - pending/cancelled は未取得として再検索対象
    """
    prepared = prepare_bulk_lookup_targets(
        selected_codes=selected_codes,
        sku_groups=sku_groups,
        rows=rows,
        max_targets=10_000,
    )
    force = {
        stock.text(k) for k in (force_search_keys or []) if stock.text(k)
    }
    prior = [r for r in (prior_results or []) if isinstance(r, dict)]
    by_code: dict[str, dict] = {}
    for row in prior:
        if not is_fetched_bulk_status(row.get('status') or ''):
            continue
        for code in row.get('skuCodes') or []:
            c = stock.text(code)
            if c and c not in by_code:
                by_code[c] = row

    to_fetch_codes: list[str] = []
    skipped: list[dict] = []
    criteria_changed: list[dict] = []
    seen_fetch = set()
    seen_skip = set()

    for target in prepared['targets']:
        search_key = stock.text(target.get('searchKey') or '')
        current_criteria = search_criteria_dict(
            manufacturer=target.get('manufacturer') or '',
            maker_sku=target.get('makerSku') or '',
            position=target.get('position') or '',
        )
        for code in target.get('skuCodes') or []:
            c = stock.text(code)
            if not c:
                continue
            cached = by_code.get(c)
            forced = bool(search_key and search_key in force)
            if cached and not forced:
                prev_criteria = criteria_from_result_row(cached)
                if search_criteria_equal(current_criteria, prev_criteria):
                    if c not in seen_skip:
                        seen_skip.add(c)
                        skipped.append({
                            'skuCode': c,
                            'searchKey': cached.get('searchKey') or search_key,
                            'status': cached.get('status') or '',
                            'reason': '取得済みのため省略',
                        })
                    continue
                if c not in seen_fetch:
                    criteria_changed.append({
                        'skuCode': c,
                        'previous': prev_criteria,
                        'current': current_criteria,
                        'previousSearchKey': cached.get('searchKey') or '',
                        'currentSearchKey': search_key,
                    })
            if c not in seen_fetch:
                seen_fetch.add(c)
                to_fetch_codes.append(c)

    # 上限超過分など targets 外の selected は prepare 側 excluded/limitSkipped に任せる
    return {
        'toFetchCodes': to_fetch_codes,
        'skipped': skipped,
        'criteriaChanged': criteria_changed,
        'newFetchCount': len(to_fetch_codes),
        'skippedCachedCount': len(skipped),
        'preflightLabel': (
            f'新規取得{len(to_fetch_codes)}件／取得済みのため省略{len(skipped)}件'
        ),
        'excluded': prepared['excluded'],
        'excludedCount': prepared['excludedCount'],
    }


def merge_bulk_result_rows(
    prior_results: list,
    new_results: list,
    *,
    replace_sku_codes: list = None,
) -> list:
    """結果一覧を追記・更新（同一 searchKey は重複させない）。"""
    replace_codes = {
        stock.text(c) for c in (replace_sku_codes or []) if stock.text(c)
    }
    new_rows = [r for r in (new_results or []) if isinstance(r, dict)]
    new_keys = {stock.text(r.get('searchKey') or '') for r in new_rows}
    new_keys.discard('')
    kept = []
    for row in prior_results or []:
        if not isinstance(row, dict):
            continue
        key = stock.text(row.get('searchKey') or '')
        if key and key in new_keys:
            continue
        codes = [stock.text(c) for c in (row.get('skuCodes') or [])]
        if replace_codes and any(c in replace_codes for c in codes if c):
            continue
        kept.append(row)
    return kept + new_rows


def recount_bulk_result_counts(results: list) -> dict:
    counts = _empty_counts()
    for row in results or []:
        if isinstance(row, dict):
            _bump_counts(counts, stock.text(row.get('status') or 'error') or 'error')
    return counts


def _result_row_from_search(*, target: dict, search_result: dict, status_override: str = '') -> dict:
    status = status_override or stock.text(search_result.get('status') or 'error') or 'error'
    label = (
        _STATUS_LABELS.get(status)
        or search_result.get('statusLabel')
        or status
    )
    cands = list(search_result.get('candidates') or [])
    manufacturer = (
        search_result.get('manufacturer')
        or target.get('manufacturer')
        or ''
    )
    maker_sku = target.get('makerSku') or search_result.get('makerSku') or ''
    position = target.get('position') or ''
    return {
        'searchKey': target.get('searchKey') or '',
        'skuCodes': list(target.get('skuCodes') or []),
        'rowNosBySku': dict(target.get('rowNosBySku') or {}),
        'makerSku': maker_sku,
        'position': position,
        'manufacturer': manufacturer,
        'searchCriteria': search_criteria_dict(
            manufacturer=manufacturer, maker_sku=maker_sku, position=position,
        ),
        'supplier': target.get('supplier') or '',
        'currentName': target.get('currentName') or '',
        'currentColor': target.get('currentColor') or '',
        'currentSupplement': target.get('currentSupplement') or '',
        'currentSku': target.get('currentSku') or '',
        'sourceName': target.get('sourceName') or '',
        'plannedRegisteredSku': search_result.get('plannedRegisteredSku') or target.get('currentSku') or '',
        'status': status,
        'statusLabel': label,
        'note': search_result.get('note') or '',
        'referenceUrl': search_result.get('referenceUrl') or '',
        'candidates': cands,
        'matchedPages': list(search_result.get('matchedPages') or []),
        'fromUrl': bool(search_result.get('fromUrl')),
        'adoptableSingle': status == 'ok' and len(cands) == 1 and bool((cands[0] or {}).get('name')),
    }


def _public_job(job: dict) -> dict:
    return {
        'jobId': job['id'],
        'status': job['status'],
        'done': job['done'],
        'total': job['total'],
        'currentSku': job.get('currentSku') or '',
        'currentMakerSku': job.get('currentMakerSku') or '',
        'counts': dict(job.get('counts') or _empty_counts()),
        'results': list(job.get('results') or []),
        'excluded': list(job.get('excluded') or []),
        'limitSkipped': list(job.get('limitSkipped') or []),
        'selectedCount': job.get('selectedCount') or 0,
        'excludedCount': job.get('excludedCount') or 0,
        'uniqueSearchCount': job.get('uniqueSearchCount') or 0,
        'duplicateCollapsed': job.get('duplicateCollapsed') or 0,
        'maxTargets': job.get('maxTargets') or BULK_LOOKUP_MAX_TARGETS,
        'delaySec': job.get('delaySec') or BULK_LOOKUP_DELAY_SEC,
        'error': job.get('error') or '',
        'cancelled': bool(job.get('cancel_requested')),
        'finished': job['status'] in ('done', 'cancelled', 'error'),
    }


def _purge_old_jobs_locked(now: float | None = None) -> None:
    now = time.time() if now is None else now
    dead = [
        jid for jid, job in _jobs.items()
        if (now - float(job.get('updatedAt') or job.get('createdAt') or 0)) > BULK_LOOKUP_JOB_TTL_SEC
    ]
    for jid in dead:
        _jobs.pop(jid, None)


def start_bulk_lookup_job(
    db_path,
    *,
    selected_codes: list,
    sku_groups: list,
    rows: list = None,
    max_targets: int = None,
    search_fn: Callable = None,
    sleep_fn: Callable = None,
) -> dict:
    """一括取得ジョブを開始する（バックグラウンド・順番実行）。"""
    prepared = prepare_bulk_lookup_targets(
        selected_codes=selected_codes,
        sku_groups=sku_groups,
        rows=rows,
        max_targets=BULK_LOOKUP_MAX_TARGETS if max_targets is None else max_targets,
    )
    targets = prepared['targets']
    job_id = uuid.uuid4().hex
    now = time.time()
    job = {
        'id': job_id,
        'status': 'running' if targets else 'done',
        'done': 0,
        'total': len(targets),
        'currentSku': '',
        'currentMakerSku': '',
        'counts': _empty_counts(),
        'results': [],
        'excluded': prepared['excluded'],
        'limitSkipped': prepared['limitSkipped'],
        'selectedCount': prepared['selectedCount'],
        'excludedCount': prepared['excludedCount'],
        'uniqueSearchCount': prepared['uniqueSearchCount'],
        'duplicateCollapsed': prepared['duplicateCollapsed'],
        'maxTargets': prepared['maxTargets'],
        'delaySec': prepared['delaySec'],
        'cancel_requested': False,
        'error': '',
        'createdAt': now,
        'updatedAt': now,
        'db_path': str(db_path),
        'targets': targets,
    }
    # 上限超過分は結果一覧にも「未処理」として載せる（候補なしと混同しない）
    for skipped in prepared['limitSkipped']:
        job['results'].append({
            'searchKey': skipped.get('searchKey') or '',
            'skuCodes': [skipped.get('skuCode') or ''],
            'rowNosBySku': {},
            'makerSku': skipped.get('makerSku') or '',
            'position': '',
            'manufacturer': '',
            'supplier': '',
            'currentName': '',
            'currentColor': '',
            'currentSku': skipped.get('skuCode') or '',
            'plannedRegisteredSku': '',
            'status': 'pending',
            'statusLabel': _STATUS_LABELS['pending'],
            'note': skipped.get('reason') or '',
            'referenceUrl': '',
            'candidates': [],
            'matchedPages': [],
            'adoptableSingle': False,
        })
        _bump_counts(job['counts'], 'pending')

    with _jobs_lock:
        _purge_old_jobs_locked(now)
        _jobs[job_id] = job

    if not targets:
        return _public_job(job)

    worker_search = search_fn or msl.search_maker_product
    worker_sleep = sleep_fn or time.sleep

    def _run():
        try:
            with stock.connect(job['db_path']) as c:
                for idx, target in enumerate(targets):
                    with _jobs_lock:
                        if job.get('cancel_requested'):
                            # 以後は中止。未処理は cancelled（候補なしにしない）
                            for rest in targets[idx:]:
                                row = _result_row_from_search(
                                    target=rest,
                                    search_result={'status': 'cancelled', 'candidates': [], 'note': '中止されたため未処理'},
                                    status_override='cancelled',
                                )
                                job['results'].append(row)
                                _bump_counts(job['counts'], 'cancelled')
                            job['status'] = 'cancelled'
                            job['currentSku'] = ''
                            job['currentMakerSku'] = ''
                            job['updatedAt'] = time.time()
                            return
                        job['currentSku'] = (target.get('skuCodes') or [''])[0]
                        job['currentMakerSku'] = target.get('makerSku') or ''
                        job['updatedAt'] = time.time()

                    try:
                        result = worker_search(
                            c,
                            manufacturer=target.get('manufacturer') or '',
                            maker_sku=(
                                msl.planned_registered_sku(
                                    maker_sku=target.get('makerSku') or '',
                                    position=target.get('position') or '',
                                )
                                if target.get('position')
                                else (target.get('makerSku') or '')
                            ),
                            current_name=target.get('currentName') or '',
                            current_color=target.get('currentColor') or '',
                            current_supplement=target.get('currentSupplement') or '',
                            current_sku=target.get('currentSku') or '',
                            source_name=target.get('sourceName') or '',
                            supplier=target.get('supplier') or '',
                        )
                    except Exception as e:  # noqa: BLE001 — ジョブ継続のため捕捉
                        result = {
                            'status': 'error',
                            'statusLabel': '取得失敗',
                            'candidates': [],
                            'matchedPages': [],
                            'referenceUrl': '',
                            'note': f'{type(e).__name__}: {e}',
                            'makerSku': target.get('makerSku') or '',
                            'plannedRegisteredSku': target.get('currentSku') or '',
                            'manufacturer': target.get('manufacturer') or '',
                        }

                    row = _result_row_from_search(target=target, search_result=result)
                    with _jobs_lock:
                        job['results'].append(row)
                        _bump_counts(job['counts'], row['status'])
                        job['done'] = idx + 1
                        job['updatedAt'] = time.time()

                    if idx + 1 < len(targets):
                        with _jobs_lock:
                            cancelled = bool(job.get('cancel_requested'))
                        if cancelled:
                            continue
                        worker_sleep(float(job.get('delaySec') or BULK_LOOKUP_DELAY_SEC))

                with _jobs_lock:
                    if job.get('cancel_requested') and job['status'] == 'running':
                        job['status'] = 'cancelled'
                    elif job['status'] == 'running':
                        job['status'] = 'done'
                    job['currentSku'] = ''
                    job['currentMakerSku'] = ''
                    job['updatedAt'] = time.time()
        except Exception as e:  # noqa: BLE001
            with _jobs_lock:
                job['status'] = 'error'
                job['error'] = f'{type(e).__name__}: {e}'
                job['updatedAt'] = time.time()

    threading.Thread(target=_run, name=f'maker-bulk-{job_id[:8]}', daemon=True).start()
    return _public_job(job)


def get_bulk_lookup_job(job_id: str) -> dict:
    jid = stock.text(job_id)
    with _jobs_lock:
        _purge_old_jobs_locked()
        job = _jobs.get(jid)
        if not job:
            raise ValueError('一括取得ジョブが見つかりません。')
        return _public_job(job)


def cancel_bulk_lookup_job(job_id: str) -> dict:
    jid = stock.text(job_id)
    with _jobs_lock:
        job = _jobs.get(jid)
        if not job:
            raise ValueError('一括取得ジョブが見つかりません。')
        job['cancel_requested'] = True
        job['updatedAt'] = time.time()
        # 未開始の残りはワーカー側で cancelled 化する。既に done ならそのまま。
        if job['status'] == 'running' and job['done'] >= job['total']:
            job['status'] = 'cancelled'
        return _public_job(job)


def reset_bulk_lookup_jobs_for_tests() -> None:
    """ユニットテスト用。本番UIからは呼ばない。"""
    with _jobs_lock:
        _jobs.clear()
