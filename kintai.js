/* 勤怠管理（マーク）画面。権限は currentUser.mark_kintai（主管理者は常に可）。 */
(function () {
  const EMP = { regular: '社員', contract: '契約', part: 'パート' };
  let pack = null;
  let mode = 'month'; // month | person
  let selectedYm = '';
  let lastImportSummary = null;

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

  function paintShareStatus() {
    const share = $('#kintaishare');
    if (!share) return;
    const saved = (pack && pack.shareRoot) || '';
    const input = $('#kintaishareinput');
    const current = input ? input.value.trim() : '';
    share.classList.remove('kintai-warn');
    if (shareInputUnsaved()) {
      share.classList.add('kintai-warn');
      share.textContent = saved
        ? '共有ルート: 保存済みあり ／ 入力欄は未保存の変更です。「共有ルートとレイアウトを保存」を押してください（未保存のパスでは取込できません）'
        : '共有ルート: 未保存の入力あり（まだサーバーに保存されていません。「共有ルートとレイアウトを保存」を押してください）';
      return;
    }
    if (saved) {
      share.textContent = '共有ルート設定あり（保存済み）';
      return;
    }
    share.textContent = '共有ルート未設定（入力しただけでは未保存です。保存ボタンが必要です）';
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
  }

  function paintImportResult(summary) {
    const box = $('#kintaiimportresult');
    if (!box) return;
    if (!summary || !summary.people) {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }
    const counts = summary.counts || {
      ok: summary.people.filter(p => p.status === '成功').length,
      error: summary.people.filter(p => p.status === 'エラー').length,
      missing: summary.people.filter(p => p.status === '未取得').length,
    };
    const rows = summary.people.map(p => {
      const kind = p.errorKind ? `（${esc(p.errorKind)}）` : '';
      const path = p.sourcePath || p.configuredPath || '';
      const note = p.resolvedNote ? `<div class="kintai-errdetail">照合: ${esc(p.resolvedNote)}</div>` : '';
      const cands = (p.candidates && p.candidates.length)
        ? `<div class="kintai-errdetail">近い候補: ${p.candidates.map(esc).join(' ／ ')}</div>`
        : '';
      const err = p.error
        ? `<div class="kintai-errdetail">${esc(p.error)}</div>`
        : '';
      const pathLine = path
        ? `<div class="kintai-errdetail">パス: ${esc(path)}</div>`
        : '';
      return `<tr class="${p.status === '成功' ? 'kintai-ok' : (p.status === 'エラー' ? 'kintai-ng' : 'kintai-miss')}">
        <td>${esc(p.displayName)}<br><small>${esc(p.employeeCode)}</small></td>
        <td><strong>${esc(p.status)}</strong>${kind}${err}${pathLine}${note}${cands}</td>
      </tr>`;
    }).join('');
    box.hidden = false;
    box.innerHTML = `<h3>取込結果の詳細</h3>
      <p>成功${counts.ok}／エラー${counts.error}／未取得${counts.missing}
      ${summary.shareRootConfigured ? '' : ' ／ <span class="kintai-warn-inline">共有ルート未保存の可能性</span>'}
      ${summary.shareRoot ? ` ／ 使用ルート: <code>${esc(summary.shareRoot)}</code>` : ''}</p>
      <table class="kintai-result-table"><thead><tr><th>対象者</th><th>結果・理由</th></tr></thead><tbody>${rows}</tbody></table>`;
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
        status.textContent = `${p.displayName}（${p.employeeCode}・${EMP[p.employment] || p.employment}）／取込: ${p.lastStatus || '未取得'}`;
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
        status.innerHTML = `${esc(status.textContent)}<div class="kintai-errdetail">${esc(p.lastError)}</div>`;
      }
      return;
    }
    const ymLabel = selectedYearMonth().replace('-', '年') + '月';
    if (status) status.textContent = `${pack.fiscalLabel} ／ ${ymLabel} の対象者比較`;
    body.innerHTML = `<table><thead><tr><th>氏名</th><th>社員番号</th><th>区分</th><th>出勤日数</th><th>休日数</th><th>有給取得日数</th><th>普通勤務</th><th>残業</th><th>勤務時間計</th><th>取込結果</th></tr></thead><tbody>${
      (pack.rows || []).map(r => {
        const m = r.month || {};
        const err = r.lastError || m.error || '';
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
          <td>${esc(r.lastStatus || m.status || '未取得')}${err ? `<small class="kintai-errdetail">${esc(err)}</small>` : ''}${r.lastImportAt ? `<small>${esc(r.lastImportAt)}</small>` : ''}</td>
        </tr>`;
      }).join('')
    }</tbody></table>`;
  }

  async function importKintai() {
    if (!canKintai()) throw Error('勤怠管理（マーク）の権限がありません。');
    if (shareInputUnsaved()) {
      const go = confirm('共有ルートの入力が未保存です。未保存のパスは取込に使われません。このまま続けますか？（キャンセルして「共有ルートとレイアウトを保存」を先に押すことを推奨）');
      if (!go) return;
    }
    if (fileInputUnsaved()) {
      const go = confirm('読取元ファイルの入力が未保存です。未保存のファイル名は取込に使われません。このまま続けますか？');
      if (!go) return;
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
      // 実ファイル名: 009のあと半角スペース2・全角括弧・全角チルダ～
      fileInput.value = pack.exampleKatoFile || '009  正社員（営業手当無し）出勤簿2026.2～.xlsx';
      fileInput.dataset.touched = '1';
    }
    paintShareStatus();
    toast('加藤さんの例示パスを入力欄に入れました。必ず「保存」ボタンを2つ（共有ルート／読取元）押してください。');
  }

  async function listShareFiles() {
    if (!canManagePaths()) throw Error('共有フォルダ一覧は利用者管理の権限が必要です。');
    if (shareInputUnsaved()) {
      throw Error('共有ルートが未保存です。先に「共有ルートとレイアウトを保存」を押してください。');
    }
    const personEl = $('#kintaifileperson');
    const personId = personEl ? Number(personEl.value) : 0;
    const result = await api('/api/kintai/list-files', { personId: personId || undefined });
    const pick = $('#kintaifilepick');
    if (pick) {
      const files = result.files || [];
      pick.innerHTML = '<option value="">' + (files.length ? `候補 ${files.length} 件` : '（候補なし）') + '</option>' +
        files.map(f => `<option value="${esc(f.name)}">${esc(f.name)}</option>`).join('');
    }
    const box = $('#kintaiproberesult');
    if (box) {
      box.hidden = false;
      box.textContent = JSON.stringify(result, null, 2);
    }
    toast(result.count ? `候補 ${result.count} 件を読みました。一覧から選ぶと読取元欄に入ります。` : '候補ファイルがありません（ルート・権限を確認）。');
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
    const listFiles = $('#kintailistfiles');
    const probe = $('#kintaiprobe');
    const shareInput = $('#kintaishareinput');
    const fileInput = $('#kintaifileinput');
    const filePerson = $('#kintaifileperson');
    const filePick = $('#kintaifilepick');
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
    if (listFiles) listFiles.onclick = () => listShareFiles().catch(e => toast(e.message));
    if (probe) probe.onclick = () => probeFile().catch(e => toast(e.message));
    if (shareInput) shareInput.addEventListener('input', () => { shareInput.dataset.touched = '1'; paintShareStatus(); });
    if (fileInput) fileInput.addEventListener('input', () => { fileInput.dataset.touched = '1'; });
    if (filePerson) filePerson.onchange = () => { if (fileInput) delete fileInput.dataset.touched; syncFileInputFromPerson(); };
    if (filePick) filePick.onchange = () => {
      if (!filePick.value || !fileInput) return;
      fileInput.value = filePick.value;
      fileInput.dataset.touched = '1';
      toast('候補のファイル名を読取元欄に入れました。「この対象者の読取元を保存」を押してください。');
    };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindKintaiControls);
  else bindKintaiControls();
})();
