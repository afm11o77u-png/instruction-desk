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

    def test_batch_toolbar_and_add_row_layout(self):
        # 説明文は撤去。一括設定・印刷・クリアは表の直前、用紙追加は表の下。
        self.assertNotIn('id="labelpastenote"', INDEX)
        self.assertNotIn('Excelの表', INDEX)
        self.assertNotIn('上から順に、左上・右上・左下・右下へ印刷されます', INDEX)
        self.assertIn('id="labeltoolbar"', INDEX)
        self.assertIn('受付内容を一括設定', INDEX)
        self.assertIn('id="labelrequestbulk"', INDEX)
        self.assertIn('id="labelrequestapply"', INDEX)
        self.assertIn('全行に適用', INDEX)
        self.assertIn('id="labelprint"', INDEX)
        self.assertIn('id="labelreset"', INDEX)
        self.assertIn('class="labeladdrow"', INDEX)
        self.assertIn('id="labeladd"', INDEX)
        self.assertLess(INDEX.index('id="labeltoolbar"'), INDEX.index('class="labeltablewrap"'))
        self.assertLess(INDEX.index('class="labeltablewrap"'), INDEX.index('class="labeladdrow"'))
        self.assertNotIn('受付内容の一括変更ができます。', INDEX)
        self.assertNotIn('Excelのように、複数行をコピーして入力欄へ貼り付けできます。', INDEX)

    def test_sheet_wires_request_batch(self):
        for needle in (
            'planLabelRequestBatch',
            "confirm(planned.confirmMessage)",
            "[data-key=\"request\"]",
            'labelrequestapply',
            'labelrequestbulk',
        ):
            self.assertIn(needle, SHEET)
        self.assertIn('planLabelRequestBatch', (ROOT / 'label_paste.js').read_text(encoding='utf-8'))

    def test_print_mapping_still_four_corners(self):
        # 既存：labelOrders → queuePrint → addressTags が 4面（左上・右上・左下・右下）
        self.assertIn("const labelPlaces=['左上','右上','左下','右下']", SHEET)
        self.assertIn('queuePrint(list,true,true,true,false,currentLabelPattern())', SHEET)
        self.assertIn('function addressTags(', SHEET)

    def test_request_hide_option_on_row_and_bulk(self):
        # グローバル印刷切替は撤去。表示なしは受付内容の選択肢。
        self.assertNotIn('labelrequestshowbox', INDEX)
        self.assertNotIn('name="labelrequestshow"', INDEX)
        self.assertNotIn('受付内容の印刷', INDEX)
        self.assertIn('id="labelrequestbulk"', INDEX)
        self.assertIn('<option>表示なし</option>', INDEX)
        self.assertIn('<option>表示なし</option>', SHEET)
        self.assertIn('taghidejob', SHEET)
        self.assertIn('labelPrintShowsRequest', SHEET)
        paste = (ROOT / 'label_paste.js').read_text(encoding='utf-8')
        self.assertIn("LABEL_REQUEST_HIDE = '表示なし'", paste)
        self.assertIn('function labelPrintShowsRequest', paste)
        self.assertIn('planLabelRequestBatch', SHEET)
        self.assertIn('slots:labelSlots(),pattern:currentLabelPattern()', SHEET)

    def test_node_unit_suite(self):
        script = ROOT / 'tests' / 'test_label_paste.js'
        proc = subprocess.run(['node', str(script)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('All label paste unit tests passed', proc.stdout)


if __name__ == '__main__':
    unittest.main()
