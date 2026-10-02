"""Per-user input presets, stored separately from posted instructions."""
import base64
import json
from auth import log
import excel_import
import repair

def initialize(c):
    repair.initialize(c)
    c.execute('CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, folder TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(user_id,folder,name))')

def handle(handler, c, path, data=None):
    if path.startswith('/api/templates/admin'): return repair.handle(handler,c,path,data)
    u = handler.session_user(c)
    if not u or u['must_change']: return 401, {'error':'ログインし直してください。'}
    if u['role'] in ('floor','floor2'): return 403, {'error':'ひな形は営業・主管理者が利用できます。'}
    if path=='/api/templates/import':
        return _import_excel(data)
    if data is None:
        return 200, [dict(r, payload=json.loads(r['payload'])) for r in c.execute('SELECT id,folder,name,version,payload FROM templates WHERE user_id=? ORDER BY folder,name', (u['id'],))]
    old = None
    if data.get('id'):
        old = c.execute('SELECT * FROM templates WHERE id=? AND user_id=?', (data['id'],u['id'])).fetchone()
        if not old: return 404, {'error':'ひな形が見つかりません。'}
        if old['version']!=data.get('version'): return 409, {'error':'別の画面で更新されました。ひな形を選び直してください。'}
    if path.endswith('/delete'):
        if not old: return 404, {'error':'ひな形が見つかりません。'}
        c.execute('DELETE FROM templates WHERE id=? AND user_id=?',(old['id'],u['id']))
        log(c,u,'自分用ひな形を削除',old['folder']+'/'+old['name'])
        return 200, {'ok':True}
    if path!='/api/templates/save': return 404, {'error':'操作が見つかりません。'}
    name=data.get('name'); folder=data.get('folder','')
    if not isinstance(name,str) or not 1<=len(name.strip())<=80: raise ValueError('保存名を1～80文字で入力してください。')
    if not isinstance(folder,str) or len(folder.strip())>80: raise ValueError('フォルダ名は80文字以内で入力してください。')
    name=name.strip();folder=folder.strip()
    p=data.get('payload')
    if not isinstance(p,dict) or not isinstance(p.get('items',[]),list) or len(p.get('items',[]))>100: raise ValueError('ひな形の内容を確認してください。')
    if any(not isinstance(x,dict) for x in p.get('items',[])): raise ValueError('商品明細を確認してください。')
    p={k:v for k,v in p.items() if k not in ['id','number','version','history','status','sample']}
    p['template']='common'
    collision=c.execute('SELECT id FROM templates WHERE user_id=? AND folder=? AND name=?',(u['id'],folder,name)).fetchone()
    if collision and (not old or collision['id']!=old['id']): return 409, {'error':'同じフォルダに同名のひな形があります。名前を変えるか、既存のひな形を選んで上書きしてください。'}
    encoded=json.dumps(p,ensure_ascii=False)
    if old:
        c.execute('UPDATE templates SET folder=?,name=?,payload=?,version=version+1 WHERE id=? AND user_id=?',(folder,name,encoded,old['id'],u['id']))
        tid=old['id']
    else:
        tid=c.execute('INSERT INTO templates(user_id,folder,name,version,payload) VALUES(?,?,?,1,?)',(u['id'],folder,name,encoded)).lastrowid
    log(c,u,'自分用ひな形を保存',folder+'/'+name)
    r=c.execute('SELECT id,folder,name,version,payload FROM templates WHERE id=?',(tid,)).fetchone()
    return 200,dict(r,payload=json.loads(r['payload']))

def _import_excel(data):
    raw=data.get('data') if isinstance(data, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return 400, {'error':'Excelファイルを選んでください。'}
    try:
        blob=base64.b64decode(raw, validate=True)
    except Exception:
        return 400, {'error':'Excelファイルを読み取れませんでした。'}
    if len(blob)>6_000_000:
        return 400, {'error':'ファイルが大きすぎます。'}
    try:
        payload, warnings=excel_import.parse_instruction(blob)
    except ValueError as e:
        return 400, {'error':str(e)}
    except Exception:
        return 400, {'error':'このExcelの書式は読み取れませんでした。'}
    return 200, {'payload':payload, 'warnings':warnings}
