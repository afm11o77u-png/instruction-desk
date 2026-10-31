"""統合ネームラベル exe 更新のお知らせ（利用者ごとのダウンロード記録）。一時DBのみ。"""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import name_exe

EXE = b'MZ' + b'\x00' * 100
OWNER = {'id': 1, 'name': '主管理者', 'role': 'owner', 'must_change': 0}
SALES = {'id': 2, 'name': '営業', 'role': 'sales', 'must_change': 0}


class NameExeUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.c = sqlite3.connect(self.data / 'test.db')
        self.c.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, role TEXT, active INTEGER DEFAULT 1)')
        self.c.executemany('INSERT INTO users VALUES(?,?,?,?,?)', [(1, 'owner', '主管理者', 'owner', 1), (2, 'sales1', '営業', 'sales', 1), (3, 'old', '退職', 'sales', 0)])

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def _upload(self, body):
        return name_exe.save(self.data, OWNER, 'a.exe', io.BytesIO(body), len(body))

    def test_no_exe_no_notice(self):
        self.assertFalse(name_exe.update_status(self.c, SALES, None)['pending'])

    def test_pending_until_download_and_again_after_update(self):
        meta = self._upload(EXE)
        self.assertTrue(name_exe.update_status(self.c, SALES, meta)['pending'])
        name_exe.record_download(self.c, SALES, meta, now=datetime(2026, 10, 31, 8, 5, tzinfo=timezone.utc))
        st = name_exe.update_status(self.c, SALES, meta)
        self.assertFalse(st['pending']); self.assertEqual(st['lastDownloadAt'], '2026-10-31 17:05')
        self.assertTrue(name_exe.update_status(self.c, OWNER, meta)['pending'])  # 利用者ごと
        meta2 = self._upload(EXE + b'v2')
        self.assertTrue(name_exe.update_status(self.c, SALES, meta2)['pending'])
        name_exe.record_download(self.c, SALES, meta2)
        self.assertFalse(name_exe.update_status(self.c, SALES, meta2)['pending'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM name_exe_downloads').fetchone()[0], 1)

    def test_same_content_reupload_is_not_an_update(self):
        meta = self._upload(EXE)
        name_exe.record_download(self.c, SALES, meta)
        self.assertFalse(name_exe.update_status(self.c, SALES, self._upload(EXE))['pending'])

    def test_report_lists_active_users(self):
        meta = self._upload(EXE)
        name_exe.record_download(self.c, SALES, meta, now=datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc))
        rep = name_exe.download_report(self.c, meta)
        self.assertEqual([(r['login'], r['latest'], r['lastDownloadAt']) for r in rep],
                         [('owner', False, None), ('sales1', True, '2026-10-31 09:00')])

    def test_schema_idempotent(self):
        name_exe.schema(self.c); name_exe.schema(self.c)


class NameExeUpdateWiringTest(unittest.TestCase):
    def test_ui_wiring(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        js = (ROOT / 'names.js').read_text(encoding='utf-8')
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn('id="exeupdate"', html)
        self.assertEqual(html.count('data-exebadge'), 3)
        self.assertIn('NEW 更新あり', html)
        self.assertIn('id="nameexeusers"', html)
        self.assertIn('setInterval(checkNameExeUpdate,60000)', js)
        self.assertIn('prefers-reduced-motion', css)


if __name__ == '__main__':
    unittest.main()
