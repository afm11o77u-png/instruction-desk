"""Preview Excel department-to-site links and save the reviewed links atomically."""
import json
import uuid
import department_excel as excel
import web_request_organizations as orgs
import web_request_department_registry as registry


def preview(d, library, org, nodes):
    source=d.get('source','customer')
    plan=excel.preview(d.get('file'),library['draft']['departments'],source)
    customers=excel.preview(d.get('customer_file'),[], 'customer') if source=='contract' else plan
    bycode={}
    for row in customers['records']:
        bycode.setdefault(row['code'].casefold(),set()).add(row['customer'])
    rows={}
    for row in plan['records']:
        code=row['code'];names=bycode.get(code.casefold(),set())
        customer=next(iter(names)) if len(names)==1 else ''
        site=customer.split(' ',1)[0]
        key=(code.casefold(),row['name'].casefold())
        if key in rows:rows[key]['count']+=1;continue
        matches=[n for n in nodes if code and excel.normalized(n.get('office_code')).casefold()==code.casefold()]
        if not matches and site:
            matches=[n for n in nodes if excel.normalized(n['name']).casefold()==site.casefold()]
        node=matches[0] if len(matches)==1 else None
        rows[key]=dict(code=code,customer=customer,site_name=site,name=row['name'],count=1,
                       node_id=node['id'] if node else '',selected=bool(node and row['name']),
                       note='コード一致' if node and excel.normalized(node.get('office_code')).casefold()==code.casefold() else '拠点名一致' if node else '登録先を選択してください')
    return dict(rows=list(rows.values()),version=library['version'],organization_version=org['version'],
                target_versions={n['node_id']:n['version'] for n in d['_assignments']},
                source_rows=plan['source_rows'],deleted=plan['deleted'])


def apply(c, d, data, base, user, validate_config, text):
    cid=base['customer_id'];rows=d.get('rows')
    if not isinstance(rows,list) or not rows or len(rows)>20000:raise ValueError('取り込む行を選択してください。')
    c.execute('BEGIN IMMEDIATE')
    try:
        org=orgs.read(c);lib=registry.library(c,base)
        if type(d.get('version')) is not int or d['version']!=lib['version'] or d.get('organization_version')!=org['version']:raise ValueError('部署・組織が更新されています。Excelを選び直してください。')
        cfg=lib['draft'];names={excel.normalized(x['name']).casefold():x['id'] for x in cfg['departments']};added=0;links={}
        for row in rows:
            if not isinstance(row,dict):raise ValueError('取り込み内容を確認してください。')
            name=excel.normalized(text(row.get('name'),'部署名',80,True));nid=row.get('node_id')
            node=next((n for n in org['nodes'] if n['id']==nid and n['customer_id']==cid),None)
            if not node or nid not in data['allowedNodeIds'] or not orgs.personnel_enabled(org,node):raise PermissionError('割り当て可能な登録先を選んでください。')
            key=name.casefold()
            if key not in names:
                names[key]=str(uuid.uuid4());cfg['departments'].append(dict(id=names[key],name=name,contracts=[]));added+=1
            links.setdefault(nid,set()).add(names[key])
        cfg=validate_config(cfg,data,cid)
        versions=d.get('target_versions')
        if not isinstance(versions,dict):raise ValueError('割り当て情報を読み直してください。')
        assigned=0
        for nid,ids in links.items():
            node=next(n for n in org['nodes'] if n['id']==nid);current=registry.selection(c,base,org,node)
            if versions.get(nid)!=current['version']:raise ValueError('割り当てが更新されています。Excelを選び直してください。')
            chosen=current['department_ids'];new=[x['id'] for x in cfg['departments'] if x['id'] in ids and x['id'] not in chosen];assigned+=len(new)
            if new:c.execute('INSERT INTO web_request_department_targets VALUES(?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET version=excluded.version,department_ids=excluded.department_ids',(nid,cid,current['version']+1,json.dumps(chosen+new)))
        if added:c.execute('INSERT INTO web_request_department_library VALUES(?,?,?) ON CONFLICT(customer_id) DO UPDATE SET version=excluded.version,config=excluded.config',(cid,lib['version']+1,json.dumps(cfg,ensure_ascii=False)))
        c.commit()
    except Exception:c.rollback();raise
    return dict(added=added,assigned=assigned,targets=len(links))


