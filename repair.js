'use strict';
const repairDialog=document.createElement('dialog');repairDialog.id='repairdesk';document.body.append(repairDialog);
let repairData={templates:[],users:[]},repairCurrent=null,repairDirty=false,repairBusy=false;
const repairFields=['customer','code','department','employee','phonetic','sex','management','employeeNumber','owner','received','request','contract','employmentDate','nameRequired','nameColor','barcodeColor','repair','finishDate','markShipDate','delivery','packing','method','methodNote','car','advance','advanceCondition','enclosures','notes'];
const repairItems=[['name','商品名'],['maker','メーカー'],['sku','品番'],['size','サイズ'],['condition','新古'],['quantity','数量'],['stock','在庫場所'],['note','備考'],['note2','備考②']];
function resetRepair(){repairDialog.close();repairDialog.replaceChildren();repairData={templates:[],users:[]};repairCurrent=null;repairDirty=false}
function repairMessage(s){repairDialog.querySelector('#repair-message').textContent=s}
function repairOwner(id){return repairData.users.find(u=>u.id===id)?.name||'利用者不明'}
async function repairRun(fn){if(repairBusy)return;repairBusy=true;repairDialog.querySelectorAll('button').forEach(b=>b.disabled=true);try{repairMessage('');await fn()}catch(e){if(repairDialog.open)repairMessage(e.message)}finally{repairBusy=false;repairDialog.querySelectorAll('button').forEach(b=>b.disabled=false)}}
async function openTemplateRepair(){
 if(currentUser?.role!=='owner')return;
 if(dirty){toast('開いている指示書の変更を保存してから管理・修正を開いてください。');return}
 const uid=currentUser.id;const data=await api('/api/templates/admin');if(currentUser?.id!==uid||currentUser.role!=='owner')return;
 repairData=data;repairCurrent=null;repairDirty=false;
 repairDialog.innerHTML=`<div class="dialoghead"><div><h2>ひな形を修正</h2><p>主管理者専用 ／ 全員のひな形を確認・修正できます。</p></div><button type="button" id="repair-close">閉じる</button></div><div class="repairbody"><div class="repairtools"><button type="button" id="repair-home">管理・修正に戻る</button><button type="button" id="repair-orders">指示書の修正へ</button><button type="button" id="repair-backup">バックアップを作成</button><button type="button" id="repair-refresh">一覧を更新</button></div><p id="repair-message" role="status"></p><section id="repair-library"><h3>全員のひな形</h3><p>ひな形を直しても、投函済みの指示書は変更されません。</p><div class="repairfilters"><label>所有者<select id="repair-owner-filter"><option value="">全員</option>${data.users.map(u=>`<option value="${u.id}">${esc(u.name)}${u.active?'':'（停止中）'}</option>`).join('')}</select></label><label>検索<input id="repair-search" placeholder="フォルダ・保存名・顧客名"></label></div><div id="repair-list"></div></section><section id="repair-editor" hidden></section></div>`;
 repairDialog.querySelector('#repair-close').onclick=closeRepair;
 repairDialog.querySelector('#repair-home').onclick=()=>{if(closeRepair())openRepair()};
 repairDialog.oncancel=e=>{e.preventDefault();closeRepair()};
 repairDialog.querySelector('#repair-refresh').onclick=()=>repairRun(async()=>{if(repairDirty&&!confirm('未保存の修正を破棄して一覧を更新しますか？'))return;repairData=await api('/api/templates/admin');repairDirty=false;repairCurrent=null;showRepairLibrary()});
 repairDialog.querySelector('#repair-backup').onclick=()=>repairRun(async()=>{const r=await api('/api/backup',{});repairMessage('バックアップを作成しました：'+r.filename)});
 repairDialog.querySelector('#repair-orders').onclick=()=>{if(!closeRepair())return;document.querySelector('.nav[data-view="all"]').click();toast('修正する指示書を検索して「開く」を押してください。保存すると変更履歴が残ります。')};
 repairDialog.querySelector('#repair-search').oninput=drawRepairList;repairDialog.querySelector('#repair-owner-filter').onchange=drawRepairList;
 drawRepairList();repairDialog.showModal();
}
function closeRepair(){if(repairBusy)return false;if(repairDirty&&!confirm('未保存の修正を破棄して閉じますか？'))return false;resetRepair();return true}
function showRepairLibrary(){repairDialog.querySelector('#repair-library').hidden=false;repairDialog.querySelector('#repair-editor').hidden=true;drawRepairList()}
function drawRepairList(){
 const q=repairDialog.querySelector('#repair-search').value.trim().toLowerCase(),owner=repairDialog.querySelector('#repair-owner-filter').value;
 const list=repairData.templates.filter(t=>(!owner||t.user_id===+owner)&&[t.name,t.folder,t.payload.customer].join(' ').toLowerCase().includes(q));
 repairDialog.querySelector('#repair-list').innerHTML=`<p>${list.length}件</p><table><thead><tr><th>所有者</th><th>フォルダ</th><th>保存名</th><th>顧客名</th><th></th></tr></thead><tbody>${list.map(t=>`<tr><td>${esc(repairOwner(t.user_id))}</td><td>${esc(t.folder)||'未分類'}</td><td>${esc(t.name)}</td><td>${esc(t.payload.customer)}</td><td><button type="button" data-repair-open="${t.id}">確認・修正</button></td></tr>`).join('')}</tbody></table>`;
 repairDialog.querySelectorAll('[data-repair-open]').forEach(b=>b.onclick=()=>editRepair(repairData.templates.find(t=>t.id===+b.dataset.repairOpen)));
}
function repairControl(key,value,item=false){
 value=value??'';let options=null;
 if(item&&key==='stock')options=['','石狩倉庫','発寒支店','管理担当持込'];
 else if(item&&key==='condition')options=['','新品','中古','未指定'];
 else if(!item){const source=document.querySelector(`#form [name="${key}"]`);if(source?.tagName==='SELECT')options=[...source.options].map(o=>o.value)}
 const attr=item?`data-repair-item="${key}"`:`data-repair-field="${key}"`;
 if(options){if(!options.includes(String(value)))options.push(String(value));return `<select ${attr}>${options.map(v=>`<option value="${esc(v)}" ${String(value)===v?'selected':''}>${esc(v)||'未設定'}</option>`).join('')}</select>`}
 if(['note','note2','notes','repair'].includes(key))return `<textarea ${attr} rows="2" maxlength="${key==='notes'?4000:key==='repair'?300:1000}">${esc(value)}</textarea>`;
 const type=['received','delivery','employmentDate','finishDate','markShipDate'].includes(key)?'date':key==='quantity'?'number':'text';
 return `<input ${attr} type="${type}" ${type==='number'?'min="1" max="99999" step="1"':'maxlength="300"'} value="${esc(value)}">`;
}
function addRepairItem(x={}){
 const row=document.createElement('div');row.className='repairitem';row.innerHTML=repairItems.map(([k,label])=>`<label>${label}${repairControl(k,x[k],true)}</label>`).join('')+'<button type="button" class="repair-remove">この明細を外す</button>';
 row.querySelector('button').onclick=()=>{row.remove();repairDirty=true};repairDialog.querySelector('#repair-items').append(row);
}
function editRepair(t){
 repairCurrent=structuredClone(t);repairDirty=false;
 repairDialog.querySelector('#repair-library').hidden=true;const panel=repairDialog.querySelector('#repair-editor');panel.hidden=false;
 panel.innerHTML=`<button type="button" id="repair-back">← 一覧へ戻る</button><h3>ひな形を修正：${esc(t.name)}</h3><form id="repair-form"><div class="repairgrid"><label>所有者・引継ぎ先<select id="repair-target">${repairData.users.filter(u=>u.id===t.user_id||(u.active&&['owner','sales'].includes(u.role))).map(u=>`<option value="${u.id}" ${u.id===t.user_id?'selected':''}>${esc(u.name)}${u.active?'':'（停止中）'}</option>`).join('')}</select></label><label>フォルダ<input id="repair-folder" maxlength="80" value="${esc(t.folder)}"></label><label>保存名<input id="repair-name" maxlength="80" required value="${esc(t.name)}"></label></div><h4>入力内容</h4><div class="repairgrid">${repairFields.map(k=>`<label>${esc(fieldNames[k]||k)}${repairControl(k,t.payload[k])}</label>`).join('')}</div><h4>商品明細</h4><div id="repair-items"></div><button type="button" id="repair-add">＋ 明細を追加</button><label class="repairreason">修正理由（必須）<textarea id="repair-reason" maxlength="500" required placeholder="例：担当変更のため引き継ぎ／商品サイズの訂正"></textarea></label><p>変更前の内容は修正履歴に保存します。「所有者」を変更すると、ひな形は引継ぎ先の方に表示されます。</p><button class="primary" type="submit">修正内容を保存</button></form><h4>管理・修正の履歴</h4><p>この機能で保存した変更が対象です。「修正前へ戻す」は、選んだ変更の直前の内容・所有者に戻します。</p><button type="button" id="repair-history-load">履歴を表示</button><div id="repair-history"></div>`;
 (t.payload.items||[]).forEach(addRepairItem);
 panel.querySelector('#repair-add').onclick=()=>{addRepairItem();repairDirty=true};
 panel.querySelector('#repair-form').oninput=()=>repairDirty=true;
 panel.querySelector('#repair-back').onclick=()=>{if(repairDirty&&!confirm('未保存の修正を破棄して戻りますか？'))return;repairDirty=false;repairCurrent=null;showRepairLibrary()};
 panel.querySelector('#repair-form').onsubmit=e=>{e.preventDefault();repairRun(saveRepair)};
 panel.querySelector('#repair-history-load').onclick=()=>repairRun(loadRepairHistory);
 repairMessage('');
}
function acceptRepair(saved){const i=repairData.templates.findIndex(t=>t.id===saved.id);if(i>=0)repairData.templates[i]=saved;editRepair(saved);repairMessage('保存しました。変更前の内容と修正理由を履歴に残しました。')}
async function saveRepair(){
 const fields=Object.fromEntries([...repairDialog.querySelectorAll('[data-repair-field]')].map(e=>[e.dataset.repairField,e.value]));
 const items=[...repairDialog.querySelectorAll('.repairitem')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-repair-item]')].map(e=>[e.dataset.repairItem,e.dataset.repairItem==='quantity'&&e.value!==''?Number(e.value):e.value])));
 const target=Number(repairDialog.querySelector('#repair-target').value);
 if(!confirm(`「${repairCurrent.name}」を修正します。${target!==repairCurrent.user_id?'\n所有者を「'+repairOwner(target)+'」に変更します。':''}\n保存しますか？`))return;
 const saved=await api('/api/templates/admin/save',{id:repairCurrent.id,version:repairCurrent.version,user_id:target,folder:repairDialog.querySelector('#repair-folder').value,name:repairDialog.querySelector('#repair-name').value,fields,items,reason:repairDialog.querySelector('#repair-reason').value});acceptRepair(saved);
}
async function loadRepairHistory(){
 const id=repairCurrent.id;const history=await api('/api/templates/admin/history',{id});if(repairCurrent?.id!==id)return;
 const changes=h=>(h.changes||[]).map(x=>{const label=x.label||(x.item?`商品${x.item}：${repairItems.find(v=>v[0]===x.field)?.[1]||x.field}`:fieldNames[x.field]||x.field);return `<li>${esc(label)}：${esc(x.before)||'未設定'} → ${esc(x.after)||'未設定'}</li>`}).join('');
 const box=repairDialog.querySelector('#repair-history');box.innerHTML=history.length?history.map(h=>`<div class="repairhistory"><strong>${esc(h.at)} ／ ${esc(h.actor)} ／ ${esc(h.action)}</strong><p>${esc(h.reason)}</p><details><summary>変更した内容</summary><ul>${changes(h)||'<li>入力内容の変更なし</li>'}</ul></details><button type="button" data-repair-restore="${h.id}">この修正の前に戻す</button></div>`).join(''):'修正履歴はありません。';
 box.querySelectorAll('[data-repair-restore]').forEach(b=>b.onclick=()=>repairRun(async()=>{const reason=repairDialog.querySelector('#repair-reason').value.trim();if(!reason){repairMessage('修正理由に、元に戻す理由を入力してください。');repairDialog.querySelector('#repair-reason').focus();return}if(!confirm('現在のひな形と入力途中の内容を、選んだ修正の直前の内容・所有者へ戻しますか？ 戻す操作も履歴に残ります。'))return;acceptRepair(await api('/api/templates/admin/restore',{id:repairCurrent.id,version:repairCurrent.version,historyId:Number(b.dataset.repairRestore),reason}))}));
}
window.addEventListener('beforeunload',e=>{if(repairDirty){e.preventDefault();e.returnValue=''}});

