import os, sys, threading, webbrowser
from pathlib import Path
import server

ROOT = Path(__file__).resolve().parent


def main():
    mode = os.environ.get('STOCK_MODE', 'live')
    if mode not in ('live', 'sample'):
        print('起動の種類が不正です。')
        return 1
    port = int(os.environ.get('STOCK_PORT', '8775' if mode == 'live' else '8776'))
    db = ROOT / 'data' / mode / 'stock.sqlite3'
    server.prepare(db, mode)
    try:
        httpd = server.ThreadingHTTPServer(('127.0.0.1', port), server.make_handler(db, mode))
    except OSError:
        print(f'ポート {port} は使用中です。起動中の窓を閉じてから、もう一度起動してください。')
        return 1
    url = f'http://127.0.0.1:{port}/'
    label = 'サンプル' if mode == 'sample' else '空の台帳'
    print(f'{label}  {url}', flush=True)
    print('終了するときは、この窓で Ctrl+C を押してください。', flush=True)
    if not os.environ.get('STOCK_NO_BROWSER'):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('終了しました。', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
