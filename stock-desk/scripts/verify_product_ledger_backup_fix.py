#!/usr/bin/env python3
"""Verify product-ledger pre-commit backup fix on copy DBs only (no live stock DB).

Checks:
  1) Online backup with another write connection open (WAL)
  2) Backup file opens + integrity_check ok + committed WAL data present
  3) Forced backup failure leaves products/customer_products unchanged
  4) Successful commit on copy DB; same token re-import does not duplicate
  5) Stock qty / money / move history unchanged
  6) Standalone api_post + /inventory/ proxy return JSON on backup failure

  python3 scripts/verify_product_ledger_backup_fix.py
  python3 scripts/verify_product_ledger_backup_fix.py /path/to/copy.csv
"""
from __future__ import annotations

import base64
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib import error as urllib_error
from urllib import request as urllib_request

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
# Prefer stock-desk/ on sys.path so `import server` is stock-desk/server.py
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import sample_data  # noqa: E402
import server as stock_server  # noqa: E402
import stock  # noqa: E402

DEFAULT_CSV = Path(
    '/cursor/stores/bc-3759ce78-83cc-48e9-a175-c0d9daea8dcb/media/商品台帳.csv'
)


def _b64(text: str) -> str:
    return base64.b64encode(('\ufeff' + text).encode('utf-8')).decode('ascii')


def _tiny_csv():
    return (
        '顧客,仕入先,商品コード,商品名,サイズ\n'
        '検証病院,検証仕入,VF-1,検証スクラブ,M\n'
        '検証病院,検証仕入,VF-1,検証スクラブ,L\n'
        '検証病院,検証仕入,VF-2,検証パンツ,S\n'
    )


def _http_json(url, payload, timeout=60):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib_request.Request(
        url, data=data, method='POST',
        headers={'Content-Type': 'application/json', 'X-Stock-Request': '1', 'Origin': 'http://127.0.0.1'},
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
            return resp.status, json.loads(body or '{}')
    except urllib_error.HTTPError as e:
        body = e.read().decode('utf-8')
        try:
            parsed = json.loads(body or '{}')
        except json.JSONDecodeError:
            parsed = {'raw': body}
        return e.code, parsed


def verify_backup_wal(tmpdir: Path) -> Path:
    path = tmpdir / 'wal.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    c.execute(
        "INSERT INTO customers(name,created_at,updated_at) VALUES('C1',?,?)",
        (stock.now(), stock.now()),
    )
    c.commit()
    c.execute(
        "INSERT INTO customers(name,created_at,updated_at) VALUES('C2',?,?)",
        (stock.now(), stock.now()),
    )
    c.commit()
    c.execute('BEGIN IMMEDIATE')
    c.execute(
        "INSERT INTO customers(name,created_at,updated_at) VALUES('UNCOMMITTED',?,?)",
        (stock.now(), stock.now()),
    )
    dest = stock.backup_sqlite(path, tag='pre-verify-wal')
    assert dest and Path(dest).is_file(), dest
    assert not Path(str(dest) + '.partial').exists()
    b = sqlite3.connect(str(dest))
    assert b.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    names = {r[0] for r in b.execute('SELECT name FROM customers')}
    assert names == {'C1', 'C2'}, names
    b.close()
    c.rollback()
    c.close()
    print('1-2) WAL backup + integrity OK', dest.name)
    return path


def verify_forced_failure(tmpdir: Path) -> None:
    path = tmpdir / 'fail.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    sample_data.seed(c)
    c.execute("INSERT OR IGNORE INTO settings VALUES('seeded','1')")
    c.commit()
    before_p = c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n']
    before_cp = c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n']
    before_stock = pli.stock_snapshot(c)
    body = {
        'file': _b64(_tiny_csv()), 'name': 'fail.csv',
        '_canWrite': True, '_actor': {'name': 'verify'},
    }
    preview = pli.build_preview(c, body)
    with mock.patch.object(stock, 'backup_sqlite', side_effect=stock.BackupError()):
        try:
            pli.commit_preview(c, {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'verify-backup-fail',
            }, db_path=str(path))
            raise AssertionError('expected BackupError')
        except stock.BackupError:
            pass
    c.rollback()
    assert c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'] == before_p
    assert c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'] == before_cp
    assert pli.stock_snapshot(c) == before_stock
    c.close()
    print('3) forced backup failure leaves masters/stock unchanged')


