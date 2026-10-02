"""Picking lists: persisted snapshots, requester provenance and audited changes."""
import json, os, re, subprocess, uuid, winreg
from datetime import datetime, date
from pathlib import Path
from auth import log
from picking_xlsx import workbook

MAIL_DIR = Path(os.environ.get('INSTRUCTION_DATA', str(Path(__file__).resolve().parent / 'data'))) / 'mail-out'

GROUPS = {'warehouse':'石狩倉庫', 'factory':'発寒支店', 'carry':'管理担当持込'}
FIELDS = ['requester','customer','name','maker','sku','size','condition','quantity','note','note2','stock']

def instruction_number(row_id, payload):
    payload=payload or {}
    if payload.get('status')=='下書き': return ''
    number=str(payload.get('number') or '')
    if re.fullmatch(r'SI-\d{8}-\d{3,4}', number) or re.fullmatch(r'SI-\d{6}', number): return number
    return f'SI-{row_id:06}'

def initialize(c):
    c.execute('CREATE TABLE IF NOT EXISTS picking_lists (id INTEGER PRIMARY KEY, version INTEGER NOT NULL, creator INTEGER NOT NULL, payload TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS picking_sources (order_id INTEGER PRIMARY KEY, list_id INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS picking_history (id INTEGER PRIMARY KEY, list_id INTEGER NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS carry_inputs (day TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS carry_checks (day TEXT NOT NULL, row_key TEXT NOT NULL, checked INTEGER NOT NULL, PRIMARY KEY(day, row_key))')
    c.execute('CREATE TABLE IF NOT EXISTS carry_prints (day TEXT PRIMARY KEY, at TEXT NOT NULL, by_name TEXT NOT NULL)')

def visible(u,r):
    return u['role'] in ('owner','floor') or u['overview'] or u['id']==r['creator']

def editable(u,r):
    return u['role'] in ('owner','floor') or u['id']==r['creator']

def stock_counts(payload):
    counts={'warehouse':0,'factory':0,'carry':0}
    for row in payload.get('rows') or []:
        key=row.get('stock') if isinstance(row,dict) else ''
        if key in counts: counts[key]+=1
    return counts

def arrange_kind(payload):
    counts=stock_counts(payload)
    if counts['warehouse']: return 'mail'
    if counts['factory'] or counts['carry']: return 'local'
    return 'empty'

def finished(payload):
    return bool(payload.get('sentAt') or payload.get('arrangedAt'))

def completion_at(payload):
    return payload.get('sentAt') or payload.get('arrangedAt') or ''

def phase(payload):
    if payload.get('status')=='取消': return '取消'
    if payload.get('sentAt'): return '手配完了（メール送信）'
    if payload.get('arrangedAt'): return '手配完了（メール不要）'
    if arrange_kind(payload)=='local': return 'メール不要・手配待ち'
    if payload.get('mailedAt'): return 'メール作成済み・送信記録待ち'
    if arrange_kind(payload)=='mail': return 'メール依頼が必要・メール未作成'
    return '保存済み・手配未完了'

def strip_list_row(row):
    if isinstance(row,dict):
        row.pop('origin',None)
        row.pop('note',None)
    return row

def strip_list_payload(payload):
    if not isinstance(payload,dict): return False
    dirty=False
    def walk(items):
        nonlocal dirty
        if not isinstance(items,list): return
        for item in items:
            if isinstance(item,dict) and ('origin' in item or 'note' in item):
                strip_list_row(item)
                dirty=True
    walk(payload.get('rows'))
    walk(payload.get('manual'))
    for source in payload.get('sources') or []:
        if isinstance(source,dict): walk(source.get('rows'))
    return dirty

