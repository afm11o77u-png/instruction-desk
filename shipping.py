"""出荷情報。

移行元 costomer（J:\\empire_info_ak.sql）との対応:
  ID         -> source_id（移行元の登録番号。新規登録は空）
  Client     -> customer（顧客名）
  ShipmentM  -> method（出荷方法）
  ShipmentF  -> pack（出荷形態）
  ShipmentF2 -> mark_pack（出荷形態(マーク)）
  DeliveryW  -> weekday（集配曜日）
  TOD        -> time_band（集配時間）
  DeliveryN  -> car（集配号車）
  Sales      -> manager（管理担当）
  Manage     -> sales（営業担当）
  ConP1      -> contract_start（契約開始日。0001-01-01 は空）
  ConP2      -> contract_end（契約終了日。0001-01-01 は空）
移行元に登録日時はない。アプリで新しく作った行だけ日時を入れる。
"""
import json, re
from datetime import datetime, date, timedelta
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

SOURCE_SQL=Path(r'J:\empire_info_ak.sql')
PAGE=40
METHODS=['自社便','福山・佐川','地方便(オリコン)','佐川指定','ヤマト指定']
PACKS=['たたみ包装','ハンガー']
WEEKDAYS=['月・木','火・金','水・土','月・水・金','火・木・土','月曜日','火曜日','水曜日','木曜日','金曜日','土曜日','毎日','月1回','月2回','TEL']
TIMES=[f'{h}:00～{h+1}:00' for h in range(5,17)]
CARS=['1号車','2号車','3号車','4・8号車','5号車','6号車','7号車','9号車(アドバンス)','10号車','11号車(アドバンス)','12号車']
PEOPLE=['森末支店長','横内課長','横内次長','山田課長','河口係長','藤浪係長','藤澤代理','田中(本社)','藤本','安藤','田中(発寒支店)','小野寺','大島','大本','菅沼']

