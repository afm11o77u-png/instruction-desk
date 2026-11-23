from contextlib import closing,contextmanager
from datetime import date,datetime
from pathlib import Path
import copy
import json
import sqlite3
import uuid

KINDS=['個人レンタル','リース','販売','その他']
SERVICES=['レンタル','リース','CL','クリーニングのみ']
OWNERS=['自社所有','顧客所有','着用者所有','その他','未確認']
SCHEMA="""
CREATE TABLE IF NOT EXISTS rental_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO rental_meta VALUES('schema_version','1');
CREATE TABLE IF NOT EXISTS contracts(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,source_ref TEXT UNIQUE,version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,external_key TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS contracts_customer ON contracts(customer_id,active,id);
CREATE TABLE IF NOT EXISTS contract_lines(id INTEGER PRIMARY KEY,contract_id INTEGER NOT NULL REFERENCES contracts(id),customer_product_id INTEGER NOT NULL,product_id INTEGER NOT NULL,quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 999),UNIQUE(contract_id,customer_product_id));
CREATE TABLE IF NOT EXISTS wearers(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,employee_code TEXT NOT NULL,name TEXT NOT NULL,kana TEXT NOT NULL DEFAULT '',site_id TEXT,department TEXT NOT NULL DEFAULT '',contract_id INTEGER REFERENCES contracts(id),status TEXT NOT NULL DEFAULT '在籍',joined_date TEXT NOT NULL DEFAULT '',left_date TEXT NOT NULL DEFAULT '',size_notes TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,external_key TEXT UNIQUE,UNIQUE(customer_id,employee_code));
CREATE INDEX IF NOT EXISTS wearers_customer ON wearers(customer_id,status,id);
CREATE TABLE IF NOT EXISTS units(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,asset_code TEXT NOT NULL UNIQUE,barcode TEXT NOT NULL UNIQUE,customer_product_id INTEGER NOT NULL,product_id INTEGER NOT NULL,size TEXT NOT NULL,status TEXT NOT NULL DEFAULT '待機',acquired_date TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,external_key TEXT UNIQUE);
CREATE INDEX IF NOT EXISTS units_customer ON units(customer_id,status,id);
CREATE TABLE IF NOT EXISTS loans(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,unit_id INTEGER NOT NULL REFERENCES units(id),wearer_id INTEGER NOT NULL REFERENCES wearers(id),contract_id INTEGER NOT NULL REFERENCES contracts(id),issued_date TEXT NOT NULL,returned_date TEXT,issued_snapshot TEXT NOT NULL,return_notes TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_loan ON loans(unit_id) WHERE returned_date IS NULL;
CREATE INDEX IF NOT EXISTS loans_wearer ON loans(wearer_id,returned_date,id);
CREATE INDEX IF NOT EXISTS loans_customer ON loans(customer_id,returned_date,id);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,entity TEXT NOT NULL,entity_id INTEGER NOT NULL,action TEXT NOT NULL,actor TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_entity ON events(entity,entity_id,id);
CREATE INDEX IF NOT EXISTS events_customer ON events(customer_id,id);
"""


def can_open(user):
    return bool(user and not user['must_change'] and (user['role'] in ('owner','sales','warehouse') or user['manage']))


def can_write(user):
    return can_open(user) and (user['role'] in ('owner','sales') or bool(user['manage']))


def initialize(path):
    with closing(sqlite3.connect(path,timeout=15)) as c:
        c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON');c.executescript(SCHEMA)
        c.execute('BEGIN IMMEDIATE')
        for table,columns in {'contracts':{'service_type':"TEXT NOT NULL DEFAULT 'レンタル'",'default_ownership':"TEXT NOT NULL DEFAULT '自社所有'"},'units':{'ownership':"TEXT NOT NULL DEFAULT '未確認'"},'loans':{'relation_type':"TEXT NOT NULL DEFAULT '貸与'"}}.items():
            existing_columns={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
            for name,definition in columns.items():
                if name not in existing_columns:c.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+definition)
        c.execute('CREATE TABLE IF NOT EXISTS wearer_contracts(wearer_id INTEGER NOT NULL REFERENCES wearers(id),contract_id INTEGER NOT NULL REFERENCES contracts(id),PRIMARY KEY(wearer_id,contract_id))')
        if c.execute("SELECT value FROM rental_meta WHERE key='schema_version'").fetchone()[0]=='1':
            c.execute('INSERT OR IGNORE INTO wearer_contracts SELECT id,contract_id FROM wearers WHERE contract_id IS NOT NULL')
        c.execute("UPDATE rental_meta SET value='2' WHERE key='schema_version'");c.commit()


