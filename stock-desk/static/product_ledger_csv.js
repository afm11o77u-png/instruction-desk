/** 旧商品台帳CSV取込（登録・設定 → 商品）。初期在庫用「旧CSV取込」とは別。 */
(function(){
  const STATUS_ORDER=['create','size_add','reuse','unchanged','needs_review','excluded']
  const STATUS_LABELS={create:'新規',size_add:'サイズ追加',reuse:'既存使用',unchanged:'変更なし',needs_review:'要確認',excluded:'対象外'}
  const PAGE_SIZE=80
  // Keep in sync with stock-desk/upload_limits.py (POST body 12MB; raw CSV decode 8MB).
  const MAX_RAW_FILE_BYTES=8_000_000
  const MAX_POST_BODY_BYTES=12_000_000
  const OVERSIZE_MSG='ファイル容量が上限を超えています'
  let ui=null

  function ensureState(){
    if(!ui){
      ui={
        file:null,fileB64:'',fileName:'',fileSize:0,history:null,historyError:'',preview:null,result:null,
        q:'',statusFilter:'needs_review',page:1,
        selections:{},commitToken:'',busy:false,open:false,
        api:null,toast:null,boot:null,stockCanWrite:null,actorName:''
      }
    }
    return ui
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
    // fetch() network/tear-down (e.g. unhandled server exception) → Failed to fetch
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
      return `${STATUS_LABELS[k]} ${n}件`
    }).join('　')
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

  function rowActions(st,r){
    const sel=st.selections[String(r.rowNo)]||{}
    const bits=[]
    if(r.status==='needs_review'&&r.product&&r.product.candidates&&r.product.candidates.length){
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
      bits.push(`<label>商品<select data-pl-prod="${r.rowNo}" ${st.busy?'disabled':''}><option value="">選択…</option>${opts}<option value="__new__" ${sel.forceCreate?'selected':''}>新規として登録</option></select></label>`)
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
    if(r.matchGroupCount>1)bits.push(`<span class="excelhint">同条件 ${r.matchGroupCount}行</span>`)
    return bits.join(' ')
  }

  function targetCell(r){
    const p=r.product||{}
    const h=r.handle||{}
    const bits=[]
    if(p.id)bits.push(`商品ID ${p.id}`)
    else if(p.action==='create')bits.push('新規商品')
    if(p.sizeAction==='add')bits.push(`サイズ追加 ${esc((r.csv||{}).size||'')}`)
    if(p.subSupplierAdd)bits.push('サブ仕入先追加（通常は変更なし）')
    if(h.id)bits.push(`取扱 ${h.id}`)
    else if(h.action==='create')bits.push('取扱新規（加工未整理）')
    else if(h.embroideryLabel||h.embroidery)bits.push(esc(h.embroideryLabel||h.embroidery))
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
    return `<div class="notice legacy-csv-result">
      <strong>${r.already?'同じ操作は登録済みです':'登録が完了しました'}</strong>
      <p>新規商品 ${c.productsCreated||0}　サイズ追加 ${c.sizesAdded||0}　取扱新規 ${c.handlesCreated||0}
     　顧客 ${c.customersCreated||0}　仕入先 ${c.suppliersCreated||0}　サブ仕入先 ${c.subSuppliersAdded||0}
     　確定行 ${c.committedRows||0}　要確認残 ${r.reviewRemaining||0}　対象外 ${r.excludedCount||0}</p>
      <p>在庫数量・金額・履歴: ${r.stockUnchanged?'変化なし':'要確認'}</p>
    </div>`
  }

  function filePickHtml(st){
    const label=st.fileName?'ファイルを変更':'CSVファイルを選ぶ'
    const sizeHint=st.fileName&&st.fileSize?`（${Number(st.fileSize).toLocaleString('ja-JP')} バイト）`:''
    return `<div class="bar product-ledger-filebar">
      <input type="file" id="plFile" class="product-ledger-file-input" accept=".csv,text/csv" ${st.busy?'disabled':''}>
      <button type="button" id="plPickFile" ${st.busy?'disabled':''}>${esc(label)}</button>
      <span class="excelhint product-ledger-filename" id="plFileLabel">${esc(st.fileName||'未選択')}${sizeHint?` ${esc(sizeHint)}`:''}${st.preview?`（${esc(st.preview.encoding||'')}）`:''}</span>
      <button type="button" id="plPreview" class="primary" ${!st.fileB64||st.busy?'disabled':''}>読み込み・照合</button>
    </div>`
  }

  function paintDialog(){
    const st=ensureState()
    const dlg=document.getElementById('dialog')
    if(!dlg||!st.open)return
    const review=(st.preview&&st.preview.reviewCount)||0
    const canWrite=typeof st.stockCanWrite==='function'?st.stockCanWrite():true
    dlg.classList.add('product-ledger-dialog','legacy-csv-detail-dialog')
    dlg.innerHTML=`<div class="dialoghead"><h2>旧商品台帳CSVを取り込む</h2><button type="button" id="plClose" class="quiet">閉じる</button></div>
      <div class="dialogbody legacy-csv-detail-body">
        <p class="merge-lead">商品と顧客取扱だけを登録します。在庫数量・仕入単価・初期在庫・入出庫履歴は変更しません。初期在庫用の「旧CSV取込」とは別機能です。プレビューまではDBを変更しません。</p>
        ${filePickHtml(st)}
        ${historyBlock(st)}
        ${st.preview?`<p class="excelhint">${esc(st.preview.note||'')}</p>
          <p><strong>${countLine(st.preview.countsRaw)}</strong>　要確認 ${review}件${review? '（未解決のまま確定できません）':''}</p>
          <div class="bar">
            <input type="search" id="plQ" placeholder="要確認の検索（顧客・品番・商品名・理由）" value="${esc(st.q||'')}">
            <select id="plStatus">
              <option value="">すべて</option>
              ${STATUS_ORDER.map(k=>`<option value="${k}" ${st.statusFilter===k?'selected':''}>${STATUS_LABELS[k]}</option>`).join('')}
            </select>
            <button type="button" id="plRePreview" ${st.busy?'disabled':''}>選択を反映して再照合</button>
            <button type="button" id="plCommit" class="primary" ${st.busy||!canWrite||review>0?'disabled':''}>確認して登録</button>
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
      // Native input is remounted on paint; keep displayed name from state only.
      try{file.value=''}catch(_){}
    }
    const runPreview=async()=>{
      if(!st.fileB64){st.toast('CSVファイルを選んでください。');return}
      try{assertUploadSize(st.file,st.fileB64)}catch(e){st.toast(apiErrorMessage(e,OVERSIZE_MSG));return}
      st.busy=true;paintDialog()
      try{
        st.preview=await st.api('/api/product-ledger-csv/preview',{
          name:st.fileName,file:st.fileB64,selections:st.selections,
          _canWrite:true,_actor:{name:st.actorName||''}
        })
        st.result=null
        if(st.preview.reviewCount>0)st.statusFilter='needs_review'
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
      if((st.preview.reviewCount||0)>0){st.toast('要確認が残っています。先に解決してください。');return}
      if(!confirm('プレビューどおり商品・取扱を登録します。在庫数量・金額・履歴は変更しません。よろしいですか？'))return
      try{assertUploadSize(st.file,st.fileB64)}catch(e){st.toast(apiErrorMessage(e,OVERSIZE_MSG));return}
      st.busy=true;paintDialog()
      try{
        if(!st.commitToken)st.commitToken=`pl-${Date.now()}-${Math.random().toString(36).slice(2,10)}`
        st.result=await st.api('/api/product-ledger-csv/commit',{
          name:st.fileName,file:st.fileB64,selections:st.selections,
          previewFingerprint:st.preview.previewFingerprint,
          previewAt:st.preview.previewAt,
          token:st.commitToken,
          _canWrite:true,_actor:{name:st.actorName||''}
        })
        st.commitToken=`pl-${Date.now()}-${Math.random().toString(36).slice(2,10)}`
        if(typeof st.boot==='function')await st.boot()
        st.toast('商品台帳CSVの登録が完了しました')
      }catch(e){
        if(e&&e.payload&&e.payload.code==='stale_preview'){
          st.toast('データが変わったため、もう一度照合してください。')
          st.preview=null
        }else if(e&&e.payload&&e.payload.code==='backup_failed'){
          // Keep commitToken so a successful retry is not double-submitted as a new token
          // after a pre-registration abort; still clear busy below for retry.
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
