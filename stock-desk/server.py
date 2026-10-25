import json, os, sqlite3, re
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import stock
import stock_history
import stock_period
import stock_orders
import excel_export
from upload_limits import (
    LARGE_BODY_MAX_BYTES,
    LARGE_BODY_PATHS,
    OVERSIZE_BODY_ERROR,
    body_limit_for,
)

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'

WRITE_PATHS = {
    '/api/customers', '/api/suppliers', '/api/products', '/api/locations',
    '/api/customer-products',
    '/api/opening', '/api/opening/correct', '/api/import',
    '/api/legacy-csv/commit',
    '/api/product-ledger-csv/commit',
    '/api/product-ledger-csv/maker-lookup/adopt',
    '/api/products/merge',
    '/api/products/registry-wipe',
    '/api/inbound', '/api/outbound', '/api/inbound/correct',
    '/api/stocktake', '/api/history/correct',
    '/api/purchase-orders', '/api/purchase-orders/mark-ordered',
    '/api/purchase-orders/cancel', '/api/purchase-orders/receive',
    '/api/company-profile',
}

PO_ID_RE = re.compile(r'^/api/purchase-orders/(\d+)$')
PO_HTML_RE = re.compile(r'^/api/purchase-orders/(\d+)/html$')


def api_get(db_path, mode, path, query, *, can_write=True, actor=None):
    if path == '/api/import/template':
        import excel_book
        return 200, excel_book.template(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    if path == '/api/bootstrap':
        with stock.connect(db_path) as c:
            body = stock.bootstrap(c)
        body['mode'] = mode
        body['canWrite'] = bool(can_write)
        body['actorName'] = (actor or {}).get('name') or ''
        body['actorRole'] = (actor or {}).get('role') or ''
        body['isOwner'] = (actor or {}).get('role') == 'owner'
        return 200, body
    if path == '/api/products/duplicates':
        import stock_product_merge
        with stock.connect(db_path) as c:
            return 200, stock_product_merge.list_duplicate_groups(c)
    if path == '/api/products/registry-wipe/preview':
        import product_registry_wipe
        try:
            with stock.connect(db_path) as c:
                return 200, product_registry_wipe.preview_wipe(c, {'_actor': actor or {}, '_canWrite': bool(can_write)})
        except PermissionError as e:
            return 403, {'error': str(e), 'deleted': False}
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '削除内容を確認してください。', 'deleted': False}
    if path == '/api/stock':
        try:
            with stock.connect(db_path) as c:
                body = stock.stock_query(c, {k: v[0] for k, v in query.items()})
        except ValueError as e:
            return 400, {'error': str(e)}
        return 200, body
    if path == '/api/move/targets':
        try:
            with stock.connect(db_path) as c:
                body = stock.move_targets(c, {k: v[0] for k, v in query.items()})
        except ValueError as e:
            return 400, {'error': str(e)}
        return 200, body
    if path == '/api/opening/entries':
        with stock.connect(db_path) as c:
            return 200, stock.opening_entries(c)
    if path == '/api/lots/history':
        lot_id = query.get('lotId', [''])[0]
        if not str(lot_id).isdigit():
            return 400, {'error': '在庫を選んでください。'}
        with stock.connect(db_path) as c:
            return 200, {'events': stock.lot_history(c, int(lot_id))}
    if path == '/api/stock-history':
        try:
            args = {k: v[0] for k, v in query.items()}
            with stock.connect(db_path) as c:
                return 200, stock_history.list_history(c, args)
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    if path == '/api/outbound/preview':
        # Allow GET-style preview via query for simple clients; prefer POST.
        try:
            args = {k: v[0] for k, v in query.items()}
            body = {
                'customerProductId': int(args['customerProductId']) if str(args.get('customerProductId', '')).isdigit() else args.get('customerProductId'),
                'size': args.get('size'),
                'locationId': int(args['locationId']) if str(args.get('locationId', '')).isdigit() else args.get('locationId'),
                'qty': int(args['qty']) if str(args.get('qty', '')).isdigit() else args.get('qty'),
            }
            with stock.connect(db_path) as c:
                return 200, stock.plan_outbound(c, body)
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    if path == '/api/period-summary':
        try:
            args = {k: v[0] for k, v in query.items()}
            with stock.connect(db_path) as c:
                return 200, stock_period.summarize_period(c, args)
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    if path == '/api/period-summary/history':
        try:
            args = {k: v[0] for k, v in query.items()}
            with stock.connect(db_path) as c:
                return 200, stock_period.period_history(c, args)
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    if path == '/api/period-summary/export.csv':
        try:
            args = {k: v[0] for k, v in query.items()}
            with stock.connect(db_path) as c:
                data = stock_period.export_csv(c, args)
            return 200, data, 'text/csv; charset=utf-8'
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    if path == '/api/period-summary/export.xlsx':
        try:
            args = {k: v[0] for k, v in query.items()}
            with stock.connect(db_path) as c:
                data = stock_period.export_xlsx(c, args)
            return 200, data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    if path == '/api/registry-export/preview':
        try:
            with stock.connect(db_path) as c:
                return 200, excel_export.preview(c, query)
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    if path == '/api/registry-export.xlsx':
        try:
            with stock.connect(db_path) as c:
                data, filename = excel_export.export_xlsx(c, query)
            return 200, data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', {
                'Content-Disposition': excel_export.content_disposition(filename),
            }
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    if path == '/api/company-profile':
        with stock.connect(db_path) as c:
            return 200, stock_orders.company_profile(c)
    if path == '/api/purchase-orders':
        try:
            with stock.connect(db_path) as c:
                return 200, stock_orders.list_purchase_orders(c, {k: v[0] for k, v in query.items()})
        except ValueError as e:
            return 400, {'error': str(e)}
    if path == '/api/purchase-order-products':
        try:
            with stock.connect(db_path) as c:
                return 200, stock_orders.product_candidates(c, {k: v[0] for k, v in query.items()})
        except ValueError as e:
            return 400, {'error': str(e)}
    m = PO_ID_RE.match(path)
    if m:
        try:
            with stock.connect(db_path) as c:
                return 200, stock_orders.get_purchase_order(c, int(m.group(1)))
        except ValueError as e:
            return 400, {'error': str(e)}
    m = PO_HTML_RE.match(path)
    if m:
        try:
            with stock.connect(db_path) as c:
                html = stock_orders.purchase_order_html(c, int(m.group(1)))
            return 200, html.encode('utf-8'), 'text/html; charset=utf-8'
        except ValueError as e:
            return 400, {'error': str(e)}
    return 404, {'error': '見つかりません。'}