@contextmanager
def connection(path):
    c=sqlite3.connect(path,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
    try:yield c
    finally:c.close()


def catalog(desk,stock,user):
    import web_requests
    import web_request_organizations as org
    if not can_open(user):raise PermissionError('レンタル管理を開く権限がありません。')
    data=web_requests.catalog(stock)
    if user['role']!='owner' and not user['manage'] and user['role']!='warehouse':
        assigned=org.staff_ids(org.read(desk),user['id'])
        data['customers']=[x for x in data['customers'] if x['id'] in assigned or web_requests.permitted(web_requests.read_config(desk,x['id']),user)]
    allowed={x['id'] for x in data['customers']}
    with closing(sqlite3.connect(stock.resolve().as_uri()+'?mode=ro',uri=True)) as s:
        s.row_factory=sqlite3.Row
        handles=[dict(r) for r in s.execute('SELECT id,customer_id,product_id,embroidery FROM customer_products WHERE active=1')]
    data['handles']=[x for x in handles if x['customer_id'] in allowed]
    pids={x['product_id'] for x in data['handles']};data['products']=[x for x in data['products'] if x['id'] in pids]
    data['sites']=[n for n in org.read(desk)['nodes'] if n['kind']=='site' and n['customer_id'] in allowed]
    presets=[]
    for customer in data['customers']:
        cfg=web_requests.read_config(desk,customer['id'])['draft']
        for dep in cfg['departments']:
            for k in dep['contracts']:
                if k['active']:
                    presets.append(dict(ref=str(customer['id'])+':'+dep['id']+':'+k['id'],customer_id=customer['id'],name=k['name'],department=dep['name'],kind=k['kind'],lines=[dict(customer_product_id=u['customer_product_id'],quantity=u['default_qty']) for u in k['uniforms'] if u['active'] and u['customer_product_id']]))
    from common_contracts import records
    data['common_contracts']=records(desk,allowed);data['sharedContracts']=True
    data['presets']=presets;data['canWrite']=can_write(user);return data


def text(value,label,limit=200,required=False):
    if not isinstance(value,str):raise ValueError(label+'を確認してください。')
    value=value.strip()
    if len(value)>limit or (required and not value):raise ValueError(label+'を確認してください。')
    return value


def integer(value,label,minimum=1,maximum=2147483647):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError(label+'を確認してください。')
    return value


def day(value,label,required=False):
    value=text(value,label,10,required)
    if value:
        try:
            if date.fromisoformat(value).isoformat()!=value:raise ValueError()
        except ValueError:raise ValueError(label+'は正しい日付を入力してください。')
    return value


def scope(data,cid):
    cid=integer(cid,'顧客')
    if cid not in {x['id'] for x in data['customers']}:raise PermissionError('この顧客を扱う権限がありません。')
    return cid


def existing(c,table,ident,data):
    row=c.execute('SELECT * FROM '+table+' WHERE id=?',(integer(ident,'対象'),)).fetchone()
    if not row:raise ValueError('対象が見つかりません。再読込してください。')
    scope(data,row['customer_id']);return dict(row)


def version(row,d):
    if type(d.get('version')) is not int or d['version']!=row['version']:raise ValueError('別の画面で変更されています。再読込してからやり直してください。')


def handle(data,cid,hid,size=None):
    hid=integer(hid,'商品')
    h=next((x for x in data['handles'] if x['id']==hid and x['customer_id']==cid),None)
    p=next((x for x in data['products'] if h and x['id']==h['product_id']),None)
    if not h or not p:raise ValueError('この顧客の有効な制服を選択してください。')
    if size is not None and size not in p['sizes']:raise ValueError('在庫の商品に登録されたサイズを選択してください。')
    return h,p


def emit(c,cid,entity,ident,action,user,payload):
    c.execute('INSERT INTO events(customer_id,entity,entity_id,action,actor,at,payload) VALUES(?,?,?,?,?,?,?)',(cid,entity,ident,action,user['name'],datetime.now().isoformat(timespec='seconds'),json.dumps(payload,ensure_ascii=False)))


def insert_or_update(c,table,values,row):
    now=datetime.now().isoformat(timespec='seconds');values=dict(values,updated_at=now)
    if row:
        c.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in values)+',version=version+1 WHERE id=?',(*values.values(),row['id']));return row['id']
    values.update(created_at=now)
    return c.execute('INSERT INTO '+table+'('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values())).lastrowid


