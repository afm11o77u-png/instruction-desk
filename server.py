import ssl
import base64, importlib.util, json, shutil, sqlite3, os, re, sys, tempfile, threading, webbrowser
from pathlib import Path
from datetime import datetime, date, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from auth import AuthMixin, schema
import templates
import label_sets
import picking
import placement
import shipping
import names
import display_modes
import migration
import kintai
import name_exe
from requirements import METHODS, check_posting

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('INSTRUCTION_DATA', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'instructions.sqlite3'
STOCK_DB = DATA / 'stock.sqlite3'
def _stock_root():
    # Prefer nested stock-desk inside this repo; keep legacy paths as transition fallbacks.
    nested = ROOT / 'stock-desk'
    if (nested / 'server.py').is_file(): return nested
    chosen = os.environ.get('STOCK_HOME')
    if chosen: return Path(chosen)
    sibling = ROOT.parent / 'stock-desk'
    if (sibling / 'server.py').is_file(): return sibling
    other = Path(r'D:\stock-desk')
    if (other / 'server.py').is_file(): return other
    return nested
STOCK_ROOT = _stock_root()
_stockdesk = None
CONFIG = json.loads((ROOT / 'network.json').read_text(encoding='utf-8-sig')) if (ROOT / 'network.json').exists() else {}
PORT = int(os.environ.get('INSTRUCTION_PORT', CONFIG.get('port', 8765)))
BIND_HOST = CONFIG.get('bind', '127.0.0.1')
ALLOWED_HOSTS = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'} | {f'{host}:{PORT}' for host in CONFIG.get('addresses', [])}
SCHEME = 'https' if CONFIG.get('tls') else 'http'
ALLOWED_ORIGINS = {SCHEME + '://' + host for host in ALLOWED_HOSTS}
STATES = ['下書き', '未着手', '作業中', '発送済', '完了']

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
        placement.bind(DATA)
        placement.schema(c)
        shipping.schema(c)
        names.bind(DATA)
        names.schema(c)
        label_sets.initialize(c)
        display_modes.initialize(c)
        picking.initialize(c)
        picking.backfill_picking_flags(c)
        kintai.bind(DATA)
        kintai.schema(c)
        if not c.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
            customers = ['宮の沢明日佳病院', 'だるま食品銭函工場', '弁釜札幌', 'サザエ食品 店舗']
            for i in range(6):
                d = {'customer':customers[i%4], 'employee':f'サンプル従業員 {i+1}', 'owner':['営業担当 A','営業担当 B','営業担当 C'][i%3], 'received':date.today().isoformat(), 'ship':(date.today()+timedelta(days=i-1)).isoformat(), 'delivery':(date.today()+timedelta(days=i+1)).isoformat(), 'due':(date.today()+timedelta(days=i-1)).isoformat(), 'request':['新規','サイズ交換','追加・補充'][i%3], 'department':'サンプル部署', 'code':'', 'management':'', 'contract':'個人レンタル', 'method':'車両便', 'car':'', 'packing':'ハンガー', 'nameColor':'白', 'barcodeColor':'白', 'notes':'操作確認用の架空データです。', 'status':STATES[[1,2,2,0,3,4][i]], 'sample':True, 'items':[{'name':['スクラブ','白衣','パンツ'][i%3], 'maker':'サンプルメーカー', 'sku':f'DEMO-{i+1:03}', 'size':['M','L','LL'][i%3], 'condition':'新品', 'quantity':2+i%3, 'note':''}]}
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
    placement.import_sources(connect, DATA)
    shipping.import_sources(connect, DATA)
    names.import_catalog(connect, DATA)
    # 女性見本の花子画像変換は起動時に走らせない（DBロック／起動失敗の再発防止）。
    # E:\ では DESK_RUN_FEMALE_IMAGE_MAINTENANCE=1 で maintain_female_sample_hanako.py を実行。
    with connect() as c:
        names.correct_rinko_yellow(c, DATA)
        names.correct_ashuka_name_formats(c, DATA)
        names.ensure_three_customer_name_formats(c, DATA)
        names.ensure_fk_name_formats(c, DATA)
        names.ensure_benkama_name_formats(c, DATA)
        names.ensure_six_customers_name_formats(c, DATA)
        names.correct_moritan_solo_white(c, DATA)
        names.correct_fresh_legacy_formats(c, DATA)
        names.correct_takeda_duplicate_formats(c, DATA)
        names.ensure_asuka_bakery_name_formats(c, DATA)
        names.ensure_small_surname_36_exact_samples(c, DATA)

def order_number(row, payload=None):
    payload=json.loads(row['payload']) if payload is None else payload
    if payload.get('status')=='下書き': return ''
    number=str(payload.get('number') or '')
    if re.fullmatch(r'SI-\d{8}-\d{3,4}', number) or re.fullmatch(r'SI-\d{6}', number): return number
    return f"SI-{row['id']:06}"

def next_instruction_number(c, day):
    prefix=f"SI-{day.strftime('%Y%m%d')}-"
    highest=0
    for (payload,) in c.execute('SELECT payload FROM orders'):
        number=str(json.loads(payload).get('number') or '')
        if number.startswith(prefix) and number[len(prefix):].isdigit(): highest=max(highest, int(number[len(prefix):]))
    seq=highest+1
    return f"{prefix}{seq:03d}" if seq<1000 else f"{prefix}{seq}"

def fix_received_and_number(c, payload, previous, row_id):
    today=date.today()
    if previous and previous.get('status')!='下書き':
        payload['received']=previous.get('received') or today.isoformat()
        payload['number']=previous.get('number') or f"SI-{row_id:06}"
        return
    payload['received']=today.isoformat()
    if payload.get('status')=='下書き':
        payload.pop('number', None)
        return
    payload['number']=next_instruction_number(c, today)

def find_order_by_number(c, raw):
    old=re.fullmatch(r'SI-(\d{1,6})', raw)
    if old: return c.execute('SELECT * FROM orders WHERE id=?',(int(old.group(1)),)).fetchone()
    if not re.fullmatch(r'SI-\d{8}-\d{3,4}', raw): return None
    for row in c.execute('SELECT * FROM orders'):
        if str(json.loads(row['payload']).get('number') or '')==raw: return row
    return None

def record(row):
    payload=json.loads(row['payload'])
    return dict(payload, id=row['id'], version=row['version'], number=order_number(row, payload))

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
        before_received=payload.get('received')
        payload['status']=status
        if d['printed']:
            if not payload.get('printedAt'):
                payload['printedAt']=now; payload['printedBy']=current['name']
        else:
            payload.pop('printedAt',None); payload.pop('printedBy',None)
        after_printed=payload.get('printedAt')
        changes={}
        if before_status!=status: changes['status']={'before':before_status,'after':status}
        if status=='下書き':
            old_number=str(payload.get('number') or '')
            if old_number:
                payload.pop('number', None)
                changes['number']={'before':old_number,'after':''}
        elif before_status=='下書き':
            payload['received']=date.today().isoformat()
            payload['number']=next_instruction_number(c, date.today())
            if before_received!=payload['received']: changes['received']={'before':before_received,'after':payload['received']}
            changes['number']={'before':'','after':payload['number']}
        if before_printed!=after_printed: changes['printedAt']={'before':before_printed,'after':after_printed}
        if not changes: return 200, record(row)
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'],now,'確認用に変更',current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
        return 200, record(c.execute('SELECT * FROM orders WHERE id=?',(row['id'],)).fetchone())

def _debug_time(value, label):
    if value in (None, ''): return ''
    if not isinstance(value, str): raise ValueError(f'{label}の日時を確認してください。')
    text=value.strip()
    if len(text)==16: text+=':00'
    try: datetime.fromisoformat(text)
    except ValueError: raise ValueError(f'{label}の日時を確認してください。')
    return text

def _debug_worker(value):
    if value in (None, '', 0): return None
    if type(value) is not int: raise ValueError('作業者を確認してください。')
    return value

def _debug_pair(payload, changes, id_key, by_key, at_key, worker_id, at, people, label):
    before_by, before_at=payload.get(by_key) or '', payload.get(at_key) or ''
    if worker_id is None and not at:
        payload.pop(id_key, None); payload.pop(by_key, None); payload.pop(at_key, None)
    else:
        if worker_id is None or not at: raise ValueError(f'{label}は作業者と日時を両方入れるか、両方空にしてください。')
        person=people.get(worker_id)
        if not person: raise ValueError(f'{label}の作業者を選び直してください。')
        payload[id_key]=worker_id; payload[by_key]=person; payload[at_key]=at
    after_by, after_at=payload.get(by_key) or '', payload.get(at_key) or ''
    if before_by!=after_by: changes[by_key]={'before':before_by,'after':after_by}
    if before_at!=after_at: changes[at_key]={'before':before_at,'after':after_at}

def debug_flags(handler,d):
    raw=d.get('items')
    if not isinstance(raw, list) or not raw or len(raw)>40: return 400, {'error':'一度に保存できるのは40件までです。'}
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        if current['role']!='owner': return 403, {'error':'確認用の変更は主管理者だけができます。'}
        people={r['id']:r['name'] for r in c.execute("SELECT id,name FROM users WHERE active=1 AND role IN ('owner','floor')")}
        updated=[]
        for item in raw:
            if not isinstance(item, dict) or type(item.get('id')) is not int or type(item.get('version')) is not int: return 400, {'error':'指示書を確認してください。'}
            status=item.get('status')
            if status not in STATES: return 400, {'error':'作業状況を選択してください。'}
            if not isinstance(item.get('printed'), bool): return 400, {'error':'印刷済みを指定してください。'}
            method=str(item.get('shipMethod') or '').strip()
            note=str(item.get('shipMethodNote') or '').strip()[:40]
            if method and method not in METHODS: return 400, {'error':'出荷方法を選択してください。'}
            if method=='その他' and not note: return 400, {'error':'出荷方法のその他を入力してください。'}
            if method!='その他': note=''
            row=c.execute('SELECT * FROM orders WHERE id=?',(item['id'],)).fetchone()
            if not row: return 404, {'error':'指示書が見つかりません。'}
            if row['version']!=item['version']: return 409, {'error':'別の画面で更新されました。一覧を更新してください。'}
            payload=json.loads(row['payload'])
            before_status, before_printed, before_received=payload.get('status'), payload.get('printedAt'), payload.get('received')
            changes={}
            payload['status']=status
            if item['printed']:
                if not payload.get('printedAt'):
                    payload['printedAt']=now; payload['printedBy']=current['name']
            else:
                payload.pop('printedAt', None); payload.pop('printedBy', None)
            if before_status!=status: changes['status']={'before':before_status,'after':status}
            if status=='下書き':
                old_number=str(payload.get('number') or '')
                if old_number:
                    payload.pop('number', None)
                    changes['number']={'before':old_number,'after':''}
            elif before_status=='下書き':
                payload['received']=date.today().isoformat()
                payload['number']=next_instruction_number(c, date.today())
                if before_received!=payload['received']: changes['received']={'before':before_received,'after':payload['received']}
                changes['number']={'before':'','after':payload['number']}
            if before_printed!=payload.get('printedAt'): changes['printedAt']={'before':before_printed,'after':payload.get('printedAt')}
            before_how=ship_label(payload.get('shipMethod'), payload.get('shipMethodNote')) if payload.get('shipMethod') else ''
            if method:
                payload['shipMethod']=method; payload['shipMethodNote']=note
            else:
                payload.pop('shipMethod', None); payload.pop('shipMethodNote', None)
            after_how=ship_label(method, note) if method else ''
            if before_how!=after_how: changes['shipMethod']={'before':before_how or '未設定','after':after_how or '未設定'}
            _debug_pair(payload, changes, 'shipWorkerId', 'shipWorker', 'shippedAt', _debug_worker(item.get('shipWorkerId')), _debug_time(item.get('shippedAt'), '発送'), people, '発送')
            _debug_pair(payload, changes, 'barcodeScanById', 'barcodeScanBy', 'barcodeScanAt', _debug_worker(item.get('barcodeScanById')), _debug_time(item.get('barcodeScanAt'), 'バーコード発行'), people, 'バーコード発行')
            if not changes:
                updated.append(record(row)); continue
            c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
            c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'],now,'確認用に変更',current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
            updated.append(record(c.execute('SELECT * FROM orders WHERE id=?',(row['id'],)).fetchone()))
        return 200, {'orders':updated}

def status_flag(handler,d):
    if type(d.get('id')) is not int: return 400, {'error':'指示書を選択してください。'}
    status=d.get('status')
    if status not in STATES: return 400, {'error':'作業状況を選択してください。'}
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        if current['role']!='owner': return 403, {'error':'作業状況の修正は主管理者だけができます。'}
        row=c.execute('SELECT * FROM orders WHERE id=?',(d['id'],)).fetchone()
        if not row: return 404, {'error':'指示書が見つかりません。'}
        if row['version']!=d.get('version'): return 409, {'error':'別の画面で更新されています。一覧を更新してください。操作は実行していません。'}
        payload=json.loads(row['payload'])
        before=payload.get('status')
        if before==status: return 200, record(row)
        payload['status']=status
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
        changes={'status':{'before':before,'after':status}}
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'],now,'作業状況を変更',current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
        return 200, record(c.execute('SELECT * FROM orders WHERE id=?',(row['id'],)).fetchone())

def print_flag(handler,d):
    if type(d.get('id')) is not int: return 400, {'error':'指示書を選択してください。'}
    if not isinstance(d.get('printed'), bool): return 400, {'error':'印刷済みを指定してください。'}
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        if current['role']!='owner': return 403, {'error':'印刷の修正は主管理者だけができます。'}
        row=c.execute('SELECT * FROM orders WHERE id=?',(d['id'],)).fetchone()
        if not row: return 404, {'error':'指示書が見つかりません。'}
        if row['version']!=d.get('version'): return 409, {'error':'別の画面で更新されています。一覧を更新してください。操作は実行していません。'}
        payload=json.loads(row['payload'])
        status_before=payload.get('status')
        before=payload.get('printedAt') or ''
        if d['printed']:
            if not payload.get('printedAt'):
                payload['printedAt']=now; payload['printedBy']=current['name']
        else:
            payload.pop('printedAt',None); payload.pop('printedBy',None)
        payload['status']=status_before
        after=payload.get('printedAt') or ''
        if before==after: return 200, record(row)
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
        action='印刷済' if d['printed'] else '印刷済を外した'
        changes={'printedAt':{'before':before,'after':after}}
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'],now,action,current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
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
            rows.append((oid,payload,payload.get('printedAt')))
        for oid,payload,before in rows:
            payload['printedAt']=now; payload['printedBy']=current['name']
            c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),oid))
            changes={'printedAt':{'before':before,'after':now}}
            c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(oid,now,'印刷',current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
            updated.append(record(c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()))
        return 200, {'orders':updated}

def complete_shipped(handler,d):
    raw=d.get('ids')
    if not isinstance(raw,list) or not raw or len(raw)>100: return 400, {'error':'一度に完了にできるのは100件までです。'}
    items=[]
    seen=set()
    for item in raw:
        if not isinstance(item,dict) or type(item.get('id')) is not int or type(item.get('version')) is not int or item['id'] in seen: return 400, {'error':'完了にする指示書を確認してください。'}
        seen.add(item['id']); items.append(item)
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        if current['role'] not in ('owner','sales'): return 403, {'error':'完了への変更は営業と主管理者が行えます。'}
        updated=[]
        for item in items:
            row=c.execute('SELECT * FROM orders WHERE id=?',(item['id'],)).fetchone()
            if not row: return 404, {'error':'指示書が見つかりません。'}
            if row['version']!=item['version']: return 409, {'error':'別の画面で更新されました。一覧を更新してから、もう一度選んでください。'}
            payload=json.loads(row['payload'])
            if not can_access(current,payload): return 404, {'error':'指示書が見つかりません。'}
            if payload.get('status')!='発送済': return 400, {'error':'発送済の指示書だけを完了にできます。'}
            payload['status']='完了'
            c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
            changes=json.dumps({'status':{'before':'発送済','after':'完了'}},ensure_ascii=False)
            c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'],now,'発送済 → 完了',current['id'],f"{current['name']} ({current['login']})",changes))
            updated.append(record(c.execute('SELECT * FROM orders WHERE id=?',(row['id'],)).fetchone()))
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
    'ship':('発送','shippedAt','shipWorker','shipWorkerId'),
}

