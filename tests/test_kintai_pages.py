"""出勤簿の表示・登録・月別集計の画面分割と、全員の時間合計。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import kintai

ROOT = Path(__file__).resolve().parents[1]


def _slice(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


class KintaiPageSplitTest(unittest.TestCase):
    def test_nav_order(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        nav = _slice(html, 'id="kintainav"', 'id="sidebarfooter"')
        rows = []
        start = 0
        while True:
            i = nav.find('class="kintai-entryrow"', start)
            if i < 0:
                break
            end = nav.index('</div>', i)
            rows.append(nav[i:end])
            start = end
        self.assertEqual(len(rows), 2)
        self.assertIn('>出勤簿<', rows[0])
        self.assertIn('>登録<', rows[0])
        self.assertNotIn('>日別<', rows[0])
        self.assertIn('>日別<', rows[1])
        self.assertIn('>月別集計<', rows[1])
        self.assertNotIn('>登録<', rows[1])
        self.assertLess(nav.index('>月別集計<'), nav.index('>シフト表<'))
        self.assertLess(nav.index('>シフト表<'), nav.index('>備品棚卸<'))
        self.assertLess(nav.index('>備品棚卸<'), nav.index('>圧着カウンター<'))
        self.assertIn('data-kintai-section="attendance"', nav)
        self.assertIn('data-kintai-section="register"', nav)
        self.assertIn('data-kintai-section="summary"', nav)
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        rule_at = css.index('#kintainav .kintai-entryrow{')
        rule = css[rule_at:css.index('}', rule_at)]
        self.assertIn('flex-flow:row nowrap', rule)
        child_at = css.index('#kintainav .kintai-entryrow .nav{')
        child = css[child_at:css.index('}', child_at)]
        self.assertIn('white-space:nowrap', child)

    def test_panels_keep_attendance_and_split_import(self):
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        att = _slice(html, 'id="kintaiattendance"', 'id="kintaidaily"')
        daily = _slice(html, 'id="kintaidaily"', 'id="kintairegister"')
        reg = _slice(html, 'id="kintairegister"', 'id="kintaisummary"')
        summary = _slice(html, 'id="kintaisummary"', 'id="kintaishift"')
        self.assertIn('id="kintaitable" class="kintai-tablewrap"', att)
        self.assertIn('id="kintaiyear"', att)
        self.assertIn('id="kintaiperson"', att)
        self.assertIn('id="kintairefresh"', att)
        self.assertNotIn('出勤簿から更新', att)
        self.assertNotIn('読取元設定', att)
        self.assertNotIn('kintaiimportresult', att)
        self.assertIn('id="kintaidaily" hidden', daily[:40])
        self.assertIn('id="kintaidailymonth"', daily)
        self.assertIn('id="kintaidailyperson"', daily)
        self.assertNotIn('出勤簿から更新', daily)
        self.assertTrue(reg.startswith('id="kintairegister" hidden') or 'id="kintairegister" hidden' in reg[:40])
        self.assertIn('出勤簿から更新', reg)
        self.assertIn('id="kintaiimport"', reg)
        self.assertIn('kintaihow', reg)
        self.assertIn('id="kintaiimportresult"', reg)
        self.assertIn('kintaipathsimport', reg)
        self.assertIn('kintaifileperson', reg)
        self.assertIn('id="kintaisummary" hidden', summary[:40])
        self.assertIn('id="kintaisummarytable"', summary)
        self.assertNotIn('出勤簿から更新', summary)
        self.assertNotIn('id="kintaitable"', summary)

    def test_routes_and_same_permission(self):
        place = (ROOT / 'placement.js').read_text(encoding='utf-8')
        self.assertIn("if(section==='crimp')return 'crimp';", place)
        self.assertIn("if(section==='register')return 'register';", place)
        self.assertIn("if(section==='summary')return 'summary';", place)
        self.assertIn("if(section==='daily')return 'daily';", place)
        self.assertIn("['shift','crimp'].includes(b.dataset.kintaiSection)", place)
        self.assertIn("else if(cur==='/kintai/register'||cur==='/kintai/summary'||cur==='/kintai/daily')path='/';", place)
        self.assertIn("path==='/kintai/register'", place)
        self.assertIn("path==='/kintai/summary'", place)
        self.assertIn("path==='/kintai/daily'", place)
        # 出勤簿は新しいパスへ移さない。登録・月別集計だけ新しいパス。
        self.assertNotIn("section==='attendance')path=", place)
        account = (ROOT / 'account.js').read_text(encoding='utf-8')
        self.assertIn('openKintaiFromLocation', account)
        self.assertIn(
            "role==='owner'||!!currentUser.manage||!!currentUser.overview||!!currentUser.mark_kintai",
            account,
        )
        server = (ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("'/kintai/register'", server)
        self.assertIn("'/kintai/summary'", server)
        self.assertIn("'/kintai/attendance'", server)
        self.assertIn("'/kintai/daily'", server)
        src = (ROOT / 'kintai.py').read_text(encoding='utf-8')
        self.assertIn("path == '/api/kintai/month-summary'", src)
        doc = (ROOT / '勤怠管理マークの使い方.md').read_text(encoding='utf-8')
        self.assertIn('/kintai/register', doc)
        self.assertIn('/kintai/summary', doc)
        self.assertIn('/kintai/daily', doc)
        self.assertIn('月別集計', doc)

    def test_sheet_columns_match_excel_and_skip_unfixed_rules(self):
        js = (ROOT / 'kintai.js').read_text(encoding='utf-8')
        person = _slice(js, 'const PERSON_COLUMNS', 'const SUMMARY_COLUMNS')
        summary = _slice(js, 'const SUMMARY_COLUMNS', 'function $(')
        person_cols = [
            '月', '出勤日数', '休日数', '累計休日数', '年間休日残数', '有給取得日数', '有給累計取得日数', '有給残数',
            '有給休暇起算日', '普通勤務時間', '残業時間', '勤務時間計', '通常勤務給与', '残業給',
            '有給付与日数', '有給失効日数', 'その他休暇等', '備考',
        ]
        summary_cols = [
            '月', '普通勤務時間（時間:分）', '残業時間（時間:分）', '勤務時間計（時間:分）',
            '通常勤務給与（円）', '残業給与（円）', '給与計（円）',
        ]
        pos = 0
        for name in person_cols:
            i = person.index(f"'{name}'", pos)
            self.assertGreater(i, pos, name)
            pos = i
        pos = 0
        for name in summary_cols:
            i = summary.index(f"'{name}'", pos)
            self.assertGreater(i, pos, name)
            pos = i
        self.assertIn("summaryRow('期間合計'", js)
        self.assertIn('/api/kintai/month-summary', js)
        self.assertIn('blankCells', js)
        render = _slice(js, 'function personSheetHtml', 'function paintKintai')
        self.assertNotIn('累計', render)
        self.assertIn('blankCells(2)', render)
        self.assertIn('blankCells(6)', render)
        self.assertNotIn('salary', js.lower())


class MonthHourSummaryTest(unittest.TestCase):
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
        people = list(self.c.execute('SELECT id FROM kintai_people WHERE active=1 ORDER BY id LIMIT 2'))
        self.a = people[0]['id']
        self.b = people[1]['id']

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

    def test_sums_everyone_and_period_without_pay(self):
        self.c.execute('UPDATE kintai_people SET retired=1 WHERE id=?', (self.a,))
        # 2026/02 はスクリーンショットの 773:31 / 15:00 / 788:31
        self.put(self.a, 2026, 2, 773 * 60 + 31, 15 * 60)
        # 2026/03 は二人分で 936:30 / 21:42 / 958:12
        self.put(self.a, 2026, 3, 400 * 60, 10 * 60)
        self.put(self.b, 2026, 3, 536 * 60 + 30, 11 * 60 + 42)
        self.c.execute(
            "INSERT INTO kintai_people(employee_code,employment,display_name,source_file,active,sort_order,retired) "
            "VALUES('zzz','regular','除外','',0,999,0)"
        )
        inactive = self.c.execute("SELECT id FROM kintai_people WHERE employee_code='zzz'").fetchone()['id']
        self.put(inactive, 2026, 2, 1000, 1000)
        self.c.commit()
        data = kintai.month_hour_summary(self.c, 2026)
        self.assertEqual(len(data['months']), 12)
        feb, mar, apr = data['months'][0], data['months'][1], data['months'][2]
        self.assertEqual(feb['key'], '2026/02')
        self.assertEqual((feb['ordinaryHm'], feb['overtimeHm'], feb['totalHm']), ('773:31', '15:00', '788:31'))
        self.assertEqual(mar['key'], '2026/03')
        self.assertEqual((mar['ordinaryHm'], mar['overtimeHm'], mar['totalHm']), ('936:30', '21:42', '958:12'))
        self.assertEqual(apr['key'], '2026/04')
        self.assertEqual((apr['ordinaryHm'], apr['overtimeHm'], apr['totalHm']), ('', '', ''))
        self.assertEqual(
            (data['period']['ordinaryHm'], data['period']['overtimeHm'], data['period']['totalHm']),
            ('1710:01', '36:42', '1746:43'),
        )
        self.assertNotIn('salary', data)
        self.assertNotIn('payYen', feb)

    def test_one_sided_minutes_and_empty_year(self):
        self.put(self.a, 2026, 5, None, 90)
        self.c.commit()
        data = kintai.month_hour_summary(self.c, 2026)
        may = data['months'][3]
        self.assertEqual(may['key'], '2026/05')
        self.assertEqual((may['ordinaryHm'], may['overtimeHm'], may['totalHm']), ('0:00', '1:30', '1:30'))
        self.assertEqual(data['period']['totalHm'], '1:30')
        empty = kintai.month_hour_summary(self.c, 2025)
        self.assertTrue(all(m['totalHm'] == '' for m in empty['months']))
        self.assertEqual(empty['period']['totalHm'], '')

    def test_api_permission_matches_attendance(self):
        class Denied:
            user = {'id': 2, 'role': 'sales', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/month-summary?year=2026'

        class Allowed:
            user = {'id': 1, 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/month-summary?year=2026'

        self.assertEqual(kintai.handle_get(Denied(), self.c, '/api/kintai/month-summary')[0], 403)
        code, body = kintai.handle_get(Allowed(), self.c, '/api/kintai/month-summary')
        self.assertEqual(code, 200)
        self.assertEqual(len(body['months']), 12)

        class Bad:
            user = Allowed.user
            path = '/api/kintai/month-summary?year=1999'

        with self.assertRaises(ValueError):
            kintai.handle_get(Bad(), self.c, '/api/kintai/month-summary')


if __name__ == '__main__':
    unittest.main()
