"""シフト表の Excel 出力。一時DBと合成データだけ。画面の表を再読取して照合する。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import kintai
import kintai_shift
import kintai_shift_xlsx

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'kintai-shift.js').read_text(encoding='utf-8')
HTML = (ROOT / 'index.html').read_text(encoding='utf-8')
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def read_xlsx(blob: bytes):
    with ZipFile(BytesIO(blob)) as zipped:
        sheet_xml = zipped.read('xl/worksheets/sheet1.xml')
        styles_xml = zipped.read('xl/styles.xml')
        workbook_xml = zipped.read('xl/workbook.xml')
        names = zipped.namelist()
    sheet = ET.fromstring(sheet_xml)
    styles = ET.fromstring(styles_xml)
    cells = {}
    for cell in sheet.iter(NS + 'c'):
        style = int(cell.get('s', '0'))
        kind = cell.get('t') or 'n'
        if kind == 'inlineStr':
            value = ''.join(node.text or '' for node in cell.iter(NS + 't'))
            cells[cell.get('r')] = (value, 's', style)
        elif cell.find(NS + 'v') is not None:
            raw = cell.find(NS + 'v').text or ''
            value = int(raw) if raw.isdigit() or (raw.startswith('-') and raw[1:].isdigit()) else float(raw)
            cells[cell.get('r')] = (value, 'n', style)
        else:
            cells[cell.get('r')] = (None, 'blank', style)
    fills = list(styles.find(NS + 'fills'))
    fonts = list(styles.find(NS + 'fonts'))
    xfs = list(styles.find(NS + 'cellXfs'))

    def paint(style_index):
        xf = xfs[style_index]
        fill = fills[int(xf.get('fillId', '0'))]
        fg = fill.find('.//' + NS + 'fgColor')
        font = fonts[int(xf.get('fontId', '0'))]
        color = font.find(NS + 'color')
        return {
            'fill': fg.get('rgb') if fg is not None else None,
            'font': color.get('rgb') if color is not None else None,
            'border': xf.get('borderId'),
            'quote': xf.get('quotePrefix'),
            'numFmt': xf.get('numFmtId'),
        }

    merges = [node.get('ref') for node in sheet.iter(NS + 'mergeCell')]
    return {
        'cells': cells,
        'paint': paint,
        'merges': merges,
        'names': names,
        'sheet_xml': sheet_xml.decode('utf-8'),
        'workbook_xml': workbook_xml.decode('utf-8'),
        'sheet': sheet,
    }


class _H:
    def __init__(self, user, path):
        self.user = user
        self.path = path
        self.headers = {}


class ShiftXlsxExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 't.sqlite3'
        self.c = sqlite3.connect(self.db)
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, login TEXT, name TEXT, password TEXT, "
            "role TEXT, manage INTEGER, overview INTEGER, active INTEGER, must_change INTEGER, "
            "version INTEGER, mark_kintai INTEGER DEFAULT 0)"
        )
        kintai.bind(Path(self.tmp.name))
        kintai.schema(self.c)
        self.c.execute('DELETE FROM kintai_people')
        self.c.execute(
            'INSERT INTO kintai_people(employee_code, employment, display_name, source_file, active, sort_order, retired) '
            'VALUES (?,?,?,?,1,1,0)',
            ('SECRET991', 'regular', 'DBにだけいる秘密太郎', 'SECRET-ATTENDANCE-PATH'),
        )
        person_id = self.c.execute('SELECT id FROM kintai_people').fetchone()[0]
        self.c.execute(
            'INSERT INTO kintai_monthly(person_id, year, month, ordinary_minutes, status, imported_at, source_mtime, error) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (person_id, 2026, 11, 424242, 'ok', '2026-11-01', 'SECRET-ATTENDANCE-PATH', ''),
        )
        self.c.execute(
            'INSERT INTO kintai_shift_work_memos(bind_key, memo, updated_at, updated_by) VALUES (?,?,?,?)',
            ('pid:1', '秘密メモZZZ', '2026-11-01', 'db'),
        )
        secret_people = json.dumps([
            {'key': 'regular:SECRET991', 'displayName': '保存だけ秘密花子', 'employment': 'regular', 'source': 'roster'}
        ], ensure_ascii=False)
        secret_cells = json.dumps({'regular:SECRET991': {'3': {'kind': 'task', 'text': 'ピ'}}}, ensure_ascii=False)
        self.c.execute(
            'INSERT INTO kintai_shift_sheets(year, month, version, updated_at, updated_by, people_json, cells_json) '
            'VALUES (?,?,?,?,?,?,?)',
            (2026, 10, 1, '2026-10-01', 'db', secret_people, json.dumps({
                'regular:SECRET991': {'3': {'kind': 'time', 'text': '十月だけの記号QQ'}}
            }, ensure_ascii=False)),
        )
        self.c.execute(
            'INSERT INTO kintai_shift_sheets(year, month, version, updated_at, updated_by, people_json, cells_json) '
            'VALUES (?,?,?,?,?,?,?)',
            (2026, 11, 4, '2026-11-02 09:00:00', 'db', secret_people, secret_cells),
        )
        self.c.commit()
        self.user = {'id': 1, 'name': '主', 'login': 'o@ex.com', 'role': 'owner', 'mark_kintai': 1}
        self.days = kintai_shift.month_calendar(2026, 11)
        self.sunday = next(d['day'] for d in self.days if d['weekday'] == 6)
        self.saturday = next(d['day'] for d in self.days if d['weekday'] == 5)
        self.holiday = next(d for d in self.days if d['holidayName'])
        self.weekdays = [d['day'] for d in self.days if d['kind'] == 'weekday' and d['day'] != self.holiday['day']]

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def _dump(self):
        return hashlib.sha256(''.join(self.c.iterdump()).encode()).hexdigest()

    def _payload(self, **extra):
        wd = self.weekdays
        people = [
            {'key': 'part:p1', 'displayName': '佐藤花子', 'employment': 'part', 'source': 'roster',
             'employeeCode': 'SECRET991', 'personId': 424242},
            {'key': 'shift:s1', 'displayName': '=1+1', 'employment': 'shift', 'source': 'shift',
             'employeeCode': 'SECRET991'},
            {'key': 'regular:r1', 'displayName': '山田<太郎>&"花"\x01髙橋', 'employment': 'regular', 'source': 'roster'},
            {'key': 'contract:c1', 'displayName': '+1+1', 'employment': 'contract', 'source': 'roster'},
            {'key': 'history:h1', 'displayName': '-2', 'employment': '', 'source': 'history'},
            {'key': 'part:at', 'displayName': '@SUM(A1)', 'employment': 'part', 'source': 'roster'},
        ]
        cells = {
            'part:p1': {
                str(self.sunday): {'kind': 'off', 'text': '=1+1'},
                str(self.saturday): {'kind': 'unknown', 'text': '？'},
                str(wd[0]): {'kind': 'time', 'text': '15:00まで'},
                str(wd[1]): {'kind': 'task', 'text': 'ピ'},
                str(wd[2]): {'kind': 'task', 'text': 'バ'},
                str(wd[3]): {'kind': 'task', 'text': '貼'},
                str(wd[4]): {'kind': 'off', 'text': '×'},
            },
            'shift:s1': {str(wd[5]): {'kind': 'time', 'text': '=NOW()'}},
            'regular:r1': {str(wd[4]): {'kind': 'off', 'text': '×'}},
        }
        memos = [
            {'key': 'part:p1', 'memo': '週5\nLEOC'},
            {'key': 'shift:s1', 'memo': '@SUM(A1)'},
            {'key': 'contract:c1', 'memo': '契約 週5 LEOC'},
            {'key': 'regular:ghost', 'memo': '幽霊メモQQ'},
        ]
        payload = {
            'year': 2026,
            'month': 11,
            'exists': True,
            'pending': True,
            'shiftPending': True,
            'memoPending': True,
            'updatedAt': '2026-11-02 09:00:00',
            'updatedBy': '=1+1',
            'people': people,
            'cells': cells,
            'memos': memos,
            'roster': [{'displayName': '名簿外の秘密人', 'employeeCode': 'SECRET991'}],
            'secretHoliday': '秘密祝日XYZ',
        }
        payload.update(extra)
        return payload

    def _post(self, user, payload):
        return kintai.handle_post(_H(user, '/api/kintai/shift/xlsx'), self.c, '/api/kintai/shift/xlsx', payload)

    def _ref(self, day, row):
        return f'{kintai_shift_xlsx._col(day + 1)}{row}'

    def test_screen_values_dates_order_symbols_and_no_write(self):
        before = self._dump()
        res = self._post(self.user, self._payload())
        self.assertEqual(res[0], 200)
        self.assertEqual(res[2], kintai_shift_xlsx.XLSX)
        self.assertEqual(self._dump(), before)
        saved = self.c.execute(
            'SELECT version, people_json FROM kintai_shift_sheets WHERE year=2026 AND month=11'
        ).fetchone()
        self.assertEqual(saved['version'], 4)
        self.assertIn('保存だけ秘密花子', saved['people_json'])

        cd = res[3]['Content-Disposition']
        cd.encode('latin-1')
        matched = re.search(r"filename\*=UTF-8''(\S+)", cd)
        self.assertEqual(unquote(matched.group(1)), 'マークシフト表_2026-11.xlsx')
        self.assertIn('filename="shift_2026-11.xlsx"', cd)

        book = read_xlsx(res[1])
        cells = book['cells']
        self.assertNotIn('xl/media/', ''.join(book['names']))
        self.assertNotIn('<f>', book['sheet_xml'])
        self.assertNotIn('<f ', book['sheet_xml'])
        self.assertEqual(cells['A1'][0], '2026年 11月 マーク シフト表')
        self.assertIn('空欄：出勤', cells['A2'][0])
        self.assertIn('画面の未保存（シフト・勤務メモ）', cells['A3'][0])
        self.assertIn('データベースは更新していません', cells['A3'][0])
        self.assertIn('3日 文化の日', cells['A3'][0])
        self.assertIn('23日 勤労感謝の日', cells['A3'][0])
        self.assertNotIn('秘密祝日XYZ', cells['A3'][0])
        self.assertEqual(cells['A4'][0], '勤務メモ')
        self.assertEqual(cells['B4'][0], '氏名')
        self.assertIn('A1:AF1', book['merges'])  # 30日 + メモ + 氏名 = 32列
        self.assertIn('A4:A5', book['merges'])
        self.assertIn('state="frozen"', book['sheet_xml'])
        self.assertIn('orientation="landscape"', book['sheet_xml'])
        self.assertIn('fitToWidth="1"', book['sheet_xml'])
        self.assertIn("'2026年11月'!$4:$5", book['workbook_xml'])

        self.assertEqual([cells[f'B{row}'][0] for row in range(6, 12)], [
            '佐藤花子', '=1+1', '山田<太郎>&"花"髙橋', '+1+1', '-2', '@SUM(A1)',
        ])
        self.assertEqual(cells['A6'][0], '週5\nLEOC')
        self.assertEqual(cells['A7'][0], '@SUM(A1)')
        self.assertEqual(cells['A9'][0], '契約 週5 LEOC')
        self.assertEqual(cells[f'A{8}'][1], 'blank')
        self.assertEqual(cells[f'A{10}'][1], 'blank')

        for day in self.days:
            ref = self._ref(day['day'], 4)
            self.assertEqual(cells[ref][0], kintai_shift_xlsx.excel_serial(date(2026, 11, day['day'])), ref)
            self.assertEqual(cells[ref][1], 'n', ref)
            self.assertEqual(cells[self._ref(day['day'], 5)][0], day['weekdayLabel'])
        self.assertNotIn(self._ref(31, 4), cells)
        self.assertEqual(len(self.days), 30)

        # 佐藤花子の記号。15:00まで は画面どおり 15:00。off に混ぜた数式は × になる。
        self.assertEqual(cells[self._ref(self.sunday, 6)][0], '×')
        self.assertEqual(cells[self._ref(self.saturday, 6)][0], '？')
        self.assertEqual(cells[self._ref(self.weekdays[0], 6)][0], '15:00')
        self.assertEqual(cells[self._ref(self.weekdays[1], 6)][0], 'ピ')
        self.assertEqual(cells[self._ref(self.weekdays[2], 6)][0], 'バ')
        self.assertEqual(cells[self._ref(self.weekdays[3], 6)][0], '貼')
        self.assertEqual(cells[self._ref(self.weekdays[4], 6)][0], '×')
        self.assertEqual(cells[self._ref(self.holiday['day'], 6)][1], 'blank')
        self.assertEqual(cells[self._ref(self.weekdays[5], 7)][0], '=NOW()')
        self.assertEqual(cells[self._ref(self.weekdays[5], 7)][1], 's')

        blob = res[1]
        for secret in (
            'SECRET991', '424242', '秘密メモZZZ', '保存だけ秘密花子', 'DBにだけいる秘密太郎',
            '十月だけの記号QQ', '名簿外の秘密人', '幽霊メモQQ', 'SECRET-ATTENDANCE-PATH',
            '齊藤', 'regular:SECRET991',
        ):
            self.assertNotIn(secret.encode('utf-8'), blob, secret)

        # 色: 土日祝の見出し、空欄の土日、×の日曜だけ赤、記号は記号色、氏名は雇用区分色
        sun_head = book['paint'](cells[self._ref(self.sunday, 4)][2])
        sat_head = book['paint'](cells[self._ref(self.saturday, 4)][2])
        hol_head = book['paint'](cells[self._ref(self.holiday['day'], 4)][2])
        wd_head = book['paint'](cells[self._ref(self.weekdays[0], 4)][2])
        self.assertEqual(sun_head['fill'], 'FFFDECEB')
        self.assertEqual(sat_head['fill'], 'FFE8F2FB')
        self.assertEqual(hol_head['fill'], 'FFFDECEB')
        self.assertEqual(wd_head['fill'], 'FFF3F7F3')
        self.assertEqual(wd_head['numFmt'], '165')
        self.assertEqual(book['paint'](cells[self._ref(self.sunday, 6)][2])['fill'], 'FFFDECEB')
        self.assertEqual(book['paint'](cells[self._ref(self.sunday, 6)][2])['font'], 'FF9D3434')
        self.assertEqual(book['paint'](cells[self._ref(self.weekdays[4], 6)][2])['font'], 'FF333333')
        self.assertIsNone(book['paint'](cells[self._ref(self.weekdays[4], 6)][2])['fill'])
        self.assertEqual(book['paint'](cells[self._ref(self.saturday, 6)][2])['fill'], 'FFFAF6EA')
        self.assertEqual(book['paint'](cells[self._ref(self.weekdays[1], 6)][2])['fill'], 'FFEEF7F1')
        self.assertEqual(book['paint'](cells[self._ref(self.weekdays[0], 6)][2])['fill'], 'FFF3F8FF')
        self.assertEqual(book['paint'](cells['B6'][2])['fill'], 'FFF7F0E8')  # パート
        self.assertEqual(book['paint'](cells['B7'][2])['fill'], 'FFF3F7FB')  # シフト専用
        self.assertEqual(book['paint'](cells['B8'][2])['fill'], 'FFE6F2EA')  # 社員
        self.assertEqual(book['paint'](cells['B9'][2])['fill'], 'FFEEF3F8')  # 契約
        self.assertEqual(book['paint'](cells['B10'][2])['fill'], 'FFFAF7F3')  # 過去
        self.assertEqual(book['paint'](cells['B7'][2])['quote'], '1')
        self.assertEqual(book['paint'](cells['A6'][2])['quote'], '1')
        self.assertEqual(book['paint'](cells[self._ref(self.weekdays[0], 6)][2])['border'], '1')
        self.assertEqual(book['paint'](cells['A1'][2])['border'], '0')

    def test_saved_label_leap_year_and_openpyxl_text(self):
        res = self._post(self.user, self._payload(pending=False, shiftPending=False, memoPending=False))
        label = read_xlsx(res[1])['cells']['A3'][0]
        self.assertIn('保存済みの表示内容', label)
        self.assertIn('最終保存：2026-11-02 09:00:00', label)
        self.assertIn('（=1+1）', label)
        self.assertNotIn('未保存', label.split('祝日')[0])

        before_leap = self._dump()
        leap = kintai_shift_xlsx.export_view({'year': 2024, 'month': 2, 'people': [], 'cells': {}})
        plain = kintai_shift_xlsx.export_view({'year': 2026, 'month': 2, 'people': [], 'cells': {}})
        leap_book = read_xlsx(leap[1])
        plain_book = read_xlsx(plain[1])
        self.assertEqual(leap_book['cells'][kintai_shift_xlsx._col(29 + 1) + '4'][0], kintai_shift_xlsx.excel_serial(date(2024, 2, 29)))
        self.assertNotIn(kintai_shift_xlsx._col(29 + 1) + '4', plain_book['cells'])
        self.assertEqual(plain_book['cells'][kintai_shift_xlsx._col(28 + 1) + '4'][0], kintai_shift_xlsx.excel_serial(date(2026, 2, 28)))
        self.assertIn('行がありません', leap_book['cells']['A6'][0])
        self.assertEqual(self._dump(), before_leap)

        try:
            import openpyxl
        except ImportError:
            self.skipTest('openpyxl not installed')
        wb = openpyxl.load_workbook(BytesIO(res[1]))
        ws = wb.active
        self.assertEqual(ws['A1'].value, '2026年 11月 マーク シフト表')
        self.assertEqual(ws['B7'].value, '=1+1')
        self.assertNotEqual(ws['B7'].data_type, 'f')
        self.assertEqual(ws['A7'].value, '@SUM(A1)')
        self.assertNotEqual(ws['A7'].data_type, 'f')
        self.assertEqual(ws['B9'].value, '+1+1')
        self.assertNotEqual(ws['B9'].data_type, 'f')
        self.assertEqual(ws['B10'].value, '-2')
        self.assertNotEqual(ws['B10'].data_type, 'f')
        self.assertEqual(ws['B11'].value, '@SUM(A1)')
        self.assertNotEqual(ws['B11'].data_type, 'f')
        shown = ws[self._ref(self.weekdays[5], 7)].value
        self.assertEqual(shown, '=NOW()')
        self.assertNotEqual(ws[self._ref(self.weekdays[5], 7)].data_type, 'f')
        self.assertEqual(ws[self._ref(self.weekdays[0], 6)].value, '15:00')
        header = ws[self._ref(1, 4)].value
        if isinstance(header, datetime):
            self.assertEqual(header.date(), date(2026, 11, 1))
        else:
            self.assertEqual(header, kintai_shift_xlsx.excel_serial(date(2026, 11, 1)))
        wb_only = openpyxl.load_workbook(BytesIO(res[1]), data_only=True)
        self.assertEqual(wb_only.active['B7'].value, '=1+1')
        self.assertNotEqual(wb_only.active['B7'].value, 2)

    def test_permission_matches_shift_view_and_rejects_bad_month(self):
        payload = {'year': 2026, 'month': 11, 'people': [], 'cells': {}}
        before = self._dump()
        cases = [
            None,
            {'id': 2, 'name': '営', 'role': 'sales', 'manage': 0, 'overview': 0, 'mark_kintai': 0},
            {'id': 3, 'name': '現', 'role': 'floor3', 'manage': 0, 'overview': 0, 'mark_kintai': 0},
            {'id': 4, 'name': 'マ', 'role': 'sales', 'manage': 0, 'overview': 0, 'mark_kintai': 1},
            {'id': 5, 'name': '管', 'role': 'sales', 'manage': 1, 'overview': 0, 'mark_kintai': 0},
            {'id': 6, 'name': '覧', 'role': 'sales', 'manage': 0, 'overview': 1, 'mark_kintai': 0},
            self.user,
        ]
        for user in cases:
            view = kintai.handle_get(_H(user, '/api/kintai/shift?year=2026&month=11'), self.c, '/api/kintai/shift')
            exported = self._post(user, payload)
            self.assertEqual(view[0], exported[0], user)
            if view[0] == 403:
                self.assertEqual(view[1], exported[1])
            else:
                self.assertEqual(exported[0], 200)
                self.assertIsInstance(exported[1], bytes)
        self.assertEqual(self._dump(), before)
        with self.assertRaises(ValueError):
            self._post(self.user, {'year': 2026, 'month': 13, 'people': []})
        self.assertEqual(
            kintai.handle_get(_H(self.user, '/api/kintai/shift/xlsx?year=2026&month=11'), self.c, '/api/kintai/shift/xlsx')[0],
            404,
        )

    def test_button_posts_screen_without_saving(self):
        self.assertIn('id="shiftexport"', HTML)
        self.assertIn('>Excel出力<', HTML)
        self.assertLess(HTML.index('id="shiftprint"'), HTML.index('id="shiftexport"'))
        self.assertIn('data-shift-group="save"', HTML)
        self.assertIn('保存・印刷', HTML)

        def fn(name):
            matched = re.search(rf'function {name}\([^)]*\)\s*\{{', JS)
            self.assertIsNotNone(matched, name)
            start = JS.find('{', matched.start())
            depth = 0
            for index in range(start, len(JS)):
                depth += {'{': 1, '}': -1}.get(JS[index], 0)
                if depth == 0:
                    return JS[matched.start():index + 1]
            raise AssertionError(name)

        body = fn('exportShiftXlsx')
        self.assertIn('/api/kintai/shift/xlsx', body)
        self.assertIn("method: 'POST'", body)
        self.assertIn('buildShiftExportPayload', body)
        self.assertNotIn('saveShift', body)
        self.assertNotIn('saveWorkMemos', body)
        self.assertNotIn('shiftFetch', body)
        self.assertNotIn('employeeCode', body)
        self.assertNotIn('personId', body)
        script = '\n'.join(fn(name) for name in (
            'exportNameFromInput', 'exportMemoFromInput', 'buildShiftExportPayload',
            'exportButtonState', 'filenameFromDisposition',
        )) + r'''
const payload = buildShiftExportPayload({
  year: 2026, month: 11, exists: false, pending: true, shiftPending: true, memoPending: false,
  people: [{ key: 'regular:r1', displayName: '  =1+1  ', employeeCode: 'SECRET991', personId: 424242, employment: 'regular', source: 'roster' }],
  cells: { 'regular:r1': { '3': { kind: 'time', text: '15:00まで' } } },
  memoOf: () => '週5\nLEOC',
});
console.log(JSON.stringify({
  payload,
  keys: Object.keys(payload.people[0]).sort(),
  empty: exportNameFromInput('   '),
  memo: exportMemoFromInput('=cmd'),
  none: exportButtonState(null, false, false),
  dirty: exportButtonState({ exists: true }, true, false),
  memoDirty: exportButtonState({ exists: true }, false, true),
  fresh: exportButtonState({ exists: false }, false, false),
  saved: exportButtonState({ exists: true }, false, false),
  fn: filenameFromDisposition("attachment; filename=\"shift_2026-11.xlsx\"; filename*=UTF-8''%E3%83%9E%E3%83%BC%E3%82%AF%E3%82%B7%E3%83%95%E3%83%88%E8%A1%A8_2026-11.xlsx", 'x'),
}));
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out['payload']['people'][0]['displayName'], '=1+1')
        self.assertEqual(out['keys'], ['displayName', 'employment', 'key', 'source'])
        self.assertNotIn('SECRET991', json.dumps(out['payload']))
        self.assertNotIn('424242', json.dumps(out['payload']))
        self.assertEqual(out['payload']['cells']['regular:r1']['3']['text'], '15:00まで')
        self.assertEqual(out['payload']['memos'][0]['memo'], '週5\nLEOC')
        self.assertTrue(out['payload']['pending'])
        self.assertEqual(out['empty'], '（未入力）')
        self.assertEqual(out['memo'], '=cmd')
        self.assertTrue(out['none']['disabled'])
        self.assertFalse(out['dirty']['disabled'])
        self.assertIn('未保存', out['dirty']['title'])
        self.assertIn('保存はしません', out['dirty']['title'])
        self.assertIn('未保存', out['memoDirty']['title'])
        self.assertIn('未保存の月', out['fresh']['title'])
        self.assertIn('保存済み', out['saved']['title'])
        self.assertEqual(out['fn'], 'マークシフト表_2026-11.xlsx')


if __name__ == '__main__':
    unittest.main()
