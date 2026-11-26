'use strict'
const assert = require('assert')
const path = require('path')
const DV = require(path.join(__dirname, '..', 'desk-views.js'))
const states = ['下書き', '未着手', '作業中', '発送済', '完了']
const owner = { role: 'owner' }

;(async () => {
  // ドラッグの作業状況変更は既存プルダウンと同じAPI・版数を送る
  const calls = []
  const fakeApi = async (p, d) => { calls.push([p, d]); return { id: d.id } }
  const order = { id: 7, version: 3, status: '未着手' }
  const r = await DV.moveStatus(fakeApi, order, '作業中', owner, states)
  assert.strictEqual(r.changed, true)
  assert.deepStrictEqual(calls, [['/api/orders/status-flag', { id: 7, version: 3, status: '作業中' }]])

  // 同じ列・主管理者以外・下書きは送らない
  calls.length = 0
  assert.deepStrictEqual(await DV.moveStatus(fakeApi, order, '未着手', owner, states), { changed: false })
  await assert.rejects(DV.moveStatus(fakeApi, order, '作業中', { role: 'sales' }, states), /主管理者だけ/)
  await assert.rejects(DV.moveStatus(fakeApi, { id: 1, version: 1, status: '下書き' }, '未着手', owner, states), /下書き/)
  await assert.rejects(DV.moveStatus(fakeApi, order, '下書き', owner, states), /選択/)
  assert.strictEqual(calls.length, 0)

  // API失敗はそのまま投げる（画面側で元に戻してメッセージ表示）
  await assert.rejects(DV.moveStatus(async () => { throw new Error('別の画面で更新されています。') }, order, '発送済', owner, states), /別の画面/)

  // 表示の記憶は画面ごと、未対応の画面・不正値は一覧
  const mem = {}; const store = { getItem: (k) => mem[k] ?? null, setItem: (k, v) => { mem[k] = v } }
  DV.saveLayout(store, 'floor', 'kanban')
  assert.strictEqual(DV.loadLayout(store, 'floor'), 'kanban')
  assert.strictEqual(DV.loadLayout(store, 'orders'), 'list')
  store.setItem('deskLayout:orders', 'bogus')
  assert.strictEqual(DV.loadLayout(store, 'orders'), 'list')
  assert.strictEqual(DV.loadLayout(store, 'drafts'), 'list')

  // かんばん：列と期日の強調
  assert.deepStrictEqual(DV.kanbanColumns('orders', ''), ['未着手', '作業中', '発送済'])
  assert.deepStrictEqual(DV.kanbanColumns('floor', '作業中'), ['作業中'])
  assert.strictEqual(DV.dueClass('2026-11-25', '2026-11-26', '作業中'), 'overdue')
  assert.strictEqual(DV.dueClass('2026-11-26', '2026-11-26', '未着手'), 'today')
  assert.strictEqual(DV.dueClass('2026-11-25', '2026-11-26', '発送済'), '')
  const list = [
    { id: 1, version: 1, status: '未着手', number: 'A-1', customer: '<顧客>', employee: '山田', request: '新規', markShipDate: '2026-11-20' },
    { id: 2, version: 1, status: '作業中', number: 'A-2', customer: '乙', employee: '佐藤', request: '交換', markShipDate: '2026-11-26' },
  ]
  const html = DV.kanbanHtml(list, { screen: 'orders', statusFilter: '', dateKey: 'markShipDate', dateLabel: 'マーク出荷日', today: '2026-11-26', canDrag: true })
  assert.ok(html.includes('&lt;顧客&gt;') && !html.includes('<顧客>'))
  assert.ok(html.includes('data-status="発送済"'))
  assert.ok(/dv-overdue[^>]*data-id="1" draggable="true"/.test(html))
  assert.ok(html.includes('class="quiet open" data-id="2"'))
  assert.ok(!DV.kanbanHtml(list, { screen: 'orders', dateKey: 'markShipDate', today: '2026-11-26', canDrag: false }).includes('draggable'))
  const ro = DV.kanbanHtml(list, { screen: 'orders', dateKey: 'markShipDate', today: '2026-11-26', canDrag: false, statusHtml: (o) => `<span class="dv-status-label">${o.status}</span>` })
  assert.ok(ro.includes('主管理者だけ') && ro.includes('自動で切り替わります') && !ro.includes('<select'))

  // カレンダー：日曜始まり、今日の強調、件数あふれ
  const weeks = DV.monthGrid('2026-11')
  assert.strictEqual(weeks[0][0].date, '2026-11-01')
  assert.ok(weeks.every((w) => w.length === 7))
  assert.strictEqual(DV.shiftMonth('2026-12', 1), '2027-01')
  assert.strictEqual(DV.shiftMonth('2026-01', -1), '2025-12')
  const many = Array.from({ length: 5 }, (_, i) => ({ id: 10 + i, status: '未着手', number: 'N' + i, customer: 'C', received: '2026-11-26' }))
  const cal = DV.calendarHtml(many, { month: '2026-11', dateKey: 'received', dateLabel: '受付日', today: '2026-11-26' })
  assert.ok(cal.includes('class="dv-cal-day today" data-date="2026-11-26"'))
  assert.ok(cal.includes('+2件'))
  assert.strictEqual((cal.match(/dv-cal-extra/g) || []).length, 2)
  assert.ok(cal.includes('2026年11月'))
  console.log('ok')
})().catch((e) => { console.error(e); process.exit(1) })
