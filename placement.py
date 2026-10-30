"""貼付け位置。

移行元 file_table（J:\\empire_info_ak.sql）との対応:
  id           -> source_id（移行元の登録番号。新規登録は空）
  file_name    -> original_name（利用者が付けたファイル名）
  file_path    -> 取り込み時の参照だけに使い、保存は relative_path
  costomer     -> customer
  part         -> part
  insert_time  -> created_at
  update_time  -> updated_at
画像本体は data/images/placement/ に置き、DBには data からの相対パスだけを入れる。
"""
import json, re, secrets, shutil
from datetime import datetime
from pathlib import Path
from PIL import Image, UnidentifiedImageError

DATA_DIR=None
def bind(data_dir):
    global DATA_DIR
    DATA_DIR=Path(data_dir)

SOURCE_SQL=Path(r'J:\empire_info_ak.sql')
SOURCE_ROOT=Path(r'J:\htdocs\upload')
MAX_BYTES=10*1024*1024
PAGE=24
KINDS={'JPEG':'image/jpeg','PNG':'image/png'}

CORRECTION_STATUSES = ('未対応', '対応中', '対応済み')
CORRECTION_PAGE = 40

def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS placements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER UNIQUE,
        original_name TEXT NOT NULL DEFAULT '',
        relative_path TEXT NOT NULL DEFAULT '',
        thumb_path TEXT NOT NULL DEFAULT '',
        customer TEXT NOT NULL DEFAULT '',
        part TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        created_by_id INTEGER,
        updated_by_id INTEGER,
        version INTEGER NOT NULL DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS placement_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        placement_id INTEGER NOT NULL,
        at TEXT NOT NULL,
        action TEXT NOT NULL,
        actor_id INTEGER,
        actor TEXT NOT NULL,
        changes TEXT NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS placement_corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        placement_id INTEGER,
        body TEXT NOT NULL,
        author_id INTEGER,
        author_name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '未対応',
        status_by_id INTEGER,
        status_by_name TEXT NOT NULL DEFAULT '',
        status_at TEXT NOT NULL DEFAULT '',
        response TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_placement_corrections_status ON placement_corrections(status, id DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_placement_corrections_placement ON placement_corrections(placement_id)')

def image_root(data):
    root=data/'images'/'placement'
    (root/'thumbs').mkdir(parents=True, exist_ok=True)
    return root

def _safe_file(data, relative):
    if not relative or not isinstance(relative, str): return None
    rel=relative.replace('\\','/').lstrip('/')
    if '..' in rel.split('/'): return None
    root=(data/'images'/'placement').resolve()
    path=(data/rel).resolve()
    if path!=root and root not in path.parents: return None
    return path

def _check_image(path):
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            kind=im.format
            if kind not in KINDS: raise ValueError('JPEGまたはPNGの画像を選んでください。')
            im.load()
            return kind
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError('画像として読み込めません。JPEGまたはPNGを選んでください。')

def _write_thumb(src, dest):
    with Image.open(src) as im:
        im=im.convert('RGB')
        im.thumbnail((960,960), Image.Resampling.LANCZOS)
        im.save(dest, 'JPEG', quality=95, subsampling=0, optimize=True)

def _store_image(data, raw, original_name):
    if len(raw)>MAX_BYTES: raise ValueError('画像は10MBまでです。')
    root=image_root(data)
    temp=root/f'.uploading-{secrets.token_hex(8)}'
    temp.write_bytes(raw)
    try:
        kind=_check_image(temp)
        ext='.jpg' if kind=='JPEG' else '.png'
        stored=secrets.token_hex(16)+ext
        final=root/stored
        temp.replace(final)
        thumb=root/'thumbs'/f'{final.stem}.jpg'
        _write_thumb(final, thumb)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    name=Path(str(original_name or 'image'+ext)).name[:255]
    return name, f'images/placement/{stored}', f'images/placement/thumbs/{final.stem}.jpg'

def _actor(user):
    return f"{user['name']} ({user['login']})"

def _row_public(row):
    return {'id':row['id'],'sourceId':row['source_id'],'number':row['source_id'] or row['id'],'originalName':row['original_name'],'customer':row['customer'],'part':row['part'],'hasImage':bool(row['relative_path']),'createdAt':row['created_at'],'updatedAt':row['updated_at'],'version':row['version']}

def _require_view(user):
    if not user or user['must_change']: return 401, {'error':'ログインし直してください。'}
    if user['role'] not in ('owner','sales','floor','floor2','floor3'): return 403, {'error':'貼付け位置は閲覧できません。'}
    return None

def _require_edit(user):
    blocked=_require_view(user)
    if blocked: return blocked
    if user['role'] not in ('owner','floor2','floor3'): return 403, {'error':'貼付け位置の登録と修正は主管理者と現場２・現場３が行えます。'}
    return None

def _text(value, label, limit):
    if not isinstance(value, str): raise ValueError(f'{label}を入力してください。')
    text=value.strip()
    if not text: raise ValueError(f'{label}を入力してください。')
    if len(text)>limit: raise ValueError(f'{label}は{limit}文字以内で入力してください。')
    return text

def search(handler, c):
    from urllib.parse import parse_qs, urlparse
    blocked=_require_view(handler.user)
    if blocked: return blocked
    q=parse_qs(urlparse(handler.path).query)
    customer=(q.get('customer') or [''])[0].strip()
    part=(q.get('part') or [''])[0].strip()
    try: offset=max(0, int((q.get('offset') or ['0'])[0]))
    except ValueError: offset=0
    where=[]; args=[]
    if customer:
        where.append('customer LIKE ?'); args.append(f'%{customer}%')
    if part:
        where.append('part LIKE ?'); args.append(f'%{part}%')
    sql=' FROM placements'+((' WHERE '+' AND '.join(where)) if where else '')
    total=c.execute('SELECT COUNT(*)'+sql, args).fetchone()[0]
    rows=c.execute('SELECT *'+sql+' ORDER BY customer, part, id LIMIT ? OFFSET ?', args+[PAGE, offset]).fetchall()
    return 200, {'total':total,'offset':offset,'limit':PAGE,'items':[_row_public(r) for r in rows]}

def customers(handler, c):
    blocked=_require_view(handler.user)
    if blocked: return blocked
    names=[r['customer'] for r in c.execute("SELECT DISTINCT customer FROM placements WHERE customer<>'' ORDER BY customer")]
    parts=[r['part'] for r in c.execute("SELECT DISTINCT part FROM placements WHERE part<>'' ORDER BY part")]
    return 200, {'customers':names,'parts':parts}

def history(handler, c, pid):
    blocked=_require_view(handler.user)
    if blocked: return blocked
    row=c.execute('SELECT id FROM placements WHERE id=?',(pid,)).fetchone()
    if not row: return 404, {'error':'貼付け位置が見つかりません。'}
    rows=[dict(r) for r in c.execute('SELECT at,action,actor,changes FROM placement_history WHERE placement_id=? ORDER BY id DESC',(pid,))]
    return 200, rows

def send_file(handler, c, pid, thumb):
    blocked=_require_view(handler.user)
    if blocked: return blocked
    row=c.execute('SELECT * FROM placements WHERE id=?',(pid,)).fetchone()
    if not row: return 404, {'error':'貼付け位置が見つかりません。'}
    rel=row['thumb_path'] if thumb else row['relative_path']
    path=_safe_file(DATA_DIR, rel)
    if not path or not path.is_file(): return 404, {'error':'画像が見つかりません。'}
    kind='image/jpeg' if path.suffix.lower() in ('.jpg','.jpeg') else 'image/png'
    return 200, path.read_bytes(), kind

def _decode_image(d):
    import base64
    raw_b64=d.get('data')
    if not isinstance(raw_b64, str) or not raw_b64: raise ValueError('画像を選んでください。')
    try: raw=base64.b64decode(raw_b64, validate=True)
    except Exception: raise ValueError('画像を読み込めません。')
    return raw

def create(handler, c, d):
    blocked=_require_edit(handler.user)
    if blocked: return blocked
    customer=_text(d.get('customer'),'顧客',50)
    part=_text(d.get('part'),'貼付け箇所',50)
    raw=_decode_image(d)
    original, rel, thumb=_store_image(DATA_DIR, raw, d.get('filename'))
    now=datetime.now().isoformat(timespec='seconds')
    cur=c.execute('''INSERT INTO placements(source_id,original_name,relative_path,thumb_path,customer,part,created_at,updated_at,created_by_id,updated_by_id,version)
        VALUES(NULL,?,?,?,?,?,?,?,?,?,1)''',(original, rel, thumb, customer, part, now, now, handler.user['id'], handler.user['id']))
    pid=cur.lastrowid
    changes={'customer':{'before':'','after':customer},'part':{'before':'','after':part},'originalName':{'before':'','after':original}}
    c.execute('INSERT INTO placement_history(placement_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(pid, now, '登録', handler.user['id'], _actor(handler.user), json.dumps(changes, ensure_ascii=False)))
    return 200, _row_public(c.execute('SELECT * FROM placements WHERE id=?',(pid,)).fetchone())

def update(handler, c, d):
    blocked=_require_edit(handler.user)
    if blocked: return blocked
    if type(d.get('id')) is not int or type(d.get('version')) is not int: return 400, {'error':'貼付け位置を選択してください。'}
    row=c.execute('SELECT * FROM placements WHERE id=?',(d['id'],)).fetchone()
    if not row: return 404, {'error':'貼付け位置が見つかりません。'}
    if row['version']!=d['version']: return 409, {'error':'別の画面で更新されました。入力内容は残しています。開き直してから保存してください。'}
    customer=_text(d.get('customer'),'顧客',50)
    part=_text(d.get('part'),'貼付け箇所',50)
    data=DATA_DIR
    new_files=None
    if d.get('data'):
        raw=_decode_image(d)
        new_files=_store_image(data, raw, d.get('filename'))
    now=datetime.now().isoformat(timespec='seconds')
    original, rel, thumb=new_files if new_files else (row['original_name'], row['relative_path'], row['thumb_path'])
    before={'customer':row['customer'],'part':row['part'],'originalName':row['original_name']}
    after={'customer':customer,'part':part,'originalName':original}
    changes={k:{'before':before[k],'after':after[k]} for k in before if before[k]!=after[k]}
    if not changes: return 200, _row_public(row)
    c.execute('''UPDATE placements SET original_name=?,relative_path=?,thumb_path=?,customer=?,part=?,updated_at=?,updated_by_id=?,version=version+1 WHERE id=?''',(original, rel, thumb, customer, part, now, handler.user['id'], row['id']))
    c.execute('INSERT INTO placement_history(placement_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'], now, '修正', handler.user['id'], _actor(handler.user), json.dumps(changes, ensure_ascii=False)))
    if new_files and row['relative_path'] and row['relative_path']!=rel:
        for old in (row['relative_path'], row['thumb_path']):
            path=_safe_file(data, old)
            if path and path.is_file(): path.unlink()
    return 200, _row_public(c.execute('SELECT * FROM placements WHERE id=?',(row['id'],)).fetchone())

