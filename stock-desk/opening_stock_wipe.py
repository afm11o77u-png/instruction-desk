"""初期在庫だけの全削除（検証用）。

商品の全削除と同じく、主管理者だけ、確認の文字、バックアップ、
画面の検索は無視、部分削除はしない。
初期在庫（opening のロット）だけを対象にする。
後から入出庫・貸出・発注で使われた初期在庫があると、全体を中止する。
通常の入出庫ロット、発注、旧QR、新QR、勤怠、商品マスタは消さない。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import stock

CONFIRM_TEXT = '初期在庫を全削除'
SCOPE_NOTE = (
    '画面の検索・絞り込みは無視し、登録済みの初期在庫をすべて対象にします。'
    '初期在庫として入ったロットだけを消します。'
    'その後に入庫・出庫・棚卸・訂正・貸出・発注で使われた初期在庫があると、一件も消さず中止します。'
    '通常の入出庫で増えた在庫、発注、旧QRの対応、新QR、商品・顧客・仕入先・場所、勤怠は残します。'
)
KEPT_LABELS = [
    '商品マスタ・サイズ・取扱',
    '顧客マスタ・仕入先マスタ・拠点と棚',
    '初期在庫以外の在庫ロットと入出庫履歴',
    '発注・貸出',
    '旧QRの対応',
    '新QR',
    '利用者・権限・設定',
    '勤怠・指示書デスク',
]


def ensure_schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS opening_stock_wipe_log (
      id INTEGER PRIMARY KEY,
      at TEXT NOT NULL,
      actor_name TEXT NOT NULL DEFAULT '',
      counts_json TEXT NOT NULL DEFAULT '{}',
      backup_path TEXT NOT NULL DEFAULT ''
    )''')


def require_owner(body):
    actor = body.get('_actor') if isinstance(body, dict) else None
    role = stock.text(actor.get('role')) if isinstance(actor, dict) else ''
    if role != 'owner':
        raise PermissionError('初期在庫の全削除は主管理者だけが実行できます。')


def require_confirm_text(body):
    typed = stock.text((body or {}).get('confirmText'))
    if typed != CONFIRM_TEXT:
        raise ValueError(f'確認のため、入力欄に「{CONFIRM_TEXT}」と正確に入力してください。')


class OpeningWipeBlocked(Exception):
    def __init__(self, blockers):
        self.blockers = list(blockers or [])
        self.message = (
            '後から使われた初期在庫があるため、初期在庫の全削除を中止しました。'
            '在庫・履歴・対応は一切削除していません。'
        )
        super().__init__(self.message)

    @property
    def payload(self):
        return {
            'error': self.message,
            'code': 'in_use',
            'deleted': False,
            'blockers': self.blockers,
        }


def _tables(c):
    return {r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _opening_lots(c):
    if 'lots' not in _tables(c):
        return []
    return list(c.execute(
        '''SELECT l.id, l.batch_id, l.customer_product_id, l.size_label, l.qty_received, l.qty_remaining,
                  l.condition, p.code AS product_code, p.name AS product_name
           FROM lots l
           LEFT JOIN customer_products cp ON cp.id = l.customer_product_id
           LEFT JOIN products p ON p.id = cp.product_id
           WHERE l.batch_id IS NOT NULL
           ORDER BY l.id'''
    ))


def _reasons_for_lot(c, lot, tables):
    reasons = []
    if int(lot['qty_remaining']) != int(lot['qty_received']):
        reasons.append('初期在庫の数量が、登録した数量から変わっています。')
    kinds = [
        row['kind'] for row in c.execute(
            'SELECT kind FROM moves WHERE lot_id=? ORDER BY id', (lot['id'],)
        )
    ] if 'moves' in tables else []
    extra = [kind for kind in kinds if kind != 'opening']
    if extra:
        reasons.append('初期在庫のあとに別の入出庫履歴があります（' + '、'.join(sorted(set(extra))) + '）。')
    if not kinds:
        reasons.append('初期在庫の登録履歴が見つかりません。')
    lot_id = int(lot['id'])
    if 'loan_lines' in tables and c.execute(
        'SELECT 1 FROM loan_lines WHERE lot_id=? LIMIT 1', (lot_id,)
    ).fetchone():
        reasons.append('貸出がこの初期在庫を参照しています。')
    if 'purchase_order_receipts' in tables:
        cols = {row['name'] for row in c.execute('PRAGMA table_info(purchase_order_receipts)')}
        if 'lot_id' in cols and c.execute(
            'SELECT 1 FROM purchase_order_receipts WHERE lot_id=? LIMIT 1', (lot_id,)
        ).fetchone():
            reasons.append('発注の入庫がこの初期在庫を参照しています。')
    return reasons


def collect_blockers(c):
    tables = _tables(c)
    blockers = []
    for lot in _opening_lots(c):
        reasons = _reasons_for_lot(c, lot, tables)
        if reasons:
            blockers.append({
                'lotId': lot['id'],
                'code': lot['product_code'] or '',
                'name': lot['product_name'] or '',
                'size': lot['size_label'] or '',
                'reasons': reasons,
            })
    return blockers


def count_targets(c):
    lots = _opening_lots(c)
    lot_ids = [int(row['id']) for row in lots]
    batch_ids = {int(row['batch_id']) for row in lots if row['batch_id'] is not None}
    moves = 0
    if lot_ids and 'moves' in _tables(c):
        marks = ','.join('?' for _ in lot_ids)
        moves = c.execute(
            f'SELECT COUNT(*) AS n FROM moves WHERE lot_id IN ({marks})', lot_ids
        ).fetchone()['n']
    return {
        'openingLots': len(lot_ids),
        'openingMoves': int(moves),
        'openingBatches': len(batch_ids),
    }


def kept_snapshot(c):
    tables = _tables(c)
    snap = {}
    for table in (
        'products', 'product_sizes', 'customer_products', 'customers', 'suppliers', 'locations',
        'purchase_orders', 'purchase_order_lines', 'qr_stock_links', 'stock_qr_codes',
    ):
        if table in tables:
            snap[table] = int(c.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n'])
    if 'lots' in tables:
        snap['otherLots'] = int(c.execute(
            'SELECT COUNT(*) AS n FROM lots WHERE batch_id IS NULL'
        ).fetchone()['n'])
    return snap


def list_audit(c, limit=20):
    ensure_schema(c)
    rows = []
    for row in c.execute(
        'SELECT * FROM opening_stock_wipe_log ORDER BY id DESC LIMIT ?', (int(limit),)
    ):
        try:
            counts = json.loads(row['counts_json'] or '{}')
        except json.JSONDecodeError:
            counts = {}
        rows.append({
            'at': row['at'],
            'actorName': row['actor_name'] or '',
            'counts': counts,
            'backupPath': row['backup_path'] or '',
        })
    return rows


def preview_wipe(c, body=None):
    body = body or {}
    require_owner(body)
    ensure_schema(c)
    blockers = collect_blockers(c)
    return {
        'confirmText': CONFIRM_TEXT,
        'scopeNote': SCOPE_NOTE,
        'kept': list(KEPT_LABELS),
        'counts': count_targets(c),
        'blockers': blockers,
        'canWipe': len(blockers) == 0,
        'history': list_audit(c),
        'keptSnapshot': kept_snapshot(c),
        'jointImport': False,
    }


def _clear_lot_maps(c, lot_ids):
    if 'legacy_csv_row_maps' not in _tables(c) or not lot_ids:
        return 0
    removed = set(lot_ids)
    changed = 0
    for row in c.execute('SELECT id, lot_ids_json FROM legacy_csv_row_maps'):
        try:
            current = json.loads(row['lot_ids_json'] or '[]')
        except json.JSONDecodeError:
            current = []
        if not isinstance(current, list):
            continue
        kept = [item for item in current if int(item) not in removed]
        if kept != current:
            c.execute(
                'UPDATE legacy_csv_row_maps SET lot_ids_json=? WHERE id=?',
                (json.dumps(kept), row['id']),
            )
            changed += 1
    return changed


def execute_wipe(c, body, *, db_path=None):
    require_owner(body)
    if body.get('_canWrite') is False:
        raise PermissionError('閲覧専用のため、初期在庫の全削除はできません。')
    require_confirm_text(body)
    ensure_schema(c)
    blockers = collect_blockers(c)
    if blockers:
        raise OpeningWipeBlocked(blockers)
    before = count_targets(c)
    before_kept = kept_snapshot(c)
    backup_path = ''
    if db_path:
        try:
            dest = stock.backup_sqlite(Path(db_path), tag='pre-opening-stock-wipe')
            backup_path = str(dest or '')
        except stock.BackupError:
            raise
        except (OSError, sqlite3.Error) as e:
            raise stock.BackupError(stock.BACKUP_FAILED_MESSAGE, cause=e) from e
        if not backup_path:
            raise stock.BackupError(stock.BACKUP_FAILED_MESSAGE)
        blockers = collect_blockers(c)
        if blockers:
            raise OpeningWipeBlocked(blockers)
    lots = _opening_lots(c)
    lot_ids = [int(row['id']) for row in lots]
    batch_ids = sorted({int(row['batch_id']) for row in lots if row['batch_id'] is not None})
    deleted = {'openingLots': 0, 'openingMoves': 0, 'openingBatches': 0, 'legacyCsvLotRefs': 0}
    if lot_ids:
        marks = ','.join('?' for _ in lot_ids)
        if 'moves' in _tables(c):
            cur = c.execute(f'DELETE FROM moves WHERE lot_id IN ({marks})', lot_ids)
            deleted['openingMoves'] = int(cur.rowcount or 0)
        deleted['legacyCsvLotRefs'] = _clear_lot_maps(c, set(lot_ids))
        cur = c.execute(f'DELETE FROM lots WHERE id IN ({marks}) AND batch_id IS NOT NULL', lot_ids)
        deleted['openingLots'] = int(cur.rowcount or 0)
    if batch_ids and 'opening_batches' in _tables(c):
        marks = ','.join('?' for _ in batch_ids)
        still = {
            int(row['batch_id'])
            for row in c.execute(
                f'SELECT DISTINCT batch_id FROM lots WHERE batch_id IN ({marks})', batch_ids
            )
            if row['batch_id'] is not None
        }
        removable = [bid for bid in batch_ids if bid not in still]
        if removable:
            marks = ','.join('?' for _ in removable)
            cur = c.execute(f'DELETE FROM opening_batches WHERE id IN ({marks})', removable)
            deleted['openingBatches'] = int(cur.rowcount or 0)
    if count_targets(c)['openingLots'] != 0:
        raise RuntimeError('初期在庫の削除が完了しませんでした。変更は取り消します。')
    if kept_snapshot(c) != before_kept:
        raise RuntimeError('残すべきマスタや初期在庫以外の件数が変わったため、削除を取り消します。')
    when = stock.stamp()
    actor = stock.text((body.get('_actor') or {}).get('name')) if isinstance(body.get('_actor'), dict) else ''
    c.execute(
        'INSERT INTO opening_stock_wipe_log(at, actor_name, counts_json, backup_path) VALUES(?,?,?,?)',
        (when, actor, json.dumps(deleted, ensure_ascii=False), backup_path),
    )
    return {
        'ok': True,
        'deleted': True,
        'actorName': actor,
        'at': when,
        'backupPath': backup_path,
        'counts': deleted,
        'beforeCounts': before,
        'kept': list(KEPT_LABELS),
        'scopeNote': SCOPE_NOTE,
        'jointImport': False,
    }
