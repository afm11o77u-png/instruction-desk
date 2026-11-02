"""商品登録の全削除（検証用）。

商品・サイズ・仕入先リンク・顧客取扱・再取込を阻むマッピングだけを消す。
在庫・履歴・発注など業務データや顧客／仕入先マスタは残す。
参照があると一切削除せず中止する（部分削除なし）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import stock

CONFIRM_TEXT = '商品登録を全削除'

SCOPE_NOTE = (
    '画面の検索・仕入先フィルタは無視し、停止中・統合済みを含む商品登録全体が対象です。'
    '顧客マスタ・仕入先マスタ・拠点／棚・在庫・ロット・入出庫／棚卸履歴・発注・'
    '利用者／権限／設定、および指示書デスク側のデータは消しません。'
)

KEPT_LABELS = [
    '顧客マスタ',
    '仕入先マスタ',
    '拠点・棚',
    '期首在庫・現在庫・ロット',
    '移動／訂正／棚卸などの履歴',
    '発注とその他の業務データ',
    '利用者・権限・設定',
    '指示書デスクのDB・顧客／商品情報・ネーム了承状態',
]


SCOPES = {
    'parts': '部品',
    'process': '加工',
    'uniform': '制服',
}


def scope_of(body):
    raw = stock.text((body or {}).get('scope'))
    if raw and raw not in SCOPES:
        raise ValueError('削除する区分を選んでください。')
    return raw


def scope_label(scope):
    return SCOPES.get(scope) or '商品登録全体'


def scope_product_ids(c, scope):
    """区分の商品ID。制服は部品・資材／加工サービス以外（カテゴリ空欄を含む）。"""
    if not scope:
        return None
    if scope == 'parts':
        sql, args = 'SELECT id FROM products WHERE COALESCE(category,\'\')=?', (stock.CATEGORY_PARTS,)
    elif scope == 'process':
        sql, args = 'SELECT id FROM products WHERE COALESCE(category,\'\')=?', (stock.CATEGORY_PROCESS_SERVICE,)
    else:
        sql, args = (
            'SELECT id FROM products WHERE COALESCE(category,\'\') NOT IN (?,?)',
            (stock.CATEGORY_PARTS, stock.CATEGORY_PROCESS_SERVICE),
        )
    return {int(r['id']) for r in c.execute(sql, args)}


def _in(ids):
    ids = sorted(ids)
    return '(' + ','.join(str(int(i)) for i in ids) + ')' if ids else '(NULL)'


class RegistryWipeBlocked(Exception):
    """商品が業務データから参照されているため削除できない。"""

    def __init__(self, blockers, message=None):
        self.blockers = list(blockers or [])
        self.message = message or (
            '在庫・履歴・発注などから参照されている商品があるため、'
            '商品登録の全削除を中止しました。関連データは一切削除していません。'
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


def _count(c, sql, params=()):
    row = c.execute(sql, params).fetchone()
    if row is None:
        return 0
    return int(row[0] if not hasattr(row, 'keys') else row['n'])


def require_owner(body):
    actor = body.get('_actor') if isinstance(body, dict) else None
    role = ''
    if isinstance(actor, dict):
        role = stock.text(actor.get('role'))
    if role != 'owner':
        raise PermissionError('商品登録の全削除は主管理者だけが実行できます。')


def require_confirm_text(body):
    typed = stock.text((body or {}).get('confirmText'))
    if typed != CONFIRM_TEXT:
        raise ValueError(
            f'確認のため、入力欄に「{CONFIRM_TEXT}」と正確に入力してください。'
        )


def actor_name(body):
    actor = (body or {}).get('_actor') if isinstance(body, dict) else None
    if isinstance(actor, dict) and stock.text(actor.get('name')):
        return stock.text(actor.get('name'))
    return stock.text((body or {}).get('actorName')) or '未記録'


def _product_label_map(c):
    out = {}
    for r in c.execute('SELECT id, code, name, color, category FROM products'):
        out[int(r['id'])] = {
            'productId': int(r['id']),
            'code': r['code'] or '',
            'codeDisplay': stock.product_code_display(r['code'], r['category'] or ''),
            'name': r['name'] or '',
            'color': r['color'] or '',
        }
    return out


def _add_blocker(bucket, labels, product_id, reason):
    pid = int(product_id)
    item = bucket.get(pid)
    if item is None:
        meta = labels.get(pid) or {
            'productId': pid,
            'code': '',
            'codeDisplay': '',
            'name': f'(削除済みまたは不明 ID {pid})',
            'color': '',
        }
        item = {
            'productId': meta['productId'],
            'code': meta.get('code') or '',
            'codeDisplay': meta.get('codeDisplay') or meta.get('code') or '',
            'name': meta.get('name') or '',
            'color': meta.get('color') or '',
            'reasons': [],
        }
        bucket[pid] = item
    if reason not in item['reasons']:
        item['reasons'].append(reason)


def _parse_lot_ids(raw):
    text = stock.text(raw)
    if not text:
        return []
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    ids = []
    for x in data:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    return ids


def collect_blockers(c, scope=''):
    """参照があると削除できない商品の一覧（部分削除なしの判定用）。"""
    ids = scope_product_ids(c, scope)
    items = _collect_blockers_all(c)
    if ids is None:
        return items
    return [b for b in items if b['productId'] in ids]


def _collect_blockers_all(c):
    labels = _product_label_map(c)
    bucket = {}
    tables = _tables(c)

    if 'lots' in tables and 'customer_products' in tables:
        for r in c.execute(
            '''SELECT DISTINCT cp.product_id AS product_id,
                      COUNT(l.id) AS lot_count,
                      COALESCE(SUM(CASE WHEN l.qty_remaining != 0 THEN 1 ELSE 0 END), 0) AS nonzero_lots
               FROM lots l
               JOIN customer_products cp ON cp.id = l.customer_product_id
               GROUP BY cp.product_id'''
        ):
            n = int(r['lot_count'] or 0)
            nz = int(r['nonzero_lots'] or 0)
            reason = (
                f'在庫ロットが {n} 件参照しています'
                + ('（数量0のロット含む）' if nz == 0 else '')
            )
            _add_blocker(bucket, labels, r['product_id'], reason)

    if 'moves' in tables and 'lots' in tables and 'customer_products' in tables:
        for r in c.execute(
            '''SELECT DISTINCT cp.product_id AS product_id, COUNT(m.id) AS move_count
               FROM moves m
               JOIN lots l ON l.id = m.lot_id
               JOIN customer_products cp ON cp.id = l.customer_product_id
               GROUP BY cp.product_id'''
        ):
            _add_blocker(
                bucket, labels, r['product_id'],
                f'入出庫／棚卸などの履歴が {int(r["move_count"] or 0)} 件あります',
            )

    if 'purchase_order_lines' in tables:
        for r in c.execute(
            '''SELECT product_id, COUNT(*) AS n
               FROM purchase_order_lines
               GROUP BY product_id'''
        ):
            _add_blocker(
                bucket, labels, r['product_id'],
                f'発注明細が {int(r["n"] or 0)} 件参照しています',
            )

    if 'loan_lines' in tables and 'lots' in tables and 'customer_products' in tables:
        for r in c.execute(
            '''SELECT DISTINCT cp.product_id AS product_id, COUNT(ll.id) AS n
               FROM loan_lines ll
               JOIN lots l ON l.id = ll.lot_id
               JOIN customer_products cp ON cp.id = l.customer_product_id
               GROUP BY cp.product_id'''
        ):
            _add_blocker(
                bucket, labels, r['product_id'],
                f'貸出明細が {int(r["n"] or 0)} 件参照しています',
            )

    # 旧CSVマップが残ロットへ紐づく場合は追跡性を壊せないので中止
    if 'legacy_csv_row_maps' in tables and 'lots' in tables:
        for r in c.execute(
            '''SELECT id, product_id, lot_ids_json FROM legacy_csv_row_maps
               WHERE product_id IS NOT NULL'''
        ):
            lot_ids = _parse_lot_ids(r['lot_ids_json'])
            if not lot_ids:
                continue
            placeholders = ','.join('?' * len(lot_ids))
            exists = c.execute(
                f'SELECT COUNT(*) AS n FROM lots WHERE id IN ({placeholders})',
                lot_ids,
            ).fetchone()['n']
            if int(exists or 0) > 0:
                _add_blocker(
                    bucket, labels, r['product_id'],
                    '旧CSV取込マップが残存ロットと結びついており、'
                    'マップ削除だと在庫／履歴の追跡性が壊れます',
                )

    return sorted(bucket.values(), key=lambda x: x['productId'])


def count_targets(c, scope=''):
    ids = scope_product_ids(c, scope)
    if ids is not None:
        return _count_scoped(c, ids)
    tables = _tables(c)

    def safe(table, sql='SELECT COUNT(*) AS n FROM {t}'):
        if table not in tables:
            return 0
        return _count(c, sql.format(t=table))

    legacy_maps = 0
    if 'legacy_csv_row_maps' in tables:
        legacy_maps = _count(
            c,
            'SELECT COUNT(*) AS n FROM legacy_csv_row_maps WHERE product_id IS NOT NULL',
        )

    return {
        'products': safe('products'),
        'productSizes': safe('product_sizes'),
        'productSuppliers': safe('product_suppliers'),
        'customerProducts': safe('customer_products'),
        'customerProductProcesses': safe('customer_product_processes'),
        'productMerges': safe('product_merges'),
        'productMergeSources': safe('product_merge_sources'),
        'productMatchAliases': safe('product_match_aliases'),
        'productLedgerRowMaps': safe('product_ledger_row_maps'),
        'makerSkuCorrections': safe('maker_sku_corrections'),
        'legacyCsvRowMaps': legacy_maps,
    }


def _handle_ids(c, ids):
    if 'customer_products' not in _tables(c):
        return set()
    return {int(r['id']) for r in c.execute(
        f'SELECT id FROM customer_products WHERE product_id IN {_in(ids)}')}


def _count_scoped(c, ids):
    tables = _tables(c)
    hids = _handle_ids(c, ids)
    p, h = _in(ids), _in(hids)

    def safe(table, where):
        if table not in tables:
            return 0
        return _count(c, f'SELECT COUNT(*) AS n FROM {table} WHERE {where}')

    return {
        'products': safe('products', f'id IN {p}'),
        'productSizes': safe('product_sizes', f'product_id IN {p}'),
        'productSuppliers': safe('product_suppliers', f'product_id IN {p}'),
        'customerProducts': safe('customer_products', f'id IN {h}'),
        'customerProductProcesses': safe(
            'customer_product_processes',
            f'customer_product_id IN {h} OR process_product_id IN {p}'),
        'productMerges': safe('product_merges', f'keep_product_id IN {p}'),
        'productMergeSources': safe('product_merge_sources', f'source_product_id IN {p}'),
        'productMatchAliases': safe('product_match_aliases', f'product_id IN {p}'),
        'productLedgerRowMaps': safe(
            'product_ledger_row_maps', f'product_id IN {p} OR customer_product_id IN {h}'),
        'makerSkuCorrections': 0,
        'legacyCsvRowMaps': safe('legacy_csv_row_maps', f'product_id IN {p}'),
    }


def _delete_scoped(c, ids):
    tables = _tables(c)
    hids = _handle_ids(c, ids)
    p, h = _in(ids), _in(hids)
    deleted = {}

    def run(key, table, where):
        if table not in tables:
            return
        cur = c.execute(f'DELETE FROM {table} WHERE {where}')
        deleted[key] = int(cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0)

    # 他区分の商品が統合先として指していれば外す（自己参照FK）
    c.execute(f'UPDATE products SET merged_into_id=NULL, merged_at=NULL WHERE merged_into_id IN {p}')
    if 'product_merges' in tables and 'product_merge_sources' in tables:
        c.execute(
            f'DELETE FROM product_merge_sources WHERE merge_id IN '
            f'(SELECT id FROM product_merges WHERE keep_product_id IN {p})'
        )
    run('productMergeSources', 'product_merge_sources', f'source_product_id IN {p}')
    run('productMerges', 'product_merges', f'keep_product_id IN {p}')
    run('productMatchAliases', 'product_match_aliases', f'product_id IN {p}')
    run('productLedgerRowMaps', 'product_ledger_row_maps',
        f'product_id IN {p} OR customer_product_id IN {h}')
    run('legacyCsvRowMaps', 'legacy_csv_row_maps', f'product_id IN {p}')
    run('productSuppliers', 'product_suppliers', f'product_id IN {p}')
    run('productSizes', 'product_sizes', f'product_id IN {p}')
    # 加工を消すとき、制服などの取扱からの加工リンクだけ外す（取扱そのものは残す）
    run('customerProductProcesses', 'customer_product_processes',
        f'customer_product_id IN {h} OR process_product_id IN {p}')
    run('customerProducts', 'customer_products', f'id IN {h}')
    run('products', 'products', f'id IN {p}')
    return deleted


def kept_snapshot(c):
    """削除前後で不変であることを確認するための件数。"""
    tables = _tables(c)
    snap = {}
    for table in (
        'customers', 'suppliers', 'locations', 'lots', 'moves', 'move_batches',
        'opening_batches', 'settings', 'purchase_orders', 'purchase_order_lines',
        'purchase_order_events', 'purchase_order_receipts', 'loans', 'loan_lines',
        'loan_events',
    ):
        if table in tables:
            snap[table] = _count(c, f'SELECT COUNT(*) AS n FROM {table}')
    return snap


def preview_wipe(c, body=None):
    body = body or {}
    require_owner(body)
    scope = scope_of(body)
    blockers = collect_blockers(c, scope)
    counts = count_targets(c, scope)
    return {
        'scope': scope,
        'scopeLabel': scope_label(scope),
        'confirmText': CONFIRM_TEXT,
        'scopeNote': SCOPE_NOTE,
        'kept': list(KEPT_LABELS),
        'counts': counts,
        'blockers': blockers,
        'canWipe': len(blockers) == 0,
        'keptSnapshot': kept_snapshot(c),
    }


def _assert_no_blockers(c, scope=''):
    blockers = collect_blockers(c, scope)
    if blockers:
        raise RegistryWipeBlocked(blockers)


def _delete_registry(c):
    tables = _tables(c)
    deleted = {}

    def run_delete(key, sql, params=()):
        cur = c.execute(sql, params)
        deleted[key] = int(cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0)

    # 自己参照FKを先に外す
    if 'products' in tables:
        c.execute('UPDATE products SET merged_into_id=NULL, merged_at=NULL')

    if 'product_merge_sources' in tables:
        run_delete('productMergeSources', 'DELETE FROM product_merge_sources')
    if 'product_merges' in tables:
        run_delete('productMerges', 'DELETE FROM product_merges')
    if 'product_match_aliases' in tables:
        run_delete('productMatchAliases', 'DELETE FROM product_match_aliases')
    if 'maker_sku_corrections' in tables:
        run_delete('makerSkuCorrections', 'DELETE FROM maker_sku_corrections')
    if 'product_ledger_row_maps' in tables:
        run_delete('productLedgerRowMaps', 'DELETE FROM product_ledger_row_maps')
    if 'legacy_csv_row_maps' in tables:
        # 在庫追跡に使う lot 紐づきマップは collect_blockers で既に拒否済み
        run_delete(
            'legacyCsvRowMaps',
            'DELETE FROM legacy_csv_row_maps WHERE product_id IS NOT NULL',
        )
    if 'product_suppliers' in tables:
        run_delete('productSuppliers', 'DELETE FROM product_suppliers')
    if 'product_sizes' in tables:
        run_delete('productSizes', 'DELETE FROM product_sizes')
    if 'customer_product_processes' in tables:
        run_delete('customerProductProcesses', 'DELETE FROM customer_product_processes')
    if 'customer_products' in tables:
        run_delete('customerProducts', 'DELETE FROM customer_products')
    if 'products' in tables:
        run_delete('products', 'DELETE FROM products')

    return deleted


def execute_wipe(c, body, *, db_path=None):
    """バックアップ → 再チェック → 1トランザクション削除。失敗時は呼び出し側が ROLLBACK。"""
    require_owner(body)
    if body.get('_canWrite') is False:
        raise PermissionError('閲覧専用のため、商品登録の全削除はできません。')
    scope = scope_of(body)
    if not scope:
        # 全体削除だけは従来どおり確認文字を求める（区分ごとの削除は画面の確認だけ）
        require_confirm_text(body)
    ids = scope_product_ids(c, scope)

    before_counts = count_targets(c, scope)
    before_kept = kept_snapshot(c)
    _assert_no_blockers(c, scope)

    backup_path = ''
    if db_path:
        try:
            dest = stock.backup_sqlite(Path(db_path), tag='pre-product-registry-wipe')
            backup_path = str(dest or '')
        except stock.BackupError:
            raise
        except (OSError, sqlite3.Error) as e:
            raise stock.BackupError(stock.BACKUP_FAILED_MESSAGE, cause=e) from e
        if not backup_path:
            raise stock.BackupError(stock.BACKUP_FAILED_MESSAGE)

    # バックアップ後に参照が増えていないか再確認
    _assert_no_blockers(c, scope)

    deleted = _delete_registry(c) if ids is None else _delete_scoped(c, ids)

    after_counts = count_targets(c) if ids is None else _count_scoped(c, ids)
    after_kept = kept_snapshot(c)
    if any(v != 0 for v in after_counts.values()):
        raise RuntimeError(
            '商品登録の削除が完了しませんでした。変更は取り消します。'
        )
    if after_kept != before_kept:
        raise RuntimeError(
            '残すべきマスタ／在庫／履歴の件数が変わったため、削除を取り消します。'
        )

    when = stock.now()
    who = actor_name(body)
    print(
        'product_registry_wipe ok '
        f'actor={who!r} at={when} backup={backup_path!r} '
        f'deleted={json.dumps(deleted, ensure_ascii=False)} '
        f'before={json.dumps(before_counts, ensure_ascii=False)}',
        flush=True,
    )
    return {
        'ok': True,
        'deleted': True,
        'scope': scope,
        'scopeLabel': scope_label(scope),
        'actorName': who,
        'at': when,
        'backupPath': backup_path,
        'counts': deleted,
        'beforeCounts': before_counts,
        'kept': list(KEPT_LABELS),
        'scopeNote': SCOPE_NOTE,
        'keptSnapshot': after_kept,
    }