def _sql_rows(text):
    rows=[]
    pattern=re.compile(r"\((\d+),\s*'((?:\\'|[^'])*)',\s*'((?:\\'|[^'])*)',\s*(NULL|'(?:\\'|[^'])*'),\s*'((?:\\'|[^'])*)',\s*'((?:\\'|[^'])*)',\s*'((?:\\'|[^'])*)'\)")
    def unquote(value):
        if value=='NULL': return ''
        return value[1:-1].replace("\\'","'").replace('\\\\','\\') if value.startswith("'") else value.replace("\\'","'")
    start=0
    while True:
        start=text.find('INSERT INTO `file_table`', start)
        if start<0: break
        end=text.find(';\n', start)
        if end<0: end=len(text)
        for match in pattern.finditer(text, start, end):
            rows.append({'id':int(match.group(1)),'file_name':unquote("'"+match.group(2)+"'"),'file_path':unquote("'"+match.group(3)+"'"),'customer':unquote(match.group(4)),'part':unquote("'"+match.group(5)+"'"),'created_at':match.group(6),'updated_at':match.group(7)})
        start=end+2
    return rows

def _source_file(file_path):
    rel=str(file_path or '').replace('\\','/').strip()
    if not rel.startswith('DATA/') or '..' in rel.split('/'): return None
    name=rel.split('/',1)[1]
    if not name or '/' in name or name in ('.','..'): return None
    path=(SOURCE_ROOT/'DATA'/name).resolve()
    root=(SOURCE_ROOT/'DATA').resolve()
    if path!=root and root not in path.parents: return None
    return path if path.is_file() else None

