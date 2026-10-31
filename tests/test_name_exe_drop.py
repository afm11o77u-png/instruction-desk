"""統合ネームラベル exe 置き場フォルダの自動取込（一時フォルダのみ）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import name_exe

EXE = b'MZ' + b'\x00' * 500


class DropWatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / 'data'
        self.drop = Path(self.tmp.name) / 'drop'
        self.drop.mkdir()
        self.logs = []
        self.w = name_exe.DropWatcher(self.data, self.drop, log=self.logs.append)

    def tearDown(self):
        self.tmp.cleanup()

    def _put(self, name, body, mtime=None):
        p = self.drop / name
        p.write_bytes(body)
        if mtime: os.utime(p, (mtime, mtime))
        return p

    def test_imports_only_after_stable(self):
        p = self._put('統合ネームラベル.exe', EXE[:100], 1_700_000_000)
        self.assertEqual(self.w.check(), 'waiting')
        p.write_bytes(EXE); os.utime(p, (1_700_000_010,) * 2)  # まだコピー中
        self.assertEqual(self.w.check(), 'waiting')
        self.assertIsNone(name_exe.info(self.data))
        self.assertEqual(self.w.check(), 'imported')
        meta = name_exe.info(self.data)
        self.assertEqual(meta['filename'], '統合ネームラベル.exe')
        self.assertEqual(meta['uploadedBy'], 'フォルダから自動取込')
        self.assertEqual((name_exe.folder(self.data) / name_exe.EXE_NAME).read_bytes(), EXE)
        self.assertTrue(p.is_file())  # 元ファイルは残す
        self.assertTrue(any('自動取込しました' in m for m in self.logs))

    def test_no_reimport(self):
        self._put('a.exe', EXE)
        self.w.check(); self.assertEqual(self.w.check(), 'imported')
        self.assertIsNone(self.w.check())
        # 再起動しても同じ中身は取り込み直さない
        w2 = name_exe.DropWatcher(self.data, self.drop, log=self.logs.append)
        w2.check(); self.assertEqual(w2.check(), 'same')
        self.assertIsNone(w2.check())

    def test_newest_by_mtime_and_replace(self):
        self._put('old.exe', EXE + b'old', 1_700_000_000)
        self._put('new.exe', EXE + b'new', 1_700_000_100)
        self.w.check(); self.assertEqual(self.w.check(), 'imported')
        self.assertEqual(name_exe.info(self.data)['filename'], 'new.exe')
        self._put('newer.exe', EXE + b'newer', 1_700_000_200)
        self.w.check(); self.assertEqual(self.w.check(), 'imported')
        self.assertEqual(name_exe.info(self.data)['filename'], 'newer.exe')
        files = sorted(p.name for p in name_exe.folder(self.data).iterdir())
        self.assertEqual(files, ['meta.json', 'togo-name-label.exe'])

    def test_invalid_ignored_and_previous_kept(self):
        self._put('good.exe', EXE, 1_700_000_000)
        self.w.check(); self.w.check()
        for name, body in (('bad.exe', b'PK not exe'), ('empty.exe', b'')):
            self._put(name, body, 1_700_000_500 + len(body))
            self.w.check(); self.assertEqual(self.w.check(), 'failed')
            self.assertIsNone(self.w.check())  # 同じ不正ファイルは繰り返さない
            (self.drop / name).unlink()
        self._put('note.txt', b'MZ', 1_700_009_999)  # .exe 以外は見ない
        self.assertEqual(name_exe.info(self.data)['filename'], 'good.exe')
        self.assertTrue(any('失敗' in m for m in self.logs))

    def test_too_large_rejected(self):
        old = name_exe.MAX_BYTES
        name_exe.MAX_BYTES = 100
        try:
            self._put('big.exe', EXE)
            self.w.check(); self.assertEqual(self.w.check(), 'failed')
        finally:
            name_exe.MAX_BYTES = old
        self.assertIsNone(name_exe.info(self.data))

    def test_empty_or_missing_folder(self):
        self.assertIsNone(self.w.check())
        w = name_exe.DropWatcher(self.data, Path(self.tmp.name) / 'nothing', log=self.logs.append)
        self.assertIsNone(w.check())

    def test_repo_drop_folder_present(self):
        d = ROOT / 'name-label-exe-drop'
        self.assertTrue((d / '.gitkeep').is_file())
        self.assertTrue((d / 'README.txt').is_file())
        self.assertIn('name-label-exe-drop/*.exe', (ROOT / '.gitignore').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
