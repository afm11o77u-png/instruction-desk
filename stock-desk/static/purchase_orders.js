/* 発注（purchase orders）UI — loaded after app.js helpers exist via shared globals. */
(function(global){
'use strict'

function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function yen(n){return Number(n).toLocaleString('ja-JP')+'円'}
function num(n){return Number(n).toLocaleString('ja-JP')}
function todayISO(d=new Date()){return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}
function newToken(){return 'po-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10)}

const PurchaseOrders={
  async loadList(){
    const q=new URLSearchParams()
    if(state.poQ)q.set('q',state.poQ)
    if(state.poStatus)q.set('status',state.poStatus)
    state.poList=await api('/api/purchase-orders?'+q.toString())
  },
  page(){
    const writable=stockCanWrite()
    const orders=(state.poList&&state.poList.orders)||[]
    const statuses=(state.poList&&state.poList.statuses)||[]
    $('#main').innerHTML=`<section class="panel po-panel">
      <div class="bar">
        ${writable?`<button type="button" class="primary" id="poCreate">発注書を作成</button>`:''}
        ${writable?`<button type="button" id="poCompany">自社情報</button>`:''}
        <input id="poQ" type="search" placeholder="発注番号・仕入先・顧客・作成者で検索" value="${esc(state.poQ||'')}">
        <label class="po-status-filter">状態
          <select id="poStatus"><option value="">すべて</option>
            ${statuses.map(s=>`<option value="${esc(s.id)}" ${state.poStatus===s.id?'selected':''}>${esc(s.label)}</option>`).join('')}
          </select>
        </label>
        <span class="count">${orders.length}件</span>
      </div>
      ${writable?'':`<p class="notice">閲覧専用のため、発注の作成・入荷はできません。</p>`}
      ${listNote('発注の見方','画面では仕入先・対象顧客と明細だけを選びます。発注日・担当・FAX・発注者は印刷用紙に手書きします。保存・プレビュー・印刷だけでは発注済みになりません。FAX/メール後に「発注済みにする」を押します。入荷は発注明細から登録し、通常の入庫と同じ在庫経路です。')}
      <div class="list-stage" id="stockliststage"><div class="tablewrap"><table class="po-table-list"><thead><tr>
        <th>発注番号</th><th>作成日時</th><th>仕入先</th><th>対象顧客</th><th>作成者</th><th>状態</th><th></th>
      </tr></thead><tbody>
        ${orders.map(o=>`<tr>
          <td class="dm-primary">${esc(o.number)}${o.needsResend?'<span class="sub">再送が必要</span>':''}</td>
          <td>${esc(o.createdAt||'')}</td>
          <td>${esc(o.supplierName)}</td>
          <td>${esc(o.customerName)}</td>
          <td>${esc(o.createdBy||o.ordererName||'')}</td>
          <td>${esc(o.statusLabel)}</td>
          <td><button type="button" class="quiet" data-po-open="${o.id}">開く</button></td>
        </tr>`).join('')||'<tr><td colspan="7">保存した発注書はまだありません。</td></tr>'}
      </tbody></table></div><div class="list-grid" id="stocklistgrid" hidden></div></div>
    </section>`
    const create=$('#poCreate')
    if(create)create.onclick=()=>PurchaseOrders.openEditor(null)
    const coBtn=$('#poCompany')
    if(coBtn)coBtn.onclick=()=>PurchaseOrders.openCompanySettings()
    const qEl=$('#poQ')
    let t=null
    qEl.oninput=()=>{
      state.poQ=qEl.value
      clearTimeout(t)
      t=setTimeout(async()=>{try{await PurchaseOrders.loadList();paint()}catch(e){toast(e.message)}},200)
    }
    $('#poStatus').onchange=async()=>{
      state.poStatus=$('#poStatus').value
      try{await PurchaseOrders.loadList();paint()}catch(e){toast(e.message)}
    }
    const stage=$('#stockliststage')
    const displayMode=typeof mountStockDisplay==='function'?mountStockDisplay(stage,'orders',()=>paint()):'standard'
    const grid=$('#stocklistgrid')
    if(grid){
      grid.innerHTML=orders.length?orders.map(o=>`<article class="dm-card"><div class="dm-primary">${esc(o.number)}${o.needsResend?' <span class="sub">再送が必要</span>':''}</div><div class="dm-secondary">${esc(o.createdAt||'')}</div><div class="dm-meta"><span>${esc(o.supplierName)}</span><span>${esc(o.customerName)}</span></div><div class="dm-meta"><span>${esc(o.createdBy||o.ordererName||'')}</span><span>${esc(o.statusLabel)}</span></div><div class="dm-actions"><button type="button" class="quiet" data-po-open="${o.id}">開く</button></div></article>`).join(''):'<div class="dm-empty">保存した発注書はまだありません。</div>'
      grid.hidden=displayMode!=='grid'
    }
    if(stage&&typeof DisplayMode!=='undefined')DisplayMode.apply(stage,typeof stockScreenId==='function'?stockScreenId('orders'):'stock-orders')
    document.querySelectorAll('[data-po-open]').forEach(b=>b.onclick=()=>PurchaseOrders.openDetail(Number(b.dataset.poOpen)))
  },
  async openDetail(id){
    try{
      const po=await api('/api/purchase-orders/'+id)
      PurchaseOrders.renderDetail(po)
    }catch(e){toast(e.message)}
  },
  renderDetail(po){
    const dlg=$('#dialog')
    dlg.classList.add('opening-dialog','po-dialog')
    const writable=stockCanWrite()
    const canReceive=writable&&(po.status==='ordered'||po.status==='partial')
    const canMark=writable&&po.status==='unordered'
    const canEdit=writable&&po.status!=='cancelled'
    const canCancel=writable&&po.status!=='cancelled'
    dlg.innerHTML=`<div class="dialoghead"><h2>発注書 ${esc(po.number)}</h2><button type="button" id="poClose" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        <p><strong>${esc(po.statusLabel)}</strong>　作成日時 ${esc(po.createdAt||'')}　作成者 ${esc(po.createdBy||po.ordererName||'')}　仕入先 ${esc(po.supplierName)}　対象顧客 ${esc(po.customerName)}
          ${po.needsResend?'　<span class="po-resend">紙の再送が必要</span>':''}</p>
        <p class="notice">印刷用紙の発注日・担当・FAX・発注者は手書き欄です（画面では入力しません）。</p>
        <div class="tablewrap"><table><thead><tr><th>#</th><th>商品</th><th>加工(社内)</th><th>サイズ</th><th>発注</th><th>入荷済</th><th>残</th><th>備考</th></tr></thead><tbody>
          ${(po.lines||[]).map(l=>`<tr>
            <td>${l.lineNo}</td>
            <td>${esc(l.code)} ${esc(l.productName)} ${esc(l.color)}</td>
            <td>${esc(l.embroideryLabel)}</td>
            <td>${esc(l.size)}</td>
            <td class="qty">${num(l.qtyOrdered)}</td>
            <td class="qty">${num(l.qtyReceived)}</td>
            <td class="qty">${num(l.qtyRemaining)}</td>
            <td>${esc(l.lineNote||'')}</td>
          </tr>`).join('')}
        </tbody></table></div>
        <p>備考：${esc(po.remarks||'（なし）')}</p>
        <h3>変更履歴</h3>
        <ul class="po-events">${(po.events||[]).map(e=>`<li>${esc(e.at)}　${esc(e.actorName||'')}　${esc(e.kind)}　${esc(e.detail||'')}</li>`).join('')||'<li>なし</li>'}</ul>
        ${(po.receipts||[]).length?`<h3>入荷履歴（発注 ${esc(po.number)}）</h3>
          <ul>${po.receipts.map(r=>`<li>${esc(r.receivedOn)}　明細#${r.lineId}　${num(r.qty)}枚　${yen(r.unitCost)}　ロット${r.lotId}</li>`).join('')}</ul>
          <p class="notice">この入荷は在庫に反映済みです。通常の入庫画面で同じ内容を再登録する必要はありません。</p>`:''}
        <p id="poDetailErr"></p>
      </div>
      <div class="dialogactions">
        <button type="button" id="poClose2">戻る</button>
        <button type="button" id="poPreviewSaved">プレビュー / 印刷</button>
        ${canEdit?`<button type="button" id="poEdit">内容を修正</button>`:''}
        ${canMark?`<button type="button" class="primary" id="poMark">発注済みにする</button>`:''}
        ${canReceive?`<button type="button" class="primary" id="poReceive">入荷を登録</button>`:''}
        ${canCancel?`<button type="button" id="poCancel">取消</button>`:''}
      </div>`
    const close=()=>{dlg.close();dlg.classList.remove('opening-dialog','po-dialog');dlg.innerHTML='';PurchaseOrders.loadList().then(()=>paint()).catch(()=>paint())}
    $('#poClose').onclick=close;$('#poClose2').onclick=close
    $('#poPreviewSaved').onclick=()=>PurchaseOrders.showHtmlPreview(null,po.id)
    const edit=$('#poEdit');if(edit)edit.onclick=()=>{dlg.close();PurchaseOrders.openEditor(po)}
    const mark=$('#poMark')
    if(mark)mark.onclick=async()=>{
      if(!window.confirm('FAX/メールで発注したあと、発注済みにしますか？（保存や印刷だけでは発注済みになりません）'))return
      try{
        const next=await api('/api/purchase-orders/mark-ordered',{id:po.id,token:newToken()})
        toast(next.already?'すでに発注済みです。':'発注済みにしました。')
        PurchaseOrders.renderDetail(next)
      }catch(e){$('#poDetailErr').textContent=e.message}
    }
    const recv=$('#poReceive');if(recv)recv.onclick=()=>PurchaseOrders.openReceive(po)
    const cancel=$('#poCancel')
    if(cancel)cancel.onclick=async()=>{
      if(!window.confirm('この発注書を取消しますか？入荷済みの在庫は減りません。'))return
      try{
        const next=await api('/api/purchase-orders/cancel',{id:po.id})
        toast('取消しました。')
        PurchaseOrders.renderDetail(next)
      }catch(e){$('#poDetailErr').textContent=e.message}
    }
    if(!dlg.open)dlg.showModal()
  },
  openCompanySettings(){
    if(!stockCanWrite()){toast('閲覧専用のため、変更できません。');return}
    const dlg=$('#dialog')
    dlg.classList.add('opening-dialog','po-dialog')
    const co=Object.assign({},state.data.company||{})
    dlg.innerHTML=`<div class="dialoghead"><h2>自社情報（発注書印刷）</h2><button type="button" id="coClose" class="quiet">閉じる</button></div>
      <div class="dialogbody">
        <p class="notice">発注書に毎回同じ内容を印刷します。注文ごとの入力はありません。参考PDFが無い場合はブランド名を初期値にしています。</p>
        <div class="grid">
          <label>会社名<input id="coName" value="${esc(co.companyName||'')}"></label>
          <label>支店・部署<input id="coBranch" value="${esc(co.companyBranch||'')}"></label>
          <label>住所<input id="coAddress" value="${esc(co.companyAddress||'')}"></label>
          <label>電話<input id="coTel" value="${esc(co.companyTel||'')}"></label>
          <label>FAX<input id="coFax" value="${esc(co.companyFax||'')}"></label>
        </div>
        <p id="coErr"></p>
      </div>
      <div class="dialogactions">
        <button type="button" id="coClose2">戻る</button>
        <button type="button" class="primary" id="coSave">保存する</button>
      </div>`
    const close=()=>{dlg.close();dlg.classList.remove('opening-dialog','po-dialog');dlg.innerHTML=''}
    $('#coClose').onclick=close;$('#coClose2').onclick=close
    $('#coSave').onclick=async()=>{
      try{
        const saved=await api('/api/company-profile',{
          companyName:$('#coName').value,companyBranch:$('#coBranch').value,
          companyAddress:$('#coAddress').value,companyTel:$('#coTel').value,companyFax:$('#coFax').value,
        })
        state.data.company=saved
        toast('自社情報を保存しました。')
        close()
      }catch(e){$('#coErr').textContent=e.message}
    }
    if(!dlg.open)dlg.showModal()
  },
  openEditor(existing){
    if(!stockCanWrite()){toast('閲覧専用のため、発注できません。');return}
    const dlg=$('#dialog')
    dlg.classList.add('opening-dialog','po-dialog')
    const draft={
      id:existing?existing.id:null,
      token:newToken(),
      supplierId:existing?existing.supplierId:null,
      customerId:existing?existing.customerId:null,
      remarks:existing?existing.remarks:'',
      supplierQuery:'',customerQuery:'',
      products:[],
      candidates:[],
      dirty:false,
    }
    if(existing&&existing.lines){
      const byCp=new Map()
      existing.lines.forEach(l=>{
        const key=l.customerProductId
        if(!byCp.has(key)){
          byCp.set(key,{
            customerProductId:l.customerProductId,
            productId:l.productId,
            code:l.code,name:l.productName,color:l.color,
            embroidery:l.embroidery,embroideryLabel:l.embroideryLabel,
            sizes:{},note:l.lineNote||'',
            sizeList:[],
          })
        }
        const row=byCp.get(key)
        row.sizes[l.size]=l.qtyOrdered
      })
      draft.products=[...byCp.values()]
    }
    const touch=()=>{draft.dirty=true}
    const render=async()=>{
      const supplier=(state.data.suppliers||[]).find(s=>s.id===draft.supplierId)
      const customer=(state.data.customers||[]).find(c=>c.id===draft.customerId)
      dlg.innerHTML=`<div class="dialoghead"><h2>${draft.id?'発注書を修正':'発注書を作成'}</h2><button type="button" id="poEdClose" class="quiet">閉じる</button></div>
        <div class="dialogbody">
          <div class="grid">
            ${searchPickerMarkup({label:'仕入先',inputId:'poSupplier',listId:'poSupplierList',clearId:'poSupplierClear',hintId:'poSupplierHint',placeholder:'仕入先名で検索',value:supplier?supplier.name:(draft.supplierQuery||'')})}
            ${searchPickerMarkup({label:'対象顧客',inputId:'poCustomer',listId:'poCustomerList',clearId:'poCustomerClear',hintId:'poCustomerHint',placeholder:'顧客名またはカナで検索',value:customer?customer.name:(draft.customerQuery||'')})}
          </div>
          <p class="notice">発注日・仕入先担当・FAX・発注者は印刷用紙に手書きします（ここでは入力しません）。作成日時・作成者・発注番号は保存時に自動記録します。</p>
          <h3>明細（1発注＝1仕入先＋1顧客）</h3>
          <p class="notice">候補は「対象顧客の取扱」かつ「選択した仕入先が通常仕入先の商品」です。発注仕入先を変えても商品マスタの通常仕入先は変わりません。加工仕様は社内用で、メーカー依頼は明細備考へ。</p>
          <div class="bar">
            <input id="poProdQ" type="search" placeholder="品番・商品名・加工で候補検索" ${draft.customerId&&draft.supplierId?'':'disabled'}>
            <button type="button" id="poAddProd" ${draft.customerId&&draft.supplierId?'':'disabled'}>候補から追加</button>
          </div>
          <div id="poProdCandidates" class="po-candidates" hidden></div>
          <div id="poProductRows"></div>
          <label>発注書全体の備考<textarea id="poRemarks" rows="2">${esc(draft.remarks)}</textarea></label>
          <p id="poEdErr"></p>
        </div>
        <div class="dialogactions">
          <button type="button" id="poEdClose2">戻る</button>
          <button type="button" id="poPreview">プレビュー / 印刷</button>
          <button type="button" class="primary" id="poSave">保存する</button>
        </div>`
      const close=()=>{if(draft.dirty&&!window.confirm('入力中の内容を破棄しますか？'))return;dlg.close();dlg.classList.remove('opening-dialog','po-dialog');dlg.innerHTML=''}
      $('#poEdClose').onclick=close;$('#poEdClose2').onclick=close
      $('#poRemarks').oninput=()=>{draft.remarks=$('#poRemarks').value;touch()}
      bindOpeningSearchPicker({
        input:$('#poSupplier'),list:$('#poSupplierList'),clearBtn:$('#poSupplierClear'),hint:$('#poSupplierHint'),
        getItems:()=>(state.data.suppliers||[]).filter(s=>s.active!==false).filter(s=>{
          const q=($('#poSupplier').value||'').trim().toLowerCase()
          return !q||String(s.name).toLowerCase().includes(q)
        }).map(s=>({id:s.id,label:s.name,html:esc(s.name),raw:s})),
        emptyText:'該当する仕入先がありません',
        isConfirmed:()=>!!(state.data.suppliers||[]).find(s=>s.id===draft.supplierId),
        confirmedLabel:()=>((state.data.suppliers||[]).find(s=>s.id===draft.supplierId)||{}).name||'',
        onPick:item=>{draft.supplierId=item.id;draft.supplierQuery=item.label;draft.products=[];touch();render()},
        onClear:()=>{draft.supplierId=null;draft.supplierQuery='';draft.products=[];touch();render()},
        onQueryChange:v=>{draft.supplierQuery=v;if(draft.supplierId){draft.supplierId=null;draft.products=[];render();return true}return false},
      })
      bindOpeningSearchPicker({
        input:$('#poCustomer'),list:$('#poCustomerList'),clearBtn:$('#poCustomerClear'),hint:$('#poCustomerHint'),
        getItems:()=>filterOpeningCustomers(state.data.customers,$('#poCustomer').value).map(c=>({id:c.id,label:c.name,html:esc(c.name),raw:c})),
        emptyText:'該当する顧客がありません',
        isConfirmed:()=>!!resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId),
        confirmedLabel:()=>(resolveConfirmedOpeningCustomer(state.data.customers,draft.customerId)||{}).name||'',
        onPick:item=>{draft.customerId=item.id;draft.customerQuery=item.label;draft.products=[];touch();render()},
        onClear:()=>{draft.customerId=null;draft.customerQuery='';draft.products=[];touch();render()},
        onQueryChange:v=>{draft.customerQuery=v;if(draft.customerId){draft.customerId=null;draft.products=[];render();return true}return false},
      })
      const paintProducts=()=>{
        const box=$('#poProductRows')
        if(!draft.products.length){box.innerHTML='<p>商品を追加してください。</p>';return}
        box.innerHTML=draft.products.map((p,i)=>{
          const sizes=p.sizeList&&p.sizeList.length?p.sizeList:(state.data.products.find(x=>x.id===p.productId)||{sizes:[]}).sizes
          p.sizeList=sizes
          return `<article class="po-product-block">
            <div class="po-product-head">
              <strong>${esc(p.code)} ${esc(p.name)} ${esc(p.color)}</strong>
              <span class="sub">${esc(p.embroideryLabel||'刺繍なし')}（社内）</span>
              <button type="button" data-rm-prod="${i}">外す</button>
            </div>
            <div class="po-size-matrix">${sizes.map(sz=>`<label class="po-size-cell">${esc(sz)}
              <input data-sz="${i}:${esc(sz)}" inputmode="numeric" value="${esc(p.sizes[sz]??'')}">
            </label>`).join('')}</div>
            <label>明細備考（メーカーへの刺繍・プリント依頼など）<input data-note="${i}" value="${esc(p.note||'')}"></label>
          </article>`
        }).join('')
        box.querySelectorAll('[data-rm-prod]').forEach(b=>b.onclick=()=>{draft.products.splice(Number(b.dataset.rmProd),1);touch();paintProducts()})
        box.querySelectorAll('[data-sz]').forEach(inp=>{
          inp.oninput=()=>{
            const [idx,sz]=inp.dataset.sz.split(':')
            const v=inp.value.trim()
            if(!v)delete draft.products[Number(idx)].sizes[sz]
            else draft.products[Number(idx)].sizes[sz]=v
            touch()
          }
        })
        box.querySelectorAll('[data-note]').forEach(inp=>{
          inp.oninput=()=>{draft.products[Number(inp.dataset.note)].note=inp.value;touch()}
        })
      }
      paintProducts()
      const loadCand=async()=>{
        if(!draft.customerId||!draft.supplierId)return
        const q=new URLSearchParams({customerId:draft.customerId,supplierId:draft.supplierId})
        const qq=$('#poProdQ').value.trim();if(qq)q.set('q',qq)
        const data=await api('/api/purchase-order-products?'+q.toString())
        draft.candidates=data.products||[]
        const box=$('#poProdCandidates')
        const taken=new Set(draft.products.map(p=>p.customerProductId))
        const list=draft.candidates.filter(p=>!taken.has(p.customerProductId))
        box.hidden=!list.length
        box.innerHTML=list.slice(0,40).map(p=>`<button type="button" class="po-cand" data-cp="${p.customerProductId}">${esc(p.label)}</button>`).join('')||'<span class="sub">候補がありません</span>'
        box.querySelectorAll('[data-cp]').forEach(b=>b.onclick=()=>{
          const p=draft.candidates.find(x=>String(x.customerProductId)===b.dataset.cp)
          if(!p)return
          draft.products.push({
            customerProductId:p.customerProductId,productId:p.productId,
            code:p.code,name:p.name,color:p.color,
            embroidery:p.embroidery,embroideryLabel:p.embroideryLabel,
            sizes:{},note:'',sizeList:p.sizes||[],
          })
          touch();paintProducts();loadCand().catch(()=>{})
        })
      }
      $('#poAddProd').onclick=()=>loadCand().catch(e=>{$('#poEdErr').textContent=e.message})
      let cq=null
      $('#poProdQ').oninput=()=>{clearTimeout(cq);cq=setTimeout(()=>loadCand().catch(()=>{}),200)}
      if(draft.customerId&&draft.supplierId)loadCand().catch(()=>{})
      const collectBody=()=>({
        id:draft.id||undefined,
        token:draft.token,
        supplierId:draft.supplierId,
        customerId:draft.customerId,
        remarks:$('#poRemarks').value,
        lines:draft.products.map(p=>({
          customerProductId:p.customerProductId,
          sizes:p.sizes,
          note:p.note||'',
        })),
      })
      $('#poPreview').onclick=async()=>{
        try{await PurchaseOrders.showHtmlPreview(collectBody(),null)}catch(e){$('#poEdErr').textContent=e.message}
      }
      $('#poSave').onclick=async()=>{
        const err=$('#poEdErr');err.textContent=''
        try{
          const saved=await api('/api/purchase-orders',collectBody())
          toast(saved.already?'同じ内容は保存済みです。':'発注書を保存しました（まだ未発注です）。')
          draft.dirty=false
          dlg.close();dlg.classList.remove('opening-dialog','po-dialog');dlg.innerHTML=''
          await PurchaseOrders.loadList()
          paint()
          PurchaseOrders.renderDetail(saved)
        }catch(e){err.textContent=e.message}
      }
    }
    render()
    if(!dlg.open)dlg.showModal()
  },
  async showHtmlPreview(draftBody,poId){
    let html
    if(poId){
      const r=await fetch(BASE+'/api/purchase-orders/'+poId+'/html')
      if(!r.ok){const j=await r.json().catch(()=>({}));throw Error(j.error||'プレビューできません。')}
      html=await r.text()
    }else{
      const r=await fetch(BASE+'/api/purchase-orders/preview',{
        method:'POST',headers:{'Content-Type':'application/json','X-Stock-Request':'1'},
        body:JSON.stringify(draftBody||{}),
      })
      if(!r.ok){const j=await r.json().catch(()=>({}));throw Error(j.error||'プレビューできません。')}
      html=await r.text()
    }
    const wrap=document.createElement('dialog')
    wrap.className='opening-dialog po-preview-dialog'
    wrap.innerHTML=`<div class="dialoghead"><h2>発注書プレビュー（A4）</h2><button type="button" class="quiet" id="poPrevClose">閉じる</button></div>
      <div class="dialogbody po-preview-body"><iframe id="poPrevFrame" title="発注書"></iframe></div>
      <div class="dialogactions">
        <button type="button" id="poPrevClose2">戻る</button>
        <button type="button" class="primary" id="poPrevPrint">印刷 / PDF保存</button>
      </div>`
    document.body.appendChild(wrap)
    wrap.showModal()
    const frame=wrap.querySelector('#poPrevFrame')
    const doc=frame.contentDocument
    doc.open();doc.write(html);doc.close()
    const close=()=>{wrap.close();wrap.remove()}
    wrap.querySelector('#poPrevClose').onclick=close
    wrap.querySelector('#poPrevClose2').onclick=close
    wrap.querySelector('#poPrevPrint').onclick=()=>{
      try{frame.contentWindow.focus();frame.contentWindow.print()}catch(_){toast('印刷画面を開けませんでした。')}
    }
  },
  openReceive(po){
    const dlg=$('#dialog')
    dlg.classList.add('opening-dialog','po-dialog')
    const remain=(po.lines||[]).filter(l=>l.qtyRemaining>0)
    const draft={
      token:newToken(),
      receivedOn:todayISO(),
      locationId:null,siteId:'',shelfId:'',
      items:remain.map(l=>({lineId:l.id,lineNo:l.lineNo,label:`${l.code} ${l.productName} ${l.size}`,remaining:l.qtyRemaining,qty:l.qtyRemaining,unitCost:'',enabled:true})),
    }
    const render=()=>{
      const keep=state.opening
      state.opening={siteId:draft.siteId,shelfId:draft.shelfId,locationId:draft.locationId}
      const locSel=openingLocationSelection()
      state.opening=keep
      draft.siteId=locSel.siteId==null?'':String(locSel.siteId)
      draft.shelfId=locSel.shelfId
      draft.locationId=locSel.locationId
      const locFields=openingLocationFields(locSel)
        .replace(/id="openingSite"/g,'id="poRecvSite"')
        .replace(/id="openingShelf"/g,'id="poRecvShelf"')
        .replace(/id="openingShelfLabel"/g,'id="poRecvShelfLabel"')
      dlg.innerHTML=`<div class="dialoghead"><h2>入荷を登録（発注 ${esc(po.number)}）</h2><button type="button" id="poRecvClose" class="quiet">閉じる</button></div>
        <div class="dialogbody">
          <p class="notice">確定すると通常の入庫と同じ経路で在庫が増えます。発注時点では在庫は増えません。残数を超える入荷はできません。同じ内容を入庫画面で再登録する必要はありません。</p>
          <div class="grid">
            <label>入荷日<input id="poRecvDate" type="date" value="${esc(draft.receivedOn)}"></label>
            ${locFields}
          </div>
          <div class="tablewrap"><table><thead><tr><th>入荷</th><th>明細</th><th>残</th><th>今回数量</th><th>仕入単価</th></tr></thead><tbody>
            ${draft.items.map((it,i)=>`<tr>
              <td><input type="checkbox" data-en="${i}" ${it.enabled?'checked':''}></td>
              <td>${esc(it.label)}<span class="sub">#${it.lineNo}</span></td>
              <td class="qty">${num(it.remaining)}</td>
              <td><input data-qty="${i}" inputmode="numeric" value="${esc(it.qty)}"></td>
              <td><input data-cost="${i}" inputmode="numeric" value="${esc(it.unitCost)}"></td>
            </tr>`).join('')||'<tr><td colspan="5">入荷できる残がありません。</td></tr>'}
          </tbody></table></div>
          <p id="poRecvErr"></p>
        </div>
        <div class="dialogactions">
          <button type="button" id="poRecvClose2">戻る</button>
          <button type="button" class="primary" id="poRecvGo">入荷を確定</button>
        </div>`
      const back=()=>{PurchaseOrders.renderDetail(po)}
      $('#poRecvClose').onclick=back;$('#poRecvClose2').onclick=back
      $('#poRecvDate').oninput=()=>{draft.receivedOn=$('#poRecvDate').value}
      const siteEl=$('#poRecvSite');if(siteEl)siteEl.onchange=()=>{draft.siteId=siteEl.value;draft.shelfId='';draft.locationId=null;render()}
      const shelfEl=$('#poRecvShelf');if(shelfEl)shelfEl.onchange=()=>{draft.shelfId=shelfEl.value;render()}
      dlg.querySelectorAll('[data-en]').forEach(el=>el.onchange=()=>{draft.items[Number(el.dataset.en)].enabled=el.checked})
      dlg.querySelectorAll('[data-qty]').forEach(el=>el.oninput=()=>{draft.items[Number(el.dataset.qty)].qty=el.value})
      dlg.querySelectorAll('[data-cost]').forEach(el=>el.oninput=()=>{draft.items[Number(el.dataset.cost)].unitCost=el.value})
      $('#poRecvGo').onclick=async()=>{
        const err=$('#poRecvErr');err.textContent=''
        try{
          const items=draft.items.filter(it=>it.enabled).map(it=>({lineId:it.lineId,qty:Number(it.qty),unitCost:it.unitCost}))
          if(!items.length)throw Error('入荷する明細にチェックを入れてください。')
          const next=await api('/api/purchase-orders/receive',{
            id:po.id,token:draft.token,receivedOn:draft.receivedOn,locationId:draft.locationId,items,
          })
          toast(next.already?'同じ入荷は登録済みです。':'入荷を登録しました。')
          PurchaseOrders.renderDetail(next)
        }catch(e){err.textContent=e.message}
      }
    }
    render()
    if(!dlg.open)dlg.showModal()
  },
}

global.PurchaseOrders=PurchaseOrders
})(window)
