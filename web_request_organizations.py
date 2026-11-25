"""Company/region/site directory and explicit customer/staff scopes.
All settings live in the instruction database. Inventory customer IDs stay unchanged.
"""
import json
import unicodedata
import uuid


DELIVERY_METHODS = ('', '自社集配', '福山便', '佐川便', 'ヤマト便')
COLLECTION_DAYS = ('月', '火', '水', '木', '金', '土')
NODE_TEXT_FIELDS = (
    ('kana', 80),
    ('office_code', 40),
    ('address', 200),
    ('phone', 40),
    ('contact', 80),
    ('postal_code', 20),
    ('fax', 40),
    ('email', 120),
    ('memo', 2000),
    ('official_name', 100),
)


def schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS web_request_org_settings (
      id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL DEFAULT 0,
      payload TEXT NOT NULL DEFAULT '{"nodes":[],"customer_scopes":[],"staff_scopes":[]}');
    INSERT OR IGNORE INTO web_request_org_settings(id) VALUES(1);
    """)
    migrate_direct_sites(c)
    migrate_page_scopes(c)


def migrate_page_scopes(c):
    """顧客向けページの割り当てを、同じ範囲・依頼可否の直接指定へ移す。ページ自体は消さない（何度実行しても同じ結果）。"""
    row=c.execute('SELECT version,payload FROM web_request_org_settings WHERE id=1').fetchone()
    payload=json.loads(row[1]);pages={p.get('id'):p for p in payload.get('request_pages',[]) if isinstance(p,dict)}
    changed=False;scopes=[]
    for scope in payload.get('customer_scopes',[]):
        page=pages.get(scope.get('page_id')) if isinstance(scope,dict) and scope.get('page_id') else None
        if page is None:
            scopes.append(scope);continue
        scopes.append(dict(account_id=scope['account_id'],node_ids=list(dict.fromkeys(page.get('node_ids',[]))),can_request=bool(page.get('can_request',True)),migrated_from_page_id=page['id']))
        changed=True
    if changed:
        payload['customer_scopes']=scopes
        with c:
            c.execute('CREATE TABLE IF NOT EXISTS web_request_org_migrations(id INTEGER PRIMARY KEY,at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,previous_version INTEGER NOT NULL,previous_payload TEXT NOT NULL)')
            c.execute('INSERT INTO web_request_org_migrations(previous_version,previous_payload) VALUES(?,?)',(row[0],row[1]))
            c.execute('UPDATE web_request_org_settings SET payload=?,version=version+1 WHERE id=1',(json.dumps(payload,ensure_ascii=False),))


def migrate_direct_sites(c):
    """Keep existing site IDs/settings; add one neutral parent category per company."""
    row=c.execute('SELECT version,payload FROM web_request_org_settings WHERE id=1').fetchone()
    payload=json.loads(row[1]);nodes=payload.get('nodes',[])
    roots=[n for n in nodes if n.get('kind')=='company']
    changed=False
    for root in roots:
        sites=[n for n in nodes if n.get('kind')=='site' and n.get('parent_id')==root['id']]
        if not sites:continue
        names={n['name'] for n in nodes if n.get('parent_id')==root['id']}
        name='直属';number=1
        while name in names:
            number+=1;name='直属（移行'+str(number)+'）'
        category_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'instruction-desk:category:'+root['id']))
        if any(n['id']==category_id for n in nodes):category_id=str(uuid.uuid4())
        nodes.append(dict(id=category_id,parent_id=root['id'],kind='region',name=name,customer_id=root['customer_id'],site_type=''))
        for site in sites:site['parent_id']=category_id
        changed=True
    if changed:
        with c:
            c.execute('CREATE TABLE IF NOT EXISTS web_request_org_migrations(id INTEGER PRIMARY KEY,at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,previous_version INTEGER NOT NULL,previous_payload TEXT NOT NULL)')
            c.execute('INSERT INTO web_request_org_migrations(previous_version,previous_payload) VALUES(?,?)',(row[0],row[1]))
            c.execute('UPDATE web_request_org_settings SET payload=?,version=version+1 WHERE id=1',(json.dumps(payload,ensure_ascii=False),))


def admin(user):
    return user is None or user['role']=='owner' or bool(user['manage'])


def read(c):
    row=c.execute('SELECT version,payload FROM web_request_org_settings WHERE id=1').fetchone()
    payload=json.loads(row['payload']);payload.setdefault('request_pages',[])
    return dict(version=row['version'],**payload)


def descendant_ids(org,node_ids):
    selected=set(node_ids)
    for _ in range(len(org['nodes'])+1):
        children={n['id'] for n in org['nodes'] if n['parent_id'] in selected}
        if children<=selected:break
        selected|=children
    return selected


def customer_ids(org,node_ids):
    selected=descendant_ids(org,node_ids)
    return {n['customer_id'] for n in org['nodes'] if n['id'] in selected and n['customer_id'] is not None}


def request_page(org,page_id):
    page=next((p for p in org.get('request_pages',[]) if p['id']==page_id),None)
    if not page:raise ValueError('顧客向けページを選んでください。')
    return page


def scope_nodes(org,scope):
    return request_page(org,scope['page_id'])['node_ids'] if scope.get('page_id') else scope['node_ids']


def account_node_ids(c,org,account_id):
    scope=next((s for s in org['customer_scopes'] if s['account_id']==account_id),None)
    if scope is None:
        account=c.execute('SELECT customer_id FROM customer_accounts WHERE id=?',(account_id,)).fetchone()
        return {n['id'] for n in org['nodes'] if n['customer_id']==account['customer_id']}
    return descendant_ids(org,scope_nodes(org,scope))


def staff_ids(org,uid):
    return customer_ids(org,[s['node_id'] for s in org['staff_scopes'] if s['user_id']==uid])


def staff_can_manage(org,uid,cid,node_id=None):
    roots=[s['node_id'] for s in org['staff_scopes'] if s['user_id']==uid]
    if node_id:return node_id in descendant_ids(org,roots)
    return any(n['id'] in roots and n['kind']=='company' and n['customer_id']==cid for n in org['nodes'])


def account_scope(c,org,account_id):
    account=c.execute('SELECT id,customer_id,active FROM customer_accounts WHERE id=?',(account_id,)).fetchone()
    if not account or not account['active']:raise ValueError('有効な顧客IDを選んでください。')
    scope=next((s for s in org['customer_scopes'] if s['account_id']==account_id),None)
    if scope is None:return {account['customer_id']},True
    if scope.get('page_id'):
        page=request_page(org,scope['page_id']);return customer_ids(org,page['node_ids']),page['can_request']
    return customer_ids(org,scope['node_ids']),scope['can_request']


def view(c,allowed,user):
    org=read(c)
    if admin(user):
        org['accounts']=[dict(r) for r in c.execute('SELECT id,name,email,customer_id,active FROM customer_accounts ORDER BY name,id')]
        return org
    # Staff may only see the paths to inventory customers they can access.
    keep={n['id'] for n in org['nodes'] if n['kind']=='site' and n['customer_id'] in allowed}
    for _ in range(len(org['nodes'])+1):
        parents={n['parent_id'] for n in org['nodes'] if n['id'] in keep and n['parent_id']}
        if parents<=keep:break
        keep|=parents
    return dict(version=org['version'],nodes=[n for n in org['nodes'] if n['id'] in keep],customer_scopes=[],staff_scopes=[s for s in org['staff_scopes'] if s['user_id']==user['id']],accounts=[],request_pages=[])


def personnel_enabled(org,node):
    if node['kind']=='site':return True
    if node['kind']!='company':return False
    return node.get('personnel_registration',not any(n['kind']=='site' and n['customer_id']==node['customer_id'] for n in org['nodes']))

def validate(payload,customers,staff,accounts):
    if not isinstance(payload,dict):raise ValueError('組織の入力を確認してください。')
    nodes=payload.get('nodes',[])
    if not isinstance(nodes,list) or len(nodes)>3000:raise ValueError('組織の登録数を確認してください。')
    result=[];ids=set();used_customers=set();names=set()
    for node in nodes:
        if not isinstance(node,dict):raise ValueError('組織の内容を確認してください。')
        nid=node.get('id');parent=node.get('parent_id');kind=node.get('kind');name=node.get('name');cid=node.get('customer_id');site_type=node.get('site_type','事業所')
        if not isinstance(nid,str) or not nid or len(nid)>80 or nid in ids:raise ValueError('組織の識別情報を確認してください。')
        if parent is not None and (not isinstance(parent,str) or len(parent)>80):raise ValueError('所属先を確認してください。')
        if kind not in ('company','region','site') or not isinstance(name,str) or not name.strip() or len(name)>100:raise ValueError('企業・カテゴリ・拠点の名前を確認してください。')
        key=(parent,' '.join(unicodedata.normalize('NFKC',name).lower().split()))
        if key in names:raise ValueError('同じ所属先に同名の組織が重複しています。')
        names.add(key);ids.add(nid)
        if kind=='company':
            if type(cid) is not int or cid not in customers:raise ValueError('最初に在庫管理の顧客を選択してください。')
            if cid in used_customers:raise ValueError('同じ顧客の組織が重複しています。')
            used_customers.add(cid);site_type=''
        elif kind=='site':
            if site_type not in ('事業所','店舗','支店','その他'):raise ValueError('拠点の種類を確認してください。')
        else:site_type=''
        result.append(dict(id=nid,parent_id=parent,kind=kind,name=name.strip(),customer_id=cid,site_type=site_type))
        if kind=='company' and 'personnel_registration' in node:
            if type(node['personnel_registration']) is not bool:raise ValueError('会社の人員登録設定を確認してください。')
            result[-1]['personnel_registration']=node['personnel_registration']
        for field,limit in NODE_TEXT_FIELDS:
            if field not in node:continue
            value=node[field]
            if not isinstance(value,str) or len(value)>limit:raise ValueError('組織の所在地・連絡先を確認してください。')
            result[-1][field]=value.strip()
        if 'delivery_method' in node:
            method=node['delivery_method']
            if method not in DELIVERY_METHODS:raise ValueError('配送方法を確認してください。')
            result[-1]['delivery_method']=method
        else:
            method=result[-1].get('delivery_method','')
        if 'collection_days' in node:
            days=node['collection_days']
            if not isinstance(days,list) or len(days)>6 or len(set(days))!=len(days) or any(d not in COLLECTION_DAYS for d in days):
                raise ValueError('集配曜日を確認してください。')
            if method and method!='自社集配' and days:
                raise ValueError('集配曜日は自社集配のときだけ設定できます。')
            result[-1]['collection_days']=[d for d in COLLECTION_DAYS if d in days]
        elif method and method!='自社集配':
            result[-1]['collection_days']=[]
    byid={n['id']:n for n in result}
    for n in result:
        p=byid.get(n['parent_id'])
        if n['kind']=='company':
            if n['parent_id'] is not None:raise ValueError('企業に所属先は指定できません。')
        elif not p or p['kind'] not in (('company',) if n['kind']=='region' else ('region',)):raise ValueError('カテゴリは会社の下、店舗・拠点はカテゴリの中に登録してください。')
    for n in result:
        if n['kind']=='company':continue
        parent=byid[n['parent_id']]
        root=parent if parent['kind']=='company' else byid[parent['parent_id']]
        supplied=n['customer_id']
        if supplied is not None and supplied!=root['customer_id']:raise ValueError('カテゴリ・拠点は親の顧客と同じ在庫を使います。')
        n['customer_id']=root['customer_id']
    raw_pages=payload.get('request_pages',[])
    if not isinstance(raw_pages,list) or len(raw_pages)>5000:raise ValueError('顧客向けページの数を確認してください。')
    pages=[];page_ids=set();page_names=set()
    for page in raw_pages:
        if not isinstance(page,dict):raise ValueError('顧客向けページを確認してください。')
        pid=page.get('id');name=page.get('name');cid=page.get('customer_id');nids=page.get('node_ids');permission=page.get('can_request')
        if not isinstance(pid,str) or not pid or len(pid)>80 or pid in page_ids or not isinstance(name,str) or not name.strip() or len(name)>100:raise ValueError('ページ名と識別情報を確認してください。')
        if type(cid) is not int or cid not in customers or not isinstance(nids,list) or any(not isinstance(n,str) or n not in byid or byid[n]['customer_id']!=cid for n in nids) or type(permission) is not bool:raise ValueError('ページの顧客と表示範囲を確認してください。')
        key=(cid,' '.join(unicodedata.normalize('NFKC',name).lower().split()))
        if key in page_names:raise ValueError('同じ顧客でページ名が重複しています。')
        page_names.add(key);page_ids.add(pid);pages.append(dict(id=pid,name=name.strip(),customer_id=cid,node_ids=list(dict.fromkeys(nids)),can_request=permission))
    cs=payload.get('customer_scopes',[]);ss=payload.get('staff_scopes',[])
    if not isinstance(cs,list) or not isinstance(ss,list) or len(cs)>5000 or len(ss)>10000:raise ValueError('担当範囲の数を確認してください。')
    customer_scopes=[];seen=set()
    for s in cs:
        if not isinstance(s,dict):raise ValueError('顧客側の担当範囲を確認してください。')
        aid=s.get('account_id')
        if s.get('page_id'):
            if type(aid) is not int or aid not in accounts or aid in seen or not isinstance(s['page_id'],str) or s['page_id'] not in page_ids:raise ValueError('顧客IDと紐づけるページを確認してください。')
            seen.add(aid);customer_scopes.append(dict(account_id=aid,page_id=s['page_id']));continue
        nids=s.get('node_ids');permission=s.get('can_request')
        if type(aid) is not int or aid not in accounts or aid in seen or not isinstance(nids,list) or any(not isinstance(x,str) or x not in ids for x in nids) or type(permission) is not bool:raise ValueError('顧客IDと閲覧・依頼範囲を確認してください。')
        seen.add(aid);customer_scopes.append(dict(account_id=aid,node_ids=list(dict.fromkeys(nids)),can_request=permission))
        if isinstance(s.get('migrated_from_page_id'),str) and len(s['migrated_from_page_id'])<=80:customer_scopes[-1]['migrated_from_page_id']=s['migrated_from_page_id']
    staff_scopes=[];seen=set()
    for s in ss:
        if not isinstance(s,dict):raise ValueError('社内の担当範囲を確認してください。')
        uid=s.get('user_id');nid=s.get('node_id')
        if type(uid) is not int or uid not in staff or not isinstance(nid,str) or nid not in ids or (uid,nid) in seen:raise ValueError('社内担当者と担当範囲を確認してください。')
        seen.add((uid,nid));staff_scopes.append(dict(user_id=uid,node_id=nid))
    settings=payload.get('staff_scope_settings',[])
    if not isinstance(settings,list) or len(settings)>10000:raise ValueError('社内担当者の設定数を確認してください。')
    staff_scope_settings=[];seen=set()
    for setting in settings:
        if not isinstance(setting,dict):raise ValueError('社内担当者の設定を確認してください。')
        uid=setting.get('user_id');cid=setting.get('customer_id')
        if type(uid) is not int or uid not in staff or type(cid) is not int or cid not in customers or (uid,cid) in seen:raise ValueError('社内担当者と顧客を確認してください。')
        seen.add((uid,cid));staff_scope_settings.append(dict(user_id=uid,customer_id=cid))
    return dict(nodes=result,customer_scopes=customer_scopes,staff_scopes=staff_scopes,request_pages=pages,staff_scope_settings=staff_scope_settings)


def save(c,payload,version,customers,staff,user):
    if not admin(user):raise PermissionError('企業・カテゴリ・利用範囲の設定は主管理者・管理者が行ってください。')
    c.execute('BEGIN IMMEDIATE')
    try:
        old=read(c)
        if type(version) is not int or old['version']!=version:raise ValueError('別の画面で組織が更新されています。再読込してください。')
        accounts={r[0] for r in c.execute('SELECT id FROM customer_accounts')}
        config=validate(payload,customers,staff,accounts)
        c.execute('UPDATE web_request_org_settings SET version=version+1,payload=? WHERE id=1',(json.dumps(config,ensure_ascii=False),));c.commit()
    except Exception:c.rollback();raise
