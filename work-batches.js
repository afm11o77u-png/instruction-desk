const workBatchContent = $('#workbatches-content');
const workBatchMessage = $('#workbatches-message');

function updateWorkBatchButtons() {
  const allowed = ['owner', 'floor'].includes(currentUser?.role);
  $('#workbatches-open').hidden = !allowed;
  $('#workbatches-create').hidden = !allowed || view !== 'floor';
}

function workBatchTable(headers, rows) {
  return '<div class="tablewrap"><table><thead><tr>' +
    headers.map(label => `<th>${esc(label)}</th>`).join('') +
    '</tr></thead><tbody>' +
    rows.map(row => '<tr>' + row.map(cell => `<td>${cell}</td>`).join('') + '</tr>').join('') +
    '</tbody></table></div>';
}

function showWorkBatchContent(title) {
  $('#workbatches-title').textContent = title;
  workBatchMessage.textContent = '';
  workBatchContent.replaceChildren();
}

async function openWorkBatches(batchId = 0) {
  showWorkBatchContent('作業のまとめ');
  workBatchMessage.textContent = '読み込み中…';
  try {
    const batches = await api('/api/picking/work-batches');
    const byId = new Map(orders.map(order => [order.id, order]));
    workBatchMessage.textContent = batches.length ? '' : '作業のまとめはまだありません。';
    if (!batches.length) return;
    workBatchContent.innerHTML = workBatchTable(
      ['まとめ', '指示書・印刷', 'ピッキング', '管理担当持込', '操作'],
      batches.map(batch => {
        const printed = batch.sources.filter(source => byId.get(source.id)?.printedAt).length;
        const carried = batch.rows.filter(row => row.stock === 'carry').length;
        return [
          `<strong>${esc(batch.title)}</strong><br>${esc(batch.number)}　${esc(batch.date)}`,
          `${printed}/${batch.sources.length}件印刷済み<br><button type="button" data-print-batch="${batch.id}">指示書・印刷</button>`,
          `${esc(batch.phase)}<br><a href="/?view=picking&amp;board=1&amp;list=${batch.id}" data-work-batch-link="${batch.id}">ピッキングリストを開く</a>`,
          `${carried}明細<br><button type="button" data-carry-batch="${batch.id}">持込リストを開く</button>`,
          currentUser.role === 'owner' ? `<button type="button" data-reset-batch="${batch.id}">テスト用リセット</button>` : ''
        ];
      })
    );
    if (currentUser.role === 'owner') {
      const bulkReset = document.createElement('button');
      bulkReset.type = 'button';
      bulkReset.textContent = 'テスト用：ピッキング依頼を一括リセット';
      bulkReset.onclick = () => openWorkBatchReset(batches);
      workBatchContent.prepend(bulkReset);
    }
    const returnLink = workBatchContent.querySelector(`[data-work-batch-link="${batchId}"]`);
    returnLink?.focus();
    for (const batch of batches) {
      workBatchContent.querySelector(`[data-print-batch="${batch.id}"]`).onclick = () => showWorkBatchPrint(batch, byId);
      const resetButton = workBatchContent.querySelector(`[data-reset-batch="${batch.id}"]`);
      if (resetButton) resetButton.onclick = () => openTestReset(batch.id, openWorkBatches);
      workBatchContent.querySelector(`[data-carry-batch="${batch.id}"]`).onclick = () => showWorkBatchCarry(batch);
    }
  } catch (error) {
    workBatchMessage.textContent = error.message;
  }
}

function openWorkBatchReset(batches) {
  repairDialog.innerHTML = `<div class="dialoghead"><h2>テスト用：ピッキング依頼を一括リセット</h2>
    <button type="button" id="repair-close">閉じる</button></div>
    <form id="batch-reset-form" class="repairbody">
      <p class="testwarn">${esc(testResetWarning)}</p>
      <label class="checklabel"><input type="checkbox" id="batch-reset-all">一覧のリストをすべて選択</label>
      ${workBatchTable(['選択', 'リスト番号', '保存名', '状態', '指示書'], batches.map(batch => [
        `<input type="checkbox" name="batch" value="${batch.id}" aria-label="${esc(batch.number)}">`,
        esc(batch.number), esc(batch.title), esc(batch.phase), `${batch.sources.length}件`
      ]))}
      <label class="checklabel"><input type="checkbox" id="batch-reset-revert" checked>手配完了で作業中になった指示書を未着手に戻す</label>
      <label class="repairreason">修正理由（必須）<textarea id="batch-reset-reason" maxlength="500" required></textarea></label>
      <label class="checklabel"><input type="checkbox" required>選択したリストはすべて、実際の倉庫へ依頼していないテストデータです</label>
      <p id="repair-message" role="status"></p>
      <button type="submit" class="primary">対象を確認してリセット</button>
    </form>`;
  const form = repairDialog.querySelector('#batch-reset-form');
  const selectAll = form.querySelector('#batch-reset-all');
  const choices = [...form.querySelectorAll('[name="batch"]')];
  selectAll.onchange = () => {
    choices.forEach(choice => { choice.checked = selectAll.checked; });
    selectAll.indeterminate = false;
  };
  for (const choice of choices) {
    choice.onchange = () => {
      const count = choices.filter(item => item.checked).length;
      selectAll.checked = count === choices.length;
      selectAll.indeterminate = count > 0 && !selectAll.checked;
    };
  }
  repairDialog.querySelector('#repair-close').onclick = closeRepair;
  repairDialog.oncancel = event => { event.preventDefault(); closeRepair(); };
  form.onsubmit = event => {
    event.preventDefault();
    repairRun(async () => {
      const selectedIds = new Set(choices.filter(choice => choice.checked).map(choice => Number(choice.value)));
      const selected = batches.filter(batch => selectedIds.has(batch.id));
      if (!selected.length) throw new Error('対象のピッキングリストを選択してください。');
      const reason = form.querySelector('#batch-reset-reason').value.trim();
      if (!reason) throw new Error('修正理由を入力してください。');
      const revertStatus = form.querySelector('#batch-reset-revert').checked;
      const endpoint = '/api/picking/test-reset/batch';
      const preview = await api(endpoint, {
        preview: true, revertStatus, lists: selected.map(({id, version}) => ({id, version}))
      });
      const numbers = selected.map(batch => batch.number).join('、');
      if (!confirm(`対象リスト ${selected.length}件（${numbers}）\n対象指示書 ${preview.sheets}件、未着手に戻す予定 ${preview.reverting}件\n\n一括リセットを実行しますか？`)) return;
      const result = await api(endpoint, {lists: preview.lists, revertStatus, reason, acknowledgeTest: true});
      resetRepair();
      toast(`ピッキング依頼 ${result.count}件をリセットしました。`);
      await refresh();
      await openWorkBatches();
    });
  };
  repairDialog.showModal();
}

