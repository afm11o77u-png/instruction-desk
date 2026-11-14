(() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  let statsData = null;
  let selectedMonthKey = null;

  async function loadProcessedStats(force = false) {
    const statusEl = $('#pstatsstatus');
    const refreshBtn = $('#pstatsrefresh');
    if (statusEl) {
      statusEl.textContent = force ? '最新情報をスキャン中...' : '集計データを読み込み中...';
      statusEl.style.color = '#666';
    }
    if (refreshBtn) refreshBtn.disabled = true;

    try {
      const url = force ? '/api/processed-stats?refresh=1' : '/api/processed-stats';
      const res = await api(url);
      statsData = res;

      if (!res.exists) {
        if (statusEl) {
          statusEl.textContent = `処理済みフォルダが見つかりません。(${res.dir})`;
          statusEl.style.color = '#c00';
        }
        return;
      }

      initYearFilter();
      renderStats();
      if (statusEl) {
        statusEl.textContent = `集計完了 (${res.updated_at} 更新 / 合計: ${res.total_all.toLocaleString()}枚)`;
        statusEl.style.color = '#2e7d32';
      }
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = `データ取得エラー: ${err.message || err}`;
        statusEl.style.color = '#c00';
      }
    } finally {
      if (refreshBtn) refreshBtn.disabled = false;
    }
  }
  window.loadProcessedStats = loadProcessedStats;

  function initYearFilter() {
    const sel = $('#pstatsyear');
    if (!sel || !statsData) return;
    const currentVal = sel.value;
    const years = statsData.years || [];

    let html = '<option value="">すべての年</option>';
    years.forEach(y => {
      html += `<option value="${y}">${y}年</option>`;
    });
    sel.innerHTML = html;
    if (currentVal && [...sel.options].some(o => o.value === currentVal)) {
      sel.value = currentVal;
    } else if (years.length > 0) {
      sel.value = String(years[0]); // 最新年を初期選択
    }
  }

  function renderStats() {
    if (!statsData || !statsData.months) return;

    const selYear = $('#pstatsyear')?.value || '';
    const filteredMonths = statsData.months.filter(m => !selYear || String(m.year) === selYear);

    // サマリー表示
    const summaryEl = $('#pstatssummary');
    if (summaryEl) {
      const totalFiltered = filteredMonths.reduce((sum, m) => sum + (m.total || 0), 0);
      const yearLabel = selYear ? `${selYear}年` : '全期間';
      summaryEl.textContent = `【${yearLabel}】 指示書合計: ${totalFiltered.toLocaleString()} 枚 (${filteredMonths.length}ヶ月)`;
    }

    // デフォルト選択月（選択中がないか絞り込み外になったら先頭）
    if (!selectedMonthKey || !filteredMonths.some(m => m.key === selectedMonthKey)) {
      selectedMonthKey = filteredMonths.length > 0 ? filteredMonths[0].key : null;
    }

    // 月別テーブル描画
    const monthBody = $('#pstatsmonthbody');
    if (monthBody) {
      if (filteredMonths.length === 0) {
        monthBody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:#999;padding:20px;">対象データがありません</td></tr>';
      } else {
        monthBody.innerHTML = filteredMonths.map(m => {
          const isSelected = m.key === selectedMonthKey;
          const bg = isSelected ? '#e8f4fd' : 'transparent';
          const weight = isSelected ? 'bold' : 'normal';
          return `
            <tr data-month-key="${esc(m.key)}" style="cursor:pointer;background:${bg};font-weight:${weight};">
              <td style="padding:10px 12px;border-bottom:1px solid #eee;color:#1976d2;">
                ${isSelected ? '▶ ' : ''}${esc(m.label)}
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #eee;text-align:right;color:#555;">
                ${m.day_count} 日
              </td>
              <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;">
                ${(m.total || 0).toLocaleString()} <small>枚</small>
              </td>
            </tr>
          `;
        }).join('');
      }
    }

    // 日別テーブル描画
    renderDays();
  }

  function renderDays() {
    const dayBody = $('#pstatsdaybody');
    const dayTitle = $('#pstatsdaytitle');
    const dayTotalEl = $('#pstatsdaymonthtotal');
    if (!dayBody || !statsData) return;

    const monthData = statsData.months.find(m => m.key === selectedMonthKey);
    if (!monthData) {
      if (dayTitle) dayTitle.textContent = '日別内訳';
      if (dayTotalEl) dayTotalEl.textContent = '';
      dayBody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#999;padding:20px;">月を選択してください</td></tr>';
      return;
    }

    if (dayTitle) dayTitle.textContent = `${monthData.label} の日別指示書枚数`;
    if (dayTotalEl) dayTotalEl.textContent = `月間合計: ${(monthData.total || 0).toLocaleString()} 枚 (${monthData.day_count} 日間)`;

    const days = monthData.days || [];
    if (days.length === 0) {
      dayBody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#999;padding:20px;">日別データがありません</td></tr>';
      return;
    }

    const maxCount = Math.max(1, ...days.map(d => d.count || 0));

    dayBody.innerHTML = days.map(d => {
      const isSun = d.weekday === '日';
      const isSat = d.weekday === '土';
      const wColor = isSun ? '#d32f2f' : isSat ? '#1976d2' : '#333';
      const wBg = isSun ? '#ffebee' : isSat ? '#e3f2fd' : 'transparent';
      const pct = Math.round(((d.count || 0) / maxCount) * 100);

      return `
        <tr style="background:${wBg};">
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-weight:500;">
            ${esc(d.label)}
          </td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;color:${wColor};font-weight:bold;">
            ${esc(d.weekday || '-')}
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;font-size:1.02rem;">
            ${(d.count || 0).toLocaleString()} <small style="font-weight:normal;color:#666;">枚</small>
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;vertical-align:middle;">
            <div style="background:#eee;border-radius:4px;height:14px;width:100%;max-width:180px;overflow:hidden;display:inline-block;vertical-align:middle;">
              <div style="background:#4caf50;height:100%;width:${pct}%;border-radius:4px;"></div>
            </div>
          </td>
        </tr>
      `;
    }).join('');
  }

  // イベントリスナー
  document.addEventListener('DOMContentLoaded', () => {
    const selYear = $('#pstatsyear');
    if (selYear) {
      selYear.addEventListener('change', renderStats);
    }

    const btnRefresh = $('#pstatsrefresh');
    if (btnRefresh) {
      btnRefresh.addEventListener('click', () => loadProcessedStats(true));
    }

    const monthBody = $('#pstatsmonthbody');
    if (monthBody) {
      monthBody.addEventListener('click', e => {
        const tr = e.target.closest('tr[data-month-key]');
        if (!tr) return;
        selectedMonthKey = tr.dataset.monthKey;
        renderStats();
      });
    }
  });
})();
