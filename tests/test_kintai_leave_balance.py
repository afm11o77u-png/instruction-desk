"""有給管理簿のセル対応・保存値・暗号化読取。"""
import base64
import sqlite3
import unittest
from io import BytesIO
from unittest.mock import patch
import kintai
import kintai_leave_balance as leave
from test_kintai_daily import _xlsx


class LeaveBalanceTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.execute('CREATE TABLE kintai_people(id INTEGER PRIMARY KEY,employee_code TEXT,display_name TEXT,retired INTEGER,active INTEGER,sort_order INTEGER)')
        self.c.executemany('INSERT INTO kintai_people VALUES(?,?,?,0,1,?)', [(1,'001','一人目',1),(2,'002','二人目',2)])

    def tearDown(self):
        self.c.close()

    def payload(self, cells=None, blob=None):
        return {'action':'update', 'filename':'leave.xlsx', 'password':'',
                'data':base64.b64encode(blob if blob is not None else _xlsx({'集計': [(3,11,37),(4,11,0)]})).decode(),
                'people': cells or [{'personId':1,'cell':'k3'},{'personId':2,'cell':'K4'}]}

    def test_update_save_zero_and_no_workbook_or_password_storage(self):
        p = self.payload()
        p['password'] = 'not-stored'
        r = leave.handle(self.c, p, kintai.read_xlsx_grid)['people']
        self.assertEqual([x['balance'] for x in r], [37,0])
        self.assertEqual(r[0]['cell'], 'K3')
        self.assertTrue(r[0]['updatedAt'])
        dump = '\n'.join(self.c.iterdump())
        self.assertNotIn('not-stored', dump)
        self.assertNotIn(p['data'], dump)

    def test_changed_cell_clears_old_value_and_unchanged_retains(self):
        leave.handle(self.c, self.payload(), kintai.read_xlsx_grid)
        p = self.payload([{'personId':1,'cell':'K5'},{'personId':2,'cell':'K4'}])
        p['action']='save'
        rows = leave.handle(self.c,p,kintai.read_xlsx_grid)['people']
        self.assertIsNone(rows[0]['balance'])
        self.assertEqual(rows[1]['balance'],0)

    def test_blank_text_and_missing_cache_do_not_show_old_balance(self):
        leave.handle(self.c,self.payload(),kintai.read_xlsx_grid)
        blob = _xlsx({'集計':[(4,11,('formula','SUM(A1:A2)'))]})
        rows=leave.handle(self.c,self.payload(blob=blob),kintai.read_xlsx_grid)['people']
        self.assertIsNone(rows[0]['balance'])
        self.assertEqual(rows[0]['note'],'空欄')
        self.assertIn('計算結果なし',rows[1]['note'])
        self.assertEqual(leave.cell_value({(3,11):'37日'},'K3')[0],None)

    def test_validate_before_any_mapping_changes(self):
        for entries in [[{'personId':1,'cell':'K3'},{'personId':2,'cell':'K0'}],
                        [{'personId':999,'cell':'K3'}],
                        [{'personId':1,'cell':'K3'},{'personId':1,'cell':'K4'}]]:
            with self.assertRaises(ValueError):
                leave.handle(self.c,self.payload(entries),kintai.read_xlsx_grid)
        self.assertTrue(all(not p['cell'] for p in leave.bundle(self.c)['people']))

    def test_missing_sheet_does_not_replace_previous_values(self):
        leave.handle(self.c,self.payload(),kintai.read_xlsx_grid)
        with self.assertRaisesRegex(ValueError,'集計'):
            leave.handle(self.c,self.payload(blob=_xlsx({'別シート':[]})),kintai.read_xlsx_grid)
        self.assertEqual(leave.bundle(self.c)['people'][0]['balance'],37)

    def test_cell_bounds(self):
        self.assertEqual(leave.normalize_cell('$k$3'),'K3')
        self.assertEqual(leave.normalize_cell('XFD1048576'),'XFD1048576')
        for value in ('XFE1','K1048577','K3:K4','=K3','集計!K3'):
            with self.assertRaises(ValueError): leave.normalize_cell(value)

    def test_encrypted_roundtrip_wrong_password_and_required(self):
        try:
            from msoffcrypto.format.ooxml import OOXMLFile
        except ImportError:
            self.skipTest('Install requirements-kintai-leave.txt for encrypted tests')
        source = _xlsx({'集計':[(3,11,22.5)]})
        # 暗号化ライブラリの小容量OLE生成制約を避け、通常のExcelサイズにする。
        import zipfile
        padded = BytesIO(source)
        with zipfile.ZipFile(padded, 'a') as book:
            book.writestr('test-padding.bin', bytes(range(256)) * 32)
        source = padded.getvalue()
        encrypted = BytesIO()
        OOXMLFile(BytesIO(source)).encrypt('test-only-password', encrypted)
        p = self.payload(blob=encrypted.getvalue())
        with self.assertRaisesRegex(ValueError,'パスワード'):
            leave.handle(self.c,p,kintai.read_xlsx_grid)
        p['password']='wrong'
        with self.assertRaisesRegex(ValueError,'パスワード'):
            leave.handle(self.c,p,kintai.read_xlsx_grid)
        p['password']='test-only-password'
        self.assertEqual(leave.handle(self.c,p,kintai.read_xlsx_grid)['people'][0]['balance'],22.5)
        self.assertNotIn(p['password'],'\n'.join(self.c.iterdump()))

    def test_permissions_are_checked_before_leave_api(self):
        from types import SimpleNamespace
        with patch.object(kintai,'require_kintai',return_value=(403,{'error':'denied'})):
            h=SimpleNamespace(user={})
            with patch.object(kintai,'shift_access_only',return_value=False):
                self.assertEqual(kintai.handle_get(h,self.c,'/api/kintai/leave-balance')[0],403)
                self.assertEqual(kintai.handle_post(h,self.c,'/api/kintai/leave-balance',{})[0],403)


if __name__=='__main__':
    unittest.main()
