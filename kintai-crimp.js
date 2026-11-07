/* 圧着カウンター（マーク管理メニューの独立セクション）。1日1件、差分＝前回からの圧着数。 */
(() => {
  const $ = s => document.querySelector(s);
  let data = null;
  const pad = n => String(n).padStart(2, '0');
  const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
  const fmt = n => (n === null || n === undefined) ? '' : Number(n).toLocaleString('ja-JP');
  const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  async function loadCrimp() {
    const month = $('#crimpmonth');
    if (!month) return;
    if (!month.value) month.value = today().slice(0, 7);
    if (!$('#crimpday').value) $('#crimpday').value = today();
    const sel = $('#crimpworker');
    if (sel && !sel.dataset.ready) {
      const rows = await api('/api/kintai/crimp/workers');
      sel.innerHTML = '<option value="">選択してください</option>' + rows.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('');
      sel.dataset.ready = '1';
    }
    data = await api('/api/kintai/crimp?month=' + encodeURIComponent(month.value));
    const showInputter = !!data.canAdmin;
    $('#crimptotal').textContent = `月合計 ${fmt(data.total)}`;
    $('#crimptable tbody').innerHTML = data.rows.map(r => {
      const name = showInputter ? esc(r.userName || '') : '';
      return `<tr data-day="${esc(r.day)}"><td>${esc(r.day)}</td><td>${fmt(r.reading)}</td><td>${r.diff === null ? '—' : fmt(r.diff)}</td><td class="crimp-inputter"${showInputter ? '' : ' hidden'}>${name}</td><td>${showInputter ? '<button type="button" class="quiet" data-crimp-edit>修正</button><button type="button" class="quiet" data-crimp-del>削除</button>' : ''}</td></tr>`;
    }).join('');
    document.querySelectorAll('#crimptable .crimp-inputter').forEach(el => { el.hidden = !showInputter; });
  }

  async function previousReading(day) {
    const ym = day.slice(0, 7);
    const pack = ym === (data && data.month) ? data : await api('/api/kintai/crimp?month=' + ym);
    const before = pack.rows.filter(r => r.day < day);
    if (before.length) return before[before.length - 1].reading;
    // 月初より前は前月の最後を見る
    const d = new Date(day + 'T00:00:00'); d.setDate(0);
    const prev = await api('/api/kintai/crimp?month=' + `${d.getFullYear()}-${pad(d.getMonth() + 1)}`);
    return prev.rows.length ? prev.rows[prev.rows.length - 1].reading : null;
  }

  async function saveCrimp(day, reading, edit, workerId) {
    const prev = await previousReading(day);
    if (prev !== null && reading < prev && !confirm(`前回（${fmt(prev)}）より小さい値です。保存しますか？`)) return;
    await api('/api/kintai/crimp', { day, reading, edit: !!edit, workerId });
    toast('保存しました。');
    $('#crimpmonth').value = day.slice(0, 7);
    await loadCrimp();
  }

  function bind() {
    const form = $('#crimpform');
    if (!form) return;
    form.addEventListener('submit', e => {
      e.preventDefault();
      const day = $('#crimpday').value, raw = $('#crimpreading').value;
      const workerId = $('#crimpworker').value;
      if (!workerId) { toast('入力者を選んでください。'); return; }
      if (!day || raw === '') return;
      saveCrimp(day, Number(raw), false, Number(workerId)).then(() => { $('#crimpreading').value = ''; }).catch(err => toast(err.message));
    });
    $('#crimpmonth').addEventListener('change', () => loadCrimp().catch(err => toast(err.message)));
    $('#crimptable').addEventListener('click', e => {
      const tr = e.target.closest('tr[data-day]');
      if (!tr) return;
      const day = tr.dataset.day;
      if (e.target.closest('[data-crimp-del]')) {
        if (!confirm(`${day} を削除しますか？`)) return;
        api('/api/kintai/crimp/delete', { day }).then(loadCrimp).catch(err => toast(err.message));
      } else if (e.target.closest('[data-crimp-edit]')) {
        const v = prompt(`${day} のカウンター`, tr.children[1].textContent.replace(/,/g, ''));
        if (v === null || v.trim() === '') return;
        const workerId = $('#crimpworker').value;
        if (!workerId) { toast('入力者を選んでください。'); return; }
        saveCrimp(day, Number(v), true, Number(workerId)).catch(err => toast(err.message));
      }
    });
  }

  bind();
  window.loadKintaiCrimp = loadCrimp;
})();
