const workBatchDialog = $('#workbatches');
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

function showWorkBatchDialog(title) {
  $('#workbatches-title').textContent = title;
  workBatchMessage.textContent = '';
  workBatchContent.replaceChildren();
  if (!workBatchDialog.open) workBatchDialog.showModal();
}

async function openWorkBatches() {
  showWorkBatchDialog('作業のまとめ');
  workBatchMessage.textContent = '読み込み中…';
  try {
    await refresh();
    const batches = await api('/api/picking/work-batches');
    const byId = new Map(orders.map(order => [order.id, order]));
    workBatchMessage.textContent = batches.length ? '' : '作業のまとめはまだありません。';
    if (!batches.length) return;
    workBatchContent.innerHTML = workBatchTable(
      ['まとめ', '指示書・印刷', 'ピッキング', '管理担当持込'],
      batches.map(batch => {
        const printed = batch.sources.filter(source => byId.get(source.id)?.printedAt).length;
        const carried = batch.rows.filter(row => row.stock === 'carry').length;
        return [
          `<strong>${esc(batch.title)}</strong><br>${esc(batch.number)}　${esc(batch.date)}`,
          `${printed}/${batch.sources.length}件印刷済み<br><button type="button" data-print-batch="${batch.id}">指示書・印刷</button>`,
          `${esc(batch.phase)}<br><a href="/picking?board=1&amp;list=${batch.id}" target="_blank" rel="noopener">ピッキングリストを開く</a>`,
          `${carried}明細<br><button type="button" data-carry-batch="${batch.id}">持込リストを開く</button>`
        ];
      })
    );
    for (const batch of batches) {
      workBatchContent.querySelector(`[data-print-batch="${batch.id}"]`).onclick = () => showWorkBatchPrint(batch, byId);
      workBatchContent.querySelector(`[data-carry-batch="${batch.id}"]`).onclick = () => showWorkBatchCarry(batch);
    }
  } catch (error) {
    workBatchMessage.textContent = error.message;
  }
}

function addWorkBatchBack() {
  const back = document.createElement('button');
  back.type = 'button';
  back.textContent = 'まとめ一覧に戻る';
  back.onclick = openWorkBatches;
  workBatchContent.prepend(back);
}

function showWorkBatchPrint(batch, byId) {
  showWorkBatchDialog(batch.title + ' ／ 指示書・印刷');
  const print = list => {
    workBatchDialog.close();
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
  showWorkBatchDialog(batch.title + ' ／ 管理担当持込');
  const rows = batch.rows.filter(row => row.stock === 'carry');
  workBatchMessage.textContent = rows.length ? '指示書の持込予定数量です。' : '管理担当持込の明細はありません。';
  if (rows.length) {
    workBatchContent.innerHTML = workBatchTable(
      ['指示番号', '顧客', '品番', '商品名', 'サイズ', '新古', '予定数量'],
      rows.map(row => [row.sourceNumber, row.customer, row.sku, row.name, row.size, row.condition, row.quantity].map(esc))
    );
  }
  addWorkBatchBack();
}

function chooseWorkBatchOrders() {
  showWorkBatchDialog('まとめを作成');
  const candidates = selected().filter(order =>
    order.status === '未着手' && !order.sample && !order.pickingListed && !order.pickingListId
  );
  if (!candidates.length) {
    workBatchMessage.textContent = '現在の絞り込みに、まとめに入れられる未着手の指示書がありません。';
    return;
  }
  workBatchContent.innerHTML =
    '<form id="workbatches-form"><label>まとめ名<input name="title" required maxlength="100"></label>' +
    workBatchTable(['選択', '指示番号', '顧客', 'マーク出荷日'], candidates.map(order => [
      `<input type="checkbox" name="orders" value="${order.id}" aria-label="${esc(order.number)}を選ぶ">`,
      esc(order.number), esc(order.customer), esc(order.markShipDate)
    ])) + '<button type="submit" class="primary">選んだ指示書でまとめを作成</button></form>';
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
      await openWorkBatches();
    } catch (error) {
      workBatchMessage.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
}

$('#workbatches-open').onclick = openWorkBatches;
$('#workbatches-create').onclick = chooseWorkBatchOrders;
$('#workbatches-close').onclick = () => workBatchDialog.close();
updateWorkBatchButtons();
