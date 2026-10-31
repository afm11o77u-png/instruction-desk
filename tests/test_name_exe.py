"""統合ネームラベル exe の配布（アップロードは主管理者のみ、ダウンロードはログイン済み）。"""
import http.client
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import name_exe

EXE = b'MZ' + b'\x00' * 1000 + 'テスト'.encode()


def _user(role='owner', must_change=0):
    return {'id': 1, 'name': '主管理者' if role == 'owner' else role, 'role': role, 'must_change': must_change}


class NameExeUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _save(self, user=None, name='統合ネームラベル v1.exe', body=EXE, length=None):
        return name_exe.save(self.data, user or _user(), name, io.BytesIO(body), len(body) if length is None else length,
                             now=datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc))

    def test_upload_records_meta_in_jst(self):
        meta = self._save()
        self.assertEqual(meta['filename'], '統合ネームラベル v1.exe')
        self.assertEqual(meta['size'], len(EXE))
        self.assertEqual(meta['uploadedAt'], '2026-10-31 15:00')
        self.assertEqual(name_exe.info(self.data)['filename'], '統合ネームラベル v1.exe')
        path, _, disp = name_exe.download(self.data, _user('floor3'))
        self.assertEqual(path.read_bytes(), EXE)
        self.assertIn("filename*=UTF-8''" + quote('統合ネームラベル v1.exe', safe=''), disp)
        self.assertEqual(unquote(disp.split("UTF-8''")[1]), '統合ネームラベル v1.exe')

    def test_replace_keeps_only_latest(self):
        self._save(name='old.exe')
        self._save(name='new.exe', body=EXE + b'2')
        files = sorted(p.name for p in name_exe.folder(self.data).iterdir())
        self.assertEqual(files, ['meta.json', 'togo-name-label.exe'])
        self.assertEqual(name_exe.info(self.data)['filename'], 'new.exe')
        self.assertEqual((name_exe.folder(self.data) / 'togo-name-label.exe').read_bytes(), EXE + b'2')

    def test_permissions(self):
        for role in ('sales', 'floor', 'floor2', 'floor3'):
            with self.assertRaises(name_exe.ExeError) as e: self._save(user=_user(role))
            self.assertEqual(e.exception.status, 403)
        for user in (None, _user(must_change=1)):
            with self.assertRaises(name_exe.ExeError) as e: name_exe.download(self.data, user)
            self.assertEqual(e.exception.status, 401)
        self.assertIsNone(name_exe.info(self.data))

    def test_not_uploaded_404(self):
        with self.assertRaises(name_exe.ExeError) as e: name_exe.download(self.data, _user('sales'))
        self.assertEqual(e.exception.status, 404)
        self.assertIn('まだアップロード', e.exception.message)

    def test_rejects_bad_files_and_keeps_previous(self):
        self._save(name='keep.exe')
        cases = [('a.zip', EXE, None, 400), ('x.exe', b'PK' + b'0' * 10, None, 400),
                 ('x.exe', b'', None, 400), ('x.exe', EXE, name_exe.MAX_BYTES + 1, 413),
                 ('x.exe', EXE, len(EXE) + 10, 400)]
        for name, body, length, status in cases:
            with self.assertRaises(name_exe.ExeError) as e: self._save(name=name, body=body, length=length)
            self.assertEqual(e.exception.status, status, name)
        self.assertEqual(name_exe.info(self.data)['filename'], 'keep.exe')
        leftovers = [p.name for p in name_exe.folder(self.data).iterdir() if p.name.startswith('.')]
        self.assertEqual(leftovers, [])

    def test_path_in_name_is_stripped(self):
        self.assertEqual(self._save(name='C:\\x\\..\\evil.exe')['filename'], 'evil.exe')


