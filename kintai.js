/* マーク管理画面。権限は mark_kintai、または管理者チェック（manage/overview）。主管理者は常に可。 */
(function () {
  const EMP = { regular: '社員', contract: '契約', part: 'パート' };
  let pack = null;
  let chosenYear = 0;
  let pinnedPersonId = null;
  let dailyYm = '';
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
        ${sheetCell(dayText(m.holidayDays))}
        ${blankCells(2)}
        ${sheetCell(dayText(m.paidLeaveDays))}
        ${blankCells(3)}
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
      status.textContent = `${p.displayName}（${p.employeeCode}・${empLabel}）／${pack.fiscalLabel || ''}／取込: ${p.lastStatus || '未取得'}`;
      if (p.lastError) status.innerHTML = `${esc(status.textContent)}${errorBlockHtml(p.lastError, false)}`;
    }
    if (body) body.innerHTML = personSheetHtml(p);
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
  }

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
      status.textContent = `${who}${sheet}${file}／${n}日`;
    }
    if (!body) return;
    if (!cols.length) {
      body.innerHTML = '';
      return;
    }
    const head = cols.map(c => `<th>${esc(c.label)}</th>`).join('');
    const rows = ((data && data.rows) || []).map(r => `<tr>${cols.map(c => {
      const text = r[c.key] == null ? '' : String(r[c.key]);
      return `<td class="${c.key === 'date' ? 'kintai-month' : ''}">${esc(text)}</td>`;
    }).join('')}</tr>`).join('');
    body.innerHTML = rows
      ? `<table class="kintai-sheet kintai-daily-sheet"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`
      : '<p class="kintaimeta">この月シートに日付の行がありません。</p>';
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

  function bindKintaiControls() {
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
    if (summaryRefresh) summaryRefresh.onclick = () => loadKintaiSummary().catch(e => toast(e.message));
    if (dailyPerson) dailyPerson.onchange = () => {
      pinnedPersonId = dailyPerson.value ? Number(dailyPerson.value) : null;
      loadKintaiDaily().catch(e => toast(e.message));
    };
    if (dailyMonth) dailyMonth.onchange = () => {
      dailyYm = dailyMonth.value || '';
      loadKintaiDaily().catch(e => toast(e.message));
    };
    if (dailyRefresh) dailyRefresh.onclick = () => loadKintaiDaily().catch(e => toast(e.message));
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
