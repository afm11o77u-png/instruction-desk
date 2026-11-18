/* Shared phone/PC collection workflow. Recording never changes stock. */
(function(){
 let data=null,selected=null,mobile=false,busy=false,dirty=false,pending=null,epoch=0,completing=false,pickingLists=null,pickingCompare=null;
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const id=()=>crypto.randomUUID();
 const idleWaiters=[];
 function release(){busy=false;lock(false);idleWaiters.splice(0).forEach(resolve=>resolve())}
 function waitIdle(){return busy?new Promise(resolve=>idleWaiters.push(resolve)):Promise.resolve()}
 const root=()=>document.getElementById('qrWork');
 const current=()=>data?.jobs.find(j=>j.id===selected)||null;
 const active=()=>data?.jobs.find(j=>j.status==='collecting'&&j.actor_id===data.actorId)||null;
 const status=j=>j._local?({collecting:'端末内・読み取り中',completed:'作業完了・未送信',sent:'送信済み'}[j.status]):({collecting:'従来の読み取り中',completed:'受信済み・在庫未反映',confirmed:'在庫反映済み'}[j.status]);
 async function request(action,body={}){return window.QrLocal?QrLocal.request(action,body):api('/api/qr-work/'+action,body)}
 function message(text,error=false){const box=document.getElementById('qrWorkMessage');if(box){box.textContent=text;box.setAttribute('role',error?'alert':'status');box.classList.toggle('error',error)}}
 function put(job){data.jobs=data.jobs.filter(x=>x.id!==job.id);data.jobs.unshift(job);selected=job.id;dirty=false;}
 function edits(){
  const job=current();if(!job)return null;
  return {id:job.id,version:job.version,_remote:!job._local,direction:root().querySelector('[data-direction]')?.value||job.direction,
   lines:job.lines.map(line=>{const row=[...root().querySelectorAll('[data-line]')].find(r=>r.dataset.line===line.key);
    if(!row)return {...line};
    if(row.dataset.deleted==='1')return {key:line.key,deleted:true};
    const val=name=>row.querySelector(`[data-field="${name}"]`)?.value;
    if(mobile||job._local)return {key:line.key,qty:val('qty'),size:line.size,condition:line.condition,locationId:line.locationId,unitCost:line.unitCost};
    return {key:line.key,qty:val('qty'),size:val('size'),condition:val('condition'),locationId:Number(val('locationId'))||null,unitCost:val('unitCost')};})};
 }
 async function save(){if(!dirty)return;put((await request('edit',edits())).job);render();}
 async function action(fn){if(busy)return;busy=true;lock(true);try{await fn()}catch(e){message(e.message,true)}finally{release()}}
 function lock(value){if(root())root().querySelectorAll('button,input,select').forEach(el=>{if(el.hasAttribute('data-complete')){el.disabled=completing;return}if(value){el.dataset.preDisabled=el.disabled?'1':'0';el.disabled=true}else if(el.dataset.preDisabled!==undefined){el.disabled=el.dataset.preDisabled==='1';delete el.dataset.preDisabled}})}
 async function refresh(){
  if(dirty&&!confirm('未保存の編集を破棄して一覧を更新しますか？'))return;
  data=await request('state');dirty=false;pending=null;
  if(!data.jobs.some(j=>j.id===selected))selected=active()?.id||data.jobs[0]?.id||null;
  render();
 }
 function options(values,value,empty){return `<option value="">${esc(empty)}</option>`+values.map(v=>`<option value="${esc(v.id)}" ${String(v.id)===String(value)?'selected':''}>${esc(v.name)}</option>`).join('')}
 function render(){
  const box=root();if(!box||!data)return;
  const job=current(),collector=mobile||job?._local,editable=job&&(job._local?job.status==='collecting':(job.status==='collecting'?job.actor_id===data.actorId:!mobile&&job.status==='completed'&&data.canConfirm));
  const visible=data.jobs.filter(j=>!mobile||j.actor_id===data.actorId);
  box.classList.toggle('qr-work-mobile',mobile);
  const locName=id=>{const hit=(data.locations||[]).find(x=>String(x.id)===String(id));return hit?hit.name:''};
  const condName=c=>c==='new'?'新品':c==='used'?'中古':'未選択';
  const pcRowHtml=l=>{
   const place=locName(l.locationId);
   const missing=[];
   if(!l.unresolved){
    if(!l.size)missing.push('サイズ未選択');
    if(!l.condition)missing.push('状態未選択');
    if(!place)missing.push('場所未選択');
   }
   return `<article class="qr-work-pc-row" data-line="${esc(l.key)}" data-unresolved="${l.unresolved?1:0}">
    <div class="qr-work-pc-summary">
     <div class="qr-work-pc-title">
      <strong>${l.unresolved?'商品未照合・集計対象外':esc(l.customerName)+' ／ '+esc(l.code)+' '+esc(l.productName)}</strong>
      <span class="qr-work-pc-meta" data-meta>${esc(l.size||'サイズ未設定')} ／ ${esc(condName(l.condition))} ／ ${esc(place||'場所未設定')}</span>
      ${missing.length?`<span class="qr-work-pc-warn" data-warn>${esc(missing.join('・'))}</span>`:'<span class="qr-work-pc-warn" data-warn hidden></span>'}
     </div>
     <div class="qr-work-pc-ctrl">
      <div class="qr-qty"><button type="button" data-minus ${editable&&!l.unresolved?'':'disabled'} aria-label="数量を1減らす">－</button><input data-field="qty" aria-label="反映する数量" type="number" min="0" max="99999" step="1" value="${l.qty}" ${editable&&!l.unresolved?'':'disabled'}><button type="button" data-plus ${editable&&!l.unresolved?'':'disabled'} aria-label="数量を1増やす">＋</button></div>
      <button type="button" class="qr-detail-btn" data-toggle-detail aria-expanded="false">詳細</button>
      ${editable&&collector?'<button type="button" data-delete>削除</button>':''}
     </div>
    </div>
    <div class="qr-work-detail" hidden>
     <p>${esc(l.color)} ／ ${esc(l.spec)} ／ QR読み取り ${l.scanCount} 回</p>
     ${collector?`<p>サイズ：${esc(l.size||'未設定')} ／ ${esc(condName(l.condition))}</p>`:`<label>サイズ<select data-field="size" ${editable&&!l.unresolved?'':'disabled'}>${options(l.sizes.map(s=>({id:s,name:s||'サイズなし'})),l.size,'選択してください')}</select></label>
     <label>新品・中古<select data-field="condition" ${editable&&!l.unresolved?'':'disabled'}>${options([{id:'new',name:'新品'},{id:'used',name:'中古'}],l.condition,'選択してください')}</select></label>
     <label>保管場所<select data-field="locationId" ${editable&&!l.unresolved?'':'disabled'}>${options(data.locations,l.locationId,'選択してください')}</select></label>
     <label class="qr-work-cost" ${job.direction==='inbound'?'':'hidden'}>仕入単価（入庫時に必須・円）<input data-field="unitCost" type="number" min="0" step="1" value="${esc(l.unitCost)}" ${editable&&!l.unresolved?'':'disabled'}></label>`}
    </div>
   </article>`;
  };
  const cRes=job&&pickingCompare&&pickingCompare.jobId===job.id?pickingCompare.data:null;
  const orderedPcLines=()=>{
   if(!job)return [];
   if(!cRes||!cRes.items)return job.lines;
   const byKey=new Map(job.lines.map(l=>[l.key,l]));
   const seen=new Set();const out=[];
   cRes.items.forEach(it=>(it.lineKeys||[]).forEach(k=>{if(byKey.has(k)&&!seen.has(k)){seen.add(k);out.push(byKey.get(k))}}));
   job.lines.forEach(l=>{if(!seen.has(l.key))out.push(l)});
   return out;
  };
  const cmpHtml=()=>{
   if(mobile||collector||!job)return '';
   const opts=(pickingLists||[]).map(p=>`<option value="${esc(p.id)}" ${pickingCompare&&Number(pickingCompare.pickingListId)===Number(p.id)?'selected':''}>${esc(p.title)}（${esc(p.date)}・${esc(p.status)}・${p.rowCount}行）</option>`).join('');
   const cRows=cRes?(cRes.items||[]).map(it=>{
    const prevNote=it.prevQty>0?`<div style="font-size:11px;color:#595959">(前回${it.prevQty}+今回${it.currentQty})</div>`:'';
    return `<tr class="qr-work-cmp-${esc(it.status)}"><td>${esc(it.customer)}</td><td>${esc(it.sku)} ${esc(it.name)}</td><td>${esc(it.size)}</td><td>${esc(it.conditionLabel)}</td><td>${esc(it.stockOriginal||'—')}</td><td style="text-align:right">${it.expectedQty}</td><td style="text-align:right">${it.scannedQty}${prevNote}</td><td>${esc(it.statusLabel)}</td></tr>`;
   }).join(''):'';
   return `<section class="qr-work-compare">
    <h4 style="margin:0 0 8px 0">指示書デスクのピッキングリストと照合</h4>
    <div class="qr-work-compare-bar">
     <select id="qrPickingListSelect"><option value="">ピッキングリストを選択してください</option>${opts}</select>
     <button type="button" id="qrPickingCompareBtn">照合する</button>
     <button type="button" id="qrPickingCompleteBtn" title="照合が完了したリストを一覧から非表示にします">このリストを完了にする</button>
    </div>
    ${cRes?`<p style="margin:10px 0 6px 0"><strong>${esc(cRes.title)}</strong>：揃っている <strong>${cRes.summary.ok}</strong>件 ／ 不足 <strong style="color:#cf1322">${cRes.summary.short}</strong>件 ／ 多い <strong style="color:#d46b08">${cRes.summary.over}</strong>件 ／ リスト外 <strong>${cRes.summary.extra}</strong>件</p>
    <div class="tablewrap"><table class="data" style="width:100%;font-size:13px"><thead><tr><th>顧客</th><th>品番・商品名</th><th>サイズ</th><th>状態</th><th>区分</th><th style="text-align:right">予定数</th><th style="text-align:right">読取数</th><th>判定</th></tr></thead><tbody>${cRows||'<tr><td colspan="8">対象行がありません</td></tr>'}</tbody></table></div>`:''}
   </section>`;
  };
  box.innerHTML=`<h2>${mobile?'読み取った制服':'倉庫の読み取り作業'}</h2>
   ${mobile?'<details class="qr-work-help"><summary>操作方法</summary>':''}
   <p>読み取り・枚数編集 → 端末内で作業完了 → 事務所Wi-Fiで送信 → PC担当者が在庫に反映</p>
   <p>読み取りと作業完了は、この端末に保存します。事務所へ送信するまでPCの受信一覧には出ません。ブラウザのデータを消去すると未送信の作業も消えるため、送信が済むまで消去しないでください。</p>
   <p>${mobile?'カメラONで連続読み取りできます。同じQRは一度画面から外して、再び映すと1枚追加します。':'カメラは1回ごとに停止します。続ける場合は「カメラで読む」を押してください。'}</p>
   ${mobile?'</details>':''}
   <p id="qrWorkMessage" role="status"></p>
   <p class="notice" role="note"><strong>新しいQRを追加したときは、倉庫へ行く前に、事務所のWi-Fiで「倉庫用の準備」を押して商品情報を更新してください。</strong> 在庫数量が増減しただけなら更新は不要です。</p>
   <details class="qr-work-help"><summary>倉庫へ行く前の準備</summary><p>${data.preparedAt?'商品情報の準備日時：'+esc(data.preparedAt):'未準備：事務所のWi-Fiで下のボタンを押してください。'}</p><p>端末の商品情報：${data.catalogOk?esc(data.catalogCount)+'件・保存確認済み':'未準備または保存情報が不完全'}</p><button type="button" data-prepare>倉庫用の準備（事務所Wi-Fi）</button><p>同じ接続先・同じブラウザで開いてください。初回は準備後に一度ページを開き直し、通信を切って動作を確認してください。</p></details>
   ${mobile?'<details class="qr-work-help"><summary>保存した作業・一覧更新</summary>':''}
   <div class="qr-work-actions"><button type="button" data-refresh>一覧を更新</button><button type="button" data-active>読み取り中の作業へ</button></div>
   <label>${mobile?'この端末に保存した作業':'端末内の作業・事務所で受信した作業'}<select data-jobs><option value="">新しい作業（最初のQRで開始）</option>${visible.map(j=>`<option value="${esc(j.id)}" ${j.id===selected?'selected':''}>${esc(j.started_at)} ／ ${esc(j.actor_name)} ／ ${status(j)} ／ ${j.lines.reduce((n,l)=>n+(l.unresolved?0:l.qty),0)}点</option>`).join('')}</select></label>
   ${mobile?'</details>':''}
   ${job?`<h3>${status(job)}</h3>${mobile?'<details class="qr-work-help"><summary>作業日時・担当</summary>':''}<p>担当：${esc(job.actor_name)}<br>開始：${esc(job.started_at)}${job.completed_at?'<br>完了：'+esc(job.completed_at):''}${job.confirmed_at?'<br>在庫反映：'+esc(job.confirmed_at)+' ／ '+esc(job.confirmed_by):''}</p>${mobile?'</details>':''}
   ${!collector?`<label>在庫に反映する操作<select data-direction ${editable?'':'disabled'}><option value="outbound" ${job.direction==='outbound'?'selected':''}>出庫（在庫を減らす）</option><option value="inbound" ${job.direction==='inbound'?'selected':''}>入庫（在庫を増やす）</option></select></label>
   <p>数量は＋・－ボタンまたは直接入力で変更できます。0は反映対象外です。サイズ・新品中古・場所が未選択の場合は確認して選んでください。出庫は選んだ場所の在庫から古い入荷順に引き当てます。</p>`:'<p>枚数を確認してください。間違えて読んだ行は削除できます。</p>'}
   <div class="${!mobile&&!collector?'qr-work-split':''}">
    <div class="qr-work-lines">${(mobile?job.lines:orderedPcLines()).map(l=>mobile?`<article class="qr-work-row" data-line="${esc(l.key)}" data-unresolved="${l.unresolved?1:0}"><span class="qr-row-name" title="${esc(l.customerName)} ／ ${esc(l.code)} ${esc(l.productName)} ／ ${esc(l.color)} ／ ${esc(l.spec)}">${l.unresolved?'商品未照合・集計対象外':esc(l.code)+' '+esc(l.productName)} <small>${esc(l.size||'未設定')}・${esc(l.condition==='new'?'新品':l.condition==='used'?'中古':'状態未設定')}</small></span><div class="qr-qty"><button type="button" data-minus ${editable&&!l.unresolved?'':'disabled'} aria-label="枚数を1減らす">－</button><input data-field="qty" aria-label="枚数" type="number" min="0" max="99999" step="1" value="${l.qty}" ${editable&&!l.unresolved?'':'disabled'}><button type="button" data-plus ${editable&&!l.unresolved?'':'disabled'} aria-label="枚数を1増やす">＋</button></div>${editable?'<button type="button" data-delete aria-label="この行を削除">削除</button>':''}</article>`:pcRowHtml(l)).join('')}</div>
    ${cmpHtml()}
   </div>
   ${job.lines.some(l=>l.unresolved)?'<p role="alert">未照合の行は集計対象外です。準備を更新して照合するか、その行を削除してください。</p>':''}
   <p><strong data-total>合計 ${job.lines.reduce((n,l)=>n+(l.unresolved?0:l.qty),0)} 点</strong> ／ ${job.lines.filter(l=>!l.unresolved).length} 種類</p>
   <div class="qr-work-actions">
   ${job.status==='collecting'&&job.actor_id===data.actorId?`<button type="button" class="primary" data-complete ${completing?'disabled':''}>${completing?'保存を待って完了しています…':'作業を完了（端末に保存）'}</button>`:''}
   ${job._local&&job.status==='completed'?'<button type="button" class="primary" data-submit>事務所へ送信</button>':''}
   ${job._local&&job.status==='completed'&&!job.submitAttempted?'<button type="button" data-resume>この作業を再開</button>':''}
   ${!job._local&&!mobile&&job.status==='completed'&&data.canConfirm?'<button type="button" class="primary" data-confirm>確認して在庫に反映</button>':''}</div>
   ${job._local&&job.status==='completed'?'<p>この作業は端末内に保存済み・未送信です。事務所のWi-Fiにつないで「事務所へ送信」を押してください。</p>':job.status==='completed'?'<p>受信した作業です。確認・編集後に「在庫に反映」を押すと、編集内容も保存して反映します。</p>':''}`:'<p>まだ作業を開始していません。QRを読み取ると自動で開始します。</p>'}`;
  box.querySelector('[data-prepare]').onclick=()=>action(async()=>{
   const button=box.querySelector('[data-prepare]'),started=Date.now();
   let stage='倉庫用の準備を開始しています…';
   const paint=()=>{message(stage+'\n経過 '+Math.floor((Date.now()-started)/1000)+'秒。画面を閉じずにお待ちください。');box.querySelector('#qrWorkMessage')?.classList.add('is-loading');};
   const progress=e=>{stage=e.detail;paint()};
   button.textContent='準備中…';box.setAttribute('aria-busy','true');
   window.addEventListener('qr-prepare-progress',progress);paint();const timer=setInterval(paint,1000);
   try{const result=await request('prepare');stage='準備した情報を確認しています…';paint();data=await request('state');render();message(result.message)}
   finally{clearInterval(timer);window.removeEventListener('qr-prepare-progress',progress);box.setAttribute('aria-busy','false');box.querySelector('#qrWorkMessage')?.classList.remove('is-loading');const currentButton=box.querySelector('[data-prepare]');if(currentButton)currentButton.textContent='倉庫用の準備（事務所Wi-Fi）';}
  });
  const resume=box.querySelector('[data-resume]');if(resume)resume.onclick=()=>action(async()=>{QrRead.stop();await QrRead.waitForRead();put((await request('resume',{id:job.id,version:job.version})).job);render();message('作業を再開しました。追加の読み取り・枚数編集・行の削除ができます。最後にもう一度作業を完了してください。');});
  const submit=box.querySelector('[data-submit]');if(submit)submit.onclick=()=>action(async()=>{message('事務所に送信しています。受信を確認するまで閉じないでください。');try{put((await request('submit',{id:job.id})).job);render();message('送信済みです。PCの受信一覧で確認できます。');}catch(e){data=await request('state');render();throw e}});
  box.querySelector('[data-refresh]').onclick=()=>action(refresh);
  box.querySelector('[data-active]').onclick=()=>action(async()=>{await save();selected=active()?.id||null;render()});
  box.querySelector('[data-jobs]').onchange=e=>{const value=e.target.value;action(async()=>{await save();selected=value||null;render()})};
  box.querySelectorAll('[data-delete]').forEach(button=>button.onclick=()=>{const row=button.closest('[data-line]');if(!confirm('この行を読み取り一覧から削除しますか？'))return;row.dataset.deleted='1';row.hidden=true;dirty=true;total();});
  const syncRowMeta=row=>{
   if(!row||row.dataset.unresolved==='1')return;
   const val=name=>row.querySelector(`[data-field="${name}"]`)?.value||'';
   const sz=val('size'),cond=val('condition'),locId=val('locationId'),place=locName(locId);
   const meta=row.querySelector('[data-meta]');
   if(meta)meta.textContent=`${sz||'サイズ未設定'} ／ ${condName(cond)} ／ ${place||'場所未設定'}`;
   const warn=row.querySelector('[data-warn]');
   if(warn){
    const miss=[];if(!sz)miss.push('サイズ未選択');if(!cond)miss.push('状態未選択');if(!place)miss.push('場所未選択');
    warn.textContent=miss.join('・');warn.hidden=!miss.length;
   }
  };
  box.querySelectorAll('[data-toggle-detail]').forEach(button=>button.onclick=()=>{
   const row=button.closest('[data-line]');const det=row&&row.querySelector('.qr-work-detail');if(!det)return;
   const open=det.hidden;det.hidden=!open;button.setAttribute('aria-expanded',open?'true':'false');button.textContent=open?'閉じる':'詳細';
  });
  const cmpBtn=box.querySelector('#qrPickingCompareBtn');
  if(cmpBtn)cmpBtn.onclick=()=>action(async()=>{
   const sel=box.querySelector('#qrPickingListSelect');const plId=Number(sel?.value||0);
   if(!plId){message('照合するピッキングリストを選んでください。',true);return}
   const cur=current();if(!cur)return;
   const editedLines=cur.lines.map(l=>{
    const row=[...box.querySelectorAll('[data-line]')].find(r=>r.dataset.line===l.key);
    if(!row||row.dataset.deleted==='1')return {...l,deleted:row?.dataset.deleted==='1'};
    const val=n=>row.querySelector(`[data-field="${n}"]`)?.value;
    return {...l,qty:val('qty')??l.qty,size:val('size')??l.size,condition:val('condition')??l.condition};
   });
   const res=await api('/api/qr-work/picking-compare',{id:cur.id,pickingListId:plId,lines:editedLines});
   pickingCompare={jobId:cur.id,pickingListId:plId,data:res};render();
  });
  const cmpDoneBtn=box.querySelector('#qrPickingCompleteBtn');
  if(cmpDoneBtn)cmpDoneBtn.onclick=()=>action(async()=>{
   const sel=box.querySelector('#qrPickingListSelect');const plId=Number(sel?.value||0);
   if(!plId){message('完了にするピッキングリストを選んでください。',true);return}
   if(!confirm('このピッキングリストを照合完了にして一覧から非表示にしますか？'))return;
   await api('/api/qr-work/picking-reconcile',{pickingListId:plId});
   pickingLists=(pickingLists||[]).filter(p=>Number(p.id)!==plId);
   if(pickingCompare&&Number(pickingCompare.pickingListId)===plId)pickingCompare=null;
   render();message('ピッキングリストを完了にしました。');
  });
  const total=()=>{const label=box.querySelector('[data-total]');if(label)label.textContent='合計 '+[...box.querySelectorAll('[data-line]:not([data-deleted="1"]):not([data-unresolved="1"]) [data-field="qty"]')].reduce((n,input)=>n+(Number(input.value)||0),0)+' 点'};
  box.querySelectorAll('[data-field], [data-direction]').forEach(input=>input.oninput=()=>{dirty=true;total();syncRowMeta(input.closest('[data-line]'));if(input.matches('[data-direction]'))box.querySelectorAll('.qr-work-cost').forEach(el=>el.hidden=input.value!=='inbound')});
  box.querySelectorAll('[data-plus],[data-minus]').forEach(button=>button.onclick=()=>{const input=button.closest('[data-line]').querySelector('[data-field="qty"]');input.value=Math.max(0,Math.min(99999,Number(input.value)+(button.hasAttribute('data-plus')?1:-1)));dirty=true;total();});
  const complete=box.querySelector('[data-complete]');if(complete)complete.onclick=async()=>{
   if(completing)return;
   completing=true;complete.disabled=true;QrRead.stop();message('読み取りの保存を待ってから作業を完了します。');
   try{
    const saved=await QrRead.waitForRead();
    await waitIdle();
    if(saved===false||pending)throw Error('最後の読み取りを保存できませんでした。読み取り結果を確認し、再読み取りしてから完了してください。');
    await action(async()=>{await save();put((await request('complete',edits())).job);render();message('端末に保存して作業を完了しました。事務所のWi-Fiにつないで、別の「事務所へ送信」を押してください。');});
   }catch(e){message(e.message,true)}finally{completing=false;const button=box.querySelector('[data-complete]');if(button){button.disabled=false;button.textContent='作業を完了（端末に保存）';}}
  };
  const confirmBtn=box.querySelector('[data-confirm]');if(confirmBtn)confirmBtn.onclick=()=>action(async()=>{
   await save();const j=current();if(!confirm(`${j.direction==='inbound'?'入庫':'出庫'}として合計${j.lines.reduce((n,l)=>n+(l.unresolved?0:l.qty),0)}点を在庫に反映しますか？`))return;
   put((await request('confirm',{id:j.id,version:j.version,_remote:!j._local})).job);render();message('在庫に反映しました。同じ作業を重複して反映することはありません。');
  });
 }
 async function mount(phone){
  mobile=phone;data=null;selected=null;dirty=false;pending=null;const run=++epoch;
  const parent=document.querySelector('.qr-read-page');if(!parent)return;
  const box=document.createElement('section');box.id='qrWork';box.className='qr-work';box.innerHTML='<h2>倉庫の読み取り作業</h2><p id="qrWorkMessage">保存した作業を読み込み中…</p>';if(mobile)parent.insertBefore(box,parent.querySelector('details'));else parent.append(box);
  try{if(window.QrLocal)await QrLocal.init(null,phone);const loaded=await request('state');if(epoch!==run||!root())return;data=loaded;selected=active()?.id||null;if(!phone){try{pickingLists=(await api('/api/qr-work/picking-lists',{})).lists||[]}catch(_){pickingLists=[]}}render();if(window.QrText)await QrText.mount(phone)}catch(e){message(e.message+' 一度ページを開き直してください。',true)}
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
   pending=null;put(response.job);render();message('この端末に保存しました。同じQRは枚数を1加算します（未送信）。');
  }finally{release()}
 }
 window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
 window.QrWork={mount,record};
})();