class NameExeHttpTest(unittest.TestCase):
    """server.Handler を一時データで起動し、HTTP の 401/403/404 と往復を確認。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ['INSTRUCTION_DATA'] = cls.tmp.name
        sys.modules.setdefault('winreg', types.ModuleType('winreg'))
        for mod in ('server',):
            sys.modules.pop(mod, None)
        try:
            import server
        except Exception as e:  # 環境依存で読み込めない場合
            raise unittest.SkipTest(f'server を読み込めません: {e}')
        cls.server = server
        assert Path(server.DATA) == Path(cls.tmp.name)
        import auth
        c = sqlite3.connect(server.DB); c.row_factory = sqlite3.Row
        c.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT DEFAULT "", role TEXT, manage INTEGER DEFAULT 0, overview INTEGER DEFAULT 0, active INTEGER DEFAULT 1, must_change INTEGER DEFAULT 0, version INTEGER DEFAULT 1)')
        c.execute('CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL)')
        cls.tokens = {}
        for i, role in enumerate(('owner', 'sales', 'floor'), 1):
            c.execute('INSERT INTO users(id, login, name, role, active, must_change) VALUES(?,?,?,?,1,0)', (i, role, role, role))
            token = 'tok-' + role
            c.execute('INSERT INTO sessions VALUES(?,?,?)', (auth.digest(token), i, 9e12))
            cls.tokens[role] = token
        c.commit(); c.close()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), type('QuietHandler', (server.Handler,), {'log_message': lambda *a: None}))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()
        os.environ.pop('INSTRUCTION_DATA', None)
        sys.modules.pop('server', None)
        cls.tmp.cleanup()

    def _req(self, method, path, role=None, body=None, name='統合.exe'):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=10)
        headers = {'Host': f'127.0.0.1:{self.server.PORT}', 'X-Desk-Request': '1'}
        if role: headers['Cookie'] = 'desk_session=' + self.tokens[role]
        if body is not None:
            headers['X-File-Name'] = quote(name); headers['Content-Type'] = 'application/octet-stream'
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse(); data = r.read(); conn.close()
        return r.status, r.getheader('Content-Disposition'), data

    def test_flow(self):
        self.assertEqual(self._req('GET', '/api/name-labels/exe')[0], 401)
        st, _, data = self._req('GET', '/api/name-labels/exe', 'sales')
        self.assertEqual(st, 404); self.assertIn('まだアップロード', json.loads(data)['error'])
        self.assertEqual(self._req('POST', '/api/name-labels/exe', None, EXE)[0], 401)
        self.assertEqual(self._req('POST', '/api/name-labels/exe', 'sales', EXE)[0], 403)
        self.assertEqual(self._req('POST', '/api/name-labels/exe', 'floor', EXE)[0], 403)
        st, _, data = self._req('POST', '/api/name-labels/exe', 'owner', EXE)
        self.assertEqual(st, 200, data)
        st, _, data = self._req('GET', '/api/name-labels/exe/info', 'floor')
        info = json.loads(data)
        self.assertEqual(info['exe']['filename'], '統合.exe'); self.assertFalse(info['canUpload'])
        st, disp, data = self._req('GET', '/api/name-labels/exe', 'floor')
        self.assertEqual(st, 200); self.assertEqual(data, EXE)
        self.assertIn("filename*=UTF-8''" + quote('統合.exe', safe=''), disp)

    def test_update_notice_per_user(self):
        self._req('POST', '/api/name-labels/exe', 'owner', EXE + b'notice')
        info = lambda role: json.loads(self._req('GET', '/api/name-labels/exe/info', role)[2])
        self.assertTrue(info('sales')['update']['pending'])
        self.assertNotIn('downloads', info('sales'))
        self.assertEqual(self._req('GET', '/api/name-labels/exe', 'sales')[0], 200)
        self.assertFalse(info('sales')['update']['pending'])
        rows = {r['login']: r['latest'] for r in info('owner')['downloads']}
        self.assertTrue(rows['sales'])
        self._req('POST', '/api/name-labels/exe', 'owner', EXE + b'notice2')
        self.assertTrue(info('sales')['update']['pending'])


if __name__ == '__main__':
    unittest.main()
