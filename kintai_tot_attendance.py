"""Read TOT daily CSV and prepare three time columns for an existing workbook."""
import base64
import binascii
import csv
import re
import unicodedata
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET

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


def col_name(value):
    out = ''
    while value:
        value, mod = divmod(value - 1, 26)
        out = chr(65 + mod) + out
    return out


def workbook_layout(raw, year, month, name, dates, read_grid):
    blob = decode_file(raw, 5_000_000)
    try:
        with ZipFile(BytesIO(blob)) as book:
            if sum(x.file_size for x in book.infolist()) > 30_000_000:
                raise ValueError('出勤簿の展開サイズが大きすぎます。')
            grid = read_grid(blob)
            candidates = [k for k in grid if k in (f'{year % 100}.{month}', f'{year}.{month}')]
            if len(candidates) != 1:
                raise ValueError('出勤簿に対象月のシート（例 26.11）が見つかりません。')
            sheet = candidates[0]
            cells = grid[sheet]
            if norm(name) not in {norm(v) for (r, c), v in cells.items() if r <= 2 and isinstance(v, str)}:
                raise ValueError('CSVで選んだ従業員と出勤簿の氏名が一致しません。本人の出勤簿を選んでください。')
            starts = [c for (r, c), v in cells.items() if r in (3, 4) and norm(v).startswith('始業時間')]
            starts = [c for c in starts if any(norm(cells.get((r, c + 1))).startswith('休憩時間') for r in (3, 4)) and any(norm(cells.get((r, c + 2))).startswith('終業時間') for r in (3, 4))]
            if len(set(starts)) != 1:
                raise ValueError('出勤簿の始業・休憩・終業の3列を特定できません。')
            col = starts[0]
            matches = []
            for first in range(5, 47):
                actual = []
                for offset in range(len(dates)):
                    value = cells.get((first + offset, 1))
                    if not isinstance(value, (int, float)) or not 1 <= value <= 100000:
                        break
                    actual.append((datetime(1899, 12, 30) + timedelta(days=value)).strftime('%m-%d'))
                if actual == [d.strftime('%m-%d') for d in dates]:
                    matches.append(first)
            if len(matches) != 1:
                raise ValueError('出勤簿の日付と締め期間が一致しません。日付セルの計算結果を確認してください。')
            first = matches[0]
            expected_year = dates[0].year
            actual_first = datetime(1899, 12, 30) + timedelta(days=cells[(first, 1)])
            ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
            # Use the workbook's relationship map to inspect the actual destination cells.
            wb = ET.fromstring(book.read('xl/workbook.xml'))
            rels = {x.get('Id'): x.get('Target') for x in ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))}
            sh = next(x for x in wb.find(ns + 'sheets') if x.get('name') == sheet)
            target = rels[sh.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            xml = ET.fromstring(book.read(path))
            destinations = {f'{col_name(c)}{r}' for r in range(first, first + len(dates)) for c in range(col, col + 3)}
            if any(c.get('r') in destinations and c.find(ns + 'f') is not None for c in xml.iter(ns + 'c')):
                raise ValueError('貼り付け先に数式があります。数式を上書きしないため停止しました。')
            for merge in xml.iter(ns + 'mergeCell'):
                parts = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)', merge.get('ref', ''))
                if parts:
                    def number(s):
                        n = 0
                        for ch in s:
                            n = n * 26 + ord(ch) - 64
                        return n
                    c1, r1, c2, r2 = parts.groups()
                    if number(c1) <= col + 2 and number(c2) >= col and int(r1) <= first + len(dates) - 1 and int(r2) >= first:
                        raise ValueError('貼り付け先に結合セルがあります。この書式は確認が必要です。')
    except (BadZipFile, KeyError, StopIteration, ET.ParseError) as error:
        raise ValueError('出勤簿の.xlsxファイルを読み込めません。') from error
    return {'sheet': sheet, 'cell': f'{col_name(col)}{first}',
            'range': f'{col_name(col)}{first}:{col_name(col + 2)}{first + len(dates) - 1}',
            'dateYearMismatch': actual_first.year != expected_year}


def prepare(payload, read_grid):
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
    layout = workbook_layout(payload.get('workbookData'), end.year, end.month, person['name'], dates, read_grid)
    config = STAFF.get(norm(person['name']))
    return 200, {'name': person['name'], 'month': payload['month'], 'start': start.isoformat(),
                 'end': end.isoformat(), 'rows': rows, 'layout': layout,
                 'defaultBreak': config[2] if config else None}
