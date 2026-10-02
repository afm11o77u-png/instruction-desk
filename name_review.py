"""Assigned-user confirmation of name-label specifications."""
import hashlib,json
from datetime import datetime

def schema(c):
 c.execute('CREATE TABLE IF NOT EXISTS name_reviews (format_id INTEGER PRIMARY KEY,assignee_id INTEGER,source_label TEXT NOT NULL DEFAULT "",confirmed_by INTEGER,confirmed_name TEXT,confirmed_at TEXT,confirmed_digest TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS name_review_history (id INTEGER PRIMARY KEY,format_id INTEGER,actor_id INTEGER,actor_name TEXT,action TEXT,at TEXT,digest TEXT)')
 # A change invalidates confirmation even if the content is later reverted.
 columns=[r['name'] for r in c.execute('PRAGMA table_info(name_formats)') if r['name']!='id']
 condition=' OR '.join('OLD."'+k+'" IS NOT NEW."'+k+'"' for k in columns)
 c.execute('CREATE TRIGGER IF NOT EXISTS name_review_format_changed AFTER UPDATE ON name_formats WHEN '+condition+' BEGIN UPDATE name_reviews SET confirmed_digest=NULL WHERE format_id=NEW.id; END')
 for event,ref in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
  c.execute('CREATE TRIGGER IF NOT EXISTS name_review_sample_'+event.lower()+' AFTER '+event+' ON name_samples BEGIN UPDATE name_reviews SET confirmed_digest=NULL WHERE format_id='+ref+'.format_id; END')
 c.execute('CREATE TRIGGER IF NOT EXISTS name_review_customer_changed AFTER UPDATE ON name_customers WHEN OLD.source_name IS NOT NEW.source_name OR OLD.reading IS NOT NEW.reading BEGIN UPDATE name_reviews SET confirmed_digest=NULL WHERE format_id IN (SELECT id FROM name_formats WHERE customer_id=NEW.id); END')

def digest(c,fid):
 row=c.execute('SELECT * FROM name_formats WHERE id=? AND retired=0',(fid,)).fetchone()
 if not row:return None
 customer=c.execute('SELECT source_name,reading FROM name_customers WHERE id=?',(row['customer_id'],)).fetchone()
 samples=[dict(r) for r in c.execute('SELECT * FROM name_samples WHERE format_id=? ORDER BY id',(fid,))]
 return hashlib.sha256(json.dumps([dict(row),list(customer),samples],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def state(c,fid,user):
 r=c.execute('SELECT r.*,u.name,u.active FROM name_reviews r LEFT JOIN users u ON u.id=r.assignee_id WHERE format_id=?',(fid,)).fetchone()
 token=digest(c,fid)
 assigned=bool(r and r['assignee_id'] and r['active'])
 confirmed=bool(assigned and r['confirmed_by']==r['assignee_id'] and r['confirmed_digest']==token)
 status='了承' if confirmed else ('再確認待ち' if r and r['confirmed_at'] else '未了承')
 return {'assigneeId':r['assignee_id'] if r else None,'assigneeName':r['name'] if r and r['name'] else '', 'sourceLabel':r['source_label'] if r else '', 'status':status,'confirmedAt':r['confirmed_at'] if confirmed else None,'confirmedName':r['confirmed_name'] if confirmed else None,'previousConfirmedAt':r['confirmed_at'] if r else None,'canConfirm':bool(assigned and user and user['active'] and not user['must_change'] and user['id']==r['assignee_id'] and not confirmed),'digest':token}

def action(handler,c,d):
 user=handler.session_user(c)
 if not user or not user['active'] or user['must_change']:return 401,{'error':'ログインし直してください。'}
 try:fid=int(d.get('id'))
 except (ValueError,TypeError):return 400,{'error':'書式が見つかりません。'}
 token=digest(c,fid)
 if token is None:return 404,{'error':'書式が見つかりません。'}
 now=datetime.now().isoformat(timespec='seconds')
 if d.get('action')=='assign':
  if user['role']!='owner':return 403,{'error':'担当者を変更できるのは主管理者です。'}
  try:uid=int(d.get('assigneeId')) if d.get('assigneeId') else None
  except (ValueError,TypeError):return 400,{'error':'担当者を選択してください。'}
  if uid and not c.execute("SELECT id FROM users WHERE id=? AND active=1 AND login NOT LIKE '*name:%'",(uid,)).fetchone():return 400,{'error':'有効な利用者を選択してください。'}
  old=c.execute('SELECT assignee_id FROM name_reviews WHERE format_id=?',(fid,)).fetchone()
  if not old or old[0]!=uid:
   c.execute('INSERT INTO name_reviews(format_id,assignee_id) VALUES(?,?) ON CONFLICT(format_id) DO UPDATE SET assignee_id=excluded.assignee_id,confirmed_digest=NULL',(fid,uid))
   c.execute('INSERT INTO name_review_history(format_id,actor_id,actor_name,action,at,digest) VALUES(?,?,?,?,?,?)',(fid,user['id'],user['name'],'担当者変更:'+str(uid),now,token))
 else:
  r=c.execute('SELECT * FROM name_reviews WHERE format_id=?',(fid,)).fetchone()
  if not r or r['assignee_id']!=user['id']:return 403,{'error':'指定された担当者本人だけが確認できます。'}
  if d.get('digest')!=token:return 409,{'error':'内容が変更されました。開き直して確認してください。'}
  if r['confirmed_digest']!=token or r['confirmed_by']!=user['id']:
   c.execute('UPDATE name_reviews SET confirmed_by=?,confirmed_name=?,confirmed_at=?,confirmed_digest=? WHERE format_id=?',(user['id'],user['name'],now,token,fid))
   c.execute('INSERT INTO name_review_history(format_id,actor_id,actor_name,action,at,digest) VALUES(?,?,?,?,?,?)',(fid,user['id'],user['name'],'内容を確認',now,token))
 return 200,state(c,fid,user)
