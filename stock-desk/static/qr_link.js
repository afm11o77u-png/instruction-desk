/** 旧QRと現在の商品の対応づけ。在庫数量は変えない。 */
(function(){
  let st=null

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

  function paint(){
    const el=dlg()
    const err=st.error?`<p class="notice qr-link-error" role="alert">${esc(st.error)}</p>`:''
    const preview=st.preview?previewHtml(st.preview):''
    const result=st.result?`<p class="notice">${esc((st.result.notices||[]).join(' '))}</p>`:''
    el.innerHTML=`<div class="dialoghead"><h2>旧QR対応</h2><button type="button" id="qrClose" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        <p class="excelhint">既存のQR（旧在庫行ID・旧顧客ID・旧メーカーID）を、今の商品・顧客・サイズ・新品中古・場所へ対応づけます。在庫数量・単価・金額は変えません。未一致から商品は作りません。</p>
        ${err}
        <div class="bar">
          <button type="button" id="qrTemplate">テンプレート</button>
          <label class="excelhint">Excel<input type="file" id="qrFile" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"></label>
          <button type="button" id="qrPreview">照合する</button>
        </div>
        ${result}
        ${preview}
      </div>`
    if(typeof el.showModal==='function'&&!el.open)el.showModal()
    bind()
  }

  function previewHtml(p){
    const counts=Object.entries(p.counts||{}).filter(([,n])=>n).map(([k,n])=>`${esc(k)} ${n}`).join('、')
    const rows=(p.rows||[]).map(rowHtml).join('')
    const writable=canWrite()
    return `<p class="excelhint">${esc(p.fileName||'')}　${counts}</p>
      <ul class="excelhint">${(p.notices||[]).map(n=>`<li>${esc(n)}</li>`).join('')}</ul>
      <div class="legacy-csv-table-wrap"><table class="legacy-csv-table">
        <thead><tr><th>行</th><th>状態</th><th>旧ID</th><th>品番 / 顧客 / サイズ</th><th>候補</th><th>登録</th></tr></thead>
        <tbody>${rows||'<tr><td colspan="6">行がありません。</td></tr>'}</tbody>
      </table></div>
      <div class="dialogactions"><button type="button" class="primary" id="qrCommit" ${writable?'':'disabled'}>${writable?'選んだ対応を登録':'閲覧専用'}</button></div>`
  }

  function rowHtml(row){
    const leg=row.legacy||{}
    const reasons=(row.reasons||[]).map(r=>`<div class="sub">${esc(r)}</div>`).join('')
    const existing=row.existing?`<div class="sub">登録済み 商品ID ${esc(row.existing.productId)} / サイズ ${esc(row.existing.sizeLabel||'—')} / ${esc(row.existing.condition==='used'?'中古':row.existing.condition==='new'?'新品':'状態未指定')}</div>`:''
    let pick=''
    if(row.canCommit&&row.status!=='exact'){
      const opts=(row.candidates||[]).map(c=>`<option value="${esc(c.key)}">${esc(c.label)}</option>`).join('')
      pick=`<select data-qr-key="${row.rowNo}"><option value="">候補を選ぶ</option>${opts}</select>`
      if(row.status==='conflict'||row.status==='unchanged'){
        pick+=`<label class="sub"><input type="checkbox" data-qr-replace="${row.rowNo}"> この対応に変更する</label>`
      }
    }else if(row.status==='exact'&&row.candidates&&row.candidates[0]){
      pick=`<div>${esc(row.candidates[0].label)}</div>`
    }
    const check=row.canCommit
      ?`<label><input type="checkbox" data-qr-include="${row.rowNo}" ${row.includeDefault?'checked':''}> 登録</label>`
      :''
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
  }

  async function downloadTemplate(){
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

  function filePayload(){
    const input=dlg().querySelector('#qrFile')
    const file=input&&input.files&&input.files[0]
    if(!file)return Promise.reject(Error('Excelファイルを選んでください。'))
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

  async function runPreview(){
    st.error=''
    st.result=null
    try{
      const file=await filePayload()
      st.file=file
      st.preview=await st.api('/api/qr-link/preview',{
        file:file.file,name:file.name,actorName:st.actorName||'',
      })
    }catch(e){
      st.error=e.message||'照合に失敗しました。'
    }
    paint()
  }

  async function runCommit(){
    if(!canWrite()){
      st.error='閲覧専用のため、登録できません。'
      paint()
      return
    }
    if(!st.preview||!st.file){
      st.error='先に照合してください。'
      paint()
      return
    }
    st.error=''
    const token='qr'+Math.random().toString(16).slice(2)+Date.now().toString(16)
    try{
      st.result=await st.api('/api/qr-link/commit',{
        file:st.file.file,
        name:st.file.name,
        previewFingerprint:st.preview.previewFingerprint,
        previewAt:st.preview.previewAt,
        token:token.slice(0,40),
        selections:selections(dlg()),
        actorName:st.actorName||'',
      })
      if(st.toast)st.toast((st.result.notices||['対応を登録しました。']).join(' '))
      st.preview=null
    }catch(e){
      st.error=e.message||'登録を中止しました。'
    }
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
    }
    paint()
  }

  window.QrLink={open}
})()