def ship_order(handler,d):
    step=str(d.get('step') or 'ship').strip()
    if step not in SCAN_STEPS: return 400, {'error':'読み取る作業を選んでください。'}
    label,at_key,by_key,id_key=SCAN_STEPS[step]
    raw=str(d.get('number','')).strip().upper().replace(' ','')
    raw=raw.strip('*')
    if not (re.fullmatch(r'SI-\d{1,6}', raw) or re.fullmatch(r'SI-\d{8}-\d{3,4}', raw)): return 400, {'error':'指示番号のバーコードを読み取ってください。'}
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
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        if current['role'] not in ('owner','floor'): return 403, {'error':'出荷仕分けは現場と主管理者が使用します。'}
        worker=c.execute("SELECT id,name FROM users WHERE id=? AND active=1 AND role IN ('owner','floor')",(worker_id,)).fetchone()
        if not worker: return 400, {'error':'作業者を選び直してください。'}
        row=find_order_by_number(c, raw)
        if not row: return 404, {'error':'その指示番号の指示書はありません。'}
        payload=json.loads(row['payload'])
        if not can_access(current,payload): return 404, {'error':'その指示番号の指示書はありません。'}
        number=order_number(row, payload)
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

FLOOR_PROGRESS=('通常作業','修理待ち','修理中','修理完了')
DUE_KEYS=('floorProgress','repairRequestedDate','repairReadyDate','shipEstimateDate','shipEstimateUnknown','floorNote','floorUpdatedBy','floorUpdatedAt','floorUpdatedById','salesConfirmPending','salesConfirmedAt','salesConfirmedBy','salesConfirmedById','salesConfirmNote','salesConfirmedEstimate','salesConfirmedUnknown','salesConfirmedMark','shortages')
SUPPLY=('未発注','発注済み','一部入荷','入荷済み')

