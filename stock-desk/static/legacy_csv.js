/* 旧システムCSV一括取込（登録・設定） */
(function(){
  const STATUS_ORDER=['create','reuse','qty_diff','unchanged','needs_review','hold']
  const PAGE_SIZE=80

  function ensureState(){
    if(!state.legacyCsv){
      state.legacyCsv={
        file:null,fileB64:'',fileName:'',history:null,preview:null,result:null,
        basisDate:'',defaultSiteName:'未確認',
        q:'',statusFilter:'',page:1,
        selections:{}, // rowNo -> {updateQty, productId, handleId}
        commitToken:'',busy:false
      }
    }
    if(!state.legacyCsv.basisDate)state.legacyCsv.basisDate=todayISO()
    if(!state.legacyCsv.defaultSiteName)state.legacyCsv.defaultSiteName='未確認'
    return state.legacyCsv
  }

  function siteOptions(selected){
    const sites=(state.data&&state.data.locations||[]).filter(l=>!l.parentId&&l.active!==false)
    const names=sites.map(s=>s.name)
    if(selected&&!names.includes(selected))names.unshift(selected)
    if(!names.includes('未確認'))names.unshift('未確認')
    return names.map(n=>`<option value="${esc(n)}" ${n===selected?'selected':''}>${esc(n)}</option>`).join('')
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

  function filteredRows(lc){
    const rows=(lc.preview&&lc.preview.rows)||[]
    const q=(lc.q||'').trim().toLowerCase()
    const st=lc.statusFilter||''
    return rows.filter(r=>{
      if(st&&r.status!==st)return false
      if(!q)return true
      const csv=r.csv||{}
      const hay=[csv.customer,csv.code,csv.productName,r.statusLabel,(r.reasons||[]).join(' ')].join(' ').toLowerCase()
      return hay.includes(q)
    })
  }

  function countLine(counts){
    if(!counts)return ''
    return STATUS_ORDER.map(k=>{
      const label={create:'新規追加',reuse:'既存使用',qty_diff:'数量差異',unchanged:'変更なし',needs_review:'要確認',hold:'保留'}[k]
      const n=counts[k]||0
      return `${label} ${n}件`
    }).join('　')
  }

  function historyBlock(lc){
    const prev=(lc.history&&lc.history.previousImports)||(lc.preview&&lc.preview.previousImports)||[]
    if(!prev.length)return '<p class="excelhint">このファイルの過去取込記録はありません。自動では再反映しません。</p>'
    const items=prev.map(p=>{
      const when=p.committedAt||p.previewAt||p.createdAt||''
      const done=p.committedAt?'登録済':'プレビューのみ'
      const cnt=p.result&&p.result.committedRowCount!=null?`確定${p.result.committedRowCount}件` : ''
      return `<li>${esc(when)}　${esc(done)}　${esc(p.actorName||'')}　${esc(cnt)}　基準日 ${esc(p.basisDate||'')}</li>`
    }).join('')
    return `<div class="list-note"><strong>前回取込情報（参考・自動再反映なし）</strong><ul class="legacy-csv-history">${items}</ul></div>`
  }

  function rowChecks(lc,r){
    const sel=lc.selections[String(r.rowNo)]||{}
    if(r.status==='qty_diff'){
      return `<label class="check"><input type="checkbox" data-qty-row="${r.rowNo}" ${sel.updateQty?'checked':''}>CSV数量へ更新</label>`
    }
    if(r.status==='needs_review'&&r.product&&r.product.candidates&&r.product.candidates.length){
      const opts=r.product.candidates.map(c=>`<option value="${c.id}" ${Number(sel.productId)===Number(c.id)?'selected':''}>${esc(c.code)} / ${esc(c.name)} / ${esc(c.supplierName||'')}</option>`).join('')
      return `<label>商品候補<select data-prod-row="${r.rowNo}"><option value="">選択…</option>${opts}</select></label>`
    }
    if(r.status==='needs_review'&&r.handle&&r.handle.candidates&&r.handle.candidates.length){
      const opts=r.handle.candidates.map(c=>`<option value="${c.id}" ${Number(sel.handleId)===Number(c.id)?'selected':''}>${esc(c.embroideryLabel||c.embroidery||'')}</option>`).join('')
      return `<label>取扱候補<select data-handle-row="${r.rowNo}"><option value="">選択…</option>${opts}</select></label>`
    }
    return ''
  }

  function renderTable(lc){
    const all=filteredRows(lc)
    const pages=Math.max(1,Math.ceil(all.length/PAGE_SIZE))
    if(lc.page>pages)lc.page=pages
    const start=(lc.page-1)*PAGE_SIZE
    const slice=all.slice(start,start+PAGE_SIZE)
    const body=slice.map(r=>{
      const csv=r.csv||{}
      const stock=r.stock||{}
      const diff=r.status==='qty_diff'?`${num(stock.currentQty)} → ${num(r.qty)}（差 ${num((r.qty||0)-(stock.currentQty||0))}）`:''
      const place=[csv.site||'（空→'+esc(lc.defaultSiteName)+'）',csv.shelf||'（棚空可）'].join(' / ')
      const reason=(r.reasons||[]).map(x=>esc(x)).join('<br>')
      const detail=[diff,reason].filter(Boolean).join('<br>')
      return `<tr class="legacy-status-${esc(r.status)}">
        <td>${r.rowNo}</td>
        <td>${esc(r.statusLabel)}</td>
        <td>${esc(csv.customer)}</td>
        <td>${esc(csv.code)}</td>
        <td>${esc(csv.productName)}</td>
        <td>${esc((r.handle&&r.handle.embroideryLabel)||'加工未整理')}</td>
        <td>${esc(csv.size)}</td>
        <td>${esc(r.conditionLabel)}</td>
        <td>${place}</td>
        <td class="num">${r.unitCost!=null?esc(yen(r.unitCost)):''}</td>
        <td class="num">${stock.exists?num(stock.currentQty):'—'}</td>
        <td class="num">${r.qty!=null?num(r.qty):''}</td>
        <td>${detail}</td>
        <td>${rowChecks(lc,r)}</td>
      </tr>`
    }).join('')
    return `<div class="legacy-csv-table-wrap"><table class="grid legacy-csv-table">
      <thead><tr>
        <th>行</th><th>判定</th><th>顧客</th><th>品番</th><th>商品名</th><th>加工</th><th>サイズ</th><th>新古</th><th>場所</th><th>単価</th><th>現在</th><th>CSV</th><th>内容</th><th>操作</th>
      </tr></thead>
      <tbody>${body||'<tr><td colspan="14">該当行がありません。</td></tr>'}</tbody>
    </table></div>
    <div class="bar legacy-csv-pager">
      <span>${num(all.length)}件中 ${num(Math.min(all.length,start+1))}–${num(Math.min(all.length,start+slice.length))} を表示</span>
      <button type="button" id="legacyCsvPrev" ${lc.page<=1?'disabled':''}>前へ</button>
      <span>${lc.page} / ${pages}</span>
      <button type="button" id="legacyCsvNext" ${lc.page>=pages?'disabled':''}>次へ</button>
    </div>`
  }

  function resultBlock(lc){
    if(!lc.result)return ''
    const r=lc.result
    const c=r.counts||{}
    return `<div class="notice legacy-csv-result">
      <p><strong>登録・更新結果</strong>${r.already?'（再送信のため追加変更なし）':''}</p>
      <p>確定 ${num(r.committedRowCount||0)} 件　／　保留・要確認 ${num(r.heldRowCount||0)} 件　／　記録者 ${esc(r.actorName||'未記録')}</p>
      <p>在庫新規 ${num(c.openingCreated||0)}　数量更新 ${num(c.qtyUpdated||0)}　顧客追加 ${num(c.customersCreated||0)}　仕入先追加 ${num(c.suppliersCreated||0)}　商品追加 ${num(c.productsCreated||0)}　取扱追加 ${num(c.handlesCreated||0)}</p>
      ${(r.notices||[]).map(n=>`<p>${esc(n)}</p>`).join('')}
      ${(r.failures||[]).length?`<p>失敗: ${(r.failures||[]).map(f=>esc(f.rowNo+'行: '+f.reason)).join(' / ')}</p>`:''}
    </div>`
  }

  window.legacyCsvPage=function legacyCsvPage(){
    const lc=ensureState()
    const canWrite=stockCanWrite()
    const preview=lc.preview
    const statusOpts=['','create','reuse','qty_diff','unchanged','needs_review','hold'].map(v=>{
      const label=v?({create:'新規追加',reuse:'既存使用',qty_diff:'数量差異',unchanged:'変更なし',needs_review:'要確認',hold:'保留'}[v]):'すべて'
      return `<option value="${v}" ${lc.statusFilter===v?'selected':''}>${label}</option>`
    }).join('')

    $('#main').innerHTML=`<div class="panel legacy-csv-panel">
      <div class="bar">
        <label>CSVファイル<input id="legacyCsvFile" type="file" accept=".csv,text/csv" ${lc.busy?'disabled':''}></label>
        <label>在庫基準日<input id="legacyCsvBasis" type="date" value="${esc(lc.basisDate)}" ${lc.busy?'disabled':''}></label>
        <label>拠点空欄時の登録先<select id="legacyCsvSite" ${lc.busy?'disabled':''}>${siteOptions(lc.defaultSiteName)}</select></label>
        <button type="button" class="primary" id="legacyCsvPreview" ${(!lc.fileB64||lc.busy||!canWrite)?'disabled':''}>取込内容を確認</button>
        <button type="button" class="primary" id="legacyCsvCommit" ${(!preview||lc.busy||!canWrite)?'disabled':''}>登録・更新</button>
      </div>
      <p class="excelhint">旧システムの在庫CSVを選びます。商品台帳.xlsxの同時取込はありません。確認の段階ではデータベースを変えません。数量は「在庫」列のみ使い、補正・購入・出荷からは再計算しません。入荷日は不明のまま（架空日付なし）。払出し順はCSV行番号を仮に使います（並び＝入荷順とは断定しません）。</p>
      ${!canWrite?'<p class="notice">閲覧専用のため、確認・登録はできません。</p>':''}
      ${lc.fileName?`<p class="excelhint">選択中: ${esc(lc.fileName)}（${num(lc.file?lc.file.size:lc.preview&&lc.preview.fileSize||0)} バイト）</p>`:''}
      ${historyBlock(lc)}
      ${preview?`<div class="notice"><p><strong>プレビュー件数</strong>　全 ${num(preview.rowCount)} 行　／　${esc(countLine(preview.countsRaw))}</p>
        <p>文字コード ${esc(preview.encoding)}　／　指紋 ${esc(preview.previewFingerprint)}</p>
        ${(preview.notices||[]).slice(0,4).map(n=>`<p>${esc(n)}</p>`).join('')}</div>
        <div class="bar">
          <label>検索<input id="legacyCsvQ" value="${esc(lc.q)}" placeholder="顧客・品番・商品名"></label>
          <label>判定<select id="legacyCsvStatus">${statusOpts}</select></label>
          <span class="opening-list-count">要確認・保留は一覧に残します（黙って除外しません）</span>
        </div>
        ${renderTable(lc)}`:''}
      ${resultBlock(lc)}
    </div>`

    const stage=$('.legacy-csv-panel')
    if(stage&&typeof DisplayMode!=='undefined'){
      mountStockDisplay(stage,'legacyCsv',()=>paint())
      DisplayMode.apply(stage,stockScreenId('legacyCsv'))
    }

    const fileInput=$('#legacyCsvFile')
    if(fileInput)fileInput.onchange=async()=>{
      const f=fileInput.files&&fileInput.files[0]
      lc.preview=null;lc.result=null;lc.selections={};lc.page=1
      if(!f){lc.file=null;lc.fileB64='';lc.fileName='';lc.history=null;paint();return}
      try{
        lc.busy=true;paint()
        lc.file=f;lc.fileName=f.name;lc.fileB64=await readFileAsBase64(f)
        lc.history=await api('/api/legacy-csv/history',{name:f.name,file:lc.fileB64})
        toast('ファイルを読みました。在庫基準日と登録先を確認し、「取込内容を確認」を押してください。')
      }catch(e){toast(e.message||String(e));lc.file=null;lc.fileB64='';lc.fileName=''}
      finally{lc.busy=false;paint()}
    }
    const basis=$('#legacyCsvBasis')
    if(basis)basis.onchange=()=>{lc.basisDate=basis.value}
    const site=$('#legacyCsvSite')
    if(site)site.onchange=()=>{lc.defaultSiteName=site.value}
    const q=$('#legacyCsvQ')
    if(q)q.oninput=e=>{const pos=e.target.selectionStart;lc.q=e.target.value;lc.page=1;paint();const el=$('#legacyCsvQ');if(el){el.focus();el.setSelectionRange(pos,pos)}}
    const st=$('#legacyCsvStatus')
    if(st)st.onchange=()=>{lc.statusFilter=st.value;lc.page=1;paint()}
    const prev=$('#legacyCsvPrev')
    if(prev)prev.onclick=()=>{lc.page=Math.max(1,lc.page-1);paint()}
    const next=$('#legacyCsvNext')
    if(next)next.onclick=()=>{lc.page=lc.page+1;paint()}

    document.querySelectorAll('[data-qty-row]').forEach(el=>{
      el.onchange=()=>{
        const key=String(el.dataset.qtyRow)
        lc.selections[key]=Object.assign({},lc.selections[key],{updateQty:el.checked})
      }
    })
    document.querySelectorAll('[data-prod-row]').forEach(el=>{
      el.onchange=()=>{
        const key=String(el.dataset.prodRow)
        const v=el.value?Number(el.value):null
        lc.selections[key]=Object.assign({},lc.selections[key],{productId:v})
      }
    })
    document.querySelectorAll('[data-handle-row]').forEach(el=>{
      el.onchange=()=>{
        const key=String(el.dataset.handleRow)
        const v=el.value?Number(el.value):null
        lc.selections[key]=Object.assign({},lc.selections[key],{handleId:v})
      }
    })

    const previewBtn=$('#legacyCsvPreview')
    if(previewBtn)previewBtn.onclick=async()=>{
      if(!lc.fileB64){toast('CSVファイルを選んでください。');return}
      if(!lc.basisDate){toast('在庫基準日を入れてください。');return}
      try{
        lc.busy=true;lc.result=null;paint()
        const body={
          name:lc.fileName,file:lc.fileB64,
          basisDate:lc.basisDate,defaultSiteName:lc.defaultSiteName,
          selections:lc.selections
        }
        lc.preview=await api('/api/legacy-csv/preview',body)
        lc.page=1
        const c=lc.preview.countsRaw||{}
        toast(`確認しました。新規${c.create||0}／既存使用${c.reuse||0}／数量差異${c.qty_diff||0}／変更なし${c.unchanged||0}／要確認${c.needs_review||0}／保留${c.hold||0}`)
      }catch(e){toast(e.message||String(e))}
      finally{lc.busy=false;paint()}
    }

    const commitBtn=$('#legacyCsvCommit')
    if(commitBtn)commitBtn.onclick=async()=>{
      if(!lc.preview){toast('先に「取込内容を確認」してください。');return}
      const c=lc.preview.countsRaw||{}
      const qtySelected=Object.values(lc.selections).filter(s=>s&&s.updateQty).length
      const hold=(c.needs_review||0)+(c.hold||0)
      const targetApprox=(c.create||0)+(c.reuse||0)+qtySelected
      const msg=`確定見込み 約 ${targetApprox} 件、保留・要確認 ${hold} 件を残します。実行しますか？\n（実行前にバックアップを取ります。エラー時は途中結果を残しません）`
      if(!window.confirm(msg))return
      try{
        lc.busy=true;paint()
        if(!lc.commitToken)lc.commitToken=crypto.randomUUID()
        const body={
          name:lc.fileName,file:lc.fileB64,
          basisDate:lc.basisDate,defaultSiteName:lc.defaultSiteName,
          previewFingerprint:lc.preview.previewFingerprint,
          previewAt:lc.preview.previewAt,
          selections:lc.selections,
          token:lc.commitToken,
          allowEmptyCommit:targetApprox===0&&hold>0?false:false
        }
        lc.result=await api('/api/legacy-csv/commit',body)
        // 成功後は新しい token（連打で二重登録しない。同一 token は already）
        if(!lc.result.already)lc.commitToken=crypto.randomUUID()
        lc.preview=null
        // 台帳再読込
        state.data=await api('/api/bootstrap')
        toast(lc.result.already?'再送信のため追加の登録はありませんでした。':`登録・更新しました（確定 ${lc.result.committedRowCount||0} / 保留 ${lc.result.heldRowCount||0}）`)
      }catch(e){
        if(e&&e.payload&&e.payload.code==='stale_preview')toast('プレビュー後にデータが変わったため、確認し直してください。')
        else toast(e.message||String(e))
        lc.commitToken=crypto.randomUUID()
      }finally{lc.busy=false;paint()}
    }
  }
})()