async function openRepair(){
 if(currentUser?.role!=='owner')return;
 if(dirty){toast('開いている指示書の変更を保存してから管理・修正を開いてください。');return}
 repairCurrent=null;repairDirty=false;
 repairDialog.innerHTML=`<div class="dialoghead"><div><h2>管理・修正</h2><p>主管理者専用 ／ 行いたい操作を選んでください。</p></div><button type="button" id="repair-close">閉じる</button></div><div class="repairbody"><div class="repairmenu"><button type="button" id="repair-templates"><strong>ひな形を修正</strong><span>全員の保存済みひな形を確認・修正します。</span></button><button type="button" id="repair-records"><strong>指示書の状態・記録を修正</strong><span>作業状況、印刷済み、バーコード発行、発送記録を修正します。</span></button><button type="button" id="repair-backup"><strong>バックアップを作成</strong><span>指示書のデータ、出荷情報、貼付け位置の画像をこのパソコン内に保存します。</span></button><button type="button" id="repair-migrate-export"><strong>移行用バックアップを書き出し</strong><span>自宅PCから会社PCへ移すためのZIPをダウンロードします。利用者・指示書・在庫を含めます。画像は容量のため含めません。</span></button><button type="button" id="repair-migrate-restore"><strong>バックアップから復元</strong><span>移行用ZIPで、このパソコンの利用者・指示書・在庫などを置き換えます。画像はZIPに含まれず、既存の画像はそのままです。通信設定などは変わりません。</span></button><button type="button" id="repair-test-reset"><strong>テスト用：ピッキング依頼をリセット</strong><span>テストで作ったリストの取り込み・保存・送信確認をやり直します。通常の削除とは別です。実際の倉庫へ依頼したリストには使わないでください。</span></button></div><p id="repair-message" role="status"></p></div>`;
 repairDialog.querySelector('#repair-close').onclick=closeRepair;
 repairDialog.oncancel=e=>{e.preventDefault();closeRepair()};
 repairDialog.querySelector('#repair-templates').onclick=()=>repairRun(openTemplateRepair);
 repairDialog.querySelector('#repair-records').onclick=()=>repairRun(async()=>{
  const uid=currentUser.id;
  const workers=await api('/api/ship-workers');
  if(currentUser?.id!==uid||currentUser?.role!=='owner')return;
  repairDialog.close();paintDebugDesk(workers);
  const box=document.querySelector('#debugdeskdialog');
  box.onclose=()=>{box.onclose=null;if(currentUser?.id===uid&&currentUser?.role==='owner')openRepair()};
 });
 repairDialog.querySelector('#repair-backup').onclick=()=>repairRun(async()=>{const r=await api('/api/backup',{});repairMessage('バックアップを作成しました：'+r.filename)});
 repairDialog.querySelector('#repair-migrate-export').onclick=()=>repairRun(exportMigrationBackup);
 repairDialog.querySelector('#repair-migrate-restore').onclick=()=>openMigrationRestore();
 repairDialog.querySelector('#repair-test-reset').onclick=()=>openTestReset();
 if(!repairDialog.open)repairDialog.showModal();
}

