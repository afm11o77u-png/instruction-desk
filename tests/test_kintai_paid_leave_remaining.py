"""現保有数から、取得日より後の締め期間の有給取得だけを引く。一時DBだけ。"""
from __future__ import annotations

import base64
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import kintai
import kintai_leave_balance as leave

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_kintai_daily as daily_fix  # noqa: E402


class HoldingMathTest(unittest.TestCase):
    def test_later_months_subtract_and_past_months_do_not(self):
        paid = {
            (2026, 10): 9,
            (2026, 11): 0,
            (2026, 12): 1,
            (2027, 1): 0.5,
        }
        value, note = kintai.paid_leave_holding_remaining(
            10, '2026-11-07T01:00:00+00:00', paid, today=date(2026, 11, 7),
        )
        self.assertEqual(note, '')
        self.assertEqual(value, 8.5)
        self.assertEqual(kintai.paid_leave_acquisition_date('2026-11-15T15:30:00+00:00'), date(2026, 11, 16))

    def test_mid_period_usage_and_missing_import_stay_blank(self):
        acquired = '2026-11-07T01:00:00+00:00'
        value, note = kintai.paid_leave_holding_remaining(
            10, acquired, {(2026, 11): 1.5, (2026, 12): 1}, today=date(2026, 11, 7),
        )
        self.assertIsNone(value)
        self.assertIn('途中', note)
        value, note = kintai.paid_leave_holding_remaining(
            10, acquired, {}, today=date(2026, 11, 7),
        )
        self.assertIsNone(value)
        self.assertIn('未取込', note)
        value, note = kintai.paid_leave_holding_remaining(
            10, acquired, {(2026, 11): 0}, today=date(2026, 12, 20),
        )
        self.assertIsNone(value)
        self.assertIn('終わった', note)

    def test_future_blank_is_not_usage_and_negative_stays(self):
        paid = {(2026, 11): 0, (2026, 12): 12}
        value, note = kintai.paid_leave_holding_remaining(
            10, '2026-11-07T01:00:00+00:00', paid, today=date(2026, 11, 7),
        )
        self.assertEqual(note, '')
        self.assertEqual(value, -2)
        self.assertIsNone(kintai.paid_leave_holding_remaining(None, '2026-11-07T01:00:00+00:00', paid)[0])
        zero, note = kintai.paid_leave_holding_remaining(
            0, '2026-11-07T01:00:00+00:00', {(2026, 11): 0}, today=date(2026, 11, 7),
        )
        self.assertEqual(zero, 0)
        self.assertEqual(note, '')

    def test_year_crossing_uses_months_after_the_read_date(self):
        paid = {
            (2026, 11): 0,
            (2026, 12): 1,
            (2027, 1): 0.5,
            (2027, 2): 1,
        }
        value, note = kintai.paid_leave_holding_remaining(
            10, '2026-11-07T01:00:00+00:00', paid, today=date(2027, 3, 1),
        )
        self.assertEqual(note, '')
        self.assertEqual(value, 7.5)
        missing = dict(paid)
        del missing[(2027, 2)]
        self.assertIsNone(kintai.paid_leave_holding_remaining(
            10, '2026-11-07T01:00:00+00:00', missing, today=date(2027, 3, 1),
        )[0])

    def test_reread_replaces_the_baseline(self):
        first, _ = kintai.paid_leave_holding_remaining(
            10, '2026-11-07T01:00:00+00:00', {(2026, 11): 0, (2026, 12): 2}, today=date(2026, 11, 7),
        )
        second, note = kintai.paid_leave_holding_remaining(
            6, '2026-12-01T01:00:00+00:00', {(2026, 12): 0, (2027, 1): 1}, today=date(2026, 12, 20),
        )
        self.assertEqual(first, 8)
        self.assertEqual(second, 5)
        self.assertEqual(note, '')
        blocked, blocked_note = kintai.paid_leave_holding_remaining(
            6, '2026-12-01T01:00:00+00:00', {(2026, 12): 2, (2027, 1): 1}, today=date(2026, 12, 20),
        )
        self.assertIsNone(blocked)
        self.assertIn('途中', blocked_note)


class HoldingScreenTest(unittest.TestCase):
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
        self.person = self.c.execute('SELECT * FROM kintai_people WHERE active=1 ORDER BY id LIMIT 1').fetchone()
        (self.data / 'kintai-local.json').write_text('{"shareRoot":"","layoutId":"","peopleFiles":{},"peopleLayouts":{}}', encoding='utf-8')
        leave.ensure_schema(self.c)
        self.today = datetime.now(kintai.JST).date()
        year, month = kintai._closing_month_containing(self.today)
        self.straddle = (year, month)
        nxt = kintai._next_year_month(year, month)
        prev = (year - 1, 12) if month == 1 else (year, month - 1)
        self._paid(*self.straddle, 0)
        self._paid(*prev, 4)
        self._paid(*nxt, 1.5)
        self.c.execute(
            '''INSERT INTO kintai_leave_cells(person_id, cell, balance, note, filename, updated_at)
               VALUES(?,?,?,?,?,?)''',
            (self.person['id'], 'K11', 10, '', 'leave.xlsx', datetime.now(timezone.utc).isoformat(timespec='seconds')),
        )
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _paid(self, year, month, paid):
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'),'ok','')''',
            (self.person['id'], year, month, 0, 0, paid, 0, 0, ''),
        )

    def test_personal_months_and_roster_match_and_ignore_failed_reread(self):
        overview = kintai.list_overview(self.c, 2026, person_id=self.person['id'])
        months = overview['rows'][0]['months']
        self.assertTrue(months)
        self.assertTrue(all(m['paidLeaveRemaining'] == 8.5 for m in months))
        roster = kintai.list_roster(self.c, 2026)
        row = next(r for r in roster['rows'] if r['personId'] == self.person['id'])
        later = kintai.list_roster(self.c, 2027)
        later_row = next(r for r in later['rows'] if r['personId'] == self.person['id'])
        self.assertEqual(row['paidLeaveRemaining'], 8.5)
        self.assertEqual(later_row['paidLeaveRemaining'], row['paidLeaveRemaining'])
        blob = base64.b64encode(daily_fix._xlsx({'別シート': [(1, 1, 1)]})).decode()
        with self.assertRaises(ValueError):
            leave.handle(self.c, {
                'action': 'update', 'filename': 'leave.xlsx', 'password': '', 'data': blob,
                'people': [{'personId': self.person['id'], 'cell': 'K11'}],
            }, kintai.read_xlsx_grid)
        kept = next(r for r in kintai.list_roster(self.c, 2026)['rows'] if r['personId'] == self.person['id'])
        self.assertEqual(kept['paidLeaveRemaining'], 8.5)
        self.assertEqual(self.c.execute(
            'SELECT balance FROM kintai_leave_cells WHERE person_id=?', (self.person['id'],),
        ).fetchone()['balance'], 10)
        floor = SimpleNamespace(user={
            'id': 3, 'name': '現', 'login': 'f', 'role': 'floor3', 'mark_kintai': 0, 'manage': 0, 'overview': 0,
        }, path='/api/kintai/roster', query='')
        self.assertEqual(kintai.handle_get(floor, self.c, '/api/kintai/roster')[0], 403)


if __name__ == '__main__':
    unittest.main()
