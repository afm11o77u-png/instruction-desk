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

    def test_contract_master_uses_department_column_without_importing_people(self):
        raw=book([['M1','ﾊﾟﾝ粉工場','氏名A',0],['M1','パン粉工場','氏名B',0],['M2','製造','氏名C',0],['M2','','氏名D',0],['M2','削除','氏名E',1]],['顧客コード','部署名','個人名','削除フラグ'])
        plan=department_excel.preview(raw,[dict(name='製造')],'contract')
        self.assertEqual([x['name'] for x in plan['candidates']],['パン粉工場','製造'])
        self.assertEqual(plan['candidates'][0]['rows'],[2,3])
        self.assertFalse(plan['candidates'][1]['selected'])
        self.assertEqual(len(plan['unresolved']),1)
        self.assertEqual(plan['deleted'],1)
        with self.assertRaises(ValueError):department_excel.preview(raw,[],'customer')
        with self.assertRaises(ValueError):department_excel.preview(raw,[],'invalid')

    def test_site_preview_groups_only_known_departments_for_selected_company(self):
        raw = book([
            ['1', '北雄ﾗｯｷｰ本部 青果', 0],
            ['2', '北雄ラッキー本部 ＯＳ', 0],
            ['3', '北雄ラッキー 岩内店', 0],
            ['4', '北雄ラッキー星置女性 管理', 0],
            ['5', '北雄ラッキー星置男性 管理', 0],
            ['6', '他社本部 青果', 0],
            ['7', '北雄ラッキー旧店舗', 1],
        ])
        plan = department_excel.preview_sites(raw, '北雄ラッキー㈱')
        self.assertEqual(plan['deleted'], 1)
        self.assertEqual(plan['matched'], 5)
        self.assertEqual([(x['name'], x['count']) for x in plan['candidates']], [
            ('本部', 2),
            ('岩内店', 1),
            ('星置女性', 1),
            ('星置男性', 1),
        ])

    def test_site_preview_uses_shared_site_key(self):
        raw = book([
            ['1', '北雄ﾗｯｷｰ篠路 管理', 0],
            ['2', 'ﾗｯｷｰﾏｰﾄ西野 管理', 0],
            ['3', 'ラッキーマート幌向', 0],
            ['4', '北雄ラッキー星置女性 管理', 0],
            ['5', '他社 管理', 0],
        ])
        sites = [dict(id='a', name='篠路店'), dict(id='b', name='西野店'), dict(id='c', name='ラッキーマート幌向店'), dict(id='d', name='星置駅前店')]
        plan = department_excel.preview_sites(raw, '北雄ラッキー㉈', sites)
        got = {x['name']: (x['existing'], x['similar'], x['checked']) for x in plan['candidates']}
        self.assertEqual(got, {
            '篠路': ('篠路店', [], False),
            'ラッキーマート西野': ('西野店', [], False),
            'ラッキーマート幌向': ('ラッキーマート幌向店', [], False),
            '星置女性': ('', ['星置駅前店'], False),
        })
