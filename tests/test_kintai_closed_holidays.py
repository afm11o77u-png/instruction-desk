import unittest
from datetime import date
import kintai


class ClosedHolidayTests(unittest.TestCase):
    def test_period_crosses_month_and_year(self):
        calc = kintai.closed_period_holidays
        self.assertEqual(calc(2026, 6, 20, 0, date(2026, 6, 16)), 11)
        self.assertEqual(calc(2027, 1, 20, 1, date(2027, 1, 16)), 10)
        self.assertEqual(calc(2026, 3, 20, 0, date(2026, 3, 16)), 8)
        self.assertEqual(calc(2028, 3, 20, 0, date(2028, 3, 16)), 9)

    def test_pending_and_invalid_are_not_zero(self):
        calc = kintai.closed_period_holidays
        for day in (date(2026, 6, 1), date(2026, 6, 15)):
            self.assertIsNone(calc(2026, 6, 20, 0, day))
        for work, paid in ((0, 0), (0, 1), (None, 0), (20, None), (32, 0), (20, -1)):
            self.assertIsNone(calc(2026, 6, work, paid, date(2026, 7, 1)))

    def test_half_day_and_zero_holidays(self):
        calc = kintai.closed_period_holidays
        self.assertEqual(calc(2026, 6, 20.5, .5, date(2026, 6, 16)), 10)
        self.assertEqual(calc(2026, 6, 31, 0, date(2026, 6, 16)), 0)
