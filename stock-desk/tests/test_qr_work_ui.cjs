// Browser integration with synthetic QR/API data; no live inventory or camera.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.QR_TEST_PLAYWRIGHT||'playwright');
const staticRoot=path.join(__dirname,'../static');
const clone=x=>JSON.parse(JSON.stringify(x));
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.QR_TEST_CHROME||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  let job=null,pc=false,stock=10,confirmed=0,loseScan=false;const tokens=new Set();
  const json=(route,value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  await page.route('https://qr.test/**',async route=>{
   const req=route.request(),url=new URL(req.url()),body=req.method()==='POST'?req.postDataJSON():{};
   if(url.pathname==='/api/me')return json(route,{user:{id:12,name:'検証担当',role:'warehouse'}});
   if(url.pathname==='/inventory/api/stock-qr/read')return json(route,{found:true,qrKind:'new',code:'TEST',productName:'白衣',lots:[]});
   if(url.pathname.startsWith('/inventory/api/qr-work/')){
    assert.equal(req.headers()['x-stock-request'],'1');
    const action=url.pathname.split('/').pop();
    if(action==='state')return json(route,{jobs:job?[clone(job)]:[],locations:[{id:1,name:'検証倉庫'}],actorId:'12',canConfirm:pc});
    if(action==='scan'){
     if(!tokens.has(body.token)){
      tokens.add(body.token);
      if(!job)job={id:'job-one',actor_id:'12',actor_name:'検証担当',status:'collecting',version:0,direction:'outbound',started_at:'2026-11-18 10:00',completed_at:null,lines:[{key:'line-one',customerProductId:1,customerName:'検証顧客',code:'TEST',productName:'白衣',size:'M',condition:'new',locationId:1,qty:0,scanCount:0,sizes:['M'],unitCost:'',lots:[]}]};
      job.lines[0].qty++;job.lines[0].scanCount++;job.version++;
     }
     if(loseScan){loseScan=false;return route.abort('failed')}
     return json(route,{job:clone(job)});
    }
    if(action==='edit'||action==='complete'){
     job.lines=body.lines.map(l=>({...job.lines.find(x=>x.key===l.key),...l,qty:Number(l.qty)}));job.direction=body.direction;job.version++;
     if(action==='complete'){job.status='completed';job.completed_at='2026-11-18 10:10'}
     return json(route,{job:clone(job)});
    }
    if(action==='confirm'){
     if(job.status!=='confirmed'){stock+=job.direction==='outbound'?-job.lines[0].qty:job.lines[0].qty;confirmed++;job.status='confirmed';job.confirmed_at='2026-11-18 10:15';job.confirmed_by='主管理者';job.version++}
     return json(route,{job:clone(job)});
    }
   }
   if(url.pathname==='/pc')return route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/inventory/qr_work.css"></head><body><main id="main"></main><script>function stockCanWrite(){return true}async function api(p,d){const r=await fetch('/inventory'+p,{method:'POST',headers:{'Content-Type':'application/json','X-Stock-Request':'1'},body:JSON.stringify(d)});return r.json()}</script><script src="/inventory/qr_work.js"></script><script src="/inventory/qr_read.js"></script><script>QrRead.page()</script></body></html>`});
   const name=url.pathname.replace('/inventory/','');
   if(['qr-mobile.html','qr-mobile.js','qr-mobile.css','qr_work.js','qr_work.css','qr_read.js'].includes(name))return route.fulfill({contentType:name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(path.join(staticRoot,name))});
   return route.fulfill({status:404,body:''});
  });
  await page.goto('https://qr.test/inventory/qr-mobile.html');
  await page.locator('[data-jobs]').waitFor();
  await page.evaluate(()=>QrRead.lookup('test-qr'));
  await page.evaluate(()=>QrRead.lookup('test-qr'));
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'2');assert.equal(stock,10);
  await page.locator('[data-plus]').click();
  assert.equal(await page.locator('[data-total]').innerText(),'合計 3 点');
  await page.locator('[data-field="qty"]').fill('4');
  await page.locator('[data-save]').click();await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.textContent.includes('保存しました'));
  await page.reload();await page.locator('[data-field="qty"]').waitFor();
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'4');
  loseScan=true;await page.evaluate(()=>QrRead.lookup('test-qr'));
  assert.equal(job.lines[0].qty,5);
  await page.evaluate(()=>QrRead.lookup('test-qr'));
  assert.equal(await page.locator('[data-field="qty"]').inputValue(),'5');
  assert.equal(job.lines[0].qty,5,'lost response retry must not increment twice');
  await page.locator('[data-complete]').click();await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.textContent.includes('作業を完了'));
  assert.equal(await page.locator('[data-confirm]').count(),0);assert.equal(stock,10);
  pc=true;await page.goto('https://qr.test/pc');await page.locator('[data-jobs]').waitFor();await page.locator('[data-jobs]').selectOption('job-one');
  await page.locator('[data-confirm]').waitFor();page.on('dialog',d=>d.accept());
  await page.locator('[data-confirm]').click();try{await page.waitForFunction(()=>document.querySelector('#qrWorkMessage')?.textContent.includes('在庫に反映しました'),null,{timeout:5000})}catch(e){console.error({message:await page.locator('#qrWorkMessage').innerText(),errors,job,stock});throw e}
  assert.equal(stock,5);assert.equal(confirmed,1);assert.equal(await page.locator('[data-confirm]').count(),0);
  assert.deepEqual(errors,[]);
  console.log('PASS: phone scan count/edit/save/reload/lost-response retry/complete; PC confirmation; no phone confirmation or JS errors');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
