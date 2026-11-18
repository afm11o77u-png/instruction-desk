/* Shared phone/PC collection workflow. Recording never changes stock. */
(function(){
 let data=null,selected=null,mobile=false,busy=false,dirty=false,pending=null,epoch=0;
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const id=()=>crypto.randomUUID();
 const root=()=>document.getElementById('qrWork');
 const current=()=>data?.jobs.find(j=>j.id===selected)||null;
 const active=()=>data?.jobs.find(j=>j.status==='collecting'&&j.actor_id===data.actorId)||null;
 const status=j=>({collecting:'読み取り中',completed:'完了・在庫未反映',confirmed:'在庫反映済み'}[j.status]);
 async function request(action,body={}){return api('/api/qr-work/'+action,body)}
 function message(text,error=false){const box=document.getElementById('qrWorkMessage');if(box){box.textContent=text;box.setAttribute('role',error?'alert':'status');box.classList.toggle('error',error)}}
 function put(job){data.jobs=data.jobs.filter(x=>x.id!==job.id);data.jobs.unshift(job);selected=job.id;dirty=false;}
 function edits(){
  const job=current();if(!job)return null;
  return {id:job.id,version:job.version,direction:root().querySelector('[data-direction]')?.value||job.direction,
   lines:job.lines.map(line=>{const row=[...root().querySelectorAll('[data-line]')].find(r=>r.dataset.line===line.key);
    if(!row)return {...line};
    if(row.dataset.deleted==='1')return {key:line.key,deleted:true};
    const val=name=>row.querySelector(`[data-field="${name}"]`)?.value;
    if(mobile)return {key:line.key,qty:val('qty'),size:line.size,condition:line.condition,locationId:line.locationId,unitCost:line.unitCost};
    return {key:line.key,qty:val('qty'),size:val('size'),condition:val('condition'),locationId:Number(val('locationId'))||null,unitCost:val('unitCost')};})};
 }
 async function save(){if(!dirty)return;put((await request('edit',edits())).job);render();}
 async function action(fn){if(busy)return;busy=true;lock(true);try{await fn()}catch(e){message(e.message,true)}finally{busy=false;lock(false)}}
 function lock(value){if(root())root().querySelectorAll('button,input,select').forEach(el=>{if(value){el.dataset.preDisabled=el.disabled?'1':'0';el.disabled=true}else if(el.dataset.preDisabled!==undefined){el.disabled=el.dataset.preDisabled==='1';delete el.dataset.preDisabled}})}
 async function refresh(){
  if(dirty&&!confirm('未保存の編集を破棄して一覧を更新しますか？'))return;
  data=await request('state');dirty=false;pending=null;
  if(!data.jobs.some(j=>j.id===selected))selected=active()?.id||data.jobs[0]?.id||null;
  render();
 }
 function options(values,value,empty){return `<option value="">${esc(empty)}</option>`+values.map(v=>`<option value="${esc(v.id)}" ${String(v.id)===String(value)?'selected':''}>${esc(v.name)}</option>`).join('')}
 function render(){
  const box=root();if(!box||!data)return;
  const job=current(),editable=job&&job.status!=='confirmed'&&(job.actor_id===data.actorId||(!mobile&&job.status==='completed'&&data.canConfirm));
  const visible=data.jobs.filter(j=>!mobile||j.actor_id===data.actorId);
  box.innerHTML=`<h2>倉庫の読み取り作業</h2>
   <p>最初のQRで作業開始 → 同じQRで数量＋1 → 数量を確認して完了 → PCで在庫に反映</p>
   <p>読み取り・保存・完了だけでは在庫は変わりません。Wi-Fi接続中に利用してください。</p>
   <p>カメラは1回の読み取りで停止します。続ける場合は「次のQRを読む」（PCでは「カメラで読む」）を押してください。同じQRを映し続けて数量が増えることを防ぎます。</p>
   <p id="qrWorkMessage" role="status"></p>
   <div class="qr-work-actions"><button type="button" data-refresh>一覧を更新</button><button type="button" data-active>読み取り中の作業へ</button></div>
   <label>保存された作業<select data-jobs><option value="">新しい作業（最初のQRで開始）</option>${visible.map(j=>`<option value="${esc(j.id)}" ${j.id===selected?'selected':''}>${esc(j.started_at)} ／ ${esc(j.actor_name)} ／ ${status(j)} ／ ${j.lines.reduce((n,l)=>n+l.qty,0)}点</option>`).join('')}</select></label>
   ${job?`<h3>${status(job)}</h3><p>担当：${esc(job.actor_name)}<br>開始：${esc(job.started_at)}${job.completed_at?'<br>完了：'+esc(job.completed_at):''}${job.confirmed_at?'<br>在庫反映：'+esc(job.confirmed_at)+' ／ '+esc(job.confirmed_by):''}</p>
   ${!mobile?`<label>在庫に反映する操作<select data-direction ${editable?'':'disabled'}><option value="outbound" ${job.direction==='outbound'?'selected':''}>出庫（在庫を減らす）</option><option value="inbound" ${job.direction==='inbound'?'selected':''}>入庫（在庫を増やす）</option></select></label>
   <p>数量は＋・－ボタンまたは直接入力で変更できます。0は反映対象外です。サイズ・新品中古・場所が未選択の場合は確認して選んでください。出庫は選んだ場所の在庫から古い入荷順に引き当てます。</p>`:'<p>枚数を確認してください。間違えて読んだ行は削除できます。</p>'}
   <div class="qr-work-lines">${job.lines.map(l=>`<article data-line="${esc(l.key)}"><strong>${esc(l.customerName)} ／ ${esc(l.code)} ${esc(l.productName)}</strong>
    <p>${esc(l.color)} ／ ${esc(l.spec)}</p>
    <p>QR読み取り ${l.scanCount} 回</p>
    <label>${mobile?'枚数':'反映する数量'}<div class="qr-qty"><button type="button" data-minus ${editable?'':'disabled'} aria-label="数量を1減らす">－</button><input data-field="qty" type="number" min="0" max="99999" step="1" value="${l.qty}" ${editable?'':'disabled'}><button type="button" data-plus ${editable?'':'disabled'} aria-label="数量を1増やす">＋</button></div></label>
    ${mobile?`<p>サイズ：${esc(l.size||'未設定')} ／ ${esc(l.condition==='new'?'新品':l.condition==='used'?'中古':'状態未設定')}</p>`:`<label>サイズ<select data-field="size" ${editable?'':'disabled'}>${options(l.sizes.map(s=>({id:s,name:s||'サイズなし'})),l.size,'選択してください')}</select></label>
    <label>新品・中古<select data-field="condition" ${editable?'':'disabled'}>${options([{id:'new',name:'新品'},{id:'used',name:'中古'}],l.condition,'選択してください')}</select></label>
    <label>保管場所<select data-field="locationId" ${editable?'':'disabled'}>${options(data.locations,l.locationId,'選択してください')}</select></label>
    <label class="qr-work-cost" ${job.direction==='inbound'?'':'hidden'}>仕入単価（入庫時に必須・円）<input data-field="unitCost" type="number" min="0" step="1" value="${esc(l.unitCost)}" ${editable?'':'disabled'}></label>`}
    ${editable?'<button type="button" data-delete>この行を削除</button>':''}
   </article>`).join('')}</div>
   <p><strong data-total>合計 ${job.lines.reduce((n,l)=>n+l.qty,0)} 点</strong> ／ ${job.lines.length} 種類</p>
   <div class="qr-work-actions">${editable?'<button type="button" data-save>編集した内容を保存</button>':''}
   ${job.status==='collecting'&&job.actor_id===data.actorId?'<button type="button" class="primary" data-complete>読み取り作業を完了</button>':''}
   ${!mobile&&job.status==='completed'&&data.canConfirm?'<button type="button" class="primary" data-confirm>確認して在庫に反映</button>':''}</div>
   ${job.status==='completed'?'<p>PCの「QR読み取り」でこの作業を選び、内容を確認して在庫に反映してください。</p>':''}`:'<p>まだ作業を開始していません。QRを読み取ると自動で開始します。</p>'}`;
  box.querySelector('[data-refresh]').onclick=()=>action(refresh);
  box.querySelector('[data-active]').onclick=()=>action(async()=>{await save();selected=active()?.id||null;render()});
  box.querySelector('[data-jobs]').onchange=e=>{const value=e.target.value;action(async()=>{await save();selected=value||null;render()})};
  box.querySelectorAll('[data-delete]').forEach(button=>button.onclick=()=>{const row=button.closest('[data-line]');if(!confirm('この行を読み取り一覧から削除しますか？'))return;row.dataset.deleted='1';row.hidden=true;dirty=true;total();});
  const total=()=>{const label=box.querySelector('[data-total]');if(label)label.textContent='合計 '+[...box.querySelectorAll('[data-line]:not([data-deleted="1"]) [data-field="qty"]')].reduce((n,input)=>n+(Number(input.value)||0),0)+' 点'};
  box.querySelectorAll('[data-field], [data-direction]').forEach(input=>input.oninput=()=>{dirty=true;total();if(input.matches('[data-direction]'))box.querySelectorAll('.qr-work-cost').forEach(el=>el.hidden=input.value!=='inbound')});
  box.querySelectorAll('[data-plus],[data-minus]').forEach(button=>button.onclick=()=>{const input=button.closest('[data-line]').querySelector('[data-field="qty"]');input.value=Math.max(0,Math.min(99999,Number(input.value)+(button.hasAttribute('data-plus')?1:-1)));dirty=true;total();});
  const saveBtn=box.querySelector('[data-save]');if(saveBtn)saveBtn.onclick=()=>action(async()=>{await save();message('保存しました。在庫数量は変えていません。')});
  const complete=box.querySelector('[data-complete]');if(complete)complete.onclick=()=>action(async()=>{
   QrRead.stop();await save();put((await request('complete',edits())).job);render();message('作業を完了しました。PC側で在庫への反映を確認できます。');
  });
  const confirmBtn=box.querySelector('[data-confirm]');if(confirmBtn)confirmBtn.onclick=()=>action(async()=>{
   await save();const j=current();if(!confirm(`${j.direction==='inbound'?'入庫':'出庫'}として合計${j.lines.reduce((n,l)=>n+l.qty,0)}点を在庫に反映しますか？`))return;
   put((await request('confirm',{id:j.id,version:j.version})).job);render();message('在庫に反映しました。同じ作業を重複して反映することはありません。');
  });
 }
 async function mount(phone){
  mobile=phone;data=null;selected=null;dirty=false;pending=null;const run=++epoch;
  const parent=document.querySelector('.qr-read-page');if(!parent)return;
  const box=document.createElement('section');box.id='qrWork';box.className='qr-work';box.innerHTML='<h2>倉庫の読み取り作業</h2><p id="qrWorkMessage">保存した作業を読み込み中…</p>';parent.append(box);
  try{const loaded=await request('state');if(epoch!==run||!root())return;data=loaded;selected=active()?.id||null;render()}catch(e){message(e.message+' 一度ページを開き直してください。',true)}
 }
 async function record(raw){
  if(!root()||!data)throw Error('作業一覧を読み込めませんでした。ページを開き直してください。');
  if(busy)throw Error('保存処理中です。終わってからもう一度読んでください。');
  busy=true;lock(true);
  try{
   await save();
   const job=active();
   if(job&&selected!==job.id)throw Error('読み取り中の作業へ戻ってから読んでください。');
   // Keep a failed request token so retrying after a lost response cannot double count.
   if(pending&&pending.qr!==raw)throw Error('前の読み取りの保存結果が未確認です。同じQRを再読込するか一覧を更新してください。');
   if(!pending)pending={qr:raw,token:id(),...(job?{id:job.id,version:job.version}:{})};
   let response;
   try{response=await request('scan',pending)}catch(e){if(e.status&&e.status<500)pending=null;throw e}
   pending=null;put(response.job);render();message('読み取りを保存しました。同じQRは数量を1加算します。');
  }finally{busy=false;lock(false)}
 }
 window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
 window.QrWork={mount,record};
})();
