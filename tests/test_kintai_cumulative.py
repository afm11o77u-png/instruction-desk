"""累計は取込済みの月次を足すだけ。枠・残数・起算日は作らない。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import kintai


class CumulativeColumnTest(unittest.TestCase):
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
        self.person = self.c.execute(
            'SELECT * FROM kintai_people WHERE active=1 ORDER BY id LIMIT 1'
        ).fetchone()
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def put(self, year, month, holiday, paid, work=9):
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'),'ok','')''',
            (self.person['id'], year, month, work, holiday, paid, 60, 0, ''),
        )

    def test_cumulative_uses_imported_months_and_stops_at_a_gap(self):
        self.put(2026, 2, 2, 1)
        self.put(2026, 4, 3, 0.5)
        self.c.commit()
        view = kintai.list_overview(self.c, 2026, person_id=self.person['id'])
        months = { (m['year'], m['month']): m for m in view['rows'][0]['months'] }
        feb, mar, apr = months[(2026, 2)], months[(2026, 3)], months[(2026, 4)]
        self.assertEqual(feb['holidayDays'], 2)
        self.assertEqual(feb['holidayCumulative'], 2)
        self.assertEqual(feb['paidLeaveDays'], 1)
        self.assertEqual(feb['paidLeaveCumulative'], 1)
        self.assertIsNone(mar['holidayCumulative'])
        self.assertIsNone(mar['paidLeaveCumulative'])
        self.assertIsNone(apr['holidayCumulative'])
        self.assertIsNone(apr['paidLeaveCumulative'])
        self.assertIsNone(feb.get('holidayRemaining'))
        self.assertIsNone(feb.get('paidLeaveRemaining'))
        self.assertIsNone(feb.get('paidLeaveBaseDate'))
        # 出勤累計は日別入力の月合計。ファイルが無い月は Excel の出勤日数へ戻さない
        self.assertIsNone(feb['workDays'])
        self.assertIsNone(feb['workCumulative'])

    def test_roster_sums_a_complete_prefix_and_leaves_unknowns_blank(self):
        for year, month in kintai.fiscal_months(2026, 2):
            self.put(year, month, 1, 0.5, work=110)
        self.c.commit()
        roster = kintai.list_roster(self.c, 2026)
        row = next(r for r in roster['rows'] if r['personId'] == self.person['id'])
        self.assertEqual(row['holidayCumulative'], 12)
        self.assertEqual(row['paidLeaveCumulative'], 6)
        self.assertIsNone(row['workCumulative'])
        self.assertIsNone(row['holidayAllowance'])
        self.assertIsNone(row['holidayRemaining'])
        self.assertIsNone(row['paidLeaveOpening'])
        self.assertIsNone(row['paidLeaveRemaining'])
        self.assertIsNone(row['hiredOn'])
        self.assertIsNone(row['retiredOn'])
        self.assertIsNone(row['paidLeaveBaseDate'])
        self.assertNotIn(110, (
            row['holidayAllowance'], row['holidayRemaining'], row['holidayCumulative'],
        ))

    def test_roster_permission_matches_attendance(self):
        class Denied:
            user = {'id': 2, 'role': 'sales', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/roster?year=2026'

        class Allowed:
            user = {'id': 1, 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/roster?year=2026'

        self.assertEqual(kintai.handle_get(Denied(), self.c, '/api/kintai/roster')[0], 403)
        code, body = kintai.handle_get(Allowed(), self.c, '/api/kintai/roster')
        self.assertEqual(code, 200)
        self.assertTrue(any(r['personId'] == self.person['id'] for r in body['rows']))


if __name__ == '__main__':
    unittest.main()
