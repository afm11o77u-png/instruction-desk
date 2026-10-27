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

  function cloneCells(src) {
    return JSON.parse(JSON.stringify(src || {}));
  }

  function cellAt(key, day) {
    const row = cells[key];
    if (!row) return null;
    return row[String(day)] || row[day] || null;
  }

  function setCell(key, day, value, recordUndo) {
    if (recordUndo) {
      undoStack.push(cloneCells(cells));
      if (undoStack.length > 100) undoStack.shift();
    }
    if (!cells[key]) cells[key] = {};
    const k = String(day);
    if (!value) {
      delete cells[key][k];
      if (!Object.keys(cells[key]).length) delete cells[key];
    } else {
      cells[key][k] = value;
    }
    shiftDirty = true;
    setSaveState('dirty');
    scheduleSave();
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
    people = (data && data.people) || [];
    cells = cloneCells((data && data.cells) || {});
    shiftDirty = false;
    undoStack = [];
    const input = $('#shiftyearmonth');
    if (input && year && month) input.value = ymValue(year, month);
    paintShift();
    const meta = $('#shiftmeta');
    if (meta) {
      const when = data && data.updatedAt ? `最終保存 ${data.updatedAt}` : 'まだ保存がありません';
      const who = data && data.updatedBy ? `（${data.updatedBy}）` : '';
      meta.textContent = `${year}年${month}月 ・ 在籍 ${people.filter(p => p.source === 'roster').length}人` +
        (people.some(p => p.source === 'history') ? ` ／ 過去保持 ${people.filter(p => p.source === 'history').length}人` : '') +
        ` ・ ${when}${who}`;
    }
  }

  function paintModeButtons() {
    document.querySelectorAll('.shift-mode').forEach(b => {
      b.classList.toggle('on', b.dataset.shiftMode === editMode);
    });
    const hint = $('#shifthint');
    if (!hint) return;
    if (editMode === 'off') hint.textContent = '休みモード: 左クリックで×を入れます。もう一度で解除。時間指定のセルは×に置き換えます。';
    else if (editMode === 'time') hint.textContent = '時間指定モード: 左クリックで上の文言を連続入力できます。右クリックでクリア。';
    else if (editMode === 'erase') hint.textContent = '消去モード: 左クリックでセルを空（出勤）にします。';
    else hint.textContent = '通常: 左クリックで×のON/OFF、右クリックでクリア。コンテキストメニュー抑止はセル内のみです。';
  }

  function cellLabel(cell) {
    if (!cell) return '';
    if (cell.kind === 'off') return '×';
    return cell.text || '';
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
      const hist = p.source === 'history' ? ' shift-history' : '';
      const tds = days.map(d => {
        const cell = cellAt(p.key, d.day);
        const cls = [
          'shift-cell',
          d.kind === 'sat' ? 'shift-sat' : '',
          (d.kind === 'sun' || d.kind === 'holiday') ? 'shift-sun' : '',
          cell && cell.kind === 'off' ? 'shift-off' : '',
          cell && cell.kind === 'time' ? 'shift-time' : '',
        ].filter(Boolean).join(' ');
        const label = cellLabel(cell);
        return `<td class="${cls}" data-person="${esc(p.key)}" data-day="${d.day}" tabindex="0">${esc(label)}</td>`;
      }).join('');
      return `<tr class="${hist}"><th class="shift-name">${esc(p.displayName)}${p.source === 'history' ? '<small>過去</small>' : ''}</th>${tds}</tr>`;
    }).join('');
    table.innerHTML = `<thead><tr><th class="shift-corner">氏名</th>${headDays}</tr></thead><tbody>${body || `<tr><td colspan="${days.length + 1}" class="shift-empty">在籍者がいません。出勤簿側の対象者を確認してください。</td></tr>`}</tbody>`;
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
    // normal: toggle ×
    if (cur && cur.kind === 'off') setCell(key, day, null, true);
    else if (!cur) setCell(key, day, { kind: 'off', text: '×' }, true);
    else setCell(key, day, { kind: 'off', text: '×' }, true);
    paintShift();
  }

  function onGridPointer(e) {
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

  function undoOnce() {
    if (!undoStack.length) {
      toast('戻す操作がありません。');
      return;
    }
    cells = undoStack.pop();
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
      try { await saveShift(); } catch (_) { /* 失敗時も切替は続行せず保持 */ }
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
    const payload = {
      year,
      month,
      version,
      people,
      cells,
    };
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
      if (e.conflict && e.sheet) {
        // 同時編集: サーバー側を表示。ローカル未保存は保持しないと要件の「失敗時保持」と衝突し得るが、
        // 上書き防止が優先。未保存内容は undo スタックに残し、シートはサーバー版へ。
        const local = cloneCells(cells);
        undoStack.push(local);
        applySheet(e.sheet);
        shiftDirty = false;
        setSaveState('failed', e.message);
        toast(e.message);
        return;
      }
      // 通信失敗など: 画面の入力は保持したまま再試行
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
    document.body.classList.add('printing-shift');
    const done = () => document.body.classList.remove('printing-shift');
    window.addEventListener('afterprint', done, { once: true });
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
    const wrap = $('#shiftgridwrap');
    if (wrap) {
      wrap.addEventListener('click', onGridPointer);
      wrap.addEventListener('contextmenu', onGridPointer);
    }
  }

  window.loadKintaiShift = loadKintaiShift;
  window.paintKintaiShift = paintShift;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindShiftControls);
  else bindShiftControls();
})();
