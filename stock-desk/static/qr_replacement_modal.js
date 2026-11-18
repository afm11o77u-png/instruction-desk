/** QRコード貼り替えが必要な商品（56件）の共通モーダル・一覧表示・CSV出力 */
(function(){
  const BASE = location.pathname.startsWith('/inventory') ? '/inventory' : ''
  let modalEl = null
  let cachedData = null
  let isBusy = false

  function esc(s){
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
  }

  function ensureModal(){
    if (modalEl && modalEl.isConnected) return modalEl
    modalEl = document.createElement('dialog')
    modalEl.id = 'qrReplacementModal'
    modalEl.className = 'qr-replacement-dialog'
    modalEl.style.cssText = 'max-width:960px; width:95vw; max-height:85vh; border-radius:10px; border:1px solid #d9d9d9; padding:0; box-shadow:0 8px 24px rgba(0,0,0,0.2); overflow:hidden;'
    document.body.appendChild(modalEl)
    return modalEl
  }

  async function loadData(){
    if (cachedData) return cachedData
    const res = await fetch(`${BASE}/api/qr-replacement/targets`, {
      headers: { 'X-Stock-Request': '1' }
    })
    if (!res.ok) throw new Error('貼り替え対象データを取得できませんでした。')
    cachedData = await res.json()
    return cachedData
  }

  function renderContent(data){
    const targets = (data && data.targets) || []
    const totalCount = data ? data.totalCount : targets.length
    const mergedLots = (data && data.mergedLots) || []
    const mergedLotCount = data && data.mergedLotCount != null ? data.mergedLotCount : mergedLots.length

    const rows = targets.map((t, idx) => {
      const items = t.items || []
      const rowspan = items.length || 1
      const itemRows = items.map((item, itemIdx) => {
        const locs = (item.locations || []).map(l => esc(l.display)).join('<br>') || '<span style="color:#8c8c8c;">在庫なし</span>'
        const qrBtn = item.productId
          ? `<button type="button" class="quiet qr-preview-btn" data-pid="${item.productId}" data-code="${esc(item.code)}" data-name="${esc(item.name)}" style="padding:2px 8px; font-size:12px;">新QR</button>`
          : ''

        if (itemIdx === 0) {
          return `
            <tr>
              <td rowspan="${rowspan}" style="vertical-align:top; font-weight:bold; background:#fafafa;">
                ${idx + 1}. ${esc(t.originalCode)}
                <div style="font-size:11px; color:#d46b08; font-weight:normal;">${esc(t.splitType)}</div>
              </td>
              <td style="font-weight:bold;">${esc(item.code)}</td>
              <td>${esc(item.name)}</td>
              <td>${esc(item.color || '—')}</td>
              <td style="text-align:right; font-weight:bold;">${item.totalQty}着</td>
              <td style="font-size:12px;">${locs}</td>
              <td style="text-align:center;">${qrBtn}</td>
            </tr>
          `
        } else {
          return `
            <tr>
              <td style="font-weight:bold;">${esc(item.code)}</td>
              <td>${esc(item.name)}</td>
              <td>${esc(item.color || '—')}</td>
              <td style="text-align:right; font-weight:bold;">${item.totalQty}着</td>
              <td style="font-size:12px;">${locs}</td>
              <td style="text-align:center;">${qrBtn}</td>
            </tr>
          `
        }
      }).join('')
      return itemRows
    }).join('')

    const mergedRows = mergedLots.map((m, idx) => `
      <tr>
        <td>${idx + 1}. ${esc(m.customerName)}<div style="font-size:11px; color:#d46b08;">${esc(m.reason)}</div></td>
        <td style="font-weight:bold;">${esc(m.code)}</td>
        <td>${esc(m.name)} (${esc(m.size)} / ${esc(m.conditionLabel)})</td>
        <td>${esc(m.color || '—')}</td>
        <td style="text-align:right; font-weight:bold;">${m.qty}着</td>
        <td style="font-size:12px;">${esc(m.place)}</td>
        <td style="text-align:center;"><button type="button" class="quiet qr-new-lot-btn" data-cpid="${m.customerProductId}" data-size="${esc(m.size)}" data-cond="${esc(m.condition)}" style="padding:2px 8px; font-size:12px;">新QR</button></td>
      </tr>
    `).join('')

    return `
      <div style="display:flex; flex-direction:column; max-height:85vh;">
        <div style="padding:16px 20px; background:#fffbe6; border-bottom:2px solid #faad14; display:flex; justify-content:space-between; align-items:flex-start;">
          <div>
            <h3 style="margin:0 0 6px 0; color:#d46b08; font-size:17px; display:flex; align-items:center; gap:8px;">
              <span>⚠️</span> <span>QRコードの貼り替えが必要な商品・在庫（分割 ${totalCount}件 ／ 合算・未対応 ${mergedLotCount}件）</span>
            </h3>
            <p style="margin:0; font-size:13px; color:#595959; line-height:1.5;">
              複数色が混在していた商品（${totalCount}件）や、同じ在庫に合算されて旧QRが複数ある在庫（${mergedLotCount}件）です。<br>
              <strong>旧QRのままでは色や在庫行が特定できないため、新QRコードを発行して貼り替えてください。</strong>
            </p>
          </div>
          <button type="button" id="qrModalClose" style="background:none; border:none; font-size:20px; cursor:pointer; color:#8c8c8c; padding:4px 8px;">✕</button>
        </div>

        <div style="padding:10px 20px; background:#fafafa; border-bottom:1px solid #e8e8e8; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
          <div style="font-size:13px; color:#262626;">
            色・規格分割: <strong>${totalCount}件</strong>（${(data && data.splitCodes ? data.splitCodes.length : '—')}品番） ／ 合算・旧QR未対応ロット: <strong>${mergedLotCount}件</strong>
          </div>
          <div style="display:flex; gap:8px;">
            <a href="${BASE}/api/qr-replacement/export.csv" download="qr-replacement-targets.csv" class="button primary" style="text-decoration:none; display:inline-flex; align-items:center; gap:4px; font-size:13px; padding:6px 14px;">
              <span>📥</span> <span>作業用CSV（Excel）を出力</span>
            </a>
            <button type="button" id="qrModalPrint" style="font-size:13px; padding:6px 14px;">🖨️ 一覧を印刷</button>
          </div>
        </div>

        <div style="flex:1; overflow-y:auto; padding:12px 20px;" class="tablewrap">
          <h4 style="margin:4px 0 8px 0; font-size:14px;">1. 色・規格が分割された商品（${totalCount}件）</h4>
          <table class="data" style="width:100%; font-size:13px; border-collapse:collapse;">
            <thead>
              <tr style="background:#f0f0f0;">
                <th style="width:16%;">元品番</th>
                <th style="width:18%;">新商品コード</th>
                <th style="width:20%;">商品名</th>
                <th style="width:12%;">色</th>
                <th style="width:10%; text-align:right;">現在庫</th>
                <th style="width:18%;">保管場所 / 棚</th>
                <th style="width:6%; text-align:center;">新QR</th>
              </tr>
            </thead>
            <tbody>
              ${rows || '<tr><td colspan="7" style="text-align:center; padding:20px;">対象商品がありません。</td></tr>'}
            </tbody>
          </table>
          ${mergedLots.length ? `
          <h4 style="margin:20px 0 8px 0; font-size:14px;">2. 同じ在庫に合算・旧QR未対応の在庫ロット（${mergedLotCount}件）</h4>
          <table class="data" style="width:100%; font-size:13px; border-collapse:collapse;">
            <thead>
              <tr style="background:#f0f0f0;">
                <th style="width:16%;">顧客 / 理由</th>
                <th style="width:14%;">品番</th>
                <th style="width:24%;">商品名 (サイズ / 状態)</th>
                <th style="width:10%;">色</th>
                <th style="width:8%; text-align:right;">現在庫</th>
                <th style="width:20%;">保管場所 / 棚</th>
                <th style="width:8%; text-align:center;">新QR</th>
              </tr>
            </thead>
            <tbody>
              ${mergedRows}
            </tbody>
          </table>` : ''}
        </div>

        <div style="padding:12px 20px; background:#f5f5f5; border-top:1px solid #e8e8e8; text-align:right;">
          <button type="button" id="qrModalBottomClose" style="padding:6px 18px;">閉じる</button>
        </div>
      </div>
    `
  }

  async function open(){
    const dlg = ensureModal()
    dlg.innerHTML = '<div style="padding:40px; text-align:center; font-size:15px; color:#595959;">貼り替え対象データを読み込み中...</div>'
    dlg.showModal()

    try {
      const data = await loadData()
      dlg.innerHTML = renderContent(data)

      const closeBtn = dlg.querySelector('#qrModalClose')
      const bottomCloseBtn = dlg.querySelector('#qrModalBottomClose')
      const printBtn = dlg.querySelector('#qrModalPrint')

      const closeHandler = () => dlg.close()
      if (closeBtn) closeBtn.onclick = closeHandler
      if (bottomCloseBtn) bottomCloseBtn.onclick = closeHandler
      if (printBtn) printBtn.onclick = () => window.print()

      // 合算・未対応ロットの新QR発行ボタン
      dlg.querySelectorAll('.qr-new-lot-btn').forEach(btn => {
        btn.onclick = async () => {
          btn.disabled = true
          try {
            const r = await api('/api/stock-qr', {
              customerProductId: Number(btn.dataset.cpid),
              size: btn.dataset.size,
              condition: btn.dataset.cond,
              preferLegacy: false,
            })
            dlg.close()
            if (typeof showStockQrDialog === 'function') showStockQrDialog(r)
          } catch (e) {
            alert((e && e.message) || '新QRを発行できませんでした。')
            btn.disabled = false
          }
        }
      })

      // 新QRプレビューボタン
      dlg.querySelectorAll('.qr-preview-btn').forEach(btn => {
        btn.onclick = () => {
          const pid = btn.dataset.pid
          const code = btn.dataset.code
          const name = btn.dataset.name
          if (typeof window.stockOpenProductModal === 'function' && pid) {
            dlg.close()
            window.stockOpenProductModal(pid)
          } else {
            alert(`新商品: ${code}\n商品名: ${name}\n\n在庫一覧または商品管理から新QRコードを発行できます。`)
          }
        }
      })
    } catch (err) {
      dlg.innerHTML = `
        <div style="padding:24px; text-align:center;">
          <p style="color:#cf1322;">${esc(err.message)}</p>
          <button type="button" onclick="document.getElementById('qrReplacementModal').close()">閉じる</button>
        </div>
      `
    }
  }

  window.QrReplacementModal = { open, loadData }
})()
