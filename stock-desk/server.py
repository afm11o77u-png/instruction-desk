import json, os, sqlite3
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import stock

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'

WRITE_PATHS = {
    '/api/customers', '/api/suppliers', '/api/products', '/api/locations',
    '/api/opening', '/api/opening/correct', '/api/import',
    '/api/inbound', '/api/outbound', '/api/inbound/correct',
}


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
        return 200, body
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
    if path == '/api/outbound/preview':
        try:
            with stock.connect(db_path) as c:
                return 200, stock.plan_outbound(c, payload)
        except (ValueError, TypeError, KeyError) as e:
            return 400, {'error': str(e) or '入力を確認してください。'}
    routes = {
        '/api/customers': stock.save_customer,
        '/api/suppliers': stock.save_supplier,
        '/api/products': stock.save_product,
        '/api/locations': stock.save_location,
        '/api/opening': stock.register_opening,
        '/api/opening/correct': stock.correct_opening,
        '/api/inbound': stock.register_inbound,
        '/api/outbound': stock.register_outbound,
        '/api/inbound/correct': stock.correct_inbound,
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
        if path in ('/api/opening', '/api/inbound', '/api/outbound'):
            with stock.connect(db_path) as c:
                if path == '/api/opening':
                    row = c.execute('SELECT id FROM opening_batches WHERE client_token=?', (stock.text(payload.get('token')),)).fetchone()
                    if row:
                        return 200, stock.batch_result(c, row['id'], True)
                else:
                    row = c.execute('SELECT id FROM move_batches WHERE client_token=?', (stock.text(payload.get('token')),)).fetchone()
                    if row:
                        return 200, stock._move_token_result(c, row['id'], True)
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
                if len(result) == 3:
                    self.send_bytes(result[0], result[1], result[2])
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
            limit = 12_000_000 if urlparse(self.path).path == '/api/import' else 1_000_000
            if length > limit:
                self.send(400, {'error': '送信データが大きすぎます。'})
                return
            try:
                body = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
            except json.JSONDecodeError:
                self.send(400, {'error': '送信内容を読めませんでした。'})
                return
            status, result = api_post(db_path, mode, urlparse(self.path).path, body, can_write=True)
            self.send(status, result)

        def send(self, status, body):
            self.send_bytes(status, json.dumps(body, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

        def send_bytes(self, status, data, kind):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
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