def save(path,data,user,d):
    if not can_write(user):raise PermissionError('登録・貸与を行う権限がありません。')
    if not isinstance(d,dict):raise ValueError('入力を確認してください。')
    entity=d.get('entity');action=d.get('action','save')
    if entity=='contract' and data.get('sharedContracts'):raise ValueError('契約・対象制服・枚数は、指示書デスクの「顧客・契約管理」で登録・変更してください。')
    if entity not in ('contract','wearer','unit','loan'):raise ValueError('操作を確認してください。')
    table={'contract':'contracts','wearer':'wearers','unit':'units','loan':'loans'}[entity]
    with connection(path) as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            row=existing(c,table,d['id'],data) if d.get('id') else None
            if row:version(row,d)
            if entity=='loan':
                result=loan_action(c,data,user,d,row,action);c.commit();return result
            if action!='save':raise ValueError('操作を確認してください。')
            cid=scope(data,d.get('customer_id'))
            if row and row['customer_id']!=cid:raise ValueError('登録後の顧客は変更できません。')
            notes=text(d.get('notes',''),'備考',2000)
            if entity=='contract':
                name=text(d.get('name',''),'契約名',100,True);kind=d.get('kind');start=day(d.get('start_date',''),'契約開始日',True);end=day(d.get('end_date',''),'契約終了日')
                service_type=d.get('service_type',row['service_type'] if row else 'レンタル');default_ownership=d.get('default_ownership',row['default_ownership'] if row else ('顧客所有' if service_type=='クリーニングのみ' else '自社所有'))
                if service_type not in SERVICES or default_ownership not in OWNERS:raise ValueError('取引区分・所有区分の初期値を確認してください。')
                if kind not in KINDS or (end and end<start):raise ValueError('契約形態・期間を確認してください。')
                if type(d.get('active')) is not bool:raise ValueError('契約の有効状態を確認してください。')
                ref=d.get('source_ref') or None
                if ref and not (row and row['source_ref']==ref) and not any(p['ref']==ref and p['customer_id']==cid for p in data['presets']):raise ValueError('参照するWEB契約を確認してください。')
                lines=d.get('lines',[])
                if not isinstance(lines,list) or not 1<=len(lines)<=200:raise ValueError('契約の制服を1件以上登録してください。')
                checked=[];seen=set()
                for item in lines:
                    if not isinstance(item,dict):raise ValueError('契約の制服を確認してください。')
                    h,p=handle(data,cid,item.get('customer_product_id'))
                    if h['id'] in seen:raise ValueError('同じ制服・加工が重複しています。')
                    seen.add(h['id']);checked.append((h['id'],p['id'],integer(item.get('quantity'),'貸与枚数',1,999)))
                # An existing WEB reference is immutable so it cannot be silently switched.
                if row and ref!=row['source_ref']:raise ValueError('登録後のWEB契約の参照先は変更できません。')
                ident=insert_or_update(c,table,dict(customer_id=cid,name=name,kind=kind,start_date=start,end_date=end,notes=notes,active=int(d['active']),source_ref=ref,service_type=service_type,default_ownership=default_ownership),row)
                c.execute('DELETE FROM contract_lines WHERE contract_id=?',(ident,));c.executemany('INSERT INTO contract_lines(contract_id,customer_product_id,product_id,quantity) VALUES(?,?,?,?)',[(ident,*x) for x in checked])
                snapshot=dict(d,lines=[dict(customer_product_id=x[0],product_id=x[1],quantity=x[2]) for x in checked])
            elif entity=='wearer':
                name=text(d.get('name',''),'着用者名',100,True);code=text(d.get('employee_code',''),'社員・管理番号',80,True);kana=text(d.get('kana',''),'フリガナ',100);department=text(d.get('department',''),'部署',100);site=d.get('site_id') or None
                if site and not any(n['id']==site and n['customer_id']==cid for n in data['sites']):raise ValueError('この顧客の拠点を選択してください。')
                status=d.get('status');joined=day(d.get('joined_date',''),'入社日');left=day(d.get('left_date',''),'退職日')
                if status not in ('在籍','休止','退職') or (joined and left and left<joined):raise ValueError('在籍状態と日付を確認してください。')
                contract_ids=d.get('contract_ids',([d['contract_id']] if d.get('contract_id') else []))
                if not isinstance(contract_ids,list) or len(contract_ids)>200:raise ValueError('利用する契約を確認してください。')
                contract_ids=list(dict.fromkeys(integer(k,'契約') for k in contract_ids))
                for contract_id in contract_ids:
                    contract=existing(c,'contracts',contract_id,data)
                    if contract['customer_id']!=cid:raise ValueError('同じ顧客の契約を選択してください。')
                contract_id=contract_ids[0] if contract_ids else None
                ident=insert_or_update(c,table,dict(customer_id=cid,name=name,employee_code=code,kana=kana,site_id=site,department=department,contract_id=contract_id,status=status,joined_date=joined,left_date=left,size_notes=text(d.get('size_notes',''),'サイズ情報',500),notes=notes),row)
                c.execute('DELETE FROM wearer_contracts WHERE wearer_id=?',(ident,))
                c.executemany('INSERT INTO wearer_contracts VALUES(?,?)',[(ident,k) for k in contract_ids]);snapshot=dict(d,contract_ids=contract_ids)
            else:
                barcode=text(d.get('barcode',''),'個体バーコード',150,True);size=text(d.get('size',''),'サイズ',40,True);h,p=handle(data,cid,d.get('customer_product_id'),size);status=d.get('status')
                ownership=d.get('ownership',row['ownership'] if row else '未確認')
                if ownership not in OWNERS:raise ValueError('制服の所有区分を確認してください。')
                if status not in ('待機','修理','廃棄'):raise ValueError('個体の状態を確認してください。')
                if row and (row['customer_product_id']!=h['id'] or row['size']!=size or row['barcode']!=barcode):raise ValueError('個体の登録後は、商品・サイズ・バーコードを変更できません。')
                if row and c.execute('SELECT 1 FROM loans WHERE unit_id=? AND returned_date IS NULL',(row['id'],)).fetchone() and status!='待機':raise ValueError('貸与中の個体は、先に返却を記録してください。')
                ident=insert_or_update(c,table,dict(customer_id=cid,asset_code=row['asset_code'] if row else 'PENDING-'+uuid.uuid4().hex,barcode=barcode,customer_product_id=h['id'],product_id=p['id'],size=size,status=status,acquired_date=day(d.get('acquired_date',''),'登録・取得日'),notes=notes,ownership=ownership),row)
                if not row:c.execute('UPDATE units SET asset_code=? WHERE id=?',('RU-'+str(ident).zfill(8),ident))
                snapshot=d
            emit(c,cid,entity,ident,'変更' if row else '登録',user,snapshot);c.commit();return dict(ok=True,id=ident)
        except sqlite3.IntegrityError as e:
            c.rollback();raise ValueError('管理番号・個体バーコード・WEB契約の参照先が重複していないか確認してください。') from e
        except Exception:c.rollback();raise