def strip_saved_list_fields(c):
    changed=0
    for row in c.execute('SELECT id,payload FROM picking_lists'):
        payload=json.loads(row['payload'])
        if strip_list_payload(payload):
            c.execute('UPDATE picking_lists SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
            changed+=1
    return changed

def record(r,u):
    payload=json.loads(r['payload'])
    strip_list_payload(payload)
    return dict(payload, id=r['id'],version=r['version'],number=f"PK-{r['id']:06}",canEdit=editable(u,r),phase=phase(payload))

def text(v,limit=300):
    if v is None: return ''
    if not isinstance(v,(str,int,float)): raise ValueError('入力内容を確認してください。')
    s=str(v).strip()
    if len(s)>limit or any(ord(ch)<32 and ch not in '\n\t\r' for ch in s): raise ValueError(f'入力は{limit}文字以内で、制御文字を含めずに入力してください。')
    return s

def stock(v):
    s=str(v or '').strip()
    if s in ('0','倉庫','石狩倉庫','warehouse'): return 'warehouse'
    if s in ('a','a：工場にある','工場','工場内','発寒支店','factory'): return 'factory'
    if s in ('b','b：揃っている','揃っている','持ち込み','管理担当持込','carry'): return 'carry'
    return ''

CARRY_KEY=re.compile(r'carry:\d{4}-\d{2}-\d{2}:[a-f0-9]{12}')

def carry_keys(rows):
    return [r.get('key') for r in rows or [] if isinstance(r,dict) and CARRY_KEY.fullmatch(r.get('key') or '')]

def mutate_carry(c, keys, list_id, claim):
    days={}
    for key in keys: days.setdefault(key.split(':')[1], []).append(key)
    for day, wanted in days.items():
        row=c.execute('SELECT payload FROM carry_inputs WHERE day=?',(day,)).fetchone()
        if not row:
            if claim: raise ValueError('管理担当持込の入力が見つかりません。入力画面を確認してください。')
            continue
        payload=json.loads(row['payload']); found=set()
        for item in payload.get('rows',[]):
            if item.get('key') not in wanted: continue
            found.add(item['key'])
            if claim:
                if item.get('listId') not in (None,'',list_id): raise ValueError('この入力は別の一覧に入っています。出力をやり直してください。')
                item['listId']=list_id
            elif item.get('listId')==list_id: item['listId']=None
        if claim and found!=set(wanted): raise ValueError('管理担当持込の入力が更新されています。出力をやり直してください。')
        c.execute('UPDATE carry_inputs SET version=version+1, payload=? WHERE day=?',(json.dumps(payload,ensure_ascii=False),day))

def sync_carry(c, list_id, manual, previous, active):
    if active:
        gone=[k for k in carry_keys(previous) if k not in set(carry_keys(manual))]
        if gone: mutate_carry(c, gone, list_id, False)
        if carry_keys(manual): mutate_carry(c, carry_keys(manual), list_id, True)
    elif carry_keys(previous): mutate_carry(c, carry_keys(previous), list_id, False)

def carry_manual(c, day, linked=False):
    row=c.execute('SELECT payload FROM carry_inputs WHERE day=?',(day,)).fetchone()
    if not row: return []
    manual=[]
    for item in json.loads(row['payload']).get('rows',[]):
        if item.get('listId') and not linked: continue
        manual.append({k:item.get(k,'') for k in FIELDS}|{'key':item['key'],'origin':item.get('origin') or '交換依頼(他)','requester':item.get('requester') or 'マーク','stock':'carry','sourceNumber':'直接依頼'})
    return manual

def carry_input(c,u,path,d):
    if path.endswith('/list'):
        days=[]
        for row in c.execute('SELECT day,version,payload FROM carry_inputs ORDER BY day DESC'):
            payload=json.loads(row['payload'])
            rows=payload.get('rows') or []
            if rows: days.append({'day':row['day'],'version':row['version'],'updatedAt':payload.get('updatedAt',''),'updatedBy':payload.get('updatedBy',''),'rows':rows})
        return 200,{'days':days}
    day=text(d.get('day',''),10)
    try: date.fromisoformat(day)
    except ValueError: raise ValueError('日付を確認してください。')
    if path.endswith('/load'):
        row=c.execute('SELECT version,payload FROM carry_inputs WHERE day=?',(day,)).fetchone()
        if not row: return 200,{'day':day,'version':0,'rows':[]}
        return 200,{'day':day,'version':row['version'],'rows':json.loads(row['payload']).get('rows',[])}
    if not path.endswith('/save'): return 404,{'error':'見つかりません。'}
    version=d.get('version')
    rows_in=d.get('rows')
    if type(version) is not int or not isinstance(rows_in,list) or len(rows_in)>200: raise ValueError('入力内容を確認してください。')
    existing=c.execute('SELECT version,payload FROM carry_inputs WHERE day=?',(day,)).fetchone()
    if (existing['version'] if existing else 0)!=version: return 409,{'error':'別の画面で更新されています。日付を選び直してください。'}
    old_rows=json.loads(existing['payload']).get('rows',[]) if existing else []
    old_by={r.get('key'):r for r in old_rows}
    new_rows=[]; seen=set()
    for raw in rows_in:
        if not isinstance(raw,dict): raise ValueError('明細を確認してください。')
        key=text(raw.get('key',''),40)
        if not CARRY_KEY.fullmatch(key) or not key.startswith('carry:'+day+':') or key in seen: key='carry:'+day+':'+uuid.uuid4().hex[:12]
        seen.add(key)
        prev=old_by.get(key)
        if prev and prev.get('listId'):
            new_rows.append(prev); continue
        item={k:text(raw.get(k,''),1000 if k in ('note','note2') else 300) for k in ('requester','origin','customer','name','maker','sku','size','condition','note','note2')}
        q=raw.get('quantity',1)
        if isinstance(q,bool) or not re.fullmatch(r'[1-9][0-9]{0,4}',str(q or '')): raise ValueError('数量は1～99999の整数で入力してください。')
        if not item['customer'] and not item['name']: continue
        if not item['customer'] or not item['name']: raise ValueError('顧客名と商品名を入力してください。')
        item.update(key=key,quantity=int(q),stock='carry',requester=item['requester'] or 'マーク',origin=item['origin'] or '交換依頼(他)',listId=None)
        if item['condition'] not in ('','未指定','新品','中古'): item['condition']='未指定'
        new_rows.append(item)
    for key,prev in old_by.items():
        if prev.get('listId') and key not in seen: new_rows.append(prev)
    now=datetime.now().isoformat(timespec='seconds')
    payload=json.dumps({'rows':new_rows,'updatedAt':now,'updatedBy':u['name']},ensure_ascii=False)
    if existing: c.execute('UPDATE carry_inputs SET version=version+1,payload=? WHERE day=?',(payload,day)); version=existing['version']+1
    else: c.execute('INSERT INTO carry_inputs(day,version,payload) VALUES(?,1,?)',(day,payload)); version=1
    log(c,u,'管理担当持込：入力',day)
    return 200,{'day':day,'version':version,'rows':new_rows}

def manual_listed(payload):
    return bool(payload.get('pickingManual')) and bool(payload.get('pickingListed')) and not (payload.get('pickingListNumber') or '')

def imported(c,u,source,can_access):
    if not isinstance(source,dict) or type(source.get('id')) is not int: raise ValueError('指示書を選択してください。')
    row=c.execute('SELECT * FROM orders WHERE id=?',(source['id'],)).fetchone()
    if not row: raise ValueError('指示書が見つかりません。')
    d=json.loads(row['payload'])
    if not can_access(u,d): raise ValueError('この指示書を取り込む権限がありません。')
    if d.get('status')=='下書き' or d.get('sample'): raise ValueError('下書き・サンプルは取り込めません。')
    if manual_listed(d): raise ValueError('この指示書は主管理者がピッキングリスト済にしています。取り込み対象から外れています。')
    if d.get('status')!='未着手': raise ValueError('未着手の指示書だけ取り込めます。作業状況が変わった場合は、対象を読み直してください。')
    if source.get('version')!=row['version']: raise ValueError('指示書が更新されました。対象を読み直してください。')
    person=c.execute('SELECT name FROM users WHERE id=?',(d.get('assigneeId'),)).fetchone()
    name=person['name'] if person else d.get('owner','')
    rows=[]
    for i,x in enumerate(d.get('items',[])):
        if not str(x.get('name','')).strip(): continue
        v={k:x.get(k,'') for k in FIELDS if k!='note'}
        strip_list_row(v)
        v.update(key=f"order:{row['id']}:{i}",sourceNumber=instruction_number(row['id'], d),requester=name,customer=d.get('customer',''),stock=stock(x.get('stock')),stockOriginal=x.get('stock',''))
        rows.append(v)
    if not rows: raise ValueError('商品明細のない指示書は取り込めません。')
    return {'id':row['id'],'version':row['version'],'number':instruction_number(row['id'], d),'customer':d.get('customer',''),'rows':rows}

def validate_rows(rows,final):
    for n,r in enumerate(rows,1):
        if r.get('stock') not in ('',*GROUPS): raise ValueError('在庫場所を選択してください。')
        for k in FIELDS:
            if k=='note': continue
            if k!='quantity': r[k]=text(r.get(k,''),1000 if k=='note2' else 300)
        strip_list_row(r)
        q=r.get('quantity','')
        if q in ('',None): r['quantity']=''
        elif isinstance(q,bool) or not re.fullmatch(r'[1-9][0-9]{0,4}',str(q)): raise ValueError(f'{n}行目：数量は1～99999の整数で入力してください。')
        else: r['quantity']=int(q)
        if final:
            for k,label in [('requester','依頼者・確認先'),('customer','顧客名'),('name','商品名'),('quantity','数量'),('stock','在庫場所')]:
                if not r.get(k): raise ValueError(f'{n}行目：{label}を入力してください。')
    if final and not rows: raise ValueError('明細を1行以上入力してください。石狩倉庫・発寒支店・管理担当持込のいずれかに明細が必要です。')

def thunderbird():
    keys=((winreg.HKEY_CURRENT_USER,r'Software\Clients\Mail\Mozilla Thunderbird\shell\open\command'),(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Clients\Mail\Mozilla Thunderbird\shell\open\command'),(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\WOW6432Node\Clients\Mail\Mozilla Thunderbird\shell\open\command'))
    for root,name in keys:
        try:
            with winreg.OpenKey(root,name) as key: command=winreg.QueryValue(key,'')
        except OSError: continue
        match=re.match(r'"([^"]+)"',command) or re.match(r'(\S+)',command)
        if match and Path(match.group(1)).is_file(): return match.group(1)
    fallback=Path(r'C:\Program Files\Mozilla Thunderbird\thunderbird.exe')
    if fallback.is_file(): return str(fallback)
    raise ValueError('このパソコンのThunderbirdが見つかりません。')

def start_unstarted(c,u,sources,now,can_access):
    changed=[]; ids=[]; seen=set()
    for source in sources or []:
        oid=source.get('id') if isinstance(source,dict) else None
        if type(oid) is not int or oid in seen: continue
        seen.add(oid)
        row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
        if not row: continue
        payload=json.loads(row['payload'])
        if not can_access(u,payload) or payload.get('status')!='未着手': continue
        payload['status']='作業中'
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),oid))
        changes={'status':{'before':'未着手','after':'作業中'}}
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(oid,now,'未着手 → 作業中',u['id'],f"{u['name']} ({u['login']})",json.dumps(changes,ensure_ascii=False)))
        changed.append(instruction_number(oid, payload)); ids.append(oid)
    return changed, ids

def revert_started(c,u,before,now,can_access):
    if not before.get('sentAt'): return []
    ids=set(before.get('startedOrderIds') or [])
    sources=[s for s in before.get('sources') or [] if isinstance(s,dict) and s.get('id') in ids]
    return shift_orders(c,u,sources,'作業中','未着手',now,can_access)

def shift_orders(c,u,sources,before_status,after_status,now,can_access):
    changed=[]; seen=set()
    for source in sources or []:
        oid=source.get('id') if isinstance(source,dict) else None
        if type(oid) is not int or oid in seen: continue
        seen.add(oid)
        row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
        if not row: continue
        payload=json.loads(row['payload'])
        if not can_access(u,payload) or payload.get('status')!=before_status: continue
        payload['status']=after_status
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),oid))
        changes={'status':{'before':before_status,'after':after_status}}
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(oid,now,f'{before_status} → {after_status}',u['id'],f"{u['name']} ({u['login']})",json.dumps(changes,ensure_ascii=False)))
        changed.append(instruction_number(oid, payload))
    return changed

def tune_flags(c,u,d,can_access):
    return 400,{'error':'リストの削除は「削除」を使ってください。'}

