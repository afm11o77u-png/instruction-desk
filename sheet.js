const C39={"0":"nnnwwnwnn","1":"wnnwnnnnw","2":"nnwwnnnnw","3":"wnwwnnnnn","4":"nnnwwnnnw","5":"wnnwwnnnn","6":"nnwwwnnnn","7":"nnnwnnwnw","8":"wnnwnnwnn","9":"nnwwnnwnn","A":"wnnnnwnnw","B":"nnwnnwnnw","C":"wnwnnwnnn","D":"nnnnwwnnw","E":"wnnnwwnnn","F":"nnwnwwnnn","G":"nnnnnwwnw","H":"wnnnnwwnn","I":"nnwnnwwnn","J":"nnnnwwwnn","K":"wnnnnnnww","L":"nnwnnnnww","M":"wnwnnnnwn","N":"nnnnwnnww","O":"wnnnwnnwn","P":"nnwnwnnwn","Q":"nnnnnnwww","R":"wnnnnnwwn","S":"nnwnnnwwn","T":"nnnnwnwwn","U":"wwnnnnnnw","V":"nwwnnnnnw","W":"wwwnnnnnn","X":"nwnnwnnnw","Y":"wwnnwnnnn","Z":"nwwnwnnnn","-":"nwnnnnwnw",".":"wwnnnnwnn"," ":"nwwnnnwnn","*":"nwnnwnwnn","$":"nwnwnwnnn","/":"nwnwnnnwn","+":"nwnnnwnwn","%":"nnnwnwnwn"};
function barcodeSvg(text){const data='*'+text+'*';if(![...data].every(ch=>C39[ch]))return '';const narrow=1,wide=2.5,quiet=10,height=40;let x=quiet,rects='';for(const ch of data){const pattern=C39[ch];for(let i=0;i<pattern.length;i++){const width=pattern[i]==='w'?wide:narrow;if(i%2===0)rects+=`<rect x="${x}" y="0" width="${width}" height="${height}"/>`;x+=width}x+=narrow}return `<svg class="sheetbarcode" viewBox="0 0 ${x-narrow+quiet} ${height}" role="img" aria-label="指示番号 ${text}"><g fill="currentColor">${rects}</g></svg>`}
function assignedNumber(order){const number=String(order?.number||'');if(!order||order.status==='下書き')return '';return /^SI-\d{8}-\d{3,4}$/.test(number)||/^SI-\d{6}$/.test(number)?number:''}
function applySheetTemplate(o){$('#sheettemplate').value='common';$('#sheetcaption').textContent='共通書式';const number=assignedNumber(o);$('#sheetnumber').textContent=number||'保存時に自動採番';$('#sheetnumber').parentElement.querySelector('.sheetbarcode')?.remove();if(number)$('#sheetnumber').insertAdjacentHTML('afterend',barcodeSvg(number))}
function stockLabel(v){const s=String(v||'').trim();const alias={'倉庫':'石狩倉庫','0':'石狩倉庫','a：工場にある':'発寒支店','a':'発寒支店','工場':'発寒支店','工場内':'発寒支店','b：揃っている':'管理担当持込','b':'管理担当持込','揃っている':'管理担当持込','持ち込み':'管理担当持込'};return ['石狩倉庫','発寒支店','管理担当持込'].includes(s)?s:(alias[s]||s)}
addItem=function(x={}){const el=document.createElement('div');el.className='item';const cols=[['name','アイテム名'],['maker','メーカー'],['sku','品番'],['size','サイズ'],['condition','新古'],['quantity','数量'],['note','備考']];const line=document.createElement('div');line.className='itemline';line.innerHTML=cols.map(([key,label])=>{let control;if(key==='condition')control=`<select data-key="${key}"><option>新品</option><option>中古</option><option>未指定</option></select>`;else if(key==='note')control=`<textarea data-key="note" rows="1">${esc(x.note)}</textarea>`;else control=`<input data-key="${key}" ${key==='quantity'?'type="number" min="1" max="99999" step="1" required':'type="text"'} ${key==='name'?'required':''} value="${esc(key==='quantity'?(x[key]??''):x[key])}">`;return `<label><span>${label}</span>${control}</label>`}).join('')+'<button type="button" class="remove" aria-label="この商品を削除">×</button>';el.append(line);const foot=document.createElement('div');foot.className='itemfoot';foot.innerHTML=`<label class="stockline"><span>在庫場所<small>印刷対象外</small><em>必須</em></span><select data-key="stock"><option value="">選択してください</option><option>石狩倉庫</option><option>発寒支店</option><option>管理担当持込</option></select></label><label class="note2line"><span>備考②<small>印刷対象外</small></span><textarea data-key="note2" rows="1">${esc(x.note2)}</textarea></label>`;el.append(foot);el.querySelector('[data-key="condition"]').value=x.condition||'新品';const stock=stockLabel(x.stock),stockEl=el.querySelector('[data-key="stock"]');if(stock&&![...stockEl.options].some(o=>o.value===stock))stockEl.add(new Option(stock,stock));stockEl.value=stock;el.querySelector('.remove').onclick=()=>{el.remove();dirty=true};$('#items').append(el);el.querySelectorAll('textarea').forEach(note=>{const resizeNote=()=>{note.style.height='auto';note.style.height=Math.max(40,note.scrollHeight)+'px'};note.addEventListener('input',resizeNote);requestAnimationFrame(resizeNote)})};
function printedSheet(order){const clone=document.querySelector('.papersheet').cloneNode(true);clone.querySelectorAll('[id]').forEach(el=>el.removeAttribute('id'));clone.querySelectorAll('.sheetsection small,em').forEach(el=>el.remove());clone.querySelectorAll('input,select,textarea').forEach(el=>{const name=el.getAttribute('name');if(!name||name==='methodNote'){el.remove();return}let text;if(name==='assigneeId')text=order.assigneeName||order.owner||'—';else if(el.tagName==='SELECT'){el.value=order[name]??'';text=el.value===String(order[name]??'')?el.selectedOptions[0]?.textContent||'—':order[name]||'—';if(name==='method'&&order.method==='その他'&&String(order.methodNote||'').trim())text='その他（'+String(order.methodNote).trim()+'）'}else text=order[name]||'—';const v=document.createElement('div');v.className='fieldvalue';v.textContent=text||'—';el.replaceWith(v)});clone.style.zoom='1';clone.querySelectorAll('button').forEach(el=>el.remove());const strong=clone.querySelector('.papernumber strong');const number=assignedNumber(order);strong.textContent=number||'保存時に自動採番';clone.querySelector('.sheetbarcode')?.remove();if(number)strong.insertAdjacentHTML('afterend',barcodeSvg(number));const container=clone.querySelector('.sheetitemswrap');const items=order.items||[];container.innerHTML=`<table class="printeditems"><colgroup>${itemWidths.map(w=>`<col style="width:${w/itemWidths.reduce((a,b)=>a+b,0)*100}%">`).join('')}</colgroup><thead><tr>${['アイテム名','メーカー','品番','サイズ','新古','数量','備考'].map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${Array.from({length:Math.max(12,items.length)},(_,i)=>items[i]||{}).map(x=>`<tr>${['name','maker','sku','size','condition','quantity','note'].map(k=>`<td>${esc(x[k])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;container.style.overflow='visible';return clone}
function labelPx(mm){return mm*96/25.4}
function fitLabelText(text,widthMm,heightMm,maxPt){const value=String(text||'').trim();if(!value)return 0;const probe=document.createElement('span');probe.style.cssText='position:fixed;left:-9999px;top:0;visibility:hidden;font-weight:700;line-height:1.05;white-space:nowrap;display:inline-block;font-family:"BIZ UDPGothic","Yu Gothic UI","Yu Gothic",Meiryo,sans-serif;';probe.textContent=value;document.body.append(probe);const maxW=Math.max(8,labelPx(widthMm)-2),maxH=labelPx(heightMm);const cap=Math.max(4,maxPt||32);let lo=4,hi=Math.min(cap,Math.max(4,heightMm/0.3528/1.05));for(let i=0;i<18;i++){const mid=(lo+hi)/2;probe.style.fontSize=mid+'pt';if(probe.offsetWidth<=maxW&&probe.offsetHeight<=maxH)lo=mid;else hi=mid}probe.remove();return Math.round(lo*10)/10}
function fitWhoSize(name,widthMm,heightMm,maxPt){const row=document.createElement('div');row.style.cssText='position:fixed;left:-9999px;top:0;visibility:hidden;display:flex;justify-content:center;align-items:center;gap:.5em;width:'+labelPx(widthMm)+'px;font-weight:700;line-height:1.05;white-space:nowrap;font-family:"BIZ UDPGothic","Yu Gothic UI","Yu Gothic",Meiryo,sans-serif;';const a=document.createElement('span');a.textContent=name;const b=document.createElement('span');b.textContent='様';a.style.whiteSpace=b.style.whiteSpace='nowrap';row.append(a,b);document.body.append(row);const maxW=labelPx(widthMm),maxH=labelPx(heightMm);const cap=Math.max(4,maxPt||54);let lo=4,hi=Math.min(cap,Math.max(4,heightMm/0.3528/1.05));for(let i=0;i<18;i++){const mid=(lo+hi)/2;row.style.fontSize=mid+'pt';if(a.offsetWidth+b.offsetWidth+(parseFloat(getComputedStyle(row).columnGap)||0)<=maxW&&Math.max(a.offsetHeight,b.offsetHeight)<=maxH)lo=mid;else hi=mid}row.remove();return Math.round(lo*10)/10}
function clampLabel(el){if(!el||!String(el.textContent||'').trim())return;let size=parseFloat(el.style.fontSize);if(!size)return;for(let i=0;i<80;i++){if(el.scrollWidth<=el.clientWidth+0.5&&el.scrollHeight<=el.clientHeight+0.5)return;size=Math.max(4,Math.round((size-.4)*10)/10);el.style.fontSize=size+'pt';if(size<=4)return}}
function currentLabelPattern(){const picked=document.querySelector('input[name="labelpattern"]:checked');return picked&&picked.value==='3'?'3':'4'}
function applyLabelPatternUi(pattern){const three=String(pattern)==='3';const panel=$('#labeladdresspanel');if(panel)panel.classList.toggle('labelpattern3',three);if(three){$('#labelcards')?.querySelectorAll('[data-key="management"],[data-key="department"]').forEach(el=>{el.value=''})}}
function setLabelPattern(pattern){const want=String(pattern)==='3'?'3':'4';document.querySelectorAll('input[name="labelpattern"]').forEach(el=>{el.checked=el.value===want});applyLabelPatternUi(want)}
function addressTags(list,pattern='4'){const lines=String(pattern)==='3'?'3':'4';const pages=[];for(let i=0;i<list.length;i+=4){const page=document.createElement('section');page.className='labelpage'+(lines==='3'?' labelpage3':'');for(let n=0;n<4;n++){const order=list[i+n];const tag=document.createElement('div');tag.className=order?'tag':'tag tagempty';if(lines==='3')tag.classList.add('taglines3');if(order){const body=document.createElement('div');body.className='tagbody';const customer=document.createElement('p');customer.className='tagcustomer';customer.textContent=order.customer||'';const who=document.createElement('p');who.className='tagwho';const raw=String(order.employee||'').trim().replace(/様$/,'');if(raw){const name=document.createElement('span');name.className='tagname';name.textContent=raw;const sama=document.createElement('span');sama.className='tagsama';sama.textContent='様';who.append(name,sama)}const job=document.createElement('p');job.className='tagjob';job.textContent=order.request||'';if(lines==='3'){for(const [node,extra] of [[customer,''],[who,'nameband']]){const band=document.createElement('div');band.className='tagband'+(extra?' '+extra:'');band.append(node);body.append(band)}tag.append(body,job)}else{const mid=document.createElement('div');mid.className='tagmid';const management=String(order.management||'').trim();const dept=document.createElement('p');dept.className='tagdept';dept.textContent=order.department||'';if(management){mid.classList.add('hassplit');const code=document.createElement('p');code.className='tagcode';code.textContent=management;mid.append(code,dept)}else mid.append(dept);for(const [node,extra] of [[customer,''],[mid,''],[who,'nameband']]){const band=document.createElement('div');band.className='tagband'+(extra?' '+extra:'');band.append(node);body.append(band)}tag.append(body,job)}}page.append(tag)}pages.push(page)}fitAddressTags(pages);return pages}
function fitAddressTags(pages){
 if(!pages?.length)return;
 let host=$('#labelfit');
 if(!host){host=document.createElement('iframe');host.id='labelfit';host.setAttribute('aria-hidden','true');host.style.cssText='position:fixed;left:-12000px;top:0;width:297mm;height:210mm;border:0;visibility:hidden';document.body.append(host)}
 const doc=host.contentDocument;
 doc.open();
 doc.write(`<!doctype html><html><head><meta charset="utf-8"><style>${labelPrintCss}</style></head><body></body></html>`);
 doc.close();
 const body=doc.body;
 body.replaceChildren(...pages);
 void body.offsetHeight;
 const mm=px=>px*25.4/96;
 for(const tag of body.querySelectorAll('.tag:not(.tagempty)')){
  const bands=[...tag.querySelectorAll('.tagband')];
  const lines3=tag.classList.contains('taglines3');
  if(lines3?bands.length<2:bands.length<3)continue;
  const customer=tag.querySelector('.tagcustomer'),dept=tag.querySelector('.tagdept'),code=tag.querySelector('.tagcode'),who=tag.querySelector('.tagwho'),job=tag.querySelector('.tagjob');
  const boxW=el=>{const s=doc.defaultView.getComputedStyle(el);return Math.max(8,mm(el.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight))-2.5)};
  const boxH=el=>{const s=doc.defaultView.getComputedStyle(el);return Math.max(3,mm(el.clientHeight-parseFloat(s.paddingTop)-parseFloat(s.paddingBottom))-1)};
  const raw=who.querySelector('.tagname')?.textContent||'';
  const customerText=customer.textContent||'';
  const customerLen=[...customerText.replace(/\s/g,'')].length;
  const customerCap=lines3?(customerLen<=4?56:customerLen<=6?48:customerLen<=8?42:36):(customerLen<=4?48:customerLen<=6?42:customerLen<=8?36:32);
  const customerSize=fitLabelText(customerText,boxW(bands[0]),boxH(bands[0])*.92,customerCap);
  const nameBand=bands[lines3?1:2];
  const nameLen=[...raw.replace(/\s/g,'')].length;
  const nameCap=lines3?(nameLen<=2?78:nameLen<=4?70:58):(nameLen<=2?68:nameLen<=4?60:50);
  const whoSize=raw?fitWhoSize(raw,boxW(nameBand),boxH(nameBand)*.92,nameCap):0;
  const jobSize=fitLabelText(job.textContent,boxW(job),boxH(job)*.95,lines3?36:28);
  if(customerSize)customer.style.fontSize=customerSize+'pt';
  if(whoSize)who.style.fontSize=whoSize+'pt';
  if(jobSize)job.style.fontSize=jobSize+'pt';
  clampLabel(customer);clampLabel(who);clampLabel(job);
  if(!lines3){
   const deptSize=fitLabelText(dept.textContent,boxW(code?dept:bands[1]),boxH(bands[1])*.92,30);
   const codeSize=code?fitLabelText(code.textContent,boxW(code),boxH(bands[1])*.92,30):0;
   if(deptSize)dept.style.fontSize=deptSize+'pt';
   if(codeSize)code.style.fontSize=codeSize+'pt';
   clampLabel(dept);if(code)clampLabel(code);
  }
 }
 body.replaceChildren();
}
let pendingPrintIds=[],printing=false,reopenEditor=false,printWatch=null;
const previewSteps=[.5,.65,.8,1,1.15,1.3,1.45];
let previewZoom=1.15,previewFit=true;
function clearPrintWatch(){if(printWatch){clearTimeout(printWatch);printWatch=null}}
function unlockPrinting(){clearPrintWatch();pendingPrintIds=[];printing=false}
function armPrintWatch(){clearPrintWatch();printWatch=setTimeout(()=>{printWatch=null;if(!printing||document.body.classList.contains('previewing'))return;pendingPrintIds=[];printing=false;toast('印刷の応答がありませんでした。もう一度お試しください。')},90000)}
function guardPrinting(){if(!printing)return false;if(document.body.classList.contains('previewing')){toast('印刷プレビューを表示中です。閉じてから操作してください。');return true}unlockPrinting();toast('印刷処理が途中で止まっていました。解除したので、もう一度操作してください。');return true}
function wrapPreviewPages(){
 const area=$('#printarea');
 [...area.children].forEach((sheet,i)=>{
  const landscape=sheet.classList.contains('labelpage');
  const page=document.createElement('div');page.className='previewpage'+(landscape?' previewlandscape':'');
  page.dataset.caption=`${i+1}ページ ／ A4 ${landscape?'横（297 × 210 mm）':'縦（210 × 297 mm）'}`;
  sheet.before(page);page.append(sheet);
 });
}
function applyPreviewZoom(){
 if(!document.body.classList.contains('previewing'))return;
 const bar=$('#previewbar'),area=$('#printarea');
 document.body.style.setProperty('--preview-top',bar.offsetHeight+'px');
 const pages=[...area.querySelectorAll('.previewpage')];
 const widest=Math.max(1,...pages.map(p=>p.offsetWidth));
 if(previewFit)previewZoom=Math.max(.2,Math.min(1.15,(area.clientWidth-64)/widest));
 document.body.style.setProperty('--preview-zoom',String(previewZoom));
 $('#previewscale').textContent=Math.round(previewZoom*100)+'%';
 $('#previewsmaller').disabled=previewZoom<=previewSteps[0];
 $('#previewlarger').disabled=previewZoom>=previewSteps.at(-1);
 $('#previewfit').setAttribute('aria-pressed',String(previewFit));
 pages.forEach(page=>{
  const sheet=page.firstElementChild;
  const available=(page.classList.contains('previewlandscape')?194:273)*96/25.4;
  const overflow=sheet.offsetHeight>available+1;
  page.classList.toggle('previewoverflow',overflow);
  page.dataset.warning=overflow?'内容がA4の印刷範囲を超えています。印刷時は複数ページになります。':'';
 });
}
function openPrintPreview(){reopenEditor=!!$('#editor')?.open;if(reopenEditor)$('#editor').close();wrapPreviewPages();document.body.classList.add('previewing');$('#previewbar').hidden=false;$('#previewprint').hidden=draftPreview||previewChoice;$('#previewchoiceprint').hidden=draftPreview||!previewChoice;showPreviewKind(previewShow);previewFit=true;applyPreviewZoom();$('#printarea').scrollTop=0;$('#printarea').scrollLeft=0}
new ResizeObserver(()=>applyPreviewZoom()).observe($('#previewbar'));
window.addEventListener('resize',applyPreviewZoom);
function closePrintPreview(){draftPreview=false;document.body.classList.remove('draftpreview');document.body.classList.remove('previewing');$('#previewbar').hidden=true;previewChoice=false;previewOrder=null;previewShow='sheet';$('#previewprint').hidden=false;$('#previewchoiceprint').hidden=true;$('#previewchoiceprint').textContent='印刷';$('#previewseesheet').hidden=true;$('#previewseelabel').hidden=true;const reopen=reopenEditor;reopenEditor=false;if(reopen)$('#editor').showModal()}
let draftPreview=false;
let previewChoice=false,previewOrder=null,previewShow='sheet';
function showPreviewKind(kind){
 previewShow=kind==='label'?'label':'sheet';
 const choice=previewChoice;
 $('#previewseesheet').hidden=!choice;
 $('#previewseelabel').hidden=!choice;
 $('#previewseesheet').setAttribute('aria-pressed',String(choice&&previewShow==='sheet'));
 $('#previewseelabel').setAttribute('aria-pressed',String(choice&&previewShow==='label'));
 if(choice)$('#previewchoiceprint').textContent='印刷';
 $('#printarea').querySelectorAll('.previewpage').forEach(page=>{
  const label=page.classList.contains('previewlandscape');
  page.hidden=choice&&(previewShow==='sheet'?label:!label);
 });
}
function openFloorSheetPreview(order){if(!order)return;previewChoice=true;previewOrder=order;previewShow='sheet';queuePrint([order],false,true,true)}
function queuePrint(list,labelsOnly,previewOnly=false,includeLabels=true,confirmationOnly=false,labelPattern='4'){
 if(!list.length)return;
 if(guardPrinting())return;
 // labelOrders() keeps null slots for blank print positions; skip those here.
 draftPreview=confirmationOnly||list.some(o=>o&&o.status==='下書き');
 if(draftPreview){previewOnly=true;labelsOnly=false;includeLabels=false;previewChoice=false;previewOrder=null}
 document.body.classList.toggle('draftpreview',draftPreview);
 printing=true;pendingPrintIds=labelsOnly||draftPreview?[]:list.filter(Boolean).map(o=>o.id);
 try{
  $('#printarea').replaceChildren(...(labelsOnly?addressTags(list,labelPattern):[...list.map(printedSheet),...(includeLabels?addressTags(list,'4'):[])]));
  if(previewOnly){openPrintPreview();return}
  // Close the modal before printing so its backdrop cannot cover the paper.
  reopenEditor=!!$('#editor')?.open;
  if(reopenEditor)$('#editor').close();
  if(labelsOnly){printLabelFrame();return}
  armPrintWatch();
  window.print();
 }catch(e){
  unlockPrinting();closePrintPreview();$('#printarea').replaceChildren();
  toast('印刷画面を開けませんでした。もう一度お試しください。');
 }
}
window.addEventListener('afterprint',()=>{const ids=pendingPrintIds;clearPrintWatch();pendingPrintIds=[];printing=false;closePrintPreview();$('#printarea').replaceChildren();if(!ids.length)return;api('/api/orders/printed',{ids}).then(r=>{for(const o of r.orders){const i=orders.findIndex(x=>x.id===o.id);if(i>=0)orders[i]=o;if(editing?.id===o.id)editing=structuredClone(o)}render();toast(ids.length===1?'印刷したことを記録しました。':`${ids.length}件を印刷済みにしました。作業状況は変えていません。`)}).catch(e=>toast(e.message))});
$('#previewprint').onclick=()=>{if(draftPreview||!document.body.classList.contains('previewing'))return;if($('#printarea').querySelector('.labelpage')&&!$('#printarea').querySelector('.papersheet'))printLabelFrame();else{armPrintWatch();window.print()}};
$('#previewseesheet').onclick=()=>{showPreviewKind('sheet');previewFit=true;applyPreviewZoom();$('#printarea').scrollTop=0;$('#printarea').scrollLeft=0};
$('#previewseelabel').onclick=()=>{if(!$('#printarea .labelpage')){toast('宛名に入れる内容がありません。');return}showPreviewKind('label');previewFit=true;applyPreviewZoom();$('#printarea').scrollTop=0;$('#printarea').scrollLeft=0};
$('#previewchoiceprint').onclick=()=>{if(draftPreview||!document.body.classList.contains('previewing'))return;if(previewShow==='label'){if(!previewOrder)return;const pages=addressTags([previewOrder]);if(!pages.length){toast('宛名に入れる内容がありません。');return}printLabelFrame(pages,true);return}armPrintWatch();window.print()};
const labelPrintCss=`@page{size:A4 landscape;margin:18mm 12mm 6mm 12mm}html,body{margin:0;padding:0;background:#fff}*{box-sizing:border-box}.labelpage{width:273mm;height:186mm;margin:0;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;position:relative;overflow:hidden;break-after:page;page-break-after:always;background:#fff}.labelpage:last-child{break-after:auto;page-break-after:auto}.labelpage::before,.labelpage::after{content:"";position:absolute;z-index:3}.labelpage::before{left:0;right:0;top:50%;border-top:1px dashed #000}.labelpage::after{top:0;bottom:0;left:50%;border-left:1px dashed #000}.tag{min-width:0;min-height:0;height:100%;display:flex;flex-direction:column;overflow:hidden;text-align:center;color:#000;padding:2.5mm 4mm;box-sizing:border-box}.tagbody{flex:1 1 auto;min-height:0;width:100%;display:grid;grid-template-rows:31fr 18fr 47fr}.tagband{min-width:0;min-height:0;height:auto;max-height:none;display:flex;align-items:center;justify-content:center;overflow:hidden;padding:1.5mm 2mm;box-sizing:border-box}.tagband:nth-child(1){justify-content:flex-start;padding-left:8mm}.tagband:nth-child(2){align-items:flex-start;padding-top:1.5mm}.nameband{align-items:center;padding:1.5mm 2mm 2mm}.tagmid{width:100%;height:100%;min-width:0;display:flex;align-items:center;justify-content:center}.tagmid.hassplit{display:grid;grid-template-columns:24% minmax(0,1fr);align-items:center;column-gap:2mm;padding-left:14mm}.tagcustomer,.tagdept,.tagcode,.tagjob,.tagwho{margin:0;width:100%;max-width:100%;max-height:100%;font-weight:700;line-height:1.05;overflow:hidden;text-align:center}.tagcustomer{text-align:left}.tagwho{display:flex;justify-content:center;align-items:center;gap:.5em}.tagname,.tagsama{min-width:0;white-space:nowrap}.tagcustomer,.tagdept,.tagcode,.tagjob{white-space:nowrap}.tagjob{flex:0 0 18%;height:auto;max-height:none;display:flex;align-items:center;justify-content:center;padding:1mm 2mm 1.6mm;letter-spacing:.04em;box-sizing:border-box}.tag.taglines3 .tagbody{grid-template-rows:40fr 42fr}.tag.taglines3 .tagjob{flex:0 0 28%}.tag.taglines3 .tagband:nth-child(1){justify-content:flex-start;padding-left:8mm}.tag.taglines3 .nameband{align-items:center}.noticepage{display:flex!important;grid-template-columns:none!important;grid-template-rows:none!important;flex-direction:column;align-items:center;justify-content:center;gap:10mm;padding:12mm 16mm;box-sizing:border-box;text-align:center;overflow:hidden}.noticepage::before,.noticepage::after{display:none!important;content:none!important}.noticebody{width:100%;height:100%;min-height:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12mm;overflow:hidden}.noticeto,.noticeline,.noticeask{margin:0;width:100%;max-width:100%;font-weight:700;line-height:1.15;overflow:hidden;white-space:nowrap;text-align:center}.noticeto{letter-spacing:.04em}.noticeline{letter-spacing:.02em}.noticeask{letter-spacing:.08em}body{font-family:"BIZ UDPGothic","Yu Gothic UI","Yu Gothic",Meiryo,sans-serif}`;
function printLabelFrame(pages,keep){const list=pages||[...$('#printarea').querySelectorAll('.labelpage')];if(!list.length){toast('宛名に入れる内容がありません。');return}let frame=$('#labelframe');if(!frame){frame=document.createElement('iframe');frame.id='labelframe';frame.setAttribute('aria-hidden','true');frame.style.cssText='position:fixed;left:-12000px;top:0;width:297mm;height:210mm;border:0';document.body.append(frame)}const doc=frame.contentDocument;doc.open();doc.write(`<!doctype html><html><head><meta charset="utf-8"><title>宛名</title><style>${labelPrintCss}</style></head><body>${list.map(page=>page.outerHTML).join('')}</body></html>`);doc.close();const win=frame.contentWindow;const finish=()=>{win.removeEventListener('afterprint',finish);if(!keep)window.dispatchEvent(new Event('afterprint'))};win.addEventListener('afterprint',finish);if(!keep)armPrintWatch();win.focus();try{win.print()}catch(e){win.removeEventListener('afterprint',finish);if(!keep){unlockPrinting();closePrintPreview();$('#printarea').replaceChildren()}toast('印刷画面を開けませんでした。もう一度お試しください。')}}
$('#previewclose').onclick=()=>{unlockPrinting();closePrintPreview();$('#printarea').replaceChildren()};
$('#previewlarger').onclick=()=>{previewFit=false;previewZoom=previewSteps.find(z=>z>previewZoom+.001)??previewSteps.at(-1);applyPreviewZoom()};
$('#previewsmaller').onclick=()=>{previewFit=false;previewZoom=[...previewSteps].reverse().find(z=>z<previewZoom-.001)??previewSteps[0];applyPreviewZoom()};
$('#previewfit').onclick=()=>{previewFit=true;applyPreviewZoom();$('#printarea').scrollLeft=0};
$('#print').onclick=()=>{if(dirty){$('#formerror').textContent='印刷する前に変更を保存してください。';$('#formerror').scrollIntoView();return}if(!editing?.id)return;queuePrint([editing],false,false,false)};
function currentPreviewOrder(){
 if(editorReadOnly())return structuredClone(editing);
 const values=gather(editing?.status||'下書き');
 const order={...structuredClone(editing||{}),...values,assigneeName:values.owner||''};
 order.items=[...$('#items').querySelectorAll('.item')].map(row=>{
  const item={};row.querySelectorAll('[data-key]').forEach(el=>{item[el.dataset.key]=el.dataset.key==='quantity'?(el.value===''?'':Number(el.value)):el.value.trim()});return item;
 });
 return order;
}
$('#largepreview').onclick=()=>{
 const order=currentPreviewOrder();if(!order)return;
 $('#formerror').textContent='';
 queuePrint([order],false,true,false,dirty||!editing?.id);
};
function syncPreviewButton(){$('#largepreview').hidden=false}
new MutationObserver(syncPreviewButton).observe($('#print'),{attributes:true,attributeFilter:['hidden']});syncPreviewButton();
$('#printlabelsheet').onclick=()=>{if(dirty){$('#formerror').textContent='印刷する前に変更を保存してください。';$('#formerror').scrollIntoView();return}if(!editing?.id)return;queuePrint([editing],true)};
const labelPlaces=['左上','右上','左下','右下'];
function labelCard(){const el=document.createElement('tr');el.className='labelcard';el.innerHTML='<td class="labelposition"></td><td><input aria-label="顧客名" data-key="customer" maxlength="40" list="customers" autocomplete="off"></td><td class="labelcol-mid"><input aria-label="管理番号" data-key="management" maxlength="20" autocomplete="off"></td><td class="labelcol-mid"><input aria-label="部署名" data-key="department" maxlength="40" autocomplete="off"></td><td><input aria-label="氏名" data-key="employee" maxlength="20" placeholder="様は自動で付きます" autocomplete="off"></td><td><select aria-label="受付内容" data-key="request"><option>新規</option><option>サイズ交換</option><option selected>消耗・破損交換</option><option>追加・補充</option><option>復職</option><option>異動・移動</option><option>その他</option></select></td><td><button type="button" class="quiet labelclear">クリア</button></td>';el.querySelector('.labelclear').onclick=()=>{el.querySelectorAll('input').forEach(input=>input.value='');el.querySelector('[data-key="request"]').value='消耗・破損交換'};return el}

function renumberLabelCards(){[...$('#labelcards').children].forEach((card,i)=>{const page=Math.floor(i/4)+1;card.querySelector('.labelposition').textContent=`${page}枚目・${labelPlaces[i%4]}`})}
function resetLabelCards(){const box=$('#labelcards');box.replaceChildren();for(let i=0;i<4;i++)box.append(labelCard());renumberLabelCards()}
function labelOrders(){const list=[...$('#labelcards').querySelectorAll('.labelcard')].map(card=>{const order={};card.querySelectorAll('[data-key]').forEach(el=>order[el.dataset.key]=el.value.trim());const address=['customer','management','department','employee'].some(key=>order[key]);return address?order:null});while(list.length>4&&list.slice(0,4).every(order=>!order))list.splice(0,4);while(list.length>4&&list.slice(-4).every(order=>!order))list.splice(-4,4);return list.some(Boolean)?list:[]}
resetLabelCards();
$('#labeladd').onclick=()=>{const box=$('#labelcards');if(box.children.length>=40){toast('一度に印刷できるのは40件までです。');return}for(let i=0;i<4;i++)box.append(labelCard());renumberLabelCards();box.lastElementChild.querySelector('input').focus()};
$('#labelreset').onclick=()=>{if(labelOrders().length&&!confirm('入力した宛名をすべて消しますか？'))return;resetLabelCards()};
$('#labelprint').onclick=()=>{const list=labelOrders();if(!list.length){toast('宛名に入れる内容がありません。');return}if(list.length>40){toast('一度に印刷できるのは40件までです。');return}queuePrint(list,true,true,true,false,currentLabelPattern())};
const labelKeys=typeof LABEL_PASTE_KEYS!=='undefined'?LABEL_PASTE_KEYS:['customer','management','department','employee','request'];
const labelRequestDefault=typeof LABEL_REQUEST_DEFAULT!=='undefined'?LABEL_REQUEST_DEFAULT:'消耗・破損交換';
function ensureLabelCardCount(need){
 const box=$('#labelcards');
 const want=Math.min(typeof LABEL_PASTE_MAX_ROWS!=='undefined'?LABEL_PASTE_MAX_ROWS:40,Math.max(4,need|0));
 let target=want;while(target%4)target+=1;target=Math.min(typeof LABEL_PASTE_MAX_ROWS!=='undefined'?LABEL_PASTE_MAX_ROWS:40,target);
 while(box.children.length<target)box.append(labelCard());
 renumberLabelCards();
}
function applyLabelPasteUpdates(updates){
 const box=$('#labelcards');
 for(const item of updates){
  const card=box.children[item.row];if(!card)continue;
  const el=card.querySelector(`[data-key="${item.key}"]`);if(!el)continue;
  if(item.key==='request')el.value=item.value===''?labelRequestDefault:item.value;
  else el.value=item.value;
 }
}
$('#labelcards').addEventListener('paste',e=>{
 const field=e.target&&e.target.closest&&e.target.closest('[data-key]');
 if(!field||!field.closest('.labelcard'))return;
 if(typeof parseLabelClipboard!=='function'||typeof planLabelPaste!=='function')return;
 const text=e.clipboardData?e.clipboardData.getData('text/plain'):'';
 const grid=parseLabelClipboard(text);
 if(!grid)return;
 e.preventDefault();
 const card=field.closest('.labelcard');
 const cards=[...$('#labelcards').querySelectorAll('.labelcard')];
 const originRow=Math.max(0,cards.indexOf(card));
 const originCol=Math.max(0,labelKeys.indexOf(field.dataset.key));
 const planned=planLabelPaste({rows:grid,originRow,originCol,cardCount:cards.length});
 if(!planned.ok){toast(planned.message);return}
 const readCurrent=(row,key)=>{
  const el=cards[row]&&cards[row].querySelector(`[data-key="${key}"]`);
  return el?el.value:'';
 };
 if(typeof labelPasteNeedsConfirm==='function'&&labelPasteNeedsConfirm(planned.updates,readCurrent)){
  if(!confirm('貼り付け先に既存の入力があります。置き換えますか？'))return;
 }
 ensureLabelCardCount(planned.needCards);
 applyLabelPasteUpdates(planned.updates);
 toast(`${planned.rowCount}行を宛名に反映しました。`);
});
$('#labelrequestapply').onclick=()=>{
 if(typeof planLabelRequestBatch!=='function')return;
 const bulk=$('#labelrequestbulk');
 const cards=[...$('#labelcards').querySelectorAll('.labelcard')];
 const slots=cards.map(card=>{const slot={};card.querySelectorAll('[data-key]').forEach(el=>slot[el.dataset.key]=el.value);return slot});
 const planned=planLabelRequestBatch({request:bulk?bulk.value:'',slots});
 if(!planned.ok){toast(planned.message);return}
 if(!confirm(planned.confirmMessage))return;
 for(const row of planned.targets){
  const el=cards[row]&&cards[row].querySelector('[data-key="request"]');
  if(el)el.value=planned.value;
 }
 toast(`${planned.targets.length}件の受付内容を「${planned.value}」に変更しました。`);
};
let labelSets=[];
function labelSlots(){return [...$('#labelcards').querySelectorAll('.labelcard')].map(card=>{const slot={};card.querySelectorAll('[data-key]').forEach(el=>slot[el.dataset.key]=el.value.trim());return slot})}
function normalizeLabelPayload(payload){if(Array.isArray(payload))return{pattern:'4',slots:payload};if(payload&&typeof payload==='object')return{pattern:payload.pattern==='3'?'3':'4',slots:Array.isArray(payload.slots)?payload.slots:[]};return{pattern:'4',slots:[]}}function fillLabelCards(slots){const box=$('#labelcards');const list=Array.isArray(slots)&&slots.length?slots.slice(0,40):[null,null,null,null];while(list.length%4)list.push(null);box.replaceChildren();for(const slot of list){const card=labelCard();if(slot)for(const key of labelKeys){const el=card.querySelector(`[data-key="${key}"]`);if(el)el.value=slot[key]||(key==='request'?'消耗・破損交換':'')}box.append(card)}renumberLabelCards()}
function renderLabelSets(selectId){const list=$('#labellist');if(!list)return;const current=selectId??list.value;list.replaceChildren(new Option('選んでください',''));for(const set of labelSets)list.append(new Option(set.name,set.id));if([...list.options].some(option=>option.value===String(current)))list.value=String(current);const chosen=labelSets.find(set=>set.id===+list.value);if(chosen)$('#labelname').value=chosen.name}
async function loadLabelSets(selectId){if(!currentUser||(currentUser.role!=='owner'&&currentUser.role!=='floor'&&currentUser.role!=='floor2'))return;try{labelSets=await api('/api/label-sets');renderLabelSets(selectId);$('#labelmemoryerror').textContent=''}catch(e){$('#labelmemoryerror').textContent=e.message}}
function chosenLabelSet(){const set=labelSets.find(set=>set.id===+$('#labellist').value);if(!set)throw Error('呼び出す宛名を選んでください。');return set}
$('#labellist').onchange=()=>{const set=labelSets.find(item=>item.id===+$('#labellist').value);if(set)$('#labelname').value=set.name};
$('#labelload').onclick=async()=>{try{const selectedId=+$('#labellist').value;if(!selectedId)throw Error('呼び出す宛名を選んでください。');await loadLabelSets(selectedId);const set=chosenLabelSet();if(labelOrders().length&&!confirm(`「${set.name}」の内容に置き換えますか？`))return;const remembered=normalizeLabelPayload(set.payload);fillLabelCards(remembered.slots);setLabelPattern(remembered.pattern);$('#labelmemoryerror').textContent='';toast('記憶した宛名を呼び出しました。')}catch(e){$('#labelmemoryerror').textContent=e.message}};
async function saveLabelSet(update){try{$('#labelmemoryerror').textContent='';const chosen=update?chosenLabelSet():null;if(update&&!confirm(`「${chosen.name}」を今の入力で上書きしますか？`))return;const saved=await api('/api/label-sets/save',Object.assign({name:$('#labelname').value,slots:labelSlots(),pattern:currentLabelPattern()},update?{id:chosen.id,version:chosen.version}:{}));await loadLabelSets(saved.id);toast('宛名を記憶しました。')}catch(e){$('#labelmemoryerror').textContent=e.message}}
$('#labelsave').onclick=()=>saveLabelSet(false);
$('#labelupdate').onclick=()=>saveLabelSet(true);
$('#labeldelete').onclick=async()=>{try{const set=chosenLabelSet();if(!confirm(`「${set.name}」を削除しますか？今の入力欄はそのまま残ります。`))return;await api('/api/label-sets/delete',{id:set.id,version:set.version});$('#labelname').value='';await loadLabelSets();toast('記憶した宛名を削除しました。')}catch(e){$('#labelmemoryerror').textContent=e.message}};
const NOTICE_DEFAULT_TO='サザエ食品',NOTICE_DEFAULT_PLACE='宮の森事務所';
function ensureNoticeDefaults(){const to=$('#noticeto'),place=$('#noticeplace');if(to&&!String(to.value||'').trim())to.value=NOTICE_DEFAULT_TO;if(place&&!String(place.value||'').trim())place.value=NOTICE_DEFAULT_PLACE}
document.querySelectorAll('input[name="labelpattern"]').forEach(el=>{el.addEventListener('change',()=>setLabelPattern(el.value))});
applyLabelPatternUi(currentLabelPattern());
ensureNoticeDefaults();
function setLabelDeskMode(mode){
 const notice=mode==='notice';
 $('#labelmodeaddress').classList.toggle('on',!notice);
 $('#labelmodenotice').classList.toggle('on',notice);
 $('#labelmodeaddress').setAttribute('aria-pressed',String(!notice));
 $('#labelmodenotice').setAttribute('aria-pressed',String(notice));
 $('#labeladdresspanel').hidden=notice;
 $('#labelnoticepanel').hidden=!notice;
 if(notice){ensureNoticeDefaults();if(!$('#noticedate').value)$('#noticedate').value=today()}
}
$('#labelmodeaddress').onclick=()=>setLabelDeskMode('address');
$('#labelmodenotice').onclick=()=>setLabelDeskMode('notice');
const noticeWeekdays=['日','月','火','水','木','金','土'];
function formatNoticeDate(iso){
 const m=String(iso||'').match(/^(\d{4})-(\d{2})-(\d{2})$/);
 if(!m)return '';
 const d=new Date(+m[1],+m[2]-1,+m[3],12,0,0);
 if(Number.isNaN(d.getTime()))return '';
 return `${d.getMonth()+1}月${d.getDate()}日（${noticeWeekdays[d.getDay()]}）`;
}
function fitNoticePage(page){
 let host=$('#labelfit');
 if(!host){host=document.createElement('iframe');host.id='labelfit';host.setAttribute('aria-hidden','true');host.style.cssText='position:fixed;left:-12000px;top:0;width:297mm;height:210mm;border:0;visibility:hidden';document.body.append(host)}
 const doc=host.contentDocument;
 doc.open();
 doc.write(`<!doctype html><html><head><meta charset="utf-8"><style>${labelPrintCss}</style></head><body></body></html>`);
 doc.close();
 const body=doc.body;
 body.replaceChildren(page);
 void body.offsetHeight;
 const mm=px=>px*25.4/96;
 const box=page.querySelector('.noticebody')||page;
 const boxW=()=>{const s=doc.defaultView.getComputedStyle(box);return Math.max(20,mm(box.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight))-4)};
 const rowH=()=>Math.max(12,mm(box.clientHeight)/3.6);
 const to=page.querySelector('.noticeto'),line=page.querySelector('.noticeline'),ask=page.querySelector('.noticeask');
 const w=boxW(),h=rowH();
 const toSize=fitLabelText(to.textContent,w,h*.95,72);
 const lineSize=fitLabelText(line.textContent,w,h*.95,60);
 const askSize=fitLabelText(ask.textContent,w,h*.7,48);
 if(toSize)to.style.fontSize=toSize+'pt';
 if(lineSize)line.style.fontSize=lineSize+'pt';
 if(askSize)ask.style.fontSize=askSize+'pt';
 clampLabel(to);clampLabel(line);clampLabel(ask);
 body.replaceChildren();
}
function deliveryNoticePage({to,date,place}){
 const page=document.createElement('section');
 page.className='labelpage noticepage';
 const body=document.createElement('div');
 body.className='noticebody';
 const dest=document.createElement('p');
 dest.className='noticeto';
 dest.textContent=`${to} 様`;
 const line=document.createElement('p');
 line.className='noticeline';
 line.textContent=`${formatNoticeDate(date)}${place}へ`;
 const ask=document.createElement('p');
 ask.className='noticeask';
 ask.textContent='納品をお願いします。';
 body.append(dest,line,ask);
 page.append(body);
 fitNoticePage(page);
 return page;
}
function queueNoticePrint(){
 $('#labelnoticeerror').textContent='';
 const to=String($('#noticeto').value||'').trim();
 const date=$('#noticedate').value;
 const place=String($('#noticeplace').value||'').trim();
 if(!to){$('#labelnoticeerror').textContent='宛先を入力してください。';return}
 if(!date||!formatNoticeDate(date)){$('#labelnoticeerror').textContent='納品日を選んでください。';return}
 if(!place){$('#labelnoticeerror').textContent='納品先を入力してください。';return}
 if(guardPrinting())return;
 printing=true;pendingPrintIds=[];
 try{
  $('#printarea').replaceChildren(deliveryNoticePage({to,date,place}));
  openPrintPreview();
 }catch(e){
  unlockPrinting();closePrintPreview();$('#printarea').replaceChildren();
  toast('印刷画面を開けませんでした。もう一度お試しください。');
 }
}
$('#noticeprint').onclick=queueNoticePrint;


// Personal input presets are fetched for the authenticated account on each open.
const presetBox=document.createElement('details');presetBox.className='presetbox';presetBox.open=true;
presetBox.innerHTML=`<summary>自分用ひな形</summary><div class="presetpanes"><section class="presetpane"><div class="presethead"><h3>呼び出す</h3><span class="presetheadactions"><button type="button" class="primary" id="presetimport">Excelから読み込む</button><button type="button" id="presetbatchopen">まとめて登録</button></span><input id="presetsheetfile" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden></div><div class="presetrow"><label>フォルダ<select id="presetfolders"><option value="*">すべて</option></select></label><label>ひな形<select id="presetlist"><option value="">選んでください</option></select></label></div><div class="presetrow presetactions"><button type="button" class="primary" id="presetload">指示書として出す</button><button type="button" id="presetdelete">削除</button></div></section><section class="presetpane"><h3>保存する</h3><div class="presetrow"><label>フォルダ<input id="presetfolder" list="presetfoldernames" maxlength="80" placeholder="例：担当顧客（空欄は未分類）"></label><label>保存名<input id="presetname" maxlength="80" placeholder="例：白衣・新規セット"></label></div><div class="presetrow presetactions"><button type="button" class="primary" id="presetsave">新しく保存</button><button type="button" id="presetupdate">上書きする</button></div></section></div><datalist id="presetfoldernames"></datalist><div class="preseterror" id="preseterror" role="alert"></div>`;
document.querySelector('.sheettools').after(presetBox);
let personalPresets=[],presetBusy=false,presetEpoch=0;
function drawPresetList(selected=''){
 const folder=$('#presetfolders').value;
 $('#presetlist').innerHTML='<option value="">選んでください</option>'+personalPresets.filter(p=>folder==='*'||p.folder===folder).map(p=>`<option value="${p.id}">${esc(p.folder||'未分類')} ／ ${esc(p.name)}</option>`).join('');$('#presetlist').value=String(selected);
}
async function fetchPresets(selected=''){
 const epoch=++presetEpoch;const uid=currentUser?.id;const list=await api('/api/templates');if(epoch!==presetEpoch||uid!==currentUser?.id)return;
 personalPresets=list;const folders=[...new Set(list.map(p=>p.folder))];
 $('#presetfolders').innerHTML='<option value="*">すべて</option>'+folders.map(f=>`<option value="${esc(f)}">${esc(f||'未分類')}</option>`).join('');
 $('#presetfoldernames').innerHTML=folders.filter(Boolean).map(f=>`<option value="${esc(f)}">`).join('');drawPresetList(selected);
}
const sheetOpenEditor=openEditor;
function syncMethodNote(){const sel=$('#form').elements.method,note=$('#form').elements.methodNote;if(!sel||!note)return;const other=sel.value==='その他';note.hidden=!other;note.required=other} $('#form').elements.method.addEventListener('change',syncMethodNote);
openEditor=function(o=null){sheetOpenEditor(o);syncMethodNote();personalPresets=[];presetEpoch++;presetBox.open=true;presetBox.hidden=editorReadOnly();$('#presetfolder').value='';$('#presetname').value='';$('#preseterror').textContent='';$('#presetfolders').innerHTML='<option value="*">すべて</option>';drawPresetList();if(!presetBox.hidden)fetchPresets().catch(e=>$('#preseterror').textContent=e.message)};
$('#presetfolders').onchange=()=>{drawPresetList();$('#presetfolder').value=$('#presetfolders').value==='*'?'':$('#presetfolders').value;$('#presetname').value=''};
$('#presetlist').onchange=()=>{const p=personalPresets.find(p=>p.id===+$('#presetlist').value);$('#presetfolder').value=p?.folder||'';$('#presetname').value=p?.name||''};
async function presetAction(action){if(presetBusy||editorReadOnly())return;presetBusy=true;$('#preseterror').textContent='';presetBox.querySelectorAll('button').forEach(b=>b.disabled=true);try{await action()}catch(e){$('#preseterror').textContent=e.message}finally{presetBusy=false;presetBox.querySelectorAll('button').forEach(b=>b.disabled=false)}}
async function savePreset(update){return presetAction(async()=>{const chosen=personalPresets.find(p=>p.id===+$('#presetlist').value);if(update&&!chosen)throw Error('上書きするひな形を選んでください。');if(update&&!confirm('選択したひな形を現在の入力内容で上書きしますか？'))return;const data={folder:$('#presetfolder').value,name:$('#presetname').value,payload:gather('下書き')};if(update){data.id=chosen.id;data.version=chosen.version}const saved=await api('/api/templates/save',data);await fetchPresets(saved.id);toast('自分用ひな形に保存しました。指示書の投函はまだ行っていません。')})}
$('#presetsave').onclick=()=>savePreset(false);$('#presetupdate').onclick=()=>savePreset(true);
$('#presetdelete').onclick=()=>presetAction(async()=>{const p=personalPresets.find(p=>p.id===+$('#presetlist').value);if(!p)throw Error('削除するひな形を選んでください。');if(!confirm(`「${p.name}」を削除しますか？ 登録済みの指示書は残ります。`))return;await api('/api/templates/delete',{id:p.id,version:p.version});await fetchPresets();$('#presetfolder').value='';$('#presetname').value='';toast('ひな形を削除しました。')});
$('#presetload').onclick=()=>presetAction(async()=>{const p=personalPresets.find(p=>p.id===+$('#presetlist').value);if(!p)throw Error('呼び出すひな形を選んでください。');if(dirty&&!confirm('現在の入力内容をひな形の内容に置き換えて、新しい指示書を作成しますか？'))return;const d=structuredClone(p.payload);d.markShipDate=d.markShipDate||d.ship||'';for(const k of ['delivery','employmentDate','finishDate','markShipDate'])d[k]='';d.received=today();sheetOpenEditor();for(const[k,v]of Object.entries(d)){const el=$('#form').elements.namedItem(k);if(el&&typeof v!=='object')el.value=v}$('#items').innerHTML='';(d.items?.length?d.items:[{},{},{}]).forEach(addItem);applySheetTemplate(null);syncMethodNote();dirty=true;toast('新しい指示書として呼び出しました。内容と期日を確認して投函してください。')});
$('#presetimport').onclick=()=>$('#presetsheetfile').click();
$('#presetsheetfile').onchange=()=>presetAction(async()=>{const file=$('#presetsheetfile').files&&$('#presetsheetfile').files[0];$('#presetsheetfile').value='';if(!file)return;if(!/\.xlsx$/i.test(file.name))throw Error('Excelファイル（.xlsx）を選んでください。');if(file.size>6000000)throw Error('ファイルが大きすぎます。');if(dirty&&!confirm('現在の入力内容をExcelの内容に置き換えて、新しい指示書を作成しますか？'))return;const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode(...bytes.subarray(i,i+0x8000));const result=await api('/api/templates/import',{filename:file.name,data:btoa(binary)});const d=result.payload||{};d.received=today();for(const k of ['delivery','employmentDate','finishDate','markShipDate'])d[k]='';sheetOpenEditor();for(const[k,v]of Object.entries(d)){const el=$('#form').elements.namedItem(k);if(el&&typeof v!=='object'&&el.name!=='assigneeId')el.value=v}$('#items').innerHTML='';(d.items?.length?d.items:[{},{},{}]).forEach(addItem);applySheetTemplate(null);syncMethodNote();const stem=file.name.replace(/\.xlsx$/i,'');$('#presetname').value=stem.slice(0,80);dirty=true;toast('Excelの内容を入力しました。受付日は今日、その他の日付は空欄、受付者は今のログインのままです。保存名を確認して「新しく保存」するとひな形になります。'+(result.warnings?.length?result.warnings.join(''):''))});
const presetBatch=document.createElement('dialog');presetBatch.id='presetbatch';
presetBatch.innerHTML=`<form><h2>Excelをまとめてひな形にする</h2><p>フォルダを選ぶと、そのフォルダ名がひな形のフォルダ、ファイル名が保存名になります。ファイルだけ選ぶときは、保存先フォルダを指定します。指示書は作られません。</p><div class="presetrow presetactions"><button type="button" id="presetbatchdirbtn">フォルダを選ぶ</button><button type="button" id="presetbatchfilebtn">ファイルを選ぶ</button></div><label id="presetbatchfolderwrap" hidden>保存先フォルダ<input id="presetbatchfolder" maxlength="80" placeholder="例：宮の沢明日佳病院"></label><div id="presetbatchlist"></div><p id="presetbatcherror" class="preseterror" role="alert"></p><div class="presetrow presetactions"><button type="button" class="primary" id="presetbatchrun">一括登録</button><button type="button" id="presetbatchclose">閉じる</button></div></form>`;
document.body.append(presetBatch);
const presetBatchDir=document.createElement('input');presetBatchDir.type='file';presetBatchDir.webkitdirectory=true;presetBatchDir.multiple=true;presetBatchDir.hidden=true;
const presetBatchFiles=document.createElement('input');presetBatchFiles.type='file';presetBatchFiles.multiple=true;presetBatchFiles.accept='.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';presetBatchFiles.hidden=true;
document.body.append(presetBatchDir,presetBatchFiles);
let presetBatchRows=[];
function presetStem(name){return name.replace(/\.xlsx$/i,'').slice(0,80)}
function presetParent(file){const parts=String(file.webkitRelativePath||'').replaceAll('\\','/').split('/').filter(Boolean);return parts.length>=2?parts[parts.length-2].slice(0,80):''}
function presetBatchCollision(folder,name,index){const saved=personalPresets.find(p=>p.folder===folder&&p.name===name);if(saved)return '同じフォルダに同名のひな形があります';const again=presetBatchRows.findIndex((row,i)=>i<index&&row.folder===folder&&row.name===name);return again>=0?'この一覧の中に同じフォルダと保存名があります':''}
function renderPresetBatch(){const loose=presetBatchRows.some(row=>!row.fromPath);$('#presetbatchfolderwrap').hidden=!loose;$('#presetbatchlist').innerHTML=presetBatchRows.length?presetBatchRows.map((row,i)=>{const dup=presetBatchCollision(row.folder,row.name,i);return `<div class="batchrow" data-i="${i}"><b>${esc(row.file.name)}</b><label>フォルダ<input data-folder maxlength="80" value="${esc(row.folder)}"></label><label>保存名<input data-name maxlength="80" value="${esc(row.name)}"></label>${dup?`<label>同名<select data-choice><option value="skip"${row.choice==='overwrite'?'':' selected'}>登録しない</option><option value="overwrite"${row.choice==='overwrite'?' selected':''}>上書きする</option></select></label><small>${esc(dup)}</small>`:''}${row.problem?`<small class="batchproblem">${esc(row.problem)}</small>`:''}</div>`}).join(''):'<p>Excelファイル（.xlsx）を選んでください。</p>'}
function readPresetBatch(){const looseFolder=$('#presetbatchfolder')?.value.trim().slice(0,80)||'';return [...$('#presetbatchlist').querySelectorAll('.batchrow')].map(el=>{const i=+el.dataset.i;const src=presetBatchRows[i];const folder=(el.querySelector('[data-folder]').value.trim()||(!src.fromPath?looseFolder:'')).slice(0,80);const name=el.querySelector('[data-name]').value.trim().slice(0,80);return {file:src.file,fromPath:src.fromPath,problem:src.problem,folder,name,choice:el.querySelector('[data-choice]')?.value||'save'}})}
function takePresetBatch(files,fromFolder){presetBatchRows=[...files].filter(file=>/\.xlsx$/i.test(file.name)&&!file.name.startsWith('~$')).map(file=>{const folder=fromFolder?presetParent(file):'';const problem=file.size>6000000?'ファイルが大きすぎます。':'';return {file,fromPath:!!folder,folder,name:presetStem(file.name),choice:'skip',problem}});$('#presetbatcherror').textContent=presetBatchRows.length?'':'Excelファイル（.xlsx）がありません。';renderPresetBatch()}
async function excelPayload(file){const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode(...bytes.subarray(i,i+0x8000));const result=await api('/api/templates/import',{filename:file.name,data:btoa(binary)});const payload=result.payload||{};payload.received=today();for(const key of ['delivery','employmentDate','finishDate','markShipDate'])payload[key]='';if(currentUser?.id){payload.assigneeId=currentUser.id;payload.owner=currentUser.name||''}return {payload,warnings:result.warnings||[]}}
presetBatch.querySelector('form').onsubmit=e=>e.preventDefault();
$('#presetbatchopen').onclick=()=>{if(editorReadOnly())return;presetBatchRows=[];$('#presetbatchfolder').value='';$('#presetbatcherror').textContent='';renderPresetBatch();presetBatch.showModal()};
$('#presetbatchclose').onclick=()=>presetBatch.close();
presetBatch.addEventListener('cancel',e=>{e.preventDefault();presetBatch.close()});
$('#presetbatchdirbtn').onclick=()=>presetBatchDir.click();
$('#presetbatchfilebtn').onclick=()=>presetBatchFiles.click();
presetBatchDir.onchange=()=>{takePresetBatch(presetBatchDir.files,true);presetBatchDir.value=''};
presetBatchFiles.onchange=()=>{takePresetBatch(presetBatchFiles.files,false);presetBatchFiles.value=''};
$('#presetbatchrun').onclick=async()=>{const rows=readPresetBatch();presetBatchRows=rows;const pending=rows.filter(row=>!row.problem);const needsChoice=pending.some((row,i)=>row.choice==='save'&&presetBatchCollision(row.folder,row.name,rows.indexOf(row)));if(needsChoice){$('#presetbatcherror').textContent='同名のひな形があります。登録しないか、上書きするかを選んでください。';renderPresetBatch();return}if(!pending.length){$('#presetbatcherror').textContent='登録できるExcelファイルがありません。';return}$('#presetbatchrun').disabled=true;$('#presetbatcherror').textContent='';const done=[];try{for(const row of pending){if(!row.folder){done.push(`${row.file.name}：フォルダが空です。`);continue}if(!row.name){done.push(`${row.file.name}：保存名が空です。`);continue}if(row.choice==='skip'&&presetBatchCollision(row.folder,row.name,rows.indexOf(row))){done.push(`${row.file.name}：登録しませんでした。`);continue}try{const imported=await excelPayload(row.file);const data={folder:row.folder,name:row.name,payload:imported.payload};const existing=personalPresets.find(p=>p.folder===row.folder&&p.name===row.name);if(existing&&row.choice==='overwrite'){data.id=existing.id;data.version=existing.version}const saved=await api('/api/templates/save',data);const at=personalPresets.findIndex(p=>p.id===saved.id);if(at>=0)personalPresets[at]=saved;else personalPresets.push(saved);done.push(`${row.folder?row.folder+'／':''}${row.name} を登録しました。`+(imported.warnings.length?imported.warnings.join(''):''))}catch(e){done.push(`${row.file.name}：${e.message}`)}}}finally{$('#presetbatchrun').disabled=false}await fetchPresets().catch(()=>{});$('#presetbatcherror').textContent=done.join('\n');presetBatchRows=[];renderPresetBatch();toast('ひな形の一括登録が終わりました。指示書は作っていません。')};

const zoomLabel=document.createElement('label');zoomLabel.className='zoomlabel';zoomLabel.innerHTML='表示倍率<select id="sheetzoom"><option value="fit">画面の幅に合わせる</option><option value="1">100%</option><option value="0.85">85%</option><option value="0.7">70%</option></select>';const headtools=document.createElement('div');headtools.className='headtools';const closeButton=document.querySelector('#close');closeButton.before(headtools);headtools.append(zoomLabel,closeButton);
const paperElement=document.querySelector('.papersheet');
function fitPaper(){const v=$('#sheetzoom').value;paperElement.style.zoom=v==='fit'?Math.min(1,Math.max(.4,(document.querySelector('#editor .dialogbody').clientWidth-72)/1240)):Number(v)}
$('#sheetzoom').onchange=fitPaper;new ResizeObserver(fitPaper).observe(document.querySelector('#editor .dialogbody'));
const itemWidths=[22.5,19.25,21.75,9.625,9,8.5,27];
const blanks=document.createElement('div');blanks.className='blankitems';blanks.setAttribute('aria-hidden','true');$('#items').after(blanks);
function fillBlankRows(){blanks.innerHTML=''}
new MutationObserver(fillBlankRows).observe($('#items'),{childList:true});fillBlankRows();

