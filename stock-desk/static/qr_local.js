/* Device-only QR jobs. Nothing is submitted until the operator presses Send. */
(function(){
 let dbPromise,profile=null,phone=false,queue=Promise.resolve();
 const fresh=()=>({jobs:[]});
 function database(){return dbPromise||(dbPromise=new Promise((resolve,reject)=>{
  const req=indexedDB.open('instruction-desk-qr-device',1);
  req.onupgradeneeded=()=>req.result.createObjectStore('records');
  req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(Error('端末に保存できません。ブラウザの保存設定を確認してください。'));
 }))}
 async function get(key){const db=await database();return new Promise((resolve,reject)=>{const tx=db.transaction('records');const req=tx.objectStore('records').get(key);req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error)})}
 async function write(key,value){const db=await database();return new Promise((resolve,reject)=>{const tx=db.transaction('records','readwrite');tx.objectStore('records').put(value,key);tx.oncomplete=()=>resolve(value);tx.onerror=()=>reject(Error('端末への保存に失敗しました。容量を確認してください。'));tx.onabort=()=>reject(Error('端末への保存が中断されました。'))})}
 async function change(fn,key='jobs:'+profile.id,initial=fresh){const db=await database();return new Promise((resolve,reject)=>{
  const tx=db.transaction('records','readwrite'),store=tx.objectStore('records');let result,failure;
  const req=store.get(key);req.onsuccess=()=>{try{const state=req.result||initial();result=fn(state);store.put(state,key)}catch(e){failure=e;tx.abort()}};
  tx.oncomplete=()=>resolve(result);tx.onerror=tx.onabort=()=>reject(failure||Error('端末に保存できませんでした。作業を閉じずに空き容量を確認してください。'));
 })}
 async function session(){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),4000);
  let r;try{r=await fetch('/api/me',{cache:'no-store',signal:controller.signal})}finally{clearTimeout(timer)}
  if(!r.ok)throw Error('ログインを確認できません。');
  const m=await r.json();if(!m.user||m.user.must_change){const e=Error('事務所のWi-Fiでログインしてください。');e.auth=true;throw e}return m.user;
 }
 async function init(user,isPhone=false){
  phone=isPhone;
  if(user){profile={id:String(user.id),name:user.name};await write('profile',profile)}
  else{
   try{const u=await session();profile={id:String(u.id),name:u.name};await write('profile',profile)}
   catch(e){if(e.auth)throw e;profile=await get('profile');if(!profile)throw Error('初回は事務所のWi-Fiでログインし、倉庫用の準備をしてください。')}
  }
  return profile;
 }
 async function verify(){const u=await session();if(String(u.id)!==profile.id)throw Error('保存した作業の担当者でログインし直してください。別の利用者として送信できません。')}
 function identify(raw){
  raw=String(raw||'').replace(/[\r\n]/g,'').trim();if(!raw||raw.length>400||/^(https?:|javascript:|data:)/i.test(raw)||/[\x00-\x1f\x7f]/.test(raw))throw Error('在庫用のQRを読み取ってください。');
  let identity={id:raw};if(raw.startsWith('{')){try{const o=JSON.parse(raw);if(o&&typeof o==='object'&&!Array.isArray(o))identity={id:String(o.id||o.legacyStockId||''),customer:o.customer_id,maker:o.maker_id}}catch{throw Error('QRの文字列を確認してください。')}}
  if(!identity.id)throw Error('QRの番号がありません。');return {raw,...identity,key:(raw.startsWith('nqr1.')?'new:':'legacy:')+identity.id};
 }
 function validCatalog(catalog){return !!catalog&&Array.isArray(catalog.entries)&&(catalog.entryCount===undefined||catalog.entryCount===catalog.entries.length)&&catalog.entries.every(e=>e&&['new','legacy'].includes(e.kind)&&typeof e.qr==='string'&&e.qr&&e.target&&Number.isInteger(e.target.customerProductId)&&e.target.customerProductId>0&&typeof e.target.productName==='string'&&e.target.productName.trim())}
 function missingQr(){const e=Error('このQRは商品情報と照合できません。枚数には加算していません。事務所のWi-Fiで「倉庫用の準備」を更新してください。更新後も出る場合はQRの対応登録を確認してください。');e.status=400;return e}
 async function resolveOnline(raw){
  if(navigator.onLine===false)return null;
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),2500);
  try{
   const response=await fetch('/inventory/api/qr-work/resolve',{method:'POST',headers:{'Content-Type':'application/json','X-Stock-Request':'1'},body:JSON.stringify({qr:raw}),signal:controller.signal});
   if(!response.ok)return null;
   const result=await response.json();const e=result.entry;
   if(!validCatalog({entries:[e],entryCount:1}))return null;
   await change(c=>{if(!validCatalog(c))throw missingQr();const index=c.entries.findIndex(x=>x.kind===e.kind&&x.qr===e.qr);if(index<0)c.entries.push(e);else c.entries[index]=e;c.entryCount=c.entries.length;c.revision=null;return true},'catalog:'+profile.id);
   return e;
  }catch{return null}finally{clearTimeout(timer)}
 }
 async function entry(raw){const x=identify(raw),catalog=await get('catalog:'+profile.id);
  if(!validCatalog(catalog))throw Error('商品情報が未準備、または保存した情報が不完全です。事務所のWi-Fiで「倉庫用の準備」を更新してください。枚数には加算していません。');
  let found=catalog.entries.find(e=>(e.kind==='new'?'new:':'legacy:')+e.qr===x.key);
  if(!found)found=await resolveOnline(x.raw);
  if(found&&((x.customer!==undefined&&String(x.customer)!==found.customer)||(x.maker!==undefined&&String(x.maker)!==found.maker)))throw Error('QRの顧客・メーカーが準備済みの商品情報と一致しません。');return {identity:x,found};
 }
 async function lookup(raw){const {found}=await entry(raw);if(!found)throw missingQr();return {found:true,qrKind:found.kind,...found.target,lots:[]}}
 async function state(){
  let local=await get('jobs:'+profile.id)||fresh();const catalog=await get('catalog:'+profile.id);
  const catalogOk=validCatalog(catalog);
  const targets=new Map(catalogOk?catalog.entries.map(e=>[(e.kind==='new'?'new:':'legacy:')+e.qr,e]):[]);
  // Keep old unidentified records for review, but never include them in totals.
  let needsRepair=false;
  for(const job of local.jobs){if(job.status==='sent')continue;for(const line of job.lines){
   let match;try{const identity=identify(line.qr);match=targets.get(identity.key);if(match&&((identity.customer!==undefined&&String(identity.customer)!==match.customer)||(identity.maker!==undefined&&String(identity.maker)!==match.maker)))match=null}catch{}
   if(!!line.unresolved!==!match||(match&&(line.productName!==match.target.productName||line.code!==match.target.code)))needsRepair=true;
  }}
  if(needsRepair){await change(s=>{for(const job of s.jobs){if(job.status==='sent')continue;for(const line of job.lines){
   let match;try{const identity=identify(line.qr);match=targets.get(identity.key);if(match&&((identity.customer!==undefined&&String(identity.customer)!==match.customer)||(identity.maker!==undefined&&String(identity.maker)!==match.maker)))match=null}catch{}
   line.unresolved=!match;if(match){for(const key of ['customerProductId','customerName','code','productName','color','spec','size','condition','sizes'])line[key]=match.target[key];line.unresolved=false;}
  }}return true});local=await get('jobs:'+profile.id)||fresh();}
  let remote={jobs:[],locations:[],canConfirm:false},connected=false;
  if(!phone){try{await verify();remote=await api('/api/qr-work/state',{inbox:true});connected=true}catch{}}
  const remoteIds=new Set(remote.jobs.map(j=>j.id));
  const jobs=[...local.jobs.filter(j=>phone||!remoteIds.has(j.id)),...(!phone?remote.jobs:[])];
  return {...remote,jobs,actorId:profile.id,localMode:true,catalogCount:catalogOk?catalog.entries.length:0,catalogOk,preparedAt:await get('prepared:'+profile.id)||catalog?.preparedAt||null,connected};
 }
 async function prepare(){
  const progress=text=>window.dispatchEvent(new CustomEvent('qr-prepare-progress',{detail:text}));
  progress('事務所のサーバーへの接続を確認しています…');
  await verify();
  progress('QRと商品情報を読み込んでいます…');
  const cached=await get('catalog:'+profile.id);
  const response=await api('/api/qr-work/catalog',{revision:validCatalog(cached)&&Number.isInteger(cached.entryCount)?cached.revision:undefined});
  const catalog=response.notModified&&cached?{...cached,preparedAt:response.preparedAt}:response;
  if(!validCatalog(catalog))throw Error('商品情報を正しく取得できませんでした。以前の保存情報は残しています。もう一度準備してください。');
  if(phone){
   progress('通信なしで使えるように画面を保存しています…');
   if(!('serviceWorker' in navigator))throw Error('このブラウザでは通信なしで画面を開く準備ができません。対応するブラウザで開いてください。');
   const registration=await navigator.serviceWorker.register('/inventory/qr-offline-sw.js',{scope:'/inventory/'});
   await navigator.serviceWorker.ready;
   const worker=registration.active||registration.waiting||registration.installing;
   if(!worker)throw Error('画面の保存を確認できません。もう一度準備してください。');
   await new Promise((resolve,reject)=>{const channel=new MessageChannel();const timer=setTimeout(()=>reject(Error('画面の保存に時間がかかっています。Wi-Fiを確認して再度準備してください。')),30000);channel.port1.onmessage=e=>{clearTimeout(timer);e.data?.ok?resolve():reject(Error('画面の保存ができませんでした。もう一度準備してください。'))};worker.postMessage({type:'prepare'},[channel.port2])});
  }
  progress(`商品情報 ${catalog.entries.length}件を端末に保存しています…`);
  if(!response.notModified)await write('catalog:'+profile.id,catalog);
  const persisted=await get('catalog:'+profile.id);
  if(!validCatalog(persisted)||persisted.entries.length!==catalog.entries.length||persisted.revision!==catalog.revision)throw Error('商品情報の保存確認に失敗しました。端末の空き容量を確認して準備し直してください。');
  // Preserve pre-offline jobs without submitting them. The same UUID and source
  // version prevent two devices importing the same old work as separate receipts.
  progress('以前の途中作業を確認しています…');
  const old=await api('/api/qr-work/state',{legacyOnly:true});
  const source=old.jobs.find(j=>j.status==='collecting'&&String(j.actor_id)===profile.id);
  let inherited=false;
  const local=await get('jobs:'+profile.id)||fresh();
  if(source&&!local.jobs.some(j=>j.id===source.id||j.status==='collecting')){
   const keys=new Map();
   for(let offset=0;offset<catalog.entries.length;offset+=128){
    await Promise.all(catalog.entries.slice(offset,offset+128).map(async e=>{
     const raw=e.kind==='new'?e.qr:'legacy:'+e.qr;
     const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)));
     keys.set([...bytes].map(n=>n.toString(16).padStart(2,'0')).join(''),e);
    }));
    progress(`以前の途中作業を照合しています… ${Math.min(offset+128,catalog.entries.length)} / ${catalog.entries.length}件`);
    if(source.lines.every(l=>keys.has(l.key)))break;
   }
   const lines=source.lines.map(l=>{const e=keys.get(l.key);return e?{...l,key:(e.kind==='new'?'new:':'legacy:')+e.qr,qr:e.qr}:null});
   if(lines.every(Boolean))inherited=await change(s=>{
    if(s.jobs.some(j=>j.id===source.id||j.status==='collecting'))return false;
    s.jobs.unshift({...source,version:0,_local:true,sourceVersion:source.version,lines});return true;
   });
  }
  if(navigator.storage?.persist)await navigator.storage.persist().catch(()=>false);
  await write('prepared:'+profile.id,catalog.preparedAt);
  return {message:`倉庫用の準備ができました（${catalog.entries.length}件）。この端末・同じブラウザで開いてください。${inherited?'以前の途中作業もこの端末に引き継ぎました（未送信）。':''}`};
 }
 async function dispatch(action,body={}){
  if(action==='text-state'){
   const local=await get('texts:'+profile.id)||{draft:'',items:[]};
   return local;
  }
  if(action==='text-draft'){
   if(typeof body.text!=='string'||body.text.length>50000)throw Error('文章は50000文字までです。');
   return change(s=>{s.draft=body.text;return {saved:true}},'texts:'+profile.id,()=>({draft:'',items:[]}));
  }
  if(action==='text-save'){
   if(typeof body.text!=='string'||!body.text.trim()||body.text.length>50000)throw Error('送る文章を入力してください（50000文字まで）。');
   return change(s=>{const item={id:crypto.randomUUID(),text:body.text,createdAt:new Date().toISOString(),sent:false};s.items.unshift(item);s.draft='';return {item}},'texts:'+profile.id,()=>({draft:'',items:[]}));
  }
  if(action==='text-send'){
   const local=await get('texts:'+profile.id),item=local?.items.find(x=>x.id===body.id);
   if(!item)throw Error('端末に保存した文章が見つかりません。');
   await verify();await api('/api/qr-work/text-send',{id:item.id,text:item.text});
   return change(s=>{const row=s.items.find(x=>x.id===item.id);row.sent=true;row.sentAt=new Date().toISOString();return {sent:true}},'texts:'+profile.id,()=>({draft:'',items:[]}));
  }
  if(action==='text-list'){await verify();return api('/api/qr-work/text-list',{})}
  if(action==='state')return state();
  if(action==='prepare')return prepare();
  const local=await get('jobs:'+profile.id)||fresh();const existing=local.jobs.find(j=>j.id===body.id);
  if(action==='scan'){
   const {identity,found}=await entry(body.qr);
   if(!found)throw missingQr();
   return change(s=>{
    let j=s.jobs.find(j=>j.status==='collecting');
    if(j&&body.id&&j.id!==body.id)throw Error('読み取り中の作業を選び直してください。');
    if(!j){j={id:crypto.randomUUID(),actor_id:profile.id,actor_name:profile.name,status:'collecting',version:0,direction:'outbound',started_at:new Date().toISOString(),completed_at:null,lines:[],_local:true};s.jobs.unshift(j)}
    if(body.id&&body.version!==j.version)throw Error('別の画面で更新されました。一覧を更新してください。');
    let line=j.lines.find(l=>l.key===identity.key);
    if(line){if(line.unresolved)throw missingQr();if(line.qty>=99999)throw Error('枚数は99999までです。');line.qty++;line.scanCount++}
    else{if(j.lines.length>=1000)throw Error('1回の作業は1000種類までです。');j.lines.push({...found.target,key:identity.key,qr:identity.raw,unitCost:'',qty:1,scanCount:1})}
    j.version++;return {job:j};
   });
  }
  if((action==='edit'||action==='complete')&&existing&&!body._remote){
   return change(s=>{const j=s.jobs.find(j=>j.id===body.id);if(j.status!=='collecting'||j.version!==body.version)throw Error('作業が更新されたか、すでに完了しています。一覧を更新してください。');
    if(!Array.isArray(body.lines)||body.lines.length!==j.lines.length)throw Error('明細を確認してください。');
    const updates=new Map(body.lines.map(l=>[l.key,l]));if(updates.size!==j.lines.length)throw Error('明細が重複しています。');
    j.lines=j.lines.filter(l=>{const u=updates.get(l.key);if(!u)throw Error('明細が変わりました。一覧を更新してください。');if(u.deleted===true)return false;const n=Number(u.qty);if(!Number.isInteger(n)||n<0||n>99999)throw Error('枚数は0～99999の整数で入力してください。');l.qty=n;return true});
    if(action==='complete'){if(j.lines.some(l=>l.unresolved))throw Error('未照合の行があります。準備を更新して照合するか、その行を削除してから完了してください。');if(!j.lines.some(l=>l.qty>0))throw Error('枚数が1以上の行を残してください。');j.status='completed';j.completed_at=new Date().toISOString()}
    j.version++;return {job:j};
   });
  }
  if(action==='submit'){
   if(!existing||!['completed','sent'].includes(existing.status))throw Error('先に作業を完了してください。');
   if(existing.lines.some(l=>l.unresolved))throw Error('未照合の行が残っています。商品情報を更新して照合してから送信してください。');
   await verify();
   const response=await api('/api/qr-work/submit',{id:existing.id,sourceVersion:existing.sourceVersion,startedAt:existing.started_at,completedAt:existing.completed_at,lines:existing.lines.map(l=>({qr:l.qr,qty:l.qty,scanCount:l.scanCount}))});
   return change(s=>{const j=s.jobs.find(j=>j.id===existing.id);j.status='sent';j.sent_at=new Date().toISOString();j.version++;return {job:j,receipt:response.job.id}});
  }
  if(existing&&!body._remote)throw Error('端末内の作業は「作業完了」のあと「事務所へ送信」してください。');
  return api('/api/qr-work/'+action,body);
 }
 function request(action,body){const task=queue.then(()=>dispatch(action,body));queue=task.catch(()=>{});return task}
 window.QrLocal={init,request,lookup,clearProfile:async()=>{await write('profile',null);profile=null},currentProfile:()=>profile};
})();
