"""日別の出勤日数。一時DBと合成xlsxだけ。Excelの月計は置き換え、ファイルは書かない。"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import kintai
import split_backup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_kintai_daily as daily_fix  # noqa: E402

_xlsx = daily_fix._xlsx
_serial = daily_fix._serial


def _clock(hour, minute=0):
    return (hour * 60 + minute) / (24 * 60)


class AttendanceDaysRuleTest(unittest.TestCase):
    def test_defaults_follow_both_clocks_and_leave_next_day_intact(self):
        nine = _clock(9, 0)
        self.assertEqual(kintai.default_attendance_days(None, None), 0.0)
        self.assertEqual(kintai.default_attendance_days('', ''), 0.0)
        self.assertEqual(kintai.default_attendance_days(nine, _clock(10, 59)), 0.0)
        self.assertEqual(kintai.default_attendance_days(nine, _clock(11, 0)), 0.5)
        self.assertEqual(kintai.default_attendance_days(nine, _clock(13, 59)), 0.5)
        self.assertEqual(kintai.default_attendance_days(nine, _clock(14, 0)), 1.0)
        self.assertEqual(kintai.default_attendance_days(nine, _clock(14, 1)), 1.0)
        self.assertEqual(kintai.default_attendance_days(nine, _clock(18, 0)), 1.0)
        self.assertEqual(kintai._clock_state(1.20625), ('clock', 28 * 60 + 57))
        self.assertEqual(kintai.default_attendance_days(nine, 1.20625), 1.0)
        self.assertEqual(kintai.default_attendance_days(nine, '28:57'), 1.0)
        self.assertEqual(kintai.default_attendance_days('9:00', '11：00'), 0.5)
        self.assertEqual(kintai.default_attendance_days('9:00', '10:59'), 0.0)
        self.assertEqual(kintai.default_attendance_days('9:00', '14：00'), 1.0)
        self.assertEqual(kintai.default_attendance_days('9:00', '13:59'), 0.5)
        # 片方だけ空欄、0、数式キャッシュ無し、日付シリアルは初期値にしない
        self.assertIsNone(kintai.default_attendance_days('', _clock(18, 0)))
        self.assertIsNone(kintai.default_attendance_days(nine, ''))
        self.assertIsNone(kintai.default_attendance_days(nine, 0))
        self.assertIsNone(kintai.default_attendance_days(nine, {'__missing_cache__': True}))
        self.assertIsNone(kintai.default_attendance_days(nine, 46000))
        # datetime は既存表示と同じく時・分だけ。日付を翌日分としては足さない
        self.assertEqual(
            kintai.default_attendance_days(nine, datetime(2026, 3, 2, 4, 57)),
            0.0,
        )

    def test_sheet_defaults_stay_inside_the_closing_period(self):
        view = kintai.daily_rows_from_workbook(_march_book(), 2026, 3, kintai.SEISHAIN_2026_LAYOUT)
        by_date = {row['date']: row['workDays'] for row in view['rows']}
        self.assertEqual(list(by_date), [
            '2026/02/16', '2026/02/17', '2026/02/18', '2026/02/19', '2026/02/20',
            '2026/02/21', '2026/03/13', '2026/03/14', '2026/03/15',
        ])
        self.assertEqual(by_date['2026/02/16'], 1.0)
        self.assertEqual(by_date['2026/02/17'], 0.5)
        self.assertEqual(by_date['2026/02/18'], 0.0)
        self.assertIsNone(by_date['2026/02/19'])
        self.assertIsNone(by_date['2026/02/20'])
        self.assertIsNone(by_date['2026/02/21'])
        self.assertEqual(by_date['2026/03/13'], 0.5)
        self.assertEqual(by_date['2026/03/14'], 1.0)
        self.assertEqual(by_date['2026/03/15'], 1.0)
        self.assertNotIn('2026/02/15', by_date)
        self.assertNotIn('2026/03/16', by_date)
        self.assertEqual(view['rows'][0]['actual'], '8.00')
        self.assertEqual(view['totals'][0]['label'], '実労働時間計')
        self.assertEqual(view['totals'][0]['value'], '17.03')


class AttendanceDaysStoreTest(unittest.TestCase):
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
        self.book = fixtures / 'days.xlsx'
        self.blob = _march_book()
        self.book.write_bytes(self.blob)
        self.before = self.book.read_bytes()
        for person in (self.person, self.other):
            self.c.execute(
                'UPDATE kintai_people SET source_file=? WHERE id=?',
                ('days.xlsx', person['id']),
            )
        (self.data / 'kintai-local.json').write_text(json.dumps({
            'shareRoot': '',
            'layoutId': 'shukkinbo-2026-seishain-v1',
            'peopleFiles': {},
            'peopleLayouts': {
                str(self.person['id']): 'shukkinbo-2026-seishain-v1',
                str(self.other['id']): 'shukkinbo-2026-seishain-v1',
            },
        }, ensure_ascii=False), encoding='utf-8')
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime'),'ok','')''',
            (self.person['id'], 2026, 3, 99, 1, 0, 60, 0, ''),
        )
        self.c.commit()
        self.user = {'id': 4, 'name': 'マーク', 'login': 'm', 'role': 'floor', 'mark_kintai': 1, 'manage': 0, 'overview': 0}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_save_reload_zero_replaces_monthly_column_without_touching_excel(self):
        self.assertEqual(split_backup.table_category('kintai_daily_work_days'), split_backup.MARK)
        view = kintai.person_daily_sheet(self.c, self.person['id'], 2026, 3)
        self.assertTrue(view['readOnly'])
        self.assertTrue(view['canEditWorkDays'])
        self.assertEqual(view['workDaysTotal'], 4.0)
        saved = kintai.save_daily_work_days(self.c, self.user, {
            'personId': self.person['id'],
            'year': 2026,
            'month': 3,
            'days': [{'date': '2026/02/16', 'days': 0}],
        })
        self.assertEqual(self.book.read_bytes(), self.before)
        hit = next(row for row in saved['rows'] if row['date'] == '2026/02/16')
        self.assertEqual(hit['workDays'], 0.0)
        self.assertTrue(hit['workDaysSaved'])
        self.assertEqual(hit['actual'], '8.00')
        self.assertEqual(saved['totals'][0]['value'], '17.03')
        self.assertEqual(saved['workDaysTotal'], 3.0)
        again = kintai.person_daily_sheet(self.c, self.person['id'], 2026, 3)
        again_hit = next(row for row in again['rows'] if row['date'] == '2026/02/16')
        self.assertEqual(again_hit['workDays'], 0.0)
        self.assertTrue(again_hit['workDaysSaved'])
        stored = self.c.execute(
            'SELECT work_days FROM kintai_monthly WHERE person_id=? AND year=2026 AND month=3',
            (self.person['id'],),
        ).fetchone()
        self.assertEqual(stored['work_days'], 99)
        overview = kintai.list_overview(self.c, 2026, person_id=self.person['id'])
        march = next(m for m in overview['rows'][0]['months'] if m['month'] == 3 and m['year'] == 2026)
        feb = next(m for m in overview['rows'][0]['months'] if m['month'] == 2 and m['year'] == 2026)
        self.assertEqual(march['workDays'], 3.0)
        self.assertEqual(march['workDaysFrom'], 'daily')
        self.assertEqual(march['holidayDays'], 1)
        self.assertIsNone(feb['workDays'])
        other = kintai.person_daily_sheet(self.c, self.other['id'], 2026, 3)
        other_hit = next(row for row in other['rows'] if row['date'] == '2026/02/16')
        self.assertEqual(other_hit['workDays'], 1.0)
        self.assertFalse(other_hit['workDaysSaved'])
        self.c.execute(
            'UPDATE kintai_monthly SET work_days=3 WHERE person_id=? AND year=2026 AND month=3',
            (self.person['id'],),
        )
        overview = kintai.list_overview(self.c, 2026, person_id=self.person['id'])
        march = next(m for m in overview['rows'][0]['months'] if m['month'] == 3)
        self.assertEqual(march['workDays'], 3.0)
        cleared = kintai.save_daily_work_days(self.c, self.user, {
            'personId': self.person['id'],
            'year': 2026,
            'month': 3,
            'days': [{'date': '2026-02-16', 'days': None}],
        })
        cleared_hit = next(row for row in cleared['rows'] if row['date'] == '2026/02/16')
        self.assertEqual(cleared_hit['workDays'], 1.0)
        self.assertFalse(cleared_hit['workDaysSaved'])
        self.assertEqual(cleared['workDaysTotal'], 4.0)

    def test_year_boundary_and_rejected_dates(self):
        january = kintai.daily_rows_from_workbook(_january_book(), 2027, 1, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual([row['date'] for row in january['rows']], ['2026/12/16', '2027/01/15'])
        self.assertEqual(january['rows'][0]['workDays'], 1.0)
        self.assertEqual(january['rows'][1]['workDays'], 0.0)
        december = kintai.daily_rows_from_workbook(_january_book(), 2026, 12, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual([row['date'] for row in december['rows']], ['2026/12/15'])
        with self.assertRaises(ValueError):
            kintai.save_daily_work_days(self.c, self.user, {
                'personId': self.person['id'],
                'year': 2026,
                'month': 3,
                'days': [{'date': '2026-02-15', 'days': 1}],
            })
        with self.assertRaises(ValueError):
            kintai.save_daily_work_days(self.c, self.user, {
                'personId': self.person['id'],
                'year': 2026,
                'month': 3,
                'days': [{'date': '2026-03-16', 'days': 1}],
            })
        for bad in (-1, 2.001, 3, True, '1', float('nan')):
            with self.assertRaises(ValueError):
                kintai.save_daily_work_days(self.c, self.user, {
                    'personId': self.person['id'],
                    'year': 2026,
                    'month': 3,
                    'days': [{'date': '2026-02-16', 'days': bad}],
                })
        kept = kintai.person_daily_sheet(self.c, self.person['id'], 2026, 3)
        self.assertEqual(next(row['workDays'] for row in kept['rows'] if row['date'] == '2026/02/16'), 1.0)

    def test_leap_day_stays_in_march_period(self):
        blob, layout = _leap_march()
        view = kintai.daily_rows_from_workbook(blob, 2024, 3, layout)
        self.assertEqual([row['date'] for row in view['rows']], ['2024/02/16', '2024/02/29', '2024/03/15'])
        self.assertEqual(view['rows'][1]['workDays'], 0.0)
        plain, plain_layout = _leap_march(2026)
        plain_view = kintai.daily_rows_from_workbook(plain, 2026, 3, plain_layout)
        self.assertNotIn('2026/02/29', [row['date'] for row in plain_view['rows']])

    def test_contract_sheet_uses_the_same_rule(self):
        blob = _xlsx({
            '2026.3': [
                (3, 4, '休み'),
                (3, 5, '始業時間（00：00）'),
                (3, 7, '終業時間（00：00）'),
                (5, 1, _serial(date(2026, 2, 16))),
                (5, 5, _clock(9, 0)),
                (5, 7, _clock(11, 0)),
                (6, 1, _serial(date(2026, 2, 17))),
                (6, 5, _clock(9, 0)),
                (6, 7, _clock(10, 59)),
                (7, 1, _serial(date(2026, 3, 15))),
                (7, 5, ''),
                (7, 7, ''),
            ],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 3, kintai.MONTHDOT_2026_CONTRACT_LAYOUT)
        self.assertEqual([row['workDays'] for row in view['rows']], [0.5, 0.0, 0.0])

    def test_permissions_match_daily_view(self):
        class Denied:
            user = {'id': 2, 'name': '営', 'login': 's', 'role': 'sales', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/daily/work-days'

        class Floor:
            user = {'id': 3, 'name': '現', 'login': 'f', 'role': 'floor3', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/daily/work-days'

        class Mark:
            user = self.user
            path = '/api/kintai/daily/work-days'

        payload = {
            'personId': self.person['id'],
            'year': 2026,
            'month': 3,
            'days': [{'date': '2026-02-17', 'days': 0}],
        }
        self.assertEqual(kintai.handle_post(Denied(), self.c, '/api/kintai/daily/work-days', payload)[0], 403)
        self.assertEqual(kintai.handle_post(Floor(), self.c, '/api/kintai/daily/work-days', payload)[0], 403)
        self.assertEqual(kintai.handle_get(Denied(), self.c, '/api/kintai/daily')[0], 403)
        code, body = kintai.handle_post(Mark(), self.c, '/api/kintai/daily/work-days', payload)
        self.assertEqual(code, 200)
        hit = next(row for row in body['rows'] if row['date'] == '2026/02/17')
        self.assertEqual(hit['workDays'], 0.0)
        self.assertEqual(hit['workDaysDefault'], 0.5)
        self.assertTrue(hit['workDaysSaved'])
        self.assertEqual(self.book.read_bytes(), self.before)

    def test_saved_days_are_not_replaced_by_the_default(self):
        """11時未満の初期値0でも、保存した1や0.5は上書きしない。"""
        saved = kintai.save_daily_work_days(self.c, self.user, {
            'personId': self.person['id'],
            'year': 2026,
            'month': 3,
            'days': [
                {'date': '2026-02-16', 'days': 0.5},
                {'date': '2026-02-17', 'days': 1},
            ],
        })
        by_date = {row['date']: row for row in saved['rows']}
        self.assertEqual(by_date['2026/02/16']['workDaysDefault'], 1.0)
        self.assertEqual(by_date['2026/02/16']['workDays'], 0.5)
        self.assertTrue(by_date['2026/02/16']['workDaysSaved'])
        self.assertEqual(by_date['2026/02/17']['workDaysDefault'], 0.5)
        self.assertEqual(by_date['2026/02/17']['workDays'], 1.0)
        self.assertTrue(by_date['2026/02/17']['workDaysSaved'])
        self.assertEqual(by_date['2026/02/18']['workDays'], 0.0)
        self.assertFalse(by_date['2026/02/18']['workDaysSaved'])
        again = kintai.person_daily_sheet(self.c, self.person['id'], 2026, 3)
        again_by_date = {row['date']: row for row in again['rows']}
        self.assertEqual(again_by_date['2026/02/16']['workDays'], 0.5)
        self.assertEqual(again_by_date['2026/02/17']['workDays'], 1.0)
        self.assertEqual(kintai.default_attendance_days(_clock(9, 0), _clock(10, 59)), 0.0)
        self.assertEqual(self.book.read_bytes(), self.before)


def _headers():
    return [
        (3, 4, '休み'),
        (3, 5, '始業時間（00：00）'),
        (3, 7, '終業時間（00：00）'),
        (3, 24, '実労働時間'),
    ]


def _march_book() -> bytes:
    cal_col = kintai.SEISHAIN_2026_CAL_COLS[(2026, 3)]
    days = [
        date(2026, 2, 15), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
        date(2026, 2, 19), date(2026, 2, 20), date(2026, 2, 21), date(2026, 3, 13),
        date(2026, 3, 14), date(2026, 3, 15), date(2026, 3, 16),
    ]
    cal = [(3 + i, cal_col, _serial(day)) for i, day in enumerate(days)]
    cells = _headers() + [
        (6, 5, _clock(9, 0)), (6, 7, _clock(18, 0)), (6, 24, 8),
        (7, 5, _clock(9, 0)), (7, 7, _clock(13, 0)),
        (9, 7, _clock(18, 0)),
        (10, 5, _clock(9, 0)),
        (11, 5, _clock(9, 0)), (11, 7, ('formula', 'G11')),
        (12, 5, _clock(9, 0)), (12, 7, _clock(13, 59)),
        (13, 5, _clock(9, 0)), (13, 7, _clock(14, 0)),
        (14, 5, _clock(9, 0)), (14, 7, 1.20625),
        (6, 37, '実労働時間計'), (6, 38, 17.03),
        (7, 37, '予定時間計'), (7, 38, 0),
    ]
    return _xlsx({'年間ｶﾚﾝﾀﾞｰ': cal, '26.3': cells, '集計': []}, {'26.3': '$A$1:$AL$50'})


def _january_book() -> bytes:
    sheets = {}
    areas = {}
    for year, month, days in (
        (2026, 12, [date(2026, 12, 15), date(2026, 12, 16)]),
        (2027, 1, [date(2026, 12, 16), date(2027, 1, 15), date(2027, 1, 16)]),
    ):
        name = kintai.SEISHAIN_2026_MONTH_SHEETS[(year, month)]
        col = kintai.SEISHAIN_2026_CAL_COLS[(year, month)]
        cal = sheets.setdefault('年間ｶﾚﾝﾀﾞｰ', [])
        cells = _headers()
        for i, day in enumerate(days):
            cal.append((3 + i, col, _serial(day)))
            row = 5 + i
            if day == date(2026, 12, 16):
                cells += [(row, 5, _clock(9, 0)), (row, 7, _clock(18, 0))]
        sheets[name] = cells
        areas[name] = '$A$1:$AL$50'
    sheets['集計'] = []
    return _xlsx(sheets, areas)


def _leap_march(year=2024):
    days = [date(year, 2, 15), date(year, 2, 16), date(year, 2, 29) if year == 2024 else date(year, 2, 28), date(year, 3, 15), date(year, 3, 16)]
    cells = [(5 + i, 1, _serial(day)) for i, day in enumerate(days)]
    cells += [
        (3, 5, '始業時間（00：00）'),
        (3, 7, '終業時間（00：00）'),
        (7, 5, _clock(9, 0)),
        (7, 7, _clock(10, 0)),
    ]
    sheet = 'm'
    layout = {**kintai.MONTHDOT_2026_CONTRACT_LAYOUT, 'month_sheets': {(year, 3): sheet}}
    return _xlsx({sheet: cells}), layout


if __name__ == '__main__':
    unittest.main()
