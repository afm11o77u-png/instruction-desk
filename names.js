const DEFAULT_MIX_COLORS=['赤','青','緑','白'];
function nameMixColors(list){
 const rows=(list&&list.length?list:DEFAULT_MIX_COLORS).map(v=>String(v||'').trim()).filter(Boolean);
 return rows.length?rows:DEFAULT_MIX_COLORS.slice();
}
function solidPaperSwatch(name){
 const key=String(name||'').replace(/[?？]/g,'').trim();
 return {青:'#87CEFA',緑:'#90EE90',赤:'#F08080',黄:'#FFFFE0',黄色:'#FFFFE0',オレンジ:'#FFDAB9',白:'#FFFFFF'}[key]||'#fff';
}
function nameMixGradient(list){
 const colors=nameMixColors(list);
 const n=colors.length||1;
 return `linear-gradient(135deg,${colors.map((c,i)=>{const hex=solidPaperSwatch(c);const a=((i/n)*100).toFixed(4);const b=(((i+1)/n)*100).toFixed(4);return `${hex} ${a}%,${hex} ${b}%`}).join(',')})`;
}
const nameMixBackground=nameMixGradient(DEFAULT_MIX_COLORS);
function nameIsMix(value){return String(value||'').replace(/[?？]/g,'').trim()==='先方から指定'}
function nameMixNote(value,mixColors){
 if(!nameIsMix(value))return '';
 const colors=nameMixColors(mixColors);
 return `色は先方指定（${colors.join('・')}）。${colors.length}色表示は選択肢の見本です。`;
}
function nameMixParen(mixColors){return `（${nameMixColors(mixColors).join('・')}）`}

