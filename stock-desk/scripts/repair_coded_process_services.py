#!/usr/bin/env python3
"""品番付きの加工サービスを棚卸し・修復（既定は読取のみ。DB を開くのも読取専用）。

  python stock-desk/scripts/repair_coded_process_services.py path/to/stock.sqlite3
  python ... path/to/stock.sqlite3 --ids 12,34            # 計画だけ表示
  python ... path/to/stock.sqlite3 --ids 12,34 --commit   # バックアップ後に更新
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_processing_service as proc_svc  # noqa: E402
import stock  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description='品番付き加工サービスの修復（既定: 書込なし）')
    p.add_argument('db')
    p.add_argument('--ids', default='', help='直す商品ID（カンマ区切り）')
    p.add_argument('--commit', action='store_true', help='実際に更新する（バックアップを作成）')
    args = p.parse_args(argv)
    path = Path(args.db)
    if not path.is_file():
        print(f'DB がありません: {path}', file=sys.stderr)
        return 2
    ids = [x for x in args.ids.replace(' ', '').split(',') if x]
    if args.commit:
        if not ids:
            print('--commit には --ids が必要です', file=sys.stderr)
            return 2
        backup = stock.backup_sqlite(path, tag='pre-coded-process-repair')
        print(f'バックアップ: {backup}')
        c = sqlite3.connect(str(path))
    else:
        c = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    try:
        if ids:
            out = proc_svc.apply_coded_process_service_repair(c, ids, dry_run=not args.commit)
        else:
            out = proc_svc.inventory_coded_process_services(c)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    finally:
        c.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
