/* 棚卸（月ごとに1枚の棚卸表）UI。app.js の state / api / $ / toast / stockCanWrite を使う。
   見た目は棚卸フォーマット（Excel: 色付きヘッダー・細罫線・3桁区切り）に合わせる。 */
(function(global){
'use strict'
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function num(n){return n==null?'':Number(n).toLocaleString('ja-JP')}
function money(n){return n==null?'<span class="st-pending">原価未確定</span>':Number(n).toLocaleString('ja-JP')}
function signed(n){if(n==null)return '';return (n>0?'+':'')+Number(n).toLocaleString('ja-JP')}
function thisMonth(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`}
function at(s){return String(s||'').replace('T',' ').slice(0,16)}

const CSS=`
.st-wrap{overflow:auto;max-height:calc(100vh - 230px);border:1px solid #9e9e9e;background:#fff}
.st-grid{border-collapse:collapse;table-layout:fixed;font-size:12.5px;line-height:1.25;color:#000;width:max-content}
.st-grid th,.st-grid td{border:1px solid #9e9e9e;padding:2px 4px;height:20px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.st-grid thead th{position:sticky;top:0;z-index:2;font-weight:700;text-align:center;border-bottom:1px solid #7f7f7f}
.st-grid tfoot th{position:sticky;bottom:0;background:#f2f2f2;z-index:1}
.st-grid .n{text-align:right;font-variant-numeric:tabular-nums}
.st-grid .hA{background:#DDEBF7}.st-grid .hG{background:#C6E0B4}.st-grid .cI{background:#FFFF00}
.st-grid .cJ{background:#FFF2CC}.st-grid .cK{background:#A4C2F4}.st-grid .cL{background:#C9DAF8}.st-grid .hM{background:#fff}
.st-grid input{width:100%;box-sizing:border-box;border:0;background:transparent;font:inherit;padding:0;margin:0;height:18px;text-align:right}
.st-grid input.st-note{text-align:left}
.st-grid input:focus{outline:2px solid #1a73e8;background:#fff}
.st-grid tr.st-done td{color:#555}
.st-grid tr.st-block-start td{border-top:2px solid #595959}
.st-pending{color:#8a5a00;font-size:11px}
.st-chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 8px}
.st-chip{border:1px solid #c9dcc4;border-radius:12px;padding:2px 8px;font-size:12.5px;background:#f7fbf7}
.st-chip button{border:0;background:none;padding:0 0 0 4px;cursor:pointer;font-weight:700}
.st-pick{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.st-pick select{min-width:240px}`
function ensureCss(){if(!document.getElementById('stocktake-css')){const s=document.createElement('style');s.id='stocktake-css';s.textContent=CSS;document.head.appendChild(s)}}

const COLS=[['コード',48,'hA'],['顧客',196,'hA'],['商品名',196,'hA'],['商品コード',125,'hA'],['サイズ',80,'hA'],['仕入単価',64,'hA'],['在庫数',95,'hG'],['在庫金額',95,'hG'],['実棚点数',95,'cI'],['在庫数との差',98,'cJ'],['実棚金額',95,'cK'],['在庫金額との差',110,'cL'],['備考',112,'hM']]

function unitText(r){
  if(r.unitCost!=null)return num(r.unitCost)
  return r.unitMixed?'複数':'<span class="st-pending">原価未確定</span>'
}

const Stocktake={
  async loadList(){
    state.stList=await api('/api/stocktakes')
    if(state.stMonth)state.stCur=await api('/api/stocktakes/get?month='+encodeURIComponent(state.stMonth))
    else state.stCur=null
  },
  page(){
    ensureCss()
    if(state.stCur)return this.sheet()
    const L=state.stList||{months:[],customers:[]}
    $('#main').innerHTML=`<section class="panel">
      <div class="bar"><input id="stMonth" type="month" value="${thisMonth()}"><button type="button" class="primary" id="stOpen">開く</button></div>
      <table><thead><tr><th>年月</th><th>顧客数</th><th>入力</th><th>反映</th><th></th></tr></thead><tbody>
      ${L.months.map(m=>`<tr><td>${esc(m.month)}</td><td>${m.customerCount}</td><td>${m.enteredCount}/${m.rowCount}</td><td>${m.appliedCount}/${m.customerCount}</td><td><button type="button" data-st-month="${esc(m.month)}">開く</button></td></tr>`).join('')||'<tr><td colspan="5">棚卸はまだありません。</td></tr>'}
      </tbody></table></section>`
    const open=async month=>{
      if(!month)return toast('年月を選んでください。')
      try{state.stMonth=month;state.stCur=await api('/api/stocktakes/get?month='+encodeURIComponent(month));this.page()}catch(e){toast(e.message)}
    }
    $('#stOpen').onclick=()=>open($('#stMonth').value)
    document.querySelectorAll('[data-st-month]').forEach(b=>b.onclick=()=>open(b.dataset.stMonth))
  },
  sheet(){
    const s=state.stCur
    const w=stockCanWrite()
    const L=state.stList||{customers:[]}
    const inSheet=new Set(s.blocks.map(b=>b.customerId))
    const blockById=Object.fromEntries(s.blocks.map(b=>[b.id,b]))
    const multi={loc:new Set(s.rows.map(r=>r.locationLabel)).size>1,cond:new Set(s.rows.map(r=>r.condition)).size>1}
    const sizeText=r=>{const x=[];if(multi.loc)x.push(r.locationLabel);if(multi.cond)x.push(r.conditionLabel);return esc(r.size)+(x.length?` <span class="st-pending">${esc(x.join('・'))}</span>`:'')}
    const open=s.blocks.some(b=>!b.applied)
    const t=s.totals
    let prev=null
    $('#main').innerHTML=`<section class="panel">
      <div class="bar">
        <button type="button" id="stBack">一覧へ</button>
        <b>${esc(s.month)} 棚卸</b>
        ${w?`<span class="st-pick"><input id="stFind" type="search" placeholder="顧客を検索"><select id="stCust">${L.customers.map(c=>`<option value="${c.id}" data-k="${esc(c.name+' '+c.kana)}">${esc(c.name)}${inSheet.has(c.id)?'（追加済）':''}</option>`).join('')}</select><button type="button" id="stAdd">追加</button></span>`:''}
        ${w&&open?'<button type="button" id="stSave">保存</button><button type="button" class="primary" id="stApply">実棚で在庫を上書き</button>':''}
      </div>
      <div class="st-chips">${s.blocks.map(b=>`<span class="st-chip">${esc(b.customerName)} <span class="sub">${b.applied?`反映済 ${esc(at(b.appliedAt))} ${esc(b.appliedBy)}`:`入力 ${b.enteredCount}/${b.rowCount}`}</span>${w&&!b.applied?`<button type="button" title="棚卸表から外す" data-st-remove="${b.customerId}">×</button>`:''}</span>`).join('')||'<span class="sub">顧客を選んで「追加」すると、その顧客の在庫が下に追加されます。</span>'}</div>
      <div class="st-wrap"><table class="st-grid"><colgroup>${COLS.map(c=>`<col style="width:${c[1]}px">`).join('')}</colgroup>
      <thead><tr>${COLS.map(c=>`<th class="${c[2]}">${c[0]}</th>`).join('')}</tr></thead><tbody>
      ${s.rows.map(r=>{const b=blockById[r.blockId];const edit=w&&!b.applied;const start=prev!==null&&prev!==r.blockId;prev=r.blockId;return `<tr class="${b.applied?'st-done':''}${start?' st-block-start':''}">
        <td class="n">${esc(r.officeCode)}</td><td>${esc(r.customerName)}</td><td title="${esc(r.productName)}">${esc(r.productName)}</td><td class="n">${esc(r.productCode)}</td>
        <td>${sizeText(r)}</td><td class="n">${unitText(r)}</td><td class="n">${num(r.bookQty)}</td><td class="n">${money(r.bookAmount)}</td>
        <td class="n cI">${edit?`<input class="st-actual" data-row="${r.id}" type="text" inputmode="numeric" value="${r.actualQty??''}">`:num(r.actualQty)}</td>
        <td class="n cJ">${signed(r.diffQty)}</td>
        <td class="n cK">${r.actualQty==null?'':money(r.actualAmount)}</td>
        <td class="n cL">${r.actualQty==null||r.actualAmount==null||r.bookAmount==null?'':signed(r.diffAmount)}</td>
        <td>${edit?`<input class="st-note" data-note="${r.id}" type="text" value="${esc(r.note)}">`:esc(r.note)}</td></tr>`}).join('')||'<tr><td colspan="13">行はまだありません。</td></tr>'}
      </tbody><tfoot><tr><th colspan="6">合計</th><th class="n">${num(t.bookQty)}</th><th class="n">${money(t.bookAmount)}</th><th class="n">${num(t.actualQty)}</th><th></th><th class="n">${t.actualQty==null?'':money(t.actualAmount)}</th><th></th><th></th></tr></tfoot></table></div>
    </section>`
    const back=async()=>{state.stMonth=null;state.stCur=null;await this.loadList();this.page()}
    $('#stBack').onclick=back
    const rowsBody=()=>[...document.querySelectorAll('.st-actual')].map(i=>({id:Number(i.dataset.row),actualQty:i.value.trim(),note:(document.querySelector(`[data-note="${i.dataset.row}"]`)||{}).value||''}))
    const save=async()=>{if(!document.querySelector('.st-actual'))return;state.stCur=await api('/api/stocktakes/save',{month:s.month,rows:rowsBody()})}
    const find=$('#stFind')
    if(find)find.oninput=()=>{const q=find.value.trim();let first=null;[...$('#stCust').options].forEach(o=>{o.hidden=!!q&&!o.dataset.k.includes(q);if(!o.hidden&&!first)first=o});if(first)$('#stCust').value=first.value}
    if($('#stAdd'))$('#stAdd').onclick=async()=>{
      const cid=Number($('#stCust').value)
      if(!cid)return toast('顧客を選んでください。')
      if(inSheet.has(cid))return toast('この顧客は追加済みです。')
      try{await save();const r=await api('/api/stocktakes/add',{month:s.month,customerId:cid});state.stCur=r;toast(r.already?'この顧客は追加済みです。':`${r.added}行を追加しました。`);this.page()}catch(e){toast(e.message)}
    }
    document.querySelectorAll('[data-st-remove]').forEach(b=>b.onclick=async()=>{
      const blk=s.blocks.find(x=>x.customerId===Number(b.dataset.stRemove))
      if(!confirm(`${blk?blk.customerName:''} を棚卸表から外します。入力した実棚も消えます。よろしいですか？`))return
      try{await save();state.stCur=await api('/api/stocktakes/remove',{month:s.month,customerId:Number(b.dataset.stRemove)});this.page()}catch(e){toast(e.message)}
    })
    if($('#stSave'))$('#stSave').onclick=async()=>{try{await save();toast('保存しました。');this.page()}catch(e){toast(e.message)}}
    if($('#stApply'))$('#stApply').onclick=async()=>{
      try{
        await save()
        const cur=state.stCur
        const ready=cur.blocks.filter(b=>!b.applied&&b.enteredCount>0)
        if(!ready.length){toast('実棚が入力された未反映の顧客がありません。');this.page();return}
        const n=cur.rows.filter(r=>!r.applied&&r.actualQty!=null&&r.diffQty!==0).length
        if(!confirm(`実棚点数で在庫を上書きします。\n対象: ${ready.map(b=>b.customerName).join('、')}\n差がある ${n} 行を棚卸調整します（空欄の行は変更しません）。\n反映後、この顧客の行は変更できません。よろしいですか？`)){this.page();return}
        const r=await api('/api/stocktakes/apply',{month:s.month,confirm:true})
        state.stCur=r;toast(`${r.appliedCustomers}顧客・${r.adjusted}行を棚卸調整しました。`);this.page()
      }catch(e){toast(e.message)}
    }
  },
}
global.Stocktake=Stocktake
})(window)