def _due_date(value, label):
    if value in (None,''): return ''
    if not isinstance(value,str): raise ValueError(f'{label}を確認してください。')
    try: date.fromisoformat(value)
    except ValueError: raise ValueError(f'{label}を確認してください。')
    return value

def due_attention(payload):
    if payload.get('shipEstimateUnknown'): return True
    estimate, mark=payload.get('shipEstimateDate') or '', payload.get('markShipDate') or ''
    return bool(estimate and mark and estimate>mark)

def due_confirmed(payload):
    if not payload.get('salesConfirmedAt'): return False
    return (payload.get('salesConfirmedEstimate') or '')==(payload.get('shipEstimateDate') or '') and bool(payload.get('salesConfirmedUnknown'))==bool(payload.get('shipEstimateUnknown')) and (payload.get('salesConfirmedMark') or '')==(payload.get('markShipDate') or '')

def keep_due(payload, previous):
    for key in DUE_KEYS:
        if key not in previous: payload.pop(key, None)
        elif key=='shortages': payload[key]=json.loads(json.dumps(previous[key]))
        else: payload[key]=previous[key]

def refresh_due_for_mark(payload, previous):
    if (payload.get('markShipDate') or '')==(previous.get('markShipDate') or ''): return
    if due_confirmed(payload):
        payload['salesConfirmPending']=False
        return
    payload['salesConfirmPending']=due_attention(payload)

def floor_pending(payload, previous):
    if due_confirmed(payload): return False
    changed=(payload.get('shipEstimateDate') or '')!=(previous.get('shipEstimateDate') or '') or bool(payload.get('shipEstimateUnknown'))!=bool(previous.get('shipEstimateUnknown'))
    if not changed: return bool(previous.get('salesConfirmPending'))
    if previous.get('salesConfirmedAt'): return True
    if due_attention(payload) or previous.get('salesConfirmPending'): return True
    return False

def _actor(current):
    return f"{current['name']} ({current['login']})"

def _product_key(item):
    return tuple(str((item or {}).get(k) or '').strip() for k in ('name','maker','sku','size'))

def ensure_line_ids(items, avoid=()):
    used=set(avoid); claimed=set()
    for item in items or []:
        if not isinstance(item, dict): continue
        lid=item.get('lineId')
        if isinstance(lid, str) and re.fullmatch(r'L\d+', lid) and lid not in used and lid not in claimed:
            claimed.add(lid)
        else:
            item.pop('lineId', None)
    used |= claimed; n=1
    for item in items or []:
        if not isinstance(item, dict) or item.get('lineId'): continue
        while f'L{n}' in used: n+=1
        item['lineId']=f'L{n}'; used.add(item['lineId'])

def reattach_line_ids(new_items, old_items):
    old=[it for it in (old_items or []) if isinstance(it, dict) and isinstance(it.get('lineId'), str)]
    old_by={it['lineId']:it for it in old}
    used=set()
    for item in new_items or []:
        if not isinstance(item, dict): continue
        sent=item.get('lineId')
        if isinstance(sent, str) and sent in old_by and sent not in used and _product_key(old_by[sent])==_product_key(item):
            used.add(sent)
        else:
            item.pop('lineId', None)
    for item in new_items or []:
        if not isinstance(item, dict) or item.get('lineId'): continue
        for old_item in old:
            if old_item['lineId'] in used: continue
            if _product_key(old_item)==_product_key(item):
                item['lineId']=old_item['lineId']; used.add(old_item['lineId']); break
    ensure_line_ids(new_items, {it['lineId'] for it in old if it['lineId'] not in used})

