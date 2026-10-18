/** 重複商品の比較・統合 UI（確認後にのみ実行）。 */
(function(global){
'use strict'

function esc(s){
  return String(s==null?'':s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
}
function yen(n){
  if(n==null||n==='')return '—'
  return Number(n).toLocaleString('ja-JP')+'円'
}
function num(n){
  return Number(n||0).toLocaleString('ja-JP')
}
function specLabel(v){
  const t=String(v||'').trim()
  if(!t||t==='加工なし'||t==='刺繍なし')return '加工なし'
  return t
}

function productCardHtml(p, opts){
  opts=opts||{}
  const selected=opts.selected
  const keepId=opts.keepId
  const subNames=(p.subSuppliers||[]).map(s=>s.name).filter(Boolean).join('、')||'なし'
  const handles=(p.handles||p.links||[]).map(l=>{
    const qty=l.lotCount!=null?`（在庫${num(l.stockQty||0)}）`:''
    return `${esc(l.customerName)} / ${esc(specLabel(l.embroideryLabel||l.embroidery))}${l.isStandard?'（標準）':''}${l.active===false?' 停止':''}${qty}`
  }).join('<br>')||'未設定'
  const isKeep=Number(keepId)===Number(p.id)
  return `<article class="merge-card ${selected?'is-selected':''} ${isKeep?'is-keep':''}" data-pid="${p.id}">
    <label class="merge-pick"><input type="checkbox" data-merge-pick="${p.id}" ${selected?'checked':''}> 統合対象</label>
    <label class="merge-keep"><input type="radio" name="mergeKeep" value="${p.id}" ${isKeep?'checked':''}> 残す</label>
    <dl class="merge-dl">
      <div><dt>商品ID</dt><dd>${p.id}</dd></div>
      <div><dt>品番</dt><dd>${esc(p.codeDisplay||p.code||'品番なし')}</dd></div>
      <div><dt>商品名</dt><dd>${esc(p.name)}</dd></div>
      <div><dt>色</dt><dd>${esc(p.color)||'—'}</dd></div>
      <div><dt>カテゴリ</dt><dd>${esc(p.category)||'—'}</dd></div>
      <div><dt>通常仕入先</dt><dd>${esc(p.supplierName)||'未設定'}</dd></div>
      <div><dt>サブ仕入先</dt><dd>${esc(subNames)}</dd></div>
      <div><dt>サイズ</dt><dd>${esc((p.sizes||[]).join('、'))||'未設定'}</dd></div>
      <div><dt>使用状態</dt><dd>${p.active?'使用中':'停止'}${p.mergedIntoId?` → 統合先 ${p.mergedIntoId}`:''}</dd></div>
      <div><dt>取扱</dt><dd>${handles}</dd></div>
      <div><dt>在庫</dt><dd>${p.hasStock||p.lotCount?`${num(p.stockQty)}着 / ${yen(p.stockAmount)}（ロット${num(p.lotCount)}）`:'なし'}</dd></div>
    </dl>
  </article>`
}

function groupBlock(group, state){
  const note=group.autoSame
    ? `<p class="excelhint">${esc(group.note)}</p>`
    : `<p class="excelhint merge-special-note">${esc(group.note)}</p>`
  const cards=(group.products||[]).map(p=>{
    const selected=!!state.selected[p.id]
    const keepId=state.keepId
    return productCardHtml(p,{selected,keepId})
  }).join('')
  return `<section class="merge-group" data-kind="${esc(group.kind)}" data-code="${esc(group.code)}">
    <h3>${esc(group.codeDisplay||group.code)} <span class="sub">商品 ${num((group.products||[]).length)}件</span></h3>
    ${note}
    <div class="merge-cards">${cards}</div>
  </section>`
}

function choiceSelect(id, choices, current, allowEdit){
  const opts=(choices||[]).map(v=>`<option value="${esc(v)}" ${String(v)===String(current)?'selected':''}>${esc(v===''?'（空欄）':v)}</option>`).join('')
  return `<label>${esc(id==='mergeName'?'商品名':id==='mergeColor'?'色':'カテゴリ')}
    <select id="${id}">${opts}${allowEdit?`<option value="__edit__">手入力…</option>`:''}</select>
  </label>
  <label class="merge-edit" ${String(current)&&!(choices||[]).includes(current)?'':'hidden'}>
    手入力<input id="${id}Edit" value="${esc(current)}">
  </label>`
}

async function openProductMerge(deps){
  const {api, toast, boot, stockCanWrite, actorName}=deps
  if(!stockCanWrite()){toast('閲覧専用のため、商品の統合はできません。');return}
  const dlg=document.getElementById('dialog')
  if(!dlg)return
  let data
  try{
    data=await api('/api/products/duplicates')
  }catch(e){toast(e.message);return}

  const state={
    selected:{},
    keepId:null,
    phase:'pick', // pick | confirm
    preview:null,
    token:null,
    conflictResolutions:{},
  }

  const paint=async()=>{
    if(state.phase==='confirm'&&state.preview){
      paintConfirm()
      return
    }
    const normals=(data.normalGroups||[]).map(g=>groupBlock(g,state)).join('')||'<p>同じ品番の重複グループはありません。</p>'
    const specials=(data.specialSections||[]).map(g=>groupBlock(g,state)).join('')||''
    dlg.innerHTML=`<form class="merge-dialog"><div class="dialoghead"><h2>重複商品の整理</h2><button type="button" id="close" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        <p class="excelhint">同じ品番で複数の商品IDがあるグループを横並びで比較します。「別注」「不明」「品番なし」は通常の品番重複と分けて表示し、その理由だけでは同一商品と判断しません。統合せず残すこともできます。全件自動統合はしません。</p>
        <div class="merge-toolbar">
          <button type="button" id="mergePreview" class="primary" ${Object.keys(state.selected).length>=2?'':'disabled'}>選択した商品の統合内容を確認</button>
          <span class="sub">選択 ${num(Object.keys(state.selected).length)}件</span>
        </div>
        <h3>通常の品番重複</h3>
        ${normals}
        ${specials?`<h3>別注・不明・品番なし（手動確認）</h3>${specials}`:''}
      </div>
      <p id="error"></p>
      <div class="dialogactions"><button type="button" id="close2" class="quiet">閉じる</button></div>
    </form>`
    dlg.showModal()
    const close=()=>{dlg.close();dlg.innerHTML=''}
    dlg.querySelector('#close').onclick=close
    dlg.querySelector('#close2').onclick=close
    dlg.querySelectorAll('[data-merge-pick]').forEach(el=>{
      el.onchange=()=>{
        const pid=Number(el.dataset.mergePick)
        if(el.checked)state.selected[pid]=true
        else{
          delete state.selected[pid]
          if(Number(state.keepId)===pid)state.keepId=null
        }
        if(!state.keepId){
          const ids=Object.keys(state.selected).map(Number)
          if(ids.length)state.keepId=ids[0]
        }
        paint()
      }
    })
    dlg.querySelectorAll('input[name="mergeKeep"]').forEach(el=>{
      el.onchange=()=>{
        state.keepId=Number(el.value)
        if(!state.selected[state.keepId])state.selected[state.keepId]=true
        paint()
      }
    })
    const prev=dlg.querySelector('#mergePreview')
    if(prev)prev.onclick=()=>runPreview()
  }

  const runPreview=async()=>{
    const productIds=Object.keys(state.selected).map(Number)
    if(productIds.length<2){
      toast('統合する商品を2件以上選んでください。統合しない場合はそのまま閉じてください。')
      return
    }
    if(!state.keepId||!state.selected[state.keepId])state.keepId=productIds[0]
    try{
      state.preview=await api('/api/products/merge/preview',{
        productIds,
        keepProductId:state.keepId,
      })
      state.token=crypto.randomUUID()
      state.conflictResolutions={}
      state.phase='confirm'
      paintConfirm()
    }catch(e){
      const err=document.getElementById('error')
      if(err)err.textContent=e.message
      else toast(e.message)
    }
  }

  const paintConfirm=()=>{
    const p=state.preview
    const supplierOpts=(p.suppliers||[]).map(s=>`<option value="${s.id}" ${Number(p.supplierId)===Number(s.id)?'selected':''}>${esc(s.name)}</option>`).join('')
    const subChecks=(p.suppliers||[]).map(s=>{
      const disabled=Number(p.supplierId)===Number(s.id)
      const checked=!disabled&&(p.subSupplierIds||[]).map(Number).includes(Number(s.id))
      return `<label class="check"><input type="checkbox" data-sub-sid="${s.id}" ${checked?'checked':''} ${disabled?'disabled':''}>${esc(s.name)}</label>`
    }).join('')||'<span class="sub">候補なし</span>'
    const sizeChecks=(p.sizeUnion||[]).map(sz=>{
      const checked=(p.sizes||[]).includes(sz)
      return `<label class="check"><input type="checkbox" data-merge-size="${esc(sz)}" ${checked?'checked':''}>${esc(sz)}</label>`
    }).join('')
    const moves=(p.handleMoves||[]).map(m=>{
      const conflict=(p.handleConflicts||[]).some(c=>c.sourceHandleId===m.sourceHandleId)
      let resolve=''
      if(conflict){
        const choice=state.conflictResolutions[m.sourceHandleId]||''
        const diffText=(m.diffs||[]).map(d=>`${d.label}: 残す側=${d.keep?'はい':'いいえ'} / 統合元=${d.source?'はい':'いいえ'}`).join('、')
        resolve=`<div class="merge-conflict">差異: ${esc(diffText)}
          <label><input type="radio" name="cr${m.sourceHandleId}" value="keep" ${choice==='keep'?'checked':''}> 残す商品の設定を採用</label>
          <label><input type="radio" name="cr${m.sourceHandleId}" value="source" ${choice==='source'?'checked':''}> 統合元の設定を採用</label>
        </div>`
      }
      return `<li>商品ID ${m.sourceProductId} → ${esc(m.customerName)} / ${esc(m.embroideryLabel)}
        （${m.action==='mergeInto'?'既存取扱へまとめ':'取扱を付け替え'}・ロット${num(m.lotCount)}・数量${num(m.stockQty)}）
        ${resolve}</li>`
    }).join('')||'<li>移動する取扱はありません</li>'

    dlg.innerHTML=`<form class="merge-dialog"><div class="dialoghead"><h2>統合の確認</h2><button type="button" id="close" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        <p class="excelhint">実行前の内容です。確定するまでデータベースは変わりません。失敗時は全体を取り消します。</p>
        <dl class="merge-dl merge-summary">
          <div><dt>統合対象</dt><dd>商品ID ${(p.productIds||[]).join('、')}</dd></div>
          <div><dt>残す商品ID</dt><dd>${p.keepProductId}</dd></div>
          <div><dt>品番</dt><dd>${esc(p.codeDisplay||p.code)}</dd></div>
          <div><dt>移動する取扱</dt><dd>${num((p.handleMoves||[]).length)}件</dd></div>
          <div><dt>在庫ロット</dt><dd>${num(p.totals&&p.totals.lotCount)}件 / 数量 ${num(p.totals&&p.totals.qty)} / 金額 ${yen(p.totals&&p.totals.amount)}</dd></div>
          <div><dt>未解決の差異</dt><dd>${num(p.unresolvedConflicts)}</dd></div>
        </dl>
        <div class="grid">
          ${choiceSelect('mergeName',p.nameChoices,p.name,true)}
          ${choiceSelect('mergeColor',p.colorChoices,p.color,true)}
          <label>カテゴリ<select id="mergeCategory">${(p.categoryChoices||['','制服','部品・資材']).map(v=>`<option value="${esc(v)}" ${String(v)===String(p.category)?'selected':''}>${esc(v||'（なし）')}</option>`).join('')}</select></label>
          <label>残す商品ID<select id="mergeKeepId">${(p.productIds||[]).map(id=>`<option value="${id}" ${Number(id)===Number(p.keepProductId)?'selected':''}>${id}</option>`).join('')}</select></label>
          <label>通常の仕入先<select id="mergeSupplier"><option value="">未設定</option>${supplierOpts}</select></label>
        </div>
        <fieldset class="merge-subs"><legend>サブ仕入先（通常以外）</legend>${subChecks}</fieldset>
        <fieldset class="merge-sizes"><legend>引き継ぐサイズ（表記は同一化しません）</legend>${sizeChecks}</fieldset>
        <h3>取扱・在庫の移動</h3>
        <ul class="merge-moves">${moves}</ul>
        <ul class="excelhint">${(p.notes||[]).map(n=>`<li>${esc(n)}</li>`).join('')}</ul>
      </div>
      <p id="error"></p>
      <div class="dialogactions">
        <button type="button" id="mergeBack" class="quiet">選び直す</button>
        <button type="button" id="mergeRun" class="primary">この内容で統合する</button>
      </div>
    </form>`
    dlg.showModal()
    const err=()=>document.getElementById('error')
    dlg.querySelector('#close').onclick=()=>{dlg.close();dlg.innerHTML=''}
    dlg.querySelector('#mergeBack').onclick=()=>{state.phase='pick';state.preview=null;paint()}

    const syncChoice=(selId,editId)=>{
      const sel=dlg.querySelector('#'+selId)
      const editWrap=sel&&sel.parentElement&&sel.parentElement.nextElementSibling
      const edit=dlg.querySelector('#'+editId)
      if(!sel)return
      const apply=()=>{
        if(sel.value==='__edit__'){
          if(editWrap)editWrap.hidden=false
        }else if(editWrap)editWrap.hidden=true
      }
      sel.onchange=apply
      apply()
      return ()=>{
        if(sel.value==='__edit__')return (edit&&edit.value)||''
        return sel.value
      }
    }
    const readName=syncChoice('mergeName','mergeNameEdit')
    const readColor=syncChoice('mergeColor','mergeColorEdit')

    const refreshPreview=async()=>{
      const subSupplierIds=[...dlg.querySelectorAll('[data-sub-sid]:checked')].map(el=>Number(el.dataset.subSid))
      const sizes=[...dlg.querySelectorAll('[data-merge-size]:checked')].map(el=>el.dataset.mergeSize)
      const supplierRaw=dlg.querySelector('#mergeSupplier').value
      const body={
        productIds:p.productIds,
        keepProductId:Number(dlg.querySelector('#mergeKeepId').value),
        name:readName(),
        color:readColor(),
        category:dlg.querySelector('#mergeCategory').value,
        supplierId:supplierRaw?Number(supplierRaw):null,
        subSupplierIds,
        sizes,
      }
      try{
        state.preview=await api('/api/products/merge/preview',body)
        // 差異解決は維持
        paintConfirm()
      }catch(e){err().textContent=e.message}
    }
    dlg.querySelector('#mergeKeepId').onchange=()=>refreshPreview()
    dlg.querySelector('#mergeSupplier').onchange=()=>refreshPreview()
    dlg.querySelectorAll('[data-sub-sid]').forEach(el=>el.onchange=()=>refreshPreview())
    dlg.querySelectorAll('[data-merge-size]').forEach(el=>el.onchange=()=>refreshPreview())
    dlg.querySelectorAll('[name^="cr"]').forEach(el=>{
      el.onchange=()=>{
        const hid=Number(el.name.slice(2))
        state.conflictResolutions[hid]=el.value
      }
    })

    dlg.querySelector('#mergeRun').onclick=async()=>{
      if(!confirm('この内容で商品を統合します。よろしいですか？'))return
      const subSupplierIds=[...dlg.querySelectorAll('[data-sub-sid]:checked')].map(el=>Number(el.dataset.subSid))
      const sizes=[...dlg.querySelectorAll('[data-merge-size]:checked')].map(el=>el.dataset.mergeSize)
      const supplierRaw=dlg.querySelector('#mergeSupplier').value
      const conflictResolutions={}
      Object.keys(state.conflictResolutions).forEach(k=>{conflictResolutions[k]=state.conflictResolutions[k]})
      // 画面上のラジオも拾う
      dlg.querySelectorAll('[name^="cr"]:checked').forEach(el=>{
        conflictResolutions[el.name.slice(2)]=el.value
      })
      const body={
        token:state.token||crypto.randomUUID(),
        fingerprint:state.preview.fingerprint,
        productIds:p.productIds,
        keepProductId:Number(dlg.querySelector('#mergeKeepId').value),
        name:readName(),
        color:readColor(),
        category:dlg.querySelector('#mergeCategory').value,
        supplierId:supplierRaw?Number(supplierRaw):null,
        subSupplierIds,
        sizes,
        conflictResolutions,
        _actor:{name:actorName||''},
      }
      try{
        const result=await api('/api/products/merge',body)
        dlg.close();dlg.innerHTML=''
        const msg=result.already
          ?'この統合は保存済みです。二重には実行していません。'
          :`統合しました。数量 ${num(result.beforeQty)}→${num(result.afterQty)}、金額 ${yen(result.beforeAmount)}→${yen(result.afterAmount)}`
        toast(msg,6000)
        if(typeof boot==='function')await boot()
      }catch(e){
        if(e.status===409){
          err().textContent=e.message||'対象データが変わったため、確認し直してください。'
          try{
            state.preview=await api('/api/products/merge/preview',{
              productIds:p.productIds,
              keepProductId:Number(dlg.querySelector('#mergeKeepId').value),
            })
            state.token=crypto.randomUUID()
            paintConfirm()
          }catch(_){}
          return
        }
        err().textContent=e.message
      }
    }
  }

  await paint()
}

global.ProductMerge={open:openProductMerge}
})(window)
