import sys, tempfile, types, unittest
from pathlib import Path
from urllib.parse import unquote
sys.modules.setdefault("winreg", types.ModuleType("winreg"))
import picking


class ScanSaveTest(unittest.TestCase):
    def test_saves_into_picking_subfolder_without_root_fallback(self):
        with tempfile.TemporaryDirectory() as t:
            base = Path(t)
            h = picking.save_scan_copy(b'x', '2026-11-27', base)
            self.assertIn('ピッキングリスト', unquote(h['X-Saved-Error']))
            self.assertEqual(list(base.iterdir()), [])  # 直下へは保存しない
            (base / 'ピッキングリスト').mkdir()
            a = picking.save_scan_copy(b'x', '2026-11-27', base)
            b = picking.save_scan_copy(b'x', '2026-11-27', base)
            self.assertEqual(Path(unquote(a['X-Saved-Path'])), base / 'ピッキングリスト' / '2026-11-27_ピッキングリスト.xlsx')
            self.assertEqual(Path(unquote(b['X-Saved-Path'])).name, '2026-11-27_ピッキングリスト_2.xlsx')
            self.assertIn('X-Saved-Error', picking.save_scan_copy(b'x', '2026-11-27', base / 'none'))


if __name__ == '__main__':
    unittest.main()
