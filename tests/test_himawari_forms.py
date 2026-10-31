"""ひまわり1〜4 定型書式（Node 単体テストを実行）。"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HimawariFormsTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node がありません')
    def test_node_unit(self):
        done = subprocess.run(['node', str(ROOT / 'tests' / 'test_himawari_forms.js')], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('ok', done.stdout)


if __name__ == '__main__':
    unittest.main()
