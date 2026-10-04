const $=(s,r=document)=>r.querySelector(s)
const state={mode:'masters',tab:'customers',q:'',stopped:false,productSupplierId:'',data:null,stock:null,filters:{group:'product',customerId:'',productId:'',size:'',spec:'',locationId:''},token:'',opening:{}}
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

const STOCK_SCREENS={customers:'stock-customers',suppliers:'stock-suppliers',products:'stock-products',locations:'stock-locations',list:'stock-list'}
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
function mountStockDisplay(stage,tab,repaint){
  if(!stage||typeof DisplayMode==='undefined')return 'compact'
  const screenId=stockScreenId(tab)
  const toolbar=document.querySelector('#main .bar')
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
async function boot(){
  state.data=await api('/api/bootstrap')
  if(!state.ready&&state.data.mode==='sample'){state.mode='stock';state.tab='list'}
  state.ready=true
  const labels={sample:['試用版・架空のサンプル','サンプルのデータは data\\sample に保存されています。'],desk:['在庫管理','指示書デスクと同じ画面から使っています。在庫のデータは指示書とは別のファイルです。'],live:['試用版・このパソコンの台帳','この台帳は data\\live に保存されています。サンプルとは別です。']}
  const label=labels[state.data.mode]||labels.live
  $('#eyebrow').textContent=label[0]
  $('#foot').textContent=label[1]
  if(state.mode==='stock'&&state.tab==='list')await loadStock()
  await ensureStockDisplayModes().catch(()=>{})
  paint()
}
function paint(){
  document.querySelectorAll('.switch button[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===state.mode))
  const tabs=embedded
    ?[['customers','顧客'],['suppliers','仕入先'],['products','商品'],['locations','保管場所'],['opening','初期在庫'],['list','在庫一覧']]
    :state.mode==='masters'
    ?[['customers','顧客'],['suppliers','仕入先'],['products','商品']]
    :[['list','在庫一覧'],['opening','初期在庫'],['locations','保管場所']]
  if(!tabs.some(t=>t[0]===state.tab))state.tab=tabs[0][0]
  $('#tabs').innerHTML=tabs.map(([id,label])=>`<button type="button" data-tab="${id}" class="${id===state.tab?'active':''}">${label}</button>`).join('')
  $('#tabs').querySelectorAll('button').forEach(b=>b.onclick=async()=>{state.tab=b.dataset.tab;if(state.tab==='list')await loadStock();paint()})
  const view={customers:customers,suppliers:suppliers,products:products,locations:locations,opening:opening,list:stockList}[state.tab]
  view()
}
function excelTools(){
  return `<span class="exceltools"><button type="button" id="exceldown">記入用Excel</button><button type="button" id="excelin">Excelを読み込む</button><input id="excelfile" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden></span>`
}
function bar(placeholder,extra=''){
  return `<div class="bar">${extra}<label>検索<input id="q" value="${esc(state.q)}" placeholder="${placeholder}"></label>
    <label class="check"><input id="stopped" type="checkbox" ${state.stopped?'checked':''}>停止も表示</label>
    <button type="button" class="primary" id="add">新しく登録</button>${excelTools()}</div>
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
function locations(){
  const rows=state.data.locations.filter(r=>(state.stopped||r.active)&&r.label.includes(state.q))
  $('#main').innerHTML=`<section class="panel">${listNote('保管場所の注意','まずは「石狩倉庫①」のような拠点だけで登録できます。棚や区画は、あとからその拠点の下に追加できます。償却待ちなどの区分は、保管場所には登録しません。')}${bar('場所の名前')}<div class="list-stage" id="stockliststage"><div class="tablewrap"><table><thead><tr><th>場所ID</th><th>保管場所</th><th>種類</th><th></th></tr></thead><tbody>
    ${rows.map(r=>`<tr><td>${r.id}</td><td><span class="dm-primary">${esc(r.label)}</span> ${r.active?'':'<span class="badge stop">停止</span>'}</td><td>${r.parentId?'棚・区画':'拠点'}</td><td>${stockOpenBtn(r.id)}</td></tr>`).join('')||'<tr><td colspan="4">保管場所はまだありません。</td></tr>'}
  </tbody></table></div><div class="list-grid" id="stocklistgrid" hidden></div></div></section>`
  const grid=$('#stocklistgrid')
  if(grid)grid.innerHTML=rows.length?rows.map(r=>`<article class="dm-card"><div class="dm-primary">${esc(r.label)}${r.active?'':' <span class="badge stop">停止</span>'}</div><div class="dm-secondary">場所ID ${r.id}　${r.parentId?'棚・区画':'拠点'}</div><div class="dm-actions">${stockOpenBtn(r.id)}</div></article>`).join(''):'<div class="dm-empty">保管場所はまだありません。</div>'
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
  dlg.innerHTML=`<form><div class="dialoghead"><h2>${row?'修正':'新しく登録'}：${title}</h2><button type="button" id="close" class="quiet">閉じる</button></div><div class="dialogbody">${form(row)}</div><p id="error"></p><div class="dialogactions"><button type="button" id="stop" class="quiet" ${row?'':'hidden'}>${row&&row.active===false?'使用を再開':'使用停止'}</button><button type="button" class="primary" id="save">保存</button></div></form>`
  dlg.showModal()
  dlg.querySelector('form').onsubmit=ev=>ev.preventDefault()
  $('#close').onclick=()=>{dlg.close();dlg.innerHTML=''}
  if(row&&title==='商品')bindProductEditor(row)
  if(!row&&title==='商品')bindProductEditor({sizes:[],links:[],active:true})
  $('#save').onclick=async ev=>{
    ev.preventDefault()
    try{
      await save(readForm(row))
      dlg.close();dlg.innerHTML='';toast('保存しました。');await boot()
    }catch(e){$('#error').textContent=e.message}
  }
  const stop=$('#stop')
  if(stop)stop.onclick=async()=>{
    if(!confirm(row.active?'使用停止にします。過去の在庫は残ります。':'使用を再開します。'))return
    try{await save({...readForm(row),active:!row.active});dlg.close();dlg.innerHTML='';toast(row.active?'使用停止にしました。':'使用を再開しました。');await boot()}catch(e){$('#error').textContent=e.message}
  }
}
function field(id,label,value,required){return `<label>${label}${required?'（必須）':''}<input id="${id}" value="${esc(value||'')}"></label>`}
function customerForm(r){r=r||{};return `<div class="grid">${field('name','顧客名',r.name,true)}${field('kana','フリガナ',r.kana)}${field('phone','電話番号',r.phone)}${field('contact','担当者',r.contact)}</div><label>住所<textarea id="address">${esc(r.address||'')}</textarea></label><p class="sub">顧客IDは自動です。名前を変えても、同じ顧客のまま在庫がつながります。</p>`}
function supplierForm(r){r=r||{};return `<div class="grid">${field('name','仕入先名',r.name,true)}${field('phone','電話番号',r.phone)}${field('contact','担当者',r.contact)}</div><label>住所<textarea id="address">${esc(r.address||'')}</textarea></label>`}
function locationForm(r){
  r=r||{}
  const sites=state.data.locations.filter(x=>!x.parentId&&(x.active||x.id===r.parentId)&&x.id!==r.id)
  return `<label class="check"><input id="isShelf" type="checkbox" ${r.parentId?'checked':''}>拠点の下に棚・区画を作る</label>
    <label id="parentbox" ${r.parentId?'':'hidden'}>拠点<select id="parentId">${sites.map(s=>`<option value="${s.id}" ${s.id===r.parentId?'selected':''}>${esc(s.name)}</option>`).join('')}</select></label>
    ${field('name','場所の名前',r.name,true)}
    <p class="sub">棚を付けない場合は、チェックを外して拠点名だけ保存します。償却待ちなどの区分は場所にしないでください。</p>`
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
      b.onclick=()=>{if(editor.scheme===b.dataset.scheme)return;editor.scheme=b.dataset.scheme;draw()}
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
      if(el.dataset.k==='customerId')link.customerId=Number(el.value)
      if(el.dataset.k==='embroidery')link.embroidery=el.value.trim()
      if(el.dataset.k==='isStandard'){link.isStandard=el.checked;if(el.checked)editor.links.forEach(x=>{if(x!==link&&x.customerId===link.customerId)x.isStandard=false});draw()}
      if(el.dataset.k==='active')link.active=el.checked
    })
    $('#links').querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{editor.links.splice(Number(b.dataset.remove),1);draw()})
  }
  $('#addSize').onclick=()=>{
    const s=$('#customSize').value.trim()
    if(!s)return
    if(!editor.sizes.includes(s))editor.sizes.push(s)
    $('#customSize').value=''
    draw()
  }
  $('#addLink').onclick=()=>{
    const first=state.data.customers.find(c=>c.active)
    if(!first){$('#error').textContent='先に顧客を登録してください。';return}
    editor.links.push({customerId:first.id,embroidery:'',isStandard:editor.links.filter(l=>l.customerId===first.id).length===0,active:true})
    draw()
  }
  draw()
  $('#dialog')._editor=editor
  const shelf=$('#isShelf')
  if(shelf){}
}
function readForm(row){
  const title=$('#dialog h2').textContent
  const active=row?row.active!==false:true
  if(title.includes('顧客'))return {id:row&&row.id,name:$('#name').value,kana:$('#kana').value,phone:$('#phone').value,contact:$('#contact').value,address:$('#address').value,active}
  if(title.includes('仕入先'))return {id:row&&row.id,name:$('#name').value,phone:$('#phone').value,contact:$('#contact').value,address:$('#address').value,active}
  if(title.includes('保管場所')){
    const isShelf=$('#isShelf').checked
    return {id:row&&row.id,name:$('#name').value,parentId:isShelf?Number($('#parentId').value):null,active}
  }
  const editor=$('#dialog')._editor
  const supplier=$('#supplierId').value
  return {id:row&&row.id,code:$('#code').value,name:$('#name').value,color:$('#color').value,supplierId:supplier?Number(supplier):null,sizes:editor.sizes,links:editor.links.map(l=>({customerId:Number(l.customerId),embroidery:(l.embroidery||'').trim(),isStandard:!!l.isStandard,active:l.active!==false})),active}
}
function opening(){
  const customers=state.data.customers.filter(c=>c.active)
  const handles=[]
  state.data.products.filter(p=>p.active).forEach(p=>p.links.filter(l=>l.active).forEach(l=>{
    const customer=state.data.customers.find(c=>c.id===l.customerId)
    if(customer&&customer.active)handles.push({...l,product:p,customer})
  }))
  const customerId=Number(state.opening.customerId||customers[0]?.id||0)
  const mine=handles.filter(h=>h.customerId===customerId)
  const selected=mine.find(h=>h.id===Number(state.opening.handleId))||mine.find(h=>h.isStandard)||mine[0]
  const locations=state.data.locations.filter(l=>l.active)
  $('#main').innerHTML=`<section class="panel"><div class="dialogbody">
    <div class="bar">${excelTools()}</div>
    <p class="excelhint">記入用Excelの各シートに、顧客・仕入先・商品・取扱・保管場所・初期在庫を書けます。同じファイルをもう一度読むと台帳は空欄以外を更新し、同じ初期在庫は増やしません。数量を変えた初期在庫は、別の初期在庫として追加されます。</p>
    <p class="provisional">入荷日が分からない初期在庫は、架空の日付で埋めず「入荷日不明」にします。運用開始後の入荷より先に払い出す想定です。初期在庫同士の順番が不明なときは、払出し順の小さい番号から出します。仕入単価の安さでは順番を決めません。</p>
    <div class="grid">
      <label>顧客（必須）<select id="customerId">${customers.map(c=>`<option value="${c.id}" ${c.id===customerId?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label>
      <label>商品と加工仕様（必須）<select id="handleId">${mine.map(h=>`<option value="${h.id}" ${selected&&h.id===selected.id?'selected':''}>${esc(h.product.code)} ${esc(h.product.name)} ${esc(h.product.color)} / ${esc(specLabel(h.embroidery))}${h.isStandard?' / 標準':''} / 商品ID ${h.product.id}</option>`).join('')}</select></label>
      <label>保管場所（必須）<select id="locationId">${locations.map(l=>`<option value="${l.id}" ${String(l.id)===String(state.opening.locationId)?'selected':''}>${esc(l.label)}</option>`).join('')}</select></label>
      <label>在庫基準日（必須）<input id="basisDate" type="date" value="${esc(state.opening.basis||'')}"></label>
    </div>
    <p id="standardNote"></p>
    <label class="check"><input id="unknown" type="checkbox" checked>入荷日不明</label>
    <div class="grid"><label>入荷日<input id="receivedOn" type="date"></label><label>入荷日不明のときの払出し順<input id="payoutOrder" inputmode="numeric" value="1"></label></div>
    <h2>サイズごとの数量と仕入単価</h2>
    <div id="sizerows"></div>
    <div class="bar"><label>この商品にサイズを追加<input id="extraSize"></label><button type="button" id="addExtra">追加</button><button type="button" class="primary" id="confirmOpen">保存前に確認</button></div>
    <p id="error"></p>
  </div></section>`
  const drawSizes=()=>{
    const product=selected&&selected.product
    if(!product){$('#sizerows').innerHTML='<p>先に、顧客・商品情報で取扱を登録してください。</p>';return}
    const rows=state.opening.lines&&state.opening.lines.productId===product.id?state.opening.lines.rows:product.sizes.map(size=>({size,qty:'',unitCost:''}))
    state.opening.lines={productId:product.id,rows}
    $('#sizerows').innerHTML=rows.map((line,i)=>`<div class="sizerow"><strong>${esc(line.size)}</strong><label>数量<input data-i="${i}" data-k="qty" inputmode="numeric" value="${esc(line.qty)}"></label><label>仕入単価（円）<input data-i="${i}" data-k="unitCost" inputmode="numeric" value="${esc(line.unitCost)}"></label><button type="button" data-addprice="${i}">別の単価</button></div>`).join('')
    $('#sizerows').querySelectorAll('input').forEach(el=>el.oninput=()=>{rows[Number(el.dataset.i)][el.dataset.k]=el.value})
    $('#sizerows').querySelectorAll('[data-addprice]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.addprice);rows.splice(i+1,0,{size:rows[i].size,qty:'',unitCost:''});state.opening.lines.rows=rows;drawSizes()})
    const standard=$('#standardNote')
    standard.textContent=selected&&selected.isStandard&&mine.filter(h=>h.product.id===selected.product.id).length===1?'この顧客はこの加工仕様だけを使う標準仕様です。入出荷では、毎回選び直さなくてよい形で登録されています。':selected?'刺繍ありとなしを併用する顧客では、仕様ごとに在庫を分けています。':' '
  }
  const syncUnknown=()=>{const on=$('#unknown').checked;$('#receivedOn').disabled=on;$('#payoutOrder').disabled=!on;if(on)$('#receivedOn').value=''}
  $('#customerId').onchange=()=>{state.opening.customerId=$('#customerId').value;state.opening.handleId='';state.opening.lines=null;opening()}
  $('#handleId').onchange=()=>{state.opening.customerId=$('#customerId').value;state.opening.handleId=$('#handleId').value;state.opening.lines=null;opening()}
  $('#unknown').onchange=syncUnknown
  $('#basisDate').onchange=()=>{state.opening.basis=$('#basisDate').value}
  $('#locationId').onchange=()=>{state.opening.locationId=$('#locationId').value}
  $('#addExtra').onclick=async()=>{
    const size=$('#extraSize').value.trim()
    if(!selected||!size)return
    if(selected.product.sizes.includes(size)){$('#error').textContent='そのサイズは既にあります。LLと2Lのように違って見える表記は、自動ではまとめません。';return}
    try{
      const p=selected.product
      const keep=(state.opening.lines?.rows||[]).concat([{size,qty:'',unitCost:''}])
      await api('/api/products',{id:p.id,code:p.code,name:p.name,color:p.color,supplierId:p.supplierId,active:p.active,sizes:p.sizes.concat(size),links:p.links})
      state.opening.customerId=$('#customerId').value
      state.opening.handleId=$('#handleId').value
      state.opening.basis=$('#basisDate').value
      state.opening.locationId=$('#locationId').value
      state.data=await api('/api/bootstrap')
      state.opening.lines={productId:p.id,rows:keep}
      toast('サイズを追加しました。')
      opening()
    }catch(e){$('#error').textContent=e.message}
  }
  $('#confirmOpen').onclick=()=>confirmOpening(selected)
  syncUnknown();drawSizes();bindExcel()
}
function confirmOpening(selected){
  try{
    if(!selected)throw Error('商品と加工仕様を選んでください。')
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
    const basis=$('#basisDate').value
    if(!basis)throw Error('在庫基準日を入力してください。')
    const unknown=$('#unknown').checked
    if(!unknown&&!$('#receivedOn').value)throw Error('入荷日を入力するか、入荷日不明にしてください。')
    if(unknown&&!/^\d+$/.test($('#payoutOrder').value))throw Error('払出し順は1以上の整数です。')
    state.token=crypto.randomUUID()
    const totalQty=lines.reduce((s,l)=>s+l.qty,0)
    const totalAmount=lines.reduce((s,l)=>s+l.qty*l.unitCost,0)
    const loc=state.data.locations.find(l=>l.id===Number($('#locationId').value))
    const dlg=$('#dialog')
    dlg.innerHTML=`<div class="dialoghead"><h2>初期在庫の確認</h2></div><div class="dialogbody">
      <p>${esc(selected.customer.name)} / ${esc(selected.product.code)} ${esc(selected.product.name)} ${esc(selected.product.color)} / ${esc(specLabel(selected.embroidery))}</p>
      <p>${esc(loc.label)}　基準日 ${esc(basis)}　${unknown?'入荷日不明・払出し順 '+esc($('#payoutOrder').value):'入荷日 '+esc($('#receivedOn').value)}</p>
      <ul>${lines.map(l=>`<li>${esc(l.size)}　${num(l.qty)}着 × ${yen(l.unitCost)} ＝ ${yen(l.qty*l.unitCost)}</li>`).join('')}</ul>
      <p class="money">合計 ${num(totalQty)}着　${yen(totalAmount)}</p>
      <p>この金額は、仕入原価ベースの在庫金額です。</p><p id="error"></p></div>
      <div class="dialogactions"><button type="button" id="back">戻る</button><button type="button" class="primary" id="commit">この内容で登録</button></div>`
    dlg.showModal()
    $('#back').onclick=()=>{dlg.close();dlg.innerHTML=''}
    $('#commit').onclick=async()=>{
      const button=$('#commit');button.disabled=true
      try{
        const result=await api('/api/opening',{token:state.token,customerProductId:selected.id,locationId:Number($('#locationId').value),basisDate:basis,receivedUnknown:unknown,receivedOn:unknown?'':$('#receivedOn').value,payoutOrder:unknown?Number($('#payoutOrder').value):0,lines})
        dlg.close();dlg.innerHTML=''
        toast(result.already?'この登録は保存済みです。二重には加算していません。':'初期在庫を登録しました。')
        state.opening.lines=null
        state.mode='stock';state.tab='list'
        await boot()
      }catch(e){dlg.querySelector('#error').textContent=e.message;button.disabled=false}
    }
  }catch(e){$('#error').textContent=e.message}
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
  $('#main').innerHTML=`<section class="panel">
    <div class="bar">
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
      ${state.stock.lots.map(l=>`<tr><td class="dm-primary">${esc(l.customerName)}</td><td>${esc(l.code)} ${esc(l.productName)} ${esc(l.color)}<span class="sub dm-secondary">商品ID ${l.productId}</span></td><td>${esc(l.size)} / ${esc(l.embroideryLabel)}</td><td>${esc(l.locationLabel)}<span class="sub">${l.receivedUnknown?'入荷日不明・払出し順 '+l.payoutOrder:'入荷日 '+esc(l.receivedOn)}</span></td><td class="money">${yen(l.unitCost)}</td><td>${num(l.qty)}</td><td class="money">${yen(l.amount)}</td><td>${l.correctable?`<button type="button" data-correct="${l.id}">訂正</button>`:''}<button type="button" data-history="${l.id}">履歴</button> ${stockOpenBtn(l.id,'開く')}</td></tr>`).join('')||'<tr><td colspan="8">在庫はありません。</td></tr>'}
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
  body.innerHTML=state.stock.groups.map(g=>`<article class="dialogbody"><h2 class="dm-primary">${esc(g.title)}</h2><p class="dm-secondary">数量 ${num(g.qty)}　仕入原価ベースの在庫金額 ${yen(g.amount)}</p><ul class="parts">${g.parts.map(p=>`<li>${yen(p.unitCost)} × ${num(p.qty)} ＝ ${yen(p.amount)}（${esc(p.customerName)} / ${esc(p.code)} / 商品ID ${p.productId} / ${esc(p.size)} / ${esc(p.embroideryLabel)} / ${esc(p.locationLabel)}）</li>`).join('')}</ul></article>`).join('')||'<p class="dialogbody">在庫はありません。</p>'
  if(grid)grid.innerHTML=state.stock.groups.length?state.stock.groups.map(g=>`<div class="dm-group">${esc(g.title)}<span>${num(g.qty)} / ${yen(g.amount)}</span></div>`+g.parts.map(p=>`<article class="dm-card"><div class="dm-primary">${esc(p.customerName)} / ${esc(p.code)}</div><div class="dm-secondary">商品ID ${p.productId}　${esc(p.size)} / ${esc(p.embroideryLabel)}</div><div class="dm-meta"><span>${esc(p.locationLabel)}</span><span>${yen(p.unitCost)} × ${num(p.qty)}</span><span>${yen(p.amount)}</span></div></article>`).join('')).join(''):'<div class="dm-empty">在庫はありません。</div>'
  if(grid)grid.hidden=mode!=='grid'
  if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,stockScreenId('list'))
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
document.querySelectorAll('.switch button[data-mode]').forEach(b=>b.onclick=async()=>{state.mode=b.dataset.mode;state.tab=state.mode==='masters'?'customers':'list';$('#desknote').hidden=true;if(state.tab==='list')await loadStock();paint()})
$('#desk').onclick=()=>{if(embedded){window.parent.postMessage({type:'desk-catalog',mode:'desk'},location.origin);return}$('#desknote').hidden=false}
$('#backup').onclick=async()=>{try{const r=await api('/api/backup',{});toast('バックアップを作りました。'+r.file)}catch(e){toast(e.message)}}
document.addEventListener('change',e=>{if(e.target&&e.target.id==='isShelf'){const box=$('#parentbox');if(box)box.hidden=!e.target.checked}})
boot().catch(e=>{$('#main').textContent=e.message})
