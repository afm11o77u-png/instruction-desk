// Real browser, service worker and IndexedDB; synthetic HTTP API, never live stock.
const http=require('http'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.QR_TEST_PLAYWRIGHT||'playwright');
const folder=path.join(__dirname,'../static');
(async()=>{
 let received=[],stock=20,uploads=0,loseReceipt=false,denied=false,catalogRelease=null,catalogRequests=0,assetReads=0;
 const legacyId=require('crypto').randomUUID();
 const target={customerProductId:1,customerName:'検証顧客',code:'OFF-1',productName:'白衣',size:'M',condition:'new',sizes:['M'],unitCost:'',color:'白',spec:'標準',lots:[],locationId:1};
 const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');const json=(body,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body))};
  if(url.pathname==='/api/me')return json({user:denied?null:{id:req.headers.cookie?.includes('office=1')?1:12,name:'検証担当',role:'owner'}});
  if(url.pathname.startsWith('/inventory/api/qr-work/')){
   if(req.headers['x-stock-request']!=='1')return json({error:'header'},400);
   let raw='';for await(const chunk of req)raw+=chunk;const b=JSON.parse(raw||'{}'),action=url.pathname.split('/').pop();
   if(action==='catalog'){catalogRequests++;if(b.revision==='catalog-1')return json({notModified:true,revision:'catalog-1',preparedAt:'2026-11-19'});await new Promise(resolve=>catalogRelease=resolve);return json({revision:'catalog-1',entries:[{qr:'offline-qr',kind:'legacy',customer:'',maker:'',target}],preparedAt:'2026-11-19'});}
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
   return res.end(`<!doctype html><meta charset="utf-8"><main id="main"></main><script>async function api(p,d){let r=await fetch('/inventory'+p,{method:'POST',headers:{'Content-Type':'application/json','X-Stock-Request':'1'},body:JSON.stringify(d)});let b=await r.json();if(!r.ok)throw Error(b.error);return b}</script><script src="/inventory/qr_local.js"></script><script src="/inventory/qr_work.js"></script><script src="/inventory/qr_read.js"></script><script>QrRead.page()</script>`);
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
  await page.evaluate(()=>QrRead.lookup('offline-qr'));await page.evaluate(()=>QrRead.lookup('offline-qr'));
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'2');assert.equal(received.length,0);
  assert.equal(await page.locator('[data-save]').count(),0);
  await page.locator('[data-field="qty"]').fill('4');
  await page.locator('[data-complete]').click();await page.locator('[data-submit]').waitFor();
  assert.equal(received.length,0);assert.equal(stock,20);
  await page.reload();await page.locator('summary').filter({hasText:'保存した作業・一覧更新'}).click();
  const jobId=await page.locator('[data-jobs] option').last().getAttribute('value');await page.locator('[data-jobs]').selectOption(jobId);await page.locator('[data-submit]').waitFor();
  assert.equal(jobId,legacyId,'previous in-progress job should be inherited without submission');
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'4');
  await page.locator('[data-submit]').click();await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.classList.contains('error'));
  assert.equal(received.length,0,'offline send must retain the job locally');
  await pc.goto(base+'/pc');await pc.waitForFunction(()=>document.querySelector('[data-jobs]'));assert.equal(await pc.locator('[data-jobs] option').count(),1);
  await context.setOffline(false);loseReceipt=true;await page.locator('[data-submit]').click();
  await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.classList.contains('error'));
  assert.equal(received.length,1);assert.equal(stock,20);
  await page.locator('[data-submit]').click();await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.textContent.includes('送信済み'));
  assert.equal(received.length,1);assert(uploads>=2);
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
  assert.deepEqual(errors,[]);
  console.log('PASS: offline shell reload, offline scans/edit/complete, local persistence, explicit send, lost receipt retry, office inbox and stock confirmation');
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve))}
})().catch(e=>{console.error(e);process.exitCode=1});
