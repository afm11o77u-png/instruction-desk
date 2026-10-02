"""在庫の登録用Excel。顧客・仕入先・商品・取扱・保管場所・初期在庫を1ファイルで読む。"""
import hashlib
import json
from datetime import datetime, timedelta
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import stock

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'
SHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'

SHEETS = [
    ('顧客', [('顧客名', 'name', True), ('カナ', 'kana', False), ('住所', 'address', False), ('電話番号', 'phone', False), ('担当者', 'contact', False)]),
    ('仕入先', [('仕入先名', 'name', True), ('住所', 'address', False), ('電話番号', 'phone', False), ('担当者', 'contact', False)]),
    ('商品', [('品番', 'code', True), ('商品名', 'name', True), ('色', 'color', False), ('仕入先名', 'supplier', False), ('サイズ', 'sizes', True)]),
    ('取扱', [('品番', 'code', True), ('商品名', 'name', True), ('色', 'color', False), ('顧客名', 'customer', True), ('加工仕様', 'embroidery', False), ('標準', 'standard', False)]),
    ('保管場所', [('拠点名', 'site', True), ('棚名', 'shelf', False)]),
    ('初期在庫', [
        ('顧客名', 'customer', True), ('品番', 'code', True), ('商品名', 'name', True), ('色', 'color', False),
        ('加工仕様', 'embroidery', False), ('拠点名', 'site', True), ('棚名', 'shelf', False),
        ('在庫基準日', 'basis', True), ('入荷日', 'received', False), ('入荷日不明', 'unknown', False),
        ('払出し順', 'payout', False), ('サイズ', 'size', True), ('数量', 'qty', True), ('仕入単価', 'cost', False),
    ]),
]
ALIASES = {'フリガナ': 'カナ', 'ふりがな': 'カナ'}
YES = {'する', '1', 'はい', '標準', '○', '不明', 'true', 'True'}


def template():
    return workbook([(name, [label for label, _, _ in cols], []) for name, cols in SHEETS])


def workbook(sheets):
    out = BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as book:
        overrides = ['<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
                     '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
        rels = []
        sheet_tags = []
        for i, (name, headers, rows) in enumerate(sheets, 1):
            overrides.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
            rels.append(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>')
            sheet_tags.append(f'<sheet name="{_xml(name)}" sheetId="{i}" r:id="rId{i}"/>')
            book.writestr(f'xl/worksheets/sheet{i}.xml', _sheet_xml(headers, rows))
        rels.append('<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        book.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?>'
                      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                      '<Default Extension="xml" ContentType="application/xml"/>' + ''.join(overrides) + '</Types>')
        book.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                      '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(rels) + '</Relationships>')
        book.writestr('xl/workbook.xml', f'<workbook xmlns="{SHEET_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{"".join(sheet_tags)}</sheets></workbook>')
        book.writestr('xl/styles.xml', f'<styleSheet xmlns="{SHEET_NS}"><fonts count="1"><font><sz val="11"/><name val="Yu Gothic"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>')
    return out.getvalue()


def apply(c, body):
    raw = body.get('file') if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    import base64
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    sheets = read_sheets(blob)
    counts = {}
    for name, columns in SHEETS:
        rows = _table(sheets.get(name), name, columns)
        counts[name] = _import_sheet(c, name, rows)
    if not any(item['created'] or item['updated'] or item['already'] for item in counts.values()):
        raise ValueError('読み込む行がありません。記入用Excelの見出しに沿って入力してください。')
    return {'counts': {name: counts[name] for name, _ in SHEETS}, 'summary': _summary(counts)}


def read_sheets(blob):
    if not isinstance(blob, (bytes, bytearray)) or not blob.startswith(b'PK'):
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    if len(blob) > 8_000_000:
        raise ValueError('ファイルが大きすぎます。')
    try:
        book = ZipFile(BytesIO(blob))
    except Exception:
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    strings = _strings(book)
    try:
        workbook_xml = ET.fromstring(book.read('xl/workbook.xml'))
        rels = ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))
    except KeyError:
        raise ValueError('このExcelの書式は読み取れませんでした。')
    targets = {rel.get('Id'): rel.get('Target') for rel in rels}
    found = {}
    for sheet in workbook_xml.findall(f'{NS}sheets/{NS}sheet'):
        name = sheet.get('name') or ''
        target = targets.get(sheet.get(f'{REL}id')) or ''
        path = target if target.startswith('xl/') else 'xl/' + target.lstrip('/')
        try:
            info = book.getinfo(path)
        except KeyError:
            raise ValueError(f'「{name}」シートを読み取れませんでした。')
        if info.file_size > 2_000_000:
            raise ValueError('このExcelの書式は読み取れませんでした。')
        found[name] = _rows(ET.fromstring(book.read(path)), strings)
    return found


