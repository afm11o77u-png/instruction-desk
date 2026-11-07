"""年間休日残数。枠は2/1〜1/31、引く累計は既存の16日〜15日の月次合計。"""
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


class AnnualAllowanceTest(unittest.TestCase):
    def test_ceil_uses_365_even_when_enrollment_includes_feb_29(self):
        self.assertEqual(kintai.fiscal_enrollment_window(2026), (date(2026, 2, 1), date(2027, 1, 31)))
        full, note = kintai.enrollment_days(2026, date(2020, 1, 1), None, False)
        self.assertEqual(note, '')
        self.assertEqual(full, 365)
        self.assertEqual(kintai.annual_holiday_allowance(2026, 'regular', date(2020, 1, 1), None, False), (110, ''))
        leap, _ = kintai.enrollment_days(2024, date(2020, 1, 1), None, False)
        self.assertEqual(leap, 366)
        self.assertEqual(kintai.annual_holiday_allowance(2024, 'contract', date(2020, 1, 1), None, False)[0], 111)
        one = kintai.annual_holiday_allowance(2026, 'regular', date(2026, 2, 1), date(2026, 2, 1), False)[0]
        self.assertEqual(one, 1)
        almost, _ = kintai.enrollment_days(2026, date(2026, 2, 2), None, False)
        self.assertEqual(almost, 364)
        self.assertEqual(kintai.annual_holiday_allowance(2026, 'regular', date(2026, 2, 2), None, False)[0], 110)

    def test_missing_dates_and_other_employment_stay_blank(self):
        self.assertEqual(kintai.annual_holiday_allowance(2026, 'regular', None, None, False)[1], '入社日が未入力です')
        self.assertIsNone(kintai.annual_holiday_allowance(2026, 'contract', None, None, True)[0])
        self.assertEqual(
            kintai.annual_holiday_allowance(2026, 'contract', date(2020, 4, 1), None, True)[1],
            '退職日が未入力です',
        )
        self.assertEqual(
            kintai.annual_holiday_allowance(2026, 'part', date(2020, 4, 1), None, False)[1],
            '正社員と契約社員以外は年間休日枠の対象外です',
        )
        self.assertEqual(kintai.enrollment_days(2026, date(2027, 2, 1), None, False)[0], 0)
        hired = date(2026, 6, 1)
        days, _ = kintai.enrollment_days(2026, hired, date(2026, 8, 31), False)
        self.assertEqual(days, (date(2026, 8, 31) - hired).days + 1)


