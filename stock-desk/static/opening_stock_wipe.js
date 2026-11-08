/** 初期在庫の全削除（検証用）。商品の全削除と同じ確認の置き方。主管理者のみ。 */
(function(){
  const CONFIRM_TEXT='初期在庫を全削除'
  let st=null

  function esc(s){
    return String(s==null?'':s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
  }

  function ensureState(opts){
    if(!st)st={open:false,busy:false,preview:null,result:null,confirmText:'',error:''}
    if(opts){
      st.api=opts.api
      st.toast=opts.toast||function(){}
      st.boot=opts.boot
      st.isOwner=opts.isOwner
      st.actorName=opts.actorName||''
    }
    return st
  }

  function countLines(counts){
    const c=counts||{}
    return `初期在庫ロット ${c.openingLots||0}　初期在庫の履歴 ${c.openingMoves||0}　初期在庫の束 ${c.openingBatches||0}`
  }

  function blockersHtml(blockers){
    if(!blockers||!blockers.length)return ''
    const rows=blockers.map(b=>{
      const reasons=(b.reasons||[]).map(r=>`<li>${esc(r)}</li>`).join('')
      return `<tr><td>${esc(b.lotId)}</td><td>${esc(b.code||'')}</td><td>${esc(b.name||'')}</td><td>${esc(b.size||'')}</td><td><ul class="wipe-reasons">${reasons}</ul></td></tr>`
    }).join('')
    return `<div class="notice wipe-blockers">
      <strong>後から使われた初期在庫があるため削除できません（関連データは一切消していません）</strong>
      <div class="tablewrap"><table class="wipe-blocker-table">
        <thead><tr><th>ロット</th><th>品番</th><th>商品名</th><th>サイズ</th><th>理由</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </div>`
  }

  function paintDialog(){
    const s=ensureState()
    const dlg=document.getElementById('dialog')
    if(!dlg||!s.open)return
    const p=s.preview
    const can=p&&p.canWipe&&!s.busy&&s.confirmText===CONFIRM_TEXT
    dlg.classList.add('product-wipe-dialog')
    dlg.innerHTML=`<div class="dialoghead"><h2>初期在庫を削除（検証用）</h2><button type="button" id="owClose" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        <p class="testwarn">主管理者専用の検証用操作です。通常の入出庫削除ではありません。一部だけ消すことはせず、使われた初期在庫があると全体を中止します。</p>
        ${p?`<p class="excelhint">${esc(p.scopeNote||'')}</p>
          <p><strong>削除予定:</strong> ${esc(countLines(p.counts))}</p>
          <p><strong>残すもの:</strong> ${esc((p.kept||[]).join('、'))}</p>
          ${blockersHtml(p.blockers)}
          ${p.canWipe?`<label class="wipe-confirm-label">確認のため「${esc(CONFIRM_TEXT)}」と入力
            <input id="owConfirm" type="text" autocomplete="off" spellcheck="false" value="${esc(s.confirmText)}" ${s.busy?'disabled':''}>
          </label>
          <p id="owError" class="wipe-error" role="alert">${esc(s.error||'')}</p>`:
          `<p class="wipe-error" role="alert">使われた初期在庫があるため実行できません。</p>`}`:
          `<p>${s.busy?'削除内容を確認しています…':'削除内容を読み込めませんでした。'}</p>`}
        ${s.result&&s.result.ok?`<div class="notice"><strong>初期在庫の削除が完了しました</strong><p>${esc(countLines(s.result.counts))}</p><p class="excelhint">バックアップ: ${esc(s.result.backupPath||'—')}</p></div>`:''}
      </div>
      <div class="dialogactions">
        <button type="button" id="owCancel">閉じる</button>
        <button type="button" class="danger" id="owRun" ${can?'':'disabled'}>初期在庫を削除する</button>
      </div>`
    if(typeof dlg.showModal==='function'&&!dlg.open)dlg.showModal()
    const close=()=>{
      s.open=false;s.busy=false;s.preview=null;s.result=null;s.confirmText='';s.error=''
      dlg.classList.remove('product-wipe-dialog')
      if(dlg.open)dlg.close()
    }
    const closeBtn=document.getElementById('owClose')
    const cancelBtn=document.getElementById('owCancel')
    if(closeBtn)closeBtn.onclick=close
    if(cancelBtn)cancelBtn.onclick=close
    const input=document.getElementById('owConfirm')
    if(input)input.oninput=()=>{
      s.confirmText=input.value
      s.error=''
      const run=document.getElementById('owRun')
      if(run)run.disabled=!(s.preview&&s.preview.canWipe&&!s.busy&&s.confirmText===CONFIRM_TEXT)
    }
    const run=document.getElementById('owRun')
    if(run)run.onclick=async()=>{
      if(s.busy||!s.preview||!s.preview.canWipe)return
      if(s.confirmText!==CONFIRM_TEXT){
        s.error=`確認のため「${CONFIRM_TEXT}」と入力してください。`
        paintDialog()
        return
      }
      s.busy=true
      s.error=''
      paintDialog()
      try{
        s.result=await s.api('/api/opening-stock/wipe',{
          confirmText:CONFIRM_TEXT,
          actorName:s.actorName||'',
        })
        s.toast('初期在庫を削除しました。')
        if(typeof s.boot==='function')await s.boot()
      }catch(e){
        const payload=e&&e.payload
        if(payload&&payload.blockers){
          s.preview=Object.assign({},s.preview||{},{canWipe:false,blockers:payload.blockers})
        }
        s.error=(e&&e.message)||'初期在庫の全削除を中止しました。'
      }
      s.busy=false
      paintDialog()
    }
  }

  async function open(opts){
    const s=ensureState(opts)
    if(typeof s.isOwner==='function'&&!s.isOwner()){
      s.toast('初期在庫の全削除は主管理者だけが実行できます。')
      return
    }
    s.open=true
    s.busy=true
    s.preview=null
    s.result=null
    s.confirmText=''
    s.error=''
    paintDialog()
    try{
      s.preview=await s.api('/api/opening-stock/wipe/preview')
    }catch(e){
      s.error=(e&&e.message)||'削除内容を確認できませんでした。'
      s.toast(s.error)
    }
    s.busy=false
    paintDialog()
  }

  window.OpeningStockWipe={open}
})()
