/** Pure helpers for 初期在庫 / 入出庫 search pickers (no DOM). */
function normalizeOpeningQuery(q){
  return String(q||'').trim().toLowerCase()
}

function openingSpecLabel(embroidery){
  const t=String(embroidery||'').trim()
  if(!t||t==='加工なし'||t==='刺繍なし')return '加工なし'
  return t
}

function activeOpeningCustomers(customers){
  return (customers||[]).filter(c=>c&&c.active)
}

function filterOpeningCustomers(customers, query){
  const needle=normalizeOpeningQuery(query)
  const list=activeOpeningCustomers(customers)
  if(!needle)return list.slice()
  return list.filter(c=>{
    const name=String(c.name||'').toLowerCase()
    const kana=String(c.kana||'').toLowerCase()
    return name.includes(needle)||kana.includes(needle)
  })
}

function activeOpeningProducts(products){
  return (products||[]).filter(p=>p&&p.active&&!p.mergedIntoId)
}

function filterOpeningProducts(products, query){
  const needle=normalizeOpeningQuery(query)
  const list=activeOpeningProducts(products)
  if(!needle)return list.slice()
  return list.filter(p=>{
    const hay=`${p.code||''} ${p.name||''} ${p.color||''} ${p.supplierName||''}`.toLowerCase()
    return hay.includes(needle)
  })
}

function productPickerLabel(p){
  if(!p)return ''
  const supplier=p.supplierName?String(p.supplierName):'仕入先未設定'
  return `${p.code} ${p.name} ${p.color||''} / ${supplier}`.replace(/\s+/g,' ').trim()
}

function openingHandlesFromData(data){
  const customers=data&&data.customers||[]
  const products=data&&data.products||[]
  const handles=[]
  products.filter(p=>p&&p.active).forEach(p=>{
    (p.links||[]).filter(l=>l&&l.active).forEach(l=>{
      const customer=customers.find(c=>c.id===l.customerId)
      if(customer&&customer.active)handles.push({...l,product:p,customer})
    })
  })
  return handles
}

/** 入出庫向け: 選択済み顧客配下の取扱だけを検索 */
function filterOpeningOfferings(handles, customerId, query){
  if(customerId==null||customerId==='')return []
  const cid=Number(customerId)
  if(!Number.isFinite(cid)||cid<=0)return []
  const needle=normalizeOpeningQuery(query)
  const mine=(handles||[]).filter(h=>Number(h.customerId)===cid)
  if(!needle)return mine.slice()
  return mine.filter(h=>{
    const p=h.product||{}
    const spec=openingSpecLabel(h.embroidery)
    const hay=`${p.code||''} ${p.name||''} ${p.color||''} ${spec} ${h.embroidery||''}`.toLowerCase()
    return hay.includes(needle)
  })
}

/** 初期在庫向け: 全取扱を顧客名・品番・商品名・色・加工情報で絞り込み */
function filterOpeningHandles(handles, query){
  const list=Array.isArray(handles)?handles.slice():[]
  const needle=normalizeOpeningQuery(query)
  if(!needle)return list
  return list.filter(h=>{
    const p=h.product||{}
    const c=h.customer||{}
    const spec=openingSpecLabel(h.embroidery)
    const hay=`${c.name||''} ${p.code||''} ${p.name||''} ${p.color||''} ${spec} ${h.embroidery||''}`.toLowerCase()
    return hay.includes(needle)
  })
}

function offeringPickerLabel(h){
  if(!h||!h.product)return ''
  const p=h.product
  const spec=openingSpecLabel(h.embroidery)
  return `${p.code} ${p.name} ${p.color} / ${spec}`
}

function handlePickerLabel(h){
  if(!h||!h.product||!h.customer)return ''
  const p=h.product
  const spec=openingSpecLabel(h.embroidery)
  return `${h.customer.name}／${p.code}／${p.name}／${p.color||''}／${spec}`
}

function clearOpeningProductFields(opening){
  const next={...(opening||{})}
  delete next.handleId
  delete next.handleQuery
  delete next.lines
  delete next.sizeNotice
  return next
}

function clearOpeningCustomerSelection(opening){
  const next=clearOpeningProductFields(opening)
  delete next.customerId
  delete next.customerQuery
  return next
}

function clearOpeningHandleSelection(opening){
  const next={...(opening||{})}
  delete next.handleId
  delete next.handleQuery
  delete next.customerId
  delete next.customerQuery
  delete next.lines
  delete next.sizeNotice
  return next
}

/** Keep qty lines whose sizes still exist on the product; drop the rest and report. */
function reconcileOpeningLinesForProduct(opening, product){
  const base={...(opening||{})}
  delete base.sizeNotice
  if(!product||!product.id){
    delete base.lines
    return {opening:base,clearedSizes:[]}
  }
  const sizes=Array.isArray(product.sizes)?product.sizes:[]
  const allowed=new Set(sizes)
  const prev=base.lines
  if(!prev||!Array.isArray(prev.rows)||Number(prev.productId)!==Number(product.id)){
    const cleared=[]
    const kept=[]
    if(prev&&Array.isArray(prev.rows)&&Number(prev.productId)!==Number(product.id)){
      const bySize=new Map()
      for(const row of prev.rows){
        if(!row)continue
        if(allowed.has(row.size)){
          if(!bySize.has(row.size))bySize.set(row.size,[])
          bySize.get(row.size).push({size:row.size,qty:row.qty||'',unitCost:row.unitCost||''})
        }else if(String(row.qty||'').trim()||String(row.unitCost||'').trim()){
          if(!cleared.includes(row.size))cleared.push(row.size)
        }
      }
      for(const size of sizes){
        const rows=bySize.get(size)
        if(rows&&rows.length)kept.push(...rows)
        else kept.push({size,qty:'',unitCost:''})
      }
      base.lines={productId:product.id,rows:kept}
      return {opening:base,clearedSizes:cleared}
    }
    base.lines={productId:product.id,rows:sizes.map(size=>({size,qty:'',unitCost:''}))}
    return {opening:base,clearedSizes:[]}
  }
  return {opening:base,clearedSizes:[]}
}

