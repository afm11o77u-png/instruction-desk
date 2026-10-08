const $=(s,r=document)=>r.querySelector(s)
const state={section:'list',setupTab:'customers',mode:'stock',tab:'list',q:'',stopped:false,productSupplierId:'',data:null,stock:null,openings:null,filters:{group:'product',customerId:'',productId:'',size:'',spec:'',locationId:''},token:'',opening:{},openingDirty:false,editDirty:false}
const SETUP_TABS=[['customers','顧客'],['suppliers','仕入先'],['products','商品'],['locations','保管場所'],['opening','初期在庫']]
const SETUP_TAB_IDS=new Set(SETUP_TABS.map(([id])=>id))
const SETUP_TAB_LABEL=Object.fromEntries(SETUP_TABS)
const SETUP_TAB_KEY='stock-desk:setup-tab'
const SIZE_SCHEMES={
  letter:{id:'letter',label:'アルファベット',sizes:['S','M','L','LL','2L','3L','4L','SS','フリー']},
  gou:{id:'gou',label:'号数',sizes:['5号','7号','9号','11号','13号','15号','17号','19号','21号']}
}
function isGouSize(label){return /^\d+号$/.test(String(label||''))}
function detectSizeScheme(sizes){
  const list=sizes||[]
  const gou=list.filter(isGouSize).length
  const letter=list.filter(s=>SIZE_SCHEMES.letter.sizes.includes(s)).length
  return gou>letter?'gou':'letter'
}
const PRODUCT_SUPPLIER_KEY='stock-desk:product-supplier-filter'
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function loadProductSupplierFilter(){
  try{return localStorage.getItem(PRODUCT_SUPPLIER_KEY)||''}catch(_){return ''}
}
function saveProductSupplierFilter(value){
  try{
    if(value)localStorage.setItem(PRODUCT_SUPPLIER_KEY,value)
    else localStorage.removeItem(PRODUCT_SUPPLIER_KEY)
  }catch(_){}
}
function productSuppliersWithItems(){
  const ids=new Set(state.data.products.map(p=>p.supplierId).filter(id=>id!=null))
  return state.data.suppliers.filter(s=>ids.has(s.id)).slice().sort((a,b)=>String(a.name).localeCompare(String(b.name),'ja'))
}
function resolveProductSupplierFilter(){
  const stored=state.productSupplierId||loadProductSupplierFilter()
  if(!stored)return ''
  const ok=productSuppliersWithItems().some(s=>String(s.id)===String(stored))
  const next=ok?String(stored):''
  state.productSupplierId=next
  if(!ok)saveProductSupplierFilter('')
  return next
}
function yen(n){return Number(n).toLocaleString('ja-JP')+'円'}
function num(n){return Number(n).toLocaleString('ja-JP')}
function toast(t,ms=3200){const el=$('#toast');el.hidden=false;el.textContent=t;clearTimeout(toast._);toast._=setTimeout(()=>{el.hidden=true},ms)}
const BASE=location.pathname.startsWith('/inventory')?'/inventory':''
const embedded=new URLSearchParams(location.search).get('embed')==='1'

