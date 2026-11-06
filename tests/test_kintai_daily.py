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
        self.assertEqual([c['key'] for c in view['columns']], ['date', 'leave', 'start'])

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
        self.assertEqual(row['paidTime'], '0.5')
        self.assertIn('paidTime', [c['key'] for c in part_view['columns']])


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


if __name__ == '__main__':
    unittest.main()
