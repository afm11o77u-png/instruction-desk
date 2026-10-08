/** 入庫・出庫 screen helpers (uses opening search pickers from app.js / opening_pickers.js). */
(function (global) {
  function canWriteStock(state) {
    return !(state.data && state.data.canWrite === false);
  }

  function newMoveToken() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'm-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
  }

  function targetLabel(t) {
    return [
      t.customerName,
      t.code,
      t.productName,
      t.color,
      t.embroideryLabel,
      t.size,
      t.locationLabel,
    ]
      .filter(Boolean)
      .join(' / ');
  }

  function filterTargets(targets, query) {
    const needle = String(query || '')
      .trim()
      .toLowerCase();
    if (!needle) return targets || [];
    return (targets || []).filter((t) =>
      targetLabel(t).toLowerCase().includes(needle) ||
      String(t.productId || '').includes(needle)
    );
  }

  function qtyStepperHtml(idPrefix, value) {
    return `<div class="qty-stepper">
      <button type="button" class="quiet" id="${idPrefix}Minus" aria-label="減らす">−</button>
      <input id="${idPrefix}Qty" inputmode="numeric" value="${value}" aria-label="数量">
      <button type="button" class="quiet" id="${idPrefix}Plus" aria-label="増やす">＋</button>
    </div>`;
  }

  function bindQtyStepper(root, idPrefix, { min = 1, max = 99999, onChange } = {}) {
    const input = root.querySelector('#' + idPrefix + 'Qty');
    const minus = root.querySelector('#' + idPrefix + 'Minus');
    const plus = root.querySelector('#' + idPrefix + 'Plus');
    const read = () => {
      const t = String(input.value || '').trim().replace(/,/g, '');
      if (!/^\d+$/.test(t)) return null;
      return Number(t);
    };
    const emit = () => {
      if (typeof onChange === 'function') onChange(read());
    };
    minus.onclick = () => {
      const n = read();
      const next = Math.max(min, (n == null ? min : n) - 1);
      input.value = String(next);
      emit();
    };
    plus.onclick = () => {
      const n = read();
      const next = Math.min(max, (n == null ? min : n) + 1);
      input.value = String(next);
      emit();
    };
    input.oninput = emit;
    return { read, input };
  }

  function allocationTableHtml(plan, esc, yen, num) {
    if (!plan || !plan.allocations || !plan.allocations.length) return '<p>払出し予定はありません。</p>';
    return `<div class="tablewrap"><table><thead><tr><th>入荷</th><th>枚数</th><th>単価</th><th>出庫金額</th></tr></thead><tbody>
      ${plan.allocations
        .map((a) => {
          const day = a.receivedUnknown
            ? '入荷日不明・払出し順 ' + a.payoutOrder
            : '入荷日 ' + esc(a.receivedOn || '');
          return `<tr><td>${day}<span class="sub">ロット ${a.lotId}</span></td><td class="qty">${num(a.qty)}</td><td class="money">${yen(a.unitCost)}</td><td class="money">${yen(a.amount)}</td></tr>`;
        })
        .join('')}
    </tbody></table></div>
    <p>出庫金額合計 <strong>${yen(plan.totalAmount)}</strong></p>`;
  }

  global.StockMoves = {
    canWriteStock,
    newMoveToken,
    targetLabel,
    filterTargets,
    qtyStepperHtml,
    bindQtyStepper,
    allocationTableHtml,
  };
})(typeof window !== 'undefined' ? window : globalThis);
