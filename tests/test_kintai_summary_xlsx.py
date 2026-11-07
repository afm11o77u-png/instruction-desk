"""月別集計の Excel は、画面と同じ合計だけを出す。DB は書き換えない。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import kintai
import kintai_summary_xlsx


class SummaryXlsxTest(unittest.TestCase):
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
        people = list(self.c.execute('SELECT id FROM kintai_people WHERE active=1 ORDER BY id LIMIT 2'))
        self.a = people[0]['id']
        self.b = people[1]['id']
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def put(self, pid, year, month, ordinary, overtime):
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'),'ok','')''',
            (pid, year, month, 1, 0, 0, ordinary, overtime, ''),
        )

    def test_excel_text_does_not_become_a_formula(self):
        self.assertEqual(kintai_summary_xlsx.excel_text('=1+1'), "'=1+1")
        self.assertEqual(kintai_summary_xlsx.excel_text('+1'), "'+1")
        self.assertEqual(kintai_summary_xlsx.excel_text('-1'), "'-1")
        self.assertEqual(kintai_summary_xlsx.excel_text('@cmd'), "'@cmd")
        self.assertEqual(kintai_summary_xlsx.excel_text('773:31'), '773:31')
        self.assertEqual(kintai_summary_xlsx.excel_text('0:00'), '0:00')

    def test_workbook_matches_the_screen_and_does_not_write(self):
        self.put(self.a, 2026, 2, 25 * 60 + 5, 0)
        self.put(self.a, 2026, 3, None, 90)
        self.put(self.b, 2026, 3, None, 48 * 60)
        self.c.execute(
            "INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) "
            "VALUES('zzz','regular','除外','',0,999,0)"
        )
        inactive = self.c.execute("SELECT id FROM kintai_people WHERE employee_code='zzz'").fetchone()['id']
        self.put(inactive, 2026, 2, 1000 * 60, 1000 * 60)
        self.c.commit()
        before = self.c.execute('SELECT COUNT(*), SUM(ordinary_minutes) FROM kintai_monthly').fetchone()
        summary = kintai.month_hour_summary(self.c, 2026)
        status, body, ctype, headers = kintai_summary_xlsx.export(self.c, 2026)
        after = self.c.execute('SELECT COUNT(*), SUM(ordinary_minutes) FROM kintai_monthly').fetchone()
        self.assertEqual(status, 200)
        self.assertEqual(ctype, kintai_summary_xlsx.XLSX)
        self.assertEqual(kintai_summary_xlsx.filename(summary), '月別集計_2026-02_2027-01.xlsx')
        self.assertIn('%E6%9C%88%E5%88%A5%E9%9B%86%E8%A8%88_2026-02_2027-01.xlsx', headers['Content-Disposition'])
        self.assertEqual(before, after)
        sheets = kintai.read_xlsx_grid(body)
        grid = next(iter(sheets.values()))
        self.assertEqual([grid[(3, col)] for col in range(1, 8)], list(kintai_summary_xlsx.HEADERS))
        self.assertEqual(grid[(4, 1)], '2026/02')
        self.assertEqual(grid[(4, 2)], summary['months'][0]['ordinaryHm'])
        self.assertEqual(grid[(4, 2)], '25:05')
        self.assertEqual(grid[(4, 3)], '0:00')
        self.assertEqual(grid[(4, 4)], '25:05')
        self.assertNotIn((4, 5), grid)
        self.assertEqual(grid[(5, 2)], '0:00')
        self.assertEqual(grid[(5, 3)], summary['months'][1]['overtimeHm'])
        self.assertEqual(grid[(5, 4)], summary['months'][1]['totalHm'])
        self.assertNotIn((6, 2), grid)
        self.assertEqual(grid[(16, 1)], '期間合計')
        self.assertEqual(grid[(16, 4)], summary['period']['totalHm'])
        self.assertNotIn('1000:00', [v for v in grid.values() if isinstance(v, str)])
        keys = [grid[(row, 1)] for row in range(4, 16)]
        self.assertEqual(keys, [m['key'] for m in summary['months']])

    def test_permission_matches_the_summary_screen(self):
        class Denied:
            user = {'id': 2, 'role': 'sales', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/month-summary/xlsx?year=2026'

        class Floor:
            user = {'id': 3, 'role': 'floor3', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/month-summary/xlsx?year=2026'

        class Allowed:
            user = {'id': 1, 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/month-summary/xlsx?year=2026'

        self.assertEqual(kintai.handle_get(Denied(), self.c, '/api/kintai/month-summary/xlsx')[0], 403)
        self.assertEqual(kintai.handle_get(Floor(), self.c, '/api/kintai/month-summary/xlsx')[0], 403)
        code, body, ctype, headers = kintai.handle_get(Allowed(), self.c, '/api/kintai/month-summary/xlsx')
        self.assertEqual(code, 200)
        self.assertTrue(body[:2] == b'PK')
        self.assertIn('attachment', headers['Content-Disposition'])
        opened = kintai.read_xlsx_grid(body)
        self.assertIn('月別集計', opened)


if __name__ == '__main__':
    unittest.main()
