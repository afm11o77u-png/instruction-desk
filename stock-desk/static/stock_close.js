/** 月末締めの記録と、在庫更新の停止。app.js の api / $ / esc / toast / stockCanWrite / stockCanHold を使う。 */
const StockClose = {
  data: null,
  detail: null,
  async load(){
    this.data = await api('/api/stock-close')
    if(state.data)state.data.stockHold = this.data.hold
  },
  async page(){
    if(!this.data)await this.load()
    const d = this.data || {hold:{held:false}, records:[], notices:[]}
    const hold = d.hold || {}
    const canHold = typeof stockCanHold==='function' && stockCanHold()
    const canSave = typeof stockCanWrite==='function' && stockCanWrite()
    const month = new Date()
    const ym = `${month.getFullYear()}-${String(month.getMonth()+1).padStart(2,'0')}`
    $('#main').innerHTML = `<section class="panel">
      <p class="notice">現物の入出庫も止める必要があります。停止中は通常の在庫更新を拒否し、棚卸の反映は続けられます。保存した締め記録は、その後の通常在庫や棚卸反映では変わりません。記録の日時は棚卸で自動では直しません。経理上、棚卸の前か後かはここでは決めません。</p>
      <p>更新の停止: <strong>${hold.held?'停止中':'停止していません'}</strong>${hold.updatedAt?`（${esc(hold.updatedAt)} ${esc(hold.actorName||'')}）`:''}</p>
      <div class="bar">
        <button type="button" id="scHold" ${canHold&&!hold.held?'':'disabled'}>在庫更新を停止</button>
        <button type="button" id="scRelease" ${canHold&&hold.held?'':'disabled'}>停止を解除</button>
        ${canHold?'':'<span class="excelhint">停止と解除は、現場（倉庫担当）と主管理者だけです。</span>'}
      </div>
      <div class="bar">
        <label>対象月<input id="scMonth" type="month" value="${ym}"></label>
        <button type="button" class="primary" id="scSave" ${canSave?'':'disabled'}>この時点の在庫を記録</button>
        ${canSave?'':'<span class="excelhint">記録の保存は、在庫を変更できる人（主管理者と管理者）が行います。停止とは別です。</span>'}
      </div>
      <div class="tablewrap"><table><thead><tr><th>対象月</th><th>版</th><th>保存日時</th><th>操作者</th><th>ロット</th><th>数量</th><th>金額</th><th></th></tr></thead>
        <tbody>${(d.records||[]).map(r=>`<tr><td>${esc(r.month)}</td><td>${r.version}</td><td>${esc(r.savedAt)}</td><td>${esc(r.actorName)}</td><td>${r.lotCount}</td><td>${r.qtyTotal}</td><td>${r.amountTotal==null?'':r.amountTotal}</td><td><button type="button" data-sc-id="${r.id}">中身</button></td></tr>`).join('')||'<tr><td colspan="8">記録はまだありません。</td></tr>'}</tbody>
      </table></div>
      <div id="scDetail"></div>
    </section>`
    const reload = async ()=>{this.data=null;await this.load();await this.page()}
    const holdBtn=$('#scHold'), rel=$('#scRelease'), save=$('#scSave')
    if(holdBtn)holdBtn.onclick=async()=>{try{await api('/api/stock-close/hold',{});toast('在庫の更新を停止しました。');if(state.data)state.data.stockHold={held:true};await reload()}catch(e){toast(e.message)}}
    if(rel)rel.onclick=async()=>{try{await api('/api/stock-close/release',{});toast('在庫の更新停止を解除しました。');if(state.data)state.data.stockHold={held:false};await reload()}catch(e){toast(e.message)}}
    if(save)save.onclick=async()=>{
      try{
        const rec=await api('/api/stock-close/save',{month:$('#scMonth').value})
        toast(`${rec.month} の記録（版${rec.version}）を保存しました。停止の状態は変えていません。`)
        await reload()
      }catch(e){toast(e.message)}
    }
    document.querySelectorAll('[data-sc-id]').forEach(b=>b.onclick=async()=>{
      try{
        const rec=await api('/api/stock-close/record?id='+encodeURIComponent(b.dataset.scId))
        const box=$('#scDetail')
        if(!box)return
        box.innerHTML=`<h3>${esc(rec.month)} 版${rec.version}（${esc(rec.savedAt)}）</h3>
          <div class="tablewrap"><table><thead><tr><th>ロット</th><th>顧客</th><th>品番</th><th>商品</th><th>サイズ</th><th>状態</th><th>場所</th><th>数量</th><th>単価</th><th>金額</th></tr></thead>
          <tbody>${(rec.lines||[]).map(l=>`<tr><td>${l.lotId}</td><td>${esc(l.customerName)}</td><td>${esc(l.code)}</td><td>${esc(l.productName)}</td><td>${esc(l.size)}</td><td>${esc(l.conditionLabel)}</td><td>${esc(l.locationLabel)}</td><td>${l.qty}</td><td>${l.unitCost}</td><td>${l.amount==null?'原価未確定':l.amount}</td></tr>`).join('')}</tbody></table></div>`
      }catch(e){toast(e.message)}
    })
  },
}
