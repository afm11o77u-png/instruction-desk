/* マーク管理画面。権限は currentUser.mark_kintai（主管理者は常に可）。 */
(function () {
  const EMP = { regular: '社員', contract: '契約', part: 'パート' };
  let pack = null;
  let mode = 'month'; // month | person
  let selectedYm = '';
  let lastImportSummary = null;
  let peopleFilter = 'all'; // all | active | retired
  try {
    const raw = sessionStorage.getItem('kintaiLastImportSummary');
    if (raw) lastImportSummary = JSON.parse(raw);
  } catch (e) { lastImportSummary = null; }

  function $(sel, root) { return (root || document).querySelector(sel); }

  function fiscalStartYear() {
    const el = $('#kintaiyear');
    const n = el ? Number(el.value) : 0;
    return n || (pack && pack.preferredFiscalStartYear) || 2026;
  }

  function selectedYearMonth() {
    const el = $('#kintaimonth');
    if (el && el.value) return el.value;
    if (selectedYm) return selectedYm;
    if (pack && pack.months && pack.months[0]) return pack.months[0].key;
    return '2026-02';
  }

  function selectedPersonId() {
    const el = $('#kintaiperson');
    const v = el && el.value;
    return v ? Number(v) : null;
  }

  function num(v) {
    if (v == null || v === '') return '—';
    const n = Number(v);
    if (Number.isNaN(n)) return '—';
    return Number.isInteger(n) ? String(n) : String(n);
  }

  function canKintai() {
    return !!currentUser && (currentUser.role === 'owner' || !!currentUser.mark_kintai);
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

  function monthRowHtml(r) {
    const m = r.month || {};
    const err = r.lastError || m.error || '';
    const retired = r.retired ? '<small class="kintai-retired-tag">退職</small>' : '';
    return `<tr class="${r.retired ? 'kintai-retired-row' : ''}">
      <td><b>${esc(r.displayName)}</b>${retired}</td>
      <td>${esc(r.employeeCode)}</td>
      <td>${esc(EMP[r.employment] || r.employment)}</td>
      <td>${num(m.workDays)}</td>
      <td>${num(m.holidayDays)}</td>
      <td>${num(m.paidLeaveDays)}</td>
      <td>${esc(m.ordinaryHm || '—')}</td>
      <td>${esc(m.overtimeHm || '—')}</td>
      <td>${esc(m.totalHm || '—')}</td>
      <td><strong>${esc(r.lastStatus || m.status || '未取得')}</strong>${errorBlockHtml(err, true)}${r.lastImportAt ? `<small>${esc(r.lastImportAt)}</small>` : ''}</td>
    </tr>`;
  }

  function monthTableHtml(rows, heading) {
    if (!rows.length) return '';
    return `<h3 class="kintai-section">${esc(heading)}（${rows.length}）</h3>
      <table><thead><tr><th>氏名</th><th>社員番号</th><th>区分</th><th>出勤日数</th><th>休日数</th><th>有給取得日数</th><th>普通勤務</th><th>残業</th><th>勤務時間計</th><th>取込結果</th></tr></thead><tbody>${
        rows.map(monthRowHtml).join('')
      }</tbody></table>`;
  }

  function shareInputUnsaved() {
    const input = $('#kintaishareinput');
    if (!input || !input.dataset.touched) return false;
    const saved = (pack && pack.shareRoot) || '';
    return input.value.trim() !== saved;
  }

  function fileInputUnsaved() {
    const input = $('#kintaifileinput');
    if (!input || !input.dataset.touched) return false;
    const filePerson = $('#kintaifileperson');
    const p = pack && (pack.people || []).find(x => String(x.id) === String(filePerson && filePerson.value));
    const saved = (p && p.sourceFile) || '';
    return input.value.trim() !== saved;
  }

  function looksAbsolutePath(s) {
    const v = (s || '').trim();
    if (!v) return false;
    if (v.startsWith('\\\\') || v.startsWith('//')) return true;
    if (v.length > 2 && v[1] === ':') return true;
    if (v.startsWith('/')) return true;
    return false;
  }

  function paintShareStatus() {
    const share = $('#kintaishare');
    if (!share) return;
    const saved = (pack && pack.shareRoot) || '';
    share.classList.remove('kintai-warn');
    if (shareInputUnsaved()) {
      share.classList.add('kintai-warn');
      share.textContent = '任意の年度ルート入力が未保存です（フルパス運用なら無視可）。相対パスを使うなら「年度ルートとレイアウトを保存」を押してください';
      return;
    }
    if (saved) {
      share.textContent = '対象者ごとのフルパスが主設定。任意の年度ルートも保存済み（相対パス用）';
      return;
    }
    share.textContent = '対象者ごとに出勤簿フルパスを保存してください（年度ルートは任意）';
  }

  function syncPersonLayout() {
    const filePerson = $('#kintaifileperson');
    const layoutSel = $('#kintaipersonlayout');
    if (!filePerson || !layoutSel || !pack) return;
    const p = (pack.people || []).find(x => String(x.id) === String(filePerson.value));
    const def = pack.layoutId || 'shukkinbo-2026-seishain-v1';
    const want = (p && p.layoutId) || '';
    // 空＝既定。people の layoutId が既定と同じなら空表示
    layoutSel.value = want && want !== def ? want : (want || '');
    if (want && ![...layoutSel.options].some(o => o.value === want)) {
      layoutSel.value = '';
    }
  }

  async function loadKintai() {
    if (!canKintai()) return;
    const year = fiscalStartYear();
    const qs = new URLSearchParams({ year: String(year) });
    if (mode === 'person') {
      const pid = selectedPersonId();
      if (pid) qs.set('personId', String(pid));
    } else {
      qs.set('ym', selectedYearMonth());
    }
    pack = await api('/api/kintai?' + qs.toString());
    paintKintai();
  }

  function paintFilters() {
    if (!pack) return;
    const yearSel = $('#kintaiyear');
    if (yearSel) {
      const opts = pack.fiscalYearOptions && pack.fiscalYearOptions.length
        ? pack.fiscalYearOptions
        : [{ startYear: 2026, label: '2026年2月〜2027年1月' }];
      const want = pack.fiscalStartYear || pack.preferredFiscalStartYear || 2026;
      yearSel.innerHTML = opts.map(o =>
        `<option value="${o.startYear}" ${Number(o.startYear) === Number(want) ? 'selected' : ''}>${esc(o.label)}</option>`
      ).join('');
      if (![...yearSel.options].some(o => Number(o.value) === Number(want))) {
        yearSel.insertAdjacentHTML('afterbegin', `<option value="${want}" selected>${want}年2月〜${want + 1}年1月</option>`);
      } else {
        yearSel.value = String(want);
      }
    }
    const monthSel = $('#kintaimonth');
    if (monthSel) {
      const keep = selectedYm || selectedYearMonth();
      monthSel.innerHTML = (pack.months || []).map(m =>
        `<option value="${esc(m.key)}">${m.year}年${m.month}月</option>`
      ).join('');
      if (keep && [...monthSel.options].some(o => o.value === keep)) monthSel.value = keep;
      else if (pack.months && pack.months[0]) monthSel.value = pack.months[0].key;
      selectedYm = monthSel.value;
    }
    const filterSel = $('#kintaifilter');
    if (filterSel && !filterSel.dataset.bound) {
      filterSel.value = peopleFilter;
    }
    const personSel = $('#kintaiperson');
    if (personSel) {
      const keep = personSel.value;
      const visiblePeople = (pack.people || []).filter(p => passesPeopleFilter(p.retired));
      personSel.innerHTML = peopleSelectHtml(visiblePeople, '（月別の全員比較）');
      if (keep && [...personSel.options].some(o => o.value === keep)) personSel.value = keep;
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
    const shareInput = $('#kintaishareinput');
    if (shareInput && !shareInput.dataset.touched) {
      shareInput.value = pack.shareRoot || '';
      shareInput.placeholder = pack.exampleYearShareRoot || pack.exampleKatoShareRoot || shareInput.placeholder;
    }
    const layoutSel = $('#kintailayoutsel');
    if (layoutSel && pack.layoutId) layoutSel.value = pack.layoutId;
    syncPersonLayout();
    paintShareStatus();
    const note = $('#kintainote');
    if (note) note.textContent = pack.opsNote || '';
    const layout = $('#kintailayout');
    if (layout) layout.textContent = `レイアウト: ${pack.layoutId || '—'}${pack.layoutNote ? ' — ' + pack.layoutNote : ''}`;
    const sample = $('#kintaisampleneed');
    if (sample) sample.textContent = pack.sampleNeeded || '';
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
    syncPersonLayout();
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

  function paintKintai() {
    if (!$('#kintaidesk')) return;
    paintFilters();
    const body = $('#kintaitable');
    const status = $('#kintaistatus');
    if (!pack) {
      if (body) body.innerHTML = '';
      return;
    }
    mode = selectedPersonId() ? 'person' : 'month';
    if (mode === 'person' && pack.rows[0]?.months) {
      const p = pack.rows[0];
      const empLabel = p.retired ? '退職' : (EMP[p.employment] || p.employment);
      if (status) {
        status.textContent = `${p.displayName}（${p.employeeCode}・${empLabel}）／取込: ${p.lastStatus || '未取得'}`;
      }
      body.innerHTML = `<table><thead><tr><th>対象年月</th><th>出勤日数</th><th>休日数</th><th>有給取得日数</th><th>普通勤務</th><th>残業</th><th>勤務時間計</th><th>取込</th></tr></thead><tbody>${
        p.months.map(m => `<tr>
          <td>${m.year}年${m.month}月</td>
          <td>${num(m.workDays)}</td>
          <td>${num(m.holidayDays)}</td>
          <td>${num(m.paidLeaveDays)}</td>
          <td>${esc(m.ordinaryHm || '—')}</td>
          <td>${esc(m.overtimeHm || '—')}</td>
          <td>${esc(m.totalHm || '—')}</td>
          <td>${esc(m.status || '未取得')}${m.error ? `<small class="kintai-errdetail">${esc(m.error)}</small>` : ''}</td>
        </tr>`).join('')
      }</tbody></table>`;
      if (p.lastError) {
        status.innerHTML = `${esc(status.textContent)}${errorBlockHtml(p.lastError, false)}`;
      }
      return;
    }
    const ymLabel = selectedYearMonth().replace('-', '年') + '月';
    const visible = (pack.rows || []).filter(r => passesPeopleFilter(r.retired));
    const activeRows = visible.filter(r => !r.retired);
    const retiredRows = visible.filter(r => r.retired);
    if (status) {
      status.textContent = `${pack.fiscalLabel} ／ ${ymLabel} ／ 在職${activeRows.length}・退職${retiredRows.length}`;
    }
    body.innerHTML = monthTableHtml(activeRows, '在職') + monthTableHtml(retiredRows, '退職者')
      || '<p class="kintaimeta">表示対象の人がいません。</p>';
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
    if (shareInputUnsaved()) {
      const fileEl = $('#kintaifileinput');
      const usingAbs = looksAbsolutePath(fileEl && fileEl.value);
      if (!usingAbs) {
        const go = confirm('任意の年度ルート入力が未保存です。相対パス運用なら先に保存してください。このまま続けますか？');
        if (!go) return;
      }
    }
    if (!confirm('出勤簿から集計を更新します。元ファイルは変更しません。よろしいですか？')) return;
    const summary = await api('/api/kintai/import', { year: fiscalStartYear() });
    lastImportSummary = summary;
    const counts = summary.counts || {};
    const ok = counts.ok != null ? counts.ok : (summary.people || []).filter(p => p.status === '成功').length;
    const err = counts.error != null ? counts.error : (summary.people || []).filter(p => p.status === 'エラー').length;
    const miss = counts.missing != null ? counts.missing : (summary.people || []).filter(p => p.status === '未取得').length;
    toast(`取込完了: 成功${ok}／エラー${err}／未取得${miss}（詳細は画面の「取込結果の詳細」）`);
    paintImportResult(summary);
    await loadKintai();
  }

  async function saveShareRoot() {
    if (!canManagePaths()) throw Error('共有パス設定は利用者管理の権限が必要です。');
    const input = $('#kintaishareinput');
    const layoutSel = $('#kintailayoutsel');
    const shareRoot = input ? input.value.trim() : '';
    const layoutId = layoutSel ? layoutSel.value : undefined;
    const body = { shareRoot };
    if (layoutId) body.layoutId = layoutId;
    await api('/api/kintai/settings', body);
    if (input) delete input.dataset.touched;
    toast('年度ルートとレイアウトを保存しました（このPCの data/kintai-local.json）。');
    await loadKintai();
  }

  async function savePersonFile() {
    if (!canManagePaths()) throw Error('読取元設定は利用者管理の権限が必要です。');
    const personEl = $('#kintaifileperson');
    const fileEl = $('#kintaifileinput');
    const layoutEl = $('#kintaipersonlayout');
    const personId = personEl ? Number(personEl.value) : 0;
    const sourceFile = fileEl ? fileEl.value.trim() : '';
    if (!personId) throw Error('対象者を選んでください。');
    const body = { personId, sourceFile, layoutId: layoutEl ? layoutEl.value : '' };
    await api('/api/kintai/person-file', body);
    if (fileEl) delete fileEl.dataset.touched;
    toast('読取元を保存しました。他の対象者も同様にフルパスを設定してください。');
    await loadKintai();
  }

  function fillEmploymentExample(kind) {
    if (!pack) return;
    const examples = pack.examplePathsByEmployment || {};
    const ex = examples[kind];
    if (!ex) throw Error('この雇用区分の例示がありません。');
    const fileInput = $('#kintaifileinput');
    const filePerson = $('#kintaifileperson');
    const layoutEl = $('#kintaipersonlayout');
    const person = (pack.people || []).find(
      p => p.employeeCode === ex.employeeCode && p.employment === ex.employment
    ) || (pack.people || []).find(p => p.employeeCode === ex.employeeCode)
      || (pack.people || []).find(p => p.employment === ex.employment);
    if (filePerson && person) filePerson.value = String(person.id);
    if (fileInput) {
      fileInput.value = ex.fullPath || ex.relative || '';
      fileInput.dataset.touched = '1';
    }
    if (layoutEl && ex.layoutId) layoutEl.value = ex.layoutId;
    paintShareStatus();
    toast(`${ex.label}のフルパス例を入れました。「この対象者の読取元を保存」を押してください。`);
  }

  function fillKatoExample() { fillEmploymentExample('regular'); }

  async function listShareFiles() {
    if (!canManagePaths()) throw Error('共有フォルダ一覧は利用者管理の権限が必要です。');
    if (shareInputUnsaved()) {
      throw Error('年度ルートが未保存です。先に「年度ルートとレイアウトを保存」を押してください。');
    }
    const personEl = $('#kintaifileperson');
    const subdirEl = $('#kintaisubdir');
    const personId = personEl ? Number(personEl.value) : 0;
    const subdir = subdirEl && subdirEl.value ? subdirEl.value : '';
    const result = await api('/api/kintai/list-files', {
      personId: personId || undefined,
      subdir: subdir || undefined,
    });
    if (subdirEl && result.subfolders) {
      const keep = subdirEl.value;
      subdirEl.innerHTML = '<option value="">（年度ルート直下／対象者の相対から推定）</option>' +
        (result.subfolders || []).map(s =>
          `<option value="${esc(s.relative || s.name)}">${esc(s.name)}</option>`
        ).join('');
      if (keep && [...subdirEl.options].some(o => o.value === keep)) subdirEl.value = keep;
      else if (result.subdir) subdirEl.value = result.subdir;
    }
    const pick = $('#kintaifilepick');
    if (pick) {
      const files = result.files || [];
      pick.innerHTML = '<option value="">' + (files.length ? `候補 ${files.length} 件` : '（候補なし）') + '</option>' +
        files.map(f => {
          const rel = f.relative || f.name;
          return `<option value="${esc(rel)}">${esc(rel)}</option>`;
        }).join('');
    }
    const box = $('#kintaiproberesult');
    if (box) {
      box.hidden = false;
      box.textContent = JSON.stringify(result, null, 2);
    }
    toast(result.count ? `候補 ${result.count} 件を読みました。選ぶと相対パスが入ります。` : '候補ファイルがありません（年度ルート・雇用区分フォルダ・権限を確認）。');
  }

  async function probeFile() {
    if (!canKintai()) throw Error('マーク管理の権限がありません。');
    const personEl = $('#kintaifileperson');
    const personId = personEl ? Number(personEl.value) : 0;
    if (!personId) throw Error('対象者を選んでください。');
    const result = await api('/api/kintai/probe', { personId });
    const box = $('#kintaiproberesult');
    if (box) {
      box.hidden = false;
      box.textContent = JSON.stringify(result, null, 2);
    }
    toast('シート一覧を取得しました（集計・セル意味の判定はしていません）。');
  }

  window.loadKintai = loadKintai;
  window.paintKintai = paintKintai;
  window.importKintai = importKintai;

  function bindKintaiControls() {
    const year = $('#kintaiyear');
    const month = $('#kintaimonth');
    const person = $('#kintaiperson');
    const refresh = $('#kintairefresh');
    const imp = $('#kintaiimport');
    const saveShare = $('#kintaisaveshare');
    const saveFile = $('#kintaifilesave');
    const fillKato = $('#kintaifilefill');
    const fillContract = $('#kintaifilefill-contract');
    const fillPart = $('#kintaifilefill-part');
    const listFiles = $('#kintailistfiles');
    const probe = $('#kintaiprobe');
    const shareInput = $('#kintaishareinput');
    const fileInput = $('#kintaifileinput');
    const filePerson = $('#kintaifileperson');
    const filePick = $('#kintaifilepick');
    const subdirEl = $('#kintaisubdir');
    if (year) year.onchange = () => { selectedYm = ''; loadKintai().catch(e => toast(e.message)); };
    if (month) month.onchange = () => {
      mode = 'month';
      selectedYm = month.value;
      if (person) person.value = '';
      loadKintai().catch(e => toast(e.message));
    };
    if (person) person.onchange = () => { mode = person.value ? 'person' : 'month'; loadKintai().catch(e => toast(e.message)); };
    const filter = $('#kintaifilter');
    if (filter) filter.onchange = () => {
      peopleFilter = filter.value || 'all';
      paintKintai();
    };
    if (refresh) refresh.onclick = () => loadKintai().catch(e => toast(e.message));
    if (imp) imp.onclick = () => importKintai().catch(e => toast(e.message));
    if (saveShare) saveShare.onclick = () => saveShareRoot().catch(e => toast(e.message));
    if (saveFile) saveFile.onclick = () => savePersonFile().catch(e => toast(e.message));
    if (fillKato) fillKato.onclick = () => { try { fillKatoExample(); } catch (e) { toast(e.message); } };
    if (fillContract) fillContract.onclick = () => { try { fillEmploymentExample('contract'); } catch (e) { toast(e.message); } };
    if (fillPart) fillPart.onclick = () => { try { fillEmploymentExample('part'); } catch (e) { toast(e.message); } };
    if (listFiles) listFiles.onclick = () => listShareFiles().catch(e => toast(e.message));
    if (probe) probe.onclick = () => probeFile().catch(e => toast(e.message));
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
    if (shareInput) shareInput.addEventListener('input', () => { shareInput.dataset.touched = '1'; paintShareStatus(); });
    if (fileInput) fileInput.addEventListener('input', () => { fileInput.dataset.touched = '1'; });
    if (filePerson) filePerson.onchange = () => { if (fileInput) delete fileInput.dataset.touched; syncFileInputFromPerson(); };
    if (subdirEl) subdirEl.onchange = () => listShareFiles().catch(e => toast(e.message));
    if (filePick) filePick.onchange = () => {
      if (!filePick.value || !fileInput) return;
      fileInput.value = filePick.value;
      fileInput.dataset.touched = '1';
      toast('候補の相対パスを読取元欄に入れました。「この対象者の読取元を保存」を押してください。');
    };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindKintaiControls);
  else bindKintaiControls();
})();
