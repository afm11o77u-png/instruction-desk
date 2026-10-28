#!/usr/bin/env python3
"""部品・資材 → 加工サービス 移行候補の棚卸し（既定は読取のみ）。

使い方（ローカル DB。GitHub 上では DB を触らない）:

  python stock-desk/scripts/inventory_process_service_migration.py path/to/stock.sqlite3
  python stock-desk/scripts/inventory_process_service_migration.py path/to/stock.sqlite3 --json
  python stock-desk/scripts/inventory_process_service_migration.py path/to/stock.sqlite3 \\
      --apply-ids 12,34 --dry-run
  # 本番更新は明示が必要（レビュー後）:
  python ... --apply-ids 12,34 --commit

API（読取）: GET /api/products/process-service-migration/inventory
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_processing_service as proc_svc  # noqa: E402
import stock  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description='加工サービス移行候補の inventory（既定: 書込なし）')
    p.add_argument('db', help='stock.sqlite3 のパス')
    p.add_argument('--json', action='store_true', help='JSON で全部出力')
    p.add_argument('--include-leave', action='store_true', help='leave も一覧に含める')
    p.add_argument(
        '--apply-ids', default='',
        help='カンマ区切りの商品ID。候補のみ更新対象（--commit 無しなら dry-run）',
    )
    p.add_argument(
        '--commit', action='store_true',
        help='実際に category を加工サービスへ UPDATE（明示時のみ）',
    )
    p.add_argument(
        '--dry-run', action='store_true',
        help='--apply-ids 時に計画だけ表示（既定動作。--commit と併用不可）',
    )
    args = p.parse_args(argv)
    path = Path(args.db)
    if not path.is_file():
        print(f'DB がありません: {path}', file=sys.stderr)
        return 2

    c = stock.connect(path)
    try:
        stock.initialize(c)
        inv = proc_svc.inventory_process_service_migration(
            c, include_leave=bool(args.include_leave),
        )
        apply_ids = [
            int(x) for x in (args.apply_ids or '').replace(' ', '').split(',')
            if x.isdigit()
        ]
        result = {'inventory': inv}
        if apply_ids:
            if args.commit and args.dry_run:
                print('--commit と --dry-run は同時に使えません。', file=sys.stderr)
                return 2
            dry = not args.commit
            result['apply'] = proc_svc.apply_process_service_migration(
                c, apply_ids, dry_run=dry,
            )
            if args.commit and result['apply'].get('writes'):
                c.commit()
                print(
                    f"applied {len(result['apply']['applied'])} product(s) → 加工サービス",
                    file=sys.stderr,
                )
            elif apply_ids:
                print(
                    f"dry-run planned {len(result['apply']['planned'])} "
                    f"/ skipped {len(result['apply']['skipped'])}",
                    file=sys.stderr,
                )

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        print(inv['method'])
        print(
            f"alreadyProcessService={inv['alreadyProcessServiceCount']} "
            f"partsScanned={inv['partsScanned']} "
            f"candidates={inv['candidateCount']} "
            f"ambiguous={inv['ambiguousCount']} "
            f"leave={inv['leaveCount']}"
        )
        print('--- candidates (明確) ---')
        for item in inv['candidates']:
            print(
                f"  id={item['id']} name={item['name']!r} "
                f"supplier={item['supplierName']!r} "
                f"→ {item['suggestedServiceName']!r} ({item['reason']})"
            )
        print('--- ambiguous (レビュー) ---')
        for item in inv['ambiguous']:
            print(
                f"  id={item['id']} name={item['name']!r} "
                f"supplier={item['supplierName']!r} "
                f"code={item['code']!r} lotQty={item['lotQty']} "
                f"({item['reason']})"
            )
        if apply_ids and 'apply' in result:
            print('--- apply ---')
            print(json.dumps(result['apply'], ensure_ascii=False, indent=2))
        return 0
    finally:
        c.close()


if __name__ == '__main__':
    raise SystemExit(main())
