/* 月別シフト表。権限は currentUser.mark_kintai（主管理者は常に可）。出勤簿実績には書き込まない。 */
(function () {
  let sheet = null;
  let cells = {}; // { personKey: { day: {kind,text} } }
  let people = [];
  let year = 0;
  let month = 0;
  let version = 0;
  let editMode = 'off'; // normal | off | time | erase
  let undoStack = [];
  let saveTimer = null;
  let saveState = 'idle'; // idle | dirty | saving | saved | failed
  let saveError = '';
  let shiftDirty = false;
  let loading = false;
  let rosterList = []; // 出勤簿在籍（未掲載追加用）

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
    return JSON.parse(JSON.stringify(src || (Array.isArray(src) ? [] : {})));
  }

  function newShiftKey() {
    return 'shift:' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }

  function cellAt(key, day) {
    const row = cells[key];
    if (!row) return null;
    return row[String(day)] || row[day] || null;
  }

  function pushUndo() {
    undoStack.push({ people: cloneData(people), cells: cloneData(cells) });
    if (undoStack.length > 100) undoStack.shift();
  }

  function markDirty() {
    shiftDirty = true;
    setSaveState('dirty');
    scheduleSave();
  }

  function setCell(key, day, value, recordUndo) {
    if (recordUndo) pushUndo();
    if (!cells[key]) cells[key] = {};
    const k = String(day);
    if (!value) {
      delete cells[key][k];
      if (!Object.keys(cells[key]).length) delete cells[key];
    } else {
      cells[key][k] = value;
    }
    markDirty();
  }

  function setSaveState(state, err) {
    saveState = state;
    saveError = err || '';
    const el = $('#shiftsavestatus');
    if (!el) return;
    el.className = 'shift-save-status shift-save-' + state;
    if (state === 'idle') el.textContent = '未編集';
    else if (state === 'dirty') el.textContent = '未保存';
    else if (state === 'saving') el.textContent = '保存中…';
    else if (state === 'saved') el.textContent = '保存済み';
    else if (state === 'failed') el.textContent = '保存失敗（再試行します）' + (saveError ? '：' + saveError : '');
  }

  function scheduleSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      saveShift().catch(() => {});
    }, 450);
  }

  function applySheet(data) {
    sheet = data || null;
    year = data && data.year != null ? data.year : year;
    month = data && data.month != null ? data.month : month;
    version = data && data.version != null ? data.version : 0;
    people = cloneData((data && data.people) || []);
    cells = cloneData((data && data.cells) || {});
    rosterList = cloneData((data && data.roster) || []);
    shiftDirty = false;
    undoStack = [];
    const input = $('#shiftyearmonth');
    if (input && year && month) input.value = ymValue(year, month);
    paintShift();
    const meta = $('#shiftmeta');
    if (meta) {
      const when = data && data.updatedAt ? `最終保存 ${data.updatedAt}` : 'まだ保存がありません';
      const who = data && data.updatedBy ? `（${data.updatedBy}）` : '';
      const nRoster = people.filter(p => p.source === 'roster').length;
      const nShift = people.filter(p => p.source === 'shift').length;
      const nHist = people.filter(p => p.source === 'history').length;
      meta.textContent = `${year}年${month}月 ・ ${people.length}行` +
        `（在籍${nRoster}` +
        (nShift ? `／シフト専用${nShift}` : '') +
        (nHist ? `／過去保持${nHist}` : '') +
        `） ・ ${when}${who}`;
    }
  }

  function paintModeButtons() {
    document.querySelectorAll('.shift-mode').forEach(b => {
      b.classList.toggle('on', b.dataset.shiftMode === editMode);
    });
    const hint = $('#shifthint');
    if (!hint) return;
    if (editMode === 'off') hint.textContent = '休みモード: 左クリックで×。氏名は左列で編集、行の追加・削除はその月だけに効きます。';
    else if (editMode === 'time') hint.textContent = '時間指定モード: 左クリックで上の文言を連続入力。右クリックでクリア。';
    else if (editMode === 'erase') hint.textContent = '消去モード: 左クリックでセルを空（出勤）にします。';
    else hint.textContent = '通常: 左クリックで×のON/OFF、右クリックでクリア。氏名は直接編集できます。';
  }

  function cellLabel(cell) {
    if (!cell) return '';
    if (cell.kind === 'off') return '×';
    return cell.text || '';
  }

  function sourceTag(p) {
    if (p.source === 'shift') return '<small class="shift-tag">シフト</small>';
    if (p.source === 'history') return '<small class="shift-tag">過去</small>';
    return '';
  }

  function paintShift() {
    const table = $('#shiftgrid');
    if (!table || !sheet) return;
    const days = sheet.days || [];
    const headDays = days.map(d => {
      const cls = d.kind === 'sat' ? 'shift-sat' : (d.kind === 'sun' || d.kind === 'holiday' ? 'shift-sun' : '');
      const title = d.holidayName ? ` title="${esc(d.holidayName)}"` : '';
      return `<th class="shift-day ${cls}"${title}><span class="shift-daynum">${d.day}</span><span class="shift-wday">${esc(d.weekdayLabel)}</span></th>`;
    }).join('');
    const body = people.map(p => {
      const rowCls = [
        p.source === 'history' ? 'shift-history' : '',
        p.source === 'shift' ? 'shift-only' : '',
      ].filter(Boolean).join(' ');
      const tds = days.map(d => {
        const cell = cellAt(p.key, d.day);
        const cls = [
          'shift-cell',
          d.kind === 'sat' ? 'shift-sat' : '',
          (d.kind === 'sun' || d.kind === 'holiday') ? 'shift-sun' : '',
          cell && cell.kind === 'off' ? 'shift-off' : '',
          cell && cell.kind === 'time' ? 'shift-time' : '',
        ].filter(Boolean).join(' ');
        return `<td class="${cls}" data-person="${esc(p.key)}" data-day="${d.day}" tabindex="0">${esc(cellLabel(cell))}</td>`;
      }).join('');
      return `<tr class="${rowCls}" data-row-key="${esc(p.key)}">
        <th class="shift-name">
          <div class="shift-name-edit">
            <span class="shift-name-print">${esc(p.displayName || '')}</span>
            <input type="text" class="shift-name-input" maxlength="80" data-person="${esc(p.key)}" value="${esc(p.displayName || '')}" aria-label="氏名">
            ${sourceTag(p)}
            <button type="button" class="quiet shift-row-del no-print" data-del-person="${esc(p.key)}" title="この行を削除" aria-label="行を削除">×</button>
          </div>
        </th>${tds}
      </tr>`;
    }).join('');
    const colSpan = days.length + 1;
    table.innerHTML = `<thead><tr><th class="shift-corner">氏名</th>${headDays}</tr></thead><tbody>${body || `<tr><td colspan="${colSpan}" class="shift-empty">行がありません。「行を追加」か「未掲載の在籍者を追加」を使ってください。</td></tr>`}</tbody>`;
  }

  function applyClick(key, day, button) {
    const cur = cellAt(key, day);
    if (button === 2 || editMode === 'erase') {
      if (!cur) return;
      setCell(key, day, null, true);
      paintShift();
      return;
    }
    if (editMode === 'off') {
      if (cur && cur.kind === 'off') setCell(key, day, null, true);
      else setCell(key, day, { kind: 'off', text: '×' }, true);
      paintShift();
      return;
    }
    if (editMode === 'time') {
      const text = (($('#shifttimetext') && $('#shifttimetext').value) || '').trim() || '15:00まで';
      setCell(key, day, { kind: 'time', text }, true);
      paintShift();
      return;
    }
    if (cur && cur.kind === 'off') setCell(key, day, null, true);
    else if (!cur) setCell(key, day, { kind: 'off', text: '×' }, true);
    else setCell(key, day, { kind: 'off', text: '×' }, true);
    paintShift();
  }

  function onGridPointer(e) {
    if (e.target.closest && (e.target.closest('.shift-name-input') || e.target.closest('.shift-row-del'))) return;
    const td = e.target.closest && e.target.closest('td.shift-cell');
    if (!td) return;
    if (e.type === 'contextmenu') {
      e.preventDefault();
      applyClick(td.dataset.person, Number(td.dataset.day), 2);
      return;
    }
    if (e.type === 'click') {
      if (e.button !== 0) return;
      applyClick(td.dataset.person, Number(td.dataset.day), 0);
    }
  }

  function onNameInput(e) {
    const input = e.target.closest && e.target.closest('.shift-name-input');
    if (!input) return;
    const key = input.dataset.person;
    const p = people.find(x => x.key === key);
    if (!p) return;
    const next = input.value.slice(0, 80);
    if (p.displayName === next) return;
    if (e.type === 'focus') {
      input.dataset.prevName = p.displayName || '';
      return;
    }
    if (e.type === 'change' || e.type === 'blur') {
      if ((input.dataset.prevName || '') === next) return;
      pushUndo();
      p.displayName = next.trim() || '（未入力）';
      input.value = p.displayName;
      const printName = input.parentElement && input.parentElement.querySelector('.shift-name-print');
      if (printName) printName.textContent = p.displayName;
      delete input.dataset.prevName;
      markDirty();
      // 並び替えのため再描画はせず、タグだけ維持
    }
  }

  function deleteRow(key) {
    const p = people.find(x => x.key === key);
    if (!p) return;
    if (!confirm(`「${p.displayName || key}」の行をこの月から削除しますか？\n（出勤簿の在籍マスタや他の月のシフトは消えません）`)) return;
    pushUndo();
    people = people.filter(x => x.key !== key).map((x, i) => ({ ...x, sortOrder: i }));
    if (cells[key]) delete cells[key];
    markDirty();
    paintShift();
  }

  function addShiftRow() {
    pushUndo();
    const key = newShiftKey();
    people.push({
      key,
      personId: null,
      employeeCode: key.slice(6),
      employment: 'shift',
      displayName: '（未入力）',
      sortOrder: people.length,
      source: 'shift',
    });
    markDirty();
    paintShift();
    const input = document.querySelector(`.shift-name-input[data-person="${CSS.escape(key)}"]`);
    if (input) {
      input.focus();
      input.select();
    }
  }

  function addMissingRoster() {
    const have = new Set(people.map(p => p.key));
    const add = (rosterList || []).filter(p => p && p.key && !have.has(p.key)).map((p, i) => ({
      key: p.key,
      personId: p.personId,
      employeeCode: p.employeeCode,
      employment: p.employment,
      displayName: p.displayName,
      sortOrder: people.length + i,
      source: 'roster',
    }));
    if (!add.length) {
      toast('追加できる在籍者はいません。');
      return;
    }
    pushUndo();
    people = people.concat(add);
    markDirty();
    paintShift();
    toast(`${add.length}人をこの月の表に追加しました。`);
  }

  function undoOnce() {
    if (!undoStack.length) {
      toast('戻す操作がありません。');
      return;
    }
    const prev = undoStack.pop();
    people = prev.people || [];
    cells = prev.cells || {};
    shiftDirty = true;
    setSaveState('dirty');
    paintShift();
    scheduleSave();
  }

  async function shiftFetch(path, data) {
    const r = await fetch(path, data ? {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Desk-Request': '1' },
      body: JSON.stringify(data),
    } : undefined);
    const result = await r.json().catch(() => ({}));
    if (r.status === 409 && result && result.sheet) {
      const err = Error(result.error || 'ほかの画面で先に保存されています。');
      err.conflict = true;
      err.sheet = result.sheet;
      throw err;
    }
    if (!r.ok) {
      if (r.status === 401 && typeof authScreen === 'function') {
        currentUser = null;
        authScreen('login');
      }
      throw Error(result.error || '処理できませんでした。');
    }
    return result;
  }

  async function loadKintaiShift(forceYm) {
    if (!canKintai()) throw Error('勤怠管理の権限がありません。');
    if (loading) return;
    if (shiftDirty) {
      try { await saveShift(); } catch (_) { /* keep */ }
      if (shiftDirty) {
        toast('未保存のシフトがあるため月を切り替えできません。保存を確認してください。');
        const input = $('#shiftyearmonth');
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
      const input = $('#shiftyearmonth');
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
      const data = await api(`/api/kintai/shift?year=${y}&month=${m}`);
      if (!data.year) data.year = y;
      if (!data.month) data.month = m;
      applySheet(data);
      setSaveState(data.exists ? 'saved' : 'idle');
    } finally {
      loading = false;
    }
  }

  async function saveShift() {
    if (!canKintai()) return;
    if (!shiftDirty && saveState !== 'failed') return;
    if (!year || !month) return;
    setSaveState('saving');
    // sortOrder を現在の並びに合わせる
    people = people.map((p, i) => ({ ...p, sortOrder: i, displayName: (p.displayName || '').trim() || '（未入力）' }));
    const payload = { year, month, version, people, cells };
    try {
      const data = await shiftFetch('/api/kintai/shift', payload);
      if (!data.year) data.year = year;
      if (!data.month) data.month = month;
      const keepUndo = undoStack;
      applySheet(data);
      undoStack = keepUndo;
      shiftDirty = false;
      setSaveState('saved');
    } catch (e) {
      if (e.conflict) {
        // 競合時も画面の未保存入力は残す。自動再試行は止め、月切替時の警告対象に残す。
        shiftDirty = true;
        setSaveState('failed', e.message || 'ほかの画面で先に保存されています');
        toast((e.message || 'ほかの画面で先に保存されています') + '（入力は画面に残しています）');
        return;
      }
      // 通信・サーバーエラー: 入力保持のまま再試行
      shiftDirty = true;
      setSaveState('failed', e.message || '保存に失敗しました');
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(() => {
        saveTimer = null;
        saveShift().catch(() => {});
      }, 2000);
    }
  }

  function shiftBy(delta) {
    let y = year;
    let m = month + delta;
    while (m < 1) { m += 12; y -= 1; }
    while (m > 12) { m -= 12; y += 1; }
    const input = $('#shiftyearmonth');
    if (input) input.value = ymValue(y, m);
    loadKintaiShift({ year: y, month: m }).catch(e => toast(e.message));
  }

  function printShift() {
    const title = $('#shiftprinttitle');
    if (title && year && month) {
      title.textContent = `シフト表 ${year}年${month}月`;
    }
    document.body.classList.add('printing-shift');
    let cleaned = false;
    const done = () => {
      if (cleaned) return;
      cleaned = true;
      document.body.classList.remove('printing-shift');
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

  function bindShiftControls() {
    if (!$('#kintaishift')) return;
    document.querySelectorAll('.shift-mode').forEach(b => {
      b.onclick = () => {
        editMode = b.dataset.shiftMode || 'normal';
        paintModeButtons();
      };
    });
    paintModeButtons();
    const ym = $('#shiftyearmonth');
    if (ym) {
      const t = todayYm();
      if (!ym.value) ym.value = ymValue(t.year, t.month);
      ym.onchange = () => {
        const parsed = parseYm(ym.value);
        if (!parsed) return;
        loadKintaiShift(parsed).catch(e => toast(e.message));
      };
    }
    const prev = $('#shiftprev');
    const next = $('#shiftnext');
    const cur = $('#shiftthismonth');
    if (prev) prev.onclick = () => shiftBy(-1);
    if (next) next.onclick = () => shiftBy(1);
    if (cur) cur.onclick = () => {
      const t = todayYm();
      const input = $('#shiftyearmonth');
      if (input) input.value = ymValue(t.year, t.month);
      loadKintaiShift(t).catch(e => toast(e.message));
    };
    const undo = $('#shiftundo');
    if (undo) undo.onclick = undoOnce;
    const pr = $('#shiftprint');
    if (pr) pr.onclick = printShift;
    const add = $('#shiftaddrow');
    if (add) add.onclick = () => addShiftRow();
    const addRoster = $('#shiftaddroster');
    if (addRoster) addRoster.onclick = () => { try { addMissingRoster(); } catch (e) { toast(e.message); } };
    const wrap = $('#shiftgridwrap');
    if (wrap) {
      wrap.addEventListener('click', e => {
        const del = e.target.closest && e.target.closest('[data-del-person]');
        if (del) {
          e.preventDefault();
          deleteRow(del.dataset.delPerson);
          return;
        }
        onGridPointer(e);
      });
      wrap.addEventListener('contextmenu', onGridPointer);
      wrap.addEventListener('focusin', onNameInput);
      wrap.addEventListener('change', onNameInput);
      wrap.addEventListener('focusout', onNameInput);
    }
  }

  window.loadKintaiShift = loadKintaiShift;
  window.paintKintaiShift = paintShift;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindShiftControls);
  else bindShiftControls();
})();
