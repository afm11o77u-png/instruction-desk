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
  // unified=確認リスト（旧・個別確認＋行要確認の統合）。sourceRows=元行一覧（詳細確認用・既定折りたたみ）
  // rowExcluded=登録しない項目（今回の取り込みだけの行除外）
  // confirmed=確認済み一覧（ユーザーが方針を選び必須入力が揃ったもの。自動決定は含めない）
  const SECTION_DEFAULTS={unified:true,individual:true,bulk:false,auto:false,hold:false,rowExcluded:false,confirmed:true,sourceRows:false}
  const CONFIRMED_STATUS={pending:'反映待ち',confirmed:'確認済み'}
  const CONFIRMED_LIST_COL_COUNT=7
  // 手動「今回登録しない／この行を登録しない」。自動除外理由と区別する（恒久ルールにはしない）
  const USER_ROW_EXCLUDE_REASON='利用者が今回登録しないと指定'
  const USER_ROW_EXCLUDE_REASON_LEGACY='この行を登録しない（今回の取り込み）'
  const K2_AUTO_EXCLUDE_REASON='K2スタッフ工房の加工行ですが、加工内容を特定できないため登録対象外'
  function userExcludeReasonTokens(){
    return [USER_ROW_EXCLUDE_REASON,USER_ROW_EXCLUDE_REASON_LEGACY,K2_AUTO_EXCLUDE_REASON]
  }
  // 一覧テーブル列数。展開詳細行の colspan は必ずこれと一致させる（不一致だと table-layout:fixed で列が縮む）
  const SKU_COMPARE_COL_COUNT=8
  const UNIFIED_REVIEW_COL_COUNT=5
  const MODE_CHOOSE=''
  const MODE_PRODUCTS='products'
  const MODE_OPENING='opening_stock'
  const AUX_JUDGMENT_ONLY_COPY='初期在庫CSVは商品選定の判断にだけ使います。在庫は登録しません'
  let ui=null

  /** 加工サービス統合キー。HTML data 属性でも壊れない区切り（NUL は属性値で切れるため使わない） */
  function processingUnifiedKey(name,vendor){
    return `proc:${encodeURIComponent(String(name||''))}|${encodeURIComponent(String(vendor||''))}`
  }

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
        openingSelections:{},openingUsageFilter:'',
        history:null,historyError:'',previewError:'',preview:null,result:null,
        q:'',statusFilter:'needs_review',page:1,
        skuQ:'',skuReasonFilter:'',skuPage:1,
        selectedCodes:{},expandedCodes:{},
        sectionOpen:Object.assign({},SECTION_DEFAULTS),
        sectionPages:{unified:1,individual:1,bulk:1,approved:1,auto:1,hold:1,separate:1,confirmed:1,rowExcluded:1},
        expandedUnified:{},expandedConfirmed:{},
        confirmedQ:'',
        selections:{},skuDecisions:{},appliedSkuDecisions:{},rowClassifications:{},rowExclusions:{},commitToken:'',busy:false,open:false,
        makerLookup:null,bulkMakerLookup:null,_bulkMakerPoll:null,
        api:null,toast:null,boot:null,stockCanWrite:null,actorName:''
      }
    }
    if(ui.importMode==null)ui.importMode=MODE_CHOOSE
    if(ui.progressLabel==null)ui.progressLabel=''
    if(!ui.selectedCodes)ui.selectedCodes={}
    if(!ui.expandedCodes)ui.expandedCodes={}
    if(!ui.rowClassifications)ui.rowClassifications={}
    if(!ui.rowExclusions)ui.rowExclusions={}
    if(!ui.appliedSkuDecisions)ui.appliedSkuDecisions={}
    if(!ui.openingSelections)ui.openingSelections={}
    if(ui.openingUsageFilter==null)ui.openingUsageFilter=''
    if(ui.makerLookup===undefined)ui.makerLookup=null
    if(ui.bulkMakerLookup===undefined)ui.bulkMakerLookup=null
    if(!ui.sectionOpen)ui.sectionOpen=Object.assign({},SECTION_DEFAULTS)
    // 再描画でユーザーが開いた区分を閉じない（欠けたキーだけ既定値で補う）
    Object.keys(SECTION_DEFAULTS).forEach(k=>{
      if(ui.sectionOpen[k]==null)ui.sectionOpen[k]=SECTION_DEFAULTS[k]
    })
    // 旧「individual」開閉を確認リストへ引き継ぐ
    if(ui.sectionOpen.unified==null&&ui.sectionOpen.individual!=null){
      ui.sectionOpen.unified=!!ui.sectionOpen.individual
    }
    if(!ui.sectionPages)ui.sectionPages={unified:1,individual:1,bulk:1,approved:1,auto:1,hold:1,separate:1,confirmed:1,rowExcluded:1}
    if(ui.sectionPages.confirmed==null)ui.sectionPages.confirmed=1
    if(!ui.expandedUnified)ui.expandedUnified={}
    if(!ui.expandedConfirmed)ui.expandedConfirmed={}
    if(ui.confirmedQ==null)ui.confirmedQ=''
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
    st.selections={};st.skuDecisions={};st.appliedSkuDecisions={};st.rowClassifications={};st.rowExclusions={}
    st.rowExcludeSnapshots={}
    st.selectedCodes={};st.expandedCodes={};st.openingSelections={}
    st.makerLookup=null;st.bulkMakerLookup=null
    stopBulkMakerPoll(st)
    st.sectionOpen=Object.assign({},SECTION_DEFAULTS)
    st.sectionPages={unified:1,individual:1,bulk:1,approved:1,auto:1,hold:1,rowExcluded:1,separate:1,confirmed:1}
    st.expandedUnified={}
    st.expandedConfirmed={}
    st.skuQ='';st.skuReasonFilter='';st.confirmedQ='';st.skuPage=1;st.page=1
    st.progressLabel=''
  }

  /** 元CSV行の安定キー（表示順変更で別行へ適用されない）。サーバ row_map_key と同材料。 */
  function stableRowKeyFromCsv(csv){
    const c=csv||{}
    return [c.customer||'',c.code||'',c.productName||'',c.size||'',c.supplier||''].join('\u0001')
  }
  function stableRowKeyFromRow(r){
    return stableRowKeyFromCsv((r&&r.csv)||{})
  }
  function isRowExcluded(st,r){
    if(!r)return false
    if(r.userRowExcluded||r.autoRowExcluded)return true
    const key=stableRowKeyFromRow(r)
    const ent=st.rowExclusions&&st.rowExclusions[key]
    return !!(ent&&ent.excluded!==false)
  }
  function rowExclusionReason(st,r){
    if(!r)return ''
    if(r.deferReason)return r.deferReason
    if(r.autoRowExcluded)return K2_AUTO_EXCLUDE_REASON
    if(r.userRowExcluded)return USER_ROW_EXCLUDE_REASON
    const key=stableRowKeyFromRow(r)
    const ent=st.rowExclusions&&st.rowExclusions[key]
    return (ent&&ent.reason)||USER_ROW_EXCLUDE_REASON
  }
  function snapshotRowBeforeExclude(st,r){
    if(!r)return
    if(!st.rowExcludeSnapshots)st.rowExcludeSnapshots={}
    const key=stableRowKeyFromRow(r)
    if(st.rowExcludeSnapshots[key])return
    st.rowExcludeSnapshots[key]={
      status:r.status,
      statusLabel:r.statusLabel,
      canCommit:r.canCommit,
      reasons:(r.reasons||[]).slice(),
      userRowExcluded:!!r.userRowExcluded,
      autoRowExcluded:!!r.autoRowExcluded,
      exclusionKind:r.exclusionKind||'',
      deferReason:r.deferReason||'',
      processingServiceNeedsConfirm:!!r.processingServiceNeedsConfirm,
      skuDecisionNeeded:!!r.skuDecisionNeeded,
      skuGroupCode:((r.csv||{}).code)||''
    }
  }
  function setRowExcluded(st,r,excluded,opts){
    if(!r)return
    opts=opts||{}
    const csv=r.csv||{}
    const key=stableRowKeyFromRow(r)
    if(!st.rowExclusions)st.rowExclusions={}
    if(excluded){
      const reason=opts.reason||USER_ROW_EXCLUDE_REASON
      const kind=opts.kind||'manual'
      st.rowExclusions[key]={
        excluded:true,
        rowNo:r.rowNo,
        customer:csv.customer||'',
        code:csv.code||'',
        productName:csv.productName||'',
        size:csv.size||'',
        supplier:csv.supplier||'',
        reason,
        exclusionKind:kind
      }
    }else{
      delete st.rowExclusions[key]
    }
  }
  /** 品番グループの除外後同期（全行除外なら未決定から外す。他対象は維持）。 */
  function syncSkuGroupAfterLocalExclude(st,code){
    if(!code||!st.preview)return
    const groups=st.preview.skuGroups||[]
    const group=groups.find(g=>String(g.code)===String(code))
    if(!group)return
    const grow=((st.preview.rows)||[]).filter(r=>String((r.csv||{}).code||'')===String(code))
    if(!grow.length)return
    const active=grow.filter(r=>!isRowExcluded(st,r))
    group.userExcludedRowNos=grow.filter(r=>isRowExcluded(st,r)).map(r=>r.rowNo)
    group.activeRowNos=active.map(r=>r.rowNo)
    if(!active.length&&group.needsDecision){
      const sample=grow.find(r=>r.userRowExcluded)||grow.find(r=>r.autoRowExcluded)||grow[0]
      group.needsDecision=false
      group.userAllRowsExcluded=true
      group.decision={
        decision:'hold',
        deferReason:rowExclusionReason(st,sample),
        userRowExcluded:!!(sample&&sample.userRowExcluded),
        autoRowExcluded:!!(sample&&sample.autoRowExcluded),
        exclusionKind:(sample&&sample.exclusionKind)||'manual'
      }
    }else if(active.length&&group.userAllRowsExcluded){
      // 一部復帰: 未決定へ戻す（詳細は再照合で再評価）
      group.needsDecision=true
      group.userAllRowsExcluded=false
      if(group.decision&&(group.decision.userRowExcluded||group.decision.autoRowExcluded)){
        delete group.decision
      }
    }
  }
  /**
   * 1行の除外／復帰を state へ反映（描画なし）。rowExclusions を唯一の追跡にする。
   * 保留（defer）とは別: 今回の商品登録スコープ外にするだけで削除・恒久除外にはしない。
   */
  function markRowExcludedLocal(st,row,excluded,opts){
    opts=opts||{}
    if(!row||!st.preview)return false
    const code=((row.csv||{}).code)||''
    const reason=opts.reason||USER_ROW_EXCLUDE_REASON
    if(excluded){
      snapshotRowBeforeExclude(st,row)
      setRowExcluded(st,row,true,{reason,kind:opts.kind||'manual'})
      row.status='hold'
      row.statusLabel='登録しない'
      row.canCommit=false
      row.userRowExcluded=true
      row.autoRowExcluded=false
      row.exclusionKind='manual'
      row.skuDecisionNeeded=false
      row.processingServiceNeedsConfirm=false
      row.deferReason=reason
      const drop=userExcludeReasonTokens()
      row.reasons=(row.reasons||[]).filter(x=>!drop.some(t=>String(x).includes(t)))
      row.reasons.push(reason)
    }else{
      setRowExcluded(st,row,false)
      const key=stableRowKeyFromRow(row)
      const snap=(st.rowExcludeSnapshots&&st.rowExcludeSnapshots[key])||null
      const restoreProc=opts.processingName!=null?String(opts.processingName).trim():''
      if(restoreProc){
        setRowClassification(st,row.rowNo,{
          processing:restoreProc,
          plannedName:restoreProc,
          userEdited:true,
          userRestoredFromAutoExclude:true,
          confirmed:true,
          acceptProposal:true
        })
      }else if(row.autoRowExcluded||(row.exclusionKind==='auto')){
        setRowClassification(st,row.rowNo,{userRestoredFromAutoExclude:true})
      }
      if(snap){
        row.status=snap.status
        row.statusLabel=snap.statusLabel
        row.canCommit=snap.canCommit
        row.reasons=(snap.reasons||[]).slice()
        row.skuDecisionNeeded=snap.skuDecisionNeeded
        row.processingServiceNeedsConfirm=snap.processingServiceNeedsConfirm
        delete st.rowExcludeSnapshots[key]
      }else{
        row.status='needs_review'
        row.statusLabel='要確認'
        row.canCommit=false
        row.skuDecisionNeeded=false
        row.processingServiceNeedsConfirm=!!restoreProc
        const drop=userExcludeReasonTokens()
        row.reasons=(row.reasons||[]).filter(x=>!drop.some(t=>String(x).includes(t)))
      }
      row.userRowExcluded=false
      row.autoRowExcluded=false
      row.exclusionKind=''
      row.deferReason=''
      if(restoreProc){
        const ps=Object.assign({},row.processingService||{})
        ps.serviceName=restoreProc
        ps.displayName=restoreProc
        ps.identifiable=true
        ps.needsConfirm=false
        ps.k2AutoExclude=false
        ps.autoExcludeReason=''
        ps.userRestoredFromAutoExclude=true
        row.processingService=ps
        const pc=Object.assign({},row.parenClassification||{})
        pc.processing=restoreProc
        pc.plannedName=restoreProc
        pc.userEdited=true
        pc.userRestoredFromAutoExclude=true
        row.parenClassification=pc
        row.processingServiceNeedsConfirm=false
        row.status='create'
        row.statusLabel='新規'
        row.canCommit=true
      }
    }
    syncSkuGroupAfterLocalExclude(st,code)
    return true
  }
  /**
   * 除外／復帰をローカル反映。全件再照合・CSV再読込・busy はしない。
   * 入力中の加工名・補足・チェック・詳細開閉・スクロールは harvest＋paintDialog で保持。
   */
  function applyLocalRowExclude(st,row,excluded,opts){
    opts=opts||{}
    if(!row||!st.preview)return false
    harvestFormEdits(st)
    markRowExcludedLocal(st,row,excluded,opts)
    // 集計・登録可否・所属セクションを描画し直す（サーバ往復なし・chase スクロールなし）
    paintDialog({preserveScroll:true})
    return true
  }
  /** 確認リスト1対象の除外スコープ行。他対象は巻き込まない。 */
  function rowsForSkipRegisterTarget(st,item){
    if(!item||!st||!st.preview)return []
    const previewRows=(st.preview.rows)||[]
    if(item.kind==='sku'&&item.code){
      // 品番グループ: その対象に属する元CSV行すべて
      return previewRows.filter(r=>String((r.csv||{}).code||'')===String(item.code))
    }
    // 加工サービス等: この対象に実際に含まれている行だけ
    return (item.rows||[]).slice()
  }
  function skipRegisterScopeLabel(item){
    if(!item)return 'この対象の行'
    if(item.kind==='sku')return '品番対象の全行'
    if(item.kind==='processing')return 'この加工対象の行'
    if(item.kind==='blank')return 'この部品・資材対象の行'
    if(item.kind==='custom')return 'この別注対象の行'
    if(item.kind==='unknown')return 'この不明品番対象の行'
    return 'この対象の行'
  }
  function skipRegisterButtonMeta(st,item){
    const all=rowsForSkipRegisterTarget(st,item)
    const active=all.filter(r=>!isRowExcluded(st,r))
    return {
      scope:skipRegisterScopeLabel(item),
      total:all.length,
      active:active.length,
      alreadyExcluded:Math.max(0,all.length-active.length),
      rows:active
    }
  }
  /**
   * 確認リスト対象を「今回登録しない」。詳細を開かず、rowExclusions へ共有反映。
   * 軽い UI 更新のみ（フル再照合なし）。保留とは別操作。
   */
  function applyLocalTargetSkipRegister(st,item,opts){
    opts=opts||{}
    if(!item||!st||!st.preview)return {excluded:0,total:0,alreadyExcluded:0}
    harvestFormEdits(st)
    const meta=skipRegisterButtonMeta(st,item)
    const reason=opts.reason||USER_ROW_EXCLUDE_REASON
    meta.rows.forEach(row=>{
      markRowExcludedLocal(st,row,true,{reason,kind:'manual'})
    })
    if(opts.paint!==false)paintDialog({preserveScroll:true})
    return {
      excluded:meta.rows.length,
      total:meta.total,
      alreadyExcluded:meta.alreadyExcluded,
      scope:meta.scope,
      itemId:item.id,
      targetLabel:item.targetLabel||''
    }
  }
  function skipRegisterActionsHtml(st,item){
    if(!item||isOpeningMode(st))return ''
    const meta=skipRegisterButtonMeta(st,item)
    if(!meta.active)return ''
    return `<div class="pl-target-skip-actions">
      <button type="button" class="quiet" data-pl-target-skip="${esc(item.id)}" ${st.busy?'disabled':''}>今回登録しない</button>
      <span class="excelhint pl-skip-scope">${esc(meta.scope)} ${meta.active}行</span>
    </div>`
  }
  /**
   * 同じ内容の複数行キー。サイズ違いの同一商品はまとめる。
   * 加工名未定＋同じ業者だけではまとめない（無関係な加工行を巻き込み防止）。
   */
  function sameContentKey(r){
    if(!r)return ''
    const csv=r.csv||{}
    const ps=r.processingService||{}
    const pc=r.parenClassification||{}
    const physicalParts=!!(r.physicalPartsView||pc.physicalPartsView||ps.isPhysicalParts)
    const processingView=!!(r.processingServiceView||pc.processingServiceView||(ps.isProcessingVendor&&!physicalParts))
    if(processingView){
      const name=String(ps.serviceName||pc.processing||'').trim()
      if(!name)return ''
      const vendor=String(ps.vendorName||csv.supplier||'').trim()
      const customer=String(csv.customer||'').trim()
      return `proc\u0001${name}\u0001${vendor}\u0001${customer}`
    }
    return `g\u0001${csv.customer||''}\u0001${csv.code||''}\u0001${csv.productName||''}\u0001${csv.supplier||''}`
  }
  function sameContentRows(st,r){
    const key=sameContentKey(r)
    if(!key)return []
    return ((st.preview&&st.preview.rows)||[]).filter(x=>sameContentKey(x)===key)
  }
  function listExcludedRows(st){
    const rows=((st.preview&&st.preview.rows)||[])
    const byKey={}
    rows.forEach(r=>{
      if(isRowExcluded(st,r))byKey[stableRowKeyFromRow(r)]=r
    })
    // プレビューに無いが除外マップに残る分（再照合直後の一瞬など）
    Object.keys(st.rowExclusions||{}).forEach(k=>{
      const ent=st.rowExclusions[k]
      if(!ent||ent.excluded===false||byKey[k])return
      const auto=ent.exclusionKind==='auto'
      byKey[k]={
        rowNo:ent.rowNo,csv:ent,
        userRowExcluded:!auto,autoRowExcluded:auto,
        exclusionKind:ent.exclusionKind||(auto?'auto':'manual'),
        deferReason:ent.reason||(auto?K2_AUTO_EXCLUDE_REASON:USER_ROW_EXCLUDE_REASON),
        status:'hold',statusLabel:'登録しない',
        reasons:[ent.reason||(auto?K2_AUTO_EXCLUDE_REASON:USER_ROW_EXCLUDE_REASON)]
      }
    })
    return Object.keys(byKey).map(k=>byKey[k]).sort((a,b)=>(Number(a.rowNo)||0)-(Number(b.rowNo)||0))
  }
  function rebindExclusionsAfterPreview(st){
    // 安定キーで付け直し。入力（rowClassifications）は rowNo キーのまま維持。
    const next={}
    const rows=((st.preview&&st.preview.rows)||[])
    const byKey={}
    rows.forEach(r=>{byKey[stableRowKeyFromRow(r)]=r})
    Object.keys(st.rowExclusions||{}).forEach(k=>{
      const ent=st.rowExclusions[k]
      if(!ent||ent.excluded===false)return
      const row=byKey[k]
      if(row){
        next[k]=Object.assign({},ent,{
          rowNo:row.rowNo,
          customer:(row.csv||{}).customer||ent.customer||'',
          code:(row.csv||{}).code||ent.code||'',
          productName:(row.csv||{}).productName||ent.productName||'',
          size:(row.csv||{}).size||ent.size||'',
          supplier:(row.csv||{}).supplier||ent.supplier||'',
          excluded:true
        })
      }else{
        next[k]=Object.assign({},ent,{excluded:true})
      }
    })
    st.rowExclusions=next
  }

  /** クライアント決定を優先（折りたたみでは承認にしない。state の decision のみ）。 */
  function effectiveDecision(st,g){
    const local=skuDecision(st,g&&g.code)
    if(local&&local.decision)return local.decision
    const remote=g&&g.decision
    if(remote&&typeof remote==='object')return remote.decision||''
    return ''
  }

  /** ユーザーが選んだ方針ラベル（確認済み一覧の「選んだ対応」列）。 */
  function chosenActionLabel(dec){
    if(dec==='merge')return 'まとめる'
    if(dec==='color_split')return '色別に分けて登録'
    if(dec==='separate')return '別物のため個別に整理'
    if(dec==='hold')return '保留'
    return '未決定'
  }

  /**
   * 方針選択後の必須入力が揃っているか。
   * 再照合前（反映待ち）は方針そのものの選択で足りる（色別／個別の詳細は再照合後）。
   * 再照合後は色別の完全性・個別整理の approach などを要求する。
   * 保留・自動決定は確認済みに入れない。
   */
  function userActionChoiceComplete(st,g){
    if(!g)return false
    const dec=effectiveDecision(st,g)
    if(dec!=='merge'&&dec!=='color_split'&&dec!=='separate')return false
    const pending=skuDecisionPendingRematch(st,g.code)
    if(dec==='merge'){
      if((g.existingCount||0)>1){
        const d=skuDecision(st,g.code)
        return !!d.keepProductId
      }
      return true
    }
    if(dec==='color_split'){
      if(pending)return true
      const split=g.colorSplitPlans||{}
      return !!(split.complete&&(split.plans||[]).length&&!(split.unassignedRows||[]).length
        &&!(split.plans||[]).some(p=>p&&p.needsCollisionChoice))
    }
    // separate
    if(pending)return true
    const variants=g.variants||[]
    if(!variants.length)return true
    const plans=(skuDecision(st,g.code).plans)||{}
    return variants.every(v=>{
      const p=plans[v.key]||{}
      if(!p.approach)return false
      if(p.approach==='change_code'&&!String(p.newCode||'').trim())return false
      if(p.approach==='as_process_service'){
        if(!String(p.serviceName||'').trim())return false
        if(!String(p.vendorName||'').trim())return false
      }
      return true
    })
  }

  /** 再照合後も方針に穴が残る場合 → 確認リストへ戻す。 */
  function stillProblematicAfterRematch(st,g){
    const dec=effectiveDecision(st,g)
    if(dec!=='merge'&&dec!=='color_split'&&dec!=='separate')return false
    if(skuDecisionPendingRematch(st,g.code))return false
    return !userActionChoiceComplete(st,g)
  }

  function confirmedItemStatusKey(st,g){
    if(skuDecisionPendingRematch(st,g&&g.code))return 'pending'
    return 'confirmed'
  }

  function confirmedItemStatusLabel(st,g){
    return CONFIRMED_STATUS[confirmedItemStatusKey(st,g)]||CONFIRMED_STATUS.confirmed
  }

  /** 方針選択済みだが必須入力が残るときの残課題（確認リスト側に表示）。 */
  function remainingDecisionIssues(st,g){
    const dec=effectiveDecision(st,g)
    const out=[]
    if(dec==='merge'&&(g.existingCount||0)>1){
      const d=skuDecision(st,g.code)
      if(!d.keepProductId){
        out.push({type:'merge_keep',label:'残す商品の選択',detail:'登録済が複数あるため残す商品を選んでください',fieldKey:'name',resolved:false})
      }
    }
    if(dec==='color_split'&&!skuDecisionPendingRematch(st,g.code)){
      const split=g.colorSplitPlans||{}
      if(!(split.plans||[]).length){
        out.push({type:'color_split',label:'色別の登録先が未作成',detail:'商品名の色を確認するか行き先を指定してください',fieldKey:'color',resolved:false})
      }
      ;(split.unassignedRows||[]).forEach(u=>{
        out.push({type:'color_dest',label:`行${u.rowNo}の行き先未定`,detail:u.reason||'',fieldKey:'color',resolved:false})
      })
      ;(split.plans||[]).forEach(p=>{
        if(p&&p.needsCollisionChoice){
          out.push({type:'color_collision',label:`色「${p.color||''}」の品番衝突`,detail:'既存を使うか社内管理品番を編集してください',fieldKey:'sku',resolved:false})
        }
      })
    }
    if(dec==='separate'&&!skuDecisionPendingRematch(st,g.code)){
      const plans=(skuDecision(st,g.code).plans)||{}
      ;(g.variants||[]).forEach(v=>{
        const p=plans[v.key]||{}
        if(!p.approach){
          out.push({type:'separate_approach',label:'「'+(v.productName||'（名なし）')+'」の整理方法',detail:'品番修正・部品・加工サービス・品番なし・保留のいずれかを選んでください',fieldKey:'name',resolved:false})
        }else if(p.approach==='change_code'&&!String(p.newCode||'').trim()){
          out.push({type:'separate_code',label:'「'+(v.productName||'（名なし）')+'」の修正品番',detail:'修正品番を入力してください',fieldKey:'sku',resolved:false})
        }else if(p.approach==='as_process_service'){
          if(!String(p.serviceName||'').trim()){
            out.push({type:'separate_process_name',label:'「'+(v.productName||'（名なし）')+'」の加工名',detail:'加工名（登録・表示名）を入力してください',fieldKey:'name',resolved:false})
          }
          if(!String(p.vendorName||'').trim()){
            out.push({type:'separate_process_vendor',label:'「'+(v.productName||'（名なし）')+'」の加工業者',detail:'加工業者を入力してください',fieldKey:'supplier',resolved:false})
          }
        }
      })
    }
    return out
  }

  /**
   * 区分は judgment の state フィールドで分類（背景色では判定しない）。
   * - 個別確認が必要: needsDecision && reviewTier===individual && （未決定 or 方針選択済みだが必須未完）
   * - 一括承認候補: needsDecision && reviewTier===bulk && 未決定（未承認）
   * - 確認済み一覧: ユーザーがまとめる／色別／個別整理を選び必須が揃ったもの（自動決定は含めない）
   * - 自動決定済み: !needsDecision（autoMergePlans / plannedSingleProduct）※確認済みには混ぜない
   * - 保留: decision===hold（確認済みに混ぜない）
   * 旧「承認済み」「個別整理済み」は確認済み一覧へ統合（二重表示しない）
   */
  function partitionJudgmentGroups(st){
    const groups=((st&&st.preview&&st.preview.skuGroups)||[])
    const individual=[]
    const bulkUnapproved=[]
    const confirmed=[]
    const hold=[]
    groups.forEach(g=>{
      if(!g||!g.needsDecision)return
      const dec=effectiveDecision(st,g)
      const tier=g.reviewTier||'individual'
      if(dec==='hold'){hold.push(g);return}
      if(dec==='merge'||dec==='color_split'||dec==='separate'){
        if(userActionChoiceComplete(st,g)){confirmed.push(g);return}
        // 必須未完・再照合後の穴 → 確認リスト側に残し、選んだ対応＋残課題を出す
        individual.push(g)
        return
      }
      if(tier==='bulk'){bulkUnapproved.push(g);return}
      individual.push(g)
    })
    // 互換: 旧 approved = 確認済みのうちまとめる／色別。separate = 確認済みの個別整理
    const approved=confirmed.filter(g=>{
      const d=effectiveDecision(st,g)
      return d==='merge'||d==='color_split'
    })
    const separate=confirmed.filter(g=>effectiveDecision(st,g)==='separate')
    const autoPlans=((st&&st.preview&&st.preview.autoMergePlans)||[]).slice()
    const autoCount=(st&&st.preview&&st.preview.plannedCounts&&st.preview.plannedCounts.autoMergedSkuGroups!=null)
      ?Number(st.preview.plannedCounts.autoMergedSkuGroups)||autoPlans.length
      :autoPlans.length
    const excludedRows=(st&&st.preview&&st.preview.outcomeSummary&&st.preview.outcomeSummary.excludedRows)||0
    return {
      individual,bulkUnapproved,approved,confirmed,hold,separate,autoPlans,autoCount,excludedRows
    }
  }

  function isNormalLedgerCode(code){
    const t=String(code||'').trim()
    if(!t)return false
    if(t==='別注'||t==='不明')return false
    return true
  }

  function rowIssueResolved(st,r,issue){
    const key=String(r.rowNo)
    const edit=(st.rowClassifications&&st.rowClassifications[key])||{}
    const sel=(st.selections&&st.selections[key])||{}
    if(issue.type==='paren_confirm')return !!edit.confirmed
    if(issue.type==='processing_name'){
      const ps=r.processingService||{}
      const pc=r.parenClassification||{}
      const name=String(edit.processing!=null?edit.processing:(ps.serviceName||pc.processing||'')).trim()
      return !!edit.confirmed&&!!name
    }
    if(issue.type==='product_pick')return !!(sel.productId||sel.forceCreate)
    if(issue.type==='handle_pick')return !!sel.handleId
    if(issue.type==='category'){
      const cur=sel.category!=null?sel.category:((r.product||{}).category||'')
      return cur!=null&&String(cur)!==''
    }
    return false
  }

  function collectRowIssues(st,r){
    const issues=[]
    const ps=r.processingService||{}
    const pc=r.parenClassification||{}
    const processingView=!!(r.processingServiceView||pc.processingServiceView||(ps.isProcessingVendor&&!r.physicalPartsView&&!pc.physicalPartsView&&!ps.isPhysicalParts))
    if(processingView&&(ps.needsConfirm||pc.needsConfirm)){
      issues.push({
        type:'processing_name',
        label:'加工名の確認',
        detail:(ps.missingFields||[]).join('・')||pc.processingMissingHint||'',
        fieldKey:'procName'
      })
    }else if(pc.needsConfirm||r.parenNeedsConfirm){
      issues.push({
        type:'paren_confirm',
        label:'括弧分類・メーカー補正候補の確認',
        detail:(r.reasons||[]).join(' '),
        fieldKey:'name'
      })
    }
    if(r.status==='needs_review'&&r.product&&r.product.candidates&&r.product.candidates.length&&!r.skuDecisionNeeded){
      issues.push({type:'product_pick',label:'商品の選択',detail:'',fieldKey:'name'})
    }
    if(r.status==='needs_review'&&r.handle&&r.handle.candidates&&r.handle.candidates.length){
      issues.push({type:'handle_pick',label:'取扱の選択',detail:'',fieldKey:'supplier'})
    }
    if(r.status==='needs_review'&&r.product&&r.product.blankSku){
      issues.push({type:'category',label:'区分の確認',detail:'',fieldKey:'sku'})
    }
    if(r.status==='needs_review'&&!issues.length){
      const labs=(r.reasons||[]).filter(Boolean)
      ;(labs.length?labs:['要確認']).forEach(lab=>{
        issues.push({type:'generic',label:String(lab),detail:'',fieldKey:''})
      })
    }
    return issues.map(iss=>Object.assign({},iss,{resolved:rowIssueResolved(st,r,iss)}))
  }

  function unifiedTargetMetaForRow(r){
    const csv=r.csv||{}
    const ps=r.processingService||{}
    const pc=r.parenClassification||{}
    const physicalParts=!!(r.physicalPartsView||pc.physicalPartsView||ps.isPhysicalParts)
    const processingView=!!(r.processingServiceView||pc.processingServiceView||(ps.isProcessingVendor&&!physicalParts))
    if(processingView){
      const name=ps.serviceName||pc.processing||''
      const vendor=ps.vendorName||csv.supplier||''
      const label=`${name||'（加工名未定）'}／${vendor||'（業者未定）'}`
      return {key:processingUnifiedKey(name,vendor),kind:'processing',label,code:''}
    }
    const code=String(csv.code||'')
    if(isNormalLedgerCode(code)&&!(r.product&&r.product.blankSku)){
      return {key:`sku-row:${code}`,kind:'sku_row',label:csv.codeDisplay||code,code}
    }
    // 品番なし・別注・不明・実物部品: 既存 matchGroupKey で無関係なものをまとめない
    const mg=r.matchGroupKey||`row:${r.rowNo}`
    let kind='row'
    if(physicalParts||(r.product&&r.product.blankSku)||!code)kind='blank'
    else if(code==='別注')kind='custom'
    else if(code==='不明')kind='unknown'
    const label=code
      ?`${csv.codeDisplay||code}／${csv.productName||'—'}`
      :(csv.productName||`行${r.rowNo}`)
    return {key:`mg:${mg}`,kind,label,code,matchGroupKey:mg}
  }

  /**
   * 統合確認リスト（表示用）。品番単位の個別確認と行単位の要確認を
   * 「登録するもの・判断する対象ごと」に1行へ集約。二重表示しない。
   * 判定・保存ロジック自体は skuGroups / rows のまま。
   */
  function buildUnifiedReviewItems(st){
    const part=partitionJudgmentGroups(st)
    const items=[]
    const coveredSkuCodes=new Set()
    const previewRows=((st.preview&&st.preview.rows)||[])

    ;(part.individual||[]).forEach(g=>{
      const groupRows=previewRows.filter(r=>{
        const code=String((r.csv||{}).code||'')
        return code&&code===String(g.code)
      })
      const activeRows=groupRows.filter(r=>!isRowExcluded(st,r))
      // 全行除外の対象は未解決リストから外す
      if(groupRows.length&&!activeRows.length){
        coveredSkuCodes.add(String(g.code))
        return
      }
      const reasons=(g.reviewReasons||[]).map(r=>({
        type:'sku_reason',
        code:r.code||'',
        label:r.label||'要確認',
        detail:(r.values||[]).join(' / '),
        values:r.values||[],
        fieldKey:planFieldKeyForReason(r.code||''),
        resolved:false
      }))
      const leftover=remainingDecisionIssues(st,g)
      const issues=reasons.concat(leftover)
      const issueList=issues.length?issues:[{type:'sku_reason',label:'個別確認',detail:'',fieldKey:'',resolved:false}]
      items.push({
        id:`sku:${g.code}`,
        kind:'sku',
        sortKey:String(g.codeDisplay||g.code||''),
        targetLabel:g.codeDisplay||g.code,
        plannedName:plannedNameForGroup(st,g),
        plannedSupplier:plannedSupplierDisplay(st,g),
        plannedColor:g.plannedRegisteredColor||g.plannedColor||'',
        issues:issueList,
        unresolvedIssueCount:issueList.filter(i=>!i.resolved).length,
        group:g,
        rows:activeRows,
        rowNos:activeRows.length?activeRows.map(x=>x.rowNo):(g.rowNos||[]),
        code:g.code,
        chosenAction:effectiveDecision(st,g)||''
      })
      coveredSkuCodes.add(String(g.code))
    })

    const buckets={}
    previewRows.forEach(r=>{
      if(!r||r.status!=='needs_review')return
      if(isRowExcluded(st,r))return
      // 品番比較待ちは SKU 区分（個別／一括）側。下リストへ重複させない
      if(r.skuDecisionNeeded)return
      const csv=r.csv||{}
      const code=String(csv.code||'')
      if(code&&coveredSkuCodes.has(code))return
      const meta=unifiedTargetMetaForRow(r)
      if(!buckets[meta.key])buckets[meta.key]={meta,rows:[],issueMap:{}}
      buckets[meta.key].rows.push(r)
      collectRowIssues(st,r).forEach(iss=>{
        const ik=`${iss.type}|${iss.label}`
        const prev=buckets[meta.key].issueMap[ik]
        if(!prev)buckets[meta.key].issueMap[ik]=iss
        else if(!iss.resolved)prev.resolved=false
      })
    })

    Object.keys(buckets).forEach(bk=>{
      const b=buckets[bk]
      let issues=Object.keys(b.issueMap).map(k=>b.issueMap[k])
      let open=issues.filter(i=>!i.resolved)
      // 行ステータスが needs_review のままなら、ローカル確認済みでも再照合までリストに残す
      if(!open.length&&issues.length){
        issues=issues.concat([{type:'repreview',label:'再照合して反映',detail:'確認内容をプレビューへ反映してください',resolved:false}])
        open=issues.filter(i=>!i.resolved)
      }
      if(!open.length)return
      const sample=b.rows[0]||{}
      const csv=sample.csv||{}
      const ps=sample.processingService||{}
      const pc=sample.parenClassification||{}
      const edit=(st.rowClassifications&&st.rowClassifications[String(sample.rowNo)])||{}
      items.push({
        id:b.meta.key,
        kind:b.meta.kind,
        sortKey:String(b.meta.label||''),
        targetLabel:b.meta.label,
        plannedName:edit.plannedName!=null?edit.plannedName:(ps.serviceName||pc.plannedName||csv.productName||''),
        plannedSupplier:ps.vendorName||csv.supplier||'',
        plannedColor:edit.color!=null?edit.color:(pc.color||''),
        issues,
        unresolvedIssueCount:open.length,
        group:null,
        rows:b.rows,
        rowNos:b.rows.map(x=>x.rowNo),
        code:b.meta.code||csv.code||'',
        matchGroupKey:b.meta.matchGroupKey||sample.matchGroupKey||''
      })
    })

    items.sort((a,b)=>String(a.sortKey).localeCompare(String(b.sortKey),'ja'))
    return items
  }

  function unifiedReviewCounts(st){
    const items=buildUnifiedReviewItems(st)
    return {
      items,
      unresolved:items.length,
      issueCount:items.reduce((n,it)=>n+(Number(it.unresolvedIssueCount)||0),0)
    }
  }

  function judgmentStatusCounts(st){
    const p=partitionJudgmentGroups(st)
    const unified=unifiedReviewCounts(st)
    const rowExcluded=listExcludedRows(st).length
    let confirmedPending=0
    let confirmedDone=0
    ;(p.confirmed||[]).forEach(g=>{
      if(confirmedItemStatusKey(st,g)==='pending')confirmedPending+=1
      else confirmedDone+=1
    })
    return {
      // 未解決 = 統合リストの未解決対象数（上下二重計上しない）
      unresolved:unified.unresolved,
      issueCount:unified.issueCount,
      individualSku:p.individual.length,
      unapproved:p.bulkUnapproved.length,
      // approved は互換（確認済みのうちまとめる／色別）。UI件数は confirmed を正とする
      approved:p.approved.length,
      confirmed:(p.confirmed||[]).length,
      confirmedPending,
      confirmedDone,
      autoDecided:p.autoCount,
      hold:p.hold.length,
      separate:p.separate.length,
      excluded:p.excludedRows,
      rowExcluded
    }
  }

  function productsRegisterBlocked(st){
    const c=judgmentStatusCounts(st)
    // 確認リスト未解決=0 かつ 一括承認候補の未承認=0、かつ未反映の方針変更なしで登録可
    return (c.unresolved>0)||(c.unapproved>0)||(pendingRematchCount(st)>0)
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
    const unit=key==='unified'?'件':'品番'
    return `<div class="bar legacy-csv-pager sku-section-pager" data-sku-section-pager="${esc(key)}">
      <button type="button" data-pl-section-page="${esc(key)}" data-dir="-1" ${slice.page<=1?'disabled':''}>前へ</button>
      <span>${slice.page} / ${slice.pages}ページ（${total}${unit}）</span>
      <button type="button" data-pl-section-page="${esc(key)}" data-dir="1" ${slice.page>=slice.pages?'disabled':''}>次へ</button>
    </div>`
  }

  function judgmentStatusBarHtml(st){
    const c=judgmentStatusCounts(st)
    const pending=pendingRematchCount(st)
    return `<div class="sku-judgment-status-bar" data-sku-status-counts>
      <p class="notice sku-preregister-banner"><strong>登録確定前の確認画面です。</strong>「確認済み」「自動決定済み」「手動確定済み」は登録予定の状態であり、台帳への保存済み（登録完了）ではありません。最終の「確認して商品を登録」まで商品・在庫は変わりません。</p>
      <p class="excelhint"><strong>登録前の件数:</strong>
        未解決 ${c.unresolved}（確認リストの対象）　確認事項 ${c.issueCount}　未承認 ${c.unapproved}　確認済み ${c.confirmed}（反映待ち ${c.confirmedPending}／確認済み ${c.confirmedDone}）　自動決定済み（未登録） ${c.autoDecided}　保留 ${c.hold}${c.rowExcluded?`　登録しない ${c.rowExcluded}`:''}${c.excluded?`　対象外 ${c.excluded}`:''}${pending?`　未反映 ${pending}`:''}
      </p>
      <p class="excelhint">この確認リストの未解決が0で、必要な承認も完了すれば登録可能です。ユーザー確認済みは下の「確認済み一覧」で追跡します（自動決定・保留・登録しないとは混ぜません）。保留 ${c.hold} 件・登録しない ${c.rowExcluded||0} 行は登録対象外です。${pending?' 未反映の方針変更がある間は再照合してください。':''}</p>
      ${pendingRematchBannerHtml(st)}
    </div>`
  }

  function previewPayload(st){
    const body={
      importMode:st.importMode||MODE_PRODUCTS,
      selections:st.selections,skuDecisions:st.skuDecisions,
      rowClassifications:st.rowClassifications||{},
      rowExclusions:st.rowExclusions||{},
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
    // 軽編集は再照合しない。予定反映は最終の再照合まで未反映
    if(!(patch&&patch.pendingRematch===false))cur.pendingRematch=true
    st.rowClassifications[key]=cur
  }

  function clearRowClassificationPending(st){
    Object.keys(st.rowClassifications||{}).forEach(k=>{
      const cur=st.rowClassifications[k]
      if(cur&&typeof cur==='object')cur.pendingRematch=false
    })
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
    const c=judgmentStatusCounts(st)
    const fmt=(arr)=>((arr||[]).slice(0,8).map(x=>`${esc(x.label)} ${x.count}`).join('／'))||'—'
    const more=(arr)=>((arr||[]).length>8?` …他${(arr||[]).length-8}`:'')
    return `<div class="sku-outcome-summary">
      <h3>取込判断の件数</h3>
      <p><strong>確認リスト未解決</strong> ${c.unresolved} 件（確認事項 ${c.issueCount}）
        <span class="sub">品番比較・行要確認を統合。同じ対象は二重計上しません</span></p>
      <p><strong>自動まとめ</strong> ${s.autoMergedSkuGroups||0} 品番 / ${s.autoMergedCsvRows||0} 行
        <span class="sub">（${fmt(s.autoMergedReasons)}${more(s.autoMergedReasons)}）</span></p>
      <p><strong>参考: 品番比較 未決定</strong> ${s.needsReviewSkuGroups||0} 品番
        <span class="sub">（${fmt(s.needsReviewSkuReasons)}${more(s.needsReviewSkuReasons)}）</span></p>
      ${(s.needsReviewRows||0)?`<p><strong>参考: 行要確認</strong> ${s.needsReviewRows} 行
        <span class="sub">（${fmt(s.needsReviewRowReasons)}${more(s.needsReviewRowReasons)}）／作業は確認リスト側</span></p>`:''}
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

  function processingServiceAutoHtml(st){
    const plans=((st.preview&&st.preview.processingServicePlans)||[]).filter(p=>p&&p.autoRegister)
    if(!plans.length)return ''
    const items=plans.map(p=>{
      const codes=(p.targetGarmentCodes||[]).slice(0,6).map(esc).join('、')
      const more=(p.targetGarmentCodes||[]).length>6?` …他${(p.targetGarmentCodes||[]).length-6}`:''
      const serviceName=p.serviceName||p.displayName||'加工'
      const samples=(p.rowSpecsSamples||[]).slice(0,3).map(s=>{
        const bits=[
          s.originalName||'',
          s.customerSpecific?`個別仕様 ${s.customerSpecific}`:'',
          s.position?`位置 ${s.position}`:'',
          s.dimensions?`寸法 ${s.dimensions}`:'',
          s.composition?`構成 ${s.composition}`:''
        ].filter(Boolean)
        return `<li>行${esc(s.rowNo)}: ${esc(bits.join(' / ')||'—')}</li>`
      }).join('')
      return `<article class="sku-auto-plan sku-auto-processing" data-sku-section="auto-processing">
        <strong>${esc(serviceName)} <span class="badge">加工サービス（自動）</span></strong>
        <ul class="sku-processing-fields">
          <li>加工名: <strong>${esc(p.serviceName||'—')}</strong></li>
          <li>加工業者: <strong>${esc(p.vendorName||'—')}</strong></li>
          <li>補足／個別仕様: 内部照合用（通常画面には別名一覧を出しません）</li>
          <li>区分: 加工サービス　品番なし（対象品番 ${codes||'—'}${more}）</li>
        </ul>
        <p class="excelhint">一覧・登録の表示名は加工名をそのまま使います。登録時表示名の別入力はありません。</p>
        ${samples?`<details class="sku-processing-specs"><summary>元行の補足・個別仕様（内部照合用）</summary><ul>${samples}</ul></details>`:''}
      </article>`
    }).join('')
    const sum=st.preview&&st.preview.processingServiceSummary||{}
    return `<div class="sku-processing-auto">
      <p class="excelhint"><strong>加工サービスの自動整理</strong>（${sum.autoCount||plans.length} 件 / 新規予定 ${sum.createCount||0}・再利用 ${sum.reuseCount||0}）。確定まで未登録です。加工ごとの個別承認は不要です。</p>
      ${items}
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
      ${processingServiceAutoHtml(st)}
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

  /** 直近の成功した再照合時点の方針（decision）だけを記録。成功時のみ更新。 */
  function appliedDecisionForCode(st,code){
    const snap=st.appliedSkuDecisions&&st.appliedSkuDecisions[code]
    if(snap&&Object.prototype.hasOwnProperty.call(snap,'decision'))return snap.decision||''
    return ''
  }

  function snapshotAppliedSkuDecisions(st){
    const snap={}
    const codes=new Set([
      ...Object.keys(st.skuDecisions||{}),
      ...(((st.preview&&st.preview.skuGroups)||[]).map(g=>g&&g.code).filter(Boolean))
    ])
    codes.forEach(code=>{
      const local=skuDecision(st,code)
      let dec=local.decision||''
      if(!dec){
        const g=((st.preview&&st.preview.skuGroups)||[]).find(x=>x&&x.code===code)
        const remote=g&&g.decision
        if(remote&&typeof remote==='object')dec=remote.decision||''
        else if(typeof remote==='string')dec=remote
      }
      snap[code]={decision:dec||''}
    })
    st.appliedSkuDecisions=snap
  }

  function skuDecisionPendingRematch(st,code){
    if(!code||!st||!st.preview)return false
    const cur=skuDecision(st,code).decision||''
    const applied=appliedDecisionForCode(st,code)
    return cur!==applied
  }

  function pendingRematchCodes(st){
    const codes=new Set()
    Object.keys(st.skuDecisions||{}).forEach(code=>{
      if(skuDecisionPendingRematch(st,code))codes.add(code)
    })
    ;((st.preview&&st.preview.skuGroups)||[]).forEach(g=>{
      if(g&&g.code&&skuDecisionPendingRematch(st,g.code))codes.add(g.code)
    })
    return [...codes]
  }

  function pendingRematchCount(st){
    return pendingRematchCodes(st).length
  }

  function pendingDecisionLabel(dec){
    if(dec==='merge')return 'まとめるを選択中【未反映】'
    if(dec==='color_split')return '色別に分けて登録を選択中【未反映】'
    if(dec==='separate')return '個別に整理を選択中【未反映】'
    if(dec==='hold')return '保留を選択中【未反映】'
    return '選択中【未反映】'
  }

  function decisionDisplayLabel(st,g){
    const dec=effectiveDecision(st,g)
    if(dec&&skuDecisionPendingRematch(st,g&&g.code))return pendingDecisionLabel(dec)
    return decisionLabel(dec)
  }

  function pendingRematchBannerHtml(st){
    const n=pendingRematchCount(st)
    if(!n)return ''
    return `<div class="pl-pending-rematch-bar" role="status" data-pl-pending-count="${n}">
      <div class="pl-pending-rematch-copy">
        <strong>未反映の変更 ${n} 件</strong>
        <span class="excelhint">方針の選択は編集状態に保存済みです。登録予定への反映は「選択を反映して再照合」でまとめて行います。</span>
      </div>
      <button type="button" id="plRePreviewPending" class="primary" ${st.busy?'disabled':''}>選択を反映して再照合</button>
    </div>`
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

  /**
   * 品番チェック変更用の軽量更新。paintDialog（一覧・詳細の再生成）はしない。
   * - 内部 selectedCodes は呼び出し側で更新済みであること
   * - 「選択中○品番」表示・一括操作ボタンの有効／無効
   * - 同一品番のチェック欄が複数あっても checked を同期
   * スクロール・フォーカス・開いている詳細・編集中の値は触らない。busy にはしない。
   */
  function syncSkuSelectionUi(st){
    st=st||ensureState()
    if(!st.selectedCodes)st.selectedCodes={}
    const selected=selectedCodeList(st)
    const n=selected.length
    const countEl=document.getElementById('plSkuSelCount')
    if(countEl)countEl.textContent=`選択中 ${n} 品番（開いている区分の表示ページ／個別チェック）`
    const busy=!!st.busy
    const none=!n
    const clearSel=document.getElementById('plSkuClearSel')
    if(clearSel)clearSel.disabled=busy||none
    const bulkHold=document.getElementById('plSkuBulkHold')
    if(bulkHold)bulkHold.disabled=busy||none
    const bulkMaker=document.getElementById('plBulkMakerFetch')
    if(bulkMaker){
      const running=!!(st.bulkMakerLookup&&st.bulkMakerLookup.running)
      bulkMaker.disabled=busy||none||running
    }
    document.querySelectorAll('[data-pl-sku-check]').forEach(el=>{
      const code=el.getAttribute('data-pl-sku-check')
      const want=!!st.selectedCodes[code]
      if(el.checked!==want)el.checked=want
    })
  }

  function colorSplitSummaryForGroup(g){
    const split=g.colorSplitPlans||{}
    if(split.summaryLabel)return split.summaryLabel
    const colors=(split.plans||[]).map(p=>p.color).filter(Boolean)
    const uniq=[]
    colors.forEach(c=>{if(!uniq.includes(c))uniq.push(c)})
    if(!uniq.length)return '色別登録（色未確定）'
    return '色別登録：'+uniq.join('・')
  }

  function colorSplitNameMappings(g){
    const split=g.colorSplitPlans||{}
    if(Array.isArray(split.nameMappings)&&split.nameMappings.length)return split.nameMappings
    const out=[]
    ;(split.plans||[]).forEach(p=>{
      ;(p.rowNos||[]).forEach(rn=>{
        out.push({
          originalName:'',
          sourceRowNo:rn,
          registrationName:p.productName||'',
          isAlias:false,
          color:p.color||''
        })
      })
    })
    ;(split.unassignedRows||[]).forEach(u=>{
      out.push({
        originalName:u.originalName||'',
        sourceRowNo:u.rowNo,
        registrationName:'',
        isAlias:false,
        unassigned:true,
        reason:u.reason||'行き先未定',
        color:''
      })
    })
    return out
  }

  function plannedNameForGroup(st,g){
    const d=skuDecision(st,g.code)
    // 現在の選択のみ見る（再照合前に残る g.colorSplit フラグでは統合表示へ戻さない）
    if((d.decision||'')==='color_split'){
      return colorSplitSummaryForGroup(g)
    }
    if(d.officialName)return d.officialName
    // 色別表示用に上書きされた namePlan は、まとめへ戻した直後は使わない
    if(g.namePlan&&g.namePlan.colorSplit){
      return g.plannedRegisteredName||(g.nameCandidates&&g.nameCandidates[0])||''
    }
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
    // 操作列の短い状態。確認済み一覧では chosenActionLabel + confirmedItemStatusLabel を使う
    if(dec==='merge')return 'まとめる'
    if(dec==='color_split')return '色別に分けて登録'
    if(dec==='separate')return '別物のため個別に整理'
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
    const pending=skuDecisionPendingRematch(st,g.code)
    const hasPlans=!!plans.length
    // 再照合前は色別予定が無い／古い統合対応表を確定予定として出さない
    if(pending&&!hasPlans){
      return `<div class="sku-color-split-form" data-plan-edit="color">
        <p class="notice">色別に分けて登録を選択中【未反映】。色別の登録先・社内管理品番は再照合後に設定できます。</p>
        <p class="excelhint">入力済みの色別編集があれば保持します。方針切替では消しません。「選択を反映して再照合」で登録予定を更新してください。</p>
      </div>`
    }
    return `<div class="sku-color-split-form" data-plan-edit="color">
      <p class="excelhint">${esc(split.note||'色ごとに別商品として登録予定です。まだ保存していません。確認・編集のうえ最終登録してください。統合用の仮代表名は使いません。')}</p>
      ${pending?'<p class="notice">色別に分けて登録を選択中【未反映】。表示中の予定は前回の再照合結果です。再照合で最新化してください。</p>':''}
      <p class="excelhint">メーカー品番は元の「${esc(g.code)}」のまま（先頭ゼロも維持）。社内管理品番の初期値は「品番-色」です。緑と蛍光グリーンは別色です。</p>
      ${planCards||'<p class="notice">色候補をまだ作れません。商品名の色を確認するか、行き先を指定してください。再照合後に設定できます。</p>'}
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

  function skuRowDetailHtml(st,g,opts){
    opts=opts||{}
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
      const detected=detectedProcessServiceForVariant(st,g,v)
      const isProc=plan.approach==='as_process_service'
      const serviceName=plan.serviceName!=null?plan.serviceName:(detected.serviceName||'')
      const vendorName=plan.vendorName!=null?plan.vendorName:(detected.vendorName||'')
      const supplement=plan.supplement!=null?plan.supplement:(detected.supplement||'')
      const procFields=isProc?`
        <div class="sku-sep-process-fields">
          <label>加工名<input data-pl-sep-service="${esc(g.code)}" data-pl-sep-key="${esc(v.key)}" value="${esc(serviceName)}" placeholder="例: ライン加工" ${st.busy||decision!=='separate'?'disabled':''}></label>
          <label>加工業者<input data-pl-sep-vendor="${esc(g.code)}" data-pl-sep-key="${esc(v.key)}" value="${esc(vendorName)}" placeholder="CSV仕入先" ${st.busy||decision!=='separate'?'disabled':''}></label>
          <label>補足／個別仕様<input data-pl-sep-supp="${esc(g.code)}" data-pl-sep-key="${esc(v.key)}" value="${esc(supplement)}" placeholder="位置・寸法・顧客固有など" ${st.busy||decision!=='separate'?'disabled':''}></label>
          <p class="excelhint">加工名が登録・表示名です（別の表示名欄はありません）。衣服品番は加工SKUに使いません。同名・同業者のみ再利用し、別加工名は別名統合しません。</p>
        </div>`:''
      return `<div class="sku-variant-plan" data-variant-key="${esc(v.key)}">
        <strong>${esc(v.productName||'（名なし）')}</strong>
        <span class="sub">仕入 ${esc(v.supplier||'未設定')}　サイズ ${(v.sizes||[]).map(esc).join('、')||'—'}　顧客 ${(v.customers||[]).slice(0,3).map(esc).join('、')}${(v.customers||[]).length>3?'…':''}　CSV行 ${(v.rowNos||[]).join(',')||'—'}</span>
        <label>整理方法<select data-pl-sep-approach="${esc(g.code)}" data-pl-sep-key="${esc(v.key)}" ${st.busy||decision!=='separate'?'disabled':''}>
          <option value="">選択…</option>
          <option value="change_code" ${plan.approach==='change_code'?'selected':''}>品番を修正して登録</option>
          <option value="as_parts" ${plan.approach==='as_parts'?'selected':''}>部品・資材として整理</option>
          <option value="as_process_service" ${plan.approach==='as_process_service'?'selected':''}>加工サービスとして整理</option>
          <option value="as_blank" ${plan.approach==='as_blank'?'selected':''}>品番なしとして整理</option>
          <option value="defer" ${plan.approach==='defer'?'selected':''}>この内容は保留</option>
        </select></label>
        <label>修正品番<input data-pl-sep-code="${esc(g.code)}" data-pl-sep-key="${esc(v.key)}" value="${esc(plan.newCode||'')}" placeholder="別の品番" ${st.busy||decision!=='separate'||plan.approach!=='change_code'?'disabled':''}></label>
        ${procFields}
      </div>`
    }).join('')
    const customers=[...new Set((g.variants||[]).flatMap(v=>v.customers||[]))]
    const sizes=[...new Set((g.variants||[]).flatMap(v=>v.sizes||[]))]
    const rowNos=(g.rowNos||[]).join(', ')
    const namePlan=g.namePlan||{}
    const isColorSplit=decision==='color_split'
    // 色別選択中は統合用 nameMappings / 仮代表名を有効予定として出さない
    const mapSource=isColorSplit?colorSplitNameMappings(g):(namePlan.nameMappings||[])
    const mappings=mapSource.slice(0,24).map(m=>{
      if(m.unassigned||!m.registrationName){
        return `<li class="sku-name-map-missing">${esc(m.originalName||'（名なし）')}（行${m.sourceRowNo}）→ <strong>行き先未定</strong>${m.reason?`（${esc(m.reason)}）`:''}</li>`
      }
      const colorBit=m.color?`［色 ${esc(m.color)}］`:''
      return `<li>${esc(m.originalName||'')}（行${m.sourceRowNo}）→ ${esc(m.registrationName||'')}${m.isAlias?'（別名）':''}${colorBit}</li>`
    }).join('')
    const split=g.colorSplitPlans||{}
    const splitGaps=isColorSplit&&(!(split.plans||[]).length||(split.unassignedRows||[]).length||!split.complete)
      ?`<p class="notice">${!(split.plans||[]).length
          ?'色別の登録先がまだ作れません。商品名の色を確認するか、行き先を指定してください（統合用の名称対応表では代用しません）。'
          :'行き先未定の行や品番衝突があります。不足を解消してください（統合用の仮代表名・別名対応は使いません）。'}</p>`
      :''
    const namePlanBlock=isColorSplit
      ?`${splitGaps}
        <p class="excelhint">名称採用: 色別に分けて登録（${esc(colorSplitSummaryForGroup(g))}）</p>
        ${mappings?`<ul class="sku-name-map">${mappings}</ul>`:''}`
      :(namePlan.colorSplit
        ?`<p class="excelhint">「同じ商品としてまとめる」に切り替えました。再照合すると統合用の名称予定が表示されます（色別の対応表は使いません）。${skuDecisionPendingRematch(st,g.code)?'【未反映】':''}</p>`
        :`${namePlan.adoptionRule?`<p class="excelhint">名称採用ルール: ${esc(namePlan.adoptionRule)}${namePlan.provisional?`（ステータス「${esc(st.preview&&st.preview.provisionalNameTag||'名称未整理')}」※名前には埋め込みません）`:''}</p>`:''}
        ${(namePlan.nameCandidates&&namePlan.nameCandidates.length>1)?`<p class="excelhint">名称候補: ${namePlan.nameCandidates.map(esc).join('／')}</p>`:''}
        ${(namePlan.auxNameReasons&&namePlan.auxNameReasons.length)?`<p class="excelhint">${namePlan.auxNameReasons.map(esc).join(' ')}</p>`:''}
        ${mappings?`<ul class="sku-name-map">${mappings}</ul>`:''}`)
    const auxPanel=auxSupplierPanelHtml(st,g,d)
    const colspan=Number(opts.colspan)||SKU_COMPARE_COL_COUNT
    const decisionPending=!!(decision&&skuDecisionPendingRematch(st,g.code))
    return `<tr class="sku-detail-row" data-sku-detail="${esc(g.code)}"><td colspan="${colspan}">
      <div class="sku-detail-body">
        <p class="excelhint">顧客: ${esc(customers.join('、')||'—')}　サイズ: ${esc(sizes.join('、')||'—')}　CSV行: ${esc(rowNos||'—')}</p>
        ${decisionPending?`<p class="notice">${esc(pendingDecisionLabel(decision))} — 登録予定の再計算は「選択を反映して再照合」でまとめて行います。</p>`:''}
        ${namePlanBlock}
        ${isColorSplit?'':auxPanel}
        <div class="bar sku-decision-btns">
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="merge" class="${decision==='merge'?'primary':''}" ${st.busy?'disabled':''}>同じ商品としてまとめる</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="color_split" class="${decision==='color_split'?'primary':''}" ${st.busy?'disabled':''}>色別に分けて登録</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="separate" class="${decision==='separate'?'primary':''}" ${st.busy?'disabled':''}>別物のため個別に整理する</button>
          <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="hold" class="${decision==='hold'?'primary':''}" ${st.busy?'disabled':''}>今回は保留する</button>
          ${(g.makerCorrection&&g.makerCorrection.showButton)?`<button type="button" class="quiet" data-pl-sku-maker-correct="${esc(g.code)}" ${st.busy?'disabled':''}>メーカー情報で補正</button>`:''}
        </div>
        ${decision==='color_split'?colorSplitFormHtml(st,g,d):''}
        ${decision==='merge'?`<div class="sku-merge-form">
          ${decisionPending&&(namePlan.colorSplit||!(namePlan.nameMappings||[]).length)?'<p class="excelhint">統合用の名称予定は再照合後に設定できます。代表名・色・通常仕入先は今すぐ編集できます（方針切替では消しません）。</p>':''}
          ${nameColorSplitPreviewHtml(Object.assign({},g,g.auxPrimary||{},{productName:plannedNameForGroup(st,g),color:d.color||g.plannedRegisteredColor||g.plannedColor||''}))}
          <label data-plan-edit="name">公式商品名<select data-pl-official-name="${esc(g.code)}" ${st.busy?'disabled':''}><option value="">仮代表名を使う…</option>${nameOpts}</select></label>
          <label data-plan-edit="name">代表名（編集可）<input data-pl-official-name-edit="${esc(g.code)}" value="${esc(d.officialName||g.plannedRegisteredName||'')}" placeholder="空なら仮代表名（名称未整理ステータス）" ${st.busy?'disabled':''}></label>
          <label data-plan-edit="color">色<input data-pl-official-color="${esc(g.code)}" value="${esc(d.color||g.plannedRegisteredColor||g.plannedColor||(g.auxPrimary&&g.auxPrimary.plannedRegisteredColor)||'')}" ${st.busy?'disabled':''}></label>
          <label data-plan-edit="supplier">通常仕入先<select data-pl-primary-sup="${esc(g.code)}" ${st.busy?'disabled':''}><option value="">未指定</option>${supOpts}</select></label>
          ${d.primarySupplierManual?'<p class="excelhint">通常仕入先は手動選択です（初期在庫CSVの再読込でも上書きしません）。</p>':(g.auxPrimary&&g.auxPrimary.autoPrimarySupplier?`<p class="excelhint">通常仕入先は自動設定: ${esc(plannedSupplierDisplay(st,g))}</p>`:'')}
          <p class="excelhint">予定代表名: ${esc(plannedNameForGroup(st,g)||'—')}　予定色: ${esc(d.color||g.plannedRegisteredColor||g.plannedColor||'（空）')}　予定通常仕入先: ${esc(plannedSupplierDisplay(st,g))}</p>
          <fieldset><legend>残す商品（登録済が複数のとき必須）</legend>${existing}</fieldset>
          ${g.existingCount>1?`<fieldset><legend>統合する商品（残す以外）</legend>${mergeSources||'<span class="sub">残す商品を先に選んでください</span>'}</fieldset>`:''}
          <p class="excelhint">旧商品名・仕入先は照合用の別名として残します。既存の公式名は仮名では上書きしません。サイズの LL≡2L 同一化はしません。</p>
        </div>`:''}
        ${decision==='separate'?`<div class="sku-separate-form">
          <p class="excelhint">同じ通常品番のまま別商品として新規登録はできません。品番修正・部品・資材・加工サービス・品番なし・保留のいずれかを内容ごとに選んでください。</p>
          ${decisionPending?'<p class="excelhint">個別整理の内容は保持します。登録予定への反映は再照合後です。</p>':''}
          ${variantPlans}
        </div>`:''}
        ${decision==='hold'?`<div class="sku-hold-form">
          <label>保留理由<input data-pl-defer-reason="${esc(g.code)}" value="${esc(d.deferReason||'')}" placeholder="例: 制服本体と加工サービスの切り分けが未確定" ${st.busy?'disabled':''}></label>
          <p class="excelhint">この品番は今回登録しません。同じCSVを後で再読込して処理できます。${decisionPending?'【未反映】再照合で登録予定へ反映します。':''}</p>
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

  /** 個別整理「加工サービス」欄の初期値（検出加工名＋CSV仕入先）。編集状態のみ。 */
  function detectedProcessServiceForVariant(st,g,v){
    const rowNos=new Set((v&&v.rowNos||[]).map(Number))
    const rows=((st&&st.preview&&st.preview.rows)||[]).filter(r=>rowNos.has(Number(r.rowNo)))
    let serviceName=''
    let vendorName=''
    let supplement=''
    rows.forEach(r=>{
      const ps=r.processingService||{}
      const pc=r.parenClassification||{}
      const csv=r.csv||{}
      if(!serviceName){
        serviceName=ps.serviceName||pc.processing||v.processing||''
      }
      if(!vendorName){
        vendorName=ps.vendorName||pc.processingVendor||csv.supplier||v.supplier||''
      }
      if(!supplement){
        const specs=ps.rowSpecs||{}
        const bits=[specs.customerSpecific,specs.parenSpecs,specs.dimensions,specs.composition].filter(Boolean)
        supplement=bits.join('／')||pc.supplement||''
      }
    })
    if(!serviceName)serviceName=(v&&v.processing)||(v&&v.productName)||''
    if(!vendorName)vendorName=(v&&v.supplier)||''
    return {serviceName,vendorName,supplement}
  }

  /** 整理方法を加工サービスにしたとき、空欄だけ検出値で埋める（既存編集は消さない）。 */
  function initSeparateProcessServicePlan(st,code,key){
    const g=((st.preview&&st.preview.skuGroups)||[]).find(x=>x&&x.code===code)
    const v=((g&&g.variants)||[]).find(x=>x&&x.key===key)||{key}
    const d=skuDecision(st,code)
    const plans={...(d.plans||{})}
    const prev=plans[key]||{}
    const detected=detectedProcessServiceForVariant(st,g,v)
    plans[key]={
      ...prev,
      approach:'as_process_service',
      serviceName:String(prev.serviceName||'').trim()?prev.serviceName:detected.serviceName,
      vendorName:String(prev.vendorName||'').trim()?prev.vendorName:detected.vendorName,
      supplement:prev.supplement!=null?prev.supplement:detected.supplement
    }
    setSkuDecision(st,code,{plans})
  }

  function skuListRowsHtml(st,groups,opts){
    opts=opts||{}
    const section=opts.section||''
    const showApproveHint=!!opts.showApproveHint
    if(!(groups||[]).length){
      return `<tr><td colspan="${SKU_COMPARE_COL_COUNT}">該当する品番はありません。</td></tr>`
    }
    return groups.map(g=>{
      const d=skuDecision(st,g.code)
      const decision=d.decision||''
      const checked=!!st.selectedCodes[g.code]
      const expanded=!!st.expandedCodes[g.code]
      const tier=g.reviewTier||'individual'
      const isColorSplit=decision==='color_split'
      const stateLabel=decision
        ?(skuDecisionPendingRematch(st,g.code)?pendingDecisionLabel(decision):decisionLabel(decision))
        :(tier==='bulk'?'未承認（一括承認候補）':(TIER_LABELS[tier]||g.reviewTierLabel||'未決定'))
      // 統合リストと同じ項目別状態表示（色別中も仮代表名を確定値に見せない）
      const planItem={kind:'sku',code:g.code,targetLabel:g.codeDisplay||g.code,group:g,rows:[],rowNos:g.rowNos||[],issues:[]}
      return `<tr class="sku-list-row sku-tier-${esc(tier)} ${decision?`sku-dec-${esc(decision)}`:''}${skuDecisionPendingRematch(st,g.code)?' is-pending-rematch':''}" data-sku-code="${esc(g.code)}" data-sku-section="${esc(section)}" data-review-tier="${esc(tier)}" data-decision="${esc(decision||'')}">
        <td class="sku-check" data-label="選択"><input type="checkbox" data-pl-sku-check="${esc(g.code)}" ${checked?'checked':''} ${st.busy?'disabled':''}></td>
        <td class="sku-code" data-label="品番"><button type="button" class="linkish" data-pl-sku-expand="${esc(g.code)}">${esc(g.codeDisplay||g.code)}</button>
          <div class="sub">${esc(TIER_LABELS[tier]||g.reviewTierLabel||'')}</div></td>
        <td class="sku-names" data-label="商品名候補">${candidatesCell(g.nameCandidates||(g.variants||[]).map(v=>v.productName),g.nameCandidateDetails)}</td>
        <td class="sku-sups" data-label="仕入先候補">${candidatesCell(g.supplierCandidates||(g.variants||[]).map(v=>v.supplier),g.supplierCandidateDetails)}</td>
        <td class="sku-other" data-label="色・加工など">${otherDiffsCell(g)}</td>
        <td class="sku-why" data-label="要確認理由">${reasonCell(g)}</td>
        <td class="sku-plan" data-label="予定の代表名・通常仕入先">
          ${unifiedPlanHtml(st,planItem)}
          ${showApproveHint&&!decision?`<div class="sub">採用理由: ${(g.reviewReasons||[]).map(r=>esc(r.label||'')).filter(Boolean).join('／')||'名差・仕入先差のみ'}</div>`:''}
          ${!isColorSplit&&(g.sourceAdoptedName||(g.auxPrimary&&g.auxPrimary.sourceAdoptedName))?`<div class="sub">ソース名: ${esc(g.sourceAdoptedName||g.auxPrimary.sourceAdoptedName)}</div>`:''}
          ${!isColorSplit&&(g.changeSummary||(g.auxPrimary&&g.auxPrimary.changeSummary))?`<div class="sub sku-color-change">${esc(g.changeSummary||g.auxPrimary.changeSummary)}</div>`:''}
        </td>
        <td class="sku-action" data-label="操作">
          <div class="sku-action-label ${decision?`is-${esc(decision)}`:''}${skuDecisionPendingRematch(st,g.code)?' is-pending-rematch':''}">${esc(stateLabel)}</div>
          <div class="bar sku-mini-btns">
            <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="merge" class="${decision==='merge'?'primary':'quiet'}" ${st.busy?'disabled':''}>まとめ</button>
            <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="color_split" class="${decision==='color_split'?'primary':'quiet'}" ${st.busy?'disabled':''}>色別に分けて登録</button>
            <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="hold" class="${decision==='hold'?'primary':'quiet'}" ${st.busy?'disabled':''}>保留</button>
            ${(g.makerCorrection&&g.makerCorrection.showButton)?`<button type="button" class="quiet" data-pl-sku-maker-correct="${esc(g.code)}" ${st.busy?'disabled':''}>メーカー情報で補正</button>`:''}
            <button type="button" data-pl-sku-expand="${esc(g.code)}" class="quiet">${expanded?'詳細を閉じる':'詳細を開く'}</button>
          </div>
        </td>
      </tr>${skuMakerLookupRowHtml(st,g.code)}${expanded?skuRowDetailHtml(st,g):''}`
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

  function matchesUnifiedFilters(st,item){
    const q=(st.skuQ||'').trim().toLowerCase()
    const reason=st.skuReasonFilter||''
    if(reason==='bulk')return false
    if(reason==='merge'||reason==='hold')return false
    if(reason==='individual'&&item.kind!=='sku')return false
    if(reason==='pending'&&!(item.unresolvedIssueCount>0))return false
    if(reason==='name_wording'&&!(item.group&&item.group.nameWordingDiff))return false
    if(reason==='supplier'&&!(item.group&&item.group.supplierDiff))return false
    if(reason==='color'&&!(item.group&&item.group.colorDiffPossible))return false
    if(reason==='processing'&&!(item.group&&item.group.processingDiffPossible)&&item.kind!=='processing')return false
    if(reason==='kind'&&!(item.group&&item.group.kindDiffPossible))return false
    if(!q)return true
    const hay=[
      item.targetLabel,item.plannedName,item.plannedSupplier,item.plannedColor,item.code,
      (item.issues||[]).map(i=>i.label).join(' '),
      (item.rowNos||[]).join(' ')
    ].join(' ').toLowerCase()
    return hay.includes(q)
  }

  const PLAN_STATUS={
    AUTO:'自動決定済み',
    MANUAL:'手動確定済み',
    PROVISIONAL:'仮',
    NEED_CHOICE:'選択が必要',
    UNSET:'未設定',
    NA:'該当なし'
  }
  const PLAN_FIELD_LABELS={
    sku:'品番',
    name:'商品名',
    color:'色',
    supplier:'仕入先',
    processing:'必要な加工・仕様',
    procName:'加工名',
    procVendor:'加工業者',
    procSpec:'補足／個別仕様'
  }

  function planFieldKeyForReason(code){
    const c=String(code||'')
    if(c==='color')return 'color'
    if(c==='name_wording'||c==='existing_dup'||c==='compare')return 'name'
    if(c==='supplier')return 'supplier'
    if(c==='processing'||c==='kind'||c==='gender'||c==='fabric')return 'processing'
    return ''
  }

  function planFieldKeyForIssue(issue){
    if(!issue)return ''
    if(issue.fieldKey)return issue.fieldKey
    if(issue.type==='sku_reason')return planFieldKeyForReason(issue.code||'')
    if(issue.type==='processing_name')return 'procName'
    if(issue.type==='paren_confirm')return 'name'
    if(issue.type==='product_pick')return 'name'
    if(issue.type==='handle_pick')return 'supplier'
    if(issue.type==='category')return 'sku'
    return ''
  }

  function planStatusUnsettled(status){
    return status===PLAN_STATUS.PROVISIONAL
      ||status===PLAN_STATUS.NEED_CHOICE
      ||status===PLAN_STATUS.UNSET
  }

  function makePlanField(key,value,status,opts){
    opts=opts||{}
    const v=(value==null||value==='')?'':String(value)
    return {
      key,
      label:PLAN_FIELD_LABELS[key]||key,
      value:v,
      status:status||'',
      unsettled:planStatusUnsettled(status),
      hint:opts.hint||'',
      editHint:opts.editHint||''
    }
  }

  function uniqueNonEmpty(values){
    const out=[]
    ;(values||[]).forEach(v=>{
      const t=String(v==null?'':v).trim()
      if(t&&!out.includes(t))out.push(t)
    })
    return out
  }

  function sharedVariantProductName(g){
    const names=uniqueNonEmpty((g.variants||[]).map(v=>v.productName||v.productNameBase||''))
    if(names.length===1)return names[0]
    const planName=String((g.productPlan&&g.productPlan.productName)||'').trim()
    if(planName)return planName
    return ''
  }

  function variantColors(g){
    return uniqueNonEmpty((g.variants||[]).map(v=>v.color||''))
  }

  function variantProcessings(g){
    return uniqueNonEmpty((g.variants||[]).map(v=>v.processing||''))
  }

  function hasReasonCode(g,code){
    return (g.reviewReasons||[]).some(r=>(r.code||'')===code)
  }

  function skuNamePlanField(st,g,d){
    const aux=g.auxPrimary||{}
    const namePlan=g.namePlan||{}
    if(d.officialName){
      return makePlanField('name',d.officialName,PLAN_STATUS.MANUAL,{editHint:'詳細の代表名'})
    }
    const adopted=String(g.plannedRegisteredName||aux.plannedRegisteredName||'').trim()
    if(adopted&&!aux.officialNameNeedsChoice&&!namePlan.provisional){
      return makePlanField('name',adopted,PLAN_STATUS.AUTO,{
        hint:g.nameAdoptionRule||aux.nameAdoptionRule||namePlan.adoptionRule||'',
        editHint:'詳細の代表名'
      })
    }
    const shared=sharedVariantProductName(g)
    const colorOpen=!!(g.colorDiffPossible||hasReasonCode(g,'color'))
    const nameOpen=!!(
      namePlan.provisional
      ||g.provisionalName
      ||aux.officialNameNeedsChoice
      ||aux.pairNeedsChoice
      ||g.nameWordingDiff
      ||hasReasonCode(g,'name_wording')
    )
    // 色差があり共通ベース名だけ取れるときは、仮の「スクラブ ブルー」を確定値に見せない
    if(shared&&(colorOpen||nameOpen||!adopted)){
      if(colorOpen&&!g.nameWordingDiff){
        return makePlanField('name',shared,PLAN_STATUS.PROVISIONAL,{
          hint:'共通部分',
          editHint:'詳細の代表名／色別登録'
        })
      }
      if(g.nameWordingDiff&&!shared){
        const cands=uniqueNonEmpty(g.nameCandidates||[]).join('／')
        return makePlanField('name',cands||'—',PLAN_STATUS.NEED_CHOICE,{
          hint:'候補から選択',
          editHint:'詳細の公式商品名'
        })
      }
      if(namePlan.provisional||g.provisionalName||aux.officialNameNeedsChoice){
        return makePlanField('name',shared||namePlan.officialName||g.plannedOfficialName||'—',PLAN_STATUS.PROVISIONAL,{
          hint:shared?'共通部分':'仮代表名',
          editHint:'詳細の代表名'
        })
      }
      if(shared){
        return makePlanField('name',shared,PLAN_STATUS.AUTO,{
          hint:namePlan.adoptionRule||g.nameAdoptionRule||'共通部分',
          editHint:'詳細の代表名'
        })
      }
    }
    if(g.nameWordingDiff||aux.officialNameNeedsChoice||aux.pairNeedsChoice){
      const cands=uniqueNonEmpty([
        ...(g.nameCandidates||[]),
        ...((g.variants||[]).map(v=>v.productName||''))
      ]).join('／')
      return makePlanField('name',cands||namePlan.officialName||'—',PLAN_STATUS.NEED_CHOICE,{
        hint:'候補から選択',
        editHint:'詳細の公式商品名'
      })
    }
    if(namePlan.provisional||g.provisionalName){
      return makePlanField('name',namePlan.officialName||g.plannedOfficialName||'—',PLAN_STATUS.PROVISIONAL,{
        hint:'仮代表名',
        editHint:'詳細の代表名'
      })
    }
    const fallback=namePlan.officialName||g.plannedOfficialName||g.plannedRegisteredName||shared||''
    if(!fallback){
      return makePlanField('name','',PLAN_STATUS.UNSET,{editHint:'詳細の代表名'})
    }
    const autoRule=namePlan.adoptionRuleCode||aux.nameAdoptionRuleCode||''
    const auto=autoRule&&autoRule!=='csv_row_order'&&autoRule!=='fallback_code'
    return makePlanField('name',fallback,auto?PLAN_STATUS.AUTO:PLAN_STATUS.PROVISIONAL,{
      hint:namePlan.adoptionRule||g.nameAdoptionRule||'',
      editHint:'詳細の代表名'
    })
  }

  function skuColorPlanField(st,g,d){
    const aux=g.auxPrimary||{}
    if(d.color){
      return makePlanField('color',d.color,PLAN_STATUS.MANUAL,{editHint:'詳細の色'})
    }
    const adopted=String(
      g.plannedRegisteredColor||g.plannedColor||aux.plannedRegisteredColor||aux.adoptedColor||''
    ).trim()
    const colors=variantColors(g)
    const colorOpen=!!(g.colorDiffPossible||hasReasonCode(g,'color'))
    if(colorOpen){
      return makePlanField('color',colors.join('／')||(g.reviewReasons||[])
        .filter(r=>r.code==='color')
        .flatMap(r=>r.values||[])
        .join('／')||'—',PLAN_STATUS.NEED_CHOICE,{
        hint:colors.length>1?'候補':'色の確認',
        editHint:'詳細の色／色別に分けて登録'
      })
    }
    if(adopted){
      return makePlanField('color',adopted,PLAN_STATUS.AUTO,{editHint:'詳細の色'})
    }
    if(colors.length===1){
      return makePlanField('color',colors[0],PLAN_STATUS.AUTO,{editHint:'詳細の色'})
    }
    if(colors.length>1){
      return makePlanField('color',colors.join('／'),PLAN_STATUS.NEED_CHOICE,{
        editHint:'詳細の色／色別に分けて登録'
      })
    }
    // 色情報なし＝この商品では色欄が不要（情報不足の未設定と区別）
    return makePlanField('color','',PLAN_STATUS.NA)
  }

  function skuSupplierPlanField(st,g,d){
    const aux=g.auxPrimary||{}
    const value=plannedSupplierForGroup(st,g)
    const reason=plannedSupplierReasonForGroup(st,g)
    if(d.primarySupplierManual&&d.primarySupplier){
      return makePlanField('supplier',d.primarySupplier,PLAN_STATUS.MANUAL,{
        hint:'手動選択',
        editHint:'詳細の通常仕入先'
      })
    }
    if(g.supplierDiff||hasReasonCode(g,'supplier')||aux.needsManualPrimary){
      const cands=uniqueNonEmpty(g.supplierCandidates||[]).join('／')
      return makePlanField('supplier',value||cands||'',value?PLAN_STATUS.NEED_CHOICE:PLAN_STATUS.NEED_CHOICE,{
        hint:aux.manualReason||(cands?'候補から選択':''),
        editHint:'詳細の通常仕入先'
      })
    }
    if(value){
      const auto=!!(
        aux.singleCandidateAuto
        ||aux.autoPrimarySupplier
        ||g.auxStockPickAuto
        ||reason
      )
      return makePlanField('supplier',value,auto?PLAN_STATUS.AUTO:PLAN_STATUS.MANUAL,{
        hint:reason||aux.autoReason||'',
        editHint:'詳細の通常仕入先'
      })
    }
    const cands=uniqueNonEmpty(g.supplierCandidates||[])
    if(cands.length){
      return makePlanField('supplier',cands.join('／'),PLAN_STATUS.NEED_CHOICE,{
        editHint:'詳細の通常仕入先'
      })
    }
    return makePlanField('supplier','',PLAN_STATUS.UNSET,{
      hint:'仕入先情報が不足',
      editHint:'詳細の通常仕入先'
    })
  }

  function skuProcessingPlanField(st,g,d){
    const procs=variantProcessings(g)
    if(g.processingDiffPossible||hasReasonCode(g,'processing')){
      return makePlanField('processing',procs.join('／')||'—',PLAN_STATUS.NEED_CHOICE,{
        editHint:'詳細の色・加工など'
      })
    }
    if(procs.length===1){
      return makePlanField('processing',procs[0],PLAN_STATUS.AUTO)
    }
    if(procs.length>1){
      return makePlanField('processing',procs.join('／'),PLAN_STATUS.NEED_CHOICE,{
        editHint:'詳細'
      })
    }
    return makePlanField('processing','',PLAN_STATUS.NA)
  }

  function colorSplitPlanFields(st,g,d){
    const fields=[]
    fields.push(makePlanField('sku',g.codeDisplay||g.code||'',PLAN_STATUS.AUTO,{hint:'色別登録中'}))
    const split=g.colorSplitPlans||{}
    const plans=split.plans||[]
    const unassigned=split.unassignedRows||[]
    if(!plans.length){
      fields.push(makePlanField('name','',PLAN_STATUS.NEED_CHOICE,{
        hint:'色別の登録先未作成',
        editHint:'詳細の色別に分けて登録'
      }))
      fields.push(makePlanField('color','',PLAN_STATUS.NEED_CHOICE,{editHint:'詳細の色別に分けて登録'}))
    }else{
      // 統合用の仮代表名は出さない。色ごとの予定だけを並べる
      const nameBits=plans.map(p=>{
        const edited=d.colorPlans&&d.colorPlans[p.colorKey]
        const name=(edited&&edited.productName!=null&&edited.productName!=='')?edited.productName:(p.productName||'')
        return `${name||'—'}（${p.color||'?'}）`
      })
      const anyManualName=plans.some(p=>{
        const edited=d.colorPlans&&d.colorPlans[p.colorKey]
        return edited&&edited.productName!=null&&String(edited.productName)!==''&&String(edited.productName)!==String(p.productName||'')
      })
      const nameStatus=unassigned.length||!split.complete
        ?PLAN_STATUS.NEED_CHOICE
        :(anyManualName?PLAN_STATUS.MANUAL:PLAN_STATUS.AUTO)
      fields.push(makePlanField('name',nameBits.join('／'),nameStatus,{
        hint:'色別の各登録予定',
        editHint:'詳細の色別フォーム'
      }))
      const colorBits=plans.map(p=>p.color||'').filter(Boolean)
      const collision=plans.some(p=>p.needsCollisionChoice)
      const colorStatus=(unassigned.length||collision||!split.complete)
        ?PLAN_STATUS.NEED_CHOICE
        :PLAN_STATUS.AUTO
      fields.push(makePlanField('color',colorBits.join('／'),colorStatus,{
        hint:unassigned.length?`行き先未定 ${unassigned.length} 行`:(collision?'品番衝突の解消が必要':''),
        editHint:'詳細の色別フォーム'
      }))
    }
    fields.push(skuSupplierPlanField(st,g,d))
    fields.push(skuProcessingPlanField(st,g,d))
    return fields
  }

  function skuPlannedStatusFields(st,item){
    const g=item.group
    if(!g)return []
    const d=skuDecision(st,g.code)
    if((d.decision||'')==='color_split'){
      return colorSplitPlanFields(st,g,d)
    }
    return [
      makePlanField('sku',g.codeDisplay||g.code||item.targetLabel||'',PLAN_STATUS.AUTO),
      skuNamePlanField(st,g,d),
      skuColorPlanField(st,g,d),
      skuSupplierPlanField(st,g,d),
      skuProcessingPlanField(st,g,d)
    ]
  }

  function processingPlannedStatusFields(st,item){
    const rows=item.rows||[]
    const sample=rows[0]||{}
    const csv=sample.csv||{}
    const ps=sample.processingService||{}
    const pc=sample.parenClassification||{}
    const edit=(st.rowClassifications&&st.rowClassifications[String(sample.rowNo)])||{}
    const name=String(edit.processing!=null?edit.processing:(edit.plannedName!=null?edit.plannedName:(ps.serviceName||pc.processing||''))).trim()
    const vendor=String(ps.vendorName||csv.supplier||item.plannedSupplier||'').trim()
    const supplement=String(
      edit.supplement!=null?edit.supplement:(pc.supplement||sample.plannedRegisteredSupplement||'')
    ).trim()
    const specBits=uniqueNonEmpty((rows||[]).flatMap(r=>{
      const raw=(r.processingService&&r.processingService.rowSpecs)||[]
      const list=Array.isArray(raw)?raw:(raw&&typeof raw==='object'?[raw]:[])
      return list.map(x=>[
        x.customerSpecific,x.position,x.dimensions,x.composition,x.originalName
      ].filter(Boolean).join(' / '))
    }))
    const specText=supplement||specBits.slice(0,3).join('／')
    const needsName=!!(ps.needsConfirm||pc.needsConfirm||!name)
    const nameManual=edit.processing!=null||(edit.plannedName!=null&&edit.userEdited)
    const nameStatus=nameManual
      ?PLAN_STATUS.MANUAL
      :(needsName?(name?PLAN_STATUS.NEED_CHOICE:PLAN_STATUS.UNSET):(name?PLAN_STATUS.AUTO:PLAN_STATUS.UNSET))
    const vendorStatus=vendor
      ?(edit.vendor!=null?PLAN_STATUS.MANUAL:PLAN_STATUS.AUTO)
      :PLAN_STATUS.UNSET
    let specStatus=PLAN_STATUS.NA
    if(specText)specStatus=edit.supplement!=null?PLAN_STATUS.MANUAL:PLAN_STATUS.AUTO
    else if(ps.missingFields&&ps.missingFields.length)specStatus=PLAN_STATUS.UNSET
    return [
      makePlanField('procName',name,nameStatus,{editHint:'詳細の加工名'}),
      makePlanField('procVendor',vendor,vendorStatus,{editHint:'詳細'}),
      makePlanField('procSpec',specText,specStatus,{
        hint:specBits.length>3?'他は詳細へ':'',
        editHint:'詳細の補足／個別仕様'
      })
    ]
  }

  function rowPlannedStatusFields(st,item){
    const rows=item.rows||[]
    const sample=rows[0]||{}
    const csv=sample.csv||{}
    const pc=sample.parenClassification||{}
    const edit=(st.rowClassifications&&st.rowClassifications[String(sample.rowNo)])||{}
    const code=item.code||csv.code||''
    const name=edit.plannedName!=null?edit.plannedName:(pc.plannedName||csv.productName||item.plannedName||'')
    const color=edit.color!=null?edit.color:(pc.color||item.plannedColor||'')
    const supplier=csv.supplier||item.plannedSupplier||''
    const nameNeed=!!(pc.needsConfirm||sample.parenNeedsConfirm||(item.issues||[]).some(i=>!i.resolved&&(i.type==='paren_confirm'||i.type==='product_pick')))
    const colorCands=uniqueNonEmpty(pc.colorCandidates||[])
    const colorNeed=colorCands.length>1||!!(pc.needsConfirm&&!color&&colorCands.length)
    const fields=[]
    if(code){
      fields.push(makePlanField('sku',csv.codeDisplay||code,PLAN_STATUS.AUTO))
    }
    fields.push(makePlanField(
      'name',
      name||(nameNeed?(uniqueNonEmpty(pc.nameCandidates||[csv.productName]).join('／')):''),
      edit.plannedName!=null?PLAN_STATUS.MANUAL:(nameNeed?PLAN_STATUS.NEED_CHOICE:(name?PLAN_STATUS.AUTO:PLAN_STATUS.UNSET)),
      {editHint:'詳細の商品名／名称／加工名'}
    ))
    if(colorNeed||color||colorCands.length){
      fields.push(makePlanField(
        'color',
        color||colorCands.join('／'),
        edit.color!=null?PLAN_STATUS.MANUAL:(colorNeed||colorCands.length>1?PLAN_STATUS.NEED_CHOICE:(color?PLAN_STATUS.AUTO:PLAN_STATUS.UNSET)),
        {editHint:'詳細の色'}
      ))
    }else{
      fields.push(makePlanField('color','',PLAN_STATUS.NA))
    }
    fields.push(makePlanField(
      'supplier',
      supplier,
      supplier?PLAN_STATUS.AUTO:PLAN_STATUS.UNSET,
      {hint:supplier?'':'仕入先情報が不足'}
    ))
    fields.push(makePlanField('processing','',PLAN_STATUS.NA))
    return fields
  }

  function buildPlannedStatusFields(st,item){
    if(!item)return []
    if(item.kind==='sku')return skuPlannedStatusFields(st,item)
    if(item.kind==='processing')return processingPlannedStatusFields(st,item)
    return rowPlannedStatusFields(st,item)
  }

  function planFieldStatusClass(status){
    if(status===PLAN_STATUS.NEED_CHOICE)return 'is-need-choice'
    if(status===PLAN_STATUS.PROVISIONAL)return 'is-provisional'
    if(status===PLAN_STATUS.UNSET)return 'is-unset'
    if(status===PLAN_STATUS.AUTO)return 'is-auto'
    if(status===PLAN_STATUS.MANUAL)return 'is-manual'
    if(status===PLAN_STATUS.NA)return 'is-na'
    return ''
  }

  function renderPlanFieldHtml(field){
    const status=field.status||''
    const cls=['sku-plan-field',planFieldStatusClass(status)]
    if(field.unsettled)cls.push('is-unsettled')
    const valueText=field.value||'—'
    const statusBit=status?`<span class="sku-plan-status">【${esc(status)}】</span>`:''
    const hintBit=field.hint?`<span class="sku-plan-hint">（${esc(field.hint)}）</span>`:''
    const editBit=(field.unsettled&&field.editHint)
      ?`<span class="sku-plan-edit-hint">${esc(field.editHint)}</span>`:''
    return `<div class="${cls.join(' ')}" data-plan-field="${esc(field.key)}">
      <span class="sku-plan-label">${esc(field.label)}</span>
      <span class="sku-plan-main">
        <span class="sku-plan-value">${esc(valueText)}</span>
        ${statusBit}${hintBit}${editBit}
      </span>
    </div>`
  }

  function unifiedIssuesHtml(item){
    const open=(item.issues||[]).filter(i=>!i.resolved)
    if(!open.length)return '<span class="sub">—</span>'
    return `<ul class="sku-reason-list unified-issue-list">${open.map(i=>{
      const fieldKey=planFieldKeyForIssue(i)
      const fieldLab=fieldKey?(PLAN_FIELD_LABELS[fieldKey]||fieldKey):''
      const det=i.detail?`（${esc(i.detail)}）`:''
      const mapBit=fieldLab?`<span class="unified-issue-field">→ ${esc(fieldLab)}</span>`:''
      return `<li data-issue-field="${esc(fieldKey||'')}">${esc(i.label||'')}${det}${mapBit}</li>`
    }).join('')}</ul>`
  }

  function unifiedPlanHtml(st,item){
    const fields=buildPlannedStatusFields(st,item)
    if(!fields.length){
      return `<div class="sku-plan-fields"><div class="sub">—</div></div>`
    }
    const unsettled=fields.filter(f=>f.unsettled)
    const head=unsettled.length
      ?`<div class="sku-plan-summary is-unsettled">未確定 ${unsettled.length} 項目（登録確定前）</div>`
      :`<div class="sku-plan-summary">予定は揃っています（まだ未登録）</div>`
    return `<div class="sku-plan-fields">${head}${fields.map(renderPlanFieldHtml).join('')}</div>`
  }

  function unifiedRowDetailHtml(st,item){
    let rows=item.rows||[]
    // 品番対象でも元行カードを出し、行単位の「登録しない」を使えるようにする
    if(!rows.length&&item.kind==='sku'&&item.group){
      const code=String(item.code||item.group.code||'')
      rows=((st.preview&&st.preview.rows)||[]).filter(r=>String((r.csv||{}).code||'')===code)
    }
    if(!rows.length)return ''
    const body=rows.map(r=>{
      const csv=r.csv||{}
      const excluded=isRowExcluded(st,r)
      return `<div class="unified-row-detail-card${excluded?' is-row-excluded':''}" data-unified-row="${r.rowNo}">
        <p class="excelhint">行 ${r.rowNo}　顧客 ${esc(csv.customer||'—')}　サイズ ${esc(csv.size||'—')}　原文 ${esc(csv.productName||'—')}　仕入先 ${esc(csv.supplier||'—')}${excluded?'　<span class="badge">登録しない</span>':''}</p>
        <div class="unified-row-detail-grid">
          <div>${parenCell(st,r)}</div>
          <div><p class="excelhint">登録先: ${targetCell(r)}</p><div class="bar">${rowActions(st,r)}</div></div>
        </div>
        <p class="excelhint">${esc((r.reasons||[]).join(' ')||'')}</p>
      </div>`
    }).join('')
    const rowNos=(item.rowNos||[]).length
      ?`<p class="excelhint">元CSV行: ${(item.rowNos||[]).join(', ')}</p>`:''
    return `<tr class="sku-detail-row unified-detail-row" data-unified-detail="${esc(item.id)}"><td colspan="${UNIFIED_REVIEW_COL_COUNT}">
      <div class="sku-detail-body">
        <p class="excelhint">対象内の行だけを編集します。品番単位の操作で別行の個別仕様を一律上書きしません。行ごとの「この行を登録しない」は品番の「保留」とは別です。登録予定の未確定項目は下の入力欄で選択・編集してください（まだ未登録）。</p>
        ${rowNos}
        ${body}
      </div>
    </td></tr>`
  }

  function unifiedListRowsHtml(st,items){
    if(!(items||[]).length){
      return `<tr><td colspan="${UNIFIED_REVIEW_COL_COUNT}">未解決の確認対象はありません。</td></tr>`
    }
    const detailOpts={colspan:UNIFIED_REVIEW_COL_COUNT}
    return items.map(item=>{
      const expanded=!!(st.expandedUnified&&st.expandedUnified[item.id])
      if(item.kind==='sku'&&item.group){
        // 既存の品番操作を統合リストから利用（チェック・まとめ／色別／保留／メーカー／詳細）
        const g=item.group
        const d=skuDecision(st,g.code)
        const decision=d.decision||''
        const checked=!!st.selectedCodes[g.code]
        const skuExpanded=!!st.expandedCodes[g.code]
        const pendingDec=!!(decision&&skuDecisionPendingRematch(st,g.code))
        return `<tr class="sku-list-row unified-list-row sku-tier-individual${pendingDec?' is-pending-rematch':''}" data-unified-id="${esc(item.id)}" data-sku-code="${esc(g.code)}" data-sku-section="unified" data-review-tier="individual" data-decision="${esc(decision||'')}">
          <td class="sku-check" data-label="選択"><input type="checkbox" data-pl-sku-check="${esc(g.code)}" ${checked?'checked':''} ${st.busy?'disabled':''}></td>
          <td class="sku-code" data-label="対象"><button type="button" class="linkish" data-pl-sku-expand="${esc(g.code)}">${esc(item.targetLabel)}</button>
            <div class="sub">品番単位</div></td>
          <td class="sku-plan" data-label="登録予定">${unifiedPlanHtml(st,item)}</td>
          <td class="sku-why" data-label="残っている確認事項">${unifiedIssuesHtml(item)}</td>
          <td class="sku-action" data-label="操作">
            <div class="sku-action-label${pendingDec?' is-pending-rematch':''}">${esc(decision
              ?(pendingDec?pendingDecisionLabel(decision):(stillProblematicAfterRematch(st,g)?`${chosenActionLabel(decision)}（残課題あり）`:decisionLabel(decision)))
              :'未決定')}</div>
            <div class="bar sku-mini-btns">
              <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="merge" class="${decision==='merge'?'primary':'quiet'}" ${st.busy?'disabled':''}>まとめる</button>
              <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="color_split" class="${decision==='color_split'?'primary':'quiet'}" ${st.busy?'disabled':''}>色別に分けて登録</button>
              <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="separate" class="${decision==='separate'?'primary':'quiet'}" ${st.busy?'disabled':''}>別物のため個別に整理</button>
              <button type="button" data-pl-sku-dec="${esc(g.code)}" data-dec="hold" class="${decision==='hold'?'primary':'quiet'}" ${st.busy?'disabled':''}>保留</button>
              ${(g.makerCorrection&&g.makerCorrection.showButton)?`<button type="button" class="quiet" data-pl-sku-maker-correct="${esc(g.code)}" ${st.busy?'disabled':''}>メーカー情報で補正</button>`:''}
              <button type="button" data-pl-sku-expand="${esc(g.code)}" class="quiet">${skuExpanded?'詳細を閉じる':'詳細を開く'}</button>
            </div>
            ${skipRegisterActionsHtml(st,item)}
          </td>
        </tr>${skuMakerLookupRowHtml(st,g.code,detailOpts)}${skuExpanded?skuRowDetailHtml(st,g,detailOpts):''}${skuExpanded?unifiedRowDetailHtml(st,item):''}`
      }
      const kindLabel={processing:'加工サービス',blank:'品番なし・部品資材',custom:'別注',unknown:'不明',sku_row:'行要確認',row:'行要確認'}[item.kind]||'要確認'
      return `<tr class="sku-list-row unified-list-row unified-kind-${esc(item.kind)}" data-unified-id="${esc(item.id)}" data-sku-section="unified">
        <td class="sku-check" data-label="選択"><span class="sub">—</span></td>
        <td class="sku-code" data-label="対象"><button type="button" class="linkish" data-pl-unified-expand="${esc(item.id)}">${esc(item.targetLabel)}</button>
          <div class="sub">${esc(kindLabel)}</div></td>
        <td class="sku-plan" data-label="登録予定">${unifiedPlanHtml(st,item)}</td>
        <td class="sku-why" data-label="残っている確認事項">${unifiedIssuesHtml(item)}</td>
        <td class="sku-action" data-label="操作">
          <div class="sku-action-label">要確認</div>
          <div class="bar sku-mini-btns">
            ${item.rows&&item.rows[0]&&(item.rows[0].makerCorrection||{}).eligible&&(item.rows[0].makerCorrection||{}).makerSku
              ?`<button type="button" class="quiet" data-pl-maker-correct="${item.rows[0].rowNo}" ${st.busy?'disabled':''}>メーカー情報で補正</button>`:''}
            <button type="button" class="quiet" data-pl-unified-expand="${esc(item.id)}">${expanded?'詳細を閉じる':'詳細を開く'}</button>
          </div>
          ${skipRegisterActionsHtml(st,item)}
        </td>
      </tr>${expanded?unifiedRowDetailHtml(st,item):''}`
    }).join('')
  }

  function unifiedCompareTableHtml(st,items){
    return `<div class="sku-compare-wrap unified-review-wrap"><table class="sku-compare-table unified-review-table">
      <thead><tr>
        <th></th><th>対象</th><th>登録予定</th><th>残っている確認事項</th><th class="sku-action-h">操作</th>
      </tr></thead>
      <tbody>${unifiedListRowsHtml(st,items)}</tbody>
    </table></div>`
  }

  function sourceRowsPanelHtml(st){
    const open=!!(st.sectionOpen&&st.sectionOpen.sourceRows)
    const review=(st.preview&&st.preview.reviewCount)||0
    return `<section class="sku-judgment-section sku-section-source-rows" data-sku-section-block="sourceRows">
      <header class="sku-section-header bar">
        <h4>元行一覧（詳細確認用） <span class="sub">確認作業の第二リストではありません</span></h4>
        ${sectionToggleBtn(st,'sourceRows',open?'閉じる':'開く')}
      </header>
      ${open?`<div class="sku-section-body">
        <p class="excelhint">顧客・サイズ・行番号・原文・行ごとの仕様／登録先はここか、確認リストの「詳細を開く」で確認してください。通常の未解決処理は上の確認リストだけで完結します。</p>
        <div class="bar product-ledger-preview-actions">
          <input type="search" id="plQ" placeholder="検索（顧客・品番・商品名・理由）" value="${esc(st.q||'')}">
          <select id="plStatus">
            <option value="">すべて</option>
            ${STATUS_ORDER.map(k=>`<option value="${k}" ${st.statusFilter===k?'selected':''}>${STATUS_LABELS[k]}</option>`).join('')}
          </select>
          <span class="excelhint">行の要確認 ${review} 件（参考。未解決件数は確認リスト側）</span>
        </div>
        ${tableHtml(st)}
      </div>`:`<p class="excelhint sku-section-collapsed-hint">折りたたみ中。確認リストの操作後に行の対応を見たいときだけ開いてください。</p>`}
    </section>`
  }

  function originalNamesForGroup(g){
    const fromVariants=(g.variants||[]).map(v=>v.productName).filter(Boolean)
    const fromCands=g.nameCandidates||[]
    const uniq=[]
    ;[...fromVariants,...fromCands].forEach(n=>{
      const t=String(n||'').trim()
      if(t&&!uniq.includes(t))uniq.push(t)
    })
    return uniq
  }

  function sizesForColorPlan(st,g,plan){
    const rowNos=new Set((plan.rowNos||[]).map(Number))
    const sizes=[]
    const add=(s)=>{
      const t=String(s||'').trim()
      if(t&&!sizes.includes(t))sizes.push(t)
    }
    ;((st.preview&&st.preview.rows)||[]).forEach(r=>{
      if(rowNos.has(Number(r.rowNo)))add((r.csv||{}).size)
    })
    if(sizes.length)return sizes
    ;(g.variants||[]).forEach(v=>{
      const overlap=(v.rowNos||[]).some(rn=>rowNos.has(Number(rn)))
      if(overlap)(v.sizes||[]).forEach(add)
    })
    return sizes
  }

  function matchesConfirmedFilters(st,g){
    const q=(st.confirmedQ!=null&&st.confirmedQ!=='')?String(st.confirmedQ).trim().toLowerCase():(st.skuQ||'').trim().toLowerCase()
    if(!q)return true
    const dec=effectiveDecision(st,g)
    const status=confirmedItemStatusLabel(st,g)
    const hay=[
      g.code,g.codeDisplay,
      ...(originalNamesForGroup(g)),
      chosenActionLabel(dec),dec,status,
      plannedNameForGroup(st,g),
      plannedSupplierForGroup(st,g),
      g.plannedRegisteredColor||g.plannedColor||'',
      ...(((g.colorSplitPlans||{}).plans)||[]).flatMap(p=>[p.internalCode,p.makerCode,p.productName,p.color])
    ].join(' ').toLowerCase()
    return hay.includes(q)
  }

  function colorSplitConfirmedDetailHtml(st,g){
    const split=g.colorSplitPlans||{}
    const plans=split.plans||[]
    if(skuDecisionPendingRematch(st,g.code)&&!plans.length){
      return `<p class="excelhint">色別の各商品（社内管理品番・メーカー品番・名・色・仕入先・サイズ）は再照合後に表示します。</p>`
    }
    if(!plans.length){
      return `<p class="notice">色別の登録先がまだありません。確認リスト側の残課題を解消してください。</p>`
    }
    const supplier=plannedSupplierForGroup(st,g)||'未設定'
    const rows=plans.map(p=>{
      const sizes=sizesForColorPlan(st,g,p)
      return `<tr>
        <td>${esc(p.internalCode||p.suggestedInternalCode||'—')}</td>
        <td>${esc(p.makerCode||g.code||'—')}</td>
        <td>${esc(p.productName||'—')}</td>
        <td>${esc(p.color||'—')}</td>
        <td>${esc(supplier)}</td>
        <td>${esc(sizes.join('、')||'—')}</td>
      </tr>`
    }).join('')
    return `<div class="confirmed-color-split-detail">
      <p class="excelhint">色別に分けた各商品の登録予定（代表名だけではなく分割後の全件）:</p>
      <div class="legacy-csv-table-wrap"><table class="legacy-csv-table confirmed-split-table">
        <thead><tr><th>社内管理品番</th><th>メーカー品番</th><th>商品名</th><th>色</th><th>通常仕入先</th><th>サイズ</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </div>`
  }

  function separateProcessServiceConfirmedDetailHtml(st,g){
    const plans=(skuDecision(st,g.code).plans)||{}
    const remote=g.separateProcessServicePlans||[]
    const rows=(g.variants||[]).map(v=>{
      const p=plans[v.key]||{}
      if(p.approach!=='as_process_service')return ''
      const detected=detectedProcessServiceForVariant(st,g,v)
      const remoteHit=remote.find(r=>String(r.variantKey||'')===String(v.key||''))||{}
      const name=p.serviceName||remoteHit.serviceName||detected.serviceName||'—'
      const vendor=p.vendorName||remoteHit.vendorName||detected.vendorName||'—'
      const cat=remoteHit.category||'加工サービス'
      const action=remoteHit.action==='reuse'?'再利用':'新規'
      return `<tr>
        <td>${esc(v.productName||'—')}</td>
        <td>${esc(name)}</td>
        <td>${esc(vendor)}</td>
        <td>${esc(cat)}</td>
        <td>${esc(action)}</td>
      </tr>`
    }).filter(Boolean).join('')
    if(!rows)return ''
    return `<div class="confirmed-process-service-detail">
      <p class="excelhint">加工サービスとして整理する登録予定（名・業者・区分）:</p>
      <div class="legacy-csv-table-wrap"><table class="legacy-csv-table confirmed-split-table">
        <thead><tr><th>CSV内容</th><th>加工名（登録・表示）</th><th>加工業者</th><th>区分</th><th>予定</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </div>`
  }

  function confirmedDetailHtml(st,g){
    const dec=effectiveDecision(st,g)
    const expanded=!!(st.expandedConfirmed&&st.expandedConfirmed[g.code])
    if(!expanded)return ''
    // skuRowDetailHtml が独立した <tr> を返すので、色別／加工サービスの各商品表だけ先に1行足す
    const processIntro=dec==='separate'?separateProcessServiceConfirmedDetailHtml(st,g):''
    const splitIntro=dec==='color_split'
      ?`<tr class="sku-detail-row confirmed-split-row" data-confirmed-detail="${esc(g.code)}"><td colspan="${CONFIRMED_LIST_COL_COUNT}">
          <div class="sku-detail-body confirmed-detail-body">
            <p class="excelhint">登録予定の確認です。まだ台帳へ保存していません（確認済み ≠ 登録完了）。</p>
            ${colorSplitConfirmedDetailHtml(st,g)}
          </div>
        </td></tr>`
      :`<tr class="sku-detail-row confirmed-split-row" data-confirmed-detail="${esc(g.code)}"><td colspan="${CONFIRMED_LIST_COL_COUNT}">
          <div class="sku-detail-body confirmed-detail-body">
            <p class="excelhint">登録予定の確認です。まだ台帳へ保存していません（確認済み ≠ 登録完了）。</p>
            ${processIntro}
          </div>
        </td></tr>`
    // 対応変更用に既存の詳細フォーム（方針ボタン・色別／個別フォーム）も続ける
    return splitIntro+skuRowDetailHtml(st,g,{colspan:CONFIRMED_LIST_COL_COUNT})
  }

  function confirmedListRowsHtml(st,groups){
    if(!(groups||[]).length){
      return `<tr><td colspan="${CONFIRMED_LIST_COL_COUNT}">確認済みの品番はありません。</td></tr>`
    }
    return groups.map(g=>{
      const dec=effectiveDecision(st,g)
      const statusKey=confirmedItemStatusKey(st,g)
      const status=CONFIRMED_STATUS[statusKey]
      const expanded=!!(st.expandedConfirmed&&st.expandedConfirmed[g.code])
      const originals=originalNamesForGroup(g)
      const sepPlans=(skuDecision(st,g.code).plans)||{}
      const procVariants=(dec==='separate'?(g.variants||[]):[]).filter(v=>(sepPlans[v.key]||{}).approach==='as_process_service')
      const planName=dec==='color_split'
        ?colorSplitSummaryForGroup(g)
        :(procVariants.length
          ?procVariants.map(v=>{
            const p=sepPlans[v.key]||{}
            const det=detectedProcessServiceForVariant(st,g,v)
            return p.serviceName||det.serviceName||v.productName||''
          }).filter(Boolean).join('／')
          :plannedNameForGroup(st,g))
      const planColor=dec==='color_split'
        ?(((g.colorSplitPlans||{}).plans)||[]).map(p=>p.color).filter(Boolean).join('・')
        :(skuDecision(st,g.code).color||g.plannedRegisteredColor||g.plannedColor||'')
      const planSup=procVariants.length
        ?procVariants.map(v=>{
          const p=sepPlans[v.key]||{}
          const det=detectedProcessServiceForVariant(st,g,v)
          return p.vendorName||det.vendorName||v.supplier||''
        }).filter(Boolean).join('／')||plannedSupplierDisplay(st,g)
        :plannedSupplierDisplay(st,g)
      return `<tr class="sku-list-row confirmed-list-row is-status-${esc(statusKey)}" data-sku-code="${esc(g.code)}" data-sku-section="confirmed" data-decision="${esc(dec||'')}" data-confirmed-status="${esc(statusKey)}">
        <td class="confirmed-sku" data-label="元の品番">${esc(g.codeDisplay||g.code||'—')}</td>
        <td class="confirmed-orig-name" data-label="元の商品名">${esc(originals.join('／')||'—')}</td>
        <td class="confirmed-action" data-label="選んだ対応">${esc(chosenActionLabel(dec))}</td>
        <td class="confirmed-plan" data-label="登録予定">
          <div>名: ${esc(planName||'—')}</div>
          <div>色: ${esc(planColor||'（空）')}</div>
          <div>${procVariants.length?'加工業者':'通常仕入先'}: ${esc(planSup)}</div>
          ${procVariants.length?`<div class="sub">区分: 加工サービス（${procVariants.length} 件・詳細で名・業者を表示）</div>`:''}
          ${dec==='color_split'?`<div class="sub">分割 ${(((g.colorSplitPlans||{}).plans)||[]).length||0} 色（詳細で各商品を表示）</div>`:''}
        </td>
        <td class="confirmed-status" data-label="状態"><span class="badge confirmed-status-badge is-${esc(statusKey)}">${esc(status)}</span></td>
        <td class="confirmed-ops" data-label="操作" colspan="2">
          <div class="bar sku-mini-btns">
            <button type="button" class="quiet" data-pl-confirmed-expand="${esc(g.code)}">${expanded?'詳細を閉じる':'詳細を開く'}</button>
            <button type="button" class="quiet" data-pl-confirmed-change="${esc(g.code)}" ${st.busy?'disabled':''}>対応を変更する</button>
          </div>
        </td>
      </tr>${confirmedDetailHtml(st,g)}`
    }).join('')
  }

  function confirmedCompareTableHtml(st,groups){
    return `<div class="sku-compare-wrap confirmed-review-wrap"><table class="sku-compare-table confirmed-review-table">
      <thead><tr>
        <th>元の品番</th><th>元の商品名</th><th>選んだ対応</th><th>登録予定（名・色・通常仕入先）</th><th>状態</th><th colspan="2" class="sku-action-h">操作</th>
      </tr></thead>
      <tbody>${confirmedListRowsHtml(st,groups)}</tbody>
    </table></div>`
  }

  function confirmedSectionHtml(st,part,counts){
    const confirmedAll=(part.confirmed||[]).filter(g=>matchesConfirmedFilters(st,g))
    const slice=pageSlice(st,'confirmed',confirmedAll)
    const open=!!(st.sectionOpen&&st.sectionOpen.confirmed)
    const body=open?`<div class="sku-section-body">
      <p class="excelhint">方針を選び必須入力が揃った品番です。再照合前は「反映待ち」、再照合後に問題がなければ「確認済み」です。自動決定済み・保留・登録しない項目はここへ混ぜません。最終の台帳保存は下の「確認して商品を登録」です（確認済み ≠ 登録完了）。</p>
      <div class="bar sku-list-toolbar confirmed-toolbar">
        <input type="search" id="plConfirmedQ" placeholder="確認済みを検索（品番・名・対応・状態）" value="${esc(st.confirmedQ||'')}" ${st.busy?'disabled':''}>
        <span class="excelhint">表示 ${confirmedAll.length} / 全 ${counts.confirmed} 件（反映待ち ${counts.confirmedPending}／確認済み ${counts.confirmedDone}）</span>
      </div>
      ${sectionPagerHtml('confirmed',slice,confirmedAll.length)}
      ${confirmedCompareTableHtml(st,slice.pageItems)}
    </div>`:''
    return `<section class="sku-judgment-section sku-section-confirmed" data-sku-section-block="confirmed">
      <header class="sku-section-header bar">
        <h4>確認済み一覧 <span class="sku-section-count">${counts.confirmed}件</span>
          <span class="sub">反映待ち ${counts.confirmedPending}／確認済み ${counts.confirmedDone}</span></h4>
        ${sectionToggleBtn(st,'confirmed')}
      </header>
      ${open?body:`<p class="excelhint sku-section-collapsed-hint">閉じています。確認済み ${counts.confirmed} 件（反映待ち ${counts.confirmedPending}／確認済み ${counts.confirmedDone}）。開いても選択・編集は消えません。</p>`}
    </section>`
  }

  function skuGroupsHtml(st){
    const part=partitionJudgmentGroups(st)
    const counts=judgmentStatusCounts(st)
    const unifiedAll=buildUnifiedReviewItems(st).filter(it=>matchesUnifiedFilters(st,it))
    const excludedRowList=listExcludedRows(st)
    const hasAny=counts.unresolved||counts.unapproved||counts.confirmed||counts.autoDecided||counts.hold||counts.excluded||counts.rowExcluded
    if(!hasAny&&!(st.preview&&(st.preview.skuGroups||[]).length)&&!((st.preview&&st.preview.rows)||[]).length)return ''
    const selected=selectedCodeList(st)
    const filterList=(arr)=>arr.filter(g=>matchesSkuFilters(st,g))
    const bulkAll=filterList(part.bulkUnapproved)
    const holdAll=filterList(part.hold)
    const unifiedSlice=pageSlice(st,'unified',unifiedAll)
    const bulkSlice=pageSlice(st,'bulk',bulkAll)
    const holdSlice=pageSlice(st,'hold',holdAll)
    const rowExSlice=pageSlice(st,'rowExcluded',excludedRowList)
    const bulkPages=bulkSlice.pages
    const unifiedOpen=!!(st.sectionOpen&&(st.sectionOpen.unified!=null?st.sectionOpen.unified:st.sectionOpen.individual))
    const bulkOpen=!!(st.sectionOpen&&st.sectionOpen.bulk)
    const autoOpen=!!(st.sectionOpen&&st.sectionOpen.auto)
    const holdOpen=!!(st.sectionOpen&&st.sectionOpen.hold)
    const rowExOpen=!!(st.sectionOpen&&st.sectionOpen.rowExcluded)
    const bulkApproveLabel=`一括承認候補${part.bulkUnapproved.length}件を承認`
    const bulkScopeHint=part.bulkUnapproved.length
      ?`承認対象は「一括承認候補」区分の未承認 ${part.bulkUnapproved.length} 件すべて（${bulkPages}ページ分）。確認リスト（未解決）・保留・自動決定済み・確認済み一覧は含みません。折りたたみだけでは承認されません。承認後は下の「確認済み一覧」へ移ります。`
      :'未承認の一括承認候補はありません。'

    const unifiedBody=unifiedOpen?`<div class="sku-section-body">
      <p class="excelhint">1行＝登録・判断する対象（通常品番／色別の親品番／加工名＋業者／品番なし・別注・部品資材の識別単位）。同じ対象の複数確認は同じ行にまとめ、解決した項目だけ外します。方針を選び必須入力が揃うと下の「確認済み一覧」へ移ります。下の元行一覧を別途確認する必要はありません。</p>
      <p class="excelhint">「登録予定」は品番・商品名・色・仕入先・加工／仕様ごとの<strong>予定値と状態</strong>です。未確定だけを強調します。「自動決定済み／手動確定済み」も登録確定前の予定です。長い判定根拠や元CSV行番号は「詳細を開く」側で確認してください。</p>
      ${sectionPagerHtml('unified',unifiedSlice,unifiedAll.length)}
      ${unifiedCompareTableHtml(st,unifiedSlice.pageItems)}
    </div>`:''

    const bulkBody=bulkOpen?`<div class="sku-section-body">
      <p class="excelhint">${esc(bulkScopeHint)}</p>
      <h4>未承認 ${part.bulkUnapproved.length} 件</h4>
      ${sectionPagerHtml('bulk',bulkSlice,bulkAll.length)}
      ${skuCompareTableHtml(st,bulkSlice.pageItems,{section:'bulk',showApproveHint:true})}
      <p class="excelhint">承認済みの品番は「承認済み（未登録）」としてここに残さず、下の「確認済み一覧」で追跡します（二重表示しません）。</p>
    </div>`:''

    const autoBody=autoOpen?autoDecidedBodyHtml(st,part):''

    const holdBody=holdOpen?`<div class="sku-section-body">
      <p class="excelhint">保留は登録対象外です。件数は隠さず残します。対象外 ${part.excludedRows||0} 行も outcome に計上されています。確認済み一覧には混ぜません。</p>
      ${sectionPagerHtml('hold',holdSlice,holdAll.length)}
      ${skuCompareTableHtml(st,holdSlice.pageItems,{section:'hold'})}
    </div>`:''

    return `<div class="sku-groups-panel sku-judgment-sections" data-sku-judgment-sections>
      <h3>確認リスト（統合）</h3>
      <p class="excelhint">品番単位の判断とCSV行単位の要確認を1つのリストに統合しています。未解決から順に対応してください。チェックは一括操作の選択だけで、チェックだけでは承認・検索・登録しません。</p>
      ${judgmentStatusBarHtml(st)}
      <div class="bar sku-list-toolbar">
        <input type="search" id="plSkuQ" placeholder="対象・商品名・仕入先・確認事項で検索" value="${esc(st.skuQ||'')}" ${st.busy?'disabled':''}>
        <select id="plSkuReason" ${st.busy?'disabled':''}>
          <option value="">理由すべて</option>
          <option value="bulk" ${st.skuReasonFilter==='bulk'?'selected':''}>一括承認候補</option>
          <option value="individual" ${st.skuReasonFilter==='individual'?'selected':''}>品番の個別確認</option>
          <option value="pending" ${st.skuReasonFilter==='pending'?'selected':''}>未決定のみ</option>
          <option value="merge" ${st.skuReasonFilter==='merge'?'selected':''}>確認済み（まとめる）</option>
          <option value="hold" ${st.skuReasonFilter==='hold'?'selected':''}>保留済</option>
          <option value="name_wording" ${st.skuReasonFilter==='name_wording'?'selected':''}>名差</option>
          <option value="supplier" ${st.skuReasonFilter==='supplier'?'selected':''}>仕入先差</option>
          <option value="color" ${st.skuReasonFilter==='color'?'selected':''}>色の疑い</option>
          <option value="processing" ${st.skuReasonFilter==='processing'?'selected':''}>加工の疑い</option>
          <option value="kind" ${st.skuReasonFilter==='kind'?'selected':''}>商品種の疑い</option>
        </select>
        <button type="button" id="plSkuSelectPage" ${st.busy||!(unifiedOpen||bulkOpen)?'disabled':''}>開いている区分の表示ページを選択</button>
        <button type="button" id="plSkuClearSel" class="quiet" ${st.busy||!selected.length?'disabled':''}>選択解除</button>
        <button type="button" id="plSkuBulkHold" ${st.busy||!selected.length?'disabled':''}>選択を今回は保留する</button>
        <button type="button" id="plBulkMakerFetch" class="quiet" ${st.busy||!selected.length||(st.bulkMakerLookup&&st.bulkMakerLookup.running)?'disabled':''}>選択した商品のメーカー情報を取得</button>
        <span class="excelhint" id="plSkuSelCount">選択中 ${selected.length} 品番（開いている区分の表示ページ／個別チェック）</span>
      </div>
      ${bulkMakerPanelHtml(st)}

      <section class="sku-judgment-section sku-section-individual sku-section-unified" data-sku-section-block="unified">
        <header class="sku-section-header bar">
          <h4>確認リスト <span class="sku-section-count">未解決 ${counts.unresolved}件</span>
            <span class="sub">確認事項 ${counts.issueCount}</span></h4>
          ${sectionToggleBtn(st,'unified')}
        </header>
        ${unifiedOpen?unifiedBody:`<p class="excelhint sku-section-collapsed-hint">閉じています。未解決 ${counts.unresolved} 件／確認事項 ${counts.issueCount}（開いても選択・編集は消えません）。</p>`}
      </section>

      <section class="sku-judgment-section sku-section-bulk" data-sku-section-block="bulk">
        <header class="sku-section-header bar">
          <h4>一括承認候補 <span class="sku-section-count">${part.bulkUnapproved.length}件</span>
            <span class="sub">未承認 ${part.bulkUnapproved.length}（承認後は確認済み一覧へ）</span></h4>
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
        ${autoOpen?autoBody:`<p class="excelhint sku-section-collapsed-hint">閉じています。開くと決定内容と理由を確認できます（まだ未登録・${part.autoCount}件）。ユーザー確認済み一覧には含めません。</p>`}
      </section>

      <section class="sku-judgment-section sku-section-hold" data-sku-section-block="hold">
        <header class="sku-section-header bar">
          <h4>保留・対象外 <span class="sku-section-count">保留 ${part.hold.length} / 対象外 ${part.excludedRows||0}</span></h4>
          ${sectionToggleBtn(st,'hold')}
        </header>
        ${holdOpen?holdBody:`<p class="excelhint sku-section-collapsed-hint">件数は上の登録前サマリでも確認できます。黙って非表示にはしません（保留 ${part.hold.length} / 対象外 ${part.excludedRows||0}）。</p>`}
      </section>

      <section class="sku-judgment-section sku-section-row-excluded" data-sku-section-block="rowExcluded">
        <header class="sku-section-header bar">
          <h4>登録しない項目 <span class="sku-section-count">${excludedRowList.length}行</span></h4>
          ${sectionToggleBtn(st,'rowExcluded')}
        </header>
        ${rowExOpen?`<div class="sku-section-body">
          <p class="excelhint">今回の取り込みだけ商品登録対象から外した元CSV行です（削除ではありません。恒久除外ルールにはしません）。確認リストの「今回登録しない」と詳細の「この行を登録しない」は同じ除外状態を共有します。手動理由は「利用者が今回登録しないと指定」、自動除外は別理由です。加工名不明の自動除外は加工名を入れて「登録対象に戻す」と再照合でも勝手に除外しません。保留（後回し）とは別です。確認済み一覧には混ぜません。</p>
          ${sectionPagerHtml('rowExcluded',rowExSlice,excludedRowList.length)}
          <div class="legacy-csv-table-wrap"><table class="legacy-csv-table product-ledger-table">
            <thead><tr><th>行</th><th>顧客</th><th>仕入先</th><th>品番</th><th>原文名</th><th>サイズ</th><th>理由</th><th>操作</th></tr></thead>
            <tbody>${(rowExSlice.pageItems||[]).map(r=>{
              const csv=r.csv||{}
              const reason=rowExclusionReason(st,r)
              const auto=!!(r.autoRowExcluded||(r.exclusionKind==='auto'))
              const kindLabel=auto?'自動除外':'手動除外'
              return `<tr class="legacy-status-hold" data-pl-excl-kind="${auto?'auto':'manual'}">
                <td>${r.rowNo||'—'}</td>
                <td>${esc(csv.customer||'')}</td>
                <td>${esc(csv.supplier||'')}</td>
                <td>${esc(csv.codeDisplay||csv.code||'')}</td>
                <td>${esc(csv.productName||'')}</td>
                <td>${esc(csv.size||'')}</td>
                <td><span class="badge">${esc(kindLabel)}</span> ${esc(reason)}</td>
                <td>
                  ${auto?`<label class="excelhint">加工名<input data-pl-row-restore-proc="${r.rowNo}" data-pl-row-restore-key="${esc(stableRowKeyFromRow(r))}" value="${esc(((st.rowClassifications||{})[String(r.rowNo)]||{}).processing||'')}" ${st.busy?'disabled':''} placeholder="例: ほつれ補修"></label>`:''}
                  <button type="button" class="quiet" data-pl-row-include="${r.rowNo}" data-pl-row-include-key="${esc(stableRowKeyFromRow(r))}" data-pl-row-include-auto="${auto?'1':''}" ${st.busy?'disabled':''}>登録対象に戻す</button>
                </td>
              </tr>`
            }).join('')||'<tr><td colspan="8">登録しない行はありません。</td></tr>'}
          </tbody></table></div>
        </div>`:`<p class="excelhint sku-section-collapsed-hint">折りたたみ中。登録しない ${excludedRowList.length} 行（開いても他の選択・入力は消えません）。</p>`}
      </section>

      ${confirmedSectionHtml(st,part,counts)}
    </div>`
  }

  function rowExcludeActionsHtml(st,r){
    if(!r||isOpeningMode(st))return ''
    const excluded=isRowExcluded(st,r)
    if(excluded){
      const reason=rowExclusionReason(st,r)
      const auto=!!(r.autoRowExcluded||(r.exclusionKind==='auto'))
      return `<div class="pl-row-exclude-actions" data-pl-row-exclude-box="${r.rowNo}">
        <p class="excelhint">この行は今回の商品登録対象から外しています（削除ではありません）。${esc(reason)}</p>
        ${auto?`<label class="excelhint">加工名<input data-pl-row-restore-proc="${r.rowNo}" value="${esc(((st.rowClassifications||{})[String(r.rowNo)]||{}).processing||'')}" ${st.busy?'disabled':''} placeholder="例: ほつれ補修"></label>`:''}
        <button type="button" class="quiet" data-pl-row-include="${r.rowNo}" data-pl-row-include-auto="${auto?'1':''}" ${st.busy?'disabled':''}>登録対象に戻す</button>
      </div>`
    }
    const peers=sameContentRows(st,r).filter(x=>!isRowExcluded(st,x))
    const multi=peers.length>1
    const peerHint=multi
      ?`<details class="pl-same-content-detail"><summary>同じ内容の対象を確認（${peers.length}行）</summary>
          <ul class="excelhint">${peers.map(p=>{
            const c=p.csv||{}
            return `<li>行${p.rowNo}　サイズ ${esc(c.size||'—')}　顧客 ${esc(c.customer||'—')}　原文 ${esc(c.productName||'—')}　仕入先 ${esc(c.supplier||'—')}</li>`
          }).join('')}</ul>
          <button type="button" class="quiet" data-pl-row-exclude-same="${r.rowNo}" ${st.busy?'disabled':''}>同じ内容の${peers.length}行を登録しない</button>
          <p class="excelhint">個別の「この行を登録しない」とは別操作です。加工名未定＋同じ業者だけではまとめません。</p>
        </details>`
      :''
    return `<div class="pl-row-exclude-actions" data-pl-row-exclude-box="${r.rowNo}">
      <button type="button" class="quiet" data-pl-row-exclude="${r.rowNo}" ${st.busy?'disabled':''}>この行を登録しない</button>
      ${peerHint}
      <p class="excelhint">取り込み対象から外します（元CSV・登録済みデータは消しません）。</p>
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
      const opts=(st.preview&&st.preview.categoryOptions)||['','制服','部品・資材','加工サービス']
      bits.push(`<label>区分<select data-pl-cat="${r.rowNo}" ${st.busy?'disabled':''}>${opts.map(v=>`<option value="${esc(v)}" ${cur===v?'selected':''}>${esc(v||'（なし）')}</option>`).join('')}</select></label>`)
    }
    // メーカー情報で補正: 要確認かつ具体的なメーカー品番がある行だけ
    const mk=r.makerCorrection||{}
    if(r.status==='needs_review'&&mk.eligible&&mk.makerSku){
      bits.push(`<button type="button" class="quiet" data-pl-maker-correct="${r.rowNo}" ${st.busy?'disabled':''}>メーカー情報で補正</button>`)
    }
    if(r.skuDecisionNeeded)bits.push('<span class="excelhint">↑品番比較で決定</span>')
    if(r.matchGroupCount>1)bits.push(`<span class="excelhint">同品番 ${r.matchGroupCount}行</span>`)
    // 区分の下へ「この行を登録しない」（削除ではない）
    bits.push(rowExcludeActionsHtml(st,r))
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

  const REGISTER_KINDS=['商品','部品・資材','加工サービス']

  /** 自動検出の登録区分（手動が無ければこれ）。加工／素材の入力だけでは切り替えない。 */
  function autoRegisterKindForRow(r){
    const pc=r&&r.parenClassification||{}
    const ps=r&&r.processingService||{}
    if(pc.autoRegisterKind&&REGISTER_KINDS.includes(pc.autoRegisterKind))return pc.autoRegisterKind
    const physicalParts=!!(r&&(r.physicalPartsView||pc.physicalPartsView||ps.isPhysicalParts))
    if(physicalParts)return '部品・資材'
    const processingView=!!(r&&(r.processingServiceView||pc.processingServiceView||(ps.isProcessingVendor&&!physicalParts)))
    if(processingView)return '加工サービス'
    return '商品'
  }

  /** 表示・編集に使う登録区分。手動 registerKind が勝つ。 */
  function effectiveRegisterKind(st,r){
    const edit=(st&&st.rowClassifications&&st.rowClassifications[String(r&&r.rowNo)])||{}
    const pc=r&&r.parenClassification||{}
    if(REGISTER_KINDS.includes(edit.registerKind))return edit.registerKind
    if(pc.registerKindManual&&REGISTER_KINDS.includes(pc.registerKind))return pc.registerKind
    return autoRegisterKindForRow(r)
  }

  function finalNameWithSupplement(name,supplement){
    const n=String(name||'').trim()
    const s=String(supplement||'').trim()
    if(!s)return n
    if(!n)return `（${s}）`
    if(n.includes(s))return n
    return `${n}（${s}）`
  }

  function registerAfterPreviewHtml(st,r,kind,fields){
    const edit=(st.rowClassifications&&st.rowClassifications[String(r.rowNo)])||{}
    const pending=!!edit.pendingRematch
    const pendingBit=pending?' <span class="badge">未反映</span>':''
    const rows=[
      ['登録区分',kind],
      ['最終名',fields.finalName||'—'],
      ['色',fields.color||'—'],
      [fields.supplierLabel||'仕入先',fields.supplier||'—'],
      ['素材',fields.material||'—'],
      ['必要な加工・仕様',fields.processSpec||'—'],
      ['用途',fields.purpose||'—'],
      ['補足',fields.supplement||'—']
    ]
    return `<div class="pl-register-after-preview">
      <p class="excelhint"><strong>登録後の内容</strong>${pendingBit}${pending?'（再照合で予定へ反映）':''}</p>
      <ul class="excelhint pl-register-after-list">${rows.map(([lab,val])=>
        `<li>${esc(lab)}: <strong>${esc(val||'—')}</strong></li>`
      ).join('')}</ul>
      ${fields.suppNote||''}
    </div>`
  }

  function unclassifiedBlockHtml(st,r,uncText,disabled){
    if(!uncText)return ''
    const edit=st.rowClassifications[String(r.rowNo)]||{}
    const move=edit.unclassifiedMove||'keep_in_name'
    return `<div class="pl-unclassified-block">
      <label>分類できなかった原文<input data-pl-paren-unc="${r.rowNo}" value="${esc(uncText)}" ${disabled}></label>
      <p class="excelhint">黙って消しません。行き先を選んでください。</p>
      <label class="pl-unc-move"><input type="radio" name="pl-unc-move-${r.rowNo}" data-pl-unc-move="${r.rowNo}" value="keep_in_name" ${move==='keep_in_name'?'checked':''} ${disabled}> 名称に残す</label>
      <label class="pl-unc-move"><input type="radio" name="pl-unc-move-${r.rowNo}" data-pl-unc-move="${r.rowNo}" value="to_purpose" ${move==='to_purpose'?'checked':''} ${disabled}> 用途へ移す</label>
      <label class="pl-unc-move"><input type="radio" name="pl-unc-move-${r.rowNo}" data-pl-unc-move="${r.rowNo}" value="to_supplement" ${move==='to_supplement'?'checked':''} ${disabled}> 補足へ移す</label>
    </div>`
  }

  function unusedFieldsNoteHtml(kind,edit,pc){
    const kept=[]
    if(kind==='商品'){
      if((edit.partsUsage!=null?edit.partsUsage:pc.partsUsage)||'')kept.push('用途（部品用）は未使用・保持')
    }else if(kind==='部品・資材'){
      if((edit.processing!=null?edit.processing:pc.processing)||'')kept.push('必要な加工・仕様は未使用・保持（商品の加工仕様とは別）')
    }else if(kind==='加工サービス'){
      if((edit.color!=null?edit.color:pc.color)||'')kept.push('色は未使用・保持')
      if((edit.material!=null?edit.material:pc.material)||'')kept.push('素材は未使用・保持')
      if((edit.partsUsage!=null?edit.partsUsage:pc.partsUsage)||'')kept.push('用途は未使用・保持')
    }
    if(!kept.length)return ''
    return `<p class="excelhint pl-unused-fields">区分変更でも入力は消しません。${esc(kept.join('／'))}。</p>`
  }

  function parenCell(st,r){
    const pc=r.parenClassification||{}
    const ps=r.processingService||{}
    const edit=st.rowClassifications[String(r.rowNo)]||{}
    const planned=edit.plannedName!=null?edit.plannedName:(pc.plannedName||'')
    const color=edit.color!=null?edit.color:(pc.color||'')
    const processing=edit.processing!=null?edit.processing:(pc.processing||'')
    const material=edit.material!=null?edit.material:(pc.material||'')
    const partsUsage=edit.partsUsage!=null?edit.partsUsage:(pc.partsUsage||'')
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
    const disabled=st.busy?'disabled':''
    const autoKind=autoRegisterKindForRow(r)
    const kind=effectiveRegisterKind(st,r)
    const manual=!!(edit.registerKind||pc.registerKindManual)
    const kindOpts=REGISTER_KINDS.map(k=>
      `<option value="${esc(k)}" ${kind===k?'selected':''}>${esc(k)}</option>`
    ).join('')
    const kindHead=`<div class="pl-register-kind-head">
      <label>登録区分<select data-pl-register-kind="${r.rowNo}" ${disabled}>${kindOpts}</select></label>
      <span class="excelhint">${manual?'手動（自動より優先）':`自動検出: ${esc(autoKind)}`}</span>
      <p class="excelhint">加工・素材欄への入力だけでは区分は切り替わりません。</p>
    </div>`
    const unusedNote=unusedFieldsNoteHtml(kind,edit,pc)
    const uncBlock=unclassifiedBlockHtml(st,r,uncText,disabled)
    const suppAppendNote=`<p class="excelhint">補足は登録時に名称へ括弧付きで付きます（例: 名称（補足））。</p>`

    if(kind==='部品・資材'){
      const partName=edit.plannedName!=null?edit.plannedName:(planned||(r.csv&&r.csv.productName)||'')
      const vendorName=(r.csv&&r.csv.supplier)||pc.processingVendor||''
      const purpose=partsUsage||''
      // 用途が補足へ二重に載らないよう、表示用補足から用途を除く
      let suppOnly=supplement||''
      if(purpose&&suppOnly===purpose)suppOnly=''
      else if(purpose&&suppOnly.startsWith(purpose+'／'))suppOnly=suppOnly.slice(purpose.length+1)
      const finalName=finalNameWithSupplement(partName,suppOnly||purpose)
      const preview=registerAfterPreviewHtml(st,r,kind,{
        finalName,color,supplier:vendorName,supplierLabel:'仕入先',
        material,processSpec:'',purpose,supplement:suppOnly,
        suppNote:suppAppendNote
      })
      return `<div class="pl-paren-edit pl-physical-parts-edit">
        ${kindHead}
        ${noticeHtml}
        <label>名称<input data-pl-paren-name="${r.rowNo}" value="${esc(partName)}" ${disabled} placeholder="例: DCP515用生地"></label>
        <label>仕入先<input value="${esc(vendorName)}" readonly disabled></label>
        <label>色（任意）<input data-pl-paren-color="${r.rowNo}" value="${esc(color)}" ${disabled}></label>
        <label>素材（任意）<input data-pl-paren-mat="${r.rowNo}" value="${esc(material)}" ${disabled} placeholder="生地そのものは名称側（部品）"></label>
        <p class="excelhint">素材は生地の材質など。生地自体は部品・資材の名称です。</p>
        <label>用途（任意）<input data-pl-paren-purpose="${r.rowNo}" value="${esc(purpose)}" ${disabled} placeholder="例: 袖／裾加工用"></label>
        <p class="excelhint">「袖／裾加工用」は用途です。加工サービス名にはしません。</p>
        <label>補足<input data-pl-paren-supp="${r.rowNo}" value="${esc(suppOnly)}" ${disabled} placeholder="色・寸法など"></label>
        ${suppAppendNote}
        ${uncBlock}
        ${unusedNote}
        ${preview}
      </div>`
    }

    if(kind==='加工サービス'){
      const serviceName=edit.processing!=null?edit.processing:(ps.serviceName||processing||'')
      const vendorName=ps.vendorName||pc.processingVendor||(r.csv&&r.csv.supplier)||''
      const specs=ps.rowSpecs||{}
      const specBits=[specs.customerSpecific,specs.parenSpecs,specs.dimensions,specs.composition].filter(Boolean)
      const specText=edit.supplement!=null?edit.supplement:(specBits.join('／')||supplement||'')
      const autoOk=!!(ps.identifiable&&ps.isProcessingService&&!ps.needsConfirm)
      const flag=autoOk?'自動（加工サービス）':(ps.needsConfirm?'要確認（加工名不足）':'加工サービス')
      const missing=pc.processingMissingHint||((ps.missingFields||[]).join('・'))||''
      const preview=registerAfterPreviewHtml(st,r,kind,{
        finalName:serviceName,color:'',supplier:vendorName,supplierLabel:'加工業者',
        material:'',processSpec:'',purpose:'',supplement:specText,
        suppNote:`<p class="excelhint">加工名＝登録・表示名です（登録時表示名の別欄はありません）。商品の「必要な加工・仕様」とは別です。</p>`
      })
      return `<div class="pl-paren-edit pl-processing-service-edit" data-pl-processing-sync="${r.rowNo}">
        ${kindHead}
        <div class="excelhint">${esc(flag)}</div>
        ${missing?`<div class="excelhint pl-paren-info">${esc(missing)}</div>`:''}
        ${noticeHtml}
        <label>加工名<input data-pl-paren-proc="${r.rowNo}" data-pl-processing-name-sync="1" value="${esc(serviceName)}" ${disabled||autoOk?'readonly':''} ${disabled} placeholder="例: サイズダウン"></label>
        <label>加工業者<input value="${esc(vendorName)}" readonly disabled></label>
        <label>補足・個別仕様<input data-pl-paren-supp="${r.rowNo}" value="${esc(specText)}" ${disabled} placeholder="位置・寸法・顧客固有など"></label>
        ${autoOk?`<p class="excelhint">一覧・登録の表示は加工名をそのまま使います。</p>`:''}
        ${ps.needsConfirm?`<label class="pl-paren-confirm"><input type="checkbox" data-pl-paren-ok="${r.rowNo}" ${edit.confirmed?'checked':''} ${disabled}> 加工名を確認した</label>`:''}
        ${uncBlock}
        ${unusedNote}
        ${preview}
      </div>`
    }

    // 商品
    const flag=pc.needsConfirm?'要確認':(pc.autoClassified||pc.scopedColorCorrection?'自動':'—')
    const supplier=(r.csv&&r.csv.supplier)||''
    const finalName=finalNameWithSupplement(planned,supplement)
    const preview=registerAfterPreviewHtml(st,r,kind,{
      finalName,color,supplier,supplierLabel:'仕入先',
      material,processSpec:processing,purpose:'',supplement,
      suppNote:suppAppendNote
    })
    return `<div class="pl-paren-edit pl-product-edit">
      ${kindHead}
      <div class="excelhint">${esc(flag)}${pc.keptOriginal&&pc.needsConfirm?'（原文維持）':''}</div>
      ${noticeHtml}
      ${scopedPreview}
      <label>商品名<input data-pl-paren-name="${r.rowNo}" value="${esc(planned)}" ${disabled}></label>
      <label>色<input data-pl-paren-color="${r.rowNo}" value="${esc(color)}" ${disabled}></label>
      <label>仕入先<input value="${esc(supplier)}" readonly disabled></label>
      <label>必要な加工・仕様<input data-pl-paren-proc="${r.rowNo}" value="${esc(processing)}" ${disabled} placeholder="商品に付く加工仕様（加工サービス名ではない）"></label>
      <label>素材<input data-pl-paren-mat="${r.rowNo}" value="${esc(material)}" ${disabled} placeholder="材質・生地の種類など"></label>
      <p class="excelhint">素材は材質のヒントです。生地そのものの在庫は部品・資材区分で登録します。</p>
      <label>補足<input data-pl-paren-supp="${r.rowNo}" value="${esc(supplement)}" ${disabled}></label>
      ${suppAppendNote}
      ${uncBlock}
      ${unusedNote}
      ${preview}
      ${pc.needsConfirm?`<label class="pl-paren-confirm"><input type="checkbox" data-pl-paren-ok="${r.rowNo}" ${edit.confirmed?'checked':''} ${disabled}> 分類を確認した</label>`:''}
    </div>`
  }

  function makerLookupPanelHtml(st,opts){
    opts=opts||{}
    const panel=st.makerLookup
    if(!panel)return ''
    // 品番行インライン表示時は、その品番向けパネルだけ出す
    if(opts.forSkuCode!=null&&(panel.scope!=='sku'||String(panel.skuCode)!==String(opts.forSkuCode)))return ''
    if(opts.forRowNo!=null&&(panel.scope!=='row'||Number(panel.rowNo)!==Number(opts.forRowNo)))return ''
    const res=panel.result||{}
    const cands=Array.isArray(res.candidates)?res.candidates:[]
    const matchedPages=Array.isArray(res.matchedPages)?res.matchedPages:[]
    const searching=!panel.result&&!panel.error&&(panel.statusLabel==='検索中…'||st.busy)
    const status=esc(res.statusLabel||panel.statusLabel||(searching?'検索中…':'—'))
    const note=esc(res.note||'')
    const ref=res.referenceUrl||''
    const skuScope=panel.scope==='sku'
    const scopeLabel=skuScope
      ?`品番 ${esc(panel.skuCode||panel.makerSku||'')}（グループ ${(panel.rowNos||[]).length||0} 行）`
      :`行 ${esc(String(panel.rowNo||''))}`
    const list=cands.map((c,i)=>{
      const diff=c.diff||{}
      const missing=(c.missingFields||[]).filter(Boolean)
      const plannedSku=c.plannedRegisteredSku||''
      const currentSku=diff.currentSku||panel.currentSku||panel.makerSku||''
      const cleaned=c.cleanedName||c.name||''
      const originalTitle=c.originalTitle||c.title||''
      // 上下は品番末尾へ移すため、補足に上／下が残っていれば表示しない
      const suppDisp=(c.supplement==='上'||c.supplement==='下')?'':(c.supplement||'')
      const urls=(Array.isArray(c.sourceUrls)&&c.sourceUrls.length?c.sourceUrls:(c.sourceUrl?[c.sourceUrl]:[]))
      const fs=c.fieldSources||{}
      const colorUnavailable=!!(c.colorUnavailable||(!c.color&&(missing.includes('color')||(fs.color&&fs.color.unavailable))))
      const keepColor=(!c.color&&(diff.currentColor||''))?diff.currentColor:''
      const adoptColorDisp=c.color||(keepColor?`${keepColor}（既存維持）`:'（色未取得・空上書きなし）')
      const fieldSrcLine=(label,key,fallbackVal)=>{
        const f=fs[key]||{}
        const val=f.value||fallbackVal||''
        const srcLabel=f.sourceLabel||(key==='color'&&colorUnavailable?'色未取得':'')
        const srcUrl=f.sourceUrl||''
        const unavail=key==='color'&&(f.unavailable||colorUnavailable)&&!val
        return `<div class="excelhint">${esc(label)}: <strong>${esc(unavail?'取得できませんでした':(val||'—'))}</strong>`+
          `${srcLabel?`　出典 ${esc(srcLabel)}`:''}`+
          `${srcUrl?`　<a href="${esc(srcUrl)}" target="_blank" rel="noopener">URL</a>`:''}`+
          `${f.complemented?' <span class="sub">（同一品番ページから補完）</span>':''}</div>`
      }
      return `<li class="pl-maker-cand">
        <div>
          <div class="excelhint">元掲載名: ${esc(originalTitle||'—')}</div>
          <div><strong>整理後商品名: ${esc(cleaned||'—')}</strong>${c.color?`　色 ${esc(c.color)}`:(colorUnavailable?'　<span class="sub">色未取得</span>':'')}${c.size?`　サイズ ${esc(c.size)} <span class="sub">（表示・照合用／CSVサイズは変更しません）</span>`:''}${suppDisp?`　補足 ${esc(suppDisp)}`:''}
            ${plannedSku?`　品番 <strong>${esc(plannedSku)}</strong>`:(c.makerSku?`　品番 ${esc(c.makerSku)}`:'')}
            ${c.position?`　上下 ${esc(c.position)}`:''}
            ${c.manufacturer?`　メーカー ${esc(c.manufacturer)}`:''}
            ${c.fromSaved?' <span class="badge">保存済み</span>':''}
            ${c.preferred?' <span class="sub">優先候補</span>':''}
            ${c.makerColorCode?`　メーカー色番 ${esc(c.makerColorCode)}`:''}
            ${c.skuMatchEvidence?` <span class="sub">${esc(c.skuMatchEvidence)}</span>`:(c.skuMatch==='structured'?' <span class="sub">本文商品コード一致</span>':'')}
            ${colorUnavailable?' <span class="badge">色補正未完了</span>':''}
          </div>
        </div>
        ${fieldSrcLine('商品名','name',cleaned)}
        ${fieldSrcLine('色','color',c.color||'')}
        ${fieldSrcLine('品番','sku',plannedSku||c.makerSku||'')}
        ${missing.length?`<div class="excelhint">不足項目: ${missing.map(esc).join('、')}</div>`:''}
        ${colorUnavailable?`<div class="excelhint">色は取得できませんでした。名のみの候補を色補正完了とは扱いません。既存・手動の色は空では上書きしません。</div>`:''}
        <div class="excelhint">現在の予定: 名 ${esc(diff.currentName||'—')} / 色 ${esc(diff.currentColor||'（空）')} / 品番 ${esc(currentSku||'—')} / 補足 ${esc(diff.currentSupplement||'（空）')}</div>
        <div class="excelhint">採用後の予定: 名 ${esc(cleaned||'—')} / 色 ${esc(adoptColorDisp)} / 品番 ${esc(plannedSku||currentSku||'—')} / 補足 ${esc(suppDisp||'（空）')}</div>
        <div class="excelhint">差分: 名 ${diff.nameChanged?'変わる':'同じ'} / 色 ${diff.colorChanged?'変わる':'同じ'} / 品番 ${diff.skuChanged||(plannedSku&&plannedSku!==currentSku)?'変わる':'同じ'} / 補足 ${diff.supplementChanged?'変わる':'同じ'}</div>
        ${urls.length?`<div class="excelhint">参照URL: ${urls.map(u=>`<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`).join('　')}</div>`:''}
        <button type="button" class="primary" data-pl-maker-adopt="${i}" ${st.busy||!cleaned?'disabled':''}>採用</button>
      </li>`
    }).join('')
    const matchedList=matchedPages.length
      ?`<ul class="pl-maker-matched-pages">${matchedPages.map(p=>{
          const miss=(p.missingFields||[]).filter(Boolean)
          return `<li class="excelhint">
            ${p.sourceUrl?`<a href="${esc(p.sourceUrl)}" target="_blank" rel="noopener">参照ページ</a>`:'参照ページ'}
            ${p.title?` — ${esc(p.title)}`:''}
            ${miss.length?`　不足: ${miss.map(esc).join('、')}`:''}
          </li>`
        }).join('')}</ul>`
      :''
    const resStatus=res.status||''
    const colorMissing=cands.some(c=>!!(c.colorUnavailable||(!(c.color||'').trim()&&(c.missingFields||[]).includes('color'))))
    const showUrlForm=!searching&&(makerLookupNeedsUrlAssist(resStatus)||!cands.length||colorMissing||!!panel.urlFetchError)
    const urlForm=showUrlForm
      ?makerUrlFetchFormHtml({
        scope:panel.scope||'',
        inputId:'plMakerUrlInput',
        btnId:'plMakerUrlFetch',
        value:panel.urlInput||'',
        disabled:!!st.busy
      })
      :''
    const urlErr=panel.urlFetchError
      ?`<p class="product-ledger-import-error" role="alert">${esc(panel.urlFetchError)}</p>`
      :''
    return `<div class="notice pl-maker-lookup-panel" role="region" aria-label="メーカー情報で補正" data-pl-maker-panel="${esc(panel.scope||'')}">
      <div class="bar">
        <strong>メーカー情報で補正</strong>
        <span class="sub">${scopeLabel}　検索品番 ${esc(panel.makerSku||res.searchMakerSku||'')}
          ${(res.sourceSku&&res.sourceSku!==(panel.makerSku||res.searchMakerSku))?`　元品番 ${esc(res.sourceSku)}`:''}
          ${res.usageNote?`　注記 ${esc(res.usageNote)}`:''}
          ${panel.manufacturer?`　メーカー ${esc(panel.manufacturer)}`:''}
          ${panel.supplier?`　仕入先（検索キー外） ${esc(panel.supplier)}`:''}
        </span>
        <button type="button" class="quiet" id="plMakerViewResults" ${st.busy?'disabled':''}>結果を見る</button>
        <button type="button" class="quiet" id="plMakerLookupClose" ${st.busy?'disabled':''}>閉じる</button>
      </div>
      <p>状態: <strong>${status}</strong>${searching?' <span class="sub">メーカーページを検索しています…</span>':''}${res.fromUrl?' <span class="sub">（URL指定）</span>':''}</p>
      ${note?`<p class="excelhint">${note}</p>`:''}
      ${skuScope?`<p class="excelhint">品番内の色表記ゆれをメーカー情報で揃え、1商品の登録予定へまとめます。メーカー側に複数色がある場合は候補から選んでください（自動では決めません）。</p>`:''}
      ${panel.error?`<p class="product-ledger-import-error" role="alert">${esc(panel.error)}</p>`:''}
      ${matchedList}
      ${cands.length?`<ul class="pl-maker-cand-list">${list}</ul>`:(searching?'':`<p class="excelhint">採用できる候補はありません。</p>`)}
      ${urlErr}${urlForm}
      ${ref?`<p class="excelhint"><a href="${esc(ref)}" target="_blank" rel="noopener">参考ページを開く</a>（開いただけでは補正完了になりません）</p>`:''}
      <p class="excelhint">「採用」はプレビューの予定名／色／登録予定品番（上下は品番末尾）を更新します。台帳への登録は「確認して商品を登録」で行います。原文名は照合用に残します。</p>
    </div>`
  }

  function skuMakerLookupRowHtml(st,code,opts){
    opts=opts||{}
    const panel=st.makerLookup
    if(!panel||panel.scope!=='sku'||String(panel.skuCode)!==String(code))return ''
    const colspan=Number(opts.colspan)||SKU_COMPARE_COL_COUNT
    return `<tr class="sku-maker-lookup-row" data-sku-maker-lookup="${esc(code)}"><td colspan="${colspan}">${makerLookupPanelHtml(st,{forSkuCode:code})}</td></tr>`
  }

  /**
   * メーカー結果パネルへスクロール（ユーザーが「結果を見る」を押したときだけ）。
   * 取得開始・完了・候補更新では自動呼び出ししない。
   */
  function scrollMakerLookupIntoView(){
    const dlg=document.getElementById('productLedgerCsvDialog')||document.querySelector('dialog.product-ledger-csv-dialog')||document.getElementById('dialog')
    const root=dlg||document
    const el=root.querySelector('.pl-bulk-maker-panel')||root.querySelector('.pl-maker-lookup-panel')||root.querySelector('[data-sku-maker-lookup]')
    if(el&&typeof el.scrollIntoView==='function'){
      try{el.scrollIntoView({block:'nearest',inline:'nearest'})}catch(_){el.scrollIntoView(false)}
    }
  }

  function bindMakerViewResultsButtons(){
    const bulkView=document.getElementById('plBulkMakerViewResults')
    if(bulkView)bulkView.onclick=()=>scrollMakerLookupIntoView()
    const singleView=document.getElementById('plMakerViewResults')
    if(singleView)singleView.onclick=()=>scrollMakerLookupIntoView()
  }

  function stopBulkMakerPoll(st){
    const s=st||ensureState()
    if(s._bulkMakerPoll){
      try{clearInterval(s._bulkMakerPoll)}catch(_){}
      s._bulkMakerPoll=null
    }
  }

  function bulkMakerStatusLabel(status){
    const map={
      ok:'一致候補1件',multiple:'候補複数',colorway_choice:'配色の選択が必要',
      fetch_failed:'検索結果取得不可',
      page_unavailable:'商品ページ取得不可',sku_mismatch:'品番不一致',
      maker_mismatch:'メーカー不一致',
      parse_incomplete:'品番一致したが必要項目抽出不可',
      official_no_hit:'公式サイトに該当なし',no_match:'候補なし',
      invalid_sku:'メーカー品番が対象外',invalid_url:'URLが対象外',
      pending:'未処理',cancelled:'未処理・中止',error:'取得失敗'
    }
    return map[status]||status||'—'
  }

  function makerLookupNeedsUrlAssist(status){
    return ['no_match','official_no_hit','parse_incomplete','sku_mismatch','maker_mismatch',
      'page_unavailable','fetch_failed','invalid_url','error'].includes(status||'')
  }

  function makerUrlFetchFormHtml(opts){
    opts=opts||{}
    const inputId=opts.inputId||'plMakerUrlInput'
    const btnId=opts.btnId||'plMakerUrlFetch'
    const resultKey=opts.resultKey||''
    const disabled=opts.disabled?'disabled':''
    const value=esc(opts.value||'')
    return `<div class="pl-maker-url-fetch" data-pl-maker-url-scope="${esc(opts.scope||'')}" ${resultKey?`data-pl-maker-url-result="${esc(resultKey)}"`:''}>
      <label class="pl-maker-url-label">商品ページURL
        <input type="url" id="${esc(inputId)}" data-pl-maker-url-input value="${value}"
          placeholder="https://…" autocomplete="off" ${disabled}>
      </label>
      <button type="button" class="quiet" id="${esc(btnId)}" data-pl-maker-url-fetch ${disabled}>このURLから商品情報を取得</button>
      <p class="excelhint">公開の HTTP/HTTPS のみ。URL入力だけでは登録予定は変わりません。「採用」で反映します。</p>
    </div>`
  }

  function bulkMakerAdoptSelectionKey(result,candIdx){
    return `${result.searchKey||result.skuCodes&&result.skuCodes[0]||''}#${candIdx}`
  }

  /** 確認残り件数（採用メタ表示用）。 */
  function bulkMakerRemainingConfirmCount(panel){
    if(!panel)return 0
    const adoptSel=panel.adoptSelected||{}
    let remainingConfirm=0
    ;(panel.results||[]).forEach(r=>{
      if(r.status==='ok'&&r.adoptableSingle){
        if(!adoptSel[bulkMakerAdoptSelectionKey(r,0)]&&!r.adopted)remainingConfirm+=1
      }else if(r.status==='multiple'||r.status==='colorway_choice'){
        const picked=Object.keys(adoptSel).some(k=>k.startsWith(`${r.searchKey}#`)&&adoptSel[k])
        if(!picked&&!r.adopted)remainingConfirm+=1
      }else if(['parse_incomplete','sku_mismatch','maker_mismatch','invalid_url',
        'page_unavailable','fetch_failed','error','official_no_hit'].includes(r.status)){
        remainingConfirm+=1
      }
    })
    return remainingConfirm
  }

  function bulkMakerProgressLine(panel){
    if(!panel)return ''
    const running=!!panel.running&&!panel.finished
    if(running){
      return `処理中 ${panel.done||0}／${panel.total||0}`
        +(panel.currentMakerSku||panel.currentSku?`　現在: ${panel.currentMakerSku||panel.currentSku}`:'')
    }
    if(panel.finished)return `完了 ${panel.done||0}／${panel.total||0}`
    return '準備中'
  }

  function bulkMakerCountLine(panel){
    const counts=(panel&&panel.counts)||{}
    return `候補1件 ${counts.ok||0}　複数 ${(counts.multiple||0)+(counts.colorwayChoice||0)}　要確認 ${counts.needsConfirm||0}　取得失敗 ${(counts.fetchFailed||0)+(counts.pageUnavailable||0)+(counts.error||0)}　候補なし ${(counts.noMatch||0)+(counts.officialNoHit||0)}　未処理・中止 ${(counts.cancelled||0)+(counts.pending||0)}`
  }

  /**
   * 候補チェック変更用の軽量更新。paintDialog（一覧・候補リストの再生成）はしない。
   * - 内部 adoptSelected は呼び出し側で更新済みであること
   * - 同一商品の他候補チェック同期・採用ボタン有効／無効・採用済／確認残り
   * スクロール・フォーカス・他の編集中の値は触らない。採用・再照合・登録は開始しない。
   */
  function syncBulkMakerAdoptSelectionUi(st){
    st=st||ensureState()
    const panel=st.bulkMakerLookup
    if(!panel)return
    if(!panel.adoptSelected)panel.adoptSelected={}
    const adoptSel=panel.adoptSelected
    document.querySelectorAll('[data-pl-bulk-maker-pick]').forEach(el=>{
      const key=el.getAttribute('data-pl-bulk-maker-pick')
      const want=!!adoptSel[key]
      if(el.checked!==want)el.checked=want
    })
    const running=!!panel.running&&!panel.finished
    const anySelected=Object.keys(adoptSel).some(k=>adoptSel[k])
    const adoptBtn=document.getElementById('plBulkMakerAdopt')
    if(adoptBtn)adoptBtn.disabled=!!st.busy||running||!anySelected
    const meta=document.getElementById('plBulkMakerAdoptMeta')
    if(meta){
      meta.textContent=`採用済 ${panel.adoptedCount||0}　確認残り ${bulkMakerRemainingConfirmCount(panel)}`
    }
  }

  /** 結果テーブル構造が変わったか（進捗数字だけの変化は含めない）。 */
  function bulkMakerStructureSignature(panel){
    if(!panel)return ''
    const results=panel.results||[]
    return [
      panel.running?'1':'0',
      panel.finished?'1':'0',
      panel.error||'',
      (panel.excluded||[]).length,
      results.length,
      results.map(r=>[
        r.searchKey||'',
        r.status||'',
        (r.candidates||[]).length,
        r.adopted?'1':'0',
        r.fromUrl?'1':'0',
        (r.note||'').length
      ].join(':')).join('|')
    ].join('::')
  }

  function harvestBulkMakerUrlInputs(st){
    const panel=st&&st.bulkMakerLookup
    if(!panel)return
    panel.urlInputs=panel.urlInputs||{}
    document.querySelectorAll('.pl-bulk-maker-panel [data-pl-maker-url-input]').forEach(input=>{
      const wrap=input.closest('.pl-maker-url-fetch')
      const idxAttr=wrap&&wrap.getAttribute('data-pl-maker-url-result')
      const ri=Number(idxAttr)
      const row=(panel.results||[])[ri]
      const key=(row&&row.searchKey)||String(ri)
      panel.urlInputs[key]=input.value||''
    })
  }

  /** 進捗テキストだけ更新（候補リストDOMは触らない）。 */
  function patchBulkMakerProgressTexts(st){
    const panel=st&&st.bulkMakerLookup
    if(!panel)return
    const prog=document.getElementById('plBulkMakerProgress')
    if(prog)prog.textContent=bulkMakerProgressLine(panel)
    const counts=document.getElementById('plBulkMakerCounts')
    if(counts)counts.textContent=bulkMakerCountLine(panel)
    const meta=document.getElementById('plBulkMakerAdoptMeta')
    if(meta){
      meta.textContent=`採用済 ${panel.adoptedCount||0}　確認残り ${bulkMakerRemainingConfirmCount(panel)}`
    }
  }

  /**
   * 一括メーカーパネルだけ差し替え。外枠ダイアログと候補リストの縦横スクロールを保持。
   * フォーカス復元は preventScroll（focusIntoView 相当の自動スクロールを避ける）。
   */
  function refreshBulkMakerPanelUi(st){
    st=st||ensureState()
    const dlg=document.getElementById('dialog')
    if(!dlg||!st.open)return
    const existing=dlg.querySelector('.pl-bulk-maker-panel')
    if(!existing||!st.bulkMakerLookup){
      paintDialog()
      return
    }
    harvestBulkMakerUrlInputs(st)
    const body=ledgerBodyEl(dlg)
    const bodyScroll=readScrollPos(body)
    const wrapScroll=readScrollPos(existing.querySelector('.pl-bulk-maker-results-wrap'))
    const active=document.activeElement
    const activePick=active&&active.getAttribute?active.getAttribute('data-pl-bulk-maker-pick'):''
    const activeUrlId=active&&active.id&&String(active.id).indexOf('plBulkMakerUrl')===0?active.id:''
    const tmp=document.createElement('div')
    tmp.innerHTML=bulkMakerPanelHtml(st)
    const next=tmp.firstElementChild
    if(!next)return
    existing.replaceWith(next)
    applyScrollPos(body,bodyScroll)
    applyScrollPos(next.querySelector('.pl-bulk-maker-results-wrap'),wrapScroll)
    bindBulkMakerPanelHandlers()
    let focusEl=null
    if(activePick){
      focusEl=next.querySelector(`[data-pl-bulk-maker-pick="${CSS.escape(activePick)}"]`)
    }else if(activeUrlId){
      focusEl=document.getElementById(activeUrlId)
    }
    if(focusEl&&typeof focusEl.focus==='function'){
      try{focusEl.focus({preventScroll:true})}catch(_){try{focusEl.focus()}catch(__){}}
    }
  }

  /** 一括メーカーパネル内の操作（候補チェック・採用・中止・URL取得）。 */
  function bindBulkMakerPanelHandlers(){
    const st=ensureState()
    bindMakerViewResultsButtons()
    const bulkMakerCancel=$('#plBulkMakerCancel')
    if(bulkMakerCancel)bulkMakerCancel.onclick=()=>cancelBulkMakerLookup(st)
    const bulkMakerClose=$('#plBulkMakerClose')
    if(bulkMakerClose)bulkMakerClose.onclick=()=>{
      stopBulkMakerPoll(st)
      st.bulkMakerLookup=null
      paintDialog()
    }
    const bulkMakerAdopt=$('#plBulkMakerAdopt')
    if(bulkMakerAdopt)bulkMakerAdopt.onclick=()=>adoptBulkMakerSelections(st)
    document.querySelectorAll('[data-pl-bulk-maker-pick]').forEach(el=>{
      el.onchange=()=>{
        if(!st.bulkMakerLookup)return
        const key=el.getAttribute('data-pl-bulk-maker-pick')
        const ri=Number(el.getAttribute('data-result-idx'))
        const result=(st.bulkMakerLookup.results||[])[ri]
        if(!st.bulkMakerLookup.adoptSelected)st.bulkMakerLookup.adoptSelected={}
        // 同一結果の候補は単一選択（複数候補から1つ）
        if(result&&result.searchKey){
          Object.keys(st.bulkMakerLookup.adoptSelected).forEach(k=>{
            if(k.startsWith(`${result.searchKey}#`)&&k!==key)st.bulkMakerLookup.adoptSelected[k]=false
          })
        }
        st.bulkMakerLookup.adoptSelected[key]=!!el.checked
        // チェックだけでは採用・再照合・登録しない。候補リストも作り直さない。
        syncBulkMakerAdoptSelectionUi(st)
      }
    })
    document.querySelectorAll('.pl-bulk-maker-panel [data-pl-maker-url-input]').forEach(input=>{
      const sync=()=>{
        if(!st.bulkMakerLookup)return
        harvestBulkMakerUrlInputs(st)
      }
      input.oninput=sync
      input.onchange=sync
    })
    document.querySelectorAll('[data-pl-maker-url-fetch]').forEach(el=>{
      if(el.id==='plMakerUrlFetch')return
      el.onclick=async()=>{
        const panel=st.bulkMakerLookup
        if(!panel||panel.running){st.toast('一括取得の完了後にURL指定できます。');return}
        const wrap=el.closest('.pl-maker-url-fetch')
        const idxAttr=wrap&&wrap.getAttribute('data-pl-maker-url-result')
        const ri=Number(idxAttr)
        const row=(panel.results||[])[ri]
        if(!row){st.toast('対象行が見つかりません。');return}
        const input=wrap&&wrap.querySelector('[data-pl-maker-url-input]')
        const url=((input&&input.value)||'').trim()
        const key=row.searchKey||String(ri)
        panel.urlInputs=panel.urlInputs||{}
        panel.urlFetchErrors=panel.urlFetchErrors||{}
        panel.urlInputs[key]=url
        if(!url){st.toast('商品ページURLを入力してください。');return}
        st.busy=true
        panel.urlFetchErrors[key]=''
        refreshBulkMakerPanelUi(st)
        try{
          const result=await st.api('/api/product-ledger-csv/maker-lookup/from-url',{
            url,
            manufacturer:row.manufacturer||'',
            makerSku:row.makerSku||'',
            currentName:row.currentName||'',
            currentColor:row.currentColor||'',
            currentSupplement:row.currentSupplement||'',
            currentSku:row.currentSku||row.makerSku||'',
            sourceName:row.sourceName||'',
            supplier:row.supplier||''
          })
          const cands=result.candidates||[]
          const next=Object.assign({},row,{
            status:result.status,
            statusLabel:result.statusLabel||bulkMakerStatusLabel(result.status),
            note:result.note||'',
            candidates:cands,
            referenceUrl:result.referenceUrl||url,
            plannedRegisteredSku:result.plannedRegisteredSku||row.plannedRegisteredSku,
            fromUrl:true,
            matchedPages:result.matchedPages||[],
            adoptableSingle:result.status==='ok'&&cands.length===1&&!!(cands[0]&&cands[0].name)
          })
          const results=(panel.results||[]).slice()
          results[ri]=next
          // 件数の再集計（候補なしと失敗を混同しない）
          const counts=Object.assign({},panel.counts||{})
          const prevStatus=row.status||''
          const dec=(k)=>{if(counts[k])counts[k]=Math.max(0,(counts[k]||0)-1)}
          const statusKey={
            ok:'ok',multiple:'multiple',colorway_choice:'colorwayChoice',
            no_match:'noMatch',official_no_hit:'officialNoHit',
            fetch_failed:'fetchFailed',page_unavailable:'pageUnavailable',
            sku_mismatch:'needsConfirm',maker_mismatch:'needsConfirm',
            parse_incomplete:'needsConfirm',error:'error',invalid_url:'needsConfirm'
          }
          if(statusKey[prevStatus])dec(statusKey[prevStatus]==='needsConfirm'?'needsConfirm':statusKey[prevStatus])
          if(next.status==='ok')counts.ok=(counts.ok||0)+1
          else if(next.status==='multiple')counts.multiple=(counts.multiple||0)+1
          else if(next.status==='colorway_choice')counts.colorwayChoice=(counts.colorwayChoice||0)+1
          else if(next.status==='no_match')counts.noMatch=(counts.noMatch||0)+1
          else if(next.status==='official_no_hit')counts.officialNoHit=(counts.officialNoHit||0)+1
          else if(next.status==='fetch_failed')counts.fetchFailed=(counts.fetchFailed||0)+1
          else if(next.status==='page_unavailable')counts.pageUnavailable=(counts.pageUnavailable||0)+1
          else if(next.status==='error')counts.error=(counts.error||0)+1
          else counts.needsConfirm=(counts.needsConfirm||0)+1
          if(next.adoptableSingle){
            const pickKey=bulkMakerAdoptSelectionKey(next,0)
            panel.adoptSelected=Object.assign({},panel.adoptSelected||{})
            panel.adoptSelected[pickKey]=true
          }
          if(cands.length&&cands[0]&&cands[0].name){
            panel.urlFetchErrors[key]=''
            st.toast(`品番 ${row.makerSku||''}: URLから候補を取得しました`)
          }else{
            panel.urlFetchErrors[key]=result.note||result.statusLabel||'候補を取得できませんでした'
          }
          st.bulkMakerLookup=Object.assign({},panel,{results,counts})
        }catch(e){
          panel.urlFetchErrors[key]=apiErrorMessage(e,'URLからの取得に失敗しました。')
          st.bulkMakerLookup=Object.assign({},panel)
          st.toast(panel.urlFetchErrors[key],6000)
        }finally{
          st.busy=false
          // URL取得後は候補リスト構造が変わるのでパネル差し替え（外枠・内部スクロール保持）
          refreshBulkMakerPanelUi(st)
        }
      }
    })
  }

  function bulkMakerPanelHtml(st){
    const panel=st.bulkMakerLookup
    if(!panel)return ''
    const running=!!panel.running&&!panel.finished
    const results=panel.results||[]
    const excluded=panel.excluded||[]
    const adoptSel=panel.adoptSelected||{}
    const adoptedCount=panel.adoptedCount||0
    const remainingConfirm=bulkMakerRemainingConfirmCount(panel)
    const progressLine=bulkMakerProgressLine(panel)
    const countLine=bulkMakerCountLine(panel)
    const exclNote=excluded.length
      ?`<p class="excelhint">メーカー品番が対象外のため除外 ${excluded.length} 件（別注・品番なし・空欄など）: ${excluded.slice(0,12).map(e=>esc(e.skuCode||'')).join('、')}${excluded.length>12?` …他${excluded.length-12}`:''}</p>`
      :''
    const dupNote=(panel.duplicateCollapsed||0)>0
      ?`<p class="excelhint">同じメーカー・品番・上下の重複検索を ${panel.duplicateCollapsed} 件まとめました（上下区分は区別します）。</p>`
      :''
    const rows=results.map((r,ri)=>{
      const codes=(r.skuCodes||[]).join('、')||'—'
      const cands=r.candidates||[]
      const status=r.status||''
      let candHtml=''
      if(cands.length){
        candHtml=`<ul class="pl-bulk-maker-cands">${cands.map((c,ci)=>{
          const key=bulkMakerAdoptSelectionKey(r,ci)
          const checked=!!adoptSel[key]
          const plannedSku=c.plannedRegisteredSku||r.plannedRegisteredSku||''
          const supp=(c.supplement==='上'||c.supplement==='下')?'':(c.supplement||'')
          const disabled=!!panel.running||!!r.adopted||st.busy
          const cleaned=c.cleanedName||c.name||''
          const originalTitle=c.originalTitle||c.title||''
          const urls=(Array.isArray(c.sourceUrls)&&c.sourceUrls.length?c.sourceUrls:(c.sourceUrl?[c.sourceUrl]:[]))
          const fs=c.fieldSources||{}
          const colorUnavailable=!!(c.colorUnavailable||(!c.color&&((c.missingFields||[]).includes('color'))))
          const keepColor=(!c.color&&(r.currentColor||''))?r.currentColor:''
          const adoptColorDisp=c.color||(keepColor?`${keepColor}（既存維持）`:'（色未取得・空上書きなし）')
          const srcBit=(key,fallback)=>{
            const f=fs[key]||{}
            const val=f.value||fallback||''
            const unavail=key==='color'&&(f.unavailable||colorUnavailable)&&!c.color
            return `${esc(key==='name'?'名':key==='color'?'色':'品番')} ${esc(unavail?'未取得':(val||'—'))}`+
              `${f.sourceLabel?`（${esc(f.sourceLabel)}）`:''}`+
              `${f.sourceUrl?` <a href="${esc(f.sourceUrl)}" target="_blank" rel="noopener">URL</a>`:''}`
          }
          return `<li>
            <label class="pl-bulk-maker-cand-pick">
              <input type="checkbox" data-pl-bulk-maker-pick="${esc(key)}" data-result-idx="${ri}" data-cand-idx="${ci}" ${checked?'checked':''} ${disabled?'disabled':''}>
              <span>
                <div class="excelhint">元掲載名: ${esc(originalTitle||'—')}</div>
                <strong>整理後: ${esc(cleaned||'—')}</strong>${c.color?`　色 ${esc(c.color)}`:(colorUnavailable?'　<span class="sub">色未取得</span>':'')}${supp?`　補足 ${esc(supp)}`:''}
                ${plannedSku?`　品番 ${esc(plannedSku)}`:''}
                ${c.makerColorCode?`　色番 ${esc(c.makerColorCode)}`:''}
                ${c.skuMatchEvidence?` <span class="sub">${esc(c.skuMatchEvidence)}</span>`:''}
                ${colorUnavailable?' <span class="badge">色補正未完了</span>':''}
                ${urls.length?` ${urls.map(u=>`<a href="${esc(u)}" target="_blank" rel="noopener">参照URL</a>`).join(' ')}`:''}
              </span>
            </label>
            <div class="excelhint">採用前確認: ${srcBit('name',cleaned)} ／ ${srcBit('color',c.color||'')} ／ ${srcBit('sku',plannedSku||'')}</div>
            ${colorUnavailable?`<div class="excelhint">色は取得できませんでした。名のみを色補正完了とはしません。既存色は空では上書きしません。</div>`:''}
            <div class="excelhint">変更前: 名 ${esc(r.currentName||'—')} / 色 ${esc(r.currentColor||'（空）')} / 品番 ${esc(r.currentSku||'—')}</div>
            <div class="excelhint">変更後: 名 ${esc(cleaned||'—')} / 色 ${esc(adoptColorDisp)} / 品番 ${esc(plannedSku||r.currentSku||'—')}</div>
          </li>`
        }).join('')}</ul>`
      }else if(r.referenceUrl){
        candHtml=`<p class="excelhint"><a href="${esc(r.referenceUrl)}" target="_blank" rel="noopener">参考ページ</a>（開いただけでは補正完了になりません）</p>`
      }
      const colorMissing=cands.some(c=>!!(c.colorUnavailable||(!(c.color||'').trim()&&(c.missingFields||[]).includes('color'))))
      const urlAssist=(!cands.length||makerLookupNeedsUrlAssist(status)||colorMissing)&&!r.adopted&&!panel.running
        ?makerUrlFetchFormHtml({
          scope:'bulk',
          inputId:`plBulkMakerUrl-${ri}`,
          btnId:`plBulkMakerUrlFetch-${ri}`,
          resultKey:String(ri),
          value:(panel.urlInputs&&panel.urlInputs[r.searchKey||ri])||'',
          disabled:!!st.busy||!!panel.running
        })
        :''
      const urlErr=(panel.urlFetchErrors&&panel.urlFetchErrors[r.searchKey||ri])
        ?`<p class="product-ledger-import-error" role="alert">${esc(panel.urlFetchErrors[r.searchKey||ri])}</p>`
        :''
      return `<tr class="pl-bulk-maker-result" data-bulk-status="${esc(status)}" data-bulk-result-idx="${ri}">
        <td>${esc(codes)}</td>
        <td>${esc(r.makerSku||'—')}${r.position?`（${esc(r.position)}）`:''}</td>
        <td><strong>${esc(r.statusLabel||bulkMakerStatusLabel(status))}</strong>${r.adopted?' <span class="badge">採用済</span>':''}${r.fromUrl?' <span class="sub">（URL指定）</span>':''}${r.note?`<div class="excelhint">${esc(r.note)}</div>`:''}${candHtml}${urlErr}${urlAssist}</td>
      </tr>`
    }).join('')
    const resultTable=results.length
      ?`<div class="pl-bulk-maker-results-wrap"><table class="data pl-bulk-maker-results">
        <thead><tr><th>品番（元行対応）</th><th>メーカー品番</th><th>結果・候補</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`
      :(running?'':`<p class="excelhint">取得結果はまだありません。</p>`)
    const adoptDisabled=st.busy||running||!Object.keys(adoptSel).some(k=>adoptSel[k])
    return `<div class="notice pl-bulk-maker-panel" role="region" aria-label="選択した商品のメーカー情報" data-pl-bulk-maker-panel>
      <div class="bar">
        <strong>選択した商品のメーカー情報</strong>
        <span class="sub" id="plBulkMakerProgress">${esc(progressLine)}</span>
        ${running?`<button type="button" class="quiet" id="plBulkMakerCancel">中止</button>`:''}
        <button type="button" class="quiet" id="plBulkMakerViewResults">結果を見る</button>
        ${panel.finished?`<button type="button" class="quiet" id="plBulkMakerClose">結果を閉じる</button>`:''}
      </div>
      <p class="excelhint" id="plBulkMakerCounts">${esc(countLine)}</p>
      ${exclNote}${dupNote}
      <p class="excelhint">取得だけでは登録予定は変わりません。候補を確認してから「選択した補正を採用」してください（最終登録は既存の登録確定操作）。上限 ${panel.maxTargets||40} ユニーク検索／間隔 ${panel.delaySec||0.8} 秒。無料の公開ページのみ。</p>
      ${resultTable}
      <div class="bar">
        <button type="button" class="primary" id="plBulkMakerAdopt" ${adoptDisabled?'disabled':''}>選択した補正を採用</button>
        <span class="excelhint" id="plBulkMakerAdoptMeta">採用済 ${adoptedCount}　確認残り ${remainingConfirm}</span>
      </div>
    </div>`
  }

  function mergeBulkMakerJobIntoState(st,job,prev){
    const prevAdopt=(prev&&prev.adoptSelected)||{}
    const prevResults=(prev&&prev.results)||[]
    const adoptedMap={}
    prevResults.forEach(r=>{
      if(r&&r.adopted)adoptedMap[r.searchKey||'']=true
    })
    const results=(job.results||[]).map(r=>{
      const copy=Object.assign({},r)
      if(adoptedMap[copy.searchKey||''])copy.adopted=true
      return copy
    })
    const adoptSelected=Object.assign({},prevAdopt)
    // 一致候補1件は確認しやすいよう初期選択（複数は手動）
    results.forEach(r=>{
      if(r.adoptableSingle&&!r.adopted){
        const key=bulkMakerAdoptSelectionKey(r,0)
        if(adoptSelected[key]==null)adoptSelected[key]=true
      }
    })
    st.bulkMakerLookup={
      jobId:job.jobId,
      running:!job.finished,
      finished:!!job.finished,
      done:job.done||0,
      total:job.total||0,
      currentSku:job.currentSku||'',
      currentMakerSku:job.currentMakerSku||'',
      counts:job.counts||{},
      results,
      excluded:job.excluded||[],
      limitSkipped:job.limitSkipped||[],
      selectedCount:job.selectedCount||0,
      excludedCount:job.excludedCount||0,
      uniqueSearchCount:job.uniqueSearchCount||0,
      duplicateCollapsed:job.duplicateCollapsed||0,
      maxTargets:job.maxTargets||40,
      delaySec:job.delaySec||0.8,
      error:job.error||'',
      adoptSelected,
      adoptedCount:(prev&&prev.adoptedCount)||0,
      urlInputs:(prev&&prev.urlInputs)||{},
      urlFetchErrors:(prev&&prev.urlFetchErrors)||{},
      _completionToasted:(prev&&prev._completionToasted)||false
    }
  }

  async function fetchMakerFromUrlForPanel(st,panel,url){
    const result=await st.api('/api/product-ledger-csv/maker-lookup/from-url',{
      url,
      manufacturer:panel.manufacturer||'',
      makerSku:panel.makerSku,
      currentName:panel.currentName||(panel.result&&panel.result.candidates&&panel.result.candidates[0]&&panel.result.candidates[0].diff&&panel.result.candidates[0].diff.currentName)||'',
      currentColor:panel.currentColor||'',
      currentSupplement:panel.currentSupplement||'',
      currentSku:panel.currentSku||panel.makerSku||'',
      sourceName:panel.sourceName||'',
      supplier:panel.supplier||''
    })
    return result
  }

  async function startBulkMakerLookup(st){
    const codes=selectedCodeList(st)
    if(!codes.length){st.toast('品番を選択してください。');return}
    if(st.bulkMakerLookup&&st.bulkMakerLookup.running){st.toast('一括取得が進行中です。');return}
    const codeSet=new Set(codes.map(String))
    const groups=((st.preview&&st.preview.skuGroups)||[]).filter(g=>codeSet.has(String(g.code)))
    const rowNoSet=new Set()
    groups.forEach(g=>(g.rowNos||[]).forEach(n=>rowNoSet.add(Number(n))))
    const rows=((st.preview&&st.preview.rows)||[]).filter(r=>rowNoSet.has(Number(r.rowNo)))
    // 対象外の事前カウント（サーバーでも再計算）
    let excludedHint=0
    groups.forEach(g=>{
      const mk=g.makerCorrection||{}
      const sku=mk.makerSku||''
      const ok=mk.bulkEligible!=null?!!mk.bulkEligible:!!sku
      if(!ok)excludedHint+=1
    })
    st.busy=true
    st.bulkMakerLookup={
      running:true,finished:false,done:0,total:0,currentSku:'',currentMakerSku:'',
      counts:{},results:[],excluded:[],adoptSelected:{},adoptedCount:0,
      excludedCount:excludedHint,maxTargets:40,delaySec:0.8
    }
    // 自動スクロールしない。開始は toast／進捗テキストのみ（「結果を見る」は任意）。
    paintDialog()
    try{
      const job=await st.api('/api/product-ledger-csv/maker-lookup/bulk/start',{
        selectedCodes:codes,
        skuGroups:groups,
        rows
      })
      mergeBulkMakerJobIntoState(st,job,st.bulkMakerLookup)
      if(job.excludedCount){
        st.toast(`対象外 ${job.excludedCount} 件を除外し、ユニーク検索 ${job.uniqueSearchCount||0} 件を開始しました。必要なら「結果を見る」でパネルへ移動できます。`)
      }else{
        st.toast(`メーカー情報の一括取得を開始（ユニーク検索 ${job.uniqueSearchCount||0} 件）。必要なら「結果を見る」でパネルへ移動できます。`)
      }
      if(!job.finished)startBulkMakerPoll(st)
      else{
        stopBulkMakerPoll(st)
        if(st.bulkMakerLookup)st.bulkMakerLookup._completionToasted=true
        st.toast('メーカー情報の一括取得が完了しました。必要なら「結果を見る」でパネルへ移動できます。')
      }
    }catch(e){
      st.bulkMakerLookup=Object.assign({},st.bulkMakerLookup,{
        running:false,finished:true,error:apiErrorMessage(e,'一括取得を開始できませんでした。')
      })
      st.toast(apiErrorMessage(e,'一括取得を開始できませんでした。'),6000)
      stopBulkMakerPoll(st)
    }finally{
      st.busy=false
      paintDialog()
    }
  }

  function startBulkMakerPoll(st){
    stopBulkMakerPoll(st)
    st._bulkMakerPoll=setInterval(()=>{pollBulkMakerLookup(st)},900)
  }

  async function pollBulkMakerLookup(st){
    const panel=st.bulkMakerLookup
    if(!panel||!panel.jobId){stopBulkMakerPoll(st);return}
    if(panel._polling)return
    panel._polling=true
    try{
      const prevSig=bulkMakerStructureSignature(panel)
      const job=await st.api('/api/product-ledger-csv/maker-lookup/bulk/status',{jobId:panel.jobId})
      mergeBulkMakerJobIntoState(st,job,panel)
      // 進捗更新でも外枠・候補リストのスクロール／チェック操作を乱さない
      const next=st.bulkMakerLookup
      const nextSig=bulkMakerStructureSignature(next)
      if(prevSig===nextSig&&document.querySelector('.pl-bulk-maker-panel')){
        patchBulkMakerProgressTexts(st)
      }else{
        refreshBulkMakerPanelUi(st)
      }
      if(job.finished){
        stopBulkMakerPoll(st)
        // 完了は toast／進捗のみ。自動スクロールしない。
        if(st.bulkMakerLookup&&!st.bulkMakerLookup._completionToasted){
          st.bulkMakerLookup._completionToasted=true
          st.toast('メーカー情報の一括取得が完了しました。必要なら「結果を見る」でパネルへ移動できます。')
        }
      }
    }catch(e){
      // 一時失敗は次の poll で再試行
      if(panel.running){
        /* keep polling */
      }else{
        stopBulkMakerPoll(st)
      }
    }finally{
      if(st.bulkMakerLookup)st.bulkMakerLookup._polling=false
    }
  }

  async function cancelBulkMakerLookup(st){
    const panel=st.bulkMakerLookup
    if(!panel||!panel.jobId)return
    try{
      const job=await st.api('/api/product-ledger-csv/maker-lookup/bulk/cancel',{jobId:panel.jobId})
      mergeBulkMakerJobIntoState(st,job,panel)
      st.toast('一括取得を中止しました（取得済み結果は保持）')
      paintDialog()
    }catch(e){
      st.toast(apiErrorMessage(e,'中止に失敗しました。'),6000)
    }
  }

  function isProcessingServicePreviewRow(st,rowNo){
    const row=((st.preview&&st.preview.rows)||[]).find(r=>Number(r.rowNo)===Number(rowNo))
    if(!row)return false
    const ps=row.processingService||{}
    const pc=row.parenClassification||{}
    if(ps.isPhysicalParts||row.physicalPartsView||pc.physicalPartsView)return false
    return !!(row.processingServiceView||pc.processingServiceView||ps.isProcessingService||ps.isProcessingVendor)
  }

  async function applyMakerCandidateToPreview(st,{scope,skuCode,rowNos,rowNo,cand,manufacturer,makerSku,sourceSku,usageNote}){
    const plannedSku=cand.plannedRegisteredSku||''
    const position=cand.position||''
    const cleaned=cand.cleanedName||cand.name||''
    let supplement=cand.supplement||''
    if(supplement==='上'||supplement==='下')supplement=''
    const panel=st.makerLookup||st.bulkMakerLookup||{}
    const res=(panel&&panel.result)||{}
    const srcSku=sourceSku||res.sourceSku||''
    const note=usageNote||res.usageNote||''
    const diff=(cand&&cand.diff)||{}
    const currentColor=diff.currentColor||(panel&&panel.currentColor)||''
    // 空の色で既存・手動色を消さない
    const adoptColor=(cand.color||'').trim()?cand.color:(currentColor||'')
    await st.api('/api/product-ledger-csv/maker-lookup/adopt',{
      manufacturer:cand.manufacturer||manufacturer||'',
      makerSku:makerSku,
      name:cleaned,
      color:cand.color||'',
      currentColor,
      supplement,
      position,
      plannedRegisteredSku:plannedSku,
      sourceUrl:cand.sourceUrl||'',
      sourceSku:srcSku,
      usageNote:note,
      originalTitle:cand.originalTitle||cand.title||'',
      makerColorCode:cand.makerColorCode||'',
      baseSku:cand.baseSku||''
    })
    const classPatch={
      plannedName:cleaned,
      color:adoptColor,
      supplement,
      position,
      plannedRegisteredSku:plannedSku,
      registeredCode:plannedSku,
      unclassified:[],
      userEdited:true,
      confirmed:true,
      acceptProposal:true,
      makerAdopted:true,
      makerSourceUrl:cand.sourceUrl||'',
      makerManufacturer:cand.manufacturer||manufacturer||'',
      sourceSku:srcSku,
      searchMakerSku:makerSku||'',
      usageNote:note,
      originalTitle:cand.originalTitle||cand.title||'',
      cleanedName:cleaned,
      makerColorUnavailable:!(cand.color||'').trim()
    }
    if(scope==='sku'){
      // 加工サービス行へメーカー商品名を表示名として流し込まない（加工名を唯一の表示元に保つ）
      ;(rowNos||[]).forEach(rn=>{
        if(isProcessingServicePreviewRow(st,rn))return
        setRowClassification(st,rn,classPatch)
      })
      const group=((st.preview&&st.preview.skuGroups)||[]).find(g=>String(g.code)===String(skuCode))
      const d=skuDecision(st,skuCode)
      if((d.decision||'')==='color_split'){
        // 呼び出し側で確認済み想定。ここでも安全側に merge へ
      }
      setSkuDecision(st,skuCode,{
        decision:'merge',
        officialName:cleaned,
        color:adoptColor,
        plannedRegisteredSku:plannedSku,
        position,
        makerAdopted:true,
        makerSourceUrl:cand.sourceUrl||'',
        makerManufacturer:cand.manufacturer||manufacturer||'',
        sourceSku:srcSku,
        searchMakerSku:makerSku||'',
        usageNote:note,
        originalTitle:cand.originalTitle||cand.title||'',
        cleanedName:cleaned,
        makerColorUnavailable:!(cand.color||'').trim()
      })
      if(group)applyMergeDefaults(st,skuCode,group)
      setSkuDecision(st,skuCode,{
        decision:'merge',
        officialName:cleaned,
        color:adoptColor,
        plannedRegisteredSku:plannedSku,
        position,
        makerAdopted:true,
        makerSourceUrl:cand.sourceUrl||'',
        makerManufacturer:cand.manufacturer||manufacturer||'',
        sourceSku:srcSku,
        searchMakerSku:makerSku||'',
        usageNote:note,
        originalTitle:cand.originalTitle||cand.title||'',
        cleanedName:cleaned,
        makerColorUnavailable:!(cand.color||'').trim()
      })
    }else if(rowNo!=null){
      if(!isProcessingServicePreviewRow(st,rowNo)){
        setRowClassification(st,rowNo,classPatch)
      }
    }
  }

  async function adoptBulkMakerSelections(st){
    const panel=st.bulkMakerLookup
    if(!panel||panel.running)return
    const adoptSel=panel.adoptSelected||{}
    const picks=[]
    ;(panel.results||[]).forEach((r,ri)=>{
      if(r.adopted)return
      ;(r.candidates||[]).forEach((c,ci)=>{
        const key=bulkMakerAdoptSelectionKey(r,ci)
        if(adoptSel[key])picks.push({result:r,cand:c,ri,ci,key})
      })
    })
    if(!picks.length){st.toast('採用する補正を選択してください。');return}
    // 色分割中の品番は確認
    const colorSplitCodes=[]
    picks.forEach(p=>{
      ;(p.result.skuCodes||[]).forEach(code=>{
        if((skuDecision(st,code).decision||'')==='color_split'&&!colorSplitCodes.includes(code)){
          colorSplitCodes.push(code)
        }
      })
    })
    if(colorSplitCodes.length){
      const ok=confirm(
        `選択した補正の採用で、色別登録中の ${colorSplitCodes.length} 品番を「同じ商品としてまとめる」に切り替えます。\n`+
        `品番: ${colorSplitCodes.slice(0,12).join('、')}${colorSplitCodes.length>12?` …他${colorSplitCodes.length-12}`:''}\n\nよろしいですか？`
      )
      if(!ok)return
    }
    st.busy=true
    paintDialog()
    let adopted=0
    try{
      for(const p of picks){
        const codes=p.result.skuCodes||[]
        for(const code of codes){
          const rowNos=(p.result.rowNosBySku&&p.result.rowNosBySku[code])||[]
          await applyMakerCandidateToPreview(st,{
            scope:'sku',
            skuCode:code,
            rowNos:rowNos.length?rowNos:(((st.preview.skuGroups||[]).find(g=>String(g.code)===String(code))||{}).rowNos||[]),
            cand:p.cand,
            manufacturer:p.result.manufacturer,
            makerSku:p.result.makerSku
          })
        }
        p.result.adopted=true
        adoptSel[p.key]=false
        adopted+=1
      }
      panel.adoptedCount=(panel.adoptedCount||0)+adopted
      const nextPreview=await st.api('/api/product-ledger-csv/preview',previewPayload(st))
      st.preview=nextPreview
      st.previewError=''
      snapshotAppliedSkuDecisions(st)
      st.toast(`メーカー補正を ${adopted} 件採用しました（プレビュー反映のみ。台帳未登録）`)
    }catch(e){
      st.toast(apiErrorMessage(e,'一括採用に失敗しました。'),6000)
    }finally{
      st.busy=false
      paintDialog()
    }
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
        <td class="lc-status">${esc(r.statusLabel||'')}${pc.needsConfirm?' <span class="badge">括弧</span>':''}${(r.processingServiceView||(r.processingService&&r.processingService.isProcessingVendor))?' <span class="badge">加工</span>':''}</td>
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
    const usageFilter=st.openingUsageFilter||''
    const allPlans=os.plans||[]
    const filteredPlans=usageFilter==='販売'
      ?allPlans.filter(p=>{
        const t=p.target||{}
        const u=t.usageKind||t.handlingType||''
        const emb=t.embroidery||''
        return u==='販売'||emb==='販売'||String(emb).endsWith('｜販売')
      })
      :usageFilter==='other'
        ?allPlans.filter(p=>{
          const t=p.target||{}
          const u=t.usageKind||t.handlingType||''
          const emb=t.embroidery||''
          return !(u==='販売'||emb==='販売'||String(emb).endsWith('｜販売'))
        })
        :allPlans
    const plans=filteredPlans.slice(0,60)
    const rows=plans.map(p=>{
      const t=p.target||{}
      const csv=p.csv||{}
      const place=p.place||{}
      const stock=p.stock||{}
      const sel=st.openingSelections[String(p.rowNo)]||{}
      const usage=t.usageKind||t.handlingType||''
      const emb=t.embroidery||''
      const usageLabel=usage==='販売'||emb==='販売'||String(emb).endsWith('｜販売')
        ?(emb&&emb!=='販売'?emb:'販売')
        :(emb||'—')
      const qtyCheck=p.status==='qty_diff'
        ?`<label class="check"><input type="checkbox" data-pl-open-qty="${p.rowNo}" ${sel.updateQty?'checked':''}>CSV数量へ更新</label>`
        :''
      return `<tr>
        <td>${p.rowNo}</td>
        <td>${esc(p.statusLabel||p.status)}</td>
        <td>${esc(t.officialName||t.productName||'')}<div class="excelhint">元: ${esc(csv.productName||'')}</div></td>
        <td>${esc(usageLabel)}${t.usageConflict?' <span class="badge">用途要確認</span>':''}</td>
        <td>${esc(t.size||csv.size||'')}</td>
        <td>${esc(p.conditionLabel||'')}</td>
        <td class="num">${p.qty!=null?esc(String(p.qty)):'—'}</td>
        <td>${esc(place.siteName||'')}${place.shelfName?` / ${esc(place.shelfName)}`:''}${place.usedDefaultSite?' <span class="excelhint">(空→未確認)</span>':''}</td>
        <td class="num">${p.unitCost!=null?esc(String(p.unitCost)):'—'}</td>
        <td>${stock.exists!=null?`現在 ${esc(String(stock.currentQty))}`:'—'}</td>
        <td>${esc((p.reasons||[]).join(' '))}${p.productRegistrationExcluded?` <span class="badge">${esc(p.productRegistrationExcludedLabel||'商品登録時に除外した項目')}</span>`:''}${qtyCheck}</td>
      </tr>`
    }).join('')
    const more=filteredPlans.length>60?`<p class="excelhint">先頭60件を表示（絞込後 ${filteredPlans.length} / 全 ${allPlans.length} 件）。</p>`:''
    const exclHint=((os.plans||[]).some(p=>p.productRegistrationExcluded))
      ?'<p class="excelhint">「商品登録時に除外した項目」は表示のみです。初期在庫数量を自動では外しません。登録先が無い行は確認対象のままです。</p>'
      :''
    return `<section class="product-ledger-opening-preview">
      <h3>初期在庫の登録予定</h3>
      <p class="notice">${esc(os.note||sum.note||'確認後に初期在庫を登録します。数量・新品中古・拠点・単価を確認してください。')}</p>
      ${exclHint}
      <p><strong>${esc(countLine)}</strong></p>
      <div class="bar">
        <label>取扱種別<select id="plOpenUsageFilter" ${st.busy?'disabled':''}>
          <option value="" ${!usageFilter?'selected':''}>すべて</option>
          <option value="販売" ${usageFilter==='販売'?'selected':''}>販売</option>
          <option value="other" ${usageFilter==='other'?'selected':''}>販売以外</option>
        </select></label>
        <span class="excelhint">表示 ${filteredPlans.length} / ${allPlans.length} 件（在庫一覧の加工仕様フィルタでも「販売」を選べます）</span>
      </div>
      ${masterBits.length?`<p class="excelhint">${masterBits.join('　')}</p>`:''}
      <div class="legacy-csv-table-wrap"><table class="data sku-compare-table">
        <thead><tr><th>行</th><th>判定</th><th>登録先商品</th><th>取扱</th><th>サイズ</th><th>新品中古</th><th>数量</th><th>拠点/棚</th><th>単価</th><th>既存在庫</th><th>理由</th></tr></thead>
        <tbody>${rows||'<tr><td colspan="11">該当行なし</td></tr>'}</tbody>
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

  /** 内容が短くなったときもはみ出さないようクランプして復元 */
  function applyScrollPos(el,pos){
    if(!el||!pos)return
    const maxTop=Math.max(0,(el.scrollHeight||0)-(el.clientHeight||0))
    const maxLeft=Math.max(0,(el.scrollWidth||0)-(el.clientWidth||0))
    const top=Math.min(Math.max(0,pos.top||0),maxTop)
    const left=Math.min(Math.max(0,pos.left||0),maxLeft)
    el.scrollTop=top
    el.scrollLeft=left
  }

  function ledgerBodyEl(dlg){
    return dlg?dlg.querySelector('.legacy-csv-detail-body'):null
  }

  /** フォーカス復元用キー（preventScroll とセットで使う） */
  function captureFocusKey(dlg){
    const el=typeof document!=='undefined'?document.activeElement:null
    if(!el||!dlg||(typeof dlg.contains==='function'&&!dlg.contains(el)))return null
    if(el.id)return {type:'id',value:el.id}
    const attrs=[
      'data-pl-sku-check','data-pl-bulk-maker-pick','data-pl-unified-expand',
      'data-pl-sku-expand','data-pl-confirmed-expand','data-pl-sku-dec',
      'data-pl-maker-url-input','data-pl-official-name-edit','data-pl-official-color'
    ]
    for(let i=0;i<attrs.length;i++){
      const a=attrs[i]
      const v=el.getAttribute&&el.getAttribute(a)
      if(v!=null&&v!=='')return {type:'attr',attr:a,value:v}
    }
    return null
  }

  function restoreFocusKey(dlg,key){
    if(!key||!dlg)return
    let el=null
    if(key.type==='id')el=document.getElementById(key.value)
    else if(key.type==='attr'&&key.attr){
      el=dlg.querySelector(`[${key.attr}="${CSS.escape(String(key.value))}"]`)
    }
    if(el&&typeof el.focus==='function'){
      try{el.focus({preventScroll:true})}catch(_){try{el.focus()}catch(__){}}
    }
  }

  // 連続 paint 時に古い復元が後から走らないよう、共有ポリシーは1系統だけ
  let _ledgerScrollRestoreToken=0

  /**
   * 共有スクロール維持ポリシー（外枠・内側一覧・メーカー候補の V/H）。
   * 操作行への追従・先頭ジャンプはしない。最新の1回だけ適用。
   */
  function scheduleLedgerScrollRestore(dlg,saved,opts,focusKey){
    if(!saved&&!focusKey)return
    const token=++_ledgerScrollRestoreToken
    const apply=()=>{
      if(token!==_ledgerScrollRestoreToken){
        scrollLog('restore-superseded',token,_ledgerScrollRestoreToken)
        return
      }
      if(saved)restoreLedgerScroll(dlg,saved,opts||{})
      if(focusKey)restoreFocusKey(dlg,focusKey)
    }
    // 1系統のみ。連続 paint は token で古い復元を捨てる（二重 restore を積まない）。
    apply()
  }

  /** 縦スクロール本体（モーダル）内で見えている先頭行と相対位置（追従用ではなく復元用） */
  function captureVisibleSkuAnchor(dlg){
    const body=ledgerBodyEl(dlg)
    if(!body)return null
    const br=body.getBoundingClientRect()
    const rows=dlg.querySelectorAll('tr.sku-list-row[data-sku-code],tr.sku-list-row[data-unified-id]')
    for(let i=0;i<rows.length;i++){
      const row=rows[i]
      const rr=row.getBoundingClientRect()
      if(rr.bottom<=br.top+4)continue
      if(rr.top>=br.bottom-4)break
      return {
        code:row.getAttribute('data-sku-code')||row.getAttribute('data-unified-id')||'',
        offset:rr.top-br.top,
        section:row.getAttribute('data-sku-section')||''
      }
    }
    return null
  }

  /**
   * 画面上の入力・チェックを state へ吸い上げ（再照合・除外ローカル反映の前に呼ぶ）。
   * 加工名・補足・確認チェック・品番編集などを保持する。
   */
  function harvestFormEdits(st){
    if(!st)return
    document.querySelectorAll('[data-pl-official-name-edit]').forEach(el=>{
      setSkuDecision(st,el.getAttribute('data-pl-official-name-edit'),{officialName:el.value})
    })
    document.querySelectorAll('[data-pl-official-color]').forEach(el=>{
      setSkuDecision(st,el.getAttribute('data-pl-official-color'),{color:el.value})
    })
    document.querySelectorAll('[data-pl-defer-reason]').forEach(el=>{
      setSkuDecision(st,el.getAttribute('data-pl-defer-reason'),{deferReason:el.value})
    })
    document.querySelectorAll('[data-pl-paren-name]').forEach(el=>{
      setRowClassification(st,el.getAttribute('data-pl-paren-name'),{plannedName:el.value})
    })
    document.querySelectorAll('[data-pl-paren-color]').forEach(el=>{
      setRowClassification(st,el.getAttribute('data-pl-paren-color'),{color:el.value})
    })
    document.querySelectorAll('[data-pl-paren-proc]').forEach(el=>{
      const rowNo=el.getAttribute('data-pl-paren-proc')
      const patch={processing:el.value}
      if(el.getAttribute('data-pl-processing-name-sync')){
        patch.plannedName=el.value
      }
      setRowClassification(st,rowNo,patch)
    })
    document.querySelectorAll('[data-pl-paren-mat]').forEach(el=>{
      setRowClassification(st,el.getAttribute('data-pl-paren-mat'),{material:el.value})
    })
    document.querySelectorAll('[data-pl-paren-purpose]').forEach(el=>{
      setRowClassification(st,el.getAttribute('data-pl-paren-purpose'),{partsUsage:el.value})
    })
    document.querySelectorAll('[data-pl-paren-supp]').forEach(el=>{
      setRowClassification(st,el.getAttribute('data-pl-paren-supp'),{supplement:el.value})
    })
    document.querySelectorAll('[data-pl-paren-unc]').forEach(el=>{
      const v=el.value.trim()
      setRowClassification(st,el.getAttribute('data-pl-paren-unc'),{unclassified:v?v.split(/[、,]/).map(s=>s.trim()).filter(Boolean):[]})
    })
    document.querySelectorAll('[data-pl-unc-move]:checked').forEach(el=>{
      setRowClassification(st,el.getAttribute('data-pl-unc-move'),{unclassifiedMove:el.value})
    })
    document.querySelectorAll('[data-pl-register-kind]').forEach(el=>{
      setRowClassification(st,el.getAttribute('data-pl-register-kind'),{registerKind:el.value})
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
    document.querySelectorAll('[data-pl-sep-service]').forEach(el=>{
      const code=el.getAttribute('data-pl-sep-service')
      const key=el.getAttribute('data-pl-sep-key')
      const d=skuDecision(st,code)
      const plans={...(d.plans||{})}
      plans[key]={...(plans[key]||{}),serviceName:el.value}
      setSkuDecision(st,code,{plans})
    })
    document.querySelectorAll('[data-pl-sep-vendor]').forEach(el=>{
      const code=el.getAttribute('data-pl-sep-vendor')
      const key=el.getAttribute('data-pl-sep-key')
      const d=skuDecision(st,code)
      const plans={...(d.plans||{})}
      plans[key]={...(plans[key]||{}),vendorName:el.value}
      setSkuDecision(st,code,{plans})
    })
    document.querySelectorAll('[data-pl-sep-supp]').forEach(el=>{
      const code=el.getAttribute('data-pl-sep-supp')
      const key=el.getAttribute('data-pl-sep-key')
      const d=skuDecision(st,code)
      const plans={...(d.plans||{})}
      plans[key]={...(plans[key]||{}),supplement:el.value}
      setSkuDecision(st,code,{plans})
    })
    document.querySelectorAll('[data-pl-sep-approach]').forEach(el=>{
      const code=el.getAttribute('data-pl-sep-approach')
      const key=el.getAttribute('data-pl-sep-key')
      const d=skuDecision(st,code)
      const plans={...(d.plans||{})}
      plans[key]={...(plans[key]||{}),approach:el.value}
      setSkuDecision(st,code,{plans})
    })
    // 色別登録先・社内管理品番は方針切替でも消さない
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
  }

  /** paintDialog の innerHTML 全描画で消えるスクロールを退避する（外枠・内側・メーカー候補の V/H） */
  function captureLedgerScroll(dlg){
    const wraps={}
    dlg.querySelectorAll('.sku-compare-wrap').forEach((el,i)=>{
      wraps[el.closest('[data-sku-section-block]')?.getAttribute('data-sku-section-block')||`wrap${i}`]=readScrollPos(el)
    })
    const tables={}
    dlg.querySelectorAll('.legacy-csv-table-wrap').forEach((el,i)=>{
      tables[el.closest('[data-sku-section-block]')?.getAttribute('data-sku-section-block')||`table${i}`]=readScrollPos(el)
    })
    const body=ledgerBodyEl(dlg)
    const visibleAnchor=captureVisibleSkuAnchor(dlg)
    const snap={
      body:readScrollPos(body),
      wrap:readScrollPos(dlg.querySelector('.sku-compare-wrap')),
      wraps,
      sections:readScrollPos(dlg.querySelector('.sku-judgment-sections')),
      table:readScrollPos(dlg.querySelector('.legacy-csv-table-wrap')),
      tables,
      makerResults:readScrollPos(dlg.querySelector('.pl-bulk-maker-results-wrap')),
      visibleAnchor
    }
    scrollLog('capture',snap)
    return snap
  }

  /**
   * 表示中だった行を同じ相対オフセットへ（ビューポート維持用）。
   * section 指定時はその区分内だけ探す（確認済みへ移った行を追わない）。
   * 縦の主スクローラーは .legacy-csv-detail-body（一覧 wrap は横専用）。
   */
  function ensureSkuRowInView(dlg,code,opts){
    opts=opts||{}
    if(!code)return false
    const key=CSS.escape(String(code))
    const sec=opts.section!=null&&opts.section!==''?CSS.escape(String(opts.section)):''
    let row=null
    if(sec){
      // 同じ区分に残っているときだけ。別区分（確認済み等）への chase はしない。
      row=dlg.querySelector(`tr.sku-list-row[data-sku-section="${sec}"][data-sku-code="${key}"]`)
        ||dlg.querySelector(`tr.sku-list-row[data-sku-section="${sec}"][data-unified-id="${key}"]`)
    }else{
      row=dlg.querySelector(`tr.sku-list-row[data-sku-code="${key}"]`)
        ||dlg.querySelector(`tr.sku-list-row[data-unified-id="${key}"]`)
    }
    if(!row){
      scrollLog('anchor-missing',code,opts.section||'')
      return false
    }
    const body=ledgerBodyEl(dlg)
    if(body){
      const br=body.getBoundingClientRect()
      const rr=row.getBoundingClientRect()
      const preferred=opts.offset!=null?opts.offset:Math.min(140,Math.max(48,body.clientHeight*0.22))
      const delta=rr.top-br.top-preferred
      if(Math.abs(delta)>2)body.scrollTop+=delta
    }
    // 横は capture/restore の scrollLeft で維持。ここでは chase しない。
    return true
  }

  /** 表示行が区分移動・削除で消えたとき、同区分→全体の近傍行を同じオフセットへ */
  function restoreNearbySkuInView(dlg,saved,movedCode){
    if(!saved||!saved.visibleAnchor)return
    const body=ledgerBodyEl(dlg)
    if(!body)return
    const sec=saved.visibleAnchor.section||''
    const offset=saved.visibleAnchor.offset
    const sel=sec
      ?`tr.sku-list-row[data-sku-section="${CSS.escape(sec)}"]`
      :'tr.sku-list-row[data-sku-code],tr.sku-list-row[data-unified-id]'
    let rows=[].slice.call(dlg.querySelectorAll(sel))
    if(!rows.length){
      rows=[].slice.call(dlg.querySelectorAll('tr.sku-list-row[data-sku-code],tr.sku-list-row[data-unified-id]'))
    }
    if(!rows.length){
      // 行が無く内容が短いときは退避位置をクランプするだけ
      if(saved.body)applyScrollPos(body,saved.body)
      return
    }
    const br=body.getBoundingClientRect()
    let best=null
    let bestDist=Infinity
    rows.forEach(r=>{
      const c=r.getAttribute('data-sku-code')||r.getAttribute('data-unified-id')
      if(movedCode&&c===movedCode)return
      const dist=Math.abs((r.getBoundingClientRect().top-br.top)-(offset!=null?offset:80))
      if(dist<bestDist){bestDist=dist;best=r}
    })
    const pick=best||rows[0]
    if(pick){
      const pickCode=pick.getAttribute('data-sku-code')||pick.getAttribute('data-unified-id')
      const pickSec=pick.getAttribute('data-sku-section')||''
      ensureSkuRowInView(dlg,pickCode,{offset,section:pickSec})
    }
  }

  /**
   * 共有スクロール復元。操作行（opts.anchorCode）への追従はしない。
   * ビューに見えていた行＋各スクロール領域の V/H のみ復元。
   */
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
    const tableEls=dlg.querySelectorAll('.legacy-csv-table-wrap')
    if(opts.resetTableScroll){
      tableEls.forEach(table=>{table.scrollTop=0;table.scrollLeft=0})
    }else if(saved.tables){
      tableEls.forEach((el,i)=>{
        const key=el.closest('[data-sku-section-block]')?.getAttribute('data-sku-section-block')||`table${i}`
        applyScrollPos(el,saved.tables[key]||saved.table)
      })
    }else{
      applyScrollPos(dlg.querySelector('.legacy-csv-table-wrap'),saved.table)
    }
    // メーカー候補リスト（内側スクロール）の縦横も復元
    if(!opts.resetListScroll){
      applyScrollPos(dlg.querySelector('.pl-bulk-maker-results-wrap'),saved.makerResults)
    }
    // 操作行 chase 禁止。見えていた区分の行だけ維持。区分移動（確認済み等）なら近傍。
    if(!opts.resetListScroll&&saved.visibleAnchor){
      const viewCode=saved.visibleAnchor.code||''
      const viewSec=saved.visibleAnchor.section||''
      const offset=saved.visibleAnchor.offset
      if(viewCode){
        if(!ensureSkuRowInView(dlg,viewCode,{offset,section:viewSec})){
          restoreNearbySkuInView(dlg,saved,viewCode)
        }
      }else{
        restoreNearbySkuInView(dlg,saved,'')
      }
    }
    scrollLog('restore',{opts,body:readScrollPos(body),anchor:saved.visibleAnchor&&saved.visibleAnchor.code})
  }

  /**
   * @param {{preserveScroll?:boolean,resetListScroll?:boolean,resetTableScroll?:boolean}} [opts]
   * preserveScroll 既定 true（初回描画・明示 false 以外は V/H を復元）。
   * ページ切替・検索変更時は resetListScroll / resetTableScroll で一覧だけ先頭へ。
   * 操作行への scroll chase（旧 anchorCode）は行わない。
   */
  function paintDialog(opts){
    opts=opts||{}
    const st=ensureState()
    const dlg=document.getElementById('dialog')
    if(!dlg||!st.open)return
    const hadBody=!!dlg.querySelector('.legacy-csv-detail-body')
    const saved=(opts.preserveScroll!==false&&hadBody)?captureLedgerScroll(dlg):null
    const focusKey=(opts.preserveScroll!==false&&hadBody)?captureFocusKey(dlg):null
    const review=(st.preview&&st.preview.reviewCount)||0
    const skuPending=(st.preview&&st.preview.skuDecisionPending)||0
    const statusCounts=st.preview?judgmentStatusCounts(st):null
    const registerBlocked=st.preview?productsRegisterBlocked(st):true
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
          <p><strong>${countLine(st.preview.countsRaw)}</strong>
           　確認リスト未解決 ${statusCounts?statusCounts.unresolved:0}件　確認事項 ${statusCounts?statusCounts.issueCount:0}
           　未承認 ${statusCounts?statusCounts.unapproved:0}
           　登録しない ${statusCounts?statusCounts.rowExcluded:0}行
           　（参考: 行要確認 ${review} / 品番未決定 ${skuPending}）
            ${registerBlocked
              ?(pendingRematchCount(st)>0
                ?' — 未反映の方針変更があります。再照合してください。'
                :' — 未解決または未承認が残っているため登録できません。保留または「この行を登録しない」で登録対象から外せます。')
              :' — 確認リストの未解決が0で承認も完了しているため登録可能です。'}</p>
          <p class="excelhint">${esc(plannedLine(st))}</p>
          ${outcomeSummaryHtml(st)}
          ${pendingRematchBannerHtml(st)}
          ${skuGroupsHtml(st)}
          <div class="bar product-ledger-preview-actions">
            <button type="button" id="plRePreview" class="${pendingRematchCount(st)?'primary':''}" ${st.busy?'disabled':''}>選択を反映して再照合</button>
            <button type="button" id="plCommit" class="primary" ${st.busy||!canWrite||registerBlocked?'disabled':''} title="${pendingRematchCount(st)?'再照合してください':''}">確認して商品を登録（保留は除外）</button>
          </div>
          ${st.makerLookup&&st.makerLookup.scope==='row'?makerLookupPanelHtml(st,{forRowNo:st.makerLookup.rowNo}):''}
          ${sourceRowsPanelHtml(st)}`):''
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
    // 共有ポリシー1回のみ（操作行 chase / メーカー自動 scroll はしない）
    scheduleLedgerScrollRestore(dlg,saved,opts,focusKey)
    // 読み込み失敗メッセージは先頭へ戻す描画（preserveScroll:false）のときだけ見える位置へ。
    if(st.previewError&&!saved){
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
    st.appliedSkuDecisions={}
    st.rowClassifications={}
    st.rowExclusions={}
    st.rowExcludeSnapshots={}
    st.makerLookup=null
    st.bulkMakerLookup=null
    stopBulkMakerPoll(st)
    st.selectedCodes={}
    st.expandedCodes={}
    st.sectionOpen=Object.assign({},SECTION_DEFAULTS)
    st.sectionPages={unified:1,individual:1,bulk:1,approved:1,auto:1,hold:1,rowExcluded:1,separate:1}
    st.expandedUnified={}
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
    const usageFilter=$('#plOpenUsageFilter')
    if(usageFilter)usageFilter.onchange=()=>{
      st.openingUsageFilter=usageFilter.value||''
      paintDialog({preserveScroll:true})
    }
    document.querySelectorAll('[data-pl-open-qty]').forEach(el=>{
      el.onchange=()=>{
        const rowNo=el.getAttribute('data-pl-open-qty')
        st.openingSelections[String(rowNo)]=Object.assign({},st.openingSelections[String(rowNo)]||{},{updateQty:!!el.checked})
      }
    })
    const runPreview=async(previewOpts)=>{
      previewOpts=previewOpts&&typeof previewOpts==='object'?previewOpts:{}
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
        // フォーム値を吸い上げ（除外ローカル反映と同じ経路）
        harvestFormEdits(st)
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
          // 除外判断を安定キーで付け直し（表示順変更・自動整理で別行へ飛ばない）
          rebindExclusionsAfterPreview(st)
          // 自動提案をクライアント決定へ反映（手動は上書きしない／除外を勝手に戻さない）
          ;((st.preview&&st.preview.skuGroups)||[]).forEach(g=>{
            if(skuDecision(st,g.code).decision==='merge')applyAuxDefaultsForMerge(st,g.code,g)
          })
          // 成功時のみ未反映を解消（失敗時は appliedSkuDecisions を触らない）
          snapshotAppliedSkuDecisions(st)
          clearRowClassificationPending(st)
          // 元行一覧は詳細確認用。既定はすべて表示（確認作業は統合リスト側）
          st.statusFilter=''
          if(st.sectionOpen)st.sectionOpen.sourceRows=false
          st.page=1
        }else{
          snapshotAppliedSkuDecisions(st)
          clearRowClassificationPending(st)
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
    const rePending=$('#plRePreviewPending');if(rePending)rePending.onclick=runPreview
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
    const confirmedQ=$('#plConfirmedQ');if(confirmedQ)confirmedQ.oninput=()=>{
      st.confirmedQ=confirmedQ.value
      if(!st.sectionPages)st.sectionPages={}
      st.sectionPages.confirmed=1
      // 検索時のみ一覧先頭へ。方針移動の再描画では resetListScroll を付けない（スクロール維持）
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
        if(key==='unified')st.sectionOpen.individual=st.sectionOpen.unified
        if(key==='individual')st.sectionOpen.unified=st.sectionOpen.individual
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-unified-expand]').forEach(el=>{
      el.onclick=()=>{
        const id=el.getAttribute('data-pl-unified-expand')
        if(!st.expandedUnified)st.expandedUnified={}
        st.expandedUnified[id]=!st.expandedUnified[id]
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
      const unifiedOpen=st.sectionOpen&&(st.sectionOpen.unified!=null?st.sectionOpen.unified:st.sectionOpen.individual)
      if(unifiedOpen){
        pageSlice(st,'unified',buildUnifiedReviewItems(st).filter(it=>matchesUnifiedFilters(st,it))).pageItems.forEach(it=>{
          if(it.kind==='sku'&&it.code)pick.push({code:it.code})
        })
      }
      if(st.sectionOpen&&st.sectionOpen.bulk){
        pageSlice(st,'bulk',part.bulkUnapproved.filter(g=>matchesSkuFilters(st,g))).pageItems.forEach(g=>pick.push(g))
      }
      pick.forEach(g=>{st.selectedCodes[g.code]=true})
      st.toast(pick.length
        ?`開いている区分の表示ページ ${pick.length} 品番を選択しました`
        :'開いている区分に選択できる品番がありません')
      // 一覧・詳細は作り直さない（選択状態・件数・ボタンのみ）
      syncSkuSelectionUi(st)
    }
    const clearSel=$('#plSkuClearSel')
    if(clearSel)clearSel.onclick=()=>{st.selectedCodes={};syncSkuSelectionUi(st)}
    const bulkMakerFetch=$('#plBulkMakerFetch')
    if(bulkMakerFetch)bulkMakerFetch.onclick=()=>startBulkMakerLookup(st)
    bindBulkMakerPanelHandlers()
    const bulkApprove=$('#plSkuBulkApproveSection')
    if(bulkApprove)bulkApprove.onclick=()=>{
      // 一括承認は「一括承認候補」区分の未承認のみ。確認リスト（旧・個別確認が必要）は絶対に含めない。
      // 折りたたみ状態でも承認可能。開閉だけでは decision を変えない。
      const part=partitionJudgmentGroups(st)
      const bulk=part.bulkUnapproved.slice()
      if(!bulk.length){st.toast('未承認の一括承認候補がありません。');return}
      if(part.individual.length){
        // 確認リスト側の未解決は対象外であることを明示（混入防止）
      }
      const pages=Math.max(1,Math.ceil(bulk.length/SKU_PAGE_SIZE))
      const lines=bulk.slice(0,15).map(g=>{
        const name=plannedNameForGroup(st,g)||'（仮代表名）'
        const sup=plannedSupplierDisplay(st,g)
        return `・${g.code}: ${name} / 通常仕入先 ${sup}`
      })
      const more=bulk.length>15?`\n…他 ${bulk.length-15} 品番`:''
      const jc=judgmentStatusCounts(st)
      const msg=`【承認対象スコープ】一括承認候補の未承認 ${bulk.length} 件すべて（${pages}ページ分）\n確認リストの未解決 ${jc.unresolved} 件は含めません。\n品番ごとに予定の代表名・通常仕入先を使います（他品番の仕入先は割り当てません）。\n承認後は「確認済み一覧」（反映待ち→再照合後に確認済み）へ移ります。折りたたみだけでは承認されません。確認済み ≠ 登録完了です。\n\n${lines.join('\n')}${more}\n\nよろしいですか？`
      if(!confirm(msg))return
      harvestFormEdits(st)
      bulk.forEach(g=>applyMergeDefaults(st,g.code,g))
      const pendingN=pendingRematchCount(st)
      st.toast(pendingN
        ?`一括承認候補 ${bulk.length} 件を確認済み一覧へ移しました【反映待ち ${pendingN}】。「選択を反映して再照合」で登録予定を更新してください。`
        :`一括承認候補 ${bulk.length} 件を確認済み一覧へ移しました（確認リスト未解決 ${jc.unresolved} 件は対象外）`)
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
      harvestFormEdits(st)
      targets.forEach(g=>{
        setSkuDecision(st,g.code,{decision:'hold',deferReason:skuDecision(st,g.code).deferReason||'一括保留'})
      })
      const pendingN=pendingRematchCount(st)
      st.toast(pendingN
        ?`${targets.length} 品番を保留にしました【未反映 ${pendingN}】。再照合で登録予定へ反映します。`
        :`${targets.length} 品番を保留にしました`)
      paintDialog()
    }

    document.querySelectorAll('[data-pl-sku-check]').forEach(el=>{
      el.onchange=()=>{
        const code=el.getAttribute('data-pl-sku-check')
        st.selectedCodes[code]=!!el.checked
        // チェックだけでは検索・再照合・承認・登録を開始しない。画面全体も再描画しない。
        syncSkuSelectionUi(st)
      }
    })
    document.querySelectorAll('[data-pl-sku-expand]').forEach(el=>{
      el.onclick=()=>{
        const code=el.getAttribute('data-pl-sku-expand')
        st.expandedCodes[code]=!st.expandedCodes[code]
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-confirmed-expand]').forEach(el=>{
      el.onclick=()=>{
        const code=el.getAttribute('data-pl-confirmed-expand')
        if(!st.expandedConfirmed)st.expandedConfirmed={}
        st.expandedConfirmed[code]=!st.expandedConfirmed[code]
        paintDialog()
      }
    })
    document.querySelectorAll('[data-pl-confirmed-change]').forEach(el=>{
      el.onclick=()=>{
        const code=el.getAttribute('data-pl-confirmed-change')
        if(!st.expandedConfirmed)st.expandedConfirmed={}
        st.expandedConfirmed[code]=true
        if(!st.sectionOpen)st.sectionOpen=Object.assign({},SECTION_DEFAULTS)
        st.sectionOpen.confirmed=true
        st.toast('詳細で対応を選び直せます。必須が揃うまで確認リストへ戻ります。')
        paintDialog()
      }
    })

    document.querySelectorAll('[data-pl-sku-dec]').forEach(el=>{
      el.onclick=()=>{
        if(st.busy)return
        const code=el.getAttribute('data-pl-sku-dec')
        const dec=el.getAttribute('data-dec')
        const group=((st.preview&&st.preview.skuGroups)||[]).find(g=>g.code===code)
        // 方針選択は編集状態へ保存するだけ。全件再照合・CSV再読込・メーカー検索はしない。
        harvestFormEdits(st)
        if(dec==='merge'&&group){
          applyMergeDefaults(st,code,group)
          st.expandedCodes[code]=true
          if(skuDecisionPendingRematch(st,code)){
            const moved=userActionChoiceComplete(st,group)
            st.toast(moved
              ?'まとめるを選択しました【反映待ち】。確認済み一覧へ移しました。再照合で登録予定を更新してください。'
              :'まとめるを選択しました【未反映】。残課題がある間は確認リストに残します。')
          }
        }else if(dec==='color_split'){
          // 色別の colorPlans / 個別整理 plans 等は方針切替で消さない
          setSkuDecision(st,code,{decision:'color_split'})
          st.expandedCodes[code]=true
          st.toast('色別に分けて登録を選択しました【反映待ち】。確認済み一覧へ移しました。登録予定は再照合後に設定できます。')
        }else{
          setSkuDecision(st,code,{decision:dec})
          if(dec==='separate'||dec==='hold')st.expandedCodes[code]=true
          if(skuDecisionPendingRematch(st,code)){
            st.toast(dec==='hold'
              ?'保留を選択しました【未反映】。保留一覧へ移します（確認済み一覧には混ぜません）。'
              :'個別に整理を選択しました【反映待ち】。確認済み一覧へ移しました。再照合で登録予定へ反映します。')
          }
        }
        // 軽いUI更新のみ。確認済み移動でも操作行 chase せず、ビューポートを維持。
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
        // 方針・整理方法の選択は編集状態のみ。再照合は「選択を反映して再照合」
        harvestFormEdits(st)
        const code=el.getAttribute('data-pl-sep-approach')
        const key=el.getAttribute('data-pl-sep-key')
        if(el.value==='as_process_service'){
          initSeparateProcessServicePlan(st,code,key)
        }else{
          const d=skuDecision(st,code)
          const plans={...(d.plans||{})}
          plans[key]={...(plans[key]||{}),approach:el.value}
          setSkuDecision(st,code,{plans})
        }
        // スクロール chase なし（方針選択と同じく paintDialog() のみ）
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
      el.onchange=()=>{
        const rowNo=el.getAttribute('data-pl-paren-proc')
        const patch={processing:el.value}
        if(el.getAttribute('data-pl-processing-name-sync')){
          patch.plannedName=el.value
        }
        // 加工欄の入力だけでは登録区分を切り替えない（再照合もしない）
        setRowClassification(st,rowNo,patch)
      }
    })
    document.querySelectorAll('[data-pl-paren-mat]').forEach(el=>{
      // 素材欄の入力だけでは登録区分を切り替えない
      el.onchange=()=>setRowClassification(st,el.getAttribute('data-pl-paren-mat'),{material:el.value})
    })
    document.querySelectorAll('[data-pl-paren-purpose]').forEach(el=>{
      el.onchange=()=>setRowClassification(st,el.getAttribute('data-pl-paren-purpose'),{partsUsage:el.value})
    })
    document.querySelectorAll('[data-pl-paren-supp]').forEach(el=>{
      el.onchange=()=>setRowClassification(st,el.getAttribute('data-pl-paren-supp'),{supplement:el.value})
    })
    document.querySelectorAll('[data-pl-register-kind]').forEach(el=>{
      el.onchange=()=>{
        // 区分変更は編集状態のみ（再照合なし・chase-scrollなし）。入力値は消さない
        setRowClassification(st,el.getAttribute('data-pl-register-kind'),{registerKind:el.value})
        paintDialog({preserveScroll:true})
      }
    })
    document.querySelectorAll('[data-pl-unc-move]').forEach(el=>{
      el.onchange=()=>{
        if(!el.checked)return
        setRowClassification(st,el.getAttribute('data-pl-unc-move'),{unclassifiedMove:el.value})
        paintDialog({preserveScroll:true})
      }
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
        const currentSku=(row.csv&&(row.csv.registeredCode||row.csv.plannedRegisteredSku||row.csv.code))||mk.makerSku||''
        st.busy=true
        st.makerLookup={
          scope:'row',rowNo,makerSku:mk.makerSku,manufacturer:mk.manufacturer||'',
          supplier:(row.csv&&row.csv.supplier)||'',sourceName,currentSku,
          currentName,currentColor,currentSupplement,
          statusLabel:'検索中…',result:null,error:'',urlInput:'',urlFetchError:''
        }
        paintDialog()
        try{
          const result=await st.api('/api/product-ledger-csv/maker-lookup',{
            manufacturer:mk.manufacturer||'',
            makerSku:mk.makerSku,
            currentName,currentColor,currentSupplement,currentSku,
            sourceName,
            supplier:(row.csv&&row.csv.supplier)||''
          })
          st.makerLookup=Object.assign({},st.makerLookup,{
            result,
            manufacturer:result.manufacturer||mk.manufacturer||'',
            currentSku:result.plannedRegisteredSku||currentSku,
            statusLabel:result.statusLabel||result.status||'',error:''
          })
          st.toast('メーカー情報の取得が完了しました。必要なら「結果を見る」でパネルへ移動できます。')
        }catch(e){
          st.makerLookup=Object.assign({},st.makerLookup,{
            result:{status:'fetch_failed',statusLabel:'検索結果取得不可',candidates:[],
              matchedPages:[],referenceUrl:'',note:'検索リクエストに失敗しました。'},
            statusLabel:'検索結果取得不可',
            error:apiErrorMessage(e,'メーカー情報を取得できませんでした。')
          })
          st.toast(apiErrorMessage(e,'メーカー情報を取得できませんでした。'),6000)
        }finally{
          st.busy=false
          paintDialog()
        }
      }
    })
    document.querySelectorAll('[data-pl-sku-maker-correct]').forEach(el=>{
      el.onclick=async()=>{
        const code=el.getAttribute('data-pl-sku-maker-correct')
        const group=((st.preview&&st.preview.skuGroups)||[]).find(g=>String(g.code)===String(code))
        if(!group){st.toast('対象品番が見つかりません。');return}
        const mk=group.makerCorrection||{}
        if(!mk.showButton||!mk.makerSku){st.toast('メーカー品番が対象外か、確認待ちではありません。');return}
        const rowNos=Array.isArray(mk.rowNos)&&mk.rowNos.length?mk.rowNos:(group.rowNos||[])
        const firstRow=(st.preview.rows||[]).find(r=>rowNos.map(Number).includes(Number(r.rowNo)))
        const d=skuDecision(st,code)
        const sourceName=mk.sourceNameHint||(firstRow&&firstRow.csv&&firstRow.csv.productName)||''
        const currentName=d.officialName||(group.plannedOfficialName)||(group.namePlan&&group.namePlan.officialName)||sourceName||''
        const currentColor=d.color||group.plannedRegisteredColor||group.plannedColor||''
        const currentSupplement=''
        const currentSku=(firstRow&&firstRow.csv&&(firstRow.csv.registeredCode||firstRow.csv.plannedRegisteredSku||firstRow.csv.code))||code||mk.makerSku||''
        st.busy=true
        // 結果を品番の直下に出すため、検索中も展開しておく
        st.expandedCodes[code]=true
        st.makerLookup={
          scope:'sku',skuCode:code,rowNos,rowNo:firstRow?firstRow.rowNo:null,
          makerSku:mk.makerSku,manufacturer:mk.manufacturer||'',
          supplier:mk.supplier||(firstRow&&firstRow.csv&&firstRow.csv.supplier)||'',
          sourceName,currentSku,currentName,currentColor,currentSupplement,
          statusLabel:'検索中…',result:null,error:'',urlInput:'',urlFetchError:''
        }
        paintDialog()
        try{
          const result=await st.api('/api/product-ledger-csv/maker-lookup',{
            manufacturer:mk.manufacturer||'',
            makerSku:mk.makerSku,
            currentName,currentColor,currentSupplement,currentSku,
            sourceName,
            supplier:mk.supplier||(firstRow&&firstRow.csv&&firstRow.csv.supplier)||''
          })
          st.makerLookup=Object.assign({},st.makerLookup,{
            result,
            manufacturer:result.manufacturer||mk.manufacturer||'',
            currentSku:result.plannedRegisteredSku||currentSku,
            statusLabel:result.statusLabel||result.status||'',error:''
          })
          st.toast('メーカー情報の取得が完了しました。必要なら「結果を見る」でパネルへ移動できます。')
        }catch(e){
          st.makerLookup=Object.assign({},st.makerLookup,{
            result:{status:'fetch_failed',statusLabel:'検索結果取得不可',candidates:[],
              matchedPages:[],referenceUrl:'',note:'検索リクエストに失敗しました。'},
            statusLabel:'検索結果取得不可',
            error:apiErrorMessage(e,'メーカー情報を取得できませんでした。')
          })
          st.toast(apiErrorMessage(e,'メーカー情報を取得できませんでした。'),6000)
        }finally{
          st.busy=false
          paintDialog()
        }
      }
    })
    bindMakerViewResultsButtons()
    const makerClose=$('#plMakerLookupClose')
    if(makerClose)makerClose.onclick=()=>{st.makerLookup=null;paintDialog()}
    const makerUrlInput=$('#plMakerUrlInput')
    if(makerUrlInput){
      makerUrlInput.onchange=()=>{
        if(st.makerLookup)st.makerLookup.urlInput=makerUrlInput.value||''
      }
      makerUrlInput.oninput=()=>{
        if(st.makerLookup)st.makerLookup.urlInput=makerUrlInput.value||''
      }
    }
    const makerUrlFetch=$('#plMakerUrlFetch')
    if(makerUrlFetch){
      makerUrlFetch.onclick=async()=>{
        const panel=st.makerLookup
        if(!panel||!panel.makerSku){st.toast('メーカー品番がありません。');return}
        const url=(makerUrlInput&&makerUrlInput.value||panel.urlInput||'').trim()
        if(!url){st.toast('商品ページURLを入力してください。');return}
        st.busy=true
        panel.urlInput=url
        panel.urlFetchError=''
        panel.statusLabel='URLから取得中…'
        paintDialog()
        try{
          const result=await fetchMakerFromUrlForPanel(st,panel,url)
          const adoptable=(result.candidates||[]).some(c=>c&&c.name)
          st.makerLookup=Object.assign({},panel,{
            result,
            manufacturer:result.manufacturer||panel.manufacturer||'',
            currentSku:result.plannedRegisteredSku||panel.currentSku,
            statusLabel:result.statusLabel||result.status||'',
            error:'',
            urlFetchError:adoptable?'':(result.note||result.statusLabel||'URLから候補を取得できませんでした。'),
            urlInput:url
          })
          if(adoptable)st.toast('URLから候補を取得しました。内容を確認して「採用」してください。')
          else st.toast(result.statusLabel||'URLから候補を取得できませんでした。',6000)
        }catch(e){
          st.makerLookup=Object.assign({},panel,{
            urlFetchError:apiErrorMessage(e,'URLからの取得に失敗しました。'),
            statusLabel:panel.statusLabel||'取得失敗'
          })
        }finally{
          st.busy=false
          paintDialog()
        }
      }
    }
    // 一括メーカーの URL 取得ハンドラは bindBulkMakerPanelHandlers 側
    document.querySelectorAll('[data-pl-maker-adopt]').forEach(el=>{
      el.onclick=async()=>{
        const panel=st.makerLookup
        if(!panel||!panel.result)return
        const idx=Number(el.getAttribute('data-pl-maker-adopt'))
        const cand=(panel.result.candidates||[])[idx]
        if(!cand){st.toast('候補が見つかりません。');return}
        const skuScope=panel.scope==='sku'
        if(skuScope){
          const code=panel.skuCode
          const d=skuDecision(st,code)
          if((d.decision||'')==='color_split'){
            const ok=confirm(
              `現在「色別に分けて登録」が選ばれています。\n`+
              `メーカー情報の採用で「同じ商品としてまとめる」に切り替えます。\n\n`+
              `変更内容:\n`+
              `・判断: 色別に分けて登録 → 同じ商品としてまとめる\n`+
              `・登録予定名: ${cand.name||'—'}\n`+
              `・登録予定色: ${cand.color||'（空）'}\n\n`+
              `よろしいですか？`
            )
            if(!ok)return
          }
        }
        st.busy=true
        paintDialog()
        try{
          await applyMakerCandidateToPreview(st,{
            scope:skuScope?'sku':'row',
            skuCode:panel.skuCode,
            rowNos:panel.rowNos||[],
            rowNo:panel.rowNo,
            cand,
            manufacturer:panel.manufacturer||(panel.result&&panel.result.manufacturer)||'',
            makerSku:panel.makerSku
          })
          st.makerLookup=null
          st.toast(skuScope
            ?'メーカー情報を品番グループの登録予定へ採用しました（まだ台帳には登録していません）'
            :'メーカー情報を予定へ採用しました（まだ台帳には登録していません）')
          // 再照合して差分を画面へ反映（成功時のみ反映済み）
          const nextPreview=await st.api('/api/product-ledger-csv/preview',previewPayload(st))
          st.preview=nextPreview
          st.previewError=''
          snapshotAppliedSkuDecisions(st)
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
    const findPreviewRowByNo=(rowNo)=>((st.preview&&st.preview.rows)||[]).find(r=>Number(r.rowNo)===Number(rowNo))
    // 除外／復帰はローカル反映のみ（全件再照合・CSV再読込は「選択を反映して再照合」へ）
    document.querySelectorAll('[data-pl-row-exclude]').forEach(el=>{
      el.onclick=()=>{
        if(st.busy)return
        const row=findPreviewRowByNo(el.getAttribute('data-pl-row-exclude'))
        if(!row){st.toast('対象行が見つかりません。');return}
        applyLocalRowExclude(st,row,true)
        st.toast(`行 ${row.rowNo} を今回の登録対象から外しました（削除ではありません）`)
      }
    })
    document.querySelectorAll('[data-pl-row-exclude-same]').forEach(el=>{
      el.onclick=()=>{
        if(st.busy)return
        const row=findPreviewRowByNo(el.getAttribute('data-pl-row-exclude-same'))
        if(!row){st.toast('対象行が見つかりません。');return}
        const peers=sameContentRows(st,row)
        if(peers.length<2){st.toast('同じ内容の複数行がありません。');return}
        const lines=peers.slice(0,12).map(p=>{
          const c=p.csv||{}
          return `・行${p.rowNo} サイズ${c.size||'—'} 顧客${c.customer||'—'} ${c.productName||''}`
        })
        const more=peers.length>12?`\n…他 ${peers.length-12} 行`:''
        const ok=confirm(
          `同じ内容の ${peers.length} 行を今回の登録対象から外します（削除ではありません）。\n`+
          `個別の「この行を登録しない」とは別操作です。\n\n${lines.join('\n')}${more}\n\nよろしいですか？`
        )
        if(!ok)return
        harvestFormEdits(st)
        peers.forEach(p=>markRowExcludedLocal(st,p,true))
        paintDialog({preserveScroll:true})
        st.toast(`同じ内容の ${peers.length} 行を登録対象から外しました`)
      }
    })
    document.querySelectorAll('[data-pl-target-skip]').forEach(el=>{
      el.onclick=()=>{
        if(st.busy)return
        const id=el.getAttribute('data-pl-target-skip')
        // 詳細を開かず対象スコープだけ除外（他対象は巻き込まない）
        const item=buildUnifiedReviewItems(st).find(it=>String(it.id)===String(id))
        if(!item){st.toast('対象が見つかりません。');return}
        const result=applyLocalTargetSkipRegister(st,item)
        if(!result.excluded){
          st.toast('除外できる行がありません（既に登録しないか、対象行がありません）')
          return
        }
        st.toast(
          `「${result.targetLabel||'対象'}」の ${result.excluded} 行を今回登録しないにしました`+
          `（${result.scope}・登録しない項目へ。保留とは別です。削除ではありません）`
        )
      }
    })
    document.querySelectorAll('[data-pl-row-include]').forEach(el=>{
      el.onclick=()=>{
        if(st.busy)return
        const rowNo=el.getAttribute('data-pl-row-include')
        const keyAttr=el.getAttribute('data-pl-row-include-key')
        const needProc=el.getAttribute('data-pl-row-include-auto')==='1'
        let row=findPreviewRowByNo(rowNo)
        if(!row&&keyAttr&&st.rowExclusions&&st.rowExclusions[keyAttr]){
          const ent=st.rowExclusions[keyAttr]
          row={rowNo:ent.rowNo,csv:ent,autoRowExcluded:ent.exclusionKind==='auto',exclusionKind:ent.exclusionKind||'manual',deferReason:ent.reason||''}
        }
        let procName=''
        if(needProc||(row&&(row.autoRowExcluded||row.exclusionKind==='auto'))){
          const inp=document.querySelector(`[data-pl-row-restore-proc="${CSS.escape(String(rowNo))}"]`)
            ||(keyAttr&&document.querySelector(`[data-pl-row-restore-key="${CSS.escape(String(keyAttr))}"]`))
          procName=((inp&&inp.value)||'').trim()
          if(!procName){
            st.toast('自動除外を戻すには加工名を入力してください。')
            if(inp&&typeof inp.focus==='function'){try{inp.focus({preventScroll:true})}catch(_){try{inp.focus()}catch(__){}}}
            return
          }
        }
        if(!row&&keyAttr){
          delete st.rowExclusions[keyAttr]
          st.toast('登録対象に戻しました')
          paintDialog({preserveScroll:true})
          return
        }
        if(!row){st.toast('対象行が見つかりません。');return}
        applyLocalRowExclude(st,row,false,{processingName:procName})
        st.toast(procName
          ?`行 ${row.rowNo} を加工名「${procName}」で登録対象に戻しました（再照合でも勝手に除外しません）`
          :`行 ${row.rowNo} を登録対象に戻しました`)
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
        if(productsRegisterBlocked(st)){
          if(pendingRematchCount(st)>0){
            st.toast('未反映の方針変更があります。再照合してください。');return
          }
          st.toast('確認リストの未解決、または一括承認候補の未承認が残っています。先に解決してください。');return
        }
        const p=st.preview.plannedCounts||{}
        const jc=judgmentStatusCounts(st)
        const statusBlock=`\n\n【判断区分の件数】未解決 ${jc.unresolved}（確認事項 ${jc.issueCount}） / 未承認 ${jc.unapproved} / 確認済み ${jc.confirmed}（反映待ち ${jc.confirmedPending}／確認済み ${jc.confirmedDone}） / 自動決定済み（未登録） ${jc.autoDecided} / 保留 ${jc.hold}${jc.excluded?` / 対象外 ${jc.excluded}`:''}`
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
      buildUnifiedReviewItems,
      unifiedReviewCounts,
      productsRegisterBlocked,
      collectRowIssues,
      unifiedTargetMetaForRow,
      processingUnifiedKey,
      unifiedListRowsHtml,
      unifiedRowDetailHtml,
      unifiedPlanHtml,
      unifiedIssuesHtml,
      buildPlannedStatusFields,
      plannedNameForGroup,
      plannedSupplierForGroup,
      PLAN_STATUS,
      skuRowDetailHtml,
      effectiveDecision,
      decisionLabel,
      chosenActionLabel,
      pendingDecisionLabel,
      decisionDisplayLabel,
      userActionChoiceComplete,
      stillProblematicAfterRematch,
      remainingDecisionIssues,
      confirmedItemStatusKey,
      confirmedItemStatusLabel,
      confirmedSectionHtml,
      confirmedListRowsHtml,
      confirmedCompareTableHtml,
      matchesConfirmedFilters,
      colorSplitConfirmedDetailHtml,
      separateProcessServiceConfirmedDetailHtml,
      detectedProcessServiceForVariant,
      initSeparateProcessServicePlan,
      originalNamesForGroup,
      CONFIRMED_STATUS,
      CONFIRMED_LIST_COL_COUNT,
      appliedDecisionForCode,
      snapshotAppliedSkuDecisions,
      skuDecisionPendingRematch,
      pendingRematchCodes,
      pendingRematchCount,
      pendingRematchBannerHtml,
      setSkuDecision,
      applyMergeDefaults,
      SECTION_DEFAULTS,
      SKU_COMPARE_COL_COUNT,
      UNIFIED_REVIEW_COL_COUNT,
      pageSlice,
      SKU_PAGE_SIZE,
      MODE_PRODUCTS,
      MODE_OPENING,
      AUX_JUDGMENT_ONLY_COPY,
      USER_ROW_EXCLUDE_REASON,
      USER_ROW_EXCLUDE_REASON_LEGACY,
      K2_AUTO_EXCLUDE_REASON,
      userExcludeReasonTokens,
      previewPayload,
      selectedCodeList,
      syncSkuSelectionUi,
      syncBulkMakerAdoptSelectionUi,
      bulkMakerAdoptSelectionKey,
      bulkMakerRemainingConfirmCount,
      bulkMakerStructureSignature,
      bulkMakerProgressLine,
      bulkMakerCountLine,
      patchBulkMakerProgressTexts,
      scrollMakerLookupIntoView,
      scheduleLedgerScrollRestore,
      captureLedgerScroll,
      restoreLedgerScroll,
      applyScrollPos,
      readScrollPos,
      ensureState,
      stableRowKeyFromCsv,
      stableRowKeyFromRow,
      isRowExcluded,
      setRowExcluded,
      rowExclusionReason,
      markRowExcludedLocal,
      applyLocalRowExclude,
      rowsForSkipRegisterTarget,
      skipRegisterScopeLabel,
      skipRegisterButtonMeta,
      applyLocalTargetSkipRegister,
      skipRegisterActionsHtml,
      harvestFormEdits,
      snapshotRowBeforeExclude,
      syncSkuGroupAfterLocalExclude,
      sameContentKey,
      sameContentRows,
      listExcludedRows,
      rebindExclusionsAfterPreview,
      rowExcludeActionsHtml,
      skuGroupsHtml,
      judgmentStatusBarHtml,
      parenCell,
      autoRegisterKindForRow,
      effectiveRegisterKind,
      setRowClassification,
      clearRowClassificationPending,
      finalNameWithSupplement,
      REGISTER_KINDS
    }
  }
})()
