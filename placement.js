let placementShowAll=false;
let placementOffset=0, placementTotal=0, placementBusy=false, placementQueued=false, placementEditing=null, placementDirty=false;
const placementChoices={customer:[],part:[]};
function placementUrl(id, kind){return '/api/placements/'+id+'/'+kind}
function paintPlacementClear(input){const btn=input.parentElement.querySelector('.placementclear');if(btn)btn.hidden=!input.value}
function hidePlacementChoices(){document.querySelectorAll('.placementpicks').forEach(el=>{el.hidden=true})}
function showPlacementChoices(input, names){const box=input.parentElement.querySelector('.placementpicks');const q=input.value.trim();if(!q&&!names.length){box.hidden=true;box.innerHTML='';return}const rows=q?names.filter(name=>name.includes(q)):names.slice();box.innerHTML=rows.length?rows.map(name=>`<button type="button" class="placementpick">${esc(name)}</button>`).join(''):'<p class="placementpickempty">該当する名前はありません。</p>';box.hidden=false}
function bindPlacementFind(inputId, key){const input=$('#'+inputId);const box=input.parentElement.querySelector('.placementpicks');input.addEventListener('focus',()=>showPlacementChoices(input,placementChoices[key]));input.addEventListener('input',()=>{paintPlacementClear(input);showPlacementChoices(input,placementChoices[key]);if(!input.value.trim())loadPlacements(true)});input.parentElement.querySelector('.placementclear').onclick=()=>{input.value='';paintPlacementClear(input);showPlacementChoices(input,placementChoices[key]);input.focus();loadPlacements(true)};box.addEventListener('mousedown',e=>{const pick=e.target.closest('.placementpick');if(!pick)return;e.preventDefault();input.value=pick.textContent;paintPlacementClear(input);hidePlacementChoices();loadPlacements(true)});input.addEventListener('keydown',e=>{if(e.key==='Escape'){hidePlacementChoices();return}if(box.hidden)return;const buttons=[...box.querySelectorAll('.placementpick')];if(!buttons.length)return;const current=buttons.findIndex(b=>b.classList.contains('active'));if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();const pick=buttons[(current+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length];buttons.forEach(b=>b.classList.remove('active'));pick.classList.add('active');pick.scrollIntoView({block:'nearest'})}else if(e.key==='Enter'&&current>=0){e.preventDefault();input.value=buttons[current].textContent;paintPlacementClear(input);hidePlacementChoices();loadPlacements(true)}})}
async function placementOptions(){const data=await api('/api/placements/options');const fillList=(id,rows)=>{const list=$('#'+id);if(!list)return;list.innerHTML=rows.map(name=>`<option value="${esc(name)}"></option>`).join('')};placementChoices.customer=data.customers||[];placementChoices.part=data.parts||[];fillList('placementcustomers',placementChoices.customer);fillList('placementparts',placementChoices.part);paintPlacementClear($('#placementcustomer'));const customerInput=$('#placementcustomer');const customerBox=customerInput.parentElement.querySelector('.placementpicks');if(customerBox&&!customerBox.hidden)showPlacementChoices(customerInput,placementChoices.customer)}
function placementQuery(){return new URLSearchParams({customer:$('#placementcustomer').value.trim(),offset:String(placementOffset)})}
async function loadPlacements(reset){if(placementBusy){if(reset)placementQueued=true;return}placementBusy=true;if(reset){placementOffset=0;$('#placementgrid').innerHTML=''}$('#placementstatus').textContent='読み込んでいます。';try{if(reset)await placementOptions();let data=await api('/api/placements?'+placementQuery());if(placementShowAll){const query=new URLSearchParams(placementQuery());const items=[...data.items];while(items.length<data.total){if(placementQueued)return;query.set('offset',String(items.length));const next=await api('/api/placements?'+query);if(!next.items.length)break;items.push(...next.items);data.total=next.total;$('#placementstatus').textContent=`読み込み中 ${items.length} / ${data.total} 件`;}data.items=items;}if(placementQueued)return;placementTotal=data.total;const grid=$('#placementgrid');if(!data.items.length&&placementOffset===0){$('#placementstatus').textContent='該当する貼付け位置はありません。';$('#placementmore').hidden=true;placementBusy=false;return}for(const item of data.items){const card=document.createElement('button');card.type='button';card.className='placementcard';card.dataset.id=String(item.id);paintPlacementCard(card,item);grid.append(card)}placementOffset+=data.items.length;$('#placementstatus').textContent=`${Math.min(placementOffset,placementTotal)} / ${placementTotal} 件`;$('#placementmore').hidden=placementOffset>=placementTotal;$('#placementnew').hidden=view!=='placement'||(currentUser?.role!=='owner'&&currentUser?.role!=='floor2')}catch(e){$('#placementstatus').textContent=e.message||'読み込めませんでした。'}finally{placementBusy=false;if(placementQueued){placementQueued=false;loadPlacements(true)}}}
let placementPreviewUrl='';
function clearPlacementPreview(){if(placementPreviewUrl){URL.revokeObjectURL(placementPreviewUrl);placementPreviewUrl=''}}
function paintPlacementCard(card,item){card.innerHTML=item.hasImage?`<img loading="lazy" decoding="async" src="${placementUrl(item.id,'thumb')}?v=${item.version||1}" alt="">`:`<span class="placementmiss">画像が見つかりません</span>`;card.insertAdjacentHTML('beforeend',`<b>${esc(item.customer)||'—'}</b><span>${esc(item.part)||'—'}</span>`);card.onclick=()=>openPlacement(item)}
function applyPlacementZoom(){const img=$('#placementfull');const stage=document.querySelector('.placementstage');if(!img.naturalWidth||!stage?.clientWidth||!stage.clientHeight)return;const fit=Math.min(stage.clientWidth/img.naturalWidth,stage.clientHeight/img.naturalHeight);const scale=(Number($('#placementzoom').value)||100)/100;img.style.width=Math.max(1,Math.round(img.naturalWidth*fit*scale))+'px'}
function fitPlacementStage(){const dialog=$('#placementview');const stage=document.querySelector('.placementstage');const zoomLabel=document.querySelector('#placementzoom')?.closest('label');const fileLabel=document.querySelector('#placementviewfile');if(!dialog?.open||!stage||!zoomLabel)return;const before=stage.getBoundingClientRect().top-dialog.getBoundingClientRect().top+dialog.scrollTop;const fileH=fileLabel&&!fileLabel.hidden?fileLabel.getBoundingClientRect().height+10:0;const room=dialog.clientHeight-before-zoomLabel.getBoundingClientRect().height-fileH-12;stage.style.height=Math.max(180,Math.floor(room))+'px'}
function resetPlacementView(tries){tries=tries||0;const img=$('#placementfull');const stage=document.querySelector('.placementstage');if(img.hidden)return;if((!img.naturalWidth||!stage?.clientHeight)&&tries<8){requestAnimationFrame(()=>resetPlacementView(tries+1));return}const dialog=$('#placementview');if(dialog)dialog.scrollTop=0;$('#placementzoom').value='100';fitPlacementStage();if(stage){stage.scrollTop=0;stage.scrollLeft=0}applyPlacementZoom()}
function showPlacementImage(item){clearPlacementPreview();const img=$('#placementfull');const file=$('#placementviewimage').files[0];if(file){placementPreviewUrl=URL.createObjectURL(file);$('#placementmissing').hidden=true;img.hidden=false;img.src=placementPreviewUrl;if(img.complete)resetPlacementView();return}$('#placementmissing').hidden=!!item?.hasImage;img.hidden=!item?.hasImage;if(item?.hasImage){img.src=placementUrl(item.id,'image')+'?v='+(item.version||1);if(img.complete)resetPlacementView()}else img.removeAttribute('src')}
function openPlacement(item){placementEditing=item;placementDirty=false;$('#placementviewerror').textContent='';$('#placementviewimage').value='';const owner=currentUser?.role==='owner'||currentUser?.role==='floor2';$('#placementviewfields').hidden=!owner;$('#placementviewsave').hidden=!owner;$('#placementviewfile').hidden=!owner;$('#placementviewhint').hidden=!owner;$('#placementnumber').textContent='登録番号 '+(item.number??'');$('#placementtitle').textContent=owner?'貼付け位置':((item.customer||'')+' / '+(item.part||''));$('#placementviewcustomer').value=item.customer||'';$('#placementviewpart').value=item.part||'';$('#placementmeta').textContent='登録 '+String(item.createdAt||'').replace('T',' ')+'　更新 '+String(item.updatedAt||'').replace('T',' ');$('#placementzoom').value='100';if(!$('#placementview').open)$('#placementview').showModal();showPlacementImage(item);requestAnimationFrame(()=>fitPlacementStage())}
function closePlacementView(){if(placementDirty&&!confirm('保存していない内容を破棄して閉じますか？'))return;clearPlacementPreview();placementDirty=false;$('#placementview').close()}
function openPlacementForm(item){placementEditing=item||null;placementDirty=false;$('#placementerror').textContent='';$('#placementedittitle').textContent=item?'貼付け位置を修正':'貼付け位置を登録';const form=$('#placementform');form.reset();if(item){form.elements.customer.value=item.customer||'';form.elements.part.value=item.part||''}$('#placementpreview').hidden=true;$('#placementpreview').removeAttribute('src');$('#placementkeep').hidden=!item;if(!$('#placementedit').open)$('#placementedit').showModal()}
function closePlacementForm(){if(placementDirty&&!confirm('保存していない内容を破棄して閉じますか？'))return;$('#placementedit').close();placementDirty=false}
function fileToBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>{const text=String(reader.result||'');resolve(text.slice(text.indexOf(',')+1))};reader.onerror=()=>reject(Error('画像を読み込めません。'));reader.readAsDataURL(file)})}
$('#placementsearch').onsubmit=e=>{e.preventDefault();hidePlacementChoices();loadPlacements(true)};
bindPlacementFind('placementcustomer','customer');
document.addEventListener('mousedown',e=>{if(!e.target.closest('.placementfind'))hidePlacementChoices()});
$('#placementmore').onclick=()=>loadPlacements(false);
$('#placementviewclose').onclick=closePlacementView;
$('#placementview').addEventListener('cancel',e=>{e.preventDefault();closePlacementView()});
$('#placementfull').addEventListener('load',()=>resetPlacementView());
$('#placementzoom').oninput=()=>{applyPlacementZoom();if(Number($('#placementzoom').value)<=100){const stage=document.querySelector('.placementstage');if(stage){stage.scrollTop=0;stage.scrollLeft=0}}};
$('#placementviewform').addEventListener('input',e=>{if(e.target.id==='placementzoom')return;placementDirty=true});
$('#placementviewimage').onchange=()=>{placementDirty=true;const file=$('#placementviewimage').files[0];if(file&&file.size>10*1024*1024){$('#placementviewerror').textContent='画像は10MBまでです。';$('#placementviewimage').value='';showPlacementImage(placementEditing);return}$('#placementviewerror').textContent='';showPlacementImage(placementEditing)};
$('#placementviewform').onsubmit=async e=>{e.preventDefault();if(!placementEditing||(currentUser?.role!=='owner'&&currentUser?.role!=='floor2'))return;const customer=$('#placementviewcustomer').value.trim();const part=$('#placementviewpart').value.trim();const file=$('#placementviewimage').files[0];if(!customer||!part){$('#placementviewerror').textContent='顧客と貼付け箇所を入力してください。';return}if(file&&file.size>10*1024*1024){$('#placementviewerror').textContent='画像は10MBまでです。';return}if($('#placementviewsave').disabled)return;$('#placementviewsave').disabled=true;$('#placementviewerror').textContent='';try{const body={id:placementEditing.id,version:placementEditing.version,customer,part};if(file){body.filename=file.name;body.data=await fileToBase64(file)}const saved=await api('/api/placements/save',body);placementDirty=false;openPlacement(saved);const card=document.querySelector('.placementcard[data-id="'+saved.id+'"]');if(card)paintPlacementCard(card,saved);toast('貼付け位置を修正しました。')}catch(err){$('#placementviewerror').textContent=err.message}finally{$('#placementviewsave').disabled=false}};
$('#placementnew').onclick=()=>openPlacementForm(null);
$('#placementeditclose').onclick=closePlacementForm;
$('#placementedit').addEventListener('cancel',e=>{e.preventDefault();closePlacementForm()});
$('#placementform').addEventListener('input',()=>{placementDirty=true});
$('#placementform').elements.image.onchange=()=>{const file=$('#placementform').elements.image.files[0];placementDirty=true;if(!file){$('#placementpreview').hidden=true;return}const url=URL.createObjectURL(file);$('#placementpreview').src=url;$('#placementpreview').hidden=false};
$('#placementform').onsubmit=async e=>{e.preventDefault();if(placementBusy)return;const form=$('#placementform');const file=form.elements.image.files[0];if(!placementEditing&&!file){$('#placementerror').textContent='画像を選んでください。';return}if(file&&file.size>10*1024*1024){$('#placementerror').textContent='画像は10MBまでです。';return}placementBusy=true;$('#placementerror').textContent='';try{const body={customer:form.elements.customer.value,part:form.elements.part.value};if(file){body.filename=file.name;body.data=await fileToBase64(file)}let saved;if(placementEditing){body.id=placementEditing.id;body.version=placementEditing.version;saved=await api('/api/placements/save',body)}else saved=await api('/api/placements',body);placementDirty=false;$('#placementedit').close();$('#placementview').close();toast(placementEditing?'貼付け位置を修正しました。':'貼付け位置を登録しました。');await loadPlacements(true);const fresh=[...document.querySelectorAll('.placementcard')];void saved;void fresh}catch(err){$('#placementerror').textContent=err.message}finally{placementBusy=false}};
let stockSection='list'
function normalizeStockSection(section){
  return section==='setup'||section==='move'||section==='period'||section==='orders'?section:'list'
}
function syncStockNav(section){
  stockSection=normalizeStockSection(section)
  document.querySelectorAll('#stocknav .nav').forEach(b=>b.classList.toggle('active',b.dataset.stockSection===stockSection))
}
function tellStockFrame(section){
  const frame=$('#stockframe')
  if(!frame||!frame.contentWindow)return
  try{frame.contentWindow.postMessage({type:'stock-section',section:normalizeStockSection(section)},location.origin)}catch(_){}
}
function showCatalog(which){const stockAllowed=currentUser?.role==='owner'||!!currentUser?.manage||!!currentUser?.overview;if(which==='stock'&&!stockAllowed)which='desk';const titles={desk:'指示書デスク',info:'顧客・商品情報',stock:'在庫管理'};document.body.classList.toggle('catalog-info',which==='info');document.body.classList.toggle('catalog-stock',which==='stock');$('#brandtitle').textContent=titles[which]||titles.desk;document.title=$('#brandtitle').textContent;document.querySelectorAll('#modes .mode').forEach(b=>b.classList.toggle('on',b.dataset.catalog===which));$('#desknav').hidden=which!=='desk';$('#infonav').hidden=which!=='info';const stockNav=$('#stocknav');if(stockNav)stockNav.hidden=which!=='stock';const stock=$('#stockdesk');if(stock)stock.hidden=which!=='stock';if(which==='stock'){syncStockNav('list');const frame=$('#stockframe');const want='/inventory/?embed=1';if(frame&&frame.getAttribute('src')!==want){frame.src=want;frame.onload=()=>{tellStockFrame(stockSection);frame.onload=null}}else tellStockFrame(stockSection);return}const box=which==='info'?'#infonav':'#desknav';const current=document.querySelector(`${box} .nav.active:not([hidden])`)||document.querySelector(`${box} .nav:not([hidden])`);if(current)current.click()}
function showDeskCatalog(){showCatalog('desk')}
function showInfoCatalog(){showCatalog('info')}
document.querySelectorAll('#modes .mode').forEach(b=>b.onclick=()=>showCatalog(b.dataset.catalog));
document.querySelectorAll('#stocknav .nav').forEach(b=>b.onclick=()=>{if(b.dataset.stockSection===stockSection&&document.body.classList.contains('catalog-stock')){tellStockFrame(stockSection);return}syncStockNav(b.dataset.stockSection);if(!document.body.classList.contains('catalog-stock'))showCatalog('stock');else tellStockFrame(stockSection)});
window.addEventListener('message',e=>{if(e.origin!==location.origin||!e.data)return;if(e.data.type==='desk-catalog'){if(e.data.mode==='desk'||e.data.mode==='info')showCatalog(e.data.mode);return}if(e.data.type==='stock-section-changed'&&(e.data.section==='list'||e.data.section==='setup'||e.data.section==='move'||e.data.section==='period'||e.data.section==='orders'))syncStockNav(e.data.section)});