def reconcile_shortages(payload):
    items={it.get('lineId'):it for it in (payload.get('items') or []) if isinstance(it, dict)}
    rows=payload.get('shortages')
    if not isinstance(rows, list):
        payload.pop('shortages', None); return
    kept=[]
    for row in rows:
        if not isinstance(row, dict): continue
        item=items.get(row.get('lineId'))
        if not item: row['link']='missing'
        elif _product_key(item)!=_product_key(row): row['link']='changed'
        else: row['link']='ok'
        kept.append(row)
    if kept: payload['shortages']=kept
    else: payload.pop('shortages', None)

def shortage_text(rows):
    if not rows: return 'なし'
    bits=[]
    for s in rows:
        if not isinstance(s, dict): continue
        label=' '.join(x for x in (str(s.get('name') or ''), str(s.get('sku') or ''), str(s.get('size') or '')) if x).strip()
        maker='未定' if s.get('makerShipUnknown') else (s.get('makerShipDate') or '未入力')
        arrival='未定' if s.get('arrivalUnknown') else (s.get('arrivalDate') or '未入力')
        link={'missing':'（明細から外れています）','changed':'（明細の商品が変わっています）'}.get(s.get('link'),'')
        bits.append(f"{label} {s.get('qty')}枚 {s.get('supply')} 発注{s.get('orderedDate') or '未入力'} メーカー{maker} 入荷{arrival}{link}")
    return ' / '.join(bits) or 'なし'

def _next_shortage_id(previous, built):
    used={r.get('id') for r in list(previous)+built if isinstance(r, dict)}
    n=1
    while f'S{n}' in used: n+=1
    return f'S{n}'

def parse_shortages(raw, items, previous_rows):
    # Shortage-only saves may send just lineId/qty (and optional id); supply/dates default or inherit.
    if raw is None: raw=[]
    if not isinstance(raw, list) or len(raw)>40: raise ValueError('不足商品が多すぎます。')
    previous=[r for r in (previous_rows or []) if isinstance(r, dict)]
    by_id={r.get('id'):r for r in previous if isinstance(r.get('id'), str)}
    by_line={r.get('lineId'):r for r in previous if isinstance(r.get('lineId'), str)}
    items_by={it.get('lineId'):it for it in items if isinstance(it, dict)}
    seen=set(); out=[]
    for raw_row in raw:
        if not isinstance(raw_row, dict): raise ValueError('不足商品を確認してください。')
        sid=raw_row.get('id')
        prev=by_id.get(sid) if isinstance(sid, str) else None
        if prev:
            line_id=prev.get('lineId')
            snap={k:str(prev.get(k) or '') for k in ('name','maker','sku','size')}
        else:
            line_id=raw_row.get('lineId')
            if not prev and isinstance(line_id, str): prev=by_line.get(line_id)
            item=items_by.get(line_id)
            if not isinstance(item, dict): raise ValueError('不足の対象商品を、商品明細から選び直してください。')
            snap={k:str(item.get(k) or '').strip() for k in ('name','maker','sku','size')}
            if not snap['name']: raise ValueError('商品名のない明細は不足登録できません。')
        if not line_id or line_id in seen: raise ValueError('同じ商品の不足は1件にまとめてください。')
        seen.add(line_id)
        qty=raw_row.get('qty')
        if type(qty) is not int or qty<1 or qty>99999: raise ValueError('不足数量は1以上の整数で入力してください。')
        item=items_by.get(line_id)
        if isinstance(item, dict):
            ship=item.get('quantity')
            if type(ship) is not int:
                try: ship=int(ship)
                except (TypeError, ValueError): ship=0
            if qty>ship: raise ValueError('不足枚数は出荷枚数を超えられません。')
        supply=raw_row.get('supply')
        if supply not in SUPPLY:
            supply=prev.get('supply') if prev and prev.get('supply') in SUPPLY else '未発注'
        if isinstance(raw_row.get('makerShipUnknown'), bool):
            maker_unknown=raw_row['makerShipUnknown']
        elif prev is not None:
            maker_unknown=bool(prev.get('makerShipUnknown'))
        else:
            maker_unknown=False
        if isinstance(raw_row.get('arrivalUnknown'), bool):
            arrival_unknown=raw_row['arrivalUnknown']
        elif prev is not None:
            arrival_unknown=bool(prev.get('arrivalUnknown'))
        else:
            arrival_unknown=False
        if 'orderedDate' in raw_row:
            ordered=_due_date(raw_row.get('orderedDate'),'発注日')
        else:
            ordered=(prev.get('orderedDate') or '') if prev else ''
            if ordered: ordered=_due_date(ordered,'発注日')
        if 'makerShipDate' in raw_row or 'makerShipUnknown' in raw_row:
            maker=_due_date(raw_row.get('makerShipDate'),'メーカー出荷予定日')
        else:
            maker=(prev.get('makerShipDate') or '') if prev else ''
            if maker: maker=_due_date(maker,'メーカー出荷予定日')
        if 'arrivalDate' in raw_row or 'arrivalUnknown' in raw_row:
            arrival=_due_date(raw_row.get('arrivalDate'),'入荷予定日')
        else:
            arrival=(prev.get('arrivalDate') or '') if prev else ''
            if arrival: arrival=_due_date(arrival,'入荷予定日')
        if maker_unknown: maker=''
        if arrival_unknown: arrival=''
        if 'note' in raw_row:
            note=raw_row.get('note') if isinstance(raw_row.get('note'), str) else ''
        else:
            note=prev.get('note') if prev and isinstance(prev.get('note'), str) else ''
        note=note.strip()[:300]
        row={'id':(prev.get('id') if prev and isinstance(prev.get('id'), str) else None) or _next_shortage_id(previous, out),'lineId':line_id,**snap,'qty':qty,'supply':supply,'makerShipUnknown':maker_unknown,'arrivalUnknown':arrival_unknown}
        if ordered: row['orderedDate']=ordered
        if maker: row['makerShipDate']=maker
        if arrival: row['arrivalDate']=arrival
        if note: row['note']=note
        out.append(row)
    return out

def supply_dates_changed(before, after):
    def key(rows):
        return sorted((r.get('id'), r.get('makerShipDate') or '', bool(r.get('makerShipUnknown')), r.get('arrivalDate') or '', bool(r.get('arrivalUnknown'))) for r in (rows or []) if isinstance(r, dict))
    return key(before)!=key(after)

