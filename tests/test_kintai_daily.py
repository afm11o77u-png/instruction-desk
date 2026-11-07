"""日別出勤簿: 保存パスの月シートを読取専用で返す。月次集計はしない。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import kintai

ROOT = Path(__file__).resolve().parents[1]


def _col_letters(n: int) -> str:
    s = ''
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xlsx(sheets: dict[str, list[tuple[int, int, object]]]) -> bytes:
    """シート名 → [(row, col, value)] の最小 xlsx。値は数値か文字列。"""
    names = list(sheets)
    out = BytesIO()
    ss = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'

    def cell(col, row, value):
        ref = f'{_col_letters(col)}{row}'
        if isinstance(value, tuple) and value and value[0] == 'formula':
            return f'<c r="{ref}"><f>{escape(str(value[1]))}</f></c>'
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        text = escape(str(value if value is not None else ''))
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + ''.join(
                f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for i in range(1, len(names) + 1)
            )
            + '</Types>',
        )
        z.writestr(
            '_rels/.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        z.writestr(
            'xl/_rels/workbook.xml.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + ''.join(
                f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(names) + 1)
            )
            + '</Relationships>',
        )
        tags = ''.join(
            f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, name in enumerate(names, 1)
        )
        z.writestr(
            'xl/workbook.xml',
            f'<workbook xmlns="{ss}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{tags}</sheets></workbook>',
        )
        for i, name in enumerate(names, 1):
            rows = {}
            for row, col, value in sheets[name]:
                rows.setdefault(row, []).append(cell(col, row, value))
            body = ''.join(f'<row r="{row}">{"".join(rows[row])}</row>' for row in sorted(rows))
            z.writestr(
                f'xl/worksheets/sheet{i}.xml',
                f'<worksheet xmlns="{ss}"><sheetData>{body}</sheetData></worksheet>',
            )
    return out.getvalue()


def _serial(day: date) -> int:
    return (day - date(1899, 12, 30)).days


class DailySheetReadTest(unittest.TestCase):
    def test_fixture_days_are_raw_cells_not_monthly_totals(self):
        blob = kintai.build_fixture_xlsx({
            (2026, 2): [
                {'day': 1, 'start': '9:00', 'leave': '', 'ordinary_minutes': 480, 'overtime_minutes': 30},
                {'day': 2, 'start': '', 'leave': '1.0有休', 'ordinary_minutes': 0, 'overtime_minutes': 0},
            ],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.FIXTURE_LAYOUT)
        self.assertEqual(view['sheetName'], '2026-02')
        by_date = {row['date']: row for row in view['rows']}
        self.assertEqual(by_date['2026/02/01']['start'], '9:00')
        self.assertEqual(by_date['2026/02/01']['leave'], '')
        self.assertEqual(by_date['2026/02/01']['ordinary'], '480')
        self.assertEqual(by_date['2026/02/01']['overtime'], '30')
        self.assertEqual(by_date['2026/02/02']['leave'], '1.0有休')
        self.assertNotIn('workDays', by_date['2026/02/01'])
        labels = [c['label'] for c in view['columns']]
        self.assertEqual(labels[:3], ['日', '始業', '休み'])

    def test_seishain_uses_calendar_date_and_month_sheet_cells(self):
        cal = '年間ｶﾚﾝﾀﾞｰ'
        blob = _xlsx({
            cal: [
                (3, 2, _serial(date(2026, 2, 1))),
                (4, 2, _serial(date(2026, 3, 1))),
            ],
            '26.2': [
                (4, 4, '休み'),
                (4, 5, '始業時間'),
                (5, 1, _serial(date(2025, 2, 1))),
                (5, 4, ''),
                (5, 5, 0.375),
                (6, 4, '1.0有休'),
                (6, 5, ''),
            ],
            '集計': [],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        self.assertEqual(view['sheetName'], '26.2')
        self.assertEqual([c['label'] for c in view['columns'][:3]], ['日付', '休み', '始業時間'])
        self.assertEqual(view['rows'][0]['date'], '2026/02/01')
        self.assertEqual(view['rows'][0]['start'], '9:00')
        self.assertNotIn('2025', view['rows'][0]['date'])
        self.assertEqual(view['rows'][1]['date'], '2026/03/01')
        self.assertEqual(view['rows'][1]['leave'], '1.0有休')
        self.assertEqual(view['rows'][1]['start'], '')

    def test_monthdot_and_part_show_sheet_dates_without_converting_paid_hours(self):
        contract = _xlsx({
            '2026.2': [
                (5, 1, _serial(date(2026, 2, 1))),
                (5, 4, ''),
                (5, 5, '8:30'),
                (6, 1, _serial(date(2026, 2, 2))),
                (6, 4, '0.5有休'),
                (6, 5, ''),
            ],
        })
        view = kintai.daily_rows_from_workbook(contract, 2026, 2, kintai.MONTHDOT_2026_CONTRACT_LAYOUT)
        self.assertEqual(view['rows'][0]['date'], '2026/02/01')
        self.assertEqual(view['rows'][0]['start'], '8:30')
        self.assertEqual(view['rows'][1]['leave'], '0.5有休')
        self.assertEqual([c['key'] for c in view['columns']], [
            'date', 'leave', 'start', 'breakTime', 'end', 'calendarHours',
            'actual', 'earlyNight', 'dayOvertime', 'nightOvertime', 'total',
        ])
        # 見出しが無い時間列は、隣の数値を流用しない
        self.assertEqual(view['rows'][0]['calendarHours'], '（列なし）')
        self.assertEqual(view['rows'][0]['total'], '（列なし）')

        part = _xlsx({
            '2026.2': [
                (5, 1, _serial(date(2026, 2, 1))),
                (5, 4, ''),
                (5, 5, 0.5),
                (5, 8, 0.375),
            ],
        })
        part_view = kintai.daily_rows_from_workbook(part, 2026, 2, kintai.MONTHDOT_2026_PART_LAYOUT)
        row = part_view['rows'][0]
        self.assertEqual(row['start'], '9:00')
        self.assertNotIn('paidTime', [c['key'] for c in part_view['columns']])
        self.assertNotIn('0.5', row.values())

    def test_headed_columns_follow_excel_order_and_keep_blanks(self):
        """表示名は11個だけ。合計は実労働時間計の保存値で、実労働時間との足し算ではない。"""
        break_serial = 70 / (24 * 60)  # 1:10
        blob = _xlsx({
            '年間ｶﾚﾝﾀﾞｰ': [
                (3, 2, _serial(date(2026, 2, 2))),
            ],
            '26.2': [
                (3, 3, '曜日'),
                (3, 4, '休みヤス'),
                (3, 5, '始業時間（00：00）シギョウジカン'),
                (3, 6, '休憩時間（00：00）キュウケイジカン'),
                (3, 7, '終業時間（00：00）シュウギョウジカン'),
                (3, 8, '年間ｶﾚﾝﾀﾞｰ時間ネンカンカレンダー'),
                (3, 9, 'ｶﾚﾝﾀﾞ変換'),
                (3, 16, '深夜残業判定①'),
                (3, 22, '早朝勤務'),
                (3, 24, '実労働時間'),
                (3, 25, '早朝深夜勤務時間'),
                (3, 26, '日残業\n（深残除く）'),
                (3, 27, '深夜残業'),
                (3, 36, '休出早朝深夜勤務'),
                (3, 39, 'メモ'),
                (3, 40, '実労働時間計'),
                (5, 3, '月'),
                (5, 4, ''),
                (5, 5, 9 / 24),
                (5, 6, break_serial),
                (5, 7, ''),
                (5, 8, 8),
                (5, 9, 8),
                (5, 16, 99),
                (5, 22, 0),
                (5, 24, 8.5),
                (5, 25, 0),
                (5, 26, ''),
                (5, 27, 0.25),
                (5, 30, 77),
                (5, 36, ('formula', 'Z5+AA5')),
                (5, 39, '遅番'),
                (5, 40, 9.1),
            ],
            '集計': [],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        labels = [c['label'] for c in view['columns']]
        self.assertEqual(labels, [
            '日付', '休み', '始業時間', '休憩時間', '終業時間', '年間ｶﾚﾝﾀﾞｰ時間',
            '実労働時間', '早朝深夜勤務時間', '日残業', '深夜残業', '合計',
        ])
        self.assertNotIn('（00', ''.join(labels))
        by_key = {c['key']: c for c in view['columns']}
        self.assertEqual(by_key['total']['col'], 40)
        self.assertEqual(by_key['actual']['col'], 24)
        self.assertEqual(by_key['dayOvertime']['sourceLabel'], '日残業\n（深残除く）')
        row = view['rows'][0]
        self.assertEqual(row['date'], '2026/02/02')
        self.assertEqual(row['leave'], '')
        self.assertEqual(row['start'], '9:00')
        self.assertEqual(row['breakTime'], '1:10')
        self.assertEqual(row['end'], '')
        self.assertNotEqual(row['end'], '0')
        self.assertEqual(row['calendarHours'], '8')
        self.assertEqual(row['actual'], '8.5')
        self.assertEqual(row['earlyNight'], '0')
        self.assertEqual(row['dayOvertime'], '')
        self.assertEqual(row['nightOvertime'], '0.25')
        self.assertEqual(row['total'], '9.1')
        self.assertNotEqual(row['total'], row['actual'])
        self.assertNotIn('99', row.values())
        self.assertNotIn('77', row.values())
        self.assertNotIn('遅番', row.values())
        self.assertNotIn('月', row.values())

    def test_duplicate_total_header_is_not_guessed(self):
        blob = _xlsx({
            '年間ｶﾚﾝﾀﾞｰ': [(3, 2, _serial(date(2026, 2, 1)))],
            '26.2': [
                (3, 24, '実労働時間'),
                (3, 40, '実労働時間計'),
                (3, 41, '実労働時間計'),
                (5, 24, 8.5),
                (5, 40, 9.1),
                (5, 41, 3),
            ],
            '集計': [],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.SEISHAIN_2026_LAYOUT)
        row = view['rows'][0]
        self.assertEqual(row['actual'], '8.5')
        self.assertEqual(row['total'], '（列が複数）')
        self.assertNotIn('9.1', row.values())
        self.assertNotIn('3', row.values())

    def test_part_columns_use_their_own_positions_not_seishain(self):
        blob = _xlsx({
            '2026.2': [
                (3, 4, '休み'),
                (3, 5, '有休\n時間ユウキュウジカン'),
                (3, 6, '時給ジキュウ'),
                (3, 8, '始業時間（00：00）'),
                (3, 9, '休憩時間（00：00）'),
                (3, 10, '終業時間（00：00）'),
                (3, 24, '実労働時間'),
                (3, 25, '早朝深夜勤務時間'),
                (3, 26, '日残業\n（深残除く）'),
                (3, 27, '深夜残業'),
                (3, 40, '実労働時間計'),
                (5, 1, _serial(date(2026, 2, 1))),
                (5, 4, ''),
                (5, 5, 0.5),
                (5, 6, 1105),
                (5, 8, 8.933333333333334 / 24),
                (5, 9, 70 / (24 * 60)),
                (5, 10, 16 / 24),
                (5, 24, 6.25),
                (5, 25, ''),
                (5, 26, 0.5),
                (5, 27, 0),
                (5, 40, 7.5),
            ],
        })
        view = kintai.daily_rows_from_workbook(blob, 2026, 2, kintai.MONTHDOT_2026_PART_LAYOUT)
        self.assertEqual([c['label'] for c in view['columns']], [
            '日付', '休み', '始業時間', '休憩時間', '終業時間', '年間ｶﾚﾝﾀﾞｰ時間',
            '実労働時間', '早朝深夜勤務時間', '日残業', '深夜残業', '合計',
        ])
        by_key = {c['key']: c for c in view['columns']}
        self.assertEqual(by_key['start']['col'], 8)
        self.assertEqual(by_key['breakTime']['col'], 9)
        self.assertEqual(by_key['end']['col'], 10)
        self.assertEqual(by_key['total']['col'], 40)
        self.assertEqual(by_key['calendarHours']['missing'], '（列なし）')
        row = view['rows'][0]
        self.assertEqual(row['start'], '8:56')
        self.assertEqual(row['breakTime'], '1:10')
        self.assertEqual(row['end'], '16:00')
        self.assertEqual(row['actual'], '6.25')
        self.assertEqual(row['dayOvertime'], '0.5')
        self.assertEqual(row['nightOvertime'], '0')
        self.assertEqual(row['total'], '7.5')
        self.assertEqual(row['calendarHours'], '（列なし）')
        self.assertNotIn('0.5有休', row.values())
        self.assertNotEqual(row['breakTime'], '1105')
        self.assertNotIn('1105', row.values())
        self.assertNotIn('paidTime', by_key)

    def test_daily_table_scrolls_and_keeps_date_with_headers(self):
        js = (ROOT / 'kintai.js').read_text(encoding='utf-8')
        css = (ROOT / 'style.css').read_text(encoding='utf-8')
        html = (ROOT / 'index.html').read_text(encoding='utf-8')
        paint = js.split('function paintDaily', 1)[1].split('async function ensurePeoplePack', 1)[0]
        self.assertIn('cols.map', paint)
        self.assertIn('scope="col"', paint)
        self.assertIn('scope="row"', paint)
        self.assertIn('title=', paint)
        self.assertNotIn("['date','leave','start']", paint)
        daily_css = css[css.find('#kintaidaily'):css.find('#kintaifileinput')]
        self.assertIn('#kintaidaily .kintai-tablewrap{max-width:100%', daily_css)
        self.assertIn('overflow:auto', daily_css)
        self.assertIn('.kintai-daily-sheet{width:max-content', daily_css)
        self.assertIn('padding:1px 3px', daily_css)
        self.assertIn('white-space:nowrap', daily_css)
        self.assertIn('.kintai-daily-sticky{position:sticky;left:0', daily_css)
        self.assertIn('実労働時間計', html)
        self.assertIn('（列なし）', html)
        self.assertNotIn('見出しがある列をExcelの並び', html)


class DailySheetApiTest(unittest.TestCase):
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
        self.book = fixtures / 'daily.xlsx'
        self.blob = kintai.build_fixture_xlsx({
            (2026, 2): [
                {'day': 3, 'start': '10:00', 'leave': '', 'ordinary_minutes': 450, 'overtime_minutes': 15},
            ],
        })
        self.book.write_bytes(self.blob)
        self.c.execute('UPDATE kintai_people SET source_file=? WHERE id=?', ('daily.xlsx', self.person['id']))
        (self.data / 'kintai-local.json').write_text(json.dumps({
            'shareRoot': '',
            'layoutId': 'fixture-v1',
            'peopleFiles': {},
            'peopleLayouts': {str(self.person['id']): 'fixture-v1'},
        }, ensure_ascii=False), encoding='utf-8')
        self.c.commit()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_reads_saved_path_without_writing_or_storing_days(self):
        before = self.book.read_bytes()
        mtime = self.book.stat().st_mtime
        monthly = self.c.execute('SELECT COUNT(*) FROM kintai_monthly').fetchone()[0]
        view = kintai.person_daily_sheet(self.c, self.person['id'], 2026, 2)
        self.assertEqual(self.book.read_bytes(), before)
        self.assertEqual(self.book.stat().st_mtime, mtime)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM kintai_monthly').fetchone()[0], monthly)
        self.assertTrue(view['readOnly'])
        self.assertEqual(view['fileName'], 'daily.xlsx')
        hit = next(row for row in view['rows'] if row['date'] == '2026/02/03')
        self.assertEqual(hit['start'], '10:00')
        self.assertEqual(hit['ordinary'], '450')

    def test_permission_matches_attendance(self):
        class Denied:
            user = {'id': 2, 'role': 'sales', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = '/api/kintai/daily?personId=1&year=2026&month=2'

        class Allowed:
            user = {'id': 1, 'role': 'owner', 'mark_kintai': 0, 'manage': 0, 'overview': 0}
            path = f"/api/kintai/daily?personId={self.person['id']}&year=2026&month=2"

        self.assertEqual(kintai.handle_get(Denied(), self.c, '/api/kintai/daily')[0], 403)
        code, body = kintai.handle_get(Allowed(), self.c, '/api/kintai/daily')
        self.assertEqual(code, 200)
        self.assertEqual(body['sheetName'], '2026-02')
        self.assertEqual(body['personId'], self.person['id'])

    def test_saved_path_returns_sheet_columns_without_writing(self):
        blob = _xlsx({
            '年間ｶﾚﾝﾀﾞｰ': [(3, 2, _serial(date(2026, 2, 3)))],
            '26.2': [
                (3, 4, '休み'),
                (3, 5, '始業時間（00：00）'),
                (3, 6, '休憩時間（00：00）'),
                (3, 7, '終業時間（00：00）'),
                (3, 24, '実労働時間'),
                (3, 40, '実労働時間計'),
                (5, 5, 9 / 24),
                (5, 6, 70 / (24 * 60)),
                (5, 24, 8.5),
                (5, 40, 8.5),
            ],
            '集計': [],
        })
        self.book.write_bytes(blob)
        before = self.book.read_bytes()
        mtime = self.book.stat().st_mtime
        monthly = self.c.execute('SELECT COUNT(*) FROM kintai_monthly').fetchone()[0]
        (self.data / 'kintai-local.json').write_text(json.dumps({
            'shareRoot': '',
            'layoutId': 'shukkinbo-2026-seishain-v1',
            'peopleFiles': {},
            'peopleLayouts': {str(self.person['id']): 'shukkinbo-2026-seishain-v1'},
        }, ensure_ascii=False), encoding='utf-8')
        view = kintai.person_daily_sheet(self.c, self.person['id'], 2026, 2)
        self.assertEqual(self.book.read_bytes(), before)
        self.assertEqual(self.book.stat().st_mtime, mtime)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM kintai_monthly').fetchone()[0], monthly)
        self.assertTrue(view['readOnly'])
        labels = [c['label'] for c in view['columns']]
        self.assertEqual(labels, [
            '日付', '休み', '始業時間', '休憩時間', '終業時間', '年間ｶﾚﾝﾀﾞｰ時間',
            '実労働時間', '早朝深夜勤務時間', '日残業', '深夜残業', '合計',
        ])
        hit = view['rows'][0]
        self.assertEqual(hit['date'], '2026/02/03')
        self.assertEqual(hit['start'], '9:00')
        self.assertEqual(hit['breakTime'], '1:10')
        self.assertEqual(hit['end'], '')
        self.assertEqual(hit['actual'], '8.5')
        self.assertEqual(hit['total'], '8.5')
        self.assertEqual(hit['calendarHours'], '（列なし）')


if __name__ == '__main__':
    unittest.main()
