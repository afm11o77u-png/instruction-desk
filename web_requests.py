"""WEB依頼の部署・採用品設定。在庫DBは読み取り専用。"""
import web_request_inheritance as inheritance
import web_request_organizations as organizations
import web_request_department_registry as department_registry
import web_request_product_display as product_display
import auth
import unicodedata
import json
import copy
import base64
import io
from datetime import datetime
from contextlib import closing
import sqlite3

def schema(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS web_request_departments (
      id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, name TEXT NOT NULL,
      UNIQUE(customer_id,name));
    CREATE TABLE IF NOT EXISTS web_request_uniforms (
      id INTEGER PRIMARY KEY, department_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
      display_name TEXT NOT NULL DEFAULT '', sizes TEXT NOT NULL DEFAULT '[]',
      published INTEGER NOT NULL DEFAULT 0, UNIQUE(department_id,product_id));
    ''')
    c.execute("CREATE TABLE IF NOT EXISTS web_request_customer_settings (customer_id INTEGER PRIMARY KEY, version INTEGER NOT NULL DEFAULT 0, draft TEXT NOT NULL, publication TEXT, status TEXT NOT NULL DEFAULT 'draft')")

    department_registry.schema(c)
    organizations.schema(c)
    product_display.schema(c)
    inheritance.schema(c)

def catalog(path):
    if not path.exists(): return {'customers':[], 'products':[]}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as s:
        s.row_factory=sqlite3.Row
        cols={r[1] for r in s.execute('PRAGMA table_info(customers)')}
        def colsql(name):
            return name if name in cols else "'' AS "+name
        customers=[]
        for r in s.execute('SELECT id,name,kana,'+colsql('customer_code')+','+colsql('postal_code')+','+colsql('address')+','+colsql('phone')+','+colsql('contact')+','+colsql('fax')+','+colsql('email')+','+colsql('memo')+','+colsql('updated_at')+' FROM customers WHERE active=1 ORDER BY name'):
            customers.append(dict(id=r['id'],name=r['name'],kana=r['kana'] or '',address=r['address'] or '',phone=r['phone'] or '',contact=r['contact'] or '',customerCode=r['customer_code'] or '',postalCode=r['postal_code'] or '',fax=r['fax'] or '',email=r['email'] or '',memo=r['memo'] or '',updatedAt=r['updated_at'] or ''))

        manufacturer="manufacturer" if "manufacturer" in {r[1] for r in s.execute("PRAGMA table_info(products)")} else "'' AS manufacturer"
        products=[dict(r) for r in s.execute("SELECT id,code,name,color,"+manufacturer+" FROM products WHERE active=1 AND merged_into_id IS NULL AND category IN ('','制服') ORDER BY name,id")]
        sizes={}
        for r in s.execute('SELECT product_id,label FROM product_sizes ORDER BY sort_order,id'):
            sizes.setdefault(r['product_id'],[]).append(r['label'])
        links={}
        for r in s.execute('SELECT DISTINCT customer_id,product_id FROM customer_products WHERE active=1'):
            links.setdefault(r['product_id'],[]).append(r['customer_id'])
        for p in products:
            p['sizes']=sizes.get(p['id'],[])
            p['customers']=links.get(p['id'],[])
        return {'customers':customers,'products':products}

REQUEST_TYPES = ['新規', 'サイズ交換', '破損交換', '追加・補充', 'その他']

def can_open(u):
    return bool(u and not u['must_change'] and (u['role'] in ('owner','sales') or u['manage']))

def default_config():
    return dict(primary_user_id=None, backup_user_id=None, delivery_to='', receive_place='', customer_note='', request_types=REQUEST_TYPES[:], departments=[])

def read_config(c,cid):
    row=c.execute('SELECT * FROM web_request_customer_settings WHERE customer_id=?',(cid,)).fetchone()
    if row:
        return dict(customer_id=cid,version=row['version'],draft=json.loads(row['draft']),publication=json.loads(row['publication']) if row['publication'] else None,status=row['status'])
    cfg=default_config()
    # Preserve previous department/uniform settings as drafts requiring handling selection.
    for dep in c.execute('SELECT * FROM web_request_departments WHERE customer_id=?',(cid,)):
        uniforms=[dict(r) for r in c.execute('SELECT * FROM web_request_uniforms WHERE department_id=?',(dep['id'],))]
        cfg['departments'].append(dict(id='legacy-'+str(dep['id']),name=dep['name'],contracts=[dict(id='legacy-contract-'+str(dep['id']),name='標準契約',kind='要設定',default_qty=1,active=True,uniforms=[dict(id='legacy-uniform-'+str(x['id']),customer_product_id=None,product_id=x['product_id'],display_name=x['display_name'],description='',default_qty=1,sort_order=0,photo='',active=bool(x['published'])) for x in uniforms])]))
    return dict(customer_id=cid,version=0,draft=cfg,publication=None,status='draft')

def permitted(profile,u):
    if u is None or u['role']=='owner' or u['manage']: return True
    cfg=profile['draft']; assigned=[cfg.get('primary_user_id'),cfg.get('backup_user_id')]
    return not any(assigned) or u['id'] in assigned

def state(c,path,user=None,preview_account_id=None,preview_page_id=None):
    data=catalog(path)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as s:
        s.row_factory=sqlite3.Row
        data['handles']=[dict(r) for r in s.execute('SELECT id,customer_id,product_id,embroidery FROM customer_products WHERE active=1')]
    profiles=[read_config(c,x['id']) for x in data['customers']]
    org=organizations.read(c)
    assigned=organizations.staff_ids(org,user['id']) if user else set()
    data['profiles']=[x for x in profiles if permitted(x,user) or x['customer_id'] in assigned]
    preview=preview_account_id is not None or preview_page_id is not None
    if preview:
        if not organizations.admin(user):raise PermissionError('顧客向け表示の確認は管理者が行ってください。')
        if preview_account_id is not None and preview_page_id is not None:raise ValueError('確認する利用者かページを1つ選んでください。')
        if preview_page_id is not None:
            page=organizations.request_page(org,preview_page_id);scope=organizations.customer_ids(org,page['node_ids']);can_request=page['can_request'];visible=organizations.descendant_ids(org,page['node_ids'])
            data['previewPageId']=page['id'];data['previewPageName']=page['name']
        else:
            scope,can_request=organizations.account_scope(c,org,preview_account_id);visible=organizations.account_node_ids(c,org,preview_account_id);data['previewAccountId']=preview_account_id
        data['profiles']=[x for x in data['profiles'] if x['customer_id'] in scope]
        data['previewCanRequest']=can_request
    allowed={x['customer_id'] for x in data['profiles']}
    data['customers']=[x for x in data['customers'] if x['id'] in allowed]
    data['handles']=[x for x in data['handles'] if x['customer_id'] in allowed]
    pids={x['product_id'] for x in data['handles']}
    data['products']=[x for x in data['products'] if x['id'] in pids]
    data['staff']=[dict(r) for r in c.execute("SELECT id,name FROM users WHERE active=1 AND (role IN ('owner','sales') OR manage=1) ORDER BY name")]
    data['requestTypes']=REQUEST_TYPES
    data['userId']=user['id'] if user else None
    data['isAdmin']=organizations.admin(user)
    user_columns={r[1] for r in c.execute('PRAGMA table_info(users)')}
    login_column='login' if 'login' in user_columns else "'' AS login"
    data['organizationStaff']=[dict(r) for r in c.execute('SELECT id,name,'+login_column+" FROM users WHERE active=1"+(" AND password<>''" if 'password' in user_columns else '')+' ORDER BY name')] if data['isAdmin'] and not preview else data['staff']
    data['organizationStaff']=[u for u in data['organizationStaff'] if not auth.is_name_only(u.get('login'))]
    data['organizations']=organizations.view(c,allowed,user) if not preview else organizations.view(c,allowed,dict(role='sales',manage=0,id=-1))
    if preview:
        keep=set(visible)
        for _ in range(len(org['nodes'])+1):
            keep|={n['parent_id'] for n in org['nodes'] if n['id'] in keep and n['parent_id']}
        data['organizations']['nodes']=[n for n in data['organizations']['nodes'] if n['id'] in keep]
        data['allowedNodeIds']=sorted(visible)
    elif user and not organizations.admin(user):
        visible={n['id'] for n in org['nodes'] if n['customer_id'] in allowed and (permitted(read_config(c,n['customer_id']),user) or organizations.staff_can_manage(org,user['id'],n['customer_id'],n['id']))}
        keep=set(visible)
        for _ in range(len(org['nodes'])+1):keep|={n['parent_id'] for n in org['nodes'] if n['id'] in keep and n['parent_id']}
        data['organizations']['nodes']=[n for n in data['organizations']['nodes'] if n['id'] in keep]
        data['allowedNodeIds']=sorted(visible)
    else:data['allowedNodeIds']=[n['id'] for n in data['organizations']['nodes']]
    data['manageCommonCustomerIds']=[p['customer_id'] for p in data['profiles'] if permitted(p,user) or (user and organizations.staff_can_manage(org,user['id'],p['customer_id']))]

    data['scopeProfiles']=[inheritance.profile(c,p,org,n) for p in data['profiles'] for n in data['organizations']['nodes'] if n['customer_id']==p['customer_id'] and n['kind']!='company' and n['id'] in data['allowedNodeIds']]
    data['personnelTargets']=[dict(node_id=n['id'],customer_id=n['customer_id'],kind=n['kind'],name=n['name']) for n in data['organizations']['nodes'] if organizations.personnel_enabled(data['organizations'],n)]
    data['departmentLibraries']=[department_registry.library(c,p) for p in data['profiles']] if not preview else []
    data['departmentAssignments']=[dict(department_registry.selection(c,next(p for p in data['profiles'] if p['customer_id']==n['customer_id']),org,n),customer_id=n['customer_id']) for n in data['organizations']['nodes'] if n['id'] in data['allowedNodeIds'] and organizations.personnel_enabled(org,n)] if not preview else []
    products={p['id']:p for p in data['products']};handles={};bases={x['customer_id']:x for x in data['profiles']};nodes={n['id']:n for n in org['nodes']}
    for h in data['handles']:handles.setdefault(h['customer_id'],{})[h['id']]=h['product_id']
    libraries={cid for cid in bases if has_library(c,cid)}
    for p in data['profiles']+data['scopeProfiles']:
        if p['customer_id'] in libraries:p['live']=live_config(c,bases[p['customer_id']],org,nodes.get(p.get('node_id')),handles.get(p['customer_id'],{}),products)
    data['productDisplay']=product_display.read_map(c,[p['id'] for p in data['products']])
    data['assignedCustomerIds']=sorted({x['customer_id'] for x in profiles if user and user['id'] in (x['draft'].get('primary_user_id'),x['draft'].get('backup_user_id'))}|assigned)
    return data

def has_library(c,cid):
    return c.execute('SELECT 1 FROM web_request_department_library WHERE customer_id=?',(cid,)).fetchone() is not None

def offerable(cfg,handles,products,today=None):
    """Keep only what the order screen may offer: active, in-date contracts and linked uniforms with sizes."""
    today=today or datetime.now().strftime('%Y-%m-%d');out=copy.deepcopy(cfg)
    for dep in out.get('departments',[]):
        contracts=[]
        for k in dep.get('contracts',[]):
            if not k.get('active') or (k.get('start_date') and k['start_date']>today) or (k.get('end_date') and k['end_date']<today):continue
            k['uniforms']=[u for u in k.get('uniforms',[]) if u.get('active') and u.get('customer_product_id') in handles and products.get(handles[u['customer_product_id']],{}).get('sizes')]
            contracts.append(k)
        dep['contracts']=contracts
    return out

def live_config(c,base,org,node,handles,products):
    """Latest saved department library, filtered by the node's explicit assignments; no publish step."""
    cid=base['customer_id'];nodes=[n for n in org['nodes'] if n['customer_id']==cid]
    lib=department_registry.library(c,base)['draft']
    if node is None:node=next((n for n in nodes if n['kind']=='company'),None)
    cfg,_=inheritance.resolve(c,base,org,node['id']) if node and node['kind']!='company' else (copy.deepcopy(base['draft']),None)
    _,public_status=inheritance.resolve(c,base,org,node['id'],True) if node and node['kind']!='company' else (None,base['status'])
    if not node:departments=lib['departments']
    elif organizations.personnel_enabled(org,node):
        chosen=set(department_registry.selection(c,base,org,node)['department_ids']);departments=[d for d in lib['departments'] if d['id'] in chosen]
    else:departments=[]
    cfg['departments']=inheritance.annotate(dict(departments=departments),'部署登録')['departments']
    status='paused' if base['status']=='paused' or public_status=='paused' else 'published'
    return dict(config=offerable(cfg,handles,products),status=status)

def text(value,label,limit=100,required=False):
    if not isinstance(value,str): raise ValueError(label+'を確認してください。')
    value=value.strip()
    if len(value)>limit or (required and not value): raise ValueError(label+'を確認してください。')
    return value

def number(value,label,low=1,high=99):
    if type(value) is not int or not low<=value<=high: raise ValueError(label+'を確認してください。')
    return value

def normalize_photo(value):
    if not value:return ''
    if not isinstance(value,str) or len(value)>2400000:raise ValueError('写真の容量が大きすぎます。')
    try:
        from PIL import Image, ImageOps
        head,encoded=value.split(',',1)
        if head not in ('data:image/jpeg;base64','data:image/png;base64','data:image/webp;base64'):raise ValueError()
        with Image.open(io.BytesIO(base64.b64decode(encoded,validate=True))) as img:
            if img.width*img.height>16000000 or getattr(img,'n_frames',1)!=1:raise ValueError()
            img=ImageOps.exif_transpose(img);img.thumbnail((1000,1000))
            canvas=Image.new('RGB',img.size,'white')
            if 'A' in img.getbands():canvas.paste(img,mask=img.getchannel('A'))
            else:canvas.paste(img.convert('RGB'))
            out=io.BytesIO();canvas.save(out,format='JPEG',quality=82)
        return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode('ascii')
    except Exception as e:raise ValueError('写真はJPEG・PNG・WebPの静止画像を選んでください。') from e

def validate_config(cfg,data,cid):
    if not isinstance(cfg,dict):raise ValueError('設定を確認してください。')
    out=default_config()
    staff_ids={x['id'] for x in data['staff']}
    for k in ('primary_user_id','backup_user_id'):
        v=cfg.get(k)
        if v is not None and (type(v) is not int or v not in staff_ids):raise ValueError('担当者を確認してください。')
        out[k]=v
    if out['primary_user_id'] and out['primary_user_id']==out['backup_user_id']:raise ValueError('主担当と副担当は別の人を選んでください。')
    for k in ('delivery_to','receive_place','customer_note'):out[k]=text(cfg.get(k,''),'納品先・案内文',1000)
    types=cfg.get('request_types')
    if not isinstance(types,list) or not types or any(x not in REQUEST_TYPES for x in types):raise ValueError('受付する依頼を選んでください。')
    out['request_types']=list(dict.fromkeys(types))
    handles={x['id']:x for x in data['handles'] if x['customer_id']==cid}
    products={x['id']:x for x in data['products']}
    ids=set()
    def ident(x):
        v=text(x.get('id',''),'識別情報',80,True)
        if v in ids:raise ValueError('識別情報が重複しています。')
        ids.add(v);return v
    deps=cfg.get('departments',[])
    if not isinstance(deps,list) or len(deps)>100:raise ValueError('部署数を確認してください。')
    department_names=set()
    for d in deps:
        if not isinstance(d,dict):raise ValueError('部署の内容を確認してください。')
        dep=dict(id=ident(d),name=text(d.get('name',''),'部署名',80,True),contracts=[])
        if d.get('no_department') is True:dep['no_department']=True
        name_key=' '.join(unicodedata.normalize('NFKC',dep['name']).lower().split())
        if name_key in department_names:raise ValueError('同じ名前の部署が重複しています。部署名を確認してください。')
        department_names.add(name_key)
        contracts=d.get('contracts',[])
        if not isinstance(contracts,list) or len(contracts)>100:raise ValueError('契約数を確認してください。')
        for k in contracts:
            if not isinstance(k,dict):raise ValueError('契約の内容を確認してください。')
            contract=dict(id=ident(k),name=text(k.get('name',''),'契約名',100,True),kind=text(k.get('kind',''),'契約形態',100,True),default_qty=None if k.get('service_type') in ('CL','クリーニングのみ') else number(k.get('default_qty'),'標準枚数',1,999),active=bool(k.get('active')),uniforms=[])
            from datetime import date
            service=k.get('service_type','レンタル');ownership=k.get('default_ownership','顧客所有' if service=='クリーニングのみ' else '自社所有')
            if service not in ('レンタル','リース','CL','クリーニングのみ') or ownership not in ('自社所有','顧客所有','着用者所有','その他','未確認'):raise ValueError('取引区分・所有区分を確認してください。')
            if service in ('レンタル','リース','CL'):ownership='顧客所有' if service=='CL' else '自社所有'
            start=text(k.get('start_date',''),'契約開始日',10);end=text(k.get('end_date',''),'契約終了日',10)
            for value in (start,end):
                if value:
                    try:
                        if date.fromisoformat(value).isoformat()!=value:raise ValueError()
                    except ValueError:raise ValueError('契約期間を正しい日付で入力してください。')
            if start and end and end<start:raise ValueError('契約終了日は開始日以降にしてください。')
            contract.update(service_type=service,default_ownership=ownership,start_date=start,end_date=end,notes=text(k.get('notes',''),'契約備考',2000))
            uniforms=k.get('uniforms',[])
            if not isinstance(uniforms,list) or len(uniforms)>200:raise ValueError('制服数を確認してください。')
            seen=set()
            for u in uniforms:
                if not isinstance(u,dict):raise ValueError('制服の内容を確認してください。')
                hid=u.get('customer_product_id')
                if hid is not None:number(hid,'在庫の制服',1,2147483647)
                h=handles.get(hid);p=products.get(h['product_id']) if h else None
                # Legacy unresolved links can remain drafts, but cannot be published.
                legacy=hid is None and str(u.get('id','')).startswith('legacy-')
                if not legacy and (not h or not p):raise ValueError('この顧客の在庫に登録された制服を選んでください。')
                if hid in seen and hid is not None:raise ValueError('同じ契約に同じ制服が重複しています。')
                seen.add(hid)
                hem=bool(u.get('hem_processing'));days=number(u.get('processing_days',0),'加工日数',0,365);days=days if hem else 0
                if hem and days<1:raise ValueError('裾上げ（他）の加工日数を1日以上で入力してください。')
                contract['uniforms'].append(dict(id=ident(u),customer_product_id=hid,product_id=h['product_id'] if h else u.get('product_id'),display_name=text(u.get('display_name',''),'表示名',120),description=text(u.get('description',''),'説明',1000),default_qty=None if service in ('CL','クリーニングのみ') else number(u.get('default_qty'),'契約枚数',1,999),sort_order=number(u.get('sort_order',0),'表示順',0,9999),photo=normalize_photo(u.get('photo','')),active=bool(u.get('active')),hem_processing=hem,processing_days=days))
            dep['contracts'].append(contract)
        out['departments'].append(dep)
    return out

def save(c,path,d,user=None):
    if d.get('action')=='organization_site_import_preview':
        import department_excel
        if not user or not organizations.admin(user):raise PermissionError('拠点の一括取り込みは主管理者・管理者が行ってください。')
        cid=number(d.get('customer_id'),'顧客',1,2147483647)
        customer=next((x for x in state(c,path,user)['customers'] if x['id']==cid),None)
        if not customer:raise ValueError('登録先の会社を選択してください。')
        o=organizations.read(c);root=next((n for n in o['nodes'] if n['kind']=='company' and n['customer_id']==cid),None)
        sites=[n for n in o['nodes'] if n['kind']=='site' and n['customer_id']==cid] if root else []
        return department_excel.preview_sites(d.get('file'),root['name'] if root else customer['name'],sites)
    if d.get('action') in ('load_site_details','save_site_details'):
        import web_request_site_details as site_details
        return site_details.handle(c,path,d,user)
    if d.get('action') in ('department_assignment_import_preview','department_assignment_import'):
        import department_excel_assignment as importer
        cid=number(d.get('customer_id'),'顧客',1,2147483647);data=state(c,path,user)
        if cid not in data['manageCommonCustomerIds']:raise PermissionError('この会社への取り込み権限がありません。')
        base=read_config(c,cid)
        if d['action']=='department_assignment_import_preview':
            org=organizations.read(c)
            nodes=[n for n in org['nodes'] if n['customer_id']==cid and n['id'] in data['allowedNodeIds'] and organizations.personnel_enabled(org,n)]
            return importer.preview(dict(d,_assignments=data['departmentAssignments']),department_registry.library(c,base),org,nodes)
        result=importer.apply(c,d,data,base,user,validate_config,text)
        return dict(state(c,path,user),departmentImportResult=result)
    if d.get('action') in ('collection_days_import_preview','collection_days_import'):
        import web_request_collection_import as collection_import
        if not user or not organizations.admin(user):raise PermissionError('集配曜日の取り込みは主管理者・管理者が行ってください。')
        result=collection_import.handle(c,d,state(c,path,user),user)
        return dict(state(c,path,user),collectionImport=result) if d['action']=='collection_days_import' else dict(collectionImport=result)
    if d.get('action')=='organization':
        data=state(c,path,user)
        organizations.save(c,d.get('organization'),d.get('version'),{x['id'] for x in data['customers']},{x['id'] for x in data.get('organizationStaff',data['staff'])},user,backup=d.get('backup') is True)
        return state(c,path,user)
    if d.get('action') in ('department_import_preview','department_import'):
        import department_excel
        import uuid
        cid=number(d.get('customer_id'),'顧客',1,2147483647);data=state(c,path,user)
        if cid not in {x['id'] for x in data['customers']} or cid not in data['manageCommonCustomerIds']:raise PermissionError('この会社の部署を取り込む権限がありません。')
        current=department_registry.library(c,read_config(c,cid))
        if d['action']=='department_import_preview':
            return dict(department_excel.preview(d.get('file'),current['draft']['departments'],d.get('source','customer')),customer_id=cid,version=current['version'])
        if type(d.get('version')) is not int or d['version']!=current['version']:raise ValueError('部署一覧が更新されています。Excelを選び直してください。')
        names=d.get('names')
        if not isinstance(names,list) or not names or len(names)>500:raise ValueError('取り込む部署を1件以上選んでください。')
        cfg=current['draft'];known={department_excel.normalized(x['name']).casefold() for x in cfg['departments']};added=skipped=0
        for value in names:
            name=text(value,'部署名',80,True);name=department_excel.normalized(name);key=name.casefold()
            if key in known:skipped+=1;continue
            cfg['departments'].append(dict(id=str(uuid.uuid4()),name=name,contracts=[]));known.add(key);added+=1
        if not added:return dict(data,departmentImportResult=dict(added=0,skipped=skipped))
        result=save(c,path,dict(action='department_library',customer_id=cid,version=current['version'],config=cfg),user)
        result['departmentImportResult']=dict(added=added,skipped=skipped)
        return result
    if d.get('action') in ('department_library','assign_department'):
        cid=number(d.get('customer_id'),'顧客',1,2147483647);data=state(c,path,user)
        if cid not in {x['id'] for x in data['customers']}:raise PermissionError('この会社の設定を変更できません。')
        base=read_config(c,cid);org=organizations.read(c)
        if d['action']=='department_library':
            if not permitted(base,user) and cid not in data['manageCommonCustomerIds']:raise PermissionError('会社の部署を編集する権限がありません。')
            cfg=validate_config(d.get('config'),data,cid)
            c.execute('BEGIN IMMEDIATE')
            try:
                current=department_registry.library(c,base)
                if type(d.get('version')) is not int or current['version']!=d['version']:raise ValueError('部署一覧が更新されています。再読込してください。')
                c.execute('INSERT INTO web_request_department_library VALUES(?,?,?) ON CONFLICT(customer_id) DO UPDATE SET version=excluded.version,config=excluded.config',(cid,current['version']+1,json.dumps(cfg,ensure_ascii=False)))
                c.commit()
            except Exception:c.rollback();raise
        else:
            operation=d.get('operation');targets=d.get('targets');dep_ids=d.get('department_ids',[d.get('department_id')])
            if operation not in ('add','remove') or not isinstance(targets,list) or not targets or len(targets)>2000:raise ValueError('対象と操作を確認してください。')
            lib=department_registry.library(c,base)
            if not isinstance(dep_ids,list) or not dep_ids or any(not isinstance(x,str) for x in dep_ids) or len(set(dep_ids))!=len(dep_ids) or not set(dep_ids)<={x['id'] for x in lib['draft']['departments']}:raise ValueError('登録済みの部署を選んでください。')
            c.execute('BEGIN IMMEDIATE')
            try:
                if lib['version']!=d.get('library_version'):raise ValueError('部署一覧が更新されています。再読込してください。')
                seen=set()
                for target in targets:
                    nid=target.get('node_id');node=next((n for n in org['nodes'] if n['id']==nid and n['customer_id']==cid),None)
                    if nid in seen or not node or nid not in data['allowedNodeIds'] or not organizations.personnel_enabled(org,node):raise PermissionError('この登録先には割り当てできません。')
                    seen.add(nid);current=department_registry.selection(c,base,org,node)
                    if current['version']!=target.get('version'):raise ValueError('割り当てが更新されています。再読込してください。')
                    ids=current['department_ids'];ids=[x for x in ids if x not in dep_ids]
                    if operation=='add':ids.extend(dep_ids)
                    c.execute('INSERT INTO web_request_department_targets VALUES(?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET version=excluded.version,department_ids=excluded.department_ids',(nid,cid,current['version']+1,json.dumps(ids)))
                c.commit()
            except Exception:c.rollback();raise
        return state(c,path,user)
    if d.get('action')=='company_personnel':
        if not organizations.admin(user):raise PermissionError('管理者が設定してください。')
        cid=number(d.get('customer_id'),'顧客',1,2147483647)
        if type(d.get('enabled')) is not bool:raise ValueError('会社の人員登録設定を確認してください。')
        data=state(c,path,user);payload=data['organizations'];root=next((n for n in payload['nodes'] if n['kind']=='company' and n['customer_id']==cid),None)
        if not root:raise ValueError('会社を組織構成で登録してください。')
        root['personnel_registration']=d['enabled']
        organizations.save(c,payload,d.get('version'),{x['id'] for x in data['customers']},{x['id'] for x in data['organizationStaff']},user)
        return state(c,path,user)
    if d.get('action')=='save_many':return save_many(c,path,d,user)
    if d.get('node_id'):return save_scope(c,path,d,user)
    cid=number(d.get('customer_id'),'顧客',1,2147483647)
    data=state(c,path,user)
    if cid not in {x['id'] for x in data['customers']}:raise PermissionError('この顧客の設定を変更する権限がありません。')
    c.execute('BEGIN IMMEDIATE')
    try:
        current=read_config(c,cid)
        if not permitted(current,user) and (not user or not organizations.staff_can_manage(organizations.read(c),user['id'],cid)):raise PermissionError('担当者の設定を確認してください。')
        if type(d.get('version')) is not int or d['version']!=current['version']:raise ValueError('別の画面で更新されています。一覧を更新してからやり直してください。')
        action=d.get('action')
        if action not in ('save','publish','pause'):raise ValueError('操作を確認してください。')
        cfg=validate_config(d.get('config'),data,cid)
        if not data['isAdmin']:
            if not cfg['primary_user_id'] and not cfg['backup_user_id']:cfg['primary_user_id']=user['id']
            if user['id'] not in (cfg['primary_user_id'],cfg['backup_user_id']) and cid not in organizations.staff_ids(organizations.read(c),user['id']):raise ValueError('自分を主担当か副担当に設定してください。')
        publication=current['publication'];status=current['status']
        if action=='publish':
            active=[u for dep in cfg['departments'] for k in dep['contracts'] if k['active'] for u in k['uniforms'] if u['active']]
            pmap={x['id']:x for x in data['products']}
            if not active:raise ValueError('表示する契約と制服を１つ以上登録してください。')
            if any(not u['customer_product_id'] or not pmap.get(u['product_id'],{}).get('sizes') for u in active):raise ValueError('未紐づけの制服やサイズ未登録の制服を確認してください。')
            publication=dict(config=cfg,published_at=datetime.now().isoformat(timespec='seconds'),version=current['version']+1);status='published'
        elif action=='pause':status='paused'
        c.execute("INSERT INTO web_request_customer_settings(customer_id,version,draft,publication,status) VALUES(?,?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET version=excluded.version,draft=excluded.draft,publication=excluded.publication,status=excluded.status",(cid,current['version']+1,json.dumps(cfg,ensure_ascii=False),json.dumps(publication,ensure_ascii=False) if publication else None,status))
        c.commit()
    except Exception:
        c.rollback();raise
    return state(c,path,user)


def save_scope(c,path,d,user):
    cid=number(d.get('customer_id'),'顧客',1,2147483647)
    data=state(c,path,user);node_id=text(d.get('node_id'),'地区・拠点',80,True)
    if cid not in {x['id'] for x in data['customers']} or node_id not in data['allowedNodeIds']:raise PermissionError('この地区・拠点の設定を変更できません。')
    org=organizations.read(c);inheritance.chain(org,cid,node_id)
    c.execute('BEGIN IMMEDIATE')
    try:
        base=read_config(c,cid);old=inheritance.record(c,node_id)
        if type(d.get('version')) is not int or d['version']!=old['version']:raise ValueError('設定が更新されています。再読込してください。')
        parent,_=inheritance.resolve(c,base,org,node_id,skip_last=True)
        clean_parent=validate_config(parent,data,cid)
        if validate_config(d.get('parent_config'),data,cid)!=clean_parent:raise ValueError('共通設定または地区設定が更新されています。再読込して確認してください。')
        mode=d.get('inheritance_mode')
        if mode not in ('inherit','custom'):raise ValueError('共通設定を使うか独自設定かを選んでください。')
        config=validate_config(d.get('config'),data,cid)
        payload=dict(mode=mode,patch=inheritance.difference(clean_parent,config) if mode=='custom' else {})
        action=d.get('action');publication=old['publication'];status=old['status']
        if action not in ('save','publish','pause'):raise ValueError('操作を確認してください。')
        if action=='publish':
            if base['status']!='published':raise ValueError('先に会社全体の共通設定を「公開・更新」してください。')
            parent_public,parent_status=inheritance.resolve(c,base,org,node_id,True,True)
            effective=inheritance.apply(parent_public,payload['patch'],'独自設定') if mode=='custom' else parent_public
            checked=validate_config(effective,data,cid)
            active=[u for dep in checked['departments'] for k in dep['contracts'] if k['active'] for u in k['uniforms'] if u['active']]
            if parent_status!='published' or not active:raise ValueError('親の設定が公開中で、選択できる制服があることを確認してください。')
            products={p['id']:p for p in data['products']}
            if any(not u['customer_product_id'] or not products.get(u['product_id'],{}).get('sizes') for u in active):raise ValueError('制服の紐づけとサイズを確認してください。')
            publication=dict(payload,version=old['version']+1);status='published'
        elif action=='pause':status='paused'
        c.execute('INSERT INTO web_request_scope_settings(node_id,customer_id,version,draft,publication,status) VALUES(?,?,?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET version=excluded.version,draft=excluded.draft,publication=excluded.publication,status=excluded.status',(node_id,cid,old['version']+1,json.dumps(payload,ensure_ascii=False),json.dumps(publication,ensure_ascii=False) if publication else None,status))
        c.commit()
    except Exception:c.rollback();raise
    return state(c,path,user)


def save_many(c,path,d,user):
    """Copy one node's effective departments/contracts/uniforms to many sites in one transaction.

    Each target keeps its own non-department settings. The result is stored as a patch against the
    target's own parent, so a target whose result equals its parent becomes 'inherit'.
    """
    cid=number(d.get('customer_id'),'顧客',1,2147483647)
    data=state(c,path,user)
    if cid not in {x['id'] for x in data['customers']}:raise PermissionError('この顧客の設定を変更する権限がありません。')
    targets=d.get('targets')
    if not isinstance(targets,list) or not targets or len(targets)>500:raise ValueError('設定する拠点を選んでください。')
    allowed=set(data['allowedNodeIds']);source_id=d.get('source_node_id') or None
    if source_id is not None and source_id not in allowed:raise PermissionError('呼び出す設定を確認できません。')
    org=organizations.read(c);byid={n['id']:n for n in org['nodes']}
    if source_id is not None:inheritance.chain(org,cid,source_id)
    c.execute('BEGIN IMMEDIATE')
    try:
        base=read_config(c,cid)
        source=inheritance.resolve(c,base,org,source_id)[0] if source_id else base['draft']
        departments=validate_config(source,data,cid)['departments']
        expected=validate_config(dict(default_config(),departments=d.get('expected_departments')),data,cid)['departments']
        if expected!=departments:raise ValueError('呼び出した設定が別の画面で更新されています。再読込してからやり直してください。')
        selected=d.get('department_ids')
        if selected is not None:
            if not isinstance(selected,list) or any(not isinstance(x,str) for x in selected) or len(selected)!=len(set(selected)) or not set(selected)<={x['id'] for x in departments}:raise ValueError('割り当てる部署を選び直してください。')
            departments=[x for x in departments if x['id'] in selected]
        seen=set();conflicts=[];rows=[]
        for t in targets:
            if not isinstance(t,dict):raise ValueError('設定する拠点を確認してください。')
            node_id=text(t.get('node_id'),'拠点',80,True);node=byid.get(node_id)
            if node_id in seen:raise ValueError('同じ拠点が重複しています。')
            seen.add(node_id)
            if node_id not in allowed or not node or node['customer_id']!=cid:raise PermissionError('この拠点の設定を変更できません。')
            if node['kind']!='site':raise ValueError('まとめて設定できるのは店舗・拠点だけです。')
            inheritance.chain(org,cid,node_id)
            old=inheritance.record(c,node_id)
            if type(t.get('version')) is not int or t['version']!=old['version']:conflicts.append(node['name']);continue
            current,_=inheritance.resolve(c,base,org,node_id)
            parent,_=inheritance.resolve(c,base,org,node_id,skip_last=True)
            clean_parent=validate_config(parent,data,cid)
            config=validate_config(dict(current,departments=departments),data,cid)
            patch=inheritance.difference(clean_parent,config)
            rows.append((node_id,old,dict(mode='custom' if patch else 'inherit',patch=patch)))
        if conflicts:raise ValueError('次の拠点は別の画面で更新されています。再読込してからやり直してください：'+'、'.join(conflicts))
        for node_id,old,payload in rows:
            c.execute('INSERT INTO web_request_scope_settings(node_id,customer_id,version,draft,publication,status) VALUES(?,?,?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET version=excluded.version,draft=excluded.draft',(node_id,cid,old['version']+1,json.dumps(payload,ensure_ascii=False),json.dumps(old['publication'],ensure_ascii=False) if old['publication'] else None,old['status']))
        c.commit()
    except Exception:c.rollback();raise
    return state(c,path,user)
