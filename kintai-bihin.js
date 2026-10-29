/* 月別備品棚卸。権限は mark_kintai、または管理者チェック（manage/overview）。主管理者は常に可。
   在庫デスク入出庫・出勤簿実績・シフト表には書き込まない。
   保存は「保存」押下時のみ（表示中年月を上書き）。自動保存しない。 */
(function () {
  let sheet = null;
  let rows = [];
  let year = 0;
  let month = 0;
  let version = 0;
  let saveState = 'idle'; // idle | dirty | saving | saved | failed
  let saveError = '';
  let bihinDirty = false;
  let saving = false;
  let loading = false;
  let leaveDialogResolver = null;
  let masterItems = [];
  let orderVendors = [];
  let orderPrepKey = '';
  let orderBusy = false;
  /** 表示中月シートの選択行（rowKey）。保存はせず、削除は画面上の行だけ。 */
  let selectedRowKeys = new Set();

  function $(sel, root) { return (root || document).querySelector(sel); }

  function canKintai() {
    return !!currentUser && (currentUser.role === 'owner' || !!currentUser.manage || !!currentUser.overview || !!currentUser.mark_kintai);
  }

  function todayYm() {
    const d = new Date();
    return { year: d.getFullYear(), month: d.getMonth() + 1 };
  }

  function ymValue(y, m) {
    return `${y}-${String(m).padStart(2, '0')}`;
  }

  function parseYm(value) {
    const m = String(value || '').match(/^(\d{4})-(\d{2})$/);
    if (!m) return null;
    const y = Number(m[1]);
    const mo = Number(m[2]);
    if (!y || mo < 1 || mo > 12) return null;
    return { year: y, month: mo };
  }

  function cloneData(src) {
    return JSON.parse(JSON.stringify(src == null ? [] : src));
  }

  function toast(msg) {
    if (typeof window.toast === 'function') window.toast(msg);
    else if (typeof message === 'function') message(msg);
    else console.log(msg);
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /** 数量×単価を四捨五入（round half up）して円。qty が null/空なら null。 */
  function moneyYen(qty, unitPrice) {
    if (qty === null || qty === undefined || qty === '') return null;
    const q = Number(qty);
    const p = Number(unitPrice);
    if (!Number.isFinite(q) || !Number.isFinite(p)) return null;
    const raw = q * p;
    // half-up to integer yen
    return Math.sign(raw) * Math.floor(Math.abs(raw) + 0.5);
  }

  function usageQty(opening, purchase, closing) {
    if (opening === null || opening === undefined || opening === '') return null;
    if (purchase === null || purchase === undefined || purchase === '') return null;
    if (closing === null || closing === undefined || closing === '') return null;
    const o = Number(opening);
    const p = Number(purchase);
    const c = Number(closing);
    if (![o, p, c].every(Number.isFinite)) return null;
    return o + p - c;
  }

  function parseQtyInput(raw) {
    const s = String(raw == null ? '' : raw).trim().replace(/,/g, '');
    if (s === '') return { ok: true, value: null };
    const n = Number(s);
    if (!Number.isFinite(n)) return { ok: false, error: '数量の形式が不正です。' };
    if (n < 0) return { ok: false, error: '負の値は使えません。' };
    return { ok: true, value: n };
  }

  function formatNum(v) {
    if (v === null || v === undefined || v === '') return '';
    return String(v);
  }

  function formatYen(v) {
    if (v === null || v === undefined || v === '') return '—';
    return Number(v).toLocaleString('ja-JP');
  }

  function enrichRow(r) {
    const unitPrice = r.unitPrice == null || r.unitPrice === '' ? 0 : Number(r.unitPrice);
    const opening = r.openingQty;
    const purchase = r.purchaseQty;
    const closing = r.closingQty;
    const used = usageQty(opening, purchase, closing);
    return {
      ...r,
      unitPrice,
      openingAmount: moneyYen(opening, unitPrice),
      purchaseAmount: moneyYen(purchase, unitPrice),
      closingAmount: moneyYen(closing, unitPrice),
      usageQty: used,
      usageAmount: used === null ? null : moneyYen(used, unitPrice),
      usageIncomplete: used === null,
      negativeUsage: used !== null && used < 0,
    };
  }

  function recomputeAll() {
    rows = rows.map(enrichRow);
  }

  function formatSavedAt(iso) {
    if (!iso) return '';
    const s = String(iso).replace('T', ' ').trim();
    return s.length > 19 ? s.slice(0, 19) : s;
  }

  function setSaveState(state, err) {
    saveState = state;
    saveError = err || '';
    const el = $('#bihinsavestatus');
    if (!el) return;
    el.className = 'shift-save-status shift-save-' + state;
    if (state === 'idle') el.textContent = '未保存';
    else if (state === 'dirty') el.textContent = '未保存の変更があります';
    else if (state === 'saving') el.textContent = '保存中…';
    else if (state === 'saved') {
      const when = formatSavedAt(sheet && sheet.updatedAt);
      el.textContent = when ? `保存済み（${when}）` : '保存済み';
    } else if (state === 'failed') {
      el.textContent = '保存失敗' + (saveError ? '：' + saveError : '');
    }
    const btn = $('#bihinsave');
    if (btn) btn.disabled = state === 'saving' || (state === 'saved' && !bihinDirty);
  }

  function markDirty() {
    bihinDirty = true;
    setSaveState('dirty');
  }

  function applySheet(data) {
    sheet = data || null;
    year = data && data.year != null ? data.year : year;
    month = data && data.month != null ? data.month : month;
    version = data && data.version != null ? data.version : 0;
    rows = cloneData((data && data.rows) || []).map(enrichRow);
    masterItems = cloneData((data && data.items) || []);
    selectedRowKeys = new Set();
    bihinDirty = false;
    const input = $('#bihinyearmonth');
    if (input && year && month) input.value = ymValue(year, month);
    paintBihin();
    paintNotices(data);
    const meta = $('#bihinmeta');
    if (meta) {
      const when = data && data.updatedAt ? `最終保存 ${data.updatedAt}` : 'まだ保存がありません';
      const who = data && data.updatedBy ? `（${data.updatedBy}）` : '';
      meta.textContent = `${year}年${month}月 ・ ${rows.length}行 ・ ${when}${who}`;
    }
  }

  function rowKeyOf(r, i) {
    if (r && r.rowKey != null && r.rowKey !== '') return String(r.rowKey);
    if (r && r.itemId != null && r.itemId !== '') return 'item:' + r.itemId;
    return 'idx:' + i;
  }

  function updateRowDeleteButton() {
    const btn = $('#bihinrowdelete');
    if (!btn) return;
    btn.disabled = selectedRowKeys.size === 0;
  }

  function syncHeaderCheck() {
    const head = $('#bihinselectall');
    if (!head) return;
    const n = rows.length;
    const sel = selectedRowKeys.size;
    head.checked = n > 0 && sel === n;
    head.indeterminate = sel > 0 && sel < n;
  }

  function paintNotices(data) {
    const el = $('#bihinnotice');
    if (!el) return;
    const parts = [];
    (data && data.notices || []).forEach(n => {
      if (n && n.message) parts.push(n.message);
    });
    const mm = (data && data.carryMismatches) || [];
    if (mm.length) {
      const sample = mm.slice(0, 5).map(m => {
        const prev = m.previousClosingQty == null ? '（空）' : m.previousClosingQty;
        const cur = m.openingQty == null ? '（空）' : m.openingQty;
        return `${m.name || '（無題）'}: 前月末 ${prev} → 当月初 ${cur}`;
      }).join('／');
      parts.push(`食い違い ${mm.length}件（${sample}${mm.length > 5 ? ' …' : ''}）`);
    }
    if (!parts.length) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.textContent = parts.join(' ');
  }

  function totalsFromRows() {
    const keys = ['openingAmount', 'purchaseAmount', 'closingAmount', 'usageAmount'];
    const out = {};
    keys.forEach(k => {
      let any = false;
      let sum = 0;
      rows.forEach(r => {
        if (r[k] == null) return;
        any = true;
        sum += Number(r[k]) || 0;
      });
      out[k] = any ? sum : null;
    });
    return out;
  }

  function paintBihin() {
    const table = $('#bihingrid');
    if (!table) return;
    if (!rows.length) {
      selectedRowKeys = new Set();
      table.innerHTML = `<tbody><tr><td class="bihin-empty-msg" colspan="13">
        表示する品目がありません。右上の「品目マスタ」から科目・品名・単位・単価を登録してください（カタログの推測登録はしません）。
      </td></tr></tbody>`;
      updateRowDeleteButton();
      return;
    }
    const validKeys = new Set(rows.map((r, i) => rowKeyOf(r, i)));
    selectedRowKeys = new Set([...selectedRowKeys].filter(k => validKeys.has(k)));
    const head = `<thead>
      <tr class="bihin-head-groups">
        <th rowspan="2" class="bihin-row-check-col no-print">
          <input type="checkbox" id="bihinselectall" class="bihin-row-check no-print" aria-label="すべての行を選択">
        </th>
        <th rowspan="2">科目</th>
        <th rowspan="2">品名</th>
        <th rowspan="2">単位</th>
        <th rowspan="2">単価</th>
        <th colspan="2">月初在庫</th>
        <th colspan="2">当月購入</th>
        <th colspan="2">月末在庫</th>
        <th colspan="2">当月使用</th>
      </tr>
      <tr class="bihin-head-fields">
        <th>数量</th><th>金額</th>
        <th>数量</th><th>金額</th>
        <th>数量</th><th>金額</th>
        <th>数量</th><th>金額</th>
      </tr>
    </thead>`;
    const body = '<tbody>' + rows.map((r, i) => {
      const warn = r.negativeUsage ? ' bihin-warn' : '';
      const usageQtyText = r.usageIncomplete ? '—' : formatNum(r.usageQty);
      const usageAmtText = r.usageIncomplete ? '—' : formatYen(r.usageAmount);
      const key = rowKeyOf(r, i);
      const checked = selectedRowKeys.has(key) ? ' checked' : '';
      return `<tr data-row-index="${i}" data-row-key="${esc(key)}">
        <td class="bihin-row-check-col no-print"><input type="checkbox" class="bihin-row-check no-print" data-row-key="${esc(key)}" aria-label="${esc(r.name || '行')}を選択"${checked}></td>
        <td class="bihin-cat bihin-calc">${esc(r.category)}</td>
        <td class="bihin-name bihin-calc">${esc(r.name)}</td>
        <td class="bihin-unit bihin-calc">${esc(r.unit)}</td>
        <td class="bihin-num bihin-calc">${formatYen(r.unitPrice)}</td>
        <td class="bihin-num bihin-input"><input class="bihin-qty" data-field="openingQty" data-i="${i}" inputmode="decimal" value="${esc(formatNum(r.openingQty))}" aria-label="月初数量"></td>
        <td class="bihin-num bihin-calc">${formatYen(r.openingAmount)}</td>
        <td class="bihin-num bihin-input"><input class="bihin-qty" data-field="purchaseQty" data-i="${i}" inputmode="decimal" value="${esc(formatNum(r.purchaseQty))}" aria-label="購入数量"></td>
        <td class="bihin-num bihin-calc">${formatYen(r.purchaseAmount)}</td>
        <td class="bihin-num bihin-input"><input class="bihin-qty" data-field="closingQty" data-i="${i}" inputmode="decimal" value="${esc(formatNum(r.closingQty))}" aria-label="月末数量"></td>
        <td class="bihin-num bihin-calc">${formatYen(r.closingAmount)}</td>
        <td class="bihin-num bihin-calc${warn}">${esc(usageQtyText)}</td>
        <td class="bihin-num bihin-calc${warn}">${usageAmtText}</td>
      </tr>`;
    }).join('') + '</tbody>';
    const t = totalsFromRows();
    const foot = `<tfoot><tr>
      <td colspan="5">金額合計（数量は単位が異なるため合計しません）</td>
      <td class="bihin-calc"></td><td class="bihin-num bihin-calc">${formatYen(t.openingAmount)}</td>
      <td class="bihin-calc"></td><td class="bihin-num bihin-calc">${formatYen(t.purchaseAmount)}</td>
      <td class="bihin-calc"></td><td class="bihin-num bihin-calc">${formatYen(t.closingAmount)}</td>
      <td class="bihin-calc"></td><td class="bihin-num bihin-calc">${formatYen(t.usageAmount)}</td>
    </tr></tfoot>`;
    table.innerHTML = head + body + foot;
    syncHeaderCheck();
    updateRowDeleteButton();
  }

  function deleteSelectedRows() {
    if (!selectedRowKeys.size) return;
    const doomed = [];
    rows.forEach((r, i) => {
      const key = rowKeyOf(r, i);
      if (selectedRowKeys.has(key)) doomed.push({ key, row: r, index: i });
    });
    if (!doomed.length) return;
    const ym = `${year}年${month}月`;
    const lines = doomed.map(d => {
      const name = d.row.name || '（無題）';
      const price = formatYen(d.row.unitPrice);
      return `・${name}（単価 ${price}）`;
    }).join('\n');
    const ok = confirm(
      `${ym}の次の行を削除しますか？\n\n${lines}\n\n` +
      '表示中の月の棚卸行だけを消します。品目マスタ・他の月・発注履歴は変わりません。\n' +
      '保存するまで確定しません（未保存の変更になります）。'
    );
    if (!ok) return;
    const drop = new Set(doomed.map(d => d.key));
    rows = rows
      .filter((r, i) => !drop.has(rowKeyOf(r, i)))
      .map(enrichRow);
    selectedRowKeys = new Set();
    markDirty();
    paintBihin();
    const meta = $('#bihinmeta');
    if (meta) {
      const when = sheet && sheet.updatedAt ? `最終保存 ${sheet.updatedAt}` : 'まだ保存がありません';
      const who = sheet && sheet.updatedBy ? `（${sheet.updatedBy}）` : '';
      meta.textContent = `${year}年${month}月 ・ ${rows.length}行 ・ ${when}${who}`;
    }
    toast(`${doomed.length}行を削除しました（未保存）。保存で確定します。`);
  }

  async function api(url, body) {
    const opts = {
      headers: { 'X-Desk-Request': '1' },
      credentials: 'same-origin',
    };
    if (body !== undefined) {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(url, opts);
    let result = {};
    try { result = await r.json(); } catch (_) { /* keep */ }
    if (!r.ok) {
      if (r.status === 401 && typeof authScreen === 'function') {
        currentUser = null;
        authScreen('login');
      }
      const err = Error(result.error || '処理できませんでした。');
      if (result.conflict) err.conflict = true;
      if (result.sheet) err.sheet = result.sheet;
      throw err;
    }
    return result;
  }

  function resolveLeaveDialog(choice) {
    const dlg = $('#bihinsavedialog');
    if (dlg && dlg.open) dlg.close();
    const fn = leaveDialogResolver;
    leaveDialogResolver = null;
    if (fn) fn(choice);
  }

  function askLeaveAction() {
    return new Promise(resolve => {
      const dlg = $('#bihinsavedialog');
      if (!dlg || typeof dlg.showModal !== 'function') {
        if (confirm('未保存の変更があります。保存して移動しますか？\n（キャンセル後に破棄を選べます）')) {
          resolve('save');
        } else if (confirm('変更を破棄して移動しますか？')) {
          resolve('discard');
        } else {
          resolve('cancel');
        }
        return;
      }
      leaveDialogResolver = resolve;
      if (!dlg.open) dlg.showModal();
    });
  }

  async function confirmLeaveBihin() {
    if (!bihinDirty) return true;
    const action = await askLeaveAction();
    if (action === 'cancel') return false;
    if (action === 'save') {
      try {
        await saveBihin({ force: true });
      } catch (_) { /* keep */ }
      if (bihinDirty) {
        toast('保存できなかったため移動を中止しました。');
        return false;
      }
      return true;
    }
    try {
      await reloadCurrentMonth({ discard: true });
    } catch (e) {
      toast(e.message || '破棄に失敗しました。');
      return false;
    }
    return true;
  }

  async function reloadCurrentMonth(opts) {
    if (!year || !month) return;
    const data = await api(`/api/kintai/bihin?year=${year}&month=${month}`);
    applySheet(data);
    setSaveState(data.exists ? 'saved' : 'idle');
    if (opts && opts.discard) toast('変更を破棄し、最後の保存内容に戻しました。');
  }

  async function loadKintaiBihin(forceYm, opts) {
    if (!canKintai()) throw Error('マーク管理の権限がありません。');
    if (loading) return;
    const skipGuard = opts && opts.skipGuard;
    if (!skipGuard && bihinDirty) {
      const ok = await confirmLeaveBihin();
      if (!ok) {
        const input = $('#bihinyearmonth');
        if (input && year && month) input.value = ymValue(year, month);
        return;
      }
    }
    loading = true;
    try {
      let y = year;
      let m = month;
      if (forceYm) {
        y = forceYm.year;
        m = forceYm.month;
      } else if (!y || !m) {
        const t = todayYm();
        y = t.year;
        m = t.month;
      }
      const input = $('#bihinyearmonth');
      if (input && input.value) {
        const parsed = parseYm(input.value);
        if (parsed && !forceYm) {
          y = parsed.year;
          m = parsed.month;
        }
      }
      year = y;
      month = m;
      if (input) input.value = ymValue(y, m);
      const data = await api(`/api/kintai/bihin?year=${y}&month=${m}`);
      applySheet(data);
      setSaveState(data.exists ? 'saved' : 'idle');
    } finally {
      loading = false;
    }
  }

  function collectPayloadRows() {
    return rows.map((r, i) => ({
      rowKey: r.rowKey,
      itemId: r.itemId,
      category: r.category || '',
      name: r.name || '',
      unit: r.unit || '',
      unitPrice: r.unitPrice,
      sortOrder: i,
      openingQty: r.openingQty,
      purchaseQty: r.purchaseQty,
      closingQty: r.closingQty,
    }));
  }

  async function saveBihin(opts) {
    if (!canKintai()) return;
    const force = opts && opts.force;
    if (saving) return;
    if (!force && !bihinDirty && saveState !== 'failed') return;
    if (!year || !month) return;

    recomputeAll();
    const neg = rows.filter(r => r.negativeUsage);
    if (neg.length) {
      const names = neg.map(r => r.name || '（無題）').join('、');
      setSaveState('failed', '使用数量が負の行があります');
      toast(`使用数量が負になっています（${names}）。月初・購入・月末を確認してください。（入力は画面に残しています）`);
      throw Error('使用数量が負です。');
    }

    saving = true;
    setSaveState('saving');
    const payload = { year, month, version, rows: collectPayloadRows() };
    try {
      const data = await api('/api/kintai/bihin', payload);
      applySheet(data);
      bihinDirty = false;
      setSaveState('saved');
      toast(`${year}年${month}月の備品棚卸を保存しました。`);
    } catch (e) {
      bihinDirty = true;
      setSaveState('failed', e.message || '保存に失敗しました');
      toast((e.message || '保存に失敗しました') + '（入力は画面に残しています）');
      throw e;
    } finally {
      saving = false;
      setSaveState(saveState);
    }
  }

  function bihinBy(delta) {
    let y = year;
    let m = month + delta;
    while (m < 1) { m += 12; y -= 1; }
    while (m > 12) { m -= 12; y += 1; }
    const input = $('#bihinyearmonth');
    if (input) input.value = ymValue(y, m);
    loadKintaiBihin({ year: y, month: m }).catch(e => toast(e.message));
  }

  function onQtyInput(e) {
    const el = e.target;
    if (!el || !el.classList || !el.classList.contains('bihin-qty')) return;
    const i = Number(el.dataset.i);
    const field = el.dataset.field;
    if (!rows[i] || !field) return;
    const parsed = parseQtyInput(el.value);
    if (!parsed.ok) {
      el.setCustomValidity(parsed.error);
      el.reportValidity();
      return;
    }
    el.setCustomValidity('');
    const prev = rows[i][field];
    if (prev === parsed.value || (prev == null && parsed.value == null)) {
      // still allow formatting cleanup without dirty if identical
    } else {
      rows[i][field] = parsed.value;
      markDirty();
    }
    rows[i] = enrichRow(rows[i]);
    // 金額・使用列だけ更新（フォーカス維持のため入力欄は触らない）
    const tr = el.closest('tr');
    if (!tr) return;
    const cells = tr.querySelectorAll('td');
    // 0選択 1科目 2品名 3単位 4単価 5月初数 6月初金 7購入数 8購入金 9月末数 10月末金 11使用数 12使用金
    const r = rows[i];
    if (cells[6]) cells[6].textContent = formatYen(r.openingAmount);
    if (cells[8]) cells[8].textContent = formatYen(r.purchaseAmount);
    if (cells[10]) cells[10].textContent = formatYen(r.closingAmount);
    if (cells[11]) {
      cells[11].textContent = r.usageIncomplete ? '—' : formatNum(r.usageQty);
      cells[11].classList.toggle('bihin-warn', !!r.negativeUsage);
    }
    if (cells[12]) {
      cells[12].textContent = r.usageIncomplete ? '—' : formatYen(r.usageAmount);
      cells[12].classList.toggle('bihin-warn', !!r.negativeUsage);
    }
    const foot = $('#bihingrid tfoot tr');
    if (foot) {
      const t = totalsFromRows();
      const fcells = foot.querySelectorAll('td');
      // colspan5 + empty + openAmt + empty + purchAmt + empty + closeAmt + empty + usageAmt
      if (fcells[2]) fcells[2].textContent = formatYen(t.openingAmount);
      if (fcells[4]) fcells[4].textContent = formatYen(t.purchaseAmount);
      if (fcells[6]) fcells[6].textContent = formatYen(t.closingAmount);
      if (fcells[8]) fcells[8].textContent = formatYen(t.usageAmount);
    }
  }

  function onRowCheckChange(e) {
    const el = e.target;
    if (!el || !el.classList || !el.classList.contains('bihin-row-check')) return;
    if (el.id === 'bihinselectall') {
      if (el.checked) {
        selectedRowKeys = new Set(rows.map((r, i) => rowKeyOf(r, i)));
      } else {
        selectedRowKeys = new Set();
      }
      paintBihin();
      return;
    }
    const key = el.dataset.rowKey;
    if (!key) return;
    if (el.checked) selectedRowKeys.add(key);
    else selectedRowKeys.delete(key);
    syncHeaderCheck();
    updateRowDeleteButton();
  }

  function onBeforeUnload(e) {
    if (!bihinDirty) return;
    e.preventDefault();
    e.returnValue = '';
  }

  /* ---- マスタ ---- */
  function openMasterDialog() {
    const dlg = $('#bihinmasterdialog');
    if (!dlg || typeof dlg.showModal !== 'function') return;
    clearMasterForm();
    loadMasterList().then(() => {
      if (!dlg.open) dlg.showModal();
    }).catch(e => toast(e.message));
  }

  function closeMasterDialog() {
    const dlg = $('#bihinmasterdialog');
    if (dlg && dlg.open) dlg.close();
  }

  function clearMasterForm() {
    const id = $('#bihinmasterid');
    if (id) id.value = '';
    ['bihinmastercategory', 'bihinmastername', 'bihinmasterunit', 'bihinmasterprice'].forEach(k => {
      const el = $('#' + k);
      if (el) el.value = '';
    });
    const err = $('#bihinmastererror');
    if (err) err.textContent = '';
  }

  async function loadMasterList() {
    const data = await api('/api/kintai/bihin/items');
    masterItems = (data && data.items) || [];
    paintMasterList();
  }

  function paintMasterList() {
    const body = $('#bihinmasterbody');
    const deletedBody = $('#bihinmasterdeletedbody');
    if (!body) return;
    const active = masterItems.filter(it => it.active !== false);
    const deleted = masterItems.filter(it => it.active === false);
    if (!active.length) {
      body.innerHTML = '<tr><td colspan="5">まだ品目がありません。上の欄から登録してください。</td></tr>';
    } else {
      body.innerHTML = active.map(it => `<tr>
        <td>${esc(it.category)}</td>
        <td>${esc(it.name)}</td>
        <td>${esc(it.unit)}</td>
        <td class="bihin-num">${formatYen(it.unitPrice)}</td>
        <td class="bihin-master-row-actions">
          <button type="button" data-edit-item="${it.id}">編集</button>
          <button type="button" class="danger" data-delete-item="${it.id}">削除</button>
        </td>
      </tr>`).join('');
    }
    if (deletedBody) {
      if (!deleted.length) {
        deletedBody.innerHTML = '<tr><td colspan="5">削除済みはありません。</td></tr>';
      } else {
        deletedBody.innerHTML = deleted.map(it => `<tr class="bihin-master-deleted-row">
          <td>${esc(it.category)}</td>
          <td>${esc(it.name)}</td>
          <td>${esc(it.unit)}</td>
          <td class="bihin-num">${formatYen(it.unitPrice)}</td>
          <td><button type="button" data-restore-item="${it.id}">復元</button></td>
        </tr>`).join('');
      }
    }
  }

  async function softDeleteMasterItem(id) {
    const it = masterItems.find(x => x.id === id);
    if (!it) {
      toast('品目が見つかりません。');
      return;
    }
    const name = it.name || '（無題）';
    const ok = confirm(
      `「${name}」を削除しますか？\n\n今後の棚卸・発注の候補から外します。保存済みの棚卸・発注履歴は残ります。`
    );
    if (!ok) return;
    const err = $('#bihinmastererror');
    if (err) err.textContent = '';
    try {
      await api('/api/kintai/bihin/items/delete', { id });
      toast(`「${name}」を削除しました（復元できます）。`);
      const editing = $('#bihinmasterid');
      if (editing && Number(editing.value) === id) clearMasterForm();
      await loadMasterList();
      // 未保存月を表示中なら候補から外すため再読込（dirty なら確認のみ）
      if (sheet && !sheet.exists) {
        if (!bihinDirty) {
          await loadKintaiBihin({ year, month }, { skipGuard: true });
        } else {
          toast('表示中の未保存の変更があるため、マスタ反映の再読込はしていません。保存または破棄後に再表示してください。');
        }
      }
    } catch (e) {
      if (err) err.textContent = e.message || '削除に失敗しました。';
      throw e;
    }
  }

  async function restoreMasterItem(id) {
    const it = masterItems.find(x => x.id === id);
    if (!it) {
      toast('品目が見つかりません。');
      return;
    }
    const name = it.name || '（無題）';
    const err = $('#bihinmastererror');
    if (err) err.textContent = '';
    try {
      await api('/api/kintai/bihin/items/restore', { id });
      toast(`「${name}」を復元しました（過去の棚卸は書き換えません）。`);
      await loadMasterList();
      if (sheet && !sheet.exists) {
        if (!bihinDirty) {
          await loadKintaiBihin({ year, month }, { skipGuard: true });
        } else {
          toast('表示中の未保存の変更があるため、マスタ反映の再読込はしていません。保存または破棄後に再表示してください。');
        }
      }
    } catch (e) {
      if (err) err.textContent = e.message || '復元に失敗しました。';
      throw e;
    }
  }

  function fillMasterForm(it) {
    $('#bihinmasterid').value = it.id;
    $('#bihinmastercategory').value = it.category || '';
    $('#bihinmastername').value = it.name || '';
    $('#bihinmasterunit').value = it.unit || '';
    $('#bihinmasterprice').value = it.unitPrice == null ? '' : it.unitPrice;
    const err = $('#bihinmastererror');
    if (err) err.textContent = '';
  }

  async function saveMasterItem() {
    const err = $('#bihinmastererror');
    if (err) err.textContent = '';
    const idRaw = ($('#bihinmasterid') && $('#bihinmasterid').value) || '';
    const name = ($('#bihinmastername') && $('#bihinmastername').value || '').trim();
    const priceRaw = $('#bihinmasterprice') && $('#bihinmasterprice').value;
    if (!name) {
      if (err) err.textContent = '品名を入力してください。';
      return;
    }
    const price = Number(String(priceRaw || '').replace(/,/g, ''));
    if (!Number.isFinite(price) || price < 0) {
      if (err) err.textContent = '単価を確認してください（負は不可）。';
      return;
    }
    const payload = {
      category: ($('#bihinmastercategory') && $('#bihinmastercategory').value || '').trim(),
      name,
      unit: ($('#bihinmasterunit') && $('#bihinmasterunit').value || '').trim(),
      unitPrice: price,
      active: true,
    };
    if (idRaw) payload.id = Number(idRaw);
    try {
      await api('/api/kintai/bihin/items', payload);
      toast('品目マスタを保存しました（保存済みの月は書き換えません）。');
      clearMasterForm();
      await loadMasterList();
      // 未保存月を表示中ならマスタ反映のため再読込（dirty なら確認）
      if (sheet && !sheet.exists) {
        if (!bihinDirty) {
          await loadKintaiBihin({ year, month }, { skipGuard: true });
        } else {
          toast('表示中の未保存の変更があるため、マスタ反映の再読込はしていません。保存または破棄後に再表示してください。');
        }
      }
    } catch (e) {
      if (err) err.textContent = e.message || '保存に失敗しました。';
      throw e;
    }
  }

  /* ---- 棚卸記入用紙（印刷専用。報告UI・保存データは変更しない） ---- */

  /**
   * HIQラベル（カラー）のみ。括弧の全角/半角・空白ゆれを吸収。
   * HIQラベル（白）／（透明）など他色は対象外。
   */
  function isHiqColorLabel(name) {
    const n = String(name == null ? '' : name)
      .normalize('NFKC')
      .replace(/[\s\u3000]+/g, '');
    return n === 'HIQラベル(カラー)';
  }

  function formatPrintPrice(unitPrice) {
    const p = Number(unitPrice);
    if (!Number.isFinite(p)) return '単価 —';
    return `単価 ${p.toLocaleString('ja-JP')}円`;
  }

  /** 表示中スナップショットから印刷行を組み立て。数量・金額は載せない。 */
  function expandCountSheetRows(sourceRows) {
    const out = [];
    (sourceRows || []).forEach((r, idx) => {
      const name = r && r.name != null ? String(r.name) : '';
      const unit = r && r.unit != null ? String(r.unit) : '';
      const unitPrice = r && r.unitPrice != null ? r.unitPrice : '';
      const sortOrder = r && r.sortOrder != null ? r.sortOrder : idx;
      if (isHiqColorLabel(name)) {
        out.push({
          kind: 'hiq',
          name,
          unit,
          unitPrice,
          sortOrder,
          colors: ['赤', '緑', '青', '合計'],
        });
      } else {
        out.push({
          kind: 'single',
          name,
          unit,
          unitPrice,
          sortOrder,
          color: '—',
        });
      }
    });
    return out;
  }

  function countSheetTitleYm(y, m) {
    return `${y}年${m}月分`;
  }

  function buildCountSheetHtml(sourceRows, y, m) {
    const blocks = expandCountSheetRows(sourceRows);
    const ymText = countSheetTitleYm(y, m);
    // 見本順: タイトル／年月 → 棚卸日・記入者 → 説明2行 → 列見出し。
    // thead に置きページ送りで年月＋見出しが繰り返される。
    const head = `<thead>
      <tr class="bihin-count-banner">
        <th colspan="5">
          <div class="bihin-count-head">
            <span class="bihin-count-title">備品棚卸 記入用紙</span>
            <span class="bihin-count-ym">${esc(ymText)}</span>
          </div>
        </th>
      </tr>
      <tr class="bihin-count-meta">
        <th colspan="5">
          <div class="bihin-count-meta-row">
            <span class="bihin-count-date">棚卸日：　　　年　　月　　日</span>
            <span class="bihin-count-writer">記入者：<span class="bihin-count-writer-line" aria-hidden="true"></span></span>
          </div>
        </th>
      </tr>
      <tr class="bihin-count-instructions">
        <th colspan="5">
          <p>実際に数えた月末在庫を、数量欄へ記入してください。</p>
          <p>HIQラベル（カラー）は赤・緑・青を数え、単価ごとに合計してください。</p>
        </th>
      </tr>
      <tr class="bihin-count-cols">
        <th class="bihin-count-col-name">品名・単価</th>
        <th class="bihin-count-col-color">色</th>
        <th class="bihin-count-col-unit">単位</th>
        <th class="bihin-count-col-qty">棚卸数量（手書き）</th>
        <th class="bihin-count-col-note">備考</th>
      </tr>
    </thead>`;

    const bodies = blocks.map(block => {
      if (block.kind === 'hiq') {
        const nameCell = `<td class="bihin-count-name" rowspan="4">
          <div class="bihin-count-name-text">${esc(block.name)}</div>
          <div class="bihin-count-price">${esc(formatPrintPrice(block.unitPrice))}</div>
        </td>`;
        const rowsHtml = block.colors.map((color, i) => {
          const isTotal = color === '合計';
          const trClass = isTotal ? ' class="bihin-count-hiq-total"' : '';
          const note = isTotal ? 'この合計を報告画面へ入力' : '';
          const nameTd = i === 0 ? nameCell : '';
          return `<tr${trClass}>${nameTd}
            <td class="bihin-count-color${isTotal ? ' bihin-count-total-label' : ''}">${esc(color)}</td>
            <td class="bihin-count-unit">${esc(block.unit || '')}</td>
            <td class="bihin-count-qty"></td>
            <td class="bihin-count-note">${esc(note)}</td>
          </tr>`;
        }).join('');
        return `<tbody class="bihin-count-block bihin-count-hiq">${rowsHtml}</tbody>`;
      }
      return `<tbody class="bihin-count-block"><tr>
        <td class="bihin-count-name">
          <div class="bihin-count-name-text">${esc(block.name)}</div>
          <div class="bihin-count-price">${esc(formatPrintPrice(block.unitPrice))}</div>
        </td>
        <td class="bihin-count-color">${esc(block.color)}</td>
        <td class="bihin-count-unit">${esc(block.unit || '')}</td>
        <td class="bihin-count-qty"></td>
        <td class="bihin-count-note"></td>
      </tr></tbody>`;
    }).join('');

    // メモ・脚注は table 外（tfoot だと毎ページ繰り返されるため）
    const after = `<div class="bihin-count-after">
      <div class="bihin-count-memo">
        <div class="bihin-count-memo-label">連絡事項・メモ</div>
        <div class="bihin-count-memo-box"></div>
      </div>
      <div class="bihin-count-footnotes">
        <p>※ 単価は同名品を区別するための表示です。数量・金額の実績は印字しません。</p>
        <p>※ 報告画面ではHIQラベルを色別に分けず、各単価の合計数量を入力します。</p>
      </div>
    </div>`;

    return `<table class="bihin-count-table" aria-label="備品棚卸記入用紙">
      <colgroup>
        <col class="bihin-count-col-name">
        <col class="bihin-count-col-color">
        <col class="bihin-count-col-unit">
        <col class="bihin-count-col-qty">
        <col class="bihin-count-col-note">
      </colgroup>
      ${head}${bodies}
    </table>${after}`;
  }

  function paintCountSheet() {
    const el = $('#bihincountsheet');
    if (!el) return;
    el.innerHTML = buildCountSheetHtml(rows, year, month);
  }

  function closeBihinPrintDialog() {
    const dlg = $('#bihinprintdialog');
    if (dlg && typeof dlg.close === 'function') dlg.close();
  }

  function openBihinPrintDialog() {
    const dlg = $('#bihinprintdialog');
    if (!dlg || typeof dlg.showModal !== 'function') {
      runBihinCountPrint();
      return;
    }
    dlg.showModal();
  }

  /**
   * Chromium は unnamed @page の向きを採用しやすい。既存の A4 縦を明示し、
   * シフト印刷の横向き一時CSSが残っていても縦に戻す。印刷後に外す。
   */
  const BIHIN_PRINT_PAGE_CSS_ID = 'bihin-count-print-page-css';
  const BIHIN_PRINT_PAGE_CSS = '@page{size:A4 portrait;margin:12mm}';

  function ensureBihinPortraitPage() {
    let el = document.getElementById(BIHIN_PRINT_PAGE_CSS_ID);
    if (!el) {
      el = document.createElement('style');
      el.id = BIHIN_PRINT_PAGE_CSS_ID;
      document.head.appendChild(el);
    }
    el.textContent = BIHIN_PRINT_PAGE_CSS;
  }

  function clearBihinPortraitPage() {
    const el = document.getElementById(BIHIN_PRINT_PAGE_CSS_ID);
    if (el) el.remove();
  }

  function runBihinCountPrint() {
    // 印刷は表示中の live スナップショットのみ。保存データは書き換えない。save も呼ばない。
    paintCountSheet();
    document.body.classList.add('printing-bihin-count');
    ensureBihinPortraitPage();
    let cleaned = false;
    const done = () => {
      if (cleaned) return;
      cleaned = true;
      clearBihinPortraitPage();
      document.body.classList.remove('printing-bihin-count');
      window.removeEventListener('afterprint', done);
      if (mql) {
        if (mql.removeEventListener) mql.removeEventListener('change', onPrintChange);
        else if (mql.removeListener) mql.removeListener(onPrintChange);
      }
    };
    const onPrintChange = (e) => {
      if (!e.matches) done();
    };
    const mql = window.matchMedia && window.matchMedia('print');
    window.addEventListener('afterprint', done);
    if (mql) {
      if (mql.addEventListener) mql.addEventListener('change', onPrintChange);
      else if (mql.addListener) mql.addListener(onPrintChange);
    }
    setTimeout(() => window.print(), 50);
  }

  function printBihinCountSheet() {
    openBihinPrintDialog();
  }

  /* ---- 発注（注文・設定・履歴） ---- */
  function newPrepKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'prep-' + Date.now().toString(16) + '-' + Math.random().toString(16).slice(2);
  }

  function vendorNameById(id) {
    const v = orderVendors.find(x => Number(x.id) === Number(id));
    return v ? (v.name || '') : '';
  }

  async function loadOrderSettingsBundle() {
    const data = await api('/api/kintai/bihin/order/settings');
    orderVendors = (data && data.vendors) || [];
    masterItems = (data && data.items) || masterItems;
    const note = $('#bihinordertemplatenote');
    if (note) {
      const src = data && data.templateSource;
      const path = data && data.templatePath;
      if (path) note.textContent = `注文書テンプレート: ${path}（${src || '—'}）。原本は上書きしません。`;
      else note.textContent = String(src || '注文書テンプレートが見つかりません。');
    }
    return data;
  }

  /** 発注単位が数字だけか（半角・全角）。要確認フラグ用。自動書き換えしない。 */
  function isDigitsOnlyOrderUnit(unit) {
    const s = String(unit || '').trim();
    return s !== '' && /^[0-9０-９]+$/.test(s);
  }

  /** 表示用単位。空／数字のみは出さない（保存値は書き換えない）。 */
  function displayOrderUnit(unit) {
    const u = String(unit || '').trim();
    if (!u || isDigitsOnlyOrderUnit(u)) return '';
    return u;
  }

  /** 数量と単位を空白区切り。単位が空／数字のみなら数量のみ（「1 1」「11」にしない）。 */
  function formatOrderQtyUnit(qty, unit) {
    const q = qty == null || qty === '' ? '' : String(qty);
    const u = displayOrderUnit(unit);
    if (!u) return q;
    if (!q) return u;
    return `${q} ${u}`;
  }

  function paintOrderForm() {
    const body = $('#bihinorderbody');
    if (!body) return;
    const items = (masterItems || []).filter(it => it.active !== false);
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="6">品目がありません。先に品目マスタと発注設定を登録してください。</td></tr>';
      return;
    }
    body.innerHTML = items.map(it => {
      const ready = it.vendorId && it.orderUnit && it.packingQty != null && it.packingQty !== '';
      const hint = ready ? '' : '（発注設定不足）';
      const unitNeedsReview = it.orderUnitNeedsReview || isDigitsOnlyOrderUnit(it.orderUnit);
      const unitReview = unitNeedsReview
        ? ' <span class="kintaimeta bihin-unit-review" title="箱・巻・個などの単位を入力">要確認</span>'
        : '';
      return `<tr data-item-id="${it.id}">
        <td>${esc(it.name)}${hint ? ` <span class="kintaimeta">${hint}</span>` : ''}</td>
        <td>${esc(it.orderProductName || it.name || '')}</td>
        <td>${esc(vendorNameById(it.vendorId) || '—')}</td>
        <td class="${unitNeedsReview ? 'bihin-warn' : ''}">${esc(it.orderUnit || '—')}${unitReview}</td>
        <td class="bihin-num">${it.packingQty == null || it.packingQty === '' ? '—' : esc(it.packingQty)}</td>
        <td><input class="bihin-order-qty" data-item-id="${it.id}" inputmode="decimal" value="" aria-label="${esc(it.name)}の発注数量"></td>
      </tr>`;
    }).join('');
  }

  async function openOrderDialog() {
    const dlg = $('#bihinorderdialog');
    if (!dlg || typeof dlg.showModal !== 'function') return;
    const err = $('#bihinordererror');
    if (err) err.textContent = '';
    orderPrepKey = newPrepKey();
    const dateEl = $('#bihinorderdate');
    if (dateEl && !dateEl.value) {
      const t = new Date();
      dateEl.value = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
    }
    try {
      await loadOrderSettingsBundle();
      paintOrderForm();
      if (!dlg.open) dlg.showModal();
    } catch (e) {
      toast(e.message || '発注画面を開けませんでした。');
    }
  }

  function closeOrderDialog() {
    const dlg = $('#bihinorderdialog');
    if (dlg && dlg.open) dlg.close();
  }

  async function submitOrder() {
    const err = $('#bihinordererror');
    if (err) err.textContent = '';
    if (orderBusy) return;
    const lines = [];
    const inputs = document.querySelectorAll('#bihinorderbody .bihin-order-qty');
    for (const inp of inputs) {
      const parsed = parseQtyInput(inp.value);
      if (!parsed.ok) {
        inp.setCustomValidity(parsed.error);
        inp.reportValidity();
        if (err) err.textContent = parsed.error;
        return;
      }
      inp.setCustomValidity('');
      if (parsed.value == null || parsed.value === 0) continue;
      if (parsed.value < 0) {
        if (err) err.textContent = '負の数量は使えません。';
        return;
      }
      lines.push({ itemId: Number(inp.dataset.itemId), orderQty: parsed.value });
    }
    if (!lines.length) {
      if (err) err.textContent = '発注数量を1つ以上入力してください。';
      return;
    }
    if (!confirm('仕入先ごとにメール作成画面を開きます。送信はご自身で行ってください（自動送信しません）。よろしいですか？')) return;
    orderBusy = true;
    const go = $('#bihinordergo');
    if (go) go.disabled = true;
    try {
      if (!orderPrepKey) orderPrepKey = newPrepKey();
      const payload = {
        prepKey: orderPrepKey,
        orderDate: ($('#bihinorderdate') && $('#bihinorderdate').value) || '',
        lines,
      };
      const result = await api('/api/kintai/bihin/order/prepare', payload);
      toast((result && result.message) || '発注準備が完了しました。');
      closeOrderDialog();
      // 発注日の月が表示中なら再読込（購入加算を反映）。表示中が別月なら触らない。
      const od = (result && result.year && result.month) ? { year: result.year, month: result.month } : null;
      if (od && od.year === year && od.month === month && !bihinDirty) {
        await reloadCurrentMonth({});
      }
    } catch (e) {
      if (err) err.textContent = e.message || '発注準備に失敗しました。当月購入は加算していません。';
      // 失敗時は同じ prepKey のまま再試行可（サーバ側で未作成なら再実行）
      throw e;
    } finally {
      orderBusy = false;
      if (go) go.disabled = false;
    }
  }

  function paintVendorList() {
    const body = $('#bihinvendorbodylist');
    if (!body) return;
    const rows = orderVendors.filter(v => v.active !== false);
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="4">仕入先がありません。上の欄から登録してください。</td></tr>';
      return;
    }
    body.innerHTML = rows.map(v => `<tr>
      <td>${esc(v.name)}</td>
      <td>${esc(v.email)}</td>
      <td>${esc(v.modeLabel || v.mode)}</td>
      <td><button type="button" data-edit-vendor="${v.id}">編集</button></td>
    </tr>`).join('');
  }

  const ORDER_CONTENT_PLACEHOLDER = '{注文内容}';

  function clearVendorForm() {
    const id = $('#bihinvendorid');
    if (id) id.value = '';
    ['bihinvendorname', 'bihinvendoremail', 'bihinvendorsubject', 'bihinvendorbody'].forEach(k => {
      const el = $('#' + k);
      if (el) el.value = '';
    });
    const mode = $('#bihinvendormode');
    if (mode) mode.value = 'excel';
  }

  function fillVendorForm(v) {
    $('#bihinvendorid').value = v.id;
    $('#bihinvendorname').value = v.name || '';
    $('#bihinvendoremail').value = v.email || '';
    $('#bihinvendormode').value = v.mode === 'body' ? 'body' : 'excel';
    $('#bihinvendorsubject').value = v.subjectTemplate || '';
    // 既存テンプレは上書きしない（空のまま読込）
    $('#bihinvendorbody').value = v.bodyTemplate || '';
  }

  function insertOrderContentPlaceholder() {
    const ta = $('#bihinvendorbody');
    if (!ta) return;
    const ph = ORDER_CONTENT_PLACEHOLDER;
    const start = typeof ta.selectionStart === 'number' ? ta.selectionStart : ta.value.length;
    const end = typeof ta.selectionEnd === 'number' ? ta.selectionEnd : start;
    const before = ta.value.slice(0, start);
    const after = ta.value.slice(end);
    ta.value = before + ph + after;
    const caret = start + ph.length;
    ta.focus();
    if (typeof ta.setSelectionRange === 'function') ta.setSelectionRange(caret, caret);
  }

  function paintItemOrderSelects() {
    const sel = $('#bihinitemorderselect');
    const vend = $('#bihinitemordervendor');
    if (vend) {
      vend.innerHTML = '<option value="">（未設定）</option>' + orderVendors
        .filter(v => v.active !== false)
        .map(v => `<option value="${v.id}">${esc(v.name)}</option>`).join('');
    }
    if (sel) {
      const items = (masterItems || []).filter(it => it.active !== false);
      sel.innerHTML = '<option value="">選んでください</option>' + items
        .map(it => `<option value="${it.id}">${esc(it.category ? it.category + ' / ' : '')}${esc(it.name)}</option>`).join('');
    }
  }

  function updateItemOrderUnitHint(unitValue) {
    const hint = $('#bihinitemorderunithint');
    if (!hint) return;
    const bad = isDigitsOnlyOrderUnit(unitValue);
    hint.hidden = !bad;
  }

  function fillItemOrderForm(it) {
    if (!it) return;
    const sel = $('#bihinitemorderselect');
    if (sel) sel.value = String(it.id);
    const vend = $('#bihinitemordervendor');
    if (vend) vend.value = it.vendorId != null ? String(it.vendorId) : '';
    const name = $('#bihinitemordername');
    if (name) name.value = it.orderProductName || '';
    const unit = $('#bihinitemorderunit');
    if (unit) unit.value = it.orderUnit || '';
    updateItemOrderUnitHint(it.orderUnit || '');
    const pack = $('#bihinitemorderpacking');
    if (pack) pack.value = it.packingQty == null ? '' : it.packingQty;
    const spec = $('#bihinitemorderspec');
    if (spec) spec.value = it.orderSpec || '';
    const qtyCell = $('#bihinitemorderqtycell');
    if (qtyCell) qtyCell.value = it.orderQtyCell || '';
  }

  async function openOrderSettingsDialog() {
    const dlg = $('#bihinordersettingsdialog');
    if (!dlg || typeof dlg.showModal !== 'function') return;
    const err = $('#bihinordersettingserror');
    if (err) err.textContent = '';
    try {
      await loadOrderSettingsBundle();
      paintVendorList();
      paintItemOrderSelects();
      clearVendorForm();
      if (!dlg.open) dlg.showModal();
    } catch (e) {
      toast(e.message || '発注設定を開けませんでした。');
    }
  }

  function closeOrderSettingsDialog() {
    const dlg = $('#bihinordersettingsdialog');
    if (dlg && dlg.open) dlg.close();
  }

  async function saveVendorFromForm() {
    const err = $('#bihinordersettingserror');
    if (err) err.textContent = '';
    const name = ($('#bihinvendorname') && $('#bihinvendorname').value || '').trim();
    if (!name) {
      if (err) err.textContent = '仕入先名を入力してください。';
      return;
    }
    const payload = {
      name,
      email: ($('#bihinvendoremail') && $('#bihinvendoremail').value || '').trim(),
      mode: ($('#bihinvendormode') && $('#bihinvendormode').value) || 'excel',
      subjectTemplate: ($('#bihinvendorsubject') && $('#bihinvendorsubject').value) || '',
      bodyTemplate: ($('#bihinvendorbody') && $('#bihinvendorbody').value) || '',
      active: true,
    };
    const idRaw = ($('#bihinvendorid') && $('#bihinvendorid').value) || '';
    if (idRaw) payload.id = Number(idRaw);
    await api('/api/kintai/bihin/vendors', payload);
    toast('仕入先を保存しました。');
    clearVendorForm();
    await loadOrderSettingsBundle();
    paintVendorList();
    paintItemOrderSelects();
  }

  async function saveItemOrderFromForm() {
    const err = $('#bihinordersettingserror');
    if (err) err.textContent = '';
    const id = Number($('#bihinitemorderselect') && $('#bihinitemorderselect').value);
    if (!id) {
      if (err) err.textContent = '品目を選んでください。';
      return;
    }
    const it = masterItems.find(x => x.id === id);
    if (!it) {
      if (err) err.textContent = '品目が見つかりません。';
      return;
    }
    const vendRaw = ($('#bihinitemordervendor') && $('#bihinitemordervendor').value) || '';
    const packingRaw = ($('#bihinitemorderpacking') && $('#bihinitemorderpacking').value) || '';
    const payload = {
      id,
      category: it.category || '',
      name: it.name || '',
      unit: it.unit || '',
      unitPrice: it.unitPrice,
      active: true,
      vendorId: vendRaw === '' ? null : Number(vendRaw),
      orderProductName: ($('#bihinitemordername') && $('#bihinitemordername').value || '').trim(),
      orderUnit: ($('#bihinitemorderunit') && $('#bihinitemorderunit').value || '').trim(),
      orderSpec: ($('#bihinitemorderspec') && $('#bihinitemorderspec').value || '').trim(),
      orderQtyCell: ($('#bihinitemorderqtycell') && $('#bihinitemorderqtycell').value || '').trim().toUpperCase(),
      packingQty: packingRaw === '' ? '' : Number(packingRaw),
    };
    if (isDigitsOnlyOrderUnit(payload.orderUnit)) {
      toast('発注単位が数字だけです。箱・巻・個などの単位を入力してください（自動では書き換えません）。');
    }
    await api('/api/kintai/bihin/items', payload);
    toast('品目の発注設定を保存しました（保存済み月の数量は書き換えません）。');
    await loadOrderSettingsBundle();
    paintItemOrderSelects();
    const again = masterItems.find(x => x.id === id);
    if (again) fillItemOrderForm(again);
  }

  async function openOrderHistoryDialog() {
    const dlg = $('#bihinorderhistorydialog');
    if (!dlg || typeof dlg.showModal !== 'function') return;
    const err = $('#bihinorderhistoryerror');
    if (err) err.textContent = '';
    try {
      const data = await api('/api/kintai/bihin/orders');
      const body = $('#bihinorderhistorybody');
      const hist = (data && data.history) || [];
      if (!body) return;
      if (!hist.length) {
        body.innerHTML = '<tr><td colspan="8">発注履歴はまだありません。</td></tr>';
      } else {
        body.innerHTML = hist.map(h => {
          const qty = formatOrderQtyUnit(h.orderQty, h.orderUnit);
          const unitBad = isDigitsOnlyOrderUnit(h.orderUnit);
          const ym = `${h.year}年${h.month}月`;
          const cancelled = h.cancelled ? '取消済み' : '—';
          const btn = h.cancelled
            ? ''
            : `<button type="button" data-cancel-order="${h.orderId}">取消</button>`;
          return `<tr>
            <td>${esc(h.datetime || '')}</td>
            <td>${esc(h.vendor || '')}</td>
            <td>${esc(h.item || '')}</td>
            <td class="bihin-num${unitBad ? ' bihin-warn' : ''}">${esc(qty)}${unitBad ? ' <span class="kintaimeta">要確認</span>' : ''}</td>
            <td>${esc(ym)}</td>
            <td>${esc(h.modeLabel || h.mode || '')}</td>
            <td>${esc(cancelled)}</td>
            <td>${btn}</td>
          </tr>`;
        }).join('');
      }
      if (!dlg.open) dlg.showModal();
    } catch (e) {
      toast(e.message || '発注履歴を開けませんでした。');
    }
  }

  function closeOrderHistoryDialog() {
    const dlg = $('#bihinorderhistorydialog');
    if (dlg && dlg.open) dlg.close();
  }

  async function cancelOrderById(id) {
    if (!confirm('この発注を取消しますか？この発注分の当月購入加算のみ戻します。')) return;
    const result = await api('/api/kintai/bihin/order/cancel', { id: Number(id) });
    toast((result && result.message) || '取消しました。');
    await openOrderHistoryDialog();
    if (!bihinDirty) await reloadCurrentMonth({});
  }

  function bindBihinControls() {
    if (!$('#kintaibihin')) return;
    const ym = $('#bihinyearmonth');
    if (ym) {
      const t = todayYm();
      if (!ym.value) ym.value = ymValue(t.year, t.month);
      ym.onchange = () => {
        const parsed = parseYm(ym.value);
        if (!parsed) return;
        loadKintaiBihin(parsed).catch(e => toast(e.message));
      };
    }
    const prev = $('#bihinprev');
    const next = $('#bihinnext');
    const cur = $('#bihinthismonth');
    if (prev) prev.onclick = () => bihinBy(-1);
    if (next) next.onclick = () => bihinBy(1);
    if (cur) cur.onclick = () => {
      const t = todayYm();
      const input = $('#bihinyearmonth');
      if (input) input.value = ymValue(t.year, t.month);
      loadKintaiBihin(t).catch(e => toast(e.message));
    };
    const saveBtn = $('#bihinsave');
    if (saveBtn) {
      saveBtn.onclick = () => { saveBihin({ force: true }).catch(() => {}); };
    }
    const rowDelBtn = $('#bihinrowdelete');
    if (rowDelBtn) rowDelBtn.onclick = () => deleteSelectedRows();
    const printBtn = $('#bihinprint');
    if (printBtn) printBtn.onclick = () => printBihinCountSheet();
    const printGo = $('#bihinprintdialog-go');
    if (printGo) {
      printGo.onclick = () => {
        closeBihinPrintDialog();
        runBihinCountPrint();
      };
    }
    const printCancel = $('#bihinprintdialog-cancel');
    if (printCancel) printCancel.onclick = () => closeBihinPrintDialog();
    const printClose = $('#bihinprintdialog-close');
    if (printClose) printClose.onclick = () => closeBihinPrintDialog();
    const printDlg = $('#bihinprintdialog');
    if (printDlg) {
      printDlg.addEventListener('cancel', e => {
        e.preventDefault();
        closeBihinPrintDialog();
      });
    }
    const masterBtn = $('#bihinmaster');
    if (masterBtn) masterBtn.onclick = () => openMasterDialog();
    const orderBtn = $('#bihinorder');
    if (orderBtn) orderBtn.onclick = () => { openOrderDialog().catch(e => toast(e.message)); };
    const orderGo = $('#bihinordergo');
    if (orderGo) orderGo.onclick = () => { submitOrder().catch(() => {}); };
    const orderCancel = $('#bihinordercancel');
    if (orderCancel) orderCancel.onclick = () => closeOrderDialog();
    const orderClose = $('#bihinorderdialog-close');
    if (orderClose) orderClose.onclick = () => closeOrderDialog();
    const settingsBtn = $('#bihinordersettings');
    if (settingsBtn) settingsBtn.onclick = () => { openOrderSettingsDialog().catch(e => toast(e.message)); };
    const settingsClose = $('#bihinordersettingsdialog-close');
    if (settingsClose) settingsClose.onclick = () => closeOrderSettingsDialog();
    const vInsert = $('#bihinvendorinsertorder');
    if (vInsert) vInsert.onclick = () => insertOrderContentPlaceholder();
    const vSave = $('#bihinvendorsave');
    if (vSave) vSave.onclick = () => { saveVendorFromForm().catch(e => {
      const err = $('#bihinordersettingserror');
      if (err) err.textContent = e.message || '保存に失敗しました。';
    }); };
    const vNew = $('#bihinvendornew');
    if (vNew) vNew.onclick = () => clearVendorForm();
    const vList = $('#bihinvendorbodylist');
    if (vList) {
      vList.addEventListener('click', e => {
        const btn = e.target.closest && e.target.closest('[data-edit-vendor]');
        if (!btn) return;
        const id = Number(btn.dataset.editVendor);
        const v = orderVendors.find(x => x.id === id);
        if (v) fillVendorForm(v);
      });
    }
    const itemSel = $('#bihinitemorderselect');
    if (itemSel) {
      itemSel.onchange = () => {
        const id = Number(itemSel.value);
        const it = masterItems.find(x => x.id === id);
        if (it) fillItemOrderForm(it);
        else updateItemOrderUnitHint('');
      };
    }
    const itemUnit = $('#bihinitemorderunit');
    if (itemUnit) {
      itemUnit.addEventListener('input', () => updateItemOrderUnitHint(itemUnit.value));
      itemUnit.addEventListener('change', () => updateItemOrderUnitHint(itemUnit.value));
    }
    const itemSave = $('#bihinitemordersave');
    if (itemSave) itemSave.onclick = () => { saveItemOrderFromForm().catch(e => {
      const err = $('#bihinordersettingserror');
      if (err) err.textContent = e.message || '保存に失敗しました。';
    }); };
    const histBtn = $('#bihinorderhistory');
    if (histBtn) histBtn.onclick = () => { openOrderHistoryDialog().catch(e => toast(e.message)); };
    const histClose = $('#bihinorderhistorydialog-close');
    if (histClose) histClose.onclick = () => closeOrderHistoryDialog();
    const histBody = $('#bihinorderhistorybody');
    if (histBody) {
      histBody.addEventListener('click', e => {
        const btn = e.target.closest && e.target.closest('[data-cancel-order]');
        if (!btn) return;
        cancelOrderById(btn.dataset.cancelOrder).catch(err => toast(err.message));
      });
    }

    const wrap = $('#bihingridwrap');
    if (wrap) {
      wrap.addEventListener('input', onQtyInput);
      wrap.addEventListener('change', e => {
        if (e.target && e.target.classList && e.target.classList.contains('bihin-row-check')) {
          onRowCheckChange(e);
          return;
        }
        onQtyInput(e);
      });
    }

    const dlgSave = $('#bihinsavedialog-save');
    const dlgDiscard = $('#bihinsavedialog-discard');
    const dlgCancel = $('#bihinsavedialog-cancel');
    if (dlgSave) dlgSave.onclick = () => resolveLeaveDialog('save');
    if (dlgDiscard) dlgDiscard.onclick = () => resolveLeaveDialog('discard');
    if (dlgCancel) dlgCancel.onclick = () => resolveLeaveDialog('cancel');
    const dlg = $('#bihinsavedialog');
    if (dlg) {
      dlg.addEventListener('cancel', e => {
        e.preventDefault();
        resolveLeaveDialog('cancel');
      });
    }

    const mClose = $('#bihinmasterdialog-close');
    if (mClose) mClose.onclick = () => closeMasterDialog();
    const mNew = $('#bihinmasternew');
    if (mNew) mNew.onclick = () => clearMasterForm();
    const mSave = $('#bihinmastersave');
    if (mSave) mSave.onclick = () => { saveMasterItem().catch(() => {}); };
    const mBody = $('#bihinmasterbody');
    if (mBody) {
      mBody.addEventListener('click', e => {
        const editBtn = e.target.closest && e.target.closest('[data-edit-item]');
        if (editBtn) {
          const id = Number(editBtn.dataset.editItem);
          const it = masterItems.find(x => x.id === id);
          if (it) fillMasterForm(it);
          return;
        }
        const delBtn = e.target.closest && e.target.closest('[data-delete-item]');
        if (delBtn) {
          softDeleteMasterItem(Number(delBtn.dataset.deleteItem)).catch(() => {});
        }
      });
    }
    const mDeleted = $('#bihinmasterdeletedbody');
    if (mDeleted) {
      mDeleted.addEventListener('click', e => {
        const btn = e.target.closest && e.target.closest('[data-restore-item]');
        if (!btn) return;
        restoreMasterItem(Number(btn.dataset.restoreItem)).catch(() => {});
      });
    }

    window.addEventListener('beforeunload', onBeforeUnload);
    setSaveState(saveState);
  }

  function isBihinSectionActive() {
    const panel = $('#kintaibihin');
    return !!(panel && !panel.hidden && document.body.classList.contains('catalog-kintai'));
  }

  window.loadKintaiBihin = loadKintaiBihin;
  window.saveKintaiBihin = saveBihin;
  window.isKintaiBihinDirty = () => !!bihinDirty;
  window.confirmKintaiBihinLeave = async () => {
    if (!isBihinSectionActive() || !bihinDirty) return true;
    return confirmLeaveBihin();
  };
  // 単体テスト／確認用に公開
  window._bihinMath = { moneyYen, usageQty, parseQtyInput };
  window._bihinCountSheet = {
    isHiqColorLabel,
    expandCountSheetRows,
    buildCountSheetHtml,
    formatPrintPrice,
    countSheetTitleYm,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindBihinControls);
  else bindBihinControls();
})();
