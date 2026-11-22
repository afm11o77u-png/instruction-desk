"""Shared customer contracts; WEB drafts are authoritative, rental rows are projections."""
from contextlib import closing
from datetime import datetime
import copy
import json
import sqlite3


def records(c, customer_ids):
    import web_requests as web
    import web_request_inheritance as inheritance
    import web_request_organizations as organizations
    org = organizations.read(c)
    result = []
    for cid in customer_ids:
        base = web.read_config(c, cid)
        configs = [(None, '顧客共通', base['draft'])]
        for node in org['nodes']:
            if node['customer_id'] == cid and node['kind'] != 'company' and inheritance.record(c, node['id'])['draft']['mode'] == 'custom':
                configs.append((node['id'], node['name'], inheritance.resolve(c, base, org, node['id'])[0]))
        for node_id, scope_name, cfg in configs:
            for dep in cfg.get('departments', []):
                for contract in dep.get('contracts', []):
                    ref = f"{cid}:{dep['id']}:{contract['id']}"
                    if node_id: ref = f"scope:{node_id}:" + ref
                    shared={key:copy.deepcopy(value) for key,value in contract.items() if key!='uniforms'}
                    shared['uniforms']=[{key:value for key,value in u.items() if key not in ('photo','description','display_name','_source')} for u in contract.get('uniforms',[])]
                    result.append(dict(ref=ref, customer_id=cid, node_id=node_id, department_id=dep['id'], contract_key=contract['id'], department=dep['name'], scope_name=scope_name, contract=shared))
    return result


def migrate(c, rental_path):
    """Idempotently preserve independent rental contracts in shared drafts, never publish."""
    import web_requests as web
    with closing(sqlite3.connect(rental_path)) as rental:
        rental.row_factory = sqlite3.Row
        rows = [dict(r) for r in rental.execute('SELECT * FROM contracts')]
        known = {r['ref']: r for r in records(c, {r['customer_id'] for r in rows})}
        mappings = []
        c.execute('BEGIN IMMEDIATE')
        try:
            for row in rows:
                # A previous shared mapping must not resurrect deliberately removed contracts.
                if (row.get('external_key') or '').startswith('common:'):
                    continue
                ref = row.get('source_ref')
                if ref in known:
                    entry=known[ref];profile=web.read_config(c,row['customer_id']);cfg=copy.deepcopy(profile['draft'])
                    contract=next(k for d in cfg['departments'] if d['id']==entry['department_id'] for k in d['contracts'] if k['id']==entry['contract_key'])
                    changed=False
                    for key in ('service_type','default_ownership','start_date','end_date','notes'):
                        if key not in contract:contract[key]=row[key];changed=True
                    if changed:
                        c.execute('UPDATE web_request_customer_settings SET version=?,draft=? WHERE customer_id=?',(profile['version']+1,json.dumps(cfg,ensure_ascii=False),row['customer_id']))
                    mappings.append((ref, row['id'])); continue
                profile = web.read_config(c, row['customer_id'])
                cfg = copy.deepcopy(profile['draft'])
                dep = next((d for d in cfg['departments'] if d['id'] == 'uniform-import'), None)
                if dep is None:
                    name = 'ユニフォーム管理から引継ぎ'
                    names = {d['name'] for d in cfg['departments']}
                    while name in names: name += '・引継ぎ'
                    dep = dict(id='uniform-import', name=name, contracts=[]); cfg['departments'].append(dep)
                key = 'uniform-' + str(row['id'])
                if not any(k['id'] == key for k in dep['contracts']):
                    lines = [dict(x) for x in rental.execute('SELECT * FROM contract_lines WHERE contract_id=?', (row['id'],))]
                    dep['contracts'].append(dict(id=key, name=row['name'], kind=row['kind'], default_qty=3, active=bool(row['active']), service_type=row['service_type'], default_ownership=row['default_ownership'], start_date=row['start_date'], end_date=row['end_date'], notes=row['notes'], uniforms=[dict(id=f"uniform-line-{x['id']}", customer_product_id=x['customer_product_id'], product_id=x['product_id'], default_qty=x['quantity'], display_name='', description='', photo='', sort_order=i, active=True) for i, x in enumerate(lines)]))
                    c.execute("INSERT INTO web_request_customer_settings VALUES(?,?,?,?,?) ON CONFLICT(customer_id) DO UPDATE SET version=excluded.version,draft=excluded.draft", (row['customer_id'], profile['version']+1, json.dumps(cfg, ensure_ascii=False), json.dumps(profile['publication'], ensure_ascii=False) if profile['publication'] else None, profile['status']))
                mappings.append((f"{row['customer_id']}:uniform-import:{key}", row['id']))
            c.commit()
        except Exception:
            c.rollback(); raise
        for ref, ident in mappings:
            rental.execute('UPDATE contracts SET source_ref=?,external_key=? WHERE id=?', (ref, 'common:'+ref, ident))
        rental.commit()


