"""Read a workplace instruction workbook into an unsaved sheet payload."""
import re
import zipfile
from xml.etree import ElementTree as ET

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'

LABELS = [
    ('顧客コード', 'code'),
    ('顧客名', 'customer'),
    ('受付内容', 'request'),
    ('受付者', 'skip'),
    ('受付日', 'skip'),
    ('指示番号', 'skip'),
    ('契約区分', 'contract'),
    ('社員番号', 'employeeNumber'),
    ('従業員名', 'employee'),
    ('管理番号', 'management'),
    ('ネーム部署名', 'department'),
    ('ふりがな', 'phonetic'),
    ('バーコード色', 'barcodeColor'),
    ('ネーム（色）', 'nameColor'),
    ('ネーム色', 'nameColor'),
    ('ネーム', 'nameRequired'),
    ('性別', 'sex'),
    ('部署', 'department'),
    ('入社日', 'skip'),
    ('納品希望日', 'skip'),
    ('修理内容', 'repair'),
    ('仕上り予定日', 'skip'),
    ('マーク出荷日', 'shipNote'),
    ('納品形態', 'packing'),
    ('納品方法', 'method'),
    ('号車', 'car'),
    ('先出しの有無', 'advance'),
    ('先出の有無', 'advance'),
    ('同梱物', 'enclosures'),
    ('胸バーコード', 'chest'),
    ('備考', 'notes'),
    ('担当者', 'skip'),
    ('セット', 'department'),
]
CHOICES = {
    'request': ['消耗・破損交換', 'サイズ交換', '追加・補充', '異動・移動', '新規', '復職', 'その他'],
    'contract': ['個人レンタル', 'サイズレンタル', '商品リース', 'クリーニング', 'その他'],
    'packing': ['ハンガー', 'たたみ', 'その他'],
    'sex': ['女性', '男性', 'その他'],
    'nameRequired': ['あり', 'なし'],
    'advance': ['する', 'しない'],
}
CHOICE_LABELS = {
    'request': '受付内容', 'contract': '契約区分', 'packing': '納品形態',
    'sex': '性別', 'nameRequired': 'ネームの有無', 'advance': '先出しの有無', 'method': '納品方法',
}
KEEP_DIGITS = {'employeeNumber', 'management', 'chest'}


def parse_instruction(blob):
    if not blob.startswith(b'PK'):
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    try:
        book = zipfile.ZipFile(__import__('io').BytesIO(blob))
    except zipfile.BadZipFile:
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    grid = _grid(book)
    header = next((row for row in sorted({r for r, _ in grid}) if _is_item_header(grid, row)), None)
    if header is None:
        raise ValueError('「指示書」シートの商品欄を読み取れませんでした。')
    repair = next((row for row in range(header + 1, header + 40) if _starts(grid.get((row, 1), ''), '修理内容')), header + 15)
    payload, warnings, notes = {}, [], []
    side = {'weekday': ''}
    stop = repair + 12
    for row in range(repair, repair + 20):
        marker = _compact(grid.get((row, 1), ''))
        if marker.startswith('担当者') or marker.startswith('バーコード発行'):
            stop = row
            break
    for row in sorted({r for r, _ in grid}):
        if row >= stop or header <= row < repair:
            continue
        _read_labels(grid, row, payload, notes, warnings, side)
    if side['weekday']:
        car = payload.get('car', '')
        if side['weekday'] not in car:
            payload['car'] = _clean(f'{car} {side["weekday"]}'.strip(), 300)
    items = _items(grid, header, repair)
    if not payload.get('customer') and not items:
        raise ValueError('「指示書」シートから内容を読み取れませんでした。')
    if items:
        payload['items'] = items
    if notes:
        payload['notes'] = _clean('\n'.join(notes), 2000)
    return payload, warnings


def _grid(book):
    strings = _strings(book)
    path = _sheet_path(book)
    root = ET.fromstring(_part(book, path))
    grid = {}
    for cell in root.iter(f'{NS}c'):
        ref = cell.get('r') or ''
        if not ref:
            continue
        text = _cell_text(cell, strings)
        if text:
            grid[(_row(ref), _col(ref))] = text
    return grid


