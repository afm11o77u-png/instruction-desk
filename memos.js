'use strict';
// 構想メモ（主管理者のみ・読み取り専用）。memos/*.md を一覧表示。
const memoDialog=document.createElement('dialog');memoDialog.id='memodesk';document.body.append(memoDialog);
function resetMemos(){memoDialog.close();memoDialog.replaceChildren()}
// [red]…[/red] で囲んだ部分は赤字（複数行にまたがる場合は行ごとに囲む）。
function memoInline(s){return esc(s).replace(/\[red\]([\s\S]*?)\[\/red\]/g,'<span class="memo-red">$1</span>').replace(/\[([^\]\n]+)\]\(memo:([a-z0-9-]+)\)/g,'<button type="button" class="quiet memo-link" data-memo-link="$2">$1 →</button>')}
function memoHtml(md){
 const out=[];let list=null;let code=null;
 const close=()=>{if(list){out.push(`</${list}>`);list=null}};
 const closeCode=()=>{
  if(!code)return;
  out.push(`<div class="memo-code"><button type="button" class="quiet memo-copy">コピー</button><pre><code>${esc(code.join('\n'))}</code></pre></div>`);
  code=null;
 };
 for(const raw of md.split('\n')){
  if(code){
   if(raw.trim()==='```'){closeCode();continue}
   code.push(raw);
   continue;
  }
  if(raw.trim().startsWith('```')){close();code=[];continue}
  const line=raw.trimEnd();let m;
  if(!line){close();continue}
  if((m=line.match(/^(#{1,3}) (.*)$/))){close();const n=m[1].length+1;out.push(`<h${n}>${memoInline(m[2])}</h${n}>`);continue}
  if((m=line.match(/^\d+\. (.*)$/))){if(list!=='ol'){close();out.push('<ol>');list='ol'}const num=line.match(/^(\d+)/)[1];out.push(`<li value="${num}">${memoInline(m[1])}</li>`);continue}
  if((m=line.match(/^[-*] (.*)$/))){if(list!=='ul'){close();out.push('<ul>');list='ul'}out.push(`<li>${memoInline(m[1])}</li>`);continue}
  close();out.push(`<p>${memoInline(line)}</p>`)
 }
 closeCode();close();return out.join('')
}
function bindMemoCopy(){
 memoDialog.querySelectorAll('[data-memo-link]').forEach(b=>{
  b.onclick=()=>showMemo(b.dataset.memoLink).catch(e=>toast(e.message));
 });
 memoDialog.querySelectorAll('.memo-copy').forEach(b=>{
  b.onclick=async()=>{
   const code=b.parentElement.querySelector('code');
   const text=code?code.innerText:'';
   try{await navigator.clipboard.writeText(text);toast('コピーしました。')}
   catch(e){toast('コピーできませんでした。')}
  };
 });
}
async function showMemo(id){
 const m=await api('/api/memos/'+encodeURIComponent(id));
 memoDialog.querySelectorAll('[data-memo]').forEach(b=>b.classList.toggle('active',b.dataset.memo===id));
 memoDialog.querySelector('#memo-body').innerHTML=memoHtml(m.markdown);
 bindMemoCopy();
}
async function openMemos(opts){
 if(currentUser?.role!=='owner')return;
 opts=opts||{};
 const list=await api('/api/memos');
 const shown=opts.only?list.filter(m=>m.id===opts.only):list;
 const title=opts.title||'構想メモ';
 memoDialog.innerHTML=`<div class="dialoghead"><h2>${esc(title)}</h2><button type="button" class="quiet" id="memo-close">閉じる ×</button></div><div class="dialogbody memo-layout"><nav class="memo-list">${shown.map(m=>`<button type="button" class="nav" data-memo="${esc(m.id)}">${esc(m.title)}</button>`).join('')||'<p>メモはありません。</p>'}</nav><article id="memo-body" class="memo-body"></article></div>`;
 memoDialog.querySelector('#memo-close').onclick=()=>memoDialog.close();
 memoDialog.querySelectorAll('[data-memo]').forEach(b=>b.onclick=()=>showMemo(b.dataset.memo).catch(e=>toast(e.message)));
 memoDialog.showModal();
 if(opts.only)await showMemo(opts.only);
 else if(shown.length)await showMemo(shown[0].id);
}
function openOldDataMemo(){
 return openMemos({only:'03-kyudata-toridashi',title:'旧データの取り出し方'});
}

function openStockMemo(){
 return openMemos({only:'06-product-code-color-rules',title:'在庫メモ'});
}

function openProcedureMemo(){
 return openMemos({only:'04-mark-workflow',title:'手順メモ'});
}