function addWorkBatchBack() {
  const back = document.createElement('button');
  back.type = 'button';
  back.textContent = 'まとめ一覧に戻る';
  back.onclick = () => openWorkBatches();
  workBatchContent.prepend(back);
}

function showWorkBatchPrint(batch, byId) {
  showWorkBatchContent(batch.title + ' ／ 指示書・印刷');
  const print = list => {
    queuePrint(list, false, true);
  };
  const unprinted = batch.sources.map(source => byId.get(source.id)).filter(order => order && !order.printedAt);
  workBatchContent.innerHTML = '<button type="button" id="workbatches-print-all">未印刷分をまとめて印刷</button>' +
    workBatchTable(['指示番号', '顧客', '印刷状況', '操作'], batch.sources.map(source => {
      const order = byId.get(source.id);
      return [esc(source.number), esc(source.customer),
        order ? (order.printedAt ? '印刷済み' : '未印刷') : '指示書を取得できません',
        order ? `<button type="button" data-print-order="${source.id}">${order.printedAt ? '再印刷' : '印刷'}</button>` : ''];
    }));
  addWorkBatchBack();
  $('#workbatches-print-all').disabled = !unprinted.length;
  $('#workbatches-print-all').onclick = () => print(unprinted);
  workBatchContent.querySelectorAll('[data-print-order]').forEach(button => {
    button.onclick = () => print([byId.get(Number(button.dataset.printOrder))]);
  });
}

function showWorkBatchCarry(batch) {
  showWorkBatchContent(batch.title + ' ／ 管理担当持込');
  const rows = batch.rows.filter(row => row.stock === 'carry');
  workBatchMessage.textContent = rows.length ? '指示書の持込予定数量です。管理担当持込の「入力済み」にも保存されています。' : '管理担当持込の明細はありません。';
  if (rows.length) {
    workBatchContent.innerHTML = workBatchTable(
      ['指示番号', '顧客', '品番', '商品名', 'サイズ', '新古', '予定数量'],
      rows.map(row => [row.sourceNumber, row.customer, row.sku, row.name, row.size, row.condition, row.quantity].map(esc))
    );
  }
  addWorkBatchBack();
}

function chooseWorkBatchOrders() {
  const candidates = selected().filter(order =>
    order.status === '未着手' && !order.sample && !order.pickingListed && !order.pickingListId
  );
  if (!candidates.length) {
    toast('現在の絞り込みに、まとめに入れられる未着手の指示書がありません。');
    return;
  }
  view = 'work-batches';
  history.replaceState(null, '', '/?view=work-batches');
  document.querySelectorAll('.nav[data-view]').forEach(nav =>
    nav.classList.toggle('active', nav.dataset.view === view)
  );
  listChrome();
  showWorkBatchContent('まとめを作成');
  workBatchContent.innerHTML =
    '<form id="workbatches-form"><label>まとめ名<input name="title" required maxlength="100"></label>' +
    workBatchTable(['選択', '指示番号', '顧客', 'マーク出荷日'], candidates.map(order => [
      `<input type="checkbox" name="orders" value="${order.id}" aria-label="${esc(order.number)}を選ぶ">`,
      esc(order.number), esc(order.customer), esc(order.markShipDate)
    ])) + '<button type="submit" class="primary">選んだ指示書でまとめを作成</button></form>';
  addWorkBatchBack();
  const form = $('#workbatches-form');
  form.elements.title.value = today() + ' 現場作業';
  workBatchMessage.textContent = '対象を選ぶと、同じ指示書でピッキングリストと持込明細を作成します。';
  form.onsubmit = async event => {
    event.preventDefault();
    const data = new FormData(form);
    const ids = new Set(data.getAll('orders').map(Number));
    if (!ids.size || ids.size > 40) {
      workBatchMessage.textContent = '指示書を1～40件選んでください。';
      return;
    }
    const button = event.submitter;
    button.disabled = true;
    try {
      await api('/api/picking/work-batches', {
        title: data.get('title'), date: today(),
        sources: candidates.filter(order => ids.has(order.id)).map(({id, version}) => ({id, version}))
      });
      await refresh();
      await openWorkBatches();
    } catch (error) {
      workBatchMessage.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
}

function openWorkBatchFromLocation() {
  const url = new URL(location.href);
  const batchId = Number(url.searchParams.get('workBatch'));
  if (!Number.isSafeInteger(batchId) || batchId <= 0) return openWorkBatches();
  url.searchParams.delete('workBatch');
  history.replaceState(null, '', url);
  return openWorkBatches(batchId);
}

$('#workbatches-create').onclick = chooseWorkBatchOrders;
updateWorkBatchButtons();
