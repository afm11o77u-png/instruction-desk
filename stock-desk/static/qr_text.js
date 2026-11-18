/* Paste on a phone, receive and copy on a PC. Independent of stock jobs. */
(function(){
 let run=0;
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 async function mount(phone){
  if(!window.QrLocal)return;
  const parent=document.querySelector('.qr-read-page');if(!parent)return;
  const token=++run;let saving=Promise.resolve(),timer=0,working=false;
  const box=document.createElement('section');box.className='qr-work qr-text';box.id='qrText';parent.append(box);
  function note(text,error=false){const el=box.querySelector('[data-text-note]');if(el){el.textContent=text;el.setAttribute('role',error?'alert':'status')}}
  function draft(text){saving=saving.catch(()=>{}).then(()=>QrLocal.request('text-draft',{text}));return saving}
  async function refresh(){
   const local=await QrLocal.request('text-state');if(token!==run||!box.isConnected)return;
   box.innerHTML=`<details><summary>${phone?'文章をPCへ送る':'スマホから受け取った文章'}</summary>
    <p>スマホのChatGPTの回答などを貼り付け、事務所のWi-Fiで送信できます。同じ利用者でPCにログインし、コピーしてChatGPTに貼り付けてください。</p>
    <p data-text-note role="status"></p>
    <label>送る文章（50000文字まで）<textarea data-text-draft rows="6" maxlength="50000" placeholder="文章をここに貼り付けてください">${esc(local.draft)}</textarea></label>
    <button type="button" data-text-send>文章を送信</button>
    <h3>この端末の送信記録</h3><div data-text-outbox>${local.items.map((item,i)=>`<details><summary>${item.sent?'送信済み':'未送信'} ／ ${esc(item.createdAt.replace('T',' ').slice(0,19))}</summary><textarea readonly rows="5">${esc(item.text)}</textarea>${item.sent?'':`<button type="button" data-text-retry="${i}">この文章を再送信</button>`}</details>`).join('')||'<p>まだありません。</p>'}</div>
    ${phone?'':'<h3>事務所で受信した文章</h3><button type="button" data-text-refresh>受信一覧を更新</button><div data-text-inbox></div>'}
   </details>`;
   const input=box.querySelector('[data-text-draft]');
   input.oninput=()=>{clearTimeout(timer);const text=input.value;timer=setTimeout(()=>{draft(text).then(()=>note('入力した文章を端末に保存しました。まだ送信していません。')).catch(e=>note(e.message,true))},200)};
   async function operation(fn){if(working)return;working=true;box.querySelectorAll('button,textarea').forEach(el=>el.disabled=true);try{clearTimeout(timer);await draft(input.value);await fn()}catch(e){note(e.message+' 未送信の文章はこの端末に残っています。',true)}finally{working=false;box.querySelectorAll('button,textarea').forEach(el=>el.disabled=false)}}
   box.querySelector('[data-text-send]').onclick=()=>operation(async()=>{
    const saved=await QrLocal.request('text-save',{text:input.value});
    input.value='';await refresh();box.querySelector('details').open=true;note('文章を送信しています…');
    await QrLocal.request('text-send',{id:saved.item.id});await refresh();box.querySelector('details').open=true;note('送信しました。PCのQR読み取りページで「受信一覧を更新」を押してください。');
   });
   box.querySelectorAll('[data-text-retry]').forEach(button=>button.onclick=()=>operation(async()=>{note('再送信しています…');await QrLocal.request('text-send',{id:local.items[Number(button.dataset.textRetry)].id});await refresh();box.querySelector('details').open=true;note('送信しました。')}));
   const update=box.querySelector('[data-text-refresh]');if(update)update.onclick=()=>operation(async()=>{
    note('受信した文章を読み込んでいます…');const received=await QrLocal.request('text-list');const list=box.querySelector('[data-text-inbox]');
    list.innerHTML=received.messages.map((item,i)=>`<article><p>${esc(item.actor_name)} ／ ${esc(item.received_at.replace('T',' '))}</p><textarea readonly rows="7" data-text-body="${i}">${esc(item.body)}</textarea><button type="button" data-text-copy="${i}">全文をコピー</button></article>`).join('')||'<p>受信した文章はありません。</p>';
    list.querySelectorAll('[data-text-copy]').forEach(button=>button.onclick=async()=>{const input=list.querySelector(`[data-text-body="${button.dataset.textCopy}"]`);try{await navigator.clipboard.writeText(input.value);note('全文をコピーしました。PCのChatGPTに貼り付けてください。')}catch{input.focus();input.select();note('本文を選択しました。コピーしてChatGPTに貼り付けてください。')}});
    note(`${received.messages.length}件の文章を受信しています。`);
   });
   if(working)box.querySelectorAll('button,textarea').forEach(el=>el.disabled=true);
  }
  await refresh();
 }
 window.QrText={mount};
})();
