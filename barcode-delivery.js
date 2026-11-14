(() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  let historyList = [];

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

    card.innerHTML = `
      <div style="background:#fff;border:2px solid #2e7d32;border-radius:12px;padding:24px;box-shadow:0 4px 12px rgba(0,0,0,0.08);margin-bottom:24px;">
        
        <!-- 最重要情報: 集配曜日 & 集配号車 (超特大表示) -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;background:#e8f5e9;padding:20px;border-radius:10px;border:1.5px solid #a5d6a7;margin-bottom:20px;">
          <div style="text-align:center;border-right:2px dashed #81c784;padding-right:12px;">
            <div style="font-size:16px;font-weight:bold;color:#1b5e20;margin-bottom:6px;display:flex;align-items:center;justify-content:center;gap:6px;">
              <span>📅</span> 集配曜日
            </div>
            <div style="font-size:42px;font-weight:900;color:#1b5e20;letter-spacing:1px;line-height:1.1;">
              ${esc(weekday)}
            </div>
            ${timeBand ? `<div style="font-size:15px;color:#2e7d32;font-weight:bold;margin-top:8px;">⏰ ${esc(timeBand)}</div>` : ''}
          </div>
          
          <div style="text-align:center;padding-left:12px;">
            <div style="font-size:16px;font-weight:bold;color:#0d47a1;margin-bottom:6px;display:flex;align-items:center;justify-content:center;gap:6px;">
              <span>🚚</span> 集配号車 / サイクル
            </div>
            <div style="font-size:42px;font-weight:900;color:#0d47a1;letter-spacing:1px;line-height:1.1;">
              ${esc(car)}
            </div>
            ${method ? `<div style="font-size:15px;color:#1565c0;font-weight:bold;margin-top:8px;">📦 ${esc(method)}</div>` : ''}
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
      return `
        <tr style="border-bottom:1px solid #eee;cursor:pointer;" onclick="window.reselectBarcodeHistory('${esc(h.masterCode)}')">
          <td style="padding:10px 12px;font-weight:bold;color:#555;font-family:monospace;">${esc(h.masterCode)}</td>
          <td style="padding:10px 12px;font-weight:bold;color:#222;">${esc(h.customer || '—')}</td>
          <td style="padding:10px 12px;text-align:center;font-weight:900;color:#1b5e20;font-size:15px;background:#f1f8e9;">
            ${esc(h.weekday || '—')}
          </td>
          <td style="padding:10px 12px;text-align:center;font-weight:900;color:#0d47a1;font-size:15px;background:#e3f2fd;">
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

    if (input) {
      setTimeout(() => input.focus(), 100);
    }
  }

  window.initBarcodeDelivery = initBarcodeDelivery;

  document.addEventListener('DOMContentLoaded', () => {
    initBarcodeDelivery();
  });
})();
