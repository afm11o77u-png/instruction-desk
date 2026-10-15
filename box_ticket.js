/* ボックス貼付用管理票：印刷レイアウトの純関数。現場一覧・手入力再発行の両方から使う。 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const BOX_TICKET_CSS =
    '@page{size:A4 landscape;margin:18mm 12mm 6mm}*{box-sizing:border-box}html,body{margin:0;background:white;color:black;font-family:"Yu Gothic",Meiryo,sans-serif}.page{width:273mm;height:186mm;display:grid;grid-template-columns:repeat(2,136.5mm);grid-template-rows:repeat(4,46.5mm);break-after:page}.page:last-child{break-after:auto}.ticket{width:136.5mm;height:46.5mm;border:1px solid black;padding:2mm 3mm;display:flex;flex-direction:column;gap:.6mm;overflow:hidden;font-size:10pt;line-height:1.2}.customer{font-weight:bold;font-size:13pt;overflow-wrap:anywhere}.person{display:flex;justify-content:space-between;gap:3mm;border-bottom:1px solid black;padding-bottom:.7mm}.person b{font-size:12pt}.person span{white-space:nowrap}.missing{flex:1;min-height:0;font-size:9pt}.missing>div{display:flex;justify-content:space-between;gap:2mm}.missing strong{white-space:nowrap}.request,.date{font-size:10pt}.date{border-top:1px solid black;padding-top:.6mm}';

  const BOX_TICKET_KINDS = ['repair', 'shortage'];
  const BOX_TICKET_REQUEST_OPTIONS = ['新規', 'サイズ交換', '消耗・破損交換', '追加・補充', '復職', '異動・移動', 'その他'];

  function boxTicketEsc(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function boxTicketSupplyLabel(it) {
    return [it && it.name, it && it.sku, it && it.size].filter(Boolean).join(' / ') || '商品名なし';
  }

  function activeBoxTicketShortages(o) {
    return (o && Array.isArray(o.shortages) ? o.shortages : []).filter(
      (s) => Number(s.qty) > 0 && s.supply !== '入荷済み'
    );
  }

  function boxTicketPages(o, esc, supplyLabel) {
    const escape = esc || boxTicketEsc;
    const label = supplyLabel || boxTicketSupplyLabel;
    const rows = activeBoxTicketShortages(o);
    const chunks = [];
    for (let i = 0; i < rows.length; i += 3) chunks.push(rows.slice(i, i + 3));
    if (!chunks.length) chunks.push([]);
    return chunks.map(
      (chunk, i) =>
        `<section class="ticket"><div class="customer">${escape(o.customer || '—')}</div><div class="person"><b>${escape(o.employee || '—')}</b><span>${escape(o.number || '—')}${chunks.length > 1 ? `　${i + 1}/${chunks.length}` : ''}</span></div><div class="missing"><b>不足商品</b>${
          chunk.length
            ? chunk
                .map(
                  (s) =>
                    `<div>${escape(label(s))}<strong>不足 ${escape(String(s.qty))} 枚</strong></div>`
                )
                .join('')
            : '<div>不足なし（修理待ち）</div>'
        }</div><div class="request"><b>依頼内容</b> ${escape(o.request || '—')}</div><div class="date"><b>マーク出荷日</b> ${escape(o.markShipDate || '未設定')}</div></section>`
    );
  }

  function boxTicketPreviewHtml(tickets, css) {
    const style = css || BOX_TICKET_CSS;
    const pages = Array.from({ length: Math.ceil((tickets.length || 0) / 8) }, (_, i) => {
      const slice = tickets.slice(i * 8, i * 8 + 8);
      return `<div class="page">${slice.join('')}</div>`;
    }).join('');
    return (
      '<!doctype html><html><head><meta charset="utf-8"><title>ボックス管理票</title><style>' +
      style +
      '@media screen{body{background:#eee;padding:12px}.page{background:white;margin-bottom:12px}}' +
      '</style></head><body>' +
      pages +
      '</body></html>'
    );
  }

  function normalizeManualShortages(rows) {
    const list = Array.isArray(rows) ? rows : [];
    return list
      .map((row) => {
        const name = String((row && row.name) || '').trim();
        const sku = String((row && row.sku) || '').trim();
        const size = String((row && row.size) || '').trim();
        const qty = Number(row && row.qty);
        if (!name && !sku && !size && !(Number.isInteger(qty) && qty > 0)) return null;
        return {
          name,
          sku,
          size,
          qty: Number.isInteger(qty) && qty > 0 ? qty : 0,
          supply: String((row && row.supply) || '').trim(),
        };
      })
      .filter(Boolean)
      .filter((s) => Number(s.qty) > 0 && s.supply !== '入荷済み');
  }

  function buildManualBoxTicketOrder(input) {
    const kind = input && input.kind === 'shortage' ? 'shortage' : 'repair';
    const customer = String((input && input.customer) || '').trim();
    const employee = String((input && input.employee) || '').trim();
    const number = String((input && input.number) || '').trim();
    const request = String((input && input.request) || '').trim();
    const markShipDate = String((input && input.markShipDate) || '').trim();
    const shortages = kind === 'shortage' ? normalizeManualShortages(input && input.shortages) : [];
    return {
      kind,
      customer,
      employee,
      number,
      request,
      markShipDate,
      shortages,
      printOnly: true,
    };
  }

  function validateManualBoxTicketOrder(order) {
    if (!order || !order.customer) return { ok: false, error: '顧客名を入力してください。' };
    if (!order.employee) return { ok: false, error: '従業員名を入力してください。' };
    if (!order.number) return { ok: false, error: '指示書番号を入力してください。' };
    if (order.kind === 'shortage' && !activeBoxTicketShortages(order).length) {
      return { ok: false, error: '在庫待ちでは不足商品と枚数を1件以上入力してください。' };
    }
    return { ok: true, order };
  }

  /** 手入力印刷はAPIを呼ばない。登録・更新パスは存在しない。 */
  function manualBoxTicketIsPrintOnly() {
    return true;
  }

  function manualBoxTicketMutatesOrders() {
    return false;
  }

  return {
    BOX_TICKET_CSS,
    BOX_TICKET_KINDS,
    BOX_TICKET_REQUEST_OPTIONS,
    boxTicketEsc,
    boxTicketSupplyLabel,
    activeBoxTicketShortages,
    boxTicketPages,
    boxTicketPreviewHtml,
    normalizeManualShortages,
    buildManualBoxTicketOrder,
    validateManualBoxTicketOrder,
    manualBoxTicketIsPrintOnly,
    manualBoxTicketMutatesOrders,
  };
});
