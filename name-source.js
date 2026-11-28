// ネーム情報（ソース）：Excelの表をそのまま編集し、WEBのネーム情報とは差分を見て行ごとに反映する。
(function(){
const NOTE_SPAN=['placement','size','color','font','font_size','bold','lines','name_rule','fixed_text','band','note','memo'];
let ns={columns:[],rows:[],syncFields:[],canEdit:false},nsDiffData=null;
const nsEsc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function nsMount(){
 if(document.getElementById('namesourcedesk'))return;
 const base=document.getElementById('nameinfodesk');if(!base)return;
 const sec=document.createElement('section');sec.id='namesourcedesk';sec.hidden=true;
 sec.innerHTML=`<form id="nsbar"><input id="nsq" placeholder="絞り込み" maxlength="80" aria-label="絞り込み"><button type="button" class="primary" id="nsdiffopen">差分を確認</button><button type="button" id="nsexport">Excel書き出し</button><button type="button" id="nsimport" hidden>Excel取り込み</button><button type="button" id="nsadd" hidden>行を追加</button><input type="file" id="nsfile" accept=".xlsx" hidden></form><p id="nsstatus"></p><div class="nswrap"><table id="nstable"></table></div>`;
 base.after(sec);
 const dlg=document.createElement('dialog');dlg.id='nsdiff';
 dlg.innerHTML=`<div class="dialoghead"><h2>差分</h2><span id="nscounts"></span><button type="button" id="nsrelink" hidden>再照合</button><button type="button" class="quiet" id="nsdiffclose">閉じる ×</button></div><div class="dialogbody" id="nsdiffbody"></div><div class="dialog-actions"><button type="button" class="primary" id="nsapply" hidden>反映</button></div>`;
 document.body.append(dlg);
 $('#nsq').oninput=nsPaint;
 $('#nsbar').onsubmit=e=>e.preventDefault();
 $('#nsexport').onclick=()=>{location.href='/api/name-source/export'};
 $('#nsimport').onclick=()=>$('#nsfile').click();
 $('#nsfile').onchange=nsImport;
 $('#nsadd').onclick=()=>nsAdd(null);
 $('#nsdiffopen').onclick=()=>nsDiff(false);
 $('#nsrelink').onclick=()=>nsDiff(true);
 $('#nsdiffclose').onclick=()=>dlg.close();
 $('#nsapply').onclick=nsApply;
 $('#nstable').addEventListener('click',nsClick);
}
async function loadNameSource(){
 if(currentUser?.role!=='owner'){const desk=document.getElementById('namesourcedesk');if(desk)desk.hidden=true;return}
 nsMount();$('#namesourcedesk').hidden=false;
 try{ns=await api('/api/name-source')}catch(e){$('#nsstatus').textContent=e.message;return}
 $('#nsimport').hidden=$('#nsadd').hidden=!ns.canEdit;
 nsPaint();
}
window.loadNameSource=loadNameSource;
nsMount();
function nsLabel(key){return (ns.columns.find(c=>c.key===key)||{}).label||key}
function nsPaint(){
 const q=($('#nsq').value||'').trim().toLowerCase();
 const keys=ns.columns.map(c=>c.key);
 const rows=q?ns.rows.filter(r=>keys.some(k=>String(r[k]||'').toLowerCase().includes(q))):ns.rows;
 const linked=ns.rows.filter(r=>r.formatId).length;
 $('#nsstatus').textContent=ns.rows.length?`${rows.length} / ${ns.rows.length}行　紐づけ ${linked}`+(ns.meta?.importedAt?`　取り込み ${ns.meta.importedAt.replace('T',' ')}`:''):'まだ取り込んでいません。';
 const head='<thead><tr>'+ns.columns.map(c=>`<th class="${ns.syncFields.includes(c.key)?'nssync':''}" title="${ns.syncFields.includes(c.key)?'WEBと比べる列':''}">${nsEsc(c.label)}</th>`).join('')+(ns.canEdit?'<th></th>':'')+'</tr></thead>';
 const body=rows.map(r=>{
  let cells='';
  for(const k of keys){
   if(r.kind==='note'&&NOTE_SPAN.includes(k)){if(k==='placement')cells+=`<td class="nsnote" colspan="${NOTE_SPAN.length}" data-field="placement">${nsEsc(r.placement)}</td>`;continue}
   cells+=`<td data-field="${k}">${nsEsc(r[k])}</td>`;
  }
  const act=ns.canEdit?`<td class="nsact"><button type="button" data-ns-add title="下に行を追加">＋</button><button type="button" data-ns-del title="行を削除">×</button></td>`:'';
  return `<tr data-id="${r.id}" class="${r.kind==='note'?'nsnoterow':''}">${cells}${act}</tr>`;
 }).join('');
 $('#nstable').innerHTML=head+'<tbody>'+body+'</tbody>';
}
function nsClick(e){
 const tr=e.target.closest('tr[data-id]');if(!tr)return;
 const id=Number(tr.dataset.id);
 if(e.target.closest('[data-ns-add]'))return nsAdd(id);
 if(e.target.closest('[data-ns-del]'))return nsDelete(id);
 const td=e.target.closest('td[data-field]');
 if(!td||!ns.canEdit||td.querySelector('textarea'))return;
 const row=ns.rows.find(r=>r.id===id);const field=td.dataset.field;const old=row[field]||'';
 const ta=document.createElement('textarea');ta.value=old;ta.rows=Math.max(2,old.split('\n').length);
 td.textContent='';td.append(ta);ta.focus();
 let done=false;
 const finish=async save=>{
  if(done)return;done=true;
  if(!save||ta.value===old){td.textContent=old;return}
  try{const saved=await api('/api/name-source/save',{id,field,value:ta.value});Object.assign(row,saved);td.textContent=saved[field]||''}
  catch(err){toast(err.message);td.textContent=old}
 };
 ta.onblur=()=>finish(true);
 ta.onkeydown=ev=>{if(ev.key==='Escape'){ev.preventDefault();finish(false)}else if(ev.key==='Enter'&&(ev.ctrlKey||ev.metaKey)){ev.preventDefault();ta.blur()}};
}
async function nsAdd(afterId){
 try{const row=await api('/api/name-source/add',afterId?{afterId}:{});await loadNameSource();const tr=document.querySelector(`#nstable tr[data-id="${row.id}"]`);if(tr){tr.scrollIntoView({block:'center'});tr.classList.add('nsnew')}}catch(e){toast(e.message)}
}
async function nsDelete(id){
 const row=ns.rows.find(r=>r.id===id);
 if(!confirm(`この行を削除しますか？\n${row.customer||''} ${row.placement||''}`))return;
 try{await api('/api/name-source/delete',{id});await loadNameSource()}catch(e){toast(e.message)}
}
async function nsImport(){
 const file=$('#nsfile').files[0];$('#nsfile').value='';if(!file)return;
 const data=await new Promise((ok,ng)=>{const fr=new FileReader();fr.onload=()=>ok(String(fr.result).split(',')[1]);fr.onerror=ng;fr.readAsDataURL(file)});
 try{
  const pre=(await api('/api/name-source/import',{data,filename:file.name})).preview;
  if(!confirm(`${file.name}\n${pre.rows}行（補足 ${pre.notes}行）`+(pre.current?`\n今の${pre.current}行を置き換えます。`:'')+'\nWEBのネーム情報は変わりません。'))return;
  const r=await api('/api/name-source/import',{data,filename:file.name,confirm:true});
  toast(`取り込みました（紐づけ ${r.autoLinked}件）`);await loadNameSource();
 }catch(e){toast(e.message)}
}
const NS_TYPES=[['changed','違い'],['source_only','ソースのみ'],['web_only','WEBのみ']];
const NS_OPTS={changed:[['','保留'],['to_web','ソース→WEB'],['to_source','WEB→ソース']],source_only:[['','保留'],['to_web','ソース→WEB']],web_only:[['','保留'],['to_source','WEB→ソース']]};
async function nsDiff(relink){
 const dlg=$('#nsdiff');
 try{nsDiffData=await api('/api/name-source/diff',relink?{relink:true}:{})}catch(e){toast(e.message);return}
 const c=nsDiffData.counts;
 $('#nscounts').textContent=`一致 ${c.same}　違い ${c.changed}　ソースのみ ${c.source_only}　WEBのみ ${c.web_only}`;
 $('#nsrelink').hidden=$('#nsapply').hidden=!ns.canEdit;
 let html='';
 for(const [type,label] of NS_TYPES){
  const items=nsDiffData.items.filter(i=>i.type===type);if(!items.length)continue;
  const bulk=ns.canEdit?`<details class="nsbulk"><summary>まとめて</summary><div>${NS_OPTS[type].map(([v,l])=>`<button type="button" data-ns-bulk="${type}" data-v="${v}">${l}</button>`).join('')}</div></details>`:'';
  html+=`<section class="nsgroup"><h3>${label} ${items.length}${bulk}</h3><table><tbody>`+items.map(i=>`<tr><td><b>${nsEsc(i.customer)}</b><small>${nsEsc(i.title)} ${nsEsc(i.nameId)}</small></td><td>${i.diffs.map(d=>`<div class="nsd"><span>${nsEsc(d.label)}</span><s>${nsEsc(type==='web_only'?'':d.source)}</s><i>${nsEsc(type==='source_only'?'':d.web)}</i></div>`).join('')}</td><td>${ns.canEdit?`<select data-ns-key="${i.key}" data-type="${type}">${NS_OPTS[type].map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select>`:''}</td></tr>`).join('')+'</tbody></table></section>';
 }
 $('#nsdiffbody').innerHTML=(html?'<p class="nslegend"><s>ソース</s><i>WEB</i></p>':'<p>違いはありません。</p>')+html;
 $('#nsdiffbody').querySelectorAll('[data-ns-bulk]').forEach(b=>b.onclick=()=>{$('#nsdiffbody').querySelectorAll(`select[data-type="${b.dataset.nsBulk}"]`).forEach(s=>s.value=b.dataset.v);b.closest('details').open=false});
 if(!dlg.open)dlg.showModal();
}
async function nsApply(){
 const choices={};let toWeb=0,toSrc=0;
 $('#nsdiffbody').querySelectorAll('select[data-ns-key]').forEach(s=>{if(s.value){choices[s.dataset.nsKey]=s.value;s.value==='to_web'?toWeb++:toSrc++}});
 if(!toWeb&&!toSrc){toast('反映する行を選んでください。');return}
 if(!confirm(`ソース→WEB ${toWeb}件\nWEB→ソース ${toSrc}件\n反映します（反映前にバックアップします）。`))return;
 try{const r=await api('/api/name-source/apply',{choices});toast(`反映しました（バックアップ：${r.backup||'なし'}）`);await loadNameSource();await nsDiff(false)}catch(e){toast(e.message)}
}
})();
