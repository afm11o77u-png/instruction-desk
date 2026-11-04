'use strict';
// 構想メモ（主管理者のみ・読み取り専用）。memos/*.md を一覧表示。
const memoDialog=document.createElement('dialog');memoDialog.id='memodesk';document.body.append(memoDialog);
function resetMemos(){memoDialog.close();memoDialog.replaceChildren()}
// [red]…[/red] で囲んだ部分は赤字（複数行にまたがる場合は行ごとに囲む）。
function memoInline(s){return esc(s).replace(/\[red\]([\s\S]*?)\[\/red\]/g,'<span class="memo-red">$1</span>')}
function memoHtml(md){
 const out=[];let list=null;
 const close=()=>{if(list){out.push(`</${list}>`);list=null}};
 for(const raw of md.split('\n')){
  const line=raw.trimEnd();let m;
  if(!line){close();continue}
  if((m=line.match(/^(#{1,3}) (.*)$/))){close();const n=m[1].length+1;out.push(`<h${n}>${memoInline(m[2])}</h${n}>`);continue}
  if((m=line.match(/^\d+\. (.*)$/))){if(list!=='ol'){close();out.push('<ol>');list='ol'}const num=line.match(/^(\d+)/)[1];out.push(`<li value="${num}">${memoInline(m[1])}</li>`);continue}
  if((m=line.match(/^[-*] (.*)$/))){if(list!=='ul'){close();out.push('<ul>');list='ul'}out.push(`<li>${memoInline(m[1])}</li>`);continue}
  close();out.push(`<p>${memoInline(line)}</p>`)
 }
 close();return out.join('')
}
async function showMemo(id){
 const m=await api('/api/memos/'+encodeURIComponent(id));
 memoDialog.querySelectorAll('[data-memo]').forEach(b=>b.classList.toggle('active',b.dataset.memo===id));
 memoDialog.querySelector('#memo-body').innerHTML=memoHtml(m.markdown);
}
async function openMemos(){
 if(currentUser?.role!=='owner')return;
 const list=await api('/api/memos');
 memoDialog.innerHTML=`<div class="dialoghead"><h2>構想メモ</h2><button type="button" class="quiet" id="memo-close">閉じる ×</button></div><div class="dialogbody memo-layout"><nav class="memo-list">${list.map(m=>`<button type="button" class="nav" data-memo="${esc(m.id)}">${esc(m.title)}</button>`).join('')||'<p>メモはありません。</p>'}</nav><article id="memo-body" class="memo-body"></article></div>`;
 memoDialog.querySelector('#memo-close').onclick=()=>memoDialog.close();
 memoDialog.querySelectorAll('[data-memo]').forEach(b=>b.onclick=()=>showMemo(b.dataset.memo).catch(e=>toast(e.message)));
 memoDialog.showModal();
 if(list.length)await showMemo(list[0].id);
}
