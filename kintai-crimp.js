/* 圧着カウンター（シフト表の下）。1日1件、差分＝前回からの圧着数。 */
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
    data = await api('/api/kintai/crimp?month=' + encodeURIComponent(month.value));
    $('#crimptotal').textContent = `月合計 ${fmt(data.total)}`;
    $('#crimptable tbody').innerHTML = data.rows.map(r => `<tr data-day="${esc(r.day)}"><td>${esc(r.day)}</td><td>${fmt(r.reading)}</td><td>${r.diff === null ? '—' : fmt(r.diff)}</td><td>${esc(r.userName)}</td><td>${data.canAdmin ? '<button type="button" class="quiet" data-crimp-edit>修正</button><button type="button" class="quiet" data-crimp-del>削除</button>' : ''}</td></tr>`).join('');
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

  async function saveCrimp(day, reading, edit) {
    const prev = await previousReading(day);
    if (prev !== null && reading < prev && !confirm(`前回（${fmt(prev)}）より小さい値です。保存しますか？`)) return;
    await api('/api/kintai/crimp', { day, reading, edit: !!edit });
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
      if (!day || raw === '') return;
      saveCrimp(day, Number(raw), false).then(() => { $('#crimpreading').value = ''; }).catch(err => toast(err.message));
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
        saveCrimp(day, Number(v), true).catch(err => toast(err.message));
      }
    });
  }

  bind();
  window.loadKintaiCrimp = loadCrimp;
})();