def loan_action(c,data,user,d,row,action):
    today=date.today().isoformat()
    if action in ('issue','assign') and row is None:
        unit=existing(c,'units',d.get('unit_id'),data);wearer=existing(c,'wearers',d.get('wearer_id'),data);contract=existing(c,'contracts',d.get('contract_id'),data)
        if len({unit['customer_id'],wearer['customer_id'],contract['customer_id']})!=1:raise ValueError('顧客の異なる制服・着用者・契約を組み合わせることはできません。')
        relation_type='着用登録' if action=='assign' else '貸与'
        if (contract['service_type'] in ('CL','クリーニングのみ'))!=(action=='assign'):raise ValueError('クリーニングのみの契約は着用登録、レンタル契約は貸与を選択してください。')
        if action=='assign' and unit['ownership']=='未確認':raise ValueError('着用登録の前に制服の所有区分を確認してください。')
        links=[r[0] for r in c.execute('SELECT contract_id FROM wearer_contracts WHERE wearer_id=?',(wearer['id'],))]
        if contract['id'] not in links:raise ValueError('着用者の利用契約に、この契約を登録してください。')
        issued=day(d.get('issued_date',today),'開始日',True)
        if wearer['status']!='在籍' or unit['status']!='待機' or not contract['active'] or issued<contract['start_date'] or (contract['end_date'] and issued>contract['end_date']):raise ValueError('着用者・個体の状態と、契約の有効期間を確認してください。')
        if c.execute('SELECT 1 FROM loans WHERE unit_id=? AND returned_date IS NULL',(unit['id'],)).fetchone():raise ValueError('この個体はすでに貸与・着用登録中です。')
        if not c.execute('SELECT 1 FROM contract_lines WHERE contract_id=? AND customer_product_id=?',(contract['id'],unit['customer_product_id'])).fetchone():raise ValueError('この制服は選択した契約の対象商品にありません。')
        snapshot=dict(unit=unit,wearer=wearer,contract=contract,lines=[dict(r) for r in c.execute('SELECT * FROM contract_lines WHERE contract_id=?',(contract['id'],))])
        now=datetime.now().isoformat(timespec='seconds')
        ident=c.execute('INSERT INTO loans(customer_id,unit_id,wearer_id,contract_id,issued_date,issued_snapshot,created_at,updated_at,relation_type) VALUES(?,?,?,?,?,?,?,?,?)',(unit['customer_id'],unit['id'],wearer['id'],contract['id'],issued,json.dumps(snapshot,ensure_ascii=False),now,now,relation_type)).lastrowid
        c.execute('UPDATE units SET version=version+1,updated_at=? WHERE id=?',(now,unit['id']))
        emit(c,unit['customer_id'],'unit',unit['id'],relation_type,user,dict(loan_id=ident,wearer_id=wearer['id'],contract_id=contract['id'],date=issued));return dict(ok=True,id=ident)
    if action in ('return','unassign') and row:
        if (row['relation_type']=='着用登録')!=(action=='unassign'):raise ValueError('着用登録は着用解除、貸与は返却を選択してください。')
        if row['returned_date']:raise ValueError('この貸与は返却済みです。')
        returned=day(d.get('returned_date',today),'返却日',True)
        if returned<row['issued_date']:raise ValueError('返却日は貸与日以降にしてください。')
        notes=text(d.get('return_notes',''),'返却メモ',1000);now=datetime.now().isoformat(timespec='seconds')
        c.execute('UPDATE loans SET returned_date=?,return_notes=?,version=version+1,updated_at=? WHERE id=?',(returned,notes,now,row['id']))
        c.execute('UPDATE units SET version=version+1,updated_at=? WHERE id=?',(now,row['unit_id']))
        emit(c,row['customer_id'],'unit',row['unit_id'],'着用解除' if action=='unassign' else '返却',user,dict(loan_id=row['id'],wearer_id=row['wearer_id'],date=returned,notes=notes));return dict(ok=True,id=row['id'])
    raise ValueError('貸与・返却の操作を確認してください。')


