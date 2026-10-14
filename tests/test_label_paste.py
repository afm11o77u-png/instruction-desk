"""宛名書き：Excel（TSV）一括貼り付け。"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
SHEET = (ROOT / 'sheet.js').read_text(encoding='utf-8')
SERVER = (ROOT / 'server.py').read_text(encoding='utf-8')


class LabelPasteAssetsTest(unittest.TestCase):
    def test_label_paste_script_loaded_before_sheet(self):
        self.assertIn('src="/label_paste.js"', INDEX)
        self.assertLess(INDEX.index('src="/label_paste.js"'), INDEX.index('src="/sheet.js"'))
        self.assertIn("'/label_paste.js':'label_paste.js'", SERVER)

    def test_sheet_wires_paste_origin_and_confirm(self):
        for needle in (
            "addEventListener('paste'",
            'parseLabelClipboard',
            'planLabelPaste',
            'labelPasteNeedsConfirm',
            '貼り付け先に既存の入力があります。置き換えますか？',
            'ensureLabelCardCount',
            'applyLabelPasteUpdates',
        ):
            self.assertIn(needle, SHEET)

    def test_hint_mentions_excel_column_order(self):
        self.assertIn('顧客名／管理番号／部署名／氏名／受付内容', INDEX)
        self.assertIn('Excelの表', INDEX)

    def test_print_mapping_still_four_corners(self):
        # 既存：labelOrders → queuePrint → addressTags が 4面（左上・右上・左下・右下）
        self.assertIn("const labelPlaces=['左上','右上','左下','右下']", SHEET)
        self.assertIn('queuePrint(list,true,true,true,false,currentLabelPattern())', SHEET)
        self.assertIn('function addressTags(', SHEET)

    def test_node_unit_suite(self):
        script = ROOT / 'tests' / 'test_label_paste.js'
        proc = subprocess.run(['node', str(script)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('All label paste unit tests passed', proc.stdout)


if __name__ == '__main__':
    unittest.main()
