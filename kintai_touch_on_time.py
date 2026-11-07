"""Touch On Time CSV export from the displayed shift sheet. No database writes."""
import csv
import re
from datetime import date
from io import StringIO

import kintai_shift_xlsx

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


def build(payload):
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
