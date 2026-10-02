import json, os, sqlite3
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import stock

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'


def api_get(db_path, mode, path, query):
    if path == '/api/import/template':
        import excel_book
        return 200, excel_book.template(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    if path == '/api/bootstrap':
        with stock.connect(db_path) as c:
            body = stock.bootstrap(c)
        body['mode'] = mode
        return 200, body
    if path == '/api/stock':
        try:
            with stock.connect(db_path) as c:
                body = stock.stock_query(c, {k: v[0] for k, v in query.items()})
        except ValueError as e:
            return 400, {'error': str(e)}
        return 200, body
    if path == '/api/lots/history':
        lot_id = query.get('lotId', [''])[0]
        if not str(lot_id).isdigit():
            return 400, {'error': '在庫を選んでください。'}
        with stock.connect(db_path) as c:
            return 200, {'events': stock.lot_history(c, int(lot_id))}
    return 404, {'error': '見つかりません。'}


def api_post(db_path, mode, path, body):
    if path == '/api/import':
        import excel_book
        try:
            with stock.connect(db_path) as c:
                c.execute('BEGIN IMMEDIATE')
                result = excel_book.apply(c, body)
        except ValueError as e:
            return 400, {'error': str(e) or 'Excelを確認してください。'}
        except sqlite3.IntegrityError:
            return 400, {'error': '同じ内容が既にあるため、読み込みを中止しました。'}
        return 200, result
    routes = {
        '/api/customers': stock.save_customer,
        '/api/suppliers': stock.save_supplier,
        '/api/products': stock.save_product,
        '/api/locations': stock.save_location,
        '/api/opening': stock.register_opening,
        '/api/opening/correct': stock.correct_opening,
    }
    if path == '/api/backup':
        try:
            return 200, {'file': backup(db_path, mode)}
        except OSError:
            return 400, {'error': 'バックアップを作れませんでした。'}
    fn = routes.get(path)
    if not fn:
        return 404, {'error': '見つかりません。'}
    try:
        with stock.connect(db_path) as c:
            c.execute('BEGIN IMMEDIATE')
            result = fn(c, body)
    except (ValueError, TypeError, KeyError) as e:
        return 400, {'error': str(e) or '入力を確認してください。'}
    except sqlite3.IntegrityError:
        if path == '/api/opening':
            with stock.connect(db_path) as c:
                row = c.execute('SELECT id FROM opening_batches WHERE client_token=?', (stock.text(body.get('token')),)).fetchone()
                if row:
                    return 200, stock.batch_result(c, row['id'], True)
        return 400, {'error': '同じ内容が既にあるため、保存しませんでした。'}
    return 200, result


def make_handler(db_path, mode):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f'{self.address_string()} {fmt % args}', flush=True)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.startswith('/api/'):
                result = api_get(db_path, mode, parsed.path, parse_qs(parsed.query))
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
            status, result = api_post(db_path, mode, urlparse(self.path).path, body)
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


def backup(db_path, mode):
    folder = Path(db_path).parent / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    name = f'{mode}-{stock.now().replace(":", "").replace("-", "")}.sqlite3'
    dest = folder / name
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return name


def prepare(db_path, mode):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with stock.connect(db_path) as c:
        stock.initialize(c)
        if mode == 'sample' and not c.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
            import sample_data
            sample_data.seed(c)
            c.execute("INSERT INTO settings VALUES('seeded','1')")
