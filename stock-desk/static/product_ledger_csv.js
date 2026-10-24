/** 旧CSV一括取り込み（登録・設定・右上）。標準は二段階:
 *  1. 商品を登録（台帳必須・初期在庫CSVは判定補助のみ／在庫は書かない）
 *  2. 初期在庫を登録（初期在庫CSVのみ＋永続マップで登録先解決）
 */
(function(){
  const STATUS_ORDER=['create','merge','size_add','reuse','unchanged','needs_review','hold','excluded']
  const STATUS_LABELS={
    create:'新規',merge:'統合して登録',size_add:'サイズ追加',reuse:'既存使用',
    unchanged:'変更なし',needs_review:'要確認',hold:'保留',excluded:'対象外'
  }
  const OPEN_STATUS_ORDER=['create','reuse','qty_diff','unchanged','needs_review','hold','excluded','complement']
  const OPEN_STATUS_LABELS={
    create:'新規追加',reuse:'既存使用',qty_diff:'数量差異',unchanged:'変更なし',
    needs_review:'要確認',hold:'保留',excluded:'対象外',complement:'補完候補'
  }
  const PAGE_SIZE=80
  const SKU_PAGE_SIZE=40
  // Keep in sync with stock-desk/upload_limits.py (POST body 12MB; raw CSV decode 8MB).
  const MAX_RAW_FILE_BYTES=8_000_000
  const MAX_POST_BODY_BYTES=12_000_000
  const OVERSIZE_MSG='ファイル容量が上限を超えています'
  const TIER_LABELS={auto:'自動まとめ',bulk:'一括承認候補',individual:'個別確認'}
  const SECTION_DEFAULTS={individual:true,bulk:false,auto:false,hold:false}
  const MODE_CHOOSE=''
  const MODE_PRODUCTS='products'
  const MODE_OPENING='opening_stock'
  const AUX_JUDGMENT_ONLY_COPY='初期在庫CSVは商品選定の判断にだけ使います。在庫は登録しません'
  let ui=null

  function todayISO(){
    const d=new Date()
    const m=String(d.getMonth()+1).padStart(2,'0')
    const day=String(d.getDate()).padStart(2,'0')
    return `${d.getFullYear()}-${m}-${day}`
  }

  function ensureState(){
    if(!ui){
      ui={
        importMode:MODE_CHOOSE,
        progressLabel:'',
        file:null,fileB64:'',fileName:'',fileSize:0,
        auxFile:null,auxFileB64:'',auxFileName:'',auxFileSize:0,
        basisDate:'',defaultSiteName:'未確認',
        openingSelections:{},
        history:null,historyError:'',previewError:'',preview:null,result:null,
        q:'',statusFilter:'needs_review',page:1,
        skuQ:'',skuReasonFilter:'',skuPage:1,
        selectedCodes:{},expandedCodes:{},
        sectionOpen:Object.assign({},SECTION_DEFAULTS),
        sectionPages:{individual:1,bulk:1,approved:1,auto:1,hold:1,separate:1},
        selections:{},skuDecisions:{},rowClassifications:{},commitToken:'',busy:false,open:false,
        makerLookup:null,
        api:null,toast:null,boot:null,stockCanWrite:null,actorName:''
      }
    }
    if(ui.importMode==null)ui.importMode=MODE_CHOOSE
    if(ui.progressLabel==null)ui.progressLabel=''
    if(!ui.selectedCodes)ui.selectedCodes={}
    if(!ui.expandedCodes)ui.expandedCodes={}
    if(!ui.rowClassifications)ui.rowClassifications={}
    if(!ui.openingSelections)ui.openingSelections={}
    if(ui.makerLookup===undefined)ui.makerLookup=null
    if(!ui.sectionOpen)ui.sectionOpen=Object.assign({},SECTION_DEFAULTS)
    // 再描画でユーザーが開いた区分を閉じない（欠けたキーだけ既定値で補う）
    Object.keys(SECTION_DEFAULTS).forEach(k=>{
      if(ui.sectionOpen[k]==null)ui.sectionOpen[k]=SECTION_DEFAULTS[k]
    })
    if(!ui.sectionPages)ui.sectionPages={individual:1,bulk:1,approved:1,auto:1,hold:1,separate:1}
    if(ui.previewReqSeq==null)ui.previewReqSeq=0
    if(!ui.basisDate)ui.basisDate=todayISO()
    if(!ui.defaultSiteName)ui.defaultSiteName='未確認'
    return ui
  }

  function isProductsMode(st){return (st||ensureState()).importMode===MODE_PRODUCTS}
  function isOpeningMode(st){return (st||ensureState()).importMode===MODE_OPENING}

  function resetStageFiles(st){
    st.file=null;st.fileB64='';st.fileName='';st.fileSize=0
    st.auxFile=null;st.auxFileB64='';st.auxFileName='';st.auxFileSize=0
    st.history=null;st.historyError='';st.preview=null;st.previewError='';st.result=null
    st.selections={};st.skuDecisions={};st.rowClassifications={}
    st.selectedCodes={};st.expandedCodes={};st.openingSelections={}
    st.sectionOpen=Object.assign({},SECTION_DEFAULTS)
    st.sectionPages={individual:1,bulk:1,approved:1,auto:1,hold:1,separate:1}
    st.skuQ='';st.skuReasonFilter='';st.skuPage=1;st.page=1
    st.progressLabel=''
  }

  /** クライアント決定を優先（折りたたみでは承認にしない。state の decision のみ）。 */
  function effectiveDecision(st,g){
    const local=skuDecision(st,g&&g.code)
    if(local&&local.decision)return local.decision
    const remote=g&&g.decision
    if(remote&&typeof remote==='object')return remote.decision||''
    return ''
  }

  /**
   * 区分は judgment の state フィールドで分類（背景色では判定しない）。
   * - 個別確認が必要: needsDecision && reviewTier===individual && 未決定
   * - 一括承認候補: needsDecision && reviewTier===bulk && 未決定（未承認）
   * - 承認済み（未登録）: needsDecision && decision===merge
   * - 自動決定済み: !needsDecision（autoMergePlans / plannedSingleProduct）
   * - 保留 / 個別整理: decision どおり（黙って隠さない）
   */
  function partitionJudgmentGroups(st){
    const groups=((st&&st.preview&&st.preview.skuGroups)||[])
    const individual=[]
    const bulkUnapproved=[]
    const approved=[]
    const hold=[]
    const separate=[]
    groups.forEach(g=>{
      if(!g||!g.needsDecision)return
      const dec=effectiveDecision(st,g)
      const tier=g.reviewTier||'individual'
      if(dec==='merge'||dec==='color_split'){approved.push(g);return}
      if(dec==='hold'){hold.push(g);return}
      if(dec==='separate'){separate.push(g);return}
      if(tier==='bulk'){bulkUnapproved.push(g);return}
      individual.push(g)
    })
    const autoPlans=((st&&st.preview&&st.preview.autoMergePlans)||[]).slice()
    const autoCount=(st&&st.preview&&st.preview.plannedCounts&&st.preview.plannedCounts.autoMergedSkuGroups!=null)
      ?Number(st.preview.plannedCounts.autoMergedSkuGroups)||autoPlans.length
      :autoPlans.length
    const excludedRows=(st&&st.preview&&st.preview.outcomeSummary&&st.preview.outcomeSummary.excludedRows)||0
    return {
      individual,bulkUnapproved,approved,hold,separate,autoPlans,autoCount,excludedRows
    }
  }

  function judgmentStatusCounts(st){
    const p=partitionJudgmentGroups(st)
    return {
      unresolved:p.individual.length,
      unapproved:p.bulkUnapproved.length,
      approved:p.approved.length,
      autoDecided:p.autoCount,
      hold:p.hold.length,
      separate:p.separate.length,
      excluded:p.excludedRows
    }
  }

  function pageSlice(st,key,items){
    const list=items||[]
    if(!st.sectionPages)st.sectionPages={}
    const pages=Math.max(1,Math.ceil(list.length/SKU_PAGE_SIZE)||1)
    let page=Number(st.sectionPages[key])||1
    if(page>pages)page=pages
    if(page<1)page=1
    st.sectionPages[key]=page
    const start=(page-1)*SKU_PAGE_SIZE
    return {key,page,pages,start,pageItems:list.slice(start,start+SKU_PAGE_SIZE)}
  }

  function sectionPagerHtml(key,slice,total){
    if(!total)return ''
    return `<div class="bar legacy-csv-pager sku-section-pager" data-sku-section-pager="${esc(key)}">
      <button type="button" data-pl-section-page="${esc(key)}" data-dir="-1" ${slice.page<=1?'disabled':''}>前へ</button>
      <span>${slice.page} / ${slice.pages}ページ（${total}品番）</span>
      <button type="button" data-pl-section-page="${esc(key)}" data-dir="1" ${slice.page>=slice.pages?'disabled':''}>次へ</button>
    </div>`
  }

  function judgmentStatusBarHtml(st){
    const c=judgmentStatusCounts(st)
    return `<div class="sku-judgment-status-bar" data-sku-status-counts>
      <p class="excelhint"><strong>登録前の件数:</strong>
        未解決 ${c.unresolved}　未承認 ${c.unapproved}　承認済み（未登録） ${c.approved}　自動決定済み（未登録） ${c.autoDecided}　保留 ${c.hold}${c.separate?`　個別整理 ${c.separate}`:''}${c.excluded?`　対象外 ${c.excluded}`:''}
      </p>
    </div>`
  }

  function previewPayload(st){
    const body={
      importMode:st.importMode||MODE_PRODUCTS,
      selections:st.selections,skuDecisions:st.skuDecisions,
      rowClassifications:st.rowClassifications||{},
      _canWrite:true,_actor:{name:st.actorName||''}
    }
    if(isOpeningMode(st)){
      // 段階2: 初期在庫CSVのみ。台帳CSVは送らない（再判定しない）
      body.auxFile=st.auxFileB64
      body.auxName=st.auxFileName||'初期在庫.csv'
      body.registerOpeningStock=true
      body.basisDate=st.basisDate||todayISO()
      body.defaultSiteName=st.defaultSiteName||'未確認'
      body.openingSelections=st.openingSelections||{}
      return body
    }
    // 段階1: 商品登録。初期在庫CSVがあっても在庫は登録しない
    body.name=st.fileName
    body.file=st.fileB64
    body.registerOpeningStock=false
    if(st.auxFileB64){
      body.auxFile=st.auxFileB64
      body.auxName=st.auxFileName||'初期在庫.csv'
    }
    return body
  }

  function siteOptions(selected){
    const sites=((typeof state!=='undefined'&&state.data&&state.data.locations)||[]).filter(l=>!l.parentId&&l.active!==false)
    const names=sites.map(s=>s.name)
    if(selected&&!names.includes(selected))names.unshift(selected)
    if(!names.includes('未確認'))names.unshift('未確認')
    return names.map(n=>`<option value="${esc(n)}" ${n===selected?'selected':''}>${esc(n)}</option>`).join('')
  }

  function setRowClassification(st,rowNo,patch){
    const key=String(rowNo)
    const cur=Object.assign({},st.rowClassifications[key]||{})
    Object.assign(cur,patch||{})
    cur.userEdited=true
    st.rowClassifications[key]=cur
  }

  function applyAuxDefaultsForMerge(st,code,group){
    const d=skuDecision(st,code)
    const aux=group&&group.auxPrimary
    if(!aux)return
    const patch={}
    // 候補1社の自動設定・補助CSV自動判定のどちらも適用（手動選択は上書きしない）
    if(!d.primarySupplierManual){
      const auto=aux.autoPrimarySupplier||group.plannedPrimarySupplier||''
      if(auto){
        patch.primarySupplier=auto
        patch.primarySupplierManual=false
      }
    }else if(aux.autoPrimarySupplier&&d.primarySupplier&&d.primarySupplier!==aux.autoPrimarySupplier){
      patch.auxPrimaryConflict=true
    }
    if(!d.officialName&&aux.suggestedOfficialName&&!aux.officialNameNeedsChoice){
      patch.officialName=aux.suggestedOfficialName
    }
    const pkg=aux.adoptedPackage||group.adoptedPackage
    if(!d.color&&(aux.adoptedColor||(pkg&&pkg.color))){
      patch.color=aux.adoptedColor||pkg.color
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

  function outcomeSummaryHtml(st){
    const s=st.preview&&st.preview.outcomeSummary
    if(!s)return ''
    const fmt=(arr)=>((arr||[]).slice(0,8).map(x=>`${esc(x.label)} ${x.count}`).join('／'))||'—'
    const more=(arr)=>((arr||[]).length>8?` …他${(arr||[]).length-8}`:'')
    return `<div class="sku-outcome-summary">
      <h3>取込判断の件数</h3>
      <p><strong>自動まとめ</strong> ${s.autoMergedSkuGroups||0} 品番 / ${s.autoMergedCsvRows||0} 行
        <span class="sub">（${fmt(s.autoMergedReasons)}${more(s.autoMergedReasons)}）</span></p>
      <p><strong>要確認（品番比較 未決定）</strong> ${s.needsReviewSkuGroups||0} 品番
        <span class="sub">（${fmt(s.needsReviewSkuReasons)}${more(s.needsReviewSkuReasons)}）</span></p>
      ${(s.needsReviewRows||0)?`<p><strong>要確認（行）</strong> ${s.needsReviewRows} 行
        <span class="sub">（${fmt(s.needsReviewRowReasons)}${more(s.needsReviewRowReasons)}）</span></p>`:''}
      <p><strong>対象外</strong> ${s.excludedRows||0} 行
        <span class="sub">（${fmt(s.excludedReasons)}${more(s.excludedReasons)}）</span></p>
    </div>`
  }

  function nameColorSplitPreviewHtml(src){
    if(!src)return ''
    const sourceName=src.sourceAdoptedName||(src.adoptedPackage&&src.adoptedPackage.sourceName)||(src.adoptedPackage&&src.adoptedPackage.productName)||src.csvOriginalName||''
    const plannedName=src.plannedRegisteredName||src.plannedOfficialName||src.productName||(src.adoptedPackage&&src.adoptedPackage.plannedName)||''
    const plannedColor=src.plannedRegisteredColor||src.plannedColor||src.color||(src.adoptedPackage&&src.adoptedPackage.color)||''
    const plannedSupplement=src.plannedRegisteredSupplement||src.supplement||(src.parenClassification&&src.parenClassification.supplement)||''
    const reason=src.auxStockPickReason||src.nameAdoptionRule||src.scopedColorCorrectionReason||(src.auxPrimary&&(src.auxPrimary.pairAdoptionRule||src.auxPrimary.nameAdoptionRule))||''
    const summary=src.changeSummary||(src.auxPrimary&&src.auxPrimary.changeSummary)||''
    if(!sourceName&&!plannedColor&&!summary&&!plannedSupplement)return ''
    return `<div class="sku-color-split-preview">
      <p class="excelhint"><strong>登録プレビュー（商品名／色）</strong></p>
      <ul class="sku-color-split-list">
        <li>採用ソース商品名: <strong>${esc(sourceName||'—')}</strong></li>
        <li>登録予定の商品名: <strong>${esc(plannedName||'—')}</strong></li>
        <li>登録予定の色: <strong>${esc(plannedColor||'（空）')}</strong></li>
        ${plannedSupplement?`<li>登録予定の補足: <strong>${esc(plannedSupplement)}</strong></li>`:''}
        ${reason?`<li>採用理由: ${esc(reason)}</li>`:''}
        ${summary?`<li class="sku-color-change">${esc(summary)}</li>`:''}
      </ul>
    </div>`
  }

  function stockPickPackageHtml(plan){
    if(!plan||!plan.auxStockPickAuto)return ''
    const reason=plan.auxStockPickReason||'初期在庫が最多のため、この候補をメインに採用'
    const kept=(plan.keptPackages||[]).map(k=>{
      const bits=[k.productName||'',k.supplier?`仕入 ${k.supplier}`:'',k.color?`色 ${k.color}`:'',k.processing?`加工 ${k.processing}`:'',k.material?`素材 ${k.material}`:''].filter(Boolean)
      return `<li>${esc(bits.join(' / '))}</li>`
    }).join('')
    return `<div class="sku-stock-pick">
      <p class="excelhint"><strong>自動決定済み（未登録）:</strong> ${esc(reason)}</p>
      ${nameColorSplitPreviewHtml(plan)}
      <p class="excelhint">採用メイン: ${esc(plan.productName||'—')}　仕入 ${esc(plan.supplier||'未設定')}
        ${plan.color?`　色 ${esc(plan.color)}`:''}${plan.processing?`　加工 ${esc(plan.processing)}`:''}${plan.material?`　素材 ${esc(plan.material)}`:''}</p>
      ${kept?`<p class="excelhint">非採用（別名・サブ・原文仕様として保持）:</p><ul class="sku-kept-list">${kept}</ul>`:''}
    </div>`
  }

  function autoDecidedBodyHtml(st,part){
    const plans=part.autoPlans||[]
    const p=(st.preview&&st.preview.plannedCounts)||{}
    const stockPicked=plans.filter(x=>x.auxStockPickAuto)
    const slice=pageSlice(st,'auto',plans)
    const cards=slice.pageItems.map(plan=>{
      const sizes=(plan.sizes||[]).slice(0,12).map(esc).join('、')
      const sizeMore=(plan.sizes||[]).length>12?` …他${(plan.sizes||[]).length-12}`:''
      const custs=(plan.customers||[]).slice(0,4).map(esc).join('、')
      const custMore=(plan.customers||[]).length>4?` …他${(plan.customers||[]).length-4}`:''
      const reason=plan.auxStockPickReason||plan.nameAdoptionRule||((plan.reviewReasons||[]).find(r=>r.code==='auto')||{}).label||'自動まとめ'
      return `<article class="sku-auto-plan ${plan.auxStockPickAuto?'sku-auto-stock-pick':''}" data-sku-section="auto">
        <strong>${esc(plan.label||'新規商品')}${plan.auxStockPickAuto?'（在庫採用）':''} <span class="badge">自動決定済み（未登録）</span></strong>
        <span>品番 ${esc(plan.code||'')}　${esc(plan.productName||'')}　仕入 ${esc(plan.supplier||'未設定')}</span>
        <p class="excelhint"><strong>決定内容・理由:</strong> ${esc(reason)}</p>
        ${stockPickPackageHtml(plan)}
        <span class="sub">取扱顧客 ${custs||'—'}${custMore}　予定サイズ ${sizes||'—'}${sizeMore}　（CSV ${plan.csvRowCount||0}行 → サイズ ${plan.sizeCount||0} / 顧客 ${plan.customerCount||0}）</span>
      </article>`
    }).join('')
    return `<div class="sku-auto-panel sku-section-body">
      <p class="excelhint">サイズ・顧客・＊/*・空白/全角半角・明示シノニムなどは自動で1商品へまとめています。ここも<strong>まだ未登録</strong>です（最終の「確認して登録」まで台帳は変わりません）。</p>
      <p class="excelhint">自動決定済み ${part.autoCount||0} 品番 / ${p.autoMergedCsvRows||0} 行${stockPicked.length?`（うち在庫採用 ${stockPicked.length}）`:''}</p>
      ${sectionPagerHtml('auto',slice,plans.length)}
      ${cards||'<p class="sub">自動決定の明細はありません。</p>'}
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
    const reason=aux.autoReason||aux.manualReason||g.auxStockPickReason||''
    const conflict=(d.primarySupplierManual&&aux.autoPrimarySupplier&&d.primarySupplier&&d.primarySupplier!==aux.autoPrimarySupplier)
      ||resolved.conflictWithAuto
    const existingPrimary=resolved.existingPrimarySupplier
      ||((g.existingProducts||[]).find(p=>Number(p.id)===Number(d.keepProductId))||{}).supplierName||''
    const chosen=d.primarySupplier||resolved.primarySupplier||aux.autoPrimarySupplier||''
    const changeNeeded=!!(existingPrimary&&chosen&&existingPrimary!==chosen&&(d.keepProductId||g.existingCount===1))
    const pkg=aux.adoptedPackage||g.adoptedPackage
    const kept=(aux.keptPackages||g.keptPackages||[]).map(k=>{
      const bits=[k.productName||'',k.supplier?`仕入 ${k.supplier}`:'',k.color?`色 ${k.color}`:'',k.processing?`加工 ${k.processing}`:'',k.material?`素材 ${k.material}`:''].filter(Boolean)
      return `<li>${esc(bits.join(' / '))}</li>`
    }).join('')
    const splitSrc=Object.assign({},g,aux,pkg?{adoptedPackage:pkg}:{})
    const votes=aux.ledgerNameVotes||g.ledgerNameVotes||[]
    const voteAdopted=aux.ledgerNameVoteAdopted||g.ledgerNameVoteAdopted
    const voteBlock=votes.length?`<div class="sku-ledger-votes">
      <p class="excelhint"><strong>商品台帳の名称票（正規化後）</strong></p>
      <ul class="sku-cand-list">${votes.map(v=>{
        const raw=(v.preNormalizeNames||[]).map(esc).join(' / ')||esc(v.normalizedName||'')
        const mark=voteAdopted&&v.normalizedName===voteAdopted.normalizedName?' ←採用':''
        return `<li><span class="sku-cand-name">${raw}</span> <span class="sku-cand-stock">${esc(String(v.count||0))}行${mark}</span></li>`
      }).join('')}</ul>
    </div>`:''
    const packageBlock=(pkg||aux.changeSummary||aux.plannedRegisteredColor||aux.colorDecidedNameUndecided||votes.length)?`<div class="sku-stock-pick">
      <p class="excelhint"><strong>${g.auxStockPickAuto||!g.needsDecision?'自動決定済み（未登録）':'提案'}:</strong> ${esc(aux.pairAdoptionRule||reason||'初期在庫が最多のため、この候補をメインに採用')}</p>
      ${voteBlock}
      ${(aux.pairCandidates||[]).length?`<ul class="sku-cand-list sku-pair-stock-list">${(aux.pairCandidates||[]).map(p=>{
        const bits=[p.productName||'',p.supplier?`仕入 ${p.supplier}`:''].filter(Boolean).join(' / ')
        const stockLabel=p.hasAuxMatch||p.auxStockDisplay&&p.auxStockDisplay!=='—'
          ?`初期在庫：${p.auxStockDisplay||'—'}`
          :'初期在庫：—'
        return `<li><span class="sku-cand-name">${esc(bits)}</span> <span class="sku-cand-stock">${esc(stockLabel)}</span></li>`
      }).join('')}</ul>`:''}
      ${nameColorSplitPreviewHtml(splitSrc)}
      ${pkg?`<p class="excelhint">採用ソース名: ${esc(pkg.productName||aux.sourceAdoptedName||'—')}　登録予定名: ${esc(aux.plannedRegisteredName||aux.suggestedOfficialName||pkg.plannedName||'—')}　仕入 ${esc(pkg.supplier||aux.suggestedPrimarySupplier||'—')}
        ${pkg.color||aux.adoptedColor||aux.plannedRegisteredColor?`　色 ${esc(pkg.color||aux.adoptedColor||aux.plannedRegisteredColor)}`:''}
        ${pkg.processing||aux.adoptedProcessing?`　加工 ${esc(pkg.processing||aux.adoptedProcessing)}`:''}
        ${pkg.material||aux.adoptedMaterial?`　素材 ${esc(pkg.material||aux.adoptedMaterial)}`:''}</p>`:''}
      ${kept?`<p class="excelhint">非採用（別名・サブ・原文仕様として保持）:</p><ul class="sku-kept-list">${kept}</ul>`:''}
    </div>`:''
    return `<div class="sku-aux-panel">
      <h4>初期在庫CSVによる通常仕入先・メイン候補判定</h4>
      <p class="excelhint">CSV先頭行では選びません。正の初期在庫合計が最多の候補が一意→そのフルパッケージをメイン採用。全候補が確認済み在庫0→商品台帳で最多の名称を採用（表記ゆれを合算。同数は要確認。明示色より優先）。在庫で決まらない（同数・該当なし・不正/空・負が複数）ときは明示色のある商品名を優先。正の在庫が1社だけ→通常仕入先。手動選択・色別に分けて登録は上書きしません。</p>
      ${packageBlock}
      ${reason&&!packageBlock?`<p class="excelhint">${esc(reason)}</p>`:''}
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

  function plannedSupplierReasonForGroup(st,g){
    const d=skuDecision(st,g.code)
    if(d.primarySupplierManual&&d.primarySupplier)return '手動選択'
    if(d.primarySupplier&&!(g.auxPrimary&&g.auxPrimary.autoPrimarySupplier&&d.primarySupplier===g.auxPrimary.autoPrimarySupplier)){
      // 明示選択だが manual フラグなし（旧UI互換）→理由なし
      if(!g.plannedPrimarySupplierReason)return ''
    }
    return g.plannedPrimarySupplierReason
      ||(g.primarySupplierResolved&&g.primarySupplierResolved.autoReason)
      ||(g.auxPrimary&&g.auxPrimary.autoReason)
      ||''
  }

  function plannedSupplierDisplay(st,g){
    const sup=plannedSupplierForGroup(st,g)
    if(!sup)return '未設定'
    const reason=plannedSupplierReasonForGroup(st,g)
    return reason?`${sup}（${reason}）`:sup
  }

  function candidatesCell(values,details){
    const detailList=Array.isArray(details)?details:null
    if(detailList&&detailList.length){
      return `<ul class="sku-cand-list">${detailList.map(d=>{
        const name=esc(d.value||'')
        const label=esc(d.auxStockLabel||(d.hasAuxMatch?`初期在庫：${d.auxStockDisplay||'—'}`:'—'))
        const cls=d.hasAuxMatch?'sku-cand-stock':'sku-cand-stock is-missing'
        return `<li><span class="sku-cand-name">${name}</span> <span class="${cls}">${label}</span></li>`
      }).join('')}</ul>`
    }
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
    if(dec==='merge')return '承認済み（未登録）'
    if(dec==='color_split')return '色分割選択済み（未登録）'
    if(dec==='separate')return '個別整理（未登録）'
    if(dec==='hold')return '保留'
    return '未決定'
  }

  function colorSplitFormHtml(st,g,d){
    const split=g.colorSplitPlans||{}
    const plans=split.plans||[]
    const unassigned=split.unassignedRows||[]
    const colorOpts=plans.map(p=>`<option value="${esc(p.colorKey||p.color)}">${esc(p.color)}</option>`).join('')
    const planCards=plans.map(p=>{
      const existing=(p.existingProducts||[]).map(x=>
        `<label class="check"><input type="radio" name="plColorKeep-${esc(g.code)}-${esc(p.colorKey)}" data-pl-color-keep="${esc(g.code)}" data-pl-color-key="${esc(p.colorKey)}" value="${x.id}" ${Number(p.keepProductId)===Number(x.id)?'checked':''} ${st.busy?'disabled':''}>
          既存を使う ID ${x.id}　${esc(x.name||'')}${x.color?` / ${esc(x.color)}`:''}</label>`
      ).join('')
      const collision=p.needsCollisionChoice
        ?`<p class="notice">社内管理品番「${esc(p.internalCode)}」が衝突しています。既存を使うか、品番を編集してください（上書きしません）。</p>
           <fieldset><legend>衝突の解決</legend>
             ${existing||'<span class="sub">既存候補なし（品番を編集）</span>'}
             <label class="check"><input type="radio" name="plColorKeep-${esc(g.code)}-${esc(p.colorKey)}" data-pl-color-keep="${esc(g.code)}" data-pl-color-key="${esc(p.colorKey)}" value="" ${!p.keepProductId?'checked':''} ${st.busy?'disabled':''}>品番を編集して新規</label>
           </fieldset>`
        :(p.collision&&p.keepProductId?`<p class="excelhint">既存商品 ID ${esc(String(p.keepProductId))} を使用予定</p>`:'')
      return `<div class="sku-color-plan" data-color-key="${esc(p.colorKey)}">
        <strong>色: ${esc(p.color)}</strong>
        <span class="sub">CSV行 ${(p.rowNos||[]).join(',')||'—'}　メーカー品番 ${esc(p.makerCode||g.code)}</span>
        <label>社内管理品番<input data-pl-color-internal="${esc(g.code)}" data-pl-color-key="${esc(p.colorKey)}" value="${esc(p.internalCode||'')}" placeholder="${esc(p.suggestedInternalCode||'')}" ${st.busy?'disabled':''}></label>
        <label>商品名<input data-pl-color-name="${esc(g.code)}" data-pl-color-key="${esc(p.colorKey)}" value="${esc(p.productName||'')}" ${st.busy?'disabled':''}></label>
        <label>色（確認）<input data-pl-color-color="${esc(g.code)}" data-pl-color-key="${esc(p.colorKey)}" value="${esc(p.color||'')}" ${st.busy?'disabled':''}></label>
        ${collision}
      </div>`
    }).join('')
    const unCards=unassigned.map(u=>`<div class="sku-color-unassigned">
      <span>行 ${u.rowNo}: ${esc(u.originalName||'')} — ${esc(u.reason||'')}</span>
      <label>登録先の色<select data-pl-color-dest="${esc(g.code)}" data-pl-row="${u.rowNo}" ${st.busy||!plans.length?'disabled':''}>
        <option value="">選択…</option>${colorOpts}
      </select></label>
    </div>`).join('')
    return `<div class="sku-color-split-form">
      <p class="excelhint">${esc(split.note||'色ごとに別商品として登録予定です。まだ保存していません。確認・編集のうえ最終登録してください。')}</p>
      <p class="excelhint">メーカー品番は元の「${esc(g.code)}」のまま（先頭ゼロも維持）。社内管理品番の初期値は「品番-色」です。緑と蛍光グリーンは別色です。</p>
      ${planCards||'<p class="notice">色候補をまだ作れません。商品名の色を確認するか、行き先を指定してください。</p>'}
      ${unCards?`<div class="sku-color-unassigned-list"><h4>行き先未定の行</h4>${unCards}</div>`:''}
      ${split.complete?'<p class="excelhint">予定は揃っています。再照合のあと「確認して商品を登録」で確定できます。</p>':'<p class="excelhint">未定の行き先・品番衝突を解消してから再照合してください。</p>'}
    </div>`
  }

  function matchesSkuFilters(st,g){
    const q=(st.skuQ||'').trim().toLowerCase()
    const reason=st.skuReasonFilter||''
    if(reason==='bulk'&&g.reviewTier!=='bulk')return false
    if(reason==='individual'&&g.reviewTier!=='individual')return false
    if(reason==='pending'&&effectiveDecision(st,g))return false
    if(reason==='merge'&&effectiveDecision(st,g)!=='merge')return false
    if(reason==='hold'&&effectiveDecision(st,g)!=='hold')return false
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
        ${namePlan.adoptionRule?`<p class="excelhint">名称採用ルール: ${esc(namePlan.adoptionRule)}${namePlan.provisional?`（ステータス「${esc(st.preview&&st.preview.provisionalNameTag||'名称未整理')}」※名前には埋め込みません）`:''}</p>`:''}
        ${(namePlan.nameCandidates&&namePlan.nameCandidates.length>1)?`<p class="excelhint">名称候補: ${namePlan.nameCandidates.map(esc).join('／')}</p>`:''}
        ${(namePlan.auxNameReasons&&namePlan.auxNameReasons.length)?`<p class="excelhint">${namePlan.auxNameReasons.map(esc).join(' ')}</p>`:''}
        ${mappings?`<ul class="sku-name-map">${mappings}</ul>`:''}
        ${auxPanel}
        <div class="bar sku-decision-btns">
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="merge" class="${decision==='merge'?'primary':''}" ${st.busy?'disabled':''}>同じ商品としてまとめる</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="color_split" class="${decision==='color_split'?'primary':''}" ${st.busy?'disabled':''}>色別に分けて登録</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="separate" class="${decision==='separate'?'primary':''}" ${st.busy?'disabled':''}>別物のため個別に整理する</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="hold" class="${decision==='hold'?'primary':''}" ${st.busy?'disabled':''}>今回は保留する</button>
        </div>
        ${decision==='color_split'?colorSplitFormHtml(st,g,d):''}
        ${decision==='merge'?`<div class="sku-merge-form">
          ${nameColorSplitPreviewHtml(Object.assign({},g,g.auxPrimary||{},{productName:plannedNameForGroup(st,g),color:d.color||g.plannedRegisteredColor||g.plannedColor||''}))}
          <label>公式商品名<select data-pl-official-name="${esc(g.code)}" ${st.busy?'disabled':''}><option value="">仮代表名を使う…</option>${nameOpts}</select></label>
          <label>代表名（編集可）<input data-pl-official-name-edit="${esc(g.code)}" value="${esc(d.officialName||g.plannedRegisteredName||'')}" placeholder="空なら仮代表名（名称未整理ステータス）" ${st.busy?'disabled':''}></label>
          <label>色<input data-pl-official-color="${esc(g.code)}" value="${esc(d.color||g.plannedRegisteredColor||g.plannedColor||(g.auxPrimary&&g.auxPrimary.plannedRegisteredColor)||'')}" ${st.busy?'disabled':''}></label>
          <label>通常仕入先<select data-pl-primary-sup="${esc(g.code)}" ${st.busy?'disabled':''}><option value="">未指定</option>${supOpts}</select></label>
          ${d.primarySupplierManual?'<p class="excelhint">通常仕入先は手動選択です（初期在庫CSVの再読込でも上書きしません）。</p>':(g.auxPrimary&&g.auxPrimary.autoPrimarySupplier?`<p class="excelhint">通常仕入先は自動設定: ${esc(plannedSupplierDisplay(st,g))}</p>`:'')}
          <p class="excelhint">予定代表名: ${esc(plannedNameForGroup(st,g)||'—')}　予定色: ${esc(d.color||g.plannedRegisteredColor||g.plannedColor||'（空）')}　予定通常仕入先: ${esc(plannedSupplierDisplay(st,g))}</p>
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

  function skuListRowsHtml(st,groups,opts){
    opts=opts||{}
    const section=opts.section||''
    const showApproveHint=!!opts.showApproveHint
    if(!(groups||[]).length){
      return '<tr><td colspan="8">該当する品番はありません。</td></tr>'
    }
    return groups.map(g=>{
      const d=skuDecision(st,g.code)
      const decision=d.decision||''
      const checked=!!st.selectedCodes[g.code]
      const expanded=!!st.expandedCodes[g.code]
      const tier=g.reviewTier||'individual'
      const plannedName=plannedNameForGroup(st,g)
      const provisional=!(d.officialName)&&(g.namePlan&&g.namePlan.provisional||g.provisionalName)
      const stateLabel=decision?decisionLabel(decision):(tier==='bulk'?'未承認（一括承認候補）':(TIER_LABELS[tier]||g.reviewTierLabel||'未決定'))
      return `<tr class="sku-list-row sku-tier-${esc(tier)} ${decision?`sku-dec-${esc(decision)}`:''}" data-sku-code="${esc(g.code)}" data-sku-section="${esc(section)}" data-review-tier="${esc(tier)}" data-decision="${esc(decision||'')}">
        <td class="sku-check" data-label="選択"><input type="checkbox" data-pl-sku-check="${esc(g.code)}" ${checked?'checked':''} ${st.busy?'disabled':''}></td>
        <td class="sku-code" data-label="品番"><button type="button" class="linkish" data-pl-sku-expand="${esc(g.code)}">${esc(g.codeDisplay||g.code)}</button>
          <div class="sub">${esc(TIER_LABELS[tier]||g.reviewTierLabel||'')}</div></td>
        <td class="sku-names" data-label="商品名候補">${candidatesCell(g.nameCandidates||(g.variants||[]).map(v=>v.productName),g.nameCandidateDetails)}</td>
        <td class="sku-sups" data-label="仕入先候補">${candidatesCell(g.supplierCandidates||(g.variants||[]).map(v=>v.supplier),g.supplierCandidateDetails)}</td>
        <td class="sku-other" data-label="色・加工など">${otherDiffsCell(g)}</td>
        <td class="sku-why" data-label="要確認理由">${reasonCell(g)}</td>
        <td class="sku-plan" data-label="予定の代表名・通常仕入先">
          <div><strong>${esc(plannedName||'—')}</strong>${provisional?' <span class="badge">名称未整理</span>':''}</div>
          ${showApproveHint&&!decision?`<div class="sub">採用理由: ${(g.reviewReasons||[]).map(r=>esc(r.label||'')).filter(Boolean).join('／')||'名差・仕入先差のみ'}</div>
          <div class="sub">登録予定: ${esc(plannedName||'—')} / 通常仕入先 ${esc(plannedSupplierDisplay(st,g))}</div>`:''}
          ${(g.plannedRegisteredColor||g.plannedColor||(g.auxPrimary&&g.auxPrimary.plannedRegisteredColor)||(d.color))?`<div class="sub">登録予定色: ${esc(d.color||g.plannedRegisteredColor||g.plannedColor||(g.auxPrimary&&g.auxPrimary.plannedRegisteredColor)||'')}</div>`:''}
          ${(g.sourceAdoptedName||(g.auxPrimary&&g.auxPrimary.sourceAdoptedName))?`<div class="sub">ソース名: ${esc(g.sourceAdoptedName||g.auxPrimary.sourceAdoptedName)}</div>`:''}
          ${(g.changeSummary||(g.auxPrimary&&g.auxPrimary.changeSummary))?`<div class="sub sku-color-change">${esc(g.changeSummary||g.auxPrimary.changeSummary)}</div>`:''}
          <div class="sub">通常仕入先: ${esc(plannedSupplierDisplay(st,g))}</div>
          ${(g.nameAdoptionRule||(g.namePlan&&g.namePlan.adoptionRule))?`<div class="sub">${esc(g.nameAdoptionRule||g.namePlan.adoptionRule)}</div>`:''}
        </td>
        <td class="sku-action" data-label="操作">
          <div class="sku-action-label ${decision?`is-${esc(decision)}`:''}">${esc(stateLabel)}</div>
          <div class="bar sku-mini-btns">
            <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="merge" class="${decision==='merge'?'primary':'quiet'}" ${st.busy?'disabled':''}>まとめ</button>
            <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="color_split" class="${decision==='color_split'?'primary':'quiet'}" ${st.busy?'disabled':''}>色別に分けて登録</button>
            <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="hold" class="${decision==='hold'?'primary':'quiet'}" ${st.busy?'disabled':''}>保留</button>
            <button type="button" data-pl-sku-expand="${esc(g.code)}" class="quiet">${expanded?'詳細を閉じる':'詳細を開く'}</button>
          </div>
        </td>
      </tr>${expanded?skuRowDetailHtml(st,g):''}`
    }).join('')
  }

  function skuCompareTableHtml(st,groups,opts){
    return `<div class="sku-compare-wrap"><table class="sku-compare-table">
      <thead><tr>
        <th></th><th>品番</th><th>商品名候補</th><th>仕入先候補</th><th>色・加工など</th><th>要確認理由</th><th>予定の代表名・通常仕入先</th><th class="sku-action-h">操作</th>
      </tr></thead>
      <tbody>${skuListRowsHtml(st,groups,opts)}</tbody>
    </table></div>`
  }

  function sectionToggleBtn(st,key,openLabel){
    const open=!!(st.sectionOpen&&st.sectionOpen[key])
    return `<button type="button" class="quiet" data-pl-section-toggle="${esc(key)}" ${st.busy?'disabled':''}>${open?'閉じる':(openLabel||'開く')}</button>`
  }

  function skuGroupsHtml(st){
    const part=partitionJudgmentGroups(st)
    const counts=judgmentStatusCounts(st)
    const hasAny=counts.unresolved||counts.unapproved||counts.approved||counts.autoDecided||counts.hold||counts.separate||counts.excluded
    if(!hasAny&&!(st.preview&&(st.preview.skuGroups||[]).length))return ''
    const selected=selectedCodeList(st)
    const filterList=(arr)=>arr.filter(g=>matchesSkuFilters(st,g))
    const indivAll=filterList(part.individual)
    const bulkAll=filterList(part.bulkUnapproved)
    const approvedAll=filterList(part.approved)
    const holdAll=filterList(part.hold)
    const separateAll=filterList(part.separate)
    const indivSlice=pageSlice(st,'individual',indivAll)
    const bulkSlice=pageSlice(st,'bulk',bulkAll)
    const approvedSlice=pageSlice(st,'approved',approvedAll)
    const holdSlice=pageSlice(st,'hold',holdAll)
    const separateSlice=pageSlice(st,'separate',separateAll)
    const bulkPages=bulkSlice.pages
    const indivOpen=!!(st.sectionOpen&&st.sectionOpen.individual)
    const bulkOpen=!!(st.sectionOpen&&st.sectionOpen.bulk)
    const autoOpen=!!(st.sectionOpen&&st.sectionOpen.auto)
    const holdOpen=!!(st.sectionOpen&&st.sectionOpen.hold)
    const bulkApproveLabel=`一括承認候補${part.bulkUnapproved.length}件を承認`
    const bulkScopeHint=part.bulkUnapproved.length
      ?`承認対象は「一括承認候補」区分の未承認 ${part.bulkUnapproved.length} 件すべて（${bulkPages}ページ分）。個別確認が必要・保留・自動決定済みは含みません。折りたたみだけでは承認されません。`
      :'未承認の一括承認候補はありません。'

    const indivBody=indivOpen?`<div class="sku-section-body">
      ${sectionPagerHtml('individual',indivSlice,indivAll.length)}
      ${skuCompareTableHtml(st,indivSlice.pageItems,{section:'individual'})}
      ${separateAll.length?`<div class="sku-subsection">
        <h4>個別整理済み（未登録） ${separateAll.length} 件</h4>
        ${sectionPagerHtml('separate',separateSlice,separateAll.length)}
        ${skuCompareTableHtml(st,separateSlice.pageItems,{section:'separate'})}
      </div>`:''}
    </div>`:''

    const bulkBody=bulkOpen?`<div class="sku-section-body">
      <p class="excelhint">${esc(bulkScopeHint)}</p>
      <h4>未承認 ${part.bulkUnapproved.length} 件</h4>
      ${sectionPagerHtml('bulk',bulkSlice,bulkAll.length)}
      ${skuCompareTableHtml(st,bulkSlice.pageItems,{section:'bulk',showApproveHint:true})}
      <h4>承認済み（未登録） ${part.approved.length} 件</h4>
      <p class="excelhint">一括承認後も内容を確認できます。最終登録が終わるまで「未登録」です（折りたたみ＝承認ではありません）。</p>
      ${sectionPagerHtml('approved',approvedSlice,approvedAll.length)}
      ${skuCompareTableHtml(st,approvedSlice.pageItems,{section:'approved'})}
    </div>`:''

    const autoBody=autoOpen?autoDecidedBodyHtml(st,part):''

    const holdBody=holdOpen?`<div class="sku-section-body">
      <p class="excelhint">保留は登録対象外です。件数は隠さず残します。対象外 ${part.excludedRows||0} 行も outcome に計上されています。</p>
      ${sectionPagerHtml('hold',holdSlice,holdAll.length)}
      ${skuCompareTableHtml(st,holdSlice.pageItems,{section:'hold'})}
    </div>`:''

    return `<div class="sku-groups-panel sku-judgment-sections" data-sku-judgment-sections>
      <h3>品番ごとの判断一覧（通常品番）</h3>
      <p class="excelhint">区分は判断状態（reviewTier / needsDecision / decision）で振り分けます。色は見た目の補助だけです。1行=1品番。未解決から順に対応してください。代表名未確定時は「名称未整理」ステータス（名前へは埋め込みません）。</p>
      ${judgmentStatusBarHtml(st)}
      <div class="bar sku-list-toolbar">
        <input type="search" id="plSkuQ" placeholder="品番・商品名・仕入先・理由で検索" value="${esc(st.skuQ||'')}" ${st.busy?'disabled':''}>
        <select id="plSkuReason" ${st.busy?'disabled':''}>
          <option value="">理由すべて</option>
          <option value="bulk" ${st.skuReasonFilter==='bulk'?'selected':''}>一括承認候補</option>
          <option value="individual" ${st.skuReasonFilter==='individual'?'selected':''}>個別確認</option>
          <option value="pending" ${st.skuReasonFilter==='pending'?'selected':''}>未決定のみ</option>
          <option value="merge" ${st.skuReasonFilter==='merge'?'selected':''}>承認済み</option>
          <option value="hold" ${st.skuReasonFilter==='hold'?'selected':''}>保留済</option>
          <option value="name_wording" ${st.skuReasonFilter==='name_wording'?'selected':''}>名差</option>
          <option value="supplier" ${st.skuReasonFilter==='supplier'?'selected':''}>仕入先差</option>
          <option value="color" ${st.skuReasonFilter==='color'?'selected':''}>色の疑い</option>
          <option value="processing" ${st.skuReasonFilter==='processing'?'selected':''}>加工の疑い</option>
          <option value="kind" ${st.skuReasonFilter==='kind'?'selected':''}>商品種の疑い</option>
        </select>
        <button type="button" id="plSkuSelectPage" ${st.busy||!(indivOpen||bulkOpen)?'disabled':''}>開いている区分の表示ページを選択</button>
        <button type="button" id="plSkuClearSel" class="quiet" ${st.busy||!selected.length?'disabled':''}>選択解除</button>
        <button type="button" id="plSkuBulkHold" ${st.busy||!selected.length?'disabled':''}>選択を今回は保留する</button>
        <span class="excelhint">選択中 ${selected.length} 品番</span>
      </div>

      <section class="sku-judgment-section sku-section-individual" data-sku-section-block="individual">
        <header class="sku-section-header bar">
          <h4>個別確認が必要 <span class="sku-section-count">${part.individual.length}件</span>${part.separate.length?` <span class="sub">／個別整理 ${part.separate.length}</span>`:''}</h4>
          ${sectionToggleBtn(st,'individual')}
        </header>
        ${indivOpen?indivBody:`<p class="excelhint sku-section-collapsed-hint">閉じています。未解決 ${part.individual.length} 件${part.separate.length?`／個別整理 ${part.separate.length} 件`:''}（開いても選択・編集は消えません）。</p>`}
      </section>

      <section class="sku-judgment-section sku-section-bulk" data-sku-section-block="bulk">
        <header class="sku-section-header bar">
          <h4>一括承認候補 <span class="sku-section-count">${part.bulkUnapproved.length}件</span>
            <span class="sub">未承認 ${part.bulkUnapproved.length} / 承認済み（未登録） ${part.approved.length}</span></h4>
          ${sectionToggleBtn(st,'bulk')}
          <button type="button" id="plSkuBulkApproveSection" class="primary" ${st.busy||!part.bulkUnapproved.length?'disabled':''}>${esc(bulkApproveLabel)}</button>
        </header>
        <p class="excelhint">${esc(bulkScopeHint)}</p>
        ${bulkOpen?bulkBody:''}
      </section>

      <section class="sku-judgment-section sku-section-auto" data-sku-section-block="auto">
        <header class="sku-section-header bar">
          <h4>自動決定済み <span class="sku-section-count">${part.autoCount}件</span> <span class="badge">未登録</span></h4>
          ${sectionToggleBtn(st,'auto')}
        </header>
        ${autoOpen?autoBody:`<p class="excelhint sku-section-collapsed-hint">閉じています。開くと決定内容と理由を確認できます（まだ未登録・${part.autoCount}件）。</p>`}
      </section>

      <section class="sku-judgment-section sku-section-hold" data-sku-section-block="hold">
        <header class="sku-section-header bar">
          <h4>保留・対象外 <span class="sku-section-count">保留 ${part.hold.length} / 対象外 ${part.excludedRows||0}</span></h4>
          ${sectionToggleBtn(st,'hold')}
        </header>
        ${holdOpen?holdBody:`<p class="excelhint sku-section-collapsed-hint">件数は上の登録前サマリでも確認できます。黙って非表示にはしません（保留 ${part.hold.length} / 対象外 ${part.excludedRows||0}）。</p>`}
      </section>
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
    // メーカー情報で補正: 要確認かつ具体的なメーカー品番がある行だけ
    const mk=r.makerCorrection||{}
    if(r.status==='needs_review'&&mk.eligible&&mk.makerSku){
      bits.push(`<button type="button" class="quiet" data-pl-maker-correct="${r.rowNo}" ${st.busy?'disabled':''}>メーカー情報で補正</button>`)
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

  function parenCell(st,r){
    const pc=r.parenClassification||{}
    const edit=st.rowClassifications[String(r.rowNo)]||{}
    const planned=edit.plannedName!=null?edit.plannedName:(pc.plannedName||'')
    const color=edit.color!=null?edit.color:(pc.color||'')
    const processing=edit.processing!=null?edit.processing:(pc.processing||'')
    const material=edit.material!=null?edit.material:(pc.material||'')
    const supplement=edit.supplement!=null?edit.supplement:(pc.supplement||r.plannedRegisteredSupplement||'')
    const unc=(edit.unclassified!=null?edit.unclassified:(pc.unclassified||[]))
    const uncText=Array.isArray(unc)?unc.join('、'):String(unc||'')
    const notices=Array.isArray(pc.infoNotices)?pc.infoNotices:[]
    const noticeHtml=notices.length
      ?`<div class="excelhint pl-paren-info">${notices.map(n=>esc(n)).join('<br>')}</div>`
      :''
    const scopedPreview=(pc.scopedColorCorrection||r.scopedColorCorrectionReason||r.changeSummary)
      ? nameColorSplitPreviewHtml(Object.assign({},r,pc,{
          sourceAdoptedName:(r.csv&&r.csv.productName)||pc.originalName||'',
          plannedRegisteredName:planned,
          plannedRegisteredColor:color,
          plannedRegisteredSupplement:supplement,
          changeSummary:pc.changeSummary||r.changeSummary||'',
          scopedColorCorrectionReason:r.product&&r.product.scopedColorCorrectionReason
        }))
      :''
    const flag=pc.needsConfirm?'要確認':(pc.autoClassified||pc.scopedColorCorrection?'自動':'—')
    const disabled=st.busy?'disabled':''
    return `<div class="pl-paren-edit">
      <div class="excelhint">${esc(flag)}${pc.keptOriginal&&pc.needsConfirm?'（原文維持）':''}</div>
      ${noticeHtml}
      ${scopedPreview}
      <label>予定名<input data-pl-paren-name="${r.rowNo}" value="${esc(planned)}" ${disabled}></label>
      <label>色<input data-pl-paren-color="${r.rowNo}" value="${esc(color)}" ${disabled}></label>
      <label>補足<input data-pl-paren-supp="${r.rowNo}" value="${esc(supplement)}" ${disabled}></label>
      <label>加工<input data-pl-paren-proc="${r.rowNo}" value="${esc(processing)}" ${disabled}></label>
      <label>生地<input data-pl-paren-mat="${r.rowNo}" value="${esc(material)}" ${disabled}></label>
      <label>未分類<input data-pl-paren-unc="${r.rowNo}" value="${esc(uncText)}" ${disabled}></label>
      ${pc.needsConfirm?`<label class="pl-paren-confirm"><input type="checkbox" data-pl-paren-ok="${r.rowNo}" ${edit.confirmed?'checked':''} ${disabled}> 分類を確認した</label>`:''}
    </div>`
  }

  function makerLookupPanelHtml(st){
    const panel=st.makerLookup
    if(!panel)return ''
    const res=panel.result||{}
    const cands=Array.isArray(res.candidates)?res.candidates:[]
    const status=esc(res.statusLabel||panel.statusLabel||'—')
    const note=esc(res.note||'')
    const ref=res.referenceUrl||''
    const list=cands.map((c,i)=>{
      const diff=c.diff||{}
      return `<li class="pl-maker-cand">
        <div><strong>${esc(c.name||'—')}</strong>${c.color?`　色 ${esc(c.color)}`:''}${c.supplement?`　補足 ${esc(c.supplement)}`:''}
          ${c.makerSku?`　品番 ${esc(c.makerSku)}`:''}
          ${c.manufacturer?`　メーカー ${esc(c.manufacturer)}`:''}
          ${c.fromSaved?' <span class="badge">保存済み</span>':''}
          ${c.preferred?' <span class="sub">優先候補</span>':''}
        </div>
        <div class="excelhint">現在の予定: 名 ${esc(diff.currentName||'—')} / 色 ${esc(diff.currentColor||'（空）')} / 補足 ${esc(diff.currentSupplement||'（空）')}</div>
        <div class="excelhint">差分: 名 ${diff.nameChanged?'変わる':'同じ'} / 色 ${diff.colorChanged?'変わる':'同じ'} / 補足 ${diff.supplementChanged?'変わる':'同じ'}</div>
        ${c.sourceUrl?`<div class="excelhint"><a href="${esc(c.sourceUrl)}" target="_blank" rel="noopener">参考URL</a></div>`:''}
        <button type="button" class="primary" data-pl-maker-adopt="${i}" ${st.busy?'disabled':''}>採用</button>
      </li>`
    }).join('')
    return `<div class="notice pl-maker-lookup-panel" role="region" aria-label="メーカー情報で補正">
      <div class="bar">
        <strong>メーカー情報で補正</strong>
        <span class="sub">行 ${esc(String(panel.rowNo||''))}　メーカー品番 ${esc(panel.makerSku||'')}
          ${panel.manufacturer?`　メーカー ${esc(panel.manufacturer)}`:''}
          ${panel.supplier?`　仕入先（検索キー外） ${esc(panel.supplier)}`:''}
        </span>
        <button type="button" class="quiet" id="plMakerLookupClose" ${st.busy?'disabled':''}>閉じる</button>
      </div>
      <p>状態: <strong>${status}</strong></p>
      ${note?`<p class="excelhint">${note}</p>`:''}
      ${panel.error?`<p class="product-ledger-import-error" role="alert">${esc(panel.error)}</p>`:''}
      ${cands.length?`<ul class="pl-maker-cand-list">${list}</ul>`:`<p class="excelhint">採用できる候補はありません。</p>`}
      ${ref?`<p class="excelhint"><a href="${esc(ref)}" target="_blank" rel="noopener">参考ページを開く</a>（開いただけでは補正完了になりません）</p>`:''}
      <p class="excelhint">「採用」はプレビューの予定名／色／補足だけを更新します。台帳への登録は「確認して商品を登録」で行います。原文名は照合用に残します。</p>
    </div>`
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
      const pc=r.parenClassification||{}
      return `<tr class="legacy-status-${esc(r.status)}">
        <td class="lc-row">${r.rowNo}</td>
        <td class="lc-status">${esc(r.statusLabel||'')}${pc.needsConfirm?' <span class="badge">括弧</span>':''}</td>
        <td class="lc-customer">${esc(csv.customer||'')}</td>
        <td class="lc-customer">${esc(csv.supplier||'')}</td>
        <td class="lc-code">${esc(csv.codeDisplay||csv.code||'')}</td>
        <td class="lc-product">${esc(csv.productName||'')}${csv.usedMarkerPossible?' <span class="badge">＊中古候補</span>':''}</td>
        <td class="lc-paren">${parenCell(st,r)}</td>
        <td class="lc-size">${esc(csv.size||'')}</td>
        <td class="lc-handle">${targetCell(r)}</td>
        <td class="lc-detail"><span class="lc-clip">${esc(reason)}</span></td>
        <td class="lc-actions">${rowActions(st,r)}</td>
      </tr>`
    }).join('')||'<tr><td colspan="11">該当する行はありません。</td></tr>'
    return `<div class="legacy-csv-table-wrap"><table class="legacy-csv-table product-ledger-table">
      <thead><tr><th>行</th><th>判定</th><th>顧客</th><th>仕入先</th><th>品番</th><th>原文名</th><th>括弧分類（予定名／色／加工／生地）</th><th>サイズ</th><th>登録先</th><th>理由</th><th>選択</th></tr></thead>
      <tbody>${body}</tbody></table></div>
      <div class="bar legacy-csv-pager">
        <button type="button" id="plPrev" ${st.page<=1||st.busy?'disabled':''}>前へ</button>
        <span>${st.page} / ${pages}ページ（表示 ${all.length}件）</span>
        <button type="button" id="plNext" ${st.page>=pages||st.busy?'disabled':''}>次へ</button>
      </div>`
  }

  function timingsHtml(st){
    const t=st.preview&&st.preview.timingsMs
    if(!t)return ''
    return `<p class="excelhint" data-pl-timings>計測: CSV読込 ${t.csvParse||0}ms　商品照合 ${t.productJudgment||0}ms　初期在庫準備 ${t.openingStockPrep||0}ms　合計 ${t.total||0}ms</p>`
  }

  function resultHtml(st){
    const r=st.result
    if(!r)return ''
    const c=r.counts||{}
    const s=r.confirmSummary||{}
    if(r.importMode===MODE_OPENING||(r.registerOpeningStock&&!c.productsCreated&&!s.productsToCreate)){
      return `<div class="notice legacy-csv-result">
        <strong>${r.already?'同じ操作は登録済みです':'初期在庫の登録が完了しました'}</strong>
        <p>初期在庫: 新規 ${c.openingCreated||0}　数量更新 ${c.openingQtyUpdated||0}　変更なし ${c.openingUnchanged||0}　保留 ${c.openingHeld||0}　補完候補スキップ ${c.openingComplementSkipped||0}</p>
      </div>`
    }
    const openingBit=r.registerOpeningStock
      ?`<p>初期在庫: 新規 ${c.openingCreated||0}　数量更新 ${c.openingQtyUpdated||0}　変更なし ${c.openingUnchanged||0}　保留 ${c.openingHeld||0}　補完候補スキップ ${c.openingComplementSkipped||0}</p>`
      :`<p>在庫数量・金額・履歴: ${r.stockUnchanged?'変化なし':'要確認'}（商品登録段階では在庫を書きません）</p>`
    return `<div class="notice legacy-csv-result">
      <strong>${r.already?'同じ操作は登録済みです':'商品登録が完了しました'}</strong>
      <p>新規商品 ${c.productsCreated||0}　再利用行 ${c.reused||0}　統合元 ${c.productsMerged||0}
     　サイズ追加 ${c.sizesAdded||0}　取扱新規 ${c.handlesCreated||0}
     　顧客 ${c.customersCreated||0}　仕入先 ${c.suppliersCreated||0}　サブ仕入先 ${c.subSuppliersAdded||0}
     　確定行 ${c.committedRows||0}　保留 ${c.deferred||r.deferredCount||0}　要確認残 ${r.reviewRemaining||0}　対象外 ${r.excludedCount||0}</p>
      ${s.csvRows!=null?`<p class="excelhint">確定時サマリ: CSV行 ${s.csvRows} / 新規商品 ${s.productsToCreate} / 再利用 ${s.productsToReuse} / 統合 ${s.productsToMerge}（行数≠新規商品数）</p>`:''}
      ${openingBit}
      <p class="excelhint">初期在庫は画面を閉じて「旧CSV一括取り込み」→「初期在庫を登録」から行えます（マップは保存済み）。</p>
    </div>`
  }

  function openingStockSectionsHtml(st){
    const os=st.preview&&st.preview.openingStock
    if(!os||!os.enabled)return ''
    const sum=st.preview.openingStockSummary||{}
    const counts=os.counts||{}
    const countLine=OPEN_STATUS_ORDER.map(k=>{
      const n=counts[k]||0
      if(!n&&k!=='create'&&k!=='complement')return ''
      return `${OPEN_STATUS_LABELS[k]} ${n}件`
    }).filter(Boolean).join('　')
    const masters=os.mastersToCreate||{}
    const masterBits=[]
    if((masters.customers||[]).length)masterBits.push(`顧客新規予定: ${masters.customers.slice(0,8).map(esc).join('、')}${(masters.customers.length>8?` …他${masters.customers.length-8}`:'')}`)
    if((masters.suppliers||[]).length)masterBits.push(`仕入先新規予定: ${masters.suppliers.slice(0,8).map(esc).join('、')}${(masters.suppliers.length>8?` …他${masters.suppliers.length-8}`:'')}`)
    if((masters.sites||[]).length)masterBits.push(`拠点新規予定: ${masters.sites.map(esc).join('、')}`)
    if((masters.shelves||[]).length)masterBits.push(`棚新規予定: ${masters.shelves.slice(0,8).map(esc).join('、')}`)
    const plans=(os.plans||[]).slice(0,60)
    const rows=plans.map(p=>{
      const t=p.target||{}
      const csv=p.csv||{}
      const place=p.place||{}
      const stock=p.stock||{}
      const sel=st.openingSelections[String(p.rowNo)]||{}
      const qtyCheck=p.status==='qty_diff'
        ?`<label class="check"><input type="checkbox" data-pl-open-qty="${p.rowNo}" ${sel.updateQty?'checked':''}>CSV数量へ更新</label>`
        :''
      return `<tr>
        <td>${p.rowNo}</td>
        <td>${esc(p.statusLabel||p.status)}</td>
        <td>${esc(t.officialName||t.productName||'')}<div class="excelhint">元: ${esc(csv.productName||'')}</div></td>
        <td>${esc(t.size||csv.size||'')}</td>
        <td>${esc(p.conditionLabel||'')}</td>
        <td class="num">${p.qty!=null?esc(String(p.qty)):'—'}</td>
        <td>${esc(place.siteName||'')}${place.shelfName?` / ${esc(place.shelfName)}`:''}${place.usedDefaultSite?' <span class="excelhint">(空→未確認)</span>':''}</td>
        <td class="num">${p.unitCost!=null?esc(String(p.unitCost)):'—'}</td>
        <td>${stock.exists!=null?`現在 ${esc(String(stock.currentQty))}`:'—'}</td>
        <td>${esc((p.reasons||[]).join(' '))}${qtyCheck}</td>
      </tr>`
    }).join('')
    const more=(os.plans||[]).length>60?`<p class="excelhint">先頭60件を表示（全 ${(os.plans||[]).length} 件）。絞り込みは今後拡張予定。</p>`:''
    return `<section class="product-ledger-opening-preview">
      <h3>初期在庫の登録予定</h3>
      <p class="notice">${esc(os.note||sum.note||'確認後に初期在庫を登録します。数量・新品中古・拠点・単価を確認してください。')}</p>
      <p><strong>${esc(countLine)}</strong></p>
      ${masterBits.length?`<p class="excelhint">${masterBits.join('　')}</p>`:''}
      <div class="legacy-csv-table-wrap"><table class="data sku-compare-table">
        <thead><tr><th>行</th><th>判定</th><th>登録先商品</th><th>サイズ</th><th>新品中古</th><th>数量</th><th>拠点/棚</th><th>単価</th><th>既存在庫</th><th>理由</th></tr></thead>
        <tbody>${rows||'<tr><td colspan="10">該当行なし</td></tr>'}</tbody>
      </table></div>
      ${more}
    </section>`
  }

  function modeChooserHtml(st){
    return `<div class="product-ledger-mode-chooser">
      <p class="merge-lead">旧CSV一括取り込みは<strong>二段階</strong>です。先に商品を登録し、あとから初期在庫を登録します（待ち時間を分けます）。</p>
      <div class="bar product-ledger-mode-actions">
        <button type="button" class="primary" id="plModeProducts" ${st.busy?'disabled':''}>商品を登録</button>
        <button type="button" class="primary" id="plModeOpening" ${st.busy?'disabled':''}>初期在庫を登録</button>
      </div>
      <p class="excelhint">商品登録では初期在庫CSVを判定補助に使えても在庫は書きません。初期在庫登録は商品台帳CSVの再選択不要です。</p>
    </div>`
  }

  function filePickHtml(st){
    if(isOpeningMode(st)){
      const auxLabel=st.auxFileName?'初期在庫CSVを変更':'初期在庫CSVを選ぶ（必須）'
      const auxHint=st.auxFileName&&st.auxFileSize?`（${Number(st.auxFileSize).toLocaleString('ja-JP')} バイト）`:''
      const auxMeta=st.preview&&st.preview.auxOpeningStock
      return `<div class="product-ledger-files">
        <p class="notice" role="status">商品台帳CSVの再選択は不要です。商品登録時に保存した原文→登録先マップで照合します（メイン名選定のやり直しなし）。</p>
        <div class="bar product-ledger-filebar">
          <input type="file" id="plAuxFile" class="product-ledger-file-input" accept=".csv,text/csv" ${st.busy?'disabled':''}>
          <button type="button" id="plPickAux" ${st.busy?'disabled':''}>${esc(auxLabel)}</button>
          <span class="excelhint product-ledger-filename">${esc(st.auxFileName||'未選択')}${auxHint?` ${esc(auxHint)}`:''}${auxMeta?`（${esc(auxMeta.encoding||'')} / ${auxMeta.rowCount||0}行）`:''}</span>
        </div>
        <div class="bar">
          <label>在庫基準日<input id="plBasis" type="date" value="${esc(st.basisDate||'')}" ${st.busy?'disabled':''}></label>
          <label>拠点空欄時の登録先<select id="plDefaultSite" ${st.busy?'disabled':''}>${siteOptions(st.defaultSiteName)}</select></label>
        </div>
        <div class="bar">
          <button type="button" id="plPreview" class="primary" ${!st.auxFileB64||st.busy?'disabled':''}>読み込み・照合</button>
          <button type="button" id="plBackMode" class="quiet" ${st.busy?'disabled':''}>段階選択に戻る</button>
        </div>
        ${st.progressLabel?`<p class="excelhint" role="status">${esc(st.progressLabel)}</p>`:''}
        ${st.previewError?`<p class="notice product-ledger-import-error" role="alert">${esc(st.previewError)}</p>`:''}
      </div>`
    }
    const label=st.fileName?'台帳CSVを変更':'商品台帳CSVを選ぶ（必須）'
    const sizeHint=st.fileName&&st.fileSize?`（${Number(st.fileSize).toLocaleString('ja-JP')} バイト）`:''
    const auxLabel=st.auxFileName?'初期在庫CSVを変更':'初期在庫CSV（任意・判定補助）'
    const auxHint=st.auxFileName&&st.auxFileSize?`（${Number(st.auxFileSize).toLocaleString('ja-JP')} バイト）`:''
    const auxMeta=st.preview&&st.preview.auxOpeningStock
    const auxSelected=!!st.auxFileB64
    return `<div class="product-ledger-files">
      <div class="bar product-ledger-filebar">
        <input type="file" id="plFile" class="product-ledger-file-input" accept=".csv,text/csv" ${st.busy?'disabled':''}>
        <button type="button" id="plPickFile" ${st.busy?'disabled':''}>${esc(label)}</button>
        <span class="excelhint product-ledger-filename" id="plFileLabel">${esc(st.fileName||'未選択')}${sizeHint?` ${esc(sizeHint)}`:''}${st.preview?`（${esc(st.preview.encoding||'')}）`:''}</span>
      </div>
      <div class="bar product-ledger-filebar">
        <input type="file" id="plAuxFile" class="product-ledger-file-input" accept=".csv,text/csv" ${st.busy?'disabled':''}>
        <button type="button" id="plPickAux" ${st.busy?'disabled':''}>${esc(auxLabel)}</button>
        <span class="excelhint product-ledger-filename">${esc(st.auxFileName||'未選択（台帳のみ可）')}${auxHint?` ${esc(auxHint)}`:''}${auxMeta?`（${esc(auxMeta.encoding||'')} / ${auxMeta.rowCount||0}行）`:''}</span>
        ${st.auxFileB64?`<button type="button" id="plClearAux" class="quiet" ${st.busy?'disabled':''}>初期在庫CSVを外す</button>`:''}
      </div>
      ${auxSelected
        ?`<p class="notice" role="status"><strong>${esc(AUX_JUDGMENT_ONLY_COPY)}</strong>。メイン商品名・仕入先・仕様の選定にだけ使い、在庫数量・単価・入出庫履歴は書きません。</p>`
        :`<p class="excelhint">初期在庫CSVを選ばない場合も、商品・取扱などの台帳登録のみです（在庫数量は書き込みません）。</p>`}
      <div class="bar">
        <button type="button" id="plPreview" class="primary" ${!st.fileB64||st.busy?'disabled':''}>読み込み・照合</button>
        <button type="button" id="plBackMode" class="quiet" ${st.busy?'disabled':''}>段階選択に戻る</button>
      </div>
      ${st.progressLabel?`<p class="excelhint" role="status">${esc(st.progressLabel)}</p>`:''}
      ${st.previewError?`<p class="notice product-ledger-import-error" role="alert">${esc(st.previewError)}</p>`:''}
    </div>`
  }

  // 診断用。本番ノイズを避けるため既定オフ（?ledgerScrollDebug=1 で有効）。
  function ledgerScrollDebug(){
    try{
      return !!(typeof location!=='undefined'&&location.search&&/[?&]ledgerScrollDebug=1(?:&|$)/.test(location.search))
    }catch(_){return false}
  }
  function scrollLog(){
    if(!ledgerScrollDebug())return
    try{console.debug.apply(console,['[ledger-scroll]'].concat([].slice.call(arguments)))}catch(_){}
  }

  function readScrollPos(el){
    return {top:el?el.scrollTop:0,left:el?el.scrollLeft:0}
  }

  function applyScrollPos(el,pos){
    if(!el||!pos)return
    el.scrollTop=pos.top||0
    el.scrollLeft=pos.left||0
  }

  function ledgerBodyEl(dlg){
    return dlg?dlg.querySelector('.legacy-csv-detail-body'):null
  }

  /** 縦スクロール本体（モーダル）内で見えている先頭 SKU と相対位置 */
  function captureVisibleSkuAnchor(dlg){
    const body=ledgerBodyEl(dlg)
    if(!body)return null
    const br=body.getBoundingClientRect()
    const rows=dlg.querySelectorAll('tr.sku-list-row[data-sku-code]')
    for(let i=0;i<rows.length;i++){
      const row=rows[i]
      const rr=row.getBoundingClientRect()
      if(rr.bottom<=br.top+4)continue
      if(rr.top>=br.bottom-4)break
      return {
        code:row.getAttribute('data-sku-code')||'',
        offset:rr.top-br.top,
        section:row.getAttribute('data-sku-section')||''
      }
    }
    return null
  }

  /** paintDialog の innerHTML 全描画で消えるスクロールを退避する */
  function captureLedgerScroll(dlg){
    const wraps={}
    dlg.querySelectorAll('.sku-compare-wrap').forEach((el,i)=>{
      wraps[el.closest('[data-sku-section-block]')?.getAttribute('data-sku-section-block')||`wrap${i}`]=readScrollPos(el)
    })
    const body=ledgerBodyEl(dlg)
    const visibleAnchor=captureVisibleSkuAnchor(dlg)
    const snap={
      body:readScrollPos(body),
      wrap:readScrollPos(dlg.querySelector('.sku-compare-wrap')),
      wraps,
      sections:readScrollPos(dlg.querySelector('.sku-judgment-sections')),
      table:readScrollPos(dlg.querySelector('.legacy-csv-table-wrap')),
      visibleAnchor
    }
    scrollLog('capture',snap)
    return snap
  }

  /**
   * 操作した品番行をモーダル縦スクロール内の同じ相対位置へ。
   * 縦の主スクローラーは .legacy-csv-detail-body（一覧 wrap は横専用）。
   */
  function ensureSkuRowInView(dlg,code,opts){
    opts=opts||{}
    if(!code)return false
    const row=dlg.querySelector(`tr.sku-list-row[data-sku-code="${CSS.escape(String(code))}"]`)
    if(!row){
      scrollLog('anchor-missing',code)
      return false
    }
    const body=ledgerBodyEl(dlg)
    const wrap=row.closest('.sku-compare-wrap')
    if(body){
      const br=body.getBoundingClientRect()
      const rr=row.getBoundingClientRect()
      const preferred=opts.offset!=null?opts.offset:Math.min(140,Math.max(48,body.clientHeight*0.22))
      const delta=rr.top-br.top-preferred
      if(Math.abs(delta)>2)body.scrollTop+=delta
    }
    if(wrap){
      const wr=wrap.getBoundingClientRect()
      const rr=row.getBoundingClientRect()
      if(rr.left<wr.left+4)wrap.scrollLeft-=(wr.left-rr.left)+8
      else if(rr.right>wr.right-4)wrap.scrollLeft+=(rr.right-wr.right)+8
    }
    return true
  }

  /** 操作行が区分移動で消えたとき、同区分の近傍行を同じオフセットへ */
  function restoreNearbySkuInView(dlg,saved,movedCode){
    if(!saved||!saved.visibleAnchor)return
    const body=ledgerBodyEl(dlg)
    if(!body)return
    const sec=saved.visibleAnchor.section||''
    const offset=saved.visibleAnchor.offset
    const sel=sec
      ?`tr.sku-list-row[data-sku-section="${CSS.escape(sec)}"]`
      :'tr.sku-list-row[data-sku-code]'
    const rows=[].slice.call(dlg.querySelectorAll(sel))
    if(!rows.length)return
    const br=body.getBoundingClientRect()
    let best=null
    let bestDist=Infinity
    rows.forEach(r=>{
      const c=r.getAttribute('data-sku-code')
      if(movedCode&&c===movedCode)return
      const dist=Math.abs((r.getBoundingClientRect().top-br.top)-(offset!=null?offset:80))
      if(dist<bestDist){bestDist=dist;best=r}
    })
    const pick=best||rows[0]
    if(pick)ensureSkuRowInView(dlg,pick.getAttribute('data-sku-code'),{offset})
  }

  function restoreLedgerScroll(dlg,saved,opts){
    if(!saved)return
    opts=opts||{}
    const body=ledgerBodyEl(dlg)
    const wrapEls=dlg.querySelectorAll('.sku-compare-wrap')
    if(opts.resetListScroll){
      // ページ／検索など意図的な一覧先頭へ。横は戻し、縦は判断一覧パネル先頭へ。
      wrapEls.forEach(wrap=>{wrap.scrollTop=0;wrap.scrollLeft=0})
      if(body){
        const panel=dlg.querySelector('.sku-judgment-sections')||dlg.querySelector('.sku-groups-panel')
        if(panel){
          const br=body.getBoundingClientRect()
          const pr=panel.getBoundingClientRect()
          body.scrollTop+=(pr.top-br.top)-6
        }else{
          applyScrollPos(body,{top:0,left:(saved.body&&saved.body.left)||0})
        }
      }
    }else{
      applyScrollPos(body,saved.body)
      if(saved.wraps){
        wrapEls.forEach((el,i)=>{
          const key=el.closest('[data-sku-section-block]')?.getAttribute('data-sku-section-block')||`wrap${i}`
          const pos=saved.wraps[key]||saved.wrap||{top:0,left:0}
          // 縦は body に一本化。wrap は横位置のみ復元。
          applyScrollPos(el,{top:0,left:pos.left||0})
        })
      }else{
        const w=dlg.querySelector('.sku-compare-wrap')
        if(w&&saved.wrap)applyScrollPos(w,{top:0,left:saved.wrap.left||0})
      }
    }
    const table=dlg.querySelector('.legacy-csv-table-wrap')
    if(opts.resetTableScroll){
      if(table){table.scrollTop=0;table.scrollLeft=0}
    }else{
      applyScrollPos(table,saved.table)
    }
    if(!opts.resetListScroll){
      const anchorCode=opts.anchorCode||(saved.visibleAnchor&&saved.visibleAnchor.code)||''
      const offset=saved.visibleAnchor?saved.visibleAnchor.offset:undefined
      if(anchorCode){
        if(!ensureSkuRowInView(dlg,anchorCode,{offset})){
          restoreNearbySkuInView(dlg,saved,anchorCode)
        }
      }else if(saved.visibleAnchor){
        restoreNearbySkuInView(dlg,saved,'')
      }
    }
    scrollLog('restore',{opts,body:readScrollPos(body),anchor:opts.anchorCode||(saved.visibleAnchor&&saved.visibleAnchor.code)})
  }

  /**
   * @param {{preserveScroll?:boolean,resetListScroll?:boolean,resetTableScroll?:boolean,anchorCode?:string}} [opts]
   * preserveScroll 既定 true（初回描画・明示 false 以外は V/H を復元）。
   * ページ切替・検索変更時は resetListScroll / resetTableScroll で一覧だけ先頭へ。
   */
  function paintDialog(opts){
    opts=opts||{}
    const st=ensureState()
    const dlg=document.getElementById('dialog')
    if(!dlg||!st.open)return
    const hadBody=!!dlg.querySelector('.legacy-csv-detail-body')
    const saved=(opts.preserveScroll!==false&&hadBody)?captureLedgerScroll(dlg):null
    const review=(st.preview&&st.preview.reviewCount)||0
    const skuPending=(st.preview&&st.preview.skuDecisionPending)||0
    const canWrite=typeof st.stockCanWrite==='function'?st.stockCanWrite():true
    dlg.classList.add('product-ledger-dialog','legacy-csv-detail-dialog')
    if(!st.importMode){
      dlg.innerHTML=`<div class="dialoghead"><h2>旧CSV一括取り込み</h2><button type="button" id="plClose" class="quiet">閉じる</button></div>
        <div class="dialogbody legacy-csv-detail-body">${modeChooserHtml(st)}${resultHtml(st)}</div>`
      if(typeof dlg.showModal==='function'&&!dlg.open)dlg.showModal()
      bindDialog()
      return
    }
    const openingOnly=isOpeningMode(st)
    const title=openingOnly?'旧CSV一括取り込み（初期在庫を登録）':'旧CSV一括取り込み（商品を登録）'
    const lead=openingOnly
      ?'初期在庫CSVだけで登録します。登録先は商品登録時の永続マップから原文キーで解決します。曖昧・削除済み・未登録は理由を示し、別商品へ推測登録しません。プレビューまではDBを変更しません。'
      :`商品台帳CSVから商品と顧客取扱を登録します。初期在庫CSVは任意で、<strong>${esc(AUX_JUDGMENT_ONLY_COPY)}</strong>。通常品番は<strong>1品番=1商品</strong>です。プレビューまではDBを変更しません。`
    const previewBody=st.preview?(openingOnly
      ?`<p class="excelhint">${esc(st.preview.note||'')}</p>
          ${timingsHtml(st)}
          ${st.preview.auxOpeningStock?`<p class="notice">初期在庫CSV: ${esc(st.preview.auxOpeningStock.fileName||'')}　${esc(st.preview.auxOpeningStock.note||'')}</p>`:''}
          ${openingStockSectionsHtml(st)}
          <div class="bar product-ledger-preview-actions">
            <button type="button" id="plRePreview" ${st.busy?'disabled':''}>選択を反映して再照合</button>
            <button type="button" id="plCommit" class="primary" ${st.busy||!canWrite?'disabled':''}>確認して初期在庫を登録</button>
          </div>`
      :`<p class="excelhint">${esc(st.preview.note||'')}</p>
          ${timingsHtml(st)}
          ${st.preview.auxOpeningStock?`<p class="excelhint">初期在庫CSV（判定補助）: ${esc(st.preview.auxOpeningStock.fileName||'')}　${esc(st.preview.auxOpeningStock.note||'')}</p>`:''}
          <p><strong>${countLine(st.preview.countsRaw)}</strong>　行の要確認 ${review}件　品番未決定 ${skuPending}件${(review||skuPending)? '（未決定の品番は確定できません。保留にすれば登録対象から外せます）':''}</p>
          <p class="excelhint">${esc(plannedLine(st))}</p>
          ${outcomeSummaryHtml(st)}
          ${skuGroupsHtml(st)}
          <div class="bar product-ledger-preview-actions">
            <input type="search" id="plQ" placeholder="検索（顧客・品番・商品名・理由）" value="${esc(st.q||'')}">
            <select id="plStatus">
              <option value="">すべて</option>
              ${STATUS_ORDER.map(k=>`<option value="${k}" ${st.statusFilter===k?'selected':''}>${STATUS_LABELS[k]}</option>`).join('')}
            </select>
            <button type="button" id="plRePreview" ${st.busy?'disabled':''}>選択を反映して再照合</button>
            <button type="button" id="plCommit" class="primary" ${st.busy||!canWrite||review>0||skuPending>0?'disabled':''}>確認して商品を登録（保留は除外）</button>
          </div>
          ${makerLookupPanelHtml(st)}
          ${tableHtml(st)}`):''
    dlg.innerHTML=`<div class="dialoghead"><h2>${esc(title)}</h2><button type="button" id="plClose" class="quiet">閉じる</button></div>
      <div class="dialogbody legacy-csv-detail-body">
        <p class="merge-lead">${lead}</p>
        ${filePickHtml(st)}
        ${historyBlock(st)}
        ${previewBody}
        ${resultHtml(st)}
      </div>`
    if(typeof dlg.showModal==='function'&&!dlg.open)dlg.showModal()
    bindDialog()
    restoreLedgerScroll(dlg,saved,opts)
    // Preview errors sit under「読み込み・照合」; ensure they are not left below the fold.
    if(st.previewError){
      const errEl=dlg.querySelector('.product-ledger-import-error')
      if(errEl&&typeof errEl.scrollIntoView==='function'){
        try{errEl.scrollIntoView({block:'nearest',inline:'nearest'})}catch(_){errEl.scrollIntoView(false)}
      }
    }
  }

  async function loadSelectedFile(st,f){
    if(!f)return
    assertUploadSize(f,null)
    st.file=f
    st.fileName=f.name
    st.fileSize=f.size||0
    st.preview=null
    st.previewError=''
    st.result=null
    st.selections={}
    st.skuDecisions={}
    st.rowClassifications={}
    st.makerLookup=null
    st.selectedCodes={}
    st.expandedCodes={}
    st.sectionOpen=Object.assign({},SECTION_DEFAULTS)
    st.sectionPages={individual:1,bulk:1,approved:1,auto:1,hold:1,separate:1}
    st.skuQ='';st.skuReasonFilter='';st.skuPage=1
    st.page=1
    st.history=null
    st.historyError=''
    st.busy=true
    paintDialog({preserveScroll:false})
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
      const msg=apiErrorMessage(e,'ファイルを読めませんでした。')
      st.previewError=msg
      st.toast(msg,8000)
    }
    st.busy=false
    paintDialog({preserveScroll:false})
  }

  async function loadAuxFile(st,f){
    if(!f)return
    assertUploadSize(f,null)
    st.busy=true
    paintDialog({preserveScroll:false})
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
      st.previewError=''
      st.result=null
      // 手動指定は残し、自動結果との差分は再照合で提示
      Object.keys(st.skuDecisions||{}).forEach(code=>{
        const d=st.skuDecisions[code]
        if(d&&!d.primarySupplierManual){
          delete d.primarySupplier
          delete d.auxPrimaryConflict
        }
      })
      st.toast(isOpeningMode(st)
        ?'初期在庫CSVを読みました。「読み込み・照合」で登録予定を確認してください。'
        :`${AUX_JUDGMENT_ONLY_COPY}。台帳CSVとあわせて「読み込み・照合」してください。`)
    }catch(e){
      st.auxFile=null;st.auxFileB64='';st.auxFileName='';st.auxFileSize=0
      const msg=apiErrorMessage(e,'初期在庫CSVを読めませんでした。')
      st.previewError=msg
      st.toast(msg,8000)
    }
    st.busy=false
    paintDialog({preserveScroll:false})
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
    const modeProducts=$('#plModeProducts')
    if(modeProducts)modeProducts.onclick=()=>{
      if(st.busy)return
      resetStageFiles(st)
      st.importMode=MODE_PRODUCTS
      paintDialog({preserveScroll:false})
    }
    const modeOpening=$('#plModeOpening')
    if(modeOpening)modeOpening.onclick=()=>{
      if(st.busy)return
      resetStageFiles(st)
      st.importMode=MODE_OPENING
      paintDialog({preserveScroll:false})
    }
    const backMode=$('#plBackMode')
    if(backMode)backMode.onclick=()=>{
      if(st.busy)return
      resetStageFiles(st)
      st.importMode=MODE_CHOOSE
      paintDialog({preserveScroll:false})
    }
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
      st.openingSelections={}
      st.preview=null;st.previewError='';st.result=null
      Object.keys(st.skuDecisions||{}).forEach(code=>{
        const d=st.skuDecisions[code]
        if(d&&!d.primarySupplierManual){
          delete d.primarySupplier
          delete d.auxPrimaryConflict
        }
      })
      paintDialog()
    }
    const basis=$('#plBasis')
    if(basis)basis.onchange=()=>{st.basisDate=basis.value;st.preview=null}
    const site=$('#plDefaultSite')
    if(site)site.onchange=()=>{st.defaultSiteName=site.value;st.preview=null}
    document.querySelectorAll('[data-pl-open-qty]').forEach(el=>{
      el.onchange=()=>{
        const rowNo=el.getAttribute('data-pl-open-qty')
        st.openingSelections[String(rowNo)]=Object.assign({},st.openingSelections[String(rowNo)]||{},{updateQty:!!el.checked})
      }
    })
    const runPreview=async()=>{
      // 既にプレビュー表示中の再照合はスクロール維持。初回照合のみ先頭へ。
      const keepScroll=!!st.preview
      const reqId=(st.previewReqSeq=(st.previewReqSeq||0)+1)
      const paintPreview=(extra)=>{
        const o=Object.assign({},extra||{})
        if(!keepScroll)o.preserveScroll=false
        paintDialog(o)
      }
      if(isOpeningMode(st)){
        if(!st.auxFileB64){st.toast('初期在庫CSVを選んでください。');return}
        try{assertUploadSize(st.auxFile,st.auxFileB64)}catch(e){
          const msg=apiErrorMessage(e,OVERSIZE_MSG)
          st.previewError=msg
          st.toast(msg,8000)
          paintPreview()
          return
        }
      }else{
        if(!st.fileB64){st.toast('CSVファイルを選んでください。');return}
        try{assertUploadSize(st.file,st.fileB64)}catch(e){
          const msg=apiErrorMessage(e,OVERSIZE_MSG)
          st.previewError=msg
          st.toast(msg,8000)
          paintPreview()
          return
        }
        if(st.auxFileB64){
          try{assertUploadSize(st.auxFile,st.auxFileB64)}catch(e){
            const msg=apiErrorMessage(e,OVERSIZE_MSG)
            st.previewError=msg
            st.toast(msg,8000)
            paintPreview()
            return
          }
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
        // 括弧分類の編集は再照合・詳細開閉でも保持
        document.querySelectorAll('[data-pl-paren-name]').forEach(el=>{
          setRowClassification(st,el.getAttribute('data-pl-paren-name'),{plannedName:el.value})
        })
        document.querySelectorAll('[data-pl-paren-color]').forEach(el=>{
          setRowClassification(st,el.getAttribute('data-pl-paren-color'),{color:el.value})
        })
        document.querySelectorAll('[data-pl-paren-proc]').forEach(el=>{
          setRowClassification(st,el.getAttribute('data-pl-paren-proc'),{processing:el.value})
        })
        document.querySelectorAll('[data-pl-paren-mat]').forEach(el=>{
          setRowClassification(st,el.getAttribute('data-pl-paren-mat'),{material:el.value})
        })
        document.querySelectorAll('[data-pl-paren-supp]').forEach(el=>{
          setRowClassification(st,el.getAttribute('data-pl-paren-supp'),{supplement:el.value})
        })
        document.querySelectorAll('[data-pl-paren-unc]').forEach(el=>{
          const v=el.value.trim()
          setRowClassification(st,el.getAttribute('data-pl-paren-unc'),{unclassified:v?v.split(/[、,]/).map(s=>s.trim()).filter(Boolean):[]})
        })
        document.querySelectorAll('[data-pl-paren-ok]').forEach(el=>{
          setRowClassification(st,el.getAttribute('data-pl-paren-ok'),{confirmed:!!el.checked,acceptProposal:!!el.checked})
        })
        document.querySelectorAll('[data-pl-sep-code]').forEach(el=>{
          const code=el.getAttribute('data-pl-sep-code')
          const key=el.getAttribute('data-pl-sep-key')
          const d=skuDecision(st,code)
          const plans={...(d.plans||{})}
          plans[key]={...(plans[key]||{}),newCode:el.value}
          setSkuDecision(st,code,{plans})
        })
        // 色分割フォーム → skuDecisions.colorPlans / rowDestinations
        ;(()=>{
          const byCode={}
          document.querySelectorAll('[data-pl-color-internal]').forEach(el=>{
            const code=el.getAttribute('data-pl-color-internal')
            const key=el.getAttribute('data-pl-color-key')
            if(!byCode[code])byCode[code]={colorPlans:{},rowDestinations:{...(skuDecision(st,code).rowDestinations||{})}}
            byCode[code].colorPlans[key]={...(byCode[code].colorPlans[key]||{}),internalCode:el.value,colorKey:key}
          })
          document.querySelectorAll('[data-pl-color-name]').forEach(el=>{
            const code=el.getAttribute('data-pl-color-name')
            const key=el.getAttribute('data-pl-color-key')
            if(!byCode[code])byCode[code]={colorPlans:{},rowDestinations:{...(skuDecision(st,code).rowDestinations||{})}}
            byCode[code].colorPlans[key]={...(byCode[code].colorPlans[key]||{}),productName:el.value,colorKey:key}
          })
          document.querySelectorAll('[data-pl-color-color]').forEach(el=>{
            const code=el.getAttribute('data-pl-color-color')
            const key=el.getAttribute('data-pl-color-key')
            if(!byCode[code])byCode[code]={colorPlans:{},rowDestinations:{...(skuDecision(st,code).rowDestinations||{})}}
            byCode[code].colorPlans[key]={...(byCode[code].colorPlans[key]||{}),color:el.value,colorKey:key}
          })
          document.querySelectorAll('[data-pl-color-keep]:checked').forEach(el=>{
            const code=el.getAttribute('data-pl-color-keep')
            const key=el.getAttribute('data-pl-color-key')
            if(!byCode[code])byCode[code]={colorPlans:{},rowDestinations:{...(skuDecision(st,code).rowDestinations||{})}}
            const vid=el.value
            byCode[code].colorPlans[key]={
              ...(byCode[code].colorPlans[key]||{}),
              colorKey:key,
              keepProductId:vid?Number(vid):null,
              collisionAction:vid?'reuse':'edit'
            }
          })
          document.querySelectorAll('[data-pl-color-dest]').forEach(el=>{
            const code=el.getAttribute('data-pl-color-dest')
            const rowNo=el.getAttribute('data-pl-row')
            if(!byCode[code])byCode[code]={colorPlans:{},rowDestinations:{...(skuDecision(st,code).rowDestinations||{})}}
            if(el.value)byCode[code].rowDestinations[String(rowNo)]=el.value
            else delete byCode[code].rowDestinations[String(rowNo)]
          })
          Object.keys(byCode).forEach(code=>{
            const prev=skuDecision(st,code)
            const mergedPlans={...(prev.colorPlans||{}),...byCode[code].colorPlans}
            Object.keys(byCode[code].colorPlans).forEach(k=>{
              mergedPlans[k]={...(prev.colorPlans&&prev.colorPlans[k]||{}),...byCode[code].colorPlans[k]}
            })
            setSkuDecision(st,code,{
              colorPlans:mergedPlans,
              rowDestinations:{...(prev.rowDestinations||{}),...byCode[code].rowDestinations}
            })
          })
        })()
        ;((st.preview&&st.preview.skuGroups)||[]).forEach(g=>{
          if(skuDecision(st,g.code).decision==='merge'){
            syncMergeSources(st,g.code)
            applyAuxDefaultsForMerge(st,g.code,g)
          }
        })
      }
      st.previewError=''
      st.progressLabel=isOpeningMode(st)?'CSVを読み込み中…':'CSVを読み込み中…'
      st.busy=true;paintPreview()
      try{
        st.progressLabel=isOpeningMode(st)?'初期在庫の登録予定を作成中…':'商品を照合中…'
        paintPreview()
        const nextPreview=await st.api('/api/product-ledger-csv/preview',previewPayload(st))
        // 遅い応答が新しい照合結果を上書きしない
        if(reqId!==st.previewReqSeq){
          scrollLog('stale-preview-ignored',reqId,st.previewReqSeq)
          return
        }
        st.preview=nextPreview
        st.previewError=''
        st.result=null
        st.progressLabel=''
        if(!isOpeningMode(st)){
          // 自動提案をクライアント決定へ反映（手動は上書きしない）
          ;((st.preview&&st.preview.skuGroups)||[]).forEach(g=>{
            if(skuDecision(st,g.code).decision==='merge')applyAuxDefaultsForMerge(st,g.code,g)
          })
          // サイズ差の自動まとめ行は要確認に出さない。真の要確認だけ絞り込む。
          if(st.preview.reviewCount>0)st.statusFilter='needs_review'
          else if(st.preview.skuDecisionPending>0)st.statusFilter=''
          else st.statusFilter=''
          st.page=1
        }
        if(st.preview.previousImports&&!st.history){
          st.history={previousImports:st.preview.previousImports}
          st.historyError=''
        }
      }catch(e){
        if(reqId!==st.previewReqSeq){
          scrollLog('stale-preview-error-ignored',reqId,st.previewReqSeq)
          return
        }
        // Modal dialog is in the top layer; bottom toast alone is often clipped.
        // Keep the full message inside the import modal near「読み込み・照合」.
        const msg=apiErrorMessage(e,'照合に失敗しました。')
        st.previewError=msg
        st.progressLabel=''
        st.toast(msg,8000)
      }
      if(reqId!==st.previewReqSeq)return
      st.busy=false
      // 再照合は位置維持。初回のみ先頭（preserveScroll:false）。
      paintPreview(keepScroll?{}:{preserveScroll:false})
    }
    const prevBtn=$('#plPreview');if(prevBtn)prevBtn.onclick=runPreview
    const reBtn=$('#plRePreview');if(reBtn)reBtn.onclick=runPreview
    const q=$('#plQ');if(q)q.oninput=()=>{st.q=q.value;st.page=1;paintDialog({resetTableScroll:true})}
    const stSel=$('#plStatus');if(stSel)stSel.onchange=()=>{st.statusFilter=stSel.value;st.page=1;paintDialog({resetTableScroll:true})}
    const pPrev=$('#plPrev');if(pPrev)pPrev.onclick=()=>{st.page=Math.max(1,st.page-1);paintDialog({resetTableScroll:true})}
    const pNext=$('#plNext');if(pNext)pNext.onclick=()=>{st.page+=1;paintDialog({resetTableScroll:true})}
    const skuQ=$('#plSkuQ');if(skuQ)skuQ.oninput=()=>{
      st.skuQ=skuQ.value
      Object.keys(st.sectionPages||{}).forEach(k=>{st.sectionPages[k]=1})
      st.skuPage=1
      paintDialog({resetListScroll:true})
    }
    const skuReason=$('#plSkuReason');if(skuReason)skuReason.onchange=()=>{
      st.skuReasonFilter=skuReason.value
      Object.keys(st.sectionPages||{}).forEach(k=>{st.sectionPages[k]=1})
      st.skuPage=1
      paintDialog({resetListScroll:true})
    }
    document.querySelectorAll('[data-pl-section-toggle]').forEach(el=>{
      el.onclick=()=>{
        const key=el.getAttribute('data-pl-section-toggle')
        if(!st.sectionOpen)st.sectionOpen=Object.assign({},SECTION_DEFAULTS)
        // 開閉は選択・編集を消さない（skuDecisions / selectedCodes はそのまま）
        st.sectionOpen[key]=!st.sectionOpen[key]
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-section-page]').forEach(el=>{
      el.onclick=()=>{
        const key=el.getAttribute('data-pl-section-page')
        const dir=Number(el.getAttribute('data-dir')||0)
        if(!st.sectionPages)st.sectionPages={}
        st.sectionPages[key]=Math.max(1,(Number(st.sectionPages[key])||1)+dir)
        paintDialog({resetListScroll:true})
      }
    })
    const selPage=$('#plSkuSelectPage')
    if(selPage)selPage.onclick=()=>{
      const part=partitionJudgmentGroups(st)
      const pick=[]
      if(st.sectionOpen&&st.sectionOpen.individual){
        pageSlice(st,'individual',part.individual.filter(g=>matchesSkuFilters(st,g))).pageItems.forEach(g=>pick.push(g))
      }
      if(st.sectionOpen&&st.sectionOpen.bulk){
        pageSlice(st,'bulk',part.bulkUnapproved.filter(g=>matchesSkuFilters(st,g))).pageItems.forEach(g=>pick.push(g))
      }
      pick.forEach(g=>{st.selectedCodes[g.code]=true})
      st.toast(pick.length
        ?`開いている区分の表示ページ ${pick.length} 品番を選択しました`
        :'開いている区分に選択できる品番がありません')
      paintDialog()
    }
    const clearSel=$('#plSkuClearSel')
    if(clearSel)clearSel.onclick=()=>{st.selectedCodes={};paintDialog()}
    const bulkApprove=$('#plSkuBulkApproveSection')
    if(bulkApprove)bulkApprove.onclick=()=>{
      // 一括承認は「一括承認候補」区分の未承認のみ。個別確認が必要は絶対に含めない。
      // 折りたたみ状態でも承認可能。開閉だけでは decision を変えない。
      const part=partitionJudgmentGroups(st)
      const bulk=part.bulkUnapproved.slice()
      if(!bulk.length){st.toast('未承認の一括承認候補がありません。');return}
      if(part.individual.length){
        // 個別確認は対象外であることを明示（混入防止）
      }
      const pages=Math.max(1,Math.ceil(bulk.length/SKU_PAGE_SIZE))
      const lines=bulk.slice(0,15).map(g=>{
        const name=plannedNameForGroup(st,g)||'（仮代表名）'
        const sup=plannedSupplierDisplay(st,g)
        return `・${g.code}: ${name} / 通常仕入先 ${sup}`
      })
      const more=bulk.length>15?`\n…他 ${bulk.length-15} 品番`:''
      const msg=`【承認対象スコープ】一括承認候補の未承認 ${bulk.length} 件すべて（${pages}ページ分）\n個別確認が必要 ${part.individual.length} 件は含めません。\n品番ごとに予定の代表名・通常仕入先を使います（他品番の仕入先は割り当てません）。\n承認後は「承認済み（未登録）」として確認できます（折りたたみだけでは承認されません。最終登録までは未登録です）。\n\n${lines.join('\n')}${more}\n\nよろしいですか？`
      if(!confirm(msg))return
      bulk.forEach(g=>applyMergeDefaults(st,g.code,g))
      st.toast(`一括承認候補 ${bulk.length} 件を承認済み（未登録）にしました（個別確認 ${part.individual.length} 件は対象外）`)
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
        paintDialog({anchorCode:code})
      }
    })
    document.querySelectorAll('[data-pl-sku-expand]').forEach(el=>{
      el.onclick=()=>{
        const code=el.getAttribute('data-pl-sku-expand')
        st.expandedCodes[code]=!st.expandedCodes[code]
        paintDialog({anchorCode:code})
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
        }else if(dec==='color_split'){
          setSkuDecision(st,code,{decision:'color_split'})
          st.expandedCodes[code]=true
          // 予定は再照合でサーバ生成（クリックだけでは保存しない）
          st.toast('色別の登録予定を作ります。内容を確認・編集してから最終登録してください。')
          paintDialog({anchorCode:code})
          runPreview()
          return
        }else{
          setSkuDecision(st,code,{decision:dec})
          if(dec==='separate'||dec==='hold')st.expandedCodes[code]=true
        }
        paintDialog({anchorCode:code})
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
    document.querySelectorAll('[data-pl-paren-name]').forEach(el=>{
      el.onchange=()=>setRowClassification(st,el.getAttribute('data-pl-paren-name'),{plannedName:el.value})
    })
    document.querySelectorAll('[data-pl-paren-color]').forEach(el=>{
      el.onchange=()=>setRowClassification(st,el.getAttribute('data-pl-paren-color'),{color:el.value})
    })
    document.querySelectorAll('[data-pl-paren-proc]').forEach(el=>{
      el.onchange=()=>setRowClassification(st,el.getAttribute('data-pl-paren-proc'),{processing:el.value})
    })
    document.querySelectorAll('[data-pl-paren-mat]').forEach(el=>{
      el.onchange=()=>setRowClassification(st,el.getAttribute('data-pl-paren-mat'),{material:el.value})
    })
    document.querySelectorAll('[data-pl-paren-supp]').forEach(el=>{
      el.onchange=()=>setRowClassification(st,el.getAttribute('data-pl-paren-supp'),{supplement:el.value})
    })
    document.querySelectorAll('[data-pl-maker-correct]').forEach(el=>{
      el.onclick=async()=>{
        const rowNo=Number(el.getAttribute('data-pl-maker-correct'))
        const row=(st.preview&&st.preview.rows||[]).find(r=>Number(r.rowNo)===rowNo)
        if(!row){st.toast('対象行が見つかりません。');return}
        const mk=row.makerCorrection||{}
        if(!mk.eligible||!mk.makerSku){st.toast('メーカー品番が対象外です。');return}
        const pc=row.parenClassification||{}
        const edit=st.rowClassifications[String(rowNo)]||{}
        const sourceName=(row.csv&&row.csv.productName)||''
        const currentName=edit.plannedName!=null?edit.plannedName:(pc.plannedName||(row.product&&row.product.officialName)||sourceName||'')
        const currentColor=edit.color!=null?edit.color:(pc.color||(row.product&&row.product.officialColor)||'')
        const currentSupplement=edit.supplement!=null?edit.supplement:(pc.supplement||'')
        st.busy=true
        st.makerLookup={
          rowNo,makerSku:mk.makerSku,manufacturer:mk.manufacturer||'',
          supplier:(row.csv&&row.csv.supplier)||'',sourceName,
          statusLabel:'検索中…',result:null,error:''
        }
        paintDialog()
        try{
          const result=await st.api('/api/product-ledger-csv/maker-lookup',{
            manufacturer:mk.manufacturer||'',
            makerSku:mk.makerSku,
            currentName,currentColor,currentSupplement,
            sourceName,
            supplier:(row.csv&&row.csv.supplier)||''
          })
          st.makerLookup=Object.assign({},st.makerLookup,{
            result,
            manufacturer:result.manufacturer||mk.manufacturer||'',
            statusLabel:result.statusLabel||result.status||'',error:''
          })
        }catch(e){
          st.makerLookup=Object.assign({},st.makerLookup,{
            result:{status:'fetch_failed',statusLabel:'取得できない',candidates:[],
              referenceUrl:'',note:'検索リクエストに失敗しました。'},
            statusLabel:'取得できない',
            error:apiErrorMessage(e,'メーカー情報を取得できませんでした。')
          })
        }finally{
          st.busy=false
          paintDialog()
        }
      }
    })
    const makerClose=$('#plMakerLookupClose')
    if(makerClose)makerClose.onclick=()=>{st.makerLookup=null;paintDialog()}
    document.querySelectorAll('[data-pl-maker-adopt]').forEach(el=>{
      el.onclick=async()=>{
        const panel=st.makerLookup
        if(!panel||!panel.result)return
        const idx=Number(el.getAttribute('data-pl-maker-adopt'))
        const cand=(panel.result.candidates||[])[idx]
        if(!cand){st.toast('候補が見つかりません。');return}
        st.busy=true
        paintDialog()
        try{
          await st.api('/api/product-ledger-csv/maker-lookup/adopt',{
            manufacturer:cand.manufacturer||panel.manufacturer||panel.result.manufacturer||'',
            makerSku:panel.makerSku,
            name:cand.name||'',
            color:cand.color||'',
            supplement:cand.supplement||'',
            sourceUrl:cand.sourceUrl||''
          })
          // プレビューのみ更新（台帳登録は「確認して商品を登録」）。原文は別名に残す。
          // メーカー情報を名・色の根拠にし、旧名の「白」などとの色競合を確認待ちに残さない。
          setRowClassification(st,panel.rowNo,{
            plannedName:cand.name||'',
            color:cand.color||'',
            supplement:cand.supplement||'',
            unclassified:[],
            userEdited:true,
            confirmed:true,
            acceptProposal:true,
            makerAdopted:true,
            makerSourceUrl:cand.sourceUrl||'',
            makerManufacturer:cand.manufacturer||panel.manufacturer||''
          })
          st.makerLookup=null
          st.toast('メーカー情報を予定へ採用しました（まだ台帳には登録していません）')
          // 再照合して差分を画面へ反映
          const nextPreview=await st.api('/api/product-ledger-csv/preview',previewPayload(st))
          st.preview=nextPreview
          st.previewError=''
        }catch(e){
          st.toast(apiErrorMessage(e,'採用に失敗しました。'),6000)
        }finally{
          st.busy=false
          paintDialog()
        }
      }
    })
    document.querySelectorAll('[data-pl-paren-unc]').forEach(el=>{
      el.onchange=()=>{
        const v=el.value.trim()
        setRowClassification(st,el.getAttribute('data-pl-paren-unc'),{
          unclassified:v?v.split(/[、,]/).map(s=>s.trim()).filter(Boolean):[]
        })
      }
    })
    document.querySelectorAll('[data-pl-paren-ok]').forEach(el=>{
      el.onchange=()=>{
        setRowClassification(st,el.getAttribute('data-pl-paren-ok'),{
          confirmed:!!el.checked,acceptProposal:!!el.checked
        })
      }
    })
    const commit=$('#plCommit')
    if(commit)commit.onclick=async()=>{
      const canWriteNow=typeof st.stockCanWrite==='function'?st.stockCanWrite():true
      if(!st.preview)return
      if(!canWriteNow){st.toast('閲覧専用のため登録できません。');return}
      if(isOpeningMode(st)){
        const osSum=st.preview.openingStockSummary||{}
        const summary=`【初期在庫を登録】新規 ${osSum.create||0} / 再利用 ${osSum.reuse||0} / 数量差異(選択時) ${osSum.qtyDiff||0} / 要確認 ${osSum.needsReview||0} / 補完候補(登録しない) ${osSum.complement||0}\n在庫基準日 ${st.basisDate||''} / 拠点空欄→ ${st.defaultSiteName||'未確認'}\n登録先は商品登録時の永続マップから解決します。\n\nよろしいですか？`
        if(!confirm(summary))return
        if(!st.basisDate){st.toast('在庫基準日を指定してください。');return}
        try{assertUploadSize(st.auxFile,st.auxFileB64)}catch(e){st.toast(apiErrorMessage(e,OVERSIZE_MSG));return}
      }else{
        if((st.preview.reviewCount||0)>0||(st.preview.skuDecisionPending||0)>0){
          st.toast('要確認または品番比較の未決定が残っています。先に解決してください。');return
        }
        const p=st.preview.plannedCounts||{}
        const jc=judgmentStatusCounts(st)
        const statusBlock=`\n\n【判断区分の件数】未解決 ${jc.unresolved} / 未承認 ${jc.unapproved} / 承認済み（未登録） ${jc.approved} / 自動決定済み（未登録） ${jc.autoDecided} / 保留 ${jc.hold}${jc.excluded?` / 対象外 ${jc.excluded}`:''}`
        const deferList=(p.deferredReasons||[]).slice(0,12).map(d=>
          `・行${d.rowNo} 品番${d.code||'—'} ${d.reason||'保留'}`
        )
        const deferMore=(p.deferredReasons||[]).length>12?`\n…他 ${(p.deferredReasons||[]).length-12} 件`:''
        const deferBlock=(p.rowsDeferred||0)
          ?`\n\n【今回は登録しない保留】 ${p.rowsDeferred} 行\n${deferList.join('\n')}${deferMore}\n（黙って捨てません。同じCSVの再読込で後から処理できます）`
          :'\n\n保留行はありません。'
        const stockNote=`\n\n${AUX_JUDGMENT_ONLY_COPY}。在庫数量・金額・履歴は変更しません。`
        const summary=`【商品を登録】新規商品 ${p.productsToCreate||0} / 再利用 ${p.productsToReuse||0} / 統合 ${p.productsToMerge||0} / サイズ追加 ${p.sizesToAdd||0} / 取扱新規 ${p.handlesToCreate||0}\n（CSV行 ${p.csvRows||0} ※行数と新規商品数は別です）${statusBlock}${deferBlock}${stockNote}\n\nよろしいですか？`
        if(!confirm(summary))return
        try{assertUploadSize(st.file,st.fileB64)}catch(e){st.toast(apiErrorMessage(e,OVERSIZE_MSG));return}
      }
      st.busy=true;st.progressLabel='登録処理中…';paintDialog()
      try{
        if(!st.commitToken)st.commitToken=`pl-${Date.now()}-${Math.random().toString(36).slice(2,10)}`
        const commitBody=previewPayload(st)
        commitBody.previewFingerprint=st.preview.previewFingerprint
        commitBody.previewAt=st.preview.previewAt
        commitBody.token=st.commitToken
        st.result=await st.api('/api/product-ledger-csv/commit',commitBody)
        st.commitToken=`pl-${Date.now()}-${Math.random().toString(36).slice(2,10)}`
        if(typeof st.boot==='function')await st.boot()
        st.toast(isOpeningMode(st)?'初期在庫の登録が完了しました':'商品登録が完了しました（在庫は書いていません）')
      }catch(e){
        if(e&&e.payload&&e.payload.code==='stale_preview'){
          st.toast('データが変わったため、もう一度照合してください。')
          st.preview=null
        }else if(e&&e.payload&&e.payload.code==='backup_failed'){
          st.toast(apiErrorMessage(e,'登録前のバックアップに失敗したため、登録を中止しました'))
        }else st.toast(apiErrorMessage(e,'登録に失敗しました。登録は完了していません。'))
      }
      st.progressLabel=''
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
    st.previewError=''
    st.progressLabel=''
    // 毎回段階選択から（閉じたあとも標準フローを保つ）
    resetStageFiles(st)
    st.importMode=MODE_CHOOSE
    paintDialog()
  }

  window.ProductLedgerCsv={
    open,
    // 区分分類の単体検証用（本番UIは open のみ使用）
    __test:{
      partitionJudgmentGroups,
      judgmentStatusCounts,
      effectiveDecision,
      decisionLabel,
      SECTION_DEFAULTS,
      pageSlice,
      SKU_PAGE_SIZE,
      MODE_PRODUCTS,
      MODE_OPENING,
      AUX_JUDGMENT_ONLY_COPY,
      previewPayload
    }
  }
})()
