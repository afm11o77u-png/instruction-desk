"""定型書式印刷の配線と印刷専用の保証。"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
SHEET = (ROOT / 'sheet.js').read_text(encoding='utf-8')
SERVER = (ROOT / 'server.py').read_text(encoding='utf-8')
FIXED = (ROOT / 'fixed_label.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'style.css').read_text(encoding='utf-8')


class FixedLabelWiringTest(unittest.TestCase):
    def test_module_loaded_and_served(self):
        self.assertIn('src="/fixed_label.js"', INDEX)
        self.assertLess(INDEX.index('src="/fixed_label.js"'), INDEX.index('src="/app.js"'))
        self.assertLess(INDEX.index('src="/fixed_label.js"'), INDEX.index('src="/sheet.js"'))
        self.assertIn("'/fixed_label.js':'fixed_label.js'", SERVER)

    def test_label_desk_has_fixed_mode_beside_ticket(self):
        self.assertIn('id="labelmodeticket"', INDEX)
        self.assertIn('id="labelmodefixed"', INDEX)
        self.assertIn('定型書式印刷', INDEX)
        self.assertIn('管理票印刷', INDEX)
        self.assertLess(INDEX.index('id="labelmodeticket"'), INDEX.index('id="labelmodefixed"'))
        self.assertIn('id="labelfixedpanel"', INDEX)
        self.assertIn('id="fixedformat"', INDEX)
        self.assertIn('id="fixedsheets"', INDEX)
        self.assertIn('id="fixedprint"', INDEX)
        self.assertIn('印刷する用紙枚数', INDEX)
        self.assertIn('部数は1部', INDEX)
        self.assertIn('id="labeladdresspanel"', INDEX)
        self.assertIn('id="labelticketpanel"', INDEX)

    def test_sheet_mode_and_print_only_path(self):
        self.assertIn("mode==='fixed'", SHEET)
        self.assertIn('labelmodefixed', SHEET)
        self.assertIn('labelfixedpanel', SHEET)
        self.assertIn('function printFixedLabel', SHEET)
        self.assertIn('function openFixedLabelPreview', SHEET)
        self.assertIn('fillFixedLabelFormatSelect', SHEET)
        start = SHEET.index('function printFixedLabel')
        end = SHEET.index('fillFixedLabelFormatSelect();', start)
        body = SHEET[start:end]
        for forbidden in (
            "api('/api/orders",
            'api("/api/orders',
            'fetch(',
            '/api/orders',
        ):
            self.assertNotIn(forbidden, body)
        self.assertIn('validateFixedLabelPrintInput', body)
        self.assertIn('openFixedLabelPreview', body)
        self.assertIn('印刷専用', SHEET)

    def test_css_and_registry(self):
        self.assertIn('@page{size:A4 landscape;margin:0}', FIXED)
        self.assertIn('148.5mm', FIXED)
        self.assertIn('105mm', FIXED)
        self.assertIn('dashed', FIXED)
        self.assertIn('daruma-zenibako', FIXED)
        self.assertIn('だるま食品銭函工場', FIXED)
        self.assertIn('製造１課', FIXED)
        self.assertIn('設備保全課', FIXED)
        self.assertIn('工場長', FIXED)
        self.assertIn('新規　交換　その他', FIXED)
        self.assertIn('fixedLabelIsPrintOnly', FIXED)
        self.assertIn('fixedLabelMutatesOrders', FIXED)
        self.assertIn('.labelfixedform', STYLE)

    def test_preview_guides(self):
        self.assertIn('ヘッダーとフッター', INDEX)
        self.assertIn('部数は1部', INDEX)
        self.assertIn('アプリから強制解除はできません', INDEX)
        self.assertIn('部数は1部', SHEET)


class FixedLabelUnitTest(unittest.TestCase):
    def test_node_unit(self):
        script = ROOT / 'tests' / 'test_fixed_label.js'
        completed = subprocess.run(
            ['node', str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('ok', completed.stdout)


class FixedLabelPrintMeasureTest(unittest.TestCase):
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

        script = ROOT / 'tests' / 'measure_fixed_label_pdf.js'
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


if __name__ == '__main__':
    unittest.main()
