/** Pure helpers for 初期在庫 customer / product+spec search pickers (no DOM). */
function normalizeOpeningQuery(q){
  return String(q||'').trim().toLowerCase()
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

function filterOpeningOfferings(handles, customerId, query){
  if(customerId==null||customerId==='')return []
  const cid=Number(customerId)
  if(!Number.isFinite(cid)||cid<=0)return []
  const needle=normalizeOpeningQuery(query)
  const mine=(handles||[]).filter(h=>Number(h.customerId)===cid)
  if(!needle)return mine.slice()
  return mine.filter(h=>{
    const p=h.product||{}
    const spec=h.embroidery?String(h.embroidery):'刺繍なし'
    const hay=`${p.code||''} ${p.name||''} ${p.color||''} ${spec} ${h.embroidery||''}`.toLowerCase()
    return hay.includes(needle)
  })
}

function offeringPickerLabel(h){
  if(!h||!h.product)return ''
  const p=h.product
  const spec=h.embroidery?String(h.embroidery):'刺繍なし'
  return `${p.code} ${p.name} ${p.color} / ${spec}`
}

function clearOpeningProductFields(opening){
  const next={...(opening||{})}
  delete next.handleId
  delete next.handleQuery
  delete next.lines
  return next
}

function clearOpeningCustomerSelection(opening){
  const next=clearOpeningProductFields(opening)
  delete next.customerId
  delete next.customerQuery
  return next
}

/** After successful save: keep customer; drop product/qty so values cannot linger for accidental re-register. */
function openingStateAfterSuccessfulSave(opening){
  const keep=opening&&opening.customerId!=null&&opening.customerId!==''?{customerId:String(opening.customerId)}:{}
  if(opening&&opening.basis)keep.basis=opening.basis
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

function resolveConfirmedOpeningOffering(handles, customerId, handleId){
  if(handleId==null||handleId==='')return null
  const hid=Number(handleId)
  const cid=Number(customerId)
  if(!Number.isFinite(hid)||!Number.isFinite(cid))return null
  return (handles||[]).find(h=>Number(h.id)===hid&&Number(h.customerId)===cid)||null
}

if(typeof module!=='undefined'&&module.exports){
  module.exports={
    normalizeOpeningQuery,
    activeOpeningCustomers,
    filterOpeningCustomers,
    openingHandlesFromData,
    filterOpeningOfferings,
    offeringPickerLabel,
    clearOpeningProductFields,
    clearOpeningCustomerSelection,
    openingStateAfterSuccessfulSave,
    resolveConfirmedOpeningCustomer,
    resolveConfirmedOpeningOffering
  }
}