function nameDimensions(item){
 if(item.sizeKind==='unknown'||item.sizeKind==='blank'||item.blank||/未指定|無地/.test(item.size||''))return null;
 const match=String(item.size||'').match(/([0-9.]+)\s*[×x]\s*([0-9.]+)\s*mm/i);
 const w=Number(item.widthMm||match?.[1]),h=Number(item.heightMm||match?.[2]);
 return Number.isFinite(w)&&Number.isFinite(h)&&w>0&&h>0?{w,h}:null;
}
function nameSizeHeading(item){
 const d=nameDimensions(item);
 if(!d)return item.blank?'無地・寸法未表示':'サイズ未指定（要確認）';
 const kind=item.sizeKind==='small'||String(item.size||'').startsWith('小')?'小ネーム':item.sizeKind==='large'||String(item.size||'').startsWith('大')?'大ネーム':'ネーム';
 return `${kind}｜横${d.w} × 縦${d.h} mm`;
}
function nameMixStyle(item,paperColor){
 const color=paperColor===undefined?item.fixedColor:paperColor;
 if(!nameIsMix(color))return '';
 return `background:${nameMixGradient(item.mixColors)}`;
}
function nameScalePicture(item,url,paperColor){
 const mix=!!url&&nameIsMix(paperColor===undefined?item.fixedColor:paperColor);
 const mixStyle=mix?nameMixStyle(item,paperColor):'';
 const d=nameDimensions(item);
 const img=url?`<img src="${url}" alt="${esc(item.format||'ネーム見本')}" loading="lazy">`:`<span class="namemiss">${item.blank?'印字なし':'見本なし'}</span>`;
 if(!d)return `<span class="namescale-stage unknown"><span class="nameunknown-image${mix?' namepaper-mix':''}"${mixStyle?` style="${mixStyle}"`:''}>${img}</span></span>`;
 return `<span class="namescale-stage"><span class="namescale-box${mix?' namepaper-mix':''}" style="width:${d.w*2}px;height:${d.h*2}px${mixStyle?`;${mixStyle}`:''}"><span class="namedim-width">${d.w} mm</span><span class="namedim-height">${d.h} mm</span>${img}</span></span>`;
}
let nameZoomDialog;
function openNameZoom(item,sample){
 if(!nameZoomDialog){
  nameZoomDialog=document.createElement('dialog');nameZoomDialog.id='namezoom';nameZoomDialog.setAttribute('aria-labelledby','namezoomtitle');
  nameZoomDialog.innerHTML='<div class="namezoom-head"><h2 id="namezoomtitle"></h2><button type="button" id="namezoomclose">閉じる</button></div><p id="namezoomnote"></p><div class="namezoom-image"><span id="namezoompaper"><img id="namezoomimage" alt=""></span></div>';
  document.body.appendChild(nameZoomDialog);
  $('#namezoomclose').onclick=()=>nameZoomDialog.close();
  nameZoomDialog.addEventListener('click',e=>{if(e.target===nameZoomDialog)nameZoomDialog.close()});
 }
 $('#namezoomtitle').textContent=nameSizeHeading(item);
 $('#namezoomnote').textContent=[item.format,sample.condition,nameMixNote(sample.paperColor||item.fixedColor,item.mixColors),'拡大表示です。実物大ではありません。'].filter(Boolean).join(' ／ ');
 $('#namezoomimage').src=nameSampleUrl(sample.id);$('#namezoomimage').alt=item.format+' '+(sample.condition||'');
 const mix=nameIsMix(sample.paperColor||item.fixedColor);
 $('#namezoompaper').classList.toggle('namepaper-mix',mix);
 $('#namezoompaper').style.background=mix?nameMixGradient(item.mixColors):'';
 const dim=nameDimensions(item);$('#namezoompaper').style.setProperty('--name-ratio',dim?dim.w/dim.h:1.6);
 nameZoomDialog.showModal();
}
let nameBusy=false;
function nameSampleUrl(id){return '/api/name-labels/samples/'+id}
function fontLabel(name){
 const text=String(name||'');
 if(text.normalize('NFKC').toUpperCase().startsWith('HG丸ゴシック')) return 'HG丸ゴシック';
 return text;
}
function paperSwatch(name,mixColors){
 if(nameIsMix(name))return nameMixGradient(mixColors);
 return solidPaperSwatch(name);
}
function nameSwatches(colors,fixed,mixColors){
 const rows=(colors||[]).map(row=>`<span class="nameswatch"><i style="background:${esc(nameIsMix(row.color)?nameMixGradient(mixColors):(row.swatch||solidPaperSwatch(row.color)))}"></i>${esc(row.group)}：${esc(row.color)||'色未設定'}</span>`).join('');
 if(rows)return rows;
 if(fixed)return `<span class="nameswatch"><i style="background:${paperSwatch(fixed,mixColors)}"></i>${esc(fixed)}${nameIsMix(fixed)?nameMixParen(mixColors):''}</span>`;
 return '色の指定はありません';
}
function paintNameCustomers(data){
 const box=$('#namecustomers');
 const items=data.items||[];
 const fonts=(data.missingFonts||[]).filter(name=>!String(name).normalize('NFKC').toUpperCase().startsWith('HG丸ゴシック'));
 const fontNote=fonts.length?`このパソコンにない書体：${fonts.join('、')}。その形式は要確認です。`:''
 $('#namestatus').textContent=items.length?`${items.length}件の顧客です。${fontNote}`:(fontNote||'該当する顧客はありません。');
 box.innerHTML=items.map(item=>{
  const people=(item.assignees||[]).filter(Boolean);
  const who=people.length?people.join('、'):'担当未設定';
  return `<button type="button" class="namecustomer" data-id="${item.id}"><b>${esc(item.sourceName)}</b><span>担当：${esc(who)}</span><span>了承 ${item.approvedCount||0}　未了承 ${item.unapprovedCount||0}</span><span>${item.formatCount}形式${item.reviewCount?`　要確認 ${item.reviewCount}`:''}</span><span>${item.linkStatus==='対応'?'指示書デスクの顧客名と一致':'未対応'}</span></button>`;
 }).join('');
 box.querySelectorAll('.namecustomer').forEach(button=>button.onclick=()=>openNameCustomer(Number(button.dataset.id)));
}
function paintNameFormats(data){
 const box=$('#nameformats');
 const link=data.linkStatus==='対応'?`指示書デスクの顧客名と一致：${data.linkedName}`:'指示書デスクの顧客とは未対応です。名前だけでは結び付けていません。';
 const formats=(data.formats||[]).map(item=>`<button type="button" class="namecard" data-id="${item.id}"><span class="namesize-heading">${esc(nameSizeHeading(item))}</span>${nameScalePicture(item,item.hasImage?nameSampleUrl(item.sampleId):null)}<b>${esc(item.format)}</b><span class="namecolors">${nameSwatches(item.colors,item.fixedColor,item.mixColors)}</span><span>${esc(item.placement)||'貼付け位置の指定なし'}</span>${item.needsReview?'<em>要確認</em>':''}</button>`).join('');
 const owner=currentUser?.role==='owner';
 const childCount=(data.formats||[]).length;
 box.innerHTML=`<div class="namehead"><h2>${esc(data.sourceName)}</h2><p>${esc(link)}</p><p>${esc(data.reading)?`読み：${esc(data.reading)}`:''}</p>${owner?`<div class="namehead-actions"><button type="button" id="nameaddformat">この顧客に形式を追加</button><button type="button" class="danger" id="namedeletecustomer">この顧客を削除</button></div>`:''}</div><p class="namescale-note">大小を同じ縮尺で表示しています（実物大ではありません）。形式を開くと、見本をクリックして拡大できます。</p><div class="namegrid">${formats||'<p>形式はありません。</p>'}</div>`;
 box.querySelectorAll('.namecard').forEach(button=>button.onclick=()=>openNameFormat(Number(button.dataset.id)));
 const add=$('#nameaddformat');
 if(add)add.onclick=()=>openNameEditor(null,{id:data.id,sourceName:data.sourceName,reading:data.reading||''});
 const del=$('#namedeletecustomer');
 if(del)del.onclick=()=>confirmDeleteCustomer(data,childCount);
}
async function loadNames(reset){
 if(nameBusy)return;
 nameBusy=true;
 syncNameDeletedButton();
 if(reset)$('#nameformats').innerHTML='<p class="nameempty">左の顧客を選ぶと、形式がここに出ます。</p>';
 $('#namestatus').textContent='読み込んでいます。';
 try{
  const q=$('#namecustomer').value.trim();
  const data=await api('/api/name-labels'+(q?'?q='+encodeURIComponent(q):''));
  paintNameCustomers(data);
 }catch(e){$('#namestatus').textContent=e.message||'読み込めませんでした。'}
 finally{nameBusy=false}
}
async function openNameCustomer(id){
 document.querySelectorAll('#namecustomers .namecustomer').forEach(button=>button.classList.toggle('active',Number(button.dataset.id)===id));
 try{
  paintNameFormats(await api('/api/name-labels/customers/'+id));
  $('#nameformats').scrollTop=0;
 }catch(e){$('#namestatus').textContent=e.message||'読み込めませんでした。'}
}
function paintNameDeptRoleSection(item){
 const depts=item.departments||[];
 const roles=item.roles||[];
 const pairs=Array.isArray(item.deptRolePairs)?item.deptRolePairs:[];
 // 役職あり／ペアありのときだけ独立セクション（部署のみの従来顧客は貼付け位置下の一行表示を維持）
 if(!roles.length&&!pairs.length)return{html:'',hideInlineDepartments:false};
 let body='';
 if(pairs.length){
  body=`<table class="namedeptrole-table"><thead><tr><th>部署</th><th>役職</th></tr></thead><tbody>${pairs.map(row=>{
   const dept=esc(row&&row.department!=null?row.department:row&&row[0]!=null?row[0]:'');
   const role=esc(row&&row.role!=null?row.role:row&&row[1]!=null?row[1]:'');
   return `<tr><td>${dept}</td><td>${role}</td></tr>`;
  }).join('')}</tbody></table>`;
 }else{
  if(depts.length){
   body+=`<h4>部署</h4><ul class="namedeptrole-list">${depts.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`;
  }
  if(roles.length){
   body+=`<h4>役職</h4><ul class="namedeptrole-list">${roles.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`;
  }
 }
 return{
  html:`<section class="namedeptrole"><h3>部署・役職</h3>${body}</section>`,
  hideInlineDepartments:true,
 };
}
function paintNameDetail(item){
 const colors=nameSwatches(item.colors,item.fixedColor,item.mixColors);
 const mixNoteFor=value=>nameMixNote(value,item.mixColors);
 const samples=(item.samples||[]).map(sample=>`<figure class="namesample"><p class="namesize-heading">${esc(nameSizeHeading(item))}</p>${sample.hasImage?`<button type="button" class="namezoom-trigger" data-namezoom="${sample.id}" aria-label="${esc(item.format)}の見本を拡大">${nameScalePicture(item,nameSampleUrl(sample.id),sample.paperColor||item.fixedColor)}<span class="namezoom-hint">クリックして拡大</span></button>`:nameScalePicture(item,null)}<figcaption>${sample.condition?esc(sample.condition)+'　':''}${esc(sample.paperColor)?`この条件の用紙色：${esc(sample.paperColor)}`:''}<br>${esc(sample.caption)}${mixNoteFor(sample.paperColor||item.fixedColor)?`<br>${esc(mixNoteFor(sample.paperColor||item.fixedColor))}`:''}</figcaption>${currentUser?.role==='owner'?`<button type="button" class="danger namesample-delete" data-delete-sample="${sample.id}">この画像を削除</button>`:''}</figure>`).join('');
 const reasons=(item.reviewReasons||[]).filter(text=>!/書体|代替表示/.test(text)).map(text=>`<li>${esc(text)}</li>`).join('');
 const deptRole=paintNameDeptRoleSection(item);
 const departments=deptRole.hideInlineDepartments?'':(item.departments||[]).map(esc).join('、');
 const mixCount=nameMixColors(item.mixColors).length;
 $('#nametitle').textContent=item.format||'ネーム形式';
 $('#namenumber').textContent=item.number||'';
 $('#namebody').innerHTML=`<p class="namewarn">画像の背景は用紙の色です。「先方から指定」の${mixCount}色表示は色の選択肢を示し、実物が多色という意味ではありません。文字とコードはプリンターが印字する内容です。氏名と番号は架空です。</p>
  <p class="namescale-note">大小を同じ縮尺で表示しています。実物大ではありません。寸法は画像の向きで表記しています。</p><div class="namesamples">${samples||'<p>見本画像はありません。</p>'}</div>
  ${deptRole.html}
  <dl class="namedef">
   <dt>元の顧客名</dt><dd>${esc(item.sourceName)||'—'}</dd>
   <dt>指示書デスクの顧客</dt><dd>${item.linkStatus==='対応'?esc(item.linkedName):'未対応'}</dd>
   <dt>形式名</dt><dd>${esc(item.format)||'—'}</dd>
   <dt>サイズ</dt><dd>${esc(item.size)||'—'}</dd>
   <dt>用紙の色と条件</dt><dd><div class="namecolors">${colors}</div>${item.colorCaption?`<p>条件の名前：${esc(item.colorCaption)}</p>`:''}${item.fixedColor?`<p>指定色：${esc(item.fixedColor)}</p>`:''}</dd>
   <dt>印字内容</dt><dd>${esc(item.layout)||'—'}${item.heading?`<br>見出し：${esc(item.heading).replaceAll('\n',' / ')}`:''}${item.reading?`<br>文字種：${esc(item.reading)}`:''}${item.numberLength?`<br>番号桁数：${item.numberLength}`:''}${item.maximumFont?`<br>氏名の最大：${item.maximumFont} pt`:''}${item.headingFont?`<br>見出し：${item.headingFont} pt`:''}</dd>
   <dt>書体</dt><dd>${esc(fontLabel(item.family))||'—'}${item.headerFamily?`<br>見出しの書体：${esc(fontLabel(item.headerFamily))}`:''}</dd>
   <dt>衣類・貼付け位置</dt><dd>${esc(item.placement)||'指定なし'}${departments?`<br>部署：${departments}`:''}</dd>
   <dt>注意・例外</dt><dd>${esc(item.note)||'なし'}${item.pending?`<br>未確定：${esc(item.pending)}`:''}</dd>
   <dt>照合用</dt><dd>元の形式名を登録番号にはしていません。元資料の行：${item.sourceRow??'—'}（照合用）<br>${esc(item.identity)}</dd>
  </dl>
  ${reasons?`<h3>要確認</h3><ul>${reasons}</ul>`:''}`;
 paintNameReview(item);
 $('#namebody').querySelectorAll('[data-namezoom]').forEach(button=>button.onclick=()=>{const sample=item.samples.find(x=>String(x.id)===button.dataset.namezoom);if(sample)openNameZoom(item,sample)});
 const edit=$('#nameeditbtn');
 if(edit){edit.hidden=currentUser?.role!=='owner';edit.onclick=()=>openNameEditor(item)}
 $('#namebody').querySelectorAll('[data-delete-sample]').forEach(button=>button.onclick=()=>{const sample=item.samples.find(x=>String(x.id)===button.dataset.deleteSample);if(sample)confirmDeleteSample(item,sample)});
 const delFmt=$('#namedeleteformat');
 if(delFmt){delFmt.hidden=currentUser?.role!=='owner';delFmt.onclick=()=>confirmDeleteFormat(item)}
}
async function openNameFormat(id){
 const item=await api('/api/name-labels/formats/'+id);
 paintNameDetail(item);
 if(!$('#namedetail').open)$('#namedetail').showModal();
}
async function confirmDeleteCustomer(data,childCount){
 if(currentUser?.role!=='owner')return;
 const n=Number(childCount)||0;
 const msg=`「${data.sourceName}」を削除しますか？\n\n子情報（形式）${n}件もまとめて一覧から非表示になります。\n見本画像ファイルは削除しません。後から「削除済み」から復元できます。\n\nキャンセルなら何も変わりません。`;
 if(!confirm(msg))return;
 try{
  await api('/api/name-labels/delete',{kind:'customer',id:data.id});
  $('#nameformats').innerHTML='<p class="nameempty">左の顧客を選ぶと、形式がここに出ます。</p>';
  await loadNames(false);
  toast('顧客を削除しました（復元できます）。');
 }catch(e){toast(e.message||'削除できませんでした。')}
}
async function confirmDeleteFormat(item){
 if(currentUser?.role!=='owner')return;
 const msg=`「${item.format}」（顧客：${item.sourceName}）を削除しますか？\n\nこの形式と見本表示だけが一覧から消えます。顧客や他の形式は残ります。\n見本画像ファイルは削除しません。後から「削除済み」から復元できます。\n\nキャンセルなら何も変わりません。`;
 if(!confirm(msg))return;
 try{
  await api('/api/name-labels/delete',{kind:'format',id:item.id});
  if($('#namedetail').open)$('#namedetail').close();
  if(item.customerId)await openNameCustomer(item.customerId);
  await loadNames(false);
  toast('形式を削除しました（復元できます）。');
 }catch(e){toast(e.message||'削除できませんでした。')}
}
function nameSampleLabel(sample){
 const parts=[sample.condition||'',sample.paperColor?`用紙色：${sample.paperColor}`:''].filter(Boolean);
 return parts.join('　')||sample.caption||`見本 ${sample.id}`;
}
async function confirmDeleteSample(item,sample){
 if(currentUser?.role!=='owner')return;
 if((item.samples||[]).length<=1){toast('この形式に残っている最後の見本のため削除できません。不要な場合は「この形式を削除」を使ってください。');return}
 const msg=`「${item.format}」（顧客：${item.sourceName}）の見本「${nameSampleLabel(sample)}」を削除しますか？\n\nこの画像1件だけが表示から消えます。形式・顧客・他の見本は残ります。\n画像ファイルは削除しません。後から「削除済み」から復元できます。\n内容が変わるため、了承済みの場合は再確認が必要になります。\n\nキャンセルなら何も変わりません。`;
 if(!confirm(msg))return;
 try{
  await api('/api/name-labels/delete',{kind:'sample',id:sample.id});
  await openNameFormat(item.id);
  if(item.customerId)await openNameCustomer(item.customerId);
  toast('見本画像を削除しました（復元できます）。');
 }catch(e){toast(e.message||'削除できませんでした。')}
}
async function openNameDeleted(){
 if(currentUser?.role!=='owner')return;
 const box=$('#namedeletedbody');
 box.innerHTML='<p>読み込んでいます。</p>';
 if(!$('#namedeleted').open)$('#namedeleted').showModal();
 try{
  const data=await api('/api/name-labels/deleted');
  const customers=(data.customers||[]).map(item=>`<div class="namedeleted-row"><div><b>${esc(item.sourceName)}</b><span>大元（顧客）／削除 ${esc(item.deletedAt||'—')}／${esc(item.deletedByName||'—')}</span><span>一緒に隠した子情報：${item.hiddenWithParent||0}件${item.individuallyDeletedFormats?`（別途個別削除：${item.individuallyDeletedFormats}件）`:''}</span></div><button type="button" data-restore-customer="${item.id}">復元</button></div>`).join('');
  const formats=(data.formats||[]).map(item=>`<div class="namedeleted-row"><div><b>${esc(item.format)}</b><span>子情報（形式）${esc(item.number||'')}／顧客：${esc(item.sourceName)}${item.parentDeleted?'（顧客も削除中）':''}</span><span>削除 ${esc(item.deletedAt||'—')}／${esc(item.deletedByName||'—')}</span></div><button type="button" data-restore-format="${item.id}">復元</button></div>`).join('');
  const samples=(data.samples||[]).map(item=>`<div class="namedeleted-row"><div><b>${esc(item.label)}</b><span>見本画像／形式：${esc(item.format)} ${esc(item.number||'')}／顧客：${esc(item.sourceName)}${item.parentDeleted?'（形式または顧客も削除中）':''}</span><span>削除 ${esc(item.deletedAt||'—')}／${esc(item.deletedByName||'—')}</span></div><button type="button" data-restore-sample="${item.id}">復元</button></div>`).join('');
  box.innerHTML=(customers||formats||samples)?`<h3>削除済みの大元（顧客）</h3>${customers||'<p>ありません。</p>'}<h3>個別に削除した子情報（形式）</h3>${formats||'<p>ありません。</p>'}<h3>個別に削除した見本画像</h3>${samples||'<p>ありません。</p>'}`:'<p>削除済みはありません。</p>';
  box.querySelectorAll('[data-restore-sample]').forEach(button=>button.onclick=async()=>{
   if(!confirm('この見本画像を復元しますか？'))return;
   try{
    const result=await api('/api/name-labels/restore',{kind:'sample',id:Number(button.dataset.restoreSample)});
    toast(result.parentDeleted?'見本画像を復元しました（形式または顧客が削除中のため、その復元後に表示されます）。':'見本画像を復元しました。');
    await openNameDeleted();
    await loadNames(false);
   }catch(e){toast(e.message||'復元できませんでした。')}
  });
  box.querySelectorAll('[data-restore-customer]').forEach(button=>button.onclick=async()=>{
   if(!confirm('この顧客と、一緒に削除した子情報を復元しますか？\n（以前に個別削除した子情報は戻りません）'))return;
   try{
    await api('/api/name-labels/restore',{kind:'customer',id:Number(button.dataset.restoreCustomer)});
    toast('顧客を復元しました。');
    await openNameDeleted();
    await loadNames(false);
   }catch(e){toast(e.message||'復元できませんでした。')}
  });
  box.querySelectorAll('[data-restore-format]').forEach(button=>button.onclick=async()=>{
   if(!confirm('この形式を復元しますか？'))return;
   try{
    const result=await api('/api/name-labels/restore',{kind:'format',id:Number(button.dataset.restoreFormat)});
    toast(result.parentDeleted?'形式を復元しました（顧客が削除中のため、顧客の復元後に表示されます）。':'形式を復元しました。');
    await openNameDeleted();
    await loadNames(false);
    if(result.customerId&&!result.parentDeleted)await openNameCustomer(result.customerId);
   }catch(e){toast(e.message||'復元できませんでした。')}
  });
 }catch(e){box.innerHTML=`<p>${esc(e.message||'読み込めませんでした。')}</p>`}
}
function syncNameDeletedButton(){
 const button=$('#namedeletedopen');
 if(button)button.hidden=currentUser?.role!=='owner';
}
$('#namesearch').onsubmit=e=>{e.preventDefault();loadNames(true)};
$('#nameclose').onclick=()=>$('#namedetail').close();
$('#namedetail').addEventListener('cancel',e=>{e.preventDefault();$('#namedetail').close()});
const nameDeletedOpen=$('#namedeletedopen');
 if(nameDeletedOpen)nameDeletedOpen.onclick=()=>openNameDeleted();
