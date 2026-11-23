"""Administrator view of customer, organization and contract fields."""
import json
import uuid
from datetime import datetime,timezone
from contextlib import closing
import sqlite3
import web_requests
import web_request_organizations as organizations
import web_request_inheritance as inheritance

FIELD_GROUPS={
'customers':('顧客基本情報',[('id','顧客ID'),('name','顧客名'),('kana','フリガナ'),('office_code','営業所コード'),('address','住所'),('phone','電話番号'),('contact','顧客側の窓口担当者'),('active','利用状態'),('created_at','登録日時'),('updated_at','更新日時'),('stocktake_target','棚卸対象（在庫用）')]),
'nodes':('組織',[('id','組織ID'),('parent_id','所属先の組織ID'),('kind','区分（会社・地区・拠点）'),('name','組織名'),('customer_id','顧客ID'),('site_type','拠点の呼び方')]),
'staff_scopes':('社内担当範囲',[('user_id','社内担当者ID'),('node_id','担当する組織ID')]),
'staff_scope_settings':('担当範囲の設定済み記録',[('user_id','社内担当者ID'),('customer_id','顧客ID')]),
'base':('顧客共通設定',[('customer_id','顧客ID'),('primary_user_id','主担当ID'),('backup_user_id','副担当ID'),('delivery_to','標準納品先（保存済みの旧設定）'),('receive_place','標準受取場所（保存済みの旧設定）'),('customer_note','顧客への案内'),('request_types','受付する依頼の種類')]),
'departments':('部署',[('id','部署ID'),('name','部署名'),('contracts','配下の契約（別表）')]),
'contracts':('契約',[('id','契約ID'),('name','契約名'),('kind','契約形態'),('service_type','取引区分'),('default_ownership','所有区分の初期値'),('start_date','契約開始日'),('end_date','契約終了日'),('notes','契約備考'),('default_qty','制服追加時の初期枚数'),('active','有効・停止'),('uniforms','対象制服（別表）')]),
'uniforms':('契約の対象制服',[('id','制服設定ID'),('customer_product_id','顧客の取扱商品ID'),('product_id','商品ID'),('display_name','顧客向け表示名'),('description','説明'),('default_qty','制服ごとの契約枚数'),('sort_order','表示順'),('photo','写真'),('active','対象・対象外')]),
'scopes':('地区・店舗の共通／個別設定',[('node_id','組織ID'),('customer_id','顧客ID'),('version','更新番号'),('draft','下書き：mode（共通／個別）・patch（差分）'),('publication','反映済み：mode・patch・version'),('status','下書き／反映済み／停止')]),
'pages':('WEB依頼用・顧客向けページ',[('id','ページID'),('name','ページ名'),('customer_id','顧客ID'),('node_ids','表示する組織IDの一覧'),('can_request','依頼可否')]),
'customer_scopes':('WEB依頼用・利用者の表示範囲',[('account_id','顧客側アカウントID'),('page_id','利用ページID'),('node_ids','個別指定の組織ID一覧'),('can_request','依頼可否')]),
}

def require_admin(user):
    if not web_requests.can_open(user) or user['role']!='owner':raise PermissionError('データ管理は主管理者だけが利用できます。')