function selectOpeningHandle(opening, handle){
  const next={...(opening||{})}
  if(!handle||handle.id==null)return clearOpeningHandleSelection(next)
  next.handleId=String(handle.id)
  next.handleQuery=handlePickerLabel(handle)
  next.customerId=String(handle.customerId)
  next.customerQuery=handle.customer?handle.customer.name:''
  const reconciled=reconcileOpeningLinesForProduct(next,handle.product)
  const out=reconciled.opening
  if(reconciled.clearedSizes.length){
    out.sizeNotice=`商品が変わったため、登録サイズにない「${reconciled.clearedSizes.join('、')}」の数量・単価を解除しました。`
  }
  return out
}

/** After successful save: drop 取扱/数量; keep location/basis/condition for continuous register. */
function openingStateAfterSuccessfulSave(opening){
  const keep={condition:(opening&&opening.condition)==='used'?'used':'new'}
  if(opening&&opening.basis)keep.basis=opening.basis
  if(opening&&opening.siteId!=null&&opening.siteId!=='')keep.siteId=String(opening.siteId)
  if(opening&&opening.shelfId!=null&&opening.shelfId!=='')keep.shelfId=String(opening.shelfId)
  if(opening&&opening.locationId!=null&&opening.locationId!=='')keep.locationId=String(opening.locationId)
  return keep
}

/** After 通常入庫 save: drop 取扱/数量; keep location/condition/receivedOn (no opening-only fields). */
function inboundStateAfterSuccessfulSave(opening){
  const keep={condition:(opening&&opening.condition)==='used'?'used':'new'}
  if(opening&&opening.receivedOn)keep.receivedOn=opening.receivedOn
  if(opening&&opening.note)keep.note=opening.note
  if(opening&&opening.siteId!=null&&opening.siteId!=='')keep.siteId=String(opening.siteId)
  if(opening&&opening.shelfId!=null&&opening.shelfId!=='')keep.shelfId=String(opening.shelfId)
  if(opening&&opening.locationId!=null&&opening.locationId!=='')keep.locationId=String(opening.locationId)
  return keep
}

function resolveConfirmedOpeningCustomer(customers, customerId){
  if(customerId==null||customerId==='')return null
  const id=Number(customerId)
  if(!Number.isFinite(id))return null
  return activeOpeningCustomers(customers).find(c=>Number(c.id)===id)||null
}

function resolveConfirmedOpeningProduct(products, productId){
  if(productId==null||productId==='')return null
  const id=Number(productId)
  if(!Number.isFinite(id))return null
  return activeOpeningProducts(products).find(p=>Number(p.id)===id)||null
}

function resolveConfirmedOpeningOffering(handles, customerId, handleId){
  if(handleId==null||handleId==='')return null
  const hid=Number(handleId)
  const cid=Number(customerId)
  if(!Number.isFinite(hid)||!Number.isFinite(cid))return null
  return (handles||[]).find(h=>Number(h.id)===hid&&Number(h.customerId)===cid)||null
}

function resolveConfirmedOpeningHandle(handles, handleId){
  if(handleId==null||handleId==='')return null
  const hid=Number(handleId)
  if(!Number.isFinite(hid))return null
  return (handles||[]).find(h=>Number(h.id)===hid)||null
}

/** Filter registered 初期在庫 list rows by 顧客名・品番・商品名 (partial, case-insensitive). */
function filterOpeningListEntries(entries, query){
  const list=Array.isArray(entries)?entries:[]
  const needle=normalizeOpeningQuery(query)
  if(!needle)return list.slice()
  return list.filter(e=>{
    const hay=`${e&&e.customerName||''} ${e&&e.code||''} ${e&&e.productName||''}`.toLowerCase()
    return hay.includes(needle)
  })
}

if(typeof module!=='undefined'&&module.exports){
  module.exports={
    normalizeOpeningQuery,
    activeOpeningCustomers,
    filterOpeningCustomers,
    activeOpeningProducts,
    filterOpeningProducts,
    productPickerLabel,
    openingHandlesFromData,
    filterOpeningOfferings,
    filterOpeningHandles,
    offeringPickerLabel,
    handlePickerLabel,
    clearOpeningProductFields,
    clearOpeningCustomerSelection,
    clearOpeningHandleSelection,
    reconcileOpeningLinesForProduct,
    selectOpeningHandle,
    openingStateAfterSuccessfulSave,
    inboundStateAfterSuccessfulSave,
    resolveConfirmedOpeningCustomer,
    resolveConfirmedOpeningProduct,
    resolveConfirmedOpeningOffering,
    resolveConfirmedOpeningHandle,
    filterOpeningListEntries
  }
}