const STOCK_SCREENS={customers:'stock-customers',suppliers:'stock-suppliers',products:'stock-products',locations:'stock-locations',opening:'stock-opening',list:'stock-list'}
async function deskDisplayFetch(path,data){
  const opts=data?{method:'POST',headers:{'Content-Type':'application/json','X-Desk-Request':'1'},body:JSON.stringify(data)}:{}
  const r=await fetch(path,opts)
  let result={}
  try{result=await r.json()}catch(_){result={}}
  if(!r.ok)throw Error(result.error||'表示設定を保存できませんでした。')
  return result
}
async function ensureStockDisplayModes(){
  if(typeof DisplayMode==='undefined')return
  await DisplayMode.loadAll(()=>deskDisplayFetch('/api/display-modes'))
}
function saveStockDisplay(screenId,mode){return deskDisplayFetch('/api/display-modes',{screenId,mode})}
function stockScreenId(tab){return STOCK_SCREENS[tab]||('stock-'+tab)}
function syncStockListDisplaySlot(section){
  const slot=$('#stocklistdisplay')
  if(!slot)return
  if(section==='list'){
    slot.hidden=false
    return
  }
  slot.hidden=true
  slot.innerHTML=''
}
function mountStockDisplay(stage,tab,repaint){
  if(!stage||typeof DisplayMode==='undefined')return 'standard'
  const screenId=stockScreenId(tab)
  // 在庫一覧: mount 表示 on the title-row right end (outside the filter frame).
  // Setup screens keep compact chrome inside `.bar`.
  const toolbar=tab==='list'
    ? document.querySelector('#stocklistdisplay')
    : document.querySelector('#main .bar')
  DisplayMode.mount(stage,screenId,async(mode)=>{
    await DisplayMode.set(screenId,mode,saveStockDisplay)
    if(typeof repaint==='function')repaint()
  },toolbar)
  return DisplayMode.get(screenId)
}
function listNote(summary,body){
  return `<details class="list-note"><summary>${summary}</summary><p class="list-note-body">${body}</p></details>`
}
function stockOpenBtn(id,label='開く'){return `<button type="button" class="quiet stockopen" data-id="${id}">${label}</button>`}
async function api(path,data){
  const r=await fetch(BASE+path,data?{method:'POST',headers:{'Content-Type':'application/json','X-Stock-Request':'1'},body:JSON.stringify(data)}:{})
  const result=await r.json()
  if(!r.ok)throw Error(result.error||'処理できませんでした。')
  return result
}
function specLabel(v){return v?v:'刺繍なし'}
function loadSetupTab(){
  try{
    const stored=localStorage.getItem(SETUP_TAB_KEY)
    if(stored&&SETUP_TAB_IDS.has(stored))return stored
  }catch(_){}
  return 'customers'
}
function saveSetupTab(tab){
  try{localStorage.setItem(SETUP_TAB_KEY,tab)}catch(_){}
}
function applySectionState(section,setupTab){
  state.section=section==='setup'?'setup':'list'
  if(state.section==='list'){
    state.tab='list'
    state.mode='stock'
  }else{
    const next=SETUP_TAB_IDS.has(setupTab)?setupTab:(SETUP_TAB_IDS.has(state.setupTab)?state.setupTab:loadSetupTab())
    state.setupTab=next
    state.tab=next
    state.mode=next==='customers'||next==='suppliers'||next==='products'?'masters':'stock'
    saveSetupTab(next)
  }
}
function notifyParentSection(){
  if(!embedded||!window.parent||window.parent===window)return
  try{window.parent.postMessage({type:'stock-section-changed',section:state.section},location.origin)}catch(_){}
}
async function setStockSection(section,{setupTab,silent}={}){
  const next=section==='setup'?'setup':'list'
  if(next===state.section&&!(setupTab&&SETUP_TAB_IDS.has(setupTab)&&setupTab!==state.tab)){
    if(!silent)notifyParentSection()
    return true
  }
  if(!(await ensureCanNavigate()))return false
  applySectionState(next,setupTab)
  if(state.tab==='list')await loadStock()
  if(state.tab==='opening')await loadOpeningEntries()
  paint()
  if(!silent)notifyParentSection()
  return true
}
async function setSetupTab(tab){
  if(!SETUP_TAB_IDS.has(tab))return false
  if(state.section==='setup'&&state.tab===tab)return true
  if(!(await ensureCanNavigate()))return false
  applySectionState('setup',tab)
  if(state.tab==='opening')await loadOpeningEntries()
  paint()
  notifyParentSection()
  return true
}
async function boot(){
  state.data=await api('/api/bootstrap')
  if(!state.ready){
    const params=new URLSearchParams(location.search)
    const sectionParam=params.get('section')
    applySectionState(sectionParam==='setup'?'setup':'list',loadSetupTab())
  }
  state.ready=true
  const labels={sample:['試用版・架空のサンプル','サンプルのデータは data\\sample に保存されています。'],desk:['在庫管理','指示書デスクと同じ画面から使っています。在庫のデータは指示書とは別のファイルです。'],live:['試用版・このパソコンの台帳','この台帳は data\\live に保存されています。サンプルとは別です。']}
  const label=labels[state.data.mode]||labels.live
  $('#eyebrow').textContent=label[0]
  $('#foot').textContent=label[1]
  if(state.tab==='list')await loadStock()
  if(state.tab==='opening')await loadOpeningEntries()
  await ensureStockDisplayModes().catch(()=>{})
  paint()
  notifyParentSection()
}
function paint(){
  if(state.section==='setup'){
    if(!SETUP_TAB_IDS.has(state.tab))state.tab=state.setupTab&&SETUP_TAB_IDS.has(state.setupTab)?state.setupTab:'customers'
    state.setupTab=state.tab
    saveSetupTab(state.tab)
    state.mode=state.tab==='customers'||state.tab==='suppliers'||state.tab==='products'?'masters':'stock'
  }else{
    state.tab='list'
    state.mode='stock'
  }
  document.querySelectorAll('.switch button[data-mode]').forEach(b=>{
    const want=b.dataset.mode==='masters'?'setup':'list'
    b.classList.toggle('active',want===state.section)
  })
  const title=$('#stocktitle')
  if(title)title.textContent=state.section==='setup'?(SETUP_TAB_LABEL[state.tab]||'顧客'):'在庫一覧'
  const tabs=$('#tabs')
  if(state.section==='setup'){
    tabs.hidden=false
    tabs.innerHTML=SETUP_TABS.map(([id,label])=>`<button type="button" data-tab="${id}" class="${id===state.tab?'active':''}">${label}</button>`).join('')
    tabs.querySelectorAll('button').forEach(b=>b.onclick=async()=>{
      if(b.dataset.tab===state.tab)return
      await setSetupTab(b.dataset.tab)
    })
  }else{
    tabs.hidden=true
    tabs.innerHTML=''
  }
  syncStockListDisplaySlot(state.section)
  const view={customers:customers,suppliers:suppliers,products:products,locations:locations,opening:opening,list:stockList}[state.tab]
  view()
}
async function loadOpeningEntries(){
  state.openings=await api('/api/opening/entries')
}
function openingDialogEl(){return $('#openingDialog')}
function openingDialogIsOpen(){const dlg=openingDialogEl();return !!(dlg&&dlg.open)}
function touchOpeningDirty(){state.openingDirty=true}
function closeOpeningDialog(){
  const dlg=openingDialogEl()
  if(!dlg)return
  dlg.close()
  dlg.innerHTML=''
  state.openingDirty=false
}
async function ensureLeaveOpeningForm(){
  if(!openingDialogIsOpen())return true
  if(state.openingDirty&&!window.confirm('入力中の内容を破棄しますか？'))return false
  closeOpeningDialog()
  return true
}
function editDialogEl(){return $('#dialog')}
function editDialogIsOpen(){const dlg=editDialogEl();return !!(dlg&&dlg.open)}
async function ensureLeaveEditDialog(){
  if(!editDialogIsOpen())return true
  if(state.editDirty&&!window.confirm('入力中の内容を破棄しますか？'))return false
  const dlg=editDialogEl()
  dlg.close()
  dlg.innerHTML=''
  state.editDirty=false
  return true
}
async function ensureCanNavigate(){
  if(!(await ensureLeaveOpeningForm()))return false
  if(!(await ensureLeaveEditDialog()))return false
  return true
}
function excelTools(){
  return `<span class="exceltools"><button type="button" id="exceldown">記入用Excel</button><button type="button" id="excelin">Excelを読み込む</button><input id="excelfile" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden></span>`
}
function inactiveToggle(){
  return `<label class="check inactive-toggle"><input id="stopped" type="checkbox" ${state.stopped?'checked':''}>停止も表示</label>`
}
function bar(placeholder,extra='',addLabel='新しく登録'){
  return `<div class="bar">${extra}<label>検索<input id="q" value="${esc(state.q)}" placeholder="${placeholder}"></label>
    ${inactiveToggle()}
    <button type="button" class="primary" id="add">${esc(addLabel)}</button>${excelTools()}</div>
    ${listNote('Excel取込の説明','記入用Excelの各シートに、顧客・仕入先・商品・取扱・保管場所・初期在庫を書けます。同じファイルをもう一度読むと台帳は空欄以外を更新し、同じ初期在庫は増やしません。数量を変えた初期在庫は、別の初期在庫として追加されます。')}`
}
function productSupplierControl(current){
  const options=['<option value="">すべて</option>'].concat(
    productSuppliersWithItems().map(s=>`<option value="${s.id}" ${String(current)===String(s.id)?'selected':''}>${esc(s.name)}</option>`)
  ).join('')
  return `<label class="supplier-filter">仕入先<select id="productSupplier" aria-label="仕入先で絞り込み">${options}</select></label>`
}
function bindExcel(){
  const down=$('#exceldown'),pick=$('#excelin'),file=$('#excelfile')
  if(!down||!pick||!file)return
  down.onclick=async()=>{
    const r=await fetch(BASE+'/api/import/template')
    if(!r.ok){let message='記入用Excelをダウンロードできませんでした。';try{message=(await r.json()).error||message}catch(e){}toast(message);return}
    const blob=await r.blob()
    const a=document.createElement('a')
    a.href=URL.createObjectURL(blob)
    a.download='在庫登録.xlsx'
    a.click()
    URL.revokeObjectURL(a.href)
  }
  pick.onclick=()=>file.click()
  file.onchange=async()=>{
    const chosen=file.files&&file.files[0]
    file.value=''
    if(!chosen)return
    try{
      const bytes=new Uint8Array(await chosen.arrayBuffer())
      let text=''
      for(let i=0;i<bytes.length;i+=0x8000)text+=String.fromCharCode(...bytes.subarray(i,i+0x8000))
      const result=await api('/api/import',{name:chosen.name,file:btoa(text)})
      toast(result.summary,8000)
      await boot()
    }catch(e){toast(e.message)}
  }
}
function customers(){
  const rows=state.data.customers.filter(r=>(state.stopped||r.active)&&`${r.name}${r.kana}${r.phone}${r.contact}${r.address}`.includes(state.q))
  const blank='<span class="blank">—</span>'
  $('#main').innerHTML=`<section class="panel">${bar('顧客名・カナ・電話番号・担当者')}<div class="list-stage" id="stockliststage"><div class="directory-scroll"><table class="directory"><thead><tr><th>顧客</th><th>電話番号</th><th>担当者</th><th>住所</th><th></th></tr></thead><tbody>
    ${rows.map(r=>`<tr class="${r.active?'':'is-stopped'}"><td class="who"><span class="sid">${r.id}</span><span class="nameline"><b class="dm-primary">${esc(r.name)}</b>${r.active?'':'<span class="badge stop">停止</span>'}${r.kana?`<span class="sub dm-secondary">${esc(r.kana)}</span>`:''}</span></td><td class="tel">${r.phone?esc(r.phone):blank}</td><td>${r.contact?`<span class="pill">${esc(r.contact)}</span>`:blank}</td><td class="addr">${r.address?esc(r.address):blank}</td><td class="act">${stockOpenBtn(r.id)}</td></tr>`).join('')||'<tr><td colspan="5">該当する顧客はありません。</td></tr>'}
  </tbody></table></div><div class="list-grid" id="stocklistgrid" hidden></div></div></section>`
  const grid=$('#stocklistgrid')
  if(grid)grid.innerHTML=rows.length?rows.map(r=>`<article class="dm-card ${r.active?'':'is-stopped'}"><div class="dm-primary">${esc(r.name)}${r.active?'':' <span class="badge stop">停止</span>'}</div>${r.kana?`<div class="dm-secondary">${esc(r.kana)}</div>`:''}<div class="dm-meta"><span>${r.phone?esc(r.phone):'—'}</span><span>${r.contact?esc(r.contact):'—'}</span></div><div class="dm-secondary">${r.address?esc(r.address):'—'}</div><div class="dm-actions">${stockOpenBtn(r.id)}</div></article>`).join(''):'<div class="dm-empty">該当する顧客はありません。</div>'
  bindList(rows,'顧客',customerForm,body=>api('/api/customers',body))
}
function suppliers(){
  const rows=state.data.suppliers.filter(r=>(state.stopped||r.active)&&`${r.name}${r.phone}${r.contact}${r.address}`.includes(state.q))
  const blank='<span class="blank">—</span>'
  $('#main').innerHTML=`<section class="panel supplier-panel">${listNote('仕入先について','仕入先はメーカーとは別です。商品に入れるのは、ふだん頼む仕入先です。')}${bar('仕入先名・電話番号・担当者')}<div class="list-stage" id="stockliststage"><div class="directory-scroll"><table class="directory"><thead><tr><th>仕入先</th><th>電話番号</th><th>担当者</th><th>住所</th><th></th></tr></thead><tbody>
    ${rows.map(r=>`<tr class="${r.active?'':'is-stopped'}"><td class="who"><span class="sid">${r.id}</span><span class="nameline"><b class="dm-primary">${esc(r.name)}</b>${r.active?'':'<span class="badge stop">停止</span>'}</span></td><td class="tel">${r.phone?esc(r.phone):blank}</td><td>${r.contact?`<span class="pill">${esc(r.contact)}</span>`:blank}</td><td class="addr">${r.address?esc(r.address):blank}</td><td class="act">${stockOpenBtn(r.id)}</td></tr>`).join('')||'<tr><td colspan="5">該当する仕入先はありません。</td></tr>'}
  </tbody></table></div><div class="list-grid" id="stocklistgrid" hidden></div></div></section>`
  const grid=$('#stocklistgrid')
  if(grid)grid.innerHTML=rows.length?rows.map(r=>`<article class="dm-card"><div class="dm-primary">${esc(r.name)}${r.active?'':' <span class="badge stop">停止</span>'}</div><div class="dm-meta"><span>${r.phone?esc(r.phone):'—'}</span><span>${r.contact?esc(r.contact):'—'}</span></div><div class="dm-secondary">${r.address?esc(r.address):'—'}</div><div class="dm-actions">${stockOpenBtn(r.id)}</div></article>`).join(''):'<div class="dm-empty">該当する仕入先はありません。</div>'
  bindList(rows,'仕入先',supplierForm,body=>api('/api/suppliers',body))
}
function products(){
  const supplierId=resolveProductSupplierFilter()
  const rows=state.data.products.filter(r=>{
    if(supplierId&&String(r.supplierId)!==String(supplierId))return false
    const text=`${r.code}${r.name}${r.color}${r.supplierName}${r.links.map(l=>l.customerName+l.embroidery).join('')}`
    return (state.stopped||r.active)&&text.includes(state.q)
  })
  $('#main').innerHTML=`<section class="panel">${listNote('商品一覧の注意','品番が同じでも、中の商品IDが違えば別の商品です。顧客ごとの刺繍は、ここで一度登録します。仕入先を選ぶと、その仕入先の商品だけを一覧できます。')}${bar('品番・商品名・顧客',productSupplierControl(supplierId))}<div class="list-stage" id="stockliststage"><div class="tablewrap"><table><thead><tr><th>商品ID</th><th>商品</th><th>サイズ</th><th>取扱</th><th></th></tr></thead><tbody>
    ${rows.map(r=>`<tr><td>${r.id}</td><td><span class="dm-primary">${esc(r.code)} ${esc(r.name)} ${esc(r.color)}</span> ${r.active?'':'<span class="badge stop">停止</span>'}<span class="sub dm-secondary">通常の仕入先: ${esc(r.supplierName)||'未設定'}</span></td><td>${r.sizes.map(esc).join('、')}</td><td>${r.links.map(l=>`${esc(l.customerName)} / ${esc(specLabel(l.embroidery))}${l.isStandard?'（標準）':''}${l.active?'':' 停止'}`).join('<br>')||'未設定'}</td><td>${stockOpenBtn(r.id)}</td></tr>`).join('')||'<tr><td colspan="5">該当する商品はありません。</td></tr>'}
  </tbody></table></div><div class="list-grid" id="stocklistgrid" hidden></div></div></section>`
  const grid=$('#stocklistgrid')
  if(grid)grid.innerHTML=rows.length?rows.map(r=>`<article class="dm-card"><div class="dm-primary">${esc(r.code)} ${esc(r.name)} ${esc(r.color)}${r.active?'':' <span class="badge stop">停止</span>'}</div><div class="dm-secondary">商品ID ${r.id}　仕入先 ${esc(r.supplierName)||'未設定'}</div><div class="dm-meta"><span>${r.sizes.map(esc).join('、')||'サイズ未設定'}</span></div><div class="dm-secondary">${r.links.map(l=>`${esc(l.customerName)} / ${esc(specLabel(l.embroidery))}`).join(' · ')||'取扱未設定'}</div><div class="dm-actions">${stockOpenBtn(r.id)}</div></article>`).join(''):'<div class="dm-empty">該当する商品はありません。</div>'
  bindList(rows,'商品',productForm,body=>api('/api/products',body))
  const supplierSelect=$('#productSupplier')
  if(supplierSelect)supplierSelect.onchange=()=>{
    state.productSupplierId=supplierSelect.value
    saveProductSupplierFilter(state.productSupplierId)
    paint()
  }
}
const SHELF_PREVIEW_LIMIT=3
function siteLocationRows(){
  const locations=state.data.locations||[]
  const sites=locations.filter(r=>!r.parentId)
  const byParent={}
  locations.forEach(r=>{
    if(!r.parentId)return
    ;(byParent[r.parentId]||(byParent[r.parentId]=[])).push(r)
  })
  const q=(state.q||'').trim()
  return sites.filter(site=>{
    const shelves=(byParent[site.id]||[]).filter(s=>state.stopped||s.active)
    if(!state.stopped&&!site.active)return false
    if(!q)return true
    if((site.name||'').includes(q)||(site.label||'').includes(q))return true
    return shelves.some(s=>(s.name||'').includes(q)||(s.label||'').includes(q))
  }).map(site=>{
    const shelves=(byParent[site.id]||[]).filter(s=>state.stopped||s.active)
    const names=shelves.map(s=>s.active?s.name:`${s.name}（停止）`)
    let shelfSummary='棚なし'
    let shelfExtraCount=0
    if(names.length){
      if(names.length<=SHELF_PREVIEW_LIMIT)shelfSummary=names.join('、')
      else{
        shelfExtraCount=names.length-SHELF_PREVIEW_LIMIT
        shelfSummary=names.slice(0,SHELF_PREVIEW_LIMIT).join('、')+`、ほか${shelfExtraCount}件`
      }
    }
    return {...site,shelves,shelfSummary,shelfExtraCount}
  })
}
function locations(){
  const rows=siteLocationRows()
  $('#main').innerHTML=`<section class="panel">${listNote('保管場所の注意','「拠点を新しく登録」では、空の拠点名から新しい拠点だけを作ります。既存の拠点の棚を増やす・直すときは、一覧の「開く」から行います。空の棚名は保存時に無視します（棚なしでも登録できます）。')}${bar('拠点名・棚・区画名','','拠点を新しく登録')}<div class="list-stage" id="stockliststage"><div class="tablewrap"><table class="site-locations"><thead><tr><th>拠点名</th><th>棚・区画</th><th>操作</th></tr></thead><tbody>
    ${rows.map(r=>`<tr class="${r.active?'':'is-stopped'}"><td><span class="dm-primary">${esc(r.name)}</span> ${r.active?'':'<span class="badge stop">停止</span>'}</td><td class="shelf-summary">${esc(r.shelfSummary)}</td><td>${stockOpenBtn(r.id)}</td></tr>`).join('')||'<tr><td colspan="3">保管場所はまだありません。</td></tr>'}
  </tbody></table></div><div class="list-grid" id="stocklistgrid" hidden></div></div></section>`
  const grid=$('#stocklistgrid')
  if(grid)grid.innerHTML=rows.length?rows.map(r=>`<article class="dm-card ${r.active?'':'is-stopped'}"><div class="dm-primary">${esc(r.name)}${r.active?'':' <span class="badge stop">停止</span>'}</div><div class="dm-secondary">${esc(r.shelfSummary)}</div><div class="dm-actions">${stockOpenBtn(r.id)}</div></article>`).join(''):'<div class="dm-empty">保管場所はまだありません。</div>'
  bindList(rows,'保管場所',locationForm,body=>api('/api/locations',body))
}
function bindList(rows,title,form,save){
  const stage=$('#stockliststage')
  const mode=mountStockDisplay(stage,state.tab,()=>paint())
  const grid=$('#stocklistgrid')
  if(grid)grid.hidden=mode!=='grid'
  if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,stockScreenId(state.tab))
  $('#q').oninput=e=>{const pos=e.target.selectionStart;state.q=e.target.value;paint();const q=$('#q');q.focus();q.setSelectionRange(pos,pos)}
  $('#stopped').onchange=e=>{state.stopped=e.target.checked;paint()}
  $('#add').onclick=()=>edit(null,title,form,save)
  document.querySelectorAll('#main .stockopen,[data-id]').forEach(b=>{
    if(!b.dataset.id)return
    b.onclick=()=>edit(rows.find(r=>r.id===Number(b.dataset.id)),title,form,save)
  })
  bindExcel()
}
function edit(row,title,form,save){
  const dlg=$('#dialog')
  const heading=row?'修正：'+title:(title==='保管場所'?'拠点を新しく登録':'新しく登録：'+title)
  state.editDirty=false
  dlg.innerHTML=`<form><div class="dialoghead"><h2>${heading}</h2><button type="button" id="close" class="quiet">閉じる</button></div><div class="dialogbody">${form(row)}</div><p id="error"></p><div class="dialogactions"><button type="button" id="stop" class="quiet" ${row?'':'hidden'}>${row&&row.active===false?'使用を再開':'使用停止'}</button><button type="button" class="primary" id="save">保存</button></div></form>`
  dlg.showModal()
  const formEl=dlg.querySelector('form')
  formEl.onsubmit=ev=>ev.preventDefault()
  formEl.addEventListener('input',()=>{state.editDirty=true})
  formEl.addEventListener('change',()=>{state.editDirty=true})
  const tryCloseEdit=async()=>{
    if(!(await ensureLeaveEditDialog()))return
  }
  $('#close').onclick=()=>tryCloseEdit()
  dlg.oncancel=ev=>{
    if(state.editDirty&&!window.confirm('入力中の内容を破棄しますか？')){ev.preventDefault();return}
    dlg.innerHTML=''
    state.editDirty=false
  }
  if(row&&title==='商品')bindProductEditor(row)
  if(!row&&title==='商品')bindProductEditor({sizes:[],links:[],active:true})
  if(title==='保管場所')bindLocationEditor(row)
  $('#save').onclick=async ev=>{
    ev.preventDefault()
    try{
      await save(readForm(row))
      state.editDirty=false
      dlg.close();dlg.innerHTML='';toast('保存しました。');await boot()
    }catch(e){$('#error').textContent=e.message}
  }
  const stop=$('#stop')
  if(stop)stop.onclick=async()=>{
    if(!confirm(row.active?'使用停止にします。過去の在庫は残ります。':'使用を再開します。'))return
    try{
      let payload
      if(title==='保管場所'){
        payload=row.parentId
          ?{id:row.id,shelfName:row.name,active:!row.active}
          :{id:row.id,siteName:($('#siteName')&&$('#siteName').value)||row.name,active:!row.active}
      }else{
        payload={...readForm(row),active:!row.active}
      }
      await save(payload)
      state.editDirty=false
      dlg.close();dlg.innerHTML='';toast(row.active?'使用停止にしました。':'使用を再開しました。');await boot()
    }catch(e){$('#error').textContent=e.message}
  }
}
function field(id,label,value,required){return `<label>${label}${required?'（必須）':''}<input id="${id}" value="${esc(value||'')}"></label>`}
function customerForm(r){r=r||{};return `<div class="grid">${field('name','顧客名',r.name,true)}${field('kana','フリガナ',r.kana)}${field('phone','電話番号',r.phone)}${field('contact','担当者',r.contact)}</div><label>住所<textarea id="address">${esc(r.address||'')}</textarea></label><p class="sub">顧客IDは自動です。名前を変えても、同じ顧客のまま在庫がつながります。</p>`}
function supplierForm(r){r=r||{};return `<div class="grid">${field('name','仕入先名',r.name,true)}${field('phone','電話番号',r.phone)}${field('contact','担当者',r.contact)}</div><label>住所<textarea id="address">${esc(r.address||'')}</textarea></label>`}
function locationSiteContext(r){
  if(!r||!r.id)return {site:null,shelves:[]}
  if(r.parentId){
    const site=state.data.locations.find(x=>x.id===r.parentId)||null
    const shelves=state.data.locations.filter(x=>x.parentId===r.parentId).map(x=>({id:x.id,name:x.name,hasStock:!!x.hasStock,active:x.active!==false}))
    return {site,shelves}
  }
  const shelves=state.data.locations.filter(x=>x.parentId===r.id).map(x=>({id:x.id,name:x.name,hasStock:!!x.hasStock,active:x.active!==false}))
  return {site:r,shelves}
}
function locationForm(r){
  r=r||{}
  const ctx=locationSiteContext(r)
  if(r.id){
    const siteName=ctx.site?ctx.site.name:r.name
    const nameField=r.parentId
      ?`<label>拠点名<input id="siteNameDisplay" value="${esc(siteName)}" readonly></label>`
      :field('siteName','拠点名',siteName,true)
    return `${nameField}
      <h2>棚・区画</h2>
      <div class="links" id="shelfRows"></div>
      <button type="button" id="addShelf">棚を追加</button>
      <p class="sub">名前の変更は、場所の呼び方だけを直します。在庫を別の場所へ移す操作ではありません。在庫がある棚は削除できません。空の行は保存時に無視します。</p>`
  }
  return `${field('siteName','拠点名','',true)}
    <h2>棚・区画</h2>
    <div class="links" id="shelfRows"></div>
    <button type="button" id="addShelf">棚を追加</button>
    <p class="sub">新しい拠点と、必要なら複数の棚を一度の保存で登録できます。棚を追加しなくても拠点だけ登録できます。すでに同じ拠点名がある場合は登録できません（既存の拠点は一覧の「開く」から直してください）。</p>`
}
function touchEditDirty(){state.editDirty=true}
function bindLocationEditor(row){
  const ctx=locationSiteContext(row)
  const editor={shelves:ctx.shelves.map(s=>({...s})),removed:[]}
  const draw=()=>{
    const box=$('#shelfRows')
    if(!box)return
    box.innerHTML=editor.shelves.map((s,i)=>`<div class="linkrow shelfline">
      <label>棚・区画名<input data-i="${i}" data-k="name" value="${esc(s.name)}" placeholder="例：棚1、1F、償却待ち" autocomplete="off"></label>
      ${s.id?`<span class="sub">場所ID ${s.id}</span>`:'<span class="sub">新規</span>'}
      ${s.hasStock
        ?`<span class="sub shelf-lock">在庫があるため削除できません</span>`
        :`<button type="button" data-remove="${i}">外す</button>`}
    </div>`).join('')||'<p class="sub">棚・区画はまだありません。「棚を追加」で行を増やせます。</p>'
    box.querySelectorAll('[data-k=name]').forEach(el=>{
      el.oninput=()=>{editor.shelves[Number(el.dataset.i)].name=el.value;touchEditDirty()}
    })
    box.querySelectorAll('[data-remove]').forEach(b=>{
      b.onclick=()=>{
        const i=Number(b.dataset.remove)
        const item=editor.shelves[i]
        if(item&&item.hasStock){$('#error').textContent='在庫がある棚・区画は削除できません。';return}
        if(item&&item.id)editor.removed.push(item.id)
        editor.shelves.splice(i,1)
        touchEditDirty()
        draw()
      }
    })
  }
  const add=$('#addShelf')
  if(add)add.onclick=()=>{editor.shelves.push({id:null,name:'',hasStock:false,active:true});touchEditDirty();draw()}
  draw()
  $('#dialog')._locationEditor=editor
}
function collectLocationShelves(){
  const editor=$('#dialog')._locationEditor
  if(!editor)return []
  const shelves=editor.shelves.map(s=>{
    const item={name:(s.name||'').trim()}
    if(s.id)item.id=s.id
    return item
  })
  ;(editor.removed||[]).forEach(id=>shelves.push({id,name:'',remove:true}))
  return shelves
}
function productForm(r){
  r=r||{sizes:[],links:[],active:true}
  const suppliers=['<option value="">未設定</option>'].concat(state.data.suppliers.filter(s=>s.active||s.id===r.supplierId).map(s=>`<option value="${s.id}" ${s.id===r.supplierId?'selected':''}>${esc(s.name)}</option>`)).join('')
  const scheme=detectSizeScheme(r.sizes)
  return `<div class="grid">${field('code','品番',r.code,true)}${field('name','商品名',r.name,true)}${field('color','色',r.color)}<label>通常の仕入先<select id="supplierId">${suppliers}</select></label></div>
    <div class="size-scheme" role="group" aria-label="サイズ表記の切替">
      <span class="size-scheme-label">サイズ表記</span>
      <div class="seg" id="sizeScheme">
        <button type="button" data-scheme="letter" class="${scheme==='letter'?'on':''}">アルファベット</button>
        <button type="button" data-scheme="gou" class="${scheme==='gou'?'on':''}">号数</button>
      </div>
    </div>
    <p id="sizeHint">サイズをまとめて選びます。LLと2Lは別のサイズです。表記を切り替えても、すでに選んだサイズはそのまま残ります。</p>
    <div class="chips" id="chips"></div>
    <div class="bar"><label>特殊サイズ<input id="customSize" placeholder="例：150"></label><button type="button" id="addSize">サイズを追加</button></div>
    <h2>この商品を扱う顧客</h2><div class="links" id="links"></div><button type="button" id="addLink">顧客の取扱を追加</button>`
}
function bindProductEditor(row){
  const editor={sizes:[...row.sizes],links:row.links.map(l=>({...l})),scheme:detectSizeScheme(row.sizes)}
  const draw=()=>{
    const scheme=SIZE_SCHEMES[editor.scheme]||SIZE_SCHEMES.letter
    $('#sizeScheme').querySelectorAll('button').forEach(b=>{
      b.classList.toggle('on',b.dataset.scheme===editor.scheme)
      b.onclick=()=>{if(editor.scheme===b.dataset.scheme)return;editor.scheme=b.dataset.scheme;touchEditDirty();draw()}
    })
    const hint=$('#sizeHint')
    if(hint)hint.textContent=editor.scheme==='gou'
      ?'号数サイズをまとめて選びます。表記を切り替えても、すでに選んだアルファベットサイズはそのまま残ります。'
      :'アルファベットサイズをまとめて選びます。LLと2Lは別のサイズです。表記を切り替えても、すでに選んだ号数サイズはそのまま残ります。'
    const custom=$('#customSize')
    if(custom)custom.placeholder=editor.scheme==='gou'?'例：10号':'例：150'
    const preset=scheme.sizes
    const kept=editor.sizes.filter(s=>!preset.includes(s))
    const shown=[...preset,...kept]
    $('#chips').innerHTML=shown.map(s=>`<button type="button" class="${editor.sizes.includes(s)?'on':''}" data-size="${esc(s)}">${esc(s)}</button>`).join('')
    $('#chips').querySelectorAll('button').forEach(b=>b.onclick=()=>{
      const s=b.dataset.size
      editor.sizes=editor.sizes.includes(s)?editor.sizes.filter(x=>x!==s):editor.sizes.concat(s)
      touchEditDirty()
      draw()
    })
    const customers=state.data.customers.filter(c=>c.active)
    $('#links').innerHTML=editor.links.map((l,i)=>`<div class="linkrow">
      <label>顧客<select data-i="${i}" data-k="customerId">${customers.map(c=>`<option value="${c.id}" ${c.id===l.customerId?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label>
      <label>刺繍・加工<input data-i="${i}" data-k="embroidery" value="${esc(l.embroidery)}" placeholder="空欄は刺繍なし"></label>
      <label class="check"><input data-i="${i}" data-k="isStandard" type="checkbox" ${l.isStandard?'checked':''}>標準仕様</label>
      <label class="check"><input data-i="${i}" data-k="active" type="checkbox" ${l.active!==false?'checked':''}>使用中</label>
      <button type="button" data-remove="${i}">外す</button></div>`).join('')||'<p>まだ顧客に結び付いていません。</p>'
    $('#links').querySelectorAll('[data-k]').forEach(el=>el.onchange=()=>{
      const link=editor.links[Number(el.dataset.i)]
      touchEditDirty()
      if(el.dataset.k==='customerId')link.customerId=Number(el.value)
      if(el.dataset.k==='embroidery')link.embroidery=el.value.trim()
      if(el.dataset.k==='isStandard'){link.isStandard=el.checked;if(el.checked)editor.links.forEach(x=>{if(x!==link&&x.customerId===link.customerId)x.isStandard=false});draw()}
      if(el.dataset.k==='active')link.active=el.checked
    })
    $('#links').querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{editor.links.splice(Number(b.dataset.remove),1);touchEditDirty();draw()})
  }
  $('#addSize').onclick=()=>{
    const s=$('#customSize').value.trim()
    if(!s)return
    if(!editor.sizes.includes(s))editor.sizes.push(s)
    $('#customSize').value=''
    touchEditDirty()
    draw()
  }
  $('#addLink').onclick=()=>{
    const first=state.data.customers.find(c=>c.active)
    if(!first){$('#error').textContent='先に顧客を登録してください。';return}
    editor.links.push({customerId:first.id,embroidery:'',isStandard:editor.links.filter(l=>l.customerId===first.id).length===0,active:true})
    touchEditDirty()
    draw()
  }
  draw()
  $('#dialog')._editor=editor
}
function readForm(row){
  const title=$('#dialog h2').textContent
  const active=row?row.active!==false:true
  if(title.includes('顧客'))return {id:row&&row.id,name:$('#name').value,kana:$('#kana').value,phone:$('#phone').value,contact:$('#contact').value,address:$('#address').value,active}
  if(title.includes('仕入先'))return {id:row&&row.id,name:$('#name').value,phone:$('#phone').value,contact:$('#contact').value,address:$('#address').value,active}
  if(title.includes('保管場所')||title.includes('拠点を新しく登録')){
    const shelves=collectLocationShelves()
    const active=row?row.active!==false:true
    if(row&&row.id){
      const siteId=row.parentId||row.id
      const siteNameEl=$('#siteName')
      const display=$('#siteNameDisplay')
      const siteName=siteNameEl?siteNameEl.value:(display?display.value:'')
      return {id:siteId,siteName,shelves,active:row.parentId?(state.data.locations.find(x=>x.id===siteId)||{active:true}).active!==false:active}
    }
    // 新規は常に create-only（既存IDを付けない）。同名はサーバーが拒否する。
    return {siteName:($('#siteName')&&$('#siteName').value)||'',shelves,active}
  }
  const editor=$('#dialog')._editor
  const supplier=$('#supplierId').value
  return {id:row&&row.id,code:$('#code').value,name:$('#name').value,color:$('#color').value,supplierId:supplier?Number(supplier):null,sizes:editor.sizes,links:editor.links.map(l=>({customerId:Number(l.customerId),embroidery:(l.embroidery||'').trim(),isStandard:!!l.isStandard,active:l.active!==false})),active}
}
function searchPickerMarkup({label,inputId,listId,clearId,hintId,placeholder,value}){
  return `<label class="search-picker">${esc(label)}（必須）
      <div class="search-picker-control">
        <input id="${inputId}" type="search" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="false" aria-autocomplete="list" aria-controls="${listId}" placeholder="${esc(placeholder)}" value="${esc(value||'')}">
        <button type="button" class="quiet search-picker-clear" id="${clearId}" ${value?'':'hidden'}>クリア</button>
      </div>
      <ul id="${listId}" class="search-picker-list" role="listbox" hidden></ul>
      <p class="search-picker-hint" id="${hintId}"></p>
    </label>`
}
function bindOpeningSearchPicker({input,list,clearBtn,hint,getItems,emptyText,isConfirmed,confirmedLabel,onPick,onClear,onQueryChange}){
  let active=-1
  let open=false
  const setHint=(text,confirmed)=>{
    if(!hint)return
    hint.textContent=text||''
    hint.classList.toggle('is-confirmed',!!confirmed)
  }
  const close=()=>{
    open=false
    active=-1
    list.hidden=true
    list.innerHTML=''
    input.setAttribute('aria-expanded','false')
  }
  const paintList=()=>{
    const items=getItems()
    list.innerHTML=''
    if(!items.length){
      list.innerHTML=`<li class="search-picker-empty">${esc(emptyText)}</li>`
    }else{
      items.forEach((item,i)=>{
        const li=document.createElement('li')
        li.setAttribute('role','presentation')
        const btn=document.createElement('button')
        btn.type='button'
        btn.setAttribute('role','option')
        btn.dataset.index=String(i)
        btn.id=`${list.id}-opt-${i}`
        if(i===active)btn.classList.add('is-active')
        btn.innerHTML=item.html||esc(item.label)
        btn.onmousedown=e=>e.preventDefault()
        btn.onclick=()=>{onPick(item);close();syncChrome()}
        li.appendChild(btn)
        list.appendChild(li)
      })
    }
    list.hidden=false
    open=true
    input.setAttribute('aria-expanded','true')
    const current=active>=0&&items[active]?`${list.id}-opt-${active}`:null
    if(current)input.setAttribute('aria-activedescendant',current)
    else input.removeAttribute('aria-activedescendant')
  }
  const syncChrome=()=>{
    const confirmed=isConfirmed()
    if(clearBtn)clearBtn.hidden=!confirmed&&!String(input.value||'').trim()
    if(confirmed)setHint('選択済み: '+confirmedLabel(),true)
    else if(String(input.value||'').trim())setHint('候補から選んで確定してください（入力だけでは保存できません）',false)
    else setHint('',false)
  }
  const move=(delta)=>{
    const items=getItems()
    if(!items.length){paintList();return}
    if(!open)paintList()
    active=(active+delta+items.length)%items.length
    paintList()
    const el=list.querySelector('.is-active')
    if(el&&el.scrollIntoView)el.scrollIntoView({block:'nearest'})
  }
  input.onfocus=()=>{paintList();syncChrome()}
  input.oninput=()=>{
    if(onQueryChange(input.value))return
    active=-1
    paintList()
    syncChrome()
  }
  input.onkeydown=e=>{
    if(e.key==='ArrowDown'){e.preventDefault();move(1);return}
    if(e.key==='ArrowUp'){e.preventDefault();move(-1);return}
    if(e.key==='Enter'){
      const items=getItems()
      if(open&&active>=0&&items[active]){e.preventDefault();onPick(items[active]);close();syncChrome();return}
      if(open&&items.length===1){e.preventDefault();onPick(items[0]);close();syncChrome();return}
      return
    }
    if(e.key==='Escape'){if(open){e.preventDefault();close()}}
  }
  input.onblur=()=>setTimeout(()=>{if(!list.contains(document.activeElement))close()},120)
  if(clearBtn)clearBtn.onclick=()=>{onClear();input.value='';close();syncChrome();input.focus()}
  syncChrome()
  return {syncChrome,close}
}
function openingLocationSelection(){
  const locations=state.data.locations||[]
  const sites=locations.filter(l=>!l.parentId&&l.active)
  let siteId=state.opening.siteId!=null&&state.opening.siteId!==''?Number(state.opening.siteId):null
  let shelfId=state.opening.shelfId!=null&&state.opening.shelfId!==''?String(state.opening.shelfId):''
  const saved=state.opening.locationId!=null&&state.opening.locationId!==''?Number(state.opening.locationId):null
  if(saved!=null&&(siteId==null||Number.isNaN(siteId))){
    const loc=locations.find(l=>l.id===saved)
    if(loc){
      if(loc.parentId){siteId=loc.parentId;shelfId=String(loc.id)}
      else{siteId=loc.id;shelfId=String(loc.id)}
    }
  }
  if(siteId==null||Number.isNaN(siteId)||!sites.some(s=>s.id===siteId)){
    siteId=sites[0]?sites[0].id:null
    shelfId=''
  }
  const shelves=siteId==null?[]:locations.filter(l=>l.parentId===siteId&&l.active)
  const requireShelf=shelves.length>0
  const options=[]
  if(requireShelf&&saved===siteId){
    options.push({id:siteId,name:'棚指定なし',siteOnly:true})
  }
  shelves.forEach(s=>options.push({id:s.id,name:s.name,siteOnly:false}))
  if(requireShelf){
    const ok=options.some(o=>String(o.id)===String(shelfId))
    if(!ok)shelfId=''
  }else{
    shelfId=siteId==null?'':String(siteId)
  }
  let locationId=null
  if(siteId!=null){
    if(!requireShelf)locationId=siteId
    else if(shelfId!=='')locationId=Number(shelfId)
  }
  return {sites,siteId,shelves:options,requireShelf,shelfId,locationId}
}
function openingLocationFields(sel){
  const siteOpts=sel.sites.map(s=>`<option value="${s.id}" ${s.id===sel.siteId?'selected':''}>${esc(s.name)}</option>`).join('')
  const shelfOpts=sel.requireShelf
    ?`<option value="">選んでください</option>`+sel.shelves.map(s=>`<option value="${s.id}" ${String(s.id)===String(sel.shelfId)?'selected':''}>${esc(s.name)}</option>`).join('')
    :''
  return `<label>拠点（必須）<select id="openingSite">${siteOpts||'<option value="">拠点がありません</option>'}</select></label>
      <label id="openingShelfLabel" ${sel.requireShelf?'':'hidden'}>棚・区画（必須）<select id="openingShelf">${shelfOpts}</select></label>
      <input type="hidden" id="locationId" value="${sel.locationId==null?'':sel.locationId}">`
}
function opening(){
  const entries=(state.openings&&state.openings.entries)||[]
  $('#main').innerHTML=`<section class="panel">
    <div class="bar">
      <button type="button" class="primary" id="openOpeningRegister">初期在庫を登録</button>
      ${excelTools()}
    </div>
    ${listNote('初期在庫一覧','ここは登録した初期在庫の一覧です。現在数量の集計は「在庫一覧」で確認します。数量は登録時（訂正後は訂正後）の数量です。')}
    <div class="list-stage" id="stockliststage"><div class="tablewrap"><table><thead><tr><th>基準日</th><th>顧客</th><th>商品</th><th>加工仕様</th><th>サイズ</th><th>数量</th><th>仕入単価</th><th>保管場所</th></tr></thead><tbody>
      ${entries.map(e=>`<tr>
        <td>${esc(e.basisDate)}</td>
        <td class="dm-primary">${esc(e.customerName)}</td>
        <td>${esc(e.code)} ${esc(e.productName)} ${esc(e.color)}<span class="sub dm-secondary">商品ID ${e.productId}</span></td>
        <td>${esc(e.embroideryLabel)}</td>
        <td>${esc(e.size)}</td>
        <td class="qty">${num(e.qty)}</td>
        <td class="money">${yen(e.unitCost)}</td>
        <td>${esc(e.locationLabel)}</td>
      </tr>`).join('')||'<tr><td colspan="8">登録された初期在庫はまだありません。</td></tr>'}
    </tbody></table></div><div class="list-grid" id="stocklistgrid" hidden></div></div>
  </section>`
  const grid=$('#stocklistgrid')
  if(grid)grid.innerHTML=entries.length?entries.map(e=>`<article class="dm-card"><div class="dm-primary">${esc(e.customerName)}</div><div class="dm-secondary">${esc(e.code)} ${esc(e.productName)} ${esc(e.color)} · 商品ID ${e.productId}</div><div class="dm-meta"><span>${esc(e.basisDate)}</span><span>${esc(e.embroideryLabel)}</span><span>${esc(e.size)}</span><span>${num(e.qty)}</span><span class="money">${yen(e.unitCost)}</span></div><div class="dm-secondary">${esc(e.locationLabel)}</div></article>`).join(''):'<div class="dm-empty">登録された初期在庫はまだありません。</div>'
  const stage=$('#stockliststage')
  const mode=mountStockDisplay(stage,'opening',()=>paint())
  if(grid)grid.hidden=mode!=='grid'
  if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,stockScreenId('opening'))
  $('#openOpeningRegister').onclick=()=>openOpeningRegister()
  bindExcel()
}
function openOpeningRegister(){
  const dlg=openingDialogEl()
  if(!dlg)return
  state.openingDirty=false
  mountOpeningRegisterForm()
  if(!dlg.open)dlg.showModal()
  dlg.oncancel=ev=>{
    if(state.openingDirty&&!window.confirm('入力中の内容を破棄しますか？')){ev.preventDefault();return}
    dlg.innerHTML=''
    state.openingDirty=false
  }
}
function mountOpeningRegisterForm(){
  const dlg=openingDialogEl()
  if(!dlg)return
  const handles=openingHandlesFromData(state.data)
  const customer=resolveConfirmedOpeningCustomer(state.data.customers,state.opening.customerId)
  const selected=customer?resolveConfirmedOpeningOffering(handles,customer.id,state.opening.handleId):null
  const customerQuery=customer?customer.name:(state.opening.customerQuery||'')
  const handleQuery=selected?offeringPickerLabel(selected):(state.opening.handleQuery||'')
  const mine=customer?filterOpeningOfferings(handles,customer.id,''):[]
  const locSel=openingLocationSelection()
  state.opening.siteId=locSel.siteId==null?'':String(locSel.siteId)
  state.opening.shelfId=locSel.shelfId
  state.opening.locationId=locSel.locationId==null?'':String(locSel.locationId)
  const unknownChecked=state.opening.unknown===false?false:true
  dlg.innerHTML=`<div class="dialoghead"><h2>初期在庫を登録</h2><button type="button" id="closeOpeningForm" class="quiet">閉じる</button></div>
    <div class="dialogbody">
    <p class="excelhint">記入用Excelの各シートに、顧客・仕入先・商品・取扱・保管場所・初期在庫を書けます。同じファイルをもう一度読むと台帳は空欄以外を更新し、同じ初期在庫は増やしません。数量を変えた初期在庫は、別の初期在庫として追加されます。</p>
    <p class="provisional">入荷日が分からない初期在庫は、架空の日付で埋めず「入荷日不明」にします。運用開始後の入荷より先に払い出す想定です。初期在庫同士の順番が不明なときは、払出し順の小さい番号から出します。仕入単価の安さでは順番を決めません。</p>
    <div class="grid">
      ${searchPickerMarkup({label:'顧客',inputId:'customerQuery',listId:'customerOptions',clearId:'customerClear',hintId:'customerHint',placeholder:'顧客名またはカナで検索',value:customerQuery})}
      ${searchPickerMarkup({label:'商品と加工仕様',inputId:'handleQuery',listId:'handleOptions',clearId:'handleClear',hintId:'handleHint',placeholder:customer?'品番・商品名・加工仕様で検索':'先に顧客を選択',value:handleQuery})}
      ${openingLocationFields(locSel)}
      <label>在庫基準日（必須）<input id="basisDate" type="date" value="${esc(state.opening.basis||'')}"></label>
    </div>
    <p id="standardNote"></p>
    <label class="check"><input id="unknown" type="checkbox" ${unknownChecked?'checked':''}>入荷日不明</label>
    <div class="grid"><label>入荷日<input id="receivedOn" type="date" value="${esc(state.opening.receivedOn||'')}"></label><label>入荷日不明のときの払出し順<input id="payoutOrder" inputmode="numeric" value="${esc(state.opening.payoutOrder||'1')}"></label></div>
    <h2>サイズごとの数量と仕入単価</h2>
    <div id="sizerows"></div>
    <div class="bar"><label>この商品にサイズを追加<input id="extraSize"></label><button type="button" id="addExtra">追加</button></div>
    <p id="error"></p>
  </div>
  <div class="dialogactions"><button type="button" id="cancelOpeningForm">閉じる</button><button type="button" class="primary" id="confirmOpen">保存前に確認</button></div>`
  const root=dlg
  const drawSizes=()=>{
    const product=selected&&selected.product
    if(!customer){$('#sizerows',root).innerHTML='<p>顧客を候補から選んでください。</p>';$('#standardNote',root).textContent=' ';return}
    if(!product){$('#sizerows',root).innerHTML='<p>商品と加工仕様を候補から選んでください。取扱が未登録のときは、先に顧客・商品情報で登録してください。</p>';$('#standardNote',root).textContent=' ';return}
    const rows=state.opening.lines&&state.opening.lines.productId===product.id?state.opening.lines.rows:product.sizes.map(size=>({size,qty:'',unitCost:''}))
    state.opening.lines={productId:product.id,rows}
    $('#sizerows',root).innerHTML=rows.map((line,i)=>`<div class="sizerow"><strong>${esc(line.size)}</strong><label>数量<input data-i="${i}" data-k="qty" inputmode="numeric" value="${esc(line.qty)}"></label><label>仕入単価（円）<input data-i="${i}" data-k="unitCost" inputmode="numeric" value="${esc(line.unitCost)}"></label><button type="button" data-addprice="${i}">別の単価</button></div>`).join('')
    $('#sizerows',root).querySelectorAll('input').forEach(el=>el.oninput=()=>{rows[Number(el.dataset.i)][el.dataset.k]=el.value;touchOpeningDirty()})
    $('#sizerows',root).querySelectorAll('[data-addprice]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.addprice);rows.splice(i+1,0,{size:rows[i].size,qty:'',unitCost:''});state.opening.lines.rows=rows;touchOpeningDirty();drawSizes()})
    const standard=$('#standardNote',root)
    standard.textContent=selected&&selected.isStandard&&mine.filter(h=>h.product.id===selected.product.id).length===1?'この顧客はこの加工仕様だけを使う標準仕様です。入出荷では、毎回選び直さなくてよい形で登録されています。':selected?'刺繍ありとなしを併用する顧客では、仕様ごとに在庫を分けています。':' '
  }
  const syncUnknown=()=>{
    const on=$('#unknown',root).checked
    state.opening.unknown=on
    $('#receivedOn',root).disabled=on
    $('#payoutOrder',root).disabled=!on
    if(on){$('#receivedOn',root).value='';state.opening.receivedOn=''}
  }
  const syncLocationState=()=>{
    const next=openingLocationSelection()
    state.opening.siteId=next.siteId==null?'':String(next.siteId)
    state.opening.shelfId=next.shelfId
    state.opening.locationId=next.locationId==null?'':String(next.locationId)
    const hidden=$('#locationId',root)
    if(hidden)hidden.value=state.opening.locationId
  }
  const requestClose=async()=>{
    if(!(await ensureLeaveOpeningForm()))return
  }
  $('#closeOpeningForm',root).onclick=requestClose
  $('#cancelOpeningForm',root).onclick=requestClose
  const customerInput=$('#customerQuery',root)
  const handleInput=$('#handleQuery',root)
  bindOpeningSearchPicker({
    input:customerInput,
    list:$('#customerOptions',root),
    clearBtn:$('#customerClear',root),
    hint:$('#customerHint',root),
    emptyText:'該当する顧客がありません',
    getItems:()=>filterOpeningCustomers(state.data.customers,customerInput.value).map(c=>({
      id:c.id,
      label:c.name,
      html:`<span class="dm-primary">${esc(c.name)}</span>${c.kana?`<span class="sub dm-secondary">${esc(c.kana)}</span>`:''}`,
      raw:c
    })),
    isConfirmed:()=>!!resolveConfirmedOpeningCustomer(state.data.customers,state.opening.customerId),
    confirmedLabel:()=>{
      const c=resolveConfirmedOpeningCustomer(state.data.customers,state.opening.customerId)
      return c?c.name:''
    },
    onPick:item=>{
      const prev=state.opening.customerId
      state.opening.customerId=String(item.id)
      state.opening.customerQuery=item.label
      if(String(prev)!==String(item.id))state.opening=clearOpeningProductFields(state.opening)
      touchOpeningDirty()
      mountOpeningRegisterForm()
    },
    onClear:()=>{state.opening=clearOpeningCustomerSelection(state.opening);touchOpeningDirty();mountOpeningRegisterForm()},
    onQueryChange:value=>{
      touchOpeningDirty()
      const confirmed=resolveConfirmedOpeningCustomer(state.data.customers,state.opening.customerId)
      if(confirmed&&value!==confirmed.name){
        state.opening=clearOpeningCustomerSelection(state.opening)
        state.opening.customerQuery=value
        mountOpeningRegisterForm()
        const el=$('#customerQuery',openingDialogEl())
        if(el){el.focus();const n=el.value.length;el.setSelectionRange(n,n)}
        return true
      }
      state.opening.customerQuery=value
      return false
    }
  })
  bindOpeningSearchPicker({
    input:handleInput,
    list:$('#handleOptions',root),
    clearBtn:$('#handleClear',root),
    hint:$('#handleHint',root),
    emptyText:customer?'該当する商品がありません':'先に顧客を候補から選んでください',
    getItems:()=>{
      if(!resolveConfirmedOpeningCustomer(state.data.customers,state.opening.customerId))return []
      return filterOpeningOfferings(handles,state.opening.customerId,handleInput.value).map(h=>({
        id:h.id,
        label:offeringPickerLabel(h),
        html:`<span class="dm-primary">${esc(h.product.code)} ${esc(h.product.name)}</span><span class="sub dm-secondary">${esc(h.product.color)} / ${esc(specLabel(h.embroidery))}${h.isStandard?' / 標準':''}</span>`,
        raw:h
      }))
    },
    isConfirmed:()=>!!resolveConfirmedOpeningOffering(handles,state.opening.customerId,state.opening.handleId),
    confirmedLabel:()=>{
      const h=resolveConfirmedOpeningOffering(handles,state.opening.customerId,state.opening.handleId)
      return h?offeringPickerLabel(h):''
    },
    onPick:item=>{
      state.opening.handleId=String(item.id)
      state.opening.handleQuery=item.label
      state.opening.lines=null
      touchOpeningDirty()
      mountOpeningRegisterForm()
    },
    onClear:()=>{state.opening=clearOpeningProductFields(state.opening);touchOpeningDirty();mountOpeningRegisterForm()},
    onQueryChange:value=>{
      touchOpeningDirty()
      const confirmed=resolveConfirmedOpeningOffering(handles,state.opening.customerId,state.opening.handleId)
      if(confirmed&&value!==offeringPickerLabel(confirmed)){
        state.opening=clearOpeningProductFields(state.opening)
        state.opening.handleQuery=value
        mountOpeningRegisterForm()
        const el=$('#handleQuery',openingDialogEl())
        if(el){el.focus();const n=el.value.length;el.setSelectionRange(n,n)}
        return true
      }
      state.opening.handleQuery=value
      return false
    }
  })
  if(!customer)handleInput.disabled=true
  $('#unknown',root).onchange=()=>{touchOpeningDirty();syncUnknown()}
  $('#basisDate',root).onchange=()=>{state.opening.basis=$('#basisDate',root).value;touchOpeningDirty()}
  $('#receivedOn',root).oninput=()=>{state.opening.receivedOn=$('#receivedOn',root).value;touchOpeningDirty()}
  $('#payoutOrder',root).oninput=()=>{state.opening.payoutOrder=$('#payoutOrder',root).value;touchOpeningDirty()}
  const siteEl=$('#openingSite',root)
  if(siteEl)siteEl.onchange=()=>{
    state.opening.siteId=siteEl.value
    state.opening.shelfId=''
    state.opening.locationId=''
    touchOpeningDirty()
    mountOpeningRegisterForm()
  }
  const shelfEl=$('#openingShelf',root)
  if(shelfEl)shelfEl.onchange=()=>{
    state.opening.shelfId=shelfEl.value
    touchOpeningDirty()
    syncLocationState()
  }
  $('#addExtra',root).onclick=async()=>{
    const size=$('#extraSize',root).value.trim()
    if(!selected||!size)return
    if(selected.product.sizes.includes(size)){$('#error',root).textContent='そのサイズは既にあります。LLと2Lのように違って見える表記は、自動ではまとめません。';return}
    try{
      const p=selected.product
      const keep=(state.opening.lines?.rows||[]).concat([{size,qty:'',unitCost:''}])
      await api('/api/products',{id:p.id,code:p.code,name:p.name,color:p.color,supplierId:p.supplierId,active:p.active,sizes:p.sizes.concat(size),links:p.links})
      state.opening.basis=$('#basisDate',root).value
      syncLocationState()
      state.data=await api('/api/bootstrap')
      state.opening.lines={productId:p.id,rows:keep}
      touchOpeningDirty()
      toast('サイズを追加しました。')
      mountOpeningRegisterForm()
    }catch(e){$('#error',root).textContent=e.message}
  }
  $('#confirmOpen',root).onclick=()=>confirmOpening()
  syncUnknown();drawSizes()
}
function confirmOpening(){
  const formRoot=openingDialogEl()||document
  try{
    const handles=openingHandlesFromData(state.data)
    const customer=resolveConfirmedOpeningCustomer(state.data.customers,state.opening.customerId)
    if(!customer)throw Error('顧客を候補から選んで確定してください。入力した文字だけでは保存できません。')
    const selected=resolveConfirmedOpeningOffering(handles,customer.id,state.opening.handleId)
    if(!selected)throw Error('商品と加工仕様を候補から選んで確定してください。入力した文字だけでは保存できません。')
    if(!state.opening.lines||!state.opening.lines.rows)throw Error('数量のある行がありません。')
    const lines=[]
    for(const line of state.opening.lines.rows){
      const qty=line.qty.trim()
      const cost=line.unitCost.trim()
      if(!qty&&!cost)continue
      if(!qty||!cost)throw Error(`${line.size}は、数量と仕入単価を両方入力してください。空欄を0円にはしません。`)
      if(!/^\d+$/.test(qty)||Number(qty)<1)throw Error(`${line.size}の数量は1以上の整数です。`)
      if(!/^\d+$/.test(cost))throw Error(`${line.size}の仕入単価を入力してください。空欄を0円にはしません。`)
      lines.push({size:line.size,qty:Number(qty),unitCost:Number(cost)})
    }
    if(!lines.length)throw Error('数量のある行がありません。')
    const seen=new Set()
    for(const line of lines){
      const key=line.size+'\0'+line.unitCost
      if(seen.has(key))throw Error('同じサイズ・同じ単価の行があります。数量を1行にまとめてください。')
      seen.add(key)
    }
    const basis=$('#basisDate',formRoot).value
    if(!basis)throw Error('在庫基準日を入力してください。')
    const unknown=$('#unknown',formRoot).checked
    if(!unknown&&!$('#receivedOn',formRoot).value)throw Error('入荷日を入力するか、入荷日不明にしてください。')
    if(unknown&&!/^\d+$/.test($('#payoutOrder',formRoot).value))throw Error('払出し順は1以上の整数です。')
    const siteId=$('#openingSite',formRoot)?$('#openingSite',formRoot).value:''
    if(!siteId)throw Error('拠点を選んでください。')
    const shelfEl=$('#openingShelf',formRoot)
    const shelfLabel=$('#openingShelfLabel',formRoot)
    const needShelf=shelfLabel&&!shelfLabel.hidden
    const shelfId=shelfEl?shelfEl.value:''
    if(needShelf&&!shelfId)throw Error('棚・区画を選んでください。')
    const locationId=Number(needShelf?shelfId:siteId)
    const loc=state.data.locations.find(l=>l.id===locationId)
    if(!loc)throw Error('保管場所を、登録済みの場所から選んでください。')
    const locLabel=loc.parentId?loc.label:(needShelf&&Number(shelfId)===Number(siteId)?'棚指定なし（'+loc.name+'）':loc.label)
    state.token=crypto.randomUUID()
    const totalQty=lines.reduce((s,l)=>s+l.qty,0)
    const totalAmount=lines.reduce((s,l)=>s+l.qty*l.unitCost,0)
    const dlg=$('#dialog')
    dlg.innerHTML=`<div class="dialoghead"><h2>初期在庫の確認</h2></div><div class="dialogbody">
      <p>${esc(selected.customer.name)} / ${esc(selected.product.code)} ${esc(selected.product.name)} ${esc(selected.product.color)} / ${esc(specLabel(selected.embroidery))}</p>
      <p>${esc(locLabel)}　基準日 ${esc(basis)}　${unknown?'入荷日不明・払出し順 '+esc($('#payoutOrder',formRoot).value):'入荷日 '+esc($('#receivedOn',formRoot).value)}</p>
      <ul>${lines.map(l=>`<li>${esc(l.size)}　${num(l.qty)}着 × ${yen(l.unitCost)} ＝ ${yen(l.qty*l.unitCost)}</li>`).join('')}</ul>
      <p class="money">合計 ${num(totalQty)}着　${yen(totalAmount)}</p>
      <p>この金額は、仕入原価ベースの在庫金額です。</p><p id="error"></p></div>
      <div class="dialogactions"><button type="button" id="back">戻る</button><button type="button" class="primary" id="commit">この内容で登録</button></div>`
    dlg.showModal()
    $('#back',dlg).onclick=()=>{dlg.close();dlg.innerHTML=''}
    $('#commit',dlg).onclick=async()=>{
      const button=$('#commit',dlg);button.disabled=true
      try{
        const result=await api('/api/opening',{token:state.token,customerProductId:selected.id,locationId,basisDate:basis,receivedUnknown:unknown,receivedOn:unknown?'':$('#receivedOn',formRoot).value,payoutOrder:unknown?Number($('#payoutOrder',formRoot).value):0,lines})
        dlg.close();dlg.innerHTML=''
        toast(result.already?'この登録は保存済みです。二重には加算していません。':'初期在庫を登録しました。')
        state.opening=openingStateAfterSuccessfulSave(state.opening)
        state.openingDirty=false
        applySectionState('setup','opening')
        state.data=await api('/api/bootstrap')
        await loadOpeningEntries()
        paint()
        openOpeningRegister()
      }catch(e){dlg.querySelector('#error').textContent=e.message;button.disabled=false}
    }
  }catch(e){
    const err=$('#error',formRoot)
    if(err)err.textContent=e.message
  }
}
async function loadStock(){
  const f=state.filters
  const q=new URLSearchParams({group:f.group})
  if(f.customerId)q.set('customerId',f.customerId)
  if(f.productId)q.set('productId',f.productId)
  if(f.size)q.set('size',f.size)
  if(f.spec)q.set('spec',f.spec)
  if(f.locationId)q.set('locationId',f.locationId)
  state.stock=await api('/api/stock?'+q.toString())
}
function stockList(){
  const f=state.filters
  const sizes=[...new Set(state.data.products.flatMap(p=>p.sizes))]
  const specs=new Map()
  state.data.products.forEach(p=>p.links.forEach(l=>specs.set(l.embroidery||'none',specLabel(l.embroidery))))
  const opt=(value,label,current)=>`<option value="${esc(value)}" ${String(current)===String(value)?'selected':''}>${esc(label)}</option>`
  $('#main').innerHTML=`<section class="panel stock-list-panel">
    <div class="bar stock-list-filters">
      <label>顧客<select id="fCustomer"><option value="">すべて</option>${state.data.customers.map(c=>opt(c.id,c.name,f.customerId)).join('')}</select></label>
      <label>商品<select id="fProduct"><option value="">すべて</option>${state.data.products.map(p=>opt(p.id,`${p.code} ${p.name} ${p.color}（ID ${p.id}）`,f.productId)).join('')}</select></label>
      <label>サイズ<select id="fSize"><option value="">すべて</option>${sizes.map(s=>opt(s,s,f.size)).join('')}</select></label>
      <label>加工仕様<select id="fSpec"><option value="">すべて</option>${[...specs].map(([k,v])=>opt(k,v,f.spec)).join('')}</select></label>
      <label>保管場所<select id="fLocation"><option value="">すべて</option>${state.data.locations.map(l=>opt(l.id,l.label,f.locationId)).join('')}</select></label>
    </div>
    <div class="tabs" id="groups">${[['product','商品別'],['customer','顧客別'],['location','場所別'],['lot','入荷分']].map(([id,label])=>`<button type="button" data-group="${id}" class="${f.group===id?'active':''}">${label}</button>`).join('')}</div>
    <div class="total"><div>数量<strong>${state.stock?num(state.stock.totalQty):0}</strong></div><div>仕入原価ベースの在庫金額<strong>${state.stock?yen(state.stock.totalAmount):yen(0)}</strong></div></div>
    ${listNote('在庫金額の見方','表示中の金額は、各入荷分の「残数量×仕入単価」を足したものです。会社全体の資産額や、会計上の評価額と同じものではありません。数量と金額は計算結果なので、手入力できません。')}
    <div class="list-stage" id="stockliststage"><div id="stockbody"></div><div class="list-grid" id="stocklistgrid" hidden></div></div></section>`
  const apply=async()=>{f.customerId=$('#fCustomer').value;f.productId=$('#fProduct').value;f.size=$('#fSize').value;f.spec=$('#fSpec').value;f.locationId=$('#fLocation').value;await loadStock();paint()}
  ;['fCustomer','fProduct','fSize','fSpec','fLocation'].forEach(id=>$('#'+id).onchange=apply)
  $('#groups').querySelectorAll('button').forEach(b=>b.onclick=async()=>{f.group=b.dataset.group;await loadStock();paint()})
  const stage=$('#stockliststage')
  const mode=mountStockDisplay(stage,'list',()=>paint())
  const body=$('#stockbody')
  const grid=$('#stocklistgrid')
  if(!state.stock){body.innerHTML='';if(grid)grid.innerHTML='';return}
  if(f.group==='lot'){
    body.innerHTML=`<div class="tablewrap"><table><thead><tr><th>顧客</th><th>商品</th><th>仕様</th><th>場所</th><th>単価</th><th>数量</th><th>金額</th><th></th></tr></thead><tbody>
      ${state.stock.lots.map(l=>`<tr><td class="dm-primary">${esc(l.customerName)}</td><td>${esc(l.code)} ${esc(l.productName)} ${esc(l.color)}<span class="sub dm-secondary">商品ID ${l.productId}</span></td><td>${esc(l.size)} / ${esc(l.embroideryLabel)}</td><td>${esc(l.locationLabel)}<span class="sub">${l.receivedUnknown?'入荷日不明・払出し順 '+l.payoutOrder:'入荷日 '+esc(l.receivedOn)}</span></td><td class="money">${yen(l.unitCost)}</td><td class="qty">${num(l.qty)}</td><td class="money">${yen(l.amount)}</td><td>${l.correctable?`<button type="button" data-correct="${l.id}">訂正</button>`:''}<button type="button" data-history="${l.id}">履歴</button> ${stockOpenBtn(l.id,'開く')}</td></tr>`).join('')||'<tr><td colspan="8">在庫はありません。</td></tr>'}
    </tbody></table></div>`
    if(grid)grid.innerHTML=state.stock.lots.length?state.stock.lots.map(l=>`<article class="dm-card"><div class="dm-primary">${esc(l.customerName)}</div><div class="dm-secondary">${esc(l.code)} ${esc(l.productName)} ${esc(l.color)}（商品ID ${l.productId}）</div><div class="dm-meta"><span>${esc(l.size)} / ${esc(l.embroideryLabel)}</span><span>${esc(l.locationLabel)}</span></div><div class="dm-secondary">${l.receivedUnknown?'入荷日不明・払出し順 '+l.payoutOrder:'入荷日 '+esc(l.receivedOn)}</div><div class="dm-meta"><span>${yen(l.unitCost)}</span><span>${num(l.qty)}</span><span>${yen(l.amount)}</span></div><div class="dm-actions">${l.correctable?`<button type="button" data-correct="${l.id}">訂正</button>`:''}<button type="button" data-history="${l.id}">履歴</button><button type="button" class="quiet stockopen" data-id="${l.id}">開く</button></div></article>`).join(''):'<div class="dm-empty">在庫はありません。</div>'
    const bindLot=()=>{
      document.querySelectorAll('[data-correct]').forEach(b=>b.onclick=()=>correctLot(state.stock.lots.find(l=>l.id===Number(b.dataset.correct))))
      document.querySelectorAll('[data-history]').forEach(b=>b.onclick=()=>showHistory(Number(b.dataset.history)))
      document.querySelectorAll('.stockopen').forEach(b=>b.onclick=()=>showHistory(Number(b.dataset.id)))
    }
    bindLot()
    if(grid)grid.hidden=mode!=='grid'
    if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,stockScreenId('list'))
    return
  }
  if(f.group==='product'){
    body.innerHTML=renderProductStockList(state.stock.groups)||'<p class="dialogbody">在庫はありません。</p>'
    if(grid)grid.innerHTML=renderProductStockGrid(state.stock.groups)
  }else if(f.group==='customer'){
    body.innerHTML=renderCustomerStockList(state.stock.groups)||'<p class="dialogbody">在庫はありません。</p>'
    if(grid)grid.innerHTML=renderCustomerStockGrid(state.stock.groups)
  }else if(f.group==='location'){
    body.innerHTML=renderLocationStockList(state.stock.groups)||'<p class="dialogbody">在庫はありません。</p>'
    if(grid)grid.innerHTML=renderLocationStockGrid(state.stock.groups)
  }else{
    body.innerHTML='<p class="dialogbody">在庫はありません。</p>'
    if(grid)grid.innerHTML='<div class="dm-empty">在庫はありません。</div>'
  }
  if(grid)grid.hidden=mode!=='grid'
  if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,stockScreenId('list'))
}

function priceBreakdownHtml(prices){
  if(!prices||prices.length<2)return ''
  return `<details class="price-breakdown"><summary>単価の内訳</summary><ul>${prices.map(p=>`<li>${yen(p.unitCost)} × ${num(p.qty)}枚</li>`).join('')}</ul></details>`
}

function stockGroupHeading(g){
  return `<h2 class="dm-primary product-stock-title">${esc(g.title)}</h2>
    <p class="dm-secondary product-stock-total">合計${num(g.qty)}枚／在庫金額${yen(g.amount)}</p>`
}

function productNameColor(r){
  return r.color?`${esc(r.productName)}／${esc(r.color)}`:esc(r.productName)
}

function renderProductStockList(groups){
  if(!groups.length)return ''
  return groups.map(g=>`<article class="product-stock-group">
    ${stockGroupHeading(g)}
    <div class="tablewrap"><table class="product-stock-table"><thead><tr><th>顧客</th><th>加工仕様</th><th>サイズ</th><th>保管場所</th><th>数量</th><th>在庫金額</th></tr></thead><tbody>
      ${(g.rows||[]).map(r=>`<tr>
        <td class="dm-primary">${esc(r.customerName)}</td>
        <td>${esc(r.embroideryLabel)}</td>
        <td>${esc(r.size)}</td>
        <td>${esc(r.locationLabel)}</td>
        <td class="qty">${num(r.qty)}${priceBreakdownHtml(r.prices)}</td>
        <td class="money">${yen(r.amount)}</td>
      </tr>`).join('')||'<tr><td colspan="6">在庫はありません。</td></tr>'}
    </tbody></table></div>
  </article>`).join('')
}

function renderProductStockGrid(groups){
  if(!groups.length)return '<div class="dm-empty">在庫はありません。</div>'
  return groups.map(g=>`<div class="dm-group">${esc(g.title)}<span>${num(g.qty)}枚 / ${yen(g.amount)}</span></div>`+
    (g.rows||[]).map(r=>`<article class="dm-card">
      <div class="dm-primary">${esc(r.customerName)}</div>
      <div class="dm-secondary">${esc(r.embroideryLabel)} / ${esc(r.size)}</div>
      <div class="dm-meta"><span>${esc(r.locationLabel)}</span><span>${num(r.qty)}</span><span>${yen(r.amount)}</span></div>
      ${priceBreakdownHtml(r.prices)}
    </article>`).join('')
  ).join('')
}

function renderCustomerStockList(groups){
  if(!groups.length)return ''
  return groups.map(g=>{
    let prevPid=null
    const rows=(g.rows||[]).map(r=>{
      const breakCls=prevPid!=null&&prevPid!==r.productId?' is-product-break':''
      prevPid=r.productId
      return `<tr class="${breakCls.trim()}">
        <td class="dm-primary">${esc(r.code)}</td>
        <td>${productNameColor(r)}</td>
        <td>${esc(r.embroideryLabel)}</td>
        <td>${esc(r.size)}</td>
        <td>${esc(r.locationLabel)}</td>
        <td class="qty">${num(r.qty)}${priceBreakdownHtml(r.prices)}</td>
        <td class="money">${yen(r.amount)}</td>
      </tr>`
    }).join('')
    return `<article class="product-stock-group">
    ${stockGroupHeading(g)}
    <div class="tablewrap"><table class="product-stock-table"><thead><tr><th>品番</th><th>商品名／色</th><th>加工仕様</th><th>サイズ</th><th>保管場所</th><th>数量</th><th>在庫金額</th></tr></thead><tbody>
      ${rows||'<tr><td colspan="7">在庫はありません。</td></tr>'}
    </tbody></table></div>
  </article>`
  }).join('')
}

function renderCustomerStockGrid(groups){
  if(!groups.length)return '<div class="dm-empty">在庫はありません。</div>'
  return groups.map(g=>{
    let prevPid=null
    return `<div class="dm-group">${esc(g.title)}<span>${num(g.qty)}枚 / ${yen(g.amount)}</span></div>`+
      (g.rows||[]).map(r=>{
        const breakCls=prevPid!=null&&prevPid!==r.productId?' is-product-break':''
        prevPid=r.productId
        return `<article class="dm-card${breakCls}">
      <div class="dm-primary">${esc(r.code)} ${productNameColor(r)}</div>
      <div class="dm-secondary">${esc(r.embroideryLabel)} / ${esc(r.size)}</div>
      <div class="dm-meta"><span>${esc(r.locationLabel)}</span><span>${num(r.qty)}</span><span>${yen(r.amount)}</span></div>
      ${priceBreakdownHtml(r.prices)}
    </article>`
      }).join('')
  }).join('')
}

function renderLocationStockList(groups){
  if(!groups.length)return ''
  return groups.map(g=>{
    let prevLoc=null
    const rows=(g.rows||[]).map(r=>{
      const breakCls=prevLoc!=null&&prevLoc!==r.locationId?' is-shelf-break':''
      prevLoc=r.locationId
      return `<tr class="${breakCls.trim()}">
        <td class="dm-primary">${esc(r.shelfLabel)}</td>
        <td>${esc(r.customerName)}</td>
        <td>${esc(r.code)}</td>
        <td>${productNameColor(r)}</td>
        <td>${esc(r.embroideryLabel)}</td>
        <td>${esc(r.size)}</td>
        <td class="qty">${num(r.qty)}${priceBreakdownHtml(r.prices)}</td>
        <td class="money">${yen(r.amount)}</td>
      </tr>`
    }).join('')
    return `<article class="product-stock-group">
    ${stockGroupHeading(g)}
    <div class="tablewrap"><table class="product-stock-table"><thead><tr><th>棚・区画</th><th>顧客</th><th>品番</th><th>商品名／色</th><th>加工仕様</th><th>サイズ</th><th>数量</th><th>在庫金額</th></tr></thead><tbody>
      ${rows||'<tr><td colspan="8">在庫はありません。</td></tr>'}
    </tbody></table></div>
  </article>`
  }).join('')
}

function renderLocationStockGrid(groups){
  if(!groups.length)return '<div class="dm-empty">在庫はありません。</div>'
  return groups.map(g=>{
    let prevLoc=null
    return `<div class="dm-group">${esc(g.title)}<span>${num(g.qty)}枚 / ${yen(g.amount)}</span></div>`+
      (g.rows||[]).map(r=>{
        const breakCls=prevLoc!=null&&prevLoc!==r.locationId?' is-shelf-break':''
        prevLoc=r.locationId
        return `<article class="dm-card${breakCls}">
      <div class="dm-primary">${esc(r.shelfLabel)} / ${esc(r.customerName)}</div>
      <div class="dm-secondary">${esc(r.code)} ${productNameColor(r)}</div>
      <div class="dm-meta"><span>${esc(r.embroideryLabel)}</span><span>${esc(r.size)}</span><span>${num(r.qty)}</span><span>${yen(r.amount)}</span></div>
      ${priceBreakdownHtml(r.prices)}
    </article>`
      }).join('')
  }).join('')
}

function correctLot(lot){
  const dlg=$('#dialog')
  dlg.innerHTML=`<form><div class="dialoghead"><h2>初期在庫の訂正</h2></div><div class="dialogbody">
    <p>${esc(lot.customerName)} / ${esc(lot.code)} ${esc(lot.productName)} / ${esc(lot.size)} / ${esc(lot.embroideryLabel)} / ${esc(lot.locationLabel)}</p>
    <p>変更前: ${num(lot.qty)} × ${yen(lot.unitCost)} ＝ ${yen(lot.amount)}</p>
    <div class="grid"><label>訂正後の数量（必須）<input id="qty" inputmode="numeric" value="${lot.qty}"></label><label>訂正後の仕入単価（必須）<input id="unitCost" inputmode="numeric" value="${lot.unitCost}"></label></div>
    <p id="preview"></p><label>訂正の理由（必須）<input id="reason"></label><p id="error"></p></div>
    <div class="dialogactions"><button type="button" id="close">戻る</button><button type="button" class="primary" id="save">確認して保存</button></div></form>`
  dlg.showModal()
  dlg.querySelector('form').onsubmit=ev=>ev.preventDefault()
  const preview=()=>{
    const q=$('#qty').value.trim(),c=$('#unitCost').value.trim()
    if(!/^\d+$/.test(q)||!/^\d+$/.test(c)){$('#preview').textContent='数量と仕入単価を入力すると、訂正後の金額を表示します。空欄は0円にしません。';return null}
    const amount=Number(q)*Number(c)
    $('#preview').textContent=`変更後: ${num(q)} × ${yen(c)} ＝ ${yen(amount)}`
    return {qty:Number(q),unitCost:Number(c),amount}
  }
  let armed=false
  const arm=()=>{armed=false;const button=$('#save');if(button)button.textContent='確認して保存'}
  $('#qty').oninput=()=>{arm();preview()};$('#unitCost').oninput=()=>{arm();preview()};preview()
  $('#close').onclick=()=>{dlg.close();dlg.innerHTML=''}
  $('#save').onclick=async()=>{
    const next=preview()
    if(!next){$('#error').textContent='数量と仕入単価を入力してください。空欄を0円にはしません。';return}
    if(!$('#reason').value.trim()){$('#error').textContent='訂正の理由を入力してください。';return}
    if(!armed){armed=true;$('#save').textContent='この内容で訂正';$('#error').textContent=`変更前 ${yen(lot.amount)}、変更後 ${yen(next.amount)}。よければもう一度押してください。`;return}
    const button=$('#save');button.disabled=true
    try{
      await api('/api/opening/correct',{lotId:lot.id,qty:next.qty,unitCost:next.unitCost,reason:$('#reason').value.trim()})
      dlg.close();dlg.innerHTML='';toast('初期在庫を訂正しました。');await loadStock();paint()
    }catch(e){$('#error').textContent=e.message;button.disabled=false;armed=false;button.textContent='確認して保存'}
  }
}
async function showHistory(lotId){
  const data=await api('/api/lots/history?lotId='+lotId)
  const names={opening:'初期在庫',opening_correction:'初期在庫の訂正'}
  const dlg=$('#dialog')
  dlg.innerHTML=`<div class="dialoghead"><h2>在庫の履歴</h2><button type="button" id="close" class="quiet">閉じる</button></div><div class="dialogbody"><ul>${data.events.map(e=>`<li>${esc(e.at)}　${esc(names[e.kind]||e.kind)}　数量 ${e.qtyBefore??'-'}→${e.qtyAfter??'-'}　単価 ${e.costBefore??'-'}→${e.costAfter??'-'}　金額 ${e.amountBefore??'-'}→${e.amountAfter??'-'}<span class="sub">${esc(e.note)}</span></li>`).join('')}</ul></div>`
  dlg.showModal();$('#close').onclick=()=>{dlg.close();dlg.innerHTML=''}
}
document.querySelectorAll('.switch button[data-mode]').forEach(b=>b.onclick=async()=>{
  $('#desknote').hidden=true
  await setStockSection(b.dataset.mode==='masters'?'setup':'list')
})
$('#desk').onclick=async()=>{
  if(!(await ensureCanNavigate()))return
  if(embedded){window.parent.postMessage({type:'desk-catalog',mode:'desk'},location.origin);return}
  $('#desknote').hidden=false
}
window.addEventListener('message',async e=>{
  if(e.origin!==location.origin||!e.data||e.data.type!=='stock-section')return
  await setStockSection(e.data.section,{setupTab:e.data.setupTab})
})
boot().catch(e=>{$('#main').textContent=e.message})
