"""One customer master for the shared desk and inventory; existing IDs are retained."""
import base64
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import web_requests
import web_request_organizations as organizations

COLUMNS=[('顧客ID','id',False),('顧客名','name',True),('フリガナ','kana',False),('住所','address',False),('電話番号','phone',False),('顧客窓口','contact',False),('顧客コード','officeCode',False),('利用状態','active',False)]

def access(user):
    if not web_requests.can_open(user):raise PermissionError('顧客・契約管理を利用する権限がありません。')

def can_write(user):return user['role']=='owner' or bool(user['manage'])

def directory(c,stock_path,user):
    import stock
    access(user)
    state=web_requests.state(c,stock_path,user)
    allowed={r['id'] for r in state['customers']}
    org=organizations.read(c)
    staff={r['id']:r['name'] for r in state['staff']}
    rental_ids=set()
    rental_path=Path(stock_path).parent/'rental.sqlite3'
    if rental_path.exists():
        with closing(sqlite3.connect(rental_path)) as rental:
            for table, in rental.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                if 'customer_id' in {r[1] for r in rental.execute(f'PRAGMA table_info("{table}")')}:
                    rental_ids.update(r[0] for r in rental.execute(f'SELECT DISTINCT customer_id FROM "{table}"'))
    result=[]
    with closing(stock.connect(stock_path)) as s, s:
        for raw in s.execute('SELECT * FROM customers ORDER BY id'):
            if not can_write(user) and raw['id'] not in allowed:continue
            row=stock.customer_row(s,raw['id']);cid=row['id'];nodes=[n for n in org['nodes'] if n['customer_id']==cid]
            p=web_requests.read_config(c,cid);cfg=p['draft']
            scopes=[x for x in state.get('scopeProfiles',[]) if x['customer_id']==cid and x.get('draft_mode')=='custom']
            contracts=[k for d in cfg['departments'] for k in d['contracts']]
            for scope in scopes:contracts.extend(k for d in scope['draft']['departments'] for k in d['contracts'])
            ids={n['id'] for n in nodes}
            row.update(rentalEnabled=cid in rental_ids,updatedAt=raw['updated_at'],regions=sum(n['kind']=='region' for n in nodes),sites=sum(n['kind']=='site' for n in nodes),contractCount=len(contracts),staffNames=sorted({staff[x['user_id']] for x in org['staff_scopes'] if x['node_id'] in ids and x['user_id'] in staff}|{staff[x] for x in [cfg.get('primary_user_id'),cfg.get('backup_user_id')] if x in staff}),webEnabled=any(x['customer_id']==cid for x in org.get('request_pages',[])),stockEnabled=bool(s.execute('SELECT 1 FROM customer_products WHERE customer_id=? LIMIT 1',(cid,)).fetchone()))
            result.append(row)
    return dict(customers=result,canWrite=can_write(user),isOwner=user['role']=='owner')

def save(stock_path,user,body):
    import stock
    access(user)
    if not can_write(user):raise PermissionError('顧客の登録・編集は主管理者・管理者が行います。')
    with closing(stock.connect(stock_path)) as s, s:
        s.execute('BEGIN IMMEDIATE')
        if body.get('id'):
            old=s.execute('SELECT updated_at FROM customers WHERE id=?',(body['id'],)).fetchone()
            if not old:raise ValueError('顧客が見つかりません。')
            if body.get('updatedAt')!=old['updated_at']:raise ValueError('別の画面で変更されています。一覧を再読込してください。')
        row=stock.save_customer(s,body)
    return row

def excel(stock_path,template=False):
    import stock,excel_book
    with closing(stock.connect(stock_path)) as s, s:
        rows=[] if template else [[r['id'],r['name'],r['kana'],r['address'],r['phone'],r['contact'],r['office_code'],'使用中' if r['active'] else '停止中'] for r in s.execute('SELECT * FROM customers ORDER BY id')]
    return excel_book.workbook([('顧客',[x[0] for x in COLUMNS],rows)])