def preview_library(d, library):
    """顧客Excelから部署名だけを取り出す（同じ名前は1行にまとめる）。拠点への割り当てはしない。"""
    plan=excel.preview(d.get('file'),library['draft']['departments'],'customer')
    rows=[dict(name=g['name'],count=len(g['rows']),existing=g['existing'],selected=not g['existing'],
               examples=[e['customer'] for e in g['examples']]) for g in plan['candidates']]
    return dict(rows=rows,version=library['version'],source_rows=plan['source_rows'],deleted=plan['deleted'],
                unresolved=len(plan['unresolved']))


def apply_library(c, d, data, base, validate_config, text):
    """選んだ部署名を会社の部署として登録する（割り当てなし＝どの拠点にも未割り当て）。"""
    cid=base['customer_id'];rows=d.get('rows')
    if not isinstance(rows,list) or not rows or len(rows)>2000:raise ValueError('取り込む部署を選択してください。')
    c.execute('BEGIN IMMEDIATE')
    try:
        lib=registry.library(c,base)
        if type(d.get('version')) is not int or d['version']!=lib['version']:raise ValueError('部署が更新されています。Excelを選び直してください。')
        cfg=lib['draft'];names={excel.normalized(x['name']).casefold() for x in cfg['departments']};added=0;skipped=0
        for row in rows:
            if not isinstance(row,dict):raise ValueError('取り込み内容を確認してください。')
            name=excel.normalized(text(row.get('name'),'部署名',80,True));key=name.casefold()
            if key in names:skipped+=1;continue
            names.add(key);cfg['departments'].append(dict(id=str(uuid.uuid4()),name=name,contracts=[]));added+=1
        if added:
            cfg=validate_config(cfg,data,cid)
            c.execute('INSERT INTO web_request_department_library VALUES(?,?,?) ON CONFLICT(customer_id) DO UPDATE SET version=excluded.version,config=excluded.config',(cid,lib['version']+1,json.dumps(cfg,ensure_ascii=False)))
        c.commit()
    except Exception:c.rollback();raise
    return dict(added=added,skipped=skipped,assigned=0,targets=0)


def _alias_key(site):
    import site_name_key as snk
    return snk._plain(site)


def ensure_aliases(c):
    c.execute('CREATE TABLE IF NOT EXISTS web_request_site_aliases(customer_id INTEGER NOT NULL,source TEXT NOT NULL,node_id TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(customer_id,source))')


def read_aliases(c, cid):
    ensure_aliases(c)
    return {r[0]:r[1] for r in c.execute('SELECT source,node_id FROM web_request_site_aliases WHERE customer_id=?',(cid,))}


def _match_site(code, site, nodes, company, terms=(), aliases=None):
    """営業所コード → 前回選んだ店 → 同じ名前 → 拠点の一括取り込みと同じゆるい一致 → 追加の名前を省いて一致 → 名前の一部・始まりが一致（1件のときだけ・要確認）。"""
    import site_name_key as snk
    if code:
        hit=[n for n in nodes if excel.normalized(n.get('office_code')).casefold()==code.casefold()]
        if len(hit)==1:return hit[0],'コード一致',False
        if len(hit)>1:return None,'同じコードの拠点が複数あります',False
    if aliases:
        hit=aliases.get(code_alias(code)) if code else None
        if not hit and site:hit=aliases.get(_alias_key(site))
        node=next((n for n in nodes if n['id']==hit),None) if hit else None
        if node:return node,'前回選んだ店',False
    if not site:return None,'顧客名から店を特定できません',False
    hit=[n for n in nodes if excel.normalized(n['name']).casefold()==site.casefold()]
    if len(hit)==1:return hit[0],'拠点名一致',False
    sites=[n for n in nodes if n['kind']=='site']
    node,how,similar=snk.find(site,company,sites,lambda n:company)
    if node:return node,how,False
    if how.startswith('同じ名前'):return None,how,False
    key=(snk.site_keys(site,company) or [''])[0]
    tkeys=[k for k in (snk._plain(t) for t in terms) if k]
    def bare(k):
        for t in tkeys:
            if k.startswith(t) and len(k)>len(t):return k[len(t):]
        return k
    if tkeys and key:
        top=[n for n in sites for k in (snk.site_keys(n['name'],company) or [''])[:1] if k and bare(k)==bare(key)]
        if len(top)==1:return top[0],'拠点名一致（追加の名前を省略）',False
    if len(key)>=2:
        part=[(len(k),n) for n in sites for k in (snk.site_keys(n['name'],company) or [''])[:1] if len(k)>=2 and (k in key or key in k)]
        if part:
            best=max(p[0] for p in part);top=[n for l,n in part if l==best]
            if len(top)==1:return top[0],'名前の一部が一致（確認してください）',True
            return None,'似た拠点が複数あります',False
    if len(similar)==1:
        node=next(n for n in sites if n['name']==similar[0])
        return node,'名前の始まりが一致（確認してください）',True
    return None,'一致する拠点がありません',False