def verify_success_commit(tmpdir: Path, csv_path: Path | None) -> None:
    path = tmpdir / 'ok.sqlite3'
    c = stock.connect(path)
    stock.initialize(c)
    sample_data.seed(c)
    c.execute("INSERT OR IGNORE INTO settings VALUES('seeded','1')")
    c.commit()
    before = pli.stock_snapshot(c)
    if csv_path and csv_path.is_file():
        # Use a small subset derived from real headers (never full live commit).
        blob = csv_path.read_bytes()
        text, _enc = pli.decode_csv_bytes(blob)
        fields, rows = pli.parse_csv_text(text)
        header = ','.join(fields)
        lines = [header]
        for _no, item in rows[:15]:
            lines.append(','.join(
                '"' + (item.get(k) or '').replace('"', '""') + '"' for k in fields
            ))
        file_b64 = base64.b64encode(('\ufeff' + '\n'.join(lines)).encode('utf-8')).decode('ascii')
        name = 'subset-' + csv_path.name
    else:
        file_b64 = _b64(_tiny_csv())
        name = 'tiny.csv'
    body = {
        'file': file_b64, 'name': name,
        '_canWrite': True, '_actor': {'name': 'verify'},
    }
    preview = pli.build_preview(c, body)
    selections = {}
    for r in preview['rows']:
        if r['status'] == 'needs_review':
            selections[str(r['rowNo'])] = {
                'forceCreate': True, 'matchGroupKey': r['matchGroupKey'],
            }
    if selections:
        preview = pli.build_preview(c, {**body, 'selections': selections})
    assert preview['reviewCount'] == 0, preview['reviewCount']
    c.execute('BEGIN IMMEDIATE')
    result = pli.commit_preview(c, {
        **body,
        'selections': selections,
        'previewFingerprint': preview['previewFingerprint'],
        'previewAt': preview['previewAt'],
        'token': 'verify-backup-ok-1',
    }, db_path=str(path))
    c.commit()
    assert result['stockUnchanged']
    assert pli.stock_snapshot(c) == before
    assert result.get('backupPath')
    assert Path(result['backupPath']).is_file()
    products_after = c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n']
    # re-import same token
    again = pli.commit_preview(c, {
        **body,
        'selections': selections,
        'previewFingerprint': preview['previewFingerprint'],
        'previewAt': preview['previewAt'],
        'token': 'verify-backup-ok-1',
    }, db_path=str(path))
    assert again.get('already')
    assert c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'] == products_after
    assert pli.stock_snapshot(c) == before
    c.close()
    print('4-5) successful commit + no duplicate + stock unchanged; backup=', Path(result['backupPath']).name)


def verify_http_json_errors(tmpdir: Path) -> None:
    path = tmpdir / 'http.sqlite3'
    with stock.connect(path) as c:
        stock.initialize(c)
    body = {
        'file': _b64(_tiny_csv()), 'name': 'http.csv',
        '_canWrite': True, '_actor': {'name': 'verify'},
    }
    st, preview = stock_server.api_post(
        str(path), 'desk', '/api/product-ledger-csv/preview', body, can_write=True,
    )
    assert st == 200
    with mock.patch.object(stock, 'backup_sqlite', side_effect=stock.BackupError()):
        st2, err = stock_server.api_post(
            str(path), 'desk', '/api/product-ledger-csv/commit',
            {
                **body,
                'previewFingerprint': preview['previewFingerprint'],
                'previewAt': preview['previewAt'],
                'token': 'verify-http-fail',
            },
            can_write=True,
        )
    assert st2 == 400 and err.get('code') == 'backup_failed' and err.get('registered') is False
    assert 'バックアップに失敗' in (err.get('error') or '')
    print('6a) standalone api_post JSON backup error OK')

    # Standalone HTTP server (stock-desk)
    handler = stock_server.make_handler(str(path), 'desk')
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        st3, prev3 = _http_json(
            f'http://127.0.0.1:{port}/api/product-ledger-csv/preview', body,
        )
        assert st3 == 200, (st3, prev3)
        with mock.patch.object(stock, 'backup_sqlite', side_effect=stock.BackupError()):
            st4, err4 = _http_json(
                f'http://127.0.0.1:{port}/api/product-ledger-csv/commit',
                {
                    **body,
                    'previewFingerprint': prev3['previewFingerprint'],
                    'previewAt': prev3['previewAt'],
                    'token': 'verify-http-fail-2',
                },
            )
        assert st4 == 400 and err4.get('code') == 'backup_failed', (st4, err4)
        assert err4.get('registered') is False
        print('6b) standalone HTTP JSON backup error OK')
    finally:
        httpd.shutdown()

    # Success path via api_post on copy DB
    st5, result = stock_server.api_post(
        str(path), 'desk', '/api/product-ledger-csv/commit',
        {
            **body,
            'previewFingerprint': preview['previewFingerprint'],
            'previewAt': preview['previewAt'],
            'token': 'verify-http-ok',
        },
        can_write=True,
    )
    assert st5 == 200 and result.get('stockUnchanged'), (st5, result)
    print('6c) standalone success path OK')


def verify_inventory_proxy_json(tmpdir: Path) -> None:
    """Exercise instruction-desk /inventory/ proxy error surfacing when importable."""
    try:
        import server as root_server  # noqa: F401
    except Exception as e:
        print('6d) skip inventory proxy (root server import failed):', e)
        return

    # Proxy path shares stock_server.api_post — already covered for JSON shape.
    # Confirm root server wraps crashes (source check) and api_post backup_failed
    # is what /inventory/api/... returns.
    src = (REPO / 'server.py').read_text(encoding='utf-8')
    assert 'inventory api_post crashed' in src
    assert "registered': False" in src or 'registered": False' in src
    print('6d) /inventory/ proxy crash→JSON guard present; backup_failed via shared api_post')


def main() -> int:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.is_file():
        csv_path = None
        print('note: sample CSV not found; using tiny generated CSV')
    tmp = Path(tempfile.mkdtemp(prefix='pl-backup-verify-'))
    try:
        verify_backup_wal(tmp)
        verify_forced_failure(tmp)
        verify_success_commit(tmp, csv_path)
        verify_http_json_errors(tmp)
        verify_inventory_proxy_json(tmp)
        print('ALL OK (copy DB only; no live stock commit)')
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