def api_post(db_path, mode, path, body, *, can_write=True, actor=None):
    if path in WRITE_PATHS and not can_write:
        return 403, {'error': '閲覧専用のため、数量の変更はできません。'}
    payload = dict(body or {})
    payload['_canWrite'] = bool(can_write)
    if actor:
        payload['_actor'] = actor
    if path == '/api/import':
        import excel_book
        try:
            with stock.connect(db_path) as c:
                c.execute('BEGIN IMMEDIATE')
                result = excel_book.apply(c, payload)
        except ValueError as e:
            return 400, {'error': str(e) or 'Excelを確認してください。'}
        except sqlite3.IntegrityError:
            return 400, {'error': '同じ内容が既にあるため、読み込みを中止しました。'}
        return 200, result
    if path == '/api/legacy-csv/preview':
        import legacy_csv_import
        try:
            with stock.connect(db_path) as c:
                # プレビューは台帳・在庫を変更しない（読み取りのみ）
                result = legacy_csv_import.build_preview(c, payload)
        except ValueError as e:
            return 400, {'error': str(e) or 'CSVを確認してください。'}
        return 200, result
    if path == '/api/legacy-csv/history':
        import legacy_csv_import
        try:
            with stock.connect(db_path) as c:
                result = legacy_csv_import.file_history(c, payload)
        except ValueError as e:
            return 400, {'error': str(e) or 'CSVを確認してください。'}
        return 200, result
    if path == '/api/legacy-csv/commit':
        import legacy_csv_import
        try:
            with stock.connect(db_path) as c:
                c.execute('BEGIN IMMEDIATE')
                result = legacy_csv_import.commit_preview(c, payload, db_path=db_path)
        except stock.StockConflict as e:
            return 409, e.payload
        except stock.BackupError as e:
            cause = getattr(e, 'cause', None)
            print(
                f'legacy-csv commit backup failed: {e}'
                + (f' cause={type(cause).__name__}: {cause}' if cause else ''),
                flush=True,
            )
            return 400, {
                'error': str(e) or stock.BACKUP_FAILED_MESSAGE,
                'code': 'backup_failed',
                'registered': False,
            }
        except ValueError as e:
            return 400, {'error': str(e) or '取込を確認してください。'}
        except sqlite3.IntegrityError:
            return 400, {'error': '同じ内容が既にあるため、取込を中止しました。'}
        except (OSError, sqlite3.Error) as e:
            print(f'legacy-csv commit failed: {type(e).__name__}: {e}', flush=True)
            return 400, {
                'error': '取込を中止しました。登録は完了していません。',
                'code': 'commit_failed',
                'registered': False,
            }
        return 200, result
    if path == '/api/product-ledger-csv/preview':
        import product_ledger_import
        try:
            with stock.connect(db_path) as c:
                result = product_ledger_import.build_preview(c, payload)
        except ValueError as e:
            return 400, {'error': str(e) or 'CSVを確認してください。'}
        return 200, result
    if path == '/api/product-ledger-csv/history':
        import product_ledger_import
        try:
            with stock.connect(db_path) as c:
                result = product_ledger_import.file_history(c, payload)
        except ValueError as e:
            return 400, {'error': str(e) or 'CSVを確認してください。'}
        return 200, result
    if path == '/api/product-ledger-csv/commit':
        import product_ledger_import
        try:
            with stock.connect(db_path) as c:
                c.execute('BEGIN IMMEDIATE')
                result = product_ledger_import.commit_preview(c, payload, db_path=db_path)
        except stock.StockConflict as e:
            return 409, e.payload
        except stock.BackupError as e:
            cause = getattr(e, 'cause', None)
            print(
                f'product-ledger commit backup failed: {e}'
                + (f' cause={type(cause).__name__}: {cause}' if cause else ''),
                flush=True,
            )
            return 400, {
                'error': str(e) or stock.BACKUP_FAILED_MESSAGE,
                'code': 'backup_failed',
                'registered': False,
            }
        except ValueError as e:
            return 400, {'error': str(e) or '取込を確認してください。'}
        except RuntimeError as e:
            print(f'product-ledger commit aborted: {type(e).__name__}: {e}', flush=True)
            return 400, {
                'error': str(e) or '取込を中止しました。登録は完了していません。',
                'registered': False,
            }
        except sqlite3.IntegrityError:
            return 400, {'error': '同じ内容が既にあるため、取込を中止しました。'}
        except (OSError, sqlite3.Error) as e:
            print(f'product-ledger commit failed: {type(e).__name__}: {e}', flush=True)
            return 400, {
                'error': '取込を中止しました。登録は完了していません。',
                'code': 'commit_failed',
                'registered': False,
            }
        return 200, result
    if path == '/api/product-ledger-csv/maker-lookup':
        import maker_sku_lookup
        try:
            with stock.connect(db_path) as c:
                result = maker_sku_lookup.search_maker_product(
                    c,
                    manufacturer=payload.get('manufacturer') or '',
                    maker_sku=payload.get('makerSku') or payload.get('maker_sku') or '',
                    current_name=payload.get('currentName') or payload.get('current_name') or '',
                    current_color=payload.get('currentColor') or payload.get('current_color') or '',
                    current_supplement=(
                        payload.get('currentSupplement')
                        or payload.get('current_supplement')
                        or ''
                    ),
                    current_sku=(
                        payload.get('currentSku')
                        or payload.get('current_sku')
                        or payload.get('plannedRegisteredSku')
                        or ''
                    ),
                    source_name=payload.get('sourceName') or payload.get('source_name') or '',
                    supplier=payload.get('supplier') or '',
                )
        except ValueError as e:
            return 400, {'error': str(e) or 'メーカー品番を確認してください。'}
        except (OSError, sqlite3.Error) as e:
            print(f'maker-lookup failed: {type(e).__name__}: {e}', flush=True)
            return 400, {'error': 'メーカー情報の検索に失敗しました。', 'code': 'maker_lookup_failed'}
        return 200, result
    if path == '/api/product-ledger-csv/maker-lookup/from-url':
        import maker_sku_lookup
        try:
            with stock.connect(db_path) as c:
                result = maker_sku_lookup.lookup_maker_product_from_url(
                    c,
                    url=payload.get('url') or payload.get('sourceUrl') or payload.get('source_url') or '',
                    manufacturer=payload.get('manufacturer') or '',
                    maker_sku=payload.get('makerSku') or payload.get('maker_sku') or '',
                    current_name=payload.get('currentName') or payload.get('current_name') or '',
                    current_color=payload.get('currentColor') or payload.get('current_color') or '',
                    current_supplement=(
                        payload.get('currentSupplement')
                        or payload.get('current_supplement')
                        or ''
                    ),
                    current_sku=(
                        payload.get('currentSku')
                        or payload.get('current_sku')
                        or payload.get('plannedRegisteredSku')
                        or ''
                    ),
                    source_name=payload.get('sourceName') or payload.get('source_name') or '',
                    supplier=payload.get('supplier') or '',
                )
        except ValueError as e:
            return 400, {'error': str(e) or 'URLとメーカー品番を確認してください。'}
        except (OSError, sqlite3.Error) as e:
            print(f'maker-lookup from-url failed: {type(e).__name__}: {e}', flush=True)
            return 400, {
                'error': '指定URLからの商品情報取得に失敗しました。',
                'code': 'maker_lookup_from_url_failed',
            }
        return 200, result
    if path == '/api/product-ledger-csv/maker-lookup/adopt':
        import maker_sku_lookup
        try:
            with stock.connect(db_path) as c:
                c.execute('BEGIN IMMEDIATE')
                result = maker_sku_lookup.adopt_maker_correction(
                    c,
                    manufacturer=payload.get('manufacturer') or '',
                    maker_sku=payload.get('makerSku') or payload.get('maker_sku') or '',
                    name=payload.get('name') or '',
                    color=payload.get('color') or '',
                    supplement=payload.get('supplement') or '',
                    position=payload.get('position') or '',
                    planned_registered_sku=(
                        payload.get('plannedRegisteredSku')
                        or payload.get('planned_registered_sku')
                        or ''
                    ),
                    source_url=payload.get('sourceUrl') or payload.get('source_url') or '',
                )
                c.commit()
        except ValueError as e:
            return 400, {'error': str(e) or '採用内容を確認してください。'}
        except (OSError, sqlite3.Error) as e:
            print(f'maker-lookup adopt failed: {type(e).__name__}: {e}', flush=True)
            return 400, {'error': '採用内容の保存に失敗しました。', 'code': 'maker_adopt_failed'}
        return 200, result
    if path == '/api/product-ledger-csv/maker-lookup/bulk/start':
        import maker_sku_bulk_lookup
        try:
            result = maker_sku_bulk_lookup.start_bulk_lookup_job(
                db_path,
                selected_codes=payload.get('selectedCodes') or payload.get('selected_codes') or [],
                sku_groups=payload.get('skuGroups') or payload.get('sku_groups') or [],
                rows=payload.get('rows') or [],
                max_targets=payload.get('maxTargets') or payload.get('max_targets') or None,
            )
        except ValueError as e:
            return 400, {'error': str(e) or '一括取得を開始できません。'}
        except (OSError, sqlite3.Error, TypeError) as e:
            print(f'maker-lookup bulk start failed: {type(e).__name__}: {e}', flush=True)
            return 400, {'error': '一括取得の開始に失敗しました。', 'code': 'maker_bulk_start_failed'}
        return 200, result
    if path == '/api/product-ledger-csv/maker-lookup/bulk/status':
        import maker_sku_bulk_lookup
        try:
            result = maker_sku_bulk_lookup.get_bulk_lookup_job(
                payload.get('jobId') or payload.get('job_id') or '',
            )
        except ValueError as e:
            return 400, {'error': str(e) or '一括取得ジョブが見つかりません。'}
        return 200, result
    if path == '/api/product-ledger-csv/maker-lookup/bulk/cancel':
        import maker_sku_bulk_lookup
        try:
            result = maker_sku_bulk_lookup.cancel_bulk_lookup_job(
                payload.get('jobId') or payload.get('job_id') or '',
            )
        except ValueError as e:
            return 400, {'error': str(e) or '一括取得ジョブが見つかりません。'}
        return 200, result
    if path == '/api/products/merge/preview':
        import stock_product_merge
        try:
            with stock.connect(db_path) as c:
                return 200, stock_product_merge.preview_merge(c, payload)
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '統合内容を確認してください。'}
    if path == '/api/products/merge':
        import stock_product_merge
        if not can_write:
            return 403, {'error': '閲覧専用のため、商品の統合はできません。'}
        try:
            with stock.connect(db_path) as c:
                c.execute('BEGIN IMMEDIATE')
                result = stock_product_merge.execute_merge(c, payload, db_path=db_path)
        except stock.StockConflict as e:
            return 409, e.payload
        except stock.BackupError as e:
            cause = getattr(e, 'cause', None)
            print(
                f'product merge backup failed: {e}'
                + (f' cause={type(cause).__name__}: {cause}' if cause else ''),
                flush=True,
            )
            return 400, {
                'error': str(e) or stock.BACKUP_FAILED_MESSAGE,
                'code': 'backup_failed',
                'registered': False,
            }
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '統合を確認してください。'}
        except sqlite3.IntegrityError:
            return 400, {'error': '同じ統合操作が既にあるため、中止しました。'}
        except (OSError, sqlite3.Error) as e:
            print(f'product merge failed: {type(e).__name__}: {e}', flush=True)
            return 400, {
                'error': '統合を中止しました。変更は完了していません。',
                'code': 'commit_failed',
                'registered': False,
            }
        return 200, result
    if path == '/api/products/registry-wipe':
        import product_registry_wipe
        if not can_write:
            return 403, {'error': '閲覧専用のため、商品登録の全削除はできません。', 'deleted': False}
        try:
            with stock.connect(db_path) as c:
                c.execute('BEGIN IMMEDIATE')
                result = product_registry_wipe.execute_wipe(c, payload, db_path=db_path)
        except PermissionError as e:
            return 403, {'error': str(e), 'deleted': False}
        except product_registry_wipe.RegistryWipeBlocked as e:
            return 409, e.payload
        except stock.BackupError as e:
            cause = getattr(e, 'cause', None)
            print(
                f'product registry wipe backup failed: {e}'
                + (f' cause={type(cause).__name__}: {cause}' if cause else ''),
                flush=True,
            )
            return 400, {
                'error': '削除前のバックアップに失敗したため、削除を中止しました。商品登録は変わっていません。',
                'code': 'backup_failed',
                'deleted': False,
            }
        except ValueError as e:
            return 400, {'error': str(e) or '削除内容を確認してください。', 'deleted': False}
        except (RuntimeError, sqlite3.IntegrityError, OSError, sqlite3.Error) as e:
            print(f'product registry wipe failed: {type(e).__name__}: {e}', flush=True)
            return 400, {
                'error': '商品登録の全削除を中止しました。変更は完了していません。',
                'code': 'commit_failed',
                'deleted': False,
            }
        return 200, result
    if path == '/api/outbound/preview':
        try:
            with stock.connect(db_path) as c:
                return 200, stock.plan_outbound(c, payload)
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    if path == '/api/purchase-orders/preview':
        # Pre-save PDF/print preview (HTML). Does not persist or change status.
        try:
            with stock.connect(db_path) as c:
                html = stock_orders.preview_purchase_order_html(c, payload)
            return 200, html.encode('utf-8'), 'text/html; charset=utf-8'
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    routes = {
        '/api/customers': stock.save_customer,
        '/api/suppliers': stock.save_supplier,
        '/api/products': stock.save_product,
        '/api/customer-products': stock.ensure_customer_product,
        '/api/locations': stock.save_location,
        '/api/opening': stock.register_opening,
        '/api/opening/correct': stock.correct_opening,
        '/api/inbound': stock.register_inbound,
        '/api/outbound': stock.register_outbound,
        '/api/inbound/correct': stock.correct_inbound,
        '/api/stocktake': stock.register_stocktake,
        '/api/history/correct': stock.correct_history_move,
        '/api/purchase-orders': stock_orders.save_purchase_order,
        '/api/purchase-orders/mark-ordered': stock_orders.mark_ordered,
        '/api/purchase-orders/cancel': stock_orders.cancel_purchase_order,
        '/api/purchase-orders/receive': stock_orders.receive_purchase_order,
        '/api/company-profile': stock_orders.save_company_profile,
    }
    fn = routes.get(path)
    if not fn:
        return 404, {'error': '見つかりません。'}
    try:
        with stock.connect(db_path) as c:
            c.execute('BEGIN IMMEDIATE')
            result = fn(c, payload)
    except stock.StockConflict as e:
        return 409, e.payload
    except (ValueError, TypeError, KeyError) as e:
        return 400, {'error': str(e) or '入力を確認してください。'}
    except sqlite3.IntegrityError:
        if path in ('/api/opening', '/api/inbound', '/api/outbound', '/api/stocktake'):
            with stock.connect(db_path) as c:
                if path == '/api/opening':
                    row = c.execute('SELECT id FROM opening_batches WHERE client_token=?', (stock.text(payload.get('token')),)).fetchone()
                    if row:
                        return 200, stock.batch_result(c, row['id'], True)
                else:
                    row = c.execute('SELECT id FROM move_batches WHERE client_token=?', (stock.text(payload.get('token')),)).fetchone()
                    if row:
                        return 200, stock._move_token_result(c, row['id'], True)
        if path in ('/api/purchase-orders', '/api/purchase-orders/receive'):
            with stock.connect(db_path) as c:
                if path == '/api/purchase-orders':
                    row = c.execute(
                        'SELECT id FROM purchase_orders WHERE client_token=?',
                        (stock.text(payload.get('token')),),
                    ).fetchone()
                    if row:
                        return 200, {**stock_orders.get_purchase_order(c, row['id']), 'already': True}
                else:
                    row = c.execute(
                        'SELECT purchase_order_id FROM purchase_order_receipts WHERE client_token=?',
                        (stock.text(payload.get('token')),),
                    ).fetchone()
                    if row:
                        return 200, {
                            **stock_orders.get_purchase_order(c, row['purchase_order_id']),
                            'already': True,
                        }
        return 400, {'error': '同じ内容が既にあるため、保存しませんでした。'}
    return 200, result