def get(path,data,params):
    entity=params.get('entity','summary');cid=params.get('customer_id');allowed=[x['id'] for x in data['customers']]
    if cid:allowed=[scope(data,int(cid))]
    placeholder=','.join('?' for _ in allowed) or 'NULL'
    where='t.customer_id IN ('+placeholder+')';args=list(allowed)
    with connection(path) as c:
        if entity=='summary':
            counts={t:c.execute('SELECT count(*) FROM '+t+' t WHERE '+where,args).fetchone()[0] for t in ['contracts','wearers','units']}
            counts['loaned']=c.execute("SELECT count(*) FROM loans t WHERE "+where+" AND t.returned_date IS NULL AND t.relation_type='貸与'",args).fetchone()[0]
            counts['assigned']=c.execute("SELECT count(*) FROM loans t WHERE "+where+" AND t.returned_date IS NULL AND t.relation_type='着用登録'",args).fetchone()[0]
            counts['return_pending']=c.execute("SELECT count(*) FROM loans t JOIN wearers w ON w.id=t.wearer_id WHERE "+where+" AND t.returned_date IS NULL AND t.relation_type='貸与' AND w.status='退職'",args).fetchone()[0]
            return counts
        table={'contracts':'contracts','wearers':'wearers','units':'units','loans':'loans','events':'events'}.get(entity)
        if not table:raise ValueError('表示する台帳を確認してください。')
        ident=params.get('id');search=str(params.get('q','')).strip()[:200];status=params.get('status','')
        if ident:where+=' AND t.id=?';args.append(integer(int(ident),'対象'))
        if search:
            columns={'contracts':['name','notes'],'wearers':['employee_code','name','kana','department'],'units':['asset_code','barcode','notes'],'loans':[],'events':['action','actor']}[entity]
            if columns:where+=' AND ('+' OR '.join('instr(lower(t.'+col+'),lower(?))>0' for col in columns)+')';args.extend([search]*len(columns))
        if status and entity=='wearers':where+=' AND t.status=?';args.append(status)
        if status and entity=='units':
            if status in ('貸与中','着用登録中'):
                where+=" AND EXISTS(SELECT 1 FROM loans z WHERE z.unit_id=t.id AND z.returned_date IS NULL AND z.relation_type=?)";args.append('貸与' if status=='貸与中' else '着用登録')
            else:
                where+=' AND t.status=?';args.append(status)
                if status=='待機':where+=' AND NOT EXISTS(SELECT 1 FROM loans z WHERE z.unit_id=t.id AND z.returned_date IS NULL)'
        if status and entity=='loans':
            where+=' AND t.returned_date IS '+('NULL' if status in ('貸与中','着用登録中') else 'NOT NULL')
            where+=' AND t.relation_type=?';args.append('着用登録' if status in ('着用登録中','着用解除済み') else '貸与')
        if params.get('wearer_id') and entity=='loans':where+=' AND t.wearer_id=?';args.append(int(params['wearer_id']))
        if params.get('unit_id') and entity=='loans':where+=' AND t.unit_id=?';args.append(int(params['unit_id']))
        if params.get('record_id') and entity=='events':
            if params.get('record_type') not in ('contract','wearer','unit'):raise ValueError('履歴の対象を確認してください。')
            where+=' AND t.entity=? AND t.entity_id=?';args.extend([params['record_type'],int(params['record_id'])])
        total=c.execute('SELECT count(*) FROM '+table+' t WHERE '+where,args).fetchone()[0]
        page=max(1,int(params.get('page',1)));limit=50
        joins='';extra=''
        if entity=='units':joins=' LEFT JOIN loans l ON l.unit_id=t.id AND l.returned_date IS NULL LEFT JOIN wearers w ON w.id=l.wearer_id';extra=',l.id AS loan_id,l.wearer_id,l.relation_type,w.name AS wearer_name'
        if entity=='wearers':extra=',(SELECT count(*) FROM loans l WHERE l.wearer_id=t.id AND l.returned_date IS NULL) AS loan_count'
        if entity=='loans':joins=' JOIN units u ON u.id=t.unit_id JOIN wearers w ON w.id=t.wearer_id JOIN contracts k ON k.id=t.contract_id';extra=',u.asset_code,u.barcode,u.product_id,u.customer_product_id,u.size,w.name AS wearer_name,w.employee_code,k.name AS contract_name'
        rows=[dict(r) for r in c.execute('SELECT t.*'+extra+' FROM '+table+' t'+joins+' WHERE '+where+' ORDER BY t.id DESC LIMIT ? OFFSET ?',(*args,limit,(page-1)*limit))]
        for row in rows:
            if entity=='wearers':
                row['contracts']=[dict(r) for r in c.execute('SELECT k.id,k.name,k.service_type FROM contracts k JOIN wearer_contracts w ON w.contract_id=k.id WHERE w.wearer_id=? ORDER BY k.id',(row['id'],))];row['contract_ids']=[k['id'] for k in row['contracts']]
            if entity=='contracts':row['lines']=[dict(r) for r in c.execute('SELECT * FROM contract_lines WHERE contract_id=? ORDER BY id',(row['id'],))]
            if entity=='loans':row['issued_snapshot']=json.loads(row['issued_snapshot']) if ident else None
            if entity=='events':row['payload']=json.loads(row['payload']) if ident else None
        return dict(rows=rows,total=total,page=page,pageSize=limit)
