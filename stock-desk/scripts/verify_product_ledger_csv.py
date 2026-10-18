#!/usr/bin/env python3
"""商品台帳CSV取込の検証（テスト用DBのみ。実運用本登録しない）。

  python3 scripts/verify_product_ledger_csv.py
  python3 scripts/verify_product_ledger_csv.py /path/to/商品台帳.csv
"""
from __future__ import annotations

import base64
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import sample_data  # noqa: E402
import stock  # noqa: E402

DEFAULT_CSV = Path(
    '/cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/media/商品台帳.csv'
)


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.is_file():
        print('CSV not found:', csv_path)
        return 1

    blob = csv_path.read_bytes()
    text, enc = pli.decode_csv_bytes(blob)
    fields, rows = pli.parse_csv_text(text)
    print('encoding:', enc)
    print('fields:', fields)
    print('rows:', len(rows))
    print('BOM:', blob.startswith(b'\xef\xbb\xbf'))

    folder = tempfile.TemporaryDirectory()
    path = Path(folder.name) / 'stock.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    sample_data.seed(c)
    c.execute("INSERT INTO settings VALUES('seeded','1')")
    # サンプルに初期在庫がある想定でスナップショット
    before = pli.stock_snapshot(c)
    print('stock before:', before)

    # 実ファイル全件プレビュー（DB変更なし）
    body = {
        'file': base64.b64encode(blob).decode('ascii'),
        'name': csv_path.name,
        '_canWrite': True,
        '_actor': {'name': 'verify'},
    }
    preview = pli.build_preview(c, body)
    c.commit()
    mid = pli.stock_snapshot(c)
    assert mid == before, (before, mid)
    print('preview counts:', preview['countsRaw'])
    print('review:', preview['reviewCount'], 'encoding:', preview['encoding'])

    # 小さなサブセットで本登録（先頭の確定可能行だけ）
    # 実ファイル全文の本登録はしない（時間がかかる・実データ相当のため）
    header = ','.join(fields)
    subset_lines = [header]
    commit_candidates = []
    for row_no, item in rows[:80]:
        # 別注・空品番・＊は多様に拾う
        code = (item.get('商品コード') or '').strip()
        name = (item.get('商品名') or '')
        if code == '別注' or not code or name.startswith('＊') or name.startswith('*'):
            commit_candidates.append((row_no, item))
        elif len(commit_candidates) < 12:
            commit_candidates.append((row_no, item))
        if len(commit_candidates) >= 20:
            break
    # 行番号を保つため元CSVの該当行を再構築するのは難しいので、tiny CSV を作る
    tiny = [header]
    for _, item in commit_candidates:
        tiny.append(','.join([
            '"' + (item.get(k) or '').replace('"', '""') + '"'
            for k in fields
        ]))
    tiny_text = '\n'.join(tiny)
    tiny_body = {
        'file': base64.b64encode(('\ufeff' + tiny_text).encode('utf-8')).decode('ascii'),
        'name': 'subset.csv',
        '_canWrite': True,
        '_actor': {'name': 'verify'},
    }
    p2 = pli.build_preview(c, tiny_body)
    # 要確認は forceCreate で解消（検証用）
    selections = {}
    for r in p2['rows']:
        if r['status'] == 'needs_review':
            selections[str(r['rowNo'])] = {
                'forceCreate': True,
                'matchGroupKey': r['matchGroupKey'],
            }
    if selections:
        p2 = pli.build_preview(c, {**tiny_body, 'selections': selections})
    print('subset preview:', p2['countsRaw'], 'review', p2['reviewCount'])
    if p2['reviewCount']:
        print('WARN: review remains; skip commit of subset')
    else:
        result = pli.commit_preview(c, {
            **tiny_body,
            'selections': selections,
            'previewFingerprint': p2['previewFingerprint'],
            'previewAt': p2['previewAt'],
            'token': 'verify-pl-subset-1',
        }, db_path=str(path))
        c.commit()
        after = pli.stock_snapshot(c)
        print('commit counts:', result['counts'])
        print('stock after:', after)
        assert after == before, (before, after)
        assert result['stockUnchanged']

        # 再取込
        p3 = pli.build_preview(c, {**tiny_body, 'selections': selections})
        print('reimport unchanged-ish:', p3['countsRaw'])
        result2 = pli.commit_preview(c, {
            **tiny_body,
            'selections': selections,
            'previewFingerprint': p3['previewFingerprint'],
            'previewAt': p3['previewAt'],
            'token': 'verify-pl-subset-1',
        }, db_path=str(path))
        assert result2.get('already')
        assert pli.stock_snapshot(c) == before

    c.close()
    folder.cleanup()
    print('OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
