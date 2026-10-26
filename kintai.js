/* 勤怠管理（マーク）画面。権限は currentUser.mark_kintai（主管理者は常に可）。 */
(function () {
  const EMP = { regular: '社員', contract: '契約', part: 'パート' };
  let pack = null;
  let mode = 'month'; // month | person
  let selectedYm = '';

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
      // 必ず今の年度（例: 2026年2月〜2027年1月）を選べる／初期選択する
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
    const personSel = $('#kintaiperson');
    if (personSel) {
      const keep = personSel.value;
      personSel.innerHTML = '<option value="">（月別の全員比較）</option>' +
        (pack.people || []).map(p =>
          `<option value="${p.id}">${esc(p.displayName)}（${esc(p.employeeCode)}・${EMP[p.employment] || p.employment}）</option>`
        ).join('');
      if (keep && [...personSel.options].some(o => o.value === keep)) personSel.value = keep;
    }
    const filePerson = $('#kintaifileperson');
    if (filePerson) {
      const keep = filePerson.value;
      filePerson.innerHTML = (pack.people || []).map(p =>
        `<option value="${p.id}">${esc(p.displayName)}（${esc(p.employeeCode)}）</option>`
      ).join('');
      if (keep && [...filePerson.options].some(o => o.value === keep)) filePerson.value = keep;
      syncFileInputFromPerson();
    }
    const shareInput = $('#kintaishareinput');
    if (shareInput && !shareInput.dataset.touched) {
      shareInput.value = pack.shareRoot || '';
      shareInput.placeholder = pack.exampleKatoShareRoot || shareInput.placeholder;
    }
    const layoutSel = $('#kintailayoutsel');
    if (layoutSel && pack.layoutId) layoutSel.value = pack.layoutId;
    const share = $('#kintaishare');
    if (share) {
      share.textContent = pack.shareRootConfigured
        ? `共有ルート設定あり`
        : '共有ルート未設定（ローカル data/kintai-local.json または画面から設定）';
    }
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
  }

  function syncFileInputFromPerson() {
    const filePerson = $('#kintaifileperson');
    const fileInput = $('#kintaifileinput');
    if (!filePerson || !fileInput || !pack) return;
    const p = (pack.people || []).find(x => String(x.id) === String(filePerson.value));
    if (!fileInput.dataset.touched) fileInput.value = (p && p.sourceFile) || '';
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
      if (status) {
        status.textContent = `${p.displayName}（${p.employeeCode}・${EMP[p.employment] || p.employment}）／取込: ${p.lastStatus || '未取得'}${p.lastError ? ' — ' + p.lastError : ''}`;
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
          <td>${esc(m.status || '未取得')}${m.error ? `<small>${esc(m.error)}</small>` : ''}</td>
        </tr>`).join('')
      }</tbody></table>`;
      return;
    }
    const ymLabel = selectedYearMonth().replace('-', '年') + '月';
    if (status) status.textContent = `${pack.fiscalLabel} ／ ${ymLabel} の対象者比較`;
    body.innerHTML = `<table><thead><tr><th>氏名</th><th>社員番号</th><th>区分</th><th>出勤日数</th><th>休日数</th><th>有給取得日数</th><th>普通勤務</th><th>残業</th><th>勤務時間計</th><th>取込結果</th></tr></thead><tbody>${
      (pack.rows || []).map(r => {
        const m = r.month || {};
        return `<tr>
          <td><b>${esc(r.displayName)}</b></td>
          <td>${esc(r.employeeCode)}</td>
          <td>${esc(EMP[r.employment] || r.employment)}</td>
          <td>${num(m.workDays)}</td>
          <td>${num(m.holidayDays)}</td>
          <td>${num(m.paidLeaveDays)}</td>
          <td>${esc(m.ordinaryHm || '—')}</td>
          <td>${esc(m.overtimeHm || '—')}</td>
          <td>${esc(m.totalHm || '—')}</td>
          <td>${esc(r.lastStatus || m.status || '未取得')}${(r.lastError || m.error) ? `<small>${esc(r.lastError || m.error)}</small>` : ''}${r.lastImportAt ? `<small>${esc(r.lastImportAt)}</small>` : ''}</td>
        </tr>`;
      }).join('')
    }</tbody></table>`;
  }

  async function importKintai() {
    if (!canKintai()) throw Error('勤怠管理（マーク）の権限がありません。');
    if (!confirm('出勤簿から集計を更新します。元ファイルは変更しません。よろしいですか？')) return;
    const summary = await api('/api/kintai/import', { year: fiscalStartYear() });
    const ok = (summary.people || []).filter(p => p.status === '成功').length;
    const err = (summary.people || []).filter(p => p.status === 'エラー').length;
    const miss = (summary.people || []).filter(p => p.status === '未取得').length;
    toast(`取込完了: 成功${ok}／エラー${err}／未取得${miss}`);
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
    toast('共有ルートとレイアウトを保存しました（このPCの data/kintai-local.json）。');
    await loadKintai();
  }

  async function savePersonFile() {
    if (!canManagePaths()) throw Error('読取元設定は利用者管理の権限が必要です。');
    const personEl = $('#kintaifileperson');
    const fileEl = $('#kintaifileinput');
    const personId = personEl ? Number(personEl.value) : 0;
    const sourceFile = fileEl ? fileEl.value.trim() : '';
    if (!personId) throw Error('対象者を選んでください。');
    await api('/api/kintai/person-file', { personId, sourceFile });
    if (fileEl) delete fileEl.dataset.touched;
    toast('読取元を保存しました。');
    await loadKintai();
  }

  function fillKatoExample() {
    if (!pack) return;
    const shareInput = $('#kintaishareinput');
    const fileInput = $('#kintaifileinput');
    const filePerson = $('#kintaifileperson');
    const kato = (pack.people || []).find(p => p.employeeCode === '009' && p.employment === 'regular');
    if (filePerson && kato) filePerson.value = String(kato.id);
    if (shareInput) {
      shareInput.value = pack.exampleKatoShareRoot || '';
      shareInput.dataset.touched = '1';
    }
    if (fileInput) {
      fileInput.value = pack.exampleKatoFile || '';
      fileInput.dataset.touched = '1';
    }
    toast('加藤さんの例示パスを入力欄に入れました。保存ボタンで確定してください（マーク管理は変更しません）。');
  }

  async function probeFile() {
    if (!canKintai()) throw Error('勤怠管理（マーク）の権限がありません。');
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
    const probe = $('#kintaiprobe');
    const shareInput = $('#kintaishareinput');
    const fileInput = $('#kintaifileinput');
    const filePerson = $('#kintaifileperson');
    if (year) year.onchange = () => { selectedYm = ''; loadKintai().catch(e => toast(e.message)); };
    if (month) month.onchange = () => {
      mode = 'month';
      selectedYm = month.value;
      if (person) person.value = '';
      loadKintai().catch(e => toast(e.message));
    };
    if (person) person.onchange = () => { mode = person.value ? 'person' : 'month'; loadKintai().catch(e => toast(e.message)); };
    if (refresh) refresh.onclick = () => loadKintai().catch(e => toast(e.message));
    if (imp) imp.onclick = () => importKintai().catch(e => toast(e.message));
    if (saveShare) saveShare.onclick = () => saveShareRoot().catch(e => toast(e.message));
    if (saveFile) saveFile.onclick = () => savePersonFile().catch(e => toast(e.message));
    if (fillKato) fillKato.onclick = () => fillKatoExample();
    if (probe) probe.onclick = () => probeFile().catch(e => toast(e.message));
    if (shareInput) shareInput.addEventListener('input', () => { shareInput.dataset.touched = '1'; });
    if (fileInput) fileInput.addEventListener('input', () => { fileInput.dataset.touched = '1'; });
    if (filePerson) filePerson.onchange = () => { if (fileInput) delete fileInput.dataset.touched; syncFileInputFromPerson(); };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindKintaiControls);
  else bindKintaiControls();
})();