def _part(book, name, limit=2_000_000):
    try:
        info = book.getinfo(name)
    except KeyError:
        raise ValueError('「指示書」シートが見つかりません。')
    if info.file_size > limit:
        raise ValueError('このExcelの書式は読み取れませんでした。')
    return book.read(name)


def _strings(book):
    if 'xl/sharedStrings.xml' not in book.namelist():
        return []
    root = ET.fromstring(_part(book, 'xl/sharedStrings.xml'))
    return [_rich_text(node) for node in root.findall(f'{NS}si')]


def _rich_text(node, phonetic=False):
    parts = []
    if node.tag == f'{NS}t' and not phonetic and node.text:
        parts.append(node.text)
    for child in list(node):
        parts.append(_rich_text(child, phonetic or node.tag == f'{NS}rPh'))
    return ''.join(parts)


def _sheet_path(book):
    workbook = ET.fromstring(_part(book, 'xl/workbook.xml'))
    chosen = None
    for sheet in workbook.findall(f'{NS}sheets/{NS}sheet'):
        if sheet.get('name') == '指示書':
            chosen = sheet.get(f'{REL}id')
            break
    if not chosen:
        raise ValueError('「指示書」シートが見つかりません。')
    rels = ET.fromstring(_part(book, 'xl/_rels/workbook.xml.rels'))
    for rel in rels.findall(f'{PKG}Relationship'):
        if rel.get('Id') == chosen:
            target = rel.get('Target', '').lstrip('/')
            return target if target.startswith('xl/') else 'xl/' + target
    raise ValueError('「指示書」シートが見つかりません。')


def _cell_text(cell, strings):
    kind = cell.get('t')
    if kind == 'inlineStr':
        return _clean(_rich_text(cell.find(f'{NS}is') or ET.Element('is')), 1000)
    value = cell.find(f'{NS}v')
    if value is None or not value.text or kind in ('b', 'e'):
        return ''
    if kind == 's':
        try:
            return _clean(strings[int(value.text)], 1000)
        except (IndexError, ValueError):
            return ''
    if re.fullmatch(r'-?\d+', value.text):
        return value.text
    if re.fullmatch(r'-?\d+\.0+', value.text):
        return value.text.split('.')[0]
    return _clean(value.text, 1000)


def _is_item_header(grid, row):
    return any(_starts(grid[(row, col)], 'アイテム') for col in range(1, 8) if (row, col) in grid)


def _items(grid, header, repair):
    columns = {}
    seen_note = False
    for col in range(1, 16):
        label = _compact(grid.get((header, col), ''))
        if label.startswith('備考'):
            columns[col] = 'note2' if seen_note else 'note'
            seen_note = True
        elif label.startswith('在庫'):
            columns[col] = 'stock'
        elif label.startswith('新古'):
            columns[col] = 'condition'
        elif label.startswith('数量'):
            columns[col] = 'quantity'
        elif label.startswith('サイズ'):
            columns[col] = 'size'
        elif label.startswith('品番'):
            columns[col] = 'sku'
        elif label.startswith('メーカー'):
            columns[col] = 'maker'
        elif label.startswith('アイテム'):
            columns[col] = 'name'
    items = []
    for row in range(header + 1, repair):
        item = {'name': '', 'maker': '', 'sku': '', 'size': '', 'condition': '未指定', 'quantity': 0, 'note': '', 'note2': '', 'stock': ''}
        for col, key in columns.items():
            raw = grid.get((row, col), '')
            if key == 'condition':
                item[key] = _condition(raw)
            elif key == 'quantity':
                item[key] = _quantity(raw)
            elif key == 'stock':
                item[key] = _stock(raw)
            elif raw:
                item[key] = _clean(raw, 1000 if key in ('note', 'note2') else 300)
        if item['note2'] and item['note2'] == item['note']:
            item['note2'] = ''
        if any(item[key] for key in ('name', 'maker', 'sku', 'size', 'note', 'note2', 'stock')):
            items.append(item)
        if len(items) >= 100:
            break
    return items


def _read_labels(grid, row, payload, notes, warnings, side):
    cols = [col for col in range(1, 12) if (row, col) in grid]
    for col in cols:
        key = _label(grid[(row, col)])
        if not key:
            continue
        values = []
        for next_col in range(col + 1, col + 5):
            text = grid.get((row, next_col), '')
            if text and _label(text):
                break
            if text:
                values.append(text)
        if key == 'notes':
            for follow in range(row + 1, row + 6):
                if _label(grid.get((follow, 1), '')):
                    break
                extra = grid.get((follow, 2), '')
                if extra and not _label(extra):
                    values.append(extra)
        _apply(key, values, payload, notes, warnings, side)


