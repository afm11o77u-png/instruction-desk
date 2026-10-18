'use strict'
const assert = require('assert')
const path = require('path')
const {
  BOX_TICKET_CSS,
  BOX_TICKET_KINDS,
  BOX_TICKET_PER_PAGE,
  BOX_TICKET_MANUAL_MAX_PEOPLE,
  BOX_TICKET_EMPTY_SLOT,
  boxTicketPages,
  boxTicketPreviewHtml,
  boxTicketPageCount,
  boxTicketInstructionNumber,
  buildManualBoxTicketOrder,
  buildManualBoxTicketOrders,
  validateManualBoxTicketOrder,
  isManualBoxTicketPersonBlank,
  manualBoxTicketIsPrintOnly,
  manualBoxTicketMutatesOrders,
  activeBoxTicketShortages,
  boxTicketOrderDateLabel,
  boxTicketToday,
} = require(path.join(__dirname, '../box_ticket.js'))

assert.strictEqual(BOX_TICKET_PER_PAGE, 6)
assert.strictEqual(BOX_TICKET_MANUAL_MAX_PEOPLE, 6)
assert.ok(BOX_TICKET_EMPTY_SLOT.includes('ticket-empty'))
assert.ok(BOX_TICKET_CSS.includes('A4 landscape'))
assert.ok(BOX_TICKET_CSS.includes('@page{size:A4 landscape;margin:0}'))
assert.ok(BOX_TICKET_CSS.includes('width:297mm'))
assert.ok(BOX_TICKET_CSS.includes('height:210mm'))
assert.ok(BOX_TICKET_CSS.includes('148.5mm'))
assert.ok(BOX_TICKET_CSS.includes('70mm'))
assert.ok(!BOX_TICKET_CSS.includes('52.5mm'))
assert.ok(!BOX_TICKET_CSS.includes('136.5mm'))
assert.ok(!BOX_TICKET_CSS.includes('46.5mm'))
assert.ok(!BOX_TICKET_CSS.includes('273mm'))
assert.ok(!BOX_TICKET_CSS.includes('186mm'))
assert.ok(!BOX_TICKET_CSS.includes('margin:18mm'))
assert.deepStrictEqual(BOX_TICKET_KINDS, ['repair', 'shortage'])
assert.strictEqual(manualBoxTicketIsPrintOnly(), true)
assert.strictEqual(manualBoxTicketMutatesOrders(), false)

// Font sizes enlarged for 70mm cells (esp. customer / person)
assert.ok(BOX_TICKET_CSS.includes('font-size:13.5pt'))
assert.ok(BOX_TICKET_CSS.includes('font-size:18pt'))
assert.ok(BOX_TICKET_CSS.includes('font-size:16.5pt'))
assert.ok(BOX_TICKET_CSS.includes('font-size:11.5pt'))

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
assert.strictEqual(cssProp('.ticket', 'height'), '70mm')
assert.ok(BOX_TICKET_CSS.includes('grid-template-columns:repeat(2,148.5mm)'))
assert.ok(BOX_TICKET_CSS.includes('grid-template-rows:repeat(3,70mm)'))
assert.ok(!BOX_TICKET_CSS.includes('repeat(4,'))
// 2×3 cell = full sheet
assert.strictEqual(148.5 * 2, 297)
assert.strictEqual(70 * 3, 210)
assert.ok(BOX_TICKET_CSS.includes('.instr{'))
assert.ok(BOX_TICKET_CSS.includes('min-width:34mm'))

function sampleTicket(i, extras) {
  return boxTicketPages({
    customer: `顧客${i}`,
    employee: `従業員${i}`,
    number: `SI-${String(i).padStart(6, '0')}`,
    request: '消耗・破損交換',
    markShipDate: '2026-10-20',
    shortages: [],
    ...(extras || {}),
  })[0]
}

function countPages(html) {
  return (html.match(/class="page"/g) || []).length
}
function countFilledTickets(html) {
  return (html.match(/<section class="ticket">/g) || []).length
}
function countEmptySlots(html) {
  return (html.match(/<section class="ticket ticket-empty"/g) || []).length
}

