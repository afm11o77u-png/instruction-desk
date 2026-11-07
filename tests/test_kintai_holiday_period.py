"""休日数は締め期間の暦日数から出勤日数と有給取得日数を引く。一時DBと合成xlsxだけ。"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import kintai

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_kintai_daily as daily_fix  # noqa: E402

_xlsx = daily_fix._xlsx
_serial = daily_fix._serial


def _clock(hour, minute=0):
    return (hour * 60 + minute) / (24 * 60)


class HolidayFormulaTest(unittest.TestCase):
    def test_period_lengths_and_examples(self):
        self.assertEqual(kintai.closing_period_days(2026, 7), 30)
        self.assertEqual(kintai.closing_period_days(2026, 2), 31)
        self.assertEqual(kintai.closing_period_days(2026, 3), 28)
        self.assertEqual(kintai.closing_period_days(2024, 3), 29)
        self.assertEqual(kintai.closing_period_days(2027, 1), 31)
        self.assertEqual(kintai.displayed_holiday_days(2026, 7, 17.5, 1.5), 11)
        self.assertEqual(kintai.displayed_holiday_days(2026, 7, 17.5, 0), 12.5)
        self.assertEqual(kintai.displayed_holiday_days(2026, 3, 20, 10), -2)
        self.assertIsNone(kintai.displayed_holiday_days(2026, 7, None, 0))
        self.assertIsNone(kintai.displayed_holiday_days(2026, 7, 17.5, None))
        self.assertIsNone(kintai.displayed_holiday_days(2026, 7, 17.5, 0, complete=False))


class HolidayFromAttendanceTest(unittest.TestCase):
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
        fixtures = self.data / 'kintai-fixtures'
        fixtures.mkdir()
        self.book = fixtures / 'holiday.xlsx'
        self.book.write_bytes(_feb_and_march())
        self.before = self.book.read_bytes()
        for person in (self.person, self.other):
            self.c.execute('UPDATE kintai_people SET source_file=? WHERE id=?', ('holiday.xlsx', person['id']))
        (self.data / 'kintai-local.json').write_text(json.dumps({
            'shareRoot': '',
            'layoutId': 'shukkinbo-2026-monthdot-v1',
            'peopleFiles': {},
            'peopleLayouts': {
                str(self.person['id']): 'shukkinbo-2026-monthdot-v1',
                str(self.other['id']): 'shukkinbo-2026-monthdot-v1',
            },
        }, ensure_ascii=False), encoding='utf-8')
        self.c.commit()
        self.user = {'id': 1, 'name': '主', 'login': 'o', 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def put_paid(self, year, month, paid, holiday_stored=99):
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'),'ok','')''',
            (self.person['id'], year, month, 99, holiday_stored, paid, 60, 0, ''),
        )
        self.c.commit()

    def _month(self, year, month):
        view = kintai.list_overview(self.c, 2026, person_id=self.person['id'])
        return next(m for m in view['rows'][0]['months'] if (m['year'], m['month']) == (year, month))

    def march(self):
        return self._month(2026, 3)

    def test_half_day_paid_leave_and_saved_attendance_change_holiday(self):
        self.put_paid(2026, 2, 0)
        self.put_paid(2026, 3, 1)
        feb = self._month(2026, 2)
        march = self.march()
        self.assertEqual(feb['workDays'], 1)
        self.assertEqual(feb['holidayDays'], 30)
        self.assertEqual(march['workDays'], 1.5)
        self.assertTrue(march['workDaysComplete'])
        self.assertEqual(march['paidLeaveDays'], 1)
        self.assertEqual(march['holidayDays'], 25.5)
        self.assertEqual(march['holidayCumulative'], 55.5)
        self.assertEqual(self.c.execute(
            'SELECT holiday_days FROM kintai_monthly WHERE person_id=? AND year=2026 AND month=3',
            (self.person['id'],),
        ).fetchone()['holiday_days'], 99)
        self.assertEqual(self.book.read_bytes(), self.before)
        kintai.save_daily_work_days(self.c, self.user, {
            'personId': self.person['id'],
            'year': 2026,
            'month': 3,
            'days': [{'date': '2026-02-16', 'days': 0}],
        })
        march = self.march()
        self.assertEqual(march['workDays'], 0.5)
        self.assertEqual(march['holidayDays'], 26.5)
        self.assertEqual(march['holidayCumulative'], 56.5)
        roster = kintai.list_roster(self.c, 2026)
        row = next(r for r in roster['rows'] if r['personId'] == self.person['id'])
        self.assertEqual(row['holidayCumulative'], march['holidayCumulative'])
        self.assertEqual(row['holidayCumulative'], 56.5)
        other = kintai.list_roster(self.c, 2026)
        other_row = next(r for r in other['rows'] if r['personId'] == self.other['id'])
        self.assertIsNone(other_row['holidayCumulative'])

    def test_missing_paid_leave_unset_day_and_negative_stay_visible(self):
        self.assertIsNone(self.march()['holidayDays'])
        self.put_paid(2026, 3, 0)
        self.assertEqual(self.march()['holidayDays'], 26.5)
        self.c.execute(
            'UPDATE kintai_monthly SET paid_leave_days=30 WHERE person_id=? AND year=2026 AND month=3',
            (self.person['id'],),
        )
        self.c.commit()
        self.assertEqual(self.march()['holidayDays'], -3.5)
        self.book.write_bytes(_two_day_march(unset=True))
        self.assertEqual(self.march()['workDays'], 1.5)
        self.assertFalse(self.march()['workDaysComplete'])
        self.assertIsNone(self.march()['holidayDays'])

    def test_leap_and_year_wrap_use_the_same_span(self):
        leap_book, leap_layout = _one_day_book(2024, date(2024, 2, 16))
        plain_book, plain_layout = _one_day_book(2026, date(2026, 2, 16))
        self.assertEqual(kintai.closing_period_days(2024, 3), 29)
        self.assertEqual(kintai.closing_period_days(2026, 3), 28)
        leap_view = kintai.daily_rows_from_workbook(leap_book, 2024, 3, leap_layout)
        plain_view = kintai.daily_rows_from_workbook(plain_book, 2026, 3, plain_layout)
        self.assertEqual([row['date'] for row in leap_view['rows']], ['2024/02/16'])
        self.assertEqual(leap_view['rows'][0]['workDays'], 1)
        self.assertEqual([row['date'] for row in plain_view['rows']], ['2026/02/16'])
        self.assertEqual(kintai.displayed_holiday_days(2024, 3, 1, 0), 28)
        self.assertEqual(kintai.displayed_holiday_days(2026, 3, 1, 0), 27)
        self.assertEqual(kintai.displayed_holiday_days(2027, 1, 1, 0), 30)


def _day_headers():
    return [
        (3, 4, '休み'),
        (3, 5, '始業時間（00：00）'),
        (3, 7, '終業時間（00：00）'),
    ]


def _feb_and_march() -> bytes:
    february = _day_headers() + [
        (5, 1, _serial(date(2026, 1, 16))),
        (5, 5, _clock(9, 0)),
        (5, 7, _clock(18, 0)),
    ]
    march = _day_headers() + [
        (5, 1, _serial(date(2026, 2, 16))),
        (5, 5, _clock(9, 0)),
        (5, 7, _clock(18, 0)),
        (6, 1, _serial(date(2026, 2, 17))),
        (6, 5, _clock(9, 0)),
        (6, 7, _clock(12, 0)),
    ]
    return _xlsx({'2026.2': february, '2026.3': march})


def _two_day_march(unset=False) -> bytes:
    march = _day_headers() + [
        (5, 1, _serial(date(2026, 2, 16))),
        (5, 5, _clock(9, 0)),
        (5, 7, _clock(18, 0)),
        (6, 1, _serial(date(2026, 2, 17))),
        (6, 5, _clock(9, 0)),
        (6, 7, _clock(12, 0)),
    ]
    if unset:
        march += [
            (7, 1, _serial(date(2026, 2, 18))),
            (7, 5, _clock(9, 0)),
        ]
    return _xlsx({'2026.2': _day_headers() + [
        (5, 1, _serial(date(2026, 1, 16))),
        (5, 5, _clock(9, 0)),
        (5, 7, _clock(18, 0)),
    ], '2026.3': march})


def _one_day_book(year, day):
    sheet = 'm'
    cells = [
        (3, 5, '始業時間（00：00）'),
        (3, 7, '終業時間（00：00）'),
        (5, 1, _serial(day)),
        (5, 5, _clock(9, 0)),
        (5, 7, _clock(18, 0)),
    ]
    layout = {**kintai.MONTHDOT_2026_CONTRACT_LAYOUT, 'month_sheets': {(year, 3): sheet}}
    return _xlsx({sheet: cells}), layout


if __name__ == '__main__':
    unittest.main()
