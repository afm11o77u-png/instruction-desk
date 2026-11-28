"""ワンボタン更新版の初回配布（3ファイル入りzip）。既存 exe スロットは消さない。"""
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
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.platform != 'win32':
    _wr = types.ModuleType('winreg')
    _wr.HKEY_CURRENT_USER = 1
    _wr.HKEY_LOCAL_MACHINE = 2
    _wr.HKEY_CLASSES_ROOT = 3
    _wr.REG_SZ = 1
    class _Key:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def _open(*a, **k):
        return _Key()
    def _enum(*a, **k):
        raise OSError('stub')
    _wr.OpenKey = _open
    _wr.EnumKey = _enum
    _wr.QueryValue = lambda *a, **k: ''
    _wr.QueryValueEx = _enum
    sys.modules['winreg'] = _wr

import name_exe
import name_setup

EXE = b'MZ' + b'\x00' * 200
EXE2 = b'MZ' + b'\x22' * 200
DLL = b'MZ' + b'\x11' * 150
CFG = b'<?xml version="1.0"?><configuration/>'
OPTIONAL = b'MZ' + b'\x33' * 80
OLD_EXE = b'MZ' + b'\x44' * 300
DOWNLOAD_NAME = '統合ネームラベル.zip'


def _user(role='owner', must_change=0):
    return {'id': 1, 'name': '主管理者' if role == 'owner' else role, 'role': role, 'must_change': must_change}