def code_alias(code):
    return 'code:'+excel.normalized(code).casefold()


def _contract_records(d, deps):
    """契約マスタ（1人1行）＋顧客マスタ → (顧客コード, 顧客名, 店名, 部署) の行と、使わなかった行の理由別件数。
    部署は契約の部署名が登録済みならそれ、違えば顧客マスタの顧客名の部署（登録済みのときだけ）。"""
    plan=excel.preview(d.get('file'),[],'contract',skip_retired=True)
    master={}
    for r in excel.preview(d.get('customer_file'),[],'customer')['records']:master.setdefault(r['code'].casefold(),r)
    out=[];reasons={}
    for r in plan['records']:
        m=master.get(r['code'].casefold());dep=deps.get(r['name'].casefold()) if r['name'] else None
        site=m['site_name'] if m else ''
        if not dep and m:
            dep=deps.get(m['name'].casefold()) if m['name'] else None
            head,_,last=m['customer'].rpartition(' ')
            if not dep and ' ' in m['name'] and deps.get(last.casefold()):dep,site=deps[last.casefold()],head
        if not dep:
            why='部署名「'+(r['name'] or '空欄')+'」'+('' if m else '（顧客マスタにないコード）')
            reasons[why]=reasons.get(why,0)+1;continue
        out.append(dict(code=r['code'],customer=m['customer'] if m else '',site=site,dep=dep,in_master=bool(m)))
    return plan,out,reasons


def _store_code(code):
    """AI-101-01 → AI-101（同じ店の部署違い）。区切りがなければ空。"""
    head,sep,_=code.rpartition('-')
    return head.casefold() if sep and '-' in head else ''


def dep_done(current, nid, did):
    return did in current.get(nid,set())


def preview_assign(d, library, org, nodes, assignments, company, terms=(), aliases=None):
    """登録済みの部署だけを、Excelの各店（顧客コード）に割り当てる案。部署は作らない。"""
    deps={excel.normalized(x['name']).casefold():x for x in library['draft']['departments'] if not x.get('no_department')}
    current={a['node_id']:set(a['department_ids']) for a in assignments}
    rows={};skipped=0;blank=0;reasons={}
    contract=d.get('source')=='contract'
    if contract:
        plan,found,reasons=_contract_records(d,deps);skipped=sum(reasons.values())
    else:
        plan=excel.preview(d.get('file'),[], 'customer');found=[]
        for r in plan['records']:
            code,customer,name=r['code'],r['customer'],r['name']
            if not name:blank+=1;continue
            dep=deps.get(name.casefold());site=r['site_name']
            head,_,last=customer.rpartition(' ')
            if not dep and ' ' in name and deps.get(last.casefold()):dep,site=deps[last.casefold()],head  # 「シティ あばしり店 管理」は最後の語を部署にする
            if not dep:skipped+=1;continue
            found.append(dict(code=code,customer=customer,site=site,dep=dep,in_master=True))
    for f in found:
        code,customer,site,dep=f['code'],f['customer'],f['site'],f['dep']
        key=(code.casefold(),dep['id'])
        if key in rows:rows[key]['count']+=1;continue
        node,how,loose=_match_site(code,site,nodes,company,terms,aliases)
        if not node and not f['in_master']:how='顧客マスタにないコードです。登録先を選んでください'
        done=bool(node and dep['id'] in current.get(node['id'],set()))
        rows[key]=dict(code=code,customer=customer,site_name=site,department_id=dep['id'],name=dep['name'],count=1,
                       node_id=node['id'] if node else '',matched=bool(node),loose=loose,assigned=done,
                       selected=bool(node) and not done,note='割当済み' if done else how)
    # 同じ店コード（AI-101-01 と AI-101-21 など）で、名前から1つの店に決まった行があれば、未一致の行もその店にする（要確認）。
    by_store={}
    for r in rows.values():
        sc=_store_code(r['code'])
        if sc and r['matched'] and not r['loose']:by_store.setdefault(sc,set()).add(r['node_id'])
    for r in rows.values():
        cand=by_store.get(_store_code(r['code']),set())
        if not r['matched'] and len(cand)==1:
            nid=next(iter(cand));done=dep_done(current,nid,r['department_id'])
            r.update(node_id=nid,matched=True,loose=True,assigned=done,selected=not done,note='割当済み' if done else '同じ店コードの行から（確認してください）')
    order=lambda r:(0 if not r['matched'] else 1 if r['loose'] else 3 if r['assigned'] else 2)
    out=sorted(rows.values(),key=lambda r:(order(r),r['site_name'],r['code'],r['name']))
    return dict(rows=out,version=library['version'],organization_version=org['version'],
                target_versions={a['node_id']:a['version'] for a in assignments},
                source_rows=plan['source_rows'],deleted=plan['deleted'],retired=plan.get('retired',0),unregistered=skipped,blank=blank,
                reasons=sorted(([k,v] for k,v in reasons.items()),key=lambda x:-x[1]),source='contract' if contract else 'customer')


