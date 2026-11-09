"""WEB依頼の部署・採用品設定。在庫DBは読み取り専用。"""
import json
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

def catalog(path):
    if not path.exists(): return {'customers':[], 'products':[]}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as s:
        s.row_factory=sqlite3.Row
        customers=[dict(r) for r in s.execute('SELECT id,name FROM customers WHERE active=1 ORDER BY name')]
        products=[dict(r) for r in s.execute("SELECT id,code,name,color FROM products WHERE active=1 AND merged_into_id IS NULL AND category IN ('','制服') ORDER BY name,id")]
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

def state(c,path):
    data=catalog(path)
    data['departments']=[dict(r) for r in c.execute('SELECT * FROM web_request_departments ORDER BY name,id')]
    data['uniforms']=[]
    for r in c.execute('SELECT * FROM web_request_uniforms ORDER BY id'):
        row=dict(r);row['sizes']=json.loads(row['sizes']);data['uniforms'].append(row)
    return data

def save(c,path,d):
    data=catalog(path)
    if d.get('action')=='department':
        cid=int(d['customer_id']);name=str(d.get('name','')).strip()
        if cid not in {x['id'] for x in data['customers']} or not name or len(name)>80:
            raise ValueError('顧客と部署名を確認してください。')
        c.execute('INSERT OR IGNORE INTO web_request_departments(customer_id,name) VALUES(?,?)',(cid,name))
    elif d.get('action')=='uniform':
        did=int(d['department_id']);pid=int(d['product_id'])
        if not c.execute('SELECT 1 FROM web_request_departments WHERE id=?',(did,)).fetchone():
            raise ValueError('部署を選んでください。')
        p=next((x for x in data['products'] if x['id']==pid),None)
        if not p: raise ValueError('有効な制服を選んでください。')
        sizes=d.get('sizes',[]);name=str(d.get('display_name','')).strip()
        if not isinstance(sizes,list) or not sizes or any(x not in p['sizes'] for x in sizes) or len(name)>120:
            raise ValueError('在庫に登録されているサイズを１つ以上選んでください。')
        c.execute('''INSERT INTO web_request_uniforms(department_id,product_id,display_name,sizes,published)
          VALUES(?,?,?,?,?) ON CONFLICT(department_id,product_id) DO UPDATE SET
          display_name=excluded.display_name,sizes=excluded.sizes,published=excluded.published''',
          (did,pid,name,json.dumps(list(dict.fromkeys(sizes)),ensure_ascii=False),int(bool(d.get('published')))))
    elif d.get('action')=='remove':
        c.execute('DELETE FROM web_request_uniforms WHERE id=?',(int(d['id']),))
    else: raise ValueError('操作を確認してください。')
    c.commit()
    return state(c,path)
