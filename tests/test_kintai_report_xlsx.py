import unittest
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from unittest.mock import patch

import kintai
import kintai_report_xlsx as report


class ReportTest(unittest.TestCase):
    def data(self):
        months = [{'year': 2026 if m > 1 else 2027, 'month': m,
                   'workDays': 20.5, 'holidayDays': None, 'ordinaryHm': '165:33'}
                  for m in list(range(2, 13)) + [1]]
        overview = {'fiscalLabel': '2026年度', 'rows': [{
            'displayName': '試験 & 確認', 'employeeCode': '009', 'months': months}]}
        columns = [{'key': 'date', 'label': '日付'}] + list(kintai.DAILY_VIEW_FIELDS)
        daily = {'year': 2026, 'month': 11, 'columns': columns,
                 'rows': [{'date': f'2026/10/{i:02d}', 'start': '8:30',
                           'workDays': 0.5 if i == 1 else None} for i in range(1, 32)]}
        return overview, daily

    def test_print_and_values(self):
        overview, daily = self.data()
        blob = report.workbook(overview, daily)
        ns = {'s': report.NS}
        with ZipFile(BytesIO(blob)) as z:
            for name in z.namelist():
                if name.endswith('.xml') or name.endswith('.rels'):
                    ET.fromstring(z.read(name))
            sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            setup = sheet.find('s:pageSetup', ns)
            self.assertEqual(setup.attrib['paperSize'], '9')
            self.assertEqual(setup.attrib['fitToHeight'], '1')
            self.assertEqual(setup.attrib['fitToWidth'], '1')
            self.assertEqual(sheet.find('.//s:c[@r="B5"]/s:v', ns).text, '20.5')
            self.assertIsNone(sheet.find('.//s:c[@r="C5"]/s:v', ns))
            self.assertIn('165:33', z.read('xl/worksheets/sheet1.xml').decode())
            self.assertEqual(len(sheet.findall('s:sheetData/s:row', ns)), 52)

    def test_invalid_period_does_not_read_source(self):
        with patch.object(kintai, 'person_daily_sheet') as read:
            with self.assertRaises(ValueError):
                report.export(None, 2026, 1, (2027, 2))
            read.assert_not_called()

    def test_overflow_not_silently_truncated(self):
        overview, daily = self.data()
        daily['rows'].append(daily['rows'][0])
        with self.assertRaises(ValueError):
            report.workbook(overview, daily)


if __name__ == '__main__':
    unittest.main()