class AnnualRemainingStoreTest(unittest.TestCase):
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
        self.c.execute('UPDATE kintai_people SET employment=? WHERE id=?', ('regular', self.person['id']))
        self.c.execute('UPDATE kintai_people SET employment=? WHERE id=?', ('part', self.other['id']))
        fixtures = self.data / 'kintai-fixtures'
        fixtures.mkdir()
        self.book = fixtures / 'annual.xlsx'
        self.book.write_bytes(_february_only())
        self.before = self.book.read_bytes()
        for person in (self.person, self.other):
            self.c.execute('UPDATE kintai_people SET source_file=? WHERE id=?', ('annual.xlsx', person['id']))
        (self.data / 'kintai-local.json').write_text(json.dumps({
            'shareRoot': '',
            'layoutId': 'shukkinbo-2026-monthdot-v1',
            'peopleFiles': {},
            'peopleLayouts': {
                str(self.person['id']): 'shukkinbo-2026-monthdot-v1',
                str(self.other['id']): 'shukkinbo-2026-monthdot-v1',
            },
        }, ensure_ascii=False), encoding='utf-8')
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'),'ok','')''',
            (self.person['id'], 2026, 2, 99, 4, 0, 60, 0, ''),
        )
        self.c.commit()
        self.user = {'id': 1, 'name': '主', 'login': 'o', 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def months(self, person_id=None):
        view = kintai.list_overview(self.c, 2026, person_id=person_id or self.person['id'])
        return {(m['year'], m['month']): m for m in view['rows'][0]['months']}

    def test_dates_save_and_remaining_matches_roster_without_filling_gaps(self):
        blank = self.months()[(2026, 2)]
        self.assertIsNone(blank['holidayAllowance'])
        self.assertEqual(blank['holidayAllowanceNote'], '入社日が未入力です')
        self.assertIsNone(blank['holidayRemaining'])
        saved = kintai.save_employment_dates(self.c, self.user, {
            'year': 2026,
            'people': [
                {'personId': self.person['id'], 'hiredOn': '2020-04-01', 'retiredOn': None},
                {'personId': self.other['id'], 'hiredOn': '2020-04-01', 'retiredOn': None},
            ],
        })
        self.assertEqual(self.book.read_bytes(), self.before)
        again = kintai.list_roster(self.c, 2026)
        row = next(r for r in again['rows'] if r['personId'] == self.person['id'])
        part = next(r for r in again['rows'] if r['personId'] == self.other['id'])
        self.assertEqual(row['hiredOn'], '2020-04-01')
        self.assertIsNone(row['retiredOn'])
        self.assertEqual(row['holidayAllowance'], 110)
        feb = self.months()[(2026, 2)]
        march = self.months()[(2026, 3)]
        self.assertEqual(feb['holidayDays'], 30)
        self.assertEqual(feb['holidayCumulative'], 30)
        self.assertEqual(feb['holidayRemaining'], 80)
        self.assertIsNone(march['holidayDays'])
        self.assertIsNone(march['holidayCumulative'])
        self.assertIsNone(march['holidayRemaining'])
        self.assertEqual(row['holidayCumulative'], feb['holidayCumulative'])
        self.assertEqual(row['holidayRemaining'], feb['holidayRemaining'])
        self.assertIsNone(part['holidayAllowance'])
        self.assertIsNone(part['holidayRemaining'])
        self.assertEqual(saved['rows'][0]['personId'] > 0, True)
        stored = self.c.execute('SELECT hired_on, retired FROM kintai_people WHERE id=?', (self.person['id'],)).fetchone()
        self.assertEqual(stored['hired_on'], '2020-04-01')
        self.assertEqual(stored['retired'], 0)

    def test_reader_cannot_save_dates(self):
        class Denied:
            user = {'id': 2, 'name': '営', 'login': 's', 'role': 'sales', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/roster/employment'

        class Floor:
            user = {'id': 3, 'name': '現', 'login': 'f', 'role': 'floor3', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/roster/employment'

        payload = {'year': 2026, 'people': [{'personId': self.person['id'], 'hiredOn': '2020-04-01', 'retiredOn': None}]}
        self.assertEqual(kintai.handle_post(Denied(), self.c, '/api/kintai/roster/employment', payload)[0], 403)
        self.assertEqual(kintai.handle_post(Floor(), self.c, '/api/kintai/roster/employment', payload)[0], 403)
        self.assertIsNone(self.c.execute('SELECT hired_on FROM kintai_people WHERE id=?', (self.person['id'],)).fetchone()['hired_on'])

    def test_base_date_is_per_person_and_can_be_cleared(self):
        kintai.save_employment_dates(self.c, self.user, {
            'year': 2026,
            'people': [{
                'personId': self.person['id'],
                'hiredOn': '2019-04-01',
                'retiredOn': '2026-06-30',
                'paidLeaveBaseOn': '2019-10-01',
            }],
        })
        roster_2026 = kintai.list_roster(self.c, 2026)
        roster_2027 = kintai.list_roster(self.c, 2027)
        row = next(r for r in roster_2026['rows'] if r['personId'] == self.person['id'])
        later = next(r for r in roster_2027['rows'] if r['personId'] == self.person['id'])
        other = next(r for r in roster_2026['rows'] if r['personId'] == self.other['id'])
        self.assertEqual(row['paidLeaveBaseDate'], '2019-10-01')
        self.assertEqual(row['retiredOn'], '2026-06-30')
        self.assertEqual(later['paidLeaveBaseDate'], row['paidLeaveBaseDate'])
        self.assertEqual(later['hiredOn'], row['hiredOn'])
        self.assertIsNone(other['paidLeaveBaseDate'])
        feb = self.months()[(2026, 2)]
        self.assertEqual(feb['paidLeaveBaseDate'], '2019-10-01')
        self.assertEqual(feb['holidayAllowance'], kintai.annual_holiday_allowance(
            2026, 'regular', date(2019, 4, 1), date(2026, 6, 30), False,
        )[0])
        self.assertIsNone(feb.get('paidLeaveRemaining'))
        cleared = kintai.save_employment_dates(self.c, self.user, {
            'year': 2026,
            'people': [{
                'personId': self.person['id'],
                'hiredOn': None,
                'retiredOn': None,
                'paidLeaveBaseOn': None,
            }],
        })
        cleared_row = next(r for r in cleared['rows'] if r['personId'] == self.person['id'])
        self.assertIsNone(cleared_row['hiredOn'])
        self.assertIsNone(cleared_row['retiredOn'])
        self.assertIsNone(cleared_row['paidLeaveBaseDate'])
        self.assertIsNone(self.months()[(2026, 2)]['paidLeaveBaseDate'])
        self.assertEqual(self.months()[(2026, 2)]['holidayAllowanceNote'], '入社日が未入力です')
        with self.assertRaises(ValueError):
            kintai.save_employment_dates(self.c, self.user, {
                'year': 2026,
                'people': [{
                    'personId': self.person['id'],
                    'hiredOn': '2020-04-01',
                    'retiredOn': None,
                    'paidLeaveBaseOn': '2020-13-01',
                }],
            })
        self.assertIsNone(self.c.execute(
            'SELECT paid_leave_base_on FROM kintai_people WHERE id=?',
            (self.person['id'],),
        ).fetchone()['paid_leave_base_on'])


def _february_only() -> bytes:
    return _xlsx({'2026.2': [
        (3, 5, '始業時間（00：00）'),
        (3, 7, '終業時間（00：00）'),
        (5, 1, _serial(date(2026, 1, 16))),
        (5, 5, _clock(9, 0)),
        (5, 7, _clock(18, 0)),
    ]})


if __name__ == '__main__':
    unittest.main()
