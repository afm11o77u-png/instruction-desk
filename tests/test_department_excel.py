import base64
import io
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import department_excel
from openpyxl import Workbook


def book(rows,headers=None):
    w=Workbook();s=w.active;s.title='dbo_M_顧客マスタ';s.append(headers or ['顧客コード','顧客名','削除フラグ'])
    for row in rows:s.append(row)
    out=io.BytesIO();w.save(out);return base64.b64encode(out.getvalue()).decode()


class DepartmentExcelTest(unittest.TestCase):
    def test_normalized_names_deduplicate_and_uncertain_rows_require_review(self):
        plan=department_excel.preview(book([['AI-100-02','北雄ﾗｯｷｰ本部 ＯＳ',0],['AI-101-02','店舗 OS',0],['AI-100-03','店舗 ｸﾞﾛｻﾘｰ',0],['AI-112-01','北雄ﾗｯｷｰ花川南',0],['AI-113-01','店舗 削除済',1]]),[dict(name='OS')])
        self.assertEqual(plan['source_rows'],5);self.assertEqual(plan['deleted'],1)
        self.assertEqual([x['name'] for x in plan['candidates']],['OS','グロサリー'])
        self.assertTrue(plan['candidates'][0]['existing']);self.assertFalse(plan['candidates'][0]['selected'])
        self.assertEqual(plan['candidates'][0]['rows'],[2,3]);self.assertEqual(len(plan['unresolved']),1)
        self.assertFalse(plan['unresolved'][0]['selected'])

    def test_explicit_department_column_and_bad_workbook(self):
        plan=department_excel.preview(book([['A','会社名','精肉']],[ '顧客コード','顧客名','部署名']),[])
        self.assertEqual(plan['candidates'][0]['name'],'精肉')
        for data in ('bad',book([['A','会社名']],['コード','名前'])):
            with self.assertRaises(ValueError):department_excel.preview(data,[])
