const $=(s,r=document)=>r.querySelector(s)
const state={section:'list',setupTab:'customers',mode:'stock',tab:'list',q:'',openingListQ:'',moveQ:'',moveMode:'in',moveTargets:null,moveDraft:{},moveDirty:false,period:{from:'',to:'',customerId:'',productId:'',size:'',spec:'',locationId:'',condition:'',group:'detail'},periodResult:null,history:{from:'',to:'',customerId:'',productId:'',kind:'',condition:'',locationId:'',page:1,pageSize:50},historyResult:null,poQ:'',poStatus:'',poList:null,stopped:false,productSupplierId:'',data:null,stock:null,openings:null,filters:{group:'customer',customerId:'',productId:'',size:'',spec:'',locationId:'',condition:''},token:'',opening:{condition:'new'},openingDirty:false,openingHandleForm:null,editDirty:false}
const CONDITION_OPTS=[['new','新品'],['used','中古']]
function conditionLabel(code){return code==='used'?'中古':code==='new'?'新品':(code?'未設定':'新品')}
function conditionQtyText(r){
  const n=r.qtyNew!=null?r.qtyNew:(r.condition==='new'?r.qty:0)
  const u=r.qtyUsed!=null?r.qtyUsed:(r.condition==='used'?r.qty:0)
  const t=r.qty!=null?r.qty:(Number(n)||0)+(Number(u)||0)
  return `新品${num(n)}枚／中古${num(u)}枚／合計${num(t)}枚`
}
function conditionSelectHtml(id,value,opts){
  const cur=value||'new'
  const allowAll=opts&&opts.allowAll
  const all=allowAll?`<option value="" ${cur===''?'selected':''}>すべて</option>`:''
  return `<select id="${id}" aria-label="状態">${all}${CONDITION_OPTS.map(([v,l])=>`<option value="${v}" ${cur===v?'selected':''}>${l}</option>`).join('')}</select>`
}
const STOCK_SECTIONS=new Set(['list','move','history','setup','period','orders'])
function normalizeSection(section){return STOCK_SECTIONS.has(section)?section:'list'}
function stockCanWrite(){return !(state.data&&state.data.canWrite===false)}
function monthStartISO(d=new Date()){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-01`}
function todayISO(d=new Date()){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}
function prevMonthBounds(d=new Date()){
  const first=new Date(d.getFullYear(),d.getMonth(),1)
  const prevEnd=new Date(first.getTime()-86400000)
  const prevStart=new Date(prevEnd.getFullYear(),prevEnd.getMonth(),1)
  const iso=x=>`${x.getFullYear()}-${String(x.getMonth()+1).padStart(2,'0')}-${String(x.getDate()).padStart(2,'0')}`
  return {from:iso(prevStart),to:iso(prevEnd)}
}
function ensurePeriodDefaults(){
  if(!state.period.from)state.period.from=monthStartISO()
  if(!state.period.to)state.period.to=todayISO()
}
function ensureHistoryDefaults(){
  if(!state.history.from)state.history.from=monthStartISO()
  if(!state.history.to)state.history.to=todayISO()
  if(!state.history.page)state.history.page=1
  if(!state.history.pageSize)state.history.pageSize=50
}
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

const STOCK_SCREENS={customers:'stock-customers',suppliers:'stock-suppliers',products:'stock-products',locations:'stock-locations',opening:'stock-opening',list:'stock-list',move:'stock-move',history:'stock-history',period:'stock-period',orders:'stock-orders'}
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
  // Title-row 表示 slot: 在庫一覧系 + 登録・設定（枠外・右上）
  if(section==='list'||section==='move'||section==='history'||section==='period'||section==='orders'||section==='setup'){
    slot.hidden=false
    return
  }
  slot.hidden=true
  slot.innerHTML=''
}
function mountStockDisplay(stage,tab,repaint){
  if(!stage||typeof DisplayMode==='undefined')return 'standard'
  const screenId=stockScreenId(tab)
  // Title-row 表示 slot (syncStockListDisplaySlot): 在庫一覧・入庫出庫・期間・発注・登録設定.
  // Must remount here so the visible control matches this page's screenId (not a leftover).
  const useTitleSlot=tab==='list'||tab==='move'||tab==='history'||tab==='period'||tab==='orders'||SETUP_TAB_IDS.has(tab)
  const toolbar=useTitleSlot
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
  let result={}
  try{result=await r.json()}catch(_){result={}}
  if(!r.ok){
    const err=Error(result.error||'処理できませんでした。')
    err.status=r.status
    err.payload=result
    throw err
  }
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
  state.section=normalizeSection(section)
  if(state.section==='list'){
    state.tab='list'
    state.mode='stock'
  }else if(state.section==='move'){
    state.tab='move'
    state.mode='stock'
  }else if(state.section==='history'){
    state.tab='history'
    state.mode='stock'
    ensureHistoryDefaults()
  }else if(state.section==='period'){
    state.tab='period'
    state.mode='stock'
    ensurePeriodDefaults()
  }else if(state.section==='orders'){
    state.tab='orders'
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
  const next=normalizeSection(section)
  if(next===state.section&&!(setupTab&&SETUP_TAB_IDS.has(setupTab)&&setupTab!==state.tab)){
    if(!silent)notifyParentSection()
    return true
  }
  if(!(await ensureCanNavigate()))return false
  applySectionState(next,setupTab)
  if(state.tab==='list')await loadStock()
  if(state.tab==='move')await loadMoveTargets()
  if(state.tab==='opening')await loadOpeningEntries()
  if(state.tab==='history'){ensureHistoryDefaults();await loadStockHistory()}
  if(state.tab==='period')ensurePeriodDefaults()
  if(state.tab==='orders'&&typeof PurchaseOrders!=='undefined')await PurchaseOrders.loadList()
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
    applySectionState(normalizeSection(params.get('section')),loadSetupTab())
  }
  state.ready=true
  const labels={sample:['試用版・架空のサンプル','サンプルのデータは data\\sample に保存されています。'],desk:['在庫管理','指示書デスクと同じ画面から使っています。在庫のデータは指示書とは別のファイルです。'],live:['試用版・このパソコンの台帳','この台帳は data\\live に保存されています。サンプルとは別です。']}
  const label=labels[state.data.mode]||labels.live
  $('#eyebrow').textContent=label[0]
  $('#foot').textContent=label[1]
  if(state.tab==='list')await loadStock()
  if(state.tab==='move')await loadMoveTargets()
  if(state.tab==='opening')await loadOpeningEntries()
  if(state.tab==='history'){ensureHistoryDefaults();await loadStockHistory()}
  if(state.tab==='period')ensurePeriodDefaults()
  if(state.tab==='orders'&&typeof PurchaseOrders!=='undefined')await PurchaseOrders.loadList()
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
  }else if(state.section==='move'){
    state.tab='move'
    state.mode='stock'
  }else if(state.section==='history'){
    state.tab='history'
    state.mode='stock'
    ensureHistoryDefaults()
  }else if(state.section==='period'){
    state.tab='period'
    state.mode='stock'
    ensurePeriodDefaults()
  }else if(state.section==='orders'){
    state.tab='orders'
    state.mode='stock'
  }else{
    state.tab='list'
    state.mode='stock'
  }
  document.querySelectorAll('.switch button[data-mode]').forEach(b=>{
    const want=b.dataset.mode==='masters'?'setup':'list'
    b.classList.toggle('active',want===state.section||(want==='list'&&(state.section==='move'||state.section==='history'||state.section==='period'||state.section==='orders')))
  })
  const title=$('#stocktitle')
  const titles={setup:SETUP_TAB_LABEL[state.tab]||'顧客',move:'入庫・出庫',history:'履歴',period:'期間入出荷集計',orders:'発注',list:'在庫一覧'}
  if(title)title.textContent=titles[state.section]||titles.list
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
  const view={customers:customers,suppliers:suppliers,products:products,locations:locations,opening:opening,list:stockList,move:movePage,history:historyPage,period:periodPage,orders:()=>typeof PurchaseOrders!=='undefined'?PurchaseOrders.page():($('#main').innerHTML='<p>発注画面を読み込めませんでした。</p>')}[state.tab]
  view()
}
async function loadOpeningEntries(){
  state.openings=await api('/api/opening/entries')
}
function openingDialogEl(){return $('#openingDialog')}
function openingDialogIsOpen(){const dlg=openingDialogEl();return !!(dlg&&dlg.open)}
function touchOpeningDirty(){state.openingDirty=true}
function closeOpeningDialog(){
  state.openingHandleForm=null
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
function supplierForm(r){r=r||{};return `<div class="grid">${field('name','仕入先名',r.name,true)}${field('phone','電話番号',r.phone)}${field('fax','FAX',r.fax)}${field('contact','担当者',r.contact)}</div><label>住所<textarea id="address">${esc(r.address||'')}</textarea></label>`}
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
  if(title.includes('仕入先'))return {id:row&&row.id,name:$('#name').value,phone:$('#phone').value,fax:$('#fax')?$('#fax').value:'',contact:$('#contact').value,address:$('#address').value,active}
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
function searchPickerMarkup({label,inputId,listId,clearId,hintId,placeholder,value,required=true}){
  const req=required?'（必須）':''
  return `<label class="search-picker">${esc(label)}${req}
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
    if(typeof onQueryChange==='function'&&onQueryChange(input.value))return
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
  const all=(state.openings&&state.openings.entries)||[]
  const q=state.openingListQ||''
  const entries=typeof filterOpeningListEntries==='function'?filterOpeningListEntries(all,q):all
  const emptyMsg=all.length&&!entries.length?'該当する初期在庫はありません':'登録された初期在庫はまだありません。'
  const countLabel=q.trim()?`${entries.length} / ${all.length}件`:`${all.length}件`
  $('#main').innerHTML=`<section class="panel">
    <div class="bar">
      <label class="opening-list-search">検索<input id="openingListQ" value="${esc(q)}" placeholder="顧客名・品番・商品名で検索" aria-label="顧客名・品番・商品名で検索"></label>
      <span class="opening-list-count" id="openingListCount">${esc(countLabel)}</span>
      <button type="button" class="primary" id="openOpeningRegister">初期在庫を登録</button>
      ${excelTools()}
    </div>
    ${listNote('初期在庫一覧','ここは登録した初期在庫の一覧です。現在数量の集計は「在庫一覧」で確認します。数量は登録時（訂正後は訂正後）の数量です。')}
    <div class="list-stage" id="stockliststage"><div class="tablewrap"><table><thead><tr><th>顧客</th><th>商品</th><th>加工仕様</th><th>サイズ</th><th>状態</th><th>数量</th><th>仕入単価</th><th>保管場所</th></tr></thead><tbody>
      ${entries.map(e=>`<tr>
        <td class="dm-primary">${esc(e.customerName)}</td>
        <td>${esc(e.code)} ${esc(e.productName)} ${esc(e.color)}<span class="sub dm-secondary">商品ID ${e.productId}</span></td>
        <td>${esc(e.embroideryLabel)}</td>
        <td>${esc(e.size)}</td>
        <td>${esc(e.conditionLabel||conditionLabel(e.condition))}</td>
        <td class="qty">${num(e.qty)}</td>
        <td class="money">${yen(e.unitCost)}</td>
        <td>${esc(e.locationLabel)}</td>
      </tr>`).join('')||`<tr><td colspan="8">${emptyMsg}</td></tr>`}
    </tbody></table></div><div class="list-grid" id="stocklistgrid" hidden></div></div>
  </section>`
  const grid=$('#stocklistgrid')
  if(grid)grid.innerHTML=entries.length?entries.map(e=>`<article class="dm-card"><div class="dm-primary">${esc(e.customerName)}</div><div class="dm-secondary">${esc(e.code)} ${esc(e.productName)} ${esc(e.color)} · 商品ID ${e.productId}</div><div class="dm-meta"><span>${esc(e.embroideryLabel)}</span><span>${esc(e.size)}</span><span>${esc(e.conditionLabel||conditionLabel(e.condition))}</span><span>${num(e.qty)}</span><span class="money">${yen(e.unitCost)}</span></div><div class="dm-secondary">${esc(e.locationLabel)}</div></article>`).join(''):`<div class="dm-empty">${emptyMsg}</div>`
  const stage=$('#stockliststage')
  const mode=mountStockDisplay(stage,'opening',()=>paint())
  if(grid)grid.hidden=mode!=='grid'
  if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,stockScreenId('opening'))
  $('#openOpeningRegister').onclick=()=>openOpeningRegister()
  const search=$('#openingListQ')
  if(search)search.oninput=e=>{const pos=e.target.selectionStart;state.openingListQ=e.target.value;paint();const qEl=$('#openingListQ');if(qEl){qEl.focus();qEl.setSelectionRange(pos,pos)}}
  bindExcel()
}
function openOpeningRegister(){
  const dlg=openingDialogEl()
  if(!dlg)return
  state.openingDirty=false
  state.openingHandleForm=null
  mountOpeningRegisterForm()
  if(!dlg.open)dlg.showModal()
  dlg.oncancel=ev=>{
    if(state.openingHandleForm){
      ev.preventDefault()
      cancelOpeningHandleCreate()
      return
    }
    if(state.openingDirty&&!window.confirm('入力中の内容を破棄しますか？')){ev.preventDefault();return}
    dlg.innerHTML=''
    state.openingDirty=false
    state.openingHandleForm=null
  }
}
function cancelOpeningHandleCreate(){
  state.openingHandleForm=null
  mountOpeningRegisterForm()
}
function openOpeningHandleCreate(){
  state.openingHandleForm={
    customerId:null,
    customerQuery:'',
    productId:null,
    productQuery:'',
    embroidery:'',
    saving:false
  }
  mountOpeningRegisterForm()
}
function mountOpeningHandleCreateForm(dlg){
  const draft=state.openingHandleForm||{}
  const customer=resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId)
  const product=resolveConfirmedOpeningProduct(state.data.products,draft.productId)
  const customerQuery=customer?customer.name:(draft.customerQuery||'')
  const productQuery=product?productPickerLabel(product):(draft.productQuery||'')
  dlg.innerHTML=`<div class="dialoghead"><h2>取扱を新しく登録</h2><button type="button" id="closeHandleCreate" class="quiet">キャンセル</button></div>
    <div class="dialogbody">
      <p class="provisional">顧客と商品は台帳から選びます。未登録の名前では自動作成しません。保存するのは顧客・商品・加工情報の紐づけだけで、在庫の枚数は増えません。</p>
      <div class="grid">
        ${searchPickerMarkup({label:'顧客',inputId:'handleCustomerQuery',listId:'handleCustomerOptions',clearId:'handleCustomerClear',hintId:'handleCustomerHint',placeholder:'顧客名またはカナで検索',value:customerQuery})}
        ${searchPickerMarkup({label:'商品',inputId:'handleProductQuery',listId:'handleProductOptions',clearId:'handleProductClear',hintId:'handleProductHint',placeholder:'品番・商品名・色・仕入先で検索',value:productQuery})}
        <label>加工情報<input id="handleEmbroidery" maxlength="80" placeholder="空欄は刺繍なし" value="${esc(draft.embroidery||'')}"></label>
      </div>
      <p class="sub">顧客・商品が一覧に無いときは、先にそれぞれの台帳で登録してください。</p>
      <p id="error"></p>
    </div>
    <div class="dialogactions"><button type="button" id="cancelHandleCreate">キャンセル</button><button type="button" class="primary" id="saveHandleCreate">取扱を保存</button></div>`
  const root=dlg
  const customerInput=$('#handleCustomerQuery',root)
  const productInput=$('#handleProductQuery',root)
  bindOpeningSearchPicker({
    input:customerInput,
    list:$('#handleCustomerOptions',root),
    clearBtn:$('#handleCustomerClear',root),
    hint:$('#handleCustomerHint',root),
    emptyText:'該当する顧客がありません。先に顧客台帳で登録してください',
    getItems:()=>filterOpeningCustomers(state.data.customers,customerInput.value).map(c=>({
      id:c.id,
      label:c.name,
      html:`<span class="dm-primary">${esc(c.name)}</span>${c.kana?`<span class="sub dm-secondary">${esc(c.kana)}</span>`:''}`,
      raw:c
    })),
    isConfirmed:()=>!!resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId),
    confirmedLabel:()=>{
      const c=resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId)
      return c?c.name:''
    },
    onPick:item=>{
      draft.customerId=String(item.id)
      draft.customerQuery=item.label
      mountOpeningRegisterForm()
    },
    onClear:()=>{draft.customerId=null;draft.customerQuery='';mountOpeningRegisterForm()},
    onQueryChange:value=>{
      const confirmed=resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId)
      if(confirmed&&value!==confirmed.name){
        draft.customerId=null
        draft.customerQuery=value
        mountOpeningRegisterForm()
        const el=$('#handleCustomerQuery',openingDialogEl())
        if(el){el.focus();const n=el.value.length;el.setSelectionRange(n,n)}
        return true
      }
      draft.customerQuery=value
      return false
    }
  })
  bindOpeningSearchPicker({
    input:productInput,
    list:$('#handleProductOptions',root),
    clearBtn:$('#handleProductClear',root),
    hint:$('#handleProductHint',root),
    emptyText:'該当する商品がありません。先に商品台帳で登録してください',
    getItems:()=>filterOpeningProducts(state.data.products,productInput.value).map(p=>({
      id:p.id,
      label:productPickerLabel(p),
      html:`<span class="dm-primary">${esc(p.code)} ${esc(p.name)}</span><span class="sub dm-secondary">${esc(p.color||'')} / ${esc(p.supplierName||'仕入先未設定')} · 商品ID ${p.id}</span>`,
      raw:p
    })),
    isConfirmed:()=>!!resolveConfirmedOpeningProduct(state.data.products,draft.productId),
    confirmedLabel:()=>{
      const p=resolveConfirmedOpeningProduct(state.data.products,draft.productId)
      return p?productPickerLabel(p):''
    },
    onPick:item=>{
      draft.productId=String(item.id)
      draft.productQuery=item.label
      mountOpeningRegisterForm()
    },
    onClear:()=>{draft.productId=null;draft.productQuery='';mountOpeningRegisterForm()},
    onQueryChange:value=>{
      const confirmed=resolveConfirmedOpeningProduct(state.data.products,draft.productId)
      if(confirmed&&value!==productPickerLabel(confirmed)){
        draft.productId=null
        draft.productQuery=value
        mountOpeningRegisterForm()
        const el=$('#handleProductQuery',openingDialogEl())
        if(el){el.focus();const n=el.value.length;el.setSelectionRange(n,n)}
        return true
      }
      draft.productQuery=value
      return false
    }
  })
  $('#handleEmbroidery',root).oninput=e=>{draft.embroidery=e.target.value}
  const cancel=()=>cancelOpeningHandleCreate()
  $('#closeHandleCreate',root).onclick=cancel
  $('#cancelHandleCreate',root).onclick=cancel
  $('#saveHandleCreate',root).onclick=async()=>{
    const err=$('#error',root)
    const button=$('#saveHandleCreate',root)
    if(draft.saving)return
    try{
      const c=resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId)
      if(!c)throw Error('顧客を候補から選んで確定してください。未登録の名前では作成できません。')
      const p=resolveConfirmedOpeningProduct(state.data.products,draft.productId)
      if(!p)throw Error('商品を候補から選んで確定してください。未登録の名前では作成できません。')
      draft.saving=true
      button.disabled=true
      const result=await api('/api/customer-products',{
        customerId:Number(c.id),
        productId:Number(p.id),
        embroidery:String(draft.embroidery||'').trim()
      })
      state.data=await api('/api/bootstrap')
      const handles=openingHandlesFromData(state.data)
      const selected=resolveConfirmedOpeningHandle(handles,result.id)
      if(!selected)throw Error('取扱は保存されましたが、初期在庫の候補に反映できませんでした。画面を開き直してください。')
      state.opening=selectOpeningHandle(state.opening,selected)
      state.openingHandleForm=null
      touchOpeningDirty()
      toast(result.already?'同じ取扱が既にあったため、それを選びました。在庫はまだ増やしていません。':'取扱を登録し、初期在庫の入力に戻しました。在庫はまだ増やしていません。')
      mountOpeningRegisterForm()
    }catch(e){
      draft.saving=false
      button.disabled=false
      if(err)err.textContent=e.message
    }
  }
}
function mountOpeningRegisterForm(){
  const dlg=openingDialogEl()
  if(!dlg)return
  if(state.openingHandleForm){
    mountOpeningHandleCreateForm(dlg)
    return
  }
  const handles=openingHandlesFromData(state.data)
  const selected=resolveConfirmedOpeningHandle(handles,state.opening.handleId)
  const handleQuery=selected?handlePickerLabel(selected):(state.opening.handleQuery||'')
  const peers=selected?handles.filter(h=>Number(h.customerId)===Number(selected.customerId)&&Number(h.product.id)===Number(selected.product.id)):[]
  const locSel=openingLocationSelection()
  state.opening.siteId=locSel.siteId==null?'':String(locSel.siteId)
  state.opening.shelfId=locSel.shelfId
  state.opening.locationId=locSel.locationId==null?'':String(locSel.locationId)
  const unknownChecked=state.opening.unknown===false?false:true
  const sizeNotice=state.opening.sizeNotice||''
  dlg.innerHTML=`<div class="dialoghead"><h2>初期在庫を登録</h2><button type="button" id="closeOpeningForm" class="quiet">閉じる</button></div>
    <div class="dialogbody">
    <p class="excelhint">記入用Excelの各シートに、顧客・仕入先・商品・取扱・保管場所・初期在庫を書けます。同じファイルをもう一度読むと台帳は空欄以外を更新し、同じ初期在庫は増やしません。数量を変えた初期在庫は、別の初期在庫として追加されます。</p>
    <p class="provisional">入荷日が分からない初期在庫は、架空の日付で埋めず「入荷日不明」にします。運用開始後の入荷より先に払い出す想定です。初期在庫同士の順番が不明なときは、払出し順の小さい番号から出します。仕入単価の安さでは順番を決めません。</p>
    <div class="opening-handle-block">
      ${searchPickerMarkup({label:'取扱',inputId:'handleQuery',listId:'handleOptions',clearId:'handleClear',hintId:'handleHint',placeholder:'顧客名・品番・商品名・加工情報で検索',value:handleQuery})}
      <div class="opening-handle-tools">
        <button type="button" class="quiet" id="openHandleCreate">取扱を新しく登録</button>
        <p class="opening-handle-empty-guide" id="handleEmptyGuide">候補が無いときは「取扱を新しく登録」から、登録済みの顧客と商品を選んで追加できます。顧客・商品そのものは各台帳で先に登録してください。</p>
      </div>
    </div>
    <div class="grid">
      ${openingLocationFields(locSel)}
      <label>在庫基準日（必須）<input id="basisDate" type="date" value="${esc(state.opening.basis||'')}"></label>
      <label>状態（必須）${conditionSelectHtml('openingCondition',state.opening.condition||'new')}</label>
    </div>
    <p class="notice" id="conditionHint">状態は新品を初期選択しています。同じ取扱・サイズ・場所でも、新品と中古は別々に登録できます。</p>
    <p id="standardNote"></p>
    <p id="sizeNotice" class="notice" ${sizeNotice?'':'hidden'}>${esc(sizeNotice)}</p>
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
    if(!product){$('#sizerows',root).innerHTML='<p>取扱を候補から選んでください。未登録なら「取扱を新しく登録」から追加できます。</p>';$('#standardNote',root).textContent=' ';return}
    const rows=state.opening.lines&&state.opening.lines.productId===product.id?state.opening.lines.rows:product.sizes.map(size=>({size,qty:'',unitCost:''}))
    state.opening.lines={productId:product.id,rows}
    $('#sizerows',root).innerHTML=rows.map((line,i)=>`<div class="sizerow"><strong>${esc(line.size)}</strong><label>数量<input data-i="${i}" data-k="qty" inputmode="numeric" value="${esc(line.qty)}"></label><label>仕入単価（円）<input data-i="${i}" data-k="unitCost" inputmode="numeric" value="${esc(line.unitCost)}"></label><button type="button" data-addprice="${i}">別の単価</button></div>`).join('')
    $('#sizerows',root).querySelectorAll('input').forEach(el=>el.oninput=()=>{rows[Number(el.dataset.i)][el.dataset.k]=el.value;touchOpeningDirty()})
    $('#sizerows',root).querySelectorAll('[data-addprice]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.addprice);rows.splice(i+1,0,{size:rows[i].size,qty:'',unitCost:''});state.opening.lines.rows=rows;touchOpeningDirty();drawSizes()})
    const standard=$('#standardNote',root)
    standard.textContent=selected&&selected.isStandard&&peers.length===1?'この顧客はこの加工仕様だけを使う標準仕様です。入出荷では、毎回選び直さなくてよい形で登録されています。':selected?'刺繍ありとなしを併用する顧客では、仕様ごとに在庫を分けています。':' '
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
  $('#openHandleCreate',root).onclick=()=>openOpeningHandleCreate()
  const handleInput=$('#handleQuery',root)
  const emptyGuide=$('#handleEmptyGuide',root)
  const refreshEmptyGuide=()=>{
    if(!emptyGuide)return
    const q=String(handleInput.value||'').trim()
    const hits=filterOpeningHandles(handles,handleInput.value)
    const show=!selected&&(q?hits.length===0:handles.length===0)
    emptyGuide.hidden=!show
  }
  bindOpeningSearchPicker({
    input:handleInput,
    list:$('#handleOptions',root),
    clearBtn:$('#handleClear',root),
    hint:$('#handleHint',root),
    emptyText:'該当する取扱がありません。「取扱を新しく登録」から追加できます',
    getItems:()=>filterOpeningHandles(handles,handleInput.value).map(h=>({
      id:h.id,
      label:handlePickerLabel(h),
      html:`<span class="dm-primary">${esc(h.customer.name)}／${esc(h.product.code)}／${esc(h.product.name)}</span><span class="sub dm-secondary">${esc(h.product.color||'')}／${esc(specLabel(h.embroidery))}${h.isStandard?'／標準':''}</span>`,
      raw:h
    })),
    isConfirmed:()=>!!resolveConfirmedOpeningHandle(handles,state.opening.handleId),
    confirmedLabel:()=>{
      const h=resolveConfirmedOpeningHandle(handles,state.opening.handleId)
      return h?handlePickerLabel(h):''
    },
    onPick:item=>{
      state.opening=selectOpeningHandle(state.opening,item.raw||resolveConfirmedOpeningHandle(handles,item.id))
      touchOpeningDirty()
      mountOpeningRegisterForm()
    },
    onClear:()=>{state.opening=clearOpeningHandleSelection(state.opening);touchOpeningDirty();mountOpeningRegisterForm()},
    onQueryChange:value=>{
      touchOpeningDirty()
      const confirmed=resolveConfirmedOpeningHandle(handles,state.opening.handleId)
      if(confirmed&&value!==handlePickerLabel(confirmed)){
        state.opening=clearOpeningHandleSelection(state.opening)
        state.opening.handleQuery=value
        mountOpeningRegisterForm()
        const el=$('#handleQuery',openingDialogEl())
        if(el){el.focus();const n=el.value.length;el.setSelectionRange(n,n)}
        return true
      }
      state.opening.handleQuery=value
      refreshEmptyGuide()
      return false
    }
  })
  const origFocus=handleInput.onfocus
  handleInput.onfocus=e=>{if(typeof origFocus==='function')origFocus(e);refreshEmptyGuide()}
  refreshEmptyGuide()
  $('#unknown',root).onchange=()=>{touchOpeningDirty();syncUnknown()}
  $('#basisDate',root).onchange=()=>{state.opening.basis=$('#basisDate',root).value;touchOpeningDirty()}
  const condEl=$('#openingCondition',root)
  if(condEl){
    if(!state.opening.condition)state.opening.condition='new'
    condEl.value=state.opening.condition==='used'?'used':'new'
    condEl.onchange=()=>{state.opening.condition=condEl.value;touchOpeningDirty()}
  }
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
  if(sizeNotice){
    delete state.opening.sizeNotice
  }
}
function confirmOpening(){
  const formRoot=openingDialogEl()||document
  try{
    const handles=openingHandlesFromData(state.data)
    const selected=resolveConfirmedOpeningHandle(handles,state.opening.handleId)
    if(!selected)throw Error('取扱を候補から選んで確定してください。入力した文字だけでは保存できません。')
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
    const condition=($('#openingCondition',formRoot)&&$('#openingCondition',formRoot).value)==='used'?'used':'new'
    state.opening.condition=condition
    state.token=crypto.randomUUID()
    const totalQty=lines.reduce((s,l)=>s+l.qty,0)
    const totalAmount=lines.reduce((s,l)=>s+l.qty*l.unitCost,0)
    const dlg=$('#dialog')
    dlg.innerHTML=`<div class="dialoghead"><h2>初期在庫の確認</h2></div><div class="dialogbody">
      <p>${esc(selected.customer.name)} / ${esc(selected.product.code)} ${esc(selected.product.name)} ${esc(selected.product.color)} / ${esc(specLabel(selected.embroidery))}</p>
      <p>${esc(locLabel)}　状態 <strong>${esc(conditionLabel(condition))}</strong>　基準日 ${esc(basis)}　${unknown?'入荷日不明・払出し順 '+esc($('#payoutOrder',formRoot).value):'入荷日 '+esc($('#receivedOn',formRoot).value)}</p>
      <ul>${lines.map(l=>`<li>${esc(l.size)}　${num(l.qty)}着 × ${yen(l.unitCost)} ＝ ${yen(l.qty*l.unitCost)}</li>`).join('')}</ul>
      <p class="money">合計 ${num(totalQty)}着　${yen(totalAmount)}</p>
      <p>この金額は、仕入原価ベースの在庫金額です。</p><p id="error"></p></div>
      <div class="dialogactions"><button type="button" id="back">戻る</button><button type="button" class="primary" id="commit">この内容で登録</button></div>`
    dlg.showModal()
    $('#back',dlg).onclick=()=>{dlg.close();dlg.innerHTML=''}
    $('#commit',dlg).onclick=async()=>{
      const button=$('#commit',dlg);button.disabled=true
      try{
        const result=await api('/api/opening',{token:state.token,customerProductId:selected.id,locationId,basisDate:basis,receivedUnknown:unknown,receivedOn:unknown?'':$('#receivedOn',formRoot).value,payoutOrder:unknown?Number($('#payoutOrder',formRoot).value):0,condition,lines})
        dlg.close();dlg.innerHTML=''
        toast(result.already?'この登録は保存済みです。二重には加算していません。':'初期在庫を登録しました。')
        state.opening=openingStateAfterSuccessfulSave(state.opening)
        state.openingDirty=false
        state.openingHandleForm=null
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
  if(f.condition)q.set('condition',f.condition)
  state.stock=await api('/api/stock?'+q.toString())
}
function stockList(){
  const f=state.filters
  const sizes=[...new Set(state.data.products.flatMap(p=>p.sizes))]
  const specs=new Map()
  state.data.products.forEach(p=>p.links.forEach(l=>specs.set(l.embroidery||'none',specLabel(l.embroidery))))
  const opt=(value,label,current)=>`<option value="${esc(value)}" ${String(current)===String(value)?'selected':''}>${esc(label)}</option>`
  const totalNew=state.stock?state.stock.totalQtyNew:0
  const totalUsed=state.stock?state.stock.totalQtyUsed:0
  $('#main').innerHTML=`<section class="panel stock-list-panel">
    <div class="bar stock-list-filters">
      <label>顧客<select id="fCustomer"><option value="">すべて</option>${state.data.customers.map(c=>opt(c.id,c.name,f.customerId)).join('')}</select></label>
      <label>商品<select id="fProduct"><option value="">すべて</option>${state.data.products.map(p=>opt(p.id,`${p.code} ${p.name} ${p.color}（ID ${p.id}）`,f.productId)).join('')}</select></label>
      <label>サイズ<select id="fSize"><option value="">すべて</option>${sizes.map(s=>opt(s,s,f.size)).join('')}</select></label>
      <label>加工仕様<select id="fSpec"><option value="">すべて</option>${[...specs].map(([k,v])=>opt(k,v,f.spec)).join('')}</select></label>
      <label>保管場所<select id="fLocation"><option value="">すべて</option>${state.data.locations.map(l=>opt(l.id,l.label,f.locationId)).join('')}</select></label>
      <label>状態${conditionSelectHtml('fCondition',f.condition||'',{allowAll:true})}</label>
    </div>
    <div class="tabs" id="groups">${[['customer','顧客別'],['product','商品別'],['location','場所別'],['lot','在庫明細']].map(([id,label])=>`<button type="button" data-group="${id}" class="${f.group===id?'active':''}">${label}</button>`).join('')}</div>
    <div class="total"><div>数量<strong>${state.stock?conditionQtyText({qtyNew:totalNew,qtyUsed:totalUsed,qty:state.stock.totalQty}):conditionQtyText({qtyNew:0,qtyUsed:0,qty:0})}</strong></div><div>仕入原価ベースの在庫金額<strong>${state.stock?yen(state.stock.totalAmount):yen(0)}</strong></div></div>
    ${listNote('在庫金額の見方','表示中の金額は、各入荷（在庫明細）の「残数量×仕入単価」を足したものです。中古を新品の単価では評価しません。会社全体の資産額や、会計上の評価額と同じものではありません。数量と金額は計算結果なので、手入力できません。中古レコードが無い場合も画面上は中古0枚と表示します。')}
    <div class="list-stage" id="stockliststage"><div id="stockbody"></div><div class="list-grid" id="stocklistgrid" hidden></div></div></section>`
  const apply=async()=>{f.customerId=$('#fCustomer').value;f.productId=$('#fProduct').value;f.size=$('#fSize').value;f.spec=$('#fSpec').value;f.locationId=$('#fLocation').value;f.condition=$('#fCondition').value;await loadStock();paint()}
  ;['fCustomer','fProduct','fSize','fSpec','fLocation','fCondition'].forEach(id=>$('#'+id).onchange=apply)
  $('#groups').querySelectorAll('button').forEach(b=>b.onclick=async()=>{f.group=b.dataset.group;await loadStock();paint()})
  const stage=$('#stockliststage')
  const mode=mountStockDisplay(stage,'list',()=>paint())
  const body=$('#stockbody')
  const grid=$('#stocklistgrid')
  if(!state.stock){body.innerHTML='';if(grid)grid.innerHTML='';return}
  if(f.group==='lot'){
    body.innerHTML=`<div class="tablewrap"><table><thead><tr><th>顧客</th><th>商品</th><th>仕様</th><th>状態</th><th>場所</th><th>単価</th><th>数量</th><th>金額</th><th></th></tr></thead><tbody>
      ${state.stock.lots.map(l=>`<tr><td class="dm-primary">${esc(l.customerName)}</td><td>${esc(l.code)} ${esc(l.productName)} ${esc(l.color)}<span class="sub dm-secondary">商品ID ${l.productId}</span></td><td>${esc(l.size)} / ${esc(l.embroideryLabel)}</td><td>${esc(l.conditionLabel||conditionLabel(l.condition))}</td><td>${esc(l.locationLabel)}<span class="sub">${l.receivedUnknown?'入荷日不明・払出し順 '+l.payoutOrder:'入荷日 '+esc(l.receivedOn)}</span></td><td class="money">${yen(l.unitCost)}</td><td class="qty">${num(l.qty)}</td><td class="money">${yen(l.amount)}</td><td>${l.correctable?`<button type="button" data-correct="${l.id}">訂正</button>`:''}${l.inboundCorrectable&&stockCanWrite()?`<button type="button" data-inbound-correct="${l.id}">入庫を訂正</button>`:''} ${stockOpenBtn(l.id,'開く')}</td></tr>`).join('')||'<tr><td colspan="9">在庫はありません。</td></tr>'}
    </tbody></table></div>`
    if(grid)grid.innerHTML=state.stock.lots.length?state.stock.lots.map(l=>`<article class="dm-card"><div class="dm-primary">${esc(l.customerName)}</div><div class="dm-secondary">${esc(l.code)} ${esc(l.productName)} ${esc(l.color)}（商品ID ${l.productId}）</div><div class="dm-meta"><span>${esc(l.size)} / ${esc(l.embroideryLabel)}</span><span>${esc(l.conditionLabel||conditionLabel(l.condition))}</span><span>${esc(l.locationLabel)}</span></div><div class="dm-secondary">${l.receivedUnknown?'入荷日不明・払出し順 '+l.payoutOrder:'入荷日 '+esc(l.receivedOn)}</div><div class="dm-meta"><span>${yen(l.unitCost)}</span><span>${num(l.qty)}</span><span>${yen(l.amount)}</span></div><div class="dm-actions">${l.correctable?`<button type="button" data-correct="${l.id}">訂正</button>`:''}${l.inboundCorrectable&&stockCanWrite()?`<button type="button" data-inbound-correct="${l.id}">入庫を訂正</button>`:''}<button type="button" class="quiet stockopen" data-id="${l.id}">開く</button></div></article>`).join(''):'<div class="dm-empty">在庫はありません。</div>'
    const bindLot=()=>{
      document.querySelectorAll('[data-correct]').forEach(b=>b.onclick=()=>correctLot(state.stock.lots.find(l=>l.id===Number(b.dataset.correct))))
      document.querySelectorAll('[data-inbound-correct]').forEach(b=>b.onclick=()=>correctInboundLot(state.stock.lots.find(l=>l.id===Number(b.dataset.inboundCorrect))))
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
  if(!prices||!prices.length)return ''
  if(prices.length===1&&!(prices[0].receipts&&prices[0].receipts.length>1))return ''
  return `<details class="price-breakdown"><summary>単価の内訳</summary><ul>${prices.map(p=>{
    const line=`${yen(p.unitCost)} × ${num(p.qty)}枚 ＝ ${yen(p.amount)}`
    const receipts=(p.receipts||[])
    const receiptHtml=receipts.length>1?`<ul class="receipt-breakdown">${receipts.map(r=>{
      const day=r.receivedUnknown?`入荷日不明・払出し順 ${r.payoutOrder}`:`入荷日 ${esc(r.receivedOn||'')}`
      const cond=r.conditionLabel||conditionLabel(r.condition)
      return `<li>${esc(cond)}／${day}：${num(r.qty)}枚（ロット ${r.lotId}）</li>`
    }).join('')}</ul>`:''
    return `<li>${line}${receiptHtml}</li>`
  }).join('')}</ul></details>`
}

function stockGroupHeading(g){
  return `<h2 class="dm-primary product-stock-title">${esc(g.title)}</h2>
    <p class="dm-secondary product-stock-total">${conditionQtyText(g)}／在庫金額${yen(g.amount)}</p>`
}

function productNameColor(r){
  return r.color?`${esc(r.productName)}／${esc(r.color)}`:esc(r.productName)
}

function renderProductStockList(groups){
  if(!groups.length)return ''
  return groups.map(g=>`<article class="product-stock-group">
    ${stockGroupHeading(g)}
    <div class="tablewrap"><table class="product-stock-table"><thead><tr><th>顧客</th><th>加工仕様</th><th>サイズ</th><th>保管場所</th><th>数量（新品／中古／合計）</th><th>在庫金額</th></tr></thead><tbody>
      ${(g.rows||[]).map(r=>`<tr>
        <td class="dm-primary">${esc(r.customerName)}</td>
        <td>${esc(r.embroideryLabel)}</td>
        <td>${esc(r.size)}</td>
        <td>${esc(r.locationLabel)}</td>
        <td class="qty">${conditionQtyText(r)}${priceBreakdownHtml(r.prices)}</td>
        <td class="money">${yen(r.amount)}</td>
      </tr>`).join('')||'<tr><td colspan="6">在庫はありません。</td></tr>'}
    </tbody></table></div>
  </article>`).join('')
}

function renderProductStockGrid(groups){
  if(!groups.length)return '<div class="dm-empty">在庫はありません。</div>'
  return groups.map(g=>`<div class="dm-group">${esc(g.title)}<span>${conditionQtyText(g)} / ${yen(g.amount)}</span></div>`+
    (g.rows||[]).map(r=>`<article class="dm-card">
      <div class="dm-primary">${esc(r.customerName)}</div>
      <div class="dm-secondary">${esc(r.embroideryLabel)} / ${esc(r.size)}</div>
      <div class="dm-meta"><span>${esc(r.locationLabel)}</span><span>${conditionQtyText(r)}</span><span>${yen(r.amount)}</span></div>
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
        <td class="qty">${conditionQtyText(r)}${priceBreakdownHtml(r.prices)}</td>
        <td class="money">${yen(r.amount)}</td>
      </tr>`
    }).join('')
    return `<article class="product-stock-group">
    ${stockGroupHeading(g)}
    <div class="tablewrap"><table class="product-stock-table"><thead><tr><th>品番</th><th>商品名／色</th><th>加工仕様</th><th>サイズ</th><th>保管場所</th><th>数量（新品／中古／合計）</th><th>在庫金額</th></tr></thead><tbody>
      ${rows||'<tr><td colspan="7">在庫はありません。</td></tr>'}
    </tbody></table></div>
  </article>`
  }).join('')
}

function renderCustomerStockGrid(groups){
  if(!groups.length)return '<div class="dm-empty">在庫はありません。</div>'
  return groups.map(g=>{
    let prevPid=null
    return `<div class="dm-group">${esc(g.title)}<span>${conditionQtyText(g)} / ${yen(g.amount)}</span></div>`+
      (g.rows||[]).map(r=>{
        const breakCls=prevPid!=null&&prevPid!==r.productId?' is-product-break':''
        prevPid=r.productId
        return `<article class="dm-card${breakCls}">
      <div class="dm-primary">${esc(r.code)} ${productNameColor(r)}</div>
      <div class="dm-secondary">${esc(r.embroideryLabel)} / ${esc(r.size)}</div>
      <div class="dm-meta"><span>${esc(r.locationLabel)}</span><span>${conditionQtyText(r)}</span><span>${yen(r.amount)}</span></div>
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
        <td class="qty">${conditionQtyText(r)}${priceBreakdownHtml(r.prices)}</td>
        <td class="money">${yen(r.amount)}</td>
      </tr>`
    }).join('')
    return `<article class="product-stock-group">
    ${stockGroupHeading(g)}
    <div class="tablewrap"><table class="product-stock-table"><thead><tr><th>棚・区画</th><th>顧客</th><th>品番</th><th>商品名／色</th><th>加工仕様</th><th>サイズ</th><th>数量（新品／中古／合計）</th><th>在庫金額</th></tr></thead><tbody>
      ${rows||'<tr><td colspan="8">在庫はありません。</td></tr>'}
    </tbody></table></div>
  </article>`
  }).join('')
}

function renderLocationStockGrid(groups){
  if(!groups.length)return '<div class="dm-empty">在庫はありません。</div>'
  return groups.map(g=>{
    let prevLoc=null
    return `<div class="dm-group">${esc(g.title)}<span>${conditionQtyText(g)} / ${yen(g.amount)}</span></div>`+
      (g.rows||[]).map(r=>{
        const breakCls=prevLoc!=null&&prevLoc!==r.locationId?' is-shelf-break':''
        prevLoc=r.locationId
        return `<article class="dm-card${breakCls}">
      <div class="dm-primary">${esc(r.shelfLabel)} / ${esc(r.customerName)}</div>
      <div class="dm-secondary">${esc(r.code)} ${productNameColor(r)}</div>
      <div class="dm-meta"><span>${esc(r.embroideryLabel)}</span><span>${esc(r.size)}</span><span>${conditionQtyText(r)}</span><span>${yen(r.amount)}</span></div>
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
function correctInboundLot(lot){
  if(!stockCanWrite()){toast('閲覧専用のため、訂正できません。');return}
  const dlg=$('#dialog')
  dlg.innerHTML=`<form><div class="dialoghead"><h2>入庫の訂正</h2></div><div class="dialogbody">
    <p>日次入庫の訂正です。初期在庫の訂正とは別です。</p>
    <p>${esc(lot.customerName)} / ${esc(lot.code)} ${esc(lot.productName)} / ${esc(lot.size)} / ${esc(lot.embroideryLabel)} / ${esc(lot.locationLabel)}</p>
    <p>変更前: ${num(lot.qty)} × ${yen(lot.unitCost)} ＝ ${yen(lot.amount)}</p>
    <div class="grid"><label>訂正後の数量（必須）<input id="qty" inputmode="numeric" value="${lot.qty}"></label><label>訂正後の仕入単価（必須）<input id="unitCost" inputmode="numeric" value="${lot.unitCost}"></label></div>
    <p id="preview"></p><label>訂正の理由（必須）<input id="reason"></label><p id="error"></p></div>
    <div class="dialogactions"><button type="button" id="close">戻る</button><button type="button" class="primary" id="save">確認して保存</button></div></form>`
  dlg.showModal()
  dlg.querySelector('form').onsubmit=ev=>ev.preventDefault()
  const preview=()=>{
    const q=$('#qty').value.trim(),c=$('#unitCost').value.trim()
    if(!/^\d+$/.test(q)||!/^\d+$/.test(c)){$('#preview').textContent='数量と仕入単価を入力すると、訂正後の金額を表示します。';return null}
    const amount=Number(q)*Number(c)
    $('#preview').textContent=`変更後: ${num(q)} × ${yen(c)} ＝ ${yen(amount)}`
    return {qty:Number(q),unitCost:Number(c),amount}
  }
  let armed=false
  const arm=()=>{armed=false;$('#save').textContent='確認して保存'}
  $('#qty').oninput=()=>{arm();preview()};$('#unitCost').oninput=()=>{arm();preview()};preview()
  $('#close').onclick=()=>{dlg.close();dlg.innerHTML=''}
  $('#save').onclick=async()=>{
    const next=preview()
    if(!next){$('#error').textContent='数量と仕入単価を入力してください。';return}
    if(!$('#reason').value.trim()){$('#error').textContent='訂正の理由を入力してください。';return}
    if(!armed){armed=true;$('#save').textContent='この内容で訂正';$('#error').textContent=`変更前 ${yen(lot.amount)}、変更後 ${yen(next.amount)}。よければもう一度押してください。`;return}
    const button=$('#save');button.disabled=true
    try{
      await api('/api/inbound/correct',{lotId:lot.id,qty:next.qty,unitCost:next.unitCost,reason:$('#reason').value.trim()})
      dlg.close();dlg.innerHTML='';toast('入庫を訂正しました。');await loadStock();paint()
    }catch(e){$('#error').textContent=e.message;button.disabled=false;armed=false;button.textContent='確認して保存'}
  }
}
async function showHistory(lotId){
  const data=await api('/api/lots/history?lotId='+lotId)
  const names={opening:'初期在庫',opening_correction:'初期在庫の訂正',inbound:'入庫',outbound:'出庫',inbound_correction:'入庫の訂正'}
  const dlg=$('#dialog')
  dlg.innerHTML=`<div class="dialoghead"><h2>在庫の履歴</h2><button type="button" id="close" class="quiet">閉じる</button></div><div class="dialogbody"><ul>${data.events.map(e=>{
    const who=e.actorName?`　${esc(e.actorName)}` : ''
    return `<li>${esc(e.at)}${who}　${esc(names[e.kind]||e.kind)}　数量 ${e.qtyBefore??'-'}→${e.qtyAfter??'-'}　単価 ${e.costBefore??'-'}→${e.costAfter??'-'}　金額 ${e.amountBefore??'-'}→${e.amountAfter??'-'}<span class="sub">${esc(e.note)}</span></li>`
  }).join('')}</ul></div>`
  dlg.showModal();$('#close').onclick=()=>{dlg.close();dlg.innerHTML=''}
}
function historyQueryParams(){
  ensureHistoryDefaults()
  const h=state.history
  const q=new URLSearchParams({from:h.from,to:h.to,page:String(h.page||1),pageSize:String(h.pageSize||50)})
  if(h.customerId)q.set('customerId',h.customerId)
  if(h.productId)q.set('productId',h.productId)
  if(h.kind)q.set('kind',h.kind)
  if(h.condition)q.set('condition',h.condition)
  if(h.locationId)q.set('locationId',h.locationId)
  return q
}
async function loadStockHistory(){
  ensureHistoryDefaults()
  const h=state.history
  if(!h.from||!h.to){toast('開始日と終了日を入力してください。');return}
  if(h.from>h.to){toast('開始日が終了日より後になっています。');return}
  state.historyResult=await api('/api/stock-history?'+historyQueryParams().toString())
}
function clearHistoryFilters(){
  state.history={from:monthStartISO(),to:todayISO(),customerId:'',productId:'',kind:'',condition:'',locationId:'',page:1,pageSize:state.history.pageSize||50}
  state.historyResult=null
  paint()
}
function filterHistoryCustomers(query){
  const needle=String(query||'').trim().toLowerCase()
  const list=(state.data.customers||[]).slice()
  if(!needle)return list
  return list.filter(c=>{
    const name=String(c.name||'').toLowerCase()
    const kana=String(c.kana||'').toLowerCase()
    return name.includes(needle)||kana.includes(needle)
  })
}
function filterHistoryProducts(query){
  const needle=String(query||'').trim().toLowerCase()
  const list=(state.data.products||[]).slice()
  if(!needle)return list
  return list.filter(p=>{
    const hay=`${p.code||''} ${p.name||''} ${p.color||''} ${p.supplierName||''}`.toLowerCase()
    return hay.includes(needle)
  })
}
function historyProductLabel(p){
  if(!p)return ''
  return `${p.code} ${p.name} ${p.color||''}（ID ${p.id}）`.replace(/\s+/g,' ').trim()
}
function historyDeltaText(e){
  const d=Number(e.qtyDelta)
  if(!Number.isFinite(d))return '—'
  if(d>0)return '+'+num(d)
  return num(d)
}
function historyLotQtyText(e){
  const before=e.qtyBefore==null?'—':num(e.qtyBefore)
  const after=e.qtyAfter==null?'—':num(e.qtyAfter)
  return `${before}→${after}`
}
function historyDetailHtml(e){
  const batch=e.batchLineCount>1
    ?`<p>同一出庫操作（先入先出）のロット内訳 ${e.batchLineIndex||'?'}／${e.batchLineCount}（バッチ ${e.moveBatchId}）</p>
      <ul>${(e.batchPeers||[]).map(p=>`<li${p.isSelf?' class="is-self"':''}>ロット${p.lotId} ${esc(p.size)}／${esc(p.conditionLabel)}　増減 ${num(p.qtyDelta)}　ロット残 ${p.qtyBefore??'—'}→${p.qtyAfter??'—'}　単価 ${yen(p.unitCost)}${p.isSelf?'（この行）':''}</li>`).join('')}</ul>`
    :''
  return `<div class="history-detail">
    <p>単価 ${e.costBefore==null?'—':yen(e.costBefore)}→${e.costAfter==null?'—':yen(e.costAfter)}　金額 ${e.amountBefore==null?'—':yen(e.amountBefore)}→${e.amountAfter==null?'—':yen(e.amountAfter)}</p>
    <p>対象ロットID ${e.lotId}${e.lotQtyRemaining!=null?`（現在残 ${num(e.lotQtyRemaining)}）`:''}</p>
    ${batch}
  </div>`
}
function historyPage(){
  ensureHistoryDefaults()
  const h=state.history
  const result=state.historyResult
  const opt=(value,label,current)=>`<option value="${esc(value)}" ${String(current)===String(value)?'selected':''}>${esc(label)}</option>`
  const kindOpts=[['','すべて'],['opening','初期在庫'],['inbound','入庫'],['outbound','出庫'],['correction','訂正'],['opening_correction','初期在庫の訂正'],['inbound_correction','入庫の訂正']]
  const cust=h.customerId?(state.data.customers||[]).find(c=>String(c.id)===String(h.customerId)):null
  const prod=h.productId?(state.data.products||[]).find(p=>String(p.id)===String(h.productId)):null
  const total=result?result.total:null
  const page=result?result.page:h.page
  const pages=result?result.totalPages:0
  $('#main').innerHTML=`<section class="panel history-panel">
    <p class="notice">期間は<strong>記録日時（日本時間）</strong>を基準にします。終了日の一日分も含みます。変更前・変更後の数量は<strong>対象ロットの残数</strong>です（商品全体の残数ではありません）。顧客名・商品名は現在の台帳名称です。</p>
    <div class="bar period-filters history-filters">
      <label>開始日（記録日時）<input id="hFrom" type="date" value="${esc(h.from)}"></label>
      <label>終了日（記録日時）<input id="hTo" type="date" value="${esc(h.to)}"></label>
      <button type="button" id="hThisMonth">今月</button>
      <button type="button" id="hPrevMonth">先月</button>
      ${searchPickerMarkup({label:'顧客',inputId:'hCustomerQ',listId:'hCustomerList',clearId:'hCustomerClear',hintId:'hCustomerHint',placeholder:'顧客名で絞り込み',value:cust?cust.name:'',required:false})}
      ${searchPickerMarkup({label:'商品',inputId:'hProductQ',listId:'hProductList',clearId:'hProductClear',hintId:'hProductHint',placeholder:'品番・商品名で絞り込み',value:prod?historyProductLabel(prod):'',required:false})}
      <label>操作内容<select id="hKind">${kindOpts.map(([v,l])=>opt(v,l,h.kind||'')).join('')}</select></label>
      <label>状態${conditionSelectHtml('hCondition',h.condition||'',{allowAll:true})}</label>
      <label>保管場所<select id="hLocation"><option value="">すべて</option>${(state.data.locations||[]).map(l=>opt(l.id,l.label,h.locationId)).join('')}</select></label>
      <button type="button" class="primary" id="hRun">表示する</button>
      <button type="button" id="hClear">条件を解除</button>
    </div>
    <div class="bar">
      <span class="count">${result?`${num(total)}件中 ${total?num((page-1)*result.pageSize+1):0}–${num(Math.min(page*result.pageSize,total))}件を表示（${esc(result.sortLabel||'記録日時の新しい順')}・1ページ ${result.pageSize}件）`:'条件を選んで「表示する」を押してください。'}</span>
      <button type="button" id="hPrev" ${result&&page>1?'':'disabled'}>前へ</button>
      <button type="button" id="hNext" ${result&&page<pages?'':'disabled'}>次へ</button>
      <span class="count">${result&&pages?`ページ ${page} / ${pages}`:''}</span>
    </div>
    ${listNote('履歴の見方','ロット単位の既存記録を新しい順に表示します。在庫0のロットや使用停止の顧客・商品の履歴も残します。同一出庫が複数ロットに分かれる場合は詳細でロット内訳を確認できます。履歴の閲覧では在庫データを変更しません。')}
    <div class="list-stage" id="stockliststage"><div id="historybody" class="tablewrap period-tablewrap"></div><div class="list-grid" id="stocklistgrid" hidden></div></div>
  </section>`
  const stage=$('#stockliststage')
  mountStockDisplay(stage,'history',()=>paint())
  if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,stockScreenId('history'))
  const syncFields=()=>{
    h.from=$('#hFrom').value;h.to=$('#hTo').value
    h.kind=$('#hKind').value;h.condition=$('#hCondition')?$('#hCondition').value:''
    h.locationId=$('#hLocation').value
  }
  const custInput=$('#hCustomerQ')
  const prodInput=$('#hProductQ')
  bindOpeningSearchPicker({
    input:custInput,list:$('#hCustomerList'),clearBtn:$('#hCustomerClear'),hint:$('#hCustomerHint'),
    getItems:()=>filterHistoryCustomers(custInput.value).slice(0,40).map(c=>({
      id:c.id,label:c.name,
      html:`<span class="dm-primary">${esc(c.name)}</span>${c.active?'':'<span class="badge stop">停止</span>'}${c.kana?`<span class="sub dm-secondary">${esc(c.kana)}</span>`:''}`,
    })),
    emptyText:'該当する顧客はありません。',
    isConfirmed:()=>!!h.customerId,
    confirmedLabel:()=>{
      const c=(state.data.customers||[]).find(x=>String(x.id)===String(h.customerId))
      return c?c.name:''
    },
    onPick:item=>{h.customerId=String(item.id);h.page=1;custInput.value=item.label},
    onClear:()=>{h.customerId='';h.page=1},
    onQueryChange:v=>{if(h.customerId){h.customerId='';h.page=1}return false},
  })
  bindOpeningSearchPicker({
    input:prodInput,list:$('#hProductList'),clearBtn:$('#hProductClear'),hint:$('#hProductHint'),
    getItems:()=>filterHistoryProducts(prodInput.value).slice(0,40).map(p=>({
      id:p.id,label:historyProductLabel(p),
      html:`<span class="dm-primary">${esc(historyProductLabel(p))}</span>${p.active?'':'<span class="badge stop">停止</span>'}`,
    })),
    emptyText:'該当する商品はありません。',
    isConfirmed:()=>!!h.productId,
    confirmedLabel:()=>{
      const p=(state.data.products||[]).find(x=>String(x.id)===String(h.productId))
      return p?historyProductLabel(p):''
    },
    onPick:item=>{h.productId=String(item.id);h.page=1;prodInput.value=item.label},
    onClear:()=>{h.productId='';h.page=1},
    onQueryChange:v=>{if(h.productId){h.productId='';h.page=1}return false},
  })
  $('#hThisMonth').onclick=()=>{h.from=monthStartISO();h.to=todayISO();h.page=1;paint()}
  $('#hPrevMonth').onclick=()=>{const b=prevMonthBounds();h.from=b.from;h.to=b.to;h.page=1;paint()}
  $('#hRun').onclick=async()=>{syncFields();h.page=1;try{await loadStockHistory();paint()}catch(e){toast(e.message)}}
  $('#hClear').onclick=()=>clearHistoryFilters()
  $('#hPrev').onclick=async()=>{if(!result||page<=1)return;syncFields();h.page=page-1;try{await loadStockHistory();paint()}catch(e){toast(e.message)}}
  $('#hNext').onclick=async()=>{if(!result||page>=pages)return;syncFields();h.page=page+1;try{await loadStockHistory();paint()}catch(e){toast(e.message)}}
  const body=$('#historybody')
  if(!result){body.innerHTML='<p class="dialogbody">条件を選んで「表示する」を押してください。</p>';return}
  const events=result.events||[]
  body.innerHTML=`<table class="period-table history-table"><thead><tr>
    <th>記録日時</th><th>業務日</th><th>操作内容</th><th>顧客</th><th>品番・商品名・色</th><th>加工仕様</th><th>状態</th><th>サイズ・保管場所</th><th>増減数量</th><th>ロット数量（前→後）</th><th>担当者</th><th>備考</th><th></th>
  </tr></thead><tbody>
    ${events.map((e,i)=>{
      const batchMark=e.batchLineCount>1?`<span class="sub">同一操作 ${e.batchLineIndex}/${e.batchLineCount}</span>`:''
      const stopCust=e.customerActive===false?'<span class="badge stop">停止</span>':''
      const stopProd=e.productActive===false?'<span class="badge stop">停止</span>':''
      return `<tr>
        <td>${esc(e.at)}</td>
        <td>${esc(e.businessOnLabel||'未記録')}</td>
        <td>${esc(e.kindLabel)}${batchMark}</td>
        <td class="dm-primary">${esc(e.customerName)}${stopCust}</td>
        <td>${esc(e.code)} ${esc(e.productName)} ${esc(e.color)}${stopProd}<span class="sub dm-secondary">商品ID ${e.productId}</span></td>
        <td>${esc(e.embroideryLabel)}</td>
        <td>${esc(e.conditionLabel||conditionLabel(e.condition))}</td>
        <td>${esc(e.size)} / ${esc(e.locationLabel)}</td>
        <td class="qty">${historyDeltaText(e)}</td>
        <td class="qty">${historyLotQtyText(e)}<span class="sub">対象ロット</span></td>
        <td>${esc(e.actorNameLabel||'未記録')}</td>
        <td>${esc(e.note||'')}</td>
        <td><button type="button" class="quiet" data-hist-detail="${i}">詳細</button></td>
      </tr>`
    }).join('')||'<tr><td colspan="13">該当する履歴はありません。</td></tr>'}
  </tbody></table>`
  body.querySelectorAll('[data-hist-detail]').forEach(b=>b.onclick=()=>{
    const e=events[Number(b.dataset.histDetail)]
    if(!e)return
    const dlg=$('#dialog')
    dlg.innerHTML=`<div class="dialoghead"><h2>履歴の詳細</h2><button type="button" id="close" class="quiet">閉じる</button></div><div class="dialogbody">
      <p>${esc(e.at)}　${esc(e.kindLabel)}　${esc(e.customerName)} / ${esc(e.code)} ${esc(e.productName)}</p>
      ${historyDetailHtml(e)}
      <p class="sub">${esc(result.qtyScopeNote||'')}</p>
      <p class="sub">${esc(result.namesNote||'')}</p>
    </div>`
    dlg.showModal();$('#close').onclick=()=>{dlg.close();dlg.innerHTML=''}
  })
}
async function loadMoveTargets(){
  const q=new URLSearchParams({mode:state.moveMode==='out'?'out':'in'})
  if(state.moveQ)q.set('q',state.moveQ)
  state.moveTargets=await api('/api/move/targets?'+q.toString())
}
function periodQueryParams(){
  ensurePeriodDefaults()
  const p=state.period
  const q=new URLSearchParams({from:p.from,to:p.to,group:p.group||'detail'})
  if(p.customerId)q.set('customerId',p.customerId)
  if(p.productId)q.set('productId',p.productId)
  if(p.size)q.set('size',p.size)
  if(p.spec)q.set('spec',p.spec)
  if(p.locationId)q.set('locationId',p.locationId)
  if(p.condition)q.set('condition',p.condition)
  return q
}
async function runPeriodSummary(){
  const p=state.period
  if(!p.from||!p.to){toast('開始日と終了日を入力してください。');return}
  if(p.from>p.to){toast('開始日が終了日より後になっています。');return}
  state.periodResult=await api('/api/period-summary?'+periodQueryParams().toString())
  paint()
}
function clearPeriodFilters(){
  state.period={from:monthStartISO(),to:todayISO(),customerId:'',productId:'',size:'',spec:'',locationId:'',condition:'',group:state.period.group||'detail'}
  state.periodResult=null
  paint()
}
function periodPage(){
  ensurePeriodDefaults()
  const p=state.period
  const sizes=[...new Set((state.data.products||[]).flatMap(x=>x.sizes||[]))]
  const specs=new Map()
  ;(state.data.products||[]).forEach(prod=>(prod.links||[]).forEach(l=>specs.set(l.embroidery||'none',specLabel(l.embroidery))))
  const opt=(value,label,current)=>`<option value="${esc(value)}" ${String(current)===String(value)?'selected':''}>${esc(label)}</option>`
  const result=state.periodResult
  const totals=result&&result.totals
  $('#main').innerHTML=`<section class="panel period-panel">
    <div class="bar period-filters">
      <label>開始日<input id="pFrom" type="date" value="${esc(p.from)}"></label>
      <label>終了日<input id="pTo" type="date" value="${esc(p.to)}"></label>
      <button type="button" id="pThisMonth">今月</button>
      <button type="button" id="pPrevMonth">先月</button>
      <label>顧客<select id="pCustomer"><option value="">すべて</option>${(state.data.customers||[]).map(c=>opt(c.id,c.name,p.customerId)).join('')}</select></label>
      <label>商品<select id="pProduct"><option value="">すべて</option>${(state.data.products||[]).map(prod=>opt(prod.id,`${prod.code} ${prod.name} ${prod.color}（ID ${prod.id}）`,p.productId)).join('')}</select></label>
      <label>サイズ<select id="pSize"><option value="">すべて</option>${sizes.map(s=>opt(s,s,p.size)).join('')}</select></label>
      <label>加工仕様<select id="pSpec"><option value="">すべて</option>${[...specs].map(([k,v])=>opt(k,v,p.spec)).join('')}</select></label>
      <label>保管場所<select id="pLocation"><option value="">すべて</option>${(state.data.locations||[]).map(l=>opt(l.id,l.label,p.locationId)).join('')}</select></label>
      <label>状態${conditionSelectHtml('pCondition',p.condition||'',{allowAll:true})}</label>
      <button type="button" class="primary" id="pRun">集計する</button>
      <button type="button" id="pClear">条件をクリア</button>
    </div>
    <div class="tabs" id="periodGroups">${[['detail','明細'],['product','商品別'],['customer','顧客別'],['location','場所別']].map(([id,label])=>`<button type="button" data-group="${id}" class="${(p.group||'detail')===id?'active':''}">${label}</button>`).join('')}</div>
    <div class="total period-totals">
      <div>期間内入庫数<strong>${totals?num(totals.inboundQty):'—'}</strong></div>
      <div>期間内出庫数<strong>${totals?num(totals.outboundQty):'—'}</strong></div>
      <div>訂正増減数<strong>${totals?num(totals.correctionQty):'—'}</strong></div>
      <div>入庫金額（仕入原価）<strong>${totals?yen(totals.inboundAmount):'—'}</strong></div>
      <div>出庫金額（仕入原価）<strong>${totals?yen(totals.outboundAmount):'—'}</strong></div>
    </div>
    <div class="bar">
      <button type="button" id="pCsv" ${result?'':'disabled'}>CSV</button>
      <button type="button" id="pXlsx" ${result?'':'disabled'}>Excel</button>
      <span class="count">${result?`${(result.rows||[]).length}行（算出可 ${totals.computableRows} / 算出不可 ${totals.unavailableRows}）`:''}</span>
    </div>
    ${listNote('集計の見方','業務日（入荷日・出庫日・在庫基準日）で集計します。登録日時は使いません。金額は仕入原価です。期首＋入庫−出庫＋訂正増減＝期末。業務日がない履歴がある行は算出不可とし、0で埋めません。初期在庫は期間内入庫に数えません。')}
    <div class="list-stage" id="stockliststage"><div id="periodbody" class="tablewrap period-tablewrap"></div><div class="list-grid" id="stocklistgrid" hidden></div></div>
  </section>`
  const stage=$('#stockliststage')
  mountStockDisplay(stage,'period',()=>paint())
  if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,stockScreenId('period'))
  const syncFields=()=>{
    p.from=$('#pFrom').value;p.to=$('#pTo').value
    p.customerId=$('#pCustomer').value;p.productId=$('#pProduct').value
    p.size=$('#pSize').value;p.spec=$('#pSpec').value;p.locationId=$('#pLocation').value
    p.condition=$('#pCondition')?$('#pCondition').value:''
  }
  $('#pThisMonth').onclick=()=>{p.from=monthStartISO();p.to=todayISO();paint()}
  $('#pPrevMonth').onclick=()=>{const b=prevMonthBounds();p.from=b.from;p.to=b.to;paint()}
  $('#pRun').onclick=async()=>{syncFields();try{await runPeriodSummary()}catch(e){toast(e.message)}}
  $('#pClear').onclick=()=>clearPeriodFilters()
  $('#periodGroups').querySelectorAll('button').forEach(b=>b.onclick=async()=>{
    syncFields();p.group=b.dataset.group
    if(state.periodResult){try{await runPeriodSummary()}catch(e){toast(e.message)}}
    else paint()
  })
  const dl=async(path,filename)=>{
    syncFields()
    const r=await fetch(BASE+path+'?'+periodQueryParams().toString())
    if(!r.ok){const j=await r.json().catch(()=>({}));throw Error(j.error||'書き出せませんでした。')}
    const blob=await r.blob()
    const a=document.createElement('a')
    a.href=URL.createObjectURL(blob);a.download=filename;a.click()
    setTimeout(()=>URL.revokeObjectURL(a.href),1000)
  }
  $('#pCsv').onclick=async()=>{try{await dl('/api/period-summary/export.csv','期間入出荷集計.csv')}catch(e){toast(e.message)}}
  $('#pXlsx').onclick=async()=>{try{await dl('/api/period-summary/export.xlsx','期間入出荷集計.xlsx')}catch(e){toast(e.message)}}
  const body=$('#periodbody')
  if(!result){body.innerHTML='<p class="dialogbody">条件を選んで「集計する」を押してください。</p>';return}
  if(p.group==='detail'){
    body.innerHTML=`<table class="period-table"><thead><tr>
      <th>顧客</th><th>品番・商品名・色</th><th>加工仕様</th><th>サイズ</th><th>状態</th><th>保管場所</th>
      <th>期首在庫数</th><th>期間内入庫数</th><th>期間内出庫数</th><th>訂正増減数</th><th>期末在庫数</th>
      <th>期間内入庫金額（仕入原価）</th><th>期間内出庫金額（仕入原価）</th><th></th>
    </tr></thead><tbody>
      ${(result.rows||[]).map((row,i)=>{
        if(!row.computable){
          return `<tr class="period-unavailable"><td>${esc(row.customerName)}</td><td>${esc(row.code)} ${esc(row.productName)} ${esc(row.color)}<span class="sub">商品ID ${row.productId}</span></td><td>${esc(row.embroideryLabel)}</td><td>${esc(row.size)}</td><td>${esc(row.conditionLabel||conditionLabel(row.condition))}</td><td>${esc(row.locationLabel)}</td><td colspan="7">算出不可：${esc(row.unavailableReason||'')}</td><td></td></tr>`
        }
        const prices=(row.priceBreakdown||[]).length>1?`<details class="price-breakdown"><summary>出庫単価内訳</summary><ul>${row.priceBreakdown.map(x=>`<li>${yen(x.unitCost)} × ${num(x.qty)}枚 ＝ ${yen(x.amount)}</li>`).join('')}</ul></details>`:''
        return `<tr>
          <td>${esc(row.customerName)}</td>
          <td>${esc(row.code)} ${esc(row.productName)} ${esc(row.color)}<span class="sub">商品ID ${row.productId}</span></td>
          <td>${esc(row.embroideryLabel)}</td><td>${esc(row.size)}</td><td>${esc(row.conditionLabel||conditionLabel(row.condition))}</td><td>${esc(row.locationLabel)}</td>
          <td class="qty">${num(row.openingQty)}</td><td class="qty">${num(row.inboundQty)}</td><td class="qty">${num(row.outboundQty)}</td>
          <td class="qty">${num(row.correctionQty)}</td><td class="qty">${num(row.closingQty)}</td>
          <td class="money">${yen(row.inboundAmount)}</td><td class="money">${yen(row.outboundAmount)}${prices}</td>
          <td><button type="button" class="quiet" data-period-hist="${i}">内訳</button></td>
        </tr>`
      }).join('')||'<tr><td colspan="14">該当する行がありません。</td></tr>'}
    </tbody></table>`
    body.querySelectorAll('[data-period-hist]').forEach(b=>b.onclick=()=>showPeriodHistory(result.rows[Number(b.dataset.periodHist)]))
  }else{
    body.innerHTML=(result.groups||[]).map(g=>`<article class="product-stock-group">
      <h2 class="dm-primary product-stock-title">${esc(g.title)}</h2>
      <p class="dm-secondary product-stock-total">入庫${num(g.inboundQty)} / 出庫${num(g.outboundQty)} / 訂正${num(g.correctionQty)}　入庫金額${yen(g.inboundAmount)} / 出庫金額${yen(g.outboundAmount)}</p>
      <div class="tablewrap"><table class="period-table"><thead><tr>
        <th>顧客</th><th>品番・商品名・色</th><th>加工仕様</th><th>サイズ</th><th>状態</th><th>保管場所</th>
        <th>期首</th><th>入庫</th><th>出庫</th><th>訂正</th><th>期末</th><th>入庫金額</th><th>出庫金額</th><th></th>
      </tr></thead><tbody>
        ${(g.rows||[]).map((row,i)=>{
          if(!row.computable)return `<tr><td colspan="14">算出不可：${esc(row.customerName)} / ${esc(row.code)} … ${esc(row.unavailableReason||'')}</td></tr>`
          return `<tr>
            <td>${esc(row.customerName)}</td><td>${esc(row.code)} ${esc(row.productName)} ${esc(row.color)}</td>
            <td>${esc(row.embroideryLabel)}</td><td>${esc(row.size)}</td><td>${esc(row.conditionLabel||conditionLabel(row.condition))}</td><td>${esc(row.locationLabel)}</td>
            <td class="qty">${num(row.openingQty)}</td><td class="qty">${num(row.inboundQty)}</td><td class="qty">${num(row.outboundQty)}</td>
            <td class="qty">${num(row.correctionQty)}</td><td class="qty">${num(row.closingQty)}</td>
            <td class="money">${yen(row.inboundAmount)}</td><td class="money">${yen(row.outboundAmount)}</td>
            <td><button type="button" class="quiet" data-cp="${row.customerProductId}" data-size="${esc(row.size)}" data-loc="${row.locationId}" data-condition="${esc(row.condition||'new')}">内訳</button></td>
          </tr>`
        }).join('')}
      </tbody></table></div>
    </article>`).join('')||'<p class="dialogbody">該当する行がありません。</p>'
    body.querySelectorAll('[data-cp]').forEach(b=>b.onclick=()=>{
      const row=(result.rows||[]).find(r=>String(r.customerProductId)===b.dataset.cp&&r.size===b.dataset.size&&String(r.locationId)===b.dataset.loc&&String(r.condition||'new')===String(b.dataset.condition||'new'))
      if(row)showPeriodHistory(row)
    })
  }
}
function showPeriodHistory(row){
  const dlg=$('#dialog')
  if(!row.computable){
    dlg.innerHTML=`<div class="dialoghead"><h2>内訳</h2><button type="button" id="close" class="quiet">閉じる</button></div><div class="dialogbody"><p>算出不可：${esc(row.unavailableReason||'')}</p></div>`
    dlg.showModal();$('#close').onclick=()=>{dlg.close();dlg.innerHTML=''}
    return
  }
  const prices=(row.priceBreakdown||[]).length?`<h3>出庫単価内訳（仕入原価）</h3><ul>${row.priceBreakdown.map(p=>`<li>${yen(p.unitCost)} × ${num(p.qty)}枚 ＝ ${yen(p.amount)}</li>`).join('')}</ul>`:''
  dlg.innerHTML=`<div class="dialoghead"><h2>期間内の内訳</h2><button type="button" id="close" class="quiet">閉じる</button></div>
    <div class="dialogbody">
      <p>${esc(row.customerName)} / ${esc(row.code)} ${esc(row.productName)} / ${esc(row.size)} / ${esc(row.embroideryLabel)} / ${esc(row.conditionLabel||conditionLabel(row.condition))} / ${esc(row.locationLabel)}</p>
      <p>期首 ${num(row.openingQty)} ＋ 入庫 ${num(row.inboundQty)} − 出庫 ${num(row.outboundQty)} ＋ 訂正 ${num(row.correctionQty)} ＝ 期末 ${num(row.closingQty)}</p>
      ${prices}
      <div class="tablewrap"><table><thead><tr><th>業務日</th><th>登録日時</th><th>区分</th><th>数量</th><th>単価</th><th>金額</th><th>担当者</th><th>備考</th></tr></thead><tbody>
        ${(row.history||[]).map(e=>`<tr>
          <td>${esc(e.businessOn||'')}</td><td>${esc(e.at||'')}</td><td>${esc(e.kindLabel||e.kind)}</td>
          <td class="qty">${num(e.qty)}</td><td class="money">${yen(e.unitCost)}</td><td class="money">${yen(e.amount)}</td>
          <td>${esc(e.actorName||'')}</td><td>${esc(e.note||'')}</td>
        </tr>`).join('')||'<tr><td colspan="8">期間内の明細はありません。</td></tr>'}
      </tbody></table></div>
    </div>`
  dlg.showModal();$('#close').onclick=()=>{dlg.close();dlg.innerHTML=''}
}
function movePage(){
  const SM=typeof StockMoves!=='undefined'?StockMoves:null
  const writable=stockCanWrite()
  const mode=state.moveMode==='out'?'out':'in'
  const targets=(state.moveTargets&&state.moveTargets.targets)||[]
  const filtered=SM?SM.filterTargets(targets,state.moveQ):targets
  $('#main').innerHTML=`<section class="panel move-panel">
    <div class="move-mode-toggle" role="group" aria-label="入庫または出庫">
      <button type="button" data-move-mode="in" class="${mode==='in'?'active primary':''}">入庫する</button>
      <button type="button" data-move-mode="out" class="${mode==='out'?'active primary':''}">出庫する</button>
    </div>
    ${writable?'':`<p class="notice">閲覧専用のため、入庫・出庫はできません。</p>`}
    <div class="bar">
      <input id="moveQ" type="search" placeholder="顧客・品番・商品・サイズ・場所で検索" value="${esc(state.moveQ||'')}">
      <span class="count">${filtered.length}件</span>
      ${mode==='in'&&writable?`<button type="button" class="primary" id="openInboundNew">一覧にない商品を入庫</button>`:''}
    </div>
    ${mode==='in'?`<p class="notice move-new-hint">登録済みの取扱から、初めて在庫を登録する組み合わせを選びます</p>`:''}
    ${listNote('入庫・出庫の見方',mode==='in'
      ?'一覧は、これまでに在庫登録した組み合わせ（状態ごと）です。現在庫が0枚でも残ります。補充は行の「選択」から入庫します。新品しか無い取扱でも、入庫画面で中古を選んで初回入庫できます。初めての組み合わせだけ「一覧にない商品を入庫」を使います。'
      :'出庫は現在庫がある組み合わせだけです。新品／中古を行で選び、選んだ状態の在庫だけを減らします。状態をまたぐ自動振替はしません。入荷日の古い順（先入先出）です。')}
    <div class="list-stage" id="stockliststage"><div class="tablewrap"><table class="move-target-table"><thead><tr><th>顧客</th><th>商品</th><th>加工仕様</th><th>サイズ</th><th>状態</th><th>保管場所</th><th>現在庫</th><th>金額</th><th></th></tr></thead><tbody>
      ${filtered.map(t=>`<tr class="${Number(t.qty)===0?'is-zero-stock':''}">
        <td class="dm-primary">${esc(t.customerName)}</td>
        <td>${esc(t.code)} ${esc(t.productName)} ${esc(t.color)}<span class="sub dm-secondary">商品ID ${t.productId}</span></td>
        <td>${esc(t.embroideryLabel)}</td>
        <td>${esc(t.size)}</td>
        <td>${esc(t.conditionLabel||conditionLabel(t.condition))}</td>
        <td>${esc(t.locationLabel)}</td>
        <td class="qty">${num(t.qty)}枚</td>
        <td class="money">${yen(t.amount)}</td>
        <td>${writable?`<button type="button" class="primary" data-select-target="${t.customerProductId}" data-size="${esc(t.size)}" data-location="${t.locationId}" data-condition="${esc(t.condition||'new')}">選択</button>`:'—'}</td>
      </tr>`).join('')||`<tr><td colspan="9">${mode==='in'?'在庫登録済みの組み合わせはありません。「一覧にない商品を入庫」から初めての組み合わせを登録できます。':'出庫できる在庫はありません。'}</td></tr>`}
    </tbody></table></div><div class="list-grid" id="stocklistgrid" hidden></div></div>
  </section>`
  const emptyMove=mode==='in'?'在庫登録済みの組み合わせはありません。「一覧にない商品を入庫」から初めての組み合わせを登録できます。':'出庫できる在庫はありません。'
  const grid=$('#stocklistgrid')
  if(grid)grid.innerHTML=filtered.length?filtered.map(t=>`<article class="dm-card${Number(t.qty)===0?' is-zero-stock':''}"><div class="dm-primary">${esc(t.customerName)}</div><div class="dm-secondary">${esc(t.code)} ${esc(t.productName)} ${esc(t.color)} · 商品ID ${t.productId}</div><div class="dm-meta"><span>${esc(t.embroideryLabel)}</span><span>${esc(t.size)}</span><span>${esc(t.conditionLabel||conditionLabel(t.condition))}</span><span>${esc(t.locationLabel)}</span></div><div class="dm-meta"><span class="qty">${num(t.qty)}枚</span><span class="money">${yen(t.amount)}</span></div>${writable?`<div class="dm-actions"><button type="button" class="primary" data-select-target="${t.customerProductId}" data-size="${esc(t.size)}" data-location="${t.locationId}" data-condition="${esc(t.condition||'new')}">選択</button></div>`:''}</article>`).join(''):`<div class="dm-empty">${emptyMove}</div>`
  const stage=$('#stockliststage')
  const displayMode=mountStockDisplay(stage,'move',()=>paint())
  if(grid)grid.hidden=displayMode!=='grid'
  if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,stockScreenId('move'))
  document.querySelectorAll('[data-move-mode]').forEach(b=>b.onclick=async()=>{
    state.moveMode=b.dataset.moveMode
    try{await loadMoveTargets()}catch(e){toast(e.message)}
    paint()
  })
  const qEl=$('#moveQ')
  let qTimer=null
  qEl.oninput=()=>{
    state.moveQ=qEl.value
    clearTimeout(qTimer)
    qTimer=setTimeout(async()=>{await loadMoveTargets();paint()},200)
  }
  const openNew=$('#openInboundNew')
  if(openNew)openNew.onclick=()=>openInboundDialog(null)
  document.querySelectorAll('[data-select-target]').forEach(b=>b.onclick=()=>{
    const target=targets.find(t=>String(t.customerProductId)===b.dataset.selectTarget&&t.size===b.dataset.size&&String(t.locationId)===b.dataset.location&&String(t.condition||'new')===String(b.dataset.condition||'new'))
    if(!target)return
    if(mode==='in')openInboundDialog(target)
    else openOutboundDialog(target)
  })
}
function moveDialogEl(){return $('#dialog')}
function openInboundDialog(target){
  if(!stockCanWrite()){toast('閲覧専用のため、入庫できません。');return}
  const SM=StockMoves
  const dlg=moveDialogEl()
  const allTargets=(state.moveTargets&&state.moveTargets.targets)||[]
  const draft={
    customerProductId:target?target.customerProductId:null,
    customerId:target?target.customerId:null,
    handleId:target?String(target.customerProductId):'',
    size:target?target.size:'',
    locationId:target?target.locationId:null,
    condition:target?(target.condition||'new'):'new',
    siteId:'',shelfId:'',
    qty:1,
    unitCost:'',
    receivedOn:new Date().toISOString().slice(0,10),
    note:'',
    currentQty:target?target.qty:0,
    customerQuery:'',handleQuery:'',
  }
  state.moveDraft=draft
  state.moveDirty=false
  const handles=openingHandlesFromData(state.data)
  const moveLocationFields=(sel)=>openingLocationFields(sel)
    .replace(/id="openingSite"/g,'id="moveSite"')
    .replace(/id="openingShelf"/g,'id="moveShelf"')
    .replace(/id="openingShelfLabel"/g,'id="moveShelfLabel"')
  const render=()=>{
    const offering=draft.handleId
      ?resolveConfirmedOpeningOffering(handles,draft.customerId,draft.handleId)
      :(draft.customerProductId?handles.find(h=>Number(h.id)===Number(draft.customerProductId)):null)
    if(offering){
      draft.customerProductId=offering.id
      draft.customerId=offering.customerId
      draft.handleId=String(offering.id)
    }
    const customer=resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId)
    const sizes=offering?(state.data.products.find(p=>p.id===offering.productId)||{sizes:[]}).sizes:[]
    const keep=state.opening
    state.opening={siteId:draft.siteId,shelfId:draft.shelfId,locationId:draft.locationId}
    const locSel=openingLocationSelection()
    state.opening=keep
    draft.siteId=locSel.siteId==null?'':String(locSel.siteId)
    draft.shelfId=locSel.shelfId
    if(!target)draft.locationId=locSel.locationId
    const syncCurrentFromTargets=()=>{
      const hit=allTargets.find(t=>Number(t.customerProductId)===Number(draft.customerProductId)&&t.size===draft.size&&Number(t.locationId)===Number(draft.locationId)&&String(t.condition||'new')===String(draft.condition||'new'))
      draft.currentQty=hit?hit.qty:0
    }
    if(target||(draft.customerProductId&&draft.size&&draft.locationId))syncCurrentFromTargets()
    const current=draft.currentQty||0
    const qty=Number(draft.qty)||0
    dlg.innerHTML=`<div class="dialoghead"><h2>入庫する</h2><button type="button" id="closeMove" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        ${target?`<p>${esc(target.customerName)} / ${esc(target.code)} ${esc(target.productName)} / ${esc(target.size)} / ${esc(target.embroideryLabel)} / ${esc(target.locationLabel)}</p>`:
          `<div class="grid">
            ${searchPickerMarkup({label:'顧客',inputId:'moveCustomer',listId:'moveCustomerList',clearId:'moveCustomerClear',hintId:'moveCustomerHint',placeholder:'顧客名またはカナで検索',value:customer?customer.name:(draft.customerQuery||'')})}
            ${searchPickerMarkup({label:'商品と加工仕様',inputId:'moveHandle',listId:'moveHandleList',clearId:'moveHandleClear',hintId:'moveHandleHint',placeholder:customer?'品番・商品名・加工仕様で検索':'先に顧客を選択',value:offering?offeringPickerLabel(offering):(draft.handleQuery||'')})}
          </div>
          <div class="grid">
            <label>サイズ（必須）<select id="moveSize"><option value="">選んでください</option>${sizes.map(s=>`<option value="${esc(s)}" ${draft.size===s?'selected':''}>${esc(s)}</option>`).join('')}</select></label>
            ${moveLocationFields(locSel)}
          </div>`}
        <div class="grid">
          <label>状態（必須）${conditionSelectHtml('inCondition',draft.condition||'new')}</label>
          <label>入庫数量（必須）${SM.qtyStepperHtml('in',draft.qty||1)}</label>
          <label>仕入単価（必須）<input id="inUnitCost" inputmode="numeric" value="${esc(draft.unitCost)}"></label>
          <label>入荷日（必須）<input id="inReceivedOn" type="date" value="${esc(draft.receivedOn||'')}"></label>
        </div>
        <p class="notice">状態は新品を初期選択しています。同じ取扱から中古を初めて入庫する場合も、ここで中古を選んでください（中古0枚のレコードは自動作成しません）。</p>
        <label>備考（任意）<input id="inNote" value="${esc(draft.note||'')}"></label>
        <p id="inPreview">${esc(conditionLabel(draft.condition))}の現在庫 ${num(current)}枚 → 入庫後 ${num(current+qty)}枚</p>
        <p id="moveError"></p>
      </div>
      <div class="dialogactions"><button type="button" id="closeMove2">戻る</button><button type="button" class="primary" id="confirmInbound" ${stockCanWrite()?'':'disabled'}>${num(qty)}枚を入庫する</button></div>`
    let stepper
    const syncPreview=()=>{
      const q=stepper.read()
      draft.qty=q==null?draft.qty:q
      $('#inPreview').textContent=`${conditionLabel(draft.condition)}の現在庫 ${num(draft.currentQty||0)}枚 → 入庫後 ${num((draft.currentQty||0)+(Number(draft.qty)||0))}枚`
      $('#confirmInbound').textContent=`${num(draft.qty||0)}枚を入庫する`
    }
    stepper=SM.bindQtyStepper(dlg,'in',{onChange:()=>{state.moveDirty=true;syncPreview()}})
    const inCond=$('#inCondition')
    if(inCond)inCond.onchange=()=>{draft.condition=inCond.value==='used'?'used':'new';syncCurrentFromTargets();state.moveDirty=true;syncPreview()}
    $('#inUnitCost').oninput=()=>{draft.unitCost=$('#inUnitCost').value;state.moveDirty=true}
    $('#inReceivedOn').oninput=()=>{draft.receivedOn=$('#inReceivedOn').value;state.moveDirty=true}
    $('#inNote').oninput=()=>{draft.note=$('#inNote').value;state.moveDirty=true}
    const close=()=>{if(state.moveDirty&&!window.confirm('入力中の内容を破棄しますか？'))return;dlg.close();dlg.innerHTML='';state.moveDirty=false}
    $('#closeMove').onclick=close;$('#closeMove2').onclick=close
    if(!target){
      const sizeEl=$('#moveSize')
      if(sizeEl)sizeEl.onchange=()=>{draft.size=sizeEl.value;state.moveDirty=true}
      const siteEl=$('#moveSite')
      if(siteEl)siteEl.onchange=()=>{draft.siteId=siteEl.value;draft.shelfId='';draft.locationId=null;state.moveDirty=true;render()}
      const shelfEl=$('#moveShelf')
      if(shelfEl)shelfEl.onchange=()=>{draft.shelfId=shelfEl.value;state.moveDirty=true;render()}
      bindOpeningSearchPicker({
        input:$('#moveCustomer'),list:$('#moveCustomerList'),clearBtn:$('#moveCustomerClear'),hint:$('#moveCustomerHint'),
        getItems:()=>filterOpeningCustomers(state.data.customers,$('#moveCustomer').value).map(c=>({id:c.id,label:c.name,html:esc(c.name),raw:c})),
        emptyText:'該当する顧客がありません',
        isConfirmed:()=>!!resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId),
        confirmedLabel:()=>(resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId)||{}).name||'',
        onPick:item=>{draft.customerId=item.id;draft.customerQuery=item.label;draft.handleId='';draft.customerProductId=null;draft.size='';draft.handleQuery='';state.moveDirty=true;render()},
        onClear:()=>{Object.assign(draft,clearOpeningCustomerSelection(draft));draft.customerQuery='';state.moveDirty=true;render()},
        onQueryChange:v=>{draft.customerQuery=v;if(draft.customerId){draft.customerId=null;draft.handleId='';draft.customerProductId=null;draft.size='';render();return true}return false},
      })
      bindOpeningSearchPicker({
        input:$('#moveHandle'),list:$('#moveHandleList'),clearBtn:$('#moveHandleClear'),hint:$('#moveHandleHint'),
        getItems:()=>filterOpeningOfferings(handles,draft.customerId,$('#moveHandle').value).map(h=>({id:h.id,label:offeringPickerLabel(h),html:esc(offeringPickerLabel(h)),raw:h})),
        emptyText:'該当する商品・加工仕様がありません',
        isConfirmed:()=>!!resolveConfirmedOpeningOffering(handles,draft.customerId,draft.handleId),
        confirmedLabel:()=>{const h=resolveConfirmedOpeningOffering(handles,draft.customerId,draft.handleId);return h?offeringPickerLabel(h):''},
        onPick:item=>{draft.handleId=String(item.id);draft.customerProductId=item.id;draft.handleQuery=item.label;draft.size='';state.moveDirty=true;render()},
        onClear:()=>{Object.assign(draft,clearOpeningProductFields(draft));draft.handleQuery='';state.moveDirty=true;render()},
        onQueryChange:v=>{draft.handleQuery=v;if(draft.handleId){draft.handleId='';draft.customerProductId=null;draft.size='';render();return true}return false},
      })
    }
    $('#confirmInbound').onclick=async()=>{
      const err=$('#moveError')
      err.textContent=''
      const q=stepper.read()
      if(q==null||q<1){err.textContent='入庫数量を入力してください。';return}
      draft.qty=q
      const costRaw=String($('#inUnitCost').value||'').trim().replace(/,/g,'')
      draft.receivedOn=$('#inReceivedOn').value
      draft.note=$('#inNote').value
      if(!target){
        if(!draft.customerProductId){err.textContent='顧客と商品の取扱を選んでください。';return}
        draft.size=$('#moveSize').value
        if(!draft.size){err.textContent='サイズを選んでください。';return}
        draft.siteId=$('#moveSite')?$('#moveSite').value:draft.siteId
        draft.shelfId=$('#moveShelf')?$('#moveShelf').value:draft.shelfId
        const prev=state.opening
        state.opening={siteId:draft.siteId,shelfId:draft.shelfId,locationId:draft.locationId}
        const sel=openingLocationSelection()
        state.opening=prev
        if(sel.locationId==null){err.textContent='保管場所を選んでください。';return}
        draft.locationId=sel.locationId
      }
      const button=$('#confirmInbound')
      button.disabled=true
      try{
        await api('/api/inbound',{
          token:SM.newMoveToken(),
          customerProductId:Number(draft.customerProductId),
          size:draft.size,
          locationId:Number(draft.locationId),
          condition:draft.condition==='used'?'used':'new',
          qty:Number(draft.qty),
          unitCost:costRaw===''?'':Number(costRaw),
          receivedOn:draft.receivedOn,
          note:draft.note,
        })
        state.moveDirty=false
        dlg.close();dlg.innerHTML=''
        toast('入庫しました。')
        await loadMoveTargets();paint()
      }catch(e){err.textContent=e.message;button.disabled=false}
    }
  }
  if(!dlg.open)dlg.showModal()
  render()
}
function openOutboundDialog(target){
  if(!stockCanWrite()){toast('閲覧専用のため、出庫できません。');return}
  const SM=StockMoves
  const dlg=moveDialogEl()
  let plan=null
  let fingerprint=''
  let confirmChanged=false
  state.moveDirty=false
  const outCondition=target.condition||'new'
  const paintDialog=async()=>{
    dlg.innerHTML=`<div class="dialoghead"><h2>出庫する</h2><button type="button" id="closeMove" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        <p>${esc(target.customerName)} / ${esc(target.code)} ${esc(target.productName)} / ${esc(target.size)} / ${esc(target.embroideryLabel)} / ${esc(target.locationLabel)}</p>
        <p><strong>状態：${esc(target.conditionLabel||conditionLabel(outCondition))}</strong>（この状態の在庫だけを減らします。状態をまたぐ自動振替はしません）</p>
        <label>出庫日（必須）<input id="outBusinessOn" type="date" value="${esc(todayISO())}"></label>
        <label>出庫数量（必須）${SM.qtyStepperHtml('out',1)}</label>
        <p id="outPreview">読み込み中…</p>
        <div id="outAlloc"></div>
        <label>備考（任意）<input id="outNote"></label>
        <p id="moveError"></p>
      </div>
      <div class="dialogactions"><button type="button" id="closeMove2">戻る</button><button type="button" class="primary" id="confirmOutbound">出庫する</button></div>`
    const close=()=>{if(state.moveDirty&&!window.confirm('入力中の内容を破棄しますか？'))return;dlg.close();dlg.innerHTML='';state.moveDirty=false}
    $('#closeMove').onclick=close;$('#closeMove2').onclick=close
    const refresh=async()=>{
      const err=$('#moveError');err.textContent=''
      const q=stepper.read()
      if(q==null||q<1){$('#outPreview').textContent='出庫数量を入力してください。';$('#outAlloc').innerHTML='';$('#confirmOutbound').disabled=true;return}
      try{
        plan=await api('/api/outbound/preview',{
          customerProductId:Number(target.customerProductId),
          size:target.size,
          locationId:Number(target.locationId),
          condition:outCondition,
          qty:q,
        })
        fingerprint=plan.fingerprint
        $('#outPreview').textContent=`${conditionLabel(outCondition)}の現在庫 ${num(plan.currentQty)}枚 → 出庫 ${num(plan.outboundQty)}枚 → 出庫後 ${num(plan.afterQty)}枚`
        if(!plan.ok){
          $('#outAlloc').innerHTML=`<p class="notice">在庫が${num(plan.shortfall)}枚不足しています。出庫できるのは${num(plan.currentQty)}枚までです。</p>`
          $('#confirmOutbound').disabled=true
          $('#confirmOutbound').textContent='在庫不足のため出庫できません'
        }else{
          $('#outAlloc').innerHTML=SM.allocationTableHtml(plan,esc,yen,num)
          $('#confirmOutbound').disabled=!stockCanWrite()
          $('#confirmOutbound').textContent=confirmChanged?'この内訳で出庫する':`${num(plan.outboundQty)}枚を出庫する`
        }
      }catch(e){$('#outPreview').textContent='';$('#outAlloc').innerHTML='';err.textContent=e.message;$('#confirmOutbound').disabled=true}
    }
    const stepper=SM.bindQtyStepper(dlg,'out',{onChange:()=>{state.moveDirty=true;confirmChanged=false;refresh()}})
    $('#outBusinessOn').oninput=()=>{state.moveDirty=true}
    $('#outNote').oninput=()=>{state.moveDirty=true}
    $('#confirmOutbound').onclick=async()=>{
      const err=$('#moveError');err.textContent=''
      const q=stepper.read()
      if(q==null||q<1){err.textContent='出庫数量を入力してください。';return}
      const businessOn=$('#outBusinessOn').value
      if(!businessOn){err.textContent='出庫日を入力してください。';return}
      if(!plan||!plan.ok){err.textContent='在庫が不足しています。';return}
      const button=$('#confirmOutbound');button.disabled=true
      try{
        await api('/api/outbound',{
          token:SM.newMoveToken(),
          customerProductId:Number(target.customerProductId),
          size:target.size,
          locationId:Number(target.locationId),
          condition:outCondition,
          qty:q,
          businessOn,
          note:$('#outNote').value,
          fingerprint,
          confirmChanged,
        })
        state.moveDirty=false
        dlg.close();dlg.innerHTML=''
        toast('出庫しました。')
        await loadMoveTargets();paint()
      }catch(e){
        if(e.status===409&&e.payload&&e.payload.needsReconfirm){
          plan=e.payload.plan
          fingerprint=plan.fingerprint
          confirmChanged=true
          err.textContent=e.message
          $('#outPreview').textContent=`現在庫 ${num(plan.currentQty)}枚 → 出庫 ${num(plan.outboundQty)}枚 → 出庫後 ${num(plan.afterQty)}枚`
          $('#outAlloc').innerHTML=SM.allocationTableHtml(plan,esc,yen,num)+(plan.ok?'':'')
          button.disabled=!plan.ok
          button.textContent=plan.ok?'この内訳で出庫する':'在庫不足のため出庫できません'
          return
        }
        err.textContent=e.message;button.disabled=false
      }
    }
    await refresh()
  }
  if(!dlg.open)dlg.showModal()
  paintDialog()
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
