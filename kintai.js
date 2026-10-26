/* 勤怠管理（マーク）画面。権限は currentUser.mark_kintai。 */
(function () {
  const EMP = { regular: '社員', contract: '契約', part: 'パート' };
  let pack = null;
  let mode = 'month'; // month | person

  function $(sel, root) { return (root || document).querySelector(sel); }

  function fiscalStartYear() {
    const el = $('#kintaiyear');
    return el ? Number(el.value) : 2026;
  }

  function selectedMonth() {
    const el = $('#kintaimonth');
    return el ? Number(el.value) : 2;
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

  async function loadKintai() {
    if (!canKintai()) return;
    const year = fiscalStartYear();
    const qs = new URLSearchParams({ year: String(year) });
    if (mode === 'person') {
      const pid = selectedPersonId();
      if (pid) qs.set('personId', String(pid));
    } else {
      qs.set('month', String(selectedMonth()));
    }
    pack = await api('/api/kintai?' + qs.toString());
    paintKintai();
  }

  function paintFilters() {
    if (!pack) return;
    const yearSel = $('#kintaiyear');
    if (yearSel && !yearSel.dataset.ready) {
      const y = pack.fiscalStartYear || 2026;
      yearSel.innerHTML = [y - 1, y, y + 1].map(v =>
        `<option value="${v}" ${v === y ? 'selected' : ''}>${v}年${pack.fiscalStartMonth || 2}月〜</option>`
      ).join('');
      yearSel.dataset.ready = '1';
    }
    const monthSel = $('#kintaimonth');
    if (monthSel) {
      monthSel.innerHTML = (pack.months || []).map(m =>
        `<option value="${m.month}">${m.year}年${m.month}月</option>`
      ).join('');
      const cur = selectedMonth();
      if ([...monthSel.options].some(o => Number(o.value) === cur)) monthSel.value = String(cur);
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
    const share = $('#kintaishare');
    if (share) {
      share.textContent = pack.shareRootConfigured
        ? `共有ルート設定あり（${pack.shareRoot}）`
        : '共有ルート未設定（検証用 data/kintai-fixtures またはローカル設定が必要）';
    }
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
    if (status) status.textContent = `${pack.fiscalLabel} ／ ${selectedMonth()}月の対象者比較`;
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
    if (!(currentUser?.role === 'owner' || currentUser?.manage)) {
      throw Error('共有パス設定は利用者管理の権限が必要です。');
    }
    const input = $('#kintaishareinput');
    const shareRoot = input ? input.value.trim() : '';
    await api('/api/kintai/settings', { shareRoot });
    toast('共有ルートを保存しました（このPCの data/kintai-local.json）。');
    await loadKintai();
  }

  window.loadKintai = loadKintai;
  window.paintKintai = paintKintai;
  window.importKintai = importKintai;

  document.addEventListener('DOMContentLoaded', () => {
    const year = $('#kintaiyear');
    const month = $('#kintaimonth');
    const person = $('#kintaiperson');
    const refresh = $('#kintairefresh');
    const imp = $('#kintaiimport');
    const saveShare = $('#kintaisaveshare');
    if (year) year.onchange = () => loadKintai().catch(e => toast(e.message));
    if (month) month.onchange = () => { mode = 'month'; if (person) person.value = ''; loadKintai().catch(e => toast(e.message)); };
    if (person) person.onchange = () => { mode = person.value ? 'person' : 'month'; loadKintai().catch(e => toast(e.message)); };
    if (refresh) refresh.onclick = () => loadKintai().catch(e => toast(e.message));
    if (imp) imp.onclick = () => importKintai().catch(e => toast(e.message));
    if (saveShare) saveShare.onclick = () => saveShareRoot().catch(e => toast(e.message));
  });
})();
