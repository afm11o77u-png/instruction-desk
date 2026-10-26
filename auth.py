"""Local authentication and account administration. No external services."""
import hashlib, hmac, secrets, time, re, sqlite3, json
from http.cookies import SimpleCookie

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError('パスワードは12～128文字で入力してください。')
    return _hash(password)

def gate_hash(password):
    if not isinstance(password, str) or not 4 <= len(password) <= 16:
        raise ValueError('確認用のパスワードは4～16文字で入力してください。')
    return _hash(password)

def _hash(password):
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()
    return salt + ':' + value

def verify(password, stored):
    if not isinstance(password, str) or len(password)>128: return False
    salt, expected = stored.split(':')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()
    return hmac.compare_digest(actual, expected)

NAME_ONLY='*name:'

def is_name_only(login):
    return isinstance(login,str) and login.startswith(NAME_ONLY)

def new_name_only_login():
    return NAME_ONLY+secrets.token_urlsafe(18)

def login_label(login):
    return 'ログインなし' if is_name_only(login) else login

def public(u):
    if not u: return None
    keys=('id','login','name','role','manage','overview','active','must_change','version')
    data={k:u[k] for k in keys}
    # マーク管理（勤怠）: 初期OFF。列が無い旧DBでも公開形は 0
    try:
        data['mark_kintai']=int(bool(u['mark_kintai']))
    except (KeyError, IndexError, TypeError):
        data['mark_kintai']=0
    if is_name_only(data['login']):
        data['login']=''; data['nameOnly']=True
    return data

def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, login TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, manage INTEGER NOT NULL DEFAULT 0, overview INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1)')
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_owner ON users(role) WHERE role='owner'")
    if 'overview' not in {r['name'] for r in c.execute('PRAGMA table_info(users)')}:
        c.execute('ALTER TABLE users ADD COLUMN overview INTEGER NOT NULL DEFAULT 0')
    cols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
    if 'gate' not in cols: c.execute('ALTER TABLE users ADD COLUMN gate TEXT')
    if 'mark_kintai' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN mark_kintai INTEGER NOT NULL DEFAULT 0')
    c.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS login_gates (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS auth_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor_id INTEGER, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL)')
    cols={r['name'] for r in c.execute('PRAGMA table_info(history)')}
    for key,typ in [('actor_id','INTEGER'),('actor','TEXT'),('changes','TEXT')]:
        if key not in cols: c.execute(f'ALTER TABLE history ADD COLUMN {key} {typ}')

def log(c,u,action,target):
    c.execute("INSERT INTO audit(at,actor_id,actor,action,target) VALUES(datetime('now','localtime'),?,?,?,?)", (u['id'] if u else None, f"{u['name']} ({u['login']})" if u else '初回設定',action,str(target)))

def limited(c,key):
    row=c.execute('SELECT * FROM auth_attempts WHERE key=?',(key,)).fetchone()
    return row and row['count']>=5 and row['until']>time.time()

def failure(c,key):
    old=c.execute('SELECT * FROM auth_attempts WHERE key=?',(key,)).fetchone()
    count=old['count']+1 if old and old['until']>time.time() else 1
    c.execute('INSERT OR REPLACE INTO auth_attempts VALUES(?,?,?)',(key,count,time.time()+300))

def login_value(value, existing=None):
    if not isinstance(value, str): raise ValueError('ログインIDはメールアドレスを80文字以内で入力してください。')
    login=value.strip()
    if existing is not None and login.casefold()==existing.casefold(): return existing
    if not re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', login) or not 6<=len(login)<=80:
        raise ValueError('ログインIDはメールアドレスを80文字以内で入力してください。')
    return login

def identity(d):
    login=login_value(d.get('login','')); name=d.get('name','').strip()
    if not 1<=len(name)<=80: raise ValueError('氏名を80文字以内で入力してください。')
    return login,name

