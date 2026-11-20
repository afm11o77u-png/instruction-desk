"""ID-based patches: untouched settings inherit future parent changes."""
import copy
import json

ARRAYS={'departments','contracts','uniforms'}


def schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS web_request_scope_settings(
    node_id TEXT PRIMARY KEY,customer_id INTEGER NOT NULL,version INTEGER NOT NULL DEFAULT 0,
    draft TEXT NOT NULL,publication TEXT,status TEXT NOT NULL DEFAULT 'draft')""")


def difference(parent,current):
    out={}
    for key,value in current.items():
        if key.startswith('_'):continue
        old=parent.get(key)
        if key in ARRAYS:
            oldmap={x['id']:x for x in (old or [])};newmap={x['id']:x for x in value};changes=[]
            for item in value:
                if item['id'] not in oldmap:changes.append(copy.deepcopy(item))
                else:
                    patch=difference(oldmap[item['id']],item)
                    if patch:changes.append(dict(patch,id=item['id'],_existing=True))
            changes.extend(dict(id=k,_delete=True) for k in oldmap if k not in newmap)
            if changes:out[key]=changes
        elif old!=value:out[key]=copy.deepcopy(value)
    return out


def apply(parent,patch,source):
    out=copy.deepcopy(parent)
    for key,value in patch.items():
        if key in ARRAYS:
            rows={x['id']:x for x in out.get(key,[])}
            for item in value:
                ident=item['id']
                if item.get('_delete'):rows.pop(ident,None)
                elif not item.get('_existing') or ident in rows:
                    rows[ident]=apply(rows.get(ident,{}),item,source)
                    if key=='uniforms':rows[ident]['_source']=source
            out[key]=list(rows.values())
        elif not key.startswith('_'):out[key]=copy.deepcopy(value)
    return out


def annotate(config,source):
    config=copy.deepcopy(config)
    for d in config.get('departments',[]):
        for k in d.get('contracts',[]):
            for u in k.get('uniforms',[]):u['_source']=source
    return config


def record(c,node_id):
    r=c.execute('SELECT * FROM web_request_scope_settings WHERE node_id=?',(node_id,)).fetchone()
    if not r:return dict(version=0,draft=dict(mode='inherit',patch={}),publication=None,status='draft')
    return dict(version=r['version'],draft=json.loads(r['draft']),publication=json.loads(r['publication']) if r['publication'] else None,status=r['status'])


def chain(org,cid,node_id):
    if not node_id:return []
    byid={n['id']:n for n in org['nodes']};n=byid.get(node_id)
    if not n or n['customer_id']!=cid or n['kind']=='company':raise ValueError('この顧客の地区・拠点を選んでください。')
    path=[];seen=set()
    while n and n['kind']!='company':
        if n['id'] in seen:raise ValueError('所属先の関係を確認してください。')
        seen.add(n['id']);path.append(n);n=byid.get(n['parent_id'])
    return list(reversed(path))


def resolve(c,base,org,node_id,published=False,skip_last=False):
    path=chain(org,base['customer_id'],node_id)
    if skip_last:path=path[:-1]
    cfg=annotate(base['publication']['config'] if published and base['publication'] else base['draft'],'顧客共通')
    status=base['status'] if published else 'draft'
    if published and not base['publication']:status='draft'
    for n in path:
        r=record(c,n['id']);payload=r['publication'] if published else r['draft']
        if payload and payload['mode']=='custom':cfg=apply(cfg,payload['patch'],n['name'])
        if published and r['status']=='paused':status='paused'
    return cfg,status


def profile(c,base,org,node):
    r=record(c,node['id']);draft,_=resolve(c,base,org,node['id']);public,status=resolve(c,base,org,node['id'],True)
    parent,_=resolve(c,base,org,node['id'],skip_last=True)
    return dict(customer_id=base['customer_id'],node_id=node['id'],name=node['name'],version=r['version'],draft=draft,
       draft_mode=r['draft']['mode'],parent_config=parent,publication=dict(config=public,version=(r['publication'] or {}).get('version',0)) if status=='published' else None,status=status,
       own_status=r['status'],own_publication=r['publication'])