def synchronize(path, data):
    """Refresh rental lookup rows without changing IDs, wearer links or historical snapshots."""
    from rental_management.service import connection
    if 'common_contracts' not in data: return
    now = datetime.now().isoformat(timespec='seconds')
    with connection(path) as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            refs = set()
            for entry in data['common_contracts']:
                ref = entry['ref']; refs.add(ref); k = entry['contract']
                row = c.execute('SELECT * FROM contracts WHERE source_ref=?', (ref,)).fetchone()
                name = f"{entry['department']} / {k['name']}"
                if entry['node_id']: name = entry['scope_name'] + ' / ' + name
                values = dict(customer_id=entry['customer_id'], name=name, kind=k['kind'], start_date=k.get('start_date') or '0001-01-01', end_date=k.get('end_date', ''), notes=k.get('notes', ''), active=int(bool(k['active'])), source_ref=ref, external_key='common:'+ref, service_type=k.get('service_type', 'レンタル'), default_ownership=k.get('default_ownership', '自社所有'))
                valid_handles = {h['id']: h for h in data['handles'] if h['customer_id'] == entry['customer_id']}
                lines = [(u['customer_product_id'], valid_handles[u['customer_product_id']]['product_id'], u['default_qty']) for u in k['uniforms'] if u['active'] and u.get('customer_product_id') in valid_handles]
                if row:
                    ident = row['id']
                    old_lines = [tuple(x) for x in c.execute('SELECT customer_product_id,product_id,quantity FROM contract_lines WHERE contract_id=? ORDER BY customer_product_id', (ident,))]
                    if all(row[key] == value for key, value in values.items()) and old_lines == sorted(lines): continue
                    c.execute('UPDATE contracts SET '+','.join(key+'=?' for key in values)+',version=version+1,updated_at=? WHERE id=?', (*values.values(), now, ident))
                else:
                    ident = c.execute('INSERT INTO contracts('+','.join(values)+',created_at,updated_at) VALUES('+','.join('?' for _ in values)+',?,?)', (*values.values(), now, now)).lastrowid
                c.execute('DELETE FROM contract_lines WHERE contract_id=?', (ident,))
                c.executemany('INSERT INTO contract_lines(contract_id,customer_product_id,product_id,quantity) VALUES(?,?,?,?)', [(ident, *line) for line in lines])
            allowed = {x['id'] for x in data['customers']}
            for row in c.execute('SELECT id,customer_id,source_ref,active FROM contracts WHERE external_key LIKE ?', ('common:%',)).fetchall():
                if row['customer_id'] in allowed and row['source_ref'] not in refs and row['active']:
                    c.execute('UPDATE contracts SET active=0,version=version+1,updated_at=? WHERE id=?', (now, row['id']))
            c.commit()
        except Exception:
            c.rollback(); raise
