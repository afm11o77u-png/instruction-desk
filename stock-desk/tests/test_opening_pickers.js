'use strict'
const assert = require('assert')
const path = require('path')
const {
  filterOpeningCustomers,
  filterOpeningOfferings,
  filterOpeningHandles,
  filterOpeningProducts,
  filterOpeningListEntries,
  openingHandlesFromData,
  clearOpeningProductFields,
  clearOpeningCustomerSelection,
  clearOpeningHandleSelection,
  reconcileOpeningLinesForProduct,
  selectOpeningHandle,
  openingStateAfterSuccessfulSave,
  inboundStateAfterSuccessfulSave,
  resolveConfirmedOpeningCustomer,
  resolveConfirmedOpeningProduct,
  resolveConfirmedOpeningOffering,
  resolveConfirmedOpeningHandle,
  offeringPickerLabel,
  handlePickerLabel,
  productPickerLabel
} = require(path.join(__dirname, '../static/opening_pickers.js'))

const listEntries = [
  {id: 1, customerName: 'あおば記念病院', code: 'SCR-100', productName: '白スクラブ', color: '白', basisDate: '2026-09-01'},
  {id: 2, customerName: 'かしわ食品 本店', code: 'PAN-200', productName: 'パンツ', color: '紺', basisDate: '2026-09-01'},
  {id: 3, customerName: '青葉商事', code: 'SCR-100', productName: '白スクラブ', color: '白', basisDate: '2026-08-01'}
]

const customers = [
  {id: 1, name: 'あおば記念病院', kana: 'アオバキネンビョウイン', active: true},
  {id: 2, name: 'かしわ食品 本店', kana: 'カシワショクホンテン', active: true},
  {id: 3, name: '停止クリニック', kana: 'テイシクリニック', active: false},
  {id: 4, name: '青葉商事', kana: 'アオバショウジ', active: true}
]

const data = {
  customers,
  products: [
    {
      id: 10, code: 'SCR-100', name: '白スクラブ', color: '白', active: true, supplierName: '北光繊維',
      sizes: ['M', 'L', 'LL'],
      links: [
        {id: 100, customerId: 1, embroidery: '', isStandard: true, active: true},
        {id: 101, customerId: 1, embroidery: '左胸ネーム', isStandard: false, active: true},
        {id: 102, customerId: 2, embroidery: '', isStandard: true, active: true},
        {id: 103, customerId: 1, embroidery: '停止仕様', isStandard: false, active: false}
      ]
    },
    {
      id: 11, code: 'PAN-200', name: 'パンツ', color: '紺', active: false, supplierName: '北光繊維',
      sizes: ['S', 'M'],
      links: [{id: 110, customerId: 1, embroidery: '', isStandard: true, active: true}]
    },
    {
      id: 12, code: 'JKT-1', name: 'ジャケット', color: '紺', active: true, supplierName: '札幌商会',
      sizes: ['M', '2L'],
      links: [{id: 120, customerId: 4, embroidery: '背中ネーム', isStandard: true, active: true}]
    }
  ]
}

function test(name, fn) {
  try {
    fn()
    console.log('OK', name)
  } catch (e) {
    console.error('NG', name)
    console.error(e)
    process.exitCode = 1
  }
}

test('filter by partial customer name', () => {
  const hit = filterOpeningCustomers(customers, 'あおば')
  assert.deepStrictEqual(hit.map(c => c.id), [1])
  const food = filterOpeningCustomers(customers, '食品')
  assert.deepStrictEqual(food.map(c => c.id), [2])
})

test('filter by registered kana', () => {
  const hit = filterOpeningCustomers(customers, 'カシワ')
  assert.strictEqual(hit.length, 1)
  assert.strictEqual(hit[0].id, 2)
  const aobaKana = filterOpeningCustomers(customers, 'アオバ')
  assert.deepStrictEqual(aobaKana.map(c => c.id).sort(), [1, 4])
})

test('only active customers are selectable', () => {
  const all = filterOpeningCustomers(customers, '')
  assert.ok(all.every(c => c.active))
  assert.ok(!all.some(c => c.id === 3))
  assert.strictEqual(resolveConfirmedOpeningCustomer(customers, 3), null)
  assert.ok(resolveConfirmedOpeningCustomer(customers, 1))
})