def state(c,stock_path,user):
    require_admin(user)
    org=organizations.read(c)
    groups={key:dict(key=key,title=title,fields=[dict(key=k,label=v) for k,v in fields],rows=[]) for key,(title,fields) in FIELD_GROUPS.items()}
    with closing(sqlite3.connect(stock_path)) as s:
        s.row_factory=sqlite3.Row
        groups['customers']['rows']=[dict(r) for r in s.execute('SELECT * FROM customers ORDER BY id')]
    for row in groups['customers']['rows']:
        roots={n['id'] for n in org['nodes'] if n['kind']=='company' and n['customer_id']==row['id']}
        row['company_staff_ids']=sorted({x['user_id'] for x in org['staff_scopes'] if x['node_id'] in roots})
        cfg=web_requests.read_config(c,row['id']);row['primary_user_id']=cfg['draft'].get('primary_user_id');row['backup_user_id']=cfg['draft'].get('backup_user_id');row['contract_version']=cfg['version']
    groups['customers']['fields'][3:3]=[dict(key='primary_user_id',label='主担当'),dict(key='backup_user_id',label='副担当'),dict(key='company_staff_ids',label='顧客全体の担当範囲（参照）'),dict(key='contract_version',label='設定更新番号')]
    for key in ['nodes','staff_scopes','staff_scope_settings','customer_scopes']:groups[key]['rows']=org.get(key,[])
    groups['pages']['rows']=org.get('request_pages',[])
    for customer in groups['customers']['rows']:
        cid=customer['id'];base=web_requests.read_config(c,cid)
        groups['base']['rows'].append(dict(customer_id=cid,**{k:v for k,v in base['draft'].items() if k!='departments'}))
        configs=[('',base['draft'])]
        for node in org['nodes']:
            if node['customer_id']==cid and node['kind']!='company':configs.append((node['id'],inheritance.profile(c,base,org,node)['draft']))
        for node_id,cfg in configs:
            context=dict(customer_id=cid,node_id=node_id or '顧客共通')
            for d in cfg['departments']:
                groups['departments']['rows'].append(dict(**context,id=d['id'],name=d['name'],contracts=len(d['contracts'])))
                for contract in d['contracts']:
                    groups['contracts']['rows'].append(dict(**context,department_id=d['id'],**{k:(len(v) if k=='uniforms' else v) for k,v in contract.items()}))
                    for u in contract['uniforms']:
                        groups['uniforms']['rows'].append(dict(**context,department_id=d['id'],contract_id=contract['id'],**{k:('写真登録あり' if v else '') if k=='photo' else v for k,v in u.items()}))
    groups['scopes']['rows']=[dict(r) for r in c.execute('SELECT * FROM web_request_scope_settings ORDER BY customer_id,node_id')]
    for group in groups.values():
        existing={f['key'] for f in group['fields']}
        for key in ['customer_id','node_id','department_id','contract_id']:
            if key not in existing and any(key in r for r in group['rows']):group['fields'].insert(0,dict(key=key,label={'customer_id':'顧客ID','node_id':'設定範囲','department_id':'部署ID','contract_id':'契約ID'}[key]))
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='customer_data_changes'").fetchone():
        groups['history']=dict(key='history',title='表からの変更履歴',fields=[dict(key=k,label=v) for k,v in [('id','変更ID'),('at','保存日時'),('user_id','操作者ID'),('changes','変更前・変更後')]],rows=[dict(r) for r in c.execute('SELECT * FROM customer_data_changes ORDER BY at DESC LIMIT 100')])
    storage=[]
    for name in ['web_request_customer_settings','web_request_scope_settings','web_request_org_settings','web_request_departments','web_request_uniforms']:
        storage.append(dict(table=name,fields=[dict(key=r[1],type=r[2]) for r in c.execute('PRAGMA table_info('+name+')')]))
    return dict(groups=list(groups.values()),storage=storage,orgVersion=org['version'],staff=[dict(r) for r in c.execute("SELECT id,name FROM users WHERE active=1 AND (role IN ('owner','sales') OR manage=1) ORDER BY name")])

EDITABLE=['name','kana','office_code','address','phone','contact','active','primary_user_id','backup_user_id']

