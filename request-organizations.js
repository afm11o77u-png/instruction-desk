/* Internal directory editor and customer visibility preview. No customer login is exposed. */
(()=>{'use strict';
const $=s=>document.querySelector(s),desk=()=>window.WebRequestDesk,esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),clone=x=>JSON.parse(JSON.stringify(x));
let org={version:0,nodes:[],customer_scopes:[],staff_scopes:[],accounts:[]},dirty=false,pending=new Set(),editing=null,saving=false,displayedPage='';
const data=()=>desk().getData(),admin=()=>data().isAdmin&&!data().previewAccountId&&!data().previewPageId,notice=s=>desk().notify(s),nameKey=s=>String(s).normalize('NFKC').toLowerCase().trim();
function nodePath(id){const n=org.nodes.find(x=>x.id===id);if(!n)return '';const p=org.nodes.find(x=>x.id===n.parent_id);return (p?nodePath(p.id)+' / ':'')+n.name}
function nodeOptions(nodes){return nodes.map(n=>`<option value="${esc(n.id)}">${esc(nodePath(n.id))}</option>`).join('')}
function customerPath(cid){const selected=$('#scope-select')?.value,site=org.nodes.find(n=>n.id===selected&&n.customer_id===cid)||org.nodes.find(n=>n.kind==='company'&&n.customer_id===cid);return site?nodePath(site.id):''}
function descendants(id){const found=new Set([id]);for(let i=0;i<=org.nodes.length;i++){let added=false;org.nodes.forEach(n=>{if(found.has(n.parent_id)&&!found.has(n.id)){found.add(n.id);added=true}});if(!added)break}return found}
function matchesCustomer(cid){const company=$('#org-company-filter').value,region=$('#org-region-filter').value;if(!company&&!region)return true;const allowed=descendants(region||company);return org.nodes.some(n=>n.kind==='site'&&n.customer_id===cid&&allowed.has(n.id))}
function filters(){const old=$('#org-company-filter').value;$('#org-company-filter').innerHTML='<option value="">すべての企業</option>'+nodeOptions(org.nodes.filter(n=>n.kind==='company'));if(org.nodes.some(n=>n.id===old&&n.kind==='company'))$('#org-company-filter').value=old;regionFilter()}
function regionFilter(){const company=$('#org-company-filter').value,old=$('#org-region-filter').value;$('#org-region-filter').innerHTML='<option value="">すべてのカテゴリ</option>'+nodeOptions(org.nodes.filter(n=>n.kind==='region'&&(!company||n.parent_id===company)));if([...$('#org-region-filter').options].some(o=>o.value===old))$('#org-region-filter').value=old}
function setStatus(){const status=saving?'保存中…':dirty||pending.size?'未保存の変更があります':'保存済み';$('#org-status').textContent=status;const pageStatus=$('#web-page-status');if(pageStatus)pageStatus.textContent=status;$('#org-save-bar').hidden=!admin();$('#org-save').disabled=saving||pending.size>0||!dirty;$('#org-save').textContent=saving?'保存中…':'変更をまとめて保存';$('#org-save-hint').textContent=pending.size?'入力中の内容は、右側の追加・変更または適用ボタンで図に反映してから保存してください。':dirty?'図に反映した追加・編集・移動を、このボタンで正式に保存します。':'すべての変更が保存されています。';}

function mark(form){moveHistory=[];syncMoveUndo();if(form)pending.add(form);else dirty=true;setStatus()}
function rootInUse(n){return org.nodes.some(x=>x.parent_id===n.id)||(org.request_pages||[]).some(p=>p.node_ids.includes(n.id))||org.staff_scopes.some(s=>s.node_id===n.id)||org.customer_scopes.some(s=>(s.node_ids||[]).includes(n.id))}
function prepareSelectedRoot(){const customer=data().customers.find(c=>c.id===Number($('#org-root-customer').value)),prepare=desk().getMode()==='organization'&&admin();org.nodes=org.nodes.filter(n=>!n._prepared||rootInUse(n)||(prepare&&n.customer_id===customer?.id));if(prepare&&customer&&!selectedRoot())org.nodes.push({id:crypto.randomUUID?.()||('customer-'+customer.id),parent_id:null,kind:'company',name:customer.name,customer_id:customer.id,site_type:'',_prepared:true});}
function selectedRoot(){return org.nodes.find(n=>n.kind==='company'&&n.customer_id===Number($('#org-root-customer').value))}function rootOptions(){const old=$('#org-root-customer').value;$('#org-root-customer').innerHTML='<option value="">顧客を選択してください</option>'+desk().getCustomers().map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');const wanted=old||new URLSearchParams(location.search).get('customer_id')||'';if([...$('#org-root-customer').options].some(o=>o.value===wanted))$('#org-root-customer').value=wanted;syncRootSearch();}
let rootMatches=[],rootCursor=-1,diagramSelection=null,draggedSite=null,draggedSites=[],moveHistory=[],selectedSites=new Set(),band=null,lastSiteClick=null;
function closeRootSearch(){$('#org-root-options').hidden=true;$('#org-root-search').setAttribute('aria-expanded','false');$('#org-root-search').removeAttribute('aria-activedescendant');rootCursor=-1}
function syncRootSearch(){const c=data().customers.find(x=>x.id===Number($('#org-root-customer').value));$('#org-root-search').value=c?.name||'';closeRootSearch()}
function rootCandidates(all=false){const term=all?'':nameKey($('#org-root-search').value);rootMatches=desk().getCustomers().filter(c=>nameKey(c.name).includes(term));rootCursor=-1;$('#org-root-options').innerHTML=rootMatches.length?rootMatches.map((c,i)=>`<div role="option" id="org-root-option-${i}" data-org-customer-id="${c.id}" aria-selected="${String(c.id)===$('#org-root-customer').value}">${esc(c.name)}</div>`).join(''):'<div role="status" class="customer-no-match">一致する顧客がありません</div>';$('#org-root-options').hidden=false;$('#org-root-search').setAttribute('aria-expanded','true');$('#org-root-search').removeAttribute('aria-activedescendant')}
function chooseRoot(id){const old=$('#org-root-customer').value;$('#org-root-customer').value=String(id);if($('#org-root-customer').onchange()===false)$('#org-root-customer').value=old;syncRootSearch()}

function staffLabel(n){const ancestors=new Set([n.id]);let parent=n.parent_id;while(parent){ancestors.add(parent);parent=org.nodes.find(x=>x.id===parent)?.parent_id}const ids=new Set(org.staff_scopes.filter(s=>ancestors.has(s.node_id)).map(s=>s.user_id));return (data().organizationStaff||data().staff).filter(u=>ids.has(u.id)).map(u=>u.name).join('、')||'未設定'}
let directFocus=false;
function directName(root){return String(root?.direct_group_name||'').trim()||'直下'}
function directHeading(root,count){return `<button type="button" class="org-category-heading ${diagramSelection===root.id&&directFocus?'selected':''}" data-org-select="${esc(root.id)}" data-org-direct="${esc(root.id)}" data-org-kind="direct" data-org-drop="${esc(root.id)}" aria-pressed="${diagramSelection===root.id&&directFocus}"><span class="org-diagram-kind">カテゴリ（まとめる単位）</span><strong>${esc(directName(root))}</strong><small>配下の店舗・事業所 ${count}件</small><span class="org-staff-label">担当：${esc(staffLabel(root))}</span></button>`}
function deliveryLabel(n){const method=n.delivery_method||'',days=(n.collection_days||[]).join('');if(!method)return '';return method==='自社集配'?(days?'自社集配・'+days:'自社集配'):method}
function diagramNode(n){const total=[...descendants(n.id)].filter(id=>org.nodes.some(x=>x.id===id&&x.kind==='site')).length,multi=n.kind==='site'&&selectedSites.has(n.id);return `<button type="button" class="${n.kind==='region'?'org-category-heading':'org-diagram-card'} ${diagramSelection===n.id&&!(directFocus&&n.kind==='company')?'selected':''}${multi?' multi-selected':''}" data-org-select="${esc(n.id)}" data-org-kind="${esc(n.kind)}" ${admin()&&n.kind==='site'?'draggable="true"':''} ${n.kind!=='site'?'data-org-drop="'+esc(n.id)+'"':''} aria-pressed="${diagramSelection===n.id||multi}"><span class="org-diagram-kind">${n.kind==='company'?'顧客・会社':n.kind==='region'?'カテゴリ（まとめる単位）':'拠点'}</span><strong>${esc(n.name)}</strong><small>${n.kind==='company'||n.kind==='region'?'配下の店舗・事業所 '+total+'件':(admin()?'ドラッグして所属先を変更できます':'選ぶと所属先と設定を確認')}</small>${n.kind==='site'&&deliveryLabel(n)?`<span class="org-delivery-tag">${esc(deliveryLabel(n))}</span>`:''}<span class="org-staff-label">担当：${esc(staffLabel(n))}</span></button>`}
function renderDiagramSelection(){const n=org.nodes.find(x=>x.id===diagramSelection),panel=$('#org-selection');if(!n){panel.hidden=true;return}const children=org.nodes.filter(x=>x.parent_id===n.id),sites=[...descendants(n.id)].map(id=>org.nodes.find(x=>x.id===id)).filter(x=>x?.kind==='site');panel.hidden=false;if(n.kind==='company'&&directFocus){panel.innerHTML=`<strong>${esc(directName(n))}</strong><p class="hint">カテゴリ（会社の直下）：${esc(n.name)}に直接属する店舗・事業所 ${children.filter(x=>x.kind==='site').length}件</p>${admin()?`<label>カテゴリ名<input id="org-direct-name" maxlength="100" value="${esc(directName(n))}"></label><div class="actions"><button type="button" id="org-direct-rename">名前を変更</button><button type="button" data-org-add="site">＋ このカテゴリに店舗・事業所を追加</button></div>`:''}`;return}panel.innerHTML=`<strong>${esc(n.name)}</strong>${admin()&&n.kind==='company'?`<label class="check"><input id="org-company-personnel" type="checkbox" ${(n.personnel_registration??!org.nodes.some(x=>x.customer_id===n.customer_id&&x.kind==='site'))?'checked':''}> この会社自体で人員を管理する</label>`:''}<div class="actions">${admin()?`<button type="button" data-org-add="site">＋ ${n.kind==='company'?'この会社に':n.kind==='region'?'このカテゴリに':'同じ所属先に'}店舗・事業所を追加</button>`:''}</div>`}
// 会社直下の枠を「カテゴリ」として扱う：見出しの選択と名前の変更（データは会社の直下のまま、会社に direct_group_name として保存）。
$('#org-tree').addEventListener('click',e=>{const jump=e.target.closest('[data-org-jump]'),select=e.target.closest('[data-org-select]');if(jump)directFocus=org.nodes.some(n=>n.id===jump.dataset.orgJump&&n.kind==='company');else if(select)directFocus=!!select.closest('[data-org-direct]')},true);
function renameDirectGroup(){const root=selectedRoot(),input=$('#org-direct-name');if(!admin()||!root||!input)return;const name=input.value.trim();if(!name||name.length>100){notice('カテゴリ名を1〜100文字で入力してください。');return}if(org.nodes.some(n=>n.parent_id===root.id&&n.kind==='region'&&nameKey(n.name)===nameKey(name))){notice('同じ名前のカテゴリがすでにあります。別の名前にしてください。');return}if(name===directName(root))return;root.direct_group_name=name;mark();tree();notice('カテゴリ名を「'+name+'」に変更しました。最後にまとめて保存してください。')}
$('#org-selection').addEventListener('click',e=>{if(e.target.id==='org-direct-rename')renameDirectGroup()});
$('#org-selection').addEventListener('keydown',e=>{if(e.target.id==='org-direct-name'&&e.key==='Enter'){e.preventDefault();renameDirectGroup()}});
$('#org-selection').addEventListener('change',e=>{if(e.target.id!=='org-company-personnel')return;const n=selectedRoot();if(!admin()||!n)return;n.personnel_registration=e.target.checked;mark();notice('会社の人員登録設定を変更しました。最後にまとめて保存してください。')});
function diagramAddSlot(parent){if(!admin())return '';return `<li class="org-diagram-branch org-add-branch"><div class="org-add-card"><span class="org-diagram-kind">追加用の枠・未登録</span><strong>${parent.kind==='company'?'会社の直下に追加できます':'このカテゴリの拠点を増やせます'}</strong><p>登録済みの項目と並んで表示されます。</p>${parent.kind==='company'?`<button type="button" data-org-add="region" data-org-add-parent="${esc(parent.id)}">＋ カテゴリを追加</button>`:''}<button type="button" data-org-add="site" data-org-add-parent="${esc(parent.id)}">＋ 店舗・事業所を追加</button></div></li>`}
function tree(){const root=selectedRoot();if(!root){$('#org-tree').innerHTML='<p class="org-diagram-empty">上で顧客を選ぶと、その会社の構成図を表示します。カテゴリ・店舗は図から追加できます。</p>';$('#org-selection').hidden=true;ensureOrgToolbars();return}if(!descendants(root.id).has(diagramSelection))diagramSelection=root.id;const children=org.nodes.filter(n=>n.parent_id===root.id),sites=children.filter(n=>n.kind==='site'),regions=children.filter(n=>n.kind==='region');function siteGrid(parent,items){return `<ul class="org-wrapped-sites">${items.map(n=>`<li>${diagramNode(n)}</li>`).join('')+diagramAddSlot(parent)}</ul>`}$('#org-tree').innerHTML=`${admin()?`<div class="org-drop-tray"><span>ドラッグの移動先</span>${[root,...regions].map(n=>`<button type="button" class="org-drop-target" data-org-drop="${esc(n.id)}" data-org-jump="${esc(n.id)}" title="この枠に店舗・事業所をドラッグして移動">${esc(n.kind==='company'?directName(n):n.name)}</button>`).join('')}</div>`:''}<div class="org-diagram-canvas org-wrapped-diagram"><div class="org-diagram-root">${diagramNode(root)}</div><div class="org-parent-groups">${!regions.length&&!sites.length?'<p>店舗・事業所は会社の直下に追加できます。地域や管理上のまとまりで分けたい場合は、カテゴリを作成してください。</p>':''}${sites.length||admin()?`<section class="org-site-group org-region-group org-direct-group" data-org-drop="${esc(root.id)}" aria-label="${esc(directName(root))}の店舗・事業所"><div class="org-region-heading">${directHeading(root,sites.length)}<p>所属会社：${esc(root.name)} ／ このカテゴリの配下：${sites.length}件（会社の直下）</p></div><div class="org-region-members"><p class="org-region-members-label">${esc(directName(root))}に属する店舗・事業所</p>${siteGrid(root,sites)}</div></section>`:''}${regions.map(region=>{const sites=org.nodes.filter(n=>n.parent_id===region.id&&n.kind==='site');return `<section class="org-site-group org-region-group" data-org-drop="${esc(region.id)}" aria-label="${esc(region.name)}の店舗・事業所"><div class="org-region-heading">${diagramNode(region)}<p>所属会社：${esc(root.name)} ／ このカテゴリの配下：${sites.length}件</p></div><div class="org-region-members"><p class="org-region-members-label">${esc(region.name)}に属する店舗・事業所</p>${siteGrid(region,sites)}</div></section>`}).join('')}</div></div><p class="org-diagram-caption">同じ枠内の店舗・事業所は、同じ会社・カテゴリに属します。件数が増えると下の行へ折り返して表示します。</p>`;renderDiagramSelection();ensureOrgToolbars();syncMoveUndo();syncSelectToolbar();renderDeliveryBulk()}

function visibleSites(){return [...document.querySelectorAll('#org-tree [data-org-kind="site"]')].map(el=>el.dataset.orgSelect)}
function currentCompanySites(){const root=selectedRoot();return root?org.nodes.filter(n=>n.customer_id===root.customer_id&&n.kind==='site'):[]}
function selectAllSites(on){selectedSites=new Set(on?currentCompanySites().map(n=>n.id):[]);renderDeliveryBulk();tree();syncSelectToolbar()}
function selectDiagramNode(id,opts={}){const node=org.nodes.find(n=>n.id===id);if(!node)return;if(!opts.keepPending&&pending.has('node')&&!confirm('入力途中の組織を破棄して選択を切り替えますか？'))return;if(!opts.keepPending)pending.delete('node');diagramSelection=id;if(!opts.silent){if(node.kind==='company')newNode();else if(!(opts.multiOnly&&node.kind==='site'))editNode(id)}tree();renderDeliveryBulk()}
function addDiagramNode(kind,parentId=null){if(pending.has('node')&&!confirm('入力途中の組織を破棄して追加しますか？'))return;const selected=org.nodes.find(n=>n.id===(parentId||diagramSelection))||selectedRoot();if(!selected)return;diagramSelection=selected.id;tree();newNode();$('#org-kind').value=kind;parentOptions();$('#org-parent').value=kind==='region'?selectedRoot().id:(selected.kind==='site'?selected.parent_id:selected.id);$('#org-editor').hidden=false;$('#org-editor-title').textContent=(kind==='region'?'カテゴリ':'店舗・事業所')+'を追加';$('#org-name').focus();$('#org-editor').scrollIntoView({block:'nearest',behavior:'smooth'});setStatus()}

function parentOptions(){const kind=$('#org-kind').value,old=$('#org-parent').value,root=selectedRoot(),allowed=root?descendants(root.id):new Set();$('#org-parent').innerHTML='<option value="">所属先を選択</option>'+nodeOptions(org.nodes.filter(n=>allowed.has(n.id)&&n.id!==editing&&(n.kind==='company'||(kind==='site'&&n.kind==='region'))));if(root&&kind==='site'){const o=[...$('#org-parent').options].find(o=>o.value===root.id);if(o)o.textContent=root.name+' / '+directName(root)+'（会社の直下）'}if(old&&[...$('#org-parent').options].some(o=>o.value===old))$('#org-parent').value=old;else if(root)$('#org-parent').value=root.id;$('#org-parent').disabled=!admin();$('#org-source-customer-fields').hidden=true;updateNameLabel();updatePlacementPreview();}

function updatePlacementPreview(){const parent=org.nodes.find(n=>n.id===$('#org-parent').value),kind=$('#org-kind').value,name=$('#org-name').value.trim();$('#org-placement').textContent=parent?nodePath(parent.id)+(parent.kind==='company'&&kind==='site'?' / '+directName(parent):'')+' → '+(name||(kind==='region'?'新しいカテゴリ':'新しい店舗・事業所')):'追加先を選択してください。'}
function updateNameLabel(){const category=$('#org-kind').value==='region';$('#org-name-label').textContent=category?'カテゴリ名':'拠点名';$('#org-name').placeholder=category?'例：直属、札幌市内、地方':'例：篠路店、札幌支店、本社工場';$('#org-name-help').textContent=category?'店舗・拠点を地域や管理上のまとまりで分類します。':'店舗・支店・事業所・工場など、実際に使用する拠点の名称を入力してください。'}
function inventoryOptions(){const term=nameKey($('#org-customer-search').value),old=$('#org-customer').value;$('#org-customer').innerHTML='<option value="">在庫顧客を選択</option>'+data().customers.filter(c=>nameKey(c.name).includes(term)).map(c=>`<option value="${c.id}">${esc(c.name)}${org.nodes.some(n=>n.customer_id===c.id&&n.id!==editing)?'（他拠点で使用中）':''}</option>`).join('');if([...$('#org-customer').options].some(o=>o.value===old))$('#org-customer').value=old}
function newNode(keepSelection=false){$('#org-kind').closest('label').hidden=false;$('#org-kind').disabled=!admin();$('#org-editor').hidden=!selectedRoot();const previous=keepSelection?{kind:$('#org-kind').value,parent:$('#org-parent').value}:null;editing=null;pending.delete('node');$('#org-node-form').reset();if(previous){$('#org-kind').value=previous.kind;}$('#org-editor-title').textContent='組織を追加';$('#org-node-apply').textContent='編集中の一覧に追加';$('#org-node-delete').disabled=true;parentOptions();if(previous&&[...$('#org-parent').options].some(o=>o.value===previous.parent))$('#org-parent').value=previous.parent;inventoryOptions();setStatus();if(previous)$('#org-name').focus();updatePlacementPreview()}
const nodeFields=['kana','office_code','address','phone','contact'];
function editNode(id){if(pending.has('node')&&!confirm('入力途中の組織を破棄しますか？'))return;const n=org.nodes.find(n=>n.id===id);if(!n)return;editing=id;$('#org-editor').hidden=false;pending.delete('node');$('#org-kind').value=n.kind;$('#org-kind').disabled=true;$('#org-kind').closest('label').hidden=true;parentOptions();$('#org-parent').value=n.parent_id||'';$('#org-name').value=n.name;updateNameLabel();$('#org-customer-search').value='';inventoryOptions();$('#org-customer').value=n.customer_id||'';$('#org-editor-title').textContent=n.kind==='region'?'カテゴリを編集':'拠点を編集';$('#org-node-apply').textContent='編集中の一覧に変更を適用';$('#org-node-delete').disabled=!admin();setStatus();updatePlacementPreview()}
function scopesHtml(selected,nodes=org.nodes){return nodes.map(n=>`<label class="org-scope"><input type="checkbox" value="${esc(n.id)}" ${selected.includes(n.id)?'checked':''}>${esc(nodePath(n.id))}</label>`).join('')||'<p>先に企業・カテゴリ・拠点を登録してください。</p>'}
function legacyPageScope(scope){const page=scope?.page_id?(org.request_pages||[]).find(p=>p.id===scope.page_id):null;return page?{account_id:scope.account_id,node_ids:page.node_ids,can_request:page.can_request}:scope}
function accountScopeNodes(selected){const cid=Number($('#org-root-customer').value);return cid?org.nodes.filter(n=>n.customer_id===cid||selected.includes(n.id)):org.nodes}
function accountForm(){const aid=Number($('#org-account').value),scope=legacyPageScope(org.customer_scopes.find(s=>s.account_id===aid)),selected=scope?.node_ids||[];$('#org-account-page').innerHTML='<option value=""></option>';$('#org-account-page').value='';$('#org-account-page').closest('label').hidden=true;$('#org-account-direct-fields').hidden=false;$('#org-account-nodes').innerHTML=scopesHtml(selected,accountScopeNodes(selected));$('#org-can-request').checked=scope?.can_request??true;$('#org-account-hint').textContent=!aid?'利用者を選ぶと、その人が見られる範囲（会社全体・カテゴリ・拠点）を選べます。':scope?'チェックした会社・カテゴリ・拠点の契約と制服を表示します（下の階層も含みます）。チェックをすべて外すと、どの拠点も表示されません。':'範囲が未設定のため、ログインアカウントの登録先の会社全体を対象にしています。';pending.delete('account');if(!admin())$('#org-account-nodes').querySelectorAll('input').forEach(e=>e.disabled=true)}
function staffNodes(){const root=selectedRoot();return root?org.nodes.filter(n=>n.customer_id===root.customer_id):[]}
function staffForm(){const uid=Number($('#org-staff').value),root=selectedRoot(),nodes=staffNodes().filter(n=>n.kind==='site'),saved=org.staff_scopes.filter(s=>s.user_id===uid).map(s=>s.node_id),covered=new Set(saved.flatMap(id=>[...descendants(id)])),configured=(org.staff_scope_settings||[]).some(s=>s.user_id===uid&&s.customer_id===root?.customer_id);$('#org-staff-mode').value=saved.includes(root?.id)||(!configured&&!saved.some(id=>staffNodes().some(n=>n.id===id)))?'all':'individual';$('#org-staff-nodes').innerHTML=scopesHtml(nodes.filter(n=>covered.has(n.id)||!configured&&!saved.some(id=>staffNodes().some(n=>n.id===id))).map(n=>n.id),nodes);pending.delete('staff');const disabled=!admin()||!uid||!root;$('#org-staff-nodes').querySelectorAll('input').forEach(e=>e.disabled=disabled);for(const id of ['org-staff-all','org-staff-none','org-staff-apply','org-staff-mode','org-staff-remove-all','org-staff-remove-selected'])$('#'+id).disabled=disabled;staffMode()}
function staffMode(){$('#org-staff-nodes').hidden=false;for(const id of ['org-staff-all','org-staff-none','org-staff-remove-selected'])$('#'+id).hidden=false;if(typeof syncStaffDiagramButtons==='function')syncStaffDiagramButtons()}
$('#org-staff-mode').onchange=()=>{if($('#org-staff-mode').value==='all')$('#org-staff-nodes').querySelectorAll('input').forEach(e=>e.checked=true);staffMode();mark('staff')};
function setStaffChecks(checked){if(!admin()||!Number($('#org-staff').value)||!staffNodes().length)return;$('#org-staff-nodes').querySelectorAll('input').forEach(e=>e.checked=checked);$('#org-staff-mode').value='individual';mark('staff')}
$('#org-staff-all').onclick=()=>setStaffChecks(true);$('#org-staff-none').onclick=()=>setStaffChecks(false);
const diagramHeightKey='instruction-desk.org-diagram-height';
function setDiagramHeight(value){const height=Math.max(400,Math.min(1600,Number(value)||800));$('#org-tree').style.height=height+'px';$('#org-diagram-height').value=height;$('#org-diagram-height-value').textContent=height+'px';try{localStorage.setItem(diagramHeightKey,String(height))}catch{}}
let savedDiagramHeight;try{savedDiagramHeight=localStorage.getItem(diagramHeightKey)}catch{}setDiagramHeight(savedDiagramHeight);
$('#org-diagram-height').oninput=e=>setDiagramHeight(e.target.value);$('#org-diagram-height').addEventListener('wheel',e=>{if(e.ctrlKey||!e.deltaY)return;e.preventDefault();setDiagramHeight(Number(e.currentTarget.value)+(e.deltaY<0?50:-50))},{passive:false});$('#org-diagram-height-reset').onclick=()=>setDiagramHeight(800);

function scopeSelectors(){const a=$('#org-account').value,u=$('#org-staff').value;$('#org-account').innerHTML='<option value="">利用者を選択</option>'+(org.accounts||[]).map(x=>`<option value="${x.id}">${esc(x.name)} / ${esc(x.email)}${x.active?'':'（停止中）'}</option>`).join('');if([...$('#org-account').options].some(x=>x.value===a))$('#org-account').value=a;$('#org-staff').innerHTML='<option value="">社内担当者を選択</option>'+(data().organizationStaff||data().staff).map(x=>`<option value="${x.id}">${esc(x.name)}${x.login?' ／ '+esc(x.login):''}</option>`).join('');if([...$('#org-staff').options].some(x=>x.value===u))$('#org-staff').value=u;accountForm();staffForm();pageSelectors()}
function layoutWebSettings(web){const head=$('#org-customer-start>h3');if(head)head.textContent=web?'① 会社を選ぶ':'① 会社の登録・選択';if(!$('#web-settings'))return;const intro=$('#web-settings .section-title p');if(intro)intro.textContent='会社を選び、お客さんの画面に出す制服の表示名・写真と、ログインする利用者（お客さん）を設定します。契約・制服・枚数は顧客・契約管理の登録に従います。';const perm=$('#web-page-permission');if(perm)perm.textContent='制服の表示名・写真と利用者の設定は主管理者・管理者が行います。';const pageEditor=$('#web-page-editors .org-page-editor');if(pageEditor)pageEditor.hidden=true;const account=$('#org-account')?.closest('section');if(account){account.classList.add('web-user-section');const h=account.querySelector('h3');if(h)h.textContent='③ 利用者（お客さんのログイン）';const lead=h?.nextElementSibling;if(lead&&lead.tagName==='P')lead.textContent='お客さんのログインアカウントを登録・編集・停止し、その人が見られる範囲（会社全体・カテゴリ・拠点）と、依頼できるか閲覧のみかを決めます。見られる契約・制服は顧客・契約管理の登録から決まります。';const sel=$('#org-account')?.closest('label');if(sel&&sel.firstChild&&sel.firstChild.nodeType===3)sel.firstChild.textContent='利用者（お客さんのログインアカウント）';const apply=$('#org-account-apply');if(apply)apply.textContent='この利用者の範囲を適用';const note=account.querySelector(':scope>p.small:last-child');if(note)note.textContent='範囲を適用したら、下の「利用者の設定を保存」で確定します。実ログイン・依頼送信はまだ接続していません。';const newBtn=$('#web-user-new');if(newBtn)newBtn.textContent='＋ 利用者を新規登録'}}
function configurePanel(mode){const web=mode==='web-settings',picker=$('#org-customer-start');if(web)$('#web-page-customer').append(picker);else $('#organization').insertBefore(picker,$('.org-structure-layout'));layoutWebSettings(web);$('#org-customer-help').textContent=web?'会社を選ぶと、その会社の制服の表示名・写真と利用者の設定を表示します。契約・制服・組織構成は顧客・契約管理で登録します。':'顧客を選ぶと、そのままカテゴリ・店舗・事業所を追加できます。変更は画面下部の「変更をまとめて保存」で確定します。';$('#web-page-editors').hidden=!admin();$('#web-page-permission').hidden=admin();const pageSaveBtn=$('#web-page-save');if(pageSaveBtn)pageSaveBtn.hidden=!admin();const url=new URL('/customer-contracts',location.origin);url.searchParams.set('page','organization');if($('#org-root-customer').value)url.searchParams.set('customer_id',$('#org-root-customer').value);const orgLink=$('#web-organization-link');if(orgLink)orgLink.href=url.pathname+url.search;}
// 拠点（店舗）の一括取り込み：顧客マスタのExcelから、選んだ会社の直下に拠点を追加する。
// プレビューで選んだ拠点を編集中の一覧に追加し、「変更をまとめて保存」で確定する（それまで保存しない）。
let siteImported=false;
// site_name_key.py と同じ照合：NFKC・空白/大小無視、先頭の会社名と末尾の「店」を外す。補助キーはブランド名（ラッキーマート）を外したもの。
const SITE_BRANDS=['ラッキーマート'];
function plainKey(s){return String(s??'').normalize('NFKC').replace(/\s+/g,'').toLowerCase()}
function companyKey(name){let s=String(name??'').normalize('NFKC').trim();const corp=/^(?:株式会社|有限会社|\(株\)|\(有\))\s*|\s*(?:株式会社|有限会社|\(株\)|\(有\))$/g;s=s.replace(corp,'');while(s&&!/[\p{L}\p{Nd}]$/u.test(s))s=s.slice(0,-1);return plainKey(s.replace(corp,''))}
function siteKeys(name,company){let k=plainKey(name);const ck=companyKey(company);if(ck&&k.startsWith(ck)&&k.length>ck.length)k=k.slice(ck.length);if(k.endsWith('店')&&k.length>1)k=k.slice(0,-1);if(!k)return [];const keys=[k];for(const b of SITE_BRANDS.map(plainKey))if(k.startsWith(b)&&k.length>b.length)keys.push(k.slice(b.length));return keys}
function siteImportKey(name){return plainKey(name)}
function existingSiteKeys(root){const keys=new Set();org.nodes.filter(n=>n.kind==='site'&&n.customer_id===root.customer_id).forEach(n=>{siteKeys(n.name,root.name).forEach(k=>keys.add(k));if(n.office_code)keys.add('code:'+plainKey(n.office_code))});org.nodes.filter(n=>n.parent_id===root.id&&n.kind!=='site').forEach(n=>keys.add('other:'+plainKey(n.name)));return keys}
// 構成図の見出しの右上に、取り込みボタン（拠点・集配曜日）と「未保存の変更を取り消す」を並べる。
function orgImportActions(){
  let box=$('#org-import-actions');if(box)return box;
  const reload=$('#org-reload');if(!reload||!reload.parentNode)return null;
  const wrap=document.createElement('div');wrap.className='org-title-actions';reload.before(wrap);
  box=document.createElement('div');box.id='org-import-actions';box.className='org-import-actions';
  wrap.append(box,reload);return box;
}
window.orgImportActions=orgImportActions;
function ensureSiteImport(){
  const actions=orgImportActions();if(!actions)return null;let btn=$('#org-site-import-open');
  if(!btn){btn=document.createElement('button');btn.type='button';btn.id='org-site-import-open';btn.textContent='拠点の一括取り込み';btn.onclick=openSiteImport;actions.prepend(btn)}
  return btn;
}
function syncSiteImport(){const btn=ensureSiteImport();if(btn)btn.hidden=!admin()||!selectedRoot()||desk().getMode()!=='organization'}
function openSiteImport(){
  const root=selectedRoot();
  if(!admin()||!root||saving)return;
  if(pending.size)return notice('入力途中の内容を一覧に適用してから取り込んでください。');
  const dialog=document.createElement('dialog');dialog.id='org-site-import-dialog';dialog.setAttribute('aria-labelledby','org-import-title');
  dialog.innerHTML=`<h2 id="org-import-title">拠点の一括取り込み</h2><p>登録先は <strong>${esc(root.name)}</strong> の直下です。</p><p class="small">顧客マスタのExcelから、選んでいる会社の直下に店舗・事業所をまとめて追加します。同じ名前の拠点がすでにある候補は追加しません（末尾の「店」や会社名の有無は同じとみなします）。追加した拠点は「変更をまとめて保存」で確定します。順番は「拠点の取り込み → 保存 → 集配曜日の一括取り込み」です。</p><label>顧客マスタのExcel<input type="file" accept=".xlsx"></label><p class="small">「顧客名」から会社名が一致する候補を探し、末尾の既知の部署名（管理・青果など）を外して拠点名にします。</p><div class="department-import-scroll"></div><p role="alert"></p><div class="actions" data-select-actions hidden><button type="button" data-select-all>未登録の候補をすべて選択</button><button type="button" data-clear-all>選択を解除</button></div><div class="dialog-actions"><button type="button" data-cancel>閉じる</button><button type="button" data-preview class="primary">内容を確認</button><button type="button" data-apply class="primary" hidden>選択した拠点を一覧に追加</button></div>`;
  document.body.append(dialog);
  const file=dialog.querySelector('input[type=file]'),preview=dialog.querySelector('[data-preview]'),apply=dialog.querySelector('[data-apply]'),output=dialog.querySelector('.department-import-scroll'),message=dialog.querySelector('[role=alert]'),selectActions=dialog.querySelector('[data-select-actions]');
  // 会社PC版から：未登録の候補をまとめて選ぶ／外す（登録済みの候補は選べないまま）。
  dialog.querySelector('[data-select-all]').onclick=()=>output.querySelectorAll('input[type=checkbox]:not(:disabled)').forEach(box=>box.checked=true);
  dialog.querySelector('[data-clear-all]').onclick=()=>output.querySelectorAll('input[type=checkbox]').forEach(box=>box.checked=false);
  dialog.addEventListener('close',()=>dialog.remove());
  dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();
  file.onchange=()=>{apply.hidden=selectActions.hidden=true;output.replaceChildren();message.textContent=''};
  preview.onclick=async()=>{
    preview.disabled=file.disabled=true;apply.hidden=selectActions.hidden=true;output.replaceChildren();message.textContent='Excelを読み込んでいます…';
    try{
      const plan=await desk().previewOrganizationSites(root.customer_id,file.files[0]),keys=existingSiteKeys(root);
      message.textContent=plan.candidates.length?`対象 ${plan.matched}行から ${plan.candidates.length}件の候補が見つかりました。拠点名を確認し、不要な候補はチェックを外してください。`:'この会社の拠点候補が見つかりませんでした。Excelの顧客名を確認してください。';
      output.innerHTML=plan.candidates.length?'<table><thead><tr><th>取込</th><th>拠点名</th><th>元の顧客名</th></tr></thead><tbody>'+plan.candidates.map(({name,count,examples,existing,similar,checked},index)=>{const exists=!!existing||siteKeys(name,root.name).some(k=>keys.has(k)),note=exists?'・登録済み（'+esc(existing||name)+'）・追加しません':(similar||[]).length?'・似た拠点あり（'+similar.map(esc).join('、')+'）':'';return `<tr><td><input type="checkbox" ${exists?'disabled':(checked===false?'':'checked')} aria-label="${index+1}件目を取り込む"></td><td><input type="text" maxlength="100" value="${esc(name)}" aria-label="${index+1}件目の拠点名"></td><td>${examples.map(esc).join('<br>')}<br>${count}行${note}</td></tr>`}).join('')+'</tbody></table>':'';
      apply.hidden=selectActions.hidden=!plan.candidates.length;
    }catch(error){message.textContent=error.message}
    finally{preview.disabled=file.disabled=false}
  };
  apply.onclick=()=>{
    if(!admin()||saving||pending.size||selectedRoot()?.id!==root.id){message.textContent='会社の選択と編集中の内容を確認してから、再度取り込んでください。';return}
    const keys=existingSiteKeys(root),additions=[];let duplicates=0;
    for(const row of output.querySelectorAll('tbody tr')){
      const box=row.querySelector('input[type=checkbox]');if(!box.checked||box.disabled)continue;
      const input=row.querySelector('input[type=text]'),name=input.value.trim();
      if(!name||name.length>100){message.textContent='選択した拠点の名前を100文字以内で入力してください。';input.focus();return}
      const sk=siteKeys(name,root.name);
      if(!sk.length||sk.some(k=>keys.has(k))||keys.has('code:'+plainKey(name))||keys.has('other:'+plainKey(name))){duplicates++;continue}
      sk.forEach(k=>keys.add(k));
      additions.push({id:crypto.randomUUID?.()||('org-'+Date.now()+'-'+Math.random().toString(36).slice(2)),kind:'site',parent_id:root.id,customer_id:root.customer_id,name,site_type:'その他'});
    }
    if(!additions.length){message.textContent=duplicates?'選択した候補はすべて同名の拠点があるため、追加するものがありません。':'取り込む拠点を選択してください。';return}
    if(org.nodes.length+additions.length>3000){message.textContent='組織の登録上限を超えています。取り込む拠点を減らしてください。';return}
    org.nodes.push(...additions);siteImported=true;mark();render();dialog.close();
    notice(`${additions.length}件を編集中の一覧に追加しました。${duplicates?'同名の'+duplicates+'件は省きました。':''}「変更をまとめて保存」で確定してください。`);
  };
  dialog.showModal();
}
function patchDirectSiteTexts(){
  const intro=$('#organization .section-title p');if(intro&&intro.textContent.includes('店舗・拠点は必ずカテゴリの中に登録します。'))intro.textContent=intro.textContent.replace('店舗・拠点は必ずカテゴリの中に登録します。','店舗・事業所は会社の直下にも、カテゴリの中にも登録できます。');
  document.querySelectorAll('#organization details p, #organization p').forEach(p=>{if(p.children.length===0&&p.textContent.includes('最初にカテゴリを作り、拠点は必ずどれか1つのカテゴリに登録します。'))p.textContent=p.textContent.replace('最初にカテゴリを作り、拠点は必ずどれか1つのカテゴリに登録します。','カテゴリを使わない場合は、所属先に会社を選んでください。')});
}
function render(){if(pending.size)return;rootOptions();configurePanel(desk().getMode());prepareSelectedRoot();syncSiteImport();patchDirectSiteTexts();tree();scopeSelectors();newNode();$('#org-access-editors').hidden=!admin();$('#org-access-section').hidden=!admin()||!selectedRoot();$('#org-editor').hidden=!selectedRoot();$('#org-node-form').hidden=!admin();$('#org-editor-title').hidden=!admin();$('#org-editor-title').nextElementSibling.hidden=!admin();$('#org-placement').hidden=!admin();$('#org-save').hidden=!admin();setStatus()}
function canLeave(){if(saving||userSaving||$('#web-user-editor').open)return false;if(window.CustomerDirectory&&!window.CustomerDirectory.canLeaveCompany())return false;if((dirty||pending.size)&&!confirm('保存していない組織・利用範囲の変更を破棄しますか？'))return false;if(dirty||pending.size){org=clone(data().organizations||org);dirty=false;moveHistory=[];pending.clear();render()}return true}
function received(){org=clone(data().organizations||{version:0,nodes:[],customer_scopes:[],staff_scopes:[],accounts:[]});org.request_pages??=[];dirty=false;moveHistory=[];syncMoveUndo();pending.clear();selectedCompany='';selectedRegion='';$('#org-company-filter').value='';$('#org-region-filter').value='';filters();desk().refreshCustomers();render();const preview=!!(data().previewAccountId||data().previewPageId);$('#scope-preview-banner').hidden=!preview;$('#scope-preview-label').textContent=preview?(data().previewPageName||data().previewAccountName)+'の表示範囲を確認中'+(data().previewCanRequest?'（依頼可能）':'（閲覧のみ）'):'';for(const name of ['organization','settings','details','staff','setup','web-settings'])document.querySelectorAll(`[data-tab="${name}"]`).forEach(e=>e.hidden=preview||(name==='setup'&&location.pathname!=='/customer-contracts'));$('#request-form button[type=submit]').disabled=data().previewCanRequest===false;if(preview)desk().tab('order');}
$('#org-root-customer').onchange=()=>{if(window.CustomerDirectory&&!window.CustomerDirectory.canLeaveCompany())return false;if(pending.size&&!confirm('入力途中の組織・利用者設定を破棄して顧客を切り替えますか？'))return false;pending.clear();diagramSelection=null;render();desk().syncCommonCustomer();return true};
$('#org-parent').onchange=()=>{mark('node');updatePlacementPreview()};$('#org-kind').onchange=()=>{parentOptions();mark('node')};$('#org-customer-search').oninput=inventoryOptions;$('#org-customer').onchange=()=>{const c=data().customers.find(x=>x.id===Number($('#org-customer').value));if(c)$('#org-name').value=c.name;mark('node')};
$('#org-node-form').onsubmit=e=>{e.preventDefault();if(!admin())return;if(!selectedRoot())return notice('先に顧客を選択してカテゴリ・拠点の設定を開始してください。');if(pending.has('account')||pending.has('staff')||pending.has('page'))return notice('先に入力中の利用範囲・担当範囲を適用してください。');const kind=$('#org-kind').value,parent=$('#org-parent').value||null,name=$('#org-name').value.trim(),customer=Number($('#org-root-customer').value),old=org.nodes.find(n=>n.id===editing);if(kind!=='company'&&!parent)return notice('所属先を選択してください。');if(kind==='site'&&!customer)return notice('在庫顧客を選択してください。');if(org.nodes.some(n=>n.id!==editing&&n.parent_id===parent&&nameKey(n.name)===nameKey(name)))return notice('同じ所属先に同名の組織があります。');if(old&&old.kind!==kind&&org.nodes.some(n=>n.parent_id===old.id))return notice('下に組織があるため、種類は変更できません。');const n={id:editing||crypto.randomUUID?.()||('org-'+Date.now()+'-'+Math.random().toString(36).slice(2)),kind,parent_id:kind==='company'?null:parent,name,customer_id:customer,site_type:kind==='site'?(old?.site_type||'その他'):''};if(old)Object.assign(old,n);else org.nodes.push(n);diagramSelection=n.id;pending.delete('node');mark();tree();scopeSelectors();newNode(true);notice('編集中の一覧に適用しました。最後にまとめて保存してください。')};
$('#org-node-new').onclick=()=>{if(!pending.has('node')||confirm('入力途中の組織を破棄しますか？'))newNode()};
$('#org-node-delete').onclick=()=>{if(!admin()||!editing)return;if(org.nodes.some(n=>n.parent_id===editing))return notice('下にカテゴリ・拠点があります。先にそれらを移動または削除してください。');if((org.request_pages||[]).some(p=>p.node_ids.includes(editing))||org.customer_scopes.some(s=>(s.node_ids||[]).includes(editing))||org.staff_scopes.some(s=>s.node_id===editing))return notice('利用範囲・担当範囲で使用中です。先に範囲の指定を外してください。');if(!confirm('選択した組織を編集中の一覧から削除しますか？在庫の顧客は削除されません。'))return;org.nodes=org.nodes.filter(n=>n.id!==editing);mark();render()};
$('#org-tree').onclick=e=>{const select=e.target.closest('[data-org-select]'),add=e.target.closest('[data-org-add]'),settings=e.target.closest('[data-org-settings]');const jump=e.target.closest('[data-org-jump]');if(jump)selectDiagramNode(jump.dataset.orgJump);if(select){const node=org.nodes.find(n=>n.id===select.dataset.orgSelect);if(node?.kind==='site'&&(e.ctrlKey||e.metaKey||e.shiftKey)){e.preventDefault();toggleSiteSelection(node.id,e);return}if(node?.kind==='site'){selectedSites=new Set([node.id]);lastSiteClick=node.id}else selectedSites.clear();selectDiagramNode(select.dataset.orgSelect);}if(add)addDiagramNode(add.dataset.orgAdd,add.dataset.orgAddParent);if(settings){if(dirty||pending.size)return notice('先に組織・利用範囲をまとめて保存してください。');if(!canLeave())return;$('#org-company-filter').value='';regionFilter();$('#org-region-filter').value='';desk().tab('settings');desk().selectCustomer(Number(settings.dataset.orgSettings));if(settings.dataset.orgScope){$('#scope-select').value=settings.dataset.orgScope;$('#scope-select').onchange()}}};
$('#org-selection').onclick=e=>$('#org-tree').onclick(e);

function syncMoveUndo(){const button=$('#org-move-undo');if(button){button.disabled=!admin()||saving||!moveHistory.length;button.hidden=!admin();$('#org-move-state').textContent=moveHistory.length?'未保存のドラッグ移動：'+moveHistory.length+'件':'移動したら図に反映されます。最後に「まとめて保存」で確定します。'}}
function validDrop(site,parent){return admin()&&!saving&&!pending.size&&site?.kind==='site'&&parent&&['company','region'].includes(parent.kind)&&site.customer_id===parent.customer_id&&site.parent_id!==parent.id&&!org.nodes.some(n=>n.id!==site.id&&n.parent_id===parent.id&&nameKey(n.name)===nameKey(site.name))}
function clearDropHighlights(){document.querySelectorAll('#org-tree .drop-ready,#org-tree .drop-over,#org-tree .dragging').forEach(el=>el.classList.remove('drop-ready','drop-over','dragging'))}
function refreshAfterMove(site){if(site)diagramSelection=site.id;tree();scopeSelectors();if(site&&editing===site.id)editNode(site.id);else parentOptions();setStatus();syncMoveUndo();renderDeliveryBulk()}
function moveSite(site,parent){if(!validDrop(site,parent))return false;moveHistory.push({id:site.id,from:site.parent_id,to:parent.id,dirtyBefore:dirty});if(moveHistory.length>50)moveHistory.shift();site.parent_id=parent.id;dirty=true;refreshAfterMove(site);notice(site.name+'を'+parent.name+'へ移動しました。ドラッグで戻すか「移動を元に戻す」で取り消せます。');return true}
function canBatchMove(sites,parent){if(!parent||!['company','region'].includes(parent.kind))return '移動先の会社・カテゴリを確認してください。';const taken=new Set(org.nodes.filter(n=>n.parent_id===parent.id&&n.kind==='site'&&!sites.some(s=>s.id===n.id)).map(n=>nameKey(n.name)));for(const site of sites){if(site.kind!=='site'||site.customer_id!==parent.customer_id)return '同じ会社の拠点だけをまとめて移動できます。';if(site.parent_id===parent.id)continue;if(taken.has(nameKey(site.name)))return '同じ所属先に同名の拠点があるため、まとめて移動できません。';taken.add(nameKey(site.name))}return ''}
function moveSites(sites,parent){const message=canBatchMove(sites,parent);if(message){notice(message);return false}const moving=sites.filter(s=>s.parent_id!==parent.id);if(!moving.length)return false;const batch=moving.map(s=>({id:s.id,from:s.parent_id,to:parent.id}));moveHistory.push({batch,dirtyBefore:dirty,id:moving[0].id,from:moving[0].parent_id,to:parent.id});if(moveHistory.length>50)moveHistory.shift();moving.forEach(s=>{s.parent_id=parent.id});dirty=true;refreshAfterMove(moving[0]);notice(moving.length===1?moving[0].name+'を'+parent.name+'へ移動しました。':'拠点'+moving.length+'件を'+parent.name+'へ移動しました。最後に「変更をまとめて保存」で確定します。');return true}
function undoLastMove(){const last=moveHistory.pop();if(!last)return;const items=last.batch||[{id:last.id,from:last.from,to:last.to}];items.slice().reverse().forEach(item=>{const site=org.nodes.find(n=>n.id===item.id);if(site)site.parent_id=item.from});dirty=!!last.dirtyBefore||moveHistory.length>0;refreshAfterMove(org.nodes.find(n=>n.id===(items[0]||last).id));notice('移動を元に戻しました。')}
$('#org-tree').addEventListener('dragstart',e=>{const card=e.target.closest('[data-org-select]'),site=org.nodes.find(n=>n.id===card?.dataset.orgSelect);if(!admin()||saving||pending.size||site?.kind!=='site'){e.preventDefault();if(pending.size)notice('入力中の内容を追加・適用するかクリアしてから、ドラッグしてください。');return}if(selectedSites.has(site.id)&&selectedSites.size){draggedSites=currentCompanySites().filter(n=>selectedSites.has(n.id))}else{draggedSites=[site];selectedSites=new Set([site.id])}draggedSite=site.id;e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',site.id);card.classList.add('dragging');document.querySelectorAll('#org-tree [data-org-select][data-org-kind="site"]').forEach(el=>{if(draggedSites.some(n=>n.id===el.dataset.orgSelect))el.classList.add('dragging')});document.querySelectorAll('#org-tree [data-org-drop]').forEach(el=>{if(canBatchMove(draggedSites,org.nodes.find(n=>n.id===el.dataset.orgDrop))==='')el.classList.add('drop-ready')})});
$('#org-tree').addEventListener('dragover',e=>{const card=e.target.closest('[data-org-drop]'),sites=draggedSites.length?draggedSites:[org.nodes.find(n=>n.id===draggedSite)].filter(Boolean),parent=org.nodes.find(n=>n.id===card?.dataset.orgDrop);if((sites.length>1?canBatchMove(sites,parent)!=='':!validDrop(sites[0],parent)))return;e.preventDefault();e.dataTransfer.dropEffect='move';document.querySelectorAll('#org-tree .drop-over').forEach(el=>el.classList.remove('drop-over'));card.classList.add('drop-over')});
$('#org-tree').addEventListener('dragleave',e=>{const card=e.target.closest('[data-org-drop]');if(card&&!card.contains(e.relatedTarget))card.classList.remove('drop-over')});
$('#org-tree').addEventListener('drop',e=>{const card=e.target.closest('[data-org-drop]'),sites=draggedSites.length?draggedSites:[org.nodes.find(n=>n.id===draggedSite)].filter(Boolean),parent=org.nodes.find(n=>n.id===card?.dataset.orgDrop);e.preventDefault();clearDropHighlights();if(sites.length>1)moveSites(sites,parent);else if(sites[0]&&validDrop(sites[0],parent))moveSite(sites[0],parent);draggedSite=null;draggedSites=[]});
$('#org-tree').addEventListener('dragend',()=>{draggedSite=null;draggedSites=[];clearDropHighlights()});

let userEditor=null,userSaving=false,userDirty=false;
async function userApi(body){const response=await fetch('/api/customer-accounts',body?{method:'POST',headers:{'Content-Type':'application/json','X-Desk-Request':'1'},body:JSON.stringify(body)}:{cache:'no-store'}),result=await response.json();if(!response.ok)throw Error(result.error||'利用者情報を保存できませんでした。');return result}
async function openUserEditor(action){if(!admin()||saving||userSaving)return;if(dirty||pending.size)return notice('先に編集中の内容を適用し、「利用者の設定を保存」を押してください。');if(action!=='create'&&!$('#org-account').value)return notice('編集する利用者を選択してください。');try{const state=await userApi(),old=action==='create'?null:state.accounts.find(a=>a.id===Number($('#org-account').value));if(action!=='create'&&!old)throw Error('利用者が見つかりません。再読み込みしてください。');userEditor={action,old};const form=$('#web-user-form');form.reset();$('#web-user-error').textContent='';$('#web-user-title').textContent=action==='create'?'利用する人を新規登録':action==='reset'?'パスワードを再設定':'利用者を編集・停止';form.elements.customer_id.innerHTML='<option value="">顧客を選択してください</option>'+state.customers.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('');for(const key of ['customer_id','name','email','notification_email'])form.elements[key].value=old?.[key]||(key==='customer_id'?$('#org-root-customer').value:'');form.elements.active.value=old?.active===0?'0':'1';$('#web-user-fields').hidden=action==='reset';for(const el of $('#web-user-fields').querySelectorAll('input,select'))el.disabled=action==='reset';$('#web-user-passwords').hidden=action==='update';for(const el of $('#web-user-passwords').querySelectorAll('input')){el.disabled=action==='update';el.required=action!=='update'}userDirty=false;$('#web-user-editor').showModal()}catch(e){notice(e.message)}}
function closeUserEditor(){if(userSaving)return;if(userDirty&&!confirm('保存していない利用者の入力を破棄しますか？'))return;$('#web-user-form').reset();userEditor=null;userDirty=false;$('#web-user-editor').close()}
$('#web-user-new').onclick=()=>openUserEditor('create');$('#web-user-edit').onclick=()=>openUserEditor('update');$('#web-user-reset').onclick=()=>openUserEditor('reset');$('#web-user-cancel').onclick=closeUserEditor;$('#web-user-editor').addEventListener('cancel',e=>{e.preventDefault();closeUserEditor()});$('#web-user-form').oninput=()=>userDirty=true;
$('#web-user-form').onsubmit=async e=>{e.preventDefault();if(userSaving||!userEditor)return;const values=Object.fromEntries(new FormData(e.currentTarget)),payload={...values,action:userEditor.action,active:values.active==='1'};if(userEditor.old){payload.id=userEditor.old.id;payload.version=userEditor.old.version}if(payload.action!=='update'&&values.password!==values.confirm){$('#web-user-error').textContent='パスワードが一致しません。';return}delete payload.confirm;userSaving=true;$('#web-user-submit').disabled=true;$('#web-user-cancel').disabled=true;$('#web-user-error').textContent='';try{const result=await userApi(payload),saved=result.id||payload.id;userDirty=false;userEditor=null;$('#web-user-form').reset();$('#web-user-editor').close();try{await desk().loadSettings();if(saved){$('#org-account').value=String(saved);accountForm();}notice('利用者を保存しました。続けて、その人が見られる範囲を設定できます。')}catch(refreshError){notice('利用者は保存済みです。一覧を再読み込みしてください。')}}catch(err){$('#web-user-error').textContent=err.message}finally{userSaving=false;$('#web-user-submit').disabled=false;$('#web-user-cancel').disabled=false}};
window.addEventListener('beforeunload',e=>{if(userDirty||userSaving){e.preventDefault();e.returnValue=''}});
$('#org-account').onchange=()=>{if(pending.has('account')&&!confirm('未適用の利用範囲を破棄しますか？'))return;accountForm();setStatus()};$('#org-staff').onchange=()=>{if(pending.has('staff')&&!confirm('未適用の担当範囲を破棄しますか？'))return;staffForm();setStatus()};
$('#org-account-nodes').onchange=()=>mark('account');$('#org-can-request').onchange=()=>mark('account');$('#org-staff-nodes').onchange=()=>{$('#org-staff-mode').value='individual';mark('staff')};
$('#org-account-apply').onclick=()=>{const aid=Number($('#org-account').value);if(!admin()||!aid)return notice('利用者を選択してください。');org.customer_scopes=org.customer_scopes.filter(s=>s.account_id!==aid);org.customer_scopes.push({account_id:aid,node_ids:[...$('#org-account-nodes').querySelectorAll('input:checked')].map(x=>x.value),can_request:$('#org-can-request').checked});pending.delete('account');mark();notice('この利用者の範囲を適用しました。最後に「利用者の設定を保存」を押してください。')};
function removeStaff(all){const uid=Number($('#org-staff').value),root=selectedRoot();if(!admin()||!uid||!root)return;const nodes=staffNodes(),ids=new Set(nodes.map(n=>n.id)),remove=new Set([...$('#org-staff-nodes').querySelectorAll('input:checked')].map(e=>e.value));if(!all&&!remove.size)return notice('担当を外す拠点にチェックしてください。');const covered=new Set(org.staff_scopes.filter(s=>s.user_id===uid&&ids.has(s.node_id)).flatMap(s=>[...descendants(s.node_id)]));const remaining=all?[]:nodes.filter(n=>n.kind==='site'&&covered.has(n.id)&&!remove.has(n.id)).map(n=>({user_id:uid,node_id:n.id}));org.staff_scopes=org.staff_scopes.filter(s=>s.user_id!==uid||!ids.has(s.node_id)).concat(remaining);org.staff_scope_settings=(org.staff_scope_settings||[]).filter(s=>s.user_id!==uid||s.customer_id!==root.customer_id).concat([{user_id:uid,customer_id:root.customer_id}]);pending.delete('staff');mark();staffForm();tree();notice('選択した担当者を'+(all?'全拠点':'チェックした拠点')+'から外しました。最後にまとめて保存してください。')}
$('#org-staff-remove-all').onclick=()=>removeStaff(true);$('#org-staff-remove-selected').onclick=()=>removeStaff(false);
$('#org-staff-apply').onclick=()=>{const uid=Number($('#org-staff').value),root=selectedRoot(),ids=new Set(staffNodes().map(n=>n.id));if(!admin()||!uid||!root)return notice('顧客と社内担当者を選択してください。');org.staff_scopes=org.staff_scopes.filter(s=>s.user_id!==uid||!ids.has(s.node_id)).concat(($('#org-staff-mode').value==='all'?[root.id]:[...$('#org-staff-nodes').querySelectorAll('input:checked')].map(x=>x.value)).map(id=>({user_id:uid,node_id:id})));org.staff_scope_settings=(org.staff_scope_settings||[]).filter(s=>s.user_id!==uid||s.customer_id!==root.customer_id).concat([{user_id:uid,customer_id:root.customer_id}]);pending.delete('staff');mark();tree();notice('担当範囲を一覧に適用しました。最後にまとめて保存してください。')};
$('#org-save').onclick=async()=>{if(!admin()||saving)return;if(pending.size)return notice('入力中の組織・利用範囲を、各欄の追加／適用ボタンで一覧に適用してください。');saving=true;setStatus();document.querySelectorAll('#organization input,#organization select,#organization button,#web-settings input,#web-settings select,#web-settings button,[data-tab]').forEach(e=>e.disabled=true);try{const payload=clone(org);payload.nodes=org.nodes.filter(n=>!n._prepared||rootInUse(n)).map(n=>{const copy={...n};delete copy._prepared;return copy});await desk().saveOrganization(payload,org.version,{backup:siteImported});siteImported=false;notice(desk().getMode()==='web-settings'?'WEB依頼の利用者設定を保存しました。':'企業・カテゴリ・拠点と利用範囲を保存しました。')}catch(e){notice(e.message)}finally{saving=false;document.querySelectorAll('#organization input,#organization select,#organization button,#web-settings input,#web-settings select,#web-settings button,[data-tab]').forEach(e=>e.disabled=false);$('#org-node-delete').disabled=!editing;parentOptions();setStatus();syncMoveUndo()}};
const pageSave=$('#web-page-save');if(pageSave)pageSave.onclick=()=>$('#org-save').onclick();const pageReload=$('#web-page-reload');if(pageReload)pageReload.onclick=()=>$('#org-reload').onclick();
$('#org-reload').onclick=()=>{if(canLeave())desk().loadSettings().catch(e=>notice(e.message))};
$('#org-account-preview').onclick=async()=>{const aid=Number($('#org-account').value);if(!aid)return notice('利用者を選択してください。');if(dirty||pending.size)return notice('組織・利用範囲を保存してから確認してください。');try{await desk().loadSettings(aid)}catch(e){notice(e.message)}};
$('#scope-preview-exit').onclick=()=>desk().loadSettings().catch(e=>notice(e.message));
let selectedCompany='',selectedRegion='';$('#org-company-filter').onchange=()=>{const next=$('#org-company-filter').value;regionFilter();if(!desk().refreshCustomers()){$('#org-company-filter').value=selectedCompany;regionFilter();$('#org-region-filter').value=selectedRegion;return}selectedCompany=next;selectedRegion=$('#org-region-filter').value};$('#org-region-filter').onchange=()=>{if(!desk().refreshCustomers()){$('#org-region-filter').value=selectedRegion;return}selectedRegion=$('#org-region-filter').value};
window.addEventListener('beforeunload',e=>{if(dirty||pending.size){e.preventDefault();e.returnValue=''}});window.addEventListener('web-request-data',received);function toggleSiteSelection(id,e){
  const order=visibleSites();
  if(e.shiftKey&&lastSiteClick&&order.includes(lastSiteClick)&&order.includes(id)){
    const a=order.indexOf(lastSiteClick),b=order.indexOf(id),from=Math.min(a,b),to=Math.max(a,b);
    if(!e.ctrlKey&&!e.metaKey)selectedSites=new Set();
    for(let i=from;i<=to;i++)selectedSites.add(order[i]);
  }else{
    if(selectedSites.has(id))selectedSites.delete(id);else selectedSites.add(id);
    lastSiteClick=id;
  }
  diagramSelection=id;
  tree();
  renderDeliveryBulk();
}
function ensureOrgToolbars(){
  const treeEl=$('#org-tree');
  if(!treeEl)return;
  if(!$('#org-select-toolbar')){
    const bar=document.createElement('div');
    bar.id='org-select-toolbar';
    bar.className='org-select-toolbar';
    bar.innerHTML='<div class="org-move-toolbar"><span>拠点の複数選択</span><strong id="org-select-count"></strong><button type="button" id="org-select-all">すべてチェック</button><button type="button" id="org-select-none">すべてチェックを外す</button><button type="button" id="org-move-undo">移動を元に戻す</button><p id="org-move-state"></p><p class="small">Ctrl（または⌘）クリック、Shiftクリック、図の余白をドラッグして複数の拠点を選べます。選んだ拠点はまとめて移動・配送設定できます。</p></div>';
    const panel=treeEl.closest('.org-diagram-panel')||treeEl.parentElement;
    panel.insertBefore(bar, treeEl);
    $('#org-select-all').onclick=()=>selectAllSites(true);
    $('#org-select-none').onclick=()=>selectAllSites(false);
    $('#org-move-undo').onclick=()=>undoLastMove();
  }
  if(!$('#org-delivery-bulk')){
    const box=document.createElement('section');
    box.id='org-delivery-bulk';
    box.className='org-delivery-bulk request-record';
    box.hidden=true;
    box.innerHTML='<h3>選んだ拠点の配送設定</h3><p class="small" id="org-delivery-count"></p><label>配送方法<select id="org-delivery-method"><option value="">未設定</option><option value="自社集配">自社集配</option><option value="福山便">福山便</option><option value="佐川便">佐川便</option><option value="ヤマト便">ヤマト便</option></select></label><fieldset id="org-collection-days" hidden><legend>集配曜日（月〜日・複数可）</legend>'+['月','火','水','木','金','土','日'].map(d=>'<label class="check"><input type="checkbox" value="'+d+'">'+d+'</label>').join('')+'</fieldset><div class="actions"><button type="button" class="primary" id="org-delivery-apply">選んだ拠点に適用</button></div><p class="small">適用した内容は、下の「変更をまとめて保存」で確定します。</p>';
    const editor=$('#org-editor');
    if(editor)editor.insertBefore(box, editor.firstChild);
    $('#org-delivery-method').onchange=()=>{$('#org-collection-days').hidden=$('#org-delivery-method').value!=='自社集配'};
    $('#org-delivery-apply').onclick=applyDeliveryBulk;
  }
  if(!$('#org-staff-from-diagram')&&$('#org-staff-none')){
    const wrap=document.createElement('span');wrap.id='org-staff-from-diagram';wrap.className='org-staff-from-diagram';
    wrap.innerHTML='<button type="button" id="org-staff-diagram-add">図で選んだ拠点をチェック</button><button type="button" id="org-staff-diagram-only">図で選んだ拠点だけにする</button>';
    $('#org-staff-none').after(wrap);
    $('#org-staff-diagram-add').onclick=()=>staffFromDiagram(false);
    $('#org-staff-diagram-only').onclick=()=>staffFromDiagram(true);
  }
  // 「選んだ拠点の配送設定」は「社内担当者を設定」のすぐ下に置く。
  const access=$('#org-access-section'),bulk=$('#org-delivery-bulk');
  if(access&&bulk&&access.nextElementSibling!==bulk)access.after(bulk);
  syncStaffDiagramButtons();
  bindRubberBand();
}
// 図で選んだ拠点（範囲選択・Ctrl/Shiftクリック）を、社内担当者の担当範囲のチェックに反映する。確定は「担当範囲を一覧に適用」→「変更をまとめて保存」。
function staffFromDiagram(only){
  const ids=new Set(selectedSiteNodes().map(n=>n.id));
  if(!admin()||!Number($('#org-staff').value))return notice('先に社内担当者を選んでください。');
  if(!ids.size)return notice('先に図で拠点を選んでください（余白をドラッグ、またはCtrl/Shiftクリック）。');
  $('#org-staff-mode').value='individual';
  $('#org-staff-nodes').querySelectorAll('input').forEach(e=>{if(ids.has(e.value))e.checked=true;else if(only)e.checked=false});
  mark('staff');
  notice('図で選んだ拠点'+ids.size+'件を担当範囲にチェックしました。「担当範囲を一覧に適用」を押してから、最後に「変更をまとめて保存」で確定します。');
}
function syncStaffDiagramButtons(){
  const n=selectedSiteNodes().length,disabled=!admin()||!Number($('#org-staff')?.value)||!n;
  for(const [id,label] of [['org-staff-diagram-add','図で選んだ拠点をチェック'],['org-staff-diagram-only','図で選んだ拠点だけにする']]){const b=$('#'+id);if(!b)continue;b.disabled=disabled;b.textContent=label+(n?'（'+n+'件）':'')}
}
function syncSelectToolbar(){
  const all=$('#org-select-all'),none=$('#org-select-none');
  if(!all)return;
  const sites=currentCompanySites();
  all.disabled=!admin()||!sites.length;
  none.disabled=!admin()||!selectedSites.size;
  const count=$('#org-select-count');if(count)count.textContent=selectedSiteNodes().length?'選択中 '+selectedSiteNodes().length+'件':'';
  syncMoveUndo();
}
function selectedSiteNodes(){return currentCompanySites().filter(n=>selectedSites.has(n.id))}
function renderDeliveryBulk(){
  syncStaffDiagramButtons();syncSelectToolbar();
  const box=$('#org-delivery-bulk');
  if(!box)return;
  const sites=selectedSiteNodes();
  box.hidden=!admin()||!sites.length;
  if($('#org-delivery-count'))$('#org-delivery-count').textContent=sites.length?('選択中の拠点：'+sites.length+'件。配送方法を選んで適用します。'):'';
  if(!sites.length)return;
  const methods=[...new Set(sites.map(n=>n.delivery_method||''))];
  $('#org-delivery-method').value=methods.length===1?methods[0]:'';
  $('#org-collection-days').hidden=$('#org-delivery-method').value!=='自社集配';
  const daySet=new Set();
  let mixed=false;
  sites.forEach((n,i)=>{
    const days=n.collection_days||[];
    if(i===0)days.forEach(d=>daySet.add(d));
    else if(days.join()!==[...daySet].join())mixed=true;
  });
  $('#org-collection-days').querySelectorAll('input').forEach(inp=>{inp.checked=!mixed&&daySet.has(inp.value)});
}
function applyDeliveryBulk(){
  const sites=selectedSiteNodes();
  if(!sites.length)return notice('先に拠点を選んでください。');
  const method=$('#org-delivery-method').value;
  if(method&&!['自社集配','福山便','佐川便','ヤマト便'].includes(method))return notice('配送方法を確認してください。');
  const days=method==='自社集配'?[...$('#org-collection-days').querySelectorAll('input:checked')].map(x=>x.value):[];
  sites.forEach(n=>{n.delivery_method=method;n.collection_days=days});
  mark();
  tree();
  renderDeliveryBulk();
  notice('選んだ拠点'+sites.length+'件に配送設定を適用しました。最後に「変更をまとめて保存」を押してください。');
}
function bindRubberBand(){
  const canvas=$('#org-tree');
  if(!canvas||canvas.dataset.rubberBound)return;
  canvas.dataset.rubberBound='1';
  canvas.addEventListener('mousedown',e=>{
    if(e.button!==0||!admin())return;
    if(e.target.closest('[data-org-select],[data-org-add],[data-org-settings],[data-org-jump],.org-drop-tray,.org-add-card,button,input,select,label,a'))return;
    e.preventDefault();
    const rect=canvas.getBoundingClientRect();
    band={x:e.clientX,y:e.clientY,ox:e.clientX-rect.left+canvas.scrollLeft,oy:e.clientY-rect.top+canvas.scrollTop};
    let box=document.getElementById('org-rubber-band');
    if(!box){box=document.createElement('div');box.id='org-rubber-band';canvas.appendChild(box)}
    const style=getComputedStyle(canvas);
    if(style.position==='static')canvas.style.position='relative';
    const move=ev=>{
      if(!band)return;
      const r=canvas.getBoundingClientRect();
      const x=Math.min(band.x,ev.clientX)-r.left+canvas.scrollLeft,y=Math.min(band.y,ev.clientY)-r.top+canvas.scrollTop;
      const w=Math.abs(ev.clientX-band.x),h=Math.abs(ev.clientY-band.y);
      Object.assign(box.style,{display:'block',left:x+'px',top:y+'px',width:w+'px',height:h+'px'});
    };
    const up=ev=>{
      document.removeEventListener('mousemove',move);
      document.removeEventListener('mouseup',up);
      if(!band)return;
      const w=Math.abs(ev.clientX-band.x),h=Math.abs(ev.clientY-band.y);
      box.style.display='none';
      if(w>4||h>4){
        const left=Math.min(band.x,ev.clientX),top=Math.min(band.y,ev.clientY),right=left+Math.max(w,1),bottom=top+Math.max(h,1);
        if(!ev.ctrlKey&&!ev.metaKey&&!ev.shiftKey)selectedSites=new Set();
        document.querySelectorAll('#org-tree [data-org-kind="site"]').forEach(el=>{
          const r=el.getBoundingClientRect();
          if(r.right>=left&&r.left<=right&&r.bottom>=top&&r.top<=bottom)selectedSites.add(el.dataset.orgSelect);
        });
        if(selectedSites.size){const first=[...selectedSites][0];diagramSelection=first;lastSiteClick=first}
        tree();
        renderDeliveryBulk();
      }else if(!ev.ctrlKey&&!ev.metaKey&&!ev.shiftKey&&selectedSites.size){
        // 余白をクリック（ほぼ動かさない）したら、拠点の複数選択だけを解除する（未保存の変更はそのまま）。
        selectedSites=new Set();lastSiteClick=null;
        tree();
        renderDeliveryBulk();
      }
      band=null;
    };
    document.addEventListener('mousemove',move);
    document.addEventListener('mouseup',up);
  });
}

window.WebRequestOrganization={companySaved:(id,body)=>{rootOptions();$('#org-root-customer').value=String(id);diagramSelection=null;render();const root=selectedRoot();if(root){root.name=body.name;for(const key of nodeFields)root[key]=body[key==='office_code'?'officeCode':key]||'';delete root._prepared;mark();tree()}desk().syncCommonCustomer()},hasChanges:()=>dirty||pending.size>0,render,configurePanel,canLeave,matchesCustomer,customerPath,customerLabel:()=>undefined};if(data().organizations)received();
$('#org-root-search').onfocus=()=>{rootCandidates(true);$('#org-root-search').select()};$('#org-root-search').oninput=e=>{if(!e.isComposing)rootCandidates()};$('#org-root-search').addEventListener('compositionend',()=>rootCandidates());$('#org-root-search').onblur=syncRootSearch;
$('#org-root-search').onkeydown=e=>{if(e.isComposing)return;if(e.key==='Escape'){e.preventDefault();syncRootSearch();return}if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();if($('#org-root-options').hidden)rootCandidates(true);if(!rootMatches.length)return;rootCursor=Math.max(0,Math.min(rootMatches.length-1,rootCursor+(e.key==='ArrowDown'?1:-1)));$('#org-root-options').querySelectorAll('[role=option]').forEach((x,i)=>x.classList.toggle('highlighted',i===rootCursor));const item=$('#org-root-option-'+rootCursor);$('#org-root-search').setAttribute('aria-activedescendant',item.id);item.scrollIntoView({block:'nearest'})}if(e.key==='Enter'&&!$('#org-root-options').hidden){e.preventDefault();if(rootCursor>=0)chooseRoot(rootMatches[rootCursor].id);else if(rootMatches.length===1)chooseRoot(rootMatches[0].id)}};
$('#org-root-options').onpointerdown=e=>{const item=e.target.closest('[data-org-customer-id]');if(item){e.preventDefault();chooseRoot(Number(item.dataset.orgCustomerId))}};
function openRootList(){closeRootSearch();desk().openCustomerList({selectedId:()=>Number($('#org-root-customer').value),select:chooseRoot,close:closeRootSearch})}$('#org-root-list').onclick=openRootList;$('.org-root-picker').oncontextmenu=e=>{e.preventDefault();openRootList()};
function pageSelectors(){const old=$('#org-request-page').value,cid=Number($('#org-root-customer').value);$('#org-request-page').innerHTML='<option value="">新しいページを作成</option>'+(org.request_pages||[]).filter(p=>p.customer_id===cid).map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');if([...$('#org-request-page').options].some(o=>o.value===old))$('#org-request-page').value=old;pageForm()}
function pageForm(){displayedPage=$('#org-request-page').value;const page=(org.request_pages||[]).find(p=>p.id===$('#org-request-page').value),cid=Number($('#org-root-customer').value);$('#org-page-name').value=page?.name||'';$('#org-page-nodes').innerHTML=org.nodes.filter(n=>n.customer_id===cid).map(n=>`<label class="org-scope"><input type="checkbox" value="${esc(n.id)}" ${(page?.node_ids||[]).includes(n.id)?'checked':''}>${esc(nodePath(n.id))}</label>`).join('')||'<p>上で顧客を選び、カテゴリ・店舗を登録してください。</p>';$('#org-page-can-request').checked=page?.can_request??true;pending.delete('page');setStatus()}
$('#org-request-page').onchange=()=>{if(pending.has('page')&&!confirm('未適用のページ設定を破棄しますか？')){$('#org-request-page').value=displayedPage;return}pageForm()};$('#org-page-name').oninput=()=>mark('page');$('#org-page-nodes').onchange=()=>mark('page');$('#org-page-can-request').onchange=()=>mark('page');
$('#org-page-apply').onclick=()=>{if(!admin())return;if(pending.has('node')||pending.has('account')||pending.has('staff'))return notice('先に入力中の組織や利用者の設定を適用してください。');const customer=Number($('#org-root-customer').value),name=$('#org-page-name').value.trim(),id=$('#org-request-page').value||crypto.randomUUID?.()||('page-'+Date.now());if(!selectedRoot())return notice('上で顧客を選択してください。');if(!name||name.length>100)return notice('ページ名を100文字以内で入力してください。');org.request_pages??=[];if(org.request_pages.some(p=>p.id!==id&&p.customer_id===customer&&nameKey(p.name)===nameKey(name)))return notice('この顧客には同名のページがあります。');const value={id,name,customer_id:customer,node_ids:[...$('#org-page-nodes').querySelectorAll('input:checked')].map(x=>x.value),can_request:$('#org-page-can-request').checked},old=org.request_pages.find(p=>p.id===id);if(old)Object.assign(old,value);else org.request_pages.push(value);pending.delete('page');mark();scopeSelectors();$('#org-request-page').value=id;pageForm();notice('ページを編集中の一覧に適用しました。「ページ・利用者の設定を保存」で保存してください。顧客IDは後から紐づけられます。')};
$('#org-page-preview').onclick=async()=>{const id=$('#org-request-page').value;if(!id)return notice('作成したページを選択してください。');if(dirty||pending.size)return notice('先に「ページ・利用者の設定を保存」を押してください。');try{await desk().loadSettings(null,id)}catch(e){notice(e.message)}};
$('#org-page-delete').onclick=()=>{const id=$('#org-request-page').value;if(!admin()||!id)return notice('削除するページを選んでください。');if(org.customer_scopes.some(s=>s.page_id===id))return notice('このページを利用する人が設定されています。先にその人の利用ページを変更してください。');if(!confirm('このページを編集中の一覧から削除しますか？制服・拠点は削除されません。'))return;org.request_pages=org.request_pages.filter(p=>p.id!==id);pending.delete('page');mark();scopeSelectors()};
$('#org-account-page').onchange=()=>{$('#org-account-direct-fields').hidden=!!$('#org-account-page').value;mark('account')};
if(!document.getElementById('collection-days-import-js')){const sc=document.createElement('script');sc.id='collection-days-import-js';sc.src='/collection-days-import.js?v=20261126_collection_import';document.head.appendChild(sc)}
})();