def send_mail(c,u,d,can_access):
    if type(d.get('id')) is not int: raise ValueError('リストを選択してください。')
    row=c.execute('SELECT * FROM picking_lists WHERE id=?',(d['id'],)).fetchone()
    if not row or not visible(u,row): return 404,{'error':'リストが見つかりません。'}
    if not editable(u,row): return 403,{'error':'このリストを編集する権限がありません。'}
    if row['version']!=d.get('version'): return 409,{'error':'別の画面で更新されています。この画面の内容は残しています。操作は実行していません。一覧から開き直してください。'}
    value=record(row,u)
    if value['status']!='確定': return 400,{'error':'依頼を保存してからメールを開いてください。'}
    if value.get('sentAt'): return 400,{'error':'送信確認済みの依頼は再送信できません。'}
    if value.get('status')=='取消' or value.get('arrangedAt'): return 400,{'error':'手配完了済み・削除済みのリストではメールを作成できません。'}
    kind=arrange_kind(value)
    if kind!='mail': return 400,{'error':'石狩倉庫の明細がないため、倉庫向けメールは作成しません。' if kind=='local' else '明細を1行以上入力してください。石狩倉庫・発寒支店・管理担当持込のいずれかに明細が必要です。'}
    to=text(d.get('to',''),80)
    if not re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',to): raise ValueError('宛先はメールアドレスを80文字以内で入力してください。')
    day=text(value.get('date',''),10); when=date.fromisoformat(day)
    if not re.fullmatch(r'PK-\d{6}',value['number']): raise ValueError('リスト番号を確認してください。')
    MAIL_DIR.mkdir(parents=True,exist_ok=True)
    cutoff=datetime.now().timestamp()-7*86400
    for old in MAIL_DIR.glob('*.xlsx'):
        try:
            if old.stat().st_mtime<cutoff: old.unlink()
        except OSError: pass
    path=MAIL_DIR/f"{value['number']}_{day}_v{row['version']}.xlsx"
    path.write_bytes(workbook(value))
    subject=f"【ピッキング依頼】{when:%Y年%m月%d日}／{value['number']}"
    body=f"石狩倉庫 ご担当者様　お疲れ様です。{when:%Y年%m月%d日}のピッキングリスト（{value['number']}）を添付いたします。添付ファイルの「石狩倉庫」シートをご確認のうえ、商品のピッキングをお願いいたします。商品・サイズ・数量などに不明点がございましたら、各明細の「依頼者・確認先」へご確認ください。欠品等によりご用意が難しい場合も、該当の依頼者へご連絡をお願いいたします。よろしくお願いいたします。"
    attachment=path.as_uri()
    if any("'" in part or '\n' in part or '\r' in part for part in (to,subject,body,attachment)): raise ValueError('メールの内容を確認してください。')
    compose=f"to='{to}',subject='{subject}',body='{body}',attachment='{attachment}'"
    now=datetime.now().isoformat(timespec='seconds')
    stored=json.loads(row['payload'])
    first=not stored.get('mailedAt')
    if first:
        stored['mailedAt']=now; stored['mailedBy']=u['name']
    stored.pop('mailNeedsRecreate',None)
    started, ids=start_unstarted(c,u,stored.get('sources',[]),now,can_access)
    stored['sentAt']=now; stored['sentBy']=u['name']; stored['arrangedAt']=now; stored['arrangedBy']=u['name']; stored['arrangeMethod']='メール送信'; stored['startedOrderIds']=ids
    new_payload=json.dumps(stored,ensure_ascii=False)
    c.execute('UPDATE picking_lists SET version=version+1,payload=? WHERE id=?',(new_payload,row['id']))
    note=f'。未着手の指示書{len(started)}件を作業中にしました' if started else '。作業中に変える未着手の指示書はありませんでした'
    c.execute('INSERT INTO picking_history(list_id,at,actor,action,before_json,after_json) VALUES(?,?,?,?,?,?)',(row['id'],now,u['name'],f'Thunderbirdで送信画面を開いた：{to}{note}',row['payload'],new_payload))
    log(c,u,'ピッキングリスト：メール画面',f"{value['number']} {to}")
    try: subprocess.Popen([thunderbird(),'-compose',compose])
    except OSError: raise ValueError('Thunderbirdを開けませんでした。')
    fresh=record(c.execute('SELECT * FROM picking_lists WHERE id=?',(row['id'],)).fetchone(),u)
    return 200,{**fresh,'mailOpened':True,'firstMail':first,'started':started}

def confirm_sent(c,u,d,can_access):
    if type(d.get('id')) is not int: raise ValueError('リストを選択してください。')
    row=c.execute('SELECT * FROM picking_lists WHERE id=?',(d['id'],)).fetchone()
    if not row or not visible(u,row): return 404,{'error':'リストが見つかりません。'}
    if not editable(u,row): return 403,{'error':'このリストを編集する権限がありません。'}
    stored=json.loads(row['payload'])
    if stored.get('sentAt') or stored.get('arrangedAt'): return 200,{**record(row,u),'already':True}
    if row['version']!=d.get('version'): return 409,{'error':'別の画面で更新されています。この画面の内容は残しています。操作は実行していません。一覧から開き直してください。'}
    if stored.get('status')=='取消': return 400,{'error':'削除済みのリストは記録できません。'}
    if stored.get('status')!='確定': return 400,{'error':'依頼を保存してから記録してください。'}
    if arrange_kind(stored)!='mail': return 400,{'error':'石狩倉庫の明細がないリストは、メールの送信記録では完了できません。'}
    if not stored.get('mailedAt'): return 400,{'error':'先にThunderbirdで依頼メールを開いてください。'}
    now=datetime.now().isoformat(timespec='seconds')
    started, ids=start_unstarted(c,u,stored.get('sources',[]),now,can_access)
    stored['sentAt']=now; stored['sentBy']=u['name']; stored['arrangedAt']=now; stored['arrangedBy']=u['name']; stored['arrangeMethod']='メール送信'; stored['startedOrderIds']=ids
    new_payload=json.dumps(stored,ensure_ascii=False)
    c.execute('UPDATE picking_lists SET version=version+1,payload=? WHERE id=?',(new_payload,row['id']))
    action='送信したことを記録'+(f'。未着手の指示書{len(started)}件を作業中にしました' if started else '')
    c.execute('INSERT INTO picking_history(list_id,at,actor,action,before_json,after_json) VALUES(?,?,?,?,?,?)',(row['id'],now,u['name'],action,row['payload'],new_payload))
    log(c,u,'ピッキングリスト：送信確認',f"PK-{row['id']:06}")
    fresh=record(c.execute('SELECT * FROM picking_lists WHERE id=?',(row['id'],)).fetchone(),u)
    return 200,{**fresh,'started':started}

def confirm_arranged(c,u,d,can_access):
    if type(d.get('id')) is not int: raise ValueError('リストを選択してください。')
    row=c.execute('SELECT * FROM picking_lists WHERE id=?',(d['id'],)).fetchone()
    if not row or not visible(u,row): return 404,{'error':'リストが見つかりません。'}
    if not editable(u,row): return 403,{'error':'このリストを編集する権限がありません。'}
    stored=json.loads(row['payload'])
    if stored.get('arrangedAt') or stored.get('sentAt'): return 200,{**record(row,u),'already':True}
    if row['version']!=d.get('version'): return 409,{'error':'別の画面で更新されています。この画面の内容は残しています。操作は実行していません。一覧から開き直してください。'}
    if stored.get('status')=='取消': return 400,{'error':'削除済みのリストは記録できません。'}
    if stored.get('status')!='確定': return 400,{'error':'依頼を保存してから記録してください。'}
    kind=arrange_kind(stored)
    if kind=='mail': return 400,{'error':'石狩倉庫の明細があるため、メールで依頼してから送信を記録してください。'}
    if kind!='local': return 400,{'error':'明細を1行以上入力してください。石狩倉庫・発寒支店・管理担当持込のいずれかに明細が必要です。'}
    now=datetime.now().isoformat(timespec='seconds')
    started, ids=start_unstarted(c,u,stored.get('sources',[]),now,can_access)
    stored['arrangedAt']=now; stored['arrangedBy']=u['name']; stored['arrangeMethod']='メール不要の手配'; stored['startedOrderIds']=ids
    new_payload=json.dumps(stored,ensure_ascii=False)
    c.execute('UPDATE picking_lists SET version=version+1,payload=? WHERE id=?',(new_payload,row['id']))
    action='手配したことを記録'+(f'。未着手の指示書{len(started)}件を作業中にしました' if started else '')
    c.execute('INSERT INTO picking_history(list_id,at,actor,action,before_json,after_json) VALUES(?,?,?,?,?,?)',(row['id'],now,u['name'],action,row['payload'],new_payload))
    log(c,u,'ピッキングリスト：手配完了',f"PK-{row['id']:06}")
    fresh=record(c.execute('SELECT * FROM picking_lists WHERE id=?',(row['id'],)).fetchone(),u)
    return 200,{**fresh,'started':started}

BOARD_PAGE=20

def sheet_item(row, payload):
    return {'id':row['id'],'version':row['version'],'number':instruction_number(row['id'], payload),'customer':payload.get('customer',''),'employee':payload.get('employee',''),'owner':payload.get('owner',''),'date':payload.get('markShipDate') or '','status':payload.get('status','')}

def list_bucket(payload):
    if payload.get('status')=='取消': return 'cancelled'
    if finished(payload): return 'sent'
    return 'waiting'

def list_brief(r,u,payload):
    rows=payload.get('rows') or []
    bits=[payload.get('title',''), f"PK-{r['id']:06}", payload.get('date','')]
    for row in rows:
        if isinstance(row,dict): bits.extend([str(row.get('customer') or ''), str(row.get('requester') or '')])
    method=payload.get('arrangeMethod') or ('メール送信' if payload.get('sentAt') else ('メール不要の手配' if payload.get('arrangedAt') else ''))
    return {'id':r['id'],'version':r['version'],'number':f"PK-{r['id']:06}",'title':payload.get('title',''),'date':payload.get('date',''),'lines':len(rows),'phase':phase(payload),'status':payload.get('status',''),'mailedAt':payload.get('mailedAt') or '','mailedBy':payload.get('mailedBy') or '','sentAt':payload.get('sentAt') or '','sentBy':payload.get('sentBy') or '','arrangedAt':payload.get('arrangedAt') or '','arrangedBy':payload.get('arrangedBy') or '','arrangeMethod':method,'updatedBy':payload.get('updatedBy') or payload.get('createdBy') or '','updatedAt':payload.get('updatedAt') or '','canEdit':editable(u,r),'_search':' '.join(bits).lower()}

def sheet_has_lines(payload):
    return any(isinstance(x,dict) and str(x.get('name') or '').strip() for x in (payload.get('items') or []))

