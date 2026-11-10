"""Excelのシート参照が解決できることを検証する回帰テスト。"""
import unittest
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


if __name__ == '__main__':
    unittest.main()
