"""月別シフト表: 祝日・暦・保存・同時編集・出勤簿非影響・名簿マージ。"""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

import kintai
import kintai_shift

ROOT = Path(__file__).resolve().parents[1]


def _chrome_bin() -> str | None:
    for name in ('google-chrome-stable', 'google-chrome', 'chromium', 'chromium-browser'):
        path = shutil.which(name)
        if path:
            return path
    return None


def _pdf_mediabox(pdf_path: Path) -> tuple[float, float]:
    data = pdf_path.read_bytes()
    m = re.search(rb'/MediaBox\s*\[\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s*\]', data)
    if not m:
        raise AssertionError(f'no MediaBox in {pdf_path}')
    x0, y0, x1, y1 = (float(m.group(i)) for i in range(1, 5))
    return (x1 - x0, y1 - y0)


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

    def test_new_month_seeds_sunday_offs(self):
        """未作成月は全員の日曜を×で初期化する（2026-11-1/8/15/22/29）。"""
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertFalse(sheet['exists'])
        sundays = [d['day'] for d in sheet['days'] if d['weekday'] == 6]
        self.assertEqual(sundays, [1, 8, 15, 22, 29])
        for p in sheet['people']:
            row = sheet['cells'].get(p['key'], {})
            for day in sundays:
                self.assertEqual(row[str(day)], {'kind': 'off', 'text': '×'}, msg=f'{p["key"]} day {day}')
            # 日曜以外は初期化しない
            self.assertNotIn('2', row)
            self.assertNotIn('3', row)

    def test_seed_sunday_does_not_overwrite_existing_cells(self):
        people = [{'key': 'regular:001'}, {'key': 'part:002'}]
        days = kintai_shift.month_calendar(2026, 11)
        cells = {
            'regular:001': {
                '1': {'kind': 'time', 'text': '15:00まで'},  # 日曜・手入力
                '8': {'kind': 'off', 'text': '×'},
            },
        }
        seeded = kintai_shift.seed_sunday_offs(people, cells, days)
        self.assertEqual(seeded['regular:001']['1']['kind'], 'time')
        self.assertEqual(seeded['regular:001']['1']['text'], '15:00まで')
        self.assertEqual(seeded['regular:001']['8']['kind'], 'off')
        self.assertEqual(seeded['regular:001']['15'], {'kind': 'off', 'text': '×'})
        self.assertEqual(seeded['part:002']['1'], {'kind': 'off', 'text': '×'})

    def test_saved_month_reload_does_not_reseed_sundays(self):
        """保存済み月はスナップショットのまま。空の日曜を再初期化しない。"""
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        # 日曜を意図的に空（クリア）にして保存
        cells = {
            'regular:001': {'3': {'kind': 'off', 'text': '×'}},
            'part:002': {'5': {'kind': 'time', 'text': '12:00まで'}},
        }
        code, saved = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': sheet['people'], 'cells': cells,
        })
        self.assertEqual(code, 200)
        again = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertTrue(again['exists'])
        self.assertEqual(again['cells']['regular:001']['3']['kind'], 'off')
        self.assertNotIn('1', again['cells'].get('regular:001', {}))
        self.assertNotIn('8', again['cells'].get('regular:001', {}))
        self.assertNotIn('1', again['cells'].get('part:002', {}))
        self.assertEqual(again['cells']['part:002']['5']['text'], '12:00まで')

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

    def test_second_save_updates_without_connection_rowcount(self):
        """再編集保存: Connection.rowcount 参照だと AttributeError→500 になる回帰を防ぐ。"""
        import inspect
        src = inspect.getsource(kintai_shift.save_shift_sheet)
        self.assertNotIn('c.rowcount', src)
        self.assertIn('cur.rowcount', src)
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        code1, s1 = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': sheet['people'], 'cells': {},
        })
        self.assertEqual(code1, 200)
        self.assertEqual(s1['version'], 1)
        code2, s2 = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 1,
            'people': s1['people'],
            'cells': {'regular:001': {'7': {'kind': 'off', 'text': '×'}}},
        })
        self.assertEqual(code2, 200)
        self.assertEqual(s2['version'], 2)
        self.assertEqual(s2['cells']['regular:001']['7']['kind'], 'off')
        reloaded = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual(reloaded['version'], 2)
        self.assertEqual(reloaded['cells']['regular:001']['7']['text'], '×')

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

    def test_reorder_people_persists_with_cells(self):
        """ドラッグ入替後の並びを保存し、セルは氏名キーに追従したまま戻る。"""
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        by_key = {p['key']: dict(p) for p in sheet['people']}
        # シフト専用を先頭、在籍を入れ替え（source 種別をまたぐ）
        people = [
            {
                'key': 'shift:abc123',
                'personId': None,
                'employeeCode': 'abc123',
                'employment': 'shift',
                'displayName': '臨時スタッフ',
                'sortOrder': 0,
                'source': 'shift',
            },
            {**by_key['part:002'], 'sortOrder': 1},
            {**by_key['regular:001'], 'sortOrder': 2},
        ]
        cells = {
            'shift:abc123': {'1': {'kind': 'off', 'text': '×'}},
            'part:002': {'2': {'kind': 'time', 'text': '15:00まで'}},
            'regular:001': {'3': {'kind': 'off', 'text': '×'}},
        }
        code, saved = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': people,
            'cells': cells,
        })
        self.assertEqual(code, 200)
        keys = [p['key'] for p in saved['people']]
        self.assertEqual(keys, ['shift:abc123', 'part:002', 'regular:001'])
        self.assertEqual([p['sortOrder'] for p in saved['people']], [0, 1, 2])
        self.assertEqual(saved['cells']['shift:abc123']['1']['kind'], 'off')
        self.assertEqual(saved['cells']['part:002']['2']['text'], '15:00まで')
        self.assertEqual(saved['cells']['regular:001']['3']['kind'], 'off')
        again = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual([p['key'] for p in again['people']], keys)
        self.assertEqual(again['cells']['part:002']['2']['text'], '15:00まで')
        # 再度入れ替えても version 付きで上書きできる
        flipped = list(reversed(again['people']))
        code2, saved2 = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': again['version'],
            'people': flipped,
            'cells': again['cells'],
        })
        self.assertEqual(code2, 200)
        self.assertEqual(
            [p['key'] for p in saved2['people']],
            ['regular:001', 'part:002', 'shift:abc123'],
        )
        self.assertEqual(saved2['cells']['shift:abc123']['1']['text'], '×')

    def test_default_order_applies_to_uncreated_month(self):
        """標準並び保存 → 未作成月を開くと並びが一致する。"""
        empty = kintai_shift.get_default_order(self.c)
        self.assertFalse(empty['exists'])
        self.assertEqual(empty['keys'], [])
        # 在籍マスタ順は 001, 002。標準並びは逆順
        saved = kintai_shift.save_default_order(self.c, self.user, {
            'keys': ['part:002', 'regular:001'],
        })
        self.assertTrue(saved['exists'])
        self.assertEqual(saved['keys'], ['part:002', 'regular:001'])
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 12)
        self.assertFalse(sheet['exists'])
        self.assertEqual([p['key'] for p in sheet['people']], ['part:002', 'regular:001'])

    def test_default_order_persists_on_reload(self):
        kintai_shift.save_default_order(self.c, self.user, {
            'keys': ['part:002', 'regular:001'],
        })
        again = kintai_shift.get_default_order(self.c)
        self.assertEqual(again['keys'], ['part:002', 'regular:001'])
        self.assertTrue(again['updatedAt'])
        self.assertEqual(again['updatedBy'], '主')

    def test_default_order_change_does_not_reorder_saved_month(self):
        """標準並び変更後も、保存済み月の並び・セルは変わらない。"""
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        people = [
            {**sheet['people'][0], 'sortOrder': 0},  # regular:001
            {**sheet['people'][1], 'sortOrder': 1},  # part:002
        ]
        cells = {
            'regular:001': {'3': {'kind': 'off', 'text': '×'}},
            'part:002': {'5': {'kind': 'time', 'text': '15:00まで'}},
        }
        code, nov = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': people, 'cells': cells,
        })
        self.assertEqual(code, 200)
        self.assertEqual([p['key'] for p in nov['people']], ['regular:001', 'part:002'])
        # 標準並びを逆にしても保存済み11月は不変
        kintai_shift.save_default_order(self.c, self.user, {
            'keys': ['part:002', 'regular:001'],
        })
        again = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual([p['key'] for p in again['people']], ['regular:001', 'part:002'])
        self.assertEqual(again['cells']['regular:001']['3']['kind'], 'off')
        self.assertEqual(again['cells']['part:002']['5']['text'], '15:00まで')
        self.assertEqual(again['version'], nov['version'])

    def test_default_order_skips_inactive_appends_new(self):
        kintai_shift.save_default_order(self.c, self.user, {
            'keys': ['part:002', 'regular:001', 'regular:gone'],
        })
        self.c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) '
            'VALUES(?,?,?,?,1,3,0)',
            ('003', 'regular', '新規次郎', ''),
        )
        self.c.execute('UPDATE kintai_people SET retired=1 WHERE employee_code=?', ('002',))
        self.c.commit()
        sheet = kintai_shift.get_shift_sheet(self.c, 2027, 1)
        self.assertFalse(sheet['exists'])
        keys = [p['key'] for p in sheet['people']]
        # 退職した part:002 と存在しない gone は飛ばし、新規は末尾
        self.assertEqual(keys, ['regular:001', 'regular:003'])

    def test_default_order_save_does_not_clobber_month_sheet(self):
        """月保存と標準並び保存は互いのデータを上書きしない。"""
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        cells = {'regular:001': {'1': {'kind': 'off', 'text': '×'}}}
        code, saved = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': sheet['people'], 'cells': cells,
        })
        self.assertEqual(code, 200)
        before_people = self.c.execute(
            'SELECT people_json, cells_json, version FROM kintai_shift_sheets WHERE year=2026 AND month=11'
        ).fetchone()
        kintai_shift.save_default_order(self.c, self.user, {
            'keys': ['part:002', 'regular:001'],
        })
        after_people = self.c.execute(
            'SELECT people_json, cells_json, version FROM kintai_shift_sheets WHERE year=2026 AND month=11'
        ).fetchone()
        self.assertEqual(before_people['people_json'], after_people['people_json'])
        self.assertEqual(before_people['cells_json'], after_people['cells_json'])
        self.assertEqual(before_people['version'], after_people['version'])
        # 逆に月保存しても標準並びは消えない
        kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': saved['version'],
            'people': list(reversed(saved['people'])),
            'cells': cells,
        })
        self.assertEqual(
            kintai_shift.get_default_order(self.c)['keys'],
            ['part:002', 'regular:001'],
        )

    def test_default_order_stores_keys_not_names_or_cells(self):
        """標準並びは identity key のみ。同姓同名・セル内容は含めない。"""
        self.c.execute(
            'INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) '
            'VALUES(?,?,?,?,1,3,0)',
            ('009', 'regular', '山田太郎', ''),  # 同姓同名
        )
        self.c.commit()
        kintai_shift.save_default_order(self.c, self.user, {
            'keys': [
                {'key': 'regular:009', 'displayName': '山田太郎', 'cells': {'1': {'kind': 'off'}}},
                'regular:001',
            ],
        })
        order = kintai_shift.get_default_order(self.c)
        self.assertEqual(order['keys'], ['regular:009', 'regular:001'])
        raw = self.c.execute('SELECT keys_json FROM kintai_shift_default_order WHERE id=1').fetchone()['keys_json']
        self.assertNotIn('山田', raw)
        self.assertNotIn('off', raw)
        self.assertNotIn('cells', raw)

    def test_reorder_cells_stay_with_person_keys(self):
        """並び替え後もセルは person key に紐づき、別人へずれない。"""
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        flipped = list(reversed(sheet['people']))
        cells = {
            'regular:001': {'7': {'kind': 'off', 'text': '×'}},
            'part:002': {'8': {'kind': 'time', 'text': '12:00まで'}},
        }
        code, saved = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': flipped, 'cells': cells,
        })
        self.assertEqual(code, 200)
        self.assertEqual([p['key'] for p in saved['people']], ['part:002', 'regular:001'])
        self.assertEqual(saved['cells']['regular:001']['7']['kind'], 'off')
        self.assertEqual(saved['cells']['part:002']['8']['text'], '12:00まで')
        # 標準並びを別順にしても保存月のセル所有者は不変
        kintai_shift.save_default_order(self.c, self.user, {'keys': ['regular:001', 'part:002']})
        again = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual(again['cells']['regular:001']['7']['text'], '×')
        self.assertNotIn('7', again['cells'].get('part:002', {}))

    def test_no_default_keeps_existing_initial_order(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertFalse(sheet['exists'])
        self.assertEqual([p['key'] for p in sheet['people']], ['regular:001', 'part:002'])


class ShiftDayXToggleFixtureTest(unittest.TestCase):
    """日付見出し「この日を全員×」トグルの決定表（kintai-shift.js bulkMarkOneDay と対応）。"""

    OFF = {'kind': 'off', 'text': '×'}
    TIME = {'kind': 'time', 'text': '15:00まで'}

    @staticmethod
    def day_has_any_off(cells_by_person, day):
        d = str(day)
        for row in cells_by_person.values():
            cur = (row or {}).get(d)
            if cur and cur.get('kind') == 'off':
                return True
        return False

    @classmethod
    def apply_toggle(cls, cells_by_person, day, mode='empty'):
        """1クリック後の cells と動作種別（clear / fill）を返す。"""
        d = str(day)
        out = {k: dict(v or {}) for k, v in cells_by_person.items()}
        if cls.day_has_any_off(out, day):
            for key in list(out):
                out[key].pop(d, None)
            return out, 'clear'
        off = dict(cls.OFF)
        for key in list(out):
            cur = out[key].get(d)
            if mode == 'empty':
                if cur:
                    continue
            elif cur and cur.get('kind') == 'off':
                continue
            out[key][d] = dict(off)
        return out, 'fill'

    def test_no_off_fills_empty_only(self):
        before = {
            'a': {},
            'b': {'3': dict(self.TIME)},
            'c': {},
        }
        after, action = self.apply_toggle(before, 3, mode='empty')
        self.assertEqual(action, 'fill')
        self.assertEqual(after['a']['3'], self.OFF)
        self.assertEqual(after['b']['3'], self.TIME)  # 空欄のみなので時間は残す
        self.assertEqual(after['c']['3'], self.OFF)

    def test_no_off_overwrite_sets_all(self):
        before = {
            'a': {},
            'b': {'5': dict(self.TIME)},
        }
        after, action = self.apply_toggle(before, 5, mode='overwrite')
        self.assertEqual(action, 'fill')
        self.assertEqual(after['a']['5'], self.OFF)
        self.assertEqual(after['b']['5'], self.OFF)

    def test_any_off_clears_everyone_including_time(self):
        before = {
            'a': {'7': dict(self.OFF)},
            'b': {'7': dict(self.TIME)},
            'c': {},
        }
        after, action = self.apply_toggle(before, 7, mode='empty')
        self.assertEqual(action, 'clear')
        self.assertNotIn('7', after['a'])
        self.assertNotIn('7', after['b'])
        self.assertNotIn('7', after['c'])

    def test_second_click_clears_after_fill(self):
        before = {'a': {}, 'b': {}}
        filled, action1 = self.apply_toggle(before, 1, mode='empty')
        self.assertEqual(action1, 'fill')
        cleared, action2 = self.apply_toggle(filled, 1, mode='empty')
        self.assertEqual(action2, 'clear')
        self.assertNotIn('1', cleared['a'])
        self.assertNotIn('1', cleared['b'])


class ShiftUiContractTest(unittest.TestCase):
    def test_top_nav_and_panels(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('id="modekintai"', index)
        self.assertNotIn('>勤怠管理<', index)
        self.assertIn('id="kintainav"', index)
        self.assertIn('data-kintai-section="attendance"', index)
        self.assertIn('data-kintai-section="shift"', index)
        self.assertIn('id="kintaiattendance"', index)
        self.assertIn('id="kintaishift"', index)
        self.assertIn('id="shiftgrid"', index)
        self.assertIn('id="shiftaddrow"', index)
        self.assertIn('id="shiftaddroster"', index)
        self.assertIn('id="shiftsave"', index)
        self.assertIn('id="shiftsavedefaultorder"', index)
        self.assertIn('標準の並び順として保存', index)
        self.assertIn('未作成の月に使用します。保存済みの月には反映しません', index)
        self.assertIn('id="shiftbulkwday"', index)
        self.assertIn('今月の指定曜日を全員×', index)
        self.assertIn('id="shiftbulkweekday"', index)
        self.assertIn('name="shiftbulkmode"', index)
        self.assertIn('value="empty"', index)
        self.assertIn('value="overwrite"', index)
        self.assertIn('空欄のみ', index)
        self.assertIn('すべて上書きして×', index)
        self.assertIn('id="shiftbulkscope"', index)
        self.assertIn('この日を全員×', index)
        self.assertIn('id="shiftsavedialog"', index)
        self.assertIn('保存して移動', index)
        self.assertIn('破棄して移動', index)
        self.assertNotIn('id="kintai-open"', index)
        self.assertIn('kintai-shift.js', index)
        # 出勤簿はそのまま
        self.assertIn('出勤簿から更新', index)
        self.assertIn('id="kintaitable"', index)
        # 保存済み月へ標準並びを適用する機能は置かない
        self.assertNotIn('保存済みの月に適用', index)
        self.assertNotIn('applyDefaultOrderToSaved', index)

    def test_scripts_and_server_route(self):
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("'/kintai-shift.js':'kintai-shift.js'", server)
        place = (ROOT / 'placement.js').read_text(encoding='utf-8')
        self.assertIn("kintai:'マーク管理'", place)
        self.assertIn('catalog-kintai', place)
        self.assertIn('showKintaiSection', place)
        account = (ROOT / 'account.js').read_text(encoding='utf-8')
        self.assertIn('id="markmanage"', account)
        self.assertIn('mark_kintai', account)
        self.assertNotIn('kintai-open', account)
        js = (ROOT / 'kintai-shift.js').read_text(encoding='utf-8')
        self.assertIn('loadKintaiShift', js)
        self.assertIn('contextmenu', js)
        self.assertIn('shiftFetch', js)
        self.assertIn('addShiftRow', js)
        self.assertIn('deleteRow', js)
        self.assertIn('shift-name-input', js)
        self.assertIn('reorderPeople', js)
        self.assertIn('data-drag-person', js)
        self.assertIn('shift-drag', js)
        self.assertIn('onRowDragStart', js)
        self.assertIn('pushUndo', js)
        self.assertIn('saveDefaultOrder', js)
        self.assertIn('/api/kintai/shift/default-order', js)
        self.assertIn('shiftsavedefaultorder', js)
        # 標準並び保存は keys のみ（cells を送らない）
        self.assertIn("shiftFetch('/api/kintai/shift/default-order', { keys })", js)
        self.assertNotIn('applyDefaultToSaved', js)
        # 一括×（日付見出し・指定曜日）と undo / 未保存
        self.assertIn('bulkMarkOffDays', js)
        self.assertIn('bulkMarkOneDay', js)
        self.assertIn('bulkMarkWeekday', js)
        self.assertIn('この日を全員×', js)
        self.assertIn('data-bulk-day', js)
        self.assertIn("mode === 'empty'", js)
        self.assertIn('overwrite', js)
        self.assertIn('visiblePeople', js)
        self.assertIn('updateBulkScopeNote', js)
        self.assertIn('seedSundaysForKeys', js)
        # 日付見出し「この日を全員×」はトグル（×あり→全員空欄／なし→既存一括×）
        self.assertIn('dayHasAnyOff', js)
        self.assertIn('bulkClearOneDay', js)
        self.assertIn("cur.kind === 'off'", js)
        self.assertIn('dayHasAnyOff(d, targets)', js)
        self.assertIn('bulkClearOneDay(d, label)', js)
        self.assertIn('bulkMarkOffDays([d], bulkFillMode(), label)', js)
        self.assertIn('pushUndo()', js)
        self.assertIn('markDirty()', js)
        # 曜日一括はトグルせず従来の一括×のみ
        weekday_fn = js[js.find('function bulkMarkWeekday'):js.find('function seedSundaysForKeys')]
        self.assertIn('bulkMarkOffDays(days, bulkFillMode()', weekday_fn)
        self.assertNotIn('bulkClearOneDay', weekday_fn)
        self.assertNotIn('dayHasAnyOff', weekday_fn)
        self.assertIn('ひとつ戻す', (ROOT / 'index.html').read_text(encoding='utf-8'))
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        self.assertIn('shift-sat', css)
        self.assertIn('shift-sun', css)
        self.assertIn('.shift-drag', css)
        self.assertIn('.shift-day-bulk', css)
        self.assertIn('.shift-bulk-row', css)
        self.assertIn('shift-drop-before', css)
        self.assertIn('cursor:grab', css)
        self.assertIn('A4 landscape', css)
        self.assertIn('printing-shift', css)
        self.assertIn('shift-name-input', css)
        self.assertIn('shift-name-print', css)
        # 共通 body>*{display:none} を打ち消し、main を再表示する（白紙印刷の再発防止）
        compact = css.replace(' ', '').replace('\n', '')
        self.assertIn('body.printing-shift>main{display:block!important', compact)
        self.assertIn('page:shift-sheet', compact)
        self.assertIn('@pageshift-sheet{size:A4landscape', compact)
        # 通常印刷の @page{size:A4} は残す。named page に加え main/#kintaishift へ page 割当
        self.assertIn('@mediaprint{body>*{display:none!important}', compact)
        self.assertIn('@page{size:A4;margin:12mm}', compact)
        self.assertIn('body.printing-shift#kintaishift{page:shift-sheet}', compact)
        # CSS の実 @page ルールは named shift-sheet のみ（unnamed landscape は JS 一時差し込み）
        self.assertEqual(len(re.findall(r'@page\s+shift-sheet\s*\{[^}]*A4 landscape', css)), 1)
        self.assertNotIn('@page{size:A4 landscape', css.replace(' ', ''))
        # コメント以外に unnamed landscape ルールを直書きしていないこと（compact からコメント除去はしないので JS 側を契約）
        # 画面の土日祝色は維持。印刷はカラー用／白黒用で切替（白黒は PR#228 の #d9d9d9/#bfbfbf）
        self.assertIn('background:#d9d9d9!important', css)
        self.assertIn('background:#bfbfbf!important', css)
        self.assertIn('print-shift-color', css)
        self.assertIn('print-shift-bw', css)
        self.assertIn('body.printing-shift.print-shift-color .shift-grid .shift-sat', css)
        self.assertIn('body.printing-shift.print-shift-bw .shift-grid .shift-sat', css)
        # カラー用は画面と同系の薄青／薄赤
        self.assertIn('background:#e8f2fb!important', css)
        self.assertIn('background:#fdeceb!important', css)
        print_block = css[css.find('body.printing-shift'):]
        self.assertNotIn('repeating-linear-gradient', print_block)
        self.assertIn('.shift-grid .shift-sat,.shift-grid th.shift-sat{background:#e8f2fb}', css)
        self.assertIn('.shift-grid .shift-sun,.shift-grid th.shift-sun{background:#fdeceb}', css)
        self.assertIn('body.printing-shift .shift-drag', css)
        self.assertIn('shift-print-banner', css)
        self.assertIn('shift-print-legend', css)
        # 見本どおり: 濃緑バー・タイトル濃グレー大きめ・ゼブラ・×赤系・薄い罫線
        self.assertIn('2.8pt solid #245c47', css)
        self.assertIn('color:#464a4d', css)
        self.assertIn('font-size:18pt', css)
        self.assertIn('shift-print-title-text{font-size:18pt;font-weight:700', css.replace('\n', '').replace(' ', ''))
        # 凡例は見本どおり小さめ（タイトルより小さい）
        self.assertIn('shift-print-legend{font-size:8pt;font-weight:400', css.replace('\n', '').replace(' ', ''))
        self.assertIn('tbody tr:nth-child(even)', css)
        self.assertIn('background:#f0f5f2!important', css)
        self.assertIn('color:#b85c4a!important', css)
        # 印刷の時刻は「15:00」1行。「まで」行は出さない
        self.assertIn('shift-time-until{display:none!important}', css.replace('\n', '').replace(' ', ''))
        self.assertIn('0.35pt solid #c5cbc7', css)
        # 画面: 区分色・勤務メモ・日付列狭め。印刷からメモ列・凡例・区分色を除外
        self.assertIn('shift-emp-regular', css)
        self.assertIn('shift-emp-contract', css)
        self.assertIn('shift-emp-part', css)
        self.assertIn('background:#e6f2ea', css)
        self.assertIn('background:#eef3f8', css)
        self.assertIn('background:#f7f0e8', css)
        self.assertIn('shift-col-memo', css)
        self.assertIn('shift-memo-input', css)
        # 勤務メモは2行折返し・日付セルと同高(1.7rem)・超過クリップ（行高を増やさない）
        memo_css = css.replace('\n', '').replace(' ', '')
        self.assertIn('.shift-memo{', memo_css)
        self.assertIn('height:1.7rem', memo_css)
        self.assertIn('max-height:1.7rem', memo_css)
        self.assertIn('white-space:normal', memo_css)  # nowrap を上書きして折返し
        self.assertIn('overflow:hidden', memo_css)
        self.assertIn('font-size:9px', memo_css)
        self.assertIn('line-height:1.15', memo_css)
        self.assertIn('white-space:pre-wrap', memo_css)
        self.assertIn('resize:none', memo_css)
        self.assertIn('min-width:1.72rem', css)
        self.assertIn('body.printing-shift .shift-memo', css)
        self.assertIn('body.printing-shift .shift-emp-legend', css)
        self.assertIn('body.printing-shift .shift-corner-memo', css)
        # 氏名は1行（nowrap・十分な min-width）。break-all / 強制折返しなし
        # colgroup で氏名列幅を確保（colspan バナー対策）
        self.assertIn('col.shift-col-name{width:9em}', css.replace('\n', '').replace(' ', ''))
        self.assertIn('col.shift-col-day{width:auto}', css.replace('\n', '').replace(' ', ''))
        name_rule_start = css.find('body.printing-shift .shift-grid .shift-name{')
        self.assertGreater(name_rule_start, 0)
        name_print_rules = css[name_rule_start:]
        name_print_rules = name_print_rules[: name_print_rules.find('body.printing-shift .shift-grid .shift-day')]
        self.assertIn('white-space:nowrap', name_print_rules)
        self.assertIn('min-width:9em', name_print_rules)
        self.assertIn('width:9em', name_print_rules)
        self.assertNotIn('break-all', name_print_rules)
        self.assertNotIn('white-space:normal', name_print_rules)
        name_compact = name_print_rules.replace('\n', '').replace(' ', '')
        self.assertIn('shift-name-print{display:inline!important;font-weight:700;color:#333;white-space:nowrap', name_compact)
        js_for_cols = (ROOT / 'kintai-shift.js').read_text(encoding='utf-8')
        self.assertIn('shift-col-name', js_for_cols)
        self.assertIn('<colgroup>', js_for_cols)
        # 見本フッタ文言は印刷に出さない
        self.assertNotIn('確認用イメージ', css)
        self.assertNotIn('A4 横 / 1か月分', css)
        self.assertNotIn('A4 横／1か月分', css)
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="shiftprinttitle"', index)
        self.assertIn('class="shift-toolbar no-print"', index)
        self.assertIn('id="shiftprintdialog"', index)
        self.assertIn('カラー用', index)
        self.assertIn('白黒用', index)
        self.assertIn('プリンタドライバ', index)
        self.assertIn('背景のグラフィック', index)
        self.assertIn('ヘッダー／フッター', index)
        self.assertNotIn('確認用イメージ', index)
        js = (ROOT / 'kintai-shift.js').read_text(encoding='utf-8')
        self.assertIn('shiftprinttitle', js)
        self.assertIn('shift-name-print', js)
        self.assertIn('printing-shift', js)
        self.assertIn('print-shift-color', js)
        self.assertIn('print-shift-bw', js)
        self.assertIn('openPrintDialog', js)
        self.assertIn('runPrint', js)
        self.assertIn('マーク シフト表', js)
        self.assertIn('空欄：出勤　×：休み　時刻：退勤予定', js)
        self.assertIn('shift-print-banner', js)
        self.assertIn('afterprint', js)
        # 勤務メモ・区分色・時刻表示正規化
        self.assertIn('displayTimeText', js)
        self.assertIn('saveWorkMemos', js)
        self.assertIn('/api/kintai/shift/work-memos', js)
        self.assertIn('keepLocalMemos', js)
        self.assertIn('empNameClass', js)
        self.assertIn('shift-emp-', js)
        self.assertIn('shift-memo-input', js)
        self.assertIn('<textarea class="shift-memo-input" rows="2"', js)
        self.assertIn("'15:00'", js)
        self.assertNotIn("'15:00まで'", js)
        self.assertIn('id="shiftsavememos"', index)
        self.assertIn('value="15:00"', index)
        self.assertIn('shiftemplegend', index)
        # Chromium 向け: unnamed @page landscape を一時差し込み（named page だけでは縦のまま）
        self.assertIn('ensureShiftLandscapePage', js)
        self.assertIn('clearShiftLandscapePage', js)
        self.assertIn('shift-print-page-css', js)
        self.assertIn("@page{size:A4 landscape;margin:8mm}", js)
        self.assertIn('ensureShiftLandscapePage()', js)
        self.assertIn('clearShiftLandscapePage()', js)
        # 印刷モードは保存データを書き換えない
        self.assertIn('保存データは書き換えない', js)
        # 手動上書き保存（編集ごとの自動保存はしない）
        self.assertIn('confirmKintaiShiftLeave', js)
        self.assertIn('未保存の変更', js)
        self.assertIn('保存済み（', js)
        self.assertIn("beforeunload", js)
        self.assertIn('saving', js)
        self.assertNotIn('scheduleSave', js)
        # 一括×後は markDirty → 未保存。undoOnce で戻す
        self.assertIn('markDirty()', js)
        self.assertIn('undoOnce', js)
        py = (ROOT / 'kintai_shift.py').read_text(encoding='utf-8')
        self.assertIn('def seed_sunday_offs', py)
        self.assertIn('seed_sunday_offs(people, cells, days)', py)
        self.assertNotIn('再試行します', js)
        self.assertNotIn('setTimeout(() => {\n        saveTimer = null;\n        saveShift()', js)
        place = (ROOT / 'placement.js').read_text(encoding='utf-8')
        self.assertIn('confirmKintaiShiftLeave', place)

    def test_kintai_api_avoids_disk_space_error_copy(self):
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("'/api/kintai/shift/work-memos'", server)
        self.assertIn("'/api/kintai/shift/default-order'", server)
        self.assertIn("'/api/kintai/shift'", server)
        self.assertIn('シフト表を保存できませんでした。入力内容はそのまま残しています', server)
        self.assertIn('勤怠の処理に失敗しました。入力内容はそのまま残しています', server)
        # 勤怠API専用の reply 文言に容量案内を含めない
        self.assertIn("return self.reply(500, {'error': 'シフト表を保存できませんでした。入力内容はそのまま残しています。しばらくして再度お試しください。'})", server)
        js = (ROOT / 'kintai-shift.js').read_text(encoding='utf-8')
        self.assertIn('入力は画面に残しています', js)
        self.assertIn('if (e.conflict)', js)
        # 自動再試行タイマーが復活していないこと
        self.assertNotIn('再試行します', js)

    def test_shift_print_unnamed_page_forces_landscape_pdf(self):
        """Chromium: later unnamed @page landscape overrides global portrait (PDF MediaBox).

        Interactive print preview uses the unnamed @page for default orientation; headless
        may also honor named pages, so we prove the inject against a portrait-only baseline.
        """
        chrome = _chrome_bin()
        if not chrome:
            self.skipTest('Chrome/Chromium not available')

        # Same cascade as the app: style/sheet @page{size:A4} then JS inject landscape.
        html_portrait = (
            '<!doctype html><html><head><meta charset="utf-8">'
            '<style>@page{size:A4;margin:12mm}body{margin:0}.box{font:12pt sans-serif}</style>'
            '</head><body><div class="box">portrait baseline</div></body></html>'
        )
        html_forced = (
            '<!doctype html><html><head><meta charset="utf-8">'
            '<style>@page{size:A4;margin:12mm}body{margin:0}.box{font:12pt sans-serif}</style>'
            '<style id="shift-print-page-css">@page{size:A4 landscape;margin:8mm}</style>'
            '</head><body class="printing-shift"><div class="box">shift forced</div></body></html>'
        )
        js = (ROOT / 'kintai-shift.js').read_text(encoding='utf-8')
        self.assertIn("@page{size:A4 landscape;margin:8mm}", js)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            portrait_html = tmp_path / 'portrait.html'
            forced_html = tmp_path / 'forced.html'
            portrait_pdf = tmp_path / 'portrait.pdf'
            forced_pdf = tmp_path / 'forced.pdf'
            portrait_html.write_text(html_portrait, encoding='utf-8')
            forced_html.write_text(html_forced, encoding='utf-8')

            def print_pdf(src: Path, dest: Path) -> None:
                cmd = [
                    chrome,
                    '--headless=new',
                    '--disable-gpu',
                    '--no-pdf-header-footer',
                    f'--print-to-pdf={dest}',
                    src.resolve().as_uri(),
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, msg=r.stderr or r.stdout)
                self.assertTrue(dest.is_file() and dest.stat().st_size > 100)

            print_pdf(portrait_html, portrait_pdf)
            print_pdf(forced_html, forced_pdf)

            pw, ph = _pdf_mediabox(portrait_pdf)
            fw, fh = _pdf_mediabox(forced_pdf)
            self.assertLess(pw, ph, msg=f'baseline should be portrait, got {pw}x{ph}')
            self.assertGreater(fw, fh, msg=f'forced PDF should be landscape, got {fw}x{fh}')
            self.assertAlmostEqual(fw / fh, 297 / 210, delta=0.05)

    def test_shift_print_name_one_line_fixture_metrics(self):
        """長い氏名（齊藤 遼太 等）が印刷CSSで1行のまま（高さ ≈ 1行）。"""
        import html as html_lib
        import json

        chrome = _chrome_bin()
        if not chrome:
            self.skipTest('Chrome/Chromium not available')

        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        # シフト表印刷ブロックだけ取り出し（共通 body>*{display:none} は含めない）
        marker = '/* シフト表印刷:'
        start = css.find(marker)
        self.assertGreater(start, 0)
        media = css.find('@media print{', start)
        self.assertGreater(media, 0)
        i = media + len('@media print{')
        depth = 1
        while i < len(css) and depth:
            if css[i] == '{':
                depth += 1
            elif css[i] == '}':
                depth -= 1
            i += 1
        print_css = css[media + len('@media print{'): i - 1]
        # @page は計測に不要。display:none 系も落とす
        print_css = re.sub(r'@page\s+shift-sheet\s*\{[^}]*\}', '', print_css)
        print_css = re.sub(
            r'body\.printing-shift[^\{]*\{[^}]*display:\s*none!important;?[^}]*\}',
            '',
            print_css,
        )

        names = ['齊藤 遼太', '斉藤 遼太', '今城 卓也', '長谷川 恵美子', '山田 太郎']
        rows = ''.join(
            f'<tr><th class="shift-name"><div class="shift-name-edit">'
            f'<span class="shift-name-print">{n}</span></div></th>'
            + ''.join('<td class="shift-cell">×</td>' for _ in range(31))
            + '</tr>'
            for n in names
        )
        days = ''.join(
            f'<th class="shift-day"><span class="shift-daynum">{d}</span></th>'
            for d in range(1, 32)
        )
        cols = '<colgroup><col class="shift-col-name">' + '<col class="shift-col-day">' * 31 + '</colgroup>'
        html = f'''<!doctype html><html><head><meta charset="utf-8">
<style>
{print_css}
html,body{{margin:0;background:#fff}}
.shift-print-banner{{display:table-row!important}}
.shift-grid-wrap{{width:281mm}}
.shift-grid{{width:100%;table-layout:fixed;border-collapse:collapse;font-size:8.5pt;
  font-family:"BIZ UDPGothic","Yu Gothic UI","Yu Gothic",Meiryo,sans-serif}}
</style></head>
<body class="printing-shift print-shift-color">
<div class="shift-grid-wrap"><table class="shift-grid">
{cols}
<thead><tr class="shift-print-banner"><th colspan="32"><div class="shift-print-head">
<span class="shift-print-title-text">2026年 10月 マーク シフト表</span>
<span class="shift-print-legend">空欄：出勤　×：休み　時刻：退勤予定</span>
</div></th></tr>
<tr><th class="shift-corner">氏名</th>{days}</tr></thead>
<tbody>{rows}</tbody>
</table></div>
<script>
const marks = [...document.querySelectorAll('.shift-name-print')].map((el) => {{
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  const th = el.closest('.shift-name');
  const thr = th.getBoundingClientRect();
  return {{
    text: el.textContent,
    h: r.height,
    w: r.width,
    colW: thr.width,
    whiteSpace: cs.whiteSpace,
    wordBreak: cs.wordBreak,
  }};
}});
const title = document.querySelector('.shift-print-title-text');
const tcs = getComputedStyle(title);
const out = {{
  marks,
  titleFontSize: tcs.fontSize,
  titleFontWeight: tcs.fontWeight,
  legendFontSize: getComputedStyle(document.querySelector('.shift-print-legend')).fontSize,
}};
document.body.setAttribute('data-metrics', JSON.stringify(out));
</script>
</body></html>'''

        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / 'name-one-line.html'
            page.write_text(html, encoding='utf-8')
            cmd = [
                chrome,
                '--headless=new',
                '--disable-gpu',
                '--virtual-time-budget=3000',
                '--dump-dom',
                page.resolve().as_uri(),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, msg=r.stderr or r.stdout)
            mm = re.search(r'data-metrics="([^"]+)"', r.stdout)
            self.assertIsNotNone(mm, msg='metrics missing from dump-dom')
            metrics = json.loads(html_lib.unescape(mm.group(1)))

        for mark in metrics['marks']:
            self.assertEqual(mark['whiteSpace'], 'nowrap', msg=mark)
            self.assertNotEqual(mark['wordBreak'], 'break-all', msg=mark)
            # 1行: 折り返しなら高さがおよそ2倍になる
            self.assertLess(mark['h'], 28, msg=f'name wrapped? {mark}')
            self.assertGreaterEqual(mark['colW'] + 0.5, mark['w'], msg=mark)
            self.assertGreaterEqual(mark['colW'], 70, msg=f'name col too narrow: {mark}')

        def px(v: str) -> float:
            return float(v.replace('px', ''))

        self.assertGreater(px(metrics['titleFontSize']), px(metrics['legendFontSize']) * 1.5)
        self.assertAlmostEqual(px(metrics['titleFontSize']), 24.0, delta=1.0)
        self.assertIn(str(metrics['titleFontWeight']), ('700', 'bold'))


class ShiftWorkMemoTest(unittest.TestCase):
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

    def test_memo_bind_prefers_person_id(self):
        self.assertEqual(
            kintai_shift.memo_bind_key({'personId': 7, 'key': 'regular:001'}),
            'pid:7',
        )
        self.assertEqual(
            kintai_shift.memo_bind_key({'personId': None, 'key': 'shift:abc'}),
            'key:shift:abc',
        )

    def test_work_memo_persists_across_months_by_person_id(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        p = next(x for x in sheet['people'] if x['key'] == 'regular:001')
        self.assertIsInstance(p['personId'], int)
        saved = kintai_shift.save_work_memos(self.c, self.user, {
            'memos': [{'key': p['key'], 'personId': p['personId'], 'memo': '週5'}],
        })
        self.assertEqual(saved['workMemos']['regular:001'], '週5')
        # 月シートは未作成のまま
        nov = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertFalse(nov['exists'])
        self.assertEqual(nov['workMemos'].get('regular:001'), '週5')
        # 別月でも同じ personId で見える
        dec = kintai_shift.get_shift_sheet(self.c, 2026, 12)
        self.assertEqual(dec['workMemos'].get('regular:001'), '週5')
        # 氏名を変えてもメモは残る
        people = [{**x, 'displayName': '山田（別名）' if x['key'] == 'regular:001' else x['displayName']}
                  for x in sheet['people']]
        code, _ = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0, 'people': people, 'cells': {},
        })
        self.assertEqual(code, 200)
        again = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        self.assertEqual(again['workMemos'].get('regular:001'), '週5')
        # 月保存でメモテーブル件数が消えない
        n = self.c.execute('SELECT COUNT(*) AS n FROM kintai_shift_work_memos').fetchone()['n']
        self.assertEqual(n, 1)

    def test_save_shift_does_not_write_memos(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        kintai_shift.save_work_memos(self.c, self.user, {
            'memos': [{'key': 'part:002', 'personId': sheet['people'][1]['personId'], 'memo': '午前'}],
        })
        # ペイロードにメモを混ぜても月保存は無視（APIは cells/people のみ）
        code, saved = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': sheet['people'],
            'cells': {'regular:001': {'3': {'kind': 'off', 'text': '×'}}},
            'workMemos': {'regular:001': 'should-not-apply'},
        })
        self.assertEqual(code, 200)
        self.assertEqual(saved['cells']['regular:001']['3']['kind'], 'off')
        self.assertEqual(saved['workMemos'].get('part:002'), '午前')
        self.assertNotIn('regular:001', saved['workMemos'])

    def test_existing_time_until_not_bulk_destroyed(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        code, saved = kintai_shift.save_shift_sheet(self.c, self.user, {
            'year': 2026, 'month': 11, 'version': 0,
            'people': sheet['people'],
            'cells': {
                'regular:001': {
                    '5': {'kind': 'time', 'text': '15:00まで'},
                    '6': {'kind': 'time', 'text': '早退予定'},
                },
            },
        })
        self.assertEqual(code, 200)
        self.assertEqual(saved['cells']['regular:001']['5']['text'], '15:00まで')
        self.assertEqual(saved['cells']['regular:001']['6']['text'], '早退予定')
        # 表示正規化は JS。サーバは保存値を維持
        js = (ROOT / 'kintai-shift.js').read_text(encoding='utf-8')
        self.assertIn(r'^(\d{1,2}:\d{2})\s*まで$', js)

    def test_employment_on_people_not_duplicated_field(self):
        sheet = kintai_shift.get_shift_sheet(self.c, 2026, 11)
        by_key = {p['key']: p for p in sheet['people']}
        self.assertEqual(by_key['regular:001']['employment'], 'regular')
        self.assertEqual(by_key['part:002']['employment'], 'part')
        # 区分は在籍 identity から。別名フィールドを増やしていない
        self.assertNotIn('employmentColor', by_key['regular:001'])
        self.assertNotIn('kintaiKubun', by_key['regular:001'])


if __name__ == '__main__':
    unittest.main()
