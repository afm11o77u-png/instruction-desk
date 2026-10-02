"""Owner-only template correction with version checks and reversible history."""
import json
from datetime import date, datetime
from auth import log

FIELDS = {'customer','employee','owner','received','delivery','request','department','code','management','contract','method','methodNote','car','packing','nameColor','barcodeColor','notes','employmentDate','sex','phonetic','employeeNumber','repair','finishDate','markShipDate','enclosures','advance','advanceCondition','nameRequired'}
ITEM_FIELDS = {'name','maker','sku','size','condition','quantity','note','note2','stock'}
DATES = {'received','delivery','employmentDate','finishDate','markShipDate'}

def initialize(c):
    c.execute('CREATE TABLE IF NOT EXISTS template_repairs (id INTEGER PRIMARY KEY, template_id INTEGER NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL)')

def snapshot(row):
    return {**{k:row[k] for k in ('id','user_id','folder','name','version')},'payload':json.loads(row['payload'])}

def history_record(c,row):
    result={k:row[k] for k in ('id','at','actor','reason','action')}
    a=json.loads(row['before_json']);b=json.loads(row['after_json']);changes=[]
    for key,label in [('user_id','所有者'),('folder','フォルダ'),('name','保存名')]:
        if a.get(key)!=b.get(key):
            values=[a.get(key,''),b.get(key,'')]
            if key=='user_id':
                values=[(c.execute('SELECT name FROM users WHERE id=?',(v,)).fetchone() or [str(v)])[0] for v in values]
            changes.append({'label':label,'before':values[0],'after':values[1]})
    for key in sorted(FIELDS):
        if a['payload'].get(key,'')!=b['payload'].get(key,''):
            changes.append({'field':key,'before':a['payload'].get(key,''),'after':b['payload'].get(key,'')})
    ai=a['payload'].get('items',[]);bi=b['payload'].get('items',[])
    for i in range(max(len(ai),len(bi))):
        left=ai[i] if i<len(ai) else {};right=bi[i] if i<len(bi) else {}
        for key in sorted(ITEM_FIELDS):
            if left.get(key,'')!=right.get(key,''):
                changes.append({'item':i+1,'field':key,'before':left.get(key,''),'after':right.get(key,'')})
    result['changes']=changes
    return result

def short(value, limit, label, required=False):
    if not isinstance(value,str) or len(value)>limit or any(ord(x)<32 and x not in '\n\r\t' for x in value): raise ValueError(f'{label}を{limit}文字以内で入力してください。')
    value=value.strip()
    if required and not value: raise ValueError(f'{label}を入力してください。')
    return value

def handle(h,c,path,d=None):
    u=h.session_user(c)
    if not u or u['must_change']: return 401,{'error':'ログインし直してください。'}
    if u['role']!='owner': return 403,{'error':'管理・修正は主管理者だけが使用できます。'}
    if d is None:
        if path!='/api/templates/admin': return 404,{'error':'操作が見つかりません。'}
        return 200,{'templates':[snapshot(r) for r in c.execute('SELECT * FROM templates ORDER BY user_id,folder,name')],
                    'users':[dict(r) for r in c.execute('SELECT id,name,role,active FROM users ORDER BY name')]}
    if path not in ('/api/templates/admin/save','/api/templates/admin/history','/api/templates/admin/restore'): return 404,{'error':'操作が見つかりません。'}
    if type(d.get('id')) is not int: raise ValueError('ひな形を選択してください。')
    row=c.execute('SELECT * FROM templates WHERE id=?',(d['id'],)).fetchone()
    if not row: return 404,{'error':'ひな形が見つかりません。'}
    if path.endswith('/history'):
        return 200,[history_record(c,r) for r in c.execute('SELECT * FROM template_repairs WHERE template_id=? ORDER BY id DESC LIMIT 100',(row['id'],)).fetchall()]
    if type(d.get('version')) is not int or row['version']!=d['version']: return 409,{'error':'別の画面で更新されています。一覧を更新してから開き直してください。'}
    reason=short(d.get('reason'),500,'修正理由',True)
    before=snapshot(row)
    if path.endswith('/restore'):
        if type(d.get('historyId')) is not int: raise ValueError('戻す履歴を選んでください。')
        history=c.execute('SELECT before_json FROM template_repairs WHERE id=? AND template_id=?',(d['historyId'],row['id'])).fetchone()
        if not history: return 404,{'error':'履歴が見つかりません。'}
        target=json.loads(history['before_json']);action='修正前へ戻す'
    else:
        target={**before,'user_id':d.get('user_id'),'folder':short(d.get('folder'),80,'フォルダ名'),'name':short(d.get('name'),80,'保存名',True)}
        patch=d.get('fields');items=d.get('items')
        if not isinstance(patch,dict) or set(patch)-FIELDS: raise ValueError('修正項目を確認してください。')
        payload=dict(before['payload'])
        for k,v in patch.items():
            payload[k]=short(v,4000 if k=='notes' else 300,'入力内容')
            if k in DATES and payload[k]: date.fromisoformat(payload[k])
        if not isinstance(items,list) or len(items)>100: raise ValueError('商品明細は100行以内にしてください。')
        clean=[]
        for item in items:
            if not isinstance(item,dict) or set(item)-ITEM_FIELDS: raise ValueError('商品明細を確認してください。')
            x={k:short(v,1000 if k in ('note','note2') else 300,'商品明細') for k,v in item.items() if k!='quantity'}
            q=item.get('quantity','')
            if q!='' and (type(q) is not int or not 1<=q<=99999): raise ValueError('数量は1～99999、または空欄にしてください。')
            x['quantity']=q;clean.append(x)
        payload['items']=clean;target['payload']=payload;action='ひな形を修正'
    if type(target['user_id']) is not int: raise ValueError('所有者を選んでください。')
    person=c.execute('SELECT role,active FROM users WHERE id=?',(target['user_id'],)).fetchone()
    if not person: raise ValueError('所有者が見つかりません。')
    if target['user_id']!=row['user_id'] and (not person['active'] or person['role'] not in ('owner','sales')): raise ValueError('引継ぎ先は有効な営業・主管理者を選んでください。')
    collision=c.execute('SELECT id FROM templates WHERE user_id=? AND folder=? AND name=? AND id<>?',(target['user_id'],target['folder'],target['name'],row['id'])).fetchone()
    if collision: return 409,{'error':'引継ぎ先の同じフォルダに同名のひな形があります。保存名を変更してください。'}
    target['version']=row['version']+1
    c.execute('UPDATE templates SET user_id=?,folder=?,name=?,payload=?,version=? WHERE id=?',(target['user_id'],target['folder'],target['name'],json.dumps(target['payload'],ensure_ascii=False),target['version'],row['id']))
    c.execute('INSERT INTO template_repairs(template_id,at,actor,reason,action,before_json,after_json) VALUES(?,?,?,?,?,?,?)',(row['id'],datetime.now().isoformat(timespec='seconds'),u['name'],reason,action,json.dumps(before,ensure_ascii=False),json.dumps(target,ensure_ascii=False)))
    log(c,u,'管理・修正：'+action,str(row['id'])+' '+target['name']+' ／ '+reason)
    return 200,target
