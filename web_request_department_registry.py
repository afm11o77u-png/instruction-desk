"""Department definitions and explicit personnel-target assignments."""
import copy
import json


def schema(c):
    c.execute("CREATE TABLE IF NOT EXISTS web_request_department_library(customer_id INTEGER PRIMARY KEY,version INTEGER NOT NULL DEFAULT 0,config TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS web_request_department_targets(node_id TEXT PRIMARY KEY,customer_id INTEGER NOT NULL,version INTEGER NOT NULL DEFAULT 0,department_ids TEXT NOT NULL)")


def library(c,base):
    row=c.execute('SELECT * FROM web_request_department_library WHERE customer_id=?',(base['customer_id'],)).fetchone()
    cfg=json.loads(row['config']) if row else copy.deepcopy(base['draft'])
    if not row:
        import web_request_organizations as organizations
        import web_request_inheritance as inheritance
        org=organizations.read(c);known={d['id'] for d in cfg['departments']};names={d['name'] for d in cfg['departments']}
        for n in org['nodes']:
            if n['customer_id']!=base['customer_id'] or n['kind']!='site' or inheritance.record(c,n['id'])['draft']['mode']!='custom':continue
            for original in inheritance.resolve(c,base,org,n['id'])[0]['departments']:
                if original['id'] in known:continue
                dep=copy.deepcopy(original)
                if dep['name'] in names:dep['name']=(dep['name'][:35]+'（'+n['name'][:35]+'）')[:80]
                stem=dep['name'];suffix=2
                while dep['name'] in names:
                    dep['name']=stem[:70]+'（'+str(suffix)+'）';suffix+=1
                cfg['departments'].append(dep);known.add(dep['id']);names.add(dep['name'])
    return dict(customer_id=base['customer_id'],version=row['version'] if row else 0,draft=cfg,status='draft',publication=None)


def selection(c,base,org,node):
    row=c.execute('SELECT * FROM web_request_department_targets WHERE node_id=?',(node['id'],)).fetchone()
    if row:return dict(node_id=node['id'],version=row['version'],department_ids=json.loads(row['department_ids']))
    # Only previous explicit registrations are candidates; inherited defaults are not assignments.
    if node['kind']=='site':
        import web_request_inheritance as inheritance
        if inheritance.record(c,node['id'])['draft']['mode']=='custom':
            cfg=inheritance.resolve(c,base,org,node['id'])[0]
            return dict(node_id=node['id'],version=0,department_ids=[d['id'] for d in cfg['departments']])
    return dict(node_id=node['id'],version=0,department_ids=[])


def configuration(c,base,org,node):
    cfg=copy.deepcopy(library(c,base)['draft'])
    chosen=set(selection(c,base,org,node)['department_ids'])
    cfg['departments']=[d for d in cfg['departments'] if d['id'] in chosen]
    return cfg