def _import_sheet(c, name, rows):
    if name == '顧客':
        return _people(c, rows, 'customers', stock.save_customer, stock.customer_row, '顧客')
    if name == '仕入先':
        return _people(c, rows, 'suppliers', stock.save_supplier, stock.supplier_row, '仕入先')
    if name == '商品':
        return _products(c, rows)
    if name == '取扱':
        return _links(c, rows)
    if name == '保管場所':
        return _locations(c, rows)
    if name == '初期在庫':
        return _opening(c, rows)
    return {'created': 0, 'updated': 0, 'already': 0}


def _people(c, rows, table, save, fetch, label):
    created = updated = 0
    for row_no, item in rows:
        name = item['name']
        current = c.execute(f'SELECT id FROM {table} WHERE name=?', (name,)).fetchone()
        if current:
            stored = fetch(c, current['id'])
            body = {'id': current['id'], 'name': name, 'address': item['address'] or stored['address'],
                    'phone': item['phone'] or stored['phone'], 'contact': item['contact'] or stored['contact'], 'active': stored['active']}
            if 'kana' in item:
                body['kana'] = item['kana'] or stored['kana']
            try:
                save(c, body)
            except ValueError as e:
                raise ValueError(f'{label}の{row_no}行目：{e}') from e
            updated += 1
        else:
            body = {'name': name, 'address': item['address'], 'phone': item['phone'], 'contact': item['contact'], 'active': True}
            if 'kana' in item:
                body['kana'] = item['kana']
            try:
                save(c, body)
            except ValueError as e:
                raise ValueError(f'{label}の{row_no}行目：{e}') from e
            created += 1
    return {'created': created, 'updated': updated, 'already': 0}


def _products(c, rows):
    created = updated = 0
    for row_no, item in rows:
        supplier_id = _supplier_id(c, item['supplier'], '商品', row_no)
        sizes = _split_sizes(item['sizes'])
        if not sizes:
            raise ValueError(f'商品の{row_no}行目：サイズを1つ以上入れてください。LLと2Lは別のサイズです。')
        matches = _match_products(c, item['code'], item['name'], item['color'], supplier_id)
        if len(matches) > 1:
            raise ValueError(f'商品の{row_no}行目：同じ品番・商品名・色・仕入先の商品が複数あります。')
        try:
            if matches:
                current = stock.product_row(c, matches[0])
                stock.save_product(c, {'id': current['id'], 'code': item['code'], 'name': item['name'], 'color': item['color'],
                                       'supplierId': supplier_id, 'sizes': sizes, 'active': current['active'],
                                       'links': [{'customerId': link['customerId'], 'embroidery': link['embroidery'], 'isStandard': link['isStandard'], 'active': link['active']} for link in current['links']]})
                updated += 1
            else:
                stock.save_product(c, {'code': item['code'], 'name': item['name'], 'color': item['color'], 'supplierId': supplier_id,
                                       'sizes': sizes, 'links': [], 'active': True})
                created += 1
        except ValueError as e:
            raise ValueError(f'商品の{row_no}行目：{e}') from e
    return {'created': created, 'updated': updated, 'already': 0}


