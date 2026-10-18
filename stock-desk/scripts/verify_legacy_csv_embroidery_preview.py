#!/usr/bin/env python3
"""旧CSV加工照合の修正前後プレビュー比較（テスト用DBのみ。本登録しない）。

主検証CSV:
  /cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/internal/item_report.csv

使い方:
  python3 scripts/verify_legacy_csv_embroidery_preview.py
  python3 scripts/verify_legacy_csv_embroidery_preview.py /path/to/item_report.csv
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


def old_handle_would_block(c, customer_id, product_id):
    """修正前ロジック: 有効取扱が加工なし1件だけだと reasons 付き create → 要確認化。"""
    if not customer_id or not product_id:
        return False, ''
    rows = list(c.execute(
        '''SELECT id,embroidery,active FROM customer_products
           WHERE customer_id=? AND product_id=? ORDER BY id''',
        (customer_id, product_id),
    ))
    if not rows:
        return False, ''
    pending = [
        r for r in rows
        if stock.normalize_embroidery(r['embroidery']) == legacy_csv_import.PENDING_EMBROIDERY
        and r['active']
    ]
    if len(pending) == 1:
        return False, ''
    active = [r for r in rows if r['active']]
    if len(active) == 1 and stock.normalize_embroidery(active[0]['embroidery']) == '':
        return True, 'existing_empty_embroidery'
    return False, ''


def reason_bucket(reasons):
    text = ' '.join(reasons or [])
    if '品番（商品コード）が空' in text or '品番（商品コード）が空です' in text:
        return 'empty_code'
    if '「＊」は中古' in text:
        return 'star_vs_new'
    if '停止中' in text:
        return 'stopped'
    if '取扱が複数' in text or '加工仕様は「' in text:
        return 'embroidery_ambiguous'
    if '㈱' in text or '表記差' in text:
        return 'company_form'
    if '同じ品番の商品が複数' in text:
        return 'product_dup'
    if '仕入先が既存と異なります' in text:
        return 'supplier_conflict'
    if '新古' in text:
        return 'condition'
    if reasons:
        return 'other'
    return 'none'


def preview_body(blob, name='item_report.csv'):
    return {
        'file': base64.b64encode(blob).decode('ascii'),
        'name': name,
        'basisDate': '2026-10-17',
        'defaultSiteName': '未確認',
        '_canWrite': True,
        '_actor': {'name': '検証（本登録なし）'},
    }


def seed_empty_embroidery_from_csv(c, rows, *, limit=400):
    """CSV先頭から顧客×品番を登録し、取扱を加工なし1件にする（本番相当の不一致を再現）。"""
    stock.ensure_unconfirmed_site(c)
    seen = set()
    seeded = 0
    for row_no, item in rows:
        if seeded >= limit:
            break
        code = stock.text(item.get('商品コード'))
        cust = stock.text(item.get('顧客'))
        name = stock.text(item.get('商品名'))
        sup = stock.text(item.get('仕入先')) or '検証仕入'
        size = (item.get('サイズ') or '').strip() if isinstance(item.get('サイズ'), str) else str(item.get('サイズ') or '').strip()
        if not code or not cust or not name or not size:
            continue
        key = (cust, code)
        if key in seen:
            continue
        seen.add(key)
        cu = c.execute('SELECT id FROM customers WHERE name=?', (cust,)).fetchone()
        if not cu:
            cu = stock.save_customer(c, {'name': cust, 'active': True})
        else:
            cu = {'id': cu['id']}
        su = c.execute('SELECT id FROM suppliers WHERE name=?', (sup,)).fetchone()
        if not su:
            su = stock.save_supplier(c, {'name': sup, 'active': True})
        else:
            su = {'id': su['id']}
        prod = c.execute('SELECT id FROM products WHERE code=?', (code,)).fetchone()
        if not prod:
            prod = stock.save_product(c, {
                'code': code, 'name': name, 'color': '', 'supplierId': su['id'],
                'active': True, 'sizes': [size],
                'links': [{
                    'customerId': cu['id'], 'embroidery': '',
                    'isStandard': True, 'active': True,
                }],
            })
        else:
            # 既存商品に加工なし取扱を1件だけ付与
            exists = c.execute(
                '''SELECT id FROM customer_products
                   WHERE customer_id=? AND product_id=? AND active=1''',
                (cu['id'], prod['id']),
            ).fetchone()
            if not exists:
                stock.ensure_customer_product(c, {
                    'customerId': cu['id'], 'productId': prod['id'], 'embroidery': '',
                })
        seeded += 1
    c.commit()
    return seeded


def summarize(preview, c=None):
    raw = preview['countsRaw']
    buckets = Counter()
    old_block = 0
    new_reuse_empty = 0
    for row in preview['rows']:
        buckets[reason_bucket(row.get('reasons'))] += 1
        h = row.get('handle') or {}
        if (
            h.get('action') == 'reuse'
            and h.get('embroideryLabel') == '加工なし'
            and h.get('pendingEmbroiderySort')
        ):
            new_reuse_empty += 1
        if c is not None:
            blocked, _ = old_handle_would_block(
                c,
                (row.get('customer') or {}).get('id'),
                (row.get('product') or {}).get('id'),
            )
            # 修正前は blocked かつ他理由が無くても要確認化していた
            if blocked and row['status'] != 'needs_review':
                # 新ロジックでは再利用等に変わった行
                pass
            if blocked:
                old_block += 1
    return {
        'countsRaw': raw,
        'counts': preview['counts'],
        'reasonBuckets': dict(buckets),
        'rowsThatOldLogicWouldBlockOnEmptyEmbroidery': old_block,
        'rowsReusingEmptyEmbroideryWithPendingSort': new_reuse_empty,
        'rowCount': preview['rowCount'],
        'encoding': preview['encoding'],
        'fileSha256': preview['fileSha256'],
        'fileSize': preview['fileSize'],
    }


def pick_csv(argv):
    if len(argv) > 1:
        p = Path(argv[1])
        if not p.is_file():
            raise SystemExit(f'CSVが見つかりません: {p}')
        return p, 'cli'
    candidates = []
    if UPLOAD_CANDIDATE.is_file():
        candidates.append(('upload', UPLOAD_CANDIDATE))
    if DEFAULT_CSV.is_file():
        candidates.append(('store', DEFAULT_CSV))
    if not candidates:
        raise SystemExit('item_report.csv が見つかりません')
    # ユーザー手元の (1).csv 相当を優先。無ければ store。
    if candidates[0][0] == 'upload':
        return candidates[0][1], 'upload'
    return candidates[0][1], 'store'


def main():
    csv_path, source = pick_csv(sys.argv)
    blob = csv_path.read_bytes()
    text, enc = legacy_csv_import.decode_csv_bytes(blob)
    fields, rows = legacy_csv_import.parse_csv_text(text)
    out = {
        'csvPath': str(csv_path),
        'csvSource': source,
        'csvSize': len(blob),
        'encoding': enc,
        'dataRows': len(rows),
        'header': fields,
        'uploadCandidatePresent': UPLOAD_CANDIDATE.is_file(),
        'productionCommit': False,
    }

    # 1) 空のテストDB
    folder1 = tempfile.TemporaryDirectory()
    path1 = Path(folder1.name) / 'empty.sqlite3'
    c1 = stock.connect(path1)
    stock.initialize(c1)
    stock.ensure_unconfirmed_site(c1)
    c1.commit()
    p_empty = legacy_csv_import.build_preview(c1, preview_body(blob, csv_path.name))
    out['emptyDb'] = summarize(p_empty, c1)
    c1.close()
    folder1.cleanup()

    # 2) 加工なし取扱を種まきしたテストDB（修正対象の不一致を再現）
    folder2 = tempfile.TemporaryDirectory()
    path2 = Path(folder2.name) / 'seeded.sqlite3'
    c2 = stock.connect(path2)
    stock.initialize(c2)
    seeded_n = seed_empty_embroidery_from_csv(c2, rows, limit=500)
    p_seeded = legacy_csv_import.build_preview(c2, preview_body(blob, csv_path.name))
    seeded_summary = summarize(p_seeded, c2)
    seeded_summary['seededCustomerProductPairs'] = seeded_n
    # 修正前相当: 種まき分は旧ロジックなら要確認化していた件数
    old_blocked = seeded_summary['rowsThatOldLogicWouldBlockOnEmptyEmbroidery']
    new_review = seeded_summary['countsRaw'].get('needs_review', 0)
    new_reuse = seeded_summary['rowsReusingEmptyEmbroideryWithPendingSort']
    seeded_summary['comparison'] = {
        'oldWouldBlockEmptyEmbroideryRows': old_blocked,
        'newReuseEmptyEmbroideryRows': new_reuse,
        'newNeedsReviewTotal': new_review,
        'note': (
            '種まきした顧客×品番は既存取扱が加工なし1件。'
            '修正前は自動付与「加工未整理」不一致で要確認化。'
            '修正後は既存再利用（加工仕様未変更）。'
        ),
    }
    out['seededEmptyEmbroideryDb'] = seeded_summary
    # 本登録しないことを確認
    out['seededDbLotsAfterPreviewOnly'] = c2.execute(
        'SELECT COUNT(*) AS n FROM lots'
    ).fetchone()['n']
    c2.close()
    folder2.cleanup()

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


if __name__ == '__main__':
    main()