def make_handler(db_path, mode):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f'{self.address_string()} {fmt % args}', flush=True)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.startswith('/api/'):
                result = api_get(db_path, mode, parsed.path, parse_qs(parsed.query), can_write=True)
                if len(result) >= 3:
                    headers = result[3] if len(result) > 3 else None
                    self.send_bytes(result[0], result[1], result[2], headers)
                else:
                    self.send(result[0], result[1])
                return
            name = 'index.html' if parsed.path == '/' else parsed.path.lstrip('/')
            path = (STATIC / name).resolve()
            if STATIC.resolve() not in path.parents and path != STATIC.resolve():
                self.send(404, {'error': '見つかりません。'})
                return
            if not path.is_file():
                self.send(404, {'error': '見つかりません。'})
                return
            kind = 'text/html; charset=utf-8' if path.suffix == '.html' else 'text/css; charset=utf-8' if path.suffix == '.css' else 'text/javascript; charset=utf-8'
            data = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if not self.headers.get('X-Stock-Request') == '1':
                self.send(400, {'error': '画面からの操作だけを受け付けます。'})
                return
            host = (self.headers.get('Origin') or '').replace('http://', '').split(':')[0]
            if host not in ('127.0.0.1', 'localhost'):
                self.send(400, {'error': 'このパソコンからの操作だけを受け付けます。'})
                return
            length = int(self.headers.get('Content-Length') or 0)
            path = urlparse(self.path).path
            if length > body_limit_for(path):
                self.send(400, {'error': OVERSIZE_BODY_ERROR})
                return
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
            except json.JSONDecodeError:
                self.send(400, {'error': '送信内容を読めませんでした。'})
                return
            try:
                result = api_post(db_path, mode, urlparse(self.path).path, body, can_write=True)
            except Exception as e:
                # Never tear the HTTP response (browser would only show Failed to fetch).
                print(f'api_post crashed path={path}: {type(e).__name__}: {e}', flush=True)
                self.send(500, {
                    'error': '処理中にエラーが発生しました。登録は完了していません。',
                    'registered': False,
                })
                return
            if len(result) >= 3:
                headers = result[3] if len(result) > 3 else None
                self.send_bytes(result[0], result[1], result[2], headers)
            else:
                self.send(result[0], result[1])

        def send(self, status, body):
            self.send_bytes(status, json.dumps(body, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

        def send_bytes(self, status, data, kind, headers=None):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)
    return Handler


def prepare(db_path, mode):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    path = Path(db_path)
    if path.is_file():
        with stock.connect(db_path) as c:
            if stock.needs_migration(c):
                stock.backup_sqlite(path)
    with stock.connect(db_path) as c:
        stock.initialize(c)
        if mode == 'sample' and not c.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
            import sample_data
            sample_data.seed(c)
            c.execute("INSERT INTO settings VALUES('seeded','1')")