def floor_progress(handler,d):
    if type(d.get('id')) is not int or type(d.get('version')) is not int: return 400, {'error':'指示書を選択してください。'}
    progress=d.get('floorProgress')
    if progress not in FLOOR_PROGRESS: return 400, {'error':'現場の状況を選択してください。'}
    try:
        requested=_due_date(d.get('repairRequestedDate'),'修理依頼日')
        ready=_due_date(d.get('repairReadyDate'),'仕上がり予定日')
        estimate=_due_date(d.get('shipEstimateDate'),'出荷見込日')
    except ValueError as e: return 400, {'error':str(e)}
    if not isinstance(d.get('shipEstimateUnknown'), bool): return 400, {'error':'出荷見込み未定の指定を確認してください。'}
    unknown=d['shipEstimateUnknown']
    if unknown: estimate=''
    note=d.get('floorNote') if isinstance(d.get('floorNote'), str) else ''
    note=note.strip()[:500]
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        if current['role'] not in ('owner','floor'): return 403, {'error':'現場進捗の登録は現場と主管理者が行えます。'}
        row=c.execute('SELECT * FROM orders WHERE id=?',(d['id'],)).fetchone()
        if not row: return 404, {'error':'指示書が見つかりません。'}
        if row['version']!=d['version']: return 409, {'error':'別の画面で更新されました。入力内容は残しています。一覧を更新してから、もう一度保存してください。'}
        payload=json.loads(row['payload'])
        if not can_access(current,payload): return 404, {'error':'指示書が見つかりません。'}
        if payload.get('status')=='下書き': return 400, {'error':'下書きには現場進捗を登録できません。先に投函してください。'}
        previous=dict(payload)
        items=payload.get('items') if isinstance(payload.get('items'), list) else []
        ensure_line_ids(items)
        payload['items']=items
        # shortages 省略時は既存を維持（修理遅延の簡易登録など）
        if 'shortages' in d:
            try: shortages=parse_shortages(d.get('shortages'), items, previous.get('shortages'))
            except ValueError as e: return 400, {'error':str(e)}
            if shortages: payload['shortages']=shortages
            else: payload.pop('shortages', None)
        reconcile_shortages(payload)
        payload['floorProgress']=progress
        if requested: payload['repairRequestedDate']=requested
        else: payload.pop('repairRequestedDate', None)
        if ready: payload['repairReadyDate']=ready
        else: payload.pop('repairReadyDate', None)
        if estimate: payload['shipEstimateDate']=estimate
        else: payload.pop('shipEstimateDate', None)
        payload['shipEstimateUnknown']=unknown
        if note: payload['floorNote']=note
        else: payload.pop('floorNote', None)
        payload['floorUpdatedById']=current['id']
        payload['floorUpdatedBy']=current['name']
        payload['floorUpdatedAt']=now
        payload['salesConfirmPending']=floor_pending(payload, previous)
        changes={k:{'before':previous.get(k),'after':payload.get(k)} for k in ('floorProgress','repairRequestedDate','repairReadyDate','shipEstimateDate','shipEstimateUnknown','floorNote','salesConfirmPending') if previous.get(k)!=payload.get(k)}
        if previous.get('shortages')!=payload.get('shortages'): changes['shortages']={'before':shortage_text(previous.get('shortages')),'after':shortage_text(payload.get('shortages'))}
        if not changes: return 200, record(row)
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'],now,'現場進捗を更新',current['id'],_actor(current),json.dumps(changes,ensure_ascii=False)))
        return 200, record(c.execute('SELECT * FROM orders WHERE id=?',(row['id'],)).fetchone())

def due_confirm(handler,d):
    # Confirm-only: shipEstimateDate / floor progress fields are ignored even if sent.
    if type(d.get('id')) is not int or type(d.get('version')) is not int: return 400, {'error':'指示書を選択してください。'}
    memo=d.get('salesConfirmNote') if isinstance(d.get('salesConfirmNote'), str) else ''
    memo=memo.strip()[:500]
    now=datetime.now().isoformat(timespec='seconds')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current=handler.session_user(c)
        if not current or current['must_change']: return 401, {'error':'ログインし直してください。'}
        if current['role'] not in ('owner','sales'): return 403, {'error':'納期の確認は営業と主管理者が行えます。'}
        row=c.execute('SELECT * FROM orders WHERE id=?',(d['id'],)).fetchone()
        if not row: return 404, {'error':'指示書が見つかりません。'}
        if row['version']!=d['version']: return 409, {'error':'別の画面で更新されました。入力内容は残しています。一覧を更新してから、もう一度保存してください。'}
        payload=json.loads(row['payload'])
        if not can_access(current,payload): return 404, {'error':'指示書が見つかりません。'}
        if payload.get('status')=='下書き': return 400, {'error':'下書きの納期は確認できません。'}
        if not payload.get('salesConfirmPending') and not due_attention(payload): return 400, {'error':'今の出荷見込みは、確認が必要な状態ではありません。'}
        if due_confirmed(payload): return 400, {'error':'この納期情報は確認済みです。'}
        previous=dict(payload)
        payload['salesConfirmedAt']=now
        payload['salesConfirmedBy']=current['name']
        payload['salesConfirmedById']=current['id']
        payload['salesConfirmedEstimate']=payload.get('shipEstimateDate') or ''
        payload['salesConfirmedUnknown']=bool(payload.get('shipEstimateUnknown'))
        payload['salesConfirmedMark']=payload.get('markShipDate') or ''
        payload['salesConfirmPending']=False
        if memo: payload['salesConfirmNote']=memo
        else: payload.pop('salesConfirmNote', None)
        changes={k:{'before':previous.get(k),'after':payload.get(k)} for k in ('salesConfirmedAt','salesConfirmedBy','salesConfirmNote','salesConfirmedEstimate','salesConfirmedUnknown','salesConfirmedMark','salesConfirmPending') if previous.get(k)!=payload.get(k)}
        c.execute('UPDATE orders SET version=version+1,payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),row['id']))
        c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(row['id'],now,'納期を確認',current['id'],_actor(current),json.dumps(changes,ensure_ascii=False)))
        return 200, record(c.execute('SELECT * FROM orders WHERE id=?',(row['id'],)).fetchone())

def can_access(user,payload):
    if user['role'] in ('floor2','floor3'): return False
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
    return {k:v for k,v in d.items() if k not in ['id','version','number','history','pickingRequest','pickingListed','pickingListNumber','pickingListId','pickingManual']}

