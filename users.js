/* 利用者一覧（読み取り専用）。データは /api/users（利用者管理と同じ権限）。 */
(() => {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const roleNames = { owner: '主管理者', sales: '営業', floor: '現場', floor2: '現場２', floor3: '現場３', warehouse: '倉庫担当' };
  const roleOrder = { owner: 0, sales: 1, floor: 2, floor2: 3, floor3: 4, warehouse: 5 };
  let rows = [], key = null, dir = 1;
  const text = {
    login: u => u.login || '', name: u => u.name || '', role: u => roleNames[u.role] || u.role,
    admin: u => (u.manage || u.overview) ? '○' : '',
    mark: u => (u.role === 'owner' || u.mark_kintai) ? '○' : '',
    active: u => u.active ? '利用中' : '停止',
    pending: u => u.must_change ? '変更待ち' : '',
  };
  const sortVal = { role: u => roleOrder[u.role] ?? 9 };
  function render() {
    const list = rows.slice();
    if (key) {
      const f = sortVal[key] || text[key];
      list.sort((a, b) => { const x = f(a), y = f(b); return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y), 'ja')) * dir || a.id - b.id; });
    }
    document.querySelectorAll('th[data-key]').forEach(th => th.setAttribute('aria-sort', th.dataset.key === key ? (dir > 0 ? 'ascending' : 'descending') : 'none'));
    $('#userlist tbody').innerHTML = list.map(u => `<tr data-id="${u.id}"${u.active ? '' : ' class="inactive"'}>${Object.keys(text).map(k => `<td>${esc(text[k](u))}</td>`).join('')}</tr>`).join('');
  }
  document.querySelector('#userlist thead').addEventListener('click', e => {
    const th = e.target.closest('th[data-key]'); if (!th) return;
    if (key === th.dataset.key) dir = -dir; else { key = th.dataset.key; dir = 1; }
    render();
  });
  fetch('/api/users').then(async r => {
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || '読み込めませんでした。');
    rows = d; $('#usercount').textContent = `${rows.length}人`; render();
  }).catch(e => { $('#usermessage').textContent = e.message; });
})();