def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS shipments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER UNIQUE,
        customer TEXT NOT NULL DEFAULT '',
        method TEXT NOT NULL DEFAULT '',
        pack TEXT NOT NULL DEFAULT '',
        mark_pack TEXT NOT NULL DEFAULT '',
        weekday TEXT NOT NULL DEFAULT '',
        time_band TEXT NOT NULL DEFAULT '',
        car TEXT NOT NULL DEFAULT '',
        manager TEXT NOT NULL DEFAULT '',
        sales TEXT NOT NULL DEFAULT '',
        contract_start TEXT NOT NULL DEFAULT '',
        contract_end TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT '',
        created_by_id INTEGER,
        updated_by_id INTEGER,
        version INTEGER NOT NULL DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS shipment_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shipment_id INTEGER NOT NULL,
        at TEXT NOT NULL,
        action TEXT NOT NULL,
        actor_id INTEGER,
        actor TEXT NOT NULL,
        changes TEXT NOT NULL
    )''')

def _actor(user):
    return f"{user['name']} ({user['login']})"

def _row_public(row):
    return {
        'id':row['id'],'sourceId':row['source_id'],'number':row['source_id'] or row['id'],
        'customer':row['customer'],'method':row['method'],'pack':row['pack'],'markPack':row['mark_pack'],
        'weekday':row['weekday'],'timeBand':row['time_band'],'car':row['car'],'manager':row['manager'],'sales':row['sales'],
        'contractStart':row['contract_start'],'contractEnd':row['contract_end'],
        'createdAt':row['created_at'],'updatedAt':row['updated_at'],'version':row['version']
    }

def _require_view(user):
    if not user or user['must_change']: return 401, {'error':'ログインし直してください。'}
    if user['role'] not in ('owner','sales','floor','floor2'): return 403, {'error':'出荷情報は閲覧できません。'}
    return None

def _require_edit(user):
    blocked=_require_view(user)
    if blocked: return blocked
    if user['role']!='owner': return 403, {'error':'出荷情報の登録と修正は主管理者が行えます。'}
    return None

def _text(value, label, limit, required=False):
    if value is None: value=''
    if not isinstance(value, str): raise ValueError(f'{label}を確認してください。')
    text=value.strip()
    if required and not text: raise ValueError(f'{label}を入力してください。')
    if len(text)>limit: raise ValueError(f'{label}は{limit}文字以内で入力してください。')
    return text

def _day(value, label):
    text=_text(value, label, 10)
    if not text: return ''
    try: date.fromisoformat(text)
    except ValueError: raise ValueError(f'{label}を確認してください。')
    if text<='0001-01-01': return ''
    return text

def _fields(d):
    return {
        'customer':_text(d.get('customer'),'顧客',50,True),
        'method':_text(d.get('method'),'出荷方法',50),
        'pack':_text(d.get('pack'),'出荷形態',50),
        'mark_pack':_text(d.get('markPack'),'出荷形態(マーク)',50),
        'weekday':_text(d.get('weekday'),'集配曜日',50),
        'time_band':_text(d.get('timeBand'),'集配時間',50),
        'car':_text(d.get('car'),'集配号車',50),
        'manager':_text(d.get('manager'),'管理担当',50),
        'sales':_text(d.get('sales'),'営業担当',50),
        'contract_start':_day(d.get('contractStart'),'契約開始日'),
        'contract_end':_day(d.get('contractEnd'),'契約終了日'),
    }

def search(handler, c):
    from urllib.parse import parse_qs, urlparse
    blocked=_require_view(handler.user)
    if blocked: return blocked
    q=parse_qs(urlparse(handler.path).query)
    customer=(q.get('customer') or [''])[0].strip()
    try: offset=max(0, int((q.get('offset') or ['0'])[0]))
    except ValueError: offset=0
    where=[]; args=[]
    if customer:
        where.append('customer LIKE ?'); args.append(f'%{customer}%')
    sql=' FROM shipments'+((' WHERE '+' AND '.join(where)) if where else '')
    total=c.execute('SELECT COUNT(*)'+sql, args).fetchone()[0]
    rows=c.execute('SELECT *'+sql+' ORDER BY customer, id LIMIT ? OFFSET ?', args+[PAGE, offset]).fetchall()
    return 200, {'total':total,'offset':offset,'limit':PAGE,'items':[_row_public(r) for r in rows]}

def customers(handler, c):
    blocked=_require_view(handler.user)
    if blocked: return blocked
    names=[r['customer'] for r in c.execute("SELECT DISTINCT customer FROM shipments WHERE customer<>'' ORDER BY customer")]
    return 200, {'customers':names,'methods':METHODS,'packs':PACKS,'weekdays':WEEKDAYS,'times':TIMES,'cars':CARS,'people':PEOPLE}

def create(handler, c, d):
    blocked=_require_edit(handler.user)
    if blocked: return blocked
    fields=_fields(d)
    now=datetime.now().isoformat(timespec='seconds')
    cur=c.execute('''INSERT INTO shipments(source_id,customer,method,pack,mark_pack,weekday,time_band,car,manager,sales,contract_start,contract_end,created_at,updated_at,created_by_id,updated_by_id,version)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)''',(
        _next_source(c),fields['customer'],fields['method'],fields['pack'],fields['mark_pack'],fields['weekday'],fields['time_band'],fields['car'],fields['manager'],fields['sales'],fields['contract_start'],fields['contract_end'],now,now,handler.user['id'],handler.user['id']))
    sid=cur.lastrowid
    changes={k:{'before':'','after':v} for k,v in fields.items() if v}
    c.execute('INSERT INTO shipment_history(shipment_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(sid, now, '登録', handler.user['id'], _actor(handler.user), json.dumps(changes, ensure_ascii=False)))
    return 200, _row_public(c.execute('SELECT * FROM shipments WHERE id=?',(sid,)).fetchone())

def update(handler, c, d):
    blocked=_require_edit(handler.user)
    if blocked: return blocked
    if type(d.get('id')) is not int or type(d.get('version')) is not int: return 400, {'error':'出荷情報を選択してください。'}
    row=c.execute('SELECT * FROM shipments WHERE id=?',(d['id'],)).fetchone()
    if not row: return 404, {'error':'出荷情報が見つかりません。'}
    if row['version']!=d['version']: return 409, {'error':'別の画面で更新されました。入力内容は残しています。一覧を更新してから、もう一度保存してください。'}
    fields=_fields(d)
    before={k:row[k] for k in fields}
    changes={k:{'before':before[k],'after':fields[k]} for k in fields if before[k]!=fields[k]}
    if not changes: return 200, _row_public(row)
    now=datetime.now().isoformat(timespec='seconds')
    c.execute('''UPDATE shipments SET customer=?,method=?,pack=?,mark_pack=?,weekday=?,time_band=?,car=?,manager=?,sales=?,contract_start=?,contract_end=?,updated_at=?,updated_by_id=?,version=version+1 WHERE id=?''',(
        fields['customer'],fields['method'],fields['pack'],fields['mark_pack'],fields['weekday'],fields['time_band'],fields['car'],fields['manager'],fields['sales'],fields['contract_start'],fields['contract_end'],now,handler.user['id'],row['id']))
    c.execute('INSERT INTO shipment_history(shipment_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'], now, '修正', handler.user['id'], _actor(handler.user), json.dumps(changes, ensure_ascii=False)))
    return 200, _row_public(c.execute('SELECT * FROM shipments WHERE id=?',(row['id'],)).fetchone())

def _tuples(body):
    i=body.find('VALUES')
    if i<0: return []
    i+=6
    rows=[]
    n=len(body)
    while i<n:
        while i<n and body[i] in ' \r\n\t,': i+=1
        if i>=n or body[i]!='(': break
        i+=1
        fields=[]
        while i<n:
            while i<n and body[i] in ' \r\n\t': i+=1
            if body.startswith('NULL', i):
                fields.append(''); i+=4
            elif i<n and body[i]=="'":
                i+=1
                chars=[]
                while i<n:
                    if body[i]=='\\' and i+1<n:
                        chars.append(body[i+1]); i+=2; continue
                    if body[i]=="'":
                        i+=1; break
                    chars.append(body[i]); i+=1
                fields.append(''.join(chars))
            else:
                j=i
                while j<n and body[j] not in ',)': j+=1
                fields.append(body[i:j].strip()); i=j
            while i<n and body[i] in ' \r\n\t': i+=1
            if i<n and body[i]==',':
                i+=1; continue
            if i<n and body[i]==')':
                i+=1; break
            break
        if len(fields)>=12: rows.append(fields[:12])
    return rows

def _blank_day(value):
    text=str(value or '').strip()
    if text in ('','0000-00-00','0001-01-01'): return ''
    return text

def _sql_rows(text):
    rows=[]
    start=0
    needle='INSERT INTO `costomer`'
    while True:
        start=text.find(needle, start)
        if start<0: break
        end=text.find(';\n', start)
        if end<0: end=len(text)
        for fields in _tuples(text[start:end]):
            rows.append({
                'id':int(fields[0]),
                'method':fields[1].strip(),
                'pack':fields[2].strip(),
                'mark_pack':fields[3].strip(),
                'weekday':fields[4].strip(),
                'car':fields[5].strip(),
                'manager':fields[6].strip(),
                'sales':fields[7].strip(),
                'customer':fields[8].strip(),
                'time_band':fields[9].strip(),
                'contract_start':_blank_day(fields[10]),
                'contract_end':_blank_day(fields[11]),
            })
        start=end+2
    return rows

def import_sources(connect, data_dir):
    if not SOURCE_SQL.is_file():
        return {'skipped':True,'reason':'SQLがありません。'}
    parsed=_sql_rows(SOURCE_SQL.read_text(encoding='utf-8'))
    report={'sourceRows':len(parsed),'imported':0,'already':0,'skippedEdited':0}
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for row in parsed:
            existing=c.execute('SELECT id, updated_by_id FROM shipments WHERE source_id=?',(row['id'],)).fetchone()
            if existing:
                report['already']+=1
                if existing['updated_by_id'] is not None: report['skippedEdited']+=1
                continue
            c.execute('''INSERT INTO shipments(source_id,customer,method,pack,mark_pack,weekday,time_band,car,manager,sales,contract_start,contract_end,created_at,updated_at,created_by_id,updated_by_id,version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?, '', '', NULL, NULL, 1)''',(
                row['id'],row['customer'],row['method'],row['pack'],row['mark_pack'],row['weekday'],row['time_band'],row['car'],row['manager'],row['sales'],row['contract_start'],row['contract_end']))
            report['imported']+=1
        c.execute("INSERT OR REPLACE INTO settings VALUES('shipment_import',?)",(json.dumps(report, ensure_ascii=False),))
    (Path(data_dir)/'shipment-import-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report

XLSX='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
SHEET_NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
SHEET_TAG='{'+SHEET_NS+'}'
REL_NS='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG_NS='{http://schemas.openxmlformats.org/package/2006/relationships}'
EXCEL_COLUMNS=[
    ('登録番号','number'),
    ('顧客名','customer'),
    ('出荷方法','method'),
    ('出荷形態','pack'),
    ('出荷形態(マーク)','markPack'),
    ('集配曜日','weekday'),
    ('集配時間','timeBand'),
    ('集配号車','car'),
    ('管理担当','manager'),
    ('営業担当','sales'),
    ('契約開始','contractStart'),
    ('契約終了','contractEnd'),
]
EXCEL_ALIASES={
    '登録番号':'number','顧客名':'customer','顧客':'customer','出荷方法':'method','出荷形態':'pack',
    '出荷形態(マーク)':'markPack','集配曜日':'weekday','集配時間':'timeBand','集配開始時間':'timeBand',
    '集配号車':'car','管理担当':'manager','営業担当':'sales','契約開始':'contractStart','契約開始日':'contractStart',
    '契約終了':'contractEnd','契約終了日':'contractEnd',
}
EXCEL_FIELDS=['customer','method','pack','markPack','weekday','timeBand','car','manager','sales','contractStart','contractEnd']

def _next_source(c):
    current=c.execute('SELECT MAX(source_id) FROM shipments').fetchone()[0] or 0
    return int(current)+1

def _number(row):
    return row['source_id'] or row['id']

def _excel_text(value):
    return '' if value is None else str(value).strip()

def _excel_day(value):
    text=_excel_text(value)
    if text in ('','0000-00-00','0001-01-01'): return ''
    if ' ' in text: text=text.split(' ',1)[0]
    matched=re.fullmatch(r'(\d{4})[./-](\d{1,2})[./-](\d{1,2})', text)
    if matched:
        year,month,day=map(int, matched.groups())
        return date(year, month, day).isoformat()
    if re.fullmatch(r'\d+(\.0+)?', text):
        serial=int(float(text))
        if 1<=serial<=80000:
            return (date(1899,12,30)+timedelta(days=serial)).isoformat()
    return text

def export_book(handler, c):
    blocked=_require_view(handler.user)
    if blocked: return blocked
    rows=c.execute('SELECT * FROM shipments ORDER BY customer, id').fetchall()
    return 200, _workbook(rows), XLSX

def import_book(handler, c, blob):
    blocked=_require_edit(handler.user)
    if blocked: return blocked
    try: parsed=_parse_book(blob)
    except ValueError as e: return 400, {'error':str(e)}
    errors=[]
    ready=[]
    seen=set()
    for item in parsed:
        number=item['number']
        if number is not None:
            if number in seen:
                errors.append(f"{item['row']}行目：登録番号 {number} がファイル内で重複しています。")
                continue
            seen.add(number)
            found=_find_number(c, number)
            if not found:
                errors.append(f"{item['row']}行目：登録番号 {number} の出荷情報が見つかりません。空欄にすると新しい行になります。")
                continue
            item['existing']=found
        try:
            item['fields']=_fields(item['values'])
        except ValueError as e:
            errors.append(f"{item['row']}行目：{e}")
            continue
        ready.append(item)
    if errors:
        shown=errors[:8]
        if len(errors)>8: shown.append(f'ほか {len(errors)-8} 件')
        return 400, {'error':'\n'.join(shown)}
    if not ready: return 400, {'error':'登録できる行がありません。'}
    created=updated=unchanged=0
    now=datetime.now().isoformat(timespec='seconds')
    for item in ready:
        fields=item['fields']
        if item.get('existing'):
            row=item['existing']
            before={k:row[k] for k in fields}
            changes={k:{'before':before[k],'after':fields[k]} for k in fields if before[k]!=fields[k]}
            if not changes:
                unchanged+=1
                continue
            c.execute('''UPDATE shipments SET customer=?,method=?,pack=?,mark_pack=?,weekday=?,time_band=?,car=?,manager=?,sales=?,contract_start=?,contract_end=?,updated_at=?,updated_by_id=?,version=version+1 WHERE id=?''',(
                fields['customer'],fields['method'],fields['pack'],fields['mark_pack'],fields['weekday'],fields['time_band'],fields['car'],fields['manager'],fields['sales'],fields['contract_start'],fields['contract_end'],now,handler.user['id'],row['id']))
            c.execute('INSERT INTO shipment_history(shipment_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'], now, 'Excel取込', handler.user['id'], _actor(handler.user), json.dumps(changes, ensure_ascii=False)))
            updated+=1
        else:
            cur=c.execute('''INSERT INTO shipments(source_id,customer,method,pack,mark_pack,weekday,time_band,car,manager,sales,contract_start,contract_end,created_at,updated_at,created_by_id,updated_by_id,version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)''',(
                _next_source(c),fields['customer'],fields['method'],fields['pack'],fields['mark_pack'],fields['weekday'],fields['time_band'],fields['car'],fields['manager'],fields['sales'],fields['contract_start'],fields['contract_end'],now,now,handler.user['id'],handler.user['id']))
            changes={k:{'before':'','after':v} for k,v in fields.items() if v}
            c.execute('INSERT INTO shipment_history(shipment_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(cur.lastrowid, now, 'Excel取込', handler.user['id'], _actor(handler.user), json.dumps(changes, ensure_ascii=False)))
            created+=1
    return 200, {'created':created,'updated':updated,'unchanged':unchanged}

def _find_number(c, number):
    row=c.execute('SELECT * FROM shipments WHERE source_id=?',(number,)).fetchone()
    if row: return row
    return c.execute('SELECT * FROM shipments WHERE id=? AND source_id IS NULL',(number,)).fetchone()

def _workbook(rows):
    out=BytesIO()
    with ZipFile(out,'w',ZIP_DEFLATED) as book:
        book.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        book.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        book.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        book.writestr('xl/workbook.xml',f'<workbook xmlns="{SHEET_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="出荷情報" sheetId="1" r:id="rId1"/></sheets></workbook>')
        book.writestr('xl/styles.xml',f'<styleSheet xmlns="{SHEET_NS}"><fonts count="1"><font><sz val="11"/><name val="Yu Gothic"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>')
        sheet=['<row r="1">'+''.join(_xlsx_cell(i,1,label) for i, (label, _) in enumerate(EXCEL_COLUMNS))+'</row>']
        for index, row in enumerate(rows, 2):
            values={
                'number':_number(row),'customer':row['customer'],'method':row['method'],'pack':row['pack'],'markPack':row['mark_pack'],
                'weekday':row['weekday'],'timeBand':row['time_band'],'car':row['car'],'manager':row['manager'],'sales':row['sales'],
                'contractStart':row['contract_start'],'contractEnd':row['contract_end'],
            }
            sheet.append(f'<row r="{index}">'+''.join(_xlsx_cell(i,index,values[key]) for i,(label,key) in enumerate(EXCEL_COLUMNS))+'</row>')
        last=max(1, len(rows)+1)
        cols=''.join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i,width in enumerate([12,28,16,14,18,14,16,18,14,14,14,14],1))
        book.writestr('xl/worksheets/sheet1.xml',f'<worksheet xmlns="{SHEET_NS}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>{cols}</cols><sheetData>{"".join(sheet)}</sheetData><autoFilter ref="A1:L{last}"/></worksheet>')
    return out.getvalue()

def _xlsx_cell(col, row, value):
    letters=''
    number=col+1
    while number:
        number, rem=divmod(number-1, 26)
        letters=chr(65+rem)+letters
    return f'<c r="{letters}{row}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value if value is not None else ""))}</t></is></c>'

def _parse_book(blob):
    if not isinstance(blob,(bytes,bytearray)) or not blob.startswith(b'PK'):
        raise ValueError('Excelファイル（.xlsx）を選んでください。')
    if len(blob)>8_000_000: raise ValueError('ファイルが大きすぎます。')
    try: book=ZipFile(BytesIO(blob))
    except BadZipFile: raise ValueError('Excelファイル（.xlsx）を選んでください。')
    grid=_sheet_grid(book)
    header_row, columns=_excel_header(grid)
    parsed=[]
    for row in range(header_row+1, header_row+3001):
        values={}
        blank=True
        for col, key in columns.items():
            text=_excel_text(grid.get((row,col),''))
            if key in ('contractStart','contractEnd'): text=_excel_day(text)
            if text: blank=False
            values[key]=text
        if blank: continue
        number=_excel_number(values.get('number',''), row)
        for key in EXCEL_FIELDS:
            values.setdefault(key,'')
        parsed.append({'row':row,'number':number,'values':values})
    return parsed

def _excel_number(text, row):
    if not text: return None
    if not re.fullmatch(r'\d+', text): raise ValueError(f'{row}行目：登録番号は数字で入力してください。')
    number=int(text)
    if number<1: raise ValueError(f'{row}行目：登録番号は数字で入力してください。')
    return number

def _excel_header(grid):
    for row in range(1, 6):
        columns={}
        used=set()
        for col in range(1, 40):
            label=re.sub(r'\s+','',grid.get((row,col),'').replace('（','(').replace('）',')'))
            key=EXCEL_ALIASES.get(label)
            if not key: continue
            if key in used: raise ValueError(f'{label}の列が重複しています。')
            used.add(key)
            columns[col]=key
        if 'customer' in used:
            if any(index>row+3000 for index,_col in grid): raise ValueError('行が多すぎます。3000行までにしてください。')
            return row, columns
    raise ValueError('1行目に「顧客名」の列が必要です。')

def _sheet_grid(book):
    strings=_shared_strings(book)
    root=ET.fromstring(_zip_part(book, _first_sheet(book)))
    grid={}
    for cell in root.iter(f'{SHEET_TAG}c'):
        ref=cell.get('r') or ''
        if not ref: continue
        text=_sheet_cell(cell, strings)
        if text: grid[(_sheet_row(ref), _sheet_col(ref))]=text
    return grid

def _first_sheet(book):
    workbook=ET.fromstring(_zip_part(book, 'xl/workbook.xml'))
    sheets=workbook.findall(f'{SHEET_TAG}sheets/{SHEET_TAG}sheet')
    if not sheets: raise ValueError('Excelのシートを読み取れませんでした。')
    chosen=sheets[0].get(f'{REL_NS}id')
    rels=ET.fromstring(_zip_part(book, 'xl/_rels/workbook.xml.rels'))
    for rel in rels.findall(f'{PKG_NS}Relationship'):
        if rel.get('Id')==chosen:
            target=rel.get('Target','').lstrip('/')
            return target if target.startswith('xl/') else 'xl/'+target
    raise ValueError('Excelのシートを読み取れませんでした。')

def _shared_strings(book):
    if 'xl/sharedStrings.xml' not in book.namelist(): return []
    root=ET.fromstring(_zip_part(book, 'xl/sharedStrings.xml'))
    return [_sheet_rich(node) for node in root.findall(f'{SHEET_TAG}si')]

def _sheet_rich(node, phonetic=False):
    parts=[]
    if node.tag==f'{SHEET_TAG}t' and not phonetic and node.text: parts.append(node.text)
    for child in list(node):
        parts.append(_sheet_rich(child, phonetic or node.tag==f'{SHEET_TAG}rPh'))
    return ''.join(parts)

def _zip_part(book, name):
    try: info=book.getinfo(name)
    except KeyError: raise ValueError('Excelのシートを読み取れませんでした。')
    if info.file_size>8_000_000: raise ValueError('このExcelの書式は読み取れませんでした。')
    return book.read(name)

def _sheet_cell(cell, strings):
    kind=cell.get('t')
    if kind=='inlineStr':
        return _sheet_rich(cell.find(f'{SHEET_TAG}is') or ET.Element('is')).strip()
    value=cell.find(f'{SHEET_TAG}v')
    if value is None or not value.text or kind in ('b','e'): return ''
    if kind=='s':
        try: return strings[int(value.text)].strip()
        except (IndexError, ValueError): return ''
    text=value.text.strip()
    if re.fullmatch(r'-?\d+\.0+', text): return text.split('.')[0]
    return text

def _sheet_row(ref):
    return int(re.search(r'\d+', ref).group())

def _sheet_col(ref):
    letters=re.match(r'[A-Z]+', ref).group()
    number=0
    for ch in letters: number=number*26+ord(ch)-64
    return number