def apply_assign(c, d, data, base):
    """登録済みの部署を選んだ拠点に追加する（既存の割り当ては残す。部署は作らない）。"""
    cid=base['customer_id'];rows=d.get('rows')
    if not isinstance(rows,list) or not rows or len(rows)>20000:raise ValueError('割り当てる行を選択してください。')
    c.execute('BEGIN IMMEDIATE')
    try:
        org=orgs.read(c);lib=registry.library(c,base)
        if type(d.get('version')) is not int or d['version']!=lib['version'] or d.get('organization_version')!=org['version']:raise ValueError('部署・組織が更新されています。Excelを選び直してください。')
        ids={x['id'] for x in lib['draft']['departments']};links={}
        for row in rows:
            if not isinstance(row,dict):raise ValueError('割り当て内容を確認してください。')
            nid=row.get('node_id');did=row.get('department_id')
            if did not in ids:raise ValueError('登録されていない部署は割り当てられません。部署を先に登録してください。')
            node=next((n for n in org['nodes'] if n['id']==nid and n['customer_id']==cid),None)
            if not node or nid not in data['allowedNodeIds'] or not orgs.personnel_enabled(org,node):raise PermissionError('割り当て可能な登録先を選んでください。')
            links.setdefault(nid,set()).add(did)
        remember=[]
        for row in rows:
            src=row.get('site_name')
            if not row.get('remember'):continue
            if isinstance(src,str) and 0<len(src)<=120 and _alias_key(src):remember.append((_alias_key(src),row['node_id']))
            code=row.get('code')
            if isinstance(code,str) and 0<len(code)<=60 and excel.normalized(code):remember.append((code_alias(code),row['node_id']))
        versions=d.get('target_versions')
        if not isinstance(versions,dict):raise ValueError('割り当て情報を読み直してください。')
        assigned=0
        for nid,chosen_ids in links.items():
            node=next(n for n in org['nodes'] if n['id']==nid);current=registry.selection(c,base,org,node)
            if versions.get(nid,0)!=current['version']:raise ValueError('割り当てが更新されています。Excelを選び直してください。')
            have=current['department_ids'];new=[x['id'] for x in lib['draft']['departments'] if x['id'] in chosen_ids and x['id'] not in have];assigned+=len(new)
            if new:c.execute('INSERT INTO web_request_department_targets VALUES(?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET version=excluded.version,department_ids=excluded.department_ids',(nid,cid,current['version']+1,json.dumps(have+new)))
        if remember:
            ensure_aliases(c)
            for src,nid in remember:c.execute('INSERT INTO web_request_site_aliases(customer_id,source,node_id) VALUES(?,?,?) ON CONFLICT(customer_id,source) DO UPDATE SET node_id=excluded.node_id,updated_at=CURRENT_TIMESTAMP',(cid,src,nid))
        c.commit()
    except Exception:c.rollback();raise
    return dict(added=0,assigned=assigned,targets=len(links),remembered=len(dict(remember)))
