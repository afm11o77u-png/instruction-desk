// Real browser, service worker and IndexedDB; synthetic HTTP API, never live stock.
const http=require('http'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.QR_TEST_PLAYWRIGHT||'playwright');
const folder=path.join(__dirname,'../static');
(async()=>{
 let textMessages=[],received=[],stock=20,uploads=0,loseReceipt=false,denied=false,catalogRelease=null,catalogRequests=0,assetReads=0;
 const legacyId=require('crypto').randomUUID();
 const newQr='nqr1.'+'a'.repeat(32),missingQr='nqr1.'+'b'.repeat(32),lateQr='nqr1.'+'c'.repeat(32);
 const oldPayload=JSON.stringify({id:'offline-qr',customer_id:'',maker_id:''});
 const target={customerProductId:1,customerName:'検証顧客',code:'OFF-1',productName:'白衣',size:'M',condition:'new',sizes:['M'],unitCost:'',color:'白',spec:'標準',lots:[],locationId:1};
 const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');const json=(body,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body))};
  if(url.pathname==='/api/me')return json({user:denied?null:{id:req.headers.cookie?.includes('office=1')?1:12,name:'検証担当',role:'owner'}});
  if(url.pathname.startsWith('/inventory/api/qr-work/')){
   if(req.headers['x-stock-request']!=='1')return json({error:'header'},400);
   let raw='';for await(const chunk of req)raw+=chunk;const b=JSON.parse(raw||'{}'),action=url.pathname.split('/').pop();
   if(action==='text-send'){let item=textMessages.find(x=>x.id===b.id);if(!item){item={id:b.id,body:b.text,actor_name:'検証担当',received_at:'2026-11-19T01:00:00'};textMessages.push(item)}return json({message:item})}
   if(action==='text-list')return json({messages:textMessages});
   if(action==='resolve'){if(b.qr!==lateQr)return json({error:'未照合'},400);return json({entry:{qr:lateQr,kind:'new',target:{...target,productName:'追加確認した制服'}}})}
   if(action==='catalog'){catalogRequests++;if(b.revision==='catalog-1')return json({notModified:true,revision:'catalog-1',entryCount:2,preparedAt:'2026-11-19'});if(catalogRequests===1)await new Promise(resolve=>catalogRelease=resolve);return json({revision:'catalog-1',entryCount:2,entries:[{qr:'offline-qr',kind:'legacy',customer:'',maker:'',target},{qr:newQr,kind:'new',target:{...target,productName:'新方式で登録済みの制服'}}],preparedAt:'2026-11-19'});}
   if(action==='state')return json({jobs:b.inbox?received:[...received,{id:legacyId,actor_id:'12',actor_name:'検証担当',version:1,status:'collecting',direction:'outbound',started_at:'2026-11-19T00:00:00Z',lines:[{...target,qty:0,scanCount:1,key:require('crypto').createHash('sha256').update('legacy:offline-qr').digest('hex')}]}],locations:[{id:1,name:'検証倉庫'}],actorId:'1',canConfirm:true});
   if(action==='submit'){
    uploads++;if(b.id===legacyId)assert.equal(b.sourceVersion,1);
    let job=received.find(j=>j.id===b.id);
    if(!job){job={id:b.id,status:'completed',version:1,direction:'outbound',actor_id:'12',actor_name:'検証担当',started_at:b.startedAt,completed_at:b.completedAt,lines:b.lines.map(l=>({...target,...l,key:'server-row'}))};received.push(job)}
    if(loseReceipt){loseReceipt=false;return json({error:'受信確認を返せませんでした（検証）'},503)}
    return json({job});
   }
   if(action==='edit'){const job=received.find(j=>j.id===b.id);job.lines=b.lines.map(l=>({...job.lines[0],...l,qty:Number(l.qty)}));job.version++;return json({job})}
   if(action==='confirm'){const job=received.find(j=>j.id===b.id);if(job.status!=='confirmed'){stock-=job.lines.reduce((n,l)=>n+l.qty,0);job.status='confirmed';job.version++}return json({job})}
   return json({error:'Unexpected '+action},400);
  }
  if(url.pathname==='/pc'){
   res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});
   return res.end(`<!doctype html><meta charset="utf-8"><main id="main"></main><script>async function api(p,d){let r=await fetch('/inventory'+p,{method:'POST',headers:{'Content-Type':'application/json','X-Stock-Request':'1'},body:JSON.stringify(d)});let b=await r.json();if(!r.ok)throw Error(b.error);return b}</script><script src="/inventory/qr_local.js"></script><script src="/inventory/qr_work.js"></script><script src="/inventory/qr_text.js"></script><script src="/inventory/qr_read.js"></script><script>QrRead.page()</script>`);
  }
  if(url.pathname!=='/inventory/qr-offline-sw.js')assetReads++;
  const file=path.resolve(folder,url.pathname.replace('/inventory/',''));
  if(!file.startsWith(path.resolve(folder)+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);return res.end()}
  res.writeHead(200,{'Content-Type':file.endsWith('.js')?'text/javascript; charset=utf-8':file.endsWith('.css')?'text/css; charset=utf-8':'text/html; charset=utf-8'});res.end(fs.readFileSync(file));
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const base=`http://127.0.0.1:${server.address().port}`;
 let browser;
 try{
  browser=await chromium.launch({headless:true,executablePath:process.env.QR_TEST_CHROME||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const context=await browser.newContext({viewport:{width:390,height:844}}),office=await browser.newContext();
  const page=await context.newPage(),pc=await office.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await office.addCookies([{name:'office',value:'1',url:base}]);
  await page.goto(base+'/inventory/qr-mobile.html');await page.locator('[data-prepare]').waitFor({state:'attached'});
  await page.locator('summary').filter({hasText:'倉庫へ行く前の準備'}).click();await page.locator('[data-prepare]').click();
  await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.classList.contains('is-loading'));
  assert.equal(await page.locator('[data-prepare]').innerText(),'準備中…');
  assert.equal(await page.locator('[data-prepare]').isDisabled(),true);
  await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.textContent.includes('QRと商品情報'));
  assert(catalogRelease);catalogRelease();
  await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.textContent.includes('準備ができました'));
  assert.equal(await page.locator('#qrWorkMessage').getAttribute('class'),null);
  const beforeAssets=assetReads;
  await page.locator('summary').filter({hasText:'倉庫へ行く前の準備'}).click();await page.locator('[data-prepare]').click();
  await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.textContent.includes('準備ができました'));
  assert.equal(catalogRequests,2);assert.equal(assetReads,beforeAssets,'prepared screen assets must not be downloaded twice');
  await page.reload();await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
  await context.setOffline(true);await page.reload();await page.locator('#qrCamToggle').waitFor();
  await page.waitForFunction(()=>document.querySelector('[data-jobs]'));
  const beforeInvalid=await page.evaluate(()=>QrLocal.request('state'));
  assert.equal(await page.evaluate(qr=>QrRead.lookup(qr),missingQr),false);
  assert((await page.locator('#qrScanResult').innerText()).includes('加算していません'));
  const afterInvalid=await page.evaluate(()=>QrLocal.request('state'));assert.deepEqual(afterInvalid.jobs,beforeInvalid.jobs);
  assert.equal(await page.evaluate(async qr=>{try{await QrLocal.request('scan',{qr,token:crypto.randomUUID()});return false}catch{return true}},missingQr),true);
  await page.evaluate(qr=>QrRead.lookup(qr),oldPayload);await page.evaluate(()=>QrRead.lookup('offline-qr'));
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'2');assert.equal(received.length,0);
  assert.equal(await page.locator('[data-save]').count(),0);
  await page.locator('[data-field="qty"]').fill('4');
  await page.locator('[data-complete]').click();await page.locator('[data-submit]').waitFor();
  assert.equal(received.length,0);assert.equal(stock,20);
  await page.reload();await page.locator('summary').filter({hasText:'保存した作業・一覧更新'}).click();
  const jobId=await page.locator('[data-jobs] option').last().getAttribute('value');await page.locator('[data-jobs]').selectOption(jobId);await page.locator('[data-submit]').waitFor();
  assert.equal(jobId,legacyId,'previous in-progress job should be inherited without submission');
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'4');
  await page.locator('[data-resume]').click();await page.locator('[data-complete]').waitFor();
  await page.evaluate(()=>QrRead.lookup('offline-qr'));
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'5');
  await page.locator('[data-field="qty"]').fill('4');await page.locator('[data-complete]').click();await page.locator('[data-submit]').waitFor();
  assert.equal(received.length,0,'resuming locally must not submit work');
  await page.locator('[data-submit]').click();await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.classList.contains('error'));
  assert.equal(received.length,0,'offline send must retain the job locally');
  await pc.goto(base+'/pc');await pc.waitForFunction(()=>document.querySelector('[data-jobs]'));assert.equal(await pc.locator('[data-jobs] option').count(),1);
  await context.setOffline(false);loseReceipt=true;await page.locator('[data-submit]').click();
  await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.classList.contains('error'));
  assert.equal(received.length,1);assert.equal(stock,20);
  await page.locator('[data-submit]').click();await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.textContent.includes('送信済み'));
  assert.equal(received.length,1);assert(uploads>=2);
  assert.equal(await page.evaluate(async id=>{try{await QrLocal.request('resume',{id,version:0});return false}catch{return true}},jobId),true,'sent work must not resume');
  await pc.reload();await pc.locator('[data-jobs]').selectOption(jobId);await pc.locator('[data-confirm]').waitFor();
  assert.equal(await pc.locator('[data-save]').count(),0);
  pc.on('dialog',d=>d.accept());await pc.locator('[data-field="qty"]').fill('3');await pc.locator('[data-confirm]').click();
  await pc.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.textContent.includes('在庫に反映しました'));assert.equal(stock,17);
  await context.setOffline(true);await page.evaluate(()=>QrRead.lookup('offline-qr'));
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'1');
  assert.equal(received.length,1,'new work after sending must stay device-local');
  page.on('dialog',d=>d.accept());await page.locator('[data-delete]').click();
  await page.evaluate(()=>QrRead.lookup('offline-qr'));
  assert.equal(await page.locator('[data-line]').count(),1);
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'1','deleted garment can be scanned again offline');
  const longText='スマホのChatGPTの説明\n  空白・改行\n<script>window.injected=true</script>\n'+'長い文章です。'.repeat(1000);
  await page.locator('#qrText summary').first().click();await page.locator('[data-text-draft]').fill(longText);
  await page.locator('[data-text-send]').click();
  await page.waitForFunction(()=>document.querySelector('[data-text-note]')?.textContent.includes('未送信の文章'));
  assert.equal(textMessages.length,0);
  await page.reload();await page.locator('#qrText summary').first().click();
  await page.locator('[data-text-retry]').first().waitFor({state:'attached'});
  await page.locator('[data-text-outbox] summary').first().click();
  assert.equal(await page.locator('[data-text-outbox] textarea').first().inputValue(),longText);
  await context.setOffline(false);await page.locator('[data-text-retry]').first().click();
  await page.waitForFunction(()=>document.querySelector('[data-text-note]')?.textContent.includes('送信しました'));
  assert.equal(textMessages.length,1);
  await pc.locator('#qrText summary').first().click();await pc.locator('[data-text-refresh]').click();
  await pc.waitForSelector('[data-text-copy]');
  assert.equal(await pc.locator('[data-text-body]').first().inputValue(),longText);
  await pc.evaluate(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.testCopied=text}},configurable:true}));
  await pc.locator('[data-text-copy]').first().click();
  assert.equal(await pc.evaluate(()=>window.testCopied),longText);
  assert.equal(await pc.evaluate(()=>window.injected),undefined);
  assert.equal(stock,17,'text transfer must not change inventory');
  await page.evaluate(qr=>QrRead.lookup(qr),lateQr);
  assert((await page.locator('[data-line]').last().innerText()).includes('追加確認した制服'));
  await context.setOffline(true);await page.reload();await page.locator('[data-field="qty"]').first().waitFor();
  assert((await page.locator('[data-line]').last().innerText()).includes('追加確認した制服'),'server-verified item must remain named offline');
  const totalBefore=await page.locator('[data-total]').innerText();
  await page.evaluate(async qr=>{const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('instruction-desk-qr-device',1);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});await new Promise((resolve,reject)=>{const t=db.transaction('records','readwrite'),s=t.objectStore('records'),r=s.get('jobs:12');r.onsuccess=()=>{const data=r.result;data.jobs.find(j=>j.status==='collecting').lines.push({qr,key:'new:'+qr,qty:7,scanCount:7,code:qr,productName:'商品未確認',sizes:[],size:'',condition:'',unitCost:''});s.put(data,'jobs:12')};t.oncomplete=resolve;t.onerror=()=>reject(t.error)});db.close()},missingQr);
  await page.reload();await page.locator('[data-unresolved="1"]').waitFor();
  assert.equal(await page.locator('[data-total]').innerText(),totalBefore,'previous unidentified rows must not count');
  assert.equal(await page.locator('[data-unresolved="1"] input').isDisabled(),true);
  assert((await page.locator('[data-unresolved="1"]').innerText()).includes('集計対象外'));
  assert.equal(await page.evaluate(qr=>QrRead.lookup(qr),newQr),true);
  assert((await page.locator('[data-line]').last().innerText()).includes('新方式で登録済みの制服'),'prepared nqr1 must resolve by exact ID while offline');
  const rowsBefore=await page.locator('[data-line]').count(),qtyBefore=await page.locator('[data-field="qty"]').last().inputValue();
  await page.evaluate(async()=>{const db=await new Promise(resolve=>{const r=indexedDB.open('instruction-desk-qr-device',1);r.onsuccess=()=>resolve(r.result)});await new Promise((resolve,reject)=>{const t=db.transaction('records','readwrite'),s=t.objectStore('records'),r=s.get('catalog:12');r.onsuccess=()=>{const c=r.result;window.savedCatalogForTest=c;s.put({...c,entryCount:c.entries.length+1},'catalog:12')};t.oncomplete=resolve;t.onerror=()=>reject(t.error)});db.close()});
  assert.equal(await page.evaluate(qr=>QrRead.lookup(qr),newQr),false);
  assert((await page.locator('#qrScanResult').innerText()).includes('不完全'));
  assert.equal(await page.locator('[data-line]').count(),rowsBefore);
  assert.equal(await page.locator('[data-field="qty"]').last().inputValue(),qtyBefore,'bad saved catalog must never increment');


  assert.deepEqual(errors,[]);
  console.log('PASS: offline shell reload, offline scans/edit/complete, local persistence, explicit send, lost receipt retry, office inbox and stock confirmation');
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(e=>{console.error(e);process.exitCode=1});