def import_sources(connect, data_dir):
    if not SOURCE_SQL.is_file():
        return {'skipped':True,'reason':'SQLがありません。'}
    text=SOURCE_SQL.read_text(encoding='utf-8')
    parsed=_sql_rows(text)
    report={'sourceRows':len(parsed),'imported':0,'already':0,'filledImage':0,'missingImage':[],'unreadable':[]}
    image_root(data_dir)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for row in parsed:
            existing=c.execute('SELECT * FROM placements WHERE source_id=?',(row['id'],)).fetchone()
            src=_source_file(row['file_path'])
            if existing:
                report['already']+=1
                if existing['updated_by_id'] is None and not existing['relative_path'] and src:
                    try:
                        raw=src.read_bytes()
                        original, rel, thumb=_store_image(data_dir, raw, row['file_name'])
                    except Exception as e:
                        report['unreadable'].append({'sourceId':row['id'],'file':row['file_path'],'error':str(e)})
                        continue
                    c.execute('UPDATE placements SET original_name=?,relative_path=?,thumb_path=? WHERE id=? AND updated_by_id IS NULL',(original, rel, thumb, existing['id']))
                    report['filledImage']+=1
                continue
            original, rel, thumb, missing='', '', '', False
            if not src:
                missing=True
                report['missingImage'].append({'sourceId':row['id'],'customer':row['customer'],'part':row['part'],'file':row['file_path']})
                original=Path(row['file_name']).name[:255]
            else:
                try:
                    raw=src.read_bytes()
                    original, rel, thumb=_store_image(data_dir, raw, row['file_name'])
                except Exception as e:
                    missing=True
                    original=Path(row['file_name']).name[:255]
                    report['unreadable'].append({'sourceId':row['id'],'file':row['file_path'],'error':str(e)})
            c.execute('''INSERT INTO placements(source_id,original_name,relative_path,thumb_path,customer,part,created_at,updated_at,created_by_id,updated_by_id,version)
                VALUES(?,?,?,?,?,?,?,?,NULL,NULL,1)''',(row['id'], original, rel, thumb, row['customer'], row['part'], row['created_at'], row['updated_at']))
            report['imported']+=1
            if missing and src is None and not any(x['sourceId']==row['id'] for x in report['missingImage']):
                report['missingImage'].append({'sourceId':row['id'],'customer':row['customer'],'part':row['part'],'file':row['file_path']})
        c.execute("INSERT OR REPLACE INTO settings VALUES('placement_import',?)",(json.dumps({'sourceRows':report['sourceRows'],'imported':report['imported'],'already':report['already'],'missing':len(report['missingImage']),'unreadable':len(report['unreadable'])},ensure_ascii=False),))
    (data_dir/'placement-import-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report

def get(handler, c, pid):
    blocked=_require_view(handler.user)
    if blocked: return blocked
    row=c.execute('SELECT * FROM placements WHERE id=?',(pid,)).fetchone()
    if not row: return 404, {'error':'貼付け位置が見つかりません。'}
    return 200, _row_public(row)

def _correction_public(row):
    return {
        'id':row['id'],
        'placementId':row['placement_id'],
        'customer':row['customer'] or '',
        'part':row['part'] or '',
        'hasPlacement':row['placement_id'] is not None,
        'placementMissing':row['placement_id'] is not None and not row['placement_exists'],
        'body':row['body'],
        'authorId':row['author_id'],
        'authorName':row['author_name'],
        'createdAt':row['created_at'],
        'status':row['status'],
        'statusById':row['status_by_id'],
        'statusByName':row['status_by_name'],
        'statusAt':row['status_at'],
        'response':row['response'],
        'updatedAt':row['updated_at'],
        'version':row['version'],
    }

def _correction_select(where='', args=None, limit=None, offset=0):
    sql='''SELECT c.*,
        p.customer AS customer,
        p.part AS part,
        CASE WHEN c.placement_id IS NULL THEN 0 WHEN p.id IS NULL THEN 0 ELSE 1 END AS placement_exists
        FROM placement_corrections c
        LEFT JOIN placements p ON p.id=c.placement_id'''
    if where: sql+=' WHERE '+where
    sql+=' ORDER BY CASE c.status WHEN \'未対応\' THEN 0 WHEN \'対応中\' THEN 1 ELSE 2 END, c.id DESC'
    if limit is not None:
        sql+=' LIMIT ? OFFSET ?'
        return sql, list(args or [])+[limit, offset]
    return sql, list(args or [])

def list_corrections(handler, c):
    from urllib.parse import parse_qs, urlparse
    blocked=_require_view(handler.user)
    if blocked: return blocked
    q=parse_qs(urlparse(handler.path).query)
    status=(q.get('status') or [''])[0].strip()
    try: offset=max(0, int((q.get('offset') or ['0'])[0]))
    except ValueError: offset=0
    where=[]; args=[]
    if status:
        if status not in CORRECTION_STATUSES: return 400, {'error':'対応状況を確認してください。'}
        where.append('c.status=?'); args.append(status)
    where_sql=' AND '.join(where)
    count_sql='SELECT COUNT(*) FROM placement_corrections c'+(' WHERE '+where_sql if where_sql else '')
    total=c.execute(count_sql, args).fetchone()[0]
    sql, sql_args=_correction_select(where_sql, args, CORRECTION_PAGE, offset)
    rows=c.execute(sql, sql_args).fetchall()
    counts={s:c.execute('SELECT COUNT(*) FROM placement_corrections WHERE status=?',(s,)).fetchone()[0] for s in CORRECTION_STATUSES}
    return 200, {'total':total,'offset':offset,'limit':CORRECTION_PAGE,'counts':counts,'items':[_correction_public(r) for r in rows]}

def create_correction(handler, c, d):
    blocked=_require_view(handler.user)
    if blocked: return blocked
    body=_text(d.get('body'),'依頼内容',2000)
    placement_id=d.get('placementId')
    if placement_id is None or placement_id=='':
        placement_id=None
    elif type(placement_id) is not int or placement_id<=0:
        return 400, {'error':'貼付け位置を確認してください。'}
    else:
        if not c.execute('SELECT id FROM placements WHERE id=?',(placement_id,)).fetchone():
            return 404, {'error':'貼付け位置が見つかりません。'}
    now=datetime.now().isoformat(timespec='seconds')
    user=handler.user
    cur=c.execute('''INSERT INTO placement_corrections(
        placement_id,body,author_id,author_name,created_at,status,status_by_id,status_by_name,status_at,response,updated_at,version)
        VALUES(?,?,?,?,?,'未対応',NULL,'','','',?,1)''',
        (placement_id, body, user['id'], user['name'], now, now))
    sql, args=_correction_select('c.id=?', [cur.lastrowid])
    return 200, _correction_public(c.execute(sql, args).fetchone())

def update_correction(handler, c, d):
    blocked=_require_edit(handler.user)
    if blocked: return blocked
    if type(d.get('id')) is not int or type(d.get('version')) is not int:
        return 400, {'error':'修正依頼を選択してください。'}
    row=c.execute('SELECT * FROM placement_corrections WHERE id=?',(d['id'],)).fetchone()
    if not row: return 404, {'error':'修正依頼が見つかりません。'}
    if row['version']!=d['version']:
        return 409, {'error':'別の画面で更新されました。開き直してから保存してください。'}
    status=d.get('status')
    if status not in CORRECTION_STATUSES: return 400, {'error':'対応状況を確認してください。'}
    raw=d.get('response')
    if raw is None: raw=row['response']
    if not isinstance(raw, str): return 400, {'error':'対応コメントを確認してください。'}
    response=raw.strip()
    if len(response)>2000: return 400, {'error':'対応コメントは2000文字以内で入力してください。'}
    if status==row['status'] and response==row['response']:
        sql, args=_correction_select('c.id=?', [row['id']])
        return 200, _correction_public(c.execute(sql, args).fetchone())
    now=datetime.now().isoformat(timespec='seconds')
    user=handler.user
    c.execute('''UPDATE placement_corrections SET status=?,status_by_id=?,status_by_name=?,status_at=?,response=?,updated_at=?,version=version+1 WHERE id=?''',
        (status, user['id'], user['name'], now, response, now, row['id']))
    sql, args=_correction_select('c.id=?', [row['id']])
    return 200, _correction_public(c.execute(sql, args).fetchone())

def make_backup(connect, data_dir):
    import zipfile
    folder=data_dir/'backups'
    folder.mkdir(exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    dest=folder/f'desk-{stamp}.zip'
    temp=folder/f'.desk-{stamp}.sqlite3'
    sqlite3=__import__('sqlite3')
    target=sqlite3.connect(temp)
    try:
        with connect() as source:
            source.backup(target)
    finally:
        target.close()
    with zipfile.ZipFile(dest, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(temp, 'instructions.sqlite3')
        for folder, label in ((data_dir/'images'/'placement','placement'), (data_dir/'images'/'names','names')):
            if folder.exists():
                for path in folder.rglob('*'):
                    if path.is_file() and not path.name.startswith('.'):
                        zf.write(path, str(Path('images')/label/path.relative_to(folder)).replace('\\','/'))
    try: temp.unlink()
    except OSError: pass
    return dest
