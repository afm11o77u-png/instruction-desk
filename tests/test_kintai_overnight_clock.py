import unittest
from datetime import datetime

import kintai


class OvernightClockTest(unittest.TestCase):
    def test_next_day_end_time(self):
        self.assertEqual(kintai._display_clock(1.20625), '28:57')
        self.assertEqual(kintai._daily_cell_text(1.20625, {'clock': True}), '28:57')

    def test_midnight_and_boundaries(self):
        for value, expected in [(1, '24:00'), (1.5, '36:00'), (2, '48:00'), (9 / 24, '9:00'), (70 / 1440, '1:10')]:
            with self.subTest(value=value):
                self.assertEqual(kintai._display_clock(value), expected)

    def test_other_values_and_hour_columns_unchanged(self):
        self.assertEqual(kintai._display_clock(None), '')
        self.assertEqual(kintai._display_clock(''), '')
        self.assertEqual(kintai._display_clock('28:57'), '28:57')
        self.assertEqual(kintai._display_clock(46000), kintai._display_raw(46000))
        self.assertEqual(kintai._display_clock(datetime(2026, 10, 11, 4, 57)), '4:57')
        self.assertEqual(kintai._daily_cell_text(1.20625, {'hours': True}), '1.21')


if __name__ == '__main__':
    unittest.main()