def eligible_sheets(c,u,can_access):
    used={r['order_id'] for r in c.execute('SELECT order_id FROM picking_sources')}
    sheets=[]
    for r in c.execute('SELECT * FROM orders ORDER BY id DESC'):
        payload=json.loads(r['payload'])
        if can_access(u,payload) and payload.get('status')=='未着手' and not payload.get('sample') and r['id'] not in used and not manual_listed(payload) and sheet_has_lines(payload):
            sheets.append(sheet_item(r, payload))
    sheets.sort(key=lambda s: (s['date'] or '9999-99-99', s['number']))
    return sheets

def request_label(payload):
    if payload.get('status')=='確定': return 'ピッキングリスト済'
    return ''

def request_links(c):
    used={r['order_id']:r['list_id'] for r in c.execute('SELECT order_id,list_id FROM picking_sources')}
    if not used: return {}
    lists={r['id']:r['payload'] for r in c.execute('SELECT id,payload FROM picking_lists')}
    out={}
    for oid,lid in used.items():
        raw=lists.get(lid)
        if not raw: continue
        payload=json.loads(raw)
        label=request_label(payload)
        if not label: continue
        out[oid]={'pickingRequest':label,'pickingListed':True,'pickingListNumber':f'PK-{lid:06}','pickingListId':lid,'pickingManual':False}
    return out

def order_extra(order_id, payload, links):
    extra=links.get(order_id)
    if extra: return {**extra,'pickingManual':False}
    if manual_listed(payload):
        return {'pickingRequest':'ピッキングリスト済','pickingListed':True,'pickingListNumber':'','pickingListId':None,'pickingManual':True}
    return {'pickingRequest':'','pickingListed':False,'pickingListNumber':'','pickingListId':None,'pickingManual':False}

def apply_picking_flags(c,u,on_ids,off_ids,number):
    now=datetime.now().isoformat(timespec='seconds')
    actor_id=u.get('id') if isinstance(u,dict) else None
    actor=f"{u['name']} ({u['login']})" if isinstance(u,dict) and u.get('login') else (u.get('name') if isinstance(u,dict) else 'システム更新')
    def write(oid,on):
        row=c.execute('SELECT id,payload FROM orders WHERE id=?',(oid,)).fetchone()
        if not row: return
        payload=json.loads(row['payload'])
        current=bool(payload.get('pickingListed'))
        current_no=payload.get('pickingListNumber') or ''
        manual=bool(payload.get('pickingManual'))
        if on and current and current_no==number and not manual: return
        if not on and not current and not current_no and not manual: return
        if on:
            payload['pickingListed']=True
            payload['pickingListNumber']=number
            payload.pop('pickingManual',None)
            payload.pop('pickingListId',None)
            action='ピッキングリスト済'
            changes={'ピッキングリスト済':{'before':current_no or ('主管理者による手動設定' if manual else ''),'after':number}}
        else:
            payload.pop('pickingListed',None)
            payload.pop('pickingListNumber',None)
            payload.pop('pickingListId',None)
            payload.pop('pickingManual',None)
            action='ピッキングリスト済を外した'
            changes={'ピッキングリスト済':{'before':current_no or ('主管理者による手動設定' if manual else '済'),'after':''}}
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),oid))
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(oid,now,action,actor_id,actor,json.dumps(changes,ensure_ascii=False)))
    for oid in on_ids:
        if type(oid) is int: write(oid,True)
    off=set(off_ids)-set(on_ids)
    for oid in off:
        if type(oid) is int: write(oid,False)

def backfill_picking_flags(c):
    used={r['order_id']:r['list_id'] for r in c.execute('SELECT order_id,list_id FROM picking_sources')}
    now=datetime.now().isoformat(timespec='seconds')
    for row in c.execute('SELECT id,payload FROM orders'):
        payload=json.loads(row['payload'])
        lid=used.get(row['id'])
        if lid:
            owner_list=c.execute('SELECT payload FROM picking_lists WHERE id=?',(lid,)).fetchone()
            if owner_list and json.loads(owner_list['payload']).get('status')=='取消': continue
        number=f'PK-{lid:06}' if lid else ''
        listed=bool(lid)
        current=bool(payload.get('pickingListed'))
        current_no=payload.get('pickingListNumber') or ''
        manual=manual_listed(payload)
        if not listed and manual: continue
        if listed and current and current_no==number and not payload.get('pickingManual'): continue
        if not listed and not current and not current_no and not payload.get('pickingManual'): continue
        if listed:
            payload['pickingListed']=True
            payload['pickingListNumber']=number
            payload.pop('pickingManual',None)
            payload.pop('pickingListId',None)
            action='ピッキングリスト済'
            changes={'ピッキングリスト済':{'before':current_no or ('主管理者による手動設定' if manual else ''),'after':number}}
        else:
            payload.pop('pickingListed',None)
            payload.pop('pickingListNumber',None)
            payload.pop('pickingListId',None)
            payload.pop('pickingManual',None)
            action='ピッキングリスト済を外した'
            changes={'ピッキングリスト済':{'before':current_no or '済','after':''}}
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
        c.execute('INSERT INTO history(order_id,at,action,actor,changes) VALUES(?,?,?,?,?)',(row['id'],now,action,'システム更新',json.dumps(changes,ensure_ascii=False)))

def candidate_rows(c,u,can_access,q,skip):
    rows=eligible_sheets(c,u,can_access)
    rows=[s for s in rows if s['id'] not in skip]
    if q: rows=[s for s in rows if q in ' '.join([s['number'],s['customer'],s['owner'],s['date']]).lower()]
    return rows

