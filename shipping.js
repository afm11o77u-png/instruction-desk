let shipShowAll=false;
let shipOffset=0, shipTotal=0, shipBusy=false, shipQueued=false, shipEditing=null, shipDirty=false, shipNames=[];
let shipItems=[];
const SHIPINFO_SCREEN='shipinfo';
/* 出荷情報（既存781件）と出荷情報一覧（別保存・空から）は同じ画面・同じ処理で、保存先だけ分ける。 */
let shipEditBase=null;
function shipIsList(){return typeof view!=='undefined'&&view==='shiplist'}
function shipBase(){return shipIsList()?'/api/shipment-list':'/api/shipments'}
function shipLabel(){return shipIsList()?'出荷情報一覧':'出荷情報'}
/* 出荷情報一覧では 出荷方法・出荷形態・出荷形態(マーク)・集配時間・営業担当・契約開始・契約終了 を出さない */
const SHIP_LIST_HIDDEN=['method','pack','markPack','timeBand','sales','contractStart','contractEnd'];
function syncShipFields(){const on=shipIsList();const head=document.querySelectorAll('#shipinfodesk thead th');[1,2,3,5,8,9,10].forEach(i=>head[i]?.classList.add('ship-extra'));document.querySelectorAll('#shipinfoform [name]').forEach(el=>{if(SHIP_LIST_HIDDEN.includes(el.name))el.closest('label')?.classList.add('ship-extra')});$('#shipinfodesk')?.classList.toggle('ship-list-mode',on);$('#shipinfoview')?.classList.toggle('ship-list-mode',on)}
function shipOnPage(){return typeof view!=='undefined'&&(view==='shipinfo'||view==='shiplist')}
const shipLists={method:['自社便','福山・佐川','地方便(オリコン)','佐川指定','ヤマト指定'],pack:['たたみ包装','ハンガー'],weekday:['月・木','火・金','水・土','月・水・金','火・木・土','月曜日','火曜日','水曜日','木曜日','金曜日','土曜日','毎日','月1回','月2回','TEL'],timeBand:[...'56789'.split(''),10,11,12,13,14,15,16].map(h=>`${h}:00～${Number(h)+1}:00`),car:['1号車','2号車','3号車','4・8号車','5号車','6号車','7号車','9号車(アドバンス)','10号車','11号車(アドバンス)','12号車'],manager:['森末支店長','横内課長','横内次長','山田課長','河口係長','藤浪係長','藤澤代理','田中(本社)','藤本','安藤','田中(発寒支店)','小野寺','大島','大本','菅沼']};
shipLists.sales=shipLists.manager;shipLists.markPack=shipLists.pack;
function shipCell(value){return esc(value)||'—'}
function paintShipClear(){const input=$('#shipinfocustomer');const btn=input.parentElement.querySelector('.placementclear');btn.hidden=!input.value}
function hideShipChoices(){const box=$('#shipinfosearch .placementpicks');if(box)box.hidden=true}
function showShipChoices(){const input=$('#shipinfocustomer');const box=input.parentElement.querySelector('.placementpicks');const q=input.value.trim();const rows=q?shipNames.filter(name=>name.includes(q)):shipNames.slice();box.innerHTML=rows.length?rows.map(name=>`<button type="button" class="placementpick">${esc(name)}</button>`).join(''):'<p class="placementpickempty">該当する名前はありません。</p>';box.hidden=false}
function fillShipSelect(select, names, current){const values=['',...names];if(current&&!values.includes(current))values.push(current);select.innerHTML=values.map(name=>`<option value="${esc(name)}">${name?esc(name):'選択してください'}</option>`).join('');select.value=current||''}
function paintShipRow(row, item){row.dataset.id=String(item.id);row.innerHTML=`<td><span class="dm-primary">${shipCell(item.customer)}</span></td><td class="ship-extra">${shipCell(item.method)}</td><td class="ship-extra">${shipCell(item.pack)}</td><td class="ship-extra">${shipCell(item.markPack)}</td><td>${shipCell(item.weekday)}</td><td class="ship-extra">${shipCell(item.timeBand)}</td><td>${shipCell(item.car)}</td><td>${shipCell(item.manager)}</td><td class="ship-extra">${shipCell(item.sales)}</td><td class="ship-extra">${shipCell(item.contractStart)}</td><td class="ship-extra">${shipCell(item.contractEnd)}</td><td><button type="button" class="quiet shipopen" data-id="${item.id}">開く</button></td>`;row.querySelector('button').onclick=()=>openShipment(item)}
function shipGridCard(item){
  if(shipIsList())return `<article class="dm-card" data-id="${item.id}"><div class="dm-primary">${shipCell(item.customer)}</div><div class="dm-meta"><span>${shipCell(item.weekday)}</span><span>${shipCell(item.car)}</span><span>${shipCell(item.manager)}</span></div><div class="dm-actions"><button type="button" class="quiet shipopen" data-id="${item.id}">開く</button></div></article>`;
  return `<article class="dm-card" data-id="${item.id}"><div class="dm-primary">${shipCell(item.customer)}</div><div class="dm-meta"><span>${shipCell(item.method)}</span><span>${shipCell(item.pack)}</span><span>${shipCell(item.weekday)}</span><span>${shipCell(item.timeBand)}</span></div><div class="dm-secondary">${shipCell(item.car)}　${shipCell(item.manager)}　${shipCell(item.sales)}</div><div class="dm-secondary">契約 ${shipCell(item.contractStart)} ～ ${shipCell(item.contractEnd)}</div><div class="dm-actions"><button type="button" class="quiet shipopen" data-id="${item.id}">開く</button></div></article>`;
}
function ensureShipDisplayBar(){
  const stage=$('#shipinfoliststage');
  if(!stage||typeof DisplayMode==='undefined')return;
  const toolbar=$('#shipinfosearch');
  DisplayMode.mount(stage,SHIPINFO_SCREEN,async(mode)=>{
    await DisplayMode.set(SHIPINFO_SCREEN,mode,(id,m)=>api('/api/display-modes',{screenId:id,mode:m}));
    paintShipList();
  },toolbar);
}
function paintShipList(){
  ensureShipDisplayBar();syncShipFields();
  const mode=typeof DisplayMode!=='undefined'?DisplayMode.get(SHIPINFO_SCREEN):'standard';
  const stage=$('#shipinfoliststage');
  const body=$('#shipinfobody');
  const grid=$('#shipinfolistgrid');
  if(!body)return;
  body.innerHTML='';
  if(!shipItems.length){
    if(grid){grid.innerHTML='<div class="dm-empty">該当する出荷情報はありません。</div>';grid.hidden=mode!=='grid'}
    if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,SHIPINFO_SCREEN);
    return;
  }
  for(const item of shipItems){
    const row=document.createElement('tr');
    paintShipRow(row,item);
    body.append(row);
  }
  if(grid){
    grid.innerHTML=shipItems.map(shipGridCard).join('');
    grid.querySelectorAll('.shipopen').forEach(b=>b.onclick=()=>{
      const item=shipItems.find(x=>String(x.id)===String(b.dataset.id));
      if(item)openShipment(item);
    });
    grid.hidden=mode!=='grid';
  }
  if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,SHIPINFO_SCREEN);
}
async function loadShipments(reset){if(shipBusy){if(reset)shipQueued=true;return}shipBusy=true;const base=shipBase();if(reset){shipOffset=0;shipItems=[];$('#shipinfobody').innerHTML='';const g=$('#shipinfolistgrid');if(g)g.innerHTML=''}$('#shipinfostatus').textContent='読み込んでいます。';try{if(typeof ensureDisplayModes==='function')await ensureDisplayModes().catch(()=>{});if(reset){const data=await api(base+'/options');shipNames=data.customers||[];if(data.methods)shipLists.method=data.methods;if(data.packs){shipLists.pack=data.packs;shipLists.markPack=data.packs}if(data.weekdays)shipLists.weekday=data.weekdays;if(data.times)shipLists.timeBand=data.times;if(data.cars)shipLists.car=data.cars;if(data.people){shipLists.manager=data.people;shipLists.sales=data.people}paintShipClear()}let data=await api(base+'?'+new URLSearchParams({customer:$('#shipinfocustomer').value.trim(),offset:String(shipOffset)}));if(shipShowAll){const query=new URLSearchParams({customer:$('#shipinfocustomer').value.trim()});const items=[...data.items];while(items.length<data.total){if(shipQueued)return;query.set('offset',String(items.length));const next=await api(base+'?'+query);if(!next.items.length)break;items.push(...next.items);data.total=next.total;$('#shipinfostatus').textContent=`読み込み中 ${items.length} / ${data.total} 件`;}data.items=items;}if(shipQueued||base!==shipBase())return;shipTotal=data.total;if(!data.items.length&&shipOffset===0){shipItems=[];paintShipList();$('#shipinfostatus').textContent='該当する出荷情報はありません。';$('#shipinfomore').hidden=true;shipBusy=false;return}if(reset)shipItems=data.items.slice();else shipItems=shipItems.concat(data.items);shipOffset=shipItems.length;paintShipList();$('#shipinfostatus').textContent=`${Math.min(shipOffset,shipTotal)} / ${shipTotal} 件`;$('#shipinfomore').hidden=shipOffset>=shipTotal}catch(e){$('#shipinfostatus').textContent=e.message||'読み込めませんでした。'}finally{shipBusy=false;$('#shipinfoexport').hidden=!shipOnPage();$('#shipinfoimport').hidden=!shipOnPage()||currentUser?.role!=='owner';$('#shipinfonew').hidden=!shipOnPage()||currentUser?.role!=='owner';if(shipQueued){shipQueued=false;loadShipments(true)}}}
function shipPayload(form){const v=name=>form.elements[name].value;return {customer:v('customer'),method:v('method'),pack:v('pack'),markPack:v('markPack'),weekday:v('weekday'),timeBand:v('timeBand'),car:v('car'),manager:v('manager'),sales:v('sales'),contractStart:v('contractStart'),contractEnd:v('contractEnd')}}
function openShipment(item){shipEditing=item||null;shipEditBase=shipBase();syncShipFields();shipDirty=false;$('#shipinfoerror').textContent='';const form=$('#shipinfoform');const owner=currentUser?.role==='owner';$('#shipinfonumber').textContent=item?'登録番号 '+(item.number??''):'新規';$('#shipinfotitle').textContent=owner?(item?shipLabel():shipLabel()+'を登録'):(item?.customer||shipLabel());form.elements.customer.value=item?.customer||'';for(const name of ['method','pack','markPack','weekday','timeBand','car','manager','sales']){const key=name==='markPack'?'markPack':name==='timeBand'?'timeBand':name;fillShipSelect(form.elements[name], shipLists[name]||[], item?.[key]||'')}form.elements.contractStart.value=item?.contractStart||'';form.elements.contractEnd.value=item?.contractEnd||'';[...form.elements].forEach(el=>{if(el.name)el.disabled=!owner});$('#shipinfosave').hidden=!owner;$('#shipinfosave').disabled=false;if(!$('#shipinfoview').open)$('#shipinfoview').showModal()}
function closeShipment(){if(shipDirty&&!confirm('保存していない内容を破棄して閉じますか？'))return;shipDirty=false;$('#shipinfoview').close()}
$('#shipinfosearch').onsubmit=e=>{e.preventDefault();hideShipChoices();loadShipments(true)};
$('#shipinfocustomer').addEventListener('focus',showShipChoices);
$('#shipinfocustomer').addEventListener('input',()=>{paintShipClear();showShipChoices();if(!$('#shipinfocustomer').value.trim())loadShipments(true)});
$('#shipinfosearch .placementclear').onclick=()=>{$('#shipinfocustomer').value='';paintShipClear();showShipChoices();$('#shipinfocustomer').focus();loadShipments(true)};
$('#shipinfosearch .placementpicks').addEventListener('mousedown',e=>{const pick=e.target.closest('.placementpick');if(!pick)return;e.preventDefault();$('#shipinfocustomer').value=pick.textContent;paintShipClear();hideShipChoices();loadShipments(true)});
document.addEventListener('mousedown',e=>{if(!e.target.closest('.shipinfofind'))hideShipChoices()});
$('#shipinfomore').onclick=()=>loadShipments(false);
$('#shipinfonew').onclick=()=>openShipment(null);
$('#shipinfoexport').onclick=async()=>{try{const r=await fetch(shipBase()+'/xlsx');if(!r.ok){const d=await r.json();throw Error(d.error||'書き出せませんでした。')}const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download=shipLabel()+'.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000)}catch(e){$('#shipinfostatus').textContent=e.message||'書き出せませんでした。'}};
$('#shipinfoimport').onclick=()=>{$('#shipinfofile').value='';$('#shipinfofile').click()};
$('#shipinfofile').onchange=async()=>{const file=$('#shipinfofile').files&&$('#shipinfofile').files[0];$('#shipinfofile').value='';if(!file)return;if(!/\.xlsx$/i.test(file.name)||file.name.startsWith('~$')){$('#shipinfostatus').textContent='Excelファイル（.xlsx）を選んでください。';return}if(file.size>8000000){$('#shipinfostatus').textContent='ファイルが大きすぎます。';return}const base=shipBase(),label=shipLabel();if(!confirm(label+'にExcelの内容を取り込みます。登録番号がある行は上書き、空欄の行は追加です。ファイルにない行は残り、削除はしません。'))return;$('#shipinfoimport').disabled=true;$('#shipinfostatus').textContent='取り込んでいます。';try{const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode(...bytes.subarray(i,i+0x8000));const result=await api(base+'/import',{filename:file.name,data:btoa(binary)});toast(`${label}に取り込みました${result.master?'（顧客マスタ）':''}。追加 ${result.created}件、修正 ${result.updated}件、変更なし ${result.unchanged}件${result.skipped?`、顧客名なしで飛ばした行 ${result.skipped}件`:''}${result.unknownFlags?`、集配曜日フラグが読めず空欄 ${result.unknownFlags}件`:''}`);await loadShipments(true)}catch(e){$('#shipinfostatus').textContent=e.message||'取り込めませんでした。'}finally{$('#shipinfoimport').disabled=false}};
$('#shipinfoclose').onclick=closeShipment;
$('#shipinfoview').addEventListener('cancel',e=>{e.preventDefault();closeShipment()});
$('#shipinfoform').addEventListener('input',()=>{shipDirty=true});
$('#shipinfoform').onsubmit=async e=>{e.preventDefault();if(currentUser?.role!=='owner'||$('#shipinfosave').disabled)return;const form=$('#shipinfoform');const body=shipPayload(form);const base=shipEditBase||shipBase();if(!body.customer.trim()){$('#shipinfoerror').textContent='顧客を入力してください。';return}$('#shipinfosave').disabled=true;$('#shipinfoerror').textContent='';try{let saved;if(shipEditing){body.id=shipEditing.id;body.version=shipEditing.version;saved=await api(base+'/save',body)}else saved=await api(base,body);shipDirty=false;if(shipEditing){const idx=shipItems.findIndex(x=>x.id===saved.id);if(idx>=0)shipItems[idx]=saved;paintShipList();openShipment(saved);toast('出荷情報を修正しました。')}else{$('#shipinfoview').close();toast('出荷情報を登録しました。');await loadShipments(true)}}catch(err){$('#shipinfoerror').textContent=err.message}finally{$('#shipinfosave').disabled=false}};

const shipAllButton=document.createElement('button');
shipAllButton.type='button';
shipAllButton.id='shipshowall';
shipAllButton.className='quiet';
shipAllButton.textContent='全件表示';
shipAllButton.setAttribute('aria-pressed','false');
$('#shipinfosearch').append(shipAllButton);
shipAllButton.onclick=()=>{shipShowAll=!shipShowAll;shipAllButton.textContent=shipShowAll?'通常表示に戻す':'全件表示';shipAllButton.setAttribute('aria-pressed',String(shipShowAll));loadShipments(true);};
