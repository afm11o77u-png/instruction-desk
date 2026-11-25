/* 集配曜日の一括取り込み（組織構成・担当者の画面）。主管理者・管理者のみ。
   ファイルを選ぶ → プレビュー（保存しない） → 「この内容で取り込む」で保存。 */
(()=>{'use strict';
const $=s=>document.querySelector(s),desk=()=>window.WebRequestDesk,esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let file=null,preview=null,busy=false;
const isAdmin=()=>{const d=desk()?.getData?.();return !!(d&&d.isAdmin&&!d.previewAccountId&&!d.previewPageId)};
async function post(body){const r=await fetch('/api/web-request/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Desk-Request':'1'},body:JSON.stringify(body)});let result={};try{result=await r.json()}catch(e){}if(!r.ok)throw Error(result.error||'取り込みに失敗しました。');return result}
function readFile(f){return new Promise((ok,ng)=>{const fr=new FileReader();fr.onload=()=>ok(String(fr.result).split(',',2)[1]||'');fr.onerror=()=>ng(Error('ファイルを読み取れませんでした。'));fr.readAsDataURL(f)})}
function ensure(){
  const actions=window.orgImportActions?.();if(!actions)return null;
  let btn=$('#collection-import-open');
  if(!btn){btn=document.createElement('button');btn.type='button';btn.id='collection-import-open';btn.textContent='集配曜日の一括取り込み';btn.onclick=open;actions.append(btn)}
  let box=$('#collection-import');
  if(!box){
    box=document.createElement('dialog');box.id='collection-import';box.className='collection-import';box.setAttribute('aria-labelledby','collection-import-title');
    box.innerHTML='<h2 id="collection-import-title">集配曜日の一括取り込み</h2><p class="small">顧客マスタ（Excel・CSV）の「集配曜日フラグ」から、拠点の配送方法と集配曜日をまとめて登録します。拠点は営業所コード＝顧客コード、なければ拠点名＝顧客名の店名で探します（末尾の「店」や会社名の有無は同じとみなします）。登録済みの拠点だけが対象で、新しい拠点は作りません。先に「拠点の一括取り込み」で拠点を追加して保存してから行ってください。プレビューを表示し、「この内容で取り込む」を押すまで保存しません。</p><div class="actions"><input id="collection-import-file" type="file" accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"><button type="button" id="collection-import-preview">プレビュー</button></div><div id="collection-import-result"></div><div class="dialog-actions"><button type="button" id="collection-import-close">閉じる</button></div>';
    document.body.append(box);
    $('#collection-import-file').onchange=()=>{file=$('#collection-import-file').files[0]||null;preview=null;show()};
    $('#collection-import-preview').onclick=runPreview;
    $('#collection-import-close').onclick=()=>box.close();
  }
  return btn;
}
function open(){
  if(!isAdmin())return;
  if(window.WebRequestOrganization?.hasChanges?.())desk().notify('組織構成に未保存の変更があります。取り込む前に「変更をまとめて保存」で保存してください。');
  const box=$('#collection-import');if(box&&!box.open)box.showModal();
}
function table(head,rows){return rows.length?'<div class="collection-import-scroll"><table class="collection-import-table"><thead><tr>'+head.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>':'<p class="small">ありません。</p>'}
function show(){
  const out=$('#collection-import-result');if(!out)return;
  if(!preview){out.innerHTML='';return}
  const p=preview,n=p.counts;
  const changes=p.changes.map(x=>[x.codes.join('、'),x.customers.join('、'),x.site,x.before+' → '+x.after]);
  const flagRow=x=>[x.row+'行目',x.code,x.customer,x.flag||'（空）',x.cycle||'（空）',x.site||'',x.reason];
  out.innerHTML='<p class="collection-import-summary"><b>一致した拠点 '+n.matched_sites+'件</b>（変更 '+n.changes+'件・変更なし '+n.unchanged+'件）／ 一致しない '+n.unmatched+'行 ／ 要確認 '+n.review+'行 ／ 読み飛ばし '+n.skipped+'行（全 '+n.rows+'行）</p>'
   +'<h4>変更する拠点（'+n.changes+'件）</h4>'+table(['顧客コード','顧客名','拠点','変更前 → 変更後'],changes)
   +'<details'+(p.unmatched.length?' open':'')+'><summary>一致しない顧客コード（'+n.unmatched+'行）</summary>'+table(['行','顧客コード','顧客名','集配曜日フラグ','集配サイクル','拠点','理由'],p.unmatched.map(flagRow))+'</details>'
   +'<details'+(p.review.length?' open':'')+'><summary>要確認・変更しない（'+n.review+'行）</summary>'+table(['行','顧客コード','顧客名','集配曜日フラグ','集配サイクル','拠点','理由'],p.review.map(flagRow))+'</details>'
   +'<details><summary>読み飛ばし（'+n.skipped+'行）</summary>'+table(['行','顧客コード','顧客名','集配曜日フラグ','集配サイクル','拠点','理由'],p.skipped.map(flagRow))+'</details>'
   +'<div class="actions"><button type="button" class="primary" id="collection-import-apply" '+(n.changes&&!busy?'':'disabled')+'>この内容で取り込む（'+n.changes+'件）</button><button type="button" id="collection-import-cancel">やめる</button></div>';
  $('#collection-import-apply').onclick=runApply;
  $('#collection-import-cancel').onclick=()=>{preview=null;show()};
}
async function runPreview(){
  if(busy)return;if(!file)return desk().notify('取り込むファイルを選んでください。');
  if(!/\.(xlsx|csv)$/i.test(file.name))return desk().notify('Excel（.xlsx）またはCSVを選んでください。');
  if(file.size>8000000)return desk().notify('ファイルは8MB以下にしてください。');
  busy=true;try{const raw=await readFile(file);const r=await post({action:'collection_days_import_preview',file:raw,filename:file.name});preview=r.collectionImport;preview._raw=raw;preview._name=file.name}catch(e){preview=null;desk().notify(e.message)}finally{busy=false;show()}
}
async function runApply(){
  if(busy||!preview)return;
  if(window.WebRequestOrganization?.hasChanges?.())return desk().notify('組織構成に未保存の変更があります。先に「変更をまとめて保存」で保存するか、変更を取り消してから取り込んでください。');
  if(!confirm(preview.counts.changes+'件の拠点の配送方法・集配曜日を変更します。よろしいですか？'))return;
  busy=true;show();
  try{const r=await post({action:'collection_days_import',file:preview._raw,filename:preview._name,confirm:true,organization_version:preview.organization_version,expected_changes:preview.counts.changes});const saved=r.collectionImport?.saved||0;preview=null;file=null;const input=$('#collection-import-file');if(input)input.value='';await desk().loadSettings();$('#collection-import')?.close();desk().notify('集配曜日を'+saved+'件の拠点に取り込みました。')}
  catch(e){desk().notify(e.message)}finally{busy=false;show()}
}
function render(){const btn=ensure();if(!btn)return;btn.hidden=!isAdmin()}
window.CollectionDaysImport={render};
window.addEventListener('web-request-data',render);
render();
})();
