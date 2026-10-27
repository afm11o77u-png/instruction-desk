/* 月別備品棚卸。権限は currentUser.mark_kintai（主管理者は常に可）。
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

  function $(sel, root) { return (root || document).querySelector(sel); }

  function canKintai() {
    return !!currentUser && (currentUser.role === 'owner' || !!currentUser.mark_kintai);
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
      table.innerHTML = `<tbody><tr><td class="bihin-empty-msg" colspan="12">
        表示する品目がありません。右上の「品目マスタ」から科目・品名・単位・単価を登録してください（カタログの推測登録はしません）。
      </td></tr></tbody>`;
      return;
    }
    const head = `<thead>
      <tr class="bihin-head-groups">
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
      return `<tr data-row-index="${i}">
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
      <td colspan="4">金額合計（数量は単位が異なるため合計しません）</td>
      <td class="bihin-calc"></td><td class="bihin-num bihin-calc">${formatYen(t.openingAmount)}</td>
      <td class="bihin-calc"></td><td class="bihin-num bihin-calc">${formatYen(t.purchaseAmount)}</td>
      <td class="bihin-calc"></td><td class="bihin-num bihin-calc">${formatYen(t.closingAmount)}</td>
      <td class="bihin-calc"></td><td class="bihin-num bihin-calc">${formatYen(t.usageAmount)}</td>
    </tr></tfoot>`;
    table.innerHTML = head + body + foot;
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
    if (!canKintai()) throw Error('勤怠管理の権限がありません。');
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
    // 0科目 1品名 2単位 3単価 4月初数 5月初金 6購入数 7購入金 8月末数 9月末金 10使用数 11使用金
    const r = rows[i];
    if (cells[5]) cells[5].textContent = formatYen(r.openingAmount);
    if (cells[7]) cells[7].textContent = formatYen(r.purchaseAmount);
    if (cells[9]) cells[9].textContent = formatYen(r.closingAmount);
    if (cells[10]) {
      cells[10].textContent = r.usageIncomplete ? '—' : formatNum(r.usageQty);
      cells[10].classList.toggle('bihin-warn', !!r.negativeUsage);
    }
    if (cells[11]) {
      cells[11].textContent = r.usageIncomplete ? '—' : formatYen(r.usageAmount);
      cells[11].classList.toggle('bihin-warn', !!r.negativeUsage);
    }
    const foot = $('#bihingrid tfoot tr');
    if (foot) {
      const t = totalsFromRows();
      const fcells = foot.querySelectorAll('td');
      // colspan4 + empty + openAmt + empty + purchAmt + empty + closeAmt + empty + usageAmt
      if (fcells[2]) fcells[2].textContent = formatYen(t.openingAmount);
      if (fcells[4]) fcells[4].textContent = formatYen(t.purchaseAmount);
      if (fcells[6]) fcells[6].textContent = formatYen(t.closingAmount);
      if (fcells[8]) fcells[8].textContent = formatYen(t.usageAmount);
    }
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
    if (!body) return;
    const active = masterItems.filter(it => it.active !== false);
    if (!active.length) {
      body.innerHTML = '<tr><td colspan="5">まだ品目がありません。上の欄から登録してください。</td></tr>';
      return;
    }
    body.innerHTML = active.map(it => `<tr>
      <td>${esc(it.category)}</td>
      <td>${esc(it.name)}</td>
      <td>${esc(it.unit)}</td>
      <td class="bihin-num">${formatYen(it.unitPrice)}</td>
      <td><button type="button" data-edit-item="${it.id}">編集</button></td>
    </tr>`).join('');
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
            <span>棚卸日：　　年　　月　　日</span>
            <span>記入者：</span>
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

    const wrap = $('#bihingridwrap');
    if (wrap) {
      wrap.addEventListener('input', onQtyInput);
      wrap.addEventListener('change', onQtyInput);
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
        const btn = e.target.closest && e.target.closest('[data-edit-item]');
        if (!btn) return;
        const id = Number(btn.dataset.editItem);
        const it = masterItems.find(x => x.id === id);
        if (it) fillMasterForm(it);
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
