/* 棚卸（顧客×月）UI。app.js の state / api / $ / toast / stockCanWrite を使う。 */
(function(global){
'use strict'
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function num(n){return n==null?'':Number(n).toLocaleString('ja-JP')}
function money(n){return n==null?'<span class="sub">原価未確定</span>':Number(n).toLocaleString('ja-JP')+'円'}
function signed(n,f){if(n==null)return '';return (n>0?'+':'')+f(n)}
function thisMonth(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`}
function at(s){return String(s||'').replace('T',' ').slice(0,16)}

function unitText(r){
  if(r.unitCost!=null)return Number(r.unitCost).toLocaleString('ja-JP')+'円'
  return r.unitMixed?'複数':'<span class="sub">原価未確定</span>'
}
function sizeText(r,multi){
  const extra=[]
  if(multi.loc)extra.push(r.locationLabel)
  if(multi.cond)extra.push(r.conditionLabel)
  return esc(r.size)+(extra.length?`<span class="sub"> ${esc(extra.join('・'))}</span>`:'')
}

const Stocktake={
  async loadList(){
    state.stList=await api('/api/stocktakes')
    if(state.stId)state.stCur=await api('/api/stocktakes/get?id='+state.stId)
    else state.stCur=null
  },
  page(){
    if(state.stCur)return this.sheet()
    const w=stockCanWrite()
    const L=state.stList||{stocktakes:[],customers:[]}
    $('#main').innerHTML=`<section class="panel">
      ${w?`<div class="bar">
        <input id="stMonth" type="month" value="${esc(state.stMonth||thisMonth())}">
        <input id="stFind" type="search" placeholder="顧客を検索">
        <button type="button" class="primary" id="stCreate">選んだ顧客の棚卸表を作成</button>
        <span class="sub" id="stPicked"></span>
      </div>
      <div id="stCustList" style="max-height:220px;overflow:auto;border:1px solid #d5ead4;border-radius:8px;padding:6px;margin:0 0 12px">
        ${L.customers.map(c=>`<label class="check" data-st-name="${esc(c.name+' '+c.kana)}" style="display:block"><input type="checkbox" value="${c.id}"> ${esc(c.name)}</label>`).join('')||'<span class="sub">顧客がありません。</span>'}
      </div>`:''}
      <table><thead><tr><th>年月</th><th>顧客</th><th>入力</th><th>状態</th><th></th></tr></thead><tbody>
      ${L.stocktakes.map(s=>`<tr><td>${esc(s.month)}</td><td>${esc(s.customerName)}</td><td>${s.enteredCount}/${s.rowCount}</td><td>${s.appliedAt?`反映済 ${esc(at(s.appliedAt))}`:'入力中'}</td><td><button type="button" data-st-open="${s.id}">開く</button></td></tr>`).join('')||'<tr><td colspan="5">棚卸はまだありません。</td></tr>'}
      </tbody></table></section>`
    const picked=()=>[...document.querySelectorAll('#stCustList input:checked')].map(i=>Number(i.value))
    const showPicked=()=>{const el=$('#stPicked');if(el)el.textContent=picked().length?`${picked().length}件選択`:''}
    document.querySelectorAll('#stCustList input').forEach(i=>i.onchange=showPicked)
    const find=$('#stFind')
    if(find)find.oninput=()=>{const q=find.value.trim();document.querySelectorAll('#stCustList [data-st-name]').forEach(l=>{l.hidden=!!q&&!l.dataset.stName.includes(q)})}
    const btn=$('#stCreate')
    if(btn)btn.onclick=async()=>{
      const ids=picked()
      if(!ids.length)return toast('顧客を選んでください。')
      const month=$('#stMonth').value;state.stMonth=month
      let made=0,already=0,last=null
      try{
        for(const cid of ids){
          const r=await api('/api/stocktakes/create',{customerId:cid,month})
          r.already?already++:made++;last=r
        }
      }catch(e){toast(e.message)}
      if(!last)return
      if(ids.length===1){state.stId=last.id;state.stCur=last;return this.page()}
      toast(`${made}件作成${already?`・${already}件は作成済み`:''}`)
      state.stId=null;await this.loadList();this.page()
    }
    document.querySelectorAll('[data-st-open]').forEach(b=>b.onclick=async()=>{
      state.stId=Number(b.dataset.stOpen)
      try{state.stCur=await api('/api/stocktakes/get?id='+state.stId);this.page()}catch(e){toast(e.message)}
    })
  },
  sheet(){
    const s=state.stCur
    const edit=stockCanWrite()&&!s.applied
    const multi={loc:new Set(s.rows.map(r=>r.locationLabel)).size>1,cond:new Set(s.rows.map(r=>r.condition)).size>1}
    const t=s.totals
    $('#main').innerHTML=`<section class="panel">
      <div class="bar">
        <button type="button" id="stBack">一覧へ</button>
        <b>${esc(s.month)} ${esc(s.customerName)}</b>
        <span class="sub">${s.applied?`在庫に反映済（${esc(at(s.appliedAt))} ${esc(s.appliedBy)}）`:'入力中'}</span>
        ${edit?'<button type="button" id="stSave">保存</button><button type="button" class="primary" id="stApply">実棚で在庫を上書き</button>':''}
      </div>
      <div class="table-scroll"><table class="stocktake-table"><thead><tr>
        <th>コード</th><th>顧客</th><th>商品名</th><th>商品コード</th><th>サイズ</th><th>仕入単価</th><th>在庫数</th><th>在庫金額</th><th>実棚点数</th><th>在庫数との差</th><th>実棚金額</th><th>在庫金額との差</th>
      </tr></thead><tbody>
      ${s.rows.map(r=>`<tr>
        <td>${esc(r.officeCode)}</td><td>${esc(r.customerName)}</td><td>${esc(r.productName)}</td><td>${esc(r.productCode)}</td>
        <td>${sizeText(r,multi)}</td><td class="money">${unitText(r)}</td>
        <td class="qty">${num(r.bookQty)}</td><td class="money">${money(r.bookAmount)}</td>
        <td class="qty">${edit?`<input class="st-actual" data-row="${r.id}" type="number" inputmode="numeric" min="0" step="1" value="${r.actualQty??''}" style="width:6em">`:num(r.actualQty)}</td>
        <td class="qty">${signed(r.diffQty,num)}</td>
        <td class="money">${r.actualQty==null?'':money(r.actualAmount)}</td>
        <td class="money">${r.actualQty==null||r.actualAmount==null||r.bookAmount==null?'':signed(r.diffAmount,n=>Number(n).toLocaleString('ja-JP')+'円')}</td>
      </tr>`).join('')||'<tr><td colspan="12">この顧客の在庫はありません。</td></tr>'}
      </tbody><tfoot><tr><th colspan="6">合計</th><th class="qty">${num(t.bookQty)}</th><th class="money">${money(t.bookAmount)}</th><th class="qty">${num(t.actualQty)}</th><th></th><th class="money">${t.actualQty==null?'':money(t.actualAmount)}</th><th></th></tr></tfoot></table></div>
    </section>`
    $('#stBack').onclick=async()=>{state.stId=null;state.stCur=null;await this.loadList();this.page()}
    const rowsBody=()=>[...document.querySelectorAll('.st-actual')].map(i=>({id:Number(i.dataset.row),actualQty:i.value}))
    const save=async()=>{state.stCur=await api('/api/stocktakes/save',{id:s.id,rows:rowsBody()})}
    if($('#stSave'))$('#stSave').onclick=async()=>{try{await save();toast('保存しました。');this.page()}catch(e){toast(e.message)}}
    if($('#stApply'))$('#stApply').onclick=async()=>{
      try{
        await save()
        const n=state.stCur.rows.filter(r=>r.actualQty!=null&&r.diffQty!==0).length
        if(!confirm(`実棚点数で在庫を上書きします（差がある ${n} 行）。実行後は実棚を変更できません。よろしいですか？`)){this.page();return}
        const r=await api('/api/stocktakes/apply',{id:s.id,confirm:true})
        state.stCur=r;toast(`${r.adjusted}行を棚卸調整しました。`);this.page()
      }catch(e){toast(e.message)}
    }
  },
}
global.Stocktake=Stocktake
})(window)