def stockdesk():
    global _stockdesk
    if _stockdesk: return _stockdesk
    if str(STOCK_ROOT) not in sys.path: sys.path.insert(0, str(STOCK_ROOT))
    spec = importlib.util.spec_from_file_location('stockdesk_server', STOCK_ROOT / 'server.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _stockdesk = mod
    return mod

def ensure_stock():
    stockdesk().prepare(STOCK_DB, 'desk')

class Handler(AuthMixin, BaseHTTPRequestHandler):
    def end_headers(self):
        cookies=getattr(self,'cookie',None)
        if isinstance(cookies,str): cookies=[cookies]
        for item in cookies or []: self.send_header('Set-Cookie',item + ('; Secure' if CONFIG.get('tls') else ''))
        self.send_header('X-Frame-Options','SAMEORIGIN')
        super().end_headers()
    def reply(self,status,data,ctype='application/json; charset=utf-8',headers=None):
        b = data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(b))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers(); self.wfile.write(b)
    def reply_file(self, status, path, ctype='application/octet-stream', headers=None):
        """Stream a file to the client without loading the whole body into RAM."""
        path = Path(path)
        size = path.stat().st_size
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        with open(path, 'rb') as f:
            shutil.copyfileobj(f, self.wfile, length=1024 * 1024)
    def serve_inventory(self, method):
        from urllib.parse import urlparse, parse_qs
        parsed=urlparse(self.path)
        path=parsed.path
        if not (STOCK_ROOT / 'server.py').is_file(): return self.reply(503, {'error':'在庫管理の置き場所が見つかりません。'})
        with connect() as c:
            u=self.session_user(c)
        if not u: return self.reply(401, {'error':'ログインしてください。'})
        if u['must_change']: return self.reply(403, {'error':'最初に仮パスワードを変更してください。'})
        if u['role']!='owner' and not u['manage'] and not u['overview']: return self.reply(403, {'error':'在庫管理は主管理者と管理者が使用します。'})
        # overview-only (no manage) is read-only for stock quantity changes.
        can_write = u['role']=='owner' or bool(u['manage'])
        actor = {'id': u['id'], 'name': u['name'], 'role': u['role']}
        ensure_stock()
        if path.startswith('/inventory/api'):
            api_path=path[len('/inventory'):] or '/'
            # 商品登録の全削除（検証用）は主管理者のみ（manage 権限でも不可）
            if api_path in ('/api/products/registry-wipe', '/api/products/registry-wipe/preview') and u['role']!='owner':
                return self.reply(403, {'error':'商品登録の全削除は主管理者だけが実行できます。', 'deleted': False})
            if method=='GET':
                return self.reply(*stockdesk().api_get(STOCK_DB, 'desk', api_path, parse_qs(parsed.query), can_write=can_write, actor=actor))
            if self.headers.get('X-Stock-Request')!='1': return self.reply(400, {'error':'画面からの操作だけを受け付けます。'})
            origin=self.headers.get('Origin') or ''
            if origin and origin not in ALLOWED_ORIGINS: return self.reply(403, {'error':'接続元を確認してください。'})
            try: n=int(self.headers.get('Content-Length') or 0)
            except ValueError: return self.reply(400, {'error':'送信内容を読めませんでした。'})
            # Same paths + limit as stock-desk standalone (stock-desk/upload_limits.py).
            sd = stockdesk()
            if n > sd.body_limit_for(api_path):
                return self.reply(400, {'error': sd.OVERSIZE_BODY_ERROR})
            try: body=json.loads(self.rfile.read(n).decode('utf-8') or '{}')
            except json.JSONDecodeError: return self.reply(400, {'error':'送信内容を読めませんでした。'})
            try:
                result=sd.api_post(STOCK_DB, 'desk', api_path, body, can_write=can_write, actor=actor)
            except Exception as e:
                # Keep JSON error body so /inventory/ proxy never looks like Failed to fetch.
                print(f'inventory api_post crashed path={api_path}: {type(e).__name__}: {e}', flush=True)
                return self.reply(500, {
                    'error': '処理中にエラーが発生しました。登録は完了していません。',
                    'registered': False,
                })
            return self.reply(*result)
        rel='index.html' if path in ('/inventory','/inventory/') else path[len('/inventory/'):]
        root=(STOCK_ROOT/'static').resolve()
        file=(root/rel).resolve()
        if root not in file.parents and file!=root: return self.reply(404, {'error':'見つかりません。'})
        if not file.is_file(): return self.reply(404, {'error':'見つかりません。'})
        kind={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}.get(file.suffix[1:],'application/octet-stream')
        return self.reply(200, file.read_bytes(), kind)
    def do_GET(self):
        if self.headers.get('Host') not in ALLOWED_HOSTS: return self.reply(403, {'error':'接続先アドレスを確認してください。'})
        path=self.path.split('?')[0]
        if path=='/inventory' or path.startswith('/inventory/'): return self.serve_inventory('GET')
        if path.startswith('/api/') and migration.is_busy() and path != '/api/me':
            return self.reply(503, {'error':'移行処理の実行中です。完了するまで操作を待ってください。'})
        if path == '/api/migration/export':
            try:
                with connect() as c:
                    u = self.session_user(c)
                    migration.owner_guard(u)
                dest, manifest = migration.build_export_zip(DATA)
                # Stream from disk (copy remains under data/backups); avoid loading whole ZIP into RAM.
                return self.reply_file(200, dest, 'application/zip', {
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
        if path in ('/api/templates','/api/templates/admin'):
            with connect() as c: result=templates.handle(self,c,path)
            return self.reply(*result)
        if path=='/api/label-sets':
            with connect() as c: result=label_sets.handle(self,c,path)
            return self.reply(*result)
        if path=='/api/display-modes':
            with connect() as c: result=display_modes.handle_get(self,c)
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
                user_names={r['id']:r['name'] for r in c.execute('SELECT id,name FROM users')}
                links=picking.request_links(c)
                rows=[]
                for r in c.execute('SELECT * FROM orders ORDER BY id DESC'):
                    item=present(r,self.user,user_names)
                    if not item: continue
                    extra=picking.order_extra(r['id'], json.loads(r['payload']), links)
                    item.update(extra)
                    rows.append(item)
            return self.reply(200,rows)
        if path=='/api/shipments/xlsx':
            with connect() as c: result=shipping.export_book(self, c)
            return self.reply(*result)
        if path=='/api/shipments' or path=='/api/shipments/options':
            with connect() as c:
                result=shipping.search(self, c) if path=='/api/shipments' else shipping.customers(self, c)
            return self.reply(*result)
        if path=='/api/placements' or path=='/api/placements/options' or re.fullmatch(r'/api/placements/\d+', path) or re.fullmatch(r'/api/placements/\d+/(thumb|image)', path) or re.fullmatch(r'/api/placements/\d+/history', path):
            with connect() as c:
                if path=='/api/placements': result=placement.search(self, c)
                elif path=='/api/placements/options': result=placement.customers(self, c)
                elif path.endswith('/history'): result=placement.history(self, c, int(path.split('/')[3]))
                elif re.fullmatch(r'/api/placements/\d+', path): result=placement.get(self, c, int(path.rsplit('/',1)[1]))
                else:
                    result=placement.send_file(self, c, int(path.split('/')[3]), path.endswith('/thumb'))
            if len(result)==3: return self.reply(result[0], result[1], result[2])
            return self.reply(*result)
        if path=='/api/placement-corrections':
            with connect() as c:
                result=placement.list_corrections(self, c)
            return self.reply(*result)
        if path in ('/api/name-labels/exe', '/api/name-labels/exe/info'):
            with connect() as c: u=self.session_user(c)
            try:
                if path.endswith('/info'):
                    name_exe.check_user(u)
                    owner=u['role']=='owner'
                    data={'exe': name_exe.info(DATA), 'canUpload': owner, 'maxBytes': name_exe.MAX_BYTES}
                    if data['exe']: data['exe'].pop('sha256', None)
                    if owner: data['dropFolder']={'path': str(EXE_DROP_DIR), 'enabled': EXE_DROP_ENABLED, 'intervalSeconds': name_exe.DROP_INTERVAL}
                    return self.reply(200, data)
                file, meta, disposition = name_exe.download(DATA, u)
            except name_exe.ExeError as e:
                return self.reply(e.status, {'error': e.message})
            return self.reply_file(200, file, 'application/vnd.microsoft.portable-executable', {'Content-Disposition': disposition})
        if path=='/api/name-labels' or path.startswith('/api/name-labels/'):
            with connect() as c:
                if path=='/api/name-labels': result=names.search(self, c)
                elif path=='/api/name-labels/deleted': result=names.list_deleted(self, c)
                elif path.startswith('/api/name-labels/customers/'): result=names.customer(self, c, int(path.rsplit('/',1)[1]))
                elif path.startswith('/api/name-labels/formats/'): result=names.detail(self, c, int(path.rsplit('/',1)[1]))
                elif path.startswith('/api/name-labels/samples/'): result=names.send_sample(self, c, int(path.rsplit('/',1)[1]))
                else: result=404, {'error':'見つかりません。'}
            if len(result)==3: return self.reply(result[0], result[1], result[2])
            return self.reply(*result)
        if path=='/api/kintai' or path.startswith('/api/kintai/'):
            try:
                with connect() as c:
                    result=kintai.handle_get(self, c, path)
                return self.reply(*result)
            except (ValueError, TypeError, KeyError) as e:
                return self.reply(400, {'error': str(e)})
        if path.startswith('/api/history/'):
            try: oid=int(path.rsplit('/',1)[1])
            except ValueError: return self.reply(400,{'error':'番号が正しくありません。'})
            with connect() as c:
                order=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
                if not order or not can_access(self.user,json.loads(order['payload'])): return self.reply(404,{'error':'指示書が見つかりません。'})
                rows=[dict(r) for r in c.execute('SELECT at,action,actor,changes FROM history WHERE order_id=? ORDER BY id DESC',(oid,))]
            return self.reply(200,rows)
        files={'/repair.js':'repair.js','/repair.css':'repair.css','/shipping.js':'shipping.js','/placement.js':'placement.js','/names.js':'names.js','/kintai.js':'kintai.js','/kintai-shift.js':'kintai-shift.js','/kintai-bihin.js':'kintai-bihin.js','/picking':'picking.html','/picking-history':'picking-history.html','/picking-history.js':'picking-history.js','/picking-board':'picking-board.html','/picking-board.js':'picking-board.js','/picking.js':'picking.js','/picking.css':'picking.css','/':'index.html','/app.js':'app.js','/style.css':'style.css','/account.js':'account.js','/account.css':'account.css','/box_ticket.js':'box_ticket.js','/fixed_label.js':'fixed_label.js','/handwrite_label.js':'handwrite_label.js','/label_paste.js':'label_paste.js','/sheet.js':'sheet.js','/sheet.css':'sheet.css','/display-mode.js':'display-mode.js','/display-mode.css':'display-mode.css','/logo.jpg':'logo.jpg','/favicon.png':'favicon.png','/mascots.png':'mascots.png'}
        if path in files:
            f=ROOT/files[path]
            return self.reply(200,f.read_bytes(),{'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8','jpg':'image/jpeg','png':'image/png'}[f.suffix[1:]])
        if path.startswith('/fixed-label/'):
            rel=path[len('/fixed-label/'):]
            if not rel or '/' in rel or rel in ('.','..') or '..' in rel:
                return self.reply(404,{'error':'見つかりません。'})
            f=(ROOT/'fixed-label'/rel).resolve()
            root=(ROOT/'fixed-label').resolve()
            if root not in f.parents and f!=root: return self.reply(404,{'error':'見つかりません。'})
            if not f.is_file(): return self.reply(404,{'error':'見つかりません。'})
            kinds={'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','pdf':'application/pdf'}
            kind=kinds.get(f.suffix[1:].lower())
            if not kind: return self.reply(404,{'error':'見つかりません。'})
            return self.reply(200,f.read_bytes(),kind)
        self.reply(404,{'error':'見つかりません。'})
    def do_POST(self):
        if self.headers.get('Host') not in ALLOWED_HOSTS: return self.reply(403, {'error':'接続先アドレスを確認してください。'})
        route=self.path.split('?')[0]
        if route.startswith('/inventory/'):
            if migration.is_busy(): return self.reply(503, {'error':'移行処理の実行中です。完了するまで操作を待ってください。'})
            return self.serve_inventory('POST')
        if self.headers.get('X-Desk-Request')!='1': return self.reply(403,{'error':'画面を再読み込みして操作してください。'})
        if self.headers.get('Origin') and self.headers['Origin'] not in ALLOWED_ORIGINS:
            return self.reply(403,{'error':'接続元を確認してください。'})
        if migration.is_busy() and not route.startswith('/api/migration/'):
            return self.reply(503, {'error':'移行処理の実行中です。完了するまで操作を待ってください。'})
        if route=='/api/name-labels/exe':
            from urllib.parse import unquote
            with connect() as c: u=self.session_user(c)
            try:
                try: n=int(self.headers.get('Content-Length') or 0)
                except ValueError: raise name_exe.ExeError(400, '送信内容を読めませんでした。')
                meta=name_exe.save(DATA, u, unquote(self.headers.get('X-File-Name') or ''), self.rfile, n)
            except name_exe.ExeError as e:
                self.close_connection=True
                return self.reply(e.status, {'error': e.message})
            except OSError:
                return self.reply(500, {'error': 'exeを保存できませんでした。'})
            return self.reply(200, {'ok': True, 'exe': meta})
        try:
            n=int(self.headers.get('Content-Length','0'))
            limit=80000000 if route in ('/api/migration/inspect','/api/migration/restore') else 14000000 if route in ('/api/placements','/api/placements/save','/api/name-labels','/api/name-labels/save') else 9000000 if route in ('/api/templates/import','/api/shipments/import') else 1000000
            if n>limit: raise ValueError('データが大きすぎます。')
            d=json.loads(self.rfile.read(n))
            if not isinstance(d,dict): raise ValueError('入力内容を確認してください。')
            if route in ('/api/migration/inspect','/api/migration/restore'):
                try:
                    from contextlib import closing
                    with closing(connect()) as c:
                        with c:
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
                            summary, _manifest = migration.inspect_zip(
                                upload, DATA,
                                user_map=d.get('userMap'),
                                name_assignee_overrides=d.get('nameAssigneeOverrides'),
                            )
                            return self.reply(200, summary)
                        if d.get('confirm') is not True:
                            raise migration.MigrationError('復元するには、業務データを置き換えることに同意してください。')
                        result = migration.restore_from_zip(
                            DATA, upload, actor=actor, user_map=d.get('userMap'),
                            name_assignee_overrides=d.get('nameAssigneeOverrides'),
                        )
                        # Force client re-login.
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
                    import traceback
                    traceback.print_exc()
                    return self.reply(500, {'error': '復元処理でエラーが発生しました。詳しい原因をdata/startup.logに記録しました。'})
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
            if self.path=='/api/display-modes':
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    result=display_modes.handle_post(self,c,d)
                return self.reply(*result)
            if self.path=='/api/backup':
                dest=placement.make_backup(connect, DATA)
                return self.reply(200,{'message':'バックアップを作成しました。指示書のデータ、出荷情報、貼付け位置の画像、修正依頼掲示板を含みます。','filename':dest.name})
            if self.path=='/api/ship': return self.reply(*ship_order(self,d))
            if self.path=='/api/orders/flags': return self.reply(*tune_flags(self,d))
            if self.path=='/api/orders/debug': return self.reply(*debug_flags(self,d))
            if self.path=='/api/orders/printed': return self.reply(*mark_printed(self,d))
            if self.path=='/api/orders/print-flag': return self.reply(*print_flag(self,d))
            if self.path=='/api/orders/status-flag': return self.reply(*status_flag(self,d))
            if self.path=='/api/orders/complete': return self.reply(*complete_shipped(self,d))
            if self.path=='/api/orders/floor-progress': return self.reply(*floor_progress(self,d))
            if self.path=='/api/orders/due-confirm': return self.reply(*due_confirm(self,d))
            if self.path in ('/api/shipments','/api/shipments/save','/api/shipments/import'):
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    try:
                        if self.path=='/api/shipments/import':
                            raw=d.get('data')
                            if not isinstance(raw,str): raise ValueError('Excelファイル（.xlsx）を選んでください。')
                            name=str(d.get('filename') or '')
                            if name and not name.lower().endswith('.xlsx'): raise ValueError('Excelファイル（.xlsx）を選んでください。')
                            try: blob=base64.b64decode(raw)
                            except ValueError: raise ValueError('Excelファイル（.xlsx）を選んでください。')
                            result=shipping.import_book(self, c, blob)
                        else:
                            result=shipping.create(self, c, d) if self.path=='/api/shipments' else shipping.update(self, c, d)
                    except ValueError as e:
                        return self.reply(400, {'error':str(e)})
                return self.reply(*result)
            if self.path in ('/api/placements','/api/placements/save'):
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    result=placement.create(self, c, d) if self.path=='/api/placements' else placement.update(self, c, d)
                return self.reply(*result)
            if self.path in ('/api/placement-corrections','/api/placement-corrections/status'):
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    try:
                        result=placement.create_correction(self, c, d) if self.path=='/api/placement-corrections' else placement.update_correction(self, c, d)
                    except ValueError as e:
                        return self.reply(400, {'error':str(e)})
                return self.reply(*result)
            if self.path=='/api/name-labels/review':
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    result=names.name_review.action(self,c,d)
                return self.reply(*result)
            if self.path in ('/api/name-labels','/api/name-labels/save'):
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    result=names.create(self, c, d) if self.path=='/api/name-labels' else names.update(self, c, d)
                return self.reply(*result)
            if self.path=='/api/name-labels/delete':
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    result=names.soft_delete(self, c, d)
                return self.reply(*result)
            if self.path=='/api/name-labels/restore':
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    result=names.restore(self, c, d)
                return self.reply(*result)
            if self.path=='/api/kintai/import' or self.path.startswith('/api/kintai/'):
                kintai_path=self.path.split('?')[0]
                try:
                    with connect() as c:
                        c.execute('BEGIN IMMEDIATE')
                        result=kintai.handle_post(self, c, kintai_path, d)
                    return self.reply(*result)
                except (ValueError, TypeError, KeyError) as e:
                    return self.reply(400, {'error': str(e)})
                except Exception:
                    # 勤怠APIは汎用の容量案内に落とさず、入力保持を明示する（本文・氏名はログに出さない）
                    import logging
                    logging.getLogger('instruction-desk').exception('kintai api error path=%s', kintai_path)
                    if kintai_path in (
                        '/api/kintai/shift',
                        '/api/kintai/shift/default-order',
                        '/api/kintai/shift/work-memos',
                    ):
                        return self.reply(500, {'error': 'シフト表を保存できませんでした。入力内容はそのまま残しています。しばらくして再度お試しください。'})
                    if kintai_path in (
                        '/api/kintai/bihin',
                        '/api/kintai/bihin/items',
                        '/api/kintai/bihin/items/delete',
                        '/api/kintai/bihin/items/restore',
                        '/api/kintai/bihin/vendors',
                        '/api/kintai/bihin/order/settings',
                        '/api/kintai/bihin/order/prepare',
                        '/api/kintai/bihin/order/cancel',
                        '/api/kintai/bihin/orders',
                    ):
                        return self.reply(500, {'error': '備品棚卸を保存できませんでした。入力内容はそのまま残しています。しばらくして再度お試しください。'})
                    return self.reply(500, {'error': '勤怠の処理に失敗しました。入力内容はそのまま残しています。しばらくして再度お試しください。'})
            if self.path!='/api/orders': return self.reply(404,{'error':'見つかりません。'})
            payload=validate(d); now=datetime.now().isoformat(timespec='seconds')
            payload.pop('authorId',None); payload.pop('authorName',None)
            with connect() as c:
                c.execute('BEGIN IMMEDIATE')
                current=self.session_user(c)
                if not current or current['must_change']: return self.reply(401,{'error':'ログインし直してください。'})
                if current['role'] in ('floor2','floor3'): return self.reply(403,{'error':'この権限では指示書を変更できません。'})
                if current['role']=='floor': return self.reply(403,{'error':'現場の指示書は閲覧専用です。変更は営業担当者に依頼してください。'})
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
                    reattach_line_ids(payload.get('items') if isinstance(payload.get('items'), list) else [], previous.get('items'))
                    keep_due(payload, previous)
                    reconcile_shortages(payload)
                    refresh_due_for_mark(payload, previous)
                    apply_charge(c,payload,previous,current,False)
                    fix_received_and_number(c,payload,previous,d['id'])
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
                    for key in DUE_KEYS: payload.pop(key, None)
                    ensure_line_ids(payload.get('items') if isinstance(payload.get('items'), list) else [])
                    payload['authorId']=current['id']; payload['authorName']=current['name']
                    apply_charge(c,payload,previous,current,True)
                    fix_received_and_number(c,payload,previous,None)
                    if payload['status']!='下書き': check_posting(payload)
                    cur=c.execute('INSERT INTO orders(version,payload) VALUES(1,?)',(json.dumps(payload,ensure_ascii=False),)); oid=cur.lastrowid; action='下書きを作成' if payload['status']=='下書き' else '新規作成・投函'
                changes={k:{'before':previous.get(k),'after':payload.get(k)} for k in sorted(set(previous)|set(payload)) if k not in ('authorId','authorName','assigneeId','assigneeName') and previous.get(k)!=payload.get(k)}
                if 'shortages' in changes: changes['shortages']={'before':shortage_text(previous.get('shortages')),'after':shortage_text(payload.get('shortages'))}
                c.execute('INSERT INTO history(order_id,at,action,actor_id,actor,changes) VALUES(?,?,?,?,?,?)',(oid,now,action,current['id'],f"{current['name']} ({current['login']})",json.dumps(changes,ensure_ascii=False)))
                result=record(c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone())
            self.reply(200,result)
        except (ValueError,TypeError,KeyError) as e: self.reply(400,{'error':str(e)})
        except Exception: self.reply(500,{'error':'保存できませんでした。保存場所の空き容量や接続を確認してください。'})

def exe_drop_settings():
    """統合ネームラベルexeの置き場フォルダ。環境変数 > network.json > 既定（アプリ直下 name-label-exe-drop）。"""
    path=os.environ.get('NAME_LABEL_EXE_DROP') or CONFIG.get('nameLabelExeDrop') or str(ROOT/'name-label-exe-drop')
    flag=os.environ.get('NAME_LABEL_EXE_DROP_ENABLED')
    enabled=(flag.strip().lower() not in ('0','false','no','off','')) if flag is not None else bool(CONFIG.get('nameLabelExeDropEnabled', True))
    return Path(path), enabled
EXE_DROP_DIR, EXE_DROP_ENABLED = exe_drop_settings()

if __name__=='__main__':
    initialize()
    if EXE_DROP_ENABLED:
        name_exe.DropWatcher(DATA, EXE_DROP_DIR, log=lambda m: print(m, flush=True)).start()
        print(f'統合ネームラベルexeの置き場: {EXE_DROP_DIR}', flush=True)
    server=ThreadingHTTPServer((BIND_HOST,PORT),Handler)
    if CONFIG.get('tls'):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(ROOT / 'tls/server.crt', ROOT / 'tls/server.key')
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    print(f'{SCHEME}://127.0.0.1:{PORT}',flush=True)
    if '--open' in sys.argv: threading.Timer(.5,lambda:webbrowser.open(f'{SCHEME}://127.0.0.1:{PORT}')).start()
    server.serve_forever()
