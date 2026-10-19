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
        self.assertIn('type="radio"', INDEX[INDEX.index('labelticketkind'):INDEX.index('labelticketkind') + 200])
        self.assertNotIn('type="checkbox"', INDEX[INDEX.index('class="labelticketkind"'):INDEX.index('class="labelticketkind"') + 280])
        self.assertIn('value="repair"', INDEX)
        self.assertIn('value="shortage"', INDEX)
        self.assertIn('id="ticketprint"', INDEX)
        self.assertIn('id="labeladdresspanel"', INDEX)
        self.assertIn('id="labelprint"', INDEX)
        # 指示書番号入力は廃止。印刷位置には当日日付＋「発注」を出す。
        ticket_panel = INDEX[INDEX.index('id="labelticketpanel"'):INDEX.index('id="labelfixedpanel"')]
        self.assertNotIn('ticknumber', ticket_panel)
        self.assertNotIn('指示書番号', ticket_panel)
        self.assertIn('boxTicketOrderDateLabel', BOX)
        self.assertIn(' 発注', BOX)
        self.assertNotIn('指示書番号を入力してください', BOX)
        self.assertNotIn("number:$('#ticknumber')", SHEET)

    def test_ticket_kind_radio_css_resets_global_input(self):
        # グローバル input{width:100%;min-height:43px;border…} を打ち消し、通常の小さいラジオにする。
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn('#labeldesk .labelpattern input[type=radio],#labeldesk .labelticketkind input[type=radio]', css)
        rule_start = css.index('#labeldesk .labelpattern input[type=radio],#labeldesk .labelticketkind input[type=radio]')
        rule = css[rule_start:css.index('}', rule_start) + 1]
        for needle in ('all:unset', 'min-height:0', 'border:0', 'padding:0', 'background:transparent', '-webkit-appearance:radio', 'width:11px', 'height:11px'):
            self.assertIn(needle, rule)
        # ラベル文字だけ大きく、部品サイズは据え置き。
        self.assertIn('#labeldesk .labelticketkind label{display:inline-flex;flex-direction:row;gap:4px;align-items:center;font-size:16px;', css)

    def test_app_reuses_shared_pages_and_preview(self):
        self.assertIn('deskBoxTicketPages', APP)
        self.assertIn('boxTicketPreviewHtml', APP)
        self.assertIn('BOX_TICKET_CSS', APP)
        self.assertIn('function openBoxTicketPreview', APP)
        # Layout HTML is no longer duplicated as a local template string with ticket class sections.
        self.assertNotIn('不足なし（修理待ち）', APP)
        self.assertIn('不足なし（修理待ち）', BOX)

    def test_full_a4_landscape_six_split_css(self):
        self.assertIn('@page{size:A4 landscape;margin:0}', BOX)
        self.assertIn('width:297mm', BOX)
        self.assertIn('height:210mm', BOX)
        self.assertIn('148.5mm', BOX)
        self.assertIn('70mm', BOX)
        self.assertIn('BOX_TICKET_PER_PAGE = 6', BOX)
        self.assertIn('grid-template-columns:repeat(2,148.5mm)', BOX)
        self.assertIn('grid-template-rows:repeat(3,70mm)', BOX)
        self.assertNotIn('52.5mm', BOX)
        self.assertNotIn('repeat(4,', BOX)
        self.assertNotIn('136.5mm', BOX)
        self.assertNotIn('46.5mm', BOX)
        self.assertNotIn('273mm', BOX)
        self.assertNotIn('186mm', BOX)
        self.assertNotIn('margin:18mm', BOX)
        self.assertIn('class="instr"', BOX)
        self.assertIn('orderDateLabel', BOX)
        self.assertIn('instructionNumber', BOX)
        self.assertIn('ticket-empty', BOX)

    def test_manual_multi_person_ui(self):
        self.assertIn('id="labelticketpeople"', INDEX)
        self.assertIn('id="ticketaddperson"', INDEX)
        self.assertIn('1人分を追加', INDEX)
        self.assertIn('1ページ6枚', INDEX)
        self.assertIn('addLabelTicketPerson', SHEET)
        self.assertIn('buildManualBoxTicketOrders', SHEET)
        self.assertIn('BOX_TICKET_MANUAL_MAX_PEOPLE', SHEET)
        self.assertIn('instructionNumber', SHEET)
        self.assertIn('入力済みの人を削除しますか', SHEET)
        # 現場一覧のページ計算は6枠単位（旧8割は残さない）
        self.assertNotIn('Math.ceil(n/8)', APP)
        self.assertIn('BOX_TICKET_PER_PAGE', APP)
        self.assertIn('boxTicketPageCount', APP)
        self.assertIn('1ページ${perPage}枠', APP)

    def test_preview_guides_browser_header_footer_off(self):
        self.assertIn('ヘッダーとフッター', APP)
        self.assertIn('アプリから強制解除はできません', APP)
        self.assertIn('余白なし', APP)
        self.assertIn('倍率100％', APP)

    def test_manual_print_path_does_not_call_order_apis(self):
        self.assertIn('printManualBoxTicket', SHEET)
        start = SHEET.index('function printManualBoxTicket')
        end = SHEET.index('function fillFixedLabelFormatSelect()', start)
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
        self.assertIn('buildManualBoxTicketOrders', body)
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


class BoxTicketPrintMeasureTest(unittest.TestCase):
    """Headless Chrome がある環境では PDF MediaBox と改ページを証明する。"""

    def test_pdf_page_size_and_breaks(self):
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

        script = ROOT / 'tests' / 'measure_box_ticket_pdf.js'
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
        self.assertIn('"pageCount": 1', completed.stdout)
        self.assertIn('"pageCount": 2', completed.stdout)
        self.assertIn('"widthMm": 297', completed.stdout)
        self.assertIn('"heightMm": 209', completed.stdout)


if __name__ == '__main__':
    unittest.main()
