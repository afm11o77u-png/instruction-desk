(() => {
 const page = $('#personalformats');
 const editor = $('#editor');
 const saveButton = $('#personalformatsave');
 const panes = {
  left:{grid:$('#formatgrid'),path:$('#formatpath')},
  right:{grid:$('#formatrightgrid'),path:$('#formatrightpath')},
 };
 const splitButton = $('#formatsplit');
 let split = false, rightFolder = '', selectedSide = 'left';
 const folderOf = side => side==='right' ? rightFolder : folder;
 const preview = $('#formatpreview');
 const destination = $('#formatdestination');
 let library = {version: 0, entries: []};
 let folder = '', selectedId = '', previewId = '', changing = false;
 let dragId = '';
 const get = id => library.entries.find(entry => entry.id === id);
 const message = text => { $('#formatmessage').textContent = text; };
 let historyToken = '', returningToPage = false, resumeEditor = false;
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
  if(resumeEditor&&canManagePosting()&&!editor.open)editor.showModal();
  resumeEditor = false;
 }

 window.resetPersonalFormatsPage = () => {resumeEditor=false;closePage()};


 function pushFormatLocation(depth) {
  const url = new URL(location.href);
  url.searchParams.set('formats','1');
  history.pushState({personalFormats:{token:historyToken,depth,folder,previewId,rightFolder,selectedSide,split}},'',url);
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
  rightFolder = get(mark.rightFolder)?.kind === 'folder' ? mark.rightFolder : '';
  split = !!mark.split;
  selectedSide = split && mark.selectedSide==='right' ? 'right' : 'left';
  const entry = get(mark.previewId);
  selectedId = previewId = entry?.kind === 'format' && entry.parent === folderOf(selectedSide) ? entry.id : '';
  paint();
 });
 const context = $('#formatcontext');
 const copyAction = $('#formatcopy');
 const pasteAction = $('#formatpaste');
 const renameAction = $('#formatcontextrename');
 let renameTimer;
 let copied = null, contextTarget = null, contextFocus = null, contextFolder = '';

 function closeContext(restoreFocus = false) {
  if(context.hidden)return;
  context.hidden = true;
  if(restoreFocus && contextFocus?.isConnected)contextFocus.focus();
  contextFocus = null;
 }

 page.addEventListener('contextmenu', event => {
  const side = panes.left.grid.contains(event.target) ? 'left' : panes.right.grid.contains(event.target) ? 'right' : '';
  if(changing || !side)return;
  clearTimeout(renameTimer);
  contextFolder = folderOf(side);
  const tile = event.target.closest('.formaticon');
  const entry = tile && get(tile.dataset.id);
  event.preventDefault();
  closeContext();
  contextTarget = entry || null;
  contextFocus = tile || document.activeElement;
  copyAction.hidden = entry?.kind !== 'format';
  renameAction.hidden = !entry;
  pasteAction.disabled = copied?.user !== currentUser;
  pasteAction.textContent = entry?.kind === 'folder' ? 'このフォルダに貼り付け' : '貼り付け';
  const bounds = page.getBoundingClientRect();
  const anchor = tile?.getBoundingClientRect() || bounds;
  context.hidden = false;
  context.style.left = Math.max(bounds.left,Math.min(event.clientX || anchor.left,bounds.right-context.offsetWidth))+'px';
  context.style.top = Math.max(bounds.top,Math.min(event.clientY || anchor.bottom,bounds.bottom-context.offsetHeight))+'px';
  if(!copyAction.hidden)copyAction.focus();
  else if(!renameAction.hidden)renameAction.focus();
  else if(!pasteAction.disabled)pasteAction.focus();
 });
 page.addEventListener('pointerdown', event => {
  if(!context.contains(event.target))closeContext();
 });
 page.addEventListener('keydown', event => {
  if(event.key==='Escape' && !context.hidden){event.preventDefault();closeContext(true)}
 });

 renameAction.onclick = () => {
  const tile = contextFocus;
  const entry = contextTarget;
  closeContext();
  if(entry)startRename(entry,tile);
 };

 copyAction.onclick = () => {
  if(contextTarget?.kind !== 'format' || !canManagePosting())return;
  copied = {user:currentUser, name:contextTarget.name, data:structuredClone(contextTarget.data)};
  closeContext(true);
  message('書式をコピーしました。貼り付け先で右クリックしてください。');
 };
 pasteAction.onclick = () => {
  if(changing || !canManagePosting() || copied?.user !== currentUser)return;
  const parent = contextTarget?.kind === 'folder' ? contextTarget.id : contextFolder;
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

 const extraLabels = {code:'顧客コード',management:'管理番号',employeeNumber:'社員番号',
  employee:'従業員名',phonetic:'ふりがな',sex:'性別',repair:'修理内容',notes:'備考'};
 const recurring = {...labels,code:'顧客コード',repair:'修理内容',notes:'備考'};

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

 function openFolder(id, remember = true, side = 'left') {
  const moved = id !== folderOf(side) || !!previewId;
  if(side==='right')rightFolder = id;
  else folder = id;
  selectedSide = side;
  selectedId = previewId = '';
  if(remember && moved)rememberFormatLocation();
  paint();
 }

 function currentRows(parent) {
  return library.entries.filter(entry => entry.parent === parent).sort((a,b) =>
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

 function fitIconName(label) {
  label.style.fontSize = '';
  if(label.clientHeight === 0 || label.scrollHeight <= label.clientHeight)return;
  const preferred = parseFloat(getComputedStyle(label).fontSize);
  for(let fontSize = Math.floor(preferred)-1; fontSize >= 7; fontSize--){
   label.style.fontSize = fontSize+'px';
   if(label.scrollHeight <= label.clientHeight)return;
  }
 }

 function layoutIcons(side) {
  const {grid} = panes[side];
  const [width,height] = sizes[library.iconSize] || sizes.medium;
  grid.style.setProperty('--format-width',width+'px');
  grid.style.setProperty('--format-height',height+'px');
  const cols = Math.max(1,Math.floor((grid.clientWidth+gap)/(width+gap)));
  const slots = library.layout==='aligned' ? occupiedSlots(currentRows(folderOf(side))) : null;
  for(const tile of grid.querySelectorAll('.formaticon')){
   const slot = slots?.get(tile.dataset.id);
   tile.style.gridColumn = slot == null ? '' : String(slot%cols+1);
   tile.style.gridRow = slot == null ? '' : String(Math.floor(slot/cols)+1);
   const label = tile.querySelector('.formaticon-name');
   if(label)fitIconName(label);
  }
 }

 function bindPane(side) {
  const {grid} = panes[side];
  new ResizeObserver(()=>{if(!page.hidden && (side==='left'||split))layoutIcons(side)}).observe(grid);
  grid.addEventListener('dragover', event => {
   const entry = get(dragId);
   if(!changing && entry && (validDestination(entry,folderOf(side)) || (entry.parent===folderOf(side) && library.layout==='aligned'))){
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
   }
  });
  grid.addEventListener('drop', event => {
   const entry = get(dragId);
   if(!entry || changing)return;
   const parent = folderOf(side);
   if(entry.parent!==parent){
    if(validDestination(entry,parent)){event.preventDefault();move(entry.id,parent)}
    return;
   }
   if(library.layout!=='aligned')return;
   event.preventDefault();
   const [width,height] = sizes[library.iconSize] || sizes.medium;
   const bounds = grid.getBoundingClientRect();
   const cols = Math.max(1,Math.floor((grid.clientWidth+gap)/(width+gap)));
   const column = Math.min(cols-1,Math.max(0,Math.floor((event.clientX-bounds.left)/(width+gap))));
   const row = Math.max(0,Math.floor((event.clientY-bounds.top)/(height+gap)));
   const target = Math.min(599,row*cols+column);
   const slots = occupiedSlots(currentRows(folderOf(side)));
   const previous = slots.get(entry.id);
   if(previous===target)return;
   for(const [id,slot] of slots)if(slot===target)slots.set(id,previous);
   slots.set(entry.id,target);
   update(library.entries.map(item => slots.has(item.id) ? {...item,slot:slots.get(item.id)} : item));
  });
 }
 bindPane('left');
 bindPane('right');
 document.fonts?.ready.then(()=>{if(!page.hidden){layoutIcons('left');if(split)layoutIcons('right')}});
 layoutControl.onchange = () => update(library.entries,'',{layout:layoutControl.value});
 sizeControl.onchange = () => update(library.entries,'',{iconSize:sizeControl.value});

 function paintPane(side) {
  const {grid,path} = panes[side];
  const parent = folderOf(side);
  const crumbs = [{id:'',name:'自分の書式'}, ...folderPath(parent)];
  path.innerHTML = crumbs.map(entry => `<button type="button" data-folder="${entry.id}">${esc(entry.name)}</button>`).join('');
  path.querySelectorAll('button').forEach(button => {
   const id = button.dataset.folder;
   button.onclick = () => openFolder(id,true,side);
   bindDrop(button,id);
  });
  const rows = currentRows(parent);
  grid.innerHTML = rows.length ? rows.map(entry => `<button type="button" class="formaticon" title="${esc(entry.name)}" data-id="${entry.id}" draggable="true" aria-pressed="${entry.id===selectedId&&side===selectedSide}"><span class="formaticon-symbol" aria-hidden="true">${entry.kind==='folder'?'📁':'📄'}</span><span class="formaticon-name">${esc(entry.name)}</span><small>${entry.kind==='folder'?'フォルダ':'指示書の書式'}</small></button>`).join('')
   : '<p>この場所にはまだ書式がありません。指示書の作成・編集画面から保存できます。</p>';
  grid.querySelectorAll('button').forEach(button => {
   const id = button.dataset.id;
   button.onclick = event => {
    clearTimeout(renameTimer);
    const renameByClick = !changing && event.detail===1 && selectedId===id && selectedSide===side &&
     !!event.target.closest('.formaticon-name');
    if(renameByClick)renameTimer = setTimeout(() => startRename(get(id),button),550);
    if(selectedId !== id)previewId = '';
    selectedId = id;
    selectedSide = side;
    for(const pane of Object.values(panes))pane.grid.querySelectorAll('button').forEach(item => item.setAttribute('aria-pressed',String(item===button)));
    paintActions();
    paintPreview();
   };
   button.ondblclick = () => {clearTimeout(renameTimer);openSelected()};
   button.ondragstart = event => {
    clearTimeout(renameTimer);
    dragId = id;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain',id);
   };
   button.ondragend = () => { dragId = ''; };
   if(get(id).kind==='folder')bindDrop(button,id);
  });
  layoutIcons(side);
 }

 function paint() {
  clearTimeout(renameTimer);
  closeContext();
  if(folder && !get(folder))folder = '';
  if(rightFolder && !get(rightFolder))rightFolder = '';
  $('#formatrightpane').hidden = !split;
  $('#formatpanes').classList.toggle('is-split',split);
  splitButton.setAttribute('aria-pressed',String(split));
  splitButton.textContent = split ? '分割を閉じる' : '分割表示';
  paintPane('left');
  if(split)paintPane('right');
  paintActions();
  paintPreview();
 }

 function paintActions() {
  splitButton.disabled = layoutControl.disabled = sizeControl.disabled = changing;
  $('#formatimport').disabled = $('#formatbatchopen').disabled = changing;
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
  const details = Object.entries({...labels,...extraLabels}).filter(([key]) => d[key])
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
  if(entry.kind === 'folder'){openFolder(entry.id,true,selectedSide);return}
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
  if(resumeEditor && dirty && !confirm('現在の入力内容を破棄して、この書式を開きますか？'))return;
  resumeEditor = false;
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

 const openPage = async (fromEditor = false) => {
  if(!canManagePosting() || changing || !page.hidden || returningToPage)return;
  const user = currentUser;
  resumeEditor = fromEditor;
  if(fromEditor)editor.close();
  folder = rightFolder = selectedId = previewId = '';
  split = false;
  selectedSide = 'left';
  historyToken = crypto.randomUUID();
  showPage();
  pushFormatLocation(1);
  for(const pane of Object.values(panes))pane.grid.replaceChildren();
  $('#formatrightpane').hidden = true;
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
 $('#personalformatsopen').onclick = () => openPage();
 $('#personalformatspick').onclick = () => openPage(true);
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
 splitButton.onclick = () => {
  split = !split;
  if(split)rightFolder = get(folder)?.parent || '';
  else if(selectedSide==='right'){
   selectedSide = 'left';
   selectedId = previewId = '';
  }
  rememberFormatLocation();
  paint();
 };
 $('#formatopen').onclick = openSelected;
 $('#formatmove').onclick = () => move(selectedId,destination.value);
 $('#formatfolder').onclick = () => {
  const name = prompt('フォルダ名')?.trim();
  if(name)update([...library.entries,{id:crypto.randomUUID(),kind:'folder',name,parent:folderOf(selectedSide)}]);
 };
 function startRename(entry,tile) {
  clearTimeout(renameTimer);
  if(changing || !entry || !tile?.isConnected || page.hidden || !canManagePosting())return;
  selectedId = entry.id;
  selectedSide = panes.right.grid.contains(tile) ? 'right' : 'left';
  if(previewId !== entry.id)previewId = '';
  for(const pane of Object.values(panes))pane.grid.querySelectorAll('.formaticon').forEach(item=>{
   item.setAttribute('aria-pressed',String(item===tile));
  });
  const wrapper = document.createElement('div');
  wrapper.className = 'formaticon is-renaming';
  wrapper.dataset.id = entry.id;
  wrapper.style.cssText = tile.style.cssText;
  const input = document.createElement('input');
  input.className = 'format-rename-input';
  input.type = 'text';
  input.maxLength = 80;
  input.value = entry.name;
  input.setAttribute('aria-label',entry.kind==='folder'?'フォルダ名':'書式名');
  wrapper.append(tile.querySelector('.formaticon-symbol').cloneNode(true),input,tile.querySelector('small').cloneNode(true));
  tile.replaceWith(wrapper);
  let finished = false;
  const finish = save => {
   if(finished || !wrapper.isConnected)return;
   finished = true;
   const name = input.value.trim();
   paint();
   const replacement = panes[selectedSide].grid.querySelector('.formaticon[aria-pressed="true"]');
   replacement?.focus();
   if(save && name && name!==entry.name)update(library.entries.map(item=>item.id===entry.id?{...item,name}:item));
  };
  input.onkeydown = event => {
   if(event.isComposing)return;
   if(event.key==='Enter'||event.key==='Escape'){
    event.preventDefault();
    event.stopPropagation();
    finish(event.key==='Enter');
   }
  };
  input.onblur = () => finish(true);
  input.focus();
  input.select();
  paintActions();
  paintPreview();
 }

 $('#formatrename').onclick = () => {
  const tile = panes[selectedSide].grid.querySelector('.formaticon[aria-pressed="true"]');
  startRename(get(selectedId),tile);
 };
 $('#formatdelete').onclick = () => {
  const entry = get(selectedId);
  if(!entry)return;
  if(library.entries.some(item => item.parent===entry.id)){message('中の項目を移動してからフォルダを削除してください。');return}
  if(!confirm(`「${entry.name}」を削除しますか？`))return;
  selectedId = previewId = '';
  update(library.entries.filter(item => item.id!==entry.id));
 };

 const singleFile = $('#formatimportfile');
 async function readExcel(file) {
  if(file.size>6000000)throw Error('ファイルが大きすぎます。');
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for(let i=0;i<bytes.length;i+=0x8000)binary += String.fromCharCode(...bytes.subarray(i,i+0x8000));
  return api('/api/templates/import',{filename:file.name,data:btoa(binary)});
 }

 $('#formatimport').onclick = () => singleFile.click();
 singleFile.onchange = async () => {
  const file = singleFile.files[0];
  singleFile.value = '';
  if(!file || changing)return;
  const user = currentUser;
  changing = true;
  paintActions();
  try {
   const result = await readExcel(file);
   if(user!==currentUser || page.hidden)return;
   if(resumeEditor && dirty && !confirm('現在の入力内容を破棄して、Excelの内容で新規作成しますか？'))return;
   resumeEditor = false;
   closePage();
   openEditor(null,result.payload);
   dirty = true;
   toast('Excelの内容を入力しました。'+(result.warnings||[]).join(' '));
  } catch(error) {
   message(error.message);
  } finally {
   changing = false;
   paintActions();
  }
 };

 const batch = $('#formatbatch');
 const batchRun = $('#formatbatchrun');
 const batchResult = $('#formatbatchresult');
 const batchFolder = $('#formatbatchdestination');
 const batchCollision = $('#formatbatchcollision');
 let batchFiles = [];
 let importing = false;
 const cleanName = value => Array.from(value.trim()).slice(0,80).join('');

 function selectBatchFiles(input) {
  batchFiles = [...input.files].filter(file => /\.xlsx$/i.test(file.name) && !file.name.startsWith('~$'));
  input.value = '';
  $('#formatbatchselection').textContent = batchFiles.length ? batchFiles.length+'件を選択' : 'Excelファイル（.xlsx）がありません。';
  batchRun.disabled = !batchFiles.length;
  batchResult.textContent = '';
 }

 function createBatchFolder(entries,parent,name) {
  const match = entries.find(entry=>entry.kind==='folder'&&entry.parent===parent&&entry.name===name);
  if(match)return match.id;
  const id = crypto.randomUUID();
  entries.push({id,kind:'folder',parent,name});
  return id;
 }

 $('#formatbatchopen').onclick = () => {
  if(changing)return;
  batchFiles = [];
  batchRun.disabled = true;
  $('#formatbatchselection').textContent = '';
  batchResult.textContent = '';
  batchFolder.innerHTML = '<option value="">自分の書式（最上位）</option>'+library.entries.filter(entry=>entry.kind==='folder').map(entry=>
   `<option value="${entry.id}">${esc(folderPath(entry.id).map(part=>part.name).join(' / '))}</option>`).join('');
  batchFolder.value = folderOf(selectedSide);
  batchCollision.value = 'skip';
  batch.showModal();
 };
 $('#formatbatchclose').onclick = () => {if(!importing)batch.close()};
 batch.addEventListener('cancel',event=>{if(importing)event.preventDefault()});
 for(const [button,id] of [['formatbatchpick','formatbatchfiles'],['formatbatchpickdir','formatbatchdir']]){
  const input = $('#'+id);
  $('#'+button).onclick = () => {if(!importing)input.click()};
  input.onchange = () => selectBatchFiles(input);
 }
 batchRun.onclick = async () => {
  if(changing || !batchFiles.length)return;
  const user = currentUser;
  const base = batchFolder.value;
  const overwrite = batchCollision.value==='overwrite';
  if(overwrite && !confirm('保存先に同じ名前の書式がある場合は上書きします。続けますか？'))return;
  const results = [], entries = library.entries.map(entry=>({...entry}));
  let count = 0;
  changing = importing = true;
  batch.querySelectorAll('button,select').forEach(control=>control.disabled=true);
  try {
   for(const file of batchFiles){
    if(user!==currentUser || page.hidden)return;
    batchResult.textContent = '読み込み中… '+file.name;
    try {
     const name = cleanName(file.name.replace(/\.xlsx$/i,''));
     if(!name)throw Error('保存名を付けられません。');
     const path = (file.webkitRelativePath||'').split('/').slice(0,-1).map(cleanName).filter(Boolean);
     let parent = base;
     for(const folderName of path){
      parent = entries.find(entry=>entry.kind==='folder'&&entry.parent===parent&&entry.name===folderName)?.id;
      if(!parent)break;
     }
     const previous = parent!=null && entries.find(entry=>entry.kind==='format'&&entry.parent===parent&&entry.name===name);
     if(previous && !overwrite){results.push(file.name+'：同名のため登録を飛ばしました。');continue}
     const result = await readExcel(file);
     if(user!==currentUser || page.hidden)return;
     parent = path.reduce((id,folderName)=>createBatchFolder(entries,id,folderName),base);
     const entry = {id:previous?.id || crypto.randomUUID(),slot:previous?.slot,kind:'format',parent,name,data:result.payload};
     if(previous)entries[entries.indexOf(previous)] = entry;
     else entries.push(entry);
     count++;
     results.push(file.name+'：'+(previous?'上書き':'登録')+(result.warnings?.length?'（'+result.warnings.join(' ')+'）':''));
    } catch(error) {
     results.push(file.name+'：'+error.message);
    }
   }
   if(!count){batchResult.textContent=results.join('\n');return}
   const saved = await api('/api/personal-formats',{userId:user.id,version:library.version,entries});
   if(user!==currentUser || page.hidden)return;
   library = saved;
   batchFiles = [];
   paint();
   batchResult.textContent = count+'件を自分の書式に保存しました。\n'+results.join('\n');
  } catch(error) {
   batchResult.textContent = error.message;
  } finally {
   changing = importing = false;
   batch.querySelectorAll('button,select').forEach(control=>control.disabled=false);
   batchRun.disabled = !batchFiles.length;
   paintActions();
  }
 };

 saveButton.onclick = async () => {
  if(saveButton.disabled || !canManagePosting())return;
  const user = currentUser;
  const editingId = editor.dataset.formatId;
  const draft = gather('下書き');
  const savedData = get(editingId)?.data || {};
  const editLabels = editingId ? {...recurring,...extraLabels} : recurring;
  const data = {...savedData,...Object.fromEntries(Object.keys(editLabels).map(key => [key,draft[key]||'']))};
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
