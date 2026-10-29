/* 月別シフト表。権限は mark_kintai、または管理者チェック（manage/overview）。主管理者は常に可。出勤簿実績には書き込まない。
   保存は「保存」押下時のみ（表示中年月を上書き）。編集ごとの自動保存はしない。 */
(function () {
  let sheet = null;
  let cells = {}; // { personKey: { day: {kind,text} } }
  let people = [];
  let year = 0;
  let month = 0;
  let version = 0;
  let editMode = 'off'; // normal | off | task | time | erase
  let undoStack = [];
  let saveState = 'idle'; // idle | dirty | saving | saved | failed
  let saveError = '';
  let shiftDirty = false;
  let saving = false;
  let loading = false;
  let rosterList = []; // 出勤簿在籍（未掲載追加用）
  let workMemos = {}; // { personKey: text } 人ごと・月横断（personId 優先でサーバ保存）
  let memoDirty = false;
  let memoSaving = false;
  let leaveDialogResolver = null;
  let dragPersonKey = null; // 行ドラッグ中の person key
  const EMP_LABEL = { regular: '社員', contract: '契約社員', part: 'パート' };
  const TASK_MARKS = ['ピ', 'バ', '貼'];
  const PRINT_LEGEND = '空欄：出勤　×：休み　ピ：ピッキング　バ：バーコード　貼：シール貼　時刻：退勤予定';

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

  function formatSavedAt(iso) {
    if (!iso) return '';
    const s = String(iso).replace('T', ' ').trim();
    return s.length > 19 ? s.slice(0, 19) : s;
  }

  function setSaveState(state, err) {
    saveState = state;
    saveError = err || '';
    const el = $('#shiftsavestatus');
    if (!el) return;
    el.className = 'shift-save-status shift-save-' + state;
    if (state === 'idle') el.textContent = '未保存';
    else if (state === 'dirty') el.textContent = '未保存の変更';
    else if (state === 'saving') el.textContent = '保存中…';
    else if (state === 'saved') {
      const when = formatSavedAt(sheet && sheet.updatedAt);
      el.textContent = when ? `保存済み（${when}）` : '保存済み';
    } else if (state === 'failed') {
      el.textContent = '保存失敗' + (saveError ? '：' + saveError : '');
    }
    const btn = $('#shiftsave');
    // 未保存月の初回保存・失敗再試行・未保存の変更は押下可。保存済みで変更なしのときだけ無効
    if (btn) btn.disabled = state === 'saving' || (state === 'saved' && !shiftDirty);
  }

  function applySheet(data, opts) {
    sheet = data || null;
    year = data && data.year != null ? data.year : year;
    month = data && data.month != null ? data.month : month;
    version = data && data.version != null ? data.version : 0;
    people = cloneData((data && data.people) || []);
    cells = cloneData((data && data.cells) || {});
    rosterList = cloneData((data && data.roster) || []);
    // 勤務メモは月シートと独立。月保存の応答ではローカル編集を潰さない。
    if (!(opts && opts.keepLocalMemos) && data && data.workMemos && typeof data.workMemos === 'object') {
      workMemos = cloneData(data.workMemos);
      memoDirty = false;
      setMemoSaveState(Object.keys(workMemos).some(k => workMemos[k]) ? 'saved' : 'idle');
    }
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

  function setMemoSaveState(state, err) {
    const el = $('#shiftmemostatus');
    if (!el) return;
    el.className = 'shift-save-status shift-memo-status shift-save-' + state;
    if (state === 'idle') el.textContent = 'メモ未保存';
    else if (state === 'dirty') el.textContent = 'メモ未保存の変更';
    else if (state === 'saving') el.textContent = 'メモ保存中…';
    else if (state === 'saved') el.textContent = 'メモ保存済み';
    else if (state === 'failed') el.textContent = 'メモ保存失敗' + (err ? '：' + err : '');
    const btn = $('#shiftsavememos');
    if (btn) btn.disabled = state === 'saving' || (state === 'saved' && !memoDirty) || (state === 'idle' && !memoDirty);
  }

  function markMemoDirty() {
    memoDirty = true;
    setMemoSaveState('dirty');
  }

  function empNameClass(p) {
    const e = p && p.employment;
    if (e === 'regular' || e === 'contract' || e === 'part') return 'shift-emp-' + e;
    return '';
  }

  /** 表示用: 「15:00まで」→「15:00」。保存データは書き換えない。非時刻メモはそのまま。 */
  function displayTimeText(text) {
    const t = String(text || '');
    const m = t.match(/^(\d{1,2}:\d{2})\s*まで$/);
    return m ? m[1] : t;
  }

  function selectedTaskMark() {
    const sel = $('#shifttaskmark');
    const v = sel ? String(sel.value || '').trim() : '';
    return TASK_MARKS.includes(v) ? v : 'ピ';
  }

  function paintModeButtons() {
    document.querySelectorAll('.shift-mode').forEach(b => {
      b.classList.toggle('on', b.dataset.shiftMode === editMode);
    });
    const hint = $('#shifthint');
    if (!hint) return;
    if (editMode === 'off') hint.textContent = '休みモード: 左クリックで×。氏名左の≡で行入替、氏名編集・行追加削除はその月だけ。「保存」でこの年月を上書きします。';
    else if (editMode === 'task') hint.textContent = '作業モード: 左クリックで上の記号（ピ＝ピッキング／バ＝バーコード発行／貼＝シール貼り）を連続入力。同じ記号をもう一度で解除。「保存」でこの年月を上書きします。';
    else if (editMode === 'time') hint.textContent = '時間指定モード: 左クリックで上の時刻（例: 15:00）を連続入力。表示は「まで」なし。「保存」でこの年月を上書きします（勤務メモは別保存）。';
    else if (editMode === 'erase') hint.textContent = '消去モード: 左クリックでセルを空（出勤）にします。≡で行入替。「保存」でこの年月を上書きします。';
    else hint.textContent = '通常: 左クリックで空欄→×→ピ→バ→貼→空欄。右クリックでクリア。≡で行入替、氏名は直接編集。「保存」でこの年月を上書きします。';
  }

  function cellLabel(cell) {
    if (!cell) return '';
    if (cell.kind === 'off') return '×';
    if (cell.kind === 'task') return TASK_MARKS.includes(cell.text) ? cell.text : '';
    return displayTimeText(cell.text || '');
  }

  /** セル表示。時刻＋「まで」は「15:00」のみ（保存値は変更しない）。作業記号・自由メモはそのまま。 */
  function cellHtml(cell) {
    if (!cell) return '';
    if (cell.kind === 'off') return '×';
    if (cell.kind === 'task') {
      const mark = TASK_MARKS.includes(cell.text) ? cell.text : '';
      return mark ? `<span class="shift-task-mark">${esc(mark)}</span>` : '';
    }
    const raw = String(cell.text || '');
    const shown = displayTimeText(raw);
    if (/^\d{1,2}:\d{2}$/.test(String(shown).trim())) {
      return `<span class="shift-time-main">${esc(shown)}</span>`;
    }
    return esc(shown);
  }

  /** 通常モードの循環: 空→×→ピ→バ→貼→空。時刻セルは従来どおり×へ上書き（時刻入力は時間指定モード）。 */
  function nextNormalCell(cur) {
    if (!cur) return { kind: 'off', text: '×' };
    if (cur.kind === 'off') return { kind: 'task', text: 'ピ' };
    if (cur.kind === 'task') {
      const i = TASK_MARKS.indexOf(cur.text);
      if (i === 0) return { kind: 'task', text: 'バ' };
      if (i === 1) return { kind: 'task', text: '貼' };
      return null;
    }
    return { kind: 'off', text: '×' };
  }

  function printTitleText() {
    if (!year || !month) return 'マーク シフト表';
    return `${year}年 ${month}月 マーク シフト表`;
  }

  function sourceTag(p) {
    if (p.source === 'shift') return '<small class="shift-tag">シフト</small>';
    if (p.source === 'history') return '<small class="shift-tag">過去</small>';
    return '';
  }

  /** 表示中の表の人員。将来の検索／絞り込みがあればここで狭める。 */
  function visiblePeople() {
    return people.slice();
  }

  function updateBulkScopeNote() {
    const el = $('#shiftbulkscope');
    if (!el) return;
    const n = visiblePeople().length;
    const total = people.length;
    if (n < total) {
      el.textContent = `対象: 絞り込み中の ${n}人（表全体 ${total}人のうち）`;
    } else {
      el.textContent = `対象: 表示中の表の全員（${n}人）`;
    }
  }

  function bulkFillMode() {
    const checked = document.querySelector('input[name="shiftbulkmode"]:checked');
    return checked && checked.value === 'overwrite' ? 'overwrite' : 'empty';
  }

  /**
   * 指定日を対象者へ×。mode=empty は空欄のみ、overwrite は既存も×へ。
   * 変更があれば undo 1段・未保存表示。日曜の個別編集はそのまま可能。
   */
  function bulkMarkOffDays(dayNums, mode, label) {
    const days = (dayNums || []).map(Number).filter(d => d >= 1);
    const targets = visiblePeople();
    if (!days.length) {
      toast('対象の日付がありません。');
      return;
    }
    if (!targets.length) {
      toast('対象者がいません。');
      return;
    }
    pushUndo();
    const off = { kind: 'off', text: '×' };
    let changed = 0;
    for (const p of targets) {
      if (!p || !p.key) continue;
      for (const day of days) {
        const cur = cellAt(p.key, day);
        if (mode === 'empty') {
          if (cur) continue;
        } else if (cur && cur.kind === 'off') {
          continue;
        }
        if (!cells[p.key]) cells[p.key] = {};
        cells[p.key][String(day)] = { kind: off.kind, text: off.text };
        changed += 1;
      }
    }
    if (!changed) {
      undoStack.pop();
      toast(mode === 'empty' ? '空欄がありませんでした。' : '変更するセルがありませんでした。');
      return;
    }
    markDirty();
    paintShift();
    const who = targets.length === people.length
      ? `表示中の全員（${targets.length}人）`
      : `絞り込み中の ${targets.length}人`;
    toast(`${label || '一括×'}: ${changed}件（${who}・${mode === 'empty' ? '空欄のみ' : '上書き'}）。「ひとつ戻す」で取り消せます。`);
  }

  /** 対象者のうち、その日に×が1人でもいれば true。 */
  function dayHasAnyOff(day, targets) {
    const list = targets || visiblePeople();
    for (const p of list) {
      if (!p || !p.key) continue;
      const cur = cellAt(p.key, day);
      if (cur && cur.kind === 'off') return true;
    }
    return false;
  }

  /**
   * 指定日のセルを対象者全員ブランクへ。undo 1段・未保存表示は一括×と同じ。
   */
  function bulkClearOneDay(day, label) {
    const d = Number(day);
    const targets = visiblePeople();
    if (!(d >= 1)) {
      toast('対象の日付がありません。');
      return;
    }
    if (!targets.length) {
      toast('対象者がいません。');
      return;
    }
    pushUndo();
    let changed = 0;
    for (const p of targets) {
      if (!p || !p.key) continue;
      const cur = cellAt(p.key, d);
      if (!cur) continue;
      const k = String(d);
      delete cells[p.key][k];
      if (!Object.keys(cells[p.key]).length) delete cells[p.key];
      changed += 1;
    }
    if (!changed) {
      undoStack.pop();
      toast('消すセルがありませんでした。');
      return;
    }
    markDirty();
    paintShift();
    const who = targets.length === people.length
      ? `表示中の全員（${targets.length}人）`
      : `絞り込み中の ${targets.length}人`;
    toast(`${label || '一括クリア'}: ${changed}件を空欄に（${who}）。「ひとつ戻す」で取り消せます。`);
  }

  /**
   * 日付見出し「この日を全員×」のトグル:
   * その日に×が1人でもあれば全員を空欄へ。なければ既存の一括×（空欄のみ／上書き）。
   */
  function bulkMarkOneDay(day) {
    const d = Number(day);
    const targets = visiblePeople();
    const label = `${d}日を全員×`;
    if (dayHasAnyOff(d, targets)) {
      bulkClearOneDay(d, label);
      return;
    }
    bulkMarkOffDays([d], bulkFillMode(), label);
  }

  function bulkMarkWeekday() {
    const sel = $('#shiftbulkweekday');
    const wd = sel ? Number(sel.value) : 6;
    if (!sheet || !sheet.days) return;
    const days = (sheet.days || []).filter(d => Number(d.weekday) === wd).map(d => d.day);
    const optLabel = sel && sel.selectedOptions && sel.selectedOptions[0]
      ? `今月の${sel.selectedOptions[0].textContent}曜を全員×`
      : '今月の指定曜日を全員×';
    bulkMarkOffDays(days, bulkFillMode(), optLabel);
  }

  /** 未保存月で行を追加したとき、空の日曜だけ×（保存済み・既存セルは触らない）。 */
  function seedSundaysForKeys(keys, recordUndo) {
    if (!sheet || sheet.exists) return;
    const sundayDays = (sheet.days || []).filter(d => Number(d.weekday) === 6).map(d => d.day);
    if (!sundayDays.length || !(keys || []).length) return;
    let changed = false;
    if (recordUndo) pushUndo();
    for (const key of keys) {
      for (const day of sundayDays) {
        if (cellAt(key, day)) continue;
        if (!cells[key]) cells[key] = {};
        cells[key][String(day)] = { kind: 'off', text: '×' };
        changed = true;
      }
    }
    if (recordUndo && !changed) undoStack.pop();
  }

  function paintShift() {
    const table = $('#shiftgrid');
    if (!table || !sheet) return;
    const days = sheet.days || [];
    const headDays = days.map(d => {
      const cls = d.kind === 'sat' ? 'shift-sat' : (d.kind === 'sun' || d.kind === 'holiday' ? 'shift-sun' : '');
      const title = d.holidayName ? ` title="${esc(d.holidayName)}"` : '';
      return `<th class="shift-day ${cls}"${title}><span class="shift-daynum">${d.day}</span><span class="shift-wday">${esc(d.weekdayLabel)}</span><button type="button" class="shift-day-bulk no-print" data-bulk-day="${d.day}" title="${d.day}日を表示中の全員×" aria-label="${d.day}日を表示中の全員×">ALL</button></th>`;
    }).join('');
    const body = people.map(p => {
      const rowCls = [
        p.source === 'history' ? 'shift-history' : '',
        p.source === 'shift' ? 'shift-only' : '',
      ].filter(Boolean).join(' ');
      const empCls = empNameClass(p);
      const memoVal = workMemos[p.key] || '';
      const tds = days.map(d => {
        const cell = cellAt(p.key, d.day);
        const cls = [
          'shift-cell',
          d.kind === 'sat' ? 'shift-sat' : '',
          (d.kind === 'sun' || d.kind === 'holiday') ? 'shift-sun' : '',
          cell && cell.kind === 'off' ? 'shift-off' : '',
          cell && cell.kind === 'time' ? 'shift-time' : '',
          cell && cell.kind === 'task' ? 'shift-task' : '',
        ].filter(Boolean).join(' ');
        return `<td class="${cls}" data-person="${esc(p.key)}" data-day="${d.day}" tabindex="0">${cellHtml(cell)}</td>`;
      }).join('');
      const empTitle = EMP_LABEL[p.employment] ? ` title="出勤簿区分: ${EMP_LABEL[p.employment]}"` : '';
      return `<tr class="${rowCls}" data-row-key="${esc(p.key)}">
        <td class="shift-memo">
          <textarea class="shift-memo-input" rows="2" maxlength="80" data-memo-person="${esc(p.key)}" placeholder="例: 週5" aria-label="勤務メモ ${esc(p.displayName || '')}">${esc(memoVal)}</textarea>
        </td>
        <th class="shift-name ${empCls}"${empTitle}>
          <div class="shift-name-edit">
            <span class="shift-drag no-print" draggable="true" data-drag-person="${esc(p.key)}" title="ドラッグで行を入替" aria-label="行を並べ替え" role="button">≡</span>
            <span class="shift-name-print">${esc(p.displayName || '')}</span>
            <input type="text" class="shift-name-input" maxlength="80" data-person="${esc(p.key)}" value="${esc(p.displayName || '')}" aria-label="氏名">
            ${sourceTag(p)}
            <button type="button" class="quiet shift-row-del no-print" data-del-person="${esc(p.key)}" title="この行を削除" aria-label="行を削除">×</button>
          </div>
        </th>${tds}
      </tr>`;
    }).join('');
    // 画面: メモ＋氏名＋日付。印刷ではメモ列を CSS で除外（空白列を残さない）
    const colSpan = days.length + 2;
    const cols = `<colgroup><col class="shift-col-memo"><col class="shift-col-name">${'<col class="shift-col-day">'.repeat(days.length)}</colgroup>`;
    const banner = `<tr class="shift-print-banner"><th colspan="${colSpan}"><div class="shift-print-head"><span class="shift-print-title-text">${esc(printTitleText())}</span><span class="shift-print-legend">${esc(PRINT_LEGEND)}</span></div></th></tr>`;
    const titleEl = $('#shiftprinttitle');
    if (titleEl) titleEl.textContent = printTitleText();
    table.innerHTML = `${cols}<thead>${banner}<tr><th class="shift-corner shift-corner-memo no-print">勤務メモ</th><th class="shift-corner shift-corner-name">氏名</th>${headDays}</tr></thead><tbody>${body || `<tr><td colspan="${colSpan}" class="shift-empty">行がありません。「行を追加」か「未掲載の在籍者を追加」を使ってください。</td></tr>`}</tbody>`;
    updateBulkScopeNote();
  }

  /** 人員行を fromKey → toKey の前（before）または後（after）へ移動。セルは person key 紐づけのまま。 */
  function reorderPeople(fromKey, toKey, place) {
    if (!fromKey || !toKey || fromKey === toKey) return false;
    const fromIdx = people.findIndex(p => p.key === fromKey);
    if (fromIdx < 0) return false;
    pushUndo();
    const [item] = people.splice(fromIdx, 1);
    let insertAt = people.findIndex(p => p.key === toKey);
    if (insertAt < 0) {
      people.splice(fromIdx, 0, item); // 復元
      undoStack.pop();
      return false;
    }
    if (place === 'after') insertAt += 1;
    people.splice(insertAt, 0, item);
    people = people.map((p, i) => ({ ...p, sortOrder: i }));
    markDirty();
    paintShift();
    return true;
  }

  function clearDragIndicators() {
    const table = $('#shiftgrid');
    if (!table) return;
    table.querySelectorAll('tr.shift-drag-over, tr.shift-dragging').forEach(tr => {
      tr.classList.remove('shift-drag-over', 'shift-dragging', 'shift-drop-before', 'shift-drop-after');
    });
  }

  function onRowDragStart(e) {
    const handle = e.target.closest && e.target.closest('[data-drag-person]');
    if (!handle) return;
    const key = handle.dataset.dragPerson;
    if (!key) return;
    dragPersonKey = key;
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', key); } catch (_) { /* IE ignore */ }
    const tr = handle.closest('tr');
    if (tr) tr.classList.add('shift-dragging');
  }

  function onRowDragEnd() {
    dragPersonKey = null;
    clearDragIndicators();
  }

  function dropPlaceForEvent(tr, e) {
    const rect = tr.getBoundingClientRect();
    const mid = rect.top + rect.height / 2;
    return e.clientY < mid ? 'before' : 'after';
  }

  function onRowDragOver(e) {
    if (!dragPersonKey) return;
    const tr = e.target.closest && e.target.closest('tr[data-row-key]');
    if (!tr) return;
    const toKey = tr.dataset.rowKey;
    if (!toKey || toKey === dragPersonKey) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    clearDragIndicators();
    const place = dropPlaceForEvent(tr, e);
    tr.classList.add('shift-drag-over', place === 'before' ? 'shift-drop-before' : 'shift-drop-after');
    const fromTr = $('#shiftgrid') && $('#shiftgrid').querySelector(`tr[data-row-key="${CSS.escape(dragPersonKey)}"]`);
    if (fromTr) fromTr.classList.add('shift-dragging');
  }

  function onRowDrop(e) {
    if (!dragPersonKey) return;
    const tr = e.target.closest && e.target.closest('tr[data-row-key]');
    if (!tr) return;
    const toKey = tr.dataset.rowKey;
    if (!toKey || toKey === dragPersonKey) {
      onRowDragEnd();
      return;
    }
    e.preventDefault();
    const place = dropPlaceForEvent(tr, e);
    const fromKey = dragPersonKey;
    onRowDragEnd();
    reorderPeople(fromKey, toKey, place);
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
    if (editMode === 'task') {
      const mark = selectedTaskMark();
      if (cur && cur.kind === 'task' && cur.text === mark) setCell(key, day, null, true);
      else setCell(key, day, { kind: 'task', text: mark }, true);
      paintShift();
      return;
    }
    if (editMode === 'time') {
      const text = (($('#shifttimetext') && $('#shifttimetext').value) || '').trim() || '15:00';
      setCell(key, day, { kind: 'time', text }, true);
      paintShift();
      return;
    }
    setCell(key, day, nextNormalCell(cur), true);
    paintShift();
  }

  function onGridPointer(e) {
    if (e.target.closest && (e.target.closest('.shift-name-input') || e.target.closest('.shift-memo-input') || e.target.closest('.shift-row-del') || e.target.closest('.shift-drag') || e.target.closest('.shift-day-bulk'))) return;
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
    }
  }

  function onMemoInput(e) {
    const input = e.target.closest && e.target.closest('.shift-memo-input');
    if (!input) return;
    const key = input.dataset.memoPerson;
    if (!key) return;
    if (e.type === 'focus') {
      input.dataset.prevMemo = workMemos[key] || '';
      return;
    }
    if (e.type === 'input') {
      const next = input.value.slice(0, 80);
      if ((workMemos[key] || '') === next) return;
      workMemos[key] = next;
      markMemoDirty();
      return;
    }
    if (e.type === 'change' || e.type === 'blur') {
      const next = input.value.slice(0, 80);
      workMemos[key] = next;
      if ((input.dataset.prevMemo || '') !== next) markMemoDirty();
      delete input.dataset.prevMemo;
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
    seedSundaysForKeys([key], false);
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
    seedSundaysForKeys(add.map(p => p.key), false);
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

  function resolveLeaveDialog(choice) {
    const dlg = $('#shiftsavedialog');
    if (dlg && dlg.open) dlg.close();
    const fn = leaveDialogResolver;
    leaveDialogResolver = null;
    if (fn) fn(choice);
  }

  function askLeaveAction() {
    return new Promise(resolve => {
      const dlg = $('#shiftsavedialog');
      if (!dlg || typeof dlg.showModal !== 'function') {
        // フォールバック: 保存するか／破棄するかの二段確認
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

  async function confirmLeaveMemos() {
    if (!memoDirty) return true;
    if (confirm('勤務メモに未保存の変更があります。保存しますか？\n（キャンセルで破棄して移動）')) {
      try {
        await saveWorkMemos();
      } catch (_) { /* keep */ }
      if (memoDirty) {
        toast('勤務メモを保存できなかったため移動を中止しました。');
        return false;
      }
      return true;
    }
    // 破棄: サーバのメモへ戻す（月シートは触らない）
    try {
      const data = await api(`/api/kintai/shift?year=${year}&month=${month}`);
      workMemos = cloneData((data && data.workMemos) || {});
      memoDirty = false;
      setMemoSaveState(Object.keys(workMemos).some(k => workMemos[k]) ? 'saved' : 'idle');
      paintShift();
    } catch (e) {
      toast(e.message || '勤務メモの破棄に失敗しました。');
      return false;
    }
    return true;
  }

  /** 未保存があれば確認。true=続行可 / false=キャンセル */
  async function confirmLeaveShift() {
    if (!shiftDirty && !memoDirty) return true;
    if (shiftDirty) {
      const action = await askLeaveAction();
      if (action === 'cancel') return false;
      if (action === 'save') {
        try {
          await saveShift({ force: true });
        } catch (_) { /* keep */ }
        if (shiftDirty) {
          toast('保存できなかったため移動を中止しました。');
          return false;
        }
      } else {
        // discard: 最終保存（または未保存月の初期表示）へ戻してから移動可にする
        try {
          await reloadCurrentMonth({ discard: true });
        } catch (e) {
          toast(e.message || '破棄に失敗しました。');
          return false;
        }
      }
    }
    return confirmLeaveMemos();
  }

  async function reloadCurrentMonth(opts) {
    if (!year || !month) return;
    const data = await api(`/api/kintai/shift?year=${year}&month=${month}`);
    if (!data.year) data.year = year;
    if (!data.month) data.month = month;
    // 月の破棄では勤務メモのローカル編集を残す（メモは別保存）
    applySheet(data, { keepLocalMemos: !(opts && opts.reloadMemos) && memoDirty });
    if (opts && opts.reloadMemos) {
      workMemos = cloneData((data && data.workMemos) || {});
      memoDirty = false;
      setMemoSaveState(Object.keys(workMemos).some(k => workMemos[k]) ? 'saved' : 'idle');
    }
    setSaveState(data.exists ? 'saved' : 'idle');
    if (opts && opts.discard) toast('変更を破棄し、最後の保存内容に戻しました。');
  }

  async function loadKintaiShift(forceYm, opts) {
    if (!canKintai()) throw Error('マーク管理の権限がありません。');
    if (loading) return;
    const skipGuard = opts && opts.skipGuard;
    if (!skipGuard && shiftDirty) {
      const ok = await confirmLeaveShift();
      if (!ok) {
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

  async function saveShift(opts) {
    if (!canKintai()) return;
    const force = opts && opts.force;
    if (saving) return;
    if (!force && !shiftDirty && saveState !== 'failed') return;
    if (!year || !month) return;
    saving = true;
    setSaveState('saving');
    people = people.map((p, i) => ({ ...p, sortOrder: i, displayName: (p.displayName || '').trim() || '（未入力）' }));
    const payload = { year, month, version, people, cells };
    try {
      const data = await shiftFetch('/api/kintai/shift', payload);
      if (!data.year) data.year = year;
      if (!data.month) data.month = month;
      const keepUndo = undoStack;
      // 月保存は勤務メモを送らない・上書きしない
      applySheet(data, { keepLocalMemos: true });
      undoStack = keepUndo;
      shiftDirty = false;
      setSaveState('saved');
      toast(`${year}年${month}月のシフト表を保存しました。`);
    } catch (e) {
      if (e.conflict) {
        shiftDirty = true;
        setSaveState('failed', e.message || 'ほかの画面で先に保存されています');
        toast((e.message || 'ほかの画面で先に保存されています') + '（入力は画面に残しています）');
        throw e;
      }
      shiftDirty = true;
      setSaveState('failed', e.message || '保存に失敗しました');
      toast((e.message || '保存に失敗しました') + '（入力は画面に残しています）');
      throw e;
    } finally {
      saving = false;
      setSaveState(saveState); // refresh button disabled state
    }
  }

  /** 勤務メモのみ保存（月シートの version / cells / 並びは送らない・変更しない）。 */
  async function saveWorkMemos() {
    if (!canKintai()) return;
    if (memoSaving) return;
    memoSaving = true;
    setMemoSaveState('saving');
    const memos = people.map(p => ({
      key: p.key,
      personId: typeof p.personId === 'number' ? p.personId : null,
      memo: (workMemos[p.key] != null ? workMemos[p.key] : '').slice(0, 80),
    }));
    try {
      const data = await shiftFetch('/api/kintai/shift/work-memos', { memos });
      const next = (data && data.workMemos) || {};
      // 画面上の全行キーを反映（空メモも消す）
      const merged = {};
      for (const p of people) {
        merged[p.key] = next[p.key] != null ? next[p.key] : '';
      }
      workMemos = merged;
      memoDirty = false;
      setMemoSaveState('saved');
      toast('勤務メモを保存しました（月のシフト表は変更していません）。');
    } catch (e) {
      memoDirty = true;
      setMemoSaveState('failed', e.message || '保存に失敗しました');
      toast((e.message || '勤務メモの保存に失敗しました') + '（入力は画面に残しています）');
      throw e;
    } finally {
      memoSaving = false;
      setMemoSaveState(memoDirty ? 'dirty' : 'saved');
    }
  }

  /** 画面上の現在並びを「今後の未作成月」の標準並びとして保存（セル・月シートは送らない）。 */
  async function saveDefaultOrder() {
    if (!canKintai()) return;
    const keys = people.map(p => p && p.key).filter(Boolean);
    if (!keys.length) {
      toast('並び順として保存する行がありません。');
      return;
    }
    const btn = $('#shiftsavedefaultorder');
    if (btn) btn.disabled = true;
    try {
      const data = await shiftFetch('/api/kintai/shift/default-order', { keys });
      const n = (data && data.keys && data.keys.length) || keys.length;
      toast(`標準の並び順を保存しました（${n}人・未作成の月に使用）。`);
    } catch (e) {
      toast(e.message || '標準の並び順の保存に失敗しました。');
      throw e;
    } finally {
      if (btn) btn.disabled = false;
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

  function closePrintDialog() {
    const dlg = $('#shiftprintdialog');
    if (dlg && typeof dlg.close === 'function') dlg.close();
  }

  function openPrintDialog() {
    const dlg = $('#shiftprintdialog');
    if (!dlg || typeof dlg.showModal !== 'function') {
      // 古い環境向け: ダイアログ無しでカラー用として印刷
      runPrint('color');
      return;
    }
    const color = dlg.querySelector('input[name="shiftprintmode"][value="color"]');
    if (color) color.checked = true;
    dlg.showModal();
  }

  function selectedPrintMode() {
    const checked = document.querySelector('input[name="shiftprintmode"]:checked');
    return checked && checked.value === 'bw' ? 'bw' : 'color';
  }

  /**
   * Chromium の印刷プレビューは unnamed @page の向きを採用し、named page
   *（page: shift-sheet）を無視することがある。style.css / sheet.css の
   * @page{size:A4}（縦）より後に unnamed landscape を差し込み、プレビュー既定を横にする。
   * 印刷終了後に外すので指示書など通常の A4 縦は壊さない。
   */
  const SHIFT_PRINT_PAGE_CSS_ID = 'shift-print-page-css';
  const SHIFT_PRINT_PAGE_CSS = '@page{size:A4 landscape;margin:6mm}';

  function ensureShiftLandscapePage() {
    let el = document.getElementById(SHIFT_PRINT_PAGE_CSS_ID);
    if (!el) {
      el = document.createElement('style');
      el.id = SHIFT_PRINT_PAGE_CSS_ID;
      document.head.appendChild(el);
    }
    el.textContent = SHIFT_PRINT_PAGE_CSS;
  }

  function clearShiftLandscapePage() {
    const el = document.getElementById(SHIFT_PRINT_PAGE_CSS_ID);
    if (el) el.remove();
  }

  function runPrint(mode) {
    const title = $('#shiftprinttitle');
    if (title) title.textContent = printTitleText();
    // 印刷は表示中の live データのみ。保存データは書き換えない。
    document.body.classList.remove('print-shift-color', 'print-shift-bw');
    document.body.classList.add('printing-shift', mode === 'bw' ? 'print-shift-bw' : 'print-shift-color');
    ensureShiftLandscapePage();
    let cleaned = false;
    const done = () => {
      if (cleaned) return;
      cleaned = true;
      clearShiftLandscapePage();
      document.body.classList.remove('printing-shift', 'print-shift-color', 'print-shift-bw');
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

  function printShift() {
    openPrintDialog();
  }

  function onBeforeUnload(e) {
    if (!shiftDirty && !memoDirty) return;
    e.preventDefault();
    e.returnValue = '';
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
    const saveBtn = $('#shiftsave');
    if (saveBtn) {
      saveBtn.onclick = () => {
        saveShift({ force: true }).catch(() => {});
      };
    }
    const memoBtn = $('#shiftsavememos');
    if (memoBtn) {
      memoBtn.onclick = () => {
        saveWorkMemos().catch(() => {});
      };
    }
    setMemoSaveState(memoDirty ? 'dirty' : 'idle');
    const defaultOrderBtn = $('#shiftsavedefaultorder');
    if (defaultOrderBtn) {
      defaultOrderBtn.onclick = () => {
        saveDefaultOrder().catch(() => {});
      };
    }
    const pr = $('#shiftprint');
    if (pr) pr.onclick = printShift;
    const printGo = $('#shiftprintdialog-go');
    if (printGo) {
      printGo.onclick = () => {
        const mode = selectedPrintMode();
        closePrintDialog();
        runPrint(mode);
      };
    }
    const printCancel = $('#shiftprintdialog-cancel');
    if (printCancel) printCancel.onclick = () => closePrintDialog();
    const printClose = $('#shiftprintdialog-close');
    if (printClose) printClose.onclick = () => closePrintDialog();
    const printDlg = $('#shiftprintdialog');
    if (printDlg) {
      printDlg.addEventListener('cancel', e => {
        e.preventDefault();
        closePrintDialog();
      });
    }
    const add = $('#shiftaddrow');
    if (add) add.onclick = () => addShiftRow();
    const addRoster = $('#shiftaddroster');
    if (addRoster) addRoster.onclick = () => { try { addMissingRoster(); } catch (e) { toast(e.message); } };
    const bulkWday = $('#shiftbulkwday');
    if (bulkWday) bulkWday.onclick = () => bulkMarkWeekday();
    document.querySelectorAll('input[name="shiftbulkmode"]').forEach(r => {
      r.onchange = updateBulkScopeNote;
    });
    updateBulkScopeNote();
    const wrap = $('#shiftgridwrap');
    if (wrap) {
      wrap.addEventListener('click', e => {
        const bulkDay = e.target.closest && e.target.closest('[data-bulk-day]');
        if (bulkDay) {
          e.preventDefault();
          bulkMarkOneDay(Number(bulkDay.dataset.bulkDay));
          return;
        }
        const del = e.target.closest && e.target.closest('[data-del-person]');
        if (del) {
          e.preventDefault();
          deleteRow(del.dataset.delPerson);
          return;
        }
        onGridPointer(e);
      });
      wrap.addEventListener('contextmenu', onGridPointer);
      wrap.addEventListener('focusin', e => { onNameInput(e); onMemoInput(e); });
      wrap.addEventListener('input', onMemoInput);
      wrap.addEventListener('change', e => { onNameInput(e); onMemoInput(e); });
      wrap.addEventListener('focusout', e => { onNameInput(e); onMemoInput(e); });
      wrap.addEventListener('dragstart', onRowDragStart);
      wrap.addEventListener('dragend', onRowDragEnd);
      wrap.addEventListener('dragover', onRowDragOver);
      wrap.addEventListener('drop', onRowDrop);
      wrap.addEventListener('dragleave', e => {
        // 子要素間移動では清消しすぎないよう、ラップ外へ出たときだけ
        if (e.target === wrap) clearDragIndicators();
      });
    }
    const dlgSave = $('#shiftsavedialog-save');
    const dlgDiscard = $('#shiftsavedialog-discard');
    const dlgCancel = $('#shiftsavedialog-cancel');
    if (dlgSave) dlgSave.onclick = () => resolveLeaveDialog('save');
    if (dlgDiscard) dlgDiscard.onclick = () => resolveLeaveDialog('discard');
    if (dlgCancel) dlgCancel.onclick = () => resolveLeaveDialog('cancel');
    const dlg = $('#shiftsavedialog');
    if (dlg) {
      dlg.addEventListener('cancel', e => {
        e.preventDefault();
        resolveLeaveDialog('cancel');
      });
    }
    window.addEventListener('beforeunload', onBeforeUnload);
    setSaveState(saveState);
  }

  function isShiftSectionActive() {
    const panel = $('#kintaishift');
    return !!(panel && !panel.hidden && document.body.classList.contains('catalog-kintai'));
  }

  window.loadKintaiShift = loadKintaiShift;
  window.paintKintaiShift = paintShift;
  window.saveKintaiShift = saveShift;
  window.saveKintaiShiftWorkMemos = saveWorkMemos;
  window.isKintaiShiftDirty = () => !!(shiftDirty || memoDirty);
  window.confirmKintaiShiftLeave = async () => {
    if (!isShiftSectionActive() || (!shiftDirty && !memoDirty)) return true;
    return confirmLeaveShift();
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindShiftControls);
  else bindShiftControls();
})();