def pack(names=None, extra=None):
    """テスト用 zip。names は {zip内の名前: バイト列}。"""
    files = names or {
        '統合ネームラベル.exe': EXE,
        'zxing.dll': DLL,
        '統合ネームラベル.exe.config': CFG,
    }
    if extra:
        files = dict(files, **extra)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class NameSetupUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self._fetch = name_setup.fetch_github_exe
        name_setup._github_cache.update(at=0.0, ok=False, data=None)

    def tearDown(self):
        name_setup.fetch_github_exe = self._fetch
        self.tmp.cleanup()

    def _save(self, user=None, name='導入.zip', body=None, length=None, notes=None, now=None):
        body = pack() if body is None else body
        return name_setup.save(
            self.data, user or _user(), name, io.BytesIO(body),
            len(body) if length is None else length,
            now=now or datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc), notes=notes)

    def _save_exe(self, name='旧.exe', body=OLD_EXE, notes=None):
        return name_exe.save(
            self.data, _user(), name, io.BytesIO(body), len(body),
            now=datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc), notes=notes)

    def test_upload_normalizes_and_downloads_japanese_name(self):
        body = pack({
            'folder/togo-name-label.exe': EXE,
            'folder/zxing.dll': DLL,
            'folder/app.exe.config': CFG,
            'folder/使い方.txt': b'ignore',
        })
        meta = self._save(name='何か.zip', body=body, notes='・初回導入用')
        self.assertEqual(meta['filename'], DOWNLOAD_NAME)
        self.assertEqual(meta['uploadedAt'], '2026-11-28 17:00')
        name_setup.fetch_github_exe = lambda *a, **k: None
        path, got, disp, ctype = name_setup.download(self.data, _user('floor3'))
        self.assertEqual(ctype, 'application/zip')
        self.assertIn("filename*=UTF-8''" + quote(DOWNLOAD_NAME, safe=''), disp)
        self.assertEqual(unquote(disp.split("UTF-8''")[1]), DOWNLOAD_NAME)
        with zipfile.ZipFile(path) as zf:
            self.assertEqual(set(zf.namelist()), {
                '統合ネームラベル.exe', 'zxing.dll', '統合ネームラベル.exe.config'})
            self.assertEqual(zf.read('統合ネームラベル.exe'), EXE)
        self.assertEqual(name_setup.current_notes(self.data), '・初回導入用')
        files = sorted(p.name for p in name_setup.folder(self.data).iterdir() if not p.name.startswith('.'))
        self.assertEqual(files, ['history.json', 'meta.json', 'package.zip'])

    def test_keeps_optional_dotnetbarcode(self):
        body = pack(extra={'DotNetBarcode.dll': OPTIONAL})
        self._save(body=body)
        name_setup.fetch_github_exe = lambda *a, **k: None
        path, *_ = name_setup.download(self.data, _user())
        with zipfile.ZipFile(path) as zf:
            self.assertIn('DotNetBarcode.dll', zf.namelist())
            self.assertEqual(zf.read('DotNetBarcode.dll'), OPTIONAL)

    def test_rejects_incomplete_or_non_zip_and_keeps_previous(self):
        self._save(name='keep.zip')
        cases = [
            ('a.exe', EXE, 400),
            ('x.zip', b'MZ' + b'0' * 20, 400),
            ('x.zip', pack({'統合ネームラベル.exe': EXE, 'zxing.dll': DLL}), 400),
            ('x.zip', b'', 400),
        ]
        for name, body, status in cases:
            with self.assertRaises(name_setup.ExeError) as e:
                self._save(name=name, body=body)
            self.assertEqual(e.exception.status, status, name)
        with self.assertRaises(name_setup.ExeError) as e:
            self._save(name='x.zip', body=pack(), length=name_setup.MAX_BYTES + 1)
        self.assertEqual(e.exception.status, 413)
        self.assertEqual(name_setup.info(self.data)['filename'], DOWNLOAD_NAME)
        leftovers = [p.name for p in name_setup.folder(self.data).iterdir() if p.name.startswith('.') and not p.name.startswith('.serve-')]
        self.assertEqual(leftovers, [])

    def test_permissions_and_404(self):
        for role in ('sales', 'floor', 'floor2', 'floor3'):
            with self.assertRaises(name_setup.ExeError) as e:
                self._save(user=_user(role))
            self.assertEqual(e.exception.status, 403)
        with self.assertRaises(name_setup.ExeError) as e:
            name_setup.download(self.data, _user('sales'))
        self.assertEqual(e.exception.status, 404)
        for user in (None, _user(must_change=1)):
            with self.assertRaises(name_setup.ExeError) as e:
                name_setup.download(self.data, user)
            self.assertEqual(e.exception.status, 401)

    def test_does_not_touch_existing_exe_slot(self):
        self._save_exe(notes='・旧exeのメモ')
        exe_files = sorted(p.name for p in name_exe.folder(self.data).iterdir())
        self._save(notes='・新しい配布')
        self.assertEqual(sorted(p.name for p in name_exe.folder(self.data).iterdir()), exe_files)
        self.assertEqual((name_exe.folder(self.data) / 'togo-name-label.exe').read_bytes(), OLD_EXE)
        self.assertEqual(name_exe.current_notes(self.data), '・旧exeのメモ')
        self.assertEqual(name_setup.display_notes(self.data), '・新しい配布')
        self.assertEqual(name_setup.display_history(self.data)[0]['filename'], DOWNLOAD_NAME)

    def test_display_falls_back_to_exe_notes(self):
        self._save_exe(notes='・既存の更新内容')
        self.assertEqual(name_setup.display_notes(self.data), '・既存の更新内容')
        notes = name_setup.release_notes(self.data)
        self.assertEqual(notes['latest']['notes'], '・既存の更新内容')
        self.assertEqual(notes['latest']['uploaded_at'], '2026-10-31T15:00:00+09:00')
        self.assertNotIn('uploadedBy', json.dumps(notes))
        self.assertNotIn('主管理者', json.dumps(notes, ensure_ascii=False))

    def test_github_exe_replaces_only_when_available(self):
        self._save()
        name_setup.fetch_github_exe = lambda *a, **k: EXE2
        path, *_ = name_setup.download(self.data, _user())
        with zipfile.ZipFile(path) as zf:
            self.assertEqual(zf.read('統合ネームラベル.exe'), EXE2)
            self.assertEqual(zf.read('zxing.dll'), DLL)
        # 保存した本体は差し替えない
        with zipfile.ZipFile(name_setup.folder(self.data) / 'package.zip') as zf:
            self.assertEqual(zf.read('統合ネームラベル.exe'), EXE)
        name_setup.fetch_github_exe = lambda *a, **k: None
        path, *_ = name_setup.download(self.data, _user())
        with zipfile.ZipFile(path) as zf:
            self.assertEqual(zf.read('統合ネームラベル.exe'), EXE)

    def test_release_notes_html_escapes(self):
        self._save(notes='<script>x</script>')
        page = name_setup.release_notes_html(self.data)
        self.assertIn('&lt;script&gt;x&lt;/script&gt;', page)
        self.assertNotIn('<script>x</script>', page)
        self.assertIn('2026-11-28T17:00:00+09:00', page)


class NameSetupHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ['INSTRUCTION_DATA'] = cls.tmp.name
        if sys.platform != 'win32':
            sys.modules['winreg'] = sys.modules.get('winreg') or types.ModuleType('winreg')
        for mod in ('server',):
            sys.modules.pop(mod, None)
        try:
            import server
        except Exception as e:
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
            token = 'tok-setup-' + role
            c.execute('INSERT INTO sessions VALUES(?,?,?)', (auth.digest(token), i, 9e12))
            cls.tokens[role] = token
        c.commit(); c.close()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), type('QuietHandler', (server.Handler,), {'log_message': lambda *a: None}))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls._fetch = name_setup.fetch_github_exe
        name_setup.fetch_github_exe = lambda *a, **k: None

    @classmethod
    def tearDownClass(cls):
        name_setup.fetch_github_exe = cls._fetch
        cls.httpd.shutdown(); cls.httpd.server_close()
        os.environ.pop('INSTRUCTION_DATA', None)
        sys.modules.pop('server', None)
        cls.tmp.cleanup()

    def _req(self, method, path, role=None, body=None, name='導入.zip', notes=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=10)
        headers = {'Host': f'127.0.0.1:{self.server.PORT}', 'X-Desk-Request': '1'}
        if role:
            headers['Cookie'] = 'desk_session=' + self.tokens[role]
        if body is not None:
            headers['X-File-Name'] = quote(name)
            headers['Content-Type'] = 'application/octet-stream'
            if notes is not None:
                headers['X-Exe-Notes'] = quote(notes)
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse(); data = r.read(); conn.close()
        return r.status, r.getheader('Content-Disposition'), r.getheader('Content-Type'), data

    def test_public_notes_and_setup_download(self):
        st, _, _, data = self._req('GET', '/api/name-label/release-notes')
        self.assertEqual(st, 200, data)
        payload = json.loads(data)
        self.assertIsNone(payload['latest'])
        self.assertEqual(payload['history'], [])
        st, _, ctype, page = self._req('GET', '/name-label/release-notes')
        self.assertEqual(st, 200, page)
        self.assertIn('text/html', ctype or '')
        self.assertIn('まだ更新内容はありません'.encode('utf-8'), page)

        self.assertEqual(self._req('GET', '/api/name-labels/setup')[0], 401)
        self.assertEqual(self._req('POST', '/api/name-labels/setup', 'sales', pack())[0], 403)
        st, _, _, data = self._req('POST', '/api/name-labels/setup', 'owner', pack(), name='導入.zip', notes='・公開メモ')
        self.assertEqual(st, 200, data)

        st, _, _, data = self._req('GET', '/api/name-label/release-notes')
        self.assertEqual(st, 200, data)
        payload = json.loads(data)
        self.assertEqual(payload['latest']['notes'], '・公開メモ')
        self.assertEqual(payload['latest']['file_name'], DOWNLOAD_NAME)
        self.assertTrue(payload['latest']['uploaded_at'].endswith('+09:00'))
        self.assertNotIn('uploadedBy', data.decode())
        self.assertNotIn('主管理者', data.decode())

        st, disp, _, body = self._req('GET', '/api/name-labels/setup', 'floor')
        self.assertEqual(st, 200)
        self.assertIn("filename*=UTF-8''" + quote(DOWNLOAD_NAME, safe=''), disp)
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            self.assertEqual(zf.read('zxing.dll'), DLL)


class NameSetupWiringTest(unittest.TestCase):
    def test_ui_wiring(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        js = (ROOT / 'names.js').read_text(encoding='utf-8')
        deleted = html.find('id="namedeletedopen"')
        setup = html.find('id="namesetupdownload"')
        exe = html.find('id="nameexeopen"')
        self.assertTrue(0 < deleted < setup < exe)
        self.assertIn('disabled title="まだアップロードされていません">ダウンロード</button>', html)
        self.assertNotIn('id="nameexedownload"', html)
        self.assertIn('初回導入用のzipです', html)
        self.assertIn('accept=".zip,application/zip"', html)
        self.assertIn('/api/name-labels/setup', js)
        self.assertIn('/\\.zip$/i.test(file.name)', js)
        self.assertIn("$('#nameexenotescurrent').textContent=notes", js)
        self.assertNotIn('request-web.html', js)


if __name__ == '__main__':
    unittest.main()
