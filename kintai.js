/* マーク管理画面。権限は mark_kintai、または管理者チェック（manage/overview）。主管理者は常に可。 */
(function () {
  const EMP = { regular: '社員', contract: '契約', part: 'パート' };
  let pack = null;
  let chosenYear = 0;
  let pinnedPersonId = null;
  let dailyYm = '';
  let lastDaily = null;
  let lastImportSummary = null;
  let peopleFilter = 'all'; // all | active | retired
  // 出勤簿（個人）の列。累計・残数・起算日・給与などは集計ルール未定のため空欄。
  const PERSON_COLUMNS = [
    '月', '出勤日数', '休日数', '累計休日数', '年間休日残数', '有給取得日数', '有給累計取得日数', '有給残数',
    '有給休暇起算日', '普通勤務時間', '残業時間', '勤務時間計', '通常勤務給与', '残業給', '有給付与日数', '有給失効日数', 'その他休暇等', '備考',
  ];
  // 月別集計（全員合計）。給与列は未集計。
  const SUMMARY_COLUMNS = [
    '月', '普通勤務時間（時間:分）', '残業時間（時間:分）', '勤務時間計（時間:分）', '通常勤務給与（円）', '残業給与（円）', '給与計（円）',
  ];
  try {
    const raw = sessionStorage.getItem('kintaiLastImportSummary');
    if (raw) lastImportSummary = JSON.parse(raw);
  } catch (e) { lastImportSummary = null; }

  function $(sel, root) { return (root || document).querySelector(sel); }

  function fiscalStartYear() {
    if (chosenYear) return chosenYear;
    const el = $('#kintaiyear');
    const n = el ? Number(el.value) : 0;
    return n || (pack && pack.preferredFiscalStartYear) || 2026;
  }

  function rememberYear(year) {
    const n = Number(year);
    if (n) chosenYear = n;
    return chosenYear || fiscalStartYear();
  }

  function yearOptionsFrom(source) {
    if (source && source.fiscalYearOptions && source.fiscalYearOptions.length) return source.fiscalYearOptions;
    if (pack && pack.fiscalYearOptions && pack.fiscalYearOptions.length) return pack.fiscalYearOptions;
    const y = (source && source.fiscalStartYear) || chosenYear || 2026;
    const label = (source && source.fiscalLabel) || `${y}年2月〜${y + 1}年1月`;
    return [{ startYear: y, label }];
  }

  function fillYearSelect(el, source) {
    if (!el) return;
    const opts = yearOptionsFrom(source);
    const want = (source && source.fiscalStartYear) || chosenYear || (pack && pack.fiscalStartYear) || 2026;
    el.innerHTML = opts.map(o => `<option value="${o.startYear}">${esc(o.label)}</option>`).join('');
    if (![...el.options].some(o => Number(o.value) === Number(want))) {
      el.insertAdjacentHTML('afterbegin', `<option value="${want}">${want}年2月〜${want + 1}年1月</option>`);
    }
    el.value = String(want);
  }

  function fillAllYearSelects(source) {
    ['kintaiyear', 'kintaiimportyear', 'kintaisummaryyear', 'kintaidailyyear'].forEach(id => fillYearSelect(document.getElementById(id), source));
  }

  function syncYearValue(year) {
    ['kintaiyear', 'kintaiimportyear', 'kintaisummaryyear', 'kintaidailyyear'].forEach(id => {
      const el = document.getElementById(id);
      if (el && [...el.options].some(o => Number(o.value) === Number(year))) el.value = String(year);
    });
  }

  function selectedPersonId() {
    const el = $('#kintaiperson');
    const v = el && el.value;
    return v ? Number(v) : null;
  }

  function canKintai() {
    return !!currentUser && (currentUser.role === 'owner' || !!currentUser.manage || !!currentUser.overview || !!currentUser.mark_kintai);
  }

  function canManagePaths() {
    return !!currentUser && (currentUser.role === 'owner' || !!currentUser.manage);
  }

  function personFilterMode() {
    const el = $('#kintaifilter');
    if (el && el.value) peopleFilter = el.value;
    return peopleFilter || 'all';
  }

  function passesPeopleFilter(retired) {
    const f = personFilterMode();
    if (f === 'active') return !retired;
    if (f === 'retired') return !!retired;
    return true;
  }

  function personOptionHtml(p) {
    const tag = p.retired ? '退職' : (EMP[p.employment] || p.employment);
    return `<option value="${p.id}">${esc(p.displayName)}（${esc(p.employeeCode)}・${esc(tag)}）</option>`;
  }

  function applyPersonCode(personId, employeeCode) {
    if (!pack || personId == null || employeeCode == null || employeeCode === '') return;
    const id = String(personId);
    (pack.people || []).forEach(p => { if (String(p.id) === id) p.employeeCode = employeeCode; });
    (pack.rows || []).forEach(r => { if (String(r.personId) === id) r.employeeCode = employeeCode; });
  }

  function peopleSelectHtml(people, emptyLabel) {
    const list = people || [];
    const active = list.filter(p => !p.retired);
    const retired = list.filter(p => p.retired);
    let html = emptyLabel != null ? `<option value="">${emptyLabel}</option>` : '';
    if (active.length) {
      html += `<optgroup label="在職">${active.map(personOptionHtml).join('')}</optgroup>`;
    }
    if (retired.length) {
      html += `<optgroup label="退職者">${retired.map(personOptionHtml).join('')}</optgroup>`;
    }
    if (!active.length && !retired.length && emptyLabel == null) {
      html = '<option value="">（対象者なし）</option>';
    }
    return html;
  }

  function errorBlockHtml(err, compact) {
    const text = (err || '').trim();
    if (!text) return '';
    if (!compact && text.length < 80 && !text.includes('\n')) {
      return `<div class="kintai-errdetail">${esc(text)}</div>`;
    }
    const preview = text.split('\n')[0].slice(0, 72);
    return `<details class="kintai-err-expand"${compact ? '' : ' open'}>
      <summary>${esc(preview)}${text.length > preview.length ? '…' : ''}（全文）</summary>
      <pre class="kintai-err-full">${esc(text)}</pre>
    </details>`;
  }

  function dayText(v) {
    if (v == null || v === '') return '';
    const n = Number(v);
    if (Number.isNaN(n)) return '';
    return n.toFixed(1);
  }

  function sheetCell(text) {
    if (text == null || text === '') return '<td class="kintai-blank"></td>';
    return `<td>${esc(String(text))}</td>`;
  }

  function blankCells(n) {
    return '<td class="kintai-blank"></td>'.repeat(n);
  }

  function fileInputUnsaved() {
    const input = $('#kintaifileinput');
    if (!input || !input.dataset.touched) return false;
    const filePerson = $('#kintaifileperson');
    const p = pack && (pack.people || []).find(x => String(x.id) === String(filePerson && filePerson.value));
    const saved = (p && p.sourceFile) || '';
    return input.value.trim() !== saved;
  }

  function paintShareStatus() {
    const share = $('#kintaishare');
    if (share) share.textContent = '';
  }

  function visiblePeople() {
    return ((pack && pack.people) || []).filter(p => passesPeopleFilter(p.retired));
  }

  async function loadKintai() {
    if (!canKintai()) return;
    const year = rememberYear(fiscalStartYear());
    const qs = new URLSearchParams({ year: String(year) });
    let pid = pinnedPersonId || selectedPersonId();
    if (!pid) {
      pack = await api('/api/kintai?' + qs.toString());
      const first = visiblePeople()[0];
      pid = first ? first.id : null;
    }
    if (pid) {
      pinnedPersonId = pid;
      qs.set('personId', String(pid));
    }
    pack = await api('/api/kintai?' + qs.toString());
    paintKintai();
  }

  function paintFilters() {
    if (!pack) return;
    if (!chosenYear && pack.fiscalStartYear) chosenYear = Number(pack.fiscalStartYear);
    fillAllYearSelects(pack);
    const filterSel = $('#kintaifilter');
    if (filterSel && !filterSel.dataset.bound) {
      filterSel.value = peopleFilter;
    }
    const personSel = $('#kintaiperson');
    if (personSel) {
      const keep = pinnedPersonId || personSel.value;
      const people = visiblePeople();
      personSel.innerHTML = peopleSelectHtml(people, null);
      if (keep && [...personSel.options].some(o => o.value === String(keep))) personSel.value = String(keep);
      else if (personSel.options[0]) personSel.value = personSel.options[0].value;
      if (personSel.value) pinnedPersonId = Number(personSel.value);
    }
    const dailyPerson = $('#kintaidailyperson');
    if (dailyPerson) {
      const keep = pinnedPersonId || dailyPerson.value;
      const people = visiblePeople();
      dailyPerson.innerHTML = peopleSelectHtml(people, null);
      if (keep && [...dailyPerson.options].some(o => o.value === String(keep))) dailyPerson.value = String(keep);
    }
    const monthSel = $('#kintaidailymonth');
    if (monthSel) {
      const keep = dailyYm || monthSel.value;
      monthSel.innerHTML = (pack.months || []).map(m =>
        `<option value="${esc(m.key)}">${m.year}年${m.month}月</option>`
      ).join('');
      if (keep && [...monthSel.options].some(o => o.value === keep)) monthSel.value = keep;
      else if (monthSel.options[0]) monthSel.value = monthSel.options[0].value;
      dailyYm = monthSel.value || dailyYm;
    }
    const filePerson = $('#kintaifileperson');
    if (filePerson) {
      const keep = filePerson.value;
      filePerson.innerHTML = peopleSelectHtml(pack.people || [], null);
      if (keep && [...filePerson.options].some(o => o.value === keep)) filePerson.value = keep;
      syncFileInputFromPerson();
    }
    const pathsStatus = $('#kintaipathsstatus');
    if (pathsStatus && !pathsStatus.dataset.locked) {
      pathsStatus.textContent = pack.pathsListSaved
        ? '出勤簿パス一覧: このPCに保存済み（再読込可）'
        : '出勤簿パス一覧: 未取込';
    }
    paintShareStatus();
    const note = $('#kintainote');
    if (note) note.textContent = pack.opsNote || '';
    const layout = $('#kintailayout');
    if (layout) layout.textContent = `レイアウト: ${pack.layoutId || '—'}${pack.layoutNote ? ' — ' + pack.layoutNote : ''}`;
    const last = $('#kintailast');
    if (last) {
      last.textContent = pack.lastImport
        ? `最終取込: ${pack.lastImport.at}（${pack.lastImport.actor}）／${pack.lastImport.fiscal_label}`
        : '最終取込: まだありません';
    }
    const settings = document.querySelector('.kintaisettings');
    if (settings) settings.hidden = !canManagePaths();
    if (lastImportSummary) paintImportResult(lastImportSummary);
  }

  function syncFileInputFromPerson() {
    const filePerson = $('#kintaifileperson');
    const fileInput = $('#kintaifileinput');
    if (!filePerson || !fileInput || !pack) return;
    const p = (pack.people || []).find(x => String(x.id) === String(filePerson.value));
    if (!fileInput.dataset.touched) fileInput.value = (p && p.sourceFile) || '';
  }

  function paintImportResult(summary) {
    const box = $('#kintaiimportresult');
    if (!box) return;
    if (!summary || !summary.people) {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }
    try { sessionStorage.setItem('kintaiLastImportSummary', JSON.stringify(summary)); } catch (e) { /* ignore */ }
    const counts = summary.counts || {
      ok: summary.people.filter(p => p.status === '成功').length,
      error: summary.people.filter(p => p.status === 'エラー').length,
      missing: summary.people.filter(p => p.status === '未取得').length,
    };
    const rows = summary.people.map(p => {
      const kind = p.errorKind ? `（${esc(p.errorKind)}）` : '';
      const path = p.sourcePath || p.configuredPath || '';
      const layout = p.layoutId ? `<div class="kintai-errdetail">レイアウト: ${esc(p.layoutId)}</div>` : '';
      const emp = p.employment ? `<div class="kintai-errdetail">雇用区分: ${esc(EMP[p.employment] || p.employment)}</div>` : '';
      const note = p.resolvedNote ? `<div class="kintai-errdetail">照合: ${esc(p.resolvedNote)}</div>` : '';
      const cands = (p.candidates && p.candidates.length)
        ? `<div class="kintai-errdetail">近い候補: ${p.candidates.map(esc).join(' ／ ')}</div>`
        : '';
      const err = errorBlockHtml(p.error, false);
      const pathLine = path
        ? `<div class="kintai-errdetail">パス: ${esc(path)}</div>`
        : '';
      return `<tr class="${p.status === '成功' ? 'kintai-ok' : (p.status === 'エラー' ? 'kintai-ng' : 'kintai-miss')}">
        <td>${esc(p.displayName)}<br><small>${esc(p.employeeCode)}</small></td>
        <td><strong>${esc(p.status)}</strong>${kind}${err}${layout}${emp}${pathLine}${note}${cands}</td>
      </tr>`;
    }).join('');
    box.hidden = false;
    box.innerHTML = `<h3>取込結果の詳細</h3>
      <p>成功${counts.ok}／エラー${counts.error}／未取得${counts.missing}
      ${summary.shareRoot ? ` ／ 補助ルート: <code>${esc(summary.shareRoot)}</code>` : ''}
      ／ 理由は下の「全文」を開いて確認（長いパスより先に原因を表示）</p>
      <div class="kintai-result-scroll">
      <table class="kintai-result-table"><thead><tr><th>対象者</th><th>結果・理由</th></tr></thead><tbody>${rows}</tbody></table>
      </div>`;
  }

  function personSheetHtml(p) {
    const head = PERSON_COLUMNS.map(name => `<th>${esc(name)}</th>`).join('');
    const rows = ((p && p.months) || []).map(m => {
      const label = `${m.year}/${String(m.month).padStart(2, '0')}`;
      return `<tr>
        <td class="kintai-month"><button type="button" class="kintai-daylink" data-year="${m.year}" data-month="${m.month}">${esc(label)}</button></td>
        ${sheetCell(dayText(m.workDays))}
        ${sheetCell(m.holidayDays == null ? (m.workDays != null ? '未確定' : '') : dayText(m.holidayDays))}
        ${sheetCell(dayText(m.holidayCumulative))}
        ${m.holidayRemaining == null
          ? `<td class="kintai-blank" title="${esc(m.holidayAllowanceNote || '')}">${esc(m.holidayAllowanceNote || '')}</td>`
          : sheetCell(dayText(m.holidayRemaining))}
        ${sheetCell(dayText(m.paidLeaveDays))}
        ${sheetCell(dayText(m.paidLeaveCumulative))}
        ${m.paidLeaveShowsRead
          ? `<td title="${esc(m.paidLeaveRemainingNote || 'この取得日に読み取った現保有数です。')}">${esc(dayText(m.paidLeaveRemaining))}</td>`/*
          : '<td class="kintai-blank"></td>'}
        ${m.paidLeaveShowsRead

            ? `<td class="kintai-blank" title="${esc(m.paidLeaveComputedNote || '')}"></td>`
          : `<td title="${esc(m.paidLeaveComputedNote || '')}">${esc(dayText(m.paidLeaveComputedRemaining))}</td>`) : ''}
          : '<td class="kintai-blank"></td>'*/
          : '<td class="kintai-blank"></td>'}
        ${sheetCell(m.paidLeaveBaseDate || '')}
        ${sheetCell(m.ordinaryHm)}
        ${sheetCell(m.overtimeHm)}
        ${sheetCell(m.totalHm)}
        ${blankCells(6)}
      </tr>`;
    }).join('');
    return `<table class="kintai-sheet kintai-person-sheet"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
  }

  function paintKintai() {
    if (!$('#kintaidesk')) return;
    paintFilters();
    const body = $('#kintaitable');
    const status = $('#kintaistatus');
    if (!pack) {
      if (body) body.innerHTML = '';
      return;
    }
    const p = (pack.rows || []).find(r => r.months) || null;
    if (!p) {
      if (status) status.textContent = '表示する対象者がいません。';
      if (body) body.innerHTML = '';
      return;
    }
    const empLabel = p.retired ? '退職' : (EMP[p.employment] || p.employment);
    if (status) {
      const hold = (p.months || [])[0] || {};
      const readText = hold.paidLeaveReadBalance == null
        ? ''
        : `／取得した現保有数 ${dayText(hold.paidLeaveReadBalance)}（取得日 ${hold.paidLeaveAcquiredOn || ''}）`;
      const computedText = hold.paidLeaveReadBalance == null
        ? ''
        : (hold.paidLeaveComputedRemaining == null
          ? `／計算残数は空欄${hold.paidLeaveComputedNote ? '：' + hold.paidLeaveComputedNote : ''}`
          : `／計算残数 ${dayText(hold.paidLeaveComputedRemaining)}（終わった締め期間だけ。日ごとには未対応）`);
      status.textContent = `${p.displayName}（${p.employeeCode}・${empLabel}）／${pack.fiscalLabel || ''}／取込: ${p.lastStatus || '未取得'}${readText}`;
      if (p.lastError) status.innerHTML = `${esc(status.textContent)}${errorBlockHtml(p.lastError, false)}`;
    }
    if (body) body.innerHTML = personSheetHtml(p);
    const roster = $('#kintairoster');
    if (roster && !roster.hidden) loadKintaiRoster().catch(e => toast(e.message));
  }

  function rosterCell(value) {
    return sheetCell(value == null || value === '' ? '' : dayText(value));
  }

  function paintRoster(data) {
    const body = $('#kintairoster');
    if (!body) return;
    const rows = ((data && data.rows) || []).filter(r => passesPeopleFilter(r.retired));
    const editable = !!(data && data.canEditEmploymentDates);
    const head = ['氏名', '在籍状況', '年間休日枠', '開始有給残', '出勤累計', '休日累計', '休日残数', '有給取得累計', '有給残数', '取得日', '入社日', '退社日', '雇用区分', '有給休暇起算日'];
    const dateLabel = { hired: '入社日', retired: '退社日', base: '有給休暇起算日' };
    const dateCell = (personId, field, value) => editable
      ? `<td><input type="date" class="kintai-roster-date" data-person="${personId}" data-field="${field}" value="${esc(value || '')}" aria-label="${dateLabel[field]}"></td>`
      : `<td>${esc(value || '')}</td>`;
    const allowanceCell = (r) => r.holidayAllowance == null
      ? `<td class="kintai-blank" title="${esc(r.holidayAllowanceNote || '')}">${esc(r.holidayAllowanceNote || '')}</td>`
      : rosterCell(r.holidayAllowance);
    const bodyRows = rows.map(r => `<tr>
      <td>${esc(r.displayName || '')}<br><small>${esc(r.employeeCode || '')}</small></td>
      <td>${r.retired ? '退職' : '在職'}</td>
      ${allowanceCell(r)}
      <td class="kintai-blank"></td>
      ${rosterCell(r.workCumulative)}
      ${rosterCell(r.holidayCumulative)}
      ${r.holidayRemaining == null ? '<td class="kintai-blank"></td>' : rosterCell(r.holidayRemaining)}
      ${rosterCell(r.paidLeaveCumulative)}
      ${r.paidLeaveRemaining == null
        ? '<td class="kintai-blank" title="管理簿から読み取った現保有数はまだありません。"></td>'
        : `<td title="管理簿から読み取った現保有数です。取得日より前の月の残数ではありません。">${esc(dayText(r.paidLeaveRemaining))}</td>`}
      <td>${esc(r.paidLeaveAcquiredOn || '')}</td>
      ${''/*
        ? `<td class="kintai-blank" title="${esc(r.paidLeaveComputedNote || '')}"></td>`
        : `<td title="${esc(r.paidLeaveComputedNote || '')}">${esc(dayText(r.paidLeaveComputedRemaining))}</td>`*/}
      ${dateCell(r.personId, 'hired', r.hiredOn)}
      ${dateCell(r.personId, 'retired', r.retiredOn)}
      <td>${esc(EMP[r.employment] || r.employment || '')}</td>
      ${dateCell(r.personId, 'base', r.paidLeaveBaseDate)}
    </tr>`).join('');
    body.innerHTML = `<p class="kintaimeta">入社日・退社日・有給休暇起算日は、人ごとに1組です。年度ごとには分かれません。退社日の保存先は退職日と同じです。有給休暇起算日は表示するだけで、付与や失効、残数の計算には使いません。年間休日枠は、正社員と契約社員の2月1日〜翌年1月31日の在籍日数で、110日×在籍日数÷365を1日単位で切り上げます。休日残数は、その枠から2月度以降の休日累計（各月は16日〜15日）を引いた数です。有給残数は、管理簿から読み取った現保有数です。取得日より前の月の残数としては出しません。計算残数は、その値から、取得日より後に終わり切った締め期間の有給取得だけを引いた数です。取得日を含む期間は日に分けられないので、そこに有給取得があると計算残数は空欄です。まだ終わっていない月は、取込済みでも先に引きません。日ごとの減算には未対応です。</p>
      ${editable ? '<p class="kintai-workdays-bar"><button type="button" id="kintairosterdatesave">入社日・退社日・有給休暇起算日を保存</button></p>' : ''}
      <table class="kintai-sheet kintai-person-sheet"><thead><tr>${head.map(name => `<th>${esc(name)}</th>`).join('')}</tr></thead><tbody>${bodyRows}</tbody></table>`;
  }

  async function saveRosterDates() {
    if (!canKintai()) throw Error('マーク管理の権限がありません。');
    const people = [];
    const valueOf = (personId, field) => {
      const el = document.querySelector(`#kintairoster .kintai-roster-date[data-field="${field}"][data-person="${personId}"]`);
      return el && el.value ? el.value : null;
    };
    document.querySelectorAll('#kintairoster .kintai-roster-date[data-field="hired"]').forEach(el => {
      const personId = Number(el.dataset.person);
      people.push({
        personId,
        hiredOn: el.value || null,
        retiredOn: valueOf(personId, 'retired'),
        paidLeaveBaseOn: valueOf(personId, 'base'),
      });
    });
    const data = await api('/api/kintai/roster/employment', { year: rememberYear(fiscalStartYear()), people });
    paintRoster(data);
    toast('入社日・退社日・有給休暇起算日を保存しました。');
  }

  let rosterLoad = 0;

  async function loadKintaiRoster() {
    if (!canKintai()) return;
    const token = ++rosterLoad;
    const year = rememberYear(fiscalStartYear());
    const roster = $('#kintairoster');
    try {
      const data = await api('/api/kintai/roster?year=' + encodeURIComponent(year));
      if (token !== rosterLoad || !roster || roster.hidden) return;
      paintRoster(data);
    } catch (err) {
      if (token === rosterLoad && roster && !roster.hidden) {
        roster.innerHTML = '<p class="kintaimeta" role="status">読み込めませんでした。</p>';
      }
      throw err;
    }
  }

  function summaryRow(label, item, total) {
    return `<tr class="${total ? 'kintai-total' : ''}">
      <td class="kintai-month">${esc(label)}</td>
      ${sheetCell(item && item.ordinaryHm)}
      ${sheetCell(item && item.overtimeHm)}
      ${sheetCell(item && item.totalHm)}
      ${blankCells(3)}
    </tr>`;
  }

  function paintSummary(data) {
    fillAllYearSelects(data || pack);
    const status = $('#kintaisummarystatus');
    const body = $('#kintaisummarytable');
    if (status) {
      status.textContent = data && data.fiscalLabel ? `${data.fiscalLabel}　全員の合計` : '';
    }
    if (!body) return;
    const head = SUMMARY_COLUMNS.map(name => `<th>${esc(name)}</th>`).join('');
    const months = (data && data.months) || [];
    const rows = months.map(m => summaryRow(m.key || `${m.year}/${String(m.month).padStart(2, '0')}`, m, false)).join('');
    const period = (data && data.period) || {};
    body.innerHTML = `<table class="kintai-sheet kintai-summary-sheet"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody><tfoot>${summaryRow('期間合計', period, true)}</tfoot></table>`;
  }

  async function loadKintaiSummary() {
    if (!canKintai()) return;
    const year = rememberYear(fiscalStartYear());
    const data = await api('/api/kintai/month-summary?year=' + encodeURIComponent(year));
    if (data && data.fiscalStartYear) rememberYear(data.fiscalStartYear);
    paintSummary(data);
    try { await loadExecReport(); } catch (e) { paintExecReport(null); }
  }

  let execReportData = null;

  function defaultExecYm() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  }

  function currentExecYm() {
    const el = $('#kintaiexecmonth');
    if (el && /^\d{4}-\d{2}$/.test(el.value)) return el.value;
    const ym = defaultExecYm();
    if (el) el.value = ym;
    return ym;
  }

  function renderExecContractRows(tbodyId, items, kind) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return;
    const list = (items && items.length) ? items : Array.from({ length: 6 }, () => ({ customer: '', period: '', note: '' }));
    tbody.innerHTML = list.map((it, idx) => `<tr>
      <td class="kintai-month">${idx + 1}</td>
      <td><input type="text" class="kintai-exec-input" data-kind="${kind}" data-idx="${idx}" data-field="customer" maxlength="120" value="${esc((it && it.customer) || '')}" placeholder="顧客名"></td>
      <td><input type="text" class="kintai-exec-input" data-kind="${kind}" data-idx="${idx}" data-field="period" maxlength="120" value="${esc((it && it.period) || '')}" placeholder="例: 11/1〜11/15"></td>
      <td><input type="text" class="kintai-exec-input" data-kind="${kind}" data-idx="${idx}" data-field="note" maxlength="200" value="${esc((it && it.note) || '')}" placeholder="備考"></td>
    </tr>`).join('');
  }

  function collectExecSide(tbodyId) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return [];
    return [...tbody.querySelectorAll('tr')].map(tr => {
      const get = (f) => {
        const el = tr.querySelector(`input[data-field="${f}"]`);
        return el ? el.value.trim() : '';
      };
      return { customer: get('customer'), period: get('period'), note: get('note') };
    });
  }

  function paintExecReport(data) {
    execReportData = data || null;
    const el = $('#kintaiexecmonth');
    if (el && data && data.year && data.month) {
      el.value = `${data.year}-${String(data.month).padStart(2, '0')}`;
    }
    const slotCount = Math.max(
      6,
      (data && data.newContracts && data.newContracts.length) || 0,
      (data && data.renewalContracts && data.renewalContracts.length) || 0
    );
    const padSlots = (arr) => {
      const out = (arr || []).slice();
      while (out.length < slotCount) out.push({ customer: '', period: '', note: '' });
      return out;
    };
    renderExecContractRows('kintaiexecnewbody', padSlots(data && data.newContracts), 'new');
    renderExecContractRows('kintaiexecrenbody', padSlots(data && data.renewalContracts), 'renewal');
    const st = $('#kintaiexecstatus');
    if (st) {
      st.textContent = (data && data.updatedAt)
        ? `契約枠 最終保存: ${data.updatedAt}${data.updatedBy ? '（' + data.updatedBy + '）' : ''}`
        : '契約枠: 未保存（空枠のままでも出力できます）';
    }
  }

  async function loadExecReport(ymOverride) {
    if (!canKintai()) return;
    const el = $('#kintaiexecmonth');
    if (ymOverride && el) el.value = ymOverride;
    const ym = currentExecYm();
    const fy = rememberYear(fiscalStartYear());
    const data = await api(`/api/kintai/exec-report?ym=${encodeURIComponent(ym)}&fiscalYear=${encodeURIComponent(fy)}`);
    paintExecReport(data);
    return data;
  }

  function addExecContractSlot() {
    const newList = collectExecSide('kintaiexecnewbody');
    const renList = collectExecSide('kintaiexecrenbody');
    if (newList.length >= 30) {
      toast('契約枠は最大30枠までです。');
      return;
    }
    newList.push({ customer: '', period: '', note: '' });
    renList.push({ customer: '', period: '', note: '' });
    renderExecContractRows('kintaiexecnewbody', newList, 'new');
    renderExecContractRows('kintaiexecrenbody', renList, 'renewal');
  }

  async function saveExecContracts(silent) {
    if (!canKintai()) throw Error('マーク管理の権限がありません。');
    const ym = currentExecYm();
    const parts = ym.split('-');
    const fy = rememberYear(fiscalStartYear());
    const payload = {
      year: Number(parts[0]),
      month: Number(parts[1]),
      fiscalYear: fy,
      newContracts: collectExecSide('kintaiexecnewbody'),
      renewalContracts: collectExecSide('kintaiexecrenbody'),
    };
    const data = await api('/api/kintai/exec-report', payload);
    paintExecReport(data);
    if (!silent) toast(`${payload.year}年${payload.month}月のユニフォーム契約枠を保存しました。`);
    return data;
  }

  async function exportExecReportXlsx() {
    await saveExecContracts(true);
    const ym = currentExecYm();
    const fy = rememberYear(fiscalStartYear());
    const res = await fetch(`/api/kintai/exec-report/xlsx?ym=${encodeURIComponent(ym)}&fiscalYear=${encodeURIComponent(fy)}`, { credentials: 'same-origin' });
    if (!res.ok) {
      let msg = 'エクセル出力に失敗しました。';
      try { const j = await res.json(); if (j && j.error) msg = j.error; } catch (_) {}
      throw Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `幹部会議報告_${ym}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast(`幹部会議報告（${ym}）をExcel出力しました。`);
  }

  function buildExecPrintHtml(data) {
    const y = data.year;
    const m = data.month;
    const summary = data.summary || {};
    const bihin = data.bihin || {};
    const newC = data.newContracts || [];
    const renC = data.renewalContracts || [];
    const fmtYen = (v) => (v === null || v === undefined || v === '') ? '' : Number(v).toLocaleString('ja-JP');
    const fmtNum = (v) => (v === null || v === undefined || v === '') ? '' : String(v);

    const sumRows = (summary.months || []).map(sm => {
      const label = sm.key || `${sm.year}/${String(sm.month).padStart(2, '0')}`;
      return `<tr>
        <td class="exec-center">${esc(label)}</td>
        <td class="exec-center">${esc(sm.ordinaryHm || '')}</td>
        <td class="exec-center">${esc(sm.overtimeHm || '')}</td>
        <td class="exec-center">${esc(sm.totalHm || '')}</td>
        <td></td><td></td><td></td>
      </tr>`;
    }).join('');
    const p = summary.period || {};
    const sumTotal = `<tr class="exec-total">
      <td class="exec-center">期間合計</td>
      <td class="exec-center">${esc(p.ordinaryHm || '')}</td>
      <td class="exec-center">${esc(p.overtimeHm || '')}</td>
      <td class="exec-center">${esc(p.totalHm || '')}</td>
      <td></td><td></td><td></td>
    </tr>`;

    const contractTable = (title, list) => {
      const rows = list.map((it, i) => `<tr>
        <td class="exec-center">${i + 1}</td>
        <td>${esc(it.customer || '')}</td>
        <td>${esc(it.period || '')}</td>
        <td>${esc(it.note || '')}</td>
      </tr>`).join('');
      return `<div>
        <table>
          <thead>
            <tr><th colspan="4">${esc(title)}</th></tr>
            <tr><th style="width:2.4em">No.</th><th>顧客名</th><th style="width:28%">作業期間</th><th style="width:24%">備考</th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
    };

    const bRows = (bihin.rows || []).map(r => {
      const uQty = r.usageIncomplete ? '—' : fmtNum(r.usageQty);
      const uAmt = r.usageIncomplete ? '—' : fmtYen(r.usageAmount);
      return `<tr>
        <td>${esc(r.category || '')}</td>
        <td>${esc(r.name || '')}</td>
        <td class="exec-center">${esc(r.unit || '')}</td>
        <td class="exec-num">${esc(fmtNum(r.unitPrice))}</td>
        <td class="exec-num">${esc(fmtNum(r.openingQty))}</td>
        <td class="exec-num">${esc(fmtYen(r.openingAmount))}</td>
        <td class="exec-num">${esc(fmtNum(r.purchaseQty))}</td>
        <td class="exec-num">${esc(fmtYen(r.purchaseAmount))}</td>
        <td class="exec-num">${esc(fmtNum(r.closingQty))}</td>
        <td class="exec-num">${esc(fmtYen(r.closingAmount))}</td>
        <td class="exec-num">${esc(uQty)}</td>
        <td class="exec-num">${esc(uAmt)}</td>
      </tr>`;
    }).join('');
    const bt = bihin.totals || {};
    const bTotal = `<tr class="exec-total">
      <td colspan="4" class="exec-center">合計</td>
      <td></td><td class="exec-num">${esc(fmtYen(bt.openingAmount))}</td>
      <td></td><td class="exec-num">${esc(fmtYen(bt.purchaseAmount))}</td>
      <td></td><td class="exec-num">${esc(fmtYen(bt.closingAmount))}</td>
      <td></td><td class="exec-num">${esc(fmtYen(bt.usageAmount))}</td>
    </tr>`;

    return `<div class="exec-report-print">
      <h2>幹部会議報告書　${y}年${m}月度</h2>
      <h3>1. 月別集計（${esc(summary.fiscalLabel || '')}　全員の合計）</h3>
      <table>
        <thead><tr><th>月</th><th>普通勤務時間</th><th>残業時間</th><th>勤務時間計</th><th>通常勤務給与</th><th>残業給与</th><th>給与計</th></tr></thead>
        <tbody>${sumRows}${sumTotal}</tbody>
      </table>
      <h3>2. ユニフォーム契約報告（${y}年${m}月度）</h3>
      <div class="exec-contracts-grid">
        ${contractTable('【新規分】', newC)}
        ${contractTable('【入替分】', renC)}
      </div>
      <h3>3. 備品棚卸（${y}年${m}月度）</h3>
      <table>
        <thead>
          <tr>
            <th rowspan="2">科目</th><th rowspan="2">品名</th><th rowspan="2">単位</th><th rowspan="2">単価</th>
            <th colspan="2">月初在庫</th><th colspan="2">当月購入</th><th colspan="2">月末在庫</th><th colspan="2">当月使用</th>
          </tr>
          <tr>
            <th>数量</th><th>金額</th><th>数量</th><th>金額</th><th>数量</th><th>金額</th><th>数量</th><th>金額</th>
          </tr>
        </thead>
        <tbody>${bRows}${bTotal}</tbody>
      </table>
    </div>`;
  }

  async function printExecReportPdf() {
    const data = await saveExecContracts(true);
    const sheet = $('#bihincountsheet');
    if (!sheet) return;
    sheet.innerHTML = buildExecPrintHtml(data);
    document.body.classList.add('printing-bihin-count');
    let styleEl = document.getElementById('bihin-count-print-page-css');
    if (!styleEl) {
      styleEl = document.createElement('style');
      styleEl.id = 'bihin-count-print-page-css';
      document.head.appendChild(styleEl);
    }
    styleEl.textContent = '@page{size:A4 portrait;margin:10mm}';
    let cleaned = false;
    const done = () => {
      if (cleaned) return;
      cleaned = true;
      const el = document.getElementById('bihin-count-print-page-css');
      if (el) el.remove();
      document.body.classList.remove('printing-bihin-count');
      window.removeEventListener('afterprint', done);
    };
    window.addEventListener('afterprint', done);
    setTimeout(() => window.print(), 50);
  }

  window.openExecReport = async function (ym) {
    if (typeof showKintaiSection === 'function') showKintaiSection('summary');
    await loadExecReport(ym);
    const panel = $('#kintaiexecpanel');
    if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  function paintPathsImportResult(summary) {
    const box = $('#kintaiimportresult');
    const status = $('#kintaipathsstatus');
    if (status) {
      status.dataset.locked = '1';
      const c = summary.counts || {};
      status.textContent = `パス一覧取込: 更新${c.updated || 0}／追加${c.created || 0}／未照合${c.unmatched || 0}／一覧外保持${c.keptUnlisted || 0}`
        + (summary.reloaded ? '（再読込）' : '');
    }
    if (!box) return;
    const rows = (summary.people || []).map(p => `<tr class="${p.status === '未照合' || p.status === 'エラー' ? 'kintai-ng' : 'kintai-ok'}">
      <td>${esc(p.displayName)}<br><small>${esc(p.employeeCode || '')}${p.retired ? '・退職' : ''}</small></td>
      <td><strong>${esc(p.status)}</strong><div class="kintai-errdetail">${esc(p.detail || '')}</div>
      ${p.path ? `<div class="kintai-errdetail">パス: ${esc(p.path)}</div>` : ''}</td>
    </tr>`).join('');
    const kept = (summary.keptUnlisted || []).map(p =>
      `${esc(p.displayName)}（${esc(p.employeeCode)}）`
    ).join(' ／ ');
    box.hidden = false;
    box.innerHTML = `<h3>出勤簿パス一覧の取込結果</h3>
      <p>一覧に無い対象者は変更していません${kept ? `: ${kept}` : ''}。</p>
      <table class="kintai-result-table"><thead><tr><th>氏名</th><th>結果</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function importPathsFile(file) {
    if (!canManagePaths()) throw Error('出勤簿パス一覧の取込は利用者管理の権限が必要です。');
    if (!file) return;
    if (!/\.xlsx$/i.test(file.name) || file.name.startsWith('~$')) {
      throw Error('Excelファイル（.xlsx）を選んでください。');
    }
    if (file.size > 2000000) throw Error('ファイルが大きすぎます。');
    if (!confirm('出勤簿パス一覧を取り込みます。氏名に対応するフルパスを保存し、退職者を分けます。一覧に無い人は消しません。よろしいですか？')) return;
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    const summary = await api('/api/kintai/paths-import', { filename: file.name, data: btoa(binary) });
    paintPathsImportResult(summary);
    toast(`パス一覧を取り込みました（更新${summary.counts.updated}／追加${summary.counts.created}）`);
    await loadKintai();
  }

  async function reloadPathsFile() {
    if (!canManagePaths()) throw Error('出勤簿パス一覧の再読込は利用者管理の権限が必要です。');
    if (!confirm('保存済みの出勤簿パス一覧を再読込します。よろしいですか？')) return;
    const summary = await api('/api/kintai/paths-import', { reload: true });
    paintPathsImportResult(summary);
    toast(`パス一覧を再読込しました（更新${summary.counts.updated}／追加${summary.counts.created}）`);
    await loadKintai();
  }

  async function importKintai() {
    if (!canKintai()) throw Error('マーク管理の権限がありません。');
    if (fileInputUnsaved()) {
      const go = confirm('読取元ファイルの入力が未保存です。未保存のパスは取込に使われません。このまま続けますか？');
      if (!go) return;
    }
    if (!confirm('出勤簿から集計を更新します。元ファイルは変更しません。よろしいですか？')) return;
    const summary = await api('/api/kintai/import', { year: fiscalStartYear() });
    lastImportSummary = summary;
    (summary.people || []).forEach(p => applyPersonCode(p.personId, p.employeeCode));
    const counts = summary.counts || {};
    const ok = counts.ok != null ? counts.ok : (summary.people || []).filter(p => p.status === '成功').length;
    const err = counts.error != null ? counts.error : (summary.people || []).filter(p => p.status === 'エラー').length;
    const miss = counts.missing != null ? counts.missing : (summary.people || []).filter(p => p.status === '未取得').length;
    toast(`取込完了: 成功${ok}／エラー${err}／未取得${miss}（詳細は画面の「取込結果の詳細」）`);
    paintImportResult(summary);
    paintKintai();
    await loadKintai();
  }

  async function savePersonFile() {
    if (!canManagePaths()) throw Error('読取元設定は利用者管理の権限が必要です。');
    const personEl = $('#kintaifileperson');
    const fileEl = $('#kintaifileinput');
    const personId = personEl ? Number(personEl.value) : 0;
    const sourceFile = fileEl ? fileEl.value.trim() : '';
    if (!personId) throw Error('対象者を選んでください。');
    // レイアウトは雇用区分で自動選択（layoutId は送らない＝既存の個別指定は保持）
    const body = { personId, sourceFile };
    const saved = await api('/api/kintai/person-file', body);
    if (fileEl) delete fileEl.dataset.touched;
    const person = pack && (pack.people || []).find(x => String(x.id) === String(personId));
    const previousCode = person && person.employeeCode;
    if (person) person.sourceFile = sourceFile;
    if (saved && saved.employeeCode) applyPersonCode(personId, saved.employeeCode);
    paintKintai();
    if (saved && saved.employeeCode && previousCode && previousCode !== saved.employeeCode) {
      toast(`読取元を保存しました。社員番号を ${saved.employeeCode} に更新しました。`);
    } else {
      toast('読取元を保存しました。');
    }
    await loadKintai();
  }

  function paintDaily(data) {
    const status = $('#kintaidailystatus');
    const body = $('#kintaidailytable');
    const cols = (data && data.columns) || [];
    if (status) {
      const who = data && data.displayName ? `${data.displayName}（${data.employeeCode || ''}）` : '';
      const sheet = data && data.sheetName ? `／シート ${data.sheetName}` : '';
      const file = data && data.fileName ? `／${data.fileName}` : '';
      const n = data && data.rows ? data.rows.length : 0;
      const period = data && data.periodStart && data.periodEnd ? `／${data.periodStart}～${data.periodEnd}` : '';
      status.textContent = `${who}${sheet}${file}${period}／${n}日`;
    }
    if (!body) return;
    if (!cols.length) {
      body.innerHTML = '';
      return;
    }
    const tip = (c) => {
      if (c.missing) return `${c.label} ${c.missing}`;
      if (c.sourceLabel && c.sourceLabel !== c.label) return `${c.label}（${c.sourceLabel}）`;
      return c.label;
    };
    const dayCols = cols.filter(c => c.key !== 'total');
    const editable = !!(data && data.canEditWorkDays);
    lastDaily = data || null;
    const workCell = (r) => {
      const value = r.workDays == null ? '' : String(r.workDays);
      const initial = r.workDaysDefault == null ? '' : String(r.workDaysDefault);
      const date = r.date || '';
      if (!editable) return `<td class="kintai-workdays-cell">${esc(value)}</td>`;
      return `<td class="kintai-workdays-cell"><input class="kintai-workdays" type="number" inputmode="decimal" min="0" max="2" step="0.01" aria-label="${esc(date)}の出勤日数" data-date="${esc(date)}" data-default="${esc(initial)}" data-saved="${r.workDaysSaved ? '1' : '0'}" value="${esc(value)}"></td>`;
    };
    const head = dayCols.map((c, i) => `<th scope="col" title="${esc(tip(c))}"${i === 0 ? ' class="kintai-daily-sticky"' : ''}>${esc(c.label)}</th>`).join('') + '<th scope="col">出勤日数</th>';
    const rows = ((data && data.rows) || []).map(r => `<tr>${dayCols.map((c, i) => {
      const text = r[c.key] == null ? '' : String(r[c.key]);
      if (i === 0) return `<th scope="row" class="kintai-daily-sticky kintai-month" title="${esc(tip(c))}">${esc(text)}</th>`;
      return `<td title="${esc(tip(c))}">${esc(text)}</td>`;
    }).join('')}${workCell(r)}</tr>`).join('');
    const totals = (data && data.totals) || [];
    const totalsNote = data && data.totalsNote ? data.totalsNote : '';
    const totalsHtml = totals.length
      ? `<h3 class="kintai-section">合計</h3><p class="kintaimeta">この月シートの集計名と数値です。日付の行とは別で、元の順のままです。${totals.length}件</p><table class="kintai-sheet kintai-daily-sheet kintai-daily-totals"><thead><tr><th scope="col">項目</th><th scope="col">数値</th></tr></thead><tbody>${totals.map(t => `<tr><td class="kintai-daily-total-label">${esc(t.label || '')}</td><td class="kintai-daily-total-value">${esc(t.value || '')}</td></tr>`).join('')}</tbody></table>`
      : (totalsNote ? `<h3 class="kintai-section">合計</h3><p class="kintaimeta">${esc(totalsNote)}</p>` : '');
    const daysHtml = rows
      ? `<table class="kintai-sheet kintai-daily-sheet"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`
      : `<p class="kintaimeta">${data && data.periodStart ? `${esc(data.periodStart)}～${esc(data.periodEnd)}の` : ''}この月シートに対象の日付がありません。</p>`;
    const totalText = data && data.workDaysTotal != null
      ? (function () {
        let h = data.holidayDaysTotal;
        if (h == null && Array.isArray(data.rows)) {
          let zeroCount = 0, nonzeroCount = 0;
          data.rows.forEach(r => {
            if (r.workDays == null || r.workDays === '') return;
            const n = Number(r.workDays);
            if (!Number.isNaN(n)) { if (n === 0) zeroCount += 1; else nonzeroCount += 1; }
          });
          h = zeroCount + (nonzeroCount - Number(data.workDaysTotal));
        }
        const p = data.paidLeaveDaysTotal;
        const hm = data.totalHm ? ` ／ 普通勤務時間 ${data.ordinaryHm} ／ 残業時間 ${data.overtimeHm} ／ 勤務時間計 ${data.totalHm}` : '';
        return `出勤日数の合計 ${dayText(data.workDaysTotal)}${h != null ? ` ／ 休日数の合計 ${dayText(h)}` : ''}${p != null ? ` ／ 有休の合計 ${dayText(p)}` : ''}${hm}（この月の16日〜15日）`;
      })()
      : '';
    const saveBar = rows
      ? `<p class="kintai-workdays-bar">${editable ? '<button type="button" id="kintaiworkdayssave">出勤日数を保存</button> <button type="button" class="primary" id="kintaipaidleavesave">有休の合計を出勤簿に反映</button>' : ''}<span class="kintaimeta">${esc(totalText)}</span></p>`
      : '';
    const actuals = (data && data.actuals) || [];
    const actualsHtml = actuals.length
      ? `<section><h3 class="kintai-section">実績</h3><p class="kintaimeta">元のExcelに保存されている実績値です。</p><table class="kintai-sheet kintai-daily-sheet kintai-daily-totals"><thead><tr><th scope="col">項目</th><th scope="col">時間</th></tr></thead><tbody>${actuals.map(t => `<tr><td class="kintai-daily-total-label">${esc(t.label || '')}</td><td class="kintai-daily-total-value">${esc(t.value ?? '')}</td></tr>`).join('')}</tbody></table></section>`
      : '';
    body.innerHTML = daysHtml + saveBar + `<div class="kintai-daily-summaries"><section>${totalsHtml}</section>${actualsHtml}</div>`;
  }

  async function saveDailyWorkDays() {
    if (!canKintai()) throw Error('マーク管理の権限がありません。');
    if (!lastDaily) throw Error('日別を表示してから保存してください。');
    const inputs = [...document.querySelectorAll('#kintaidailytable .kintai-workdays')];
    const days = [];
    inputs.forEach(el => {
      const typed = el.value.trim();
      const initial = el.dataset.default || '';
      const wasSaved = el.dataset.saved === '1';
      if (typed !== '' && !Number.isFinite(Number(typed))) throw Error('出勤日数は数値で入れてください。');
      const same = (typed === '' && initial === '') || (typed !== '' && initial !== '' && Number(typed) === Number(initial));
      if (same) {
        if (wasSaved) days.push({ date: el.dataset.date, days: null });
        return;
      }
      days.push({ date: el.dataset.date, days: typed === '' ? null : Number(typed) });
    });
    const saved = await api('/api/kintai/daily/work-days', {
      personId: lastDaily.personId,
      year: lastDaily.year,
      month: lastDaily.month,
      days,
    });
    paintDaily(saved);
    loadKintai().catch(() => {});
    toast('出勤日数・休日数・勤務時間を保存しました。');
  }

  async function saveDailyPaidLeave() {
    if (!canKintai()) throw Error('マーク管理の権限がありません。');
    if (!lastDaily) throw Error('日別を表示してから反映してください。');
    const saved = await api('/api/kintai/daily/paid-leave', {
      personId: lastDaily.personId,
      year: lastDaily.year,
      month: lastDaily.month,
    });
    paintDaily(saved);
    loadKintai().catch(() => {});
    const v = saved.savedPaidLeaveDays != null ? saved.savedPaidLeaveDays : saved.paidLeaveDaysTotal;
    toast(`有給取得日数（${dayText(v)}日）を出勤簿に反映しました。`);
  }

  async function ensurePeoplePack() {
    const year = rememberYear(fiscalStartYear());
    if (pack && Number(pack.fiscalStartYear) === Number(year) && pack.people && pack.months) return;
    const pid = pinnedPersonId || selectedPersonId();
    const qs = new URLSearchParams({ year: String(year) });
    if (pid) qs.set('personId', String(pid));
    pack = await api('/api/kintai?' + qs.toString());
  }

  async function loadKintaiDaily() {
    if (!canKintai()) return;
    await ensurePeoplePack();
    paintFilters();
    const monthSel = $('#kintaidailymonth');
    const personSel = $('#kintaidailyperson');
    const ym = (monthSel && monthSel.value) || dailyYm;
    dailyYm = ym || '';
    const pid = (personSel && personSel.value) ? Number(personSel.value) : (pinnedPersonId || selectedPersonId());
    const status = $('#kintaidailystatus');
    const body = $('#kintaidailytable');
    if (!pid || !ym) {
      if (status) status.textContent = '人と月を選んでください。';
      if (body) body.innerHTML = '';
      return;
    }
    const parts = ym.split('-');
    const data = await api('/api/kintai/daily?personId=' + encodeURIComponent(pid) + '&year=' + encodeURIComponent(parts[0]) + '&month=' + encodeURIComponent(parts[1]));
    paintDaily(data);
  }

  function openDailyMonth(year, month) {
    if (!year || !month) return;
    dailyYm = year + '-' + String(month).padStart(2, '0');
    const pid = selectedPersonId();
    if (pid) pinnedPersonId = pid;
    if (typeof showKintaiSection === 'function') showKintaiSection('daily');
  }

  window.loadKintai = loadKintai;
  window.paintKintai = paintKintai;
  window.importKintai = importKintai;
  window.loadKintaiSummary = loadKintaiSummary;
  window.loadKintaiDaily = loadKintaiDaily;


  let leaveLoading = false;
  function paintLeaveBalances(data) {
    const body = $('#kintaileaverows');
    body.innerHTML = (data.people || []).map(p => `<tr>
      <td>${esc(p.name)}<br><small>${esc(p.employeeCode)}${p.retired ? '／退職' : ''}</small></td>
      <td><input class="kintai-leave-cell" data-person="${p.personId}" value="${esc(p.cell)}" placeholder="K3" maxlength="12" aria-label="${esc(p.name)}の集計セル"></td>
      <td>${p.balance == null ? '' : esc(p.balance) + '日'}</td>
      <td>${esc(p.note || '')}</td>
      <td>${esc(p.updatedAt ? new Date(p.updatedAt).toLocaleString('ja-JP') : '')}<br><small>${esc(p.filename || '')}</small></td>
    </tr>`).join('');
  }
  async function loadLeaveBalances() {
    paintLeaveBalances(await api('/api/kintai/leave-balance'));
  }
  async function saveLeaveBalances(action) {
    if (leaveLoading) return;
    leaveLoading = true;
    const status = $('#kintaileavestatus');
    const password = $('#kintaileavepassword');
    const applyBtn = $('#kintaileaveapply');
    $('#kintaileavesave').disabled = $('#kintaileaveupdate').disabled = true;
    if (applyBtn) applyBtn.disabled = true;
    try {
      const people = Array.from(document.querySelectorAll('.kintai-leave-cell')).map(el => ({
        personId: Number(el.dataset.person), cell: el.value.trim(),
      }));
      const payload = { action, people };
      if (action === 'update') {
        const file = $('#kintaileavefile').files[0];
        if (!file || !file.name.toLowerCase().endsWith('.xlsx') || file.size > 10 * 1024 * 1024) throw Error('10MB以下の.xlsxを選択してください。');
        payload.filename = file.name;
        payload.data = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(',')[1]);
          reader.onerror = () => reject(Error('Excelを読み込めません。'));
          reader.readAsDataURL(file);
        });
        payload.password = password.value;
      }
      status.textContent = action === 'apply' ? '現保有数を出勤簿に反映しています…' : (action === 'update' ? '集計シートを読み取っています…' : 'セル位置を保存しています…');
      const data = await api('/api/kintai/leave-balance', payload);
      paintLeaveBalances(data);
      status.textContent = action === 'apply'
        ? `読み込んだ現保有数を出勤簿の有給残数に反映しました（${data.appliedCount || 0}名）。`
        : (action === 'update' ? '現保有数を更新しました。空欄や確認事項がある行は内容を確認してください。' : 'セル位置を保存しました。変更した行の前回値は消去しました。');
      if (action === 'apply') toast('有給残数を出勤簿に反映しました。');
      await loadKintai();
      const roster = $('#kintairoster');
      if (roster && !roster.hidden) await loadKintaiRoster();
    } catch (e) {
      status.textContent = e.message || '読み取りに失敗しました。';
    } finally {
      password.value = '';
      leaveLoading = false;
      $('#kintaileavesave').disabled = $('#kintaileaveupdate').disabled = false;
      if (applyBtn) applyBtn.disabled = false;
    }
  }

  function bindKintaiControls() {
    const reportExport = $('#kintaireportexport');
    if (reportExport) reportExport.onclick = async () => {
      reportExport.disabled = true;
      try {
        const pid = selectedPersonId();
        if (!pid) throw Error('対象者を選択してください。');
        const qs = new URLSearchParams({ year: String(fiscalStartYear()), personId: String(pid) });
        const response = await fetch('/api/kintai/report/xlsx?' + qs, { credentials: 'same-origin' });
        if (!response.ok) {
          const error = await response.json();
          throw Error(error.error || 'Excel出力に失敗しました。');
        }
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        const a = document.createElement('a');
        const url = URL.createObjectURL(blob);
        a.href = url;
        a.download = match ? decodeURIComponent(match[1]) : `勤怠通知_${fiscalStartYear()}年度.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (e) { toast(e.message); }
      finally { reportExport.disabled = false; }
    };
    const year = $('#kintaiyear');
    const importYear = $('#kintaiimportyear');
    const summaryYear = $('#kintaisummaryyear');
    const dailyYear = $('#kintaidailyyear');
    const person = $('#kintaiperson');
    const dailyPerson = $('#kintaidailyperson');
    const dailyMonth = $('#kintaidailymonth');
    const refresh = $('#kintairefresh');
    const summaryRefresh = $('#kintaisummaryrefresh');
    const dailyRefresh = $('#kintaidailyrefresh');
    const imp = $('#kintaiimport');
    const saveFile = $('#kintaifilesave');
    const fileInput = $('#kintaifileinput');
    const filePerson = $('#kintaifileperson');
    const onYear = (el, load) => {
      if (!el) return;
      el.onchange = () => {
        rememberYear(el.value);
        syncYearValue(chosenYear);
        load().catch(e => toast(e.message));
      };
    };
    onYear(year, loadKintai);
    onYear(importYear, loadKintai);
    onYear(summaryYear, loadKintaiSummary);
    onYear(dailyYear, loadKintaiDaily);
    if (person) person.onchange = () => {
      pinnedPersonId = person.value ? Number(person.value) : null;
      loadKintai().catch(e => toast(e.message));
    };
    const filter = $('#kintaifilter');
    if (filter) filter.onchange = () => {
      peopleFilter = filter.value || 'all';
      const people = visiblePeople();
      const cur = person && person.value;
      if (!people.some(p => String(p.id) === String(cur))) {
        pinnedPersonId = people[0] ? people[0].id : null;
        loadKintai().catch(e => toast(e.message));
      } else paintKintai();
    };
    if (refresh) refresh.onclick = () => loadKintai().catch(e => toast(e.message));
    const leavePanel = $('#kintaileavepanel');
    if (leavePanel && !leavePanel.dataset.bound) {
      leavePanel.dataset.bound = '1';
      leavePanel.addEventListener('toggle', () => {
        if (leavePanel.open && !leavePanel.dataset.loaded) loadLeaveBalances().then(() => { leavePanel.dataset.loaded = '1'; }).catch(e => { $('#kintaileavestatus').textContent = e.message; });
      });
      $('#kintaileaverows').addEventListener('input', e => {
        if (!e.target.matches('.kintai-leave-cell')) return;
        const cells = e.target.closest('tr').cells;
        cells[2].textContent = '';
        cells[3].textContent = 'セル位置が未保存です';
      });
      $('#kintaileavesave').onclick = () => saveLeaveBalances('save');
      $('#kintaileaveupdate').onclick = () => saveLeaveBalances('update');
      if ($('#kintaileaveapply')) $('#kintaileaveapply').onclick = () => saveLeaveBalances('apply');
    }
    const rosterBtn = $('#kintairosteropen');
    if (rosterBtn) rosterBtn.onclick = () => {
      const roster = $('#kintairoster');
      if (!roster) return;
      roster.hidden = !roster.hidden;
      rosterBtn.textContent = roster.hidden ? '全員の累計' : '全員の累計を閉じる';
      if (!roster.hidden) {
        roster.innerHTML = '<p class="kintaimeta" role="status">読み込み中です。</p>';
        roster.scrollIntoView({ block: 'nearest' });
        loadKintaiRoster().catch(e => toast(e.message));
      }
    };
    const rosterBox = $('#kintairoster');
    if (rosterBox && !rosterBox.dataset.datebound) {
      rosterBox.dataset.datebound = '1';
      rosterBox.addEventListener('click', (e) => {
        if (!e.target.closest('#kintairosterdatesave')) return;
        saveRosterDates().catch(err => toast(err.message));
      });
    }
    if (summaryRefresh) summaryRefresh.onclick = () => loadKintaiSummary().catch(e => toast(e.message));
    const execMonth = $('#kintaiexecmonth');
    if (execMonth) execMonth.onchange = () => loadExecReport().catch(e => toast(e.message));
    const execAdd = $('#kintaiexecaddslot');
    if (execAdd) execAdd.onclick = () => addExecContractSlot();
    const execSave = $('#kintaiexecsave');
    if (execSave) execSave.onclick = () => saveExecContracts(false).catch(e => toast(e.message));
    const execXlsx = $('#kintaiexecxlsx');
    if (execXlsx) execXlsx.onclick = () => exportExecReportXlsx().catch(e => toast(e.message));
    const execPdf = $('#kintaiexecpdf');
    if (execPdf) execPdf.onclick = () => printExecReportPdf().catch(e => toast(e.message));
    paintExecReport(null);
    if (dailyPerson) dailyPerson.onchange = () => {
      pinnedPersonId = dailyPerson.value ? Number(dailyPerson.value) : null;
      loadKintaiDaily().catch(e => toast(e.message));
    };
    if (dailyMonth) dailyMonth.onchange = () => {
      dailyYm = dailyMonth.value || '';
      loadKintaiDaily().catch(e => toast(e.message));
    };
    if (dailyRefresh) dailyRefresh.onclick = () => loadKintaiDaily().catch(e => toast(e.message));
    const dailyPaidBtn = $('#kintaidailypaidleavesave');
    if (dailyPaidBtn) dailyPaidBtn.onclick = () => saveDailyPaidLeave().catch(e => toast(e.message));
    const dailyTable = $('#kintaidailytable');
    if (dailyTable && !dailyTable.dataset.workdaybound) {
      dailyTable.dataset.workdaybound = '1';
      dailyTable.addEventListener('click', (e) => {
        if (e.target.closest('#kintaipaidleavesave')) {
          saveDailyPaidLeave().catch(err => toast(err.message));
          return;
        }
        if (!e.target.closest('#kintaiworkdayssave')) return;
        saveDailyWorkDays().catch(err => toast(err.message));
      });
    }
    const dayTable = $('#kintaitable');
    if (dayTable && !dayTable.dataset.daybound) {
      dayTable.dataset.daybound = '1';
      dayTable.addEventListener('click', (e) => {
        const button = e.target.closest('.kintai-daylink');
        if (!button) return;
        openDailyMonth(Number(button.dataset.year), Number(button.dataset.month));
      });
    }
    if (imp) imp.onclick = () => {
      if (importYear && importYear.value) rememberYear(importYear.value);
      importKintai().catch(e => toast(e.message));
    };
    if (saveFile) saveFile.onclick = () => savePersonFile().catch(e => toast(e.message));
    const pathsImportBtn = $('#kintaipathsimport');
    const pathsReloadBtn = $('#kintaipathsreload');
    const pathsFile = $('#kintaipathsfile');
    if (pathsImportBtn && pathsFile) {
      pathsImportBtn.onclick = () => { pathsFile.value = ''; pathsFile.click(); };
      pathsFile.onchange = () => {
        const file = pathsFile.files && pathsFile.files[0];
        pathsFile.value = '';
        importPathsFile(file).catch(e => toast(e.message));
      };
    }
    if (pathsReloadBtn) pathsReloadBtn.onclick = () => reloadPathsFile().catch(e => toast(e.message));
    if (fileInput) fileInput.addEventListener('input', () => { fileInput.dataset.touched = '1'; });
    if (filePerson) filePerson.onchange = () => { if (fileInput) delete fileInput.dataset.touched; syncFileInputFromPerson(); };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindKintaiControls);
  else bindKintaiControls();
})();