def _links(c, rows):
    created = updated = 0
    grouped = {}
    order = []
    for row_no, item in rows:
        key = (item['code'], item['name'], item['color'])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append((row_no, item))
    for key in order:
        matches = c.execute('SELECT id FROM products WHERE code=? AND name=? AND color=?', key).fetchall()
        if not matches:
            raise ValueError(f'取扱の{grouped[key][0][0]}行目：先に商品シートへ、この品番・商品名・色を入れてください。')
        if len(matches) > 1:
            raise ValueError(f'取扱の{grouped[key][0][0]}行目：同じ品番・商品名・色の商品が複数あります。')
        current = stock.product_row(c, matches[0]['id'])
        links = { (link['customerId'], link['embroidery']): {'customerId': link['customerId'], 'embroidery': link['embroidery'], 'isStandard': link['isStandard'], 'active': link['active']} for link in current['links'] }
        changed = False
        for row_no, item in grouped[key]:
            customer = c.execute('SELECT id FROM customers WHERE name=?', (item['customer'],)).fetchone()
            if not customer:
                raise ValueError(f'取扱の{row_no}行目：顧客「{item["customer"]}」がありません。顧客シートに先に入れてください。')
            standard = item['standard'] in YES
            link_key = (customer['id'], item['embroidery'])
            previous = links.get(link_key)
            if standard:
                for other in links.values():
                    if other['customerId'] == customer['id']:
                        other['isStandard'] = False
            if previous:
                previous['isStandard'] = standard
                updated += 1
            else:
                links[link_key] = {'customerId': customer['id'], 'embroidery': item['embroidery'], 'isStandard': standard, 'active': True}
                created += 1
            changed = True
        if changed:
            try:
                stock.save_product(c, {'id': current['id'], 'code': current['code'], 'name': current['name'], 'color': current['color'],
                                       'supplierId': current['supplierId'], 'sizes': current['sizes'], 'active': current['active'],
                                       'links': list(links.values())})
            except ValueError as e:
                raise ValueError(f'取扱の{grouped[key][0][0]}行目：{e}') from e
    return {'created': created, 'updated': updated, 'already': 0}


def _locations(c, rows):
    created = updated = 0
    for row_no, item in rows:
        site = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL', (item['site'],)).fetchone()
        if site:
            site_id = site['id']
            updated += 0
        else:
            try:
                site_id = stock.save_location(c, {'name': item['site'], 'active': True})['id']
            except ValueError as e:
                raise ValueError(f'保管場所の{row_no}行目：{e}') from e
            created += 1
        if not item['shelf']:
            if site:
                updated += 1
            continue
        shelf = c.execute('SELECT id FROM locations WHERE name=? AND parent_id=?', (item['shelf'], site_id)).fetchone()
        if shelf:
            updated += 1
            continue
        try:
            stock.save_location(c, {'name': item['shelf'], 'parentId': site_id, 'active': True})
        except ValueError as e:
            raise ValueError(f'保管場所の{row_no}行目：{e}') from e
        created += 1
    return {'created': created, 'updated': updated, 'already': 0}