const nameDeletedClose=$('#namedeletedclose');
 if(nameDeletedClose)nameDeletedClose.onclick=()=>$('#namedeleted').close();
const nameDeletedDialog=$('#namedeleted');
 if(nameDeletedDialog)nameDeletedDialog.addEventListener('cancel',e=>{e.preventDefault();nameDeletedDialog.close()});
function nameExeSize(n){return n>=1048576?`${(n/1048576).toFixed(1)} MB`:`${Math.max(1,Math.round(n/1024))} KB`}
let nameExeMax=200*1024*1024;
async function openNameExe(){
 const info=$('#nameexeinfo');
 info.innerHTML='<p>読み込み中…</p>';
 $('#nameexestatus').textContent='';
 if(!$('#nameexe').open)$('#nameexe').showModal();
 try{
  const data=await api('/api/name-labels/exe/info');
  nameExeMax=data.maxBytes||nameExeMax;
  const exe=data.exe;
  info.innerHTML=exe?`<dl class="namedef"><dt>ファイル名</dt><dd>${esc(exe.filename)}</dd><dt>サイズ</dt><dd>${esc(nameExeSize(exe.size))}</dd><dt>アップロード日時</dt><dd>${esc(exe.uploadedAt)}（日本時間）${exe.uploadedBy?`／${esc(exe.uploadedBy)}`:''}</dd></dl>`:'<p>まだアップロードされていません。</p>';
  $('#nameexedownload').hidden=!exe;
  $('#nameexeupload').hidden=!data.canUpload;
  const drop=data.dropFolder;
  $('#nameexedrop').innerHTML=drop?(drop.enabled?`置き場フォルダ：<code>${esc(drop.path)}</code><br>ここにexeを置くと、約${esc(String(drop.intervalSeconds*2))}秒〜1分で自動的に差し替わります（置いたファイルはそのまま残ります）。`:`置き場フォルダからの自動取込は無効です（${esc(drop.path)}）。`):'';
 }catch(e){info.innerHTML=`<p>${esc(e.message||'読み込めませんでした。')}</p>`}
}
async function sendNameExe(){
 const file=$('#nameexefile').files[0];
 const status=$('#nameexestatus');
 if(!file){status.textContent='exeファイルを選んでください。';return}
 if(!/\.exe$/i.test(file.name)){status.textContent='拡張子が .exe のファイルを選んでください。';return}
 if(file.size>nameExeMax){status.textContent=`ファイルが大きすぎます（上限 ${Math.round(nameExeMax/1048576)}MB）。`;return}
 if(!confirm(`「${file.name}」（${nameExeSize(file.size)}）をアップロードしますか？\n今の配布ファイルは置き換わります。`))return;
 status.textContent='アップロード中…';$('#nameexesend').disabled=true;
 try{
  const r=await fetch('/api/name-labels/exe',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Desk-Request':'1','X-File-Name':encodeURIComponent(file.name)},body:file});
  const d=await r.json().catch(()=>({}));
  if(!r.ok)throw Error(d.error||'アップロードできませんでした。');
  $('#nameexefile').value='';
  await openNameExe();
  status.textContent='アップロードしました。';
 }catch(e){status.textContent=e.message||'アップロードできませんでした。'}
 finally{$('#nameexesend').disabled=false}
}
$('#nameexeopen').onclick=()=>openNameExe();
$('#nameexeclose').onclick=()=>$('#nameexe').close();
$('#nameexesend').onclick=()=>sendNameExe();
$('#nameexedownload').onclick=()=>{location.href='/api/name-labels/exe'};
// account.js initializes login state later; loadNames updates the button.
let nameEditing=null;
function syncNameSize(){
 const kind=$('#nameformsize').value;
 const locked=kind==='unknown'||kind==='blank';
 $('#nameformwidth').disabled=locked;
 $('#nameformheight').disabled=locked;
 if(kind==='small'&&!$('#nameformwidth').value){$('#nameformwidth').value='50';$('#nameformheight').value='24'}
 if(kind==='large'&&!$('#nameformwidth').value){$('#nameformwidth').value='80';$('#nameformheight').value='50'}
}
function openNameEditor(item,customer){
 if(currentUser?.role!=='owner')return;
 nameEditing=item?{id:item.id,customerId:item.customerId}:customer?{id:null,customerId:customer.id}:null;
 $('#nameformerror').textContent='';
 $('#nameformtitle').textContent=item?'ネーム情報を修正':'ネーム情報を登録';
 $('#nameformnumber').textContent=item?(item.number||''):'';
 $('#nameformcustomer').value=item?item.sourceName||'':customer?customer.sourceName||'':'';
 $('#nameformreading').value=item?item.customerReading||'':customer?customer.reading||'':'';
 $('#nameformformat').value=item?item.format||'':'';
 $('#nameformsize').value=item?item.sizeKind||'small':'small';
 $('#nameformwidth').value=item&&item.widthMm?item.widthMm:'';
 $('#nameformheight').value=item&&item.heightMm?item.heightMm:'';
 $('#nameformplacement').value=item?item.placement||'':'';
 $('#nameformheading').value=item?item.heading||'':'';
 $('#nameformcolors').value=item?item.colorText||'':'';
 $('#nameformcolorcaption').value=item?item.colorCaption||'':'';
 $('#nameformdepartments').value=item?item.departmentText||'':'';
 $('#nameformnote').value=item?item.note||'':'';
 $('#nameformpending').value=item?item.pending||'':'';
 $('#nameformimage').value='';
 $('#nameformfont').textContent=item&&item.family?`書体の記載：${fontLabel(item.family)}。HG丸ゴシックは太字です。`:'書体の記載：HG丸ゴシック（太字）';
 syncNameSize();
 if($('#namedetail').open)$('#namedetail').close();
 if(!$('#nameform').open)$('#nameform').showModal();
 $('#nameformcustomer').focus();
}
$('#nameformsize').onchange=syncNameSize;
$('#namenew').onclick=()=>openNameEditor(null,null);
$('#nameformclose').onclick=()=>$('#nameform').close();
$('#nameform').addEventListener('cancel',e=>{e.preventDefault();$('#nameform').close()});
$('#nameedit').onsubmit=async e=>{
 e.preventDefault();
 if(currentUser?.role!=='owner'||$('#nameformsave').disabled)return;
 $('#nameformerror').textContent='';
 const file=$('#nameformimage').files[0];
 if(file&&file.size>8*1024*1024){$('#nameformerror').textContent='見本画像は8MBまでです。';return}
 $('#nameformsave').disabled=true;
 try{
  const body={
   customerName:$('#nameformcustomer').value,
   reading:$('#nameformreading').value,
   formatName:$('#nameformformat').value,
   sizeKind:$('#nameformsize').value,
   widthMm:$('#nameformwidth').value,
   heightMm:$('#nameformheight').value,
   placement:$('#nameformplacement').value,
   heading:$('#nameformheading').value,
   colorText:$('#nameformcolors').value,
   colorCaption:$('#nameformcolorcaption').value,
   departmentText:$('#nameformdepartments').value,
   note:$('#nameformnote').value,
   pending:$('#nameformpending').value
  };
  if(nameEditing?.customerId&&!nameEditing.id)body.customerId=nameEditing.customerId;
  if(file){body.filename=file.name;body.data=await fileToBase64(file)}
  const saved=nameEditing?.id?await api('/api/name-labels/save',Object.assign({id:nameEditing.id},body)):await api('/api/name-labels',body);
  $('#nameform').close();
  await loadNames(false);
  if(saved.customerId)await openNameCustomer(saved.customerId);
  if(saved.id)await openNameFormat(saved.id);
  toast(nameEditing?.id?'ネーム情報を修正しました。':'ネーム情報を登録しました。');
 }catch(err){$('#nameformerror').textContent=err.message||'保存できませんでした。'}
 finally{$('#nameformsave').disabled=false}
};

function paintNameReview(item){
 const r=item.review||{};
 const owner=currentUser?.role==='owner';
 const showConfirm=!!r.canConfirm||(owner&&r.status!=='了承');
 if(!showConfirm&&!owner)return;
 const panel=document.createElement('section');panel.className='name-review-panel';
 panel.style.cssText='border:1px solid #9dceae;border-radius:8px;padding:16px;margin:12px 0;background:#f4faf4';
 panel.innerHTML=`${r.canConfirm?'<button type="button" class="primary" id="nameconfirm">内容を確認して了承する</button>':owner&&r.status!=='了承'?'<button type="button" class="primary" id="nameconfirm" disabled title="了承は指定された担当者本人だけが行えます">内容を確認して了承する</button>':''}<p id="namereviewmessage" role="status"></p>${owner?`<label>確認担当者<select id="namereviewassignee"><option value="">未設定</option>${(item.reviewUsers||[]).map(u=>`<option value="${u.id}" ${u.id===r.assigneeId?'selected':''}>${esc(u.name)}</option>`).join('')}</select></label><button type="button" id="nameassign">担当者を保存</button>`:''}`;
 $('#namebody').prepend(panel);
 const submit=async(button,payload)=>{button.disabled=true;try{await api('/api/name-labels/review',{id:item.id,...payload});await openNameFormat(item.id);await openNameCustomer(item.customerId);const list=await api('/api/name-labels?q='+encodeURIComponent($('#namecustomer').value.trim()));paintNameCustomers(list)}catch(e){$('#namereviewmessage').textContent=e.message;button.disabled=false}};
 const confirmButton=$('#nameconfirm');if(confirmButton&&r.canConfirm)confirmButton.onclick=()=>{if(confirm('この形式の内容を確認し、了承として記録しますか？'))submit(confirmButton,{digest:r.digest})};
 const assignButton=$('#nameassign');if(assignButton)assignButton.onclick=()=>submit(assignButton,{action:'assign',assigneeId:$('#namereviewassignee').value});
}
