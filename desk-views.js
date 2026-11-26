/* 指示書デスク：一覧／かんばん／カレンダーの表示切り替え（指示書一覧・現場の作業一覧）。
   作業状況の変更は、既存の作業状況プルダウンと同じ /api/orders/status-flag を使う（権限・版数チェックはサーバー側で同じ）。 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DeskViews = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const LAYOUTS = [
    { id: 'list', label: '一覧' },
    { id: 'kanban', label: 'かんばん' },
    { id: 'calendar', label: 'カレンダー' },
  ];
  const SCREENS = ['orders', 'floor'];
  const STATUS_API = '/api/orders/status-flag';
  const OPEN_STATES = ['未着手', '作業中'];
  const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土'];
  const CALENDAR_MAX_ITEMS = 3;

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }
  function storageKey(screen) { return 'deskLayout:' + screen; }
  function normalizeLayout(value) { return LAYOUTS.some((l) => l.id === value) ? value : 'list'; }
  function supports(screen) { return SCREENS.includes(screen); }
  function loadLayout(storage, screen) {
    if (!supports(screen)) return 'list';
    try { return normalizeLayout(storage && storage.getItem(storageKey(screen))); } catch (e) { return 'list'; }
  }
  function saveLayout(storage, screen, layout) {
    if (!supports(screen)) return;
    try { if (storage) storage.setItem(storageKey(screen), normalizeLayout(layout)); } catch (e) { /* 保存できなくても表示は切り替える */ }
  }

  /* かんばんの列：アプリの作業状況の順（下書きは投函前なので列にしない）。
     現場の作業一覧は発送済を一覧から外すが、移動先として発送済の列を置く。
     作業状況で絞り込んでいるときはその列だけ。 */
  function kanbanColumns(screen, statusFilter) {
    const cols = ['未着手', '作業中', '発送済'];
    if (statusFilter) return [statusFilter];
    return cols;
  }
  function groupByStatus(list, columns) {
    const map = new Map(columns.map((c) => [c, []]));
    for (const o of list) { if (map.has(o.status)) map.get(o.status).push(o); }
    return map;
  }
  function dueClass(date, todayStr, status) {
    if (!date || !OPEN_STATES.includes(status)) return '';
    if (date < todayStr) return 'overdue';
    if (date === todayStr) return 'today';
    return '';
  }
  function dayKey(o, key) { return String((o && o[key]) || '').slice(0, 10); }

  /* 作業状況の変更計画。プルダウンと同じ条件（主管理者のみ・下書きは対象外・同じ状況は何もしない）。 */
  function planStatusMove(order, target, user, states) {
    if (!order) return { ok: false, error: '指示書が見つかりません。' };
    if (!user || user.role !== 'owner') return { ok: false, error: '作業状況の修正は主管理者だけができます。' };
    if (!Array.isArray(states) || !states.includes(target) || target === '下書き') return { ok: false, error: '作業状況を選択してください。' };
    if (order.status === '下書き') return { ok: false, error: '下書きの投函は指示書一覧または編集画面から行ってください。' };
    if (order.status === target) return { ok: false, same: true };
    return { ok: true, path: STATUS_API, payload: { id: order.id, version: order.version, status: target } };
  }
  async function moveStatus(apiFn, order, target, user, states) {
    const plan = planStatusMove(order, target, user, states);
    if (!plan.ok) {
      if (plan.same) return { changed: false };
      throw new Error(plan.error);
    }
    const result = await apiFn(plan.path, plan.payload);
    return { changed: true, result };
  }

  function switchHtml(current) {
    return '<span class="dv-switch-caption">見せ方</span>' + LAYOUTS.map((l) =>
      `<button type="button" class="dv-switch-btn${l.id === current ? ' on' : ''}" data-desk-layout="${l.id}" aria-pressed="${l.id === current}">${l.label}</button>`
    ).join('');
  }

  function kanbanCardHtml(o, opts) {
    const due = dayKey(o, opts.dateKey);
    const cls = dueClass(due, opts.today, o.status);
    const draggable = opts.canDrag && o.status !== '下書き';
    const statusCtl = typeof opts.statusHtml === 'function' ? opts.statusHtml(o) : '';
    return `<article class="dv-card${cls ? ' dv-' + cls : ''}" data-id="${o.id}"${draggable ? ' draggable="true"' : ''}>` +
      `<div class="dv-card-number">${esc(o.number) || '—'}${o.sample ? ' ・サンプル' : ''}</div>` +
      `<div class="dv-card-customer">${esc(o.customer) || '顧客未設定'}</div>` +
      `<div class="dv-card-meta"><span>${esc(o.employee) || '—'}</span><span class="dv-card-request">${esc(o.request) || '—'}</span></div>` +
      `<div class="dv-card-due">${esc(opts.dateLabel)} ${esc(due) || '未定'}${cls === 'overdue' ? ' <b>期限超過</b>' : cls === 'today' ? ' <b>本日</b>' : ''}</div>` +
      `<div class="dv-card-actions">${statusCtl}<button type="button" class="quiet open" data-id="${o.id}">開く</button></div>` +
      `</article>`;
  }
  function kanbanHtml(list, opts) {
    const columns = kanbanColumns(opts.screen, opts.statusFilter);
    const groups = groupByStatus(list, columns);
    const note = opts.canDrag ? 'カードを別の列へドラッグすると作業状況を変更します（カード内の作業状況でも変更できます）。' : '作業状況は作業の進み（ピッキングリストの作成・読み取り・発送など）に合わせて自動で切り替わります。カードの移動と作業状況の手直しは主管理者だけができます。';
    return `<p class="dv-note">${note}</p><div class="dv-kanban">` + columns.map((c) => {
      const rows = groups.get(c) || [];
      const hint = opts.screen === 'floor' && c === '発送済' ? '<p class="dv-col-hint">ここへ移すと現場の作業一覧から外れます。</p>' : '';
      return `<section class="dv-col" data-status="${esc(c)}"><h3>${esc(c)}<span>${rows.length} 件</span></h3>${hint}<div class="dv-col-body">` +
        (rows.length ? rows.map((o) => kanbanCardHtml(o, opts)).join('') : '<p class="dv-empty">なし</p>') + '</div></section>';
    }).join('') + '</div>';
  }

  function pad(n) { return String(n).padStart(2, '0'); }
  function ymd(y, m, d) { return `${y}-${pad(m)}-${pad(d)}`; }
  function shiftMonth(month, delta) {
    const [y, m] = String(month).split('-').map(Number);
    const t = new Date(y, m - 1 + delta, 1);
    return `${t.getFullYear()}-${pad(t.getMonth() + 1)}`;
  }
  /* 日曜始まりの週ごとのマス。前後月の日は inMonth:false。 */
  function monthGrid(month) {
    const [y, m] = String(month).split('-').map(Number);
    const first = new Date(y, m - 1, 1);
    const start = new Date(y, m - 1, 1 - first.getDay());
    const last = new Date(y, m, 0);
    const end = new Date(y, m - 1, last.getDate() + (6 - last.getDay()));
    const weeks = [];
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      if (!weeks.length || weeks[weeks.length - 1].length === 7) weeks.push([]);
      weeks[weeks.length - 1].push({ date: ymd(d.getFullYear(), d.getMonth() + 1, d.getDate()), day: d.getDate(), inMonth: d.getMonth() === m - 1 });
    }
    return weeks;
  }
  function calendarBuckets(list, key) {
    const map = new Map();
    for (const o of list) {
      const k = dayKey(o, key);
      if (!k) continue;
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(o);
    }
    return map;
  }
  function calendarHtml(list, opts) {
    const month = opts.month;
    const [y, m] = month.split('-').map(Number);
    const buckets = calendarBuckets(list, opts.dateKey);
    const undated = list.filter((o) => !dayKey(o, opts.dateKey)).length;
    const max = opts.maxItems || CALENDAR_MAX_ITEMS;
    const head = `<div class="dv-cal-head"><button type="button" class="quiet" data-cal-move="-1" aria-label="前の月">‹ 前の月</button><strong>${y}年${m}月</strong><button type="button" class="quiet" data-cal-move="1" aria-label="次の月">次の月 ›</button><button type="button" class="quiet" data-cal-move="0">今月</button><span class="dv-note">${esc(opts.dateLabel)}で表示${undated ? `／日付未定 ${undated} 件` : ''}</span></div>`;
    const week = '<div class="dv-cal-row dv-cal-week">' + WEEKDAYS.map((w, i) => `<div class="dv-cal-wd${i === 0 ? ' sun' : i === 6 ? ' sat' : ''}">${w}</div>`).join('') + '</div>';
    const rows = monthGrid(month).map((wk) => '<div class="dv-cal-row">' + wk.map((c) => {
      const items = buckets.get(c.date) || [];
      const more = Math.max(0, items.length - max);
      const cls = ['dv-cal-day', c.inMonth ? '' : 'out', c.date === opts.today ? 'today' : ''].filter(Boolean).join(' ');
      return `<div class="${cls}" data-date="${c.date}"><div class="dv-cal-num">${c.day}</div>` +
        items.map((o, i) => {
          const dc = dueClass(c.date, opts.today, o.status);
          return `<button type="button" class="dv-cal-item open${dc ? ' dv-' + dc : ''}${i >= max ? ' dv-cal-extra' : ''}" data-id="${o.id}" title="${esc(o.number)} ${esc(o.customer)} ${esc(o.employee)} ${esc(o.status)}"><span class="number">${esc(o.number) || '—'}</span> ${esc(o.customer) || '顧客未設定'}</button>`;
        }).join('') +
        (more > 0 ? `<button type="button" class="dv-cal-more" data-date="${c.date}" data-label="+${more}件">+${more}件</button>` : '') + '</div>';
    }).join('') + '</div>').join('');
    return head + `<div class="dv-cal">${week}${rows}</div>`;
  }

  /* 画面専用のCSSを読み込む（index.html の head は他の変更と重なりやすいので、ここで足す）。 */
  if (typeof document !== 'undefined' && document.head && !document.getElementById('desk-views-css')) {
    const link = document.createElement('link');
    link.id = 'desk-views-css';
    link.rel = 'stylesheet';
    link.href = '/desk-views.css?v=20261126_kanban_owner';
    document.head.appendChild(link);
  }

  return {
    LAYOUTS, SCREENS, STATUS_API, CALENDAR_MAX_ITEMS,
    storageKey, normalizeLayout, supports, loadLayout, saveLayout,
    kanbanColumns, groupByStatus, dueClass, planStatusMove, moveStatus,
    switchHtml, kanbanCardHtml, kanbanHtml, shiftMonth, monthGrid, calendarBuckets, calendarHtml,
  };
});
