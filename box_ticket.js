/* ボックス貼付用管理票：印刷レイアウトの純関数。現場一覧・手入力再発行の両方から使う。 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  /* A4横 297×210mm・余白0・2×3の6分割。1枠 148.5×70mm。枠サイズは枚数に依存しない。 */
  const BOX_TICKET_PER_PAGE = 6;
  const BOX_TICKET_MANUAL_MAX_PEOPLE = 6;
  const BOX_TICKET_EMPTY_SLOT =
    '<section class="ticket ticket-empty" aria-hidden="true"></section>';
  const BOX_TICKET_CSS =
    '@page{size:A4 landscape;margin:0}*{box-sizing:border-box}html,body{margin:0;padding:0;background:white;color:black;font-family:"Yu Gothic",Meiryo,sans-serif}.page{width:297mm;height:210mm;display:grid;grid-template-columns:repeat(2,148.5mm);grid-template-rows:repeat(3,70mm);break-after:page;page-break-after:always}.page:last-child{break-after:auto;page-break-after:auto}.ticket{width:148.5mm;height:70mm;border:1px solid black;padding:3mm 3.5mm;display:flex;flex-direction:column;gap:1mm;overflow:hidden;font-size:13.5pt;line-height:1.25}.ticket-empty{padding:0}.ticket-empty>*{display:none}.head{display:flex;justify-content:space-between;align-items:flex-start;gap:2.5mm;min-width:0}.customer{font-weight:bold;font-size:18pt;line-height:1.2;min-width:0;flex:1;overflow-wrap:anywhere;word-break:break-word}.instr{flex:0 0 34mm;width:34mm;min-width:34mm;min-height:1.35em;text-align:right;font-size:11pt;line-height:1.25;overflow-wrap:anywhere;word-break:break-word;white-space:normal}.person{display:flex;justify-content:space-between;align-items:flex-start;gap:3mm;border-bottom:1px solid black;padding-bottom:1mm}.person b{font-size:16.5pt;line-height:1.2;min-width:0;flex:1;overflow-wrap:anywhere;word-break:break-word}.person span{white-space:nowrap;flex-shrink:0;padding-top:.2mm;font-size:12pt}.missing{flex:1;min-height:0;font-size:11.5pt;line-height:1.25;overflow:hidden}.missing>div{display:flex;justify-content:space-between;align-items:flex-start;gap:2mm;min-width:0;overflow-wrap:anywhere;word-break:break-word}.missing strong{white-space:nowrap;flex-shrink:0}.request,.date{font-size:13pt;line-height:1.25}.request{overflow-wrap:anywhere;word-break:break-word}.date{border-top:1px solid black;padding-top:1mm}';

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

  /** 管理票のマーク出荷日と同じ YYYY-MM-DD（ローカル日付）。 */
  function boxTicketToday(now) {
    const d = now instanceof Date ? now : new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  /** 「当日日付 発注」。指示書番号とは別項目。 */
  function boxTicketOrderDateLabel(now) {
    return `${boxTicketToday(now)} 発注`;
  }

  function boxTicketPageCount(ticketCount) {
    const n = Number(ticketCount) || 0;
    return n > 0 ? Math.ceil(n / BOX_TICKET_PER_PAGE) : 0;
  }

  function activeBoxTicketShortages(o) {
    return (o && Array.isArray(o.shortages) ? o.shortages : []).filter(
      (s) => Number(s.qty) > 0 && s.supply !== '入荷済み'
    );
  }

  /** 指示書番号。手入力の printOnly では instructionNumber のみ。現場は number。 */
  function boxTicketInstructionNumber(o) {
    if (!o) return '';
    if (o.instructionNumber != null && String(o.instructionNumber).trim() !== '') {
      return String(o.instructionNumber).trim();
    }
    if (o.printOnly) return '';
    return String(o.number || '').trim();
  }

  function boxTicketPages(o, esc, supplyLabel) {
    const escape = esc || boxTicketEsc;
    const label = supplyLabel || boxTicketSupplyLabel;
    const rows = activeBoxTicketShortages(o);
    const chunks = [];
    for (let i = 0; i < rows.length; i += 3) chunks.push(rows.slice(i, i + 3));
    if (!chunks.length) chunks.push([]);
    const orderDate = String(o.orderDateLabel || boxTicketOrderDateLabel(o && o.now)).trim();
    const instr = boxTicketInstructionNumber(o);
    return chunks.map((chunk, i) => {
      const cont = chunks.length > 1 ? `　${i + 1}/${chunks.length}` : '';
      return (
        `<section class="ticket"><div class="head"><div class="customer">${escape(o.customer || '—')}</div><div class="instr">${escape(instr)}</div></div><div class="person"><b>${escape(o.employee || '—')}</b><span>${escape(orderDate)}${cont}</span></div><div class="missing"><b>不足商品</b>${
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
    });
  }

  function boxTicketPreviewHtml(tickets, css) {
    const style = css || BOX_TICKET_CSS;
    const list = Array.isArray(tickets) ? tickets : [];
    const pageCount = boxTicketPageCount(list.length);
    const pages = Array.from({ length: pageCount }, (_, i) => {
      const slice = list.slice(i * BOX_TICKET_PER_PAGE, i * BOX_TICKET_PER_PAGE + BOX_TICKET_PER_PAGE);
      while (slice.length < BOX_TICKET_PER_PAGE) slice.push(BOX_TICKET_EMPTY_SLOT);
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

  function isManualBoxTicketPersonBlank(input) {
    const customer = String((input && input.customer) || '').trim();
    const employee = String((input && input.employee) || '').trim();
    const request = String((input && input.request) || '').trim();
    const markShipDate = String((input && input.markShipDate) || '').trim();
    const instructionNumber = String((input && input.instructionNumber) || '').trim();
    const shortages = normalizeManualShortages(input && input.shortages);
    return !customer && !employee && !request && !markShipDate && !instructionNumber && !shortages.length;
  }

  function buildManualBoxTicketOrder(input) {
    const kind = input && input.kind === 'shortage' ? 'shortage' : 'repair';
    const customer = String((input && input.customer) || '').trim();
    const employee = String((input && input.employee) || '').trim();
    const instructionNumber = String((input && input.instructionNumber) || '').trim();
    const request = String((input && input.request) || '').trim();
    const markShipDate = String((input && input.markShipDate) || '').trim();
    const shortages = kind === 'shortage' ? normalizeManualShortages(input && input.shortages) : [];
    const orderDateLabel = boxTicketOrderDateLabel(input && input.now);
    return {
      kind,
      customer,
      employee,
      instructionNumber,
      orderDateLabel,
      number: orderDateLabel,
      request,
      markShipDate,
      shortages,
      printOnly: true,
    };
  }

  function validateManualBoxTicketOrder(order) {
    if (!order || !order.customer) return { ok: false, error: '顧客名を入力してください。' };
    if (!order.employee) return { ok: false, error: '従業員名を入力してください。' };
    if (order.kind === 'shortage' && !activeBoxTicketShortages(order).length) {
      return { ok: false, error: '在庫待ちでは不足商品と枚数を1件以上入力してください。' };
    }
    return { ok: true, order };
  }

  /** 手入力の複数人分。完全未入力はスキップ。入力途中はエラー。 */
  function buildManualBoxTicketOrders(people, shared) {
    const kind = shared && shared.kind === 'shortage' ? 'shortage' : 'repair';
    const now = shared && shared.now;
    const list = Array.isArray(people) ? people : [];
    const orders = [];
    for (let i = 0; i < list.length; i++) {
      const person = { ...(list[i] || {}), kind, now };
      if (isManualBoxTicketPersonBlank(person)) continue;
      const built = buildManualBoxTicketOrder(person);
      const checked = validateManualBoxTicketOrder(built);
      if (!checked.ok) {
        return { ok: false, error: `${i + 1}人目: ${checked.error}`, orders: [] };
      }
      orders.push(checked.order);
    }
    if (!orders.length) return { ok: false, error: '1人分以上入力してください。', orders: [] };
    if (list.length > BOX_TICKET_MANUAL_MAX_PEOPLE) {
      return { ok: false, error: `手入力は最大${BOX_TICKET_MANUAL_MAX_PEOPLE}人までです。`, orders: [] };
    }
    return { ok: true, orders };
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
    BOX_TICKET_PER_PAGE,
    BOX_TICKET_MANUAL_MAX_PEOPLE,
    BOX_TICKET_EMPTY_SLOT,
    BOX_TICKET_KINDS,
    BOX_TICKET_REQUEST_OPTIONS,
    boxTicketEsc,
    boxTicketSupplyLabel,
    boxTicketToday,
    boxTicketOrderDateLabel,
    boxTicketPageCount,
    boxTicketInstructionNumber,
    activeBoxTicketShortages,
    boxTicketPages,
    boxTicketPreviewHtml,
    normalizeManualShortages,
    isManualBoxTicketPersonBlank,
    buildManualBoxTicketOrder,
    buildManualBoxTicketOrders,
    validateManualBoxTicketOrder,
    manualBoxTicketIsPrintOnly,
    manualBoxTicketMutatesOrders,
  };
});
