"""シフト表: ×の赤は日曜・祝日だけ（平日・土曜は赤くしない）。"""
import re
import unittest
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / 'style.css').read_text(encoding='utf-8')
RED = ('#9d3434', '#b85c4a', '#fff5f3')


def rules(selector):
    return [m.group(1) for m in re.finditer(r'(?:^|[},])\s*' + re.escape(selector) + r'\s*\{([^}]*)\}', CSS, re.M)]


class ShiftOffColorTest(unittest.TestCase):
    def test_plain_off_is_not_red(self):
        for sel in ('.shift-grid td.shift-off', 'body.printing-shift.print-shift-color .shift-grid td.shift-off'):
            body = ' '.join(rules(sel))
            self.assertTrue(body, sel)
            for c in RED:
                self.assertNotIn(c, body, sel)
            self.assertNotIn('background', body, sel)

    def test_sunday_holiday_off_stays_red(self):
        self.assertIn('#9d3434', ' '.join(rules('.shift-grid td.shift-sun.shift-off')))
        self.assertIn('#b85c4a', ' '.join(rules('body.printing-shift.print-shift-color .shift-grid td.shift-sun.shift-off')))
        js = (Path(__file__).resolve().parents[1] / 'kintai-shift.js').read_text(encoding='utf-8')
        self.assertIn("(d.kind === 'sun' || d.kind === 'holiday') ? 'shift-sun'", js)


if __name__ == '__main__':
    unittest.main()
