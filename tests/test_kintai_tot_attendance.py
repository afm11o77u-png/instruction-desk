import base64
import csv
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import date, timedelta
from io import StringIO

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


class TotAttendanceTest(unittest.TestCase):
    def payload(self, **options):
        p = {'csvData': csv_file([
            ['加藤 篤郎', '2026/10/16(金)', '08:54', '70', '17:44', '09:00'],
            ['加藤 篤郎', '2026/10/17(土)', '09:00', '', '17:40', '09:00'],
            ['加藤 篤郎', '2026/10/18(日)', '', '', '', ''],
            ['加藤 篤郎', '2026/10/19(月)', '09:00', '0', '15:00', '09:00'],
        ]), 'person': '加藤篤郎', 'month': '2026-11'}
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

    def test_period_without_workbook(self):
        _, body = tot.prepare(self.payload())
        self.assertNotIn('layout', body)
        self.assertEqual(len(body['rows']), 31)
        self.assertEqual(body['rows'][0]['date'], '2026-10-16')
        self.assertEqual(body['rows'][-1]['date'], '2026-11-15')
        self.assertEqual(body['defaultBreak'], 70)
        self.assertIn('空欄', body['rows'][1]['note'])
        self.assertIn('ありません', body['rows'][4]['note'])

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
        _, result = tot.prepare(p)
        self.assertEqual(result['rows'][0]['start'], '08:52 17:42')
        self.assertIn('原CSV', result['rows'][0]['note'])

    def test_months_leap_year(self):
        self.assertEqual(tot.closing_month(date(2026, 12, 16)), '2027-01')
        self.assertEqual(tot.closing_month(date(2026, 10, 15)), '2026-10')
        a, b = tot.period('2024-03')
        self.assertEqual((b - a).days + 1, 29)

    def test_empty_days_are_not_classified_as_holidays(self):
        p = self.payload()
        _, result = tot.prepare(p)
        self.assertEqual([r['date'] for r in result['rows']][0], '2026-10-16')
        self.assertEqual(result['rows'][2]['start'], '')
        self.assertEqual(result['rows'][2]['rest'], '')
        self.assertEqual(result['rows'][2]['end'], '')
        self.assertTrue(all('holiday' not in row for row in result['rows']))

    def test_open_folder_local_only_and_fixed_path(self):
        remote = SimpleNamespace(client_address=('192.168.1.50', 1234))
        local = SimpleNamespace(client_address=('127.0.0.1', 1234))
        with patch.object(tot.subprocess, 'Popen') as run:
            self.assertEqual(tot.open_folder(remote)[0], 403)
            run.assert_not_called()
            with patch.object(tot.os, 'name', 'nt'):
                self.assertEqual(tot.open_folder(local)[0], 200)
            run.assert_called_once_with(['explorer.exe', tot.ATTENDANCE_FOLDER], shell=False)
        self.assertIn('出勤簿   2026年度版', tot.ATTENDANCE_FOLDER)

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
