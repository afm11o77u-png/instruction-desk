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
  const SKU_PAGE_SIZE=40
  // Keep in sync with stock-desk/upload_limits.py (POST body 12MB; raw CSV decode 8MB).
  const MAX_RAW_FILE_BYTES=8_000_000
  const MAX_POST_BODY_BYTES=12_000_000
  const OVERSIZE_MSG='ファイル容量が上限を超えています'
  const TIER_LABELS={auto:'自動まとめ',bulk:'一括承認候補',individual:'個別確認'}
  let ui=null

  function ensureState(){
    if(!ui){
      ui={
        file:null,fileB64:'',fileName:'',fileSize:0,
        auxFile:null,auxFileB64:'',auxFileName:'',auxFileSize:0,
        history:null,historyError:'',preview:null,result:null,
        q:'',statusFilter:'needs_review',page:1,
        skuQ:'',skuReasonFilter:'',skuPage:1,
        selectedCodes:{},expandedCodes:{},
        selections:{},skuDecisions:{},commitToken:'',busy:false,open:false,
        api:null,toast:null,boot:null,stockCanWrite:null,actorName:''
      }
    }
    if(!ui.selectedCodes)ui.selectedCodes={}
    if(!ui.expandedCodes)ui.expandedCodes={}
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

  function decisionGroups(st){
    return ((st.preview&&st.preview.skuGroups)||[]).filter(g=>g.needsDecision)
  }

  function filteredSkuGroups(st){
    const q=(st.skuQ||'').trim().toLowerCase()
    const reason=st.skuReasonFilter||''
    return decisionGroups(st).filter(g=>{
      if(reason==='bulk'&&g.reviewTier!=='bulk')return false
      if(reason==='individual'&&g.reviewTier!=='individual')return false
      if(reason==='pending'&&skuDecision(st,g.code).decision)return false
      if(reason==='merge'&&skuDecision(st,g.code).decision!=='merge')return false
      if(reason==='hold'&&skuDecision(st,g.code).decision!=='hold')return false
      if(reason==='name_wording'&&!g.nameWordingDiff)return false
      if(reason==='supplier'&&!g.supplierDiff)return false
      if(reason==='color'&&!g.colorDiffPossible)return false
      if(reason==='processing'&&!g.processingDiffPossible)return false
      if(reason==='kind'&&!g.kindDiffPossible)return false
      if(!q)return true
      const hay=[
        g.code,g.codeDisplay,
        (g.nameCandidates||[]).join(' '),
        (g.supplierCandidates||[]).join(' '),
        (g.reviewReasons||[]).map(r=>r.label).join(' '),
        g.reviewTierLabel,g.plannedOfficialName,g.plannedPrimarySupplier,
        g.nameAdoptionRule
      ].join(' ').toLowerCase()
      return hay.includes(q)
    })
  }

  function selectedCodeList(st){
    return Object.keys(st.selectedCodes||{}).filter(c=>st.selectedCodes[c])
  }

  function plannedNameForGroup(st,g){
    const d=skuDecision(st,g.code)
    if(d.officialName)return d.officialName
    return (g.namePlan&&g.namePlan.officialName)||g.plannedOfficialName||''
  }

  function plannedSupplierForGroup(st,g){
    const d=skuDecision(st,g.code)
    if(d.primarySupplier)return d.primarySupplier
    return g.plannedPrimarySupplier||(g.auxPrimary&&g.auxPrimary.autoPrimarySupplier)||''
  }

  function candidatesCell(values){
    const list=(values||[]).filter(Boolean)
    if(!list.length)return '<span class="sub">—</span>'
    return `<ul class="sku-cand-list">${list.map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`
  }

  function otherDiffsCell(g){
    const diffs=g.otherDiffs||[]
    if(!diffs.length){
      if(g.nameWordingDiff||g.supplierDiff)return '<span class="sub">名差・仕入先差のみ</span>'
      return '<span class="sub">—</span>'
    }
    return diffs.map(d=>`<div class="sku-other-diff"><strong>${esc(d.label||'')}</strong><div>${(d.values||[]).map(esc).join(' / ')||'—'}</div></div>`).join('')
  }

  function reasonCell(g){
    const reasons=g.reviewReasons||[]
    if(!reasons.length)return '<span class="sub">—</span>'
    return `<ul class="sku-reason-list">${reasons.map(r=>{
      const vals=(r.values||[]).length?`（${(r.values||[]).map(esc).join(' / ')}）`:''
      return `<li>${esc(r.label||'')}${vals}</li>`
    }).join('')}</ul>`
  }

  function decisionLabel(dec){
    if(dec==='merge')return 'まとめる'
    if(dec==='separate')return '個別整理'
    if(dec==='hold')return '保留'
    return '未決定'
  }

  function skuRowDetailHtml(st,g){
    const d=skuDecision(st,g.code)
    const decision=d.decision||''
    const names=[...new Set([...(g.nameCandidates||[]),...(g.variants||[]).map(v=>v.productName).filter(Boolean)])]
    const suppliers=[...new Set([...(g.supplierCandidates||[]),...(g.variants||[]).map(v=>v.supplier).filter(Boolean)])]
    const nameOpts=names.map(n=>`<option value="${esc(n)}" ${d.officialName===n?'selected':''}>${esc(n)}</option>`).join('')
    const effectivePrimary=plannedSupplierForGroup(st,g)
    const supOpts=suppliers.map(s=>`<option value="${esc(s)}" ${effectivePrimary===s?'selected':''}>${esc(s)}</option>`).join('')
    const existing=(g.existingProducts||[]).map(p=>{
      return `<label class="check"><input type="radio" name="plKeep-${esc(g.code)}" data-pl-keep="${esc(g.code)}" value="${p.id}" ${Number(d.keepProductId)===Number(p.id)?'checked':''} ${st.busy?'disabled':''}>
        ID ${p.id}　${esc(p.name||'')}${p.color?` / ${esc(p.color)}`:''}${p.supplierName?`　仕入 ${esc(p.supplierName)}`:''}</label>`
    }).join('')||'<span class="sub">登録済なし（新規1商品）</span>'
    const mergeSources=(g.existingProducts||[]).filter(p=>Number(p.id)!==Number(d.keepProductId)).map(p=>{
      const checked=(d.mergeSourceIds||[]).map(Number).includes(Number(p.id))||(decision==='merge'&&g.existingCount>1&&d.keepProductId)
      return `<label class="check"><input type="checkbox" data-pl-merge-src="${esc(g.code)}" value="${p.id}" ${checked?'checked':''} ${st.busy||decision!=='merge'||!d.keepProductId?'disabled':''}>統合元 ID ${p.id}</label>`
    }).join('')
    const variantPlans=(g.variants||[]).map(v=>{
      const plans=(d.plans&&typeof d.plans==='object')?d.plans:{}
      const plan=plans[v.key]||{}
      return `<div class="sku-variant-plan" data-variant-key="${esc(v.key)}">
        <strong>${esc(v.productName||'（名なし）')}</strong>
        <span class="sub">仕入 ${esc(v.supplier||'未設定')}　サイズ ${(v.sizes||[]).map(esc).join('、')||'—'}　顧客 ${(v.customers||[]).slice(0,3).map(esc).join('、')}${(v.customers||[]).length>3?'…':''}　CSV行 ${(v.rowNos||[]).join(',')||'—'}</span>
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
    const customers=[...new Set((g.variants||[]).flatMap(v=>v.customers||[]))]
    const sizes=[...new Set((g.variants||[]).flatMap(v=>v.sizes||[]))]
    const rowNos=(g.rowNos||[]).join(', ')
    const namePlan=g.namePlan||{}
    const mappings=(namePlan.nameMappings||[]).slice(0,12).map(m=>
      `<li>${esc(m.originalName||'')}（行${m.sourceRowNo}）→ ${esc(m.registrationName||'')}${m.isAlias?'（別名）':''}</li>`
    ).join('')
    const auxPanel=auxSupplierPanelHtml(st,g,d)
    return `<tr class="sku-detail-row" data-sku-detail="${esc(g.code)}"><td colspan="8">
      <div class="sku-detail-body">
        <p class="excelhint">顧客: ${esc(customers.join('、')||'—')}　サイズ: ${esc(sizes.join('、')||'—')}　CSV行: ${esc(rowNos||'—')}</p>
        ${namePlan.adoptionRule?`<p class="excelhint">名称採用ルール: ${esc(namePlan.adoptionRule)}${namePlan.provisional?`（タグ「${esc(st.preview&&st.preview.provisionalNameTag||'名称未整理')}」）`:''}</p>`:''}
        ${mappings?`<ul class="sku-name-map">${mappings}</ul>`:''}
        ${auxPanel}
        <div class="bar sku-decision-btns">
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="merge" class="${decision==='merge'?'primary':''}" ${st.busy?'disabled':''}>同じ商品としてまとめる</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="separate" class="${decision==='separate'?'primary':''}" ${st.busy?'disabled':''}>別物のため個別に整理する</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="hold" class="${decision==='hold'?'primary':''}" ${st.busy?'disabled':''}>今回は保留する</button>
        </div>
        ${decision==='merge'?`<div class="sku-merge-form">
          <label>公式商品名<select data-pl-official-name="${esc(g.code)}" ${st.busy?'disabled':''}><option value="">仮代表名を使う…</option>${nameOpts}</select></label>
          <label>公式名（編集可）<input data-pl-official-name-edit="${esc(g.code)}" value="${esc(d.officialName||'')}" placeholder="空なら仮代表名（名称未整理）" ${st.busy?'disabled':''}></label>
          <label>色<input data-pl-official-color="${esc(g.code)}" value="${esc(d.color||'')}" ${st.busy?'disabled':''}></label>
          <label>通常仕入先<select data-pl-primary-sup="${esc(g.code)}" ${st.busy?'disabled':''}><option value="">未指定</option>${supOpts}</select></label>
          ${d.primarySupplierManual?'<p class="excelhint">通常仕入先は手動選択です（初期在庫CSVの再読込でも上書きしません）。</p>':(g.auxPrimary&&g.auxPrimary.autoPrimarySupplier?`<p class="excelhint">通常仕入先は初期在庫CSVから自動提案: ${esc(g.auxPrimary.autoPrimarySupplier)}</p>`:'')}
          <p class="excelhint">予定代表名: ${esc(plannedNameForGroup(st,g)||'—')}　予定通常仕入先: ${esc(plannedSupplierForGroup(st,g)||'未設定')}</p>
          <fieldset><legend>残す商品（登録済が複数のとき必須）</legend>${existing}</fieldset>
          ${g.existingCount>1?`<fieldset><legend>統合する商品（残す以外）</legend>${mergeSources||'<span class="sub">残す商品を先に選んでください</span>'}</fieldset>`:''}
          <p class="excelhint">旧商品名・仕入先は照合用の別名として残します。既存の公式名は仮名では上書きしません。サイズの LL≡2L 同一化はしません。</p>
        </div>`:''}
        ${decision==='separate'?`<div class="sku-separate-form">
          <p class="excelhint">同じ通常品番のまま別商品として新規登録はできません。品番修正・部品扱い・保留のいずれかを内容ごとに選んでください。</p>
          ${variantPlans}
        </div>`:''}
        ${decision==='hold'?`<div class="sku-hold-form">
          <label>保留理由<input data-pl-defer-reason="${esc(g.code)}" value="${esc(d.deferReason||'')}" placeholder="例: 制服本体と加工サービスの切り分けが未確定" ${st.busy?'disabled':''}></label>
          <p class="excelhint">この品番は今回登録しません。同じCSVを後で再読込して処理できます。</p>
        </div>`:''}
      </div>
    </td></tr>`
  }

  function applyMergeDefaults(st,code,group){
    const patch={decision:'merge'}
    // 公式名は必須にしない（仮代表名で登録可）。ユーザー指定があれば残す
    if(group.existingCount===1){
      patch.keepProductId=group.existingProducts[0].id
      patch.mergeSourceIds=[]
    }
    setSkuDecision(st,code,patch)
    applyAuxDefaultsForMerge(st,code,group)
  }

  function skuGroupsHtml(st){
    const all=filteredSkuGroups(st)
    const pending=decisionGroups(st)
    if(!pending.length)return ''
    const pages=Math.max(1,Math.ceil(all.length/SKU_PAGE_SIZE))
    if(st.skuPage>pages)st.skuPage=pages
    if(st.skuPage<1)st.skuPage=1
    const start=(st.skuPage-1)*SKU_PAGE_SIZE
    const pageGroups=all.slice(start,start+SKU_PAGE_SIZE)
    const selected=selectedCodeList(st)
    const selectedOnPage=pageGroups.filter(g=>st.selectedCodes[g.code]).length
    const bulkCount=pending.filter(g=>g.reviewTier==='bulk').length
    const indivCount=pending.filter(g=>g.reviewTier==='individual').length
    const decided=pending.filter(g=>skuDecision(st,g.code).decision).length
    const body=pageGroups.map(g=>{
      const d=skuDecision(st,g.code)
      const decision=d.decision||''
      const checked=!!st.selectedCodes[g.code]
      const expanded=!!st.expandedCodes[g.code]
      const tier=g.reviewTier||'individual'
      const plannedName=plannedNameForGroup(st,g)
      const plannedSup=plannedSupplierForGroup(st,g)
      const provisional=!(d.officialName)&&(g.namePlan&&g.namePlan.provisional||g.provisionalName)
      return `<tr class="sku-list-row sku-tier-${esc(tier)} ${decision?`sku-dec-${esc(decision)}`:''}" data-sku-code="${esc(g.code)}">
        <td class="sku-check"><input type="checkbox" data-pl-sku-check="${esc(g.code)}" ${checked?'checked':''} ${st.busy?'disabled':''}></td>
        <td class="sku-code"><button type="button" class="linkish" data-pl-sku-expand="${esc(g.code)}">${esc(g.codeDisplay||g.code)}</button>
          <div class="sub">${esc(TIER_LABELS[tier]||g.reviewTierLabel||'')}</div></td>
        <td class="sku-names">${candidatesCell(g.nameCandidates||(g.variants||[]).map(v=>v.productName))}</td>
        <td class="sku-sups">${candidatesCell(g.supplierCandidates||(g.variants||[]).map(v=>v.supplier))}</td>
        <td class="sku-other">${otherDiffsCell(g)}</td>
        <td class="sku-why">${reasonCell(g)}</td>
        <td class="sku-plan">
          <div><strong>${esc(plannedName||'—')}</strong>${provisional?' <span class="badge">名称未整理</span>':''}</div>
          <div class="sub">通常仕入先: ${esc(plannedSup||'未設定')}</div>
          ${(g.nameAdoptionRule||(g.namePlan&&g.namePlan.adoptionRule))?`<div class="sub">${esc(g.nameAdoptionRule||g.namePlan.adoptionRule)}</div>`:''}
        </td>
        <td class="sku-action">
          <div class="sku-action-label">${esc(decisionLabel(decision))}</div>
          <div class="bar sku-mini-btns">
            <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="merge" class="quiet ${decision==='merge'?'primary':''}" ${st.busy?'disabled':''}>まとめ</button>
            <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="hold" class="quiet ${decision==='hold'?'primary':''}" ${st.busy?'disabled':''}>保留</button>
            <button type="button" data-pl-sku-expand="${esc(g.code)}" class="quiet">${expanded?'閉じる':'詳細'}</button>
          </div>
        </td>
      </tr>${expanded?skuRowDetailHtml(st,g):''}`
    }).join('')||'<tr><td colspan="8">該当する品番はありません。</td></tr>'

    return `<div class="sku-groups-panel">
      <h3>品番ごとの比較一覧（通常品番）</h3>
      <p class="excelhint">1行=1品番。候補の商品名・仕入先をそのまま表示します。「内容の違い○種」だけでは判断させません。サイズ・顧客・先頭「＊/*」は自動まとめ済みです。一括承認候補は名差・仕入先差のみ。色・加工・商品種の疑いは個別確認（一括まとめの対象外）。公式名未設定でも仮代表名で登録できます。</p>
      <p class="excelhint">要確認 ${pending.length} 品番（一括承認候補 ${bulkCount} / 個別確認 ${indivCount}）　決定済 ${decided}　選択中 ${selected.length} 品番</p>
      <div class="bar sku-list-toolbar">
        <input type="search" id="plSkuQ" placeholder="品番・商品名・仕入先・理由で検索" value="${esc(st.skuQ||'')}" ${st.busy?'disabled':''}>
        <select id="plSkuReason" ${st.busy?'disabled':''}>
          <option value="">理由すべて</option>
          <option value="bulk" ${st.skuReasonFilter==='bulk'?'selected':''}>一括承認候補</option>
          <option value="individual" ${st.skuReasonFilter==='individual'?'selected':''}>個別確認</option>
          <option value="pending" ${st.skuReasonFilter==='pending'?'selected':''}>未決定のみ</option>
          <option value="merge" ${st.skuReasonFilter==='merge'?'selected':''}>まとめ済</option>
          <option value="hold" ${st.skuReasonFilter==='hold'?'selected':''}>保留済</option>
          <option value="name_wording" ${st.skuReasonFilter==='name_wording'?'selected':''}>名差</option>
          <option value="supplier" ${st.skuReasonFilter==='supplier'?'selected':''}>仕入先差</option>
          <option value="color" ${st.skuReasonFilter==='color'?'selected':''}>色の疑い</option>
          <option value="processing" ${st.skuReasonFilter==='processing'?'selected':''}>加工の疑い</option>
          <option value="kind" ${st.skuReasonFilter==='kind'?'selected':''}>商品種の疑い</option>
        </select>
        <button type="button" id="plSkuSelectPage" ${st.busy||!pageGroups.length?'disabled':''}>このページを選択（${pageGroups.length}）</button>
        <button type="button" id="plSkuSelectFiltered" ${st.busy||!all.length?'disabled':''}>絞り込み全件を選択（${all.length}）</button>
        <button type="button" id="plSkuClearSel" class="quiet" ${st.busy||!selected.length?'disabled':''}>選択解除</button>
      </div>
      <div class="bar sku-bulk-bar">
        <button type="button" id="plSkuBulkMerge" class="primary" ${st.busy||!selected.length?'disabled':''}>選択を同じ商品としてまとめる</button>
        <button type="button" id="plSkuBulkHold" ${st.busy||!selected.length?'disabled':''}>選択を今回は保留する</button>
        <span class="excelhint">選択 ${selected.length}（うちこのページ ${selectedOnPage}） / 表示 ${all.length} 品番</span>
      </div>
      <div class="sku-compare-wrap"><table class="sku-compare-table">
        <thead><tr>
          <th></th><th>品番</th><th>商品名候補</th><th>仕入先候補</th><th>色・加工など</th><th>要確認理由</th><th>予定の代表名・通常仕入先</th><th>操作</th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table></div>
      <div class="bar legacy-csv-pager">
        <button type="button" id="plSkuPrev" ${st.skuPage<=1||st.busy?'disabled':''}>前へ</button>
        <span>${st.skuPage} / ${pages}ページ（表示 ${all.length}品番）</span>
        <button type="button" id="plSkuNext" ${st.skuPage>=pages||st.busy?'disabled':''}>次へ</button>
      </div>
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
        <p class="merge-lead">商品と顧客取扱だけを登録します。在庫数量・仕入単価・初期在庫・入出庫履歴は変更しません。通常品番は<strong>1品番=1商品</strong>です。初期在庫用の「旧CSV取込」とは別機能です。プレビューまではDBを変更しません。保留した品番は登録せず残せます。</p>
        ${filePickHtml(st)}
        ${historyBlock(st)}
        ${st.preview?`<p class="excelhint">${esc(st.preview.note||'')}</p>
          ${st.preview.auxOpeningStock?`<p class="excelhint">初期在庫CSV（判定用）: ${esc(st.preview.auxOpeningStock.fileName||'')}　${esc(st.preview.auxOpeningStock.note||'')}</p>`:''}
          <p><strong>${countLine(st.preview.countsRaw)}</strong>　行の要確認 ${review}件　品番未決定 ${skuPending}件${(review||skuPending)? '（未決定の品番は確定できません。保留にすれば登録対象から外せます）':''}</p>
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
            <button type="button" id="plCommit" class="primary" ${st.busy||!canWrite||review>0||skuPending>0?'disabled':''}>確認して登録（保留は除外）</button>
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
    st.selectedCodes={}
    st.expandedCodes={}
    st.skuQ='';st.skuReasonFilter='';st.skuPage=1
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
    const skuQ=$('#plSkuQ');if(skuQ)skuQ.oninput=()=>{st.skuQ=skuQ.value;st.skuPage=1;paintDialog()}
    const skuReason=$('#plSkuReason');if(skuReason)skuReason.onchange=()=>{st.skuReasonFilter=skuReason.value;st.skuPage=1;paintDialog()}
    const skuPrev=$('#plSkuPrev');if(skuPrev)skuPrev.onclick=()=>{st.skuPage=Math.max(1,st.skuPage-1);paintDialog()}
    const skuNext=$('#plSkuNext');if(skuNext)skuNext.onclick=()=>{st.skuPage+=1;paintDialog()}
    const selPage=$('#plSkuSelectPage')
    if(selPage)selPage.onclick=()=>{
      const all=filteredSkuGroups(st)
      const pages=Math.max(1,Math.ceil(all.length/SKU_PAGE_SIZE))
      const start=(Math.min(st.skuPage,pages)-1)*SKU_PAGE_SIZE
      all.slice(start,start+SKU_PAGE_SIZE).forEach(g=>{st.selectedCodes[g.code]=true})
      st.toast(`このページ ${Math.min(SKU_PAGE_SIZE,all.length-start)} 品番を選択しました（絞り込み全件 ${all.length} とは別です）`)
      paintDialog()
    }
    const selFiltered=$('#plSkuSelectFiltered')
    if(selFiltered)selFiltered.onclick=()=>{
      const all=filteredSkuGroups(st)
      all.forEach(g=>{st.selectedCodes[g.code]=true})
      st.toast(`絞り込み全件 ${all.length} 品番を選択しました`)
      paintDialog()
    }
    const clearSel=$('#plSkuClearSel')
    if(clearSel)clearSel.onclick=()=>{st.selectedCodes={};paintDialog()}
    const bulkMerge=$('#plSkuBulkMerge')
    if(bulkMerge)bulkMerge.onclick=()=>{
      const codes=selectedCodeList(st)
      if(!codes.length){st.toast('品番を選択してください。');return}
      const groups=decisionGroups(st)
      const byCode=Object.fromEntries(groups.map(g=>[g.code,g]))
      const bulk=[],individual=[],missing=[]
      codes.forEach(code=>{
        const g=byCode[code]
        if(!g){missing.push(code);return}
        if(g.reviewTier==='individual')individual.push(g)
        else bulk.push(g)
      })
      if(!bulk.length){
        st.toast(individual.length
          ?`選択 ${codes.length} 品番はすべて個別確認です。一括まとめには含めません。詳細から判断してください。`
          :'一括まとめできる品番がありません。')
        return
      }
      const lines=bulk.slice(0,15).map(g=>{
        const name=plannedNameForGroup(st,g)||'（仮代表名）'
        const sup=plannedSupplierForGroup(st,g)||'未設定'
        return `・${g.code}: ${name} / 通常仕入先 ${sup}`
      })
      const more=bulk.length>15?`\n…他 ${bulk.length-15} 品番`:''
      const warn=individual.length
        ?`\n\n※個別確認 ${individual.length} 品番は一括まとめから除外します（色・加工・商品種の疑い）。`
        :''
      const msg=`選択 ${codes.length} 品番のうち、一括承認候補 ${bulk.length} 品番を「同じ商品としてまとめます」。\n品番ごとに予定の代表名・通常仕入先を使います（他品番の仕入先は割り当てません）。\n\n${lines.join('\n')}${more}${warn}\n\nよろしいですか？`
      if(!confirm(msg))return
      bulk.forEach(g=>applyMergeDefaults(st,g.code,g))
      st.toast(`${bulk.length} 品番をまとめ予定にしました${individual.length?`（個別確認 ${individual.length} は除外）`:''}`)
      paintDialog()
    }
    const bulkHold=$('#plSkuBulkHold')
    if(bulkHold)bulkHold.onclick=()=>{
      const codes=selectedCodeList(st)
      if(!codes.length){st.toast('品番を選択してください。');return}
      const groups=decisionGroups(st)
      const byCode=Object.fromEntries(groups.map(g=>[g.code,g]))
      const targets=codes.map(c=>byCode[c]).filter(Boolean)
      const msg=`選択 ${targets.length} 品番を「今回は保留」します。\n保留分は今回登録せず、同じCSVの再読込で後から処理できます。\n\n品番: ${targets.slice(0,20).map(g=>g.code).join('、')}${targets.length>20?` …他${targets.length-20}`:''}\n\nよろしいですか？`
      if(!confirm(msg))return
      targets.forEach(g=>{
        setSkuDecision(st,g.code,{decision:'hold',deferReason:skuDecision(st,g.code).deferReason||'一括保留'})
      })
      st.toast(`${targets.length} 品番を保留にしました`)
      paintDialog()
    }

    document.querySelectorAll('[data-pl-sku-check]').forEach(el=>{
      el.onchange=()=>{
        const code=el.getAttribute('data-pl-sku-check')
        st.selectedCodes[code]=!!el.checked
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-sku-expand]').forEach(el=>{
      el.onclick=()=>{
        const code=el.getAttribute('data-pl-sku-expand')
        st.expandedCodes[code]=!st.expandedCodes[code]
        paintDialog()
      }
    })

    document.querySelectorAll('[data-pl-sku-dec]').forEach(el=>{
      el.onclick=()=>{
        const code=el.getAttribute('data-pl-sku-dec')
        const dec=el.getAttribute('data-dec')
        const group=((st.preview&&st.preview.skuGroups)||[]).find(g=>g.code===code)
        if(dec==='merge'&&group){
          applyMergeDefaults(st,code,group)
          st.expandedCodes[code]=true
        }else{
          setSkuDecision(st,code,{decision:dec})
          if(dec==='separate'||dec==='hold')st.expandedCodes[code]=true
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
      const deferList=(p.deferredReasons||[]).slice(0,12).map(d=>
        `・行${d.rowNo} 品番${d.code||'—'} ${d.reason||'保留'}`
      )
      const deferMore=(p.deferredReasons||[]).length>12?`\n…他 ${(p.deferredReasons||[]).length-12} 件`:''
      const deferBlock=(p.rowsDeferred||0)
        ?`\n\n【今回は登録しない保留】 ${p.rowsDeferred} 行\n${deferList.join('\n')}${deferMore}\n（黙って捨てません。同じCSVの再読込で後から処理できます）`
        :'\n\n保留行はありません。'
      const summary=`【登録する】新規商品 ${p.productsToCreate||0} / 再利用 ${p.productsToReuse||0} / 統合 ${p.productsToMerge||0} / サイズ追加 ${p.sizesToAdd||0} / 取扱新規 ${p.handlesToCreate||0}\n（CSV行 ${p.csvRows||0} ※行数と新規商品数は別です）${deferBlock}\n\n在庫数量・金額・履歴は変更しません。よろしいですか？`
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
