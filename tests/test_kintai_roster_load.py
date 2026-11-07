"""全員の累計は、同じ出勤簿を月の数だけ読み直さない。"""
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


class RosterReadOnceTest(unittest.TestCase):
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
        fixtures = self.data / 'kintai-fixtures'
        fixtures.mkdir()
        book = fixtures / 'once.xlsx'
        book.write_bytes(_book())
        self.c.execute('UPDATE kintai_people SET source_file=?, employment=? WHERE id=?', ('once.xlsx', 'regular', self.person['id']))
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'),'ok','')''',
            (self.person['id'], 2026, 2, 1, 0, 0, 60, 0, ''),
        )
        (self.data / 'kintai-local.json').write_text(json.dumps({
            'shareRoot': '',
            'layoutId': 'shukkinbo-2026-monthdot-v1',
            'peopleFiles': {},
            'peopleLayouts': {str(self.person['id']): 'shukkinbo-2026-monthdot-v1'},
        }, ensure_ascii=False), encoding='utf-8')
        self.c.commit()
        self.user = {'id': 1, 'name': '主', 'login': 'o', 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_one_workbook_read_for_twelve_months_and_edits_are_fresh(self):
        reads = {'grid': 0, 'areas': 0}
        orig_grid = kintai.read_xlsx_grid
        orig_areas = kintai.read_print_areas

        def grid(blob):
            reads['grid'] += 1
            return orig_grid(blob)

        def areas(blob):
            reads['areas'] += 1
            return orig_areas(blob)

        kintai.read_xlsx_grid = grid
        kintai.read_print_areas = areas
        try:
            roster = kintai.list_roster(self.c, 2026)
        finally:
            kintai.read_xlsx_grid = orig_grid
            kintai.read_print_areas = orig_areas
        with_file = 1
        self.assertEqual(reads['grid'], with_file)
        self.assertEqual(reads['areas'], with_file)
        row = next(r for r in roster['rows'] if r['personId'] == self.person['id'])
        fresh = dict(self.c.execute('SELECT * FROM kintai_people WHERE id=?', (self.person['id'],)).fetchone())
        direct = kintai.month_attendance_total(self.c, fresh, 2026, 2)
        # 保存前。2月の出勤は日別の初期値。
        self.assertEqual(row['workCumulative'], direct)
        kintai.save_daily_work_days(self.c, self.user, {
            'personId': self.person['id'],
            'year': 2026,
            'month': 2,
            'days': [{'date': '2026-01-16', 'days': 0}],
        })
        again = kintai.list_roster(self.c, 2026)
        updated = next(r for r in again['rows'] if r['personId'] == self.person['id'])
        self.assertNotEqual(updated['workCumulative'], row['workCumulative'])
        self.assertEqual(updated['workCumulative'], 0)


def _book() -> bytes:
    return daily_fix._xlsx({'2026.2': [
        (3, 5, '始業時間（00：00）'),
        (3, 7, '終業時間（00：00）'),
        (5, 1, daily_fix._serial(date(2026, 1, 16))),
        (5, 5, 9 / 24),
        (5, 7, 18 / 24),
    ]})


if __name__ == '__main__':
    unittest.main()