const placementAllButton=document.createElement('button');
placementAllButton.type='button';
placementAllButton.id='placementshowall';
placementAllButton.className='quiet';
placementAllButton.textContent='全件表示';
placementAllButton.setAttribute('aria-pressed','false');
$('#placementsearch').append(placementAllButton);
placementAllButton.onclick=()=>{placementShowAll=!placementShowAll;placementAllButton.textContent=placementShowAll?'通常表示に戻す':'全件表示';placementAllButton.setAttribute('aria-pressed',String(placementShowAll));loadPlacements(true);};

let placementBoardOffset=0, placementBoardTotal=0, placementBoardBusy=false, placementBoardQueued=false;
let placementRequestTarget=null, placementResponding=null;
function canEditPlacement(){return currentUser?.role==='owner'||currentUser?.role==='floor2'}
function canViewPlacement(){return ['owner','sales','floor','floor2'].includes(currentUser?.role)}
function formatPlacementAt(value){return String(value||'').replace('T',' ')}
function placementTargetLabel(item){
  if(!item?.hasPlacement) return '貼付け位置一覧への依頼（画像なし）';
  if(item.placementMissing) return '紐づく貼付け位置が見つかりません';
  return `${item.customer||'—'} ／ ${item.part||'—'}`;
}
async function loadPlacementBoard(reset){
  if(!canViewPlacement()) return;
  if(placementBoardBusy){if(reset)placementBoardQueued=true;return}
  placementBoardBusy=true;
  if(reset){placementBoardOffset=0;$('#placementboardlist').innerHTML=''}
  $('#placementboarderror').textContent='';
  try{
    const status=$('#placementboardstatus').value;
    const query=new URLSearchParams({offset:String(placementBoardOffset)});
    if(status) query.set('status',status);
    const data=await api('/api/placement-corrections?'+query);
    if(placementBoardQueued) return;
    placementBoardTotal=data.total;
    const counts=data.counts||{};
    $('#placementboardcounts').textContent=`未対応 ${counts['未対応']||0}　対応中 ${counts['対応中']||0}　対応済み ${counts['対応済み']||0}`;
    const list=$('#placementboardlist');
    if(!data.items.length&&placementBoardOffset===0){
      list.innerHTML='<p class="duehint">該当する修正依頼はありません。</p>';
      $('#placementboardmore').hidden=true;
      return;
    }
    for(const item of data.items){
      const card=document.createElement('article');
      card.className='placementboardcard'+(item.status==='未対応'?' is-open':'');
      card.dataset.id=String(item.id);
      paintPlacementBoardCard(card,item);
      list.append(card);
    }
    placementBoardOffset+=data.items.length;
    $('#placementboardmore').hidden=placementBoardOffset>=placementBoardTotal;
  }catch(e){
    $('#placementboarderror').textContent=e.message||'読み込めませんでした。';
  }finally{
    placementBoardBusy=false;
    if(placementBoardQueued){placementBoardQueued=false;loadPlacementBoard(true)}
  }
}
function paintPlacementBoardCard(card,item){
  const canEdit=canEditPlacement();
  const link=item.hasPlacement&&!item.placementMissing
    ?`<button type="button" class="quiet placementboardopenimg" data-placement="${item.placementId}">画像を開く</button>`
    :'';
  const respond=canEdit?`<button type="button" class="quiet placementboardrespond" data-id="${item.id}">対応を更新</button>`:'';
  const statusMeta=item.statusAt?`${esc(item.statusByName||'—')}　${esc(formatPlacementAt(item.statusAt))}`:'—';
  const statusClass=item.status==='未対応'?'is-open-status':item.status==='対応中'?'is-doing':'is-done';
  card.innerHTML=`<div class="placementboardtop"><span class="placementboardbadge ${statusClass}">${esc(item.status)}</span><strong>${esc(placementTargetLabel(item))}</strong></div>
    <p class="placementboardbody">${esc(item.body)}</p>
    <p class="placementboardmeta">投稿　${esc(item.authorName||'—')}　${esc(formatPlacementAt(item.createdAt))}</p>
    <p class="placementboardmeta">対応更新　${statusMeta}</p>
    ${item.response?`<p class="placementboardresponse"><span>対応コメント</span>${esc(item.response)}</p>`:''}
    <div class="placementboardactions">${link}${respond}</div>`;
  card.querySelector('.placementboardopenimg')?.addEventListener('click',async ()=>{
    try{
      const placement=await api('/api/placements/'+item.placementId);
      openPlacement(placement);
    }catch(err){toast(err.message||'画像を開けませんでした。')}
  });
  card.querySelector('.placementboardrespond')?.addEventListener('click',()=>openPlacementRespond(item));
}
function openPlacementBoard(){
  if(!canViewPlacement()){toast('貼付け位置は閲覧できません。');return}
  if(!$('#placementboard').open)$('#placementboard').showModal();
  loadPlacementBoard(true);
}
function closePlacementBoard(){$('#placementboard').close()}
function openPlacementRequest(placement){
  if(!canViewPlacement()){toast('貼付け位置は閲覧できません。');return}
  placementRequestTarget=placement||null;
  $('#placementrequesterror').textContent='';
  $('#placementrequestbody').value='';
  $('#placementrequesttitle').textContent=placement?'画像の修正を依頼':'修正を依頼';
  $('#placementrequestlink').textContent=placement
    ?`対象：${placement.customer||'—'} ／ ${placement.part||'—'}`
    :'対象：貼付け位置一覧（画像に紐づけない依頼）';
  if(!$('#placementrequest').open)$('#placementrequest').showModal();
  $('#placementrequestbody').focus();
}
function closePlacementRequest(){$('#placementrequest').close();placementRequestTarget=null}
function openPlacementRespond(item){
  if(!canEditPlacement()){toast('対応状況の変更は主管理者と現場２が行えます。');return}
  placementResponding=item;
  $('#placementresponderror').textContent='';
  $('#placementrespondstatus').value=item.status||'未対応';
  $('#placementrespondcomment').value=item.response||'';
  $('#placementrespondmeta').textContent=`${placementTargetLabel(item)}　／　投稿 ${item.authorName||'—'}　${formatPlacementAt(item.createdAt)}`;
  if(!$('#placementrespond').open)$('#placementrespond').showModal();
}
function closePlacementRespond(){$('#placementrespond').close();placementResponding=null}
$('#placementboardopen').onclick=()=>openPlacementBoard();
$('#placementboardclose').onclick=closePlacementBoard;
$('#placementboard').addEventListener('cancel',e=>{e.preventDefault();closePlacementBoard()});
$('#placementboardstatus').onchange=()=>loadPlacementBoard(true);
$('#placementboardmore').onclick=()=>loadPlacementBoard(false);
$('#placementboardnew').onclick=()=>openPlacementRequest(null);
$('#placementrequestfromview').onclick=()=>{if(placementEditing)openPlacementRequest(placementEditing)};
$('#placementrequestclose').onclick=closePlacementRequest;
$('#placementrequest').addEventListener('cancel',e=>{e.preventDefault();closePlacementRequest()});
$('#placementrequestform').onsubmit=async e=>{
  e.preventDefault();
  const body=$('#placementrequestbody').value.trim();
  if(!body){$('#placementrequesterror').textContent='依頼内容を入力してください。';return}
  const submit=$('#placementrequestform').querySelector('[type=submit]');
  if(submit.disabled)return;
  submit.disabled=true;$('#placementrequesterror').textContent='';
  try{
    const payload={body};
    if(placementRequestTarget?.id) payload.placementId=placementRequestTarget.id;
    await api('/api/placement-corrections',payload);
    closePlacementRequest();
    toast('修正依頼を投稿しました。');
    if($('#placementboard').open) loadPlacementBoard(true);
    else openPlacementBoard();
  }catch(err){$('#placementrequesterror').textContent=err.message}
  finally{submit.disabled=false}
};
$('#placementrespondclose').onclick=closePlacementRespond;
$('#placementrespond').addEventListener('cancel',e=>{e.preventDefault();closePlacementRespond()});
$('#placementrespondform').onsubmit=async e=>{
  e.preventDefault();
  if(!placementResponding||!canEditPlacement())return;
  const submit=$('#placementrespondform').querySelector('[type=submit]');
  if(submit.disabled)return;
  submit.disabled=true;$('#placementresponderror').textContent='';
  try{
    await api('/api/placement-corrections/status',{
      id:placementResponding.id,
      version:placementResponding.version,
      status:$('#placementrespondstatus').value,
      response:$('#placementrespondcomment').value,
    });
    closePlacementRespond();
    toast('対応状況を更新しました。');
    if($('#placementboard').open) loadPlacementBoard(true);
  }catch(err){$('#placementresponderror').textContent=err.message}
  finally{submit.disabled=false}
};
