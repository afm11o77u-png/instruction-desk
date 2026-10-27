"""月別シフト表: 祝日・暦・保存・同時編集・出勤簿非影響・名簿マージ。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import kintai
import kintai_shift

ROOT = Path(__file__).resolve().parents[1]


class JapanHolidayTest(unittest.TestCase):
    def test_2026_known_holidays(self):
        h = kintai_shift.japan_holidays(2026)
        self.assertEqual(h[date(2026, 1, 1)], '元日')
        self.assertEqual(h[date(2026, 1, 12)], '成人の日')  # 第2月曜
        self.assertEqual(h[date(2026, 2, 11)], '建国記念の日')
        self.assertEqual(h[date(2026, 2, 23)], '天皇誕生日')
        self.assertIn(date(2026, 3, 20), h)  # 春分
        self.assertEqual(h[date(2026, 4, 29)], '昭和の日')
        self.assertEqual(h[date(2026, 5, 3)], '憲法記念日')
        self.assertEqual(h[date(2026, 5, 4)], 'みどりの日')
        self.assertEqual(h[date(2026, 5, 5)], 'こどもの日')
        self.assertEqual(h[date(2026, 5, 6)], '振替休日')  # 5/3が日曜
        self.assertEqual(h[date(2026, 7, 20)], '海の日')
        self.assertEqual(h[date(2026, 8, 11)], '山の日')
        self.assertEqual(h[date(2026, 9, 21)], '敬老の日')
        self.assertIn(date(2026, 9, 22), h)  # 秋分近傍
        self.assertEqual(h[date(2026, 10, 12)], 'スポーツの日')
        self.assertEqual(h[date(2026, 11, 3)], '文化の日')
        self.assertEqual(h[date(2026, 11, 23)], '勤労感謝の日')

    def test_month_calendar_lengths(self):
        self.assertEqual(len(kintai_shift.month_calendar(2026, 2)), 28)
        self.assertEqual(len(kintai_shift.month_calendar(2024, 2)), 29)
        self.assertEqual(len(kintai_shift.month_calendar(2026, 4)), 30)
        self.assertEqual(len(kintai_shift.month_calendar(2026, 11)), 30)
        nov = kintai_shift.month_calendar(2026, 11)
        self.assertEqual(nov[0]['day'], 1)
        self.assertEqual(nov[-1]['day'], 30)
        # 2026-11-1 は日曜
        self.assertEqual(nov[0]['kind'], 'sun')
        # 2026-11-3 祝日
        self.assertEqual(nov[2]['kind'], 'holiday')
        # 土曜
        sat = next(d for d in nov if d['weekday'] == 5)
        self.assertEqual(sat['kind'], 'sat')


class ShiftSheetStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 't.sqlite3'
        self.c = sqlite3.connect(self.db)
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, "
            "version INTEGER, mark_kintai INTEGER DEFAULT 0)"
        )
        kintai.bind(Path(self.tmp.name))
        kintai.schema(self.c)
        # 既定シードを消して明示マスタを入れる
        self.c.execute('DELETE FROM kintai_people')
        self.c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) '
            'VALUES(?,?,?,?,1,1,0)',
            ('001', 'regular', '山田太郎', ''),
        )
        self.c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) '
            'VALUES(?,?,?,?,1,2,0)',
            ('002', 'part', '佐藤花子', ''),
        )
        self.c.commit()
        self.user = {'id': 1, 'name': '主', 'login': 'o@ex.com', 'role': 'owner', 'mark_kintai': 1}

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_get_empty_uses_roster(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual(sheet['year'], 2026)
        self.assertEqual(sheet['month'], 11)
        self.assertEqual(sheet['version'], 0)
        self.assertFalse(sheet['exists'])
        self.assertEqual(len(sheet['days']), 30)
        keys = [p['key'] for p in sheet['people']]
        self.assertEqual(keys, ['regular:001', 'part:002'])

    def test_save_and_restore(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        cells = {
            'regular:001': {'3': {'kind': 'off', 'text': '×'}, '5': {'kind': 'time', 'text': '15:00まで'}},
        }
        code, saved = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': sheet['people'], 'cells': cells,
        })
        self.assertEqual(code, 200)
        self.assertEqual(saved['version'], 1)
        self.assertEqual(saved['cells']['regular:001']['3']['kind'], 'off')
        self.assertEqual(saved['cells']['regular:001']['5']['text'], '15:00まで')
        again = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual(again['version'], 1)
        self.assertEqual(again['cells']['regular:001']['5']['text'], '15:00まで')

    def test_conflict_does_not_overwrite(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': sheet['people'],
            'cells': {'regular:001': {'1': {'kind': 'off', 'text': '×'}}},
        })
        code, body = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,  # stale
            'people': sheet['people'],
            'cells': {'regular:001': {'2': {'kind': 'off', 'text': '×'}}},
        })
        self.assertEqual(code, 409)
        self.assertTrue(body.get('conflict'))
        cur = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertIn('1', cur['cells']['regular:001'])
        self.assertNotIn('2', cur['cells'].get('regular:001', {}))

    def test_history_kept_when_person_leaves(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': sheet['people'],
            'cells': {'part:002': {'10': {'kind': 'off', 'text': '×'}}},
        })
        self.c.execute('UPDATE kintai_people SET retired=1 WHERE employee_code=?', ('002',))
        self.c.commit()
        again = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        keys = {p['key']: p for p in again['people']}
        self.assertIn('regular:001', keys)
        self.assertIn('part:002', keys)
        self.assertEqual(keys['part:002']['source'], 'history')
        self.assertEqual(keys['part:002']['displayName'], '佐藤花子')
        self.assertEqual(again['cells']['part:002']['10']['kind'], 'off')

    def test_does_not_touch_monthly_attendance(self):
        self.c.execute(
            '''INSERT INTO kintai_monthly(
                person_id,year,month,work_days,holiday_days,paid_leave_days,
                ordinary_minutes,overtime_minutes,source_mtime,imported_at,status,error
            ) VALUES (1,2026,11,20,8,1,0,0,'','2026-01-01T00:00:00','取込済','')'''
        )
        self.c.commit()
        before = self.c.execute('SELECT work_days,status FROM kintai_monthly WHERE person_id=1').fetchone()
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': sheet['people'],
            'cells': {'regular:001': {'1': {'kind': 'off', 'text': '×'}}},
        })
        after = self.c.execute('SELECT work_days,status FROM kintai_monthly WHERE person_id=1').fetchone()
        self.assertEqual(before['work_days'], after['work_days'])
        self.assertEqual(before['status'], after['status'])

    def test_remove_row_does_not_come_back(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        only = [p for p in sheet['people'] if p['key'] == 'regular:001']
        code, saved = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': only,
            'cells': {'regular:001': {'1': {'kind': 'off', 'text': '×'}}},
        })
        self.assertEqual(code, 200)
        keys = [p['key'] for p in saved['people']]
        self.assertEqual(keys, ['regular:001'])
        again = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual([p['key'] for p in again['people']], ['regular:001'])
        # 在籍マスタ自体は残る
        n = self.c.execute('SELECT COUNT(*) FROM kintai_people WHERE active=1').fetchone()[0]
        self.assertEqual(n, 2)

    def test_rename_keeps_snapshot_and_other_month_intact(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        people = [{**p, 'displayName': '山田（11月）' if p['key'] == 'regular:001' else p['displayName']} for p in sheet['people']]
        kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0, 'people': people, 'cells': {},
        })
        nov = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual(next(p['displayName'] for p in nov['people'] if p['key'] == 'regular:001'), '山田（11月）')
        # 別月は未保存→在籍マスタの氏名
        dec = kintai_shift.get_shift_sheet(self.c, 2026, 12)
        self.assertFalse(dec['exists'])
        self.assertEqual(next(p['displayName'] for p in dec['people'] if p['key'] == 'regular:001'), '山田太郎')
        # マスタ未変更
        name = self.c.execute(
            "SELECT display_name FROM kintai_people WHERE employee_code='001'"
        ).fetchone()['display_name']
        self.assertEqual(name, '山田太郎')

    def test_shift_only_row_does_not_touch_people_master(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        before = self.c.execute('SELECT COUNT(*) FROM kintai_people').fetchone()[0]
        people = list(sheet['people']) + [{
            'key': 'shift:abc123',
            'personId': None,
            'employeeCode': 'abc123',
            'employment': 'shift',
            'displayName': '臨時スタッフ',
            'sortOrder': 99,
            'source': 'shift',
        }]
        code, saved = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': people,
            'cells': {'shift:abc123': {'2': {'kind': 'off', 'text': '×'}}},
        })
        self.assertEqual(code, 200)
        shift_rows = [p for p in saved['people'] if p['source'] == 'shift']
        self.assertEqual(len(shift_rows), 1)
        self.assertEqual(shift_rows[0]['displayName'], '臨時スタッフ')
        after = self.c.execute('SELECT COUNT(*) FROM kintai_people').fetchone()[0]
        self.assertEqual(before, after)
        self.assertFalse(any(p['key'] == 'shift:abc123' for p in saved['roster']))


class ShiftUiContractTest(unittest.TestCase):
    def test_top_nav_and_panels(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('data-catalog="kintai"', index)
        self.assertIn('>勤怠管理<', index)
        self.assertIn('id="kintainav"', index)
        self.assertIn('data-kintai-section="attendance"', index)
        self.assertIn('data-kintai-section="shift"', index)
        self.assertIn('id="kintaiattendance"', index)
        self.assertIn('id="kintaishift"', index)
        self.assertIn('id="shiftgrid"', index)
        self.assertIn('id="shiftaddrow"', index)
        self.assertIn('id="shiftaddroster"', index)
        self.assertNotIn('id="kintai-open"', index)
        self.assertIn('kintai-shift.js', index)
        # 出勤簿はそのまま
        self.assertIn('出勤簿から更新', index)
        self.assertIn('id="kintaitable"', index)

    def test_scripts_and_server_route(self):
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("'/kintai-shift.js':'kintai-shift.js'", server)
        place = (ROOT / 'placement.js').read_text(encoding='utf-8')
        self.assertIn("kintai:'勤怠管理'", place)
        self.assertIn('catalog-kintai', place)
        self.assertIn('showKintaiSection', place)
        account = (ROOT / 'account.js').read_text(encoding='utf-8')
        self.assertIn('data-catalog="kintai"', account)
        self.assertIn('mark_kintai', account)
        self.assertNotIn('kintai-open', account)
        js = (ROOT / 'kintai-shift.js').read_text(encoding='utf-8')
        self.assertIn('loadKintaiShift', js)
        self.assertIn('contextmenu', js)
        self.assertIn('shiftFetch', js)
        self.assertIn('addShiftRow', js)
        self.assertIn('deleteRow', js)
        self.assertIn('shift-name-input', js)
        self.assertIn('ひとつ戻す', (ROOT / 'index.html').read_text(encoding='utf-8'))
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn('shift-sat', css)
        self.assertIn('shift-sun', css)
        self.assertIn('A4 landscape', css)
        self.assertIn('printing-shift', css)
        self.assertIn('shift-name-input', css)


if __name__ == '__main__':
    unittest.main()