class AuthMixin:
    def session_user(self,c):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie','')); token=cookie['desk_session'].value
        except (KeyError, Exception): return None
        return c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1',(digest(token),time.time())).fetchone()

    def new_session(self,c,uid):
        token=secrets.token_urlsafe(32)
        c.execute('DELETE FROM sessions WHERE expires<=?',(time.time(),))
        c.execute('INSERT INTO sessions VALUES(?,?,?)',(digest(token),uid,time.time()+28800))
        self.cookie=[f'desk_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800','desk_gate=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0']

    def gate_user(self,c):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie','')); token=cookie['desk_gate'].value
        except (KeyError, Exception): return None
        row=c.execute('SELECT user_id FROM login_gates WHERE token=? AND expires>?',(digest(token),time.time())).fetchone()
        if not row: return None
        return c.execute('SELECT * FROM users WHERE id=? AND active=1 AND role=?',(row['user_id'],'owner')).fetchone()

    def new_gate(self,c,uid):
        token=secrets.token_urlsafe(32)
        c.execute('DELETE FROM login_gates WHERE expires<=? OR user_id=?',(time.time(),uid))
        c.execute('INSERT INTO login_gates VALUES(?,?,?)',(digest(token),uid,time.time()+180))
        self.cookie=f'desk_gate={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=180'

    def clear_gate(self,c):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie','')); token=cookie['desk_gate'].value
            c.execute('DELETE FROM login_gates WHERE token=?',(digest(token),))
        except (KeyError, Exception): pass
        self.cookie='desk_gate=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'

    def auth_get(self,path,c):
        if path=='/api/me':
            u=self.session_user(c)
            return 200,{'setup':not c.execute('SELECT 1 FROM users LIMIT 1').fetchone(),'user':public(u) if u else None}
        u=self.session_user(c)
        if not u: return 401,{'error':'ログインしてください。'}
        if u['must_change']: return 403,{'error':'最初に仮パスワードを変更してください。'}
        self.user=u
        if path in ['/api/users','/api/audit']:
            if not (u['role']=='owner' or u['manage']): return 403,{'error':'利用者管理の権限が必要です。'}
            if path=='/api/users': return 200,[public(r) for r in c.execute('SELECT * FROM users ORDER BY id')]
            return 200,[dict(r) for r in c.execute('SELECT at,actor,action,target FROM audit ORDER BY id DESC LIMIT 200')]
        return None

    def auth_post(self,path,d,c):
        u=self.session_user(c)
        if path=='/api/setup':
            if c.execute('SELECT 1 FROM users LIMIT 1').fetchone(): return 409,{'error':'初回設定は完了しています。ログインしてください。'}
            login,name=identity(d); hashed=password_hash(d.get('password')); gate=gate_hash(d.get('gate'))
            if d.get('password')==d.get('gate'): raise ValueError('確認用のパスワードは、ログインパスワードとは別にしてください。')
            if d.get('gate')!=d.get('gateConfirm'): raise ValueError('確認用パスワードが一致しません。')
            uid=c.execute("INSERT INTO users(login,name,password,role,manage,gate) VALUES(?,?,?,'owner',1,?)",(login,name,hashed,gate)).lastrowid
            code=secrets.token_urlsafe(24)
            c.execute("INSERT OR REPLACE INTO settings VALUES('recovery',?)",(digest(code),))
            u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();log(c,u,'主管理者を作成',login);self.new_session(c,uid)
            return 200,{'user':public(u),'recovery':code}
        if path in ['/api/login','/api/recover']:
            key='login:'+self.client_address[0] if path.endswith('login') else 'recovery:'+self.client_address[0]
            if limited(c,key): return 429,{'error':'試行回数が上限に達しました。5分待ってからやり直してください。'}
            if path.endswith('login'):
                u=c.execute('SELECT * FROM users WHERE login=?',(str(d.get('login','')).strip()[:80],)).fetchone()
                if not u or not u['active'] or is_name_only(u['login']) or not verify(d.get('password',''),u['password']):
                    failure(c,key);return 401,{'error':'IDまたはパスワードが違うか、利用停止されています。'}
                c.execute('DELETE FROM auth_attempts WHERE key=?',(key,))
                if u['role']=='owner' and u['gate']:
                    self.new_gate(c,u['id']); log(c,u,'ログインの確認待ち',u['login'])
                    return 200,{'gate':True,'needSetup':not u['gate']}
                self.new_session(c,u['id']);log(c,u,'ログイン',u['login'])
                return 200,{'user':public(u)}
            r=c.execute("SELECT value FROM settings WHERE key='recovery'").fetchone()
            if not r or not hmac.compare_digest(r['value'],digest(str(d.get('code','')))):
                failure(c,key);return 401,{'error':'復旧コードが違います。'}
            u=c.execute("SELECT * FROM users WHERE role='owner'").fetchone()
            hashed=password_hash(d.get('password')); gate=gate_hash(d.get('gate')); code=secrets.token_urlsafe(24)
            if d.get('password')==d.get('gate'): raise ValueError('確認用のパスワードは、ログインパスワードとは別にしてください。')
            if d.get('gate')!=d.get('gateConfirm'): raise ValueError('確認用パスワードが一致しません。')
            c.execute('UPDATE users SET password=?,gate=?,must_change=0,version=version+1 WHERE id=?',(hashed,gate,u['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],));c.execute("UPDATE settings SET value=? WHERE key='recovery'",(digest(code),));c.execute('DELETE FROM auth_attempts WHERE key=?',(key,));log(c,u,'復旧コードで主管理者パスワードを再設定',u['login'])
            return 200,{'recovery':code,'login':u['login']}
        if path=='/api/login-cancel':
            self.clear_gate(c); return 200,{'ok':True}
        if path=='/api/login-gate':
            key='gate:'+self.client_address[0]
            if limited(c,key):
                self.clear_gate(c); return 429,{'error':'試行回数が上限に達しました。5分待ってからやり直してください。'}
            owner=self.gate_user(c)
            if not owner: return 401,{'error':'ログインからやり直してください。'}
            if not owner['gate']:
                if d.get('gate')!=d.get('confirm'): raise ValueError('確認用パスワードが一致しません。')
                hashed=gate_hash(d.get('gate'))
                c.execute('UPDATE users SET gate=?,version=version+1 WHERE id=?',(hashed,owner['id']))
                log(c,owner,'確認用パスワードを設定',owner['login'])
            elif not verify(d.get('gate',''), owner['gate']):
                failure(c,key); return 401,{'error':'確認用のパスワードが違います。'}
            c.execute('DELETE FROM login_gates WHERE user_id=?',(owner['id'],))
            c.execute('DELETE FROM auth_attempts WHERE key=?',(key,))
            self.new_session(c,owner['id']); log(c,owner,'ログイン',owner['login'])
            return 200,{'user':public(owner)}
        if not u: return 401,{'error':'ログインしてください。'}
        if path=='/api/logout':
            cookie=SimpleCookie(self.headers.get('Cookie',''));c.execute('DELETE FROM sessions WHERE token=?',(digest(cookie['desk_session'].value),));self.cookie='desk_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'
            return 200,{'ok':True}
        if path=='/api/password':
            if not verify(d.get('current',''),u['password']): return 400,{'error':'現在のパスワードが違います。'}
            if d.get('current')==d.get('password'): raise ValueError('現在とは違うパスワードを設定してください。')
            hashed=password_hash(d.get('password'))
            gate=u['gate']
            if u['role']=='owner' and str(d.get('gate') or '').strip():
                if not u['gate'] or not verify(d.get('currentGate',''), u['gate']): return 400,{'error':'現在の確認用パスワードが違います。'}
                if d.get('gate')!=d.get('gateConfirm'): raise ValueError('確認用パスワードが一致しません。')
                if d.get('password')==d.get('gate'): raise ValueError('確認用のパスワードは、ログインパスワードとは別にしてください。')
                gate=gate_hash(d.get('gate'))
            c.execute('UPDATE users SET password=?,gate=?,must_change=0,version=version+1 WHERE id=?',(hashed,gate,u['id']));c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],));self.new_session(c,u['id']);log(c,u,'自分のパスワードを変更',u['login'])
            return 200,{'ok':True}
        if u['must_change']: return 403,{'error':'最初に仮パスワードを変更してください。'}
        self.user=u
        if path.startswith('/api/users/'):
            if not (u['role']=='owner' or u['manage']): return 403,{'error':'利用者管理の権限が必要です。'}
            if path=='/api/users/create':
                role=d.get('role')
                if role not in ['sales','floor','floor2']: raise ValueError('業務権限を選択してください。')
                raw_login=d.get('login','')
                if not isinstance(raw_login,str): raise ValueError('ログインIDはメールアドレスを80文字以内で入力してください。')
                raw_login=raw_login.strip()
                name=d.get('name','')
                if not isinstance(name,str): raise ValueError('氏名を確認してください。')
                name=name.strip()
                if not 1<=len(name)<=80: raise ValueError('氏名を80文字以内で入力してください。')
                admin=bool(d.get('admin') or d.get('manage') or d.get('overview'))
                if admin and u['role']!='owner': return 403,{'error':'管理者を付与できるのは主管理者だけです。'}
                if role=='floor2' and admin: raise ValueError('現場２に管理者は付けられません。')
                name_only=role=='floor' and raw_login==''
                if name_only and admin: raise ValueError('ログインしない作業者は管理者にできません。')
                if not name_only and raw_login=='': raise ValueError('メールアドレスが必要です。現場の作業者だけ空欄にできます。')
                login=new_name_only_login() if name_only else login_value(raw_login)
                temp=None if name_only else secrets.token_urlsafe(15)
                # mark_kintai は初期OFF（推測でONにしない）
                try: uid=c.execute('INSERT INTO users(login,name,password,role,manage,overview,must_change,mark_kintai) VALUES(?,?,?,?,?,?,?,0)',(login,name,password_hash(temp or secrets.token_urlsafe(18)),role,int(admin),int(admin),0 if name_only else 1)).lastrowid
                except sqlite3.IntegrityError: raise ValueError('そのメールアドレスはすでに登録されています。')
                log(c,u,('作業者を氏名だけ追加' if name_only else '利用者を追加')+('・管理者を付与' if admin else ''),name if name_only else login)
                if name_only: return 200,{'nameOnly':True,'name':name,'login':''}
                return 200,{'temporary':temp,'login':login}
            target=c.execute('SELECT * FROM users WHERE id=?',(d.get('id'),)).fetchone()
            if not target: return 404,{'error':'利用者が見つかりません。'}
            if d.get('version')!=target['version']: return 409,{'error':'利用者情報が更新されています。一覧を開き直してください。'}
            if path=='/api/users/reset':
                if is_name_only(target['login']): return 400,{'error':'メールアドレスがないため、パスワードは発行できません。メールアドレスを入れて保存すると、仮パスワードを発行します。'}
                if target['role']=='owner' or target['id']==u['id'] or (u['role']!='owner' and target['manage']): return 403,{'error':'このアカウントは操作できません。自分のパスワードは専用画面で変更してください。'}
                temp=secrets.token_urlsafe(15);c.execute('UPDATE users SET password=?,must_change=1,version=version+1 WHERE id=?',(password_hash(temp),target['id']));c.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],));log(c,u,'仮パスワードを再発行',target['login'])
                return 200,{'temporary':temp,'login':target['login']}
            if path=='/api/users/update':
                name=d.get('name','')
                if not isinstance(name,str): raise ValueError('氏名を確認してください。')
                name=name.strip()
                if not 1<=len(name)<=80: raise ValueError('氏名を80文字以内で入力してください。')
                if target['id']==u['id']:
                    if u['role']!='owner': return 403,{'error':'自分の氏名を変更できるのは主管理者だけです。'}
                    login=login_value(d.get('login', target['login']), target['login']) if 'login' in d else target['login']
                    if name==target['name'] and login==target['login']: return 200,{'ok':True,'name':name,'login':login}
                    try: c.execute('UPDATE users SET name=?,login=?,version=version+1 WHERE id=?',(name,login,target['id']))
                    except sqlite3.IntegrityError: raise ValueError('そのメールアドレスはすでに登録されています。')
                    if login!=target['login']:
                        c.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],)); self.new_session(c,target['id'])
                        log(c,u,f"ログインIDを変更：{target['login']} → {login}",login)
                    if name!=target['name']: log(c,u,f"氏名を変更：{target['name']} → {name}",login)
                    return 200,{'ok':True,'name':name,'login':login}
                if target['role']=='owner' or (u['role']!='owner' and target['manage']): return 403,{'error':'このアカウントは操作できません。自分のパスワードは専用画面で変更してください。'}
                role=d.get('role');active=int(bool(d.get('active')))
                if u['role']=='owner' and 'admin' in d: admin=int(bool(d.get('admin')))
                elif u['role']=='owner' and ('manage' in d or 'overview' in d): admin=int(bool(d.get('manage') or d.get('overview')))
                else: admin=int(bool(target['manage'] or target['overview']))
                manage=admin; overview=admin
                if role not in ['sales','floor','floor2']: raise ValueError('業務権限を選択してください。')
                if role=='floor2' and admin: raise ValueError('現場２に管理者は付けられません。')
                if u['role']=='owner' and 'login' in d:
                    raw_login=d.get('login')
                    if not isinstance(raw_login,str): raise ValueError('ログインIDはメールアドレスを80文字以内で入力してください。')
                    raw_login=raw_login.strip()
                    if raw_login=='':
                        if role!='floor': raise ValueError('メールアドレスが必要です。現場の作業者だけ空欄にできます。')
                        login=target['login'] if is_name_only(target['login']) else new_name_only_login()
                    else:
                        login=login_value(raw_login, None if is_name_only(target['login']) else target['login'])
                else:
                    login=target['login']
                if is_name_only(login) and admin: raise ValueError('ログインしない作業者は管理者にできません。')
                if u['role']!='owner' and admin!=int(bool(target['manage'] or target['overview'])): return 403,{'error':'管理者の変更は主管理者だけができます。'}
                if u['role']!='owner' and 'login' in d and str(d.get('login','')).strip().casefold()!=( '' if is_name_only(target['login']) else target['login'].casefold()): return 403,{'error':'ログインIDを変更できるのは主管理者だけです。'}
                issued=secrets.token_urlsafe(15) if is_name_only(target['login']) and not is_name_only(login) else None
                became_name_only=not is_name_only(target['login']) and is_name_only(login)
                # マーク管理は利用者管理権限者のみ変更可（この分岐に到達済み）。未送信時は現状維持
                if 'mark_kintai' in d:
                    mark_kintai=int(bool(d.get('mark_kintai')))
                else:
                    try: mark_kintai=int(bool(target['mark_kintai']))
                    except (KeyError, IndexError, TypeError): mark_kintai=0
                try: prev_mark=int(bool(target['mark_kintai']))
                except (KeyError, IndexError, TypeError): prev_mark=0
                access_changed=role!=target['role'] or manage!=target['manage'] or overview!=target['overview'] or active!=target['active'] or login!=target['login'] or mark_kintai!=prev_mark
                try:
                    if issued: c.execute('UPDATE users SET login=?,name=?,password=?,role=?,manage=?,overview=?,active=?,mark_kintai=?,must_change=1,version=version+1 WHERE id=?',(login,name,password_hash(issued),role,manage,overview,active,mark_kintai,target['id']))
                    elif became_name_only: c.execute('UPDATE users SET login=?,name=?,password=?,role=?,manage=?,overview=?,active=?,mark_kintai=?,must_change=0,version=version+1 WHERE id=?',(login,name,password_hash(secrets.token_urlsafe(18)),role,manage,overview,active,mark_kintai,target['id']))
                    else: c.execute('UPDATE users SET login=?,name=?,role=?,manage=?,overview=?,active=?,mark_kintai=?,version=version+1 WHERE id=?',(login,name,role,manage,overview,active,mark_kintai,target['id']))
                except sqlite3.IntegrityError: raise ValueError('そのメールアドレスはすでに登録されています。')
                if access_changed or issued or became_name_only: c.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],))
                log(c,u,f"利用者設定を変更：ログインID {login_label(target['login'])} → {login_label(login)}、氏名 {target['name']} → {name}、業務 {target['role']} → {role}、管理者 {int(bool(target['manage'] or target['overview']))} → {admin}、マーク管理 {prev_mark} → {mark_kintai}、有効 {target['active']} → {active}",login_label(login))
                if issued: return 200,{'ok':True,'name':name,'temporary':issued,'login':login}
                return 200,{'ok':True,'name':name}
            return 404,{'error':'操作が見つかりません。'}
        if path=='/api/backup' and u['role']!='owner': return 403,{'error':'バックアップは主管理者だけが作成できます。'}
        if path.startswith('/api/migration/') and (not u or u['role']!='owner'):
            return 403,{'error':'移行用バックアップは主管理者だけが使用できます。'}
        return None
