"""Read TOT daily CSV and prepare three time columns for clipboard use."""
import base64
import binascii
import csv
import ipaddress
import os
import subprocess
import re
import unicodedata
from datetime import date, timedelta
from io import StringIO

from kintai_touch_on_time import STAFF


def norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def decode_file(raw, limit):
    if not isinstance(raw, str) or len(raw) > limit * 4 // 3 + 4:
        raise ValueError('ファイルが大きすぎるか、読み込めません。')
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('ファイルを読み込めません。選び直してください。') from error
    if not data or len(data) > limit:
        raise ValueError('ファイルのサイズを確認してください。')
    return data


def time_text(value):
    text = unicodedata.normalize('NFKC', str(value or '')).strip()
    if not text:
        return ''
    match = re.fullmatch(r'(\d{1,2}):(\d{2})(?::00)?', text)
    if not match or int(match[1]) > 47 or int(match[2]) > 59:
        raise ValueError(f'時刻「{text[:40]}」を確認してください（例 9:00）。')
    return f'{int(match[1])}:{match[2]}'


def parse_csv(raw):
    blob = decode_file(raw, 3_000_000)
    for encoding in ('utf-8-sig', 'cp932'):
        try:
            text = blob.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('CSVはUTF-8またはShift_JISで保存してください。')
    reader = csv.reader(StringIO(text, newline=''))
    header = next(reader, [])
    required = ['名前', '日時', '出勤時刻(時刻のみ)', '休憩時間', '退勤時刻(時刻のみ)']
    headings = [norm(x) for x in header]
    if any(headings.count(norm(x)) != 1 for x in required):
        raise ValueError('CSVには「名前・日時・出勤時刻(時刻のみ)・休憩時間・退勤時刻(時刻のみ)」が必要です。出勤予定時刻ではなく実際の出勤時刻を出力してください。')
    indexes = [headings.index(norm(x)) for x in required]
    people = {}
    for line, row in enumerate(reader, 2):
        if not any(row):
            continue
        if line > 20001 or len(row) != len(header):
            raise ValueError(f'CSVの{line}行目の列数・件数を確認してください。')
        name, raw_date, start, rest, end = [row[i].strip() for i in indexes]
        if not name or len(name) > 80:
            raise ValueError(f'{line}行目の氏名を確認してください。')
        match = re.fullmatch(r'(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:\([^)]*\))?', unicodedata.normalize('NFKC', raw_date))
        try:
            day = date(*map(int, match.groups())) if match else None
            if day is None:
                raise ValueError()
        except ValueError as error:
            raise ValueError(f'{name}さん・CSV{line}行目の日付／時間を確認してください。{error}') from error
        warnings = []
        parsed = []
        for label, value in [('出勤', start), ('退勤', end)]:
            try:
                parsed.append(time_text(value))
            except ValueError:
                parsed.append(value[:80])
                warnings.append(f'原CSVの{label}欄「{value[:40]}」を確認')
        start, end = parsed
        if rest:
            if not re.fullmatch(r'\d+(?:\.0+)?', rest) or float(rest) > 1440:
                warnings.append(f'原CSVの休憩欄「{rest[:40]}」を確認')
                rest = rest[:80]
            else:
                count = int(float(rest))
                rest = f'{count // 60}:{count % 60:02d}'
        key = norm(name)
        item = people.setdefault(key, {'name': name, 'days': {}})
        if day.isoformat() in item['days']:
            raise ValueError(f'{name}さんの{day}が重複しています。同姓同名や複数勤務先のデータを確認してください。')
        item['days'][day.isoformat()] = {'start': start, 'rest': rest, 'end': end, 'sourceWarning': '／'.join(warnings)}
    if not people:
        raise ValueError('CSVに日別データがありません。')
    return people


def closing_month(day):
    first = day.replace(day=1)
    return (first if day.day <= 15 else (first + timedelta(days=32)).replace(day=1)).strftime('%Y-%m')


def import_preview(payload):
    people = parse_csv(payload.get('csvData'))
    months = sorted({closing_month(date.fromisoformat(d)) for p in people.values() for d in p['days']})
    return 200, {'people': [{'key': key, 'name': p['name']} for key, p in people.items()], 'months': months}


def period(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'20\d{2}-\d{2}', value):
            raise ValueError()
        end = date.fromisoformat(value + '-15')
    except ValueError as error:
        raise ValueError('締め月を選んでください。') from error
    start = (end.replace(day=1) - timedelta(days=1)).replace(day=16)
    return start, end


def prepare(payload):
    people = parse_csv(payload.get('csvData'))
    person = people.get(payload.get('person'))
    if not person:
        raise ValueError('従業員を選択してください。')
    start, end = period(payload.get('month'))
    dates = [start + timedelta(days=x) for x in range((end - start).days + 1)]
    rows = []
    for day in dates:
        source = person['days'].get(day.isoformat())
        row = {'date': day.isoformat(), 'start': '', 'rest': '', 'end': '', 'note': ''}
        if source is None:
            row['note'] = 'CSVにこの日付がありません。'
        else:
            row.update(source)
            if bool(row['start']) != bool(row['end']):
                row['note'] = '出勤または退勤の打刻が不足しています。'
            elif row['start'] and not row['rest']:
                row['note'] = '休憩時間が空欄です。'
            elif not row['start'] and not row['end'] and row['rest']:
                row['note'] = '出退勤がない日の休憩を確認してください。'
            elif not row['start'] and not row['end']:
                row['note'] = '出退勤なし（休み・未入力の確認）'
            row['note'] = '／'.join(x for x in [source.get('sourceWarning'), row['note']] if x)
        rows.append(row)
    config = STAFF.get(norm(person['name']))
    return 200, {'name': person['name'], 'month': payload['month'], 'start': start.isoformat(),
                 'end': end.isoformat(), 'rows': rows,
                 'defaultBreak': config[2] if config else None}


ATTENDANCE_FOLDER = "\\\\Desktop-iu7d38q\\e\\札幌発寒支店共有\\札幌発寒支店\\札幌発寒支店管理職以外閲覧禁止\\※閲覧禁止※\\出勤簿   2026年度版"


def open_folder(handler):
    """Open only the fixed folder, only for a browser on the server PC."""
    try:
        local = ipaddress.ip_address(handler.client_address[0]).is_loopback
    except (AttributeError, ValueError, IndexError):
        local = False
    if not local:
        return 403, {'error': '別のPCからは直接開けません。「パスをコピー」を使い、エクスプローラーのアドレス欄へ貼り付けてください。'}
    if os.name != 'nt':
        return 400, {'error': 'フォルダーを開く操作はWindows用です。「パスをコピー」を使ってください。'}
    try:
        subprocess.Popen(['explorer.exe', ATTENDANCE_FOLDER], shell=False)
    except OSError as error:
        raise ValueError('フォルダーを開けませんでした。「パスをコピー」を使ってください。') from error
    return 200, {'message': 'エクスプローラーへフォルダーを開く指示を送りました。'}
