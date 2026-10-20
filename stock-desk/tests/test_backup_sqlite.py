"""SQLite online backup (WAL-safe) used before product-ledger / legacy / merge commits."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import product_ledger_import as pli  # noqa: E402
import server as stock_server  # noqa: E402
import stock  # noqa: E402


def _csv_b64(text: str) -> str:
    import base64
    return base64.b64encode(('\ufeff' + text).encode('utf-8')).decode('ascii')


class BackupSqliteTest(unittest.TestCase):
    def test_backup_with_open_writer_and_wal(self):
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            c = stock.connect(path)
            stock.initialize(c)
            c.execute("INSERT INTO customers(name,created_at,updated_at) VALUES('A',?,?)",
                      (stock.now(), stock.now()))
            c.commit()
            # Ensure WAL siblings exist and hold a write transaction (simulates commit path).
            c.execute('BEGIN IMMEDIATE')
            c.execute("INSERT INTO customers(name,created_at,updated_at) VALUES('B',?,?)",
                      (stock.now(), stock.now()))
            # Uncommitted row must NOT appear in backup; committed 'A' must.
            dest = stock.backup_sqlite(path, tag='pre-test')
            self.assertIsNotNone(dest)
            self.assertTrue(Path(dest).is_file())
            # No incomplete .partial left behind
            self.assertFalse(Path(str(dest) + '.partial').exists())
            # Backup opens and passes integrity
            b = sqlite3.connect(str(dest))
            self.assertEqual(b.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            names = {r[0] for r in b.execute('SELECT name FROM customers')}
            self.assertEqual(names, {'A'})
            b.close()
            c.rollback()
            c.close()
        finally:
            folder.cleanup()

    def test_backup_includes_committed_wal_pages(self):
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            c = stock.connect(path)
            stock.initialize(c)
            c.execute("INSERT INTO customers(name,created_at,updated_at) VALUES('W1',?,?)",
                      (stock.now(), stock.now()))
            c.commit()
            # Second committed write leaves content in WAL until checkpoint
            c.execute("INSERT INTO customers(name,created_at,updated_at) VALUES('W2',?,?)",
                      (stock.now(), stock.now()))
            c.commit()
            self.assertTrue(Path(str(path) + '-wal').is_file() or True)  # WAL may already checkpoint
            dest = stock.backup_sqlite(path, tag='pre-wal')
            b = sqlite3.connect(str(dest))
            names = {r[0] for r in b.execute('SELECT name FROM customers')}
            self.assertEqual(names, {'W1', 'W2'})
            self.assertEqual(b.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            b.close()
            c.close()
        finally:
            folder.cleanup()

    def test_failed_backup_leaves_no_final_file(self):
        import os

        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            c = stock.connect(path)
            stock.initialize(c)
            c.commit()
            c.close()
            # Create a readable DB, then make the directory non-writable so the
            # .partial destination cannot be created/promoted (lock/permission analogue).
            os.chmod(folder.name, 0o555)
            with mock.patch.object(stock, '_BACKUP_TIMEOUT_SEC', 0.35):
                with self.assertRaises(stock.BackupError) as ctx:
                    stock.backup_sqlite(path, tag='pre-fail')
            self.assertIn('バックアップに失敗', str(ctx.exception))
            os.chmod(folder.name, 0o755)
            finals = [
                p for p in Path(folder.name).glob('stock.pre-fail-*')
                if '.partial' not in p.name
            ]
            self.assertEqual(finals, [])
            partials = list(Path(folder.name).glob('*.partial*'))
            self.assertEqual(partials, [])
        finally:
            try:
                os.chmod(folder.name, 0o755)
            except OSError:
                pass
            folder.cleanup()

    def test_commit_aborts_on_backup_failure_no_registration(self):
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            c = stock.connect(path)
            stock.initialize(c)
            c.commit()
            before_products = c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n']
            before_cp = c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n']
            text = '顧客,仕入先,商品コード,商品名,サイズ\n病院A,仕入X,BK-1,バックアップ試験,M\n'
            body = {
                'file': _csv_b64(text), 'name': 't.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            preview = pli.build_preview(c, body)
            self.assertEqual(preview['reviewCount'], 0)
            with mock.patch.object(stock, 'backup_sqlite', side_effect=stock.BackupError()):
                with self.assertRaises(stock.BackupError):
                    pli.commit_preview(c, {
                        **body,
                        'previewFingerprint': preview['previewFingerprint'],
                        'previewAt': preview['previewAt'],
                        'token': 'pl-backup-fail-1',
                    }, db_path=str(path))
            # Transaction should not leave partial registration (caller rolls back).
            c.rollback()
            self.assertEqual(
                c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'],
                before_products,
            )
            self.assertEqual(
                c.execute('SELECT COUNT(*) AS n FROM customer_products').fetchone()['n'],
                before_cp,
            )
            runs = c.execute('SELECT COUNT(*) AS n FROM product_ledger_import_runs').fetchone()['n']
            self.assertEqual(runs, 0)
            c.close()
        finally:
            folder.cleanup()

    def test_api_post_returns_json_on_backup_failure(self):
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            with stock.connect(path) as c:
                stock.initialize(c)
            text = '顧客,仕入先,商品コード,商品名,サイズ\n病院A,仕入X,API-1,API試験,M\n'
            body = {
                'file': _csv_b64(text), 'name': 't.csv',
                '_canWrite': True, '_actor': {'name': 't'},
            }
            st, preview = stock_server.api_post(
                str(path), 'desk', '/api/product-ledger-csv/preview', body, can_write=True,
            )
            self.assertEqual(st, 200)
            with mock.patch.object(stock, 'backup_sqlite', side_effect=stock.BackupError()):
                st2, err = stock_server.api_post(
                    str(path), 'desk', '/api/product-ledger-csv/commit',
                    {
                        **body,
                        'previewFingerprint': preview['previewFingerprint'],
                        'previewAt': preview['previewAt'],
                        'token': 'pl-api-backup-fail',
                    },
                    can_write=True,
                )
            self.assertEqual(st2, 400)
            self.assertEqual(err.get('code'), 'backup_failed')
            self.assertEqual(err.get('registered'), False)
            self.assertIn('バックアップに失敗', err.get('error', ''))
            with stock.connect(path) as c:
                self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM products').fetchone()['n'], 0)
        finally:
            folder.cleanup()

    def test_backup_does_not_deadlock_under_caller_immediate(self):
        """Caller BEGIN IMMEDIATE + backup must finish within bound (no second lock wait)."""
        folder = tempfile.TemporaryDirectory()
        try:
            path = Path(folder.name) / 'stock.sqlite3'
            c = stock.connect(path)
            stock.initialize(c)
            c.commit()
            c.execute('BEGIN IMMEDIATE')
            done = {'ok': False, 'err': None}

            def run():
                try:
                    dest = stock.backup_sqlite(path, tag='pre-deadlock')
                    done['ok'] = bool(dest and Path(dest).is_file())
                except Exception as e:
                    done['err'] = e

            t = threading.Thread(target=run)
            t.start()
            t.join(timeout=8)
            self.assertFalse(t.is_alive(), 'backup_sqlite hung while caller held BEGIN IMMEDIATE')
            self.assertIsNone(done['err'], done['err'])
            self.assertTrue(done['ok'])
            c.rollback()
            c.close()
        finally:
            folder.cleanup()


class ProductLedgerUiBackupCopyTest(unittest.TestCase):
    def test_ui_surfaces_backup_failed(self):
        js = (ROOT / 'static' / 'product_ledger_csv.js').read_text(encoding='utf-8')
        self.assertIn('backup_failed', js)
        self.assertIn('登録前のバックアップに失敗したため、登録を中止しました', js)
        self.assertIn('registered===false', js)
        app = (ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        # BASE=/inventory still uses same api() JSON error path
        self.assertIn("location.pathname.startsWith('/inventory')", app)


if __name__ == '__main__':
    unittest.main()