def candidate_view(c,u,d,can_access):
    d=d or {}
    q=text(d.get('q') or '',80).lower()
    page=d.get('page') or 1
    if type(page) is not int or page<1 or page>10000: raise ValueError('ページを確認してください。')
    raw=d.get('except') or []
    if not isinstance(raw,list) or len(raw)>100 or any(type(i) is not int for i in raw): raise ValueError('取り込み済みの指示書を確認してください。')
    base=candidate_rows(c,u,can_access,'',set(raw))
    shown=[s for s in base if not q or q in ' '.join([s['number'],s['customer'],s['owner'],s['date']]).lower()]
    if d.get('ids'):
        if len(base)>100: return 200,{'total':len(base),'items':[],'limited':True,'scopeTotal':len(base)}
        return 200,{'total':len(base),'items':[{'id':s['id'],'version':s['version'],'number':s['number']} for s in base],'scopeTotal':len(base)}
    total=len(shown)
    pages=max(1,(total+BOARD_PAGE-1)//BOARD_PAGE)
    if page>pages: page=pages
    offset=(page-1)*BOARD_PAGE
    result={'items':shown[offset:offset+BOARD_PAGE],'total':total,'page':page,'pages':pages,'pageSize':BOARD_PAGE,'q':q,'scopeTotal':len(base)}
    if d.get('scope'): result['scope']=[{'id':s['id'],'version':s['version'],'number':s['number'],'customer':s['customer']} for s in base]
    return 200,result

def gather_sources(c,u,d,can_access):
    raw=d.get('items')
    if not isinstance(raw,list): raise ValueError('対象の指示書を確認してください。')
    if len(raw)>100:
        return 400,{'error':f'1つのリストに入れられる指示書は100件までです。送られた対象は{len(raw)}件です。先頭の一部は取り込んでいません。{len(raw)-100}件は別のリストにしてください。'}
    added=[]; failed=[]; seen=set()
    for item in raw:
        if not isinstance(item,dict) or type(item.get('id')) is not int:
            failed.append({'id':None,'number':'','reason':'指示書を確認してください。'})
            continue
        oid=item['id']
        if oid in seen:
            failed.append({'id':oid,'number':'','reason':'同じ指示書が重複しています。'})
            continue
        seen.add(oid)
        number=''
        try:
            row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
            if row: number=instruction_number(row['id'], json.loads(row['payload']))
            source=imported(c,u,item,can_access)
            number=source['number']
            if c.execute('SELECT 1 FROM picking_sources WHERE order_id=?',(source['id'],)).fetchone():
                raise ValueError('この指示書はすでに別のリストに取り込まれています。')
            added.append(source)
        except ValueError as e:
            failed.append({'id':oid,'number':number,'reason':str(e)})
    return 200,{'added':added,'failed':failed,'addedCount':len(added),'failedCount':len(failed)}

def board_view(c,u,d,can_access):
    bucket=d.get('bucket') or ''
    if bucket=='drafts': bucket='waiting'  # Older bookmarks use the unified saved list.
    if bucket=='cancelled': bucket='waiting'  # Deleted lists stay out of the normal board.
    if bucket not in ('','sheets','drafts','waiting','sent','cancelled'): raise ValueError('表示を確認してください。')
    q=text(d.get('q') or '',80).lower()
    start=text(d.get('from') or '',10)
    end=text(d.get('to') or '',10)
    if start: date.fromisoformat(start)
    if end: date.fromisoformat(end)
    if start and end and start>end: raise ValueError('マーク出荷日の開始は、終了以前にしてください。')
    page=d.get('page') or 1
    if type(page) is not int or page<1 or page>10000: raise ValueError('ページを確認してください。')
    sheets=eligible_sheets(c,u,can_access)
    visible_lists=[]
    for r in c.execute('SELECT * FROM picking_lists ORDER BY id DESC'):
        if visible(u,r): visible_lists.append((r, json.loads(r['payload'])))
    counts={'sheets':len(sheets),'drafts':0,'waiting':0,'sent':0,'cancelled':0}
    for _,payload in visible_lists: counts[list_bucket(payload)]+=1
    if not bucket:
        bucket='drafts' if counts['drafts'] else 'waiting' if counts['waiting'] else 'sheets'
    if bucket=='sheets':
        rows=sheets
        if q: rows=[s for s in rows if q in ' '.join([s['number'],s['customer'],s['employee'],s['owner'],s['date']]).lower()]
        if start or end: rows=[s for s in rows if s['date'] and (not start or s['date']>=start) and (not end or s['date']<=end)]
    else:
        rows=[]
        for r,payload in visible_lists:
            if list_bucket(payload)!=bucket: continue
            item=list_brief(r,u,payload)
            if q and q not in item['_search']: continue
            item.pop('_search',None)
            rows.append(item)
    total=len(rows)
    pages=max(1,(total+BOARD_PAGE-1)//BOARD_PAGE)
    if page>pages: page=pages
    offset=(page-1)*BOARD_PAGE
    return 200,{'counts':counts,'bucket':bucket,'page':page,'pages':pages,'pageSize':BOARD_PAGE,'total':total,'from':start,'to':end,'q':q,'items':rows[offset:offset+BOARD_PAGE]}

def history_view(c,u,d):
    kind=d.get('kind') or ''
    if kind=='cancelled': raise ValueError('削除したリストは一覧に出しません。')
    if kind not in ('','waiting','sent'): raise ValueError('状態を確認してください。')
    q=text(d.get('q') or '',80).lower()
    start=text(d.get('from') or '',10)
    end=text(d.get('to') or '',10)
    if start: date.fromisoformat(start)
    if end: date.fromisoformat(end)
    if start and end and start>end: raise ValueError('依頼日の開始は、終了以前にしてください。')
    page=d.get('page') or 1
    if type(page) is not int or page<1 or page>10000: raise ValueError('ページを確認してください。')
    counts={'waiting':0,'sent':0}
    rows=[]
    for r in c.execute('SELECT * FROM picking_lists'):
        if not visible(u,r): continue
        payload=json.loads(r['payload'])
        bucket=list_bucket(payload)
        if bucket not in ('waiting','sent'): continue
        counts[bucket]+=1
        if kind and bucket!=kind: continue
        day=payload.get('date') or ''
        if (start or end) and (not day or (start and day<start) or (end and day>end)): continue
        item=list_brief(r,u,payload)
        if q and q not in item['_search']: continue
        item.pop('_search',None)
        rows.append(item)
    rows.sort(key=lambda x: (x['date'] or '0000-00-00', x['id']))
    rows.reverse()
    total=len(rows)
    pages=max(1,(total+BOARD_PAGE-1)//BOARD_PAGE)
    if page>pages: page=pages
    offset=(page-1)*BOARD_PAGE
    return 200,{'counts':counts,'kind':kind,'page':page,'pages':pages,'pageSize':BOARD_PAGE,'total':total,'from':start,'to':end,'q':q,'items':rows[offset:offset+BOARD_PAGE]}

def reopen_list(c,u,d):
    if type(d.get('id')) is not int: raise ValueError('リストを選択してください。')
    row=c.execute('SELECT * FROM picking_lists WHERE id=?',(d['id'],)).fetchone()
    if not row or not visible(u,row): return 404,{'error':'リストが見つかりません。'}
    if not editable(u,row): return 403,{'error':'このリストを編集する権限がありません。'}
    if row['version']!=d.get('version'): return 409,{'error':'別の画面で更新されています。この画面の内容は残しています。操作は実行していません。一覧から開き直してください。'}
    before=json.loads(row['payload'])
    if finished(before) or before.get('status')=='取消': return 400,{'error':'削除済み・手配完了済みの依頼は編集できません。'}
    return 200,record(row,u)

def source_link(c, oid):
    row=c.execute('SELECT list_id FROM picking_sources WHERE order_id=?',(oid,)).fetchone()
    if not row: return None
    lst=c.execute('SELECT id,payload FROM picking_lists WHERE id=?',(row['list_id'],)).fetchone()
    if not lst: return {'id':row['list_id'],'number':'','phase':'','status':'','sentAt':'','mailedAt':'','title':'','deleted':True,'missing':True}
    payload=json.loads(lst['payload'])
    return {'id':lst['id'],'number':f"PK-{lst['id']:06}",'phase':phase(payload),'status':payload.get('status') or '','sentAt':payload.get('sentAt') or '','mailedAt':payload.get('mailedAt') or '','title':payload.get('title') or '','deleted':payload.get('status')=='取消','missing':False}

TEST_RESET_WARNING='この操作はテストをやり直すためのものです。送信したメールや、倉庫にある添付Excelは取り消せません。実際の倉庫へ依頼したリストには使用しないでください。'
_SAFE_LATER_ACTIONS=frozenset({
    '印刷','印刷済','印刷済を外した',
    'ピッキングリスト済','ピッキングリスト済を外した',
    'ピッキングリスト済（主管理者による手動設定）','ピッキングリスト済を外した（主管理者）',
    'テスト用リセットでピッキングリスト済を外した','テスト用リセットを実施した',
})

def _list_number(list_id):
    return f'PK-{int(list_id):06}'

def _changes(raw):
    try: value=json.loads(raw or '{}')
    except json.JSONDecodeError: return None
    return value if isinstance(value,dict) else None

def _status_decision(c, oid, payload, sent_at, started_ids, revert):
    status=payload.get('status') or ''
    if not revert:
        note='作業状況は変えません。'
        if status!='未着手': note+='未着手ではないため、リセット後も取り込み対象にはなりません。'
        else: note+='現在未着手なので、取り込み関係を外せば再び対象になります。'
        return {'revert':False,'reason':note}
    if oid not in set(started_ids or []):
        return {'revert':False,'reason':'このリストの手配完了で作業中にした記録がないため、戻しません。'}
    if not sent_at:
        return {'revert':False,'reason':'手配完了の日時がないため、戻しません。'}
    if status=='発送済': return {'revert':False,'reason':'現在発送済のため戻しません。'}
    if status=='完了': return {'revert':False,'reason':'現在完了のため戻しません。'}
    if status!='作業中': return {'revert':False,'reason':f'現在{status or "未設定"}のため戻しません。作業中のまま残っているものだけが対象です。'}
    start=None
    rows=list(c.execute('SELECT id,at,action,changes FROM history WHERE order_id=? ORDER BY id',(oid,)))
    for item in rows:
        if item['at']!=sent_at or item['action']!='未着手 → 作業中': continue
        moved=(_changes(item['changes']) or {}).get('status') or {}
        if moved.get('before')=='未着手' and moved.get('after')=='作業中': start=item
    if not start:
        return {'revert':False,'reason':'手配完了で未着手から作業中になった記録を確認できないため、戻しません。'}
    for item in rows:
        if item['id']<=start['id'] or item['action'] in _SAFE_LATER_ACTIONS: continue
        return {'revert':False,'reason':f"その後に別の記録があります（{item['at']} {item['action']}）。安全のため戻しません。"}
    return {'revert':True,'reason':'手配完了で未着手から作業中になり、その後の作業進行や状態変更は見当たりません。未着手に戻します。'}

def _sheet_release(c, list_id, number, oid):
    row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    link=source_link(c, oid)
    if not row:
        return {'found':False,'releaseSource':False,'clearFlag':False,'conflict':True,'reason':'指示書が見つかりません。関係は外しません。','row':None,'payload':{}}
    payload=json.loads(row['payload'])
    flag_no=payload.get('pickingListNumber') or ''
    manual=manual_listed(payload)
    ours=bool(link) and link.get('id')==list_id
    other=bool(link) and link.get('id')!=list_id and not link.get('deleted') and not link.get('missing')
    base={'found':True,'row':row,'payload':payload,'other':link.get('number') if other else ''}
    if other:
        return {**base,'releaseSource':False,'clearFlag':False,'conflict':True,'reason':f"別の有効なリスト {link['number']} に入っています。関係もフラグも外しません。"}
    if ours and manual and flag_no!=number:
        return {**base,'releaseSource':False,'clearFlag':False,'conflict':True,'reason':'このリストの取り込み関係と、手動のピッキングリスト済が同時にあります。勝手に外しません。'}
    if ours and flag_no and flag_no!=number:
        return {**base,'releaseSource':False,'clearFlag':False,'conflict':True,'reason':f'フラグは {flag_no} のままです。このリストの番号と違うため、関係は外しません。'}
    if ours:
        return {**base,'releaseSource':True,'clearFlag':flag_no==number or bool(payload.get('pickingListed')),'conflict':False,'reason':''}
    if flag_no==number:
        return {**base,'releaseSource':False,'clearFlag':True,'conflict':False,'reason':''}
    if manual:
        return {**base,'releaseSource':False,'clearFlag':False,'conflict':False,'reason':'主管理者による手動設定のため、フラグは外しません。'}
    return {**base,'releaseSource':False,'clearFlag':False,'conflict':False,'reason':'このリストとの有効な取り込み関係はありません。'}

def _carry_links(c, list_id):
    found=[]
    for row in c.execute('SELECT day,version,payload FROM carry_inputs'):
        for item in json.loads(row['payload']).get('rows') or []:
            if item.get('listId')!=list_id: continue
            found.append({'day':row['day'],'version':row['version'],'key':item.get('key') or '','customer':item.get('customer') or '','name':item.get('name') or ''})
    return found

def test_reset_plan(c, row, revert):
    payload=json.loads(row['payload'])
    number=_list_number(row['id'])
    already=payload.get('status')=='取消'
    ids=[]
    for source in payload.get('sources') or []:
        if isinstance(source,dict) and type(source.get('id')) is int and source['id'] not in ids: ids.append(source['id'])
    for item in c.execute('SELECT order_id FROM picking_sources WHERE list_id=?',(row['id'],)):
        if item['order_id'] not in ids: ids.append(item['order_id'])
    started=[i for i in (payload.get('startedOrderIds') or []) if type(i) is int]
    for oid in started:
        if oid not in ids: ids.append(oid)
    sheets=[]; remove=[]; restore=[]; skipped=[]
    for oid in ids:
        rel=_sheet_release(c, row['id'], number, oid)
        live=rel['payload']
        label=instruction_number(oid, live) if rel['found'] else f'指示書ID {oid}'
        decision=_status_decision(c, oid, live, completion_at(payload), started, revert) if rel['found'] else {'revert':False,'reason':'指示書が見つかりません。作業状況は変えません。'}
        if rel.get('conflict') and decision['revert']:
            decision={'revert':False,'reason':rel['reason']+'作業状況も戻しません。'}
        if already:
            rel={**rel,'releaseSource':False,'clearFlag':False}
            decision={'revert':False,'reason':'すでに削除扱いです。状態は変えません。'}
        customer=(live.get('customer') if rel['found'] else '') or ''
        status=(live.get('status') if rel['found'] else '') or ''
        if rel['releaseSource']: remove.append(f'{label} の取り込み関係を外します。')
        if rel['clearFlag']: remove.append(f'{label} のピッキングリスト済を外します。')
        if decision['revert']: restore.append(f'{label} を作業中から未着手に戻します。')
        else:
            bits=[]
            if rel['reason']: bits.append(rel['reason'])
            if revert or status!='未着手' or already: bits.append(decision['reason'])
            if bits: skipped.append({'number':label,'customer':customer,'reason':' '.join(dict.fromkeys(bits))})
        sheets.append({'orderId':oid,'version':rel['row']['version'] if rel['row'] else None,'number':label,'customer':customer,'status':status,'releaseSource':rel['releaseSource'],'clearFlag':rel['clearFlag'],'revertStatus':decision['revert'],'releaseText':'取り込み関係を外します。' if rel['releaseSource'] else (rel['reason'] or '取り込み関係はありません。'),'flagText':'ピッキングリスト済を外します。' if rel['clearFlag'] else 'ピッキングリスト済は外しません。','statusText':decision['reason']})
    carry=[] if already else _carry_links(c, row['id'])
    for item in carry: remove.append(f"{item['day']} {item['customer']} {item['name']} の持込の取り込み済みを外します。")
    if not restore: restore.append('作業状況は戻しません。' if not revert or already else '未着手に戻す指示書はありません。')
    keep=['リストの保存内容、メール作成・送信確認・手配完了の記録、これまでの変更履歴は残します。','指示書と管理担当持込の元データは削除しません。','他の有効なリストと、無関係な手動のピッキングリスト済は変えません。','送信したメールと、倉庫にある添付Excelは取り消せません。','削除したリストは復活させません。やり直すときは、新しいリストを作成します。']
    notice='すでに削除扱いのため、再実行しても状態は変えません。' if already else ('作業状況は変更しません。未着手ではない指示書は、再び取り込み対象になりません。' if not revert else '手配完了で未着手から作業中になり、その後の変更がない指示書だけ未着手に戻します。対象外の理由は各指示書に出します。')
    return {'id':row['id'],'version':row['version'],'number':number,'title':payload.get('title') or '','date':payload.get('date') or '','phase':phase(payload),'status':payload.get('status') or '','mailedAt':payload.get('mailedAt') or '','mailedBy':payload.get('mailedBy') or '','sentAt':payload.get('sentAt') or '','sentBy':payload.get('sentBy') or '','arrangedAt':payload.get('arrangedAt') or '','arrangedBy':payload.get('arrangedBy') or '','arrangeMethod':payload.get('arrangeMethod') or ('メール送信' if payload.get('sentAt') else ('メール不要の手配' if payload.get('arrangedAt') else '')),'already':already,'warning':TEST_RESET_WARNING,'statusNotice':notice,'sheets':sheets,'carry':[{'day':x['day'],'version':x['version'],'key':x['key'],'customer':x['customer'],'name':x['name']} for x in carry],'remove':remove or ['外す取り込み関係はありません。'],'restore':restore,'keep':keep,'skipped':skipped}

def test_reset_search(c,u,d):
    if u['role']!='owner': return 403, {'error':'テスト用のリセットは主管理者だけができます。'}
    q=text((d or {}).get('q') or '',40).strip()
    if len(q)<2: raise ValueError('リスト番号を2文字以上で入力してください。')
    needle=q.lower().replace(' ','')
    digits=''.join(ch for ch in needle if ch.isdigit())
    hits=[]
    for r in c.execute('SELECT * FROM picking_lists ORDER BY id DESC'):
        payload=json.loads(r['payload'])
        number=_list_number(r['id'])
        blob=(number+' '+(payload.get('title') or '')).lower().replace(' ','')
        if needle not in blob and not (digits and digits in f"{r['id']:06}"): continue
        hits.append({'id':r['id'],'version':r['version'],'number':number,'title':payload.get('title') or '','date':payload.get('date') or '','phase':phase(payload),'status':payload.get('status') or '','mailedAt':payload.get('mailedAt') or '','sentAt':payload.get('sentAt') or '','arrangedAt':payload.get('arrangedAt') or ''})
        if len(hits)>=20: break
    return 200, {'items':hits}

def test_reset_preview(c,u,d):
    if u['role']!='owner': return 403, {'error':'テスト用のリセットは主管理者だけができます。'}
    if type(d.get('id')) is not int: raise ValueError('リストを選択してください。')
    row=c.execute('SELECT * FROM picking_lists WHERE id=?',(d['id'],)).fetchone()
    if not row: return 404, {'error':'リストが見つかりません。'}
    return 200, test_reset_plan(c, row, d.get('revertStatus') is True)

def _expect_map(items, label):
    found={}
    if items is None: items=[]
    if not isinstance(items,list): raise ValueError(f'{label}を確認し直してください。')
    for item in items:
        if not isinstance(item,dict) or type(item.get('id')) is not int or type(item.get('version')) is not int: raise ValueError('確認画面を開き直してください。操作は実行していません。')
        found[item['id']]=item['version']
    return found

def test_reset_apply(c,u,d):
    if u['role']!='owner': return 403, {'error':'テスト用のリセットは主管理者だけができます。'}
    if type(d.get('id')) is not int: raise ValueError('リストを選択してください。')
    if d.get('acknowledgeTest') is not True: return 400, {'error':'テストデータであることを確認してください。実際の倉庫へ依頼したリストには使用しないでください。'}
    reason=text(d.get('reason') or '',500)
    if not reason: raise ValueError('修正理由を入力してください。')
    row=c.execute('SELECT * FROM picking_lists WHERE id=?',(d['id'],)).fetchone()
    if not row: return 404, {'error':'リストが見つかりません。'}
    before=json.loads(row['payload'])
    number=_list_number(row['id'])
    if before.get('status')=='取消': return 200, {'already':True,'number':number,'message':'すでに削除扱いです。状態は変えていません。'}
    if row['version']!=d.get('version'): return 409, {'error':'別の画面で更新されています。内容を確認し直してください。操作は実行していません。'}
    if text(d.get('confirmNumber') or '',20)!=number: return 400, {'error':'リスト番号が一致しません。対象の番号を入力してください。'}
    revert=d.get('revertStatus') is True
    plan=test_reset_plan(c, row, revert)
    orders=_expect_map(d.get('orders'), '指示書')
    for sheet in plan['sheets']:
        if sheet['version'] is None: continue
        if orders.get(sheet['orderId'])!=sheet['version']: return 409, {'error':'確認画面を開いた後に指示書が更新されています。内容を確認し直してください。操作は実行していません。'}
    d_carry=d.get('carry') or []
    carry={}
    if not isinstance(d_carry,list): raise ValueError('確認画面を開き直してください。操作は実行していません。')
    for item in d_carry:
        if not isinstance(item,dict) or not isinstance(item.get('day'),str) or type(item.get('version')) is not int: raise ValueError('確認画面を開き直してください。操作は実行していません。')
        carry[item['day']]=item['version']
    seen=set()
    for item in plan['carry']:
        if item['day'] in seen: continue
        seen.add(item['day'])
        if carry.get(item['day'])!=item['version']: return 409, {'error':'確認画面を開いた後に管理担当持込が更新されています。内容を確認し直してください。操作は実行していません。'}
    fresh=c.execute('SELECT version,payload FROM picking_lists WHERE id=?',(row['id'],)).fetchone()
    if fresh['version']!=row['version'] or json.loads(fresh['payload']).get('status')=='取消': return 409, {'error':'別の画面で更新されています。内容を確認し直してください。操作は実行していません。'}
    now=datetime.now().isoformat(timespec='seconds')
    actor=f"{u['name']} ({u['login']})"
    released=[]; reverted=[]; skipped=list(plan['skipped'])
    for sheet in plan['sheets']:
        if sheet['releaseSource']:
            c.execute('DELETE FROM picking_sources WHERE order_id=? AND list_id=?',(sheet['orderId'],row['id']))
        if not sheet['clearFlag'] and not sheet['revertStatus']: continue
        current=c.execute('SELECT * FROM orders WHERE id=?',(sheet['orderId'],)).fetchone()
        if not current or current['version']!=sheet['version']: raise ValueError('確認画面を開いた後に指示書が更新されています。内容を確認し直してください。操作は実行していません。')
        payload=json.loads(current['payload'])
        changes={}
        if sheet['clearFlag']:
            before_no=payload.get('pickingListNumber') or ('済' if payload.get('pickingListed') else '')
            payload.pop('pickingListed',None); payload.pop('pickingListNumber',None); payload.pop('pickingListId',None); payload.pop('pickingManual',None)
            changes['ピッキングリスト済']={'before':before_no,'after':''}
            released.append(sheet['number'])
        if sheet['revertStatus']:
            if payload.get('status')!='作業中': raise ValueError('確認画面を開いた後に作業状況が変わっています。内容を確認し直してください。操作は実行していません。')
            changes['作業状況']={'before':'作業中','after':'未着手'}
            payload['status']='未着手'
            reverted.append(sheet['number'])
        changes['理由']={'before':'','after':reason}
        changes['リスト']={'before':number,'after':number}
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),sheet['orderId']))
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(sheet['orderId'],now,'テスト用リセットを実施した',u['id'],actor,json.dumps(changes,ensure_ascii=False)))
    if plan['carry']: mutate_carry(c, [item['key'] for item in plan['carry'] if item.get('key')], row['id'], False)
    report={'released':released,'reverted':reverted,'skipped':skipped,'revertRequested':revert}
    value={**before,'status':'取消','deletedAt':now,'deletedBy':u['name'],'updatedAt':now,'testResetAt':now,'testResetBy':u['name'],'testResetReason':reason,'testResetRevert':revert,'testResetReport':report}
    encoded=json.dumps(value,ensure_ascii=False)
    c.execute('UPDATE picking_lists SET version=version+1,payload=? WHERE id=?',(encoded,row['id']))
    c.execute('INSERT INTO picking_history(list_id,at,actor,action,before_json,after_json) VALUES(?,?,?,?,?,?)',(row['id'],now,u['name'],'テスト用リセットを実施した',row['payload'],encoded))
    log(c,u,'ピッキングリスト：テスト用リセットを実施した',f'{number} {reason}')
    return 200, {'already':False,'number':number,'version':row['version']+1,'message':'テスト用リセットを実施しました。リストは一覧から外し、記録は残しています。','released':released,'reverted':reverted,'skipped':skipped,'warning':TEST_RESET_WARNING}

