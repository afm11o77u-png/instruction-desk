/* 現単価: 初期設定・要確認一覧・取り込みルール・商品マスタの現単価/特値 */
const CurrentPrice=(()=>{
  const yenv=v=>v==null?'—':Number(v).toLocaleString('ja-JP')
  function dlg(){
    let d=document.getElementById('cpDialog')
    if(!d){d=document.createElement('dialog');d.id='cpDialog';d.className='cp-dialog';document.body.appendChild(d)}
    return d
  }
  function show(title,body){
    const d=dlg()
    d.innerHTML=`<form method="dialog"><div class="dialoghead"><h2>${esc(title)}</h2><button type="button" class="quiet" data-cp-close>閉じる</button></div><div class="dialogbody">${body}</div><p class="cp-error" id="cpError"></p></form>`
    d.querySelector('form').onsubmit=e=>e.preventDefault()
    d.querySelector('[data-cp-close]').onclick=()=>{d.close();d.innerHTML=''}
    if(!d.open)d.showModal()
    return d
  }
  const err=(d,e)=>{const el=d.querySelector('#cpError');if(el)el.textContent=e.message||String(e)}
  const fileB64=f=>new Promise((ok,ng)=>{const r=new FileReader();r.onload=()=>ok(String(r.result).split(',')[1]||'');r.onerror=()=>ng(Error('ファイルを読めませんでした。'));r.readAsDataURL(f)})
  const tok=()=>Math.random().toString(36).slice(2)

  function summaryHtml(s){
    if(!s)return ''
    const rs=Object.entries(s.reviewByReason||{}).map(([k,v])=>`<li>${esc(k)}: ${v}</li>`).join('')
    const ig=Object.entries(s.ignoredByReason||{}).map(([k,v])=>`${esc(k)} ${v}`).join('、')
    return `<ul class="cp-summary"><li>単価を設定: ${s.set}（棚卸 ${s.fromStocktake} / 旧データ ${s.fromDb}）</li><li>要確認: ${s.review}</li>${rs?`<ul>${rs}</ul>`:''}<li>未設定: ${s.unset}</li><li>棚卸 ${s.stocktakeRows}行 → 照合 ${s.matched} / 対象外 ${s.ignored}${ig?`（${ig}）`:''}</li></ul>`
  }
  function openInit(){
    const d=show('現単価の初期設定',`<p class="excelhint">毎回同じルールで現単価を上書きします。在庫数は変わりません。</p>
      <label>棚卸Excel（.xlsx）<input type="file" id="cpFile" accept=".xlsx"></label>
      <div class="bar"><button type="button" class="primary" id="cpRun">実行</button><button type="button" class="quiet" id="cpRules">取り込みルール</button><button type="button" class="quiet" id="cpReview">要確認一覧</button></div>
      <div id="cpResult"></div>`)
    d.querySelector('#cpRules').onclick=()=>openRules()
    d.querySelector('#cpReview').onclick=()=>openReview()
    d.querySelector('#cpRun').onclick=async()=>{
      const f=d.querySelector('#cpFile').files[0]
      if(!f&&!confirm('棚卸Excelなしで、旧データだけで実行しますか？'))return
      const btn=d.querySelector('#cpRun');btn.disabled=true
      try{
        const body={token:tok()}
        if(f){body.file=await fileB64(f);body.name=f.name}
        const r=await api('/api/prices/initial',body)
        const ig=(r.ignoredRows||[]).map(x=>`<tr><td>${x.rowNo}</td><td>${esc(x.customer)}</td><td>${esc(x.code)}</td><td>${esc(x.name)}</td><td>${esc(x.size)}</td><td class="money">${yenv(x.price)}</td><td>${esc(x.reason)}</td></tr>`).join('')
        d.querySelector('#cpResult').innerHTML=summaryHtml(r.summary)+(ig?`<details><summary>対象外 ${r.ignoredRows.length}行</summary><div class="tablewrap"><table><thead><tr><th>行</th><th>顧客</th><th>品番</th><th>商品名</th><th>サイズ</th><th>単価</th><th>理由</th></tr></thead><tbody>${ig}</tbody></table></div></details>`:'')
        toast('現単価を設定しました。')
      }catch(e){err(d,e)}finally{btn.disabled=false}
    }
  }

  async function openReview(){
    const d=show('要確認・未設定','<p>読み込み中…</p>')
    const paint=async()=>{
      try{
        const r=await api('/api/prices/review')
        const rows=r.items.map(i=>`<tr data-id="${i.id}"><td>${esc(i.statusLabel)}</td><td>${esc(i.supplierName)}</td><td>${esc(i.code)}</td><td>${esc(i.productName)} ${esc(i.color)}</td><td>${esc(i.size)}</td><td>${esc(i.reason)}</td><td><input class="cp-price" inputmode="numeric" value="${i.price??''}"></td><td><button type="button" data-save>保存</button></td></tr>`).join('')
        d.querySelector('.dialogbody').innerHTML=`<p class="excelhint">単価を保存すると要確認は外れます。</p><div class="tablewrap"><table><thead><tr><th>状態</th><th>仕入先</th><th>品番</th><th>商品</th><th>サイズ</th><th>理由</th><th>現単価</th><th></th></tr></thead><tbody>${rows||'<tr><td colspan="8">ありません</td></tr>'}</tbody></table></div>`
        d.querySelectorAll('[data-save]').forEach(b=>b.onclick=async()=>{
          const tr=b.closest('tr')
          try{await api('/api/prices/base',{id:Number(tr.dataset.id),price:tr.querySelector('.cp-price').value});toast('保存しました。');paint()}catch(e){err(d,e)}
        })
      }catch(e){err(d,e)}
    }
    paint()
  }

  const RULES=`<ol class="cp-rules">
<li><strong>照合</strong>：棚卸Excelの1枚目の各行を「顧客＋商品コード＋サイズ」で登録済みの初期在庫と照らし合わせます。全角・半角の違いと空白は無視し、Excelで数字になった品番（105.0）は「105」として扱います。仕入先は一致した在庫データの仕入先を使います。</li>
<li><strong>品番の読み替え</strong>：一致しないときは順に試します。①下の読替表 ②末尾の括弧書きを外す（6991-099(2601へ変更)→6991-099） ③Excelが日付に変えた品番を戻す（44927→2023-01） ④先頭の0を補う（51→00051）。</li>
<li><strong>棚卸にある品</strong>：棚卸の単価のうち一番高いものを現単価にします（0円は除く）。旧データの最高単価と違うとき、顧客によって棚卸単価が2倍以上違うときは「要確認」にします。</li>
<li><strong>棚卸にない品</strong>：旧データ（0円を除く）の一番高い単価にします。ただし一番高い単価が一番多い単価の3倍以上なら、一番多い単価にして「要確認」にします。「一番多い」は在庫数が多い順、同じなら購入数が多い順です。</li>
<li><strong>0円しかない品</strong>：「未設定」にします。</li>
<li>単価のまとまりは「仕入先×商品×サイズ」です。実行するたびに同じルールで上書きします（手で直した単価も戻ります）。在庫数は変わりません。</li>
<li>一致しなかった行は対象外として一覧に出し、取り込みません。特値（顧客ごとの単価）は自動では作りません。</li>
</ol>`
  async function openRules(){
    const d=show('取り込みルール',RULES+'<h3>品番の読替表</h3><div id="cpMaps">読み込み中…</div>')
    const paint=async maps=>{
      if(!maps)maps=(await api('/api/prices/code-map')).maps
      const row=m=>`<tr data-id="${m.id||''}"><td><input data-k="customerName" value="${esc(m.customerName||'')}" placeholder="空欄=全顧客"></td><td><input data-k="stockCode" value="${esc(m.stockCode||'')}"></td><td><input data-k="realCode" value="${esc(m.realCode||'')}"></td><td><button type="button" data-save>${m.id?'保存':'追加'}</button>${m.id?' <button type="button" class="quiet" data-del>削除</button>':''}</td></tr>`
      d.querySelector('#cpMaps').innerHTML=`<div class="tablewrap"><table><thead><tr><th>顧客</th><th>棚卸の品番</th><th>正しい品番</th><th></th></tr></thead><tbody>${maps.map(row).join('')}${row({})}</tbody></table></div>`
      d.querySelectorAll('#cpMaps tr[data-id]').forEach(tr=>{
        const val=()=>Object.fromEntries([...tr.querySelectorAll('[data-k]')].map(i=>[i.dataset.k,i.value]))
        const id=tr.dataset.id?Number(tr.dataset.id):null
        const s=tr.querySelector('[data-save]'),x=tr.querySelector('[data-del]')
        if(s)s.onclick=async()=>{try{paint((await api('/api/prices/code-map',{...val(),id})).maps);toast('保存しました。')}catch(e){err(d,e)}}
        if(x)x.onclick=async()=>{if(!confirm('削除しますか？'))return;try{paint((await api('/api/prices/code-map',{id,delete:true})).maps)}catch(e){err(d,e)}}
      })
    }
    try{await paint()}catch(e){err(d,e)}
  }

  async function mountProduct(el,product){
    if(!el||!product||!product.id)return
    const pid=product.id
    const sizes=(product.sizes||[]).map(s=>typeof s==='string'?s:(s.label||s.size||'')).filter(Boolean)
    const customers=((state.data&&state.data.customers)||[]).filter(c=>c.active!==false)
    const suppliers=(state.data&&state.data.suppliers)||[]
    const paint=async()=>{
      let r
      try{r=await api('/api/prices/product?productId='+pid)}catch(e){el.innerHTML=`<p class="excelhint">${esc(e.message)}</p>`;return}
      const supIds=[...new Set([product.supplierId||0,...r.base.map(b=>b.supplierId),...(product.subSupplierIds||[])].map(Number))]
      const supName=id=>id?((suppliers.find(s=>s.id===id)||{}).name||''):'未設定'
      const base=r.base.map(b=>`<tr data-base-id="${b.id}"><td>${esc(b.supplierName||'未設定')}</td><td>${esc(b.size)}</td><td><input data-k="price" inputmode="numeric" value="${b.price}" aria-label="現単価"></td><td>${esc(b.statusLabel)}${b.reason?` <span class="sub">${esc(b.reason)}</span>`:''}</td><td><button type="button" data-save>保存</button></td></tr>`).join('')
      const baseSizeOpts=['<option value="">サイズ</option>'].concat(sizes.map(s=>`<option>${esc(s)}</option>`)).join('')
      const baseNew=`<tr data-base-new><td><select data-k="supplierId">${supIds.map(id=>`<option value="${id}">${esc(supName(id))}</option>`).join('')}</select></td><td><select data-k="size">${baseSizeOpts}</select></td><td><input data-k="price" inputmode="numeric" aria-label="現単価"></td><td></td><td><button type="button" data-add>追加</button></td></tr>`
      const sizeOpts=sel=>['<option value="">全サイズ</option>'].concat(sizes.map(s=>`<option ${s===sel?'selected':''}>${esc(s)}</option>`)).join('')
      const sp=r.specials.map(s=>`<tr data-id="${s.id}"><td>${esc(s.customerName)}</td><td>${esc(s.supplierName||'未設定')}</td><td><select data-k="size">${sizeOpts(s.size||'')}</select></td><td><input data-k="price" inputmode="numeric" value="${s.price}"></td><td><button type="button" data-save>保存</button> <button type="button" class="quiet" data-del>削除</button></td></tr>`).join('')
      el.innerHTML=`<h2>現単価</h2><div class="tablewrap"><table><thead><tr><th>仕入先</th><th>サイズ</th><th>現単価</th><th>状態</th><th></th></tr></thead><tbody>${base}${baseNew}</tbody></table></div>
        <h3>特値</h3><div class="tablewrap"><table><thead><tr><th>顧客</th><th>仕入先</th><th>サイズ</th><th>単価</th><th></th></tr></thead><tbody>${sp}
        <tr data-new><td><select data-k="customerId"><option value="">顧客</option>${customers.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></td><td><select data-k="supplierId">${supIds.map(id=>`<option value="${id}">${esc(supName(id))}</option>`).join('')}</select></td><td><select data-k="size">${sizeOpts('')}</select></td><td><input data-k="price" inputmode="numeric"></td><td><button type="button" data-add>追加</button></td></tr></tbody></table></div><p class="cp-error" data-cp-err></p>`
      const fail=e=>{el.querySelector('[data-cp-err]').textContent=e.message}
      const val=tr=>Object.fromEntries([...tr.querySelectorAll('[data-k]')].map(i=>[i.dataset.k,i.value]))
      el.querySelectorAll('tr[data-base-id]').forEach(tr=>{
        tr.querySelector('[data-save]').onclick=async()=>{try{await api('/api/prices/base',{id:Number(tr.dataset.baseId),price:val(tr).price});toast('保存しました。');paint()}catch(e){fail(e)}}
      })
      const bn=el.querySelector('tr[data-base-new]')
      bn.querySelector('[data-add]').onclick=async()=>{try{await api('/api/prices/base',{...val(bn),productId:pid});toast('追加しました。');paint()}catch(e){fail(e)}}
      el.querySelectorAll('tr[data-id]').forEach(tr=>{
        const id=Number(tr.dataset.id)
        tr.querySelector('[data-save]').onclick=async()=>{try{await api('/api/prices/special',{...val(tr),id});toast('保存しました。');paint()}catch(e){fail(e)}}
        tr.querySelector('[data-del]').onclick=async()=>{if(!confirm('特値を削除しますか？'))return;try{await api('/api/prices/special',{id,delete:true});paint()}catch(e){fail(e)}}
      })
      const nr=el.querySelector('tr[data-new]')
      nr.querySelector('[data-add]').onclick=async()=>{try{await api('/api/prices/special',{...val(nr),productId:pid});toast('追加しました。');paint()}catch(e){fail(e)}}
    }
    // 現単価・特値の入力は商品の「保存」とは別に即時保存。編集中フラグを立てない
    el.addEventListener('input',e=>e.stopPropagation())
    el.addEventListener('change',e=>e.stopPropagation())
    el.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.tagName==='INPUT')e.preventDefault()})
    paint()
  }
  // 商品一覧の「単価」：現単価と特値の確認のみ（編集は商品フォームで）
  async function openProductView(product){
    const d=show(`${product.code||''} ${product.name||''}`.trim()+' の単価','<p class="excelhint">読み込み中…</p>')
    const body=d.querySelector('.dialogbody')
    let r
    try{r=await api('/api/prices/product?productId='+product.id)}catch(e){body.innerHTML=`<p class="cp-error">${esc(e.message)}</p>`;return}
    if(!d.open)return
    const st=b=>b.status==='unset'?'<span class="badge stop">未設定</span>':b.status==='review'?`<span class="badge warn">要確認</span>${b.reason?` <span class="sub">${esc(b.reason)}</span>`:''}`:''
    const base=r.base.map(b=>`<tr><td>${esc(b.supplierName||'未設定')}</td><td>${esc(b.size)}</td><td class="money">${b.status==='unset'?'—':yenv(b.price)}</td><td>${st(b)}</td></tr>`).join('')
    const sp=r.specials.map(s=>`<tr><td>${esc(s.customerName)}</td><td>${esc(s.supplierName||'未設定')}</td><td>${esc(s.size||'全サイズ')}</td><td class="money">${yenv(s.price)}</td></tr>`).join('')
    body.innerHTML=`<h3>現単価</h3>${base?`<div class="tablewrap"><table><thead><tr><th>仕入先</th><th>サイズ</th><th>現単価</th><th>状態</th></tr></thead><tbody>${base}</tbody></table></div>`:'<p>なし</p>'}
      <h3>特値</h3>${sp?`<div class="tablewrap"><table><thead><tr><th>顧客</th><th>仕入先</th><th>サイズ</th><th>単価</th></tr></thead><tbody>${sp}</tbody></table></div>`:'<p>なし</p>'}`
  }
  return {openInit,openReview,openRules,mountProduct,openProductView}
})()
