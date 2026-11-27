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
  let previewEpoch = 0;

  function renderRefRepairsBox(repairs, isPreview) {
    if (!repairs || !repairs.length) return '';
    const title = isPreview
      ? `🔧 #REF! などの数式エラーを指示書シートから自動補完します（${repairs.length}件）`
      : `🔧 #REF! などの数式エラーを指示書シートから自動補完しました（${repairs.length}件）`;
    const items = repairs.map(r =>
      `<li><strong>${esc(r.file)}</strong>（${esc(r.row)}行目・${esc(r.field)}）: ` +
      `<span style="color:#c00;">${esc(r.before)}</span> → ` +
      `<strong style="color:#1b5e20;">${esc(r.after)}</strong> ` +
      `<span style="color:#666;font-size:0.82rem;">（参照元: ${esc(r.source)}）</span></li>`
    ).join('');
    return `
      <div style="margin-top:10px;padding:8px 10px;background:#fff8e1;border:1px solid #ffe082;border-radius:6px;font-size:0.85rem;color:#5d4037;">
        <div style="font-weight:bold;color:#e65100;margin-bottom:4px;">${title}</div>
        <ul style="margin:0;padding-left:18px;max-height:140px;overflow-y:auto;">${items}</ul>
      </div>
    `;
  }

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
    const btnLabelOnly = document.getElementById('picking-sort-label-only');
    const epoch = ++previewEpoch;
    if (btnLabelOnly) btnLabelOnly.disabled = true;
    const btnCancel = document.getElementById('picking-sort-cancel');
    const inputIshikari = document.getElementById('picking-sort-ishikari-name');
    const inputReport = document.getElementById('picking-sort-report-name');
    const inputProcessedDate = document.getElementById('picking-sort-processed-date');
    const labelCountText = document.getElementById('picking-sort-label-count');

    lastAddresses = [];
    if (resultBox) { resultBox.style.display = 'none'; resultBox.innerHTML = ''; }
    if (previewCount) { previewCount.textContent = '【処理中・・・】のファイルを確認中...'; previewCount.style.color = ''; }
    if (previewFiles) { previewFiles.innerHTML = ''; }
    if (labelCountText) { labelCountText.textContent = '宛名情報: 確認中...'; }
    if (inputIshikari) { inputIshikari.value = ''; }
    if (inputReport) { inputReport.value = ''; }
    if (inputProcessedDate) inputProcessedDate.value = '';
    for (const field of [inputIshikari, inputReport, inputProcessedDate]) if (field) field.disabled = false;
    if (btnExec) { btnExec.disabled = true; btnExec.textContent = '仕分けを実行する'; btnExec.dataset.done = '0'; }
    if (btnCancel) { btnCancel.textContent = 'キャンセル'; btnCancel.hidden = false; }

    if (!modal.open) modal.showModal();

    try {
      const res = await api('/api/picking-sort/preview');
      if (epoch !== previewEpoch || !modal.open) return;
      if (inputIshikari && res.next_ishikari_file) inputIshikari.value = res.next_ishikari_file;
      if (inputReport && res.default_report_file) inputReport.value = res.default_report_file;
      if (inputProcessedDate && res.default_processed_date) inputProcessedDate.value = res.default_processed_date;

      if (res.resume_move) {
        if (previewCount) previewCount.textContent = '前回のExcelは保存済みです。元の指示書の移動を再開できます。';
        if (previewFiles) previewFiles.innerHTML = res.files.map(f => `<li>${esc(f)}</li>`).join('');
        if (labelCountText) labelCountText.textContent = '移動完了後に宛名情報を表示します';
        for (const field of [inputIshikari, inputReport, inputProcessedDate]) if (field) field.disabled = true;
        if (btnExec) {
          btnExec.disabled = false;
          btnExec.textContent = '指示書の移動を再開する';
        }
        return;
      }

      if (!res.exists) {
        if (previewCount) {
          previewCount.textContent = `【処理中・・・】フォルダが見つかりません。(${res.dir})`;
          previewCount.style.color = '#c00';
        }
        if (labelCountText) labelCountText.textContent = '対象フォルダがありません';
        return;
      }
      const bp = res.barcode_pending || {};
      if (res.count === 0 && bp.rows > 0) {
        if (previewCount) previewCount.textContent = `仕分け対象の指示書: 0 件 ／ 統合待ちのバーコード明細: ${bp.rows} 件（${bp.lists}リスト）`;
        if (labelCountText) labelCountText.textContent = '宛名情報: なし';
        if (btnExec) btnExec.disabled = false;
      } else if (res.count === 0) {
        if (previewCount) previewCount.textContent = '現在、仕分け対象のファイルはありません（0件）。';
        if (labelCountText) labelCountText.textContent = '仕分け対象なし（0件）';
        if (btnExec) btnExec.disabled = true;
      } else {
        if (previewCount) previewCount.textContent = `仕分け対象の指示書: ${res.count} 件` + (bp.rows > 0 ? ` ／ 統合待ちのバーコード明細: ${bp.rows} 件（${bp.lists}リスト）` : '');
        if (previewFiles) previewFiles.innerHTML = res.files.map(f => `<li>${esc(f)}</li>`).join('');
        lastAddresses = res.addresses || [];
        if (btnLabelOnly) btnLabelOnly.disabled = !lastAddresses.length;
        if (labelCountText) {
          labelCountText.textContent = lastAddresses.length > 0
            ? `宛名情報: ${lastAddresses.length}件の指示書から宛名データを検出`
            : `宛名情報: 実行時に各指示書から自動抽出します`;
        }
        if (resultBox && res.ref_repairs && res.ref_repairs.length > 0) {
          resultBox.style.display = 'block';
          resultBox.style.background = 'transparent';
          resultBox.style.borderColor = 'transparent';
          resultBox.innerHTML = renderRefRepairsBox(res.ref_repairs, true);
        }
        if (btnExec) btnExec.disabled = false;
      }
    } catch (err) {
      if (epoch !== previewEpoch || !modal.open) return;
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

    if (t.closest('#picking-sort-label-only, #picking-sort-open-print')) {
      e.preventDefault();
      e.stopPropagation();
      if (lastAddresses && lastAddresses.length > 0 && typeof openImportedAddressPreview === 'function') {
        closeModal();
        openImportedAddressPreview(lastAddresses);
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
      const btnLabelOnly = document.getElementById('picking-sort-label-only');
      if (btnLabelOnly) btnLabelOnly.disabled = true;
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
          custom_report_file,
          processed_date: document.getElementById('picking-sort-processed-date')?.value || ''
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
                ${res.report_file ? `<li><strong>報告用リスト:</strong> ${esc(res.report_file)} ${res.resumed_move ? 'は前回保存済み' : 'に追記'} (持ち込み:${rCounts.carry||0}件)</li>` : '<li><strong>報告用リスト:</strong> 対象データなし</li>'}
                <li><strong>処理済み移動:</strong> ${esc(res.processed_date || '')} のフォルダに ${res.moved_count}件を移動</li>
                ${lastAddresses.length > 0 ? `<li><strong>宛名書き:</strong> ${lastAddresses.length}件の宛名データを抽出完了</li>` : ''}
              </ul>
              ${renderRefRepairsBox(res.ref_repairs, false)}
              ${lastAddresses.length > 0 ? `
                <div style="margin-top:10px;">
                  <button type="button" id="picking-sort-open-print" class="primary" style="padding:6px 12px;font-size:0.88rem;cursor:pointer;">
                    宛名書き印刷画面を開く（${lastAddresses.length}件）
                  </button>
                </div>
              ` : ''}
              ${res.barcode_rows > 0 ? `<div style="margin-top:6px;font-size:0.88rem;color:#333;"><strong>バーコード明細 ${res.barcode_rows}件（${res.barcode_lists}リスト）を統合</strong></div>` : ''}
            `;
          }
          if (previewCount) {
            previewCount.textContent = res.remaining_count > 0
              ? `仕分け完了（実行中に新しく投入された指示書が ${res.remaining_count} 件残っています）`
              : '仕分け完了（処理中フォルダは空になりました）';
          }
          if (previewFiles) previewFiles.innerHTML = '';
          t.disabled = false;
          t.dataset.done = '1';
          t.textContent = '完了（閉じる）';
          if (btnCancel) btnCancel.hidden = true;
          if (typeof toast === 'function') toast('指示書の仕分けと保存が完了しました');

          const hasRepairs = Array.isArray(res.ref_repairs) && res.ref_repairs.length > 0;
          if (autoPrintLabels && !hasRepairs && lastAddresses.length > 0 && typeof openImportedAddressPreview === 'function') {
            closeModal();
            openImportedAddressPreview(lastAddresses);
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
          if (btnLabelOnly) btnLabelOnly.disabled = !lastAddresses.length;
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
        if (btnLabelOnly) btnLabelOnly.disabled = !lastAddresses.length;
      }
    }
  });
})();
