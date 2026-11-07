import base64
import csv
import unittest
from types import SimpleNamespace
from datetime import date, timedelta
from io import BytesIO, StringIO
from xml.sax.saxutils import escape
from zipfile import ZipFile

import kintai
import kintai_tot_attendance as tot


def encoded(blob):
    return base64.b64encode(blob).decode('ascii')


def csv_file(rows, encoding='cp932', header=None):
    out = StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(header or ['名前', '日時', '出勤時刻(時刻のみ)', '休憩時間', '退勤時刻(時刻のみ)', '出勤予定時刻(時刻のみ)'])
    writer.writerows(rows)
    return encoded(out.getvalue().encode(encoding))


def workbook(name='加藤 篤郎', col=5, formula=False, merged=False):
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    def cell(ref, value):
        if isinstance(value, int):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    body = cell('AK2', name)
    for c, label in zip(range(col, col + 3), ['始業時間（00：00）', '休憩時間（00：00）', '終業時間（00：00）']):
        body += cell(tot.col_name(c) + '3', label)
    for offset in range(42):
        # This real template has stale cached years: preserve month/day alignment.
        d = date(2025, 10, 16) + timedelta(days=offset)
        body += cell('A' + str(offset + 5), (d - date(1899, 12, 30)).days)
    if formula:
        body += f'<c r="{tot.col_name(col)}5"><f>1+1</f><v>2</v></c>'
    extra = f'<mergeCells><mergeCell ref="{tot.col_name(col)}5:{tot.col_name(col + 1)}5"/></mergeCells>' if merged else ''
    out = BytesIO()
    with ZipFile(out, 'w') as z:
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="26.11" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{ns}"><sheetData><row>{body}</row></sheetData>{extra}</worksheet>')
    return encoded(out.getvalue())


class TotAttendanceTest(unittest.TestCase):
    def payload(self, **options):
        p = {'csvData': csv_file([
            ['加藤 篤郎', '2026/10/16(金)', '08:54', '70', '17:44', '09:00'],
            ['加藤 篤郎', '2026/10/17(土)', '09:00', '', '17:40', '09:00'],
            ['加藤 篤郎', '2026/10/18(日)', '', '', '', ''],
            ['加藤 篤郎', '2026/10/19(月)', '09:00', '0', '15:00', '09:00'],
        ]), 'workbookData': workbook(), 'person': '加藤篤郎', 'month': '2026-11'}
        p.update(options)
        return p

    def test_csv_and_time_conversion(self):
        people = tot.parse_csv(self.payload()['csvData'])
        days = people['加藤篤郎']['days']
        self.assertEqual(days['2026-10-16']['start'], '8:54')
        self.assertEqual(days['2026-10-16']['rest'], '1:10')
        self.assertEqual(days['2026-10-16']['end'], '17:44')
        self.assertEqual(days['2026-10-17']['rest'], '')
        self.assertEqual(days['2026-10-19']['rest'], '0:00')

    def test_period_and_destination(self):
        _, body = tot.prepare(self.payload(), kintai.read_xlsx_grid)
        self.assertEqual(body['layout']['range'], 'E5:G35')
        self.assertTrue(body['layout']['dateYearMismatch'])
        self.assertEqual(len(body['rows']), 31)
        self.assertEqual(body['rows'][0]['date'], '2026-10-16')
        self.assertEqual(body['rows'][-1]['date'], '2026-11-15')
        self.assertEqual(body['defaultBreak'], 70)
        self.assertIn('空欄', body['rows'][1]['note'])
        self.assertIn('ありません', body['rows'][4]['note'])

    def test_other_input_columns(self):
        _, body = tot.prepare(self.payload(workbookData=workbook(col=8)), kintai.read_xlsx_grid)
        self.assertEqual(body['layout']['range'], 'H5:J35')

    def test_workbook_identity_formulas_merges(self):
        for data in [workbook(name='別の人'), workbook(formula=True), workbook(merged=True)]:
            with self.assertRaises(ValueError):
                tot.prepare(self.payload(workbookData=data), kintai.read_xlsx_grid)

    def test_duplicate_or_planned_start_rejected(self):
        row = ['加藤 篤郎', '2026/10/16(金)', '9:00', '70', '17:40', '9:00']
        with self.assertRaisesRegex(ValueError, '重複'):
            tot.parse_csv(csv_file([row, row]))
        with self.assertRaisesRegex(ValueError, '実際'):
            tot.parse_csv(csv_file([row], header=['名前', '日時', '始業予定', '休憩時間', '退勤時刻(時刻のみ)', '出勤予定時刻(時刻のみ)']))

    def test_utf8_errors_and_schedule_not_used(self):
        row = ['加藤 篤郎', '2026/10/16(金)', '', '', '17:40', '9:00']
        people = tot.parse_csv(csv_file([row], 'utf-8-sig'))
        self.assertEqual(people['加藤篤郎']['days']['2026-10-16']['start'], '')
        row[2] = '=1+1'
        parsed = tot.parse_csv(csv_file([row]))
        self.assertIn('原CSV', parsed['加藤篤郎']['days']['2026-10-16']['sourceWarning'])
        with self.assertRaises(ValueError):
            tot.parse_csv('not base64')

    def test_multiple_punches_preserved_for_review(self):
        row = ['加藤 篤郎', '2026/10/16(金)', '08:52 17:42', '', '', '9:00']
        p = self.payload(csvData=csv_file([row]))
        _, result = tot.prepare(p, kintai.read_xlsx_grid)
        self.assertEqual(result['rows'][0]['start'], '08:52 17:42')
        self.assertIn('原CSV', result['rows'][0]['note'])

    def test_months_leap_year(self):
        self.assertEqual(tot.closing_month(date(2026, 12, 16)), '2027-01')
        self.assertEqual(tot.closing_month(date(2026, 10, 15)), '2026-10')
        a, b = tot.period('2024-03')
        self.assertEqual((b - a).days + 1, 29)

    def test_endpoints_permissions_no_database_writes(self):
        class NoDatabase:
            def execute(self, *args):
                raise AssertionError('Conversion must not access database')
        owner = SimpleNamespace(user={'role': 'owner'})
        no_access = SimpleNamespace(user={'role': 'user', 'manage': 0, 'overview': 0, 'mark_kintai': 0})
        for action in ['parse', 'prepare']:
            route = '/api/kintai/tot-attendance/' + action
            self.assertEqual(kintai.handle_post(owner, NoDatabase(), route, self.payload())[0], 200)
            self.assertEqual(kintai.handle_post(no_access, NoDatabase(), route, self.payload())[0], 403)


if __name__ == '__main__':
    unittest.main()