def _opening(c, rows):
    groups = []
    index = {}
    for row_no, item in rows:
        unknown = item['unknown'] in YES
        received = '' if unknown else _day(item['received'], '初期在庫', row_no, '入荷日')
        if unknown and stock.text(item['received']):
            raise ValueError(f'初期在庫の{row_no}行目：入荷日が不明な場合は、入荷日を空にしてください。')
        basis = _day(item['basis'], '初期在庫', row_no, '在庫基準日')
        key = (item['customer'], item['code'], item['name'], item['color'], item['embroidery'], item['site'], item['shelf'], basis, received, unknown, item['payout'])
        if key not in index:
            index[key] = len(groups)
            groups.append({'key': key, 'rows': []})
        groups[index[key]]['rows'].append((row_no, item))
    created = already = 0
    for group in groups:
        customer, code, name, color, embroidery, site, shelf, basis, received, unknown, payout = group['key']
        row_no = group['rows'][0][0]
        person = c.execute('SELECT id FROM customers WHERE name=?', (customer,)).fetchone()
        if not person:
            raise ValueError(f'初期在庫の{row_no}行目：顧客「{customer}」がありません。')
        matches = c.execute('SELECT id FROM products WHERE code=? AND name=? AND color=?', (code, name, color)).fetchall()
        if len(matches) != 1:
            raise ValueError(f'初期在庫の{row_no}行目：品番・商品名・色が一致する商品が{"複数あります" if matches else "ありません"}。')
        handle = c.execute('SELECT id FROM customer_products WHERE customer_id=? AND product_id=? AND embroidery=? AND active=1',
                           (person['id'], matches[0]['id'], embroidery)).fetchone()
        if not handle:
            raise ValueError(f'初期在庫の{row_no}行目：この顧客と商品の取扱がありません。取扱シートに先に入れてください。')
        place = _place_id(c, site, shelf, row_no)
        lines = []
        for line_no, item in group['rows']:
            try:
                qty = stock.parse_qty(item['qty'], '数量')
                cost = stock.parse_money(item['cost'], '仕入単価')
            except ValueError as e:
                raise ValueError(f'初期在庫の{line_no}行目：{e}') from e
            lines.append({'size': item['size'], 'qty': qty, 'unitCost': cost})
        token_source = json.dumps({'key': group['key'], 'lines': lines}, ensure_ascii=False, sort_keys=True)
        token = 'xlsx-' + hashlib.sha256(token_source.encode()).hexdigest()[:40]
        body = {'token': token, 'customerProductId': handle['id'], 'locationId': place, 'basisDate': basis,
                'receivedUnknown': unknown, 'receivedOn': received, 'payoutOrder': int(payout) if unknown else 0, 'lines': lines}
        if unknown and not str(payout).strip():
            raise ValueError(f'初期在庫の{row_no}行目：入荷日不明のときは払出し順を入れてください。')
        try:
            result = stock.register_opening(c, body)
        except ValueError as e:
            raise ValueError(f'初期在庫の{row_no}行目：{e}') from e
        if result.get('already'):
            already += 1
        else:
            created += 1
    return {'created': created, 'updated': 0, 'already': already}


def _place_id(c, site, shelf, row_no):
    parent = c.execute('SELECT id FROM locations WHERE name=? AND parent_id IS NULL', (site,)).fetchone()
    if not parent:
        raise ValueError(f'初期在庫の{row_no}行目：拠点「{site}」がありません。')
    if not shelf:
        return parent['id']
    child = c.execute('SELECT id FROM locations WHERE name=? AND parent_id=?', (shelf, parent['id'])).fetchone()
    if not child:
        raise ValueError(f'初期在庫の{row_no}行目：棚「{shelf}」が拠点「{site}」にありません。')
    return child['id']


def _supplier_id(c, name, sheet, row_no):
    if not name:
        return None
    row = c.execute('SELECT id FROM suppliers WHERE name=?', (name,)).fetchone()
    if not row:
        raise ValueError(f'{sheet}の{row_no}行目：仕入先「{name}」がありません。仕入先シートに先に入れてください。')
    return row['id']


def _match_products(c, code, name, color, supplier_id):
    if supplier_id is None:
        rows = c.execute('SELECT id FROM products WHERE code=? AND name=? AND color=? AND supplier_id IS NULL', (code, name, color)).fetchall()
    else:
        rows = c.execute('SELECT id FROM products WHERE code=? AND name=? AND color=? AND supplier_id=?', (code, name, color, supplier_id)).fetchall()
    return [r['id'] for r in rows]


def _split_sizes(value):
    labels = []
    for part in value.replace('，', '、').replace(',', '、').replace('/', '、').replace('／', '、').split('、'):
        label = part.strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _day(value, sheet, row_no, label):
    text = stock.text(value)
    if not text:
        raise ValueError(f'{sheet}の{row_no}行目：{label}を入力してください。')
    text = text.replace('/', '-').replace('.', '-')
    parts = text.split('-')
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        text = f'{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}'
    elif text.replace('.', '', 1).isdigit():
        serial = float(text)
        if serial <= 0 or serial > 80000:
            raise ValueError(f'{sheet}の{row_no}行目：{label}は日付で入力してください。')
        text = (datetime(1899, 12, 30) + timedelta(days=int(serial))).date().isoformat()
    try:
        return stock.parse_date(text, label)
    except ValueError as e:
        raise ValueError(f'{sheet}の{row_no}行目：{e}') from e


