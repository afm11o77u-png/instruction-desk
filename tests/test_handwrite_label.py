"""顧客名＋手書き宛名印刷の配線と印刷専用の保証。"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
SHEET = (ROOT / 'sheet.js').read_text(encoding='utf-8')
SERVER = (ROOT / 'server.py').read_text(encoding='utf-8')
HANDWRITE = (ROOT / 'handwrite_label.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'style.css').read_text(encoding='utf-8')


class HandwriteLabelWiringTest(unittest.TestCase):
    def test_module_loaded_and_served(self):
        self.assertIn('src="/handwrite_label.js"', INDEX)
        self.assertLess(INDEX.index('src="/handwrite_label.js"'), INDEX.index('src="/app.js"'))
        self.assertLess(INDEX.index('src="/handwrite_label.js"'), INDEX.index('src="/sheet.js"'))
        self.assertIn("'/handwrite_label.js':'handwrite_label.js'", SERVER)

    def test_label_desk_has_handwrite_mode_beside_fixed(self):
        self.assertIn('id="labelmodefixed"', INDEX)
        self.assertIn('id="labelmodehandwrite"', INDEX)
        self.assertIn('定型書式印刷', INDEX)
        self.assertIn('顧客名＋手書き', INDEX)
        self.assertLess(INDEX.index('id="labelmodefixed"'), INDEX.index('id="labelmodehandwrite"'))
        self.assertIn('id="labelhandwritepanel"', INDEX)
        self.assertIn('id="handwritecustomer"', INDEX)
        self.assertIn('id="handwritesheets"', INDEX)
        self.assertIn('id="handwriteprint"', INDEX)
        self.assertIn('印刷する用紙枚数', INDEX)
        self.assertIn('id="labeladdresspanel"', INDEX)
        self.assertIn('id="labelfixedpanel"', INDEX)
        self.assertIn('id="labelticketpanel"', INDEX)

    def test_sheet_mode_and_print_only_path(self):
        self.assertIn("mode==='handwrite'", SHEET)
        self.assertIn('labelmodehandwrite', SHEET)
        self.assertIn('labelhandwritepanel', SHEET)
        self.assertIn('function printHandwriteLabel', SHEET)
        self.assertIn('function openHandwriteLabelPreview', SHEET)
        start = SHEET.index('function printHandwriteLabel')
        end = SHEET.index("$('#handwriteprint')", start)
        body = SHEET[start:end]
        for forbidden in (
            "api('/api/orders",
            'api("/api/orders',
            'fetch(',
            '/api/orders',
        ):
            self.assertNotIn(forbidden, body)
        self.assertIn('validateHandwriteLabelPrintInput', body)
        self.assertIn('openHandwriteLabelPreview', body)
        self.assertIn('印刷専用', SHEET)
        self.assertIn('入力内容は印刷後も保持', SHEET)

    def test_css_and_layout(self):
        self.assertIn('@page{size:A4 landscape;margin:0}', HANDWRITE)
        self.assertIn('148.5mm', HANDWRITE)
        self.assertIn('105mm', HANDWRITE)
        self.assertIn('顧客名を入力してください', HANDWRITE)
        self.assertIn('印刷する用紙枚数は1以上の整数', HANDWRITE)
        self.assertIn('handwriteLabelIsPrintOnly', HANDWRITE)
        self.assertIn('handwriteLabelMutatesOrders', HANDWRITE)
        self.assertIn('sourcePageRotate', HANDWRITE)  # comment about sample /Rotate 180
        self.assertIn('/Rotate 180', HANDWRITE)
        self.assertIn('.labelhandwriteform', STYLE)
        # 既存の宛名・定型・管理票を壊していないこと
        self.assertIn('id="labelmodeaddress"', INDEX)
        self.assertIn('id="labelmodefixed"', INDEX)
        self.assertIn('id="labelmodeticket"', INDEX)

    def test_sample_reference_asset(self):
        path = ROOT / 'fixed-label' / '2238-customer-handwrite.pdf'
        self.assertTrue(path.is_file(), str(path))
        self.assertGreater(path.stat().st_size, 1000)

    def test_preview_guides(self):
        self.assertIn('ヘッダーとフッター', INDEX)
        self.assertIn('部数は1部', INDEX)
        self.assertIn('アプリから強制解除はできません', INDEX)


class HandwriteLabelUnitTest(unittest.TestCase):
    def test_node_unit(self):
        script = ROOT / 'tests' / 'test_handwrite_label.js'
        completed = subprocess.run(
            ['node', str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('ok', completed.stdout)


class HandwriteLabelPrintMeasureTest(unittest.TestCase):
    """Headless Chrome がある環境では 1/3/10 枚のページ数と MediaBox を証明する。"""

    def test_pdf_page_counts_for_sheet_inputs(self):
        chrome = None
        for candidate in (
            'google-chrome-stable',
            'google-chrome',
            'chromium-browser',
            'chromium',
        ):
            found = subprocess.run(['which', candidate], capture_output=True, text=True)
            if found.returncode == 0:
                chrome = found.stdout.strip()
                break
        if not chrome:
            self.skipTest('Chrome/Chromium not available for PDF measure')
        import os

        script = ROOT / 'tests' / 'measure_handwrite_label_pdf.js'
        env = os.environ.copy()
        env['CHROME_PATH'] = chrome
        completed = subprocess.run(
            ['node', str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('"ok": true', completed.stdout)
        self.assertIn('"sheets": 1', completed.stdout)
        self.assertIn('"sheets": 3', completed.stdout)
        self.assertIn('"sheets": 10', completed.stdout)
        self.assertIn('"pageCount": 1', completed.stdout)
        self.assertIn('"pageCount": 3', completed.stdout)
        self.assertIn('"pageCount": 10', completed.stdout)
        self.assertIn('"widthMm": 297', completed.stdout)
        self.assertRegex(completed.stdout, r'"heightMm": 20[89]|210')
        self.assertIn('"cellsPerPage": 4', completed.stdout)
        self.assertIn('"hasCustomerName": true', completed.stdout)
        self.assertIn('"hasSama": true', completed.stdout)
        self.assertIn('"personalNameBlank": true', completed.stdout)


if __name__ == '__main__':
    unittest.main()
