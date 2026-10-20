/** 旧商品台帳CSV取込（登録・設定 → 商品）。初期在庫用「旧CSV取込」とは別。
 * 通常品番は1品番=1商品。名差・仕入先差は品番ごとの比較UIで解決する。
 */
(function(){
  const STATUS_ORDER=['create','merge','size_add','reuse','unchanged','needs_review','hold','excluded']
  const STATUS_LABELS={
    create:'新規',merge:'統合して登録',size_add:'サイズ追加',reuse:'既存使用',
    unchanged:'変更なし',needs_review:'要確認',hold:'保留',excluded:'対象外'
  }
  const PAGE_SIZE=80
  // Keep in sync with stock-desk/upload_limits.py (POST body 12MB; raw CSV decode 8MB).
  const MAX_RAW_FILE_BYTES=8_000_000
  const MAX_POST_BODY_BYTES=12_000_000
  const OVERSIZE_MSG='ファイル容量が上限を超えています'
  let ui=null

  function ensureState(){
    if(!ui){
      ui={
        file:null,fileB64:'',fileName:'',fileSize:0,
        auxFile:null,auxFileB64:'',auxFileName:'',auxFileSize:0,
        history:null,historyError:'',preview:null,result:null,
        q:'',statusFilter:'needs_review',page:1,
        selections:{},skuDecisions:{},commitToken:'',busy:false,open:false,
        api:null,toast:null,boot:null,stockCanWrite:null,actorName:''
      }
    }
    return ui
  }

  function previewPayload(st){
    const body={
      name:st.fileName,file:st.fileB64,selections:st.selections,skuDecisions:st.skuDecisions,
      _canWrite:true,_actor:{name:st.actorName||''}
    }
    if(st.auxFileB64){
      body.auxFile=st.auxFileB64
      body.auxName=st.auxFileName||'初期在庫.csv'
    }
    return body
  }

  function applyAuxDefaultsForMerge(st,code,group){
    const d=skuDecision(st,code)
    const aux=group&&group.auxPrimary
    if(!aux||!aux.applicable)return
    const patch={}
    if(!d.primarySupplierManual){
      if(aux.autoPrimarySupplier){
        if(d.primarySupplier&&d.primarySupplier!==aux.autoPrimarySupplier&&!d.primarySupplierManual){
          // 再照合で自動結果が変わった場合: 手動でなければ自動へ寄せる
        }
        patch.primarySupplier=aux.autoPrimarySupplier
        patch.primarySupplierManual=false
      }
    }else if(aux.autoPrimarySupplier&&d.primarySupplier&&d.primarySupplier!==aux.autoPrimarySupplier){
      patch.auxPrimaryConflict=true
    }
    if(!d.officialName&&aux.suggestedOfficialName&&!aux.officialNameNeedsChoice){
      patch.officialName=aux.suggestedOfficialName
    }
    if(Object.keys(patch).length)setSkuDecision(st,code,patch)
  }

  function readFileAsBase64(file){
    return new Promise((resolve,reject)=>{
      const reader=new FileReader()
      reader.onload=()=>{
        const buf=reader.result
        const bytes=new Uint8Array(buf)
        let binary=''
        const chunk=0x8000
        for(let i=0;i<bytes.length;i+=chunk){
          binary+=String.fromCharCode.apply(null,bytes.subarray(i,i+chunk))
        }
        resolve(btoa(binary))
      }
      reader.onerror=()=>reject(Error('ファイルを読めませんでした。'))
      reader.readAsArrayBuffer(file)
    })
  }

  function estimatePostBodyBytes(fileName,b64){
    try{return JSON.stringify({name:fileName||'',file:b64||''}).length}
    catch(_){return (b64||'').length+((fileName||'').length)+64}
  }

  function assertUploadSize(file,b64){
    if(file&&file.size>MAX_RAW_FILE_BYTES)throw Error(OVERSIZE_MSG)
    if(b64&&estimatePostBodyBytes(file&&file.name||'',b64)>MAX_POST_BODY_BYTES)throw Error(OVERSIZE_MSG)
  }

  function apiErrorMessage(e,fallback){
    if(!e)return fallback
    const fromPayload=e.payload&&(e.payload.error||e.payload.message)
    let msg=fromPayload||e.message||''
    if(/failed to fetch|networkerror|load failed|communication/i.test(msg)){
      return fallback||'通信できませんでした。登録は完了していません。サーバーを再起動してからやり直してください。'
    }
    if(e.payload&&e.payload.registered===false&&msg){
      return msg
    }
    if(!msg||msg==='処理できませんでした。')return fallback||'処理できませんでした。'
    return msg
  }

  function filteredRows(st){
    const rows=(st.preview&&st.preview.rows)||[]
    const q=(st.q||'').trim().toLowerCase()
    const filter=st.statusFilter||''
    return rows.filter(r=>{
      if(filter&&r.status!==filter)return false
      if(!q)return true
      const csv=r.csv||{}
      const hay=[
        csv.customer,csv.supplier,csv.code,csv.codeDisplay,csv.productName,csv.size,
        r.statusLabel,(r.reasons||[]).join(' '),
        r.product&&r.product.id,r.handle&&r.handle.id
      ].join(' ').toLowerCase()
      return hay.includes(q)
    })
  }

  function countLine(counts){
    if(!counts)return ''
    return STATUS_ORDER.map(k=>{
      const n=counts[k]||0
      if(!n&&k==='merge')return null
      return `${STATUS_LABELS[k]} ${n}件`
    }).filter(Boolean).join('　')
  }

  function plannedLine(st){
    const p=st.preview&&st.preview.plannedCounts
    if(!p)return ''
    const auto=p.autoMergedSkuGroups!=null
      ?`　自動まとめ ${p.autoMergedSkuGroups||0}品番/${p.autoMergedCsvRows||0}行`
      :''
    return `予定: 新規商品 ${p.productsToCreate||0}　再利用 ${p.productsToReuse||0}　統合 ${p.productsToMerge||0}　保留行 ${p.rowsDeferred||0}　サイズ追加 ${p.sizesToAdd||0}　取扱新規 ${p.handlesToCreate||0}　（CSV行 ${p.csvRows||0}${auto}）`
  }

  function autoMergePlansHtml(st){
    const plans=(st.preview&&st.preview.autoMergePlans)||[]
    if(!plans.length)return ''
    const p=st.preview.plannedCounts||{}
    const sample=plans.filter(x=>(x.sizeCount||0)>1||(x.customerCount||0)>1).slice(0,12)
    const more=Math.max(0,(p.autoMergedSkuGroups||plans.length)-sample.length)
    const cards=sample.map(plan=>{
      const sizes=(plan.sizes||[]).slice(0,12).map(esc).join('、')
      const sizeMore=(plan.sizes||[]).length>12?` …他${(plan.sizes||[]).length-12}`:''
      const custs=(plan.customers||[]).slice(0,4).map(esc).join('、')
      const custMore=(plan.customers||[]).length>4?` …他${(plan.customers||[]).length-4}`:''
      return `<article class="sku-auto-plan">
        <strong>${esc(plan.label||'新規商品')}</strong>
        <span>品番 ${esc(plan.code||'')}　${esc(plan.productName||'')}　仕入 ${esc(plan.supplier||'未設定')}</span>
        <span class="sub">取扱顧客 ${custs||'—'}${custMore}　予定サイズ ${sizes||'—'}${sizeMore}　（CSV ${plan.csvRowCount||0}行 → サイズ ${plan.sizeCount||0} / 顧客 ${plan.customerCount||0}）</span>
      </article>`
    }).join('')
    return `<div class="sku-auto-panel">
      <h3>サイズ・顧客・中古標記差の自動まとめ（要確認ではない）</h3>
      <p class="excelhint">品番・仕入先が同じで、サイズや顧客だけ違う行、または商品名の差が先頭「＊/*」中古標記だけの行は、1商品としてまとめます。公式名は標記なしを優先し、CSV原文は照合用別名に残します。LL≡2L の同一化や、CSVに無い中間サイズの追加はしません。</p>
      <p class="excelhint">自動まとめ ${p.autoMergedSkuGroups||0} 品番 / ${p.autoMergedCsvRows||0} 行（下は複数サイズまたは複数顧客の例）</p>
      ${cards||'<p class="sub">複数サイズの自動まとめ例はありません（単一行の品番のみ）。</p>'}
      ${more?`<p class="excelhint">他 ${more} 品番も同様に自動まとめ予定です。</p>`:''}
    </div>`
  }

  function historyBlock(st){
    if(!st.fileB64)return ''
    if(st.historyError){
      return `<p class="excelhint notice-inline">${esc(st.historyError)}</p>`
    }
    if(!st.history)return ''
    const prev=st.history.previousImports||(st.preview&&st.preview.previousImports)||[]
    if(!prev.length)return '<p class="excelhint">このファイルの過去取込記録はありません。</p>'
    const items=prev.map(p=>{
      const when=p.committedAt||p.previewAt||p.createdAt||''
      const done=p.committedAt?'登録済':'プレビューのみ'
      const cnt=p.result&&p.result.counts&&p.result.counts.committedRows!=null?`確定${p.result.counts.committedRows}件`:''
      return `<li>${esc(when)}　${esc(done)}　${esc(p.actorName||'')}　${esc(cnt)}</li>`
    }).join('')
    return `<div class="list-note"><strong>前回取込情報（参考）</strong><ul class="legacy-csv-history">${items}</ul></div>`
  }

  function applySelectionToGroup(st,rowNo,patch){
    const row=((st.preview&&st.preview.rows)||[]).find(r=>Number(r.rowNo)===Number(rowNo))
    if(!row)return 0
    const gk=row.matchGroupKey
    let n=0
    ;(st.preview.rows||[]).forEach(r=>{
      if(r.matchGroupKey!==gk)return
      const key=String(r.rowNo)
      st.selections[key]={...(st.selections[key]||{}),...patch,matchGroupKey:gk}
      n+=1
    })
    return n
  }

  function skuDecision(st,code){
    return st.skuDecisions[code]||{}
  }

  function setSkuDecision(st,code,patch){
    st.skuDecisions[code]={...(st.skuDecisions[code]||{}),...patch}
  }

  function auxSupplierPanelHtml(st,g,d){
    const aux=g.auxPrimary
    if(!aux||!aux.applicable)return ''
    const resolved=g.primarySupplierResolved||{}
    const rows=(aux.supplierJudgments||[]).map(j=>{
      const role=j.plannedRoleLabel||'—'
      const sizes=(j.auxSizeStocks||[]).map(s=>`${esc(s.size||'—')}:${esc(s.qtyDisplay)}`).join('、')||'—'
      const match=j.auxStatus==='none'
        ?'<span class="aux-none">該当登録なし</span>'
        :`<span class="aux-matched">${esc(j.auxStatusLabel||'該当あり')}</span>`
      return `<tr>
        <td>${esc(j.supplier||'')}</td>
        <td>${esc(j.ledgerName||(j.ledgerNames||[]).join(' / ')||'—')}</td>
        <td>${match}</td>
        <td class="aux-sizes">${sizes}</td>
        <td><strong>${esc(role)}</strong></td>
      </tr>`
    }).join('')
    const reason=aux.autoReason||aux.manualReason||''
    const conflict=(d.primarySupplierManual&&aux.autoPrimarySupplier&&d.primarySupplier&&d.primarySupplier!==aux.autoPrimarySupplier)
      ||resolved.conflictWithAuto
    const existingPrimary=resolved.existingPrimarySupplier
      ||((g.existingProducts||[]).find(p=>Number(p.id)===Number(d.keepProductId))||{}).supplierName||''
    const chosen=d.primarySupplier||resolved.primarySupplier||aux.autoPrimarySupplier||''
    const changeNeeded=!!(existingPrimary&&chosen&&existingPrimary!==chosen&&(d.keepProductId||g.existingCount===1))
    return `<div class="sku-aux-panel">
      <h4>初期在庫CSVによる通常仕入先判定</h4>
      <p class="excelhint">数量最大やCSV先頭行では選びません。正の在庫が1社だけ→通常。正の在庫がなく該当行が1社だけ→通常。それ以外は手動選択です。</p>
      ${reason?`<p class="excelhint">${esc(reason)}</p>`:''}
      ${conflict?`<p class="notice">自動判定「${esc(aux.autoPrimarySupplier)}」と手動選択「${esc(d.primarySupplier)}」が異なります。手動を優先します（上書きしません）。</p>`:''}
      <table class="sku-aux-table">
        <thead><tr><th>仕入先</th><th>台帳側の商品名</th><th>初期在庫CSV</th><th>サイズ別在庫</th><th>予定</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${changeNeeded?`<label class="check primary-change-confirm"><input type="checkbox" data-pl-confirm-primary="${esc(g.code)}" ${d.confirmPrimaryChange?'checked':''} ${st.busy?'disabled':''}>
        通常仕入先を「${esc(existingPrimary)}」→「${esc(chosen)}」へ変更することを確認する（黙って変更しません）</label>`:''}
    </div>`
  }

  function skuGroupsHtml(st){
    const groups=((st.preview&&st.preview.skuGroups)||[]).filter(g=>g.needsDecision)
    if(!groups.length)return ''
    const cards=groups.map(g=>{
      const d=skuDecision(st,g.code)
      const decision=d.decision||''
      const names=[...new Set((g.variants||[]).map(v=>v.productName).filter(Boolean))]
      const suppliers=[...new Set((g.variants||[]).map(v=>v.supplier).filter(Boolean))]
      const existing=(g.existingProducts||[]).map(p=>{
        return `<label class="check"><input type="radio" name="plKeep-${esc(g.code)}" data-pl-keep="${esc(g.code)}" value="${p.id}" ${Number(d.keepProductId)===Number(p.id)?'checked':''} ${st.busy?'disabled':''}>
          ID ${p.id}　${esc(p.name||'')}${p.color?` / ${esc(p.color)}`:''}${p.supplierName?`　仕入 ${esc(p.supplierName)}`:''}</label>`
      }).join('')||'<span class="sub">登録済なし（新規1商品）</span>'
      const nameOpts=names.map(n=>`<option value="${esc(n)}" ${d.officialName===n?'selected':''}>${esc(n)}</option>`).join('')
      const effectivePrimary=d.primarySupplier||(g.auxPrimary&&g.auxPrimary.autoPrimarySupplier)||''
      const supOpts=suppliers.map(s=>`<option value="${esc(s)}" ${effectivePrimary===s?'selected':''}>${esc(s)}</option>`).join('')
      const variantPlans=(g.variants||[]).map(v=>{
        const plans=(d.plans&&typeof d.plans==='object')?d.plans:{}
        const plan=plans[v.key]||{}
        return `<div class="sku-variant-plan" data-variant-key="${esc(v.key)}">
          <strong>${esc(v.productName||'（名なし）')}</strong>
          <span class="sub">仕入 ${esc(v.supplier||'未設定')}　サイズ ${(v.sizes||[]).map(esc).join('、')||'—'}　顧客 ${(v.customers||[]).slice(0,3).map(esc).join('、')}${(v.customers||[]).length>3?'…':''}</span>
          <label>整理方法<select data-pl-sep-approach="${esc(g.code)}" data-pl-sep-key="${esc(v.key)}" ${st.busy||decision!=='separate'?'disabled':''}>
            <option value="">選択…</option>
            <option value="change_code" ${plan.approach==='change_code'?'selected':''}>品番を修正して登録</option>
            <option value="as_parts" ${plan.approach==='as_parts'?'selected':''}>部品・資材として整理</option>
            <option value="as_blank" ${plan.approach==='as_blank'?'selected':''}>品番なしとして整理</option>
            <option value="defer" ${plan.approach==='defer'?'selected':''}>この内容は保留</option>
          </select></label>
          <label>修正品番<input data-pl-sep-code="${esc(g.code)}" data-pl-sep-key="${esc(v.key)}" value="${esc(plan.newCode||'')}" placeholder="別の品番" ${st.busy||decision!=='separate'||plan.approach!=='change_code'?'disabled':''}></label>
        </div>`
      }).join('')
      const mergeSources=(g.existingProducts||[]).filter(p=>Number(p.id)!==Number(d.keepProductId)).map(p=>{
        const checked=(d.mergeSourceIds||[]).map(Number).includes(Number(p.id))||(decision==='merge'&&g.existingCount>1&&d.keepProductId)
        return `<label class="check"><input type="checkbox" data-pl-merge-src="${esc(g.code)}" value="${p.id}" ${checked?'checked':''} ${st.busy||decision!=='merge'||!d.keepProductId?'disabled':''}>統合元 ID ${p.id}</label>`
      }).join('')
      const auxPanel=auxSupplierPanelHtml(st,g,d)
      return `<article class="sku-group-card" data-sku-code="${esc(g.code)}">
        <header><strong>品番 ${esc(g.codeDisplay||g.code)}</strong>
          <span class="excelhint">${esc(g.applyScope||'')}</span></header>
        <p>CSV内容 ${(g.variants||[]).length} 種　行 ${g.rowCount}　登録済 ${g.existingCount}</p>
        ${auxPanel}
        <div class="bar sku-decision-btns">
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="merge" class="${decision==='merge'?'primary':''}" ${st.busy?'disabled':''}>同じ商品としてまとめる</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="separate" class="${decision==='separate'?'primary':''}" ${st.busy?'disabled':''}>別物のため個別に整理する</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="hold" class="${decision==='hold'?'primary':''}" ${st.busy?'disabled':''}>今回は保留する</button>
        </div>
        ${decision==='merge'?`<div class="sku-merge-form">
          <label>公式商品名<select data-pl-official-name="${esc(g.code)}" ${st.busy?'disabled':''}><option value="">選択または下で編集…</option>${nameOpts}</select></label>
          <label>公式名（編集可）<input data-pl-official-name-edit="${esc(g.code)}" value="${esc(d.officialName||'')}" ${st.busy?'disabled':''}></label>
          <label>色<input data-pl-official-color="${esc(g.code)}" value="${esc(d.color||'')}" ${st.busy?'disabled':''}></label>
          <label>通常仕入先<select data-pl-primary-sup="${esc(g.code)}" ${st.busy?'disabled':''}><option value="">未指定</option>${supOpts}</select></label>
          ${d.primarySupplierManual?'<p class="excelhint">通常仕入先は手動選択です（初期在庫CSVの再読込でも上書きしません）。</p>':(g.auxPrimary&&g.auxPrimary.autoPrimarySupplier?`<p class="excelhint">通常仕入先は初期在庫CSVから自動提案: ${esc(g.auxPrimary.autoPrimarySupplier)}</p>`:'')}
          <fieldset><legend>残す商品（登録済が複数のとき必須）</legend>${existing}</fieldset>
          ${g.existingCount>1?`<fieldset><legend>統合する商品（残す以外）</legend>${mergeSources||'<span class="sub">残す商品を先に選んでください</span>'}</fieldset>`:''}
          <p class="excelhint">旧商品名・仕入先は照合用の別名として残します。サイズは LL≡2L などの同一化はしません。取扱・加工仕様は商品に付けたまま残します。</p>
        </div>`:''}
        ${decision==='separate'?`<div class="sku-separate-form">
          <p class="excelhint">同じ通常品番のまま別商品として新規登録はできません。品番修正・部品扱い・保留のいずれかを内容ごとに選んでください。</p>
          ${variantPlans}
        </div>`:''}
        ${decision==='hold'?`<div class="sku-hold-form">
          <label>保留理由<input data-pl-defer-reason="${esc(g.code)}" value="${esc(d.deferReason||'')}" placeholder="例: 制服本体と加工サービスの切り分けが未確定" ${st.busy?'disabled':''}></label>
          <p class="excelhint">この品番の対象行は今回の登録から除外します。元行はプレビューに残ります。</p>
        </div>`:''}
      </article>`
    }).join('')
    return `<div class="sku-groups-panel">
      <h3>品番ごとの比較（通常品番）</h3>
      <p class="excelhint">商品名／色／仕入先など判断が必要な差だけここで一度決めます。サイズ・顧客・先頭「＊/*」中古標記だけの差は要確認に出さず、同じ商品へ自動まとめします。決定は品番単位で対象行に適用されます（適用範囲は各カードに表示）。</p>
      ${cards}
    </div>`
  }

  function rowActions(st,r){
    const sel=st.selections[String(r.rowNo)]||{}
    const bits=[]
    if(r.status==='needs_review'&&r.product&&r.product.candidates&&r.product.candidates.length&&!r.skuDecisionNeeded){
      const opts=r.product.candidates.map(c=>{
        const cust=(c.customerNames&&c.customerNames.length)?c.customerNames.join('、'):''
        const sizes=(c.sizes||[]).join('、')
        const bits2=[
          `ID ${c.id}`,
          c.codeDisplay||c.code||'品番なし',
          cust?`顧客 ${cust}`:'',
          c.name||'',
          c.color?`色 ${c.color}`:'色（空）',
          c.supplierName?`仕入先 ${c.supplierName}`:'',
          sizes?`サイズ ${sizes}`:''
        ].filter(Boolean)
        return `<option value="${c.id}" ${Number(sel.productId)===Number(c.id)?'selected':''}>${esc(bits2.join(' / '))}</option>`
      }).join('')
      const allowNew=!(r.product&&r.csv&&r.csv.code&&!r.product.blankSku&&r.csv.code!=='別注'&&r.csv.code!=='不明')
      bits.push(`<label>商品<select data-pl-prod="${r.rowNo}" ${st.busy?'disabled':''}><option value="">選択…</option>${opts}${allowNew?`<option value="__new__" ${sel.forceCreate?'selected':''}>新規として登録</option>`:''}</select></label>`)
    }
    if(r.status==='needs_review'&&r.handle&&r.handle.candidates&&r.handle.candidates.length){
      const opts=r.handle.candidates.map(c=>`<option value="${c.id}" ${Number(sel.handleId)===Number(c.id)?'selected':''}>${esc(c.embroideryLabel||c.embroidery||'')}</option>`).join('')
      bits.push(`<label>取扱<select data-pl-handle="${r.rowNo}" ${st.busy?'disabled':''}><option value="">選択…</option>${opts}</select></label>`)
    }
    if(r.product&&r.product.blankSku){
      const cur=sel.category!=null?sel.category:(r.product.category||'')
      const opts=(st.preview&&st.preview.categoryOptions)||['','制服','部品・資材']
      bits.push(`<label>区分<select data-pl-cat="${r.rowNo}" ${st.busy?'disabled':''}>${opts.map(v=>`<option value="${esc(v)}" ${cur===v?'selected':''}>${esc(v||'（なし）')}</option>`).join('')}</select></label>`)
    }
    if(r.skuDecisionNeeded)bits.push('<span class="excelhint">↑品番比較で決定</span>')
    if(r.matchGroupCount>1)bits.push(`<span class="excelhint">同品番 ${r.matchGroupCount}行</span>`)
    return bits.join(' ')
  }

  function targetCell(r){
    const p=r.product||{}
    const h=r.handle||{}
    const plan=r.productPlan||{}
    const bits=[]
    if(plan.label)bits.push(esc(plan.label))
    else if(p.id)bits.push(`商品ID ${p.id}`)
    else if(p.action==='create')bits.push('新規商品')
    if(p.id&&plan.label!=='既存商品を使用')bits.push(`商品ID ${p.id}`)
    if(p.officialName)bits.push(`公式名 ${esc(p.officialName)}`)
    if(plan.sizes&&plan.sizes.length){
      const shown=plan.sizes.slice(0,8).map(esc).join('、')
      const more=plan.sizes.length>8?` …他${plan.sizes.length-8}`:''
      bits.push(`予定サイズ ${shown}${more}`)
    }else if(p.sizeAction==='add'){
      bits.push(`サイズ追加 ${esc((r.csv||{}).size||'')}`)
    }
    if(plan.customers&&plan.customers.length>1){
      bits.push(`取扱顧客 ${plan.customerCount}件`)
    }
    if(p.subSupplierAdd)bits.push('サブ仕入先追加（通常は変更なし）')
    if(p.mergeSourceIds&&p.mergeSourceIds.length)bits.push(`統合元 ${p.mergeSourceIds.join(',')}`)
    if(h.id)bits.push(`取扱 ${h.id}`)
    else if(h.action==='create')bits.push('取扱新規（加工未整理）')
    else if(h.embroideryLabel||h.embroidery)bits.push(esc(h.embroideryLabel||h.embroidery))
    if(r.usedMarkerAutoMerged)bits.push('中古標記まとめ')
    else if(r.skuAutoMerged&&(r.matchGroupCount||0)>1)bits.push('サイズ自動まとめ')
    if(r.status==='hold')bits.push(`保留 ${esc(r.deferReason||'')}`)
    return bits.join(' / ')||'—'
  }

  function tableHtml(st){
    const all=filteredRows(st)
    const pages=Math.max(1,Math.ceil(all.length/PAGE_SIZE))
    if(st.page>pages)st.page=pages
    const start=(st.page-1)*PAGE_SIZE
    const pageRows=all.slice(start,start+PAGE_SIZE)
    const body=pageRows.map(r=>{
      const csv=r.csv||{}
      const reason=(r.reasons||[]).join(' ')
      return `<tr class="legacy-status-${esc(r.status)}">
        <td class="lc-row">${r.rowNo}</td>
        <td class="lc-status">${esc(r.statusLabel||'')}</td>
        <td class="lc-customer">${esc(csv.customer||'')}</td>
        <td class="lc-customer">${esc(csv.supplier||'')}</td>
        <td class="lc-code">${esc(csv.codeDisplay||csv.code||'')}</td>
        <td class="lc-product">${esc(csv.productName||'')}${csv.usedMarkerPossible?' <span class="badge">＊中古候補</span>':''}</td>
        <td class="lc-size">${esc(csv.size||'')}</td>
        <td class="lc-handle">${targetCell(r)}</td>
        <td class="lc-detail"><span class="lc-clip">${esc(reason)}</span></td>
        <td class="lc-actions">${rowActions(st,r)}</td>
      </tr>`
    }).join('')||'<tr><td colspan="10">該当する行はありません。</td></tr>'
    return `<div class="legacy-csv-table-wrap"><table class="legacy-csv-table product-ledger-table">
      <thead><tr><th>行</th><th>判定</th><th>顧客</th><th>仕入先</th><th>品番</th><th>商品名</th><th>サイズ</th><th>登録先</th><th>理由</th><th>選択</th></tr></thead>
      <tbody>${body}</tbody></table></div>
      <div class="bar legacy-csv-pager">
        <button type="button" id="plPrev" ${st.page<=1||st.busy?'disabled':''}>前へ</button>
        <span>${st.page} / ${pages}ページ（表示 ${all.length}件）</span>
        <button type="button" id="plNext" ${st.page>=pages||st.busy?'disabled':''}>次へ</button>
      </div>`
  }

  function resultHtml(st){
    const r=st.result
    if(!r)return ''
    const c=r.counts||{}
    const s=r.confirmSummary||{}
    return `<div class="notice legacy-csv-result">
      <strong>${r.already?'同じ操作は登録済みです':'登録が完了しました'}</strong>
      <p>新規商品 ${c.productsCreated||0}　再利用行 ${c.reused||0}　統合元 ${c.productsMerged||0}
     　サイズ追加 ${c.sizesAdded||0}　取扱新規 ${c.handlesCreated||0}
     　顧客 ${c.customersCreated||0}　仕入先 ${c.suppliersCreated||0}　サブ仕入先 ${c.subSuppliersAdded||0}
     　確定行 ${c.committedRows||0}　保留 ${c.deferred||r.deferredCount||0}　要確認残 ${r.reviewRemaining||0}　対象外 ${r.excludedCount||0}</p>
      ${s.csvRows!=null?`<p class="excelhint">確定時サマリ: CSV行 ${s.csvRows} / 新規商品 ${s.productsToCreate} / 再利用 ${s.productsToReuse} / 統合 ${s.productsToMerge}（行数≠新規商品数）</p>`:''}
      <p>在庫数量・金額・履歴: ${r.stockUnchanged?'変化なし':'要確認'}</p>
    </div>`
  }

  function filePickHtml(st){
    const label=st.fileName?'台帳CSVを変更':'商品台帳CSVを選ぶ'
    const sizeHint=st.fileName&&st.fileSize?`（${Number(st.fileSize).toLocaleString('ja-JP')} バイト）`:''
    const auxLabel=st.auxFileName?'初期在庫CSVを変更':'初期在庫CSV（任意）'
    const auxHint=st.auxFileName&&st.auxFileSize?`（${Number(st.auxFileSize).toLocaleString('ja-JP')} バイト）`:''
    const auxMeta=st.preview&&st.preview.auxOpeningStock
    return `<div class="product-ledger-files">
      <div class="bar product-ledger-filebar">
        <input type="file" id="plFile" class="product-ledger-file-input" accept=".csv,text/csv" ${st.busy?'disabled':''}>
        <button type="button" id="plPickFile" ${st.busy?'disabled':''}>${esc(label)}</button>
        <span class="excelhint product-ledger-filename" id="plFileLabel">${esc(st.fileName||'未選択')}${sizeHint?` ${esc(sizeHint)}`:''}${st.preview?`（${esc(st.preview.encoding||'')}）`:''}</span>
      </div>
      <div class="bar product-ledger-filebar">
        <input type="file" id="plAuxFile" class="product-ledger-file-input" accept=".csv,text/csv" ${st.busy?'disabled':''}>
        <button type="button" id="plPickAux" ${st.busy?'disabled':''}>${esc(auxLabel)}</button>
        <span class="excelhint product-ledger-filename">${esc(st.auxFileName||'未選択（なくても可）')}${auxHint?` ${esc(auxHint)}`:''}${auxMeta?`（${esc(auxMeta.encoding||'')} / ${auxMeta.rowCount||0}行）`:''}</span>
        ${st.auxFileB64?`<button type="button" id="plClearAux" class="quiet" ${st.busy?'disabled':''}>初期在庫CSVを外す</button>`:''}
      </div>
      <p class="excelhint">初期在庫用CSVは通常仕入先の判定補助だけに使います。在庫数量・単価・履歴は登録しません。未選択でも従来どおり台帳取込できます。</p>
      <div class="bar">
        <button type="button" id="plPreview" class="primary" ${!st.fileB64||st.busy?'disabled':''}>読み込み・照合</button>
      </div>
    </div>`
  }

  function paintDialog(){
    const st=ensureState()
    const dlg=document.getElementById('dialog')
    if(!dlg||!st.open)return
    const review=(st.preview&&st.preview.reviewCount)||0
    const skuPending=(st.preview&&st.preview.skuDecisionPending)||0
    const canWrite=typeof st.stockCanWrite==='function'?st.stockCanWrite():true
    dlg.classList.add('product-ledger-dialog','legacy-csv-detail-dialog')
    dlg.innerHTML=`<div class="dialoghead"><h2>旧商品台帳CSVを取り込む</h2><button type="button" id="plClose" class="quiet">閉じる</button></div>
      <div class="dialogbody legacy-csv-detail-body">
        <p class="merge-lead">商品と顧客取扱だけを登録します。在庫数量・仕入単価・初期在庫・入出庫履歴は変更しません。通常品番は<strong>1品番=1商品</strong>です。初期在庫用の「旧CSV取込」とは別機能です。プレビューまではDBを変更しません。</p>
        ${filePickHtml(st)}
        ${historyBlock(st)}
        ${st.preview?`<p class="excelhint">${esc(st.preview.note||'')}</p>
          ${st.preview.auxOpeningStock?`<p class="excelhint">初期在庫CSV（判定用）: ${esc(st.preview.auxOpeningStock.fileName||'')}　${esc(st.preview.auxOpeningStock.note||'')}</p>`:''}
          <p><strong>${countLine(st.preview.countsRaw)}</strong>　要確認 ${review}件　品番未決定 ${skuPending}件${(review||skuPending)? '（未解決のまま確定できません）':''}</p>
          <p class="excelhint">${esc(plannedLine(st))}</p>
          ${autoMergePlansHtml(st)}
          ${skuGroupsHtml(st)}
          <div class="bar">
            <input type="search" id="plQ" placeholder="検索（顧客・品番・商品名・理由）" value="${esc(st.q||'')}">
            <select id="plStatus">
              <option value="">すべて</option>
              ${STATUS_ORDER.map(k=>`<option value="${k}" ${st.statusFilter===k?'selected':''}>${STATUS_LABELS[k]}</option>`).join('')}
            </select>
            <button type="button" id="plRePreview" ${st.busy?'disabled':''}>選択を反映して再照合</button>
            <button type="button" id="plCommit" class="primary" ${st.busy||!canWrite||review>0||skuPending>0?'disabled':''}>確認して登録</button>
          </div>
          ${tableHtml(st)}`:''}
        ${resultHtml(st)}
      </div>`
    if(typeof dlg.showModal==='function'&&!dlg.open)dlg.showModal()
    bindDialog()
  }

  async function loadSelectedFile(st,f){
    if(!f)return
    assertUploadSize(f,null)
    st.file=f
    st.fileName=f.name
    st.fileSize=f.size||0
    st.preview=null
    st.result=null
    st.selections={}
    st.skuDecisions={}
    st.page=1
    st.history=null
    st.historyError=''
    st.busy=true
    paintDialog()
    try{
      const b64=await readFileAsBase64(f)
      assertUploadSize(f,b64)
      st.fileB64=b64
      try{
        st.history=await st.api('/api/product-ledger-csv/history',{name:f.name,file:st.fileB64})
        st.historyError=''
      }catch(he){
        st.history=null
        const detail=apiErrorMessage(he,'')
        st.historyError=detail&&detail!=='履歴を確認できませんでした。'
          ?`履歴を確認できませんでした。（${detail}）`
          :'履歴を確認できませんでした。'
        st.toast(st.historyError)
      }
    }catch(e){
      st.file=null;st.fileB64='';st.fileName='';st.fileSize=0;st.history=null;st.historyError=''
      st.toast(apiErrorMessage(e,'ファイルを読めませんでした。'))
    }
    st.busy=false
    paintDialog()
  }

  async function loadAuxFile(st,f){
    if(!f)return
    assertUploadSize(f,null)
    st.busy=true
    paintDialog()
    try{
      const b64=await readFileAsBase64(f)
      // 台帳+補助の合算が上限を超えないよう概算
      const combined=estimatePostBodyBytes(st.fileName||'',st.fileB64||'')+estimatePostBodyBytes(f.name,b64)
      if(combined>MAX_POST_BODY_BYTES)throw Error(OVERSIZE_MSG)
      st.auxFile=f
      st.auxFileName=f.name
      st.auxFileSize=f.size||0
      st.auxFileB64=b64
      st.preview=null
      st.result=null
      // 手動指定は残し、自動結果との差分は再照合で提示
      Object.keys(st.skuDecisions||{}).forEach(code=>{
        const d=st.skuDecisions[code]
        if(d&&!d.primarySupplierManual){
          delete d.primarySupplier
          delete d.auxPrimaryConflict
        }
      })
      st.toast('初期在庫CSVを読みました。台帳CSVとあわせて「読み込み・照合」してください。')
    }catch(e){
      st.auxFile=null;st.auxFileB64='';st.auxFileName='';st.auxFileSize=0
      st.toast(apiErrorMessage(e,'初期在庫CSVを読めませんでした。'))
    }
    st.busy=false
    paintDialog()
  }

  function syncMergeSources(st,code){
    const d=skuDecision(st,code)
    const keep=d.keepProductId
    const src=[]
    document.querySelectorAll(`[data-pl-merge-src="${CSS.escape(code)}"]`).forEach(el=>{
      if(el.checked&&Number(el.value)!==Number(keep))src.push(Number(el.value))
    })
    // 既存が複数で keep があるとき、未チェックでも残す以外を統合対象にする（明示）
    const group=((st.preview&&st.preview.skuGroups)||[]).find(g=>g.code===code)
    if(group&&group.existingCount>1&&keep&&!src.length){
      ;(group.existingProducts||[]).forEach(p=>{
        if(Number(p.id)!==Number(keep))src.push(Number(p.id))
      })
    }
    setSkuDecision(st,code,{mergeSourceIds:src})
  }

  function bindDialog(){
    const st=ensureState()
    const close=()=>{
      st.open=false
      const dlg=document.getElementById('dialog')
      if(dlg){dlg.close();dlg.classList.remove('product-ledger-dialog','legacy-csv-detail-dialog');dlg.innerHTML=''}
    }
    const closeBtn=$('#plClose')
    if(closeBtn)closeBtn.onclick=close
    const file=$('#plFile')
    const pick=$('#plPickFile')
    if(pick&&file){
      pick.onclick=()=>{if(!st.busy)file.click()}
    }
    if(file)file.onchange=async()=>{
      const f=file.files&&file.files[0]
      if(!f)return
      await loadSelectedFile(st,f)
      try{file.value=''}catch(_){}
    }
    const auxFile=$('#plAuxFile')
    const pickAux=$('#plPickAux')
    if(pickAux&&auxFile){
      pickAux.onclick=()=>{if(!st.busy)auxFile.click()}
    }
    if(auxFile)auxFile.onchange=async()=>{
      const f=auxFile.files&&auxFile.files[0]
      if(!f)return
      await loadAuxFile(st,f)
      try{auxFile.value=''}catch(_){}
    }
    const clearAux=$('#plClearAux')
    if(clearAux)clearAux.onclick=()=>{
      st.auxFile=null;st.auxFileB64='';st.auxFileName='';st.auxFileSize=0
      st.preview=null;st.result=null
      Object.keys(st.skuDecisions||{}).forEach(code=>{
        const d=st.skuDecisions[code]
        if(d&&!d.primarySupplierManual){
          delete d.primarySupplier
          delete d.auxPrimaryConflict
        }
      })
      paintDialog()
    }
    const runPreview=async()=>{
      if(!st.fileB64){st.toast('CSVファイルを選んでください。');return}
      try{assertUploadSize(st.file,st.fileB64)}catch(e){st.toast(apiErrorMessage(e,OVERSIZE_MSG));return}
      if(st.auxFileB64){
        try{assertUploadSize(st.auxFile,st.auxFileB64)}catch(e){st.toast(apiErrorMessage(e,OVERSIZE_MSG));return}
      }
      // フォーム値を skuDecisions へ吸い上げ
      document.querySelectorAll('[data-pl-official-name-edit]').forEach(el=>{
        setSkuDecision(st,el.getAttribute('data-pl-official-name-edit'),{officialName:el.value})
      })
      document.querySelectorAll('[data-pl-official-color]').forEach(el=>{
        setSkuDecision(st,el.getAttribute('data-pl-official-color'),{color:el.value})
      })
      document.querySelectorAll('[data-pl-defer-reason]').forEach(el=>{
        setSkuDecision(st,el.getAttribute('data-pl-defer-reason'),{deferReason:el.value})
      })
      document.querySelectorAll('[data-pl-sep-code]').forEach(el=>{
        const code=el.getAttribute('data-pl-sep-code')
        const key=el.getAttribute('data-pl-sep-key')
        const d=skuDecision(st,code)
        const plans={...(d.plans||{})}
        plans[key]={...(plans[key]||{}),newCode:el.value}
        setSkuDecision(st,code,{plans})
      })
      ;((st.preview&&st.preview.skuGroups)||[]).forEach(g=>{
        if(skuDecision(st,g.code).decision==='merge'){
          syncMergeSources(st,g.code)
          applyAuxDefaultsForMerge(st,g.code,g)
        }
      })
      st.busy=true;paintDialog()
      try{
        st.preview=await st.api('/api/product-ledger-csv/preview',previewPayload(st))
        st.result=null
        // 自動提案をクライアント決定へ反映（手動は上書きしない）
        ;((st.preview&&st.preview.skuGroups)||[]).forEach(g=>{
          if(skuDecision(st,g.code).decision==='merge')applyAuxDefaultsForMerge(st,g.code,g)
        })
        // サイズ差の自動まとめ行は要確認に出さない。真の要確認だけ絞り込む。
        if(st.preview.reviewCount>0)st.statusFilter='needs_review'
        else if(st.preview.skuDecisionPending>0)st.statusFilter=''
        else st.statusFilter=''
        st.page=1
        if(st.preview.previousImports&&!st.history){
          st.history={previousImports:st.preview.previousImports}
          st.historyError=''
        }
      }catch(e){st.toast(apiErrorMessage(e,'照合に失敗しました。'))}
      st.busy=false;paintDialog()
    }
    const prevBtn=$('#plPreview');if(prevBtn)prevBtn.onclick=runPreview
    const reBtn=$('#plRePreview');if(reBtn)reBtn.onclick=runPreview
    const q=$('#plQ');if(q)q.oninput=()=>{st.q=q.value;st.page=1;paintDialog()}
    const stSel=$('#plStatus');if(stSel)stSel.onchange=()=>{st.statusFilter=stSel.value;st.page=1;paintDialog()}
    const pPrev=$('#plPrev');if(pPrev)pPrev.onclick=()=>{st.page=Math.max(1,st.page-1);paintDialog()}
    const pNext=$('#plNext');if(pNext)pNext.onclick=()=>{st.page+=1;paintDialog()}

    document.querySelectorAll('[data-pl-sku-dec]').forEach(el=>{
      el.onclick=()=>{
        const code=el.getAttribute('data-pl-sku-dec')
        const dec=el.getAttribute('data-dec')
        const group=((st.preview&&st.preview.skuGroups)||[]).find(g=>g.code===code)
        const patch={decision:dec}
        if(dec==='merge'&&group){
          const names=(group.variants||[]).map(v=>v.productName).filter(Boolean)
          if(!skuDecision(st,code).officialName&&names.length)patch.officialName=names[0]
          if(group.existingCount===1){
            patch.keepProductId=group.existingProducts[0].id
            patch.mergeSourceIds=[]
          }
          setSkuDecision(st,code,patch)
          applyAuxDefaultsForMerge(st,code,group)
        }else{
          setSkuDecision(st,code,patch)
        }
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-keep]').forEach(el=>{
      el.onchange=()=>{
        const code=el.getAttribute('data-pl-keep')
        setSkuDecision(st,code,{keepProductId:Number(el.value),confirmPrimaryChange:false})
        syncMergeSources(st,code)
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-official-name]').forEach(el=>{
      el.onchange=()=>{
        setSkuDecision(st,el.getAttribute('data-pl-official-name'),{officialName:el.value})
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-primary-sup]').forEach(el=>{
      el.onchange=()=>{
        const code=el.getAttribute('data-pl-primary-sup')
        setSkuDecision(st,code,{
          primarySupplier:el.value,
          primarySupplierManual:!!el.value,
          confirmPrimaryChange:false,
          auxPrimaryConflict:false
        })
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-confirm-primary]').forEach(el=>{
      el.onchange=()=>{
        setSkuDecision(st,el.getAttribute('data-pl-confirm-primary'),{confirmPrimaryChange:!!el.checked})
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-sep-approach]').forEach(el=>{
      el.onchange=()=>{
        const code=el.getAttribute('data-pl-sep-approach')
        const key=el.getAttribute('data-pl-sep-key')
        const d=skuDecision(st,code)
        const plans={...(d.plans||{})}
        plans[key]={...(plans[key]||{}),approach:el.value}
        setSkuDecision(st,code,{plans})
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-merge-src]').forEach(el=>{
      el.onchange=()=>syncMergeSources(st,el.getAttribute('data-pl-merge-src'))
    })

    document.querySelectorAll('[data-pl-prod]').forEach(el=>{
      el.onchange=()=>{
        const rowNo=el.getAttribute('data-pl-prod')
        if(el.value==='__new__'){
          const n=applySelectionToGroup(st,rowNo,{forceCreate:true,productId:null})
          st.toast(`同条件 ${n} 行に「新規」を適用しました`)
        }else if(el.value){
          const n=applySelectionToGroup(st,rowNo,{productId:Number(el.value),forceCreate:false})
          st.toast(`同条件 ${n} 行に商品ID ${el.value} を適用しました`)
        }else{
          applySelectionToGroup(st,rowNo,{productId:null,forceCreate:false})
        }
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-handle]').forEach(el=>{
      el.onchange=()=>{
        const rowNo=el.getAttribute('data-pl-handle')
        const n=applySelectionToGroup(st,rowNo,{handleId:el.value?Number(el.value):null})
        if(el.value)st.toast(`同条件 ${n} 行に取扱を適用しました`)
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-cat]').forEach(el=>{
      el.onchange=()=>{
        applySelectionToGroup(st,el.getAttribute('data-pl-cat'),{category:el.value})
        paintDialog()
      }
    })
    const commit=$('#plCommit')
    if(commit)commit.onclick=async()=>{
      const canWriteNow=typeof st.stockCanWrite==='function'?st.stockCanWrite():true
      if(!st.preview)return
      if(!canWriteNow){st.toast('閲覧専用のため登録できません。');return}
      if((st.preview.reviewCount||0)>0||(st.preview.skuDecisionPending||0)>0){
        st.toast('要確認または品番比較の未決定が残っています。先に解決してください。');return
      }
      const p=st.preview.plannedCounts||{}
      const summary=`新規商品 ${p.productsToCreate||0} / 再利用 ${p.productsToReuse||0} / 統合 ${p.productsToMerge||0} / 保留行 ${p.rowsDeferred||0} / サイズ追加 ${p.sizesToAdd||0} / 取扱新規 ${p.handlesToCreate||0}\n（CSV行 ${p.csvRows||0} ※行数と新規商品数は別です）\n在庫数量・金額・履歴は変更しません。よろしいですか？`
      if(!confirm(summary))return
      try{assertUploadSize(st.file,st.fileB64)}catch(e){st.toast(apiErrorMessage(e,OVERSIZE_MSG));return}
      st.busy=true;paintDialog()
      try{
        if(!st.commitToken)st.commitToken=`pl-${Date.now()}-${Math.random().toString(36).slice(2,10)}`
        const commitBody=previewPayload(st)
        commitBody.previewFingerprint=st.preview.previewFingerprint
        commitBody.previewAt=st.preview.previewAt
        commitBody.token=st.commitToken
        st.result=await st.api('/api/product-ledger-csv/commit',commitBody)
        st.commitToken=`pl-${Date.now()}-${Math.random().toString(36).slice(2,10)}`
        if(typeof st.boot==='function')await st.boot()
        st.toast('商品台帳CSVの登録が完了しました')
      }catch(e){
        if(e&&e.payload&&e.payload.code==='stale_preview'){
          st.toast('データが変わったため、もう一度照合してください。')
          st.preview=null
        }else if(e&&e.payload&&e.payload.code==='backup_failed'){
          st.toast(apiErrorMessage(e,'登録前のバックアップに失敗したため、登録を中止しました'))
        }else st.toast(apiErrorMessage(e,'登録に失敗しました。登録は完了していません。'))
      }
      st.busy=false;paintDialog()
    }
  }

  function open(opts){
    const st=ensureState()
    st.api=opts.api
    st.toast=opts.toast||(m=>alert(m))
    st.boot=opts.boot
    st.stockCanWrite=opts.stockCanWrite
    st.actorName=opts.actorName||''
    st.open=true
    st.result=null
    paintDialog()
  }

  window.ProductLedgerCsv={open}
})()
