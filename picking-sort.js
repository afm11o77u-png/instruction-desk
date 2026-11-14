(() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function canUsePickingSort() {
    if (typeof currentUser === 'undefined' || !currentUser) return false;
    return currentUser.role === 'owner' || currentUser.role === 'floor3' || !!currentUser.manage || !!currentUser.overview;
  }

  function updatePickingSortNav() {
    const btn = document.getElementById('nav-picking-sort');
    if (!btn) return;
    const allowed = canUsePickingSort();
    btn.hidden = !allowed;
  }

  if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', updatePickingSortNav);
  } else {
    updatePickingSortNav();
  }
  window.addEventListener('load', updatePickingSortNav);
  setInterval(updatePickingSortNav, 1000);

  let lastAddresses = [];

  function closeModal() {
    const modal = document.getElementById('picking-sort-modal');
    if (modal && modal.open) modal.close();
  }

  window.openPickingSortModal = async function(e) {
    if (e && e.preventDefault) {
      e.preventDefault();
      e.stopPropagation();
    }
    const modal = document.getElementById('picking-sort-modal');
    if (!modal) return;

    const resultBox = document.getElementById('picking-sort-result');
    const previewCount = document.getElementById('picking-sort-count');
    const previewFiles = document.getElementById('picking-sort-files');
    const btnExec = document.getElementById('picking-sort-exec');
    const btnCancel = document.getElementById('picking-sort-cancel');
    const inputIshikari = document.getElementById('picking-sort-ishikari-name');
    const inputReport = document.getElementById('picking-sort-report-name');
    const labelCountText = document.getElementById('picking-sort-label-count');

    lastAddresses = [];
    if (resultBox) { resultBox.style.display = 'none'; resultBox.innerHTML = ''; }
    if (previewCount) { previewCount.textContent = '【処理中・・・】のファイルを確認中...'; previewCount.style.color = ''; }
    if (previewFiles) { previewFiles.innerHTML = ''; }
    if (labelCountText) { labelCountText.textContent = '宛名情報: 確認中...'; }
    if (inputIshikari) { inputIshikari.value = ''; }
    if (inputReport) { inputReport.value = ''; }
    if (btnExec) { btnExec.disabled = true; btnExec.textContent = '仕分けを実行する'; btnExec.dataset.done = '0'; }
    if (btnCancel) { btnCancel.textContent = 'キャンセル'; btnCancel.hidden = false; }

    if (!modal.open) modal.showModal();

    try {
      const res = await api('/api/picking-sort/preview');
      if (inputIshikari && res.next_ishikari_file) inputIshikari.value = res.next_ishikari_file;
      if (inputReport && res.default_report_file) inputReport.value = res.default_report_file;

      if (!res.exists) {
        if (previewCount) {
          previewCount.textContent = `【処理中・・・】フォルダが見つかりません。(${res.dir})`;
          previewCount.style.color = '#c00';
        }
        if (labelCountText) labelCountText.textContent = '対象フォルダがありません';
        return;
      }
      if (res.count === 0) {
        if (previewCount) previewCount.textContent = '現在、仕分け対象のファイルはありません（0件）。';
        if (labelCountText) labelCountText.textContent = '仕分け対象なし（0件）';
        if (btnExec) btnExec.disabled = true;
      } else {
        if (previewCount) previewCount.textContent = `仕分け対象の指示書: ${res.count} 件`;
        if (previewFiles) previewFiles.innerHTML = res.files.map(f => `<li>${esc(f)}</li>`).join('');
        lastAddresses = res.addresses || [];
        if (labelCountText) {
          labelCountText.textContent = lastAddresses.length > 0
            ? `宛名情報: ${lastAddresses.length}件の指示書から宛名データを検出`
            : `宛名情報: 実行時に各指示書から自動抽出します`;
        }
        if (btnExec) btnExec.disabled = false;
      }
    } catch (err) {
      if (previewCount) {
        previewCount.textContent = `ファイル一覧の確認に失敗しました: ${err.message || err}`;
        previewCount.style.color = '#c00';
      }
      if (labelCountText) labelCountText.textContent = '宛名情報の確認失敗';
    }
  };

  // document click delegation for modal buttons
  document.addEventListener('click', async (e) => {
    const t = e.target;
    if (!t) return;

    if (t.id === 'nav-picking-sort' || t.closest('#nav-picking-sort')) {
      e.preventDefault();
      e.stopPropagation();
      window.openPickingSortModal(e);
      return;
    }

    if (t.id === 'picking-sort-close' || t.id === 'picking-sort-cancel') {
      closeModal();
      return;
    }

    if (t.id === 'picking-sort-open-print' || t.closest('#picking-sort-open-print')) {
      e.preventDefault();
      e.stopPropagation();
      if (lastAddresses && lastAddresses.length > 0 && typeof queuePrint === 'function') {
        closeModal();
        queuePrint(lastAddresses, true, true);
      } else {
        if (typeof toast === 'function') toast('印刷対象の宛名データがありません。');
      }
      return;
    }

    if (t.id === 'picking-sort-exec') {
      if (t.dataset.done === '1') {
        closeModal();
        return;
      }

      t.disabled = true;
      t.textContent = '仕分け・保存処理中...';
      const resultBox = document.getElementById('picking-sort-result');
      const previewCount = document.getElementById('picking-sort-count');
      const previewFiles = document.getElementById('picking-sort-files');
      const btnCancel = document.getElementById('picking-sort-cancel');
      if (resultBox) resultBox.style.display = 'none';

      try {
        const inputIshikari = document.getElementById('picking-sort-ishikari-name');
        const inputReport = document.getElementById('picking-sort-report-name');
        const custom_ishikari_file = inputIshikari ? inputIshikari.value.trim() : '';
        const custom_report_file = inputReport ? inputReport.value.trim() : '';

        const res = await api('/api/picking-sort/execute', {
          custom_ishikari_file,
          custom_report_file
        });
        if (res.success) {
          lastAddresses = res.addresses || [];
          const autoPrintLabels = document.getElementById('picking-sort-print-labels')?.checked;

          if (resultBox) {
            resultBox.style.display = 'block';
            resultBox.style.background = '#eef7ee';
            resultBox.style.borderColor = '#bce1bc';
            const iCounts = res.ishikari_counts || {};
            const rCounts = res.report_counts || {};
            resultBox.innerHTML = `
              <div style="font-weight:bold;color:#2e7d32;margin-bottom:6px;">✓ ${esc(res.message)}</div>
              <ul style="margin:4px 0;padding-left:20px;font-size:0.88rem;color:#333;">
                ${res.ishikari_file ? `<li><strong>石狩倉庫ブック:</strong> ${esc(res.ishikari_file)} (石狩:${iCounts.ishikari||0}件, 工場:${iCounts.factory||0}件, 持ち込み:${iCounts.carry||0}件)</li>` : '<li><strong>石狩倉庫ブック:</strong> 対象データなし</li>'}
                ${res.report_file ? `<li><strong>報告用リスト:</strong> ${esc(res.report_file)} に追記 (持ち込み:${rCounts.carry||0}件, 工場:${rCounts.factory||0}件)</li>` : '<li><strong>報告用リスト:</strong> 対象データなし</li>'}
                <li><strong>処理済み移動:</strong> ${res.moved_count}件のファイルを移動完了</li>
                ${lastAddresses.length > 0 ? `<li><strong>宛名書き:</strong> ${lastAddresses.length}件の宛名データを抽出完了</li>` : ''}
              </ul>
              ${lastAddresses.length > 0 ? `
                <div style="margin-top:10px;">
                  <button type="button" id="picking-sort-open-print" class="primary" style="padding:6px 12px;font-size:0.88rem;cursor:pointer;">
                    宛名書き印刷画面を開く（${lastAddresses.length}件）
                  </button>
                </div>
              ` : ''}
            `;
          }
          if (previewCount) previewCount.textContent = '仕分け完了（処理中フォルダは空になりました）';
          if (previewFiles) previewFiles.innerHTML = '';
          t.disabled = false;
          t.dataset.done = '1';
          t.textContent = '完了（閉じる）';
          if (btnCancel) btnCancel.hidden = true;
          if (typeof toast === 'function') toast('指示書の仕分けと保存が完了しました');

          if (autoPrintLabels && lastAddresses.length > 0 && typeof queuePrint === 'function') {
            closeModal();
            queuePrint(lastAddresses, true, true);
          }
        } else {
          if (resultBox) {
            resultBox.style.display = 'block';
            resultBox.style.background = '#fdebee';
            resultBox.style.borderColor = '#facfd4';
            resultBox.innerHTML = `<div style="color:#c00;">${esc(res.message || 'エラーが発生しました')}</div>`;
          }
          t.disabled = false;
          t.dataset.done = '0';
          t.textContent = '再試行';
        }
      } catch (err) {
        if (resultBox) {
          resultBox.style.display = 'block';
          resultBox.style.background = '#fdebee';
          resultBox.style.borderColor = '#facfd4';
          resultBox.innerHTML = `<div style="color:#c00;">エラー: ${esc(err.message || err)}</div>`;
        }
        t.disabled = false;
        t.dataset.done = '0';
        t.textContent = '再試行';
      }
    }
  });
})();