test('offerings scoped to selected customer and active only', () => {
  const handles = openingHandlesFromData(data)
  assert.ok(!handles.some(h => h.id === 103 || h.id === 110))
  const forAoba = filterOpeningOfferings(handles, 1, '')
  assert.deepStrictEqual(forAoba.map(h => h.id).sort(), [100, 101])
  const forOther = filterOpeningOfferings(handles, 2, '')
  assert.deepStrictEqual(forOther.map(h => h.id), [102])
  assert.deepStrictEqual(filterOpeningOfferings(handles, '', 'SCR'), [])
})

test('offerings searchable by code name and spec text', () => {
  const handles = openingHandlesFromData(data)
  assert.strictEqual(filterOpeningOfferings(handles, 1, 'SCR-100').length, 2)
  assert.strictEqual(filterOpeningOfferings(handles, 1, '左胸').length, 1)
  assert.strictEqual(filterOpeningOfferings(handles, 1, '白スクラブ').length, 2)
  assert.ok(offeringPickerLabel(filterOpeningOfferings(handles, 1, '左胸')[0]).includes('左胸ネーム'))
})

test('handles searchable across customers by name code product color processing', () => {
  const handles = openingHandlesFromData(data)
  assert.deepStrictEqual(filterOpeningHandles(handles, '').map(h => h.id).sort(), [100, 101, 102, 120])
  assert.deepStrictEqual(filterOpeningHandles(handles, 'あおば').map(h => h.id).sort(), [100, 101])
  assert.deepStrictEqual(filterOpeningHandles(handles, 'SCR-100').map(h => h.id).sort(), [100, 101, 102])
  assert.strictEqual(filterOpeningHandles(handles, '左胸').length, 1)
  assert.strictEqual(filterOpeningHandles(handles, '背中').length, 1)
  assert.strictEqual(filterOpeningHandles(handles, 'ジャケット').length, 1)
  assert.strictEqual(filterOpeningHandles(handles, '紺').map(h => h.id).includes(120), true)
  const label = handlePickerLabel(handles.find(h => h.id === 101))
  assert.ok(label.includes('あおば記念病院'))
  assert.ok(label.includes('SCR-100'))
  assert.ok(label.includes('左胸ネーム'))
  assert.ok(label.includes('／'))
})

test('product picker shows code name color supplier; active only', () => {
  const hits = filterOpeningProducts(data.products, '札幌')
  assert.strictEqual(hits.length, 1)
  assert.strictEqual(hits[0].id, 12)
  assert.ok(!filterOpeningProducts(data.products, '').some(p => p.id === 11))
  assert.ok(productPickerLabel(hits[0]).includes('仕入先') || productPickerLabel(hits[0]).includes('札幌商会'))
  assert.ok(resolveConfirmedOpeningProduct(data.products, 12))
  assert.strictEqual(resolveConfirmedOpeningProduct(data.products, 11), null)
})

test('customer change clears product fields', () => {
  const cleared = clearOpeningProductFields({
    customerId: '1', handleId: '100', handleQuery: 'x', lines: {productId: 10, rows: [{size: 'M', qty: '5', unitCost: '1000'}]}, basis: '2026-01-01'
  })
  assert.strictEqual(cleared.customerId, '1')
  assert.strictEqual(cleared.handleId, undefined)
  assert.strictEqual(cleared.lines, undefined)
  assert.strictEqual(cleared.basis, '2026-01-01')
  const full = clearOpeningCustomerSelection({customerId: '1', customerQuery: 'a', handleId: '100', lines: {}})
  assert.strictEqual(full.customerId, undefined)
  assert.strictEqual(full.handleId, undefined)
})

test('select handle reconciles invalid sizes and keeps valid qty', () => {
  const handles = openingHandlesFromData(data)
  const opening = {
    basis: '2026-09-01',
    locationId: '8',
    lines: {
      productId: 10,
      rows: [
        {size: 'M', qty: '3', unitCost: '1000'},
        {size: 'LL', qty: '2', unitCost: '1100'},
        {size: '3L', qty: '1', unitCost: '1200'}
      ]
    }
  }
  const next = selectOpeningHandle(opening, handles.find(h => h.id === 120))
  assert.strictEqual(next.handleId, '120')
  assert.strictEqual(next.customerId, '4')
  assert.ok(next.sizeNotice.includes('3L') || next.sizeNotice.includes('LL'))
  assert.ok(next.lines.rows.some(r => r.size === 'M' && r.qty === '3'))
  assert.ok(!next.lines.rows.some(r => r.size === 'LL' && r.qty === '2'))
  assert.ok(!next.lines.rows.some(r => r.size === '3L'))
  const cleared = clearOpeningHandleSelection(next)
  assert.strictEqual(cleared.handleId, undefined)
  assert.strictEqual(cleared.lines, undefined)
})

