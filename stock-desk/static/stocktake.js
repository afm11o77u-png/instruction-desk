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
.st-grid tr[draggable]{cursor:grab}.st-grid tr.st-drag{opacity:.4}
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
.st-pick{padding:8px 12px;text-align:left;border-bottom:1px solid var(--line)}
.st-pick-head{display:flex;gap:8px;align-items:center;justify-content:flex-start;margin:0 0 4px}
.st-pick-head input{width:280px;margin:0}
.st-pick-list{max-height:220px;overflow:auto;border:1px solid #ccc;padding:4px 8px;width:fit-content;min-width:280px;text-align:left}
.st-pick-list label{display:block;margin:0;padding:1px 0;white-space:nowrap}
.st-pick-list label.st-has{color:#777}
.st-pick-list label[hidden]{display:none}
.st-mis{color:#c00;font-weight:700;cursor:help}
.st-tabs{display:flex;flex-wrap:wrap;gap:2px;margin:6px 0 0}
.st-tabs button{border:1px solid #9e9e9e;border-bottom:0;background:#f2f2f2;padding:3px 10px;font-size:12.5px;cursor:pointer}
.st-tabs button.on{background:#fff;font-weight:700}
.st-cs{font-family:'ＭＳ Ｐゴシック','MS PGothic',sans-serif}
.st-cs th,.st-cs td{height:30px;border-color:#000}
.st-cs thead th{background:#D9D9D9;font-size:14.5px}
.st-cs thead th.k{font-size:18.5px}
.st-cs td.wrap{white-space:normal;line-height:1.1}
.st-cs td.b{font-weight:700;font-size:16px}.st-cs td.b1{font-weight:700;font-size:14.5px}
.st-cs input{height:26px;font-size:14.5px}`
function ensureCss(){if(!document.getElementById('stocktake-css')){const s=document.createElement('style');s.id='stocktake-css';s.textContent=CSS;document.head.appendChild(s)}}

const COLS=[['コード',48,'hA'],['顧客',196,'hA'],['商品名',196,'hA'],['商品コード',125,'hA'],['サイズ',80,'hA'],['仕入単価',64,'hA'],['在庫数',95,'hG'],['在庫金額',95,'hG'],['実棚点数',95,'cI'],['在庫数との差',98,'cJ'],['実棚金額',95,'cK'],['在庫金額との差',110,'cL'],['備考',112,'hM']]

function unitText(r){
  if(r.unitCost!=null)return num(r.unitCost)
  return r.unitMixed?'複数':'<span class="st-pending">原価未確定</span>'
}

function mis(r){return r.checkMismatch?`<span class="st-mis" title="チェックシートの計: ${r.checkTotal}">※</span>`:''}
const fileB64=f=>new Promise((ok,ng)=>{const r=new FileReader();r.onload=()=>ok(String(r.result).split(',')[1]||'');r.onerror=()=>ng(Error('ファイルを読めませんでした。'));r.readAsDataURL(f)})
function bindExcel(self,s,save){
  $('#stXlsx').onclick=async()=>{
    try{
      await save()
      const r=await fetch(BASE+'/api/stocktakes/checksheet.xlsx?month='+encodeURIComponent(s.month))
      if(!r.ok){let m='Excelを出力できませんでした。';try{m=(await r.json()).error||m}catch(e){}throw Error(m)}
      const a=document.createElement('a');a.href=URL.createObjectURL(await r.blob());a.download=`棚卸チェックシート_${s.month}.xlsx`;a.click()
      setTimeout(()=>URL.revokeObjectURL(a.href),1000)
    }catch(e){toast(e.message)}
  }
  const imp=$('#stImport')
  if(!imp)return
  imp.onclick=()=>{
    const f=document.createElement('input');f.type='file';f.accept='.xlsx'
    f.onchange=async()=>{
      if(!f.files[0])return
      try{
        await save()
        const body={month:s.month,fileBase64:await fileB64(f.files[0])}
        const pv=await api('/api/stocktakes/checksheet/import-preview',body)
        if(pv.errors.length){toast(pv.errors.slice(0,3).join(' / '),8000);return}
        const lines=pv.updates.slice(0,15).map(u=>`${u.sheet} ${u.productName} ${u.size}: ${u.before??'空欄'}→${u.after??'空欄'}`)
        if(pv.updates.length>15)lines.push(`…ほか${pv.updates.length-15}行`)
        const msg=`更新 ${pv.updateCount}行 / 変更なし ${pv.unchanged}行 / 該当なし ${pv.unmatched}行 / 反映済のため除外 ${pv.skippedApplied}行`
        if(!pv.updateCount){toast(msg,6000);return}
        if(!confirm(msg+'\n\n'+lines.join('\n')+'\n\n取り込みますか？'))return
        state.stCur=await api('/api/stocktakes/checksheet/import',{...body,confirm:true})
        toast(`${state.stCur.imported}行を取り込みました。`);self.page()
      }catch(e){toast(e.message)}
    }
    f.click()
  }
}

const Stocktake={
  async loadList(){
    state.stList=await api('/api/stocktakes')
    if(state.stMonth)state.stCur=await api('/api/stocktakes/get?month='+encodeURIComponent(state.stMonth))
    else state.stCur=null
  },
  page(){
    ensureCss()
    if(state.stCur&&state.stView==='check')return this.check()
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
  check(){
    const s=state.stCur
    const w=stockCanWrite()
    if(!s.blocks.some(b=>b.id===state.stTab))state.stTab=(s.blocks[0]||{}).id
    const b=s.blocks.find(x=>x.id===state.stTab)
    const edit=w&&b&&!b.applied
    const rows=b?s.rows.filter(r=>r.blockId===b.id):[]
    const k=b?b.checkCols:5
    const px=w=>Math.floor(w*7+5)
    const W=[px(b?b.supplierWidth:14.125),px(24),px(21.125),px(6.625),px(6.625),...Array(k+1).fill(px(10.625))]
    const sz=r=>esc(r.size)+(r.condition!=='new'?`（${esc(r.conditionLabel)}）`:'')
    $('#main').innerHTML=`<section class="panel">
      <div class="bar"><button type="button" id="stBack">棚卸表へ</button><b>${esc(s.month)} 棚卸チェックシート</b>
        <button type="button" id="stXlsx">Excel出力</button>${w&&s.blocks.some(x=>!x.applied)?'<button type="button" id="stImport">Excel取込</button>':''}
        ${edit?'<button type="button" class="primary" id="stSave">保存</button>':''}${b&&b.applied?'<span class="sub">上書き済</span>':''}</div>
      <div class="st-tabs">${s.blocks.map(x=>`<button type="button" data-tab="${x.id}" class="${x.id===state.stTab?'on':''}">${esc(x.customerName)}</button>`).join('')}</div>
      <div class="st-wrap"><table class="st-grid st-cs"><colgroup>${W.map(x=>`<col style="width:${x}px">`).join('')}<col style="width:24px"></colgroup>
      <thead><tr><th>仕入先</th><th>商品名</th><th>商品コード</th><th>サイズ</th><th>在庫数</th>${'<th></th>'.repeat(k)}<th class="k">計</th><th></th></tr></thead><tbody>
      ${rows.map(r=>`<tr data-row="${r.id}"><td class="b1 wrap">${esc(r.supplierName)}</td><td class="b" title="${esc(r.productName)}">${esc(r.productName)}</td><td class="b">${esc(r.productCode)}</td><td class="b">${sz(r)}</td><td class="b n">${num(r.bookQty)}</td>
        ${r.checks.map((v,i)=>`<td class="n">${edit?`<input class="st-ck" data-i="${i}" type="text" inputmode="numeric" value="${v??''}">`:num(v)}</td>`).join('')}
        <td class="n b st-sum">${num(r.checkTotal)}</td><td>${mis(r)}</td></tr>`).join('')||'<tr><td colspan="12">行はありません。</td></tr>'}
      </tbody></table></div></section>`
    const collect=()=>[...document.querySelectorAll('tr[data-row]')].filter(tr=>tr.querySelector('.st-ck')).map(tr=>({id:Number(tr.dataset.row),checks:[...tr.querySelectorAll('.st-ck')].map(i=>i.value.trim())}))
    const save=async()=>{const rs=collect();if(rs.length)state.stCur=await api('/api/stocktakes/save',{month:s.month,rows:rs})}
    document.querySelectorAll('.st-ck').forEach(i=>i.oninput=()=>{
      const tr=i.closest('tr');const v=[...tr.querySelectorAll('.st-ck')].map(x=>x.value.trim()).filter(x=>x!=='').map(Number)
      tr.querySelector('.st-sum').textContent=v.length&&v.every(Number.isFinite)?num(v.reduce((a,x)=>a+x,0)):''
    })
    $('#stBack').onclick=async()=>{try{await save();state.stView=null;this.page()}catch(e){toast(e.message)}}
    document.querySelectorAll('[data-tab]').forEach(t=>t.onclick=async()=>{try{await save();state.stTab=Number(t.dataset.tab);this.page()}catch(e){toast(e.message)}})
    if($('#stSave'))$('#stSave').onclick=async()=>{try{await save();toast('保存しました。');this.page()}catch(e){toast(e.message)}}
    bindExcel(this,s,save)
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
      <div class="bar"><button type="button" id="stBack">一覧へ</button><b>${esc(s.month)} 棚卸</b></div>
      ${w?`<div class="st-pick"><div class="st-pick-head"><input id="stFind" type="search" placeholder="顧客を検索"><span class="sub" id="stPicked"></span><button type="button" id="stAdd">選んだ顧客を追加</button></div>
        <div class="st-pick-list">${L.customers.map(c=>{const has=inSheet.has(c.id);return `<label class="check${has?' st-has':''}" data-st-name="${esc(c.name+' '+(c.kana||''))}"><input type="checkbox" value="${c.id}"${has?' checked disabled':''}> ${esc(c.name)}${has?' <span class="sub">追加済</span>':''}</label>`}).join('')||'<span class="sub">顧客がありません。</span>'}</div></div>`:''}
      <div class="bar"><button type="button" id="stCheck">チェックシート</button><button type="button" id="stXlsx">Excel出力</button>${w&&open?'<button type="button" id="stImport">Excel取込</button>':''}
        ${w&&open?'<button type="button" id="stSave">保存</button><button type="button" class="primary" id="stApply">実棚で在庫を上書き</button>':''}
      </div>
      <div class="st-chips">${s.blocks.map(b=>`<span class="st-chip">${esc(b.customerName)} <span class="sub">${b.applied?`反映済 ${esc(at(b.appliedAt))} ${esc(b.appliedBy)}`:`入力 ${b.enteredCount}/${b.rowCount}`}</span>${w&&!b.applied?`<button type="button" title="棚卸表から外す" data-st-remove="${b.customerId}">×</button>`:''}</span>`).join('')||'<span class="sub">顧客にチェックを入れて「選んだ顧客を追加」すると、その顧客の在庫が下に追加されます。</span>'}</div>
      <div class="st-wrap"><table class="st-grid"><colgroup>${COLS.map(c=>`<col style="width:${c[1]}px">`).join('')}</colgroup>
      <thead><tr>${COLS.map(c=>`<th class="${c[2]}">${c[0]}</th>`).join('')}</tr></thead><tbody>
      ${s.rows.map(r=>{const b=blockById[r.blockId];const edit=w&&!b.applied;const start=prev!==null&&prev!==r.blockId;prev=r.blockId;return `<tr class="${b.applied?'st-done':''}${start?' st-block-start':''}" data-rid="${r.id}" data-blk="${r.blockId}"${w?' draggable="true"':''}>
        <td class="n">${esc(r.officeCode)}</td><td>${esc(r.customerName)}</td><td title="${esc(r.productName)}">${esc(r.productName)}</td><td class="n">${esc(r.productCode)}</td>
        <td>${sizeText(r)}</td><td class="n">${unitText(r)}</td><td class="n">${num(r.bookQty)}</td><td class="n">${money(r.bookAmount)}</td>
        <td class="n cI">${edit?`<input class="st-actual" data-row="${r.id}" type="text" inputmode="numeric" value="${r.actualQty??''}">`:num(r.actualQty)}${mis(r)}</td>
        <td class="n cJ">${signed(r.diffQty)}</td>
        <td class="n cK">${r.actualQty==null?'':money(r.actualAmount)}</td>
        <td class="n cL">${r.actualQty==null||r.actualAmount==null||r.bookAmount==null?'':signed(r.diffAmount)}</td>
        <td>${edit?`<input class="st-note" data-note="${r.id}" type="text" value="${esc(r.note)}">`:esc(r.note)}</td></tr>`}).join('')||'<tr><td colspan="13">行はまだありません。</td></tr>'}
      </tbody><tfoot><tr><th colspan="6">合計</th><th class="n">${num(t.bookQty)}</th><th class="n">${money(t.bookAmount)}</th><th class="n">${num(t.actualQty)}</th><th></th><th class="n">${t.actualQty==null?'':money(t.actualAmount)}</th><th></th><th></th></tr></tfoot></table></div>
    </section>`
    $('#stCheck').onclick=async()=>{try{await save();state.stView='check';this.page()}catch(e){toast(e.message)}}
    const back=async()=>{state.stMonth=null;state.stCur=null;state.stView=null;await this.loadList();this.page()}
    $('#stBack').onclick=back
    const rowsBody=()=>[...document.querySelectorAll('.st-actual')].map(i=>({id:Number(i.dataset.row),actualQty:i.value.trim(),note:(document.querySelector(`[data-note="${i.dataset.row}"]`)||{}).value||''}))
    const save=async()=>{if(!document.querySelector('.st-actual'))return;state.stCur=await api('/api/stocktakes/save',{month:s.month,rows:rowsBody()})}
    bindExcel(this,s,save)
    if(w)bindDrag(s,save,()=>this.page())
    const picked=()=>[...document.querySelectorAll('.st-pick-list input:checked:not(:disabled)')].map(i=>Number(i.value))
    const showPicked=()=>{const el=$('#stPicked');if(el)el.textContent=`${picked().length}件選択`}
    showPicked()
    document.querySelectorAll('.st-pick-list input').forEach(i=>i.onchange=showPicked)
    const find=$('#stFind')
    if(find)find.oninput=()=>{const q=find.value.trim();document.querySelectorAll('[data-st-name]').forEach(l=>{l.hidden=!!q&&!l.dataset.stName.includes(q)})}
    if($('#stAdd'))$('#stAdd').onclick=async()=>{
      const ids=picked()
      if(!ids.length)return toast('顧客にチェックを入れてください。')
      try{await save();const r=await api('/api/stocktakes/add',{month:s.month,customerIds:ids});state.stCur=r;toast(`${r.addedCustomers}件の顧客（${r.added}行）を追加しました。`+(r.skipped?`追加済みの${r.skipped}件は飛ばしました。`:''));this.page()}catch(e){toast(e.message)}
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
// 行のドラッグ並べ替え（同じ顧客の中だけ）。落とした時点で順番を保存する。
function bindDrag(s,save,rerender){
  const body=document.querySelector('.st-grid tbody');if(!body)return
  let drag=null
  body.addEventListener('dragstart',e=>{const tr=e.target.closest&&e.target.closest('tr[data-rid]');if(!tr)return;const a=document.activeElement;if(a&&a.tagName==='INPUT'&&tr.contains(a)){e.preventDefault();return}drag=tr;tr.classList.add('st-drag');e.dataTransfer.effectAllowed='move';try{e.dataTransfer.setData('text/plain',tr.dataset.rid)}catch(_){}})
  body.addEventListener('dragover',e=>{const tr=e.target.closest('tr[data-rid]');if(!drag||!tr||tr===drag||tr.dataset.blk!==drag.dataset.blk)return;e.preventDefault();const r=tr.getBoundingClientRect();(e.clientY>r.top+r.height/2?tr.after(drag):tr.before(drag))})
  body.addEventListener('dragend',()=>{if(drag)drag.classList.remove('st-drag')})
  body.addEventListener('drop',async e=>{
    if(!drag)return;e.preventDefault()
    const blk=drag.dataset.blk;drag.classList.remove('st-drag');drag=null
    const b=s.blocks.find(x=>String(x.id)===blk);if(!b)return
    const ids=[...body.querySelectorAll(`tr[data-blk="${blk}"]`)].map(t=>Number(t.dataset.rid))
    try{await save();state.stCur=await api('/api/stocktakes/order',{month:s.month,customerId:b.customerId,rowIds:ids});rerender()}catch(err){toast(err.message);rerender()}
  })
}
global.Stocktake=Stocktake
})(window)