def _table(grid_rows, name, columns):
    if not grid_rows:
        return []
    headers = {ALIASES.get(cell, cell): index for index, cell in enumerate(grid_rows[0])}
    missing = [label for label, _, required in columns if required and label not in headers]
    if missing:
        if any(any(cell for cell in row) for row in grid_rows):
            raise ValueError(f'「{name}」シートの見出しが違います。記入用Excelの見出しを使ってください。')
        return []
    parsed = []
    for offset, row in enumerate(grid_rows[1:], 2):
        if offset > 3001:
            raise ValueError(f'「{name}」シートは3000行までです。')
        item = {}
        blank = True
        for label, key, required in columns:
            index = headers.get(label)
            value = row[index].strip() if index is not None and index < len(row) else ''
            if value:
                blank = False
            item[key] = value
        if blank:
            continue
        for label, key, required in columns:
            if required and not item[key]:
                raise ValueError(f'{name}の{offset}行目：{label}を入力してください。')
        parsed.append((offset, item))
    return parsed


def _summary(counts):
    labels = [('顧客', '顧客'), ('仕入先', '仕入先'), ('商品', '商品'), ('取扱', '取扱'), ('保管場所', '保管場所'), ('初期在庫', '初期在庫')]
    parts = []
    for key, label in labels:
        item = counts[key]
        if key == '初期在庫' and (item['created'] or item['already']):
            parts.append(f'{label} 新規{item["created"]}・同じ内容{item["already"]}')
        elif item['created'] or item['updated']:
            parts.append(f'{label} 新規{item["created"]}・更新{item["updated"]}')
    return '、'.join(parts) + 'を読み込みました。同じ初期在庫は増やしていません。'


def _rows(root, strings):
    rows = []
    for row in root.iter(f'{NS}row'):
        cells = {}
        for cell in row.findall(f'{NS}c'):
            ref = cell.get('r') or ''
            if not ref:
                continue
            cells[_col(ref)] = _cell(cell, strings)
        if not cells:
            rows.append([])
            continue
        width = max(cells) + 1
        rows.append([cells.get(i, '') for i in range(width)])
    return rows


def _cell(cell, strings):
    kind = cell.get('t')
    if kind == 'inlineStr':
        return ''.join(node.text or '' for node in cell.iter(f'{NS}t')).strip()
    value = cell.find(f'{NS}v')
    if value is None or value.text is None:
        return ''
    if kind == 's':
        try:
            return strings[int(value.text)].strip()
        except (IndexError, ValueError):
            return ''
    text = value.text.strip()
    if text.endswith('.0') and text[:-2].lstrip('-').isdigit():
        text = text[:-2]
    return text


def _strings(book):
    try:
        root = ET.fromstring(book.read('xl/sharedStrings.xml'))
    except KeyError:
        return []
    return [''.join(node.text or '' for node in item.iter(f'{NS}t')) for item in root.findall(f'{NS}si')]


def _col(ref):
    letters = ''.join(ch for ch in ref if ch.isalpha())
    number = 0
    for ch in letters:
        number = number * 26 + ord(ch.upper()) - 64
    return number - 1


def _sheet_xml(headers, rows):
    body = [_row_xml(1, headers)]
    for index, row in enumerate(rows, 2):
        body.append(_row_xml(index, row))
    last = max(1, len(rows) + 1)
    end = _letter(len(headers) - 1) if headers else 'A'
    return (f'<worksheet xmlns="{SHEET_NS}"><sheetViews><sheetView workbookViewId="0">'
            f'<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            f'<sheetData>{"".join(body)}</sheetData><autoFilter ref="A1:{end}{last}"/></worksheet>')


def _row_xml(index, values):
    cells = []
    for col, value in enumerate(values):
        cells.append(f'<c r="{_letter(col)}{index}" t="inlineStr"><is><t xml:space="preserve">{_xml(value)}</t></is></c>')
    return f'<row r="{index}">{"".join(cells)}</row>'


def _letter(index):
    number = index + 1
    letters = ''
    while number:
        number, rem = divmod(number - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _xml(value):
    return (str(value if value is not None else '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))
