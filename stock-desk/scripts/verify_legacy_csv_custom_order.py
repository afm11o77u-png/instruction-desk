#!/usr/bin/env python3
"""旧CSV 品番「別注」取込の検証（テスト用DBのみ。実運用本登録しない）。

  python3 scripts/verify_legacy_csv_custom_order.py
  python3 scripts/verify_legacy_csv_custom_order.py /path/to/item_report.csv
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
from collections import Counter, defaultdict
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

# PR #131 報告の空DBプレビュー（品番なし対応後）
BEFORE_BLANK_SKU_FIX = {
    'create': 14371,
    'reuse': 0,
    'qty_diff': 0,
    'unchanged': 0,
    'needs_review': 107,
    'hold': 263,
    'excluded': 1,
}


def preview_body(blob, name='item_report.csv'):
    return {
        'file': base64.b64encode(blob).decode('ascii'),
        'name': name,
        'basisDate': '2026-10-18',
        'defaultSiteName': '未確認',
        '_canWrite': True,
        '_actor': {'name': '別注検証'},
    }


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.is_file():
        raise SystemExit(f'CSV が見つかりません: {csv_path}')
    blob = csv_path.read_bytes()
    text, enc = legacy_csv_import.decode_csv_bytes(blob)
    fields, rows = legacy_csv_import.parse_csv_text(text)

    exact = [
        (no, item) for no, item in rows
        if legacy_csv_import.is_custom_order_code(item.get('商品コード'))
    ]
    partial = [
        (no, item) for no, item in rows
        if '別注' in stock.text(item.get('商品コード'))
        and not legacy_csv_import.is_custom_order_code(item.get('商品コード'))
    ]

    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    c.commit()

    body = preview_body(blob, csv_path.name)
    preview = legacy_csv_import.build_preview(c, body)
    c.commit()
    # プレビューは台帳・在庫を変えない
    assert c.execute('SELECT COUNT(*) AS n FROM lots').fetchone()['n'] == 0
    assert c.execute('SELECT COUNT(*) AS n FROM customers').fetchone()['n'] == 0

    raw = preview['countsRaw']
    custom_rows = [r for r in preview['rows'] if r.get('product', {}).get('customOrder')]
    custom_create = [r for r in custom_rows if r['status'] == 'create']
    by_cust_name = defaultdict(list)
    for r in custom_rows:
        key = (r['csv']['customer'], r['csv']['productName'])
        by_cust_name[key].append(r)

    # 試験取込: 別注の代表数行だけ（本番本登録なし）
    sample_keys = list(by_cust_name.keys())[:3]
    include = []
    for key in sample_keys:
        # 各商品の先頭サイズ1行
        include.append(by_cust_name[key][0]['rowNo'])
    # サイズ違いも含めて同一商品になるか見るため、先頭キーの別サイズがあれば追加
    if sample_keys:
        sizes = by_cust_name[sample_keys[0]]
        if len(sizes) > 1:
            include.append(sizes[1]['rowNo'])

    commit = legacy_csv_import.commit_preview(c, {
        **body,
        'previewFingerprint': preview['previewFingerprint'],
        'previewAt': preview['previewAt'],
        'token': 'verify-custom-order-1',
        'includeRowNos': include,
    }, db_path=str(path))
    c.commit()

    products = list(c.execute(
        "SELECT id,name,category FROM products WHERE code='別注' ORDER BY id"
    ))
    maps = c.execute('SELECT COUNT(*) AS n FROM legacy_csv_row_maps').fetchone()['n']

    # 再プレビュー（対応記録あり）
    preview2 = legacy_csv_import.build_preview(c, body)
    re_custom = [r for r in preview2['rows'] if r['rowNo'] in include]
    from_map = sum(1 for r in re_custom if r.get('product', {}).get('fromImportMap'))

    # 商品名整理後も対応から参照
    if products:
        pid = products[0]['id']
        old_name = products[0]['name']
        c.execute(
            "UPDATE products SET name=?, updated_at=? WHERE id=?",
            ('整理後名称', stock.stamp(), pid),
        )
        c.commit()
        preview3 = legacy_csv_import.build_preview(c, body)
        # 元CSV名でマップされる行
        mapped_rows = [
            r for r in preview3['rows']
            if r.get('product', {}).get('fromImportMap') and r['product'].get('id') == pid
        ]
        renamed_ok = len(mapped_rows) > 0
        # 元に戻す（検証DBのみ）
        c.execute(
            "UPDATE products SET name=?, updated_at=? WHERE id=?",
            (old_name, stock.stamp(), pid),
        )
        c.commit()
    else:
        renamed_ok = False

    report = {
        'csvPath': str(csv_path),
        'uploadCandidatePresent': UPLOAD_CANDIDATE.is_file(),
        'encoding': enc,
        'rowCount': len(rows),
        'exactCustomOrderRows': len(exact),
        'partialCodeWithCustomOrder': len(partial),
        'uniqueCustomerProductName': len(by_cust_name),
        'previewEmptyDb': {
            'countsRaw': raw,
            'counts': preview['counts'],
            'deltaFromBlankSkuReport': {
                k: raw.get(k, 0) - BEFORE_BLANK_SKU_FIX.get(k, 0)
                for k in BEFORE_BLANK_SKU_FIX
            },
        },
        'customOrderPreview': {
            'rows': len(custom_rows),
            'create': len(custom_create),
            'statuses': dict(Counter(r['status'] for r in custom_rows)),
            'allCreateAreUniform': all(
                r['product'].get('category') == '制服' for r in custom_create
            ),
        },
        'trialCommit': {
            'includeRowNos': include,
            'committedRowCount': commit.get('committedRowCount'),
            'productsCreated': commit.get('counts', {}).get('productsCreated'),
            'sizesAdded': commit.get('counts', {}).get('sizesAdded'),
            'openingCreated': commit.get('counts', {}).get('openingCreated'),
            'customOrderProductsInDb': len(products),
            'rowMaps': maps,
            'productionDbTouched': False,
        },
        'repreview': {
            'fromImportMapAmongCommitted': from_map,
            'renamedProductStillMapped': renamed_ok,
        },
        'notices': [
            '実運用DBへの本登録はしていない（一時テストDBのみ）',
            '既存商品の統合・色加工の自動分解は未実施',
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    c.close()
    folder.cleanup()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
