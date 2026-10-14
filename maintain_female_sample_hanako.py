#!/usr/bin/env python3
"""女性区分ネーム見本の架空氏名を山田花子へ（起動と独立した保守）。

通常の起動・schema・catalog 取込では画像変換しない。
このスクリプトだけが DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1 のとき実行する。

使い方（会社PC / E:\\instruction-desk）:

  set DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1
  py -3 -X utf8 maintain_female_sample_hanako.py

  または: 女性見本花子保守.cmd

画像生成中は DB 書き込みトランザクションを保持しない。
完了後に短いトランザクションで参照先だけ更新する。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import names

DATA = Path(os.environ.get('INSTRUCTION_DATA', str(ROOT / 'data')))
DB = DATA / 'instructions.sqlite3'


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=60000')
    return c


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    dry = '--dry-run' in argv
    if dry:
        print('dry-run は候補一覧のみ（未実装の簡易確認）。実際の変換はオプション無しで実行してください。')
    if not names.female_image_maintenance_allowed():
        print(
            f'[中止] 環境変数 {names.FEMALE_MAINTENANCE_ENV}=1 を設定してから実行してください。',
            file=sys.stderr,
        )
        print(
            '通常起動では変換しません。例:',
            file=sys.stderr,
        )
        print(
            f'  set {names.FEMALE_MAINTENANCE_ENV}=1',
            file=sys.stderr,
        )
        print(
            '  py -3 -X utf8 maintain_female_sample_hanako.py',
            file=sys.stderr,
        )
        return 2

    if not DB.is_file():
        print(f'[中止] DB が見つかりません: {DB}', file=sys.stderr)
        return 1

    names.bind(DATA)

    def on_progress(info):
        phase = info.get('phase')
        if phase == 'image':
            print(
                f"[画像] {info.get('index')}/{info.get('total')} "
                f"sample={info.get('sampleId')} {info.get('customer')} / {info.get('format')}",
                flush=True,
            )
        elif phase == 'commit':
            print(
                f"[DB] {info.get('index')}/{info.get('total')} sample={info.get('sampleId')}",
                flush=True,
            )

    print(f'データ: {DATA}')
    print(f'DB: {DB}')
    print(f'ルール版: {names.FEMALE_HANAKO_RULE_VERSION}')
    report = names.run_female_sample_hanako_maintenance(
        connect, DATA, require_env=True, progress_callback=on_progress,
    )
    out = DATA / names.FEMALE_HANAKO_REPORT
    print(json.dumps({
        'changed': report.get('changed'),
        'failed': report.get('failed'),
        'conflicts': report.get('conflicts'),
        'skippedComplete': report.get('skippedComplete'),
        'skipped': report.get('skipped'),
        'reason': report.get('reason'),
        'ruleVersion': report.get('ruleVersion'),
        'backupDir': report.get('backupDir'),
        'report': str(out) if out.is_file() else '',
    }, ensure_ascii=False, indent=2))
    if report.get('failed'):
        print(f"失敗 {report['failed']} 件（元のまま・再実行可）。詳細は {out}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
