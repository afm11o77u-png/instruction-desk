"""勤怠管理（マーク）: 権限・集計・取込・037非混同・失敗時保持。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import kintai

ROOT = Path(__file__).resolve().parents[1]


class KintaiAggTest(unittest.TestCase):
    def test_format_hm_over_24h(self):
        self.assertEqual(kintai.format_hm(160 * 60 + 30), '160:30')
        self.assertEqual(kintai.format_hm(0), '0:00')

    def test_paid_leave_half_and_full(self):
        self.assertEqual(kintai.parse_paid_leave('1.0有休'), 1.0)
        self.assertEqual(kintai.parse_paid_leave('0.5有休'), 0.5)
        self.assertIsNone(kintai.parse_paid_leave(''))
        self.assertTrue(kintai.is_paid_leave_cell('1.0有休'))

    def test_aggregate_rules(self):
        # 2026-02: 28日. as_of=2026-02-20 → 21〜28は将来扱いで休日にしない
        days = []
        for d in range(1, 29):
            if d in (1, 2, 3, 4, 5):
                days.append({'day': d, 'start': '9:00', 'leave': '', 'ordinary_minutes': 480, 'overtime_minutes': 30})
            elif d == 6:
                days.append({'day': d, 'start': '', 'leave': '1.0有休', 'ordinary_minutes': 0, 'overtime_minutes': 0})
            elif d == 7:
                days.append({'day': d, 'start': '9:00', 'leave': '0.5有休', 'ordinary_minutes': 240, 'overtime_minutes': 0})
            else:
                days.append({'day': d, 'start': '', 'leave': '', 'ordinary_minutes': 0, 'overtime_minutes': 0})
        blob = kintai.build_fixture_xlsx({(2026, 2): days})
        sheets = kintai.read_xlsx_grid(blob)
        grid = sheets['2026-02']
        agg = kintai.aggregate_month_from_grid(
            grid, 2026, 2, kintai.FIXTURE_LAYOUT, as_of=date(2026, 2, 20)
        )
        self.assertEqual(agg['work_days'], 6.0)  # 1-5 + 7
        self.assertEqual(agg['paid_leave_days'], 1.5)  # 1.0 + 0.5
        # 休日: 空欄かつ有休でない、かつ as_of 以前 → 8..20 = 13日（6は有休なので除外）
        self.assertEqual(agg['holiday_days'], 13.0)
        self.assertEqual(agg['ordinary_minutes'], 5 * 480 + 240)
        self.assertEqual(agg['overtime_minutes'], 5 * 30)
        self.assertEqual(agg['total_minutes'], agg['ordinary_minutes'] + agg['overtime_minutes'])

    def test_tiny_negative_overtime_becomes_zero(self):
        self.assertEqual(kintai._as_minutes(-0.01, '残業'), 0)

    def test_large_negative_overtime_errors(self):
        with self.assertRaises(ValueError):
            kintai._as_minutes(-5, '残業')

    def test_missing_formula_cache_errors(self):
        grid = {(2, 1): 1, (2, 2): '9:00', (2, 3): '', (2, 4): {'__missing_cache__': True, 'formula': 'A1'}, (2, 5): 0}
        with self.assertRaises(ValueError) as ctx:
            kintai.aggregate_month_from_grid(grid, 2026, 2, kintai.FIXTURE_LAYOUT, as_of=date(2026, 2, 28))
        self.assertIn('計算結果がありません', str(ctx.exception))


class KintaiDbTest(unittest.TestCase):
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
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_seed_people_keeps_037_distinct(self):
        rows = list(self.c.execute(
            "SELECT employee_code, employment, display_name FROM kintai_people WHERE employee_code='037' ORDER BY employment"
        ))
        self.assertEqual(len(rows), 2)
        emps = {r['employment'] for r in rows}
        self.assertEqual(emps, {'contract', 'part'})
        names = {r['display_name'] for r in rows}
        self.assertIn('須永 結香', names)
        self.assertIn('南田 久美子', names)

    def test_mark_kintai_default_off(self):
        cols = {r['name'] for r in self.c.execute('PRAGMA table_info(users)')}
        self.assertIn('mark_kintai', cols)
        self.c.execute(
            "INSERT INTO users(login,name,password,role,manage,overview,active,must_change,version,mark_kintai) "
            "VALUES('a@ex.com','A','x','sales',0,0,1,0,1,0)"
        )
        u = self.c.execute('SELECT * FROM users WHERE login=?', ('a@ex.com',)).fetchone()
        self.assertFalse(kintai.can_use_kintai(u))
        owner = dict(id=1, role='owner', mark_kintai=0)
        self.assertTrue(kintai.can_use_kintai(owner))

    def test_import_upsert_and_failure_keeps_previous(self):
        fixtures = self.data / 'kintai-fixtures'
        fixtures.mkdir()
        # 2名分（037契約と037パートを別ファイル）
        people = list(self.c.execute(
            "SELECT * FROM kintai_people WHERE employee_code='037' ORDER BY employment"
        ))
        self.assertEqual(len(people), 2)
        months = kintai.fiscal_months(2026, 2)

        def month_days(work=True):
            out = []
            for y, m in months:
                days = []
                last = 28 if m == 2 else 30 if m in (4, 6, 9, 11) else 31
                if m == 2:
                    last = 28
                for d in range(1, last + 1):
                    if work and d <= 10:
                        days.append({'day': d, 'start': '9:00', 'leave': '', 'ordinary_minutes': 480, 'overtime_minutes': 60})
                    else:
                        days.append({'day': d, 'start': '', 'leave': '', 'ordinary_minutes': 0, 'overtime_minutes': 0})
                out[(y, m)] = days  # type: ignore
            return out

        # build properly
        def build_days(work_days_count=10):
            data = {}
            for y, m in months:
                import calendar
                last = calendar.monthrange(y, m)[1]
                days = []
                for d in range(1, last + 1):
                    if d <= work_days_count:
                        days.append({'day': d, 'start': '9:00', 'leave': '', 'ordinary_minutes': 480, 'overtime_minutes': 60})
                    else:
                        days.append({'day': d, 'start': '', 'leave': '', 'ordinary_minutes': 0, 'overtime_minutes': 0})
                data[(y, m)] = days
            return data

        files = {}
        for p in people:
            name = f"{p['id']}_{p['employment']}.xlsx"
            (fixtures / name).write_bytes(kintai.build_fixture_xlsx(build_days(10)))
            self.c.execute('UPDATE kintai_people SET source_file=? WHERE id=?', (name, p['id']))
            files[p['id']] = name
        self.c.commit()

        # local settings: empty share root → fixtures dir。
        # 契約・パートは既定が monthdot-pending なので、検証用に個別で fixture-v1 を指定。
        people_layouts = {str(p['id']): 'fixture-v1' for p in people}
        (self.data / 'kintai-local.json').write_text(
            json.dumps(
                {'shareRoot': '', 'layoutId': 'fixture-v1', 'peopleFiles': {}, 'peopleLayouts': people_layouts},
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
        user = {'id': 1, 'name': '主', 'login': 'o@ex.com', 'role': 'owner', 'mark_kintai': 1}
        summary = kintai.import_all(self.c, user, 2026, as_of=date(2027, 1, 31))
        self.c.commit()
        self.assertEqual(sum(1 for p in summary['people'] if p['personId'] in files and p['status'] == '成功'), 2)

        # 037 契約の2月
        contract = next(p for p in people if p['employment'] == 'contract')
        part = next(p for p in people if p['employment'] == 'part')
        c_row = self.c.execute(
            'SELECT * FROM kintai_monthly WHERE person_id=? AND year=2026 AND month=2', (contract['id'],)
        ).fetchone()
        p_row = self.c.execute(
            'SELECT * FROM kintai_monthly WHERE person_id=? AND year=2026 AND month=2', (part['id'],)
        ).fetchone()
        self.assertIsNotNone(c_row)
        self.assertIsNotNone(p_row)
        self.assertEqual(c_row['work_days'], 10)
        self.assertEqual(p_row['work_days'], 10)
        self.assertNotEqual(contract['id'], part['id'])

        prev_ordinary = c_row['ordinary_minutes']

        # 壊したファイルで再取込 → 前回保持
        (fixtures / files[contract['id']]).write_bytes(b'not-xlsx')
        summary2 = kintai.import_all(self.c, user, 2026, as_of=date(2027, 1, 31))
        self.c.commit()
        failed = next(p for p in summary2['people'] if p['personId'] == contract['id'])
        self.assertEqual(failed['status'], 'エラー')
        kept = self.c.execute(
            'SELECT * FROM kintai_monthly WHERE person_id=? AND year=2026 AND month=2', (contract['id'],)
        ).fetchone()
        self.assertEqual(kept['ordinary_minutes'], prev_ordinary)
        self.assertEqual(kept['status'], 'ok')

        # 再取込成功で重複行が増えない
        (fixtures / files[contract['id']]).write_bytes(kintai.build_fixture_xlsx(build_days(8)))
        kintai.import_all(self.c, user, 2026, as_of=date(2027, 1, 31))
        self.c.commit()
        n = self.c.execute(
            'SELECT COUNT(*) FROM kintai_monthly WHERE person_id=? AND year=2026 AND month=2',
            (contract['id'],),
        ).fetchone()[0]
        self.assertEqual(n, 1)
        updated = self.c.execute(
            'SELECT work_days FROM kintai_monthly WHERE person_id=? AND year=2026 AND month=2',
            (contract['id'],),
        ).fetchone()
        self.assertEqual(updated['work_days'], 8)

    def test_api_denied_without_flag(self):
        class H:
            user = {'id': 2, 'role': 'sales', 'mark_kintai': 0, 'manage': 0}
            path = '/api/kintai'
        code, body = kintai.handle_get(H(), self.c, '/api/kintai')
        self.assertEqual(code, 403)

    def test_source_file_not_mutated(self):
        fixtures = self.data / 'kintai-fixtures'
        fixtures.mkdir()
        p = self.c.execute('SELECT * FROM kintai_people ORDER BY id LIMIT 1').fetchone()
        name = 'readonly.xlsx'
        path = fixtures / name
        original = kintai.build_fixture_xlsx({
            ym: [
                {'day': d, 'start': '9:00' if d == 1 else '', 'leave': '', 'ordinary_minutes': 480 if d == 1 else 0, 'overtime_minutes': 0}
                for d in range(1, (__import__('calendar').monthrange(*ym)[1] + 1))
            ]
            for ym in kintai.fiscal_months(2026, 2)
        })
        path.write_bytes(original)
        before = path.read_bytes()
        mtime_before = path.stat().st_mtime
        self.c.execute('UPDATE kintai_people SET source_file=? WHERE id=?', (name, p['id']))
        self.c.commit()
        (self.data / 'kintai-local.json').write_text(
            json.dumps({'shareRoot': '', 'layoutId': 'fixture-v1'}, ensure_ascii=False), encoding='utf-8'
        )
        kintai.import_all(self.c, {'id': 1, 'name': '主', 'login': 'o@ex.com'}, 2026, as_of=date(2027, 1, 31))
        after = path.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(mtime_before, path.stat().st_mtime)


class KintaiNavWireTest(unittest.TestCase):
    def test_files_and_menu(self):
        index = (ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('data-view="kintai"', index)
        self.assertIn('勤怠管理（マーク）', index)
        self.assertIn('kintai.js', index)
        self.assertIn('id="kintaidesk"', index)
        account = (ROOT / 'account.js').read_text(encoding='utf-8')
        self.assertIn('mark_kintai', account)
        self.assertIn('kintai-open', account)
        auth = (ROOT / 'auth.py').read_text(encoding='utf-8')
        self.assertIn('mark_kintai', auth)
        gitignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('kintai-local.json', gitignore)
        self.assertIn('kintai-fixtures', gitignore)


if __name__ == '__main__':
    unittest.main()