test('reconcile same product keeps lines unchanged', () => {
  const product = data.products[0]
  const opening = {lines: {productId: 10, rows: [{size: 'M', qty: '1', unitCost: '900'}]}}
  const {opening: next, clearedSizes} = reconcileOpeningLinesForProduct(opening, product)
  assert.deepStrictEqual(clearedSizes, [])
  assert.strictEqual(next.lines.rows[0].qty, '1')
})

test('post-save clears handle and qty; keeps location/basis', () => {
  const next = openingStateAfterSuccessfulSave({
    customerId: '1',
    handleId: '100',
    lines: {productId: 10, rows: [{size: 'M', qty: '5', unitCost: '1000'}]},
    customerQuery: 'あおば記念病院',
    handleQuery: 'SCR',
    basis: '2026-09-01',
    siteId: '7',
    shelfId: '8',
    locationId: '8'
  })
  assert.strictEqual(next.customerId, undefined)
  assert.strictEqual(next.handleId, undefined)
  assert.strictEqual(next.lines, undefined)
  assert.strictEqual(next.basis, '2026-09-01')
  assert.strictEqual(next.locationId, '8')
})

test('inbound post-save keeps location/receivedOn; drops handle and basis', () => {
  const next = inboundStateAfterSuccessfulSave({
    customerId: '1',
    handleId: '100',
    lines: {productId: 10, rows: [{size: 'M', qty: '5', unitCost: '1000'}]},
    basis: '2026-09-01',
    receivedOn: '2026-10-10',
    note: 'メモ',
    condition: 'used',
    siteId: '7',
    shelfId: '8',
    locationId: '8'
  })
  assert.strictEqual(next.handleId, undefined)
  assert.strictEqual(next.lines, undefined)
  assert.strictEqual(next.basis, undefined)
  assert.strictEqual(next.receivedOn, '2026-10-10')
  assert.strictEqual(next.note, 'メモ')
  assert.strictEqual(next.condition, 'used')
  assert.strictEqual(next.locationId, '8')
})

test('cannot resolve unconfirmed typed text as selection', () => {
  assert.strictEqual(resolveConfirmedOpeningCustomer(customers, ''), null)
  assert.strictEqual(resolveConfirmedOpeningCustomer(customers, 'あおば'), null)
  assert.strictEqual(resolveConfirmedOpeningOffering(openingHandlesFromData(data), 1, ''), null)
  assert.strictEqual(resolveConfirmedOpeningOffering(openingHandlesFromData(data), 1, 'SCR'), null)
  assert.ok(resolveConfirmedOpeningOffering(openingHandlesFromData(data), 1, 100))
  assert.strictEqual(resolveConfirmedOpeningOffering(openingHandlesFromData(data), 2, 100), null)
  assert.ok(resolveConfirmedOpeningHandle(openingHandlesFromData(data), 100))
  assert.strictEqual(resolveConfirmedOpeningHandle(openingHandlesFromData(data), 'あおば'), null)
})

test('list filter matches customer code productName; empty query returns all', () => {
  assert.deepStrictEqual(filterOpeningListEntries(listEntries, '').map(e => e.id), [1, 2, 3])
  assert.deepStrictEqual(filterOpeningListEntries(listEntries, '  ').map(e => e.id), [1, 2, 3])
  assert.deepStrictEqual(filterOpeningListEntries(listEntries, 'あおば').map(e => e.id), [1])
  assert.deepStrictEqual(filterOpeningListEntries(listEntries, 'SCR').map(e => e.id), [1, 3])
  assert.deepStrictEqual(filterOpeningListEntries(listEntries, 'パンツ').map(e => e.id), [2])
  assert.deepStrictEqual(filterOpeningListEntries(listEntries, '存在しない').map(e => e.id), [])
  // color / basisDate are not search fields
  assert.deepStrictEqual(filterOpeningListEntries(listEntries, '紺').map(e => e.id), [])
  assert.deepStrictEqual(filterOpeningListEntries(listEntries, '2026-09').map(e => e.id), [])
})

if (!process.exitCode) console.log('All opening picker unit tests passed')
