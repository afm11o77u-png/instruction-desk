/* 月末締め UI。app.js の state / api / $ / toast / stockCanWrite を使う。 */
(function(global){
'use strict'
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function num(n){return n==null?'':Number(n).toLocaleString('ja-JP')}
function money(n){return n==null?'原価未確定':Number(n).toLocaleString('ja-JP')}
function at(s){return String(s||'').replace('T',' ').slice(0,16)}
function thisMonth(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`}

const CSS=`.mc-help{margin:8px 0 12px;padding:10px 12px;background:#f7fafc;border:1px solid #c5d6e8;border-radius:6px}
.mc-flow{margin:6px 0 0;padding-left:1.4em;line-height:1.55}
.mc-flow li{margin:2px 0}`
function ensureCss(){if(!document.getElementById('month-close-css')){const s=document.createElement('style');s.id='month-close-css';s.textContent=CSS;document.head.appendChild(s)}}
const FLOW=`<ol class="mc-flow">
<li><b>締め</b>を押すと、その瞬間の全在庫の数量と金額を記録します（経理はこの記録を使います）。</li>
<li>入出庫は止めなくて構いません。</li>
<li>棚卸表に顧客をいつ追加しても構いません。表の「在庫数」「在庫金額」は締め時の記録です。</li>
<li>数えて実棚を入力します（数えている最中はその商品を動かさないでください）。</li>
<li>「実棚を反映」で、<b>実棚 − 締め時在庫数</b>の差だけを今の在庫に増減します。</li>
<li>例：締め10・実棚9・数えたあと3枚出庫して今7 → 反映すると6（棚の実物と一致）。</li>
</ol>`

const MonthClose={
  async loadList(){
    state.mcList=await api('/api/month-closes')
    if(!state.mcMonth)state.mcMonth=thisMonth()
    state.mcCur=await api('/api/month-closes/get?month='+encodeURIComponent(state.mcMonth))
  },
  page(){
    ensureCss()
    const L=state.mcList||{months:[]}
    const s=state.mcCur||{month:state.mcMonth||thisMonth(),closed:false,rows:[]}
    const w=stockCanWrite()
    $('#main').innerHTML=`
    <div class="bar"><b>月末締め</b>
      <label>対象月<input type="month" id="mcMonth" value="${esc(s.month)}"></label>
      ${w?`<button type="button" class="primary" id="mcClose">締め</button>`:''}
      ${s.closed?`<button type="button" id="mcXlsx">Excel出力</button>`:''}
    </div>
    <div class="mc-help"><p class="notice">棚卸の基準になる記録です。流れ：</p>${FLOW}</div>
    ${s.closed?`<p class="sub">締め済み ${esc(at(s.header.closedAt))} ${esc(s.header.closedBy)} ／ ${num(s.rowCount)}行 ／ 数量合計 ${num(s.totalQty)} ／ 金額合計 ${money(s.totalAmount)}</p>
      <div class="tablewrap"><table class="data"><thead><tr>
        <th>顧客</th><th>仕入先</th><th>品番</th><th>商品名</th><th>サイズ</th><th>場所</th><th>状態</th><th class="n">単価</th><th class="n">数量</th><th class="n">金額</th>
      </tr></thead><tbody>
      ${s.rows.map(r=>`<tr>
        <td>${esc(r.customerName)}</td><td>${esc(r.supplierName)}</td><td>${esc(r.productCode)}</td>
        <td>${esc(r.productName)}${r.color?' '+esc(r.color):''}</td><td>${esc(r.size)}</td>
        <td>${esc(r.locationLabel)}</td><td>${esc(r.conditionLabel)}</td>
        <td class="n">${r.costPending?'原価未確定':num(r.unitCost)}</td>
        <td class="n">${num(r.qty)}</td><td class="n">${money(r.amount)}</td>
      </tr>`).join('')||'<tr><td colspan="10">在庫はありませんでした。</td></tr>'}
      </tbody></table></div>`
    :`<p class="notice">この月はまだ締めていません。</p>`}
    <h3>過去の締め</h3>
    <div class="tablewrap"><table class="data"><thead><tr><th>月</th><th>日時</th><th>実行者</th><th class="n">行数</th><th></th></tr></thead><tbody>
      ${(L.months||[]).map(m=>`<tr><td>${esc(m.month)}</td><td>${esc(at(m.closedAt))}</td><td>${esc(m.closedBy)}</td><td class="n">${num(m.rowCount)}</td>
        <td><button type="button" data-mc-month="${esc(m.month)}">開く</button></td></tr>`).join('')||'<tr><td colspan="5">まだありません。</td></tr>'}
    </tbody></table></div>`
    const load=async month=>{state.mcMonth=month;state.mcCur=await api('/api/month-closes/get?month='+encodeURIComponent(month));this.page()}
    $('#mcMonth').onchange=async()=>{try{await load($('#mcMonth').value)}catch(e){toast(e.message)}}
    document.querySelectorAll('[data-mc-month]').forEach(b=>b.onclick=async()=>{try{await load(b.dataset.mcMonth)}catch(e){toast(e.message)}})
    if($('#mcXlsx'))$('#mcXlsx').onclick=async()=>{
      try{
        const r=await fetch('/api/month-closes/export.xlsx?month='+encodeURIComponent(s.month))
        if(!r.ok){const j=await r.json().catch(()=>({}));throw Error(j.error||'出力できませんでした。')}
        const a=document.createElement('a');a.href=URL.createObjectURL(await r.blob());a.download=`月末締め_${s.month}.xlsx`;a.click()
      }catch(e){toast(e.message)}
    }
    if($('#mcClose'))$('#mcClose').onclick=async()=>{
      const month=$('#mcMonth').value
      try{
        let body={month}
        const cur=await api('/api/month-closes/get?month='+encodeURIComponent(month))
        if(cur.closed){
          if(!confirm(`${month} は締め済みです（${at(cur.header.closedAt)} ${cur.header.closedBy}）。上書きしますか？`))return
          body.confirm=true
        }else if(!confirm(`${month} の在庫を締めますか？`))return
        state.mcCur=await api('/api/month-closes/close',body)
        state.mcList=await api('/api/month-closes')
        state.mcMonth=month
        toast('締めました。');this.page()
      }catch(e){toast(e.message)}
    }
  },
}
global.MonthClose=MonthClose
})(window)