def _apply(key, values, payload, notes, warnings, side):
    values = [value for value in values if not (re.fullmatch(r'\d{5}', value) and key not in KEEP_DIGITS)]
    if key in ('skip',) or not values:
        return
    if key == 'shipNote':
        notes.extend(_clean(value, 300) for value in values if not re.fullmatch(r'\d+', value))
        return
    if key == 'chest':
        line = '胸バーコード：' + _clean(values[0], 80)
        if line not in notes:
            notes.append(line)
        return
    if key == 'notes':
        notes.extend(_clean(value, 500) for value in values)
        return
    if key == 'department' and payload.get('department'):
        return
    if key in CHOICES or key == 'method':
        chosen, raw = _choice(key, values[0])
        if chosen and not payload.get(key):
            payload[key] = chosen
            if key == 'packing' and _compact(values[0]) != chosen:
                line = '納品形態：' + _clean(values[0], 80)
                if line not in notes:
                    notes.append(line)
        elif raw and not payload.get(key):
            message = f'{CHOICE_LABELS[key]}「{raw}」は選択肢にないため入れていません。'
            if message not in warnings:
                warnings.append(message)
        if key == 'advance' and len(values) > 1 and not payload.get('advanceCondition'):
            payload['advanceCondition'] = _clean(values[1], 300)
        if key == 'method':
            if chosen == 'その他' and raw:
                payload['methodNote'] = raw
            _weekday(values[0], side)
        return
    if not payload.get(key):
        payload[key] = _clean(values[0], 300)


def _choice(key, value):
    text = _compact(value)
    if key == 'method':
        if text.startswith('車両便'):
            return '車両便', ''
        if '送り' in text:
            return '送り', ''
        if 'オリコン' in text:
            return 'オリコン', ''
        if '担当者へ' in text:
            return '担当者へ', ''
        if text in ('車両便', '送り', 'オリコン', '担当者へ', 'その他'):
            return text, ''
        return 'その他', _clean(value, 40)
    for option in CHOICES[key]:
        if text == option or text.startswith(option):
            return option, ''
    return '', _clean(value, 40)


def _weekday(value, side):
    found = re.search(r'[（(]([^）)]+)[）)]', value)
    if found and not side['weekday']:
        side['weekday'] = _clean(found.group(1), 40)


def _label(text):
    compact = _compact(text)
    if not compact or len(compact) > 24:
        return None
    for name, key in LABELS:
        if compact == name or compact.startswith(name):
            return key
    return None


def _starts(text, name):
    return _compact(text).startswith(name)


def _condition(value):
    text = _compact(value)
    if text.startswith('新'):
        return '新品'
    if text.startswith('古') or '中古' in text:
        return '中古'
    return '未指定'


def _stock(value):
    text = _compact(value).lower().replace('「', '').replace('」', '')
    if text in ('a', 'ａ') or '工場' in text or '発寒' in text:
        return '発寒支店'
    if text in ('b', 'ｂ') or '揃' in text or '持込' in text or '持ち込み' in text:
        return '管理担当持込'
    if '倉庫' in text or '石狩' in text:
        return '石狩倉庫'
    return ''


def _quantity(value):
    text = _compact(value)
    if re.fullmatch(r'\d+', text):
        number = int(text)
        if 0 <= number <= 99999:
            return number
    return 0


def _compact(text):
    text = str(text).split('※')[0]
    return re.sub(r'[\s　/／]+', '', text)


def _clean(text, limit):
    kept = []
    for ch in str(text).replace('\r\n', '\n').replace('\r', '\n'):
        if ch in '\n\t' or ord(ch) >= 32:
            kept.append(ch)
    return ''.join(kept).strip()[:limit]


def _col(ref):
    number = 0
    for ch in ref:
        if not ch.isalpha():
            break
        number = number * 26 + ord(ch.upper()) - 64
    return number


def _row(ref):
    return int(''.join(ch for ch in ref if ch.isdigit()) or 0)
