(() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function canUsePickingSort() {
    if (typeof currentUser === 'undefined' || !currentUser) return false;
    return currentUser.role === 'owner' || currentUser.role === 'floor3' || !!currentUser.manage || !!currentUser.overview;
  }

  function updatePickingSortNav() {
    const btn = $('#nav-picking-sort');
    if (!btn) return;
    const allowed = canUsePickingSort();
    btn.hidden = !allowed;
  }

  // 複数のタイミングで確実に表示判定を実行
  if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', updatePickingSortNav);
  } else {
    updatePickingSortNav();
  }
  window.addEventListener('load', updatePickingSortNav);
  setInterval(updatePickingSortNav, 1000);

  const modal = $('#picking-sort-modal');
  const btnOpen = $('#nav-picking-sort');
  const btnClose = $('#picking-sort-close');
  const btnCancel = $('#picking-sort-cancel');
  const btnExec = $('#picking-sort-exec');
  const previewCount = $('#picking-sort-count');
  const previewFiles = $('#picking-sort-files');
  const resultBox = $('#picking-sort-result');

  if (btnOpen) {
    btnOpen.addEventListener('click', async () => {
      if (!modal) return;
      resultBox.style.display = 'none';
      resultBox.innerHTML = '';
      previewCount.textContent = '【処理中・・・】のファイルを確認中...';
      previewFiles.innerHTML = '';
      btnExec.disabled = true;
      btnExec.textContent = '仕分けを実行する';
      modal.showModal();

      try {
        const res = await api('/api/picking-sort/preview');
        if (!res.exists) {
          previewCount.textContent = `【処理中・・・】フォルダが見つかりません。(${res.dir})`;
          previewCount.style.color = '#c00';
          return;
        }
        previewCount.style.color = '';
        if (res.count === 0) {
          previewCount.textContent = '現在、仕分け対象のファイルはありません（0件）。';
          btnExec.disabled = true;
        } else {
          previewCount.textContent = `仕分け対象の指示書: ${res.count} 件`;
          previewFiles.innerHTML = res.files.map(f => `<li>${esc(f)}</li>`).join('');
          btnExec.disabled = false;
        }
      } catch (err) {
        previewCount.textContent = `ファイル一覧の確認に失敗しました: ${err.message || err}`;
        previewCount.style.color = '#c00';
      }
    });
  }

  function closeModal() {
    if (modal && modal.open) modal.close();
  }

  if (btnClose) btnClose.addEventListener('click', closeModal);
  if (btnCancel) btnCancel.addEventListener('click', closeModal);

  if (btnExec) {
    btnExec.addEventListener('click', async () => {
      btnExec.disabled = true;
      btnExec.textContent = '仕分け・保存処理中...';
      resultBox.style.display = 'none';

      try {
        const res = await api('/api/picking-sort/execute', {});
        if (res.success) {
          resultBox.style.display = 'block';
          resultBox.style.background = '#eef7ee';
          resultBox.style.borderColor = '#bce1bc';
          resultBox.innerHTML = `
            <div style="font-weight:bold;color:#2e7d32;margin-bottom:6px;">✓ ${esc(res.message)}</div>
            <ul style="margin:4px 0;padding-left:20px;font-size:0.88rem;color:#333;">
              ${res.ishikari_file ? `<li><strong>石狩倉庫:</strong> ${esc(res.ishikari_file)} (${res.ishikari_count}件)</li>` : '<li><strong>石狩倉庫:</strong> 対象データなし</li>'}
              ${res.carry_file ? `<li><strong>持ち込み:</strong> ${esc(res.carry_file)} の「持ち込み」シートに追記 (${res.carry_count}件)</li>` : '<li><strong>持ち込み:</strong> 対象データなし</li>'}
              <li><strong>処理済み移動:</strong> ${res.moved_count}件のファイルを移動完了</li>
            </ul>
          `;
          previewCount.textContent = '仕分け完了（処理中フォルダは空になりました）';
          previewFiles.innerHTML = '';
          btnExec.textContent = '完了';
          if (typeof toast === 'function') toast('指示書の仕分けと保存が完了しました');
        } else {
          resultBox.style.display = 'block';
          resultBox.style.background = '#fdebee';
          resultBox.style.borderColor = '#facfd4';
          resultBox.innerHTML = `<div style="color:#c00;">${esc(res.message || 'エラーが発生しました')}</div>`;
          btnExec.disabled = false;
          btnExec.textContent = '再試行';
        }
      } catch (err) {
        resultBox.style.display = 'block';
        resultBox.style.background = '#fdebee';
        resultBox.style.borderColor = '#facfd4';
        resultBox.innerHTML = `<div style="color:#c00;">エラー: ${esc(err.message || err)}</div>`;
        btnExec.disabled = false;
        btnExec.textContent = '再試行';
      }
    });
  }
})();
