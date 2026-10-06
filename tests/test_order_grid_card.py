"""Source assertions for order-list grid card layout (指示書一覧 / 現場 / 下書き)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'app.js').read_text(encoding='utf-8')
CSS = (ROOT / 'display-mode.css').read_text(encoding='utf-8')


def _fn(name: str) -> str:
    m = re.search(rf'function {name}\([^)]*\)\{{', APP_JS)
    if not m:
        raise AssertionError(f'missing function {name}')
    start = m.start()
    nxt = re.search(r'\nfunction ', APP_JS[m.end():])
    end = m.end() + nxt.start() if nxt else len(APP_JS)
    return APP_JS[start:end]


class OrderGridCardLayoutTest(unittest.TestCase):
    def test_request_and_employee_share_third_row(self):
        body = _fn('orderGridCard')
        card = body[body.index('class="dm-card${late}"'):]
        self.assertIn('class="dm-number"', card)
        self.assertIn('class="dm-primary"', card)
        row = re.search(
            r'<div class="dm-meta"><span class="dm-request">.*?</span><span class="dm-employee">.*?</span></div>',
            card,
        )
        self.assertIsNotNone(row, 'request and employee must be adjacent spans in one dm-meta row')
        i_num = card.index('class="dm-number"')
        i_pri = card.index('class="dm-primary"')
        i_meta = card.index('class="dm-request"')
        self.assertLess(i_num, i_pri)
        self.assertLess(i_pri, i_meta)

    def test_mark_ship_date_below_due_chip(self):
        body = _fn('orderGridCard')
        i_status = body.index('orderStatusBand(')
        i_due = body.index('dueChip(')
        i_mark = body.index('class="dm-mark"')
        self.assertLess(i_status, i_due)
        self.assertLess(i_due, i_mark)
        self.assertIn('マーク出荷日 ${mark}', body)
        self.assertNotIn('マーク出荷日 ${mark}</span></div><div class="dm-status">', body)

    def test_floor_owner_stays_on_employee_cell(self):
        body = _fn('orderGridCard')
        self.assertIn("view==='floor'&&o.owner", body)
        self.assertIn('dm-employee', body)
        self.assertNotRegex(
            _fn('orderGridCard').split('if(shipReview)')[1],
            r'dm-secondary">\$\{esc\(o\.employee\)',
        )

    def test_table_status_block_unchanged_order(self):
        block = _fn('orderStatusBlock')
        self.assertIn('orderStatusBand(o,flagChecks)', block)
        self.assertIn('dueChip(o)', block)
        self.assertNotIn('dm-mark', block)
        self.assertNotIn('dm-request', block)

    def test_grid_css_mark_bold_status_narrow_centered(self):
        def rule(selector: str) -> str:
            m = re.search(re.escape(selector) + r'\{([^}]*)\}', CSS)
            self.assertIsNotNone(m, f'missing {selector}')
            return m.group(1)

        mark = rule('.list-grid .dm-card .dm-mark')
        self.assertIn('font-weight:700', mark)

        meta = rule('.list-grid .dm-card .dm-meta')
        self.assertIn('flex-wrap:nowrap', meta)
        self.assertIn('justify-content:flex-start', meta)

        request = rule('.list-grid .dm-card .dm-request')
        self.assertIn('flex:0 1 auto', request)
        self.assertIn('width:max-content', request)
        self.assertIn('min-width:0', request)
        self.assertNotIn('flex:1', request)

        employee = rule('.list-grid .dm-card .dm-employee')
        self.assertIn('flex:0 1 auto', employee)
        self.assertIn('min-width:0', employee)
        self.assertIn('text-overflow:ellipsis', employee)
        self.assertIn('white-space:nowrap', employee)
        self.assertNotIn('flex:1 1 0', employee)

        main = rule('.list-grid .dm-card .dm-status-main')
        self.assertIn('flex:0 0 7.2em', main)
        self.assertIn('max-width:7.2em', main)
        self.assertIn('justify-content:center', main)
        self.assertNotIn('flex:1 1 auto', main)

        badge = rule('.list-grid .dm-card .dm-status-main .badge')
        self.assertIn('text-align:center', badge)

        sel = rule('.list-grid .dm-card .dm-status-main select.flagstatus')
        self.assertIn('text-align:center', sel)
        self.assertIn('text-align-last:center', sel)
        self.assertNotIn('max-width:none', sel)

        band = rule('.list-grid .dm-card .dm-status-band')
        self.assertIn('flex-direction:row', band)
        checks = rule('.list-grid .dm-card .flagchecks')
        self.assertIn('flex-direction:column', checks)


if __name__ == '__main__':
    unittest.main()
