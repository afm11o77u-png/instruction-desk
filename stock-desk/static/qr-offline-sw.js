const CACHE='instruction-qr-shell-20261119-offline3';
const ASSETS=['qr-mobile.html','qr-mobile.css?v=20261119_offline1','qr_work.css?v=20261119_offline3','qr_local.js?v=20261119_offline3','qr_work.js?v=20261119_offline3','qr_read.js?v=20261119_offline1','qr-mobile.js?v=20261119_offline1','vendor/jsqr/jsQR.js'].map(p=>'/inventory/'+p);
async function prepare(){
 const responses=await Promise.all(ASSETS.map(async url=>{const r=await fetch(url,{cache:'reload'});if(!r.ok||r.redirected)throw Error('Cannot cache authenticated QR shell');return [url,r]}));
 const cache=await caches.open(CACHE);await Promise.all(responses.map(([url,r])=>cache.put(url,r)));
}
self.addEventListener('install',event=>event.waitUntil(prepare().then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('message',event=>{if(event.data?.type==='prepare')event.waitUntil(prepare().then(()=>event.ports[0]?.postMessage({ok:true})).catch(()=>event.ports[0]?.postMessage({ok:false})))});
self.addEventListener('fetch',event=>{
 const u=new URL(event.request.url);
 if(event.request.method!=='GET'||u.origin!==self.location.origin||!ASSETS.some(p=>new URL(p,self.location.origin).pathname===u.pathname))return;
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
 event.respondWith(fetch(event.request,{signal:controller.signal}).finally(()=>clearTimeout(timer)).then(async response=>{
  // Login redirects must never replace the saved offline page.
  if(response.ok&&!response.redirected){const cache=await caches.open(CACHE);await cache.put(event.request,response.clone());return response}
  return (await caches.open(CACHE)).match(event.request,{ignoreSearch:true}).then(cached=>cached||response);
 }).catch(async()=>{const cached=await(await caches.open(CACHE)).match(event.request,{ignoreSearch:true});return cached||Response.error()}));
});
