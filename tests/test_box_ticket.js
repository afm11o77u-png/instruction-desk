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
assert.ok(BOX_TICKET_CSS.includes('@page{size:A4 landscape;margin:0}'))
assert.ok(BOX_TICKET_CSS.includes('width:297mm'))
assert.ok(BOX_TICKET_CSS.includes('height:210mm'))
assert.ok(BOX_TICKET_CSS.includes('148.5mm'))
assert.ok(BOX_TICKET_CSS.includes('52.5mm'))
assert.ok(!BOX_TICKET_CSS.includes('136.5mm'))
assert.ok(!BOX_TICKET_CSS.includes('46.5mm'))
assert.ok(!BOX_TICKET_CSS.includes('273mm'))
assert.ok(!BOX_TICKET_CSS.includes('186mm'))
assert.ok(!BOX_TICKET_CSS.includes('margin:18mm'))
assert.deepStrictEqual(BOX_TICKET_KINDS, ['repair', 'shortage'])
assert.strictEqual(manualBoxTicketIsPrintOnly(), true)
assert.strictEqual(manualBoxTicketMutatesOrders(), false)

// Font sizes ~10–13% larger than previous 10/13/12/9/10 pt
assert.ok(BOX_TICKET_CSS.includes('font-size:11.2pt'))
assert.ok(BOX_TICKET_CSS.includes('font-size:14.5pt'))
assert.ok(BOX_TICKET_CSS.includes('font-size:13.5pt'))
assert.ok(BOX_TICKET_CSS.includes('font-size:10pt'))

function cssBlock(selector) {
  const start = BOX_TICKET_CSS.indexOf(selector + '{')
  assert.ok(start >= 0, `missing block ${selector}`)
  const end = BOX_TICKET_CSS.indexOf('}', start)
  return BOX_TICKET_CSS.slice(start, end + 1)
}
function cssProp(selector, prop) {
  const m = cssBlock(selector).match(new RegExp(prop + ':([^;}]+)'))
  assert.ok(m, `missing ${prop} in ${selector}`)
  return m[1].trim()
}

assert.strictEqual(cssProp('.page', 'width'), '297mm')
assert.strictEqual(cssProp('.page', 'height'), '210mm')
assert.strictEqual(cssProp('.ticket', 'width'), '148.5mm')
assert.strictEqual(cssProp('.ticket', 'height'), '52.5mm')
assert.ok(BOX_TICKET_CSS.includes('grid-template-columns:repeat(2,148.5mm)'))
assert.ok(BOX_TICKET_CSS.includes('grid-template-rows:repeat(4,52.5mm)'))
// 2×4× cell = full sheet
assert.strictEqual(148.5 * 2, 297)
assert.strictEqual(52.5 * 4, 210)

function sampleTicket(i) {
  return boxTicketPages({
    customer: `顧客${i}`,
    employee: `従業員${i}`,
    number: `SI-${String(i).padStart(6, '0')}`,
    request: '消耗・破損交換',
    markShipDate: '2026-10-20',
    shortages: [],
  })[0]
}

// 1 / 8 / 9 tickets → 1 / 1 / 2 pages; ticket size CSS unchanged
const one = [sampleTicket(1)]
const eight = Array.from({ length: 8 }, (_, i) => sampleTicket(i + 1))
const nine = Array.from({ length: 9 }, (_, i) => sampleTicket(i + 1))
assert.strictEqual((boxTicketPreviewHtml(one).match(/class="page"/g) || []).length, 1)
assert.strictEqual((boxTicketPreviewHtml(eight).match(/class="page"/g) || []).length, 1)
assert.strictEqual((boxTicketPreviewHtml(nine).match(/class="page"/g) || []).length, 2)
assert.strictEqual((boxTicketPreviewHtml(one).match(/class="ticket"/g) || []).length, 1)
assert.strictEqual((boxTicketPreviewHtml(eight).match(/class="ticket"/g) || []).length, 8)
assert.strictEqual((boxTicketPreviewHtml(nine).match(/class="ticket"/g) || []).length, 9)
// Partial page still uses full cell size in CSS (no shrink rules)
assert.ok(!BOX_TICKET_CSS.includes('scale'))
assert.ok(!BOX_TICKET_CSS.includes('transform'))
assert.ok(!/zoom\s*:/.test(BOX_TICKET_CSS))

// Long names / 3 shortage lines still rendered (no content dropped)
const longNames = boxTicketPages({
  customer: 'とても長い顧客名株式会社あいうえお病院センター東口クリニック第二分院',
  employee: 'とても長い従業員名のサンプル太郎次郎',
  number: 'SI-000999',
  request: '追加・補充',
  markShipDate: '2026-11-01',
  shortages: [
    { name: '超長い商品名シャツホワイトストライプ', sku: 'SKU-AAAA-1111', size: 'LL', qty: 2 },
    { name: '超長い商品名ズボンネイビー', sku: 'SKU-BBBB-2222', size: '88', qty: 1 },
    { name: '超長い商品名帽子フリー', sku: 'SKU-CCCC-3333', size: 'F', qty: 3 },
  ],
})
assert.strictEqual(longNames.length, 1)
assert.ok(longNames[0].includes('とても長い顧客名'))
assert.ok(longNames[0].includes('とても長い従業員名'))
assert.ok(longNames[0].includes('超長い商品名シャツ'))
assert.ok(longNames[0].includes('超長い商品名ズボン'))
assert.ok(longNames[0].includes('超長い商品名帽子'))
assert.ok(longNames[0].includes('不足 2 枚'))
assert.ok(longNames[0].includes('不足 3 枚'))

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
assert.ok(html.includes('148.5mm'))
assert.ok(html.includes('297mm'))

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
