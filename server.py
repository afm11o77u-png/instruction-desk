import ssl
import base64, json, sqlite3, os, re, sys, tempfile, threading, webbrowser
from pathlib import Path
from datetime import datetime, date, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from auth import AuthMixin, schema
import templates
import label_sets
import picking
import migration
from requirements import METHODS, check_posting

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('INSTRUCTION_DATA', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'instructions.sqlite3'
CONFIG = json.loads((ROOT / 'network.json').read_text(encoding='utf-8-sig')) if (ROOT / 'network.json').exists() else {}
PORT = int(os.environ.get('INSTRUCTION_PORT', CONFIG.get('port', 8765)))
BIND_HOST = CONFIG.get('bind', '127.0.0.1')
ALLOWED_HOSTS = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'} | {f'{host}:{PORT}' for host in CONFIG.get('addresses', [])}
SCHEME = 'https' if CONFIG.get('tls') else 'http'
ALLOWED_ORIGINS = {SCHEME + '://' + host for host in ALLOWED_HOSTS}
STATES = ['下書き', '未着手', '作業中', '検品済', '発送済', '完了']

def connect():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c

def initialize():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, payload TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        schema(c)
        templates.initialize(c)
        label_sets.initialize(c)
        picking.initialize(c)
        if not c.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
            customers = ['宮の沢明日佳病院', 'だるま食品銭函工場', '弁釜札幌', 'サザエ食品 店舗']
            for i in range(6):
                d = {'customer':customers[i%4], 'employee':f'サンプル従業員 {i+1}', 'owner':['営業担当 A','営業担当 B','営業担当 C'][i%3], 'received':date.today().isoformat(), 'ship':(date.today()+timedelta(days=i-1)).isoformat(), 'delivery':(date.today()+timedelta(days=i+1)).isoformat(), 'due':(date.today()+timedelta(days=i-1)).isoformat(), 'request':['新規','サイズ交換','追加・補充'][i%3], 'department':'サンプル部署', 'code':'', 'management':'', 'contract':'個人レンタル', 'method':'車両便', 'car':'', 'packing':'ハンガー', 'nameColor':'白', 'barcodeColor':'白', 'notes':'操作確認用の架空データです。', 'status':STATES[[1,2,3,0,4,5][i]], 'sample':True, 'items':[{'name':['スクラブ','白衣','パンツ'][i%3], 'maker':'サンプルメーカー', 'sku':f'DEMO-{i+1:03}', 'size':['M','L','LL'][i%3], 'condition':'新品', 'quantity':2+i%3, 'note':''}]}
                c.execute('INSERT INTO orders(version,payload) VALUES(1,?)', (json.dumps(d,ensure_ascii=False),))
            c.execute("INSERT INTO settings VALUES('seeded','1')")
        if not c.execute("SELECT 1 FROM settings WHERE key='mark_ship_unified'").fetchone():
            for table in ['orders','templates']:
                for row in c.execute(f'SELECT id,payload FROM {table}').fetchall():
                    value=json.loads(row['payload'])
                    if not value.get('markShipDate') and value.get('ship'):
                        value['markShipDate']=value['ship']
                        c.execute(f'UPDATE {table} SET payload=?,version=version+1 WHERE id=?',(json.dumps(value,ensure_ascii=False),row['id']))
                        if table=='orders':
                            changes=json.dumps({'markShipDate':{'before':None,'after':value['markShipDate']}},ensure_ascii=False)
                            c.execute("INSERT INTO history(order_id,at,action,actor,changes) VALUES(?,datetime('now','localtime'),?,?,?)",(row['id'],'発送予定日をマーク出荷日に引き継ぎ','システム更新',changes))
            c.execute("INSERT INTO settings VALUES('mark_ship_unified','1')")
        if not c.execute("SELECT 1 FROM settings WHERE key='author_recorded'").fetchone():
            for row in c.execute('SELECT id,payload FROM orders').fetchall():
                value=json.loads(row['payload'])
                if value.get('authorId'): continue
                hist=c.execute('SELECT actor_id,actor FROM history WHERE order_id=? AND actor_id IS NOT NULL ORDER BY id ASC LIMIT 1',(row['id'],)).fetchone()
                if not hist: continue
                value['authorId']=hist['actor_id']
                value['authorName']=(hist['actor'] or '').split(' (')[0] or '担当未設定'
                c.execute('UPDATE orders SET payload=? WHERE id=?',(json.dumps(value,ensure_ascii=False),row['id']))
            c.execute("INSERT INTO settings VALUES('author_recorded','1')")
        if not c.execute("SELECT 1 FROM settings WHERE key='author_owner_linked'").fetchone():
            owner=c.execute("SELECT id,name FROM users WHERE role='owner'").fetchone()
            if owner:
                for row in c.execute('SELECT id,payload FROM orders').fetchall():
                    value=json.loads(row['payload'])
                    if value.get('authorId') or value.get('sample'): continue
                    value['authorId']=owner['id']; value['authorName']=owner['name']
                    c.execute('UPDATE orders SET payload=? WHERE id=?',(json.dumps(value,ensure_ascii=False),row['id']))
            c.execute("INSERT INTO settings VALUES('author_owner_linked','1')")

def record(row):
    return dict(json.loads(row['payload']), id=row['id'], version=row['version'], number=f"SI-{row['id']:06}")

def tune_flags(handler,d):
    if type(d.get('id')) is not int: return 400, {'error':'指示書を選択してください。'}
    status=d.get('status')
    if status not in STATES: return 400, {'error':'作業状況を選択してください。'}
    if not isinstance(d.get('printed'), bool): return 400, {'error':'印刷済みを指定してください。'}
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        if current['role']!='owner': return 403, {'error':'確認用の変更は主管理者だけができます。'}
        row=c.execute('SELECT * FROM orders WHERE id=?',(d['id'],)).fetchone()
        if not row: return 404, {'error':'指示書が見つかりません。'}
        if row['version']!=d.get('version'): return 409, {'error':'別の画面で更新されました。一覧を更新してください。'}
        payload=json.loads(row['payload'])
        before_status=payload.get('status')
        before_printed=payload.get('printedAt')
        payload['status']=status
        if d['printed']:
            if not payload.get('printedAt'):
                payload['printedAt']=now; payload['printedBy']=current['name']
        else:
            payload.pop('printedAt',None); payload.pop('printedBy',None)
        after_printed=payload.get('printedAt')
        changes={}
        if before_status!=status: changes['status']={'before':before_status,'after':status}
        if before_printed!=after_printed: changes['printedAt']={'before':before_printed,'after':after_printed}
        if not changes: return 200, record(row)
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'],now,'確認用に変更',current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
        return 200, record(c.execute('SELECT * FROM orders WHERE id=?',(row['id'],)).fetchone())

def mark_printed(handler,d):
    raw=d.get('ids')
    if not isinstance(raw,list) or not raw or len(raw)>40: return 400, {'error':'一度に印刷できるのは40件までです。'}
    ids=[]
    for oid in raw:
        if type(oid) is not int or oid in ids: return 400, {'error':'印刷する指示書を確認してください。'}
        ids.append(oid)
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        updated=[]
        rows=[]
        for oid in ids:
            row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
            if not row: return 404, {'error':'指示書が見つかりません。'}
            payload=json.loads(row['payload'])
            if not can_access(current,payload): return 404, {'error':'指示書が見つかりません。'}
            if payload.get('status')=='下書き': return 400, {'error':'下書きはまとめて印刷しません。先に投函してください。'}
            rows.append((oid,payload,payload.get('printedAt')))
        for oid,payload,before in rows:
            payload['printedAt']=now; payload['printedBy']=current['name']
            c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),oid))
            changes={'printedAt':{'before':before,'after':now}}
            c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(oid,now,'印刷',current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
            updated.append(record(c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()))
        return 200, {'orders':updated}

def ship_label(method,note):
    method=(method or '').strip(); note=(note or '').strip()
    if method=='その他' and note: return f'その他（{note}）'
    return method or '未設定'

def ship_warning(payload):
    sheet,note=payload.get('method') or '',payload.get('methodNote') or ''
    chosen,chosen_note=payload.get('shipMethod') or '',payload.get('shipMethodNote') or ''
    if not chosen: return ''
    if sheet!=chosen or (sheet=='その他' and (note or '').strip()!=(chosen_note or '').strip()):
        return f'指示書の納品方法は「{ship_label(sheet,note)}」です。出荷方法は「{ship_label(chosen,chosen_note)}」です。'
    return ''

SCAN_STEPS={
    'barcode':('バーコード発行','barcodeScanAt','barcodeScanBy','barcodeScanById'),
    'stock':('在庫合わせ','stockScanAt','stockScanBy','stockScanById'),
    'set':('セット作業','setScanAt','setScanBy','setScanById'),
    'ship':('発送','shippedAt','shipWorker','shipWorkerId'),
}

def ship_order(handler,d):
    step=str(d.get('step') or 'ship').strip()
    if step not in SCAN_STEPS: return 400, {'error':'読み取る作業を選んでください。'}
    label,at_key,by_key,id_key=SCAN_STEPS[step]
    raw=str(d.get('number','')).strip().upper().replace(' ','')
    raw=raw.strip('*')
    matched=re.fullmatch(r'SI-(\d{1,6})', raw)
    if not matched: return 400, {'error':'指示番号のバーコードを読み取ってください。'}
    method=str(d.get('method') or '').strip()
    note=str(d.get('methodNote') or '').strip()[:40]
    if step=='ship':
        if method not in METHODS: return 400, {'error':'出荷方法を選んでください。'}
        if method=='その他' and not note: return 400, {'error':'その他の内容を入力してください。'}
        if method!='その他': note=''
    else:
        method=''; note=''
    try: worker_id=int(d.get('workerId'))
    except (TypeError,ValueError): return 400, {'error':'作業者を選んでください。'}
    oid=int(matched.group(1))
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        if current['role'] not in ('owner','floor'): return 403, {'error':'出荷仕分けは現場と主管理者が使用します。'}
        worker=c.execute("SELECT id,name FROM users WHERE id=? AND active=1 AND role IN ('owner','floor')",(worker_id,)).fetchone()
        if not worker: return 400, {'error':'作業者を選び直してください。'}
        row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
        if not row: return 404, {'error':'その指示番号の指示書はありません。'}
        payload=json.loads(row['payload'])
        if not can_access(current,payload): return 404, {'error':'その指示番号の指示書はありません。'}
        number=f"SI-{row['id']:06}"
        status=payload.get('status')
        shown={'number':number,'customer':payload.get('customer') or '','employee':payload.get('employee') or '','status':status,'step':step,'stepLabel':label,'already':True,'worker':payload.get(by_key) or '','scannedAt':payload.get(at_key) or '','shipWorker':payload.get('shipWorker') or '','shipMethod':payload.get('shipMethod') or '','shipMethodNote':payload.get('shipMethodNote') or ''}
        if status=='下書き': return 400, {'error':'下書きは読み取れません。先に投函してください。'}
        if step=='ship' and status=='発送済': return 200, {**shown,'warning':ship_warning(payload),'message':f'{number} はすでに発送済です。'}
        if step=='ship' and status=='完了': return 200, {**shown,'warning':ship_warning(payload),'message':f'{number} は完了済みです。'}
        if step!='ship' and payload.get(at_key): return 200, {**shown,'message':f'{number} の{label}は、すでに記録済みです。'}
        new_status=status
        payload[at_key]=now; payload[by_key]=worker['name']; payload[id_key]=worker['id']
        changes={by_key:{'before':'','after':worker['name']},at_key:{'before':'','after':now}}
        if step=='ship':
            new_status='発送済'
            payload['shipMethod']=method; payload['shipMethodNote']=note
            changes['shipMethod']={'before':'','after':ship_label(method,note)}
        elif status=='未着手':
            new_status='作業中'
        if new_status!=status:
            payload['status']=new_status
            changes['status']={'before':status,'after':new_status}
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),oid))
        action=f'{status} → {new_status}（{label}）' if new_status!=status else f'{label}を記録'
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(oid,now,action,current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
        return 200, {**shown,'status':new_status,'already':False,'worker':worker['name'],'scannedAt':now,'shipWorker':payload.get('shipWorker') or '','shipMethod':payload.get('shipMethod') or '','shipMethodNote':payload.get('shipMethodNote') or '','warning':ship_warning(payload) if step=='ship' else '','message':f'{number} の{label}を記録しました。' if step!='ship' else f'{number} を発送済にしました。'}

def can_access(user,payload):
    if user['role']=='owner' or user['overview']: return True
    if user['role']=='floor': return payload.get('status')!='下書き'
    return user['id'] in (payload.get('authorId'),payload.get('assigneeId'))

def present(row,user,names):
    item=record(row)
    if not can_access(user,item): return None
    item['authorName']=names.get(item.get('authorId')) or item.get('authorName') or '担当未設定'
    item['assigneeName']=names.get(item.get('assigneeId')) or ''
    return item

def charge_person(c,raw):
    try: uid=int(raw)
    except (TypeError,ValueError): return None
    return c.execute("SELECT id,name FROM users WHERE id=? AND active=1 AND role IN ('owner','sales')",(uid,)).fetchone()

def apply_charge(c,payload,previous,current,creating):
    payload.pop('assigneeName',None)
    if not creating and current['role']=='floor':
        payload['assigneeId']=previous.get('assigneeId'); payload['owner']=previous.get('owner'); return
    raw=payload.get('assigneeId')
    if not creating and raw in (None,''):
        payload['assigneeId']=previous.get('assigneeId'); payload['owner']=previous.get('owner'); return
    person=charge_person(c, raw if raw not in (None,'') else current['id'])
    if not person: raise ValueError('担当者を利用者から選んでください。')
    payload['assigneeId']=person['id']; payload['owner']=person['name']

def validate(d):
    if not isinstance(d,dict): raise ValueError('保存内容を確認してください。')
    if d.get('status') not in STATES: raise ValueError('作業状況が正しくありません。')
    for k in ['received','ship','delivery','due','employmentDate','finishDate','markShipDate']:
        if d.get(k): date.fromisoformat(d[k])
    items=d.get('items',[])
    if not isinstance(items,list) or len(items)>100: raise ValueError('商品明細を確認してください。')
    for x in items:
        if not isinstance(x,dict): raise ValueError('商品明細を確認してください。')
    return {k:v for k,v in d.items() if k not in ['id','version','number','history']}

class Handler(AuthMixin, BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(30)
        if isinstance(self.request, ssl.SSLSocket):
            self.request.settimeout(10)
            self.request.do_handshake()
            self.request.settimeout(30)
        super().setup()

    def end_headers(self):
        cookies = getattr(self, 'cookie', None)
        if isinstance(cookies, str): cookies = [cookies]
        for cookie in cookies or []:
            self.send_header('Set-Cookie', cookie + ('; Secure' if CONFIG.get('tls') else ''))
        self.send_header('X-Frame-Options','SAMEORIGIN')
        super().end_headers()
    def reply(self,status,data,ctype='application/json; charset=utf-8',headers=None):
        b = data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.headers.get('Host') not in ALLOWED_HOSTS: return self.reply(403, {'error':'接続先アドレスを確認してください。'})
        path=self.path.split('?')[0]
        if path.startswith('/api/') and migration.is_busy() and path != '/api/me':
            return self.reply(503, {'error':'移行処理の実行中です。完了するまで操作を待ってください。'})
        if path == '/api/migration/export':
            try:
                with connect() as c:
                    u = self.session_user(c)
                    migration.owner_guard(u)
                dest, manifest = migration.build_export_zip(DATA)
                data = dest.read_bytes()
                return self.reply(200, data, 'application/zip', {
                    'Content-Disposition': f'attachment; filename="{dest.name}"',
                    'X-Migration-Created-At': str(manifest.get('createdAt') or ''),
                })
            except migration.MigrationError as e:
                return self.reply(403 if '主管理者' in str(e) or 'ログイン' in str(e) else 400, {'error': str(e)})
            except OSError as e:
                return self.reply(400, {'error': migration.oserror_message(e, '移行用バックアップの作成')})
            except Exception:
                return self.reply(500, {'error': '移行用バックアップを作成できませんでした。'})
        if path.startswith('/api/'):
            with connect() as c: result=self.auth_get(path,c)
            if result is not None: return self.reply(*result)
        if path=='/api/picking' or path.startswith('/api/picking/'):
            try:
                with connect() as c: result=picking.handle(self,c,path,None,can_access)
                return self.reply(*result)
            except (ValueError,TypeError,KeyError) as e: return self.reply(400,{'error':str(e)})
        if path=='/api/templates':
            with connect() as c: result=templates.handle(self,c,path)
            return self.reply(*result)
        if path=='/api/label-sets':
            with connect() as c: result=label_sets.handle(self,c,path)
            return self.reply(*result)
        if path=='/api/ship-workers':
            if self.user['role'] not in ('owner','floor'): return self.reply(403,{'error':'出荷仕分けは現場と主管理者が使用します。'})
            with connect() as c:
                rows=[{'id':r['id'],'name':r['name']} for r in c.execute("SELECT id,name FROM users WHERE active=1 AND role IN ('owner','floor') ORDER BY name")]
            return self.reply(200,rows)
        if path=='/api/assignees':
            with connect() as c:
                rows=[{'id':r['id'],'name':r['name']} for r in c.execute("SELECT id,name FROM users WHERE active=1 AND role IN ('owner','sales') ORDER BY name")]
            return self.reply(200,rows)
        if path=='/api/orders':
            with connect() as c:
                names={r['id']:r['name'] for r in c.execute('SELECT id,name FROM users')}
                rows=[item for r in c.execute('SELECT * FROM orders ORDER BY id DESC') if (item:=present(r,self.user,names))]
            return self.reply(200,rows)
        if path.startswith('/api/history/'):
            try: oid=int(path.rsplit('/',1)[1])
            except ValueError: return self.reply(400,{'error':'番号が正しくありません。'})
            with connect() as c:
                order=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
                if not order or not can_access(self.user,json.loads(order['payload'])): return self.reply(404,{'error':'指示書が見つかりません。'})
                rows=[dict(r) for r in c.execute('SELECT at,action,actor,changes FROM history WHERE order_id=? ORDER BY id DESC',(oid,))]
            return self.reply(200,rows)
        files={'/repair.js':'repair.js','/repair.css':'repair.css','/picking':'picking.html','/picking.js':'picking.js','/picking.css':'picking.css','/':'index.html','/app.js':'app.js','/style.css':'style.css','/account.js':'account.js','/account.css':'account.css','/sheet.js':'sheet.js','/sheet.css':'sheet.css','/logo.jpg':'logo.jpg','/favicon.png':'favicon.png','/mascots.png':'mascots.png'}
        if path in files:
            f=ROOT/files[path]
            return self.reply(200,f.read_bytes(),{'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8','jpg':'image/jpeg','png':'image/png'}[f.suffix[1:]])
        self.reply(404,{'error':'見つかりません。'})
    def do_POST(self):
        if self.headers.get('Host') not in ALLOWED_HOSTS: return self.reply(403, {'error':'接続先アドレスを確認してください。'})
        route=self.path.split('?')[0]
        if self.headers.get('X-Desk-Request')!='1': return self.reply(403,{'error':'画面を再読み込みして操作してください。'})
        if self.headers.get('Origin') and self.headers['Origin'] not in ALLOWED_ORIGINS:
            return self.reply(403,{'error':'接続元を確認してください。'})
        if migration.is_busy() and not route.startswith('/api/migration/'):
            return self.reply(503, {'error':'移行処理の実行中です。完了するまで操作を待ってください。'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            limit=80000000 if route in ('/api/migration/inspect','/api/migration/restore') else 9000000 if route=='/api/templates/import' else 1000000
            if n>limit: raise ValueError('データが大きすぎます。')
            d=json.loads(self.rfile.read(n))
            if not isinstance(d,dict): raise ValueError('入力内容を確認してください。')
            if route in ('/api/migration/inspect','/api/migration/restore'):
                try:
                    with connect() as c:
                        u = self.session_user(c)
                        migration.owner_guard(u)
                        actor = {'id': u['id'], 'name': u['name'], 'login': u['login']}
                    raw = d.get('data')
                    if not isinstance(raw, str) or not raw:
                        raise migration.MigrationError('移行用ZIPを選んでください。')
                    name = str(d.get('filename') or '')
                    if name and not name.lower().endswith('.zip'):
                        raise migration.MigrationError('移行用ZIP（.zip）を選んでください。')
                    try:
                        blob = base64.b64decode(raw)
                    except Exception as e:
                        raise migration.MigrationError('ZIPを読めませんでした。') from e
                    with tempfile.NamedTemporaryFile(prefix='mig-upload-', suffix='.zip', delete=False) as tmp:
                        tmp.write(blob)
                        upload = Path(tmp.name)
                    try:
                        if route == '/api/migration/inspect':
                            summary, _manifest = migration.inspect_zip(upload, DATA)
                            return self.reply(200, summary)
                        if d.get('confirm') is not True:
                            raise migration.MigrationError('復元するには、業務データを置き換えることに同意してください。')
                        result = migration.restore_from_zip(
                            DATA, upload, actor=actor, user_map=d.get('userMap'),
                        )
                        self.cookie = [
                            'desk_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0',
                            'desk_gate=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0',
                        ]
                        return self.reply(200, result)
                    finally:
                        try: upload.unlink()
                        except OSError: pass
                except migration.MigrationError as e:
                    msg = str(e)
                    status = 403 if ('主管理者' in msg or 'ログイン' in msg) else 400
                    return self.reply(status, {'error': msg})
                except Exception:
                    return self.reply(500, {'error': '復元できませんでした。'})
            with connect() as c:
                c.execute('BEGIN IMMEDIATE')
                result=self.auth_post(route,d,c)
            if result is not None: return self.reply(*result)
            if route.startswith('/api/picking/') or route.startswith('/api/carry-input/'):
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    result=picking.handle(self,c,route,d,can_access)
                return self.reply(*result)
            if route.startswith('/api/templates/'):
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    result=templates.handle(self,c,route,d)
                return self.reply(*result)
            if route.startswith('/api/label-sets/'):
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    result=label_sets.handle(self,c,route,d)
                return self.reply(*result)
            if route=='/api/backup':
                folder=DATA/'backups'; folder.mkdir(exist_ok=True)
                dest=folder/f"instructions-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
                with connect() as source, sqlite3.connect(dest) as target: source.backup(target)
                return self.reply(200,{'message':'バックアップを作成しました。','filename':dest.name})
            if route=='/api/ship': return self.reply(*ship_order(self,d))
            if route=='/api/orders/flags': return self.reply(*tune_flags(self,d))
            if route=='/api/orders/printed': return self.reply(*mark_printed(self,d))
            if route!='/api/orders': return self.reply(404,{'error':'見つかりません。'})
            payload=validate(d); now=datetime.now().isoformat(timespec='seconds')
            payload.pop('authorId',None); payload.pop('authorName',None)
            with connect() as c:
                c.execute('BEGIN IMMEDIATE')
                current=self.session_user(c)
                if not current or current['must_change']: return self.reply(401,{'error':'ログインし直してください。'})
                if d.get('id'):
                    old=c.execute('SELECT * FROM orders WHERE id=?',(d['id'],)).fetchone()
                    if not old: return self.reply(404,{'error':'指示書が見つかりません。'})
                    if old['version']!=d.get('version'): return self.reply(409,{'error':'別の画面で更新されました。一覧を更新して開き直してください。入力内容は画面に残っています。'})
                    previous=json.loads(old['payload'])
                    if not can_access(current,previous): return self.reply(404,{'error':'指示書が見つかりません。'})
                    payload['authorId']=previous.get('authorId'); payload['authorName']=previous.get('authorName')
                    if previous.get('printedAt'):
                        payload['printedAt']=previous['printedAt']; payload['printedBy']=previous.get('printedBy','')
                    else:
                        payload.pop('printedAt',None); payload.pop('printedBy',None)
                    for key in ('shipWorkerId','shipWorker','shipMethod','shipMethodNote','shippedAt','barcodeScanAt','barcodeScanBy','barcodeScanById','stockScanAt','stockScanBy','stockScanById','setScanAt','setScanBy','setScanById'):
                        if previous.get(key) not in (None,''): payload[key]=previous[key]
                        else: payload.pop(key,None)
                    apply_charge(c,payload,previous,current,False)
                    before=previous['status']
                    if current['role']!='floor' and payload['status']!='下書き': check_posting(payload)
                    if current['role']=='floor':
                        if before=='下書き': return self.reply(403,{'error':'現場から下書きは操作できません。'})
                        if {k:v for k,v in payload.items() if k not in ('status','authorId','authorName','assigneeId','assigneeName')}!={k:v for k,v in previous.items() if k not in ('status','authorId','authorName','assigneeId','assigneeName')}: return self.reply(403,{'error':'現場の権限では作業状況のみ変更できます。'})
                    if before!='下書き' and payload['status']=='下書き': raise ValueError('投函後は下書きに戻せません。')
                    c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),d['id']))
                    oid=d['id']; action='内容を更新' if before==payload['status'] else f"{before} → {payload['status']}"
                else:
                    if current['role']=='floor': return self.reply(403,{'error':'指示書の作成は営業・主管理者が行えます。'})
                    previous={}
                    payload['authorId']=current['id']; payload['authorName']=current['name']
                    apply_charge(c,payload,previous,current,True)
                    if payload['status']!='下書き': check_posting(payload)
                    cur=c.execute('INSERT INTO orders(version,payload) VALUES(1,?)',(json.dumps(payload,ensure_ascii=False),)); oid=cur.lastrowid; action='下書きを作成' if payload['status']=='下書き' else '新規作成・投函'
                changes={k:{'before':previous.get(k),'after':payload.get(k)} for k in sorted(set(previous)|set(payload)) if k not in ('authorId','authorName','assigneeId','assigneeName') and previous.get(k)!=payload.get(k)}
                c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(oid,now,action,current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
                result=record(c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone())
            self.reply(200,result)
        except (ValueError,TypeError,KeyError) as e: self.reply(400,{'error':str(e)})
        except Exception: self.reply(500,{'error':'保存できませんでした。保存場所の空き容量や接続を確認してください。'})

if __name__=='__main__':
    initialize()
    server=ThreadingHTTPServer((BIND_HOST,PORT),Handler)
    if CONFIG.get('tls'):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(ROOT / 'tls/server.crt', ROOT / 'tls/server.key')
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    print(f'{SCHEME}://127.0.0.1:{PORT}',flush=True)
    if '--open' in sys.argv: threading.Timer(.5,lambda:webbrowser.open(f'{SCHEME}://127.0.0.1:{PORT}')).start()
    server.serve_forever()
