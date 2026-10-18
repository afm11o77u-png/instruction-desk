#!/usr/bin/env python3
"""旧CSV 品番なし取込の検証（テスト用DBのみ。実運用本登録しない）。

  python3 scripts/verify_legacy_csv_blank_sku.py
  python3 scripts/verify_legacy_csv_blank_sku.py /path/to/item_report.csv
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import legacy_csv_import  # noqa: E402
import stock  # noqa: E402

DEFAULT_CSV = Path(
    '/cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/internal/item_report.csv'
)
UPLOAD_CANDIDATE = Path(
    '/home/ubuntu/.cursor/projects/workspace/uploads/item_report__1__8884.csv'
)

# 修正前（品番空→要確認）の空DBプレビュー参考値（PR #123 / #129 報告）
BEFORE_EMPTY_DB = {
    'create': 14057,
    'reuse': 0,
    'qty_diff': 0,
    'unchanged': 0,
    'needs_review': 422,
    'hold': 263,
    'excluded': 0,
}


def preview_body(blob, name='item_report.csv'):
    return {
        'file': base64.b64encode(blob).decode('ascii'),
        'name': name,
        'basisDate': '2026-10-18',
        'defaultSiteName': '未確認',
        '_canWrite': True,
        '_actor': {'name': '品番なし検証'},
    }


def reason_bucket(text: str) -> str:
    if '対象外' in text or '在庫が0のため対象外' in text:
        return 'excluded_empty_name_zero'
    if '品番（商品コード）が空' in text:
        return 'blank_sku_old'
    if '＊' in text and '中古' in text:
        return 'star_new_conflict'
    if '加工仕様' in text or '取扱が複数' in text or 'CSVに加工欄' in text:
        return 'embroidery'
    if '仕入先が既存と異なる' in text or '表記差' in text:
        return 'supplier_or_form'
    if '停止中' in text:
        return 'inactive'
    if '同じ品番' in text or '品番なしで' in text and '複数' in text:
        return 'product_ambiguous'
    if 'カテゴリ' in text:
        return 'category'
    if '商品名が空' in text:
        return 'empty_name'
    if 'サイズが空' in text:
        return 'empty_size'
    if '重複候補' in text:
        return 'csv_duplicate'
    return 'other'


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.is_file():
        raise SystemExit(f'CSV が見つかりません: {csv_path}')
    blob = csv_path.read_bytes()
    text, enc = legacy_csv_import.decode_csv_bytes(blob)
    fields, rows = legacy_csv_import.parse_csv_text(text)

    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    c.commit()

    body = preview_body(blob, csv_path.name)
    preview = legacy_csv_import.build_preview(c, body)
    c.commit()

    raw = preview['countsRaw']
    blank_rows = [r for r in preview['rows'] if r.get('product', {}).get('blankSku') or r['csv'].get('code') == '']
    uniform = [r for r in blank_rows if r.get('product', {}).get('category') == '制服']
    parts = [r for r in blank_rows if r.get('product', {}).get('category') == '部品・資材']
    excluded = [r for r in preview['rows'] if r['status'] == 'excluded']

    review_reasons = Counter()
    for r in preview['rows']:
        if r['status'] != 'needs_review':
            continue
        joined = ' '.join(r.get('reasons') or [])
        review_reasons[reason_bucket(joined)] += 1

    fabric_checks = []
    for r in blank_rows:
        name = r['csv'].get('productName') or ''
        if name in ('NPC白生地', '861382-007用生地　10m', '861382-060用生地　10m') or 'シェフメイト' in name:
            fabric_checks.append({
                'rowNo': r['rowNo'],
                'name': name,
                'size': r['csv'].get('size'),
                'qty': r.get('qty'),
                'unitCost': r.get('unitCost'),
                'status': r['status'],
                'category': r.get('product', {}).get('category'),
            })

    # 試験取込: 制服例外＋部品1＋生地1＋対象外を含む小さな確定（同じプレビュー指紋で全 create/reuse）
    # 実運用禁止のため一時DBのみ。件数が多いので allow せず、対象外以外の create を確定。
    # 全件 commit は重いので、制服・部品サンプル行だけ includeRowNos で試験する。
    sample_nos = []
    for pair in legacy_csv_import.UNIFORM_BLANK_SKU_PAIRS:
        for r in blank_rows:
            if (
                legacy_csv_import.identity_match_key(r['csv'].get('customer'))
                == legacy_csv_import.identity_match_key(pair[0])
                and legacy_csv_import.identity_match_key(r['csv'].get('productName'))
                == legacy_csv_import.identity_match_key(pair[1])
                and r['status'] == 'create'
            ):
                sample_nos.append(r['rowNo'])
                break
    for r in blank_rows:
        if r['csv'].get('productName') == 'NPC白生地' and r['status'] == 'create':
            sample_nos.append(r['rowNo'])
            break
    for r in blank_rows:
        if r['csv'].get('productName') == '丈直し' and r['status'] == 'create':
            sample_nos.append(r['rowNo'])
            break

    trial = None
    if sample_nos:
        trial = legacy_csv_import.commit_preview(c, {
            **body,
            'previewFingerprint': preview['previewFingerprint'],
            'previewAt': preview['previewAt'],
            'token': 'verify-blank-sku-trial',
            'includeRowNos': sample_nos,
        }, db_path=str(path))
        c.commit()
        # 再プレビュー（重複防止）
        preview_after = legacy_csv_import.build_preview(c, body)
        re_create_uniform = sum(
            1 for r in preview_after['rows']
            if r.get('product', {}).get('category') == '制服'
            and r['status'] == 'create'
            and any(
                legacy_csv_import.identity_match_key(r['csv'].get('productName'))
                == legacy_csv_import.identity_match_key(p[1])
                for p in legacy_csv_import.UNIFORM_BLANK_SKU_PAIRS
            )
        )
    else:
        preview_after = None
        re_create_uniform = None

    report = {
        'csvPath': str(csv_path),
        'fileSize': len(blob),
        'encoding': enc,
        'rowCount': len(rows),
        'uploadCandidatePresent': UPLOAD_CANDIDATE.is_file(),
        'beforeEmptyDbReference': BEFORE_EMPTY_DB,
        'afterEmptyDbPreview': raw,
        'delta': {k: raw.get(k, 0) - BEFORE_EMPTY_DB.get(k, 0) for k in set(raw) | set(BEFORE_EMPTY_DB)},
        'blankSkuRows': len(blank_rows),
        'uniformBlankCreateOrOk': len([r for r in uniform if r['status'] in ('create', 'reuse', 'unchanged')]),
        'uniformBlankReview': len([r for r in uniform if r['status'] == 'needs_review']),
        'partsBlank': len(parts),
        'excluded': {
            'count': len(excluded),
            'rows': [
                {'rowNo': r['rowNo'], 'reason': r.get('excludeReason') or (r['reasons'][0] if r['reasons'] else ''),
                 'customer': r['csv'].get('customer'), 'supplier': r['csv'].get('supplier')}
                for r in excluded
            ],
        },
        'reviewReasonBuckets': dict(review_reasons),
        'fabricChecks': fabric_checks,
        'uniformPairsMatched': {
            f'{a} / {b}': sum(
                1 for r in blank_rows
                if legacy_csv_import.is_uniform_blank_sku_exception(r['csv'].get('customer'), r['csv'].get('productName'))
                and legacy_csv_import.identity_match_key(r['csv'].get('productName'))
                == legacy_csv_import.identity_match_key(b)
                and legacy_csv_import.identity_match_key(r['csv'].get('customer'))
                == legacy_csv_import.identity_match_key(a)
            )
            for a, b in legacy_csv_import.UNIFORM_BLANK_SKU_PAIRS
        },
        'trialImport': {
            'includeRowNos': sample_nos,
            'result': {
                'committedRowCount': trial.get('committedRowCount') if trial else None,
                'excludedRowCount': trial.get('excludedRowCount') if trial else None,
                'counts': trial.get('counts') if trial else None,
            } if trial else None,
            'dbProductsBlank': c.execute("SELECT COUNT(*) AS n FROM products WHERE code=''").fetchone()['n'],
            'dbLots': c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'],
            'repreviewUniformCreate': re_create_uniform,
            'productionCommit': False,
        },
        'lotsUnchangedByPreviewOnly': c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'] if not trial else 'see trial',
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    c.close()
    folder.cleanup()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
