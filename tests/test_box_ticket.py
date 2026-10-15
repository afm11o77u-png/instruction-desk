"""管理票印刷（手入力再発行）の配線と印刷専用の保証。"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / 'index.html').read_text(encoding='utf-8')
SHEET = (ROOT / 'sheet.js').read_text(encoding='utf-8')
APP = (ROOT / 'app.js').read_text(encoding='utf-8')
SERVER = (ROOT / 'server.py').read_text(encoding='utf-8')
BOX = (ROOT / 'box_ticket.js').read_text(encoding='utf-8')


class BoxTicketSharedLayoutTest(unittest.TestCase):
    def test_shared_module_loaded_before_app_and_sheet(self):
        self.assertIn('src="/box_ticket.js"', INDEX)
        self.assertLess(INDEX.index('src="/box_ticket.js"'), INDEX.index('src="/app.js"'))
        self.assertLess(INDEX.index('src="/box_ticket.js"'), INDEX.index('src="/sheet.js"'))
        self.assertIn("'/box_ticket.js':'box_ticket.js'", SERVER)

    def test_label_desk_has_ticket_mode_without_removing_address(self):
        self.assertIn('id="labelmodeaddress"', INDEX)
        self.assertIn('id="labelmodenotice"', INDEX)
        self.assertIn('id="labelmodeticket"', INDEX)
        self.assertIn('管理票印刷', INDEX)
        self.assertIn('id="labelticketpanel"', INDEX)
        self.assertIn('name="labelticketkind"', INDEX)
        self.assertIn('value="repair"', INDEX)
        self.assertIn('value="shortage"', INDEX)
        self.assertIn('id="ticketprint"', INDEX)
        self.assertIn('id="labeladdresspanel"', INDEX)
        self.assertIn('id="labelprint"', INDEX)

    def test_app_reuses_shared_pages_and_preview(self):
        self.assertIn('deskBoxTicketPages', APP)
        self.assertIn('boxTicketPreviewHtml', APP)
        self.assertIn('BOX_TICKET_CSS', APP)
        self.assertIn('function openBoxTicketPreview', APP)
        # Layout HTML is no longer duplicated as a local template string with ticket class sections.
        self.assertNotIn('不足なし（修理待ち）', APP)
        self.assertIn('不足なし（修理待ち）', BOX)

    def test_manual_print_path_does_not_call_order_apis(self):
        self.assertIn('printManualBoxTicket', SHEET)
        start = SHEET.index('function printManualBoxTicket')
        end = SHEET.index('document.querySelectorAll(\'input[name="labelticketkind"]\'', start)
        body = SHEET[start:end]
        for forbidden in (
            "api('/api/orders",
            'api("/api/orders',
            "api('/api/orders/floor-progress",
            'fetch(',
            '/api/orders',
        ):
            self.assertNotIn(forbidden, body)
        self.assertIn('openBoxTicketPreview', body)
        self.assertIn('buildManualBoxTicketOrder', body)
        self.assertIn('validateManualBoxTicketOrder', body)
        self.assertIn('案件は登録していません', body)

    def test_shared_module_declares_print_only(self):
        self.assertIn('manualBoxTicketIsPrintOnly', BOX)
        self.assertIn('manualBoxTicketMutatesOrders', BOX)
        self.assertIn('printOnly: true', BOX)


class BoxTicketUnitTest(unittest.TestCase):
    def test_node_unit(self):
        script = ROOT / 'tests' / 'test_box_ticket.js'
        completed = subprocess.run(
            ['node', str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('ok', completed.stdout)


if __name__ == '__main__':
    unittest.main()
