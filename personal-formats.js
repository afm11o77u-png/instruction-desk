(() => {
 const dialog = $('#personalformats');
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
   event.preventDefault();
   const id = dragId;
   dragId = '';
   move(id,target);
  };
 }

 function openFolder(id) {
  folder = id;
  selectedId = previewId = '';
  paint();
 }

 function paint() {
  const crumbs = [{id:'',name:'自分の書式'}, ...folderPath(folder)];
  const path = $('#formatpath');
  path.innerHTML = crumbs.map(entry => `<button type="button" data-folder="${entry.id}">${esc(entry.name)}</button>`).join('');
  path.querySelectorAll('button').forEach(button => {
   const id = button.dataset.folder;
   button.onclick = () => openFolder(id);
   bindDrop(button,id);
  });
  const rows = library.entries.filter(entry => entry.parent === folder).sort((a,b) =>
   a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name,'ja'));
  grid.innerHTML = rows.length ? rows.map(entry => `<button type="button" class="formaticon" data-id="${entry.id}" draggable="true" aria-pressed="${entry.id===selectedId}"><span aria-hidden="true">${entry.kind==='folder'?'📁':'📄'}</span>${esc(entry.name)}<small>${entry.kind==='folder'?'フォルダ':'指示書の書式'}</small></button>`).join('')
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
  paintActions();
  paintPreview();
 }

 function paintActions() {
  const back = $('#formatback');
  back.disabled = changing || (!previewId && !folder);
  back.title = previewId ? '書式の一覧に戻る' : '一つ上のフォルダに戻る';
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
  previewId = entry.id;
  paintActions();
  paintPreview();
  preview.scrollIntoView({block:'nearest'});
 }

 function useFormat(entry, update) {
  if(!canManagePosting())return;
  dialog.close();
  openEditor(null,entry.data);
  if(typeof syncMethodNote === 'function')syncMethodNote();
  if(update){
   editor.dataset.formatId = entry.id;
   $('#edittitle').textContent = '書式を編集 — '+entry.name;
   saveButton.textContent = 'この書式を更新';
  }
  dirty = true;
 }

 async function update(entries) {
  if(changing)return;
  const user = currentUser;
  changing = true;
  paintActions();
  try {
   const saved = await api('/api/personal-formats',{userId:user.id,version:library.version,entries});
   if(currentUser !== user)return;
   library = saved;
   message('');
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
  update(library.entries.map(item => item.id===id ? {...item,parent:target} : item));
 }

 $('#personalformatsopen').onclick = async () => {
  if(!canManagePosting() || changing || dialog.open)return;
  const user = currentUser;
  dialog.showModal();
  grid.replaceChildren();
  preview.hidden = true;
  message('読み込み中…');
  changing = true;
  paintActions();
  try {
   const loaded = await api('/api/personal-formats');
   if(currentUser !== user || !dialog.open)return;
   library = loaded;
   openFolder('');
   message('');
  } catch(error) {
   library = {version:0,entries:[]};
   openFolder('');
   message(error.message);
  } finally {
   changing = false;
   paintActions();
  }
 };
 $('#formatback').onclick = () => {
  if(previewId){
   previewId = '';
   paintActions();
   paintPreview();
   grid.querySelector('[aria-pressed="true"]')?.focus();
   return;
  }
  if(folder)openFolder(get(folder)?.parent || '');
 };
 $('#personalformatsclose').onclick = () => dialog.close();
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
   const entry = {id:editingId||crypto.randomUUID(),parent:previous?.parent||'',kind:'format',name,data};
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