// 1 / 6 / 7 tickets → 1 / 1 / 2 pages; unused slots padded empty
const one = [sampleTicket(1)]
const six = Array.from({ length: 6 }, (_, i) => sampleTicket(i + 1))
const seven = Array.from({ length: 7 }, (_, i) => sampleTicket(i + 1))
assert.strictEqual(boxTicketPageCount(1), 1)
assert.strictEqual(boxTicketPageCount(6), 1)
assert.strictEqual(boxTicketPageCount(7), 2)
assert.strictEqual(countPages(boxTicketPreviewHtml(one)), 1)
assert.strictEqual(countPages(boxTicketPreviewHtml(six)), 1)
assert.strictEqual(countPages(boxTicketPreviewHtml(seven)), 2)
assert.strictEqual(countFilledTickets(boxTicketPreviewHtml(one)), 1)
assert.strictEqual(countEmptySlots(boxTicketPreviewHtml(one)), 5)
assert.strictEqual(countFilledTickets(boxTicketPreviewHtml(six)), 6)
assert.strictEqual(countEmptySlots(boxTicketPreviewHtml(six)), 0)
assert.strictEqual(countFilledTickets(boxTicketPreviewHtml(seven)), 7)
assert.strictEqual(countEmptySlots(boxTicketPreviewHtml(seven)), 5)
// Empty slots are border-only shells (no customer / order date / labels inside)
assert.ok(BOX_TICKET_EMPTY_SLOT.includes('ticket-empty'))
assert.ok(!BOX_TICKET_EMPTY_SLOT.includes('マーク出荷日'))
assert.ok(!BOX_TICKET_EMPTY_SLOT.includes('発注'))
assert.ok(!BOX_TICKET_EMPTY_SLOT.includes('不足商品'))
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
assert.ok(longNames[0].includes('SI-000999'))
assert.ok(longNames[0].includes('class="instr"'))

const fixedNow = new Date(2026, 9, 18, 12, 0, 0)
assert.strictEqual(boxTicketToday(fixedNow), '2026-10-18')
assert.strictEqual(boxTicketOrderDateLabel(fixedNow), '2026-10-18 発注')

// Site order: number → 右上指示書番号、日付＋発注は別
const sitePages = boxTicketPages({
  customer: '現場顧客',
  employee: '現場太郎',
  number: 'SI-777001',
  now: fixedNow,
  request: '消耗・破損交換',
  markShipDate: '2026-10-20',
  shortages: [],
})
assert.strictEqual(sitePages.length, 1)
assert.ok(sitePages[0].includes('SI-777001'))
assert.ok(sitePages[0].includes('2026-10-18 発注'))
assert.ok(sitePages[0].includes('class="instr">SI-777001'))
assert.strictEqual(boxTicketInstructionNumber({ number: 'SI-777001' }), 'SI-777001')

// Repair manual: 日付＋発注 + optional instruction number
const repair = buildManualBoxTicketOrder({
  kind: 'repair',
  customer: 'あおば病院',
  employee: '山田太郎',
  instructionNumber: 'SI-000100',
  now: fixedNow,
  request: '消耗・破損交換',
  markShipDate: '2026-10-20',
  shortages: [{ name: '無視される', qty: 2 }],
})
assert.strictEqual(repair.printOnly, true)
assert.strictEqual(repair.orderDateLabel, '2026-10-18 発注')
assert.strictEqual(repair.instructionNumber, 'SI-000100')
assert.deepStrictEqual(repair.shortages, [])
const repairPages = boxTicketPages(repair)
assert.strictEqual(repairPages.length, 1)
assert.ok(repairPages[0].includes('あおば病院'))
assert.ok(repairPages[0].includes('山田太郎'))
assert.ok(repairPages[0].includes('2026-10-18 発注'))
assert.ok(repairPages[0].includes('SI-000100'))
assert.ok(repairPages[0].includes('消耗・破損交換'))
assert.ok(repairPages[0].includes('不足なし（修理待ち）'))
assert.ok(!repairPages[0].includes('無視される'))

// Manual without instruction number: instr area blank (no 未入力 / —)
const noInstr = buildManualBoxTicketOrder({
  kind: 'repair',
  customer: '客',
  employee: '名',
  now: fixedNow,
})
const noInstrHtml = boxTicketPages(noInstr)[0]
assert.ok(noInstrHtml.includes('class="instr"></div>'))
assert.ok(!noInstrHtml.includes('未入力'))
assert.ok(!/class="instr">—</.test(noInstrHtml))
assert.ok(noInstrHtml.includes('2026-10-18 発注'))

