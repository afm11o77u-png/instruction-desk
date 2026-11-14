(() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  let historyList = [];
  let allCustomers = [];
  let filteredCustomers = [];
  let displayLimit = 100;
  let isCustomersLoaded = false;

  function switchTab(target) {
    const scanTab = $('#bdelivtab-scan');
    const listTab = $('#bdelivtab-list');
    const scanView = $('#bdelivscanview');
    const listView = $('#bdelivcustomersview');

    if (target === 'list') {
      if (scanTab) { scanTab.className = 'quiet'; }
      if (listTab) { listTab.className = 'primary'; }
      if (scanView) scanView.hidden = true;
      if (listView) listView.hidden = false;
      if (!isCustomersLoaded) {
        loadCustomers();
      }
    } else {
      if (scanTab) { scanTab.className = 'primary'; }
      if (listTab) { listTab.className = 'quiet'; }
      if (scanView) scanView.hidden = false;
      if (listView) listView.hidden = true;
      const input = $('#bdelivcode');
      if (input) setTimeout(() => input.focus(), 50);
    }
  }

  async function loadCustomers(forceSync = false) {
    const countEl = $('#bdelivcustcount');
    const tbody = $('#bdelivcustbody');
    const syncStatus = $('#bdelivcustsyncstatus');

    if (countEl) countEl.textContent = forceSync ? 'Accessから同期中...' : '顧客一覧を読み込み中...';
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:30px;color:#666;">読み込み中...</td></tr>`;
    }

    try {
      if (forceSync) {
        if (syncStatus) syncStatus.textContent = 'Accessからデータを取得中...';
        const sRes = await api('/api/barcode-delivery/sync');
        if (!sRes.ok) {
          throw new Error(sRes.message || '同期に失敗しました');
        }
        if (syncStatus) syncStatus.textContent = `同期完了: ${sRes.count || 0}件 (${sRes.updatedAt || ''})`;
      }

      // Fetch all cached customers (up to 20000)
      const res = await api('/api/barcode-delivery/customers?limit=20000');
      if (!res.ok) {
        throw new Error(res.message || 'データ取得エラー');
      }

      allCustomers = res.customers || [];
      isCustomersLoaded = true;

      // Populate filter dropdowns if provided
      setupDropdowns(res.cars || [], res.weekdays || []);

      applyCustomerFilter();
    } catch (err) {
      if (countEl) countEl.textContent = '読み込み失敗';
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:30px;color:#c62828;">読み込みに失敗しました: ${esc(err.message || err)}</td></tr>`;
      }
    }
  }

  function setupDropdowns(cars, weekdays) {
    const carSel = $('#bdelivcustcar');
    const wdaySel = $('#bdelivcustwday');

    if (carSel && carSel.options.length <= 1) {
      const current = carSel.value;
      carSel.innerHTML = '<option value="">すべての号車</option>';
      cars.forEach(c => {
        if (!c) return;
        const opt = document.createElement('option');
        opt.value = c;
        opt.textContent = c;
        if (c === current) opt.selected = true;
        carSel.appendChild(opt);
      });
    }

    if (wdaySel && wdaySel.options.length <= 1) {
      const current = wdaySel.value;
      wdaySel.innerHTML = '<option value="">すべての曜日</option>';
      weekdays.forEach(w => {
        if (!w) return;
        const opt = document.createElement('option');
        opt.value = w;
        opt.textContent = w;
        if (w === current) opt.selected = true;
        wdaySel.appendChild(opt);
      });
    }
  }

  function applyCustomerFilter() {
    const q = ($('#bdelivcustsearch')?.value || '').trim().toLowerCase();
    const type = $('#bdelivcusttype')?.value || 'all';
    const car = $('#bdelivcustcar')?.value || '';
    const wday = $('#bdelivcustwday')?.value || '';

    const tokens = q ? q.split(/\s+/).filter(Boolean) : [];

    filteredCustomers = allCustomers.filter(c => {
      // 1. Delivery type
      if (type === 'regular' && c.isNoDelivery) return false;
      if (type === 'delivery_only' && !c.isNoDelivery) return false;

      // 2. Car / cycle
      if (car && c.cycle !== car) return false;

      // 3. Weekday
      if (wday && c.weekday !== wday) return false;

      // 4. Search query tokens
      if (tokens.length) {
        const text = (c.name + ' ' + c.code).toLowerCase();
        for (const tok of tokens) {
          if (!text.includes(tok)) return false;
        }
      }

      return true;
    });

    displayLimit = 100;
    renderCustomerTable();
  }

  function renderCustomerTable() {
    const countEl = $('#bdelivcustcount');
    const tbody = $('#bdelivcustbody');
    const moreWrap = $('#bdelivcustmorewrap');

    if (countEl) {
      const total = allCustomers.length;
      const count = filteredCustomers.length;
      countEl.innerHTML = `全 <strong>${total.toLocaleString()}</strong> 件中 <strong>${count.toLocaleString()}</strong> 件を表示`;
    }

    if (!tbody) return;

    if (!filteredCustomers.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:36px;color:#888;">条件に該当する顧客は見つかりませんでした。</td></tr>`;
      if (moreWrap) moreWrap.hidden = true;
      return;
    }

    const slice = filteredCustomers.slice(0, displayLimit);
    const rowsHtml = slice.map(c => {
      const isNoDeliv = c.isNoDelivery;
      const typeBadge = isNoDeliv
        ? `<span style="background:#fff3e0;color:#e65100;padding:3px 8px;border-radius:4px;font-weight:bold;font-size:12px;border:1px solid #ffe082;">📦 送り便</span>`
        : `<span style="background:#e8f5e9;color:#1b5e20;padding:3px 8px;border-radius:4px;font-weight:bold;font-size:12px;border:1px solid #c8e6c9;">🚚 自社便集配</span>`;

      const dayColor = isNoDeliv ? '#78909c' : '#1b5e20';
      const carColor = isNoDeliv ? '#d84315' : '#0d47a1';

      return `
        <tr style="border-bottom:1px solid #eee;transition:background 0.15s;" onmouseover="this.style.background='#f8fafd'" onmouseout="this.style.background='transparent'">
          <td style="padding:10px 14px;font-family:monospace;font-weight:bold;color:#555;">${esc(c.code)}</td>
          <td style="padding:10px 14px;font-weight:bold;color:#222;">${esc(c.name)}</td>
          <td style="padding:10px 14px;text-align:center;">${typeBadge}</td>
          <td style="padding:10px 14px;text-align:center;font-weight:bold;color:${dayColor};font-size:14px;">
            ${esc(c.weekday || '—')}
          </td>
          <td style="padding:10px 14px;text-align:center;font-weight:900;color:${carColor};font-size:14px;">
            ${esc(c.cycle || '—')}
          </td>
        </tr>
      `;
    }).join('');

    tbody.innerHTML = rowsHtml;

    if (moreWrap) {
      moreWrap.hidden = filteredCustomers.length <= displayLimit;
    }
  }

  async function handleBarcodeSubmit(code) {
    code = (code || '').trim();
    if (!code) return;

    const input = $('#bdelivcode');
    const statusEl = $('#bdelivstatus');
    const resultEl = $('#bdelivcard');
    
    if (statusEl) {
      statusEl.textContent = '照会中...';
      statusEl.style.color = '#0288d1';
    }

    try {
      const res = await api(`/api/barcode-delivery?code=${encodeURIComponent(code)}`);
      
      if (!res.ok || !res.found) {
        if (statusEl) {
          statusEl.textContent = res.message || `バーコード【${code}】は見つかりませんでした。`;
          statusEl.style.color = '#c62828';
        }
        renderNotFoundCard(code, res.message);
      } else {
        if (statusEl) {
          statusEl.textContent = `照会完了: ${res.masterCode}`;
          statusEl.style.color = '#2e7d32';
        }
        renderSuccessCard(res);
        addToHistory(res);
      }
    } catch (err) {
      if (statusEl) {
        statusEl.textContent = `通信エラー: ${err.message || err}`;
        statusEl.style.color = '#c62828';
      }
    } finally {
      if (input) {
        input.value = '';
        input.focus();
      }
    }
  }

  function renderSuccessCard(d) {
    const card = $('#bdelivcard');
    if (!card) return;

    const weekday = d.weekday || '未設定';
    const car = d.car || '未設定';
    const timeBand = d.timeBand || '';
    const method = d.method || '';
    const isNoDelivery = d.isNoDelivery || weekday === '集配なし' || car.includes('送り便') || car.includes('対象外');

    const boxBorder = isNoDelivery ? '#b0bec5' : '#2e7d32';
    const headerBg = isNoDelivery ? '#f5f5f5' : '#e8f5e9';
    const headerBorder = isNoDelivery ? '#cfd8dc' : '#a5d6a7';
    const dayColor = isNoDelivery ? '#546e7a' : '#1b5e20';
    const carColor = isNoDelivery ? '#d84315' : '#0d47a1';

    card.innerHTML = `
      <div style="background:#fff;border:2px solid ${boxBorder};border-radius:12px;padding:24px;box-shadow:0 4px 12px rgba(0,0,0,0.08);margin-bottom:24px;">
        
        ${isNoDelivery ? `
          <div style="background:#fff3e0;border:1px solid #ffe082;border-radius:8px;padding:8px 14px;margin-bottom:16px;display:flex;align-items:center;gap:8px;color:#e65100;font-weight:bold;font-size:14px;">
            <span>⚠️</span> <span>この顧客は定期集配ルート外（送り便・宅配便など）です。</span>
          </div>
        ` : ''}

        <!-- 最重要情報: 集配曜日 & 集配号車 (超特大表示) -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;background:${headerBg};padding:20px;border-radius:10px;border:1.5px solid ${headerBorder};margin-bottom:20px;">
          <div style="text-align:center;border-right:2px dashed ${headerBorder};padding-right:12px;">
            <div style="font-size:16px;font-weight:bold;color:${dayColor};margin-bottom:6px;display:flex;align-items:center;justify-content:center;gap:6px;">
              <span>📅</span> 集配曜日
            </div>
            <div style="font-size:42px;font-weight:900;color:${dayColor};letter-spacing:1px;line-height:1.1;">
              ${esc(weekday)}
            </div>
            ${timeBand ? `<div style="font-size:15px;color:${dayColor};font-weight:bold;margin-top:8px;">⏰ ${esc(timeBand)}</div>` : ''}
          </div>
          
          <div style="text-align:center;padding-left:12px;">
            <div style="font-size:16px;font-weight:bold;color:${carColor};margin-bottom:6px;display:flex;align-items:center;justify-content:center;gap:6px;">
              <span>${isNoDelivery ? '📦' : '🚚'}</span> 集配号車 / サイクル
            </div>
            <div style="font-size:${car.length > 6 ? '32px' : '42px'};font-weight:900;color:${carColor};letter-spacing:1px;line-height:1.1;">
              ${esc(car)}
            </div>
            ${method ? `<div style="font-size:15px;color:${carColor};font-weight:bold;margin-top:8px;">📦 ${esc(method)}</div>` : ''}
          </div>
        </div>

        <!-- 顧客名 -->
        <div style="border-bottom:1px solid #e0e0e0;padding-bottom:14px;margin-bottom:16px;">
          <div style="font-size:13px;color:#666;font-weight:600;margin-bottom:2px;">顧客名</div>
          <div style="font-size:26px;font-weight:bold;color:#222;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;">
            <span>${esc(d.customer || '顧客名なし')}</span>
            ${d.customerCode ? `<span style="font-size:14px;color:#666;font-weight:normal;background:#f0f0f0;padding:2px 8px;border-radius:4px;">コード: ${esc(d.customerCode)}</span>` : ''}
          </div>
        </div>

        <!-- アイテム詳細・個人名 -->
        <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:16px;background:#fafafa;padding:16px;border-radius:8px;border:1px solid #eee;">
          <div>
            <div style="font-size:12px;color:#666;font-weight:600;">商品名（アイテム）</div>
            <div style="font-size:18px;font-weight:bold;color:#333;margin-top:2px;">${esc(d.itemName || '—')}</div>
          </div>
          <div>
            <div style="font-size:12px;color:#666;font-weight:600;">サイズ</div>
            <div style="font-size:20px;font-weight:900;color:#d32f2f;margin-top:2px;">
              <span style="background:#ffebee;padding:2px 10px;border-radius:6px;border:1px solid #ffcdd2;">${esc(d.size || '—')}</span>
            </div>
          </div>
          <div>
            <div style="font-size:12px;color:#666;font-weight:600;">部署</div>
            <div style="font-size:16px;font-weight:bold;color:#444;margin-top:2px;">${esc(d.department || '—')}</div>
          </div>
          <div>
            <div style="font-size:12px;color:#666;font-weight:600;">個人名</div>
            <div style="font-size:18px;font-weight:bold;color:#1565c0;margin-top:2px;">${esc(d.person || '—')}</div>
          </div>
        </div>

        <div style="margin-top:12px;font-size:12px;color:#888;text-align:right;">
          バーコード: <strong style="color:#555;">${esc(d.masterCode)}</strong>
        </div>
      </div>
    `;
  }

  function renderNotFoundCard(code, msg) {
    const card = $('#bdelivcard');
    if (!card) return;

    card.innerHTML = `
      <div style="background:#ffebee;border:2px solid #ef5350;border-radius:12px;padding:24px;text-align:center;box-shadow:0 4px 12px rgba(0,0,0,0.06);margin-bottom:24px;">
        <div style="font-size:36px;margin-bottom:8px;">⚠️</div>
        <div style="font-size:22px;font-weight:bold;color:#c62828;margin-bottom:8px;">
          バーコード【${esc(code)}】のデータがありません
        </div>
        <p style="font-size:14px;color:#666;margin:0;">
          ${esc(msg || 'M_識別コードマスタまたは顧客出荷情報に一致するデータが見つかりませんでした。')}
        </p>
      </div>
    `;
  }

  function addToHistory(d) {
    // 直近10件を保持
    historyList.unshift(d);
    if (historyList.length > 10) historyList.pop();
    renderHistory();
  }

  function renderHistory() {
    const wrap = $('#bdelivhistory');
    if (!wrap) return;

    if (!historyList.length) {
      wrap.innerHTML = '<p style="color:#888;font-size:13px;margin:8px 0;">まだスキャン履歴はありません。</p>';
      return;
    }

    const rows = historyList.map(h => {
      const isNoDeliv = h.isNoDelivery || h.weekday === '集配なし' || (h.car && (h.car.includes('送り便') || h.car.includes('対象外')));
      const dayBg = isNoDeliv ? '#f5f5f5' : '#f1f8e9';
      const dayColor = isNoDeliv ? '#546e7a' : '#1b5e20';
      const carBg = isNoDeliv ? '#fff3e0' : '#e3f2fd';
      const carColor = isNoDeliv ? '#e65100' : '#0d47a1';

      return `
        <tr style="border-bottom:1px solid #eee;cursor:pointer;" onclick="window.reselectBarcodeHistory('${esc(h.masterCode)}')">
          <td style="padding:10px 12px;font-weight:bold;color:#555;font-family:monospace;">${esc(h.masterCode)}</td>
          <td style="padding:10px 12px;font-weight:bold;color:#222;">${esc(h.customer || '—')}</td>
          <td style="padding:10px 12px;text-align:center;font-weight:900;color:${dayColor};font-size:15px;background:${dayBg};">
            ${esc(h.weekday || '—')}
          </td>
          <td style="padding:10px 12px;text-align:center;font-weight:900;color:${carColor};font-size:14px;background:${carBg};">
            ${esc(h.car || '—')}
          </td>
          <td style="padding:10px 12px;color:#333;">${esc(h.itemName || '—')}</td>
          <td style="padding:10px 12px;text-align:center;font-weight:bold;color:#c62828;">${esc(h.size || '—')}</td>
          <td style="padding:10px 12px;color:#1565c0;">${esc(h.person || '—')}</td>
        </tr>
      `;
    }).join('');

    wrap.innerHTML = `
      <div style="margin-top:20px;background:#fff;border:1px solid #ddd;border-radius:8px;overflow:hidden;">
        <div style="background:#f5f7fa;padding:10px 16px;border-bottom:1px solid #ddd;font-weight:bold;color:#444;font-size:14px;display:flex;justify-content:space-between;align-items:center;">
          <span>📋 直近のスキャン履歴（最新10件）</span>
          <span style="font-size:12px;color:#888;font-weight:normal;">行をクリックで再表示</span>
        </div>
        <div style="overflow-x:auto;">
          <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead>
              <tr style="background:#fafafa;border-bottom:1px solid #eee;color:#666;font-size:12px;">
                <th style="padding:8px 12px;text-align:left;">バーコード</th>
                <th style="padding:8px 12px;text-align:left;">顧客名</th>
                <th style="padding:8px 12px;text-align:center;">集配曜日</th>
                <th style="padding:8px 12px;text-align:center;">号車</th>
                <th style="padding:8px 12px;text-align:left;">商品名</th>
                <th style="padding:8px 12px;text-align:center;">サイズ</th>
                <th style="padding:8px 12px;text-align:left;">個人名</th>
              </tr>
            </thead>
            <tbody>
              ${rows}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  window.reselectBarcodeHistory = function(code) {
    const item = historyList.find(h => h.masterCode === code);
    if (item) {
      renderSuccessCard(item);
      const input = $('#bdelivcode');
      if (input) input.focus();
    }
  };

  function initBarcodeDelivery() {
    const form = $('#bdelivform');
    const input = $('#bdelivcode');
    const clearBtn = $('#bdelivclear');

    if (form && !form.dataset.bound) {
      form.dataset.bound = '1';
      form.addEventListener('submit', e => {
        e.preventDefault();
        handleBarcodeSubmit(input?.value);
      });
    }

    if (clearBtn && !clearBtn.dataset.bound) {
      clearBtn.dataset.bound = '1';
      clearBtn.addEventListener('click', () => {
        if (input) {
          input.value = '';
          input.focus();
        }
      });
    }

    // Tab buttons
    const tabScan = $('#bdelivtab-scan');
    const tabList = $('#bdelivtab-list');
    const linkToList = $('#bdelivlink-tolist');

    if (tabScan && !tabScan.dataset.bound) {
      tabScan.dataset.bound = '1';
      tabScan.addEventListener('click', () => switchTab('scan'));
    }
    if (tabList && !tabList.dataset.bound) {
      tabList.dataset.bound = '1';
      tabList.addEventListener('click', () => switchTab('list'));
    }
    if (linkToList && !linkToList.dataset.bound) {
      linkToList.dataset.bound = '1';
      linkToList.addEventListener('click', e => {
        e.preventDefault();
        switchTab('list');
      });
    }

    // Customer filters
    const searchInput = $('#bdelivcustsearch');
    let searchDebounceTimer = null;
    if (searchInput && !searchInput.dataset.bound) {
      searchInput.dataset.bound = '1';
      searchInput.addEventListener('input', () => {
        clearTimeout(searchDebounceTimer);
        searchDebounceTimer = setTimeout(() => applyCustomerFilter(), 120);
      });
    }

    const typeSel = $('#bdelivcusttype');
    const carSel = $('#bdelivcustcar');
    const wdaySel = $('#bdelivcustwday');

    [typeSel, carSel, wdaySel].forEach(sel => {
      if (sel && !sel.dataset.bound) {
        sel.dataset.bound = '1';
        sel.addEventListener('change', () => applyCustomerFilter());
      }
    });

    // More button
    const moreBtn = $('#bdelivcustmore');
    if (moreBtn && !moreBtn.dataset.bound) {
      moreBtn.dataset.bound = '1';
      moreBtn.addEventListener('click', () => {
        displayLimit += 100;
        renderCustomerTable();
      });
    }

    // Sync button
    const syncBtn = $('#bdelivsyncbtn');
    if (syncBtn && !syncBtn.dataset.bound) {
      syncBtn.dataset.bound = '1';
      syncBtn.addEventListener('click', async () => {
        syncBtn.disabled = true;
        const origText = syncBtn.textContent;
        syncBtn.textContent = '🔄 同期中...';
        try {
          await loadCustomers(true);
        } finally {
          syncBtn.disabled = false;
          syncBtn.textContent = origText;
        }
      });
    }

    if (input) {
      setTimeout(() => input.focus(), 100);
    }
  }

  window.initBarcodeDelivery = initBarcodeDelivery;

  document.addEventListener('DOMContentLoaded', () => {
    initBarcodeDelivery();
  });
})();
