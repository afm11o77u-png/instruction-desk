"""統合ネームラベル exe の更新内容（リリースノート）と履歴。一時フォルダ・一時DBのみ。"""
import io
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
NOTES = '・印刷の余白を修正\r\n- 新しい書式を追加\n\n<script>alert(1)</script>  '


def at(h):
    return datetime(2026, 10, 31, h, 0, tzinfo=timezone.utc)


class NameExeNotesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def up(self, body, notes=None, h=1):
        return name_exe.save(self.data, OWNER, 'a.exe', io.BytesIO(body), len(body), now=at(h), notes=notes)

    def test_upload_with_notes_and_history_newest_first(self):
        self.up(EXE, NOTES, 1)
        self.assertEqual(name_exe.current_notes(self.data), '・印刷の余白を修正\n- 新しい書式を追加\n\n<script>alert(1)</script>')
        self.up(EXE + b'2', None, 2)
        self.assertEqual(name_exe.current_notes(self.data), '')
        hist = name_exe.history(self.data)
        self.assertEqual([h['uploadedAt'] for h in hist], ['2026-10-31 11:00', '2026-10-31 10:00'])
        self.assertEqual([h['current'] for h in hist], [True, False])
        self.assertTrue(hist[1]['notes'].startswith('・印刷'))
        self.assertNotIn('sha256', hist[0])

    def test_edit_notes_for_drop_import_keeps_notice(self):
        with tempfile.TemporaryDirectory() as drop:
            p = Path(drop) / 'auto.exe'; p.write_bytes(EXE)
            w = name_exe.DropWatcher(self.data, drop, log=lambda *a: None)
            w.check(); self.assertEqual(w.check(), 'imported')
        c = sqlite3.connect(':memory:')
        meta = name_exe.info(self.data)
        name_exe.record_download(c, SALES, meta)
        name_exe.set_notes(self.data, OWNER, '・自動取込の版')
        self.assertEqual(name_exe.current_notes(self.data), '・自動取込の版')
        self.assertEqual(name_exe.history(self.data)[0]['uploadedBy'], name_exe.DROP_UPLOADER)
        self.assertFalse(name_exe.update_status(c, SALES, name_exe.info(self.data))['pending'])

    def test_same_content_reupload_keeps_notes(self):
        self.up(EXE, '・最初', 1)
        self.up(EXE, None, 2)
        self.assertEqual(name_exe.current_notes(self.data), '・最初')
        self.assertEqual(len(name_exe.history(self.data)), 1)

    def test_permissions_and_limits(self):
        self.up(EXE)
        with self.assertRaises(name_exe.ExeError) as e: name_exe.set_notes(self.data, SALES, 'x')
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(name_exe.ExeError) as e: name_exe.set_notes(self.data, None, 'x')
        self.assertEqual(e.exception.status, 401)
        with self.assertRaises(name_exe.ExeError) as e: name_exe.set_notes(self.data, OWNER, 'あ' * (name_exe.NOTES_MAX + 1))
        self.assertEqual(e.exception.status, 400)
        with self.assertRaises(name_exe.ExeError): name_exe.set_notes(self.data, OWNER, 123)

    def test_no_exe_yet(self):
        with self.assertRaises(name_exe.ExeError) as e: name_exe.set_notes(self.data, OWNER, 'x')
        self.assertEqual(e.exception.status, 404)
        self.assertEqual(name_exe.history(self.data), [])

    def test_ui_escapes_and_keeps_line_breaks(self):
        js = (ROOT / 'names.js').read_text(encoding='utf-8')
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn("$('#nameexenotescurrent').textContent=notes", js)
        self.assertIn('${esc(h.notes)}', js)
        self.assertNotIn("exeupdatenote", js)  # バナーは廃止
        self.assertIn('.exenotes{white-space:pre-wrap', css)
