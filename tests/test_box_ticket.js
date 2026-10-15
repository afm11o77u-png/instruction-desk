'use strict'
const assert = require('assert')
const path = require('path')
const {
  BOX_TICKET_CSS,
  BOX_TICKET_KINDS,
  boxTicketPages,
  boxTicketPreviewHtml,
  buildManualBoxTicketOrder,
  validateManualBoxTicketOrder,
  manualBoxTicketIsPrintOnly,
  manualBoxTicketMutatesOrders,
  activeBoxTicketShortages,
} = require(path.join(__dirname, '../box_ticket.js'))

assert.ok(BOX_TICKET_CSS.includes('A4 landscape'))
assert.ok(BOX_TICKET_CSS.includes('136.5mm'))
assert.deepStrictEqual(BOX_TICKET_KINDS, ['repair', 'shortage'])
assert.strictEqual(manualBoxTicketIsPrintOnly(), true)
assert.strictEqual(manualBoxTicketMutatesOrders(), false)

// Repair ticket: empty shortages → 不足なし（修理待ち）
const repair = buildManualBoxTicketOrder({
  kind: 'repair',
  customer: 'あおば病院',
  employee: '山田太郎',
  number: 'SI-000100',
  request: '消耗・破損交換',
  markShipDate: '2026-10-20',
  shortages: [{ name: '無視される', qty: 2 }],
})
assert.strictEqual(repair.printOnly, true)
assert.deepStrictEqual(repair.shortages, [])
const repairPages = boxTicketPages(repair)
assert.strictEqual(repairPages.length, 1)
assert.ok(repairPages[0].includes('あおば病院'))
assert.ok(repairPages[0].includes('山田太郎'))
assert.ok(repairPages[0].includes('SI-000100'))
assert.ok(repairPages[0].includes('消耗・破損交換'))
assert.ok(repairPages[0].includes('2026-10-20'))
assert.ok(repairPages[0].includes('不足なし（修理待ち）'))
assert.ok(!repairPages[0].includes('無視される'))

// Shortage ticket: chunks of 3
const shortage = buildManualBoxTicketOrder({
  kind: 'shortage',
  customer: 'かしわ食品',
  employee: '佐藤花子',
  number: 'SI-000200',
  request: '追加・補充',
  markShipDate: '2026-11-01',
  shortages: [
    { name: 'シャツ', sku: 'A1', size: 'M', qty: 2 },
    { name: 'ズボン', sku: 'B2', size: 'L', qty: 1 },
    { name: '帽子', sku: '', size: 'F', qty: 3 },
    { name: '靴', sku: 'C3', size: '26', qty: 1 },
    { name: 'ゼロ', qty: 0 },
    { name: '入荷済み扱い', qty: 5, supply: '入荷済み' },
  ],
})
assert.strictEqual(activeBoxTicketShortages(shortage).length, 4)
const shortagePages = boxTicketPages(shortage)
assert.strictEqual(shortagePages.length, 2)
assert.ok(shortagePages[0].includes('シャツ / A1 / M'))
assert.ok(shortagePages[0].includes('不足 2 枚'))
assert.ok(shortagePages[0].includes('1/2'))
assert.ok(shortagePages[1].includes('靴 / C3 / 26'))
assert.ok(shortagePages[1].includes('2/2'))
assert.ok(!shortagePages.join('').includes('ゼロ'))
assert.ok(!shortagePages.join('').includes('入荷済み扱い'))

const ok = validateManualBoxTicketOrder(shortage)
assert.strictEqual(ok.ok, true)

const badRepair = validateManualBoxTicketOrder(buildManualBoxTicketOrder({ kind: 'repair', customer: '', employee: 'a', number: '1' }))
assert.strictEqual(badRepair.ok, false)

const badShortage = validateManualBoxTicketOrder(
  buildManualBoxTicketOrder({
    kind: 'shortage',
    customer: '客',
    employee: '名',
    number: 'SI-1',
    shortages: [],
  })
)
assert.strictEqual(badShortage.ok, false)
assert.ok(badShortage.error.includes('不足商品'))

const html = boxTicketPreviewHtml(shortagePages)
assert.ok(html.includes('A4 landscape'))
assert.ok(html.includes('class="page"'))
assert.ok(html.includes('ボックス管理票'))
assert.strictEqual((html.match(/class="page"/g) || []).length, 1)

// Escaping
const escaped = boxTicketPages({
  customer: '<b>x</b>',
  employee: 'a&b',
  number: '"n"',
  request: '',
  markShipDate: '',
  shortages: [],
})[0]
assert.ok(escaped.includes('&lt;b&gt;x&lt;/b&gt;'))
assert.ok(escaped.includes('a&amp;b'))
assert.ok(escaped.includes('&quot;n&quot;'))

console.log('test_box_ticket.js: ok')
