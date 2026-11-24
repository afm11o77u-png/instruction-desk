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
