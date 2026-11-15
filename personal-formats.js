(() => {
 const page = $('#personalformats');
 const editor = $('#editor');
 const saveButton = $('#personalformatsave');
 const grid = $('#formatgrid');
 const preview = $('#formatpreview');
 const destination = $('#formatdestination');
 let library = {version: 0, entries: []};
 let folder = '', selectedId = '', previewId = '', changing = false;
 let dragId = '';
 const get = id => library.entries.find(entry => entry.id === id);
 const message = text => { $('#formatmessage').textContent = text; };
 let historyToken = '', returningToPage = false;
 const layoutControl = $('#formatlayout');
 const sizeControl = $('#formaticonsize');
 const sizes = {small:[100,94],medium:[140,124],large:[180,156]};
 const gap = 8;

 function showPage() {
  page.hidden = false;
  document.body.classList.add('personal-formats-page');
 }

 function closePage(restoreHistory = true) {
  if(page.hidden)return;
  page.hidden = true;
  document.body.classList.remove('personal-formats-page');
  closeContext();
  const mark = history.state?.personalFormats;
  if(restoreHistory && historyToken && mark?.token === historyToken){
   returningToPage = true;
   history.go(-mark.depth);
  }
  historyToken = '';
 }

 window.resetPersonalFormatsPage = () => closePage();


 function pushFormatLocation(depth) {
  const url = new URL(location.href);
  url.searchParams.set('formats','1');
  history.pushState({personalFormats:{token:historyToken,depth,folder,previewId}},'',url);
 }

 function rememberFormatLocation() {
  const mark = history.state?.personalFormats;
  if(mark?.token === historyToken)pushFormatLocation(mark.depth + 1);
 }

 window.addEventListener('popstate', event => {
  if(returningToPage){returningToPage = false;return}
  if(page.hidden || !historyToken)return;
  const mark = event.state?.personalFormats;
  if(mark?.token !== historyToken){
   historyToken = '';
   closePage(false);
   return;
  }
  folder = get(mark.folder)?.kind === 'folder' ? mark.folder : '';
  const entry = get(mark.previewId);
  selectedId = previewId = entry?.kind === 'format' && entry.parent === folder ? entry.id : '';
  paint();
 });
 const context = $('#formatcontext');
 const copyAction = $('#formatcopy');
 const pasteAction = $('#formatpaste');
 let copied = null, contextTarget = null, contextFocus = null;

 function closeContext(restoreFocus = false) {
  if(context.hidden)return;
  context.hidden = true;
  if(restoreFocus && contextFocus?.isConnected)contextFocus.focus();
  contextFocus = null;
 }

 page.addEventListener('contextmenu', event => {
  if(changing || !grid.contains(event.target))return;
  const tile = event.target.closest('.formaticon');
  const entry = tile && get(tile.dataset.id);
  event.preventDefault();
  closeContext();
  contextTarget = entry || null;
  contextFocus = tile || document.activeElement;
  copyAction.hidden = entry?.kind !== 'format';
  pasteAction.disabled = copied?.user !== currentUser;
  pasteAction.textContent = entry?.kind === 'folder' ? 'このフォルダに貼り付け' : '貼り付け';
  const bounds = page.getBoundingClientRect();
  const anchor = tile?.getBoundingClientRect() || bounds;
  context.hidden = false;
  context.style.left = Math.max(bounds.left,Math.min(event.clientX || anchor.left,bounds.right-context.offsetWidth))+'px';
  context.style.top = Math.max(bounds.top,Math.min(event.clientY || anchor.bottom,bounds.bottom-context.offsetHeight))+'px';
  if(!copyAction.hidden)copyAction.focus();
  else if(!pasteAction.disabled)pasteAction.focus();
 });
 page.addEventListener('pointerdown', event => {
  if(!context.contains(event.target))closeContext();
 });
 page.addEventListener('keydown', event => {
  if(event.key==='Escape' && !context.hidden){event.preventDefault();closeContext(true)}
 });

 copyAction.onclick = () => {
  if(contextTarget?.kind !== 'format' || !canManagePosting())return;
  copied = {user:currentUser, name:contextTarget.name, data:structuredClone(contextTarget.data)};
  closeContext(true);
  message('書式をコピーしました。貼り付け先で右クリックしてください。');
 };
 pasteAction.onclick = () => {
  if(changing || !canManagePosting() || copied?.user !== currentUser)return;
  const parent = contextTarget?.kind === 'folder' ? contextTarget.id : folder;
  const existing = new Set(library.entries.filter(item=>item.parent===parent).map(item=>item.name));
  let name = copied.name;
  for(let number = 1; existing.has(name); number++){
   const suffix = number===1 ? ' のコピー' : ' のコピー ('+number+')';
   name = Array.from(copied.name).slice(0,80-suffix.length).join('')+suffix;
  }
  closeContext(true);
  update([...library.entries,{id:crypto.randomUUID(),name,parent,kind:'format',data:copied.data}],'書式を貼り付けました。');
 };

 const labels = {
  customer:'顧客名', department:'部署', request:'受付内容', contract:'契約区分',
  barcodeColor:'バーコード色', nameColor:'ネームの色', nameRequired:'ネームの有無',
  method:'納品方法', methodNote:'納品方法の補足', packing:'荷姿', car:'号車',
  advance:'先出し', advanceCondition:'先出し条件', enclosures:'同梱物'
 };

 function folderPath(id) {
  const path = [];
  for(let entry = get(id); entry; entry = get(entry.parent))path.unshift(entry);
  return path;
 }

 function validDestination(entry, target) {
  return target !== entry.parent && (!target || get(target)?.kind === 'folder')
   && !folderPath(target).some(part => part.id === entry.id);
 }

 function bindDrop(button, target) {
  button.ondragover = event => {
   const entry = get(dragId);
   if(!changing && entry && validDestination(entry,target)) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
   }
  };
  button.ondrop = event => {
   const entry = get(dragId);
   if(!entry || !validDestination(entry,target))return;
   event.preventDefault();
   event.stopPropagation();
   const id = dragId;
   dragId = '';
   move(id,target);
  };
 }

 function openFolder(id, remember = true) {
  const moved = id !== folder || !!previewId;
  folder = id;
  selectedId = previewId = '';
  if(remember && moved)rememberFormatLocation();
  paint();
 }

 function currentRows() {
  return library.entries.filter(entry => entry.parent === folder).sort((a,b) =>
   a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name,'ja'));
 }

 function occupiedSlots(rows) {
  const slots = new Map(), used = new Set();
  for(const entry of rows){
   if(Number.isInteger(entry.slot) && !used.has(entry.slot)){
    slots.set(entry.id,entry.slot);
    used.add(entry.slot);
   }
  }
  let next = 0;
  for(const entry of rows){
   if(slots.has(entry.id))continue;
   while(used.has(next))next++;
   slots.set(entry.id,next);
   used.add(next);
  }
  return slots;
 }

 function layoutIcons() {
  const [width,height] = sizes[library.iconSize] || sizes.medium;
  grid.style.setProperty('--format-width',width+'px');
  grid.style.setProperty('--format-height',height+'px');
  const cols = Math.max(1,Math.floor((grid.clientWidth+gap)/(width+gap)));
  const slots = library.layout==='aligned' ? occupiedSlots(currentRows()) : null;
  for(const tile of grid.querySelectorAll('.formaticon')){
   const slot = slots?.get(tile.dataset.id);
   tile.style.gridColumn = slot == null ? '' : String(slot%cols+1);
   tile.style.gridRow = slot == null ? '' : String(Math.floor(slot/cols)+1);
  }
 }

 new ResizeObserver(()=>{if(!page.hidden)layoutIcons()}).observe(grid);
 grid.addEventListener('dragover', event => {
  if(!changing && dragId && library.layout==='aligned')event.preventDefault();
 });
 grid.addEventListener('drop', event => {
  const entry = get(dragId);
  if(!entry || changing || library.layout!=='aligned' || entry.parent!==folder)return;
  event.preventDefault();
  const [width,height] = sizes[library.iconSize] || sizes.medium;
  const bounds = grid.getBoundingClientRect();
  const cols = Math.max(1,Math.floor((grid.clientWidth+gap)/(width+gap)));
  const column = Math.min(cols-1,Math.max(0,Math.floor((event.clientX-bounds.left)/(width+gap))));
  const row = Math.max(0,Math.floor((event.clientY-bounds.top)/(height+gap)));
  const target = Math.min(599,row*cols+column);
  const slots = occupiedSlots(currentRows());
  const previous = slots.get(entry.id);
  if(previous===target)return;
  for(const [id,slot] of slots)if(slot===target)slots.set(id,previous);
  slots.set(entry.id,target);
  update(library.entries.map(item => slots.has(item.id) ? {...item,slot:slots.get(item.id)} : item));
 });
 layoutControl.onchange = () => update(library.entries,'',{layout:layoutControl.value});
 sizeControl.onchange = () => update(library.entries,'',{iconSize:sizeControl.value});

 function paint() {
  closeContext();
  const crumbs = [{id:'',name:'自分の書式'}, ...folderPath(folder)];
  const path = $('#formatpath');
  path.innerHTML = crumbs.map(entry => `<button type="button" data-folder="${entry.id}">${esc(entry.name)}</button>`).join('');
  path.querySelectorAll('button').forEach(button => {
   const id = button.dataset.folder;
   button.onclick = () => openFolder(id);
   bindDrop(button,id);
  });
  const rows = currentRows();
  grid.innerHTML = rows.length ? rows.map(entry => `<button type="button" class="formaticon" title="${esc(entry.name)}" data-id="${entry.id}" draggable="true" aria-pressed="${entry.id===selectedId}"><span aria-hidden="true">${entry.kind==='folder'?'📁':'📄'}</span>${esc(entry.name)}<small>${entry.kind==='folder'?'フォルダ':'指示書の書式'}</small></button>`).join('')
   : '<p>この場所にはまだ書式がありません。指示書の作成・編集画面から保存できます。</p>';
  grid.querySelectorAll('button').forEach(button => {
   const id = button.dataset.id;
   button.onclick = () => {
    if(selectedId !== id)previewId = '';
    selectedId = id;
    grid.querySelectorAll('button').forEach(item => item.setAttribute('aria-pressed',String(item===button)));
    paintActions();
    paintPreview();
   };
   button.ondblclick = openSelected;
   button.ondragstart = event => {
    dragId = id;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain',id);
   };
   button.ondragend = () => { dragId = ''; };
   if(get(id).kind==='folder')bindDrop(button,id);
  });
  layoutIcons();
  paintActions();
  paintPreview();
 }

 function paintActions() {
  layoutControl.disabled = sizeControl.disabled = changing;
  layoutControl.value = library.layout || 'auto';
  sizeControl.value = library.iconSize || 'medium';
  const back = $('#formatback');
  const mark = history.state?.personalFormats;
  back.disabled = changing || mark?.token !== historyToken || mark.depth <= 1;
  back.title = '一つ前の表示に戻る';
  const entry = get(selectedId);
  for(const id of ['formatopen','formatrename','formatdelete'])$('#'+id).disabled = !entry || changing;
  $('#formatfolder').disabled = changing;
  const options = entry ? ['', ...library.entries.filter(item => item.kind==='folder').map(item => item.id)]
   .filter(id => validDestination(entry,id)) : [];
  destination.innerHTML = options.map(id => `<option value="${id}">${esc(id ? folderPath(id).map(part=>part.name).join(' / ') : '自分の書式（最上位）')}</option>`).join('');
  destination.disabled = $('#formatmove').disabled = !options.length || changing;
 }

 function paintPreview() {
  const entry = get(previewId);
  preview.hidden = !entry;
  if(!entry){preview.replaceChildren();return}
  const d = entry.data;
  const details = Object.entries(labels).filter(([key]) => d[key])
   .map(([key,label]) => `<div><dt>${label}</dt><dd>${esc(d[key])}</dd></div>`).join('');
  const columns = [['name','商品名'],['maker','メーカー'],['sku','品番'],['size','サイズ'],
   ['condition','新古'],['quantity','数量'],['stock','在庫場所'],['note','備考']];
  const items = d.items.length ? '<div class="tablewrap"><table><thead><tr>'+
   columns.map(([,label])=>`<th>${label}</th>`).join('')+'</tr></thead><tbody>'+
   d.items.map(item=>'<tr>'+columns.map(([key])=>`<td>${esc(item[key]??'')}</td>`).join('')+'</tr>').join('')+'</tbody></table></div>'
   : '<p>商品明細はありません。</p>';
  preview.innerHTML = `<h3>${esc(entry.name)}</h3><dl>${details}</dl>${items}<div class="formatactions"><button type="button" class="primary" id="formatcreate">この書式で新規作成</button><button type="button" id="formatedit">書式を編集</button></div>`;
  $('#formatcreate').onclick = () => useFormat(entry,false);
  $('#formatedit').onclick = () => useFormat(entry,true);
 }

 function openSelected() {
  const entry = get(selectedId);
  if(!entry)return;
  if(entry.kind === 'folder'){openFolder(entry.id);return}
  if(previewId !== entry.id){
   previewId = entry.id;
   rememberFormatLocation();
  }
  paintActions();
  paintPreview();
  preview.scrollIntoView({block:'nearest'});
 }

 function useFormat(entry, update) {
  if(!canManagePosting())return;
  closePage();
  openEditor(null,entry.data);
  if(typeof syncMethodNote === 'function')syncMethodNote();
  if(update){
   editor.dataset.formatId = entry.id;
   $('#edittitle').textContent = '書式を編集 — '+entry.name;
   saveButton.textContent = 'この書式を更新';
  }
  dirty = true;
 }

 async function update(entries, successMessage = '', preferences = {}) {
  if(changing)return;
  const user = currentUser;
  changing = true;
  paintActions();
  try {
   const saved = await api('/api/personal-formats',{userId:user.id,version:library.version,entries,...preferences});
   if(currentUser !== user)return;
   library = saved;
   message(successMessage);
   paint();
  } catch(error) {
   message(error.message);
  } finally {
   changing = false;
   paintActions();
  }
 }

 function move(id,target) {
  const entry = get(id);
  if(!entry || !validDestination(entry,target))return;
  if(selectedId === id)selectedId = previewId = '';
  update(library.entries.map(item => item.id===id ? {...item,parent:target,slot:undefined} : item));
 }

 const openPage = async () => {
  if(!canManagePosting() || changing || !page.hidden || returningToPage)return;
  const user = currentUser;
  folder = selectedId = previewId = '';
  historyToken = crypto.randomUUID();
  showPage();
  pushFormatLocation(1);
  grid.replaceChildren();
  preview.hidden = true;
  message('読み込み中…');
  changing = true;
  paintActions();
  try {
   const loaded = await api('/api/personal-formats');
   if(currentUser !== user || page.hidden)return;
   library = loaded;
   openFolder('',false);
   message('');
  } catch(error) {
   library = {version:0,entries:[]};
   openFolder('',false);
   message(error.message);
  } finally {
   changing = false;
   paintActions();
  }
 };
 $('#personalformatsopen').onclick = openPage;
 window.openPersonalFormatsFromLocation = () => {
  const url = new URL(location.href);
  if(!url.searchParams.has('formats'))return;
  url.searchParams.delete('formats');
  history.replaceState(history.state,'',url);
  return openPage();
 };
 $('#formatback').onclick = () => {
  if(!$('#formatback').disabled)history.back();
 };
 $('#personalformatsclose').onclick = () => closePage();
 $('#formatopen').onclick = openSelected;
 $('#formatmove').onclick = () => move(selectedId,destination.value);
 $('#formatfolder').onclick = () => {
  const name = prompt('フォルダ名')?.trim();
  if(name)update([...library.entries,{id:crypto.randomUUID(),kind:'folder',name,parent:folder}]);
 };
 $('#formatrename').onclick = () => {
  const entry = get(selectedId);
  if(!entry)return;
  const name = prompt('新しい名前',entry.name)?.trim();
  if(name && name!==entry.name)update(library.entries.map(item => item.id===entry.id ? {...item,name} : item));
 };
 $('#formatdelete').onclick = () => {
  const entry = get(selectedId);
  if(!entry)return;
  if(library.entries.some(item => item.parent===entry.id)){message('中の項目を移動してからフォルダを削除してください。');return}
  if(!confirm(`「${entry.name}」を削除しますか？`))return;
  selectedId = previewId = '';
  update(library.entries.filter(item => item.id!==entry.id));
 };

 saveButton.onclick = async () => {
  if(saveButton.disabled || !canManagePosting())return;
  const user = currentUser;
  const editingId = editor.dataset.formatId;
  const draft = gather('下書き');
  const data = Object.fromEntries(Object.keys(labels).map(key => [key,draft[key]||'']));
  data.items = draft.items.filter(item => ['name','maker','sku','size','stock','note','note2'].some(key => item[key]));
  if(!data.customer.trim() && !data.items.some(item => item.name)){
   $('#formerror').textContent = '顧客名か商品名を入力してから書式に保存してください。';
   return;
  }
  const name = prompt('書式名',get(editingId)?.name || data.customer)?.trim();
  if(!name)return;
  saveButton.disabled = true;
  $('#formerror').textContent = '';
  try {
   const state = editingId ? library : await api('/api/personal-formats');
   if(currentUser !== user || !editor.open)return;
   const previous = state.entries.find(item=>item.id===editingId);
   if(editingId && !previous)throw Error('書式を開き直してください。');
   const entry = {id:editingId||crypto.randomUUID(),parent:previous?.parent||'',slot:previous?.slot,kind:'format',name,data};
   const entries = editingId ? state.entries.map(item=>item.id===editingId?entry:item) : [...state.entries,entry];
   const saved = await api('/api/personal-formats',{userId:user.id,version:state.version,entries});
   if(currentUser !== user)return;
   library = saved;
   if(editingId)dirty = false;
   toast(editingId?'書式を更新しました。':'自分の書式に保存しました。');
  } catch(error) {
   $('#formerror').textContent = error.message;
   $('#formerror').scrollIntoView({block:'nearest'});
  } finally {
   saveButton.disabled = false;
  }
 };
})();
