"""Excelのシート参照が解決できることを検証する回帰テスト。"""
import sqlite3
import unittest
from unittest.mock import patch
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import kintai_exec_report as report


class ExecReportXlsxTest(unittest.TestCase):
    def test_sheet_relationship_and_excel_readback(self):
        data = report.workbook({
            'year': 2026, 'month': 11, 'fiscalStartYear': 2026,
            'summary': {'months': [{'key': '2026/11', 'ordinaryHm': '350:11',
                                    'overtimeHm': '3:22', 'totalHm': '353:33'}]},
            'newContracts': [{'customer': '試験顧客', 'period': '11/1〜11/15'}],
        })
        with ZipFile(BytesIO(data)) as z:
            book = ET.fromstring(z.read('xl/workbook.xml'))
            sheet = book.find(f'{{{report.NS}}}sheets/{{{report.NS}}}sheet')
            rel_id = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            self.assertEqual(rel_id, 'rId1')
            rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
            target = next(r.get('Target') for r in rels if r.get('Id') == rel_id)
            self.assertIn('xl/' + target, z.namelist())
        import openpyxl
        book = openpyxl.load_workbook(BytesIO(data))
        self.assertEqual(book.sheetnames, ['幹部会議報告_2026年11月'])
        values = [cell.value for row in book.active for cell in row]
        self.assertIn('350:11', values)
        self.assertIn('試験顧客', values)


    def test_month_rows_and_totals_stop_at_report_month(self):
        import openpyxl
        months = [
            {'year': 2026 if m >= 2 else 2027, 'month': m,
             'key': f'{2026 if m >= 2 else 2027}/{m:02d}',
             'ordinaryMinutes': 60, 'overtimeMinutes': 5, 'totalMinutes': 65,
             'ordinaryHm': '1:00', 'overtimeHm': '0:05', 'totalHm': '1:05'}
            for m in list(range(2, 13)) + [1]
        ]
        for year, month, count in [(2026, 2, 1), (2026, 10, 9), (2027, 1, 12)]:
            with self.subTest(year=year, month=month):
                data = report.workbook({
                    'year': year, 'month': month, 'fiscalStartYear': 2026,
                    'summary': {'months': months, 'fiscalLabel': '2026年2月〜2027年1月',
                                'period': {'ordinaryHm': '999:00'}},
                })
                sheet = openpyxl.load_workbook(BytesIO(data)).active
                labels = [row[0].value for row in sheet]
                self.assertEqual([v for v in labels if v in [m['key'] for m in months]],
                                 [m['key'] for m in months[:count]])
                total_row = labels.index('期間合計') + 1
                self.assertEqual(sheet.cell(total_row, 2).value, f'{count}:00')
                self.assertEqual(sheet.cell(total_row, 4).value,
                                 f'{count * 5 // 60}:{count * 5 % 60:02d}')
                self.assertIn(f'2026年2月〜{year}年{month}月', sheet.cell(2, 1).value)

    def test_empty_month_totals_remain_blank(self):
        summary = report._summary_through_month({'months': [
            {'year': 2026, 'month': 2, 'ordinaryMinutes': None, 'ordinaryHm': ''},
            {'year': 2026, 'month': 3, 'ordinaryMinutes': 60, 'ordinaryHm': '1:00'},
        ]}, 2026, 2)
        self.assertEqual(len(summary['months']), 1)
        self.assertEqual(summary['period']['ordinaryHm'], '')

    def test_crimp_report_uses_target_month_without_names(self):
        import openpyxl
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = sqlite3.Row
            report.kintai_crimp.schema(conn)
            conn.executemany(
                'INSERT INTO kintai_crimp_readings(day, reading, user_name, worker_name, updated_at) VALUES(?, ?, ?, ?, ?)',
                [(day, count, '入力者テスト', '作業者テスト', '') for day, count in [
                    ('2026-09-30', 100), ('2026-10-05', 128), ('2026-10-31', 200), ('2026-11-01', 220)
                ]],
            )
            with patch.object(report.kintai_bihin, 'get_sheet', return_value={}):
                data = report.bundle(conn, 2026, 10, None, lambda c, fy: {})
                empty = report.bundle(conn, 2026, 8, None, lambda c, fy: {})
            self.assertEqual(data['crimp'], {'start': '2026-10-01', 'end': '2026-10-31', 'total': 100})
            self.assertIsNone(empty['crimp']['total'])
            sheet = openpyxl.load_workbook(BytesIO(report.workbook(data))).active
            cells = [cell.value for row in sheet for cell in row]
            self.assertIn('4. 圧着数（2026-10-01〜2026-10-31）', cells)
            self.assertIn(100, cells)
            self.assertNotIn('入力者テスト', cells)
            self.assertNotIn('作業者テスト', cells)


if __name__ == '__main__':
    unittest.main()