def handle(h,c,path,d,can_access):
    u=h.session_user(c)
    if not u or u['must_change']: return 401,{'error':'ログインし直してください。'}
    if path.startswith('/api/picking/test-reset'):
        if u['role']!='owner': return 403,{'error':'テスト用のリセットは主管理者だけができます。'}
        if d is None: return 404,{'error':'見つかりません。'}
        if path=='/api/picking/test-reset/search': return test_reset_search(c,u,d)
        if path=='/api/picking/test-reset/preview': return test_reset_preview(c,u,d)
        if path=='/api/picking/test-reset': return test_reset_apply(c,u,d)
        return 404,{'error':'見つかりません。'}
    if path.startswith('/api/carry-input'):
        if u['role'] not in ('owner','floor'): return 403,{'error':'管理担当持込の入力は現場と主管理者が使用します。'}
        return carry_input(c,u,path,d or {})
    if u['role'] not in ('owner','floor','sales'): return 403,{'error':'この操作はできません。'}
    if path in ('/api/picking/carry-day','/api/picking/carry-check','/api/picking/carry-print','/api/picking/carry-month') and u['role'] not in ('owner','sales'): return 403,{'error':'管理担当持込の出力は営業と主管理者が使用します。'}
    if u['role']=='sales' and path not in ('/api/picking/carry-day','/api/picking/carry-check','/api/picking/carry-print','/api/picking/carry-month','/api/picking/save','/api/picking/cancel','/api/picking/mail','/api/picking/sent','/api/picking/arrange','/api/picking/reopen','/api/picking') and not re.fullmatch(r'/api/picking/\d+/(history|xlsx)',path):
        return 403,{'error':'管理担当持込の出力は営業と主管理者が使用します。'}
    if d is None:
        if path=='/api/picking':
            return 200,[row for row in (record(r,u) for r in c.execute('SELECT * FROM picking_lists ORDER BY id DESC') if visible(u,r)) if row.get('status')!='取消']
        one=re.fullmatch(r'/api/picking/(\d+)',path)
        if one:
            r=c.execute('SELECT * FROM picking_lists WHERE id=?',(int(one[1]),)).fetchone()
            if not r or not visible(u,r): return 404,{'error':'リストが見つかりません。'}
            return 200,record(r,u)
        if path=='/api/picking/candidates':
            return candidate_view(c,u,{'page':1},can_access)
        match=re.fullmatch(r'/api/picking/(\d+)/(history|xlsx)',path)
        if not match: return 404,{'error':'見つかりません。'}
        r=c.execute('SELECT * FROM picking_lists WHERE id=?',(int(match[1]),)).fetchone()
        if not r or not visible(u,r): return 404,{'error':'リストが見つかりません。'}
        if match[2]=='history':
            return 200,[dict(x) for x in c.execute('SELECT at,actor,action,before_json,after_json FROM picking_history WHERE list_id=? ORDER BY id DESC',(r['id'],))]
        v=record(r,u)
        if v['status']!='確定': return 400,{'error':'依頼を保存してから出力してください。'}
        return 200,workbook(v),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    if path=='/api/picking/carry-check':
        day=text(d.get('date',''),10)
        try: date.fromisoformat(day)
        except ValueError: raise ValueError('日付を確認してください。')
        key=text(d.get('key',''),80)
        if not re.fullmatch(r'order:\d+:\d+',key) and not (CARRY_KEY.fullmatch(key) and key.startswith('carry:'+day+':')): raise ValueError('明細を確認してください。')
        c.execute('INSERT INTO carry_checks(day,row_key,checked) VALUES(?,?,?) ON CONFLICT(day,row_key) DO UPDATE SET checked=excluded.checked',(day,key,1 if d.get('checked') is True else 0))
        return 200,{'ok':True}
    if path=='/api/picking/carry-day':
        day=text(d.get('date',''),10)
        try: date.fromisoformat(day)
        except ValueError: raise ValueError('日付を確認してください。')
        marks={r['row_key'] for r in c.execute('SELECT row_key FROM carry_checks WHERE day=? AND checked=1',(day,))}
        sources=[]
        for r in c.execute('SELECT * FROM orders ORDER BY id'):
            v=json.loads(r['payload'])
            if v.get('markShipDate')!=day or v.get('status')=='下書き' or v.get('sample'): continue
            rows=[]
            for i,x in enumerate(v.get('items') or []):
                if stock(x.get('stock'))!='carry' or not str(x.get('name','')).strip(): continue
                item={k:x.get(k,'') for k in FIELDS}
                key=f"order:{r['id']}:{i}"
                item.update(key=key,customer=v.get('customer',''),stock='carry',sourceNumber=instruction_number(r['id'], v),checked=key in marks)
                rows.append(item)
            if rows: sources.append({'id':r['id'],'number':instruction_number(r['id'], v),'customer':v.get('customer',''),'rows':rows})
        manual=carry_manual(c,day,True)
        for item in manual: item['checked']=item.get('key') in marks
        printed=c.execute('SELECT at,by_name FROM carry_prints WHERE day=?',(day,)).fetchone()
        return 200,{'sources':sources,'manual':manual,'printedAt':printed['at'] if printed else '','printedBy':printed['by_name'] if printed else ''}
    if path=='/api/picking/carry-print':
        day=text(d.get('date',''),10)
        try: date.fromisoformat(day)
        except ValueError: raise ValueError('日付を確認してください。')
        now=datetime.now().isoformat(timespec='seconds')
        c.execute('INSERT INTO carry_prints(day,at,by_name) VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET at=excluded.at, by_name=excluded.by_name',(day,now,u['name']))
        log(c,u,'管理担当持込：印刷',day)
        return 200,{'printedAt':now,'printedBy':u['name']}
    if path=='/api/picking/carry-month':
        month=text(d.get('month',''),7)
        if not re.fullmatch(r'\d{4}-\d{2}',month): raise ValueError('月を確認してください。')
        counts={}
        for r in c.execute('SELECT payload FROM orders'):
            v=json.loads(r['payload'])
            day=str(v.get('markShipDate') or '')
            if not day.startswith(month+'-') or v.get('status')=='下書き' or v.get('sample'): continue
            n=sum(1 for x in (v.get('items') or []) if stock(x.get('stock'))=='carry' and str(x.get('name','')).strip())
            if n: counts[day]=counts.get(day,0)+n
        for r in c.execute('SELECT day,payload FROM carry_inputs WHERE day LIKE ?',(month+'-%',)):
            rows=json.loads(r['payload']).get('rows') or []
            if rows: counts[r['day']]=counts.get(r['day'],0)+len(rows)
        printed={r['day']:r for r in c.execute('SELECT day,at,by_name FROM carry_prints WHERE day LIKE ?',(month+'-%',))}
        days=[{'day':day,'count':counts[day],'printedAt':printed[day]['at'] if day in printed else '','printedBy':printed[day]['by_name'] if day in printed else ''} for day in sorted(counts)]
        return 200,{'month':month,'days':days,'missing':sum(1 for day in days if not day['printedAt'])}
    if path=='/api/picking/board': return board_view(c,u,d,can_access)
    if path=='/api/picking/history': return history_view(c,u,d)
    if path=='/api/picking/candidates': return candidate_view(c,u,d,can_access)
    if path=='/api/picking/gather': return gather_sources(c,u,d,can_access)
    if path=='/api/picking/preview':
        source=imported(c,u,d,can_access)
        if c.execute('SELECT 1 FROM picking_sources WHERE order_id=?',(source['id'],)).fetchone(): return 409,{'error':'この指示書はすでに別のリストに取り込まれています。'}
        return 200,source
    if path=='/api/picking/mail': return send_mail(c,u,d,can_access)
    if path=='/api/picking/sent': return confirm_sent(c,u,d,can_access)
    if path=='/api/picking/arrange': return confirm_arranged(c,u,d,can_access)
    if path=='/api/picking/reopen': return reopen_list(c,u,d)
    if path=='/api/picking/flags': return tune_flags(c,u,d,can_access)
    if path in ('/api/picking/listed/search','/api/picking/listed'): return 404,{'error':'ピッキングリスト済の手修正はできません。リストの保存・削除、またはテスト用リセットで変わります。'}
    if path not in ('/api/picking/save','/api/picking/cancel'): return 404,{'error':'見つかりません。'}
    old=None; before={}
    if d.get('id'):
        old=c.execute('SELECT * FROM picking_lists WHERE id=?',(d['id'],)).fetchone()
        if not old or not visible(u,old): return 404,{'error':'リストが見つかりません。'}
        if not editable(u,old): return 403,{'error':'このリストを編集する権限がありません。'}
        if old['version']!=d.get('version'): return 409,{'error':'別の画面で更新されています。この画面の入力は残しています。操作は実行していません。一覧から開き直してください。'}
        before=json.loads(old['payload'])
    now=datetime.now().isoformat(timespec='seconds')
    reverted=None
    if path.endswith('/cancel'):
        if not old or before.get('status')=='取消': return 400,{'error':'削除できるリストを選択してください。'}
        if finished(before): return 400,{'error':'手配完了済みのリストは削除できません。送信したメールは取り消せず、指示書を再取り込み可能にすると二重の依頼になります。'}
        if (before.get('mailedAt') or before.get('mailNeedsRecreate')) and d.get('acknowledgeMailDiscard') is not True:
            return 409,{'error':'Thunderbirdで開いている依頼メールも破棄してください。すでに送信している場合は削除せず、依頼メールを開くボタンで作業中にしてください。'}
        value={**before,'status':'取消','deletedAt':now,'deletedBy':u['name'],'updatedAt':now}
        action='削除（送信前。指示書を再取り込み可能に変更）'
    else:
        if finished(before) or before.get('status')=='取消': return 400,{'error':'削除済み・手配完了済みの依頼は編集できません。手配前のリストは、削除するか、編集して指示書を外して保存してください。'}
        status='確定'  # Persist complete saved requests; retain the enum for existing integrations.
        title=text(d.get('title',''),100)
        if not title: raise ValueError('保存名を入力してください。')
        day=text(d.get('date','')); date.fromisoformat(day)
        raw=d.get('sources',[]); manual=d.get('manual',[])
        if not isinstance(raw,list) or not isinstance(manual,list) or len(raw)>100 or len(manual)>500: raise ValueError('明細が多すぎます。リストを分けてください。')
        sources=[]; seen=set(); previous={s['id']:s for s in before.get('sources',[])}
        for s in raw:
            if not isinstance(s,dict) or type(s.get('id')) is not int: raise ValueError('指示書を確認してください。')
            if s['id'] in seen: raise ValueError('同じ指示書が重複しています。')
            seen.add(s['id'])
            used=c.execute('SELECT list_id FROM picking_sources WHERE order_id=?',(s['id'],)).fetchone()
            if used and (not old or used['list_id']!=old['id']): return 409,{'error':'選択した指示書は別のリストに取り込まれています。'}
            source=previous.get(s['id']) or imported(c,u,s,can_access)
            sources.append(source)
        manualrows=[]; keys=set()
        for r in manual:
            if not isinstance(r,dict): raise ValueError('明細を確認してください。')
            key=text(r.get('key',''),80)
            if CARRY_KEY.fullmatch(key) and key not in keys: pass
            elif not re.fullmatch(r'manual:[a-zA-Z0-9-]{1,64}',key) or key in keys: key='manual:'+uuid.uuid4().hex
            keys.add(key)
            v={k:r.get(k,'') for k in FIELDS if k!='note'}
            strip_list_row(v)
            v.update(key=key,sourceNumber='直接依頼')
            manualrows.append(v)
        rows=[r for s in sources for r in s['rows']]+manualrows
        if len(rows)>500: raise ValueError('1リスト500明細以内に分けてください。')
        validate_rows(rows,status=='確定')
        draft={'rows':rows}
        if arrange_kind(draft)=='empty': raise ValueError('明細を1行以上入力してください。石狩倉庫・発寒支店・管理担当持込のいずれかに明細が必要です。')
        mail_open=bool(before.get('mailedAt') or before.get('mailNeedsRecreate'))
        if mail_open and d.get('acknowledgeMailChange') is not True:
            if arrange_kind(draft)=='local': return 409,{'error':'石狩倉庫の明細がなくなります。開いている古い依頼メールを破棄することを確認してから保存してください。新しいメールは作りません。すでに送信している場合は、変更を保存せず、依頼メールを開くボタンで作業中にしてください。'}
            return 409,{'error':'メール作成後の修正です。古いメールを破棄して作り直すことを確認してから保存してください。'}
        value={'title':title,'date':day,'status':status,'sources':sources,'manual':manualrows,'rows':rows,'createdBy':before.get('createdBy',u['name']),'updatedBy':u['name'],'updatedAt':now}
        action='保存内容を変更' if old else '依頼を保存'
        if mail_open and arrange_kind(draft)=='local':
            action='保存内容を変更（石狩倉庫の明細がなくなり、メールは不要）'
        elif mail_open:
            value['mailNeedsRecreate']=True
            if before.get('mailedAt'): action+='（メールの作り直しが必要）'
    payload=json.dumps(value,ensure_ascii=False)
    if old:
        lid=old['id']; c.execute('UPDATE picking_lists SET version=version+1,payload=? WHERE id=?',(payload,lid))
    else: lid=c.execute('INSERT INTO picking_lists(version,creator,payload) VALUES(1,?,?)',(u['id'],payload)).lastrowid
    c.execute('DELETE FROM picking_sources WHERE list_id=?',(lid,))
    if value['status']!='取消':
        c.executemany('INSERT INTO picking_sources(order_id,list_id) VALUES(?,?)',[(s['id'],lid) for s in value['sources']])
    sync_carry(c, lid, value.get('manual') or [], (before or {}).get('manual') or [], value['status']!='取消')
    c.execute('INSERT INTO picking_history(list_id,at,actor,action,before_json,after_json) VALUES(?,?,?,?,?,?)',(lid,now,u['name'],action,json.dumps(before,ensure_ascii=False),payload))
    log(c,u,'ピッキングリスト：'+action,f'PK-{lid:06}')
    prev_ids=[s['id'] for s in (before.get('sources') or []) if isinstance(s,dict) and type(s.get('id')) is int]
    next_ids=[] if value['status']=='取消' else [s['id'] for s in value['sources'] if isinstance(s,dict) and type(s.get('id')) is int]
    apply_picking_flags(c,u,next_ids,[i for i in prev_ids if i not in set(next_ids)],f'PK-{lid:06}')
    result=record(c.execute('SELECT * FROM picking_lists WHERE id=?',(lid,)).fetchone(),u)
    if reverted is not None: result={**result,'reverted':reverted}
    return 200,result
