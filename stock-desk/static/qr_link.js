/** 旧QRと現在の商品の対応づけ。在庫数量は変えない。照合ルールはサーバ側のまま。 */
(function(){
  let st=null

  const LABEL_READING='読み込み中…'
  const LABEL_MATCHING='照合中…（登録はまだしていません）'
  const LABEL_SAVING='登録中…'

  // メインは要確認（候補が複数を含む）。完全一致は閉じて別リスト。判定は変えない。
  const SECTIONS=[
    {id:'invalid', title:'不正', statuses:['invalid'], kind:'alert', mode:'open',
      hint:'この行は登録できません。内容を直してから、もう一度照合してください。'},
    {id:'duplicate', title:'同じ旧ID', statuses:['duplicate'], kind:'alert', mode:'open',
      hint:'同じ旧在庫行IDが複数あります。一つにしてから、もう一度照合してください。'},
    {id:'conflict', title:'別対応が登録済み', statuses:['conflict'], kind:'action', mode:'open',
      hint:'登録済みと違う対応です。変えるときだけ候補を選んでください。'},
    {id:'main', title:'要確認', statuses:['review','ambiguous'], kind:'main', mode:'open', hint:''},
    {id:'exact', title:'完全一致', statuses:['exact'], kind:'aside', mode:'details-closed',
      hint:'初期選択のまま登録します。確認するとき開いてください。'},
    {id:'unmatched', title:'未一致', statuses:['unmatched'], kind:'separate', mode:'details-open',
      hint:'品番がある未一致と、制服の可能性がある品名はここに残します。部品・加工の候補は品名ごとにまとめ、件数を確認してから対応不要にします。'},
    {id:'out', title:'対象外', statuses:['out_of_scope'], kind:'aside', mode:'details-closed',
      hint:'部品・資材と加工サービス、および対応不要にした行です。未一致ではありません。登録せず、商品も作りません。保存した対応不要は、同じ品名の同じ旧IDを再取込しても戻します。'},
    {id:'unchanged', title:'登録済み', statuses:['unchanged'], kind:'aside', mode:'details-closed',
      hint:'同じ対応が登録済みです。変えるときだけ開いて指定してください。'},
  ]

  function esc(s){
    return String(s==null?'':s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
  }

  function basePath(){
    return location.pathname.startsWith('/inventory')?'/inventory':''
  }

  function dlg(){
    let el=document.getElementById('qrLinkDialog')
    if(!el){
      el=document.createElement('dialog')
      el.id='qrLinkDialog'
      el.className='qr-link-dialog'
      document.body.appendChild(el)
    }
    return el
  }

  function canWrite(){
    return st&&typeof st.stockCanWrite==='function'?st.stockCanWrite():true
  }

  function viewModel(){
    return {
      error:st.error||'',
      result:st.result,
      preview:st.preview,
      progressLabel:st.progressLabel||'',
      busy:!!st.busy,
      writable:canWrite(),
    }
  }

  function actionLabels(vm){
    const label=vm.progressLabel||''
    const saving=label.indexOf('登録')===0
    const reading=label.indexOf('読み込み')===0
    let preview='照合する'
    if(vm.busy&&label&&!saving)preview=reading?LABEL_READING:'照合中…'
    let commit=vm.writable?'選んだ対応を登録':'閲覧専用'
    if(vm.writable&&vm.busy&&saving)commit=LABEL_SAVING
    return {preview, commit}
  }

  function renderDialog(vm){
    vm=vm||{}
    const labels=actionLabels(vm)
    const dis=vm.busy?' disabled':''
    const err=vm.error?`<p class="notice qr-link-error" role="alert">${esc(vm.error)}</p>`:''
    const progress=vm.progressLabel?`<p class="notice qr-link-progress" id="qrProgress" role="status">${esc(vm.progressLabel)}</p>`:''
    const result=vm.result?`<p class="notice qr-link-result">${esc((vm.result.notices||[]).join(' '))}</p>`:''
    const preview=vm.preview?previewHtml(vm.preview,{
      writable:!!vm.writable, busy:!!vm.busy, commitLabel:labels.commit,
    }):''
    return `<div class="dialoghead"><h2>旧QR対応</h2><button type="button" id="qrClose" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        <p class="excelhint">既存のQR（旧在庫行ID・旧顧客ID・旧メーカーID）を、今の商品・顧客・サイズ・新品中古・場所へ対応づけます。在庫数量・単価・金額は変えません。未一致から商品は作りません。部品・資材と加工サービスは対象外です。</p>
        ${progress}
        ${err}
        <div class="bar">
          <button type="button" id="qrTemplate"${dis}>テンプレート</button>
          <label class="excelhint">Excel<input type="file" id="qrFile" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"${dis}></label>
          <button type="button" id="qrPreview"${dis}>${esc(labels.preview)}</button>
        </div>
        ${result}
        ${preview}
      </div>`
  }

  function paint(){
    const el=dlg()
    el.innerHTML=renderDialog(viewModel())
    if(st.busy)el.setAttribute('aria-busy','true')
    else el.removeAttribute('aria-busy')
    if(st.open&&typeof el.showModal==='function'&&!el.open)el.showModal()
    bind()
  }

  function rowsFor(rows, statuses){
    const set={}
    statuses.forEach(s=>{set[s]=true})
    return (rows||[]).filter(row=>set[row.status])
  }

  function mainHint(rows){
    if(!rows.length)return '要確認の行はありません。'
    const n=rows.filter(row=>row.status==='ambiguous').length
    const base='候補を選んで登録します。'
    return n?`${base}候補が複数 ${n} 件もこの一覧です。`:base
  }

  function tableHtml(rows, busy){
    return `<div class="legacy-csv-table-wrap"><table class="legacy-csv-table">
      <thead><tr><th>行</th><th>状態</th><th>旧ID</th><th>品番 / 顧客 / サイズ</th><th>候補</th><th>登録</th></tr></thead>
      <tbody>${rows.map(row=>rowHtml(row, busy)).join('')}</tbody>
    </table></div>`
  }

  function groupCards(groups, busy, mode){
    const list=(groups||[]).filter(g=>mode==='open'?g.openCount:g.savedCount)
    if(!list.length)return ''
    return `<div class="qr-scope-groups">${list.map(g=>{
      const n=mode==='open'?g.openCount:g.savedCount
      const button=mode==='open'
        ?`<button type="button" data-qr-dismiss-group="${esc(g.key)}" data-qr-group-name="${esc(g.itemName)}" data-qr-group-count="${n}"${busy?' disabled':''}>${n}件を対応不要にする</button>`
        :`<button type="button" data-qr-restore-group="${esc(g.key)}" data-qr-group-name="${esc(g.itemName)}" data-qr-group-count="${n}"${busy?' disabled':''}>${n}件を未一致に戻す</button>`
      return `<div class="qr-scope-group"><p><strong>${esc(g.scopeLabel)}</strong>　${esc(g.itemName)}　${n}件</p><p class="excelhint">${esc(g.reason)}</p>${button}</div>`
    }).join('')}</div>`
  }

  function sectionBlock(def, rows, busy){
    const groups=(st&&st.preview&&st.preview.scopeGroups)||[]
    const grouped=def.id==='unmatched'?groupCards(groups, busy, 'open'):def.id==='out'?groupCards(groups, busy, 'saved'):''
    const shown=def.id==='unmatched'
      ?rows.filter(row=>!row.scopeHint)
      :def.id==='out'
        ?rows.filter(row=>!(row.dismissSaved&&row.scopeHint))
        :rows
    if(!shown.length&&!grouped&&def.id!=='main')return ''
    const hint=def.id==='main'?mainHint(rows):def.hint
    const title=(def.id==='main'&&!rows.length)?def.title:`${def.title} ${rows.length}`
    const body=`<p class="excelhint">${esc(hint)}</p>${grouped}${shown.length?tableHtml(shown, busy):''}`
    const cls=`qr-link-section qr-link-section-${def.kind}`
    if(def.mode==='details-closed'||def.mode==='details-open'){
      const open=def.mode==='details-open'?' open':''
      return `<details class="${cls}" data-qr-section="${def.id}"${open}><summary>${esc(title)}</summary>${body}</details>`
    }
    return `<section class="${cls}" data-qr-section="${def.id}"><h3>${esc(title)}</h3>${body}</section>`
  }

  function sectionsHtml(rows, busy){
    const known={}
    SECTIONS.forEach(def=>(def.statuses||[]).forEach(s=>{known[s]=true}))
    const parts=SECTIONS.map(def=>sectionBlock(def, rowsFor(rows, def.statuses||[]), busy)).filter(Boolean)
    const other=(rows||[]).filter(row=>!known[row.status])
    if(other.length){
      parts.push(sectionBlock({
        id:'other', title:'その他', kind:'separate', mode:'open',
        hint:'区分に入らない行です。',
      }, other, busy))
    }
    return parts.join('')
  }

  function previewHtml(p, opts){
    opts=opts||{}
    const writable=!!opts.writable
    const busy=!!opts.busy
    const rows=p.rows||[]
    const tally={}
    rows.forEach(row=>{
      const key=row.statusLabel||row.status
      tally[key]=(tally[key]||0)+1
    })
    const counts=Object.entries(tally).filter(([,n])=>n).map(([k,n])=>`${esc(k)} ${n}`).join('、')
    const notices=(p.notices||[]).map(n=>`<li>${esc(n)}</li>`).join('')
    const body=rows.length?sectionsHtml(rows, busy):'<p class="excelhint">行がありません。</p>'
    const commitDis=(!writable||busy)?' disabled':''
    const commitLabel=opts.commitLabel||(writable?'選んだ対応を登録':'閲覧専用')
    return `<div class="qr-link-preview">
      <p class="excelhint">${esc(p.fileName||'')}　${counts}</p>
      <ul class="excelhint">${notices}</ul>
      ${body}
      <div class="dialogactions"><button type="button" class="primary" id="qrCommit"${commitDis}>${esc(commitLabel)}</button></div>
    </div>`
  }

  function diffLines(row){
    if(row.status!=='review'&&row.status!=='ambiguous')return []
    const cs=row.candidates||[]
    const lines=[]
    cs.forEach(c=>{
      const note=String(c.matchNote||'')
      if(!note||note==='完全一致')return
      const text=cs.length>1&&(c.productName||c.code)?`${c.productName||c.code}：${note}`:note
      if(lines.indexOf(text)<0)lines.push(text)
    })
    return lines
  }

  function rowHtml(row, busy){
    const leg=row.legacy||{}
    const dis=busy?' disabled':''
    const have=(row.reasons||[]).join('\n')
    const extra=diffLines(row).filter(t=>have.indexOf(t)<0&&have.indexOf(t.split('：').slice(1).join('：'))<0)
    const reasons=(row.reasons||[]).map(r=>{
      const diff=r.indexOf('旧：')>=0
      return `<div class="${diff?'sub qr-link-diff':'sub'}">${esc(r)}</div>`
    }).join('')+extra.map(t=>`<div class="sub qr-link-diff">${esc(t)}</div>`).join('')
    const existing=row.existing?`<div class="sub">登録済み 商品ID ${esc(row.existing.productId)} / サイズ ${esc(row.existing.sizeLabel||'—')} / ${esc(row.existing.condition==='used'?'中古':row.existing.condition==='new'?'新品':'状態未指定')}</div>`:''
    let pick=''
    if(row.canCommit&&row.status!=='exact'){
      const opts=(row.candidates||[]).map(c=>`<option value="${esc(c.key)}">${esc(c.label)}</option>`).join('')
      pick=`<select data-qr-key="${row.rowNo}"${dis}><option value="">候補を選ぶ</option>${opts}</select>`
      if(row.status==='conflict'||row.status==='unchanged'){
        pick+=`<label class="sub"><input type="checkbox" data-qr-replace="${row.rowNo}"${dis}> この対応に変更する</label>`
      }
    }else if(row.status==='exact'&&row.candidates&&row.candidates[0]){
      pick=`<div>${esc(row.candidates[0].label)}</div>`
    }
    let check=row.canCommit
      ?`<label><input type="checkbox" data-qr-include="${row.rowNo}"${row.includeDefault?' checked':''}${dis}> 登録</label>`
      :''
    if(!row.canCommit&&row.status==='unmatched'&&!row.scopeHint){
      check=`<label><input type="checkbox" data-qr-dismiss-one="${esc(leg.stockId)}"${dis}> 対応不要</label>`
    }else if(!row.canCommit&&row.dismissSaved){
      check=`<label><input type="checkbox" data-qr-restore-one="${esc(leg.stockId)}" checked${dis}> 対応不要</label>`
    }
    return `<tr>
      <td>${row.rowNo}</td>
      <td>${esc(row.statusLabel||row.status)}</td>
      <td>${esc(leg.stockId)}<div class="sub">${esc(leg.customerId)}</div></td>
      <td>${esc(leg.itemNum)} ${esc(leg.itemName)}<div class="sub">${esc(leg.customerName)} / ${esc(leg.itemSize||'サイズ空')} / ${esc(leg.condition==='used'?'中古':leg.condition==='new'?'新品':'状態未指定')}</div>${reasons}${existing}</td>
      <td>${pick}</td>
      <td>${check}</td>
    </tr>`
  }

  function selections(root){
    const out={}
    root.querySelectorAll('[data-qr-include]').forEach(box=>{
      const no=box.getAttribute('data-qr-include')
      const keyEl=root.querySelector(`[data-qr-key="${no}"]`)
      const rep=root.querySelector(`[data-qr-replace="${no}"]`)
      out[no]={
        include:box.checked,
        candidateKey:keyEl?keyEl.value:'',
        replace:!!(rep&&rep.checked),
      }
    })
    return out
  }

  function bind(){
    const el=dlg()
    const close=el.querySelector('#qrClose')
    if(close)close.onclick=()=>{st.open=false;el.close()}
    const tpl=el.querySelector('#qrTemplate')
    if(tpl)tpl.onclick=downloadTemplate
    const previewBtn=el.querySelector('#qrPreview')
    if(previewBtn)previewBtn.onclick=runPreview
    const commitBtn=el.querySelector('#qrCommit')
    if(commitBtn)commitBtn.onclick=runCommit
    el.querySelectorAll('[data-qr-dismiss-group]').forEach(btn=>{
      btn.onclick=()=>runDismissGroup(btn.getAttribute('data-qr-dismiss-group'), btn.getAttribute('data-qr-group-name'), btn.getAttribute('data-qr-group-count'))
    })
    el.querySelectorAll('[data-qr-restore-group]').forEach(btn=>{
      btn.onclick=()=>runRestoreGroup(btn.getAttribute('data-qr-restore-group'), btn.getAttribute('data-qr-group-name'), btn.getAttribute('data-qr-group-count'))
    })
    el.querySelectorAll('[data-qr-dismiss-one]').forEach(box=>{
      box.onchange=()=>{
        if(!box.checked)return
        runDismissOne(box.getAttribute('data-qr-dismiss-one'))
      }
    })
    el.querySelectorAll('[data-qr-restore-one]').forEach(box=>{
      box.onchange=()=>{
        if(box.checked)return
        runRestoreOne(box.getAttribute('data-qr-restore-one'))
      }
    })
  }

  function applyBusyDom(){
    const el=dlg()
    const vm=viewModel()
    const labels=actionLabels(vm)
    let status=el.querySelector('#qrProgress')
    if(vm.progressLabel){
      if(!status){
        status=document.createElement('p')
        status.id='qrProgress'
        status.className='notice qr-link-progress'
        status.setAttribute('role','status')
        const intro=el.querySelector('.dialogbody > .excelhint')
        if(intro)intro.insertAdjacentElement('afterend', status)
        else{
          const body=el.querySelector('.dialogbody')
          if(body)body.insertBefore(status, body.firstChild)
        }
      }
      status.textContent=vm.progressLabel
    }else if(status){
      status.remove()
    }
    if(vm.busy)el.setAttribute('aria-busy','true')
    else el.removeAttribute('aria-busy')
    const previewBtn=el.querySelector('#qrPreview')
    if(previewBtn){
      previewBtn.disabled=!!vm.busy
      previewBtn.textContent=labels.preview
    }
    const tpl=el.querySelector('#qrTemplate')
    if(tpl)tpl.disabled=!!vm.busy
    const file=el.querySelector('#qrFile')
    if(file)file.disabled=!!vm.busy
    const commitBtn=el.querySelector('#qrCommit')
    if(commitBtn){
      commitBtn.disabled=!!vm.busy||!vm.writable
      commitBtn.textContent=labels.commit
    }
    el.querySelectorAll('[data-qr-include],[data-qr-key],[data-qr-replace]').forEach(node=>{
      node.disabled=!!vm.busy
    })
  }

  async function downloadTemplate(){
    if(st.busy)return
    try{
      const r=await fetch(basePath()+'/api/qr-link/template')
      if(!r.ok){
        st.error='テンプレートを取得できませんでした。'
        paint()
        return
      }
      const blob=await r.blob()
      const a=document.createElement('a')
      a.href=URL.createObjectURL(blob)
      a.download='qr-link-template.xlsx'
      a.click()
      setTimeout(()=>URL.revokeObjectURL(a.href),1000)
    }catch(e){
      st.error='テンプレートを取得できませんでした。'
      paint()
    }
  }

  function selectedFile(){
    const input=dlg().querySelector('#qrFile')
    const file=input&&input.files&&input.files[0]
    if(!file)throw Error('Excelファイルを選んでください。')
    return file
  }

  function readFile(file){
    return new Promise((resolve,reject)=>{
      const reader=new FileReader()
      reader.onload=()=>{
        const text=String(reader.result||'')
        const comma=text.indexOf(',')
        resolve({
          file:comma>=0?text.slice(comma+1):text,
          name:file.name||'qr-link.xlsx',
        })
      }
      reader.onerror=()=>reject(Error('Excelを読めませんでした。'))
      reader.readAsDataURL(file)
    })
  }

  function clearTransient(){
    dlg().querySelectorAll('.qr-link-error,.qr-link-result').forEach(n=>n.remove())
  }

  async function refreshPreview(session){
    session.preview=await session.api('/api/qr-link/preview',{
      file:session.file.file,name:session.file.name,actorName:session.actorName||'',
    })
  }

  async function runDismissGroup(groupKey, name, count){
    if(st.busy||!st.file)return
    const n=Number(count)||0
    if(!window.confirm(`「${name}」${n}件を対応不要にします。登録はしません。同じ品名の同じ旧IDは、次に読み込んでも対象外のままです。`))return
    const session=st
    session.busy=true
    session.error=''
    session.progressLabel=LABEL_SAVING
    paint()
    try{
      const result=await session.api('/api/qr-link/dismiss',{
        file:session.file.file,name:session.file.name,groupKey:groupKey,actorName:session.actorName||'',
      })
      await refreshPreview(session)
      if(session.toast)session.toast(`「${result.itemName}」${result.dismissed}件を対応不要にしました。`)
    }catch(e){
      session.error=e.message||'対応不要にできませんでした。'
    }
    session.busy=false
    session.progressLabel=''
    paint()
  }

  async function runRestoreGroup(groupKey, name, count){
    if(st.busy||!st.file)return
    const n=Number(count)||0
    if(!window.confirm(`「${name}」${n}件を未一致に戻します。`))return
    const session=st
    session.busy=true
    session.error=''
    session.progressLabel=LABEL_SAVING
    paint()
    try{
      const result=await session.api('/api/qr-link/dismiss-restore',{
        file:session.file.file,name:session.file.name,groupKey:groupKey,actorName:session.actorName||'',
      })
      await refreshPreview(session)
      if(session.toast)session.toast(`「${result.itemName}」${result.restored}件を未一致に戻しました。`)
    }catch(e){
      session.error=e.message||'戻せませんでした。'
    }
    session.busy=false
    session.progressLabel=''
    paint()
  }

  async function runDismissOne(stockId){
    if(st.busy||!st.file)return
    if(!window.confirm('この1件を対応不要にします。登録はしません。'))return
    const session=st
    session.busy=true
    session.error=''
    paint()
    try{
      await session.api('/api/qr-link/dismiss',{
        file:session.file.file,name:session.file.name,legacyStockId:stockId,actorName:session.actorName||'',
      })
      await refreshPreview(session)
    }catch(e){
      session.error=e.message||'対応不要にできませんでした。'
    }
    session.busy=false
    paint()
  }

  async function runRestoreOne(stockId){
    if(st.busy||!st.file)return
    const session=st
    session.busy=true
    session.error=''
    paint()
    try{
      await session.api('/api/qr-link/dismiss-restore',{
        file:session.file.file,name:session.file.name,legacyStockId:stockId,actorName:session.actorName||'',
      })
      await refreshPreview(session)
    }catch(e){
      session.error=e.message||'戻せませんでした。'
    }
    session.busy=false
    paint()
  }

  async function runPreview(){
    if(st.busy)return
    const session=st
    let raw
    try{
      raw=selectedFile()
    }catch(e){
      session.error=e.message||'照合に失敗しました。'
      session.progressLabel=''
      session.busy=false
      paint()
      return
    }
    session.error=''
    session.result=null
    session.busy=true
    session.progressLabel=LABEL_READING
    dlg().querySelectorAll('.qr-link-error,.qr-link-result,.qr-link-preview').forEach(n=>n.remove())
    applyBusyDom()
    try{
      const file=await readFile(raw)
      if(st!==session)return
      session.file=file
      session.progressLabel=LABEL_MATCHING
      applyBusyDom()
      session.dismissed={}
      session.preview=await session.api('/api/qr-link/preview',{
        file:file.file,name:file.name,actorName:session.actorName||'',
      })
    }catch(e){
      if(st!==session)return
      session.error=e.message||'照合に失敗しました。'
    }
    if(st!==session)return
    session.busy=false
    session.progressLabel=''
    paint()
  }

  async function runCommit(){
    if(st.busy)return
    const session=st
    if(!canWrite()){
      session.error='閲覧専用のため、登録できません。'
      paint()
      return
    }
    if(!session.preview||!session.file){
      session.error='先に照合してください。'
      paint()
      return
    }
    const picked=selections(dlg())
    session.error=''
    clearTransient()
    session.busy=true
    session.progressLabel=LABEL_SAVING
    applyBusyDom()
    const token='qr'+Math.random().toString(16).slice(2)+Date.now().toString(16)
    try{
      session.result=await session.api('/api/qr-link/commit',{
        file:session.file.file,
        name:session.file.name,
        previewFingerprint:session.preview.previewFingerprint,
        previewAt:session.preview.previewAt,
        token:token.slice(0,40),
        selections:picked,
        actorName:session.actorName||'',
      })
      if(st!==session)return
      if(session.toast)session.toast((session.result.notices||['対応を登録しました。']).join(' '))
      session.preview=null
    }catch(e){
      if(st!==session)return
      session.error=e.message||'登録を中止しました。'
    }
    if(st!==session)return
    session.busy=false
    session.progressLabel=''
    paint()
  }

  function open(opts){
    st={
      open:true,
      api:opts.api,
      toast:opts.toast,
      stockCanWrite:opts.stockCanWrite,
      actorName:opts.actorName||'',
      preview:null,
      result:null,
      file:null,
      error:'',
      busy:false,
      progressLabel:'',
    }
    paint()
  }

  window.QrLink={
    open,
    __test:{renderDialog, previewHtml, actionLabels},
  }
})()
