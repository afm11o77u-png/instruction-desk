"""入社日以降の日だけを休日数と出勤合計に使う。一時DBと合成xlsxだけ。"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import kintai

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_kintai_daily as daily_fix  # noqa: E402

_xlsx = daily_fix._xlsx
_serial = daily_fix._serial


def _clock(hour, minute=0):
    return (hour * 60 + minute) / (24 * 60)


def _headers():
    return [
        (3, 5, '始業時間（00：00）'),
        (3, 7, '終業時間（00：00）'),
    ]


def _one_day(day, worked):
    cells = list(_headers())
    cells.append((5, 1, _serial(day)))
    if worked:
        cells.append((5, 5, _clock(9)))
        cells.append((5, 7, _clock(18)))
    return cells


def _may_sheet():
    """2026年5月度（4/16〜5/15）。5/15だけ出勤、それ以外は始終空欄。"""
    cells = list(_headers())
    day = date(2026, 4, 16)
    row = 5
    while day <= date(2026, 5, 15):
        cells.append((row, 1, _serial(day)))
        if day == date(2026, 4, 16) or day == date(2026, 5, 15):
            cells.append((row, 5, _clock(9)))
            cells.append((row, 7, _clock(18)))
        row += 1
        day += timedelta(days=1)
    return cells


class HireWindowTest(unittest.TestCase):
    def test_may_14_uses_two_days_and_months_before_may_are_outside(self):
        window = kintai.holiday_count_window(2026, 5, date(2026, 5, 14))
        self.assertEqual(window['scope'], 'partial')
        self.assertEqual(window['start'], date(2026, 5, 14))
        self.assertEqual(window['end'], date(2026, 5, 15))
        self.assertEqual(window['days'], 2)
        for month in (2, 3, 4):
            self.assertEqual(kintai.holiday_count_window(2026, month, '2026-05-14')['scope'], 'before-hire')
        self.assertEqual(kintai.holiday_count_window(2026, 5, None)['scope'], 'full')
        self.assertEqual(kintai.holiday_count_window(2026, 5, None)['days'], 30)
        self.assertEqual(kintai.holiday_count_window(2026, 5, date(2026, 4, 16))['scope'], 'full')
        self.assertEqual(kintai.holiday_count_window(2026, 5, date(2026, 5, 15))['days'], 1)
        self.assertEqual(kintai.holiday_count_window(2026, 12, date(2026, 5, 14))['scope'], 'full')
        self.assertEqual(kintai.displayed_holiday_days(2026, 5, 1, 0, span_days=2), 1)


class HireHolidayRosterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        kintai.bind(self.data)
        self.db = self.data / 't.sqlite3'
        self.c = sqlite3.connect(self.db)
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, version INTEGER)"
        )
        kintai.schema(self.c)
        people = list(self.c.execute('SELECT * FROM kintai_people WHERE active=1 ORDER BY id LIMIT 2'))
        self.person = people[0]
        self.other = people[1]
        self.c.execute(
            'UPDATE kintai_people SET employment=?, hired_on=? WHERE id=?',
            ('contract', '2026-05-14', self.person['id']),
        )
        fixtures = self.data / 'kintai-fixtures'
        fixtures.mkdir()
        self.book = fixtures / 'hire.xlsx'
        self.book.write_bytes(_book())
        self.before = self.book.read_bytes()
        self.c.execute('UPDATE kintai_people SET source_file=? WHERE id=?', ('hire.xlsx', self.person['id']))
        (self.data / 'kintai-local.json').write_text(json.dumps({
            'shareRoot': '',
            'layoutId': 'shukkinbo-2026-monthdot-v1',
            'peopleFiles': {},
            'peopleLayouts': {str(self.person['id']): 'shukkinbo-2026-monthdot-v1'},
        }, ensure_ascii=False), encoding='utf-8')
        for month in range(2, 13):
            year = 2026 if month >= 2 else 2027
            paid = 2 if month == 2 else 0
            self._paid(year, month, paid)
        self._paid(2027, 1, 0)
        self.c.commit()
        self.user = {'id': 1, 'name': '主', 'login': 'o', 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _paid(self, year, month, paid):
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'),'ok','')''',
            (self.person['id'], year, month, 99, 99, paid, 0, 0, ''),
        )

    def months(self):
        view = kintai.list_overview(self.c, 2026, person_id=self.person['id'])
        return {(m['year'], m['month']): m for m in view['rows'][0]['months']}

    def test_pre_hire_months_are_skipped_and_may_uses_two_days(self):
        kintai.save_daily_work_days(self.c, self.user, {
            'personId': self.person['id'],
            'year': 2026,
            'month': 2,
            'days': [{'date': '2026-01-16', 'days': 2}],
        })
        kintai.save_daily_work_days(self.c, self.user, {
            'personId': self.person['id'],
            'year': 2026,
            'month': 5,
            'days': [{'date': '2026-05-13', 'days': 2}],
        })
        months = self.months()
        february = months[(2026, 2)]
        may = months[(2026, 5)]
        self.assertTrue(february['holidaySkip'])
        self.assertTrue(february['workSkip'])
        self.assertIsNone(february['holidayDays'])
        self.assertIsNone(february['workDays'])
        self.assertIsNone(february['holidayCumulative'])
        self.assertEqual(february['paidLeaveCumulative'], 2)
        self.assertEqual(may['holidayScope'], 'partial')
        self.assertEqual(may['holidaySpanDays'], 2)
        self.assertEqual(may['workDays'], 1)
        self.assertTrue(may['workDaysComplete'])
        self.assertEqual(may['holidayDays'], 1)
        self.assertEqual(may['holidayCumulative'], 1)
        self.assertEqual(may['workCumulative'], 1)
        for month in (3, 4):
            self.assertTrue(months[(2026, month)]['holidaySkip'])
            self.assertIsNone(months[(2026, month)]['holidayCumulative'])
        december = months[(2026, 12)]
        self.assertEqual(december['holidayScope'], 'full')
        self.assertEqual(december['holidayDays'], kintai.closing_period_days(2026, 12))
        january = months[(2027, 1)]
        roster = kintai.list_roster(self.c, 2026)
        row = next(r for r in roster['rows'] if r['personId'] == self.person['id'])
        other = next(r for r in roster['rows'] if r['personId'] == self.other['id'])
        self.assertEqual(row['holidayCumulative'], january['holidayCumulative'])
        self.assertEqual(row['workCumulative'], january['workCumulative'])
        self.assertEqual(row['holidayCumulative'], may['holidayDays'] + sum(
            kintai.closing_period_days(year, month)
            for year, month in (
                (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12), (2027, 1),
            )
        ))
        self.assertIsNone(other['holidayCumulative'])
        self.assertEqual(self.book.read_bytes(), self.before)
        daily = kintai.person_daily_sheet(self.c, self.person['id'], 2026, 5)
        self.assertEqual(daily['workDaysTotal'], may['workDays'])
        by_date = {row['date']: row for row in daily['rows']}
        self.assertFalse(by_date['2026/04/16']['workDaysInRange'])
        self.assertEqual(by_date['2026/04/16']['workDays'], 1)
        self.assertFalse(by_date['2026/05/13']['workDaysInRange'])
        self.assertEqual(by_date['2026/05/13']['workDays'], 2)
        self.assertTrue(by_date['2026/05/14']['workDaysInRange'])
        self.assertEqual(by_date['2026/05/14']['workDays'], 0)
        self.assertTrue(by_date['2026/05/15']['workDaysInRange'])
        self.assertEqual(daily['countScope'], 'partial')

    def test_saved_day_on_hire_date_changes_the_same_totals(self):
        kintai.save_daily_work_days(self.c, self.user, {
            'personId': self.person['id'],
            'year': 2026,
            'month': 5,
            'days': [{'date': '2026-05-14', 'days': 0.5}],
        })
        may = self.months()[(2026, 5)]
        roster = kintai.list_roster(self.c, 2026)
        row = next(r for r in roster['rows'] if r['personId'] == self.person['id'])
        self.assertEqual(may['workDays'], 1.5)
        self.assertEqual(may['holidayDays'], 0.5)
        self.assertEqual(kintai.person_daily_sheet(self.c, self.person['id'], 2026, 5)['workDaysTotal'], 1.5)
        self.assertEqual(row['workCumulative'], self.months()[(2027, 1)]['workCumulative'])
        self.assertEqual(row['holidayCumulative'], self.months()[(2027, 1)]['holidayCumulative'])

    def test_nonzero_monthly_paid_leave_is_not_split(self):
        self.c.execute(
            'UPDATE kintai_monthly SET paid_leave_days=1 WHERE person_id=? AND year=2026 AND month=5',
            (self.person['id'],),
        )
        self.c.commit()
        may = self.months()[(2026, 5)]
        self.assertEqual(may['workDays'], 1)
        self.assertIsNone(may['holidayDays'])
        self.assertIn('振り分け', may['holidayNote'])
        self.assertIsNone(may['holidayCumulative'])
        roster = kintai.list_roster(self.c, 2026)
        row = next(r for r in roster['rows'] if r['personId'] == self.person['id'])
        self.assertIsNone(row['holidayCumulative'])
        self.assertEqual(self.book.read_bytes(), self.before)

    def test_without_hire_date_december_blank_clock_stays_a_full_holiday_month(self):
        self.c.execute('UPDATE kintai_people SET hired_on=NULL WHERE id=?', (self.person['id'],))
        self.c.commit()
        december = self.months()[(2026, 12)]
        self.assertFalse(december.get('holidaySkip'))
        self.assertEqual(december['workDays'], 0)
        self.assertEqual(december['holidayDays'], kintai.closing_period_days(2026, 12))


def _book() -> bytes:
    sheets = {
        '2026.2': _one_day(date(2026, 1, 16), worked=True),
        '2026.5': _may_sheet(),
    }
    for month in range(6, 13):
        start, _end = kintai.closing_period(2026, month)
        sheets[f'2026.{month}'] = _one_day(start, worked=False)
    start, _end = kintai.closing_period(2027, 1)
    sheets['2027.1'] = _one_day(start, worked=False)
    return _xlsx(sheets)


if __name__ == '__main__':
    unittest.main()