def save(c,stock_path,user,body):
    import stock
    require_admin(user)
    changes=body.get('changes')
    if not isinstance(changes,list) or not 1<=len(changes)<=1000:raise ValueError('保存する変更を確認してください。')
    if c.in_transaction:c.commit()
    c.execute('ATTACH DATABASE ? AS customer_stock',(str(stock_path),))
    try:
        c.execute('BEGIN IMMEDIATE')
        org=organizations.read(c)
        if body.get('orgVersion')!=org['version']:raise ValueError('担当範囲が別の画面で更新されています。再読込してください。')
        staff={r[0] for r in c.execute("SELECT id FROM users WHERE active=1 AND (role IN ('owner','sales') OR manage=1)")}
        log=[];seen=set();org_changed=False
        for change in changes:
            if not isinstance(change,dict) or type(change.get('id')) is not int or change['id'] in seen:raise ValueError('顧客IDを確認してください。')
            cid=change['id'];seen.add(cid)
            raw=c.execute('SELECT * FROM customer_stock.customers WHERE id=?',(cid,)).fetchone()
            if not raw:raise ValueError('顧客が見つかりません。')
            before=dict(raw);roots={n['id'] for n in org['nodes'] if n['kind']=='company' and n['customer_id']==cid}
            before['company_staff_ids']=sorted({x['user_id'] for x in org['staff_scopes'] if x['node_id'] in roots})
            base=web_requests.read_config(c,cid);before['primary_user_id']=base['draft'].get('primary_user_id');before['backup_user_id']=base['draft'].get('backup_user_id');before['contract_version']=base['version']
            if change.get('before')!=before:raise ValueError('顧客情報が別の画面で更新されています。再読込してください。')
            after=change.get('after')
            if not isinstance(after,dict) or set(after)!=set(EDITABLE):raise ValueError('編集できる項目を確認してください。')
            values=dict(name=stock.need(after['name'],'顧客名'),kana=stock.optional(after['kana'],'フリガナ',80),office_code=stock.optional(after['office_code'],'営業所コード',40),address=stock.optional(after['address'],'住所',200),phone=stock.optional(after['phone'],'電話番号',40),contact=stock.optional(after['contact'],'顧客窓口',80))
            if type(after['active']) not in (bool,int) or after['active'] not in (0,1):raise ValueError('利用状態を確認してください。')
            if c.execute('SELECT 1 FROM customer_stock.customers WHERE name=? AND id<>?',(values['name'],cid)).fetchone():raise ValueError('同じ名前の顧客がすでに登録されています。')
            values['active']=int(after['active'])
            for key in ['primary_user_id','backup_user_id']:
                if after[key] is not None and (type(after[key]) is not int or after[key] not in staff and after[key]!=before[key]):raise ValueError('主担当・副担当を確認してください。')
            if after['primary_user_id'] and after['primary_user_id']==after['backup_user_id']:raise ValueError('主担当と副担当は別の人を選んでください。')
            old_roles={before[key] for key in ['primary_user_id','backup_user_id'] if before[key] is not None}
            new_roles={after[key] for key in ['primary_user_id','backup_user_id'] if after[key] is not None}
            role_changed=any(before[key]!=after[key] for key in ['primary_user_id','backup_user_id'])
            if any(before[k]!=values[k] for k in values):
                values['updated_at']=stock.stamp()
                c.execute('UPDATE customer_stock.customers SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),cid))
            if role_changed:
                org_changed=True
                if not roots and new_roles:
                    root_id=str(uuid.uuid4());org['nodes'].append(dict(id=root_id,parent_id=None,kind='company',name=values['name'],customer_id=cid,site_type=''));roots={root_id}
                org['staff_scopes']=[x for x in org['staff_scopes'] if not (x['node_id'] in roots and x['user_id'] in old_roles-new_roles)]
                for uid in new_roles:
                    if not any(x['node_id'] in roots and x['user_id']==uid for x in org['staff_scopes']):org['staff_scopes'].append(dict(user_id=uid,node_id=next(iter(roots))))
                settings=org.setdefault('staff_scope_settings',[])
                for uid in old_roles|new_roles:
                    if not any(x['user_id']==uid and x['customer_id']==cid for x in settings):settings.append(dict(user_id=uid,customer_id=cid))
                config=dict(base['draft'],primary_user_id=after['primary_user_id'],backup_user_id=after['backup_user_id'])
                c.execute("INSERT INTO web_request_customer_settings(customer_id,version,draft,publication,status) VALUES(?,?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET version=excluded.version,draft=excluded.draft",(cid,base['version']+1,json.dumps(config,ensure_ascii=False),json.dumps(base['publication'],ensure_ascii=False) if base['publication'] else None,base['status']))
            updated=dict(c.execute('SELECT * FROM customer_stock.customers WHERE id=?',(cid,)).fetchone())
            updated.update(company_staff_ids=sorted({x['user_id'] for x in org['staff_scopes'] if x['node_id'] in roots}),primary_user_id=after['primary_user_id'],backup_user_id=after['backup_user_id'],contract_version=base['version']+int(role_changed))
            log.append(dict(id=cid,before=before,after=updated))
        if org_changed:
            customers={r[0] for r in c.execute('SELECT id FROM customer_stock.customers')};users={r[0] for r in c.execute('SELECT id FROM users')};accounts={r[0] for r in c.execute('SELECT id FROM customer_accounts')}
            payload=organizations.validate(org,customers,users,accounts)
            c.execute('UPDATE web_request_org_settings SET version=version+1,payload=? WHERE id=1',(json.dumps(payload,ensure_ascii=False),))
        c.execute("CREATE TABLE IF NOT EXISTS customer_data_changes(id TEXT PRIMARY KEY,at TEXT NOT NULL,user_id INTEGER NOT NULL,changes TEXT NOT NULL)")
        event=str(uuid.uuid4());c.execute('INSERT INTO customer_data_changes VALUES(?,?,?,?)',(event,datetime.now(timezone.utc).isoformat(),user['id'],json.dumps(log,ensure_ascii=False)))
        c.commit()
        return dict(saved=len(log),history_id=event)
    except:
        c.rollback();raise
    finally:c.execute('DETACH DATABASE customer_stock')

def undo(c,stock_path,user,event):
    require_admin(user)
    org=organizations.read(c)
    row=c.execute('SELECT changes FROM customer_data_changes WHERE id=?',(event,)).fetchone()
    if not row:raise ValueError('変更履歴が見つかりません。')
    changes=[]
    with closing(sqlite3.connect(stock_path)) as s:
        s.row_factory=sqlite3.Row
        for entry in json.loads(row['changes']):
            raw=s.execute('SELECT * FROM customers WHERE id=?',(entry['id'],)).fetchone()
            if not raw:raise ValueError('顧客が見つかりません。')
            current=dict(raw);roots={n['id'] for n in org['nodes'] if n['kind']=='company' and n['customer_id']==entry['id']}
            current['company_staff_ids']=sorted({x['user_id'] for x in org['staff_scopes'] if x['node_id'] in roots})
            cfg=web_requests.read_config(c,entry['id']);current.update(primary_user_id=cfg['draft'].get('primary_user_id'),backup_user_id=cfg['draft'].get('backup_user_id'),contract_version=cfg['version'])
            if current!=entry['after']:raise ValueError('保存後に変更されています。履歴の内容を確認し、必要な項目を編集してください。')
            changes.append(dict(id=entry['id'],before=current,after={key:entry['before'][key] for key in EDITABLE}))
    return save(c,stock_path,user,dict(changes=changes,orgVersion=org['version']))
