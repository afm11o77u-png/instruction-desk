"""Touch On Time CSV export from the displayed shift sheet. No database writes."""
import csv
import base64
import re
from datetime import date, timedelta
from io import StringIO, BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

import kintai_shift_xlsx
import kintai_shift

# Explicitly approved department members; external employee codes are text.
STAFF = {
    '今城卓也': ('31002179', '17:40', 70),
    '菊池敏子': ('31002036', '16:00', 70),
    '伊藤良子': ('31002183', '17:40', 70),
    '須永結香': ('31002184', '17:40', 70),
    '小川春佳': ('31002161', '17:00', 70),
    '田中明美': ('31002124', '15:00', 0),
    '八島里恵': ('31002058', '17:00', 70),
    '廣瀬愛': ('31002099', '15:00', 10),
    '小山さとみ': ('31002131', '15:00', 10),
    '南田久美子': ('31002174', '17:00', 70),
    '加藤篤郎': ('00001804', '17:40', 70),
}
HEADER = ['#従業員コード※', '勤務日※', '出勤所属コード', '出勤予定',
          '休憩予定時間', '退勤予定', '勤務日種別コード']


def from_excel(encoded):
    """Read this application's exported shift workbook, without executing formulas."""
    if not isinstance(encoded, str) or len(encoded) > 4_000_000:
        raise ValueError('Excelファイルは3MB以下にしてください。')
    ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    try:
        blob = base64.b64decode(encoded, validate=True)
        with ZipFile(BytesIO(blob)) as z:
            if sum(i.file_size for i in z.infolist()) > 20_000_000:
                raise ValueError('Excelファイルが大きすぎます。')
            shared = []
            if 'xl/sharedStrings.xml' in z.namelist():
                shared = [''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml'))]
            root = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            values = {}
            for cell in root.iter(ns + 'c'):
                if cell.find(ns + 'f') is not None:
                    raise ValueError('数式を含むファイルは変換できません。シフト表からExcelを出力し直してください。')
                raw = cell.findtext(ns + 'v') or ''
                if cell.get('t') == 'inlineStr':
                    raw = ''.join(t.text or '' for t in cell.iter(ns + 't'))
                elif cell.get('t') == 's':
                    raw = shared[int(raw)]
                values[cell.get('r')] = raw
    except (BadZipFile, KeyError, IndexError, ET.ParseError, TypeError, ValueError) as error:
        raise ValueError('シフト表から出力した.xlsxファイルを選択してください。' + str(error)) from error
    match = re.search(r'(20\d{2})年\s*(\d{1,2})月', values.get('A1', ''))
    if not match or values.get('B4') != '氏名':
        raise ValueError('このシフト表から出力したExcelファイルを選択してください。')
    year, month = map(int, match.groups())
    days = kintai_shift.month_calendar(year, month)
    for day in range(1, len(days) + 1):
        col = kintai_shift_xlsx._col(day + 1)
        raw = values.get(col + '4', '')
        expected = date(year, month, day)
        try:
            actual = date(1899, 12, 30) + timedelta(days=float(raw))
        except (ValueError, OverflowError):
            try:
                actual = date.fromisoformat(raw[:10])
            except ValueError as error:
                raise ValueError('Excelの日付見出しを確認してください。') from error
        if actual != expected:
            raise ValueError('Excelの日付と対象月が一致しません。')
    people, cells = [], {}
    for row in range(6, 406):
        name = values.get(f'B{row}', '').strip()
        if not name:
            continue
        key = f'shift:excel{row}'
        people.append({'key': key, 'displayName': name, 'source': 'shift'})
        marks = {}
        for day in range(1, len(days) + 1):
            text = values.get(kintai_shift_xlsx._col(day + 1) + str(row), '').strip()
            if not text:
                continue
            kind = 'off' if text == '×' else 'unknown' if text in ('?', '？') else 'task' if text in kintai_shift.TASK_TEXTS else 'time'
            marks[str(day)] = {'kind': kind, 'text': text}
        cells[key] = marks
    return {'year': year, 'month': month, 'people': people, 'cells': cells}


def build(payload):
    if payload.get('excelData') is not None:
        payload = from_excel(payload['excelData'])
    view = kintai_shift_xlsx.build_view(payload)
    rows, members, excluded, seen = [], [], [], set()
    for person in view['people']:
        name = person['displayName']
        config = STAFF.get(re.sub(r'\s+', '', name))
        if config is None:
            excluded.append(name)
            continue
        code, end, rest = config
        if code in seen:
            raise ValueError(f'{name}さんが重複しています。シフト表の氏名を確認してください。')
        seen.add(code)
        counts = {'work': 0, 'holiday': 0, 'unknown': 0, 'off': 0}
        by_day = view['cells'].get(person['key'], {})
        for day in range(1, len(view['days']) + 1):
            cell = by_day.get(str(day), {})
            kind = cell.get('kind')
            current = date(view['year'], view['month'], day)
            if kind == 'unknown':
                counts['unknown'] += 1
                continue
            if kind == 'off':
                if current.weekday() == 6:
                    rows.append([code, current.strftime('%Y%m%d'), '', '', '', '', '2'])
                    counts['holiday'] += 1
                else:
                    counts['off'] += 1
                continue
            finish = end
            if kind == 'time':
                text = str(cell.get('text', '')).strip()
                match = re.fullmatch(r'(\d{1,2}):(\d{2})(?:\s*まで)?', text)
                if not match or int(match[1]) > 23 or int(match[2]) > 59:
                    raise ValueError(f'{name}さんの{day}日の退勤予定を「15:00」の形式にしてください。')
                finish = f'{int(match[1]):02d}:{match[2]}'
            minutes = int(finish[:2]) * 60 + int(finish[3:]) - 540
            if minutes <= rest:
                raise ValueError(f'{name}さんの{day}日は退勤時刻と休憩時間を確認してください。')
            rows.append([code, current.strftime('%Y%m%d'), '', '09:00', str(rest), finish, '1'])
            counts['work'] += 1
        members.append({'name': name, 'code': code, 'start': '09:00', 'end': end,
                        'breakMinutes': rest, **counts})
    if not rows:
        raise ValueError('CSVに出力できる対象者・予定がありません。')
    return view, rows, {'members': members, 'excluded': excluded, 'records': len(rows),
                        'year': view['year'], 'month': view['month'],
                        'pending': bool(payload.get('pending'))}


def preview(payload):
    return 200, build(payload)[2]


def export_view(payload):
    view, rows, _ = build(payload)
    stream = StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\r\n')
    writer.writerow(HEADER)
    writer.writerows(rows)
    name = f"TouchOnTime_schedule_{view['year']:04d}-{view['month']:02d}.csv"
    return 200, stream.getvalue().encode('cp932'), 'text/csv; charset=Shift_JIS', {
        'Content-Disposition': f'attachment; filename="{name}"'}