def import_excel(stock_path,user,body):
    import stock,excel_book
    access(user)
    if not can_write(user):raise PermissionError('Excel取込は主管理者・管理者が行います。')
    try:blob=base64.b64decode(body.get('file',''),validate=True)
    except Exception:raise ValueError('記入用Excelを選択してください。')
    sheets=excel_book.read_sheets(blob)
    if set(sheets)!={'顧客'}:raise ValueError('共通登録の「顧客」シートだけのExcelを使用してください。')
    rows,_=excel_book._table(sheets.get('顧客'),'顧客',COLUMNS)
    if not rows:raise ValueError('登録する顧客がありません。')
    seen=set();created=updated=0
    with closing(stock.connect(stock_path)) as s, s:
        s.execute('BEGIN IMMEDIATE')
        for number,item in rows:
            raw_id=item.get('id','');cid=None
            if raw_id:
                try:cid=int(raw_id)
                except (TypeError,ValueError):raise ValueError(f'{number}行目の顧客IDを確認してください。')
                if cid in seen:raise ValueError('同じ顧客IDが複数行あります。')
                seen.add(cid)
            current=s.execute('SELECT id FROM customers WHERE id=?',(cid,)).fetchone() if cid else s.execute('SELECT id FROM customers WHERE name=?',(item['name'],)).fetchone()
            if cid and not current:raise ValueError(f'{number}行目の顧客IDは登録されていません。新規はIDを空欄にしてください。')
            state=item.get('active','')
            if state not in ('','使用中','停止中'):raise ValueError(f'{number}行目の利用状態は「使用中」「停止中」で指定してください。')
            payload=dict(item);payload['id']=current['id'] if current else None
            payload['active']=state!='停止中' if state else (stock.customer_row(s,current['id'])['active'] if current else True)
            stock.save_customer(s,payload)
            if current:updated+=1
            else:created+=1
    return dict(created=created,updated=updated,summary=f'顧客を新規{created}件・更新{updated}件登録しました。')

def unify(c,stock_path,user,apply=False,expected=None):
    import stock
    access(user)
    if user['role']!='owner':raise PermissionError('表記揺れの統合は主管理者が行います。')
    with closing(stock.connect(stock_path)) as s, s:
        s.execute('BEGIN IMMEDIATE')
        preview=stock.preview_customer_duplicates(s)
        if not apply:return preview
        if expected!=preview['duplicates']:raise ValueError('重複顧客の内容が変わりました。もう一度確認してください。')
        sources={x['id'] for g in preview['duplicates'] for x in g['sources']}
        org=organizations.read(c)
        if any(n['customer_id'] in sources for n in org['nodes']):raise ValueError('組織・契約で使用中の顧客が含まれます。関連設定の整理後に統合してください。')
        for table, in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if 'customer_id' not in {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}:continue
            for cid in sources:
                if c.execute(f'SELECT 1 FROM "{table}" WHERE customer_id=? LIMIT 1',(cid,)).fetchone():raise ValueError('他の業務で使用中の顧客が含まれるため統合できません。')
        rental_path=Path(stock_path).parent/'rental.sqlite3'
        if rental_path.exists():
            with closing(sqlite3.connect(rental_path)) as rental:
                for table, in rental.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    if 'customer_id' not in {r[1] for r in rental.execute(f'PRAGMA table_info("{table}")')}:continue
                    for cid in sources:
                        if rental.execute(f'SELECT 1 FROM "{table}" WHERE customer_id=? LIMIT 1',(cid,)).fetchone():raise ValueError('レンタル管理で使用中の顧客が含まれるため統合できません。')
        from datetime import datetime
        folder=Path(stock_path).parent/'backups';folder.mkdir(exist_ok=True)
        target=folder/('customer-unify-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
        with closing(sqlite3.connect(stock_path)) as source, closing(sqlite3.connect(target)) as backup:source.backup(backup)
        return stock.apply_customer_duplicates_merge(s,{'isOwner':True})
