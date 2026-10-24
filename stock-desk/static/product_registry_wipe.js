/** 商品登録の全削除（検証用）。登録・設定 → 商品。主管理者のみ。 */
(function(){
  const CONFIRM_TEXT='商品登録を全削除'
  let st=null

  function esc(s){
    return String(s==null?'':s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
  }

  function apiErrorMessage(err, fallback){
    if(err&&err.message)return err.message
    return fallback||'処理できませんでした。'
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
    return [
      `商品 ${c.products||0}`,
      `サイズ ${c.productSizes||0}`,
      `仕入先リンク ${c.productSuppliers||0}`,
      `顧客取扱 ${c.customerProducts||0}`,
      `統合記録 ${c.productMerges||0}`,
      `照合別名 ${c.productMatchAliases||0}`,
      `メーカー補正 ${c.makerSkuCorrections||0}`,
      `台帳CSVマップ ${c.productLedgerRowMaps||0}`,
      `旧CSV商品マップ ${c.legacyCsvRowMaps||0}`,
    ].join('　')
  }

  function blockersHtml(blockers){
    if(!blockers||!blockers.length)return ''
    const rows=blockers.map(b=>{
      const reasons=(b.reasons||[]).map(r=>`<li>${esc(r)}</li>`).join('')
      return `<tr>
        <td>${esc(b.productId)}</td>
        <td>${esc(b.codeDisplay||b.code||'')}</td>
        <td>${esc(b.name||'')}${b.color?` ${esc(b.color)}`:''}</td>
        <td><ul class="wipe-reasons">${reasons||'<li>—</li>'}</ul></td>
      </tr>`
    }).join('')
    return `<div class="notice wipe-blockers">
      <strong>参照があるため削除できません（関連データは一切消していません）</strong>
      <div class="tablewrap"><table class="wipe-blocker-table">
        <thead><tr><th>商品ID</th><th>品番</th><th>商品名</th><th>理由</th></tr></thead>
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
    dlg.innerHTML=`<div class="dialoghead"><h2>商品登録を全削除（検証用）</h2><button type="button" id="pwClose" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        <p class="testwarn">主管理者専用の検証用操作です。通常の商品削除ではありません。部分削除はせず、参照があると全体を中止します。</p>
        ${p?`<p class="excelhint">${esc(p.scopeNote||'')}</p>
          <p><strong>削除予定:</strong> ${esc(countLines(p.counts))}</p>
          <p><strong>残すもの:</strong> ${esc((p.kept||[]).join('、'))}</p>
          ${blockersHtml(p.blockers)}
          ${p.canWipe?`<label class="wipe-confirm-label">確認のため「${esc(CONFIRM_TEXT)}」と入力
            <input id="pwConfirm" type="text" autocomplete="off" spellcheck="false" value="${esc(s.confirmText)}" ${s.busy?'disabled':''}>
          </label>
          <p id="pwError" class="wipe-error" role="alert">${esc(s.error||'')}</p>`:
          `<p class="wipe-error" role="alert">参照がある商品があるため実行できません。在庫・履歴・発注などを残したまま商品登録だけ消すことはできません。</p>`}`:
          `<p>${s.busy?'削除内容を確認しています…':'削除内容を読み込めませんでした。'}</p>
           <p id="pwError" class="wipe-error" role="alert">${esc(s.error||'')}</p>`}
        ${s.result&&s.result.ok?`<div class="notice legacy-csv-result">
          <strong>商品登録の全削除が完了しました</strong>
          <p>${esc(countLines(s.result.counts))}</p>
          <p class="excelhint">バックアップ: ${esc(s.result.backupPath||'—')}　実行: ${esc(s.result.actorName||'')} ${esc(s.result.at||'')}</p>
        </div>`:''}
      </div>
      <div class="dialogactions">
        <button type="button" id="pwCancel">閉じる</button>
        <button type="button" class="danger" id="pwRun" ${can?'':'disabled'}>商品登録を全削除する</button>
      </div>`
    if(typeof dlg.showModal==='function'&&!dlg.open)dlg.showModal()
    bindDialog()
  }

  function bindDialog(){
    const s=ensureState()
    const close=()=>{
      s.open=false;s.busy=false;s.preview=null;s.result=null;s.confirmText='';s.error=''
      const dlg=document.getElementById('dialog')
      if(dlg){dlg.classList.remove('product-wipe-dialog');if(dlg.open)dlg.close()}
    }
    const closeBtn=document.getElementById('pwClose')
    const cancelBtn=document.getElementById('pwCancel')
    if(closeBtn)closeBtn.onclick=close
    if(cancelBtn)cancelBtn.onclick=close
    const input=document.getElementById('pwConfirm')
    if(input){
      input.oninput=()=>{
        s.confirmText=input.value
        s.error=''
        const run=document.getElementById('pwRun')
        if(run)run.disabled=!(s.preview&&s.preview.canWipe&&!s.busy&&s.confirmText===CONFIRM_TEXT)
      }
    }
    const run=document.getElementById('pwRun')
    if(run)run.onclick=()=>doWipe()
  }

  async function loadPreview(){
    const s=ensureState()
    s.busy=true;s.error='';s.preview=null;s.result=null;s.confirmText=''
    paintDialog()
    try{
      s.preview=await s.api('/api/products/registry-wipe/preview')
    }catch(e){
      s.error=apiErrorMessage(e,'削除内容を確認できませんでした。')
      s.toast(s.error)
    }
    s.busy=false
    paintDialog()
  }

  async function doWipe(){
    const s=ensureState()
    if(s.busy)return
    if(!s.preview||!s.preview.canWipe)return
    if(s.confirmText!==CONFIRM_TEXT){
      s.error=`確認のため「${CONFIRM_TEXT}」と入力してください。`
      paintDialog()
      return
    }
    s.busy=true;s.error=''
    paintDialog()
    try{
      const result=await s.api('/api/products/registry-wipe',{
        confirmText:CONFIRM_TEXT,
        actorName:s.actorName||'',
      })
      s.result=result
      s.toast('商品登録を全削除しました。')
      if(typeof s.boot==='function')await s.boot()
    }catch(e){
      const payload=e&&e.payload
      if(payload&&payload.blockers&&payload.blockers.length){
        s.preview=Object.assign({},s.preview||{},{
          canWipe:false,
          blockers:payload.blockers,
        })
      }
      s.error=apiErrorMessage(e,'商品登録の全削除を中止しました。')
      s.toast(s.error)
    }
    s.busy=false
    s.confirmText=''
    paintDialog()
  }

  function open(opts){
    const s=ensureState(opts||{})
    if(typeof s.isOwner==='function'? !s.isOwner() : !s.isOwner){
      s.toast('商品登録の全削除は主管理者だけが実行できます。')
      return
    }
    s.open=true
    loadPreview()
  }

  window.ProductRegistryWipe={open,CONFIRM_TEXT}
})()