// Shortage ticket: chunks of 3 → continuation marker with order date
const shortage = buildManualBoxTicketOrder({
  kind: 'shortage',
  customer: 'かしわ食品',
  employee: '佐藤花子',
  instructionNumber: 'SI-000200',
  now: fixedNow,
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
assert.strictEqual(shortage.orderDateLabel, '2026-10-18 発注')
assert.strictEqual(shortage.instructionNumber, 'SI-000200')
assert.strictEqual(activeBoxTicketShortages(shortage).length, 4)
const shortagePages = boxTicketPages(shortage)
assert.strictEqual(shortagePages.length, 2)
assert.ok(shortagePages[0].includes('シャツ / A1 / M'))
assert.ok(shortagePages[0].includes('不足 2 枚'))
assert.ok(shortagePages[0].includes('2026-10-18 発注'))
assert.ok(shortagePages[0].includes('1/2'))
assert.ok(shortagePages[0].includes('SI-000200'))
assert.ok(shortagePages[1].includes('靴 / C3 / 26'))
assert.ok(shortagePages[1].includes('2/2'))
assert.ok(!shortagePages.join('').includes('ゼロ'))
assert.ok(!shortagePages.join('').includes('入荷済み扱い'))

const ok = validateManualBoxTicketOrder(shortage)
assert.strictEqual(ok.ok, true)

const badRepair = validateManualBoxTicketOrder(buildManualBoxTicketOrder({ kind: 'repair', customer: '', employee: 'a' }))
assert.strictEqual(badRepair.ok, false)

const noNumberNeeded = validateManualBoxTicketOrder(
  buildManualBoxTicketOrder({
    kind: 'repair',
    customer: '客',
    employee: '名',
    now: fixedNow,
  })
)
assert.strictEqual(noNumberNeeded.ok, true)
assert.strictEqual(noNumberNeeded.order.instructionNumber, '')

const badShortage = validateManualBoxTicketOrder(
  buildManualBoxTicketOrder({
    kind: 'shortage',
    customer: '客',
    employee: '名',
    shortages: [],
  })
)
assert.strictEqual(badShortage.ok, false)
assert.ok(badShortage.error.includes('不足商品'))

// Multi-person: blank skipped, partial invalid, max 6
assert.strictEqual(isManualBoxTicketPersonBlank({ customer: '', employee: '' }), true)
const multi = buildManualBoxTicketOrders(
  [
    { customer: 'A社', employee: '一郎', instructionNumber: 'N1' },
    { customer: '', employee: '' },
    { customer: 'B社', employee: '二郎' },
  ],
  { kind: 'repair', now: fixedNow }
)
assert.strictEqual(multi.ok, true)
assert.strictEqual(multi.orders.length, 2)
assert.strictEqual(multi.orders[0].instructionNumber, 'N1')
assert.strictEqual(multi.orders[1].instructionNumber, '')

const partial = buildManualBoxTicketOrders(
  [{ customer: '途中', employee: '' }],
  { kind: 'repair', now: fixedNow }
)
assert.strictEqual(partial.ok, false)
assert.ok(partial.error.includes('1人目'))

const none = buildManualBoxTicketOrders([{}, {}], { kind: 'repair', now: fixedNow })
assert.strictEqual(none.ok, false)
assert.ok(none.error.includes('1人分以上'))

const multiTickets = multi.orders.flatMap((o) => boxTicketPages(o))
assert.strictEqual(multiTickets.length, 2)
const multiHtml = boxTicketPreviewHtml(multiTickets)
assert.strictEqual(countPages(multiHtml), 1)
assert.strictEqual(countEmptySlots(multiHtml), 4)

// 6 people + shortage split: person count ≠ ticket count
const sixPeopleShortage = buildManualBoxTicketOrders(
  Array.from({ length: 6 }, (_, i) => ({
    customer: `客${i + 1}`,
    employee: `名${i + 1}`,
    shortages:
      i === 0
        ? [
            { name: 'a', qty: 1 },
            { name: 'b', qty: 1 },
            { name: 'c', qty: 1 },
            { name: 'd', qty: 1 },
          ]
        : [{ name: 'x', qty: 1 }],
  })),
  { kind: 'shortage', now: fixedNow }
)
assert.strictEqual(sixPeopleShortage.ok, true)
assert.strictEqual(sixPeopleShortage.orders.length, 6)
const sixPeopleTickets = sixPeopleShortage.orders.flatMap((o) => boxTicketPages(o))
assert.strictEqual(sixPeopleTickets.length, 7) // first person splits to 2
assert.strictEqual(boxTicketPageCount(sixPeopleTickets.length), 2)

const html = boxTicketPreviewHtml(shortagePages)
assert.ok(html.includes('A4 landscape'))
assert.ok(html.includes('class="page"'))
assert.ok(html.includes('ボックス管理票'))
assert.strictEqual(countPages(html), 1)
assert.ok(html.includes('148.5mm'))
assert.ok(html.includes('70mm'))
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