function migrationFileToBase64(file){
 if(typeof fileToBase64==='function')return fileToBase64(file);
 return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>{const text=String(reader.result||'');resolve(text.slice(text.indexOf(',')+1))};reader.onerror=()=>reject(Error('ZIPを読み込めません。'));reader.readAsDataURL(file)});
}
async function exportMigrationBackup(){
 if(currentUser?.role!=='owner')return;
 repairMessage('書き出し中です。データ量によっては数分かかることがあります。');
 const r=await fetch('/api/migration/export');
 if(!r.ok){let msg='書き出せませんでした。';try{msg=(await r.json()).error||msg}catch{}throw Error(msg)}
 const blob=await r.blob();
 const header=(r.headers.get('Content-Disposition')||'').match(/filename=\"([^\"]+)\"/);
 const name=header?header[1]:`desk-migration-${new Date().toISOString().slice(0,19).replace(/[-:T]/g,'')}.zip`;
 const url=URL.createObjectURL(blob);
 const a=document.createElement('a');a.href=url;a.download=name;a.click();
 setTimeout(()=>URL.revokeObjectURL(url),15000);
 repairMessage('移行用バックアップを書き出しました：'+name+'　USBなどへコピーして、会社PCで「バックアップから復元」を使ってください。');
}

function migrationCountLines(counts){
 const labels=[['orders','指示書'],['users','利用者'],['templates','ひな形'],['pickingLists','ピッキングリスト'],['placements','貼付け位置'],['shipments','出荷情報'],['nameFormats','ネーム形式'],['labelSets','宛名の記憶'],['stockCustomers','在庫・顧客'],['stockProducts','在庫・商品'],['stockLots','在庫ロット']];
 return labels.map(([k,label])=>`${label}：${counts?.[k]??0}件`).join('\n');
}

function openMigrationRestore(){
 if(currentUser?.role!=='owner')return;
 let summary=null,file=null,dataB64='';
 repairDialog.innerHTML=`<div class="dialoghead"><div><h2>バックアップから復元</h2><p>主管理者専用 ／ 移行用ZIPで業務データを置き換えます。</p></div><button type="button" id="repair-close">閉じる</button></div><div class="repairbody"><div class="repairtools"><button type="button" id="repair-home">管理・修正に戻る</button></div><p id="repair-message" role="status"></p><p class="testwarn">この操作は、このパソコン上の指示書・利用者・在庫などの業務データを<strong>置き換え</strong>ます。貼付け位置・ネーム見本などの画像はZIPに含まれず、このパソコン上の画像はそのまま残ります。通信設定・起動設定・Thunderbirdの場所は変わりません。復元の直前に、いまの内容の自動バックアップも作ります。</p><form id="mig-form" class="repairfilters" style="grid-template-columns:1fr auto"><label>移行用ZIP<input id="mig-file" type="file" accept=".zip,application/zip" required></label><button type="submit">内容を確認</button></form><div id="mig-summary"></div><div id="mig-confirm" hidden><label class="checklabel"><input type="checkbox" id="mig-agree">このパソコンの業務データを置き換えることに同意します</label><button type="button" class="primary" id="mig-run" disabled>復元を実行</button></div></div>`;
 repairDialog.querySelector('#repair-close').onclick=closeRepair;
 repairDialog.querySelector('#repair-home').onclick=()=>openRepair();
 const paintSummary=()=>{
  const box=repairDialog.querySelector('#mig-summary');
  if(!summary){box.innerHTML='';repairDialog.querySelector('#mig-confirm').hidden=true;return}
  box.innerHTML=`<article class="repairhistory"><p><strong>作成日時</strong>　${esc(summary.createdAt||'不明')}</p><p><strong>形式</strong>　v${esc(String(summary.formatVersion||''))}　／　ファイル ${summary.fileCount??0} 件</p><pre class="migcounts">${esc(migrationCountLines(summary.keyCounts))}</pre><p>${esc(summary.warning||'')}</p></article>`;
  repairDialog.querySelector('#mig-confirm').hidden=false;
  const agree=repairDialog.querySelector('#mig-agree');const run=repairDialog.querySelector('#mig-run');
  agree.checked=false;run.disabled=true;
  agree.onchange=()=>{run.disabled=!agree.checked};
 };
 repairDialog.querySelector('#mig-form').onsubmit=e=>repairRun(async()=>{
  e.preventDefault();
  const input=repairDialog.querySelector('#mig-file');
  file=input.files?.[0];
  if(!file)throw Error('ZIPを選んでください。');
  dataB64=await migrationFileToBase64(file);
  summary=await api('/api/migration/inspect',{filename:file.name,data:dataB64});
  paintSummary();
  repairMessage('内容を確認し、同意にチェックしてから復元を実行してください。');
 });
 repairDialog.querySelector('#mig-run').onclick=()=>repairRun(async()=>{
  if(!summary||!dataB64)throw Error('先にZIPの内容を確認してください。');
  if(!repairDialog.querySelector('#mig-agree').checked)throw Error('置き換えへの同意が必要です。');
  if(!confirm('このパソコンの業務データを、選んだバックアップの内容で置き換えます。よろしいですか？'))return;
  const result=await api('/api/migration/restore',{filename:file.name,data:dataB64,confirm:true});
  const steps=(result.restartSteps||[]).map((s,i)=>`${i+1}. ${s}`).join('\n');
  repairDialog.innerHTML=`<div class="dialoghead"><div><h2>復元が完了しました</h2><p>主管理者専用</p></div></div><div class="repairbody"><p id="repair-message" role="status">作成日時 ${esc(result.createdAt||'')} のバックアップを反映しました。自動バックアップ：${esc(result.autoBackup||'—')}</p><pre class="migcounts">${esc(migrationCountLines(result.keyCounts))}</pre><p class="testwarn">つぎの手順で続けてください。</p><pre class="migcounts">${esc(steps)}</pre><button type="button" class="primary" id="mig-relogin">ログイン画面へ</button></div>`;
  repairDialog.querySelector('#mig-relogin').onclick=()=>{resetRepair();currentUser=null;dirty=false;authScreen('login')};
 });
 if(!repairDialog.open)repairDialog.showModal();
}

const testResetWarning='この操作はテストをやり直すためのものです。送信したメールや、倉庫にある添付Excelは取り消せません。実際の倉庫へ依頼したリストには使用しないでください。';
function openTestReset(){
 if(currentUser?.role!=='owner')return;
 let plan=null,revert=false;
 repairDialog.innerHTML=`<div class="dialoghead"><div><h2>テスト用：ピッキング依頼をリセット</h2><p>主管理者専用 ／ 通常の削除とは別です。</p></div><button type="button" id="repair-close">閉じる</button></div><div class="repairbody"><div class="repairtools"><button type="button" id="repair-home">管理・修正に戻る</button></div><p id="repair-message" role="status"></p><p class="testwarn">${esc(testResetWarning)}</p><form id="test-search" class="repairfilters"><label>ピッキングリスト番号<input id="test-q" maxlength="40" placeholder="PK-000001" required></label><button type="submit">検索</button></form><div id="test-results"></div><div id="test-detail"></div></div>`;
 repairDialog.querySelector('#repair-close').onclick=closeRepair;
 repairDialog.querySelector('#repair-home').onclick=()=>openRepair();
 const paintList=items=>{
  const box=repairDialog.querySelector('#test-results');
  if(!items.length){box.innerHTML='<p>該当するリストはありません。</p>';return}
  box.innerHTML=items.map(item=>`<article class="repairhistory"><p><strong>${esc(item.number)}</strong>　${esc(item.title)||'保存名なし'}　${esc(item.phase)}</p><p>メール作成：${item.mailedAt?esc(item.mailedAt):'なし'}　送信確認：${item.sentAt?esc(item.sentAt):'なし'}　手配完了：${item.arrangedAt?esc(item.arrangedAt):'なし'}</p><button type="button" data-test-open="${item.id}">内容を確認</button></article>`).join('');
  box.querySelectorAll('[data-test-open]').forEach(b=>b.onclick=()=>repairRun(()=>loadPlan(Number(b.dataset.testOpen))));
 };
 const paintPlan=()=>{
  const box=repairDialog.querySelector('#test-detail');
  if(!plan){box.innerHTML='';return}
  const mail=plan.mailedAt?`${plan.mailedAt}　${plan.mailedBy||''}`:'なし';
  const sent=plan.sentAt?`${plan.sentAt}　${plan.sentBy||''}`:'なし';
  const sheets=plan.sheets.map(s=>`<article class="repairhistory"><p><strong>${esc(s.number)}</strong>　${esc(s.customer)||'顧客名なし'}　作業状況 ${esc(s.status)||'—'}</p><p>取り込み：${esc(s.releaseText)}</p><p>フラグ：${esc(s.flagText)}</p><p>作業状況の扱い：${esc(s.statusText)}</p></article>`).join('')||'<p>このリストに指示書はありません。</p>';
  const lines=name=>`<ul class="testkeep">${(plan[name]||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
  const skipped=plan.skipped.length?`<h3>対象外</h3><ul class="testkeep">${plan.skipped.map(s=>`<li>${esc(s.number)}：${esc(s.reason)}</li>`).join('')}</ul>`:'';
  box.innerHTML=`<h3>${esc(plan.number)}　${esc(plan.title)||'保存名なし'}</h3><p>状態：${esc(plan.phase)}　依頼日：${esc(plan.date)||'—'}</p><p>メール作成：${esc(mail)}</p><p>送信確認：${esc(sent)}</p><p>手配完了：${plan.arrangedAt?esc(plan.arrangedAt)+'　'+esc(plan.arrangedBy||'')+'　'+esc(plan.arrangeMethod||''):'なし'}</p><h3>含まれている指示書</h3>${sheets}<h3>解除する情報</h3>${lines('remove')}<h3>元に戻す情報</h3>${lines('restore')}<h3>変更しない情報</h3>${lines('keep')}${skipped}<p class="testwarn">${esc(plan.statusNotice)}<br>${esc(plan.warning)}</p>${plan.already?'<p>すでに削除扱いです。新しいリストを作成して再テストしてください。</p>':`<form id="test-run"><label class="checklabel"><input type="checkbox" id="test-revert" ${revert?'checked':''}>このリストの手配完了で作業中になった指示書も、未着手に戻す</label><label class="repairreason">修正理由（必須）<textarea id="test-reason" maxlength="500" required placeholder="例：手配完了の動作確認をやり直す"></textarea></label><label>対象のリスト番号を再入力<input id="test-number" maxlength="20" required placeholder="${esc(plan.number)}" autocomplete="off"></label><label class="checklabel"><input type="checkbox" id="test-ack">実際の倉庫へ依頼していないテストデータです</label><button class="primary" type="submit">テスト用リセットを実行</button></form>`}`;
  const check=box.querySelector('#test-revert');
  if(check)check.onchange=()=>{
   const reason=repairDialog.querySelector('#test-reason').value;
   const typed=repairDialog.querySelector('#test-number').value;
   const ack=repairDialog.querySelector('#test-ack').checked;
   revert=check.checked;
   repairRun(async()=>{await loadPlan(plan.id);const reasonBox=repairDialog.querySelector('#test-reason');if(reasonBox)reasonBox.value=reason;const numberBox=repairDialog.querySelector('#test-number');if(numberBox)numberBox.value=typed;const ackBox=repairDialog.querySelector('#test-ack');if(ackBox)ackBox.checked=ack});
  };
  const form=box.querySelector('#test-run');
  if(form)form.onsubmit=e=>{e.preventDefault();repairRun(runReset)};
 };
 const loadPlan=async id=>{
  plan=await api('/api/picking/test-reset/preview',{id,revertStatus:revert});
  paintPlan();
 };
 const runReset=async()=>{
  if(currentUser?.role!=='owner'||!plan||plan.already)return;
  const reason=repairDialog.querySelector('#test-reason').value.trim();
  const typed=repairDialog.querySelector('#test-number').value.trim();
  const ack=repairDialog.querySelector('#test-ack').checked;
  if(!reason){repairMessage('修正理由を入力してください。');repairDialog.querySelector('#test-reason').focus();return}
  if(typed!==plan.number){repairMessage('リスト番号が一致しません。対象の番号を入力してください。');repairDialog.querySelector('#test-number').focus();return}
  if(!ack){repairMessage('テストデータであることを確認してください。実際の倉庫へ依頼したリストには使用しないでください。');repairDialog.querySelector('#test-ack').focus();return}
  const extra=plan.sentAt?'送信確認済みのテストデータを戻します。実際の倉庫へ依頼したリストではありません。\n':'';
  if(!confirm(`${plan.number} をテスト用にリセットします。\n${extra}${testResetWarning}\n理由：${reason}`))return;
  const result=await api('/api/picking/test-reset',{id:plan.id,version:plan.version,reason,confirmNumber:typed,acknowledgeTest:true,revertStatus:revert,orders:plan.sheets.filter(s=>s.version!=null).map(s=>({id:s.orderId,version:s.version})),carry:[...new Map(plan.carry.map(x=>[x.day,x])).values()].map(x=>({day:x.day,version:x.version}))});
  repairMessage(result.already?result.message:'テスト用リセットを実施しました。リストは一覧から外し、記録は残しています。新しいリストを作成して再テストしてください。');
  if(typeof refresh==='function')await refresh();
  plan=await api('/api/picking/test-reset/preview',{id:plan.id,revertStatus:false});
  revert=false;
  paintPlan();
 };
 repairDialog.querySelector('#test-search').onsubmit=e=>{e.preventDefault();repairRun(async()=>{plan=null;paintPlan();const box=repairDialog.querySelector('#test-results');box.textContent='検索しています。';const found=await api('/api/picking/test-reset/search',{q:repairDialog.querySelector('#test-q').value.trim()});paintList(found.items||[]);if(!(found.items||[]).length){repairMessage('該当するリストはありません。');box.textContent='該当するリストはありません。'}})};
}

