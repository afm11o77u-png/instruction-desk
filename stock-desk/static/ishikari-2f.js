// 石狩倉庫2F 棚番号：配置図と棚番号一覧を1つのデータから描く。編集モードで図と一覧を直して保存する。
'use strict';
const SIDES=[['t','top'],['r','right'],['b','bottom'],['l','left']];
function cellStyle(b){
  let s=`grid-area:${b.r}/${b.c}/span ${b.rs}/span ${b.cs};font-size:${Number(b.size).toFixed(1)}px;background:${b.bg};color:${b.fg};font-weight:700`;
  for(const [k,n] of SIDES)if((b.border||'').includes(k))s+=`;border-${n}:1px solid #6D8479`;
  return s;
}
const nameKey=v=>String(v??'').normalize('NFKC').replace(/\s+/g,'').toLowerCase();
const searchKey=v=>String(v??'').trim().normalize('NFKC').toLowerCase();
if(typeof module!=='undefined')module.exports={cellStyle,nameKey};
if(typeof document!=='undefined')(function(){
const $=s=>document.querySelector(s);
const map=$('#map'),tbody=$('#items'),search=$('#search'),count=$('#count'),editBtn=$('#edit-toggle'),editor=$('#block-editor'),bar=$('#editbar'),status=$('#edit-status'),loadNote=$('#load-note');
let saved=null,data=null,version=0,canEdit=false,editing=false,dirty=false,selected=null,busy=false;
const clone=v=>JSON.parse(JSON.stringify(v));
const newId=p=>p+(crypto.randomUUID?crypto.randomUUID().slice(0,8):Date.now().toString(36)+Math.random().toString(36).slice(2,6));
function el(tag,attrs={},...kids){const e=document.createElement(tag);for(const [k,v] of Object.entries(attrs)){if(k==='text')e.textContent=v;else if(k==='class')e.className=v;else if(k.startsWith('on'))e[k]=v;else e.setAttribute(k,v)}e.append(...kids);return e}
function markDirty(){dirty=true;paintBar()}
function paintBar(){bar.hidden=!editing;status.textContent=dirty?'未保存の変更があります。「保存」で確定します。':'変更はありません。';$('#edit-save').disabled=busy||!dirty;$('#edit-cancel').disabled=busy}
// ---- 配置図 ----
function renderMap(){
  map.style.gridTemplateColumns=data.columns.map(w=>w+'px').join(' ');
  map.style.gridTemplateRows=data.rows.map(h=>h+'px').join(' ');
  map.replaceChildren(...data.blocks.map(b=>{
    const d=el('div',{class:'cell'+(b.vertical?' vertical':'')+(editing&&selected===b.id?' selected':''),'data-id':b.id},el('span',{text:b.text}));
    d.style.cssText=cellStyle(b);
    if(editing&&selected===b.id)d.append(el('i',{class:'resize','aria-hidden':'true'}));
    d.title=b.text;
    return d;
  }));
  fitAll();
  drawRoute();
}
// 文字が枠に入りきらないときは折り返し、それでも入らなければ文字を小さくして全体を見せる（表示だけ。保存する大きさは変えない）。
const MIN_FIT=6;
function fitCell(d){
  const span=d.querySelector('span'),b=block(d.dataset.id);if(!span||!b)return;
  const max=Number(b.size)||12,w=d.clientWidth-4,h=d.clientHeight-4;if(w<=0||h<=0)return;
  span.style.fontSize='';span.style.width='';span.style.whiteSpace='';
  const vertical=d.classList.contains('vertical');
  // 縦書きは回転前の幅＝枠の高さ、回転前の高さ＝枠の幅
  const room=vertical?[h,w]:[w,h];
  if(vertical&&span.offsetWidth>room[0]){span.style.width=room[0]+'px';span.style.whiteSpace='pre-line'}
  const fits=()=>(vertical?span.offsetWidth:span.scrollWidth)<=room[0]+0.5&&span.offsetHeight<=room[1]+0.5;
  if(fits())return;
  let lo=MIN_FIT,hi=max;
  for(let i=0;i<8;i++){const mid=(lo+hi)/2;span.style.fontSize=mid+'px';if(fits())lo=mid;else hi=mid}
  span.style.fontSize=Math.floor(lo*10)/10+'px';
}
function fitAll(){for(const d of map.children)fitCell(d)}
if(document.fonts&&document.fonts.ready)document.fonts.ready.then(()=>{if(data)fitAll()});
// 非表示で読み込まれたときや画面の大きさが変わったときも合わせ直す
let fitQueued=false;if(window.ResizeObserver)new ResizeObserver(()=>{if(fitQueued||!data)return;fitQueued=true;requestAnimationFrame(()=>{fitQueued=false;fitAll()})}).observe(map.parentElement);
function block(id){return data.blocks.find(b=>b.id===id)}
function starts(sizes){const out=[12];for(const s of sizes)out.push(out[out.length-1]+s);return out}
function trackAt(st,x){for(let i=0;i<st.length-1;i++)if(x<st[i+1])return i+1;return st.length-1}
let drag=null;
map.addEventListener('pointerdown',e=>{
  if(!editing||e.button!==0)return;
  const cell=e.target.closest('.cell');
  if(routeMode){if(cell){e.preventDefault();routeClick(cell)}return}
  if(!cell){selected=null;renderMap();renderEditor();return}
  const b=block(cell.dataset.id);if(!b)return;
  e.preventDefault();
  map.parentElement.focus({preventScroll:true}); // 矢印キー・Deleteを図で受ける
  if(selected!==b.id){selected=b.id;renderMap();renderEditor()}
  const target=map.querySelector(`[data-id="${CSS.escape(b.id)}"]`);
  drag={b,mode:e.target.classList.contains('resize')?'resize':'move',x:e.clientX,y:e.clientY,orig:{r:b.r,c:b.c,rs:b.rs,cs:b.cs},moved:false,target};
  target.setPointerCapture?.(e.pointerId);
});
map.addEventListener('pointermove',e=>{
  if(!drag)return;
  const dx=e.clientX-drag.x,dy=e.clientY-drag.y;
  if(!drag.moved&&Math.abs(dx)+Math.abs(dy)<4)return;
  drag.moved=true;
  const cs=starts(data.columns),rs=starts(data.rows),o=drag.orig,b=drag.b,nc=data.columns.length,nr=data.rows.length;
  if(drag.mode==='move'){
    b.c=Math.min(Math.max(1,trackAt(cs,cs[o.c-1]+dx+2)),nc-o.cs+1);
    b.r=Math.min(Math.max(1,trackAt(rs,rs[o.r-1]+dy+2)),nr-o.rs+1);
  }else{
    b.cs=Math.max(1,Math.min(trackAt(cs,cs[o.c-1+o.cs]+dx-2)-o.c+1,nc-o.c+1));
    b.rs=Math.max(1,Math.min(trackAt(rs,rs[o.r-1+o.rs]+dy-2)-o.r+1,nr-o.r+1));
  }
  drag.target.style.gridArea=`${b.r}/${b.c}/span ${b.rs}/span ${b.cs}`;
});
function endDrag(){if(!drag)return;const d=drag;drag=null;if(d.moved)fitCell(d.target);if(d.moved&&(d.b.r!==d.orig.r||d.b.c!==d.orig.c||d.b.rs!==d.orig.rs||d.b.cs!==d.orig.cs)){markDirty();renderEditor()}}
map.addEventListener('pointerup',endDrag);map.addEventListener('pointercancel',endDrag);
document.addEventListener('keydown',e=>{
  if(!editing||!selected||e.target.closest('input,textarea,select,button'))return;
  const b=block(selected);if(!b)return;
  const nc=data.columns.length,nr=data.rows.length,mv={ArrowLeft:[0,-1],ArrowRight:[0,1],ArrowUp:[-1,0],ArrowDown:[1,0]}[e.key];
  if(mv){e.preventDefault();b.r=Math.min(Math.max(1,b.r+mv[0]),nr-b.rs+1);b.c=Math.min(Math.max(1,b.c+mv[1]),nc-b.cs+1);markDirty();renderMap();renderEditor()}
  else if(e.key==='Delete'){e.preventDefault();removeBlock()}
});
// ---- ブロックの編集欄（配置図の下） ----
function linkedItems(b){const k=nameKey(b.text);return k?data.items.filter(it=>nameKey(it.name)===k):[]}
function field(label,input){return el('label',{},label,input)}
function renderEditor(){
  editor.hidden=!editing;
  if(!editing)return;
  const b=selected&&block(selected),box=$('#block-fields');
  $('#block-delete').disabled=$('#block-copy').disabled=!b;
  if(!b){box.replaceChildren(el('p',{class:'hint',text:'図のブロックをクリックすると、ここで名称・棚番号・枝番号などを直せます。ドラッグで移動、右下の角をドラッグで大きさを変えられます（矢印キーで1マス移動、Deleteで削除）。'}));return}
  const nc=data.columns.length,nr=data.rows.length;
  const text=el('textarea',{rows:'2',maxlength:'200'});text.value=b.text;
  text.onchange=()=>{const old=b.text;b.text=text.value;if($('#link-names').checked&&nameKey(old))for(const it of data.items)if(nameKey(it.name)===nameKey(old))it.name=b.text.replace(/\s*\n\s*/g,'');markDirty();renderMap();renderItems();renderEditor()};
  const num=(key,label,max)=>{const i=el('input',{type:'number',min:'1',max:String(max),step:'1'});i.value=b[key];i.onchange=()=>{const v=Math.round(Number(i.value));const lim={r:nr-b.rs+1,c:nc-b.cs+1,rs:nr-b.r+1,cs:nc-b.c+1}[key];if(!(v>=1&&v<=lim)){i.value=b[key];return}b[key]=v;markDirty();renderMap();renderEditor()};return field(label,i)};
  const size=el('input',{type:'number',min:'6',max:'40',step:'0.1'});size.value=b.size;size.onchange=()=>{const v=Number(size.value);if(!(v>=6&&v<=40)){size.value=b.size;return}b.size=Math.round(v*10)/10;markDirty();renderMap()};
  const color=(key,label)=>{const i=el('input',{type:'color'});i.value=b[key];i.onchange=()=>{b[key]=i.value.toUpperCase();markDirty();renderMap()};return field(label,i)};
  const check=(label,get,set)=>{const i=el('input',{type:'checkbox'});i.checked=get();i.onchange=()=>{set(i.checked);markDirty();renderMap()};return el('label',{class:'check'},i,label)};
  const rows=linkedItems(b);
  const linked=rows.length?el('table',{class:'linked'},el('thead',{},el('tr',{},...['名称','棚番号','名称別の枝番号','棚全体の枝番号'].map(t=>el('th',{text:t})))),el('tbody',{},...rows.map(it=>el('tr',{},el('td',{text:it.name}),...['shelf','branch','all_branch'].map(f=>{const i=el('input',{type:'text',maxlength:'200','aria-label':f});i.value=it[f];i.onchange=()=>{it[f]=i.value.trim();markDirty();renderItems()};return el('td',{},i)})))))
    :el('p',{class:'hint',text:'棚番号一覧に同じ名称の行はありません。一覧の「＋ 行を追加」で追加できます。'});
  box.replaceChildren(
    el('div',{class:'fieldrow'},field('文字',text),num('r','行',nr),num('c','列',nc),num('rs','高さ（マス）',nr),num('cs','幅（マス）',nc),field('文字の大きさ(px)',size),color('bg','背景色'),color('fg','文字色'),
      check('枠線',()=>!!b.border,v=>b.border=v?'trbl':''),check('縦書き',()=>b.vertical,v=>b.vertical=v)),
    ...(overlapWith(b)?[el('p',{class:'hint warn',text:`「${overlapWith(b).text}」と重なっています。重なったままでは保存できません。`})]:[]),
    el('h3',{text:'棚番号一覧の同じ名称の行'}),linked);
}
function overlapWith(b){return data.blocks.find(o=>o!==b&&o.r<b.r+b.rs&&b.r<o.r+o.rs&&o.c<b.c+b.cs&&b.c<o.c+o.cs)}
// 希望の位置から近い順に、ほかのブロックと重ならない場所を探す。
function freeSpot(b){const nc=data.columns.length,nr=data.rows.length,cand=[];for(let r=1;r<=nr-b.rs+1;r++)for(let c=1;c<=nc-b.cs+1;c++)cand.push([r,c,Math.abs(r-b.r)*3+Math.abs(c-b.c)]);cand.sort((x,y)=>x[2]-y[2]);for(const [r,c] of cand)if(!data.blocks.some(o=>o.r<r+b.rs&&r<o.r+o.rs&&o.c<c+b.cs&&c<o.c+o.cs))return [r,c];return null}
function addBlock(){
  const src=selected&&block(selected),nc=data.columns.length,nr=data.rows.length;
  const b=src?{...clone(src),id:newId('b')}:{id:newId('b'),r:1,c:1,rs:2,cs:4,text:'新しいブロック',size:13.3,bg:'#DCECDF',fg:'#173A30',border:'trbl',vertical:false};
  if(src){b.c=Math.min(src.c+src.cs,nc-b.cs+1);b.r=Math.min(src.r,nr-b.rs+1)}
  else{const sc=map.parentElement,cs=starts(data.columns),rs=starts(data.rows);b.c=Math.min(trackAt(cs,sc.scrollLeft+sc.clientWidth/2),nc-b.cs+1);b.r=Math.min(trackAt(rs,sc.scrollTop+Math.min(sc.clientHeight,rs[rs.length-1])/2),nr-b.rs+1)}
  const free=freeSpot(b);if(free){b.r=free[0];b.c=free[1]}
  data.blocks.push(b);selected=b.id;markDirty();renderMap();renderEditor();
  map.querySelector(`[data-id="${CSS.escape(b.id)}"]`)?.scrollIntoView({block:'nearest',inline:'nearest'});
}
function removeBlock(){const b=selected&&block(selected);if(!b)return;if(!confirm(`「${b.text||'（文字なし）'}」のブロックを削除しますか？（保存するまで確定しません）`))return;data.blocks=data.blocks.filter(x=>x!==b);if(Array.isArray(data.route))data.route=data.route.filter(id=>id!==b.id);selected=null;markDirty();renderMap();renderEditor();renderRoute()}
$('#block-add').onclick=()=>{selected=null;addBlock()};
$('#block-copy').onclick=addBlock;
$('#block-delete').onclick=removeBlock;
// 重なったブロックは保存しない（Excelの結合セルにできないため）。
function overlap(){const taken=new Map();for(const b of data.blocks)for(let r=b.r;r<b.r+b.rs;r++)for(let c=b.c;c<b.c+b.cs;c++){const k=r*1000+c,o=taken.get(k);if(o&&o!==b)return [o,b];taken.set(k,b)}return null}
// ---- ルート（通る順。ピッキングリストの石狩倉庫の明細をこの順に並べる） ----
let routeMode=false,routeNote='';
const routeShow=$('#route-show');
try{routeShow.checked=localStorage.getItem('ishikari-route-show')!=='0'}catch{}
routeShow.onchange=()=>{try{localStorage.setItem('ishikari-route-show',routeShow.checked?'1':'0')}catch{}drawRoute()};
const SVGNS='http://www.w3.org/2000/svg';
function svg(tag,attrs){const e=document.createElementNS(SVGNS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e}
function routeIds(){return (data&&Array.isArray(data.route)?data.route:[]).filter(id=>block(id))}
function blockText(b){return String(b&&b.text||'').replace(/\s*\n\s*/g,'')||'（文字なし）'}
function drawRoute(){
  const layer=$('#route-layer');if(!data||!layer)return;
  const cs=starts(data.columns),rs=starts(data.rows),W=cs[cs.length-1]+12,H=rs[rs.length-1]+12;
  layer.setAttribute('width',W);layer.setAttribute('height',H);layer.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const ids=routeIds();
  if(!ids.length||!(routeShow.checked||routeMode)){layer.replaceChildren();return}
  const pts=ids.map(id=>{const b=block(id);return [(cs[b.c-1]+cs[b.c-1+b.cs])/2,(rs[b.r-1]+rs[b.r-1+b.rs])/2]});
  const defs=svg('defs',{}),mk=svg('marker',{id:'route-arrow',viewBox:'0 0 10 10',refX:'9',refY:'5',markerWidth:'6',markerHeight:'6',orient:'auto'});
  mk.append(svg('path',{d:'M0,0L10,5L0,10z',fill:'#d0342c'}));defs.append(mk);
  const kids=[defs],R=11;
  for(let i=0;i<pts.length-1;i++){const [x1,y1]=pts[i],[x2,y2]=pts[i+1],dx=x2-x1,dy=y2-y1,len=Math.hypot(dx,dy);if(len<R*2+4)continue;const ux=dx/len,uy=dy/len;
    kids.push(svg('line',{x1:x1+ux*R,y1:y1+uy*R,x2:x2-ux*(R+2),y2:y2-uy*(R+2),stroke:'#d0342c','stroke-width':'2.5','stroke-opacity':'.85','marker-end':'url(#route-arrow)'}))}
  pts.forEach(([x,y],i)=>{const g=svg('g',{class:'route-step'});g.append(svg('circle',{cx:x,cy:y,r:R,fill:'#d0342c',stroke:'#fff','stroke-width':'2'}));
    const t=svg('text',{x,y:y+0.5,'text-anchor':'middle','dominant-baseline':'central','font-size':i>=99?'9':'11','font-weight':'700',fill:'#fff'});t.textContent=String(i+1);g.append(t);kids.push(g)});
  layer.replaceChildren(...kids);
}
function setRoute(ids,note){data.route=ids;routeNote=note||'';markDirty();renderRoute()}
function renderRoute(){
  if(!data)return;
  const ids=routeIds();
  $('#route-summary').textContent=ids.length?`ルート ${ids.length}か所（ピッキングリストの石狩倉庫の明細をこの順に並べます）`:(canEdit?'ルートは未入力です。編集モードの「ルート入力」で入力できます。':'ルートは未入力です。');
  $('#route-editor').hidden=!editing;
  document.body.classList.toggle('route-input',editing&&routeMode);
  const btn=$('#route-mode');btn.textContent=routeMode?'ルート入力を終える':'ルート入力を始める';btn.classList.toggle('primary',routeMode);
  $('#route-clear').disabled=!ids.length;
  $('#route-msg').textContent=routeNote||(routeMode?'図のブロックを通る順にクリックしてください。クリックした順に①②③…と矢印でつながります。':'「ルート入力を始める」を押してから、図のブロックを通る順にクリックします。順番は下の一覧の↑↓で入れ替え、×で外せます。');
  const list=$('#route-list');
  list.replaceChildren(...(editing?ids:[]).map((id,i)=>{
    const mv=(d)=>{const r=ids.slice();[r[i],r[i+d]]=[r[i+d],r[i]];setRoute(r)};
    return el('li',{},el('b',{text:`${i+1}.`}),el('span',{text:blockText(block(id))}),
      el('button',{type:'button',text:'↑',title:'前へ','aria-label':`${i+1}番目を前へ`,onclick:()=>mv(-1),...(i===0?{disabled:''}:{})}),
      el('button',{type:'button',text:'↓',title:'後ろへ','aria-label':`${i+1}番目を後ろへ`,onclick:()=>mv(1),...(i===ids.length-1?{disabled:''}:{})}),
      el('button',{type:'button',text:'×',title:'ルートから外す','aria-label':`${i+1}番目をルートから外す`,onclick:()=>setRoute(ids.filter(x=>x!==id),`「${blockText(block(id))}」をルートから外しました。`)}));
  }));
  drawRoute();
}
function routeClick(cell){
  const id=cell.dataset.id,ids=routeIds(),at=ids.indexOf(id);
  if(at>=0){routeNote=`「${blockText(block(id))}」はすでにルートの${at+1}番目です。外すときは下の一覧の×を押してください。`;renderRoute();return}
  setRoute([...ids,id],`${ids.length+1}番目：「${blockText(block(id))}」`);
  const list=$('#route-list');list.scrollTop=list.scrollHeight;
}
$('#route-mode').onclick=()=>{routeMode=!routeMode;routeNote='';if(routeMode&&selected){selected=null;renderMap();renderEditor()}renderRoute()};
$('#route-clear').onclick=()=>{if(!confirm('ルートを全部消しますか？（保存するまで確定しません）'))return;setRoute([],'ルートを全部消しました。')};
// ---- 棚番号一覧 ----
const FIELDS=['name','shelf','branch','all_branch','rack','note'];
function renderItems(){
  $('#items-head-op').hidden=!editing;$('#item-add').hidden=!editing;
  const focus=document.activeElement&&tbody.contains(document.activeElement)?[document.activeElement.closest('tr')?.dataset.id,document.activeElement.dataset.field]:null;
  // 編集欄の幅は、その列でいちばん長い文字に合わせる（全角1文字=1em、半角は約0.6em）
  const ems=v=>[...String(v??'')].reduce((n,ch)=>n+(/[\x20-\x7e\uff61-\uff9f]/.test(ch)?0.6:1),0);
  const colEm=Object.fromEntries(FIELDS.map(f=>[f,Math.min(16,Math.max(3,...data.items.map(it=>ems(it[f])))+0.5)]));
  tbody.replaceChildren(...data.items.map(it=>{
    const tr=el('tr',{'data-id':it.id});
    for(const f of FIELDS){
      if(!editing){tr.append(el('td',{text:it[f]}));continue}
      const i=el('input',{type:'text',maxlength:'200','data-field':f,'aria-label':f});i.value=it[f];i.style.width=colEm[f]+'em';i.title=it[f];
      i.onchange=()=>{const old=it[f];it[f]=i.value.trim();if(f==='name'){if(!it.name){alert('名称は空にできません。行ごと消すときは「削除」を押してください。');it.name=old;i.value=old;return}if($('#link-names').checked&&nameKey(old))for(const b of data.blocks)if(nameKey(b.text)===nameKey(old))b.text=it.name;renderMap();renderEditor()}markDirty();filter()};
      tr.append(el('td',{},i));
    }
    if(editing)tr.append(el('td',{},el('button',{type:'button',text:'削除',onclick:()=>{if(!confirm(`「${it.name}」の行を削除しますか？（保存するまで確定しません）`))return;data.items=data.items.filter(x=>x!==it);markDirty();renderItems();renderEditor()}})));
    return tr;
  }));
  if(focus)tbody.querySelector(`tr[data-id="${CSS.escape(focus[0]||'')}"] [data-field="${focus[1]}"]`)?.focus();
  filter();
}
function filter(){
  const term=searchKey(search.value);let shown=0;
  for(const tr of tbody.rows){
    const it=data.items.find(x=>x.id===tr.dataset.id);if(!it)continue;
    const rack=searchKey(it.shelf);
    tr.hidden=term!==''&&!(searchKey(it.name).includes(term)||rack===term||'【'+rack+'】'===term);
    if(!tr.hidden)shown++;
  }
  count.textContent=shown+'件';
}
search.addEventListener('input',filter);
$('#item-add').onclick=()=>{const it={id:newId('s'),name:'新しい名称',shelf:'',branch:'',all_branch:'',rack:'',note:''};data.items.push(it);search.value='';markDirty();renderItems();tbody.querySelector(`tr[data-id="${CSS.escape(it.id)}"] input`)?.select()};
// ---- 編集モード・保存・取消 ----
function setEditing(on){editing=on;document.body.classList.toggle('editing',on);editBtn.textContent=on?'編集モードを終える':'編集モード';if(!on){selected=null;routeMode=false;routeNote=''}renderMap();renderItems();renderEditor();renderRoute();paintBar()}
editBtn.onclick=()=>{if(editing&&dirty&&!confirm('保存していない変更があります。変更を捨てて編集モードを終えますか？'))return;if(editing&&dirty){data=clone(saved);dirty=false}setEditing(!editing)};
$('#edit-cancel').onclick=()=>{if(dirty&&!confirm('保存していない変更をすべて取り消して、保存済みの内容に戻しますか？'))return;data=clone(saved);dirty=false;selected=null;routeNote='';renderMap();renderItems();renderEditor();renderRoute();paintBar()};
$('#edit-save').onclick=async()=>{
  if(busy||!dirty)return;
  const hit=overlap();if(hit){selected=hit[1].id;renderMap();renderEditor();const m=`配置図の「${hit[0].text}」と「${hit[1].text}」が重なっています。位置か大きさを直してから保存してください。`;status.textContent=m;alert(m);return}
  busy=true;paintBar();status.textContent='保存しています…';
  try{
    const res=await fetch('api/ishikari-2f',{method:'POST',headers:{'Content-Type':'application/json','X-Stock-Request':'1'},body:JSON.stringify({version,data})});
    const body=await res.json().catch(()=>({}));
    if(!res.ok)throw Error(body.error||'保存できませんでした。');
    apply(body);dirty=false;busy=false;routeNote='';renderMap();renderItems();renderEditor();renderRoute();paintBar();status.textContent=`保存しました（版${version}）。`;
  }catch(err){busy=false;paintBar();status.textContent=err.message;alert(err.message)}
};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
function apply(body){saved=body.data;if(!Array.isArray(saved.route))saved.route=[];data=clone(saved);version=body.version;canEdit=!!body.can_edit;editBtn.hidden=!canEdit;loadNote.textContent=body.version?`最終保存：${String(body.updated_at||'').replace('T',' ')}${body.updated_by?' '+body.updated_by:''}（版${body.version}）`:''}
fetch('api/ishikari-2f',{headers:{'X-Stock-Request':'1'}}).then(async res=>{const body=await res.json().catch(()=>({}));if(!res.ok)throw Error(body.error||'棚番号を読み込めませんでした。');apply(body);renderMap();renderItems();renderRoute()}).catch(err=>{loadNote.textContent=err.message});
})();
